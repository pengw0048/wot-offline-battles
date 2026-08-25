//! Deterministic #1513 module, crew, fire, and repair authority.
//!
//! Native code supplies collision/material facts and already-transformed
//! interior ray/cone intersections. This module owns every saving throw,
//! module-health transition, crew knockout, fire, ammo-rack, and repair
//! verdict. All random samples are explicit inputs, so a replay or retry never
//! draws a different result.

use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

pub const DAMAGE_RANDOMIZATION: f64 = 0.25;
pub const MIN_MODULE_DAMAGE_FACTOR: f64 = 1.0 - DAMAGE_RANDOMIZATION;
pub const MAX_MODULE_DAMAGE_FACTOR: f64 = 1.0 + DAMAGE_RANDOMIZATION;
pub const MAX_CRITICAL_DEVICE_HP: f64 = 1_000_000_000.0;
pub const CRITICAL_HP_FRACTION: f64 = 0.5;
pub const BASE_TRACK_REPAIR_SECONDS: f64 = 10.0;
pub const BASE_MODULE_REPAIR_SECONDS: f64 = 18.0;
pub const REPAIR_SKILL_SPEEDUP: f64 = 1.0;
pub const FIRE_DAMAGE_FRACTION_PER_SECOND: f64 = 0.05;
pub const FIRE_DURATION_MS: u64 = 10_000;
pub const FIRE_TICK_MICROS: u64 = 1_000_000;
pub const DAMAGED_MODULE_EFFICIENCY: f64 = 0.5;
pub const DESTROYED_MODULE_EFFICIENCY: f64 = 0.25;
pub const MIN_VISION_FACTOR: f64 = 0.5;
pub const CREW_FACTOR_BASE: f64 = 0.57;
pub const CREW_FACTOR_SLOPE: f64 = 0.43;
pub const COMMANDER_ADDITION_RATIO: f64 = 10.0;
pub const DEAD_EYE_BONUS: f64 = 0.03;
pub const HE_CONE_COS: f64 = 0.707_106_781_186_547_6;
pub const HE_CONE_EDGE_FACTOR: f64 = 1.414_213_562_373_095_1;
pub const MAX_NATIVE_CRITICAL_LAYERS: usize = 128;
pub const MAX_INTERNAL_CRITICAL_HITS: usize = 64;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum DeviceName {
    #[serde(rename = "ammoBayHealth")]
    AmmoBayHealth,
    #[serde(rename = "engineHealth")]
    EngineHealth,
    #[serde(rename = "fuelTankHealth")]
    FuelTankHealth,
    #[serde(rename = "gunHealth")]
    GunHealth,
    #[serde(rename = "leftTrackHealth")]
    LeftTrackHealth,
    #[serde(rename = "radioHealth")]
    RadioHealth,
    #[serde(rename = "rightTrackHealth")]
    RightTrackHealth,
    #[serde(rename = "surveyingDeviceHealth")]
    SurveyingDeviceHealth,
    #[serde(rename = "turretRotatorHealth")]
    TurretRotatorHealth,
}

pub const ALL_DEVICE_NAMES: [DeviceName; 9] = [
    DeviceName::AmmoBayHealth,
    DeviceName::EngineHealth,
    DeviceName::FuelTankHealth,
    DeviceName::GunHealth,
    DeviceName::LeftTrackHealth,
    DeviceName::RadioHealth,
    DeviceName::RightTrackHealth,
    DeviceName::SurveyingDeviceHealth,
    DeviceName::TurretRotatorHealth,
];

impl DeviceName {
    pub const fn wire_name(self) -> &'static str {
        match self {
            Self::AmmoBayHealth => "ammoBayHealth",
            Self::EngineHealth => "engineHealth",
            Self::FuelTankHealth => "fuelTankHealth",
            Self::GunHealth => "gunHealth",
            Self::LeftTrackHealth => "leftTrackHealth",
            Self::RadioHealth => "radioHealth",
            Self::RightTrackHealth => "rightTrackHealth",
            Self::SurveyingDeviceHealth => "surveyingDeviceHealth",
            Self::TurretRotatorHealth => "turretRotatorHealth",
        }
    }

    pub const fn is_track(self) -> bool {
        matches!(self, Self::LeftTrackHealth | Self::RightTrackHealth)
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum CrewName {
    #[serde(rename = "commander")]
    Commander,
    #[serde(rename = "driver")]
    Driver,
    #[serde(rename = "gunner1")]
    Gunner1,
    #[serde(rename = "gunner2")]
    Gunner2,
    #[serde(rename = "loader1")]
    Loader1,
    #[serde(rename = "loader2")]
    Loader2,
    #[serde(rename = "radioman1")]
    Radioman1,
    #[serde(rename = "radioman2")]
    Radioman2,
}

impl CrewName {
    pub const fn wire_name(self) -> &'static str {
        match self {
            Self::Commander => "commander",
            Self::Driver => "driver",
            Self::Gunner1 => "gunner1",
            Self::Gunner2 => "gunner2",
            Self::Loader1 => "loader1",
            Self::Loader2 => "loader2",
            Self::Radioman1 => "radioman1",
            Self::Radioman2 => "radioman2",
        }
    }

    pub const fn base_role(self) -> CrewRole {
        match self {
            Self::Commander => CrewRole::Commander,
            Self::Driver => CrewRole::Driver,
            Self::Gunner1 | Self::Gunner2 => CrewRole::Gunner,
            Self::Loader1 | Self::Loader2 => CrewRole::Loader,
            Self::Radioman1 | Self::Radioman2 => CrewRole::Radioman,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum CrewRole {
    Commander,
    Driver,
    Gunner,
    Loader,
    Radioman,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(tag = "kind", content = "name", rename_all = "snake_case")]
pub enum CriticalTarget {
    Device(DeviceName),
    Crew(CrewName),
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CriticalShellKind {
    ArmorPiercing,
    ArmorPiercingCr,
    ArmorPiercingHe,
    HollowCharge,
    HighExplosive,
}

impl CriticalShellKind {
    fn receives_dead_eye(self) -> bool {
        matches!(
            self,
            Self::ArmorPiercing | Self::ArmorPiercingCr | Self::HollowCharge
        )
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalShell {
    pub kind: CriticalShellKind,
    /// Exact #1513 `shell.damage[1]`. `None` preserves the Python fallback to
    /// already-resolved hull damage.
    pub module_damage: Option<f64>,
}

impl CriticalShell {
    fn validate(self) -> Result<(), CriticalDamageError> {
        if self.module_damage.is_some_and(|value| {
            !value.is_finite() || !(0.0..=MAX_CRITICAL_DEVICE_HP).contains(&value)
        }) {
            return Err(CriticalDamageError::InvalidShell);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DeviceProfile {
    pub max_hp: f64,
    pub regen_hp: f64,
}

impl DeviceProfile {
    fn validate(self, name: DeviceName) -> Result<(), CriticalDamageError> {
        if !self.max_hp.is_finite()
            || self.max_hp < 1.0
            || self.max_hp > MAX_CRITICAL_DEVICE_HP
            || !self.regen_hp.is_finite()
            || self.regen_hp <= 0.0
            || self.regen_hp > self.max_hp
        {
            return Err(CriticalDamageError::InvalidDeviceProfile { name });
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CrewMemberProfile {
    pub name: CrewName,
    pub roles: BTreeSet<CrewRole>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalProfile {
    pub devices: BTreeMap<DeviceName, DeviceProfile>,
    pub crew: Vec<CrewMemberProfile>,
    pub engine_fire_starting_chance: f64,
    pub repair_speed_factor: f64,
}

impl CriticalProfile {
    pub fn validate(&self) -> Result<(), CriticalDamageError> {
        if self.devices.len() != ALL_DEVICE_NAMES.len() {
            return Err(CriticalDamageError::IncompleteDeviceProfile);
        }
        for name in ALL_DEVICE_NAMES {
            self.devices
                .get(&name)
                .ok_or(CriticalDamageError::IncompleteDeviceProfile)?
                .validate(name)?;
        }
        if self.crew.is_empty() || self.crew.len() > 8 {
            return Err(CriticalDamageError::InvalidCrewProfile);
        }
        let mut names = BTreeSet::new();
        for member in &self.crew {
            if !names.insert(member.name)
                || member.roles.is_empty()
                || !member.roles.contains(&member.name.base_role())
            {
                return Err(CriticalDamageError::InvalidCrewProfile);
            }
        }
        if !self.engine_fire_starting_chance.is_finite()
            || !(0.0..=1.0).contains(&self.engine_fire_starting_chance)
            || !self.repair_speed_factor.is_finite()
            || self.repair_speed_factor <= 0.0
        {
            return Err(CriticalDamageError::InvalidProfileFactor);
        }
        Ok(())
    }

    fn device(&self, name: DeviceName) -> &DeviceProfile {
        // A validated profile always contains the complete fixed set.
        &self.devices[&name]
    }

    pub fn crew_roster(&self) -> BTreeSet<CrewName> {
        self.crew.iter().map(|member| member.name).collect()
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DeviceCondition {
    Normal,
    Critical,
    Destroyed,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DeviceState {
    pub hp: f64,
    pub condition: DeviceCondition,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalState {
    pub revision: u64,
    /// Sparse touched-device map. An absent device is at full health/normal.
    pub devices: BTreeMap<DeviceName, DeviceState>,
    pub crew_ko: BTreeSet<CrewName>,
    pub on_fire: bool,
    pub ammo_rack_death: bool,
    /// Internal timing fields are authoritative but intentionally absent from
    /// the public critical payload and do not advance its revision alone.
    pub fire_started_ms: Option<u64>,
    pub fire_timer_micros: u64,
}

impl Default for CriticalState {
    fn default() -> Self {
        Self {
            revision: 0,
            devices: BTreeMap::new(),
            crew_ko: BTreeSet::new(),
            on_fire: false,
            ammo_rack_death: false,
            fire_started_ms: None,
            fire_timer_micros: 0,
        }
    }
}

impl CriticalState {
    pub fn validate(&self, profile: &CriticalProfile) -> Result<(), CriticalDamageError> {
        profile.validate()?;
        for (name, state) in &self.devices {
            let maximum = profile.device(*name).max_hp;
            if !state.hp.is_finite()
                || state.hp < 0.0
                || state.hp > maximum
                || (state.condition == DeviceCondition::Destroyed && state.hp >= maximum)
                || (state.condition != DeviceCondition::Destroyed && state.hp <= 0.0)
                || (state.condition == DeviceCondition::Normal
                    && state.hp <= maximum * CRITICAL_HP_FRACTION)
            {
                return Err(CriticalDamageError::InvalidDeviceState { name: *name });
            }
        }
        if !self.crew_ko.is_subset(&profile.crew_roster()) {
            return Err(CriticalDamageError::InvalidCrewState);
        }
        if !self.on_fire && self.fire_started_ms.is_some() {
            return Err(CriticalDamageError::InvalidFireState);
        }
        Ok(())
    }

    pub fn device_state(&self, profile: &CriticalProfile, name: DeviceName) -> DeviceState {
        self.devices.get(&name).copied().unwrap_or(DeviceState {
            hp: profile.device(name).max_hp,
            condition: DeviceCondition::Normal,
        })
    }

    fn public_eq(&self, other: &Self) -> bool {
        self.devices == other.devices
            && self.crew_ko == other.crew_ko
            && self.on_fire == other.on_fire
            && self.ammo_rack_death == other.ammo_rack_death
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalLayer {
    pub distance_m: f64,
    pub armor_mm: f64,
    pub vehicle_damage_factor: f64,
    pub target: Option<CriticalTarget>,
    pub chance_to_hit_by_projectile: Option<f64>,
    pub chance_to_hit_by_explosion: Option<f64>,
}

impl CriticalLayer {
    pub fn is_structural(self) -> bool {
        self.vehicle_damage_factor != 0.0 && self.armor_mm > 0.0
    }

    fn validate(self) -> Result<(), CriticalDamageError> {
        if !self.distance_m.is_finite()
            || self.distance_m < 0.0
            || !self.armor_mm.is_finite()
            || self.armor_mm < 0.0
            || !self.vehicle_damage_factor.is_finite()
            || self.vehicle_damage_factor < 0.0
            || !valid_optional_chance(self.chance_to_hit_by_projectile)
            || !valid_optional_chance(self.chance_to_hit_by_explosion)
        {
            return Err(CriticalDamageError::InvalidCriticalLayer);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct InternalCriticalHit {
    /// Direct-ray hits use metres from the original native trace start so the
    /// second-structural-plate filter remains exact. HE-cone hits use axial
    /// metres from the burst; explosion resolution disables distance filters.
    pub distance_m: f64,
    pub target: CriticalTarget,
}

impl InternalCriticalHit {
    fn validate(self) -> Result<(), CriticalDamageError> {
        if self.distance_m.is_finite() && self.distance_m >= 0.0 {
            Ok(())
        } else {
            Err(CriticalDamageError::InvalidInternalHit)
        }
    }
}

/// `internal_hits=None` means the validated per-vehicle layout was unavailable
/// and must fail closed. `Some([])` means geometry was available and the path
/// crossed no internal target.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalTrace {
    pub native_layers: Vec<CriticalLayer>,
    pub internal_hits: Option<Vec<InternalCriticalHit>>,
}

impl CriticalTrace {
    fn validate(&self, profile: &CriticalProfile) -> Result<(), CriticalDamageError> {
        if self.native_layers.len() > MAX_NATIVE_CRITICAL_LAYERS
            || self
                .internal_hits
                .as_ref()
                .is_some_and(|hits| hits.len() > MAX_INTERNAL_CRITICAL_HITS)
        {
            return Err(CriticalDamageError::TraceCapacity);
        }
        let roster = profile.crew_roster();
        for layer in &self.native_layers {
            layer.validate()?;
            validate_target(layer.target, &roster)?;
        }
        if let Some(hits) = &self.internal_hits {
            for hit in hits {
                hit.validate()?;
                validate_target(Some(hit.target), &roster)?;
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CriticalCause {
    Shot,
    Explosion,
    Repair,
    Equipment,
    Fire,
    Drowning,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CrewCondition {
    Normal,
    Destroyed,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CriticalEvent {
    Device {
        name: DeviceName,
        old_state: DeviceCondition,
        state: DeviceCondition,
        cause: CriticalCause,
    },
    Crew {
        name: CrewName,
        state: CrewCondition,
        cause: CriticalCause,
    },
    Fire {
        state: bool,
        cause: CriticalCause,
    },
    AmmoRack {
        state: DeviceCondition,
        cause: CriticalCause,
    },
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DeviceRecord {
    pub name: DeviceName,
    pub hp: f64,
    pub max_hp: f64,
    pub state: DeviceCondition,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalPayload {
    pub revision: u64,
    pub base_revision: u64,
    pub devices: Vec<DeviceRecord>,
    pub destroyed: Vec<DeviceName>,
    pub crew_ko: Vec<CrewName>,
    pub crew_roster: Vec<CrewName>,
    pub fire: bool,
    pub ammo_rack_death: bool,
    pub events: Vec<CriticalEvent>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CriticalSamples {
    /// One shell-owned +/-25% module-damage factor.
    pub module_damage_factor: f64,
    /// At most one saving-throw sample is consumed for each logical target.
    pub target_rolls: BTreeMap<CriticalTarget, f64>,
    /// Consumed only after a successful engine HP loss while not already on
    /// fire and with a positive engine fire chance.
    pub engine_fire_roll: Option<f64>,
}

impl CriticalSamples {
    fn validate(&self) -> Result<(), CriticalDamageError> {
        if !self.module_damage_factor.is_finite()
            || !(MIN_MODULE_DAMAGE_FACTOR..=MAX_MODULE_DAMAGE_FACTOR)
                .contains(&self.module_damage_factor)
            || self.target_rolls.values().any(|value| !valid_roll(*value))
            || self
                .engine_fire_roll
                .is_some_and(|value| !valid_roll(value))
        {
            return Err(CriticalDamageError::InvalidRandomSample);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CriticalConfig {
    pub module_damage: bool,
    pub crew_damage: bool,
    pub internal_module_damage: bool,
    /// The old module-test bench still destroys the rack module but suppresses
    /// the otherwise unconditional hull kill.
    pub suppress_ammo_rack_death: bool,
}

impl Default for CriticalConfig {
    fn default() -> Self {
        Self {
            module_damage: true,
            crew_damage: true,
            internal_module_damage: true,
            suppress_ammo_rack_death: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StrikeInput {
    pub hull_damage: u32,
    pub current_hull_health: u32,
    pub shell: CriticalShell,
    pub penetrated: Option<bool>,
    pub by_explosion: bool,
    pub dead_eye: bool,
    pub distance_filters: bool,
    pub now_ms: Option<u64>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RepairTickInput {
    pub dt_micros: u64,
    pub vehicle_alive: bool,
    pub repair_skill_percent: f64,
    pub has_big_repair_kit: bool,
    /// Exact live #1513 repair factor. When present it replaces the percentage
    /// curve and remains normalized to the port's fully-trained speed.
    pub repair_factor: Option<f64>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FireTickInput {
    pub dt_micros: u64,
    pub now_ms: Option<u64>,
    pub current_hull_health: u32,
    pub max_hull_health: u32,
    pub module_test_mode: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CriticalMutation {
    pub base_revision: u64,
    pub revision: u64,
    pub hull_damage: u32,
    pub payload: Option<CriticalPayload>,
    before: CriticalState,
    after: CriticalState,
}

impl CriticalMutation {
    pub fn state(&self) -> &CriticalState {
        &self.after
    }

    pub fn changes_internal_state(&self) -> bool {
        self.before != self.after
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CommitDisposition {
    Applied { revision: u64 },
    ExactRetry { revision: u64 },
    NoChange { revision: u64 },
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CriticalStat {
    Reload,
    Dispersion,
    AimTime,
    TurretSpeed,
    Mobility,
    Vision,
    Signal,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum CriticalDamageError {
    #[error("critical profile is missing one or more #1513 device HP pools")]
    IncompleteDeviceProfile,
    #[error("device profile for {name:?} is invalid")]
    InvalidDeviceProfile { name: DeviceName },
    #[error("crew profile is invalid")]
    InvalidCrewProfile,
    #[error("critical profile factor is invalid")]
    InvalidProfileFactor,
    #[error("device state for {name:?} is invalid")]
    InvalidDeviceState { name: DeviceName },
    #[error("crew state contains a member outside the descriptor roster")]
    InvalidCrewState,
    #[error("fire timing state is invalid")]
    InvalidFireState,
    #[error("shell critical descriptor is invalid")]
    InvalidShell,
    #[error("critical trace exceeds its capacity")]
    TraceCapacity,
    #[error("native critical layer is invalid")]
    InvalidCriticalLayer,
    #[error("internal critical hit is invalid")]
    InvalidInternalHit,
    #[error("critical target is not valid for the vehicle profile")]
    InvalidTarget,
    #[error("deterministic random sample is invalid")]
    InvalidRandomSample,
    #[error("saving-throw sample is missing for {target:?}")]
    MissingTargetRoll { target: CriticalTarget },
    #[error("engine fire sample is required")]
    MissingEngineFireRoll,
    #[error("repair input is invalid")]
    InvalidRepairInput,
    #[error("device action names an untouched or unavailable module")]
    DeviceNotRepairable,
    #[error("crew action names a fit or unavailable crew member")]
    CrewNotRestorable,
    #[error("critical revision is exhausted")]
    RevisionExhausted,
    #[error("fire timer is exhausted")]
    FireTimerExhausted,
    #[error("ammo-rack kill damage exceeds u32")]
    AmmoRackDamageOverflow,
    #[error("critical proposal base state no longer matches authority state")]
    RevisionConflict,
}

#[derive(Clone, Debug)]
pub struct CriticalLedger {
    profile: CriticalProfile,
    state: CriticalState,
    last_commit: Option<CriticalMutation>,
}

impl CriticalLedger {
    pub fn new(
        profile: CriticalProfile,
        state: CriticalState,
    ) -> Result<Self, CriticalDamageError> {
        state.validate(&profile)?;
        Ok(Self {
            profile,
            state,
            last_commit: None,
        })
    }

    pub fn profile(&self) -> &CriticalProfile {
        &self.profile
    }

    pub fn state(&self) -> &CriticalState {
        &self.state
    }

    /// Build a durable full-state payload for snapshot recovery. Transition
    /// events belong only to the ordered mutation event and are never replayed
    /// from a snapshot baseline.
    pub fn snapshot_payload(&self) -> CriticalPayload {
        let mut devices = self
            .state
            .devices
            .iter()
            .map(|(&name, &state)| DeviceRecord {
                name,
                hp: state.hp,
                max_hp: self.profile.device(name).max_hp,
                state: state.condition,
            })
            .collect::<Vec<_>>();
        devices.sort_by_key(|device| device.name);
        CriticalPayload {
            revision: self.state.revision,
            base_revision: self.state.revision,
            destroyed: devices
                .iter()
                .filter(|device| device.state == DeviceCondition::Destroyed)
                .map(|device| device.name)
                .collect(),
            devices,
            crew_ko: self.state.crew_ko.iter().copied().collect(),
            crew_roster: self.profile.crew_roster().into_iter().collect(),
            fire: self.state.on_fire,
            ammo_rack_death: self.state.ammo_rack_death,
            events: Vec::new(),
        }
    }

    pub fn propose_strike(
        &self,
        trace: &CriticalTrace,
        input: StrikeInput,
        samples: &CriticalSamples,
        config: CriticalConfig,
    ) -> Result<CriticalMutation, CriticalDamageError> {
        propose_strike(&self.profile, &self.state, trace, input, samples, config)
    }

    pub fn commit(
        &mut self,
        mutation: CriticalMutation,
    ) -> Result<CommitDisposition, CriticalDamageError> {
        if self.state == mutation.before {
            if mutation.before == mutation.after {
                return Ok(CommitDisposition::NoChange {
                    revision: self.state.revision,
                });
            }
            self.state = mutation.after.clone();
            let revision = self.state.revision;
            self.last_commit = Some(mutation);
            return Ok(CommitDisposition::Applied { revision });
        }
        if self.last_commit.as_ref() == Some(&mutation) && self.state == mutation.after {
            return Ok(CommitDisposition::ExactRetry {
                revision: self.state.revision,
            });
        }
        Err(CriticalDamageError::RevisionConflict)
    }
}

#[derive(Clone, Copy)]
struct ScoredHit {
    distance_m: f64,
    target: CriticalTarget,
    projectile_chance: Option<f64>,
    explosion_chance: Option<f64>,
    stable_order: usize,
}

/// Pure, detached equivalent of Python `propose_direct`/`propose_explosion`.
pub fn propose_strike(
    profile: &CriticalProfile,
    state: &CriticalState,
    trace: &CriticalTrace,
    input: StrikeInput,
    samples: &CriticalSamples,
    config: CriticalConfig,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    trace.validate(profile)?;
    input.shell.validate()?;
    samples.validate()?;
    if !config.module_damage {
        return finalize_mutation(
            profile,
            state,
            state.clone(),
            input.hull_damage,
            if input.by_explosion {
                CriticalCause::Explosion
            } else {
                CriticalCause::Shot
            },
        );
    }

    let cause = if input.by_explosion {
        CriticalCause::Explosion
    } else {
        CriticalCause::Shot
    };
    let module_damage = match input.shell.module_damage {
        Some(value) => value * samples.module_damage_factor,
        None => f64::from(input.hull_damage),
    };
    let dead_eye_bonus = if input.dead_eye && input.shell.kind.receives_dead_eye() {
        DEAD_EYE_BONUS
    } else {
        0.0
    };

    let exit_distance = if input.distance_filters {
        trace
            .native_layers
            .iter()
            .filter(|layer| layer.is_structural())
            .map(|layer| layer.distance_m)
            .collect::<Vec<_>>()
            .tap_mut(|values| {
                values.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal))
            })
            .get(1)
            .copied()
    } else {
        None
    };
    let stopping_distance = if input.distance_filters && input.penetrated == Some(false) {
        trace
            .native_layers
            .iter()
            .filter(|layer| layer.is_structural())
            .map(|layer| layer.distance_m)
            .min_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal))
            .or(Some(1.0e9))
    } else {
        None
    };

    let mut scored = Vec::new();
    for (stable_order, layer) in trace.native_layers.iter().enumerate() {
        if let Some(target) = layer.target {
            scored.push(ScoredHit {
                distance_m: layer.distance_m,
                target,
                projectile_chance: layer.chance_to_hit_by_projectile,
                explosion_chance: layer.chance_to_hit_by_explosion,
                stable_order,
            });
        }
    }
    if config.internal_module_damage
        && (trace.internal_hits.is_some() || input.penetrated != Some(false))
    {
        if let Some(internal_hits) = &trace.internal_hits {
            let offset = trace.native_layers.len();
            for (index, hit) in internal_hits.iter().enumerate() {
                scored.push(ScoredHit {
                    distance_m: hit.distance_m,
                    target: hit.target,
                    projectile_chance: None,
                    explosion_chance: None,
                    stable_order: offset + index,
                });
            }
        }
    }
    scored.sort_by(|left, right| {
        left.distance_m
            .partial_cmp(&right.distance_m)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.stable_order.cmp(&right.stable_order))
    });

    let mut after = state.clone();
    let mut hull_damage = input.hull_damage;
    let mut rolled_targets = BTreeSet::new();
    for hit in scored {
        if stopping_distance.is_some_and(|distance| hit.distance_m > distance)
            || exit_distance.is_some_and(|distance| hit.distance_m > distance)
        {
            continue;
        }
        if !rolled_targets.insert(hit.target) {
            continue;
        }
        if matches!(hit.target, CriticalTarget::Crew(_)) && !config.crew_damage {
            continue;
        }
        let chance = hit_chance(hit, input.by_explosion, dead_eye_bonus);
        let roll = samples
            .target_rolls
            .get(&hit.target)
            .copied()
            .ok_or(CriticalDamageError::MissingTargetRoll { target: hit.target })?;
        if roll >= chance {
            continue;
        }

        match hit.target {
            CriticalTarget::Crew(name) => {
                after.crew_ko.insert(name);
            }
            CriticalTarget::Device(name) => {
                let maximum = profile.device(name).max_hp;
                let previous = after.device_state(profile, name);
                let current_hp = (previous.hp - module_damage).max(0.0);
                let condition = if current_hp <= 0.0 {
                    DeviceCondition::Destroyed
                } else if previous.condition == DeviceCondition::Critical
                    || current_hp <= maximum * CRITICAL_HP_FRACTION
                {
                    DeviceCondition::Critical
                } else {
                    DeviceCondition::Normal
                };
                after.devices.insert(
                    name,
                    DeviceState {
                        hp: current_hp,
                        condition,
                    },
                );

                if name == DeviceName::AmmoBayHealth && current_hp <= 0.0 {
                    if !config.suppress_ammo_rack_death {
                        hull_damage = input
                            .current_hull_health
                            .checked_add(10)
                            .ok_or(CriticalDamageError::AmmoRackDamageOverflow)?;
                        after.ammo_rack_death = true;
                        break;
                    }
                }

                let hp_lost = current_hp < previous.hp;
                if hp_lost
                    && name == DeviceName::FuelTankHealth
                    && current_hp <= 0.0
                    && !after.on_fire
                {
                    ignite(&mut after, input.now_ms);
                } else if hp_lost
                    && name == DeviceName::EngineHealth
                    && !after.on_fire
                    && profile.engine_fire_starting_chance > 0.0
                {
                    let fire_roll = samples
                        .engine_fire_roll
                        .ok_or(CriticalDamageError::MissingEngineFireRoll)?;
                    if fire_roll < profile.engine_fire_starting_chance {
                        ignite(&mut after, input.now_ms);
                    }
                }
            }
        }
    }

    finalize_mutation(profile, state, after, hull_damage, cause)
}

/// Deterministic equipment/module HP drain. It bypasses saving throws and,
/// exactly like Python, does not ignite fuel/engine or detonate an ammo rack.
pub fn propose_device_damage_over_time(
    profile: &CriticalProfile,
    state: &CriticalState,
    current_hull_health: u32,
    name: DeviceName,
    amount: f64,
    cause: CriticalCause,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    if !amount.is_finite() || amount <= 0.0 {
        return Err(CriticalDamageError::InvalidShell);
    }
    if current_hull_health == 0 {
        return finalize_mutation(profile, state, state.clone(), 0, cause);
    }
    let mut after = state.clone();
    let maximum = profile.device(name).max_hp;
    let previous = after.device_state(profile, name);
    if previous.hp <= 0.0 {
        return finalize_mutation(profile, state, after, 0, cause);
    }
    let hp = (previous.hp - amount).max(0.0);
    let condition = if hp <= 0.0 {
        DeviceCondition::Destroyed
    } else if previous.condition == DeviceCondition::Critical
        || hp <= maximum * CRITICAL_HP_FRACTION
    {
        DeviceCondition::Critical
    } else {
        DeviceCondition::Normal
    };
    after.devices.insert(name, DeviceState { hp, condition });
    finalize_mutation(profile, state, after, 0, cause)
}

pub fn propose_repair_tick(
    profile: &CriticalProfile,
    state: &CriticalState,
    input: RepairTickInput,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    validate_repair_input(input)?;
    if input.dt_micros == 0 || !input.vehicle_alive {
        return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Repair);
    }
    let mut after = state.clone();
    let names: Vec<_> = after.devices.keys().copied().collect();
    for name in names {
        let descriptor = profile.device(name);
        let previous = after.devices[&name];
        if previous.hp >= descriptor.regen_hp
            || (name == DeviceName::FuelTankHealth && after.on_fire)
        {
            continue;
        }
        let seconds = repair_seconds(profile, name, input)?;
        let rate = descriptor.regen_hp / seconds.max(0.1);
        let hp =
            (previous.hp + rate * input.dt_micros as f64 / 1_000_000.0).min(descriptor.regen_hp);
        let condition =
            if previous.condition == DeviceCondition::Destroyed && hp >= descriptor.regen_hp {
                // Explicitly critical even when a #1513 regen cap lies above 50%.
                DeviceCondition::Critical
            } else {
                previous.condition
            };
        after.devices.insert(name, DeviceState { hp, condition });
    }
    finalize_mutation(profile, state, after, 0, CriticalCause::Repair)
}

pub fn propose_repair_device(
    profile: &CriticalProfile,
    state: &CriticalState,
    selected: Option<DeviceName>,
    repair_all: bool,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    let names: Vec<_> = if repair_all {
        state.devices.keys().copied().collect()
    } else {
        let name = selected.ok_or(CriticalDamageError::DeviceNotRepairable)?;
        if !state.devices.contains_key(&name) {
            return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Repair);
        }
        vec![name]
    };
    let mut after = state.clone();
    for name in names {
        let maximum = profile.device(name).max_hp;
        let previous = after.device_state(profile, name);
        if previous.condition != DeviceCondition::Normal || previous.hp < maximum {
            after.devices.insert(
                name,
                DeviceState {
                    hp: maximum,
                    condition: DeviceCondition::Normal,
                },
            );
        }
    }
    finalize_mutation(profile, state, after, 0, CriticalCause::Repair)
}

pub fn propose_restore_crew(
    profile: &CriticalProfile,
    state: &CriticalState,
    selected: Option<CrewName>,
    restore_all: bool,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    let mut after = state.clone();
    if restore_all {
        if after.crew_ko.is_empty() {
            return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Repair);
        }
        after.crew_ko.clear();
    } else {
        let name = selected.ok_or(CriticalDamageError::CrewNotRestorable)?;
        if !after.crew_ko.remove(&name) {
            return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Repair);
        }
    }
    finalize_mutation(profile, state, after, 0, CriticalCause::Repair)
}

pub fn propose_use_extinguisher(
    profile: &CriticalProfile,
    state: &CriticalState,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    if !state.on_fire {
        return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Repair);
    }
    let mut after = state.clone();
    extinguish(profile, &mut after);
    finalize_mutation(profile, state, after, 0, CriticalCause::Repair)
}

pub fn propose_fire_tick(
    profile: &CriticalProfile,
    state: &CriticalState,
    input: FireTickInput,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    if input.dt_micros == 0 || !state.on_fire || input.current_hull_health == 0 {
        return finalize_mutation(profile, state, state.clone(), 0, CriticalCause::Fire);
    }
    let mut after = state.clone();
    if after.fire_started_ms.is_none() {
        after.fire_started_ms = input.now_ms;
    }
    if let (Some(started), Some(now)) = (after.fire_started_ms, input.now_ms) {
        if now >= started && now - started >= FIRE_DURATION_MS {
            // The extinguishing frame may still complete one final fire tick.
            extinguish(profile, &mut after);
        }
    }
    let mut timer = after
        .fire_timer_micros
        .checked_add(input.dt_micros)
        .ok_or(CriticalDamageError::FireTimerExhausted)?;
    let mut damage = 0;
    if timer >= FIRE_TICK_MICROS {
        timer -= FIRE_TICK_MICROS;
        if !input.module_test_mode {
            damage = ((f64::from(input.max_hull_health) * FIRE_DAMAGE_FRACTION_PER_SECOND) as u32)
                .max(1);
        }
    }
    after.fire_timer_micros = timer;
    finalize_mutation(profile, state, after, damage, CriticalCause::Fire)
}

pub fn propose_drowning(
    profile: &CriticalProfile,
    state: &CriticalState,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    let mut after = state.clone();
    knock_out_everything(profile, &mut after);
    finalize_mutation(profile, state, after, 0, CriticalCause::Drowning)
}

pub fn propose_death(
    profile: &CriticalProfile,
    state: &CriticalState,
    cause: CriticalCause,
) -> Result<CriticalMutation, CriticalDamageError> {
    state.validate(profile)?;
    let mut after = state.clone();
    if after.on_fire {
        extinguish(profile, &mut after);
    }
    knock_out_everything(profile, &mut after);
    finalize_mutation(profile, state, after, 0, cause)
}

pub fn he_internal_depth_m(caliber_mm: f64) -> Result<f64, CriticalDamageError> {
    if !caliber_mm.is_finite() || caliber_mm < 0.0 {
        return Err(CriticalDamageError::InvalidShell);
    }
    Ok(caliber_mm / 100.0)
}

pub fn movement_hard_gated(state: &CriticalState) -> bool {
    [
        DeviceName::EngineHealth,
        DeviceName::LeftTrackHealth,
        DeviceName::RightTrackHealth,
    ]
    .into_iter()
    .any(|name| {
        state
            .devices
            .get(&name)
            .is_some_and(|device| device.condition == DeviceCondition::Destroyed)
    })
}

pub fn firing_hard_gated(state: &CriticalState) -> bool {
    state
        .devices
        .get(&DeviceName::GunHealth)
        .is_some_and(|device| device.condition == DeviceCondition::Destroyed)
}

pub fn stat_factor(profile: &CriticalProfile, state: &CriticalState, stat: CriticalStat) -> f64 {
    crew_stat_factor(profile, state, stat) * module_stat_factor(state, stat)
}

pub fn clamp_vision_factor(value: f64) -> f64 {
    if !value.is_finite() {
        1.0
    } else {
        value.clamp(MIN_VISION_FACTOR, 1.0)
    }
}

fn crew_stat_factor(profile: &CriticalProfile, state: &CriticalState, stat: CriticalStat) -> f64 {
    let impaired: BTreeSet<_> = profile
        .crew
        .iter()
        .filter(|member| state.crew_ko.contains(&member.name))
        .flat_map(|member| member.roles.iter().copied())
        .collect();
    let commander_out = impaired.contains(&CrewRole::Commander);
    let fit = crew_role_factor(1.0, true);
    let role_out = crew_role_factor(0.0, true);
    let commander_role_out = crew_role_factor(1.0, false);
    let time_factor = fit / role_out;
    let speed_factor = role_out / fit;
    let commander_time = fit / commander_role_out;
    let commander_speed = commander_role_out / fit;
    let mut factor = 1.0;
    match stat {
        CriticalStat::Reload => {
            if impaired.contains(&CrewRole::Loader) {
                factor *= time_factor;
            }
            if commander_out {
                factor *= commander_time;
            }
        }
        CriticalStat::Dispersion | CriticalStat::AimTime => {
            if impaired.contains(&CrewRole::Gunner) {
                factor *= time_factor;
            }
            if commander_out {
                factor *= commander_time;
            }
        }
        CriticalStat::TurretSpeed => {
            if impaired.contains(&CrewRole::Gunner) {
                factor *= speed_factor;
            }
            if commander_out {
                factor *= commander_speed;
            }
        }
        CriticalStat::Mobility => {
            if impaired.contains(&CrewRole::Driver) {
                factor *= speed_factor;
            }
            if commander_out {
                factor *= commander_speed;
            }
        }
        CriticalStat::Vision => {
            if commander_out {
                factor *= speed_factor;
            }
            if impaired.contains(&CrewRole::Radioman) {
                factor *= speed_factor;
            }
        }
        CriticalStat::Signal => {
            if impaired.contains(&CrewRole::Radioman) {
                factor *= speed_factor;
            }
            if commander_out {
                factor *= commander_speed;
            }
        }
    }
    factor
}

fn module_stat_factor(state: &CriticalState, stat: CriticalStat) -> f64 {
    let (name, critical_factor, destroyed_factor) = match stat {
        CriticalStat::Reload => (DeviceName::AmmoBayHealth, 2.0, None),
        CriticalStat::Dispersion | CriticalStat::AimTime => (DeviceName::GunHealth, 2.0, None),
        CriticalStat::TurretSpeed => (DeviceName::TurretRotatorHealth, 0.5, Some(0.0)),
        CriticalStat::Mobility => (DeviceName::EngineHealth, 0.5, Some(0.0)),
        CriticalStat::Vision => (
            DeviceName::SurveyingDeviceHealth,
            DAMAGED_MODULE_EFFICIENCY,
            Some(DESTROYED_MODULE_EFFICIENCY),
        ),
        CriticalStat::Signal => (
            DeviceName::RadioHealth,
            DAMAGED_MODULE_EFFICIENCY,
            Some(DESTROYED_MODULE_EFFICIENCY),
        ),
    };
    match state
        .devices
        .get(&name)
        .map_or(DeviceCondition::Normal, |device| device.condition)
    {
        DeviceCondition::Normal => 1.0,
        DeviceCondition::Critical => critical_factor,
        DeviceCondition::Destroyed => destroyed_factor.unwrap_or(1.0),
    }
}

fn crew_role_factor(level_share: f64, commander_alive: bool) -> f64 {
    let commander_bonus = if commander_alive {
        100.0 / COMMANDER_ADDITION_RATIO
    } else {
        0.0
    };
    let level = (100.0 + commander_bonus) * level_share.clamp(0.0, 1.0);
    CREW_FACTOR_BASE + CREW_FACTOR_SLOPE * (level / 100.0)
}

fn hit_chance(hit: ScoredHit, by_explosion: bool, dead_eye_bonus: f64) -> f64 {
    let live = if by_explosion {
        hit.explosion_chance
    } else {
        hit.projectile_chance
    };
    (live.unwrap_or_else(|| fallback_chance(hit.target, by_explosion)) + dead_eye_bonus).min(1.0)
}

pub fn fallback_chance(target: CriticalTarget, by_explosion: bool) -> f64 {
    match target {
        CriticalTarget::Crew(_) if by_explosion => 0.15,
        CriticalTarget::Crew(_) => 0.33,
        CriticalTarget::Device(DeviceName::AmmoBayHealth) => 0.27,
        CriticalTarget::Device(
            DeviceName::EngineHealth
            | DeviceName::FuelTankHealth
            | DeviceName::RadioHealth
            | DeviceName::TurretRotatorHealth
            | DeviceName::SurveyingDeviceHealth,
        ) => 0.45,
        CriticalTarget::Device(DeviceName::GunHealth) => 0.33,
        CriticalTarget::Device(DeviceName::LeftTrackHealth | DeviceName::RightTrackHealth) => 1.0,
    }
}

fn repair_seconds(
    profile: &CriticalProfile,
    name: DeviceName,
    input: RepairTickInput,
) -> Result<f64, CriticalDamageError> {
    validate_repair_input(input)?;
    let base = if name.is_track() {
        BASE_TRACK_REPAIR_SECONDS
    } else {
        BASE_MODULE_REPAIR_SECONDS
    };
    let mut factor = if let Some(repair_factor) = input.repair_factor {
        (1.0 + REPAIR_SKILL_SPEEDUP) * repair_factor.max(0.0)
    } else {
        1.0 + REPAIR_SKILL_SPEEDUP * (input.repair_skill_percent.clamp(0.0, 100.0) / 100.0)
    };
    factor *= profile.repair_speed_factor;
    if input.has_big_repair_kit {
        factor *= 1.10;
    }
    if factor <= 0.0 {
        factor = 1.0;
    }
    Ok(base / factor)
}

fn validate_repair_input(input: RepairTickInput) -> Result<(), CriticalDamageError> {
    if !input.repair_skill_percent.is_finite()
        || input.repair_factor.is_some_and(|value| !value.is_finite())
    {
        return Err(CriticalDamageError::InvalidRepairInput);
    }
    Ok(())
}

fn ignite(state: &mut CriticalState, now_ms: Option<u64>) {
    state.on_fire = true;
    state.fire_started_ms = now_ms;
}

fn extinguish(profile: &CriticalProfile, state: &mut CriticalState) {
    state.on_fire = false;
    state.fire_started_ms = None;
    let name = DeviceName::FuelTankHealth;
    let cap = profile.device(name).regen_hp;
    if state
        .devices
        .get(&name)
        .is_some_and(|device| device.hp < cap)
    {
        state.devices.insert(
            name,
            DeviceState {
                hp: cap,
                condition: DeviceCondition::Critical,
            },
        );
    }
}

fn knock_out_everything(profile: &CriticalProfile, state: &mut CriticalState) {
    for name in ALL_DEVICE_NAMES {
        state.devices.insert(
            name,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
    }
    state.crew_ko = profile.crew_roster();
}

fn finalize_mutation(
    profile: &CriticalProfile,
    before: &CriticalState,
    mut after: CriticalState,
    hull_damage: u32,
    cause: CriticalCause,
) -> Result<CriticalMutation, CriticalDamageError> {
    let public_changed = !before.public_eq(&after);
    after.revision = if public_changed {
        before
            .revision
            .checked_add(1)
            .ok_or(CriticalDamageError::RevisionExhausted)?
    } else {
        before.revision
    };
    after.validate(profile)?;
    let payload = if public_changed {
        Some(build_payload(profile, before, &after, cause))
    } else {
        None
    };
    Ok(CriticalMutation {
        base_revision: before.revision,
        revision: after.revision,
        hull_damage,
        payload,
        before: before.clone(),
        after,
    })
}

fn build_payload(
    profile: &CriticalProfile,
    before: &CriticalState,
    after: &CriticalState,
    cause: CriticalCause,
) -> CriticalPayload {
    let names: BTreeSet<_> = before
        .devices
        .keys()
        .chain(after.devices.keys())
        .copied()
        .collect();
    let devices: Vec<_> = names
        .iter()
        .map(|name| {
            let state = after.device_state(profile, *name);
            DeviceRecord {
                name: *name,
                hp: state.hp,
                max_hp: profile.device(*name).max_hp,
                state: state.condition,
            }
        })
        .collect();
    let mut events = Vec::new();
    for name in &names {
        let old_state = before.device_state(profile, *name).condition;
        let new_state = after.device_state(profile, *name).condition;
        if old_state != new_state {
            events.push(CriticalEvent::Device {
                name: *name,
                old_state,
                state: new_state,
                cause,
            });
        }
    }
    for name in after.crew_ko.difference(&before.crew_ko) {
        events.push(CriticalEvent::Crew {
            name: *name,
            state: CrewCondition::Destroyed,
            cause,
        });
    }
    for name in before.crew_ko.difference(&after.crew_ko) {
        events.push(CriticalEvent::Crew {
            name: *name,
            state: CrewCondition::Normal,
            cause,
        });
    }
    if before.on_fire != after.on_fire {
        events.push(CriticalEvent::Fire {
            state: after.on_fire,
            cause,
        });
    }
    if !before.ammo_rack_death && after.ammo_rack_death {
        events.push(CriticalEvent::AmmoRack {
            state: DeviceCondition::Destroyed,
            cause,
        });
    }
    CriticalPayload {
        revision: after.revision,
        base_revision: before.revision,
        devices,
        destroyed: after
            .devices
            .iter()
            .filter_map(|(name, state)| {
                (state.condition == DeviceCondition::Destroyed).then_some(*name)
            })
            .collect(),
        crew_ko: after.crew_ko.iter().copied().collect(),
        crew_roster: profile.crew.iter().map(|member| member.name).collect(),
        fire: after.on_fire,
        ammo_rack_death: after.ammo_rack_death,
        events,
    }
}

fn validate_target(
    target: Option<CriticalTarget>,
    roster: &BTreeSet<CrewName>,
) -> Result<(), CriticalDamageError> {
    if target.is_some_and(|target| match target {
        CriticalTarget::Device(name) => !ALL_DEVICE_NAMES.contains(&name),
        CriticalTarget::Crew(name) => !roster.contains(&name),
    }) {
        Err(CriticalDamageError::InvalidTarget)
    } else {
        Ok(())
    }
}

fn valid_optional_chance(value: Option<f64>) -> bool {
    value.is_none_or(|chance| chance.is_finite() && (0.0..=1.0).contains(&chance))
}

fn valid_roll(value: f64) -> bool {
    value.is_finite() && (0.0..1.0).contains(&value)
}

trait TapMut: Sized {
    fn tap_mut(self, function: impl FnOnce(&mut Self)) -> Self;
}

impl<T> TapMut for T {
    fn tap_mut(mut self, function: impl FnOnce(&mut Self)) -> Self {
        function(&mut self);
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile() -> CriticalProfile {
        let devices = ALL_DEVICE_NAMES
            .into_iter()
            .map(|name| {
                (
                    name,
                    DeviceProfile {
                        max_hp: 100.0,
                        regen_hp: 50.0,
                    },
                )
            })
            .collect();
        CriticalProfile {
            devices,
            crew: vec![
                crew(CrewName::Commander, &[CrewRole::Commander]),
                crew(CrewName::Driver, &[CrewRole::Driver]),
                crew(CrewName::Gunner1, &[CrewRole::Gunner]),
                crew(CrewName::Loader1, &[CrewRole::Loader]),
                crew(CrewName::Radioman1, &[CrewRole::Radioman]),
            ],
            engine_fire_starting_chance: 0.0,
            repair_speed_factor: 1.0,
        }
    }

    fn crew(name: CrewName, roles: &[CrewRole]) -> CrewMemberProfile {
        CrewMemberProfile {
            name,
            roles: roles.iter().copied().collect(),
        }
    }

    fn shell(kind: CriticalShellKind, module_damage: f64) -> CriticalShell {
        CriticalShell {
            kind,
            module_damage: Some(module_damage),
        }
    }

    fn target_layer(distance_m: f64, target: CriticalTarget, chance: f64) -> CriticalLayer {
        CriticalLayer {
            distance_m,
            armor_mm: 20.0,
            vehicle_damage_factor: 0.0,
            target: Some(target),
            chance_to_hit_by_projectile: Some(chance),
            chance_to_hit_by_explosion: Some(chance),
        }
    }

    fn wall(distance_m: f64) -> CriticalLayer {
        CriticalLayer {
            distance_m,
            armor_mm: 20.0,
            vehicle_damage_factor: 1.0,
            target: None,
            chance_to_hit_by_projectile: None,
            chance_to_hit_by_explosion: None,
        }
    }

    fn trace(layers: Vec<CriticalLayer>) -> CriticalTrace {
        CriticalTrace {
            native_layers: layers,
            internal_hits: None,
        }
    }

    fn input(kind: CriticalShellKind, module_damage: f64) -> StrikeInput {
        StrikeInput {
            hull_damage: 0,
            current_hull_health: 500,
            shell: shell(kind, module_damage),
            penetrated: Some(false),
            by_explosion: false,
            dead_eye: false,
            distance_filters: true,
            now_ms: Some(12_000),
        }
    }

    fn samples(targets: &[(CriticalTarget, f64)]) -> CriticalSamples {
        CriticalSamples {
            module_damage_factor: 1.0,
            target_rolls: targets.iter().copied().collect(),
            engine_fire_roll: None,
        }
    }

    fn event_states(payload: &CriticalPayload) -> Vec<(DeviceName, DeviceCondition)> {
        payload
            .events
            .iter()
            .filter_map(|event| match event {
                CriticalEvent::Device { name, state, .. } => Some((*name, *state)),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn external_track_uses_module_damage_and_can_crit_without_hull_penetration() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::LeftTrackHealth);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 120.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        let state = mutation.state().devices[&DeviceName::LeftTrackHealth];
        assert_eq!(state.hp, 0.0);
        assert_eq!(state.condition, DeviceCondition::Destroyed);
        assert!(movement_hard_gated(mutation.state()));
        assert_eq!(mutation.revision, 1);
        assert_eq!(
            event_states(mutation.payload.as_ref().unwrap()),
            vec![(DeviceName::LeftTrackHealth, DeviceCondition::Destroyed)]
        );
    }

    #[test]
    fn hidden_damage_crosses_half_max_health_not_regen_health() {
        let mut profile = profile();
        profile
            .devices
            .get_mut(&DeviceName::EngineHealth)
            .unwrap()
            .regen_hp = 80.0;
        let target = CriticalTarget::Device(DeviceName::EngineHealth);
        let first = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 30.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(first.state().devices[&DeviceName::EngineHealth].hp, 70.0);
        assert_eq!(
            first.state().devices[&DeviceName::EngineHealth].condition,
            DeviceCondition::Normal
        );
        assert!(first.payload.as_ref().unwrap().events.is_empty());
        assert_eq!(first.revision, 1, "hidden HP is still replicated state");

        let second = propose_strike(
            &profile,
            first.state(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 20.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(second.state().devices[&DeviceName::EngineHealth].hp, 50.0);
        assert_eq!(
            second.state().devices[&DeviceName::EngineHealth].condition,
            DeviceCondition::Critical
        );
        assert_eq!(
            event_states(second.payload.as_ref().unwrap()),
            vec![(DeviceName::EngineHealth, DeviceCondition::Critical)]
        );
    }

    #[test]
    fn duplicate_boxes_damage_one_logical_target_once() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::LeftTrackHealth);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![
                target_layer(1.0, target, 1.0),
                target_layer(1.1, target, 1.0),
            ]),
            input(CriticalShellKind::ArmorPiercing, 20.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(
            mutation.state().devices[&DeviceName::LeftTrackHealth].hp,
            80.0
        );
    }

    #[test]
    fn unavailable_internal_layout_fails_closed() {
        let profile = profile();
        let mut strike = input(CriticalShellKind::ArmorPiercing, 80.0);
        strike.hull_damage = 123;
        strike.penetrated = Some(true);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &CriticalTrace {
                native_layers: vec![wall(1.0)],
                internal_hits: None,
            },
            strike,
            &samples(&[]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(mutation.hull_damage, 123);
        assert!(mutation.payload.is_none());
        assert!(mutation.state().devices.is_empty());
    }

    #[test]
    fn direct_internal_distance_obeys_second_structural_plate() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::EngineHealth);
        let run = |distance_m| {
            let mut strike = input(CriticalShellKind::ArmorPiercing, 60.0);
            strike.penetrated = Some(true);
            propose_strike(
                &profile,
                &CriticalState::default(),
                &CriticalTrace {
                    native_layers: vec![wall(0.0), wall(1.0)],
                    internal_hits: Some(vec![InternalCriticalHit { distance_m, target }]),
                },
                strike,
                &samples(&[(target, 0.0)]),
                CriticalConfig::default(),
            )
            .unwrap()
        };
        assert_eq!(
            run(0.99).state().devices[&DeviceName::EngineHealth].hp,
            40.0
        );
        assert!(run(1.01).state().devices.is_empty());
    }

    #[test]
    fn nonpenetration_blocks_targets_behind_first_structural_plate() {
        let profile = profile();
        let front = CriticalTarget::Device(DeviceName::LeftTrackHealth);
        let rear = CriticalTarget::Device(DeviceName::EngineHealth);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![
                target_layer(0.5, front, 1.0),
                wall(1.0),
                target_layer(1.1, rear, 1.0),
            ]),
            input(CriticalShellKind::ArmorPiercing, 20.0),
            &samples(&[(front, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert!(mutation
            .state()
            .devices
            .contains_key(&DeviceName::LeftTrackHealth));
        assert!(!mutation
            .state()
            .devices
            .contains_key(&DeviceName::EngineHealth));
    }

    #[test]
    fn dead_eye_adds_three_points_only_to_ap_apcr_and_heat() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::GunHealth);
        for kind in [
            CriticalShellKind::ArmorPiercing,
            CriticalShellKind::ArmorPiercingCr,
            CriticalShellKind::HollowCharge,
        ] {
            let mut strike = input(kind, 20.0);
            strike.dead_eye = true;
            let mutation = propose_strike(
                &profile,
                &CriticalState::default(),
                &trace(vec![target_layer(1.0, target, 0.45)]),
                strike,
                &samples(&[(target, 0.47)]),
                CriticalConfig::default(),
            )
            .unwrap();
            assert_eq!(mutation.state().devices[&DeviceName::GunHealth].hp, 80.0);
        }
        let mut he = input(CriticalShellKind::HighExplosive, 20.0);
        he.dead_eye = true;
        assert!(propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 0.45)]),
            he,
            &samples(&[(target, 0.47)]),
            CriticalConfig::default(),
        )
        .unwrap()
        .state()
        .devices
        .is_empty());
    }

    #[test]
    fn crew_explosion_uses_point_fifteen_fallback() {
        let profile = profile();
        let target = CriticalTarget::Crew(CrewName::Commander);
        let mut strike = input(CriticalShellKind::HighExplosive, 20.0);
        strike.by_explosion = true;
        strike.distance_filters = false;
        strike.penetrated = None;
        let trace = CriticalTrace {
            native_layers: vec![],
            internal_hits: Some(vec![InternalCriticalHit {
                distance_m: 0.5,
                target,
            }]),
        };
        assert!(propose_strike(
            &profile,
            &CriticalState::default(),
            &trace,
            strike,
            &samples(&[(target, 0.149_999)]),
            CriticalConfig::default(),
        )
        .unwrap()
        .state()
        .crew_ko
        .contains(&CrewName::Commander));
        assert!(propose_strike(
            &profile,
            &CriticalState::default(),
            &trace,
            strike,
            &samples(&[(target, 0.15)]),
            CriticalConfig::default(),
        )
        .unwrap()
        .state()
        .crew_ko
        .is_empty());
    }

    #[test]
    fn every_successful_engine_loss_gets_one_fire_roll() {
        let mut profile = profile();
        profile.engine_fire_starting_chance = 1.0;
        let target = CriticalTarget::Device(DeviceName::EngineHealth);
        let mut deterministic = samples(&[(target, 0.0)]);
        deterministic.engine_fire_roll = Some(0.0);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 5.0),
            &deterministic,
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(mutation.state().devices[&DeviceName::EngineHealth].hp, 95.0);
        assert!(mutation.state().on_fire);
        assert_eq!(mutation.state().fire_started_ms, Some(12_000));
        assert!(mutation
            .payload
            .as_ref()
            .unwrap()
            .events
            .iter()
            .any(|event| matches!(event, CriticalEvent::Fire { state: true, .. })));
    }

    #[test]
    fn fuel_tank_ignites_only_at_zero_hp() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::FuelTankHealth);
        let mut state = CriticalState::default();
        state.devices.insert(
            DeviceName::FuelTankHealth,
            DeviceState {
                hp: 30.0,
                condition: DeviceCondition::Critical,
            },
        );
        let first = propose_strike(
            &profile,
            &state,
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 20.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(first.state().devices[&DeviceName::FuelTankHealth].hp, 10.0);
        assert!(!first.state().on_fire);
        let second = propose_strike(
            &profile,
            first.state(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 20.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(second.state().devices[&DeviceName::FuelTankHealth].hp, 0.0);
        assert!(second.state().on_fire);
    }

    #[test]
    fn ammo_rack_detonation_is_unconditional_after_module_destruction() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::AmmoBayHealth);
        let mutation = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 120.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(mutation.hull_damage, 510);
        assert!(mutation.state().ammo_rack_death);
        assert!(matches!(
            mutation.payload.as_ref().unwrap().events.last(),
            Some(CriticalEvent::AmmoRack { .. })
        ));

        let suppressed = propose_strike(
            &profile,
            &CriticalState::default(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 120.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig {
                suppress_ammo_rack_death: true,
                ..CriticalConfig::default()
            },
        )
        .unwrap();
        assert_eq!(suppressed.hull_damage, 0);
        assert!(!suppressed.state().ammo_rack_death);
        assert_eq!(
            suppressed.state().devices[&DeviceName::AmmoBayHealth].condition,
            DeviceCondition::Destroyed
        );
    }

    #[test]
    fn proposal_is_detached_and_commit_is_revision_fenced_and_idempotent() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::GunHealth);
        let mut ledger = CriticalLedger::new(profile, CriticalState::default()).unwrap();
        let proposal = ledger
            .propose_strike(
                &trace(vec![target_layer(1.0, target, 1.0)]),
                input(CriticalShellKind::ArmorPiercing, 20.0),
                &samples(&[(target, 0.0)]),
                CriticalConfig::default(),
            )
            .unwrap();
        assert!(ledger.state().devices.is_empty());
        assert_eq!(
            ledger.commit(proposal.clone()).unwrap(),
            CommitDisposition::Applied { revision: 1 }
        );
        assert_eq!(
            ledger.commit(proposal).unwrap(),
            CommitDisposition::ExactRetry { revision: 1 }
        );

        let stale_state = CriticalState::default();
        let stale = propose_device_damage_over_time(
            ledger.profile(),
            &stale_state,
            500,
            DeviceName::EngineHealth,
            5.0,
            CriticalCause::Equipment,
        )
        .unwrap();
        assert_eq!(
            ledger.commit(stale),
            Err(CriticalDamageError::RevisionConflict)
        );
    }

    #[test]
    fn frozen_critical_targets_apply_to_the_latest_repaired_state() {
        let profile = profile();
        let engine = CriticalTarget::Device(DeviceName::EngineHealth);
        let frozen_engine_trace = trace(vec![target_layer(1.0, engine, 1.0)]);
        let mut destroyed = CriticalState::default();
        destroyed.devices.insert(
            DeviceName::EngineHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        let repaired =
            propose_repair_device(&profile, &destroyed, Some(DeviceName::EngineHealth), false)
                .unwrap();
        let engine_hit = propose_strike(
            &profile,
            repaired.state(),
            &frozen_engine_trace,
            input(CriticalShellKind::ArmorPiercing, 60.0),
            &samples(&[(engine, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(
            engine_hit.state().devices[&DeviceName::EngineHealth],
            DeviceState {
                hp: 40.0,
                condition: DeviceCondition::Critical,
            }
        );

        let driver = CriticalTarget::Crew(CrewName::Driver);
        let frozen_driver_trace = trace(vec![target_layer(1.0, driver, 1.0)]);
        let mut knocked_out = CriticalState::default();
        knocked_out.crew_ko.insert(CrewName::Driver);
        let restored =
            propose_restore_crew(&profile, &knocked_out, Some(CrewName::Driver), false).unwrap();
        let driver_hit = propose_strike(
            &profile,
            restored.state(),
            &frozen_driver_trace,
            input(CriticalShellKind::ArmorPiercing, 1.0),
            &samples(&[(driver, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert!(driver_hit.state().crew_ko.contains(&CrewName::Driver));
    }

    #[test]
    fn deterministic_equipment_damage_preserves_hidden_and_destroyed_states() {
        let profile = profile();
        let first = propose_device_damage_over_time(
            &profile,
            &CriticalState::default(),
            500,
            DeviceName::EngineHealth,
            1.5,
            CriticalCause::Equipment,
        )
        .unwrap();
        assert_eq!(first.state().devices[&DeviceName::EngineHealth].hp, 98.5);
        assert!(first.payload.as_ref().unwrap().events.is_empty());
        let final_hit = propose_device_damage_over_time(
            &profile,
            first.state(),
            500,
            DeviceName::EngineHealth,
            200.0,
            CriticalCause::Equipment,
        )
        .unwrap();
        assert_eq!(final_hit.state().devices[&DeviceName::EngineHealth].hp, 0.0);
        assert!(movement_hard_gated(final_hit.state()));
    }

    #[test]
    fn fire_ticks_once_per_call_and_burnout_restores_fuel_to_regen_cap() {
        let profile = profile();
        let mut state = CriticalState {
            on_fire: true,
            fire_started_ms: Some(0),
            ..CriticalState::default()
        };
        state.devices.insert(
            DeviceName::FuelTankHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        let first = propose_fire_tick(
            &profile,
            &state,
            FireTickInput {
                dt_micros: 1_000_000,
                now_ms: Some(1_000),
                current_hull_health: 500,
                max_hull_health: 500,
                module_test_mode: false,
            },
        )
        .unwrap();
        assert_eq!(first.hull_damage, 25);
        assert!(first.payload.is_none());

        let mut ending = state;
        ending.fire_timer_micros = 900_000;
        let final_tick = propose_fire_tick(
            &profile,
            &ending,
            FireTickInput {
                dt_micros: 100_000,
                now_ms: Some(10_000),
                current_hull_health: 500,
                max_hull_health: 500,
                module_test_mode: false,
            },
        )
        .unwrap();
        assert_eq!(final_tick.hull_damage, 25);
        assert!(!final_tick.state().on_fire);
        assert_eq!(
            final_tick.state().devices[&DeviceName::FuelTankHealth],
            DeviceState {
                hp: 50.0,
                condition: DeviceCondition::Critical
            }
        );
        assert_eq!(
            event_states(final_tick.payload.as_ref().unwrap()),
            vec![(DeviceName::FuelTankHealth, DeviceCondition::Critical)]
        );
        assert!(final_tick
            .payload
            .as_ref()
            .unwrap()
            .events
            .iter()
            .any(|event| matches!(event, CriticalEvent::Fire { state: false, .. })));
    }

    #[test]
    fn destroyed_module_repairs_to_descriptor_cap_and_stays_explicitly_critical() {
        let mut profile = profile();
        profile
            .devices
            .get_mut(&DeviceName::LeftTrackHealth)
            .unwrap()
            .regen_hp = 80.0;
        let mut state = CriticalState::default();
        state.devices.insert(
            DeviceName::LeftTrackHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        let repair = propose_repair_tick(
            &profile,
            &state,
            RepairTickInput {
                dt_micros: 10_000_000,
                vehicle_alive: true,
                repair_skill_percent: 0.0,
                has_big_repair_kit: false,
                repair_factor: None,
            },
        )
        .unwrap();
        assert_eq!(
            repair.state().devices[&DeviceName::LeftTrackHealth],
            DeviceState {
                hp: 80.0,
                condition: DeviceCondition::Critical
            }
        );
        assert!(!movement_hard_gated(repair.state()));
        assert_eq!(
            event_states(repair.payload.as_ref().unwrap()),
            vec![(DeviceName::LeftTrackHealth, DeviceCondition::Critical)]
        );
    }

    #[test]
    fn repair_kit_restores_full_normal_and_module_can_be_destroyed_again() {
        let profile = profile();
        let mut state = CriticalState::default();
        state.devices.insert(
            DeviceName::LeftTrackHealth,
            DeviceState {
                hp: 50.0,
                condition: DeviceCondition::Critical,
            },
        );
        let repaired =
            propose_repair_device(&profile, &state, Some(DeviceName::LeftTrackHealth), false)
                .unwrap();
        assert_eq!(
            repaired.state().devices[&DeviceName::LeftTrackHealth],
            DeviceState {
                hp: 100.0,
                condition: DeviceCondition::Normal
            }
        );
        let target = CriticalTarget::Device(DeviceName::LeftTrackHealth);
        let destroyed = propose_strike(
            &profile,
            repaired.state(),
            &trace(vec![target_layer(1.0, target, 1.0)]),
            input(CriticalShellKind::ArmorPiercing, 120.0),
            &samples(&[(target, 0.0)]),
            CriticalConfig::default(),
        )
        .unwrap();
        assert_eq!(
            destroyed.state().devices[&DeviceName::LeftTrackHealth].condition,
            DeviceCondition::Destroyed
        );
    }

    #[test]
    fn extinguisher_restores_destroyed_fuel_and_preserves_fire_timer() {
        let profile = profile();
        let mut state = CriticalState {
            on_fire: true,
            fire_started_ms: Some(1_000),
            fire_timer_micros: 500_000,
            ..CriticalState::default()
        };
        state.devices.insert(
            DeviceName::FuelTankHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        let result = propose_use_extinguisher(&profile, &state).unwrap();
        assert!(!result.state().on_fire);
        assert_eq!(result.state().fire_started_ms, None);
        assert_eq!(result.state().fire_timer_micros, 500_000);
        assert_eq!(
            result.state().devices[&DeviceName::FuelTankHealth].condition,
            DeviceCondition::Critical
        );
    }

    #[test]
    fn small_med_kit_restores_only_selected_crew_member() {
        let profile = profile();
        let mut state = CriticalState::default();
        state.crew_ko = [CrewName::Driver, CrewName::Loader1].into_iter().collect();
        let result = propose_restore_crew(&profile, &state, Some(CrewName::Driver), false).unwrap();
        assert!(!result.state().crew_ko.contains(&CrewName::Driver));
        assert!(result.state().crew_ko.contains(&CrewName::Loader1));
        assert!(result.payload.as_ref().unwrap().events.iter().any(|event| {
            matches!(
                event,
                CriticalEvent::Crew {
                    name: CrewName::Driver,
                    state: CrewCondition::Normal,
                    ..
                }
            )
        }));
    }

    #[test]
    fn drowning_and_death_knock_out_real_roster_and_all_modules() {
        let profile = profile();
        let drowned = propose_drowning(&profile, &CriticalState::default()).unwrap();
        assert_eq!(drowned.state().devices.len(), ALL_DEVICE_NAMES.len());
        assert!(drowned
            .state()
            .devices
            .values()
            .all(|device| device.condition == DeviceCondition::Destroyed));
        assert_eq!(drowned.state().crew_ko, profile.crew_roster());
        assert!(drowned
            .payload
            .as_ref()
            .unwrap()
            .events
            .iter()
            .all(|event| {
                match event {
                    CriticalEvent::Device { cause, .. } | CriticalEvent::Crew { cause, .. } => {
                        *cause == CriticalCause::Drowning
                    }
                    _ => true,
                }
            }));

        let mut burning = CriticalState {
            on_fire: true,
            fire_started_ms: Some(1_000),
            ..CriticalState::default()
        };
        burning.devices.insert(
            DeviceName::FuelTankHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        let dead = propose_death(&profile, &burning, CriticalCause::Fire).unwrap();
        assert!(!dead.state().on_fire);
        assert!(dead
            .payload
            .as_ref()
            .unwrap()
            .events
            .iter()
            .any(|event| matches!(
                event,
                CriticalEvent::Fire {
                    state: false,
                    cause: CriticalCause::Fire
                }
            )));
    }

    #[test]
    fn crew_and_module_stat_factors_match_1513_curve() {
        let profile = profile();
        let fit = CriticalState::default();
        for stat in [
            CriticalStat::Reload,
            CriticalStat::AimTime,
            CriticalStat::Dispersion,
            CriticalStat::TurretSpeed,
            CriticalStat::Mobility,
            CriticalStat::Vision,
            CriticalStat::Signal,
        ] {
            assert_eq!(stat_factor(&profile, &fit, stat), 1.0);
        }
        let mut injured = CriticalState::default();
        injured.crew_ko.insert(CrewName::Loader1);
        assert_close(
            stat_factor(&profile, &injured, CriticalStat::Reload),
            1.043 / 0.57,
        );
        injured.crew_ko.clear();
        injured.crew_ko.insert(CrewName::Driver);
        injured.devices.insert(
            DeviceName::EngineHealth,
            DeviceState {
                hp: 50.0,
                condition: DeviceCondition::Critical,
            },
        );
        assert_close(
            stat_factor(&profile, &injured, CriticalStat::Mobility),
            (0.57 / 1.043) * 0.5,
        );
        injured.devices.insert(
            DeviceName::EngineHealth,
            DeviceState {
                hp: 0.0,
                condition: DeviceCondition::Destroyed,
            },
        );
        assert_eq!(stat_factor(&profile, &injured, CriticalStat::Mobility), 0.0);
        assert!(movement_hard_gated(&injured));
    }

    #[test]
    fn multi_role_crew_member_impairs_every_descriptor_role() {
        let mut profile = profile();
        profile.crew[0]
            .roles
            .extend([CrewRole::Gunner, CrewRole::Radioman]);
        let mut state = CriticalState::default();
        state.crew_ko.insert(CrewName::Commander);
        assert!(stat_factor(&profile, &state, CriticalStat::AimTime) > 1.043);
        assert!(stat_factor(&profile, &state, CriticalStat::Signal) < 1.0);
    }

    #[test]
    fn he_cone_contract_keeps_45_degrees_and_caliber_over_100_depth() {
        assert_eq!(HE_CONE_COS, 0.707_106_781_186_547_6);
        assert_eq!(HE_CONE_EDGE_FACTOR, 1.414_213_562_373_095_1);
        assert_eq!(he_internal_depth_m(100.0).unwrap(), 1.0);
        assert_eq!(he_internal_depth_m(152.0).unwrap(), 1.52);
    }

    #[test]
    fn missing_or_out_of_range_samples_fail_closed() {
        let profile = profile();
        let target = CriticalTarget::Device(DeviceName::GunHealth);
        assert_eq!(
            propose_strike(
                &profile,
                &CriticalState::default(),
                &trace(vec![target_layer(1.0, target, 1.0)]),
                input(CriticalShellKind::ArmorPiercing, 20.0),
                &samples(&[]),
                CriticalConfig::default(),
            ),
            Err(CriticalDamageError::MissingTargetRoll { target })
        );
        let mut invalid = samples(&[(target, 1.0)]);
        invalid.module_damage_factor = 1.250_001;
        assert_eq!(
            propose_strike(
                &profile,
                &CriticalState::default(),
                &trace(vec![target_layer(1.0, target, 1.0)]),
                input(CriticalShellKind::ArmorPiercing, 20.0),
                &invalid,
                CriticalConfig::default(),
            ),
            Err(CriticalDamageError::InvalidRandomSample)
        );
    }

    fn assert_close(left: f64, right: f64) {
        assert!((left - right).abs() <= 1.0e-12, "{left} != {right}");
    }
}
