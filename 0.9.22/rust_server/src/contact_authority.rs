//! Canonical vehicle-observer contact authority.
//!
//! Descriptor donation supplies actor-scoped view and camouflage inputs. The
//! native oracle supplies exact-T+3 geometry evidence for one frozen pair.
//! This module joins those two sources without manufacturing an unavailable
//! loadout or turning an unavailable native result into occlusion.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};
use thiserror::Error;

use crate::authority_runtime::{
    FailedNativeObservation, NativeFiringLaneSample, NativeObservationIntent,
    NativeObservationSample, TimedOutNativeObservation,
};
use crate::bot_sim::{time_us_at_tick, Vec3};
use crate::combat::{VehicleKey, VehicleKind, MAX_COMBAT_ID};
use crate::descriptor::AuthoritySpottingInput;
use crate::protocol::{OracleLineage, OracleV1BatchKey, Tick, ORACLE_PIPELINE_TICKS};
use crate::spotting::{
    evaluate_observer_firing_lane, evaluate_team_contact, fired_recently, visibility_cache_ttl_us,
    ContactTarget, ContactTargetKind, LastKnownDecision, NativeFiringLaneEvidence,
    NativeVisibilityEvidence, ObserverTargetInput, TeamContactInput,
    MOVING_SPEED_EPSILON_METRES_PER_SECOND, SHOT_LANE_CACHE_US, SPG_SHOT_LANE_RANGE_METRES,
};

const MAX_WORLD_XZ: f64 = 2_000.0;
const MIN_WORLD_Y: f64 = -1_000.0;
const MAX_WORLD_Y: f64 = 1_000.0;
const MAX_ACTOR_SPEED_METRES_PER_SECOND: f64 = 200.0;
const FROZEN_POSITION_EPSILON: f64 = 1.0e-9;

#[derive(Clone, Debug, PartialEq)]
pub struct ContactActorState {
    pub key: VehicleKey,
    pub team: u8,
    pub alive: bool,
    pub world_pose: bool,
    pub position: Vec3,
    pub speed_metres_per_second: f64,
    pub health: u32,
    pub max_health: u32,
    /// `None` means this actor cannot receive a native firing permission. The
    /// battle loop supplies the ordinary or SPG range from the strict planner
    /// manifest; contact visibility itself remains available without it.
    pub firing_lane_range_metres: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ContactPosition {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl From<Vec3> for ContactPosition {
    fn from(value: Vec3) -> Self {
        Self {
            x: value.x,
            y: value.y,
            z: value.z,
        }
    }
}

/// One team-target projection produced at a fixed authority tick.
///
/// A retained hidden contact keeps its last-known payload here for later
/// client presentation, but [`CanonicalContact::planner_value`] deliberately
/// withholds that payload from the planner's strict negative observation.
#[derive(Clone, Debug, PartialEq)]
pub struct CanonicalContact {
    pub observing_team: u8,
    pub target: ContactTarget,
    pub target_team: u8,
    /// Direct team visibility at this authority tick. Planner JSON keeps this
    /// strict and never turns a retained presentation lease into live sight.
    pub visible: bool,
    /// Relative client presentation remains visible while the frozen lease is
    /// positive, even after direct visibility becomes stale.
    pub presentation_visible: bool,
    pub visible_by_bot_ids: BTreeSet<u32>,
    pub visible_by_player_ids: BTreeSet<u32>,
    pub shootable_by_bot_ids: BTreeSet<u32>,
    pub last_known_position: ContactPosition,
    pub last_known_speed_metres_per_second: f64,
    pub last_known_health: u32,
    pub max_health: u32,
    pub observed_at_us: u64,
    pub expires_at_us: u64,
}

impl CanonicalContact {
    pub fn planner_value(&self) -> Value {
        let target_kind = match self.target.kind {
            ContactTargetKind::Human => "human",
            ContactTargetKind::Bot => "bot",
        };
        let mut value = json!({
            "observing_team": self.observing_team,
            "target_kind": target_kind,
            "target_id": self.target.id,
            "target_team": self.target_team,
            "visible": self.visible,
            "shootable_by_bot_ids": self
                .shootable_by_bot_ids
                .iter()
                .copied()
                .collect::<Vec<_>>(),
        });
        if self.visible {
            let raw = value
                .as_object_mut()
                .expect("canonical contact JSON starts as an object");
            raw.insert("x".to_owned(), json!(self.last_known_position.x));
            raw.insert("y".to_owned(), json!(self.last_known_position.y));
            raw.insert("z".to_owned(), json!(self.last_known_position.z));
            raw.insert("health".to_owned(), json!(self.last_known_health));
            raw.insert("max_health".to_owned(), json!(self.max_health));
            raw.insert(
                "speed".to_owned(),
                json!(self.last_known_speed_metres_per_second),
            );
        }
        value
    }
}

#[derive(Clone, Debug, Error, PartialEq)]
pub enum ContactAuthorityError {
    #[error("spotting inputs were already installed")]
    InputsAlreadyInstalled,
    #[error("native lineage was already bound to a different round")]
    ConflictingLineage,
    #[error("spotting input for {vehicle:?} is outside the actor contract")]
    InvalidSpottingInput { vehicle: VehicleKey },
    #[error("contact actor {vehicle:?} is outside the canonical state contract")]
    InvalidActorState { vehicle: VehicleKey },
    #[error("contact frame {tick} contains duplicate actor {vehicle:?}")]
    DuplicateActorState { tick: Tick, vehicle: VehicleKey },
    #[error("contact frame {tick} does not advance the previous frame {previous_tick}")]
    NonMonotonicFrame { previous_tick: Tick, tick: Tick },
    #[error("native observation arrived before a lineage was bound")]
    MissingLineage,
    #[error("native observation has an invalid {field}")]
    InvalidNativeEnvelope { field: &'static str },
    #[error("native observation for issued tick {issued_tick} has no frozen actor frame")]
    MissingFrozenFrame { issued_tick: Tick },
    #[error("native observation frozen geometry does not match actor {vehicle:?}")]
    FrozenGeometryMismatch { vehicle: VehicleKey },
    #[error("native observation conflicts with an already admitted pair sample")]
    ConflictingNativeEvidence,
}

#[derive(Clone, Debug, PartialEq)]
struct FrozenActorState {
    state: ContactActorState,
    stationary_since_us: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
struct FrozenPair {
    observer: FrozenActorState,
    target: FrozenActorState,
    issued_tick: Tick,
    apply_tick: Tick,
    batch_key: OracleV1BatchKey,
    evaluated_for_recent_fire: bool,
}

impl FrozenPair {
    fn same_sample(&self, other: &Self) -> bool {
        self.issued_tick == other.issued_tick
            && self.apply_tick == other.apply_tick
            && self.batch_key == other.batch_key
            && self.observer.state.key == other.observer.state.key
            && self.target.state.key == other.target.state.key
            && self.evaluated_for_recent_fire == other.evaluated_for_recent_fire
            && same_position(self.observer.state.position, other.observer.state.position)
            && same_position(self.target.state.position, other.target.state.position)
    }
}

#[derive(Clone, Debug, PartialEq)]
struct VisibilityRecord {
    pair: FrozenPair,
    evidence: NativeVisibilityEvidence,
}

#[derive(Clone, Debug, PartialEq)]
struct LaneRecord {
    pair: FrozenPair,
    evidence: NativeFiringLaneEvidence,
}

#[derive(Clone, Debug, Default, PartialEq)]
struct PairEvidence {
    visibility: Option<VisibilityRecord>,
    lane: Option<LaneRecord>,
}

#[derive(Clone, Debug, PartialEq)]
struct LastKnownRecord {
    position: ContactPosition,
    speed_metres_per_second: f64,
    health: u32,
    max_health: u32,
    observed_at_us: u64,
}

/// Per-round contact state. Both player and bot observers are evaluated only
/// when their exact actor-scoped loadout was installed.
#[derive(Clone, Debug, Default)]
pub struct ContactAuthority {
    lineage: Option<OracleLineage>,
    inputs_installed: bool,
    inputs: BTreeMap<VehicleKey, AuthoritySpottingInput>,
    frames: BTreeMap<Tick, BTreeMap<VehicleKey, FrozenActorState>>,
    stationary_since_us: BTreeMap<VehicleKey, u64>,
    last_frame_tick: Option<Tick>,
    pair_evidence: BTreeMap<(VehicleKey, VehicleKey), PairEvidence>,
    last_fire_us: BTreeMap<VehicleKey, u64>,
    last_known: BTreeMap<(u8, ContactTarget), LastKnownRecord>,
    latest_contacts: Vec<CanonicalContact>,
    last_ingested_tick: Option<Tick>,
}

impl ContactAuthority {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind_lineage(&mut self, lineage: OracleLineage) -> Result<(), ContactAuthorityError> {
        match self.lineage {
            None => self.lineage = Some(lineage),
            Some(current) if current == lineage => {}
            Some(_) => return Err(ContactAuthorityError::ConflictingLineage),
        }
        Ok(())
    }

    /// Install exact per-actor inputs once. An omitted actor remains
    /// unavailable; no donor-default substitution occurs in this layer.
    pub fn install_inputs(
        &mut self,
        inputs: BTreeMap<VehicleKey, AuthoritySpottingInput>,
    ) -> Result<(), ContactAuthorityError> {
        if self.inputs_installed {
            return Err(ContactAuthorityError::InputsAlreadyInstalled);
        }
        for (&vehicle, input) in &inputs {
            if !valid_vehicle(vehicle) || !valid_spotting_input(*input) {
                return Err(ContactAuthorityError::InvalidSpottingInput { vehicle });
            }
        }
        self.inputs = inputs;
        self.inputs_installed = true;
        self.frames.clear();
        self.stationary_since_us.clear();
        self.last_frame_tick = None;
        self.pair_evidence.clear();
        self.last_fire_us.clear();
        self.last_known.clear();
        self.latest_contacts.clear();
        self.last_ingested_tick = None;
        Ok(())
    }

    pub fn inputs(&self) -> &BTreeMap<VehicleKey, AuthoritySpottingInput> {
        &self.inputs
    }

    pub fn spotting_vehicles(&self) -> BTreeSet<VehicleKey> {
        self.inputs.keys().copied().collect()
    }

    pub fn latest_contacts(&self) -> &[CanonicalContact] {
        &self.latest_contacts
    }

    pub fn planner_contacts(&self) -> Value {
        Value::Array(
            self.latest_contacts
                .iter()
                .map(CanonicalContact::planner_value)
                .collect(),
        )
    }

    pub fn last_fire_at_us(&self, vehicle: VehicleKey) -> Option<u64> {
        self.last_fire_us.get(&vehicle).copied()
    }

    /// Record the state used to issue this tick's native intents. Call this
    /// after the bot step and before RAM corrections mutate bot geometry.
    pub fn record_observation_frame(
        &mut self,
        tick: Tick,
        actors: &[ContactActorState],
    ) -> Result<(), ContactAuthorityError> {
        if let Some(previous_tick) = self.last_frame_tick {
            if tick <= previous_tick {
                return Err(ContactAuthorityError::NonMonotonicFrame {
                    previous_tick,
                    tick,
                });
            }
            if tick != previous_tick.saturating_add(1) {
                self.stationary_since_us.clear();
            }
        }
        let now_us = time_us_at_tick(tick);
        let mut frame = BTreeMap::new();
        let mut present = BTreeSet::new();
        for state in actors {
            validate_actor_state(state)?;
            if !present.insert(state.key) {
                return Err(ContactAuthorityError::DuplicateActorState {
                    tick,
                    vehicle: state.key,
                });
            }
            let stationary_since_us = if state.alive
                && state.world_pose
                && state.speed_metres_per_second.abs() <= MOVING_SPEED_EPSILON_METRES_PER_SECOND
            {
                let since = self.stationary_since_us.entry(state.key).or_insert(now_us);
                Some(*since)
            } else {
                self.stationary_since_us.remove(&state.key);
                None
            };
            frame.insert(
                state.key,
                FrozenActorState {
                    state: state.clone(),
                    stationary_since_us,
                },
            );
        }
        self.stationary_since_us
            .retain(|vehicle, _| present.contains(vehicle));
        self.frames.insert(tick, frame);
        let oldest = tick.saturating_sub(ORACLE_PIPELINE_TICKS);
        self.frames.retain(|frame_tick, _| *frame_tick >= oldest);
        self.last_frame_tick = Some(tick);
        Ok(())
    }

    /// Record a launch only after the battle projectile ledger admitted it.
    pub fn note_fire(&mut self, vehicle: VehicleKey, tick: Tick) -> bool {
        if !valid_vehicle(vehicle) {
            return false;
        }
        let fired_at_us = time_us_at_tick(tick);
        if self
            .last_fire_us
            .get(&vehicle)
            .is_some_and(|previous| *previous >= fired_at_us)
        {
            return false;
        }
        self.last_fire_us.insert(vehicle, fired_at_us);
        true
    }

    /// Consume all native observation outcomes for one exact apply tick.
    /// Failed and timed-out evidence is envelope-validated and otherwise left
    /// untouched, so it cannot overwrite a previous positive or negative ray.
    pub fn ingest_native_tick(
        &mut self,
        tick: Tick,
        visibility: &[NativeObservationSample],
        lanes: &[NativeFiringLaneSample],
        failed: &[FailedNativeObservation],
        timed_out: &[TimedOutNativeObservation],
    ) -> Result<(), ContactAuthorityError> {
        if self
            .last_ingested_tick
            .is_some_and(|previous| tick < previous)
        {
            return Err(ContactAuthorityError::InvalidNativeEnvelope {
                field: "tick_order",
            });
        }
        for sample in failed {
            self.validate_envelope(tick, &sample.intent, sample.batch_key)?;
        }
        for sample in timed_out {
            self.validate_envelope(tick, &sample.intent, sample.batch_key)?;
        }

        // Stage successes so one malformed sibling cannot partially replace a
        // previously canonical pair cache.
        let mut next = self.pair_evidence.clone();
        for sample in visibility {
            let Some(pair) = self.frozen_pair(tick, &sample.intent, sample.batch_key)? else {
                continue;
            };
            if !sample.foliage_bonus.is_finite()
                || !(0.0..=0.60).contains(&sample.foliage_bonus)
                || sample.evaluated_for_recent_fire != sample.intent.evaluated_for_recent_fire
            {
                return Err(ContactAuthorityError::InvalidNativeEnvelope {
                    field: "spotting_evidence",
                });
            }
            let key = (pair.observer.state.key, pair.target.state.key);
            let record = VisibilityRecord {
                evidence: NativeVisibilityEvidence {
                    sampled_at_us: time_us_at_tick(pair.apply_tick),
                    line_of_sight: sample.line_of_sight,
                    foliage_bonus: Some(sample.foliage_bonus),
                    evaluated_for_recent_fire: sample.evaluated_for_recent_fire,
                },
                pair,
            };
            insert_visibility(next.entry(key).or_default(), record)?;
        }
        for sample in lanes {
            let Some(pair) = self.frozen_pair(tick, &sample.intent, sample.batch_key)? else {
                continue;
            };
            let key = (pair.observer.state.key, pair.target.state.key);
            let record = LaneRecord {
                evidence: NativeFiringLaneEvidence {
                    sampled_at_us: time_us_at_tick(pair.apply_tick),
                    clear: sample.clear,
                },
                pair,
            };
            insert_lane(next.entry(key).or_default(), record)?;
        }
        self.pair_evidence = next;
        self.last_ingested_tick = Some(tick);
        Ok(())
    }

    /// Evaluate both teams from the current canonical roster. Successful
    /// native evidence still uses its issued-tick positions and motion state;
    /// current state contributes only liveness/health and a proximity fallback
    /// when no fresh frozen native sample exists.
    pub fn evaluate_tick(
        &mut self,
        tick: Tick,
        actors: &[ContactActorState],
    ) -> Result<Vec<CanonicalContact>, ContactAuthorityError> {
        let current = actor_map(tick, actors)?;
        let now_us = time_us_at_tick(tick);
        let mut observing_teams = current
            .values()
            .filter(|state| state.alive && state.world_pose && self.inputs.contains_key(&state.key))
            .map(|state| state.team)
            .collect::<BTreeSet<_>>();
        observing_teams.extend(self.last_known.keys().map(|(team, _)| *team));

        let mut contacts = Vec::new();
        for observing_team in observing_teams {
            let observers = current
                .values()
                .filter(|state| {
                    state.team == observing_team
                        && state.alive
                        && state.world_pose
                        && self.inputs.contains_key(&state.key)
                })
                .collect::<Vec<_>>();
            for target_state in current.values().filter(|state| {
                state.team != observing_team && self.inputs.contains_key(&state.key)
            }) {
                let Some(target) = contact_target(target_state.key) else {
                    continue;
                };
                let previous = self.last_known.get(&(observing_team, target)).cloned();
                let mut observer_inputs = Vec::new();
                let mut bot_lane_inputs = Vec::<(VehicleKey, ObserverTargetInput)>::new();
                let mut observed_targets = BTreeMap::<u32, FrozenActorState>::new();
                let mut observer_vehicles = BTreeMap::<u32, VehicleKey>::new();
                if target_state.alive && target_state.world_pose {
                    for observer_state in &observers {
                        let observer_id = observer_token(observer_state.key)
                            .expect("validated actor identity has a spotting token");
                        let Some(observer_profile) = self.inputs.get(&observer_state.key) else {
                            continue;
                        };
                        let Some(target_profile) = self.inputs.get(&target_state.key) else {
                            continue;
                        };
                        let pair_key = (observer_state.key, target_state.key);
                        let evidence = self.pair_evidence.get(&pair_key);
                        let fired_now = fired_recently(
                            now_us,
                            self.last_fire_us.get(&target_state.key).copied(),
                        );
                        let visibility = evidence
                            .and_then(|record| record.visibility.as_ref())
                            .filter(|record| visibility_usable(record, now_us, target, fired_now));
                        let lane = evidence
                            .and_then(|record| record.lane.as_ref())
                            .filter(|record| lane_usable(record, now_us));
                        let mut camouflage = target_profile.target;
                        camouflage.last_fired_at_us =
                            self.last_fire_us.get(&target_state.key).copied();
                        let input_for =
                            |pair: &FrozenPair,
                             native_visibility: Option<NativeVisibilityEvidence>,
                             native_firing_lane: Option<NativeFiringLaneEvidence>,
                             firing_lane_range_metres: f64| {
                                ObserverTargetInput {
                                    observer_id,
                                    target,
                                    now_us,
                                    target_alive: target_state.alive,
                                    distance_metres: distance_xz(
                                        pair.observer.state.position,
                                        pair.target.state.position,
                                    ),
                                    observer_speed_metres_per_second: pair
                                        .observer
                                        .state
                                        .speed_metres_per_second,
                                    observer_stationary_since_us: pair.observer.stationary_since_us,
                                    view: observer_profile.observer,
                                    target_speed_metres_per_second: pair
                                        .target
                                        .state
                                        .speed_metres_per_second,
                                    target_stationary_since_us: pair.target.stationary_since_us,
                                    camouflage,
                                    native_visibility,
                                    native_firing_lane,
                                    firing_lane_range_metres,
                                }
                            };
                        if observer_state.key.kind == VehicleKind::Bot {
                            if let Some(lane) = lane {
                                bot_lane_inputs.push((
                                    observer_state.key,
                                    input_for(
                                        &lane.pair,
                                        None,
                                        Some(lane.evidence),
                                        observer_state.firing_lane_range_metres.unwrap_or(0.0),
                                    ),
                                ));
                            }
                        }
                        let Some(visibility) = visibility else {
                            // Direct visibility, including proximity spotting,
                            // is unavailable without exact native evidence.
                            continue;
                        };
                        let native_lane = (observer_state.key.kind == VehicleKind::Bot)
                            .then_some(lane)
                            .flatten()
                            .filter(|record| record.pair.same_sample(&visibility.pair))
                            .map(|record| record.evidence);
                        observer_inputs.push(input_for(
                            &visibility.pair,
                            Some(visibility.evidence),
                            native_lane,
                            if observer_state.key.kind == VehicleKind::Bot {
                                observer_state.firing_lane_range_metres.unwrap_or(0.0)
                            } else {
                                0.0
                            },
                        ));
                        let target_frozen = &visibility.pair.target;
                        observed_targets.insert(observer_id, target_frozen.clone());
                        let replaced = observer_vehicles.insert(observer_id, observer_state.key);
                        debug_assert!(replaced.is_none());
                    }
                }
                let decision = evaluate_team_contact(TeamContactInput {
                    target,
                    target_alive: target_state.alive,
                    now_us,
                    previous_last_seen_us: previous.as_ref().map(|record| record.observed_at_us),
                    observers: observer_inputs,
                });
                let record = match decision.last_known {
                    LastKnownDecision::Refresh { observed_at_us } => {
                        let observer_id = *decision
                            .directly_visible_by_observer_ids
                            .iter()
                            .next()
                            .expect("visible team contact has a direct observer");
                        let observed = observed_targets
                            .get(&observer_id)
                            .expect("direct observer retained its frozen target");
                        let record = LastKnownRecord {
                            position: observed.state.position.into(),
                            speed_metres_per_second: observed.state.speed_metres_per_second,
                            health: target_state.health,
                            max_health: target_state.max_health,
                            observed_at_us,
                        };
                        self.last_known
                            .insert((observing_team, target), record.clone());
                        Some(record)
                    }
                    LastKnownDecision::Retain { .. } => previous,
                    LastKnownDecision::Forget => {
                        self.last_known.remove(&(observing_team, target));
                        None
                    }
                };
                let Some(record) = record else {
                    continue;
                };
                let mut visible_by_bot_ids = BTreeSet::new();
                let mut visible_by_player_ids = BTreeSet::new();
                for observer_id in &decision.directly_visible_by_observer_ids {
                    let vehicle = observer_vehicles
                        .get(observer_id)
                        .expect("direct observer retains its typed identity");
                    let id = u32::try_from(vehicle.id).expect("validated observer id remains u32");
                    match vehicle.kind {
                        VehicleKind::Bot => {
                            visible_by_bot_ids.insert(id);
                        }
                        VehicleKind::Player => {
                            visible_by_player_ids.insert(id);
                        }
                    }
                }
                let mut shootable_by_bot_ids = decision
                    .shootable_by_observer_ids
                    .iter()
                    .filter_map(|observer_id| {
                        let vehicle = observer_vehicles.get(observer_id)?;
                        (vehicle.kind == VehicleKind::Bot)
                            .then(|| u32::try_from(vehicle.id).ok())
                            .flatten()
                    })
                    .collect::<BTreeSet<_>>();
                if decision.visible {
                    for (vehicle, lane_input) in bot_lane_inputs {
                        if evaluate_observer_firing_lane(&lane_input, true).shootable {
                            shootable_by_bot_ids.insert(
                                u32::try_from(vehicle.id)
                                    .expect("validated bot observer id remains u32"),
                            );
                        }
                    }
                }
                contacts.push(CanonicalContact {
                    observing_team,
                    target,
                    target_team: target_state.team,
                    visible: decision.visible,
                    presentation_visible: record
                        .observed_at_us
                        .saturating_add(crate::spotting::CONTACT_LAST_KNOWN_MEMORY_US)
                        > now_us,
                    visible_by_bot_ids,
                    visible_by_player_ids,
                    shootable_by_bot_ids,
                    last_known_position: record.position,
                    last_known_speed_metres_per_second: record.speed_metres_per_second,
                    last_known_health: record.health,
                    max_health: record.max_health,
                    observed_at_us: record.observed_at_us,
                    expires_at_us: record
                        .observed_at_us
                        .saturating_add(crate::spotting::CONTACT_LAST_KNOWN_MEMORY_US),
                });
            }
        }
        self.latest_contacts = contacts.clone();
        Ok(contacts)
    }

    fn validate_envelope(
        &self,
        tick: Tick,
        intent: &NativeObservationIntent,
        batch_key: OracleV1BatchKey,
    ) -> Result<(), ContactAuthorityError> {
        let lineage = self.lineage.ok_or(ContactAuthorityError::MissingLineage)?;
        if intent.lineage != lineage || batch_key.lineage != lineage {
            return Err(ContactAuthorityError::InvalidNativeEnvelope { field: "lineage" });
        }
        if intent.apply_tick != tick
            || intent.issued_tick.checked_add(ORACLE_PIPELINE_TICKS) != Some(intent.apply_tick)
        {
            return Err(ContactAuthorityError::InvalidNativeEnvelope { field: "tick" });
        }
        if !valid_vehicle(intent.observer)
            || !valid_vehicle(intent.target)
            || intent.observer == intent.target
        {
            return Err(ContactAuthorityError::InvalidNativeEnvelope { field: "actor_id" });
        }
        Ok(())
    }

    fn frozen_pair(
        &self,
        tick: Tick,
        intent: &NativeObservationIntent,
        batch_key: OracleV1BatchKey,
    ) -> Result<Option<FrozenPair>, ContactAuthorityError> {
        self.validate_envelope(tick, intent, batch_key)?;
        let observer_key = intent.observer;
        let target_key = intent.target;
        if !self.inputs.contains_key(&observer_key) || !self.inputs.contains_key(&target_key) {
            return Ok(None);
        }
        let frame = self.frames.get(&intent.issued_tick).ok_or(
            ContactAuthorityError::MissingFrozenFrame {
                issued_tick: intent.issued_tick,
            },
        )?;
        let Some(observer) = frame.get(&observer_key) else {
            return Err(ContactAuthorityError::FrozenGeometryMismatch {
                vehicle: observer_key,
            });
        };
        let Some(target) = frame.get(&target_key) else {
            return Err(ContactAuthorityError::FrozenGeometryMismatch {
                vehicle: target_key,
            });
        };
        if observer.state.team == target.state.team
            || !same_position(observer.state.position, intent.source_position)
        {
            return Err(ContactAuthorityError::FrozenGeometryMismatch {
                vehicle: observer_key,
            });
        }
        if !same_position(target.state.position, intent.target_position) {
            return Err(ContactAuthorityError::FrozenGeometryMismatch {
                vehicle: target_key,
            });
        }
        if !observer.state.alive
            || !observer.state.world_pose
            || !target.state.alive
            || !target.state.world_pose
        {
            return Ok(None);
        }
        Ok(Some(FrozenPair {
            observer: observer.clone(),
            target: target.clone(),
            issued_tick: intent.issued_tick,
            apply_tick: intent.apply_tick,
            batch_key,
            evaluated_for_recent_fire: intent.evaluated_for_recent_fire,
        }))
    }
}

fn insert_visibility(
    evidence: &mut PairEvidence,
    next: VisibilityRecord,
) -> Result<(), ContactAuthorityError> {
    match evidence.visibility.as_ref() {
        Some(current) if next.pair.apply_tick < current.pair.apply_tick => {
            return Err(ContactAuthorityError::ConflictingNativeEvidence)
        }
        Some(current) if next.pair.apply_tick == current.pair.apply_tick => {
            if !next.pair.same_sample(&current.pair) || next.evidence != current.evidence {
                return Err(ContactAuthorityError::ConflictingNativeEvidence);
            }
            return Ok(());
        }
        _ => {}
    }
    evidence.visibility = Some(next);
    Ok(())
}

fn insert_lane(evidence: &mut PairEvidence, next: LaneRecord) -> Result<(), ContactAuthorityError> {
    match evidence.lane.as_ref() {
        Some(current) if next.pair.apply_tick < current.pair.apply_tick => {
            return Err(ContactAuthorityError::ConflictingNativeEvidence)
        }
        Some(current) if next.pair.apply_tick == current.pair.apply_tick => {
            if !next.pair.same_sample(&current.pair) || next.evidence != current.evidence {
                return Err(ContactAuthorityError::ConflictingNativeEvidence);
            }
            return Ok(());
        }
        _ => {}
    }
    evidence.lane = Some(next);
    Ok(())
}

fn actor_map(
    tick: Tick,
    actors: &[ContactActorState],
) -> Result<BTreeMap<VehicleKey, ContactActorState>, ContactAuthorityError> {
    let mut result = BTreeMap::new();
    for actor in actors {
        validate_actor_state(actor)?;
        if result.insert(actor.key, actor.clone()).is_some() {
            return Err(ContactAuthorityError::DuplicateActorState {
                tick,
                vehicle: actor.key,
            });
        }
    }
    Ok(result)
}

fn visibility_usable(
    record: &VisibilityRecord,
    now_us: u64,
    target: ContactTarget,
    fired_now: bool,
) -> bool {
    record.evidence.sampled_at_us <= now_us
        && now_us - record.evidence.sampled_at_us
            < visibility_cache_ttl_us(
                observer_token(record.pair.observer.state.key)
                    .expect("frozen observer has a validated spotting token"),
                target,
            )
        && record.evidence.evaluated_for_recent_fire == fired_now
}

fn lane_usable(record: &LaneRecord, now_us: u64) -> bool {
    record.evidence.sampled_at_us <= now_us
        && now_us - record.evidence.sampled_at_us <= SHOT_LANE_CACHE_US
}

fn distance_xz(first: Vec3, second: Vec3) -> f64 {
    (first.x - second.x).hypot(first.z - second.z)
}

fn contact_target(vehicle: VehicleKey) -> Option<ContactTarget> {
    let id = u32::try_from(vehicle.id).ok().filter(|id| *id > 0)?;
    let kind = match vehicle.kind {
        VehicleKind::Player => ContactTargetKind::Human,
        VehicleKind::Bot => ContactTargetKind::Bot,
    };
    Some(ContactTarget { kind, id })
}

/// Preserve every existing bot phase while giving players a disjoint adapter
/// namespace for the legacy `u32` spotting-law interface.
fn observer_token(vehicle: VehicleKey) -> Option<u32> {
    let id = u32::try_from(vehicle.id).ok().filter(|id| *id > 0)?;
    Some(match vehicle.kind {
        VehicleKind::Bot => id,
        VehicleKind::Player => id | (1 << 31),
    })
}

fn valid_vehicle(vehicle: VehicleKey) -> bool {
    vehicle.id > 0 && vehicle.id <= MAX_COMBAT_ID && u32::try_from(vehicle.id).is_ok()
}

fn validate_actor_state(state: &ContactActorState) -> Result<(), ContactAuthorityError> {
    let position = state.position;
    let valid_position = position.x.is_finite()
        && position.y.is_finite()
        && position.z.is_finite()
        && position.x.abs() <= MAX_WORLD_XZ
        && (MIN_WORLD_Y..=MAX_WORLD_Y).contains(&position.y)
        && position.z.abs() <= MAX_WORLD_XZ;
    let valid_lane = state.firing_lane_range_metres.is_none_or(|range| {
        range.is_finite() && (0.0..=SPG_SHOT_LANE_RANGE_METRES).contains(&range)
    });
    if !valid_vehicle(state.key)
        || !matches!(state.team, 1 | 2)
        || !valid_position
        || !state.speed_metres_per_second.is_finite()
        || state.speed_metres_per_second.abs() > MAX_ACTOR_SPEED_METRES_PER_SECOND
        || state.max_health == 0
        || state.health > state.max_health
        || state.alive != (state.health > 0)
        || !valid_lane
    {
        return Err(ContactAuthorityError::InvalidActorState { vehicle: state.key });
    }
    Ok(())
}

fn valid_spotting_input(input: AuthoritySpottingInput) -> bool {
    let observer = input.observer;
    let target = input.target;
    [
        observer.base_range_metres,
        observer.misc_factor,
        observer.crew_factor,
        observer.binocular_factor,
        target.moving,
        target.stationary,
        target.moving_aspect.additive,
        target.moving_aspect.multiplier,
        target.stationary_aspect.additive,
        target.stationary_aspect.multiplier,
        target.invisibility_factor_at_shot,
    ]
    .iter()
    .all(|value| value.is_finite())
        && observer.base_range_metres > 0.0
        && observer.misc_factor > 0.0
        && observer.crew_factor > 0.0
        && observer.binocular_factor >= 1.0
        && (!observer.has_binoculars && observer.binocular_delay_us == 0 || observer.has_binoculars)
        && target.moving >= 0.0
        && target.stationary >= 0.0
        && target.moving_aspect.multiplier >= 0.0
        && target.stationary_aspect.multiplier >= 0.0
        && (0.0..=1.0).contains(&target.invisibility_factor_at_shot)
        && (!target.has_camouflage_net && target.camouflage_net_delay_us == 0
            || target.has_camouflage_net)
        && target.last_fired_at_us.is_none()
}

fn same_position(first: Vec3, second: Vec3) -> bool {
    (first.x - second.x).abs() <= FROZEN_POSITION_EPSILON
        && (first.y - second.y).abs() <= FROZEN_POSITION_EPSILON
        && (first.z - second.z).abs() <= FROZEN_POSITION_EPSILON
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::authority_runtime::{NativeObservationEvidenceKind, NativeObservationPurpose};
    use crate::bot_sim::TargetKind;
    use crate::protocol::EntityRef;
    use crate::spotting::{
        CamouflageAspect, ObserverView, TargetCamouflage, ORDINARY_SHOT_LANE_RANGE_METRES,
        STILL_DEVICE_DELAY_US,
    };

    fn lineage() -> OracleLineage {
        OracleLineage {
            round_id: 4,
            authority_epoch: 2,
            oracle_generation: 1,
        }
    }

    fn key(kind: VehicleKind, id: u64) -> VehicleKey {
        VehicleKey { kind, id }
    }

    fn profile() -> AuthoritySpottingInput {
        AuthoritySpottingInput {
            observer: ObserverView {
                base_range_metres: 400.0,
                misc_factor: 1.0,
                crew_factor: 1.0,
                binocular_factor: 1.25,
                has_binoculars: false,
                binocular_delay_us: 0,
            },
            target: TargetCamouflage {
                moving: 0.1,
                stationary: 0.15,
                moving_aspect: CamouflageAspect::default(),
                stationary_aspect: CamouflageAspect::default(),
                has_camouflage_net: false,
                camouflage_net_delay_us: 0,
                invisibility_factor_at_shot: 0.25,
                last_fired_at_us: None,
            },
        }
    }

    fn actor(kind: VehicleKind, id: u64, team: u8, x: f64) -> ContactActorState {
        ContactActorState {
            key: key(kind, id),
            team,
            alive: true,
            world_pose: true,
            position: Vec3::new(x, 0.0, 0.0),
            speed_metres_per_second: 0.0,
            health: 100,
            max_health: 100,
            firing_lane_range_metres: (kind == VehicleKind::Bot)
                .then_some(ORDINARY_SHOT_LANE_RANGE_METRES),
        }
    }

    fn intent(
        issued_tick: Tick,
        target_kind: TargetKind,
        target_id: u32,
    ) -> NativeObservationIntent {
        let target = key(
            match target_kind {
                TargetKind::Bot => VehicleKind::Bot,
                TargetKind::Human => VehicleKind::Player,
            },
            u64::from(target_id),
        );
        intent_for(
            issued_tick,
            key(VehicleKind::Bot, 11),
            target,
            EntityRef {
                entity_id: 101,
                generation: 1,
            },
            EntityRef {
                entity_id: 202,
                generation: 1,
            },
        )
    }

    fn intent_for(
        issued_tick: Tick,
        observer: VehicleKey,
        target: VehicleKey,
        observer_entity: EntityRef,
        target_entity: EntityRef,
    ) -> NativeObservationIntent {
        NativeObservationIntent {
            purpose: NativeObservationPurpose::Discovery,
            observer,
            target,
            lineage: lineage(),
            observer_entity,
            target_entity,
            issued_tick,
            apply_tick: issued_tick + ORACLE_PIPELINE_TICKS,
            source_position: Vec3::new(0.0, 0.0, 0.0),
            target_position: Vec3::new(100.0, 0.0, 0.0),
            evaluated_for_recent_fire: false,
        }
    }

    fn batch_key(sequence: u64) -> OracleV1BatchKey {
        OracleV1BatchKey {
            lineage: lineage(),
            batch_seq: sequence.into(),
        }
    }

    fn authority(with_target_profile: bool) -> ContactAuthority {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        let mut inputs = BTreeMap::from([(key(VehicleKind::Bot, 11), profile())]);
        if with_target_profile {
            inputs.insert(key(VehicleKind::Player, 7), profile());
        }
        authority.install_inputs(inputs).unwrap();
        authority
            .record_observation_frame(
                10,
                &[
                    actor(VehicleKind::Bot, 11, 1, 0.0),
                    actor(VehicleKind::Player, 7, 2, 100.0),
                ],
            )
            .unwrap();
        authority
    }

    fn visibility_sample() -> NativeObservationSample {
        NativeObservationSample {
            intent: intent(10, TargetKind::Human, 7),
            batch_key: batch_key(3),
            line_of_sight: true,
            foliage_bonus: 0.0,
            evaluated_for_recent_fire: false,
        }
    }

    fn lane_sample() -> NativeFiringLaneSample {
        NativeFiringLaneSample {
            intent: intent(10, TargetKind::Human, 7),
            batch_key: batch_key(3),
            clear: true,
        }
    }

    #[test]
    fn exact_t3_pair_uses_frozen_geometry_and_drives_planner_contact() {
        let mut authority = authority(true);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 430.0),
        ];
        authority.record_observation_frame(13, &current).unwrap();
        let contacts = authority.evaluate_tick(13, &current).unwrap();
        assert_eq!(contacts.len(), 1);
        assert!(contacts[0].visible);
        assert!(contacts[0].presentation_visible);
        assert_eq!(contacts[0].visible_by_bot_ids, BTreeSet::from([11]));
        assert!(contacts[0].visible_by_player_ids.is_empty());
        assert_eq!(contacts[0].last_known_position.x, 100.0);
        assert_eq!(contacts[0].shootable_by_bot_ids, BTreeSet::from([11]));
        assert_eq!(
            contacts[0].planner_value(),
            json!({
                "observing_team": 1,
                "target_kind": "human",
                "target_id": 7,
                "target_team": 2,
                "visible": true,
                "shootable_by_bot_ids": [11],
                "x": 100.0,
                "y": 0.0,
                "z": 0.0,
                "health": 100,
                "max_health": 100,
                "speed": 0.0,
            })
        );
    }

    #[test]
    fn absent_exact_target_loadout_fails_closed() {
        let mut authority = authority(false);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(13, &current).unwrap();
        assert!(authority.evaluate_tick(13, &current).unwrap().is_empty());
    }

    #[test]
    fn unavailable_and_timeout_do_not_replace_a_successful_cache() {
        let mut authority = authority(true);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        let failed = FailedNativeObservation {
            intent: intent(11, TargetKind::Human, 7),
            batch_key: batch_key(4),
            evidence: NativeObservationEvidenceKind::Spotting,
            reason: "native_unavailable".to_owned(),
        };
        let timed_out = TimedOutNativeObservation {
            intent: intent(11, TargetKind::Human, 7),
            batch_key: batch_key(4),
            evidence: NativeObservationEvidenceKind::FiringLane,
        };
        // Give the failed sample its own exact frozen frame. Neither outcome
        // is admitted into the successful pair cache.
        authority
            .record_observation_frame(
                11,
                &[
                    actor(VehicleKind::Bot, 11, 1, 0.0),
                    actor(VehicleKind::Player, 7, 2, 100.0),
                ],
            )
            .unwrap();
        authority
            .ingest_native_tick(14, &[], &[], &[failed], &[timed_out])
            .unwrap();
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(14, &current).unwrap();
        let contacts = authority.evaluate_tick(14, &current).unwrap();
        assert!(contacts[0].visible);
        assert_eq!(contacts[0].shootable_by_bot_ids, BTreeSet::from([11]));
    }

    #[test]
    fn recent_fire_echo_mismatch_is_transactional_not_negative_evidence() {
        let mut authority = authority(true);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        let mut mismatched = visibility_sample();
        mismatched.evaluated_for_recent_fire = true;
        assert!(matches!(
            authority.ingest_native_tick(13, &[mismatched], &[], &[], &[]),
            Err(ContactAuthorityError::InvalidNativeEnvelope {
                field: "spotting_evidence"
            })
        ));
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(13, &current).unwrap();
        assert!(authority.evaluate_tick(13, &current).unwrap()[0].visible);
    }

    #[test]
    fn committed_fire_invalidates_the_old_echo_without_inventing_occlusion() {
        let mut authority = authority(true);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        assert!(authority.note_fire(key(VehicleKind::Player, 7), 13));
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(13, &current).unwrap();
        assert!(authority.evaluate_tick(13, &current).unwrap().is_empty());
        assert_eq!(
            authority.last_fire_at_us(key(VehicleKind::Player, 7)),
            Some(time_us_at_tick(13))
        );
    }

    #[test]
    fn player_only_team_directly_spots_from_exact_native_evidence() {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        authority
            .install_inputs(BTreeMap::from([
                (key(VehicleKind::Player, 7), profile()),
                (key(VehicleKind::Bot, 12), profile()),
            ]))
            .unwrap();
        let actors = [
            actor(VehicleKind::Player, 7, 1, 0.0),
            actor(VehicleKind::Bot, 12, 2, 100.0),
        ];
        authority.record_observation_frame(1, &actors).unwrap();
        let native_intent = intent_for(
            1,
            key(VehicleKind::Player, 7),
            key(VehicleKind::Bot, 12),
            EntityRef {
                entity_id: 701,
                generation: 1,
            },
            EntityRef {
                entity_id: 1201,
                generation: 1,
            },
        );
        authority
            .ingest_native_tick(
                4,
                &[NativeObservationSample {
                    intent: native_intent,
                    batch_key: batch_key(20),
                    line_of_sight: true,
                    foliage_bonus: 0.0,
                    evaluated_for_recent_fire: false,
                }],
                &[],
                &[],
                &[],
            )
            .unwrap();
        authority.record_observation_frame(4, &actors).unwrap();
        let contacts = authority.evaluate_tick(4, &actors).unwrap();
        assert_eq!(contacts.len(), 1);
        assert_eq!(contacts[0].observing_team, 1);
        assert_eq!(contacts[0].target.kind, ContactTargetKind::Bot);
        assert!(contacts[0].visible_by_bot_ids.is_empty());
        assert_eq!(contacts[0].visible_by_player_ids, BTreeSet::from([7]));
        assert!(contacts[0].shootable_by_bot_ids.is_empty());
    }

    #[test]
    fn same_numeric_player_and_bot_observers_keep_distinct_wire_sets() {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        authority
            .install_inputs(BTreeMap::from([
                (key(VehicleKind::Player, 11), profile()),
                (key(VehicleKind::Bot, 11), profile()),
                (key(VehicleKind::Player, 7), profile()),
            ]))
            .unwrap();
        let actors = [
            actor(VehicleKind::Player, 11, 1, 0.0),
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(10, &actors).unwrap();
        let player_intent = intent_for(
            10,
            key(VehicleKind::Player, 11),
            key(VehicleKind::Player, 7),
            EntityRef {
                entity_id: 111,
                generation: 1,
            },
            EntityRef {
                entity_id: 700,
                generation: 1,
            },
        );
        let bot_intent = intent_for(
            10,
            key(VehicleKind::Bot, 11),
            key(VehicleKind::Player, 7),
            EntityRef {
                entity_id: 112,
                generation: 1,
            },
            EntityRef {
                entity_id: 700,
                generation: 1,
            },
        );
        authority
            .ingest_native_tick(
                13,
                &[
                    NativeObservationSample {
                        intent: player_intent,
                        batch_key: batch_key(21),
                        line_of_sight: true,
                        foliage_bonus: 0.0,
                        evaluated_for_recent_fire: false,
                    },
                    NativeObservationSample {
                        intent: bot_intent.clone(),
                        batch_key: batch_key(21),
                        line_of_sight: true,
                        foliage_bonus: 0.0,
                        evaluated_for_recent_fire: false,
                    },
                ],
                &[NativeFiringLaneSample {
                    intent: bot_intent,
                    batch_key: batch_key(21),
                    clear: true,
                }],
                &[],
                &[],
            )
            .unwrap();
        authority.record_observation_frame(13, &actors).unwrap();
        let contact = authority
            .evaluate_tick(13, &actors)
            .unwrap()
            .into_iter()
            .find(|contact| contact.observing_team == 1)
            .unwrap();
        assert_eq!(contact.visible_by_bot_ids, BTreeSet::from([11]));
        assert_eq!(contact.visible_by_player_ids, BTreeSet::from([11]));
        assert_eq!(contact.shootable_by_bot_ids, BTreeSet::from([11]));
    }

    #[test]
    fn missing_native_evidence_fails_closed_even_inside_proximity_range() {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        authority
            .install_inputs(BTreeMap::from([
                (key(VehicleKind::Player, 7), profile()),
                (key(VehicleKind::Bot, 12), profile()),
            ]))
            .unwrap();
        let actors = [
            actor(VehicleKind::Player, 7, 1, 0.0),
            actor(VehicleKind::Bot, 12, 2, 10.0),
        ];
        authority.record_observation_frame(1, &actors).unwrap();
        assert!(authority.evaluate_tick(1, &actors).unwrap().is_empty());
    }

    #[test]
    fn wrong_observer_kind_or_id_is_rejected_against_frozen_geometry() {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        authority
            .install_inputs(BTreeMap::from([
                (key(VehicleKind::Bot, 11), profile()),
                (key(VehicleKind::Player, 11), profile()),
                (key(VehicleKind::Bot, 12), profile()),
                (key(VehicleKind::Player, 7), profile()),
            ]))
            .unwrap();
        authority
            .record_observation_frame(
                10,
                &[
                    actor(VehicleKind::Bot, 11, 1, 0.0),
                    actor(VehicleKind::Player, 11, 1, 25.0),
                    actor(VehicleKind::Bot, 12, 1, 50.0),
                    actor(VehicleKind::Player, 7, 2, 100.0),
                ],
            )
            .unwrap();
        for observer in [key(VehicleKind::Player, 11), key(VehicleKind::Bot, 12)] {
            let sample = NativeObservationSample {
                intent: intent_for(
                    10,
                    observer,
                    key(VehicleKind::Player, 7),
                    EntityRef {
                        entity_id: 101,
                        generation: 1,
                    },
                    EntityRef {
                        entity_id: 202,
                        generation: 1,
                    },
                ),
                batch_key: batch_key(22),
                line_of_sight: true,
                foliage_bonus: 0.0,
                evaluated_for_recent_fire: false,
            };
            assert!(matches!(
                authority.ingest_native_tick(13, &[sample], &[], &[], &[]),
                Err(ContactAuthorityError::FrozenGeometryMismatch { vehicle })
                    if vehicle == observer
            ));
        }
    }

    #[test]
    fn stillness_resets_across_an_unobserved_frame_gap() {
        let mut authority = ContactAuthority::new();
        authority.bind_lineage(lineage()).unwrap();
        authority
            .install_inputs(BTreeMap::from([
                (key(VehicleKind::Bot, 11), profile()),
                (key(VehicleKind::Player, 7), profile()),
            ]))
            .unwrap();
        let actors = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 49.0),
        ];
        authority.record_observation_frame(1, &actors).unwrap();
        authority.record_observation_frame(100, &actors).unwrap();
        let observer = authority.frames[&100][&key(VehicleKind::Bot, 11)].clone();
        assert_eq!(observer.stationary_since_us, Some(time_us_at_tick(100)));
    }

    #[test]
    fn stale_success_withdraws_planner_visibility_but_retains_last_known_payload() {
        let mut authority = authority(true);
        authority
            .ingest_native_tick(13, &[visibility_sample()], &[lane_sample()], &[], &[])
            .unwrap();
        let current = [
            actor(VehicleKind::Bot, 11, 1, 0.0),
            actor(VehicleKind::Player, 7, 2, 100.0),
        ];
        authority.record_observation_frame(13, &current).unwrap();
        assert!(authority.evaluate_tick(13, &current).unwrap()[0].visible);
        authority.record_observation_frame(30, &current).unwrap();
        let hidden = authority.evaluate_tick(30, &current).unwrap();
        assert_eq!(hidden.len(), 1);
        assert!(!hidden[0].visible);
        assert!(hidden[0].presentation_visible);
        assert!(hidden[0].visible_by_bot_ids.is_empty());
        assert!(hidden[0].visible_by_player_ids.is_empty());
        assert!(hidden[0].shootable_by_bot_ids.is_empty());
        assert_eq!(hidden[0].last_known_position.x, 100.0);
        assert_eq!(
            hidden[0].planner_value(),
            json!({
                "observing_team": 1,
                "target_kind": "human",
                "target_id": 7,
                "target_team": 2,
                "visible": false,
                "shootable_by_bot_ids": [],
            })
        );
    }

    #[test]
    fn descriptor_dynamic_fire_timestamp_is_rejected_at_install() {
        let mut invalid = profile();
        invalid.target.last_fired_at_us = Some(1);
        let mut authority = ContactAuthority::new();
        assert!(matches!(
            authority.install_inputs(BTreeMap::from([(key(VehicleKind::Bot, 11), invalid)])),
            Err(ContactAuthorityError::InvalidSpottingInput { .. })
        ));
        assert_eq!(STILL_DEVICE_DELAY_US, 3_000_000);
    }
}
