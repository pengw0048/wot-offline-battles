//! Deterministic tank-to-tank collision and ramming authority.
//!
//! Visible clients may submit an immutable contact receipt, but never a damage
//! verdict. The receipt is replayed against the exact canonical bot history
//! frame named by `(bot_state_revision, presentation_time_us)`. A missing or
//! contradictory historical frame is terminal with zero damage.

use crate::combat::{DamageProposal, DamageSource, VehicleKey, VehicleKind, MAX_COMBAT_ID};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use thiserror::Error;

pub const POSITION_SLOP: f64 = 0.01;
pub const POSITION_PERCENT: f64 = 0.95;
pub const VERTICAL_SLOP: f64 = 0.02;
/// The exact ramming law has no gameplay damage cap. The wire/storage type is
/// the only bound at this layer; the final battle commit performs its own
/// server-range validation.
pub const MAX_RAM_DAMAGE: u32 = u32::MAX;
pub const MAX_PENDING_RAM_CONTACTS: usize = 16;
// Receipts may legally trail the current bot revision by 255 publications.
// Keep a bracket on both sides so interpolation does not evict the left edge.
pub const MAX_BOT_HISTORY_FRAMES: usize = 512;
pub const MAX_RECEIPT_HISTORY: usize = 64;
pub const MAX_RAM_POSE_FRAMES_PER_VEHICLE: usize = 512;
pub const MAX_RAM_POSE_RETRY_HISTORY: usize = 64;
pub const MAX_RAM_POSE_SAMPLE_GAP_US: u64 = 250_000;
/// Bound one-frame skew between the native callback point and the visible
/// client's copied player-body pose. Bot history and native evidence remain
/// exact.
pub const RAM_CONTACT_POINT_SLOP: f64 = 0.75;

pub const RAM_WEIGHT_SCALE: f64 = 0.001;
pub const RAM_KINETIC_FACTOR: f64 = 0.5;
pub const RAM_HE_DAMAGE_FACTOR: f64 = 0.5;
pub const RAM_ARMOR_ABSORPTION_FACTOR: f64 = 1.1;
/// Temporary product tuning while the exact #1513 ramming curve is audited.
/// Preserve every physical input and modifier; scale only final HP loss.
pub const RAM_DAMAGE_COEFFICIENT: f64 = 0.25;
pub const MIN_SPALL_COEFFICIENT: f64 = 1.0;
pub const MAX_SPALL_COEFFICIENT: f64 = 1.5;
pub const MAX_CONTROLLED_IMPACT_BONUS: f64 = 0.15;

const MAX_EXACT_INT: u64 = 9_007_199_254_740_991;
const MAX_RECEIPT_SEQUENCE: u64 = 2_147_483_647;
const MAX_BOT_ID: u64 = 30;
const MAX_POSITION_XZ: f64 = 2_000.0;
const MIN_POSITION_Y: f64 = -1_000.0;
const MAX_POSITION_Y: f64 = 1_000.0;
const MAX_VELOCITY: f64 = 200.0;
const MAX_MASS: f64 = 10_000_000.0;
const MAX_SHAPE_EXTENT: f64 = 100.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RamDamageProfile {
    spall_coefficient: f64,
    controlled_impact_bonus: f64,
}

impl RamDamageProfile {
    /// Construct descriptor/crew inputs without silently normalizing them.
    pub fn new(spall_coefficient: f64, controlled_impact_bonus: f64) -> Result<Self, RamError> {
        if !spall_coefficient.is_finite()
            || !(MIN_SPALL_COEFFICIENT..=MAX_SPALL_COEFFICIENT).contains(&spall_coefficient)
            || !controlled_impact_bonus.is_finite()
            || !(0.0..=MAX_CONTROLLED_IMPACT_BONUS).contains(&controlled_impact_bonus)
        {
            return Err(RamError::InvalidDamageProfile);
        }
        Ok(Self {
            spall_coefficient,
            controlled_impact_bonus,
        })
    }

    pub fn spall_coefficient(self) -> f64 {
        self.spall_coefficient
    }

    pub fn controlled_impact_bonus(self) -> f64 {
        self.controlled_impact_bonus
    }
}

impl Default for RamDamageProfile {
    fn default() -> Self {
        Self {
            spall_coefficient: MIN_SPALL_COEFFICIENT,
            controlled_impact_bonus: 0.0,
        }
    }
}

/// Armour returned by the native collision model at this exact hull contact.
/// This deliberately has no descriptor-summary or hull-primary fallback.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NativeContactArmor {
    millimeters: f64,
}

impl NativeContactArmor {
    pub fn new(millimeters: f64) -> Result<Self, RamError> {
        if !millimeters.is_finite() || millimeters < 0.0 {
            return Err(RamError::InvalidContactArmor);
        }
        Ok(Self { millimeters })
    }

    pub fn millimeters(self) -> f64 {
        self.millimeters
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RamVehicleContactEvidence {
    armor: NativeContactArmor,
    profile: RamDamageProfile,
}

impl RamVehicleContactEvidence {
    pub fn new(armor: NativeContactArmor, profile: RamDamageProfile) -> Self {
        Self { armor, profile }
    }

    pub fn armor(self) -> NativeContactArmor {
        self.armor
    }

    pub fn profile(self) -> RamDamageProfile {
        self.profile
    }
}

/// A source-owned cursor. `VehicleKey` remains part of every timeline key, so
/// player 7 and bot 7 are distinct identities even at the same cursor.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct RamSourceCursor {
    episode: u64,
    frontier: u64,
}

impl RamSourceCursor {
    pub fn new(episode: u64, frontier: u64) -> Result<Self, RamError> {
        if episode > MAX_RECEIPT_SEQUENCE || frontier > MAX_RECEIPT_SEQUENCE {
            return Err(RamError::InvalidSourceCursor);
        }
        Ok(Self { episode, frontier })
    }

    pub fn episode(self) -> u64 {
        self.episode
    }

    pub fn frontier(self) -> u64 {
        self.frontier
    }
}

/// Immutable native evidence for the canonical ordering in `pair`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NativeRamContactEvidence {
    pub pair: RamPair,
    pub cursor: RamSourceCursor,
    pub source_time_us: u64,
    pub first: RamVehicleContactEvidence,
    pub second: RamVehicleContactEvidence,
    pub first_moving: bool,
    pub second_moving: bool,
}

impl NativeRamContactEvidence {
    pub fn new(
        pair: RamPair,
        cursor: RamSourceCursor,
        source_time_us: u64,
        first: RamVehicleContactEvidence,
        second: RamVehicleContactEvidence,
        first_moving: bool,
        second_moving: bool,
    ) -> Result<Self, RamError> {
        if source_time_us > MAX_EXACT_INT {
            return Err(RamError::InvalidNativeEvidence);
        }
        Ok(Self {
            pair,
            cursor,
            source_time_us,
            first,
            second,
            first_moving,
            second_moving,
        })
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ExactRamDamage {
    /// HP received by the canonical first vehicle.
    pub first: u32,
    /// HP received by the canonical second vehicle.
    pub second: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RamShape {
    pub half_width: f64,
    pub half_length: f64,
    pub lower_y: f64,
    pub upper_y: f64,
}

impl RamShape {
    pub fn new(
        half_width: f64,
        half_length: f64,
        lower_y: f64,
        upper_y: f64,
    ) -> Result<Self, RamError> {
        let shape = Self {
            half_width,
            half_length,
            lower_y,
            upper_y,
        };
        shape.validate()?;
        Ok(shape)
    }

    fn validate(self) -> Result<(), RamError> {
        if ![
            self.half_width,
            self.half_length,
            self.lower_y,
            self.upper_y,
        ]
        .into_iter()
        .all(f64::is_finite)
            || !(0.0 < self.half_width && self.half_width <= MAX_SHAPE_EXTENT)
            || !(0.0 < self.half_length && self.half_length <= MAX_SHAPE_EXTENT)
            || self.lower_y >= self.upper_y
            || self.lower_y < -MAX_SHAPE_EXTENT
            || self.upper_y > MAX_SHAPE_EXTENT
        {
            return Err(RamError::InvalidBody);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RamBody {
    pub key: VehicleKey,
    /// Canonical battle team copied from the server-owned entity roster.
    pub team: u8,
    pub alive: bool,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub mass: f64,
    pub vx: f64,
    pub vy: f64,
    pub vz: f64,
    pub turret_yaw: f64,
    pub gun_pitch: f64,
    pub siege_state: u8,
    pub shape: RamShape,
}

impl RamBody {
    pub fn validate(&self) -> Result<(), RamError> {
        if self.key.id == 0
            || self.key.id > MAX_COMBAT_ID
            || !(1..=2).contains(&self.team)
            || ![
                self.x,
                self.y,
                self.z,
                self.yaw,
                self.pitch,
                self.roll,
                self.mass,
                self.vx,
                self.vy,
                self.vz,
                self.turret_yaw,
                self.gun_pitch,
            ]
            .into_iter()
            .all(f64::is_finite)
            || self.x.abs() > MAX_POSITION_XZ
            || self.z.abs() > MAX_POSITION_XZ
            || !(MIN_POSITION_Y..=MAX_POSITION_Y).contains(&self.y)
            || !(1.0..=MAX_MASS).contains(&self.mass)
            || self.vx.abs() > MAX_VELOCITY
            || self.vy.abs() > MAX_VELOCITY
            || self.vz.abs() > MAX_VELOCITY
            || self.siege_state > 3
        {
            return Err(RamError::InvalidBody);
        }
        self.shape.validate()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct RamPair {
    pub first: VehicleKey,
    pub second: VehicleKey,
}

impl RamPair {
    pub fn new(first: VehicleKey, second: VehicleKey) -> Result<Self, RamError> {
        if first == second {
            return Err(RamError::DuplicateBody(first));
        }
        Ok(if first < second {
            Self { first, second }
        } else {
            Self {
                first: second,
                second: first,
            }
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RamPoseFrame {
    cursor: RamSourceCursor,
    source_time_us: u64,
    body: RamBody,
}

impl RamPoseFrame {
    pub fn new(
        cursor: RamSourceCursor,
        source_time_us: u64,
        body: RamBody,
    ) -> Result<Self, RamError> {
        body.validate()?;
        if source_time_us > MAX_EXACT_INT {
            return Err(RamError::InvalidPoseFrame);
        }
        Ok(Self {
            cursor,
            source_time_us,
            body,
        })
    }

    pub fn cursor(&self) -> RamSourceCursor {
        self.cursor
    }

    pub fn source_time_us(&self) -> u64 {
        self.source_time_us
    }

    pub fn body(&self) -> &RamBody {
        &self.body
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RamPoseAdmission {
    New,
    DiscontinuityReset,
    ExactRetry,
}

#[derive(Clone, Debug, PartialEq)]
pub enum RamPoseLookup {
    Pending,
    Unavailable,
    Found(RamBody),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RamPoseQuery {
    pub key: VehicleKey,
    pub cursor: RamSourceCursor,
    pub source_time_us: u64,
}

impl RamPoseQuery {
    pub fn new(
        key: VehicleKey,
        cursor: RamSourceCursor,
        source_time_us: u64,
    ) -> Result<Self, RamError> {
        if key.id == 0 || key.id > MAX_COMBAT_ID || source_time_us > MAX_EXACT_INT {
            return Err(RamError::InvalidPoseQuery);
        }
        Ok(Self {
            key,
            cursor,
            source_time_us,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum RamPosePairLookup {
    Pending,
    Unavailable,
    Found {
        pair: RamPair,
        first: RamBody,
        second: RamBody,
    },
}

#[derive(Clone, Debug, Default)]
struct RamPoseStream {
    frames: VecDeque<RamPoseFrame>,
    fingerprints: BTreeMap<RamSourceCursor, RamPoseFrame>,
    fingerprint_order: VecDeque<RamSourceCursor>,
    latest: Option<(RamSourceCursor, u64)>,
}

/// Generic source-time history for any `VehicleKey`, including player/player
/// pairs. New cursors and timestamps advance strictly; an immutable retained
/// frame is the only accepted retry. Lookups require a real bracket and never
/// extrapolate a pose.
#[derive(Clone, Debug, Default)]
pub struct RamPoseTimeline {
    streams: BTreeMap<VehicleKey, RamPoseStream>,
}

impl RamPoseTimeline {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record(&mut self, frame: RamPoseFrame) -> Result<RamPoseAdmission, RamError> {
        self.record_with_discontinuity_policy(frame, false)
    }

    /// Admit an ordered live input pose while containing a discontinuous
    /// client clock to this vehicle's historical collision stream. Cursor and
    /// retry fences remain strict, but interpolation never crosses a clock
    /// rewind or an unbounded sample gap.
    pub fn record_streaming(&mut self, frame: RamPoseFrame) -> Result<RamPoseAdmission, RamError> {
        self.record_with_discontinuity_policy(frame, true)
    }

    fn record_with_discontinuity_policy(
        &mut self,
        frame: RamPoseFrame,
        reset_discontinuity: bool,
    ) -> Result<RamPoseAdmission, RamError> {
        frame.body.validate()?;
        if frame.source_time_us > MAX_EXACT_INT
            || frame.cursor.episode > MAX_RECEIPT_SEQUENCE
            || frame.cursor.frontier > MAX_RECEIPT_SEQUENCE
        {
            return Err(RamError::InvalidPoseFrame);
        }
        let key = frame.body.key;
        let stream = self.streams.entry(key).or_default();
        if let Some(previous) = stream.fingerprints.get(&frame.cursor) {
            return if previous == &frame {
                Ok(RamPoseAdmission::ExactRetry)
            } else {
                Err(RamError::PoseFrameConflict {
                    key,
                    cursor: frame.cursor,
                })
            };
        }
        let mut discontinuity = false;
        if let Some((latest_cursor, latest_time_us)) = stream.latest {
            if frame.cursor <= latest_cursor {
                return Err(RamError::StalePoseRetry {
                    key,
                    cursor: frame.cursor,
                });
            }
            let time_regressed = frame.source_time_us <= latest_time_us;
            if time_regressed && !reset_discontinuity {
                return Err(RamError::PoseTimelineRegression(key));
            }
            discontinuity = reset_discontinuity
                && (time_regressed
                    || frame.source_time_us.saturating_sub(latest_time_us)
                        > MAX_RAM_POSE_SAMPLE_GAP_US);
        }

        if discontinuity {
            stream.frames.clear();
        }
        stream.latest = Some((frame.cursor, frame.source_time_us));
        stream.frames.push_back(frame.clone());
        stream.fingerprints.insert(frame.cursor, frame);
        stream.fingerprint_order.push_back(
            stream
                .frames
                .back()
                .expect("the admitted pose frame was retained")
                .cursor,
        );
        while stream.frames.len() > MAX_RAM_POSE_FRAMES_PER_VEHICLE {
            stream.frames.pop_front();
        }
        while stream.fingerprint_order.len() > MAX_RAM_POSE_RETRY_HISTORY {
            if let Some(cursor) = stream.fingerprint_order.pop_front() {
                stream.fingerprints.remove(&cursor);
            }
        }
        Ok(if discontinuity {
            RamPoseAdmission::DiscontinuityReset
        } else {
            RamPoseAdmission::New
        })
    }

    pub fn body_at(&self, query: RamPoseQuery) -> Result<RamPoseLookup, RamError> {
        if query.key.id == 0 || query.key.id > MAX_COMBAT_ID || query.source_time_us > MAX_EXACT_INT
        {
            return Err(RamError::InvalidPoseQuery);
        }
        let Some(stream) = self.streams.get(&query.key) else {
            return Ok(RamPoseLookup::Pending);
        };
        let Some((latest_cursor, _)) = stream.latest else {
            return Ok(RamPoseLookup::Pending);
        };
        if query.cursor > latest_cursor {
            return Ok(RamPoseLookup::Pending);
        }

        let target = (query.source_time_us, query.cursor.frontier);
        let mut left: Option<&RamPoseFrame> = None;
        let mut right: Option<&RamPoseFrame> = None;
        for frame in &stream.frames {
            if frame.cursor.episode != query.cursor.episode
                || frame.cursor.frontier > query.cursor.frontier
            {
                continue;
            }
            let frame_key = (frame.source_time_us, frame.cursor.frontier);
            if frame_key <= target {
                left = Some(frame);
            }
            if frame_key >= target {
                right = Some(frame);
                break;
            }
        }
        let (Some(left), Some(right)) = (left, right) else {
            return Ok(RamPoseLookup::Unavailable);
        };
        if left.cursor == right.cursor {
            return Ok(RamPoseLookup::Found(left.body.clone()));
        }
        Ok(
            match interpolate_body(
                &left.body,
                left.source_time_us,
                &right.body,
                right.source_time_us,
                query.source_time_us,
            ) {
                Some(body) => RamPoseLookup::Found(body),
                None => RamPoseLookup::Unavailable,
            },
        )
    }

    pub fn pair_at(
        &self,
        first_query: RamPoseQuery,
        second_query: RamPoseQuery,
    ) -> Result<RamPosePairLookup, RamError> {
        let pair = RamPair::new(first_query.key, second_query.key)?;
        let first = self.body_at(first_query)?;
        let second = self.body_at(second_query)?;
        match (first, second) {
            (RamPoseLookup::Pending, _) | (_, RamPoseLookup::Pending) => {
                Ok(RamPosePairLookup::Pending)
            }
            (RamPoseLookup::Unavailable, _) | (_, RamPoseLookup::Unavailable) => {
                Ok(RamPosePairLookup::Unavailable)
            }
            (RamPoseLookup::Found(first), RamPoseLookup::Found(second)) => {
                let (first, second) = if first.key == pair.first {
                    (first, second)
                } else {
                    (second, first)
                };
                Ok(RamPosePairLookup::Found {
                    pair,
                    first,
                    second,
                })
            }
        }
    }

    /// Reconstruct two player bodies at one server-time presentation edge.
    /// A future edge remains pending until both streams reach it; an edge
    /// older than the retained real bracket is terminally unavailable.
    pub fn pair_at_source_time(
        &self,
        first_key: VehicleKey,
        second_key: VehicleKey,
        source_time_us: u64,
    ) -> Result<RamPosePairLookup, RamError> {
        let pair = RamPair::new(first_key, second_key)?;
        let first = self.body_at_source_time(pair.first, source_time_us)?;
        let second = self.body_at_source_time(pair.second, source_time_us)?;
        match (first, second) {
            (RamPoseLookup::Pending, _) | (_, RamPoseLookup::Pending) => {
                Ok(RamPosePairLookup::Pending)
            }
            (RamPoseLookup::Unavailable, _) | (_, RamPoseLookup::Unavailable) => {
                Ok(RamPosePairLookup::Unavailable)
            }
            (RamPoseLookup::Found(first), RamPoseLookup::Found(second)) => {
                Ok(RamPosePairLookup::Found {
                    pair,
                    first,
                    second,
                })
            }
        }
    }

    fn body_at_source_time(
        &self,
        key: VehicleKey,
        source_time_us: u64,
    ) -> Result<RamPoseLookup, RamError> {
        if key.id == 0 || key.id > MAX_COMBAT_ID || source_time_us > MAX_EXACT_INT {
            return Err(RamError::InvalidPoseQuery);
        }
        let Some(stream) = self.streams.get(&key) else {
            return Ok(RamPoseLookup::Pending);
        };
        let Some((_, latest_time_us)) = stream.latest else {
            return Ok(RamPoseLookup::Pending);
        };
        if latest_time_us < source_time_us {
            return Ok(RamPoseLookup::Pending);
        }
        let left = stream
            .frames
            .iter()
            .rev()
            .find(|frame| frame.source_time_us <= source_time_us);
        let right = stream
            .frames
            .iter()
            .find(|frame| frame.source_time_us >= source_time_us);
        let (Some(left), Some(right)) = (left, right) else {
            return Ok(RamPoseLookup::Unavailable);
        };
        if left.cursor == right.cursor {
            return Ok(RamPoseLookup::Found(left.body.clone()));
        }
        if right.source_time_us.saturating_sub(left.source_time_us) > MAX_RAM_POSE_SAMPLE_GAP_US {
            return Ok(RamPoseLookup::Unavailable);
        }
        Ok(
            match interpolate_body_with_velocity(
                &left.body,
                left.source_time_us,
                &right.body,
                right.source_time_us,
                source_time_us,
            ) {
                Some(body) => RamPoseLookup::Found(body),
                None => RamPoseLookup::Unavailable,
            },
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RamBodyDelta {
    pub key: VehicleKey,
    pub correction_x: f64,
    pub correction_z: f64,
    pub velocity_x: f64,
    pub velocity_z: f64,
}

impl RamBodyDelta {
    fn zero(key: VehicleKey) -> Self {
        Self {
            key,
            correction_x: 0.0,
            correction_z: 0.0,
            velocity_x: 0.0,
            velocity_z: 0.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AtomicRamDamage {
    pub operation_id: String,
    pub first: DamageProposal,
    pub second: DamageProposal,
}

impl AtomicRamDamage {
    pub fn validate(&self) -> Result<(), RamError> {
        if self.operation_id.is_empty()
            || self.operation_id.len() > 96
            || self.first.source != DamageSource::Ram
            || self.second.source != DamageSource::Ram
            || self.first.target == self.second.target
            || self.first.attacker != Some(self.second.target)
            || self.second.attacker != Some(self.first.target)
        {
            return Err(RamError::InvalidDamage);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RamResolutionSource {
    FixedTick {
        time_us: u64,
    },
    PlayerReceipt {
        player_id: u64,
        sequence: u64,
        bot_state_revision: u64,
        presentation_time_us: u64,
    },
    PlayerPairReceipt {
        reporter_player_id: u64,
        sequence: u64,
        target_player_id: u64,
        presentation_time_us: u64,
    },
}

/// One immutable, Rust-owned impact which still needs two structural armour
/// measurements from the hidden native oracle. The client contributes only
/// its frozen contact pose/point for a delayed player receipt; profiles,
/// episode identity and every damage input remain server-owned.
#[derive(Clone, Debug, PartialEq)]
pub struct RamContactProbe {
    pub source: RamResolutionSource,
    pub pair: RamPair,
    pub cursor: RamSourceCursor,
    pub source_time_us: u64,
    pub first: RamBody,
    pub second: RamBody,
    pub contact_x: f64,
    pub contact_y: f64,
    pub contact_z: f64,
    /// Canonical second-to-first unit normal.
    pub normal_x: f64,
    pub normal_z: f64,
    pub first_moving: bool,
    pub second_moving: bool,
}

impl RamContactProbe {
    pub fn key(&self) -> (RamPair, RamSourceCursor) {
        (self.pair, self.cursor)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RamResolutionOutcome {
    Damage,
    Contact,
    ActiveContact,
    NoContact,
    VehicleUnavailable,
    HistoricalStateUnavailable,
    NativeEvidenceUnavailable,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RamResolution {
    pub source: RamResolutionSource,
    pub pair: RamPair,
    pub first_delta: RamBodyDelta,
    pub second_delta: RamBodyDelta,
    pub damage: Option<AtomicRamDamage>,
    pub outcome: RamResolutionOutcome,
}

impl RamResolution {
    pub fn deltas(&self) -> impl Iterator<Item = &RamBodyDelta> {
        [&self.first_delta, &self.second_delta].into_iter()
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct RamFrameResolution {
    pub contacts: Vec<RamResolution>,
}

impl RamFrameResolution {
    pub fn damage_transactions(&self) -> impl Iterator<Item = &AtomicRamDamage> {
        self.contacts
            .iter()
            .filter_map(|resolution| resolution.damage.as_ref())
    }

    pub fn deltas(&self) -> impl Iterator<Item = &RamBodyDelta> {
        self.contacts.iter().flat_map(RamResolution::deltas)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct PlayerRamReceipt {
    pub sequence: u64,
    pub bot_id: u64,
    pub bot_state_revision: u64,
    pub presentation_time_us: u64,
    pub contact_x: f64,
    pub contact_y: f64,
    pub contact_z: f64,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub vx: f64,
    pub vy: f64,
    pub vz: f64,
    pub turret_yaw: f64,
    pub gun_pitch: f64,
    pub siege_state: u8,
}

impl PlayerRamReceipt {
    pub fn parse(value: &Value) -> Result<Self, RamError> {
        const FIELDS: [&str; 19] = [
            "seq",
            "bot_id",
            "bot_state_revision",
            "presentation_time_us",
            "contact_x",
            "contact_y",
            "contact_z",
            "x",
            "y",
            "z",
            "yaw",
            "pitch",
            "roll",
            "vx",
            "vy",
            "vz",
            "turret_yaw",
            "gun_pitch",
            "siege_state",
        ];
        let object = value.as_object().ok_or(RamError::InvalidReceipt)?;
        if object.len() != FIELDS.len()
            || object.keys().any(|field| !FIELDS.contains(&field.as_str()))
        {
            return Err(RamError::InvalidReceipt);
        }
        let receipt = Self {
            sequence: exact_u64(object, "seq", 1, MAX_RECEIPT_SEQUENCE)?,
            bot_id: exact_u64(object, "bot_id", 1, MAX_BOT_ID)?,
            bot_state_revision: exact_u64(object, "bot_state_revision", 0, MAX_RECEIPT_SEQUENCE)?,
            presentation_time_us: exact_u64(object, "presentation_time_us", 0, MAX_EXACT_INT)?,
            contact_x: finite_f64(object, "contact_x", -MAX_POSITION_XZ, MAX_POSITION_XZ)?,
            contact_y: finite_f64(object, "contact_y", MIN_POSITION_Y, MAX_POSITION_Y)?,
            contact_z: finite_f64(object, "contact_z", -MAX_POSITION_XZ, MAX_POSITION_XZ)?,
            x: finite_f64(object, "x", -MAX_POSITION_XZ, MAX_POSITION_XZ)?,
            y: finite_f64(object, "y", MIN_POSITION_Y, MAX_POSITION_Y)?,
            z: finite_f64(object, "z", -MAX_POSITION_XZ, MAX_POSITION_XZ)?,
            yaw: finite_f64(object, "yaw", -1.0e6, 1.0e6)?,
            pitch: finite_f64(object, "pitch", -1.0e6, 1.0e6)?,
            roll: finite_f64(object, "roll", -1.0e6, 1.0e6)?,
            vx: finite_f64(object, "vx", -MAX_VELOCITY, MAX_VELOCITY)?,
            vy: finite_f64(object, "vy", -MAX_VELOCITY, MAX_VELOCITY)?,
            vz: finite_f64(object, "vz", -MAX_VELOCITY, MAX_VELOCITY)?,
            turret_yaw: finite_f64(object, "turret_yaw", -1.0e6, 1.0e6)?,
            gun_pitch: finite_f64(object, "gun_pitch", -1.0e6, 1.0e6)?,
            siege_state: u8::try_from(exact_u64(object, "siege_state", 0, 3)?)
                .map_err(|_| RamError::InvalidReceipt)?,
        };
        Ok(receipt)
    }

    fn as_body(&self, player_id: u64, profile: &RamBody) -> RamBody {
        RamBody {
            key: VehicleKey {
                kind: VehicleKind::Player,
                id: player_id,
            },
            team: profile.team,
            alive: profile.alive,
            x: self.x,
            y: self.y,
            z: self.z,
            yaw: self.yaw,
            pitch: self.pitch,
            roll: self.roll,
            mass: profile.mass,
            vx: self.vx,
            vy: self.vy,
            vz: self.vz,
            turret_yaw: self.turret_yaw,
            gun_pitch: self.gun_pitch,
            siege_state: self.siege_state,
            shape: profile.shape,
        }
    }

    pub fn to_value(&self) -> Value {
        serde_json::json!({
            "seq": self.sequence,
            "bot_id": self.bot_id,
            "bot_state_revision": self.bot_state_revision,
            "presentation_time_us": self.presentation_time_us,
            "contact_x": self.contact_x,
            "contact_y": self.contact_y,
            "contact_z": self.contact_z,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "roll": self.roll,
            "vx": self.vx,
            "vy": self.vy,
            "vz": self.vz,
            "turret_yaw": self.turret_yaw,
            "gun_pitch": self.gun_pitch,
            "siege_state": self.siege_state,
        })
    }
}

/// A visible-client fact that two presented player hulls entered one contact
/// episode. The lower player id is the sole reporter; pose, velocity, mass,
/// armour, health and damage remain server/native-authority inputs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerPairRamReceipt {
    pub sequence: u64,
    pub target_player_id: u64,
    pub presentation_time_us: u64,
}

impl PlayerPairRamReceipt {
    pub fn parse_for_reporter(reporter_player_id: u64, value: &Value) -> Result<Self, RamError> {
        const FIELDS: [&str; 3] = ["seq", "target_player_id", "presentation_time_us"];
        if reporter_player_id == 0 || reporter_player_id > MAX_COMBAT_ID {
            return Err(RamError::InvalidPlayer);
        }
        let object = value.as_object().ok_or(RamError::InvalidReceipt)?;
        if object.len() != FIELDS.len()
            || object.keys().any(|field| !FIELDS.contains(&field.as_str()))
        {
            return Err(RamError::InvalidReceipt);
        }
        let receipt = Self {
            sequence: exact_u64(object, "seq", 1, MAX_RECEIPT_SEQUENCE)?,
            target_player_id: exact_u64(object, "target_player_id", 1, MAX_COMBAT_ID)?,
            presentation_time_us: exact_u64(object, "presentation_time_us", 0, MAX_EXACT_INT)?,
        };
        if reporter_player_id >= receipt.target_player_id {
            return Err(RamError::InvalidReceipt);
        }
        Ok(receipt)
    }

    pub fn to_value(self) -> Value {
        serde_json::json!({
            "seq": self.sequence,
            "target_player_id": self.target_player_id,
            "presentation_time_us": self.presentation_time_us,
        })
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PlayerRamLedgerState {
    pub admitted_sequence: u64,
    pub resolved_sequence: u64,
    pub pending: usize,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct PlayerRamProjection {
    pub admitted_sequence: u64,
    pub resolved_sequence: u64,
    pub contacts: Vec<Value>,
    pub results: Vec<Value>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReceiptAdmission {
    New {
        admitted_sequence: u64,
        count: usize,
    },
    ExactRetry {
        admitted_sequence: u64,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HistoryAdmission {
    New,
    ExactRetry,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResolutionCommit {
    New,
    ExactRetry,
}

#[derive(Clone, Debug, Error, PartialEq)]
pub enum RamError {
    #[error("ram body is invalid")]
    InvalidBody,
    #[error("ram descriptor or crew damage profile is invalid")]
    InvalidDamageProfile,
    #[error("native ram contact armor is invalid")]
    InvalidContactArmor,
    #[error("native ram contact evidence is invalid")]
    InvalidNativeEvidence,
    #[error("native ram contact evidence conflicts for {pair:?} at {source_time_us}")]
    NativeEvidenceConflict { pair: RamPair, source_time_us: u64 },
    #[error("exact ram impact inputs are invalid")]
    InvalidImpactInputs,
    #[error("exact ram damage is outside the u32 server amount range")]
    DamageOutOfRange,
    #[error("ram source cursor is invalid")]
    InvalidSourceCursor,
    #[error("ram pose frame is invalid")]
    InvalidPoseFrame,
    #[error("ram pose query is invalid")]
    InvalidPoseQuery,
    #[error("ram pose timeline regressed for {0:?}")]
    PoseTimelineRegression(VehicleKey),
    #[error("ram pose frame conflicts for {key:?} at {cursor:?}")]
    PoseFrameConflict {
        key: VehicleKey,
        cursor: RamSourceCursor,
    },
    #[error("ram pose retry is older than retained history for {key:?} at {cursor:?}")]
    StalePoseRetry {
        key: VehicleKey,
        cursor: RamSourceCursor,
    },
    #[error("ram frame contains duplicate body {0:?}")]
    DuplicateBody(VehicleKey),
    #[error("ram effect is invalid")]
    InvalidDamage,
    #[error("player ram receipt is invalid")]
    InvalidReceipt,
    #[error("player id is invalid")]
    InvalidPlayer,
    #[error("player ram receipt sequence {actual} is not contiguous after {expected_after}")]
    ReceiptSequenceGap { expected_after: u64, actual: u64 },
    #[error("player ram receipt sequence {0} conflicts with an earlier receipt")]
    ReceiptConflict(u64),
    #[error("player ram receipt sequence {0} is older than retained retry history")]
    StaleReceiptRetry(u64),
    #[error("player ram receipt timeline regressed at sequence {0}")]
    ReceiptTimelineRegression(u64),
    #[error("player ram receipt pending ledger is full")]
    PendingLimit,
    #[error("canonical bot history must advance monotonically")]
    HistoricalRevisionRegression,
    #[error("canonical bot history retry conflicts with the retained frame")]
    HistoricalFrameConflict,
    #[error("canonical history may contain only bot bodies")]
    HistoricalBodyKind,
    #[error("player ram resolution has not been prepared")]
    ResolutionNotPrepared,
    #[error("player ram resolution commit conflicts with the prepared result")]
    ResolutionConflict,
}

#[derive(Clone, Debug, Default)]
struct PlayerLedger {
    admitted_sequence: u64,
    resolved_sequence: u64,
    pending: BTreeMap<u64, PendingReceipt>,
    fingerprints: BTreeMap<u64, PlayerRamReceipt>,
    terminal: BTreeMap<u64, RamResolution>,
    rejected: BTreeSet<u64>,
}

#[derive(Clone, Debug, Default)]
struct PlayerPairLedger {
    admitted_sequence: u64,
    resolved_sequence: u64,
    pending: BTreeMap<u64, PendingPlayerPairReceipt>,
    fingerprints: BTreeMap<u64, PlayerPairRamReceipt>,
    rejected: BTreeSet<u64>,
}

#[derive(Clone, Debug)]
struct PendingPlayerPairReceipt {
    receipt: PlayerPairRamReceipt,
    prepared: Option<RamResolution>,
}

#[derive(Clone, Debug)]
struct PendingReceipt {
    receipt: PlayerRamReceipt,
    response_applied: bool,
    prepared: Option<RamResolution>,
}

#[derive(Clone, Debug, PartialEq)]
struct BotHistoryFrame {
    revision: u64,
    presentation_time_us: u64,
    bodies: BTreeMap<u64, RamBody>,
}

#[derive(Clone, Debug, PartialEq)]
enum HistoricalBodyLookup {
    Pending,
    Unavailable,
    Found(RamBody),
}

/// Apply the documented 9.22 kinetic/HE ramming law in canonical pair order.
///
/// Masses are kilograms at the API boundary and are converted to tonnes once.
/// There is deliberately no safe-speed threshold, mass-ratio normalization or
/// HP cap. Armour is accepted only through typed per-contact native evidence.
pub fn exact_ram_damage(
    closing_speed: f64,
    first_mass_kg: f64,
    second_mass_kg: f64,
    first_evidence: RamVehicleContactEvidence,
    second_evidence: RamVehicleContactEvidence,
    first_moving: bool,
    second_moving: bool,
) -> Result<ExactRamDamage, RamError> {
    if !closing_speed.is_finite()
        || closing_speed < 0.0
        || !first_mass_kg.is_finite()
        || first_mass_kg <= 0.0
        || !second_mass_kg.is_finite()
        || second_mass_kg <= 0.0
    {
        return Err(RamError::InvalidImpactInputs);
    }
    let first_tonnes = first_mass_kg * RAM_WEIGHT_SCALE;
    let second_tonnes = second_mass_kg * RAM_WEIGHT_SCALE;
    let combined_tonnes = first_tonnes + second_tonnes;
    let potential = RAM_KINETIC_FACTOR * combined_tonnes * closing_speed * closing_speed;
    if !potential.is_finite() {
        return Err(RamError::DamageOutOfRange);
    }

    let first_share = potential * (second_tonnes / combined_tonnes);
    let second_share = potential * (first_tonnes / combined_tonnes);
    let mut first_received = (RAM_HE_DAMAGE_FACTOR * first_share
        - RAM_ARMOR_ABSORPTION_FACTOR
            * first_evidence.armor.millimeters
            * first_evidence.profile.spall_coefficient)
        .max(0.0);
    let mut second_received = (RAM_HE_DAMAGE_FACTOR * second_share
        - RAM_ARMOR_ABSORPTION_FACTOR
            * second_evidence.armor.millimeters
            * second_evidence.profile.spall_coefficient)
        .max(0.0);

    // Controlled Impact changes only final received/inflicted damage and only
    // while that skill owner's vehicle is moving.
    if first_moving {
        first_received *= 1.0 - first_evidence.profile.controlled_impact_bonus;
        second_received *= 1.0 + first_evidence.profile.controlled_impact_bonus;
    }
    if second_moving {
        second_received *= 1.0 - second_evidence.profile.controlled_impact_bonus;
        first_received *= 1.0 + second_evidence.profile.controlled_impact_bonus;
    }

    Ok(ExactRamDamage {
        first: exact_damage_amount(first_received * RAM_DAMAGE_COEFFICIENT)?,
        second: exact_damage_amount(second_received * RAM_DAMAGE_COEFFICIENT)?,
    })
}

#[derive(Clone, Debug, Default)]
pub struct RamAuthority {
    active_contacts: BTreeSet<RamPair>,
    players: BTreeMap<u64, PlayerLedger>,
    player_pairs: BTreeMap<u64, PlayerPairLedger>,
    bot_history: BTreeMap<u64, BotHistoryFrame>,
    bot_history_order: VecDeque<u64>,
}

impl RamAuthority {
    pub fn new() -> Self {
        Self::default()
    }

    /// Resolve every canonical body pair exactly once for one fixed tick.
    pub fn resolve_frame(
        &mut self,
        time_us: u64,
        bodies: &[RamBody],
    ) -> Result<RamFrameResolution, RamError> {
        self.resolve_frame_with_native_evidence(time_us, bodies, &[])
    }

    /// Resolve collision response and exact damage when both contact armour
    /// values are backed by immutable native evidence for this source time.
    /// Missing evidence is a normal fail-closed contact with no HP mutation.
    pub fn resolve_frame_with_native_evidence(
        &mut self,
        time_us: u64,
        bodies: &[RamBody],
        native_evidence: &[NativeRamContactEvidence],
    ) -> Result<RamFrameResolution, RamError> {
        if time_us > MAX_EXACT_INT
            || native_evidence
                .iter()
                .any(|evidence| evidence.source_time_us != time_us)
        {
            return Err(RamError::InvalidNativeEvidence);
        }
        let native_evidence = canonical_native_evidence(native_evidence)?;
        let bodies = canonical_bodies(bodies, None)?;
        let ordered = bodies.values().collect::<Vec<_>>();
        let previous_contacts = self.active_contacts.clone();
        let mut overlap_pairs = BTreeSet::new();
        let mut damaging_pairs = BTreeSet::new();
        let mut contacts = Vec::new();

        for first_index in 0..ordered.len() {
            for second_index in (first_index + 1)..ordered.len() {
                let first = ordered[first_index];
                let second = ordered[second_index];
                let Some(contact) = pair_contact(first, second) else {
                    continue;
                };
                let pair = RamPair::new(first.key, second.key)?;
                overlap_pairs.insert(pair);
                let impact_contact = pair_impact_contact(first, second);
                let pair_result = pair_result(
                    first,
                    second,
                    contact,
                    impact_contact,
                    native_evidence.values().find(|evidence| {
                        evidence.pair == pair && evidence.source_time_us == time_us
                    }),
                )?;
                let mut outcome = RamResolutionOutcome::Contact;
                let mut damage = None;
                if let Some((damage_first, damage_second)) = pair_result.damage {
                    damaging_pairs.insert(pair);
                    if previous_contacts.contains(&pair) {
                        outcome = RamResolutionOutcome::ActiveContact;
                    } else {
                        damage = Some(atomic_damage(
                            pair,
                            damage_first,
                            damage_second,
                            format!(
                                "ram:tick:{time_us}:{}:{}:{}:{}",
                                kind_token(pair.first.kind),
                                pair.first.id,
                                kind_token(pair.second.kind),
                                pair.second.id
                            ),
                        )?);
                        outcome = RamResolutionOutcome::Damage;
                    }
                }
                contacts.push(RamResolution {
                    source: RamResolutionSource::FixedTick { time_us },
                    pair,
                    first_delta: pair_result.first_delta,
                    second_delta: pair_result.second_delta,
                    damage,
                    outcome,
                });
            }
        }

        self.active_contacts = previous_contacts
            .intersection(&overlap_pairs)
            .copied()
            .chain(damaging_pairs)
            .collect();
        Ok(RamFrameResolution { contacts })
    }

    /// Retain one exact canonical bot publication for delayed player proofs.
    pub fn record_bot_frame(
        &mut self,
        revision: u64,
        presentation_time_us: u64,
        bodies: &[RamBody],
    ) -> Result<HistoryAdmission, RamError> {
        if revision > MAX_RECEIPT_SEQUENCE || presentation_time_us > MAX_EXACT_INT {
            return Err(RamError::HistoricalRevisionRegression);
        }
        let canonical = canonical_bodies(bodies, Some(VehicleKind::Bot))?;
        let frame = BotHistoryFrame {
            revision,
            presentation_time_us,
            bodies: canonical
                .into_iter()
                .map(|(key, body)| (key.id, body))
                .collect(),
        };
        if let Some(previous) = self.bot_history.get(&revision) {
            return if previous == &frame {
                Ok(HistoryAdmission::ExactRetry)
            } else {
                Err(RamError::HistoricalFrameConflict)
            };
        }
        if let Some(last_revision) = self.bot_history_order.back().copied() {
            let last = self
                .bot_history
                .get(&last_revision)
                .expect("history order only contains retained frames");
            if revision <= last_revision || presentation_time_us < last.presentation_time_us {
                return Err(RamError::HistoricalRevisionRegression);
            }
        }
        self.bot_history.insert(revision, frame);
        self.bot_history_order.push_back(revision);
        while self.bot_history_order.len() > MAX_BOT_HISTORY_FRAMES {
            if let Some(oldest) = self.bot_history_order.pop_front() {
                self.bot_history.remove(&oldest);
            }
        }
        Ok(HistoryAdmission::New)
    }

    /// Atomically admit a contiguous batch. Exact repeats are successes;
    /// reusing a sequence for different immutable data is a conflict.
    pub fn admit_player_receipts(
        &mut self,
        player_id: u64,
        values: &[Value],
    ) -> Result<ReceiptAdmission, RamError> {
        let (proposed, admission) = self.proposed_player_receipts(player_id, values)?;
        self.players.insert(player_id, proposed);
        Ok(admission)
    }

    /// Terminalize one identifiable but permanently invalid receipt without
    /// letting it block later player input or later contiguous contacts.
    pub fn reject_player_receipt(
        &mut self,
        player_id: u64,
        sequence: u64,
    ) -> Result<ReceiptAdmission, RamError> {
        if player_id == 0 || player_id > MAX_COMBAT_ID {
            return Err(RamError::InvalidPlayer);
        }
        if sequence == 0 || sequence > MAX_RECEIPT_SEQUENCE {
            return Err(RamError::InvalidReceipt);
        }
        let mut proposed = self.players.get(&player_id).cloned().unwrap_or_default();
        if sequence <= proposed.admitted_sequence {
            return Ok(ReceiptAdmission::ExactRetry {
                admitted_sequence: proposed.admitted_sequence,
            });
        }
        if sequence != proposed.admitted_sequence.saturating_add(1) {
            return Err(RamError::ReceiptSequenceGap {
                expected_after: proposed.admitted_sequence,
                actual: sequence,
            });
        }
        proposed.admitted_sequence = sequence;
        proposed.rejected.insert(sequence);
        advance_player_terminal_prefix(&mut proposed);
        trim_receipt_history(&mut proposed);
        self.players.insert(player_id, proposed);
        Ok(ReceiptAdmission::New {
            admitted_sequence: sequence,
            count: 1,
        })
    }

    /// Validate an input batch without changing the admitted high-water.
    /// The battle loop uses this before committing the enclosing input.
    pub fn validate_player_receipts(
        &self,
        player_id: u64,
        values: &[Value],
    ) -> Result<ReceiptAdmission, RamError> {
        self.proposed_player_receipts(player_id, values)
            .map(|(_, admission)| admission)
    }

    fn proposed_player_receipts(
        &self,
        player_id: u64,
        values: &[Value],
    ) -> Result<(PlayerLedger, ReceiptAdmission), RamError> {
        if player_id == 0 || player_id > MAX_COMBAT_ID {
            return Err(RamError::InvalidPlayer);
        }
        let mut batch = BTreeMap::<u64, PlayerRamReceipt>::new();
        for value in values {
            let receipt = PlayerRamReceipt::parse(value)?;
            match batch.get(&receipt.sequence) {
                Some(previous) if previous != &receipt => {
                    return Err(RamError::ReceiptConflict(receipt.sequence));
                }
                Some(_) => {}
                None => {
                    batch.insert(receipt.sequence, receipt);
                }
            }
        }
        let mut proposed = self.players.get(&player_id).cloned().unwrap_or_default();
        let mut new_count = 0;
        for (sequence, receipt) in batch {
            if sequence <= proposed.admitted_sequence {
                match proposed.fingerprints.get(&sequence) {
                    Some(previous) if previous == &receipt => continue,
                    Some(_) => return Err(RamError::ReceiptConflict(sequence)),
                    None => return Err(RamError::StaleReceiptRetry(sequence)),
                }
            }
            if sequence != proposed.admitted_sequence + 1 {
                return Err(RamError::ReceiptSequenceGap {
                    expected_after: proposed.admitted_sequence,
                    actual: sequence,
                });
            }
            if proposed.pending.len() >= MAX_PENDING_RAM_CONTACTS {
                return Err(RamError::PendingLimit);
            }
            if let Some((_, previous)) = proposed.fingerprints.iter().next_back() {
                if receipt.bot_state_revision < previous.bot_state_revision
                    || receipt.presentation_time_us < previous.presentation_time_us
                {
                    return Err(RamError::ReceiptTimelineRegression(sequence));
                }
            }
            proposed.pending.insert(
                sequence,
                PendingReceipt {
                    receipt: receipt.clone(),
                    response_applied: false,
                    prepared: None,
                },
            );
            proposed.fingerprints.insert(sequence, receipt);
            proposed.admitted_sequence = sequence;
            new_count += 1;
        }
        trim_receipt_history(&mut proposed);
        let admitted_sequence = proposed.admitted_sequence;
        let admission = if new_count == 0 {
            ReceiptAdmission::ExactRetry { admitted_sequence }
        } else {
            ReceiptAdmission::New {
                admitted_sequence,
                count: new_count,
            }
        };
        Ok((proposed, admission))
    }

    /// Prepare every independently decidable receipt, then return only the
    /// contiguous prepared prefix which may advance the ordered high-water.
    pub fn prepare_player_receipts(
        &mut self,
        current_bodies: &[RamBody],
    ) -> Result<Vec<RamResolution>, RamError> {
        self.prepare_player_receipts_with_native_evidence(current_bodies, &[])
    }

    /// Prepare delayed player contacts using only evidence whose canonical
    /// pair and source timestamp exactly match the immutable receipt.
    pub fn prepare_player_receipts_with_native_evidence(
        &mut self,
        current_bodies: &[RamBody],
        native_evidence: &[NativeRamContactEvidence],
    ) -> Result<Vec<RamResolution>, RamError> {
        let native_evidence = canonical_native_evidence(native_evidence)?;
        let current = canonical_bodies(current_bodies, None)?;
        let player_ids = self.players.keys().copied().collect::<Vec<_>>();
        let mut resolutions = Vec::new();
        for player_id in player_ids {
            let sequences = self
                .players
                .get(&player_id)
                .expect("player id came from the ledger map")
                .pending
                .keys()
                .copied()
                .collect::<Vec<_>>();
            for sequence in sequences {
                self.prepare_player_sequence(player_id, sequence, &current, &native_evidence)?;
            }
            let ledger = self
                .players
                .get(&player_id)
                .expect("player id came from the ledger map");
            for pending in ledger.pending.values() {
                let Some(prepared) = pending.prepared.clone() else {
                    break;
                };
                resolutions.push(prepared);
            }
        }
        Ok(resolutions)
    }

    pub fn commit_player_resolution(
        &mut self,
        resolution: &RamResolution,
    ) -> Result<ResolutionCommit, RamError> {
        self.commit_player_resolutions(std::slice::from_ref(resolution))
            .map(|mut commits| commits.remove(0))
    }

    /// Validate a prepared receipt batch without advancing resolved high-water.
    pub fn validate_player_resolutions(
        &self,
        resolutions: &[RamResolution],
    ) -> Result<Vec<ResolutionCommit>, RamError> {
        self.proposed_player_resolution_commits(resolutions)
            .map(|(_, commits)| commits)
    }

    /// Atomically advance every included player's resolved high-water.
    pub fn commit_player_resolutions(
        &mut self,
        resolutions: &[RamResolution],
    ) -> Result<Vec<ResolutionCommit>, RamError> {
        let (players, commits) = self.proposed_player_resolution_commits(resolutions)?;
        for (player_id, ledger) in players {
            self.players.insert(player_id, ledger);
        }
        Ok(commits)
    }

    fn proposed_player_resolution_commits(
        &self,
        resolutions: &[RamResolution],
    ) -> Result<(BTreeMap<u64, PlayerLedger>, Vec<ResolutionCommit>), RamError> {
        let mut players = BTreeMap::<u64, PlayerLedger>::new();
        let mut commits = Vec::with_capacity(resolutions.len());
        for resolution in resolutions {
            let RamResolutionSource::PlayerReceipt { player_id, .. } = resolution.source else {
                return Err(RamError::ResolutionConflict);
            };
            if !players.contains_key(&player_id) {
                players.insert(
                    player_id,
                    self.players
                        .get(&player_id)
                        .cloned()
                        .ok_or(RamError::ResolutionNotPrepared)?,
                );
            }
            let ledger = players
                .get_mut(&player_id)
                .expect("the prepared player ledger was inserted above");
            commits.push(commit_player_resolution_to_ledger(ledger, resolution)?);
        }
        Ok((players, commits))
    }

    pub fn player_ledger_state(&self, player_id: u64) -> PlayerRamLedgerState {
        self.players
            .get(&player_id)
            .map(|ledger| PlayerRamLedgerState {
                admitted_sequence: ledger.admitted_sequence,
                resolved_sequence: ledger.resolved_sequence,
                pending: ledger.pending.len(),
            })
            .unwrap_or_default()
    }

    pub fn player_projection(&self, player_id: u64) -> PlayerRamProjection {
        self.players
            .get(&player_id)
            .map_or_else(PlayerRamProjection::default, |ledger| {
                let mut results = ledger
                    .terminal
                    .iter()
                    .map(|(&sequence, resolution)| {
                        (
                            sequence,
                            serde_json::json!({
                                "seq": sequence,
                                "outcome": player_outcome_token(resolution.outcome),
                            }),
                        )
                    })
                    .collect::<BTreeMap<_, _>>();
                for &sequence in &ledger.rejected {
                    results.insert(
                        sequence,
                        serde_json::json!({
                            "seq": sequence,
                            "outcome": "unavailable",
                        }),
                    );
                }
                PlayerRamProjection {
                    admitted_sequence: ledger.admitted_sequence,
                    resolved_sequence: ledger.resolved_sequence,
                    contacts: ledger
                        .pending
                        .values()
                        .map(|pending| pending.receipt.to_value())
                        .collect(),
                    results: results.into_values().collect(),
                }
            })
    }

    pub fn validate_player_pair_receipts(
        &self,
        reporter_player_id: u64,
        values: &[Value],
    ) -> Result<ReceiptAdmission, RamError> {
        self.proposed_player_pair_receipts(reporter_player_id, values)
            .map(|(_, admission)| admission)
    }

    pub fn admit_player_pair_receipts(
        &mut self,
        reporter_player_id: u64,
        values: &[Value],
    ) -> Result<ReceiptAdmission, RamError> {
        let (proposed, admission) =
            self.proposed_player_pair_receipts(reporter_player_id, values)?;
        self.player_pairs.insert(reporter_player_id, proposed);
        Ok(admission)
    }

    /// Terminalize one identifiable but permanently invalid player-pair
    /// receipt. Pair receipts expose only their admitted/resolved high-water.
    pub fn reject_player_pair_receipt(
        &mut self,
        reporter_player_id: u64,
        sequence: u64,
    ) -> Result<ReceiptAdmission, RamError> {
        if reporter_player_id == 0 || reporter_player_id > MAX_COMBAT_ID {
            return Err(RamError::InvalidPlayer);
        }
        if sequence == 0 || sequence > MAX_RECEIPT_SEQUENCE {
            return Err(RamError::InvalidReceipt);
        }
        let mut proposed = self
            .player_pairs
            .get(&reporter_player_id)
            .cloned()
            .unwrap_or_default();
        if sequence <= proposed.admitted_sequence {
            return Ok(ReceiptAdmission::ExactRetry {
                admitted_sequence: proposed.admitted_sequence,
            });
        }
        if sequence != proposed.admitted_sequence.saturating_add(1) {
            return Err(RamError::ReceiptSequenceGap {
                expected_after: proposed.admitted_sequence,
                actual: sequence,
            });
        }
        proposed.admitted_sequence = sequence;
        proposed.rejected.insert(sequence);
        advance_player_pair_terminal_prefix(&mut proposed);
        trim_player_pair_history(&mut proposed);
        self.player_pairs.insert(reporter_player_id, proposed);
        Ok(ReceiptAdmission::New {
            admitted_sequence: sequence,
            count: 1,
        })
    }

    fn proposed_player_pair_receipts(
        &self,
        reporter_player_id: u64,
        values: &[Value],
    ) -> Result<(PlayerPairLedger, ReceiptAdmission), RamError> {
        if values.len() > MAX_PENDING_RAM_CONTACTS {
            return Err(RamError::PendingLimit);
        }
        let mut batch = BTreeMap::<u64, PlayerPairRamReceipt>::new();
        for value in values {
            let receipt = PlayerPairRamReceipt::parse_for_reporter(reporter_player_id, value)?;
            match batch.get(&receipt.sequence) {
                Some(previous) if previous != &receipt => {
                    return Err(RamError::ReceiptConflict(receipt.sequence));
                }
                Some(_) => {}
                None => {
                    batch.insert(receipt.sequence, receipt);
                }
            }
        }
        let mut proposed = self
            .player_pairs
            .get(&reporter_player_id)
            .cloned()
            .unwrap_or_default();
        let mut new_count = 0;
        for (sequence, receipt) in batch {
            if sequence <= proposed.admitted_sequence {
                match proposed.fingerprints.get(&sequence) {
                    Some(previous) if previous == &receipt => continue,
                    Some(_) => return Err(RamError::ReceiptConflict(sequence)),
                    None => return Err(RamError::StaleReceiptRetry(sequence)),
                }
            }
            if sequence != proposed.admitted_sequence + 1 {
                return Err(RamError::ReceiptSequenceGap {
                    expected_after: proposed.admitted_sequence,
                    actual: sequence,
                });
            }
            if proposed.pending.len() >= MAX_PENDING_RAM_CONTACTS {
                return Err(RamError::PendingLimit);
            }
            if let Some((_, previous)) = proposed.fingerprints.iter().next_back() {
                if receipt.presentation_time_us < previous.presentation_time_us {
                    return Err(RamError::ReceiptTimelineRegression(sequence));
                }
            }
            proposed.pending.insert(
                sequence,
                PendingPlayerPairReceipt {
                    receipt,
                    prepared: None,
                },
            );
            proposed.fingerprints.insert(sequence, receipt);
            proposed.admitted_sequence = sequence;
            new_count += 1;
        }
        trim_player_pair_history(&mut proposed);
        let admitted_sequence = proposed.admitted_sequence;
        let admission = if new_count == 0 {
            ReceiptAdmission::ExactRetry { admitted_sequence }
        } else {
            ReceiptAdmission::New {
                admitted_sequence,
                count: new_count,
            }
        };
        Ok((proposed, admission))
    }

    pub fn player_pair_ledger_state(&self, reporter_player_id: u64) -> PlayerRamLedgerState {
        self.player_pairs
            .get(&reporter_player_id)
            .map(|ledger| PlayerRamLedgerState {
                admitted_sequence: ledger.admitted_sequence,
                resolved_sequence: ledger.resolved_sequence,
                pending: ledger.pending.len(),
            })
            .unwrap_or_default()
    }

    /// Close player/player receipts whose historical pose bracket was broken
    /// by one participant's live clock discontinuity. A probe already frozen
    /// for the native oracle remains valid and is explicitly preserved.
    pub fn invalidate_unfrozen_player_pair_history(
        &mut self,
        player_id: u64,
        frozen_receipts: &BTreeSet<(u64, u64)>,
    ) -> Result<Vec<RamResolution>, RamError> {
        if player_id == 0 || player_id > MAX_COMBAT_ID {
            return Err(RamError::InvalidPlayer);
        }
        let reporter_ids = self.player_pairs.keys().copied().collect::<Vec<_>>();
        for reporter_player_id in reporter_ids {
            let sequences = self
                .player_pairs
                .get(&reporter_player_id)
                .expect("reporter came from the player-pair ledger")
                .pending
                .iter()
                .filter_map(|(&sequence, pending)| {
                    let involves_player = reporter_player_id == player_id
                        || pending.receipt.target_player_id == player_id;
                    (involves_player
                        && pending.prepared.is_none()
                        && !frozen_receipts.contains(&(reporter_player_id, sequence)))
                    .then_some(sequence)
                })
                .collect::<Vec<_>>();
            for sequence in sequences {
                let receipt = self
                    .player_pairs
                    .get(&reporter_player_id)
                    .and_then(|ledger| ledger.pending.get(&sequence))
                    .expect("selected player-pair receipt remains pending")
                    .receipt;
                let pair = RamPair::new(
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: reporter_player_id,
                    },
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: receipt.target_player_id,
                    },
                )?;
                self.player_pairs
                    .get_mut(&reporter_player_id)
                    .and_then(|ledger| ledger.pending.get_mut(&sequence))
                    .expect("selected player-pair receipt remains pending")
                    .prepared = Some(RamResolution {
                    source: RamResolutionSource::PlayerPairReceipt {
                        reporter_player_id,
                        sequence,
                        target_player_id: receipt.target_player_id,
                        presentation_time_us: receipt.presentation_time_us,
                    },
                    pair,
                    first_delta: RamBodyDelta::zero(pair.first),
                    second_delta: RamBodyDelta::zero(pair.second),
                    damage: None,
                    outcome: RamResolutionOutcome::HistoricalStateUnavailable,
                });
            }
        }
        self.commit_ready_player_pair_resolutions()
    }

    /// Freeze every independently reconstructable player/player receipt.
    /// Presentation collision response remains local; these probes own only
    /// the delayed native armour evidence and canonical HP transaction.
    pub fn prepare_player_pair_contact_probes(
        &mut self,
        timeline: &RamPoseTimeline,
    ) -> Result<Vec<RamContactProbe>, RamError> {
        let reporters = self.player_pairs.keys().copied().collect::<Vec<_>>();
        let mut probes = Vec::new();
        for reporter_player_id in reporters {
            let sequences = self
                .player_pairs
                .get(&reporter_player_id)
                .expect("reporter came from the player-pair ledger")
                .pending
                .keys()
                .copied()
                .collect::<Vec<_>>();
            for sequence in sequences {
                let pending = self
                    .player_pairs
                    .get(&reporter_player_id)
                    .and_then(|ledger| ledger.pending.get(&sequence))
                    .expect("sequence came from the player-pair ledger");
                if pending.prepared.is_some() {
                    continue;
                }
                let receipt = pending.receipt;
                let reporter_key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: reporter_player_id,
                };
                let target_key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: receipt.target_player_id,
                };
                let pair = RamPair::new(reporter_key, target_key)?;
                let source = RamResolutionSource::PlayerPairReceipt {
                    reporter_player_id,
                    sequence,
                    target_player_id: receipt.target_player_id,
                    presentation_time_us: receipt.presentation_time_us,
                };
                let unavailable = |outcome| RamResolution {
                    source,
                    pair,
                    first_delta: RamBodyDelta::zero(pair.first),
                    second_delta: RamBodyDelta::zero(pair.second),
                    damage: None,
                    outcome,
                };
                let (first, second) = match timeline.pair_at_source_time(
                    reporter_key,
                    target_key,
                    receipt.presentation_time_us,
                )? {
                    RamPosePairLookup::Pending => continue,
                    RamPosePairLookup::Unavailable => {
                        self.store_player_pair_prepared(
                            reporter_player_id,
                            sequence,
                            unavailable(RamResolutionOutcome::HistoricalStateUnavailable),
                        )?;
                        continue;
                    }
                    RamPosePairLookup::Found {
                        pair: found_pair,
                        first,
                        second,
                    } => {
                        if found_pair != pair {
                            return Err(RamError::ResolutionConflict);
                        }
                        (first, second)
                    }
                };
                if !first.alive || !second.alive {
                    self.store_player_pair_prepared(
                        reporter_player_id,
                        sequence,
                        unavailable(RamResolutionOutcome::VehicleUnavailable),
                    )?;
                    continue;
                }
                if pair_contact(&first, &second).is_none() {
                    self.store_player_pair_prepared(
                        reporter_player_id,
                        sequence,
                        unavailable(RamResolutionOutcome::NoContact),
                    )?;
                    continue;
                }
                let Some(impact_contact) = pair_impact_contact(&first, &second) else {
                    self.store_player_pair_prepared(
                        reporter_player_id,
                        sequence,
                        unavailable(RamResolutionOutcome::Contact),
                    )?;
                    continue;
                };
                if !contact_needs_native(&first, &second, impact_contact) {
                    self.store_player_pair_prepared(
                        reporter_player_id,
                        sequence,
                        unavailable(RamResolutionOutcome::Contact),
                    )?;
                    continue;
                }
                let cursor = RamSourceCursor::new(sequence, 0)?;
                match contact_probe(
                    source,
                    cursor,
                    receipt.presentation_time_us,
                    &first,
                    &second,
                    impact_contact,
                    None,
                    (0.0, 0.0),
                )? {
                    Some(probe) => probes.push(probe),
                    None => self.store_player_pair_prepared(
                        reporter_player_id,
                        sequence,
                        unavailable(RamResolutionOutcome::NoContact),
                    )?,
                }
            }
        }
        Ok(probes)
    }

    pub fn resolve_player_pair_contact_probe(
        &mut self,
        probe: &RamContactProbe,
        evidence: NativeRamContactEvidence,
    ) -> Result<RamResolution, RamError> {
        let RamResolutionSource::PlayerPairReceipt {
            reporter_player_id,
            sequence,
            target_player_id,
            presentation_time_us,
        } = probe.source
        else {
            return Err(RamError::ResolutionConflict);
        };
        self.validate_player_pair_probe(
            probe,
            reporter_player_id,
            sequence,
            target_player_id,
            presentation_time_us,
        )?;
        if evidence.pair != probe.pair
            || evidence.cursor != probe.cursor
            || evidence.source_time_us != probe.source_time_us
            || evidence.first_moving != probe.first_moving
            || evidence.second_moving != probe.second_moving
        {
            return Err(RamError::InvalidNativeEvidence);
        }
        let Some(response_contact) = pair_contact(&probe.first, &probe.second) else {
            return Err(RamError::InvalidNativeEvidence);
        };
        let Some(impact_contact) = pair_impact_contact(&probe.first, &probe.second) else {
            return Err(RamError::InvalidNativeEvidence);
        };
        if !probe_matches_contact(probe, impact_contact) {
            return Err(RamError::InvalidNativeEvidence);
        }
        let pair_result = pair_result(
            &probe.first,
            &probe.second,
            response_contact,
            Some(impact_contact),
            Some(&evidence),
        )?;
        let (damage, outcome) = match pair_result.damage {
            None => (None, RamResolutionOutcome::Contact),
            Some((damage_first, damage_second)) => (
                Some(atomic_damage(
                    probe.pair,
                    damage_first,
                    damage_second,
                    format!("ram:player-pair:{reporter_player_id}:{sequence}:{target_player_id}"),
                )?),
                RamResolutionOutcome::Damage,
            ),
        };
        let resolution = RamResolution {
            source: probe.source,
            pair: probe.pair,
            first_delta: RamBodyDelta::zero(probe.pair.first),
            second_delta: RamBodyDelta::zero(probe.pair.second),
            damage,
            outcome,
        };
        self.store_player_pair_prepared(reporter_player_id, sequence, resolution.clone())?;
        Ok(resolution)
    }

    pub fn prepare_player_pair_probe_unavailable(
        &mut self,
        probe: &RamContactProbe,
    ) -> Result<RamResolution, RamError> {
        let RamResolutionSource::PlayerPairReceipt {
            reporter_player_id,
            sequence,
            target_player_id,
            presentation_time_us,
        } = probe.source
        else {
            return Err(RamError::ResolutionConflict);
        };
        self.validate_player_pair_probe(
            probe,
            reporter_player_id,
            sequence,
            target_player_id,
            presentation_time_us,
        )?;
        let resolution = RamResolution {
            source: probe.source,
            pair: probe.pair,
            first_delta: RamBodyDelta::zero(probe.pair.first),
            second_delta: RamBodyDelta::zero(probe.pair.second),
            damage: None,
            outcome: RamResolutionOutcome::NativeEvidenceUnavailable,
        };
        self.store_player_pair_prepared(reporter_player_id, sequence, resolution.clone())?;
        Ok(resolution)
    }

    pub fn commit_ready_player_pair_resolutions(&mut self) -> Result<Vec<RamResolution>, RamError> {
        let reporters = self.player_pairs.keys().copied().collect::<Vec<_>>();
        let mut committed = Vec::new();
        for reporter_player_id in reporters {
            loop {
                let ledger = self
                    .player_pairs
                    .get_mut(&reporter_player_id)
                    .expect("reporter came from the player-pair ledger");
                advance_player_pair_terminal_prefix(ledger);
                let sequence = ledger.resolved_sequence.saturating_add(1);
                let Some(resolution) = ledger
                    .pending
                    .get(&sequence)
                    .and_then(|pending| pending.prepared.clone())
                else {
                    break;
                };
                ledger.pending.remove(&sequence);
                ledger.resolved_sequence = sequence;
                advance_player_pair_terminal_prefix(ledger);
                trim_player_pair_history(ledger);
                committed.push(resolution);
            }
        }
        Ok(committed)
    }

    /// Close every admitted contact without HP mutation after the battle has
    /// already committed a terminal result. This keeps both receipt ledgers
    /// observable and contiguous without reviving combat after finish.
    pub fn finish_pending_receipts(&mut self) -> Result<(), RamError> {
        let player_ids = self.players.keys().copied().collect::<Vec<_>>();
        let mut player_resolutions = Vec::new();
        for player_id in player_ids {
            let sequences = self
                .players
                .get(&player_id)
                .expect("player came from the receipt ledger")
                .pending
                .keys()
                .copied()
                .collect::<Vec<_>>();
            for sequence in sequences {
                let receipt = self
                    .players
                    .get(&player_id)
                    .and_then(|ledger| ledger.pending.get(&sequence))
                    .expect("sequence came from the receipt ledger")
                    .receipt
                    .clone();
                let pair = RamPair::new(
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: player_id,
                    },
                    VehicleKey {
                        kind: VehicleKind::Bot,
                        id: receipt.bot_id,
                    },
                )?;
                let resolution = RamResolution {
                    source: RamResolutionSource::PlayerReceipt {
                        player_id,
                        sequence,
                        bot_state_revision: receipt.bot_state_revision,
                        presentation_time_us: receipt.presentation_time_us,
                    },
                    pair,
                    first_delta: RamBodyDelta::zero(pair.first),
                    second_delta: RamBodyDelta::zero(pair.second),
                    damage: None,
                    outcome: RamResolutionOutcome::VehicleUnavailable,
                };
                self.players
                    .get_mut(&player_id)
                    .and_then(|ledger| ledger.pending.get_mut(&sequence))
                    .expect("validated pending receipt remains present")
                    .prepared = Some(resolution.clone());
                player_resolutions.push(resolution);
            }
        }
        self.commit_player_resolutions(&player_resolutions)?;

        let reporter_ids = self.player_pairs.keys().copied().collect::<Vec<_>>();
        for reporter_player_id in reporter_ids {
            let sequences = self
                .player_pairs
                .get(&reporter_player_id)
                .expect("reporter came from the player-pair ledger")
                .pending
                .keys()
                .copied()
                .collect::<Vec<_>>();
            for sequence in sequences {
                let receipt = self
                    .player_pairs
                    .get(&reporter_player_id)
                    .and_then(|ledger| ledger.pending.get(&sequence))
                    .expect("sequence came from the player-pair ledger")
                    .receipt;
                let pair = RamPair::new(
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: reporter_player_id,
                    },
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: receipt.target_player_id,
                    },
                )?;
                let resolution = RamResolution {
                    source: RamResolutionSource::PlayerPairReceipt {
                        reporter_player_id,
                        sequence,
                        target_player_id: receipt.target_player_id,
                        presentation_time_us: receipt.presentation_time_us,
                    },
                    pair,
                    first_delta: RamBodyDelta::zero(pair.first),
                    second_delta: RamBodyDelta::zero(pair.second),
                    damage: None,
                    outcome: RamResolutionOutcome::VehicleUnavailable,
                };
                self.player_pairs
                    .get_mut(&reporter_player_id)
                    .and_then(|ledger| ledger.pending.get_mut(&sequence))
                    .expect("validated pending player-pair receipt remains present")
                    .prepared = Some(resolution);
            }
        }
        self.commit_ready_player_pair_resolutions()?;
        Ok(())
    }

    fn validate_player_pair_probe(
        &self,
        probe: &RamContactProbe,
        reporter_player_id: u64,
        sequence: u64,
        target_player_id: u64,
        presentation_time_us: u64,
    ) -> Result<(), RamError> {
        let pending = self
            .player_pairs
            .get(&reporter_player_id)
            .and_then(|ledger| ledger.pending.get(&sequence))
            .filter(|pending| {
                pending.prepared.is_none()
                    && pending.receipt.target_player_id == target_player_id
                    && pending.receipt.presentation_time_us == presentation_time_us
            })
            .ok_or(RamError::ResolutionNotPrepared)?;
        let expected_pair = RamPair::new(
            VehicleKey {
                kind: VehicleKind::Player,
                id: reporter_player_id,
            },
            VehicleKey {
                kind: VehicleKind::Player,
                id: pending.receipt.target_player_id,
            },
        )?;
        if probe.pair != expected_pair
            || probe.cursor != RamSourceCursor::new(sequence, 0)?
            || probe.source_time_us != presentation_time_us
        {
            return Err(RamError::ResolutionConflict);
        }
        Ok(())
    }

    fn store_player_pair_prepared(
        &mut self,
        reporter_player_id: u64,
        sequence: u64,
        resolution: RamResolution,
    ) -> Result<(), RamError> {
        let pending = self
            .player_pairs
            .get_mut(&reporter_player_id)
            .and_then(|ledger| ledger.pending.get_mut(&sequence))
            .ok_or(RamError::ResolutionNotPrepared)?;
        if let Some(previous) = &pending.prepared {
            return if previous == &resolution {
                Ok(())
            } else {
                Err(RamError::ResolutionConflict)
            };
        }
        pending.prepared = Some(resolution);
        Ok(())
    }

    pub fn active_contacts(&self) -> &BTreeSet<RamPair> {
        &self.active_contacts
    }

    /// Freeze every closing contact in one canonical frame before asking the
    /// hidden native oracle for armour. Collision response may be committed
    /// immediately; the returned probes own only the delayed HP transaction.
    pub fn fixed_contact_probes(
        &self,
        source_time_us: u64,
        cursor: RamSourceCursor,
        bodies: &[RamBody],
    ) -> Result<Vec<RamContactProbe>, RamError> {
        if source_time_us > MAX_EXACT_INT {
            return Err(RamError::InvalidPoseFrame);
        }
        let bodies = canonical_bodies(bodies, None)?;
        let ordered = bodies.values().collect::<Vec<_>>();
        let mut probes = Vec::new();
        for first_index in 0..ordered.len() {
            for second_index in (first_index + 1)..ordered.len() {
                let first = ordered[first_index];
                let second = ordered[second_index];
                if pair_contact(first, second).is_none() {
                    continue;
                }
                let Some(impact_contact) = pair_impact_contact(first, second) else {
                    continue;
                };
                if !contact_needs_native(first, second, impact_contact) {
                    continue;
                }
                if let Some(probe) = contact_probe(
                    RamResolutionSource::FixedTick {
                        time_us: source_time_us,
                    },
                    cursor,
                    source_time_us,
                    first,
                    second,
                    impact_contact,
                    None,
                    (0.0, 0.0),
                )? {
                    probes.push(probe);
                }
            }
        }
        Ok(probes)
    }

    /// Return every immutable native probe whose collision-response half has
    /// not yet committed. HP results remain ordered by the receipt ledger, but
    /// one earlier native query must not delay a later Bot's source response.
    pub fn player_contact_probes(
        &self,
        current_bodies: &[RamBody],
    ) -> Result<Vec<RamContactProbe>, RamError> {
        let current = canonical_bodies(current_bodies, None)?;
        let mut probes = Vec::new();
        for (&player_id, ledger) in &self.players {
            for (&sequence, pending) in &ledger.pending {
                if pending.response_applied || pending.prepared.is_some() {
                    continue;
                }
                let receipt = &pending.receipt;
                let player_key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: player_id,
                };
                let bot_key = VehicleKey {
                    kind: VehicleKind::Bot,
                    id: receipt.bot_id,
                };
                let pair = RamPair::new(player_key, bot_key)?;
                let Some(player_current) = current.get(&player_key) else {
                    continue;
                };
                let Some(bot_current) = current.get(&bot_key) else {
                    continue;
                };
                let HistoricalBodyLookup::Found(bot_historical) = self.historical_body_at(
                    receipt.bot_id,
                    receipt.bot_state_revision,
                    receipt.presentation_time_us,
                ) else {
                    continue;
                };
                if !player_current.alive || !bot_current.alive || !bot_historical.alive {
                    continue;
                }
                let player_historical = receipt.as_body(player_id, player_current);
                let (first, second) = if pair.first == player_key {
                    (&player_historical, &bot_historical)
                } else {
                    (&bot_historical, &player_historical)
                };
                if pair_contact(first, second).is_none() {
                    continue;
                }
                let point = (receipt.contact_x, receipt.contact_y, receipt.contact_z);
                if !contact_point_inside_with_slop(
                    &player_historical,
                    point,
                    RAM_CONTACT_POINT_SLOP,
                ) || !contact_point_inside(&bot_historical, point)
                {
                    continue;
                }
                let Some(impact_contact) = pair_impact_contact(first, second) else {
                    continue;
                };
                if !contact_needs_native(first, second, impact_contact) {
                    continue;
                }
                let cursor = RamSourceCursor::new(sequence, receipt.bot_state_revision)?;
                if let Some(probe) = contact_probe(
                    RamResolutionSource::PlayerReceipt {
                        player_id,
                        sequence,
                        bot_state_revision: receipt.bot_state_revision,
                        presentation_time_us: receipt.presentation_time_us,
                    },
                    cursor,
                    receipt.presentation_time_us,
                    first,
                    second,
                    impact_contact,
                    Some(point),
                    if pair.first == player_key {
                        (RAM_CONTACT_POINT_SLOP, 0.0)
                    } else {
                        (0.0, RAM_CONTACT_POINT_SLOP)
                    },
                )? {
                    probes.push(probe);
                }
            }
        }
        Ok(probes)
    }

    /// Persist the source-tick Bot response fence independently of the
    /// receipt's later ordered HP result.
    pub fn mark_player_contact_response_applied(
        &mut self,
        probe: &RamContactProbe,
    ) -> Result<(), RamError> {
        let RamResolutionSource::PlayerReceipt {
            player_id,
            sequence,
            bot_state_revision,
            presentation_time_us,
        } = probe.source
        else {
            return Err(RamError::ResolutionConflict);
        };
        let pending = self
            .players
            .get_mut(&player_id)
            .and_then(|ledger| ledger.pending.get_mut(&sequence))
            .filter(|pending| {
                pending.receipt.bot_state_revision == bot_state_revision
                    && pending.receipt.presentation_time_us == presentation_time_us
            })
            .ok_or(RamError::ResolutionNotPrepared)?;
        if pending.response_applied {
            return Err(RamError::ResolutionConflict);
        }
        pending.response_applied = true;
        Ok(())
    }

    /// Resolve the delayed damage half of one already-applied fixed-frame
    /// collision. Positional and velocity deltas are deliberately zero here:
    /// they were committed atomically at the source tick, before T+3 armour.
    pub fn resolve_fixed_contact_probe(
        &mut self,
        probe: &RamContactProbe,
        evidence: NativeRamContactEvidence,
    ) -> Result<RamResolution, RamError> {
        let RamResolutionSource::FixedTick { time_us } = probe.source else {
            return Err(RamError::InvalidNativeEvidence);
        };
        if time_us != probe.source_time_us
            || evidence.pair != probe.pair
            || evidence.cursor != probe.cursor
            || evidence.source_time_us != probe.source_time_us
            || evidence.first_moving != probe.first_moving
            || evidence.second_moving != probe.second_moving
        {
            return Err(RamError::InvalidNativeEvidence);
        }
        let Some(response_contact) = pair_contact(&probe.first, &probe.second) else {
            return Err(RamError::InvalidNativeEvidence);
        };
        let Some(impact_contact) = pair_impact_contact(&probe.first, &probe.second) else {
            return Err(RamError::InvalidNativeEvidence);
        };
        if !probe_matches_contact(probe, impact_contact) {
            return Err(RamError::InvalidNativeEvidence);
        }
        let pair_result = pair_result(
            &probe.first,
            &probe.second,
            response_contact,
            Some(impact_contact),
            Some(&evidence),
        )?;
        let (damage, outcome) = match pair_result.damage {
            None => (None, RamResolutionOutcome::Contact),
            Some((damage_first, damage_second)) => (
                Some(atomic_damage(
                    probe.pair,
                    damage_first,
                    damage_second,
                    format!(
                        "ram:tick:{}:{}:{}:{}:{}",
                        probe.source_time_us,
                        kind_token(probe.pair.first.kind),
                        probe.pair.first.id,
                        kind_token(probe.pair.second.kind),
                        probe.pair.second.id
                    ),
                )?),
                RamResolutionOutcome::Damage,
            ),
        };
        Ok(RamResolution {
            source: probe.source,
            pair: probe.pair,
            first_delta: RamBodyDelta::zero(probe.pair.first),
            second_delta: RamBodyDelta::zero(probe.pair.second),
            damage,
            outcome,
        })
    }

    /// Resolve only the canonical Bot's velocity half for a newly admitted
    /// player contact. Native armour is irrelevant to collision response, so
    /// this can be committed at the receipt tick while HP waits for T+3.
    pub fn player_contact_response(
        &self,
        probe: &RamContactProbe,
    ) -> Result<RamResolution, RamError> {
        let RamResolutionSource::PlayerReceipt { .. } = probe.source else {
            return Err(RamError::ResolutionConflict);
        };
        let bot_key = if probe.pair.first.kind == VehicleKind::Bot
            && probe.pair.second.kind == VehicleKind::Player
        {
            probe.pair.first
        } else if probe.pair.second.kind == VehicleKind::Bot
            && probe.pair.first.kind == VehicleKind::Player
        {
            probe.pair.second
        } else {
            return Err(RamError::ResolutionConflict);
        };
        let Some(response_contact) = pair_contact(&probe.first, &probe.second) else {
            return Err(RamError::ResolutionConflict);
        };
        let Some(impact_contact) = pair_impact_contact(&probe.first, &probe.second) else {
            return Err(RamError::ResolutionConflict);
        };
        if !probe_matches_contact(probe, impact_contact)
            || !contact_needs_native(&probe.first, &probe.second, impact_contact)
        {
            return Err(RamError::ResolutionConflict);
        }
        let pair_result = pair_result(
            &probe.first,
            &probe.second,
            response_contact,
            Some(impact_contact),
            None,
        )?;
        let mut first_delta = RamBodyDelta::zero(probe.pair.first);
        let mut second_delta = RamBodyDelta::zero(probe.pair.second);
        let bot_delta = if bot_key == probe.pair.first {
            pair_result.first_delta
        } else {
            pair_result.second_delta
        };
        if bot_key == probe.pair.first {
            first_delta.velocity_x = bot_delta.velocity_x;
            first_delta.velocity_z = bot_delta.velocity_z;
        } else {
            second_delta.velocity_x = bot_delta.velocity_x;
            second_delta.velocity_z = bot_delta.velocity_z;
        }
        Ok(RamResolution {
            source: probe.source,
            pair: probe.pair,
            first_delta,
            second_delta,
            damage: None,
            outcome: RamResolutionOutcome::Contact,
        })
    }

    /// Consume a player receipt whose exact hidden-native query completed
    /// without two structural plates. This is terminal and fail-closed so a
    /// permanently unsupported head cannot block the ordered receipt ledger.
    pub fn prepare_player_probe_unavailable(
        &mut self,
        probe: &RamContactProbe,
    ) -> Result<RamResolution, RamError> {
        let RamResolutionSource::PlayerReceipt {
            player_id,
            sequence,
            bot_state_revision,
            presentation_time_us,
        } = probe.source
        else {
            return Err(RamError::ResolutionConflict);
        };
        let pending = self
            .players
            .get(&player_id)
            .and_then(|ledger| ledger.pending.get(&sequence))
            .filter(|pending| {
                pending.prepared.is_none()
                    && pending.receipt.bot_state_revision == bot_state_revision
                    && pending.receipt.presentation_time_us == presentation_time_us
            })
            .ok_or(RamError::ResolutionNotPrepared)?;
        let receipt = &pending.receipt;
        if probe.pair
            != RamPair::new(
                VehicleKey {
                    kind: VehicleKind::Player,
                    id: player_id,
                },
                VehicleKey {
                    kind: VehicleKind::Bot,
                    id: receipt.bot_id,
                },
            )?
            || probe.cursor != RamSourceCursor::new(sequence, bot_state_revision)?
            || probe.source_time_us != presentation_time_us
        {
            return Err(RamError::ResolutionConflict);
        }
        let resolution = RamResolution {
            source: probe.source,
            pair: probe.pair,
            first_delta: RamBodyDelta::zero(probe.pair.first),
            second_delta: RamBodyDelta::zero(probe.pair.second),
            damage: None,
            outcome: RamResolutionOutcome::NativeEvidenceUnavailable,
        };
        self.store_prepared(player_id, sequence, resolution.clone())?;
        Ok(resolution)
    }

    fn prepare_player_sequence(
        &mut self,
        player_id: u64,
        sequence: u64,
        current: &BTreeMap<VehicleKey, RamBody>,
        native_evidence: &BTreeMap<(RamPair, RamSourceCursor, u64), NativeRamContactEvidence>,
    ) -> Result<Option<RamResolution>, RamError> {
        let (receipt, response_applied, prepared) = {
            let ledger = self
                .players
                .get(&player_id)
                .expect("player id came from the ledger map");
            let Some(pending) = ledger.pending.get(&sequence) else {
                return Ok(None);
            };
            (
                pending.receipt.clone(),
                pending.response_applied,
                pending.prepared.clone(),
            )
        };
        if let Some(prepared) = prepared {
            return Ok(Some(prepared));
        }

        let player_key = VehicleKey {
            kind: VehicleKind::Player,
            id: player_id,
        };
        let bot_key = VehicleKey {
            kind: VehicleKind::Bot,
            id: receipt.bot_id,
        };
        let pair = RamPair::new(player_key, bot_key)?;
        let source = RamResolutionSource::PlayerReceipt {
            player_id,
            sequence,
            bot_state_revision: receipt.bot_state_revision,
            presentation_time_us: receipt.presentation_time_us,
        };
        let unavailable = |outcome| RamResolution {
            source,
            pair,
            first_delta: RamBodyDelta::zero(pair.first),
            second_delta: RamBodyDelta::zero(pair.second),
            damage: None,
            outcome,
        };

        let Some(player_current) = current.get(&player_key) else {
            let resolution = unavailable(RamResolutionOutcome::VehicleUnavailable);
            self.store_prepared(player_id, sequence, resolution.clone())?;
            return Ok(Some(resolution));
        };
        let bot_current = current.get(&bot_key);
        let bot_historical = match self.historical_body_at(
            receipt.bot_id,
            receipt.bot_state_revision,
            receipt.presentation_time_us,
        ) {
            HistoricalBodyLookup::Pending => return Ok(None),
            HistoricalBodyLookup::Unavailable => {
                let resolution = unavailable(RamResolutionOutcome::HistoricalStateUnavailable);
                self.store_prepared(player_id, sequence, resolution.clone())?;
                return Ok(Some(resolution));
            }
            HistoricalBodyLookup::Found(body) => body,
        };
        if !player_current.alive
            || bot_current.is_none_or(|body| !body.alive)
            || !bot_historical.alive
        {
            let resolution = unavailable(RamResolutionOutcome::VehicleUnavailable);
            self.store_prepared(player_id, sequence, resolution.clone())?;
            return Ok(Some(resolution));
        }
        let player_historical = receipt.as_body(player_id, player_current);
        let (first, second) = if pair.first == player_key {
            (&player_historical, &bot_historical)
        } else {
            (&bot_historical, &player_historical)
        };
        let Some(response_contact) = pair_contact(first, second) else {
            let resolution = unavailable(RamResolutionOutcome::NoContact);
            self.store_prepared(player_id, sequence, resolution.clone())?;
            return Ok(Some(resolution));
        };
        let point = (receipt.contact_x, receipt.contact_y, receipt.contact_z);
        if !contact_point_inside_with_slop(&player_historical, point, RAM_CONTACT_POINT_SLOP)
            || !contact_point_inside(&bot_historical, point)
        {
            let resolution = unavailable(RamResolutionOutcome::NoContact);
            self.store_prepared(player_id, sequence, resolution.clone())?;
            return Ok(Some(resolution));
        }
        let impact_contact = pair_impact_contact(first, second);
        let expected_cursor = RamSourceCursor::new(sequence, receipt.bot_state_revision)?;
        let evidence = native_evidence.get(&(pair, expected_cursor, receipt.presentation_time_us));
        if impact_contact.is_some_and(|contact| contact_needs_native(first, second, contact))
            && evidence.is_none()
        {
            return Ok(None);
        }
        if evidence.is_some_and(|evidence| evidence.cursor != expected_cursor) {
            return Err(RamError::InvalidNativeEvidence);
        }
        let pair_result = pair_result(first, second, response_contact, impact_contact, evidence)?;

        // The visible client already applied its half at the presented pose.
        // Apply only the historical bot velocity half and no late positional
        // correction to the current canonical simulation.
        let mut first_delta = RamBodyDelta::zero(pair.first);
        let mut second_delta = RamBodyDelta::zero(pair.second);
        let bot_delta = if pair.first == bot_key {
            pair_result.first_delta
        } else {
            pair_result.second_delta
        };
        if !response_applied {
            if pair.first == bot_key {
                first_delta.velocity_x = bot_delta.velocity_x;
                first_delta.velocity_z = bot_delta.velocity_z;
            } else {
                second_delta.velocity_x = bot_delta.velocity_x;
                second_delta.velocity_z = bot_delta.velocity_z;
            }
        }

        let (damage, outcome) = match pair_result.damage {
            None => (None, RamResolutionOutcome::Contact),
            Some((damage_first, damage_second)) => (
                Some(atomic_damage(
                    pair,
                    damage_first,
                    damage_second,
                    format!("ram:player:{player_id}:contact:{sequence}"),
                )?),
                RamResolutionOutcome::Damage,
            ),
        };
        let resolution = RamResolution {
            source,
            pair,
            first_delta,
            second_delta,
            damage,
            outcome,
        };
        self.store_prepared(player_id, sequence, resolution.clone())?;
        Ok(Some(resolution))
    }

    fn store_prepared(
        &mut self,
        player_id: u64,
        sequence: u64,
        resolution: RamResolution,
    ) -> Result<(), RamError> {
        let pending = self
            .players
            .get_mut(&player_id)
            .and_then(|ledger| ledger.pending.get_mut(&sequence))
            .ok_or(RamError::ResolutionNotPrepared)?;
        match &pending.prepared {
            Some(previous) if previous != &resolution => Err(RamError::ResolutionConflict),
            Some(_) => Ok(()),
            None => {
                pending.prepared = Some(resolution);
                Ok(())
            }
        }
    }

    fn historical_body_at(
        &self,
        bot_id: u64,
        revision: u64,
        presentation_time_us: u64,
    ) -> HistoricalBodyLookup {
        let target = (presentation_time_us, revision);
        let Some(latest_revision) = self.bot_history_order.back().copied() else {
            return HistoricalBodyLookup::Pending;
        };
        let latest = self
            .bot_history
            .get(&latest_revision)
            .expect("history order only contains retained frames");
        if (latest.presentation_time_us, latest.revision) < target {
            return HistoricalBodyLookup::Pending;
        }

        let mut left: Option<&BotHistoryFrame> = None;
        let mut right: Option<&BotHistoryFrame> = None;
        for frame_revision in &self.bot_history_order {
            let frame = self
                .bot_history
                .get(frame_revision)
                .expect("history order only contains retained frames");
            if !frame.bodies.contains_key(&bot_id) {
                continue;
            }
            let key = (frame.presentation_time_us, frame.revision);
            if key <= target {
                left = Some(frame);
            }
            if key >= target {
                right = Some(frame);
                break;
            }
        }
        let (Some(left), Some(right)) = (left, right) else {
            return HistoricalBodyLookup::Unavailable;
        };
        if left.presentation_time_us > presentation_time_us
            || left.revision > revision
            || right.presentation_time_us < presentation_time_us
            || right.revision < revision
        {
            return HistoricalBodyLookup::Unavailable;
        }
        let left_body = left
            .bodies
            .get(&bot_id)
            .expect("the selected left frame contains the bot");
        let right_body = right
            .bodies
            .get(&bot_id)
            .expect("the selected right frame contains the bot");
        if left_body.team != right_body.team {
            return HistoricalBodyLookup::Unavailable;
        }
        if left.revision == right.revision {
            return HistoricalBodyLookup::Found(left_body.clone());
        }
        let span_us = right
            .presentation_time_us
            .saturating_sub(left.presentation_time_us);
        if span_us == 0 {
            return HistoricalBodyLookup::Unavailable;
        }
        let progress = (presentation_time_us - left.presentation_time_us) as f64 / span_us as f64;
        if !(0.0..=1.0).contains(&progress) {
            return HistoricalBodyLookup::Unavailable;
        }
        let seconds = span_us as f64 / 1_000_000.0;
        let mut body = left_body.clone();
        body.x = lerp(left_body.x, right_body.x, progress);
        body.y = lerp(left_body.y, right_body.y, progress);
        body.z = lerp(left_body.z, right_body.z, progress);
        body.yaw = left_body.yaw + angle_delta(left_body.yaw, right_body.yaw) * progress;
        body.pitch = lerp(left_body.pitch, right_body.pitch, progress);
        body.roll = lerp(left_body.roll, right_body.roll, progress);
        body.turret_yaw = left_body.turret_yaw
            + angle_delta(left_body.turret_yaw, right_body.turret_yaw) * progress;
        body.gun_pitch =
            left_body.gun_pitch + angle_delta(left_body.gun_pitch, right_body.gun_pitch) * progress;
        body.vx = (right_body.x - left_body.x) / seconds;
        body.vy = (right_body.y - left_body.y) / seconds;
        body.vz = (right_body.z - left_body.z) / seconds;
        if progress >= 1.0 {
            body.alive = right_body.alive;
            body.mass = right_body.mass;
            body.shape = right_body.shape;
            body.siege_state = right_body.siege_state;
        }
        if body.validate().is_err() {
            return HistoricalBodyLookup::Unavailable;
        }
        HistoricalBodyLookup::Found(body)
    }
}

fn commit_player_resolution_to_ledger(
    ledger: &mut PlayerLedger,
    resolution: &RamResolution,
) -> Result<ResolutionCommit, RamError> {
    let RamResolutionSource::PlayerReceipt { sequence, .. } = resolution.source else {
        return Err(RamError::ResolutionConflict);
    };
    if sequence <= ledger.resolved_sequence {
        return match ledger.terminal.get(&sequence) {
            Some(previous) if previous == resolution => Ok(ResolutionCommit::ExactRetry),
            _ => Err(RamError::ResolutionConflict),
        };
    }
    if sequence != ledger.resolved_sequence + 1 {
        return Err(RamError::ResolutionConflict);
    }
    let pending = ledger
        .pending
        .get(&sequence)
        .ok_or(RamError::ResolutionNotPrepared)?;
    if pending.prepared.as_ref() != Some(resolution) {
        return Err(RamError::ResolutionConflict);
    }
    ledger.pending.remove(&sequence);
    ledger.resolved_sequence = sequence;
    ledger.terminal.insert(sequence, resolution.clone());
    advance_player_terminal_prefix(ledger);
    trim_receipt_history(ledger);
    Ok(ResolutionCommit::New)
}

fn player_outcome_token(outcome: RamResolutionOutcome) -> &'static str {
    match outcome {
        RamResolutionOutcome::Damage => "damage",
        RamResolutionOutcome::Contact | RamResolutionOutcome::ActiveContact => "contact",
        RamResolutionOutcome::NoContact
        | RamResolutionOutcome::VehicleUnavailable
        | RamResolutionOutcome::HistoricalStateUnavailable
        | RamResolutionOutcome::NativeEvidenceUnavailable => "unavailable",
    }
}

#[derive(Clone, Copy, Debug)]
struct Contact {
    normal_x: f64,
    normal_z: f64,
    penetration: f64,
}

#[derive(Clone, Copy, Debug)]
struct PairResult {
    first_delta: RamBodyDelta,
    second_delta: RamBodyDelta,
    damage: Option<(u32, u32)>,
}

fn canonical_native_evidence(
    evidence: &[NativeRamContactEvidence],
) -> Result<BTreeMap<(RamPair, RamSourceCursor, u64), NativeRamContactEvidence>, RamError> {
    let mut canonical = BTreeMap::new();
    for item in evidence {
        let expected_pair = RamPair::new(item.pair.first, item.pair.second)?;
        if expected_pair != item.pair
            || item.source_time_us > MAX_EXACT_INT
            || item.cursor.episode > MAX_RECEIPT_SEQUENCE
            || item.cursor.frontier > MAX_RECEIPT_SEQUENCE
            || RamDamageProfile::new(
                item.first.profile.spall_coefficient,
                item.first.profile.controlled_impact_bonus,
            )
            .is_err()
            || RamDamageProfile::new(
                item.second.profile.spall_coefficient,
                item.second.profile.controlled_impact_bonus,
            )
            .is_err()
            || NativeContactArmor::new(item.first.armor.millimeters).is_err()
            || NativeContactArmor::new(item.second.armor.millimeters).is_err()
        {
            return Err(RamError::InvalidNativeEvidence);
        }
        let key = (item.pair, item.cursor, item.source_time_us);
        match canonical.get(&key) {
            Some(previous) if previous == item => {}
            Some(_) => {
                return Err(RamError::NativeEvidenceConflict {
                    pair: item.pair,
                    source_time_us: item.source_time_us,
                });
            }
            None => {
                canonical.insert(key, *item);
            }
        }
    }
    Ok(canonical)
}

fn exact_damage_amount(value: f64) -> Result<u32, RamError> {
    let floored = value.floor();
    if !floored.is_finite() || floored < 0.0 || floored > f64::from(u32::MAX) {
        return Err(RamError::DamageOutOfRange);
    }
    Ok(floored as u32)
}

fn interpolate_body(
    left: &RamBody,
    left_time_us: u64,
    right: &RamBody,
    right_time_us: u64,
    source_time_us: u64,
) -> Option<RamBody> {
    if left.key != right.key
        || left.team != right.team
        || left.mass != right.mass
        || left.shape != right.shape
        || right_time_us <= left_time_us
        || source_time_us < left_time_us
        || source_time_us > right_time_us
    {
        return None;
    }
    if source_time_us == left_time_us {
        return Some(left.clone());
    }
    if source_time_us == right_time_us {
        return Some(right.clone());
    }
    let span_us = right_time_us - left_time_us;
    let progress = (source_time_us - left_time_us) as f64 / span_us as f64;
    if !progress.is_finite() || !(0.0..=1.0).contains(&progress) {
        return None;
    }
    let mut body = left.clone();
    body.x = lerp(left.x, right.x, progress);
    body.y = lerp(left.y, right.y, progress);
    body.z = lerp(left.z, right.z, progress);
    body.yaw = left.yaw + angle_delta(left.yaw, right.yaw) * progress;
    body.pitch = lerp(left.pitch, right.pitch, progress);
    body.roll = lerp(left.roll, right.roll, progress);
    body.turret_yaw = left.turret_yaw + angle_delta(left.turret_yaw, right.turret_yaw) * progress;
    body.gun_pitch = left.gun_pitch + angle_delta(left.gun_pitch, right.gun_pitch) * progress;
    let seconds = span_us as f64 / 1_000_000.0;
    body.vx = (right.x - left.x) / seconds;
    body.vy = (right.y - left.y) / seconds;
    body.vz = (right.z - left.z) / seconds;
    body.validate().ok()?;
    Some(body)
}

fn interpolate_body_with_velocity(
    left: &RamBody,
    left_time_us: u64,
    right: &RamBody,
    right_time_us: u64,
    source_time_us: u64,
) -> Option<RamBody> {
    let mut body = interpolate_body(left, left_time_us, right, right_time_us, source_time_us)?;
    let span_us = right_time_us.checked_sub(left_time_us)?;
    let progress = source_time_us.checked_sub(left_time_us)? as f64 / span_us as f64;
    body.vx = lerp(left.vx, right.vx, progress);
    body.vy = lerp(left.vy, right.vy, progress);
    body.vz = lerp(left.vz, right.vz, progress);
    body.validate().ok()?;
    Some(body)
}

fn canonical_bodies(
    bodies: &[RamBody],
    required_kind: Option<VehicleKind>,
) -> Result<BTreeMap<VehicleKey, RamBody>, RamError> {
    let mut result = BTreeMap::new();
    for body in bodies {
        body.validate()?;
        if required_kind.is_some_and(|kind| body.key.kind != kind) {
            return Err(RamError::HistoricalBodyKind);
        }
        if result.insert(body.key, body.clone()).is_some() {
            return Err(RamError::DuplicateBody(body.key));
        }
    }
    Ok(result)
}

fn axes(yaw: f64) -> [[f64; 2]; 2] {
    let sine = yaw.sin();
    let cosine = yaw.cos();
    [[cosine, -sine], [sine, cosine]]
}

fn vertical_overlap(first: &RamBody, second: &RamBody) -> bool {
    let first_low = first.y + first.shape.lower_y;
    let first_high = first.y + first.shape.upper_y;
    let second_low = second.y + second.shape.lower_y;
    let second_high = second.y + second.shape.upper_y;
    first_high.min(second_high) - first_low.max(second_low) > VERTICAL_SLOP
}

fn same_team(first: &RamBody, second: &RamBody) -> bool {
    first.team == second.team
}

fn contact_needs_native(first: &RamBody, second: &RamBody, contact: Contact) -> bool {
    if !first.alive || !second.alive || same_team(first, second) {
        return false;
    }
    let relative_normal =
        (first.vx - second.vx) * contact.normal_x + (first.vz - second.vz) * contact.normal_z;
    relative_normal < 0.0
}

fn contact_probe(
    source: RamResolutionSource,
    cursor: RamSourceCursor,
    source_time_us: u64,
    first: &RamBody,
    second: &RamBody,
    contact: Contact,
    contact_point: Option<(f64, f64, f64)>,
    contact_point_slop: (f64, f64),
) -> Result<Option<RamContactProbe>, RamError> {
    let pair = RamPair::new(first.key, second.key)?;
    if pair.first != first.key || pair.second != second.key || source_time_us > MAX_EXACT_INT {
        return Err(RamError::InvalidPoseFrame);
    }
    let point = match contact_point {
        Some(point) => point,
        None => {
            let Some((x, z)) = obb_overlap_point(first, second) else {
                return Ok(None);
            };
            let low = (first.y + first.shape.lower_y).max(second.y + second.shape.lower_y);
            let high = (first.y + first.shape.upper_y).min(second.y + second.shape.upper_y);
            if high <= low {
                return Ok(None);
            }
            (x, (low + high) * 0.5, z)
        }
    };
    if ![point.0, point.1, point.2].into_iter().all(f64::is_finite)
        || !contact_point_inside_with_slop(first, point, contact_point_slop.0)
        || !contact_point_inside_with_slop(second, point, contact_point_slop.1)
    {
        return Ok(None);
    }
    Ok(Some(RamContactProbe {
        source,
        pair,
        cursor,
        source_time_us,
        first: first.clone(),
        second: second.clone(),
        contact_x: point.0,
        contact_y: point.1,
        contact_z: point.2,
        normal_x: contact.normal_x,
        normal_z: contact.normal_z,
        first_moving: first.vx != 0.0 || first.vy != 0.0 || first.vz != 0.0,
        second_moving: second.vx != 0.0 || second.vy != 0.0 || second.vz != 0.0,
    }))
}

fn contact_point_inside(body: &RamBody, point: (f64, f64, f64)) -> bool {
    contact_point_inside_with_slop(body, point, 0.0)
}

fn contact_point_inside_with_slop(body: &RamBody, point: (f64, f64, f64), slop: f64) -> bool {
    const EPSILON: f64 = 1.0e-7;
    if !slop.is_finite() || slop < 0.0 {
        return false;
    }
    let dx = point.0 - body.x;
    let dz = point.2 - body.z;
    let sine = body.yaw.sin();
    let cosine = body.yaw.cos();
    let local_right = dx * cosine - dz * sine;
    let local_forward = dx * sine + dz * cosine;
    local_right.abs() <= body.shape.half_width + slop + EPSILON
        && local_forward.abs() <= body.shape.half_length + slop + EPSILON
        && point.1 >= body.y + body.shape.lower_y - slop - EPSILON
        && point.1 <= body.y + body.shape.upper_y + slop + EPSILON
}

fn obb_vertices(body: &RamBody) -> Vec<(f64, f64)> {
    let sine = body.yaw.sin();
    let cosine = body.yaw.cos();
    let right = (cosine, -sine);
    let forward = (sine, cosine);
    [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
        .into_iter()
        .map(|(side, longitudinal)| {
            (
                body.x
                    + side * body.shape.half_width * right.0
                    + longitudinal * body.shape.half_length * forward.0,
                body.z
                    + side * body.shape.half_width * right.1
                    + longitudinal * body.shape.half_length * forward.1,
            )
        })
        .collect()
}

fn clip_polygon(polygon: Vec<(f64, f64)>, start: (f64, f64), end: (f64, f64)) -> Vec<(f64, f64)> {
    if polygon.is_empty() {
        return polygon;
    }
    let edge_x = end.0 - start.0;
    let edge_z = end.1 - start.1;
    let inside =
        |point: (f64, f64)| edge_x * (point.1 - start.1) - edge_z * (point.0 - start.0) >= -1.0e-7;
    let intersection = |first: (f64, f64), second: (f64, f64)| {
        let segment_x = second.0 - first.0;
        let segment_z = second.1 - first.1;
        let denominator = segment_x * edge_z - segment_z * edge_x;
        if denominator.abs() <= 1.0e-12 {
            second
        } else {
            let ratio = ((start.0 - first.0) * edge_z - (start.1 - first.1) * edge_x) / denominator;
            (first.0 + ratio * segment_x, first.1 + ratio * segment_z)
        }
    };
    let mut output = Vec::new();
    let mut previous = *polygon.last().expect("non-empty polygon");
    let mut previous_inside = inside(previous);
    for current in polygon {
        let current_inside = inside(current);
        if current_inside != previous_inside {
            output.push(intersection(previous, current));
        }
        if current_inside {
            output.push(current);
        }
        previous = current;
        previous_inside = current_inside;
    }
    output
}

fn obb_overlap_point(first: &RamBody, second: &RamBody) -> Option<(f64, f64)> {
    let mut polygon = obb_vertices(first);
    let clip = obb_vertices(second);
    for index in 0..clip.len() {
        polygon = clip_polygon(polygon, clip[index], clip[(index + 1) % clip.len()]);
        if polygon.is_empty() {
            return None;
        }
    }
    let count = polygon.len() as f64;
    Some((
        polygon.iter().map(|point| point.0).sum::<f64>() / count,
        polygon.iter().map(|point| point.1).sum::<f64>() / count,
    ))
}

fn pair_contact(first: &RamBody, second: &RamBody) -> Option<Contact> {
    if !vertical_overlap(first, second) {
        return None;
    }
    let center_dx = first.x - second.x;
    let center_dz = first.z - second.z;
    let first_radius = first.shape.half_width.hypot(first.shape.half_length);
    let second_radius = second.shape.half_width.hypot(second.shape.half_length);
    let maximum_distance = first_radius + second_radius + 0.25;
    if center_dx * center_dx + center_dz * center_dz > maximum_distance * maximum_distance {
        return None;
    }

    let first_axes = axes(first.yaw);
    let second_axes = axes(second.yaw);
    let mut best: Option<Contact> = None;
    for axis in [first_axes[0], first_axes[1], second_axes[0], second_axes[1]] {
        let mut axis_x = axis[0];
        let mut axis_z = axis[1];
        let radius_first = first.shape.half_width
            * (axis_x * first_axes[0][0] + axis_z * first_axes[0][1]).abs()
            + first.shape.half_length
                * (axis_x * first_axes[1][0] + axis_z * first_axes[1][1]).abs();
        let radius_second = second.shape.half_width
            * (axis_x * second_axes[0][0] + axis_z * second_axes[0][1]).abs()
            + second.shape.half_length
                * (axis_x * second_axes[1][0] + axis_z * second_axes[1][1]).abs();
        let signed_distance = center_dx * axis_x + center_dz * axis_z;
        let overlap = radius_first + radius_second - signed_distance.abs();
        if overlap <= 0.0 {
            return None;
        }
        if best.is_none_or(|contact| overlap < contact.penetration) {
            if signed_distance < 0.0 {
                axis_x = -axis_x;
                axis_z = -axis_z;
            }
            best = Some(Contact {
                normal_x: axis_x,
                normal_z: axis_z,
                penetration: overlap,
            });
        }
    }
    let mut contact = best?;
    let projection = center_dx * contact.normal_x + center_dz * contact.normal_z;
    if projection.abs() <= 1.0e-9 {
        if contact.normal_x < -1.0e-9
            || (contact.normal_x.abs() <= 1.0e-9 && contact.normal_z < 0.0)
        {
            contact.normal_x = -contact.normal_x;
            contact.normal_z = -contact.normal_z;
        }
        // `first` is canonical by VehicleKey, so the positive undirected axis
        // is a stable B -> A normal even for coincident centers.
    }
    Some(contact)
}

/// Recover the historical first horizontal impact face for an overlapping
/// pair. The current minimum-translation axis remains the correct separation
/// direction, but it can rotate to another face after one delayed/deep step.
fn pair_impact_contact(first: &RamBody, second: &RamBody) -> Option<Contact> {
    if !vertical_overlap(first, second) {
        return None;
    }
    let first_axes = axes(first.yaw);
    let second_axes = axes(second.yaw);
    let center_dx = first.x - second.x;
    let center_dz = first.z - second.z;
    let relative_x = first.vx - second.vx;
    let relative_z = first.vz - second.vz;
    if ![center_dx, center_dz, relative_x, relative_z]
        .into_iter()
        .all(f64::is_finite)
    {
        return None;
    }

    let mut best_age: Option<f64> = None;
    let mut best_contact: Option<Contact> = None;
    for axis in [first_axes[0], first_axes[1], second_axes[0], second_axes[1]] {
        let axis_x = axis[0];
        let axis_z = axis[1];
        let radius_first = first.shape.half_width
            * (axis_x * first_axes[0][0] + axis_z * first_axes[0][1]).abs()
            + first.shape.half_length
                * (axis_x * first_axes[1][0] + axis_z * first_axes[1][1]).abs();
        let radius_second = second.shape.half_width
            * (axis_x * second_axes[0][0] + axis_z * second_axes[0][1]).abs()
            + second.shape.half_length
                * (axis_x * second_axes[1][0] + axis_z * second_axes[1][1]).abs();
        let radius = radius_first + radius_second;
        let signed_distance = center_dx * axis_x + center_dz * axis_z;
        let overlap = radius - signed_distance.abs();
        if overlap <= 0.0 {
            return None;
        }
        let axis_velocity = relative_x * axis_x + relative_z * axis_z;
        if axis_velocity.abs() <= 1.0e-9 {
            continue;
        }
        let (entry_age, normal_x, normal_z) = if axis_velocity > 0.0 {
            ((radius + signed_distance) / axis_velocity, -axis_x, -axis_z)
        } else {
            ((radius - signed_distance) / -axis_velocity, axis_x, axis_z)
        };
        if entry_age < -1.0e-9 {
            continue;
        }
        if best_age.is_none_or(|age| entry_age < age - 1.0e-9) {
            best_age = Some(entry_age.max(0.0));
            best_contact = Some(Contact {
                normal_x,
                normal_z,
                penetration: overlap,
            });
        }
    }
    let contact = best_contact?;
    // An unbounded rewind could otherwise trace a separating body through the
    // other hull and invent entry on its opposite face.
    if contact.normal_x * center_dx + contact.normal_z * center_dz <= 1.0e-9 {
        return None;
    }
    Some(contact)
}

fn probe_matches_contact(probe: &RamContactProbe, contact: Contact) -> bool {
    (probe.normal_x - contact.normal_x).abs() <= 1.0e-9
        && (probe.normal_z - contact.normal_z).abs() <= 1.0e-9
}

fn pair_result(
    first: &RamBody,
    second: &RamBody,
    response_contact: Contact,
    impact_contact: Option<Contact>,
    native_evidence: Option<&NativeRamContactEvidence>,
) -> Result<PairResult, RamError> {
    let inverse_first = if first.alive { 1.0 / first.mass } else { 0.0 };
    let inverse_second = if second.alive { 1.0 / second.mass } else { 0.0 };
    let inverse_sum = inverse_first + inverse_second;
    let mut first_delta = RamBodyDelta::zero(first.key);
    let mut second_delta = RamBodyDelta::zero(second.key);
    if inverse_sum > 0.0 {
        let correction = (response_contact.penetration - POSITION_SLOP).max(0.0) * POSITION_PERCENT
            / inverse_sum;
        first_delta.correction_x = response_contact.normal_x * correction * inverse_first;
        first_delta.correction_z = response_contact.normal_z * correction * inverse_first;
        second_delta.correction_x = -response_contact.normal_x * correction * inverse_second;
        second_delta.correction_z = -response_contact.normal_z * correction * inverse_second;
    }
    let first_velocity = if first.alive {
        (first.vx, first.vz)
    } else {
        (0.0, 0.0)
    };
    let second_velocity = if second.alive {
        (second.vx, second.vz)
    } else {
        (0.0, 0.0)
    };
    let response_relative_normal = (first_velocity.0 - second_velocity.0)
        * response_contact.normal_x
        + (first_velocity.1 - second_velocity.1) * response_contact.normal_z;
    if inverse_sum > 0.0 && response_relative_normal < 0.0 {
        let impulse = -response_relative_normal / inverse_sum;
        first_delta.velocity_x = response_contact.normal_x * impulse * inverse_first;
        first_delta.velocity_z = response_contact.normal_z * impulse * inverse_first;
        second_delta.velocity_x = -response_contact.normal_x * impulse * inverse_second;
        second_delta.velocity_z = -response_contact.normal_z * impulse * inverse_second;
    }
    let damage = if first.alive && second.alive && !same_team(first, second) {
        match (impact_contact, native_evidence) {
            (Some(impact_contact), Some(evidence)) => {
                let impact_relative_normal = (first_velocity.0 - second_velocity.0)
                    * impact_contact.normal_x
                    + (first_velocity.1 - second_velocity.1) * impact_contact.normal_z;
                if impact_relative_normal >= 0.0 {
                    None
                } else {
                    let exact = exact_ram_damage(
                        -impact_relative_normal,
                        first.mass,
                        second.mass,
                        evidence.first,
                        evidence.second,
                        evidence.first_moving,
                        evidence.second_moving,
                    )?;
                    (exact.first > 0 || exact.second > 0).then_some((exact.first, exact.second))
                }
            }
            _ => None,
        }
    } else {
        None
    };
    Ok(PairResult {
        first_delta,
        second_delta,
        damage,
    })
}

fn atomic_damage(
    pair: RamPair,
    damage_first: u32,
    damage_second: u32,
    operation_id: String,
) -> Result<AtomicRamDamage, RamError> {
    let damage = AtomicRamDamage {
        operation_id,
        first: DamageProposal {
            attacker: Some(pair.second),
            target: pair.first,
            amount: damage_first,
            source: DamageSource::Ram,
        },
        second: DamageProposal {
            attacker: Some(pair.first),
            target: pair.second,
            amount: damage_second,
            source: DamageSource::Ram,
        },
    };
    damage.validate()?;
    Ok(damage)
}

fn exact_u64(
    object: &Map<String, Value>,
    field: &str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, RamError> {
    object
        .get(field)
        .and_then(Value::as_u64)
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or(RamError::InvalidReceipt)
}

fn finite_f64(
    object: &Map<String, Value>,
    field: &str,
    minimum: f64,
    maximum: f64,
) -> Result<f64, RamError> {
    object
        .get(field)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && (minimum..=maximum).contains(value))
        .ok_or(RamError::InvalidReceipt)
}

fn kind_token(kind: VehicleKind) -> &'static str {
    match kind {
        VehicleKind::Player => "player",
        VehicleKind::Bot => "bot",
    }
}

fn lerp(first: f64, second: f64, progress: f64) -> f64 {
    first + (second - first) * progress
}

fn angle_delta(first: f64, second: f64) -> f64 {
    let mut delta = second - first;
    while delta > std::f64::consts::PI {
        delta -= std::f64::consts::TAU;
    }
    while delta < -std::f64::consts::PI {
        delta += std::f64::consts::TAU;
    }
    delta
}

fn advance_player_terminal_prefix(ledger: &mut PlayerLedger) {
    while ledger.resolved_sequence < ledger.admitted_sequence {
        let next = ledger.resolved_sequence.saturating_add(1);
        if ledger.pending.contains_key(&next) {
            break;
        }
        ledger.resolved_sequence = next;
    }
}

fn advance_player_pair_terminal_prefix(ledger: &mut PlayerPairLedger) {
    while ledger.resolved_sequence < ledger.admitted_sequence {
        let next = ledger.resolved_sequence.saturating_add(1);
        if ledger.pending.contains_key(&next) {
            break;
        }
        ledger.resolved_sequence = next;
    }
}

fn trim_receipt_history(ledger: &mut PlayerLedger) {
    while ledger.fingerprints.len() > MAX_RECEIPT_HISTORY {
        let Some(oldest) = ledger.fingerprints.keys().next().copied() else {
            break;
        };
        if ledger.pending.contains_key(&oldest) {
            break;
        }
        ledger.fingerprints.remove(&oldest);
    }
    while ledger.terminal.len() > MAX_RECEIPT_HISTORY {
        let Some(oldest) = ledger.terminal.keys().next().copied() else {
            break;
        };
        ledger.terminal.remove(&oldest);
    }
    while ledger.rejected.len() > MAX_RECEIPT_HISTORY {
        let Some(oldest) = ledger.rejected.first().copied() else {
            break;
        };
        ledger.rejected.remove(&oldest);
    }
}

fn trim_player_pair_history(ledger: &mut PlayerPairLedger) {
    while ledger.fingerprints.len() > MAX_RECEIPT_HISTORY {
        let Some(oldest) = ledger.fingerprints.keys().next().copied() else {
            break;
        };
        if ledger.pending.contains_key(&oldest) {
            break;
        }
        ledger.fingerprints.remove(&oldest);
    }
    while ledger.rejected.len() > MAX_RECEIPT_HISTORY {
        let Some(oldest) = ledger.rejected.first().copied() else {
            break;
        };
        ledger.rejected.remove(&oldest);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn key(kind: VehicleKind, id: u64) -> VehicleKey {
        VehicleKey { kind, id }
    }

    fn shape() -> RamShape {
        RamShape::new(1.5, 3.5, -0.8, 2.0).unwrap()
    }

    fn body(kind: VehicleKind, id: u64, x: f64, vx: f64, mass: f64) -> RamBody {
        // Most fixtures describe opposing pairs. Keep that explicit by
        // assigning inverse id parity to Bot and player namespaces.
        let team = match kind {
            VehicleKind::Player => {
                if id % 2 == 0 {
                    2
                } else {
                    1
                }
            }
            VehicleKind::Bot => {
                if id % 2 == 0 {
                    1
                } else {
                    2
                }
            }
        };
        RamBody {
            key: key(kind, id),
            team,
            alive: true,
            x,
            y: 0.0,
            z: 0.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            mass,
            vx,
            vy: 0.0,
            vz: 0.0,
            turret_yaw: 0.0,
            gun_pitch: 0.0,
            siege_state: 0,
            shape: shape(),
        }
    }

    fn receipt(sequence: u64, revision: u64, time_us: u64) -> Value {
        json!({
            "seq": sequence,
            "bot_id": 1,
            "bot_state_revision": revision,
            "presentation_time_us": time_us,
            "contact_x": 0.0,
            "contact_y": 0.5,
            "contact_z": 0.0,
            "x": -1.0,
            "y": 0.0,
            "z": 0.0,
            "yaw": 0.0,
            "pitch": 0.0,
            "roll": 0.0,
            "vx": 10.0,
            "vy": 0.0,
            "vz": 0.0,
            "turret_yaw": 0.0,
            "gun_pitch": 0.0,
            "siege_state": 0
        })
    }

    fn vehicle_evidence(armor: f64, spall: f64, bonus: f64) -> RamVehicleContactEvidence {
        RamVehicleContactEvidence::new(
            NativeContactArmor::new(armor).unwrap(),
            RamDamageProfile::new(spall, bonus).unwrap(),
        )
    }

    fn evidence(
        first: VehicleKey,
        second: VehicleKey,
        source_time_us: u64,
        frontier: u64,
    ) -> NativeRamContactEvidence {
        NativeRamContactEvidence::new(
            RamPair::new(first, second).unwrap(),
            RamSourceCursor::new(1, frontier).unwrap(),
            source_time_us,
            vehicle_evidence(0.0, 1.0, 0.0),
            vehicle_evidence(0.0, 1.0, 0.0),
            true,
            false,
        )
        .unwrap()
    }

    fn pose_frame(
        kind: VehicleKind,
        id: u64,
        episode: u64,
        frontier: u64,
        source_time_us: u64,
        x: f64,
    ) -> RamPoseFrame {
        RamPoseFrame::new(
            RamSourceCursor::new(episode, frontier).unwrap(),
            source_time_us,
            body(kind, id, x, 0.0, 20_000.0),
        )
        .unwrap()
    }

    #[test]
    fn damage_profile_and_native_contact_armor_are_strict_typed_inputs() {
        assert!(RamDamageProfile::new(1.0, 0.0).is_ok());
        assert!(RamDamageProfile::new(1.5, 0.15).is_ok());
        assert_eq!(
            RamDamageProfile::new(1.500_001, 0.0),
            Err(RamError::InvalidDamageProfile)
        );
        assert_eq!(
            RamDamageProfile::new(1.0, 0.150_001),
            Err(RamError::InvalidDamageProfile)
        );
        assert_eq!(
            NativeContactArmor::new(-0.01),
            Err(RamError::InvalidContactArmor)
        );
        assert_eq!(
            NativeContactArmor::new(f64::NAN),
            Err(RamError::InvalidContactArmor)
        );
    }

    #[test]
    fn documented_kinetic_he_absorption_and_inverse_mass_law_is_exact() {
        let no_armor = vehicle_evidence(0.0, 1.0, 0.0);
        assert_eq!(
            exact_ram_damage(10.0, 75_000.0, 25_000.0, no_armor, no_armor, false, false,).unwrap(),
            ExactRamDamage {
                first: 156,
                second: 468,
            }
        );
        assert_eq!(
            exact_ram_damage(
                10.0,
                75_000.0,
                25_000.0,
                vehicle_evidence(100.0, 1.5, 0.0),
                vehicle_evidence(200.0, 1.0, 0.0),
                false,
                false,
            )
            .unwrap(),
            ExactRamDamage {
                first: 115,
                second: 413,
            }
        );
    }

    #[test]
    fn controlled_impact_changes_only_final_moving_side_damage() {
        let skilled = vehicle_evidence(100.0, 1.0, 0.15);
        let ordinary = vehicle_evidence(100.0, 1.0, 0.0);
        let stationary =
            exact_ram_damage(10.0, 50_000.0, 50_000.0, skilled, ordinary, false, false).unwrap();
        let moving =
            exact_ram_damage(10.0, 50_000.0, 50_000.0, skilled, ordinary, true, false).unwrap();
        assert_eq!(
            stationary,
            ExactRamDamage {
                first: 285,
                second: 285,
            }
        );
        assert_eq!(
            moving,
            ExactRamDamage {
                first: 242,
                second: 327,
            }
        );
    }

    #[test]
    fn temporary_coefficient_scales_only_final_ram_hp_loss() {
        let type_62 = vehicle_evidence(30.0, 1.0, 0.0);
        let kv_2 = vehicle_evidence(75.0, 1.0, 0.0);

        assert_eq!(
            exact_ram_damage(5.777_36, 21_300.0, 53_100.0, type_62, kv_2, true, false,).unwrap(),
            ExactRamDamage {
                first: 102,
                second: 23,
            }
        );
    }

    #[test]
    fn exact_damage_has_no_speed_threshold_ratio_clamp_or_hp_cap() {
        let no_armor = vehicle_evidence(0.0, 1.0, 0.0);
        assert_eq!(
            exact_ram_damage(1.0, 25_000.0, 25_000.0, no_armor, no_armor, false, false).unwrap(),
            ExactRamDamage {
                first: 1,
                second: 1,
            }
        );
        assert_eq!(
            exact_ram_damage(20.0, 99_000.0, 1_000.0, no_armor, no_armor, false, false).unwrap(),
            ExactRamDamage {
                first: 25,
                second: 2_475,
            }
        );
        assert_eq!(
            exact_ram_damage(
                f64::MAX,
                99_000.0,
                1_000.0,
                no_armor,
                no_armor,
                false,
                false,
            ),
            Err(RamError::DamageOutOfRange)
        );
    }

    #[test]
    fn duplicate_native_evidence_is_exact_or_a_typed_conflict() {
        let pair = (key(VehicleKind::Bot, 1), key(VehicleKind::Bot, 2));
        let original = evidence(pair.0, pair.1, 1_000_000, 1);
        let mut conflict = original;
        conflict.first = vehicle_evidence(1.0, 1.0, 0.0);
        let mut authority = RamAuthority::new();
        let bodies = [
            body(VehicleKind::Bot, 1, -1.0, 10.0, 20_000.0),
            body(VehicleKind::Bot, 2, 1.0, 0.0, 20_000.0),
        ];
        assert!(authority
            .resolve_frame_with_native_evidence(1_000_000, &bodies, &[original, original],)
            .is_ok());
        assert_eq!(
            authority
                .resolve_frame_with_native_evidence(1_000_000, &bodies, &[original, conflict],),
            Err(RamError::NativeEvidenceConflict {
                pair: RamPair::new(pair.0, pair.1).unwrap(),
                source_time_us: 1_000_000,
            })
        );
    }

    #[test]
    fn source_timeline_is_retry_safe_bracketed_and_kind_qualified() {
        let mut timeline = RamPoseTimeline::new();
        let player_left = pose_frame(VehicleKind::Player, 7, 1, 1, 1_000_000, 0.0);
        let player_right = pose_frame(VehicleKind::Player, 7, 1, 2, 2_000_000, 2.0);
        let bot_left = pose_frame(VehicleKind::Bot, 7, 1, 1, 1_000_000, 10.0);
        let bot_right = pose_frame(VehicleKind::Bot, 7, 1, 2, 2_000_000, 14.0);
        assert_eq!(
            timeline.record(player_left.clone()).unwrap(),
            RamPoseAdmission::New
        );
        assert_eq!(
            timeline.record(player_left.clone()).unwrap(),
            RamPoseAdmission::ExactRetry
        );
        timeline.record(player_right).unwrap();
        timeline.record(bot_left).unwrap();
        timeline.record(bot_right).unwrap();

        let cursor = RamSourceCursor::new(1, 2).unwrap();
        let player_query =
            RamPoseQuery::new(key(VehicleKind::Player, 7), cursor, 1_500_000).unwrap();
        let bot_query = RamPoseQuery::new(key(VehicleKind::Bot, 7), cursor, 1_500_000).unwrap();
        let RamPoseLookup::Found(player) = timeline.body_at(player_query).unwrap() else {
            panic!("the player bracket must resolve");
        };
        assert!((player.x - 1.0).abs() < 1.0e-9);
        assert!((player.vx - 2.0).abs() < 1.0e-9);
        let RamPosePairLookup::Found {
            pair,
            first,
            second,
        } = timeline.pair_at(player_query, bot_query).unwrap()
        else {
            panic!("same numeric player/bot ids must remain a valid pair");
        };
        assert_eq!(pair.first, key(VehicleKind::Player, 7));
        assert_eq!(pair.second, key(VehicleKind::Bot, 7));
        assert!((first.x - 1.0).abs() < 1.0e-9);
        assert!((second.x - 12.0).abs() < 1.0e-9);

        assert_eq!(
            timeline
                .body_at(
                    RamPoseQuery::new(key(VehicleKind::Player, 7), cursor, 2_500_000,).unwrap(),
                )
                .unwrap(),
            RamPoseLookup::Unavailable
        );
        assert_eq!(
            timeline
                .body_at(
                    RamPoseQuery::new(
                        key(VehicleKind::Player, 7),
                        RamSourceCursor::new(1, 3).unwrap(),
                        2_500_000,
                    )
                    .unwrap(),
                )
                .unwrap(),
            RamPoseLookup::Pending
        );

        let mut conflict = player_left;
        conflict.body.x = 3.0;
        assert!(matches!(
            timeline.record(conflict),
            Err(RamError::PoseFrameConflict { .. })
        ));
        assert_eq!(
            timeline.record(pose_frame(VehicleKind::Player, 7, 1, 3, 2_000_000, 3.0,)),
            Err(RamError::PoseTimelineRegression(key(
                VehicleKind::Player,
                7
            )))
        );
    }

    #[test]
    fn streaming_timeline_resets_only_the_pose_segment_and_keeps_retry_fences() {
        let mut timeline = RamPoseTimeline::new();
        let old = pose_frame(VehicleKind::Player, 7, 0, 1, 1_000_000, 100.0);
        assert_eq!(
            timeline.record_streaming(old.clone()).unwrap(),
            RamPoseAdmission::New
        );
        assert_eq!(
            timeline
                .record_streaming(pose_frame(VehicleKind::Player, 7, 0, 2, 900_000, 9.0,))
                .unwrap(),
            RamPoseAdmission::DiscontinuityReset
        );
        assert_eq!(
            timeline.record_streaming(old).unwrap(),
            RamPoseAdmission::ExactRetry
        );
        assert_eq!(
            timeline
                .record_streaming(pose_frame(VehicleKind::Player, 7, 0, 3, 1_100_000, 11.0,))
                .unwrap(),
            RamPoseAdmission::New
        );

        let RamPoseLookup::Found(reconstructed) = timeline
            .body_at_source_time(key(VehicleKind::Player, 7), 1_000_000)
            .unwrap()
        else {
            panic!("the new segment must provide the only interpolation bracket");
        };
        assert!((reconstructed.x - 10.0).abs() < 1.0e-9);
    }

    #[test]
    fn inverse_mass_response_separates_and_cancels_closing_velocity() {
        let mut authority = RamAuthority::new();
        let light = body(VehicleKind::Bot, 1, -1.0, 10.0, 10_000.0);
        let heavy = body(VehicleKind::Bot, 2, 1.0, 0.0, 40_000.0);
        let native = evidence(
            key(VehicleKind::Bot, 1),
            key(VehicleKind::Bot, 2),
            1_000_000,
            1,
        );
        let output = authority
            .resolve_frame_with_native_evidence(1_000_000, &[heavy, light], &[native])
            .unwrap();
        assert_eq!(output.contacts.len(), 1);
        let contact = &output.contacts[0];
        assert!(contact.first_delta.correction_x.abs() > contact.second_delta.correction_x.abs());
        assert!(
            (10_000.0 * contact.first_delta.correction_x
                + 40_000.0 * contact.second_delta.correction_x)
                .abs()
                < 1.0e-9
        );
        let relative_after =
            10.0 + contact.first_delta.velocity_x - contact.second_delta.velocity_x;
        assert!(relative_after.abs() < 1.0e-9);
        assert_eq!(contact.outcome, RamResolutionOutcome::Damage);
        assert_eq!(contact.damage.as_ref().unwrap().first.amount, 250);
        assert_eq!(contact.damage.as_ref().unwrap().second.amount, 62);
    }

    #[test]
    fn friendly_contact_keeps_physics_without_hp_or_native_probe() {
        let mut authority = RamAuthority::new();
        let mut first = body(VehicleKind::Bot, 1, -1.0, 10.0, 20_000.0);
        let mut second = body(VehicleKind::Bot, 2, 1.0, 0.0, 20_000.0);
        first.team = 1;
        second.team = 1;
        let native = evidence(first.key, second.key, 1_000_000, 1);

        assert!(authority
            .fixed_contact_probes(
                1_000_000,
                RamSourceCursor::new(1, 1).unwrap(),
                &[first.clone(), second.clone()],
            )
            .unwrap()
            .is_empty());
        let output = authority
            .resolve_frame_with_native_evidence(1_000_000, &[first, second], &[native])
            .unwrap();

        assert_eq!(output.contacts.len(), 1);
        let contact = &output.contacts[0];
        assert_eq!(contact.outcome, RamResolutionOutcome::Contact);
        assert!(contact.damage.is_none());
        assert_ne!(contact.first_delta.correction_x, 0.0);
        assert_ne!(contact.first_delta.velocity_x, 0.0);
    }

    #[test]
    fn friendly_player_receipt_uses_canonical_team_and_retires_without_probe() {
        let mut authority = RamAuthority::new();
        let mut player = body(VehicleKind::Player, 7, -1.0, 10.0, 10_000.0);
        let mut bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
        player.team = 1;
        bot.team = 1;
        authority
            .record_bot_frame(9, 900_000, std::slice::from_ref(&bot))
            .unwrap();
        authority
            .admit_player_receipts(7, &[receipt(1, 9, 900_000)])
            .unwrap();

        assert!(authority
            .player_contact_probes(&[player.clone(), bot.clone()])
            .unwrap()
            .is_empty());
        let prepared = authority.prepare_player_receipts(&[player, bot]).unwrap();

        assert_eq!(prepared.len(), 1);
        assert_eq!(prepared[0].outcome, RamResolutionOutcome::Contact);
        assert!(prepared[0].damage.is_none());
        authority.commit_player_resolutions(&prepared).unwrap();
        assert_eq!(authority.player_ledger_state(7).resolved_sequence, 1);
    }

    #[test]
    fn deep_overlap_probe_keeps_first_impact_face_not_separation_axis() {
        let authority = RamAuthority::new();
        let mut first = body(VehicleKind::Bot, 1, 0.0, 0.0, 25_000.0);
        let mut second = body(VehicleKind::Bot, 2, 0.0, 0.0, 30_000.0);
        first.vz = 10.0;
        second.z = 0.5;

        let separation = pair_contact(&first, &second).unwrap();
        assert_eq!((separation.normal_x, separation.normal_z), (1.0, 0.0));
        let probes = authority
            .fixed_contact_probes(
                1_000_000,
                RamSourceCursor::new(1, 1).unwrap(),
                &[first.clone(), second.clone()],
            )
            .unwrap();
        assert_eq!(probes.len(), 1);
        assert!((probes[0].normal_x - 0.0).abs() < 1.0e-9);
        assert!((probes[0].normal_z + 1.0).abs() < 1.0e-9);

        first.vz = -10.0;
        assert!(authority
            .fixed_contact_probes(
                1_000_001,
                RamSourceCursor::new(1, 2).unwrap(),
                &[first, second],
            )
            .unwrap()
            .is_empty());
    }

    #[test]
    fn active_contact_requires_only_physical_separation_before_rearming() {
        let mut authority = RamAuthority::new();
        let first = body(VehicleKind::Bot, 1, -1.0, 10.0, 20_000.0);
        let second = body(VehicleKind::Bot, 2, 1.0, 0.0, 20_000.0);
        let pair = (key(VehicleKind::Bot, 1), key(VehicleKind::Bot, 2));
        let first_hit = authority
            .resolve_frame_with_native_evidence(
                1_000_000,
                &[first.clone(), second.clone()],
                &[evidence(pair.0, pair.1, 1_000_000, 1)],
            )
            .unwrap();
        assert_eq!(first_hit.contacts[0].outcome, RamResolutionOutcome::Damage);
        let sustained = authority
            .resolve_frame_with_native_evidence(
                2_000_000,
                &[first.clone(), second.clone()],
                &[evidence(pair.0, pair.1, 2_000_000, 2)],
            )
            .unwrap();
        assert_eq!(
            sustained.contacts[0].outcome,
            RamResolutionOutcome::ActiveContact
        );

        let mut separated = second.clone();
        separated.x = 20.0;
        assert!(authority
            .resolve_frame(2_100_000, &[first.clone(), separated])
            .unwrap()
            .contacts
            .is_empty());
        let immediately_rearmed = authority
            .resolve_frame_with_native_evidence(
                2_100_001,
                &[first.clone(), second.clone()],
                &[evidence(pair.0, pair.1, 2_100_001, 3)],
            )
            .unwrap();
        assert_eq!(
            immediately_rearmed.contacts[0].outcome,
            RamResolutionOutcome::Damage
        );
    }

    #[test]
    fn missing_contact_armor_separates_without_consuming_a_later_evidenced_impact() {
        let mut authority = RamAuthority::new();
        let slow = body(VehicleKind::Bot, 1, -1.0, 1.0, 20_000.0);
        let target = body(VehicleKind::Bot, 2, 1.0, 0.0, 20_000.0);
        let harmless = authority
            .resolve_frame(1_000_000, &[slow.clone(), target.clone()])
            .unwrap();
        assert_eq!(harmless.contacts[0].outcome, RamResolutionOutcome::Contact);
        assert!(harmless.contacts[0].damage.is_none());
        assert_ne!(harmless.contacts[0].first_delta.correction_x, 0.0);
        assert!(authority.active_contacts().is_empty());
        let mut fast = slow;
        fast.vx = 10.0;
        let impact = authority
            .resolve_frame_with_native_evidence(
                1_010_000,
                &[fast, target],
                &[evidence(
                    key(VehicleKind::Bot, 1),
                    key(VehicleKind::Bot, 2),
                    1_010_000,
                    1,
                )],
            )
            .unwrap();
        assert_eq!(impact.contacts[0].outcome, RamResolutionOutcome::Damage);
    }

    #[test]
    fn vertical_separation_and_rotated_obb_fail_closed() {
        let mut authority = RamAuthority::new();
        let first = body(VehicleKind::Bot, 1, 0.0, 0.0, 20_000.0);
        let mut second = body(VehicleKind::Bot, 2, 0.0, 0.0, 20_000.0);
        second.y = 5.0;
        assert!(authority
            .resolve_frame(0, &[first.clone(), second])
            .unwrap()
            .contacts
            .is_empty());
        let mut rotated = body(VehicleKind::Bot, 2, 3.0, 0.0, 20_000.0);
        rotated.yaw = std::f64::consts::FRAC_PI_2;
        assert_eq!(
            authority
                .resolve_frame(1, &[first, rotated])
                .unwrap()
                .contacts
                .len(),
            1
        );
    }

    #[test]
    fn receipt_shape_is_strict() {
        assert!(PlayerRamReceipt::parse(&receipt(1, 1, 10)).is_ok());
        let mut extra = receipt(1, 1, 10);
        extra
            .as_object_mut()
            .unwrap()
            .insert("damage".to_owned(), json!(500));
        assert_eq!(
            PlayerRamReceipt::parse(&extra),
            Err(RamError::InvalidReceipt)
        );
        let mut fractional = receipt(1, 1, 10);
        fractional["seq"] = json!(1.5);
        assert_eq!(
            PlayerRamReceipt::parse(&fractional),
            Err(RamError::InvalidReceipt)
        );
        let mut unbounded = receipt(1, 1, 10);
        unbounded["vx"] = json!(201.0);
        assert_eq!(
            PlayerRamReceipt::parse(&unbounded),
            Err(RamError::InvalidReceipt)
        );
    }

    #[test]
    fn player_pair_receipt_is_fact_only_and_lower_id_owned() {
        let value = json!({
            "seq": 7,
            "target_player_id": 9,
            "presentation_time_us": 123_456,
        });
        let parsed = PlayerPairRamReceipt::parse_for_reporter(4, &value).unwrap();
        assert_eq!(parsed.sequence, 7);
        assert_eq!(parsed.target_player_id, 9);
        assert_eq!(parsed.presentation_time_us, 123_456);
        assert_eq!(parsed.to_value(), value);

        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(9, &value),
            Err(RamError::InvalidReceipt)
        );
        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(10, &value),
            Err(RamError::InvalidReceipt)
        );
        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(0, &value),
            Err(RamError::InvalidPlayer)
        );
    }

    #[test]
    fn player_pair_receipt_rejects_verdicts_and_unbounded_wire_values() {
        let mut verdict = json!({
            "seq": 1,
            "target_player_id": 2,
            "presentation_time_us": 1,
        });
        verdict["damage"] = json!(500);
        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(1, &verdict),
            Err(RamError::InvalidReceipt)
        );

        let fractional = json!({
            "seq": 1.5,
            "target_player_id": 2,
            "presentation_time_us": 1,
        });
        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(1, &fractional),
            Err(RamError::InvalidReceipt)
        );

        let unbounded = json!({
            "seq": 1,
            "target_player_id": 2,
            "presentation_time_us": MAX_EXACT_INT + 1,
        });
        assert_eq!(
            PlayerPairRamReceipt::parse_for_reporter(1, &unbounded),
            Err(RamError::InvalidReceipt)
        );
    }

    #[test]
    fn discontinuity_closes_only_unfrozen_player_pair_history() {
        let mut authority = RamAuthority::new();
        let pair_receipt = |sequence, presentation_time_us| {
            json!({
                "seq": sequence,
                "target_player_id": 2,
                "presentation_time_us": presentation_time_us,
            })
        };
        authority
            .admit_player_pair_receipts(
                1,
                &[pair_receipt(1, 1_000_000), pair_receipt(2, 1_100_000)],
            )
            .unwrap();

        let closed = authority
            .invalidate_unfrozen_player_pair_history(2, &BTreeSet::from([(1, 2)]))
            .unwrap();
        assert_eq!(closed.len(), 1);
        assert_eq!(
            closed[0].outcome,
            RamResolutionOutcome::HistoricalStateUnavailable
        );
        assert!(matches!(
            closed[0].source,
            RamResolutionSource::PlayerPairReceipt {
                reporter_player_id: 1,
                sequence: 1,
                target_player_id: 2,
                ..
            }
        ));
        assert_eq!(
            authority.player_pair_ledger_state(1),
            PlayerRamLedgerState {
                admitted_sequence: 2,
                resolved_sequence: 1,
                pending: 1,
            }
        );
        assert_eq!(
            authority
                .admit_player_pair_receipts(1, &[pair_receipt(3, 1_200_000)])
                .unwrap(),
            ReceiptAdmission::New {
                admitted_sequence: 3,
                count: 1,
            }
        );
        assert_eq!(authority.player_pair_ledger_state(1).pending, 2);
    }

    #[test]
    fn receipt_ledger_is_contiguous_bounded_and_retry_safe() {
        let mut authority = RamAuthority::new();
        assert_eq!(
            authority
                .validate_player_receipts(7, &[receipt(1, 1, 10)])
                .unwrap(),
            ReceiptAdmission::New {
                admitted_sequence: 1,
                count: 1
            }
        );
        assert_eq!(
            authority.player_ledger_state(7),
            PlayerRamLedgerState::default()
        );
        assert_eq!(
            authority
                .admit_player_receipts(7, &[receipt(1, 1, 10)])
                .unwrap(),
            ReceiptAdmission::New {
                admitted_sequence: 1,
                count: 1
            }
        );
        assert_eq!(
            authority
                .admit_player_receipts(7, &[receipt(1, 1, 10)])
                .unwrap(),
            ReceiptAdmission::ExactRetry {
                admitted_sequence: 1
            }
        );
        let mut conflict = receipt(1, 1, 10);
        conflict["vx"] = json!(11.0);
        assert_eq!(
            authority.admit_player_receipts(7, &[conflict]),
            Err(RamError::ReceiptConflict(1))
        );
        assert!(matches!(
            authority.admit_player_receipts(7, &[receipt(3, 3, 30)]),
            Err(RamError::ReceiptSequenceGap {
                expected_after: 1,
                actual: 3
            })
        ));
        let rest = (2..=MAX_PENDING_RAM_CONTACTS as u64)
            .map(|sequence| receipt(sequence, sequence, sequence * 10))
            .collect::<Vec<_>>();
        authority.admit_player_receipts(7, &rest).unwrap();
        assert_eq!(
            authority.player_ledger_state(7).pending,
            MAX_PENDING_RAM_CONTACTS
        );
        assert_eq!(
            authority.admit_player_receipts(7, &[receipt(17, 17, 170)]),
            Err(RamError::PendingLimit)
        );
        let projection = authority.player_projection(7);
        assert_eq!(projection.admitted_sequence, 16);
        assert_eq!(projection.resolved_sequence, 0);
        assert_eq!(projection.contacts.len(), MAX_PENDING_RAM_CONTACTS);
        assert_eq!(projection.contacts[0], receipt(1, 1, 10));
    }

    #[test]
    fn receipt_timeline_cannot_move_backwards() {
        let mut authority = RamAuthority::new();
        authority
            .admit_player_receipts(7, &[receipt(1, 5, 50)])
            .unwrap();
        assert_eq!(
            authority.admit_player_receipts(7, &[receipt(2, 4, 60)]),
            Err(RamError::ReceiptTimelineRegression(2))
        );
        assert_eq!(authority.player_ledger_state(7).admitted_sequence, 1);
    }

    #[test]
    fn history_retry_is_exact_and_history_is_bounded() {
        let mut authority = RamAuthority::new();
        let bot = body(VehicleKind::Bot, 1, 0.0, 0.0, 20_000.0);
        assert_eq!(
            authority
                .record_bot_frame(1, 10, std::slice::from_ref(&bot))
                .unwrap(),
            HistoryAdmission::New
        );
        assert_eq!(
            authority
                .record_bot_frame(1, 10, std::slice::from_ref(&bot))
                .unwrap(),
            HistoryAdmission::ExactRetry
        );
        let mut changed = bot.clone();
        changed.x = 1.0;
        assert_eq!(
            authority.record_bot_frame(1, 10, &[changed]),
            Err(RamError::HistoricalFrameConflict)
        );
        for revision in 2..=(MAX_BOT_HISTORY_FRAMES as u64 + 2) {
            authority
                .record_bot_frame(revision, revision * 10, std::slice::from_ref(&bot))
                .unwrap();
        }
        assert_eq!(authority.bot_history.len(), MAX_BOT_HISTORY_FRAMES);
        assert!(!authority.bot_history.contains_key(&1));
    }

    #[test]
    fn historical_bot_pose_is_interpolated_inside_the_receipt_revision_fence() {
        let mut authority = RamAuthority::new();
        let left = body(VehicleKind::Bot, 1, 0.0, 0.0, 40_000.0);
        let mut right = left.clone();
        right.x = 2.0;
        right.yaw = 0.2;
        authority.record_bot_frame(8, 1_000_000, &[left]).unwrap();
        authority.record_bot_frame(9, 2_000_000, &[right]).unwrap();
        let interpolated = authority.historical_body_at(1, 9, 1_500_000);
        let HistoricalBodyLookup::Found(interpolated) = interpolated else {
            panic!("the retained bracket should resolve");
        };
        assert!((interpolated.x - 1.0).abs() < 1.0e-9);
        assert!((interpolated.yaw - 0.1).abs() < 1.0e-9);
        assert!((interpolated.vx - 2.0).abs() < 1.0e-9);
        assert_eq!(
            authority.historical_body_at(1, 10, 1_500_000),
            HistoricalBodyLookup::Unavailable
        );
        assert_eq!(
            authority.historical_body_at(1, 10, 3_000_000),
            HistoricalBodyLookup::Pending
        );
    }

    #[test]
    fn player_receipt_uses_exact_canonical_history_and_two_phase_commit() {
        let mut authority = RamAuthority::new();
        let historical_bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
        authority
            .record_bot_frame(9, 900_000, std::slice::from_ref(&historical_bot))
            .unwrap();
        authority
            .admit_player_receipts(7, &[receipt(1, 9, 900_000)])
            .unwrap();

        let player = body(VehicleKind::Player, 7, 100.0, 0.0, 10_000.0);
        let current_bot = body(VehicleKind::Bot, 1, 100.0, 0.0, 40_000.0);
        let native = evidence(
            key(VehicleKind::Player, 7),
            key(VehicleKind::Bot, 1),
            900_000,
            9,
        );
        let first = authority
            .prepare_player_receipts_with_native_evidence(
                &[player.clone(), current_bot.clone()],
                &[native],
            )
            .unwrap();
        assert_eq!(first.len(), 1);
        let prepared = &first[0];
        assert_eq!(prepared.outcome, RamResolutionOutcome::Damage);
        assert_eq!(
            prepared.damage.as_ref().unwrap().operation_id,
            "ram:player:7:contact:1"
        );
        let bot_delta = prepared
            .deltas()
            .find(|delta| delta.key == key(VehicleKind::Bot, 1))
            .unwrap();
        let player_delta = prepared
            .deltas()
            .find(|delta| delta.key == key(VehicleKind::Player, 7))
            .unwrap();
        assert_ne!(bot_delta.velocity_x, 0.0);
        assert_eq!(
            *player_delta,
            RamBodyDelta::zero(key(VehicleKind::Player, 7))
        );

        let retry = authority
            .prepare_player_receipts(&[player, current_bot])
            .unwrap();
        assert_eq!(retry, first);
        assert_eq!(
            authority.validate_player_resolutions(&first).unwrap(),
            vec![ResolutionCommit::New]
        );
        assert_eq!(authority.player_ledger_state(7).resolved_sequence, 0);
        assert_eq!(
            authority.commit_player_resolutions(&first).unwrap(),
            vec![ResolutionCommit::New]
        );
        assert_eq!(
            authority.commit_player_resolution(prepared).unwrap(),
            ResolutionCommit::ExactRetry
        );
        assert_eq!(
            authority.player_ledger_state(7),
            PlayerRamLedgerState {
                admitted_sequence: 1,
                resolved_sequence: 1,
                pending: 0,
            }
        );
        assert_eq!(
            authority.player_projection(7).results,
            vec![json!({"seq": 1, "outcome": "damage"})]
        );
    }

    #[test]
    fn future_history_waits_but_missing_old_history_resolves_without_damage() {
        let mut authority = RamAuthority::new();
        authority
            .admit_player_receipts(7, &[receipt(1, 5, 500)])
            .unwrap();
        let player = body(VehicleKind::Player, 7, -1.0, 10.0, 10_000.0);
        let bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
        assert!(authority
            .prepare_player_receipts(&[player.clone(), bot.clone()])
            .unwrap()
            .is_empty());
        authority
            .record_bot_frame(6, 600, std::slice::from_ref(&bot))
            .unwrap();
        let result = authority.prepare_player_receipts(&[player, bot]).unwrap();
        assert_eq!(
            result[0].outcome,
            RamResolutionOutcome::HistoricalStateUnavailable
        );
        assert!(result[0].damage.is_none());
        authority.commit_player_resolution(&result[0]).unwrap();
        assert_eq!(authority.player_ledger_state(7).resolved_sequence, 1);
    }

    #[test]
    fn distinct_player_receipts_are_not_suppressed_by_a_time_cooldown() {
        let mut authority = RamAuthority::new();
        let bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
        authority
            .record_bot_frame(1, 1_000_000, std::slice::from_ref(&bot))
            .unwrap();
        authority
            .record_bot_frame(2, 1_500_000, std::slice::from_ref(&bot))
            .unwrap();
        authority
            .admit_player_receipts(7, &[receipt(1, 1, 1_000_000), receipt(2, 2, 1_500_000)])
            .unwrap();
        let player = body(VehicleKind::Player, 7, -1.0, 10.0, 10_000.0);
        let current_bot = bot;
        let pair = (key(VehicleKind::Player, 7), key(VehicleKind::Bot, 1));
        let first_evidence = evidence(pair.0, pair.1, 1_000_000, 1);
        let mut second_evidence = evidence(pair.0, pair.1, 1_500_000, 2);
        second_evidence.cursor = RamSourceCursor::new(2, 2).unwrap();
        let native = [first_evidence, second_evidence];
        let first = authority
            .prepare_player_receipts_with_native_evidence(
                &[player.clone(), current_bot.clone()],
                &native,
            )
            .unwrap();
        assert_eq!(first[0].outcome, RamResolutionOutcome::Damage);
        authority.commit_player_resolution(&first[0]).unwrap();
        let second = authority
            .prepare_player_receipts_with_native_evidence(&[player, current_bot], &native)
            .unwrap();
        assert_eq!(second[0].outcome, RamResolutionOutcome::Damage);
        assert!(second[0].damage.is_some());
    }

    #[test]
    fn every_pending_player_contact_gets_one_source_response_before_ordered_hp() {
        let mut authority = RamAuthority::new();
        let first_bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
        let mut second_bot = body(VehicleKind::Bot, 2, 1.0, 0.0, 40_000.0);
        second_bot.team = 2;
        second_bot.z = 0.25;
        authority
            .record_bot_frame(1, 1_000_000, &[first_bot.clone(), second_bot.clone()])
            .unwrap();
        let first = receipt(1, 1, 1_000_000);
        let mut second = receipt(2, 1, 1_000_000);
        second["bot_id"] = json!(2);
        authority
            .admit_player_receipts(7, &[first, second])
            .unwrap();
        let player = body(VehicleKind::Player, 7, -1.0, 10.0, 10_000.0);
        let current = [player, first_bot, second_bot];

        let probes = authority.player_contact_probes(&current).unwrap();
        assert_eq!(probes.len(), 2);
        for probe in &probes {
            authority
                .mark_player_contact_response_applied(probe)
                .unwrap();
        }
        assert!(authority
            .player_contact_probes(&current)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn player_receipt_contact_point_slop_is_bounded_to_the_reporter_body() {
        let fixture = |contact_x: f64| {
            let mut authority = RamAuthority::new();
            let player = body(VehicleKind::Player, 7, -1.0, 10.0, 10_000.0);
            let bot = body(VehicleKind::Bot, 1, 1.0, 0.0, 40_000.0);
            authority
                .record_bot_frame(1, 1_000_000, std::slice::from_ref(&bot))
                .unwrap();
            let mut value = receipt(1, 1, 1_000_000);
            value["contact_x"] = json!(contact_x);
            authority.admit_player_receipts(7, &[value]).unwrap();
            (authority, player, bot)
        };

        // The copied player body ends at x=0.5. One-frame skew less than the
        // 0.75 m allowance still produces the exact native probe.
        let (mut within, player, bot) = fixture(1.24);
        let probe = within
            .player_contact_probes(&[player.clone(), bot.clone()])
            .unwrap()
            .remove(0);
        let native = NativeRamContactEvidence::new(
            probe.pair,
            probe.cursor,
            probe.source_time_us,
            vehicle_evidence(0.0, 1.0, 0.0),
            vehicle_evidence(0.0, 1.0, 0.0),
            probe.first_moving,
            probe.second_moving,
        )
        .unwrap();
        let prepared = within
            .prepare_player_receipts_with_native_evidence(&[player, bot], &[native])
            .unwrap();
        assert_eq!(prepared.len(), 1);
        assert_eq!(prepared[0].outcome, RamResolutionOutcome::Damage);

        let (outside, player, bot) = fixture(1.26);
        assert!(outside
            .player_contact_probes(&[player, bot])
            .unwrap()
            .is_empty());

        // The canonical Bot's left edge is x=-0.5. Do not apply the player
        // callback allowance to Bot history or remote native evidence.
        let (bot_outside, player, bot) = fixture(-0.51);
        assert!(bot_outside
            .player_contact_probes(&[player, bot])
            .unwrap()
            .is_empty());
    }

    #[test]
    fn vertical_player_velocity_is_frozen_for_controlled_impact_moving_flag() {
        let mut authority = RamAuthority::new();
        let bot = body(VehicleKind::Bot, 1, 1.0, -10.0, 40_000.0);
        authority
            .record_bot_frame(1, 1_000_000, std::slice::from_ref(&bot))
            .unwrap();
        let mut vertical = receipt(1, 1, 1_000_000);
        vertical["vx"] = json!(0.0);
        vertical["vy"] = json!(5.0);
        vertical["vz"] = json!(0.0);
        authority.admit_player_receipts(7, &[vertical]).unwrap();
        let player = body(VehicleKind::Player, 7, -1.0, 0.0, 10_000.0);
        let probe = authority
            .player_contact_probes(&[player, bot])
            .unwrap()
            .remove(0);
        let player_moving = if probe.pair.first.kind == VehicleKind::Player {
            probe.first_moving
        } else {
            probe.second_moving
        };
        assert!(player_moving);
    }
}
