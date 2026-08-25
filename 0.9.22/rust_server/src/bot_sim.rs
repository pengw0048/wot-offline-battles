//! Engine-free 30 Hz bot simulation core.
//!
//! The strategic planner owns target and route selection. This module owns the
//! deterministic vehicle, gun, ammunition, drowning, and launch state
//! machines. Native world facts arrive as fixed-latency oracle receipts; this
//! code never imports or emulates BigWorld.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::f64::consts::PI;
use std::fmt;

use crate::navgraph::{NavGraph, NavTarget};
use crate::player_ammo::{
    PhysicalBurstAdmission, PhysicalBurstClock, PhysicalBurstDescriptor, PhysicalBurstEdge,
    PhysicalBurstError,
};

pub const TICK_RATE_HZ: u64 = 30;
pub const ORACLE_LATENCY_TICKS: u64 = 3;
/// Bot-local BigWorld probes run at 10 Hz while the deterministic simulation
/// continues at 30 Hz. Three phase lanes spread a full 30-Bot roster evenly
/// across the hidden client's render budget.
pub const NATIVE_ACTION_CADENCE_TICKS: u64 = 3;
/// Pair visibility follows the pinned client's approximately six-Hz spotting
/// cadence. Final firing still needs an uncached exact lane receipt.
pub const VISIBILITY_CADENCE_TICKS: u64 = 5;
pub const FIRE_DURATION_US: u64 = 10_000_000;
pub const FIRE_TICK_US: u64 = 1_000_000;
pub const FIRE_DAMAGE_FRACTION_PER_SECOND: f64 = 0.05;
pub const DROWNING_PROBE_US: u64 = 300_000;
pub const DROWNING_DEPTH_METRES: f64 = 1.60;
pub const DROWNING_DURATION_US: u64 = 10_000_000;
pub const DROWNING_DEATH_REASON: u8 = 5;
pub const MAX_AMMO_QUANTITY: u16 = 1_000;

const GRAVITY_FACTOR: f64 = 1.25;
const GRAVITY: f64 = 9.81 * GRAVITY_FACTOR;
const COHESION: f64 = 1.3;
const DRIVE_TRACTION: f64 = 1.0;
const SLOPE_GRIP_LNG_FULL_Y: f64 = 0.887_010_833_178_221_7; // cos(27.5 deg)
const SLOPE_GRIP_LNG_MIN_Y: f64 = 0.848_048_096_156_426; // cos(32 deg)
const SLOPE_GRIP_LNG_FULL: f64 = 1.0;
const SLOPE_GRIP_LNG_MIN: f64 = 0.1;
const SLIP_THRESHOLD_TAN: f64 = 0.520_567_050_551_746_2; // tan(27.5 deg)
const SLIP_DRAG: f64 = 10.0;
const POWER_FACTOR: f64 = GRAVITY_FACTOR;
const BACKWARD_POWER_FRACTION: f64 = 1.0;
const ANGULAR_ACCELERATION_TIME: f64 = 0.05;
const SPEED_AFFECT_ROTATION_DECREASE: f64 = 0.0;
const COH_DECAY_Y: f64 = 0.969;
const COH_DECAY_FACTOR: f64 = 5.78;
const COH_DECAY_POWER: i32 = 3;
const COH_DECAY_BOUND: f64 = 0.5;
const SLOPE_COH_DECAY: f64 = 0.25;
const SLOPE_COH_DECAY_Y: f64 = 0.72;
const COAST_BRAKE_SHARE: f64 = 0.65;
const STEER_RESIST_MULTIPLIER: f64 = 1.6;
const ENGINE_MIN_SPEED: f64 = 1.5;
const SLIDE_HOLD_TANGENT: f64 = 0.50;
const SLIDE_KINETIC: f64 = 0.45;
const OVERSPEED_MAX_FACTOR: f64 = 1.05;
const OVERSPEED_DAMP: f64 = 2.0;
const OVERSPEED_BUILD: f64 = 0.20;
const TRAFFIC_HEADWAY_SECONDS: f64 = 1.0;
const TRAFFIC_STANDSTILL_CLEARANCE: f64 = 1.5;
const HUMAN_TARGET_ID_BASE: u32 = 1_000_000;

// Cache envelopes are deliberately expressed against the immutable query
// pose, never against the last Rust state which happened to consume it.
const GROUND_LONGITUDINAL_ENVELOPE_METRES: f64 = 1.5;
const GROUND_LATERAL_ENVELOPE_METRES: f64 = 1.0;
const GROUND_HEADING_ENVELOPE_RADIANS: f64 = 0.15;
const MOTION_FORWARD_ENVELOPE_METRES: f64 = 5.25;
const MOTION_LATERAL_ENVELOPE_METRES: f64 = 1.0;
const MOTION_HEADING_ENVELOPE_RADIANS: f64 = 0.20;
const VISIBILITY_POSITION_ENVELOPE_METRES: f64 = 6.0;
const BALLISTIC_SOURCE_ENVELOPE_METRES: f64 = 1.5;
const BALLISTIC_TARGET_ENVELOPE_METRES: f64 = 1.5;
const BALLISTIC_VELOCITY_ENVELOPE_MPS: f64 = 1.0;
const LANE_SOURCE_ENVELOPE_METRES: f64 = 0.75;
const LANE_TARGET_ENVELOPE_METRES: f64 = 1.5;
const LANE_YAW_ENVELOPE_RADIANS: f64 = 0.06;
const LANE_PITCH_ENVELOPE_RADIANS: f64 = 0.04;

const CREW_FACTOR_BASE: f64 = 0.57;
const CREW_FACTOR_SLOPE: f64 = 0.43;
const COMMANDER_ADDITION_RATIO: f64 = 10.0;
const DAMAGED_MODULE_EFFICIENCY: f64 = 0.5;
const DESTROYED_MODULE_EFFICIENCY: f64 = 0.25;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Vec3 {
    pub const ZERO: Self = Self {
        x: 0.0,
        y: 0.0,
        z: 0.0,
    };

    pub const fn new(x: f64, y: f64, z: f64) -> Self {
        Self { x, y, z }
    }

    pub fn horizontal_distance(self, other: Self) -> f64 {
        (self.x - other.x).hypot(self.z - other.z)
    }

    fn is_finite(self) -> bool {
        self.x.is_finite() && self.y.is_finite() && self.z.is_finite()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum VehicleClass {
    LightTank,
    MediumTank,
    HeavyTank,
    TankDestroyer,
    Spg,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ShellCategory {
    // Variant order intentionally matches Python's lexicographic category
    // order: "he", "premium", "standard".
    HighExplosive,
    Premium,
    Standard,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ShellDescriptor {
    pub index: usize,
    pub kind: String,
    pub penetration: f64,
    pub damage: f64,
    pub speed: f64,
    pub gravity: f64,
    pub max_distance: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ClipDescriptor {
    pub size: u16,
    pub intra_reload_seconds: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GunYawLimits {
    pub minimum: f64,
    pub maximum: f64,
}

impl Default for GunYawLimits {
    fn default() -> Self {
        Self {
            minimum: -PI,
            maximum: PI,
        }
    }
}

impl GunYawLimits {
    pub fn is_limited(self) -> bool {
        !(self.minimum <= -PI + 0.1 && self.maximum >= PI - 0.1)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GunDescriptor {
    pub reload_seconds: f64,
    pub clip: Option<ClipDescriptor>,
    pub shot_dispersion_angle: f64,
    pub gun_rotation_speed: f64,
    pub turret_rotation_speed: f64,
    pub pitch_limits: (f64, f64),
    pub yaw_limits: GunYawLimits,
    pub shells: Vec<ShellDescriptor>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PhysicsProfile {
    pub mass: f64,
    pub power_watts: f64,
    pub forward_speed_limit: f64,
    pub reverse_speed_limit: f64,
    pub rotation_speed: f64,
    pub terrain_resistance: [f64; 3],
    pub specific_friction: f64,
    pub brake_deceleration: f64,
    pub native_power_ratio: f64,
}

impl Default for PhysicsProfile {
    fn default() -> Self {
        Self {
            mass: 5_730.0,
            power_watts: 45.0 * 735.498_75,
            forward_speed_limit: 32.0 / 3.6,
            reverse_speed_limit: 12.0 / 3.6,
            rotation_speed: 38.0_f64.to_radians(),
            terrain_resistance: [1.1, 1.4, 2.6],
            specific_friction: 0.6867,
            brake_deceleration: COHESION * GRAVITY,
            native_power_ratio: 1.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VehicleDescriptor {
    pub vehicle_key: String,
    pub max_ammo: u16,
    pub max_health: u32,
    pub half_length: f64,
    pub half_width: f64,
    pub gun: GunDescriptor,
    pub physics: PhysicsProfile,
    pub module_names: Vec<String>,
    pub crew_roster: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ShellProfile {
    pub index: usize,
    pub kind: String,
    pub penetration: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotProfile {
    pub class: VehicleClass,
    pub shells: Vec<ShellProfile>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceCondition {
    Healthy,
    Critical,
    Destroyed,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CriticalState {
    pub devices: BTreeMap<String, DeviceCondition>,
    pub crew_ko: BTreeSet<String>,
    pub on_fire: bool,
    pub ammo_rack_death: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CriticalStat {
    Reload,
    Dispersion,
    AimTime,
    TurretSpeed,
    Mobility,
    Vision,
    Signal,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SimError {
    InvalidDescriptor(&'static str),
    InvalidState(&'static str),
    InvalidTick {
        expected: u64,
        actual: u64,
    },
    InvalidTickDelta {
        tick: u64,
        expected_us: u64,
        actual_us: u64,
    },
    NonFinite(&'static str),
    AtomicFireInvariant,
}

impl fmt::Display for SimError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidDescriptor(message) => write!(formatter, "invalid descriptor: {message}"),
            Self::InvalidState(message) => write!(formatter, "invalid bot state: {message}"),
            Self::InvalidTick { expected, actual } => {
                write!(formatter, "expected tick {expected}, got {actual}")
            }
            Self::InvalidTickDelta {
                tick,
                expected_us,
                actual_us,
            } => write!(
                formatter,
                "tick {tick} requires {expected_us} us, got {actual_us} us"
            ),
            Self::NonFinite(field) => write!(formatter, "{field} must be finite"),
            Self::AtomicFireInvariant => {
                formatter.write_str("ammunition changed during atomic fire")
            }
        }
    }
}

impl Error for SimError {}

pub fn time_us_at_tick(tick: u64) -> u64 {
    ((u128::from(tick) * 1_000_000) / u128::from(TICK_RATE_HZ)).min(u128::from(u64::MAX)) as u64
}

pub fn fixed_dt_us(tick: u64) -> u64 {
    time_us_at_tick(tick).saturating_sub(time_us_at_tick(tick.saturating_sub(1)))
}

pub fn native_query_due(bot_id: u32, tick: u64, cadence_ticks: u64) -> bool {
    debug_assert!(cadence_ticks > 0);
    if bot_id == 0 || tick == 0 || cadence_ticks == 0 {
        return false;
    }
    (tick - 1) % cadence_ticks == (u64::from(bot_id) - 1) % cadence_ticks
}

pub fn angle_delta(target: f64, current: f64) -> f64 {
    let mut value = target - current;
    while value > PI {
        value -= PI * 2.0;
    }
    while value < -PI {
        value += PI * 2.0;
    }
    value
}

pub fn wrapped(value: f64) -> f64 {
    angle_delta(value, 0.0)
}

fn distance_3d(left: Vec3, right: Vec3) -> f64 {
    ((left.x - right.x).powi(2) + (left.y - right.y).powi(2) + (left.z - right.z).powi(2)).sqrt()
}

fn pose_offsets(origin: Vec3, yaw: f64, current: Vec3) -> (f64, f64) {
    let dx = current.x - origin.x;
    let dz = current.z - origin.z;
    let (sine, cosine) = yaw.sin_cos();
    (dx * sine + dz * cosine, (dx * cosine - dz * sine).abs())
}

fn pose_inside_ground_envelope(
    origin: Vec3,
    sample_yaw: f64,
    current: Vec3,
    current_yaw: f64,
) -> bool {
    let (forward, lateral) = pose_offsets(origin, sample_yaw, current);
    forward.abs() <= GROUND_LONGITUDINAL_ENVELOPE_METRES
        && lateral <= GROUND_LATERAL_ENVELOPE_METRES
        && angle_delta(current_yaw, sample_yaw).abs() <= GROUND_HEADING_ENVELOPE_RADIANS
}

fn pose_inside_motion_envelope(
    origin: Vec3,
    sample_yaw: f64,
    current: Vec3,
    current_yaw: f64,
) -> bool {
    let (forward, lateral) = pose_offsets(origin, sample_yaw, current);
    let angle = angle_delta(current_yaw, sample_yaw).abs();
    forward >= -0.1
        && forward <= MOTION_FORWARD_ENVELOPE_METRES
        && lateral + 20.0 * angle.sin().abs() <= MOTION_LATERAL_ENVELOPE_METRES
        && angle <= MOTION_HEADING_ENVELOPE_RADIANS
}

pub fn slew(current: f64, desired: f64, maximum_step: f64) -> f64 {
    let difference = desired - current;
    let step = maximum_step.max(0.0);
    if difference > step {
        current + step
    } else if difference < -step {
        current - step
    } else {
        desired
    }
}

fn role_base(name: &str) -> &str {
    name.trim_end_matches(|character: char| character.is_ascii_digit())
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

fn crew_stat_factor(critical: &CriticalState, stat: CriticalStat) -> f64 {
    let roles: BTreeSet<_> = critical
        .crew_ko
        .iter()
        .map(|name| role_base(name.as_str()))
        .collect();
    let commander_out = roles.contains("commander");
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
            if roles.contains("loader") {
                factor *= time_factor;
            }
            if commander_out {
                factor *= commander_time;
            }
        }
        CriticalStat::Dispersion | CriticalStat::AimTime => {
            if roles.contains("gunner") {
                factor *= time_factor;
            }
            if commander_out {
                factor *= commander_time;
            }
        }
        CriticalStat::TurretSpeed => {
            if roles.contains("gunner") {
                factor *= speed_factor;
            }
            if commander_out {
                factor *= commander_speed;
            }
        }
        CriticalStat::Mobility => {
            if roles.contains("driver") {
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
            if roles.contains("radioman") {
                factor *= speed_factor;
            }
        }
        CriticalStat::Signal => {
            if roles.contains("radioman") {
                factor *= speed_factor;
            }
            if commander_out {
                factor *= commander_speed;
            }
        }
    }
    factor
}

fn module_stat_factor(critical: &CriticalState, stat: CriticalStat) -> f64 {
    let (name, damaged, destroyed) = match stat {
        CriticalStat::Reload => ("ammoBayHealth", 2.0, None),
        CriticalStat::Dispersion | CriticalStat::AimTime => ("gunHealth", 2.0, None),
        CriticalStat::TurretSpeed => ("turretRotatorHealth", 0.5, Some(0.0)),
        CriticalStat::Mobility => ("engineHealth", 0.5, Some(0.0)),
        CriticalStat::Vision => (
            "surveyingDeviceHealth",
            DAMAGED_MODULE_EFFICIENCY,
            Some(DESTROYED_MODULE_EFFICIENCY),
        ),
        CriticalStat::Signal => (
            "radioHealth",
            DAMAGED_MODULE_EFFICIENCY,
            Some(DESTROYED_MODULE_EFFICIENCY),
        ),
    };
    match critical
        .devices
        .get(name)
        .copied()
        .unwrap_or(DeviceCondition::Healthy)
    {
        DeviceCondition::Healthy => 1.0,
        DeviceCondition::Critical => damaged,
        DeviceCondition::Destroyed => destroyed.unwrap_or(1.0),
    }
}

pub fn critical_factor(critical: &CriticalState, stat: CriticalStat) -> f64 {
    crew_stat_factor(critical, stat) * module_stat_factor(critical, stat)
}

pub fn movement_hard_gated(critical: &CriticalState) -> bool {
    ["engineHealth", "leftTrackHealth", "rightTrackHealth"]
        .iter()
        .any(|name| critical.devices.get(*name) == Some(&DeviceCondition::Destroyed))
}

pub fn firing_hard_gated(critical: &CriticalState) -> bool {
    critical.devices.get("gunHealth") == Some(&DeviceCondition::Destroyed)
}

fn longitudinal_slope_grip(slope_pitch: f64) -> f64 {
    let normal_y = slope_pitch.cos();
    let grip = if normal_y >= SLOPE_GRIP_LNG_FULL_Y {
        SLOPE_GRIP_LNG_FULL
    } else if normal_y <= SLOPE_GRIP_LNG_MIN_Y {
        SLOPE_GRIP_LNG_MIN
    } else {
        let span = SLOPE_GRIP_LNG_FULL_Y - SLOPE_GRIP_LNG_MIN_Y;
        let progress = (normal_y - SLOPE_GRIP_LNG_MIN_Y) / span;
        SLOPE_GRIP_LNG_MIN + progress * (SLOPE_GRIP_LNG_FULL - SLOPE_GRIP_LNG_MIN)
    };
    grip * DRIVE_TRACTION
}

pub fn engine_force(profile: &PhysicsProfile, speed: f64, throttle: f64, slope_pitch: f64) -> f64 {
    if throttle == 0.0 {
        return 0.0;
    }
    let mut power = profile.power_watts * POWER_FACTOR * profile.native_power_ratio;
    if throttle < 0.0 {
        power *= BACKWARD_POWER_FRACTION;
    }
    let mut force = power / speed.abs().max(ENGINE_MIN_SPEED);
    let normal_y = slope_pitch.cos().max(0.1);
    let maximum = longitudinal_slope_grip(slope_pitch) * profile.mass * GRAVITY * normal_y;
    if force > maximum {
        force = maximum;
    }
    force * throttle
}

pub fn rolling_resist_force(profile: &PhysicsProfile, terrain_index: usize, steering: bool) -> f64 {
    let terrain = profile.terrain_resistance[terrain_index.min(2)];
    let mut force = profile.mass * profile.specific_friction * GRAVITY_FACTOR * terrain;
    if steering {
        force *= STEER_RESIST_MULTIPLIER;
    }
    force
}

fn slope_cohesion(normal_y: f64) -> f64 {
    let mut cohesion = COHESION;
    if normal_y < COH_DECAY_Y {
        cohesion -= COH_DECAY_FACTOR * (COH_DECAY_Y - normal_y).powi(COH_DECAY_POWER);
    }
    if normal_y < SLOPE_COH_DECAY_Y {
        cohesion -= SLOPE_COH_DECAY;
    }
    cohesion.max(COH_DECAY_BOUND)
}

fn grip_deceleration(profile: &PhysicsProfile, slope_pitch: f64) -> f64 {
    let normal_y = slope_pitch.cos();
    let _ = profile;
    slope_cohesion(normal_y) * GRAVITY * normal_y.max(0.1)
}

/// Copied longitudinal drivetrain integration used by the Python authority.
#[allow(clippy::too_many_arguments)]
pub fn longitudinal_step(
    profile: &PhysicsProfile,
    speed: f64,
    throttle: f64,
    steering: bool,
    slope_pitch: f64,
    dt: f64,
    airborne: bool,
    terrain_index: usize,
    handbrake: bool,
) -> f64 {
    if airborne {
        return speed;
    }
    let gravity_acceleration = GRAVITY * slope_pitch.sin();
    let grip = grip_deceleration(profile, slope_pitch);
    if handbrake {
        if speed.abs() < 0.05 {
            if gravity_acceleration.abs() <= grip {
                return 0.0;
            }
            return speed + (gravity_acceleration - grip.copysign(gravity_acceleration)) * dt;
        }
        let next = speed + (gravity_acceleration - grip.copysign(speed)) * dt;
        if (speed > 0.0) != (next > 0.0) {
            return 0.0;
        }
        return next;
    }

    let rolling = rolling_resist_force(profile, terrain_index, steering) / profile.mass;
    let mut acceleration;
    if throttle != 0.0 {
        let mut engine = engine_force(profile, speed, throttle, slope_pitch) / profile.mass;
        let normal_y = slope_pitch.cos().max(0.1);
        let maximum_climb = longitudinal_slope_grip(slope_pitch) * GRAVITY * normal_y;
        let cannot_climb = throttle * gravity_acceleration < 0.0
            && maximum_climb < gravity_acceleration.abs() + rolling;
        if cannot_climb {
            engine = 0.0;
        }
        acceleration = engine + gravity_acceleration;
        acceleration -= rolling * (speed / 0.08).clamp(-1.0, 1.0);
        if cannot_climb {
            if speed.abs() > 0.05 {
                let kinetic = SLIDE_KINETIC * GRAVITY * normal_y;
                acceleration += if speed < 0.0 { kinetic } else { -kinetic };
            }
        } else if (throttle > 0.0 && speed < -0.1) || (throttle < 0.0 && speed > 0.1) {
            let needed = -speed / dt - acceleration;
            acceleration += if needed.abs() < grip {
                needed
            } else {
                grip.copysign(needed)
            };
        }
    } else if speed.abs() < 0.02 {
        let normal_y = slope_pitch.cos().max(0.1);
        let hold = SLIDE_HOLD_TANGENT * GRAVITY * normal_y;
        if gravity_acceleration.abs() <= hold {
            return 0.0;
        }
        acceleration = gravity_acceleration - hold.copysign(gravity_acceleration);
    } else {
        let motion_sign = if speed > 0.0 { 1.0 } else { -1.0 };
        let downhill_tangent = (slope_pitch.tan() * motion_sign).max(0.0);
        let fade_start = 0.8 * SLIDE_HOLD_TANGENT;
        let fade =
            ((downhill_tangent - fade_start) / (SLIDE_HOLD_TANGENT - fade_start)).clamp(0.0, 1.0);
        let resistance = rolling + COAST_BRAKE_SHARE * (1.0 - fade) * grip;
        acceleration = gravity_acceleration - resistance.copysign(speed);
    }

    let grade = if speed > 0.0 {
        -slope_pitch
    } else {
        slope_pitch
    };
    if speed.abs() > 0.5 && grade > 0.0 {
        let tangent = grade.tan();
        if tangent > SLIP_THRESHOLD_TAN {
            let slip = SLIP_DRAG * (tangent - SLIP_THRESHOLD_TAN) * GRAVITY;
            acceleration -= slip.copysign(speed);
        }
    }

    let mut next = speed + acceleration * dt;
    if throttle == 0.0
        && gravity_acceleration.abs() <= grip
        && speed != 0.0
        && (speed > 0.0) != (next > 0.0)
    {
        next = 0.0;
    }

    let direction = if next >= 0.0 { 1.0 } else { -1.0 };
    let limit = if next >= 0.0 {
        profile.forward_speed_limit
    } else {
        profile.reverse_speed_limit
    };
    if next.abs() > limit {
        let cap = limit * (OVERSPEED_MAX_FACTOR - 1.0);
        let previous_excess = (speed.abs() - limit).max(0.0);
        let excess = if throttle * direction > 0.0 && gravity_acceleration * direction > 0.05 {
            previous_excess + OVERSPEED_BUILD * slope_pitch.abs().sin() * dt
        } else {
            previous_excess - (rolling + OVERSPEED_DAMP) * dt
        }
        .clamp(0.0, cap);
        next = direction * (limit + excess);
    }
    next
}

/// Copied 0.8.2 hull-traverse ramp. Reverse intent flips steering explicitly.
pub fn traverse_step(
    profile: &PhysicsProfile,
    angular_speed: f64,
    steering: f64,
    speed: f64,
    dt: f64,
    terrain_index: usize,
    drive_intent: f64,
) -> f64 {
    let speed_ratio = speed.abs() / profile.forward_speed_limit.max(0.1);
    let rotation_modifier = 1.0 / (1.0 + speed_ratio * SPEED_AFFECT_ROTATION_DECREASE);
    let terrain_modifier =
        profile.terrain_resistance[0] / profile.terrain_resistance[terrain_index.min(2)];
    let maximum = profile.rotation_speed * rotation_modifier * terrain_modifier;
    let intent_sign = if drive_intent < 0.0 { -1.0 } else { 1.0 };
    let target = steering * intent_sign * maximum;
    let difference = target - angular_speed;
    let ramp = maximum / ANGULAR_ACCELERATION_TIME;
    let mut next = if difference.abs() < ramp * dt {
        target
    } else {
        angular_speed + ramp * dt * difference.signum()
    };
    if steering == 0.0 && next.abs() < 0.01 {
        next = 0.0;
    }
    next
}

fn shell_categories(profile: &BotProfile, shell_count: usize) -> Vec<ShellCategory> {
    let by_index: BTreeMap<_, _> = profile
        .shells
        .iter()
        .filter(|shell| shell.index < shell_count)
        .map(|shell| (shell.index, shell))
        .collect();
    let mut categories = vec![ShellCategory::Standard; shell_count];
    let mut non_he = Vec::new();
    for index in 0..shell_count {
        let kind = by_index
            .get(&index)
            .map(|shell| shell.kind.to_ascii_lowercase())
            .unwrap_or_default();
        let high_explosive = kind.contains("high_explosive")
            || (kind.contains("explosive") && !kind.contains("armor_piercing"));
        if high_explosive {
            categories[index] = ShellCategory::HighExplosive;
        } else {
            non_he.push(index);
        }
    }
    let baseline = non_he.first().copied();
    let baseline_penetration = baseline
        .and_then(|index| by_index.get(&index))
        .map(|shell| shell.penetration.max(0.0))
        .unwrap_or(0.0);
    for index in non_he {
        let penetration = by_index
            .get(&index)
            .map(|shell| shell.penetration.max(0.0))
            .unwrap_or(0.0);
        categories[index] = if Some(index) != baseline
            && baseline_penetration > 0.0
            && penetration >= baseline_penetration * 1.03
        {
            ShellCategory::Premium
        } else {
            ShellCategory::Standard
        };
    }
    categories
}

/// Allocate the exact fixed per-battle inventory used by `bot_runtime.py`.
pub fn ammunition_distribution(descriptor: &VehicleDescriptor, profile: &BotProfile) -> Vec<u16> {
    let shell_count = descriptor.gun.shells.len().max(1);
    let maximum = usize::from(descriptor.max_ammo.min(MAX_AMMO_QUANTITY));
    if maximum == 0 {
        return vec![0; shell_count];
    }
    let categories = shell_categories(profile, shell_count);
    let active: BTreeSet<_> = categories.iter().copied().collect();
    let weight = |category| match (profile.class, category) {
        (VehicleClass::Spg, ShellCategory::HighExplosive) => 4.0,
        (VehicleClass::Spg, _) => 1.0,
        (_, ShellCategory::Standard) => 3.0,
        (_, ShellCategory::Premium) => 2.0,
        (_, ShellCategory::HighExplosive) => 1.0,
    };
    let total_weight: f64 = active.iter().copied().map(weight).sum();
    if total_weight <= 0.0 {
        return vec![0; shell_count];
    }
    let mut category_counts = BTreeMap::new();
    for category in active.iter().copied() {
        let count = (maximum as f64 * weight(category) / total_weight) as usize;
        category_counts.insert(category, count);
    }
    let assigned: usize = category_counts.values().sum();
    let mut remainders: Vec<_> = active
        .iter()
        .copied()
        .map(|category| {
            let exact = maximum as f64 * weight(category) / total_weight;
            let remainder = exact - category_counts[&category] as f64;
            (category, remainder)
        })
        .collect();
    remainders.sort_by(|(left_category, left), (right_category, right)| {
        right
            .total_cmp(left)
            .then_with(|| left_category.cmp(right_category))
    });
    for offset in 0..maximum.saturating_sub(assigned) {
        *category_counts
            .get_mut(&remainders[offset % remainders.len()].0)
            .expect("active category must have a count") += 1;
    }
    let mut result = vec![0_u16; shell_count];
    for category in active {
        let indices: Vec<_> = categories
            .iter()
            .enumerate()
            .filter_map(|(index, candidate)| (*candidate == category).then_some(index))
            .collect();
        for offset in 0..category_counts[&category] {
            result[indices[offset % indices.len()]] += 1;
        }
    }
    result
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AmmoSnapshot {
    pub loaded: usize,
    pub next: usize,
    pub remaining: Vec<u16>,
    pub reload_pending: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AmmoState {
    shell_count: usize,
    categories: Vec<ShellCategory>,
    remaining: Vec<u16>,
    loaded: usize,
    next: usize,
    reload_pending: bool,
    plan_pending: bool,
}

impl AmmoState {
    pub fn new(descriptor: &VehicleDescriptor, profile: &BotProfile) -> Self {
        let shell_count = descriptor.gun.shells.len().max(1);
        let remaining = ammunition_distribution(descriptor, profile);
        Self::from_inventory(profile, shell_count, remaining)
    }

    /// Construct a canonical initial state from an exact donated inventory.
    ///
    /// Unlike [`Self::new`], this path never synthesizes an ammunition
    /// distribution. The selected round is the first stocked standard shell,
    /// or the first stocked shell of any category when no standard shell is
    /// available.
    pub fn new_exact(
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        remaining: Vec<u16>,
    ) -> Result<Self, SimError> {
        let (shell_count, _) = Self::validate_exact_inventory(descriptor, &remaining)?;
        Ok(Self::from_inventory(profile, shell_count, remaining))
    }

    /// Construct from an exact inventory and the exact garage-loaded round.
    pub fn new_exact_loaded(
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        remaining: Vec<u16>,
        loaded: usize,
    ) -> Result<Self, SimError> {
        let (shell_count, total) = Self::validate_exact_inventory(descriptor, &remaining)?;
        if loaded >= shell_count {
            return Err(SimError::InvalidState("ammunition selection"));
        }
        if total > 0 && remaining[loaded] == 0 {
            return Err(SimError::InvalidState("loaded ammunition is exhausted"));
        }
        let mut state = Self::from_inventory(profile, shell_count, remaining);
        state.loaded = loaded;
        state.next = loaded;
        Ok(state)
    }

    fn validate_exact_inventory(
        descriptor: &VehicleDescriptor,
        remaining: &[u16],
    ) -> Result<(usize, u32), SimError> {
        let shell_count = descriptor.gun.shells.len();
        if shell_count == 0 || remaining.len() != shell_count {
            return Err(SimError::InvalidState("ammunition inventory shape"));
        }
        if remaining
            .iter()
            .any(|quantity| *quantity > MAX_AMMO_QUANTITY)
        {
            return Err(SimError::InvalidState("ammunition quantity"));
        }
        let total: u32 = remaining.iter().copied().map(u32::from).sum();
        if total > u32::from(descriptor.max_ammo) {
            return Err(SimError::InvalidState("ammunition capacity"));
        }
        Ok((shell_count, total))
    }

    fn from_inventory(profile: &BotProfile, shell_count: usize, remaining: Vec<u16>) -> Self {
        debug_assert_eq!(remaining.len(), shell_count);
        let categories = shell_categories(profile, shell_count);
        let mut state = Self {
            shell_count,
            categories,
            remaining,
            loaded: 0,
            next: 0,
            reload_pending: false,
            plan_pending: true,
        };
        state.loaded = state.standard_fallback();
        state.next = state.loaded;
        state
    }

    pub fn restore(
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        snapshot: AmmoSnapshot,
    ) -> Result<Self, SimError> {
        let mut state = Self::new(descriptor, profile);
        if snapshot.remaining.len() != state.shell_count {
            return Err(SimError::InvalidState("ammunition inventory shape"));
        }
        if snapshot.loaded >= state.shell_count || snapshot.next >= state.shell_count {
            return Err(SimError::InvalidState("ammunition selection"));
        }
        let total: u32 = snapshot.remaining.iter().copied().map(u32::from).sum();
        if total > 0 && snapshot.remaining[snapshot.next] == 0 {
            return Err(SimError::InvalidState("planned ammunition is exhausted"));
        }
        if total > 0 && !snapshot.reload_pending && snapshot.remaining[snapshot.loaded] == 0 {
            return Err(SimError::InvalidState("loaded ammunition is exhausted"));
        }
        state.remaining = snapshot.remaining;
        state.loaded = snapshot.loaded;
        state.next = snapshot.next;
        state.reload_pending = snapshot.reload_pending;
        state.plan_pending = false;
        Ok(state)
    }

    fn standard_fallback(&self) -> usize {
        let candidates: Vec<_> = (0..self.shell_count)
            .filter(|index| self.remaining[*index] > 0)
            .collect();
        if let Some(index) = candidates
            .iter()
            .copied()
            .find(|index| self.categories[*index] == ShellCategory::Standard)
        {
            index
        } else {
            candidates.first().copied().unwrap_or(0)
        }
    }

    fn available(&self, requested: usize) -> usize {
        if requested < self.shell_count && self.remaining[requested] > 0 {
            requested
        } else {
            self.standard_fallback()
        }
    }

    /// Commit loaded and planned rounds only at a completed reload edge.
    pub fn stage(&mut self, requested: usize, ready: bool) -> bool {
        if !ready {
            return false;
        }
        let mut changed = false;
        if self.reload_pending {
            let selected = self.available(self.next);
            if selected != self.loaded {
                self.loaded = selected;
                changed = true;
            }
            self.reload_pending = false;
            self.plan_pending = true;
        }
        if self.plan_pending {
            let selected = self.available(requested);
            if selected != self.next {
                self.next = selected;
                changed = true;
            }
            self.plan_pending = false;
        }
        changed
    }

    pub fn can_fire(&self) -> bool {
        self.can_fire_round(false)
    }

    pub fn consume_loaded(&mut self) -> bool {
        self.consume_loaded_round(false)
    }

    fn can_fire_round(&self, continuing_burst: bool) -> bool {
        self.loaded < self.shell_count
            && self.remaining[self.loaded] > 0
            && (continuing_burst || !self.reload_pending)
    }

    fn consume_loaded_round(&mut self, continuing_burst: bool) -> bool {
        if !self.can_fire_round(continuing_burst) {
            return false;
        }
        self.remaining[self.loaded] -= 1;
        self.next = self.available(self.next);
        self.reload_pending = true;
        self.plan_pending = false;
        true
    }

    fn loaded_remaining(&self) -> u16 {
        self.remaining.get(self.loaded).copied().unwrap_or(0)
    }

    fn total_remaining(&self) -> u16 {
        self.remaining.iter().copied().sum()
    }

    fn loaded_shell_requires_full_reload(&self) -> bool {
        self.loaded_remaining() == 0 && self.total_remaining() > 0
    }

    pub fn snapshot(&self) -> AmmoSnapshot {
        AmmoSnapshot {
            loaded: self.loaded,
            next: self.next,
            remaining: self.remaining.clone(),
            reload_pending: self.reload_pending,
        }
    }

    pub fn loaded(&self) -> usize {
        self.loaded
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GunState {
    pub fully_aimed_dispersion: f64,
    pub reload_full: f64,
    pub clip_size: u16,
    pub reload_intra: f64,
    pub shell_count: usize,
    pub clip: u16,
    pub elapsed: f64,
    pub reload_duration: f64,
    reload_factor: f64,
    burst_remaining: u16,
}

impl GunState {
    pub fn new(descriptor: &GunDescriptor, fire_seq: u64) -> Result<Self, SimError> {
        if !descriptor.shot_dispersion_angle.is_finite() || descriptor.shot_dispersion_angle <= 0.0
        {
            return Err(SimError::InvalidDescriptor(
                "gun shot dispersion must be positive",
            ));
        }
        let (clip_size, reload_intra) = descriptor
            .clip
            .map(|clip| (clip.size.max(1), clip.intra_reload_seconds.max(0.01)))
            .unwrap_or((1, 0.0));
        let mut state = Self {
            fully_aimed_dispersion: descriptor.shot_dispersion_angle,
            reload_full: descriptor.reload_seconds.max(0.01),
            clip_size,
            reload_intra,
            shell_count: descriptor.shells.len().max(1),
            clip: clip_size,
            elapsed: 0.0,
            reload_duration: descriptor.reload_seconds.max(0.01),
            reload_factor: 1.0,
            burst_remaining: 0,
        };
        state.restore_fire_seq(fire_seq);
        Ok(state)
    }

    pub fn restore_fire_seq(&mut self, fire_seq: u64) {
        if self.clip_size > 1 {
            let used = fire_seq % u64::from(self.clip_size);
            self.clip = if used == 0 {
                self.clip_size
            } else {
                self.clip_size - used as u16
            };
            self.reload_duration = if used == 0 {
                self.reload_full
            } else {
                self.reload_intra
            };
        } else {
            self.clip = 1;
            self.reload_duration = self.reload_full;
        }
        self.elapsed = 0.0;
        self.burst_remaining = 0;
    }

    pub fn tick(&mut self, dt: f64) {
        self.elapsed += dt.max(0.0);
    }

    /// Preserve completed reload fraction when a live critical penalty changes.
    pub fn rescale_reload(&mut self, reload_factor: f64) -> bool {
        let reload_factor = reload_factor.max(0.0);
        if (reload_factor - self.reload_factor).abs() <= 1.0e-9 {
            return false;
        }
        let old_duration = self.reload_duration * self.reload_factor;
        let new_duration = self.reload_duration * reload_factor;
        if old_duration > 0.0 {
            if self.elapsed < old_duration {
                let completed = (self.elapsed / old_duration).clamp(0.0, 1.0);
                self.elapsed = new_duration * completed;
            } else {
                self.elapsed = new_duration + (self.elapsed - old_duration);
            }
        }
        self.reload_factor = reload_factor;
        true
    }

    pub fn ready(&self, reload_factor: f64) -> bool {
        self.elapsed > self.reload_duration * reload_factor.max(0.0)
    }

    pub fn shell_index(&self, requested: usize) -> usize {
        requested.min(self.shell_count - 1)
    }

    pub fn fire(&mut self, reload_factor: f64) -> bool {
        if self.burst_remaining > 0 || !self.ready(reload_factor) {
            return false;
        }
        self.elapsed = 0.0;
        if self.clip_size > 1 {
            self.clip -= 1;
            if self.clip == 0 {
                self.clip = self.clip_size;
                self.reload_duration = self.reload_full;
            } else {
                self.reload_duration = self.reload_intra;
            }
        } else {
            self.reload_duration = self.reload_full;
        }
        true
    }

    fn begin_physical_burst(&mut self, count: u16, reload_factor: f64) -> bool {
        if self.burst_remaining > 0 || !self.ready(reload_factor) {
            return false;
        }
        let count = count.min(self.clip);
        if count == 0 {
            return false;
        }
        self.burst_remaining = count;
        true
    }

    fn fire_physical_burst_round(&mut self, final_round: bool) -> bool {
        if self.burst_remaining == 0 || self.clip == 0 || final_round != (self.burst_remaining == 1)
        {
            return false;
        }
        self.clip -= 1;
        self.burst_remaining -= 1;
        if !final_round {
            return true;
        }
        self.elapsed = 0.0;
        if self.clip == 0 {
            self.reload_duration = self.reload_full;
        } else {
            self.reload_duration = self.reload_intra;
        }
        true
    }

    fn cancel_physical_burst(&mut self) -> bool {
        if self.burst_remaining == 0 {
            return false;
        }
        self.burst_remaining = 0;
        self.elapsed = 0.0;
        self.reload_duration = if self.clip == 0 {
            self.reload_full
        } else {
            self.reload_intra
        };
        true
    }

    fn require_full_reload(&mut self) {
        self.clip = 0;
        self.reload_duration = self.reload_full;
    }

    fn complete_physical_reload(&mut self, reload_factor: f64, available_rounds: u16) -> bool {
        if self.burst_remaining > 0 || self.clip > 0 || !self.ready(reload_factor) {
            return false;
        }
        self.clip = self.clip_size.min(available_rounds);
        true
    }

    pub fn remaining(&self, reload_factor: f64) -> f64 {
        (self.reload_duration * reload_factor.max(0.0) - self.elapsed).max(0.0)
    }
}

pub fn effective_shot_dispersion(
    gun: &GunState,
    critical: &CriticalState,
) -> Result<f64, SimError> {
    let value = gun.fully_aimed_dispersion * critical_factor(critical, CriticalStat::Dispersion);
    if !value.is_finite() || value <= 0.0 {
        return Err(SimError::InvalidState(
            "effective shot dispersion must be positive",
        ));
    }
    Ok(value)
}

// CPython's integer-seeded MT19937 and 53-bit `random()` path. The source
// creates a fresh `random.Random(seed)` per shot, so the Gaussian cache never
// crosses shots and only the first Box-Muller sample is needed here.
struct PythonRandom {
    state: [u32; 624],
    index: usize,
}

impl PythonRandom {
    fn new(seed: u32) -> Self {
        let mut random = Self {
            state: [0; 624],
            index: 624,
        };
        random.init_genrand(19_650_218);
        random.init_by_array(&[seed]);
        random
    }

    fn init_genrand(&mut self, seed: u32) {
        self.state[0] = seed;
        for index in 1..624 {
            self.state[index] = 1_812_433_253_u32
                .wrapping_mul(self.state[index - 1] ^ (self.state[index - 1] >> 30))
                .wrapping_add(index as u32);
        }
        self.index = 624;
    }

    fn init_by_array(&mut self, keys: &[u32]) {
        let mut state_index = 1;
        let mut key_index = 0;
        for _ in 0..624.max(keys.len()) {
            let mixed = (self.state[state_index - 1] ^ (self.state[state_index - 1] >> 30))
                .wrapping_mul(1_664_525);
            self.state[state_index] = (self.state[state_index] ^ mixed)
                .wrapping_add(keys[key_index])
                .wrapping_add(key_index as u32);
            state_index += 1;
            key_index += 1;
            if state_index >= 624 {
                self.state[0] = self.state[623];
                state_index = 1;
            }
            if key_index >= keys.len() {
                key_index = 0;
            }
        }
        for _ in 0..623 {
            let mixed = (self.state[state_index - 1] ^ (self.state[state_index - 1] >> 30))
                .wrapping_mul(1_566_083_941);
            self.state[state_index] =
                (self.state[state_index] ^ mixed).wrapping_sub(state_index as u32);
            state_index += 1;
            if state_index >= 624 {
                self.state[0] = self.state[623];
                state_index = 1;
            }
        }
        self.state[0] = 0x8000_0000;
    }

    fn gen_u32(&mut self) -> u32 {
        const MATRIX_A: u32 = 0x9908_b0df;
        const UPPER_MASK: u32 = 0x8000_0000;
        const LOWER_MASK: u32 = 0x7fff_ffff;
        if self.index >= 624 {
            for index in 0..227 {
                let value = (self.state[index] & UPPER_MASK) | (self.state[index + 1] & LOWER_MASK);
                self.state[index] = self.state[index + 397]
                    ^ (value >> 1)
                    ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            }
            for index in 227..623 {
                let value = (self.state[index] & UPPER_MASK) | (self.state[index + 1] & LOWER_MASK);
                self.state[index] = self.state[index - 227]
                    ^ (value >> 1)
                    ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            }
            let value = (self.state[623] & UPPER_MASK) | (self.state[0] & LOWER_MASK);
            self.state[623] =
                self.state[396] ^ (value >> 1) ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            self.index = 0;
        }
        let mut value = self.state[self.index];
        self.index += 1;
        value ^= value >> 11;
        value ^= (value << 7) & 0x9d2c_5680;
        value ^= (value << 15) & 0xefc6_0000;
        value ^= value >> 18;
        value
    }

    fn random(&mut self) -> f64 {
        let high = u64::from(self.gen_u32() >> 5);
        let low = u64::from(self.gen_u32() >> 6);
        ((high << 26) + low) as f64 / 9_007_199_254_740_992.0
    }

    fn gaussian(&mut self, sigma: f64) -> f64 {
        let angle = self.random() * 2.0 * PI;
        let radius = (-2.0 * (1.0 - self.random()).ln()).sqrt();
        angle.cos() * radius * sigma
    }

    fn uniform(&mut self, minimum: f64, maximum: f64) -> f64 {
        minimum + (maximum - minimum) * self.random()
    }
}

fn barrel_direction(yaw: f64, rendered_pitch: f64) -> Vec3 {
    let horizontal = rendered_pitch.cos();
    Vec3::new(
        yaw.sin() * horizontal,
        -rendered_pitch.sin(),
        yaw.cos() * horizontal,
    )
}

/// Deterministic bounded two-sigma shot cone, bit-for-bit seeded like Python.
pub fn dispersed_barrel_angles(
    bot_id: u32,
    round_id: u64,
    fire_seq: u64,
    yaw: f64,
    rendered_pitch: f64,
    dispersion_angle: f64,
) -> Result<(f64, f64), SimError> {
    dispersed_physical_barrel_angles(
        bot_id,
        round_id,
        fire_seq,
        0,
        yaw,
        rendered_pitch,
        dispersion_angle,
    )
}

/// Deterministic physical-round cone for one edge within a burst group.
pub fn dispersed_physical_barrel_angles(
    bot_id: u32,
    round_id: u64,
    burst_group_seq: u64,
    burst_index: u16,
    yaw: f64,
    rendered_pitch: f64,
    dispersion_angle: f64,
) -> Result<(f64, f64), SimError> {
    if !dispersion_angle.is_finite() || dispersion_angle <= 0.0 {
        return Err(SimError::InvalidState("shot dispersion must be positive"));
    }
    let direction = barrel_direction(yaw, rendered_pitch);
    let seed = (((round_id & 0xffff) * 1_000_003)
        .wrapping_add(u64::from(bot_id & 0xffff) * 9_176)
        .wrapping_add((burst_group_seq & 0x7fff_ffff) * 6_113)
        .wrapping_add(u64::from(burst_index) * 3_571)
        & 0x7fff_ffff) as u32;
    let mut random = PythonRandom::new(seed);
    let mut radius = random.gaussian(dispersion_angle / 2.0).abs();
    if radius > dispersion_angle {
        radius = dispersion_angle * random.uniform(0.0, 1.0);
    }
    let azimuth = random.uniform(0.0, 2.0 * PI);

    let reference =
        if direction.x.abs() <= direction.y.abs() && direction.x.abs() <= direction.z.abs() {
            Vec3::new(1.0, 0.0, 0.0)
        } else if direction.y.abs() <= direction.z.abs() {
            Vec3::new(0.0, 1.0, 0.0)
        } else {
            Vec3::new(0.0, 0.0, 1.0)
        };
    let mut tangent = Vec3::new(
        direction.y * reference.z - direction.z * reference.y,
        direction.z * reference.x - direction.x * reference.z,
        direction.x * reference.y - direction.y * reference.x,
    );
    let tangent_length =
        (tangent.x * tangent.x + tangent.y * tangent.y + tangent.z * tangent.z).sqrt();
    tangent.x /= tangent_length;
    tangent.y /= tangent_length;
    tangent.z /= tangent_length;
    let up = Vec3::new(
        direction.y * tangent.z - direction.z * tangent.y,
        direction.z * tangent.x - direction.x * tangent.z,
        direction.x * tangent.y - direction.y * tangent.x,
    );
    let side = Vec3::new(
        tangent.x * azimuth.cos() + up.x * azimuth.sin(),
        tangent.y * azimuth.cos() + up.y * azimuth.sin(),
        tangent.z * azimuth.cos() + up.z * azimuth.sin(),
    );
    let cosine = radius.cos();
    let sine = radius.sin();
    let dispersed = Vec3::new(
        direction.x * cosine + side.x * sine,
        direction.y * cosine + side.y * sine,
        direction.z * cosine + side.z * sine,
    );
    let horizontal = dispersed.x.hypot(dispersed.z);
    Ok((
        dispersed.x.atan2(dispersed.z),
        dispersed.y.atan2(horizontal.max(1.0e-9)),
    ))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetKind {
    Bot,
    Human,
}

impl TargetKind {
    fn planner_id(self, network_id: u32) -> u32 {
        match self {
            Self::Bot => network_id,
            Self::Human => HUMAN_TARGET_ID_BASE.saturating_add(network_id),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TargetState {
    pub network_id: u32,
    pub kind: TargetKind,
    pub team: u8,
    pub alive: bool,
    pub health: u32,
    pub position: Vec3,
    pub velocity: Vec3,
    pub yaw: f64,
    pub speed: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum RecoveryMode {
    #[default]
    Drive,
    Avoid,
    Blocked,
    ReverseTurn,
    PivotRecovery,
}

impl RecoveryMode {
    fn suppresses_hull_aim(self) -> bool {
        matches!(
            self,
            Self::Avoid | Self::Blocked | Self::ReverseTurn | Self::PivotRecovery
        )
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum CombatMode {
    #[default]
    Route,
    Engage,
    BaseDefense,
    Hold,
    Retreat,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotOrder {
    pub throttle: f64,
    pub turn: f64,
    pub target_yaw: Option<f64>,
    pub aim_position: Option<Vec3>,
    pub target: Option<TargetState>,
    pub fire_allowed: bool,
    pub fire_range: f64,
    pub requested_shell_index: usize,
    pub recovery_mode: RecoveryMode,
    pub combat_mode: CombatMode,
    /// Exact local-navigation edge selected for this tick. Native motion
    /// evidence never substitutes for this baked shallow-water policy.
    pub navigation_target: Option<NavTarget>,
}

impl Default for BotOrder {
    fn default() -> Self {
        Self {
            throttle: 0.0,
            turn: 0.0,
            target_yaw: None,
            aim_position: None,
            target: None,
            fire_allowed: false,
            fire_range: 0.0,
            requested_shell_index: 0,
            recovery_mode: RecoveryMode::Drive,
            combat_mode: CombatMode::Route,
            navigation_target: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TrafficBody {
    pub network_id: u32,
    pub kind: TargetKind,
    pub team: u8,
    pub position: Vec3,
    pub velocity: Vec3,
    pub yaw: f64,
    pub half_length: f64,
    pub half_width: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TrafficSource {
    pub bot_id: u32,
    pub team: u8,
    pub position: Vec3,
    pub yaw: f64,
    pub speed: f64,
    pub half_length: f64,
    pub half_width: f64,
    pub last_drive_pitch: f64,
}

/// Continuous same-lane following plus deterministic crossing right of way.
pub fn traffic_throttle(
    source: TrafficSource,
    throttle: f64,
    turn: f64,
    target_yaw: f64,
    neighbours: &[TrafficBody],
    physics: &PhysicsProfile,
) -> (f64, bool) {
    let throttle = throttle.clamp(-1.0, 1.0);
    if throttle <= 0.01 || !matches!(source.team, 1 | 2) {
        return (throttle, false);
    }
    let own_speed = source.speed.abs();
    let sine = source.yaw.sin();
    let cosine = source.yaw.cos();
    let target_sine = target_yaw.sin();
    let target_cosine = target_yaw.cos();
    let turning_corridor = angle_delta(target_yaw, source.yaw).abs() > 0.20;
    let corridor_yaw = if turning_corridor {
        target_yaw
    } else {
        source.yaw
    };
    let mut nearest: Option<(f64, f64, bool)> = None;
    for neighbour in neighbours {
        if neighbour.team != source.team || (neighbour.position.y - source.position.y).abs() > 5.0 {
            continue;
        }
        let dx = neighbour.position.x - source.position.x;
        let dz = neighbour.position.z - source.position.z;
        let mut forward = dx * sine + dz * cosine;
        let mut lateral = (dx * cosine - dz * sine).abs();
        if turning_corridor {
            let target_forward = dx * target_sine + dz * target_cosine;
            let target_lateral = (dx * target_cosine - dz * target_sine).abs();
            if target_forward > 0.0 && target_lateral < lateral {
                forward = target_forward;
                lateral = target_lateral;
            }
        }
        let same_direction = angle_delta(neighbour.yaw, corridor_yaw).abs() < 0.65;
        let neighbour_id = neighbour.kind.planner_id(neighbour.network_id);
        if !same_direction && neighbour_id < HUMAN_TARGET_ID_BASE && neighbour_id > source.bot_id {
            continue;
        }
        let clearance = forward - source.half_length.max(0.5) - neighbour.half_length.max(0.5);
        if forward <= 0.0
            || clearance > 9.0
            || lateral > source.half_width.max(0.3) + neighbour.half_width.max(0.3) + 0.75
        {
            continue;
        }
        let leader_speed = (neighbour.velocity.x * corridor_yaw.sin()
            + neighbour.velocity.z * corridor_yaw.cos())
        .max(0.0);
        let candidate = (clearance, leader_speed, same_direction);
        if nearest
            .as_ref()
            .is_none_or(|current| candidate.0 < current.0)
        {
            nearest = Some(candidate);
        }
    }
    let Some((clearance, leader_speed, same_direction)) = nearest else {
        return (throttle, false);
    };
    if !same_direction {
        let safe_clearance = TRAFFIC_STANDSTILL_CLEARANCE.max(own_speed * TRAFFIC_HEADWAY_SECONDS);
        if clearance <= safe_clearance {
            return (0.0, true);
        }
        if own_speed > leader_speed + 0.5 {
            let limited = throttle.min(((clearance - safe_clearance) / 4.0).clamp(0.0, 1.0));
            return (limited, limited + 1.0e-9 < throttle);
        }
        return (throttle, false);
    }
    if clearance <= TRAFFIC_STANDSTILL_CLEARANCE {
        return (0.0, true);
    }
    let drive_acceleration =
        engine_force(physics, own_speed, 1.0, source.last_drive_pitch) / physics.mass.max(1.0);
    let rolling_acceleration =
        rolling_resist_force(physics, 0, turn.abs() > 0.01) / physics.mass.max(1.0);
    let gravity_acceleration = GRAVITY * source.last_drive_pitch.sin();
    if drive_acceleration <= 0.000_001 {
        return (throttle, false);
    }
    let desired_clearance = TRAFFIC_STANDSTILL_CLEARANCE + own_speed * TRAFFIC_HEADWAY_SECONDS;
    let desired_acceleration = (clearance - desired_clearance)
        / (TRAFFIC_HEADWAY_SECONDS * TRAFFIC_HEADWAY_SECONDS)
        + (leader_speed - own_speed) / TRAFFIC_HEADWAY_SECONDS;
    let required =
        (desired_acceleration - gravity_acceleration + rolling_acceleration) / drive_acceleration;
    let limited = throttle.min(required.clamp(0.0, 1.0));
    (limited, limited + 1.0e-9 < throttle)
}

pub fn combat_hull_aim(
    hull_yaw: f64,
    target_yaw: f64,
    limits: GunYawLimits,
    turn: f64,
    throttle: f64,
    recovery_mode: RecoveryMode,
    has_target: bool,
) -> (f64, f64, bool) {
    if !has_target || recovery_mode.suppresses_hull_aim() || !limits.is_limited() {
        return (turn, throttle, false);
    }
    let relative = angle_delta(target_yaw, hull_yaw);
    if limits.minimum + 0.04 <= relative && relative <= limits.maximum - 0.04 {
        return (turn, throttle, false);
    }
    let aim_turn = (angle_delta(target_yaw, hull_yaw) / 0.58).clamp(-1.0, 1.0);
    (aim_turn, 0.0, true)
}

pub fn gun_aligned(
    target_yaw: f64,
    hull_yaw: f64,
    turret_yaw: f64,
    desired_pitch: f64,
    gun_pitch: f64,
) -> bool {
    angle_delta(target_yaw, hull_yaw + turret_yaw).abs() <= 0.06
        && (desired_pitch - gun_pitch).abs() <= 0.04
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum OracleQueryKind {
    Ground,
    Motion,
    Visibility,
    Ballistic,
    Lane,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct OracleQueryId {
    pub bot_id: u32,
    pub issued_tick: u64,
    pub apply_tick: u64,
    pub kind: OracleQueryKind,
}

impl OracleQueryId {
    fn new(bot_id: u32, issued_tick: u64, kind: OracleQueryKind) -> Self {
        Self {
            bot_id,
            issued_tick,
            apply_tick: issued_tick.saturating_add(ORACLE_LATENCY_TICKS),
            kind,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GroundQuery {
    pub id: OracleQueryId,
    pub position: Vec3,
    pub yaw: f64,
    pub half_length: f64,
    pub half_width: f64,
    pub include_water_depth: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MotionQuery {
    pub id: OracleQueryId,
    pub position: Vec3,
    pub travel_yaw: f64,
    pub speed: f64,
    pub throttle: f64,
    pub turn: f64,
    pub dt_us: u64,
    pub half_length: f64,
    pub half_width: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct VisibilityQuery {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub source_position: Vec3,
    pub target_position: Vec3,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BallisticQuery {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub shell_index: usize,
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub target_velocity: Vec3,
    pub shell_speed: f64,
    pub gravity: f64,
    pub max_distance: f64,
    pub pitch_limits: (f64, f64),
    pub prefer_high_arc: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct LaneQuery {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub fire_seq: u64,
    pub shell_index: usize,
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub shot_yaw: f64,
    pub shot_pitch: f64,
    pub flight_time: f64,
    /// Frozen shell law makes the final lane proof independent of whether a
    /// cadenced ballistic refresh happens to share this exact tick.
    pub shell_speed: f64,
    pub gravity: f64,
    pub max_distance: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum OracleQueryIntent {
    Ground(GroundQuery),
    Motion(MotionQuery),
    Visibility(VisibilityQuery),
    Ballistic(BallisticQuery),
    Lane(LaneQuery),
}

#[derive(Clone, Debug, PartialEq)]
pub struct GroundReceipt {
    pub id: OracleQueryId,
    pub sample_position: Vec3,
    pub sample_yaw: f64,
    pub contains_pose: bool,
    pub supported: bool,
    pub ground_height: f64,
    pub slope_pitch: f64,
    pub water_depth: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MotionStatus {
    Clear,
    Crushed,
    Soft,
    CapCrushed,
    Hard,
}

impl MotionStatus {
    fn accepts_pose(self) -> bool {
        matches!(self, Self::Clear | Self::Crushed)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MotionReceipt {
    pub id: OracleQueryId,
    pub sample_position: Vec3,
    pub contains_pose: bool,
    pub travel_yaw: f64,
    pub status: MotionStatus,
}

#[derive(Clone, Debug, PartialEq)]
pub struct VisibilityReceipt {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub visible: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BallisticSolution {
    pub aim_position: Vec3,
    pub yaw: f64,
    /// Rendered gun pitch: negative is elevated, matching #1513 presentation.
    pub pitch: f64,
    pub flight_time: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BallisticReceipt {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub shell_index: usize,
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub target_velocity: Vec3,
    pub solution: Option<BallisticSolution>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct LaneReceipt {
    pub id: OracleQueryId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub fire_seq: u64,
    pub shell_index: usize,
    pub source_position: Vec3,
    pub target_position: Vec3,
    pub clear: bool,
    pub origin: Vec3,
    pub shot_yaw: f64,
    /// Physical projectile elevation: positive is up.
    pub shot_pitch: f64,
    pub flight_time: f64,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct OracleReceipts {
    pub ground: Option<GroundReceipt>,
    pub motion: Option<MotionReceipt>,
    pub visibility: Option<VisibilityReceipt>,
    pub ballistic: Option<BallisticReceipt>,
    pub lane: Option<LaneReceipt>,
    /// Applied `unavailable` results and exact-T+3 timeouts are explicit cache
    /// invalidations. Absence alone means that this kind was not due.
    pub failures: BTreeSet<OracleQueryId>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FireClock {
    pub elapsed_us: u64,
    pub tick_us: u64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct DrowningClock {
    pub check_us: u64,
    pub deep_water_us: u64,
    pub drowning: bool,
    pub drowned: bool,
    pub last_depth: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotSpawn {
    pub id: u32,
    pub team: u8,
    pub round_id: u64,
    pub tick: u64,
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub health: u32,
    pub fire_seq: u64,
    pub critical: CriticalState,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotState {
    pub id: u32,
    pub team: u8,
    pub round_id: u64,
    pub tick: u64,
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub speed: f64,
    pub angular_speed: f64,
    pub last_drive_pitch: f64,
    pub airborne: bool,
    pub movement_dir: i8,
    pub rotation_dir: i8,
    pub turret_yaw: f64,
    pub aim_yaw: f64,
    pub gun_pitch: f64,
    pub desired_gun_pitch: f64,
    pub gun_aligned: bool,
    pub hull_aiming: bool,
    pub health: u32,
    pub max_health: u32,
    pub display_health: u32,
    pub alive: bool,
    pub death_reason: Option<u8>,
    pub fire_seq: u64,
    pub gun: GunState,
    pub ammo: AmmoState,
    pub critical: CriticalState,
    pub fire_clock: FireClock,
    pub drowning: DrowningClock,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeathCause {
    Fire,
    Drowning,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CombatEvent {
    FireTick {
        damage: u32,
        health: u32,
    },
    FireExtinguished,
    DrowningState {
        active: bool,
        elapsed_us: u64,
        water_depth: f64,
    },
    Destroyed {
        cause: DeathCause,
        display_health: u32,
        reason: u8,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct SourceShotId {
    pub round_id: u64,
    pub source_id: u32,
    pub fire_seq: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ProjectileLaunchPose {
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileLaunchEvent {
    pub shot_id: SourceShotId,
    pub target_kind: TargetKind,
    pub target_id: u32,
    pub shell_index: usize,
    pub origin: Vec3,
    pub velocity: Vec3,
    pub gravity: f64,
    pub max_distance: f64,
    pub max_time_ms: u32,
    pub shot_yaw: f64,
    pub shot_pitch: f64,
    pub flight_time: f64,
    pub burst_group_seq: u64,
    pub burst_index: u16,
    pub burst_count: u16,
    pub launch_time_us: u64,
    pub launch_pose: ProjectileLaunchPose,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PoseEvent {
    pub bot_id: u32,
    pub tick: u64,
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub speed: f64,
    pub movement_dir: i8,
    pub rotation_dir: i8,
    pub turret_yaw: f64,
    pub gun_pitch: f64,
    pub gun_aligned: bool,
    pub health: u32,
    pub alive: bool,
    pub fire_seq: u64,
    pub ammo: AmmoSnapshot,
    pub clip_size: u16,
    pub clip: u16,
    pub reload_time: f64,
    pub reload_duration: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum BotEvent {
    Combat(CombatEvent),
    Projectile(ProjectileLaunchEvent),
    Pose(PoseEvent),
}

#[derive(Clone, Debug, PartialEq)]
pub struct TickOutput {
    pub tick: u64,
    pub queries: Vec<OracleQueryIntent>,
    pub events: Vec<BotEvent>,
}

pub struct TickInput<'a> {
    pub tick: u64,
    pub dt_us: u64,
    pub order: &'a BotOrder,
    pub receipts: &'a OracleReceipts,
    pub neighbours: &'a [TrafficBody],
    pub navigation_graph: Option<&'a NavGraph>,
}

#[derive(Clone, Copy, Debug)]
struct AppliedControl {
    throttle: f64,
    turn: f64,
    travel_yaw: f64,
}

#[derive(Clone, Copy, Debug)]
struct SampledBurstPose {
    launch: ProjectileLaunchPose,
    aim_yaw: f64,
    gun_pitch: f64,
}

impl SampledBurstPose {
    fn from_state(state: &BotState) -> Self {
        Self {
            launch: ProjectileLaunchPose {
                position: state.position,
                yaw: state.yaw,
                pitch: state.pitch,
                roll: state.roll,
            },
            aim_yaw: state.aim_yaw,
            gun_pitch: state.gun_pitch,
        }
    }

    fn at_logical_time(
        start: Self,
        end: Self,
        start_time_us: u64,
        end_time_us: u64,
        due_time_us: u64,
    ) -> Self {
        let duration = end_time_us.saturating_sub(start_time_us);
        let ratio = if duration == 0 {
            1.0
        } else {
            due_time_us.saturating_sub(start_time_us).min(duration) as f64 / duration as f64
        };
        let interpolate = |left: f64, right: f64| left + (right - left) * ratio;
        let interpolate_angle =
            |left: f64, right: f64| wrapped(left + angle_delta(right, left) * ratio);
        Self {
            launch: ProjectileLaunchPose {
                position: Vec3::new(
                    interpolate(start.launch.position.x, end.launch.position.x),
                    interpolate(start.launch.position.y, end.launch.position.y),
                    interpolate(start.launch.position.z, end.launch.position.z),
                ),
                yaw: interpolate_angle(start.launch.yaw, end.launch.yaw),
                pitch: interpolate_angle(start.launch.pitch, end.launch.pitch),
                roll: interpolate_angle(start.launch.roll, end.launch.roll),
            },
            aim_yaw: interpolate_angle(start.aim_yaw, end.aim_yaw),
            gun_pitch: interpolate(start.gun_pitch, end.gun_pitch),
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct ActiveBotBurst {
    group_seq: u64,
    count: u16,
    shell_index: usize,
    target_kind: TargetKind,
    target_id: u32,
    flight_time: f64,
    muzzle_offset: Vec3,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct MapEnvelope {
    bounds: [f64; 4],
    half_width: f64,
    half_length: f64,
}

impl MapEnvelope {
    fn new(bounds: [f64; 4], half_width: f64, half_length: f64) -> Result<Self, SimError> {
        if !bounds.into_iter().all(f64::is_finite)
            || !half_width.is_finite()
            || !half_length.is_finite()
            || bounds[0] >= bounds[2]
            || bounds[1] >= bounds[3]
            || half_width <= 0.0
            || half_length <= 0.0
            || half_width * 2.0 > bounds[2] - bounds[0]
            || half_length * 2.0 > bounds[3] - bounds[1]
        {
            return Err(SimError::InvalidState("bot map envelope"));
        }
        Ok(Self {
            bounds,
            half_width,
            half_length,
        })
    }

    fn violation(self, position: Vec3, yaw: f64) -> [f64; 4] {
        let (sine, cosine) = yaw.sin_cos();
        let extent_x = cosine.abs() * self.half_width + sine.abs() * self.half_length;
        let extent_z = sine.abs() * self.half_width + cosine.abs() * self.half_length;
        [
            (self.bounds[0] - (position.x - extent_x)).max(0.0),
            (position.x + extent_x - self.bounds[2]).max(0.0),
            (self.bounds[1] - (position.z - extent_z)).max(0.0),
            (position.z + extent_z - self.bounds[3]).max(0.0),
        ]
    }

    fn permits(self, from_position: Vec3, from_yaw: f64, to_position: Vec3, to_yaw: f64) -> bool {
        let before = self.violation(from_position, from_yaw);
        let after = self.violation(to_position, to_yaw);
        after
            .into_iter()
            .zip(before)
            .all(|(next, previous)| next <= previous + 1.0e-9)
    }
}

fn rotate_vehicle_offset(offset: Vec3, pose: ProjectileLaunchPose) -> Vec3 {
    let (roll_sine, roll_cosine) = pose.roll.sin_cos();
    let rolled = Vec3::new(
        offset.x * roll_cosine - offset.y * roll_sine,
        offset.x * roll_sine + offset.y * roll_cosine,
        offset.z,
    );
    let (pitch_sine, pitch_cosine) = pose.pitch.sin_cos();
    let pitched = Vec3::new(
        rolled.x,
        rolled.y * pitch_cosine - rolled.z * pitch_sine,
        rolled.y * pitch_sine + rolled.z * pitch_cosine,
    );
    let (yaw_sine, yaw_cosine) = pose.yaw.sin_cos();
    Vec3::new(
        pitched.x * yaw_cosine + pitched.z * yaw_sine,
        pitched.y,
        -pitched.x * yaw_sine + pitched.z * yaw_cosine,
    )
}

fn unrotate_vehicle_offset(offset: Vec3, pose: ProjectileLaunchPose) -> Vec3 {
    let (yaw_sine, yaw_cosine) = pose.yaw.sin_cos();
    let unyawed = Vec3::new(
        offset.x * yaw_cosine - offset.z * yaw_sine,
        offset.y,
        offset.x * yaw_sine + offset.z * yaw_cosine,
    );
    let (pitch_sine, pitch_cosine) = pose.pitch.sin_cos();
    let unpitched = Vec3::new(
        unyawed.x,
        unyawed.y * pitch_cosine + unyawed.z * pitch_sine,
        -unyawed.y * pitch_sine + unyawed.z * pitch_cosine,
    );
    let (roll_sine, roll_cosine) = pose.roll.sin_cos();
    Vec3::new(
        unpitched.x * roll_cosine + unpitched.y * roll_sine,
        -unpitched.x * roll_sine + unpitched.y * roll_cosine,
        unpitched.z,
    )
}

#[derive(Clone, Debug)]
pub struct BotSimulator {
    descriptor: VehicleDescriptor,
    profile: BotProfile,
    state: BotState,
    ram_push: Vec3,
    physical_burst: PhysicalBurstDescriptor,
    physical_burst_clock: PhysicalBurstClock,
    active_physical_burst: Option<ActiveBotBurst>,
    map_envelope: Option<MapEnvelope>,
    oracle_cache: OracleReceipts,
}

impl BotSimulator {
    pub fn new(
        descriptor: VehicleDescriptor,
        profile: BotProfile,
        spawn: BotSpawn,
    ) -> Result<Self, SimError> {
        validate_descriptor(&descriptor, &profile)?;
        if spawn.id == 0 {
            return Err(SimError::InvalidState("bot id must be positive"));
        }
        if !matches!(spawn.team, 1 | 2) {
            return Err(SimError::InvalidState("bot team must be one or two"));
        }
        if !spawn.position.is_finite()
            || !spawn.yaw.is_finite()
            || !spawn.pitch.is_finite()
            || !spawn.roll.is_finite()
        {
            return Err(SimError::NonFinite("spawn pose"));
        }
        let health = spawn.health.min(descriptor.max_health);
        let gun = GunState::new(&descriptor.gun, spawn.fire_seq)?;
        let ammo = AmmoState::new(&descriptor, &profile);
        let state = BotState {
            id: spawn.id,
            team: spawn.team,
            round_id: spawn.round_id,
            tick: spawn.tick,
            position: spawn.position,
            yaw: wrapped(spawn.yaw),
            pitch: spawn.pitch,
            roll: spawn.roll,
            speed: 0.0,
            angular_speed: 0.0,
            last_drive_pitch: 0.0,
            airborne: false,
            movement_dir: 0,
            rotation_dir: 0,
            turret_yaw: 0.0,
            aim_yaw: wrapped(spawn.yaw),
            gun_pitch: 0.0,
            desired_gun_pitch: 0.0,
            gun_aligned: false,
            hull_aiming: false,
            health,
            max_health: descriptor.max_health,
            display_health: health,
            alive: health > 0,
            death_reason: None,
            fire_seq: spawn.fire_seq,
            gun,
            ammo,
            critical: spawn.critical,
            fire_clock: FireClock::default(),
            drowning: DrowningClock::default(),
        };
        Ok(Self {
            descriptor,
            profile,
            physical_burst: PhysicalBurstDescriptor {
                count: 1,
                interval_seconds: 0.0,
            },
            physical_burst_clock: PhysicalBurstClock::new(state.tick, state.fire_seq),
            active_physical_burst: None,
            map_envelope: None,
            oracle_cache: OracleReceipts::default(),
            state,
            ram_push: Vec3::ZERO,
        })
    }

    pub fn descriptor(&self) -> &VehicleDescriptor {
        &self.descriptor
    }

    pub fn profile(&self) -> &BotProfile {
        &self.profile
    }

    pub fn state(&self) -> &BotState {
        &self.state
    }

    pub fn state_mut(&mut self) -> &mut BotState {
        &mut self.state
    }

    /// Freeze the graph bounds and the exact donated ramming hull used for
    /// full-chassis edge containment. Exact replay is idempotent.
    pub fn install_map_envelope(
        &mut self,
        bounds: [f64; 4],
        half_width: f64,
        half_length: f64,
    ) -> Result<bool, SimError> {
        let envelope = MapEnvelope::new(bounds, half_width, half_length)?;
        if let Some(active) = self.map_envelope {
            return if active == envelope {
                Ok(false)
            } else {
                Err(SimError::InvalidState("conflicting bot map envelope"))
            };
        }
        if envelope
            .violation(self.state.position, self.state.yaw)
            .into_iter()
            .any(|value| value > 1.0e-9)
        {
            return Err(SimError::InvalidState(
                "bot spawn chassis is outside navigation bounds",
            ));
        }
        self.map_envelope = Some(envelope);
        Ok(true)
    }

    fn admitted_yaw(&self, candidate: f64) -> f64 {
        if self.map_envelope.is_none_or(|envelope| {
            envelope.permits(
                self.state.position,
                self.state.yaw,
                self.state.position,
                candidate,
            )
        }) {
            candidate
        } else {
            self.state.yaw
        }
    }

    fn admitted_translation(&self, delta_x: f64, delta_z: f64) -> (f64, f64) {
        let Some(envelope) = self.map_envelope else {
            return (delta_x, delta_z);
        };
        let start = self.state.position;
        let combined = Vec3::new(start.x + delta_x, start.y, start.z + delta_z);
        if envelope.permits(start, self.state.yaw, combined, self.state.yaw) {
            return (delta_x, delta_z);
        }
        let x_only = Vec3::new(start.x + delta_x, start.y, start.z);
        let admitted_x = if envelope.permits(start, self.state.yaw, x_only, self.state.yaw) {
            delta_x
        } else {
            0.0
        };
        let after_x = Vec3::new(start.x + admitted_x, start.y, start.z);
        let z_only = Vec3::new(after_x.x, start.y, start.z + delta_z);
        let admitted_z = if envelope.permits(after_x, self.state.yaw, z_only, self.state.yaw) {
            delta_z
        } else {
            0.0
        };
        (admitted_x, admitted_z)
    }

    /// Install the donated physical-round cadence for this Bot's current gun.
    ///
    /// Construction intentionally remains single-shot. A descriptor can be
    /// replaced between groups, but never while an automatic tail is armed.
    pub fn install_physical_burst(
        &mut self,
        descriptor: PhysicalBurstDescriptor,
    ) -> Result<(), PhysicalBurstError> {
        let descriptor =
            PhysicalBurstDescriptor::new(descriptor.count, descriptor.interval_seconds)?;
        if self.physical_burst_clock.active() || self.active_physical_burst.is_some() {
            return Err(PhysicalBurstError::AlreadyActive);
        }
        self.physical_burst = descriptor;
        Ok(())
    }

    pub fn physical_burst_descriptor(&self) -> PhysicalBurstDescriptor {
        self.physical_burst
    }

    pub fn physical_burst_active(&self) -> bool {
        self.physical_burst_clock.active()
    }

    /// Return the complete horizontal velocity used by ramming authority.
    pub fn ram_velocity(&self) -> Vec3 {
        if !self.state.alive {
            return Vec3::ZERO;
        }
        Vec3::new(
            self.state.yaw.sin() * self.state.speed + self.ram_push.x,
            0.0,
            self.state.yaw.cos() * self.state.speed + self.ram_push.z,
        )
    }

    /// Apply one server-owned inverse-mass collision response.
    pub fn apply_ram_delta(
        &mut self,
        correction_x: f64,
        correction_z: f64,
        velocity_x: f64,
        velocity_z: f64,
    ) -> Result<(), SimError> {
        if ![correction_x, correction_z, velocity_x, velocity_z]
            .into_iter()
            .all(f64::is_finite)
        {
            return Err(SimError::NonFinite("ram response"));
        }
        let (correction_x, correction_z) = self.admitted_translation(correction_x, correction_z);
        let next_x = self.state.position.x + correction_x;
        let next_z = self.state.position.z + correction_z;
        if next_x.abs() > 2_000.0 || next_z.abs() > 2_000.0 {
            return Err(SimError::InvalidState("ram response leaves world bounds"));
        }
        let forward_x = self.state.yaw.sin();
        let forward_z = self.state.yaw.cos();
        let forward_impulse = velocity_x * forward_x + velocity_z * forward_z;
        let applied_forward = if forward_impulse * self.state.speed < 0.0 {
            if forward_impulse.abs() >= self.state.speed.abs() {
                -self.state.speed
            } else {
                forward_impulse
            }
        } else {
            0.0
        };
        let next_speed = self.state.speed + applied_forward;
        let next_push_x = self.ram_push.x + velocity_x - applied_forward * forward_x;
        let next_push_z = self.ram_push.z + velocity_z - applied_forward * forward_z;
        if ![next_speed, next_push_x, next_push_z]
            .into_iter()
            .all(f64::is_finite)
            || next_speed.abs() > 200.0
            || next_push_x.abs() > 400.0
            || next_push_z.abs() > 400.0
        {
            return Err(SimError::InvalidState("ram response velocity is invalid"));
        }
        self.state.position.x = next_x;
        self.state.position.z = next_z;
        self.state.speed = next_speed;
        self.ram_push.x = next_push_x;
        self.ram_push.z = next_push_z;
        Ok(())
    }

    pub fn ignite(&mut self) {
        self.state.critical.on_fire = true;
        self.state.fire_clock = FireClock::default();
    }

    /// Advance one canonical fixed tick. Errors roll the bot back atomically.
    pub fn step(&mut self, input: TickInput<'_>) -> Result<TickOutput, SimError> {
        let expected_tick = self.state.tick.saturating_add(1);
        if input.tick != expected_tick {
            return Err(SimError::InvalidTick {
                expected: expected_tick,
                actual: input.tick,
            });
        }
        let expected_dt = fixed_dt_us(input.tick);
        if input.dt_us != expected_dt {
            return Err(SimError::InvalidTickDelta {
                tick: input.tick,
                expected_us: expected_dt,
                actual_us: input.dt_us,
            });
        }
        validate_order(input.order)?;
        let checkpoint = self.state.clone();
        let ram_push_checkpoint = self.ram_push;
        let physical_burst_clock_checkpoint = self.physical_burst_clock.clone();
        let active_physical_burst_checkpoint = self.active_physical_burst;
        let oracle_cache_checkpoint = self.oracle_cache.clone();
        match self.step_inner(input) {
            Ok(output) => Ok(output),
            Err(error) => {
                self.state = checkpoint;
                self.ram_push = ram_push_checkpoint;
                self.physical_burst_clock = physical_burst_clock_checkpoint;
                self.active_physical_burst = active_physical_burst_checkpoint;
                self.oracle_cache = oracle_cache_checkpoint;
                Err(error)
            }
        }
    }

    fn step_inner(&mut self, input: TickInput<'_>) -> Result<TickOutput, SimError> {
        let step_start_pose = SampledBurstPose::from_state(&self.state);
        let step_start_time_us = time_us_at_tick(input.tick.saturating_sub(1));
        let step_end_time_us = time_us_at_tick(input.tick);
        self.state.tick = input.tick;
        self.absorb_oracle_receipts(input.receipts, input.tick);
        let mut events = Vec::new();
        if self.state.alive {
            self.advance_fire(input.dt_us, &mut events);
        }

        let ground = self.cached_ground(input.tick).cloned();
        if self.state.alive {
            if let Some(receipt) = ground.as_ref() {
                if receipt.contains_pose && receipt.supported {
                    self.state.position.y = receipt.ground_height;
                    self.state.last_drive_pitch = receipt.slope_pitch;
                }
            }
            self.advance_drowning(input.dt_us, ground.as_ref(), &mut events);
        }

        if self.physical_burst_clock.active()
            && (!self.state.alive || firing_hard_gated(&self.state.critical))
        {
            self.cancel_active_physical_burst();
        }
        let burst_active_at_tick_start = self.physical_burst_clock.active();

        let mut control = AppliedControl {
            throttle: 0.0,
            turn: 0.0,
            travel_yaw: self.state.yaw,
        };
        let mut visible = false;
        let mut ballistic_owned = None;
        let reload_factor = critical_factor(&self.state.critical, CriticalStat::Reload);
        let requested = self
            .state
            .gun
            .shell_index(input.order.requested_shell_index);
        if self.state.alive {
            self.state.gun.rescale_reload(reload_factor);
            self.state.gun.tick(input.dt_us as f64 / 1_000_000.0);
            if !burst_active_at_tick_start {
                let available_rounds = self.state.ammo.total_remaining();
                self.state
                    .gun
                    .complete_physical_reload(reload_factor, available_rounds);
                self.state
                    .ammo
                    .stage(requested, self.state.gun.ready(reload_factor));
            }

            control = self.resolve_control(input.order, input.neighbours);
            let motion = self.cached_motion(control, input.tick).cloned();
            self.advance_motion(
                input.dt_us,
                control,
                ground.as_ref(),
                motion.as_ref(),
                input.navigation_graph,
                input.order.navigation_target,
            );
            self.advance_ram_push(input.dt_us);
            ballistic_owned = self
                .cached_ballistic(
                    input.order.target.as_ref(),
                    self.state.ammo.loaded(),
                    input.tick,
                )
                .cloned();
            self.advance_aim(input.order, ballistic_owned.as_ref());
            visible = self.cached_visibility(input.order.target.as_ref(), input.tick);
        }

        let step_end_pose = SampledBurstPose::from_state(&self.state);
        self.advance_active_physical_burst(
            input.tick,
            requested,
            reload_factor,
            step_start_time_us,
            step_end_time_us,
            step_start_pose,
            step_end_pose,
            &mut events,
        )?;

        if self.state.alive {
            if self.fire_gate(
                input.order,
                visible,
                ballistic_owned.as_ref(),
                reload_factor,
            ) {
                if let Some(receipt) =
                    self.valid_lane(input.receipts, input.order.target.as_ref(), input.tick)
                {
                    if receipt.clear {
                        let launch =
                            self.start_physical_fire(receipt, reload_factor, step_end_pose)?;
                        events.push(BotEvent::Projectile(launch));
                    }
                }
            }
        }

        let queries = if self.state.alive {
            self.build_queries(
                input.tick,
                input.dt_us,
                input.order,
                control,
                visible,
                ballistic_owned.as_ref(),
            )?
        } else {
            Vec::new()
        };
        events.push(BotEvent::Pose(self.pose_event()));
        Ok(TickOutput {
            tick: input.tick,
            queries,
            events,
        })
    }

    fn advance_ram_push(&mut self, dt_us: u64) {
        if self.ram_push.x == 0.0 && self.ram_push.z == 0.0 {
            return;
        }
        let dt = dt_us as f64 / 1_000_000.0;
        let requested_x = self.ram_push.x * dt;
        let requested_z = self.ram_push.z * dt;
        let (applied_x, applied_z) = self.admitted_translation(requested_x, requested_z);
        self.state.position.x = (self.state.position.x + applied_x).clamp(-2_000.0, 2_000.0);
        self.state.position.z = (self.state.position.z + applied_z).clamp(-2_000.0, 2_000.0);
        if applied_x != requested_x {
            self.ram_push.x = 0.0;
        }
        if applied_z != requested_z {
            self.ram_push.z = 0.0;
        }
        let decay = 0.90_f64.powf(dt * 60.0);
        self.ram_push.x *= decay;
        self.ram_push.z *= decay;
        if self.ram_push.x.abs() < 1.0e-6 {
            self.ram_push.x = 0.0;
        }
        if self.ram_push.z.abs() < 1.0e-6 {
            self.ram_push.z = 0.0;
        }
    }

    fn valid_receipt_id(&self, id: OracleQueryId, tick: u64, kind: OracleQueryKind) -> bool {
        id.bot_id == self.state.id
            && id.kind == kind
            && id.apply_tick == tick
            && id.issued_tick.saturating_add(ORACLE_LATENCY_TICKS) == tick
    }

    fn receipt_failed(&self, receipts: &OracleReceipts, tick: u64, kind: OracleQueryKind) -> bool {
        receipts
            .failures
            .iter()
            .any(|id| self.valid_receipt_id(*id, tick, kind))
    }

    fn absorb_oracle_receipts(&mut self, receipts: &OracleReceipts, tick: u64) {
        for kind in [
            OracleQueryKind::Ground,
            OracleQueryKind::Motion,
            OracleQueryKind::Visibility,
            OracleQueryKind::Ballistic,
        ] {
            if self.receipt_failed(receipts, tick, kind) {
                match kind {
                    OracleQueryKind::Ground => self.oracle_cache.ground = None,
                    OracleQueryKind::Motion => self.oracle_cache.motion = None,
                    OracleQueryKind::Visibility => self.oracle_cache.visibility = None,
                    OracleQueryKind::Ballistic => self.oracle_cache.ballistic = None,
                    OracleQueryKind::Lane => unreachable!(),
                }
            }
        }

        if !self.receipt_failed(receipts, tick, OracleQueryKind::Ground) {
            if let Some(receipt) = &receipts.ground {
                if self.ground_receipt_well_formed(receipt, tick) {
                    self.oracle_cache.ground = Some(receipt.clone());
                } else {
                    self.oracle_cache.ground = None;
                }
            }
        }
        if !self.receipt_failed(receipts, tick, OracleQueryKind::Motion) {
            if let Some(receipt) = &receipts.motion {
                if self.motion_receipt_well_formed(receipt, tick) {
                    self.oracle_cache.motion = Some(receipt.clone());
                } else {
                    self.oracle_cache.motion = None;
                }
            }
        }
        if !self.receipt_failed(receipts, tick, OracleQueryKind::Visibility) {
            if let Some(receipt) = &receipts.visibility {
                if self.visibility_receipt_well_formed(receipt, tick) {
                    self.oracle_cache.visibility = Some(receipt.clone());
                } else {
                    self.oracle_cache.visibility = None;
                }
            }
        }
        if !self.receipt_failed(receipts, tick, OracleQueryKind::Ballistic) {
            if let Some(receipt) = &receipts.ballistic {
                if self.ballistic_receipt_well_formed(receipt, tick) {
                    self.oracle_cache.ballistic = Some(receipt.clone());
                } else {
                    self.oracle_cache.ballistic = None;
                }
            }
        }
    }

    fn ground_receipt_well_formed(&self, receipt: &GroundReceipt, tick: u64) -> bool {
        self.valid_receipt_id(receipt.id, tick, OracleQueryKind::Ground)
            && receipt.sample_position.is_finite()
            && receipt.sample_yaw.is_finite()
            && receipt.ground_height.is_finite()
            && receipt.slope_pitch.is_finite()
            && receipt.water_depth.is_none_or(f64::is_finite)
    }

    fn motion_receipt_well_formed(&self, receipt: &MotionReceipt, tick: u64) -> bool {
        self.valid_receipt_id(receipt.id, tick, OracleQueryKind::Motion)
            && receipt.sample_position.is_finite()
            && receipt.travel_yaw.is_finite()
    }

    fn visibility_receipt_well_formed(&self, receipt: &VisibilityReceipt, tick: u64) -> bool {
        self.valid_receipt_id(receipt.id, tick, OracleQueryKind::Visibility)
            && receipt.target_id != 0
            && receipt.source_position.is_finite()
            && receipt.target_position.is_finite()
    }

    fn ballistic_receipt_well_formed(&self, receipt: &BallisticReceipt, tick: u64) -> bool {
        self.valid_receipt_id(receipt.id, tick, OracleQueryKind::Ballistic)
            && receipt.target_id != 0
            && receipt.shell_index < self.descriptor.gun.shells.len()
            && receipt.source_position.is_finite()
            && receipt.target_position.is_finite()
            && receipt.target_velocity.is_finite()
            && receipt.solution.as_ref().is_none_or(|solution| {
                solution.aim_position.is_finite()
                    && solution.yaw.is_finite()
                    && solution.pitch.is_finite()
                    && solution.flight_time.is_finite()
                    && solution.flight_time > 0.0
                    && solution.flight_time <= 20.0
                    && solution.pitch >= self.descriptor.gun.pitch_limits.0 - 0.0001
                    && solution.pitch <= self.descriptor.gun.pitch_limits.1 + 0.0001
            })
    }

    fn cached_ground(&self, tick: u64) -> Option<&GroundReceipt> {
        self.oracle_cache.ground.as_ref().filter(|receipt| {
            tick <= receipt
                .id
                .apply_tick
                .saturating_add(NATIVE_ACTION_CADENCE_TICKS)
                && pose_inside_ground_envelope(
                    receipt.sample_position,
                    receipt.sample_yaw,
                    self.state.position,
                    self.state.yaw,
                )
        })
    }

    fn cached_motion(&self, control: AppliedControl, tick: u64) -> Option<&MotionReceipt> {
        self.oracle_cache.motion.as_ref().filter(|receipt| {
            tick <= receipt
                .id
                .apply_tick
                .saturating_add(NATIVE_ACTION_CADENCE_TICKS)
                && pose_inside_motion_envelope(
                    receipt.sample_position,
                    receipt.travel_yaw,
                    self.state.position,
                    control.travel_yaw,
                )
        })
    }

    fn cached_visibility(&self, target: Option<&TargetState>, tick: u64) -> bool {
        let Some((receipt, target)) = self.oracle_cache.visibility.as_ref().zip(target) else {
            return false;
        };
        let source = Vec3::new(
            self.state.position.x,
            self.state.position.y + 1.5,
            self.state.position.z,
        );
        tick <= receipt
            .id
            .apply_tick
            .saturating_add(VISIBILITY_CADENCE_TICKS)
            && receipt.target_kind == target.kind
            && receipt.target_id == target.network_id
            && distance_3d(receipt.source_position, source) <= VISIBILITY_POSITION_ENVELOPE_METRES
            && distance_3d(receipt.target_position, target.position)
                <= VISIBILITY_POSITION_ENVELOPE_METRES
            && receipt.visible
    }

    fn cached_ballistic(
        &self,
        target: Option<&TargetState>,
        shell_index: usize,
        tick: u64,
    ) -> Option<&BallisticSolution> {
        let (receipt, target) = self.oracle_cache.ballistic.as_ref().zip(target)?;
        let source = Vec3::new(
            self.state.position.x,
            self.state.position.y + 1.5,
            self.state.position.z,
        );
        if tick
            > receipt
                .id
                .apply_tick
                .saturating_add(NATIVE_ACTION_CADENCE_TICKS)
            || receipt.target_kind != target.kind
            || receipt.target_id != target.network_id
            || receipt.shell_index != shell_index
            || distance_3d(receipt.source_position, source) > BALLISTIC_SOURCE_ENVELOPE_METRES
            || distance_3d(receipt.target_position, target.position)
                > BALLISTIC_TARGET_ENVELOPE_METRES
            || distance_3d(receipt.target_velocity, target.velocity)
                > BALLISTIC_VELOCITY_ENVELOPE_MPS
        {
            return None;
        }
        receipt.solution.as_ref()
    }

    fn valid_lane<'a>(
        &self,
        receipts: &'a OracleReceipts,
        target: Option<&TargetState>,
        tick: u64,
    ) -> Option<&'a LaneReceipt> {
        if self.receipt_failed(receipts, tick, OracleQueryKind::Lane) {
            return None;
        }
        let (receipt, target) = receipts.lane.as_ref().zip(target)?;
        let next_fire_seq = self.state.fire_seq.saturating_add(1);
        let source = Vec3::new(
            self.state.position.x,
            self.state.position.y + 1.5,
            self.state.position.z,
        );
        (self.valid_receipt_id(receipt.id, tick, OracleQueryKind::Lane)
            && receipt.target_kind == target.kind
            && receipt.target_id == target.network_id
            && receipt.fire_seq == next_fire_seq
            && receipt.shell_index == self.state.ammo.loaded()
            && receipt.source_position.is_finite()
            && receipt.target_position.is_finite()
            && distance_3d(receipt.source_position, source) <= LANE_SOURCE_ENVELOPE_METRES
            && distance_3d(receipt.target_position, target.position) <= LANE_TARGET_ENVELOPE_METRES
            && receipt.origin.is_finite()
            && receipt.shot_yaw.is_finite()
            && receipt.shot_pitch.is_finite()
            && angle_delta(receipt.shot_yaw, self.state.aim_yaw).abs() <= LANE_YAW_ENVELOPE_RADIANS
            && (receipt.shot_pitch + self.state.gun_pitch).abs() <= LANE_PITCH_ENVELOPE_RADIANS
            && receipt.flight_time.is_finite()
            && receipt.flight_time > 0.0
            && receipt.flight_time <= 20.0)
            .then_some(receipt)
    }

    fn advance_fire(&mut self, dt_us: u64, events: &mut Vec<BotEvent>) {
        if !self.state.critical.on_fire || self.state.health == 0 || dt_us == 0 {
            return;
        }
        self.state.fire_clock.elapsed_us = self
            .state
            .fire_clock
            .elapsed_us
            .saturating_add(dt_us)
            .min(FIRE_DURATION_US);
        if self.state.fire_clock.elapsed_us >= FIRE_DURATION_US {
            // Match the Python ordering: extinguish first, then allow the same
            // frame to complete the final one-second damage interval.
            self.state.critical.on_fire = false;
            events.push(BotEvent::Combat(CombatEvent::FireExtinguished));
        }
        self.state.fire_clock.tick_us = self.state.fire_clock.tick_us.saturating_add(dt_us);
        while self.state.fire_clock.tick_us >= FIRE_TICK_US {
            self.state.fire_clock.tick_us -= FIRE_TICK_US;
            let damage = ((f64::from(self.state.max_health) * FIRE_DAMAGE_FRACTION_PER_SECOND)
                as u32)
                .max(1);
            self.state.health = self.state.health.saturating_sub(damage);
            self.state.display_health = self.state.health;
            events.push(BotEvent::Combat(CombatEvent::FireTick {
                damage,
                health: self.state.health,
            }));
            if self.state.health == 0 {
                self.kill(DeathCause::Fire, 1, 0, events);
                break;
            }
        }
    }

    fn advance_drowning(
        &mut self,
        dt_us: u64,
        ground: Option<&GroundReceipt>,
        events: &mut Vec<BotEvent>,
    ) {
        if !self.state.alive || self.state.health == 0 || dt_us == 0 {
            return;
        }
        self.state.drowning.check_us = self.state.drowning.check_us.saturating_add(dt_us);
        if self.state.drowning.check_us < DROWNING_PROBE_US {
            return;
        }
        let Some(depth) = ground.and_then(|receipt| receipt.water_depth) else {
            // A missing oracle result cannot clear an existing drowning state.
            // Retain at most the Python probe's 0.5 s accepted slice.
            self.state.drowning.check_us = self.state.drowning.check_us.min(500_000);
            return;
        };
        let elapsed = self.state.drowning.check_us.min(500_000);
        self.state.drowning.check_us = 0;
        self.state.drowning.last_depth = Some(depth);
        if depth <= DROWNING_DEPTH_METRES {
            let changed = self.state.drowning.drowning || self.state.drowning.deep_water_us != 0;
            self.state.drowning.deep_water_us = 0;
            self.state.drowning.drowning = false;
            if changed {
                events.push(BotEvent::Combat(CombatEvent::DrowningState {
                    active: false,
                    elapsed_us: 0,
                    water_depth: depth,
                }));
            }
            return;
        }
        self.state.drowning.drowning = true;
        self.state.drowning.deep_water_us =
            self.state.drowning.deep_water_us.saturating_add(elapsed);
        events.push(BotEvent::Combat(CombatEvent::DrowningState {
            active: true,
            elapsed_us: self.state.drowning.deep_water_us,
            water_depth: depth,
        }));
        if self.state.drowning.deep_water_us > DROWNING_DURATION_US {
            self.state.drowning.drowned = true;
            self.state.drowning.drowning = false;
            for name in &self.descriptor.module_names {
                self.state
                    .critical
                    .devices
                    .insert(name.clone(), DeviceCondition::Destroyed);
            }
            self.state
                .critical
                .crew_ko
                .extend(self.descriptor.crew_roster.iter().cloned());
            self.state.critical.on_fire = false;
            let display_health = self.state.health;
            self.kill(
                DeathCause::Drowning,
                DROWNING_DEATH_REASON,
                display_health,
                events,
            );
        }
    }

    fn kill(
        &mut self,
        cause: DeathCause,
        reason: u8,
        display_health: u32,
        events: &mut Vec<BotEvent>,
    ) {
        self.state.health = 0;
        self.state.alive = false;
        self.state.display_health = display_health;
        self.state.death_reason = Some(reason);
        self.state.speed = 0.0;
        self.state.angular_speed = 0.0;
        self.state.movement_dir = 0;
        self.state.rotation_dir = 0;
        self.state.gun_aligned = false;
        events.push(BotEvent::Combat(CombatEvent::Destroyed {
            cause,
            display_health,
            reason,
        }));
    }

    fn resolve_control(&mut self, order: &BotOrder, _neighbours: &[TrafficBody]) -> AppliedControl {
        let mut throttle = order.throttle.clamp(-1.0, 1.0);
        let mut turn = order.turn.clamp(-1.0, 1.0);
        let target_yaw = order
            .aim_position
            .or_else(|| order.target.as_ref().map(|target| target.position))
            .map(|position| {
                (position.x - self.state.position.x).atan2(position.z - self.state.position.z)
            })
            .unwrap_or(self.state.yaw);
        let (aim_turn, aim_throttle, hull_aiming) = combat_hull_aim(
            self.state.yaw,
            target_yaw,
            self.descriptor.gun.yaw_limits,
            turn,
            throttle,
            order.recovery_mode,
            order.target.is_some() && order.combat_mode != CombatMode::BaseDefense,
        );
        turn = aim_turn;
        throttle = aim_throttle;
        self.state.hull_aiming = hull_aiming;
        if movement_hard_gated(&self.state.critical) {
            throttle = 0.0;
            turn = 0.0;
        } else if throttle.abs() > 0.01 {
            throttle *= critical_factor(&self.state.critical, CriticalStat::Mobility);
        }
        // Simultaneous contact resolution owns friendly hull interaction and
        // friendly ram damage is disabled. A second predictive headway gate
        // turns harmless same-lane traffic into visible stop/go pulses, so the
        // fixed-tick simulator preserves the planner's last valid throttle.
        let travel_yaw = if throttle >= 0.0 {
            self.state.yaw
        } else {
            self.state.yaw + PI
        };
        AppliedControl {
            throttle,
            turn,
            travel_yaw: wrapped(travel_yaw),
        }
    }

    fn advance_motion(
        &mut self,
        dt_us: u64,
        control: AppliedControl,
        ground: Option<&GroundReceipt>,
        motion: Option<&MotionReceipt>,
        navigation_graph: Option<&NavGraph>,
        navigation_target: Option<NavTarget>,
    ) {
        let dt = dt_us as f64 / 1_000_000.0;
        let movement_requested = control.throttle.abs() > 0.01
            || control.turn.abs() > 0.01
            || self.state.speed.abs() > 0.0001
            || self.state.angular_speed.abs() > 0.0001;
        if !movement_requested {
            self.state.speed = longitudinal_step(
                &self.descriptor.physics,
                self.state.speed,
                0.0,
                false,
                self.state.last_drive_pitch,
                dt,
                self.state.airborne,
                0,
                false,
            );
            self.state.angular_speed = traverse_step(
                &self.descriptor.physics,
                self.state.angular_speed,
                0.0,
                self.state.speed,
                dt,
                0,
                0.0,
            );
            self.state.movement_dir = 0;
            self.state.rotation_dir = 0;
            return;
        }
        let ground_authorized = ground.is_some_and(|receipt| {
            receipt.contains_pose && receipt.supported && receipt.slope_pitch.is_finite()
        });
        let motion = motion.filter(|receipt| {
            receipt.contains_pose
                && receipt.travel_yaw.is_finite()
                && angle_delta(receipt.travel_yaw, control.travel_yaw).abs() <= 0.25
        });
        let Some(motion) = motion.filter(|_| ground_authorized) else {
            // Fixed-latency oracle scheduling is not a collision. Preserve
            // pre-step momentum and the last confirmed pose.
            self.state.movement_dir = 0;
            self.state.rotation_dir = 0;
            return;
        };
        let previous_speed = self.state.speed;
        let next_angular = traverse_step(
            &self.descriptor.physics,
            self.state.angular_speed,
            control.turn,
            previous_speed,
            dt,
            0,
            control.throttle,
        );
        let requested_yaw = wrapped(self.state.yaw + next_angular * dt);
        let next_yaw = self.admitted_yaw(requested_yaw);
        let next_angular = if next_yaw == requested_yaw {
            next_angular
        } else {
            0.0
        };
        let mut next_speed = longitudinal_step(
            &self.descriptor.physics,
            previous_speed,
            control.throttle,
            control.turn.abs() > 0.01,
            self.state.last_drive_pitch,
            dt,
            self.state.airborne,
            0,
            false,
        );
        self.state.yaw = next_yaw;
        self.state.angular_speed = next_angular;
        let mut translation_admitted = motion.status.accepts_pose();
        match motion.status {
            MotionStatus::Clear | MotionStatus::Crushed => {
                let requested_x = next_yaw.sin() * next_speed * dt;
                let requested_z = next_yaw.cos() * next_speed * dt;
                let (applied_x, applied_z) = self.admitted_translation(requested_x, requested_z);
                let requested_position = Vec3::new(
                    self.state.position.x + applied_x,
                    self.state.position.y,
                    self.state.position.z + applied_z,
                );
                let navigation_admitted = navigation_graph.is_none_or(|graph| {
                    graph.committed_segment_clear(
                        self.state.position,
                        requested_position,
                        navigation_target,
                    )
                });
                if navigation_admitted {
                    self.state.position = requested_position;
                }
                if !navigation_admitted || applied_x != requested_x || applied_z != requested_z {
                    next_speed *= 0.2;
                    translation_admitted = false;
                }
            }
            MotionStatus::Soft | MotionStatus::CapCrushed => {
                next_speed = previous_speed;
            }
            MotionStatus::Hard => {
                next_speed *= 0.2;
            }
        }
        self.state.speed = next_speed;
        self.state.movement_dir = if translation_admitted {
            if control.throttle > 0.01 {
                1
            } else if control.throttle < -0.01 {
                -1
            } else {
                0
            }
        } else {
            0
        };
        self.state.rotation_dir = if control.turn > 0.01 {
            1
        } else if control.turn < -0.01 {
            -1
        } else {
            0
        };
    }

    fn advance_aim(&mut self, order: &BotOrder, ballistic: Option<&BallisticSolution>) {
        let fallback = order
            .target
            .as_ref()
            .map(|target| target.position)
            .unwrap_or(self.state.position);
        let aim_position = ballistic
            .map(|solution| solution.aim_position)
            .or(order.aim_position)
            .unwrap_or(fallback);
        let delta_x = aim_position.x - self.state.position.x;
        let delta_z = aim_position.z - self.state.position.z;
        let horizontal = delta_x.hypot(delta_z);
        let desired_yaw = ballistic.map(|solution| solution.yaw).unwrap_or_else(|| {
            if horizontal > 0.1 {
                delta_x.atan2(delta_z)
            } else {
                self.state.yaw
            }
        });
        let limits = self.descriptor.gun.yaw_limits;
        let mut desired_relative = angle_delta(desired_yaw, self.state.yaw);
        if limits.is_limited() {
            desired_relative = desired_relative.clamp(limits.minimum, limits.maximum);
        }
        let turret_step = self.descriptor.gun.turret_rotation_speed.max(0.0)
            * (fixed_dt_us(self.state.tick) as f64 / 1_000_000.0)
            * critical_factor(&self.state.critical, CriticalStat::TurretSpeed);
        let difference = angle_delta(desired_relative, self.state.turret_yaw);
        self.state.turret_yaw =
            wrapped(self.state.turret_yaw + difference.clamp(-turret_step, turret_step));
        if limits.is_limited() {
            self.state.turret_yaw = self.state.turret_yaw.clamp(limits.minimum, limits.maximum);
        }
        self.state.aim_yaw = wrapped(self.state.yaw + self.state.turret_yaw);
        let mut desired_pitch = ballistic.map(|solution| solution.pitch).unwrap_or_else(|| {
            -((aim_position.y + 1.0) - (self.state.position.y + 1.5)).atan2(horizontal.max(0.5))
        });
        desired_pitch = desired_pitch.clamp(
            self.descriptor.gun.pitch_limits.0,
            self.descriptor.gun.pitch_limits.1,
        );
        self.state.gun_pitch = slew(
            self.state.gun_pitch,
            desired_pitch,
            self.descriptor.gun.gun_rotation_speed.max(0.0)
                * (fixed_dt_us(self.state.tick) as f64 / 1_000_000.0),
        );
        self.state.desired_gun_pitch = desired_pitch;
        self.state.gun_aligned = order.target.is_some()
            && gun_aligned(
                desired_yaw,
                self.state.yaw,
                self.state.turret_yaw,
                desired_pitch,
                self.state.gun_pitch,
            );
    }

    fn fire_gate(
        &self,
        order: &BotOrder,
        visible: bool,
        ballistic: Option<&BallisticSolution>,
        reload_factor: f64,
    ) -> bool {
        let Some(target) = order.target.as_ref() else {
            return false;
        };
        let distance = self.state.position.horizontal_distance(target.position);
        order.fire_allowed
            && target.alive
            && target.health > 0
            && target.team != self.state.team
            && visible
            && ballistic.is_some()
            && distance > 1.0
            && (order.fire_range <= 0.0 || distance < order.fire_range)
            && !firing_hard_gated(&self.state.critical)
            && self.state.gun_aligned
            && self.state.gun.ready(reload_factor)
            && self.state.ammo.can_fire()
    }

    fn cancel_active_physical_burst(&mut self) {
        self.physical_burst_clock.cancel();
        self.active_physical_burst = None;
        self.state.gun.cancel_physical_burst();
    }

    #[allow(clippy::too_many_arguments)]
    fn advance_active_physical_burst(
        &mut self,
        tick: u64,
        requested_shell_index: usize,
        reload_factor: f64,
        step_start_time_us: u64,
        step_end_time_us: u64,
        step_start_pose: SampledBurstPose,
        step_end_pose: SampledBurstPose,
        events: &mut Vec<BotEvent>,
    ) -> Result<(), SimError> {
        let due = self
            .physical_burst_clock
            .advance_tick(tick)
            .map_err(|_| SimError::InvalidState("physical burst clock"))?;
        if due.is_empty() {
            return Ok(());
        }
        let context = self
            .active_physical_burst
            .ok_or(SimError::AtomicFireInvariant)?;
        let mut final_due_time_us = None;
        for edge in due {
            let launch_pose = SampledBurstPose::at_logical_time(
                step_start_pose,
                step_end_pose,
                step_start_time_us,
                step_end_time_us,
                edge.due_time_us,
            );
            let final_round = edge.final_round();
            let launch = self.commit_physical_burst_edge(edge, context, launch_pose, None)?;
            events.push(BotEvent::Projectile(launch));
            if final_round {
                final_due_time_us = Some(edge.due_time_us);
            }
        }
        if self.physical_burst_clock.active() {
            self.active_physical_burst = Some(context);
        } else {
            self.active_physical_burst = None;
        }
        if let Some(final_due_time_us) = final_due_time_us {
            let recovery_us = step_end_time_us.saturating_sub(final_due_time_us);
            self.state.gun.tick(recovery_us as f64 / 1_000_000.0);
            let available_rounds = self.state.ammo.total_remaining();
            self.state
                .gun
                .complete_physical_reload(reload_factor, available_rounds);
            self.state
                .ammo
                .stage(requested_shell_index, self.state.gun.ready(reload_factor));
        }
        Ok(())
    }

    fn start_physical_fire(
        &mut self,
        receipt: &LaneReceipt,
        reload_factor: f64,
        launch_pose: SampledBurstPose,
    ) -> Result<ProjectileLaunchEvent, SimError> {
        let group_seq = self
            .state
            .fire_seq
            .checked_add(1)
            .ok_or(SimError::AtomicFireInvariant)?;
        let admission = self
            .physical_burst_clock
            .arm(
                group_seq,
                u8::try_from(receipt.shell_index).map_err(|_| SimError::AtomicFireInvariant)?,
                self.physical_burst,
                self.state.ammo.loaded_remaining(),
                self.state.gun.clip,
                self.state.tick,
            )
            .map_err(|_| SimError::InvalidState("physical burst trigger"))?;
        let edge = match admission {
            PhysicalBurstAdmission::New { first } => first,
            PhysicalBurstAdmission::ExactRetry { .. } => {
                return Err(SimError::AtomicFireInvariant);
            }
        };
        if self.physical_burst.count == 1 {
            return self.commit_fire(receipt, reload_factor, edge, launch_pose);
        }

        let world_offset = Vec3::new(
            receipt.origin.x - launch_pose.launch.position.x,
            receipt.origin.y - launch_pose.launch.position.y,
            receipt.origin.z - launch_pose.launch.position.z,
        );
        let context = ActiveBotBurst {
            group_seq: edge.burst_group_seq,
            count: edge.burst_count,
            shell_index: receipt.shell_index,
            target_kind: receipt.target_kind,
            target_id: receipt.target_id,
            flight_time: receipt.flight_time,
            muzzle_offset: unrotate_vehicle_offset(world_offset, launch_pose.launch),
        };
        if !self
            .state
            .gun
            .begin_physical_burst(edge.burst_count, reload_factor)
        {
            return Err(SimError::AtomicFireInvariant);
        }
        let launch = self.commit_physical_burst_edge(edge, context, launch_pose, Some(receipt))?;
        self.active_physical_burst = self.physical_burst_clock.active().then_some(context);
        Ok(launch)
    }

    fn commit_fire(
        &mut self,
        receipt: &LaneReceipt,
        reload_factor: f64,
        edge: PhysicalBurstEdge,
        launch_pose: SampledBurstPose,
    ) -> Result<ProjectileLaunchEvent, SimError> {
        if edge.shot_seq != self.state.fire_seq.saturating_add(1)
            || edge.burst_group_seq != edge.shot_seq
            || edge.burst_index != 0
            || edge.burst_count != 1
        {
            return Err(SimError::AtomicFireInvariant);
        }
        if !self.state.gun.fire(reload_factor) {
            return Err(SimError::AtomicFireInvariant);
        }
        if !self.state.ammo.consume_loaded() {
            return Err(SimError::AtomicFireInvariant);
        }
        self.state.fire_seq = edge.shot_seq;
        Ok(self.projectile_launch_event(
            edge,
            receipt.target_kind,
            receipt.target_id,
            receipt.shell_index,
            receipt.origin,
            receipt.shot_yaw,
            receipt.shot_pitch,
            receipt.flight_time,
            launch_pose.launch,
        ))
    }

    fn commit_physical_burst_edge(
        &mut self,
        edge: PhysicalBurstEdge,
        context: ActiveBotBurst,
        launch_pose: SampledBurstPose,
        first_receipt: Option<&LaneReceipt>,
    ) -> Result<ProjectileLaunchEvent, SimError> {
        if edge.shot_seq != self.state.fire_seq.saturating_add(1)
            || edge.shot_seq != edge.burst_group_seq + u64::from(edge.burst_index)
            || edge.burst_group_seq != context.group_seq
            || edge.burst_count != context.count
            || usize::from(edge.shell_index) != context.shell_index
        {
            return Err(SimError::AtomicFireInvariant);
        }
        let (origin, shot_yaw, shot_pitch) = if let Some(receipt) = first_receipt {
            if edge.burst_index != 0
                || receipt.shell_index != context.shell_index
                || receipt.target_kind != context.target_kind
                || receipt.target_id != context.target_id
            {
                return Err(SimError::AtomicFireInvariant);
            }
            (receipt.origin, receipt.shot_yaw, receipt.shot_pitch)
        } else {
            if edge.burst_index == 0 {
                return Err(SimError::AtomicFireInvariant);
            }
            let offset = rotate_vehicle_offset(context.muzzle_offset, launch_pose.launch);
            let origin = Vec3::new(
                launch_pose.launch.position.x + offset.x,
                launch_pose.launch.position.y + offset.y,
                launch_pose.launch.position.z + offset.z,
            );
            let (shot_yaw, shot_pitch) = dispersed_physical_barrel_angles(
                self.state.id,
                self.state.round_id,
                edge.burst_group_seq,
                edge.burst_index,
                launch_pose.aim_yaw,
                launch_pose.gun_pitch,
                effective_shot_dispersion(&self.state.gun, &self.state.critical)?,
            )?;
            (origin, shot_yaw, shot_pitch)
        };
        if !self.state.gun.fire_physical_burst_round(edge.final_round()) {
            return Err(SimError::AtomicFireInvariant);
        }
        if !self.state.ammo.consume_loaded_round(edge.burst_index > 0) {
            return Err(SimError::AtomicFireInvariant);
        }
        if edge.final_round() && self.state.ammo.loaded_shell_requires_full_reload() {
            self.state.gun.require_full_reload();
        }
        self.state.fire_seq = edge.shot_seq;
        Ok(self.projectile_launch_event(
            edge,
            context.target_kind,
            context.target_id,
            context.shell_index,
            origin,
            shot_yaw,
            shot_pitch,
            context.flight_time,
            launch_pose.launch,
        ))
    }

    #[allow(clippy::too_many_arguments)]
    fn projectile_launch_event(
        &self,
        edge: PhysicalBurstEdge,
        target_kind: TargetKind,
        target_id: u32,
        shell_index: usize,
        origin: Vec3,
        shot_yaw: f64,
        shot_pitch: f64,
        flight_time: f64,
        launch_pose: ProjectileLaunchPose,
    ) -> ProjectileLaunchEvent {
        let shell = &self.descriptor.gun.shells[shell_index];
        let horizontal = shot_pitch.cos();
        let velocity = Vec3::new(
            shot_yaw.sin() * horizontal * shell.speed,
            shot_pitch.sin() * shell.speed,
            shot_yaw.cos() * horizontal * shell.speed,
        );
        ProjectileLaunchEvent {
            shot_id: SourceShotId {
                round_id: self.state.round_id,
                source_id: self.state.id,
                fire_seq: edge.shot_seq,
            },
            target_kind,
            target_id,
            shell_index,
            origin,
            velocity,
            gravity: shell.gravity,
            max_distance: shell.max_distance,
            max_time_ms: (flight_time * 1_000.0).ceil().clamp(1.0, u32::MAX as f64) as u32,
            shot_yaw,
            shot_pitch,
            flight_time,
            burst_group_seq: edge.burst_group_seq,
            burst_index: edge.burst_index,
            burst_count: edge.burst_count,
            launch_time_us: edge.due_time_us,
            launch_pose,
        }
    }

    fn build_queries(
        &self,
        tick: u64,
        dt_us: u64,
        order: &BotOrder,
        control: AppliedControl,
        visible: bool,
        ballistic: Option<&BallisticSolution>,
    ) -> Result<Vec<OracleQueryIntent>, SimError> {
        let mut queries = Vec::with_capacity(5);
        if native_query_due(self.state.id, tick, NATIVE_ACTION_CADENCE_TICKS) {
            let proof_horizon = ORACLE_LATENCY_TICKS as f64 / TICK_RATE_HZ as f64;
            let projected_distance = self.state.speed.abs() * proof_horizon;
            let ground_position = Vec3::new(
                self.state.position.x + control.travel_yaw.sin() * projected_distance,
                self.state.position.y,
                self.state.position.z + control.travel_yaw.cos() * projected_distance,
            );
            queries.push(OracleQueryIntent::Ground(GroundQuery {
                id: OracleQueryId::new(self.state.id, tick, OracleQueryKind::Ground),
                position: ground_position,
                yaw: self.state.yaw,
                half_length: self.descriptor.half_length,
                half_width: self.descriptor.half_width,
                include_water_depth: true,
            }));
            if control.throttle.abs() > 0.01
                || control.turn.abs() > 0.01
                || self.state.speed.abs() > 0.0001
                || self.state.angular_speed.abs() > 0.0001
            {
                queries.push(OracleQueryIntent::Motion(MotionQuery {
                    id: OracleQueryId::new(self.state.id, tick, OracleQueryKind::Motion),
                    position: self.state.position,
                    travel_yaw: control.travel_yaw,
                    speed: self.state.speed,
                    throttle: control.throttle,
                    turn: control.turn,
                    dt_us,
                    half_length: self.descriptor.half_length,
                    half_width: self.descriptor.half_width,
                }));
            }
        }
        let Some(target) = order
            .target
            .as_ref()
            .filter(|target| target.alive && target.health > 0 && target.team != self.state.team)
        else {
            return Ok(queries);
        };
        let shell_index = self.state.ammo.loaded();
        let shell = &self.descriptor.gun.shells[shell_index];
        let source_position = Vec3::new(
            self.state.position.x,
            self.state.position.y + 1.5,
            self.state.position.z,
        );
        if native_query_due(self.state.id, tick, VISIBILITY_CADENCE_TICKS) {
            queries.push(OracleQueryIntent::Visibility(VisibilityQuery {
                id: OracleQueryId::new(self.state.id, tick, OracleQueryKind::Visibility),
                target_kind: target.kind,
                target_id: target.network_id,
                source_position,
                target_position: target.position,
            }));
        }
        if native_query_due(self.state.id, tick, NATIVE_ACTION_CADENCE_TICKS) {
            queries.push(OracleQueryIntent::Ballistic(BallisticQuery {
                id: OracleQueryId::new(self.state.id, tick, OracleQueryKind::Ballistic),
                target_kind: target.kind,
                target_id: target.network_id,
                shell_index,
                source_position,
                target_position: target.position,
                target_velocity: target.velocity,
                shell_speed: shell.speed,
                gravity: shell.gravity,
                max_distance: shell.max_distance,
                pitch_limits: self.descriptor.gun.pitch_limits,
                prefer_high_arc: self.profile.class == VehicleClass::Spg,
            }));
        }
        let reload_factor = critical_factor(&self.state.critical, CriticalStat::Reload);
        if self.fire_gate(order, visible, ballistic, reload_factor)
            && native_query_due(self.state.id, tick, NATIVE_ACTION_CADENCE_TICKS)
        {
            let solution = ballistic.expect("fire gate requires a ballistic solution");
            let fire_seq = self.state.fire_seq.saturating_add(1);
            let (shot_yaw, shot_pitch) = dispersed_barrel_angles(
                self.state.id,
                self.state.round_id,
                fire_seq,
                self.state.aim_yaw,
                self.state.gun_pitch,
                effective_shot_dispersion(&self.state.gun, &self.state.critical)?,
            )?;
            queries.push(OracleQueryIntent::Lane(LaneQuery {
                id: OracleQueryId::new(self.state.id, tick, OracleQueryKind::Lane),
                target_kind: target.kind,
                target_id: target.network_id,
                fire_seq,
                shell_index,
                source_position,
                target_position: target.position,
                shot_yaw,
                shot_pitch,
                flight_time: solution.flight_time,
                shell_speed: shell.speed,
                gravity: shell.gravity,
                max_distance: shell.max_distance,
            }));
        }
        Ok(queries)
    }

    fn pose_event(&self) -> PoseEvent {
        let reload_factor = critical_factor(&self.state.critical, CriticalStat::Reload);
        PoseEvent {
            bot_id: self.state.id,
            tick: self.state.tick,
            position: self.state.position,
            yaw: self.state.yaw,
            pitch: self.state.pitch,
            roll: self.state.roll,
            speed: self.state.speed,
            movement_dir: self.state.movement_dir,
            rotation_dir: self.state.rotation_dir,
            turret_yaw: self.state.turret_yaw,
            gun_pitch: self.state.gun_pitch,
            gun_aligned: self.state.gun_aligned,
            health: self.state.health,
            alive: self.state.alive,
            fire_seq: self.state.fire_seq,
            ammo: self.state.ammo.snapshot(),
            clip_size: self.state.gun.clip_size,
            clip: self.state.gun.clip,
            reload_time: self.state.gun.remaining(reload_factor),
            reload_duration: self.state.gun.reload_duration * reload_factor,
        }
    }
}

fn validate_descriptor(
    descriptor: &VehicleDescriptor,
    profile: &BotProfile,
) -> Result<(), SimError> {
    if descriptor.vehicle_key.is_empty() {
        return Err(SimError::InvalidDescriptor("vehicle key is empty"));
    }
    if descriptor.max_health == 0 {
        return Err(SimError::InvalidDescriptor("max health must be positive"));
    }
    for (name, value) in [
        ("half length", descriptor.half_length),
        ("half width", descriptor.half_width),
        ("reload", descriptor.gun.reload_seconds),
        ("dispersion", descriptor.gun.shot_dispersion_angle),
        ("gun rotation", descriptor.gun.gun_rotation_speed),
        ("turret rotation", descriptor.gun.turret_rotation_speed),
        ("mass", descriptor.physics.mass),
        ("power", descriptor.physics.power_watts),
        ("forward speed", descriptor.physics.forward_speed_limit),
        ("reverse speed", descriptor.physics.reverse_speed_limit),
        ("rotation speed", descriptor.physics.rotation_speed),
        ("specific friction", descriptor.physics.specific_friction),
        ("native power ratio", descriptor.physics.native_power_ratio),
    ] {
        if !value.is_finite() || value <= 0.0 {
            return Err(SimError::InvalidDescriptor(name));
        }
    }
    if descriptor.gun.shells.is_empty() {
        return Err(SimError::InvalidDescriptor("installed gun has no shells"));
    }
    if descriptor.gun.pitch_limits.0 > descriptor.gun.pitch_limits.1
        || !descriptor.gun.pitch_limits.0.is_finite()
        || !descriptor.gun.pitch_limits.1.is_finite()
        || descriptor.gun.yaw_limits.minimum > descriptor.gun.yaw_limits.maximum
        || !descriptor.gun.yaw_limits.minimum.is_finite()
        || !descriptor.gun.yaw_limits.maximum.is_finite()
    {
        return Err(SimError::InvalidDescriptor("gun limits"));
    }
    if let Some(clip) = descriptor.gun.clip {
        if clip.size == 0
            || !clip.intra_reload_seconds.is_finite()
            || clip.intra_reload_seconds <= 0.0
        {
            return Err(SimError::InvalidDescriptor("gun clip"));
        }
    }
    for (index, shell) in descriptor.gun.shells.iter().enumerate() {
        if shell.index != index {
            return Err(SimError::InvalidDescriptor(
                "shell indices are not contiguous",
            ));
        }
        if [
            shell.penetration,
            shell.damage,
            shell.speed,
            shell.gravity,
            shell.max_distance,
        ]
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
        {
            return Err(SimError::InvalidDescriptor("shell ballistics"));
        }
    }
    if profile.shells.iter().any(|shell| {
        shell.index >= descriptor.gun.shells.len()
            || !shell.penetration.is_finite()
            || shell.penetration < 0.0
    }) {
        return Err(SimError::InvalidDescriptor("bot shell profile"));
    }
    if descriptor
        .physics
        .terrain_resistance
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
    {
        return Err(SimError::InvalidDescriptor("terrain resistance"));
    }
    Ok(())
}

fn validate_order(order: &BotOrder) -> Result<(), SimError> {
    if !order.throttle.is_finite()
        || !order.turn.is_finite()
        || !order.fire_range.is_finite()
        || order.target_yaw.is_some_and(|value| !value.is_finite())
        || order
            .aim_position
            .is_some_and(|position| !position.is_finite())
    {
        return Err(SimError::NonFinite("bot order"));
    }
    if let Some(target) = &order.target {
        if target.network_id == 0
            || !matches!(target.team, 1 | 2)
            || !target.position.is_finite()
            || !target.velocity.is_finite()
            || !target.yaw.is_finite()
            || !target.speed.is_finite()
        {
            return Err(SimError::InvalidState("target state"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shell(index: usize, kind: &str, penetration: f64, speed: f64) -> ShellDescriptor {
        ShellDescriptor {
            index,
            kind: kind.to_owned(),
            penetration,
            damage: if kind.contains("EXPLOSIVE") {
                420.0
            } else {
                300.0
            },
            speed,
            gravity: 9.81,
            max_distance: 5_000.0,
        }
    }

    fn descriptor(max_ammo: u16, reload_seconds: f64) -> VehicleDescriptor {
        VehicleDescriptor {
            vehicle_key: "ussr:R11_MS-1".to_owned(),
            max_ammo,
            max_health: 1_000,
            half_length: 3.5,
            half_width: 1.7,
            gun: GunDescriptor {
                reload_seconds,
                clip: None,
                shot_dispersion_angle: 0.03,
                gun_rotation_speed: 0.35,
                turret_rotation_speed: 0.5,
                pitch_limits: (-0.35, 0.15),
                yaw_limits: GunYawLimits::default(),
                shells: vec![
                    shell(0, "ARMOR_PIERCING", 180.0, 900.0),
                    shell(1, "ARMOR_PIERCING_CR", 260.0, 1_100.0),
                    shell(2, "HIGH_EXPLOSIVE", 60.0, 700.0),
                ],
            },
            physics: PhysicsProfile::default(),
            module_names: vec![
                "engineHealth".to_owned(),
                "gunHealth".to_owned(),
                "leftTrackHealth".to_owned(),
                "rightTrackHealth".to_owned(),
            ],
            crew_roster: vec![
                "commander".to_owned(),
                "driver".to_owned(),
                "gunner1".to_owned(),
                "loader1".to_owned(),
                "radioman1".to_owned(),
            ],
        }
    }

    fn profile(class: VehicleClass) -> BotProfile {
        BotProfile {
            class,
            shells: vec![
                ShellProfile {
                    index: 0,
                    kind: "ARMOR_PIERCING".to_owned(),
                    penetration: 180.0,
                },
                ShellProfile {
                    index: 1,
                    kind: "ARMOR_PIERCING_CR".to_owned(),
                    penetration: 260.0,
                },
                ShellProfile {
                    index: 2,
                    kind: "HIGH_EXPLOSIVE".to_owned(),
                    penetration: 60.0,
                },
            ],
        }
    }

    fn spawn(tick: u64, health: u32) -> BotSpawn {
        BotSpawn {
            id: 11,
            team: 1,
            round_id: 7,
            tick,
            position: Vec3::ZERO,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            health,
            fire_seq: 0,
            critical: CriticalState::default(),
        }
    }

    fn receipt_id(bot_id: u32, apply_tick: u64, kind: OracleQueryKind) -> OracleQueryId {
        OracleQueryId {
            bot_id,
            issued_tick: apply_tick - ORACLE_LATENCY_TICKS,
            apply_tick,
            kind,
        }
    }

    #[test]
    fn fixed_tick_delta_is_the_exact_33333_33333_33334_pattern() {
        assert_eq!(
            (1..=6).map(fixed_dt_us).collect::<Vec<_>>(),
            vec![33_333, 33_333, 33_334, 33_333, 33_333, 33_334]
        );
        assert_eq!((1..=30).map(fixed_dt_us).sum::<u64>(), 1_000_000);
    }

    #[test]
    fn ammunition_distribution_matches_python_for_tanks_and_artillery() {
        let descriptor = descriptor(60, 1.0);
        assert_eq!(
            ammunition_distribution(&descriptor, &profile(VehicleClass::MediumTank)),
            vec![30, 20, 10]
        );
        assert_eq!(
            ammunition_distribution(&descriptor, &profile(VehicleClass::Spg)),
            vec![10, 10, 40]
        );
    }

    #[test]
    fn exact_ammunition_inventory_is_preserved_validated_and_selects_a_stocked_round() {
        let exact_descriptor = descriptor(6, 1.0);
        let profile = profile(VehicleClass::MediumTank);
        let exact = AmmoState::new_exact(&exact_descriptor, &profile, vec![0, 2, 1]).unwrap();
        assert_eq!(
            exact.snapshot(),
            AmmoSnapshot {
                loaded: 1,
                next: 1,
                remaining: vec![0, 2, 1],
                reload_pending: false,
            }
        );

        let mut standard_later = profile.clone();
        standard_later.shells[0].kind = "HIGH_EXPLOSIVE".to_owned();
        standard_later.shells[1].kind = "ARMOR_PIERCING".to_owned();
        standard_later.shells[2].kind = "HIGH_EXPLOSIVE".to_owned();
        let preferred =
            AmmoState::new_exact(&exact_descriptor, &standard_later, vec![1, 2, 0]).unwrap();
        assert_eq!((preferred.loaded(), preferred.snapshot().next), (1, 1));

        assert_eq!(
            AmmoState::new_exact(&exact_descriptor, &profile, vec![1, 2]),
            Err(SimError::InvalidState("ammunition inventory shape"))
        );
        assert_eq!(
            AmmoState::new_exact(&exact_descriptor, &profile, vec![3, 3, 1]),
            Err(SimError::InvalidState("ammunition capacity"))
        );

        let large_descriptor = descriptor(2_000, 1.0);
        assert_eq!(
            AmmoState::new_exact(&large_descriptor, &profile, vec![1_001, 0, 0]),
            Err(SimError::InvalidState("ammunition quantity"))
        );
    }

    #[test]
    fn exact_loaded_ammunition_preserves_garage_selection_and_rejects_empty_slots() {
        let descriptor = descriptor(6, 1.0);
        let profile = profile(VehicleClass::MediumTank);
        let selected =
            AmmoState::new_exact_loaded(&descriptor, &profile, vec![2, 0, 1], 2).unwrap();
        assert_eq!(
            selected.snapshot(),
            AmmoSnapshot {
                loaded: 2,
                next: 2,
                remaining: vec![2, 0, 1],
                reload_pending: false,
            }
        );
        assert_eq!(
            AmmoState::new_exact_loaded(&descriptor, &profile, vec![2, 0, 1], 1),
            Err(SimError::InvalidState("loaded ammunition is exhausted"))
        );
        assert_eq!(
            AmmoState::new_exact_loaded(&descriptor, &profile, vec![2, 0, 1], 3),
            Err(SimError::InvalidState("ammunition selection"))
        );

        let empty = AmmoState::new_exact_loaded(&descriptor, &profile, vec![0, 0, 0], 2).unwrap();
        assert_eq!((empty.loaded(), empty.snapshot().next), (2, 2));
        assert!(!empty.can_fire());
    }

    #[test]
    fn loaded_round_and_planned_round_change_only_at_reload_boundary() {
        let descriptor = descriptor(6, 1.0);
        let mut ammo = AmmoState::new(&descriptor, &profile(VehicleClass::MediumTank));
        assert_eq!(ammo.snapshot().remaining, vec![3, 2, 1]);
        assert!(ammo.stage(1, true));
        assert!(!ammo.stage(2, true));
        assert!(ammo.consume_loaded());
        assert_eq!(
            ammo.snapshot(),
            AmmoSnapshot {
                loaded: 0,
                next: 1,
                remaining: vec![2, 2, 1],
                reload_pending: true,
            }
        );
        assert!(!ammo.stage(2, false));
        assert!(ammo.stage(2, true));
        assert_eq!((ammo.snapshot().loaded, ammo.snapshot().next), (1, 2));
    }

    #[test]
    fn reload_is_strict_and_clip_clock_matches_python() {
        let mut gun_descriptor = descriptor(60, 2.0).gun;
        gun_descriptor.clip = Some(ClipDescriptor {
            size: 3,
            intra_reload_seconds: 0.4,
        });
        let mut gun = GunState::new(&gun_descriptor, 0).unwrap();
        gun.tick(2.0);
        assert!(!gun.ready(1.0));
        gun.tick(0.000_001);
        assert!(gun.fire(1.0));
        assert_eq!(gun.clip, 2);
        assert_eq!(gun.reload_duration, 0.4);
        gun.tick(0.2);
        assert!(gun.rescale_reload(2.0));
        assert!((gun.elapsed - 0.4).abs() < 1.0e-12);
        assert!(!gun.ready(2.0));
        gun.tick(0.400_001);
        assert!(gun.fire(2.0));
        assert_eq!(gun.clip, 1);
    }

    #[test]
    fn python_mt19937_and_dispersion_match_python_golden_values() {
        let mut random = PythonRandom::new(5_107_064);
        let expected = [
            0.537_502_606_185_074_1,
            0.777_144_008_481_319_7,
            0.909_449_223_498_658_4,
            0.759_004_903_052_058_7,
        ];
        for value in expected {
            assert!((random.random() - value).abs() < 1.0e-15);
        }
        let (yaw, pitch) = dispersed_barrel_angles(11, 5, 1, 0.4, -0.1, 0.012).unwrap();
        assert!((yaw - 0.391_435_737_229_694_5).abs() < 1.0e-14);
        assert!((pitch - 0.105_442_614_389_985_63).abs() < 1.0e-14);
        let centre = barrel_direction(0.4, -0.1);
        let fired = Vec3::new(
            yaw.sin() * pitch.cos(),
            pitch.sin(),
            yaw.cos() * pitch.cos(),
        );
        let cone_angle = (centre.x * fired.x + centre.y * fired.y + centre.z * fired.z)
            .clamp(-1.0, 1.0)
            .acos();
        assert!(cone_angle <= 0.012 + 1.0e-12);
    }

    #[test]
    fn critical_factors_match_crew_and_module_laws() {
        let mut critical = CriticalState::default();
        critical.crew_ko.insert("gunner1".to_owned());
        critical
            .devices
            .insert("gunHealth".to_owned(), DeviceCondition::Critical);
        let expected = (crew_role_factor(1.0, true) / crew_role_factor(0.0, true)) * 2.0;
        assert!((critical_factor(&critical, CriticalStat::Dispersion) - expected).abs() < 1.0e-12);

        critical.crew_ko.insert("driver".to_owned());
        critical
            .devices
            .insert("engineHealth".to_owned(), DeviceCondition::Critical);
        let mobility = (crew_role_factor(0.0, true) / crew_role_factor(1.0, true)) * 0.5;
        assert!((critical_factor(&critical, CriticalStat::Mobility) - mobility).abs() < 1.0e-12);
        critical
            .devices
            .insert("engineHealth".to_owned(), DeviceCondition::Destroyed);
        assert_eq!(critical_factor(&critical, CriticalStat::Mobility), 0.0);
        assert!(movement_hard_gated(&critical));
    }

    #[test]
    fn copied_flat_ground_motion_matches_python_golden_trace() {
        let physics = PhysicsProfile::default();
        let mut speed = 0.0;
        let mut angular = 0.0;
        let mut yaw = 0.0;
        let mut z = 0.0;
        for tick in 1..=30 {
            let dt = fixed_dt_us(tick) as f64 / 1_000_000.0;
            angular = traverse_step(&physics, angular, 0.0, speed, dt, 0, 1.0);
            yaw += angular * dt;
            speed = longitudinal_step(&physics, speed, 1.0, false, 0.0, dt, false, 0, false);
            z += yaw.cos() * speed * dt;
        }
        assert!((speed - 2.939_150_490_662_697).abs() < 1.0e-12);
        assert_eq!(angular, 0.0);
        assert_eq!(yaw, 0.0);
        assert!((z - 1.785_596_441_997_481_5).abs() < 1.0e-12);
    }

    #[test]
    fn reverse_intent_inverts_the_traverse_ramp() {
        let physics = PhysicsProfile::default();
        let dt = fixed_dt_us(1) as f64 / 1_000_000.0;
        let angular = traverse_step(&physics, 0.0, 1.0, 0.0, dt, 0, -1.0);
        let speed = longitudinal_step(&physics, 0.0, -1.0, true, 0.0, dt, false, 0, false);
        assert!((angular - -0.442_145_655_671_125_1).abs() < 1.0e-12);
        assert!((speed - -0.160_447_512_000_981_67).abs() < 1.0e-12);
    }

    #[test]
    fn traffic_controller_stops_at_the_standstill_gap() {
        let source = TrafficSource {
            bot_id: 11,
            team: 1,
            position: Vec3::ZERO,
            yaw: 0.0,
            speed: 5.0,
            half_length: 3.5,
            half_width: 1.7,
            last_drive_pitch: 0.0,
        };
        let leader = TrafficBody {
            network_id: 12,
            kind: TargetKind::Bot,
            team: 1,
            position: Vec3::new(0.0, 0.0, 8.0),
            velocity: Vec3::ZERO,
            yaw: 0.0,
            half_length: 3.5,
            half_width: 1.7,
        };
        assert_eq!(
            traffic_throttle(source, 1.0, 0.0, 0.0, &[leader], &PhysicsProfile::default()),
            (0.0, true)
        );
    }

    #[test]
    fn fixed_tick_control_keeps_planner_throttle_near_a_teammate() {
        let mut simulation = BotSimulator::new(
            descriptor(60, 1.0),
            profile(VehicleClass::MediumTank),
            spawn(0, 1_000),
        )
        .unwrap();
        let leader = TrafficBody {
            network_id: 12,
            kind: TargetKind::Bot,
            team: 1,
            position: Vec3::new(0.0, 0.0, 8.0),
            velocity: Vec3::ZERO,
            yaw: 0.0,
            half_length: 3.5,
            half_width: 1.7,
        };
        let order = BotOrder {
            throttle: 1.0,
            ..BotOrder::default()
        };

        assert_eq!(simulation.resolve_control(&order, &[leader]).throttle, 1.0);
    }

    #[test]
    fn stretched_tick_is_rejected_without_mutating_state() {
        let mut simulation = BotSimulator::new(
            descriptor(60, 1.0),
            profile(VehicleClass::MediumTank),
            spawn(0, 1_000),
        )
        .unwrap();
        let before = simulation.state().clone();
        let error = simulation
            .step(TickInput {
                tick: 1,
                dt_us: 33_334,
                order: &BotOrder::default(),
                receipts: &OracleReceipts::default(),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap_err();
        assert!(matches!(error, SimError::InvalidTickDelta { .. }));
        assert_eq!(simulation.state(), &before);
    }

    #[test]
    fn ram_pose_and_lateral_impulse_survive_the_next_fixed_tick() {
        let mut simulation = BotSimulator::new(
            descriptor(60, 1.0),
            profile(VehicleClass::MediumTank),
            spawn(0, 1_000),
        )
        .unwrap();
        simulation.apply_ram_delta(1.0, 0.0, 6.0, 0.0).unwrap();
        assert_eq!(simulation.state().position.x, 1.0);
        assert_eq!(simulation.ram_velocity().x, 6.0);
        simulation
            .step(TickInput {
                tick: 1,
                dt_us: fixed_dt_us(1),
                order: &BotOrder::default(),
                receipts: &OracleReceipts::default(),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        assert!(simulation.state().position.x > 1.19);
        assert!(simulation.ram_velocity().x > 0.0);
        assert!(simulation.ram_velocity().x < 6.0);
    }

    #[test]
    fn donated_full_chassis_cannot_translate_push_or_rotate_past_map_edge() {
        let mut initial = spawn(0, 1_000);
        initial.position.x = 7.9;
        let mut simulation = BotSimulator::new(
            descriptor(60, 1.0),
            profile(VehicleClass::MediumTank),
            initial,
        )
        .unwrap();
        assert!(simulation
            .install_map_envelope([-10.0, -10.0, 10.0, 10.0], 2.0, 4.0)
            .unwrap());
        assert!(!simulation
            .install_map_envelope([-10.0, -10.0, 10.0, 10.0], 2.0, 4.0)
            .unwrap());

        assert_eq!(simulation.admitted_yaw(std::f64::consts::FRAC_PI_2), 0.0);
        simulation.apply_ram_delta(1.0, 0.0, 0.0, 0.0).unwrap();
        assert_eq!(simulation.state().position.x, 7.9);
        simulation.apply_ram_delta(-1.0, 0.0, 0.0, 0.0).unwrap();
        assert_eq!(simulation.state().position.x, 6.9);

        simulation.apply_ram_delta(0.0, 0.0, 8.0, 0.0).unwrap();
        simulation.advance_ram_push(1_000_000);
        assert_eq!(simulation.state().position.x, 6.9);
        assert_eq!(simulation.ram_push.x, 0.0);
    }

    #[test]
    fn fire_clock_ticks_ten_times_and_extinguishes_on_the_final_tick() {
        let mut initial = spawn(0, 1_000);
        initial.critical.on_fire = true;
        let mut simulation = BotSimulator::new(
            descriptor(60, 1.0),
            profile(VehicleClass::MediumTank),
            initial,
        )
        .unwrap();
        let order = BotOrder::default();
        let receipts = OracleReceipts::default();
        let mut fire_ticks = 0;
        let mut extinguished = 0;
        for tick in 1..=300 {
            let output = simulation
                .step(TickInput {
                    tick,
                    dt_us: fixed_dt_us(tick),
                    order: &order,
                    receipts: &receipts,
                    neighbours: &[],
                    navigation_graph: None,
                })
                .unwrap();
            for event in output.events {
                match event {
                    BotEvent::Combat(CombatEvent::FireTick { .. }) => fire_ticks += 1,
                    BotEvent::Combat(CombatEvent::FireExtinguished) => extinguished += 1,
                    _ => {}
                }
            }
        }
        assert_eq!(fire_ticks, 10);
        assert_eq!(extinguished, 1);
        assert_eq!(simulation.state().health, 500);
        assert!(!simulation.state().critical.on_fire);
    }

    #[test]
    fn drowning_requires_more_than_ten_continuous_seconds() {
        let mut descriptor = descriptor(60, 1.0);
        descriptor.max_health = 640;
        let mut simulation = BotSimulator::new(
            descriptor,
            profile(VehicleClass::MediumTank),
            spawn(90, 640),
        )
        .unwrap();
        let order = BotOrder::default();
        for tick in 91..=395 {
            let receipts = OracleReceipts {
                ground: Some(GroundReceipt {
                    id: receipt_id(11, tick, OracleQueryKind::Ground),
                    sample_position: simulation.state().position,
                    sample_yaw: simulation.state().yaw,
                    contains_pose: true,
                    supported: true,
                    ground_height: 0.0,
                    slope_pitch: 0.0,
                    water_depth: Some(2.0),
                }),
                ..OracleReceipts::default()
            };
            simulation
                .step(TickInput {
                    tick,
                    dt_us: fixed_dt_us(tick),
                    order: &order,
                    receipts: &receipts,
                    neighbours: &[],
                    navigation_graph: None,
                })
                .unwrap();
        }
        assert!(simulation.state().alive);
        let tick = 396;
        let receipts = OracleReceipts {
            ground: Some(GroundReceipt {
                id: receipt_id(11, tick, OracleQueryKind::Ground),
                sample_position: simulation.state().position,
                sample_yaw: simulation.state().yaw,
                contains_pose: true,
                supported: true,
                ground_height: 0.0,
                slope_pitch: 0.0,
                water_depth: Some(2.0),
            }),
            ..OracleReceipts::default()
        };
        simulation
            .step(TickInput {
                tick,
                dt_us: fixed_dt_us(tick),
                order: &order,
                receipts: &receipts,
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        assert!(!simulation.state().alive);
        assert_eq!(simulation.state().health, 0);
        assert_eq!(simulation.state().display_health, 640);
        assert_eq!(simulation.state().death_reason, Some(DROWNING_DEATH_REASON));
        assert!(simulation.state().drowning.drowned);
    }

    fn target() -> TargetState {
        TargetState {
            network_id: 2,
            kind: TargetKind::Human,
            team: 2,
            alive: true,
            health: 1_000,
            position: Vec3::new(0.0, 0.0, 100.0),
            velocity: Vec3::ZERO,
            yaw: 0.0,
            speed: 0.0,
        }
    }

    fn firing_receipts(tick: u64, fire_seq: u64, with_lane: bool) -> OracleReceipts {
        OracleReceipts {
            visibility: Some(VisibilityReceipt {
                id: receipt_id(11, tick, OracleQueryKind::Visibility),
                target_kind: TargetKind::Human,
                target_id: 2,
                source_position: Vec3::new(0.0, 1.5, 0.0),
                target_position: Vec3::new(0.0, 0.0, 100.0),
                visible: true,
            }),
            ballistic: Some(BallisticReceipt {
                id: receipt_id(11, tick, OracleQueryKind::Ballistic),
                target_kind: TargetKind::Human,
                target_id: 2,
                shell_index: 0,
                source_position: Vec3::new(0.0, 1.5, 0.0),
                target_position: Vec3::new(0.0, 0.0, 100.0),
                target_velocity: Vec3::ZERO,
                solution: Some(BallisticSolution {
                    aim_position: Vec3::new(0.0, 1.0, 100.0),
                    yaw: 0.0,
                    pitch: 0.0,
                    flight_time: 0.2,
                }),
            }),
            lane: with_lane.then(|| LaneReceipt {
                id: receipt_id(11, tick, OracleQueryKind::Lane),
                target_kind: TargetKind::Human,
                target_id: 2,
                fire_seq,
                shell_index: 0,
                source_position: Vec3::new(0.0, 1.5, 0.0),
                target_position: Vec3::new(0.0, 0.0, 100.0),
                clear: true,
                origin: Vec3::new(0.0, 1.5, 0.0),
                shot_yaw: 0.0,
                shot_pitch: 0.0,
                flight_time: 0.2,
            }),
            ..OracleReceipts::default()
        }
    }

    #[test]
    fn native_cache_never_crosses_target_shell_heading_deadline_or_failure() {
        let mut simulation = BotSimulator::new(
            descriptor(6, 0.01),
            profile(VehicleClass::MediumTank),
            spawn(0, 1_000),
        )
        .unwrap();
        let tick = 4;
        let mut receipts = firing_receipts(tick, 1, false);
        receipts.motion = Some(MotionReceipt {
            id: receipt_id(11, tick, OracleQueryKind::Motion),
            sample_position: Vec3::ZERO,
            contains_pose: true,
            travel_yaw: 0.0,
            status: MotionStatus::Clear,
        });
        simulation.absorb_oracle_receipts(&receipts, tick);

        let straight = AppliedControl {
            throttle: 1.0,
            turn: 0.0,
            travel_yaw: 0.0,
        };
        assert!(simulation.cached_motion(straight, tick).is_some());
        assert!(simulation
            .cached_motion(
                AppliedControl {
                    travel_yaw: 0.3,
                    ..straight
                },
                tick,
            )
            .is_none());
        assert!(simulation
            .cached_ballistic(Some(&target()), 0, tick)
            .is_some());
        assert!(simulation
            .cached_ballistic(Some(&target()), 1, tick)
            .is_none());

        let mut other_target = target();
        other_target.network_id = 3;
        assert!(simulation
            .cached_ballistic(Some(&other_target), 0, tick)
            .is_none());
        let mut moved_target = target();
        moved_target.position.x += BALLISTIC_TARGET_ENVELOPE_METRES + 0.01;
        assert!(simulation
            .cached_ballistic(Some(&moved_target), 0, tick)
            .is_none());
        assert!(simulation
            .cached_ballistic(Some(&target()), 0, tick + NATIVE_ACTION_CADENCE_TICKS + 1,)
            .is_none());

        let failure_tick = 7;
        let mut failed = OracleReceipts::default();
        failed
            .failures
            .insert(receipt_id(11, failure_tick, OracleQueryKind::Motion));
        simulation.absorb_oracle_receipts(&failed, failure_tick);
        assert!(simulation.cached_motion(straight, failure_tick).is_none());

        let recovery_tick = 10;
        let fresh = OracleReceipts {
            motion: Some(MotionReceipt {
                id: receipt_id(11, recovery_tick, OracleQueryKind::Motion),
                sample_position: simulation.state().position,
                contains_pose: true,
                travel_yaw: 0.0,
                status: MotionStatus::Clear,
            }),
            ..OracleReceipts::default()
        };
        simulation.absorb_oracle_receipts(&fresh, recovery_tick);
        assert!(simulation.cached_motion(straight, recovery_tick).is_some());
    }

    #[test]
    fn ordinary_fire_uses_only_a_fresh_current_tick_lane_receipt() {
        let mut simulation = BotSimulator::new(
            descriptor(6, 0.01),
            profile(VehicleClass::MediumTank),
            spawn(3, 1_000),
        )
        .unwrap();
        let order = BotOrder {
            target: Some(target()),
            aim_position: Some(Vec3::new(0.0, 1.0, 100.0)),
            fire_allowed: true,
            fire_range: 560.0,
            ..BotOrder::default()
        };
        let first = simulation
            .step(TickInput {
                tick: 4,
                dt_us: fixed_dt_us(4),
                order: &order,
                receipts: &firing_receipts(4, 1, true),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        let first_id = first.events.iter().find_map(|event| match event {
            BotEvent::Projectile(launch) => Some(launch.shot_id),
            _ => None,
        });
        assert_eq!(first_id.unwrap().fire_seq, 1);
        assert_eq!(simulation.state().fire_seq, 1);

        simulation
            .step(TickInput {
                tick: 5,
                dt_us: fixed_dt_us(5),
                order: &order,
                receipts: &firing_receipts(5, 1, false),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        assert_eq!(simulation.state().fire_seq, 1);

        let third = simulation
            .step(TickInput {
                tick: 6,
                dt_us: fixed_dt_us(6),
                order: &order,
                receipts: &firing_receipts(6, 2, true),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        let second_id = third.events.iter().find_map(|event| match event {
            BotEvent::Projectile(launch) => Some(launch.shot_id),
            _ => None,
        });
        assert_eq!(second_id.unwrap().fire_seq, 2);
        assert_eq!(simulation.state().fire_seq, 2);
        assert_eq!(simulation.state().ammo.snapshot().remaining, vec![1, 2, 1]);

        let launch = third.events.iter().find_map(|event| match event {
            BotEvent::Projectile(launch) => Some(launch),
            _ => None,
        });
        let launch = launch.unwrap();
        assert_eq!(launch.burst_group_seq, 2);
        assert_eq!((launch.burst_index, launch.burst_count), (0, 1));
        assert_eq!(launch.launch_time_us, time_us_at_tick(6));
    }

    #[test]
    fn physical_burst_launches_and_debits_every_round_at_strict_clip_edges() {
        let mut installed = descriptor(60, 4.0);
        installed.gun.clip = Some(ClipDescriptor {
            size: 5,
            intra_reload_seconds: 2.0,
        });
        let mut simulation = BotSimulator::new(
            installed,
            profile(VehicleClass::MediumTank),
            spawn(3, 1_000),
        )
        .unwrap();
        simulation
            .install_physical_burst(PhysicalBurstDescriptor::new(3, 0.1).unwrap())
            .unwrap();
        simulation.state_mut().gun.elapsed = 10.0;
        let initial_ammo = simulation.state().ammo.loaded_remaining();
        let fire_order = BotOrder {
            target: Some(target()),
            aim_position: Some(Vec3::new(0.0, 1.0, 100.0)),
            fire_allowed: true,
            fire_range: 560.0,
            ..BotOrder::default()
        };
        let mut launches = Vec::new();
        let first = simulation
            .step(TickInput {
                tick: 4,
                dt_us: fixed_dt_us(4),
                order: &fire_order,
                receipts: &firing_receipts(4, 1, true),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        launches.extend(first.events.into_iter().filter_map(|event| match event {
            BotEvent::Projectile(launch) => Some(launch),
            _ => None,
        }));
        for tick in 5..=10 {
            let output = simulation
                .step(TickInput {
                    tick,
                    dt_us: fixed_dt_us(tick),
                    order: &BotOrder::default(),
                    receipts: &OracleReceipts::default(),
                    neighbours: &[],
                    navigation_graph: None,
                })
                .unwrap();
            launches.extend(output.events.into_iter().filter_map(|event| match event {
                BotEvent::Projectile(launch) => Some(launch),
                _ => None,
            }));
        }

        assert_eq!(
            launches
                .iter()
                .map(|launch| launch.shot_id.fire_seq)
                .collect::<Vec<_>>(),
            vec![1, 2, 3]
        );
        assert_eq!(
            launches
                .iter()
                .map(|launch| launch.burst_index)
                .collect::<Vec<_>>(),
            vec![0, 1, 2]
        );
        assert!(launches
            .iter()
            .all(|launch| launch.burst_group_seq == 1 && launch.burst_count == 3));
        assert_eq!(
            launches
                .iter()
                .map(|launch| launch.launch_time_us)
                .collect::<Vec<_>>(),
            vec![time_us_at_tick(4), 233_333, 333_333]
        );
        assert_eq!(simulation.state().ammo.loaded_remaining(), initial_ammo - 3);
        assert_eq!(simulation.state().gun.clip, 2);
        assert_eq!(simulation.state().gun.reload_duration, 2.0);
        assert!(!simulation.physical_burst_active());
        assert_ne!(
            (launches[1].shot_yaw, launches[1].shot_pitch),
            (launches[2].shot_yaw, launches[2].shot_pitch)
        );
    }

    #[test]
    fn multiple_due_rounds_share_one_output_but_keep_ordered_logical_poses() {
        let mut installed = descriptor(60, 4.0);
        installed.gun.clip = Some(ClipDescriptor {
            size: 5,
            intra_reload_seconds: 2.0,
        });
        let mut simulation = BotSimulator::new(
            installed,
            profile(VehicleClass::MediumTank),
            spawn(3, 1_000),
        )
        .unwrap();
        simulation
            .install_physical_burst(PhysicalBurstDescriptor::new(4, 0.01).unwrap())
            .unwrap();
        simulation.state_mut().gun.elapsed = 10.0;
        let fire_order = BotOrder {
            target: Some(target()),
            aim_position: Some(Vec3::new(0.0, 1.0, 100.0)),
            fire_allowed: true,
            fire_range: 560.0,
            ..BotOrder::default()
        };
        let first = simulation
            .step(TickInput {
                tick: 4,
                dt_us: fixed_dt_us(4),
                order: &fire_order,
                receipts: &firing_receipts(4, 1, true),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        assert_eq!(
            first
                .events
                .iter()
                .filter(|event| matches!(event, BotEvent::Projectile(_)))
                .count(),
            1
        );

        simulation.ram_push = Vec3::new(0.0, 0.0, 10.0);
        let catch_up = simulation
            .step(TickInput {
                tick: 5,
                dt_us: fixed_dt_us(5),
                order: &BotOrder::default(),
                receipts: &OracleReceipts::default(),
                neighbours: &[],
                navigation_graph: None,
            })
            .unwrap();
        let launches = catch_up
            .events
            .iter()
            .filter_map(|event| match event {
                BotEvent::Projectile(launch) => Some(launch),
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(launches.len(), 3);
        assert_eq!(
            launches
                .iter()
                .map(|launch| launch.shot_id.fire_seq)
                .collect::<Vec<_>>(),
            vec![2, 3, 4]
        );
        assert_eq!(
            launches
                .iter()
                .map(|launch| launch.launch_time_us)
                .collect::<Vec<_>>(),
            vec![143_333, 153_333, 163_333]
        );
        for (index, launch) in launches.iter().enumerate() {
            let expected_z = (index + 1) as f64 / 10.0;
            assert!((launch.launch_pose.position.z - expected_z).abs() < 1.0e-9);
            assert!((launch.origin.z - expected_z).abs() < 1.0e-9);
        }
        assert!(launches
            .iter()
            .all(|launch| launch.launch_pose.position.z < simulation.state().position.z));
    }

    #[test]
    fn stalled_catch_up_freezes_each_point_one_second_edge_pose_and_muzzle() {
        for (stall_ticks, count) in [(6_u64, 3_u16), (30_u64, 11_u16)] {
            let mut installed = descriptor((count + 2) * 3, 4.0);
            installed.gun.clip = Some(ClipDescriptor {
                size: count + 1,
                intra_reload_seconds: 2.0,
            });
            let mut simulation = BotSimulator::new(
                installed,
                profile(VehicleClass::MediumTank),
                spawn(3, 1_000),
            )
            .unwrap();
            simulation
                .install_physical_burst(PhysicalBurstDescriptor::new(count, 0.1).unwrap())
                .unwrap();
            simulation.state_mut().gun.elapsed = 10.0;
            let fire_order = BotOrder {
                target: Some(target()),
                aim_position: Some(Vec3::new(0.0, 1.0, 100.0)),
                fire_allowed: true,
                fire_range: 560.0,
                ..BotOrder::default()
            };
            let first = simulation
                .step(TickInput {
                    tick: 4,
                    dt_us: fixed_dt_us(4),
                    order: &fire_order,
                    receipts: &firing_receipts(4, 1, true),
                    neighbours: &[],
                    navigation_graph: None,
                })
                .unwrap();
            let mut launches = first
                .events
                .into_iter()
                .filter_map(|event| match event {
                    BotEvent::Projectile(launch) => Some(launch),
                    _ => None,
                })
                .collect::<Vec<_>>();
            for tick in 5..=4 + stall_ticks {
                // A wall-clock catch-up still enters the core as canonical
                // fixed ticks. Hold a 10 m/s logical velocity across each one.
                simulation.ram_push = Vec3::new(0.0, 0.0, 10.0);
                let output = simulation
                    .step(TickInput {
                        tick,
                        dt_us: fixed_dt_us(tick),
                        order: &BotOrder::default(),
                        receipts: &OracleReceipts::default(),
                        neighbours: &[],
                        navigation_graph: None,
                    })
                    .unwrap();
                launches.extend(output.events.into_iter().filter_map(|event| match event {
                    BotEvent::Projectile(launch) => Some(launch),
                    _ => None,
                }));
            }

            assert_eq!(launches.len(), usize::from(count));
            for (index, launch) in launches.iter().enumerate() {
                assert_eq!(
                    launch.launch_time_us,
                    time_us_at_tick(4) + index as u64 * 100_000
                );
                assert!((launch.launch_pose.position.z - index as f64).abs() < 1.0e-9);
                assert!((launch.origin.z - index as f64).abs() < 1.0e-9);
            }
        }
    }
}
