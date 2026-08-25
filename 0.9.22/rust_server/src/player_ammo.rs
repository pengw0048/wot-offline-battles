//! Deterministic player ammunition and reload authority.
//!
//! The battle loop owns one ledger per player. Canonical input admission feeds
//! every input sequence into [`PlayerAmmoLedger::admit_input`], while a newly
//! admitted projectile launch is applied through [`PlayerAmmoLedger::admit_launch`].
//! Both ledgers retain a bounded exact-retry window and reject gaps or conflicting
//! reuse without changing ammunition.

use crate::bot_sim::{fixed_dt_us, time_us_at_tick, AmmoState, BotProfile, VehicleDescriptor};
use crate::combat::MAX_COMBAT_ID;
use crate::input::{MAX_INPUT_FINGERPRINTS, MAX_INPUT_SEQUENCE};
use std::collections::BTreeMap;
use thiserror::Error;

pub const MAX_PLAYER_AMMO_RECEIPTS: usize = MAX_INPUT_FINGERPRINTS;
pub const MAX_PHYSICAL_BURST_COUNT: u16 = 64;
pub const MAX_PHYSICAL_BURST_INTERVAL_SECONDS: f64 = 10.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PhysicalBurstDescriptor {
    pub count: u16,
    pub interval_seconds: f64,
}

impl PhysicalBurstDescriptor {
    pub fn new(count: u16, interval_seconds: f64) -> Result<Self, PhysicalBurstError> {
        if count == 0
            || count > MAX_PHYSICAL_BURST_COUNT
            || !interval_seconds.is_finite()
            || !(0.0..=MAX_PHYSICAL_BURST_INTERVAL_SECONDS).contains(&interval_seconds)
            || (count > 1 && interval_seconds <= 0.0)
        {
            return Err(PhysicalBurstError::InvalidDescriptor);
        }
        Ok(Self {
            count,
            interval_seconds: if count == 1 { 0.0 } else { interval_seconds },
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PhysicalBurstEdge {
    pub shot_seq: u64,
    pub burst_group_seq: u64,
    pub burst_index: u16,
    pub burst_count: u16,
    pub shell_index: u8,
    pub due_time_us: u64,
}

impl PhysicalBurstEdge {
    pub fn final_round(self) -> bool {
        self.burst_index.saturating_add(1) == self.burst_count
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum PhysicalBurstAdmission {
    New { first: PhysicalBurstEdge },
    ExactRetry { first: PhysicalBurstEdge },
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum PhysicalBurstError {
    #[error("physical burst descriptor is invalid")]
    InvalidDescriptor,
    #[error("physical burst identity or ammunition bounds are invalid")]
    InvalidTrigger,
    #[error("another physical burst is already active")]
    AlreadyActive,
    #[error("physical burst group sequence does not follow the last physical shell")]
    SequenceGap,
    #[error("physical burst identity was reused with different content")]
    ConflictingRetry,
    #[error("fixed tick does not follow the physical burst clock")]
    TickSequence,
    #[error("physical burst time or shot sequence is exhausted")]
    SequenceExhausted,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct ActivePhysicalBurst {
    group_seq: u64,
    count: u16,
    next_index: u16,
    interval_us: u64,
    next_due_time_us: u64,
    shell_index: u8,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct PhysicalBurstReceipt {
    descriptor: PhysicalBurstDescriptor,
    ammunition: u16,
    loaded_clip: u16,
    shell_index: u8,
    start_tick: u64,
    first: PhysicalBurstEdge,
}

/// One deterministic fixed-tick physical-burst schedule for a shooter.
///
/// The first shell is due at the trigger boundary. Later shells retain their
/// logical microsecond cadence even when one 30 Hz tick releases several
/// overdue edges. The type is cloneable so callers can stage it with the ammo
/// and projectile ledgers and publish the clone only after every edge commits.
#[derive(Clone, Debug)]
pub struct PhysicalBurstClock {
    current_tick: u64,
    last_shot_seq: u64,
    active: Option<ActivePhysicalBurst>,
    receipts: BTreeMap<u64, PhysicalBurstReceipt>,
}

impl PhysicalBurstClock {
    pub fn new(current_tick: u64, last_shot_seq: u64) -> Self {
        Self {
            current_tick,
            last_shot_seq,
            active: None,
            receipts: BTreeMap::new(),
        }
    }

    pub fn current_tick(&self) -> u64 {
        self.current_tick
    }

    pub fn last_shot_seq(&self) -> u64 {
        self.last_shot_seq
    }

    pub fn active(&self) -> bool {
        self.active.is_some()
    }

    pub fn arm(
        &mut self,
        group_seq: u64,
        shell_index: u8,
        descriptor: PhysicalBurstDescriptor,
        ammunition: u16,
        loaded_clip: u16,
        start_tick: u64,
    ) -> Result<PhysicalBurstAdmission, PhysicalBurstError> {
        let descriptor =
            PhysicalBurstDescriptor::new(descriptor.count, descriptor.interval_seconds)?;
        if let Some(receipt) = self.receipts.get(&group_seq) {
            return if receipt.descriptor == descriptor
                && receipt.ammunition == ammunition
                && receipt.loaded_clip == loaded_clip
                && receipt.shell_index == shell_index
                && receipt.start_tick == start_tick
            {
                Ok(PhysicalBurstAdmission::ExactRetry {
                    first: receipt.first,
                })
            } else {
                Err(PhysicalBurstError::ConflictingRetry)
            };
        }
        if self.active.is_some() {
            return Err(PhysicalBurstError::AlreadyActive);
        }
        if group_seq == 0
            || group_seq > MAX_COMBAT_ID
            || start_tick != self.current_tick
            || ammunition == 0
            || loaded_clip == 0
        {
            return Err(PhysicalBurstError::InvalidTrigger);
        }
        if group_seq != self.last_shot_seq.saturating_add(1) {
            return Err(PhysicalBurstError::SequenceGap);
        }
        let count = descriptor.count.min(ammunition).min(loaded_clip);
        if count == 0
            || group_seq
                .checked_add(u64::from(count - 1))
                .is_none_or(|last| last > MAX_COMBAT_ID)
        {
            return Err(PhysicalBurstError::InvalidTrigger);
        }
        let due_time_us = time_us_at_tick(start_tick);
        let first = PhysicalBurstEdge {
            shot_seq: group_seq,
            burst_group_seq: group_seq,
            burst_index: 0,
            burst_count: count,
            shell_index,
            due_time_us,
        };
        let interval_us = (count > 1)
            .then(|| seconds_to_micros(descriptor.interval_seconds))
            .transpose()?;
        let next_active = if let Some(interval_us) = interval_us {
            Some(ActivePhysicalBurst {
                group_seq,
                count,
                next_index: 1,
                interval_us,
                next_due_time_us: due_time_us
                    .checked_add(interval_us)
                    .ok_or(PhysicalBurstError::SequenceExhausted)?,
                shell_index,
            })
        } else {
            None
        };
        self.last_shot_seq = group_seq;
        self.active = next_active;
        self.receipts.insert(
            group_seq,
            PhysicalBurstReceipt {
                descriptor,
                ammunition,
                loaded_clip,
                shell_index,
                start_tick,
                first,
            },
        );
        prune_receipts(&mut self.receipts);
        Ok(PhysicalBurstAdmission::New { first })
    }

    /// Atomically release every physical edge due through `tick`.
    pub fn advance_tick(
        &mut self,
        tick: u64,
    ) -> Result<Vec<PhysicalBurstEdge>, PhysicalBurstError> {
        let expected_tick = self
            .current_tick
            .checked_add(1)
            .ok_or(PhysicalBurstError::SequenceExhausted)?;
        if tick != expected_tick {
            return Err(PhysicalBurstError::TickSequence);
        }
        let boundary_us = time_us_at_tick(tick);
        let mut staged = self.active;
        let mut last_shot_seq = self.last_shot_seq;
        let mut due = Vec::new();
        while let Some(mut active) = staged {
            if active.next_due_time_us > boundary_us {
                staged = Some(active);
                break;
            }
            let shot_seq = active
                .group_seq
                .checked_add(u64::from(active.next_index))
                .ok_or(PhysicalBurstError::SequenceExhausted)?;
            if shot_seq != last_shot_seq.saturating_add(1) || shot_seq > MAX_COMBAT_ID {
                return Err(PhysicalBurstError::SequenceExhausted);
            }
            due.push(PhysicalBurstEdge {
                shot_seq,
                burst_group_seq: active.group_seq,
                burst_index: active.next_index,
                burst_count: active.count,
                shell_index: active.shell_index,
                due_time_us: active.next_due_time_us,
            });
            last_shot_seq = shot_seq;
            active.next_index += 1;
            if active.next_index >= active.count {
                staged = None;
            } else {
                active.next_due_time_us = active
                    .next_due_time_us
                    .checked_add(active.interval_us)
                    .ok_or(PhysicalBurstError::SequenceExhausted)?;
                staged = Some(active);
            }
        }
        self.active = staged;
        self.last_shot_seq = last_shot_seq;
        self.current_tick = tick;
        Ok(due)
    }

    pub fn cancel(&mut self) -> bool {
        self.active.take().is_some()
    }
}

fn seconds_to_micros(seconds: f64) -> Result<u64, PhysicalBurstError> {
    let micros = seconds * 1_000_000.0;
    let rounded = micros.round();
    if !rounded.is_finite() || rounded < 1.0 || rounded > u64::MAX as f64 {
        return Err(PhysicalBurstError::InvalidDescriptor);
    }
    Ok(rounded as u64)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerAmmoInput {
    pub input_seq: u64,
    pub shell_index: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerAmmoLaunch {
    pub shot_seq: u64,
    pub input_seq: u64,
    pub shell_index: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerAmmoBurst {
    pub group_seq: u64,
    pub index: u16,
    pub count: u16,
}

impl PlayerAmmoBurst {
    pub fn ordinary(shot_seq: u64) -> Self {
        Self {
            group_seq: shot_seq,
            index: 0,
            count: 1,
        }
    }

    pub fn from_edge(edge: PhysicalBurstEdge) -> Self {
        Self {
            group_seq: edge.burst_group_seq,
            index: edge.burst_index,
            count: edge.burst_count,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerAmmoInputAdmission {
    New,
    ExactRetry,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerAmmoIntentAction {
    SelectCurrent { shell_index: u8 },
    SelectNext { shell_index: u8 },
    ReloadPartialClip,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerAmmoIntent {
    pub intent_seq: u64,
    pub input_seq: u64,
    pub action: PlayerAmmoIntentAction,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerAmmoIntentOutcome {
    Unchanged,
    Queued { shell_index: u8 },
    Reloading { shell_index: u8 },
    IntuitionLoaded { shell_index: u8 },
}

impl PlayerAmmoIntentOutcome {
    pub fn wire_kind(self) -> &'static str {
        match self {
            Self::Unchanged => "unchanged",
            Self::Queued { .. } => "queued",
            Self::Reloading { .. } => "reloading",
            Self::IntuitionLoaded { .. } => "intuition_loaded",
        }
    }

    pub fn shell_index(self) -> Option<u8> {
        match self {
            Self::Unchanged => None,
            Self::Queued { shell_index }
            | Self::Reloading { shell_index }
            | Self::IntuitionLoaded { shell_index } => Some(shell_index),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerAmmoIntentAdmission {
    New(PlayerAmmoIntentOutcome),
    ExactRetry(PlayerAmmoIntentOutcome),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerAmmoLaunchAdmission {
    New,
    ExactRetry,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PlayerAmmoSnapshot {
    pub player_id: u64,
    pub tick: u64,
    pub revision: u64,
    pub last_input_seq: u64,
    pub last_intent_seq: u64,
    pub last_shot_seq: u64,
    pub requested_shell: u8,
    pub loaded_shell: u8,
    pub next_shell: u8,
    pub remaining: Vec<u16>,
    pub reload_pending: bool,
    pub clip_size: u16,
    pub clip_remaining: u16,
    pub reload_factor: f64,
    pub reload_duration_seconds: f64,
    pub reload_remaining_seconds: f64,
    pub reload_ready: bool,
    pub can_fire: bool,
    pub burst_active: bool,
    pub burst_group_seq: u64,
    pub burst_count: u16,
    pub burst_next_index: u16,
    pub burst_shell_index: u8,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum PlayerAmmoError {
    #[error("player id must be in 1..=2147483647")]
    InvalidPlayerId,
    #[error("player ammunition descriptor is invalid")]
    InvalidDescriptor,
    #[error("exact player ammunition inventory is invalid")]
    InvalidExactInventory,
    #[error("input_seq must be in 1..=2147483647")]
    InvalidInputSequence,
    #[error("input sequence {received} does not follow {last}")]
    InputSequenceGap { last: u64, received: u64 },
    #[error("input sequence {sequence} fell outside the exact-retry window")]
    StaleInputRetry { sequence: u64 },
    #[error("ammo intent sequence must be in 1..=2147483647")]
    InvalidIntentSequence,
    #[error("ammo intent sequence {received} does not follow {last}")]
    IntentSequenceGap { last: u64, received: u64 },
    #[error("ammo intent sequence {sequence} fell outside the exact-retry window")]
    StaleIntentRetry { sequence: u64 },
    #[error("ammo intent sequence {sequence} was reused with different content")]
    ConflictingIntentRetry { sequence: u64 },
    #[error("ammo intent is not bound to the latest canonical input")]
    IntentInputBinding,
    #[error("loader Intuition chance count must be in 0..=16")]
    InvalidIntuitionChances,
    #[error("shell index {shell_index} is outside the donated descriptor")]
    InvalidShell { shell_index: u8 },
    #[error("shot_seq must be in 1..=2147483647")]
    InvalidShotSequence,
    #[error("shot sequence {received} does not follow {last}")]
    ShotSequenceGap { last: u64, received: u64 },
    #[error("shot sequence {sequence} fell outside the exact-retry window")]
    StaleLaunchRetry { sequence: u64 },
    #[error("shot sequence {sequence} was reused with different launch content")]
    ConflictingLaunchRetry { sequence: u64 },
    #[error("launch references unknown input sequence {input_seq}")]
    UnknownLaunchInput { input_seq: u64 },
    #[error("launch shell does not match input sequence {input_seq}")]
    LaunchInputBinding { input_seq: u64 },
    #[error("requested shell {requested} is not loaded; canonical loaded shell is {loaded}")]
    ShellNotLoaded { requested: u8, loaded: u8 },
    #[error("canonical gun reload has not completed")]
    Reloading,
    #[error("canonical loaded ammunition is exhausted")]
    OutOfAmmo,
    #[error("physical burst metadata is invalid")]
    InvalidBurst,
    #[error("another physical burst is already active")]
    BurstAlreadyActive,
    #[error("physical burst continuation does not match its armed group")]
    BurstBinding,
    #[error("reload factor must be finite and non-negative")]
    InvalidReloadFactor,
    #[error("fixed tick {received} does not follow {last}")]
    TickSequence { last: u64, received: u64 },
    #[error("fixed tick sequence is exhausted")]
    TickSequenceExhausted,
    #[error("player ammunition revision is exhausted")]
    RevisionExhausted,
    #[error("gun and ammunition state could not commit a launch atomically")]
    AtomicLaunchInvariant,
}

/// Resolve the #1513 Loader Intuition law from server-owned lineage.
///
/// Each finished loader perk owns one independent 17% lane. The result is a
/// pure function of the round authority and ordered intent identity, so an
/// exact retry after reconnect or replay cannot reroll the action.
pub fn deterministic_intuition_success(
    round_id: u64,
    authority_epoch: u64,
    player_id: u64,
    intent_seq: u64,
    chances: u8,
) -> Result<bool, PlayerAmmoError> {
    if !(1..=MAX_COMBAT_ID).contains(&player_id) {
        return Err(PlayerAmmoError::InvalidPlayerId);
    }
    if !(1..=MAX_COMBAT_ID).contains(&intent_seq) {
        return Err(PlayerAmmoError::InvalidIntentSequence);
    }
    if chances > 16 {
        return Err(PlayerAmmoError::InvalidIntuitionChances);
    }
    for lane in 0..chances {
        let mut value = round_id
            ^ authority_epoch.rotate_left(11)
            ^ player_id.rotate_left(23)
            ^ intent_seq.rotate_left(37)
            ^ u64::from(lane).rotate_left(47)
            ^ 0x6a09_e667_f3bc_c909;
        value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        value ^= value >> 31;
        let unit = ((value >> 11) as f64) * (1.0 / ((1_u64 << 53) as f64));
        if unit < 0.17 {
            return Ok(true);
        }
    }
    Ok(false)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ReloadKind {
    Full,
    Intra,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ActiveAmmoBurst {
    group_seq: u64,
    count: u16,
    next_index: u16,
    shell_index: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PlayerAmmoLaunchReceipt {
    launch: PlayerAmmoLaunch,
    burst: PlayerAmmoBurst,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PlayerAmmoIntentReceipt {
    intent: PlayerAmmoIntent,
    intuition_success: bool,
    outcome: PlayerAmmoIntentOutcome,
}

/// Server-owned ammunition state for one player.
///
/// The type is cloneable so a caller can stage it alongside a cloned battle
/// transaction and publish both only after every admission succeeds.
#[derive(Clone, Debug)]
pub struct PlayerAmmoLedger {
    player_id: u64,
    shell_count: usize,
    tick: u64,
    revision: u64,
    last_input_seq: u64,
    last_intent_seq: u64,
    last_shot_seq: u64,
    requested_shell: u8,
    intuition_chances: u8,
    reload_factor: f64,
    standard_shells: Vec<bool>,
    remaining: Vec<u16>,
    loaded_shell: u8,
    next_shell: u8,
    reload_pending: bool,
    plan_pending: bool,
    clip_size: u16,
    clip_remaining: u16,
    reload_full_seconds: f64,
    reload_intra_seconds: f64,
    reload_kind: ReloadKind,
    reload_elapsed_seconds: f64,
    active_burst: Option<ActiveAmmoBurst>,
    input_receipts: BTreeMap<u64, PlayerAmmoInput>,
    intent_receipts: BTreeMap<u64, PlayerAmmoIntentReceipt>,
    launch_receipts: BTreeMap<u64, PlayerAmmoLaunchReceipt>,
}

impl PlayerAmmoLedger {
    pub fn new(
        player_id: u64,
        start_tick: u64,
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
    ) -> Result<Self, PlayerAmmoError> {
        let shell_count = Self::validate_initial_state(player_id, descriptor)?;
        let ammo = AmmoState::new(descriptor, profile);
        Self::from_ammo(
            player_id,
            start_tick,
            descriptor,
            profile,
            shell_count,
            ammo,
        )
    }

    /// Initialize from the exact server-admitted player loadout.
    ///
    /// This constructor does not call the synthetic bot ammunition allocator.
    pub fn new_exact(
        player_id: u64,
        start_tick: u64,
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        remaining: Vec<u16>,
    ) -> Result<Self, PlayerAmmoError> {
        let shell_count = Self::validate_initial_state(player_id, descriptor)?;
        let ammo = AmmoState::new_exact(descriptor, profile, remaining)
            .map_err(|_| PlayerAmmoError::InvalidExactInventory)?;
        Self::from_ammo(
            player_id,
            start_tick,
            descriptor,
            profile,
            shell_count,
            ammo,
        )
    }

    /// Initialize from the exact loadout and exact garage-loaded shell.
    pub fn new_exact_loaded(
        player_id: u64,
        start_tick: u64,
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        remaining: Vec<u16>,
        loaded: u8,
    ) -> Result<Self, PlayerAmmoError> {
        let shell_count = Self::validate_initial_state(player_id, descriptor)?;
        let ammo = AmmoState::new_exact_loaded(descriptor, profile, remaining, usize::from(loaded))
            .map_err(|_| PlayerAmmoError::InvalidExactInventory)?;
        Self::from_ammo(
            player_id,
            start_tick,
            descriptor,
            profile,
            shell_count,
            ammo,
        )
    }

    /// Initialize the exact garage state plus the finished #1513 Intuition
    /// perk count donated inside the validated effective-params projection.
    pub fn new_exact_loaded_with_intuition(
        player_id: u64,
        start_tick: u64,
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        remaining: Vec<u16>,
        loaded: u8,
        intuition_chances: u8,
    ) -> Result<Self, PlayerAmmoError> {
        if intuition_chances > 16 {
            return Err(PlayerAmmoError::InvalidIntuitionChances);
        }
        let mut ledger = Self::new_exact_loaded(
            player_id, start_tick, descriptor, profile, remaining, loaded,
        )?;
        ledger.intuition_chances = intuition_chances;
        Ok(ledger)
    }

    fn validate_initial_state(
        player_id: u64,
        descriptor: &VehicleDescriptor,
    ) -> Result<usize, PlayerAmmoError> {
        if !(1..=MAX_COMBAT_ID).contains(&player_id) {
            return Err(PlayerAmmoError::InvalidPlayerId);
        }
        let shell_count = descriptor.gun.shells.len();
        if shell_count == 0 || shell_count > usize::from(u8::MAX) + 1 {
            return Err(PlayerAmmoError::InvalidDescriptor);
        }
        if !descriptor.gun.reload_seconds.is_finite()
            || descriptor.gun.reload_seconds <= 0.0
            || descriptor.gun.clip.is_some_and(|clip| {
                clip.size == 0
                    || !clip.intra_reload_seconds.is_finite()
                    || clip.intra_reload_seconds <= 0.0
            })
        {
            return Err(PlayerAmmoError::InvalidDescriptor);
        }
        Ok(shell_count)
    }

    fn from_ammo(
        player_id: u64,
        start_tick: u64,
        descriptor: &VehicleDescriptor,
        profile: &BotProfile,
        shell_count: usize,
        ammo: AmmoState,
    ) -> Result<Self, PlayerAmmoError> {
        let snapshot = ammo.snapshot();
        let requested_shell =
            u8::try_from(snapshot.loaded).map_err(|_| PlayerAmmoError::InvalidDescriptor)?;
        let standard_shells = standard_shell_mask(profile, shell_count);
        let (clip_size, reload_intra_seconds) = descriptor
            .gun
            .clip
            .map(|clip| (clip.size, clip.intra_reload_seconds))
            .unwrap_or((1, 0.0));
        Ok(Self {
            player_id,
            shell_count,
            tick: start_tick,
            revision: 0,
            last_input_seq: 0,
            last_intent_seq: 0,
            last_shot_seq: 0,
            requested_shell,
            intuition_chances: 0,
            reload_factor: 1.0,
            standard_shells,
            remaining: snapshot.remaining,
            loaded_shell: requested_shell,
            next_shell: u8::try_from(snapshot.next)
                .map_err(|_| PlayerAmmoError::InvalidDescriptor)?,
            reload_pending: snapshot.reload_pending,
            plan_pending: true,
            clip_size,
            clip_remaining: clip_size,
            reload_full_seconds: descriptor.gun.reload_seconds,
            reload_intra_seconds,
            reload_kind: ReloadKind::Full,
            reload_elapsed_seconds: 0.0,
            active_burst: None,
            input_receipts: BTreeMap::new(),
            intent_receipts: BTreeMap::new(),
            launch_receipts: BTreeMap::new(),
        })
    }

    pub fn player_id(&self) -> u64 {
        self.player_id
    }

    pub fn tick(&self) -> u64 {
        self.tick
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn last_input_seq(&self) -> u64 {
        self.last_input_seq
    }

    pub fn last_intent_seq(&self) -> u64 {
        self.last_intent_seq
    }

    pub fn last_shot_seq(&self) -> u64 {
        self.last_shot_seq
    }

    pub fn intuition_chances(&self) -> u8 {
        self.intuition_chances
    }

    /// Freeze the Rust-owned loaded shell against one admitted player input.
    ///
    /// The visible input carries no ammunition verdict. Every newly admitted
    /// input sequence is instead paired with the shell loaded in this ledger
    /// at that exact boundary, so later fire intents cannot select a shell by
    /// rewriting their input payload.
    pub fn admit_input(
        &mut self,
        input_seq: u64,
    ) -> Result<PlayerAmmoInputAdmission, PlayerAmmoError> {
        if !(1..=MAX_INPUT_SEQUENCE).contains(&input_seq) {
            return Err(PlayerAmmoError::InvalidInputSequence);
        }
        if self.input_receipts.contains_key(&input_seq) {
            return Ok(PlayerAmmoInputAdmission::ExactRetry);
        }
        if input_seq <= self.last_input_seq {
            return Err(PlayerAmmoError::StaleInputRetry {
                sequence: input_seq,
            });
        }
        if input_seq != self.last_input_seq.saturating_add(1) {
            return Err(PlayerAmmoError::InputSequenceGap {
                last: self.last_input_seq,
                received: input_seq,
            });
        }
        self.validate_shell(self.loaded_shell)?;
        let revision = self.next_revision()?;
        let input = PlayerAmmoInput {
            input_seq,
            shell_index: self.loaded_shell,
        };

        self.last_input_seq = input_seq;
        self.input_receipts.insert(input_seq, input);
        prune_receipts(&mut self.input_receipts);
        self.revision = revision;
        Ok(PlayerAmmoInputAdmission::New)
    }

    /// Return the Rust-owned shell frozen against an admitted input sequence.
    pub fn shell_for_input(&self, input_seq: u64) -> Option<u8> {
        self.input_receipts
            .get(&input_seq)
            .map(|input| input.shell_index)
    }

    pub fn loaded_shell(&self) -> u8 {
        self.loaded_shell
    }

    /// Apply one ordered player ammunition action. The caller supplies the
    /// deterministic Rust-owned Intuition verdict for `SelectCurrent`; all
    /// other actions ignore it. Exact retries return the original outcome.
    pub fn admit_intent(
        &mut self,
        intent: PlayerAmmoIntent,
        intuition_success: bool,
    ) -> Result<PlayerAmmoIntentAdmission, PlayerAmmoError> {
        self.admit_intent_inner(intent, intuition_success, false)
    }

    /// Consume an ordered ammunition action while an already-admitted trigger
    /// or physical burst still owns the loaded shell. Selection is queued for
    /// recovery after that launch; it cannot invalidate the frozen shot.
    pub fn admit_intent_deferred_until_launch(
        &mut self,
        intent: PlayerAmmoIntent,
        intuition_success: bool,
    ) -> Result<PlayerAmmoIntentAdmission, PlayerAmmoError> {
        self.admit_intent_inner(intent, intuition_success, true)
    }

    fn admit_intent_inner(
        &mut self,
        intent: PlayerAmmoIntent,
        intuition_success: bool,
        defer_loaded_shell: bool,
    ) -> Result<PlayerAmmoIntentAdmission, PlayerAmmoError> {
        if !(1..=MAX_COMBAT_ID).contains(&intent.intent_seq) {
            return Err(PlayerAmmoError::InvalidIntentSequence);
        }
        let intuition_success = intuition_success
            && matches!(intent.action, PlayerAmmoIntentAction::SelectCurrent { .. });
        if let Some(previous) = self.intent_receipts.get(&intent.intent_seq) {
            return if previous.intent == intent && previous.intuition_success == intuition_success {
                Ok(PlayerAmmoIntentAdmission::ExactRetry(previous.outcome))
            } else {
                Err(PlayerAmmoError::ConflictingIntentRetry {
                    sequence: intent.intent_seq,
                })
            };
        }
        if intent.intent_seq <= self.last_intent_seq {
            return Err(PlayerAmmoError::StaleIntentRetry {
                sequence: intent.intent_seq,
            });
        }
        if intent.intent_seq != self.last_intent_seq.saturating_add(1) {
            return Err(PlayerAmmoError::IntentSequenceGap {
                last: self.last_intent_seq,
                received: intent.intent_seq,
            });
        }
        if intent.input_seq == 0 || intent.input_seq != self.last_input_seq {
            return Err(PlayerAmmoError::IntentInputBinding);
        }
        let defer_loaded_shell = defer_loaded_shell || self.active_burst.is_some();
        if let PlayerAmmoIntentAction::SelectCurrent { shell_index }
        | PlayerAmmoIntentAction::SelectNext { shell_index } = intent.action
        {
            self.validate_shell(shell_index)?;
            if self.remaining[usize::from(shell_index)] == 0 {
                return Err(PlayerAmmoError::OutOfAmmo);
            }
        }

        let revision = self.next_revision()?;
        let mut staged = self.clone();
        let outcome = if defer_loaded_shell {
            staged.queue_intent_after_pending_launch(intent.action)?
        } else {
            staged.apply_intent_action(intent.action, intuition_success)?
        };
        staged.last_intent_seq = intent.intent_seq;
        staged.intent_receipts.insert(
            intent.intent_seq,
            PlayerAmmoIntentReceipt {
                intent,
                intuition_success,
                outcome,
            },
        );
        prune_receipts(&mut staged.intent_receipts);
        staged.revision = revision;
        *self = staged;
        Ok(PlayerAmmoIntentAdmission::New(outcome))
    }

    fn queue_intent_after_pending_launch(
        &mut self,
        action: PlayerAmmoIntentAction,
    ) -> Result<PlayerAmmoIntentOutcome, PlayerAmmoError> {
        match action {
            PlayerAmmoIntentAction::SelectCurrent { shell_index }
            | PlayerAmmoIntentAction::SelectNext { shell_index } => {
                self.requested_shell = shell_index;
                self.next_shell = shell_index;
                self.plan_pending = false;
                if shell_index == self.loaded_shell {
                    Ok(PlayerAmmoIntentOutcome::Unchanged)
                } else {
                    Ok(PlayerAmmoIntentOutcome::Queued { shell_index })
                }
            }
            PlayerAmmoIntentAction::ReloadPartialClip => {
                if self.clip_size <= 1 {
                    return Ok(PlayerAmmoIntentOutcome::Unchanged);
                }
                self.requested_shell = self.loaded_shell;
                self.next_shell = self.loaded_shell;
                self.plan_pending = false;
                Ok(PlayerAmmoIntentOutcome::Queued {
                    shell_index: self.loaded_shell,
                })
            }
        }
    }

    fn apply_intent_action(
        &mut self,
        action: PlayerAmmoIntentAction,
        intuition_success: bool,
    ) -> Result<PlayerAmmoIntentOutcome, PlayerAmmoError> {
        match action {
            PlayerAmmoIntentAction::SelectNext { shell_index } => {
                self.requested_shell = shell_index;
                self.plan_pending = false;
                if shell_index == self.loaded_shell {
                    self.next_shell = shell_index;
                    return Ok(PlayerAmmoIntentOutcome::Unchanged);
                }
                if self.remaining[usize::from(self.loaded_shell)] == 0 {
                    self.start_full_reload(shell_index);
                    return Ok(PlayerAmmoIntentOutcome::Reloading { shell_index });
                }
                self.next_shell = shell_index;
                Ok(PlayerAmmoIntentOutcome::Queued { shell_index })
            }
            PlayerAmmoIntentAction::SelectCurrent { shell_index } => {
                self.requested_shell = shell_index;
                self.next_shell = shell_index;
                self.plan_pending = false;
                if shell_index == self.loaded_shell {
                    return Ok(PlayerAmmoIntentOutcome::Unchanged);
                }
                if intuition_success {
                    self.loaded_shell = shell_index;
                    self.clip_remaining =
                        self.clip_size.min(self.remaining[usize::from(shell_index)]);
                    self.reload_kind = ReloadKind::Full;
                    self.reload_pending = false;
                    self.reload_elapsed_seconds = self.reload_duration()
                        + fixed_dt_us(self.tick.saturating_add(1)) as f64 / 1_000_000.0;
                    Ok(PlayerAmmoIntentOutcome::IntuitionLoaded { shell_index })
                } else {
                    self.start_full_reload(shell_index);
                    Ok(PlayerAmmoIntentOutcome::Reloading { shell_index })
                }
            }
            PlayerAmmoIntentAction::ReloadPartialClip => {
                if self.clip_size <= 1 {
                    return Ok(PlayerAmmoIntentOutcome::Unchanged);
                }
                let queued = (self.next_shell != self.loaded_shell
                    && self.remaining[usize::from(self.next_shell)] > 0)
                    .then_some(self.next_shell);
                if queued.is_none()
                    && (self.clip_remaining >= self.clip_size
                        || (self.clip_remaining == 0
                            && self.reload_pending
                            && self.reload_kind == ReloadKind::Full
                            && !self.reload_ready()))
                {
                    return Ok(PlayerAmmoIntentOutcome::Unchanged);
                }
                let shell_index = queued.unwrap_or(self.loaded_shell);
                if self.remaining[usize::from(shell_index)] == 0 {
                    return Err(PlayerAmmoError::OutOfAmmo);
                }
                self.start_full_reload(shell_index);
                Ok(PlayerAmmoIntentOutcome::Reloading { shell_index })
            }
        }
    }

    fn start_full_reload(&mut self, shell_index: u8) {
        self.loaded_shell = shell_index;
        self.requested_shell = shell_index;
        self.next_shell = shell_index;
        self.clip_remaining = 0;
        self.reload_kind = ReloadKind::Full;
        self.reload_elapsed_seconds = 0.0;
        self.reload_pending = true;
        self.plan_pending = false;
    }

    /// Consume a launch already bound to the canonical projectile admission.
    ///
    /// The operation is transactional: any failure leaves the gun clock,
    /// inventory, sequence high-water marks, and retry receipts unchanged.
    pub fn admit_launch(
        &mut self,
        launch: PlayerAmmoLaunch,
    ) -> Result<PlayerAmmoLaunchAdmission, PlayerAmmoError> {
        self.admit_physical_launch(launch, PlayerAmmoBurst::ordinary(launch.shot_seq))
    }

    /// Consume one physical shell in a descriptor burst.
    ///
    /// Every edge has an independent shot sequence and inventory debit. The
    /// reload clock begins only on the final committed edge; a failed edge
    /// leaves the entire ledger unchanged.
    pub fn admit_physical_launch(
        &mut self,
        launch: PlayerAmmoLaunch,
        burst: PlayerAmmoBurst,
    ) -> Result<PlayerAmmoLaunchAdmission, PlayerAmmoError> {
        if !(1..=MAX_COMBAT_ID).contains(&launch.shot_seq) {
            return Err(PlayerAmmoError::InvalidShotSequence);
        }
        if !(1..=MAX_INPUT_SEQUENCE).contains(&launch.input_seq) {
            return Err(PlayerAmmoError::InvalidInputSequence);
        }
        let receipt = PlayerAmmoLaunchReceipt { launch, burst };
        if let Some(previous) = self.launch_receipts.get(&launch.shot_seq) {
            return if previous == &receipt {
                Ok(PlayerAmmoLaunchAdmission::ExactRetry)
            } else {
                Err(PlayerAmmoError::ConflictingLaunchRetry {
                    sequence: launch.shot_seq,
                })
            };
        }
        if launch.shot_seq <= self.last_shot_seq {
            return Err(PlayerAmmoError::StaleLaunchRetry {
                sequence: launch.shot_seq,
            });
        }
        if launch.shot_seq != self.last_shot_seq.saturating_add(1) {
            return Err(PlayerAmmoError::ShotSequenceGap {
                last: self.last_shot_seq,
                received: launch.shot_seq,
            });
        }
        self.validate_shell(launch.shell_index)?;
        let input = self.input_receipts.get(&launch.input_seq).ok_or(
            PlayerAmmoError::UnknownLaunchInput {
                input_seq: launch.input_seq,
            },
        )?;
        if input.shell_index != launch.shell_index {
            return Err(PlayerAmmoError::LaunchInputBinding {
                input_seq: launch.input_seq,
            });
        }

        if burst.count == 0
            || burst.count > MAX_PHYSICAL_BURST_COUNT
            || burst.index >= burst.count
            || burst.group_seq == 0
            || burst.group_seq.checked_add(u64::from(burst.index)) != Some(launch.shot_seq)
        {
            return Err(PlayerAmmoError::InvalidBurst);
        }
        if self.loaded_shell != launch.shell_index {
            return Err(PlayerAmmoError::ShellNotLoaded {
                requested: launch.shell_index,
                loaded: self.loaded_shell,
            });
        }
        match (burst.index, self.active_burst) {
            (0, None) => {
                if burst.group_seq != launch.shot_seq {
                    return Err(PlayerAmmoError::InvalidBurst);
                }
                if !self.reload_ready() {
                    return Err(PlayerAmmoError::Reloading);
                }
                let loaded_remaining = self.remaining[usize::from(self.loaded_shell)];
                if self.reload_pending || self.clip_remaining == 0 || loaded_remaining == 0 {
                    return Err(PlayerAmmoError::OutOfAmmo);
                }
                if burst.count > self.clip_remaining || burst.count > loaded_remaining {
                    return Err(PlayerAmmoError::InvalidBurst);
                }
            }
            (0, Some(_)) => return Err(PlayerAmmoError::BurstAlreadyActive),
            (_, Some(active))
                if active.group_seq == burst.group_seq
                    && active.count == burst.count
                    && active.next_index == burst.index
                    && active.shell_index == launch.shell_index => {}
            (_, _) => return Err(PlayerAmmoError::BurstBinding),
        }

        let revision = self.next_revision()?;
        let loaded_index = usize::from(self.loaded_shell);
        let next_remaining = self.remaining[loaded_index]
            .checked_sub(1)
            .ok_or(PlayerAmmoError::AtomicLaunchInvariant)?;
        let next_clip = self
            .clip_remaining
            .checked_sub(1)
            .ok_or(PlayerAmmoError::AtomicLaunchInvariant)?;
        let final_round = burst.index.saturating_add(1) == burst.count;

        self.remaining[loaded_index] = next_remaining;
        self.clip_remaining = next_clip;
        self.reload_pending = true;
        self.plan_pending = false;
        self.next_shell = self.available_shell(self.next_shell);
        if final_round {
            self.active_burst = None;
            self.reload_elapsed_seconds = 0.0;
            if self.clip_remaining == 0
                || (next_remaining == 0 && self.remaining.iter().copied().sum::<u16>() > 0)
            {
                // An empty magazine stays empty through the full reload. If
                // the loaded shell type was exhausted mid-clip, discard the
                // unusable slots before switching type at that same boundary.
                self.clip_remaining = 0;
                self.reload_kind = ReloadKind::Full;
            } else {
                self.reload_kind = ReloadKind::Intra;
            }
        } else {
            self.active_burst = Some(ActiveAmmoBurst {
                group_seq: burst.group_seq,
                count: burst.count,
                next_index: burst.index + 1,
                shell_index: launch.shell_index,
            });
        }
        self.last_shot_seq = launch.shot_seq;
        self.launch_receipts.insert(launch.shot_seq, receipt);
        prune_receipts(&mut self.launch_receipts);
        self.revision = revision;
        Ok(PlayerAmmoLaunchAdmission::New)
    }

    /// Cancel only the unlaunched tail and begin ordinary recovery.
    pub fn cancel_physical_burst(&mut self) -> Result<bool, PlayerAmmoError> {
        if self.active_burst.is_none() {
            return Ok(false);
        }
        let revision = self.next_revision()?;
        self.active_burst = None;
        self.reload_elapsed_seconds = 0.0;
        if self.clip_remaining == 0
            || (self.remaining[usize::from(self.loaded_shell)] == 0
                && self.remaining.iter().copied().sum::<u16>() > 0)
        {
            self.clip_remaining = 0;
            self.reload_kind = ReloadKind::Full;
        } else {
            self.reload_kind = ReloadKind::Intra;
        }
        self.revision = revision;
        Ok(true)
    }

    /// Advance exactly one canonical fixed tick.
    ///
    /// Reload rescaling preserves the completed fraction, matching bot gun
    /// authority when a live critical-damage factor changes.
    pub fn advance_tick(&mut self, tick: u64, reload_factor: f64) -> Result<(), PlayerAmmoError> {
        if !reload_factor.is_finite() || reload_factor < 0.0 {
            return Err(PlayerAmmoError::InvalidReloadFactor);
        }
        let expected = self
            .tick
            .checked_add(1)
            .ok_or(PlayerAmmoError::TickSequenceExhausted)?;
        if tick != expected {
            return Err(PlayerAmmoError::TickSequence {
                last: self.tick,
                received: tick,
            });
        }
        let revision = self.next_revision()?;
        let mut staged = self.clone();
        if staged.active_burst.is_none() {
            staged.rescale_reload(reload_factor);
            staged.reload_elapsed_seconds += fixed_dt_us(tick) as f64 / 1_000_000.0;
            if staged.reload_ready() {
                if staged.reload_kind == ReloadKind::Full && staged.clip_remaining == 0 {
                    staged.loaded_shell = staged.available_shell(staged.next_shell);
                    staged.clip_remaining = staged
                        .clip_size
                        .min(staged.remaining[usize::from(staged.loaded_shell)]);
                }
                if staged.reload_pending {
                    // Only a full reload promotes the planned shell. An
                    // intra-clip edge retains the physically loaded type.
                    if staged.reload_kind == ReloadKind::Full {
                        staged.loaded_shell = staged.available_shell(staged.next_shell);
                    }
                    staged.reload_pending = false;
                    staged.plan_pending = true;
                }
                if staged.plan_pending {
                    staged.next_shell = staged.available_shell(staged.requested_shell);
                    staged.plan_pending = false;
                }
            }
        } else {
            // Descriptor-cadence tails are physical shells, not reload time.
            // Keep the reload factor current but freeze elapsed recovery until
            // the final edge or cancellation starts the clock.
            staged.reload_factor = reload_factor;
        }
        staged.tick = tick;
        staged.revision = revision;
        *self = staged;
        Ok(())
    }

    pub fn snapshot(&self) -> PlayerAmmoSnapshot {
        let reload_ready = self.reload_ready();
        let active = self.active_burst;
        PlayerAmmoSnapshot {
            player_id: self.player_id,
            tick: self.tick,
            revision: self.revision,
            last_input_seq: self.last_input_seq,
            last_intent_seq: self.last_intent_seq,
            last_shot_seq: self.last_shot_seq,
            requested_shell: self.requested_shell,
            loaded_shell: self.loaded_shell,
            next_shell: self.next_shell,
            remaining: self.remaining.clone(),
            reload_pending: self.reload_pending,
            clip_size: self.clip_size,
            clip_remaining: self.clip_remaining,
            reload_factor: self.reload_factor,
            reload_duration_seconds: self.reload_duration(),
            reload_remaining_seconds: self.reload_remaining(),
            reload_ready,
            can_fire: reload_ready
                && active.is_none()
                && !self.reload_pending
                && self.clip_remaining > 0
                && self.remaining[usize::from(self.loaded_shell)] > 0,
            burst_active: active.is_some(),
            burst_group_seq: active.map_or(0, |burst| burst.group_seq),
            burst_count: active.map_or(0, |burst| burst.count),
            burst_next_index: active.map_or(0, |burst| burst.next_index),
            burst_shell_index: active.map_or(self.loaded_shell, |burst| burst.shell_index),
        }
    }

    fn reload_duration(&self) -> f64 {
        match self.reload_kind {
            ReloadKind::Full => self.reload_full_seconds * self.reload_factor,
            ReloadKind::Intra => self.reload_intra_seconds,
        }
    }

    fn reload_remaining(&self) -> f64 {
        (self.reload_duration() - self.reload_elapsed_seconds).max(0.0)
    }

    fn reload_ready(&self) -> bool {
        self.reload_elapsed_seconds > self.reload_duration()
    }

    fn rescale_reload(&mut self, reload_factor: f64) {
        let old_duration = self.reload_duration();
        let next_duration = match self.reload_kind {
            ReloadKind::Full => self.reload_full_seconds * reload_factor,
            ReloadKind::Intra => self.reload_intra_seconds,
        };
        if old_duration > 0.0 {
            if self.reload_elapsed_seconds < old_duration {
                let completed = (self.reload_elapsed_seconds / old_duration).clamp(0.0, 1.0);
                self.reload_elapsed_seconds = next_duration * completed;
            } else {
                self.reload_elapsed_seconds =
                    next_duration + (self.reload_elapsed_seconds - old_duration);
            }
        }
        self.reload_factor = reload_factor;
    }

    fn available_shell(&self, requested: u8) -> u8 {
        let requested_index = usize::from(requested);
        if requested_index < self.remaining.len() && self.remaining[requested_index] > 0 {
            return requested;
        }
        if let Some(index) = self
            .remaining
            .iter()
            .enumerate()
            .find_map(|(index, quantity)| {
                (*quantity > 0 && self.standard_shells[index]).then_some(index)
            })
        {
            return u8::try_from(index).unwrap_or(0);
        }
        self.remaining
            .iter()
            .position(|quantity| *quantity > 0)
            .and_then(|index| u8::try_from(index).ok())
            .unwrap_or(0)
    }

    fn validate_shell(&self, shell_index: u8) -> Result<(), PlayerAmmoError> {
        if usize::from(shell_index) >= self.shell_count {
            return Err(PlayerAmmoError::InvalidShell { shell_index });
        }
        Ok(())
    }

    fn next_revision(&self) -> Result<u64, PlayerAmmoError> {
        self.revision
            .checked_add(1)
            .ok_or(PlayerAmmoError::RevisionExhausted)
    }
}

fn standard_shell_mask(profile: &BotProfile, shell_count: usize) -> Vec<bool> {
    let by_index: BTreeMap<_, _> = profile
        .shells
        .iter()
        .filter(|shell| shell.index < shell_count)
        .map(|shell| (shell.index, shell))
        .collect();
    let mut high_explosive = vec![false; shell_count];
    let mut non_he = Vec::new();
    for (index, flag) in high_explosive.iter_mut().enumerate() {
        let kind = by_index
            .get(&index)
            .map(|shell| shell.kind.to_ascii_lowercase())
            .unwrap_or_default();
        *flag = kind.contains("high_explosive")
            || (kind.contains("explosive") && !kind.contains("armor_piercing"));
        if !*flag {
            non_he.push(index);
        }
    }
    let baseline = non_he.first().copied();
    let baseline_penetration = baseline
        .and_then(|index| by_index.get(&index))
        .map(|shell| shell.penetration.max(0.0))
        .unwrap_or(0.0);
    let mut standard = vec![false; shell_count];
    for index in non_he {
        let penetration = by_index
            .get(&index)
            .map(|shell| shell.penetration.max(0.0))
            .unwrap_or(0.0);
        standard[index] = Some(index) == baseline
            || baseline_penetration <= 0.0
            || penetration < baseline_penetration * 1.03;
    }
    standard
}

fn prune_receipts<T>(receipts: &mut BTreeMap<u64, T>) {
    while receipts.len() > MAX_PLAYER_AMMO_RECEIPTS {
        if let Some(oldest) = receipts.keys().next().copied() {
            receipts.remove(&oldest);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bot_sim::{
        ClipDescriptor, GunDescriptor, GunYawLimits, PhysicsProfile, ShellDescriptor, ShellProfile,
        VehicleClass,
    };

    fn descriptor(
        max_ammo: u16,
        reload_seconds: f64,
        clip: Option<ClipDescriptor>,
    ) -> VehicleDescriptor {
        VehicleDescriptor {
            vehicle_key: "usa:test_tank".to_owned(),
            max_ammo,
            max_health: 1_000,
            half_length: 3.0,
            half_width: 1.5,
            gun: GunDescriptor {
                reload_seconds,
                clip,
                shot_dispersion_angle: 0.01,
                gun_rotation_speed: 1.0,
                turret_rotation_speed: 1.0,
                pitch_limits: (-0.2, 0.3),
                yaw_limits: GunYawLimits::default(),
                shells: vec![
                    shell(0, "ARMOR_PIERCING"),
                    shell(1, "ARMOR_PIERCING_CR"),
                    shell(2, "HIGH_EXPLOSIVE"),
                ],
            },
            physics: PhysicsProfile::default(),
            module_names: Vec::new(),
            crew_roster: Vec::new(),
        }
    }

    fn single_shell_descriptor(max_ammo: u16) -> VehicleDescriptor {
        let mut descriptor = descriptor(max_ammo, 1.0, None);
        descriptor.gun.shells.truncate(1);
        descriptor
    }

    fn shell(index: usize, kind: &str) -> ShellDescriptor {
        ShellDescriptor {
            index,
            kind: kind.to_owned(),
            penetration: 100.0 + index as f64 * 20.0,
            damage: 100.0,
            speed: 800.0,
            gravity: 9.81,
            max_distance: 720.0,
        }
    }

    fn profile(shell_count: usize) -> BotProfile {
        BotProfile {
            class: VehicleClass::MediumTank,
            shells: (0..shell_count)
                .map(|index| ShellProfile {
                    index,
                    kind: match index {
                        2 => "HIGH_EXPLOSIVE",
                        1 => "ARMOR_PIERCING_CR",
                        _ => "ARMOR_PIERCING",
                    }
                    .to_owned(),
                    penetration: 100.0 + index as f64 * 20.0,
                })
                .collect(),
        }
    }

    fn launch(shot_seq: u64, input_seq: u64, shell_index: u8) -> PlayerAmmoLaunch {
        PlayerAmmoLaunch {
            shot_seq,
            input_seq,
            shell_index,
        }
    }

    fn ammo_intent(
        intent_seq: u64,
        input_seq: u64,
        action: PlayerAmmoIntentAction,
    ) -> PlayerAmmoIntent {
        PlayerAmmoIntent {
            intent_seq,
            input_seq,
            action,
        }
    }

    fn advance_through(ledger: &mut PlayerAmmoLedger, through_tick: u64) {
        while ledger.tick() < through_tick {
            ledger
                .advance_tick(ledger.tick() + 1, 1.0)
                .expect("contiguous fixed tick");
        }
    }

    #[test]
    fn descriptor_inventory_and_strict_reload_match_bot_authority() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        let initial = ledger.snapshot();
        assert_eq!(initial.remaining, vec![3, 2, 1]);
        assert_eq!((initial.loaded_shell, initial.next_shell), (0, 0));
        assert!(!initial.reload_ready);

        advance_through(&mut ledger, 29);
        assert!(!ledger.snapshot().reload_ready);
        ledger.advance_tick(30, 1.0).unwrap();
        assert_eq!(ledger.snapshot().reload_remaining_seconds, 0.0);
        assert!(ledger.snapshot().reload_ready);
        assert!(ledger.snapshot().can_fire);
    }

    #[test]
    fn exact_inventory_constructor_preserves_loadout_and_rejects_invalid_arrays() {
        let exact_descriptor = descriptor(6, 1.0, None);
        let ledger =
            PlayerAmmoLedger::new_exact(7, 0, &exact_descriptor, &profile(3), vec![0, 2, 1])
                .unwrap();
        let snapshot = ledger.snapshot();
        assert_eq!(snapshot.remaining, vec![0, 2, 1]);
        assert_eq!((snapshot.loaded_shell, snapshot.next_shell), (1, 1));
        assert_eq!(snapshot.requested_shell, 1);

        assert_eq!(
            PlayerAmmoLedger::new_exact(7, 0, &exact_descriptor, &profile(3), vec![1, 2])
                .unwrap_err(),
            PlayerAmmoError::InvalidExactInventory
        );
        assert_eq!(
            PlayerAmmoLedger::new_exact(7, 0, &exact_descriptor, &profile(3), vec![3, 3, 1])
                .unwrap_err(),
            PlayerAmmoError::InvalidExactInventory
        );

        let large_descriptor = descriptor(2_000, 1.0, None);
        assert_eq!(
            PlayerAmmoLedger::new_exact(7, 0, &large_descriptor, &profile(3), vec![1_001, 0, 0],)
                .unwrap_err(),
            PlayerAmmoError::InvalidExactInventory
        );
    }

    #[test]
    fn exact_loaded_constructor_preserves_valid_garage_shell() {
        let descriptor = descriptor(6, 1.0, None);
        let selected =
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![2, 0, 1], 2)
                .unwrap()
                .snapshot();
        assert_eq!(
            (
                selected.loaded_shell,
                selected.next_shell,
                selected.requested_shell,
            ),
            (2, 2, 2)
        );
        assert_eq!(selected.remaining, vec![2, 0, 1]);

        assert_eq!(
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![2, 0, 1], 1,)
                .unwrap_err(),
            PlayerAmmoError::InvalidExactInventory
        );
        assert_eq!(
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![2, 0, 1], 3,)
                .unwrap_err(),
            PlayerAmmoError::InvalidExactInventory
        );

        let empty =
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![0, 0, 0], 2)
                .unwrap()
                .snapshot();
        assert_eq!((empty.loaded_shell, empty.next_shell), (2, 2));
        assert!(!empty.can_fire);

        let intuition = PlayerAmmoLedger::new_exact_loaded_with_intuition(
            7,
            0,
            &descriptor,
            &profile(3),
            vec![2, 0, 1],
            2,
            2,
        )
        .unwrap();
        assert_eq!(intuition.intuition_chances(), 2);
        assert_eq!(
            PlayerAmmoLedger::new_exact_loaded_with_intuition(
                7,
                0,
                &descriptor,
                &profile(3),
                vec![2, 0, 1],
                2,
                17,
            )
            .unwrap_err(),
            PlayerAmmoError::InvalidIntuitionChances
        );
    }

    #[test]
    fn input_sequence_is_contiguous_and_exact_retries_are_idempotent() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        assert_eq!(ledger.admit_input(1), Ok(PlayerAmmoInputAdmission::New));
        assert_eq!(ledger.shell_for_input(1), Some(0));
        let revision = ledger.revision();
        assert_eq!(
            ledger.admit_input(1),
            Ok(PlayerAmmoInputAdmission::ExactRetry)
        );
        assert_eq!(ledger.revision(), revision);
        assert_eq!(
            ledger.admit_input(3),
            Err(PlayerAmmoError::InputSequenceGap {
                last: 1,
                received: 3,
            })
        );
        assert_eq!(ledger.last_input_seq(), 1);
    }

    #[test]
    fn launch_consumption_is_atomic_strict_and_idempotent() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 31);

        let first = launch(1, 1, 0);
        assert_eq!(
            ledger.admit_launch(first),
            Ok(PlayerAmmoLaunchAdmission::New)
        );
        let fired = ledger.snapshot();
        assert_eq!(fired.remaining, vec![2, 2, 1]);
        assert_eq!(fired.last_shot_seq, 1);
        assert!(!fired.can_fire);
        assert_eq!(
            ledger.admit_launch(first),
            Ok(PlayerAmmoLaunchAdmission::ExactRetry)
        );
        assert_eq!(ledger.snapshot(), fired);
        assert_eq!(
            ledger.admit_launch(launch(1, 1, 1)),
            Err(PlayerAmmoError::ConflictingLaunchRetry { sequence: 1 })
        );
        assert_eq!(
            ledger.admit_launch(launch(3, 1, 0)),
            Err(PlayerAmmoError::ShotSequenceGap {
                last: 1,
                received: 3,
            })
        );
        assert_eq!(
            ledger.admit_launch(launch(2, 1, 0)),
            Err(PlayerAmmoError::Reloading)
        );
        assert_eq!(ledger.last_shot_seq(), 1);

        advance_through(&mut ledger, 60);
        assert!(!ledger.snapshot().reload_ready);
        ledger.advance_tick(61, 1.0).unwrap();
        assert_eq!(
            ledger.admit_launch(launch(2, 1, 0)),
            Ok(PlayerAmmoLaunchAdmission::New)
        );
        assert_eq!(ledger.snapshot().remaining, vec![1, 2, 1]);
    }

    #[test]
    fn clip_uses_intra_round_then_full_reload_clock() {
        let descriptor = descriptor(
            12,
            1.0,
            Some(ClipDescriptor {
                size: 2,
                intra_reload_seconds: 0.4,
            }),
        );
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 31);
        ledger.admit_launch(launch(1, 1, 0)).unwrap();
        let first = ledger.snapshot();
        assert_eq!(first.clip_remaining, 1);
        assert_eq!(first.reload_duration_seconds, 0.4);

        advance_through(&mut ledger, 43);
        assert!(!ledger.snapshot().reload_ready);
        ledger.advance_tick(44, 1.0).unwrap();
        ledger.admit_launch(launch(2, 1, 0)).unwrap();
        let second = ledger.snapshot();
        assert_eq!(second.clip_remaining, 0);
        assert_eq!(second.reload_duration_seconds, 1.0);

        advance_through(&mut ledger, 73);
        assert_eq!(ledger.snapshot().clip_remaining, 0);
        ledger.advance_tick(74, 1.0).unwrap();
        assert_eq!(ledger.snapshot().clip_remaining, 2);
    }

    #[test]
    fn queued_shell_becomes_loaded_only_at_reload_edges() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 30);
        ledger
            .admit_intent(
                ammo_intent(1, 1, PlayerAmmoIntentAction::SelectNext { shell_index: 1 }),
                false,
            )
            .unwrap();
        let planned = ledger.snapshot();
        assert_eq!((planned.loaded_shell, planned.next_shell), (0, 1));
        assert_eq!(
            ledger.admit_launch(launch(1, 1, 1)),
            Err(PlayerAmmoError::LaunchInputBinding { input_seq: 1 })
        );

        ledger.admit_input(2).unwrap();
        ledger.admit_launch(launch(1, 2, 0)).unwrap();
        ledger.admit_input(3).unwrap();
        ledger
            .admit_intent(
                ammo_intent(2, 3, PlayerAmmoIntentAction::SelectNext { shell_index: 2 }),
                false,
            )
            .unwrap();
        advance_through(&mut ledger, 59);
        assert!(!ledger.snapshot().reload_ready);
        ledger.advance_tick(60, 1.0).unwrap();
        let reloaded = ledger.snapshot();
        assert_eq!((reloaded.loaded_shell, reloaded.next_shell), (2, 2));
        assert_eq!(reloaded.requested_shell, 2);
        ledger.admit_input(4).unwrap();
        assert_eq!(
            ledger.admit_launch(launch(2, 4, 2)),
            Ok(PlayerAmmoLaunchAdmission::New)
        );
    }

    #[test]
    fn ordered_shell_actions_queue_reload_or_apply_deterministic_intuition() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 31);

        let queued = ammo_intent(1, 1, PlayerAmmoIntentAction::SelectNext { shell_index: 1 });
        assert_eq!(
            ledger.admit_intent(queued, false),
            Ok(PlayerAmmoIntentAdmission::New(
                PlayerAmmoIntentOutcome::Queued { shell_index: 1 }
            ))
        );
        assert_eq!(
            (ledger.snapshot().loaded_shell, ledger.snapshot().next_shell),
            (0, 1)
        );
        let revision = ledger.revision();
        assert_eq!(
            ledger.admit_intent(queued, false),
            Ok(PlayerAmmoIntentAdmission::ExactRetry(
                PlayerAmmoIntentOutcome::Queued { shell_index: 1 }
            ))
        );
        assert_eq!(ledger.revision(), revision);

        assert_eq!(
            ledger.admit_intent(
                ammo_intent(
                    2,
                    1,
                    PlayerAmmoIntentAction::SelectCurrent { shell_index: 1 },
                ),
                true,
            ),
            Ok(PlayerAmmoIntentAdmission::New(
                PlayerAmmoIntentOutcome::IntuitionLoaded { shell_index: 1 }
            ))
        );
        let loaded = ledger.snapshot();
        assert_eq!((loaded.loaded_shell, loaded.next_shell), (1, 1));
        assert!(loaded.can_fire);
        assert_eq!(loaded.last_intent_seq, 2);
    }

    #[test]
    fn pending_trigger_queues_shell_change_without_invalidating_frozen_launch() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 31);
        let intent = ammo_intent(
            1,
            1,
            PlayerAmmoIntentAction::SelectCurrent { shell_index: 1 },
        );
        assert_eq!(
            ledger.admit_intent_deferred_until_launch(intent, true),
            Ok(PlayerAmmoIntentAdmission::New(
                PlayerAmmoIntentOutcome::Queued { shell_index: 1 }
            ))
        );
        let queued = ledger.snapshot();
        assert_eq!((queued.loaded_shell, queued.next_shell), (0, 1));
        assert_eq!(
            ledger.admit_launch(launch(1, 1, 0)),
            Ok(PlayerAmmoLaunchAdmission::New)
        );
        assert_eq!(ledger.snapshot().remaining, vec![2, 2, 1]);
        assert_eq!(
            ledger.admit_intent(intent, true),
            Ok(PlayerAmmoIntentAdmission::ExactRetry(
                PlayerAmmoIntentOutcome::Queued { shell_index: 1 }
            ))
        );
    }

    #[test]
    fn intuition_is_replay_stable_and_uses_independent_loader_lanes() {
        assert!(!deterministic_intuition_success(4, 2, 7, 1, 0).unwrap());
        let samples = (1..=128)
            .map(|intent_seq| deterministic_intuition_success(4, 2, 7, intent_seq, 1).unwrap())
            .collect::<Vec<_>>();
        assert!(samples.iter().any(|value| *value));
        assert!(samples.iter().any(|value| !*value));
        assert_eq!(
            samples[41],
            deterministic_intuition_success(4, 2, 7, 42, 1).unwrap()
        );
        for intent_seq in 1..=128 {
            let one = deterministic_intuition_success(4, 2, 7, intent_seq, 1).unwrap();
            let two = deterministic_intuition_success(4, 2, 7, intent_seq, 2).unwrap();
            assert!(!one || two);
        }
        assert_eq!(
            deterministic_intuition_success(4, 2, 7, 1, 17),
            Err(PlayerAmmoError::InvalidIntuitionChances)
        );
    }

    #[test]
    fn partial_clip_action_discards_loaded_slots_and_starts_full_reload() {
        let descriptor = descriptor(
            12,
            1.0,
            Some(ClipDescriptor {
                size: 3,
                intra_reload_seconds: 0.4,
            }),
        );
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 31);
        ledger.admit_launch(launch(1, 1, 0)).unwrap();
        assert_eq!(ledger.snapshot().clip_remaining, 2);

        assert_eq!(
            ledger.admit_intent(
                ammo_intent(1, 1, PlayerAmmoIntentAction::ReloadPartialClip),
                false,
            ),
            Ok(PlayerAmmoIntentAdmission::New(
                PlayerAmmoIntentOutcome::Reloading { shell_index: 0 }
            ))
        );
        let reloading = ledger.snapshot();
        assert_eq!(reloading.clip_remaining, 0);
        assert_eq!(reloading.reload_duration_seconds, 1.0);
        assert_eq!(reloading.reload_remaining_seconds, 1.0);
        assert!(!reloading.can_fire);
    }

    #[test]
    fn invalid_ammo_intent_never_consumes_sequence_or_revision() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        ledger.admit_input(1).unwrap();
        let before = ledger.snapshot();

        assert_eq!(
            ledger.admit_intent(
                ammo_intent(
                    2,
                    1,
                    PlayerAmmoIntentAction::SelectCurrent { shell_index: 1 },
                ),
                false,
            ),
            Err(PlayerAmmoError::IntentSequenceGap {
                last: 0,
                received: 2,
            })
        );
        assert_eq!(ledger.snapshot(), before);
        assert_eq!(
            ledger.admit_intent(
                ammo_intent(
                    1,
                    0,
                    PlayerAmmoIntentAction::SelectCurrent { shell_index: 1 },
                ),
                false,
            ),
            Err(PlayerAmmoError::IntentInputBinding)
        );
        assert_eq!(ledger.snapshot(), before);
    }

    #[test]
    fn bounded_receipts_distinguish_exact_and_stale_retries() {
        let descriptor = single_shell_descriptor(200);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(1)).unwrap();
        for sequence in 1..=129 {
            ledger.admit_input(sequence).unwrap();
            ledger.advance_tick(sequence, 0.0).unwrap();
            ledger.admit_launch(launch(sequence, sequence, 0)).unwrap();
        }
        assert_eq!(ledger.input_receipts.len(), MAX_PLAYER_AMMO_RECEIPTS);
        assert_eq!(ledger.launch_receipts.len(), MAX_PLAYER_AMMO_RECEIPTS);
        assert_eq!(
            ledger.admit_input(1),
            Err(PlayerAmmoError::StaleInputRetry { sequence: 1 })
        );
        assert_eq!(
            ledger.admit_input(2),
            Ok(PlayerAmmoInputAdmission::ExactRetry)
        );
        assert_eq!(
            ledger.admit_launch(launch(1, 1, 0)),
            Err(PlayerAmmoError::StaleLaunchRetry { sequence: 1 })
        );
        assert_eq!(
            ledger.admit_launch(launch(2, 2, 0)),
            Ok(PlayerAmmoLaunchAdmission::ExactRetry)
        );
    }

    #[test]
    fn invalid_tick_or_reload_factor_does_not_partially_advance() {
        let descriptor = descriptor(6, 1.0, None);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(3)).unwrap();
        let before = ledger.snapshot();
        assert_eq!(
            ledger.advance_tick(2, 1.0),
            Err(PlayerAmmoError::TickSequence {
                last: 0,
                received: 2,
            })
        );
        assert_eq!(
            ledger.advance_tick(1, f64::NAN),
            Err(PlayerAmmoError::InvalidReloadFactor)
        );
        assert_eq!(ledger.snapshot(), before);
    }

    #[test]
    fn physical_burst_clock_releases_all_overdue_edges_at_logical_times() {
        let descriptor = PhysicalBurstDescriptor::new(3, 0.01).unwrap();
        let mut clock = PhysicalBurstClock::new(0, 0);
        let first = match clock.arm(1, 2, descriptor, 5, 4, 0).unwrap() {
            PhysicalBurstAdmission::New { first } => first,
            PhysicalBurstAdmission::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(
            first,
            PhysicalBurstEdge {
                shot_seq: 1,
                burst_group_seq: 1,
                burst_index: 0,
                burst_count: 3,
                shell_index: 2,
                due_time_us: 0,
            }
        );
        let due = clock.advance_tick(1).unwrap();
        assert_eq!(
            due.iter()
                .map(|edge| (edge.shot_seq, edge.burst_index, edge.due_time_us))
                .collect::<Vec<_>>(),
            vec![(2, 1, 10_000), (3, 2, 20_000)]
        );
        assert!(!clock.active());
        assert_eq!(clock.last_shot_seq(), 3);
    }

    #[test]
    fn physical_burst_clock_clamps_and_fences_exact_retry_identity() {
        let raw = PhysicalBurstDescriptor {
            count: 4,
            interval_seconds: 0.1,
        };
        let mut clock = PhysicalBurstClock::new(7, 9);
        let first = match clock.arm(10, 1, raw, 2, 3, 7).unwrap() {
            PhysicalBurstAdmission::New { first } => first,
            PhysicalBurstAdmission::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(first.burst_count, 2);
        assert_eq!(
            clock.arm(10, 1, raw, 2, 3, 7),
            Ok(PhysicalBurstAdmission::ExactRetry { first })
        );
        assert_eq!(
            clock.arm(10, 1, raw, 3, 3, 7),
            Err(PhysicalBurstError::ConflictingRetry)
        );
        assert_eq!(clock.advance_tick(9), Err(PhysicalBurstError::TickSequence));
        assert_eq!(clock.current_tick(), 7);
        assert!(clock.cancel());
        assert!(!clock.cancel());

        let mut exhausted = PhysicalBurstClock::new(u64::MAX, 0);
        let before = (exhausted.last_shot_seq(), exhausted.active());
        assert_eq!(
            exhausted.arm(
                1,
                0,
                PhysicalBurstDescriptor::new(2, 0.1).unwrap(),
                2,
                2,
                u64::MAX,
            ),
            Err(PhysicalBurstError::SequenceExhausted)
        );
        assert_eq!((exhausted.last_shot_seq(), exhausted.active()), before);
        assert_eq!(
            exhausted.advance_tick(u64::MAX),
            Err(PhysicalBurstError::SequenceExhausted)
        );
    }

    #[test]
    fn every_physical_burst_edge_debits_ammo_and_only_final_starts_reload() {
        let descriptor = descriptor(
            5,
            1.0,
            Some(ClipDescriptor {
                size: 3,
                intra_reload_seconds: 0.4,
            }),
        );
        let mut ledger =
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![5, 0, 0], 0)
                .unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 30);

        let burst = |index| PlayerAmmoBurst {
            group_seq: 1,
            index,
            count: 3,
        };
        ledger
            .admit_physical_launch(launch(1, 1, 0), burst(0))
            .unwrap();
        let first = ledger.snapshot();
        assert_eq!((first.remaining[0], first.clip_remaining), (4, 2));
        assert!(first.burst_active);
        assert!(first.reload_ready);

        ledger
            .admit_physical_launch(launch(2, 1, 0), burst(1))
            .unwrap();
        assert_eq!(
            (
                ledger.snapshot().remaining[0],
                ledger.snapshot().clip_remaining
            ),
            (3, 1)
        );
        ledger
            .admit_physical_launch(launch(3, 1, 0), burst(2))
            .unwrap();
        let final_edge = ledger.snapshot();
        assert_eq!((final_edge.remaining[0], final_edge.clip_remaining), (2, 0));
        assert!(!final_edge.burst_active);
        assert!(!final_edge.reload_ready);
        assert_eq!(final_edge.reload_duration_seconds, 1.0);

        let committed = ledger.snapshot();
        assert_eq!(
            ledger.admit_physical_launch(launch(2, 1, 0), burst(1)),
            Ok(PlayerAmmoLaunchAdmission::ExactRetry)
        );
        assert_eq!(ledger.snapshot(), committed);
        assert_eq!(
            ledger.admit_physical_launch(
                launch(2, 1, 0),
                PlayerAmmoBurst {
                    group_seq: 1,
                    index: 1,
                    count: 2,
                },
            ),
            Err(PlayerAmmoError::ConflictingLaunchRetry { sequence: 2 })
        );
        assert_eq!(ledger.snapshot(), committed);
    }

    #[test]
    fn exhausting_loaded_shell_mid_clip_forces_full_reload_and_capped_refill() {
        let descriptor = descriptor(
            3,
            1.0,
            Some(ClipDescriptor {
                size: 3,
                intra_reload_seconds: 0.2,
            }),
        );
        let mut ledger =
            PlayerAmmoLedger::new_exact_loaded(7, 0, &descriptor, &profile(3), vec![1, 2, 0], 0)
                .unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 30);
        ledger.admit_launch(launch(1, 1, 0)).unwrap();
        let empty_type = ledger.snapshot();
        assert_eq!(empty_type.remaining, vec![0, 2, 0]);
        assert_eq!(empty_type.clip_remaining, 0);
        assert_eq!(empty_type.loaded_shell, 0);
        assert_eq!(empty_type.reload_duration_seconds, 1.0);

        advance_through(&mut ledger, 59);
        assert_eq!(ledger.snapshot().clip_remaining, 0);
        ledger.advance_tick(60, 1.0).unwrap();
        let refilled = ledger.snapshot();
        assert_eq!(refilled.loaded_shell, 1);
        assert_eq!(refilled.clip_remaining, 2);
        assert!(refilled.can_fire);
    }

    #[test]
    fn exhausted_inventory_rejects_without_consuming_shot_sequence() {
        let descriptor = single_shell_descriptor(0);
        let mut ledger = PlayerAmmoLedger::new(7, 0, &descriptor, &profile(1)).unwrap();
        ledger.admit_input(1).unwrap();
        advance_through(&mut ledger, 30);
        let before = ledger.snapshot();
        assert!(before.reload_ready);
        assert!(!before.can_fire);
        assert_eq!(
            ledger.admit_launch(launch(1, 1, 0)),
            Err(PlayerAmmoError::OutOfAmmo)
        );
        assert_eq!(ledger.snapshot(), before);
        assert_eq!(ledger.last_shot_seq(), 0);
    }
}
