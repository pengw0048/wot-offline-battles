//! Deterministic per-endpoint replication planning.
//!
//! Simulation remains 30 Hz.  Ordinary snapshots are emitted every other
//! tick and may replace an older unsent snapshot.  Lifecycle messages and any
//! snapshot which changes a manifest are reliable FIFO frames.

use crate::net::DeliveryClass;
use crate::protocol::SimulationScope;
use crate::wire::{WireError, WireObject, LAN_PROTOCOL_VERSION};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, VecDeque};
use thiserror::Error;

pub type ReplicationEndpointId = u64;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Revisions {
    pub manifest: u64,
    pub orders: u64,
    pub destructibles: u64,
}

impl Revisions {
    fn is_not_newer_than(self, other: Self) -> bool {
        self.manifest <= other.manifest
            && self.orders <= other.orders
            && self.destructibles <= other.destructibles
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReplicationSnapshot {
    pub entities: Value,
    pub manifest: Value,
    pub orders: Value,
    pub destructibles: Value,
    pub revisions: Revisions,
}

impl ReplicationSnapshot {
    pub fn new(
        entities: Value,
        manifest: Value,
        orders: Value,
        destructibles: Value,
        revisions: Revisions,
    ) -> Self {
        Self {
            entities,
            manifest,
            orders,
            destructibles,
            revisions,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReplicationEmission {
    pub endpoint_id: ReplicationEndpointId,
    pub delivery: DeliveryClass,
    pub message: WireObject,
}

#[derive(Clone, Debug)]
struct EndpointState {
    scope: SimulationScope,
    known: Revisions,
    force_manifest: bool,
    last_snapshot_tick: Option<u64>,
}

impl EndpointState {
    fn new(scope: SimulationScope) -> Self {
        Self {
            scope,
            known: Revisions::default(),
            force_manifest: true,
            last_snapshot_tick: None,
        }
    }

    fn reset(&mut self, scope: SimulationScope) {
        *self = Self::new(scope);
    }
}

#[derive(Clone, Debug)]
struct PendingReliable {
    scope: SimulationScope,
    recipient: Option<ReplicationEndpointId>,
    message: WireObject,
}

#[derive(Debug, Error)]
pub enum ReplicationError {
    #[error("replication scope is stale or from an unknown future round")]
    ScopeMismatch,
    #[error("snapshot tick {actual} is not newer than {previous}")]
    TickRegression { previous: u64, actual: u64 },
    #[error("replication revisions regressed")]
    RevisionRegression,
    #[error(transparent)]
    Wire(#[from] WireError),
}

/// Single-threaded planner for all visible LAN endpoints.
#[derive(Debug)]
pub struct ReplicationScheduler {
    scope: SimulationScope,
    endpoints: BTreeMap<ReplicationEndpointId, EndpointState>,
    pending_reliable: VecDeque<PendingReliable>,
    latest_revisions: Revisions,
}

impl ReplicationScheduler {
    pub fn new(scope: SimulationScope) -> Self {
        Self {
            scope,
            endpoints: BTreeMap::new(),
            pending_reliable: VecDeque::new(),
            latest_revisions: Revisions::default(),
        }
    }

    pub fn scope(&self) -> SimulationScope {
        self.scope
    }

    pub fn add_endpoint(&mut self, endpoint_id: ReplicationEndpointId) {
        self.endpoints
            .insert(endpoint_id, EndpointState::new(self.scope));
    }

    pub fn remove_endpoint(&mut self, endpoint_id: ReplicationEndpointId) {
        self.endpoints.remove(&endpoint_id);
        self.pending_reliable
            .retain(|pending| pending.recipient != Some(endpoint_id));
    }

    pub fn force_manifest(&mut self, endpoint_id: ReplicationEndpointId) {
        if let Some(endpoint) = self.endpoints.get_mut(&endpoint_id) {
            endpoint.force_manifest = true;
        }
    }

    /// Advance to a strictly newer authority scope and fence all old frames.
    pub fn advance_scope(&mut self, scope: SimulationScope) -> Result<(), ReplicationError> {
        if scope.round_id < self.scope.round_id
            || (scope.round_id == self.scope.round_id && scope.epoch <= self.scope.epoch)
        {
            return Err(ReplicationError::ScopeMismatch);
        }
        self.scope = scope;
        self.latest_revisions = Revisions::default();
        self.pending_reliable.clear();
        for endpoint in self.endpoints.values_mut() {
            endpoint.reset(scope);
        }
        Ok(())
    }

    /// Queue a reliable lifecycle/event frame. Broadcast events are expanded
    /// at planning time so endpoint membership remains deterministic.
    pub fn queue_reliable(
        &mut self,
        scope: SimulationScope,
        recipient: Option<ReplicationEndpointId>,
        message: WireObject,
    ) -> Result<(), ReplicationError> {
        if scope != self.scope {
            return Err(ReplicationError::ScopeMismatch);
        }
        self.pending_reliable.push_back(PendingReliable {
            scope,
            recipient,
            message,
        });
        Ok(())
    }

    /// Plan reliable messages and, on even ticks, one snapshot per endpoint.
    /// The returned order is stable by endpoint id; for each endpoint all
    /// reliable messages precede its snapshot.
    pub fn plan_tick(
        &mut self,
        scope: SimulationScope,
        tick: u64,
        snapshot: &ReplicationSnapshot,
    ) -> Result<Vec<ReplicationEmission>, ReplicationError> {
        if scope != self.scope {
            return Err(ReplicationError::ScopeMismatch);
        }
        if !self.latest_revisions.is_not_newer_than(snapshot.revisions) {
            return Err(ReplicationError::RevisionRegression);
        }
        self.latest_revisions = snapshot.revisions;

        let pending: Vec<_> = self.pending_reliable.drain(..).collect();
        let mut emissions = Vec::new();
        for (&endpoint_id, endpoint) in &mut self.endpoints {
            if endpoint.scope != scope {
                endpoint.reset(scope);
            }
            for item in &pending {
                if item.scope == scope
                    && (item.recipient.is_none() || item.recipient == Some(endpoint_id))
                {
                    emissions.push(ReplicationEmission {
                        endpoint_id,
                        delivery: DeliveryClass::Reliable,
                        message: item.message.clone(),
                    });
                }
            }

            if tick % 2 != 0 {
                continue;
            }
            if let Some(previous) = endpoint.last_snapshot_tick {
                if tick <= previous {
                    return Err(ReplicationError::TickRegression {
                        previous,
                        actual: tick,
                    });
                }
            }

            let manifest_changed = endpoint.force_manifest
                || endpoint.known.manifest != snapshot.revisions.manifest
                || endpoint.known.orders != snapshot.revisions.orders
                || endpoint.known.destructibles != snapshot.revisions.destructibles;
            let message = snapshot_message(scope, tick, snapshot, manifest_changed)?;
            emissions.push(ReplicationEmission {
                endpoint_id,
                delivery: if manifest_changed {
                    DeliveryClass::Reliable
                } else {
                    DeliveryClass::Snapshot
                },
                message,
            });
            endpoint.last_snapshot_tick = Some(tick);
            if manifest_changed {
                endpoint.known = snapshot.revisions;
                endpoint.force_manifest = false;
            }
        }
        Ok(emissions)
    }
}

fn snapshot_message(
    scope: SimulationScope,
    tick: u64,
    snapshot: &ReplicationSnapshot,
    full_manifest: bool,
) -> Result<WireObject, WireError> {
    let mut fields = Map::new();
    fields.insert("protocol".to_owned(), Value::from(LAN_PROTOCOL_VERSION));
    fields.insert("round_id".to_owned(), Value::from(scope.round_id));
    fields.insert("authority_epoch".to_owned(), Value::from(scope.epoch));
    fields.insert("tick".to_owned(), Value::from(tick));
    fields.insert("entities".to_owned(), snapshot.entities.clone());
    fields.insert(
        "manifest_revision".to_owned(),
        Value::from(snapshot.revisions.manifest),
    );
    fields.insert(
        "orders_revision".to_owned(),
        Value::from(snapshot.revisions.orders),
    );
    fields.insert(
        "destructibles_revision".to_owned(),
        Value::from(snapshot.revisions.destructibles),
    );
    if full_manifest {
        fields.insert("full_manifest".to_owned(), Value::Bool(true));
        fields.insert("manifest".to_owned(), snapshot.manifest.clone());
        fields.insert("orders".to_owned(), snapshot.orders.clone());
        fields.insert("destructibles".to_owned(), snapshot.destructibles.clone());
    }
    WireObject::with_fields("snapshot", fields)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{Epoch, RoundId};
    use serde_json::json;

    fn scope(round_id: RoundId, authority_epoch: Epoch) -> SimulationScope {
        SimulationScope {
            round_id,
            epoch: authority_epoch,
        }
    }

    fn state(revisions: Revisions) -> ReplicationSnapshot {
        ReplicationSnapshot::new(
            json!([{"id": 1, "position": [1, 2, 3]}]),
            json!([{"id": 1, "vehicle": "ussr:R11_MS-1"}]),
            json!({"1": {"target": 2}}),
            json!({"5": "destroyed"}),
            revisions,
        )
    }

    fn event(kind: &str) -> WireObject {
        WireObject::new(kind).unwrap()
    }

    #[test]
    fn ordinary_snapshots_follow_a_stable_fifteen_hz_cadence() {
        let mut scheduler = ReplicationScheduler::new(scope(1, 1));
        scheduler.add_endpoint(7);
        let snapshot = state(Revisions::default());

        let first = scheduler.plan_tick(scope(1, 1), 0, &snapshot).unwrap();
        assert_eq!(first[0].delivery, DeliveryClass::Reliable);
        assert!(scheduler
            .plan_tick(scope(1, 1), 1, &snapshot)
            .unwrap()
            .is_empty());
        let second = scheduler.plan_tick(scope(1, 1), 2, &snapshot).unwrap();
        assert_eq!(second[0].delivery, DeliveryClass::Snapshot);
        assert_eq!(second[0].message.get("tick"), Some(&json!(2)));
    }

    #[test]
    fn reliable_event_precedes_manifest_or_lean_snapshot() {
        let mut scheduler = ReplicationScheduler::new(scope(1, 2));
        scheduler.add_endpoint(1);
        scheduler
            .queue_reliable(scope(1, 2), None, event("vehicle_killed"))
            .unwrap();
        let emissions = scheduler
            .plan_tick(scope(1, 2), 4, &state(Revisions::default()))
            .unwrap();
        assert_eq!(emissions.len(), 2);
        assert_eq!(emissions[0].message.kind(), "vehicle_killed");
        assert_eq!(emissions[0].delivery, DeliveryClass::Reliable);
        assert_eq!(emissions[1].message.kind(), "snapshot");
    }

    #[test]
    fn revision_bump_forces_one_reliable_manifest_snapshot() {
        let mut scheduler = ReplicationScheduler::new(scope(3, 4));
        scheduler.add_endpoint(9);
        scheduler
            .plan_tick(scope(3, 4), 0, &state(Revisions::default()))
            .unwrap();
        let bumped = state(Revisions {
            manifest: 1,
            orders: 3,
            destructibles: 2,
        });
        let full = scheduler.plan_tick(scope(3, 4), 2, &bumped).unwrap();
        assert_eq!(full[0].delivery, DeliveryClass::Reliable);
        assert_eq!(full[0].message.get("full_manifest"), Some(&json!(true)));
        let lean = scheduler.plan_tick(scope(3, 4), 4, &bumped).unwrap();
        assert_eq!(lean[0].delivery, DeliveryClass::Snapshot);
        assert_eq!(lean[0].message.get("manifest"), None);
    }

    #[test]
    fn endpoint_manifest_knowledge_is_independent() {
        let mut scheduler = ReplicationScheduler::new(scope(1, 1));
        scheduler.add_endpoint(1);
        scheduler
            .plan_tick(scope(1, 1), 0, &state(Revisions::default()))
            .unwrap();
        scheduler.add_endpoint(2);
        let emissions = scheduler
            .plan_tick(scope(1, 1), 2, &state(Revisions::default()))
            .unwrap();
        assert_eq!(emissions[0].endpoint_id, 1);
        assert_eq!(emissions[0].delivery, DeliveryClass::Snapshot);
        assert_eq!(emissions[1].endpoint_id, 2);
        assert_eq!(emissions[1].delivery, DeliveryClass::Reliable);
    }

    #[test]
    fn scope_change_discards_old_events_and_forces_manifests() {
        let mut scheduler = ReplicationScheduler::new(scope(1, 8));
        scheduler.add_endpoint(1);
        scheduler
            .plan_tick(scope(1, 8), 0, &state(Revisions::default()))
            .unwrap();
        scheduler
            .queue_reliable(scope(1, 8), None, event("stale"))
            .unwrap();
        scheduler.advance_scope(scope(2, 9)).unwrap();
        let emissions = scheduler
            .plan_tick(scope(2, 9), 0, &state(Revisions::default()))
            .unwrap();
        assert_eq!(emissions.len(), 1);
        assert_eq!(emissions[0].message.kind(), "snapshot");
        assert_eq!(emissions[0].delivery, DeliveryClass::Reliable);
        assert!(matches!(
            scheduler.queue_reliable(scope(1, 8), None, event("old")),
            Err(ReplicationError::ScopeMismatch)
        ));
    }

    #[test]
    fn snapshot_tick_regression_is_rejected_per_endpoint() {
        let mut scheduler = ReplicationScheduler::new(scope(1, 1));
        scheduler.add_endpoint(1);
        let snapshot = state(Revisions::default());
        scheduler.plan_tick(scope(1, 1), 2, &snapshot).unwrap();
        assert!(matches!(
            scheduler.plan_tick(scope(1, 1), 2, &snapshot),
            Err(ReplicationError::TickRegression {
                previous: 2,
                actual: 2
            })
        ));
    }
}
