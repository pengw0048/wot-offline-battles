//! Strict protocol-v5 messages consumed by the #1513 LAN client.
//!
//! This module is deliberately only a serializer/contract boundary.  The
//! simulation remains 30 Hz and the replication scheduler decides when to
//! publish the ordinary 15 Hz snapshots.  The hidden native client is an
//! oracle only; every visible authority field emitted here is the Rust server
//! identity (`0`).

use crate::combat::{DamageCommit, DamageSource, VehicleCombatState, VehicleKey, VehicleKind};
use crate::config::BotLineupEntry;
use crate::critical_damage::MAX_CRITICAL_DEVICE_HP;
use crate::destructible::{DestructibleKind as LedgerDestructibleKind, StoredDestructible};
use crate::player_ammo::PlayerAmmoBurst;
use crate::projectile::{ProjectileOutcome, ProjectileRecord, ProjectileResolution, SourceShot};
use crate::room::{BotTierMode, Team};
use crate::rules::StandardRules;
use crate::statistics::{AssistAward, AssistCategory, VehicleStatistics};
use crate::wire::{WireError, WireObject, LAN_PROTOCOL_VERSION};
use serde::Serialize;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::PI;
use thiserror::Error;

pub const CLIENT_BUILD_0922: &str = "wot-0.9.22.0.1-cn-1513";
pub const SERVER_AUTHORITY_ID: i64 = 0;
pub const SIMULATION_HZ: u64 = 30;
pub const SNAPSHOT_HZ: u64 = 15;
pub const SNAPSHOT_TICK_DIVISOR: u64 = SIMULATION_HZ / SNAPSHOT_HZ;

const MAX_EXACT_INT: u64 = 2_147_483_647;
const MAX_MOTION_TIME_US: u64 = 10_000_000_000_000_000;
const MAX_PLAYERS: usize = 64;
const MAX_BOTS: usize = 30;
const MAX_DESTRUCTIBLES: usize = 4096;
const MAX_PROJECTILES: usize = 256;
const MAX_EVENTS: usize = 256;
const MAX_COMBAT_HEALTH: u32 = 100_000;
const MAX_OUTFIT_BYTES: usize = 64 * 1024;
const MAX_COMPACT_DESCRIPTOR_BYTES: usize = 64 * 1024;
const MAX_CONTACT_MEMORY_SECONDS: f64 = 12.0;
const MAX_RAM_CONTACT_RESULTS: usize = 128;

#[derive(Debug, Error)]
pub enum ClientReplicationError {
    #[error("invalid #1513 client replication field: {0}")]
    Invalid(&'static str),
    #[error(transparent)]
    Wire(#[from] WireError),
    #[error("could not serialize #1513 client replication payload: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, ClientReplicationError>;

/// Round-scoped values shared by battle lifecycle and snapshot messages.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FrameScope {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub server_tick: u64,
    pub server_time_ms: u64,
    pub state_revision: u64,
}

impl FrameScope {
    fn validate(self) -> Result<()> {
        if self.round_id == 0 {
            return invalid("round_id");
        }
        for (name, value) in [
            ("authority_epoch", self.authority_epoch),
            ("server_time_ms", self.server_time_ms),
            ("state_revision", self.state_revision),
        ] {
            if value > MAX_EXACT_INT {
                return invalid(name);
            }
        }
        Ok(())
    }
}

/// The ordinary cadence used by the existing 30 Hz replication scheduler.
/// Reliable event/lineage barriers may still force an extra snapshot.
pub fn ordinary_snapshot_due(server_tick: u64) -> bool {
    server_tick % SNAPSHOT_TICK_DIVISOR == 0
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CombatPhase {
    Loading,
    Prebattle,
    Battle,
    Finished,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct TimingState {
    pub phase: CombatPhase,
    pub start_in_ms: u64,
    pub remaining_ms: u64,
    pub duration_ms: u64,
}

impl TimingState {
    fn validate(&self) -> Result<()> {
        if self.duration_ms == 0
            || self.remaining_ms > self.duration_ms
            || self.start_in_ms > MAX_EXACT_INT
            || self.remaining_ms > MAX_EXACT_INT
            || self.duration_ms > MAX_EXACT_INT
        {
            return invalid("timing");
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq)]
pub struct Point3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Point3 {
    fn validate(self, xz: f64, y_low: f64, y_high: f64) -> Result<()> {
        if !self.x.is_finite()
            || !self.y.is_finite()
            || !self.z.is_finite()
            || self.x.abs() > xz
            || self.z.abs() > xz
            || !(y_low..=y_high).contains(&self.y)
        {
            return invalid("position");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct PlayerState {
    pub id: u64,
    pub name: String,
    pub vehicle: String,
    pub vehicle_compact_descr: String,
    pub team: u8,
    pub slot: u8,
    pub world_pose: bool,
    pub spawn_x: f64,
    pub spawn_z: f64,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub aim_yaw: f64,
    pub gun_pitch: f64,
    pub forward: f64,
    pub turn: f64,
    pub speed: f64,
    pub input_seq: u64,
    pub landing_observation_seq: u64,
    pub up_cosine: f64,
    pub siege_state: u8,
    pub siege_time_left_ms: u32,
    pub fire_seq: u64,
    pub shell_index: u8,
    pub next_shell_index: u8,
    pub ammo_remaining: Vec<u16>,
    pub ammo_reload_pending: bool,
    pub reload_time: f64,
    pub reload_duration: f64,
    pub clip: u16,
    pub clip_size: u16,
    pub burst_active: bool,
    pub burst_group_seq: u64,
    pub burst_count: u16,
    pub burst_next_index: u16,
    pub burst_interval: f64,
    pub burst_time_left: f64,
    pub burst_shell_index: u8,
    pub health: u32,
    pub max_health: u32,
    pub alive: bool,
    pub death_reason: u8,
    pub display_health: u32,
    pub frags: i32,
    pub team_killer: bool,
    pub death_attacker_kind: String,
    pub death_attacker_id: u64,
    pub stun_end_server_time_ms: u64,
    pub stun_attacker_kind: String,
    pub stun_attacker_id: u64,
    pub critical_revision: u64,
    pub critical_base_revision: u64,
    pub critical_ack_seq: u64,
    pub equipment_states: Vec<Value>,
    pub equipment_revision: u64,
    pub equipment_intent_seq: u64,
    pub equipment_intent_result: Value,
    pub ram_contact_admitted_seq: u64,
    pub ram_contact_resolved_seq: u64,
    pub player_ram_contact_admitted_seq: u64,
    pub player_ram_contact_resolved_seq: u64,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub ram_contact_results: Vec<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outfits: Option<BTreeMap<u8, String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub effective_params: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ram_contact: Option<Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub ram_contacts: Vec<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub critical: Option<Value>,
}

impl PlayerState {
    fn validate(&self, require_outfits: bool) -> Result<()> {
        if self.id == 0
            || self.id > MAX_EXACT_INT
            || !valid_text(&self.name, 32)
            || !valid_text(&self.vehicle, 96)
            || (!self.vehicle_compact_descr.is_empty()
                && !canonical_base64(&self.vehicle_compact_descr, MAX_COMPACT_DESCRIPTOR_BYTES))
            || !matches!(self.team, 1 | 2)
            || self.slot >= 15
        {
            return invalid("players.identity");
        }
        if !all_finite(&[
            self.spawn_x,
            self.spawn_z,
            self.x,
            self.y,
            self.z,
            self.yaw,
            self.pitch,
            self.roll,
            self.aim_yaw,
            self.gun_pitch,
            self.forward,
            self.turn,
            self.speed,
            self.up_cosine,
        ]) || self.spawn_x.abs() > 2_000.0
            || self.spawn_z.abs() > 2_000.0
            || self.x.abs() > 2_000.0
            || !(-1_000.0..=1_000.0).contains(&self.y)
            || self.z.abs() > 2_000.0
            || self.pitch.abs() > 0.61
            || self.roll.abs() > 0.61
            || self.gun_pitch.abs() > 1.2
            || self.forward.abs() > 1.0
            || self.turn.abs() > 1.0
            || self.speed.abs() > 200.0
            || !(-1.0..=1.0).contains(&self.up_cosine)
        {
            return invalid("players.pose");
        }
        if self.siege_state > 3
            || self.siege_time_left_ms > 5_000
            || (matches!(self.siege_state, 1 | 3) && self.siege_time_left_ms == 0)
            || (matches!(self.siege_state, 0 | 2) && self.siege_time_left_ms != 0)
        {
            return invalid("players.siege");
        }
        if self.shell_index > 15
            || self.next_shell_index > 15
            || self.ammo_remaining.is_empty()
            || self.ammo_remaining.len() > 16
            || usize::from(self.shell_index) >= self.ammo_remaining.len()
            || usize::from(self.next_shell_index) >= self.ammo_remaining.len()
            || self.ammo_remaining.iter().any(|count| *count > 1_000)
            || self
                .ammo_remaining
                .iter()
                .map(|count| u64::from(*count))
                .sum::<u64>()
                > 1_000
            || !finite_range(self.reload_duration, f64::MIN_POSITIVE, 300.0)
            || !finite_range(self.reload_time, 0.0, self.reload_duration)
            || self.clip_size == 0
            || self.clip_size > 64
            || self.clip > self.clip_size
            || self.burst_count > 64
            || self.burst_next_index > self.burst_count
            || self.burst_shell_index > 15
            || !finite_range(self.burst_interval, 0.0, 10.0)
            || !finite_range(self.burst_time_left, 0.0, 10.0)
            || (self.burst_count > 1 && self.burst_interval <= 0.0)
            || (self.burst_count == 0
                && (self.burst_active
                    || self.burst_group_seq != 0
                    || self.burst_next_index != 0
                    || self.burst_interval != 0.0
                    || self.burst_time_left != 0.0))
            || (self.burst_count > 0
                && (self.burst_group_seq == 0
                    || self.fire_seq
                        != self
                            .burst_group_seq
                            .saturating_add(u64::from(self.burst_next_index))
                            .saturating_sub(1)))
            || (self.burst_active
                && (self.burst_count < 2
                    || self.burst_next_index == 0
                    || self.burst_next_index >= self.burst_count
                    || self.burst_time_left > self.burst_interval))
            || (!self.burst_active && self.burst_time_left != 0.0)
            || self.max_health == 0
            || self.max_health > 100_000
            || self.health > self.max_health
            || self.display_health > self.max_health
            || self.alive != (self.health > 0)
            || !(-30..=30).contains(&self.frags)
            || self.ram_contact_resolved_seq > self.ram_contact_admitted_seq
            || self.player_ram_contact_resolved_seq > self.player_ram_contact_admitted_seq
        {
            return invalid("players.combat");
        }
        for value in [
            self.input_seq,
            self.landing_observation_seq,
            self.fire_seq,
            self.burst_group_seq,
            self.critical_revision,
            self.critical_base_revision,
            self.critical_ack_seq,
            self.equipment_revision,
            self.equipment_intent_seq,
            self.ram_contact_admitted_seq,
            self.ram_contact_resolved_seq,
            self.player_ram_contact_admitted_seq,
            self.player_ram_contact_resolved_seq,
        ] {
            if value > MAX_EXACT_INT {
                return invalid("players.revision");
            }
        }
        let equipment_result = self.equipment_intent_result.as_object().filter(|result| {
            result.len() == 3
                && result.get("intent_seq").and_then(Value::as_u64)
                    == Some(self.equipment_intent_seq)
                && result.get("accepted").is_some_and(Value::is_boolean)
                && result
                    .get("reason")
                    .and_then(Value::as_str)
                    .is_some_and(|reason| reason.len() <= 64)
        });
        if self.equipment_states.len() > 3
            || self.equipment_states.iter().any(|state| !state.is_object())
            || equipment_result.is_none()
        {
            return invalid("players.equipment");
        }
        validate_attacker(&self.death_attacker_kind, self.death_attacker_id)?;
        validate_attacker(&self.stun_attacker_kind, self.stun_attacker_id)?;
        if self.stun_end_server_time_ms > MAX_EXACT_INT
            || (self.stun_end_server_time_ms == 0) != self.stun_attacker_kind.is_empty()
        {
            return invalid("players.stun");
        }
        if require_outfits && (self.outfits.is_none() || self.effective_params.is_none()) {
            return invalid("players.outfits");
        }
        if let Some(outfits) = &self.outfits {
            if outfits.len() > 3 {
                return invalid("players.outfits");
            }
            let mut total = 0usize;
            for (season, encoded) in outfits {
                if !matches!(*season, 1 | 2 | 4) || !canonical_base64(encoded, MAX_OUTFIT_BYTES) {
                    return invalid("players.outfits");
                }
                total = total.saturating_add(decoded_base64_len(encoded).unwrap_or(usize::MAX));
            }
            if total > MAX_OUTFIT_BYTES * 3 {
                return invalid("players.outfits");
            }
        }
        if self.effective_params.as_ref().is_some_and(|value| {
            value
                .as_object()
                .and_then(|fields| fields.get("version"))
                .and_then(Value::as_u64)
                != Some(1)
                || serde_json::to_vec(value).is_err()
        }) {
            return invalid("players.effective_params");
        }
        if self
            .ram_contact
            .as_ref()
            .is_some_and(|value| !value.is_object())
            || self.ram_contacts.len() > MAX_BOTS
            || self.ram_contacts.iter().any(|value| !value.is_object())
            || self.ram_contact_results.len() > MAX_RAM_CONTACT_RESULTS
            || {
                let mut previous = 0;
                self.ram_contact_results.iter().any(|value| {
                    let Some(result) = value.as_object() else {
                        return true;
                    };
                    let sequence = result.get("seq").and_then(Value::as_u64);
                    let outcome = result.get("outcome").and_then(Value::as_str);
                    let invalid = result.len() != 2
                        || sequence.is_none_or(|sequence| {
                            sequence == 0
                                || sequence <= previous
                                || sequence > self.ram_contact_resolved_seq
                        })
                        || !matches!(outcome, Some("damage" | "contact" | "unavailable"));
                    if let Some(sequence) = sequence {
                        previous = sequence;
                    }
                    invalid
                })
            }
            || self
                .critical
                .as_ref()
                .is_some_and(|value| !value.is_object())
        {
            return invalid("players.extended_state");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ContactState {
    pub observing_team: u8,
    pub target_kind: String,
    pub target_id: u64,
    pub target_team: u8,
    pub visible: bool,
    pub fresh: bool,
    pub time_left: f64,
    pub visible_by_bot_ids: Vec<u64>,
    pub visible_by_player_ids: Vec<u64>,
    pub shootable_by_bot_ids: Vec<u64>,
}

impl ContactState {
    fn validate(&self) -> Result<()> {
        if !matches!(self.observing_team, 1 | 2)
            || !matches!(self.target_team, 1 | 2)
            || self.observing_team == self.target_team
            || !matches!(self.target_kind.as_str(), "human" | "bot")
            || self.target_id == 0
            || self.target_id > MAX_EXACT_INT
            || !self.time_left.is_finite()
            || !(0.0..=MAX_CONTACT_MEMORY_SECONDS).contains(&self.time_left)
            || self.visible != (self.time_left > 0.0)
            || self.fresh
                != (!self.visible_by_bot_ids.is_empty() || !self.visible_by_player_ids.is_empty())
            || self.fresh && !self.visible
            || !self.fresh && !self.shootable_by_bot_ids.is_empty()
            || !valid_contact_ids(&self.visible_by_bot_ids, MAX_BOTS)
            || !valid_contact_ids(&self.visible_by_player_ids, MAX_PLAYERS)
            || !valid_contact_ids(&self.shootable_by_bot_ids, MAX_BOTS)
        {
            return invalid("snapshot.contacts");
        }
        Ok(())
    }
}

fn valid_contact_ids(values: &[u64], limit: usize) -> bool {
    values.len() <= limit
        && values.windows(2).all(|pair| pair[0] < pair[1])
        && values
            .iter()
            .all(|value| *value > 0 && *value <= MAX_EXACT_INT)
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotShellProfile {
    pub index: u8,
    pub kind: String,
    pub penetration: f64,
    pub damage: f64,
    pub speed: f64,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotProfile {
    pub class_tag: String,
    pub dominant_role: String,
    pub roles: BTreeMap<String, f64>,
    pub desired_range: f64,
    pub fire_range: f64,
    pub speed: f64,
    pub armor: f64,
    pub shells: Vec<BotShellProfile>,
}

impl BotProfile {
    fn validate(&self) -> Result<()> {
        if !valid_text(&self.class_tag, 32)
            || !valid_text(&self.dominant_role, 32)
            || self.roles.len() > 8
            || self.shells.len() > 5
            || !finite_range(self.desired_range, 0.0, 2_000.0)
            || !finite_range(self.fire_range, 0.0, 2_500.0)
            || !finite_range(self.speed, 0.0, 200.0)
            || !finite_range(self.armor, 0.0, 10_000.0)
        {
            return invalid("bot_profile");
        }
        for (role, weight) in &self.roles {
            if !valid_text(role, 32) || !finite_range(*weight, 0.0, 1.0) {
                return invalid("bot_profile.roles");
            }
        }
        for shell in &self.shells {
            if shell.index > 9
                || !valid_text(&shell.kind, 32)
                || !finite_range(shell.penetration, 0.0, 10_000.0)
                || !finite_range(shell.damage, 0.0, 10_000.0)
                || !finite_range(shell.speed, 0.0, 10_000.0)
            {
                return invalid("bot_profile.shells");
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct RouteWaypoint {
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub hold: bool,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotRoute {
    pub id: String,
    pub waypoints: Vec<RouteWaypoint>,
}

impl BotRoute {
    fn validate(&self) -> Result<()> {
        if !valid_text(&self.id, 64) || self.waypoints.len() > 32 {
            return invalid("bot_manifest.route");
        }
        for point in &self.waypoints {
            Point3 {
                x: point.x,
                y: point.y,
                z: point.z,
            }
            .validate(2_000.0, -1_000.0, 1_000.0)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotRosterEntry {
    pub id: u64,
    pub team: u8,
    pub slot: u8,
    pub name: String,
}

impl BotRosterEntry {
    fn validate(&self) -> Result<()> {
        if self.id == 0
            || self.id > MAX_EXACT_INT
            || !matches!(self.team, 1 | 2)
            || self.slot >= 15
            || !valid_text(&self.name, 32)
        {
            return invalid("bots");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotManifestEntry {
    pub id: u64,
    pub team: u8,
    pub slot: u8,
    pub name: String,
    pub vehicle: String,
    pub max_health: u32,
    pub health: u32,
    pub profile: BotProfile,
    pub route: BotRoute,
}

impl BotManifestEntry {
    fn validate(&self) -> Result<()> {
        if self.id == 0
            || self.id > MAX_EXACT_INT
            || !matches!(self.team, 1 | 2)
            || self.slot >= 15
            || !valid_text(&self.name, 32)
            || !valid_text(&self.vehicle, 96)
            || self.max_health == 0
            || self.max_health > 100_000
            || self.health > self.max_health
        {
            return invalid("bot_manifest");
        }
        self.profile.validate()?;
        self.route.validate()
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotState {
    pub id: u64,
    pub team: u8,
    pub slot: u8,
    pub name: String,
    pub vehicle: String,
    pub world_pose: bool,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub aim_yaw: f64,
    pub gun_pitch: f64,
    pub movement_dir: i8,
    pub rotation_dir: i8,
    pub fire_seq: u64,
    pub shell_index: u8,
    pub next_shell_index: u8,
    pub ammo_remaining: Vec<u16>,
    pub ammo_reload_pending: bool,
    pub health: u32,
    pub max_health: u32,
    pub alive: bool,
    pub frags: i32,
    pub team_killer: bool,
    pub death_attacker_kind: String,
    pub death_attacker_id: u64,
    pub combat_revision: u64,
    pub combat_base_revision: u64,
    pub combat_ack_seq: u64,
    pub combat_fire_elapsed: f64,
    pub combat_fire_timer: f64,
    pub fire_attacker_kind: String,
    pub fire_attacker_id: u64,
    pub stun_end_server_time_ms: u64,
    pub stun_attacker_kind: String,
    pub stun_attacker_id: u64,
    pub equipment_states: Vec<Value>,
    pub critical: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub shot_yaw: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub shot_pitch: Option<f64>,
    pub death_reason: u8,
    pub display_health: u32,
}

impl BotState {
    fn validate(&self) -> Result<()> {
        if self.id == 0
            || self.id > MAX_EXACT_INT
            || !matches!(self.team, 1 | 2)
            || self.slot >= 15
            || !valid_text(&self.name, 32)
            || !valid_text(&self.vehicle, 96)
            || self.equipment_states.len() > 3
            || self.equipment_states.iter().any(|state| !state.is_object())
            || !self.critical.is_object()
        {
            return invalid("bots.identity");
        }
        if !all_finite(&[
            self.x,
            self.y,
            self.z,
            self.yaw,
            self.pitch,
            self.roll,
            self.aim_yaw,
            self.gun_pitch,
        ]) || self.x.abs() > 2_000.0
            || !(-1_000.0..=1_000.0).contains(&self.y)
            || self.z.abs() > 2_000.0
            || self.pitch.abs() > 0.61
            || self.roll.abs() > 0.61
            || self.gun_pitch.abs() > 1.2
            || !matches!(self.movement_dir, -1..=1)
            || !matches!(self.rotation_dir, -1..=1)
        {
            return invalid("bots.pose");
        }
        if self.shell_index > 9
            || self.next_shell_index > 9
            || self.ammo_remaining.len() > 5
            || self
                .ammo_remaining
                .iter()
                .map(|value| *value as u32)
                .sum::<u32>()
                > 1_000
            || (!self.ammo_remaining.is_empty()
                && (self.shell_index as usize >= self.ammo_remaining.len()
                    || self.next_shell_index as usize >= self.ammo_remaining.len()))
        {
            return invalid("bots.ammunition");
        }
        if self.max_health == 0
            || self.max_health > 100_000
            || self.health > self.max_health
            || self.display_health > self.max_health
            || self.alive != (self.health > 0)
            || !(-30..=30).contains(&self.frags)
            || self.fire_seq > MAX_EXACT_INT
            || self.combat_revision > MAX_EXACT_INT
            || self.combat_base_revision > self.combat_revision
            || self.combat_ack_seq > MAX_EXACT_INT
            || !finite_range(self.combat_fire_elapsed, 0.0, 10.0)
            || !self.combat_fire_timer.is_finite()
            || !(0.0..1.0).contains(&self.combat_fire_timer)
        {
            return invalid("bots.combat");
        }
        let fire_critical = self
            .critical
            .as_object()
            .and_then(|critical| critical.get("fire"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if !fire_critical && (self.combat_fire_elapsed != 0.0 || self.combat_fire_timer != 0.0) {
            return invalid("bots.fire_critical");
        }
        if self.shot_yaw.is_some() != self.shot_pitch.is_some()
            || self
                .shot_yaw
                .is_some_and(|value| !finite_range(value, -PI, PI))
            || self
                .shot_pitch
                .is_some_and(|value| !finite_range(value, -1.2, 1.2))
        {
            return invalid("bots.shot_angles");
        }
        validate_attacker(&self.death_attacker_kind, self.death_attacker_id)?;
        validate_attacker(&self.fire_attacker_kind, self.fire_attacker_id)?;
        validate_attacker(&self.stun_attacker_kind, self.stun_attacker_id)?;
        if self.stun_end_server_time_ms > MAX_EXACT_INT
            || (self.stun_end_server_time_ms == 0) != self.stun_attacker_kind.is_empty()
        {
            return invalid("bots.stun");
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum OrderTargetKind {
    Human,
    Bot,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotPersonality {
    pub aggression: f64,
    pub caution: f64,
    pub teamwork: f64,
    pub patience: f64,
    pub initiative: f64,
    pub adaptability: f64,
    pub jiggle: f64,
}

impl BotPersonality {
    fn validate(&self) -> Result<()> {
        if [
            self.aggression,
            self.caution,
            self.teamwork,
            self.patience,
            self.initiative,
            self.adaptability,
            self.jiggle,
        ]
        .into_iter()
        .any(|value| !finite_range(value, 0.0, 1.0))
        {
            return invalid("bot_orders.personality");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BotOrder {
    pub id: u64,
    pub team: u8,
    pub target_id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_kind: Option<OrderTargetKind>,
    pub aim_position: Option<Point3>,
    pub face_position: Option<Point3>,
    pub move_position: Point3,
    pub fire_allowed: bool,
    pub combat_mode: String,
    pub throttle_override: Option<f64>,
    pub desired_range: f64,
    pub fire_range: f64,
    pub route_id: String,
    pub route_index: usize,
    pub route_anchor: Point3,
    pub route_join: bool,
    pub personality: BotPersonality,
    pub profile: BotProfile,
    pub shell_index: u8,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cover_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub defense_base_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hull_angle_degrees: Option<f64>,
}

impl BotOrder {
    fn validate(&self) -> Result<()> {
        if self.id == 0
            || self.id > MAX_EXACT_INT
            || !matches!(self.team, 1 | 2)
            || self.target_id.is_some() != self.target_kind.is_some()
            || self
                .target_id
                .is_some_and(|id| id == 0 || id > MAX_EXACT_INT)
            || !valid_text(&self.combat_mode, 64)
            || !valid_text(&self.route_id, 64)
            || self.route_index > 15
            || self.shell_index > 9
            || !finite_range(self.desired_range, 0.0, 2_000.0)
            || !finite_range(self.fire_range, 0.0, 2_500.0)
            || self.fire_range < self.desired_range
            || self
                .throttle_override
                .is_some_and(|value| !finite_range(value, -1.0, 1.0))
            || self
                .hull_angle_degrees
                .is_some_and(|value| !finite_range(value, -45.0, 45.0))
            || self
                .cover_id
                .as_ref()
                .is_some_and(|value| !valid_text(value, 96))
            || self
                .defense_base_id
                .as_ref()
                .is_some_and(|value| !valid_text(value, 96))
        {
            return invalid("bot_orders");
        }
        if self.fire_allowed && self.target_id.is_none() {
            return invalid("bot_orders.fire_target");
        }
        for point in [
            self.aim_position,
            self.face_position,
            Some(self.move_position),
            Some(self.route_anchor),
        ]
        .into_iter()
        .flatten()
        {
            point.validate(2_000.0, -1_000.0, 1_000.0)?;
        }
        self.personality.validate()?;
        self.profile.validate()
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CaptureBaseState {
    pub points: u8,
    pub time_left: f64,
    pub invaders: u8,
    pub stopped: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RulesState {
    pub team_1: CaptureBaseState,
    pub team_2: CaptureBaseState,
}

impl RulesState {
    /// Convert the Rust standard-mode capture state into the snapshot contract.
    /// Capture progress is snapshot state; `capture` is not a client event kind.
    pub fn from_standard_rules(rules: &StandardRules) -> Result<Self> {
        fn base(rules: &StandardRules, team: Team) -> Result<CaptureBaseState> {
            let state = rules.state(team);
            let value = CaptureBaseState {
                points: u8::try_from(state.points)
                    .map_err(|_| ClientReplicationError::Invalid("rules.bases.points"))?,
                time_left: state.time_left_seconds,
                invaders: u8::try_from(state.invaders)
                    .map_err(|_| ClientReplicationError::Invalid("rules.bases.invaders"))?,
                stopped: state.stopped,
            };
            Ok(value)
        }

        let value = Self {
            team_1: base(rules, Team::One)?,
            team_2: base(rules, Team::Two)?,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<()> {
        for base in [&self.team_1, &self.team_2] {
            if base.points > 100
                || !base.time_left.is_finite()
                || base.time_left < 0.0
                || base.invaders > MAX_BOTS as u8
            {
                return invalid("rules.bases");
            }
        }
        Ok(())
    }

    fn to_value(&self) -> Result<Value> {
        self.validate()?;
        let mut bases = Map::new();
        bases.insert("1".to_owned(), serde_json::to_value(&self.team_1)?);
        bases.insert("2".to_owned(), serde_json::to_value(&self.team_2)?);
        let mut rules = Map::new();
        rules.insert("bases".to_owned(), Value::Object(bases));
        Ok(Value::Object(rules))
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum DestructibleKind {
    Tree,
    Column,
    Fragile,
    Module,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct DestructibleState {
    pub destructible_kind: DestructibleKind,
    pub chunk_id: i64,
    pub item_index: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mat_kind: Option<u16>,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub fall_yaw: f64,
    pub speed: f64,
    pub is_shot: bool,
    pub revision: u64,
}

impl DestructibleState {
    /// Convert one server-owned ledger row to the exact client event/snapshot
    /// shape. Reports attributed to a retired client/worker authority fail
    /// closed instead of being relabelled as server-owned.
    pub fn from_stored(stored: &StoredDestructible) -> Result<Self> {
        if stored.reported_by != SERVER_AUTHORITY_ID {
            return invalid("destructibles.reported_by");
        }
        let receipt = &stored.receipt;
        let value = Self {
            destructible_kind: match receipt.key.kind {
                LedgerDestructibleKind::Tree => DestructibleKind::Tree,
                LedgerDestructibleKind::Column => DestructibleKind::Column,
                LedgerDestructibleKind::Fragile => DestructibleKind::Fragile,
                LedgerDestructibleKind::Module => DestructibleKind::Module,
            },
            chunk_id: receipt.key.chunk_id,
            item_index: receipt.key.item_index,
            mat_kind: receipt.key.material_kind,
            x: receipt.x,
            y: receipt.y,
            z: receipt.z,
            fall_yaw: receipt.fall_yaw,
            speed: receipt.speed,
            is_shot: receipt.is_shot,
            revision: stored.revision,
        };
        value.validate(stored.revision)?;
        Ok(value)
    }

    fn validate(&self, ledger_revision: u64) -> Result<()> {
        if !(-2_147_483_648..=4_294_967_295).contains(&self.chunk_id)
            || self.item_index > 1_048_575
            || (self.destructible_kind == DestructibleKind::Module && self.mat_kind.is_none())
            || !all_finite(&[self.x, self.y, self.z, self.fall_yaw, self.speed])
            || self.x.abs() > 5_000.0
            || !(-1_000.0..=3_000.0).contains(&self.y)
            || self.z.abs() > 5_000.0
            || self.fall_yaw.abs() > PI * 4.0
            || self.speed.abs() > 200.0
            || self.revision == 0
            || self.revision > ledger_revision
        {
            return invalid("destructibles");
        }
        Ok(())
    }

    fn to_value(&self) -> Result<Value> {
        let mut value = value_object(serde_json::to_value(self)?)?;
        value.insert("kind".to_owned(), Value::String("destructible".to_owned()));
        value.insert(
            "reported_by".to_owned(),
            Value::Number(SERVER_AUTHORITY_ID.into()),
        );
        Ok(Value::Object(value))
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ProjectileWireState {
    pub projectile_id: String,
    pub shooter_kind: VehicleKind,
    pub shooter_id: u64,
    pub shot_seq: u64,
    pub source_vehicle: String,
    pub source_shot: SourceShot,
    pub shell_index: u8,
    pub team: u8,
    pub origin: [f64; 3],
    pub velocity: [f64; 3],
    pub gravity: f64,
    pub max_distance: f64,
    pub max_time_ms: u64,
    pub is_he: bool,
    pub splash_radius: f64,
    pub penetration_factor: f64,
    pub launch_server_time_ms: u64,
    pub checked_through_ms: u64,
    pub checked_distance: f64,
    pub piercing_loss: f64,
    pub authority_epoch: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fire_intent_seq: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fire_input_seq: Option<u64>,
}

impl From<&ProjectileRecord> for ProjectileWireState {
    fn from(record: &ProjectileRecord) -> Self {
        Self {
            projectile_id: record.projectile_id.clone(),
            shooter_kind: record.launch.shooter.kind,
            shooter_id: record.launch.shooter.id,
            shot_seq: record.launch.shot_seq,
            source_vehicle: record.source_vehicle.clone(),
            source_shot: record.launch.source_shot.clone(),
            shell_index: record.launch.shell_index,
            team: record.team,
            origin: [
                record.launch.origin.x,
                record.launch.origin.y,
                record.launch.origin.z,
            ],
            velocity: [
                record.launch.velocity.x,
                record.launch.velocity.y,
                record.launch.velocity.z,
            ],
            gravity: record.launch.gravity,
            max_distance: record.launch.max_distance,
            max_time_ms: record.launch.max_time_ms,
            is_he: record.launch.is_he,
            splash_radius: record.launch.splash_radius,
            penetration_factor: record.launch.penetration_factor,
            launch_server_time_ms: record.launch_server_time_ms,
            checked_through_ms: record.checked_through_ms,
            checked_distance: record.checked_distance,
            piercing_loss: record.piercing_loss,
            authority_epoch: record.launch.authority_epoch,
            fire_intent_seq: record.launch.fire_intent_seq,
            fire_input_seq: record.launch.fire_input_seq,
        }
    }
}

impl ProjectileWireState {
    fn validate(&self, authority_epoch: u64, server_time_ms: u64) -> Result<()> {
        if !valid_projectile_id(&self.projectile_id)
            || self.shooter_id == 0
            || self.shooter_id > MAX_EXACT_INT
            || self.shot_seq == 0
            || self.shot_seq > MAX_EXACT_INT
            || !valid_text(&self.source_vehicle, 128)
            || self.shell_index > 9
            || !matches!(self.team, 1 | 2)
            || self.authority_epoch != authority_epoch
            || self.authority_epoch > MAX_EXACT_INT
        {
            return invalid("projectiles.identity");
        }
        let player_intent = self.fire_intent_seq.zip(self.fire_input_seq);
        if self.fire_intent_seq.is_some() != self.fire_input_seq.is_some()
            || matches!(self.shooter_kind, VehicleKind::Player) != player_intent.is_some()
            || player_intent.is_some_and(|(intent, input)| {
                intent == 0 || input == 0 || intent > MAX_EXACT_INT || input > MAX_EXACT_INT
            })
        {
            return invalid("projectiles.player_intent");
        }
        if !valid_world_position(self.origin) || !valid_launch_velocity(self.velocity) {
            return invalid("projectiles.trajectory");
        }
        if !finite_range(self.gravity, 0.000_001, 500.0)
            || !finite_range(self.max_distance, 0.000_001, 10_000.0)
            || !(1..=20_000).contains(&self.max_time_ms)
            || !finite_range(self.splash_radius, 0.0, 100.0)
            || !finite_range(self.penetration_factor, 0.0, 100.0)
            || self.launch_server_time_ms > server_time_ms
            || self.launch_server_time_ms > MAX_EXACT_INT
            || self.checked_through_ms > self.max_time_ms
            || !finite_range(self.checked_distance, 0.0, self.max_distance + 0.1)
            || !finite_range(self.piercing_loss, 0.0, 100_000.0)
        {
            return invalid("projectiles.progress");
        }
        validate_source_shot(&self.source_shot)?;
        let speed = vector_magnitude(self.velocity);
        let shell = &self.source_shot.shell;
        if !close(speed, self.source_shot.speed)
            || !close(self.gravity, self.source_shot.gravity)
            || !close(self.max_distance, self.source_shot.max_distance)
            || self.is_he != (shell.kind == "HIGH_EXPLOSIVE")
            || !close(self.splash_radius, shell.explosion_radius)
        {
            return invalid("projectiles.source_shot");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct BattleVehicleStatistics {
    pub actor_kind: VehicleKind,
    pub actor_id: u64,
    pub team: u8,
    pub shots_fired: u32,
    pub shots_hit: u32,
    pub shots_penetrated: u32,
    pub damage_dealt: u64,
    pub damage_received: u64,
    pub damage_blocked: u64,
    pub damage_assisted_track: u64,
    pub damage_assisted_radio: u64,
    pub kills: u32,
}

impl BattleVehicleStatistics {
    pub fn from_statistics(actor: VehicleKey, statistics: &VehicleStatistics) -> Self {
        Self {
            actor_kind: actor.kind,
            actor_id: actor.id,
            team: statistics.team,
            shots_fired: statistics.shots_fired,
            shots_hit: statistics.shots_hit,
            shots_penetrated: statistics.shots_penetrated,
            damage_dealt: statistics.damage_dealt,
            damage_received: statistics.damage_received,
            damage_blocked: statistics.damage_blocked,
            damage_assisted_track: statistics.damage_assisted_track,
            damage_assisted_radio: statistics.damage_assisted_radio,
            kills: statistics.kills,
        }
    }

    fn validate(&self) -> Result<()> {
        if !valid_actor(VehicleKey {
            kind: self.actor_kind,
            id: self.actor_id,
        }) || !matches!(self.team, 1 | 2)
            || [
                u64::from(self.shots_fired),
                u64::from(self.shots_hit),
                u64::from(self.shots_penetrated),
                self.damage_dealt,
                self.damage_received,
                self.damage_blocked,
                self.damage_assisted_track,
                self.damage_assisted_radio,
                u64::from(self.kills),
            ]
            .into_iter()
            .any(|value| value > MAX_EXACT_INT)
            || self.shots_hit > self.shots_fired
            || self.shots_penetrated > self.shots_hit
        {
            return invalid("battle_result.vehicle_statistics");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct BattleResultState {
    pub winner: u8,
    pub reason: String,
    pub base_team: u8,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vehicle_statistics: Option<Vec<BattleVehicleStatistics>>,
}

impl BattleResultState {
    fn validate(&self) -> Result<()> {
        if self.winner > 2 || self.base_team > 2 || !valid_text(&self.reason, 64) {
            return invalid("battle_result");
        }
        if let Some(rows) = &self.vehicle_statistics {
            if rows.len() > MAX_PLAYERS + MAX_BOTS {
                return invalid("battle_result.vehicle_statistics");
            }
            let mut actors = BTreeSet::new();
            for row in rows {
                row.validate()?;
                if !actors.insert((row.actor_kind, row.actor_id)) {
                    return invalid("battle_result.vehicle_statistics");
                }
            }
        }
        Ok(())
    }
}

/// The complete set of ordered event kinds accepted by the current #1513
/// runtime. Rust-internal names such as `projectile_launch`,
/// `projectile_resolved`, `capture`, and bot-simulation enum names must never
/// be put directly on the wire.
pub const CLIENT_EVENT_KINDS: [&str; 14] = [
    "authority",
    "bot_manifest",
    "vehicle_statistics",
    "destructible",
    "projectile_impact",
    "battle_result",
    "assist",
    "shot",
    "bot_shot",
    "health",
    "hit",
    "bot_hit",
    "bot_human_hit",
    "bot_bot_hit",
];

pub fn client_supports_event_kind(kind: &str) -> bool {
    CLIENT_EVENT_KINDS.contains(&kind)
}

/// Round roster retained by the server for ordered-event validation.
///
/// This is intentionally distinct from a single snapshot: a projectile may
/// outlive a disconnected shooter, so the server must retain every actor from
/// the round's canonical start roster until the round ends.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct EventRoster {
    actors: BTreeMap<VehicleKey, u8>,
}

impl EventRoster {
    pub fn try_new(actors: impl IntoIterator<Item = (VehicleKey, u8)>) -> Result<Self> {
        let mut result = Self::default();
        for (actor, team) in actors {
            if !valid_actor(actor)
                || !matches!(team, 1 | 2)
                || result.actors.insert(actor, team).is_some()
            {
                return invalid("events.roster");
            }
        }
        if result.actors.len() > MAX_PLAYERS + MAX_BOTS {
            return invalid("events.roster");
        }
        Ok(result)
    }

    pub fn from_battle_start(start: &BattleStart) -> Result<Self> {
        Self::try_new(
            start
                .players
                .iter()
                .map(|player| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Player,
                            id: player.id,
                        },
                        player.team,
                    )
                })
                .chain(start.bot_manifest.iter().map(|bot| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Bot,
                            id: bot.id,
                        },
                        bot.team,
                    )
                })),
        )
    }

    pub fn from_snapshot(snapshot: &SnapshotFrame) -> Result<Self> {
        Self::try_new(
            snapshot
                .players
                .iter()
                .map(|player| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Player,
                            id: player.id,
                        },
                        player.team,
                    )
                })
                .chain(snapshot.bot_manifest.iter().map(|bot| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Bot,
                            id: bot.id,
                        },
                        bot.team,
                    )
                })),
        )
    }

    pub fn team(&self, actor: VehicleKey) -> Option<u8> {
        self.actors.get(&actor).copied()
    }

    fn require(&self, actor: VehicleKey) -> Result<u8> {
        self.team(actor)
            .ok_or(ClientReplicationError::Invalid("events.unknown_actor"))
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum CriticalDeviceName {
    #[serde(rename = "engineHealth")]
    EngineHealth,
    #[serde(rename = "ammoBayHealth")]
    AmmoBayHealth,
    #[serde(rename = "fuelTankHealth")]
    FuelTankHealth,
    #[serde(rename = "radioHealth")]
    RadioHealth,
    #[serde(rename = "leftTrackHealth")]
    LeftTrackHealth,
    #[serde(rename = "rightTrackHealth")]
    RightTrackHealth,
    #[serde(rename = "gunHealth")]
    GunHealth,
    #[serde(rename = "turretRotatorHealth")]
    TurretRotatorHealth,
    #[serde(rename = "surveyingDeviceHealth")]
    SurveyingDeviceHealth,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum CriticalDeviceState {
    Normal,
    Critical,
    Destroyed,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum CriticalCrewName {
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

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CriticalCrewState {
    Normal,
    Destroyed,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CriticalCause {
    Shot,
    Explosion,
    Repair,
    Fire,
    Drowning,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AmmoRackState {
    Destroyed,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CriticalDevice {
    pub name: CriticalDeviceName,
    pub hp: f64,
    pub max_hp: f64,
    pub state: CriticalDeviceState,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CriticalTransition {
    Device {
        name: CriticalDeviceName,
        state: CriticalDeviceState,
        #[serde(skip_serializing_if = "Option::is_none")]
        old_state: Option<CriticalDeviceState>,
        cause: CriticalCause,
    },
    Crew {
        name: CriticalCrewName,
        state: CriticalCrewState,
        cause: CriticalCause,
    },
    Fire {
        state: bool,
        cause: CriticalCause,
    },
    AmmoRack {
        state: AmmoRackState,
        cause: CriticalCause,
    },
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct CriticalPayload {
    pub devices: Vec<CriticalDevice>,
    pub destroyed: Vec<CriticalDeviceName>,
    pub crew_ko: Vec<CriticalCrewName>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub crew_roster: Option<Vec<CriticalCrewName>>,
    pub fire: bool,
    pub ammo_rack_death: bool,
    pub events: Vec<CriticalTransition>,
}

impl CriticalPayload {
    fn validate(&self) -> Result<()> {
        if self.devices.len() > 16 || self.events.len() > 24 {
            return invalid("events.critical.capacity");
        }
        let mut device_names = BTreeSet::new();
        let mut destroyed_states = BTreeSet::new();
        for device in &self.devices {
            if !device_names.insert(device.name)
                || !finite_range(device.max_hp, 1.0, MAX_CRITICAL_DEVICE_HP)
                || !finite_range(device.hp, 0.0, device.max_hp)
            {
                return invalid("events.critical.devices");
            }
            if device.state == CriticalDeviceState::Destroyed {
                destroyed_states.insert(device.name);
            }
        }
        let destroyed: BTreeSet<_> = self.destroyed.iter().copied().collect();
        if destroyed.len() != self.destroyed.len() || destroyed != destroyed_states {
            return invalid("events.critical.destroyed");
        }
        let crew_ko: BTreeSet<_> = self.crew_ko.iter().copied().collect();
        if crew_ko.len() != self.crew_ko.len() {
            return invalid("events.critical.crew_ko");
        }
        if let Some(roster) = &self.crew_roster {
            let roster_set: BTreeSet<_> = roster.iter().copied().collect();
            if roster.is_empty()
                || roster.len() > 8
                || roster_set.len() != roster.len()
                || !crew_ko.is_subset(&roster_set)
            {
                return invalid("events.critical.crew_roster");
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CriticalRevision {
    Player {
        revision: u64,
        base_revision: u64,
        ack_seq: u64,
    },
    Bot {
        revision: u64,
        base_revision: u64,
        ack_seq: u64,
    },
}

impl CriticalRevision {
    fn values(self) -> (VehicleKind, u64, u64, u64) {
        match self {
            Self::Player {
                revision,
                base_revision,
                ack_seq,
            } => (VehicleKind::Player, revision, base_revision, ack_seq),
            Self::Bot {
                revision,
                base_revision,
                ack_seq,
            } => (VehicleKind::Bot, revision, base_revision, ack_seq),
        }
    }

    fn validate(self, target_kind: VehicleKind) -> Result<()> {
        let (kind, revision, base_revision, ack_seq) = self.values();
        if kind != target_kind
            || revision > MAX_EXACT_INT
            || base_revision > revision
            || ack_seq > MAX_EXACT_INT
        {
            return invalid("events.critical.revision");
        }
        Ok(())
    }

    fn insert(self, fields: &mut Map<String, Value>) {
        let (kind, revision, base_revision, ack_seq) = self.values();
        let (revision_name, base_name, ack_name) = match kind {
            VehicleKind::Player => (
                "critical_revision",
                "critical_base_revision",
                "critical_ack_seq",
            ),
            VehicleKind::Bot => ("combat_revision", "combat_base_revision", "combat_ack_seq"),
        };
        fields.insert(revision_name.to_owned(), number(revision));
        fields.insert(base_name.to_owned(), number(base_revision));
        fields.insert(ack_name.to_owned(), number(ack_seq));
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ShotEvent {
    pub projectile: ProjectileWireState,
    pub burst: PlayerAmmoBurst,
}

impl ShotEvent {
    pub fn from_record(record: &ProjectileRecord) -> Self {
        Self::from_record_with_burst(record, PlayerAmmoBurst::ordinary(record.launch.shot_seq))
    }

    pub fn from_record_with_burst(record: &ProjectileRecord, burst: PlayerAmmoBurst) -> Self {
        Self {
            projectile: ProjectileWireState::from(record),
            burst,
        }
    }

    fn to_value(&self, scope: FrameScope, roster: &EventRoster) -> Result<Value> {
        let projectile = &self.projectile;
        projectile.validate(scope.authority_epoch, scope.server_time_ms)?;
        let shooter = VehicleKey {
            kind: projectile.shooter_kind,
            id: projectile.shooter_id,
        };
        if roster.require(shooter)? != projectile.team {
            return invalid("events.shot.team");
        }

        let horizontal = projectile.velocity[0].hypot(projectile.velocity[2]);
        let shot_yaw = round_six(projectile.velocity[0].atan2(projectile.velocity[2]));
        let shot_pitch = round_six(projectile.velocity[1].atan2(horizontal));
        let mut fields = Map::new();
        fields.insert(
            "kind".to_owned(),
            Value::String(
                match projectile.shooter_kind {
                    VehicleKind::Player => "shot",
                    VehicleKind::Bot => "bot_shot",
                }
                .to_owned(),
            ),
        );
        insert_actor_role(&mut fields, "attacker", shooter);
        fields.insert(
            "projectile_id".to_owned(),
            Value::String(projectile.projectile_id.clone()),
        );
        fields.insert("shot_seq".to_owned(), number(projectile.shot_seq));
        if self.burst.count == 0
            || self.burst.count > 64
            || self.burst.index >= self.burst.count
            || self.burst.group_seq == 0
            || self.burst.group_seq > MAX_EXACT_INT
            || self
                .burst
                .group_seq
                .checked_add(u64::from(self.burst.index))
                != Some(projectile.shot_seq)
        {
            return invalid("events.shot.burst");
        }
        fields.insert("burst_group_seq".to_owned(), number(self.burst.group_seq));
        fields.insert(
            "burst_index".to_owned(),
            number(u64::from(self.burst.index)),
        );
        fields.insert(
            "burst_count".to_owned(),
            number(u64::from(self.burst.count)),
        );
        fields.insert(
            "shell_index".to_owned(),
            number(u64::from(projectile.shell_index)),
        );
        fields.insert(
            "origin".to_owned(),
            serde_json::to_value(projectile.origin)?,
        );
        fields.insert(
            "velocity".to_owned(),
            serde_json::to_value(projectile.velocity)?,
        );
        fields.insert("gravity".to_owned(), value_float(projectile.gravity)?);
        fields.insert(
            "maxDistance".to_owned(),
            value_float(projectile.max_distance)?,
        );
        fields.insert("max_time_ms".to_owned(), number(projectile.max_time_ms));
        fields.insert("is_he".to_owned(), Value::Bool(projectile.is_he));
        fields.insert(
            "splash_radius".to_owned(),
            value_float(projectile.splash_radius)?,
        );
        fields.insert(
            "penetration_factor".to_owned(),
            value_float(projectile.penetration_factor)?,
        );
        fields.insert(
            "launch_server_time_ms".to_owned(),
            number(projectile.launch_server_time_ms),
        );
        fields.insert(
            "shooter_kind".to_owned(),
            Value::String(vehicle_kind_name(projectile.shooter_kind).to_owned()),
        );
        fields.insert("shooter_id".to_owned(), number(projectile.shooter_id));
        fields.insert(
            "source_vehicle".to_owned(),
            Value::String(projectile.source_vehicle.clone()),
        );
        fields.insert(
            "source_shot".to_owned(),
            serde_json::to_value(&projectile.source_shot)?,
        );
        fields.insert(
            "authority_epoch".to_owned(),
            number(projectile.authority_epoch),
        );
        fields.insert("shot_yaw".to_owned(), value_float(shot_yaw)?);
        fields.insert("shot_pitch".to_owned(), value_float(shot_pitch)?);
        if let (Some(intent), Some(input)) = (projectile.fire_intent_seq, projectile.fire_input_seq)
        {
            fields.insert("fire_intent_seq".to_owned(), number(intent));
            fields.insert("fire_input_seq".to_owned(), number(input));
        }
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ShotImpact {
    pub projectile_id: String,
    pub shot_seq: u64,
    pub shell_index: u8,
    pub shot_result: u8,
    pub blocked_damage: u32,
    pub splash: bool,
    pub impact: Point3,
}

impl ShotImpact {
    fn validate(&self) -> Result<()> {
        if !valid_projectile_id(&self.projectile_id)
            || self.shot_seq == 0
            || self.shot_seq > MAX_EXACT_INT
            || self.shell_index > 9
            || self.shot_result > 2
            || self.blocked_damage > 5_000
            || (self.blocked_damage > 0 && (self.splash || self.shot_result == 2))
        {
            return invalid("events.combat.shot");
        }
        self.impact.validate(5_000.0, -1_000.0, 3_000.0)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct CombatEvent {
    pub commit: DamageCommit,
    pub death_reason: u8,
    pub display_health: Option<u32>,
    /// Required only for `DamageSource::Environment`, which maps to the
    /// client's explicit `client_simulation` source.
    pub client_simulation_reason: Option<u16>,
    /// Required only for `DamageSource::Shot`.
    pub shot: Option<ShotImpact>,
    pub critical: Option<CriticalPayload>,
    pub critical_revision: Option<CriticalRevision>,
}

impl CombatEvent {
    fn validate(&self, roster: &EventRoster) -> Result<(&'static str, &'static str)> {
        let target_team = roster.require(self.commit.target)?;
        let attacker_team = match self.commit.attacker {
            Some(attacker) => Some(roster.require(attacker)?),
            None => None,
        };
        if self.commit.attacker == Some(self.commit.target)
            || (self.commit.requested > MAX_COMBAT_HEALTH
                && self.commit.source != DamageSource::Ram)
            || self.commit.applied > self.commit.requested
            || self.commit.health > MAX_COMBAT_HEALTH
            || self
                .display_health
                .is_some_and(|health| health > MAX_COMBAT_HEALTH)
            || (!self.commit.dead && self.death_reason != 0)
        {
            return invalid("events.combat.state");
        }

        let (kind, source) = match self.commit.source {
            DamageSource::Shot => {
                if self.commit.attacker.is_none()
                    || self.shot.is_none()
                    || self.client_simulation_reason.is_some()
                {
                    return invalid("events.combat.shot_source");
                }
                (
                    combat_kind(self.commit.attacker, self.commit.target),
                    "shot",
                )
            }
            DamageSource::Fire => {
                if self.commit.attacker.is_none()
                    || self.shot.is_some()
                    || self.client_simulation_reason.is_some()
                {
                    return invalid("events.combat.fire_source");
                }
                let kind = combat_kind(self.commit.attacker, self.commit.target);
                // The exact client deliberately has no player->player fire
                // event contract (`hit` is not allowed for source `fire`).
                if kind == "hit" {
                    return invalid("events.combat.unsupported_player_fire");
                }
                (kind, "fire")
            }
            DamageSource::Ram => {
                if self.commit.attacker.is_none()
                    || self.shot.is_some()
                    || self.client_simulation_reason.is_some()
                {
                    return invalid("events.combat.ram_source");
                }
                (combat_kind(self.commit.attacker, self.commit.target), "ram")
            }
            DamageSource::Environment => {
                if self.commit.attacker.is_some()
                    || self.shot.is_some()
                    || self.client_simulation_reason.is_none()
                {
                    return invalid("events.combat.client_simulation_source");
                }
                ("health", "client_simulation")
            }
            DamageSource::PlayerLeft => {
                if self.commit.attacker.is_some()
                    || self.commit.target.kind != VehicleKind::Player
                    || self.shot.is_some()
                    || self.client_simulation_reason.is_some()
                    || self.death_reason != 0
                    || self.critical.is_some()
                {
                    return invalid("events.combat.player_left_source");
                }
                ("health", "player_left")
            }
        };

        if let Some(shot) = &self.shot {
            shot.validate()?;
            if shot.blocked_damage > 0 && attacker_team == Some(target_team) {
                return invalid("events.combat.blocked_team_damage");
            }
        }
        if let Some(critical) = &self.critical {
            critical.validate()?;
            if self.critical_revision.is_none() {
                return invalid("events.critical.missing_revision");
            }
        }
        if let Some(revision) = self.critical_revision {
            revision.validate(self.commit.target.kind)?;
        }
        Ok((kind, source))
    }

    fn to_value(&self, roster: &EventRoster) -> Result<Value> {
        let (kind, source) = self.validate(roster)?;
        let mut fields = Map::new();
        fields.insert("kind".to_owned(), Value::String(kind.to_owned()));
        if let Some(attacker) = self.commit.attacker {
            insert_actor_role(&mut fields, "attacker", attacker);
        }
        insert_actor_role(&mut fields, "target", self.commit.target);
        fields.insert("damage".to_owned(), number(u64::from(self.commit.applied)));
        fields.insert("health".to_owned(), number(u64::from(self.commit.health)));
        fields.insert("dead".to_owned(), Value::Bool(self.commit.dead));
        fields.insert(
            "death_reason".to_owned(),
            number(u64::from(self.death_reason)),
        );
        fields.insert("source".to_owned(), Value::String(source.to_owned()));
        fields.insert(
            "attack_reason".to_owned(),
            match self.commit.source {
                DamageSource::Shot => number(0),
                DamageSource::Fire => number(1),
                DamageSource::Ram => number(2),
                DamageSource::Environment => number(u64::from(
                    self.client_simulation_reason
                        .expect("validated client simulation reason"),
                )),
                DamageSource::PlayerLeft => Value::Null,
            },
        );
        fields.insert(
            "blocked_damage".to_owned(),
            number(u64::from(
                self.shot.as_ref().map_or(0, |shot| shot.blocked_damage),
            )),
        );
        if let Some(display_health) = self.display_health {
            fields.insert(
                "display_health".to_owned(),
                number(u64::from(display_health)),
            );
        }
        if let Some(shot) = &self.shot {
            fields.insert(
                "projectile_id".to_owned(),
                Value::String(shot.projectile_id.clone()),
            );
            fields.insert("shot_seq".to_owned(), number(shot.shot_seq));
            fields.insert(
                "shell_index".to_owned(),
                number(u64::from(shot.shell_index)),
            );
            fields.insert(
                "shot_result".to_owned(),
                number(u64::from(shot.shot_result)),
            );
            fields.insert("splash".to_owned(), Value::Bool(shot.splash));
            fields.insert("world_pose".to_owned(), Value::Bool(true));
            fields.insert("x".to_owned(), value_float(shot.impact.x)?);
            fields.insert("y".to_owned(), value_float(shot.impact.y)?);
            fields.insert("z".to_owned(), value_float(shot.impact.z)?);
        }
        if let Some(critical) = &self.critical {
            fields.insert("critical".to_owned(), serde_json::to_value(critical)?);
        }
        if let Some(revision) = self.critical_revision {
            revision.insert(&mut fields);
        }
        Ok(Value::Object(fields))
    }
}

fn combat_kind(attacker: Option<VehicleKey>, target: VehicleKey) -> &'static str {
    match (attacker.map(|actor| actor.kind), target.kind) {
        (None, _) => "health",
        (Some(VehicleKind::Player), VehicleKind::Player) => "hit",
        (Some(VehicleKind::Player), VehicleKind::Bot) => "bot_hit",
        (Some(VehicleKind::Bot), VehicleKind::Player) => "bot_human_hit",
        (Some(VehicleKind::Bot), VehicleKind::Bot) => "bot_bot_hit",
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VehicleStatisticsEvent {
    pub actor: VehicleKey,
    pub frags: i32,
    pub team_killer: bool,
}

impl VehicleStatisticsEvent {
    pub fn from_combat_state(actor: VehicleKey, state: &VehicleCombatState) -> Self {
        Self {
            actor,
            frags: state.frags,
            team_killer: state.team_killer,
        }
    }

    fn to_value(&self, roster: &EventRoster) -> Result<Value> {
        roster.require(self.actor)?;
        if !(-30..=30).contains(&self.frags) {
            return invalid("events.vehicle_statistics.frags");
        }
        let mut fields = Map::new();
        fields.insert(
            "kind".to_owned(),
            Value::String("vehicle_statistics".to_owned()),
        );
        insert_actor_pair(&mut fields, "actor", self.actor);
        fields.insert("frags".to_owned(), signed_number(i64::from(self.frags)));
        fields.insert("team_killer".to_owned(), Value::Bool(self.team_killer));
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ClientAssistCategory {
    Track,
    Radio,
    Stun,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AssistEvent {
    pub category: ClientAssistCategory,
    pub assister: VehicleKey,
    pub attacker: VehicleKey,
    pub target: VehicleKey,
    pub damage: u32,
}

impl From<&AssistAward> for AssistEvent {
    fn from(award: &AssistAward) -> Self {
        Self {
            category: match award.category {
                AssistCategory::Track => ClientAssistCategory::Track,
                AssistCategory::Radio => ClientAssistCategory::Radio,
                AssistCategory::Stun => ClientAssistCategory::Stun,
            },
            assister: award.assister,
            attacker: award.attacker,
            target: award.target,
            damage: award.damage,
        }
    }
}

impl AssistEvent {
    fn to_value(&self, roster: &EventRoster) -> Result<Value> {
        let assister_team = roster.require(self.assister)?;
        let attacker_team = roster.require(self.attacker)?;
        let target_team = roster.require(self.target)?;
        if self.damage == 0
            || self.damage > MAX_COMBAT_HEALTH
            || self.assister == self.attacker
            || self.assister == self.target
            || self.attacker == self.target
            || assister_team != attacker_team
            || assister_team == target_team
        {
            return invalid("events.assist");
        }
        let mut fields = Map::new();
        fields.insert("kind".to_owned(), Value::String("assist".to_owned()));
        fields.insert("category".to_owned(), serde_json::to_value(self.category)?);
        insert_actor_pair(&mut fields, "assister", self.assister);
        insert_actor_pair(&mut fields, "attacker", self.attacker);
        insert_actor_pair(&mut fields, "target", self.target);
        fields.insert("damage".to_owned(), number(u64::from(self.damage)));
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StunEvent {
    pub active: bool,
    pub target: VehicleKey,
    pub attacker: Option<VehicleKey>,
    pub end_server_time_ms: u64,
}

impl StunEvent {
    fn to_value(&self, roster: &EventRoster) -> Result<Value> {
        roster.require(self.target)?;
        match (self.active, self.attacker, self.end_server_time_ms) {
            (true, Some(attacker), 1..=MAX_EXACT_INT) => {
                roster.require(attacker)?;
            }
            (false, None, 0) => {}
            _ => return invalid("events.stun"),
        }
        let mut fields = Map::new();
        fields.insert("kind".to_owned(), Value::String("stun".to_owned()));
        fields.insert("active".to_owned(), Value::Bool(self.active));
        insert_actor_pair(&mut fields, "target", self.target);
        if let Some(attacker) = self.attacker {
            insert_actor_pair(&mut fields, "attacker", attacker);
        }
        fields.insert(
            "stun_end_server_time_ms".to_owned(),
            number(self.end_server_time_ms),
        );
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileImpactEvent {
    pub resolution: ProjectileResolution,
    pub shooter: VehicleKey,
    pub shot_seq: u64,
    pub hit_vehicle: bool,
    pub wreck_hit: Option<VehicleKey>,
}

impl ProjectileImpactEvent {
    pub fn from_resolution(
        resolution: &ProjectileResolution,
        record: &ProjectileRecord,
        hit_vehicle: bool,
        wreck_hit: Option<VehicleKey>,
    ) -> Result<Self> {
        if resolution.projectile_id != record.projectile_id
            || resolution.round_id != record.launch.round_id
            || resolution.authority_epoch != record.launch.authority_epoch
        {
            return invalid("events.projectile_impact.lineage");
        }
        Ok(Self {
            resolution: resolution.clone(),
            shooter: record.launch.shooter,
            shot_seq: record.launch.shot_seq,
            hit_vehicle,
            wreck_hit,
        })
    }

    fn to_value(&self, scope: FrameScope, roster: &EventRoster) -> Result<Value> {
        let resolution = &self.resolution;
        roster.require(self.shooter)?;
        if resolution.round_id != scope.round_id
            || resolution.authority_epoch != scope.authority_epoch
            || !valid_projectile_id(&resolution.projectile_id)
            || self.shot_seq == 0
            || self.shot_seq > MAX_EXACT_INT
            || resolution.base_checked_ms > resolution.resolved_time_ms
            || resolution.resolved_time_ms > 20_000
            || !finite_range(resolution.checked_distance, 0.0, 10_000.1)
            || !finite_range(resolution.piercing_loss, 0.0, 100_000.0)
            || !finite_range(resolution.penetration_factor, 0.0, 100.0)
        {
            return invalid("events.projectile_impact");
        }
        let has_impact = resolution.impact.is_some();
        if (resolution.outcome == ProjectileOutcome::Impact) != has_impact
            || (self.hit_vehicle && resolution.outcome != ProjectileOutcome::Impact)
            || (self.wreck_hit.is_some()
                && (!self.hit_vehicle || resolution.outcome != ProjectileOutcome::Impact))
        {
            return invalid("events.projectile_impact.outcome");
        }
        if let Some(impact) = resolution.impact {
            if !valid_world_position([impact.x, impact.y, impact.z]) {
                return invalid("events.projectile_impact.position");
            }
        }
        if let Some(target) = self.wreck_hit {
            roster.require(target)?;
        }

        let mut fields = Map::new();
        fields.insert(
            "kind".to_owned(),
            Value::String("projectile_impact".to_owned()),
        );
        fields.insert(
            "projectile_id".to_owned(),
            Value::String(resolution.projectile_id.clone()),
        );
        fields.insert(
            "outcome".to_owned(),
            Value::String(projectile_outcome_name(resolution.outcome).to_owned()),
        );
        fields.insert(
            "resolved_time_ms".to_owned(),
            number(resolution.resolved_time_ms),
        );
        fields.insert(
            "checked_distance".to_owned(),
            value_float(resolution.checked_distance)?,
        );
        fields.insert(
            "piercing_loss".to_owned(),
            value_float(resolution.piercing_loss)?,
        );
        fields.insert(
            "penetration_factor".to_owned(),
            value_float(resolution.penetration_factor)?,
        );
        fields.insert("hit_vehicle".to_owned(), Value::Bool(self.hit_vehicle));
        fields.insert(
            "shooter_kind".to_owned(),
            Value::String(vehicle_kind_name(self.shooter.kind).to_owned()),
        );
        fields.insert("shooter_id".to_owned(), number(self.shooter.id));
        fields.insert("shot_seq".to_owned(), number(self.shot_seq));
        if let Some(impact) = resolution.impact {
            fields.insert(
                "impact".to_owned(),
                serde_json::to_value([impact.x, impact.y, impact.z])?,
            );
        }
        if let Some(target) = self.wreck_hit {
            let mut wreck = Map::new();
            insert_actor_pair(&mut wreck, "target", target);
            fields.insert("wreck_hit".to_owned(), Value::Object(wreck));
        }
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotManifestEvent {
    pub bots: Vec<BotManifestEntry>,
}

impl BotManifestEvent {
    fn to_value(&self, roster: &EventRoster) -> Result<Value> {
        validate_manifest(&self.bots, &[])?;
        let expected: BTreeSet<_> = roster
            .actors
            .iter()
            .filter_map(|(actor, _)| (actor.kind == VehicleKind::Bot).then_some(actor.id))
            .collect();
        let actual: BTreeSet<_> = self.bots.iter().map(|bot| bot.id).collect();
        if actual != expected
            || self.bots.iter().any(|bot| {
                roster.team(VehicleKey {
                    kind: VehicleKind::Bot,
                    id: bot.id,
                }) != Some(bot.team)
            })
        {
            return invalid("events.bot_manifest.lineage");
        }
        let mut fields = Map::new();
        fields.insert("kind".to_owned(), Value::String("bot_manifest".to_owned()));
        fields.insert("bots".to_owned(), serde_json::to_value(&self.bots)?);
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum BattleClientEvent {
    Authority,
    BotManifest(BotManifestEvent),
    Shot(ShotEvent),
    Combat(CombatEvent),
    VehicleStatistics(VehicleStatisticsEvent),
    Assist(AssistEvent),
    Stun(StunEvent),
    Destructible(DestructibleState),
    ProjectileImpact(ProjectileImpactEvent),
    BattleResult(BattleResultState),
}

impl BattleClientEvent {
    fn to_value(&self, scope: FrameScope, roster: &EventRoster) -> Result<Value> {
        match self {
            Self::Authority => {
                let mut fields = Map::new();
                fields.insert("kind".to_owned(), Value::String("authority".to_owned()));
                fields.insert(
                    "player_id".to_owned(),
                    Value::Number(SERVER_AUTHORITY_ID.into()),
                );
                fields.insert("round_id".to_owned(), number(scope.round_id));
                fields.insert("authority_epoch".to_owned(), number(scope.authority_epoch));
                Ok(Value::Object(fields))
            }
            Self::BotManifest(event) => event.to_value(roster),
            Self::Shot(event) => event.to_value(scope, roster),
            Self::Combat(event) => event.to_value(roster),
            Self::VehicleStatistics(event) => event.to_value(roster),
            Self::Assist(event) => event.to_value(roster),
            Self::Stun(event) => event.to_value(roster),
            Self::Destructible(event) => {
                event.validate(event.revision)?;
                event.to_value()
            }
            Self::ProjectileImpact(event) => event.to_value(scope, roster),
            Self::BattleResult(result) => {
                result.validate()?;
                let mut fields = value_object(serde_json::to_value(result)?)?;
                fields.insert("kind".to_owned(), Value::String("battle_result".to_owned()));
                Ok(Value::Object(fields))
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleEventsFrame {
    pub scope: FrameScope,
    pub first_ordinal: u16,
    pub roster: EventRoster,
    pub events: Vec<BattleClientEvent>,
}

pub fn encode_battle_events(frame: &BattleEventsFrame) -> Result<WireObject> {
    frame.scope.validate()?;
    if frame.events.is_empty() || frame.events.len() > MAX_EVENTS {
        return invalid("events.capacity");
    }
    let last_ordinal = usize::from(frame.first_ordinal)
        .checked_add(frame.events.len() - 1)
        .ok_or(ClientReplicationError::Invalid("events.ordinal"))?;
    if last_ordinal > u16::MAX as usize {
        return invalid("events.ordinal");
    }

    let mut values = Vec::with_capacity(frame.events.len());
    for (index, event) in frame.events.iter().enumerate() {
        let mut value = value_object(event.to_value(frame.scope, &frame.roster)?)?;
        value.insert(
            "event_id".to_owned(),
            Value::String(format!(
                "{}:{}:{}",
                frame.scope.round_id,
                frame.scope.server_tick,
                usize::from(frame.first_ordinal) + index
            )),
        );
        values.push(Value::Object(value));
    }

    let mut fields = Map::new();
    insert_protocol(&mut fields);
    insert_scope(&mut fields, frame.scope, true);
    insert_server_authority(&mut fields);
    fields.insert("events".to_owned(), Value::Array(values));
    Ok(WireObject::with_fields("events", fields)?)
}

#[derive(Clone, Debug, PartialEq)]
pub enum RevisionPayload<T> {
    Omit { revision: u64 },
    Include { revision: u64, values: Vec<T> },
}

impl<T> RevisionPayload<T> {
    pub fn revision(&self) -> u64 {
        match self {
            Self::Omit { revision } | Self::Include { revision, .. } => *revision,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleStart {
    pub client_build: String,
    pub scope: FrameScope,
    /// Used only to prove the recipient exists in `players`; it is not sent.
    pub recipient_player_id: u64,
    pub map: String,
    pub requested_by: u64,
    pub host_player_id: u64,
    pub delay_seconds: f64,
    pub need_destructible_map: bool,
    pub players: Vec<PlayerState>,
    pub bots: Vec<BotRosterEntry>,
    pub team_sizes: [u8; 2],
    pub bot_tier_mode: BotTierMode,
    pub bot_lineup: Vec<BotLineupEntry>,
    pub bot_manifest: Vec<BotManifestEntry>,
    pub bot_order_revision: u64,
    pub bot_orders: Vec<BotOrder>,
    pub rules: RulesState,
    pub battle_result: Option<BattleResultState>,
    pub destructible_revision: u64,
    pub destructibles: Vec<DestructibleState>,
}

pub fn encode_battle_start(start: &BattleStart) -> Result<WireObject> {
    validate_build(&start.client_build)?;
    start.scope.validate()?;
    if start.scope.server_tick != 0
        || !valid_text(&start.map, 96)
        || start.requested_by == 0
        || start.requested_by > MAX_EXACT_INT
        || !finite_range(start.delay_seconds, 0.0, 30.0)
    {
        return invalid("battle_start");
    }
    validate_team_sizes(start.team_sizes)?;
    if start.bot_lineup.len() > MAX_BOTS
        || start.bot_lineup.iter().any(|entry| !entry.is_valid())
        || start
            .bot_lineup
            .iter()
            .map(|entry| (entry.team, entry.slot))
            .collect::<BTreeSet<_>>()
            .len()
            != start.bot_lineup.len()
    {
        return invalid("battle_start.bot_lineup");
    }
    validate_players(&start.players, true, true, start.team_sizes)?;
    let player_ids: BTreeSet<_> = start.players.iter().map(|player| player.id).collect();
    if !player_ids.contains(&start.recipient_player_id)
        || !player_ids.contains(&start.host_player_id)
        || !player_ids.contains(&start.requested_by)
    {
        return invalid("battle_start.player_membership");
    }
    validate_roster(&start.bots, start.team_sizes)?;
    validate_manifest(&start.bot_manifest, &start.bots)?;
    validate_orders(
        start.bot_order_revision,
        &start.bot_orders,
        &start.bot_manifest,
    )?;
    start.rules.validate()?;
    if let Some(result) = &start.battle_result {
        result.validate()?;
    }
    validate_destructibles(start.destructible_revision, &start.destructibles)?;

    let mut fields = Map::new();
    insert_protocol(&mut fields);
    fields.insert(
        "client_build".to_owned(),
        Value::String(start.client_build.clone()),
    );
    insert_scope(&mut fields, start.scope, false);
    fields.insert("map".to_owned(), Value::String(start.map.clone()));
    fields.insert("requested_by".to_owned(), number(start.requested_by));
    fields.insert("host_player_id".to_owned(), number(start.host_player_id));
    fields.insert("phase".to_owned(), Value::String("loading".to_owned()));
    fields.insert("delay".to_owned(), value_float(start.delay_seconds)?);
    fields.insert(
        "need_destructible_map".to_owned(),
        Value::Bool(start.need_destructible_map),
    );
    fields.insert("players".to_owned(), serde_json::to_value(&start.players)?);
    fields.insert("bots".to_owned(), serde_json::to_value(&start.bots)?);
    fields.insert(
        "team_size".to_owned(),
        number(u64::from(start.team_sizes[0].max(start.team_sizes[1]))),
    );
    fields.insert("team_sizes".to_owned(), team_sizes_value(start.team_sizes));
    fields.insert(
        "bot_tier_mode".to_owned(),
        Value::String(start.bot_tier_mode.as_str().to_owned()),
    );
    fields.insert(
        "bot_lineup".to_owned(),
        serde_json::to_value(&start.bot_lineup)?,
    );
    insert_server_authority(&mut fields);
    fields.insert(
        "bot_manifest".to_owned(),
        serde_json::to_value(&start.bot_manifest)?,
    );
    fields.insert(
        "bot_order_revision".to_owned(),
        number(start.bot_order_revision),
    );
    fields.insert(
        "bot_orders".to_owned(),
        serde_json::to_value(&start.bot_orders)?,
    );
    fields.insert("rules".to_owned(), start.rules.to_value()?);
    fields.insert(
        "battle_result".to_owned(),
        match &start.battle_result {
            Some(result) => serde_json::to_value(result)?,
            None => Value::Null,
        },
    );
    fields.insert(
        "destructible_revision".to_owned(),
        number(start.destructible_revision),
    );
    fields.insert(
        "destructibles".to_owned(),
        destructibles_value(&start.destructibles)?,
    );
    Ok(WireObject::with_fields("battle_start", fields)?)
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleLive {
    pub client_build: String,
    pub scope: FrameScope,
    pub countdown_seconds: f64,
    pub battle_duration_seconds: f64,
    pub timing: TimingState,
}

pub fn encode_battle_live(live: &BattleLive) -> Result<WireObject> {
    validate_build(&live.client_build)?;
    live.scope.validate()?;
    live.timing.validate()?;
    if !finite_range(live.countdown_seconds, 0.0, 300.0)
        || !finite_range(live.battle_duration_seconds, 0.001, 86_400.0)
        || live.timing.phase != CombatPhase::Prebattle
        || !close(
            live.countdown_seconds * 1_000.0,
            live.timing.start_in_ms as f64,
        )
        || !close(
            live.battle_duration_seconds * 1_000.0,
            live.timing.duration_ms as f64,
        )
    {
        return invalid("battle_live");
    }
    let mut fields = Map::new();
    insert_protocol(&mut fields);
    fields.insert(
        "client_build".to_owned(),
        Value::String(live.client_build.clone()),
    );
    insert_scope(&mut fields, live.scope, true);
    insert_server_authority(&mut fields);
    fields.insert(
        "countdown_seconds".to_owned(),
        value_float(live.countdown_seconds)?,
    );
    fields.insert(
        "battle_duration_seconds".to_owned(),
        value_float(live.battle_duration_seconds)?,
    );
    fields.insert("timing".to_owned(), serde_json::to_value(&live.timing)?);
    Ok(WireObject::with_fields("battle_live", fields)?)
}

/// Proof that a client has already received a full manifest for one lineage.
/// It can only be obtained by inspecting a full snapshot wire object.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FullManifestLineage {
    round_id: u64,
    authority_epoch: u64,
    bot_authority_id: i64,
}

impl FullManifestLineage {
    pub fn from_full_snapshot(message: &WireObject) -> Result<Self> {
        if message.kind() != "snapshot"
            || message.protocol() != Some(LAN_PROTOCOL_VERSION)
            || !message.get("bot_manifest").is_some_and(Value::is_array)
        {
            return invalid("snapshot.full_manifest_lineage");
        }
        let round_id = exact_u64(message.get("round_id"))
            .filter(|value| *value > 0)
            .ok_or(ClientReplicationError::Invalid("snapshot.round_id"))?;
        let authority_epoch = exact_u64(message.get("authority_epoch"))
            .filter(|value| *value <= MAX_EXACT_INT)
            .ok_or(ClientReplicationError::Invalid("snapshot.authority_epoch"))?;
        let bot_authority_id = exact_i64(message.get("bot_authority_id"))
            .ok_or(ClientReplicationError::Invalid("snapshot.bot_authority_id"))?;
        if bot_authority_id != SERVER_AUTHORITY_ID {
            return invalid("snapshot.bot_authority_id");
        }
        Ok(Self {
            round_id,
            authority_epoch,
            bot_authority_id,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SnapshotManifest<'a> {
    Full,
    Lean(&'a FullManifestLineage),
}

#[derive(Clone, Debug, PartialEq)]
pub struct SnapshotFrame {
    pub scope: FrameScope,
    pub map: String,
    pub motion_time_us: u64,
    pub bot_state_time_us: u64,
    pub players: Vec<PlayerState>,
    pub bots: Vec<BotState>,
    pub contacts: Vec<ContactState>,
    pub bot_state_revision: u64,
    /// The canonical manifest is retained even for lean encoding so the
    /// encoder can validate bot identities without trusting client cache.
    pub bot_manifest: Vec<BotManifestEntry>,
    pub bot_orders: RevisionPayload<BotOrder>,
    pub rules: RulesState,
    pub battle_result: Option<BattleResultState>,
    pub destructibles: RevisionPayload<DestructibleState>,
    pub timing: TimingState,
    pub projectile_revision: u64,
    pub projectiles: Vec<ProjectileWireState>,
}

pub fn encode_snapshot(
    snapshot: &SnapshotFrame,
    manifest_mode: SnapshotManifest<'_>,
) -> Result<WireObject> {
    snapshot.scope.validate()?;
    if !valid_text(&snapshot.map, 96)
        || snapshot.motion_time_us > MAX_MOTION_TIME_US
        || snapshot.bot_state_time_us > snapshot.motion_time_us
        || snapshot.bot_state_revision > MAX_EXACT_INT
        || snapshot.projectile_revision > MAX_EXACT_INT
    {
        return invalid("snapshot");
    }
    snapshot.timing.validate()?;
    validate_players(&snapshot.players, false, false, [15, 15])?;
    validate_manifest(&snapshot.bot_manifest, &[])?;
    validate_bot_states(&snapshot.bots, &snapshot.bot_manifest)?;
    if snapshot.contacts.len() > 64 {
        return invalid("snapshot.contacts");
    }
    for contact in &snapshot.contacts {
        contact.validate()?;
    }
    validate_revision_payload_orders(&snapshot.bot_orders, &snapshot.bot_manifest)?;
    validate_revision_payload_destructibles(&snapshot.destructibles)?;
    snapshot.rules.validate()?;
    if let Some(result) = &snapshot.battle_result {
        result.validate()?;
        if snapshot.timing.phase != CombatPhase::Finished {
            return invalid("snapshot.battle_result_timing");
        }
    }
    validate_projectiles(
        &snapshot.projectiles,
        snapshot.scope.authority_epoch,
        snapshot.scope.server_time_ms,
    )?;
    if let SnapshotManifest::Lean(lineage) = manifest_mode {
        if lineage.round_id != snapshot.scope.round_id
            || lineage.authority_epoch != snapshot.scope.authority_epoch
            || lineage.bot_authority_id != SERVER_AUTHORITY_ID
        {
            return invalid("snapshot.lean_manifest_lineage");
        }
    }

    let mut fields = Map::new();
    insert_protocol(&mut fields);
    insert_scope(&mut fields, snapshot.scope, true);
    fields.insert("map".to_owned(), Value::String(snapshot.map.clone()));
    insert_server_authority(&mut fields);
    fields.insert("motion_time_us".to_owned(), number(snapshot.motion_time_us));
    fields.insert(
        "bot_state_time_us".to_owned(),
        number(snapshot.bot_state_time_us),
    );
    fields.insert(
        "players".to_owned(),
        serde_json::to_value(&snapshot.players)?,
    );
    fields.insert("bots".to_owned(), serde_json::to_value(&snapshot.bots)?);
    fields.insert(
        "contacts".to_owned(),
        serde_json::to_value(&snapshot.contacts)?,
    );
    fields.insert(
        "bot_state_revision".to_owned(),
        number(snapshot.bot_state_revision),
    );
    if manifest_mode == SnapshotManifest::Full {
        fields.insert(
            "bot_manifest".to_owned(),
            serde_json::to_value(&snapshot.bot_manifest)?,
        );
    }
    fields.insert(
        "bot_order_revision".to_owned(),
        number(snapshot.bot_orders.revision()),
    );
    if let RevisionPayload::Include { values, .. } = &snapshot.bot_orders {
        fields.insert("bot_orders".to_owned(), serde_json::to_value(values)?);
    }
    fields.insert("rules".to_owned(), snapshot.rules.to_value()?);
    fields.insert(
        "battle_result".to_owned(),
        match &snapshot.battle_result {
            Some(result) => serde_json::to_value(result)?,
            None => Value::Null,
        },
    );
    fields.insert(
        "destructible_revision".to_owned(),
        number(snapshot.destructibles.revision()),
    );
    if let RevisionPayload::Include { values, .. } = &snapshot.destructibles {
        fields.insert("destructibles".to_owned(), destructibles_value(values)?);
    }
    fields.insert("timing".to_owned(), serde_json::to_value(&snapshot.timing)?);
    fields.insert(
        "projectile_revision".to_owned(),
        number(snapshot.projectile_revision),
    );
    fields.insert(
        "projectiles".to_owned(),
        serde_json::to_value(&snapshot.projectiles)?,
    );
    Ok(WireObject::with_fields("snapshot", fields)?)
}

#[derive(Clone, Debug, PartialEq)]
pub struct BattleResultFrame {
    pub scope: FrameScope,
    pub ordinal: u16,
    pub result: BattleResultState,
}

/// Encode the result in the actual client-facing `events` envelope.  The
/// #1513 client does not consume a standalone `type: battle_result` message.
pub fn encode_battle_result(frame: &BattleResultFrame) -> Result<WireObject> {
    encode_battle_events(&BattleEventsFrame {
        scope: frame.scope,
        first_ordinal: frame.ordinal,
        roster: EventRoster::default(),
        events: vec![BattleClientEvent::BattleResult(frame.result.clone())],
    })
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct ResultStats {
    pub shots: u64,
    pub direct_hits: u64,
    pub piercings: u64,
    pub damage: u64,
    pub damage_received: u64,
    pub damage_blocked: u64,
    pub assist_track: u64,
    pub assist_radio: u64,
    pub assist_stun: u64,
    pub kills: u64,
    pub spotted: u64,
    pub capture_points: u64,
    pub dropped_capture_points: u64,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct ResultRewards {
    pub credits: u64,
    pub xp: u64,
    pub free_xp: u64,
    pub repair_cost: u64,
    pub ammo_cost: u64,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum ResultActorKind {
    Player,
    Bot,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct ResultActorRef {
    pub kind: ResultActorKind,
    pub id: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PublicResultRow {
    pub actor: ResultActorRef,
    pub name: String,
    pub vehicle: String,
    pub team: u8,
    pub health: u64,
    pub death_reason: i16,
    pub killer: Option<ResultActorRef>,
    pub is_team_killer: bool,
    pub xp: u64,
    pub stats: ResultStats,
}

impl PublicResultRow {
    fn to_value(&self) -> Result<Value> {
        let mut fields = Map::new();
        fields.insert(
            "actor_kind".to_owned(),
            serde_json::to_value(self.actor.kind)?,
        );
        fields.insert("actor_id".to_owned(), number(self.actor.id));
        fields.insert("name".to_owned(), Value::String(self.name.clone()));
        fields.insert("vehicle".to_owned(), Value::String(self.vehicle.clone()));
        fields.insert("team".to_owned(), number(u64::from(self.team)));
        fields.insert("health".to_owned(), number(self.health));
        fields.insert(
            "death_reason".to_owned(),
            signed_number(i64::from(self.death_reason)),
        );
        match self.killer {
            Some(killer) => {
                fields.insert("killer_kind".to_owned(), serde_json::to_value(killer.kind)?);
                fields.insert("killer_id".to_owned(), number(killer.id));
            }
            None => {
                fields.insert("killer_kind".to_owned(), Value::String(String::new()));
                fields.insert("killer_id".to_owned(), number(0));
            }
        }
        fields.insert(
            "is_team_killer".to_owned(),
            Value::Bool(self.is_team_killer),
        );
        fields.insert("xp".to_owned(), number(self.xp));
        fields.insert("stats".to_owned(), serde_json::to_value(&self.stats)?);
        Ok(Value::Object(fields))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResultInteraction {
    pub target: ResultActorRef,
    pub spotted: u64,
    pub death_reason: i64,
    pub direct_hits: u64,
    pub explosion_hits: u64,
    pub piercings: u64,
    pub damage: u64,
    pub assist_track: u64,
    pub assist_radio: u64,
    pub assist_stun: u64,
    pub crits: u64,
    pub fire: u64,
    pub stun_num: u64,
    pub stun_duration: u64,
    pub damage_blocked: u64,
    pub damage_received: u64,
    pub ricochets_received: u64,
    pub no_damage_direct_hits_received: u64,
    pub target_kills: u64,
}

impl ResultInteraction {
    fn to_value(&self) -> Result<Value> {
        let mut fields = value_object(serde_json::to_value(InteractionNumbers::from(self))?)?;
        fields.insert(
            "target_kind".to_owned(),
            serde_json::to_value(self.target.kind)?,
        );
        fields.insert("target_id".to_owned(), number(self.target.id));
        Ok(Value::Object(fields))
    }
}

#[derive(Serialize)]
struct InteractionNumbers {
    spotted: u64,
    death_reason: i64,
    direct_hits: u64,
    explosion_hits: u64,
    piercings: u64,
    damage: u64,
    assist_track: u64,
    assist_radio: u64,
    assist_stun: u64,
    crits: u64,
    fire: u64,
    stun_num: u64,
    stun_duration: u64,
    damage_blocked: u64,
    damage_received: u64,
    ricochets_received: u64,
    no_damage_direct_hits_received: u64,
    target_kills: u64,
}

impl From<&ResultInteraction> for InteractionNumbers {
    fn from(value: &ResultInteraction) -> Self {
        Self {
            spotted: value.spotted,
            death_reason: value.death_reason,
            direct_hits: value.direct_hits,
            explosion_hits: value.explosion_hits,
            piercings: value.piercings,
            damage: value.damage,
            assist_track: value.assist_track,
            assist_radio: value.assist_radio,
            assist_stun: value.assist_stun,
            crits: value.crits,
            fire: value.fire,
            stun_num: value.stun_num,
            stun_duration: value.stun_duration,
            damage_blocked: value.damage_blocked,
            damage_received: value.damage_received,
            ricochets_received: value.ricochets_received,
            no_damage_direct_hits_received: value.no_damage_direct_hits_received,
            target_kills: value.target_kills,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BattleReceipt {
    pub receipt_id: String,
    pub arena_unique_id: u64,
    pub round_id: u64,
    pub player_id: u64,
    pub account_key: String,
    pub player_name: String,
    pub vehicle: String,
    pub team: u8,
    pub winner: u8,
    pub map: String,
    pub finish_reason: u8,
    pub death_reason: i16,
    pub duration: u64,
    pub premature_leave: bool,
    pub stats: ResultStats,
    pub rewards: ResultRewards,
    pub public_results: Vec<PublicResultRow>,
    pub interactions: Vec<ResultInteraction>,
}

pub fn encode_battle_receipt(receipt: &BattleReceipt) -> Result<WireObject> {
    validate_receipt(receipt)?;
    let mut fields = Map::new();
    insert_protocol(&mut fields);
    fields.insert(
        "receipt_id".to_owned(),
        Value::String(receipt.receipt_id.clone()),
    );
    fields.insert(
        "arena_unique_id".to_owned(),
        number(receipt.arena_unique_id),
    );
    fields.insert("round_id".to_owned(), number(receipt.round_id));
    fields.insert("player_id".to_owned(), number(receipt.player_id));
    fields.insert(
        "account_key".to_owned(),
        Value::String(receipt.account_key.clone()),
    );
    fields.insert(
        "player_name".to_owned(),
        Value::String(receipt.player_name.clone()),
    );
    fields.insert("vehicle".to_owned(), Value::String(receipt.vehicle.clone()));
    fields.insert("team".to_owned(), number(u64::from(receipt.team)));
    fields.insert("winner".to_owned(), number(u64::from(receipt.winner)));
    fields.insert("map".to_owned(), Value::String(receipt.map.clone()));
    fields.insert(
        "finish_reason".to_owned(),
        number(u64::from(receipt.finish_reason)),
    );
    fields.insert(
        "death_reason".to_owned(),
        signed_number(i64::from(receipt.death_reason)),
    );
    fields.insert("duration".to_owned(), number(receipt.duration));
    fields.insert(
        "premature_leave".to_owned(),
        Value::Bool(receipt.premature_leave),
    );
    fields.insert("stats".to_owned(), serde_json::to_value(&receipt.stats)?);
    fields.insert(
        "rewards".to_owned(),
        serde_json::to_value(&receipt.rewards)?,
    );
    fields.insert(
        "public_results".to_owned(),
        Value::Array(
            receipt
                .public_results
                .iter()
                .map(PublicResultRow::to_value)
                .collect::<Result<Vec<_>>>()?,
        ),
    );
    fields.insert(
        "interactions".to_owned(),
        Value::Array(
            receipt
                .interactions
                .iter()
                .map(ResultInteraction::to_value)
                .collect::<Result<Vec<_>>>()?,
        ),
    );
    Ok(WireObject::with_fields("battle_receipt", fields)?)
}

fn validate_build(build: &str) -> Result<()> {
    if build != CLIENT_BUILD_0922 {
        return invalid("client_build");
    }
    Ok(())
}

fn validate_team_sizes(team_sizes: [u8; 2]) -> Result<()> {
    if team_sizes.into_iter().any(|size| !(1..=15).contains(&size)) {
        return invalid("team_sizes");
    }
    Ok(())
}

fn validate_players(
    players: &[PlayerState],
    require_outfits: bool,
    require_nonempty: bool,
    team_sizes: [u8; 2],
) -> Result<()> {
    if (require_nonempty && players.is_empty()) || players.len() > MAX_PLAYERS {
        return invalid("players");
    }
    let mut ids = BTreeSet::new();
    let mut slots = BTreeSet::new();
    for player in players {
        player.validate(require_outfits)?;
        if player.slot >= team_sizes[(player.team - 1) as usize]
            || !ids.insert(player.id)
            || !slots.insert((player.team, player.slot))
        {
            return invalid("players.membership");
        }
    }
    Ok(())
}

fn validate_roster(roster: &[BotRosterEntry], team_sizes: [u8; 2]) -> Result<()> {
    if roster.len() > MAX_BOTS {
        return invalid("bots");
    }
    let mut ids = BTreeSet::new();
    let mut slots = BTreeSet::new();
    for bot in roster {
        bot.validate()?;
        if bot.slot >= team_sizes[(bot.team - 1) as usize]
            || !ids.insert(bot.id)
            || !slots.insert((bot.team, bot.slot))
        {
            return invalid("bots.membership");
        }
    }
    Ok(())
}

fn validate_manifest(manifest: &[BotManifestEntry], roster: &[BotRosterEntry]) -> Result<()> {
    if manifest.len() > MAX_BOTS {
        return invalid("bot_manifest");
    }
    let mut ids = BTreeSet::new();
    let mut slots = BTreeSet::new();
    for bot in manifest {
        bot.validate()?;
        if !ids.insert(bot.id) || !slots.insert((bot.team, bot.slot)) {
            return invalid("bot_manifest.membership");
        }
    }
    if !roster.is_empty() && !manifest.is_empty() {
        let expected: BTreeSet<_> = roster
            .iter()
            .map(|bot| (bot.id, bot.team, bot.slot, bot.name.as_str()))
            .collect();
        let actual: BTreeSet<_> = manifest
            .iter()
            .map(|bot| (bot.id, bot.team, bot.slot, bot.name.as_str()))
            .collect();
        if actual != expected {
            return invalid("bot_manifest.roster_lineage");
        }
    }
    Ok(())
}

fn validate_bot_states(states: &[BotState], manifest: &[BotManifestEntry]) -> Result<()> {
    if states.len() > MAX_BOTS || states.len() != manifest.len() {
        return invalid("bots");
    }
    let identities: BTreeMap<_, _> = manifest.iter().map(|bot| (bot.id, bot)).collect();
    let mut ids = BTreeSet::new();
    for state in states {
        state.validate()?;
        let identity = identities
            .get(&state.id)
            .ok_or(ClientReplicationError::Invalid("bots.manifest_lineage"))?;
        if !ids.insert(state.id)
            || state.team != identity.team
            || state.slot != identity.slot
            || state.name != identity.name
            || state.vehicle != identity.vehicle
            || state.max_health != identity.max_health
        {
            return invalid("bots.manifest_lineage");
        }
    }
    Ok(())
}

fn validate_orders(
    revision: u64,
    orders: &[BotOrder],
    manifest: &[BotManifestEntry],
) -> Result<()> {
    if revision > MAX_EXACT_INT || orders.len() > MAX_BOTS {
        return invalid("bot_orders");
    }
    let identities: BTreeMap<_, _> = manifest.iter().map(|bot| (bot.id, bot.team)).collect();
    let mut ids = BTreeSet::new();
    for order in orders {
        order.validate()?;
        if !ids.insert(order.id) || identities.get(&order.id).copied() != Some(order.team) {
            return invalid("bot_orders.membership");
        }
    }
    Ok(())
}

fn validate_revision_payload_orders(
    payload: &RevisionPayload<BotOrder>,
    manifest: &[BotManifestEntry],
) -> Result<()> {
    if payload.revision() > MAX_EXACT_INT {
        return invalid("bot_order_revision");
    }
    if let RevisionPayload::Include { revision, values } = payload {
        validate_orders(*revision, values, manifest)?;
    }
    Ok(())
}

fn validate_destructibles(revision: u64, values: &[DestructibleState]) -> Result<()> {
    if revision > MAX_EXACT_INT || values.len() > MAX_DESTRUCTIBLES {
        return invalid("destructibles");
    }
    let mut identities = BTreeSet::new();
    let mut revisions = BTreeSet::new();
    for value in values {
        value.validate(revision)?;
        if !identities.insert((
            value.destructible_kind,
            value.chunk_id,
            value.item_index,
            value.mat_kind,
        )) || !revisions.insert(value.revision)
        {
            return invalid("destructibles.identity");
        }
    }
    if revision == 0 && !values.is_empty() {
        return invalid("destructible_revision");
    }
    Ok(())
}

fn validate_revision_payload_destructibles(
    payload: &RevisionPayload<DestructibleState>,
) -> Result<()> {
    if payload.revision() > MAX_EXACT_INT {
        return invalid("destructible_revision");
    }
    if let RevisionPayload::Include { revision, values } = payload {
        validate_destructibles(*revision, values)?;
    }
    Ok(())
}

fn validate_projectiles(
    projectiles: &[ProjectileWireState],
    authority_epoch: u64,
    server_time_ms: u64,
) -> Result<()> {
    if projectiles.len() > MAX_PROJECTILES {
        return invalid("projectiles");
    }
    let mut ids = BTreeSet::new();
    for projectile in projectiles {
        projectile.validate(authority_epoch, server_time_ms)?;
        if !ids.insert(projectile.projectile_id.as_str()) {
            return invalid("projectiles.identity");
        }
    }
    Ok(())
}

fn validate_receipt(receipt: &BattleReceipt) -> Result<()> {
    if !valid_text(&receipt.receipt_id, 96)
        || receipt.round_id == 0
        || receipt.player_id == 0
        || receipt.player_id > MAX_EXACT_INT
        || !valid_text(&receipt.account_key, 64)
        || !valid_text(&receipt.player_name, 32)
        || !valid_text(&receipt.vehicle, 96)
        || !matches!(receipt.team, 1 | 2)
        || receipt.winner > 2
        || !valid_text(&receipt.map, 96)
        || !(1..=5).contains(&receipt.finish_reason)
        || receipt.rewards.repair_cost != 0
        || receipt.rewards.ammo_cost != 0
        || !(1..=MAX_BOTS).contains(&receipt.public_results.len())
        || receipt.interactions.len() > receipt.public_results.len()
    {
        return invalid("battle_receipt");
    }
    let mut actors = BTreeMap::new();
    let mut personal = None;
    for row in &receipt.public_results {
        if row.actor.id == 0
            || row.actor.id > MAX_EXACT_INT
            || !valid_text(&row.name, 32)
            || !valid_text(&row.vehicle, 96)
            || !matches!(row.team, 1 | 2)
            || !(-1..=255).contains(&row.death_reason)
            || row
                .killer
                .is_some_and(|killer| killer.id == 0 || killer.id > MAX_EXACT_INT)
            || actors.insert(row.actor, row.team).is_some()
        {
            return invalid("battle_receipt.public_results");
        }
        if row.actor
            == (ResultActorRef {
                kind: ResultActorKind::Player,
                id: receipt.player_id,
            })
        {
            personal = Some(row);
        }
    }
    let personal = personal.ok_or(ClientReplicationError::Invalid(
        "battle_receipt.personal_result",
    ))?;
    if personal.name != receipt.player_name
        || personal.vehicle != receipt.vehicle
        || personal.team != receipt.team
        || personal.death_reason != receipt.death_reason
        || personal.xp != receipt.rewards.xp
        || personal.stats != receipt.stats
    {
        return invalid("battle_receipt.personal_result");
    }
    let mut targets = BTreeSet::new();
    for interaction in &receipt.interactions {
        if actors.get(&interaction.target).is_none()
            || interaction.target
                == (ResultActorRef {
                    kind: ResultActorKind::Player,
                    id: receipt.player_id,
                })
            || actors.get(&interaction.target) == Some(&receipt.team)
            || !targets.insert(interaction.target)
            || interaction.spotted > 1
            || !(-1..=10).contains(&interaction.death_reason)
            || [
                interaction.direct_hits,
                interaction.explosion_hits,
                interaction.piercings,
                interaction.damage,
                interaction.assist_track,
                interaction.assist_radio,
                interaction.assist_stun,
                interaction.fire,
                interaction.stun_num,
                interaction.stun_duration,
                interaction.damage_received,
                interaction.ricochets_received,
                interaction.no_damage_direct_hits_received,
            ]
            .into_iter()
            .any(|value| value > 65_535)
            || interaction.crits > 4_294_967_295
            || interaction.damage_blocked > 4_294_967_295
            || interaction.target_kills > 255
        {
            return invalid("battle_receipt.interactions");
        }
    }
    Ok(())
}

fn validate_source_shot(shot: &SourceShot) -> Result<()> {
    const SHELL_KINDS: [&str; 5] = [
        "HOLLOW_CHARGE",
        "HIGH_EXPLOSIVE",
        "ARMOR_PIERCING",
        "ARMOR_PIERCING_HE",
        "ARMOR_PIERCING_CR",
    ];
    if !finite_range(shot.speed, 0.000_001, 3_000.0)
        || !finite_range(shot.gravity, 0.000_001, 500.0)
        || !finite_range(shot.max_distance, 0.000_001, 10_000.0)
        || shot
            .piercing_power
            .into_iter()
            .any(|value| !finite_range(value, 0.0, 10_000.0))
        || !SHELL_KINDS.contains(&shot.shell.kind.as_str())
        || !finite_range(shot.shell.caliber, 0.000_001, 1_000.0)
        || !finite_range(shot.shell.damage[0], 0.000_001, 10_000.0)
        || !finite_range(shot.shell.damage[1], 0.0, MAX_CRITICAL_DEVICE_HP)
        || !finite_range(shot.shell.explosion_radius, 0.0, 100.0)
    {
        return invalid("projectiles.source_shot");
    }
    Ok(())
}

fn validate_attacker(kind: &str, id: u64) -> Result<()> {
    if !matches!(kind, "" | "player" | "bot") || id > MAX_EXACT_INT || kind.is_empty() != (id == 0)
    {
        return invalid("attacker");
    }
    Ok(())
}

fn valid_actor(actor: VehicleKey) -> bool {
    actor.id > 0 && actor.id <= MAX_EXACT_INT
}

fn vehicle_kind_name(kind: VehicleKind) -> &'static str {
    match kind {
        VehicleKind::Player => "player",
        VehicleKind::Bot => "bot",
    }
}

fn projectile_outcome_name(outcome: ProjectileOutcome) -> &'static str {
    match outcome {
        ProjectileOutcome::Impact => "impact",
        ProjectileOutcome::Miss => "miss",
        ProjectileOutcome::Expired => "expired",
    }
}

/// Insert the legacy identity spelling used by the client's combat/shot
/// journal (`attacker`/`attacker_bot`, `target`/`target_bot`).
fn insert_actor_role(fields: &mut Map<String, Value>, role: &str, actor: VehicleKey) {
    let name = match actor.kind {
        VehicleKind::Player => role.to_owned(),
        VehicleKind::Bot => format!("{role}_bot"),
    };
    fields.insert(name, number(actor.id));
}

/// Insert the explicit identity spelling used by assist/statistics/wreck
/// events (`<role>_kind`, `<role>_id`).
fn insert_actor_pair(fields: &mut Map<String, Value>, role: &str, actor: VehicleKey) {
    fields.insert(
        format!("{role}_kind"),
        Value::String(vehicle_kind_name(actor.kind).to_owned()),
    );
    fields.insert(format!("{role}_id"), number(actor.id));
}

fn destructibles_value(values: &[DestructibleState]) -> Result<Value> {
    Ok(Value::Array(
        values
            .iter()
            .map(DestructibleState::to_value)
            .collect::<Result<Vec<_>>>()?,
    ))
}

fn insert_protocol(fields: &mut Map<String, Value>) {
    fields.insert("protocol".to_owned(), number(LAN_PROTOCOL_VERSION));
}

fn insert_scope(fields: &mut Map<String, Value>, scope: FrameScope, tick: bool) {
    fields.insert("round_id".to_owned(), number(scope.round_id));
    fields.insert("authority_epoch".to_owned(), number(scope.authority_epoch));
    fields.insert("server_time_ms".to_owned(), number(scope.server_time_ms));
    fields.insert("state_revision".to_owned(), number(scope.state_revision));
    if tick {
        fields.insert("server_tick".to_owned(), number(scope.server_tick));
    }
}

fn insert_server_authority(fields: &mut Map<String, Value>) {
    fields.insert(
        "bot_authority_id".to_owned(),
        Value::Number(SERVER_AUTHORITY_ID.into()),
    );
}

fn team_sizes_value(team_sizes: [u8; 2]) -> Value {
    let mut values = Map::new();
    values.insert("1".to_owned(), number(u64::from(team_sizes[0])));
    values.insert("2".to_owned(), number(u64::from(team_sizes[1])));
    Value::Object(values)
}

fn number(value: u64) -> Value {
    Value::Number(value.into())
}

fn signed_number(value: i64) -> Value {
    Value::Number(value.into())
}

fn value_float(value: f64) -> Result<Value> {
    serde_json::Number::from_f64(value)
        .map(Value::Number)
        .ok_or(ClientReplicationError::Invalid("finite_number"))
}

fn value_object(value: Value) -> Result<Map<String, Value>> {
    value
        .as_object()
        .cloned()
        .ok_or(ClientReplicationError::Invalid("object"))
}

fn exact_u64(value: Option<&Value>) -> Option<u64> {
    value?.as_u64()
}

fn exact_i64(value: Option<&Value>) -> Option<i64> {
    value?.as_i64()
}

fn invalid<T>(field: &'static str) -> Result<T> {
    Err(ClientReplicationError::Invalid(field))
}

fn valid_text(value: &str, maximum: usize) -> bool {
    let length = value.chars().count();
    length > 0 && length <= maximum && value.chars().all(|character| !character.is_control())
}

fn finite_range(value: f64, minimum: f64, maximum: f64) -> bool {
    value.is_finite() && (minimum..=maximum).contains(&value)
}

fn all_finite(values: &[f64]) -> bool {
    values.iter().all(|value| value.is_finite())
}

fn close(left: f64, right: f64) -> bool {
    (left - right).abs() <= 0.001_f64.max(right.abs() * 0.000_001)
}

fn round_six(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn vector_magnitude(vector: [f64; 3]) -> f64 {
    vector
        .into_iter()
        .map(|value| value * value)
        .sum::<f64>()
        .sqrt()
}

fn valid_world_position(position: [f64; 3]) -> bool {
    all_finite(&position)
        && position[0].abs() <= 5_000.0
        && (-1_000.0..=3_000.0).contains(&position[1])
        && position[2].abs() <= 5_000.0
}

fn valid_launch_velocity(velocity: [f64; 3]) -> bool {
    all_finite(&velocity)
        && velocity.into_iter().all(|value| value.abs() <= 3_000.0)
        && (0.000_001..=9_000_000.0)
            .contains(&velocity.into_iter().map(|value| value * value).sum::<f64>())
}

fn valid_projectile_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b':' | b'_' | b'-'))
}

/// Validate canonical RFC 4648 base64 without adding another crate dependency.
fn canonical_base64(value: &str, maximum_decoded: usize) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() % 4 != 0 || !bytes.is_ascii() {
        return false;
    }
    let padding = if bytes.ends_with(b"==") {
        2
    } else if bytes.ends_with(b"=") {
        1
    } else {
        0
    };
    let body = bytes.len() - padding;
    if bytes[..body]
        .iter()
        .any(|byte| base64_value(*byte).is_none())
        || bytes[body..].iter().any(|byte| *byte != b'=')
        || bytes[..body].contains(&b'=')
    {
        return false;
    }
    if padding == 2 {
        if body < 2 || base64_value(bytes[body - 1]).unwrap() & 0x0f != 0 {
            return false;
        }
    } else if padding == 1 && (body < 3 || base64_value(bytes[body - 1]).unwrap() & 0x03 != 0) {
        return false;
    }
    decoded_base64_len(value).is_some_and(|length| length > 0 && length <= maximum_decoded)
}

fn decoded_base64_len(value: &str) -> Option<usize> {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() % 4 != 0 {
        return None;
    }
    let padding = usize::from(bytes.ends_with(b"=")) + usize::from(bytes.ends_with(b"=="));
    bytes
        .len()
        .checked_div(4)?
        .checked_mul(3)?
        .checked_sub(padding)
}

fn base64_value(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::projectile::SourceShell;
    use serde_json::json;

    fn scope() -> FrameScope {
        FrameScope {
            round_id: 7,
            authority_epoch: 3,
            server_tick: 0,
            server_time_ms: 0,
            state_revision: 11,
        }
    }

    fn player(with_outfits: bool) -> PlayerState {
        PlayerState {
            id: 100,
            name: "Host".to_owned(),
            vehicle: "ussr:R11_MS-1".to_owned(),
            vehicle_compact_descr: String::new(),
            team: 1,
            slot: 0,
            world_pose: true,
            spawn_x: 0.0,
            spawn_z: -20.0,
            x: 0.0,
            y: 0.0,
            z: -20.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            aim_yaw: 0.0,
            gun_pitch: 0.0,
            forward: 0.0,
            turn: 0.0,
            speed: 0.0,
            input_seq: 0,
            landing_observation_seq: 0,
            up_cosine: 1.0,
            siege_state: 0,
            siege_time_left_ms: 0,
            fire_seq: 0,
            shell_index: 0,
            next_shell_index: 0,
            ammo_remaining: vec![51],
            ammo_reload_pending: false,
            reload_time: 0.0,
            reload_duration: 2.3,
            clip: 3,
            clip_size: 3,
            burst_active: false,
            burst_group_seq: 0,
            burst_count: 0,
            burst_next_index: 0,
            burst_interval: 0.0,
            burst_time_left: 0.0,
            burst_shell_index: 0,
            health: 100,
            max_health: 100,
            alive: true,
            death_reason: 0,
            display_health: 100,
            frags: 0,
            team_killer: false,
            death_attacker_kind: String::new(),
            death_attacker_id: 0,
            stun_end_server_time_ms: 0,
            stun_attacker_kind: String::new(),
            stun_attacker_id: 0,
            critical_revision: 0,
            critical_base_revision: 0,
            critical_ack_seq: 0,
            equipment_states: Vec::new(),
            equipment_revision: 0,
            equipment_intent_seq: 0,
            equipment_intent_result: serde_json::json!({
                "intent_seq": 0,
                "accepted": false,
                "reason": "",
            }),
            ram_contact_admitted_seq: 0,
            ram_contact_resolved_seq: 0,
            player_ram_contact_admitted_seq: 0,
            player_ram_contact_resolved_seq: 0,
            ram_contact_results: Vec::new(),
            outfits: with_outfits.then(|| BTreeMap::from([(1, "Ag==".to_owned())])),
            effective_params: with_outfits.then(|| serde_json::json!({"version": 1})),
            ram_contact: None,
            ram_contacts: Vec::new(),
            critical: None,
        }
    }

    fn profile() -> BotProfile {
        BotProfile {
            class_tag: "mediumTank".to_owned(),
            dominant_role: "support".to_owned(),
            roles: BTreeMap::from([("support".to_owned(), 0.8)]),
            desired_range: 180.0,
            fire_range: 500.0,
            speed: 40.0,
            armor: 50.0,
            shells: vec![BotShellProfile {
                index: 0,
                kind: "ARMOR_PIERCING".to_owned(),
                penetration: 50.0,
                damage: 40.0,
                speed: 100.0,
            }],
        }
    }

    fn manifest() -> BotManifestEntry {
        BotManifestEntry {
            id: 16,
            team: 2,
            slot: 0,
            name: "Bot-16".to_owned(),
            vehicle: "germany:G04_PzVI_Tiger_I".to_owned(),
            max_health: 1_000,
            health: 1_000,
            profile: profile(),
            route: BotRoute {
                id: "center_line".to_owned(),
                waypoints: vec![RouteWaypoint {
                    x: 0.0,
                    y: 0.0,
                    z: 20.0,
                    hold: false,
                }],
            },
        }
    }

    fn bot_state() -> BotState {
        BotState {
            id: 16,
            team: 2,
            slot: 0,
            name: "Bot-16".to_owned(),
            vehicle: "germany:G04_PzVI_Tiger_I".to_owned(),
            world_pose: true,
            x: 0.0,
            y: 0.0,
            z: 20.0,
            yaw: PI,
            pitch: 0.0,
            roll: 0.0,
            aim_yaw: PI,
            gun_pitch: 0.0,
            movement_dir: 0,
            rotation_dir: 0,
            fire_seq: 0,
            shell_index: 0,
            next_shell_index: 0,
            ammo_remaining: vec![20],
            ammo_reload_pending: false,
            health: 1_000,
            max_health: 1_000,
            alive: true,
            frags: 0,
            team_killer: false,
            death_attacker_kind: String::new(),
            death_attacker_id: 0,
            combat_revision: 0,
            combat_base_revision: 0,
            combat_ack_seq: 0,
            combat_fire_elapsed: 0.0,
            combat_fire_timer: 0.0,
            fire_attacker_kind: String::new(),
            fire_attacker_id: 0,
            stun_end_server_time_ms: 0,
            stun_attacker_kind: String::new(),
            stun_attacker_id: 0,
            equipment_states: Vec::new(),
            critical: json!({}),
            shot_yaw: None,
            shot_pitch: None,
            death_reason: 0,
            display_health: 1_000,
        }
    }

    fn personality() -> BotPersonality {
        BotPersonality {
            aggression: 0.5,
            caution: 0.5,
            teamwork: 0.5,
            patience: 0.5,
            initiative: 0.5,
            adaptability: 0.5,
            jiggle: 0.5,
        }
    }

    fn order() -> BotOrder {
        BotOrder {
            id: 16,
            team: 2,
            target_id: None,
            target_kind: None,
            aim_position: None,
            face_position: None,
            move_position: Point3 {
                x: 0.0,
                y: 0.0,
                z: 10.0,
            },
            fire_allowed: false,
            combat_mode: "route".to_owned(),
            throttle_override: None,
            desired_range: 180.0,
            fire_range: 500.0,
            route_id: "center_line".to_owned(),
            route_index: 0,
            route_anchor: Point3 {
                x: 0.0,
                y: 0.0,
                z: 10.0,
            },
            route_join: false,
            personality: personality(),
            profile: profile(),
            shell_index: 0,
            cover_id: None,
            defense_base_id: None,
            hull_angle_degrees: None,
        }
    }

    fn rules() -> RulesState {
        RulesState {
            team_1: CaptureBaseState {
                points: 0,
                time_left: 0.0,
                invaders: 0,
                stopped: false,
            },
            team_2: CaptureBaseState {
                points: 0,
                time_left: 0.0,
                invaders: 0,
                stopped: false,
            },
        }
    }

    fn battle_timing() -> TimingState {
        TimingState {
            phase: CombatPhase::Battle,
            start_in_ms: 0,
            remaining_ms: 899_000,
            duration_ms: 900_000,
        }
    }

    fn snapshot() -> SnapshotFrame {
        SnapshotFrame {
            scope: FrameScope {
                server_tick: 30,
                server_time_ms: 1_000,
                ..scope()
            },
            map: "spaces/01_karelia".to_owned(),
            motion_time_us: 1_000_000,
            bot_state_time_us: 1_000_000,
            players: vec![player(false)],
            bots: vec![bot_state()],
            contacts: vec![ContactState {
                observing_team: 1,
                target_kind: "bot".to_owned(),
                target_id: 16,
                target_team: 2,
                visible: true,
                fresh: true,
                time_left: 10.0,
                visible_by_bot_ids: vec![15],
                visible_by_player_ids: Vec::new(),
                shootable_by_bot_ids: vec![15],
            }],
            bot_state_revision: 1,
            bot_manifest: vec![manifest()],
            bot_orders: RevisionPayload::Include {
                revision: 1,
                values: vec![order()],
            },
            rules: rules(),
            battle_result: None,
            destructibles: RevisionPayload::Include {
                revision: 0,
                values: Vec::new(),
            },
            timing: battle_timing(),
            projectile_revision: 0,
            projectiles: Vec::new(),
        }
    }

    fn start() -> BattleStart {
        BattleStart {
            client_build: CLIENT_BUILD_0922.to_owned(),
            scope: scope(),
            recipient_player_id: 100,
            map: "spaces/01_karelia".to_owned(),
            requested_by: 100,
            host_player_id: 100,
            delay_seconds: 0.75,
            need_destructible_map: true,
            players: vec![player(true)],
            bots: vec![BotRosterEntry {
                id: 16,
                team: 2,
                slot: 0,
                name: "Bot-16".to_owned(),
            }],
            team_sizes: [1, 1],
            bot_tier_mode: BotTierMode::Same,
            bot_lineup: vec![BotLineupEntry {
                team: 2,
                slot: 0,
                vehicle: "germany:G12_Ltraktor".to_owned(),
            }],
            bot_manifest: vec![manifest()],
            bot_order_revision: 1,
            bot_orders: vec![order()],
            rules: rules(),
            battle_result: None,
            destructible_revision: 0,
            destructibles: Vec::new(),
        }
    }

    #[test]
    fn battle_start_matches_client_barrier_and_server_authority_contract() {
        let message = encode_battle_start(&start()).unwrap();
        assert_eq!(message.kind(), "battle_start");
        assert_eq!(message.protocol(), Some(5));
        assert_eq!(message.get("phase"), Some(&json!("loading")));
        assert_eq!(message.get("bot_authority_id"), Some(&json!(0)));
        assert_eq!(message.get("authority_epoch"), Some(&json!(3)));
        assert_eq!(message.get("server_time_ms"), Some(&json!(0)));
        assert_eq!(message.get("team_sizes"), Some(&json!({"1": 1, "2": 1})));
        assert_eq!(message.get("bot_tier_mode"), Some(&json!("same")));
        assert_eq!(
            message.get("bot_lineup"),
            Some(&json!([{
                "team": 2,
                "slot": 0,
                "vehicle": "germany:G12_Ltraktor",
            }]))
        );
        let public_player = &message.get("players").unwrap().as_array().unwrap()[0];
        assert_eq!(public_player.get("vehicle_compact_descr"), Some(&json!("")));
        assert_eq!(public_player.get("input_seq"), Some(&json!(0)));
        assert_eq!(
            public_player.get("ram_contact_admitted_seq"),
            Some(&json!(0))
        );
        assert_eq!(
            public_player.get("ram_contact_resolved_seq"),
            Some(&json!(0))
        );
        for required in [
            "round_id",
            "state_revision",
            "map",
            "requested_by",
            "host_player_id",
            "players",
            "bots",
            "bot_tier_mode",
            "bot_lineup",
            "bot_manifest",
            "bot_order_revision",
            "bot_orders",
            "rules",
            "battle_result",
            "destructible_revision",
            "destructibles",
        ] {
            assert!(message.get(required).is_some(), "missing {required}");
        }
        assert_eq!(message.get("need_destructible_map"), Some(&json!(true)));
        for retired in [
            "worker_status",
            "authority_status",
            "authority_fallback_reason",
        ] {
            assert!(message.get(retired).is_none(), "retired field {retired}");
        }
    }

    #[test]
    fn battle_start_fails_closed_for_missing_recipient_or_outfits() {
        let mut invalid = start();
        invalid.recipient_player_id = 999;
        assert!(encode_battle_start(&invalid).is_err());

        let mut invalid = start();
        invalid.players[0].outfits = None;
        assert!(encode_battle_start(&invalid).is_err());

        let mut invalid = start();
        invalid.players[0].vehicle_compact_descr = "AQ=".to_owned();
        assert!(encode_battle_start(&invalid).is_err());
    }

    #[test]
    fn battle_live_carries_tick_timing_and_authority_zero() {
        let live = BattleLive {
            client_build: CLIENT_BUILD_0922.to_owned(),
            scope: scope(),
            countdown_seconds: 5.0,
            battle_duration_seconds: 900.0,
            timing: TimingState {
                phase: CombatPhase::Prebattle,
                start_in_ms: 5_000,
                remaining_ms: 900_000,
                duration_ms: 900_000,
            },
        };
        let message = encode_battle_live(&live).unwrap();
        assert_eq!(message.kind(), "battle_live");
        assert_eq!(message.get("server_tick"), Some(&json!(0)));
        assert_eq!(message.get("state_revision"), Some(&json!(11)));
        assert_eq!(message.get("bot_authority_id"), Some(&json!(0)));
        assert_eq!(
            message.get("timing").unwrap().get("phase"),
            Some(&json!("prebattle"))
        );
    }

    #[test]
    fn full_then_lean_snapshot_requires_matching_manifest_lineage() {
        let frame = snapshot();
        let full = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        assert!(full.get("bot_manifest").is_some());
        assert_eq!(full.get("bot_authority_id"), Some(&json!(0)));
        assert_eq!(full.get("projectiles"), Some(&json!([])));
        assert_eq!(full.get("bot_order_revision"), Some(&json!(1)));
        assert!(full.get("bot_orders").is_some());
        assert!(full.get("destructibles").is_some());

        let lineage = FullManifestLineage::from_full_snapshot(&full).unwrap();
        let lean = encode_snapshot(&frame, SnapshotManifest::Lean(&lineage)).unwrap();
        assert!(lean.get("bot_manifest").is_none());
        assert_eq!(lean.get("authority_epoch"), full.get("authority_epoch"));

        let mut next_epoch = frame.clone();
        next_epoch.scope.authority_epoch += 1;
        assert!(encode_snapshot(&next_epoch, SnapshotManifest::Lean(&lineage)).is_err());
    }

    #[test]
    fn snapshot_carries_canonical_player_and_bot_stun_state() {
        let mut frame = snapshot();
        frame.players[0].stun_end_server_time_ms = 2_500;
        frame.players[0].stun_attacker_kind = "bot".to_owned();
        frame.players[0].stun_attacker_id = 16;
        frame.bots[0].stun_end_server_time_ms = 2_750;
        frame.bots[0].stun_attacker_kind = "player".to_owned();
        frame.bots[0].stun_attacker_id = 100;

        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        let player = &message.get("players").unwrap().as_array().unwrap()[0];
        let bot = &message.get("bots").unwrap().as_array().unwrap()[0];
        assert_eq!(player["stun_end_server_time_ms"], 2_500);
        assert_eq!(player["stun_attacker_kind"], "bot");
        assert_eq!(player["stun_attacker_id"], 16);
        assert_eq!(bot["stun_end_server_time_ms"], 2_750);
        assert_eq!(bot["stun_attacker_kind"], "player");
        assert_eq!(bot["stun_attacker_id"], 100);
    }

    #[test]
    fn snapshot_fails_closed_for_incomplete_bot_combat_contract() {
        let mut frame = snapshot();
        frame.bots[0].critical = Value::Null;
        assert!(encode_snapshot(&frame, SnapshotManifest::Full).is_err());

        let mut frame = snapshot();
        frame.bots[0].combat_base_revision = 2;
        frame.bots[0].combat_revision = 1;
        assert!(encode_snapshot(&frame, SnapshotManifest::Full).is_err());

        let mut frame = snapshot();
        frame.bot_state_time_us = frame.motion_time_us + 1;
        assert!(encode_snapshot(&frame, SnapshotManifest::Full).is_err());
    }

    #[test]
    fn snapshot_contact_requires_one_consistent_relative_visibility_lease() {
        let frame = snapshot();
        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        assert_eq!(
            message.get("contacts").unwrap()[0],
            json!({
                "observing_team": 1,
                "target_kind": "bot",
                "target_id": 16,
                "target_team": 2,
                "visible": true,
                "fresh": true,
                "time_left": 10.0,
                "visible_by_bot_ids": [15],
                "visible_by_player_ids": [],
                "shootable_by_bot_ids": [15],
            })
        );

        let mut remembered = snapshot();
        let contact = &mut remembered.contacts[0];
        contact.fresh = false;
        contact.time_left = 6.5;
        contact.visible_by_bot_ids.clear();
        contact.shootable_by_bot_ids.clear();
        assert!(encode_snapshot(&remembered, SnapshotManifest::Full).is_ok());

        let mut renewed_without_observer = remembered.clone();
        renewed_without_observer.contacts[0].fresh = true;
        assert!(encode_snapshot(&renewed_without_observer, SnapshotManifest::Full).is_err());

        let mut stale_shooter = remembered;
        stale_shooter.contacts[0].shootable_by_bot_ids = vec![15];
        assert!(encode_snapshot(&stale_shooter, SnapshotManifest::Full).is_err());
    }

    #[test]
    fn snapshot_manifest_orders_and_destructibles_are_independent_deltas() {
        let mut frame = snapshot();
        frame.bot_orders = RevisionPayload::Omit { revision: 1 };
        frame.destructibles = RevisionPayload::Omit { revision: 0 };
        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        assert_eq!(message.get("bot_order_revision"), Some(&json!(1)));
        assert!(message.get("bot_orders").is_none());
        assert_eq!(message.get("destructible_revision"), Some(&json!(0)));
        assert!(message.get("destructibles").is_none());
    }

    #[test]
    fn terminal_snapshot_can_replicate_after_all_visible_players_disconnect() {
        let mut frame = snapshot();
        frame.players.clear();
        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        assert_eq!(message.get("players"), Some(&json!([])));
    }

    fn source_shot() -> SourceShot {
        SourceShot {
            speed: 100.0,
            gravity: 9.81,
            max_distance: 500.0,
            piercing_power: [50.0, 40.0],
            deadeye: false,
            shell: SourceShell {
                kind: "ARMOR_PIERCING".to_owned(),
                caliber: 45.0,
                damage: [40.0, 5.0],
                explosion_radius: 0.0,
                explosion_damage_factor: None,
                explosion_damage_absorption_factor: None,
                explosion_edge_damage_factor: None,
            },
        }
    }

    #[test]
    fn trusted_large_module_damage_survives_replication_validation() {
        let mut shot = source_shot();
        shot.shell.damage[1] = MAX_CRITICAL_DEVICE_HP;
        assert!(validate_source_shot(&shot).is_ok());

        shot.shell.damage[1] = MAX_CRITICAL_DEVICE_HP + 1.0;
        assert!(validate_source_shot(&shot).is_err());
    }

    fn projectile(kind: VehicleKind) -> ProjectileWireState {
        ProjectileWireState {
            projectile_id: "7:player:100:1".to_owned(),
            shooter_kind: kind,
            shooter_id: 100,
            shot_seq: 1,
            source_vehicle: "ussr:R11_MS-1".to_owned(),
            source_shot: source_shot(),
            shell_index: 0,
            team: 1,
            origin: [0.0, 1.0, -20.0],
            velocity: [100.0, 0.0, 0.0],
            gravity: 9.81,
            max_distance: 500.0,
            max_time_ms: 5_000,
            is_he: false,
            splash_radius: 0.0,
            penetration_factor: 1.0,
            launch_server_time_ms: 900,
            checked_through_ms: 100,
            checked_distance: 10.0,
            piercing_loss: 0.0,
            authority_epoch: 3,
            fire_intent_seq: None,
            fire_input_seq: None,
        }
    }

    fn player_actor(id: u64) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id,
        }
    }

    fn bot_actor(id: u64) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Bot,
            id,
        }
    }

    fn event_roster() -> EventRoster {
        EventRoster::try_new([
            (player_actor(100), 1),
            (player_actor(101), 1),
            (player_actor(200), 2),
            (bot_actor(1), 1),
            (bot_actor(16), 2),
        ])
        .unwrap()
    }

    fn event_scope() -> FrameScope {
        FrameScope {
            server_tick: 30,
            server_time_ms: 1_000,
            ..scope()
        }
    }

    fn shot_impact(id: &str) -> ShotImpact {
        ShotImpact {
            projectile_id: id.to_owned(),
            shot_seq: 1,
            shell_index: 0,
            shot_result: 1,
            blocked_damage: 0,
            splash: false,
            impact: Point3 {
                x: 1.0,
                y: 2.0,
                z: 3.0,
            },
        }
    }

    fn damage_commit(
        source: DamageSource,
        attacker: Option<VehicleKey>,
        target: VehicleKey,
    ) -> DamageCommit {
        DamageCommit {
            attacker,
            target,
            requested: 40,
            applied: 40,
            health: 60,
            dead: false,
            source,
        }
    }

    fn combat_event(
        source: DamageSource,
        attacker: Option<VehicleKey>,
        target: VehicleKey,
        projectile_id: &str,
    ) -> CombatEvent {
        CombatEvent {
            commit: damage_commit(source, attacker, target),
            death_reason: 0,
            display_health: None,
            client_simulation_reason: (source == DamageSource::Environment).then_some(5),
            shot: (source == DamageSource::Shot).then(|| shot_impact(projectile_id)),
            critical: None,
            critical_revision: None,
        }
    }

    fn critical_payload() -> CriticalPayload {
        CriticalPayload {
            devices: vec![CriticalDevice {
                name: CriticalDeviceName::EngineHealth,
                hp: 0.0,
                max_hp: 100.0,
                state: CriticalDeviceState::Destroyed,
            }],
            destroyed: vec![CriticalDeviceName::EngineHealth],
            crew_ko: vec![CriticalCrewName::Driver],
            crew_roster: Some(vec![CriticalCrewName::Commander, CriticalCrewName::Driver]),
            fire: false,
            ammo_rack_death: false,
            events: vec![CriticalTransition::Device {
                name: CriticalDeviceName::EngineHealth,
                state: CriticalDeviceState::Destroyed,
                old_state: Some(CriticalDeviceState::Normal),
                cause: CriticalCause::Shot,
            }],
        }
    }

    #[test]
    fn projectile_snapshot_requires_player_fire_binding_and_source_law() {
        let mut frame = snapshot();
        frame.projectile_revision = 1;
        frame.projectiles = vec![projectile(VehicleKind::Player)];
        assert!(encode_snapshot(&frame, SnapshotManifest::Full).is_err());

        frame.projectiles[0].fire_intent_seq = Some(1);
        frame.projectiles[0].fire_input_seq = Some(1);
        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        let row = &message.get("projectiles").unwrap().as_array().unwrap()[0];
        assert_eq!(row.get("authority_epoch"), Some(&json!(3)));
        assert_eq!(row.get("fire_intent_seq"), Some(&json!(1)));

        frame.projectiles[0].gravity = 10.0;
        assert!(encode_snapshot(&frame, SnapshotManifest::Full).is_err());
    }

    #[test]
    fn shot_events_use_client_identity_and_full_projectile_contract() {
        let mut launched = projectile(VehicleKind::Player);
        launched.fire_intent_seq = Some(7);
        launched.fire_input_seq = Some(9);
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 4,
            roster: event_roster(),
            events: vec![BattleClientEvent::Shot(ShotEvent {
                projectile: launched.clone(),
                burst: PlayerAmmoBurst::ordinary(launched.shot_seq),
            })],
        })
        .unwrap();
        assert_eq!(message.kind(), "events");
        assert_eq!(message.get("bot_authority_id"), Some(&json!(0)));
        assert_eq!(message.get("server_time_ms"), Some(&json!(1_000)));
        let event = &message.get("events").unwrap().as_array().unwrap()[0];
        assert_eq!(event.get("event_id"), Some(&json!("7:30:4")));
        assert_eq!(event.get("kind"), Some(&json!("shot")));
        assert_eq!(event.get("attacker"), Some(&json!(100)));
        assert!(event.get("attacker_bot").is_none());
        assert_eq!(event.get("maxDistance"), Some(&json!(500.0)));
        assert!(event.get("max_distance").is_none());
        assert_eq!(event.get("fire_intent_seq"), Some(&json!(7)));
        assert_eq!(event.get("fire_input_seq"), Some(&json!(9)));
        for required in [
            "projectile_id",
            "shot_seq",
            "burst_group_seq",
            "burst_index",
            "burst_count",
            "shell_index",
            "origin",
            "velocity",
            "gravity",
            "max_time_ms",
            "is_he",
            "splash_radius",
            "penetration_factor",
            "launch_server_time_ms",
            "shooter_kind",
            "shooter_id",
            "source_vehicle",
            "source_shot",
            "authority_epoch",
            "shot_yaw",
            "shot_pitch",
        ] {
            assert!(event.get(required).is_some(), "missing {required}");
        }

        let mut bot_launch = projectile(VehicleKind::Bot);
        bot_launch.projectile_id = "7:bot:1:1".to_owned();
        bot_launch.shooter_id = 1;
        let bot_message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Shot(ShotEvent {
                burst: PlayerAmmoBurst::ordinary(bot_launch.shot_seq),
                projectile: bot_launch,
            })],
        })
        .unwrap();
        let bot_event = &bot_message.get("events").unwrap().as_array().unwrap()[0];
        assert_eq!(bot_event.get("kind"), Some(&json!("bot_shot")));
        assert_eq!(bot_event.get("attacker_bot"), Some(&json!(1)));
        assert!(bot_event.get("fire_intent_seq").is_none());

        launched.fire_input_seq = None;
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Shot(ShotEvent {
                burst: PlayerAmmoBurst::ordinary(launched.shot_seq),
                projectile: launched,
            })],
        })
        .is_err());
    }

    #[test]
    fn authority_and_manifest_barriers_are_server_owned() {
        let start = start();
        let roster = EventRoster::from_battle_start(&start).unwrap();
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster,
            events: vec![
                BattleClientEvent::Authority,
                BattleClientEvent::BotManifest(BotManifestEvent {
                    bots: start.bot_manifest,
                }),
            ],
        })
        .unwrap();
        let events = message.get("events").unwrap().as_array().unwrap();
        assert_eq!(events[0].get("kind"), Some(&json!("authority")));
        assert_eq!(events[0].get("player_id"), Some(&json!(0)));
        assert_eq!(events[0].get("authority_epoch"), Some(&json!(3)));
        assert_eq!(events[1].get("kind"), Some(&json!("bot_manifest")));
        assert_eq!(events[1].get("bots").unwrap().as_array().unwrap().len(), 1);
    }

    #[test]
    fn combat_events_cover_all_client_actor_kinds_and_death_metadata() {
        let actor_pairs = [
            (player_actor(100), player_actor(200), "hit"),
            (player_actor(100), bot_actor(16), "bot_hit"),
            (bot_actor(1), player_actor(200), "bot_human_hit"),
            (bot_actor(1), bot_actor(16), "bot_bot_hit"),
        ];
        let events = actor_pairs
            .iter()
            .enumerate()
            .map(|(index, &(attacker, target, _))| {
                BattleClientEvent::Combat(combat_event(
                    DamageSource::Shot,
                    Some(attacker),
                    target,
                    &format!("projectile-{index}"),
                ))
            })
            .collect();
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events,
        })
        .unwrap();
        let values = message.get("events").unwrap().as_array().unwrap();
        assert_eq!(values.len(), actor_pairs.len());
        for (value, (_, _, expected_kind)) in values.iter().zip(actor_pairs) {
            assert_eq!(value.get("kind"), Some(&json!(expected_kind)));
            assert_eq!(value.get("source"), Some(&json!("shot")));
            assert_eq!(value.get("attack_reason"), Some(&json!(0)));
            assert_eq!(value.get("death_reason"), Some(&json!(0)));
            assert_eq!(value.get("world_pose"), Some(&json!(true)));
        }

        let mut death = combat_event(
            DamageSource::Shot,
            Some(player_actor(100)),
            bot_actor(16),
            "fatal-projectile",
        );
        death.commit.health = 0;
        death.commit.dead = true;
        death.death_reason = 3;
        death.display_health = Some(125);
        death.critical = Some(critical_payload());
        death.critical_revision = Some(CriticalRevision::Bot {
            revision: 5,
            base_revision: 4,
            ack_seq: 2,
        });
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 10,
            roster: event_roster(),
            events: vec![BattleClientEvent::Combat(death)],
        })
        .unwrap();
        let event = &message.get("events").unwrap().as_array().unwrap()[0];
        assert_eq!(event.get("dead"), Some(&json!(true)));
        assert_eq!(event.get("death_reason"), Some(&json!(3)));
        assert_eq!(event.get("display_health"), Some(&json!(125)));
        assert_eq!(event.get("combat_revision"), Some(&json!(5)));
        assert_eq!(event.get("combat_base_revision"), Some(&json!(4)));
        assert_eq!(event.get("combat_ack_seq"), Some(&json!(2)));
        assert_eq!(
            event
                .get("critical")
                .unwrap()
                .get("devices")
                .unwrap()
                .as_array()
                .unwrap()[0]
                .get("name"),
            Some(&json!("engineHealth"))
        );
    }

    #[test]
    fn fire_environment_and_player_left_use_exact_client_sources() {
        let fire = combat_event(
            DamageSource::Fire,
            Some(bot_actor(1)),
            player_actor(200),
            "unused-fire",
        );
        let environment = combat_event(
            DamageSource::Environment,
            None,
            bot_actor(16),
            "unused-environment",
        );
        let mut player_left = combat_event(
            DamageSource::PlayerLeft,
            None,
            player_actor(200),
            "unused-left",
        );
        player_left.commit.health = 0;
        player_left.commit.dead = true;
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![
                BattleClientEvent::Combat(fire),
                BattleClientEvent::Combat(environment),
                BattleClientEvent::Combat(player_left),
            ],
        })
        .unwrap();
        let events = message.get("events").unwrap().as_array().unwrap();
        assert_eq!(events[0].get("kind"), Some(&json!("bot_human_hit")));
        assert_eq!(events[0].get("source"), Some(&json!("fire")));
        assert_eq!(events[0].get("attack_reason"), Some(&json!(1)));
        assert_eq!(events[1].get("kind"), Some(&json!("health")));
        assert_eq!(events[1].get("source"), Some(&json!("client_simulation")));
        assert_eq!(events[1].get("attack_reason"), Some(&json!(5)));
        assert!(events[1].get("attacker").is_none());
        assert!(events[1].get("attacker_bot").is_none());
        assert_eq!(events[2].get("kind"), Some(&json!("health")));
        assert_eq!(events[2].get("source"), Some(&json!("player_left")));
        assert_eq!(events[2].get("attack_reason"), Some(&Value::Null));
        assert_eq!(events[2].get("death_reason"), Some(&json!(0)));
    }

    #[test]
    fn combat_contract_fails_closed_for_incomplete_or_unsupported_causes() {
        let mut critical_without_revision = combat_event(
            DamageSource::Shot,
            Some(player_actor(100)),
            bot_actor(16),
            "critical-projectile",
        );
        critical_without_revision.critical = Some(critical_payload());
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Combat(critical_without_revision)],
        })
        .is_err());

        let player_fire = combat_event(
            DamageSource::Fire,
            Some(player_actor(100)),
            player_actor(200),
            "unused",
        );
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Combat(player_fire)],
        })
        .is_err());

        let mut inconsistent_block = combat_event(
            DamageSource::Shot,
            Some(player_actor(100)),
            bot_actor(16),
            "blocked-projectile",
        );
        inconsistent_block.shot.as_mut().unwrap().blocked_damage = 200;
        inconsistent_block.shot.as_mut().unwrap().splash = true;
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Combat(inconsistent_block)],
        })
        .is_err());

        let mut missing_environment_reason =
            combat_event(DamageSource::Environment, None, player_actor(200), "unused");
        missing_environment_reason.client_simulation_reason = None;
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::Combat(missing_environment_reason)],
        })
        .is_err());
    }

    #[test]
    fn ram_is_two_ordered_combat_events_with_exact_reason() {
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 20,
            roster: event_roster(),
            events: vec![
                BattleClientEvent::Combat(combat_event(
                    DamageSource::Ram,
                    Some(player_actor(100)),
                    bot_actor(16),
                    "unused-a",
                )),
                BattleClientEvent::Combat(combat_event(
                    DamageSource::Ram,
                    Some(bot_actor(16)),
                    player_actor(100),
                    "unused-b",
                )),
            ],
        })
        .unwrap();
        let events = message.get("events").unwrap().as_array().unwrap();
        assert_eq!(events[0].get("source"), Some(&json!("ram")));
        assert_eq!(events[0].get("attack_reason"), Some(&json!(2)));
        assert_eq!(events[0].get("event_id"), Some(&json!("7:30:20")));
        assert_eq!(events[1].get("source"), Some(&json!("ram")));
        assert_eq!(events[1].get("attack_reason"), Some(&json!(2)));
        assert_eq!(events[1].get("event_id"), Some(&json!("7:30:21")));
    }

    #[test]
    fn projectile_terminal_uses_impact_kind_and_outcome_invariants() {
        let resolution = ProjectileResolution {
            round_id: 7,
            authority_epoch: 3,
            projectile_id: "terminal-projectile".to_owned(),
            base_checked_ms: 100,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 200,
            checked_distance: 20.0,
            piercing_loss: 2.0,
            penetration_factor: 0.9,
            impact: Some(crate::projectile::ProjectileVec3 {
                x: 1.0,
                y: 2.0,
                z: 3.0,
            }),
        };
        let event = ProjectileImpactEvent {
            resolution: resolution.clone(),
            shooter: player_actor(100),
            shot_seq: 1,
            hit_vehicle: true,
            wreck_hit: Some(bot_actor(16)),
        };
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::ProjectileImpact(event)],
        })
        .unwrap();
        let value = &message.get("events").unwrap().as_array().unwrap()[0];
        assert_eq!(value.get("kind"), Some(&json!("projectile_impact")));
        assert_eq!(value.get("outcome"), Some(&json!("impact")));
        assert_eq!(
            value.get("wreck_hit"),
            Some(&json!({"target_kind": "bot", "target_id": 16}))
        );
        assert!(value.get("base_checked_ms").is_none());

        let invalid = ProjectileImpactEvent {
            resolution: ProjectileResolution {
                outcome: ProjectileOutcome::Miss,
                ..resolution
            },
            shooter: player_actor(100),
            shot_seq: 1,
            hit_vehicle: false,
            wreck_hit: None,
        };
        assert!(encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![BattleClientEvent::ProjectileImpact(invalid)],
        })
        .is_err());
    }

    #[test]
    fn destructible_statistics_assist_and_stun_events_match_client_fields() {
        let stored = StoredDestructible {
            receipt: crate::destructible::DestructibleReceipt {
                key: crate::destructible::DestructibleKey {
                    kind: LedgerDestructibleKind::Module,
                    chunk_id: 17,
                    item_index: 4,
                    material_kind: Some(9),
                },
                x: 1.0,
                y: 2.0,
                z: 3.0,
                fall_yaw: 0.5,
                speed: 6.0,
                is_shot: true,
            },
            revision: 2,
            reported_by: SERVER_AUTHORITY_ID,
        };
        let destructible = DestructibleState::from_stored(&stored).unwrap();
        let assist = AssistEvent::from(&AssistAward {
            category: AssistCategory::Track,
            assister: player_actor(101),
            attacker: player_actor(100),
            target: bot_actor(16),
            damage: 40,
        });
        let message = encode_battle_events(&BattleEventsFrame {
            scope: event_scope(),
            first_ordinal: 0,
            roster: event_roster(),
            events: vec![
                BattleClientEvent::Destructible(destructible),
                BattleClientEvent::VehicleStatistics(VehicleStatisticsEvent {
                    actor: player_actor(100),
                    frags: -1,
                    team_killer: true,
                }),
                BattleClientEvent::Assist(assist),
                BattleClientEvent::Stun(StunEvent {
                    active: true,
                    target: bot_actor(16),
                    attacker: Some(player_actor(100)),
                    end_server_time_ms: 2_500,
                }),
                BattleClientEvent::Stun(StunEvent {
                    active: false,
                    target: bot_actor(16),
                    attacker: None,
                    end_server_time_ms: 0,
                }),
            ],
        })
        .unwrap();
        let events = message.get("events").unwrap().as_array().unwrap();
        assert_eq!(events[0].get("kind"), Some(&json!("destructible")));
        assert_eq!(events[0].get("destructible_kind"), Some(&json!("module")));
        assert_eq!(events[0].get("mat_kind"), Some(&json!(9)));
        assert_eq!(events[0].get("x"), Some(&json!(1.0)));
        assert_eq!(events[0].get("reported_by"), Some(&json!(0)));
        assert_eq!(events[1].get("frags"), Some(&json!(-1)));
        assert_eq!(events[1].get("team_killer"), Some(&json!(true)));
        assert_eq!(events[2].get("category"), Some(&json!("track")));
        assert_eq!(events[2].get("assister_kind"), Some(&json!("player")));
        assert_eq!(events[2].get("target_kind"), Some(&json!("bot")));
        assert_eq!(events[3].get("kind"), Some(&json!("stun")));
        assert_eq!(events[3].get("active"), Some(&json!(true)));
        assert_eq!(events[3].get("attacker_kind"), Some(&json!("player")));
        assert_eq!(
            events[3].get("stun_end_server_time_ms"),
            Some(&json!(2_500))
        );
        assert_eq!(events[4].get("active"), Some(&json!(false)));
        assert!(events[4].get("attacker_kind").is_none());

        let mut retired_authority = stored;
        retired_authority.reported_by = -1;
        assert!(DestructibleState::from_stored(&retired_authority).is_err());
    }

    #[test]
    fn capture_is_snapshot_rules_not_an_ordered_event_kind() {
        use crate::rules::{MapPoint, VehicleForRules, VehicleKey as RulesVehicleKey};

        let mut standard = StandardRules::new(
            vec![MapPoint::new(-100.0, 0.0)],
            vec![MapPoint::new(100.0, 0.0)],
        );
        let update = standard.update(
            30,
            true,
            &[VehicleForRules {
                key: RulesVehicleKey::Human(100),
                team: Team::One,
                alive: true,
                world_pose: true,
                x: 100.0,
                z: 0.0,
            }],
        );
        assert!(update.changed);
        let capture = RulesState::from_standard_rules(&standard).unwrap();
        assert_eq!(capture.team_2.points, 1);

        let mut frame = snapshot();
        frame.rules = capture;
        let message = encode_snapshot(&frame, SnapshotManifest::Full).unwrap();
        assert_eq!(
            message
                .get("rules")
                .unwrap()
                .get("bases")
                .unwrap()
                .get("2")
                .unwrap()
                .get("points"),
            Some(&json!(1))
        );
        assert!(!client_supports_event_kind("capture"));
        assert!(!client_supports_event_kind("projectile_launch"));
        assert!(!client_supports_event_kind("projectile_resolved"));
        assert!(!client_supports_event_kind("fire_tick"));
        assert!(client_supports_event_kind("projectile_impact"));
    }

    #[test]
    fn battle_result_uses_actual_events_envelope() {
        let frame = BattleResultFrame {
            scope: FrameScope {
                server_tick: 900,
                server_time_ms: 30_000,
                ..scope()
            },
            ordinal: 2,
            result: BattleResultState {
                winner: 1,
                reason: "team_eliminated".to_owned(),
                base_team: 0,
                vehicle_statistics: Some(vec![BattleVehicleStatistics {
                    actor_kind: VehicleKind::Player,
                    actor_id: 100,
                    team: 1,
                    shots_fired: 2,
                    shots_hit: 1,
                    shots_penetrated: 1,
                    damage_dealt: 400,
                    damage_received: 0,
                    damage_blocked: 0,
                    damage_assisted_track: 0,
                    damage_assisted_radio: 0,
                    kills: 1,
                }]),
            },
        };
        let message = encode_battle_result(&frame).unwrap();
        assert_eq!(message.kind(), "events");
        assert_eq!(message.get("bot_authority_id"), Some(&json!(0)));
        let event = &message.get("events").unwrap().as_array().unwrap()[0];
        assert_eq!(event.get("kind"), Some(&json!("battle_result")));
        assert_eq!(event.get("event_id"), Some(&json!("7:900:2")));
        assert_eq!(event.get("winner"), Some(&json!(1)));
        assert_eq!(
            event.get("vehicle_statistics").unwrap().as_array().unwrap()[0].get("damage_dealt"),
            Some(&json!(400))
        );
    }

    fn stats() -> ResultStats {
        ResultStats {
            shots: 1,
            direct_hits: 1,
            piercings: 1,
            damage: 100,
            damage_received: 0,
            damage_blocked: 0,
            assist_track: 0,
            assist_radio: 0,
            assist_stun: 0,
            kills: 1,
            spotted: 1,
            capture_points: 0,
            dropped_capture_points: 0,
        }
    }

    fn zero_stats() -> ResultStats {
        ResultStats {
            shots: 0,
            direct_hits: 0,
            piercings: 0,
            damage: 0,
            damage_received: 0,
            damage_blocked: 0,
            assist_track: 0,
            assist_radio: 0,
            assist_stun: 0,
            kills: 0,
            spotted: 0,
            capture_points: 0,
            dropped_capture_points: 0,
        }
    }

    fn receipt() -> BattleReceipt {
        let personal = ResultActorRef {
            kind: ResultActorKind::Player,
            id: 100,
        };
        let enemy = ResultActorRef {
            kind: ResultActorKind::Bot,
            id: 16,
        };
        BattleReceipt {
            receipt_id: "room:7:100".to_owned(),
            arena_unique_id: 123_456,
            round_id: 7,
            player_id: 100,
            account_key: "account-100".to_owned(),
            player_name: "Host".to_owned(),
            vehicle: "ussr:R11_MS-1".to_owned(),
            team: 1,
            winner: 1,
            map: "spaces/01_karelia".to_owned(),
            finish_reason: 1,
            death_reason: -1,
            duration: 30,
            premature_leave: false,
            stats: stats(),
            rewards: ResultRewards {
                credits: 1_000,
                xp: 100,
                free_xp: 5,
                repair_cost: 0,
                ammo_cost: 0,
            },
            public_results: vec![
                PublicResultRow {
                    actor: personal,
                    name: "Host".to_owned(),
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    team: 1,
                    health: 100,
                    death_reason: -1,
                    killer: None,
                    is_team_killer: false,
                    xp: 100,
                    stats: stats(),
                },
                PublicResultRow {
                    actor: enemy,
                    name: "Bot-16".to_owned(),
                    vehicle: "germany:G04_PzVI_Tiger_I".to_owned(),
                    team: 2,
                    health: 0,
                    death_reason: 0,
                    killer: Some(personal),
                    is_team_killer: false,
                    xp: 20,
                    stats: zero_stats(),
                },
            ],
            interactions: vec![ResultInteraction {
                target: enemy,
                spotted: 1,
                death_reason: 0,
                direct_hits: 1,
                explosion_hits: 0,
                piercings: 1,
                damage: 100,
                assist_track: 0,
                assist_radio: 0,
                assist_stun: 0,
                crits: 0,
                fire: 0,
                stun_num: 0,
                stun_duration: 0,
                damage_blocked: 0,
                damage_received: 0,
                ricochets_received: 0,
                no_damage_direct_hits_received: 0,
                target_kills: 1,
            }],
        }
    }

    #[test]
    fn receipt_matches_strict_client_result_contract() {
        let message = encode_battle_receipt(&receipt()).unwrap();
        assert_eq!(message.kind(), "battle_receipt");
        assert_eq!(message.protocol(), Some(5));
        assert_eq!(message.get("repair_cost"), None);
        assert_eq!(
            message.get("rewards").unwrap().get("repair_cost"),
            Some(&json!(0))
        );
        let interaction = &message.get("interactions").unwrap().as_array().unwrap()[0];
        let expected_keys: BTreeSet<_> = [
            "target_kind",
            "target_id",
            "spotted",
            "death_reason",
            "direct_hits",
            "explosion_hits",
            "piercings",
            "damage",
            "assist_track",
            "assist_radio",
            "assist_stun",
            "crits",
            "fire",
            "stun_num",
            "stun_duration",
            "damage_blocked",
            "damage_received",
            "ricochets_received",
            "no_damage_direct_hits_received",
            "target_kills",
        ]
        .into_iter()
        .collect();
        assert_eq!(
            interaction
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>(),
            expected_keys
        );
    }

    #[test]
    fn receipt_fails_closed_for_personal_mismatch_or_friendly_interaction() {
        let mut invalid = receipt();
        invalid.public_results[0].xp += 1;
        assert!(encode_battle_receipt(&invalid).is_err());

        let mut invalid = receipt();
        invalid.interactions[0].target = ResultActorRef {
            kind: ResultActorKind::Player,
            id: 100,
        };
        assert!(encode_battle_receipt(&invalid).is_err());

        let mut invalid = receipt();
        invalid.rewards.ammo_cost = 1;
        assert!(encode_battle_receipt(&invalid).is_err());
    }

    #[test]
    fn ordinary_snapshot_cadence_is_fifteen_hertz() {
        assert!(ordinary_snapshot_due(0));
        assert!(!ordinary_snapshot_due(1));
        assert!(ordinary_snapshot_due(2));
        assert_eq!(SIMULATION_HZ / SNAPSHOT_TICK_DIVISOR, SNAPSHOT_HZ);
    }
}
