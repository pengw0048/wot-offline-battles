//! Canonical shared destructible ledger for protocol v5.

use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

use crate::descriptor_exchange::{
    DestructibleInstance, DestructibleMapDonation, DestructibleResource, DestructibleResourceKind,
    DestructibleWireId,
};
use crate::protocol::{
    DestructibleHullEvidence, DestructibleKind as WireDestructibleKind, DestructibleShotCandidate,
    MAX_DESTRUCTIBLE_HULL_CANDIDATES, MAX_ORACLE_BATCH_QUERIES,
};

pub const MAX_PROJECTILE_DESTRUCTIBLES: usize = 64;
pub const MAX_HULL_DESTRUCTIBLES_PER_TICK: usize =
    MAX_ORACLE_BATCH_QUERIES * MAX_DESTRUCTIBLE_HULL_CANDIDATES;
pub const MAX_ITEM_INDEX: u32 = 1_048_575;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum DestructibleKind {
    Tree,
    Column,
    Fragile,
    Module,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct DestructibleKey {
    pub kind: DestructibleKind,
    pub chunk_id: i64,
    pub item_index: u32,
    pub material_kind: Option<u16>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DestructibleReceipt {
    pub key: DestructibleKey,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub fall_yaw: f64,
    pub speed: f64,
    pub is_shot: bool,
}

impl DestructibleReceipt {
    pub fn normalized(mut self) -> Result<Self, DestructibleError> {
        if !(-2_147_483_648..=4_294_967_295).contains(&self.key.chunk_id)
            || self.key.item_index > MAX_ITEM_INDEX
            || (self.key.kind == DestructibleKind::Module && self.key.material_kind.is_none())
            || [self.x, self.y, self.z, self.fall_yaw, self.speed]
                .into_iter()
                .any(|value| !value.is_finite())
        {
            return Err(DestructibleError::InvalidReceipt);
        }
        self.x = rounded(self.x.clamp(-5_000.0, 5_000.0), 1_000.0);
        self.y = rounded(self.y.clamp(-1_000.0, 3_000.0), 1_000.0);
        self.z = rounded(self.z.clamp(-5_000.0, 5_000.0), 1_000.0);
        self.fall_yaw = rounded(
            self.fall_yaw
                .clamp(-std::f64::consts::PI * 4.0, std::f64::consts::PI * 4.0),
            1_000_000.0,
        );
        self.speed = rounded(self.speed.clamp(-200.0, 200.0), 1_000.0);
        Ok(self)
    }
}

fn rounded(value: f64, scale: f64) -> f64 {
    (value * scale).round() / scale
}

#[derive(Clone, Debug, PartialEq)]
pub struct StoredDestructible {
    pub receipt: DestructibleReceipt,
    pub revision: u64,
    pub reported_by: i64,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct DestructibleCommit {
    pub changed: Vec<StoredDestructible>,
    pub exact_retries: usize,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum DestructibleError {
    #[error("invalid destructible receipt")]
    InvalidReceipt,
    #[error("destructible batch exceeds {limit} entries")]
    BatchLimit { limit: usize },
    #[error("destructible batch repeats one identity")]
    DuplicateIdentity,
    #[error("destructible identity was reused with different data")]
    ConflictingRetry,
    #[error("projectile receipt must have is_shot=true")]
    NotShotReceipt,
    #[error("hull receipt must have is_shot=false")]
    ShotReceiptInHull,
    #[error("destructible catalog is invalid or incomplete")]
    InvalidCatalog,
    #[error("native destructible overlap disagrees with the frozen catalog")]
    CatalogIdentityMismatch,
    #[error("canonical destructible kinetic inputs are invalid")]
    InvalidKineticInputs,
}

#[derive(Clone, Debug)]
struct InstalledDestructible {
    instance: DestructibleInstance,
    resource: DestructibleResource,
}

/// Immutable map-scoped #1513 law installed before the battle can activate.
///
/// Native hull evidence refers to entries only by their frozen wire identity.
/// Health, resource kind and kinetic correction always come from this server-
/// owned copy of the descriptor donation.
#[derive(Clone, Debug)]
pub struct InstalledDestructibleCatalog {
    pub round_id: u64,
    pub map_name: String,
    unit_vehicle_mass: f64,
    instances: BTreeMap<DestructibleWireId, InstalledDestructible>,
}

impl InstalledDestructibleCatalog {
    pub fn from_donation(donation: DestructibleMapDonation) -> Result<Self, DestructibleError> {
        if donation.round_id == 0
            || donation.map_name.is_empty()
            || !donation.unit_vehicle_mass.is_finite()
            || donation.unit_vehicle_mass <= 0.0
            || donation.instances.is_empty()
        {
            return Err(DestructibleError::InvalidCatalog);
        }
        let mut instances = BTreeMap::new();
        for instance in donation.instances {
            let resource = donation
                .resources
                .get(&instance.resource_name)
                .cloned()
                .ok_or(DestructibleError::InvalidCatalog)?;
            let valid_health = match resource.kind {
                DestructibleResourceKind::Structure => {
                    instance.scaled_health.is_none()
                        && instance
                            .modules
                            .as_ref()
                            .is_some_and(|modules| !modules.is_empty())
                }
                DestructibleResourceKind::Tree
                | DestructibleResourceKind::Column
                | DestructibleResourceKind::Fragile => {
                    instance.scaled_health.is_some() && instance.modules.is_none()
                }
            };
            if !valid_health
                || !resource.kinetic_correction.is_finite()
                || instances
                    .insert(instance.wire, InstalledDestructible { instance, resource })
                    .is_some()
            {
                return Err(DestructibleError::InvalidCatalog);
            }
        }
        Ok(Self {
            round_id: donation.round_id,
            map_name: donation.map_name,
            unit_vehicle_mass: donation.unit_vehicle_mass,
            instances,
        })
    }

    pub fn len(&self) -> usize {
        self.instances.len()
    }

    pub fn is_empty(&self) -> bool {
        self.instances.is_empty()
    }

    fn instance(
        &self,
        chunk_id: i64,
        item_index: u32,
    ) -> Result<&InstalledDestructible, DestructibleError> {
        self.instances
            .get(&DestructibleWireId {
                chunk_id,
                item_index,
            })
            .ok_or(DestructibleError::CatalogIdentityMismatch)
    }
}

/// Server-side interpretation of read-only native destructible evidence.
///
/// The hidden client proves only frozen overlap identity, geometry and native
/// material lookup facts. It never supplies health, kinetic damage or a
/// crushable verdict and never calls a native destroy API on this path.
#[derive(Clone, Copy, Debug, Default)]
pub struct DestructibleAuthority;

impl DestructibleAuthority {
    pub fn key(
        kind: WireDestructibleKind,
        chunk_id: i64,
        item_index: i64,
        mat_kind: Option<i64>,
    ) -> Result<DestructibleKey, DestructibleError> {
        let item_index =
            u32::try_from(item_index).map_err(|_| DestructibleError::InvalidReceipt)?;
        let (kind, material_kind) = match kind {
            WireDestructibleKind::Falling => (DestructibleKind::Column, None),
            WireDestructibleKind::Fragile => (DestructibleKind::Fragile, None),
            WireDestructibleKind::Structure => (
                DestructibleKind::Module,
                Some(
                    u16::try_from(mat_kind.ok_or(DestructibleError::InvalidReceipt)?)
                        .map_err(|_| DestructibleError::InvalidReceipt)?,
                ),
            ),
        };
        let key = DestructibleKey {
            kind,
            chunk_id,
            item_index,
            material_kind,
        };
        if key.item_index > MAX_ITEM_INDEX {
            return Err(DestructibleError::InvalidReceipt);
        }
        Ok(key)
    }

    pub fn shot_receipt(
        candidate: &DestructibleShotCandidate,
        fall_yaw: f64,
    ) -> Result<DestructibleReceipt, DestructibleError> {
        DestructibleReceipt {
            key: Self::key(
                candidate.kind,
                candidate.chunk_id,
                candidate.item_index,
                candidate.mat_kind,
            )?,
            x: f64::from(candidate.impact_position.x),
            y: f64::from(candidate.impact_position.y),
            z: f64::from(candidate.impact_position.z),
            fall_yaw,
            // Matches the copied #1513 shot-destruction presentation law.
            speed: 12.0,
            is_shot: true,
        }
        .normalized()
    }

    pub fn hull_receipts(
        catalog: &InstalledDestructibleCatalog,
        evidence: &DestructibleHullEvidence,
        already_destroyed: &BTreeSet<DestructibleKey>,
        fall_yaw: f64,
        kinetic_speed: f64,
        vehicle_mass: f64,
    ) -> Result<Vec<DestructibleReceipt>, DestructibleError> {
        if !kinetic_speed.is_finite()
            || !vehicle_mass.is_finite()
            || vehicle_mass <= 0.0
            || !catalog.unit_vehicle_mass.is_finite()
            || catalog.unit_vehicle_mass <= 0.0
        {
            return Err(DestructibleError::InvalidKineticInputs);
        }
        let mut receipts = Vec::new();
        let mut seen = BTreeSet::new();
        for candidate in &evidence.candidates {
            let key = Self::key(
                candidate.kind,
                candidate.chunk_id,
                candidate.item_index,
                candidate.mat_kind,
            )?;
            let installed = catalog.instance(key.chunk_id, key.item_index)?;
            let scaled_health = match (candidate.kind, installed.resource.kind) {
                (WireDestructibleKind::Falling, DestructibleResourceKind::Column)
                | (WireDestructibleKind::Fragile, DestructibleResourceKind::Fragile) => installed
                    .instance
                    .scaled_health
                    .ok_or(DestructibleError::InvalidCatalog)?,
                (WireDestructibleKind::Structure, DestructibleResourceKind::Structure) => installed
                    .instance
                    .modules
                    .as_ref()
                    .and_then(|modules| {
                        key.material_kind
                            .and_then(|material_kind| modules.get(&material_kind))
                    })
                    .map(|module| module.scaled_health)
                    .ok_or(DestructibleError::CatalogIdentityMismatch)?,
                _ => return Err(DestructibleError::CatalogIdentityMismatch),
            };
            let mut kinetic_damage = 0.5 * vehicle_mass * kinetic_speed * kinetic_speed * 0.00015;
            if matches!(
                installed.resource.kind,
                DestructibleResourceKind::Column | DestructibleResourceKind::Fragile
            ) {
                kinetic_damage *= (vehicle_mass / catalog.unit_vehicle_mass)
                    .powf(installed.resource.kinetic_correction);
            }
            if !scaled_health.is_finite()
                || scaled_health <= 0.0
                || !kinetic_damage.is_finite()
                || kinetic_damage < 0.0
            {
                return Err(DestructibleError::InvalidKineticInputs);
            }
            if scaled_health >= kinetic_damage
                || already_destroyed.contains(&key)
                || !seen.insert(key)
            {
                continue;
            }
            receipts.push(
                DestructibleReceipt {
                    key,
                    x: f64::from(candidate.obb_center.x),
                    y: f64::from(candidate.obb_center.y),
                    z: f64::from(candidate.obb_center.z),
                    fall_yaw,
                    speed: kinetic_speed,
                    is_shot: false,
                }
                .normalized()?,
            );
        }
        Ok(receipts)
    }
}

#[derive(Clone, Debug, Default)]
pub struct DestructibleLedger {
    revision: u64,
    entries: BTreeMap<DestructibleKey, StoredDestructible>,
}

impl DestructibleLedger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn entries(&self) -> impl Iterator<Item = &StoredDestructible> {
        self.entries.values()
    }

    /// Admit one authority report. Modern standalone retries must match the
    /// canonical payload exactly.
    pub fn report(
        &mut self,
        reported_by: i64,
        receipt: DestructibleReceipt,
    ) -> Result<DestructibleCommit, DestructibleError> {
        let receipt = receipt.normalized()?;
        if let Some(previous) = self.entries.get(&receipt.key) {
            return if previous.receipt == receipt {
                Ok(DestructibleCommit {
                    changed: Vec::new(),
                    exact_retries: 1,
                })
            } else {
                Err(DestructibleError::ConflictingRetry)
            };
        }
        let stored = self.insert(reported_by, receipt);
        Ok(DestructibleCommit {
            changed: vec![stored],
            exact_retries: 0,
        })
    }

    /// Validate the whole projectile receipt list before changing any state.
    /// An identity already committed by another projectile is harmless and is
    /// skipped, matching the Python transaction boundary.
    pub fn commit_projectile_batch(
        &mut self,
        reported_by: i64,
        receipts: Vec<DestructibleReceipt>,
    ) -> Result<DestructibleCommit, DestructibleError> {
        self.commit_batch(reported_by, receipts, true, MAX_PROJECTILE_DESTRUCTIBLES)
    }

    /// Commit one server-owned vehicle-hull crush transaction atomically.
    pub fn commit_hull_batch(
        &mut self,
        reported_by: i64,
        receipts: Vec<DestructibleReceipt>,
    ) -> Result<DestructibleCommit, DestructibleError> {
        self.commit_batch(
            reported_by,
            receipts,
            false,
            MAX_HULL_DESTRUCTIBLES_PER_TICK,
        )
    }

    fn commit_batch(
        &mut self,
        reported_by: i64,
        receipts: Vec<DestructibleReceipt>,
        is_shot: bool,
        limit: usize,
    ) -> Result<DestructibleCommit, DestructibleError> {
        if receipts.len() > limit {
            return Err(DestructibleError::BatchLimit { limit });
        }
        let mut normalized = Vec::with_capacity(receipts.len());
        let mut seen = BTreeSet::new();
        for receipt in receipts {
            let receipt = receipt.normalized()?;
            if receipt.is_shot != is_shot {
                return Err(if is_shot {
                    DestructibleError::NotShotReceipt
                } else {
                    DestructibleError::ShotReceiptInHull
                });
            }
            if !seen.insert(receipt.key) {
                return Err(DestructibleError::DuplicateIdentity);
            }
            normalized.push(receipt);
        }

        let mut commit = DestructibleCommit::default();
        for receipt in normalized {
            if self.entries.contains_key(&receipt.key) {
                commit.exact_retries += 1;
                continue;
            }
            commit.changed.push(self.insert(reported_by, receipt));
        }
        Ok(commit)
    }

    fn insert(&mut self, reported_by: i64, receipt: DestructibleReceipt) -> StoredDestructible {
        self.revision = self.revision.saturating_add(1);
        let stored = StoredDestructible {
            receipt,
            revision: self.revision,
            reported_by,
        };
        self.entries.insert(stored.receipt.key, stored.clone());
        stored
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{DestructibleHullCandidate, Vec3};

    fn receipt(item_index: u32) -> DestructibleReceipt {
        DestructibleReceipt {
            key: DestructibleKey {
                kind: DestructibleKind::Fragile,
                chunk_id: 7,
                item_index,
                material_kind: None,
            },
            x: 1.23456,
            y: 2.0,
            z: 3.0,
            fall_yaw: 0.25,
            speed: 6.0,
            is_shot: true,
        }
    }

    #[test]
    fn report_normalizes_and_exact_retry_is_idempotent() {
        let mut ledger = DestructibleLedger::new();
        let first = ledger.report(-1, receipt(3)).unwrap();
        assert_eq!(first.changed[0].receipt.x, 1.235);
        assert_eq!(first.changed[0].revision, 1);
        let retry = ledger.report(-1, receipt(3)).unwrap();
        assert_eq!(retry.exact_retries, 1);
        assert_eq!(ledger.revision(), 1);
    }

    #[test]
    fn standalone_conflicting_retry_is_rejected() {
        let mut ledger = DestructibleLedger::new();
        ledger.report(-1, receipt(3)).unwrap();
        let mut conflict = receipt(3);
        conflict.speed = 9.0;
        assert_eq!(
            ledger.report(-1, conflict),
            Err(DestructibleError::ConflictingRetry)
        );
    }

    #[test]
    fn invalid_nth_projectile_receipt_is_atomic() {
        let mut ledger = DestructibleLedger::new();
        let mut invalid = receipt(2);
        invalid.x = f64::NAN;
        assert_eq!(
            ledger.commit_projectile_batch(-1, vec![receipt(1), invalid]),
            Err(DestructibleError::InvalidReceipt)
        );
        assert_eq!(ledger.revision(), 0);
        assert_eq!(ledger.entries().count(), 0);
    }

    #[test]
    fn projectile_duplicate_identity_is_rejected_before_commit() {
        let mut ledger = DestructibleLedger::new();
        assert_eq!(
            ledger.commit_projectile_batch(-1, vec![receipt(1), receipt(1)]),
            Err(DestructibleError::DuplicateIdentity)
        );
        assert_eq!(ledger.revision(), 0);
    }

    #[test]
    fn previously_destroyed_identity_is_skipped_in_projectile_batch() {
        let mut ledger = DestructibleLedger::new();
        ledger.report(-1, receipt(1)).unwrap();
        let commit = ledger
            .commit_projectile_batch(-1, vec![receipt(1), receipt(2)])
            .unwrap();
        assert_eq!(commit.exact_retries, 1);
        assert_eq!(commit.changed.len(), 1);
        assert_eq!(ledger.revision(), 2);
    }

    #[test]
    fn module_requires_a_material_kind() {
        let mut module = receipt(1);
        module.key.kind = DestructibleKind::Module;
        assert_eq!(module.normalized(), Err(DestructibleError::InvalidReceipt));
    }

    #[test]
    fn hull_authority_uses_only_frozen_catalog_for_kinetic_verdicts() {
        let candidate = |item_index| DestructibleHullCandidate {
            chunk_id: 7,
            item_index,
            mat_kind: None,
            kind: WireDestructibleKind::Fragile,
            obb_center: Vec3 {
                x: item_index as f32,
                y: 0.0,
                z: 2.0,
            },
        };
        let evidence = DestructibleHullEvidence {
            candidates: vec![candidate(1), candidate(2), candidate(3)],
            frame_travel: 0.2,
        };
        let already_destroyed = BTreeSet::from([DestructibleKey {
            kind: DestructibleKind::Fragile,
            chunk_id: 7,
            item_index: 1,
            material_kind: None,
        }]);

        let resource_name = "objects/fragile.model".to_owned();
        let catalog = InstalledDestructibleCatalog::from_donation(DestructibleMapDonation {
            round_id: 4,
            map_name: "01_karelia".to_owned(),
            unit_vehicle_mass: 1_000.0,
            resources: BTreeMap::from([(
                resource_name.clone(),
                DestructibleResource {
                    kind: DestructibleResourceKind::Fragile,
                    kinetic_correction: 0.0,
                },
            )]),
            instances: [(1_i64, 10.0), (2, 1_000.0), (3, 10.0)]
                .into_iter()
                .map(|(item_index, scaled_health)| DestructibleInstance {
                    signature: crate::descriptor_exchange::DestructibleSignature([
                        item_index, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    ]),
                    wire: DestructibleWireId {
                        chunk_id: 7,
                        item_index: item_index as u32,
                    },
                    scaled_health: Some(scaled_health),
                    modules: None,
                    resource_name: resource_name.clone(),
                })
                .collect(),
        })
        .unwrap();

        let receipts = DestructibleAuthority::hull_receipts(
            &catalog,
            &evidence,
            &already_destroyed,
            0.4,
            8.0,
            5_000.0,
        )
        .unwrap();
        assert_eq!(receipts.len(), 1);
        assert_eq!(receipts[0].key.item_index, 3);
        assert_eq!(receipts[0].fall_yaw, 0.4);
        assert_eq!(receipts[0].speed, 8.0);
        assert!(!receipts[0].is_shot);
    }
}
