//! Fixed-tick admission loop between the LAN transport and [`BattleEngine`].
//!
//! Socket threads assign one process-wide receive sequence. This module keeps
//! those messages frozen until the next 30 Hz boundary, then applies them in
//! receive order before advancing the authoritative simulation tick.

use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::PI;
use std::time::{Duration, Instant};

use serde_json::Value;
use thiserror::Error;

use crate::authority_runtime::{
    AuthorityProjectileTarget, AuthorityRuntime, AuthorityRuntimeError, AuthorityTickInput,
    BotRamDelta, CanonicalBotCombatState, HeExplosionEvidenceIntent, HeExplosionEvidenceIntentKey,
    HeExplosionEvidenceSchedule, HeExplosionEvidenceTargetIntent, NativeHeExplosionEvidenceSample,
    PlannerBuildInput, PlayerMuzzleFailureReason, PlayerMuzzleSchedule, PreparedBotRamMutation,
    PreparedRamContactArmorBatch, RamContactArmorIntent, RamContactArmorIntentKey,
};
use crate::battle::{
    BattleEngine, BattleError, BattleTickOutput, EnvironmentDamageEffect, PlayerInput,
    ProjectileDamageEffect, ProjectileTerminal,
};
use crate::bot_sim::{
    angle_delta, fixed_dt_us, CombatEvent as BotCombatEvent, CriticalState as BotCriticalState,
    DeathCause, DeviceCondition as BotDeviceCondition, GunYawLimits, TargetKind, TrafficBody,
    Vec3 as BotVec3, TICK_RATE_HZ,
};
use crate::clock::tick_offset;
use crate::combat::{
    BodyPose, DamageProposal, DamageSource, FireIntentAdmission, FireIntentRequest, VehicleKey,
    VehicleKind,
};
use crate::combat_rules::{
    he_radius, resolve_direct_hit_with_base_multiplier, ArmorLayer, CombatRuleError, MaterialInfo,
    PenetrationVerdict, ShellKind, ShotFactors, ShotInfo,
};
use crate::contact_authority::{
    CanonicalContact, ContactActorState, ContactAuthority, ContactAuthorityError,
};
use crate::critical_damage::{
    CrewName, CriticalDamageError, CriticalSamples, CriticalShell, CriticalShellKind, CriticalStat,
    CriticalState as VehicleCriticalState, CriticalTarget, CriticalTrace,
    DeviceCondition as VehicleDeviceCondition, DeviceName, StrikeInput,
};
use crate::descriptor::AuthoritySpottingInput;
use crate::descriptor_exchange::DestructibleMapDonation;
use crate::destructible::{DestructibleAuthority, InstalledDestructibleCatalog};
use crate::input::{InputAdmission, PoseState, MAX_INPUT_FINGERPRINTS, POSE_MAX_SAMPLE_GAP_US};
use crate::player_ammo::{
    deterministic_intuition_success, PhysicalBurstAdmission, PhysicalBurstClock,
    PhysicalBurstDescriptor, PhysicalBurstEdge, PhysicalBurstError, PlayerAmmoBurst,
    PlayerAmmoError, PlayerAmmoIntent, PlayerAmmoIntentAction, PlayerAmmoIntentAdmission,
    PlayerAmmoIntentOutcome, PlayerAmmoLaunch, PlayerAmmoLaunchAdmission, PlayerAmmoLedger,
    PlayerAmmoSnapshot,
};
use crate::player_environment::{
    LandingObservationContext, LandingObservationRequest, LandingObservationResult,
    PlayerEnvironmentCause, PlayerEnvironmentError, PlayerEnvironmentLedger,
    PlayerEnvironmentSnapshot, PlayerEnvironmentTick,
};
use crate::player_equipment::{
    decode_equipment_intent, BotEquipmentLedger, EquipmentEffect, EquipmentIntentResult,
    EquipmentPassiveEffects, EquipmentStateSnapshot, PlayerEquipmentContext, PlayerEquipmentError,
    PlayerEquipmentLedger, PlayerEquipmentSnapshot,
};
use crate::player_fire_clock::{
    Direction3, EffectiveDispersionFactors, PlayerFireClock, PlayerFireClockError,
    PlayerFireClockSnapshot, PlayerFireLineage, PlayerGunDispersionLaw, PlayerGunMotion,
};
use crate::projectile::{
    build_first_ricochet, LaunchAdmission, ProjectileLaunch, ProjectileOutcome, ProjectileRecord,
    ProjectileStunError, ProjectileVec3, RicochetAdmission, SourceShot, MAX_PROJECTILE_LIFETIME_MS,
};
use crate::projectile_sim::{
    critical_trace_from_vehicle_hit, frozen_he_target_from_explosion_evidence,
    resolve_frozen_he_splash, FrozenHeSplash, ProjectileFlightDecision, ProjectileFlightError,
    ProjectileTerminalCause, ProjectileTerminalProposal, MAX_FROZEN_HE_TARGETS,
};
use crate::protocol::{
    ExplosionTargetPose, OracleV1BatchKey, OracleV1BatchReply, OracleV1BatchRequest,
    RamContactPose, SimulationScope, Tick, Vec3 as OracleVec3, VehicleHit, VehicleHitLayer,
    ORACLE_PIPELINE_TICKS,
};
use crate::ram::{
    AtomicRamDamage, NativeRamContactEvidence, PlayerPairRamReceipt, PlayerRamLedgerState,
    PlayerRamProjection, PlayerRamReceipt, RamAuthority, RamBody, RamBodyDelta, RamContactProbe,
    RamDamageProfile, RamError, RamPair, RamPoseAdmission, RamPoseFrame, RamPoseTimeline,
    RamResolution, RamShape, RamSourceCursor,
};
use crate::room::Team;
use crate::rules::{StandardRules, VehicleKey as RulesVehicleKey};
use crate::sim::{IngressError, IngressQueue, TickController, TickError};
use crate::spotting::{
    ContactTargetKind, ORDINARY_SHOT_LANE_RANGE_METRES, SPG_SHOT_LANE_RANGE_METRES,
};
use crate::wire::{ConnectionId, WireObject};

const MAX_SEQUENCE: u64 = 2_147_483_647;
const MAX_RAM_CONTACTS: usize = 16;
const MAX_PLAYER_FIRE_RECEIPTS: usize = MAX_INPUT_FINGERPRINTS;
const STRATEGY_REFRESH_TICKS: Tick = TICK_RATE_HZ;

fn strategy_refresh_due(last_refresh: Option<Tick>, tick: Tick) -> bool {
    last_refresh.is_none_or(|last| tick.saturating_sub(last) >= STRATEGY_REFRESH_TICKS)
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerFireAuthorityInput {
    pub law: PlayerGunDispersionLaw,
    pub static_factors: EffectiveDispersionFactors,
    pub turret_rotation_speed_rad_s: f64,
    pub crew_factor: f64,
    pub yaw_limits: GunYawLimits,
}

#[derive(Clone, Debug, PartialEq)]
pub struct InputSideEffects {
    pub player_id: u64,
    pub ram_contacts: Vec<Value>,
    pub ram_contacts_envelope_valid: bool,
    pub player_ram_contacts: Vec<Value>,
    pub player_ram_contacts_envelope_valid: bool,
    pub siege_enabled: Option<bool>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CommandEffect {
    PlayerInput(InputSideEffects),
    FireIntent {
        player_id: u64,
        admission: FireIntentAdmission,
    },
    AmmoIntent {
        connection_id: ConnectionId,
        scope: SimulationScope,
        player_id: u64,
        intent: PlayerAmmoIntent,
        outcome: PlayerAmmoIntentOutcome,
    },
    LandingObservation {
        connection_id: ConnectionId,
        player_id: u64,
        result: LandingObservationResult,
    },
    EquipmentIntent {
        player_id: u64,
        result: EquipmentIntentResult,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandRejection {
    pub connection_id: ConnectionId,
    pub code: &'static str,
    pub message: String,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct BattleLoopOutput {
    pub ticks: Vec<BattleTickOutput>,
    pub effects: Vec<CommandEffect>,
    pub rejections: Vec<CommandRejection>,
    pub oracle_requests: Vec<OracleV1BatchRequest>,
    /// Canonical team contacts evaluated at this fixed tick. Socket/client
    /// presentation wiring consumes this separately from planner JSON.
    pub contacts: Vec<CanonicalContact>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PlayerBurstSnapshot {
    pub active: bool,
    pub group_seq: u64,
    pub count: u16,
    pub next_index: u16,
    pub interval_seconds: f64,
    pub time_left_seconds: f64,
    pub shell_index: u8,
}

#[derive(Clone, Debug)]
struct PlayerBurstTemplate {
    launch: ProjectileLaunch,
    aim_direction: ProjectileVec3,
    descriptor: PhysicalBurstDescriptor,
    start_time_us: u64,
    count: u16,
}

#[derive(Clone, Copy, Debug)]
struct PlayerFireMotionState {
    last_source_time_us: Option<u64>,
    last_hull_yaw: Option<f64>,
    linear_speed_mps: f64,
    hull_angular_speed_rad_s: f64,
    desired_world_yaw: f64,
    actual_relative_turret_yaw: f64,
    source_motion_ready: bool,
}

#[derive(Clone, Copy, Debug)]
struct PlayerFireRuntime {
    clock: PlayerFireClock,
    static_factors: EffectiveDispersionFactors,
    turret_rotation_speed_rad_s: f64,
    crew_factor: f64,
    yaw_limits: GunYawLimits,
    motion: PlayerFireMotionState,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct PlayerFireShotReceipt {
    intent_seq: u64,
    physical_round_index: u16,
    aim_direction: ProjectileVec3,
    sampled_direction: ProjectileVec3,
    final_round: bool,
}

#[derive(Clone, Copy, Debug)]
struct PreparedPlayerFireShot {
    direction: ProjectileVec3,
    next_clock: Option<PlayerFireClock>,
    receipt: PlayerFireShotReceipt,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct BotRamEpisodeState {
    episode: u64,
    overlapping: bool,
    damaging: bool,
    pending: Option<RamSourceCursor>,
}

#[derive(Clone, Debug, PartialEq)]
struct PendingHeTerminal {
    terminal: ProjectileTerminalProposal,
    record: ProjectileRecord,
    intent: HeExplosionEvidenceIntent,
    batch_key: OracleV1BatchKey,
    direct_target: Option<VehicleKey>,
}

#[derive(Clone, Debug)]
enum DueHeTerminal {
    Evidence(NativeHeExplosionEvidenceSample),
    Unavailable(HeExplosionEvidenceIntentKey),
    TimedOut(HeExplosionEvidenceIntentKey),
}

impl DueHeTerminal {
    fn key(&self) -> HeExplosionEvidenceIntentKey {
        match self {
            Self::Evidence(sample) => sample.key,
            Self::Unavailable(key) | Self::TimedOut(key) => *key,
        }
    }
}

#[derive(Clone, Debug)]
enum DueRamContact {
    Evidence(NativeRamContactEvidence),
    Unavailable(RamContactArmorIntentKey),
    TimedOut(RamContactArmorIntentKey),
}

impl DueRamContact {
    fn key(&self) -> RamContactArmorIntentKey {
        match self {
            Self::Evidence(evidence) => RamContactArmorIntentKey {
                pair: evidence.pair,
                cursor: evidence.cursor,
            },
            Self::Unavailable(key) | Self::TimedOut(key) => *key,
        }
    }
}

#[derive(Clone, Debug)]
struct PlayerBurstTick {
    clock: PhysicalBurstClock,
    edges: Vec<PhysicalBurstEdge>,
}

#[derive(Clone, Debug, Default, PartialEq)]
struct AuthorityAdvanceOutput {
    oracle_requests: Vec<OracleV1BatchRequest>,
    contacts: Vec<CanonicalContact>,
}

#[derive(Clone, Debug, Default)]
struct DueProjectileApplication {
    he_terminals: Vec<(ProjectileTerminalProposal, ProjectileRecord)>,
    ricochet_records: Vec<ProjectileRecord>,
}

#[derive(Clone, Debug)]
struct QueuedCommand {
    connection_id: ConnectionId,
    player_id: u64,
    scope: SimulationScope,
    message: WireObject,
}

#[derive(Debug, Error)]
pub enum BattleLoopError {
    #[error(transparent)]
    Ingress(#[from] IngressError),
    #[error(transparent)]
    Tick(#[from] TickError),
    #[error(transparent)]
    Battle(#[from] BattleError),
    #[error(transparent)]
    Authority(#[from] AuthorityRuntimeError),
    #[error(transparent)]
    CombatRule(#[from] CombatRuleError),
    #[error(transparent)]
    ProjectileFlight(#[from] ProjectileFlightError),
    #[error(transparent)]
    Ram(#[from] RamError),
    #[error(transparent)]
    Contact(#[from] ContactAuthorityError),
    #[error(transparent)]
    PlayerAmmo(#[from] PlayerAmmoError),
    #[error(transparent)]
    PlayerFireClock(#[from] PlayerFireClockError),
    #[error(transparent)]
    PhysicalBurst(#[from] PhysicalBurstError),
    #[error(transparent)]
    PlayerEnvironment(#[from] PlayerEnvironmentError),
    #[error(transparent)]
    PlayerEquipment(#[from] PlayerEquipmentError),
    #[error("native player environment evidence is duplicated or references an unknown actor")]
    InvalidPlayerEnvironmentEvidence,
    #[error("native destructible hull evidence is duplicated or has the wrong apply tick")]
    InvalidDestructibleHullEvidence,
    #[error("native ram contact evidence is duplicated, stale, or has no frozen impact")]
    InvalidRamContactEvidence,
    #[error(
        "native HE explosion evidence is duplicated, stale, or mismatches its frozen terminal"
    )]
    InvalidHeExplosionEvidence,
    #[error("projectile and player ammunition admissions diverged")]
    PlayerAmmoTransactionMismatch,
    #[error("projectile and player fire-clock admissions diverged")]
    PlayerFireTransactionMismatch,
    #[error("the Rust bot planner manifest is unavailable")]
    MissingPlannerManifest,
}

/// One live round. All methods are called by the server's single state owner.
pub struct BattleLoop {
    engine: BattleEngine,
    anchor: Instant,
    controller: TickController,
    ingress: IngressQueue<QueuedCommand>,
    authority: Option<AuthorityRuntime>,
    contact_authority: ContactAuthority,
    player_ammo: BTreeMap<u64, PlayerAmmoLedger>,
    player_burst_descriptors: BTreeMap<u64, PhysicalBurstDescriptor>,
    player_burst_clocks: BTreeMap<u64, PhysicalBurstClock>,
    player_burst_templates: BTreeMap<u64, PlayerBurstTemplate>,
    player_burst_snapshots: BTreeMap<u64, PlayerBurstSnapshot>,
    player_fire: BTreeMap<u64, PlayerFireRuntime>,
    player_fire_receipts: BTreeMap<(u64, u64), PlayerFireShotReceipt>,
    player_environment: BTreeMap<u64, PlayerEnvironmentLedger>,
    player_equipment: BTreeMap<u64, PlayerEquipmentLedger>,
    bot_equipment: BTreeMap<u64, BotEquipmentLedger>,
    ram: RamAuthority,
    player_ram_timeline: RamPoseTimeline,
    mounted_shots: BTreeMap<VehicleKey, BTreeMap<u8, SourceShot>>,
    hull_materials: BTreeMap<VehicleKey, Vec<MaterialInfo>>,
    vehicle_extents: BTreeMap<VehicleKey, (f64, f64)>,
    vehicle_ram_shapes: BTreeMap<VehicleKey, RamShape>,
    vehicle_masses: BTreeMap<VehicleKey, f64>,
    destructible_catalog: Option<InstalledDestructibleCatalog>,
    vehicle_ram_profiles: BTreeMap<VehicleKey, RamDamageProfile>,
    pending_ram_contacts: BTreeMap<RamContactArmorIntentKey, RamContactProbe>,
    pending_he_terminals: BTreeMap<HeExplosionEvidenceIntentKey, PendingHeTerminal>,
    bot_ram_contact_episodes: BTreeMap<RamPair, BotRamEpisodeState>,
    planner_manifest: Option<Value>,
    last_strategy_refresh_tick: Option<Tick>,
    bot_firing_lane_ranges: BTreeMap<VehicleKey, f64>,
    terminal: bool,
}

impl BattleLoop {
    pub fn new(engine: BattleEngine) -> Self {
        Self::with_anchor(engine, Instant::now())
    }

    pub fn with_anchor(engine: BattleEngine, anchor: Instant) -> Self {
        Self {
            engine,
            anchor,
            controller: TickController::new(),
            ingress: IngressQueue::new(),
            authority: None,
            contact_authority: ContactAuthority::new(),
            player_ammo: BTreeMap::new(),
            player_burst_descriptors: BTreeMap::new(),
            player_burst_clocks: BTreeMap::new(),
            player_burst_templates: BTreeMap::new(),
            player_burst_snapshots: BTreeMap::new(),
            player_fire: BTreeMap::new(),
            player_fire_receipts: BTreeMap::new(),
            player_environment: BTreeMap::new(),
            player_equipment: BTreeMap::new(),
            bot_equipment: BTreeMap::new(),
            ram: RamAuthority::new(),
            player_ram_timeline: RamPoseTimeline::new(),
            mounted_shots: BTreeMap::new(),
            hull_materials: BTreeMap::new(),
            vehicle_extents: BTreeMap::new(),
            vehicle_ram_shapes: BTreeMap::new(),
            vehicle_masses: BTreeMap::new(),
            destructible_catalog: None,
            vehicle_ram_profiles: BTreeMap::new(),
            pending_ram_contacts: BTreeMap::new(),
            pending_he_terminals: BTreeMap::new(),
            bot_ram_contact_episodes: BTreeMap::new(),
            planner_manifest: None,
            last_strategy_refresh_tick: None,
            bot_firing_lane_ranges: BTreeMap::new(),
            terminal: false,
        }
    }

    pub fn install_authority(
        &mut self,
        authority: AuthorityRuntime,
        mounted_shots: BTreeMap<VehicleKey, BTreeMap<u8, SourceShot>>,
        hull_materials: BTreeMap<VehicleKey, Vec<MaterialInfo>>,
        vehicle_extents: BTreeMap<VehicleKey, (f64, f64)>,
        vehicle_ram_shapes: BTreeMap<VehicleKey, RamShape>,
        vehicle_masses: BTreeMap<VehicleKey, f64>,
        vehicle_ram_profiles: BTreeMap<VehicleKey, RamDamageProfile>,
        planner_manifest: Value,
    ) -> Result<(), BattleLoopError> {
        let current_tick = self.controller.completed_tick();
        if authority.current_tick() != current_tick {
            return Err(BattleError::InvalidVehicle.into());
        }
        let roster = self
            .engine
            .entities()
            .map(|entity| entity.key)
            .collect::<BTreeSet<_>>();
        if vehicle_ram_shapes.keys().copied().collect::<BTreeSet<_>>() != roster
            || vehicle_ram_profiles
                .keys()
                .copied()
                .collect::<BTreeSet<_>>()
                != roster
        {
            return Err(BattleError::InvalidVehicle.into());
        }
        let player_environment = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| {
                Ok((
                    entity.key.id,
                    PlayerEnvironmentLedger::new(entity.key.id, current_tick)?,
                ))
            })
            .collect::<Result<BTreeMap<_, _>, PlayerEnvironmentError>>()?;
        self.contact_authority.bind_lineage(authority.lineage())?;
        self.bot_firing_lane_ranges = planner_bot_firing_lane_ranges(&planner_manifest);
        self.mounted_shots = mounted_shots;
        self.hull_materials = hull_materials;
        self.vehicle_extents = vehicle_extents;
        self.vehicle_ram_shapes = vehicle_ram_shapes;
        self.vehicle_masses = vehicle_masses;
        self.vehicle_ram_profiles = vehicle_ram_profiles;
        self.planner_manifest = Some(planner_manifest);
        self.last_strategy_refresh_tick = None;
        let bodies = self.ram_bodies(&authority)?;
        let bots = bodies
            .into_iter()
            .filter(|body| body.key.kind == VehicleKind::Bot)
            .collect::<Vec<_>>();
        self.ram.record_bot_frame(0, 0, &bots)?;
        self.player_environment = player_environment;
        self.authority = Some(authority);
        Ok(())
    }

    /// Freeze the exact map donation into the active Rust round. Native hull
    /// queries may only refer to this catalog; they cannot donate health or a
    /// kinetic verdict later through query replies.
    pub fn install_destructible_catalog(
        &mut self,
        donation: DestructibleMapDonation,
    ) -> Result<(), BattleLoopError> {
        if self.destructible_catalog.is_some() || donation.round_id != self.engine.scope().round_id
        {
            return Err(
                BattleError::from(crate::destructible::DestructibleError::InvalidCatalog).into(),
            );
        }
        self.destructible_catalog =
            Some(InstalledDestructibleCatalog::from_donation(donation).map_err(BattleError::from)?);
        Ok(())
    }

    /// Install only actor-scoped, exactly donated spotting inputs. Partial
    /// maps are intentional: an omitted human observer or target remains
    /// fail-closed instead of borrowing the descriptor donor's loadout.
    pub fn install_spotting_inputs(
        &mut self,
        inputs: BTreeMap<VehicleKey, AuthoritySpottingInput>,
    ) -> Result<(), BattleLoopError> {
        let roster = self
            .engine
            .entities()
            .map(|entity| entity.key)
            .collect::<BTreeSet<_>>();
        if inputs.keys().any(|vehicle| !roster.contains(vehicle)) {
            return Err(BattleError::InvalidVehicle.into());
        }
        self.contact_authority.install_inputs(inputs)?;
        self.authority
            .as_mut()
            .ok_or(BattleError::OracleUnavailable)?
            .install_spotting_observers(self.contact_authority.spotting_vehicles())?;
        let tick = self.controller.completed_tick();
        let actors = self.contact_actor_states();
        self.contact_authority
            .record_observation_frame(tick, &actors)?;
        Ok(())
    }

    pub fn contact_authority(&self) -> &ContactAuthority {
        &self.contact_authority
    }

    /// Install one exact garage-backed ammunition ledger for every player.
    pub fn install_player_ammo(
        &mut self,
        ledgers: BTreeMap<u64, PlayerAmmoLedger>,
        burst_descriptors: BTreeMap<u64, PhysicalBurstDescriptor>,
    ) -> Result<(), BattleLoopError> {
        let expected = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| entity.key.id)
            .collect::<BTreeSet<_>>();
        let current_tick = self.controller.completed_tick();
        if ledgers.keys().copied().collect::<BTreeSet<_>>() != expected
            || burst_descriptors.keys().copied().collect::<BTreeSet<_>>() != expected
            || ledgers.iter().any(|(player_id, ledger)| {
                ledger.player_id() != *player_id
                    || ledger.tick() != current_tick
                    || ledger.loaded_shell() > 9
            })
        {
            return Err(BattleError::InvalidVehicle.into());
        }
        let player_burst_clocks = ledgers
            .iter()
            .map(|(&player_id, ledger)| {
                (
                    player_id,
                    PhysicalBurstClock::new(current_tick, ledger.last_shot_seq()),
                )
            })
            .collect();
        let player_burst_snapshots = ledgers
            .iter()
            .map(|(&player_id, ledger)| {
                let ammo = ledger.snapshot();
                (
                    player_id,
                    PlayerBurstSnapshot {
                        active: false,
                        group_seq: 0,
                        count: 0,
                        next_index: 0,
                        interval_seconds: 0.0,
                        time_left_seconds: 0.0,
                        shell_index: ammo.loaded_shell,
                    },
                )
            })
            .collect();
        for (&player_id, ledger) in &ledgers {
            self.engine
                .synchronize_player_shell(player_id, ledger.loaded_shell())?;
        }
        self.player_ammo = ledgers;
        self.player_burst_descriptors = burst_descriptors;
        self.player_burst_clocks = player_burst_clocks;
        self.player_burst_snapshots = player_burst_snapshots;
        Ok(())
    }

    pub fn player_ammo_snapshot(&self, player_id: u64) -> Option<PlayerAmmoSnapshot> {
        self.player_ammo
            .get(&player_id)
            .map(PlayerAmmoLedger::snapshot)
    }

    pub fn player_burst_snapshot(&self, player_id: u64) -> Option<PlayerBurstSnapshot> {
        self.player_burst_snapshots.get(&player_id).cloned()
    }

    /// Install descriptor law plus actor-scoped effective gun factors for
    /// every visible player. Native muzzle transforms remain geometry facts;
    /// this Rust clock owns convergence, motion bloom and physical-shot bloom.
    pub fn install_player_fire(
        &mut self,
        inputs: BTreeMap<u64, PlayerFireAuthorityInput>,
    ) -> Result<(), BattleLoopError> {
        let expected = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| entity.key.id)
            .collect::<BTreeSet<_>>();
        if inputs.keys().copied().collect::<BTreeSet<_>>() != expected {
            return Err(BattleError::InvalidVehicle.into());
        }
        let tick = self.controller.completed_tick();
        let mut runtimes = BTreeMap::new();
        for (player_id, input) in inputs {
            input.law.validate()?;
            input.static_factors.validate()?;
            if !input.turret_rotation_speed_rad_s.is_finite()
                || !(0.000_001..=2.0 * PI).contains(&input.turret_rotation_speed_rad_s)
                || !input.crew_factor.is_finite()
                || !(0.01..=16.0).contains(&input.crew_factor)
                || !input.yaw_limits.minimum.is_finite()
                || !input.yaw_limits.maximum.is_finite()
                || input.yaw_limits.minimum < -PI
                || input.yaw_limits.maximum > PI
                || input.yaw_limits.minimum > input.yaw_limits.maximum
            {
                return Err(BattleError::InvalidVehicle.into());
            }
            let pose = self
                .engine
                .body_pose(VehicleKey {
                    kind: VehicleKind::Player,
                    id: player_id,
                })
                .ok_or(BattleError::InvalidVehicle)?;
            let desired_relative =
                clamp_player_turret_yaw(angle_delta(pose.aim_yaw, pose.yaw), input.yaw_limits);
            runtimes.insert(
                player_id,
                PlayerFireRuntime {
                    clock: PlayerFireClock::new(input.law, input.static_factors, tick)?,
                    static_factors: input.static_factors,
                    turret_rotation_speed_rad_s: input.turret_rotation_speed_rad_s,
                    crew_factor: input.crew_factor,
                    yaw_limits: input.yaw_limits,
                    motion: PlayerFireMotionState {
                        last_source_time_us: None,
                        last_hull_yaw: None,
                        linear_speed_mps: pose.speed,
                        hull_angular_speed_rad_s: 0.0,
                        desired_world_yaw: pose.aim_yaw,
                        actual_relative_turret_yaw: desired_relative,
                        source_motion_ready: false,
                    },
                },
            );
        }
        self.player_fire = runtimes;
        self.player_fire_receipts.clear();
        Ok(())
    }

    pub fn player_fire_snapshot(&self, player_id: u64) -> Option<PlayerFireClockSnapshot> {
        self.player_fire
            .get(&player_id)
            .map(|runtime| runtime.clock.snapshot())
    }

    pub fn player_environment_snapshot(&self, player_id: u64) -> Option<PlayerEnvironmentSnapshot> {
        self.player_environment
            .get(&player_id)
            .map(PlayerEnvironmentLedger::snapshot)
    }

    /// Install one exact effective-params equipment ledger for every player.
    pub fn install_player_equipment(
        &mut self,
        ledgers: BTreeMap<u64, PlayerEquipmentLedger>,
    ) -> Result<(), BattleLoopError> {
        let expected = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| entity.key.id)
            .collect::<BTreeSet<_>>();
        if ledgers.keys().copied().collect::<BTreeSet<_>>() != expected
            || ledgers.iter().any(|(player_id, ledger)| {
                ledger.player_id() != *player_id || ledger.clock_seconds() != 0.0
            })
        {
            return Err(BattleError::InvalidVehicle.into());
        }
        self.engine.install_player_equipment_fire_factors(
            ledgers
                .iter()
                .map(|(&player_id, ledger)| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Player,
                            id: player_id,
                        },
                        ledger.passive_effects().fire_starting_chance_factor,
                    )
                })
                .collect(),
        )?;
        self.player_equipment = ledgers;
        Ok(())
    }

    pub fn player_equipment_snapshot(&self, player_id: u64) -> Option<PlayerEquipmentSnapshot> {
        self.player_equipment
            .get(&player_id)
            .map(PlayerEquipmentLedger::snapshot)
    }

    pub fn player_equipment_passive_effects(
        &self,
        player_id: u64,
    ) -> Option<EquipmentPassiveEffects> {
        self.player_equipment
            .get(&player_id)
            .map(PlayerEquipmentLedger::passive_effects)
    }

    /// Install one independent exact hidden-oracle consumable ledger for
    /// every Rust-simulated bot.
    pub fn install_bot_equipment(
        &mut self,
        ledgers: BTreeMap<u64, BotEquipmentLedger>,
    ) -> Result<(), BattleLoopError> {
        let expected = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Bot)
            .map(|entity| entity.key.id)
            .collect::<BTreeSet<_>>();
        if ledgers.keys().copied().collect::<BTreeSet<_>>() != expected
            || ledgers
                .iter()
                .any(|(bot_id, ledger)| ledger.bot_id() != *bot_id || ledger.clock_seconds() != 0.0)
        {
            return Err(BattleError::InvalidVehicle.into());
        }
        self.engine.install_bot_equipment_fire_factors(
            ledgers
                .iter()
                .map(|(&bot_id, ledger)| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Bot,
                            id: bot_id,
                        },
                        ledger.passive_effects().fire_starting_chance_factor,
                    )
                })
                .collect(),
        )?;
        self.bot_equipment = ledgers;
        Ok(())
    }

    pub fn bot_equipment_snapshot(&self, bot_id: u64) -> Option<Vec<EquipmentStateSnapshot>> {
        self.bot_equipment
            .get(&bot_id)
            .map(BotEquipmentLedger::snapshot)
    }

    pub fn landing_observation_seq(&self, player_id: u64) -> Option<u64> {
        self.player_environment
            .get(&player_id)
            .map(PlayerEnvironmentLedger::landing_observation_seq)
    }

    pub fn authority(&self) -> Option<&AuthorityRuntime> {
        self.authority.as_ref()
    }

    pub fn authority_mut(&mut self) -> Option<&mut AuthorityRuntime> {
        self.authority.as_mut()
    }

    pub fn accept_oracle_reply(
        &mut self,
        reply: OracleV1BatchReply,
    ) -> Result<(), BattleLoopError> {
        self.authority
            .as_mut()
            .ok_or(BattleError::OracleUnavailable)?
            .accept_oracle_reply(reply)?;
        Ok(())
    }

    pub fn engine(&self) -> &BattleEngine {
        &self.engine
    }

    pub fn engine_mut(&mut self) -> &mut BattleEngine {
        &mut self.engine
    }

    pub fn ram_player_projection(&self, player_id: u64) -> PlayerRamProjection {
        self.ram.player_projection(player_id)
    }

    pub fn player_pair_ram_state(&self, player_id: u64) -> PlayerRamLedgerState {
        self.ram.player_pair_ledger_state(player_id)
    }

    pub fn elapsed(&self) -> Duration {
        self.anchor.elapsed()
    }

    pub fn is_terminal(&self) -> bool {
        self.terminal
    }

    pub fn mark_terminal(&mut self) {
        self.terminal = true;
    }

    pub fn timeout_until_next_tick(&self, maximum: Duration) -> Duration {
        if self.terminal {
            return maximum;
        }
        let deadline = tick_offset(self.controller.completed_tick().saturating_add(1));
        maximum.min(deadline.saturating_sub(self.elapsed()))
    }

    pub fn enqueue_player_message(
        &mut self,
        recv_seq: u64,
        connection_id: ConnectionId,
        player_id: u64,
        scope: SimulationScope,
        message: WireObject,
    ) -> Result<(), BattleLoopError> {
        self.ingress.push(
            recv_seq,
            QueuedCommand {
                connection_id,
                player_id,
                scope,
                message,
            },
        )?;
        Ok(())
    }

    pub fn poll(&mut self, server_time_ms: u64) -> Result<BattleLoopOutput, BattleLoopError> {
        self.poll_elapsed(self.elapsed(), server_time_ms)
    }

    pub fn poll_elapsed(
        &mut self,
        elapsed: Duration,
        server_time_ms: u64,
    ) -> Result<BattleLoopOutput, BattleLoopError> {
        if self.terminal {
            return Ok(BattleLoopOutput::default());
        }
        // Native queries must leave this process between logical ticks. A
        // multi-tick catch-up batch could otherwise create and time out the
        // same exact-T+3 request before the socket sees it.
        let Some(batch) = self.controller.poll_with_limit(elapsed, 1)? else {
            return Ok(BattleLoopOutput::default());
        };
        let mut output = BattleLoopOutput::default();
        for step in batch.iter() {
            self.advance_player_ammo(step.tick)?;
            let player_burst_ticks = self.stage_player_burst_ticks(step.tick)?;
            let mut player_muzzles = Vec::new();
            if step.tick == batch.first_tick {
                for (_, command) in self.ingress.drain_ordered() {
                    match self.apply_command(command, step.time_us, server_time_ms) {
                        Ok(effect) => {
                            if let CommandEffect::FireIntent {
                                admission: FireIntentAdmission::New(binding),
                                ..
                            } = &effect
                            {
                                player_muzzles.push(binding.clone());
                            }
                            output.effects.push(effect);
                        }
                        Err(rejection) => output.rejections.push(rejection),
                    }
                }
            }
            self.advance_player_fire_clocks(step.tick)?;
            let authority_output =
                self.advance_authority(step.tick, server_time_ms, player_burst_ticks)?;
            output
                .oracle_requests
                .extend(authority_output.oracle_requests);
            output.contacts.extend(authority_output.contacts);
            if self.engine.result().is_none() {
                for binding in player_muzzles {
                    if !self.player_physical_fire_accepting(binding.player_id)? {
                        self.engine.reject_player_fire_intent(
                            self.engine.scope(),
                            binding.player_id,
                            binding.intent_seq,
                            "player_fire_gate_closed_before_muzzle_schedule",
                        )?;
                        continue;
                    }
                    let authority = self
                        .authority
                        .as_mut()
                        .ok_or(BattleError::OracleUnavailable)?;
                    match authority.schedule_player_muzzle(binding, step.tick)? {
                        PlayerMuzzleSchedule::New { request } => {
                            output.oracle_requests.push(request);
                        }
                        PlayerMuzzleSchedule::ExactRetry { .. } => {}
                    }
                }
                self.advance_player_equipment(step.tick)?;
            } else {
                for binding in player_muzzles {
                    self.engine.reject_player_fire_intent(
                        self.engine.scope(),
                        binding.player_id,
                        binding.intent_seq,
                        "battle_ended_before_muzzle_schedule",
                    )?;
                }
            }
            let tick = self.engine.advance_tick_at(server_time_ms)?;
            debug_assert_eq!(tick.tick, step.tick);
            self.terminal = tick.result.is_some();
            output.ticks.push(tick);
            if self.terminal {
                break;
            }
        }
        Ok(output)
    }

    fn advance_player_ammo(&mut self, tick: u64) -> Result<(), BattleLoopError> {
        let factors = self
            .player_ammo
            .keys()
            .map(|player_id| {
                let key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: *player_id,
                };
                self.engine
                    .critical_stat_factor(key, CriticalStat::Reload)
                    .map(|factor| (*player_id, factor))
                    .ok_or(BattleError::InvalidVehicle)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let mut staged = self.player_ammo.clone();
        for (player_id, factor) in factors {
            staged
                .get_mut(&player_id)
                .expect("player ammunition roster was frozen")
                .advance_tick(tick, factor)?;
        }
        for (&player_id, ledger) in &staged {
            self.engine
                .synchronize_player_shell(player_id, ledger.loaded_shell())?;
        }
        self.player_ammo = staged;
        Ok(())
    }

    fn record_player_fire_input(
        &mut self,
        player_id: u64,
        source_time_us: Option<u64>,
        pose: Option<PoseState>,
        desired_world_yaw: f64,
    ) {
        let Some(runtime) = self.player_fire.get_mut(&player_id) else {
            return;
        };
        runtime.motion.desired_world_yaw = desired_world_yaw;
        let (Some(source_time_us), Some(pose)) = (source_time_us, pose) else {
            return;
        };
        let previous = runtime
            .motion
            .last_source_time_us
            .zip(runtime.motion.last_hull_yaw);
        runtime.motion.last_source_time_us = Some(source_time_us);
        runtime.motion.last_hull_yaw = Some(pose.yaw);
        runtime.motion.linear_speed_mps = pose.speed;
        let Some((previous_time_us, previous_yaw)) = previous else {
            runtime.motion.source_motion_ready = false;
            runtime.motion.hull_angular_speed_rad_s = 0.0;
            return;
        };
        let Some(dt_us) = source_time_us.checked_sub(previous_time_us) else {
            runtime.motion.source_motion_ready = false;
            runtime.motion.hull_angular_speed_rad_s = 0.0;
            return;
        };
        if dt_us == 0 || dt_us > POSE_MAX_SAMPLE_GAP_US {
            runtime.motion.source_motion_ready = false;
            runtime.motion.hull_angular_speed_rad_s = 0.0;
            return;
        }
        let angular_speed = angle_delta(pose.yaw, previous_yaw) / (dt_us as f64 / 1_000_000.0);
        if !angular_speed.is_finite() || angular_speed.abs() > 4.0 * PI {
            runtime.motion.source_motion_ready = false;
            runtime.motion.hull_angular_speed_rad_s = 0.0;
            return;
        }
        runtime.motion.hull_angular_speed_rad_s = angular_speed;
        runtime.motion.source_motion_ready = true;
    }

    fn advance_player_fire_clocks(&mut self, tick: u64) -> Result<(), BattleLoopError> {
        let mut staged = self.player_fire.clone();
        for (&player_id, runtime) in &mut staged {
            let key = VehicleKey {
                kind: VehicleKind::Player,
                id: player_id,
            };
            let pose = self
                .engine
                .body_pose(key)
                .ok_or(BattleError::InvalidVehicle)?;
            let mut dynamic_factors = runtime.static_factors;
            dynamic_factors.dispersion_factor *= self
                .engine
                .critical_stat_factor(key, CriticalStat::Dispersion)
                .ok_or(BattleError::InvalidVehicle)?;
            dynamic_factors.aiming_time_factor *= self
                .engine
                .critical_stat_factor(key, CriticalStat::AimTime)
                .ok_or(BattleError::InvalidVehicle)?;
            let turret_critical = self
                .engine
                .critical_stat_factor(key, CriticalStat::TurretSpeed)
                .ok_or(BattleError::InvalidVehicle)?;
            let desired_relative = clamp_player_turret_yaw(
                angle_delta(runtime.motion.desired_world_yaw, pose.yaw),
                runtime.yaw_limits,
            );
            let dt_seconds = fixed_dt_us(tick) as f64 / 1_000_000.0;
            let max_rate =
                runtime.turret_rotation_speed_rad_s * runtime.crew_factor * turret_critical;
            if !max_rate.is_finite() || max_rate <= 0.0 || max_rate > 4.0 * PI {
                return Err(PlayerFireClockError::OutOfRange("turret_traverse_rate").into());
            }
            let turret_delta =
                angle_delta(desired_relative, runtime.motion.actual_relative_turret_yaw)
                    .clamp(-max_rate * dt_seconds, max_rate * dt_seconds);
            runtime.motion.actual_relative_turret_yaw = clamp_player_turret_yaw(
                runtime.motion.actual_relative_turret_yaw + turret_delta,
                runtime.yaw_limits,
            );
            runtime.clock.advance_to_tick(
                tick,
                PlayerGunMotion {
                    linear_speed_mps: runtime.motion.linear_speed_mps,
                    hull_angular_speed_rad_s: runtime.motion.hull_angular_speed_rad_s,
                    turret_angular_speed_rad_s: turret_delta / dt_seconds,
                },
                dynamic_factors,
            )?;
        }
        self.player_fire = staged;
        Ok(())
    }

    fn player_fire_motion_ready(&self, player_id: u64) -> bool {
        let Some(runtime) = self.player_fire.get(&player_id) else {
            return true;
        };
        let Some(source_time_us) = runtime.motion.last_source_time_us else {
            return false;
        };
        runtime.motion.source_motion_ready
            && crate::bot_sim::time_us_at_tick(self.controller.completed_tick())
                .abs_diff(source_time_us)
                <= POSE_MAX_SAMPLE_GAP_US
    }

    fn player_physical_fire_accepting(&self, player_id: u64) -> Result<bool, BattleLoopError> {
        self.engine
            .player_physical_fire_accepting(player_id)
            .map_err(BattleLoopError::from)
    }

    fn stage_player_burst_ticks(
        &self,
        tick: u64,
    ) -> Result<BTreeMap<u64, PlayerBurstTick>, BattleLoopError> {
        self.player_burst_clocks
            .iter()
            .map(|(&player_id, clock)| {
                let mut staged = clock.clone();
                let edges = staged.advance_tick(tick)?;
                Ok((
                    player_id,
                    PlayerBurstTick {
                        clock: staged,
                        edges,
                    },
                ))
            })
            .collect()
    }

    fn commit_player_burst_ticks(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
        server_time_ms: u64,
        staged_ticks: BTreeMap<u64, PlayerBurstTick>,
    ) -> Result<(), BattleLoopError> {
        for (player_id, staged_tick) in staged_ticks {
            if !self.player_physical_fire_accepting(player_id)? {
                let mut canceled = self
                    .player_burst_clocks
                    .get(&player_id)
                    .cloned()
                    .ok_or(BattleError::InvalidVehicle)?;
                canceled.cancel();
                debug_assert!(canceled.advance_tick(tick)?.is_empty());
                self.player_ammo
                    .get_mut(&player_id)
                    .ok_or(BattleError::InvalidVehicle)?
                    .cancel_physical_burst()?;
                self.player_burst_clocks.insert(player_id, canceled);
                if let Some(snapshot) = self.player_burst_snapshots.get_mut(&player_id) {
                    snapshot.active = false;
                    snapshot.time_left_seconds = 0.0;
                }
                self.player_burst_templates.remove(&player_id);
                continue;
            }
            if staged_tick.edges.is_empty() {
                self.player_burst_clocks
                    .insert(player_id, staged_tick.clock);
                self.refresh_player_burst_snapshot(player_id, tick)?;
                continue;
            }
            let template = self
                .player_burst_templates
                .get(&player_id)
                .cloned()
                .ok_or(BattleError::InvalidProjectileEffects)?;
            for edge in &staged_tick.edges {
                self.commit_player_burst_edge(authority, tick, server_time_ms, &template, *edge)?;
            }
            let active = staged_tick.clock.active();
            self.player_burst_clocks
                .insert(player_id, staged_tick.clock);
            self.refresh_player_burst_snapshot(player_id, tick)?;
            if !active {
                self.player_burst_templates.remove(&player_id);
            }
        }
        Ok(())
    }

    fn cancel_all_player_bursts(&mut self, tick: u64) -> Result<(), BattleLoopError> {
        let player_ids = self.player_burst_clocks.keys().copied().collect::<Vec<_>>();
        for player_id in player_ids {
            let mut clock = self
                .player_burst_clocks
                .get(&player_id)
                .cloned()
                .ok_or(BattleError::InvalidVehicle)?;
            clock.cancel();
            if clock.current_tick() < tick {
                debug_assert!(clock.advance_tick(tick)?.is_empty());
            } else if clock.current_tick() != tick {
                return Err(PhysicalBurstError::TickSequence.into());
            }
            self.player_ammo
                .get_mut(&player_id)
                .ok_or(BattleError::InvalidVehicle)?
                .cancel_physical_burst()?;
            self.player_burst_clocks.insert(player_id, clock);
            self.player_burst_templates.remove(&player_id);
            self.refresh_player_burst_snapshot(player_id, tick)?;
        }
        Ok(())
    }

    fn commit_player_burst_edge(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
        server_time_ms: u64,
        template: &PlayerBurstTemplate,
        edge: PhysicalBurstEdge,
    ) -> Result<(), BattleLoopError> {
        if edge.burst_index == 0
            || edge.burst_count != template.count
            || edge.burst_group_seq != template.launch.shot_seq
        {
            return Err(BattleError::InvalidProjectileEffects.into());
        }
        let player_id = template.launch.shooter.id;
        let mut projectile = template.launch.clone();
        projectile.shot_seq = edge.shot_seq;
        let prepared_fire = self.prepare_player_fire_shot(
            player_id,
            projectile
                .fire_intent_seq
                .ok_or(BattleError::InvalidProjectileEffects)?,
            edge.shot_seq,
            edge.burst_index,
            template.aim_direction,
            edge.final_round(),
        )?;
        let direction = prepared_fire
            .as_ref()
            .map_or(template.aim_direction, |prepared| prepared.direction);
        projectile.velocity = ProjectileVec3 {
            x: direction.x * projectile.source_shot.speed,
            y: direction.y * projectile.source_shot.speed,
            z: direction.z * projectile.source_shot.speed,
        };
        projectile.penetration_factor =
            deterministic_shot_factor(self.engine.scope(), projectile.shooter, edge.shot_seq, 0);
        projectile.damage_factor =
            deterministic_shot_factor(self.engine.scope(), projectile.shooter, edge.shot_seq, 1);
        let burst = PlayerAmmoBurst::from_edge(edge);
        let mut staged_ammo = self
            .player_ammo
            .get(&player_id)
            .cloned()
            .ok_or(BattleError::InvalidVehicle)?;
        let ammo_admission = staged_ammo.admit_physical_launch(
            PlayerAmmoLaunch {
                shot_seq: edge.shot_seq,
                input_seq: projectile
                    .fire_input_seq
                    .ok_or(BattleError::InvalidProjectileEffects)?,
                shell_index: edge.shell_index,
            },
            burst,
        )?;
        let admission = self.engine.commit_player_burst_continuation(
            self.engine.scope(),
            player_id,
            projectile,
            burst,
            server_time_ms,
        )?;
        match (admission, ammo_admission) {
            (LaunchAdmission::New(record), PlayerAmmoLaunchAdmission::New) => {
                if prepared_fire
                    .as_ref()
                    .is_some_and(|prepared| prepared.next_clock.is_none())
                {
                    return Err(BattleLoopError::PlayerFireTransactionMismatch);
                }
                self.player_ammo.insert(player_id, staged_ammo);
                self.commit_prepared_player_fire_shot(player_id, edge.shot_seq, prepared_fire)?;
                authority.note_spotting_fire(record.launch.shooter, tick)?;
                self.contact_authority
                    .note_fire(record.launch.shooter, tick);
                authority.track_projectile(record, tick)?;
            }
            (LaunchAdmission::ExactRetry { .. }, PlayerAmmoLaunchAdmission::ExactRetry) => {
                if prepared_fire
                    .as_ref()
                    .is_some_and(|prepared| prepared.next_clock.is_some())
                {
                    return Err(BattleLoopError::PlayerFireTransactionMismatch);
                }
            }
            _ => return Err(BattleLoopError::PlayerAmmoTransactionMismatch),
        }
        Ok(())
    }

    fn prepare_player_fire_shot(
        &self,
        player_id: u64,
        intent_seq: u64,
        shot_seq: u64,
        physical_round_index: u16,
        aim_direction: ProjectileVec3,
        final_round: bool,
    ) -> Result<Option<PreparedPlayerFireShot>, BattleLoopError> {
        let Some(runtime) = self.player_fire.get(&player_id) else {
            return Ok(None);
        };
        if let Some(previous) = self.player_fire_receipts.get(&(player_id, shot_seq)) {
            if previous.intent_seq != intent_seq
                || previous.physical_round_index != physical_round_index
                || previous.aim_direction != aim_direction
                || previous.final_round != final_round
            {
                return Err(BattleLoopError::PlayerFireTransactionMismatch);
            }
            return Ok(Some(PreparedPlayerFireShot {
                direction: previous.sampled_direction,
                next_clock: None,
                receipt: *previous,
            }));
        }
        let sample = runtime.clock.sample_direction(
            PlayerFireLineage {
                round_id: self.engine.scope().round_id,
                authority_epoch: self.engine.scope().epoch,
                player_id,
                fire_intent_seq: intent_seq,
                physical_round_index,
            },
            Direction3::new(aim_direction.x, aim_direction.y, aim_direction.z),
        )?;
        let sampled_direction = ProjectileVec3 {
            x: sample.direction.x,
            y: sample.direction.y,
            z: sample.direction.z,
        };
        let mut next_clock = runtime.clock;
        next_clock.commit_physical_shot(final_round, runtime.static_factors)?;
        Ok(Some(PreparedPlayerFireShot {
            direction: sampled_direction,
            next_clock: Some(next_clock),
            receipt: PlayerFireShotReceipt {
                intent_seq,
                physical_round_index,
                aim_direction,
                sampled_direction,
                final_round,
            },
        }))
    }

    fn commit_prepared_player_fire_shot(
        &mut self,
        player_id: u64,
        shot_seq: u64,
        prepared: Option<PreparedPlayerFireShot>,
    ) -> Result<(), BattleLoopError> {
        let Some(prepared) = prepared else {
            return Ok(());
        };
        let next_clock = prepared
            .next_clock
            .ok_or(BattleLoopError::PlayerFireTransactionMismatch)?;
        if self
            .player_fire_receipts
            .contains_key(&(player_id, shot_seq))
            || !self.player_fire.contains_key(&player_id)
        {
            return Err(BattleLoopError::PlayerFireTransactionMismatch);
        }
        self.player_fire
            .get_mut(&player_id)
            .expect("validated player fire runtime remains installed")
            .clock = next_clock;
        self.player_fire_receipts
            .insert((player_id, shot_seq), prepared.receipt);
        let stale = self
            .player_fire_receipts
            .keys()
            .filter(|(receipt_player_id, _)| *receipt_player_id == player_id)
            .count()
            .saturating_sub(MAX_PLAYER_FIRE_RECEIPTS);
        let old_keys = self
            .player_fire_receipts
            .keys()
            .filter(|(receipt_player_id, _)| *receipt_player_id == player_id)
            .take(stale)
            .copied()
            .collect::<Vec<_>>();
        for key in old_keys {
            self.player_fire_receipts.remove(&key);
        }
        Ok(())
    }

    fn refresh_player_burst_snapshot(
        &mut self,
        player_id: u64,
        tick: u64,
    ) -> Result<(), BattleLoopError> {
        let ammo = self
            .player_ammo
            .get(&player_id)
            .ok_or(BattleError::InvalidVehicle)?
            .snapshot();
        let Some(template) = self.player_burst_templates.get(&player_id) else {
            if let Some(snapshot) = self.player_burst_snapshots.get_mut(&player_id) {
                snapshot.shell_index = ammo.loaded_shell;
            }
            return Ok(());
        };
        let active = self
            .player_burst_clocks
            .get(&player_id)
            .is_some_and(PhysicalBurstClock::active);
        let next_index = if active {
            ammo.burst_next_index
        } else {
            template.count
        };
        let interval_us = (template.descriptor.interval_seconds * 1_000_000.0).round() as u64;
        let time_left_seconds = if active {
            let next_due_us = template
                .start_time_us
                .checked_add(interval_us.saturating_mul(u64::from(next_index)))
                .ok_or(PhysicalBurstError::SequenceExhausted)?;
            next_due_us.saturating_sub(crate::sim::time_us_at_tick(tick)) as f64 / 1_000_000.0
        } else {
            0.0
        };
        self.player_burst_snapshots.insert(
            player_id,
            PlayerBurstSnapshot {
                active,
                group_seq: template.launch.shot_seq,
                count: template.count,
                next_index,
                interval_seconds: template.descriptor.interval_seconds,
                time_left_seconds,
                shell_index: template.launch.shell_index,
            },
        );
        Ok(())
    }

    fn advance_player_equipment(&mut self, tick: u64) -> Result<(), BattleLoopError> {
        let now_seconds = crate::sim::time_us_at_tick(tick) as f64 / 1_000_000.0;
        let dt_seconds = crate::sim::delta_us_for_tick(tick) as f64 / 1_000_000.0;
        let combat_live = self.engine.combat_live();
        let actors = self
            .player_equipment
            .keys()
            .map(|&player_id| {
                let key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: player_id,
                };
                let combat = self
                    .engine
                    .combat()
                    .get(key)
                    .cloned()
                    .ok_or(BattleError::UnknownVehicle(key))?;
                let profile = self
                    .engine
                    .critical_profile(key)
                    .cloned()
                    .ok_or(BattleError::InvalidVehicle)?;
                let critical = self
                    .engine
                    .critical_state(key)
                    .cloned()
                    .ok_or(BattleError::InvalidVehicle)?;
                Ok((player_id, key, combat, profile, critical))
            })
            .collect::<Result<Vec<_>, BattleError>>()?;
        let mut staged_ledgers = self.player_equipment.clone();
        let mut critical_chains = BTreeMap::<VehicleKey, Vec<_>>::new();
        for (player_id, key, combat, profile, mut critical) in actors {
            let ledger = staged_ledgers
                .get_mut(&player_id)
                .expect("player equipment roster was frozen");
            if !combat_live || !combat.alive {
                ledger.advance_clock(now_seconds)?;
                continue;
            }
            let applications = ledger.advance_automatic(now_seconds, &profile, &critical)?;
            for application in applications {
                if let Some(mutation) = application.critical_mutation {
                    critical = mutation.state().clone();
                    critical_chains.entry(key).or_default().push(mutation);
                }
            }
            if let Some(mutation) =
                ledger.propose_engine_damage(&profile, &critical, combat.health, dt_seconds)?
            {
                critical_chains.entry(key).or_default().push(mutation);
            }
        }
        self.engine
            .apply_player_equipment_critical_batch(self.engine.scope(), &critical_chains)?;
        self.player_equipment = staged_ledgers;
        Ok(())
    }

    fn advance_bot_equipment(
        &mut self,
        tick: u64,
        server_time_ms: u64,
    ) -> Result<(), BattleLoopError> {
        let now_seconds = crate::sim::time_us_at_tick(tick) as f64 / 1_000_000.0;
        let combat_live = self.engine.combat_live();
        let bot_ids = self.bot_equipment.keys().copied().collect::<Vec<_>>();
        for bot_id in bot_ids {
            let key = VehicleKey {
                kind: VehicleKind::Bot,
                id: bot_id,
            };
            let combat = self
                .engine
                .combat()
                .get(key)
                .cloned()
                .ok_or(BattleError::UnknownVehicle(key))?;
            let profile = self
                .engine
                .critical_profile(key)
                .cloned()
                .ok_or(BattleError::InvalidVehicle)?;
            let critical = self
                .engine
                .critical_state(key)
                .cloned()
                .ok_or(BattleError::InvalidVehicle)?;
            let stun_end_server_time_ms = self
                .engine
                .projectile_stun_state(key)
                .filter(|state| state.end_server_time_ms > server_time_ms)
                .map(|state| state.end_server_time_ms);
            let mut staged = self
                .bot_equipment
                .get(&bot_id)
                .cloned()
                .expect("bot equipment roster was frozen");
            if !combat_live || !combat.alive {
                staged.advance_clock(now_seconds)?;
                self.bot_equipment.insert(bot_id, staged);
                continue;
            }
            let applications =
                staged.advance_policy(now_seconds, &profile, &critical, stun_end_server_time_ms)?;
            if applications.is_empty() {
                self.bot_equipment.insert(bot_id, staged);
                continue;
            }
            let mut critical_chain = Vec::new();
            let mut stun_clear = None;
            for application in applications {
                if let Some(mutation) = application.application.critical_mutation {
                    critical_chain.push(mutation);
                }
                if let Some(base_end_server_time_ms) = application.stun_base_end_server_time_ms {
                    if stun_clear
                        .replace((tick, base_end_server_time_ms))
                        .is_some()
                    {
                        return Err(PlayerEquipmentError::InvalidBotLoadout.into());
                    }
                }
            }
            let commit = self.engine.apply_bot_equipment_batch(
                self.engine.scope(),
                key,
                critical_chain,
                stun_clear,
            );
            match commit {
                Ok(()) => {
                    self.bot_equipment.insert(bot_id, staged);
                }
                Err(BattleError::ProjectileStun(
                    ProjectileStunError::ClearCasConflict | ProjectileStunError::ConflictingRetry,
                ))
                | Err(BattleError::Critical(CriticalDamageError::RevisionConflict)) => {
                    // A stale per-bot proposal is contained to this kit poll.
                    // Both the staged item charges and all critical/stun
                    // mutations are discarded; every other actor and the
                    // fixed-tick battle continue.
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }

    fn apply_command(
        &mut self,
        command: QueuedCommand,
        receipt_time_us: u64,
        server_time_ms: u64,
    ) -> Result<CommandEffect, CommandRejection> {
        let result = match command.message.kind() {
            "input" => parse_player_input(&self.engine, command.player_id, &command.message)
                .and_then(|(mut input, side_effects)| {
                    let input_seq = exact_u64(command.message.get("input_seq"), 1, MAX_SEQUENCE)
                        .ok_or_else(|| "input_seq is invalid".to_owned())?;
                    let player_key = VehicleKey {
                        kind: VehicleKind::Player,
                        id: command.player_id,
                    };
                    let player_alive = self
                        .engine
                        .combat()
                        .get(player_key)
                        .ok_or_else(|| "player vehicle is not part of this round".to_owned())?
                        .alive;
                    if self.terminal || self.engine.result().is_some() || !player_alive {
                        validate_inactive_input_side_effects(
                            &self.engine,
                            command.player_id,
                            &side_effects,
                        )?;
                        return Ok(CommandEffect::PlayerInput(side_effects));
                    }
                    let mut staged_ammo = self
                        .player_ammo
                        .get(&command.player_id)
                        .cloned()
                        .ok_or_else(|| "player ammunition authority is unavailable".to_owned())?;
                    staged_ammo
                        .admit_input(input_seq)
                        .map_err(|error| error.to_string())?;
                    input.shell_index = staged_ammo
                        .shell_for_input(input_seq)
                        .ok_or_else(|| "canonical input shell is unavailable".to_owned())?;
                    let fire_source_time_us = input.source_time_us;
                    let fire_pose = input.pose;
                    let fire_desired_world_yaw = input.aim_yaw;
                    let mut staged_ram = self.ram.clone();
                    let mut staged_player_ram_timeline = self.player_ram_timeline.clone();
                    let is_new_input = self
                        .engine
                        .player_input_seq(command.player_id)
                        .is_none_or(|last| input_seq > last);
                    if is_new_input {
                        if let (Some(source_time_us), Some(pose)) =
                            (input.source_time_us, input.pose)
                        {
                            let key = VehicleKey {
                                kind: VehicleKind::Player,
                                id: command.player_id,
                            };
                            let entity = self
                                .engine
                                .entities()
                                .find(|entity| entity.key == key)
                                .ok_or_else(|| {
                                    "player vehicle is not part of this round".to_owned()
                                })?;
                            let alive = entity.combat.alive;
                            let (ram_vx, ram_vy, ram_vz) = if alive {
                                (pose.ram_vx, pose.ram_vy, pose.ram_vz)
                            } else {
                                (0.0, 0.0, 0.0)
                            };
                            let frame = RamPoseFrame::new(
                                RamSourceCursor::new(0, input_seq)
                                    .map_err(|error| error.to_string())?,
                                source_time_us.min(receipt_time_us),
                                RamBody {
                                    key,
                                    team: entity.team.number(),
                                    alive,
                                    x: pose.x,
                                    y: pose.y,
                                    z: pose.z,
                                    yaw: pose.yaw,
                                    pitch: input.pitch,
                                    roll: input.roll,
                                    mass: *self.vehicle_masses.get(&key).ok_or_else(|| {
                                        "player RAM mass is unavailable".to_owned()
                                    })?,
                                    vx: ram_vx,
                                    vy: ram_vy,
                                    vz: ram_vz,
                                    turret_yaw: angle_delta(pose.yaw, input.aim_yaw),
                                    gun_pitch: input.gun_pitch,
                                    siege_state: self.engine.siege_status(command.player_id).0,
                                    shape: *self.vehicle_ram_shapes.get(&key).ok_or_else(|| {
                                        "player RAM shape is unavailable".to_owned()
                                    })?,
                                },
                            )
                            .map_err(|error| error.to_string())?;
                            let ram_pose_admission = staged_player_ram_timeline
                                .record_streaming(frame)
                                .map_err(|error| error.to_string())?;
                            if ram_pose_admission == RamPoseAdmission::DiscontinuityReset {
                                let frozen_receipts = self
                                    .pending_ram_contacts
                                    .values()
                                    .filter_map(|probe| match probe.source {
                                        crate::ram::RamResolutionSource::PlayerPairReceipt {
                                            reporter_player_id,
                                            sequence,
                                            target_player_id,
                                            ..
                                        } if reporter_player_id == command.player_id
                                            || target_player_id == command.player_id =>
                                        {
                                            Some((reporter_player_id, sequence))
                                        }
                                        _ => None,
                                    })
                                    .collect::<BTreeSet<_>>();
                                staged_ram
                                    .invalidate_unfrozen_player_pair_history(
                                        command.player_id,
                                        &frozen_receipts,
                                    )
                                    .map_err(|error| error.to_string())?;
                            }
                        }
                    }
                    contain_player_ram_receipts(
                        &mut staged_ram,
                        command.player_id,
                        &side_effects.ram_contacts,
                    )
                    .map_err(|error| error.to_string())?;
                    contain_player_pair_ram_receipts(
                        &mut staged_ram,
                        &self.engine,
                        command.player_id,
                        &side_effects.player_ram_contacts,
                    )
                    .map_err(|error| error.to_string())?;
                    let input_admission = self
                        .engine
                        .submit_player_input(
                            command.scope,
                            command.player_id,
                            PlayerInput {
                                receipt_time_us,
                                ..input
                            },
                        )
                        .map_err(|error| error.to_string())?;
                    if input_admission == InputAdmission::Accepted {
                        self.record_player_fire_input(
                            command.player_id,
                            fire_source_time_us,
                            fire_pose,
                            fire_desired_world_yaw,
                        );
                    }
                    self.ram = staged_ram;
                    if input_admission == InputAdmission::Accepted {
                        self.player_ram_timeline = staged_player_ram_timeline;
                    }
                    self.player_ammo.insert(command.player_id, staged_ammo);
                    if let Some(enabled) = side_effects.siege_enabled {
                        self.engine.request_siege_state(command.player_id, enabled);
                    }
                    Ok(CommandEffect::PlayerInput(side_effects))
                }),
            "ammo_intent" => parse_ammo_intent(&command.message).and_then(|intent| {
                let mut staged_ammo = self
                    .player_ammo
                    .get(&command.player_id)
                    .cloned()
                    .ok_or_else(|| "player ammunition authority is unavailable".to_owned())?;
                let intuition_success = deterministic_intuition_success(
                    command.scope.round_id,
                    command.scope.epoch,
                    command.player_id,
                    intent.intent_seq,
                    staged_ammo.intuition_chances(),
                )
                .map_err(|error| error.to_string())?;
                let fire_pending = self
                    .engine
                    .player_fire_intent_pending(command.player_id)
                    .map_err(|error| error.to_string())?;
                let admission = if fire_pending {
                    staged_ammo.admit_intent_deferred_until_launch(intent, intuition_success)
                } else {
                    staged_ammo.admit_intent(intent, intuition_success)
                }
                .map_err(|error| error.to_string())?;
                let outcome = match admission {
                    PlayerAmmoIntentAdmission::New(outcome)
                    | PlayerAmmoIntentAdmission::ExactRetry(outcome) => outcome,
                };
                self.engine
                    .synchronize_player_shell(command.player_id, staged_ammo.loaded_shell())
                    .map_err(|error| error.to_string())?;
                self.player_ammo.insert(command.player_id, staged_ammo);
                Ok(CommandEffect::AmmoIntent {
                    connection_id: command.connection_id,
                    scope: command.scope,
                    player_id: command.player_id,
                    intent,
                    outcome,
                })
            }),
            "fire_intent" => (|| {
                if !self.player_fire_motion_ready(command.player_id) {
                    return Err("player fire motion history is unavailable".to_owned());
                }
                let ledger = self
                    .player_ammo
                    .get(&command.player_id)
                    .ok_or_else(|| "player ammunition authority is unavailable".to_owned())?;
                let input_seq = exact_u64(command.message.get("input_seq"), 1, MAX_SEQUENCE)
                    .ok_or_else(|| "input_seq is invalid".to_owned())?;
                let shell_index = ledger
                    .shell_for_input(input_seq)
                    .ok_or_else(|| "fire intent references unknown canonical input".to_owned())?;
                let request = parse_fire_intent(&command.message, shell_index)?;
                let mut staged_ammo = ledger.clone();
                staged_ammo
                    .admit_launch(PlayerAmmoLaunch {
                        shot_seq: ledger.last_shot_seq().saturating_add(1),
                        input_seq: request.input_seq,
                        shell_index: request.shell_index,
                    })
                    .map_err(|error| error.to_string())?;
                self.engine
                    .submit_fire_intent(command.scope, command.player_id, request, server_time_ms)
                    .map(|admission| CommandEffect::FireIntent {
                        player_id: command.player_id,
                        admission,
                    })
                    .map_err(|error| error.to_string())
            })(),
            "landing_observation" => LandingObservationRequest::parse(&command.message)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    if request.scope() != command.scope {
                        return Err(
                            "landing observation scope does not match its envelope".to_owned()
                        );
                    }
                    let key = VehicleKey {
                        kind: VehicleKind::Player,
                        id: command.player_id,
                    };
                    let combat =
                        self.engine.combat().get(key).cloned().ok_or_else(|| {
                            "player environment authority is unavailable".to_owned()
                        })?;
                    let current_input_seq = self
                        .engine
                        .player_input_seq(command.player_id)
                        .ok_or_else(|| "player environment authority is unavailable".to_owned())?;
                    let oldest_known_input = current_input_seq
                        .saturating_sub(MAX_INPUT_FINGERPRINTS.saturating_sub(1) as u64)
                        .max(1);
                    let input_is_known = current_input_seq > 0
                        && (oldest_known_input..=current_input_seq).contains(&request.input_seq);
                    let mut staged_environment = self
                        .player_environment
                        .get(&command.player_id)
                        .cloned()
                        .ok_or_else(|| "player environment authority is unavailable".to_owned())?;
                    let admission = staged_environment.admit_landing_observation(
                        request,
                        LandingObservationContext {
                            scope: self.engine.scope(),
                            combat_active: self.engine.combat_live(),
                            alive: combat.alive,
                            health: combat.health,
                            max_health: combat.max_health,
                            input_is_known,
                        },
                    );
                    if admission.newly_committed {
                        if admission.damage > 0 {
                            self.engine
                                .apply_environment_damage_batch_deferred(
                                    command.scope,
                                    &[EnvironmentDamageEffect {
                                        target: key,
                                        amount: admission.damage,
                                        client_simulation_reason:
                                            PlayerEnvironmentCause::WorldCollision.death_reason(),
                                    }],
                                )
                                .map_err(|error| error.to_string())?;
                        }
                        self.player_environment
                            .insert(command.player_id, staged_environment);
                    }
                    Ok(CommandEffect::LandingObservation {
                        connection_id: command.connection_id,
                        player_id: command.player_id,
                        result: admission.result,
                    })
                }),
            "equipment_intent" => {
                if command.scope != self.engine.scope() {
                    Err("equipment intent scope does not match the active battle".to_owned())
                } else {
                    decode_equipment_intent(
                        &Value::Object(command.message.fields().clone()),
                        self.engine.scope().round_id,
                    )
                    .map_err(|error| error.to_string())
                    .and_then(|intent| {
                        let key = VehicleKey {
                            kind: VehicleKind::Player,
                            id: command.player_id,
                        };
                        let combat = self.engine.combat().get(key).cloned().ok_or_else(|| {
                            "player equipment authority is unavailable".to_owned()
                        })?;
                        let profile =
                            self.engine.critical_profile(key).cloned().ok_or_else(|| {
                                "player equipment authority is unavailable".to_owned()
                            })?;
                        let critical =
                            self.engine.critical_state(key).cloned().ok_or_else(|| {
                                "player equipment authority is unavailable".to_owned()
                            })?;
                        let mut staged_equipment = self
                            .player_equipment
                            .get(&command.player_id)
                            .cloned()
                            .ok_or_else(|| {
                                "player equipment authority is unavailable".to_owned()
                            })?;
                        let stun_end_server_time_ms = self
                            .engine
                            .projectile_stun_state(key)
                            .filter(|state| state.end_server_time_ms > server_time_ms)
                            .map(|state| state.end_server_time_ms);
                        let intent_seq = intent.intent_seq;
                        let outcome = staged_equipment
                            .admit_intent(
                                intent,
                                PlayerEquipmentContext {
                                    now_seconds: receipt_time_us as f64 / 1_000_000.0,
                                    combat_accepting: self.engine.combat_live(),
                                    battle_result_committed: self.engine.result().is_some(),
                                    participating: true,
                                    alive: combat.alive,
                                    stunned: stun_end_server_time_ms.is_some(),
                                    critical_profile: &profile,
                                    critical_state: &critical,
                                },
                            )
                            .map_err(|error| error.to_string())?;
                        let mut critical_chain = BTreeMap::new();
                        if let Some(mutation) = outcome
                            .application
                            .as_ref()
                            .and_then(|application| application.critical_mutation.clone())
                        {
                            critical_chain.insert(key, vec![mutation]);
                        }
                        let stun_clear = outcome.application.as_ref().and_then(|application| {
                            matches!(
                                &application.effect,
                                EquipmentEffect::RestoreCrew {
                                    clear_stun: true,
                                    ..
                                }
                            )
                            .then(|| (key, intent_seq, stun_end_server_time_ms))
                        });
                        let stun_clear = match stun_clear {
                            Some((key, intent_seq, Some(base_end_server_time_ms))) => {
                                Some((key, intent_seq, base_end_server_time_ms))
                            }
                            Some((_, _, None)) => {
                                return Err("player equipment stun CAS is unavailable".to_owned());
                            }
                            None => None,
                        };
                        self.engine
                            .apply_player_equipment_batch(
                                command.scope,
                                &critical_chain,
                                stun_clear,
                            )
                            .map_err(|error| error.to_string())?;
                        self.player_equipment
                            .insert(command.player_id, staged_equipment);
                        Ok(CommandEffect::EquipmentIntent {
                            player_id: command.player_id,
                            result: outcome.current_result,
                        })
                    })
                }
            }
            kind => Err(format!("unsupported battle command {kind:?}")),
        };
        result.map_err(|message| CommandRejection {
            connection_id: command.connection_id,
            code: "invalid_battle_command",
            message,
        })
    }

    fn advance_authority(
        &mut self,
        tick: u64,
        server_time_ms: u64,
        player_burst_ticks: BTreeMap<u64, PlayerBurstTick>,
    ) -> Result<AuthorityAdvanceOutput, BattleLoopError> {
        let Some(mut authority) = self.authority.take() else {
            if player_burst_ticks
                .values()
                .any(|burst_tick| !burst_tick.edges.is_empty())
            {
                return Err(BattleError::OracleUnavailable.into());
            }
            for (player_id, burst_tick) in player_burst_ticks {
                self.player_burst_clocks.insert(player_id, burst_tick.clock);
                self.refresh_player_burst_snapshot(player_id, tick)?;
            }
            return Ok(AuthorityAdvanceOutput::default());
        };
        let result =
            self.advance_authority_inner(&mut authority, tick, server_time_ms, player_burst_ticks);
        self.authority = Some(authority);
        result
    }

    fn advance_authority_inner(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
        server_time_ms: u64,
        player_burst_ticks: BTreeMap<u64, PlayerBurstTick>,
    ) -> Result<AuthorityAdvanceOutput, BattleLoopError> {
        let scope = self.engine.scope();
        let (mut due, permit) = authority.release_due(tick)?;
        self.apply_due_ramming(
            authority,
            std::mem::take(&mut due.ram_contact_evidence),
            std::mem::take(&mut due.unavailable_ram_contacts),
            std::mem::take(&mut due.timed_out_ram_contacts),
        )?;
        self.apply_due_he_terminals(
            authority,
            tick,
            server_time_ms,
            std::mem::take(&mut due.he_explosion_evidence),
            std::mem::take(&mut due.unavailable_he_explosions),
            std::mem::take(&mut due.timed_out_he_explosions),
        )?;
        self.advance_player_environment(tick, std::mem::take(&mut due.player_environment))?;
        self.apply_destructible_hulls(tick, std::mem::take(&mut due.destructible_hulls))?;
        let projectile_application = self.apply_due_projectile_decisions(
            authority,
            std::mem::take(&mut due.projectile_decisions),
            server_time_ms,
        )?;
        self.contact_authority.ingest_native_tick(
            tick,
            &due.native_observations,
            &due.native_firing_lanes,
            &due.failed_native_observations,
            &due.timed_out_native_observations,
        )?;
        let pre_step_actors = self.contact_actor_states();
        let mut planning_contacts = self.contact_authority.clone();
        let due_contacts = planning_contacts.evaluate_tick(tick, &pre_step_actors)?;
        let direct_spotting = direct_spotting_observations(&due_contacts);
        self.engine.replace_direct_spotting(&direct_spotting)?;
        self.advance_bot_equipment(tick, server_time_ms)?;
        self.engine.finalize_boundary_elimination()?;

        if self.engine.result().is_some() {
            for sample in due.player_muzzles {
                self.engine.reject_player_fire_intent(
                    scope,
                    sample.binding.player_id,
                    sample.binding.intent_seq,
                    "battle_ended_before_muzzle_commit",
                )?;
            }
            for failed in due.failed_player_muzzles {
                self.engine.reject_player_fire_intent(
                    scope,
                    failed.binding.player_id,
                    failed.binding.intent_seq,
                    player_muzzle_failure_reason(failed.reason),
                )?;
            }
            for (terminal, record) in projectile_application.he_terminals {
                let projectile_id = terminal.resolution.projectile_id.clone();
                if record.projectile_id == projectile_id {
                    self.fail_projectile_locally(authority, &record, server_time_ms)?;
                } else {
                    authority.retire_projectile(&projectile_id);
                    authority.retire_projectile(&record.projectile_id);
                }
            }
            self.continue_ricochets_or_fail(
                authority,
                projectile_application.ricochet_records,
                server_time_ms,
            )?;
            self.close_terminal_ramming()?;
            self.cancel_all_player_bursts(tick)?;
            authority.close_terminal_after_due(permit)?;
            return Ok(AuthorityAdvanceOutput {
                oracle_requests: Vec::new(),
                contacts: due_contacts,
            });
        }

        let mut oracle_requests = Vec::new();
        for (terminal, record) in projectile_application.he_terminals {
            match self.schedule_he_terminal(authority, terminal, record.clone(), server_time_ms) {
                Ok(Some(request)) => oracle_requests.push(request),
                Ok(None) => {}
                Err(_) => {
                    self.fail_projectile_locally(authority, &record, server_time_ms)?;
                }
            }
        }

        let bot_ids: Vec<_> = authority.bot_ids().collect();
        for bot_id in bot_ids {
            let key = VehicleKey {
                kind: VehicleKind::Bot,
                id: u64::from(bot_id),
            };
            let view = self
                .engine
                .entities()
                .find(|entity| entity.key == key)
                .ok_or(BattleError::UnknownVehicle(key))?;
            let combat = &view.combat;
            let critical = self
                .engine
                .critical_state(key)
                .map(bot_critical_state)
                .ok_or(BattleError::InvalidVehicle)?;
            authority.sync_bot_combat(
                bot_id,
                CanonicalBotCombatState {
                    health: combat.health,
                    display_health: combat.health,
                    alive: combat.alive,
                    death_reason: (!combat.alive).then_some(view.death_reason),
                    critical,
                },
            )?;
        }

        let bot_states = planner_bot_states(authority);
        let players = planner_player_states(&self.engine);
        let manifest = self
            .planner_manifest
            .as_ref()
            .ok_or(BattleLoopError::MissingPlannerManifest)?;
        let refresh_strategy = strategy_refresh_due(self.last_strategy_refresh_tick, tick);
        let contacts = refresh_strategy.then(|| planning_contacts.planner_contacts());
        let defense = refresh_strategy.then(|| planner_defense_context(self.engine.rules()));
        let planner = authority.build_planner_orders_at_cadence(
            PlannerBuildInput {
                manifest,
                bot_states: &bot_states,
                players: &players,
                now: tick_offset(tick).as_secs_f64(),
                contacts: contacts.as_ref(),
                defense: defense.as_ref(),
            },
            refresh_strategy,
        )?;
        if refresh_strategy {
            self.last_strategy_refresh_tick = Some(tick);
        }
        let human_traffic = self.human_traffic()?;
        let world_pose_bots = self
            .engine
            .entities()
            .filter(|entity| {
                entity.key.kind == VehicleKind::Bot && entity.combat.alive && entity.world_pose
            })
            .map(|entity| u32::try_from(entity.key.id).map_err(|_| BattleError::InvalidVehicle))
            .collect::<Result<BTreeSet<_>, _>>()?;
        let projectile_targets = self
            .engine
            .entities()
            .map(|entity| AuthorityProjectileTarget {
                vehicle: entity.key,
                wreck: !entity.combat.alive,
            })
            .collect();
        let mut output = authority.step_after_due(
            permit,
            AuthorityTickInput {
                tick,
                orders: planner.orders,
                human_traffic,
                world_pose_bots,
                projectile_targets,
                static_collision_mask: u32::MAX,
            },
        )?;
        oracle_requests.append(&mut output.oracle_requests);
        self.commit_player_burst_ticks(authority, tick, server_time_ms, player_burst_ticks)?;

        for sample in due.player_muzzles {
            if !self.player_physical_fire_accepting(sample.binding.player_id)? {
                self.engine.reject_player_fire_intent(
                    scope,
                    sample.binding.player_id,
                    sample.binding.intent_seq,
                    "player_fire_gate_closed_before_muzzle_commit",
                )?;
                continue;
            }
            if sample.barrel_under_water {
                self.engine.reject_player_fire_intent(
                    scope,
                    sample.binding.player_id,
                    sample.binding.intent_seq,
                    "barrel_under_water",
                )?;
                continue;
            }
            let Some((muzzle_origin, aim_direction)) =
                player_muzzle_launch_geometry(sample.transform.position, sample.transform.basis)
            else {
                self.engine.reject_player_fire_intent(
                    scope,
                    sample.binding.player_id,
                    sample.binding.intent_seq,
                    "native_muzzle_invalid",
                )?;
                continue;
            };
            let player_key = VehicleKey {
                kind: VehicleKind::Player,
                id: sample.binding.player_id,
            };
            let source_shot = self
                .mounted_shots
                .get(&player_key)
                .and_then(|shots| shots.get(&sample.binding.shell_index))
                .cloned()
                .ok_or(BattleError::InvalidProjectileEffects)?;
            let descriptor = *self
                .player_burst_descriptors
                .get(&sample.binding.player_id)
                .ok_or(BattleError::InvalidVehicle)?;
            let ammo_before = self
                .player_ammo
                .get(&sample.binding.player_id)
                .ok_or(BattleError::InvalidVehicle)?
                .snapshot();
            let ammunition = *ammo_before
                .remaining
                .get(usize::from(sample.binding.shell_index))
                .ok_or(BattleError::InvalidProjectileEffects)?;
            let mut staged_clock = self
                .player_burst_clocks
                .get(&sample.binding.player_id)
                .cloned()
                .ok_or(BattleError::InvalidVehicle)?;
            let burst_admission = staged_clock.arm(
                sample.binding.shot_seq,
                sample.binding.shell_index,
                descriptor,
                ammunition,
                ammo_before.clip_remaining,
                tick,
            )?;
            let first_edge = match &burst_admission {
                PhysicalBurstAdmission::New { first }
                | PhysicalBurstAdmission::ExactRetry { first } => *first,
            };
            if first_edge.shot_seq != sample.binding.shot_seq {
                return Err(BattleError::InvalidProjectileEffects.into());
            }
            let prepared_fire = self.prepare_player_fire_shot(
                sample.binding.player_id,
                sample.binding.intent_seq,
                sample.binding.shot_seq,
                first_edge.burst_index,
                aim_direction,
                first_edge.final_round(),
            )?;
            let direction = prepared_fire
                .as_ref()
                .map_or(aim_direction, |prepared| prepared.direction);
            let projectile = ProjectileLaunch {
                round_id: scope.round_id,
                authority_epoch: scope.epoch,
                shooter: player_key,
                shot_seq: sample.binding.shot_seq,
                shell_index: sample.binding.shell_index,
                origin: muzzle_origin,
                velocity: ProjectileVec3 {
                    x: direction.x * source_shot.speed,
                    y: direction.y * source_shot.speed,
                    z: direction.z * source_shot.speed,
                },
                gravity: source_shot.gravity,
                max_distance: source_shot.max_distance,
                max_time_ms: MAX_PROJECTILE_LIFETIME_MS,
                is_he: source_shot.shell.kind == "HIGH_EXPLOSIVE",
                splash_radius: source_shot.shell.explosion_radius,
                penetration_factor: deterministic_shot_factor(
                    scope,
                    player_key,
                    sample.binding.shot_seq,
                    0,
                ),
                damage_factor: deterministic_shot_factor(
                    scope,
                    player_key,
                    sample.binding.shot_seq,
                    1,
                ),
                source_shot,
                fire_intent_seq: Some(sample.binding.intent_seq),
                fire_input_seq: Some(sample.binding.input_seq),
            };
            let mut staged_ammo = self
                .player_ammo
                .get(&sample.binding.player_id)
                .cloned()
                .ok_or(BattleError::InvalidVehicle)?;
            let burst = PlayerAmmoBurst::from_edge(first_edge);
            let ammo_admission = staged_ammo.admit_physical_launch(
                PlayerAmmoLaunch {
                    shot_seq: sample.binding.shot_seq,
                    input_seq: sample.binding.input_seq,
                    shell_index: sample.binding.shell_index,
                },
                burst,
            )?;
            let template = PlayerBurstTemplate {
                launch: projectile.clone(),
                aim_direction,
                descriptor,
                start_time_us: first_edge.due_time_us,
                count: first_edge.burst_count,
            };
            let admission = self.engine.commit_player_physical_launch(
                scope,
                sample.binding.player_id,
                sample.binding.intent_seq,
                projectile,
                burst,
                server_time_ms,
            )?;
            match (admission, ammo_admission, burst_admission) {
                (
                    LaunchAdmission::New(record),
                    PlayerAmmoLaunchAdmission::New,
                    PhysicalBurstAdmission::New { .. },
                ) => {
                    if prepared_fire
                        .as_ref()
                        .is_some_and(|prepared| prepared.next_clock.is_none())
                    {
                        return Err(BattleLoopError::PlayerFireTransactionMismatch);
                    }
                    self.player_ammo
                        .insert(sample.binding.player_id, staged_ammo);
                    self.commit_prepared_player_fire_shot(
                        sample.binding.player_id,
                        sample.binding.shot_seq,
                        prepared_fire,
                    )?;
                    self.player_burst_clocks
                        .insert(sample.binding.player_id, staged_clock);
                    self.player_burst_templates
                        .insert(sample.binding.player_id, template);
                    self.refresh_player_burst_snapshot(sample.binding.player_id, tick)?;
                    if !self
                        .player_burst_clocks
                        .get(&sample.binding.player_id)
                        .is_some_and(PhysicalBurstClock::active)
                    {
                        self.player_burst_templates
                            .remove(&sample.binding.player_id);
                    }
                    authority.note_spotting_fire(player_key, tick)?;
                    self.contact_authority.note_fire(player_key, tick);
                    authority.track_projectile(record, tick)?;
                }
                (
                    LaunchAdmission::ExactRetry { .. },
                    PlayerAmmoLaunchAdmission::ExactRetry,
                    PhysicalBurstAdmission::ExactRetry { .. },
                ) => {
                    if prepared_fire
                        .as_ref()
                        .is_some_and(|prepared| prepared.next_clock.is_some())
                    {
                        return Err(BattleLoopError::PlayerFireTransactionMismatch);
                    }
                }
                _ => return Err(BattleLoopError::PlayerAmmoTransactionMismatch),
            }
        }
        for failed in due.failed_player_muzzles {
            self.engine.reject_player_fire_intent(
                scope,
                failed.binding.player_id,
                failed.binding.intent_seq,
                player_muzzle_failure_reason(failed.reason),
            )?;
        }

        let mut bot_environment_effects = Vec::new();
        for bot in output.bots {
            let bot_key = VehicleKey {
                kind: VehicleKind::Bot,
                id: u64::from(bot.bot_id),
            };
            for launch in bot.launches {
                let shell_index = u8::try_from(launch.shell_index)
                    .map_err(|_| BattleError::InvalidProjectileEffects)?;
                let source_shot = self
                    .mounted_shots
                    .get(&bot_key)
                    .and_then(|shots| shots.get(&shell_index))
                    .cloned()
                    .ok_or(BattleError::InvalidProjectileEffects)?;
                if launch.shot_id.round_id != scope.round_id
                    || launch.shot_id.source_id != bot.bot_id
                {
                    return Err(BattleError::InvalidProjectileEffects.into());
                }
                let projectile = ProjectileLaunch {
                    round_id: scope.round_id,
                    authority_epoch: scope.epoch,
                    shooter: bot_key,
                    shot_seq: launch.shot_id.fire_seq,
                    shell_index,
                    origin: projectile_vec(launch.origin),
                    velocity: projectile_vec(launch.velocity),
                    gravity: source_shot.gravity,
                    max_distance: source_shot.max_distance,
                    max_time_ms: u64::from(launch.max_time_ms),
                    is_he: source_shot.shell.kind == "HIGH_EXPLOSIVE",
                    splash_radius: source_shot.shell.explosion_radius,
                    penetration_factor: deterministic_shot_factor(
                        scope,
                        bot_key,
                        launch.shot_id.fire_seq,
                        0,
                    ),
                    damage_factor: deterministic_shot_factor(
                        scope,
                        bot_key,
                        launch.shot_id.fire_seq,
                        1,
                    ),
                    source_shot,
                    fire_intent_seq: None,
                    fire_input_seq: None,
                };
                let admission = self.engine.commit_bot_launch(
                    scope,
                    u64::from(bot.bot_id),
                    projectile,
                    server_time_ms,
                )?;
                if let LaunchAdmission::New(record) = admission {
                    self.contact_authority.note_fire(bot_key, tick);
                    authority.track_projectile(record, tick)?;
                }
            }
            for event in bot.environment {
                match event {
                    BotCombatEvent::FireTick { .. } => {
                        // The simulator mirrors fire locally for motion/gun
                        // effects. BattleEngine owns the canonical timer,
                        // attacker lineage, HP mutation, and client event.
                    }
                    BotCombatEvent::Destroyed {
                        cause: DeathCause::Drowning,
                        ..
                    } => {
                        let health = self
                            .engine
                            .combat()
                            .get(bot_key)
                            .ok_or(BattleError::UnknownVehicle(bot_key))?
                            .health;
                        if health > 0 {
                            bot_environment_effects.push(EnvironmentDamageEffect {
                                target: bot_key,
                                amount: health,
                                client_simulation_reason: 5,
                            });
                        }
                    }
                    BotCombatEvent::Destroyed {
                        cause: DeathCause::Fire,
                        ..
                    } => {
                        // Canonical fire death is committed by BattleEngine.
                    }
                    BotCombatEvent::FireExtinguished | BotCombatEvent::DrowningState { .. } => {}
                }
            }
            let state = authority
                .bot_state(bot.bot_id)
                .ok_or(AuthorityRuntimeError::UnknownBot { bot_id: bot.bot_id })?;
            self.engine.update_bot_pose(
                scope,
                u64::from(bot.bot_id),
                BodyPose {
                    x: bot.pose.position.x,
                    y: bot.pose.position.y,
                    z: bot.pose.position.z,
                    yaw: bot.pose.yaw,
                    pitch: bot.pose.pitch,
                    roll: bot.pose.roll,
                    speed: bot.pose.speed,
                    aim_yaw: state.aim_yaw,
                    gun_pitch: bot.pose.gun_pitch,
                },
                true,
                u8::try_from(bot.pose.ammo.loaded)
                    .map_err(|_| BattleError::InvalidProjectileEffects)?,
                bot.pose.fire_seq,
            )?;
        }

        if !bot_environment_effects.is_empty() {
            self.engine
                .apply_environment_damage_batch_deferred(scope, &bot_environment_effects)?;
        }
        self.engine.finalize_boundary_elimination()?;
        if self.engine.result().is_some() {
            self.continue_ricochets_or_fail(
                authority,
                projectile_application.ricochet_records,
                server_time_ms,
            )?;
            self.close_terminal_ramming()?;
            self.cancel_all_player_bursts(tick)?;
            let current_actors = self.contact_actor_states();
            let contacts = self
                .contact_authority
                .evaluate_tick(tick, &current_actors)?;
            return Ok(AuthorityAdvanceOutput {
                oracle_requests: Vec::new(),
                contacts,
            });
        }

        // These are the exact post-bot-step roots used by observation intents
        // issued inside `advance_tick`; RAM may correct bot roots afterwards.
        let observation_actors = self.contact_actor_states();
        self.contact_authority
            .record_observation_frame(tick, &observation_actors)?;
        let current_ram_requests = self.advance_current_ramming(authority, tick)?;
        self.engine.finalize_boundary_elimination()?;
        if self.engine.result().is_some() {
            self.continue_ricochets_or_fail(
                authority,
                projectile_application.ricochet_records,
                server_time_ms,
            )?;
            self.close_terminal_ramming()?;
            self.cancel_all_player_bursts(tick)?;
            let current_actors = self.contact_actor_states();
            let contacts = self
                .contact_authority
                .evaluate_tick(tick, &current_actors)?;
            return Ok(AuthorityAdvanceOutput {
                oracle_requests: Vec::new(),
                contacts,
            });
        }
        oracle_requests.extend(current_ram_requests);
        self.continue_ricochets_or_fail(
            authority,
            projectile_application.ricochet_records,
            server_time_ms,
        )?;
        let current_actors = self.contact_actor_states();
        let contacts = self
            .contact_authority
            .evaluate_tick(tick, &current_actors)?;
        Ok(AuthorityAdvanceOutput {
            oracle_requests,
            contacts,
        })
    }

    fn apply_due_projectile_decisions(
        &mut self,
        authority: &mut AuthorityRuntime,
        decisions: Vec<ProjectileFlightDecision>,
        server_time_ms: u64,
    ) -> Result<DueProjectileApplication, BattleLoopError> {
        let scope = self.engine.scope();
        let mut application = DueProjectileApplication::default();
        for decision in decisions {
            match decision {
                ProjectileFlightDecision::Progress {
                    cursor,
                    destructibles,
                    ..
                } => {
                    self.engine.progress_projectile_with_destructibles(
                        scope,
                        cursor,
                        destructibles,
                        server_time_ms,
                    )?;
                }
                ProjectileFlightDecision::Terminal(proposal) => {
                    let projectile_id = proposal.resolution.projectile_id.clone();
                    let Some(record) = self.engine.projectile_record(&projectile_id).cloned()
                    else {
                        // A stale duplicate cannot authorize damage and must not
                        // disturb another projectile or the rest of the round.
                        authority.retire_projectile(&projectile_id);
                        continue;
                    };
                    if matches!(&proposal.cause, ProjectileTerminalCause::OracleTimeout) {
                        self.fail_projectile_locally(authority, &record, server_time_ms)?;
                        continue;
                    }
                    if record.launch.is_he
                        && matches!(
                            &proposal.cause,
                            ProjectileTerminalCause::Direct { .. }
                                | ProjectileTerminalCause::Wreck { .. }
                                | ProjectileTerminalCause::Terrain { .. }
                                | ProjectileTerminalCause::DestructibleBacking { .. }
                                | ProjectileTerminalCause::Destructible { .. }
                        )
                    {
                        application.he_terminals.push((proposal, record));
                        continue;
                    }
                    let direct = match &proposal.cause {
                        ProjectileTerminalCause::Direct {
                            target, native_hit, ..
                        } => {
                            let (verdict, effect) = match self.resolve_direct_projectile(
                                *target,
                                native_hit,
                                &proposal.resolution,
                                server_time_ms,
                            ) {
                                Ok(resolved) => resolved,
                                Err(_) => {
                                    self.fail_projectile_locally(
                                        authority,
                                        &record,
                                        server_time_ms,
                                    )?;
                                    continue;
                                }
                            };
                            if verdict == PenetrationVerdict::Ricochet && record.ricochet_count == 0
                            {
                                let ricochet = match build_first_ricochet(
                                    &record,
                                    &proposal.resolution,
                                    ProjectileVec3 {
                                        x: f64::from(native_hit.normal.x),
                                        y: f64::from(native_hit.normal.y),
                                        z: f64::from(native_hit.normal.z),
                                    },
                                ) {
                                    Ok(ricochet) => ricochet,
                                    Err(_) => {
                                        self.fail_projectile_locally(
                                            authority,
                                            &record,
                                            server_time_ms,
                                        )?;
                                        continue;
                                    }
                                };
                                let admission = match self.engine.continue_projectile_ricochet(
                                    scope,
                                    ricochet,
                                    proposal.destructibles.clone(),
                                    server_time_ms,
                                ) {
                                    Ok(admission) => admission,
                                    Err(_) => {
                                        self.fail_projectile_locally(
                                            authority,
                                            &record,
                                            server_time_ms,
                                        )?;
                                        continue;
                                    }
                                };
                                if let RicochetAdmission::Applied { record } = admission {
                                    application.ricochet_records.push(record);
                                }
                                continue;
                            }
                            Some(effect)
                        }
                        ProjectileTerminalCause::OracleTimeout => unreachable!(),
                        ProjectileTerminalCause::Wreck { .. }
                        | ProjectileTerminalCause::Terrain { .. }
                        | ProjectileTerminalCause::DestructibleBacking { .. }
                        | ProjectileTerminalCause::Destructible { .. }
                        | ProjectileTerminalCause::MaxDistance
                        | ProjectileTerminalCause::MaxTime => None,
                    };
                    if !authority.retire_projectile(&projectile_id) {
                        self.fail_projectile_locally(authority, &record, server_time_ms)?;
                        continue;
                    }
                    if self
                        .engine
                        .resolve_projectile_deferred(
                            scope,
                            ProjectileTerminal {
                                resolution: proposal.resolution,
                                direct,
                                splash: Vec::new(),
                                destructibles: proposal.destructibles,
                            },
                            server_time_ms,
                        )
                        .is_err()
                    {
                        self.fail_projectile_locally(authority, &record, server_time_ms)?;
                    }
                }
                ProjectileFlightDecision::IgnoredAfterTerminal { .. } => {}
            }
        }
        Ok(application)
    }

    /// Close one admitted projectile without trusting the failed native
    /// operation. The canonical ledger publishes an `expired` terminal, while
    /// ammunition and reload state committed at launch continue normally.
    fn fail_projectile_locally(
        &mut self,
        authority: &mut AuthorityRuntime,
        record: &ProjectileRecord,
        server_time_ms: u64,
    ) -> Result<(), BattleLoopError> {
        authority.retire_projectile(&record.projectile_id);
        let terminal = ProjectileTerminal {
            resolution: crate::projectile::ProjectileResolution {
                round_id: record.launch.round_id,
                authority_epoch: record.launch.authority_epoch,
                projectile_id: record.projectile_id.clone(),
                base_checked_ms: record.checked_through_ms,
                outcome: ProjectileOutcome::Expired,
                resolved_time_ms: record.checked_through_ms,
                checked_distance: record.checked_distance,
                piercing_loss: record.piercing_loss,
                penetration_factor: record.launch.penetration_factor,
                impact: None,
            },
            direct: None,
            splash: Vec::new(),
            destructibles: Vec::new(),
        };
        if self.engine.result().is_some() {
            self.engine.resolve_projectile_cleanup_after_finish(
                self.engine.scope(),
                terminal,
                server_time_ms,
            )?;
        } else {
            self.engine.resolve_projectile_deferred(
                self.engine.scope(),
                terminal,
                server_time_ms,
            )?;
        }
        Ok(())
    }

    fn continue_ricochets_or_fail(
        &mut self,
        authority: &mut AuthorityRuntime,
        records: Vec<ProjectileRecord>,
        server_time_ms: u64,
    ) -> Result<(), BattleLoopError> {
        for record in records {
            if self.engine.result().is_some()
                || authority
                    .continue_projectile_ricochet(record.clone())
                    .is_err()
            {
                let current = self
                    .engine
                    .projectile_record(&record.projectile_id)
                    .cloned()
                    .unwrap_or(record);
                self.fail_projectile_locally(authority, &current, server_time_ms)?;
            }
        }
        Ok(())
    }

    fn resolve_direct_projectile(
        &self,
        target: VehicleKey,
        native_hit: &VehicleHit,
        resolution: &crate::projectile::ProjectileResolution,
        server_time_ms: u64,
    ) -> Result<(PenetrationVerdict, ProjectileDamageEffect), BattleLoopError> {
        self.resolve_direct_projectile_with_trace(
            target,
            native_hit,
            resolution,
            server_time_ms,
            None,
        )
    }

    fn resolve_direct_projectile_with_trace(
        &self,
        target: VehicleKey,
        native_hit: &VehicleHit,
        resolution: &crate::projectile::ProjectileResolution,
        server_time_ms: u64,
        explosion_trace: Option<&CriticalTrace>,
    ) -> Result<(PenetrationVerdict, ProjectileDamageEffect), BattleLoopError> {
        let record = self
            .engine
            .projectile_record(&resolution.projectile_id)
            .ok_or_else(|| crate::projectile::ProjectileError::Unknown {
                projectile_id: resolution.projectile_id.clone(),
            })
            .map_err(BattleError::from)?;
        let shot = shot_info(&record.launch.source_shot)?;
        let layers = native_hit
            .layers
            .iter()
            .map(armor_layer)
            .collect::<Vec<_>>();
        let hull_materials = self
            .hull_materials
            .get(&target)
            .ok_or(BattleError::InvalidVehicle)?;
        let he_tuning = record
            .launch
            .source_shot
            .shell
            .he_tuning()?
            .unwrap_or_default();
        let result = resolve_direct_hit_with_base_multiplier(
            &shot,
            resolution.checked_distance,
            &layers,
            hull_materials,
            resolution.piercing_loss,
            ShotFactors::new(
                record.launch.penetration_factor,
                record.launch.damage_factor,
            )?,
            record.base_penetration_multiplier,
            he_tuning,
        )?;
        if explosion_trace.is_some() && shot.kind != ShellKind::HighExplosive {
            return Err(BattleError::InvalidProjectileEffects.into());
        }
        let native_trace;
        let trace = if let Some(trace) = explosion_trace {
            trace
        } else {
            native_trace = critical_trace_from_vehicle_hit(native_hit);
            &native_trace
        };
        let critical_targets = trace
            .native_layers
            .iter()
            .filter_map(|layer| layer.target)
            .chain(trace.internal_hits.iter().flatten().map(|hit| hit.target))
            .collect::<BTreeSet<_>>();
        let samples = CriticalSamples {
            module_damage_factor: deterministic_shot_factor(
                self.engine.scope(),
                record.launch.shooter,
                record.launch.shot_seq,
                2,
            ),
            target_rolls: critical_targets
                .into_iter()
                .map(|critical_target| {
                    (
                        critical_target,
                        deterministic_shot_unit(
                            self.engine.scope(),
                            record.launch.shooter,
                            record.launch.shot_seq,
                            critical_target_lane(critical_target),
                        ),
                    )
                })
                .collect(),
            engine_fire_roll: Some(deterministic_shot_unit(
                self.engine.scope(),
                record.launch.shooter,
                record.launch.shot_seq,
                24,
            )),
        };
        let current_hull_health = self
            .engine
            .combat()
            .get(target)
            .ok_or(BattleError::UnknownVehicle(target))?
            .health;
        let by_explosion = shot.kind == ShellKind::HighExplosive;
        let critical = self.engine.propose_critical_strike(
            target,
            trace,
            StrikeInput {
                hull_damage: result.damage,
                current_hull_health,
                shell: CriticalShell {
                    kind: critical_shell_kind(shot.kind),
                    module_damage: Some(record.launch.source_shot.shell.damage[1]),
                },
                penetrated: (!by_explosion)
                    .then_some(result.verdict == PenetrationVerdict::Penetration),
                by_explosion,
                dead_eye: record.launch.source_shot.deadeye,
                distance_filters: !by_explosion,
                now_ms: Some(server_time_ms),
            },
            &samples,
        )?;
        let hull_damage = critical.hull_damage;
        Ok((
            result.verdict,
            ProjectileDamageEffect {
                damage: DamageProposal {
                    attacker: Some(record.launch.shooter),
                    target,
                    amount: hull_damage,
                    source: DamageSource::Shot,
                },
                shot_result: result.verdict.code(),
                potential_damage: result.rolled_damage.max(hull_damage),
                critical: Some(critical),
                stun_end_server_time_ms: None,
            },
        ))
    }

    fn schedule_he_terminal(
        &mut self,
        authority: &mut AuthorityRuntime,
        terminal: ProjectileTerminalProposal,
        record: ProjectileRecord,
        server_time_ms: u64,
    ) -> Result<Option<OracleV1BatchRequest>, BattleLoopError> {
        let impact = terminal
            .resolution
            .impact
            .ok_or(BattleLoopError::InvalidHeExplosionEvidence)?;
        let direct_target = match &terminal.cause {
            ProjectileTerminalCause::Direct { target, .. } => Some(*target),
            _ => None,
        };
        let radius = he_radius(&shot_info(&record.launch.source_shot)?)?;
        let targets = self.freeze_he_target_intents(authority, impact, radius, direct_target)?;
        if targets.is_empty() {
            let projectile_id = terminal.resolution.projectile_id.clone();
            if !authority.retire_projectile(&projectile_id) {
                return Err(BattleLoopError::InvalidHeExplosionEvidence);
            }
            self.engine.resolve_projectile_deferred(
                self.engine.scope(),
                ProjectileTerminal {
                    resolution: terminal.resolution,
                    direct: None,
                    splash: Vec::new(),
                    destructibles: terminal.destructibles,
                },
                server_time_ms,
            )?;
            return Ok(None);
        }

        let intent = HeExplosionEvidenceIntent::from_terminal(&record, &terminal, targets)
            .ok_or(BattleLoopError::InvalidHeExplosionEvidence)?;
        let key = HeExplosionEvidenceIntentKey {
            plan_id: intent.plan_id,
        };
        let previous = self.pending_he_terminals.get(&key).cloned();
        if previous.as_ref().is_some_and(|pending| {
            pending.terminal != terminal
                || pending.record != record
                || pending.intent != intent
                || pending.direct_target != direct_target
        }) {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        match authority.schedule_he_explosion_evidence(intent.clone())? {
            HeExplosionEvidenceSchedule::New { request } => {
                if previous.is_some() {
                    return Err(BattleLoopError::InvalidHeExplosionEvidence);
                }
                let batch_key = request.key();
                self.pending_he_terminals.insert(
                    key,
                    PendingHeTerminal {
                        terminal,
                        record,
                        intent,
                        batch_key,
                        direct_target,
                    },
                );
                Ok(Some(request))
            }
            HeExplosionEvidenceSchedule::ExactRetry {
                key: batch_key,
                apply_tick,
            } => {
                let previous = previous.ok_or(BattleLoopError::InvalidHeExplosionEvidence)?;
                if previous.batch_key != batch_key || previous.intent.apply_tick != apply_tick {
                    return Err(BattleLoopError::InvalidHeExplosionEvidence);
                }
                Ok(None)
            }
        }
    }

    fn freeze_he_target_intents(
        &self,
        authority: &AuthorityRuntime,
        impact: ProjectileVec3,
        radius: f64,
        direct_target: Option<VehicleKey>,
    ) -> Result<Vec<HeExplosionEvidenceTargetIntent>, BattleLoopError> {
        if !radius.is_finite() || radius < 0.0 {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        let mut targets = Vec::new();
        for entity in self.engine.entities().filter(|entity| entity.combat.alive) {
            let dx = entity.pose.x - impact.x;
            let dy = entity.pose.y - impact.y;
            let dz = entity.pose.z - impact.z;
            let distance = (dx * dx + dy * dy + dz * dz).sqrt();
            if Some(entity.key) != direct_target && (!distance.is_finite() || distance > radius) {
                continue;
            }
            if !self.hull_materials.contains_key(&entity.key) {
                return Err(BattleError::InvalidVehicle.into());
            }
            let (turret_yaw, gun_pitch, siege_state) = match entity.key.kind {
                VehicleKind::Player => (
                    angle_delta(entity.pose.yaw, entity.pose.aim_yaw),
                    entity.pose.gun_pitch,
                    self.engine.siege_status(entity.key.id).0,
                ),
                VehicleKind::Bot => {
                    let bot_id =
                        u32::try_from(entity.key.id).map_err(|_| BattleError::InvalidVehicle)?;
                    let state = authority
                        .bot_state(bot_id)
                        .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
                    (state.turret_yaw, state.gun_pitch, 0)
                }
            };
            targets.push(HeExplosionEvidenceTargetIntent {
                vehicle: entity.key,
                target_pose: ExplosionTargetPose {
                    position: OracleVec3 {
                        x: entity.pose.x as f32,
                        y: entity.pose.y as f32,
                        z: entity.pose.z as f32,
                    },
                    yaw: entity.pose.yaw,
                    pitch: entity.pose.pitch,
                    roll: entity.pose.roll,
                    turret_yaw,
                    gun_pitch,
                    siege_state,
                },
            });
        }
        targets.sort_by_key(|target| target.vehicle);
        if targets.len() > MAX_FROZEN_HE_TARGETS {
            return Err(ProjectileFlightError::FrozenHeCapacity.into());
        }
        Ok(targets)
    }

    fn apply_due_he_terminals(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
        server_time_ms: u64,
        evidence: Vec<NativeHeExplosionEvidenceSample>,
        unavailable: Vec<HeExplosionEvidenceIntentKey>,
        timed_out: Vec<HeExplosionEvidenceIntentKey>,
    ) -> Result<(), BattleLoopError> {
        let mut received = BTreeMap::<HeExplosionEvidenceIntentKey, Vec<DueHeTerminal>>::new();
        for item in evidence
            .into_iter()
            .map(DueHeTerminal::Evidence)
            .chain(unavailable.into_iter().map(DueHeTerminal::Unavailable))
            .chain(timed_out.into_iter().map(DueHeTerminal::TimedOut))
        {
            received.entry(item.key()).or_default().push(item);
        }

        let mut due = self
            .pending_he_terminals
            .iter()
            .filter(|(_, pending)| pending.intent.apply_tick <= tick)
            .map(|(&key, pending)| (pending.batch_key, key))
            .collect::<Vec<_>>();
        due.sort_by_key(|(batch_key, _)| *batch_key);
        for (_, key) in due {
            let Some(pending) = self.pending_he_terminals.get(&key).cloned() else {
                continue;
            };
            let items = received.remove(&key).unwrap_or_default();
            let applied = if pending.intent.apply_tick == tick && items.len() == 1 {
                match &items[0] {
                    DueHeTerminal::Evidence(sample) if sample.batch_key == pending.batch_key => {
                        self.finalize_he_terminal(authority, &pending, sample, server_time_ms)
                            .is_ok()
                    }
                    DueHeTerminal::Unavailable(_) | DueHeTerminal::TimedOut(_) => false,
                    DueHeTerminal::Evidence(_) => false,
                }
            } else {
                false
            };
            if !applied {
                self.fail_projectile_locally(authority, &pending.record, server_time_ms)?;
            }
            self.pending_he_terminals.remove(&key);
        }
        // Unknown, stale, duplicated, or early evidence is deliberately
        // discarded. Its identity fences did not authorize any local work.
        Ok(())
    }

    fn finalize_he_terminal(
        &mut self,
        authority: &mut AuthorityRuntime,
        pending: &PendingHeTerminal,
        evidence: &NativeHeExplosionEvidenceSample,
        server_time_ms: u64,
    ) -> Result<(), BattleLoopError> {
        if self.engine.projectile_record(&pending.record.projectile_id) != Some(&pending.record) {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        let (direct, splash) =
            self.resolve_he_evidence_effects(pending, evidence, server_time_ms)?;
        if !authority.retire_projectile(&pending.record.projectile_id) {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        self.engine.resolve_projectile_deferred(
            self.engine.scope(),
            ProjectileTerminal {
                resolution: pending.terminal.resolution.clone(),
                direct,
                splash,
                destructibles: pending.terminal.destructibles.clone(),
            },
            server_time_ms,
        )?;
        Ok(())
    }

    fn resolve_he_evidence_effects(
        &self,
        pending: &PendingHeTerminal,
        sample: &NativeHeExplosionEvidenceSample,
        server_time_ms: u64,
    ) -> Result<(Option<ProjectileDamageEffect>, Vec<ProjectileDamageEffect>), BattleLoopError>
    {
        if sample.key.plan_id != pending.intent.plan_id
            || sample.projectile_id != pending.intent.projectile_id
            || sample.batch_key != pending.batch_key
            || sample.issued_tick != pending.intent.issued_tick
            || sample.apply_tick != pending.intent.apply_tick
            || sample.targets.len() != pending.intent.targets.len()
        {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        let received = sample
            .targets
            .iter()
            .map(|target| (target.vehicle, target))
            .collect::<BTreeMap<_, _>>();
        if received.len() != sample.targets.len() {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        let scope = self.engine.scope();
        let shooter = pending.record.launch.shooter;
        let shot_seq = pending.record.launch.shot_seq;
        let mut frozen = Vec::with_capacity(pending.intent.targets.len());
        for expected in &pending.intent.targets {
            let target = received
                .get(&expected.vehicle)
                .ok_or(BattleLoopError::InvalidHeExplosionEvidence)?;
            if target.query.target_pose != expected.target_pose {
                return Err(BattleLoopError::InvalidHeExplosionEvidence);
            }
            frozen.push(frozen_he_target_from_explosion_evidence(
                expected.vehicle,
                &target.query,
                target.evidence.clone(),
                self.hull_materials
                    .get(&expected.vehicle)
                    .cloned()
                    .ok_or(BattleError::InvalidVehicle)?,
                deterministic_target_shot_factor(scope, shooter, shot_seq, expected.vehicle, 32),
            )?);
        }
        let direct_trace = pending.direct_target.and_then(|target| {
            frozen
                .iter()
                .find(|candidate| candidate.target == target)
                .map(|candidate| &candidate.critical_trace)
        });
        if pending.direct_target.is_some() && direct_trace.is_none() {
            return Err(BattleLoopError::InvalidHeExplosionEvidence);
        }
        let direct = match (&pending.terminal.cause, direct_trace) {
            (
                ProjectileTerminalCause::Direct {
                    target, native_hit, ..
                },
                Some(trace),
            ) if self
                .engine
                .combat()
                .get(*target)
                .is_some_and(|state| state.alive) =>
            {
                Some(
                    self.resolve_direct_projectile_with_trace(
                        *target,
                        native_hit,
                        &pending.terminal.resolution,
                        server_time_ms,
                        Some(trace),
                    )?
                    .1,
                )
            }
            (ProjectileTerminalCause::Direct { .. }, Some(_)) => None,
            (_, None) => None,
            _ => return Err(BattleLoopError::InvalidHeExplosionEvidence),
        };
        let tuning = pending
            .record
            .launch
            .source_shot
            .shell
            .he_tuning()?
            .ok_or(BattleError::InvalidProjectileEffects)?;
        let splash = resolve_frozen_he_splash(
            &pending.record,
            &pending.terminal.resolution,
            pending.direct_target,
            &frozen,
            tuning,
        )?
        .into_iter()
        .filter(|resolved| {
            self.engine
                .combat()
                .get(resolved.target)
                .is_some_and(|state| state.alive)
        })
        .map(|resolved| self.frozen_he_splash_effect(&pending.record, resolved, server_time_ms))
        .collect::<Result<Vec<_>, _>>()?;
        Ok((direct, splash))
    }

    fn frozen_he_splash_effect(
        &self,
        record: &ProjectileRecord,
        splash: FrozenHeSplash,
        server_time_ms: u64,
    ) -> Result<ProjectileDamageEffect, BattleLoopError> {
        let scope = self.engine.scope();
        let shooter = record.launch.shooter;
        let shot_seq = record.launch.shot_seq;
        let critical_targets = splash
            .critical_trace
            .native_layers
            .iter()
            .filter_map(|layer| layer.target)
            .chain(
                splash
                    .critical_trace
                    .internal_hits
                    .iter()
                    .flatten()
                    .map(|hit| hit.target),
            )
            .collect::<BTreeSet<_>>();
        let samples = CriticalSamples {
            module_damage_factor: deterministic_target_shot_factor(
                scope,
                shooter,
                shot_seq,
                splash.target,
                33,
            ),
            target_rolls: critical_targets
                .into_iter()
                .map(|critical_target| {
                    (
                        critical_target,
                        deterministic_target_shot_unit(
                            scope,
                            shooter,
                            shot_seq,
                            splash.target,
                            critical_target_lane(critical_target),
                        ),
                    )
                })
                .collect(),
            engine_fire_roll: Some(deterministic_target_shot_unit(
                scope,
                shooter,
                shot_seq,
                splash.target,
                34,
            )),
        };
        let current_hull_health = self
            .engine
            .combat()
            .get(splash.target)
            .ok_or(BattleError::UnknownVehicle(splash.target))?
            .health;
        let critical = self.engine.propose_critical_strike(
            splash.target,
            &splash.critical_trace,
            StrikeInput {
                hull_damage: splash.hull_damage,
                current_hull_health,
                shell: CriticalShell {
                    kind: CriticalShellKind::HighExplosive,
                    module_damage: Some(record.launch.source_shot.shell.damage[1]),
                },
                penetrated: None,
                by_explosion: true,
                dead_eye: record.launch.source_shot.deadeye,
                distance_filters: false,
                now_ms: Some(server_time_ms),
            },
            &samples,
        )?;
        let hull_damage = critical.hull_damage;
        Ok(ProjectileDamageEffect {
            damage: DamageProposal {
                attacker: Some(shooter),
                target: splash.target,
                amount: hull_damage,
                source: DamageSource::Shot,
            },
            shot_result: PenetrationVerdict::Penetration.code(),
            potential_damage: splash.hull_damage.max(hull_damage),
            critical: Some(critical),
            stun_end_server_time_ms: None,
        })
    }

    fn prepare_ram_bot_deltas<'a>(
        &self,
        authority: &AuthorityRuntime,
        deltas: impl IntoIterator<Item = &'a RamBodyDelta>,
    ) -> Result<
        (
            Option<PreparedBotRamMutation>,
            BTreeMap<VehicleKey, BodyPose>,
        ),
        BattleLoopError,
    > {
        let mut aggregated = BTreeMap::<VehicleKey, RamBodyDelta>::new();
        for delta in deltas {
            if delta.key.kind != VehicleKind::Bot {
                continue;
            }
            let aggregate = aggregated.entry(delta.key).or_insert(RamBodyDelta {
                key: delta.key,
                correction_x: 0.0,
                correction_z: 0.0,
                velocity_x: 0.0,
                velocity_z: 0.0,
            });
            aggregate.correction_x += delta.correction_x;
            aggregate.correction_z += delta.correction_z;
            aggregate.velocity_x += delta.velocity_x;
            aggregate.velocity_z += delta.velocity_z;
        }
        aggregated.retain(|_, delta| {
            delta.correction_x != 0.0
                || delta.correction_z != 0.0
                || delta.velocity_x != 0.0
                || delta.velocity_z != 0.0
        });
        if aggregated.is_empty() {
            return Ok((None, BTreeMap::new()));
        }
        let bot_deltas = aggregated
            .values()
            .map(|delta| {
                Ok(BotRamDelta {
                    bot_id: u32::try_from(delta.key.id).map_err(|_| BattleError::InvalidVehicle)?,
                    correction_x: delta.correction_x,
                    correction_z: delta.correction_z,
                    velocity_x: delta.velocity_x,
                    velocity_z: delta.velocity_z,
                })
            })
            .collect::<Result<Vec<_>, BattleLoopError>>()?;
        let mutation = authority.prepare_bot_ram_mutation(&bot_deltas)?;
        let mut poses = BTreeMap::new();
        for bot_id in mutation.bot_ids() {
            let state = mutation
                .bot_state(bot_id)
                .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
            let key = VehicleKey {
                kind: VehicleKind::Bot,
                id: u64::from(bot_id),
            };
            let mut pose = self
                .engine
                .body_pose(key)
                .ok_or(BattleError::UnknownVehicle(key))?;
            pose.x = state.position.x;
            pose.y = state.position.y;
            pose.z = state.position.z;
            pose.yaw = state.yaw;
            pose.pitch = state.pitch;
            pose.roll = state.roll;
            pose.speed = state.speed;
            pose.aim_yaw = state.aim_yaw;
            pose.gun_pitch = state.gun_pitch;
            poses.insert(key, pose);
        }
        Ok((Some(mutation), poses))
    }

    fn predicted_ram_bot_bodies(
        &self,
        bodies: &[RamBody],
        mutation: Option<&PreparedBotRamMutation>,
        operations: &[AtomicRamDamage],
    ) -> Result<Vec<RamBody>, BattleLoopError> {
        let mut damage = BTreeMap::<VehicleKey, u64>::new();
        for operation in operations {
            for proposal in [&operation.first, &operation.second] {
                let total = damage.entry(proposal.target).or_default();
                *total = total.saturating_add(u64::from(proposal.amount));
            }
        }
        bodies
            .iter()
            .filter(|body| body.key.kind == VehicleKind::Bot)
            .map(|body| {
                let mut body = body.clone();
                let combat = self
                    .engine
                    .combat()
                    .get(body.key)
                    .ok_or(BattleError::UnknownVehicle(body.key))?;
                body.alive = combat.alive
                    && damage.get(&body.key).copied().unwrap_or(0) < u64::from(combat.health);
                if let Some(mutation) = mutation {
                    let bot_id =
                        u32::try_from(body.key.id).map_err(|_| BattleError::InvalidVehicle)?;
                    if let Some(state) = mutation.bot_state(bot_id) {
                        let velocity = mutation
                            .bot_ram_velocity(bot_id)
                            .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
                        body.x = state.position.x;
                        body.y = state.position.y;
                        body.z = state.position.z;
                        body.yaw = state.yaw;
                        body.pitch = state.pitch;
                        body.roll = state.roll;
                        body.vx = velocity.x;
                        body.vy = velocity.y;
                        body.vz = velocity.z;
                        body.turret_yaw = state.turret_yaw;
                        body.gun_pitch = state.gun_pitch;
                    }
                }
                Ok(body)
            })
            .collect()
    }

    /// Retire admitted RAM work without applying HP after terminal commit.
    fn close_terminal_ramming(&mut self) -> Result<(), BattleLoopError> {
        let mut staged_ram = self.ram.clone();
        staged_ram.finish_pending_receipts()?;
        for state in self.bot_ram_contact_episodes.values_mut() {
            state.pending = None;
        }
        self.ram = staged_ram;
        self.pending_ram_contacts.clear();
        Ok(())
    }

    /// Apply oracle work that became due at this boundary before any new
    /// environment, burst, bot, or projectile mutation can end the round.
    fn apply_due_ramming(
        &mut self,
        authority: &mut AuthorityRuntime,
        native_evidence: Vec<NativeRamContactEvidence>,
        unavailable: Vec<RamContactArmorIntentKey>,
        timed_out: Vec<RamContactArmorIntentKey>,
    ) -> Result<(), BattleLoopError> {
        let bodies = self.ram_bodies(authority)?;
        let combat_live = self.engine.combat_live();
        let mut staged_ram = self.ram.clone();
        let mut staged_pending = self.pending_ram_contacts.clone();
        let mut staged_episodes = self.bot_ram_contact_episodes.clone();
        let mut fixed_damage = Vec::<AtomicRamDamage>::new();

        let mut received = BTreeMap::<RamContactArmorIntentKey, Vec<DueRamContact>>::new();
        for item in native_evidence
            .into_iter()
            .map(DueRamContact::Evidence)
            .chain(unavailable.into_iter().map(DueRamContact::Unavailable))
            .chain(timed_out.into_iter().map(DueRamContact::TimedOut))
        {
            received.entry(item.key()).or_default().push(item);
        }

        for (key, mut items) in received {
            let Some(probe) = staged_pending.remove(&key) else {
                // A stale or unknown receipt cannot mutate any live contact.
                continue;
            };
            let resolved = if combat_live && items.len() == 1 {
                match items.pop().expect("one RAM outcome was checked above") {
                    DueRamContact::Evidence(evidence) => match probe.source {
                        crate::ram::RamResolutionSource::FixedTick { .. } => {
                            match staged_ram.resolve_fixed_contact_probe(&probe, evidence) {
                                Ok(resolution) => {
                                    let damaging = resolution.damage.is_some();
                                    if let Some(damage) = resolution.damage {
                                        fixed_damage.push(damage);
                                    }
                                    if let Some(state) = staged_episodes.get_mut(&probe.pair) {
                                        if state.episode == probe.cursor.episode() {
                                            if state.pending == Some(probe.cursor) {
                                                state.pending = None;
                                            }
                                            if damaging && state.overlapping {
                                                state.damaging = true;
                                            }
                                        }
                                    }
                                    true
                                }
                                Err(_) => false,
                            }
                        }
                        crate::ram::RamResolutionSource::PlayerReceipt { .. } => {
                            let mut candidate = staged_ram.clone();
                            match candidate.prepare_player_receipts_with_native_evidence(
                                &bodies,
                                std::slice::from_ref(&evidence),
                            ) {
                                Ok(_) => {
                                    staged_ram = candidate;
                                    true
                                }
                                Err(_) => false,
                            }
                        }
                        crate::ram::RamResolutionSource::PlayerPairReceipt { .. } => {
                            let mut candidate = staged_ram.clone();
                            match candidate.resolve_player_pair_contact_probe(&probe, evidence) {
                                Ok(_) => {
                                    staged_ram = candidate;
                                    true
                                }
                                Err(_) => false,
                            }
                        }
                    },
                    DueRamContact::Unavailable(_) | DueRamContact::TimedOut(_) => false,
                }
            } else {
                false
            };
            if !resolved {
                Self::make_ram_probe_unavailable(&mut staged_ram, &mut staged_episodes, &probe)?;
            }
        }

        let receipts = staged_ram.prepare_player_receipts(&bodies)?;
        staged_ram.validate_player_resolutions(&receipts)?;
        staged_ram.commit_player_resolutions(&receipts)?;
        let player_pair_receipts = staged_ram.commit_ready_player_pair_resolutions()?;
        let (mutation, poses) = if combat_live {
            self.prepare_ram_bot_deltas(authority, receipts.iter().flat_map(RamResolution::deltas))?
        } else {
            (None, BTreeMap::new())
        };
        let operations = if combat_live {
            fixed_damage
                .into_iter()
                .chain(
                    receipts
                        .iter()
                        .filter_map(|receipt| receipt.damage.as_ref())
                        .cloned(),
                )
                .chain(
                    player_pair_receipts
                        .iter()
                        .filter_map(|receipt| receipt.damage.as_ref())
                        .cloned(),
                )
                .collect::<Vec<_>>()
        } else {
            Vec::new()
        };
        if !poses.is_empty() || !operations.is_empty() {
            self.engine.apply_ram_batch_with_poses_deferred(
                self.engine.scope(),
                &operations,
                &poses,
            )?;
        }
        if let Some(mutation) = mutation {
            authority.commit_bot_ram_mutation(mutation);
        }
        self.ram = staged_ram;
        self.pending_ram_contacts = staged_pending;
        self.bot_ram_contact_episodes = staged_episodes;
        Ok(())
    }

    fn make_ram_probe_unavailable(
        ram: &mut RamAuthority,
        episodes: &mut BTreeMap<RamPair, BotRamEpisodeState>,
        probe: &RamContactProbe,
    ) -> Result<(), BattleLoopError> {
        match probe.source {
            crate::ram::RamResolutionSource::PlayerReceipt { .. } => {
                ram.prepare_player_probe_unavailable(probe)?;
            }
            crate::ram::RamResolutionSource::PlayerPairReceipt { .. } => {
                ram.prepare_player_pair_probe_unavailable(probe)?;
            }
            crate::ram::RamResolutionSource::FixedTick { .. } => {
                if let Some(state) = episodes.get_mut(&probe.pair) {
                    if state.episode == probe.cursor.episode()
                        && state.pending == Some(probe.cursor)
                    {
                        state.pending = None;
                    }
                }
            }
        }
        Ok(())
    }

    #[cfg(test)]
    fn advance_ramming(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
        native_evidence: Vec<NativeRamContactEvidence>,
        unavailable: Vec<RamContactArmorIntentKey>,
        timed_out: Vec<RamContactArmorIntentKey>,
    ) -> Result<Vec<OracleV1BatchRequest>, BattleLoopError> {
        self.apply_due_ramming(authority, native_evidence, unavailable, timed_out)?;
        self.advance_current_ramming(authority, tick)
    }

    fn advance_current_ramming(
        &mut self,
        authority: &mut AuthorityRuntime,
        tick: u64,
    ) -> Result<Vec<OracleV1BatchRequest>, BattleLoopError> {
        let time_us = tick_offset(tick).as_micros().min(u128::from(u64::MAX)) as u64;
        let bodies = self.ram_bodies(authority)?;
        let combat_live = self.engine.combat_live();
        let mut staged_ram = self.ram.clone();
        let mut staged_pending = self.pending_ram_contacts.clone();
        let mut staged_episodes = self.bot_ram_contact_episodes.clone();

        let mut new_probes = Vec::new();
        let mut frame = crate::ram::RamFrameResolution {
            contacts: Vec::new(),
        };
        let mut immediate_player_responses = Vec::new();
        if combat_live {
            // Human-to-bot collision response was already presented by the
            // visible client and is admitted only through its strict receipt.
            // The fixed frame therefore resolves canonical bot-to-bot pairs.
            let bot_bodies = bodies
                .iter()
                .filter(|body| body.key.kind == VehicleKind::Bot)
                .cloned()
                .collect::<Vec<_>>();
            let probe_cursor = RamSourceCursor::new(0, tick)?;
            let bot_probes = staged_ram.fixed_contact_probes(time_us, probe_cursor, &bot_bodies)?;
            frame = staged_ram.resolve_frame(time_us, &bot_bodies)?;
            let overlapping = frame
                .contacts
                .iter()
                .map(|contact| contact.pair)
                .collect::<BTreeSet<_>>();
            for (&pair, state) in &mut staged_episodes {
                if !overlapping.contains(&pair) {
                    state.overlapping = false;
                    state.damaging = false;
                    state.pending = None;
                }
            }
            for pair in &overlapping {
                let state = staged_episodes.entry(*pair).or_default();
                if !state.overlapping {
                    state.episode = state
                        .episode
                        .checked_add(1)
                        .ok_or(BattleLoopError::InvalidRamContactEvidence)?;
                    state.damaging = false;
                    state.pending = None;
                }
                state.overlapping = true;
            }
            for mut probe in bot_probes {
                let state = staged_episodes
                    .get_mut(&probe.pair)
                    .ok_or(BattleLoopError::InvalidRamContactEvidence)?;
                if state.damaging || state.pending.is_some() {
                    continue;
                }
                let cursor = RamSourceCursor::new(state.episode, tick)?;
                probe.cursor = cursor;
                state.pending = Some(cursor);
                new_probes.push(probe);
            }

            for probe in staged_ram.player_contact_probes(&bodies)? {
                let key = RamContactArmorIntentKey {
                    pair: probe.pair,
                    cursor: probe.cursor,
                };
                if staged_pending.contains_key(&key)
                    || new_probes
                        .iter()
                        .any(|pending| pending.pair == probe.pair && pending.cursor == probe.cursor)
                {
                    continue;
                }
                immediate_player_responses.push(staged_ram.player_contact_response(&probe)?);
                staged_ram.mark_player_contact_response_applied(&probe)?;
                new_probes.push(probe);
            }

            for probe in staged_ram.prepare_player_pair_contact_probes(&self.player_ram_timeline)? {
                let key = RamContactArmorIntentKey {
                    pair: probe.pair,
                    cursor: probe.cursor,
                };
                if staged_pending.contains_key(&key)
                    || new_probes
                        .iter()
                        .any(|pending| pending.pair == probe.pair && pending.cursor == probe.cursor)
                {
                    continue;
                }
                new_probes.push(probe);
            }
        }

        let receipts = staged_ram.prepare_player_receipts(&bodies)?;
        staged_ram.validate_player_resolutions(&receipts)?;
        staged_ram.commit_player_resolutions(&receipts)?;
        let player_pair_receipts = staged_ram.commit_ready_player_pair_resolutions()?;

        let (mutation, poses) = if combat_live {
            self.prepare_ram_bot_deltas(
                authority,
                frame
                    .deltas()
                    .chain(
                        immediate_player_responses
                            .iter()
                            .flat_map(RamResolution::deltas),
                    )
                    .chain(receipts.iter().flat_map(RamResolution::deltas)),
            )?
        } else {
            (None, BTreeMap::new())
        };
        let operations = if combat_live {
            frame
                .damage_transactions()
                .cloned()
                .chain(
                    receipts
                        .iter()
                        .filter_map(|receipt| receipt.damage.as_ref())
                        .cloned(),
                )
                .chain(
                    player_pair_receipts
                        .iter()
                        .filter_map(|receipt| receipt.damage.as_ref())
                        .cloned(),
                )
                .collect::<Vec<AtomicRamDamage>>()
        } else {
            Vec::new()
        };

        let apply_tick = tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(BattleError::InvalidVehicle)?;
        let mut intents = Vec::with_capacity(new_probes.len());
        for probe in &new_probes {
            let first_profile = *self
                .vehicle_ram_profiles
                .get(&probe.pair.first)
                .ok_or(BattleError::InvalidVehicle)?;
            let second_profile = *self
                .vehicle_ram_profiles
                .get(&probe.pair.second)
                .ok_or(BattleError::InvalidVehicle)?;
            let intent = RamContactArmorIntent {
                pair: probe.pair,
                cursor: probe.cursor,
                source_time_us: probe.source_time_us,
                issued_tick: tick,
                apply_tick,
                first_pose: ram_probe_pose(&probe.first),
                second_pose: ram_probe_pose(&probe.second),
                contact_point: BotVec3::new(probe.contact_x, probe.contact_y, probe.contact_z),
                contact_normal: BotVec3::new(probe.normal_x, 0.0, probe.normal_z),
                first_profile,
                second_profile,
                first_moving: probe.first_moving,
                second_moving: probe.second_moving,
            };
            intents.push(intent);
            let key = RamContactArmorIntentKey {
                pair: probe.pair,
                cursor: probe.cursor,
            };
            if staged_pending.insert(key, probe.clone()).is_some() {
                return Err(BattleLoopError::InvalidRamContactEvidence);
            }
        }
        let prepared_schedule: Option<PreparedRamContactArmorBatch> = if intents.is_empty() {
            None
        } else {
            Some(authority.prepare_ram_contact_armor_batch(intents)?)
        };
        let published = self.predicted_ram_bot_bodies(&bodies, mutation.as_ref(), &operations)?;
        staged_ram.record_bot_frame(tick, time_us, &published)?;

        if !poses.is_empty() || !operations.is_empty() {
            self.engine.apply_ram_batch_with_poses_deferred(
                self.engine.scope(),
                &operations,
                &poses,
            )?;
        }
        if let Some(mutation) = mutation {
            authority.commit_bot_ram_mutation(mutation);
        }
        let oracle_requests = prepared_schedule.map_or_else(Vec::new, |prepared| {
            authority.commit_ram_contact_armor_batch(prepared)
        });

        self.ram = staged_ram;
        self.pending_ram_contacts = staged_pending;
        self.bot_ram_contact_episodes = staged_episodes;
        Ok(oracle_requests)
    }

    fn advance_player_environment(
        &mut self,
        tick: u64,
        samples: Vec<crate::authority_runtime::NativePlayerEnvironmentSample>,
    ) -> Result<(), BattleLoopError> {
        let mut evidence = BTreeMap::new();
        for sample in samples {
            if !self.player_environment.contains_key(&sample.player_id)
                || evidence.insert(sample.player_id, sample.evidence).is_some()
            {
                return Err(BattleLoopError::InvalidPlayerEnvironmentEvidence);
            }
        }
        let combat_live = self.engine.combat_live();
        let actors = self
            .engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| {
                let up_cosine = self
                    .engine
                    .player_up_cosine(entity.key.id)
                    .ok_or(BattleLoopError::InvalidPlayerEnvironmentEvidence)?;
                Ok((
                    entity.key.id,
                    entity.combat.alive,
                    entity.combat.health,
                    entity.combat.max_health,
                    entity.world_pose,
                    entity.pose,
                    up_cosine,
                ))
            })
            .collect::<Result<Vec<_>, BattleLoopError>>()?;
        if actors.len() != self.player_environment.len() {
            return Err(BattleLoopError::InvalidPlayerEnvironmentEvidence);
        }
        let mut effects = Vec::new();
        for (player_id, alive, health, max_health, world_pose, pose, up_cosine) in actors {
            let decision = self
                .player_environment
                .get_mut(&player_id)
                .ok_or(BattleLoopError::InvalidPlayerEnvironmentEvidence)?
                .advance(PlayerEnvironmentTick {
                    tick,
                    combat_live,
                    alive,
                    health,
                    max_health,
                    world_pose,
                    pose,
                    up_cosine,
                    evidence: evidence.remove(&player_id),
                })?;
            if let Some(decision) = decision {
                effects.push(EnvironmentDamageEffect {
                    target: VehicleKey {
                        kind: VehicleKind::Player,
                        id: decision.player_id,
                    },
                    amount: decision.amount,
                    client_simulation_reason: decision.cause.death_reason(),
                });
            }
        }
        if !evidence.is_empty() {
            return Err(BattleLoopError::InvalidPlayerEnvironmentEvidence);
        }
        if !effects.is_empty() {
            self.engine
                .apply_environment_damage_batch_deferred(self.engine.scope(), &effects)?;
        }
        Ok(())
    }

    fn apply_destructible_hulls(
        &mut self,
        tick: u64,
        samples: Vec<crate::authority_runtime::NativeDestructibleHullSample>,
    ) -> Result<(), BattleLoopError> {
        if samples.is_empty() {
            return Ok(());
        }
        let mut actors = BTreeSet::new();
        let mut already_destroyed = self
            .engine
            .destructibles()
            .entries()
            .map(|entry| entry.receipt.key)
            .collect::<BTreeSet<_>>();
        let mut tick_receipts = Vec::new();
        let catalog = self
            .destructible_catalog
            .as_ref()
            .ok_or(crate::destructible::DestructibleError::InvalidCatalog)
            .map_err(BattleError::from)?;
        for sample in samples {
            if sample.apply_tick != tick || !actors.insert(sample.vehicle) {
                return Err(BattleLoopError::InvalidDestructibleHullEvidence);
            }
            let vehicle_mass = *self
                .vehicle_masses
                .get(&sample.vehicle)
                .ok_or(BattleError::InvalidVehicle)?;
            let receipts = DestructibleAuthority::hull_receipts(
                catalog,
                &sample.evidence,
                &already_destroyed,
                sample.yaw,
                sample.kinetic_speed,
                vehicle_mass,
            )
            .map_err(BattleError::from)?;
            already_destroyed.extend(receipts.iter().map(|receipt| receipt.key));
            tick_receipts.extend(receipts);
        }
        if !tick_receipts.is_empty() {
            self.engine
                .commit_hull_destructibles(self.engine.scope(), tick_receipts)?;
        }
        Ok(())
    }

    fn ram_bodies(&self, authority: &AuthorityRuntime) -> Result<Vec<RamBody>, BattleLoopError> {
        self.engine
            .entities()
            .map(|entity| {
                let shape = *self
                    .vehicle_ram_shapes
                    .get(&entity.key)
                    .ok_or(BattleError::InvalidVehicle)?;
                let mass = *self
                    .vehicle_masses
                    .get(&entity.key)
                    .ok_or(BattleError::InvalidVehicle)?;
                let (vx, vy, vz, turret_yaw, gun_pitch, siege_state) = match entity.key.kind {
                    VehicleKind::Player => {
                        let (vx, vy, vz) = self
                            .engine
                            .player_ram_velocity(entity.key.id)
                            .unwrap_or((0.0, 0.0, 0.0));
                        (
                            vx,
                            vy,
                            vz,
                            angle_delta(entity.pose.yaw, entity.pose.aim_yaw),
                            entity.pose.gun_pitch,
                            self.engine.siege_status(entity.key.id).0,
                        )
                    }
                    VehicleKind::Bot => {
                        let bot_id = u32::try_from(entity.key.id)
                            .map_err(|_| BattleError::InvalidVehicle)?;
                        let velocity = authority
                            .bot_ram_velocity(bot_id)
                            .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
                        let state = authority
                            .bot_state(bot_id)
                            .ok_or(AuthorityRuntimeError::UnknownBot { bot_id })?;
                        (
                            velocity.x,
                            0.0,
                            velocity.z,
                            state.turret_yaw,
                            state.gun_pitch,
                            0,
                        )
                    }
                };
                Ok(RamBody {
                    key: entity.key,
                    team: entity.team.number(),
                    alive: entity.combat.alive,
                    x: entity.pose.x,
                    y: entity.pose.y,
                    z: entity.pose.z,
                    yaw: entity.pose.yaw,
                    pitch: entity.pose.pitch,
                    roll: entity.pose.roll,
                    mass,
                    vx,
                    vy,
                    vz,
                    turret_yaw,
                    gun_pitch,
                    siege_state,
                    shape,
                })
            })
            .collect()
    }

    fn human_traffic(&self) -> Result<Vec<TrafficBody>, BattleLoopError> {
        self.engine
            .entities()
            .filter(|entity| {
                entity.key.kind == VehicleKind::Player && entity.combat.alive && entity.world_pose
            })
            .map(|entity| {
                let network_id =
                    u32::try_from(entity.key.id).map_err(|_| BattleError::InvalidInput)?;
                let (half_length, half_width) = self
                    .vehicle_extents
                    .get(&entity.key)
                    .copied()
                    .ok_or(BattleError::InvalidInput)?;
                Ok(TrafficBody {
                    network_id,
                    kind: TargetKind::Human,
                    team: entity.team.number(),
                    position: BotVec3::new(entity.pose.x, entity.pose.y, entity.pose.z),
                    velocity: BotVec3::new(
                        entity.pose.yaw.sin() * entity.pose.speed,
                        0.0,
                        entity.pose.yaw.cos() * entity.pose.speed,
                    ),
                    yaw: entity.pose.yaw,
                    half_length,
                    half_width,
                })
            })
            .collect::<Result<Vec<_>, BattleError>>()
            .map_err(BattleLoopError::Battle)
    }

    fn contact_actor_states(&self) -> Vec<ContactActorState> {
        self.engine
            .entities()
            .map(|entity| ContactActorState {
                key: entity.key,
                team: entity.team.number(),
                alive: entity.combat.alive,
                world_pose: entity.world_pose,
                position: BotVec3::new(entity.pose.x, entity.pose.y, entity.pose.z),
                speed_metres_per_second: entity.pose.speed,
                health: entity.combat.health,
                max_health: entity.combat.max_health,
                firing_lane_range_metres: self.bot_firing_lane_ranges.get(&entity.key).copied(),
            })
            .collect()
    }
}

fn planner_bot_firing_lane_ranges(manifest: &Value) -> BTreeMap<VehicleKey, f64> {
    manifest
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|entry| {
            let raw = entry.as_object()?;
            let id = raw.get("id")?.as_u64()?;
            let class_tag = raw
                .get("profile")?
                .as_object()?
                .get("class_tag")?
                .as_str()?;
            let range = match class_tag {
                "SPG" => SPG_SHOT_LANE_RANGE_METRES,
                "lightTank" | "mediumTank" | "heavyTank" | "AT-SPG" => {
                    ORDINARY_SHOT_LANE_RANGE_METRES
                }
                _ => return None,
            };
            Some((
                VehicleKey {
                    kind: VehicleKind::Bot,
                    id,
                },
                range,
            ))
        })
        .collect()
}

fn ram_probe_pose(body: &RamBody) -> RamContactPose {
    RamContactPose {
        position: OracleVec3 {
            x: body.x as f32,
            y: body.y as f32,
            z: body.z as f32,
        },
        yaw: body.yaw,
        pitch: body.pitch,
        roll: body.roll,
        turret_yaw: body.turret_yaw,
        gun_pitch: body.gun_pitch,
        siege_state: body.siege_state,
    }
}

fn planner_bot_states(authority: &AuthorityRuntime) -> Value {
    Value::Array(
        authority
            .bot_ids()
            .filter_map(|bot_id| authority.bot_state(bot_id))
            .map(|state| {
                let ammo = state.ammo.snapshot();
                serde_json::json!({
                    "id": state.id,
                    "team": state.team,
                    "alive": state.alive,
                    "health": state.health,
                    "max_health": state.max_health,
                    "x": state.position.x,
                    "y": state.position.y,
                    "z": state.position.z,
                    "yaw": state.yaw,
                    "speed": state.speed,
                    "shell_index": ammo.loaded,
                    "world_pose": true,
                    "critical": {},
                    "ammo_remaining": ammo.remaining,
                })
            })
            .collect(),
    )
}

fn planner_player_states(engine: &BattleEngine) -> Value {
    Value::Array(
        engine
            .entities()
            .filter(|entity| entity.key.kind == VehicleKind::Player)
            .map(|entity| {
                serde_json::json!({
                    "id": entity.key.id,
                    "team": entity.team.number(),
                    "alive": entity.combat.alive,
                    "health": entity.combat.health,
                    "max_health": entity.combat.max_health,
                    "x": entity.pose.x,
                    "y": entity.pose.y,
                    "z": entity.pose.z,
                    "yaw": entity.pose.yaw,
                    "speed": entity.pose.speed,
                })
            })
            .collect(),
    )
}

fn planner_defense_context(rules: &StandardRules) -> Value {
    let mut bases = serde_json::Map::new();
    let mut states = serde_json::Map::new();
    let mut contributors = serde_json::Map::new();
    for team in [Team::One, Team::Two] {
        let team_number = team.number().to_string();
        bases.insert(
            team_number.clone(),
            Value::Array(
                rules
                    .bases(team)
                    .iter()
                    .enumerate()
                    .map(|(index, base)| {
                        serde_json::json!({
                            "id": format!("{}:{index}", team.number()),
                            "x": base.x,
                            "y": 0.0,
                            "z": base.z,
                        })
                    })
                    .collect(),
            ),
        );
        let state = rules.state(team);
        states.insert(
            team_number.clone(),
            serde_json::json!({
                "points": state.points,
                "time_left": state.time_left_seconds,
                "invaders": state.invaders,
                "stopped": state.stopped,
            }),
        );
        contributors.insert(
            team_number,
            Value::Array(
                rules
                    .contributors(team)
                    .map(|(vehicle, _)| match vehicle {
                        RulesVehicleKey::Human(id) => {
                            serde_json::json!({"kind": "human", "id": id})
                        }
                        RulesVehicleKey::Bot(id) => {
                            serde_json::json!({"kind": "bot", "id": id})
                        }
                    })
                    .collect(),
            ),
        );
    }
    serde_json::json!({
        "bases": bases,
        "states": states,
        "contributors": contributors,
    })
}

fn projectile_vec(value: BotVec3) -> ProjectileVec3 {
    ProjectileVec3 {
        x: value.x,
        y: value.y,
        z: value.z,
    }
}

fn shot_info(source: &SourceShot) -> Result<ShotInfo, BattleLoopError> {
    let kind = match source.shell.kind.as_str() {
        "ARMOR_PIERCING" => ShellKind::ArmorPiercing,
        "ARMOR_PIERCING_CR" => ShellKind::ArmorPiercingCr,
        "ARMOR_PIERCING_HE" => ShellKind::ArmorPiercingHe,
        "HOLLOW_CHARGE" => ShellKind::HollowCharge,
        "HIGH_EXPLOSIVE" => ShellKind::HighExplosive,
        _ => return Err(BattleError::InvalidProjectileEffects.into()),
    };
    let shot = ShotInfo {
        kind,
        caliber_mm: source.shell.caliber,
        damage: source.shell.damage,
        explosion_radius_m: source.shell.explosion_radius,
        piercing_power: source.piercing_power,
        max_distance_m: source.max_distance,
    };
    shot.validate()?;
    Ok(shot)
}

fn critical_shell_kind(kind: ShellKind) -> CriticalShellKind {
    match kind {
        ShellKind::ArmorPiercing => CriticalShellKind::ArmorPiercing,
        ShellKind::ArmorPiercingCr => CriticalShellKind::ArmorPiercingCr,
        ShellKind::ArmorPiercingHe => CriticalShellKind::ArmorPiercingHe,
        ShellKind::HollowCharge => CriticalShellKind::HollowCharge,
        ShellKind::HighExplosive => CriticalShellKind::HighExplosive,
    }
}

fn critical_target_lane(target: CriticalTarget) -> u64 {
    match target {
        CriticalTarget::Device(name) => {
            3 + match name {
                DeviceName::AmmoBayHealth => 0,
                DeviceName::EngineHealth => 1,
                DeviceName::FuelTankHealth => 2,
                DeviceName::GunHealth => 3,
                DeviceName::LeftTrackHealth => 4,
                DeviceName::RadioHealth => 5,
                DeviceName::RightTrackHealth => 6,
                DeviceName::SurveyingDeviceHealth => 7,
                DeviceName::TurretRotatorHealth => 8,
            }
        }
        CriticalTarget::Crew(name) => {
            12 + match name {
                CrewName::Commander => 0,
                CrewName::Driver => 1,
                CrewName::Gunner1 => 2,
                CrewName::Gunner2 => 3,
                CrewName::Loader1 => 4,
                CrewName::Loader2 => 5,
                CrewName::Radioman1 => 6,
                CrewName::Radioman2 => 7,
            }
        }
    }
}

fn armor_layer(source: &VehicleHitLayer) -> ArmorLayer {
    ArmorLayer {
        distance_m: source.distance_m,
        hit_angle_cos: source.hit_angle_cos,
        component: source.component.clone(),
        material: MaterialInfo {
            armor_mm: source.material.armor_mm,
            vehicle_damage_factor: source.material.vehicle_damage_factor,
            kind: source.material.kind,
            native_identity: source.material.native_identity,
            collide_once_only: source.material.collide_once_only,
            use_hit_angle: source.material.use_hit_angle,
            check_caliber_for_hit_angle_norm: source.material.check_caliber_for_hit_angle_norm,
            may_ricochet: source.material.may_ricochet,
            check_caliber_for_ricochet: source.material.check_caliber_for_ricochet,
        },
    }
}

fn bot_critical_state(state: &VehicleCriticalState) -> BotCriticalState {
    BotCriticalState {
        devices: state
            .devices
            .iter()
            .map(|(name, device)| {
                let condition = match device.condition {
                    VehicleDeviceCondition::Normal => BotDeviceCondition::Healthy,
                    VehicleDeviceCondition::Critical => BotDeviceCondition::Critical,
                    VehicleDeviceCondition::Destroyed => BotDeviceCondition::Destroyed,
                };
                (name.wire_name().to_owned(), condition)
            })
            .collect(),
        crew_ko: state
            .crew_ko
            .iter()
            .map(|name| name.wire_name().to_owned())
            .collect(),
        on_fire: state.on_fire,
        ammo_rack_death: state.ammo_rack_death,
    }
}

fn player_muzzle_failure_reason(reason: PlayerMuzzleFailureReason) -> &'static str {
    match reason {
        PlayerMuzzleFailureReason::Unavailable => "native_muzzle_unavailable",
        PlayerMuzzleFailureReason::TimedOut => "native_muzzle_timeout",
    }
}

fn player_muzzle_launch_geometry(
    position: OracleVec3,
    basis: [f32; 9],
) -> Option<(ProjectileVec3, ProjectileVec3)> {
    let origin = ProjectileVec3 {
        x: f64::from(position.x),
        y: f64::from(position.y),
        z: f64::from(position.z),
    };
    if ![origin.x, origin.y, origin.z]
        .into_iter()
        .all(f64::is_finite)
        || !(-5_000.0..=5_000.0).contains(&origin.x)
        || !(-1_000.0..=3_000.0).contains(&origin.y)
        || !(-5_000.0..=5_000.0).contains(&origin.z)
    {
        return None;
    }
    let mut direction = ProjectileVec3 {
        x: f64::from(basis[2]),
        y: f64::from(basis[5]),
        z: f64::from(basis[8]),
    };
    let length =
        (direction.x * direction.x + direction.y * direction.y + direction.z * direction.z).sqrt();
    if !length.is_finite() || length < 0.5 || length > 1.5 {
        return None;
    }
    direction.x /= length;
    direction.y /= length;
    direction.z /= length;
    Some((origin, direction))
}

fn clamp_player_turret_yaw(yaw: f64, limits: GunYawLimits) -> f64 {
    let wrapped = angle_delta(yaw, 0.0);
    if limits.is_limited() {
        wrapped.clamp(limits.minimum, limits.maximum)
    } else {
        wrapped
    }
}

fn deterministic_shot_factor(
    scope: SimulationScope,
    shooter: VehicleKey,
    shot_seq: u64,
    lane: u64,
) -> f64 {
    0.75 + deterministic_shot_unit(scope, shooter, shot_seq, lane) * 0.5
}

fn deterministic_target_shot_factor(
    scope: SimulationScope,
    shooter: VehicleKey,
    shot_seq: u64,
    target: VehicleKey,
    lane: u64,
) -> f64 {
    0.75 + deterministic_target_shot_unit(scope, shooter, shot_seq, target, lane) * 0.5
}

fn deterministic_target_shot_unit(
    scope: SimulationScope,
    shooter: VehicleKey,
    shot_seq: u64,
    target: VehicleKey,
    lane: u64,
) -> f64 {
    let target_kind = match target.kind {
        VehicleKind::Player => 0xa409_3822_299f_31d0,
        VehicleKind::Bot => 0x082e_fa98_ec4e_6c89,
    };
    deterministic_shot_unit(
        scope,
        shooter,
        shot_seq,
        lane ^ target.id.rotate_left(17) ^ target_kind,
    )
}

fn deterministic_shot_unit(
    scope: SimulationScope,
    shooter: VehicleKey,
    shot_seq: u64,
    lane: u64,
) -> f64 {
    let kind = match shooter.kind {
        VehicleKind::Player => 0x243f_6a88_85a3_08d3,
        VehicleKind::Bot => 0x1319_8a2e_0370_7344,
    };
    let mut value = scope.round_id
        ^ scope.epoch.rotate_left(11)
        ^ shooter.id.rotate_left(23)
        ^ shot_seq.rotate_left(37)
        ^ lane.rotate_left(47)
        ^ kind;
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^= value >> 31;
    ((value >> 11) as f64) * (1.0 / ((1_u64 << 53) as f64))
}

fn direct_spotting_observations(
    contacts: &[CanonicalContact],
) -> BTreeMap<VehicleKey, BTreeSet<VehicleKey>> {
    let mut observations = BTreeMap::new();
    for contact in contacts.iter().filter(|contact| contact.visible) {
        let target = VehicleKey {
            kind: match contact.target.kind {
                ContactTargetKind::Human => VehicleKind::Player,
                ContactTargetKind::Bot => VehicleKind::Bot,
            },
            id: u64::from(contact.target.id),
        };
        for observer_id in &contact.visible_by_bot_ids {
            observations
                .entry(VehicleKey {
                    kind: VehicleKind::Bot,
                    id: u64::from(*observer_id),
                })
                .or_insert_with(BTreeSet::new)
                .insert(target);
        }
        for observer_id in &contact.visible_by_player_ids {
            observations
                .entry(VehicleKey {
                    kind: VehicleKind::Player,
                    id: u64::from(*observer_id),
                })
                .or_insert_with(BTreeSet::new)
                .insert(target);
        }
    }
    observations
}

fn contact_rows_by_sequence(values: &[Value]) -> (BTreeMap<u64, Value>, BTreeSet<u64>) {
    let mut rows = BTreeMap::new();
    let mut conflicts = BTreeSet::new();
    for value in values {
        let Some(sequence) = exact_u64(value.get("seq"), 1, MAX_SEQUENCE) else {
            continue;
        };
        match rows.get(&sequence) {
            Some(previous) if previous != value => {
                conflicts.insert(sequence);
            }
            Some(_) => {}
            None => {
                rows.insert(sequence, value.clone());
            }
        }
    }
    (rows, conflicts)
}

fn contain_player_ram_receipts(
    ram: &mut RamAuthority,
    player_id: u64,
    values: &[Value],
) -> Result<(), RamError> {
    let (rows, conflicts) = contact_rows_by_sequence(values);
    for (sequence, value) in rows {
        let state = ram.player_ledger_state(player_id);
        let admitted = state.admitted_sequence;
        if sequence <= admitted {
            continue;
        }
        if sequence != admitted.saturating_add(1) {
            break;
        }
        if state.pending >= MAX_RAM_CONTACTS {
            break;
        }
        if conflicts.contains(&sequence) || PlayerRamReceipt::parse(&value).is_err() {
            ram.reject_player_receipt(player_id, sequence)?;
            continue;
        }
        match ram.admit_player_receipts(player_id, std::slice::from_ref(&value)) {
            Ok(_) => {}
            Err(RamError::PendingLimit) => break,
            Err(RamError::ReceiptTimelineRegression(_)) => {
                ram.reject_player_receipt(player_id, sequence)?;
            }
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn contain_player_pair_ram_receipts(
    ram: &mut RamAuthority,
    engine: &BattleEngine,
    reporter_player_id: u64,
    values: &[Value],
) -> Result<(), RamError> {
    let (rows, conflicts) = contact_rows_by_sequence(values);
    for (sequence, value) in rows {
        let state = ram.player_pair_ledger_state(reporter_player_id);
        let admitted = state.admitted_sequence;
        if sequence <= admitted {
            continue;
        }
        if sequence != admitted.saturating_add(1) {
            break;
        }
        if state.pending >= MAX_RAM_CONTACTS {
            break;
        }
        let receipt = PlayerPairRamReceipt::parse_for_reporter(reporter_player_id, &value);
        let valid_target = receipt.as_ref().is_ok_and(|receipt| {
            engine
                .body_pose(VehicleKey {
                    kind: VehicleKind::Player,
                    id: receipt.target_player_id,
                })
                .is_some()
        });
        if conflicts.contains(&sequence) || !valid_target {
            ram.reject_player_pair_receipt(reporter_player_id, sequence)?;
            continue;
        }
        match ram.admit_player_pair_receipts(reporter_player_id, std::slice::from_ref(&value)) {
            Ok(_) => {}
            Err(RamError::PendingLimit) => break,
            Err(RamError::ReceiptTimelineRegression(_)) => {
                ram.reject_player_pair_receipt(reporter_player_id, sequence)?;
            }
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn validate_inactive_input_side_effects(
    engine: &BattleEngine,
    reporter_player_id: u64,
    side_effects: &InputSideEffects,
) -> Result<(), String> {
    if !side_effects.ram_contacts_envelope_valid {
        return Err("ram_contacts is invalid".to_owned());
    }
    if !side_effects.player_ram_contacts_envelope_valid {
        return Err("player_ram_contacts is invalid".to_owned());
    }
    for value in &side_effects.ram_contacts {
        PlayerRamReceipt::parse(value).map_err(|error| error.to_string())?;
    }
    for value in &side_effects.player_ram_contacts {
        let receipt = PlayerPairRamReceipt::parse_for_reporter(reporter_player_id, value)
            .map_err(|error| error.to_string())?;
        if engine
            .body_pose(VehicleKey {
                kind: VehicleKind::Player,
                id: receipt.target_player_id,
            })
            .is_none()
        {
            return Err("player RAM target is not part of this round".to_owned());
        }
    }
    Ok(())
}

fn parse_player_input(
    engine: &BattleEngine,
    player_id: u64,
    message: &WireObject,
) -> Result<(PlayerInput, InputSideEffects), String> {
    const CLIENT_VERDICTS: &[&str] = &[
        "health",
        "alive",
        "frags",
        "team_killer",
        "death_reason",
        "killer_id",
        "killer_kind",
        "critical",
        "critical_revision",
        "ram_contact",
    ];
    if CLIENT_VERDICTS
        .iter()
        .any(|field| message.get(field).is_some())
    {
        return Err("visible clients cannot publish combat verdicts".to_owned());
    }
    required_finite(message, "forward", -1.0, 1.0)?;
    required_finite(message, "turn", -1.0, 1.0)?;
    let aim_yaw = required_finite(message, "aim_yaw", -1.0e6, 1.0e6)?;
    let gun_pitch = required_finite(message, "gun_pitch", -1.2, 1.2)?;
    exact_u64(message.get("input_seq"), 1, MAX_SEQUENCE)
        .ok_or_else(|| "input_seq is invalid".to_owned())?;
    exact_u64(message.get("fire_seq"), 0, MAX_SEQUENCE)
        .ok_or_else(|| "fire_seq is invalid".to_owned())?;
    let shell_index = match message.get("shell_index") {
        Some(value) => {
            exact_u64(Some(value), 0, 9).ok_or_else(|| "shell_index is invalid".to_owned())? as u8
        }
        None => 0,
    };

    let current = engine
        .body_pose(VehicleKey {
            kind: VehicleKind::Player,
            id: player_id,
        })
        .ok_or_else(|| "player vehicle is not part of this round".to_owned())?;
    let pose_fields = ["x", "y", "z", "yaw"];
    let pose_count = pose_fields
        .iter()
        .filter(|field| message.get(field).is_some())
        .count();
    let (source_time_us, pose) = if pose_count == 0 {
        if ["pose_time_us", "ram_vx", "ram_vy", "ram_vz"]
            .iter()
            .any(|field| message.get(field).is_some())
        {
            return Err("pose time and RAM velocity require a complete pose".to_owned());
        }
        (None, None)
    } else if pose_count == pose_fields.len() {
        let source_time_us = exact_u64(message.get("pose_time_us"), 0, u64::MAX)
            .ok_or_else(|| "pose_time_us is required for a pose".to_owned())?;
        let speed = optional_finite(message, "speed", current.speed, -200.0, 200.0)?;
        (
            Some(source_time_us),
            Some(PoseState {
                x: required_finite(message, "x", -2_000.0, 2_000.0)?,
                y: required_finite(message, "y", -1_000.0, 1_000.0)?,
                z: required_finite(message, "z", -2_000.0, 2_000.0)?,
                yaw: required_finite(message, "yaw", -1.0e6, 1.0e6)?,
                speed,
                ram_vx: required_finite(message, "ram_vx", -200.0, 200.0)?,
                ram_vy: required_finite(message, "ram_vy", -200.0, 200.0)?,
                ram_vz: required_finite(message, "ram_vz", -200.0, 200.0)?,
                alive: true,
            }),
        )
    } else {
        return Err("x, y, z, and yaw must be supplied together".to_owned());
    };
    if pose.is_none() && message.get("speed").is_some() {
        optional_finite(message, "speed", current.speed, -200.0, 200.0)?;
    }
    let pitch = optional_finite(message, "pitch", current.pitch, -0.61, 0.61)?;
    let roll = optional_finite(message, "roll", current.roll, -0.61, 0.61)?;
    let up_cosine = optional_finite(
        message,
        "up_cosine",
        engine.player_up_cosine(player_id).unwrap_or(1.0),
        -1.0,
        1.0,
    )?;
    let (ram_contacts, ram_contacts_envelope_valid) = match message.get("ram_contacts") {
        None => (Vec::new(), true),
        Some(Value::Array(values))
            if values.len() <= MAX_RAM_CONTACTS && values.iter().all(Value::is_object) =>
        {
            (values.clone(), true)
        }
        Some(_) => (Vec::new(), false),
    };
    let (player_ram_contacts, player_ram_contacts_envelope_valid) =
        match message.get("player_ram_contacts") {
            None => (Vec::new(), true),
            Some(Value::Array(values))
                if values.len() <= MAX_RAM_CONTACTS && values.iter().all(Value::is_object) =>
            {
                (values.clone(), true)
            }
            Some(_) => (Vec::new(), false),
        };
    let siege_enabled = match message.get("siege_enabled") {
        None => None,
        Some(Value::Bool(value)) => Some(*value),
        Some(_) => return Err("siege_enabled must be boolean".to_owned()),
    };
    Ok((
        PlayerInput {
            message: message.clone().into_value(),
            source_time_us,
            receipt_time_us: 0,
            pose,
            pitch,
            roll,
            up_cosine,
            aim_yaw,
            gun_pitch,
            shell_index,
        },
        InputSideEffects {
            player_id,
            ram_contacts,
            ram_contacts_envelope_valid,
            player_ram_contacts,
            player_ram_contacts_envelope_valid,
            siege_enabled,
        },
    ))
}

fn parse_ammo_intent(message: &WireObject) -> Result<PlayerAmmoIntent, String> {
    let allowed = [
        "type",
        "round_id",
        "authority_epoch",
        "intent_seq",
        "input_seq",
        "action",
        "shell_index",
    ];
    if message
        .fields()
        .keys()
        .any(|field| !allowed.contains(&field.as_str()))
    {
        return Err("ammo intent contains unsupported fields".to_owned());
    }
    let action = match message.get("action").and_then(Value::as_str) {
        Some("select_current") => PlayerAmmoIntentAction::SelectCurrent {
            shell_index: exact_u64(message.get("shell_index"), 0, 9)
                .ok_or_else(|| "shell_index is invalid".to_owned())? as u8,
        },
        Some("select_next") => PlayerAmmoIntentAction::SelectNext {
            shell_index: exact_u64(message.get("shell_index"), 0, 9)
                .ok_or_else(|| "shell_index is invalid".to_owned())? as u8,
        },
        Some("reload_partial_clip") if message.get("shell_index").is_none() => {
            PlayerAmmoIntentAction::ReloadPartialClip
        }
        Some("reload_partial_clip") => {
            return Err("reload_partial_clip cannot select a shell".to_owned());
        }
        _ => return Err("ammo intent action is invalid".to_owned()),
    };
    Ok(PlayerAmmoIntent {
        intent_seq: exact_u64(message.get("intent_seq"), 1, MAX_SEQUENCE)
            .ok_or_else(|| "intent_seq is invalid".to_owned())?,
        input_seq: exact_u64(message.get("input_seq"), 1, MAX_SEQUENCE)
            .ok_or_else(|| "input_seq is invalid".to_owned())?,
        action,
    })
}

fn parse_fire_intent(
    message: &WireObject,
    canonical_shell_index: u8,
) -> Result<FireIntentRequest, String> {
    Ok(FireIntentRequest {
        intent_seq: exact_u64(message.get("intent_seq"), 1, MAX_SEQUENCE)
            .ok_or_else(|| "intent_seq is invalid".to_owned())?,
        input_seq: exact_u64(message.get("input_seq"), 1, MAX_SEQUENCE)
            .ok_or_else(|| "input_seq is invalid".to_owned())?,
        shell_index: canonical_shell_index,
    })
}

fn exact_u64(value: Option<&Value>, minimum: u64, maximum: u64) -> Option<u64> {
    value
        .and_then(Value::as_u64)
        .filter(|value| (minimum..=maximum).contains(value))
}

fn required_finite(
    message: &WireObject,
    field: &str,
    minimum: f64,
    maximum: f64,
) -> Result<f64, String> {
    let value = message
        .get(field)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && (minimum..=maximum).contains(value))
        .ok_or_else(|| format!("{field} is invalid"))?;
    Ok(value)
}

fn optional_finite(
    message: &WireObject,
    field: &str,
    default: f64,
    minimum: f64,
    maximum: f64,
) -> Result<f64, String> {
    match message.get(field) {
        None => Ok(default),
        Some(_) => required_finite(message, field, minimum, maximum),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::battle::{BattleVehicleInit, PREBATTLE_TICKS};
    use crate::bot_sim::{
        BotProfile, BotSimulator, BotSpawn, ClipDescriptor, CriticalState, GunDescriptor,
        GunYawLimits, PhysicsProfile, ShellDescriptor, ShellProfile, VehicleClass,
        VehicleDescriptor,
    };
    use crate::client_replication::BattleClientEvent;
    use crate::combat::BodyPose;
    use crate::critical_damage::{
        propose_device_damage_over_time, CrewMemberProfile, CrewName, CrewRole, CriticalCause,
        CriticalLayer, CriticalProfile, CriticalShell, CriticalShellKind, CriticalTarget,
        DeviceName, DeviceProfile, StrikeInput, ALL_DEVICE_NAMES,
    };
    use crate::descriptor_exchange::{
        DestructibleInstance, DestructibleMapDonation, DestructibleResource,
        DestructibleResourceKind, DestructibleSignature, DestructibleWireId,
    };
    use crate::planner::BotPlanner;
    use crate::player_equipment::{BotEquipmentLedger, EquipmentContract, EquipmentKind};
    use crate::projectile::{LaunchContext, ProjectileLedger, ProjectileOutcome, SourceShell};
    use crate::protocol::{
        DestructibleHullCandidate, DestructibleHullEvidence,
        DestructibleKind as WireDestructibleKind, EntityRef, OracleLineage, OracleOperation,
        OracleV1BatchKey, OracleV1BatchReply, OracleV1Result, OracleV1ResultStatus,
        PlayerMuzzleEvidence, QueryOutcome, RamContactArmorEvidence, TransformSample,
        Vec3 as OracleVec3,
    };
    use crate::room::Team;
    use crate::rules::MapPoint;
    use crate::spotting::{CamouflageAspect, ObserverView, TargetCamouflage};
    use serde_json::json;

    fn scope() -> SimulationScope {
        SimulationScope {
            round_id: 4,
            epoch: 2,
        }
    }

    fn loop_under_test() -> BattleLoop {
        loop_with_health(90)
    }

    #[test]
    fn strategy_refresh_is_one_hertz_while_fixed_ticks_remain_thirty_hertz() {
        assert!(strategy_refresh_due(None, 1));
        assert!(!strategy_refresh_due(Some(1), 30));
        assert!(strategy_refresh_due(Some(1), 31));
        assert_eq!(STRATEGY_REFRESH_TICKS, 30);
    }

    #[test]
    fn canonical_rules_projection_drives_base_defense_orders() {
        let mut rules = StandardRules::new(
            vec![MapPoint::new(10.0, -20.0)],
            vec![MapPoint::new(300.0, 400.0)],
        );
        rules.update(
            30,
            true,
            &[crate::rules::VehicleForRules {
                key: RulesVehicleKey::Bot(16),
                team: Team::Two,
                alive: true,
                world_pose: true,
                x: 10.0,
                z: -20.0,
            }],
        );
        let defense = planner_defense_context(&rules);
        assert_eq!(defense["bases"]["1"][0]["id"], "1:0");
        assert_eq!(defense["states"]["1"]["invaders"], 1);
        assert_eq!(
            defense["contributors"]["1"],
            json!([{"kind": "bot", "id": 16}])
        );

        let route = json!({
            "id": "defense-lane",
            "waypoints": [
                {"x": 100.0, "y": 0.0, "z": -100.0, "hold": false},
                {"x": 100.0, "y": 0.0, "z": 100.0, "hold": false}
            ],
        });
        let manifest = json!([
            {
                "id": 1, "team": 1, "slot": 0,
                "health": 100, "max_health": 100,
                "profile": planner_profile(), "route": route,
            },
            {
                "id": 2, "team": 1, "slot": 1,
                "health": 100, "max_health": 100,
                "profile": planner_profile(), "route": route,
            }
        ]);
        let states = json!([
            {
                "id": 1, "team": 1, "alive": true,
                "health": 100, "max_health": 100,
                "x": 80.0, "y": 0.0, "z": -80.0, "yaw": 0.0,
                "speed": 0.0, "world_pose": true,
            },
            {
                "id": 2, "team": 1, "alive": true,
                "health": 100, "max_health": 100,
                "x": 120.0, "y": 0.0, "z": -80.0, "yaw": 0.0,
                "speed": 0.0, "world_pose": true,
            }
        ]);
        let orders =
            BotPlanner::new().build_orders(&manifest, &states, &json!([]), 1.0, Some(&defense));
        assert_eq!(
            orders["orders"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|order| order["combat_mode"] == "base_defense")
                .count(),
            1
        );
    }

    fn loop_with_health(health: u32) -> BattleLoop {
        let mut engine = BattleEngine::new(
            scope(),
            vec![BattleVehicleInit {
                key: VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                },
                team: Team::One,
                vehicle: "ussr:R11_MS-1".to_owned(),
                health,
                pose: BodyPose {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                    yaw: 0.0,
                    pitch: 0.0,
                    roll: 0.0,
                    speed: 0.0,
                    aim_yaw: 0.0,
                    gun_pitch: 0.0,
                },
                world_pose: false,
            }],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        engine
            .install_critical_profiles(BTreeMap::from([(
                VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                },
                CriticalProfile {
                    devices: ALL_DEVICE_NAMES
                        .into_iter()
                        .map(|name| {
                            (
                                name,
                                DeviceProfile {
                                    max_hp: 50.0,
                                    regen_hp: 25.0,
                                },
                            )
                        })
                        .collect(),
                    crew: vec![CrewMemberProfile {
                        name: CrewName::Commander,
                        roles: BTreeSet::from([CrewRole::Commander]),
                    }],
                    engine_fire_starting_chance: 0.0,
                    repair_speed_factor: 1.0,
                },
            )]))
            .unwrap();
        let mut battle = BattleLoop::new(engine);
        let player_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        battle
            .vehicle_ram_shapes
            .insert(player_key, RamShape::new(1.0, 2.0, -1.0, 1.0).unwrap());
        battle.vehicle_masses.insert(player_key, 20_000.0);
        battle
            .vehicle_ram_profiles
            .insert(player_key, RamDamageProfile::default());
        battle
            .install_player_ammo(
                BTreeMap::from([(
                    7,
                    PlayerAmmoLedger::new_exact_loaded(
                        7,
                        0,
                        &ram_descriptor(),
                        &ram_profile(),
                        vec![30],
                        0,
                    )
                    .unwrap(),
                )]),
                BTreeMap::from([(7, PhysicalBurstDescriptor::new(1, 0.0).unwrap())]),
            )
            .unwrap();
        battle
    }

    fn projectile_failure_fixture(
        track_in_authority: bool,
    ) -> (BattleLoop, AuthorityRuntime, ProjectileRecord) {
        let player = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let bot = VehicleKey {
            kind: VehicleKind::Bot,
            id: 1,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: player,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
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
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: bot,
                    team: Team::Two,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: BodyPose {
                        x: 0.0,
                        y: 0.0,
                        z: 100.0,
                        yaw: std::f64::consts::PI,
                        pitch: 0.0,
                        roll: 0.0,
                        speed: 0.0,
                        aim_yaw: std::f64::consts::PI,
                        gun_pitch: 0.0,
                    },
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        engine
            .install_critical_profiles(BTreeMap::from([
                (
                    player,
                    CriticalProfile {
                        devices: ALL_DEVICE_NAMES
                            .into_iter()
                            .map(|name| {
                                (
                                    name,
                                    DeviceProfile {
                                        max_hp: 50.0,
                                        regen_hp: 25.0,
                                    },
                                )
                            })
                            .collect(),
                        crew: vec![CrewMemberProfile {
                            name: CrewName::Commander,
                            roles: BTreeSet::from([CrewRole::Commander]),
                        }],
                        engine_fire_starting_chance: 0.0,
                        repair_speed_factor: 1.0,
                    },
                ),
                (
                    bot,
                    CriticalProfile {
                        devices: ALL_DEVICE_NAMES
                            .into_iter()
                            .map(|name| {
                                (
                                    name,
                                    DeviceProfile {
                                        max_hp: 50.0,
                                        regen_hp: 25.0,
                                    },
                                )
                            })
                            .collect(),
                        crew: vec![CrewMemberProfile {
                            name: CrewName::Commander,
                            roles: BTreeSet::from([CrewRole::Commander]),
                        }],
                        engine_fire_starting_chance: 0.0,
                        repair_speed_factor: 1.0,
                    },
                ),
            ]))
            .unwrap();
        let mut battle = BattleLoop::new(engine);
        battle
            .vehicle_ram_shapes
            .insert(player, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap());
        battle
            .vehicle_masses
            .insert(player, PhysicsProfile::default().mass);
        battle
            .install_player_ammo(
                BTreeMap::from([(
                    7,
                    PlayerAmmoLedger::new_exact_loaded(
                        7,
                        0,
                        &ram_descriptor(),
                        &ram_profile(),
                        vec![30],
                        0,
                    )
                    .unwrap(),
                )]),
                BTreeMap::from([(7, PhysicalBurstDescriptor::new(1, 0.0).unwrap())]),
            )
            .unwrap();
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(1, 0),
                },
                33_333,
                34,
            )
            .unwrap();
        for tick in 1..=PREBATTLE_TICKS {
            battle.advance_player_ammo(tick).unwrap();
            battle.engine_mut().advance_tick().unwrap();
        }
        battle
            .engine_mut()
            .submit_fire_intent(
                scope(),
                7,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                1_000,
            )
            .unwrap();
        let source_shot = SourceShot {
            speed: 100.0,
            gravity: 9.81,
            max_distance: 720.0,
            piercing_power: [100.0, 100.0],
            deadeye: false,
            shell: SourceShell {
                kind: "ARMOR_PIERCING".to_owned(),
                caliber: 37.0,
                damage: [20.0, 10.0],
                explosion_radius: 0.0,
                explosion_damage_factor: None,
                explosion_damage_absorption_factor: None,
                explosion_edge_damage_factor: None,
            },
        };
        let launch = ProjectileLaunch {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            shooter: player,
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
            gravity: source_shot.gravity,
            max_distance: source_shot.max_distance,
            max_time_ms: MAX_PROJECTILE_LIFETIME_MS,
            is_he: false,
            splash_radius: 0.0,
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot,
            fire_intent_seq: Some(1),
            fire_input_seq: Some(1),
        };
        let record = match battle
            .engine_mut()
            .commit_player_launch(scope(), 7, 1, launch, 1_001)
            .unwrap()
        {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        };
        battle
            .player_ammo
            .get_mut(&7)
            .unwrap()
            .admit_physical_launch(
                PlayerAmmoLaunch {
                    shot_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                PlayerAmmoBurst::ordinary(1),
            )
            .unwrap();

        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(
            lineage,
            PREBATTLE_TICKS,
            1,
            vec![ram_bot_at_tick(
                1,
                Team::Two.number(),
                0.0,
                std::f64::consts::PI,
                0.0,
                PREBATTLE_TICKS,
            )],
        )
        .unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::from([(
                    1,
                    EntityRef {
                        entity_id: 101,
                        generation: 1,
                    },
                )]),
                humans: BTreeMap::from([(
                    7,
                    EntityRef {
                        entity_id: 507,
                        generation: 1,
                    },
                )]),
            })
            .unwrap();
        if track_in_authority {
            authority
                .track_projectile(record.clone(), PREBATTLE_TICKS)
                .unwrap();
        }
        (battle, authority, record)
    }

    #[test]
    fn projectile_native_failures_expire_locally_and_preserve_reload() {
        let (mut battle, mut authority, record) = projectile_failure_fixture(true);
        let projectile_id = record.projectile_id.clone();
        let timeout = ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
            plan_id: crate::projectile_sim::ProjectilePlanId {
                issued_tick: PREBATTLE_TICKS,
                projectile_ordinal: 1,
            },
            issued_tick: PREBATTLE_TICKS,
            applied_tick: PREBATTLE_TICKS + ORACLE_PIPELINE_TICKS,
            cause: ProjectileTerminalCause::OracleTimeout,
            resolution: crate::projectile::ProjectileResolution {
                round_id: record.launch.round_id,
                authority_epoch: record.launch.authority_epoch,
                projectile_id: projectile_id.clone(),
                base_checked_ms: record.checked_through_ms,
                outcome: ProjectileOutcome::Expired,
                resolved_time_ms: record.checked_through_ms,
                checked_distance: record.checked_distance,
                piercing_loss: record.piercing_loss,
                penetration_factor: record.launch.penetration_factor,
                impact: None,
            },
            destructibles: Vec::new(),
        });

        let application = battle
            .apply_due_projectile_decisions(&mut authority, vec![timeout], 1_001)
            .unwrap();
        assert!(application.he_terminals.is_empty());
        assert!(application.ricochet_records.is_empty());
        assert!(battle.engine().projectile_record(&projectile_id).is_none());
        assert_eq!(
            battle
                .engine()
                .projectiles()
                .tombstone(&projectile_id)
                .unwrap()
                .outcome,
            ProjectileOutcome::Expired
        );
        assert_eq!(authority.active_projectiles(), 0);
        assert!(battle.engine().result().is_none());
        let ammo = battle.player_ammo_snapshot(7).unwrap();
        assert_eq!(ammo.remaining, vec![29]);
        assert!(ammo.reload_pending);
        let output = battle.engine_mut().advance_tick().unwrap();
        assert!(output.client_events.iter().any(|event| matches!(
            event,
            BattleClientEvent::ProjectileImpact(impact)
                if impact.resolution.projectile_id == projectile_id
                    && impact.resolution.outcome == ProjectileOutcome::Expired
        )));

        let (mut battle, mut authority, record) = projectile_failure_fixture(false);
        let projectile_id = record.projectile_id.clone();
        let untracked_terminal = ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
            plan_id: crate::projectile_sim::ProjectilePlanId {
                issued_tick: PREBATTLE_TICKS,
                projectile_ordinal: 1,
            },
            issued_tick: PREBATTLE_TICKS,
            applied_tick: PREBATTLE_TICKS + ORACLE_PIPELINE_TICKS,
            cause: ProjectileTerminalCause::Terrain {
                native_hit: crate::protocol::RayHit {
                    fraction: 1.0,
                    position: OracleVec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0,
                    },
                    normal: OracleVec3 {
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
                projectile_id: projectile_id.clone(),
                base_checked_ms: record.checked_through_ms,
                outcome: ProjectileOutcome::Impact,
                resolved_time_ms: record.checked_through_ms,
                checked_distance: record.checked_distance,
                piercing_loss: record.piercing_loss,
                penetration_factor: record.launch.penetration_factor,
                impact: Some(record.launch.origin),
            },
            destructibles: Vec::new(),
        });

        battle
            .apply_due_projectile_decisions(&mut authority, vec![untracked_terminal], 1_001)
            .unwrap();
        assert!(battle.engine().projectile_record(&projectile_id).is_none());
        assert_eq!(
            battle
                .engine()
                .projectiles()
                .tombstone(&projectile_id)
                .unwrap()
                .outcome,
            ProjectileOutcome::Expired
        );
        assert!(battle.engine().result().is_none());
        let ammo = battle.player_ammo_snapshot(7).unwrap();
        assert_eq!(ammo.remaining, vec![29]);
        assert!(ammo.reload_pending);
    }

    fn ram_descriptor() -> VehicleDescriptor {
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

    fn ram_profile() -> BotProfile {
        BotProfile {
            class: VehicleClass::LightTank,
            shells: vec![ShellProfile {
                index: 0,
                kind: "ARMOR_PIERCING".to_owned(),
                penetration: 100.0,
            }],
        }
    }

    fn ram_bot(id: u32, team: u8, x: f64, yaw: f64, speed: f64) -> BotSimulator {
        let mut bot = BotSimulator::new(
            ram_descriptor(),
            ram_profile(),
            BotSpawn {
                id,
                team,
                round_id: scope().round_id,
                tick: 0,
                position: BotVec3::new(x, 0.0, 0.0),
                yaw,
                pitch: 0.0,
                roll: 0.0,
                health: 100,
                fire_seq: 0,
                critical: CriticalState::default(),
            },
        )
        .unwrap();
        bot.state_mut().speed = speed;
        bot
    }

    fn ram_bot_at_tick(id: u32, team: u8, x: f64, yaw: f64, speed: f64, tick: u64) -> BotSimulator {
        let mut bot = ram_bot(id, team, x, yaw, speed);
        bot.state_mut().tick = tick;
        bot
    }

    fn planner_profile() -> Value {
        json!({
            "class_tag": "lightTank",
            "dominant_role": "support",
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

    fn ram_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        OracleV1BatchReply {
            protocol_version: request.protocol_version,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: 1,
            results: request
                .queries
                .iter()
                .map(|query| OracleV1Result {
                    query_id: query.query_id,
                    key: query.key.clone(),
                    query_generation: query.query_generation,
                    entity: query.entity,
                    status: OracleV1ResultStatus::Ok {
                        outcome: match &query.operation {
                            OracleOperation::RamContactArmorEvidence(..) => {
                                QueryOutcome::RamContactArmorEvidence(RamContactArmorEvidence {
                                    first_armor_mm: 1.0,
                                    second_armor_mm: 1.0,
                                })
                            }
                            _ => panic!("expected a RAM-only native batch"),
                        },
                    },
                })
                .collect(),
        }
    }

    fn underwater_muzzle_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        let query = request.queries.first().expect("one muzzle evidence query");
        assert_eq!(request.queries.len(), 1);
        assert!(matches!(
            query.operation,
            OracleOperation::PlayerMuzzleEvidence(..)
        ));
        OracleV1BatchReply {
            protocol_version: request.protocol_version,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: 1,
            results: vec![OracleV1Result {
                query_id: query.query_id,
                key: query.key.clone(),
                query_generation: query.query_generation,
                entity: query.entity,
                status: OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::PlayerMuzzleEvidence(PlayerMuzzleEvidence {
                        transform: TransformSample {
                            position: OracleVec3 {
                                x: 10.0,
                                y: 2.0,
                                z: 20.0,
                            },
                            basis: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                        },
                        barrel_under_water: true,
                    }),
                },
            }],
        }
    }

    fn unavailable_muzzle_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        let mut reply = underwater_muzzle_reply(request);
        reply.results[0].status = OracleV1ResultStatus::Unavailable {
            code: "muzzle_unavailable".to_owned(),
            message: "muzzle node is unavailable".to_owned(),
        };
        reply
    }

    fn degenerate_muzzle_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        let mut reply = underwater_muzzle_reply(request);
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::PlayerMuzzleEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.barrel_under_water = false;
        evidence.transform.basis = [0.0; 9];
        reply
    }

    fn unsafe_position_muzzle_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        let mut reply = underwater_muzzle_reply(request);
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::PlayerMuzzleEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.barrel_under_water = false;
        evidence.transform.position.x = 6_000.0;
        reply
    }

    fn native_ram_evidence(
        battle: &BattleLoop,
        probe: &RamContactProbe,
    ) -> NativeRamContactEvidence {
        let contact_armor = crate::ram::NativeContactArmor::new(0.0).unwrap();
        NativeRamContactEvidence::new(
            probe.pair,
            probe.cursor,
            probe.source_time_us,
            crate::ram::RamVehicleContactEvidence::new(
                contact_armor,
                *battle.vehicle_ram_profiles.get(&probe.pair.first).unwrap(),
            ),
            crate::ram::RamVehicleContactEvidence::new(
                contact_armor,
                *battle.vehicle_ram_profiles.get(&probe.pair.second).unwrap(),
            ),
            probe.first_moving,
            probe.second_moving,
        )
        .unwrap()
    }

    fn input(sequence: u64) -> WireObject {
        WireObject::try_from(json!({
            "type": "input",
            "round_id": 4,
            "input_seq": sequence,
            "forward": 1.0,
            "turn": 0.0,
            "aim_yaw": 0.25,
            "gun_pitch": -0.1,
            "fire_seq": 0,
            "x": 10.0,
            "y": 2.0,
            "z": 20.0,
            "yaw": 0.2,
            "pose_time_us": 33_333,
            "speed": 5.0,
            "ram_vx": 1.0,
            "ram_vy": 0.0,
            "ram_vz": 5.0,
            "up_cosine": -0.75,
            "shell_index": 0,
        }))
        .unwrap()
    }

    fn input_with_shell(sequence: u64, shell_index: u8) -> WireObject {
        let mut fields = input(sequence).into_fields();
        fields.insert(
            "pose_time_us".to_owned(),
            json!(sequence.saturating_mul(33_333)),
        );
        fields.insert("shell_index".to_owned(), json!(shell_index));
        WireObject::try_from(Value::Object(fields)).unwrap()
    }

    fn ammo_intent_message(
        intent_seq: u64,
        input_seq: u64,
        action: &str,
        shell_index: Option<u8>,
    ) -> WireObject {
        let mut value = json!({
            "type": "ammo_intent",
            "round_id": scope().round_id,
            "authority_epoch": scope().epoch,
            "intent_seq": intent_seq,
            "input_seq": input_seq,
            "action": action,
        });
        if let Some(shell_index) = shell_index {
            value
                .as_object_mut()
                .unwrap()
                .insert("shell_index".to_owned(), json!(shell_index));
        }
        WireObject::try_from(value).unwrap()
    }

    fn fire_intent_message(intent_seq: u64, input_seq: u64, legacy_shell: u8) -> WireObject {
        WireObject::try_from(json!({
            "type": "fire_intent",
            "round_id": scope().round_id,
            "authority_epoch": scope().epoch,
            "intent_seq": intent_seq,
            "input_seq": input_seq,
            "shell_index": legacy_shell,
            "shot_origin": [999.0, 999.0, 999.0],
            "shot_direction": [0.0, 1.0, 0.0],
            "dispersion_angle": 1.0,
        }))
        .unwrap()
    }

    fn active_two_shell_battle(intuition_chances: u8) -> BattleLoop {
        let mut battle = loop_with_health(90);
        let mut descriptor = ram_descriptor();
        descriptor.max_ammo = 60;
        let mut second_shell = descriptor.gun.shells[0].clone();
        second_shell.index = 1;
        second_shell.kind = "ARMOR_PIERCING_CR".to_owned();
        descriptor.gun.shells.push(second_shell);
        let mut profile = ram_profile();
        let mut second_profile = profile.shells[0].clone();
        second_profile.index = 1;
        second_profile.kind = "ARMOR_PIERCING_CR".to_owned();
        profile.shells.push(second_profile);
        battle
            .install_player_ammo(
                BTreeMap::from([(
                    7,
                    PlayerAmmoLedger::new_exact_loaded_with_intuition(
                        7,
                        0,
                        &descriptor,
                        &profile,
                        vec![30, 30],
                        0,
                        intuition_chances,
                    )
                    .unwrap(),
                )]),
                BTreeMap::from([(7, PhysicalBurstDescriptor::new(1, 0.0).unwrap())]),
            )
            .unwrap();
        for tick in 1..=PREBATTLE_TICKS {
            battle.advance_player_ammo(tick).unwrap();
            battle.engine_mut().advance_tick().unwrap();
        }
        battle
    }

    fn player_fire_input() -> PlayerFireAuthorityInput {
        PlayerFireAuthorityInput {
            law: PlayerGunDispersionLaw {
                base_dispersion_radians: 0.01,
                aiming_time_seconds: 2.0,
                movement_bloom_per_mps: 0.2,
                hull_rotation_bloom_per_rad_s: 0.4,
                turret_rotation_bloom_per_rad_s: 0.3,
                after_shot_bloom: 1.5,
                after_shot_in_burst_bloom: 0.75,
            },
            static_factors: EffectiveDispersionFactors::IDENTITY,
            turret_rotation_speed_rad_s: 0.7,
            crew_factor: 1.0,
            yaw_limits: GunYawLimits::default(),
        }
    }

    fn landing_input(sequence: u64) -> WireObject {
        WireObject::try_from(json!({
            "type": "input",
            "round_id": 4,
            "input_seq": sequence,
            "forward": 0.0,
            "turn": 0.0,
            "aim_yaw": 0.0,
            "gun_pitch": 0.0,
            "fire_seq": 0,
            "shell_index": 0,
        }))
        .unwrap()
    }

    fn landing_observation(observation_seq: u64, input_seq: u64, impact_speed: f64) -> WireObject {
        WireObject::try_from(json!({
            "type": "landing_observation",
            "round_id": scope().round_id,
            "authority_epoch": scope().epoch,
            "observation_seq": observation_seq,
            "input_seq": input_seq,
            "impact_speed": impact_speed,
        }))
        .unwrap()
    }

    fn equipment_contract(name: &str, kind: EquipmentKind, id: u64) -> EquipmentContract {
        EquipmentContract {
            name: name.to_owned(),
            kind,
            id,
            compact_descr: 10_000 + id,
            tags: match kind {
                EquipmentKind::Repairkit => vec!["repairkit".to_owned()],
                EquipmentKind::Medkit => vec!["medkit".to_owned()],
                _ => Vec::new(),
            },
            reuse_count: 0,
            cooldown_seconds: 0.0,
            autoactivate: false,
            fire_starting_chance_factor: 1.0,
            repair_all: false,
            bonus_value: 0.0,
            crew_level_increase: 0.0,
            engine_power_factor: 1.0,
            turret_rotation_speed_factor: 1.0,
            engine_hp_loss_per_second: 0.0,
            auto_reaction_seconds: 0.0,
        }
    }

    fn bot_equipment_contracts() -> Vec<EquipmentContract> {
        let mut extinguisher =
            equipment_contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        extinguisher.autoactivate = true;
        extinguisher.fire_starting_chance_factor = 0.9;
        let mut medkit = equipment_contract("largeMedkit", EquipmentKind::Medkit, 23);
        medkit.repair_all = true;
        let mut repairkit = equipment_contract("largeRepairkit", EquipmentKind::Repairkit, 25);
        repairkit.repair_all = true;
        vec![extinguisher, medkit, repairkit]
    }

    fn equipment_intent(
        intent_seq: u64,
        equipment_id: u64,
        activation_code: u64,
        requested_active: Option<bool>,
    ) -> WireObject {
        WireObject::try_from(json!({
            "type": "equipment_intent",
            "round_id": scope().round_id,
            "intent_seq": intent_seq,
            "equipment_id": equipment_id,
            "activation_code": activation_code,
            "selected": null,
            "requested_active": requested_active,
        }))
        .unwrap()
    }

    fn active_equipment_battle(contracts: Vec<EquipmentContract>) -> BattleLoop {
        let mut battle = loop_with_health(90);
        for _ in 0..PREBATTLE_TICKS {
            battle.engine_mut().advance_tick().unwrap();
        }
        battle
            .install_player_equipment(BTreeMap::from([(
                7,
                PlayerEquipmentLedger::new(7, 0.0, contracts, Vec::new()).unwrap(),
            )]))
            .unwrap();
        battle
    }

    fn equipment_command(message: WireObject) -> QueuedCommand {
        QueuedCommand {
            connection_id: 40,
            player_id: 7,
            scope: scope(),
            message,
        }
    }

    fn damage_player_engine(battle: &mut BattleLoop, amount: f64) {
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let profile = battle.engine().critical_profile(key).unwrap().clone();
        let state = battle.engine().critical_state(key).unwrap().clone();
        let mutation = propose_device_damage_over_time(
            &profile,
            &state,
            90,
            DeviceName::EngineHealth,
            amount,
            CriticalCause::Equipment,
        )
        .unwrap();
        battle
            .engine_mut()
            .apply_player_equipment_critical_batch(
                scope(),
                &BTreeMap::from([(key, vec![mutation])]),
            )
            .unwrap();
    }

    fn ignite_player(battle: &mut BattleLoop) {
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let fuel = CriticalTarget::Device(DeviceName::FuelTankHealth);
        let mutation = battle
            .engine()
            .propose_critical_strike(
                key,
                &crate::critical_damage::CriticalTrace {
                    native_layers: vec![CriticalLayer {
                        distance_m: 1.0,
                        armor_mm: 1.0,
                        vehicle_damage_factor: 1.0,
                        target: Some(fuel),
                        chance_to_hit_by_projectile: Some(1.0),
                        chance_to_hit_by_explosion: Some(1.0),
                    }],
                    internal_hits: Some(Vec::new()),
                },
                StrikeInput {
                    hull_damage: 0,
                    current_hull_health: 90,
                    shell: CriticalShell {
                        kind: CriticalShellKind::ArmorPiercing,
                        module_damage: Some(100.0),
                    },
                    penetrated: Some(true),
                    by_explosion: false,
                    dead_eye: false,
                    distance_filters: true,
                    now_ms: Some(15_000),
                },
                &CriticalSamples {
                    module_damage_factor: 1.0,
                    target_rolls: BTreeMap::from([(fuel, 0.0)]),
                    engine_fire_roll: None,
                },
            )
            .unwrap();
        battle
            .engine_mut()
            .apply_player_equipment_critical_batch(
                scope(),
                &BTreeMap::from([(key, vec![mutation])]),
            )
            .unwrap();
        assert!(battle.engine().critical_state(key).unwrap().on_fire);
    }

    fn active_landing_battle(health: u32) -> BattleLoop {
        let mut battle = loop_with_health(health);
        for _ in 0..PREBATTLE_TICKS {
            battle.engine_mut().advance_tick().unwrap();
        }
        battle
            .player_environment
            .insert(7, PlayerEnvironmentLedger::new(7, PREBATTLE_TICKS).unwrap());
        let effect = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: landing_input(1),
                },
                tick_offset(PREBATTLE_TICKS).as_micros() as u64,
                tick_offset(PREBATTLE_TICKS).as_millis() as u64,
            )
            .unwrap();
        assert!(matches!(effect, CommandEffect::PlayerInput(_)));
        battle
    }

    fn input_with_ram(sequence: u64, contact_sequence: u64) -> WireObject {
        WireObject::try_from(json!({
            "type": "input",
            "round_id": 4,
            "input_seq": sequence,
            "forward": 1.0,
            "turn": 0.0,
            "aim_yaw": 0.25,
            "gun_pitch": -0.1,
            "fire_seq": 0,
            "x": 10.0,
            "y": 2.0,
            "z": 20.0,
            "yaw": 0.2,
            "pose_time_us": sequence.saturating_mul(33_333),
            "speed": 5.0,
            "ram_vx": 1.0,
            "ram_vy": 0.0,
            "ram_vz": 5.0,
            "shell_index": 0,
            "ram_contacts": [{
                "seq": contact_sequence,
                "bot_id": 1,
                "bot_state_revision": 0,
                "presentation_time_us": 0,
                "contact_x": 10.0,
                "contact_y": 2.0,
                "contact_z": 20.0,
                "x": 10.0,
                "y": 2.0,
                "z": 20.0,
                "yaw": 0.2,
                "pitch": 0.0,
                "roll": 0.0,
                "vx": 0.0,
                "vy": 0.0,
                "vz": 5.0,
                "turret_yaw": 0.05,
                "gun_pitch": -0.1,
                "siege_state": 0,
            }],
        }))
        .unwrap()
    }

    fn hull_sample(
        vehicle: VehicleKey,
        apply_tick: u64,
        item_index: i64,
    ) -> crate::authority_runtime::NativeDestructibleHullSample {
        crate::authority_runtime::NativeDestructibleHullSample {
            vehicle,
            batch_key: OracleV1BatchKey {
                lineage: OracleLineage {
                    round_id: scope().round_id,
                    authority_epoch: scope().epoch,
                    oracle_generation: 1,
                },
                batch_seq: item_index as u64,
            },
            issued_tick: apply_tick - 3,
            apply_tick,
            position: BotVec3::new(0.0, 0.0, 0.0),
            yaw: 0.25,
            frame_travel: 0.5,
            kinetic_speed: 15.0,
            evidence: DestructibleHullEvidence {
                candidates: vec![DestructibleHullCandidate {
                    chunk_id: 5,
                    item_index,
                    mat_kind: None,
                    kind: WireDestructibleKind::Fragile,
                    obb_center: OracleVec3 {
                        x: item_index as f32,
                        y: 0.0,
                        z: 0.0,
                    },
                }],
                frame_travel: 0.5,
            },
        }
    }

    fn install_hull_catalog(battle: &mut BattleLoop, item_indices: &[u32]) {
        let resource_name = "objects/fragile.model".to_owned();
        let instances = item_indices
            .iter()
            .map(|&item_index| {
                let mut signature = [0_i64; 12];
                signature[0] = i64::from(item_index);
                DestructibleInstance {
                    signature: DestructibleSignature(signature),
                    wire: DestructibleWireId {
                        chunk_id: 5,
                        item_index,
                    },
                    scaled_health: Some(10.0),
                    modules: None,
                    resource_name: resource_name.clone(),
                }
            })
            .collect();
        battle
            .install_destructible_catalog(DestructibleMapDonation {
                round_id: scope().round_id,
                map_name: "01_karelia".to_owned(),
                unit_vehicle_mass: 10_000.0,
                resources: BTreeMap::from([(
                    resource_name,
                    DestructibleResource {
                        kind: DestructibleResourceKind::Fragile,
                        kinetic_correction: 1.0,
                    },
                )]),
                instances,
            })
            .unwrap();
        battle.vehicle_masses.insert(
            VehicleKey {
                kind: VehicleKind::Player,
                id: 7,
            },
            10_000.0,
        );
        battle.vehicle_masses.insert(
            VehicleKey {
                kind: VehicleKind::Bot,
                id: 1,
            },
            10_000.0,
        );
    }

    #[test]
    fn ingress_waits_for_the_tick_boundary_and_preserves_receive_order() {
        let mut battle = loop_under_test();
        battle
            .enqueue_player_message(9, 40, 7, scope(), input(1))
            .unwrap();
        assert!(battle
            .poll_elapsed(Duration::from_millis(20), 20)
            .unwrap()
            .ticks
            .is_empty());
        let output = battle
            .poll_elapsed(Duration::from_nanos(33_333_333), 34)
            .unwrap();
        assert_eq!(output.ticks.len(), 1);
        assert_eq!(output.effects.len(), 1);
        assert!(output.rejections.is_empty());
        assert_eq!(
            battle
                .engine()
                .body_pose(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                })
                .unwrap()
                .x,
            10.0
        );
    }

    #[test]
    fn ammo_intents_are_ordered_retry_stable_and_rust_owned() {
        let mut battle = active_two_shell_battle(16);
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(1, 1),
                },
                33_333,
                15_000,
            )
            .unwrap();
        assert_eq!(battle.player_ammo_snapshot(7).unwrap().loaded_shell, 0);
        assert_eq!(battle.engine().entities().next().unwrap().shell_index, 0);

        let queued_message = || ammo_intent_message(1, 1, "select_next", Some(1));
        let first = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: queued_message(),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            first,
            CommandEffect::AmmoIntent {
                outcome: PlayerAmmoIntentOutcome::Queued { shell_index: 1 },
                ..
            }
        ));
        let queued_snapshot = battle.player_ammo_snapshot(7).unwrap();
        assert_eq!(
            (queued_snapshot.loaded_shell, queued_snapshot.next_shell),
            (0, 1)
        );
        let queued_revision = queued_snapshot.revision;

        let retry = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: queued_message(),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            retry,
            CommandEffect::AmmoIntent {
                outcome: PlayerAmmoIntentOutcome::Queued { shell_index: 1 },
                ..
            }
        ));
        assert_eq!(
            battle.player_ammo_snapshot(7).unwrap().revision,
            queued_revision
        );

        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(2, 0),
                },
                66_666,
                15_034,
            )
            .unwrap();
        assert!(deterministic_intuition_success(4, 2, 7, 2, 16).unwrap());
        let intuition = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: ammo_intent_message(2, 2, "select_current", Some(1)),
                },
                15_033_333,
                15_034,
            )
            .unwrap();
        assert!(matches!(
            intuition,
            CommandEffect::AmmoIntent {
                outcome: PlayerAmmoIntentOutcome::IntuitionLoaded { shell_index: 1 },
                ..
            }
        ));
        assert_eq!(battle.player_ammo_snapshot(7).unwrap().loaded_shell, 1);
        assert_eq!(battle.engine().entities().next().unwrap().shell_index, 1);
    }

    #[test]
    fn fire_intent_uses_the_shell_frozen_by_rust_input_admission() {
        let mut battle = active_two_shell_battle(0);
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(1, 1),
                },
                33_333,
                15_000,
            )
            .unwrap();

        let effect = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: fire_intent_message(1, 1, 1),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        let CommandEffect::FireIntent {
            admission: FireIntentAdmission::New(binding),
            ..
        } = effect
        else {
            panic!("a canonical fire intent should be newly admitted")
        };
        assert_eq!(binding.shell_index, 0);

        let retry = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: fire_intent_message(1, 1, 9),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            retry,
            CommandEffect::FireIntent {
                admission: FireIntentAdmission::ExactRetry,
                ..
            }
        ));
    }

    #[test]
    fn ammo_change_during_pending_muzzle_queues_without_rebinding_the_shot() {
        let mut battle = active_two_shell_battle(16);
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(1, 1),
                },
                33_333,
                15_000,
            )
            .unwrap();
        let fire = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: fire_intent_message(1, 1, 1),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            fire,
            CommandEffect::FireIntent {
                admission: FireIntentAdmission::New(_),
                ..
            }
        ));

        let change = ammo_intent_message(1, 1, "select_current", Some(1));
        let queued = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: change.clone(),
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            queued,
            CommandEffect::AmmoIntent {
                outcome: PlayerAmmoIntentOutcome::Queued { shell_index: 1 },
                ..
            }
        ));
        let snapshot = battle.player_ammo_snapshot(7).unwrap();
        assert_eq!((snapshot.loaded_shell, snapshot.next_shell), (0, 1));
        assert_eq!(
            battle
                .player_ammo
                .get_mut(&7)
                .unwrap()
                .admit_launch(PlayerAmmoLaunch {
                    shot_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                }),
            Ok(PlayerAmmoLaunchAdmission::New)
        );

        let retry = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: change,
                },
                15_000_000,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            retry,
            CommandEffect::AmmoIntent {
                outcome: PlayerAmmoIntentOutcome::Queued { shell_index: 1 },
                ..
            }
        ));
    }

    fn assert_player_muzzle_failure_preserves_ammunition(
        reply: fn(&OracleV1BatchRequest) -> OracleV1BatchReply,
    ) {
        let mut battle = loop_under_test();
        battle
            .install_player_fire(BTreeMap::from([(7, player_fire_input())]))
            .unwrap();
        let player_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(lineage, 0, 1, Vec::new()).unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::new(),
                humans: BTreeMap::from([(
                    7,
                    EntityRef {
                        entity_id: 507,
                        generation: 1,
                    },
                )]),
            })
            .unwrap();
        battle
            .install_authority(
                authority,
                BTreeMap::new(),
                BTreeMap::new(),
                BTreeMap::from([(player_key, (2.0, 1.0))]),
                battle.vehicle_ram_shapes.clone(),
                battle.vehicle_masses.clone(),
                battle.vehicle_ram_profiles.clone(),
                json!([]),
            )
            .unwrap();
        for tick in 1..=PREBATTLE_TICKS {
            battle
                .poll_elapsed(tick_offset(tick), tick_offset(tick).as_millis() as u64)
                .unwrap();
        }

        let ammo_before = battle.player_ammo_snapshot(7).unwrap();
        let issued_tick = PREBATTLE_TICKS + 1;
        let mut first_input = input_with_shell(1, 0).into_fields();
        first_input.insert(
            "pose_time_us".to_owned(),
            json!(tick_offset(PREBATTLE_TICKS).as_micros() as u64),
        );
        let mut second_input = input_with_shell(2, 0).into_fields();
        second_input.insert(
            "pose_time_us".to_owned(),
            json!(tick_offset(issued_tick).as_micros() as u64),
        );
        battle
            .enqueue_player_message(
                1,
                40,
                7,
                scope(),
                WireObject::try_from(Value::Object(first_input)).unwrap(),
            )
            .unwrap();
        battle
            .enqueue_player_message(
                2,
                40,
                7,
                scope(),
                WireObject::try_from(Value::Object(second_input)).unwrap(),
            )
            .unwrap();
        battle
            .enqueue_player_message(3, 40, 7, scope(), fire_intent_message(1, 2, 0))
            .unwrap();
        let issued = battle
            .poll_elapsed(
                tick_offset(issued_tick),
                tick_offset(issued_tick).as_millis() as u64,
            )
            .unwrap();
        assert!(
            issued.rejections.is_empty(),
            "unexpected command rejections: {:?}",
            issued.rejections
        );
        let muzzle_request = issued
            .oracle_requests
            .iter()
            .find(|request| {
                request.queries.iter().any(|query| {
                    matches!(query.operation, OracleOperation::PlayerMuzzleEvidence(..))
                })
            })
            .expect("the accepted fire intent schedules one native muzzle query")
            .clone();
        battle.accept_oracle_reply(reply(&muzzle_request)).unwrap();
        for tick in (issued_tick + 1)..=(issued_tick + ORACLE_PIPELINE_TICKS) {
            battle
                .poll_elapsed(tick_offset(tick), tick_offset(tick).as_millis() as u64)
                .unwrap();
        }

        let ammo_after = battle.player_ammo_snapshot(7).unwrap();
        assert_eq!(ammo_after.remaining, ammo_before.remaining);
        assert_eq!(ammo_after.last_shot_seq, ammo_before.last_shot_seq);
        assert_eq!(ammo_after.loaded_shell, ammo_before.loaded_shell);
        assert!(!ammo_after.reload_pending);
        assert_eq!(
            battle
                .engine()
                .entities()
                .find(|entity| entity.key == player_key)
                .unwrap()
                .last_fire_seq,
            0
        );
        assert!(!battle.engine().player_fire_intent_pending(7).unwrap());
        assert!(battle.engine().result().is_none());
    }

    #[test]
    fn underwater_player_muzzle_rejects_without_consuming_ammunition() {
        assert_player_muzzle_failure_preserves_ammunition(underwater_muzzle_reply);
    }

    #[test]
    fn unavailable_player_muzzle_rejects_without_consuming_ammunition() {
        assert_player_muzzle_failure_preserves_ammunition(unavailable_muzzle_reply);
    }

    #[test]
    fn invalid_player_muzzle_geometry_rejects_without_consuming_ammunition() {
        assert_player_muzzle_failure_preserves_ammunition(degenerate_muzzle_reply);
        assert_player_muzzle_failure_preserves_ammunition(unsafe_position_muzzle_reply);
    }

    #[test]
    fn player_fire_clock_uses_source_timed_motion_and_exact_shot_receipts() {
        let mut battle = loop_under_test();
        battle
            .install_player_fire(BTreeMap::from([(7, player_fire_input())]))
            .unwrap();
        let base = battle.player_fire_snapshot(7).unwrap().dispersion_radians;
        assert!(!battle.player_fire_motion_ready(7));

        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input_with_shell(1, 0),
                },
                33_333,
                34,
            )
            .unwrap();
        battle.advance_player_fire_clocks(1).unwrap();
        assert!(!battle.player_fire_motion_ready(7));

        let mut second_fields = input_with_shell(2, 0).into_fields();
        second_fields.insert("yaw".to_owned(), json!(0.25));
        second_fields.insert("aim_yaw".to_owned(), json!(0.8));
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: WireObject::try_from(Value::Object(second_fields)).unwrap(),
                },
                66_666,
                67,
            )
            .unwrap();
        battle.advance_player_fire_clocks(2).unwrap();
        assert!(battle.player_fire_motion_ready(7));
        assert!(battle.player_fire_snapshot(7).unwrap().dispersion_radians > base);

        let before_shot = battle.player_fire_snapshot(7).unwrap();
        let aim_direction = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 1.0,
        };
        let prepared = battle
            .prepare_player_fire_shot(7, 1, 1, 0, aim_direction, true)
            .unwrap()
            .unwrap();
        assert!(prepared.next_clock.is_some());
        battle
            .commit_prepared_player_fire_shot(7, 1, Some(prepared))
            .unwrap();
        assert!(
            battle.player_fire_snapshot(7).unwrap().dispersion_radians
                > before_shot.dispersion_radians
        );

        let retry = battle
            .prepare_player_fire_shot(7, 1, 1, 0, aim_direction, true)
            .unwrap()
            .unwrap();
        assert_eq!(retry.direction, prepared.direction);
        assert!(retry.next_clock.is_none());
        assert!(matches!(
            battle.prepare_player_fire_shot(7, 1, 1, 1, aim_direction, true),
            Err(BattleLoopError::PlayerFireTransactionMismatch)
        ));
    }

    #[test]
    fn spotting_inputs_install_the_exact_authority_observer_set() {
        let mut battle = loop_under_test();
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(lineage, 0, 1, Vec::new()).unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::new(),
                humans: BTreeMap::from([(
                    7,
                    EntityRef {
                        entity_id: 507,
                        generation: 1,
                    },
                )]),
            })
            .unwrap();
        battle.authority = Some(authority);
        let player = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let exact = BTreeSet::from([player]);
        battle
            .install_spotting_inputs(BTreeMap::from([(
                player,
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
                },
            )]))
            .unwrap();

        assert_eq!(battle.contact_authority().spotting_vehicles(), exact);
        assert!(!battle
            .authority_mut()
            .unwrap()
            .install_spotting_observers(exact)
            .unwrap());
    }

    #[test]
    fn bot_equipment_poll_commits_on_later_tick_and_snapshot_matches_that_tick() {
        let player_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let bot_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 16,
        };
        let pose = BodyPose {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            speed: 0.0,
            aim_yaw: 0.0,
            gun_pitch: 0.0,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: player_key,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 90,
                    pose,
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: bot_key,
                    team: Team::Two,
                    vehicle: "germany:G12_Ltraktor".to_owned(),
                    health: 100,
                    pose,
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        let profile = CriticalProfile {
            devices: ALL_DEVICE_NAMES
                .into_iter()
                .map(|name| {
                    (
                        name,
                        DeviceProfile {
                            max_hp: 50.0,
                            regen_hp: 25.0,
                        },
                    )
                })
                .collect(),
            crew: vec![CrewMemberProfile {
                name: CrewName::Commander,
                roles: BTreeSet::from([CrewRole::Commander]),
            }],
            engine_fire_starting_chance: 0.0,
            repair_speed_factor: 1.0,
        };
        engine
            .install_critical_profiles(BTreeMap::from([
                (player_key, profile.clone()),
                (bot_key, profile.clone()),
            ]))
            .unwrap();
        for _ in 0..PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
        let damaged = propose_device_damage_over_time(
            &profile,
            engine.critical_state(bot_key).unwrap(),
            100,
            DeviceName::EngineHealth,
            100.0,
            CriticalCause::Shot,
        )
        .unwrap();
        engine
            .apply_bot_equipment_batch(scope(), bot_key, vec![damaged], None)
            .unwrap();
        let mut battle = BattleLoop::new(engine);
        battle
            .install_bot_equipment(BTreeMap::from([(
                16,
                BotEquipmentLedger::new(16, 0.0, bot_equipment_contracts()).unwrap(),
            )]))
            .unwrap();

        let observed_tick = PREBATTLE_TICKS + 1;
        battle
            .advance_bot_equipment(observed_tick, tick_offset(observed_tick).as_millis() as u64)
            .unwrap();
        let observed = battle.bot_equipment_snapshot(16).unwrap();
        assert_eq!(observed[2].uses_left, 1);
        assert_eq!(observed[2].ai_pending_elapsed, Some(0.0));
        assert_eq!(
            battle
                .engine()
                .critical_state(bot_key)
                .unwrap()
                .device_state(&profile, DeviceName::EngineHealth)
                .condition,
            VehicleDeviceCondition::Destroyed,
        );

        let activation_tick = observed_tick + 1;
        battle
            .advance_bot_equipment(
                activation_tick,
                tick_offset(activation_tick).as_millis() as u64,
            )
            .unwrap();
        let activated = battle.bot_equipment_snapshot(16).unwrap();
        assert_eq!(activated[2].uses_left, 0);
        assert_eq!(activated[2].ai_pending_elapsed, None);
        assert_eq!(
            battle
                .engine()
                .critical_state(bot_key)
                .unwrap()
                .device_state(&profile, DeviceName::EngineHealth)
                .condition,
            VehicleDeviceCondition::Normal,
        );
    }

    #[test]
    fn equipment_intent_repair_retry_rejection_and_snapshot_are_transactional() {
        let mut repair = equipment_contract("largeRepairkit", EquipmentKind::Repairkit, 41);
        repair.repair_all = true;
        repair.bonus_value = 0.1;
        let mut battle = active_equipment_battle(vec![repair]);
        damage_player_engine(&mut battle, 40.0);
        let now_us = tick_offset(PREBATTLE_TICKS).as_micros() as u64;
        let request = || equipment_intent(1, 41, (1_u64 << 16) | 41, None);

        let accepted = battle
            .apply_command(equipment_command(request()), now_us, 15_000)
            .unwrap();
        let CommandEffect::EquipmentIntent { result, .. } = accepted else {
            panic!("equipment intent returned the wrong command effect");
        };
        assert!(result.accepted);
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        assert_eq!(
            battle
                .engine()
                .critical_state(key)
                .unwrap()
                .device_state(
                    battle.engine().critical_profile(key).unwrap(),
                    DeviceName::EngineHealth,
                )
                .condition,
            VehicleDeviceCondition::Normal,
        );
        let snapshot = battle.player_equipment_snapshot(7).unwrap();
        assert_eq!(snapshot.equipment_revision, 2);
        assert_eq!(snapshot.equipment_intent_seq, 1);
        assert_eq!(snapshot.equipment_states[0].uses_left, 0);

        let retry = battle
            .apply_command(equipment_command(request()), now_us, 15_000)
            .unwrap();
        let CommandEffect::EquipmentIntent { result, .. } = retry else {
            panic!("equipment retry returned the wrong command effect");
        };
        assert!(result.accepted);
        assert_eq!(battle.player_equipment_snapshot(7).unwrap(), snapshot);

        let rejected = battle
            .apply_command(
                equipment_command(equipment_intent(2, 99, 99, None)),
                now_us,
                15_000,
            )
            .unwrap();
        let CommandEffect::EquipmentIntent { result, .. } = rejected else {
            panic!("equipment rejection returned the wrong command effect");
        };
        assert!(!result.accepted);
        assert_eq!(result.reason, "equipment_not_mounted");
        let snapshot = battle.player_equipment_snapshot(7).unwrap();
        assert_eq!(snapshot.equipment_revision, 2);
        assert_eq!(snapshot.equipment_intent_seq, 2);
        assert_eq!(
            snapshot.equipment_intent_result.reason,
            "equipment_not_mounted"
        );
    }

    #[test]
    fn automatic_extinguisher_and_active_rpm_drain_commit_in_one_tick() {
        let mut automatic =
            equipment_contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        automatic.autoactivate = true;
        let mut limiter = equipment_contract("removedRpmLimiter", EquipmentKind::RpmLimiter, 12);
        limiter.reuse_count = -1;
        limiter.engine_power_factor = 1.1;
        limiter.engine_hp_loss_per_second = 1.5;
        let mut battle = active_equipment_battle(vec![automatic, limiter]);
        let now_us = tick_offset(PREBATTLE_TICKS).as_micros() as u64;
        let enabled = battle
            .apply_command(
                equipment_command(equipment_intent(1, 12, (1_u64 << 16) | 12, Some(true))),
                now_us,
                15_000,
            )
            .unwrap();
        assert!(matches!(
            enabled,
            CommandEffect::EquipmentIntent { result, .. } if result.accepted
        ));
        ignite_player(&mut battle);
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let profile = battle.engine().critical_profile(key).unwrap().clone();
        let engine_before = battle
            .engine()
            .critical_state(key)
            .unwrap()
            .device_state(&profile, DeviceName::EngineHealth)
            .hp;

        battle
            .advance_player_equipment(PREBATTLE_TICKS + 1)
            .unwrap();

        let critical = battle.engine().critical_state(key).unwrap();
        assert!(!critical.on_fire);
        assert!(critical.device_state(&profile, DeviceName::EngineHealth).hp < engine_before);
        let snapshot = battle.player_equipment_snapshot(7).unwrap();
        assert_eq!(snapshot.equipment_revision, 3);
        assert_eq!(snapshot.equipment_states[0].uses_left, 0);
        assert!(snapshot.equipment_states[1].active);
        let passives = battle.player_equipment_passive_effects(7).unwrap();
        assert_eq!(passives.engine_power_factor, 1.1);
        assert_eq!(passives.engine_hp_loss_per_second, 1.5);
    }

    #[test]
    fn manual_extinguisher_commits_fire_state_and_charge_together() {
        let extinguisher =
            equipment_contract("manualExtinguisher", EquipmentKind::Extinguisher, 21);
        let mut battle = active_equipment_battle(vec![extinguisher]);
        ignite_player(&mut battle);
        let now_us = tick_offset(PREBATTLE_TICKS).as_micros() as u64;

        let effect = battle
            .apply_command(
                equipment_command(equipment_intent(1, 21, 21, None)),
                now_us,
                15_000,
            )
            .unwrap();

        assert!(matches!(
            effect,
            CommandEffect::EquipmentIntent { result, .. } if result.accepted
        ));
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        assert!(!battle.engine().critical_state(key).unwrap().on_fire);
        let snapshot = battle.player_equipment_snapshot(7).unwrap();
        assert_eq!(snapshot.equipment_revision, 2);
        assert_eq!(snapshot.equipment_states[0].uses_left, 0);
    }

    #[test]
    fn landing_observation_commits_environment_damage_once_and_replays_result() {
        let mut battle = active_landing_battle(90);
        let health_before = battle
            .engine()
            .combat()
            .get(VehicleKey {
                kind: VehicleKind::Player,
                id: 7,
            })
            .unwrap()
            .health;
        let command = || QueuedCommand {
            connection_id: 40,
            player_id: 7,
            scope: scope(),
            message: landing_observation(1, 1, 20.0),
        };

        let accepted = battle.apply_command(command(), 0, 0).unwrap();
        let CommandEffect::LandingObservation { result, .. } = accepted else {
            panic!("landing observation returned the wrong command effect");
        };
        assert!(result.accepted);
        assert_eq!(result.committed_seq, 1);
        assert_eq!(battle.landing_observation_seq(7), Some(1));
        assert_eq!(
            battle
                .engine()
                .combat()
                .get(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                })
                .unwrap()
                .health,
            health_before - 27,
        );

        let health_after = battle
            .engine()
            .combat()
            .get(VehicleKey {
                kind: VehicleKind::Player,
                id: 7,
            })
            .unwrap()
            .health;
        let retry = battle.apply_command(command(), 0, 0).unwrap();
        let CommandEffect::LandingObservation { result, .. } = retry else {
            panic!("landing retry returned the wrong command effect");
        };
        assert!(result.accepted);
        assert_eq!(result.committed_seq, 1);
        assert_eq!(
            battle
                .engine()
                .combat()
                .get(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                })
                .unwrap()
                .health,
            health_after,
        );
        let tick = battle.engine_mut().advance_tick().unwrap();
        assert!(tick.client_events.iter().any(|event| matches!(
            event,
            crate::client_replication::BattleClientEvent::Combat(combat)
                if combat.commit.source == DamageSource::Environment
                    && combat.client_simulation_reason == Some(3)
        )));
    }

    #[test]
    fn landing_damage_failure_does_not_commit_the_staged_sequence() {
        let mut battle = active_landing_battle(6_000);
        let rejection = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: landing_observation(1, 1, 200.0),
                },
                0,
                0,
            )
            .unwrap_err();
        assert!(rejection.message.contains("per-effect limit"));
        assert_eq!(battle.landing_observation_seq(7), Some(0));
        assert_eq!(
            battle
                .engine()
                .combat()
                .get(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                })
                .unwrap()
                .health,
            6_000,
        );
    }

    #[test]
    fn client_verdict_and_partial_pose_are_nonfatal_rejections() {
        let mut battle = loop_under_test();
        let bad_verdict = WireObject::try_from(json!({
            "type":"input", "input_seq":1, "forward":0, "turn":0,
            "aim_yaw":0, "gun_pitch":0, "fire_seq":0, "health":0,
        }))
        .unwrap();
        let partial_pose = WireObject::try_from(json!({
            "type":"input", "input_seq":1, "forward":0, "turn":0,
            "aim_yaw":0, "gun_pitch":0, "fire_seq":0, "x":1,
        }))
        .unwrap();
        battle
            .enqueue_player_message(1, 40, 7, scope(), bad_verdict)
            .unwrap();
        battle
            .enqueue_player_message(2, 40, 7, scope(), partial_pose)
            .unwrap();
        let output = battle
            .poll_elapsed(Duration::from_nanos(33_333_333), 34)
            .unwrap();
        assert_eq!(output.rejections.len(), 2);
        assert_eq!(output.ticks.len(), 1);
    }

    #[test]
    fn player_input_preserves_native_surface_up_cosine() {
        let mut battle = loop_under_test();
        let effect = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input(1),
                },
                33_333,
                34,
            )
            .unwrap();
        assert!(matches!(effect, CommandEffect::PlayerInput(_)));
        assert_eq!(battle.engine().player_up_cosine(7), Some(-0.75));
    }

    #[test]
    fn destructible_hull_tick_rejects_atomically_before_any_commit() {
        let mut battle = loop_under_test();
        install_hull_catalog(&mut battle, &[1, 2]);
        let first = hull_sample(
            VehicleKey {
                kind: VehicleKind::Player,
                id: 7,
            },
            9,
            1,
        );
        let invalid_late = hull_sample(
            VehicleKey {
                kind: VehicleKind::Bot,
                id: 1,
            },
            10,
            2,
        );
        assert!(matches!(
            battle.apply_destructible_hulls(9, vec![first.clone(), invalid_late]),
            Err(BattleLoopError::InvalidDestructibleHullEvidence)
        ));
        assert_eq!(battle.engine().destructibles().entries().count(), 0);

        let second = hull_sample(
            VehicleKey {
                kind: VehicleKind::Bot,
                id: 1,
            },
            9,
            2,
        );
        battle
            .apply_destructible_hulls(9, vec![first, second])
            .unwrap();
        assert_eq!(battle.engine().destructibles().entries().count(), 2);
    }

    #[test]
    fn ram_receipt_gap_does_not_reject_the_ordered_player_input() {
        let mut battle = loop_under_test();
        battle
            .enqueue_player_message(1, 40, 7, scope(), input_with_ram(1, 2))
            .unwrap();
        let admitted_without_contact = battle
            .poll_elapsed(Duration::from_nanos(33_333_333), 34)
            .unwrap();
        assert!(admitted_without_contact.rejections.is_empty());
        assert_eq!(battle.engine().player_input_seq(7), Some(1));
        assert_eq!(
            battle.ram_player_projection(7),
            PlayerRamProjection::default()
        );
        assert_eq!(
            battle
                .engine()
                .body_pose(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 7,
                })
                .unwrap()
                .x,
            10.0
        );

        battle
            .enqueue_player_message(2, 40, 7, scope(), input_with_ram(2, 1))
            .unwrap();
        let admitted = battle
            .poll_elapsed(Duration::from_nanos(66_666_666), 67)
            .unwrap();
        assert!(admitted.rejections.is_empty());
        assert_eq!(battle.engine().player_input_seq(7), Some(2));
        let projection = battle.ram_player_projection(7);
        assert_eq!(projection.admitted_sequence, 1);
        assert_eq!(projection.resolved_sequence, 0);
        assert_eq!(projection.contacts.len(), 1);
    }

    #[test]
    fn invalid_ram_contact_is_terminal_without_poisoning_later_input() {
        let mut battle = loop_under_test();
        let invalid_contact = |input_sequence: u64| {
            let mut fields = input_with_ram(input_sequence, 1).into_fields();
            fields
                .get_mut("ram_contacts")
                .and_then(Value::as_array_mut)
                .unwrap()[0]["contact_x"] = json!(true);
            WireObject::try_from(Value::Object(fields)).unwrap()
        };
        let apply = |battle: &mut BattleLoop, message: WireObject| {
            battle
                .apply_command(
                    QueuedCommand {
                        connection_id: 40,
                        player_id: 7,
                        scope: scope(),
                        message,
                    },
                    66_666,
                    67,
                )
                .unwrap()
        };

        assert!(matches!(
            apply(&mut battle, invalid_contact(1)),
            CommandEffect::PlayerInput(_)
        ));
        assert_eq!(battle.engine().player_input_seq(7), Some(1));
        assert_eq!(
            battle.ram_player_projection(7),
            PlayerRamProjection {
                admitted_sequence: 1,
                resolved_sequence: 1,
                contacts: Vec::new(),
                results: vec![json!({"seq": 1, "outcome": "unavailable"})],
            }
        );

        // Repeating the rejected optional row on N+1 is an idempotent local
        // terminal; it cannot create a second result or block the core input.
        assert!(matches!(
            apply(&mut battle, invalid_contact(2)),
            CommandEffect::PlayerInput(_)
        ));
        assert_eq!(battle.engine().player_input_seq(7), Some(2));
        assert_eq!(battle.ram_player_projection(7).results.len(), 1);

        let mut malformed_container = input(3).into_fields();
        malformed_container.insert("ram_contacts".to_owned(), json!({"seq": 2}));
        assert!(matches!(
            apply(
                &mut battle,
                WireObject::try_from(Value::Object(malformed_container)).unwrap()
            ),
            CommandEffect::PlayerInput(_)
        ));
        assert_eq!(battle.engine().player_input_seq(7), Some(3));
        assert_eq!(battle.ram_player_projection(7).admitted_sequence, 1);

        assert!(matches!(
            apply(&mut battle, input_with_ram(4, 2)),
            CommandEffect::PlayerInput(_)
        ));
        assert_eq!(battle.engine().player_input_seq(7), Some(4));
        let projection = battle.ram_player_projection(7);
        assert_eq!(projection.admitted_sequence, 2);
        assert_eq!(projection.resolved_sequence, 1);
        assert_eq!(projection.contacts.len(), 1);
        assert_eq!(projection.results.len(), 1);
    }

    #[test]
    fn dead_player_input_is_validated_then_folded_without_mutation() {
        let mut battle = loop_under_test();
        let player = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        battle
            .engine_mut()
            .apply_environment_damage_batch(
                scope(),
                &[EnvironmentDamageEffect {
                    target: player,
                    amount: 90,
                    client_simulation_reason: 3,
                }],
            )
            .unwrap();
        assert!(!battle.engine().combat().get(player).unwrap().alive);
        let pose = battle.engine().body_pose(player).unwrap();
        let ammo = battle.player_ammo_snapshot(7).unwrap();
        let up_cosine = battle.engine().player_up_cosine(7);

        let accepted = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input(1),
                },
                33_333,
                34,
            )
            .unwrap();
        assert!(matches!(accepted, CommandEffect::PlayerInput(_)));
        assert_eq!(battle.engine().player_input_seq(7), Some(0));
        assert_eq!(battle.engine().body_pose(player), Some(pose));
        assert_eq!(battle.engine().player_up_cosine(7), up_cosine);
        assert_eq!(battle.player_ammo_snapshot(7).unwrap(), ammo);

        let mut malformed = input(2).into_fields();
        malformed.insert("ram_contacts".to_owned(), json!([{"seq": 1}]));
        let rejection = battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: WireObject::try_from(Value::Object(malformed)).unwrap(),
                },
                66_666,
                67,
            )
            .unwrap_err();
        assert!(rejection.message.contains("player ram receipt is invalid"));
        assert_eq!(battle.engine().player_input_seq(7), Some(0));
        assert_eq!(battle.engine().body_pose(player), Some(pose));
        assert_eq!(battle.player_ammo_snapshot(7).unwrap(), ammo);
    }

    fn fixed_ram_failure_fixture() -> (BattleLoop, AuthorityRuntime, u64, VehicleKey, VehicleKey) {
        let tick = PREBATTLE_TICKS + 1;
        let first_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 1,
        };
        let second_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: first_key,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: BodyPose {
                        x: -1.0,
                        y: 0.0,
                        z: 0.0,
                        yaw: std::f64::consts::FRAC_PI_2,
                        pitch: 0.0,
                        roll: 0.0,
                        speed: 10.0,
                        aim_yaw: 0.0,
                        gun_pitch: 0.0,
                    },
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: second_key,
                    team: Team::Two,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: BodyPose {
                        x: 1.0,
                        y: 0.0,
                        z: 0.0,
                        yaw: 0.0,
                        pitch: 0.0,
                        roll: 0.0,
                        speed: 0.0,
                        aim_yaw: 0.0,
                        gun_pitch: 0.0,
                    },
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        for _ in 0..PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
        let mut battle = BattleLoop::new(engine);
        for key in [first_key, second_key] {
            battle.vehicle_extents.insert(key, (2.0, 1.0));
            battle
                .vehicle_ram_shapes
                .insert(key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap());
            battle
                .vehicle_masses
                .insert(key, PhysicsProfile::default().mass);
            battle
                .vehicle_ram_profiles
                .insert(key, RamDamageProfile::default());
        }
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(
            lineage,
            tick,
            1,
            vec![
                ram_bot_at_tick(
                    1,
                    Team::One.number(),
                    -1.0,
                    std::f64::consts::FRAC_PI_2,
                    10.0,
                    tick,
                ),
                ram_bot_at_tick(2, Team::Two.number(), 1.0, 0.0, 0.0, tick),
            ],
        )
        .unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::from([
                    (
                        1,
                        EntityRef {
                            entity_id: 101,
                            generation: 1,
                        },
                    ),
                    (
                        2,
                        EntityRef {
                            entity_id: 102,
                            generation: 1,
                        },
                    ),
                ]),
                humans: BTreeMap::new(),
            })
            .unwrap();
        (battle, authority, tick, first_key, second_key)
    }

    #[test]
    fn ram_timeout_and_bad_evidence_make_only_the_contact_unavailable() {
        let assert_healthy_round = |battle: &BattleLoop, first: VehicleKey, second: VehicleKey| {
            assert_eq!(battle.engine().combat().get(first).unwrap().health, 100);
            assert_eq!(battle.engine().combat().get(second).unwrap().health, 100);
            assert!(battle.engine().result().is_none());
        };

        let (mut battle, mut authority, tick, first, second) = fixed_ram_failure_fixture();
        battle
            .advance_ramming(&mut authority, tick, Vec::new(), Vec::new(), Vec::new())
            .unwrap();
        let probe = battle.pending_ram_contacts.values().next().unwrap().clone();
        let key = RamContactArmorIntentKey {
            pair: probe.pair,
            cursor: probe.cursor,
        };
        let mut bad_evidence = native_ram_evidence(&battle, &probe);
        bad_evidence.source_time_us = bad_evidence.source_time_us.saturating_add(1);
        battle
            .apply_due_ramming(&mut authority, vec![bad_evidence], Vec::new(), Vec::new())
            .unwrap();
        assert!(!battle.pending_ram_contacts.contains_key(&key));
        assert_healthy_round(&battle, first, second);

        let (mut battle, mut authority, tick, first, second) = fixed_ram_failure_fixture();
        battle
            .advance_ramming(&mut authority, tick, Vec::new(), Vec::new(), Vec::new())
            .unwrap();
        let probe = battle.pending_ram_contacts.values().next().unwrap().clone();
        let key = RamContactArmorIntentKey {
            pair: probe.pair,
            cursor: probe.cursor,
        };
        battle
            .apply_due_ramming(&mut authority, Vec::new(), Vec::new(), vec![key])
            .unwrap();
        assert!(!battle.pending_ram_contacts.contains_key(&key));
        assert_healthy_round(&battle, first, second);
    }

    #[test]
    fn fixed_tick_ramming_commits_collision_before_t3_native_damage() {
        let tick = crate::battle::PREBATTLE_TICKS + 1;
        let vehicles = vec![
            BattleVehicleInit {
                key: VehicleKey {
                    kind: VehicleKind::Bot,
                    id: 1,
                },
                team: Team::One,
                vehicle: "ussr:R11_MS-1".to_owned(),
                health: 100,
                pose: BodyPose {
                    x: -1.0,
                    y: 0.0,
                    z: 0.0,
                    yaw: std::f64::consts::FRAC_PI_2,
                    pitch: 0.0,
                    roll: 0.0,
                    speed: 10.0,
                    aim_yaw: 0.0,
                    gun_pitch: 0.0,
                },
                world_pose: true,
            },
            BattleVehicleInit {
                key: VehicleKey {
                    kind: VehicleKind::Bot,
                    id: 2,
                },
                team: Team::Two,
                vehicle: "ussr:R11_MS-1".to_owned(),
                health: 100,
                pose: BodyPose {
                    x: 1.0,
                    y: 0.0,
                    z: 0.0,
                    yaw: 0.0,
                    pitch: 0.0,
                    roll: 0.0,
                    speed: 0.0,
                    aim_yaw: 0.0,
                    gun_pitch: 0.0,
                },
                world_pose: true,
            },
        ];
        let mut engine = BattleEngine::new(
            scope(),
            vehicles,
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        for _ in 0..crate::battle::PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
        let mut battle = BattleLoop::new(engine);
        for id in [1, 2] {
            let key = VehicleKey {
                kind: VehicleKind::Bot,
                id,
            };
            battle.vehicle_extents.insert(key, (2.0, 1.0));
            battle
                .vehicle_ram_shapes
                .insert(key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap());
            battle
                .vehicle_masses
                .insert(key, PhysicsProfile::default().mass);
            battle
                .vehicle_ram_profiles
                .insert(key, RamDamageProfile::default());
        }
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(
            lineage,
            tick,
            1,
            vec![
                ram_bot_at_tick(1, 1, -1.0, std::f64::consts::FRAC_PI_2, 10.0, tick),
                ram_bot_at_tick(2, 2, 1.0, 0.0, 0.0, tick),
            ],
        )
        .unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::from([
                    (
                        1,
                        EntityRef {
                            entity_id: 101,
                            generation: 1,
                        },
                    ),
                    (
                        2,
                        EntityRef {
                            entity_id: 102,
                            generation: 1,
                        },
                    ),
                ]),
                humans: BTreeMap::new(),
            })
            .unwrap();

        let requests = battle
            .advance_ramming(&mut authority, tick, Vec::new(), Vec::new(), Vec::new())
            .unwrap();

        let first_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 1,
        };
        let second_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        assert_eq!(requests.len(), 1);
        assert_eq!(battle.engine().combat().get(first_key).unwrap().health, 100);
        assert_eq!(
            battle.engine().combat().get(second_key).unwrap().health,
            100
        );
        let first_pose = battle.engine().body_pose(first_key).unwrap();
        let second_pose = battle.engine().body_pose(second_key).unwrap();
        assert!(first_pose.x < -1.0);
        assert!(second_pose.x > 1.0);
        assert_eq!(first_pose.x, authority.bot_state(1).unwrap().position.x);
        assert_eq!(second_pose.x, authority.bot_state(2).unwrap().position.x);
        assert!(authority.bot_ram_velocity(1).unwrap().x < 10.0);
        assert!(authority.bot_ram_velocity(2).unwrap().x > 0.0);

        let probe = battle.pending_ram_contacts.values().next().unwrap().clone();
        let evidence = native_ram_evidence(&battle, &probe);
        battle
            .advance_ramming(
                &mut authority,
                tick + ORACLE_PIPELINE_TICKS,
                vec![evidence],
                Vec::new(),
                Vec::new(),
            )
            .unwrap();
        assert!(battle.engine().combat().get(first_key).unwrap().health < 100);
        assert!(battle.engine().combat().get(second_key).unwrap().health < 100);
    }

    #[test]
    fn due_ram_elimination_closes_the_boundary_without_stepping_bots() {
        let first_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 1,
        };
        let second_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        let pose = |x, yaw, speed| BodyPose {
            x,
            y: 0.0,
            z: 0.0,
            yaw,
            pitch: 0.0,
            roll: 0.0,
            speed,
            aim_yaw: yaw,
            gun_pitch: 0.0,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: first_key,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 1,
                    pose: pose(-1.0, std::f64::consts::FRAC_PI_2, 20.0),
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: second_key,
                    team: Team::Two,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: pose(1.0, 0.0, 0.0),
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        let critical_profile = || CriticalProfile {
            devices: ALL_DEVICE_NAMES
                .into_iter()
                .map(|name| {
                    (
                        name,
                        DeviceProfile {
                            max_hp: 50.0,
                            regen_hp: 25.0,
                        },
                    )
                })
                .collect(),
            crew: vec![CrewMemberProfile {
                name: CrewName::Commander,
                roles: BTreeSet::from([CrewRole::Commander]),
            }],
            engine_fire_starting_chance: 0.0,
            repair_speed_factor: 1.0,
        };
        engine
            .install_critical_profiles(BTreeMap::from([
                (first_key, critical_profile()),
                (second_key, critical_profile()),
            ]))
            .unwrap();
        let mut battle = BattleLoop::new(engine);

        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(
            lineage,
            0,
            1,
            vec![
                ram_bot(1, 1, -1.0, std::f64::consts::FRAC_PI_2, 20.0),
                ram_bot(2, 2, 1.0, 0.0, 0.0),
            ],
        )
        .unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::from([
                    (
                        1,
                        EntityRef {
                            entity_id: 101,
                            generation: 1,
                        },
                    ),
                    (
                        2,
                        EntityRef {
                            entity_id: 102,
                            generation: 1,
                        },
                    ),
                ]),
                humans: BTreeMap::new(),
            })
            .unwrap();
        let shapes = BTreeMap::from([
            (first_key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap()),
            (second_key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap()),
        ]);
        let masses = BTreeMap::from([
            (first_key, PhysicsProfile::default().mass),
            (second_key, PhysicsProfile::default().mass),
        ]);
        let profiles = BTreeMap::from([
            (first_key, RamDamageProfile::default()),
            (second_key, RamDamageProfile::default()),
        ]);
        let manifest = json!([
            {
                "id": 1, "team": 1, "slot": 0, "health": 1,
                "profile": planner_profile(),
                "route": {"id": "one", "waypoints": [{"x": -1.0, "y": 0.0, "z": 50.0, "hold": false}]},
            },
            {
                "id": 2, "team": 2, "slot": 0, "health": 100,
                "profile": planner_profile(),
                "route": {"id": "two", "waypoints": [{"x": 1.0, "y": 0.0, "z": -50.0, "hold": false}]},
            },
        ]);
        battle
            .install_authority(
                authority,
                BTreeMap::new(),
                BTreeMap::new(),
                BTreeMap::from([(first_key, (2.0, 1.0)), (second_key, (2.0, 1.0))]),
                shapes,
                masses,
                profiles,
                manifest,
            )
            .unwrap();
        for tick in 1..=PREBATTLE_TICKS {
            battle
                .poll_elapsed(tick_offset(tick), tick_offset(tick).as_millis() as u64)
                .unwrap();
        }

        let issued_tick = PREBATTLE_TICKS + 1;
        let issued = battle
            .poll_elapsed(
                tick_offset(issued_tick),
                tick_offset(issued_tick).as_millis() as u64,
            )
            .unwrap();
        let request = issued
            .oracle_requests
            .iter()
            .find(|request| {
                request.queries.iter().all(|query| {
                    matches!(
                        query.operation,
                        OracleOperation::RamContactArmorEvidence(..)
                    )
                })
            })
            .cloned()
            .expect("overlapping bots issue one RAM evidence batch");
        battle.accept_oracle_reply(ram_reply(&request)).unwrap();

        for tick in (issued_tick + 1)..(issued_tick + ORACLE_PIPELINE_TICKS) {
            let output = battle
                .poll_elapsed(tick_offset(tick), tick_offset(tick).as_millis() as u64)
                .unwrap();
            assert!(output.ticks[0].result.is_none());
        }
        let apply_tick = issued_tick + ORACLE_PIPELINE_TICKS;
        let terminal = battle
            .poll_elapsed(
                tick_offset(apply_tick),
                tick_offset(apply_tick).as_millis() as u64,
            )
            .unwrap();
        assert!(terminal.ticks[0].result.is_some());
        assert!(terminal.oracle_requests.is_empty());
        assert_eq!(
            battle.authority().unwrap().bot_state(1).unwrap().tick,
            apply_tick - 1
        );
        assert!(battle.is_terminal());
    }

    #[test]
    fn player_receipt_resolves_against_published_bot_history_once() {
        let player_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let bot_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: 1,
        };
        let pose = |x, yaw, speed| BodyPose {
            x,
            y: 0.0,
            z: 0.0,
            yaw,
            pitch: 0.0,
            roll: 0.0,
            speed,
            aim_yaw: yaw,
            gun_pitch: 0.0,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: player_key,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: pose(-1.0, std::f64::consts::FRAC_PI_2, 10.0),
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: bot_key,
                    team: Team::Two,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: pose(1.0, 0.0, 0.0),
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        for _ in 0..crate::battle::PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
        let mut battle = BattleLoop::new(engine);
        for key in [player_key, bot_key] {
            battle.vehicle_extents.insert(key, (2.0, 1.0));
            battle
                .vehicle_ram_shapes
                .insert(key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap());
            battle
                .vehicle_masses
                .insert(key, PhysicsProfile::default().mass);
            battle
                .vehicle_ram_profiles
                .insert(key, RamDamageProfile::default());
        }
        let revision = crate::battle::PREBATTLE_TICKS;
        let tick = revision + 1;
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(
            lineage,
            tick,
            1,
            vec![ram_bot_at_tick(1, 2, 1.0, 0.0, 0.0, tick)],
        )
        .unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::from([(
                    1,
                    EntityRef {
                        entity_id: 101,
                        generation: 1,
                    },
                )]),
                humans: BTreeMap::from([(
                    7,
                    EntityRef {
                        entity_id: 507,
                        generation: 1,
                    },
                )]),
            })
            .unwrap();
        let presentation_time_us = tick_offset(revision).as_micros() as u64;
        let published = battle.ram_bodies(&authority).unwrap();
        let published_bot = published
            .into_iter()
            .filter(|body| body.key == bot_key)
            .collect::<Vec<_>>();
        battle
            .ram
            .record_bot_frame(revision, presentation_time_us, &published_bot)
            .unwrap();
        battle
            .ram
            .admit_player_receipts(
                7,
                &[json!({
                    "seq": 1,
                    "bot_id": 1,
                    "bot_state_revision": revision,
                    "presentation_time_us": presentation_time_us,
                    "contact_x": 0.0,
                    "contact_y": 0.5,
                    "contact_z": 0.0,
                    "x": -1.0,
                    "y": 0.0,
                    "z": 0.0,
                    "yaw": std::f64::consts::FRAC_PI_2,
                    "pitch": 0.0,
                    "roll": 0.0,
                    "vx": 10.0,
                    "vy": 0.0,
                    "vz": 0.0,
                    "turret_yaw": 0.0,
                    "gun_pitch": 0.0,
                    "siege_state": 0,
                })],
            )
            .unwrap();

        let requests = battle
            .advance_ramming(&mut authority, tick, Vec::new(), Vec::new(), Vec::new())
            .unwrap();

        assert_eq!(requests.len(), 1);
        assert_eq!(
            battle.engine().combat().get(player_key).unwrap().health,
            100
        );
        assert_eq!(battle.engine().combat().get(bot_key).unwrap().health, 100);
        assert_eq!(battle.engine().body_pose(player_key).unwrap().x, -1.0);
        assert_eq!(battle.engine().body_pose(bot_key).unwrap().x, 1.0);
        assert!(authority.bot_ram_velocity(1).unwrap().x > 0.0);
        let projection = battle.ram_player_projection(7);
        assert_eq!(projection.admitted_sequence, 1);
        assert_eq!(projection.resolved_sequence, 0);
        assert_eq!(projection.contacts.len(), 1);

        let probe = battle.pending_ram_contacts.values().next().unwrap().clone();
        let evidence = native_ram_evidence(&battle, &probe);
        battle
            .advance_ramming(
                &mut authority,
                tick + ORACLE_PIPELINE_TICKS,
                vec![evidence],
                Vec::new(),
                Vec::new(),
            )
            .unwrap();

        assert!(battle.engine().combat().get(player_key).unwrap().health < 100);
        assert!(battle.engine().combat().get(bot_key).unwrap().health < 100);
        let projection = battle.ram_player_projection(7);
        assert_eq!(projection.admitted_sequence, 1);
        assert_eq!(projection.resolved_sequence, 1);
        assert!(projection.contacts.is_empty());
    }

    #[test]
    fn player_pair_receipt_uses_ordered_pose_streams_and_t3_native_damage() {
        let first_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        let second_key = VehicleKey {
            kind: VehicleKind::Player,
            id: 8,
        };
        let spawn = |x| BodyPose {
            x,
            y: 0.0,
            z: 0.0,
            yaw: std::f64::consts::FRAC_PI_2,
            pitch: 0.0,
            roll: 0.0,
            speed: 0.0,
            aim_yaw: std::f64::consts::FRAC_PI_2,
            gun_pitch: 0.0,
        };
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                BattleVehicleInit {
                    key: first_key,
                    team: Team::One,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: spawn(-2.0),
                    world_pose: true,
                },
                BattleVehicleInit {
                    key: second_key,
                    team: Team::Two,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    health: 100,
                    pose: spawn(2.0),
                    world_pose: true,
                },
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        let critical_profile = || CriticalProfile {
            devices: ALL_DEVICE_NAMES
                .into_iter()
                .map(|name| {
                    (
                        name,
                        DeviceProfile {
                            max_hp: 50.0,
                            regen_hp: 25.0,
                        },
                    )
                })
                .collect(),
            crew: vec![CrewMemberProfile {
                name: CrewName::Commander,
                roles: BTreeSet::from([CrewRole::Commander]),
            }],
            engine_fire_starting_chance: 0.0,
            repair_speed_factor: 1.0,
        };
        engine
            .install_critical_profiles(BTreeMap::from([
                (first_key, critical_profile()),
                (second_key, critical_profile()),
            ]))
            .unwrap();
        for _ in 0..PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
        let mut battle = BattleLoop::new(engine);
        for key in [first_key, second_key] {
            battle.vehicle_extents.insert(key, (2.0, 1.0));
            battle
                .vehicle_ram_shapes
                .insert(key, RamShape::new(1.0, 2.0, -0.8, 2.0).unwrap());
            battle
                .vehicle_masses
                .insert(key, PhysicsProfile::default().mass);
            battle
                .vehicle_ram_profiles
                .insert(key, RamDamageProfile::default());
        }
        battle
            .install_player_ammo(
                BTreeMap::from([
                    (
                        7,
                        PlayerAmmoLedger::new_exact_loaded(
                            7,
                            0,
                            &ram_descriptor(),
                            &ram_profile(),
                            vec![30],
                            0,
                        )
                        .unwrap(),
                    ),
                    (
                        8,
                        PlayerAmmoLedger::new_exact_loaded(
                            8,
                            0,
                            &ram_descriptor(),
                            &ram_profile(),
                            vec![30],
                            0,
                        )
                        .unwrap(),
                    ),
                ]),
                BTreeMap::from([
                    (7, PhysicalBurstDescriptor::new(1, 0.0).unwrap()),
                    (8, PhysicalBurstDescriptor::new(1, 0.0).unwrap()),
                ]),
            )
            .unwrap();

        let player_input = |sequence: u64,
                            source_time_us: u64,
                            x: f64,
                            ram_vx: f64,
                            player_ram_contacts: Vec<Value>| {
            WireObject::try_from(json!({
                "type": "input",
                "round_id": scope().round_id,
                "input_seq": sequence,
                "forward": 0.0,
                "turn": 0.0,
                "aim_yaw": std::f64::consts::FRAC_PI_2,
                "gun_pitch": 0.0,
                "fire_seq": 0,
                "shell_index": 0,
                "x": x,
                "y": 0.0,
                "z": 0.0,
                "yaw": std::f64::consts::FRAC_PI_2,
                "pose_time_us": source_time_us,
                "speed": ram_vx,
                "ram_vx": ram_vx,
                "ram_vy": 0.0,
                "ram_vz": 0.0,
                "player_ram_contacts": player_ram_contacts,
            }))
            .unwrap()
        };
        let apply_input =
            |battle: &mut BattleLoop, player_id: u64, message: WireObject, source_time_us: u64| {
                battle
                    .apply_command(
                        QueuedCommand {
                            connection_id: player_id,
                            player_id,
                            scope: scope(),
                            message,
                        },
                        source_time_us,
                        source_time_us / 1_000,
                    )
                    .unwrap();
            };
        apply_input(
            &mut battle,
            7,
            player_input(1, 1_000_000, -2.0, 10.0, Vec::new()),
            1_000_000,
        );
        apply_input(
            &mut battle,
            8,
            player_input(1, 1_000_000, 2.0, 0.0, Vec::new()),
            1_000_000,
        );
        apply_input(
            &mut battle,
            7,
            player_input(
                2,
                1_100_000,
                -1.0,
                10.0,
                vec![json!({
                    "seq": 1,
                    "target_player_id": 8,
                    "presentation_time_us": 1_050_000,
                })],
            ),
            1_100_000,
        );
        apply_input(
            &mut battle,
            8,
            player_input(2, 1_100_000, 1.0, 0.0, Vec::new()),
            1_100_000,
        );

        let tick = PREBATTLE_TICKS + 1;
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let mut authority = AuthorityRuntime::new(lineage, tick, 1, Vec::new()).unwrap();
        authority
            .donate_native_entities(crate::authority_runtime::NativeEntityDonation {
                lineage,
                oracle_space: EntityRef {
                    entity_id: 900,
                    generation: 1,
                },
                bots: BTreeMap::new(),
                humans: BTreeMap::from([
                    (
                        7,
                        EntityRef {
                            entity_id: 507,
                            generation: 1,
                        },
                    ),
                    (
                        8,
                        EntityRef {
                            entity_id: 508,
                            generation: 1,
                        },
                    ),
                ]),
            })
            .unwrap();

        let requests = battle
            .advance_ramming(&mut authority, tick, Vec::new(), Vec::new(), Vec::new())
            .unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(
            battle.player_pair_ram_state(7),
            PlayerRamLedgerState {
                admitted_sequence: 1,
                resolved_sequence: 0,
                pending: 1,
            }
        );
        let probe = battle.pending_ram_contacts.values().next().unwrap().clone();
        assert_eq!(probe.pair, RamPair::new(first_key, second_key).unwrap());
        assert_eq!(probe.source_time_us, 1_050_000);
        assert_eq!((probe.first.vx, probe.second.vx), (10.0, 0.0));

        // A live client clock discontinuity starts a new historical segment
        // without rejecting the ordered input or cancelling this already
        // frozen native probe.
        apply_input(
            &mut battle,
            7,
            player_input(3, 900_000, -1.0, 10.0, Vec::new()),
            1_200_000,
        );
        apply_input(
            &mut battle,
            7,
            player_input(4, 1_000_000, -1.0, 10.0, Vec::new()),
            1_300_000,
        );
        assert_eq!(battle.engine().player_input_seq(7), Some(4));
        assert_eq!(
            battle.player_pair_ram_state(7),
            PlayerRamLedgerState {
                admitted_sequence: 1,
                resolved_sequence: 0,
                pending: 1,
            }
        );

        battle
            .advance_ramming(
                &mut authority,
                tick + ORACLE_PIPELINE_TICKS,
                vec![native_ram_evidence(&battle, &probe)],
                Vec::new(),
                Vec::new(),
            )
            .unwrap();

        assert_eq!(battle.engine().combat().get(first_key).unwrap().health, 65);
        assert_eq!(battle.engine().combat().get(second_key).unwrap().health, 65);
        assert_eq!(battle.engine().body_pose(first_key).unwrap().x, -1.0);
        assert_eq!(battle.engine().body_pose(second_key).unwrap().x, 1.0);
        assert_eq!(
            battle.player_pair_ram_state(7),
            PlayerRamLedgerState {
                admitted_sequence: 1,
                resolved_sequence: 1,
                pending: 0,
            }
        );
        assert_eq!(
            battle.player_pair_ram_state(8),
            PlayerRamLedgerState::default()
        );
    }

    #[test]
    fn he_terminal_intent_freezes_player_pose_and_exact_t_plus_three_window() {
        let mut battle = loop_under_test();
        let target = VehicleKey {
            kind: VehicleKind::Player,
            id: 7,
        };
        battle.hull_materials.insert(
            target,
            vec![MaterialInfo {
                armor_mm: 20.0,
                vehicle_damage_factor: 1.0,
                ..MaterialInfo::default()
            }],
        );
        battle
            .apply_command(
                QueuedCommand {
                    connection_id: 40,
                    player_id: 7,
                    scope: scope(),
                    message: input(1),
                },
                33_333,
                33,
            )
            .unwrap();
        let lineage = OracleLineage {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            oracle_generation: 1,
        };
        let authority = AuthorityRuntime::new(lineage, 0, 1, Vec::new()).unwrap();
        let impact = ProjectileVec3 {
            x: 10.0,
            y: 2.0,
            z: 20.0,
        };
        let targets = battle
            .freeze_he_target_intents(&authority, impact, 10.0, None)
            .unwrap();
        assert_eq!(targets.len(), 1);
        assert_eq!(
            targets[0].target_pose.position,
            OracleVec3 {
                x: 10.0,
                y: 2.0,
                z: 20.0,
            }
        );
        assert!((targets[0].target_pose.turret_yaw + 0.05).abs() < 1.0e-12);

        let launch = ProjectileLaunch {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            shooter: target,
            shot_seq: 1,
            shell_index: 0,
            origin: ProjectileVec3 {
                x: 10.0,
                y: 2.0,
                z: 10.0,
            },
            velocity: ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            gravity: 9.81,
            max_distance: 720.0,
            max_time_ms: 10_000,
            is_he: true,
            splash_radius: 10.0,
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot: SourceShot {
                speed: 100.0,
                gravity: 9.81,
                max_distance: 720.0,
                piercing_power: [80.0, 80.0],
                deadeye: false,
                shell: SourceShell {
                    kind: "HIGH_EXPLOSIVE".to_owned(),
                    caliber: 75.0,
                    damage: [100.0, 40.0],
                    explosion_radius: 10.0,
                    explosion_damage_factor: Some(0.5),
                    explosion_damage_absorption_factor: Some(0.5),
                    explosion_edge_damage_factor: Some(0.5),
                },
            },
            fire_intent_seq: Some(1),
            fire_input_seq: Some(1),
        };
        let record = match ProjectileLedger::new()
            .admit_launch(
                launch,
                LaunchContext {
                    round_id: scope().round_id,
                    authority_epoch: scope().epoch,
                    shooter: target,
                    team: Team::One.number(),
                    source_vehicle: "ussr:R11_MS-1".to_owned(),
                    expected_shot_seq: 1,
                    server_time_ms: 0,
                },
            )
            .unwrap()
        {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        };
        let terminal = ProjectileTerminalProposal {
            plan_id: crate::projectile_sim::ProjectilePlanId {
                issued_tick: 8,
                projectile_ordinal: 1,
            },
            issued_tick: 8,
            applied_tick: 11,
            cause: ProjectileTerminalCause::Terrain {
                native_hit: crate::protocol::RayHit {
                    fraction: 1.0,
                    position: OracleVec3 {
                        x: 10.0,
                        y: 2.0,
                        z: 20.0,
                    },
                    normal: OracleVec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0,
                    },
                    material_id: Some(1),
                    hit_entity: None,
                },
            },
            resolution: crate::projectile::ProjectileResolution {
                round_id: scope().round_id,
                authority_epoch: scope().epoch,
                projectile_id: record.projectile_id.clone(),
                base_checked_ms: record.checked_through_ms,
                outcome: ProjectileOutcome::Impact,
                resolved_time_ms: 100,
                checked_distance: 10.0,
                piercing_loss: record.piercing_loss,
                penetration_factor: record.launch.penetration_factor,
                impact: Some(impact),
            },
            destructibles: Vec::new(),
        };
        let intent = HeExplosionEvidenceIntent::from_terminal(&record, &terminal, targets).unwrap();
        assert_eq!(intent.issued_tick, 11);
        assert_eq!(intent.apply_tick, 11 + ORACLE_PIPELINE_TICKS);
    }

    #[test]
    fn sustained_lag_is_a_terminal_loop_error() {
        let mut battle = loop_under_test();
        assert!(matches!(
            battle.poll_elapsed(Duration::from_secs(1), 1_000),
            Err(BattleLoopError::Tick(TickError::SimulationOverrun {
                lag_ticks: 30
            }))
        ));
    }
}
