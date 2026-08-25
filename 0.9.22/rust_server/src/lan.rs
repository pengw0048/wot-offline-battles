use crate::descriptor::parse_player_authority_loadout;
use crate::player_fire_clock::EffectiveDispersionFactors;
use crate::room::{JoinRequest, PlayerView, RoomPhase, RoomState, Team};
use crate::vehicle_overlay::VEHICLE_OVERLAY_CAPABILITY;
use crate::wire::{Hello, WireError, WireObject, LAN_PROTOCOL_VERSION};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use thiserror::Error;

pub const CLIENT_BUILD_0922: &str = "wot-0.9.22.0.1-cn-1513";
pub const DEFAULT_VEHICLE_0922: &str = "ussr:R11_MS-1";

pub const PROJECTILE_LEDGER_V1: &str = "projectile_ledger_v1";
pub const PROJECTILE_LEDGER_V2: &str = "projectile_ledger_v2";
pub const DESTRUCTIBLE_CATALOG_V5: &str = "destructible_catalog_v5";
pub const LEAN_SNAPSHOT_MANIFEST_V1: &str = "lean_snapshot_manifest_v1";
pub const RAM_CONTACT_LEDGER_V3: &str = "ram_contact_ledger_v3";
pub const HUMAN_RAM_TIMELINE_V1: &str = "human_ram_timeline_v1";
pub const HE_EXPLOSION_EVIDENCE_V1: &str = "he_explosion_evidence_v1";
pub const PLAYER_FIRE_INTENT_V1: &str = "player_fire_intent_v1";
pub const PLAYER_FIRE_INTENT_V4: &str = "player_fire_intent_v4";
pub const PLAYER_ENVIRONMENT_V2: &str = "player_environment_v2";
pub const EFFECTIVE_PARAMS_V1: &str = "effective_params_v1";
pub const RICOCHET_CONTINUATION_V1: &str = "ricochet_continuation_v1";
pub const PLAYER_AMMO_AUTHORITY_V1: &str = "player_ammo_authority_v1";
pub const PLAYER_AUTHORITY_LOADOUT_V1: &str = "player_authority_loadout_v1";
pub const PROJECTILE_HIT_VEHICLE_V1: &str = "projectile_hit_vehicle_v1";
pub const PROJECTILE_WRECK_HIT_V1: &str = "projectile_wreck_hit_v1";
pub const RANDOM_MAP_V1: &str = "random_map_v1";
pub const TEAM_SELECTION_V1: &str = "team_selection_v1";
pub const TEAM_SIZE_SELECTION_V1: &str = "team_size_selection_v1";
pub const ORACLE_BACKED_SERVER_V1: &str = "oracle_backed_server_v1";
pub const NATIVE_ORACLE_V1: &str = "native_oracle_v1";

/// Capabilities that identify the deployed #1513 JSON schema independently
/// of diagnostic protocol/build labels.
pub const MODERN_CLIENT_REQUIRED_CAPABILITIES: &[&str] = &[
    PROJECTILE_LEDGER_V2,
    DESTRUCTIBLE_CATALOG_V5,
    RAM_CONTACT_LEDGER_V3,
    HUMAN_RAM_TIMELINE_V1,
    PLAYER_FIRE_INTENT_V4,
    PLAYER_ENVIRONMENT_V2,
    EFFECTIVE_PARAMS_V1,
    RICOCHET_CONTINUATION_V1,
];

pub const SERVER_CAPABILITIES: &[&str] = &[
    DESTRUCTIBLE_CATALOG_V5,
    LEAN_SNAPSHOT_MANIFEST_V1,
    RAM_CONTACT_LEDGER_V3,
    HUMAN_RAM_TIMELINE_V1,
    HE_EXPLOSION_EVIDENCE_V1,
    PLAYER_FIRE_INTENT_V4,
    PLAYER_ENVIRONMENT_V2,
    EFFECTIVE_PARAMS_V1,
    RICOCHET_CONTINUATION_V1,
    PLAYER_AMMO_AUTHORITY_V1,
    PLAYER_AUTHORITY_LOADOUT_V1,
    PROJECTILE_HIT_VEHICLE_V1,
    PROJECTILE_WRECK_HIT_V1,
    RANDOM_MAP_V1,
    TEAM_SELECTION_V1,
    TEAM_SIZE_SELECTION_V1,
    ORACLE_BACKED_SERVER_V1,
    NATIVE_ORACLE_V1,
    VEHICLE_OVERLAY_CAPABILITY,
];

pub const MAP_POOL_0922: &[&str] = &[
    "01_karelia",
    "02_malinovka",
    "04_himmelsdorf",
    "05_prohorovka",
    "06_ensk",
    "07_lakeville",
    "08_ruinberg",
    "10_hills",
    "11_murovanka",
    "13_erlenberg",
    "14_siegfried_line",
    "17_munchen",
    "18_cliff",
    "19_monastery",
    "22_slough",
    "23_westfeld",
    "28_desert",
    "29_el_hallouf",
    "31_airfield",
    "33_fjord",
    "34_redshire",
    "35_steppes",
    "36_fishing_bay",
    "37_caucasus",
    "38_mannerheim_line",
    "44_north_america",
    "45_north_america",
    "47_canada_a",
    "59_asia_great_wall",
    "63_tundra",
    "73_asia_korea",
    "83_kharkiv",
    "84_winter",
    "86_himmelsdorf_winter",
    "92_stalingrad",
    "95_lost_city",
    "100_thepit",
    "101_dday",
    "103_ruinberg_winter",
    "112_eiffel_tower_ctf",
    "114_czech",
];

#[derive(Clone, Debug, PartialEq)]
pub struct PlayerHello {
    pub join: JoinRequest,
    pub client_build: String,
    pub capabilities: Vec<String>,
}

#[derive(Debug, Error)]
pub enum LanSchemaError {
    #[error("unsupported client build")]
    UnsupportedClientBuild,
    #[error("invalid or unsupported capability list")]
    UnsupportedCapabilities,
    #[error("invalid account key")]
    InvalidAccountKey,
    #[error("invalid requested team")]
    InvalidTeam,
    #[error("invalid max health")]
    InvalidMaxHealth,
    #[error("invalid vehicle customization payload")]
    InvalidVehicleConfiguration,
    #[error("invalid effective vehicle parameters")]
    InvalidEffectiveParams,
    #[error("invalid command {kind}")]
    InvalidCommand { kind: String },
    #[error(transparent)]
    Wire(#[from] WireError),
}

impl PartialEq for LanSchemaError {
    fn eq(&self, other: &Self) -> bool {
        use LanSchemaError::*;
        match (self, other) {
            (UnsupportedClientBuild, UnsupportedClientBuild)
            | (UnsupportedCapabilities, UnsupportedCapabilities)
            | (InvalidAccountKey, InvalidAccountKey)
            | (InvalidTeam, InvalidTeam)
            | (InvalidMaxHealth, InvalidMaxHealth)
            | (InvalidVehicleConfiguration, InvalidVehicleConfiguration)
            | (InvalidEffectiveParams, InvalidEffectiveParams) => true,
            (InvalidCommand { kind: left }, InvalidCommand { kind: right }) => left == right,
            (Wire(left), Wire(right)) => left.to_string() == right.to_string(),
            _ => false,
        }
    }
}

impl Eq for LanSchemaError {}

impl LanSchemaError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::UnsupportedClientBuild => "unsupported_client_build",
            Self::UnsupportedCapabilities => "unsupported_capabilities",
            Self::InvalidAccountKey => "invalid_account_key",
            Self::InvalidTeam => "invalid_team",
            Self::InvalidMaxHealth => "invalid_max_health",
            Self::InvalidVehicleConfiguration => "invalid_vehicle_configuration",
            Self::InvalidEffectiveParams => "invalid_effective_params",
            Self::InvalidCommand { .. } => "invalid_command",
            Self::Wire(_) => "protocol",
        }
    }
}

pub fn parse_player_hello(hello: &Hello) -> Result<PlayerHello, LanSchemaError> {
    let object = hello.object();
    let capabilities = parse_capabilities(object.get("capabilities"))?;
    if !compatible_hello_protocol(hello, &capabilities) {
        return Err(LanSchemaError::Wire(WireError::ProtocolMismatch {
            expected: LAN_PROTOCOL_VERSION,
            actual: object
                .protocol()
                .map_or_else(|| "missing".to_owned(), |value| value.to_string()),
        }));
    }
    let declared_client_build = object
        .get("client_build")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or(LanSchemaError::UnsupportedClientBuild)?;
    if declared_client_build != CLIENT_BUILD_0922
        && !has_required_capabilities(&capabilities, MODERN_CLIENT_REQUIRED_CAPABILITIES)
    {
        return Err(LanSchemaError::UnsupportedClientBuild);
    }
    validate_capability_generation(&capabilities)?;
    let requested_name = object
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Player")
        .to_owned();
    let account_key = match object.get("account_key") {
        Some(Value::String(value)) if valid_account_key(value) => value.clone(),
        Some(_) => return Err(LanSchemaError::InvalidAccountKey),
        None => legacy_account_key(&requested_name),
    };
    let requested_team = parse_requested_team(object.get("requested_team"))?;
    let vehicle = object
        .get("vehicle")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .unwrap_or(DEFAULT_VEHICLE_0922)
        .to_owned();
    let max_health = parse_max_health(object.get("max_health"))?;
    let outfits = object.get("outfits").cloned().unwrap_or_else(|| json!({}));
    let compact = object
        .get("vehicle_compact_descr")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let ammo_remaining = parse_ammo_remaining(object.get("ammo_remaining"))?;
    let ammo_loaded_shell = object
        .get("ammo_loaded_shell")
        .cloned()
        .unwrap_or(Value::Null);
    let player_authority_loadout = object
        .get("player_authority_loadout")
        .cloned()
        .unwrap_or(Value::Null);
    let effective_params = object
        .get("effective_params")
        .cloned()
        .unwrap_or(Value::Null);
    if capabilities
        .iter()
        .any(|capability| capability == PLAYER_AMMO_AUTHORITY_V1)
        && (ammo_remaining.is_null() || ammo_loaded_shell.is_null())
    {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    if capabilities
        .iter()
        .any(|capability| capability == PLAYER_AUTHORITY_LOADOUT_V1)
        && player_authority_loadout.is_null()
    {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    if capabilities
        .iter()
        .any(|capability| capability == EFFECTIVE_PARAMS_V1)
        && !valid_effective_params(&effective_params)
    {
        return Err(LanSchemaError::InvalidEffectiveParams);
    }
    validate_vehicle_configuration(
        &outfits,
        &compact,
        &ammo_remaining,
        &ammo_loaded_shell,
        &player_authority_loadout,
    )?;

    Ok(PlayerHello {
        join: JoinRequest {
            account_key,
            requested_name,
            vehicle,
            max_health,
            requested_team,
            vehicle_configuration: json!({
                "outfits": outfits,
                "vehicle_compact_descr": compact,
                "ammo_remaining": ammo_remaining,
                "ammo_loaded_shell": ammo_loaded_shell,
                "player_authority_loadout": player_authority_loadout,
                "effective_params": effective_params,
            }),
        },
        // The label is informational after capability negotiation.  Keep one
        // internal build family so room/map logic cannot diverge by spelling.
        client_build: CLIENT_BUILD_0922.to_owned(),
        capabilities,
    })
}

pub fn compatible_hello_protocol(hello: &Hello, capabilities: &[String]) -> bool {
    hello.object().protocol() == Some(LAN_PROTOCOL_VERSION)
        || hello.object().protocol().is_some_and(|protocol| {
            protocol > 0
                && has_required_capabilities(capabilities, MODERN_CLIENT_REQUIRED_CAPABILITIES)
        })
}

pub fn has_required_capabilities(capabilities: &[String], required: &[&str]) -> bool {
    required
        .iter()
        .all(|required| capabilities.iter().any(|value| value == required))
}

fn parse_capabilities(value: Option<&Value>) -> Result<Vec<String>, LanSchemaError> {
    let values = value
        .and_then(Value::as_array)
        .ok_or(LanSchemaError::UnsupportedCapabilities)?;
    if values.len() > 32 {
        return Err(LanSchemaError::UnsupportedCapabilities);
    }
    let mut seen = BTreeSet::new();
    let mut result = Vec::with_capacity(values.len());
    for value in values {
        let capability = value
            .as_str()
            .filter(|value| !value.is_empty() && value.len() <= 64)
            .ok_or(LanSchemaError::UnsupportedCapabilities)?;
        if !seen.insert(capability) {
            return Err(LanSchemaError::UnsupportedCapabilities);
        }
        result.push(capability.to_owned());
    }
    Ok(result)
}

fn validate_capability_generation(capabilities: &[String]) -> Result<(), LanSchemaError> {
    let has = |value| capabilities.iter().any(|candidate| candidate == value);
    if has(PROJECTILE_LEDGER_V2)
        && ![
            DESTRUCTIBLE_CATALOG_V5,
            LEAN_SNAPSHOT_MANIFEST_V1,
            RAM_CONTACT_LEDGER_V3,
            HUMAN_RAM_TIMELINE_V1,
            PLAYER_FIRE_INTENT_V4,
            PLAYER_ENVIRONMENT_V2,
            EFFECTIVE_PARAMS_V1,
            RICOCHET_CONTINUATION_V1,
            PLAYER_AMMO_AUTHORITY_V1,
            PLAYER_AUTHORITY_LOADOUT_V1,
        ]
        .into_iter()
        .all(has)
    {
        return Err(LanSchemaError::UnsupportedCapabilities);
    }
    if !has(PROJECTILE_LEDGER_V2) && !has(PROJECTILE_LEDGER_V1) {
        return Err(LanSchemaError::UnsupportedCapabilities);
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct EffectiveRamInputs {
    pub mass: f64,
    pub spall_coefficient: f64,
    pub ramming_bonus: f64,
}

/// Read only the actor-scoped effective values consumed by ramming authority.
/// The client publishes a much larger canonical snapshot; unrelated sections
/// remain opaque here, while every value that can affect ram damage is strict.
pub(crate) fn effective_ram_inputs(value: &Value) -> Option<EffectiveRamInputs> {
    let fields = value.as_object()?;
    if fields.get("version").and_then(Value::as_u64) != Some(1) {
        return None;
    }
    let mass = fields
        .get("physics")?
        .as_object()?
        .get("mass")?
        .as_f64()
        .filter(|mass| mass.is_finite() && (1.0..=1_000_000.0).contains(mass))?;
    let ramming = fields.get("ramming")?.as_object()?;
    if ramming.len() != 2
        || !ramming.contains_key("spall_coefficient")
        || !ramming.contains_key("ramming_bonus")
    {
        return None;
    }
    let spall_coefficient = ramming
        .get("spall_coefficient")?
        .as_f64()
        .filter(|value| value.is_finite() && (1.0..=1.5).contains(value))?;
    let ramming_bonus = ramming
        .get("ramming_bonus")?
        .as_f64()
        .filter(|value| value.is_finite() && (0.0..=0.15).contains(value))?;
    Some(EffectiveRamInputs {
        mass,
        spall_coefficient,
        ramming_bonus,
    })
}

/// Read the immutable actor-scoped gun factors already composed by #1513.
/// Descriptor-owned bloom coefficients are parsed separately in `descriptor`.
pub(crate) fn effective_player_fire_factors(value: &Value) -> Option<EffectiveDispersionFactors> {
    let fields = value.as_object()?;
    if fields.get("version").and_then(Value::as_u64) != Some(1) {
        return None;
    }
    let loadout = fields.get("loadout")?.as_object()?;
    if loadout.get("from_client_factors").and_then(Value::as_bool) != Some(true) {
        return None;
    }
    let factor = |name: &str| {
        loadout
            .get(name)?
            .as_f64()
            .filter(|value| value.is_finite())
    };
    let parsed = EffectiveDispersionFactors {
        dispersion_factor: factor("dispersion_factor")?,
        aiming_time_factor: factor("aim_time_factor")?,
        movement_bloom_factor: factor("bloom_move_factor")?,
        hull_rotation_bloom_factor: factor("bloom_rotation_factor")?,
        turret_rotation_bloom_factor: factor("bloom_turret_factor")?,
    };
    parsed.validate().ok()?;
    Some(parsed)
}

pub(crate) fn valid_effective_params(value: &Value) -> bool {
    let Some(fields) = value.as_object() else {
        return false;
    };
    let required = [
        "version",
        "loadout",
        "physics",
        "spotting",
        "ramming",
        "ammo",
        "camouflage",
        "skills",
        "crew",
        "gun",
    ];
    fields.get("version").and_then(Value::as_u64) == Some(1)
        && required.into_iter().all(|name| fields.contains_key(name))
        && fields.len() <= 12
        && effective_ram_inputs(value).is_some()
        && effective_player_fire_factors(value).is_some()
        && serde_json::to_vec(value).is_ok_and(|encoded| encoded.len() <= 256 * 1024)
}

fn parse_requested_team(value: Option<&Value>) -> Result<Option<Team>, LanSchemaError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) if value.is_empty() || value == "auto" || value == "0" => {
            Ok(None)
        }
        Some(Value::Number(value)) if value.as_u64() == Some(0) => Ok(None),
        Some(Value::String(value)) if value == "1" => Ok(Some(Team::One)),
        Some(Value::String(value)) if value == "2" => Ok(Some(Team::Two)),
        Some(Value::Number(value)) if value.as_u64() == Some(1) => Ok(Some(Team::One)),
        Some(Value::Number(value)) if value.as_u64() == Some(2) => Ok(Some(Team::Two)),
        _ => Err(LanSchemaError::InvalidTeam),
    }
}

fn parse_max_health(value: Option<&Value>) -> Result<u32, LanSchemaError> {
    let value = match value {
        None | Some(Value::Null) => 1_000.0,
        Some(Value::Number(number)) => number
            .as_f64()
            .filter(|value| value.is_finite())
            .ok_or(LanSchemaError::InvalidMaxHealth)?,
        _ => return Err(LanSchemaError::InvalidMaxHealth),
    };
    Ok(value.clamp(1.0, 100_000.0) as u32)
}

fn valid_account_key(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn legacy_account_key(name: &str) -> String {
    let suffix: String = name
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .take(48)
        .collect();
    format!(
        "legacy_{}",
        if suffix.is_empty() { "player" } else { &suffix }
    )
}

fn parse_ammo_remaining(value: Option<&Value>) -> Result<Value, LanSchemaError> {
    let Some(value) = value else {
        return Ok(Value::Null);
    };
    if value.is_null() {
        return Ok(Value::Null);
    }
    let values = value
        .as_array()
        .filter(|values| (1..=16).contains(&values.len()))
        .ok_or(LanSchemaError::InvalidVehicleConfiguration)?;
    let mut total = 0_u64;
    for value in values {
        let count = value
            .as_u64()
            .filter(|count| *count <= 1_000)
            .ok_or(LanSchemaError::InvalidVehicleConfiguration)?;
        total = total
            .checked_add(count)
            .filter(|total| *total <= 1_000)
            .ok_or(LanSchemaError::InvalidVehicleConfiguration)?;
    }
    Ok(value.clone())
}

fn validate_vehicle_configuration(
    outfits: &Value,
    compact: &Value,
    ammo_remaining: &Value,
    ammo_loaded_shell: &Value,
    player_authority_loadout: &Value,
) -> Result<(), LanSchemaError> {
    let outfits = outfits
        .as_object()
        .ok_or(LanSchemaError::InvalidVehicleConfiguration)?;
    if outfits.len() > 3
        || outfits.iter().any(|(season, value)| {
            !matches!(season.as_str(), "1" | "2" | "4")
                || value
                    .as_str()
                    .is_none_or(|value| value.is_empty() || value.len() > 96 * 1024)
        })
    {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    if compact.as_str().is_none_or(|value| value.len() > 96 * 1024) {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    parse_ammo_remaining(Some(ammo_remaining))?;
    if !valid_ammo_loaded_shell(ammo_remaining, ammo_loaded_shell) {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    if !player_authority_loadout.is_null()
        && parse_player_authority_loadout(player_authority_loadout).is_err()
    {
        return Err(LanSchemaError::InvalidVehicleConfiguration);
    }
    Ok(())
}

fn valid_ammo_loaded_shell(ammo_remaining: &Value, loaded_shell: &Value) -> bool {
    if ammo_remaining.is_null() {
        return loaded_shell.is_null();
    }
    let Some(remaining) = ammo_remaining.as_array() else {
        return false;
    };
    let Some(index) = loaded_shell
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
    else {
        return false;
    };
    index < remaining.len()
        && (remaining.iter().all(|value| value.as_u64() == Some(0))
            || remaining[index].as_u64().is_some_and(|count| count > 0))
}

pub fn player_to_wire(player: &PlayerView) -> Value {
    let configuration = player.vehicle_configuration.as_object();
    let outfits = configuration
        .and_then(|value| value.get("outfits"))
        .cloned()
        .unwrap_or_else(|| json!({}));
    let compact = configuration
        .and_then(|value| value.get("vehicle_compact_descr"))
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let ammo_remaining = configuration
        .and_then(|value| value.get("ammo_remaining"))
        .cloned()
        .unwrap_or(Value::Null);
    let ammo_loaded_shell = configuration
        .and_then(|value| value.get("ammo_loaded_shell"))
        .cloned()
        .unwrap_or(Value::Null);
    let player_authority_loadout = configuration
        .and_then(|value| value.get("player_authority_loadout"))
        .cloned()
        .unwrap_or(Value::Null);
    let effective_params = configuration
        .and_then(|value| value.get("effective_params"))
        .cloned()
        .unwrap_or(Value::Null);
    let loaded_shell_index = ammo_loaded_shell.as_u64().unwrap_or(0);
    let (spawn_x, spawn_z, yaw) = spawn_for(player.slot, player.team);
    let mut value = json!({
        "id": player.player_id,
        "name": player.name,
        "vehicle": player.vehicle,
        "vehicle_compact_descr": compact,
        "outfits": outfits,
        "team": player.team.number(),
        "slot": player.slot,
        "world_pose": false,
        "spawn_x": spawn_x,
        "spawn_z": spawn_z,
        "x": spawn_x,
        "y": 0.0,
        "z": spawn_z,
        "yaw": yaw,
        "pitch": 0.0,
        "roll": 0.0,
        "aim_yaw": yaw,
        "gun_pitch": 0.0,
        "forward": 0.0,
        "turn": 0.0,
        "speed": 0.0,
        "input_seq": 0,
        "siege_state": 0,
        "siege_time_left_ms": 0,
        "fire_seq": 0,
        "shell_index": loaded_shell_index,
        "health": player.health,
        "max_health": player.max_health,
        "alive": true,
        "death_reason": 0,
        "display_health": player.health,
        "frags": 0,
        "team_killer": false,
        "death_attacker_kind": "",
        "death_attacker_id": 0,
        "critical_revision": 0,
        "critical_base_revision": 0,
        "critical_ack_seq": 0,
        "ram_contact_admitted_seq": 0,
        "ram_contact_resolved_seq": 0,
    });
    let fields = value
        .as_object_mut()
        .expect("player wire projection is an object");
    fields.insert("ammo_remaining".to_owned(), ammo_remaining);
    fields.insert("ammo_loaded_shell".to_owned(), ammo_loaded_shell);
    fields.insert(
        "player_authority_loadout".to_owned(),
        player_authority_loadout,
    );
    fields.insert("effective_params".to_owned(), effective_params);
    fields.insert("next_shell_index".to_owned(), json!(loaded_shell_index));
    fields.insert("shell_change_pending".to_owned(), json!(false));
    fields.insert("ammo_reload_pending".to_owned(), json!(false));
    fields.insert("landing_observation_seq".to_owned(), json!(0));
    fields.insert("up_cosine".to_owned(), json!(1.0));
    fields.insert("equipment_states".to_owned(), json!([]));
    fields.insert("equipment_revision".to_owned(), json!(0));
    fields.insert("equipment_intent_seq".to_owned(), json!(0));
    fields.insert(
        "equipment_intent_result".to_owned(),
        json!({"intent_seq": 0, "accepted": false, "reason": ""}),
    );
    value
}

pub fn welcome_message(
    room: &RoomState,
    player: &PlayerView,
    client_build: &str,
    capabilities: &[String],
    map: &str,
) -> Result<WireObject, WireError> {
    let configuration = player.vehicle_configuration.as_object();
    let outfits = configuration
        .and_then(|value| value.get("outfits"))
        .cloned()
        .unwrap_or_else(|| json!({}));
    let compact = configuration
        .and_then(|value| value.get("vehicle_compact_descr"))
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let ammo_remaining = configuration
        .and_then(|value| value.get("ammo_remaining"))
        .cloned()
        .unwrap_or(Value::Null);
    let ammo_loaded_shell = configuration
        .and_then(|value| value.get("ammo_loaded_shell"))
        .cloned()
        .unwrap_or(Value::Null);
    let player_authority_loadout = configuration
        .and_then(|value| value.get("player_authority_loadout"))
        .cloned()
        .unwrap_or(Value::Null);
    let effective_params = configuration
        .and_then(|value| value.get("effective_params"))
        .cloned()
        .unwrap_or(Value::Null);
    let (x, z, yaw) = spawn_for(player.slot, player.team);
    let mut fields = Map::new();
    fields.insert("protocol".to_owned(), json!(LAN_PROTOCOL_VERSION));
    fields.insert("client_build".to_owned(), json!(client_build));
    fields.insert("player_id".to_owned(), json!(player.player_id));
    fields.insert("name".to_owned(), json!(player.name));
    fields.insert("vehicle".to_owned(), json!(player.vehicle));
    fields.insert("vehicle_compact_descr".to_owned(), compact);
    fields.insert("ammo_remaining".to_owned(), ammo_remaining);
    fields.insert("ammo_loaded_shell".to_owned(), ammo_loaded_shell);
    fields.insert(
        "player_authority_loadout".to_owned(),
        player_authority_loadout,
    );
    fields.insert("effective_params".to_owned(), effective_params);
    fields.insert("outfits".to_owned(), outfits);
    fields.insert("team".to_owned(), json!(player.team.number()));
    fields.insert("slot".to_owned(), json!(player.slot));
    fields.insert("max_health".to_owned(), json!(player.max_health));
    fields.insert("map".to_owned(), json!(map));
    fields.insert("map_pool".to_owned(), json!(MAP_POOL_0922));
    fields.insert("host_player_id".to_owned(), json!(room.host_player_id()));
    fields.insert("phase".to_owned(), json!(phase_name(room.phase())));
    fields.insert("round_id".to_owned(), json!(room.round_id()));
    fields.insert("state_revision".to_owned(), json!(room.state_revision()));
    fields.insert("spawn".to_owned(), json!({"x":x,"y":0.0,"z":z,"yaw":yaw}));
    fields.insert("bot_authority_id".to_owned(), Value::Null);
    fields.insert("authority_epoch".to_owned(), json!(room.authority_epoch()));
    fields.insert("server_time_ms".to_owned(), json!(0));
    fields.insert(
        "team_size".to_owned(),
        json!(room.config().team_capacities[0].max(room.config().team_capacities[1])),
    );
    fields.insert("team_sizes".to_owned(), team_sizes(room));
    fields.insert(
        "bot_tier_mode".to_owned(),
        json!(room.bot_tier_mode().as_str()),
    );
    fields.insert("capabilities".to_owned(), json!(capabilities));
    fields.insert("server_capabilities".to_owned(), json!(SERVER_CAPABILITIES));
    WireObject::with_fields("welcome", fields)
}

pub fn roster_message(room: &RoomState, map: &str) -> Result<WireObject, WireError> {
    let mut fields = Map::new();
    fields.insert("protocol".to_owned(), json!(LAN_PROTOCOL_VERSION));
    fields.insert("round_id".to_owned(), json!(room.round_id()));
    fields.insert("state_revision".to_owned(), json!(room.state_revision()));
    fields.insert("phase".to_owned(), json!(phase_name(room.phase())));
    fields.insert("map".to_owned(), json!(map));
    fields.insert("map_pool".to_owned(), json!(MAP_POOL_0922));
    fields.insert(
        "players".to_owned(),
        Value::Array(
            room.players()
                .map(|player| player_to_wire(&player))
                .collect(),
        ),
    );
    fields.insert("host_player_id".to_owned(), json!(room.host_player_id()));
    fields.insert("bot_authority_id".to_owned(), Value::Null);
    fields.insert("authority_epoch".to_owned(), json!(room.authority_epoch()));
    fields.insert("server_time_ms".to_owned(), json!(0));
    fields.insert(
        "team_size".to_owned(),
        json!(room.config().team_capacities[0].max(room.config().team_capacities[1])),
    );
    fields.insert("team_sizes".to_owned(), team_sizes(room));
    fields.insert(
        "bot_tier_mode".to_owned(),
        json!(room.bot_tier_mode().as_str()),
    );
    WireObject::with_fields("roster", fields)
}

pub fn error_message(code: &str, message: &str) -> Result<WireObject, WireError> {
    let mut fields = Map::new();
    fields.insert("code".to_owned(), json!(code));
    fields.insert("message".to_owned(), json!(message));
    WireObject::with_fields("error", fields)
}

fn team_sizes(room: &RoomState) -> Value {
    json!({
        "1": room.config().team_capacities[0],
        "2": room.config().team_capacities[1],
    })
}

fn phase_name(phase: RoomPhase) -> &'static str {
    match phase {
        RoomPhase::Waiting => "waiting",
        RoomPhase::Loading => "loading",
        RoomPhase::Battle => "battle",
        RoomPhase::Finished => "battle",
    }
}

pub fn spawn_for(slot: usize, team: Team) -> (f64, f64, f64) {
    (
        slot as f64 * 12.0,
        if team == Team::One { -35.0 } else { 35.0 },
        if team == Team::One {
            0.0
        } else {
            std::f64::consts::PI
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::room::{EndpointIdentity, RoomConfig};
    use crate::wire::ConnectionRole;

    fn hello(capabilities: &[&str]) -> Hello {
        let value = json!({
            "type":"hello",
            "protocol":5,
            "client_build":CLIENT_BUILD_0922,
            "capabilities":capabilities,
            "account_key":"account_1",
            "name":"Tester",
            "vehicle":"ussr:R11_MS-1",
            "max_health":90,
            "ammo_remaining":[51],
            "ammo_loaded_shell":0,
            "player_authority_loadout":{
                "repair":{"available":false},
                "spotting":{"available":false}
            },
            "effective_params": {
                "version": 1,
                "loadout": {
                    "aim_time_factor": 0.96,
                    "dispersion_factor": 0.96,
                    "bloom_move_factor": 0.8,
                    "bloom_rotation_factor": 0.8,
                    "bloom_turret_factor": 0.74,
                    "from_client_factors": true
                },
                "physics": {"mass": 8000.0},
                "spotting": {},
                "ramming": {
                    "spall_coefficient": 1.0,
                    "ramming_bonus": 0.0
                },
                "ammo": [],
                "camouflage": {},
                "skills": {},
                "crew": {},
                "gun": {},
            },
            "outfits":{},
            "vehicle_compact_descr":"",
        });
        Hello::try_from(WireObject::try_from(value).unwrap()).unwrap()
    }

    #[test]
    fn current_main_hello_maps_to_room_join_without_wire_tokens() {
        let parsed = parse_player_hello(&hello(&[
            PROJECTILE_LEDGER_V2,
            DESTRUCTIBLE_CATALOG_V5,
            LEAN_SNAPSHOT_MANIFEST_V1,
            RAM_CONTACT_LEDGER_V3,
            HUMAN_RAM_TIMELINE_V1,
            PLAYER_FIRE_INTENT_V4,
            PLAYER_ENVIRONMENT_V2,
            EFFECTIVE_PARAMS_V1,
            RICOCHET_CONTINUATION_V1,
            PLAYER_AMMO_AUTHORITY_V1,
            PLAYER_AUTHORITY_LOADOUT_V1,
        ]))
        .unwrap();
        assert_eq!(parsed.join.account_key, "account_1");
        assert_eq!(parsed.join.max_health, 90);
        assert_eq!(parsed.join.requested_team, None);
        assert_eq!(parsed.capabilities[0], PROJECTILE_LEDGER_V2);
        assert_eq!(
            parsed.join.vehicle_configuration["player_authority_loadout"]["repair"]["available"],
            json!(false)
        );
    }

    #[test]
    fn modern_player_loadout_capability_requires_strict_actor_payload() {
        let modern = [
            PROJECTILE_LEDGER_V2,
            DESTRUCTIBLE_CATALOG_V5,
            LEAN_SNAPSHOT_MANIFEST_V1,
            RAM_CONTACT_LEDGER_V3,
            HUMAN_RAM_TIMELINE_V1,
            PLAYER_FIRE_INTENT_V4,
            PLAYER_ENVIRONMENT_V2,
            EFFECTIVE_PARAMS_V1,
            RICOCHET_CONTINUATION_V1,
            PLAYER_AMMO_AUTHORITY_V1,
            PLAYER_AUTHORITY_LOADOUT_V1,
        ];
        let mut missing = hello(&modern).into_object().into_fields();
        missing.remove("player_authority_loadout");
        let missing = Hello::try_from(WireObject::with_fields("hello", missing).unwrap()).unwrap();
        assert_eq!(
            parse_player_hello(&missing),
            Err(LanSchemaError::InvalidVehicleConfiguration)
        );

        let mut unknown = hello(&modern).into_object().into_fields();
        unknown.insert(
            "player_authority_loadout".to_owned(),
            json!({
                "repair":{"available":false,"borrowDonor":true},
                "spotting":{"available":false}
            }),
        );
        let unknown = Hello::try_from(WireObject::with_fields("hello", unknown).unwrap()).unwrap();
        assert_eq!(
            parse_player_hello(&unknown),
            Err(LanSchemaError::InvalidVehicleConfiguration)
        );
    }

    #[test]
    fn legacy_protocol_v5_smoke_capability_stays_joinable() {
        let parsed = parse_player_hello(&hello(&[PROJECTILE_LEDGER_V1])).unwrap();
        assert_eq!(parsed.capabilities, vec![PROJECTILE_LEDGER_V1]);
    }

    #[test]
    fn partial_modern_capability_set_is_rejected() {
        assert_eq!(
            parse_player_hello(&hello(&[PROJECTILE_LEDGER_V2])),
            Err(LanSchemaError::UnsupportedCapabilities)
        );
    }

    #[test]
    fn capability_complete_peer_negotiates_future_labels() {
        let modern = [
            PROJECTILE_LEDGER_V2,
            DESTRUCTIBLE_CATALOG_V5,
            LEAN_SNAPSHOT_MANIFEST_V1,
            RAM_CONTACT_LEDGER_V3,
            HUMAN_RAM_TIMELINE_V1,
            PLAYER_FIRE_INTENT_V4,
            PLAYER_ENVIRONMENT_V2,
            EFFECTIVE_PARAMS_V1,
            RICOCHET_CONTINUATION_V1,
            PLAYER_AMMO_AUTHORITY_V1,
            PLAYER_AUTHORITY_LOADOUT_V1,
        ];
        let mut fields = hello(&modern).into_object().into_fields();
        fields.insert("protocol".to_owned(), json!(6));
        fields.insert("client_build".to_owned(), json!("future-package-label"));
        let future = Hello::try_from(WireObject::with_fields("hello", fields).unwrap()).unwrap();
        let parsed = parse_player_hello(&future).unwrap();
        assert_eq!(parsed.client_build, CLIENT_BUILD_0922);
    }

    #[test]
    fn future_protocol_requires_complete_modern_capabilities() {
        let mut fields = hello(&[PROJECTILE_LEDGER_V1]).into_object().into_fields();
        fields.insert("protocol".to_owned(), json!(6));
        let future = Hello::try_from(WireObject::with_fields("hello", fields).unwrap()).unwrap();
        assert!(matches!(
            parse_player_hello(&future),
            Err(LanSchemaError::Wire(WireError::ProtocolMismatch { .. }))
        ));
    }

    #[test]
    fn effective_ram_inputs_are_actor_scoped_exact_and_bounded() {
        let mut value = hello(&[PROJECTILE_LEDGER_V1])
            .into_object()
            .into_fields()
            .remove("effective_params")
            .unwrap();
        assert_eq!(
            effective_ram_inputs(&value),
            Some(EffectiveRamInputs {
                mass: 8_000.0,
                spall_coefficient: 1.0,
                ramming_bonus: 0.0,
            })
        );

        for (path, invalid) in [
            (("physics", "mass"), json!(0.0)),
            (("ramming", "spall_coefficient"), json!(1.500_001)),
            (("ramming", "ramming_bonus"), json!(0.150_001)),
        ] {
            let mut malformed = value.clone();
            malformed[path.0][path.1] = invalid;
            assert_eq!(effective_ram_inputs(&malformed), None);
            assert!(!valid_effective_params(&malformed));
        }

        value["ramming"]["estimated"] = json!(true);
        assert_eq!(effective_ram_inputs(&value), None);
        assert!(!valid_effective_params(&value));
    }

    #[test]
    fn effective_player_fire_factors_are_actor_scoped_exact_and_bounded() {
        let value = hello(&[PROJECTILE_LEDGER_V1])
            .into_object()
            .into_fields()
            .remove("effective_params")
            .unwrap();
        assert_eq!(
            effective_player_fire_factors(&value),
            Some(EffectiveDispersionFactors {
                dispersion_factor: 0.96,
                aiming_time_factor: 0.96,
                movement_bloom_factor: 0.8,
                hull_rotation_bloom_factor: 0.8,
                turret_rotation_bloom_factor: 0.74,
            })
        );

        for (name, invalid) in [
            ("dispersion_factor", json!(0.0)),
            ("aim_time_factor", json!("NaN")),
            ("bloom_move_factor", json!(16.000_001)),
            ("bloom_rotation_factor", json!(-1.0)),
            ("bloom_turret_factor", Value::Null),
        ] {
            let mut malformed = value.clone();
            malformed["loadout"][name] = invalid;
            assert_eq!(effective_player_fire_factors(&malformed), None);
            assert!(!valid_effective_params(&malformed));
        }

        let mut missing = value.clone();
        missing["loadout"]
            .as_object_mut()
            .unwrap()
            .remove("dispersion_factor");
        assert_eq!(effective_player_fire_factors(&missing), None);
        assert!(!valid_effective_params(&missing));

        let mut reconstructed = value;
        reconstructed["loadout"]["from_client_factors"] = json!(false);
        assert_eq!(effective_player_fire_factors(&reconstructed), None);
        assert!(!valid_effective_params(&reconstructed));
    }

    #[test]
    fn welcome_and_roster_satisfy_visible_client_identity_contract() {
        let mut room = RoomState::new(RoomConfig::default());
        let parsed = parse_player_hello(&hello(&[PROJECTILE_LEDGER_V1])).unwrap();
        let joined = room
            .join_player(
                EndpointIdentity::from_transport(1, "internal-token"),
                parsed.join,
            )
            .unwrap();
        let welcome = welcome_message(
            &room,
            &joined.player,
            CLIENT_BUILD_0922,
            &parsed.capabilities,
            "01_karelia",
        )
        .unwrap();
        assert_eq!(welcome.kind(), "welcome");
        assert_eq!(welcome.get("player_id"), Some(&json!(1)));
        assert_eq!(welcome.get("host_player_id"), Some(&json!(1)));
        assert_eq!(welcome.get("authority_epoch"), Some(&json!(0)));
        assert_eq!(welcome.get("phase"), Some(&json!("waiting")));
        assert_eq!(welcome.get("team_sizes"), Some(&json!({"1":15,"2":15})));
        assert_eq!(welcome.get("bot_tier_mode"), Some(&json!("random")));
        assert_eq!(welcome.get("role"), None);
        let roster = roster_message(&room, "01_karelia").unwrap();
        assert_eq!(roster.get("players").unwrap().as_array().unwrap().len(), 1);
        assert_eq!(roster.get("bot_tier_mode"), Some(&json!("random")));
    }

    #[test]
    fn worker_role_does_not_parse_as_player_hello() {
        let mut fields = hello(&[PROJECTILE_LEDGER_V1]).into_object().into_fields();
        fields.insert("role".to_owned(), json!("simulation_worker"));
        let worker = Hello::try_from(WireObject::with_fields("hello", fields).unwrap()).unwrap();
        assert_eq!(worker.role(), ConnectionRole::SimulationWorker);
    }
}
