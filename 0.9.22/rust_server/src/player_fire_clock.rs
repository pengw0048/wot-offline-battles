//! Deterministic 30 Hz player gun-dispersion authority.
//!
//! The exact #1513 client donates descriptor and actor-scoped effective
//! factors. This module validates those scalar inputs, owns the fixed-tick
//! aiming state, applies physical-shot bloom, and samples a bounded firing
//! cone from explicit battle lineage. It has no wall clock or random source.

use std::error::Error;
use std::f64::consts::PI;
use std::fmt;

pub const PLAYER_FIRE_TICK_RATE_HZ: u64 = 30;
pub const PLAYER_FIRE_TICK_SECONDS: f64 = 1.0 / PLAYER_FIRE_TICK_RATE_HZ as f64;

const MIN_BASE_DISPERSION_RADIANS: f64 = 0.000_001;
const MAX_BASE_DISPERSION_RADIANS: f64 = 1.0;
const MIN_AIMING_TIME_SECONDS: f64 = 0.01;
const MAX_AIMING_TIME_SECONDS: f64 = 300.0;
const MAX_DESCRIPTOR_BLOOM_FACTOR: f64 = 64.0;
const MIN_EFFECTIVE_FACTOR: f64 = 0.01;
const MAX_EFFECTIVE_FACTOR: f64 = 16.0;
const MAX_LINEAR_SPEED_MPS: f64 = 200.0;
const MAX_ANGULAR_SPEED_RADIANS_PER_SECOND: f64 = 4.0 * PI;
const MIN_DISPERSION_RADIANS: f64 = 0.000_000_001;
const MAX_DISPERSION_RADIANS: f64 = 1.5;
const MAX_DISPERSION_MULTIPLIER: f64 = 15.0;
const MAX_ADVANCE_TICKS: u64 = PLAYER_FIRE_TICK_RATE_HZ * 60 * 10;
const MIN_DIRECTION_LENGTH: f64 = 0.5;
const MAX_DIRECTION_LENGTH: f64 = 2.0;
const MOTION_EXPANSION_PER_TICK: f64 = 0.2;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerGunDispersionLaw {
    /// Installed gun `shotDispersionAngle`, in radians.
    pub base_dispersion_radians: f64,
    /// Installed gun `aimingTime`, before actor-scoped factors.
    pub aiming_time_seconds: f64,
    /// Chassis movement bloom per metre per second.
    pub movement_bloom_per_mps: f64,
    /// Chassis rotation bloom per radian per second.
    pub hull_rotation_bloom_per_rad_s: f64,
    /// Gun `shotDispersionFactors.turretRotation` per radian per second.
    pub turret_rotation_bloom_per_rad_s: f64,
    /// Final physical round `shotDispersionFactors.afterShot` bloom.
    pub after_shot_bloom: f64,
    /// Non-final physical round `afterShotInBurst` bloom.
    pub after_shot_in_burst_bloom: f64,
}

impl PlayerGunDispersionLaw {
    pub fn validate(self) -> Result<(), PlayerFireClockError> {
        finite_in_range(
            "base_dispersion_radians",
            self.base_dispersion_radians,
            MIN_BASE_DISPERSION_RADIANS,
            MAX_BASE_DISPERSION_RADIANS,
        )?;
        finite_in_range(
            "aiming_time_seconds",
            self.aiming_time_seconds,
            MIN_AIMING_TIME_SECONDS,
            MAX_AIMING_TIME_SECONDS,
        )?;
        nonnegative_bounded(
            "movement_bloom_per_mps",
            self.movement_bloom_per_mps,
            MAX_DESCRIPTOR_BLOOM_FACTOR,
        )?;
        nonnegative_bounded(
            "hull_rotation_bloom_per_rad_s",
            self.hull_rotation_bloom_per_rad_s,
            MAX_DESCRIPTOR_BLOOM_FACTOR,
        )?;
        nonnegative_bounded(
            "turret_rotation_bloom_per_rad_s",
            self.turret_rotation_bloom_per_rad_s,
            MAX_DESCRIPTOR_BLOOM_FACTOR,
        )?;
        nonnegative_bounded(
            "after_shot_bloom",
            self.after_shot_bloom,
            MAX_DESCRIPTOR_BLOOM_FACTOR,
        )?;
        nonnegative_bounded(
            "after_shot_in_burst_bloom",
            self.after_shot_in_burst_bloom,
            MAX_DESCRIPTOR_BLOOM_FACTOR,
        )?;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EffectiveDispersionFactors {
    /// Complete actor-scoped multiplier on the installed dispersion angle.
    pub dispersion_factor: f64,
    /// Complete actor-scoped multiplier on the installed aiming time.
    pub aiming_time_factor: f64,
    /// Equipment and skill multiplier on chassis movement bloom.
    pub movement_bloom_factor: f64,
    /// Equipment and skill multiplier on chassis rotation bloom.
    pub hull_rotation_bloom_factor: f64,
    /// Equipment and skill multiplier on turret rotation bloom.
    pub turret_rotation_bloom_factor: f64,
}

impl EffectiveDispersionFactors {
    pub const IDENTITY: Self = Self {
        dispersion_factor: 1.0,
        aiming_time_factor: 1.0,
        movement_bloom_factor: 1.0,
        hull_rotation_bloom_factor: 1.0,
        turret_rotation_bloom_factor: 1.0,
    };

    pub fn validate(self) -> Result<(), PlayerFireClockError> {
        positive_factor("dispersion_factor", self.dispersion_factor)?;
        positive_factor("aiming_time_factor", self.aiming_time_factor)?;
        positive_factor("movement_bloom_factor", self.movement_bloom_factor)?;
        positive_factor(
            "hull_rotation_bloom_factor",
            self.hull_rotation_bloom_factor,
        )?;
        positive_factor(
            "turret_rotation_bloom_factor",
            self.turret_rotation_bloom_factor,
        )?;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PlayerGunMotion {
    pub linear_speed_mps: f64,
    pub hull_angular_speed_rad_s: f64,
    pub turret_angular_speed_rad_s: f64,
}

impl PlayerGunMotion {
    pub const STATIONARY: Self = Self {
        linear_speed_mps: 0.0,
        hull_angular_speed_rad_s: 0.0,
        turret_angular_speed_rad_s: 0.0,
    };

    pub fn validate(self) -> Result<(), PlayerFireClockError> {
        signed_bounded(
            "linear_speed_mps",
            self.linear_speed_mps,
            MAX_LINEAR_SPEED_MPS,
        )?;
        signed_bounded(
            "hull_angular_speed_rad_s",
            self.hull_angular_speed_rad_s,
            MAX_ANGULAR_SPEED_RADIANS_PER_SECOND,
        )?;
        signed_bounded(
            "turret_angular_speed_rad_s",
            self.turret_angular_speed_rad_s,
            MAX_ANGULAR_SPEED_RADIANS_PER_SECOND,
        )?;
        Ok(())
    }
}

/// Domain-separated identity for one authoritative physical-shell sample.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct PlayerFireLineage {
    /// `round_id` and `authority_epoch` are the server's battle scope.
    pub round_id: u64,
    pub authority_epoch: u64,
    pub player_id: u64,
    pub fire_intent_seq: u64,
    pub physical_round_index: u16,
}

impl PlayerFireLineage {
    pub fn validate(self) -> Result<(), PlayerFireClockError> {
        if self.round_id == 0 {
            return Err(PlayerFireClockError::OutOfRange("round_id"));
        }
        if self.authority_epoch == 0 {
            return Err(PlayerFireClockError::OutOfRange("authority_epoch"));
        }
        if self.player_id == 0 {
            return Err(PlayerFireClockError::OutOfRange("player_id"));
        }
        if self.fire_intent_seq == 0 {
            return Err(PlayerFireClockError::OutOfRange("fire_intent_seq"));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Direction3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Direction3 {
    pub const fn new(x: f64, y: f64, z: f64) -> Self {
        Self { x, y, z }
    }

    fn normalized(self) -> Result<Self, PlayerFireClockError> {
        if !self.x.is_finite() || !self.y.is_finite() || !self.z.is_finite() {
            return Err(PlayerFireClockError::NonFinite("aim_direction"));
        }
        let length_squared = self.x * self.x + self.y * self.y + self.z * self.z;
        if !length_squared.is_finite() {
            return Err(PlayerFireClockError::NonFinite("aim_direction_length"));
        }
        let length = length_squared.sqrt();
        if !(MIN_DIRECTION_LENGTH..=MAX_DIRECTION_LENGTH).contains(&length) {
            return Err(PlayerFireClockError::OutOfRange("aim_direction_length"));
        }
        Ok(Self {
            x: self.x / length,
            y: self.y / length,
            z: self.z / length,
        })
    }

    fn cross(self, other: Self) -> Self {
        Self {
            x: self.y * other.z - self.z * other.y,
            y: self.z * other.x - self.x * other.z,
            z: self.x * other.y - self.y * other.x,
        }
    }

    fn dot(self, other: Self) -> f64 {
        self.x * other.x + self.y * other.y + self.z * other.z
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerDispersionSample {
    pub direction: Direction3,
    pub cone_offset_radians: f64,
    pub azimuth_radians: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerFireClockSnapshot {
    pub law: PlayerGunDispersionLaw,
    pub tick: u64,
    pub dispersion_radians: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerFireClock {
    law: PlayerGunDispersionLaw,
    tick: u64,
    dispersion_radians: f64,
}

impl PlayerFireClock {
    /// Start fully aimed at an exact simulation tick.
    pub fn new(
        law: PlayerGunDispersionLaw,
        factors: EffectiveDispersionFactors,
        tick: u64,
    ) -> Result<Self, PlayerFireClockError> {
        law.validate()?;
        factors.validate()?;
        let dispersion_radians = fully_aimed_dispersion(law, factors)?;
        Ok(Self {
            law,
            tick,
            dispersion_radians,
        })
    }

    pub fn from_snapshot(snapshot: PlayerFireClockSnapshot) -> Result<Self, PlayerFireClockError> {
        snapshot.law.validate()?;
        validate_dispersion(snapshot.dispersion_radians)?;
        Ok(Self {
            law: snapshot.law,
            tick: snapshot.tick,
            dispersion_radians: snapshot.dispersion_radians,
        })
    }

    pub fn snapshot(self) -> PlayerFireClockSnapshot {
        PlayerFireClockSnapshot {
            law: self.law,
            tick: self.tick,
            dispersion_radians: self.dispersion_radians,
        }
    }

    pub fn law(self) -> PlayerGunDispersionLaw {
        self.law
    }

    pub fn tick(self) -> u64 {
        self.tick
    }

    pub fn dispersion_radians(self) -> f64 {
        self.dispersion_radians
    }

    /// Advance through every canonical 30 Hz edge up to `target_tick`.
    ///
    /// Validation and stepping happen on a candidate copy, so a rejected
    /// sample or arithmetic failure cannot partially advance authority state.
    pub fn advance_to_tick(
        &mut self,
        target_tick: u64,
        motion: PlayerGunMotion,
        factors: EffectiveDispersionFactors,
    ) -> Result<f64, PlayerFireClockError> {
        motion.validate()?;
        factors.validate()?;
        if target_tick < self.tick {
            return Err(PlayerFireClockError::TickRegression {
                current: self.tick,
                target: target_tick,
            });
        }
        let tick_count = target_tick - self.tick;
        if tick_count > MAX_ADVANCE_TICKS {
            return Err(PlayerFireClockError::TickGapTooLarge {
                ticks: tick_count,
                maximum: MAX_ADVANCE_TICKS,
            });
        }

        let target_dispersion = motion_target_dispersion(self.law, motion, factors)?;
        let aiming_time = self.law.aiming_time_seconds * factors.aiming_time_factor;
        finite_in_range(
            "effective_aiming_time_seconds",
            aiming_time,
            MIN_AIMING_TIME_SECONDS * MIN_EFFECTIVE_FACTOR,
            MAX_AIMING_TIME_SECONDS * MAX_EFFECTIVE_FACTOR,
        )?;
        let convergence = (-PLAYER_FIRE_TICK_SECONDS / aiming_time.max(0.1)).exp();
        if !convergence.is_finite() || !(0.0..=1.0).contains(&convergence) {
            return Err(PlayerFireClockError::Arithmetic("aim_convergence"));
        }

        let mut candidate = *self;
        for _ in 0..tick_count {
            candidate.dispersion_radians = if candidate.dispersion_radians > target_dispersion {
                target_dispersion + (candidate.dispersion_radians - target_dispersion) * convergence
            } else {
                candidate.dispersion_radians
                    + (target_dispersion - candidate.dispersion_radians) * MOTION_EXPANSION_PER_TICK
            };
            validate_dispersion(candidate.dispersion_radians)?;
            candidate.tick += 1;
        }
        *self = candidate;
        Ok(self.dispersion_radians)
    }

    /// Apply the bloom from one admitted physical shell at the current tick.
    pub fn commit_physical_shot(
        &mut self,
        final_round: bool,
        factors: EffectiveDispersionFactors,
    ) -> Result<f64, PlayerFireClockError> {
        factors.validate()?;
        let base = fully_aimed_dispersion(self.law, factors)?;
        let bloom = if final_round {
            self.law.after_shot_bloom
        } else {
            self.law.after_shot_in_burst_bloom
        };
        let jump = base * bloom;
        if !jump.is_finite() {
            return Err(PlayerFireClockError::Arithmetic("after_shot_bloom"));
        }
        let squared = self
            .dispersion_radians
            .mul_add(self.dispersion_radians, jump * jump);
        if !squared.is_finite() {
            return Err(PlayerFireClockError::Arithmetic("after_shot_bloom"));
        }
        let cap = (base * MAX_DISPERSION_MULTIPLIER).min(MAX_DISPERSION_RADIANS);
        let candidate = squared.sqrt().min(cap);
        validate_dispersion(candidate)?;
        self.dispersion_radians = candidate;
        Ok(candidate)
    }

    /// Sample one bounded two-sigma cone without mutating the aiming clock.
    pub fn sample_direction(
        self,
        lineage: PlayerFireLineage,
        aim_direction: Direction3,
    ) -> Result<PlayerDispersionSample, PlayerFireClockError> {
        lineage.validate()?;
        validate_dispersion(self.dispersion_radians)?;
        let direction = aim_direction.normalized()?;

        let u1 = lineage_unit(lineage, 0);
        let u2 = lineage_unit(lineage, 1);
        let gaussian = (-2.0 * u1.ln()).sqrt() * (2.0 * PI * u2).cos();
        let mut radius = gaussian.abs() * self.dispersion_radians / 2.0;
        if radius > self.dispersion_radians {
            radius = self.dispersion_radians * lineage_unit(lineage, 2);
        }
        let azimuth = 2.0 * PI * lineage_unit(lineage, 3);
        if !radius.is_finite()
            || !(0.0..=self.dispersion_radians).contains(&radius)
            || !azimuth.is_finite()
        {
            return Err(PlayerFireClockError::Arithmetic("dispersion_sample"));
        }

        let reference =
            if direction.x.abs() <= direction.y.abs() && direction.x.abs() <= direction.z.abs() {
                Direction3::new(1.0, 0.0, 0.0)
            } else if direction.y.abs() <= direction.z.abs() {
                Direction3::new(0.0, 1.0, 0.0)
            } else {
                Direction3::new(0.0, 0.0, 1.0)
            };
        let tangent = direction.cross(reference).normalized()?;
        let up = direction.cross(tangent);
        let side = Direction3::new(
            tangent.x * azimuth.cos() + up.x * azimuth.sin(),
            tangent.y * azimuth.cos() + up.y * azimuth.sin(),
            tangent.z * azimuth.cos() + up.z * azimuth.sin(),
        );
        let dispersed = Direction3::new(
            direction.x * radius.cos() + side.x * radius.sin(),
            direction.y * radius.cos() + side.y * radius.sin(),
            direction.z * radius.cos() + side.z * radius.sin(),
        )
        .normalized()?;
        let measured_offset = direction.dot(dispersed).clamp(-1.0, 1.0).acos();
        if !measured_offset.is_finite() || measured_offset > self.dispersion_radians + 1.0e-12 {
            return Err(PlayerFireClockError::Arithmetic("dispersion_cone"));
        }
        Ok(PlayerDispersionSample {
            direction: dispersed,
            cone_offset_radians: radius,
            azimuth_radians: azimuth,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerFireClockError {
    NonFinite(&'static str),
    OutOfRange(&'static str),
    TickRegression { current: u64, target: u64 },
    TickGapTooLarge { ticks: u64, maximum: u64 },
    Arithmetic(&'static str),
}

impl fmt::Display for PlayerFireClockError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonFinite(field) => write!(formatter, "{field} must be finite"),
            Self::OutOfRange(field) => write!(formatter, "{field} is out of range"),
            Self::TickRegression { current, target } => write!(
                formatter,
                "player fire tick regressed from {current} to {target}"
            ),
            Self::TickGapTooLarge { ticks, maximum } => {
                write!(formatter, "player fire tick gap {ticks} exceeds {maximum}")
            }
            Self::Arithmetic(stage) => {
                write!(formatter, "player fire arithmetic failed at {stage}")
            }
        }
    }
}

impl Error for PlayerFireClockError {}

fn fully_aimed_dispersion(
    law: PlayerGunDispersionLaw,
    factors: EffectiveDispersionFactors,
) -> Result<f64, PlayerFireClockError> {
    let value = law.base_dispersion_radians * factors.dispersion_factor;
    validate_dispersion(value)?;
    Ok(value)
}

fn motion_target_dispersion(
    law: PlayerGunDispersionLaw,
    motion: PlayerGunMotion,
    factors: EffectiveDispersionFactors,
) -> Result<f64, PlayerFireClockError> {
    let movement =
        motion.linear_speed_mps.abs() * law.movement_bloom_per_mps * factors.movement_bloom_factor;
    let hull_rotation = motion.hull_angular_speed_rad_s.abs()
        * law.hull_rotation_bloom_per_rad_s
        * factors.hull_rotation_bloom_factor;
    let turret_rotation = motion.turret_angular_speed_rad_s.abs()
        * law.turret_rotation_bloom_per_rad_s
        * factors.turret_rotation_bloom_factor;
    let squared = 1.0
        + movement * movement
        + hull_rotation * hull_rotation
        + turret_rotation * turret_rotation;
    if !squared.is_finite() {
        return Err(PlayerFireClockError::Arithmetic("motion_bloom"));
    }
    let value = fully_aimed_dispersion(law, factors)? * squared.sqrt();
    validate_dispersion(value)?;
    Ok(value)
}

fn validate_dispersion(value: f64) -> Result<(), PlayerFireClockError> {
    finite_in_range(
        "dispersion_radians",
        value,
        MIN_DISPERSION_RADIANS,
        MAX_DISPERSION_RADIANS,
    )
}

fn positive_factor(field: &'static str, value: f64) -> Result<(), PlayerFireClockError> {
    finite_in_range(field, value, MIN_EFFECTIVE_FACTOR, MAX_EFFECTIVE_FACTOR)
}

fn nonnegative_bounded(
    field: &'static str,
    value: f64,
    maximum: f64,
) -> Result<(), PlayerFireClockError> {
    finite_in_range(field, value, 0.0, maximum)
}

fn signed_bounded(
    field: &'static str,
    value: f64,
    maximum_absolute: f64,
) -> Result<(), PlayerFireClockError> {
    finite_in_range(field, value, -maximum_absolute, maximum_absolute)
}

fn finite_in_range(
    field: &'static str,
    value: f64,
    minimum: f64,
    maximum: f64,
) -> Result<(), PlayerFireClockError> {
    if !value.is_finite() {
        return Err(PlayerFireClockError::NonFinite(field));
    }
    if !(minimum..=maximum).contains(&value) {
        return Err(PlayerFireClockError::OutOfRange(field));
    }
    Ok(())
}

fn lineage_unit(lineage: PlayerFireLineage, lane: u64) -> f64 {
    let mut value = 0x706c_6179_6572_2d66_u64
        ^ lineage.round_id.rotate_left(7)
        ^ lineage.authority_epoch.rotate_left(19)
        ^ lineage.player_id.rotate_left(31)
        ^ lineage.fire_intent_seq.rotate_left(43)
        ^ u64::from(lineage.physical_round_index).rotate_left(53)
        ^ lane.wrapping_mul(0x9e37_79b9_7f4a_7c15);
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^= value >> 31;
    // The half-step keeps Box-Muller's logarithm strictly inside (0, 1).
    ((value >> 11) as f64 + 0.5) * (1.0 / ((1_u64 << 53) as f64))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn law() -> PlayerGunDispersionLaw {
        PlayerGunDispersionLaw {
            base_dispersion_radians: 0.01,
            aiming_time_seconds: 2.0,
            movement_bloom_per_mps: 0.2,
            hull_rotation_bloom_per_rad_s: 0.4,
            turret_rotation_bloom_per_rad_s: 0.3,
            after_shot_bloom: 1.5,
            after_shot_in_burst_bloom: 0.75,
        }
    }

    fn lineage() -> PlayerFireLineage {
        PlayerFireLineage {
            round_id: 7,
            authority_epoch: 41,
            player_id: 3,
            fire_intent_seq: 11,
            physical_round_index: 0,
        }
    }

    #[test]
    fn stationary_clock_converges_after_shot_bloom() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let mut clock = PlayerFireClock::new(law(), factors, 0).unwrap();
        let base = clock.dispersion_radians();
        let bloomed = clock.commit_physical_shot(true, factors).unwrap();
        assert!(bloomed > base);

        clock
            .advance_to_tick(180, PlayerGunMotion::STATIONARY, factors)
            .unwrap();
        assert!(clock.dispersion_radians() < bloomed);
        assert!(clock.dispersion_radians() > base);
        assert!(clock.dispersion_radians() - base < 0.001);
    }

    #[test]
    fn movement_and_rotation_expand_the_circle() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let mut stationary = PlayerFireClock::new(law(), factors, 0).unwrap();
        let mut moving = stationary;
        stationary
            .advance_to_tick(20, PlayerGunMotion::STATIONARY, factors)
            .unwrap();
        moving
            .advance_to_tick(
                20,
                PlayerGunMotion {
                    linear_speed_mps: 8.0,
                    hull_angular_speed_rad_s: 0.4,
                    turret_angular_speed_rad_s: -0.6,
                },
                factors,
            )
            .unwrap();
        assert!(moving.dispersion_radians() > stationary.dispersion_radians());
    }

    #[test]
    fn final_and_intra_burst_shots_apply_distinct_bloom() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let base = PlayerFireClock::new(law(), factors, 0).unwrap();
        let mut intra = base;
        let mut final_round = base;
        intra.commit_physical_shot(false, factors).unwrap();
        final_round.commit_physical_shot(true, factors).unwrap();
        assert!(intra.dispersion_radians() > base.dispersion_radians());
        assert!(final_round.dispersion_radians() > intra.dispersion_radians());
    }

    #[test]
    fn tick_chunking_is_exactly_equivalent() {
        let factors = EffectiveDispersionFactors {
            dispersion_factor: 0.91,
            aiming_time_factor: 0.84,
            movement_bloom_factor: 0.8,
            hull_rotation_bloom_factor: 0.8,
            turret_rotation_bloom_factor: 0.7,
        };
        let motion = PlayerGunMotion {
            linear_speed_mps: 6.5,
            hull_angular_speed_rad_s: -0.3,
            turret_angular_speed_rad_s: 0.45,
        };
        let mut whole = PlayerFireClock::new(law(), factors, 0).unwrap();
        let mut split = whole;
        whole.advance_to_tick(90, motion, factors).unwrap();
        split.advance_to_tick(17, motion, factors).unwrap();
        split.advance_to_tick(53, motion, factors).unwrap();
        split.advance_to_tick(90, motion, factors).unwrap();
        assert_eq!(whole, split);

        let restored = PlayerFireClock::from_snapshot(whole.snapshot()).unwrap();
        assert_eq!(restored, whole);
    }

    #[test]
    fn lineage_replay_is_deterministic_and_bounded() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let clock = PlayerFireClock::new(law(), factors, 9).unwrap();
        let direction = Direction3::new(0.2, -0.1, 0.97);
        let first = clock.sample_direction(lineage(), direction).unwrap();
        let replay = clock.sample_direction(lineage(), direction).unwrap();
        assert_eq!(first, replay);
        assert!(first.cone_offset_radians <= clock.dispersion_radians());

        let aim = direction.normalized().unwrap();
        let measured = aim.dot(first.direction).clamp(-1.0, 1.0).acos();
        assert!(measured <= clock.dispersion_radians() + 1.0e-12);
    }

    #[test]
    fn different_fire_lineage_changes_the_sample() {
        let clock = PlayerFireClock::new(law(), EffectiveDispersionFactors::IDENTITY, 0).unwrap();
        let first = clock
            .sample_direction(lineage(), Direction3::new(0.0, 0.0, 1.0))
            .unwrap();
        let mut next = lineage();
        next.fire_intent_seq += 1;
        let second = clock
            .sample_direction(next, Direction3::new(0.0, 0.0, 1.0))
            .unwrap();
        assert_ne!(first, second);
    }

    #[test]
    fn zero_nan_and_out_of_range_inputs_fail_without_mutation() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let mut invalid_law = law();
        invalid_law.base_dispersion_radians = 0.0;
        assert_eq!(
            PlayerFireClock::new(invalid_law, factors, 0),
            Err(PlayerFireClockError::OutOfRange("base_dispersion_radians"))
        );

        let mut clock = PlayerFireClock::new(law(), factors, 4).unwrap();
        let before = clock;
        assert_eq!(
            clock.advance_to_tick(
                5,
                PlayerGunMotion {
                    linear_speed_mps: f64::NAN,
                    ..PlayerGunMotion::STATIONARY
                },
                factors,
            ),
            Err(PlayerFireClockError::NonFinite("linear_speed_mps"))
        );
        assert_eq!(clock, before);
        assert_eq!(
            clock.advance_to_tick(
                5,
                PlayerGunMotion {
                    linear_speed_mps: MAX_LINEAR_SPEED_MPS + 1.0,
                    ..PlayerGunMotion::STATIONARY
                },
                factors,
            ),
            Err(PlayerFireClockError::OutOfRange("linear_speed_mps"))
        );
        assert_eq!(clock, before);

        assert_eq!(
            clock.sample_direction(lineage(), Direction3::new(0.0, 0.0, 0.0)),
            Err(PlayerFireClockError::OutOfRange("aim_direction_length"))
        );
        let mut invalid_lineage = lineage();
        invalid_lineage.fire_intent_seq = 0;
        assert_eq!(
            clock.sample_direction(invalid_lineage, Direction3::new(0.0, 0.0, 1.0)),
            Err(PlayerFireClockError::OutOfRange("fire_intent_seq"))
        );
    }

    #[test]
    fn invalid_snapshot_and_tick_edges_fail_closed() {
        let factors = EffectiveDispersionFactors::IDENTITY;
        let mut clock = PlayerFireClock::new(law(), factors, 10).unwrap();
        let before = clock;
        assert!(matches!(
            clock.advance_to_tick(9, PlayerGunMotion::STATIONARY, factors),
            Err(PlayerFireClockError::TickRegression { .. })
        ));
        assert_eq!(clock, before);
        assert!(matches!(
            clock.advance_to_tick(
                10 + MAX_ADVANCE_TICKS + 1,
                PlayerGunMotion::STATIONARY,
                factors,
            ),
            Err(PlayerFireClockError::TickGapTooLarge { .. })
        ));
        assert_eq!(clock, before);

        let mut snapshot = clock.snapshot();
        snapshot.dispersion_radians = f64::INFINITY;
        assert_eq!(
            PlayerFireClock::from_snapshot(snapshot),
            Err(PlayerFireClockError::NonFinite("dispersion_radians"))
        );
    }
}
