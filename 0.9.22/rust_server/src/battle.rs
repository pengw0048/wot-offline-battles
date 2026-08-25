//! Deterministic battle transaction coordinator.
//!
//! This module composes the smaller ledgers without owning sockets or native
//! engine APIs. Native-world answers enter as already fenced oracle receipts.

use crate::client_replication::{
    AmmoRackState as ClientAmmoRackState, AssistEvent, BattleClientEvent, ClientReplicationError,
    CombatEvent as ClientCombatEvent, CriticalCause as ClientCriticalCause,
    CriticalCrewName as ClientCriticalCrewName, CriticalCrewState as ClientCriticalCrewState,
    CriticalDevice as ClientCriticalDevice, CriticalDeviceName as ClientCriticalDeviceName,
    CriticalDeviceState as ClientCriticalDeviceState, CriticalPayload as ClientCriticalPayload,
    CriticalRevision as ClientCriticalRevision, CriticalTransition as ClientCriticalTransition,
    DestructibleState, Point3, ProjectileImpactEvent, ShotEvent, ShotImpact, StunEvent,
    VehicleStatisticsEvent, SERVER_AUTHORITY_ID,
};
use crate::combat::{
    BodyPose, CombatLedger, DamageCommit, DamageError, DamageProposal, DamageSource, FireContext,
    FireIntentAdmission, FireIntentError, FireIntentLedger, FireIntentRequest, VehicleCombatState,
    VehicleKey, VehicleKind,
};
use crate::critical_damage::{
    firing_hard_gated, propose_death, propose_drowning, propose_fire_tick, propose_repair_tick,
    stat_factor as critical_stat_factor, CrewCondition, CrewName, CriticalCause, CriticalConfig,
    CriticalDamageError, CriticalEvent, CriticalLedger, CriticalMutation, CriticalPayload,
    CriticalProfile, CriticalSamples, CriticalStat, CriticalState as VehicleCriticalState,
    CriticalTrace, DeviceCondition, DeviceName, FireTickInput, RepairTickInput, StrikeInput,
};
use crate::descriptor::RepairInput;
use crate::destructible::{
    DestructibleCommit, DestructibleError, DestructibleLedger, DestructibleReceipt,
};
use crate::input::{InputAdmission, InputError, InputTimeline, PoseState};
use crate::player_ammo::PlayerAmmoBurst;
use crate::projectile::{
    LaunchAdmission, LaunchContext, ProgressAdmission, ProjectileCursor, ProjectileError,
    ProjectileLaunch, ProjectileLedger, ProjectileRecord, ProjectileResolution, ProjectileRicochet,
    ProjectileStunClearAdmission, ProjectileStunError, ProjectileStunLedger, ProjectileStunState,
    ProjectileStunTarget, ResolutionAdmission, RicochetAdmission, MAX_STUN_END_SERVER_TIME_MS,
};
use crate::protocol::SimulationScope;
use crate::ram::AtomicRamDamage;
use crate::replication::{
    ReplicationEmission, ReplicationError, ReplicationScheduler, ReplicationSnapshot, Revisions,
};
use crate::room::{BattleResult, BattleWinner, Team};
use crate::rules::{MapPoint, StandardRules, VehicleForRules, VehicleKey as RulesVehicleKey};
use crate::statistics::StatisticsLedger;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

pub const PREBATTLE_TICKS: u64 = 15 * 30;
pub const BATTLE_DURATION_TICKS: u64 = 900 * 30;
pub const TERMINAL_TICK: u64 = PREBATTLE_TICKS + BATTLE_DURATION_TICKS;
pub const MAX_SPLASH_TARGETS: usize = 30;

const SIEGE_DISABLED: u8 = 0;
const SIEGE_SWITCHING_ON: u8 = 1;
const SIEGE_ENABLED: u8 = 2;
const SIEGE_SWITCHING_OFF: u8 = 3;

#[derive(Clone, Copy, Debug)]
struct SiegeParams {
    enable_seconds: f64,
    disable_seconds: f64,
    speed_limit: f64,
    damaged_engine_time_factor: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct SiegeRuntime {
    state: u8,
    transition_ticks: u64,
}

fn siege_params(vehicle: &str) -> Option<SiegeParams> {
    let (enable_seconds, disable_seconds, speed_kph, damaged_engine_time_factor) = match vehicle {
        "sweden:S10_Strv_103_0_Series" | "sweden:S11_Strv_103B" => (2.0, 1.3, 10.0, 2.0),
        "sweden:S21_UDES_03" => (2.0, 2.0, 5.0, 2.0),
        "sweden:S22_Strv_S1" => (2.0, 1.3, 8.0, 2.0),
        _ => return None,
    };
    Some(SiegeParams {
        enable_seconds,
        disable_seconds,
        speed_limit: speed_kph / 3.6,
        damaged_engine_time_factor,
    })
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleVehicleInit {
    pub key: VehicleKey,
    pub team: Team,
    pub vehicle: String,
    pub health: u32,
    pub pose: BodyPose,
    pub world_pose: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PlayerInput {
    pub message: Value,
    pub source_time_us: Option<u64>,
    pub receipt_time_us: u64,
    pub pose: Option<PoseState>,
    pub pitch: f64,
    pub roll: f64,
    pub up_cosine: f64,
    pub aim_yaw: f64,
    pub gun_pitch: f64,
    pub shell_index: u8,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileDamageEffect {
    pub damage: DamageProposal,
    pub shot_result: u8,
    pub potential_damage: u32,
    pub critical: Option<CriticalMutation>,
    /// Canonical round-relative end time already resolved by trusted Rust
    /// gameplay law. Native collision evidence never supplies this value.
    pub stun_end_server_time_ms: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EnvironmentDamageEffect {
    pub target: VehicleKey,
    pub amount: u32,
    pub client_simulation_reason: u8,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileTerminal {
    pub resolution: ProjectileResolution,
    pub direct: Option<ProjectileDamageEffect>,
    pub splash: Vec<ProjectileDamageEffect>,
    pub destructibles: Vec<DestructibleReceipt>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleTickOutput {
    pub tick: u64,
    pub combat_live: bool,
    pub result: Option<BattleResult>,
    /// Strict #1513 events accumulated since the previous fixed tick.
    ///
    /// The socket owner supplies the client-facing frame scope, stable round
    /// roster, and event ordinals to `encode_battle_events`. A battle result is
    /// deliberately not synthesized here because the strict result event also
    /// needs the server's finalized receipt/statistics projection.
    pub client_events: Vec<BattleClientEvent>,
    pub emissions: Vec<ReplicationEmission>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleEntityView {
    pub key: VehicleKey,
    pub team: Team,
    pub vehicle: String,
    pub pose: BodyPose,
    pub world_pose: bool,
    pub shell_index: u8,
    pub last_fire_seq: u64,
    pub death_reason: u8,
    pub combat: VehicleCombatState,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FireRuntimeView {
    pub attacker: VehicleKey,
    pub elapsed_seconds: f64,
    pub timer_seconds: f64,
}

#[derive(Clone, Debug)]
struct EntityRuntime {
    team: Team,
    vehicle: String,
    pose: BodyPose,
    world_pose: bool,
    shell_index: u8,
    last_fire_seq: u64,
    death_reason: u8,
}

#[derive(Clone, Debug)]
struct PlayerRuntime {
    input: InputTimeline,
    fire: FireIntentLedger,
    up_cosine: f64,
}

#[derive(Clone, Copy, Debug)]
struct ProjectileDamageContext<'a> {
    record: &'a ProjectileRecord,
    resolution: &'a ProjectileResolution,
    effects: &'a BTreeMap<VehicleKey, ProjectileEffectMetadata>,
    critical: &'a BTreeMap<VehicleKey, ClientCriticalCommit>,
    stun_assisters: &'a BTreeMap<VehicleKey, VehicleKey>,
}

#[derive(Clone, Copy, Debug)]
struct ProjectileEffectMetadata {
    shot_result: u8,
    blocked_damage: u32,
    splash: bool,
}

type ClientCriticalCommit = (ClientCriticalPayload, ClientCriticalRevision);

#[derive(Debug, Error)]
pub enum BattleError {
    #[error("battle message belongs to a stale round or authority epoch")]
    StaleScope,
    #[error("battle already has a terminal result")]
    Finished,
    #[error("unknown vehicle {0:?}")]
    UnknownVehicle(VehicleKey),
    #[error("operation requires a player vehicle")]
    NotPlayer,
    #[error("battle vehicle configuration is invalid")]
    InvalidVehicle,
    #[error("player input payload is invalid")]
    InvalidInput,
    #[error("native oracle is unavailable")]
    OracleUnavailable,
    #[error("terminal projectile proposal is invalid")]
    InvalidProjectileEffects,
    #[error("projectile terminal proposal was retried with different effects")]
    ConflictingProjectileRetry,
    #[error("ram operation was retried with different damage")]
    ConflictingRamRetry,
    #[error(transparent)]
    Input(#[from] InputError),
    #[error(transparent)]
    Fire(#[from] FireIntentError),
    #[error(transparent)]
    Projectile(#[from] ProjectileError),
    #[error(transparent)]
    ProjectileStun(#[from] ProjectileStunError),
    #[error(transparent)]
    Damage(#[from] DamageError),
    #[error(transparent)]
    Destructible(#[from] DestructibleError),
    #[error(transparent)]
    Critical(#[from] CriticalDamageError),
    #[error(transparent)]
    Replication(#[from] ReplicationError),
    #[error(transparent)]
    ClientReplication(#[from] ClientReplicationError),
}

/// Authoritative single-round state. All methods are called by one event loop.
#[derive(Debug)]
pub struct BattleEngine {
    scope: SimulationScope,
    tick: u64,
    oracle_alive: bool,
    manifest_ready: bool,
    result: Option<BattleResult>,
    entities: BTreeMap<VehicleKey, EntityRuntime>,
    players: BTreeMap<u64, PlayerRuntime>,
    siege: BTreeMap<u64, SiegeRuntime>,
    combat: CombatLedger,
    critical: BTreeMap<VehicleKey, CriticalLedger>,
    equipment_fire_starting_chance_factors: BTreeMap<VehicleKey, f64>,
    repair_inputs: BTreeMap<VehicleKey, RepairInput>,
    fire_attackers: BTreeMap<VehicleKey, VehicleKey>,
    projectiles: ProjectileLedger,
    projectile_stuns: ProjectileStunLedger,
    destructibles: DestructibleLedger,
    statistics: StatisticsLedger,
    rules: StandardRules,
    replication: ReplicationScheduler,
    pending_events: Vec<BattleClientEvent>,
    projectile_terminals: BTreeMap<String, String>,
    ram_operations: BTreeMap<String, String>,
    manifest_revision: u64,
    order_revision: u64,
}

impl BattleEngine {
    pub fn new(
        scope: SimulationScope,
        vehicles: Vec<BattleVehicleInit>,
        team_one_bases: Vec<MapPoint>,
        team_two_bases: Vec<MapPoint>,
    ) -> Result<Self, BattleError> {
        if scope.round_id == 0 || vehicles.is_empty() || vehicles.len() > 60 {
            return Err(BattleError::InvalidVehicle);
        }
        let mut entities = BTreeMap::new();
        let mut players = BTreeMap::new();
        let mut combat = CombatLedger::new();
        let mut statistics = StatisticsLedger::new();
        for vehicle in vehicles {
            if vehicle.key.id == 0
                || vehicle.vehicle.is_empty()
                || vehicle.vehicle.len() > 128
                || vehicle.health == 0
                || !finite_pose(vehicle.pose)
                || entities.contains_key(&vehicle.key)
            {
                return Err(BattleError::InvalidVehicle);
            }
            combat.insert(
                vehicle.key,
                VehicleCombatState {
                    team: vehicle.team.number(),
                    health: vehicle.health,
                    max_health: vehicle.health,
                    alive: true,
                    frags: 0,
                    team_killer: false,
                    death_attacker: None,
                },
            );
            statistics.register(vehicle.key, vehicle.team.number());
            if vehicle.key.kind == VehicleKind::Player {
                players.insert(
                    vehicle.key.id,
                    PlayerRuntime {
                        input: InputTimeline::new(),
                        fire: FireIntentLedger::new(),
                        up_cosine: 1.0,
                    },
                );
            }
            entities.insert(
                vehicle.key,
                EntityRuntime {
                    team: vehicle.team,
                    vehicle: vehicle.vehicle,
                    pose: vehicle.pose,
                    world_pose: vehicle.world_pose,
                    shell_index: 0,
                    last_fire_seq: 0,
                    death_reason: 0,
                },
            );
        }
        let siege = entities
            .iter()
            .filter_map(|(key, entity)| {
                (key.kind == VehicleKind::Player && siege_params(&entity.vehicle).is_some())
                    .then_some((key.id, SiegeRuntime::default()))
            })
            .collect();
        Ok(Self {
            scope,
            tick: 0,
            oracle_alive: true,
            manifest_ready: true,
            result: None,
            entities,
            players,
            siege,
            combat,
            critical: BTreeMap::new(),
            equipment_fire_starting_chance_factors: BTreeMap::new(),
            repair_inputs: BTreeMap::new(),
            fire_attackers: BTreeMap::new(),
            projectiles: ProjectileLedger::new(),
            projectile_stuns: ProjectileStunLedger::new(scope.round_id, scope.epoch, 0, 0)?,
            destructibles: DestructibleLedger::new(),
            statistics,
            rules: StandardRules::new(team_one_bases, team_two_bases),
            replication: ReplicationScheduler::new(scope),
            pending_events: Vec::new(),
            projectile_terminals: BTreeMap::new(),
            ram_operations: BTreeMap::new(),
            manifest_revision: 1,
            order_revision: 0,
        })
    }

    pub fn scope(&self) -> SimulationScope {
        self.scope
    }

    pub fn tick(&self) -> u64 {
        self.tick
    }

    pub fn combat_live(&self) -> bool {
        self.result.is_none() && self.tick >= PREBATTLE_TICKS
    }

    pub fn result(&self) -> Option<&BattleResult> {
        self.result.as_ref()
    }

    pub fn combat(&self) -> &CombatLedger {
        &self.combat
    }

    pub fn critical_state(&self, key: VehicleKey) -> Option<&VehicleCriticalState> {
        self.critical.get(&key).map(CriticalLedger::state)
    }

    pub fn critical_profile(&self, key: VehicleKey) -> Option<&CriticalProfile> {
        self.critical.get(&key).map(CriticalLedger::profile)
    }

    pub fn critical_stat_factor(&self, key: VehicleKey, stat: CriticalStat) -> Option<f64> {
        self.critical
            .get(&key)
            .map(|ledger| critical_stat_factor(ledger.profile(), ledger.state(), stat))
    }

    pub fn propose_critical_strike(
        &self,
        key: VehicleKey,
        trace: &CriticalTrace,
        input: StrikeInput,
        samples: &CriticalSamples,
    ) -> Result<CriticalMutation, BattleError> {
        let combat = self
            .combat
            .get(key)
            .ok_or(BattleError::UnknownVehicle(key))?;
        if input.current_hull_health != combat.health {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let ledger = self.critical.get(&key).ok_or(BattleError::InvalidVehicle)?;
        let mut profile = ledger.profile().clone();
        let fire_factor = self
            .equipment_fire_starting_chance_factors
            .get(&key)
            .copied()
            .unwrap_or(1.0);
        profile.engine_fire_starting_chance =
            (profile.engine_fire_starting_chance * fire_factor).clamp(0.0, 1.0);
        Ok(crate::critical_damage::propose_strike(
            &profile,
            ledger.state(),
            trace,
            input,
            samples,
            CriticalConfig::default(),
        )?)
    }

    pub fn client_critical_snapshot(
        &self,
        key: VehicleKey,
    ) -> Result<(ClientCriticalPayload, ClientCriticalRevision), BattleError> {
        let payload = self
            .critical
            .get(&key)
            .ok_or(BattleError::InvalidVehicle)?
            .snapshot_payload();
        client_critical_commit(key, &payload)
    }

    pub fn fire_runtime(&self, key: VehicleKey, server_time_ms: u64) -> Option<FireRuntimeView> {
        let state = self.critical.get(&key)?.state();
        if !state.on_fire {
            return None;
        }
        let attacker = *self.fire_attackers.get(&key)?;
        let elapsed_seconds = state
            .fire_started_ms
            .map(|started| server_time_ms.saturating_sub(started) as f64 / 1_000.0)
            .unwrap_or(0.0)
            .clamp(0.0, 10.0);
        Some(FireRuntimeView {
            attacker,
            elapsed_seconds,
            timer_seconds: (state.fire_timer_micros as f64 / 1_000_000.0).clamp(0.0, 0.999_999),
        })
    }

    /// Install the exact descriptor-owned module and crew profiles before the
    /// loading barrier opens. The roster is immutable for the round, so a
    /// partial or extra profile set is always a setup error.
    pub fn install_critical_profiles(
        &mut self,
        profiles: BTreeMap<VehicleKey, CriticalProfile>,
    ) -> Result<(), BattleError> {
        if !self.critical.is_empty() {
            return Err(BattleError::InvalidVehicle);
        }
        let roster: BTreeSet<_> = self.entities.keys().copied().collect();
        if profiles.keys().copied().collect::<BTreeSet<_>>() != roster {
            return Err(BattleError::InvalidVehicle);
        }
        let critical = profiles
            .into_iter()
            .map(|(key, profile)| {
                CriticalLedger::new(profile, VehicleCriticalState::default())
                    .map(|ledger| (key, ledger))
            })
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        self.critical = critical;
        Ok(())
    }

    /// Install the target-owned fire-prevention factors from the immutable
    /// visible-player equipment loadouts.
    pub fn install_player_equipment_fire_factors(
        &mut self,
        factors: BTreeMap<VehicleKey, f64>,
    ) -> Result<(), BattleError> {
        self.install_equipment_fire_factors(VehicleKind::Player, factors)
    }

    /// Install the fire-prevention factors donated with the exact hidden
    /// #1513 default bot consumables. No factor is inferred from an item name.
    pub fn install_bot_equipment_fire_factors(
        &mut self,
        factors: BTreeMap<VehicleKey, f64>,
    ) -> Result<(), BattleError> {
        self.install_equipment_fire_factors(VehicleKind::Bot, factors)
    }

    fn install_equipment_fire_factors(
        &mut self,
        kind: VehicleKind,
        factors: BTreeMap<VehicleKey, f64>,
    ) -> Result<(), BattleError> {
        let expected = self
            .entities
            .keys()
            .filter(|key| key.kind == kind)
            .copied()
            .collect::<BTreeSet<_>>();
        if self
            .equipment_fire_starting_chance_factors
            .keys()
            .any(|key| key.kind == kind)
            || factors.keys().copied().collect::<BTreeSet<_>>() != expected
            || factors
                .values()
                .any(|factor| !factor.is_finite() || *factor < 0.0)
        {
            return Err(BattleError::InvalidVehicle);
        }
        self.equipment_fire_starting_chance_factors.extend(factors);
        Ok(())
    }

    /// Commit one fixed-tick group of player equipment critical mutations.
    /// Every per-player chain is validated against a cloned critical ledger;
    /// no actor, fire lineage, or track-assist state changes unless the whole
    /// batch can commit.
    pub fn apply_player_equipment_critical_batch(
        &mut self,
        scope: SimulationScope,
        mutations: &BTreeMap<VehicleKey, Vec<CriticalMutation>>,
    ) -> Result<(), BattleError> {
        self.apply_player_equipment_batch(scope, mutations, None)
    }

    /// Commit one player equipment operation across critical and stun state.
    /// A medkit clear is an end-time CAS so a newer projectile stun cannot be
    /// erased by an older admitted intent.
    pub fn apply_player_equipment_batch(
        &mut self,
        scope: SimulationScope,
        mutations: &BTreeMap<VehicleKey, Vec<CriticalMutation>>,
        stun_clear: Option<(VehicleKey, u64, u64)>,
    ) -> Result<(), BattleError> {
        let stun_clears = stun_clear.into_iter().collect::<Vec<_>>();
        self.apply_equipment_batch(scope, VehicleKind::Player, mutations, &stun_clears)
    }

    /// Commit one bot's staged mounted-consumable operation across critical
    /// and stun state. The complete operation is clone-then-commit; a stale
    /// stun end-time leaves critical, stun, fire lineage and track state
    /// untouched so the fixed-tick loop can discard only that bot's staged
    /// equipment ledger.
    pub fn apply_bot_equipment_batch(
        &mut self,
        scope: SimulationScope,
        target: VehicleKey,
        mutations: Vec<CriticalMutation>,
        stun_clear: Option<(u64, u64)>,
    ) -> Result<(), BattleError> {
        if target.kind != VehicleKind::Bot {
            return Err(BattleError::InvalidVehicle);
        }
        let mutations = BTreeMap::from([(target, mutations)]);
        let stun_clears = stun_clear
            .map(|(sequence, base_end_server_time_ms)| (target, sequence, base_end_server_time_ms))
            .into_iter()
            .collect::<Vec<_>>();
        self.apply_equipment_batch(scope, VehicleKind::Bot, &mutations, &stun_clears)
    }

    fn apply_equipment_batch(
        &mut self,
        scope: SimulationScope,
        kind: VehicleKind,
        mutations: &BTreeMap<VehicleKey, Vec<CriticalMutation>>,
        stun_clears: &[(VehicleKey, u64, u64)],
    ) -> Result<(), BattleError> {
        self.require_active_scope(scope)?;
        let mut next_critical = self.critical.clone();
        let mut next_projectile_stuns = self.projectile_stuns.clone();
        let mut extinguished = Vec::new();
        let mut repaired_tracks = Vec::new();
        for (&key, chain) in mutations {
            if key.kind != kind || !self.entities.contains_key(&key) {
                return Err(BattleError::UnknownVehicle(key));
            }
            let ledger = next_critical
                .get_mut(&key)
                .ok_or(BattleError::InvalidVehicle)?;
            let was_on_fire = ledger.state().on_fire;
            let tracks_before = tracks_destroyed(ledger.state());
            for mutation in chain {
                ledger.commit(mutation.clone())?;
            }
            if was_on_fire && !ledger.state().on_fire {
                extinguished.push(key);
            }
            if tracks_before && !tracks_destroyed(ledger.state()) {
                repaired_tracks.push(key);
            }
        }
        let stun_clears = stun_clears
            .iter()
            .map(|&(target, intent_seq, base_end_server_time_ms)| {
                if target.kind != kind || !self.entities.contains_key(&target) {
                    return Err(BattleError::UnknownVehicle(target));
                }
                Ok(next_projectile_stuns.clear_medkit(
                    target,
                    intent_seq,
                    base_end_server_time_ms,
                )?)
            })
            .collect::<Result<Vec<_>, BattleError>>()?;
        self.critical = next_critical;
        self.projectile_stuns = next_projectile_stuns;
        for key in extinguished {
            self.fire_attackers.remove(&key);
        }
        for key in repaired_tracks {
            self.statistics.clear_track_immobilised(key);
        }
        for stun_clear in stun_clears {
            if let ProjectileStunClearAdmission::Cleared { previous } = stun_clear {
                self.pending_events.push(BattleClientEvent::Stun(StunEvent {
                    active: false,
                    target: previous.target,
                    attacker: None,
                    end_server_time_ms: 0,
                }));
            }
        }
        Ok(())
    }

    /// Install one proven #1513 automatic-repair input after critical profiles
    /// are available. Reinstalling replaces the actor's previous loadout; an
    /// unavailable donation must call `clear_repair_input` instead.
    pub fn install_repair_input(
        &mut self,
        key: VehicleKey,
        input: RepairInput,
    ) -> Result<(), BattleError> {
        if !self.entities.contains_key(&key) {
            return Err(BattleError::UnknownVehicle(key));
        }
        if !self.critical.contains_key(&key) {
            return Err(BattleError::InvalidVehicle);
        }
        if !input.repair_factor.is_finite() || input.repair_factor <= 0.0 {
            return Err(BattleError::Critical(
                CriticalDamageError::InvalidRepairInput,
            ));
        }
        self.repair_inputs.insert(key, input);
        Ok(())
    }

    /// Disable automatic repair for one actor. Missing donated input is a hard
    /// absence, never permission to use the percentage-based fallback law.
    pub fn clear_repair_input(&mut self, key: VehicleKey) -> Result<(), BattleError> {
        if !self.entities.contains_key(&key) {
            return Err(BattleError::UnknownVehicle(key));
        }
        self.repair_inputs.remove(&key);
        Ok(())
    }

    pub fn repair_input(&self, key: VehicleKey) -> Option<RepairInput> {
        self.repair_inputs.get(&key).copied()
    }

    pub fn projectiles(&self) -> &ProjectileLedger {
        &self.projectiles
    }

    pub fn projectile_stun_state(&self, key: VehicleKey) -> Option<&ProjectileStunState> {
        self.projectile_stuns.state(key)
    }

    pub fn projectile_record(&self, projectile_id: &str) -> Option<&ProjectileRecord> {
        self.projectiles.active().get(projectile_id)
    }

    pub fn destructibles(&self) -> &DestructibleLedger {
        &self.destructibles
    }

    pub fn statistics(&self) -> &StatisticsLedger {
        &self.statistics
    }

    /// Replace the direct native-visibility edges for the current authority
    /// tick. Retained presentation contacts never enter this ledger.
    pub fn replace_direct_spotting(
        &mut self,
        observations: &BTreeMap<VehicleKey, BTreeSet<VehicleKey>>,
    ) -> Result<(), BattleError> {
        for (&reporter, targets) in observations {
            let reporter_entity = self
                .entities
                .get(&reporter)
                .ok_or(BattleError::UnknownVehicle(reporter))?;
            for &target in targets {
                let target_entity = self
                    .entities
                    .get(&target)
                    .ok_or(BattleError::UnknownVehicle(target))?;
                if reporter == target || reporter_entity.team == target_entity.team {
                    return Err(BattleError::InvalidVehicle);
                }
            }
        }

        let mut next = self.statistics.clone();
        for &reporter in self.entities.keys() {
            let targets = if self.combat.get(reporter).is_some_and(|state| state.alive) {
                observations
                    .get(&reporter)
                    .map(|targets| {
                        targets
                            .iter()
                            .copied()
                            .filter(|target| {
                                self.combat.get(*target).is_some_and(|state| state.alive)
                            })
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default()
            } else {
                Vec::new()
            };
            next.report_spotted(reporter, &targets);
        }
        self.statistics = next;
        Ok(())
    }

    pub fn entities(&self) -> impl Iterator<Item = BattleEntityView> + '_ {
        self.entities.iter().map(|(&key, entity)| BattleEntityView {
            key,
            team: entity.team,
            vehicle: entity.vehicle.clone(),
            pose: entity.pose,
            world_pose: entity.world_pose,
            shell_index: entity.shell_index,
            last_fire_seq: entity.last_fire_seq,
            death_reason: entity.death_reason,
            combat: self
                .combat
                .get(key)
                .expect("entity/combat invariant")
                .clone(),
        })
    }

    pub fn body_pose(&self, key: VehicleKey) -> Option<BodyPose> {
        self.entities.get(&key).map(|entity| entity.pose)
    }

    pub fn player_input_seq(&self, player_id: u64) -> Option<u64> {
        self.players
            .get(&player_id)
            .map(|runtime| runtime.input.last_input_seq())
    }

    pub fn player_ram_velocity(&self, player_id: u64) -> Option<(f64, f64, f64)> {
        self.players
            .get(&player_id)
            .and_then(|runtime| runtime.input.poses().back())
            .map(|pose| (pose.vx, pose.vy, pose.vz))
    }

    /// Mirror the shell selected by the Rust ammunition ledger.
    ///
    /// Player input may carry legacy HUD checkpoint fields, but those fields
    /// cannot select the shell used by the fire-intent ledger. BattleLoop
    /// calls this after every canonical ammunition transition.
    pub fn synchronize_player_shell(
        &mut self,
        player_id: u64,
        shell_index: u8,
    ) -> Result<(), BattleError> {
        if shell_index > 9 || !self.players.contains_key(&player_id) {
            return Err(BattleError::InvalidInput);
        }
        self.entities
            .get_mut(&player_key(player_id))
            .ok_or(BattleError::NotPlayer)?
            .shell_index = shell_index;
        Ok(())
    }

    pub fn player_up_cosine(&self, player_id: u64) -> Option<f64> {
        self.players
            .get(&player_id)
            .map(|runtime| runtime.up_cosine)
    }

    pub fn request_siege_state(&mut self, player_id: u64, enabled: bool) -> bool {
        let key = player_key(player_id);
        let Some(entity) = self.entities.get(&key) else {
            return false;
        };
        let Some(params) = siege_params(&entity.vehicle) else {
            return false;
        };
        if !self.combat.get(key).is_some_and(|state| state.alive) {
            return false;
        }
        let engine_condition = self
            .critical
            .get(&key)
            .and_then(|ledger| ledger.state().devices.get(&DeviceName::EngineHealth))
            .map(|device| device.condition)
            .unwrap_or(DeviceCondition::Normal);
        if engine_condition == DeviceCondition::Destroyed {
            return false;
        }
        let Some(runtime) = self.siege.get_mut(&player_id) else {
            return false;
        };
        if matches!(runtime.state, SIEGE_SWITCHING_ON | SIEGE_SWITCHING_OFF) {
            return false;
        }
        let (next_state, mut duration) = if enabled {
            if runtime.state == SIEGE_ENABLED {
                return true;
            }
            (SIEGE_SWITCHING_ON, params.enable_seconds)
        } else {
            if runtime.state == SIEGE_DISABLED {
                return true;
            }
            (SIEGE_SWITCHING_OFF, params.disable_seconds)
        };
        if engine_condition == DeviceCondition::Critical {
            duration *= params.damaged_engine_time_factor;
        }
        runtime.state = next_state;
        runtime.transition_ticks = (duration * 30.0).round().max(1.0) as u64;
        true
    }

    pub fn siege_status(&self, player_id: u64) -> (u8, u32) {
        self.siege
            .get(&player_id)
            .map_or((SIEGE_DISABLED, 0), |runtime| {
                (
                    runtime.state,
                    runtime
                        .transition_ticks
                        .saturating_mul(1_000)
                        .div_ceil(30)
                        .min(u64::from(u32::MAX)) as u32,
                )
            })
    }

    fn player_speed_limit(&self, player_id: u64) -> f64 {
        let key = player_key(player_id);
        let Some(entity) = self.entities.get(&key) else {
            return 200.0;
        };
        let Some(params) = siege_params(&entity.vehicle) else {
            return 200.0;
        };
        if self
            .siege
            .get(&player_id)
            .is_some_and(|runtime| runtime.state == SIEGE_ENABLED)
        {
            params.speed_limit
        } else {
            200.0
        }
    }

    fn advance_siege_states(&mut self) {
        for runtime in self.siege.values_mut() {
            if !matches!(runtime.state, SIEGE_SWITCHING_ON | SIEGE_SWITCHING_OFF) {
                runtime.transition_ticks = 0;
                continue;
            }
            runtime.transition_ticks = runtime.transition_ticks.saturating_sub(1);
            if runtime.transition_ticks == 0 {
                runtime.state = if runtime.state == SIEGE_SWITCHING_ON {
                    SIEGE_ENABLED
                } else {
                    SIEGE_DISABLED
                };
            }
        }
    }

    pub fn rules(&self) -> &StandardRules {
        &self.rules
    }

    pub fn add_replication_endpoint(&mut self, endpoint_id: u64) {
        self.replication.add_endpoint(endpoint_id);
    }

    pub fn remove_replication_endpoint(&mut self, endpoint_id: u64) {
        self.replication.remove_endpoint(endpoint_id);
    }

    pub fn submit_player_input(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        input: PlayerInput,
    ) -> Result<InputAdmission, BattleError> {
        self.require_active_scope(scope)?;
        let key = player_key(player_id);
        let alive = self
            .combat
            .get(key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .alive;
        if input.shell_index > 9
            || ![
                input.pitch,
                input.roll,
                input.up_cosine,
                input.aim_yaw,
                input.gun_pitch,
            ]
            .into_iter()
            .all(f64::is_finite)
            || !(-1.0..=1.0).contains(&input.up_cosine)
        {
            return Err(BattleError::InvalidInput);
        }
        if input.source_time_us.is_some() != input.pose.is_some() {
            return Err(BattleError::InvalidInput);
        }
        let speed_limit = self.player_speed_limit(player_id);
        let normalized_pose = input.pose.map(|mut pose| {
            pose.speed = pose.speed.clamp(-speed_limit, speed_limit);
            pose
        });
        let runtime = self.players.get(&player_id).ok_or(BattleError::NotPlayer)?;
        let mut next_timeline = runtime.input.clone();
        let admission = next_timeline.admit(&input.message)?;
        if admission == InputAdmission::ExactRetry {
            return Ok(admission);
        }
        if let (Some(source_time_us), Some(mut pose)) = (input.source_time_us, normalized_pose) {
            pose.alive &= alive;
            if !next_timeline.record_pose(source_time_us, input.receipt_time_us, pose) {
                return Err(BattleError::InvalidInput);
            }
        }
        let runtime = self
            .players
            .get_mut(&player_id)
            .expect("validated player remains present");
        runtime.input = next_timeline;
        runtime.up_cosine = input.up_cosine;
        let entity = self
            .entities
            .get_mut(&key)
            .ok_or(BattleError::UnknownVehicle(key))?;
        if let Some(pose) = normalized_pose {
            entity.pose.x = pose.x;
            entity.pose.y = pose.y;
            entity.pose.z = pose.z;
            entity.pose.yaw = pose.yaw;
            entity.pose.speed = if alive { pose.speed } else { 0.0 };
            entity.world_pose = true;
        }
        entity.pose.pitch = input.pitch;
        entity.pose.roll = input.roll;
        entity.pose.aim_yaw = input.aim_yaw;
        entity.pose.gun_pitch = input.gun_pitch;
        entity.shell_index = input.shell_index;
        Ok(admission)
    }

    /// Install one bot pose produced by the Rust 30 Hz simulator.
    ///
    /// Combat state and projectile launches have separate transactional APIs;
    /// this boundary therefore refuses fire-sequence jumps and never accepts
    /// HP/death verdicts with motion.
    pub fn update_bot_pose(
        &mut self,
        scope: SimulationScope,
        bot_id: u64,
        pose: BodyPose,
        world_pose: bool,
        shell_index: u8,
        fire_seq: u64,
    ) -> Result<(), BattleError> {
        self.require_active_scope(scope)?;
        if !finite_pose(pose) || shell_index > 9 {
            return Err(BattleError::InvalidInput);
        }
        let key = bot_key(bot_id);
        let entity = self
            .entities
            .get_mut(&key)
            .ok_or(BattleError::UnknownVehicle(key))?;
        if fire_seq != entity.last_fire_seq {
            return Err(BattleError::InvalidProjectileEffects);
        }
        entity.pose = pose;
        entity.world_pose = world_pose;
        entity.shell_index = shell_index;
        Ok(())
    }

    /// Commit server-owned drowning/environment damage from the bot simulator.
    ///
    /// Fire damage needs the actor that originally ignited the target. This
    /// method has no such lineage input, so a fire proposal fails closed rather
    /// than emitting a client event with a fabricated attacker.
    pub fn apply_bot_environment_damage(
        &mut self,
        scope: SimulationScope,
        bot_id: u64,
        amount: u32,
        source: DamageSource,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.require_active_scope(scope)?;
        if source != DamageSource::Environment {
            return Err(BattleError::InvalidProjectileEffects);
        }
        self.apply_environment_damage_batch(
            scope,
            &[EnvironmentDamageEffect {
                target: bot_key(bot_id),
                amount,
                client_simulation_reason: 5,
            }],
        )
    }

    /// Commit one fixed-tick set of server-owned environment HP proposals.
    /// The HP, drowning critical state, client events, and possible
    /// elimination result are one transaction across every affected actor.
    pub fn apply_environment_damage_batch(
        &mut self,
        scope: SimulationScope,
        effects: &[EnvironmentDamageEffect],
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.apply_environment_damage_batch_inner(scope, effects, true)
    }

    pub(crate) fn apply_environment_damage_batch_deferred(
        &mut self,
        scope: SimulationScope,
        effects: &[EnvironmentDamageEffect],
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.apply_environment_damage_batch_inner(scope, effects, false)
    }

    fn apply_environment_damage_batch_inner(
        &mut self,
        scope: SimulationScope,
        effects: &[EnvironmentDamageEffect],
        finalize_elimination: bool,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.require_active_scope(scope)?;
        if effects.is_empty() {
            return Ok(Vec::new());
        }
        let mut reasons = BTreeMap::new();
        let proposals = effects
            .iter()
            .map(|effect| {
                if !matches!(effect.client_simulation_reason, 3 | 5 | 7)
                    || reasons
                        .insert(effect.target, u16::from(effect.client_simulation_reason))
                        .is_some()
                {
                    return Err(BattleError::InvalidProjectileEffects);
                }
                Ok(DamageProposal {
                    attacker: None,
                    target: effect.target,
                    amount: effect.amount,
                    source: DamageSource::Environment,
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        let mut next_combat = self.combat.clone();
        let commits = next_combat.apply_atomic(&proposals)?;
        let mut next_critical = self.critical.clone();
        let mut critical_commits = BTreeMap::new();
        for commit in &commits {
            if !commit.dead || reasons.get(&commit.target) != Some(&5) {
                continue;
            }
            let ledger = next_critical
                .get_mut(&commit.target)
                .ok_or(BattleError::InvalidVehicle)?;
            let drowning = propose_drowning(ledger.profile(), ledger.state())?;
            if let Some(payload) = &drowning.payload {
                critical_commits.insert(
                    commit.target,
                    client_critical_commit(commit.target, payload)?,
                );
            }
            ledger.commit(drowning)?;
        }
        self.combat = next_combat;
        self.critical = next_critical;
        self.publish_damage_commits(&commits, None, Some(&reasons), Some(&critical_commits));
        if finalize_elimination {
            self.maybe_finish_elimination()?;
        }
        Ok(commits)
    }

    pub fn submit_fire_intent(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        request: FireIntentRequest,
        server_time_ms: u64,
    ) -> Result<FireIntentAdmission, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let key = player_key(player_id);
        let entity = self
            .entities
            .get(&key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .clone();
        let alive = self.combat.get(key).expect("entity/combat invariant").alive;
        let combat_accepting = self.player_physical_fire_accepting(player_id)?;
        let runtime = self
            .players
            .get_mut(&player_id)
            .ok_or(BattleError::NotPlayer)?;
        let context = FireContext {
            player_id,
            current_input_seq: runtime.input.last_input_seq(),
            current_shell_index: entity.shell_index,
            last_fire_seq: entity.last_fire_seq,
            pose_time_us: runtime.input.pose_time_us(),
            pose: entity.pose,
            server_time_ms,
            alive,
            combat_accepting,
        };
        Ok(runtime.fire.submit(request, context)?)
    }

    pub fn player_physical_fire_accepting(&self, player_id: u64) -> Result<bool, BattleError> {
        let key = player_key(player_id);
        let alive = self
            .combat
            .get(key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .alive;
        let siege_accepting = self.siege.get(&player_id).is_none_or(|runtime| {
            !matches!(runtime.state, SIEGE_SWITCHING_ON | SIEGE_SWITCHING_OFF)
        });
        let gun_accepting = self
            .critical
            .get(&key)
            .is_none_or(|ledger| !firing_hard_gated(ledger.state()));
        Ok(self.combat_live() && alive && siege_accepting && gun_accepting)
    }

    pub fn player_fire_intent_pending(&self, player_id: u64) -> Result<bool, BattleError> {
        Ok(self
            .players
            .get(&player_id)
            .ok_or(BattleError::NotPlayer)?
            .fire
            .has_pending())
    }

    /// Close a previously admitted player fire intent without a launch.
    /// Native-query timeout is a normal rejected shot, not permission to
    /// synthesize a muzzle pose or leave the per-player pending slot wedged.
    pub fn reject_player_fire_intent(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        intent_seq: u64,
        reason: &str,
    ) -> Result<(), BattleError> {
        // Rejection is terminal cleanup, not a gameplay mutation. A T+3
        // boundary may finish the battle before its pending muzzle receipts
        // are closed, so exact-scope cleanup remains valid after `result`.
        self.require_scope(scope)?;
        self.players
            .get_mut(&player_id)
            .ok_or(BattleError::NotPlayer)?
            .fire
            .reject(intent_seq, reason)?;
        Ok(())
    }

    pub fn commit_player_launch(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        intent_seq: u64,
        launch: ProjectileLaunch,
        server_time_ms: u64,
    ) -> Result<LaunchAdmission, BattleError> {
        let burst = PlayerAmmoBurst::ordinary(launch.shot_seq);
        self.commit_player_physical_launch(
            scope,
            player_id,
            intent_seq,
            launch,
            burst,
            server_time_ms,
        )
    }

    pub fn commit_player_physical_launch(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        intent_seq: u64,
        launch: ProjectileLaunch,
        burst: PlayerAmmoBurst,
        server_time_ms: u64,
    ) -> Result<LaunchAdmission, BattleError> {
        if burst.index != 0
            || burst.count == 0
            || burst.count > crate::player_ammo::MAX_PHYSICAL_BURST_COUNT
            || burst.group_seq != launch.shot_seq
        {
            return Err(BattleError::InvalidProjectileEffects);
        }
        self.require_active_scope(scope)?;
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let key = player_key(player_id);
        let entity = self
            .entities
            .get(&key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .clone();
        if !self.player_physical_fire_accepting(player_id)? {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let mut next_projectiles = self.projectiles.clone();
        let admission = next_projectiles.admit_launch(
            launch.clone(),
            LaunchContext {
                round_id: scope.round_id,
                authority_epoch: scope.epoch,
                shooter: key,
                team: entity.team.number(),
                source_vehicle: entity.vehicle.clone(),
                expected_shot_seq: entity.last_fire_seq.saturating_add(1),
                server_time_ms,
            },
        )?;
        if let LaunchAdmission::New(record) = &admission {
            let runtime = self.players.get(&player_id).ok_or(BattleError::NotPlayer)?;
            let mut next_fire = runtime.fire.clone();
            next_fire.commit_launch(
                intent_seq,
                launch.shot_seq,
                launch
                    .fire_input_seq
                    .ok_or(BattleError::InvalidProjectileEffects)?,
                launch.shell_index,
                [launch.origin.x, launch.origin.y, launch.origin.z],
                server_time_ms,
                &record.projectile_id,
            )?;
            self.players
                .get_mut(&player_id)
                .expect("player exists")
                .fire = next_fire;
            self.entities
                .get_mut(&key)
                .expect("entity exists")
                .last_fire_seq = launch.shot_seq;
            self.statistics.record_shot(key);
            self.pending_events
                .push(BattleClientEvent::Shot(ShotEvent::from_record_with_burst(
                    record, burst,
                )));
        }
        self.projectiles = next_projectiles;
        Ok(admission)
    }

    /// Commit one later physical shell from a previously admitted player
    /// trigger. The first shell already closed the fire-intent ledger; every
    /// tail shell still owns an independent projectile identity and shot stat.
    pub fn commit_player_burst_continuation(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
        launch: ProjectileLaunch,
        burst: PlayerAmmoBurst,
        server_time_ms: u64,
    ) -> Result<LaunchAdmission, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive
            || burst.index == 0
            || burst.count <= 1
            || burst.count > crate::player_ammo::MAX_PHYSICAL_BURST_COUNT
            || burst.group_seq.checked_add(u64::from(burst.index)) != Some(launch.shot_seq)
        {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let key = player_key(player_id);
        let entity = self
            .entities
            .get(&key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .clone();
        if !self.player_physical_fire_accepting(player_id)? {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let admission = self.projectiles.admit_launch(
            launch,
            LaunchContext {
                round_id: scope.round_id,
                authority_epoch: scope.epoch,
                shooter: key,
                team: entity.team.number(),
                source_vehicle: entity.vehicle,
                expected_shot_seq: entity.last_fire_seq.saturating_add(1),
                server_time_ms,
            },
        )?;
        if let LaunchAdmission::New(record) = &admission {
            self.entities
                .get_mut(&key)
                .expect("entity exists")
                .last_fire_seq = record.launch.shot_seq;
            self.statistics.record_shot(key);
            self.pending_events
                .push(BattleClientEvent::Shot(ShotEvent::from_record_with_burst(
                    record, burst,
                )));
        }
        Ok(admission)
    }

    pub fn commit_bot_launch(
        &mut self,
        scope: SimulationScope,
        bot_id: u64,
        launch: ProjectileLaunch,
        server_time_ms: u64,
    ) -> Result<LaunchAdmission, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive || !self.combat_live() {
            return Err(BattleError::OracleUnavailable);
        }
        let key = bot_key(bot_id);
        let entity = self
            .entities
            .get(&key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .clone();
        if !self
            .combat
            .get(key)
            .ok_or(BattleError::UnknownVehicle(key))?
            .alive
            || self
                .critical
                .get(&key)
                .is_some_and(|ledger| firing_hard_gated(ledger.state()))
        {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let admission = self.projectiles.admit_launch(
            launch.clone(),
            LaunchContext {
                round_id: scope.round_id,
                authority_epoch: scope.epoch,
                shooter: key,
                team: entity.team.number(),
                source_vehicle: entity.vehicle,
                expected_shot_seq: entity.last_fire_seq.saturating_add(1),
                server_time_ms,
            },
        )?;
        if let LaunchAdmission::New(record) = &admission {
            self.entities
                .get_mut(&key)
                .expect("entity exists")
                .last_fire_seq = launch.shot_seq;
            self.statistics.record_shot(key);
            self.pending_events
                .push(BattleClientEvent::Shot(ShotEvent::from_record(record)));
        }
        Ok(admission)
    }

    /// Advance a server-owned projectile cursor after the native oracle has
    /// released its exact T+3 geometry result.
    pub fn progress_projectile(
        &mut self,
        scope: SimulationScope,
        cursor: ProjectileCursor,
        server_time_ms: u64,
    ) -> Result<ProgressAdmission, BattleError> {
        self.progress_projectile_with_destructibles(scope, cursor, Vec::new(), server_time_ms)
    }

    /// Commit one exact-T+3 projectile cursor and every destructible it
    /// traversed as one transaction. This is required for AP shells which
    /// continue flying after destroying a low-health native object.
    pub fn progress_projectile_with_destructibles(
        &mut self,
        scope: SimulationScope,
        cursor: ProjectileCursor,
        destructibles: Vec<DestructibleReceipt>,
        server_time_ms: u64,
    ) -> Result<ProgressAdmission, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let mut next_projectiles = self.projectiles.clone();
        let admission = next_projectiles.progress(std::slice::from_ref(&cursor), server_time_ms)?;
        let mut next_destructibles = self.destructibles.clone();
        let destructible_commit =
            next_destructibles.commit_projectile_batch(SERVER_AUTHORITY_ID, destructibles)?;
        let destructible_events = destructible_commit
            .changed
            .iter()
            .map(DestructibleState::from_stored)
            .collect::<Result<Vec<_>, _>>()?;

        self.projectiles = next_projectiles;
        self.destructibles = next_destructibles;
        self.pending_events.extend(
            destructible_events
                .into_iter()
                .map(BattleClientEvent::Destructible),
        );
        Ok(admission)
    }

    /// Commit the one copied #1513 ricochet continuation and every
    /// destructible crossed before its first vehicle contact as one logical
    /// transaction. The projectile remains active and keeps its identity.
    pub fn continue_projectile_ricochet(
        &mut self,
        scope: SimulationScope,
        ricochet: ProjectileRicochet,
        destructibles: Vec<DestructibleReceipt>,
        server_time_ms: u64,
    ) -> Result<RicochetAdmission, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let mut next_projectiles = self.projectiles.clone();
        let admission = next_projectiles.ricochet(ricochet, server_time_ms)?;
        let mut next_destructibles = self.destructibles.clone();
        let destructible_commit =
            next_destructibles.commit_projectile_batch(SERVER_AUTHORITY_ID, destructibles)?;
        let destructible_events = destructible_commit
            .changed
            .iter()
            .map(DestructibleState::from_stored)
            .collect::<Result<Vec<_>, _>>()?;

        self.projectiles = next_projectiles;
        self.destructibles = next_destructibles;
        self.pending_events.extend(
            destructible_events
                .into_iter()
                .map(BattleClientEvent::Destructible),
        );
        Ok(admission)
    }

    /// Commit one server-owned vehicle-hull crush batch atomically and queue
    /// only the newly canonical destructibles for replication.
    pub fn commit_hull_destructibles(
        &mut self,
        scope: SimulationScope,
        receipts: Vec<DestructibleReceipt>,
    ) -> Result<DestructibleCommit, BattleError> {
        self.require_active_scope(scope)?;
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let mut next_destructibles = self.destructibles.clone();
        let commit = next_destructibles.commit_hull_batch(SERVER_AUTHORITY_ID, receipts)?;
        let events = commit
            .changed
            .iter()
            .map(DestructibleState::from_stored)
            .collect::<Result<Vec<_>, _>>()?;

        self.destructibles = next_destructibles;
        self.pending_events
            .extend(events.into_iter().map(BattleClientEvent::Destructible));
        Ok(commit)
    }

    /// Commit terminal projectile, damage, and destructibles as one logical
    /// transaction. Every ledger is cloned and validated before publication.
    pub fn resolve_projectile(
        &mut self,
        scope: SimulationScope,
        proposal: ProjectileTerminal,
        server_time_ms: u64,
    ) -> Result<ResolutionAdmission, BattleError> {
        self.resolve_projectile_inner(scope, proposal, server_time_ms, true, false)
    }

    pub(crate) fn resolve_projectile_deferred(
        &mut self,
        scope: SimulationScope,
        proposal: ProjectileTerminal,
        server_time_ms: u64,
    ) -> Result<ResolutionAdmission, BattleError> {
        self.resolve_projectile_inner(scope, proposal, server_time_ms, false, false)
    }

    pub(crate) fn resolve_projectile_cleanup_after_finish(
        &mut self,
        scope: SimulationScope,
        proposal: ProjectileTerminal,
        server_time_ms: u64,
    ) -> Result<ResolutionAdmission, BattleError> {
        if self.result.is_none() || proposal.direct.is_some() || !proposal.splash.is_empty() {
            return Err(BattleError::InvalidProjectileEffects);
        }
        self.resolve_projectile_inner(scope, proposal, server_time_ms, false, true)
    }

    fn resolve_projectile_inner(
        &mut self,
        scope: SimulationScope,
        proposal: ProjectileTerminal,
        server_time_ms: u64,
        finalize_elimination: bool,
        allow_finished: bool,
    ) -> Result<ResolutionAdmission, BattleError> {
        if allow_finished {
            self.require_scope(scope)?;
        } else {
            self.require_active_scope(scope)?;
        }
        if !self.oracle_alive {
            return Err(BattleError::OracleUnavailable);
        }
        let projectile_id = proposal.resolution.projectile_id.clone();
        let fingerprint = format!("{proposal:?}");
        if let Some(previous) = self.projectile_terminals.get(&projectile_id) {
            return if previous == &fingerprint {
                Ok(ResolutionAdmission::ExactRetry)
            } else {
                Err(BattleError::ConflictingProjectileRetry)
            };
        }
        if proposal.splash.len() > MAX_SPLASH_TARGETS {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let mut effects = Vec::with_capacity(1 + proposal.splash.len());
        if let Some(direct) = &proposal.direct {
            effects.push((direct, false));
        }
        effects.extend(proposal.splash.iter().map(|effect| (effect, true)));
        if effects.iter().any(|(effect, _)| {
            effect.shot_result > 2
                || effect.potential_damage > 5_000
                || effect.damage.amount > effect.potential_damage
                || effect
                    .stun_end_server_time_ms
                    .is_some_and(|end| end <= server_time_ms || end > MAX_STUN_END_SERVER_TIME_MS)
                || effect
                    .critical
                    .as_ref()
                    .is_some_and(|critical| critical.hull_damage != effect.damage.amount)
        }) {
            return Err(BattleError::InvalidProjectileEffects);
        }
        let damages = effects
            .iter()
            .map(|(effect, _)| effect.damage.clone())
            .collect::<Vec<_>>();
        let mut unique = BTreeSet::new();
        if damages.iter().any(|damage| !unique.insert(damage.target)) {
            return Err(BattleError::InvalidProjectileEffects);
        }

        let active = self
            .projectiles
            .active()
            .get(&projectile_id)
            .ok_or_else(|| ProjectileError::Unknown {
                projectile_id: projectile_id.clone(),
            })?
            .clone();
        if damages.iter().any(|damage| {
            damage.attacker != Some(active.launch.shooter) || damage.source != DamageSource::Shot
        }) || (!damages.is_empty() && proposal.resolution.impact.is_none())
            || (!proposal.splash.is_empty() && !active.launch.is_he)
            || proposal
                .direct
                .as_ref()
                .is_some_and(|effect| effect.damage.target == active.launch.shooter)
        {
            return Err(BattleError::InvalidProjectileEffects);
        }

        let mut effect_metadata = BTreeMap::new();
        let stun_assisters = effects
            .iter()
            .filter_map(|(effect, _)| {
                self.projectile_stuns
                    .active_assister_at(effect.damage.target, server_time_ms)
                    .map(|attacker| (effect.damage.target, attacker))
            })
            .collect::<BTreeMap<_, _>>();
        for &(effect, splash) in &effects {
            let target = self
                .combat
                .get(effect.damage.target)
                .ok_or(BattleError::UnknownVehicle(effect.damage.target))?;
            let blocked_damage =
                if !splash && target.alive && target.team != active.team && effect.shot_result != 2
                {
                    effect.potential_damage.saturating_sub(effect.damage.amount)
                } else {
                    0
                };
            effect_metadata.insert(
                effect.damage.target,
                ProjectileEffectMetadata {
                    shot_result: effect.shot_result,
                    blocked_damage,
                    splash,
                },
            );
        }

        let mut next_projectiles = self.projectiles.clone();
        let resolution = next_projectiles.resolve(proposal.resolution.clone(), server_time_ms)?;
        if matches!(resolution, ResolutionAdmission::ExactRetry) {
            return Err(BattleError::ConflictingProjectileRetry);
        }
        let mut next_critical = self.critical.clone();
        let mut next_fire_attackers = self.fire_attackers.clone();
        let mut critical_commits = BTreeMap::new();
        for &(effect, _) in &effects {
            let Some(mutation) = &effect.critical else {
                continue;
            };
            let target = effect.damage.target;
            let was_on_fire = next_critical
                .get(&target)
                .ok_or(BattleError::InvalidVehicle)?
                .state()
                .on_fire;
            if let Some(payload) = &mutation.payload {
                critical_commits.insert(target, client_critical_commit(target, payload)?);
            }
            next_critical
                .get_mut(&target)
                .ok_or(BattleError::InvalidVehicle)?
                .commit(mutation.clone())?;
            let is_on_fire = mutation.state().on_fire;
            if !was_on_fire && is_on_fire {
                next_fire_attackers.insert(target, active.launch.shooter);
            } else if was_on_fire && !is_on_fire {
                next_fire_attackers.remove(&target);
            }
        }
        let mut next_combat = self.combat.clone();
        let mut commits = next_combat.apply_atomic(&damages)?;
        for commit in &mut commits {
            let Some(ledger) = next_critical.get(&commit.target) else {
                return Err(BattleError::InvalidVehicle);
            };
            let roster = ledger.profile().crew_roster();
            if !roster.is_empty()
                && roster.is_subset(&ledger.state().crew_ko)
                && next_combat.knock_out_crew(commit.target, commit.attacker)?
            {
                commit.dead = true;
            }
            if commit.dead {
                next_fire_attackers.remove(&commit.target);
            }
        }
        let mut next_destructibles = self.destructibles.clone();
        let destructible_commit = next_destructibles
            .commit_projectile_batch(SERVER_AUTHORITY_ID, proposal.destructibles.clone())?;
        let destructible_events = destructible_commit
            .changed
            .iter()
            .map(DestructibleState::from_stored)
            .collect::<Result<Vec<_>, _>>()?;
        let record = match &resolution {
            ResolutionAdmission::Applied { record, .. } => record,
            ResolutionAdmission::ExactRetry => {
                return Err(BattleError::ConflictingProjectileRetry);
            }
        };
        let mut next_projectile_stuns = self.projectile_stuns.clone();
        let stun_targets = effects
            .iter()
            .filter_map(|(effect, _)| {
                let end_server_time_ms = effect.stun_end_server_time_ms?;
                commits
                    .iter()
                    .find(|commit| commit.target == effect.damage.target)
                    .filter(|commit| !commit.dead)
                    .map(|commit| ProjectileStunTarget {
                        target: commit.target,
                        end_server_time_ms,
                    })
            })
            .collect::<Vec<_>>();
        let activated_stuns = if stun_targets.is_empty() {
            Vec::new()
        } else {
            next_projectile_stuns
                .apply_projectile_batch(record, &stun_targets)?
                .activated
        };
        let impact_event = ProjectileImpactEvent::from_resolution(
            &proposal.resolution,
            record,
            proposal.direct.is_some(),
            None,
        )?;

        self.projectiles = next_projectiles;
        self.projectile_stuns = next_projectile_stuns;
        self.combat = next_combat;
        self.critical = next_critical;
        self.fire_attackers = next_fire_attackers;
        self.destructibles = next_destructibles;
        self.projectile_terminals
            .insert(projectile_id.clone(), fingerprint);
        self.pending_events.extend(
            destructible_events
                .into_iter()
                .map(BattleClientEvent::Destructible),
        );
        self.pending_events
            .push(BattleClientEvent::ProjectileImpact(impact_event));
        self.publish_damage_commits(
            &commits,
            Some(ProjectileDamageContext {
                record,
                resolution: &proposal.resolution,
                effects: &effect_metadata,
                critical: &critical_commits,
                stun_assisters: &stun_assisters,
            }),
            None,
            None,
        );
        self.pending_events
            .extend(activated_stuns.into_iter().map(|state| {
                BattleClientEvent::Stun(StunEvent {
                    active: true,
                    target: state.target,
                    attacker: Some(state.attacker),
                    end_server_time_ms: state.end_server_time_ms,
                })
            }));
        if finalize_elimination {
            self.maybe_finish_elimination()?;
        }
        Ok(resolution)
    }

    pub fn apply_ram(
        &mut self,
        scope: SimulationScope,
        operation_id: &str,
        first: DamageProposal,
        second: DamageProposal,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.apply_ram_batch_with_poses(
            scope,
            &[AtomicRamDamage {
                operation_id: operation_id.to_owned(),
                first,
                second,
            }],
            &BTreeMap::new(),
        )
    }

    /// Atomically install ramming pose corrections and symmetric damage pairs.
    ///
    /// Every pose and damage operation is validated against cloned state. No
    /// live pose, HP, retry fingerprint, or event changes until the complete
    /// batch succeeds.
    pub fn apply_ram_batch_with_poses(
        &mut self,
        scope: SimulationScope,
        operations: &[AtomicRamDamage],
        poses: &BTreeMap<VehicleKey, BodyPose>,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.apply_ram_batch_with_poses_inner(scope, operations, poses, true)
    }

    pub(crate) fn apply_ram_batch_with_poses_deferred(
        &mut self,
        scope: SimulationScope,
        operations: &[AtomicRamDamage],
        poses: &BTreeMap<VehicleKey, BodyPose>,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.apply_ram_batch_with_poses_inner(scope, operations, poses, false)
    }

    fn apply_ram_batch_with_poses_inner(
        &mut self,
        scope: SimulationScope,
        operations: &[AtomicRamDamage],
        poses: &BTreeMap<VehicleKey, BodyPose>,
        finalize_elimination: bool,
    ) -> Result<Vec<DamageCommit>, BattleError> {
        self.require_active_scope(scope)?;
        if !self.combat_live() {
            return Err(BattleError::InvalidProjectileEffects);
        }

        let mut next_entities = self.entities.clone();
        for (&key, &pose) in poses {
            if !finite_pose(pose) {
                return Err(BattleError::InvalidInput);
            }
            next_entities
                .get_mut(&key)
                .ok_or(BattleError::UnknownVehicle(key))?
                .pose = pose;
        }

        let mut next_combat = self.combat.clone();
        let mut next_operations = self.ram_operations.clone();
        let mut commits = Vec::new();
        for operation in operations {
            validate_ram_damage(operation)?;
            let fingerprint = format!("{:?}:{:?}", operation.first, operation.second);
            if let Some(previous) = next_operations.get(&operation.operation_id) {
                if previous == &fingerprint {
                    continue;
                }
                return Err(BattleError::ConflictingRamRetry);
            }
            let mut operation_commits =
                next_combat.apply_atomic(&[operation.first.clone(), operation.second.clone()])?;
            commits.append(&mut operation_commits);
            next_operations.insert(operation.operation_id.clone(), fingerprint);
        }

        self.entities = next_entities;
        self.combat = next_combat;
        self.ram_operations = next_operations;
        self.publish_damage_commits(&commits, None, None, None);
        if finalize_elimination {
            self.maybe_finish_elimination()?;
        }
        Ok(commits)
    }

    pub(crate) fn finalize_boundary_elimination(&mut self) -> Result<(), BattleError> {
        self.maybe_finish_elimination()
    }

    pub fn retire_player(
        &mut self,
        scope: SimulationScope,
        player_id: u64,
    ) -> Result<DamageCommit, BattleError> {
        self.require_active_scope(scope)?;
        let key = player_key(player_id);
        if !self.players.contains_key(&player_id) {
            return Err(BattleError::NotPlayer);
        }
        let commit = self.combat.retire_player(key)?;
        if commit.applied > 0 {
            self.publish_damage_commits(std::slice::from_ref(&commit), None, None, None);
            self.maybe_finish_elimination()?;
        }
        if commit.dead {
            self.statistics.clear_spotting_actor(key);
        }
        Ok(commit)
    }

    pub fn oracle_failed(&mut self, reason: &str) -> Result<(), BattleError> {
        if self.result.is_some() {
            return Ok(());
        }
        self.oracle_alive = false;
        self.finish(
            BattleWinner::Draw,
            bounded_reason(reason, "oracle_lost"),
            None,
        )
    }

    pub fn advance_tick(&mut self) -> Result<BattleTickOutput, BattleError> {
        let next_tick = self.tick.saturating_add(1);
        let simulated_now_ms = crate::clock::tick_offset(next_tick)
            .as_millis()
            .min(u128::from(u64::MAX)) as u64;
        self.advance_tick_at(simulated_now_ms)
    }

    pub fn advance_tick_at(
        &mut self,
        server_time_ms: u64,
    ) -> Result<BattleTickOutput, BattleError> {
        let next_tick = self.tick.saturating_add(1);
        let mut next_projectile_stuns = self.projectile_stuns.clone();
        let mut cleared_stuns = next_projectile_stuns.advance_tick(next_tick, server_time_ms)?;
        if self.result.is_none() {
            let next_time_micros = crate::clock::tick_offset(next_tick).as_micros();
            let current_time_micros = crate::clock::tick_offset(self.tick).as_micros();
            let dt_micros = next_time_micros
                .saturating_sub(current_time_micros)
                .min(u128::from(u64::MAX)) as u64;
            self.advance_siege_states();
            if self.combat_live() {
                // Preserve the copied Python authority order: module repair
                // advances before the same 30 Hz slice is applied to fire.
                self.advance_repair_tick(dt_micros)?;
                self.advance_fire_tick(dt_micros, server_time_ms)?;
                self.maybe_finish_elimination()?;
            }
            self.tick = next_tick;
            if self.result.is_none() && self.tick >= TERMINAL_TICK {
                self.finish(BattleWinner::Draw, "battle_timeout".to_owned(), None)?;
            } else if self.result.is_none() {
                let vehicles = self.rules_vehicles();
                let capture = self.rules.update(self.tick, self.combat_live(), &vehicles);
                if let Some(captured) = capture.captured {
                    self.finish(
                        BattleWinner::Team(captured.winner),
                        "base captured".to_owned(),
                        Some(captured.base_team),
                    )?;
                }
            }
        } else {
            // A due oracle transaction may finish the battle before this
            // boundary's ordinary tick tail runs. The fixed-tick controller
            // has still consumed the boundary, so keep the engine cursor in
            // lockstep while publishing the terminal snapshot exactly once.
            self.tick = self.tick.saturating_add(1);
        }

        for entity in self.entities() {
            if !entity.combat.alive {
                if let Some(previous) = next_projectile_stuns.clear_terminal(entity.key)? {
                    cleared_stuns.push(previous);
                }
            }
        }
        self.projectile_stuns = next_projectile_stuns;
        self.pending_events
            .extend(cleared_stuns.into_iter().map(|state| {
                BattleClientEvent::Stun(StunEvent {
                    active: false,
                    target: state.target,
                    attacker: None,
                    end_server_time_ms: 0,
                })
            }));

        let snapshot = self.replication_snapshot();
        let emissions = self
            .replication
            .plan_tick(self.scope, self.tick, &snapshot)?;
        let client_events = std::mem::take(&mut self.pending_events);
        Ok(BattleTickOutput {
            tick: self.tick,
            combat_live: self.combat_live(),
            result: self.result.clone(),
            client_events,
            emissions,
        })
    }

    fn advance_repair_tick(&mut self, dt_micros: u64) -> Result<(), BattleError> {
        if dt_micros == 0 || self.repair_inputs.is_empty() {
            return Ok(());
        }
        let inputs: Vec<_> = self
            .repair_inputs
            .iter()
            .map(|(&key, &input)| (key, input))
            .collect();
        let mut next_critical = self.critical.clone();
        let mut clear_inputs = Vec::new();
        let mut clear_track_assists = Vec::new();
        for (key, input) in inputs {
            let combat = self
                .combat
                .get(key)
                .ok_or(BattleError::UnknownVehicle(key))?;
            if !combat.alive {
                clear_inputs.push(key);
                continue;
            }
            let ledger = next_critical
                .get_mut(&key)
                .ok_or(BattleError::InvalidVehicle)?;
            let tracks_destroyed_before = tracks_destroyed(ledger.state());
            let repair = propose_repair_tick(
                ledger.profile(),
                ledger.state(),
                RepairTickInput {
                    dt_micros,
                    vehicle_alive: true,
                    // A donated factor replaces this legacy curve. It is never
                    // omitted on this authority path.
                    repair_skill_percent: 0.0,
                    has_big_repair_kit: input.has_big_kit,
                    repair_factor: Some(input.repair_factor),
                },
            )?;
            ledger.commit(repair)?;
            if tracks_destroyed_before && !tracks_destroyed(ledger.state()) {
                clear_track_assists.push(key);
            }
        }
        self.critical = next_critical;
        for key in clear_inputs {
            self.repair_inputs.remove(&key);
        }
        for key in clear_track_assists {
            self.statistics.clear_track_immobilised(key);
        }
        // Repair HP and repaired-device transitions are carried by the next
        // canonical critical snapshot. They are not damage and must not be
        // wrapped in a fabricated zero-damage combat event.
        Ok(())
    }

    fn advance_fire_tick(
        &mut self,
        dt_micros: u64,
        server_time_ms: u64,
    ) -> Result<(), BattleError> {
        if dt_micros == 0 || self.fire_attackers.is_empty() {
            return Ok(());
        }
        let mut next_critical = self.critical.clone();
        let mut next_combat = self.combat.clone();
        let mut next_fire_attackers = self.fire_attackers.clone();
        let mut critical_commits = BTreeMap::new();
        let mut damage_commits = Vec::new();
        let burning: Vec<_> = next_fire_attackers.keys().copied().collect();
        for target in burning {
            let attacker = *next_fire_attackers
                .get(&target)
                .ok_or(BattleError::InvalidVehicle)?;
            let combat = next_combat
                .get(target)
                .ok_or(BattleError::UnknownVehicle(target))?
                .clone();
            let ledger = next_critical
                .get_mut(&target)
                .ok_or(BattleError::InvalidVehicle)?;
            if !combat.alive || !ledger.state().on_fire {
                next_fire_attackers.remove(&target);
                continue;
            }
            let fire = propose_fire_tick(
                ledger.profile(),
                ledger.state(),
                FireTickInput {
                    dt_micros,
                    now_ms: Some(server_time_ms),
                    current_hull_health: combat.health,
                    max_hull_health: combat.max_health,
                    module_test_mode: false,
                },
            )?;
            if let Some(payload) = &fire.payload {
                critical_commits.insert(target, client_critical_commit(target, payload)?);
            }
            let fire_damage = fire.hull_damage;
            ledger.commit(fire)?;
            if !ledger.state().on_fire {
                next_fire_attackers.remove(&target);
            }
            if fire_damage == 0 {
                continue;
            }
            let mut commits = next_combat.apply_atomic(&[DamageProposal {
                attacker: Some(attacker),
                target,
                amount: fire_damage,
                source: DamageSource::Fire,
            }])?;
            if commits.first().is_some_and(|commit| commit.dead) {
                let death = propose_death(ledger.profile(), ledger.state(), CriticalCause::Fire)?;
                if let Some(payload) = &death.payload {
                    critical_commits.insert(target, client_critical_commit(target, payload)?);
                }
                ledger.commit(death)?;
                next_fire_attackers.remove(&target);
            }
            damage_commits.append(&mut commits);
        }
        self.critical = next_critical;
        self.combat = next_combat;
        self.fire_attackers = next_fire_attackers;
        self.publish_damage_commits(&damage_commits, None, None, Some(&critical_commits));
        Ok(())
    }

    fn publish_damage_commits(
        &mut self,
        commits: &[DamageCommit],
        projectile: Option<ProjectileDamageContext<'_>>,
        client_simulation_reasons: Option<&BTreeMap<VehicleKey, u16>>,
        critical_overrides: Option<&BTreeMap<VehicleKey, ClientCriticalCommit>>,
    ) {
        for commit in commits {
            let eligible_radio_reporters = self.statistics.radio_reporters(commit.target);
            let client_simulation_reason = client_simulation_reasons
                .and_then(|reasons| reasons.get(&commit.target))
                .copied();
            let death_reason = if commit.dead {
                match commit.source {
                    DamageSource::Fire => 1,
                    DamageSource::Ram => 2,
                    DamageSource::Environment => client_simulation_reason
                        .unwrap_or(0)
                        .min(u16::from(u8::MAX))
                        as u8,
                    DamageSource::Shot | DamageSource::PlayerLeft => 0,
                }
            } else {
                0
            };
            if commit.dead {
                self.repair_inputs.remove(&commit.target);
                if let Some(entity) = self.entities.get_mut(&commit.target) {
                    entity.death_reason = death_reason;
                }
            }
            let critical_commit = projectile
                .and_then(|context| context.critical.get(&commit.target))
                .or_else(|| critical_overrides.and_then(|critical| critical.get(&commit.target)))
                .cloned();
            if commit.applied > 0 || critical_commit.is_some() {
                self.rules.drop_contribution(rules_key(commit.target));
            }
            let shot = projectile.map(|context| {
                let impact = context
                    .resolution
                    .impact
                    .expect("projectile damage requires an impact position");
                let effect = context
                    .effects
                    .get(&commit.target)
                    .expect("every projectile damage commit has effect metadata");
                ShotImpact {
                    projectile_id: context.record.projectile_id.clone(),
                    shot_seq: context.record.launch.shot_seq,
                    shell_index: context.record.launch.shell_index,
                    shot_result: effect.shot_result,
                    blocked_damage: effect.blocked_damage,
                    splash: effect.splash,
                    impact: Point3 {
                        x: impact.x,
                        y: impact.y,
                        z: impact.z,
                    },
                }
            });
            if let Some(context) = projectile {
                let target_team = self
                    .combat
                    .get(commit.target)
                    .map(|state| state.team)
                    .unwrap_or(0);
                if context.record.team != target_team {
                    let effect = context
                        .effects
                        .get(&commit.target)
                        .expect("every projectile damage commit has effect metadata");
                    self.statistics.record_impact(
                        context.record.launch.shooter,
                        commit.target,
                        !effect.splash,
                        effect.splash,
                        !effect.splash && effect.shot_result == 2,
                        effect.blocked_damage,
                    );
                }
            }
            let target_tracks_destroyed =
                self.critical.get(&commit.target).is_some_and(|ledger| {
                    [DeviceName::LeftTrackHealth, DeviceName::RightTrackHealth]
                        .into_iter()
                        .any(|name| {
                            ledger.state().devices.get(&name).is_some_and(|device| {
                                device.condition == DeviceCondition::Destroyed
                            })
                        })
                });
            let track_transition = critical_commit.as_ref().is_some_and(|(payload, _)| {
                payload.events.iter().any(|event| {
                    matches!(
                        event,
                        ClientCriticalTransition::Device {
                            name: ClientCriticalDeviceName::LeftTrackHealth
                                | ClientCriticalDeviceName::RightTrackHealth,
                            ..
                        }
                    )
                })
            });
            if track_transition {
                if target_tracks_destroyed {
                    if let Some(attacker) = commit.attacker {
                        self.statistics
                            .mark_track_immobilised(commit.target, attacker);
                    }
                } else {
                    self.statistics.clear_track_immobilised(commit.target);
                }
            }
            let assists = self.statistics.record_damage(
                commit.attacker,
                commit.target,
                commit.applied,
                target_tracks_destroyed,
                projectile.and_then(|context| context.stun_assisters.get(&commit.target).copied()),
                &eligible_radio_reporters,
            );
            if commit.dead {
                self.statistics.clear_spotting_actor(commit.target);
            }
            self.pending_events
                .push(BattleClientEvent::Combat(ClientCombatEvent {
                    commit: commit.clone(),
                    death_reason,
                    display_health: (commit.source == DamageSource::Environment)
                        .then_some(commit.health),
                    client_simulation_reason: (commit.source == DamageSource::Environment)
                        .then_some(client_simulation_reason.unwrap_or(5)),
                    shot,
                    critical: critical_commit.as_ref().map(|value| value.0.clone()),
                    critical_revision: critical_commit.map(|value| value.1),
                }));
            self.pending_events.extend(
                assists
                    .iter()
                    .map(AssistEvent::from)
                    .map(BattleClientEvent::Assist),
            );
            if commit.dead && commit.applied > 0 {
                if let Some(attacker) = commit.attacker {
                    self.statistics.record_enemy_frag(
                        attacker,
                        commit.target,
                        i8::try_from(death_reason).unwrap_or(10),
                    );
                    if let Some(state) = self.combat.get(attacker) {
                        self.pending_events
                            .push(BattleClientEvent::VehicleStatistics(
                                VehicleStatisticsEvent::from_combat_state(attacker, state),
                            ));
                    }
                }
            }
        }
    }

    fn maybe_finish_elimination(&mut self) -> Result<(), BattleError> {
        if self.result.is_some() || !self.combat_live() || !self.manifest_ready {
            return Ok(());
        }
        let participant_teams: BTreeSet<_> = self
            .entities
            .values()
            .map(|entity| entity.team.number())
            .collect();
        if participant_teams.len() < 2 {
            return Ok(());
        }
        let alive_teams: BTreeSet<_> = self
            .entities
            .iter()
            .filter_map(|(key, entity)| {
                self.combat
                    .get(*key)
                    .is_some_and(|state| state.alive)
                    .then_some(entity.team.number())
            })
            .collect();
        match alive_teams.len() {
            0 => self.finish(BattleWinner::Draw, "team_eliminated".to_owned(), None),
            1 => self.finish(
                BattleWinner::Team(
                    Team::try_from(*alive_teams.iter().next().expect("one team"))
                        .expect("registered team invariant"),
                ),
                "team_eliminated".to_owned(),
                None,
            ),
            _ => Ok(()),
        }
    }

    fn finish(
        &mut self,
        winner: BattleWinner,
        reason: String,
        base_team: Option<Team>,
    ) -> Result<(), BattleError> {
        if self.result.is_some() {
            return Err(BattleError::Finished);
        }
        let result = BattleResult {
            winner,
            reason,
            base_team,
        };
        self.repair_inputs.clear();
        self.result = Some(result);
        Ok(())
    }

    fn require_active_scope(&self, scope: SimulationScope) -> Result<(), BattleError> {
        self.require_scope(scope)?;
        if self.result.is_some() {
            return Err(BattleError::Finished);
        }
        Ok(())
    }

    fn require_scope(&self, scope: SimulationScope) -> Result<(), BattleError> {
        if scope != self.scope {
            return Err(BattleError::StaleScope);
        }
        Ok(())
    }

    fn rules_vehicles(&self) -> Vec<VehicleForRules> {
        self.entities
            .iter()
            .map(|(&key, entity)| {
                let combat = self.combat.get(key).expect("entity/combat invariant");
                VehicleForRules {
                    key: rules_key(key),
                    team: entity.team,
                    alive: combat.alive,
                    world_pose: entity.world_pose,
                    x: entity.pose.x,
                    z: entity.pose.z,
                }
            })
            .collect()
    }

    fn replication_snapshot(&self) -> ReplicationSnapshot {
        let entities: Vec<_> = self
            .entities
            .iter()
            .map(|(key, entity)| {
                let combat = self.combat.get(*key).expect("entity/combat invariant");
                json!({
                    "kind": kind_name(key.kind),
                    "id": key.id,
                    "team": entity.team.number(),
                    "vehicle": entity.vehicle,
                    "health": combat.health,
                    "alive": combat.alive,
                    "frags": combat.frags,
                    "team_killer": combat.team_killer,
                    "x": entity.pose.x,
                    "y": entity.pose.y,
                    "z": entity.pose.z,
                    "yaw": entity.pose.yaw,
                })
            })
            .collect();
        let manifest: Vec<_> = self
            .entities
            .iter()
            .map(|(key, entity)| {
                json!({
                    "kind": kind_name(key.kind), "id": key.id,
                    "team": entity.team.number(), "vehicle": entity.vehicle,
                })
            })
            .collect();
        let destructibles: Vec<_> = self
            .destructibles
            .entries()
            .map(|entry| {
                json!({
                    "destructible_kind": format!("{:?}", entry.receipt.key.kind).to_lowercase(),
                    "chunk_id": entry.receipt.key.chunk_id,
                    "item_index": entry.receipt.key.item_index,
                    "mat_kind": entry.receipt.key.material_kind,
                    "revision": entry.revision,
                })
            })
            .collect();
        ReplicationSnapshot::new(
            json!(entities),
            json!(manifest),
            json!([]),
            json!(destructibles),
            Revisions {
                manifest: self.manifest_revision,
                orders: self.order_revision,
                destructibles: self.destructibles.revision(),
            },
        )
    }
}

fn player_key(id: u64) -> VehicleKey {
    VehicleKey {
        kind: VehicleKind::Player,
        id,
    }
}

fn client_critical_commit(
    target: VehicleKey,
    payload: &CriticalPayload,
) -> Result<ClientCriticalCommit, BattleError> {
    let critical = ClientCriticalPayload {
        devices: payload
            .devices
            .iter()
            .map(|device| ClientCriticalDevice {
                name: client_device_name(device.name),
                hp: device.hp,
                max_hp: device.max_hp,
                state: client_device_state(device.state),
            })
            .collect(),
        destroyed: payload
            .destroyed
            .iter()
            .copied()
            .map(client_device_name)
            .collect(),
        crew_ko: payload
            .crew_ko
            .iter()
            .copied()
            .map(client_crew_name)
            .collect(),
        crew_roster: Some(
            payload
                .crew_roster
                .iter()
                .copied()
                .map(client_crew_name)
                .collect(),
        ),
        fire: payload.fire,
        ammo_rack_death: payload.ammo_rack_death,
        events: payload
            .events
            .iter()
            .map(client_critical_event)
            .collect::<Result<Vec<_>, _>>()?,
    };
    let revision = match target.kind {
        VehicleKind::Player => ClientCriticalRevision::Player {
            revision: payload.revision,
            base_revision: payload.base_revision,
            ack_seq: 0,
        },
        VehicleKind::Bot => ClientCriticalRevision::Bot {
            revision: payload.revision,
            base_revision: payload.base_revision,
            ack_seq: 0,
        },
    };
    Ok((critical, revision))
}

fn client_critical_event(event: &CriticalEvent) -> Result<ClientCriticalTransition, BattleError> {
    Ok(match *event {
        CriticalEvent::Device {
            name,
            old_state,
            state,
            cause,
        } => ClientCriticalTransition::Device {
            name: client_device_name(name),
            state: client_device_state(state),
            old_state: Some(client_device_state(old_state)),
            cause: client_critical_cause(cause)?,
        },
        CriticalEvent::Crew { name, state, cause } => ClientCriticalTransition::Crew {
            name: client_crew_name(name),
            state: match state {
                CrewCondition::Normal => ClientCriticalCrewState::Normal,
                CrewCondition::Destroyed => ClientCriticalCrewState::Destroyed,
            },
            cause: client_critical_cause(cause)?,
        },
        CriticalEvent::Fire { state, cause } => ClientCriticalTransition::Fire {
            state,
            cause: client_critical_cause(cause)?,
        },
        CriticalEvent::AmmoRack { state, cause } => {
            if state != DeviceCondition::Destroyed {
                return Err(BattleError::InvalidProjectileEffects);
            }
            ClientCriticalTransition::AmmoRack {
                state: ClientAmmoRackState::Destroyed,
                cause: client_critical_cause(cause)?,
            }
        }
    })
}

fn client_critical_cause(cause: CriticalCause) -> Result<ClientCriticalCause, BattleError> {
    match cause {
        CriticalCause::Shot => Ok(ClientCriticalCause::Shot),
        CriticalCause::Explosion => Ok(ClientCriticalCause::Explosion),
        CriticalCause::Repair => Ok(ClientCriticalCause::Repair),
        CriticalCause::Fire => Ok(ClientCriticalCause::Fire),
        CriticalCause::Drowning => Ok(ClientCriticalCause::Drowning),
        CriticalCause::Equipment => Err(BattleError::InvalidProjectileEffects),
    }
}

fn client_device_name(name: DeviceName) -> ClientCriticalDeviceName {
    match name {
        DeviceName::EngineHealth => ClientCriticalDeviceName::EngineHealth,
        DeviceName::AmmoBayHealth => ClientCriticalDeviceName::AmmoBayHealth,
        DeviceName::FuelTankHealth => ClientCriticalDeviceName::FuelTankHealth,
        DeviceName::RadioHealth => ClientCriticalDeviceName::RadioHealth,
        DeviceName::LeftTrackHealth => ClientCriticalDeviceName::LeftTrackHealth,
        DeviceName::RightTrackHealth => ClientCriticalDeviceName::RightTrackHealth,
        DeviceName::GunHealth => ClientCriticalDeviceName::GunHealth,
        DeviceName::TurretRotatorHealth => ClientCriticalDeviceName::TurretRotatorHealth,
        DeviceName::SurveyingDeviceHealth => ClientCriticalDeviceName::SurveyingDeviceHealth,
    }
}

fn client_device_state(state: DeviceCondition) -> ClientCriticalDeviceState {
    match state {
        DeviceCondition::Normal => ClientCriticalDeviceState::Normal,
        DeviceCondition::Critical => ClientCriticalDeviceState::Critical,
        DeviceCondition::Destroyed => ClientCriticalDeviceState::Destroyed,
    }
}

fn client_crew_name(name: CrewName) -> ClientCriticalCrewName {
    match name {
        CrewName::Commander => ClientCriticalCrewName::Commander,
        CrewName::Driver => ClientCriticalCrewName::Driver,
        CrewName::Gunner1 => ClientCriticalCrewName::Gunner1,
        CrewName::Gunner2 => ClientCriticalCrewName::Gunner2,
        CrewName::Loader1 => ClientCriticalCrewName::Loader1,
        CrewName::Loader2 => ClientCriticalCrewName::Loader2,
        CrewName::Radioman1 => ClientCriticalCrewName::Radioman1,
        CrewName::Radioman2 => ClientCriticalCrewName::Radioman2,
    }
}

fn bot_key(id: u64) -> VehicleKey {
    VehicleKey {
        kind: VehicleKind::Bot,
        id,
    }
}

fn tracks_destroyed(state: &VehicleCriticalState) -> bool {
    [DeviceName::LeftTrackHealth, DeviceName::RightTrackHealth]
        .into_iter()
        .any(|name| {
            state
                .devices
                .get(&name)
                .is_some_and(|device| device.condition == DeviceCondition::Destroyed)
        })
}

fn rules_key(key: VehicleKey) -> RulesVehicleKey {
    match key.kind {
        VehicleKind::Player => RulesVehicleKey::Human(key.id),
        VehicleKind::Bot => RulesVehicleKey::Bot(key.id),
    }
}

fn kind_name(kind: VehicleKind) -> &'static str {
    match kind {
        VehicleKind::Player => "player",
        VehicleKind::Bot => "bot",
    }
}

fn validate_ram_damage(operation: &AtomicRamDamage) -> Result<(), BattleError> {
    if operation.operation_id.is_empty()
        || operation.operation_id.len() > 96
        || operation.first.source != DamageSource::Ram
        || operation.second.source != DamageSource::Ram
        || operation.first.target == operation.second.target
        || operation.first.attacker != Some(operation.second.target)
        || operation.second.attacker != Some(operation.first.target)
    {
        return Err(BattleError::InvalidProjectileEffects);
    }
    Ok(())
}

fn finite_pose(pose: BodyPose) -> bool {
    [
        pose.x,
        pose.y,
        pose.z,
        pose.yaw,
        pose.pitch,
        pose.roll,
        pose.speed,
        pose.aim_yaw,
        pose.gun_pitch,
    ]
    .into_iter()
    .all(f64::is_finite)
}

fn bounded_reason(value: &str, fallback: &str) -> String {
    let source = if value.trim().is_empty() {
        fallback
    } else {
        value
    };
    source.chars().take(64).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::client_replication::{
        encode_battle_events, BattleEventsFrame, EventRoster, FrameScope,
    };
    use crate::critical_damage::{
        propose_device_damage_over_time, CrewMemberProfile, CrewRole, CriticalLayer, CriticalShell,
        CriticalShellKind, CriticalTarget, DeviceProfile, ALL_DEVICE_NAMES,
    };
    use crate::destructible::{DestructibleKey, DestructibleKind};
    use crate::net::DeliveryClass;
    use crate::projectile::{ProjectileOutcome, ProjectileVec3, SourceShell, SourceShot};

    fn scope() -> SimulationScope {
        SimulationScope {
            round_id: 1,
            epoch: 2,
        }
    }

    fn pose(x: f64, z: f64) -> BodyPose {
        BodyPose {
            x,
            y: 0.0,
            z,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            speed: 0.0,
            aim_yaw: 0.0,
            gun_pitch: 0.0,
        }
    }

    fn vehicle(key: VehicleKey, team: Team, x: f64, z: f64) -> BattleVehicleInit {
        BattleVehicleInit {
            key,
            team,
            vehicle: "ussr:R11_MS-1".to_owned(),
            health: 100,
            pose: pose(x, z),
            world_pose: true,
        }
    }

    fn engine() -> BattleEngine {
        BattleEngine::new(
            scope(),
            vec![
                vehicle(player_key(1), Team::One, 100.0, 0.0),
                vehicle(player_key(2), Team::Two, 0.0, 0.0),
                vehicle(bot_key(11), Team::One, 80.0, 0.0),
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap()
    }

    fn install_critical_profiles(engine: &mut BattleEngine) {
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
            engine_fire_starting_chance: 1.0,
            repair_speed_factor: 1.0,
        };
        let profiles = engine
            .entities()
            .map(|entity| (entity.key, profile.clone()))
            .collect();
        engine.install_critical_profiles(profiles).unwrap();
    }

    fn destroy_device(engine: &mut BattleEngine, key: VehicleKey, name: DeviceName) {
        let health = engine.combat().get(key).unwrap().health;
        let ledger = engine.critical.get_mut(&key).unwrap();
        let mutation = propose_device_damage_over_time(
            ledger.profile(),
            ledger.state(),
            health,
            name,
            100.0,
            CriticalCause::Shot,
        )
        .unwrap();
        ledger.commit(mutation).unwrap();
    }

    fn ignite_vehicle(
        engine: &mut BattleEngine,
        target: VehicleKey,
        attacker: VehicleKey,
        now_ms: u64,
    ) {
        let fuel = CriticalTarget::Device(DeviceName::FuelTankHealth);
        let health = engine.combat().get(target).unwrap().health;
        let ledger = engine.critical.get_mut(&target).unwrap();
        let mutation = ledger
            .propose_strike(
                &CriticalTrace {
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
                    current_hull_health: health,
                    shell: CriticalShell {
                        kind: CriticalShellKind::ArmorPiercing,
                        module_damage: Some(100.0),
                    },
                    penetrated: Some(true),
                    by_explosion: false,
                    dead_eye: false,
                    distance_filters: true,
                    now_ms: Some(now_ms),
                },
                &CriticalSamples {
                    module_damage_factor: 1.0,
                    target_rolls: BTreeMap::from([(fuel, 0.0)]),
                    engine_fire_roll: None,
                },
                CriticalConfig::default(),
            )
            .unwrap();
        assert!(mutation.state().on_fire);
        ledger.commit(mutation).unwrap();
        engine.fire_attackers.insert(target, attacker);
    }

    fn encode_events(tick: u64, server_time_ms: u64, events: Vec<BattleClientEvent>) -> Vec<Value> {
        let roster = EventRoster::try_new([
            (player_key(1), Team::One.number()),
            (player_key(2), Team::Two.number()),
            (bot_key(11), Team::One.number()),
        ])
        .unwrap();
        let message = encode_battle_events(&BattleEventsFrame {
            scope: FrameScope {
                round_id: scope().round_id,
                authority_epoch: scope().epoch,
                server_tick: tick,
                server_time_ms,
                state_revision: 1,
            },
            first_ordinal: 0,
            roster,
            events,
        })
        .unwrap();
        message.get("events").unwrap().as_array().unwrap().clone()
    }

    fn advance_to_live(engine: &mut BattleEngine) {
        for _ in 0..PREBATTLE_TICKS {
            engine.advance_tick().unwrap();
        }
    }

    fn input(sequence: u64) -> PlayerInput {
        PlayerInput {
            message: json!({"type":"input", "input_seq":sequence}),
            source_time_us: Some(sequence * 100_000),
            receipt_time_us: sequence * 100_000,
            pose: Some(PoseState {
                x: 100.0,
                y: 0.0,
                z: 0.0,
                yaw: 0.0,
                speed: 0.0,
                ram_vx: 0.0,
                ram_vy: 0.0,
                ram_vz: 0.0,
                alive: true,
            }),
            pitch: 0.0,
            roll: 0.0,
            up_cosine: 1.0,
            aim_yaw: 0.0,
            gun_pitch: 0.0,
            shell_index: 0,
        }
    }

    fn launch() -> ProjectileLaunch {
        ProjectileLaunch {
            round_id: 1,
            authority_epoch: 2,
            shooter: player_key(1),
            shot_seq: 1,
            shell_index: 0,
            origin: ProjectileVec3 {
                x: 100.0,
                y: 0.0,
                z: 0.0,
            },
            velocity: ProjectileVec3 {
                x: -100.0,
                y: 0.0,
                z: 0.0,
            },
            gravity: 9.81,
            max_distance: 1_000.0,
            max_time_ms: 10_000,
            is_he: false,
            splash_radius: 0.0,
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot: SourceShot {
                speed: 100.0,
                gravity: 9.81,
                max_distance: 1_000.0,
                piercing_power: [100.0, 100.0],
                deadeye: false,
                shell: SourceShell {
                    kind: "ARMOR_PIERCING".to_owned(),
                    caliber: 45.0,
                    damage: [110.0, 110.0],
                    explosion_radius: 0.0,
                    explosion_damage_factor: None,
                    explosion_damage_absorption_factor: None,
                    explosion_edge_damage_factor: None,
                },
            },
            fire_intent_seq: Some(1),
            fire_input_seq: Some(1),
        }
    }

    fn impact_resolution(projectile_id: &str) -> ProjectileResolution {
        ProjectileResolution {
            round_id: scope().round_id,
            authority_epoch: scope().epoch,
            projectile_id: projectile_id.to_owned(),
            base_checked_ms: 0,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 100,
            checked_distance: 100.0,
            piercing_loss: 0.0,
            penetration_factor: 1.0,
            impact: Some(ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            }),
        }
    }

    fn projectile_effect(
        attacker: VehicleKey,
        target: VehicleKey,
        amount: u32,
        stun_end_server_time_ms: Option<u64>,
    ) -> ProjectileDamageEffect {
        ProjectileDamageEffect {
            damage: DamageProposal {
                attacker: Some(attacker),
                target,
                amount,
                source: DamageSource::Shot,
            },
            shot_result: 2,
            potential_damage: amount,
            critical: None,
            stun_end_server_time_ms,
        }
    }

    #[test]
    fn control_only_input_advances_sequence_without_fabricating_a_pose() {
        let mut engine = engine();
        let before = engine.body_pose(player_key(1)).unwrap();
        let mut message = input(1);
        message.source_time_us = None;
        message.pose = None;
        assert_eq!(
            engine.submit_player_input(scope(), 1, message).unwrap(),
            InputAdmission::Accepted
        );
        assert_eq!(engine.body_pose(player_key(1)).unwrap(), before);
    }

    #[test]
    fn rust_ammunition_shell_overrides_the_legacy_input_checkpoint() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        engine.synchronize_player_shell(1, 1).unwrap();
        advance_to_live(&mut engine);

        assert!(matches!(
            engine.submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            ),
            Err(BattleError::Fire(FireIntentError::InputBinding))
        ));
        assert!(engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 1,
                },
                20,
            )
            .is_ok());
    }

    #[test]
    fn invalid_pose_clock_does_not_consume_the_input_sequence() {
        let mut engine = engine();
        let mut invalid = input(1);
        invalid.source_time_us = Some(1_000_000);
        invalid.receipt_time_us = 0;
        assert!(matches!(
            engine.submit_player_input(scope(), 1, invalid),
            Err(BattleError::InvalidInput)
        ));
        assert_eq!(
            engine.submit_player_input(scope(), 1, input(1)).unwrap(),
            InputAdmission::Accepted
        );
    }

    #[test]
    fn countdown_gates_fire_then_full_projectile_kill_finishes() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        assert!(matches!(
            engine.submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                10,
            ),
            Err(BattleError::Fire(FireIntentError::NotAccepting))
        ));
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 30)
            .unwrap();
        let proposal = ProjectileTerminal {
            resolution: ProjectileResolution {
                round_id: 1,
                authority_epoch: 2,
                projectile_id: "1:p:1:1".to_owned(),
                base_checked_ms: 0,
                outcome: ProjectileOutcome::Impact,
                resolved_time_ms: 100,
                checked_distance: 100.0,
                piercing_loss: 0.0,
                penetration_factor: 1.0,
                impact: Some(ProjectileVec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 0.0,
                }),
            },
            direct: Some(ProjectileDamageEffect {
                damage: DamageProposal {
                    attacker: Some(player_key(1)),
                    target: player_key(2),
                    amount: 100,
                    source: DamageSource::Shot,
                },
                shot_result: 2,
                potential_damage: 100,
                critical: None,
                stun_end_server_time_ms: None,
            }),
            splash: vec![],
            destructibles: vec![DestructibleReceipt {
                key: DestructibleKey {
                    kind: DestructibleKind::Fragile,
                    chunk_id: 7,
                    item_index: 3,
                    material_kind: None,
                },
                x: 0.0,
                y: 0.0,
                z: 0.0,
                fall_yaw: 0.0,
                speed: 10.0,
                is_shot: true,
            }],
        };
        engine.resolve_projectile(scope(), proposal, 200).unwrap();
        assert_eq!(
            engine.result().unwrap().winner,
            BattleWinner::Team(Team::One)
        );
        assert_eq!(engine.destructibles().revision(), 1);
        assert_eq!(
            engine.statistics().row(player_key(1)).unwrap().damage_dealt,
            100
        );
        let output = engine.advance_tick().unwrap();
        let events = encode_events(output.tick, 200, output.client_events);
        assert_eq!(
            events
                .iter()
                .map(|event| event.get("kind").unwrap().as_str().unwrap())
                .collect::<Vec<_>>(),
            vec![
                "shot",
                "destructible",
                "projectile_impact",
                "hit",
                "vehicle_statistics",
            ]
        );
        assert_eq!(events[1].get("reported_by"), Some(&json!(0)));
        assert_eq!(events[3].get("projectile_id"), Some(&json!("1:p:1:1")));
        assert_eq!(events[3].get("shot_result"), Some(&json!(2)));
        assert_eq!(events[3].get("world_pose"), Some(&json!(true)));
    }

    #[test]
    fn direct_and_splash_stuns_publish_expire_and_award_assist() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                15_000,
            )
            .unwrap();
        let mut he = launch();
        he.is_he = true;
        he.splash_radius = 20.0;
        he.source_shot.shell.kind = "HIGH_EXPLOSIVE".to_owned();
        he.source_shot.shell.explosion_radius = 20.0;
        he.source_shot.shell.explosion_damage_factor = Some(0.5);
        he.source_shot.shell.explosion_damage_absorption_factor = Some(1.0);
        he.source_shot.shell.explosion_edge_damage_factor = Some(0.2);
        engine
            .commit_player_launch(scope(), 1, 1, he, 15_000)
            .unwrap();

        engine
            .resolve_projectile(
                scope(),
                ProjectileTerminal {
                    resolution: impact_resolution("1:p:1:1"),
                    direct: Some(projectile_effect(
                        player_key(1),
                        player_key(2),
                        0,
                        Some(16_000),
                    )),
                    splash: vec![projectile_effect(
                        player_key(1),
                        bot_key(11),
                        0,
                        Some(16_000),
                    )],
                    destructibles: Vec::new(),
                },
                15_100,
            )
            .unwrap();
        assert_eq!(
            engine
                .projectile_stun_state(player_key(2))
                .unwrap()
                .attacker,
            player_key(1)
        );
        assert_eq!(
            engine
                .projectile_stun_state(bot_key(11))
                .unwrap()
                .end_server_time_ms,
            16_000
        );
        let activation = engine.advance_tick_at(15_100).unwrap();
        let activation_events = encode_events(activation.tick, 15_100, activation.client_events);
        assert_eq!(
            activation_events
                .iter()
                .filter(|event| event.get("kind") == Some(&json!("stun")))
                .count(),
            2
        );
        assert!(activation_events
            .iter()
            .filter(|event| { event.get("kind") == Some(&json!("stun")) })
            .all(|event| event.get("active") == Some(&json!(true))));

        let mut bot_shot = launch();
        bot_shot.shooter = bot_key(11);
        bot_shot.origin.x = 80.0;
        bot_shot.fire_intent_seq = None;
        bot_shot.fire_input_seq = None;
        engine
            .commit_bot_launch(scope(), 11, bot_shot, 15_110)
            .unwrap();
        engine
            .resolve_projectile(
                scope(),
                ProjectileTerminal {
                    resolution: impact_resolution("1:b:11:1"),
                    direct: Some(projectile_effect(bot_key(11), player_key(2), 10, None)),
                    splash: Vec::new(),
                    destructibles: Vec::new(),
                },
                15_210,
            )
            .unwrap();
        assert_eq!(
            engine
                .statistics()
                .row(player_key(1))
                .unwrap()
                .damage_assisted_stun,
            10
        );
        let damage = engine.advance_tick_at(15_210).unwrap();
        let damage_events = encode_events(damage.tick, 15_210, damage.client_events);
        assert!(damage_events.iter().any(|event| {
            event.get("kind") == Some(&json!("assist"))
                && event.get("category") == Some(&json!("stun"))
        }));

        let expired = engine.advance_tick_at(16_000).unwrap();
        assert!(engine.projectile_stun_state(player_key(2)).is_none());
        assert!(engine.projectile_stun_state(bot_key(11)).is_none());
        let expired_events = encode_events(expired.tick, 16_000, expired.client_events);
        assert_eq!(
            expired_events
                .iter()
                .filter(|event| {
                    event.get("kind") == Some(&json!("stun"))
                        && event.get("active") == Some(&json!(false))
                })
                .count(),
            2
        );
    }

    #[test]
    fn medkit_stun_clear_is_atomic_end_time_cas_and_retry_safe() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                15_000,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 15_000)
            .unwrap();
        engine
            .resolve_projectile(
                scope(),
                ProjectileTerminal {
                    resolution: impact_resolution("1:p:1:1"),
                    direct: Some(projectile_effect(
                        player_key(1),
                        player_key(2),
                        0,
                        Some(16_000),
                    )),
                    splash: Vec::new(),
                    destructibles: Vec::new(),
                },
                15_100,
            )
            .unwrap();

        assert!(matches!(
            engine.apply_player_equipment_batch(
                scope(),
                &BTreeMap::new(),
                Some((player_key(2), 1, 15_999)),
            ),
            Err(BattleError::ProjectileStun(
                ProjectileStunError::ClearCasConflict
            ))
        ));
        assert_eq!(
            engine
                .projectile_stun_state(player_key(2))
                .unwrap()
                .end_server_time_ms,
            16_000
        );
        engine
            .apply_player_equipment_batch(
                scope(),
                &BTreeMap::new(),
                Some((player_key(2), 1, 16_000)),
            )
            .unwrap();
        assert!(engine.projectile_stun_state(player_key(2)).is_none());
        let event_count = engine.pending_events.len();
        engine
            .apply_player_equipment_batch(
                scope(),
                &BTreeMap::new(),
                Some((player_key(2), 1, 16_000)),
            )
            .unwrap();
        assert_eq!(engine.pending_events.len(), event_count);
    }

    #[test]
    fn bot_equipment_stun_conflict_rolls_back_its_staged_critical_chain() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                15_000,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 15_000)
            .unwrap();
        engine
            .resolve_projectile(
                scope(),
                ProjectileTerminal {
                    resolution: impact_resolution("1:p:1:1"),
                    direct: Some(projectile_effect(
                        player_key(1),
                        bot_key(11),
                        0,
                        Some(16_000),
                    )),
                    splash: Vec::new(),
                    destructibles: Vec::new(),
                },
                15_100,
            )
            .unwrap();
        destroy_device(&mut engine, bot_key(11), DeviceName::EngineHealth);
        let repair = {
            let ledger = engine.critical.get(&bot_key(11)).unwrap();
            crate::critical_damage::propose_repair_device(
                ledger.profile(),
                ledger.state(),
                None,
                true,
            )
            .unwrap()
        };

        assert!(matches!(
            engine.apply_bot_equipment_batch(
                scope(),
                bot_key(11),
                vec![repair.clone()],
                Some((1, 15_999)),
            ),
            Err(BattleError::ProjectileStun(
                ProjectileStunError::ClearCasConflict
            ))
        ));
        assert_eq!(
            engine
                .critical_state(bot_key(11))
                .unwrap()
                .device_state(
                    engine.critical_profile(bot_key(11)).unwrap(),
                    DeviceName::EngineHealth,
                )
                .condition,
            DeviceCondition::Destroyed
        );
        assert_eq!(
            engine
                .projectile_stun_state(bot_key(11))
                .unwrap()
                .end_server_time_ms,
            16_000
        );

        engine
            .apply_bot_equipment_batch(scope(), bot_key(11), vec![repair], Some((1, 16_000)))
            .unwrap();
        assert_eq!(
            engine
                .critical_state(bot_key(11))
                .unwrap()
                .device_state(
                    engine.critical_profile(bot_key(11)).unwrap(),
                    DeviceName::EngineHealth,
                )
                .condition,
            DeviceCondition::Normal
        );
        assert!(engine.projectile_stun_state(bot_key(11)).is_none());
    }

    #[test]
    fn destroyed_player_gun_is_a_server_owned_fire_gate() {
        let mut immediate = engine();
        install_critical_profiles(&mut immediate);
        immediate.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut immediate);
        destroy_device(&mut immediate, player_key(1), DeviceName::GunHealth);

        assert!(matches!(
            immediate.submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            ),
            Err(BattleError::Fire(FireIntentError::NotAccepting))
        ));

        let mut delayed = engine();
        install_critical_profiles(&mut delayed);
        delayed.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut delayed);
        delayed
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        destroy_device(&mut delayed, player_key(1), DeviceName::GunHealth);

        assert!(matches!(
            delayed.commit_player_launch(scope(), 1, 1, launch(), 30),
            Err(BattleError::InvalidProjectileEffects)
        ));
        assert!(delayed.projectiles().active().is_empty());
    }

    #[test]
    fn due_muzzle_cannot_launch_after_same_boundary_player_death() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        engine
            .apply_environment_damage_batch(
                scope(),
                &[EnvironmentDamageEffect {
                    target: player_key(1),
                    amount: 100,
                    client_simulation_reason: 5,
                }],
            )
            .unwrap();

        assert!(matches!(
            engine.commit_player_launch(scope(), 1, 1, launch(), 30),
            Err(BattleError::InvalidProjectileEffects)
        ));
        assert!(engine.projectiles().active().is_empty());
    }

    #[test]
    fn projectile_progress_and_ap_through_destructible_commit_atomically() {
        let mut engine = engine();
        advance_to_live(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 30)
            .unwrap();
        let cursor = ProjectileCursor {
            projectile_id: "1:p:1:1".to_owned(),
            base_checked_ms: 0,
            checked_through_ms: 33,
            checked_distance: 3.3,
            piercing_loss: 25.0,
            penetration_factor: 1.0,
        };
        let receipt = DestructibleReceipt {
            key: DestructibleKey {
                kind: DestructibleKind::Fragile,
                chunk_id: 7,
                item_index: 3,
                material_kind: None,
            },
            x: 99.0,
            y: 0.0,
            z: 0.0,
            fall_yaw: -std::f64::consts::FRAC_PI_2,
            speed: 12.0,
            is_shot: true,
        };

        let mut invalid = receipt.clone();
        invalid.x = f64::NAN;
        assert!(matches!(
            engine.progress_projectile_with_destructibles(
                scope(),
                cursor.clone(),
                vec![invalid],
                100,
            ),
            Err(BattleError::Destructible(DestructibleError::InvalidReceipt))
        ));
        assert_eq!(
            engine
                .projectile_record("1:p:1:1")
                .unwrap()
                .checked_through_ms,
            0
        );
        assert_eq!(engine.destructibles().revision(), 0);

        engine
            .progress_projectile_with_destructibles(scope(), cursor, vec![receipt.clone()], 100)
            .unwrap();
        let record = engine.projectile_record("1:p:1:1").unwrap();
        assert_eq!(record.checked_through_ms, 33);
        assert_eq!(record.piercing_loss, 25.0);
        assert_eq!(engine.destructibles().revision(), 1);
        assert_eq!(
            engine.destructibles().entries().next().unwrap().receipt,
            receipt.normalized().unwrap()
        );
        let output = engine.advance_tick().unwrap();
        assert!(output
            .client_events
            .iter()
            .any(|event| matches!(event, BattleClientEvent::Destructible(_))));
    }

    #[test]
    fn hull_destructible_batch_is_atomic_and_publishes_replication_event() {
        let mut engine = engine();
        advance_to_live(&mut engine);
        let receipt = DestructibleReceipt {
            key: DestructibleKey {
                kind: DestructibleKind::Fragile,
                chunk_id: 7,
                item_index: 9,
                material_kind: None,
            },
            x: 10.0,
            y: 0.0,
            z: 20.0,
            fall_yaw: 0.5,
            speed: 8.0,
            is_shot: false,
        };
        let mut invalid = receipt.clone();
        invalid.key.item_index = 10;
        invalid.x = f64::NAN;
        assert!(matches!(
            engine.commit_hull_destructibles(scope(), vec![receipt.clone(), invalid]),
            Err(BattleError::Destructible(DestructibleError::InvalidReceipt))
        ));
        assert_eq!(engine.destructibles().revision(), 0);
        assert_eq!(engine.destructibles().entries().count(), 0);

        let commit = engine
            .commit_hull_destructibles(scope(), vec![receipt.clone()])
            .unwrap();
        assert_eq!(commit.changed.len(), 1);
        assert_eq!(engine.destructibles().revision(), 1);
        let output = engine.advance_tick().unwrap();
        let event = output
            .client_events
            .iter()
            .find_map(|event| match event {
                BattleClientEvent::Destructible(event) => Some(event),
                _ => None,
            })
            .expect("hull commit must enqueue one destructible event");
        assert_eq!(event.item_index, 9);
        assert!(!event.is_shot);
        assert_eq!(event.revision, 1);

        let retry = engine
            .commit_hull_destructibles(scope(), vec![receipt])
            .unwrap();
        assert_eq!(retry.exact_retries, 1);
        assert!(retry.changed.is_empty());
        assert_eq!(engine.destructibles().revision(), 1);
    }

    #[test]
    fn projectile_ignition_keeps_attacker_lineage_for_server_fire_ticks() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                15_000,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 15_000)
            .unwrap();
        let trace = CriticalTrace {
            native_layers: vec![CriticalLayer {
                distance_m: 100.0,
                armor_mm: 20.0,
                vehicle_damage_factor: 1.0,
                target: Some(CriticalTarget::Device(DeviceName::FuelTankHealth)),
                chance_to_hit_by_projectile: Some(1.0),
                chance_to_hit_by_explosion: Some(1.0),
            }],
            internal_hits: Some(Vec::new()),
        };
        let critical = engine
            .propose_critical_strike(
                player_key(2),
                &trace,
                StrikeInput {
                    hull_damage: 0,
                    current_hull_health: 100,
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
                    target_rolls: BTreeMap::from([(
                        CriticalTarget::Device(DeviceName::FuelTankHealth),
                        0.0,
                    )]),
                    engine_fire_roll: None,
                },
            )
            .unwrap();
        assert!(critical.state().on_fire);
        engine
            .resolve_projectile(
                scope(),
                ProjectileTerminal {
                    resolution: ProjectileResolution {
                        round_id: 1,
                        authority_epoch: 2,
                        projectile_id: "1:p:1:1".to_owned(),
                        base_checked_ms: 0,
                        outcome: ProjectileOutcome::Impact,
                        resolved_time_ms: 100,
                        checked_distance: 100.0,
                        piercing_loss: 0.0,
                        penetration_factor: 1.0,
                        impact: Some(ProjectileVec3 {
                            x: 0.0,
                            y: 0.0,
                            z: 0.0,
                        }),
                    },
                    direct: Some(ProjectileDamageEffect {
                        damage: DamageProposal {
                            attacker: Some(player_key(1)),
                            target: player_key(2),
                            amount: 0,
                            source: DamageSource::Shot,
                        },
                        shot_result: 2,
                        potential_damage: 0,
                        critical: Some(critical),
                        stun_end_server_time_ms: None,
                    }),
                    splash: Vec::new(),
                    destructibles: Vec::new(),
                },
                15_100,
            )
            .unwrap();

        let mut fire_commit = None;
        for step in 1..=30 {
            let output = engine.advance_tick_at(15_100 + step * 34).unwrap();
            fire_commit = output
                .client_events
                .into_iter()
                .find_map(|event| match event {
                    BattleClientEvent::Combat(event)
                        if event.commit.source == DamageSource::Fire =>
                    {
                        Some(event.commit)
                    }
                    _ => None,
                });
            if fire_commit.is_some() {
                break;
            }
        }
        let fire_commit = fire_commit.expect("one second of burning produces a fire tick");
        assert_eq!(fire_commit.attacker, Some(player_key(1)));
        assert_eq!(fire_commit.target, player_key(2));
        assert_eq!(fire_commit.applied, 5);
        assert_eq!(engine.combat().get(player_key(2)).unwrap().health, 95);
        assert_eq!(
            engine.statistics().row(player_key(1)).unwrap().damage_dealt,
            5
        );
    }

    #[test]
    fn target_equipment_factor_scales_engine_fire_without_changing_base_profile() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine
            .install_player_equipment_fire_factors(BTreeMap::from([
                (player_key(1), 1.0),
                (player_key(2), 0.0),
            ]))
            .unwrap();
        let engine_target = CriticalTarget::Device(DeviceName::EngineHealth);
        let trace = CriticalTrace {
            native_layers: vec![CriticalLayer {
                distance_m: 1.0,
                armor_mm: 1.0,
                vehicle_damage_factor: 1.0,
                target: Some(engine_target),
                chance_to_hit_by_projectile: Some(1.0),
                chance_to_hit_by_explosion: Some(1.0),
            }],
            internal_hits: Some(Vec::new()),
        };
        let strike = |target| {
            engine
                .propose_critical_strike(
                    target,
                    &trace,
                    StrikeInput {
                        hull_damage: 0,
                        current_hull_health: 100,
                        shell: CriticalShell {
                            kind: CriticalShellKind::ArmorPiercing,
                            module_damage: Some(10.0),
                        },
                        penetrated: Some(true),
                        by_explosion: false,
                        dead_eye: false,
                        distance_filters: true,
                        now_ms: Some(15_000),
                    },
                    &CriticalSamples {
                        module_damage_factor: 1.0,
                        target_rolls: BTreeMap::from([(engine_target, 0.0)]),
                        engine_fire_roll: Some(0.0),
                    },
                )
                .unwrap()
        };

        assert!(strike(player_key(1)).state().on_fire);
        assert!(!strike(player_key(2)).state().on_fire);
        assert_eq!(
            engine
                .critical_profile(player_key(2))
                .unwrap()
                .engine_fire_starting_chance,
            1.0,
        );
    }

    #[test]
    fn siege_transition_gates_fire_and_clamps_enabled_pose_speed() {
        let mut siege_vehicle = vehicle(player_key(1), Team::One, 100.0, 0.0);
        siege_vehicle.vehicle = "sweden:S10_Strv_103_0_Series".to_owned();
        let mut engine = BattleEngine::new(
            scope(),
            vec![siege_vehicle, vehicle(player_key(2), Team::Two, 0.0, 0.0)],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);

        assert!(engine.request_siege_state(1, true));
        assert_eq!(engine.siege_status(1), (SIEGE_SWITCHING_ON, 2_000));
        assert!(matches!(
            engine.submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                15_000,
            ),
            Err(BattleError::Fire(FireIntentError::NotAccepting))
        ));
        for _ in 0..60 {
            engine.advance_tick().unwrap();
        }
        assert_eq!(engine.siege_status(1), (SIEGE_ENABLED, 0));
        let mut fast = input(2);
        fast.pose.as_mut().unwrap().speed = 100.0;
        engine.submit_player_input(scope(), 1, fast).unwrap();
        assert!((engine.body_pose(player_key(1)).unwrap().speed - 10.0 / 3.6).abs() < 1.0e-9);

        assert!(engine.request_siege_state(1, false));
        assert_eq!(engine.siege_status(1), (SIEGE_SWITCHING_OFF, 1_300));
        for _ in 0..39 {
            engine.advance_tick().unwrap();
        }
        assert_eq!(engine.siege_status(1), (SIEGE_DISABLED, 0));
    }

    #[test]
    fn ram_is_atomic_and_exact_retry_is_a_noop() {
        let mut engine = engine();
        advance_to_live(&mut engine);
        let first = DamageProposal {
            attacker: Some(player_key(2)),
            target: player_key(1),
            amount: 50,
            source: DamageSource::Ram,
        };
        let second = DamageProposal {
            attacker: Some(player_key(1)),
            target: player_key(2),
            amount: 40,
            source: DamageSource::Ram,
        };
        assert_eq!(
            engine
                .apply_ram(scope(), "1:1:2:1", first.clone(), second.clone())
                .unwrap()
                .len(),
            2
        );
        assert!(engine
            .apply_ram(scope(), "1:1:2:1", first, second)
            .unwrap()
            .is_empty());
        assert_eq!(engine.combat().get(player_key(1)).unwrap().health, 50);
        assert_eq!(engine.combat().get(player_key(2)).unwrap().health, 60);
        let output = engine.advance_tick().unwrap();
        let events = encode_events(output.tick, 20_000, output.client_events);
        assert_eq!(
            events
                .iter()
                .map(|event| event.get("source").unwrap())
                .collect::<Vec<_>>(),
            vec![&json!("ram"), &json!("ram")]
        );
    }

    #[test]
    fn ram_batch_conflict_rolls_back_every_pose_and_damage() {
        let mut engine = engine();
        advance_to_live(&mut engine);
        let before_pose = engine.body_pose(player_key(1)).unwrap();
        let first = DamageProposal {
            attacker: Some(player_key(2)),
            target: player_key(1),
            amount: 10,
            source: DamageSource::Ram,
        };
        let second = DamageProposal {
            attacker: Some(player_key(1)),
            target: player_key(2),
            amount: 20,
            source: DamageSource::Ram,
        };
        let operations = vec![
            AtomicRamDamage {
                operation_id: "ram:batch:conflict".to_owned(),
                first: first.clone(),
                second: second.clone(),
            },
            AtomicRamDamage {
                operation_id: "ram:batch:conflict".to_owned(),
                first: DamageProposal {
                    amount: 11,
                    ..first
                },
                second,
            },
        ];
        let mut corrected = before_pose;
        corrected.x += 5.0;
        assert!(matches!(
            engine.apply_ram_batch_with_poses(
                scope(),
                &operations,
                &BTreeMap::from([(player_key(1), corrected)]),
            ),
            Err(BattleError::ConflictingRamRetry)
        ));
        assert_eq!(engine.body_pose(player_key(1)), Some(before_pose));
        assert_eq!(engine.combat().get(player_key(1)).unwrap().health, 100);
        assert_eq!(engine.combat().get(player_key(2)).unwrap().health, 100);
        assert!(engine.pending_events.is_empty());
    }

    #[test]
    fn exact_ram_damage_above_generic_effect_limit_remains_lethal_and_encodable() {
        let mut engine = engine();
        advance_to_live(&mut engine);
        let operations = [AtomicRamDamage {
            operation_id: "ram:uncapped:1".to_owned(),
            first: DamageProposal {
                attacker: Some(player_key(2)),
                target: player_key(1),
                amount: 6_001,
                source: DamageSource::Ram,
            },
            second: DamageProposal {
                attacker: Some(player_key(1)),
                target: player_key(2),
                amount: u32::MAX,
                source: DamageSource::Ram,
            },
        }];
        let commits = engine
            .apply_ram_batch_with_poses(scope(), &operations, &BTreeMap::new())
            .unwrap();
        assert_eq!(commits[0].requested, 6_001);
        assert_eq!(commits[1].requested, u32::MAX);
        assert_eq!(commits[0].applied, 100);
        assert_eq!(commits[1].applied, 100);

        let output = engine.advance_tick().unwrap();
        let events = encode_events(output.tick, 20_000, output.client_events);
        let ram_events = events
            .iter()
            .filter(|event| event.get("source") == Some(&json!("ram")))
            .collect::<Vec<_>>();
        assert_eq!(ram_events.len(), 2);
        assert!(ram_events.iter().all(|event| event["damage"] == 100));
    }

    #[test]
    fn capture_timeout_and_stale_scope_are_fenced() {
        let mut capture = engine();
        advance_to_live(&mut capture);
        for _ in 0..101 * 30 {
            capture.advance_tick().unwrap();
            if capture.result().is_some() {
                break;
            }
        }
        assert_eq!(
            capture.result().unwrap().winner,
            BattleWinner::Team(Team::Two)
        );

        let mut timeout = engine();
        timeout.rules = StandardRules::new(vec![], vec![]);
        for _ in 0..TERMINAL_TICK {
            timeout.advance_tick().unwrap();
        }
        assert_eq!(timeout.result().unwrap().reason, "battle_timeout");
        assert!(matches!(
            timeout.submit_player_input(
                SimulationScope {
                    round_id: 1,
                    epoch: 1,
                },
                1,
                input(1),
            ),
            Err(BattleError::StaleScope)
        ));
    }

    #[test]
    fn typed_events_are_returned_before_the_same_tick_snapshot() {
        let mut engine = engine();
        engine.add_replication_endpoint(9);
        let first = engine.advance_tick().unwrap();
        assert!(first.emissions.is_empty());
        let full = engine.advance_tick().unwrap();
        assert_eq!(full.emissions.len(), 1);
        assert_eq!(full.emissions[0].delivery, DeliveryClass::Reliable);
        assert!(engine.advance_tick().unwrap().emissions.is_empty());
        engine.pending_events.push(BattleClientEvent::Authority);
        let second = engine.advance_tick().unwrap();
        assert_eq!(second.client_events, vec![BattleClientEvent::Authority]);
        assert_eq!(second.emissions.len(), 1);
        assert_eq!(second.emissions[0].message.kind(), "snapshot");
    }

    #[test]
    fn player_left_and_environment_damage_are_strict_combat_events() {
        let mut environment = engine();
        environment
            .apply_bot_environment_damage(scope(), 11, 10, DamageSource::Environment)
            .unwrap();
        assert!(matches!(
            environment.apply_bot_environment_damage(scope(), 11, 10, DamageSource::Fire),
            Err(BattleError::InvalidProjectileEffects)
        ));
        let output = environment.advance_tick().unwrap();
        let events = encode_events(output.tick, 1_000, output.client_events);
        assert_eq!(events[0].get("kind"), Some(&json!("health")));
        assert_eq!(events[0].get("source"), Some(&json!("client_simulation")));
        assert_eq!(events[0].get("attack_reason"), Some(&json!(5)));

        let mut retired = engine();
        retired.retire_player(scope(), 1).unwrap();
        let output = retired.advance_tick().unwrap();
        let events = encode_events(output.tick, 1_000, output.client_events);
        assert_eq!(events[0].get("kind"), Some(&json!("health")));
        assert_eq!(events[0].get("source"), Some(&json!("player_left")));
        assert_eq!(events[0].get("attack_reason"), Some(&Value::Null));
    }

    #[test]
    fn player_environment_batch_is_atomic_and_keeps_per_actor_reasons() {
        let mut rejected = engine();
        install_critical_profiles(&mut rejected);
        assert!(matches!(
            rejected.apply_environment_damage_batch(
                scope(),
                &[
                    EnvironmentDamageEffect {
                        target: player_key(1),
                        amount: 100,
                        client_simulation_reason: 5,
                    },
                    EnvironmentDamageEffect {
                        target: player_key(2),
                        amount: 10,
                        client_simulation_reason: 9,
                    },
                ],
            ),
            Err(BattleError::InvalidProjectileEffects)
        ));
        assert_eq!(rejected.combat().get(player_key(1)).unwrap().health, 100);
        assert_eq!(rejected.combat().get(player_key(2)).unwrap().health, 100);
        assert!(rejected.pending_events.is_empty());

        let mut environment = engine();
        install_critical_profiles(&mut environment);
        let commits = environment
            .apply_environment_damage_batch(
                scope(),
                &[
                    EnvironmentDamageEffect {
                        target: player_key(1),
                        amount: 100,
                        client_simulation_reason: 5,
                    },
                    EnvironmentDamageEffect {
                        target: player_key(2),
                        amount: 10,
                        client_simulation_reason: 3,
                    },
                ],
            )
            .unwrap();
        assert_eq!(commits.len(), 2);
        assert_eq!(environment.combat().get(player_key(1)).unwrap().health, 0);
        assert_eq!(environment.combat().get(player_key(2)).unwrap().health, 90);
        assert_eq!(
            environment
                .entities()
                .find(|entity| entity.key == player_key(1))
                .unwrap()
                .death_reason,
            5
        );
        let output = environment.advance_tick().unwrap();
        let combat_events = output
            .client_events
            .into_iter()
            .filter_map(|event| match event {
                BattleClientEvent::Combat(event) => Some(event),
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(combat_events.len(), 2);
        assert_eq!(combat_events[0].commit.target, player_key(1));
        assert_eq!(combat_events[0].client_simulation_reason, Some(5));
        assert!(combat_events[0].critical.is_some());
        assert_eq!(combat_events[1].commit.target, player_key(2));
        assert_eq!(combat_events[1].client_simulation_reason, Some(3));
        assert!(combat_events[1].critical.is_none());
    }

    #[test]
    fn terminal_death_reason_survives_into_entity_snapshots() {
        let mut drowned = engine();
        install_critical_profiles(&mut drowned);
        let target = bot_key(11);
        let health = drowned.combat().get(target).unwrap().health;
        drowned
            .apply_bot_environment_damage(scope(), 11, health, DamageSource::Environment)
            .unwrap();
        let view = drowned
            .entities()
            .find(|entity| entity.key == target)
            .unwrap();
        assert!(!view.combat.alive);
        assert_eq!(view.death_reason, 5);

        let mut retired = engine();
        retired.retire_player(scope(), 1).unwrap();
        let view = retired
            .entities()
            .find(|entity| entity.key == player_key(1))
            .unwrap();
        assert!(!view.combat.alive);
        assert_eq!(view.death_reason, 0);
    }

    #[test]
    fn repair_input_is_explicit_validated_and_clearable_per_vehicle() {
        let mut engine = engine();
        let key = player_key(2);
        let input = RepairInput {
            repair_factor: 0.83,
            has_big_kit: true,
        };
        assert!(matches!(
            engine.install_repair_input(key, input),
            Err(BattleError::InvalidVehicle)
        ));
        install_critical_profiles(&mut engine);
        assert!(matches!(
            engine.install_repair_input(
                key,
                RepairInput {
                    repair_factor: 0.0,
                    has_big_kit: false,
                }
            ),
            Err(BattleError::Critical(
                CriticalDamageError::InvalidRepairInput
            ))
        ));

        engine.install_repair_input(key, input).unwrap();
        assert_eq!(engine.repair_input(key), Some(input));
        engine.clear_repair_input(key).unwrap();
        assert_eq!(engine.repair_input(key), None);
        assert!(matches!(
            engine.clear_repair_input(player_key(99)),
            Err(BattleError::UnknownVehicle(_))
        ));
    }

    #[test]
    fn unavailable_repair_stays_stopped_and_hp_progress_is_snapshot_only() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        advance_to_live(&mut engine);
        let target = player_key(2);
        destroy_device(&mut engine, target, DeviceName::LeftTrackHealth);
        let damaged = engine.critical_state(target).unwrap().clone();

        let output = engine.advance_tick().unwrap();
        assert!(output.client_events.is_empty());
        assert_eq!(engine.critical_state(target).unwrap(), &damaged);

        engine
            .install_repair_input(
                target,
                RepairInput {
                    repair_factor: 100.0,
                    has_big_kit: false,
                },
            )
            .unwrap();
        let hull_before = engine.combat().get(target).unwrap().health;
        let output = engine.advance_tick().unwrap();
        assert!(output.client_events.is_empty());
        let repairing = engine.critical_state(target).unwrap();
        let track = repairing.devices[&DeviceName::LeftTrackHealth];
        assert!(track.hp > 0.0 && track.hp < 25.0);
        assert_eq!(track.condition, DeviceCondition::Destroyed);
        assert!(repairing.revision > damaged.revision);
        assert_eq!(engine.combat().get(target).unwrap().health, hull_before);
        let (snapshot, _) = engine.client_critical_snapshot(target).unwrap();
        assert!(snapshot.events.is_empty());
        assert_eq!(snapshot.devices[0].hp, track.hp);

        engine.clear_repair_input(target).unwrap();
        let stopped = engine.critical_state(target).unwrap().clone();
        let output = engine.advance_tick().unwrap();
        assert!(output.client_events.is_empty());
        assert_eq!(engine.critical_state(target).unwrap(), &stopped);
    }

    #[test]
    fn completed_track_repair_clears_immobilisation_holder_without_damage_event() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        advance_to_live(&mut engine);
        let target = player_key(2);
        destroy_device(&mut engine, target, DeviceName::LeftTrackHealth);
        engine
            .statistics
            .mark_track_immobilised(target, player_key(1));
        let mut stale_proof = engine.statistics.clone();
        assert_eq!(
            stale_proof
                .record_damage(Some(bot_key(11)), target, 1, true, None, &BTreeSet::new(),)
                .len(),
            1
        );
        engine
            .install_repair_input(
                target,
                RepairInput {
                    repair_factor: 100.0,
                    has_big_kit: false,
                },
            )
            .unwrap();

        for _ in 0..6 {
            let output = engine.advance_tick().unwrap();
            assert!(output.client_events.is_empty());
            if !tracks_destroyed(engine.critical_state(target).unwrap()) {
                break;
            }
        }

        let track = engine.critical_state(target).unwrap().devices[&DeviceName::LeftTrackHealth];
        assert_eq!(track.hp, 25.0);
        assert_eq!(track.condition, DeviceCondition::Critical);
        let canonical_revision = engine.critical_state(target).unwrap().revision;
        let (snapshot, revision) = engine.client_critical_snapshot(target).unwrap();
        assert!(snapshot.events.is_empty());
        assert!(!snapshot
            .destroyed
            .contains(&ClientCriticalDeviceName::LeftTrackHealth));
        assert!(matches!(
            revision,
            ClientCriticalRevision::Player {
                revision,
                base_revision,
                ack_seq: 0,
            } if revision == base_revision && revision == canonical_revision
        ));
        assert!(engine
            .statistics
            .record_damage(Some(bot_key(11)), target, 1, true, None, &BTreeSet::new(),)
            .is_empty());
    }

    #[test]
    fn same_tick_repair_precedes_fire_damage_and_prevents_stale_track_assist() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        advance_to_live(&mut engine);
        let target = player_key(2);
        destroy_device(&mut engine, target, DeviceName::LeftTrackHealth);
        ignite_vehicle(&mut engine, target, bot_key(11), 15_000);
        engine
            .statistics
            .mark_track_immobilised(target, player_key(1));
        engine
            .install_repair_input(
                target,
                RepairInput {
                    // This remains below the cap after 29 slices and crosses
                    // it on the same one-second boundary as the fire tick.
                    repair_factor: 5.001,
                    has_big_kit: false,
                },
            )
            .unwrap();

        let mut terminal = None;
        for _ in 0..30 {
            terminal = Some(engine.advance_tick().unwrap());
        }
        let output = terminal.unwrap();
        assert_eq!(
            engine.critical_state(target).unwrap().devices[&DeviceName::LeftTrackHealth].condition,
            DeviceCondition::Critical
        );
        assert!(output.client_events.iter().any(|event| matches!(
            event,
            BattleClientEvent::Combat(combat) if combat.commit.source == DamageSource::Fire
        )));
        assert!(!output
            .client_events
            .iter()
            .any(|event| matches!(event, BattleClientEvent::Assist(_))));
        assert_eq!(
            engine
                .statistics()
                .row(player_key(1))
                .unwrap()
                .damage_assisted_track,
            0
        );
    }

    #[test]
    fn death_and_round_finish_drop_repair_runtime() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        let input = RepairInput {
            repair_factor: 0.57,
            has_big_kit: false,
        };
        engine.install_repair_input(player_key(1), input).unwrap();
        engine.install_repair_input(player_key(2), input).unwrap();

        engine.retire_player(scope(), 2).unwrap();
        assert_eq!(engine.repair_input(player_key(2)), None);
        assert_eq!(engine.repair_input(player_key(1)), Some(input));

        engine.oracle_failed("oracle_timeout").unwrap();
        assert_eq!(engine.repair_input(player_key(1)), None);
    }

    #[test]
    fn direct_native_spotting_drives_radio_assist_with_typed_vehicle_keys() {
        let mut engine = BattleEngine::new(
            scope(),
            vec![
                vehicle(player_key(1), Team::One, 100.0, 0.0),
                vehicle(bot_key(1), Team::One, 90.0, 0.0),
                vehicle(bot_key(11), Team::One, 80.0, 0.0),
                vehicle(player_key(2), Team::Two, 0.0, 0.0),
            ],
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
        .unwrap();
        let target = player_key(2);
        engine
            .replace_direct_spotting(&BTreeMap::from([
                (player_key(1), BTreeSet::from([target])),
                (bot_key(1), BTreeSet::from([target])),
            ]))
            .unwrap();

        let commits = engine
            .combat
            .apply_atomic(&[DamageProposal {
                attacker: Some(bot_key(11)),
                target,
                amount: 10,
                source: DamageSource::Shot,
            }])
            .unwrap();
        engine.publish_damage_commits(&commits, None, None, None);

        assert_eq!(
            engine
                .statistics()
                .row(player_key(1))
                .unwrap()
                .damage_assisted_radio,
            10
        );
        assert_eq!(
            engine
                .statistics()
                .row(bot_key(1))
                .unwrap()
                .damage_assisted_radio,
            10
        );
        assert_eq!(
            engine.statistics().radio_reporters(target),
            BTreeSet::from([player_key(1), bot_key(1)])
        );

        engine.replace_direct_spotting(&BTreeMap::new()).unwrap();
        assert!(engine.statistics().radio_reporters(target).is_empty());
        assert_eq!(engine.statistics().row(player_key(1)).unwrap().spotted, 1);
        assert_eq!(engine.statistics().row(bot_key(1)).unwrap().spotted, 1);
    }

    #[test]
    fn oracle_loss_finishes_fail_closed() {
        let mut engine = engine();
        engine.oracle_failed("oracle_timeout").unwrap();
        assert_eq!(engine.result().unwrap().winner, BattleWinner::Draw);
        assert_eq!(engine.result().unwrap().reason, "oracle_timeout");
    }

    #[test]
    fn post_finish_projectile_cleanup_accepts_only_no_damage_current_scope() {
        let mut engine = engine();
        install_critical_profiles(&mut engine);
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        engine
            .commit_player_launch(scope(), 1, 1, launch(), 30)
            .unwrap();
        engine.retire_player(scope(), 2).unwrap();
        assert!(engine.result().is_some());
        assert_eq!(engine.projectiles().active().len(), 1);

        let cleanup = ProjectileTerminal {
            resolution: ProjectileResolution {
                round_id: 1,
                authority_epoch: 2,
                projectile_id: "1:p:1:1".to_owned(),
                base_checked_ms: 0,
                outcome: ProjectileOutcome::Miss,
                resolved_time_ms: 100,
                checked_distance: 100.0,
                piercing_loss: 0.0,
                penetration_factor: 1.0,
                impact: None,
            },
            direct: None,
            splash: Vec::new(),
            destructibles: Vec::new(),
        };
        let stale_scope = SimulationScope {
            round_id: scope().round_id,
            epoch: scope().epoch - 1,
        };
        assert!(matches!(
            engine.resolve_projectile_cleanup_after_finish(stale_scope, cleanup.clone(), 200),
            Err(BattleError::StaleScope)
        ));

        let damage = ProjectileDamageEffect {
            damage: DamageProposal {
                attacker: Some(player_key(1)),
                target: player_key(2),
                amount: 1,
                source: DamageSource::Shot,
            },
            shot_result: 2,
            potential_damage: 1,
            critical: None,
            stun_end_server_time_ms: None,
        };
        let mut direct = cleanup.clone();
        direct.direct = Some(damage.clone());
        assert!(matches!(
            engine.resolve_projectile_cleanup_after_finish(scope(), direct, 200),
            Err(BattleError::InvalidProjectileEffects)
        ));
        let mut splash = cleanup.clone();
        splash.splash.push(damage);
        assert!(matches!(
            engine.resolve_projectile_cleanup_after_finish(scope(), splash, 200),
            Err(BattleError::InvalidProjectileEffects)
        ));
        assert_eq!(engine.projectiles().active().len(), 1);

        assert!(matches!(
            engine
                .resolve_projectile_cleanup_after_finish(scope(), cleanup, 200)
                .unwrap(),
            ResolutionAdmission::Applied { .. }
        ));
        assert!(engine.projectiles().active().is_empty());
    }

    #[test]
    fn post_finish_fire_intent_cleanup_preserves_scope_fence() {
        let mut engine = engine();
        engine.submit_player_input(scope(), 1, input(1)).unwrap();
        advance_to_live(&mut engine);
        engine
            .submit_fire_intent(
                scope(),
                1,
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 1,
                    shell_index: 0,
                },
                20,
            )
            .unwrap();
        engine.retire_player(scope(), 2).unwrap();
        assert!(engine.result().is_some());
        assert!(engine.player_fire_intent_pending(1).unwrap());

        let stale_scope = SimulationScope {
            round_id: scope().round_id,
            epoch: scope().epoch - 1,
        };
        assert!(matches!(
            engine.reject_player_fire_intent(stale_scope, 1, 1, "battle_finished"),
            Err(BattleError::StaleScope)
        ));
        assert!(engine.player_fire_intent_pending(1).unwrap());

        engine
            .reject_player_fire_intent(scope(), 1, 1, "battle_finished")
            .unwrap();
        assert!(!engine.player_fire_intent_pending(1).unwrap());
    }
}
