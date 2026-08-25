//! Durable, insertion-ordered storage for unacknowledged battle receipts.
//!
//! The on-disk schema deliberately remains compatible with the Python LAN
//! server. Each row is the protocol-v5 battle_receipt object sent on the
//! wire, rather than a Rust-private wrapper. A store validates and freezes a
//! complete candidate ledger before writing it, then installs that candidate
//! in memory only after the atomic replacement succeeds.

use std::collections::{HashMap, HashSet, VecDeque};
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use thiserror::Error;

use crate::room::{BattleResult, BattleWinner, ResultReceipt, Team, MAX_RESULT_RECEIPTS};

pub const RESULT_RECEIPT_STATE_SCHEMA: u64 = 1;
pub const RECEIPT_PROTOCOL_VERSION: u64 = 5;
pub const MAX_PERSISTED_RECEIPTS: usize = MAX_RESULT_RECEIPTS;

const MAX_RECEIPT_LINE_BYTES: usize = 256 * 1024;
const MAX_STATE_FILE_BYTES: u64 =
    (MAX_RECEIPT_LINE_BYTES as u64 * MAX_PERSISTED_RECEIPTS as u64) + 64 * 1024;
const UNIQUE_PATH_ATTEMPTS: usize = 16;

static PATH_NONCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Error)]
pub enum ReceiptStoreError {
    #[error("{operation} {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("invalid receipt state: {0}")]
    InvalidState(String),
    #[error("invalid battle receipt: {0}")]
    InvalidReceipt(String),
    #[error("duplicate receipt id {0:?}")]
    DuplicateReceipt(String),
    #[error("receipt id {0:?} already identifies different contents")]
    ConflictingReceipt(String),
    #[error("receipt id {receipt_id:?} belongs to another account")]
    ReceiptNotOwned { receipt_id: String },
    #[error("receipt state changed on disk while the server was running: {0}")]
    ConcurrentModification(PathBuf),
}

#[derive(Clone, Debug, PartialEq)]
pub enum AppendOutcome {
    Stored {
        receipt: ResultReceipt,
        evicted: Option<ResultReceipt>,
    },
    AlreadyPresent,
}

#[derive(Clone, Debug, PartialEq)]
pub enum AckOutcome {
    Acknowledged(ResultReceipt),
    /// The exact syntactically valid receipt ID is already absent. Treating a
    /// retransmitted ACK as success makes loss after a durable removal benign.
    AlreadyAcknowledged,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReplaceOutcome {
    pub retained: usize,
    pub evicted: Vec<ResultReceipt>,
}

#[derive(Clone, Debug)]
enum ExpectedDisk {
    Missing,
    Exact(Vec<u8>),
}

#[derive(Clone, Debug)]
struct FrozenLedger {
    receipts: VecDeque<ResultReceipt>,
    encoded: Vec<u8>,
}

/// A durable ledger whose order matches RoomState::receipt_ledger().
#[derive(Debug)]
pub struct ReceiptStore {
    path: PathBuf,
    receipts: VecDeque<ResultReceipt>,
    expected_disk: ExpectedDisk,
}

impl ReceiptStore {
    /// Load an existing schema-1 ledger, or create an empty in-memory ledger
    /// when the target does not exist. Invalid existing files are returned as
    /// errors and are never replaced with an empty state.
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, ReceiptStoreError> {
        let path = path.into();
        validate_state_path(&path)?;
        match read_existing_file(&path)? {
            None => Ok(Self {
                path,
                receipts: VecDeque::new(),
                expected_disk: ExpectedDisk::Missing,
            }),
            Some(bytes) => {
                let receipts = decode_state(&bytes)?;
                Ok(Self {
                    path,
                    receipts,
                    expected_disk: ExpectedDisk::Exact(bytes),
                })
            }
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn len(&self) -> usize {
        self.receipts.len()
    }

    pub fn is_empty(&self) -> bool {
        self.receipts.is_empty()
    }

    /// Iterate in global insertion order. Cloning this iterator produces the
    /// exact type accepted by RoomState::with_receipts.
    pub fn room_receipts(&self) -> impl ExactSizeIterator<Item = &ResultReceipt> {
        self.receipts.iter()
    }

    pub fn into_room_receipts(self) -> Vec<ResultReceipt> {
        self.receipts.into_iter().collect()
    }

    /// Return this account's receipts oldest first without changing delivery
    /// state. Delivery-once-per-connection remains a RoomState concern.
    pub fn receipts_for_account(
        &self,
        account_key: &str,
    ) -> Result<Vec<&ResultReceipt>, ReceiptStoreError> {
        validate_account_key(account_key)?;
        Ok(self
            .receipts
            .iter()
            .filter(|receipt| receipt.account_key == account_key)
            .collect())
    }

    pub fn oldest_for_account(
        &self,
        account_key: &str,
    ) -> Result<Option<&ResultReceipt>, ReceiptStoreError> {
        validate_account_key(account_key)?;
        Ok(self
            .receipts
            .iter()
            .find(|receipt| receipt.account_key == account_key))
    }

    /// Replace the durable ledger from a RoomState iterator. Inputs are
    /// cloned, normalized, validated, deduplicated, and capacity-trimmed before
    /// any I/O. The current ledger remains untouched if persistence fails.
    pub fn replace_from_room<'a>(
        &mut self,
        receipts: impl IntoIterator<Item = &'a ResultReceipt>,
    ) -> Result<ReplaceOutcome, ReceiptStoreError> {
        let mut normalized = VecDeque::new();
        let mut ids = HashSet::new();
        for receipt in receipts {
            let receipt = normalize_room_receipt(receipt)?;
            if !ids.insert(receipt.receipt_id.clone()) {
                return Err(ReceiptStoreError::DuplicateReceipt(receipt.receipt_id));
            }
            normalized.push_back(receipt);
        }

        let mut evicted = Vec::new();
        while normalized.len() > MAX_PERSISTED_RECEIPTS {
            if let Some(receipt) = normalized.pop_front() {
                evicted.push(receipt);
            }
        }
        let frozen = freeze_normalized(normalized)?;
        let retained = frozen.receipts.len();
        self.commit(frozen)?;
        Ok(ReplaceOutcome { retained, evicted })
    }

    /// Append one newly frozen receipt. An exact duplicate is idempotent; a
    /// duplicate ID with different contents is rejected.
    pub fn append(&mut self, receipt: ResultReceipt) -> Result<AppendOutcome, ReceiptStoreError> {
        let receipt = normalize_room_receipt(&receipt)?;
        if let Some(existing) = self
            .receipts
            .iter()
            .find(|existing| existing.receipt_id == receipt.receipt_id)
        {
            return if existing == &receipt {
                Ok(AppendOutcome::AlreadyPresent)
            } else {
                Err(ReceiptStoreError::ConflictingReceipt(receipt.receipt_id))
            };
        }

        let mut candidate = self.receipts.clone();
        candidate.push_back(receipt.clone());
        let evicted = if candidate.len() > MAX_PERSISTED_RECEIPTS {
            candidate.pop_front()
        } else {
            None
        };
        let frozen = freeze_normalized(candidate)?;
        self.commit(frozen)?;
        Ok(AppendOutcome::Stored { receipt, evicted })
    }

    /// Persist an ACK removal before changing memory. Repeating the same ACK
    /// after success returns AlreadyAcknowledged. An existing receipt owned
    /// by another account is never treated as an idempotent success.
    pub fn acknowledge(
        &mut self,
        account_key: &str,
        receipt_id: &str,
    ) -> Result<AckOutcome, ReceiptStoreError> {
        validate_account_key(account_key)?;
        validate_receipt_id(receipt_id)?;
        let Some(index) = self
            .receipts
            .iter()
            .position(|receipt| receipt.receipt_id == receipt_id)
        else {
            return Ok(AckOutcome::AlreadyAcknowledged);
        };
        if self.receipts[index].account_key != account_key {
            return Err(ReceiptStoreError::ReceiptNotOwned {
                receipt_id: receipt_id.to_owned(),
            });
        }

        let mut candidate = self.receipts.clone();
        let removed = candidate
            .remove(index)
            .expect("the located receipt index remains present in the clone");
        let frozen = freeze_normalized(candidate)?;
        self.commit(frozen)?;
        Ok(AckOutcome::Acknowledged(removed))
    }

    fn commit(&mut self, frozen: FrozenLedger) -> Result<(), ReceiptStoreError> {
        self.ensure_disk_unchanged()?;
        atomic_write(&self.path, &frozen.encoded)?;
        self.expected_disk = ExpectedDisk::Exact(frozen.encoded);
        self.receipts = frozen.receipts;
        Ok(())
    }

    fn ensure_disk_unchanged(&self) -> Result<(), ReceiptStoreError> {
        let current = read_existing_file(&self.path)?;
        let unchanged = match (&self.expected_disk, current) {
            (ExpectedDisk::Missing, None) => true,
            (ExpectedDisk::Exact(expected), Some(current)) => expected == &current,
            _ => false,
        };
        if unchanged {
            Ok(())
        } else {
            Err(ReceiptStoreError::ConcurrentModification(self.path.clone()))
        }
    }
}

fn validate_state_path(path: &Path) -> Result<(), ReceiptStoreError> {
    if path.file_name().is_none() {
        return Err(ReceiptStoreError::InvalidState(
            "state path must name one file".to_owned(),
        ));
    }
    Ok(())
}

fn read_existing_file(path: &Path) -> Result<Option<Vec<u8>>, ReceiptStoreError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(io_error("inspect", path, error)),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ReceiptStoreError::InvalidState(format!(
            "{} is not a regular state file",
            path.display()
        )));
    }
    if metadata.len() > MAX_STATE_FILE_BYTES {
        return Err(ReceiptStoreError::InvalidState(format!(
            "state file exceeds {MAX_STATE_FILE_BYTES} bytes"
        )));
    }

    let file = File::open(path).map_err(|error| io_error("open", path, error))?;
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(MAX_STATE_FILE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("read", path, error))?;
    if bytes.len() as u64 > MAX_STATE_FILE_BYTES {
        return Err(ReceiptStoreError::InvalidState(format!(
            "state file exceeds {MAX_STATE_FILE_BYTES} bytes"
        )));
    }
    Ok(Some(bytes))
}

fn decode_state(bytes: &[u8]) -> Result<VecDeque<ResultReceipt>, ReceiptStoreError> {
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|error| ReceiptStoreError::InvalidState(error.to_string()))?;
    let object = value.as_object().ok_or_else(|| {
        ReceiptStoreError::InvalidState("state root must be an object".to_owned())
    })?;
    if object.get("schema").and_then(Value::as_u64) != Some(RESULT_RECEIPT_STATE_SCHEMA) {
        return Err(ReceiptStoreError::InvalidState(format!(
            "schema must be {RESULT_RECEIPT_STATE_SCHEMA}"
        )));
    }
    let rows = object
        .get("receipts")
        .and_then(Value::as_array)
        .ok_or_else(|| ReceiptStoreError::InvalidState("receipts must be an array".to_owned()))?;
    if rows.len() > MAX_PERSISTED_RECEIPTS {
        return Err(ReceiptStoreError::InvalidState(format!(
            "receipt count exceeds {MAX_PERSISTED_RECEIPTS}"
        )));
    }

    let mut receipts = VecDeque::with_capacity(rows.len());
    let mut ids = HashSet::new();
    for row in rows {
        let receipt = decode_wire_receipt(row)?;
        if !ids.insert(receipt.receipt_id.clone()) {
            return Err(ReceiptStoreError::DuplicateReceipt(receipt.receipt_id));
        }
        receipts.push_back(receipt);
    }
    Ok(receipts)
}

fn normalize_room_receipt(receipt: &ResultReceipt) -> Result<ResultReceipt, ReceiptStoreError> {
    validate_receipt_id(&receipt.receipt_id)?;
    if receipt.round_id == 0 {
        return Err(invalid_receipt("round_id must be positive"));
    }
    if receipt.player_id == 0 {
        return Err(invalid_receipt("player_id must be positive"));
    }
    validate_account_key(&receipt.account_key)?;

    let decoded = decode_wire_receipt(&receipt.payload)?;
    if decoded.receipt_id != receipt.receipt_id
        || decoded.round_id != receipt.round_id
        || decoded.player_id != receipt.player_id
        || decoded.account_key != receipt.account_key
    {
        return Err(invalid_receipt(
            "wire identity does not match the room receipt",
        ));
    }
    if decoded.result.winner != receipt.result.winner {
        return Err(invalid_receipt(
            "wire winner does not match the room result",
        ));
    }
    Ok(decoded)
}

fn freeze_normalized(receipts: VecDeque<ResultReceipt>) -> Result<FrozenLedger, ReceiptStoreError> {
    if receipts.len() > MAX_PERSISTED_RECEIPTS {
        return Err(invalid_receipt("receipt ledger exceeds capacity"));
    }
    let mut ids = HashSet::new();
    let mut rows = Vec::with_capacity(receipts.len());
    for receipt in &receipts {
        if !ids.insert(receipt.receipt_id.clone()) {
            return Err(ReceiptStoreError::DuplicateReceipt(
                receipt.receipt_id.clone(),
            ));
        }
        let normalized = normalize_room_receipt(receipt)?;
        rows.push(normalized.payload);
    }
    let mut encoded = serde_json::to_vec(&json!({
        "schema": RESULT_RECEIPT_STATE_SCHEMA,
        "receipts": rows,
    }))
    .map_err(|error| invalid_receipt(error.to_string()))?;
    encoded.push(b'\n');
    if encoded.len() as u64 > MAX_STATE_FILE_BYTES {
        return Err(invalid_receipt("encoded state exceeds the file limit"));
    }
    Ok(FrozenLedger { receipts, encoded })
}

fn decode_wire_receipt(value: &Value) -> Result<ResultReceipt, ReceiptStoreError> {
    let mut payload = value
        .as_object()
        .cloned()
        .ok_or_else(|| invalid_receipt("receipt must be an object"))?;
    if required_text(&payload, "type", 64)? != "battle_receipt"
        || exact_u64(
            &payload,
            "protocol",
            RECEIPT_PROTOCOL_VERSION,
            RECEIPT_PROTOCOL_VERSION,
        )? != RECEIPT_PROTOCOL_VERSION
    {
        return Err(invalid_receipt("invalid receipt envelope"));
    }

    let receipt_id = required_text(&payload, "receipt_id", 96)?.to_owned();
    validate_receipt_id(&receipt_id)?;
    let account_key = required_text(&payload, "account_key", 64)?.to_owned();
    validate_account_key(&account_key)?;
    let player_name = required_text(&payload, "player_name", 32)?.to_owned();
    let vehicle = required_text(&payload, "vehicle", 96)?.to_owned();
    required_text(&payload, "map", 96)?;

    exact_u64(&payload, "arena_unique_id", 0, u64::MAX)?;
    let round_id = exact_u64(&payload, "round_id", 1, u64::MAX)?;
    let player_id_value = exact_u64(&payload, "player_id", 1, u32::MAX as u64)?;
    let team = exact_u64(&payload, "team", 1, 2)? as u8;
    let winner = exact_u64(&payload, "winner", 0, 2)? as u8;
    let finish_reason = exact_u64(&payload, "finish_reason", 0, 255)? as u8;
    let death_reason = exact_i64(&payload, "death_reason", -1, 255)?;
    exact_u64(&payload, "duration", 0, u64::MAX)?;
    if !matches!(payload.get("premature_leave"), Some(Value::Bool(_))) {
        return Err(invalid_receipt("premature_leave must be boolean"));
    }

    let stats = required_object(&payload, "stats")?;
    validate_nonnegative_fields(stats, STAT_FIELDS, "receipt statistic")?;
    let rewards = required_object(&payload, "rewards")?;
    validate_nonnegative_fields(rewards, REWARD_FIELDS, "receipt reward")?;
    if exact_u64(rewards, "repair_cost", 0, u64::MAX)? != 0
        || exact_u64(rewards, "ammo_cost", 0, u64::MAX)? != 0
    {
        return Err(invalid_receipt("offline service costs must be zero"));
    }

    let public_rows = payload
        .get("public_results")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid_receipt("public_results must be an array"))?;
    if !(1..=30).contains(&public_rows.len()) {
        return Err(invalid_receipt(
            "public_results must contain between 1 and 30 rows",
        ));
    }
    let public_row_count = public_rows.len();

    let personal_identity = ("player".to_owned(), player_id_value);
    let mut teams: HashMap<(String, u64), u8> = HashMap::new();
    let mut personal: Option<&Map<String, Value>> = None;
    for row in public_rows {
        let row = row
            .as_object()
            .ok_or_else(|| invalid_receipt("public result row must be an object"))?;
        let actor_kind = enum_text(row, "actor_kind", &["player", "bot"])?;
        let actor_id = exact_u64(row, "actor_id", 1, u32::MAX as u64)?;
        let identity = (actor_kind.to_owned(), actor_id);
        if teams.contains_key(&identity) {
            return Err(invalid_receipt("duplicate public result identity"));
        }
        required_text(row, "name", 32)?;
        required_text(row, "vehicle", 96)?;
        let row_team = exact_u64(row, "team", 1, 2)? as u8;
        exact_u64(row, "health", 0, u64::MAX)?;
        exact_i64(row, "death_reason", -1, 255)?;
        exact_u64(row, "xp", 0, u64::MAX)?;
        if !matches!(row.get("is_team_killer"), Some(Value::Bool(_))) {
            return Err(invalid_receipt("is_team_killer must be boolean"));
        }
        let row_stats = required_object(row, "stats")?;
        validate_nonnegative_fields(row_stats, STAT_FIELDS, "public result statistic")?;

        let killer_kind = optional_enum_text(row, "killer_kind", "", &["", "player", "bot"])?;
        let killer_id = optional_exact_u64(row, "killer_id", 0, 0, u32::MAX as u64)?;
        if killer_kind.is_empty() != (killer_id == 0) {
            return Err(invalid_receipt("invalid public result killer"));
        }

        teams.insert(identity.clone(), row_team);
        if identity == personal_identity {
            personal = Some(row);
        }
    }

    let personal = personal.ok_or_else(|| invalid_receipt("personal public result is missing"))?;
    if required_text(personal, "name", 32)? != player_name
        || required_text(personal, "vehicle", 96)? != vehicle
        || exact_u64(personal, "team", 1, 2)? as u8 != team
        || exact_i64(personal, "death_reason", -1, 255)? != death_reason
        || exact_u64(personal, "xp", 0, u64::MAX)? != exact_u64(rewards, "xp", 0, u64::MAX)?
        || personal.get("stats") != payload.get("stats")
    {
        return Err(invalid_receipt("inconsistent personal public result"));
    }

    let interactions = payload
        .entry("interactions".to_owned())
        .or_insert_with(|| Value::Array(Vec::new()))
        .as_array()
        .ok_or_else(|| invalid_receipt("interactions must be an array"))?;
    if interactions.len() > public_row_count {
        return Err(invalid_receipt("too many interaction rows"));
    }
    let mut interaction_targets = HashSet::new();
    for interaction in interactions {
        let interaction = interaction
            .as_object()
            .ok_or_else(|| invalid_receipt("interaction row must be an object"))?;
        if interaction.len() != INTERACTION_FIELDS.len() + 2
            || !interaction.contains_key("target_kind")
            || !interaction.contains_key("target_id")
            || !INTERACTION_FIELDS
                .iter()
                .all(|(field, _, _)| interaction.contains_key(*field))
        {
            return Err(invalid_receipt("interaction row has invalid fields"));
        }
        let target_kind = enum_text(interaction, "target_kind", &["player", "bot"])?;
        let target_id = exact_u64(interaction, "target_id", 1, u32::MAX as u64)?;
        let target = (target_kind.to_owned(), target_id);
        let Some(target_team) = teams.get(&target) else {
            return Err(invalid_receipt("interaction target is absent"));
        };
        if target == personal_identity
            || *target_team == team
            || !interaction_targets.insert(target)
        {
            return Err(invalid_receipt("interaction target is invalid"));
        }
        for (field, minimum, maximum) in INTERACTION_FIELDS {
            exact_i64(interaction, field, *minimum, *maximum)?;
        }
    }

    let payload = Value::Object(payload);
    let mut line =
        serde_json::to_vec(&payload).map_err(|error| invalid_receipt(error.to_string()))?;
    line.push(b'\n');
    if line.len() > MAX_RECEIPT_LINE_BYTES {
        return Err(invalid_receipt("receipt exceeds the wire line limit"));
    }

    let winner = match winner {
        0 => BattleWinner::Draw,
        1 => BattleWinner::Team(Team::One),
        2 => BattleWinner::Team(Team::Two),
        _ => unreachable!("winner was bounded above"),
    };
    let reason = match finish_reason {
        1 => "elimination",
        2 => "base captured",
        3 => "battle_timeout",
        4 => "all_players_left",
        _ => "finished",
    };
    Ok(ResultReceipt {
        receipt_id,
        round_id,
        player_id: player_id_value as u32,
        account_key,
        result: BattleResult {
            winner,
            reason: reason.to_owned(),
            base_team: None,
        },
        payload,
    })
}

const STAT_FIELDS: &[&str] = &[
    "shots",
    "direct_hits",
    "piercings",
    "damage",
    "damage_received",
    "damage_blocked",
    "assist_track",
    "assist_radio",
    "assist_stun",
    "kills",
    "spotted",
    "capture_points",
    "dropped_capture_points",
];

const REWARD_FIELDS: &[&str] = &["credits", "xp", "free_xp", "repair_cost", "ammo_cost"];

const INTERACTION_FIELDS: &[(&str, i64, i64)] = &[
    ("spotted", 0, 1),
    ("death_reason", -1, 10),
    ("direct_hits", 0, 65_535),
    ("explosion_hits", 0, 65_535),
    ("piercings", 0, 65_535),
    ("damage", 0, 65_535),
    ("assist_track", 0, 65_535),
    ("assist_radio", 0, 65_535),
    ("assist_stun", 0, 65_535),
    ("crits", 0, 4_294_967_295),
    ("fire", 0, 65_535),
    ("stun_num", 0, 65_535),
    ("stun_duration", 0, 65_535),
    ("damage_blocked", 0, 4_294_967_295),
    ("damage_received", 0, 65_535),
    ("ricochets_received", 0, 65_535),
    ("no_damage_direct_hits_received", 0, 65_535),
    ("target_kills", 0, 255),
];

fn required_object<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a Map<String, Value>, ReceiptStoreError> {
    object
        .get(field)
        .and_then(Value::as_object)
        .ok_or_else(|| invalid_receipt(format!("{field} must be an object")))
}

fn required_text<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    maximum_chars: usize,
) -> Result<&'a str, ReceiptStoreError> {
    let value = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid_receipt(format!("{field} must be a string")))?;
    let length = value.chars().count();
    if length == 0 || length > maximum_chars {
        return Err(invalid_receipt(format!(
            "{field} must contain 1 to {maximum_chars} characters"
        )));
    }
    Ok(value)
}

fn enum_text<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    accepted: &[&str],
) -> Result<&'a str, ReceiptStoreError> {
    let value = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid_receipt(format!("{field} must be a string")))?;
    if accepted.contains(&value) {
        Ok(value)
    } else {
        Err(invalid_receipt(format!("{field} has an invalid value")))
    }
}

fn optional_enum_text<'a>(
    object: &'a Map<String, Value>,
    field: &str,
    default: &'a str,
    accepted: &[&str],
) -> Result<&'a str, ReceiptStoreError> {
    match object.get(field) {
        None => Ok(default),
        Some(Value::String(value)) if accepted.contains(&value.as_str()) => Ok(value),
        _ => Err(invalid_receipt(format!("{field} has an invalid value"))),
    }
}

fn exact_u64(
    object: &Map<String, Value>,
    field: &str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ReceiptStoreError> {
    let value = object
        .get(field)
        .and_then(Value::as_u64)
        .ok_or_else(|| invalid_receipt(format!("{field} must be an unsigned integer")))?;
    if value < minimum || value > maximum {
        return Err(invalid_receipt(format!("{field} is out of range")));
    }
    Ok(value)
}

fn optional_exact_u64(
    object: &Map<String, Value>,
    field: &str,
    default: u64,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ReceiptStoreError> {
    match object.get(field) {
        None => Ok(default),
        Some(_) => exact_u64(object, field, minimum, maximum),
    }
}

fn exact_i64(
    object: &Map<String, Value>,
    field: &str,
    minimum: i64,
    maximum: i64,
) -> Result<i64, ReceiptStoreError> {
    let value = object
        .get(field)
        .and_then(Value::as_i64)
        .ok_or_else(|| invalid_receipt(format!("{field} must be an integer")))?;
    if value < minimum || value > maximum {
        return Err(invalid_receipt(format!("{field} is out of range")));
    }
    Ok(value)
}

fn validate_nonnegative_fields(
    object: &Map<String, Value>,
    fields: &[&str],
    description: &str,
) -> Result<(), ReceiptStoreError> {
    for field in fields {
        exact_u64(object, field, 0, u64::MAX)
            .map_err(|_| invalid_receipt(format!("invalid {description} {field}")))?;
    }
    Ok(())
}

fn validate_account_key(account_key: &str) -> Result<(), ReceiptStoreError> {
    if account_key.is_empty()
        || account_key.len() > 64
        || !account_key
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(invalid_receipt("invalid account_key"));
    }
    Ok(())
}

fn validate_receipt_id(receipt_id: &str) -> Result<(), ReceiptStoreError> {
    if receipt_id.is_empty()
        || receipt_id.len() > 96
        || !receipt_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b':'))
    {
        return Err(invalid_receipt("invalid receipt_id"));
    }
    Ok(())
}

fn invalid_receipt(message: impl Into<String>) -> ReceiptStoreError {
    ReceiptStoreError::InvalidReceipt(message.into())
}

fn io_error(operation: &'static str, path: &Path, source: io::Error) -> ReceiptStoreError {
    ReceiptStoreError::Io {
        operation,
        path: path.to_path_buf(),
        source,
    }
}

fn state_parent(path: &Path) -> &Path {
    path.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), ReceiptStoreError> {
    let parent = state_parent(path);
    fs::create_dir_all(parent).map_err(|error| io_error("create directory for", path, error))?;

    let (temporary, mut file) = create_unique_file(path, "tmp")?;
    let mut cleanup = RemoveOnDrop::new(temporary.clone());
    file.write_all(bytes)
        .map_err(|error| io_error("write temporary state for", path, error))?;
    file.flush()
        .map_err(|error| io_error("flush temporary state for", path, error))?;
    file.sync_all()
        .map_err(|error| io_error("sync temporary state for", path, error))?;
    drop(file);

    replace_file(&temporary, path).map_err(|error| io_error("replace", path, error))?;
    cleanup.disarm();
    sync_parent_best_effort(parent);
    Ok(())
}

fn create_unique_file(target: &Path, label: &str) -> Result<(PathBuf, File), ReceiptStoreError> {
    for _ in 0..UNIQUE_PATH_ATTEMPTS {
        let candidate = unique_sibling_path(target, label);
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => return Ok((candidate, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(io_error("create temporary state for", target, error)),
        }
    }
    Err(io_error(
        "create temporary state for",
        target,
        io::Error::new(
            io::ErrorKind::AlreadyExists,
            "temporary file name attempts exhausted",
        ),
    ))
}

fn unique_sibling_path(target: &Path, label: &str) -> PathBuf {
    let nonce = PATH_NONCE.fetch_add(1, Ordering::Relaxed);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut name = OsString::from(".");
    name.push(
        target
            .file_name()
            .expect("validated state paths always contain a file name"),
    );
    name.push(format!(".{label}-{}-{now}-{nonce}", std::process::id()));
    state_parent(target).join(name)
}

#[cfg(not(windows))]
fn replace_file(temporary: &Path, target: &Path) -> io::Result<()> {
    fs::rename(temporary, target)
}

/// std::fs::rename does not replace an existing Windows file. Keep the
/// fallback bounded and touch only uniquely named siblings derived from the
/// exact target. If installing the temporary file fails, restore the previous
/// target before returning whenever the OS permits it.
#[cfg(windows)]
fn replace_file(temporary: &Path, target: &Path) -> io::Result<()> {
    match fs::rename(temporary, target) {
        Ok(()) => return Ok(()),
        Err(error) if !target.exists() => return Err(error),
        Err(_) => {}
    }

    let metadata = fs::symlink_metadata(target)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "receipt state target is not a regular file",
        ));
    }
    let backup = (0..UNIQUE_PATH_ATTEMPTS)
        .map(|_| unique_sibling_path(target, "replace-backup"))
        .find(|candidate| !candidate.exists())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AlreadyExists,
                "replacement backup name attempts exhausted",
            )
        })?;
    fs::rename(target, &backup)?;
    match fs::rename(temporary, target) {
        Ok(()) => {
            // This path contains only the prior target moved by this call.
            // Failure to clean it is not a failed commit and must not make
            // memory diverge from the already-installed durable state.
            let _ = fs::remove_file(&backup);
            Ok(())
        }
        Err(install_error) => match fs::rename(&backup, target) {
            Ok(()) => Err(install_error),
            Err(restore_error) => Err(io::Error::new(
                install_error.kind(),
                format!(
                    "install failed ({install_error}); previous state remains at {} because restore failed ({restore_error})",
                    backup.display()
                ),
            )),
        },
    }
}

#[cfg(unix)]
fn sync_parent_best_effort(parent: &Path) {
    if let Ok(directory) = File::open(parent) {
        let _ = directory.sync_all();
    }
}

#[cfg(not(unix))]
fn sync_parent_best_effort(_parent: &Path) {}

struct RemoveOnDrop {
    path: PathBuf,
    armed: bool,
}

impl RemoveOnDrop {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for RemoveOnDrop {
    fn drop(&mut self) {
        if self.armed {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::room::{RoomConfig, RoomState};

    fn stats(damage: u64) -> Value {
        json!({
            "shots": 1,
            "direct_hits": 1,
            "piercings": 1,
            "damage": damage,
            "damage_received": 0,
            "damage_blocked": 0,
            "assist_track": 0,
            "assist_radio": 0,
            "assist_stun": 0,
            "kills": 0,
            "spotted": 1,
            "capture_points": 0,
            "dropped_capture_points": 0
        })
    }

    fn receipt(round_id: u64, account_key: &str, damage: u64) -> ResultReceipt {
        let receipt_id = format!("namespace:{round_id}:1");
        let personal_stats = stats(damage);
        let payload = json!({
            "type": "battle_receipt",
            "protocol": 5,
            "receipt_id": receipt_id,
            "arena_unique_id": 4_294_967_296_u64 + round_id,
            "round_id": round_id,
            "player_id": 1,
            "account_key": account_key,
            "player_name": "Player",
            "vehicle": "ussr:R11_MS-1",
            "team": 1,
            "winner": 1,
            "map": "01_karelia",
            "finish_reason": 1,
            "death_reason": -1,
            "duration": 90,
            "premature_leave": false,
            "stats": personal_stats,
            "rewards": {
                "credits": 1000,
                "xp": 100,
                "free_xp": 5,
                "repair_cost": 0,
                "ammo_cost": 0
            },
            "public_results": [
                {
                    "actor_kind": "player",
                    "actor_id": 1,
                    "name": "Player",
                    "vehicle": "ussr:R11_MS-1",
                    "team": 1,
                    "health": 100,
                    "death_reason": -1,
                    "killer_kind": "",
                    "killer_id": 0,
                    "is_team_killer": false,
                    "xp": 100,
                    "stats": stats(damage)
                },
                {
                    "actor_kind": "bot",
                    "actor_id": 1,
                    "name": "Enemy",
                    "vehicle": "germany:G12_Ltraktor",
                    "team": 2,
                    "health": 0,
                    "death_reason": 0,
                    "killer_kind": "player",
                    "killer_id": 1,
                    "is_team_killer": false,
                    "xp": 0,
                    "stats": stats(0)
                }
            ],
            "interactions": [{
                "target_kind": "bot",
                "target_id": 1,
                "spotted": 1,
                "death_reason": 0,
                "direct_hits": 1,
                "explosion_hits": 0,
                "piercings": 1,
                "damage": damage,
                "assist_track": 0,
                "assist_radio": 0,
                "assist_stun": 0,
                "crits": 0,
                "fire": 0,
                "stun_num": 0,
                "stun_duration": 0,
                "damage_blocked": 0,
                "damage_received": 0,
                "ricochets_received": 0,
                "no_damage_direct_hits_received": 0,
                "target_kills": 0
            }]
        });
        ResultReceipt {
            receipt_id: format!("namespace:{round_id}:1"),
            round_id,
            player_id: 1,
            account_key: account_key.to_owned(),
            result: BattleResult {
                winner: BattleWinner::Team(Team::One),
                reason: "elimination".to_owned(),
                base_team: None,
            },
            payload,
        }
    }

    #[test]
    fn round_trip_preserves_order_and_feeds_room_state() {
        let directory = TestDirectory::new("roundtrip");
        let path = directory.path().join("receipts.json");
        let first = receipt(1, "account_a", 10);
        let second = receipt(2, "account_a", 20);
        let third = receipt(3, "account_b", 30);

        let mut store = ReceiptStore::open(&path).unwrap();
        store.append(first.clone()).unwrap();
        store.append(second.clone()).unwrap();
        store.append(third.clone()).unwrap();
        drop(store);

        let reopened = ReceiptStore::open(&path).unwrap();
        assert_eq!(
            reopened.room_receipts().cloned().collect::<Vec<_>>(),
            vec![first.clone(), second, third]
        );
        assert_eq!(
            reopened.oldest_for_account("account_a").unwrap(),
            Some(&first)
        );
        let config = RoomConfig::new(30, 15, 15, "ussr:R11_MS-1", "new-process").unwrap();
        let room = RoomState::with_receipts(config, reopened.into_room_receipts()).unwrap();
        assert_eq!(room.receipt_ledger().count(), 3);
    }

    #[test]
    fn corrupt_state_fails_closed_without_overwrite() {
        let directory = TestDirectory::new("corrupt");
        let path = directory.path().join("receipts.json");
        let corrupt = b"{not valid json\n";
        fs::write(&path, corrupt).unwrap();

        assert!(matches!(
            ReceiptStore::open(&path),
            Err(ReceiptStoreError::InvalidState(_))
        ));
        assert_eq!(fs::read(&path).unwrap(), corrupt);
    }

    #[test]
    fn capacity_evicts_global_oldest_and_keeps_account_order() {
        let directory = TestDirectory::new("capacity");
        let path = directory.path().join("receipts.json");
        let receipts = (1..=257)
            .map(|round| receipt(round, if round % 2 == 0 { "even" } else { "odd" }, round))
            .collect::<Vec<_>>();
        let mut store = ReceiptStore::open(&path).unwrap();
        let outcome = store.replace_from_room(receipts.iter()).unwrap();

        assert_eq!(outcome.retained, 256);
        assert_eq!(outcome.evicted, vec![receipts[0].clone()]);
        assert_eq!(store.room_receipts().next(), Some(&receipts[1]));
        assert_eq!(store.oldest_for_account("odd").unwrap(), Some(&receipts[2]));
        assert_eq!(ReceiptStore::open(&path).unwrap().len(), 256);
    }

    #[test]
    fn acknowledgement_is_owned_durable_and_idempotent() {
        let directory = TestDirectory::new("ack");
        let path = directory.path().join("receipts.json");
        let stored = receipt(1, "owner", 10);
        let mut store = ReceiptStore::open(&path).unwrap();
        store.append(stored.clone()).unwrap();

        assert!(matches!(
            store.acknowledge("someone_else", &stored.receipt_id),
            Err(ReceiptStoreError::ReceiptNotOwned { .. })
        ));
        assert_eq!(store.len(), 1);
        assert_eq!(
            store.acknowledge("owner", &stored.receipt_id).unwrap(),
            AckOutcome::Acknowledged(stored.clone())
        );
        assert_eq!(
            store.acknowledge("owner", &stored.receipt_id).unwrap(),
            AckOutcome::AlreadyAcknowledged
        );
        assert!(ReceiptStore::open(&path).unwrap().is_empty());
    }

    #[test]
    fn failed_write_does_not_change_memory() {
        let directory = TestDirectory::new("write-failure");
        let blocker = directory.path().join("not-a-directory");
        let path = blocker.join("receipts.json");
        let mut store = ReceiptStore::open(&path).unwrap();
        fs::write(&blocker, b"block directory creation").unwrap();

        assert!(store.append(receipt(1, "owner", 10)).is_err());
        assert!(store.is_empty());
    }

    #[test]
    fn restart_reoffers_oldest_until_its_ack_is_durable() {
        let directory = TestDirectory::new("restart-redelivery");
        let path = directory.path().join("receipts.json");
        let first = receipt(1, "owner", 10);
        let second = receipt(2, "owner", 20);
        let mut store = ReceiptStore::open(&path).unwrap();
        store.append(first.clone()).unwrap();
        store.append(second.clone()).unwrap();
        drop(store);

        let mut restarted = ReceiptStore::open(&path).unwrap();
        assert_eq!(restarted.oldest_for_account("owner").unwrap(), Some(&first));
        restarted.acknowledge("owner", &first.receipt_id).unwrap();
        drop(restarted);

        let restarted_again = ReceiptStore::open(&path).unwrap();
        assert_eq!(
            restarted_again.oldest_for_account("owner").unwrap(),
            Some(&second)
        );
    }

    struct TestDirectory {
        path: PathBuf,
    }

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "offline-rust-server-receipt-store-{label}-{}-{}",
                std::process::id(),
                PATH_NONCE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self { path }
        }

        fn path(&self) -> &Path {
            &self.path
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}
