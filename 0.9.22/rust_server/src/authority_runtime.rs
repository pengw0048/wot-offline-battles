//! Single-threaded orchestration for the Rust LAN battle authority.
//!
//! This module deliberately stops at the native-world boundary. It advances
//! deterministic bot state and projectile flight, but native collision facts
//! are accepted only through [`OracleBroker`] at their exact `T + 3` apply
//! tick. Projectile terminal proposals contain geometry, never invented
//! penetration, critical-module, splash, or damage verdicts.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::f64::consts::{FRAC_PI_2, PI};

use serde_json::{Map, Value};
use thiserror::Error;

use crate::bot_sim::{
    angle_delta, fixed_dt_us, time_us_at_tick, wrapped, BotEvent, BotOrder, BotSimulator, BotState,
    CombatEvent, CombatMode, CriticalState, OracleQueryId, OracleQueryIntent, OracleReceipts,
    PoseEvent, ProjectileLaunchEvent, RecoveryMode, SimError, TargetKind, TargetState, TickInput,
    TrafficBody, Vec3,
};
use crate::combat::{
    BodyPose, FireIntentBinding, VehicleKey, VehicleKind, FIRE_INTENT_HISTORY, MAX_COMBAT_ID,
};
use crate::navgraph::{NavGraph, NavRouter, NavTarget};
use crate::oracle::{
    AppliedOracleBatch, OracleBroker, OracleBrokerError, OracleReplyDisposition,
    TimedOutOracleBatch,
};
use crate::oracle_adapter::{
    FailedOracleIntent, OracleAdapter, OracleAdapterError, OracleEntityMap, SimulationEntity,
};
use crate::planner::{BotPlanner, MAX_CONTACTS_PER_TEAM};
use crate::player_environment::{PlayerEnvironmentEvidence, PlayerGroundEvidence};
use crate::projectile::{ProjectileCursor, ProjectileOutcome, ProjectileRecord, ProjectileVec3};
use crate::projectile_sim::{
    FlightTarget, ProjectileFlightDecision, ProjectileFlightError, ProjectileFlightIntegrator,
    ProjectilePlanId, ProjectileTerminalCause, ProjectileTerminalProposal, MAX_FROZEN_HE_TARGETS,
};
use crate::protocol::{
    BatchSequence, DestructibleHullEvidence, DestructibleHullEvidenceQuery, EntityRef,
    ExplosionEvidence, ExplosionEvidenceQuery, ExplosionTargetPose, FiringLaneEvidenceQuery,
    OracleLineage, OracleOperation, OracleV1BatchKey, OracleV1BatchReply, OracleV1BatchRequest,
    OracleV1Query, OracleV1ResultStatus, PlayerMuzzleEvidence, PlayerMuzzleEvidenceQuery,
    QueryOutcome, RamContactArmorEvidenceQuery, RamContactPose, SpottingEvidenceQuery, Tick,
    TransformSample, Vec3 as OracleVec3, WorldRevision, MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
    MAX_DESTRUCTIBLE_HULL_CANDIDATES, MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS,
    MAX_ORACLE_PRIMITIVE_OPERATIONS, MAX_RAM_CONTACT_COORDINATE_M, MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    MAX_RAM_CONTACT_POSE_DISTANCE_M, ORACLE_PIPELINE_TICKS, ORACLE_PROTOCOL_VERSION,
    RAM_CONTACT_NORMAL_TOLERANCE,
};
use crate::ram::{
    NativeContactArmor, NativeRamContactEvidence, RamDamageProfile, RamPair, RamShape,
    RamSourceCursor, RamVehicleContactEvidence, MAX_RAM_POSE_RETRY_HISTORY,
};
use crate::spotting::fired_recently;

/// Supplemental discovery scans are deliberately smaller than one wire batch.
/// Each visibility intent expands to two native primitives, so this reserves
/// most of the 64-query batch for motion, firing, and projectile work.
pub const MAX_NATIVE_OBSERVATION_PAIRS_PER_TICK: usize = 8;

/// One hull evidence query has a fixed worst-case cost of 32 native
/// primitives, so no batch may contain more than eight actors.
pub const MAX_DESTRUCTIBLE_HULL_ACTORS_PER_BATCH: usize =
    MAX_ORACLE_PRIMITIVE_OPERATIONS / MAX_DESTRUCTIBLE_HULL_CANDIDATES;

/// Two deterministic lanes in each nine-tick window average exactly 0.15 s
/// at 30 Hz while keeping every admitted hull receipt independently routed.
pub const DESTRUCTIBLE_HULL_CADENCE_TICKS: Tick = 9;
pub const DESTRUCTIBLE_HULL_SAMPLES_PER_CYCLE: Tick = 2;
const DESTRUCTIBLE_HULL_PROOF_SECONDS: f64 = 0.15;
const DESTRUCTIBLE_HULL_MOVING_EPSILON_MPS: f64 = 0.0001;

const NATIVE_OBSERVATION_SOURCE_HEIGHT: f64 = 1.5;
const NATIVE_OBSERVATION_COLLISION_MASK: u32 = 128;
const PLAYER_GROUND_SUPPORT_NORMAL_Y: f64 = 0.5;
const MAX_RAM_SOURCE_TIME_US: u64 = 9_007_199_254_740_991;
const HE_EXPLOSION_EVIDENCE_RETRY_HISTORY: usize = 128;

/// Complete logical-to-native mapping for one immutable native-space
/// incarnation. A reconnect or space reload must use a new `oracle_generation`
/// and therefore a new runtime rather than changing this donation in place.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NativeEntityDonation {
    pub lineage: OracleLineage,
    pub oracle_space: EntityRef,
    pub bots: BTreeMap<u32, EntityRef>,
    pub humans: BTreeMap<u32, EntityRef>,
}

/// Logical projectile target. The runtime resolves its native `EntityRef`
/// from the fenced donation so callers cannot smuggle an unfenced hit target.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AuthorityProjectileTarget {
    pub vehicle: VehicleKey,
    pub wreck: bool,
}

/// Canonical combat state copied from the battle ledger into the bot
/// simulator. This is intentionally not a damage API: callers provide an
/// already-committed state, and health may only stay equal or decrease.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanonicalBotCombatState {
    pub health: u32,
    pub display_health: u32,
    pub alive: bool,
    pub death_reason: Option<u8>,
    pub critical: CriticalState,
}

/// One horizontal pose and velocity correction prepared by ramming authority.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BotRamDelta {
    pub bot_id: u32,
    pub correction_x: f64,
    pub correction_z: f64,
    pub velocity_x: f64,
    pub velocity_z: f64,
}

/// Transactional bot state produced before the battle ledger is committed.
///
/// Callers may project poses from this value, then commit it only after the
/// corresponding battle-ledger transaction succeeds.
#[derive(Clone, Debug)]
pub struct PreparedBotRamMutation {
    lineage: OracleLineage,
    tick: Tick,
    bots: BTreeMap<u32, BotSimulator>,
    corrections: BTreeMap<u32, Vec3>,
}

impl PreparedBotRamMutation {
    pub fn bot_ids(&self) -> impl Iterator<Item = u32> + '_ {
        self.bots.keys().copied()
    }

    pub fn bot_state(&self, bot_id: u32) -> Option<&BotState> {
        self.bots.get(&bot_id).map(BotSimulator::state)
    }

    pub fn bot_ram_velocity(&self, bot_id: u32) -> Option<Vec3> {
        self.bots.get(&bot_id).map(BotSimulator::ram_velocity)
    }
}

/// JSON-compatible strategic inputs owned by `PreparedRound` and
/// `BattleEngine`. Values are borrowed so refreshing or locally realising an
/// order does not clone the full public snapshot.
#[derive(Clone, Copy, Debug)]
pub struct PlannerBuildInput<'a> {
    pub manifest: &'a Value,
    pub bot_states: &'a Value,
    pub players: &'a Value,
    pub now: f64,
    pub contacts: Option<&'a Value>,
    pub defense: Option<&'a Value>,
}

/// Strategic fields which were consumed to produce a typed simulation order.
/// Keeping this alongside `BotOrder` makes route and tactical decisions
/// inspectable without letting untyped JSON cross the simulation boundary.
#[derive(Clone, Debug, PartialEq)]
pub struct PlannerOrderTrace {
    pub move_position: Vec3,
    pub aim_position: Option<Vec3>,
    pub face_position: Option<Vec3>,
    pub combat_mode: String,
    pub throttle_override: Option<f64>,
    pub desired_range: f64,
    pub route_id: String,
    pub route_index: usize,
    pub route_anchor: Vec3,
    pub route_join: bool,
}

/// Complete order set for one planner revision. Dead bots receive an explicit
/// typed hold, while a missing live-bot order is rejected.
#[derive(Clone, Debug, PartialEq)]
pub struct TypedPlannerOrders {
    pub revision: u64,
    pub orders: BTreeMap<u32, BotOrder>,
    pub traces: BTreeMap<u32, PlannerOrderTrace>,
}

/// Inputs that vary at one fixed simulation boundary.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct AuthorityTickInput {
    pub tick: Tick,
    /// Every registered bot must have exactly one order, including dead bots.
    pub orders: BTreeMap<u32, BotOrder>,
    /// Human bodies participate in deterministic local traffic avoidance.
    pub human_traffic: Vec<TrafficBody>,
    /// Alive bot roots which currently have a canonical native world pose.
    /// Hull evidence is never requested for an omitted bot.
    pub world_pose_bots: BTreeSet<u32>,
    /// Vehicles considered by native projectile hit tests.
    pub projectile_targets: Vec<AuthorityProjectileTarget>,
    pub static_collision_mask: u32,
}

/// Bot-owned state changes emitted for the battle layer to commit.
#[derive(Clone, Debug, PartialEq)]
pub struct AuthorityBotTick {
    pub bot_id: u32,
    pub pose: PoseEvent,
    pub environment: Vec<CombatEvent>,
    pub launches: Vec<ProjectileLaunchEvent>,
}

/// Stable identity of one player fire intent at the native-muzzle boundary.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct PlayerMuzzleIntentKey {
    pub player_id: u64,
    pub intent_seq: u64,
}

/// Result of admitting a player muzzle query. Exact retries never register or
/// resend native work, which keeps the broker's query-generation fence intact.
#[derive(Clone, Debug, PartialEq)]
pub enum PlayerMuzzleSchedule {
    New {
        request: OracleV1BatchRequest,
    },
    ExactRetry {
        key: OracleV1BatchKey,
        apply_tick: Tick,
    },
}

/// Native muzzle pose released at the fire intent's exact apply tick. This is
/// evidence for a later launch admission; it is not itself a projectile.
#[derive(Clone, Debug, PartialEq)]
pub struct PlayerMuzzleSample {
    pub binding: FireIntentBinding,
    pub entity: EntityRef,
    pub batch_key: OracleV1BatchKey,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub transform: TransformSample,
    pub barrel_under_water: bool,
}

/// Why an admitted player muzzle query could not produce launch evidence.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerMuzzleFailureReason {
    Unavailable,
    TimedOut,
}

/// A player muzzle query which could not produce native evidence at its exact
/// apply tick. This is a terminal result for the fire intent, not a battle or
/// native-oracle failure.
#[derive(Clone, Debug, PartialEq)]
pub struct FailedPlayerMuzzle {
    pub binding: FireIntentBinding,
    pub entity: EntityRef,
    pub batch_key: OracleV1BatchKey,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub reason: PlayerMuzzleFailureReason,
}

/// Immutable source contact admitted for one exact native armour query.
/// `pair` fixes the canonical first/second ordering used by every pose,
/// profile, moving flag, wire entity and returned damage input.
#[derive(Clone, Debug, PartialEq)]
pub struct RamContactArmorIntent {
    pub pair: RamPair,
    pub cursor: RamSourceCursor,
    pub source_time_us: u64,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub first_pose: RamContactPose,
    pub second_pose: RamContactPose,
    pub contact_point: Vec3,
    /// Canonical second-to-first unit normal.
    pub contact_normal: Vec3,
    pub first_profile: RamDamageProfile,
    pub second_profile: RamDamageProfile,
    pub first_moving: bool,
    pub second_moving: bool,
}

/// Stable identity for exact retries and episode/frontier de-duplication.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct RamContactArmorIntentKey {
    pub pair: RamPair,
    pub cursor: RamSourceCursor,
}

/// Result of scheduling an atomic two-sided native armour probe.
#[derive(Clone, Debug, PartialEq)]
pub enum RamContactArmorSchedule {
    New {
        request: OracleV1BatchRequest,
    },
    ExactRetry {
        key: OracleV1BatchKey,
        apply_tick: Tick,
    },
}

/// Fully validated RAM oracle registration staged against cloned broker
/// state. Committing this token cannot fail and is kept adjacent to the
/// enclosing battle-ledger commit.
pub struct PreparedRamContactArmorBatch {
    lineage: OracleLineage,
    tick: Tick,
    broker: OracleBroker,
    adapter: OracleAdapter,
    next_batch_seq: BatchSequence,
    routes: BTreeMap<OracleV1BatchKey, PendingRoute>,
    records: BTreeMap<RamContactArmorIntentKey, RamContactArmorRecord>,
    pending: BTreeSet<RamContactArmorIntentKey>,
    batches: BTreeMap<OracleV1BatchKey, Vec<RamContactArmorIntentKey>>,
    latest: BTreeMap<RamPair, (RamSourceCursor, u64)>,
    query_generations: BTreeMap<String, u64>,
    requests: Vec<OracleV1BatchRequest>,
}

impl PreparedRamContactArmorBatch {
    pub fn requests(&self) -> &[OracleV1BatchRequest] {
        &self.requests
    }
}

/// One target pose frozen when an HE projectile terminal is released.
/// Native entity identity is resolved only from the immutable runtime donation.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct HeExplosionEvidenceTargetIntent {
    pub vehicle: VehicleKey,
    pub target_pose: ExplosionTargetPose,
}

/// One atomic HE explosion-evidence batch admitted from a terminal projectile.
/// The impact must exactly echo the terminal proposal registered by this
/// runtime; incoming direction and calibre remain native-query inputs, never
/// native gameplay verdicts.
#[derive(Clone, Debug, PartialEq)]
pub struct HeExplosionEvidenceIntent {
    pub plan_id: ProjectilePlanId,
    pub projectile_id: String,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub impact: ProjectileVec3,
    pub incoming_direction: ProjectileVec3,
    pub caliber_mm: f64,
    pub targets: Vec<HeExplosionEvidenceTargetIntent>,
}

impl HeExplosionEvidenceIntent {
    /// Build the query inputs from the exact tracked projectile record and the
    /// terminal proposal which caused the explosion. This keeps calibre and
    /// incoming direction on the same projectile lineage as `plan_id`.
    pub fn from_terminal(
        record: &ProjectileRecord,
        terminal: &ProjectileTerminalProposal,
        targets: Vec<HeExplosionEvidenceTargetIntent>,
    ) -> Option<Self> {
        let source = he_projectile_source_binding(record)?;
        if terminal.resolution.projectile_id != record.projectile_id
            || terminal.resolution.round_id != record.launch.round_id
            || terminal.resolution.authority_epoch != record.launch.authority_epoch
            || terminal.resolution.outcome != ProjectileOutcome::Impact
            || !matches!(
                terminal.cause,
                ProjectileTerminalCause::Direct { .. }
                    | ProjectileTerminalCause::Wreck { .. }
                    | ProjectileTerminalCause::Terrain { .. }
                    | ProjectileTerminalCause::DestructibleBacking { .. }
                    | ProjectileTerminalCause::Destructible { .. }
            )
        {
            return None;
        }
        Some(Self {
            plan_id: terminal.plan_id,
            projectile_id: record.projectile_id.clone(),
            issued_tick: terminal.applied_tick,
            apply_tick: terminal.applied_tick.checked_add(ORACLE_PIPELINE_TICKS)?,
            impact: terminal.resolution.impact?,
            incoming_direction: he_incoming_direction(
                source,
                terminal.resolution.resolved_time_ms,
            )?,
            caliber_mm: source.caliber_mm,
            targets,
        })
    }
}

/// Stable terminal identity used for exact retries and due-batch routing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct HeExplosionEvidenceIntentKey {
    pub plan_id: ProjectilePlanId,
}

/// Result of scheduling one atomic HE evidence batch. Exact retries never
/// re-register native work or advance any generation/sequence fence.
#[derive(Clone, Debug, PartialEq)]
pub enum HeExplosionEvidenceSchedule {
    New {
        request: OracleV1BatchRequest,
    },
    ExactRetry {
        key: OracleV1BatchKey,
        apply_tick: Tick,
    },
}

/// One strictly routed target receipt. Keeping the exact query beside the
/// evidence lets the pure Rust resolver re-check the frozen pose echo.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeHeExplosionTargetEvidence {
    pub vehicle: VehicleKey,
    pub entity: EntityRef,
    pub query: ExplosionEvidenceQuery,
    pub evidence: ExplosionEvidence,
}

/// All target facts for one projectile terminal. This sample is emitted only
/// when every target query succeeds; partial native evidence is discarded.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeHeExplosionEvidenceSample {
    pub key: HeExplosionEvidenceIntentKey,
    pub projectile_id: String,
    pub batch_key: OracleV1BatchKey,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub targets: Vec<NativeHeExplosionTargetEvidence>,
}

/// Why one native line-of-sight intent was issued.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NativeObservationPurpose {
    /// Bounded round-robin discovery, independent of an existing target.
    Discovery,
    /// A target already selected by the planner and re-proved for firing.
    FireGate,
}

/// Which pair-specific native fact succeeded, failed, or timed out.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum NativeObservationEvidenceKind {
    Spotting,
    FiringLane,
}

/// Typed identity of one observation pair at a fixed pipeline boundary.
///
/// Bot-simulator intents still originate with a bot-scoped `OracleQueryId`,
/// but every native observation is translated to this vehicle-scoped key
/// before it shares discovery state with player observers.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct NativeObservationQueryId {
    pub observer: VehicleKey,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
}

/// Identity and frozen geometry of one native observation intent.
///
/// Descriptor-derived view/camouflage still belongs to the Rust spotting law;
/// this intent freezes every pair-specific input donated by the native client.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeObservationIntent {
    pub purpose: NativeObservationPurpose,
    pub observer: VehicleKey,
    pub target: VehicleKey,
    pub lineage: OracleLineage,
    pub observer_entity: EntityRef,
    pub target_entity: EntityRef,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    /// Frozen vehicle root positions. Native visibility and direct-fire lanes
    /// apply their own distinct endpoint heights and trimming laws.
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub evaluated_for_recent_fire: bool,
}

/// Successful exact-T+3 visibility and prebaked-foliage evidence.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeObservationSample {
    pub intent: NativeObservationIntent,
    pub batch_key: OracleV1BatchKey,
    pub line_of_sight: bool,
    pub foliage_bonus: f64,
    pub evaluated_for_recent_fire: bool,
}

/// Independent exact-T+3 direct barrel-lane evidence for the same pair.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeFiringLaneSample {
    pub intent: NativeObservationIntent,
    pub batch_key: OracleV1BatchKey,
    pub clear: bool,
}

/// An applied native batch whose visibility primitives were unavailable or
/// invalid. No negative visibility observation may be inferred from this.
#[derive(Clone, Debug, PartialEq)]
pub struct FailedNativeObservation {
    pub intent: NativeObservationIntent,
    pub batch_key: OracleV1BatchKey,
    pub evidence: NativeObservationEvidenceKind,
    pub reason: String,
}

/// A native observation that had no reply at its exact T+3 boundary. No
/// negative visibility observation may be inferred from a timeout.
#[derive(Clone, Debug, PartialEq)]
pub struct TimedOutNativeObservation {
    pub intent: NativeObservationIntent,
    pub batch_key: OracleV1BatchKey,
    pub evidence: NativeObservationEvidenceKind,
}

/// Successful pieces of one actor-scoped ground/water batch, released only
/// at the batch's exact T+3 boundary. A missing field means that native
/// operation was unavailable; a proven no-hit/no-water result remains a
/// successful field with an explicit empty value.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NativePlayerEnvironmentSample {
    pub player_id: u64,
    pub evidence: PlayerEnvironmentEvidence,
}

/// Successful read-only hull evidence released at its exact T+3 boundary.
/// Motion and pose fields are the frozen issue-tick values used by the native
/// query; the battle layer must not substitute the actor's later pose.
#[derive(Clone, Debug, PartialEq)]
pub struct NativeDestructibleHullSample {
    pub vehicle: VehicleKey,
    pub batch_key: OracleV1BatchKey,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub position: Vec3,
    pub yaw: f64,
    pub frame_travel: f64,
    pub kinetic_speed: f64,
    pub evidence: DestructibleHullEvidence,
}

/// Native work released at the start of one exact 30 Hz boundary.
///
/// This output deliberately excludes bot simulation and newly registered
/// oracle requests. The battle layer must commit every due authority effect
/// before consuming the matching [`ReleasedAuthorityTick`] permit.
#[derive(Clone, Debug, PartialEq)]
pub struct AuthorityDueOutput {
    pub tick: Tick,
    pub failed_oracle_intents: Vec<FailedOracleIntent>,
    pub timed_out_bot_intents: Vec<OracleQueryId>,
    pub player_muzzles: Vec<PlayerMuzzleSample>,
    pub failed_player_muzzles: Vec<FailedPlayerMuzzle>,
    /// Atomic two-sided native armour facts released only at their exact T+3
    /// boundary.
    pub ram_contact_evidence: Vec<NativeRamContactEvidence>,
    /// Applied probes whose native side returned `unavailable`. The battle
    /// layer uses the exact key to terminalize its frozen impact fail-closed.
    pub unavailable_ram_contacts: Vec<RamContactArmorIntentKey>,
    /// Probes whose exact T+3 boundary elapsed without a native reply.
    pub timed_out_ram_contacts: Vec<RamContactArmorIntentKey>,
    /// Atomic, generation-fenced HE facts released at the exact T+3 boundary.
    /// No damage or occlusion verdict is carried by these samples.
    pub he_explosion_evidence: Vec<NativeHeExplosionEvidenceSample>,
    /// Applied HE batches containing at least one unavailable target. Partial
    /// successful facts from the same batch are never released.
    pub unavailable_he_explosions: Vec<HeExplosionEvidenceIntentKey>,
    /// HE batches with no receipt at their exact T+3 boundary.
    pub timed_out_he_explosions: Vec<HeExplosionEvidenceIntentKey>,
    pub native_observations: Vec<NativeObservationSample>,
    pub native_firing_lanes: Vec<NativeFiringLaneSample>,
    pub failed_native_observations: Vec<FailedNativeObservation>,
    pub timed_out_native_observations: Vec<TimedOutNativeObservation>,
    pub player_environment: Vec<NativePlayerEnvironmentSample>,
    pub destructible_hulls: Vec<NativeDestructibleHullSample>,
    /// Progress cursors and native-geometry terminal proposals. A terminal
    /// proposal is not a combat or damage verdict.
    pub projectile_decisions: Vec<ProjectileFlightDecision>,
}

impl AuthorityDueOutput {
    pub fn projectile_progress(&self) -> impl Iterator<Item = &ProjectileCursor> {
        self.projectile_decisions
            .iter()
            .filter_map(|decision| match decision {
                ProjectileFlightDecision::Progress { cursor, .. } => Some(cursor),
                _ => None,
            })
    }

    pub fn projectile_terminals(&self) -> impl Iterator<Item = &ProjectileTerminalProposal> {
        self.projectile_decisions
            .iter()
            .filter_map(|decision| match decision {
                ProjectileFlightDecision::Terminal(proposal) => Some(proposal),
                _ => None,
            })
    }
}

/// Bot simulation and newly registered native work produced after due effects
/// have been committed by the battle layer.
#[derive(Clone, Debug, PartialEq)]
pub struct AuthorityStepOutput {
    pub tick: Tick,
    pub bots: Vec<AuthorityBotTick>,
    pub oracle_requests: Vec<OracleV1BatchRequest>,
}

/// Opaque, single-use permission to simulate the boundary released by
/// [`AuthorityRuntime::release_due`].
///
/// The permit intentionally does not implement `Clone`. Its private receipt
/// payload can be consumed only by this module's step or terminal-close APIs.
#[must_use = "a released authority tick must be stepped or terminally closed"]
pub struct ReleasedAuthorityTick {
    lineage: OracleLineage,
    tick: Tick,
    receipts: BTreeMap<u32, OracleReceipts>,
}

impl ReleasedAuthorityTick {
    pub fn tick(&self) -> Tick {
        self.tick
    }
}

/// Compatibility aggregate for callers which still advance a boundary in one
/// call. Production battle orchestration should use the explicit due/step API.
#[derive(Clone, Debug, PartialEq)]
pub struct AuthorityTickOutput {
    pub tick: Tick,
    pub bots: Vec<AuthorityBotTick>,
    pub oracle_requests: Vec<OracleV1BatchRequest>,
    pub failed_oracle_intents: Vec<FailedOracleIntent>,
    pub timed_out_bot_intents: Vec<OracleQueryId>,
    pub player_muzzles: Vec<PlayerMuzzleSample>,
    pub failed_player_muzzles: Vec<FailedPlayerMuzzle>,
    /// Atomic two-sided native armour facts released only at their exact T+3
    /// boundary.
    pub ram_contact_evidence: Vec<NativeRamContactEvidence>,
    /// Applied probes whose native side returned `unavailable`. The battle
    /// layer uses the exact key to terminalize its frozen impact fail-closed.
    pub unavailable_ram_contacts: Vec<RamContactArmorIntentKey>,
    /// Probes whose exact T+3 boundary elapsed without a native reply.
    pub timed_out_ram_contacts: Vec<RamContactArmorIntentKey>,
    /// Atomic, generation-fenced HE facts released at the exact T+3 boundary.
    /// No damage or occlusion verdict is carried by these samples.
    pub he_explosion_evidence: Vec<NativeHeExplosionEvidenceSample>,
    /// Applied HE batches containing at least one unavailable target. Partial
    /// successful facts from the same batch are never released.
    pub unavailable_he_explosions: Vec<HeExplosionEvidenceIntentKey>,
    /// HE batches with no receipt at their exact T+3 boundary.
    pub timed_out_he_explosions: Vec<HeExplosionEvidenceIntentKey>,
    pub native_observations: Vec<NativeObservationSample>,
    pub native_firing_lanes: Vec<NativeFiringLaneSample>,
    pub failed_native_observations: Vec<FailedNativeObservation>,
    pub timed_out_native_observations: Vec<TimedOutNativeObservation>,
    pub player_environment: Vec<NativePlayerEnvironmentSample>,
    pub destructible_hulls: Vec<NativeDestructibleHullSample>,
    /// Progress cursors and native-geometry terminal proposals. A terminal
    /// proposal is not a combat or damage verdict.
    pub projectile_decisions: Vec<ProjectileFlightDecision>,
}

impl AuthorityTickOutput {
    fn from_parts(due: AuthorityDueOutput, step: AuthorityStepOutput) -> Self {
        debug_assert_eq!(due.tick, step.tick);
        Self {
            tick: step.tick,
            bots: step.bots,
            oracle_requests: step.oracle_requests,
            failed_oracle_intents: due.failed_oracle_intents,
            timed_out_bot_intents: due.timed_out_bot_intents,
            player_muzzles: due.player_muzzles,
            failed_player_muzzles: due.failed_player_muzzles,
            ram_contact_evidence: due.ram_contact_evidence,
            unavailable_ram_contacts: due.unavailable_ram_contacts,
            timed_out_ram_contacts: due.timed_out_ram_contacts,
            he_explosion_evidence: due.he_explosion_evidence,
            unavailable_he_explosions: due.unavailable_he_explosions,
            timed_out_he_explosions: due.timed_out_he_explosions,
            native_observations: due.native_observations,
            native_firing_lanes: due.native_firing_lanes,
            failed_native_observations: due.failed_native_observations,
            timed_out_native_observations: due.timed_out_native_observations,
            player_environment: due.player_environment,
            destructible_hulls: due.destructible_hulls,
            projectile_decisions: due.projectile_decisions,
        }
    }

    pub fn projectile_progress(&self) -> impl Iterator<Item = &ProjectileCursor> {
        self.projectile_decisions
            .iter()
            .filter_map(|decision| match decision {
                ProjectileFlightDecision::Progress { cursor, .. } => Some(cursor),
                _ => None,
            })
    }

    pub fn projectile_terminals(&self) -> impl Iterator<Item = &ProjectileTerminalProposal> {
        self.projectile_decisions
            .iter()
            .filter_map(|decision| match decision {
                ProjectileFlightDecision::Terminal(proposal) => Some(proposal),
                _ => None,
            })
    }
}

#[derive(Debug, Error)]
pub enum AuthorityRuntimeError {
    #[error(
        "authority runtime lineage must have non-zero round, authority, and oracle generations"
    )]
    InvalidLineage,
    #[error("bot {bot_id} does not belong to runtime round {round_id}")]
    BotRoundMismatch { bot_id: u32, round_id: u64 },
    #[error("bot {bot_id} starts at tick {received}, not runtime tick {expected}")]
    BotTickMismatch {
        bot_id: u32,
        expected: Tick,
        received: Tick,
    },
    #[error("bot id {bot_id} is duplicated")]
    DuplicateBot { bot_id: u32 },
    #[error("ram correction contains bot {bot_id} more than once")]
    DuplicateBotRamDelta { bot_id: u32 },
    #[error("native donation lineage {received:?} does not match runtime lineage {active:?}")]
    DonationLineageMismatch {
        active: OracleLineage,
        received: OracleLineage,
    },
    #[error("native donation is missing bot {bot_id}")]
    MissingDonatedBot { bot_id: u32 },
    #[error("native donation contains unknown bot {bot_id}")]
    UnknownDonatedBot { bot_id: u32 },
    #[error("native donation contains invalid logical or native entity {entity:?}")]
    InvalidDonationEntity { entity: SimulationEntity },
    #[error("native donation contains invalid oracle-space entity {native:?}")]
    InvalidOracleSpace { native: EntityRef },
    #[error("native entity {native:?} is donated to both {first:?} and {second:?}")]
    DuplicateNativeEntity {
        native: EntityRef,
        first: SimulationEntity,
        second: SimulationEntity,
    },
    #[error("native entity donation conflicts with the active immutable donation")]
    ConflictingDonation,
    #[error("native entity donation is required before advancing authority state")]
    MissingDonation,
    #[error("authority tick {received} does not follow {current}")]
    TickSequence { current: Tick, received: Tick },
    #[error("authority tick {tick} was released but has not been consumed")]
    ReleasedTickPending { tick: Tick },
    #[error("released tick permit lineage {received:?} does not match active lineage {active:?}")]
    ReleasedTickLineageMismatch {
        active: OracleLineage,
        received: OracleLineage,
    },
    #[error(
        "released tick permit {permit_tick} does not match runtime tick {current_tick}, active release {released_tick:?}, and input tick {input_tick}"
    )]
    ReleasedTickMismatch {
        current_tick: Tick,
        released_tick: Option<Tick>,
        permit_tick: Tick,
        input_tick: Tick,
    },
    #[error("authority tick counter is exhausted")]
    TickCounterExhausted,
    #[error("order set is missing bot {bot_id}")]
    MissingBotOrder { bot_id: u32 },
    #[error("order set contains unknown bot {bot_id}")]
    UnknownBotOrder { bot_id: u32 },
    #[error("bot {bot_id} order is invalid")]
    InvalidBotOrder { bot_id: u32 },
    #[error("bot {bot_id} references undonated target {target:?}")]
    UndonatedOrderTarget {
        bot_id: u32,
        target: SimulationEntity,
    },
    #[error("human traffic body {player_id} is invalid or undonated")]
    InvalidHumanTraffic { player_id: u32 },
    #[error("world-pose hull set contains unknown bot {bot_id}")]
    InvalidWorldPoseBot { bot_id: u32 },
    #[error("projectile target {vehicle:?} is invalid, duplicated, or undonated")]
    InvalidProjectileTarget { vehicle: VehicleKey },
    #[error("bot {bot_id} emitted no pose or more than one pose")]
    InvalidBotOutput { bot_id: u32 },
    #[error("canonical combat sync for bot {bot_id} is invalid")]
    InvalidCombatSync { bot_id: u32 },
    #[error("canonical combat sync attempted to resurrect bot {bot_id}")]
    BotResurrection { bot_id: u32 },
    #[error("canonical combat sync attempted to heal bot {bot_id}")]
    BotHealing { bot_id: u32 },
    #[error("bot {bot_id} is not registered")]
    UnknownBot { bot_id: u32 },
    #[error("world revision regressed from {current} to {received}")]
    WorldRevisionRegression {
        current: WorldRevision,
        received: WorldRevision,
    },
    #[error("planner input field {field} is missing or invalid")]
    InvalidPlannerInput { field: &'static str },
    #[error("planner payload field {field} is missing or invalid for bot {bot_id}")]
    InvalidPlannerOutput { bot_id: u32, field: &'static str },
    #[error("planner returned duplicate bot {bot_id}")]
    DuplicatePlannerOrder { bot_id: u32 },
    #[error("planner returned unknown bot {bot_id}")]
    UnknownPlannerOrder { bot_id: u32 },
    #[error("planner returned no order for live bot {bot_id}")]
    MissingPlannerOrder { bot_id: u32 },
    #[error("planner returned unknown combat mode {mode} for bot {bot_id}")]
    UnknownPlannerCombatMode { bot_id: u32, mode: String },
    #[error("planner returned unknown target {target:?} for bot {bot_id}")]
    UnknownPlannerTarget {
        bot_id: u32,
        target: SimulationEntity,
    },
    #[error("navigation graph conflicts with the active immutable graph")]
    ConflictingNavigationGraph,
    #[error("navigation graph is required before installing bot map envelopes")]
    MissingNavigationGraph,
    #[error("navigation envelope is missing the donated ramming shape for bot {bot_id}")]
    MissingBotMapEnvelope { bot_id: u32 },
    #[error("player muzzle binding is outside the admitted fire-intent bounds")]
    InvalidPlayerMuzzleBinding,
    #[error("player muzzle query references undonated human {player_id}")]
    UndonatedPlayerMuzzle { player_id: u64 },
    #[error("player muzzle query was issued at tick {issued_tick}, but authority is at {current}")]
    PlayerMuzzleTickMismatch { current: Tick, issued_tick: Tick },
    #[error("player {player_id} already has a pending muzzle query")]
    PendingPlayerMuzzle { player_id: u64 },
    #[error("player {player_id} intent {intent_seq} was retried with different content or tick")]
    ConflictingPlayerMuzzleRetry { player_id: u64, intent_seq: u64 },
    #[error("player {player_id} muzzle query generation is exhausted")]
    PlayerMuzzleGenerationExhausted { player_id: u64 },
    #[error("player muzzle route is missing or inconsistent for {key:?}")]
    MissingPlayerMuzzlePlan { key: PlayerMuzzleIntentKey },
    #[error("player muzzle reply is unavailable or invalid for batch {key:?}")]
    InvalidPlayerMuzzleReply { key: OracleV1BatchKey },
    #[error("ram contact armour intent is invalid")]
    InvalidRamContactArmorIntent,
    #[error("ram contact armour intent references undonated vehicle {vehicle:?}")]
    UndonatedRamContactVehicle { vehicle: VehicleKey },
    #[error(
        "ram contact armour query was issued at tick {issued_tick}, but authority is at {current}"
    )]
    RamContactArmorTickMismatch { current: Tick, issued_tick: Tick },
    #[error("ram contact armour retry conflicts for {key:?}")]
    ConflictingRamContactArmorRetry { key: RamContactArmorIntentKey },
    #[error("ram contact armour cursor regressed or repeated for pair {pair:?}")]
    StaleRamContactArmorCursor { pair: RamPair },
    #[error("ram contact armour query generation is exhausted for {key}")]
    RamContactArmorGenerationExhausted { key: String },
    #[error("ram contact armour route is missing or inconsistent for {key:?}")]
    MissingRamContactArmorPlan { key: RamContactArmorIntentKey },
    #[error("ram contact armour reply is invalid for batch {key:?}")]
    InvalidRamContactArmorReply { key: OracleV1BatchKey },
    #[error("HE explosion evidence intent is invalid")]
    InvalidHeExplosionEvidenceIntent,
    #[error("HE explosion evidence target {vehicle:?} is undonated")]
    UndonatedHeExplosionTarget { vehicle: VehicleKey },
    #[error(
        "HE explosion evidence query was issued at tick {issued_tick}, but authority is at {current}"
    )]
    HeExplosionEvidenceTickMismatch { current: Tick, issued_tick: Tick },
    #[error("projectile {projectile_id} already has a pending HE evidence query")]
    PendingHeExplosionEvidence { projectile_id: String },
    #[error("HE explosion evidence retry conflicts for {key:?}")]
    ConflictingHeExplosionEvidenceRetry { key: HeExplosionEvidenceIntentKey },
    #[error("HE explosion terminal is unknown or stale for {plan_id:?}")]
    UnknownHeExplosionTerminal { plan_id: ProjectilePlanId },
    #[error("HE explosion intent does not match terminal {plan_id:?}")]
    HeExplosionTerminalMismatch { plan_id: ProjectilePlanId },
    #[error("HE explosion query generation is exhausted for {key}")]
    HeExplosionEvidenceGenerationExhausted { key: String },
    #[error("HE explosion evidence route is missing or inconsistent for {key:?}")]
    MissingHeExplosionEvidencePlan { key: HeExplosionEvidenceIntentKey },
    #[error("HE explosion evidence reply is invalid for batch {key:?}")]
    InvalidHeExplosionEvidenceReply { key: OracleV1BatchKey },
    #[error("native observation plan is missing for {id:?}")]
    MissingNativeObservationPlan { id: NativeObservationQueryId },
    #[error("native observation intent {id:?} was registered twice")]
    DuplicateNativeObservationPlan { id: NativeObservationQueryId },
    #[error("native observation batch plan is missing for {key:?}")]
    MissingNativeObservationBatch { key: OracleV1BatchKey },
    #[error("native observation batch plan was registered twice for {key:?}")]
    DuplicateNativeObservationBatch { key: OracleV1BatchKey },
    #[error("native observation batch {key:?} returned unexpected query {query_id}")]
    UnexpectedNativeObservationResult {
        key: OracleV1BatchKey,
        query_id: u64,
    },
    #[error("native observation query generation is exhausted for {key}")]
    NativeObservationGenerationExhausted { key: String },
    #[error("player environment batch plan is missing for {key:?}")]
    MissingPlayerEnvironmentBatch { key: OracleV1BatchKey },
    #[error("player environment batch plan was registered twice for {key:?}")]
    DuplicatePlayerEnvironmentBatch { key: OracleV1BatchKey },
    #[error("player environment batch {key:?} returned unexpected query {query_id}")]
    UnexpectedPlayerEnvironmentResult {
        key: OracleV1BatchKey,
        query_id: u64,
    },
    #[error("player environment query generation is exhausted for {key}")]
    PlayerEnvironmentGenerationExhausted { key: String },
    #[error("destructible hull batch plan is missing for {key:?}")]
    MissingDestructibleHullBatch { key: OracleV1BatchKey },
    #[error("destructible hull batch plan was registered twice for {key:?}")]
    DuplicateDestructibleHullBatch { key: OracleV1BatchKey },
    #[error("destructible hull batch {key:?} returned unexpected query {query_id}")]
    UnexpectedDestructibleHullResult {
        key: OracleV1BatchKey,
        query_id: u64,
    },
    #[error("destructible hull query generation is exhausted for {key}")]
    DestructibleHullGenerationExhausted { key: String },
    #[error("spotting fire marker references an invalid vehicle {vehicle:?}")]
    InvalidSpottingFireVehicle { vehicle: VehicleKey },
    #[error("spotting observer set contains an invalid or undonated vehicle {vehicle:?}")]
    InvalidSpottingObserver { vehicle: VehicleKey },
    #[error("spotting observer set conflicts with the already installed actor contract")]
    ConflictingSpottingObservers,
    #[error("spotting fire marker tick {fired_tick} is ahead of authority tick {current_tick}")]
    FutureSpottingFireTick {
        current_tick: Tick,
        fired_tick: Tick,
    },
    #[error("oracle route is missing for batch {key:?}")]
    MissingOracleRoute { key: OracleV1BatchKey },
    #[error("oracle batch {key:?} unexpectedly superseded {count} pending batches")]
    UnexpectedBatchSupersession { key: OracleV1BatchKey, count: usize },
    #[error("oracle batch sequence counter is exhausted")]
    BatchSequenceExhausted,
    #[error("bot {bot_id} simulation failed: {source}")]
    BotSimulation {
        bot_id: u32,
        #[source]
        source: SimError,
    },
    #[error(transparent)]
    Oracle(#[from] OracleBrokerError),
    #[error(transparent)]
    Adapter(#[from] OracleAdapterError),
    #[error(transparent)]
    Projectile(#[from] ProjectileFlightError),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PendingRoute {
    BotIntents,
    Projectile(ProjectilePlanId),
    PlayerMuzzle(PlayerMuzzleIntentKey),
    RamContactArmor,
    HeExplosionEvidence(HeExplosionEvidenceIntentKey),
    NativeObservations,
    PlayerEnvironment,
    DestructibleHulls,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PlayerMuzzleState {
    Pending,
    Applied,
    Unavailable,
    TimedOut,
}

#[derive(Clone, Debug)]
struct PlayerMuzzleRecord {
    binding: FireIntentBinding,
    entity: EntityRef,
    request: OracleV1BatchRequest,
    state: PlayerMuzzleState,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RamContactArmorState {
    Pending,
    Applied,
    TimedOut,
}

#[derive(Clone, Debug)]
struct RamContactArmorRecord {
    intent: RamContactArmorIntent,
    first_entity: EntityRef,
    second_entity: EntityRef,
    query_id: u64,
    request: OracleV1BatchRequest,
    state: RamContactArmorState,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HeExplosionEvidenceState {
    Pending,
    Applied,
    Unavailable,
    TimedOut,
}

#[derive(Clone, Debug)]
struct HeExplosionTerminalBinding {
    projectile_id: String,
    applied_tick: Tick,
    impact: ProjectileVec3,
    incoming_direction: ProjectileVec3,
    caliber_mm: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct HeProjectileSourceBinding {
    caliber_mm: f64,
    gravity: f64,
    segment_velocity: ProjectileVec3,
    segment_start_time_ms: u64,
}

#[derive(Clone, Copy, Debug)]
struct HeExplosionEvidenceQueryPlan {
    vehicle: VehicleKey,
    entity: EntityRef,
    query: ExplosionEvidenceQuery,
}

#[derive(Clone, Debug)]
struct HeExplosionEvidenceRecord {
    intent: HeExplosionEvidenceIntent,
    request: OracleV1BatchRequest,
    queries: BTreeMap<u64, HeExplosionEvidenceQueryPlan>,
    state: HeExplosionEvidenceState,
}

#[derive(Clone, Debug)]
struct NativeObservationCandidate {
    observer: VehicleKey,
    target: VehicleKey,
    source_position: Vec3,
    target_position: Vec3,
}

#[derive(Clone, Debug, PartialEq)]
struct NativeObservationQuery {
    id: NativeObservationQueryId,
    target: VehicleKey,
    source_position: Vec3,
    target_position: Vec3,
}

#[derive(Clone, Debug)]
struct NativeObservationQueryPlan {
    intent: NativeObservationIntent,
    evidence: NativeObservationEvidenceKind,
}

#[derive(Clone, Debug)]
struct NativeObservationBatchPlan {
    queries: BTreeMap<u64, NativeObservationQueryPlan>,
}

#[derive(Clone, Copy, Debug)]
struct PlayerEnvironmentQueryPlan {
    player_id: u64,
    pose: BodyPose,
}

#[derive(Clone, Debug)]
struct PlayerEnvironmentBatchPlan {
    players: Vec<PlayerEnvironmentQueryPlan>,
    ground_query_id: u64,
    water_query_id: u64,
}

#[derive(Clone, Debug)]
struct DestructibleHullQueryPlan {
    vehicle: VehicleKey,
    position: Vec3,
    yaw: f64,
    frame_travel: f64,
    kinetic_speed: f64,
}

#[derive(Clone, Debug)]
struct DestructibleHullBatchPlan {
    queries: BTreeMap<u64, DestructibleHullQueryPlan>,
}

enum DueBatch {
    Applied(AppliedOracleBatch),
    TimedOut(TimedOutOracleBatch),
}

#[derive(Clone, Debug)]
struct PlannerVehicleState {
    team: u8,
    alive: bool,
    health: u32,
    position: Vec3,
    velocity: Vec3,
    yaw: f64,
    speed: f64,
}

#[derive(Clone, Debug)]
struct PlannerWorld {
    bots: BTreeMap<u32, PlannerVehicleState>,
    humans: BTreeMap<u32, PlannerVehicleState>,
}

#[derive(Clone, Debug)]
struct PlannerDriveState {
    last_position: Vec3,
    last_yaw: Option<f64>,
    last_now: f64,
    stuck_seconds: f64,
    recovery_until: f64,
    recovery_count: u64,
    phase: f64,
}

impl DueBatch {
    fn key(&self) -> OracleV1BatchKey {
        match self {
            Self::Applied(batch) => batch.request.key(),
            Self::TimedOut(batch) => batch.request.key(),
        }
    }
}

/// One battle round's deterministic Rust authority.
///
/// The type is intentionally single-threaded (`&mut self` at every mutation
/// boundary). Socket tasks may parse replies elsewhere, but must enqueue them
/// back onto the battle loop before calling [`Self::advance_tick`].
pub struct AuthorityRuntime {
    lineage: OracleLineage,
    world_revision: WorldRevision,
    bots: BTreeMap<u32, BotSimulator>,
    planner: BotPlanner,
    strategic_payload: Option<Value>,
    planner_drivers: BTreeMap<u32, PlannerDriveState>,
    navigation: Option<NavRouter>,
    adapter: OracleAdapter,
    broker: OracleBroker,
    released_tick: Option<Tick>,
    projectiles: ProjectileFlightIntegrator,
    destructible_native_space_id: Option<i64>,
    donation: Option<NativeEntityDonation>,
    entities: OracleEntityMap,
    next_batch_seq: BatchSequence,
    routes: BTreeMap<OracleV1BatchKey, PendingRoute>,
    player_muzzle_records: BTreeMap<PlayerMuzzleIntentKey, PlayerMuzzleRecord>,
    pending_player_muzzles: BTreeMap<u64, PlayerMuzzleIntentKey>,
    player_muzzle_terminal_history: BTreeMap<u64, VecDeque<u64>>,
    player_muzzle_query_generations: BTreeMap<String, u64>,
    ram_contact_records: BTreeMap<RamContactArmorIntentKey, RamContactArmorRecord>,
    pending_ram_contacts: BTreeSet<RamContactArmorIntentKey>,
    ram_contact_batches: BTreeMap<OracleV1BatchKey, Vec<RamContactArmorIntentKey>>,
    ram_contact_terminal_history: BTreeMap<RamPair, VecDeque<RamSourceCursor>>,
    ram_contact_latest: BTreeMap<RamPair, (RamSourceCursor, u64)>,
    ram_contact_query_generations: BTreeMap<String, u64>,
    he_projectile_sources: BTreeMap<String, HeProjectileSourceBinding>,
    he_explosion_terminal_bindings: BTreeMap<ProjectilePlanId, HeExplosionTerminalBinding>,
    he_explosion_terminal_binding_order: VecDeque<ProjectilePlanId>,
    he_explosion_records: BTreeMap<HeExplosionEvidenceIntentKey, HeExplosionEvidenceRecord>,
    pending_he_explosion_projectiles: BTreeMap<String, HeExplosionEvidenceIntentKey>,
    he_explosion_terminal_history: VecDeque<HeExplosionEvidenceIntentKey>,
    he_explosion_latest_plans: BTreeMap<String, ProjectilePlanId>,
    he_explosion_query_generations: BTreeMap<String, u64>,
    pending_native_observations: BTreeMap<OracleV1BatchKey, NativeObservationBatchPlan>,
    native_observation_query_generations: BTreeMap<String, u64>,
    pending_player_environment: BTreeMap<OracleV1BatchKey, PlayerEnvironmentBatchPlan>,
    player_environment_query_generations: BTreeMap<String, u64>,
    pending_destructible_hulls: BTreeMap<OracleV1BatchKey, DestructibleHullBatchPlan>,
    destructible_hull_query_generations: BTreeMap<String, u64>,
    last_spotting_fire_us: BTreeMap<VehicleKey, u64>,
    spotting_observers: BTreeSet<VehicleKey>,
    spotting_observers_installed: bool,
    observation_target_cursors: BTreeMap<VehicleKey, usize>,
    observation_observer_cursor: usize,
}

impl AuthorityRuntime {
    pub fn new(
        lineage: OracleLineage,
        current_tick: Tick,
        world_revision: WorldRevision,
        bots: Vec<BotSimulator>,
    ) -> Result<Self, AuthorityRuntimeError> {
        if lineage.round_id == 0 || lineage.authority_epoch == 0 || lineage.oracle_generation == 0 {
            return Err(AuthorityRuntimeError::InvalidLineage);
        }
        let mut indexed = BTreeMap::new();
        for bot in bots {
            let state = bot.state();
            if state.round_id != lineage.round_id {
                return Err(AuthorityRuntimeError::BotRoundMismatch {
                    bot_id: state.id,
                    round_id: lineage.round_id,
                });
            }
            if state.tick != current_tick {
                return Err(AuthorityRuntimeError::BotTickMismatch {
                    bot_id: state.id,
                    expected: current_tick,
                    received: state.tick,
                });
            }
            let bot_id = state.id;
            if indexed.insert(bot_id, bot).is_some() {
                return Err(AuthorityRuntimeError::DuplicateBot { bot_id });
            }
        }
        let spotting_observers = indexed
            .keys()
            .map(|bot_id| VehicleKey {
                kind: VehicleKind::Bot,
                id: u64::from(*bot_id),
            })
            .collect();
        Ok(Self {
            lineage,
            world_revision,
            bots: indexed,
            planner: BotPlanner::new(),
            strategic_payload: None,
            planner_drivers: BTreeMap::new(),
            navigation: None,
            adapter: OracleAdapter::new(lineage, world_revision)?,
            broker: OracleBroker::new(lineage, current_tick)?,
            released_tick: None,
            projectiles: ProjectileFlightIntegrator::new(lineage, current_tick)?,
            destructible_native_space_id: None,
            donation: None,
            entities: OracleEntityMap::default(),
            next_batch_seq: 1,
            routes: BTreeMap::new(),
            player_muzzle_records: BTreeMap::new(),
            pending_player_muzzles: BTreeMap::new(),
            player_muzzle_terminal_history: BTreeMap::new(),
            player_muzzle_query_generations: BTreeMap::new(),
            ram_contact_records: BTreeMap::new(),
            pending_ram_contacts: BTreeSet::new(),
            ram_contact_batches: BTreeMap::new(),
            ram_contact_terminal_history: BTreeMap::new(),
            ram_contact_latest: BTreeMap::new(),
            ram_contact_query_generations: BTreeMap::new(),
            he_projectile_sources: BTreeMap::new(),
            he_explosion_terminal_bindings: BTreeMap::new(),
            he_explosion_terminal_binding_order: VecDeque::new(),
            he_explosion_records: BTreeMap::new(),
            pending_he_explosion_projectiles: BTreeMap::new(),
            he_explosion_terminal_history: VecDeque::new(),
            he_explosion_latest_plans: BTreeMap::new(),
            he_explosion_query_generations: BTreeMap::new(),
            pending_native_observations: BTreeMap::new(),
            native_observation_query_generations: BTreeMap::new(),
            pending_player_environment: BTreeMap::new(),
            player_environment_query_generations: BTreeMap::new(),
            pending_destructible_hulls: BTreeMap::new(),
            destructible_hull_query_generations: BTreeMap::new(),
            last_spotting_fire_us: BTreeMap::new(),
            spotting_observers,
            spotting_observers_installed: false,
            observation_target_cursors: BTreeMap::new(),
            observation_observer_cursor: 0,
        })
    }

    pub fn lineage(&self) -> OracleLineage {
        self.lineage
    }

    pub fn current_tick(&self) -> Tick {
        self.broker.current_tick()
    }

    pub fn world_revision(&self) -> WorldRevision {
        self.world_revision
    }

    pub fn next_batch_sequence(&self) -> BatchSequence {
        self.next_batch_seq
    }

    pub fn pending_oracle_batches(&self) -> usize {
        self.broker.pending_batches()
    }

    pub fn active_projectiles(&self) -> usize {
        self.projectiles.active_len()
    }

    /// Fence read-only destructible evidence to the exact native space
    /// installed for this oracle generation.
    pub fn install_destructible_native_space_id(
        &mut self,
        native_space_id: i64,
    ) -> Result<bool, AuthorityRuntimeError> {
        let installed = self
            .projectiles
            .install_destructible_native_space_id(native_space_id)?;
        self.destructible_native_space_id = Some(native_space_id);
        Ok(installed)
    }

    pub fn bot_ids(&self) -> impl Iterator<Item = u32> + '_ {
        self.bots.keys().copied()
    }

    pub fn bot_state(&self, bot_id: u32) -> Option<&BotState> {
        self.bots.get(&bot_id).map(BotSimulator::state)
    }

    pub fn bot_ram_velocity(&self, bot_id: u32) -> Option<Vec3> {
        self.bots.get(&bot_id).map(BotSimulator::ram_velocity)
    }

    /// Stage a complete set of server-owned ramming corrections without
    /// mutating the live simulation.
    pub fn prepare_bot_ram_mutation(
        &self,
        deltas: &[BotRamDelta],
    ) -> Result<PreparedBotRamMutation, AuthorityRuntimeError> {
        let mut bots = BTreeMap::new();
        let mut corrections = BTreeMap::new();
        for delta in deltas {
            if bots.contains_key(&delta.bot_id) {
                return Err(AuthorityRuntimeError::DuplicateBotRamDelta {
                    bot_id: delta.bot_id,
                });
            }
            let mut bot =
                self.bots
                    .get(&delta.bot_id)
                    .cloned()
                    .ok_or(AuthorityRuntimeError::UnknownBot {
                        bot_id: delta.bot_id,
                    })?;
            let before = bot.state().position;
            bot.apply_ram_delta(
                delta.correction_x,
                delta.correction_z,
                delta.velocity_x,
                delta.velocity_z,
            )
            .map_err(|source| AuthorityRuntimeError::BotSimulation {
                bot_id: delta.bot_id,
                source,
            })?;
            let after = bot.state().position;
            bots.insert(delta.bot_id, bot);
            corrections.insert(
                delta.bot_id,
                Vec3::new(after.x - before.x, 0.0, after.z - before.z),
            );
        }
        Ok(PreparedBotRamMutation {
            lineage: self.lineage,
            tick: self.current_tick(),
            bots,
            corrections,
        })
    }

    /// Commit a mutation prepared from this exact runtime boundary.
    ///
    /// The battle loop keeps this immediately adjacent to the successful
    /// battle-ledger commit, so these assertions protect an internal invariant
    /// rather than exposing a recoverable half-commit path.
    pub fn commit_bot_ram_mutation(&mut self, mutation: PreparedBotRamMutation) {
        assert_eq!(mutation.lineage, self.lineage);
        assert_eq!(mutation.tick, self.current_tick());
        for (bot_id, bot) in mutation.bots {
            let previous = self.bots.insert(bot_id, bot);
            assert!(previous.is_some());
            if let (Some(driver), Some(correction)) = (
                self.planner_drivers.get_mut(&bot_id),
                mutation.corrections.get(&bot_id),
            ) {
                driver.last_position.x += correction.x;
                driver.last_position.z += correction.z;
            }
        }
    }

    /// Record a canonical vehicle shot for the 0.75-second camouflage branch.
    /// Bot launches call this internally; the battle loop must call it for an
    /// admitted human launch before the next observation is scheduled.
    pub fn note_spotting_fire(
        &mut self,
        vehicle: VehicleKey,
        fired_tick: Tick,
    ) -> Result<bool, AuthorityRuntimeError> {
        let entity = vehicle_entity(vehicle)
            .ok_or(AuthorityRuntimeError::InvalidSpottingFireVehicle { vehicle })?;
        if self.donation.is_some() && self.entities.get(entity).is_none() {
            return Err(AuthorityRuntimeError::InvalidSpottingFireVehicle { vehicle });
        }
        let current_tick = self.current_tick();
        if fired_tick > current_tick {
            return Err(AuthorityRuntimeError::FutureSpottingFireTick {
                current_tick,
                fired_tick,
            });
        }
        let fired_at_us = time_us_at_tick(fired_tick);
        let previous = self.last_spotting_fire_us.get(&vehicle).copied();
        if previous.is_some_and(|previous| previous >= fired_at_us) {
            return Ok(false);
        }
        self.last_spotting_fire_us.insert(vehicle, fired_at_us);
        Ok(true)
    }

    /// Install the exact actor-scoped spotting contract once donation is
    /// fenced. Exact replay is idempotent; a different replay cannot silently
    /// add a player observer or retain an actor whose loadout was omitted.
    pub fn install_spotting_observers(
        &mut self,
        observers: BTreeSet<VehicleKey>,
    ) -> Result<bool, AuthorityRuntimeError> {
        if self.spotting_observers_installed {
            return if self.spotting_observers == observers {
                Ok(false)
            } else {
                Err(AuthorityRuntimeError::ConflictingSpottingObservers)
            };
        }
        if self.donation.is_none() {
            return Err(AuthorityRuntimeError::MissingDonation);
        }
        for &vehicle in &observers {
            let Some(entity) = vehicle_entity(vehicle) else {
                return Err(AuthorityRuntimeError::InvalidSpottingObserver { vehicle });
            };
            if self.entities.get(entity).is_none()
                || (vehicle.kind == VehicleKind::Bot
                    && !self
                        .bots
                        .contains_key(&u32::try_from(vehicle.id).unwrap_or(0)))
            {
                return Err(AuthorityRuntimeError::InvalidSpottingObserver { vehicle });
            }
        }
        self.spotting_observers = observers;
        self.spotting_observers_installed = true;
        self.observation_target_cursors.clear();
        self.observation_observer_cursor = 0;
        Ok(true)
    }

    pub fn planner(&self) -> &BotPlanner {
        &self.planner
    }

    pub fn planner_mut(&mut self) -> &mut BotPlanner {
        self.strategic_payload = None;
        &mut self.planner
    }

    /// Install the selected map's immutable local-navigation graph exactly
    /// once. The tactical route index remains the sparse wire index; internal
    /// graph cells never cross the protocol boundary.
    pub fn install_navigation_graph(
        &mut self,
        graph: NavGraph,
    ) -> Result<bool, AuthorityRuntimeError> {
        if let Some(active) = &self.navigation {
            return if active.graph() == &graph {
                Ok(false)
            } else {
                Err(AuthorityRuntimeError::ConflictingNavigationGraph)
            };
        }
        self.navigation = Some(graph.router());
        Ok(true)
    }

    pub fn navigation_graph(&self) -> Option<&NavGraph> {
        self.navigation.as_ref().map(NavRouter::graph)
    }

    /// Install full-chassis map-edge containment from the immutable graph and
    /// the exact actor-scoped ramming shapes donated for this round.
    pub fn install_bot_map_envelopes(
        &mut self,
        shapes: &BTreeMap<VehicleKey, RamShape>,
    ) -> Result<bool, AuthorityRuntimeError> {
        let bounds = self
            .navigation_graph()
            .ok_or(AuthorityRuntimeError::MissingNavigationGraph)?
            .bounds();
        let mut bots = self.bots.clone();
        let mut changed = false;
        for (&bot_id, bot) in &mut bots {
            let shape = shapes
                .get(&VehicleKey {
                    kind: VehicleKind::Bot,
                    id: u64::from(bot_id),
                })
                .ok_or(AuthorityRuntimeError::MissingBotMapEnvelope { bot_id })?;
            changed |= bot
                .install_map_envelope(bounds, shape.half_width, shape.half_length)
                .map_err(|source| AuthorityRuntimeError::BotSimulation { bot_id, source })?;
        }
        self.bots = bots;
        Ok(changed)
    }

    /// Build one complete typed order set from the canonical public battle
    /// projection. Planner and local-driver state commit only after the full
    /// JSON payload has passed strict parsing.
    pub fn build_planner_orders(
        &mut self,
        input: PlannerBuildInput<'_>,
    ) -> Result<TypedPlannerOrders, AuthorityRuntimeError> {
        self.build_planner_orders_at_cadence(input, true)
    }

    /// Refresh the full-roster tactical synthesis when requested, then
    /// realise its cached strategic payload against the current 30 Hz world
    /// pose. Bot death and target death are contained locally between the
    /// one-second strategy boundaries.
    pub fn build_planner_orders_at_cadence(
        &mut self,
        input: PlannerBuildInput<'_>,
        refresh_strategy: bool,
    ) -> Result<TypedPlannerOrders, AuthorityRuntimeError> {
        if !input.now.is_finite() || input.now < 0.0 {
            return Err(AuthorityRuntimeError::InvalidPlannerInput { field: "now" });
        }
        let world = validate_planner_inputs(&self.bots, &input)?;
        let refresh_strategy = refresh_strategy || self.strategic_payload.is_none();
        let mut staged_planner = None;
        let mut staged_payload = None;
        if refresh_strategy {
            let mut planner = self.planner.clone();
            if let Some(contacts) = input.contacts {
                validate_contacts(contacts, &world, &self.bots)?;
                let known_targets = BotPlanner::known_targets(input.bot_states, input.players);
                planner.report_contacts(contacts, &known_targets, input.now);
            }
            if let Some(defense) = input.defense {
                validate_defense(defense)?;
            }
            staged_payload = Some(planner.build_orders(
                input.manifest,
                input.bot_states,
                input.players,
                input.now,
                input.defense,
            ));
            staged_planner = Some(planner);
        }
        let raw = staged_payload
            .as_ref()
            .or(self.strategic_payload.as_ref())
            .expect("a missing strategic payload forces a refresh");
        let mut drivers = self.planner_drivers.clone();
        let mut navigation = self.navigation.clone();
        let typed = parse_planner_payload(
            &raw,
            &self.bots,
            &world,
            input.now,
            &mut drivers,
            navigation.as_mut(),
        )?;
        if let Some(planner) = staged_planner {
            self.planner = planner;
            self.strategic_payload = staged_payload;
        }
        self.planner_drivers = drivers;
        self.navigation = navigation;
        Ok(typed)
    }

    pub fn entity_ref(&self, entity: SimulationEntity) -> Option<EntityRef> {
        self.entities.get(entity)
    }

    /// Installs the native entity map transactionally. Exact replay is
    /// idempotent; any different donation in the same oracle generation is a
    /// fail-closed conflict.
    pub fn donate_native_entities(
        &mut self,
        donation: NativeEntityDonation,
    ) -> Result<bool, AuthorityRuntimeError> {
        if donation.lineage != self.lineage {
            return Err(AuthorityRuntimeError::DonationLineageMismatch {
                active: self.lineage,
                received: donation.lineage,
            });
        }
        if let Some(active) = &self.donation {
            return if active == &donation {
                Ok(false)
            } else {
                Err(AuthorityRuntimeError::ConflictingDonation)
            };
        }
        validate_native_ref(donation.oracle_space).map_err(|_| {
            AuthorityRuntimeError::InvalidOracleSpace {
                native: donation.oracle_space,
            }
        })?;

        for bot_id in self.bots.keys() {
            if !donation.bots.contains_key(bot_id) {
                return Err(AuthorityRuntimeError::MissingDonatedBot { bot_id: *bot_id });
            }
        }
        for bot_id in donation.bots.keys() {
            if !self.bots.contains_key(bot_id) {
                return Err(AuthorityRuntimeError::UnknownDonatedBot { bot_id: *bot_id });
            }
        }

        let mut entities = OracleEntityMap::default();
        let mut native_owners = BTreeMap::<(i64, u64), SimulationEntity>::new();
        for (entity, native) in donation
            .bots
            .iter()
            .map(|(id, native)| (SimulationEntity::Bot(*id), *native))
            .chain(
                donation
                    .humans
                    .iter()
                    .map(|(id, native)| (SimulationEntity::Human(*id), *native)),
            )
        {
            if validate_native_ref(native).is_err()
                || matches!(
                    entity,
                    SimulationEntity::Bot(0) | SimulationEntity::Human(0)
                )
            {
                return Err(AuthorityRuntimeError::InvalidDonationEntity { entity });
            }
            if let Some(first) = native_owners.insert((native.entity_id, native.generation), entity)
            {
                return Err(AuthorityRuntimeError::DuplicateNativeEntity {
                    native,
                    first,
                    second: entity,
                });
            }
            entities.insert(entity, native)?;
        }

        self.entities = entities;
        if !self.spotting_observers_installed {
            self.spotting_observers
                .extend(donation.humans.keys().map(|player_id| VehicleKey {
                    kind: VehicleKind::Player,
                    id: u64::from(*player_id),
                }));
        }
        self.donation = Some(donation);
        Ok(true)
    }

    /// Admit one player fire binding at the native-world boundary and return
    /// the single query which must be sent to the donated client. The query is
    /// registered immediately, so early replies can safely be buffered by the
    /// shared broker before the next simulation tick.
    pub fn schedule_player_muzzle(
        &mut self,
        binding: FireIntentBinding,
        issued_tick: Tick,
    ) -> Result<PlayerMuzzleSchedule, AuthorityRuntimeError> {
        if !valid_player_muzzle_binding(&binding) {
            return Err(AuthorityRuntimeError::InvalidPlayerMuzzleBinding);
        }
        let intent_key = PlayerMuzzleIntentKey {
            player_id: binding.player_id,
            intent_seq: binding.intent_seq,
        };
        if let Some(previous) = self.player_muzzle_records.get(&intent_key) {
            return if previous.binding == binding && previous.request.issued_tick == issued_tick {
                Ok(PlayerMuzzleSchedule::ExactRetry {
                    key: previous.request.key(),
                    apply_tick: previous.request.apply_tick,
                })
            } else {
                Err(AuthorityRuntimeError::ConflictingPlayerMuzzleRetry {
                    player_id: binding.player_id,
                    intent_seq: binding.intent_seq,
                })
            };
        }

        let current = self.current_tick();
        if issued_tick != current {
            return Err(AuthorityRuntimeError::PlayerMuzzleTickMismatch {
                current,
                issued_tick,
            });
        }
        if self.pending_player_muzzles.contains_key(&binding.player_id) {
            return Err(AuthorityRuntimeError::PendingPlayerMuzzle {
                player_id: binding.player_id,
            });
        }
        let player_id = u32::try_from(binding.player_id)
            .map_err(|_| AuthorityRuntimeError::InvalidPlayerMuzzleBinding)?;
        let entity = self
            .entities
            .get(SimulationEntity::Human(player_id))
            .ok_or(AuthorityRuntimeError::UndonatedPlayerMuzzle {
                player_id: binding.player_id,
            })?;
        let apply_tick = issued_tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        let next_batch_seq = self
            .next_batch_seq
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
        let lane = issued_tick % (ORACLE_PIPELINE_TICKS + 1);
        let query_key = format!(
            "player/{}/muzzle/e{}g{}/l{lane}",
            binding.player_id, entity.entity_id, entity.generation
        );
        let query_generation = self
            .player_muzzle_query_generations
            .get(&query_key)
            .copied()
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::PlayerMuzzleGenerationExhausted {
                player_id: binding.player_id,
            })?;
        let request = OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: self.lineage.round_id,
            authority_epoch: self.lineage.authority_epoch,
            oracle_generation: self.lineage.oracle_generation,
            batch_seq: self.next_batch_seq,
            issued_tick,
            apply_tick,
            world_revision: self.world_revision,
            queries: vec![OracleV1Query {
                query_id: 1,
                key: query_key.clone(),
                query_generation,
                entity,
                operation: OracleOperation::PlayerMuzzleEvidence(
                    PlayerMuzzleEvidenceQuery::default(),
                ),
            }],
        };

        let registration = self.broker.register_request(request.clone())?;
        if registration.invalidated_batches != 0 {
            return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                key: registration.key,
                count: registration.invalidated_batches,
            });
        }
        self.adapter.advance_batch_sequence_floor(next_batch_seq)?;
        self.next_batch_seq = next_batch_seq;
        self.player_muzzle_query_generations
            .insert(query_key, query_generation);
        let replaced_route = self
            .routes
            .insert(registration.key, PendingRoute::PlayerMuzzle(intent_key));
        debug_assert!(replaced_route.is_none());
        let replaced_record = self.player_muzzle_records.insert(
            intent_key,
            PlayerMuzzleRecord {
                binding,
                entity,
                request: request.clone(),
                state: PlayerMuzzleState::Pending,
            },
        );
        debug_assert!(replaced_record.is_none());
        let replaced_pending = self
            .pending_player_muzzles
            .insert(intent_key.player_id, intent_key);
        debug_assert!(replaced_pending.is_none());
        Ok(PlayerMuzzleSchedule::New { request })
    }

    /// Register one immutable contact for an atomic native two-sided armour
    /// probe. The request is brokered immediately, so an early native reply is
    /// buffered but cannot be released before the intent's exact T+3 tick.
    pub fn schedule_ram_contact_armor(
        &mut self,
        intent: RamContactArmorIntent,
    ) -> Result<RamContactArmorSchedule, AuthorityRuntimeError> {
        let intent_key = RamContactArmorIntentKey {
            pair: intent.pair,
            cursor: intent.cursor,
        };
        if let Some(previous) = self.ram_contact_records.get(&intent_key) {
            return if previous.intent == intent {
                Ok(RamContactArmorSchedule::ExactRetry {
                    key: previous.request.key(),
                    apply_tick: previous.request.apply_tick,
                })
            } else {
                Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry { key: intent_key })
            };
        }
        let prepared = self.prepare_ram_contact_armor_batch(vec![intent])?;
        let request = prepared
            .requests()
            .first()
            .cloned()
            .ok_or(AuthorityRuntimeError::InvalidRamContactArmorIntent)?;
        self.commit_ram_contact_armor_batch(prepared);
        Ok(RamContactArmorSchedule::New { request })
    }

    /// Stage a bounded set of independent contact queries without mutating
    /// live broker or authority state. Multiple source episodes for one pair
    /// may coexist; the cursor is part of every query and route identity.
    pub fn prepare_ram_contact_armor_batch(
        &self,
        intents: Vec<RamContactArmorIntent>,
    ) -> Result<PreparedRamContactArmorBatch, AuthorityRuntimeError> {
        const QUERIES_PER_BATCH: usize = MAX_ORACLE_PRIMITIVE_OPERATIONS / 2;

        let current = self.current_tick();
        let mut broker = self.broker.clone();
        let mut adapter = self.adapter.clone();
        let mut next_batch_seq = self.next_batch_seq;
        let mut routes = self.routes.clone();
        let mut records = self.ram_contact_records.clone();
        let mut pending = self.pending_ram_contacts.clone();
        let mut batches = self.ram_contact_batches.clone();
        let mut latest = self.ram_contact_latest.clone();
        let mut query_generations = self.ram_contact_query_generations.clone();
        let mut prepared = Vec::with_capacity(intents.len());

        for intent in intents {
            if !valid_ram_contact_armor_intent(&intent) {
                return Err(AuthorityRuntimeError::InvalidRamContactArmorIntent);
            }
            if intent.issued_tick != current {
                return Err(AuthorityRuntimeError::RamContactArmorTickMismatch {
                    current,
                    issued_tick: intent.issued_tick,
                });
            }
            let key = RamContactArmorIntentKey {
                pair: intent.pair,
                cursor: intent.cursor,
            };
            if records.contains_key(&key) {
                return Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry { key });
            }
            if pending.contains(&key) {
                return Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry { key });
            }
            if let Some((latest_cursor, latest_time_us)) = latest.get(&intent.pair) {
                if intent.cursor <= *latest_cursor || intent.source_time_us <= *latest_time_us {
                    return Err(AuthorityRuntimeError::StaleRamContactArmorCursor {
                        pair: intent.pair,
                    });
                }
            }

            let first_logical = vehicle_entity(intent.pair.first).ok_or(
                AuthorityRuntimeError::UndonatedRamContactVehicle {
                    vehicle: intent.pair.first,
                },
            )?;
            let second_logical = vehicle_entity(intent.pair.second).ok_or(
                AuthorityRuntimeError::UndonatedRamContactVehicle {
                    vehicle: intent.pair.second,
                },
            )?;
            let first_entity = self.entities.get(first_logical).ok_or(
                AuthorityRuntimeError::UndonatedRamContactVehicle {
                    vehicle: intent.pair.first,
                },
            )?;
            let second_entity = self.entities.get(second_logical).ok_or(
                AuthorityRuntimeError::UndonatedRamContactVehicle {
                    vehicle: intent.pair.second,
                },
            )?;
            let lane = intent.issued_tick % (ORACLE_PIPELINE_TICKS + 1);
            let query_key = format!(
                "ram/{}{}-{}{}/e{}g{}-e{}g{}/c{}-{}-l{lane}",
                vehicle_kind_key(intent.pair.first.kind),
                intent.pair.first.id,
                vehicle_kind_key(intent.pair.second.kind),
                intent.pair.second.id,
                first_entity.entity_id,
                first_entity.generation,
                second_entity.entity_id,
                second_entity.generation,
                intent.cursor.episode(),
                intent.cursor.frontier(),
            );
            let query_generation = query_generations
                .get(&query_key)
                .copied()
                .unwrap_or(0)
                .checked_add(1)
                .ok_or_else(
                    || AuthorityRuntimeError::RamContactArmorGenerationExhausted {
                        key: query_key.clone(),
                    },
                )?;
            query_generations.insert(query_key.clone(), query_generation);
            latest.insert(intent.pair, (intent.cursor, intent.source_time_us));
            prepared.push((
                key,
                intent,
                first_entity,
                second_entity,
                query_key,
                query_generation,
            ));
        }

        let mut requests = Vec::new();
        for chunk in prepared.chunks(QUERIES_PER_BATCH.max(1)) {
            let issued_tick = chunk[0].1.issued_tick;
            let apply_tick = chunk[0].1.apply_tick;
            if chunk
                .iter()
                .any(|entry| entry.1.issued_tick != issued_tick || entry.1.apply_tick != apply_tick)
            {
                return Err(AuthorityRuntimeError::InvalidRamContactArmorIntent);
            }
            let following_batch_seq = next_batch_seq
                .checked_add(1)
                .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
            let queries = chunk
                .iter()
                .enumerate()
                .map(
                    |(
                        index,
                        (_, intent, first_entity, second_entity, query_key, query_generation),
                    )| OracleV1Query {
                        query_id: index as u64 + 1,
                        key: query_key.clone(),
                        query_generation: *query_generation,
                        entity: *first_entity,
                        operation: OracleOperation::RamContactArmorEvidence(
                            RamContactArmorEvidenceQuery {
                                first: *first_entity,
                                second: *second_entity,
                                first_pose: intent.first_pose,
                                second_pose: intent.second_pose,
                                contact_point: oracle_vec3(intent.contact_point),
                                contact_normal: oracle_vec3(intent.contact_normal),
                            },
                        ),
                    },
                )
                .collect();
            let request = OracleV1BatchRequest {
                protocol_version: ORACLE_PROTOCOL_VERSION,
                round_id: self.lineage.round_id,
                authority_epoch: self.lineage.authority_epoch,
                oracle_generation: self.lineage.oracle_generation,
                batch_seq: next_batch_seq,
                issued_tick,
                apply_tick,
                world_revision: self.world_revision,
                queries,
            };
            let registration = broker.register_request(request.clone())?;
            if registration.invalidated_batches != 0 {
                return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                    key: registration.key,
                    count: registration.invalidated_batches,
                });
            }
            adapter.advance_batch_sequence_floor(following_batch_seq)?;
            next_batch_seq = following_batch_seq;
            if routes
                .insert(registration.key, PendingRoute::RamContactArmor)
                .is_some()
            {
                return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                    key: registration.key,
                    count: 1,
                });
            }
            let mut batch_keys = Vec::with_capacity(chunk.len());
            for (index, (key, intent, first_entity, second_entity, _, _)) in
                chunk.iter().enumerate()
            {
                if !pending.insert(*key) {
                    return Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry {
                        key: *key,
                    });
                }
                if records
                    .insert(
                        *key,
                        RamContactArmorRecord {
                            intent: intent.clone(),
                            first_entity: *first_entity,
                            second_entity: *second_entity,
                            query_id: index as u64 + 1,
                            request: request.clone(),
                            state: RamContactArmorState::Pending,
                        },
                    )
                    .is_some()
                {
                    return Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry {
                        key: *key,
                    });
                }
                batch_keys.push(*key);
            }
            if batches.insert(registration.key, batch_keys).is_some() {
                return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                    key: registration.key,
                    count: 1,
                });
            }
            requests.push(request);
        }

        Ok(PreparedRamContactArmorBatch {
            lineage: self.lineage,
            tick: current,
            broker,
            adapter,
            next_batch_seq,
            routes,
            records,
            pending,
            batches,
            latest,
            query_generations,
            requests,
        })
    }

    pub fn commit_ram_contact_armor_batch(
        &mut self,
        prepared: PreparedRamContactArmorBatch,
    ) -> Vec<OracleV1BatchRequest> {
        assert_eq!(prepared.lineage, self.lineage);
        assert_eq!(prepared.tick, self.current_tick());
        self.broker = prepared.broker;
        self.adapter = prepared.adapter;
        self.next_batch_seq = prepared.next_batch_seq;
        self.routes = prepared.routes;
        self.ram_contact_records = prepared.records;
        self.pending_ram_contacts = prepared.pending;
        self.ram_contact_batches = prepared.batches;
        self.ram_contact_latest = prepared.latest;
        self.ram_contact_query_generations = prepared.query_generations;
        prepared.requests
    }

    /// Register all frozen targets for one terminal HE impact as one atomic
    /// native evidence batch. Early replies remain buffered in the broker and
    /// cannot be released before the exact T+3 apply tick.
    pub fn schedule_he_explosion_evidence(
        &mut self,
        mut intent: HeExplosionEvidenceIntent,
    ) -> Result<HeExplosionEvidenceSchedule, AuthorityRuntimeError> {
        intent
            .targets
            .sort_by_key(|target| vehicle_order_key(target.vehicle));
        if !valid_he_explosion_evidence_intent(&intent) {
            return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceIntent);
        }

        let intent_key = HeExplosionEvidenceIntentKey {
            plan_id: intent.plan_id,
        };
        if let Some(previous) = self.he_explosion_records.get(&intent_key) {
            return if previous.intent == intent {
                Ok(HeExplosionEvidenceSchedule::ExactRetry {
                    key: previous.request.key(),
                    apply_tick: previous.request.apply_tick,
                })
            } else {
                Err(AuthorityRuntimeError::ConflictingHeExplosionEvidenceRetry { key: intent_key })
            };
        }

        let current = self.current_tick();
        if intent.issued_tick != current {
            return Err(AuthorityRuntimeError::HeExplosionEvidenceTickMismatch {
                current,
                issued_tick: intent.issued_tick,
            });
        }
        if self
            .pending_he_explosion_projectiles
            .contains_key(&intent.projectile_id)
        {
            return Err(AuthorityRuntimeError::PendingHeExplosionEvidence {
                projectile_id: intent.projectile_id,
            });
        }
        if self
            .he_explosion_latest_plans
            .get(&intent.projectile_id)
            .is_some_and(|latest| *latest >= intent.plan_id)
        {
            return Err(AuthorityRuntimeError::UnknownHeExplosionTerminal {
                plan_id: intent.plan_id,
            });
        }
        let terminal = self
            .he_explosion_terminal_bindings
            .get(&intent.plan_id)
            .ok_or(AuthorityRuntimeError::UnknownHeExplosionTerminal {
                plan_id: intent.plan_id,
            })?;
        if terminal.projectile_id != intent.projectile_id
            || terminal.applied_tick != intent.issued_tick
            || terminal.impact != intent.impact
            || terminal.incoming_direction != intent.incoming_direction
            || terminal.caliber_mm != intent.caliber_mm
        {
            return Err(AuthorityRuntimeError::HeExplosionTerminalMismatch {
                plan_id: intent.plan_id,
            });
        }

        let next_batch_seq = self
            .next_batch_seq
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
        let lane = intent.issued_tick % (ORACLE_PIPELINE_TICKS + 1);
        let mut queries = Vec::with_capacity(intent.targets.len());
        let mut query_plans = BTreeMap::new();
        let mut query_generations = Vec::with_capacity(intent.targets.len());
        for (index, target) in intent.targets.iter().enumerate() {
            let logical = vehicle_entity(target.vehicle).ok_or(
                AuthorityRuntimeError::UndonatedHeExplosionTarget {
                    vehicle: target.vehicle,
                },
            )?;
            let entity = self.entities.get(logical).ok_or(
                AuthorityRuntimeError::UndonatedHeExplosionTarget {
                    vehicle: target.vehicle,
                },
            )?;
            let query_key = format!(
                "he/{}-{}/{}{}-q{}/e{}g{}/l{lane}",
                intent.plan_id.issued_tick,
                intent.plan_id.projectile_ordinal,
                vehicle_kind_key(target.vehicle.kind),
                target.vehicle.id,
                index + 1,
                entity.entity_id,
                entity.generation,
            );
            let query_generation = self
                .he_explosion_query_generations
                .get(&query_key)
                .copied()
                .unwrap_or(0)
                .checked_add(1)
                .ok_or_else(
                    || AuthorityRuntimeError::HeExplosionEvidenceGenerationExhausted {
                        key: query_key.clone(),
                    },
                )?;
            let query_id = u64::try_from(index + 1)
                .map_err(|_| AuthorityRuntimeError::InvalidHeExplosionEvidenceIntent)?;
            let arguments = ExplosionEvidenceQuery {
                target: entity,
                impact: oracle_projectile_vec3(intent.impact),
                incoming_direction: oracle_projectile_vec3(intent.incoming_direction),
                caliber_mm: intent.caliber_mm,
                target_pose: target.target_pose,
            };
            queries.push(OracleV1Query {
                query_id,
                key: query_key.clone(),
                query_generation,
                entity,
                operation: OracleOperation::ExplosionEvidence(arguments),
            });
            query_plans.insert(
                query_id,
                HeExplosionEvidenceQueryPlan {
                    vehicle: target.vehicle,
                    entity,
                    query: arguments,
                },
            );
            query_generations.push((query_key, query_generation));
        }
        let request = OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: self.lineage.round_id,
            authority_epoch: self.lineage.authority_epoch,
            oracle_generation: self.lineage.oracle_generation,
            batch_seq: self.next_batch_seq,
            issued_tick: intent.issued_tick,
            apply_tick: intent.apply_tick,
            world_revision: self.world_revision,
            queries,
        };

        // Stage both broker and adapter so any validation/fence failure leaves
        // the live sequence, generations, routes, and retry state untouched.
        let mut broker = self.broker.clone();
        let mut adapter = self.adapter.clone();
        let registration = broker.register_request(request.clone())?;
        if registration.invalidated_batches != 0 {
            return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                key: registration.key,
                count: registration.invalidated_batches,
            });
        }
        adapter.advance_batch_sequence_floor(next_batch_seq)?;
        self.broker = broker;
        self.adapter = adapter;
        self.next_batch_seq = next_batch_seq;
        for (query_key, query_generation) in query_generations {
            self.he_explosion_query_generations
                .insert(query_key, query_generation);
        }
        let replaced_route = self.routes.insert(
            registration.key,
            PendingRoute::HeExplosionEvidence(intent_key),
        );
        debug_assert!(replaced_route.is_none());
        self.he_explosion_latest_plans
            .insert(intent.projectile_id.clone(), intent.plan_id);
        let replaced_pending = self
            .pending_he_explosion_projectiles
            .insert(intent.projectile_id.clone(), intent_key);
        debug_assert!(replaced_pending.is_none());
        let replaced_record = self.he_explosion_records.insert(
            intent_key,
            HeExplosionEvidenceRecord {
                intent,
                request: request.clone(),
                queries: query_plans,
                state: HeExplosionEvidenceState::Pending,
            },
        );
        debug_assert!(replaced_record.is_none());
        Ok(HeExplosionEvidenceSchedule::New { request })
    }

    /// Changes only the destructible/static-world revision used by future
    /// requests. Existing requests retain and validate their issued revision.
    pub fn set_world_revision(
        &mut self,
        revision: WorldRevision,
    ) -> Result<(), AuthorityRuntimeError> {
        if revision < self.world_revision {
            return Err(AuthorityRuntimeError::WorldRevisionRegression {
                current: self.world_revision,
                received: revision,
            });
        }
        self.world_revision = revision;
        self.adapter.set_world_revision(revision);
        Ok(())
    }

    /// Reconcile combat already committed by the battle ledger. This cannot
    /// resurrect or heal a bot, so client or integration mistakes cannot turn
    /// this into a second combat authority.
    pub fn sync_bot_combat(
        &mut self,
        bot_id: u32,
        canonical: CanonicalBotCombatState,
    ) -> Result<(), AuthorityRuntimeError> {
        let bot = self
            .bots
            .get_mut(&bot_id)
            .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
        let current = bot.state();
        if canonical.health > current.max_health
            || canonical.display_health > current.max_health
            || canonical.alive != (canonical.health > 0)
            || (canonical.alive && canonical.death_reason.is_some())
            || (!canonical.alive && canonical.death_reason.is_none())
        {
            return Err(AuthorityRuntimeError::InvalidCombatSync { bot_id });
        }
        if !current.alive && canonical.alive {
            return Err(AuthorityRuntimeError::BotResurrection { bot_id });
        }
        if canonical.health > current.health {
            return Err(AuthorityRuntimeError::BotHealing { bot_id });
        }
        let state = bot.state_mut();
        state.health = canonical.health;
        state.display_health = canonical.display_health;
        state.alive = canonical.alive;
        state.death_reason = canonical.death_reason;
        state.critical = canonical.critical;
        if !state.alive {
            state.speed = 0.0;
            state.angular_speed = 0.0;
            state.movement_dir = 0;
            state.rotation_dir = 0;
            state.gun_aligned = false;
        }
        Ok(())
    }

    pub fn accept_oracle_reply(
        &mut self,
        reply: OracleV1BatchReply,
    ) -> Result<OracleReplyDisposition, AuthorityRuntimeError> {
        Ok(self.broker.accept_reply(reply)?)
    }

    /// Attach a projectile already admitted by the canonical projectile
    /// ledger. The runtime never constructs or authorizes a launch record.
    pub fn track_projectile(
        &mut self,
        record: ProjectileRecord,
        launch_tick: Tick,
    ) -> Result<bool, AuthorityRuntimeError> {
        let projectile_id = record.projectile_id.clone();
        let he_source = he_projectile_source_binding(&record);
        let admitted = self.projectiles.track(record, launch_tick)?;
        if let Some(source) = he_source {
            match self.he_projectile_sources.get(&projectile_id) {
                Some(previous) => debug_assert_eq!(*previous, source),
                None => {
                    self.he_projectile_sources.insert(projectile_id, source);
                }
            }
        }
        Ok(admitted)
    }

    /// Re-arm a projectile only after the canonical combat ledger has
    /// committed the ricochet continuation record.
    pub fn continue_projectile_ricochet(
        &mut self,
        record: ProjectileRecord,
    ) -> Result<bool, AuthorityRuntimeError> {
        let projectile_id = record.projectile_id.clone();
        let he_source = he_projectile_source_binding(&record);
        let admitted = self.projectiles.continue_ricochet(record)?;
        if let Some(source) = he_source {
            self.he_projectile_sources.insert(projectile_id, source);
        }
        Ok(admitted)
    }

    pub fn retire_projectile(&mut self, projectile_id: &str) -> bool {
        let retired = self.projectiles.retire(projectile_id);
        if retired {
            self.he_projectile_sources.remove(projectile_id);
        }
        retired
    }

    /// Compatibility wrapper which releases and immediately steps one 30 Hz
    /// boundary. New battle orchestration must use [`Self::release_due`] and
    /// [`Self::step_after_due`] so canonical due effects can commit first.
    pub fn advance_tick(
        &mut self,
        input: AuthorityTickInput,
    ) -> Result<AuthorityTickOutput, AuthorityRuntimeError> {
        // Preserve the old fail-before-advance behavior for malformed complete
        // inputs while production callers migrate to the two-phase API.
        let current = self.current_tick();
        let expected = current
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        if input.tick != expected {
            return Err(AuthorityRuntimeError::TickSequence {
                current,
                received: input.tick,
            });
        }
        self.validate_tick_input(&input)?;
        self.resolve_flight_targets(&input.projectile_targets)?;

        let (due, permit) = self.release_due(input.tick)?;
        let step = self.step_after_due(permit, input)?;
        Ok(AuthorityTickOutput::from_parts(due, step))
    }

    /// Release every oracle route due at `tick` without advancing any bot or
    /// registering this boundary's new native work.
    ///
    /// Socket replies must be drained through [`Self::accept_oracle_reply`]
    /// before this call. The returned permit owns bot receipts and must be
    /// consumed exactly once by [`Self::step_after_due`] or
    /// [`Self::close_terminal_after_due`].
    pub fn release_due(
        &mut self,
        tick: Tick,
    ) -> Result<(AuthorityDueOutput, ReleasedAuthorityTick), AuthorityRuntimeError> {
        self.donation
            .as_ref()
            .ok_or(AuthorityRuntimeError::MissingDonation)?;
        if let Some(released_tick) = self.released_tick {
            return Err(AuthorityRuntimeError::ReleasedTickPending {
                tick: released_tick,
            });
        }
        let current = self.current_tick();
        let expected = current
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        if tick != expected {
            return Err(AuthorityRuntimeError::TickSequence {
                current,
                received: tick,
            });
        }

        // Preserve broker batch order across applied and timed-out work. The
        // broker exposes them separately, while projectile plans require
        // oldest-plan-first application.
        let due = self.broker.advance_to(tick)?;
        let mut due_batches = Vec::with_capacity(due.applied.len() + due.timed_out.len());
        due_batches.extend(due.applied.into_iter().map(DueBatch::Applied));
        due_batches.extend(due.timed_out.into_iter().map(DueBatch::TimedOut));
        due_batches.sort_by_key(|batch| batch.key().batch_seq);

        let mut receipts = BTreeMap::<u32, OracleReceipts>::new();
        let mut failed_oracle_intents = Vec::new();
        let mut timed_out_bot_intents = Vec::new();
        let mut player_muzzles = Vec::new();
        let mut failed_player_muzzles = Vec::new();
        let mut ram_contact_evidence = Vec::new();
        let mut unavailable_ram_contacts = Vec::new();
        let mut timed_out_ram_contacts = Vec::new();
        let mut he_explosion_evidence = Vec::new();
        let mut unavailable_he_explosions = Vec::new();
        let mut timed_out_he_explosions = Vec::new();
        let mut native_observations = Vec::new();
        let mut native_firing_lanes = Vec::new();
        let mut failed_native_observations = Vec::new();
        let mut timed_out_native_observations = Vec::new();
        let mut player_environment = Vec::new();
        let mut destructible_hulls = Vec::new();
        let mut projectile_decisions = Vec::new();
        for batch in due_batches {
            let key = batch.key();
            let route = self
                .routes
                .remove(&key)
                .ok_or(AuthorityRuntimeError::MissingOracleRoute { key })?;
            match (route, batch) {
                (PendingRoute::BotIntents, DueBatch::Applied(applied)) => {
                    let decoded = self
                        .adapter
                        .decode_reply(&applied.request, &applied.reply)?;
                    let failed = decoded.merge_into(&mut receipts)?;
                    for failure in &failed {
                        receipts
                            .entry(failure.id.bot_id)
                            .or_default()
                            .failures
                            .insert(failure.id);
                    }
                    failed_oracle_intents.extend(failed);
                }
                (PendingRoute::BotIntents, DueBatch::TimedOut(timed_out)) => {
                    let ids = self.adapter.discard_request(&timed_out.request)?;
                    for id in &ids {
                        receipts.entry(id.bot_id).or_default().failures.insert(*id);
                    }
                    timed_out_bot_intents.extend(ids);
                }
                (PendingRoute::Projectile(plan_id), DueBatch::Applied(applied)) => {
                    let decision = self
                        .projectiles
                        .apply_native_batch(tick, plan_id, &applied)?;
                    self.remember_he_explosion_terminal(&decision);
                    projectile_decisions.push(decision);
                }
                (PendingRoute::Projectile(plan_id), DueBatch::TimedOut(timed_out)) => {
                    let decision = self
                        .projectiles
                        .apply_native_timeout(tick, plan_id, &timed_out)?;
                    self.remember_he_explosion_terminal(&decision);
                    projectile_decisions.push(decision);
                }
                (PendingRoute::PlayerMuzzle(intent_key), DueBatch::Applied(applied)) => {
                    let record = self.player_muzzle_record(intent_key, &applied.request)?;
                    match decode_player_muzzle_evidence(&record, &applied)? {
                        PlayerMuzzleEvidenceOutcome::Available(evidence) => {
                            self.finish_player_muzzle(intent_key, PlayerMuzzleState::Applied)?;
                            player_muzzles.push(PlayerMuzzleSample {
                                binding: record.binding,
                                entity: record.entity,
                                batch_key: record.request.key(),
                                issued_tick: record.request.issued_tick,
                                apply_tick: record.request.apply_tick,
                                transform: evidence.transform,
                                barrel_under_water: evidence.barrel_under_water,
                            });
                        }
                        PlayerMuzzleEvidenceOutcome::Unavailable => {
                            self.finish_player_muzzle(intent_key, PlayerMuzzleState::Unavailable)?;
                            failed_player_muzzles.push(FailedPlayerMuzzle {
                                binding: record.binding,
                                entity: record.entity,
                                batch_key: record.request.key(),
                                issued_tick: record.request.issued_tick,
                                apply_tick: record.request.apply_tick,
                                reason: PlayerMuzzleFailureReason::Unavailable,
                            });
                        }
                    }
                }
                (PendingRoute::PlayerMuzzle(intent_key), DueBatch::TimedOut(timed_out)) => {
                    let record = self.player_muzzle_record(intent_key, &timed_out.request)?;
                    self.finish_player_muzzle(intent_key, PlayerMuzzleState::TimedOut)?;
                    failed_player_muzzles.push(FailedPlayerMuzzle {
                        binding: record.binding,
                        entity: record.entity,
                        batch_key: record.request.key(),
                        issued_tick: record.request.issued_tick,
                        apply_tick: record.request.apply_tick,
                        reason: PlayerMuzzleFailureReason::TimedOut,
                    });
                }
                (PendingRoute::RamContactArmor, DueBatch::Applied(applied)) => {
                    let (evidence, unavailable) =
                        self.release_applied_ram_contact_armor_batch(&applied)?;
                    ram_contact_evidence.extend(evidence);
                    unavailable_ram_contacts.extend(unavailable);
                }
                (PendingRoute::RamContactArmor, DueBatch::TimedOut(timed_out)) => {
                    timed_out_ram_contacts
                        .extend(self.release_timed_out_ram_contact_armor_batch(&timed_out)?);
                }
                (PendingRoute::HeExplosionEvidence(intent_key), DueBatch::Applied(applied)) => {
                    if let Some(evidence) =
                        self.release_applied_he_explosion_evidence(intent_key, &applied)?
                    {
                        he_explosion_evidence.push(evidence);
                    } else {
                        unavailable_he_explosions.push(intent_key);
                    }
                }
                (PendingRoute::HeExplosionEvidence(intent_key), DueBatch::TimedOut(timed_out)) => {
                    self.release_timed_out_he_explosion_evidence(intent_key, &timed_out)?;
                    timed_out_he_explosions.push(intent_key);
                }
                (PendingRoute::NativeObservations, DueBatch::Applied(applied)) => {
                    let (spotting, lanes, failed) =
                        self.release_applied_native_observations(&applied)?;
                    native_observations.extend(spotting);
                    native_firing_lanes.extend(lanes);
                    failed_native_observations.extend(failed);
                }
                (PendingRoute::NativeObservations, DueBatch::TimedOut(timed_out)) => {
                    timed_out_native_observations.extend(
                        self.release_timed_out_native_observations(timed_out.request.key())?,
                    );
                }
                (PendingRoute::PlayerEnvironment, DueBatch::Applied(applied)) => {
                    player_environment.extend(self.release_applied_player_environment(&applied)?);
                }
                (PendingRoute::PlayerEnvironment, DueBatch::TimedOut(timed_out)) => {
                    self.pending_player_environment
                        .remove(&timed_out.request.key())
                        .ok_or(AuthorityRuntimeError::MissingPlayerEnvironmentBatch {
                            key: timed_out.request.key(),
                        })?;
                }
                (PendingRoute::DestructibleHulls, DueBatch::Applied(applied)) => {
                    destructible_hulls.extend(self.release_applied_destructible_hulls(&applied)?);
                }
                (PendingRoute::DestructibleHulls, DueBatch::TimedOut(timed_out)) => {
                    self.pending_destructible_hulls
                        .remove(&timed_out.request.key())
                        .ok_or(AuthorityRuntimeError::MissingDestructibleHullBatch {
                            key: timed_out.request.key(),
                        })?;
                }
            }
        }

        let output = AuthorityDueOutput {
            tick,
            failed_oracle_intents,
            timed_out_bot_intents,
            player_muzzles,
            failed_player_muzzles,
            ram_contact_evidence,
            unavailable_ram_contacts,
            timed_out_ram_contacts,
            he_explosion_evidence,
            unavailable_he_explosions,
            timed_out_he_explosions,
            native_observations,
            native_firing_lanes,
            failed_native_observations,
            timed_out_native_observations,
            player_environment,
            destructible_hulls,
            projectile_decisions,
        };
        self.released_tick = Some(tick);
        Ok((
            output,
            ReleasedAuthorityTick {
                lineage: self.lineage,
                tick,
                receipts,
            },
        ))
    }

    /// Advance bots and register new native work after the battle layer has
    /// committed every effect returned by [`Self::release_due`].
    pub fn step_after_due(
        &mut self,
        permit: ReleasedAuthorityTick,
        input: AuthorityTickInput,
    ) -> Result<AuthorityStepOutput, AuthorityRuntimeError> {
        self.validate_released_tick(&permit, input.tick)?;
        let oracle_space = self
            .donation
            .as_ref()
            .ok_or(AuthorityRuntimeError::MissingDonation)?
            .oracle_space;
        self.validate_tick_input(&input)?;
        let flight_targets = self.resolve_flight_targets(&input.projectile_targets)?;
        let traffic = self.traffic_snapshot(&input.human_traffic);
        let receipts = permit.receipts;
        let dt_us = fixed_dt_us(input.tick);
        let empty_receipts = OracleReceipts::default();
        let mut bot_outputs = Vec::with_capacity(self.bots.len());
        let mut intents = Vec::<OracleQueryIntent>::new();
        let navigation_graph = self.navigation.as_ref().map(NavRouter::graph);
        for (bot_id, bot) in &mut self.bots {
            let order = input
                .orders
                .get(bot_id)
                .expect("tick input was validated above");
            let neighbours: Vec<_> = traffic
                .iter()
                .filter(|body| !(body.kind == TargetKind::Bot && body.network_id == *bot_id))
                .cloned()
                .collect();
            let output = bot
                .step(TickInput {
                    tick: input.tick,
                    dt_us,
                    order,
                    receipts: receipts.get(bot_id).unwrap_or(&empty_receipts),
                    neighbours: &neighbours,
                    navigation_graph,
                })
                .map_err(|source| AuthorityRuntimeError::BotSimulation {
                    bot_id: *bot_id,
                    source,
                })?;
            intents.extend(output.queries);
            bot_outputs.push(partition_bot_events(*bot_id, output.events)?);
        }
        for output in &bot_outputs {
            if !output.launches.is_empty() {
                self.note_spotting_fire(
                    VehicleKey {
                        kind: VehicleKind::Bot,
                        id: u64::from(output.bot_id),
                    },
                    input.tick,
                )?;
            }
        }

        let mut requests = Vec::<(
            OracleV1BatchRequest,
            PendingRoute,
            Option<NativeObservationBatchPlan>,
        )>::new();
        let mut player_environment_plans =
            BTreeMap::<OracleV1BatchKey, PlayerEnvironmentBatchPlan>::new();
        let mut destructible_hull_plans =
            BTreeMap::<OracleV1BatchKey, DestructibleHullBatchPlan>::new();
        if !intents.is_empty() {
            self.adapter
                .advance_batch_sequence_floor(self.next_batch_seq)?;
            let bot_batches = self
                .adapter
                .build_batches(input.tick, &intents, &self.entities)?;
            self.next_batch_seq = self.adapter.next_batch_sequence();
            for request in bot_batches {
                requests.push((request, PendingRoute::BotIntents, None));
            }
        }

        let mut observation_queries = intents
            .iter()
            .filter_map(|intent| match intent {
                OracleQueryIntent::Visibility(query) => Some(query),
                _ => None,
            })
            .filter_map(|query| {
                let observer = VehicleKey {
                    kind: VehicleKind::Bot,
                    id: u64::from(query.id.bot_id),
                };
                let target = target_vehicle(query.target_kind, query.target_id);
                (self.spotting_observers.contains(&observer)
                    && self.spotting_observers.contains(&target))
                .then_some(NativeObservationQuery {
                    id: NativeObservationQueryId {
                        observer,
                        issued_tick: query.id.issued_tick,
                        apply_tick: query.id.apply_tick,
                    },
                    target,
                    source_position: query.source_position,
                    target_position: query.target_position,
                })
            })
            .collect::<Vec<_>>();
        let mut observation_purposes = observation_queries
            .iter()
            .map(|query| (query.id, NativeObservationPurpose::FireGate))
            .collect::<BTreeMap<_, _>>();
        let discovery_budget =
            MAX_NATIVE_OBSERVATION_PAIRS_PER_TICK.saturating_sub(observation_purposes.len());
        let occupied_observers = observation_purposes
            .keys()
            .map(|id| id.observer)
            .collect::<BTreeSet<_>>();
        let mut target_cursors = self.observation_target_cursors.clone();
        let mut observer_cursor = self.observation_observer_cursor;
        let supplemental = self.select_supplemental_observations(
            input.tick,
            &input.human_traffic,
            &occupied_observers,
            discovery_budget,
            &mut target_cursors,
            &mut observer_cursor,
        )?;
        for query in supplemental {
            observation_purposes.insert(query.id, NativeObservationPurpose::Discovery);
            observation_queries.push(query);
        }
        if !observation_queries.is_empty() {
            let observation_plans =
                self.build_native_observation_plans(&observation_queries, &observation_purposes)?;
            let (request, plan) =
                self.build_native_observation_request(input.tick, observation_plans)?;
            requests.push((request, PendingRoute::NativeObservations, Some(plan)));
            self.observation_target_cursors = target_cursors;
            self.observation_observer_cursor = observer_cursor;
        }

        if !input.human_traffic.is_empty() {
            let (request, plan) = self.build_player_environment_request(
                input.tick,
                oracle_space,
                &input.human_traffic,
            )?;
            let key = request.key();
            player_environment_plans.insert(key, plan);
            requests.push((request, PendingRoute::PlayerEnvironment, None));
        }

        if let Some(native_space_id) = self.destructible_native_space_id {
            for (request, plan) in self.build_destructible_hull_requests(
                input.tick,
                native_space_id,
                &input.human_traffic,
                &input.world_pose_bots,
                &bot_outputs,
            )? {
                let key = request.key();
                destructible_hull_plans.insert(key, plan);
                requests.push((request, PendingRoute::DestructibleHulls, None));
            }
        }

        let plans = self.projectiles.plan_tick(
            input.tick,
            oracle_space,
            &flight_targets,
            input.static_collision_mask,
        )?;
        for plan in plans {
            let batch_seq = self.next_batch_seq;
            self.next_batch_seq = self
                .next_batch_seq
                .checked_add(1)
                .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
            requests.push((
                plan.request(batch_seq, self.world_revision),
                PendingRoute::Projectile(plan.id),
                None,
            ));
        }
        self.adapter
            .advance_batch_sequence_floor(self.next_batch_seq)?;

        requests.sort_by_key(|(request, _, _)| request.batch_seq);
        let mut oracle_requests = Vec::with_capacity(requests.len());
        for (request, route, observation_plan) in requests {
            let registration = self.broker.register_request(request.clone())?;
            if registration.invalidated_batches != 0 {
                return Err(AuthorityRuntimeError::UnexpectedBatchSupersession {
                    key: registration.key,
                    count: registration.invalidated_batches,
                });
            }
            self.routes.insert(registration.key, route);
            if let Some(plan) = observation_plan {
                if self
                    .pending_native_observations
                    .insert(registration.key, plan)
                    .is_some()
                {
                    return Err(AuthorityRuntimeError::DuplicateNativeObservationBatch {
                        key: registration.key,
                    });
                }
            }
            if let Some(plan) = player_environment_plans.remove(&registration.key) {
                if self
                    .pending_player_environment
                    .insert(registration.key, plan)
                    .is_some()
                {
                    return Err(AuthorityRuntimeError::DuplicatePlayerEnvironmentBatch {
                        key: registration.key,
                    });
                }
            }
            if let Some(plan) = destructible_hull_plans.remove(&registration.key) {
                if self
                    .pending_destructible_hulls
                    .insert(registration.key, plan)
                    .is_some()
                {
                    return Err(AuthorityRuntimeError::DuplicateDestructibleHullBatch {
                        key: registration.key,
                    });
                }
            }
            oracle_requests.push(request);
        }
        debug_assert!(player_environment_plans.is_empty());
        debug_assert!(destructible_hull_plans.is_empty());

        self.released_tick = None;
        Ok(AuthorityStepOutput {
            tick: input.tick,
            bots: bot_outputs,
            oracle_requests,
        })
    }

    /// Consume a released boundary after due effects ended the battle. No bot
    /// advances and no new oracle work is registered.
    pub fn close_terminal_after_due(
        &mut self,
        permit: ReleasedAuthorityTick,
    ) -> Result<(), AuthorityRuntimeError> {
        self.validate_released_tick(&permit, permit.tick)?;
        self.released_tick = None;
        Ok(())
    }

    /// Select a bounded discovery shard without deriving visibility from
    /// distance. Every selected pair still requires distinct spotting and
    /// barrel-lane oracle evidence.
    fn select_supplemental_observations(
        &self,
        issued_tick: Tick,
        humans: &[TrafficBody],
        occupied_observers: &BTreeSet<VehicleKey>,
        budget: usize,
        target_cursors: &mut BTreeMap<VehicleKey, usize>,
        observer_cursor: &mut usize,
    ) -> Result<Vec<NativeObservationQuery>, AuthorityRuntimeError> {
        if budget == 0 {
            return Ok(Vec::new());
        }
        let apply_tick = issued_tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        let mut actors = self
            .bots
            .iter()
            .filter_map(|(bot_id, bot)| {
                let state = bot.state();
                let vehicle = VehicleKey {
                    kind: VehicleKind::Bot,
                    id: u64::from(*bot_id),
                };
                (state.alive && self.spotting_observers.contains(&vehicle)).then_some((
                    vehicle,
                    state.team,
                    state.position,
                ))
            })
            .chain(humans.iter().filter_map(|human| {
                let vehicle = VehicleKey {
                    kind: VehicleKind::Player,
                    id: u64::from(human.network_id),
                };
                self.spotting_observers.contains(&vehicle).then_some((
                    vehicle,
                    human.team,
                    human.position,
                ))
            }))
            .collect::<Vec<_>>();
        actors.sort_by_key(|(vehicle, _, _)| vehicle_order_key(*vehicle));

        let mut candidates = BTreeMap::<VehicleKey, Vec<NativeObservationCandidate>>::new();
        for (observer, observer_team, observer_position) in &actors {
            if occupied_observers.contains(observer) {
                continue;
            }
            let source_position = Vec3::new(
                observer_position.x,
                observer_position.y + NATIVE_OBSERVATION_SOURCE_HEIGHT,
                observer_position.z,
            );
            let mut targets = Vec::new();
            for (target, target_team, target_position) in &actors {
                if target == observer || target_team == observer_team {
                    continue;
                }
                targets.push(NativeObservationCandidate {
                    observer: *observer,
                    target: *target,
                    source_position,
                    target_position: *target_position,
                });
            }
            targets.retain(|candidate| {
                valid_observation_segment(candidate.source_position, candidate.target_position)
            });
            targets.sort_by_key(|candidate| vehicle_order_key(candidate.target));
            if !targets.is_empty() {
                candidates.insert(*observer, targets);
            }
        }

        target_cursors.retain(|observer, _| candidates.contains_key(observer));
        let mut observers = candidates.keys().copied().collect::<Vec<_>>();
        observers.sort_by_key(|vehicle| vehicle_order_key(*vehicle));
        if observers.is_empty() {
            *observer_cursor = 0;
            return Ok(Vec::new());
        }
        let count = budget.min(observers.len());
        let start = *observer_cursor % observers.len();
        let mut selected = Vec::with_capacity(count);
        for offset in 0..count {
            let observer = observers[(start + offset) % observers.len()];
            let targets = &candidates[&observer];
            let target_cursor = target_cursors.entry(observer).or_default();
            let target = targets[*target_cursor % targets.len()].clone();
            *target_cursor = (*target_cursor + 1) % targets.len();
            selected.push(NativeObservationQuery {
                id: NativeObservationQueryId {
                    observer: target.observer,
                    issued_tick,
                    apply_tick,
                },
                target: target.target,
                source_position: target.source_position,
                target_position: target.target_position,
            });
        }
        *observer_cursor = (start + count) % observers.len();
        Ok(selected)
    }

    fn build_native_observation_plans(
        &self,
        queries: &[NativeObservationQuery],
        purposes: &BTreeMap<NativeObservationQueryId, NativeObservationPurpose>,
    ) -> Result<BTreeMap<NativeObservationQueryId, NativeObservationIntent>, AuthorityRuntimeError>
    {
        let mut plans = BTreeMap::new();
        for query in queries {
            let purpose = purposes
                .get(&query.id)
                .copied()
                .ok_or(AuthorityRuntimeError::MissingNativeObservationPlan { id: query.id })?;
            let observer_entity =
                self.entities
                    .get(vehicle_entity(query.id.observer).ok_or(
                        AuthorityRuntimeError::MissingNativeObservationPlan { id: query.id },
                    )?)
                    .ok_or(AuthorityRuntimeError::MissingNativeObservationPlan { id: query.id })?;
            let target_entity =
                self.entities
                    .get(vehicle_entity(query.target).ok_or(
                        AuthorityRuntimeError::MissingNativeObservationPlan { id: query.id },
                    )?)
                    .ok_or(AuthorityRuntimeError::MissingNativeObservationPlan { id: query.id })?;
            let evaluated_for_recent_fire = fired_recently(
                time_us_at_tick(query.id.issued_tick),
                self.last_spotting_fire_us.get(&query.target).copied(),
            );
            let plan = NativeObservationIntent {
                purpose,
                observer: query.id.observer,
                target: query.target,
                lineage: self.lineage,
                observer_entity,
                target_entity,
                issued_tick: query.id.issued_tick,
                apply_tick: query.id.apply_tick,
                source_position: Vec3::new(
                    query.source_position.x,
                    query.source_position.y - NATIVE_OBSERVATION_SOURCE_HEIGHT,
                    query.source_position.z,
                ),
                target_position: query.target_position,
                evaluated_for_recent_fire,
            };
            if plans.insert(query.id, plan).is_some() {
                return Err(AuthorityRuntimeError::DuplicateNativeObservationPlan { id: query.id });
            }
        }
        Ok(plans)
    }

    fn build_native_observation_request(
        &mut self,
        issued_tick: Tick,
        plans: BTreeMap<NativeObservationQueryId, NativeObservationIntent>,
    ) -> Result<(OracleV1BatchRequest, NativeObservationBatchPlan), AuthorityRuntimeError> {
        let apply_tick = issued_tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        let next_batch_seq = self
            .next_batch_seq
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
        let mut generations = self.native_observation_query_generations.clone();
        let mut queries = Vec::with_capacity(plans.len() * 2);
        let mut query_plans = BTreeMap::new();
        let lane = issued_tick % (ORACLE_PIPELINE_TICKS + 1);
        let mut query_id = 1u64;
        for intent in plans.into_values() {
            debug_assert_eq!(intent.issued_tick, issued_tick);
            debug_assert_eq!(intent.apply_tick, apply_tick);
            let evidence_kinds: &[NativeObservationEvidenceKind] = match intent.observer.kind {
                VehicleKind::Bot => &[
                    NativeObservationEvidenceKind::Spotting,
                    NativeObservationEvidenceKind::FiringLane,
                ],
                VehicleKind::Player => &[NativeObservationEvidenceKind::Spotting],
            };
            for &evidence in evidence_kinds {
                let key = native_observation_query_key(&intent, evidence, lane);
                let query_generation = generations
                    .get(&key)
                    .copied()
                    .unwrap_or(0)
                    .checked_add(1)
                    .ok_or_else(|| {
                    AuthorityRuntimeError::NativeObservationGenerationExhausted { key: key.clone() }
                })?;
                generations.insert(key.clone(), query_generation);
                let observer_position = oracle_vec3(intent.source_position);
                let target_position = oracle_vec3(intent.target_position);
                let operation = match evidence {
                    NativeObservationEvidenceKind::Spotting => {
                        OracleOperation::SpottingEvidence(SpottingEvidenceQuery {
                            observer: intent.observer_entity,
                            target: intent.target_entity,
                            observer_position,
                            target_position,
                            collision_mask: NATIVE_OBSERVATION_COLLISION_MASK,
                            evaluated_for_recent_fire: intent.evaluated_for_recent_fire,
                        })
                    }
                    NativeObservationEvidenceKind::FiringLane => {
                        OracleOperation::FiringLaneEvidence(FiringLaneEvidenceQuery {
                            observer: intent.observer_entity,
                            target: intent.target_entity,
                            observer_position,
                            target_position,
                            collision_mask: NATIVE_OBSERVATION_COLLISION_MASK,
                        })
                    }
                };
                queries.push(OracleV1Query {
                    query_id,
                    key,
                    query_generation,
                    entity: intent.target_entity,
                    operation,
                });
                query_plans.insert(
                    query_id,
                    NativeObservationQueryPlan {
                        intent: intent.clone(),
                        evidence,
                    },
                );
                query_id = query_id
                    .checked_add(1)
                    .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
            }
        }
        let request = OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: self.lineage.round_id,
            authority_epoch: self.lineage.authority_epoch,
            oracle_generation: self.lineage.oracle_generation,
            batch_seq: self.next_batch_seq,
            issued_tick,
            apply_tick,
            world_revision: self.world_revision,
            queries,
        };
        self.native_observation_query_generations = generations;
        self.next_batch_seq = next_batch_seq;
        Ok((
            request,
            NativeObservationBatchPlan {
                queries: query_plans,
            },
        ))
    }

    fn release_applied_native_observations(
        &mut self,
        applied: &AppliedOracleBatch,
    ) -> Result<
        (
            Vec<NativeObservationSample>,
            Vec<NativeFiringLaneSample>,
            Vec<FailedNativeObservation>,
        ),
        AuthorityRuntimeError,
    > {
        let batch_key = applied.request.key();
        let mut plan = self
            .pending_native_observations
            .remove(&batch_key)
            .ok_or(AuthorityRuntimeError::MissingNativeObservationBatch { key: batch_key })?;
        let mut spotting = Vec::new();
        let mut lanes = Vec::new();
        let mut failed = Vec::new();
        for result in &applied.reply.results {
            let query_plan = plan.queries.remove(&result.query_id).ok_or(
                AuthorityRuntimeError::UnexpectedNativeObservationResult {
                    key: batch_key,
                    query_id: result.query_id,
                },
            )?;
            match (&result.status, query_plan.evidence) {
                (
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::SpottingEvidence(evidence),
                    },
                    NativeObservationEvidenceKind::Spotting,
                ) => spotting.push(NativeObservationSample {
                    intent: query_plan.intent,
                    batch_key,
                    line_of_sight: evidence.line_of_sight,
                    foliage_bonus: evidence.foliage_bonus,
                    evaluated_for_recent_fire: evidence.evaluated_for_recent_fire,
                }),
                (
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::FiringLaneEvidence(evidence),
                    },
                    NativeObservationEvidenceKind::FiringLane,
                ) => lanes.push(NativeFiringLaneSample {
                    intent: query_plan.intent,
                    batch_key,
                    clear: evidence.clear,
                }),
                (OracleV1ResultStatus::Unavailable { code, message }, evidence) => {
                    failed.push(FailedNativeObservation {
                        intent: query_plan.intent,
                        batch_key,
                        evidence,
                        reason: format!("{code}: {message}"),
                    });
                }
                _ => {
                    return Err(AuthorityRuntimeError::UnexpectedNativeObservationResult {
                        key: batch_key,
                        query_id: result.query_id,
                    });
                }
            }
        }
        if let Some(query_id) = plan.queries.keys().next().copied() {
            return Err(AuthorityRuntimeError::UnexpectedNativeObservationResult {
                key: batch_key,
                query_id,
            });
        }
        Ok((spotting, lanes, failed))
    }

    fn release_timed_out_native_observations(
        &mut self,
        batch_key: OracleV1BatchKey,
    ) -> Result<Vec<TimedOutNativeObservation>, AuthorityRuntimeError> {
        let plan = self
            .pending_native_observations
            .remove(&batch_key)
            .ok_or(AuthorityRuntimeError::MissingNativeObservationBatch { key: batch_key })?;
        Ok(plan
            .queries
            .into_values()
            .map(|query| TimedOutNativeObservation {
                intent: query.intent,
                batch_key,
                evidence: query.evidence,
            })
            .collect())
    }

    fn build_destructible_hull_requests(
        &mut self,
        issued_tick: Tick,
        native_space_id: i64,
        humans: &[TrafficBody],
        world_pose_bots: &BTreeSet<u32>,
        bots: &[AuthorityBotTick],
    ) -> Result<Vec<(OracleV1BatchRequest, DestructibleHullBatchPlan)>, AuthorityRuntimeError> {
        debug_assert!(MAX_DESTRUCTIBLE_HULL_ACTORS_PER_BATCH > 0);
        let apply_tick = issued_tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        let lane = issued_tick % (ORACLE_PIPELINE_TICKS + 1);
        let mut actors = Vec::<(VehicleKey, EntityRef, Vec3, f64, f64, f64)>::new();
        for output in bots {
            let state = self
                .bots
                .get(&output.bot_id)
                .ok_or(AuthorityRuntimeError::UnknownBot {
                    bot_id: output.bot_id,
                })?
                .state();
            if !state.alive || !world_pose_bots.contains(&output.bot_id) {
                continue;
            }
            let vehicle = VehicleKey {
                kind: VehicleKind::Bot,
                id: u64::from(output.bot_id),
            };
            let entity = self
                .entities
                .get(SimulationEntity::Bot(output.bot_id))
                .ok_or(AuthorityRuntimeError::MissingDonatedBot {
                    bot_id: output.bot_id,
                })?;
            if (output.pose.speed.abs() <= DESTRUCTIBLE_HULL_MOVING_EPSILON_MPS
                && output.pose.movement_dir == 0
                && output.pose.rotation_dir == 0)
                || !destructible_hull_due(vehicle, issued_tick)
            {
                continue;
            }
            let kinetic_speed = output.pose.speed.clamp(
                -MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS,
                MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS,
            );
            actors.push((
                vehicle,
                entity,
                output.pose.position,
                wrapped(output.pose.yaw),
                (kinetic_speed * DESTRUCTIBLE_HULL_PROOF_SECONDS).clamp(
                    -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
                    MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
                ),
                kinetic_speed,
            ));
        }
        for body in humans {
            let vehicle = VehicleKey {
                kind: VehicleKind::Player,
                id: u64::from(body.network_id),
            };
            let entity = self
                .entities
                .get(SimulationEntity::Human(body.network_id))
                .ok_or(AuthorityRuntimeError::InvalidHumanTraffic {
                    player_id: body.network_id,
                })?;
            if body.velocity.x.hypot(body.velocity.z) <= DESTRUCTIBLE_HULL_MOVING_EPSILON_MPS
                || !destructible_hull_due(vehicle, issued_tick)
            {
                continue;
            }
            let yaw = wrapped(body.yaw);
            let kinetic_speed = (body.velocity.x * yaw.sin() + body.velocity.z * yaw.cos()).clamp(
                -MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS,
                MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS,
            );
            actors.push((
                vehicle,
                entity,
                body.position,
                yaw,
                (kinetic_speed * DESTRUCTIBLE_HULL_PROOF_SECONDS).clamp(
                    -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
                    MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
                ),
                kinetic_speed,
            ));
        }
        actors.sort_by_key(|(vehicle, ..)| *vehicle);

        let mut generations = self.destructible_hull_query_generations.clone();
        let mut next_batch_seq = self.next_batch_seq;
        let mut requests = Vec::with_capacity(
            actors
                .len()
                .div_ceil(MAX_DESTRUCTIBLE_HULL_ACTORS_PER_BATCH),
        );
        for chunk in actors.chunks(MAX_DESTRUCTIBLE_HULL_ACTORS_PER_BATCH) {
            let batch_seq = next_batch_seq;
            next_batch_seq = next_batch_seq
                .checked_add(1)
                .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
            let mut queries = Vec::with_capacity(chunk.len());
            let mut plans = BTreeMap::new();
            for (index, (vehicle, entity, position, yaw, frame_travel, kinetic_speed)) in
                chunk.iter().enumerate()
            {
                let query_id = u64::try_from(index + 1)
                    .map_err(|_| AuthorityRuntimeError::BatchSequenceExhausted)?;
                let actor_kind = match vehicle.kind {
                    VehicleKind::Bot => "bot",
                    VehicleKind::Player => "player",
                };
                let key = format!(
                    "destructible-hull/{actor_kind}/{}/e{}g{}/lane/{lane}",
                    vehicle.id, entity.entity_id, entity.generation
                );
                let query_generation = generations
                    .get(&key)
                    .copied()
                    .unwrap_or(0)
                    .checked_add(1)
                    .ok_or_else(|| {
                    AuthorityRuntimeError::DestructibleHullGenerationExhausted { key: key.clone() }
                })?;
                generations.insert(key.clone(), query_generation);
                queries.push(OracleV1Query {
                    query_id,
                    key,
                    query_generation,
                    entity: *entity,
                    operation: OracleOperation::DestructibleHullEvidence(
                        DestructibleHullEvidenceQuery {
                            space_id: native_space_id,
                            position: OracleVec3 {
                                x: position.x as f32,
                                y: position.y as f32,
                                z: position.z as f32,
                            },
                            yaw: *yaw,
                            frame_travel: *frame_travel,
                        },
                    ),
                });
                plans.insert(
                    query_id,
                    DestructibleHullQueryPlan {
                        vehicle: *vehicle,
                        position: *position,
                        yaw: *yaw,
                        frame_travel: *frame_travel,
                        kinetic_speed: *kinetic_speed,
                    },
                );
            }
            let request = OracleV1BatchRequest {
                protocol_version: ORACLE_PROTOCOL_VERSION,
                round_id: self.lineage.round_id,
                authority_epoch: self.lineage.authority_epoch,
                oracle_generation: self.lineage.oracle_generation,
                batch_seq,
                issued_tick,
                apply_tick,
                world_revision: self.world_revision,
                queries,
            };
            debug_assert!(
                request
                    .queries
                    .iter()
                    .map(|query| query.operation.primitive_count())
                    .sum::<usize>()
                    <= MAX_ORACLE_PRIMITIVE_OPERATIONS
            );
            requests.push((request, DestructibleHullBatchPlan { queries: plans }));
        }
        self.next_batch_seq = next_batch_seq;
        self.destructible_hull_query_generations = generations;
        Ok(requests)
    }

    fn release_applied_destructible_hulls(
        &mut self,
        applied: &AppliedOracleBatch,
    ) -> Result<Vec<NativeDestructibleHullSample>, AuthorityRuntimeError> {
        let batch_key = applied.request.key();
        let mut plan = self
            .pending_destructible_hulls
            .remove(&batch_key)
            .ok_or(AuthorityRuntimeError::MissingDestructibleHullBatch { key: batch_key })?;
        let mut samples = Vec::new();
        for result in &applied.reply.results {
            let frozen = plan.queries.remove(&result.query_id).ok_or(
                AuthorityRuntimeError::UnexpectedDestructibleHullResult {
                    key: batch_key,
                    query_id: result.query_id,
                },
            )?;
            match &result.status {
                OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::DestructibleHullEvidence(evidence),
                } => samples.push(NativeDestructibleHullSample {
                    vehicle: frozen.vehicle,
                    batch_key,
                    issued_tick: applied.request.issued_tick,
                    apply_tick: applied.request.apply_tick,
                    position: frozen.position,
                    yaw: frozen.yaw,
                    frame_travel: frozen.frame_travel,
                    kinetic_speed: frozen.kinetic_speed,
                    evidence: evidence.clone(),
                }),
                OracleV1ResultStatus::Unavailable { .. } => {}
                _ => {
                    return Err(AuthorityRuntimeError::UnexpectedDestructibleHullResult {
                        key: batch_key,
                        query_id: result.query_id,
                    });
                }
            }
        }
        if let Some(query_id) = plan.queries.keys().next().copied() {
            return Err(AuthorityRuntimeError::UnexpectedDestructibleHullResult {
                key: batch_key,
                query_id,
            });
        }
        Ok(samples)
    }

    fn build_player_environment_request(
        &mut self,
        issued_tick: Tick,
        oracle_space: EntityRef,
        humans: &[TrafficBody],
    ) -> Result<(OracleV1BatchRequest, PlayerEnvironmentBatchPlan), AuthorityRuntimeError> {
        let apply_tick = issued_tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(AuthorityRuntimeError::TickCounterExhausted)?;
        let next_batch_seq = self
            .next_batch_seq
            .checked_add(1)
            .ok_or(AuthorityRuntimeError::BatchSequenceExhausted)?;
        let lane = issued_tick % (ORACLE_PIPELINE_TICKS + 1);
        let mut generations = self.player_environment_query_generations.clone();
        let mut players = humans
            .iter()
            .map(|body| PlayerEnvironmentQueryPlan {
                player_id: u64::from(body.network_id),
                pose: BodyPose {
                    x: body.position.x,
                    y: body.position.y,
                    z: body.position.z,
                    yaw: body.yaw,
                    pitch: 0.0,
                    roll: 0.0,
                    speed: body.velocity.x.hypot(body.velocity.z),
                    aim_yaw: 0.0,
                    gun_pitch: 0.0,
                },
            })
            .collect::<Vec<_>>();
        players.sort_by_key(|player| player.player_id);
        let positions = players
            .iter()
            .map(|player| OracleVec3 {
                x: player.pose.x as f32,
                y: player.pose.y as f32,
                z: player.pose.z as f32,
            })
            .collect::<Vec<_>>();

        let mut queries = Vec::with_capacity(2);
        for (query_id, kind, operation) in [
            (
                1,
                "ground",
                OracleOperation::GroundSampleBatch {
                    positions: positions.clone(),
                },
            ),
            (2, "water", OracleOperation::WaterSampleBatch { positions }),
        ] {
            let key = format!("player-environment:{kind}:lane:{lane}");
            let query_generation = generations
                .get(&key)
                .copied()
                .unwrap_or(0)
                .checked_add(1)
                .ok_or_else(
                    || AuthorityRuntimeError::PlayerEnvironmentGenerationExhausted {
                        key: key.clone(),
                    },
                )?;
            generations.insert(key.clone(), query_generation);
            queries.push(OracleV1Query {
                query_id,
                key,
                query_generation,
                entity: oracle_space,
                operation,
            });
        }
        let request = OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: self.lineage.round_id,
            authority_epoch: self.lineage.authority_epoch,
            oracle_generation: self.lineage.oracle_generation,
            batch_seq: self.next_batch_seq,
            issued_tick,
            apply_tick,
            world_revision: self.world_revision,
            queries,
        };
        self.player_environment_query_generations = generations;
        self.next_batch_seq = next_batch_seq;
        Ok((
            request,
            PlayerEnvironmentBatchPlan {
                players,
                ground_query_id: 1,
                water_query_id: 2,
            },
        ))
    }

    fn release_applied_player_environment(
        &mut self,
        applied: &AppliedOracleBatch,
    ) -> Result<Vec<NativePlayerEnvironmentSample>, AuthorityRuntimeError> {
        let batch_key = applied.request.key();
        let plan = self
            .pending_player_environment
            .remove(&batch_key)
            .ok_or(AuthorityRuntimeError::MissingPlayerEnvironmentBatch { key: batch_key })?;
        let mut ground_samples = None;
        let mut water_heights = None;
        for result in &applied.reply.results {
            if result.query_id == plan.ground_query_id {
                match &result.status {
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::GroundSampleBatch { samples },
                    } if ground_samples.replace(samples.clone()).is_none() => {}
                    OracleV1ResultStatus::Unavailable { .. } => {}
                    _ => {
                        return Err(AuthorityRuntimeError::UnexpectedPlayerEnvironmentResult {
                            key: batch_key,
                            query_id: result.query_id,
                        });
                    }
                }
            } else if result.query_id == plan.water_query_id {
                match &result.status {
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::WaterSampleBatch { heights },
                    } if water_heights.replace(heights.clone()).is_none() => {}
                    OracleV1ResultStatus::Unavailable { .. } => {}
                    _ => {
                        return Err(AuthorityRuntimeError::UnexpectedPlayerEnvironmentResult {
                            key: batch_key,
                            query_id: result.query_id,
                        });
                    }
                }
            } else {
                return Err(AuthorityRuntimeError::UnexpectedPlayerEnvironmentResult {
                    key: batch_key,
                    query_id: result.query_id,
                });
            }
        }

        let mut samples = Vec::with_capacity(plan.players.len());
        for (index, player) in plan.players.into_iter().enumerate() {
            let ground = ground_samples.as_ref().map(|values| match &values[index] {
                Some(sample) => PlayerGroundEvidence {
                    height: Some(f64::from(sample.height)),
                    supported: f64::from(sample.normal.y) > PLAYER_GROUND_SUPPORT_NORMAL_Y,
                },
                None => PlayerGroundEvidence {
                    height: None,
                    supported: false,
                },
            });
            let water_depth = water_heights.as_ref().map(|values| {
                values[index]
                    .map(|height| (f64::from(height) - player.pose.y).max(0.0))
                    .unwrap_or(0.0)
            });
            if ground.is_none() && water_depth.is_none() {
                continue;
            }
            samples.push(NativePlayerEnvironmentSample {
                player_id: player.player_id,
                evidence: PlayerEnvironmentEvidence {
                    issued_tick: applied.request.issued_tick,
                    apply_tick: applied.request.apply_tick,
                    pose: player.pose,
                    ground,
                    water_depth,
                },
            });
        }
        Ok(samples)
    }

    fn player_muzzle_record(
        &self,
        key: PlayerMuzzleIntentKey,
        request: &OracleV1BatchRequest,
    ) -> Result<PlayerMuzzleRecord, AuthorityRuntimeError> {
        let record = self
            .player_muzzle_records
            .get(&key)
            .filter(|record| {
                record.state == PlayerMuzzleState::Pending && &record.request == request
            })
            .cloned()
            .ok_or(AuthorityRuntimeError::MissingPlayerMuzzlePlan { key })?;
        if self.pending_player_muzzles.get(&key.player_id) != Some(&key) {
            return Err(AuthorityRuntimeError::MissingPlayerMuzzlePlan { key });
        }
        Ok(record)
    }

    fn finish_player_muzzle(
        &mut self,
        key: PlayerMuzzleIntentKey,
        state: PlayerMuzzleState,
    ) -> Result<(), AuthorityRuntimeError> {
        if state == PlayerMuzzleState::Pending
            || self.pending_player_muzzles.get(&key.player_id) != Some(&key)
        {
            return Err(AuthorityRuntimeError::MissingPlayerMuzzlePlan { key });
        }
        let record = self
            .player_muzzle_records
            .get_mut(&key)
            .filter(|record| record.state == PlayerMuzzleState::Pending)
            .ok_or(AuthorityRuntimeError::MissingPlayerMuzzlePlan { key })?;
        record.state = state;
        self.pending_player_muzzles.remove(&key.player_id);

        let history = self
            .player_muzzle_terminal_history
            .entry(key.player_id)
            .or_default();
        history.push_back(key.intent_seq);
        while history.len() > FIRE_INTENT_HISTORY {
            if let Some(intent_seq) = history.pop_front() {
                self.player_muzzle_records.remove(&PlayerMuzzleIntentKey {
                    player_id: key.player_id,
                    intent_seq,
                });
            }
        }
        Ok(())
    }

    fn ram_contact_record(
        &self,
        key: RamContactArmorIntentKey,
        request: &OracleV1BatchRequest,
    ) -> Result<RamContactArmorRecord, AuthorityRuntimeError> {
        let record = self
            .ram_contact_records
            .get(&key)
            .filter(|record| {
                record.state == RamContactArmorState::Pending && &record.request == request
            })
            .cloned()
            .ok_or(AuthorityRuntimeError::MissingRamContactArmorPlan { key })?;
        if !self.pending_ram_contacts.contains(&key) {
            return Err(AuthorityRuntimeError::MissingRamContactArmorPlan { key });
        }
        Ok(record)
    }

    fn release_applied_ram_contact_armor_batch(
        &mut self,
        applied: &AppliedOracleBatch,
    ) -> Result<(Vec<NativeRamContactEvidence>, Vec<RamContactArmorIntentKey>), AuthorityRuntimeError>
    {
        let batch_key = applied.request.key();
        if applied.reply.key() != batch_key {
            return Err(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key });
        }
        let keys = self
            .ram_contact_batches
            .get(&batch_key)
            .cloned()
            .ok_or(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key })?;
        if keys.len() != applied.request.queries.len() || keys.len() != applied.reply.results.len()
        {
            return Err(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key });
        }
        let results = applied
            .reply
            .results
            .iter()
            .map(|result| (result.query_id, result))
            .collect::<BTreeMap<_, _>>();
        let mut evidence = Vec::new();
        let mut unavailable = Vec::new();
        for key in &keys {
            let record = self.ram_contact_record(*key, &applied.request)?;
            let query = applied
                .request
                .queries
                .iter()
                .find(|query| query.query_id == record.query_id)
                .ok_or(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key })?;
            let OracleOperation::RamContactArmorEvidence(arguments) = &query.operation else {
                return Err(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key });
            };
            if query.entity != record.first_entity
                || arguments.first != record.first_entity
                || arguments.second != record.second_entity
            {
                return Err(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key });
            }
            let result = results
                .get(&record.query_id)
                .copied()
                .ok_or(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key })?;
            if result.key != query.key
                || result.query_generation != query.query_generation
                || result.entity != query.entity
            {
                return Err(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key });
            }
            match &result.status {
                OracleV1ResultStatus::Unavailable { .. } => unavailable.push(*key),
                OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::RamContactArmorEvidence(armor),
                } => {
                    let first_armor =
                        NativeContactArmor::new(armor.first_armor_mm).map_err(|_| {
                            AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key }
                        })?;
                    let second_armor =
                        NativeContactArmor::new(armor.second_armor_mm).map_err(|_| {
                            AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key }
                        })?;
                    evidence.push(
                        NativeRamContactEvidence::new(
                            record.intent.pair,
                            record.intent.cursor,
                            record.intent.source_time_us,
                            RamVehicleContactEvidence::new(
                                first_armor,
                                record.intent.first_profile,
                            ),
                            RamVehicleContactEvidence::new(
                                second_armor,
                                record.intent.second_profile,
                            ),
                            record.intent.first_moving,
                            record.intent.second_moving,
                        )
                        .map_err(|_| {
                            AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key }
                        })?,
                    );
                }
                _ => {
                    return Err(AuthorityRuntimeError::InvalidRamContactArmorReply {
                        key: batch_key,
                    });
                }
            }
        }
        for key in &keys {
            self.finish_ram_contact_armor(*key, RamContactArmorState::Applied)?;
        }
        self.ram_contact_batches.remove(&batch_key);
        Ok((evidence, unavailable))
    }

    fn release_timed_out_ram_contact_armor_batch(
        &mut self,
        timed_out: &TimedOutOracleBatch,
    ) -> Result<Vec<RamContactArmorIntentKey>, AuthorityRuntimeError> {
        let batch_key = timed_out.request.key();
        let keys = self
            .ram_contact_batches
            .get(&batch_key)
            .cloned()
            .ok_or(AuthorityRuntimeError::InvalidRamContactArmorReply { key: batch_key })?;
        for key in &keys {
            self.ram_contact_record(*key, &timed_out.request)?;
        }
        for key in &keys {
            self.finish_ram_contact_armor(*key, RamContactArmorState::TimedOut)?;
        }
        self.ram_contact_batches.remove(&batch_key);
        Ok(keys)
    }

    fn finish_ram_contact_armor(
        &mut self,
        key: RamContactArmorIntentKey,
        state: RamContactArmorState,
    ) -> Result<(), AuthorityRuntimeError> {
        if state == RamContactArmorState::Pending || !self.pending_ram_contacts.contains(&key) {
            return Err(AuthorityRuntimeError::MissingRamContactArmorPlan { key });
        }
        let record = self
            .ram_contact_records
            .get_mut(&key)
            .filter(|record| record.state == RamContactArmorState::Pending)
            .ok_or(AuthorityRuntimeError::MissingRamContactArmorPlan { key })?;
        record.state = state;
        self.pending_ram_contacts.remove(&key);

        let history = self
            .ram_contact_terminal_history
            .entry(key.pair)
            .or_default();
        history.push_back(key.cursor);
        while history.len() > MAX_RAM_POSE_RETRY_HISTORY {
            if let Some(cursor) = history.pop_front() {
                self.ram_contact_records.remove(&RamContactArmorIntentKey {
                    pair: key.pair,
                    cursor,
                });
            }
        }
        Ok(())
    }

    fn remember_he_explosion_terminal(&mut self, decision: &ProjectileFlightDecision) {
        let ProjectileFlightDecision::Terminal(proposal) = decision else {
            return;
        };
        if !matches!(
            proposal.cause,
            ProjectileTerminalCause::Direct { .. }
                | ProjectileTerminalCause::Wreck { .. }
                | ProjectileTerminalCause::Terrain { .. }
                | ProjectileTerminalCause::DestructibleBacking { .. }
                | ProjectileTerminalCause::Destructible { .. }
        ) {
            self.he_projectile_sources
                .remove(&proposal.resolution.projectile_id);
            return;
        }
        let Some(impact) = proposal.resolution.impact else {
            self.he_projectile_sources
                .remove(&proposal.resolution.projectile_id);
            return;
        };
        let Some(source) = self
            .he_projectile_sources
            .get(&proposal.resolution.projectile_id)
            .copied()
        else {
            return;
        };
        let Some(incoming_direction) =
            he_incoming_direction(source, proposal.resolution.resolved_time_ms)
        else {
            self.he_projectile_sources
                .remove(&proposal.resolution.projectile_id);
            return;
        };
        let binding = HeExplosionTerminalBinding {
            projectile_id: proposal.resolution.projectile_id.clone(),
            applied_tick: proposal.applied_tick,
            impact,
            incoming_direction,
            caliber_mm: source.caliber_mm,
        };
        match self.he_explosion_terminal_bindings.get(&proposal.plan_id) {
            Some(previous) => {
                debug_assert_eq!(previous.projectile_id, binding.projectile_id);
                debug_assert_eq!(previous.applied_tick, binding.applied_tick);
                debug_assert_eq!(previous.impact, binding.impact);
                debug_assert_eq!(previous.incoming_direction, binding.incoming_direction);
                debug_assert_eq!(previous.caliber_mm, binding.caliber_mm);
            }
            None => {
                self.he_explosion_terminal_bindings
                    .insert(proposal.plan_id, binding);
                self.he_explosion_terminal_binding_order
                    .push_back(proposal.plan_id);
            }
        }
        while self.he_explosion_terminal_binding_order.len() > HE_EXPLOSION_EVIDENCE_RETRY_HISTORY {
            if let Some(plan_id) = self.he_explosion_terminal_binding_order.pop_front() {
                if let Some(binding) = self.he_explosion_terminal_bindings.remove(&plan_id) {
                    self.he_projectile_sources.remove(&binding.projectile_id);
                }
            }
        }
    }

    fn he_explosion_evidence_record(
        &self,
        key: HeExplosionEvidenceIntentKey,
        request: &OracleV1BatchRequest,
    ) -> Result<HeExplosionEvidenceRecord, AuthorityRuntimeError> {
        let record = self
            .he_explosion_records
            .get(&key)
            .filter(|record| {
                record.state == HeExplosionEvidenceState::Pending && &record.request == request
            })
            .cloned()
            .ok_or(AuthorityRuntimeError::MissingHeExplosionEvidencePlan { key })?;
        if self
            .pending_he_explosion_projectiles
            .get(&record.intent.projectile_id)
            != Some(&key)
        {
            return Err(AuthorityRuntimeError::MissingHeExplosionEvidencePlan { key });
        }
        Ok(record)
    }

    fn release_applied_he_explosion_evidence(
        &mut self,
        key: HeExplosionEvidenceIntentKey,
        applied: &AppliedOracleBatch,
    ) -> Result<Option<NativeHeExplosionEvidenceSample>, AuthorityRuntimeError> {
        let record = self.he_explosion_evidence_record(key, &applied.request)?;
        let batch_key = record.request.key();
        if applied.reply.key() != batch_key
            || record.request.queries.len() != record.queries.len()
            || applied.reply.results.len() != record.queries.len()
        {
            return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply { key: batch_key });
        }
        let results = applied
            .reply
            .results
            .iter()
            .map(|result| (result.query_id, result))
            .collect::<BTreeMap<_, _>>();
        if results.len() != record.queries.len() {
            return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply { key: batch_key });
        }

        let mut unavailable = false;
        let mut targets = Vec::with_capacity(record.queries.len());
        for query in &record.request.queries {
            let plan = record
                .queries
                .get(&query.query_id)
                .ok_or(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply { key: batch_key })?;
            let result = results
                .get(&query.query_id)
                .copied()
                .ok_or(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply { key: batch_key })?;
            let OracleOperation::ExplosionEvidence(arguments) = &query.operation else {
                return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply {
                    key: batch_key,
                });
            };
            if query.entity != plan.entity
                || *arguments != plan.query
                || result.key != query.key
                || result.query_generation != query.query_generation
                || result.entity != query.entity
            {
                return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply {
                    key: batch_key,
                });
            }
            match &result.status {
                OracleV1ResultStatus::Unavailable { .. } => unavailable = true,
                OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::ExplosionEvidence(evidence),
                } if evidence.target_pose == plan.query.target_pose => {
                    targets.push(NativeHeExplosionTargetEvidence {
                        vehicle: plan.vehicle,
                        entity: plan.entity,
                        query: plan.query,
                        evidence: evidence.clone(),
                    });
                }
                _ => {
                    return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply {
                        key: batch_key,
                    });
                }
            }
        }

        let state = if unavailable {
            HeExplosionEvidenceState::Unavailable
        } else {
            HeExplosionEvidenceState::Applied
        };
        self.finish_he_explosion_evidence(key, state)?;
        if unavailable {
            return Ok(None);
        }
        if targets.len() != record.queries.len() {
            return Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceReply { key: batch_key });
        }
        Ok(Some(NativeHeExplosionEvidenceSample {
            key,
            projectile_id: record.intent.projectile_id,
            batch_key,
            issued_tick: record.request.issued_tick,
            apply_tick: record.request.apply_tick,
            targets,
        }))
    }

    fn release_timed_out_he_explosion_evidence(
        &mut self,
        key: HeExplosionEvidenceIntentKey,
        timed_out: &TimedOutOracleBatch,
    ) -> Result<(), AuthorityRuntimeError> {
        self.he_explosion_evidence_record(key, &timed_out.request)?;
        self.finish_he_explosion_evidence(key, HeExplosionEvidenceState::TimedOut)
    }

    fn finish_he_explosion_evidence(
        &mut self,
        key: HeExplosionEvidenceIntentKey,
        state: HeExplosionEvidenceState,
    ) -> Result<(), AuthorityRuntimeError> {
        if state == HeExplosionEvidenceState::Pending {
            return Err(AuthorityRuntimeError::MissingHeExplosionEvidencePlan { key });
        }
        let projectile_id = self
            .he_explosion_records
            .get(&key)
            .filter(|record| record.state == HeExplosionEvidenceState::Pending)
            .map(|record| record.intent.projectile_id.clone())
            .ok_or(AuthorityRuntimeError::MissingHeExplosionEvidencePlan { key })?;
        if self.pending_he_explosion_projectiles.get(&projectile_id) != Some(&key) {
            return Err(AuthorityRuntimeError::MissingHeExplosionEvidencePlan { key });
        }
        self.he_explosion_records
            .get_mut(&key)
            .expect("HE explosion record disappeared after validation")
            .state = state;
        self.pending_he_explosion_projectiles.remove(&projectile_id);
        self.he_explosion_terminal_bindings.remove(&key.plan_id);
        self.he_projectile_sources.remove(&projectile_id);

        self.he_explosion_terminal_history.push_back(key);
        while self.he_explosion_terminal_history.len() > HE_EXPLOSION_EVIDENCE_RETRY_HISTORY {
            if let Some(expired) = self.he_explosion_terminal_history.pop_front() {
                self.he_explosion_records.remove(&expired);
            }
        }
        Ok(())
    }

    fn validate_released_tick(
        &self,
        permit: &ReleasedAuthorityTick,
        input_tick: Tick,
    ) -> Result<(), AuthorityRuntimeError> {
        if permit.lineage != self.lineage {
            return Err(AuthorityRuntimeError::ReleasedTickLineageMismatch {
                active: self.lineage,
                received: permit.lineage,
            });
        }
        let current_tick = self.current_tick();
        if current_tick != permit.tick
            || self.released_tick != Some(permit.tick)
            || input_tick != permit.tick
        {
            return Err(AuthorityRuntimeError::ReleasedTickMismatch {
                current_tick,
                released_tick: self.released_tick,
                permit_tick: permit.tick,
                input_tick,
            });
        }
        Ok(())
    }

    fn validate_tick_input(&self, input: &AuthorityTickInput) -> Result<(), AuthorityRuntimeError> {
        for bot_id in self.bots.keys() {
            let order = input
                .orders
                .get(bot_id)
                .ok_or(AuthorityRuntimeError::MissingBotOrder { bot_id: *bot_id })?;
            if !valid_order(order) {
                return Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: *bot_id });
            }
            match (self.navigation_graph(), order.navigation_target) {
                (Some(_), None)
                | (
                    None,
                    Some(NavTarget {
                        controlled_shallow: true,
                        ..
                    }),
                ) => {
                    return Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: *bot_id });
                }
                (Some(graph), Some(target))
                    if target.controlled_shallow
                        && !graph.controlled_shallow_target_valid(
                            self.bots
                                .get(bot_id)
                                .expect("validated bot disappeared")
                                .state()
                                .position,
                            target,
                        ) =>
                {
                    return Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: *bot_id });
                }
                _ => {}
            }
            if let Some(target) = &order.target {
                let entity = simulation_entity(target.kind, target.network_id);
                if self.entities.get(entity).is_none() {
                    return Err(AuthorityRuntimeError::UndonatedOrderTarget {
                        bot_id: *bot_id,
                        target: entity,
                    });
                }
            }
        }
        if let Some(bot_id) = input
            .orders
            .keys()
            .find(|bot_id| !self.bots.contains_key(bot_id))
        {
            return Err(AuthorityRuntimeError::UnknownBotOrder { bot_id: *bot_id });
        }
        if let Some(bot_id) = input
            .world_pose_bots
            .iter()
            .find(|bot_id| !self.bots.contains_key(bot_id))
        {
            return Err(AuthorityRuntimeError::InvalidWorldPoseBot { bot_id: *bot_id });
        }
        let mut humans = BTreeSet::new();
        for body in &input.human_traffic {
            let player_id = body.network_id;
            if body.kind != TargetKind::Human
                || player_id == 0
                || !humans.insert(player_id)
                || !matches!(body.team, 1 | 2)
                || !finite_vec(body.position)
                || !finite_vec(body.velocity)
                || !body.yaw.is_finite()
                || !body.half_length.is_finite()
                || body.half_length <= 0.0
                || !body.half_width.is_finite()
                || body.half_width <= 0.0
                || self
                    .entities
                    .get(SimulationEntity::Human(player_id))
                    .is_none()
            {
                return Err(AuthorityRuntimeError::InvalidHumanTraffic { player_id });
            }
        }
        Ok(())
    }

    fn resolve_flight_targets(
        &self,
        requested: &[AuthorityProjectileTarget],
    ) -> Result<Vec<FlightTarget>, AuthorityRuntimeError> {
        let mut vehicles = BTreeSet::new();
        let mut result = Vec::with_capacity(requested.len());
        for target in requested {
            let logical = vehicle_entity(target.vehicle).ok_or(
                AuthorityRuntimeError::InvalidProjectileTarget {
                    vehicle: target.vehicle,
                },
            )?;
            let Some(entity) = self.entities.get(logical) else {
                return Err(AuthorityRuntimeError::InvalidProjectileTarget {
                    vehicle: target.vehicle,
                });
            };
            if !vehicles.insert(target.vehicle) {
                return Err(AuthorityRuntimeError::InvalidProjectileTarget {
                    vehicle: target.vehicle,
                });
            }
            result.push(FlightTarget {
                vehicle: target.vehicle,
                entity,
                wreck: target.wreck,
            });
        }
        result.sort_by_key(|target| target.vehicle);
        Ok(result)
    }

    fn traffic_snapshot(&self, humans: &[TrafficBody]) -> Vec<TrafficBody> {
        let mut result = Vec::with_capacity(self.bots.len() + humans.len());
        for bot in self.bots.values() {
            let state = bot.state();
            result.push(TrafficBody {
                network_id: state.id,
                kind: TargetKind::Bot,
                team: state.team,
                position: state.position,
                velocity: bot.ram_velocity(),
                yaw: state.yaw,
                half_length: bot.descriptor().half_length,
                half_width: bot.descriptor().half_width,
            });
        }
        result.extend_from_slice(humans);
        result.sort_by_key(|body| {
            (
                match body.kind {
                    TargetKind::Bot => 0u8,
                    TargetKind::Human => 1u8,
                },
                body.network_id,
            )
        });
        result
    }
}

fn validate_planner_inputs(
    bots: &BTreeMap<u32, BotSimulator>,
    input: &PlannerBuildInput<'_>,
) -> Result<PlannerWorld, AuthorityRuntimeError> {
    let manifest = input
        .manifest
        .as_array()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: "manifest" })?;
    let mut manifest_ids = BTreeSet::new();
    for value in manifest {
        let raw = input_object(value, "manifest.entry")?;
        let id = input_u32(raw, "id", "manifest.id")?;
        let team = input_team(raw, "manifest.team")?;
        let slot = input_u64(raw, "slot", "manifest.slot")?;
        let health = input_u64(raw, "health", "manifest.health")?;
        let bot = bots
            .get(&id)
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                field: "manifest.unknown_bot",
            })?;
        if !manifest_ids.insert(id) || slot >= 15 || health > 100_000 || team != bot.state().team {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "manifest.identity",
            });
        }
        validate_profile(raw.get("profile").ok_or(
            AuthorityRuntimeError::InvalidPlannerInput {
                field: "manifest.profile",
            },
        )?)?;
        validate_route(
            raw.get("route")
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "manifest.route",
                })?,
        )?;
    }
    if manifest_ids != bots.keys().copied().collect() {
        return Err(AuthorityRuntimeError::InvalidPlannerInput {
            field: "manifest.bot_set",
        });
    }

    let states = input
        .bot_states
        .as_array()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
            field: "bot_states",
        })?;
    let mut parsed_bots = BTreeMap::new();
    for value in states {
        let raw = input_object(value, "bot_states.entry")?;
        let id = input_u32(raw, "id", "bot_states.id")?;
        let bot = bots
            .get(&id)
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                field: "bot_states.unknown_bot",
            })?;
        let state = parse_vehicle_state(raw, Some(bot.state().speed), "bot_states")?;
        let shell_index = input_u64(raw, "shell_index", "bot_states.shell_index")?;
        if state.team != bot.state().team
            || state.alive != bot.state().alive
            || state.health != bot.state().health
            || shell_index >= bot.descriptor().gun.shells.len() as u64
            || !raw.get("world_pose").is_some_and(Value::is_boolean)
            || !raw.get("critical").is_some_and(Value::is_object)
            || parsed_bots.insert(id, state).is_some()
        {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "bot_states.identity",
            });
        }
        if let Some(ammo) = raw.get("ammo_remaining") {
            let ammo = ammo
                .as_array()
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "bot_states.ammo_remaining",
                })?;
            if ammo.len() > 10 || ammo.iter().any(|value| value.as_u64().is_none()) {
                return Err(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "bot_states.ammo_remaining",
                });
            }
        }
    }
    if parsed_bots.keys().copied().collect::<BTreeSet<_>>() != manifest_ids {
        return Err(AuthorityRuntimeError::InvalidPlannerInput {
            field: "bot_states.bot_set",
        });
    }

    let players = input
        .players
        .as_array()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: "players" })?;
    let mut humans = BTreeMap::new();
    for value in players {
        let raw = input_object(value, "players.entry")?;
        let id = input_u32(raw, "id", "players.id")?;
        let state = parse_vehicle_state(raw, None, "players")?;
        if humans.insert(id, state).is_some() {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "players.duplicate",
            });
        }
    }
    Ok(PlannerWorld {
        bots: parsed_bots,
        humans,
    })
}

fn parse_vehicle_state(
    raw: &Map<String, Value>,
    fallback_speed: Option<f64>,
    prefix: &'static str,
) -> Result<PlannerVehicleState, AuthorityRuntimeError> {
    let team = input_team(raw, prefix)?;
    let alive = input_bool(raw, "alive", prefix)?;
    let health = input_u64(raw, "health", prefix).and_then(|value| {
        u32::try_from(value)
            .map_err(|_| AuthorityRuntimeError::InvalidPlannerInput { field: prefix })
    })?;
    let max_health = input_u32(raw, "max_health", prefix)?;
    let position = Vec3::new(
        input_f64(raw, "x", prefix)?,
        input_f64(raw, "y", prefix)?,
        input_f64(raw, "z", prefix)?,
    );
    let yaw = input_f64(raw, "yaw", prefix)?;
    let speed = match raw.get("speed") {
        Some(value) => value
            .as_f64()
            .filter(|value| value.is_finite())
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: prefix })?,
        None => {
            fallback_speed.ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: prefix })?
        }
    };
    if max_health == 0
        || health > max_health
        || alive != (health > 0)
        || !valid_world_position(position)
        || !yaw.is_finite()
        || yaw.abs() > PI * 4.0
        || !speed.is_finite()
        || speed.abs() > 200.0
    {
        return Err(AuthorityRuntimeError::InvalidPlannerInput { field: prefix });
    }
    Ok(PlannerVehicleState {
        team,
        alive,
        health,
        position,
        velocity: Vec3::new(yaw.sin() * speed, 0.0, yaw.cos() * speed),
        yaw: wrapped(yaw),
        speed,
    })
}

fn validate_profile(value: &Value) -> Result<(), AuthorityRuntimeError> {
    let error = || AuthorityRuntimeError::InvalidPlannerInput {
        field: "manifest.profile",
    };
    let raw = value.as_object().ok_or_else(error)?;
    for field in ["class_tag", "dominant_role"] {
        if !raw
            .get(field)
            .and_then(Value::as_str)
            .is_some_and(|value| !value.is_empty() && value.len() <= 32)
        {
            return Err(error());
        }
    }
    for field in ["speed", "desired_range", "fire_range", "armor"] {
        if !raw
            .get(field)
            .and_then(Value::as_f64)
            .is_some_and(|value| value.is_finite() && value >= 0.0)
        {
            return Err(error());
        }
    }
    let roles = raw
        .get("roles")
        .and_then(Value::as_object)
        .ok_or_else(error)?;
    if roles.len() > 16
        || roles.iter().any(|(name, value)| {
            name.is_empty()
                || name.len() > 32
                || !value
                    .as_f64()
                    .is_some_and(|value| value.is_finite() && value >= 0.0)
        })
    {
        return Err(error());
    }
    let shells = raw
        .get("shells")
        .and_then(Value::as_array)
        .ok_or_else(error)?;
    if shells.len() > 10 {
        return Err(error());
    }
    for (expected_index, value) in shells.iter().enumerate() {
        let shell = value.as_object().ok_or_else(error)?;
        if shell.get("index").and_then(Value::as_u64) != Some(expected_index as u64)
            || !shell
                .get("kind")
                .and_then(Value::as_str)
                .is_some_and(|value| !value.is_empty() && value.len() <= 48)
            || ["penetration", "damage", "speed"].into_iter().any(|field| {
                !shell
                    .get(field)
                    .and_then(Value::as_f64)
                    .is_some_and(|value| value.is_finite() && value >= 0.0)
            })
        {
            return Err(error());
        }
    }
    Ok(())
}

fn validate_route(value: &Value) -> Result<(), AuthorityRuntimeError> {
    let raw = value
        .as_object()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
            field: "manifest.route",
        })?;
    if !raw
        .get("id")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.is_empty() && value.len() <= 64)
    {
        return Err(AuthorityRuntimeError::InvalidPlannerInput {
            field: "manifest.route.id",
        });
    }
    let waypoints = raw.get("waypoints").and_then(Value::as_array).ok_or(
        AuthorityRuntimeError::InvalidPlannerInput {
            field: "manifest.route.waypoints",
        },
    )?;
    if waypoints.is_empty() || waypoints.len() > 32 {
        return Err(AuthorityRuntimeError::InvalidPlannerInput {
            field: "manifest.route.waypoints",
        });
    }
    for waypoint in waypoints {
        parse_input_point(waypoint, "manifest.route.waypoint")?;
        if waypoint
            .get("hold")
            .is_some_and(|value| !value.is_boolean())
        {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "manifest.route.waypoint.hold",
            });
        }
    }
    Ok(())
}

fn validate_contacts(
    value: &Value,
    world: &PlannerWorld,
    bots: &BTreeMap<u32, BotSimulator>,
) -> Result<(), AuthorityRuntimeError> {
    let contacts = value
        .as_array()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: "contacts" })?;
    if contacts.len() > MAX_CONTACTS_PER_TEAM * 2 {
        return Err(AuthorityRuntimeError::InvalidPlannerInput { field: "contacts" });
    }
    let mut seen = BTreeSet::new();
    for value in contacts {
        let raw = input_object(value, "contacts.entry")?;
        const ALLOWED: &[&str] = &[
            "observing_team",
            "target_kind",
            "target_id",
            "target_team",
            "visible",
            "shootable_by_bot_ids",
            "x",
            "y",
            "z",
            "health",
            "max_health",
            "class_tag",
            "armor",
            "speed",
        ];
        if raw.keys().any(|key| !ALLOWED.contains(&key.as_str())) {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "contacts.unknown_field",
            });
        }
        let observing_team = input_team_key(raw, "observing_team", "contacts.team")?;
        let target_id = input_u32(raw, "target_id", "contacts.target_id")?;
        let target_kind = input_target_kind(raw, "target_kind", "contacts.target_kind")?;
        let target = match target_kind {
            TargetKind::Bot => world.bots.get(&target_id),
            TargetKind::Human => world.humans.get(&target_id),
        }
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
            field: "contacts.unknown_target",
        })?;
        let target_team = input_team_key(raw, "target_team", "contacts.target_team")?;
        let visible = input_bool(raw, "visible", "contacts.visible")?;
        let shooters = raw
            .get("shootable_by_bot_ids")
            .and_then(Value::as_array)
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                field: "contacts.shootable_by_bot_ids",
            })?;
        let mut shooter_ids = BTreeSet::new();
        for shooter in shooters {
            let id = shooter
                .as_u64()
                .and_then(|id| u32::try_from(id).ok())
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "contacts.shootable_by_bot_ids",
                })?;
            if !shooter_ids.insert(id)
                || !bots
                    .get(&id)
                    .is_some_and(|bot| bot.state().team == observing_team)
            {
                return Err(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "contacts.shootable_by_bot_ids",
                });
            }
        }
        if target.team != target_team
            || target_team == observing_team
            || !seen.insert((observing_team, target_kind_key(target_kind), target_id))
        {
            return Err(AuthorityRuntimeError::InvalidPlannerInput {
                field: "contacts.identity",
            });
        }
        if visible {
            let point = Vec3::new(
                input_f64(raw, "x", "contacts.position")?,
                input_f64(raw, "y", "contacts.position")?,
                input_f64(raw, "z", "contacts.position")?,
            );
            let health = input_u32(raw, "health", "contacts.health")?;
            let max_health = input_u32(raw, "max_health", "contacts.max_health")?;
            if !valid_world_position(point) || max_health == 0 || health > max_health {
                return Err(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "contacts.state",
                });
            }
        }
    }
    Ok(())
}

fn validate_defense(value: &Value) -> Result<(), AuthorityRuntimeError> {
    let raw = value
        .as_object()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field: "defense" })?;
    if raw
        .keys()
        .any(|key| !matches!(key.as_str(), "bases" | "states" | "contributors"))
    {
        return Err(AuthorityRuntimeError::InvalidPlannerInput {
            field: "defense.unknown_field",
        });
    }
    if let Some(bases) = raw.get("bases") {
        let bases = bases
            .as_object()
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                field: "defense.bases",
            })?;
        validate_team_keys(bases, "defense.bases")?;
        for values in bases.values() {
            let values = values
                .as_array()
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "defense.bases",
                })?;
            if values.len() > 4 {
                return Err(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "defense.bases",
                });
            }
            for point in values {
                parse_input_point(point, "defense.base")?;
            }
        }
    }
    if let Some(states) = raw.get("states") {
        let states = states
            .as_object()
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                field: "defense.states",
            })?;
        validate_team_keys(states, "defense.states")?;
        for state in states.values() {
            let state = input_object(state, "defense.state")?;
            if !state
                .get("time_left")
                .and_then(Value::as_f64)
                .is_some_and(|value| value.is_finite() && value >= 0.0)
                || !state
                    .get("invaders")
                    .and_then(Value::as_u64)
                    .is_some_and(|value| value <= 15)
            {
                return Err(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "defense.state",
                });
            }
        }
    }
    if let Some(contributors) = raw.get("contributors") {
        let contributors =
            contributors
                .as_object()
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "defense.contributors",
                })?;
        validate_team_keys(contributors, "defense.contributors")?;
        for values in contributors.values() {
            let values = values
                .as_array()
                .ok_or(AuthorityRuntimeError::InvalidPlannerInput {
                    field: "defense.contributors",
                })?;
            for value in values {
                let value = input_object(value, "defense.contributor")?;
                input_target_kind(value, "kind", "defense.contributor.kind")?;
                input_u32(value, "id", "defense.contributor.id")?;
            }
        }
    }
    Ok(())
}

fn parse_planner_payload(
    payload: &Value,
    bots: &BTreeMap<u32, BotSimulator>,
    world: &PlannerWorld,
    now: f64,
    drivers: &mut BTreeMap<u32, PlannerDriveState>,
    mut navigation: Option<&mut NavRouter>,
) -> Result<TypedPlannerOrders, AuthorityRuntimeError> {
    let root = payload
        .as_object()
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id: 0,
            field: "root",
        })?;
    if root.len() != 2 || !root.contains_key("revision") || !root.contains_key("orders") {
        return Err(AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id: 0,
            field: "root",
        });
    }
    let revision = root.get("revision").and_then(Value::as_u64).ok_or(
        AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id: 0,
            field: "revision",
        },
    )?;
    let raw_orders = root.get("orders").and_then(Value::as_array).ok_or(
        AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id: 0,
            field: "orders",
        },
    )?;
    let mut orders = BTreeMap::new();
    let mut traces = BTreeMap::new();
    for raw in raw_orders {
        let object = raw
            .as_object()
            .ok_or(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id: 0,
                field: "order",
            })?;
        let bot_id = output_u32(object, "id", 0, "id")?;
        let bot = bots
            .get(&bot_id)
            .ok_or(AuthorityRuntimeError::UnknownPlannerOrder { bot_id })?;
        if orders.contains_key(&bot_id) {
            return Err(AuthorityRuntimeError::DuplicatePlannerOrder { bot_id });
        }
        if !world.bots.get(&bot_id).is_some_and(|state| state.alive) {
            // A bot may die between 1 Hz strategy refreshes. Ignore its cached
            // tactical order; the canonical dead hold is inserted below.
            continue;
        }
        validate_output_keys(object, bot_id)?;
        let team = output_u64(object, "team", bot_id, "team")?;
        if team != u64::from(bot.state().team) {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "team",
            });
        }
        let target_id = match object.get("target_id") {
            Some(Value::Null) => None,
            Some(value) => Some(value.as_u64().and_then(|id| u32::try_from(id).ok()).ok_or(
                AuthorityRuntimeError::InvalidPlannerOutput {
                    bot_id,
                    field: "target_id",
                },
            )?),
            None => {
                return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                    bot_id,
                    field: "target_id",
                })
            }
        };
        let target_kind = match (target_id, object.get("target_kind")) {
            (None, None) => None,
            (Some(_), Some(Value::String(value))) => Some(match value.as_str() {
                "human" | "player" => TargetKind::Human,
                "bot" => TargetKind::Bot,
                _ => {
                    return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                        bot_id,
                        field: "target_kind",
                    })
                }
            }),
            _ => {
                return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                    bot_id,
                    field: "target_kind",
                })
            }
        };
        let target = match target_id.zip(target_kind) {
            None => None,
            Some((id, kind)) => {
                let logical = simulation_entity(kind, id);
                let state = match kind {
                    TargetKind::Bot => world.bots.get(&id),
                    TargetKind::Human => world.humans.get(&id),
                }
                .ok_or(AuthorityRuntimeError::UnknownPlannerTarget {
                    bot_id,
                    target: logical,
                })?;
                if state.team == bot.state().team || (kind == TargetKind::Bot && id == bot_id) {
                    return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                        bot_id,
                        field: "target",
                    });
                }
                Some(TargetState {
                    network_id: id,
                    kind,
                    team: state.team,
                    alive: state.alive,
                    health: state.health,
                    position: state.position,
                    velocity: state.velocity,
                    yaw: state.yaw,
                    speed: state.speed,
                })
            }
        };
        let aim_position = output_optional_point(object, "aim_position", bot_id)?;
        let face_position = output_optional_point(object, "face_position", bot_id)?;
        let move_position = output_point(object, "move_position", bot_id)?;
        let requested_fire = output_bool(object, "fire_allowed", bot_id)?;
        if requested_fire && target.is_none() {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "fire_allowed",
            });
        }
        let fire_allowed = requested_fire && target.as_ref().is_some_and(|target| target.alive);
        let mode_text = output_string(object, "combat_mode", bot_id)?;
        let combat_mode = parse_combat_mode(bot_id, mode_text)?;
        let throttle_override = output_optional_f64(object, "throttle_override", bot_id)?;
        if throttle_override.is_some_and(|value| !(-1.0..=1.0).contains(&value)) {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "throttle_override",
            });
        }
        let desired_range = output_f64(object, "desired_range", bot_id)?;
        let fire_range = output_f64(object, "fire_range", bot_id)?;
        if desired_range < 0.0 || fire_range < desired_range || fire_range > 2_500.0 {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "ranges",
            });
        }
        let route_id = output_string(object, "route_id", bot_id)?.to_owned();
        if route_id.is_empty() || route_id.len() > 64 {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "route_id",
            });
        }
        let route_index = output_u64(object, "route_index", bot_id, "route_index")?;
        if route_index > 15 {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "route_index",
            });
        }
        let route_anchor = output_point(object, "route_anchor", bot_id)?;
        let route_join = output_bool(object, "route_join", bot_id)?;
        validate_personality(
            object
                .get("personality")
                .ok_or(AuthorityRuntimeError::InvalidPlannerOutput {
                    bot_id,
                    field: "personality",
                })?,
            bot_id,
        )?;
        validate_output_profile(
            object
                .get("profile")
                .ok_or(AuthorityRuntimeError::InvalidPlannerOutput {
                    bot_id,
                    field: "profile",
                })?,
            bot_id,
        )?;
        let shell_index = output_u64(object, "shell_index", bot_id, "shell_index")?;
        if shell_index >= bot.descriptor().gun.shells.len() as u64 || shell_index > 9 {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "shell_index",
            });
        }
        for field in ["cover_id", "defense_base_id"] {
            if object.get(field).is_some_and(|value| {
                !value
                    .as_str()
                    .is_some_and(|value| !value.is_empty() && value.len() <= 96)
            }) {
                return Err(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field });
            }
        }
        if output_optional_f64(object, "hull_angle_degrees", bot_id)?
            .is_some_and(|value| !(-45.0..=45.0).contains(&value))
        {
            return Err(AuthorityRuntimeError::InvalidPlannerOutput {
                bot_id,
                field: "hull_angle_degrees",
            });
        }
        let trace = PlannerOrderTrace {
            move_position,
            aim_position,
            face_position,
            combat_mode: mode_text.to_owned(),
            throttle_override,
            desired_range,
            route_id,
            route_index: route_index as usize,
            route_anchor,
            route_join,
        };
        let current = world
            .bots
            .get(&bot_id)
            .expect("live planner bot was validated above");
        let strategic_move_target =
            if route_join && current.position.horizontal_distance(route_anchor) > 1.5 {
                route_anchor
            } else {
                move_position
            };
        let navigation_target = navigation.as_deref_mut().map(|router| {
            router.next_target(
                bot_id,
                current.position,
                strategic_move_target,
                &trace.route_id,
                trace.route_index,
            )
        });
        let (throttle, turn, target_yaw, recovery_mode, resolved_aim) =
            derive_local_order(bot_id, current, &trace, navigation_target, now, drivers);
        orders.insert(
            bot_id,
            BotOrder {
                throttle,
                turn,
                target_yaw: Some(target_yaw),
                aim_position: Some(resolved_aim),
                target,
                fire_allowed,
                fire_range,
                requested_shell_index: shell_index as usize,
                recovery_mode,
                combat_mode,
                navigation_target,
            },
        );
        traces.insert(bot_id, trace);
    }

    for (bot_id, bot) in bots {
        let state = world.bots.get(bot_id).expect("input bot set was validated");
        if state.alive {
            if !orders.contains_key(bot_id) {
                return Err(AuthorityRuntimeError::MissingPlannerOrder { bot_id: *bot_id });
            }
        } else {
            let position = state.position;
            orders.insert(
                *bot_id,
                BotOrder {
                    throttle: 0.0,
                    turn: 0.0,
                    target_yaw: Some(state.yaw),
                    aim_position: Some(position),
                    target: None,
                    fire_allowed: false,
                    fire_range: 0.0,
                    requested_shell_index: bot.state().ammo.loaded(),
                    recovery_mode: RecoveryMode::Drive,
                    combat_mode: CombatMode::Hold,
                    navigation_target: Some(NavTarget {
                        point: position,
                        controlled_shallow: false,
                    }),
                },
            );
            traces.insert(
                *bot_id,
                PlannerOrderTrace {
                    move_position: position,
                    aim_position: Some(position),
                    face_position: Some(position),
                    combat_mode: "hold".to_owned(),
                    throttle_override: Some(0.0),
                    desired_range: 0.0,
                    route_id: format!("retired-{bot_id}"),
                    route_index: 0,
                    route_anchor: position,
                    route_join: false,
                },
            );
        }
    }
    drivers.retain(|bot_id, _| bots.contains_key(bot_id));
    if let Some(router) = navigation {
        router.retain_bots(|bot_id| bots.contains_key(&bot_id));
    }
    Ok(TypedPlannerOrders {
        revision,
        orders,
        traces,
    })
}

fn derive_local_order(
    bot_id: u32,
    current: &PlannerVehicleState,
    trace: &PlannerOrderTrace,
    navigation_target: Option<NavTarget>,
    now: f64,
    drivers: &mut BTreeMap<u32, PlannerDriveState>,
) -> (f64, f64, f64, RecoveryMode, Vec3) {
    let (move_target, controlled_shallow) = navigation_target
        .map(|target| (target.point, target.controlled_shallow))
        .unwrap_or_else(|| {
            (
                if trace.route_join
                    && current.position.horizontal_distance(trace.route_anchor) > 1.5
                {
                    trace.route_anchor
                } else {
                    trace.move_position
                },
                false,
            )
        });
    let resolved_aim = trace.aim_position.unwrap_or(move_target);
    let face_target = trace.face_position.unwrap_or(move_target);
    let state = drivers.entry(bot_id).or_insert_with(|| PlannerDriveState {
        last_position: current.position,
        last_yaw: None,
        last_now: now,
        stuck_seconds: 0.0,
        recovery_until: 0.0,
        recovery_count: 0,
        phase: identity_phase(bot_id),
    });
    let dt = (now - state.last_now).clamp(0.0, 0.35);
    let displacement = current.position.horizontal_distance(state.last_position);
    let yaw_rate = state
        .last_yaw
        .map(|last| angle_delta(current.yaw, last).abs() / dt.max(1.0e-6))
        .unwrap_or(0.0);
    state.last_position = current.position;
    state.last_yaw = Some(current.yaw);
    state.last_now = now;

    let movement_intent = trace.throttle_override.is_none_or(|value| value > 0.0);
    let distance = current.position.horizontal_distance(move_target);
    if !current.alive || !movement_intent || distance <= 1.5 {
        state.stuck_seconds = 0.0;
        state.recovery_until = 0.0;
        let target_yaw = yaw_to(current.position, face_target).unwrap_or(current.yaw);
        let turn = (angle_delta(target_yaw, current.yaw) / 0.58).clamp(-1.0, 1.0);
        return (0.0, turn, target_yaw, RecoveryMode::Drive, resolved_aim);
    }

    if displacement < 0.08 && current.speed.abs() < 0.35 && yaw_rate < 0.25 {
        state.stuck_seconds += dt;
    } else {
        state.stuck_seconds = (state.stuck_seconds - dt * 2.0).max(0.0);
    }
    let threshold = 1.8 + state.phase * 0.42;
    if state.recovery_until <= now && state.stuck_seconds >= threshold {
        state.recovery_until = now + 0.85 + state.phase * 0.28;
        state.stuck_seconds = 0.0;
        state.recovery_count = state.recovery_count.saturating_add(1);
    }
    if state.recovery_until > now {
        // Without a native rear-clearance result the runtime must not invent a
        // safe reverse. A bounded pivot is the fail-closed recovery action.
        let direction = if (state.recovery_count + (state.phase * 10.0) as u64) % 2 == 0 {
            -1.0
        } else {
            1.0
        };
        let target_yaw = wrapped(current.yaw + direction * 0.85);
        return (
            0.0,
            direction,
            target_yaw,
            RecoveryMode::PivotRecovery,
            resolved_aim,
        );
    }

    let target_yaw = yaw_to(current.position, move_target).unwrap_or(current.yaw);
    let delta = angle_delta(target_yaw, current.yaw);
    let turn = (delta / 0.58).clamp(-1.0, 1.0);
    let climb_grade = (move_target.y - current.position.y) / distance.max(0.1);
    let mut throttle = if (climb_grade > 0.10 && delta.abs() > 0.30)
        || delta.abs() > FRAC_PI_2
        || (controlled_shallow && delta.abs() > 0.20)
    {
        0.0
    } else {
        1.0
    };
    if delta.abs() < 0.65 {
        if let Some(override_value) = trace.throttle_override {
            throttle = override_value;
        }
    }
    (
        throttle,
        turn,
        target_yaw,
        RecoveryMode::Drive,
        resolved_aim,
    )
}

fn parse_combat_mode(bot_id: u32, value: &str) -> Result<CombatMode, AuthorityRuntimeError> {
    match value {
        "route" | "artillery_deploy" => Ok(CombatMode::Route),
        "engage" | "advance_contact" | "flank" | "cover_peek" => Ok(CombatMode::Engage),
        "base_defense" => Ok(CombatMode::BaseDefense),
        "support_hold" | "cover_hold" | "artillery_hold" | "hold" => Ok(CombatMode::Hold),
        "withdraw"
        | "low_health_retreat"
        | "under_fire_withdraw"
        | "crossfire_withdraw"
        | "take_cover"
        | "cover_return" => Ok(CombatMode::Retreat),
        _ => Err(AuthorityRuntimeError::UnknownPlannerCombatMode {
            bot_id,
            mode: value.to_owned(),
        }),
    }
}

fn validate_output_keys(
    object: &Map<String, Value>,
    bot_id: u32,
) -> Result<(), AuthorityRuntimeError> {
    const ALLOWED: &[&str] = &[
        "id",
        "team",
        "target_id",
        "target_kind",
        "aim_position",
        "face_position",
        "move_position",
        "fire_allowed",
        "combat_mode",
        "throttle_override",
        "desired_range",
        "fire_range",
        "route_id",
        "route_index",
        "route_anchor",
        "route_join",
        "personality",
        "profile",
        "shell_index",
        "cover_id",
        "defense_base_id",
        "hull_angle_degrees",
    ];
    if object.keys().any(|key| !ALLOWED.contains(&key.as_str())) {
        return Err(AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id,
            field: "unknown_field",
        });
    }
    Ok(())
}

fn validate_personality(value: &Value, bot_id: u32) -> Result<(), AuthorityRuntimeError> {
    const FIELDS: &[&str] = &[
        "aggression",
        "caution",
        "teamwork",
        "patience",
        "initiative",
        "adaptability",
        "jiggle",
    ];
    let raw = value
        .as_object()
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id,
            field: "personality",
        })?;
    if raw.len() != FIELDS.len()
        || raw.keys().any(|key| !FIELDS.contains(&key.as_str()))
        || FIELDS.iter().any(|field| {
            !raw.get(*field)
                .and_then(Value::as_f64)
                .is_some_and(|value| value.is_finite() && (0.0..=1.0).contains(&value))
        })
    {
        return Err(AuthorityRuntimeError::InvalidPlannerOutput {
            bot_id,
            field: "personality",
        });
    }
    Ok(())
}

fn validate_output_profile(value: &Value, bot_id: u32) -> Result<(), AuthorityRuntimeError> {
    validate_profile(value).map_err(|_| AuthorityRuntimeError::InvalidPlannerOutput {
        bot_id,
        field: "profile",
    })
}

fn output_point(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<Vec3, AuthorityRuntimeError> {
    let raw = object
        .get(key)
        .and_then(Value::as_object)
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key })?;
    if raw.len() != 3
        || raw
            .keys()
            .any(|key| !matches!(key.as_str(), "x" | "y" | "z"))
    {
        return Err(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key });
    }
    let point = Vec3::new(
        output_f64(raw, "x", bot_id)?,
        output_f64(raw, "y", bot_id)?,
        output_f64(raw, "z", bot_id)?,
    );
    if !valid_world_position(point) {
        return Err(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key });
    }
    Ok(point)
}

fn output_optional_point(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<Option<Vec3>, AuthorityRuntimeError> {
    match object.get(key) {
        Some(Value::Null) => Ok(None),
        Some(_) => output_point(object, key, bot_id).map(Some),
        None => Err(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key }),
    }
}

fn output_u32(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
    field: &'static str,
) -> Result<u32, AuthorityRuntimeError> {
    output_u64(object, key, bot_id, field).and_then(|value| {
        u32::try_from(value)
            .ok()
            .filter(|value| *value != 0)
            .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field })
    })
}

fn output_u64(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
    field: &'static str,
) -> Result<u64, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field })
}

fn output_f64(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<f64, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key })
}

fn output_optional_f64(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<Option<f64>, AuthorityRuntimeError> {
    match object.get(key) {
        None if matches!(key, "hull_angle_degrees") => Ok(None),
        Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_f64()
            .filter(|value| value.is_finite())
            .map(Some)
            .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key }),
        None => Err(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key }),
    }
}

fn output_bool(
    object: &Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<bool, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_bool)
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key })
}

fn output_string<'a>(
    object: &'a Map<String, Value>,
    key: &'static str,
    bot_id: u32,
) -> Result<&'a str, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or(AuthorityRuntimeError::InvalidPlannerOutput { bot_id, field: key })
}

fn input_object<'a>(
    value: &'a Value,
    field: &'static str,
) -> Result<&'a Map<String, Value>, AuthorityRuntimeError> {
    value
        .as_object()
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field })
}

fn input_u64(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<u64, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field })
}

fn input_u32(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<u32, AuthorityRuntimeError> {
    input_u64(object, key, field).and_then(|value| {
        u32::try_from(value)
            .ok()
            .filter(|value| *value != 0)
            .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field })
    })
}

fn input_f64(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<f64, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field })
}

fn input_bool(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<bool, AuthorityRuntimeError> {
    object
        .get(key)
        .and_then(Value::as_bool)
        .ok_or(AuthorityRuntimeError::InvalidPlannerInput { field })
}

fn input_team(
    object: &Map<String, Value>,
    field: &'static str,
) -> Result<u8, AuthorityRuntimeError> {
    input_team_key(object, "team", field)
}

fn input_team_key(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<u8, AuthorityRuntimeError> {
    match input_u64(object, key, field)? {
        1 => Ok(1),
        2 => Ok(2),
        _ => Err(AuthorityRuntimeError::InvalidPlannerInput { field }),
    }
}

fn input_target_kind(
    object: &Map<String, Value>,
    key: &'static str,
    field: &'static str,
) -> Result<TargetKind, AuthorityRuntimeError> {
    match object.get(key).and_then(Value::as_str) {
        Some("human" | "player") => Ok(TargetKind::Human),
        Some("bot") => Ok(TargetKind::Bot),
        _ => Err(AuthorityRuntimeError::InvalidPlannerInput { field }),
    }
}

fn parse_input_point(value: &Value, field: &'static str) -> Result<Vec3, AuthorityRuntimeError> {
    let raw = input_object(value, field)?;
    let point = Vec3::new(
        input_f64(raw, "x", field)?,
        input_f64(raw, "y", field)?,
        input_f64(raw, "z", field)?,
    );
    if valid_world_position(point) {
        Ok(point)
    } else {
        Err(AuthorityRuntimeError::InvalidPlannerInput { field })
    }
}

fn validate_team_keys(
    value: &Map<String, Value>,
    field: &'static str,
) -> Result<(), AuthorityRuntimeError> {
    if value.keys().any(|key| !matches!(key.as_str(), "1" | "2")) {
        Err(AuthorityRuntimeError::InvalidPlannerInput { field })
    } else {
        Ok(())
    }
}

fn target_kind_key(kind: TargetKind) -> u8 {
    match kind {
        TargetKind::Bot => 0,
        TargetKind::Human => 1,
    }
}

fn valid_world_position(value: Vec3) -> bool {
    finite_vec(value)
        && value.x.abs() <= 2_000.0
        && (-1_000.0..=1_000.0).contains(&value.y)
        && value.z.abs() <= 2_000.0
}

fn yaw_to(from: Vec3, to: Vec3) -> Option<f64> {
    let dx = to.x - from.x;
    let dz = to.z - from.z;
    (dx.hypot(dz) > 0.1).then(|| dx.atan2(dz))
}

fn identity_phase(bot_id: u32) -> f64 {
    let mut value = 0u32;
    for byte in bot_id.to_string().bytes() {
        value = value.wrapping_mul(33).wrapping_add(u32::from(byte)) & 0x7fff_ffff;
    }
    f64::from(value % 997) / 997.0
}

fn partition_bot_events(
    bot_id: u32,
    events: Vec<BotEvent>,
) -> Result<AuthorityBotTick, AuthorityRuntimeError> {
    let mut pose = None;
    let mut environment = Vec::new();
    let mut launches = Vec::new();
    for event in events {
        match event {
            BotEvent::Pose(value) if pose.is_none() => pose = Some(value),
            BotEvent::Pose(_) => return Err(AuthorityRuntimeError::InvalidBotOutput { bot_id }),
            BotEvent::Combat(value) => environment.push(value),
            BotEvent::Projectile(value) => launches.push(value),
        }
    }
    let pose = pose.ok_or(AuthorityRuntimeError::InvalidBotOutput { bot_id })?;
    Ok(AuthorityBotTick {
        bot_id,
        pose,
        environment,
        launches,
    })
}

fn validate_native_ref(entity: EntityRef) -> Result<(), ()> {
    if entity.entity_id <= 0 || entity.generation == 0 {
        Err(())
    } else {
        Ok(())
    }
}

fn simulation_entity(kind: TargetKind, id: u32) -> SimulationEntity {
    match kind {
        TargetKind::Bot => SimulationEntity::Bot(id),
        TargetKind::Human => SimulationEntity::Human(id),
    }
}

fn target_vehicle(kind: TargetKind, id: u32) -> VehicleKey {
    VehicleKey {
        kind: match kind {
            TargetKind::Bot => VehicleKind::Bot,
            TargetKind::Human => VehicleKind::Player,
        },
        id: u64::from(id),
    }
}

fn vehicle_entity(vehicle: VehicleKey) -> Option<SimulationEntity> {
    let id = u32::try_from(vehicle.id).ok().filter(|id| *id != 0)?;
    Some(match vehicle.kind {
        VehicleKind::Bot => SimulationEntity::Bot(id),
        VehicleKind::Player => SimulationEntity::Human(id),
    })
}

fn oracle_vec3(value: Vec3) -> OracleVec3 {
    OracleVec3 {
        x: value.x as f32,
        y: value.y as f32,
        z: value.z as f32,
    }
}

fn oracle_projectile_vec3(value: ProjectileVec3) -> OracleVec3 {
    OracleVec3 {
        x: value.x as f32,
        y: value.y as f32,
        z: value.z as f32,
    }
}

fn he_projectile_source_binding(record: &ProjectileRecord) -> Option<HeProjectileSourceBinding> {
    if !record.launch.is_he || record.launch.source_shot.shell.kind != "HIGH_EXPLOSIVE" {
        return None;
    }
    Some(HeProjectileSourceBinding {
        caliber_mm: record.launch.source_shot.shell.caliber,
        gravity: record.launch.gravity,
        segment_velocity: record.segment_velocity,
        segment_start_time_ms: record.segment_start_time_ms,
    })
}

fn he_incoming_direction(
    source: HeProjectileSourceBinding,
    resolved_time_ms: u64,
) -> Option<ProjectileVec3> {
    let elapsed_ms = resolved_time_ms.checked_sub(source.segment_start_time_ms)?;
    let elapsed_seconds = elapsed_ms as f64 / 1_000.0;
    let velocity = ProjectileVec3 {
        x: source.segment_velocity.x,
        y: source.segment_velocity.y - source.gravity * elapsed_seconds,
        z: source.segment_velocity.z,
    };
    let magnitude = velocity.magnitude();
    if !magnitude.is_finite() || magnitude <= 0.0 {
        return None;
    }
    Some(ProjectileVec3 {
        x: velocity.x / magnitude,
        y: velocity.y / magnitude,
        z: velocity.z / magnitude,
    })
}

fn valid_he_explosion_evidence_intent(intent: &HeExplosionEvidenceIntent) -> bool {
    if intent.plan_id.projectile_ordinal == 0
        || intent.projectile_id.is_empty()
        || intent.projectile_id.len() > 96
        || intent.projectile_id.chars().any(char::is_control)
        || intent.issued_tick.checked_add(ORACLE_PIPELINE_TICKS) != Some(intent.apply_tick)
        || intent.targets.is_empty()
        || intent.targets.len() > MAX_FROZEN_HE_TARGETS
        || ![
            intent.impact.x,
            intent.impact.y,
            intent.impact.z,
            intent.incoming_direction.x,
            intent.incoming_direction.y,
            intent.incoming_direction.z,
            intent.caliber_mm,
        ]
        .into_iter()
        .all(f64::is_finite)
        || intent.caliber_mm <= 0.0
    {
        return false;
    }
    let direction_length = intent.incoming_direction.magnitude();
    if direction_length <= 0.0 || !direction_length.is_finite() {
        return false;
    }
    intent
        .targets
        .windows(2)
        .all(|targets| targets[0].vehicle < targets[1].vehicle)
}

fn valid_ram_contact_armor_intent(intent: &RamContactArmorIntent) -> bool {
    let canonical_pair = match RamPair::new(intent.pair.first, intent.pair.second) {
        Ok(pair) => pair,
        Err(_) => return false,
    };
    if canonical_pair != intent.pair
        || intent.source_time_us > MAX_RAM_SOURCE_TIME_US
        || intent.issued_tick.checked_add(ORACLE_PIPELINE_TICKS) != Some(intent.apply_tick)
        || RamDamageProfile::new(
            intent.first_profile.spall_coefficient(),
            intent.first_profile.controlled_impact_bonus(),
        )
        .is_err()
        || RamDamageProfile::new(
            intent.second_profile.spall_coefficient(),
            intent.second_profile.controlled_impact_bonus(),
        )
        .is_err()
    {
        return false;
    }
    let poses = [intent.first_pose, intent.second_pose];
    if poses.iter().any(|pose| {
        ![
            f64::from(pose.position.x),
            f64::from(pose.position.y),
            f64::from(pose.position.z),
            pose.yaw,
            pose.pitch,
            pose.roll,
            pose.turret_yaw,
            pose.gun_pitch,
        ]
        .into_iter()
        .all(f64::is_finite)
            || [pose.position.x, pose.position.y, pose.position.z]
                .into_iter()
                .any(|component| f64::from(component).abs() > MAX_RAM_CONTACT_COORDINATE_M)
            || [
                pose.yaw,
                pose.pitch,
                pose.roll,
                pose.turret_yaw,
                pose.gun_pitch,
            ]
            .into_iter()
            .any(|angle| angle.abs() > MAX_RAM_CONTACT_POSE_ANGLE_RAD)
            || pose.siege_state > 3
    }) {
        return false;
    }
    if !finite_vec(intent.contact_point)
        || !finite_vec(intent.contact_normal)
        || [
            intent.contact_point.x,
            intent.contact_point.y,
            intent.contact_point.z,
            intent.contact_normal.x,
            intent.contact_normal.y,
            intent.contact_normal.z,
        ]
        .into_iter()
        .any(|component| component.abs() > MAX_RAM_CONTACT_COORDINATE_M)
    {
        return false;
    }
    let normal_length = (intent.contact_normal.x * intent.contact_normal.x
        + intent.contact_normal.y * intent.contact_normal.y
        + intent.contact_normal.z * intent.contact_normal.z)
        .sqrt();
    if (normal_length - 1.0).abs() > RAM_CONTACT_NORMAL_TOLERANCE {
        return false;
    }
    let distance_to_contact = |pose: RamContactPose| {
        let dx = intent.contact_point.x - f64::from(pose.position.x);
        let dy = intent.contact_point.y - f64::from(pose.position.y);
        let dz = intent.contact_point.z - f64::from(pose.position.z);
        (dx * dx + dy * dy + dz * dz).sqrt()
    };
    if poses
        .into_iter()
        .any(|pose| distance_to_contact(pose) > MAX_RAM_CONTACT_POSE_DISTANCE_M)
    {
        return false;
    }
    let center_delta = Vec3 {
        x: f64::from(intent.first_pose.position.x - intent.second_pose.position.x),
        y: f64::from(intent.first_pose.position.y - intent.second_pose.position.y),
        z: f64::from(intent.first_pose.position.z - intent.second_pose.position.z),
    };
    center_delta.x * intent.contact_normal.x
        + center_delta.y * intent.contact_normal.y
        + center_delta.z * intent.contact_normal.z
        >= -RAM_CONTACT_NORMAL_TOLERANCE
}

fn native_observation_query_key(
    intent: &NativeObservationIntent,
    evidence: NativeObservationEvidenceKind,
    lane: Tick,
) -> String {
    let evidence = match evidence {
        NativeObservationEvidenceKind::Spotting => "spot",
        NativeObservationEvidenceKind::FiringLane => "lane",
    };
    let observer_kind = vehicle_kind_key(intent.observer.kind);
    let target_kind = vehicle_kind_key(intent.target.kind);
    format!(
        "obs/{observer_kind}{}/{evidence}/{target_kind}{}/o{}g{}/t{}g{}/l{lane}",
        intent.observer.id,
        intent.target.id,
        intent.observer_entity.entity_id,
        intent.observer_entity.generation,
        intent.target_entity.entity_id,
        intent.target_entity.generation,
    )
}

fn destructible_hull_due(vehicle: VehicleKey, tick: Tick) -> bool {
    let kind_salt = match vehicle.kind {
        VehicleKind::Bot => 0,
        VehicleKind::Player => 5,
    };
    let phase =
        (vehicle.id.saturating_mul(17).saturating_add(kind_salt)) % DESTRUCTIBLE_HULL_CADENCE_TICKS;
    let slot = tick % DESTRUCTIBLE_HULL_CADENCE_TICKS;
    slot == phase || slot == (phase + 4) % DESTRUCTIBLE_HULL_CADENCE_TICKS
}

fn vehicle_kind_key(kind: VehicleKind) -> char {
    match kind {
        VehicleKind::Bot => 'b',
        VehicleKind::Player => 'h',
    }
}

fn vehicle_order_key(vehicle: VehicleKey) -> (u8, u64) {
    (
        match vehicle.kind {
            VehicleKind::Bot => 0,
            VehicleKind::Player => 1,
        },
        vehicle.id,
    )
}

fn finite_vec(value: Vec3) -> bool {
    value.x.is_finite() && value.y.is_finite() && value.z.is_finite()
}

fn valid_observation_segment(start: Vec3, end: Vec3) -> bool {
    if !finite_vec(start) || !finite_vec(end) {
        return false;
    }
    let dx = end.x - start.x;
    let dy = end.y - start.y;
    let dz = end.z - start.z;
    dx * dx + dy * dy + dz * dz > 1.0e-12
}

fn valid_player_muzzle_binding(binding: &FireIntentBinding) -> bool {
    binding.player_id != 0
        && binding.player_id <= MAX_COMBAT_ID
        && binding.intent_seq != 0
        && binding.intent_seq <= MAX_COMBAT_ID
        && binding.shot_seq != 0
        && binding.shot_seq <= MAX_COMBAT_ID
        && binding.input_seq != 0
        && binding.input_seq <= MAX_COMBAT_ID
        && binding.shell_index <= 9
        && binding.deadline_server_time_ms != 0
        && [
            binding.pose.x,
            binding.pose.y,
            binding.pose.z,
            binding.pose.yaw,
            binding.pose.pitch,
            binding.pose.roll,
            binding.pose.speed,
            binding.pose.aim_yaw,
            binding.pose.gun_pitch,
        ]
        .into_iter()
        .all(f64::is_finite)
}

enum PlayerMuzzleEvidenceOutcome {
    Available(PlayerMuzzleEvidence),
    Unavailable,
}

fn decode_player_muzzle_evidence(
    record: &PlayerMuzzleRecord,
    applied: &AppliedOracleBatch,
) -> Result<PlayerMuzzleEvidenceOutcome, AuthorityRuntimeError> {
    let batch_key = record.request.key();
    if applied.request != record.request || applied.reply.key() != batch_key {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    }
    let [query] = record.request.queries.as_slice() else {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    };
    if query.query_id != 1 || query.entity != record.entity {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    }
    if !matches!(
        &query.operation,
        OracleOperation::PlayerMuzzleEvidence(PlayerMuzzleEvidenceQuery {})
    ) {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    }
    let [result] = applied.reply.results.as_slice() else {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    };
    if result.query_id != query.query_id
        || result.key != query.key
        || result.query_generation != query.query_generation
        || result.entity != query.entity
    {
        return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
    }
    let outcome = match &result.status {
        OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::PlayerMuzzleEvidence(evidence),
        } => PlayerMuzzleEvidenceOutcome::Available(evidence.clone()),
        OracleV1ResultStatus::Unavailable { .. } => PlayerMuzzleEvidenceOutcome::Unavailable,
        _ => {
            return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
        }
    };
    if let PlayerMuzzleEvidenceOutcome::Available(evidence) = &outcome {
        if !evidence.transform.position.x.is_finite()
            || !evidence.transform.position.y.is_finite()
            || !evidence.transform.position.z.is_finite()
            || !evidence.transform.basis.into_iter().all(f32::is_finite)
        {
            return Err(AuthorityRuntimeError::InvalidPlayerMuzzleReply { key: batch_key });
        }
    }
    Ok(outcome)
}

fn valid_order(order: &BotOrder) -> bool {
    order.throttle.is_finite()
        && order.turn.is_finite()
        && order.fire_range.is_finite()
        && order.target_yaw.is_none_or(f64::is_finite)
        && order.aim_position.is_none_or(finite_vec)
        && order
            .navigation_target
            .is_none_or(|target| finite_vec(target.point))
        && order.target.as_ref().is_none_or(|target| {
            target.network_id != 0
                && matches!(target.team, 1 | 2)
                && finite_vec(target.position)
                && finite_vec(target.velocity)
                && target.yaw.is_finite()
                && target.speed.is_finite()
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bot_sim::{
        BotProfile, BotSpawn, ClipDescriptor, CriticalState, GunDescriptor, GunYawLimits,
        PhysicsProfile, ShellDescriptor, ShellProfile, VehicleClass, VehicleDescriptor,
    };
    use crate::combat::BodyPose;
    use crate::projectile::{
        LaunchAdmission, LaunchContext, ProjectileLaunch, ProjectileLedger, ProjectileOutcome,
        ProjectileVec3, SourceShell, SourceShot,
    };
    use crate::projectile_sim::ProjectileTerminalCause;
    use crate::protocol::{
        DestructibleHullEvidence, DestructibleShotEvidence, ExplosionEvidence, FiringLaneEvidence,
        OracleOperation, OracleV1Result, OracleV1ResultStatus, QueryOutcome,
        RamContactArmorEvidence, RayHit, SpottingEvidence, SurfaceSample, TransformSample,
        Vec3 as ProtocolVec3, ORACLE_PROTOCOL_VERSION,
    };
    use serde_json::json;

    fn lineage() -> OracleLineage {
        OracleLineage {
            round_id: 7,
            authority_epoch: 3,
            oracle_generation: 5,
        }
    }

    fn entity(entity_id: i64) -> EntityRef {
        EntityRef {
            entity_id,
            generation: 1,
        }
    }

    fn descriptor() -> VehicleDescriptor {
        VehicleDescriptor {
            vehicle_key: "ussr:R11_MS-1".to_owned(),
            max_ammo: 30,
            max_health: 100,
            half_length: 2.0,
            half_width: 1.0,
            gun: GunDescriptor {
                reload_seconds: 1.0,
                clip: Some(ClipDescriptor {
                    size: 1,
                    intra_reload_seconds: 1.0,
                }),
                shot_dispersion_angle: 0.02,
                gun_rotation_speed: 1.0,
                turret_rotation_speed: 1.0,
                pitch_limits: (-0.5, 0.5),
                yaw_limits: GunYawLimits::default(),
                shells: vec![ShellDescriptor {
                    index: 0,
                    kind: "ARMOR_PIERCING".to_owned(),
                    penetration: 100.0,
                    damage: 20.0,
                    speed: 100.0,
                    gravity: 9.81,
                    max_distance: 720.0,
                }],
            },
            physics: PhysicsProfile::default(),
            module_names: vec!["engine".to_owned()],
            crew_roster: vec!["commander".to_owned()],
        }
    }

    fn profile() -> BotProfile {
        BotProfile {
            class: VehicleClass::LightTank,
            shells: vec![ShellProfile {
                index: 0,
                kind: "ARMOR_PIERCING".to_owned(),
                penetration: 100.0,
            }],
        }
    }

    fn bot(id: u32, position: Vec3) -> BotSimulator {
        BotSimulator::new(
            descriptor(),
            profile(),
            BotSpawn {
                id,
                team: if id % 2 == 0 { 2 } else { 1 },
                round_id: lineage().round_id,
                tick: 0,
                position,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                health: 100,
                fire_seq: 0,
                critical: CriticalState::default(),
            },
        )
        .unwrap()
    }

    fn bot_vehicle(id: u32) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Bot,
            id: u64::from(id),
        }
    }

    fn player_vehicle(id: u32) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id: u64::from(id),
        }
    }

    fn donation(bot_ids: &[u32]) -> NativeEntityDonation {
        NativeEntityDonation {
            lineage: lineage(),
            oracle_space: entity(900),
            bots: bot_ids
                .iter()
                .map(|id| (*id, entity(100 + i64::from(*id))))
                .collect(),
            humans: BTreeMap::new(),
        }
    }

    fn donation_with_humans(bot_ids: &[u32], human_ids: &[u32]) -> NativeEntityDonation {
        let mut result = donation(bot_ids);
        result.humans = human_ids
            .iter()
            .map(|id| (*id, entity(500 + i64::from(*id))))
            .collect();
        result
    }

    fn fire_binding(player_id: u64, intent_seq: u64) -> FireIntentBinding {
        FireIntentBinding {
            player_id,
            intent_seq,
            shot_seq: intent_seq,
            input_seq: intent_seq,
            pose_time_us: 10_000,
            shell_index: 0,
            pose: BodyPose {
                x: 0.0,
                y: 0.0,
                z: 0.0,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                speed: 0.0,
                aim_yaw: 0.0,
                gun_pitch: 0.0,
            },
            deadline_server_time_ms: 5_010,
        }
    }

    fn ram_contact_intent(issued_tick: Tick, frontier: u64) -> RamContactArmorIntent {
        let pair = RamPair::new(player_vehicle(7), bot_vehicle(7)).unwrap();
        RamContactArmorIntent {
            pair,
            cursor: RamSourceCursor::new(1, frontier).unwrap(),
            source_time_us: frontier * 10_000,
            issued_tick,
            apply_tick: issued_tick + ORACLE_PIPELINE_TICKS,
            first_pose: RamContactPose {
                position: OracleVec3 {
                    x: 1.0,
                    y: 0.0,
                    z: 0.0,
                },
                yaw: 0.25,
                pitch: 0.1,
                roll: -0.05,
                turret_yaw: 0.0,
                gun_pitch: 0.0,
                siege_state: 0,
            },
            second_pose: RamContactPose {
                position: OracleVec3 {
                    x: -1.0,
                    y: 0.0,
                    z: 0.0,
                },
                yaw: -0.2,
                pitch: 0.0,
                roll: 0.03,
                turret_yaw: 0.0,
                gun_pitch: 0.0,
                siege_state: 0,
            },
            contact_point: Vec3::new(0.0, 0.5, 0.0),
            contact_normal: Vec3::new(1.0, 0.0, 0.0),
            first_profile: RamDamageProfile::new(1.25, 0.1).unwrap(),
            second_profile: RamDamageProfile::new(1.4, 0.15).unwrap(),
            first_moving: true,
            second_moving: false,
        }
    }

    fn orders(bot_ids: &[u32]) -> BTreeMap<u32, BotOrder> {
        bot_ids
            .iter()
            .map(|id| (*id, BotOrder::default()))
            .collect()
    }

    fn tick(tick: u64, bot_ids: &[u32]) -> AuthorityTickInput {
        AuthorityTickInput {
            tick,
            orders: orders(bot_ids),
            human_traffic: Vec::new(),
            world_pose_bots: bot_ids.iter().copied().collect(),
            projectile_targets: Vec::new(),
            static_collision_mask: 0xffff_ffff,
        }
    }

    fn tick_with_human(tick: u64, player_id: u32, y: f64) -> AuthorityTickInput {
        AuthorityTickInput {
            tick,
            orders: BTreeMap::new(),
            human_traffic: vec![TrafficBody {
                network_id: player_id,
                kind: TargetKind::Human,
                team: 1,
                position: Vec3::new(10.0, y, 20.0),
                velocity: Vec3::new(3.0, 0.0, 4.0),
                yaw: 0.5,
                half_length: 2.0,
                half_width: 1.0,
            }],
            world_pose_bots: BTreeSet::new(),
            projectile_targets: Vec::new(),
            static_collision_mask: 0xffff_ffff,
        }
    }

    #[test]
    fn typed_navigation_target_is_complete_and_validated() {
        let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../navgraphs");
        let graph = NavGraph::load_from_directory(&directory, "01_karelia").unwrap();
        let pose = graph.spawn_pose(1, 0).unwrap();
        let position = Vec3::new(pose.x, pose.y, pose.z);
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, position)]).unwrap();
        runtime.install_navigation_graph(graph).unwrap();

        let mut input = tick(1, &[1]);
        assert!(matches!(
            runtime.validate_tick_input(&input),
            Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: 1 })
        ));

        input.orders.get_mut(&1).unwrap().navigation_target = Some(NavTarget {
            point: position,
            controlled_shallow: false,
        });
        runtime.validate_tick_input(&input).unwrap();

        input.orders.get_mut(&1).unwrap().navigation_target = Some(NavTarget {
            point: Vec3::new(f64::NAN, position.y, position.z),
            controlled_shallow: false,
        });
        assert!(matches!(
            runtime.validate_tick_input(&input),
            Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: 1 })
        ));

        input.orders.get_mut(&1).unwrap().navigation_target = Some(NavTarget {
            point: position,
            controlled_shallow: true,
        });
        assert!(matches!(
            runtime.validate_tick_input(&input),
            Err(AuthorityRuntimeError::InvalidBotOrder { bot_id: 1 })
        ));
    }

    fn human_body(player_id: u32, team: u8, position: Vec3) -> TrafficBody {
        TrafficBody {
            network_id: player_id,
            kind: TargetKind::Human,
            team,
            position,
            velocity: Vec3::ZERO,
            yaw: 0.0,
            half_length: 2.0,
            half_width: 1.0,
        }
    }

    fn planner_profile(class_tag: &str) -> Value {
        json!({
            "class_tag": class_tag,
            "dominant_role": if class_tag == "SPG" { "artillery" } else { "support" },
            "speed": 12.0,
            "desired_range": 100.0,
            "fire_range": 500.0,
            "armor": 80.0,
            "roles": {"support": 1.0},
            "shells": [{
                "index": 0,
                "kind": "ARMOR_PIERCING",
                "penetration": 100.0,
                "damage": 20.0,
                "speed": 100.0,
            }],
        })
    }

    fn planner_manifest(goal_z: f64, class_tag: &str) -> Value {
        json!([{
            "id": 1,
            "team": 1,
            "slot": 0,
            "health": 100,
            "profile": planner_profile(class_tag),
            "route": {
                "id": "lane",
                "waypoints": [{"x": 0.0, "y": 0.0, "z": goal_z, "hold": false}],
            },
        }])
    }

    fn planner_bot_states() -> Value {
        json!([{
            "id": 1,
            "team": 1,
            "alive": true,
            "world_pose": true,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "yaw": 0.0,
            "health": 100,
            "max_health": 100,
            "shell_index": 0,
            "critical": {},
            "ammo_remaining": [30],
        }])
    }

    fn planner_player() -> Value {
        json!([{
            "id": 2,
            "team": 2,
            "alive": true,
            "health": 100,
            "max_health": 100,
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "yaw": 3.141592653589793,
            "speed": 0.0,
        }])
    }

    fn planner_contact() -> Value {
        json!([{
            "observing_team": 1,
            "target_kind": "human",
            "target_id": 2,
            "target_team": 2,
            "visible": true,
            "shootable_by_bot_ids": [1],
            "x": 0.0,
            "y": 0.0,
            "z": 100.0,
            "health": 100,
            "max_health": 100,
            "class_tag": "mediumTank",
            "armor": 40.0,
        }])
    }

    fn reply(request: &OracleV1BatchRequest, frame: u64, ground_height: f32) -> OracleV1BatchReply {
        OracleV1BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: frame,
            results: request
                .queries
                .iter()
                .map(|query| OracleV1Result {
                    query_id: query.query_id,
                    key: query.key.clone(),
                    query_generation: query.query_generation,
                    entity: query.entity,
                    status: OracleV1ResultStatus::Ok {
                        outcome: clear_outcome(&query.operation, ground_height),
                    },
                })
                .collect(),
        }
    }

    fn clear_outcome(operation: &OracleOperation, ground_height: f32) -> QueryOutcome {
        let surface = || SurfaceSample {
            height: ground_height,
            normal: ProtocolVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            material_id: None,
        };
        match operation {
            OracleOperation::GroundSample { .. } => QueryOutcome::GroundSample {
                sample: Some(surface()),
            },
            OracleOperation::SegmentCast { .. } => QueryOutcome::SegmentCast { hit: None },
            OracleOperation::SegmentCastBatch { segments } => QueryOutcome::SegmentCastBatch {
                hits: vec![None; segments.len()],
            },
            OracleOperation::GroundSampleBatch { positions } => QueryOutcome::GroundSampleBatch {
                samples: positions.iter().map(|_| Some(surface())).collect(),
            },
            OracleOperation::WaterSample { .. } => QueryOutcome::WaterSample { height: None },
            OracleOperation::WaterSampleBatch { positions } => QueryOutcome::WaterSampleBatch {
                heights: vec![None; positions.len()],
            },
            OracleOperation::VehicleHitTest { .. } => QueryOutcome::VehicleHitTest { hit: None },
            OracleOperation::ExplosionEvidence(arguments) => {
                QueryOutcome::ExplosionEvidence(ExplosionEvidence {
                    target_pose: arguments.target_pose,
                    vehicle_ray: None,
                    internal_hits: Some(Vec::new()),
                })
            }
            OracleOperation::NodeTransform { .. } => QueryOutcome::NodeTransform {
                transform: Some(TransformSample {
                    position: ProtocolVec3 {
                        x: 0.0,
                        y: 1.5,
                        z: 0.0,
                    },
                    basis: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                }),
            },
            OracleOperation::PlayerMuzzleEvidence(..) => {
                QueryOutcome::PlayerMuzzleEvidence(PlayerMuzzleEvidence {
                    transform: TransformSample {
                        position: ProtocolVec3 {
                            x: 0.0,
                            y: 1.5,
                            z: 0.0,
                        },
                        basis: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                    },
                    barrel_under_water: false,
                })
            }
            OracleOperation::SpottingEvidence(arguments) => {
                QueryOutcome::SpottingEvidence(SpottingEvidence {
                    line_of_sight: true,
                    foliage_bonus: 0.25,
                    evaluated_for_recent_fire: arguments.evaluated_for_recent_fire,
                })
            }
            OracleOperation::FiringLaneEvidence(..) => {
                QueryOutcome::FiringLaneEvidence(FiringLaneEvidence { clear: false })
            }
            OracleOperation::RamContactArmorEvidence(..) => {
                QueryOutcome::RamContactArmorEvidence(RamContactArmorEvidence {
                    first_armor_mm: 1.0,
                    second_armor_mm: 1.0,
                })
            }
            OracleOperation::DestructibleShotEvidence(..) => {
                QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
                    candidates: Vec::new(),
                    destroyed_skipped: 0,
                    static_collision: None,
                })
            }
            OracleOperation::DestructibleHullEvidence(arguments) => {
                QueryOutcome::DestructibleHullEvidence(DestructibleHullEvidence {
                    candidates: Vec::new(),
                    frame_travel: arguments.frame_travel,
                })
            }
        }
    }

    fn visibility_pairs(output: &AuthorityTickOutput) -> Vec<(VehicleKey, EntityRef)> {
        let mut pairs = output
            .oracle_requests
            .iter()
            .flat_map(|request| &request.queries)
            .filter_map(|query| {
                let OracleOperation::SpottingEvidence(arguments) = &query.operation else {
                    return None;
                };
                let raw_observer = query.key.split('/').nth(1)?;
                let kind = raw_observer.get(..1)?;
                let raw_id = raw_observer.get(1..)?;
                let observer = VehicleKey {
                    kind: match kind {
                        "b" => VehicleKind::Bot,
                        "h" => VehicleKind::Player,
                        _ => return None,
                    },
                    id: raw_id.parse::<u64>().ok()?,
                };
                Some((observer, arguments.target))
            })
            .collect::<Vec<_>>();
        pairs.sort_by_key(|(observer, target)| {
            (
                vehicle_order_key(*observer),
                target.entity_id,
                target.generation,
            )
        });
        pairs
    }

    fn hull_requests(output: &AuthorityTickOutput) -> Vec<&OracleV1BatchRequest> {
        output
            .oracle_requests
            .iter()
            .filter(|request| {
                request.queries.iter().all(|query| {
                    matches!(
                        query.operation,
                        OracleOperation::DestructibleHullEvidence(..)
                    )
                })
            })
            .collect()
    }

    #[test]
    fn released_due_does_not_step_bots_until_the_permit_is_consumed() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime.donate_native_entities(donation(&[1])).unwrap();

        let (due, permit) = runtime.release_due(1).unwrap();
        assert_eq!(due.tick, 1);
        assert_eq!(permit.tick(), 1);
        assert_eq!(runtime.current_tick(), 1);
        assert_eq!(runtime.bot_state(1).unwrap().tick, 0);
        assert!(due.ram_contact_evidence.is_empty());
        assert!(due.he_explosion_evidence.is_empty());
        assert!(due.projectile_decisions.is_empty());
        assert!(matches!(
            runtime.release_due(2),
            Err(AuthorityRuntimeError::ReleasedTickPending { tick: 1 })
        ));

        let output = runtime.step_after_due(permit, tick(1, &[1])).unwrap();
        assert_eq!(output.tick, 1);
        assert_eq!(output.bots.len(), 1);
        assert_eq!(runtime.bot_state(1).unwrap().tick, 1);
    }

    #[test]
    fn released_tick_runtime_fence_rejects_a_second_consumption() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime.donate_native_entities(donation(&[1])).unwrap();
        let (_, permit) = runtime.release_due(1).unwrap();
        // Public callers cannot construct or clone this opaque permit. The
        // in-module duplicate proves the runtime fence remains fail-closed even
        // if an internal regression accidentally duplicates its payload.
        let duplicate = ReleasedAuthorityTick {
            lineage: permit.lineage,
            tick: permit.tick,
            receipts: permit.receipts.clone(),
        };

        runtime.step_after_due(permit, tick(1, &[1])).unwrap();
        assert_eq!(runtime.bot_state(1).unwrap().tick, 1);
        assert!(matches!(
            runtime.step_after_due(duplicate, tick(1, &[1])),
            Err(AuthorityRuntimeError::ReleasedTickMismatch {
                current_tick: 1,
                released_tick: None,
                permit_tick: 1,
                input_tick: 1,
            })
        ));
        assert_eq!(runtime.bot_state(1).unwrap().tick, 1);
    }

    #[test]
    fn terminal_close_consumes_the_permit_without_stepping() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime.donate_native_entities(donation(&[1])).unwrap();
        let (_, permit) = runtime.release_due(1).unwrap();
        let duplicate = ReleasedAuthorityTick {
            lineage: permit.lineage,
            tick: permit.tick,
            receipts: permit.receipts.clone(),
        };

        runtime.close_terminal_after_due(permit).unwrap();
        assert_eq!(runtime.current_tick(), 1);
        assert_eq!(runtime.bot_state(1).unwrap().tick, 0);
        assert!(matches!(
            runtime.close_terminal_after_due(duplicate),
            Err(AuthorityRuntimeError::ReleasedTickMismatch {
                current_tick: 1,
                released_tick: None,
                permit_tick: 1,
                input_tick: 1,
            })
        ));
        assert_eq!(runtime.bot_state(1).unwrap().tick, 0);
    }

    #[test]
    fn donation_is_complete_fenced_and_idempotent() {
        let mut runtime = AuthorityRuntime::new(
            lineage(),
            0,
            1,
            vec![bot(2, Vec3::ZERO), bot(1, Vec3::ZERO)],
        )
        .unwrap();
        let complete = donation(&[1, 2]);
        assert!(runtime.donate_native_entities(complete.clone()).unwrap());
        assert!(!runtime.donate_native_entities(complete).unwrap());

        let mut conflicting = donation(&[1, 2]);
        conflicting.bots.insert(1, entity(777));
        assert!(matches!(
            runtime.donate_native_entities(conflicting),
            Err(AuthorityRuntimeError::ConflictingDonation)
        ));
    }

    #[test]
    fn exact_spotting_observer_set_is_typed_idempotent_and_fenced() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[1], &[1]))
            .unwrap();
        let exact = BTreeSet::from([bot_vehicle(1), player_vehicle(1)]);
        assert!(runtime.install_spotting_observers(exact.clone()).unwrap());
        assert!(!runtime.install_spotting_observers(exact).unwrap());
        assert!(matches!(
            runtime.install_spotting_observers(BTreeSet::from([bot_vehicle(1)])),
            Err(AuthorityRuntimeError::ConflictingSpottingObservers)
        ));

        let mut invalid = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        invalid.donate_native_entities(donation(&[1])).unwrap();
        assert!(matches!(
            invalid.install_spotting_observers(BTreeSet::from([player_vehicle(1)])),
            Err(AuthorityRuntimeError::InvalidSpottingObserver { vehicle })
                if vehicle == player_vehicle(1)
        ));
    }

    #[test]
    fn ricochet_continuation_is_forwarded_to_the_projectile_integrator() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        let record = player_record(20);
        let projectile_id = record.projectile_id.clone();
        assert!(matches!(
            runtime.continue_projectile_ricochet(record),
            Err(AuthorityRuntimeError::Projectile(
                ProjectileFlightError::MissingRicochetContinuation { projectile_id: missing }
            )) if missing == projectile_id
        ));
    }

    #[test]
    fn supplemental_native_observations_are_bounded_and_rotate_both_axes() {
        let bot_ids = (1..=10).collect::<Vec<_>>();
        let bots = bot_ids
            .iter()
            .map(|id| bot(*id, Vec3::new(f64::from(*id) * 10.0, 0.0, 0.0)))
            .collect();
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, bots).unwrap();
        runtime.donate_native_entities(donation(&bot_ids)).unwrap();

        let first = runtime.advance_tick(tick(1, &bot_ids)).unwrap();
        let first_pairs = visibility_pairs(&first);
        assert_eq!(first_pairs.len(), MAX_NATIVE_OBSERVATION_PAIRS_PER_TICK);
        assert!(first
            .oracle_requests
            .iter()
            .all(|request| request.lineage() == lineage() && request.apply_tick == 4));
        assert_eq!(
            first_pairs
                .iter()
                .map(|(observer, _)| observer.id as u32)
                .collect::<Vec<_>>(),
            (1..=8).collect::<Vec<_>>()
        );

        let second = runtime.advance_tick(tick(2, &bot_ids)).unwrap();
        let second_pairs = visibility_pairs(&second);
        assert_eq!(second_pairs.len(), MAX_NATIVE_OBSERVATION_PAIRS_PER_TICK);
        assert_eq!(
            second_pairs
                .iter()
                .map(|(observer, _)| observer.id as u32)
                .collect::<BTreeSet<_>>(),
            [1, 2, 3, 4, 5, 6, 9, 10].into_iter().collect()
        );
        let first_target = first_pairs
            .iter()
            .find(|(observer, _)| *observer == bot_vehicle(1))
            .unwrap()
            .1;
        let second_target = second_pairs
            .iter()
            .find(|(observer, _)| *observer == bot_vehicle(1))
            .unwrap()
            .1;
        assert_ne!(first_target, second_target);
    }

    #[test]
    fn player_only_observers_keep_typed_t3_query_and_receipt_lineage() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 9, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7, 8]))
            .unwrap();
        runtime
            .install_spotting_observers(BTreeSet::from([player_vehicle(7), player_vehicle(8)]))
            .unwrap();
        let bodies = vec![
            human_body(7, 1, Vec3::ZERO),
            human_body(8, 2, Vec3::new(0.0, 0.0, 100.0)),
        ];
        let mut first_input = tick(1, &[]);
        first_input.human_traffic = bodies.clone();
        let first = runtime.advance_tick(first_input).unwrap();
        let pairs = visibility_pairs(&first);
        assert_eq!(
            pairs,
            vec![
                (player_vehicle(7), entity(508)),
                (player_vehicle(8), entity(507)),
            ]
        );
        let request = first
            .oracle_requests
            .iter()
            .find(|request| {
                request
                    .queries
                    .iter()
                    .any(|query| matches!(query.operation, OracleOperation::SpottingEvidence(..)))
            })
            .unwrap();
        assert_eq!(request.issued_tick, 1);
        assert_eq!(request.apply_tick, 4);
        assert!(request.queries.iter().all(|query| {
            query.key.starts_with("obs/h")
                && matches!(query.operation, OracleOperation::SpottingEvidence(..))
        }));
        assert!(matches!(
            runtime.accept_oracle_reply(reply(request, 1, 0.0)).unwrap(),
            OracleReplyDisposition::Buffered { apply_tick: 4, .. }
        ));

        for tick_value in [2, 3] {
            let mut input = tick(tick_value, &[]);
            input.human_traffic = bodies.clone();
            assert!(runtime
                .advance_tick(input)
                .unwrap()
                .native_observations
                .is_empty());
        }
        let mut fourth_input = tick(4, &[]);
        fourth_input.human_traffic = bodies;
        let fourth = runtime.advance_tick(fourth_input).unwrap();
        assert_eq!(fourth.native_observations.len(), 2);
        assert_eq!(
            fourth
                .native_observations
                .iter()
                .map(|sample| (sample.intent.observer, sample.intent.target))
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([
                (player_vehicle(7), player_vehicle(8)),
                (player_vehicle(8), player_vehicle(7)),
            ])
        );
        assert!(fourth.native_observations.iter().all(|sample| {
            sample.intent.lineage == lineage()
                && sample.intent.issued_tick == 1
                && sample.intent.apply_tick == 4
                && sample.batch_key.lineage == lineage()
        }));
        assert!(fourth.native_firing_lanes.is_empty());
    }

    #[test]
    fn equal_numeric_player_and_bot_observers_do_not_collide() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[1], &[1]))
            .unwrap();
        runtime
            .install_spotting_observers(BTreeSet::from([bot_vehicle(1), player_vehicle(1)]))
            .unwrap();
        let mut input = tick(1, &[1]);
        input.human_traffic = vec![human_body(1, 2, Vec3::new(0.0, 0.0, 100.0))];
        let output = runtime.advance_tick(input).unwrap();
        assert_eq!(
            visibility_pairs(&output),
            vec![
                (bot_vehicle(1), entity(501)),
                (player_vehicle(1), entity(101)),
            ]
        );
        let observation_queries = output
            .oracle_requests
            .iter()
            .flat_map(|request| &request.queries)
            .filter(|query| {
                matches!(
                    query.operation,
                    OracleOperation::SpottingEvidence(..) | OracleOperation::FiringLaneEvidence(..)
                )
            })
            .collect::<Vec<_>>();
        assert_eq!(
            observation_queries
                .iter()
                .filter(|query| query.key.starts_with("obs/b1/"))
                .count(),
            2
        );
        assert_eq!(
            observation_queries
                .iter()
                .filter(|query| query.key.starts_with("obs/h1/"))
                .count(),
            1
        );
    }

    #[test]
    fn native_pair_request_freezes_recent_fire_and_separates_barrel_lane() {
        let mut runtime = AuthorityRuntime::new(
            lineage(),
            0,
            17,
            vec![bot(1, Vec3::ZERO), bot(2, Vec3::new(0.0, 0.0, 100.0))],
        )
        .unwrap();
        runtime.donate_native_entities(donation(&[1, 2])).unwrap();
        assert!(runtime
            .note_spotting_fire(
                VehicleKey {
                    kind: VehicleKind::Bot,
                    id: 2,
                },
                0,
            )
            .unwrap());

        let first = runtime.advance_tick(tick(1, &[1, 2])).unwrap();
        let request = first
            .oracle_requests
            .iter()
            .find(|request| {
                request
                    .queries
                    .iter()
                    .any(|query| matches!(query.operation, OracleOperation::SpottingEvidence(..)))
            })
            .unwrap();
        assert_eq!(request.issued_tick, 1);
        assert_eq!(request.apply_tick, 4);
        assert_eq!(request.world_revision, 17);
        assert_eq!(request.lineage(), lineage());
        assert_eq!(request.queries.len(), 4);

        let spotting = request
            .queries
            .iter()
            .filter_map(|query| match &query.operation {
                OracleOperation::SpottingEvidence(arguments) => Some((query, arguments)),
                _ => None,
            })
            .collect::<Vec<_>>();
        let lanes = request
            .queries
            .iter()
            .filter_map(|query| match &query.operation {
                OracleOperation::FiringLaneEvidence(arguments) => Some((query, arguments)),
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(spotting.len(), 2);
        assert_eq!(lanes.len(), 2);
        assert!(spotting.iter().all(|(query, arguments)| {
            query.entity == arguments.target
                && arguments.observer != arguments.target
                && arguments.collision_mask == NATIVE_OBSERVATION_COLLISION_MASK
        }));
        let bot_two_target = spotting
            .iter()
            .find(|(_, arguments)| arguments.target == entity(102))
            .unwrap();
        assert!(bot_two_target.1.evaluated_for_recent_fire);
        assert!(lanes.iter().all(|(query, arguments)| {
            query.entity == arguments.target
                && arguments.observer != arguments.target
                && arguments.collision_mask == NATIVE_OBSERVATION_COLLISION_MASK
        }));
        assert!(request.queries.windows(2).any(|pair| {
            matches!(pair[0].operation, OracleOperation::SpottingEvidence(..))
                && matches!(pair[1].operation, OracleOperation::FiringLaneEvidence(..))
                && pair[0].query_id != pair[1].query_id
                && pair[0].key != pair[1].key
        }));
    }

    #[test]
    fn native_observation_reply_is_released_only_at_exact_t_plus_three() {
        let mut runtime = AuthorityRuntime::new(
            lineage(),
            0,
            1,
            vec![bot(1, Vec3::ZERO), bot(2, Vec3::new(0.0, 0.0, 100.0))],
        )
        .unwrap();
        runtime.donate_native_entities(donation(&[1, 2])).unwrap();

        let first = runtime.advance_tick(tick(1, &[1, 2])).unwrap();
        assert_eq!(visibility_pairs(&first).len(), 2);
        for (index, request) in first.oracle_requests.iter().enumerate() {
            assert!(matches!(
                runtime
                    .accept_oracle_reply(reply(request, index as u64 + 1, 0.0))
                    .unwrap(),
                OracleReplyDisposition::Buffered { apply_tick: 4, .. }
            ));
        }
        assert!(runtime
            .advance_tick(tick(2, &[1, 2]))
            .unwrap()
            .native_observations
            .is_empty());
        assert!(runtime
            .advance_tick(tick(3, &[1, 2]))
            .unwrap()
            .native_observations
            .is_empty());

        let fourth = runtime.advance_tick(tick(4, &[1, 2])).unwrap();
        assert_eq!(fourth.native_observations.len(), 2);
        assert_eq!(fourth.native_firing_lanes.len(), 2);
        assert!(fourth.failed_native_observations.is_empty());
        assert!(fourth.timed_out_native_observations.is_empty());
        assert!(fourth.native_observations.iter().all(|sample| {
            sample.intent.lineage == lineage()
                && sample.intent.issued_tick == 1
                && sample.intent.apply_tick == 4
                && sample.intent.purpose == NativeObservationPurpose::Discovery
                && sample.line_of_sight
                && sample.foliage_bonus == 0.25
                && !sample.evaluated_for_recent_fire
        }));
        assert!(fourth
            .native_firing_lanes
            .iter()
            .all(|sample| !sample.clear));
    }

    #[test]
    fn observation_unavailable_and_timeout_never_become_negative_samples() {
        let make_runtime = || {
            let mut runtime = AuthorityRuntime::new(
                lineage(),
                0,
                1,
                vec![bot(1, Vec3::ZERO), bot(2, Vec3::new(0.0, 0.0, 100.0))],
            )
            .unwrap();
            runtime.donate_native_entities(donation(&[1, 2])).unwrap();
            runtime
        };

        let mut unavailable_runtime = make_runtime();
        let first = unavailable_runtime.advance_tick(tick(1, &[1, 2])).unwrap();
        for (index, request) in first.oracle_requests.iter().enumerate() {
            let mut unavailable = reply(request, index as u64 + 1, 0.0);
            for result in &mut unavailable.results {
                if matches!(
                    &result.status,
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::SpottingEvidence(..)
                    }
                ) {
                    result.status = OracleV1ResultStatus::Unavailable {
                        code: "visibility_unavailable".to_owned(),
                        message: "native visibility probe is unavailable".to_owned(),
                    };
                }
            }
            unavailable_runtime
                .accept_oracle_reply(unavailable)
                .unwrap();
        }
        unavailable_runtime.advance_tick(tick(2, &[1, 2])).unwrap();
        unavailable_runtime.advance_tick(tick(3, &[1, 2])).unwrap();
        let unavailable = unavailable_runtime.advance_tick(tick(4, &[1, 2])).unwrap();
        assert!(unavailable.native_observations.is_empty());
        assert_eq!(unavailable.native_firing_lanes.len(), 2);
        assert_eq!(unavailable.failed_native_observations.len(), 2);
        assert!(unavailable
            .failed_native_observations
            .iter()
            .all(|sample| { sample.evidence == NativeObservationEvidenceKind::Spotting }));
        assert!(unavailable.timed_out_native_observations.is_empty());

        let mut timeout_runtime = make_runtime();
        let timeout_first = timeout_runtime.advance_tick(tick(1, &[1, 2])).unwrap();
        timeout_runtime.advance_tick(tick(2, &[1, 2])).unwrap();
        timeout_runtime.advance_tick(tick(3, &[1, 2])).unwrap();
        let timeout = timeout_runtime.advance_tick(tick(4, &[1, 2])).unwrap();
        assert!(timeout.native_observations.is_empty());
        assert!(timeout.native_firing_lanes.is_empty());
        assert!(timeout.failed_native_observations.is_empty());
        assert_eq!(timeout.timed_out_native_observations.len(), 4);
        assert!(timeout
            .timed_out_native_observations
            .iter()
            .all(|sample| sample.intent.issued_tick == 1 && sample.intent.apply_tick == 4));
        assert_eq!(
            timeout
                .timed_out_native_observations
                .iter()
                .map(|sample| sample.evidence)
                .collect::<BTreeSet<_>>(),
            [
                NativeObservationEvidenceKind::Spotting,
                NativeObservationEvidenceKind::FiringLane,
            ]
            .into_iter()
            .collect()
        );
        assert!(matches!(
            timeout_runtime
                .accept_oracle_reply(reply(&timeout_first.oracle_requests[0], 10, 0.0))
                .unwrap(),
            OracleReplyDisposition::Dropped {
                reason: crate::oracle::OracleReplyDropReason::Late,
                ..
            }
        ));
    }

    #[test]
    fn player_muzzle_is_exactly_fenced_buffered_and_idempotent() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 9, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7]))
            .unwrap();
        let binding = fire_binding(7, 1);
        let request = match runtime.schedule_player_muzzle(binding.clone(), 0).unwrap() {
            PlayerMuzzleSchedule::New { request } => request,
            PlayerMuzzleSchedule::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(request.lineage(), lineage());
        assert_eq!(request.batch_seq, 1);
        assert_eq!(request.issued_tick, 0);
        assert_eq!(request.apply_tick, 3);
        assert_eq!(request.queries.len(), 1);
        assert_eq!(request.queries[0].query_id, 1);
        assert_eq!(request.queries[0].query_generation, 1);
        assert_eq!(request.queries[0].entity, entity(507));
        assert_eq!(request.queries[0].key, "player/7/muzzle/e507g1/l0");
        assert!(matches!(
            &request.queries[0].operation,
            OracleOperation::PlayerMuzzleEvidence(PlayerMuzzleEvidenceQuery {})
        ));

        assert_eq!(
            runtime.schedule_player_muzzle(binding.clone(), 0).unwrap(),
            PlayerMuzzleSchedule::ExactRetry {
                key: request.key(),
                apply_tick: 3,
            }
        );
        let mut conflict = binding.clone();
        conflict.shell_index = 1;
        assert!(matches!(
            runtime.schedule_player_muzzle(conflict, 0),
            Err(AuthorityRuntimeError::ConflictingPlayerMuzzleRetry {
                player_id: 7,
                intent_seq: 1,
            })
        ));
        assert!(matches!(
            runtime.schedule_player_muzzle(fire_binding(7, 2), 0),
            Err(AuthorityRuntimeError::PendingPlayerMuzzle { player_id: 7 })
        ));

        let mut forged = reply(&request, 1, 0.0);
        forged.results[0].query_generation += 1;
        assert!(matches!(
            runtime.accept_oracle_reply(forged),
            Err(AuthorityRuntimeError::Oracle(_))
        ));
        let native_reply = reply(&request, 1, 0.0);
        assert!(matches!(
            runtime.accept_oracle_reply(native_reply.clone()).unwrap(),
            OracleReplyDisposition::Buffered {
                key,
                apply_tick: 3,
            } if key == request.key()
        ));
        assert!(runtime
            .advance_tick(tick(1, &[]))
            .unwrap()
            .player_muzzles
            .is_empty());
        assert!(runtime
            .advance_tick(tick(2, &[]))
            .unwrap()
            .player_muzzles
            .is_empty());
        let third = runtime.advance_tick(tick(3, &[])).unwrap();
        assert!(third.failed_player_muzzles.is_empty());
        assert_eq!(third.player_muzzles.len(), 1);
        assert_eq!(third.player_muzzles[0].binding, binding);
        assert_eq!(third.player_muzzles[0].entity, entity(507));
        assert_eq!(third.player_muzzles[0].batch_key, request.key());
        assert_eq!(
            third.player_muzzles[0].transform,
            TransformSample {
                position: ProtocolVec3 {
                    x: 0.0,
                    y: 1.5,
                    z: 0.0,
                },
                basis: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            }
        );
        assert!(!third.player_muzzles[0].barrel_under_water);
        assert!(matches!(
            runtime.schedule_player_muzzle(binding.clone(), 0).unwrap(),
            PlayerMuzzleSchedule::ExactRetry { .. }
        ));
        assert!(matches!(
            runtime.accept_oracle_reply(native_reply).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { .. }
        ));
    }

    #[test]
    fn player_muzzle_timeout_is_typed_and_late_reply_is_dropped() {
        use crate::oracle::OracleReplyDropReason;

        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7]))
            .unwrap();
        let binding = fire_binding(7, 1);
        let request = match runtime.schedule_player_muzzle(binding.clone(), 0).unwrap() {
            PlayerMuzzleSchedule::New { request } => request,
            PlayerMuzzleSchedule::ExactRetry { .. } => unreachable!(),
        };
        runtime.advance_tick(tick(1, &[])).unwrap();
        runtime.advance_tick(tick(2, &[])).unwrap();
        let third = runtime.advance_tick(tick(3, &[])).unwrap();
        assert!(third.player_muzzles.is_empty());
        assert_eq!(third.failed_player_muzzles.len(), 1);
        assert_eq!(third.failed_player_muzzles[0].binding, binding);
        assert_eq!(third.failed_player_muzzles[0].entity, entity(507));
        assert_eq!(third.failed_player_muzzles[0].apply_tick, 3);
        assert_eq!(
            third.failed_player_muzzles[0].reason,
            PlayerMuzzleFailureReason::TimedOut
        );

        assert!(matches!(
            runtime
                .accept_oracle_reply(reply(&request, 1, 0.0))
                .unwrap(),
            OracleReplyDisposition::Dropped {
                reason: OracleReplyDropReason::Late,
                ..
            }
        ));
        assert!(matches!(
            runtime.schedule_player_muzzle(binding, 0).unwrap(),
            PlayerMuzzleSchedule::ExactRetry { .. }
        ));
        runtime.advance_tick(tick(4, &[])).unwrap();
        let next = runtime
            .schedule_player_muzzle(fire_binding(7, 2), 4)
            .unwrap();
        let PlayerMuzzleSchedule::New { request: next } = next else {
            unreachable!();
        };
        assert_eq!(next.batch_seq, 2);
        assert_eq!(next.queries[0].key, request.queries[0].key);
        assert_eq!(next.queries[0].query_generation, 2);
    }

    #[test]
    fn ram_contact_armor_is_atomic_frozen_exact_t_plus_three_and_fail_closed() {
        use crate::oracle::OracleReplyDropReason;

        let mut runtime =
            AuthorityRuntime::new(lineage(), 0, 1, vec![bot(7, Vec3::new(-1.0, 0.0, 0.0))])
                .unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[7], &[7]))
            .unwrap();
        let first_intent = ram_contact_intent(0, 1);
        let request = match runtime
            .schedule_ram_contact_armor(first_intent.clone())
            .unwrap()
        {
            RamContactArmorSchedule::New { request } => request,
            RamContactArmorSchedule::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(request.lineage(), lineage());
        assert_eq!(request.issued_tick, 0);
        assert_eq!(request.apply_tick, 3);
        assert_eq!(request.queries.len(), 1);
        assert_eq!(request.queries[0].entity, entity(507));
        assert_eq!(request.queries[0].query_generation, 1);
        let OracleOperation::RamContactArmorEvidence(arguments) = &request.queries[0].operation
        else {
            unreachable!()
        };
        assert_eq!(arguments.first, entity(507));
        assert_eq!(arguments.second, entity(107));
        assert_eq!(arguments.first_pose.position.x, 1.0);
        assert_eq!(arguments.first_pose.yaw, 0.25);
        assert_eq!(arguments.second_pose.position.x, -1.0);
        assert_eq!(arguments.contact_point.y, 0.5);
        assert_eq!(arguments.contact_normal.x, 1.0);

        assert_eq!(
            runtime
                .schedule_ram_contact_armor(first_intent.clone())
                .unwrap(),
            RamContactArmorSchedule::ExactRetry {
                key: request.key(),
                apply_tick: 3,
            }
        );
        let mut conflicting = first_intent.clone();
        conflicting.contact_point.y = 0.6;
        assert!(matches!(
            runtime.schedule_ram_contact_armor(conflicting),
            Err(AuthorityRuntimeError::ConflictingRamContactArmorRetry { .. })
        ));
        let concurrent = runtime
            .prepare_ram_contact_armor_batch(vec![ram_contact_intent(0, 2)])
            .unwrap();
        assert_eq!(concurrent.requests().len(), 1);
        assert_ne!(
            concurrent.requests()[0].queries[0].key,
            request.queries[0].key
        );

        let mut native_reply = reply(&request, 1, 0.0);
        native_reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::RamContactArmorEvidence(RamContactArmorEvidence {
                first_armor_mm: 70.0,
                second_armor_mm: 45.0,
            }),
        };
        assert!(matches!(
            runtime.accept_oracle_reply(native_reply.clone()).unwrap(),
            OracleReplyDisposition::Buffered {
                key,
                apply_tick: 3,
            } if key == request.key()
        ));
        assert!(runtime
            .advance_tick(tick(1, &[7]))
            .unwrap()
            .ram_contact_evidence
            .is_empty());
        assert!(runtime
            .advance_tick(tick(2, &[7]))
            .unwrap()
            .ram_contact_evidence
            .is_empty());
        let third = runtime.advance_tick(tick(3, &[7])).unwrap();
        assert_eq!(third.ram_contact_evidence.len(), 1);
        let evidence = third.ram_contact_evidence[0];
        assert_eq!(evidence.pair, first_intent.pair);
        assert_eq!(evidence.cursor, first_intent.cursor);
        assert_eq!(evidence.source_time_us, first_intent.source_time_us);
        assert_eq!(evidence.first.armor().millimeters(), 70.0);
        assert_eq!(evidence.second.armor().millimeters(), 45.0);
        assert_eq!(evidence.first.profile(), first_intent.first_profile);
        assert_eq!(evidence.second.profile(), first_intent.second_profile);
        assert!(evidence.first_moving);
        assert!(!evidence.second_moving);
        assert!(matches!(
            runtime
                .schedule_ram_contact_armor(first_intent.clone())
                .unwrap(),
            RamContactArmorSchedule::ExactRetry { .. }
        ));

        let mut stale = ram_contact_intent(3, 0);
        stale.cursor = RamSourceCursor::new(0, 0).unwrap();
        assert!(matches!(
            runtime.schedule_ram_contact_armor(stale),
            Err(AuthorityRuntimeError::StaleRamContactArmorCursor { .. })
        ));

        let unavailable_intent = ram_contact_intent(3, 2);
        let unavailable_request = match runtime
            .schedule_ram_contact_armor(unavailable_intent)
            .unwrap()
        {
            RamContactArmorSchedule::New { request } => request,
            RamContactArmorSchedule::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(unavailable_request.queries[0].query_generation, 1);
        assert_ne!(unavailable_request.queries[0].key, request.queries[0].key);
        let mut unavailable_reply = reply(&unavailable_request, 2, 0.0);
        unavailable_reply.results[0].status = OracleV1ResultStatus::Unavailable {
            code: "ram_contact_armor_unavailable".to_owned(),
            message: "second structural plate is unavailable".to_owned(),
        };
        runtime.accept_oracle_reply(unavailable_reply).unwrap();
        runtime.advance_tick(tick(4, &[7])).unwrap();
        runtime.advance_tick(tick(5, &[7])).unwrap();
        let unavailable = runtime.advance_tick(tick(6, &[7])).unwrap();
        assert!(unavailable.ram_contact_evidence.is_empty());

        let timeout_intent = ram_contact_intent(6, 3);
        let timeout_request = match runtime.schedule_ram_contact_armor(timeout_intent).unwrap() {
            RamContactArmorSchedule::New { request } => request,
            RamContactArmorSchedule::ExactRetry { .. } => unreachable!(),
        };
        runtime.advance_tick(tick(7, &[7])).unwrap();
        runtime.advance_tick(tick(8, &[7])).unwrap();
        let timeout = runtime.advance_tick(tick(9, &[7])).unwrap();
        assert!(timeout.ram_contact_evidence.is_empty());
        assert!(matches!(
            runtime
                .accept_oracle_reply(reply(&timeout_request, 3, 0.0))
                .unwrap(),
            OracleReplyDisposition::Dropped {
                reason: OracleReplyDropReason::Late,
                ..
            }
        ));
    }

    #[test]
    fn ram_contact_queries_are_batched_and_independent_source_episodes_coexist() {
        let mut runtime =
            AuthorityRuntime::new(lineage(), 0, 1, vec![bot(7, Vec3::new(-1.0, 0.0, 0.0))])
                .unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[7], &[7]))
            .unwrap();
        let first = ram_contact_intent(0, 1);
        let second = ram_contact_intent(0, 2);
        let prepared = runtime
            .prepare_ram_contact_armor_batch(vec![first.clone(), second.clone()])
            .unwrap();
        assert_eq!(prepared.requests().len(), 1);
        assert_eq!(prepared.requests()[0].queries.len(), 2);
        let request = runtime.commit_ram_contact_armor_batch(prepared).remove(0);
        runtime
            .accept_oracle_reply(reply(&request, 1, 0.0))
            .unwrap();
        runtime.advance_tick(tick(1, &[7])).unwrap();
        runtime.advance_tick(tick(2, &[7])).unwrap();
        let due = runtime.advance_tick(tick(3, &[7])).unwrap();
        assert_eq!(due.ram_contact_evidence.len(), 2);
        assert_eq!(due.ram_contact_evidence[0].cursor, first.cursor);
        assert_eq!(due.ram_contact_evidence[1].cursor, second.cursor);
    }

    #[test]
    fn player_environment_batch_is_actor_scoped_frozen_and_exact_t_plus_three() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7]))
            .unwrap();
        let first = runtime.advance_tick(tick_with_human(1, 7, 0.5)).unwrap();
        let request = first
            .oracle_requests
            .iter()
            .find(|request| {
                request.queries.iter().any(|query| {
                    matches!(query.operation, OracleOperation::GroundSampleBatch { .. })
                })
            })
            .unwrap()
            .clone();
        assert_eq!(request.issued_tick, 1);
        assert_eq!(request.apply_tick, 4);
        assert_eq!(request.queries.len(), 2);
        assert!(request
            .queries
            .iter()
            .all(|query| query.entity == entity(900)));

        let mut native = reply(&request, 1, 0.0);
        for (query, result) in request.queries.iter().zip(&mut native.results) {
            if matches!(query.operation, OracleOperation::WaterSampleBatch { .. }) {
                result.status = OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::WaterSampleBatch {
                        heights: vec![Some(2.5)],
                    },
                };
            }
        }
        assert!(matches!(
            runtime.accept_oracle_reply(native).unwrap(),
            OracleReplyDisposition::Buffered { apply_tick: 4, .. }
        ));
        assert!(runtime
            .advance_tick(tick_with_human(2, 7, 100.0))
            .unwrap()
            .player_environment
            .is_empty());
        assert!(runtime
            .advance_tick(tick_with_human(3, 7, 100.0))
            .unwrap()
            .player_environment
            .is_empty());
        let fourth = runtime.advance_tick(tick_with_human(4, 7, 100.0)).unwrap();
        assert_eq!(fourth.player_environment.len(), 1);
        let sample = fourth.player_environment[0];
        assert_eq!(sample.player_id, 7);
        assert_eq!(sample.evidence.issued_tick, 1);
        assert_eq!(sample.evidence.apply_tick, 4);
        assert_eq!(sample.evidence.pose.y, 0.5);
        assert_eq!(sample.evidence.pose.speed, 5.0);
        assert_eq!(sample.evidence.water_depth, Some(2.0));
        assert_eq!(
            sample.evidence.ground,
            Some(PlayerGroundEvidence {
                height: Some(0.0),
                supported: true,
            })
        );
    }

    #[test]
    fn unavailable_player_muzzle_is_a_typed_local_failure() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7]))
            .unwrap();
        let request = match runtime
            .schedule_player_muzzle(fire_binding(7, 1), 0)
            .unwrap()
        {
            PlayerMuzzleSchedule::New { request } => request,
            PlayerMuzzleSchedule::ExactRetry { .. } => unreachable!(),
        };
        let mut unavailable = reply(&request, 1, 0.0);
        unavailable.results[0].status = OracleV1ResultStatus::Unavailable {
            code: "muzzle_unavailable".to_owned(),
            message: "muzzle node is unavailable".to_owned(),
        };
        assert!(matches!(
            runtime.accept_oracle_reply(unavailable.clone()).unwrap(),
            OracleReplyDisposition::Buffered { .. }
        ));
        assert!(matches!(
            runtime.accept_oracle_reply(unavailable.clone()).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { .. }
        ));
        runtime.advance_tick(tick(1, &[])).unwrap();
        runtime.advance_tick(tick(2, &[])).unwrap();
        let third = runtime.advance_tick(tick(3, &[])).unwrap();
        assert!(third.player_muzzles.is_empty());
        assert_eq!(third.failed_player_muzzles.len(), 1);
        assert_eq!(third.failed_player_muzzles[0].binding, fire_binding(7, 1));
        assert_eq!(third.failed_player_muzzles[0].entity, entity(507));
        assert_eq!(third.failed_player_muzzles[0].batch_key, request.key());
        assert_eq!(
            third.failed_player_muzzles[0].reason,
            PlayerMuzzleFailureReason::Unavailable
        );
        assert!(matches!(
            runtime.accept_oracle_reply(unavailable).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { .. }
        ));
        runtime.advance_tick(tick(4, &[])).unwrap();
        assert!(matches!(
            runtime.schedule_player_muzzle(fire_binding(7, 2), 4),
            Ok(PlayerMuzzleSchedule::New { .. })
        ));
    }

    #[test]
    fn planner_hold_is_a_complete_typed_zero_throttle_order() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let manifest = planner_manifest(0.0, "SPG");
        let states = planner_bot_states();
        let players = json!([]);
        let result = runtime
            .build_planner_orders(PlannerBuildInput {
                manifest: &manifest,
                bot_states: &states,
                players: &players,
                now: 1.0,
                contacts: None,
                defense: None,
            })
            .unwrap();
        assert!(result.revision > 0);
        assert_eq!(result.orders.keys().copied().collect::<Vec<_>>(), vec![1]);
        assert_eq!(result.orders[&1].throttle, 0.0);
        assert_eq!(result.orders[&1].combat_mode, CombatMode::Hold);
        assert_eq!(result.traces[&1].combat_mode, "artillery_hold");
    }

    #[test]
    fn planner_route_becomes_direct_typed_movement() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let manifest = planner_manifest(100.0, "lightTank");
        let states = planner_bot_states();
        let players = json!([]);
        let result = runtime
            .build_planner_orders(PlannerBuildInput {
                manifest: &manifest,
                bot_states: &states,
                players: &players,
                now: 1.0,
                contacts: None,
                defense: None,
            })
            .unwrap();
        let order = &result.orders[&1];
        assert_eq!(order.combat_mode, CombatMode::Route);
        assert_eq!(order.throttle, 1.0);
        assert_eq!(order.turn, 0.0);
        assert_eq!(order.target_yaw, Some(0.0));
        assert_eq!(result.traces[&1].route_id, "lane");
        assert_eq!(result.traces[&1].move_position.z, 100.0);
    }

    #[test]
    fn cached_strategy_is_realised_against_each_fixed_tick_pose() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let forward = planner_manifest(100.0, "lightTank");
        let reverse = planner_manifest(-100.0, "lightTank");
        let players = json!([]);
        let first_states = planner_bot_states();
        let first = runtime
            .build_planner_orders_at_cadence(
                PlannerBuildInput {
                    manifest: &forward,
                    bot_states: &first_states,
                    players: &players,
                    now: 1.0,
                    contacts: None,
                    defense: None,
                },
                true,
            )
            .unwrap();

        let mut moved_states = planner_bot_states();
        moved_states[0]["x"] = json!(10.0);
        let cached = runtime
            .build_planner_orders_at_cadence(
                PlannerBuildInput {
                    manifest: &reverse,
                    bot_states: &moved_states,
                    players: &players,
                    now: 1.0 + 1.0 / 30.0,
                    contacts: None,
                    defense: None,
                },
                false,
            )
            .unwrap();

        assert_eq!(cached.revision, first.revision);
        assert_eq!(cached.traces[&1].move_position.z, 100.0);
        assert!(cached.orders[&1].target_yaw.unwrap() < 0.0);

        let refreshed = runtime
            .build_planner_orders_at_cadence(
                PlannerBuildInput {
                    manifest: &reverse,
                    bot_states: &moved_states,
                    players: &players,
                    now: 2.0,
                    contacts: None,
                    defense: None,
                },
                true,
            )
            .unwrap();
        assert_eq!(refreshed.traces[&1].move_position.z, -100.0);
    }

    #[test]
    fn cached_strategy_contains_death_until_the_next_refresh() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let manifest = planner_manifest(100.0, "mediumTank");
        let states = planner_bot_states();
        let contacts = planner_contact();
        let live_players = planner_player();
        let live = runtime
            .build_planner_orders_at_cadence(
                PlannerBuildInput {
                    manifest: &manifest,
                    bot_states: &states,
                    players: &live_players,
                    now: 1.0,
                    contacts: Some(&contacts),
                    defense: None,
                },
                true,
            )
            .unwrap();
        assert!(live.orders[&1].fire_allowed);

        let mut dead_players = planner_player();
        dead_players[0]["alive"] = json!(false);
        dead_players[0]["health"] = json!(0);
        let contained = runtime
            .build_planner_orders_at_cadence(
                PlannerBuildInput {
                    manifest: &manifest,
                    bot_states: &states,
                    players: &dead_players,
                    now: 1.0 + 1.0 / 30.0,
                    contacts: None,
                    defense: None,
                },
                false,
            )
            .unwrap();
        assert!(!contained.orders[&1].fire_allowed);
        assert!(!contained.orders[&1].target.as_ref().unwrap().alive);
    }

    #[test]
    fn ordinary_side_turn_keeps_moving_but_shallow_entry_aligns_first() {
        let current = PlannerVehicleState {
            team: 1,
            alive: true,
            health: 100,
            position: Vec3::ZERO,
            velocity: Vec3::ZERO,
            yaw: 0.0,
            speed: 0.0,
        };
        let yaw: f64 = 1.40;
        let target = Vec3::new(yaw.sin() * 100.0, 0.0, yaw.cos() * 100.0);
        let trace = PlannerOrderTrace {
            move_position: target,
            aim_position: None,
            face_position: None,
            combat_mode: "route".to_owned(),
            throttle_override: None,
            desired_range: 100.0,
            route_id: "lane".to_owned(),
            route_index: 0,
            route_anchor: Vec3::ZERO,
            route_join: false,
        };

        let mut drivers = BTreeMap::new();
        let ordinary = derive_local_order(
            1,
            &current,
            &trace,
            Some(NavTarget {
                point: target,
                controlled_shallow: false,
            }),
            1.0,
            &mut drivers,
        );
        assert_eq!(ordinary.0, 1.0);

        let mut drivers = BTreeMap::new();
        let shallow = derive_local_order(
            1,
            &current,
            &trace,
            Some(NavTarget {
                point: target,
                controlled_shallow: true,
            }),
            1.0,
            &mut drivers,
        );
        assert_eq!(shallow.0, 0.0);
    }

    #[test]
    fn stalled_route_inside_three_metres_still_reaches_recovery() {
        let current = PlannerVehicleState {
            team: 1,
            alive: true,
            health: 100,
            position: Vec3::ZERO,
            velocity: Vec3::ZERO,
            yaw: 0.0,
            speed: 0.0,
        };
        let trace = PlannerOrderTrace {
            move_position: Vec3::new(0.0, 0.0, 2.0),
            aim_position: None,
            face_position: None,
            combat_mode: "route".to_owned(),
            throttle_override: None,
            desired_range: 100.0,
            route_id: "lane".to_owned(),
            route_index: 1,
            route_anchor: Vec3::ZERO,
            route_join: false,
        };
        let mut drivers = BTreeMap::new();
        let mut recovered = false;
        for step in 0..=8 {
            let (_, _, _, mode, _) =
                derive_local_order(1, &current, &trace, None, step as f64 * 0.35, &mut drivers);
            recovered |= mode == RecoveryMode::PivotRecovery;
        }

        assert!(recovered);
    }

    #[test]
    fn planner_visible_lane_becomes_a_typed_fire_target() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let manifest = planner_manifest(100.0, "mediumTank");
        let states = planner_bot_states();
        let players = planner_player();
        let contacts = planner_contact();
        let result = runtime
            .build_planner_orders(PlannerBuildInput {
                manifest: &manifest,
                bot_states: &states,
                players: &players,
                now: 1.0,
                contacts: Some(&contacts),
                defense: None,
            })
            .unwrap();
        let order = &result.orders[&1];
        assert!(order.fire_allowed);
        assert_eq!(order.requested_shell_index, 0);
        assert!(matches!(
            order.target,
            Some(TargetState {
                network_id: 2,
                kind: TargetKind::Human,
                ..
            })
        ));
        assert_eq!(order.aim_position.unwrap().z, 100.0);
        assert_eq!(order.combat_mode, CombatMode::Engage);
    }

    #[test]
    fn planner_invalid_and_missing_inputs_fail_closed() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        let manifest = planner_manifest(100.0, "lightTank");
        let missing_states = json!([]);
        let players = json!([]);
        assert!(matches!(
            runtime.build_planner_orders(PlannerBuildInput {
                manifest: &manifest,
                bot_states: &missing_states,
                players: &players,
                now: 1.0,
                contacts: None,
                defense: None,
            }),
            Err(AuthorityRuntimeError::InvalidPlannerInput { .. })
        ));
        assert!(matches!(
            parse_combat_mode(1, "teleport"),
            Err(AuthorityRuntimeError::UnknownPlannerCombatMode { .. })
        ));
        let states = planner_bot_states();
        assert!(matches!(
            runtime.build_planner_orders(PlannerBuildInput {
                manifest: &manifest,
                bot_states: &states,
                players: &players,
                now: f64::NAN,
                contacts: None,
                defense: None,
            }),
            Err(AuthorityRuntimeError::InvalidPlannerInput { field: "now" })
        ));
    }

    #[test]
    fn bots_advance_in_id_order_and_oracle_receipts_apply_only_at_t_plus_three() {
        let mut runtime = AuthorityRuntime::new(
            lineage(),
            0,
            4,
            vec![bot(2, Vec3::new(2.0, 0.0, 0.0)), bot(1, Vec3::ZERO)],
        )
        .unwrap();
        runtime.donate_native_entities(donation(&[1, 2])).unwrap();

        let first = runtime.advance_tick(tick(1, &[1, 2])).unwrap();
        assert_eq!(
            first.bots.iter().map(|bot| bot.bot_id).collect::<Vec<_>>(),
            vec![1, 2]
        );
        assert_eq!(first.oracle_requests.len(), 2);
        assert!(first
            .oracle_requests
            .iter()
            .all(|request| request.issued_tick == 1 && request.apply_tick == 4));
        let adapter_request = first
            .oracle_requests
            .iter()
            .find(|request| {
                request.queries.iter().any(|query| {
                    matches!(query.operation, OracleOperation::GroundSampleBatch { .. })
                })
            })
            .unwrap();
        let first_reply = reply(adapter_request, 1, 6.0);
        for (index, request) in first.oracle_requests.iter().enumerate() {
            runtime
                .accept_oracle_reply(reply(request, index as u64 + 1, 6.0))
                .unwrap();
        }

        runtime.advance_tick(tick(2, &[1, 2])).unwrap();
        runtime.advance_tick(tick(3, &[1, 2])).unwrap();
        assert_eq!(runtime.bot_state(1).unwrap().position.y, 0.0);
        let fourth = runtime.advance_tick(tick(4, &[1, 2])).unwrap();
        assert_eq!(fourth.bots[0].pose.position.y, 6.0);

        assert!(matches!(
            runtime.accept_oracle_reply(first_reply).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { .. }
        ));
    }

    fn player_record(max_time_ms: u64) -> ProjectileRecord {
        player_record_kind(max_time_ms, false)
    }

    fn he_player_record(max_time_ms: u64) -> ProjectileRecord {
        player_record_kind(max_time_ms, true)
    }

    fn player_record_kind(max_time_ms: u64, is_he: bool) -> ProjectileRecord {
        let shooter = VehicleKey {
            kind: VehicleKind::Player,
            id: 1,
        };
        let launch = ProjectileLaunch {
            round_id: lineage().round_id,
            authority_epoch: lineage().authority_epoch,
            shooter,
            shot_seq: 1,
            shell_index: 0,
            origin: ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            velocity: ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            gravity: 9.81,
            max_distance: 720.0,
            max_time_ms,
            is_he,
            splash_radius: if is_he { 10.0 } else { 0.0 },
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot: SourceShot {
                speed: 100.0,
                gravity: 9.81,
                max_distance: 720.0,
                piercing_power: [100.0, 80.0],
                deadeye: false,
                shell: SourceShell {
                    kind: if is_he {
                        "HIGH_EXPLOSIVE".to_owned()
                    } else {
                        "ARMOR_PIERCING".to_owned()
                    },
                    caliber: 75.0,
                    damage: [100.0, if is_he { 40.0 } else { 0.0 }],
                    explosion_radius: if is_he { 10.0 } else { 0.0 },
                    explosion_damage_factor: is_he.then_some(0.5),
                    explosion_damage_absorption_factor: is_he.then_some(0.5),
                    explosion_edge_damage_factor: is_he.then_some(0.5),
                },
            },
            fire_intent_seq: Some(1),
            fire_input_seq: Some(1),
        };
        let context = LaunchContext {
            round_id: lineage().round_id,
            authority_epoch: lineage().authority_epoch,
            shooter,
            team: 1,
            source_vehicle: "ussr:R11_MS-1".to_owned(),
            expected_shot_seq: 1,
            server_time_ms: 0,
        };
        let mut ledger = ProjectileLedger::new();
        match ledger.admit_launch(launch, context).unwrap() {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        }
    }

    fn he_target(vehicle: VehicleKey, x: f32) -> HeExplosionEvidenceTargetIntent {
        HeExplosionEvidenceTargetIntent {
            vehicle,
            target_pose: ExplosionTargetPose {
                position: ProtocolVec3 { x, y: 0.0, z: 5.0 },
                yaw: 0.1,
                pitch: 0.02,
                roll: -0.01,
                turret_yaw: 0.2,
                gun_pitch: -0.05,
                siege_state: 0,
            },
        }
    }

    fn he_terminal(
        record: &ProjectileRecord,
        plan_id: ProjectilePlanId,
        applied_tick: Tick,
        impact: ProjectileVec3,
    ) -> ProjectileTerminalProposal {
        ProjectileTerminalProposal {
            plan_id,
            issued_tick: plan_id.issued_tick,
            applied_tick,
            cause: ProjectileTerminalCause::Terrain {
                native_hit: RayHit {
                    fraction: 0.5,
                    position: oracle_projectile_vec3(impact),
                    normal: ProtocolVec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0,
                    },
                    material_id: Some(1),
                    hit_entity: None,
                },
            },
            resolution: crate::projectile::ProjectileResolution {
                round_id: record.launch.round_id,
                authority_epoch: record.launch.authority_epoch,
                projectile_id: record.projectile_id.clone(),
                base_checked_ms: record.checked_through_ms,
                outcome: ProjectileOutcome::Impact,
                resolved_time_ms: 10,
                checked_distance: 1.0,
                piercing_loss: record.piercing_loss,
                penetration_factor: record.launch.penetration_factor,
                impact: Some(impact),
            },
            destructibles: Vec::new(),
        }
    }

    fn remember_he_terminal(
        runtime: &mut AuthorityRuntime,
        record: &ProjectileRecord,
        terminal: &ProjectileTerminalProposal,
    ) {
        runtime.he_projectile_sources.insert(
            record.projectile_id.clone(),
            he_projectile_source_binding(record).unwrap(),
        );
        runtime
            .remember_he_explosion_terminal(&ProjectileFlightDecision::Terminal(terminal.clone()));
    }

    #[test]
    fn he_explosion_evidence_is_terminal_bound_fenced_and_released_exact_t_plus_three() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7, 8]))
            .unwrap();
        let record = he_player_record(1_000);
        let plan_id = ProjectilePlanId {
            issued_tick: 0,
            projectile_ordinal: 41,
        };
        let impact = ProjectileVec3 {
            x: 0.0,
            y: 0.5,
            z: 0.0,
        };
        let terminal = he_terminal(&record, plan_id, 0, impact);
        runtime.track_projectile(record.clone(), 0).unwrap();
        runtime
            .remember_he_explosion_terminal(&ProjectileFlightDecision::Terminal(terminal.clone()));
        assert!(runtime.retire_projectile(&record.projectile_id));
        let intent = HeExplosionEvidenceIntent::from_terminal(
            &record,
            &terminal,
            vec![
                he_target(player_vehicle(8), 8.0),
                he_target(player_vehicle(7), 7.0),
            ],
        )
        .unwrap();
        let request = match runtime
            .schedule_he_explosion_evidence(intent.clone())
            .unwrap()
        {
            HeExplosionEvidenceSchedule::New { request } => request,
            HeExplosionEvidenceSchedule::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(request.lineage(), lineage());
        assert_eq!(request.issued_tick, 0);
        assert_eq!(request.apply_tick, 3);
        assert_eq!(request.queries.len(), 2);
        assert_eq!(request.queries[0].entity, entity(507));
        assert_eq!(request.queries[1].entity, entity(508));
        assert!(request
            .queries
            .iter()
            .all(|query| query.query_generation == 1));
        for query in &request.queries {
            let OracleOperation::ExplosionEvidence(arguments) = &query.operation else {
                unreachable!()
            };
            assert_eq!(arguments.target, query.entity);
            assert_eq!(arguments.impact, oracle_projectile_vec3(intent.impact));
            assert_eq!(
                arguments.incoming_direction,
                oracle_projectile_vec3(intent.incoming_direction)
            );
            assert_eq!(arguments.caliber_mm, 75.0);
        }
        assert_eq!(
            runtime
                .schedule_he_explosion_evidence(intent.clone())
                .unwrap(),
            HeExplosionEvidenceSchedule::ExactRetry {
                key: request.key(),
                apply_tick: 3,
            }
        );
        let mut conflicting = intent.clone();
        conflicting.targets[0].target_pose.yaw += 0.01;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(conflicting),
            Err(AuthorityRuntimeError::ConflictingHeExplosionEvidenceRetry { .. })
        ));

        let valid_reply = reply(&request, 1, 0.0);
        let mut wrong_generation = valid_reply.clone();
        wrong_generation.results[0].query_generation += 1;
        assert!(matches!(
            runtime.accept_oracle_reply(wrong_generation),
            Err(AuthorityRuntimeError::Oracle(_))
        ));
        let mut wrong_entity = valid_reply.clone();
        wrong_entity.results[0].entity.generation += 1;
        assert!(matches!(
            runtime.accept_oracle_reply(wrong_entity),
            Err(AuthorityRuntimeError::Oracle(_))
        ));
        let mut wrong_key = valid_reply.clone();
        wrong_key.results[0].key.push_str("/wrong");
        assert!(matches!(
            runtime.accept_oracle_reply(wrong_key),
            Err(AuthorityRuntimeError::Oracle(_))
        ));

        let mut reordered_reply = valid_reply;
        reordered_reply.results.reverse();
        assert!(matches!(
            runtime.accept_oracle_reply(reordered_reply).unwrap(),
            OracleReplyDisposition::Buffered {
                key,
                apply_tick: 3,
            } if key == request.key()
        ));
        assert!(runtime
            .advance_tick(tick(1, &[]))
            .unwrap()
            .he_explosion_evidence
            .is_empty());
        assert!(runtime
            .advance_tick(tick(2, &[]))
            .unwrap()
            .he_explosion_evidence
            .is_empty());
        let due = runtime.advance_tick(tick(3, &[])).unwrap();
        assert!(due.unavailable_he_explosions.is_empty());
        assert!(due.timed_out_he_explosions.is_empty());
        assert_eq!(due.he_explosion_evidence.len(), 1);
        let sample = &due.he_explosion_evidence[0];
        assert_eq!(sample.key.plan_id, plan_id);
        assert_eq!(sample.projectile_id, record.projectile_id);
        assert_eq!(sample.batch_key, request.key());
        assert_eq!(sample.targets.len(), 2);
        assert_eq!(sample.targets[0].vehicle, player_vehicle(7));
        assert_eq!(sample.targets[1].vehicle, player_vehicle(8));
        assert_eq!(sample.targets[0].query.target, entity(507));
        assert_eq!(
            sample.targets[0].evidence.target_pose,
            sample.targets[0].query.target_pose
        );
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(intent).unwrap(),
            HeExplosionEvidenceSchedule::ExactRetry { .. }
        ));
    }

    #[test]
    fn he_explosion_evidence_rejects_unbound_or_mixed_terminal_inputs() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7]))
            .unwrap();
        let record = he_player_record(1_000);
        let plan_id = ProjectilePlanId {
            issued_tick: 0,
            projectile_ordinal: 42,
        };
        let terminal = he_terminal(
            &record,
            plan_id,
            0,
            ProjectileVec3 {
                x: 0.0,
                y: 0.5,
                z: 0.0,
            },
        );
        let intent = HeExplosionEvidenceIntent::from_terminal(
            &record,
            &terminal,
            vec![he_target(player_vehicle(7), 7.0)],
        )
        .unwrap();
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(intent.clone()),
            Err(AuthorityRuntimeError::UnknownHeExplosionTerminal { plan_id: value })
                if value == plan_id
        ));

        remember_he_terminal(&mut runtime, &record, &terminal);
        let mut mixed_projectile = intent.clone();
        mixed_projectile.projectile_id.push_str(":other");
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(mixed_projectile),
            Err(AuthorityRuntimeError::HeExplosionTerminalMismatch { .. })
        ));
        let mut mixed_impact = intent.clone();
        mixed_impact.impact.x += 0.01;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(mixed_impact),
            Err(AuthorityRuntimeError::HeExplosionTerminalMismatch { .. })
        ));
        let mut mixed_direction = intent.clone();
        mixed_direction.incoming_direction.z -= 0.01;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(mixed_direction),
            Err(AuthorityRuntimeError::HeExplosionTerminalMismatch { .. })
        ));
        let mut mixed_caliber = intent.clone();
        mixed_caliber.caliber_mm += 1.0;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(mixed_caliber),
            Err(AuthorityRuntimeError::HeExplosionTerminalMismatch { .. })
        ));
        let mut wrong_window = intent.clone();
        wrong_window.apply_tick += 1;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(wrong_window),
            Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceIntent)
        ));
        let mut wrong_tick = intent.clone();
        wrong_tick.issued_tick = 1;
        wrong_tick.apply_tick = 4;
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(wrong_tick),
            Err(AuthorityRuntimeError::HeExplosionEvidenceTickMismatch { .. })
        ));
        let mut duplicate = intent.clone();
        duplicate.targets.push(duplicate.targets[0]);
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(duplicate),
            Err(AuthorityRuntimeError::InvalidHeExplosionEvidenceIntent)
        ));
        let mut undonated = intent;
        undonated.targets = vec![he_target(player_vehicle(99), 9.0)];
        assert!(matches!(
            runtime.schedule_he_explosion_evidence(undonated),
            Err(AuthorityRuntimeError::UndonatedHeExplosionTarget { .. })
        ));

        let ap_record = player_record(1_000);
        let ap_terminal = he_terminal(
            &ap_record,
            ProjectilePlanId {
                issued_tick: 0,
                projectile_ordinal: 43,
            },
            0,
            ProjectileVec3 {
                x: 0.0,
                y: 0.5,
                z: 0.0,
            },
        );
        assert!(HeExplosionEvidenceIntent::from_terminal(
            &ap_record,
            &ap_terminal,
            vec![he_target(player_vehicle(7), 7.0)]
        )
        .is_none());
    }

    #[test]
    fn he_explosion_unavailable_and_timeout_are_atomic_terminal_states() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[], &[7, 8]))
            .unwrap();
        let record = he_player_record(1_000);
        let first_plan = ProjectilePlanId {
            issued_tick: 0,
            projectile_ordinal: 44,
        };
        let first_terminal = he_terminal(
            &record,
            first_plan,
            0,
            ProjectileVec3 {
                x: 0.0,
                y: 0.5,
                z: 0.0,
            },
        );
        remember_he_terminal(&mut runtime, &record, &first_terminal);
        let first_intent = HeExplosionEvidenceIntent::from_terminal(
            &record,
            &first_terminal,
            vec![
                he_target(player_vehicle(7), 7.0),
                he_target(player_vehicle(8), 8.0),
            ],
        )
        .unwrap();
        let first_request = match runtime
            .schedule_he_explosion_evidence(first_intent.clone())
            .unwrap()
        {
            HeExplosionEvidenceSchedule::New { request } => request,
            HeExplosionEvidenceSchedule::ExactRetry { .. } => unreachable!(),
        };
        let mut partial = reply(&first_request, 1, 0.0);
        partial.results.reverse();
        partial.results[0].status = OracleV1ResultStatus::Unavailable {
            code: "explosion_layout_unavailable".to_owned(),
            message: "frozen component matrix unavailable".to_owned(),
        };
        runtime.accept_oracle_reply(partial).unwrap();
        runtime.advance_tick(tick(1, &[])).unwrap();
        runtime.advance_tick(tick(2, &[])).unwrap();
        let unavailable = runtime.advance_tick(tick(3, &[])).unwrap();
        assert!(unavailable.he_explosion_evidence.is_empty());
        assert_eq!(
            unavailable.unavailable_he_explosions,
            vec![HeExplosionEvidenceIntentKey {
                plan_id: first_plan
            }]
        );
        assert!(unavailable.timed_out_he_explosions.is_empty());
        assert!(matches!(
            runtime
                .schedule_he_explosion_evidence(first_intent)
                .unwrap(),
            HeExplosionEvidenceSchedule::ExactRetry { .. }
        ));

        let timeout_plan = ProjectilePlanId {
            issued_tick: 3,
            projectile_ordinal: 45,
        };
        let timeout_terminal = he_terminal(
            &record,
            timeout_plan,
            3,
            ProjectileVec3 {
                x: 1.0,
                y: 0.5,
                z: 0.0,
            },
        );
        remember_he_terminal(&mut runtime, &record, &timeout_terminal);
        let timeout_intent = HeExplosionEvidenceIntent::from_terminal(
            &record,
            &timeout_terminal,
            vec![he_target(player_vehicle(7), 7.0)],
        )
        .unwrap();
        let timeout_request = match runtime
            .schedule_he_explosion_evidence(timeout_intent.clone())
            .unwrap()
        {
            HeExplosionEvidenceSchedule::New { request } => request,
            HeExplosionEvidenceSchedule::ExactRetry { .. } => unreachable!(),
        };
        runtime.advance_tick(tick(4, &[])).unwrap();
        runtime.advance_tick(tick(5, &[])).unwrap();
        let timed_out = runtime.advance_tick(tick(6, &[])).unwrap();
        assert!(timed_out.he_explosion_evidence.is_empty());
        assert!(timed_out.unavailable_he_explosions.is_empty());
        assert_eq!(
            timed_out.timed_out_he_explosions,
            vec![HeExplosionEvidenceIntentKey {
                plan_id: timeout_plan
            }]
        );
        assert!(matches!(
            runtime
                .schedule_he_explosion_evidence(timeout_intent)
                .unwrap(),
            HeExplosionEvidenceSchedule::ExactRetry { .. }
        ));
        assert!(matches!(
            runtime
                .accept_oracle_reply(reply(&timeout_request, 2, 0.0))
                .unwrap(),
            OracleReplyDisposition::Dropped {
                reason: crate::oracle::OracleReplyDropReason::Late,
                ..
            }
        ));
    }

    #[test]
    fn player_bot_and_projectile_queries_share_one_batch_sequence() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, vec![bot(1, Vec3::ZERO)]).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&[1], &[1, 2]))
            .unwrap();
        runtime.install_destructible_native_space_id(91).unwrap();
        let player_request = match runtime
            .schedule_player_muzzle(fire_binding(2, 1), 0)
            .unwrap()
        {
            PlayerMuzzleSchedule::New { request } => request,
            PlayerMuzzleSchedule::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(player_request.batch_seq, 1);
        runtime.track_projectile(player_record(20), 0).unwrap();

        let first = runtime.advance_tick(tick(1, &[1])).unwrap();
        assert_eq!(
            first
                .oracle_requests
                .iter()
                .map(|request| request.batch_seq)
                .collect::<Vec<_>>(),
            vec![2, 3]
        );
        assert!(first.oracle_requests[0]
            .queries
            .iter()
            .all(|query| query.key.starts_with("bot/")));
        assert!(first.oracle_requests[1]
            .queries
            .iter()
            .all(|query| query.key.starts_with("pf:")));
        assert_eq!(runtime.next_batch_sequence(), 4);
    }

    #[test]
    fn destructible_hulls_cover_live_bot_and_human_roots_and_split_at_primitive_budget() {
        let bot_ids = (0..10).map(|index| 1 + index * 9).collect::<Vec<_>>();
        let bots = bot_ids
            .iter()
            .map(|id| bot(*id, Vec3::new(f64::from(*id), 0.0, 0.0)))
            .collect();
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 17, bots).unwrap();
        runtime
            .donate_native_entities(donation_with_humans(&bot_ids, &[20, 21]))
            .unwrap();
        runtime.install_destructible_native_space_id(91).unwrap();
        runtime
            .sync_bot_combat(
                *bot_ids.last().unwrap(),
                CanonicalBotCombatState {
                    health: 0,
                    display_health: 0,
                    alive: false,
                    death_reason: Some(0),
                    critical: CriticalState::default(),
                },
            )
            .unwrap();
        for bot_id in &bot_ids {
            runtime.bots.get_mut(bot_id).unwrap().state_mut().speed = 5.0;
        }
        runtime.advance_tick(tick(1, &bot_ids)).unwrap();
        runtime.advance_tick(tick(2, &bot_ids)).unwrap();
        let mut input = tick(3, &bot_ids);
        input.human_traffic = tick_with_human(3, 20, 2.0).human_traffic;
        let omitted_bot = bot_ids[bot_ids.len() - 2];
        input.world_pose_bots.remove(&omitted_bot);

        let output = runtime.advance_tick(input).unwrap();
        let requests = hull_requests(&output);
        assert_eq!(requests.len(), 2);
        assert_eq!(
            requests
                .iter()
                .map(|request| request.queries.len())
                .collect::<Vec<_>>(),
            vec![MAX_DESTRUCTIBLE_HULL_ACTORS_PER_BATCH, 1]
        );
        assert!(requests.iter().all(|request| {
            request.lineage() == lineage()
                && request.issued_tick == 3
                && request.apply_tick == 6
                && request.world_revision == 17
                && request
                    .queries
                    .iter()
                    .map(|query| query.operation.primitive_count())
                    .sum::<usize>()
                    <= MAX_ORACLE_PRIMITIVE_OPERATIONS
        }));
        let queries = requests
            .iter()
            .flat_map(|request| &request.queries)
            .collect::<Vec<_>>();
        assert_eq!(queries.len(), 9);
        let dead_bot = *bot_ids.last().unwrap();
        assert!(!queries.iter().any(|query| {
            query.entity == entity(100 + i64::from(omitted_bot))
                || query.entity == entity(100 + i64::from(dead_bot))
                || query.entity == entity(521)
        }));
        assert!(queries.iter().any(|query| {
            query.entity == entity(520)
                && matches!(
                    &query.operation,
                    OracleOperation::DestructibleHullEvidence(arguments)
                        if arguments.position == ProtocolVec3 { x: 10.0, y: 2.0, z: 20.0 }
                            && arguments.space_id == 91
                )
        }));
        for bot_id in bot_ids
            .into_iter()
            .filter(|bot_id| *bot_id != omitted_bot && *bot_id != dead_bot)
        {
            let native = entity(100 + i64::from(bot_id));
            let state = runtime.bot_state(bot_id).unwrap();
            assert!(queries.iter().any(|query| {
                query.entity == native
                    && matches!(
                        &query.operation,
                        OracleOperation::DestructibleHullEvidence(arguments)
                            if (f64::from(arguments.position.x) - state.position.x).abs() < 1.0e-5
                                && (f64::from(arguments.position.z) - state.position.z).abs() < 1.0e-5
                    )
            }));
        }
    }

    #[test]
    fn destructible_hull_evidence_is_frozen_released_at_t_plus_three_and_fail_closed() {
        let make_runtime = || {
            let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
            runtime
                .donate_native_entities(donation_with_humans(&[], &[7]))
                .unwrap();
            runtime.install_destructible_native_space_id(91).unwrap();
            runtime
        };

        let mut runtime = make_runtime();
        runtime.advance_tick(tick_with_human(1, 7, 3.0)).unwrap();
        let first = runtime.advance_tick(tick_with_human(2, 7, 3.0)).unwrap();
        let first_hull = hull_requests(&first)[0].clone();
        for (frame, request) in first.oracle_requests.iter().enumerate() {
            runtime
                .accept_oracle_reply(reply(request, frame as u64 + 1, 0.0))
                .unwrap();
        }
        assert!(runtime
            .advance_tick(tick_with_human(3, 7, 30.0))
            .unwrap()
            .destructible_hulls
            .is_empty());
        assert!(runtime
            .advance_tick(tick_with_human(4, 7, 30.0))
            .unwrap()
            .destructible_hulls
            .is_empty());
        let applied = runtime.advance_tick(tick_with_human(5, 7, 30.0)).unwrap();
        assert_eq!(applied.destructible_hulls.len(), 1);
        let sample = &applied.destructible_hulls[0];
        assert_eq!(sample.vehicle.kind, VehicleKind::Player);
        assert_eq!(sample.vehicle.id, 7);
        assert_eq!(sample.issued_tick, 2);
        assert_eq!(sample.apply_tick, 5);
        assert_eq!(sample.position, Vec3::new(10.0, 3.0, 20.0));
        assert_eq!(sample.batch_key, first_hull.key());
        let OracleOperation::DestructibleHullEvidence(arguments) = &first_hull.queries[0].operation
        else {
            unreachable!()
        };
        assert_eq!(sample.yaw, arguments.yaw);
        assert_eq!(sample.frame_travel, arguments.frame_travel);
        let expected_issued_speed = 3.0_f64 * 0.5_f64.sin() + 4.0_f64 * 0.5_f64.cos();
        assert!((sample.kinetic_speed - expected_issued_speed).abs() < 1.0e-12);

        let mut unavailable_runtime = make_runtime();
        unavailable_runtime
            .advance_tick(tick_with_human(1, 7, 3.0))
            .unwrap();
        let unavailable_first = unavailable_runtime
            .advance_tick(tick_with_human(2, 7, 3.0))
            .unwrap();
        for (frame, request) in unavailable_first.oracle_requests.iter().enumerate() {
            let mut oracle_reply = reply(request, frame as u64 + 1, 0.0);
            for result in &mut oracle_reply.results {
                if matches!(
                    result.status,
                    OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::DestructibleHullEvidence(..)
                    }
                ) {
                    result.status = OracleV1ResultStatus::Unavailable {
                        code: "destructible_evidence_unavailable".to_owned(),
                        message: "native hull sensor is unavailable".to_owned(),
                    };
                }
            }
            unavailable_runtime
                .accept_oracle_reply(oracle_reply)
                .unwrap();
        }
        unavailable_runtime
            .advance_tick(tick_with_human(3, 7, 3.0))
            .unwrap();
        unavailable_runtime
            .advance_tick(tick_with_human(4, 7, 3.0))
            .unwrap();
        assert!(unavailable_runtime
            .advance_tick(tick_with_human(5, 7, 3.0))
            .unwrap()
            .destructible_hulls
            .is_empty());

        let mut timeout_runtime = make_runtime();
        timeout_runtime
            .advance_tick(tick_with_human(1, 7, 3.0))
            .unwrap();
        let timeout_first = timeout_runtime
            .advance_tick(tick_with_human(2, 7, 3.0))
            .unwrap();
        timeout_runtime
            .advance_tick(tick_with_human(3, 7, 3.0))
            .unwrap();
        timeout_runtime
            .advance_tick(tick_with_human(4, 7, 3.0))
            .unwrap();
        assert!(timeout_runtime
            .advance_tick(tick_with_human(5, 7, 3.0))
            .unwrap()
            .destructible_hulls
            .is_empty());
        let timed_out_hull = hull_requests(&timeout_first)[0];
        assert!(matches!(
            timeout_runtime
                .accept_oracle_reply(reply(timed_out_hull, 20, 0.0))
                .unwrap(),
            OracleReplyDisposition::Dropped {
                reason: crate::oracle::OracleReplyDropReason::Late,
                ..
            }
        ));
    }

    #[test]
    fn thirty_bot_native_workload_stays_inside_the_sixty_hz_bridge_budget() {
        const BOT_COUNT: u32 = 30;
        const TEST_TICKS: Tick = 180;
        const RENDER_FRAMES_PER_TICK: usize = 2;
        const BATCHES_PER_RENDER: usize = 4;

        let bot_ids = (1..=BOT_COUNT).collect::<Vec<_>>();
        let bots = bot_ids
            .iter()
            .map(|id| {
                let pair = (*id - 1) / 2;
                let position = Vec3::new(
                    f64::from(pair) * 20.0,
                    0.0,
                    if id % 2 == 0 { 100.0 } else { 0.0 },
                );
                bot(*id, position)
            })
            .collect::<Vec<_>>();
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 17, bots).unwrap();
        runtime.donate_native_entities(donation(&bot_ids)).unwrap();
        runtime.install_destructible_native_space_id(91).unwrap();
        for bot_id in &bot_ids {
            runtime.bots.get_mut(bot_id).unwrap().state_mut().speed = 6.0;
        }

        let mut bridge_queue = VecDeque::<OracleV1BatchRequest>::new();
        let mut total_batches = 0usize;
        let mut total_primitives = 0usize;
        let mut frame_sequence = 0u64;
        let mut max_queued_batches = 0usize;
        let mut ballistic_queries = 0usize;
        let mut visibility_queries = 0usize;
        let mut destructible_hull_queries = 0usize;
        for tick_value in 1..=TEST_TICKS {
            // Keep a stable moving-target signature while exercising every
            // moving-actor hull phase; copied physics still owns the step.
            for bot_id in &bot_ids {
                runtime.bots.get_mut(bot_id).unwrap().state_mut().speed = 6.0;
            }
            let mut tick_input = tick(tick_value, &bot_ids);
            tick_input.orders = bot_ids
                .iter()
                .map(|bot_id| {
                    let target_id = if bot_id % 2 == 0 {
                        bot_id - 1
                    } else {
                        bot_id + 1
                    };
                    let target = runtime.bot_state(target_id).unwrap();
                    let velocity = Vec3::new(
                        target.yaw.sin() * target.speed,
                        0.0,
                        target.yaw.cos() * target.speed,
                    );
                    (
                        *bot_id,
                        BotOrder {
                            throttle: 0.0,
                            turn: 0.0,
                            target_yaw: Some(0.0),
                            aim_position: Some(target.position),
                            target: Some(TargetState {
                                network_id: target_id,
                                kind: TargetKind::Bot,
                                team: target.team,
                                alive: target.alive,
                                health: target.health,
                                position: target.position,
                                velocity,
                                yaw: target.yaw,
                                speed: target.speed,
                            }),
                            fire_allowed: true,
                            fire_range: 560.0,
                            requested_shell_index: 0,
                            recovery_mode: RecoveryMode::Drive,
                            combat_mode: CombatMode::Engage,
                            navigation_target: None,
                        },
                    )
                })
                .collect();
            let output = runtime.advance_tick(tick_input).unwrap();
            ballistic_queries += output
                .oracle_requests
                .iter()
                .flat_map(|request| &request.queries)
                .filter(|query| query.key.contains("/ballistic/"))
                .count();
            visibility_queries += output
                .oracle_requests
                .iter()
                .flat_map(|request| &request.queries)
                .filter(|query| query.key.contains("/visibility/"))
                .count();
            destructible_hull_queries += output
                .oracle_requests
                .iter()
                .flat_map(|request| &request.queries)
                .filter(|query| {
                    matches!(
                        query.operation,
                        OracleOperation::DestructibleHullEvidence(..)
                    )
                })
                .count();
            total_batches += output.oracle_requests.len();
            total_primitives += output
                .oracle_requests
                .iter()
                .flat_map(|request| &request.queries)
                .map(|query| query.operation.primitive_count())
                .sum::<usize>();
            bridge_queue.extend(output.oracle_requests);
            max_queued_batches = max_queued_batches.max(bridge_queue.len());

            for _ in 0..RENDER_FRAMES_PER_TICK {
                let mut frame_batches = 0usize;
                let mut frame_primitives = 0usize;
                while frame_batches < BATCHES_PER_RENDER {
                    let Some(request) = bridge_queue.front() else {
                        break;
                    };
                    let primitives = request
                        .queries
                        .iter()
                        .map(|query| query.operation.primitive_count())
                        .sum::<usize>();
                    if frame_batches > 0
                        && frame_primitives + primitives > MAX_ORACLE_PRIMITIVE_OPERATIONS
                    {
                        break;
                    }
                    let request = bridge_queue.pop_front().unwrap();
                    frame_sequence += 1;
                    assert!(matches!(
                        runtime
                            .accept_oracle_reply(reply(&request, frame_sequence, 0.0))
                            .unwrap(),
                        OracleReplyDisposition::Buffered { .. }
                    ));
                    frame_batches += 1;
                    frame_primitives += primitives;
                }
            }
            assert!(
                bridge_queue.is_empty(),
                "native bridge queue grew after fixed tick {tick_value}"
            );
        }

        assert!(max_queued_batches <= RENDER_FRAMES_PER_TICK * BATCHES_PER_RENDER);
        assert!(ballistic_queries > 0);
        assert!(visibility_queries > 0);
        assert_eq!(
            destructible_hull_queries as u64,
            u64::from(BOT_COUNT) * TEST_TICKS * DESTRUCTIBLE_HULL_SAMPLES_PER_CYCLE
                / DESTRUCTIBLE_HULL_CADENCE_TICKS
        );
        eprintln!(
            "30-bot oracle workload: {total_primitives} primitives, {total_batches} batches, max queue {max_queued_batches}"
        );
        assert!(
            total_primitives * 30 <= MAX_ORACLE_PRIMITIVE_OPERATIONS * 60 * TEST_TICKS as usize
        );
        assert!(total_batches * 30 <= BATCHES_PER_RENDER * 60 * TEST_TICKS as usize);
    }

    #[test]
    fn projectile_clear_geometry_becomes_a_terminal_proposal_not_damage() {
        let mut runtime = AuthorityRuntime::new(lineage(), 0, 1, Vec::new()).unwrap();
        let mut native = donation(&[]);
        native.humans.insert(1, entity(101));
        runtime.donate_native_entities(native).unwrap();
        runtime.install_destructible_native_space_id(91).unwrap();
        runtime.track_projectile(player_record(20), 0).unwrap();

        let first = runtime.advance_tick(tick(1, &[])).unwrap();
        assert_eq!(first.oracle_requests.len(), 1);
        assert!(first.oracle_requests[0]
            .queries
            .iter()
            .all(|query| query.key.starts_with("pf:")));
        runtime
            .accept_oracle_reply(reply(&first.oracle_requests[0], 1, 0.0))
            .unwrap();
        runtime.advance_tick(tick(2, &[])).unwrap();
        runtime.advance_tick(tick(3, &[])).unwrap();
        let fourth = runtime.advance_tick(tick(4, &[])).unwrap();
        let terminals: Vec<_> = fourth.projectile_terminals().collect();
        assert_eq!(terminals.len(), 1);
        assert_eq!(terminals[0].resolution.outcome, ProjectileOutcome::Expired);
        assert!(matches!(
            terminals[0].cause,
            ProjectileTerminalCause::MaxTime
        ));
    }
}
