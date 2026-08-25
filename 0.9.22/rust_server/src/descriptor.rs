//! Strict admission of vehicle descriptors donated by a WoT #1513 client.
//!
//! `descriptor_donation.py` deliberately sends plain JSON instead of native
//! BigWorld objects.  This module is the only place where that projection is
//! converted into the engine-free bot simulation types.  Unknown projection
//! fields are retained outside this boundary, but every field consumed here is
//! finite, bounded, and shape checked before it reaches the simulation.

use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::{FRAC_PI_2, PI};

use serde_json::{Map, Value};
use thiserror::Error;

use crate::bot_sim::{
    BotProfile, ClipDescriptor, GunDescriptor, GunYawLimits, PhysicsProfile, ShellDescriptor,
    ShellProfile, VehicleClass, VehicleDescriptor,
};
use crate::combat_rules::{MaterialInfo, MAX_HE_FACTOR, MIN_HE_FACTOR};
use crate::critical_damage::{
    CrewMemberProfile, CrewName, CrewRole, CriticalProfile, DeviceName, DeviceProfile,
    MAX_CRITICAL_DEVICE_HP,
};
use crate::player_ammo::PhysicalBurstDescriptor;
use crate::player_fire_clock::PlayerGunDispersionLaw;
use crate::projectile::{SourceShell, SourceShot};
use crate::ram::{
    RamDamageProfile, RamShape, MAX_CONTROLLED_IMPACT_BONUS, MAX_SPALL_COEFFICIENT,
    MIN_SPALL_COEFFICIENT,
};
use crate::spotting::{CamouflageAspect, ObserverView, TargetCamouflage};

pub const MAX_DESCRIPTOR_JSON_BYTES: usize = 256 * 1024;
pub const MAX_DESCRIPTOR_NODES: usize = 8_192;
pub const MAX_DESCRIPTOR_DEPTH: usize = 8;
pub const MAX_DESCRIPTOR_STRING_BYTES: usize = 256;
pub const MAX_TAGS: usize = 32;
pub const MAX_SHOTS: usize = 16;
pub const MAX_CREW: usize = 8;

const MAX_OBJECT_FIELDS: usize = 128;
const MAX_ARRAY_ITEMS: usize = 256;
const MAX_VEHICLE_KEY_BYTES: usize = 128;
const MAX_TAG_BYTES: usize = 48;
const MAX_SHELL_KIND_BYTES: usize = 64;
const MAX_HEALTH: f64 = 100_000.0;
const MAX_AMMO: f64 = 1_000.0;
const MAX_REPAIR_FACTOR: f64 = 100.0;
const MAX_SPOTTING_RANGE_METRES: f64 = 1_000.0;
const MAX_SPOTTING_FACTOR: f64 = 10.0;
const MAX_SPOTTING_ASPECT_ADDITIVE: f64 = 10.0;
const MAX_SPOTTING_DELAY_US: u64 = 60_000_000;
const MAX_BBOX_COORDINATE: f64 = 100.0;
const MAX_VEHICLE_HALF_EXTENT: f64 = 20.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RepairInput {
    pub repair_factor: f64,
    pub has_big_kit: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum DonatedRepairLoadout {
    Available(RepairInput),
    Unavailable,
}

impl DonatedRepairLoadout {
    /// Return a proven #1513 input, or `None` when the donor explicitly could
    /// not observe that actor's loadout. Callers must not synthesize a repair
    /// factor for `Unavailable`.
    pub fn input(self) -> Option<RepairInput> {
        match self {
            Self::Available(input) => Some(input),
            Self::Unavailable => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AuthorityRepairSettings {
    pub player: DonatedRepairLoadout,
    pub bot_default: DonatedRepairLoadout,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AuthoritySpottingInput {
    pub observer: ObserverView,
    /// Descriptor/loadout camouflage with no dynamic fire timestamp. The
    /// battle runtime installs the canonical server fire time on its own copy.
    pub target: TargetCamouflage,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum DonatedSpottingLoadout {
    Available(AuthoritySpottingInput),
    Unavailable,
}

impl DonatedSpottingLoadout {
    /// Return only inputs the #1513 donor could calculate through its native
    /// attribute-factor chain. `Unavailable` must never grow server defaults.
    pub fn input(self) -> Option<AuthoritySpottingInput> {
        match self {
            Self::Available(input) => Some(input),
            Self::Unavailable => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AuthoritySpottingSettings {
    /// The sole descriptor donor's selected garage crew/loadout. This cannot
    /// represent another human's different loadout under the current one-donor
    /// exchange and therefore remains an explicitly actor-scoped value.
    pub player: DonatedSpottingLoadout,
    /// The same mounted descriptor evaluated with #1513's generated default
    /// crew, no player consumables, and no player camouflage paint.
    pub bot_default: DonatedSpottingLoadout,
}

/// Exact connection-scoped loadout donated by one human participant.
///
/// Unlike the round's descriptor projection, this value is never shared
/// between actors. Explicitly unavailable inputs stay unavailable so callers
/// cannot fall back to the descriptor donor's crew or equipment.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerAuthorityLoadout {
    pub repair: DonatedRepairLoadout,
    pub spotting: DonatedSpottingLoadout,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ParsedDescriptor {
    pub descriptor: VehicleDescriptor,
    pub profile: BotProfile,
    /// Immutable installed-gun dispersion inputs donated by the exact client.
    /// Actor-scoped crew and equipment multipliers are intentionally separate.
    pub player_fire_law: PlayerGunDispersionLaw,
    /// Canonical mounted-gun law keyed by the contiguous #1513 shell index.
    ///
    /// `VehicleDescriptor::gun.shells` remains the planner's reduced view;
    /// projectile authority must use this exact source instead of reconstructing
    /// damage, penetration falloff, or shell geometry from representative
    /// values.
    pub mounted_shots: BTreeMap<u8, SourceShot>,
    /// Exact physical-shell grouping decoded from #1513 `gun.burst`.
    /// Every later shell is still independently debited and simulated.
    pub physical_burst: PhysicalBurstDescriptor,
    /// Exact module HP, crew-role, fire, and repair law donated by #1513.
    pub critical_profile: CriticalProfile,
    /// Descriptor-donor and generated-bot projections remain separate. Human
    /// runtime authority must use the participant's connection-scoped
    /// `PlayerAuthorityLoadout`, never this donor-owned `player` value.
    pub repair_settings: AuthorityRepairSettings,
    /// Donor-only human view/concealment plus generated-bot defaults. Human
    /// runtime authority uses the participant-scoped configuration donation.
    pub spotting_settings: AuthoritySpottingSettings,
    /// Exact chassis contact footprint and descriptor-relative vertical span.
    /// Ramming must not substitute the hull bbox used by planner traffic.
    pub ram_shape: RamShape,
    /// Current-main bot authority evaluates the descriptor with no human
    /// equipment or Controlled Impact skill. Human profiles remain actor-scoped
    /// under `effective_params.ramming` and must never use this value.
    pub bot_ramming_profile: RamDamageProfile,
    /// Structural hull thicknesses consumed only by the copied HE fallback
    /// when the native impact ray did not expose a structural plate.
    pub hull_materials: Vec<MaterialInfo>,
    pub level: u8,
    /// Sorted, duplicate-free #1513 type tags.
    pub tags: Vec<String>,
}

impl ParsedDescriptor {
    pub fn vehicle_key(&self) -> &str {
        &self.descriptor.vehicle_key
    }

    pub fn mounted_shot(&self, shell_index: u8) -> Option<&SourceShot> {
        self.mounted_shots.get(&shell_index)
    }
}

#[derive(Debug, Error)]
pub enum DescriptorError {
    #[error("descriptor JSON exceeds {maximum} bytes (got {actual})")]
    JsonTooLarge { actual: usize, maximum: usize },
    #[error("descriptor JSON is invalid: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("{path}: {message}")]
    InvalidField { path: String, message: String },
}

/// Parse one encoded projection.  This entry point also enforces the wire-size
/// ceiling before allocating a `serde_json::Value` tree.
pub fn parse_projection_json(encoded: &str) -> Result<ParsedDescriptor, DescriptorError> {
    if encoded.len() > MAX_DESCRIPTOR_JSON_BYTES {
        return Err(DescriptorError::JsonTooLarge {
            actual: encoded.len(),
            maximum: MAX_DESCRIPTOR_JSON_BYTES,
        });
    }
    let value = serde_json::from_str(encoded)?;
    parse_projection(&value)
}

/// Parse one already-decoded `descriptor_donation.project_descriptor` value.
pub fn parse_projection(value: &Value) -> Result<ParsedDescriptor, DescriptorError> {
    parse_projection_inner(None, value)
}

/// Strictly admit one participant's connection-scoped repair and spotting
/// inputs carried in `vehicle_configuration`.
pub fn parse_player_authority_loadout(
    value: &Value,
) -> Result<PlayerAuthorityLoadout, DescriptorError> {
    let mut nodes = 0;
    validate_tree(value, "$", 0, &mut nodes)?;
    let root = object(value, "$")?;
    exact_fields(root, "$", &["repair", "spotting"])?;
    Ok(PlayerAuthorityLoadout {
        repair: parse_repair_loadout(required(root, "repair", "$")?, "$.repair")?,
        spotting: parse_spotting_loadout(required(root, "spotting", "$")?, "$.spotting")?,
    })
}

/// Parse a projection carried under a descriptor-bundle map key.  Requiring
/// the key and projected type name to agree prevents one donated descriptor
/// from being silently installed for another vehicle.
pub fn parse_projection_for(
    expected_vehicle_key: &str,
    value: &Value,
) -> Result<ParsedDescriptor, DescriptorError> {
    validate_text(
        expected_vehicle_key,
        "$.expected_vehicle_key",
        MAX_VEHICLE_KEY_BYTES,
    )?;
    parse_projection_inner(Some(expected_vehicle_key), value)
}

fn parse_projection_inner(
    expected_vehicle_key: Option<&str>,
    value: &Value,
) -> Result<ParsedDescriptor, DescriptorError> {
    let mut nodes = 0;
    validate_tree(value, "$", 0, &mut nodes)?;
    let root = object(value, "$")?;

    let vehicle_key = text(
        required(root, "name", "$")?,
        "$.name",
        MAX_VEHICLE_KEY_BYTES,
    )?;
    if let Some(expected) = expected_vehicle_key {
        if expected != vehicle_key {
            return Err(invalid(
                "$.name",
                format!("projected name {vehicle_key:?} does not match bundle key {expected:?}"),
            ));
        }
    }
    let level = integer(required(root, "level", "$")?, "$.level", 1.0, 10.0)? as u8;
    let tags = parse_tags(required(root, "tags", "$")?, "$.tags")?;

    let type_info = object(required(root, "type", "$")?, "$.type")?;
    let type_name = text(
        required(type_info, "name", "$.type")?,
        "$.type.name",
        MAX_VEHICLE_KEY_BYTES,
    )?;
    if type_name != vehicle_key {
        return Err(invalid(
            "$.type.name",
            format!("nested type name {type_name:?} does not match $.name {vehicle_key:?}"),
        ));
    }
    let type_level = integer(
        required(type_info, "level", "$.type")?,
        "$.type.level",
        1.0,
        10.0,
    )? as u8;
    if type_level != level {
        return Err(invalid(
            "$.type.level",
            format!("nested level {type_level} does not match $.level {level}"),
        ));
    }
    let nested_tags = parse_tags(required(type_info, "tags", "$.type")?, "$.type.tags")?;
    if nested_tags != tags {
        return Err(invalid("$.type.tags", "nested tags do not match $.tags"));
    }

    let max_health = integer(
        required(root, "maxHealth", "$")?,
        "$.maxHealth",
        1.0,
        MAX_HEALTH,
    )? as u32;
    let (half_length, half_width) = parse_hull_extents(root)?;
    let ram_shape = parse_ram_shape(root)?;
    let hull_materials = parse_he_structural_armor(root)?;
    let (crew_roster, crew_profile) = parse_crew(type_info)?;
    let (gun, mounted_shots, physical_burst) = parse_gun(root)?;
    let player_fire_law = parse_player_fire_law(root)?;
    let physics = parse_physics(root)?;
    let module_names = parse_modules(root)?;
    let critical_profile = parse_critical_profile(root, crew_profile)?;
    let repair_settings = parse_repair_settings(root)?;
    let spotting_settings = parse_spotting_settings(root)?;
    let bot_ramming_profile = parse_bot_ramming_profile(root)?;
    let class = vehicle_class(&tags);
    let shells = gun
        .shells
        .iter()
        .map(|shell| ShellProfile {
            index: shell.index,
            kind: shell.kind.clone(),
            penetration: shell.penetration,
        })
        .collect();

    Ok(ParsedDescriptor {
        descriptor: VehicleDescriptor {
            vehicle_key: vehicle_key.to_owned(),
            max_ammo: parse_max_ammo(root)?,
            max_health,
            half_length,
            half_width,
            gun,
            physics,
            module_names,
            crew_roster,
        },
        profile: BotProfile { class, shells },
        player_fire_law,
        mounted_shots,
        physical_burst,
        critical_profile,
        repair_settings,
        spotting_settings,
        ram_shape,
        bot_ramming_profile,
        hull_materials,
        level,
        tags,
    })
}

fn parse_bot_ramming_profile(
    root: &Map<String, Value>,
) -> Result<RamDamageProfile, DescriptorError> {
    let settings_path = "$.rammingSettings";
    let settings = object(required(root, "rammingSettings", "$")?, settings_path)?;
    exact_fields(settings, settings_path, &["botDefault"])?;
    let profile_path = "$.rammingSettings.botDefault";
    let profile = object(
        required(settings, "botDefault", settings_path)?,
        profile_path,
    )?;
    exact_fields(
        profile,
        profile_path,
        &["spall_coefficient", "ramming_bonus"],
    )?;
    let spall_coefficient = number(
        required(profile, "spall_coefficient", profile_path)?,
        &format!("{profile_path}.spall_coefficient"),
        MIN_SPALL_COEFFICIENT,
        MAX_SPALL_COEFFICIENT,
    )?;
    let controlled_impact_bonus = number(
        required(profile, "ramming_bonus", profile_path)?,
        &format!("{profile_path}.ramming_bonus"),
        0.0,
        MAX_CONTROLLED_IMPACT_BONUS,
    )?;
    RamDamageProfile::new(spall_coefficient, controlled_impact_bonus)
        .map_err(|error| invalid(profile_path, error.to_string()))
}

fn parse_spotting_settings(
    root: &Map<String, Value>,
) -> Result<AuthoritySpottingSettings, DescriptorError> {
    let path = "$.spottingSettings";
    let settings = object(required(root, "spottingSettings", "$")?, path)?;
    exact_fields(settings, path, &["player", "botDefault"])?;
    Ok(AuthoritySpottingSettings {
        player: parse_spotting_loadout(
            required(settings, "player", path)?,
            &format!("{path}.player"),
        )?,
        bot_default: parse_spotting_loadout(
            required(settings, "botDefault", path)?,
            &format!("{path}.botDefault"),
        )?,
    })
}

fn parse_spotting_loadout(
    value: &Value,
    path: &str,
) -> Result<DonatedSpottingLoadout, DescriptorError> {
    let loadout = object(value, path)?;
    let available = boolean(
        required(loadout, "available", path)?,
        &format!("{path}.available"),
    )?;
    if !available {
        exact_fields(loadout, path, &["available"])?;
        return Ok(DonatedSpottingLoadout::Unavailable);
    }
    exact_fields(loadout, path, &["available", "observer", "target"])?;
    let observer_path = format!("{path}.observer");
    let observer = object(required(loadout, "observer", path)?, &observer_path)?;
    exact_fields(
        observer,
        &observer_path,
        &[
            "baseRangeMetres",
            "miscFactor",
            "crewFactor",
            "binocularFactor",
            "hasBinoculars",
            "binocularDelayUs",
        ],
    )?;
    let has_binoculars = boolean(
        required(observer, "hasBinoculars", &observer_path)?,
        &format!("{observer_path}.hasBinoculars"),
    )?;
    let binocular_delay_us = integer(
        required(observer, "binocularDelayUs", &observer_path)?,
        &format!("{observer_path}.binocularDelayUs"),
        0.0,
        MAX_SPOTTING_DELAY_US as f64,
    )?;
    if !has_binoculars && binocular_delay_us != 0 {
        return Err(invalid(
            format!("{observer_path}.binocularDelayUs"),
            "must be zero when hasBinoculars is false",
        ));
    }
    let observer = ObserverView {
        base_range_metres: number(
            required(observer, "baseRangeMetres", &observer_path)?,
            &format!("{observer_path}.baseRangeMetres"),
            1.0,
            MAX_SPOTTING_RANGE_METRES,
        )?,
        misc_factor: number(
            required(observer, "miscFactor", &observer_path)?,
            &format!("{observer_path}.miscFactor"),
            0.000_001,
            MAX_SPOTTING_FACTOR,
        )?,
        crew_factor: number(
            required(observer, "crewFactor", &observer_path)?,
            &format!("{observer_path}.crewFactor"),
            0.000_001,
            MAX_SPOTTING_FACTOR,
        )?,
        binocular_factor: number(
            required(observer, "binocularFactor", &observer_path)?,
            &format!("{observer_path}.binocularFactor"),
            1.0,
            MAX_SPOTTING_FACTOR,
        )?,
        has_binoculars,
        binocular_delay_us,
    };

    let target_path = format!("{path}.target");
    let target = object(required(loadout, "target", path)?, &target_path)?;
    exact_fields(
        target,
        &target_path,
        &[
            "moving",
            "stationary",
            "movingAspect",
            "stationaryAspect",
            "hasCamouflageNet",
            "camouflageNetDelayUs",
            "invisibilityFactorAtShot",
        ],
    )?;
    let has_camouflage_net = boolean(
        required(target, "hasCamouflageNet", &target_path)?,
        &format!("{target_path}.hasCamouflageNet"),
    )?;
    let camouflage_net_delay_us = integer(
        required(target, "camouflageNetDelayUs", &target_path)?,
        &format!("{target_path}.camouflageNetDelayUs"),
        0.0,
        MAX_SPOTTING_DELAY_US as f64,
    )?;
    if !has_camouflage_net && camouflage_net_delay_us != 0 {
        return Err(invalid(
            format!("{target_path}.camouflageNetDelayUs"),
            "must be zero when hasCamouflageNet is false",
        ));
    }
    let target = TargetCamouflage {
        moving: number(
            required(target, "moving", &target_path)?,
            &format!("{target_path}.moving"),
            0.0,
            MAX_SPOTTING_FACTOR,
        )?,
        stationary: number(
            required(target, "stationary", &target_path)?,
            &format!("{target_path}.stationary"),
            0.0,
            MAX_SPOTTING_FACTOR,
        )?,
        moving_aspect: parse_spotting_aspect(
            required(target, "movingAspect", &target_path)?,
            &format!("{target_path}.movingAspect"),
        )?,
        stationary_aspect: parse_spotting_aspect(
            required(target, "stationaryAspect", &target_path)?,
            &format!("{target_path}.stationaryAspect"),
        )?,
        has_camouflage_net,
        camouflage_net_delay_us,
        invisibility_factor_at_shot: number(
            required(target, "invisibilityFactorAtShot", &target_path)?,
            &format!("{target_path}.invisibilityFactorAtShot"),
            0.0,
            1.0,
        )?,
        last_fired_at_us: None,
    };
    Ok(DonatedSpottingLoadout::Available(AuthoritySpottingInput {
        observer,
        target,
    }))
}

fn parse_spotting_aspect(value: &Value, path: &str) -> Result<CamouflageAspect, DescriptorError> {
    let aspect = object(value, path)?;
    exact_fields(aspect, path, &["additive", "multiplier"])?;
    Ok(CamouflageAspect {
        additive: number(
            required(aspect, "additive", path)?,
            &format!("{path}.additive"),
            -MAX_SPOTTING_ASPECT_ADDITIVE,
            MAX_SPOTTING_ASPECT_ADDITIVE,
        )?,
        multiplier: number(
            required(aspect, "multiplier", path)?,
            &format!("{path}.multiplier"),
            0.0,
            MAX_SPOTTING_FACTOR,
        )?,
    })
}

fn parse_repair_settings(
    root: &Map<String, Value>,
) -> Result<AuthorityRepairSettings, DescriptorError> {
    let path = "$.repairSettings";
    let settings = object(required(root, "repairSettings", "$")?, path)?;
    exact_fields(settings, path, &["player", "botDefault"])?;
    Ok(AuthorityRepairSettings {
        player: parse_repair_loadout(
            required(settings, "player", path)?,
            &format!("{path}.player"),
        )?,
        bot_default: parse_repair_loadout(
            required(settings, "botDefault", path)?,
            &format!("{path}.botDefault"),
        )?,
    })
}

fn parse_repair_loadout(
    value: &Value,
    path: &str,
) -> Result<DonatedRepairLoadout, DescriptorError> {
    let loadout = object(value, path)?;
    let available = boolean(
        required(loadout, "available", path)?,
        &format!("{path}.available"),
    )?;
    if !available {
        exact_fields(loadout, path, &["available"])?;
        return Ok(DonatedRepairLoadout::Unavailable);
    }
    exact_fields(loadout, path, &["available", "repairFactor", "hasBigKit"])?;
    Ok(DonatedRepairLoadout::Available(RepairInput {
        repair_factor: number(
            required(loadout, "repairFactor", path)?,
            &format!("{path}.repairFactor"),
            0.000_001,
            MAX_REPAIR_FACTOR,
        )?,
        has_big_kit: boolean(
            required(loadout, "hasBigKit", path)?,
            &format!("{path}.hasBigKit"),
        )?,
    }))
}

fn parse_he_structural_armor(
    root: &Map<String, Value>,
) -> Result<Vec<MaterialInfo>, DescriptorError> {
    let hull = object(required(root, "hull", "$")?, "$.hull")?;
    let values = array(
        required(hull, "heStructuralArmor", "$.hull")?,
        "$.hull.heStructuralArmor",
        0,
        128,
    )?;
    values
        .iter()
        .enumerate()
        .map(|(index, value)| {
            Ok(MaterialInfo {
                armor_mm: number(
                    value,
                    &format!("$.hull.heStructuralArmor[{index}]"),
                    0.000_001,
                    2_000.0,
                )?,
                ..MaterialInfo::default()
            })
        })
        .collect()
}

fn parse_hull_extents(root: &Map<String, Value>) -> Result<(f64, f64), DescriptorError> {
    let (minimum, maximum) = parse_component_bbox(root, "hull")?;
    let half_width = minimum[0].abs().max(maximum[0].abs());
    let half_length = minimum[2].abs().max(maximum[2].abs());
    bounded_positive(
        half_width,
        "$.hull.hitTester.bbox",
        0.1,
        MAX_VEHICLE_HALF_EXTENT,
        "hull half width",
    )?;
    bounded_positive(
        half_length,
        "$.hull.hitTester.bbox",
        0.1,
        MAX_VEHICLE_HALF_EXTENT,
        "hull half length",
    )?;
    Ok((half_length, half_width))
}

fn parse_ram_shape(root: &Map<String, Value>) -> Result<RamShape, DescriptorError> {
    let (chassis_minimum, chassis_maximum) = parse_component_bbox(root, "chassis")?;
    let chassis = object(required(root, "chassis", "$")?, "$.chassis")?;
    let hull_position = numeric_vector3(
        required(chassis, "hullPosition", "$.chassis")?,
        "$.chassis.hullPosition",
    )?;
    let (_, hull_maximum) = parse_component_bbox(root, "hull")?;
    let half_width = chassis_minimum[0]
        .abs()
        .max(chassis_maximum[0].abs())
        .max(0.8);
    let half_length = chassis_minimum[2]
        .abs()
        .max(chassis_maximum[2].abs())
        .max(1.0);
    let lower_y = chassis_minimum[1];
    let upper_y = chassis_maximum[1].max(hull_position[1] + hull_maximum[1]);
    RamShape::new(half_width, half_length, lower_y, upper_y)
        .map_err(|error| invalid("$.chassis.hitTester.bbox", error.to_string()))
}

fn parse_component_bbox(
    root: &Map<String, Value>,
    component_name: &str,
) -> Result<([f64; 3], [f64; 3]), DescriptorError> {
    let component_path = format!("$.{component_name}");
    let hit_tester_path = format!("{component_path}.hitTester");
    let bbox_path = format!("{hit_tester_path}.bbox");
    let component = object(required(root, component_name, "$")?, &component_path)?;
    let hit_tester = object(
        required(component, "hitTester", &component_path)?,
        &hit_tester_path,
    )?;
    let bbox = array(
        required(hit_tester, "bbox", &hit_tester_path)?,
        &bbox_path,
        2,
        3,
    )?;
    let minimum = numeric_vector3(&bbox[0], &format!("{bbox_path}[0]"))?;
    let maximum = numeric_vector3(&bbox[1], &format!("{bbox_path}[1]"))?;
    for index in 0..3 {
        if minimum[index] > maximum[index] {
            return Err(invalid(
                format!("{bbox_path}[{index}]"),
                "minimum coordinate exceeds maximum coordinate",
            ));
        }
    }
    if bbox.len() == 3 && !bbox[2].is_null() {
        return Err(invalid(
            format!("{bbox_path}[2]"),
            "derived bbox slot must be null when present",
        ));
    }
    Ok((minimum, maximum))
}

fn parse_player_fire_law(
    root: &Map<String, Value>,
) -> Result<PlayerGunDispersionLaw, DescriptorError> {
    let gun = object(required(root, "gun", "$")?, "$.gun")?;
    let chassis = object(required(root, "chassis", "$")?, "$.chassis")?;
    let gun_factors = object(
        required(gun, "shotDispersionFactors", "$.gun")?,
        "$.gun.shotDispersionFactors",
    )?;
    let chassis_factors = array(
        required(chassis, "shotDispersionFactors", "$.chassis")?,
        "$.chassis.shotDispersionFactors",
        2,
        2,
    )?;

    let after_shot_bloom = number(
        required(gun_factors, "afterShot", "$.gun.shotDispersionFactors")?,
        "$.gun.shotDispersionFactors.afterShot",
        f64::MIN_POSITIVE,
        64.0,
    )?;
    let after_shot_in_burst_bloom = match gun_factors.get("afterShotInBurst") {
        None => after_shot_bloom,
        Some(value) => number(
            value,
            "$.gun.shotDispersionFactors.afterShotInBurst",
            0.0,
            64.0,
        )?,
    };
    let law = PlayerGunDispersionLaw {
        base_dispersion_radians: number(
            required(gun, "shotDispersionAngle", "$.gun")?,
            "$.gun.shotDispersionAngle",
            0.000_001,
            1.0,
        )?,
        aiming_time_seconds: number(
            required(gun, "aimingTime", "$.gun")?,
            "$.gun.aimingTime",
            0.01,
            300.0,
        )?,
        movement_bloom_per_mps: number(
            &chassis_factors[0],
            "$.chassis.shotDispersionFactors[0]",
            0.0,
            64.0,
        )?,
        hull_rotation_bloom_per_rad_s: number(
            &chassis_factors[1],
            "$.chassis.shotDispersionFactors[1]",
            0.0,
            64.0,
        )?,
        turret_rotation_bloom_per_rad_s: number(
            required(gun_factors, "turretRotation", "$.gun.shotDispersionFactors")?,
            "$.gun.shotDispersionFactors.turretRotation",
            0.0,
            64.0,
        )?,
        after_shot_bloom,
        after_shot_in_burst_bloom,
    };
    law.validate()
        .map_err(|error| invalid("$.gun.shotDispersionFactors", error.to_string()))?;
    Ok(law)
}

fn parse_gun(
    root: &Map<String, Value>,
) -> Result<
    (
        GunDescriptor,
        BTreeMap<u8, SourceShot>,
        PhysicalBurstDescriptor,
    ),
    DescriptorError,
> {
    let gun = object(required(root, "gun", "$")?, "$.gun")?;
    let turret = object(required(root, "turret", "$")?, "$.turret")?;

    let reload_seconds = optional_number(gun, "reloadTime", "$.gun", 0.01, 300.0)?.unwrap_or(3.0);
    let clip = match gun.get("clip") {
        None | Some(Value::Null) => None,
        Some(value) => {
            let values = array(value, "$.gun.clip", 2, 2)?;
            let size = integer(&values[0], "$.gun.clip[0]", 1.0, 64.0)? as u16;
            let intra = number(&values[1], "$.gun.clip[1]", 0.0, 300.0)?;
            if size <= 1 {
                // #1513 emits `(1, 0.0)` for ordinary single-shot guns.  The
                // simulation represents that as no autoloader clip.
                None
            } else {
                if intra <= 0.0 {
                    return Err(invalid(
                        "$.gun.clip[1]",
                        "multi-shot clip reload must be positive",
                    ));
                }
                Some(ClipDescriptor {
                    size,
                    intra_reload_seconds: intra,
                })
            }
        }
    };
    let physical_burst = match gun.get("burst") {
        None | Some(Value::Null) => PhysicalBurstDescriptor::new(1, 0.0)
            .expect("the ordinary physical burst descriptor is valid"),
        Some(value) => {
            let values = array(value, "$.gun.burst", 2, 2)?;
            let count = integer(&values[0], "$.gun.burst[0]", 1.0, 64.0)? as u16;
            let interval = number(&values[1], "$.gun.burst[1]", 0.0, 10.0)?;
            PhysicalBurstDescriptor::new(count, interval).map_err(|_| {
                invalid(
                    "$.gun.burst",
                    "multi-projectile burst interval must be positive",
                )
            })?
        }
    };
    let shot_dispersion_angle = number(
        required(gun, "shotDispersionAngle", "$.gun")?,
        "$.gun.shotDispersionAngle",
        f64::MIN_POSITIVE,
        1.0,
    )?;
    let gun_rotation_speed =
        optional_number(gun, "rotationSpeed", "$.gun", 0.000_001, 2.0 * PI)?.unwrap_or(0.35);
    let turret_rotation_speed =
        optional_number(turret, "rotationSpeed", "$.turret", 0.000_001, 2.0 * PI)?.unwrap_or(0.5);
    let pitch_limits = parse_pitch_limits(gun.get("pitchLimits"))?;
    let yaw_limits = parse_yaw_limits(gun.get("turretYawLimits"))?;

    let shot_values = array(
        required(gun, "shots", "$.gun")?,
        "$.gun.shots",
        1,
        MAX_SHOTS,
    )?;
    let mut shells = Vec::with_capacity(shot_values.len());
    let mut mounted_shots = BTreeMap::new();
    for (index, shot_value) in shot_values.iter().enumerate() {
        let shot_path = format!("$.gun.shots[{index}]");
        let shot = object(shot_value, &shot_path)?;
        let shell_path = format!("{shot_path}.shell");
        let shell = object(required(shot, "shell", &shot_path)?, &shell_path)?;
        let kind = text(
            required(shell, "kind", &shell_path)?,
            &format!("{shell_path}.kind"),
            MAX_SHELL_KIND_BYTES,
        )?;
        if !matches!(
            kind,
            "HOLLOW_CHARGE"
                | "HIGH_EXPLOSIVE"
                | "ARMOR_PIERCING"
                | "ARMOR_PIERCING_HE"
                | "ARMOR_PIERCING_CR"
        ) {
            return Err(invalid(
                format!("{shell_path}.kind"),
                format!("unsupported projectile shell kind {kind:?}"),
            ));
        }
        let penetration_value = shot
            .get("piercingPower")
            .or_else(|| shell.get("piercingPower"))
            .ok_or_else(|| invalid(&shot_path, "missing piercingPower"))?;
        let piercing_power = numeric_pair(
            penetration_value,
            &format!("{shot_path}.piercingPower"),
            0.0,
            0.0,
            10_000.0,
            10_000.0,
        )?;
        if piercing_power == [0.0, 0.0] {
            return Err(invalid(
                format!("{shot_path}.piercingPower"),
                "at least one piercing-power endpoint must be positive",
            ));
        }
        let damage_law = numeric_pair(
            required(shell, "damage", &shell_path)?,
            &format!("{shell_path}.damage"),
            0.000_001,
            0.0,
            10_000.0,
            MAX_CRITICAL_DEVICE_HP,
        )?;
        let speed = number(
            required(shot, "speed", &shot_path)?,
            &format!("{shot_path}.speed"),
            0.000_001,
            3_000.0,
        )?;
        let gravity = number(
            required(shot, "gravity", &shot_path)?,
            &format!("{shot_path}.gravity"),
            0.000_001,
            500.0,
        )?;
        let max_distance = number(
            required(shot, "maxDistance", &shot_path)?,
            &format!("{shot_path}.maxDistance"),
            0.000_001,
            10_000.0,
        )?;
        let caliber = number(
            required(shell, "caliber", &shell_path)?,
            &format!("{shell_path}.caliber"),
            0.000_001,
            1_000.0,
        )?;
        // `project_descriptor` predates `project_shot`'s explicit zero and
        // omits this field for ordinary AP shells in a real #1513 descriptor.
        // Both projections canonically mean a zero splash radius.
        let explosion_radius = match shell.get("explosionRadius") {
            Some(value) => number(value, &format!("{shell_path}.explosionRadius"), 0.0, 100.0)?,
            None => 0.0,
        };
        let (
            explosion_damage_factor,
            explosion_damage_absorption_factor,
            explosion_edge_damage_factor,
        ) = if kind == "HIGH_EXPLOSIVE" {
            (
                Some(number(
                    required(shell, "explosionDamageFactor", &shell_path)?,
                    &format!("{shell_path}.explosionDamageFactor"),
                    MIN_HE_FACTOR,
                    MAX_HE_FACTOR,
                )?),
                Some(number(
                    required(shell, "explosionDamageAbsorptionFactor", &shell_path)?,
                    &format!("{shell_path}.explosionDamageAbsorptionFactor"),
                    MIN_HE_FACTOR,
                    MAX_HE_FACTOR,
                )?),
                Some(number(
                    required(shell, "explosionEdgeDamageFactor", &shell_path)?,
                    &format!("{shell_path}.explosionEdgeDamageFactor"),
                    MIN_HE_FACTOR,
                    1.0,
                )?),
            )
        } else {
            for name in [
                "explosionDamageFactor",
                "explosionDamageAbsorptionFactor",
                "explosionEdgeDamageFactor",
            ] {
                if shell.contains_key(name) {
                    return Err(invalid(
                        format!("{shell_path}.{name}"),
                        "HE factor must be absent from a non-HE shell",
                    ));
                }
            }
            (None, None, None)
        };
        let deadeye = match shot.get("deadeye") {
            None => false,
            Some(Value::Bool(value)) => *value,
            Some(_) => {
                return Err(invalid(
                    format!("{shot_path}.deadeye"),
                    "must be a boolean when present",
                ));
            }
        };

        let source_shot = SourceShot {
            speed,
            gravity,
            max_distance,
            piercing_power,
            deadeye,
            shell: SourceShell {
                kind: kind.to_owned(),
                caliber,
                damage: damage_law,
                explosion_radius,
                explosion_damage_factor,
                explosion_damage_absorption_factor,
                explosion_edge_damage_factor,
            },
        };
        debug_assert!(index < MAX_SHOTS && index <= u8::MAX as usize);
        let shell_index = index as u8;
        mounted_shots.insert(shell_index, source_shot);

        // These scalars are intentionally only the planner/simulator's reduced
        // view. The exact projectile and combat law stays in `mounted_shots`.
        let penetration = (piercing_power[0] + piercing_power[1]) / 2.0;
        let damage = (damage_law[0] + damage_law[1]) / 2.0;
        shells.push(ShellDescriptor {
            index,
            kind: kind.to_owned(),
            penetration,
            damage,
            speed,
            gravity,
            max_distance,
        });
    }

    Ok((
        GunDescriptor {
            reload_seconds,
            clip,
            shot_dispersion_angle,
            gun_rotation_speed,
            turret_rotation_speed,
            pitch_limits,
            yaw_limits,
            shells,
        },
        mounted_shots,
        physical_burst,
    ))
}

fn parse_pitch_limits(value: Option<&Value>) -> Result<(f64, f64), DescriptorError> {
    let Some(value) = value else {
        return Ok((-0.35, 0.15));
    };
    if value.is_null() {
        return Ok((-0.35, 0.15));
    }
    let limits = if let Some(values) = value.as_array() {
        pair(values, "$.gun.pitchLimits", -FRAC_PI_2, FRAC_PI_2)?
    } else {
        let object = object(value, "$.gun.pitchLimits")?;
        if let Some(absolute) = object.get("absolute") {
            let values = array(absolute, "$.gun.pitchLimits.absolute", 2, 2)?;
            pair(values, "$.gun.pitchLimits.absolute", -FRAC_PI_2, FRAC_PI_2)?
        } else {
            let minimum = pitch_curve(
                required(object, "minPitch", "$.gun.pitchLimits")?,
                "$.gun.pitchLimits.minPitch",
                true,
            )?;
            let maximum = pitch_curve(
                required(object, "maxPitch", "$.gun.pitchLimits")?,
                "$.gun.pitchLimits.maxPitch",
                false,
            )?;
            (minimum, maximum)
        }
    };
    if limits.0 > limits.1 {
        return Err(invalid(
            "$.gun.pitchLimits",
            "minimum pitch exceeds maximum pitch",
        ));
    }
    Ok(limits)
}

fn pitch_curve(value: &Value, path: &str, take_minimum: bool) -> Result<f64, DescriptorError> {
    let points = array(value, path, 1, 64)?;
    let mut result: Option<f64> = None;
    for (index, point) in points.iter().enumerate() {
        let point_path = format!("{path}[{index}]");
        let pair = array(point, &point_path, 2, 2)?;
        number(&pair[0], &format!("{point_path}[0]"), -2.0 * PI, 2.0 * PI)?;
        let pitch = number(&pair[1], &format!("{point_path}[1]"), -FRAC_PI_2, FRAC_PI_2)?;
        result = Some(match result {
            None => pitch,
            Some(previous) if take_minimum => previous.min(pitch),
            Some(previous) => previous.max(pitch),
        });
    }
    Ok(result.expect("non-empty pitch curve was checked"))
}

fn parse_yaw_limits(value: Option<&Value>) -> Result<GunYawLimits, DescriptorError> {
    let Some(value) = value else {
        return Ok(GunYawLimits::default());
    };
    if value.is_null() {
        return Ok(GunYawLimits::default());
    }
    let values = array(value, "$.gun.turretYawLimits", 2, 2)?;
    let (minimum, maximum) = pair(values, "$.gun.turretYawLimits", -PI, PI)?;
    if minimum > maximum {
        return Err(invalid(
            "$.gun.turretYawLimits",
            "minimum yaw exceeds maximum yaw",
        ));
    }
    Ok(GunYawLimits { minimum, maximum })
}

fn parse_physics(root: &Map<String, Value>) -> Result<PhysicsProfile, DescriptorError> {
    let physics_value = required(root, "physics", "$")?;
    let physics = object(physics_value, "$.physics")?;
    let chassis = object(required(root, "chassis", "$")?, "$.chassis")?;
    let mut profile = PhysicsProfile::default();

    if let Some(value) = optional_number(physics, "weight", "$.physics", 100.0, 500_000.0)? {
        profile.mass = value;
    }
    if let Some(value) =
        optional_number(physics, "enginePower", "$.physics", 1_000.0, 20_000_000.0)?
    {
        profile.power_watts = value;
    }
    if let Some(value) = physics.get("speedLimits") {
        let values = array(value, "$.physics.speedLimits", 2, 2)?;
        profile.forward_speed_limit = number(&values[0], "$.physics.speedLimits[0]", 0.1, 100.0)?;
        profile.reverse_speed_limit = number(&values[1], "$.physics.speedLimits[1]", 0.1, 100.0)?;
    }
    if let Some(value) = physics.get("terrainResistance") {
        profile.terrain_resistance =
            numeric_triplet(value, "$.physics.terrainResistance", 0.01, 100.0)?;
    }
    if let Some(value) = physics.get("rollingFrictionFactors") {
        let factors = numeric_triplet(value, "$.physics.rollingFrictionFactors", 0.01, 100.0)?;
        for (resistance, factor) in profile.terrain_resistance.iter_mut().zip(factors) {
            *resistance *= factor;
            if !resistance.is_finite() || *resistance > 100.0 {
                return Err(invalid(
                    "$.physics.rollingFrictionFactors",
                    "combined terrain resistance exceeds 100",
                ));
            }
        }
    }
    if let Some(value) =
        optional_number(physics, "specificFriction", "$.physics", 0.000_001, 100.0)?
    {
        profile.specific_friction = value;
    }
    if let Some(value) = optional_number(physics, "nativePowerRatio", "$.physics", 0.000_001, 10.0)?
    {
        profile.native_power_ratio = value;
    }

    let raw_rotation = number(
        required(chassis, "rotationSpeed", "$.chassis")?,
        "$.chassis.rotationSpeed",
        0.000_001,
        360.0,
    )?;
    profile.rotation_speed = if raw_rotation > 6.3 {
        raw_rotation.to_radians()
    } else {
        raw_rotation
    };
    if profile.rotation_speed > 2.0 * PI {
        return Err(invalid(
            "$.chassis.rotationSpeed",
            "converted rotation speed exceeds 2*pi radians per second",
        ));
    }

    if let Some(brake_force) =
        optional_number(physics, "brakeForce", "$.physics", 0.0, 1_000_000_000.0)?
    {
        if brake_force > 0.0 {
            profile.brake_deceleration = profile
                .brake_deceleration
                .min(brake_force / profile.mass.max(1.0));
            if !profile.brake_deceleration.is_finite() || profile.brake_deceleration <= 0.0 {
                return Err(invalid(
                    "$.physics.brakeForce",
                    "brake deceleration must be positive and finite",
                ));
            }
        }
    }
    Ok(profile)
}

fn parse_max_ammo(root: &Map<String, Value>) -> Result<u16, DescriptorError> {
    let gun = object(required(root, "gun", "$")?, "$.gun")?;
    let turret = object(required(root, "turret", "$")?, "$.turret")?;
    for (value, path) in [
        (root.get("maxAmmo"), "$.maxAmmo"),
        (gun.get("maxAmmo"), "$.gun.maxAmmo"),
        (turret.get("maxAmmo"), "$.turret.maxAmmo"),
    ] {
        if let Some(value) = value {
            if value.is_null() {
                continue;
            }
            return Ok(integer(value, path, 0.0, MAX_AMMO)? as u16);
        }
    }
    Ok(45)
}

fn parse_modules(root: &Map<String, Value>) -> Result<Vec<String>, DescriptorError> {
    let mut modules = Vec::new();
    if direct_health_pool(root, "engine", "$.engine")? {
        modules.push("engineHealth".to_owned());
    }
    let hull = object(required(root, "hull", "$")?, "$.hull")?;
    if nested_health_pool(hull, "ammoBayHealth", "$.hull.ammoBayHealth")? {
        modules.push("ammoBayHealth".to_owned());
    }
    if direct_health_pool(root, "fuelTank", "$.fuelTank")? {
        modules.push("fuelTankHealth".to_owned());
    }
    if direct_health_pool(root, "radio", "$.radio")? {
        modules.push("radioHealth".to_owned());
    }
    let chassis = object(required(root, "chassis", "$")?, "$.chassis")?;
    if object_has_health_pool(chassis, "$.chassis")? {
        modules.push("leftTrackHealth".to_owned());
        modules.push("rightTrackHealth".to_owned());
    }
    let gun = object(required(root, "gun", "$")?, "$.gun")?;
    if object_has_health_pool(gun, "$.gun")? {
        modules.push("gunHealth".to_owned());
    }
    let turret = object(required(root, "turret", "$")?, "$.turret")?;
    if nested_health_pool(
        turret,
        "turretRotatorHealth",
        "$.turret.turretRotatorHealth",
    )? {
        modules.push("turretRotatorHealth".to_owned());
    }
    if nested_health_pool(
        turret,
        "surveyingDeviceHealth",
        "$.turret.surveyingDeviceHealth",
    )? {
        modules.push("surveyingDeviceHealth".to_owned());
    }
    Ok(modules)
}

fn direct_health_pool(
    root: &Map<String, Value>,
    name: &str,
    path: &str,
) -> Result<bool, DescriptorError> {
    let Some(value) = root.get(name) else {
        return Ok(false);
    };
    let component = object(value, path)?;
    object_has_health_pool(component, path)
}

fn nested_health_pool(
    component: &Map<String, Value>,
    name: &str,
    path: &str,
) -> Result<bool, DescriptorError> {
    let Some(value) = component.get(name) else {
        return Ok(false);
    };
    let pool = object(value, path)?;
    if !object_has_health_pool(pool, path)? {
        return Err(invalid(path, "health pool is missing maxHealth"));
    }
    Ok(true)
}

fn object_has_health_pool(
    component: &Map<String, Value>,
    path: &str,
) -> Result<bool, DescriptorError> {
    let Some(value) = component.get("maxHealth") else {
        return Ok(false);
    };
    integer(
        value,
        &format!("{path}.maxHealth"),
        1.0,
        MAX_CRITICAL_DEVICE_HP,
    )?;
    if let Some(regen) = component.get("maxRegenHealth") {
        integer(
            regen,
            &format!("{path}.maxRegenHealth"),
            0.0,
            MAX_CRITICAL_DEVICE_HP,
        )?;
    }
    Ok(true)
}

fn parse_crew(
    type_info: &Map<String, Value>,
) -> Result<(Vec<String>, Vec<CrewMemberProfile>), DescriptorError> {
    let roles = array(
        required(type_info, "crewRoles", "$.type")?,
        "$.type.crewRoles",
        1,
        MAX_CREW,
    )?;
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    let mut roster = Vec::with_capacity(roles.len());
    let mut profile = Vec::with_capacity(roles.len());
    for (index, raw_roles) in roles.iter().enumerate() {
        let path = format!("$.type.crewRoles[{index}]");
        let member_roles = array(raw_roles, &path, 1, 5)?;
        let mut seen = BTreeSet::new();
        let mut typed_roles = BTreeSet::new();
        let mut main_role = None;
        for (role_index, role) in member_roles.iter().enumerate() {
            let role_path = format!("{path}[{role_index}]");
            let role = text(role, &role_path, 16)?;
            if !matches!(
                role,
                "commander" | "driver" | "gunner" | "loader" | "radioman"
            ) {
                return Err(invalid(
                    role_path,
                    format!("unsupported crew role {role:?}"),
                ));
            }
            if !seen.insert(role) {
                return Err(invalid(role_path, "duplicate role for one crew member"));
            }
            typed_roles.insert(match role {
                "commander" => CrewRole::Commander,
                "driver" => CrewRole::Driver,
                "gunner" => CrewRole::Gunner,
                "loader" => CrewRole::Loader,
                "radioman" => CrewRole::Radioman,
                _ => unreachable!(),
            });
            if main_role.is_none() {
                main_role = Some(role);
            }
        }
        let main_role = main_role.expect("non-empty crew role list was checked");
        let count = counts.entry(main_role).or_default();
        *count += 1;
        let name = match main_role {
            "gunner" | "loader" | "radioman" => {
                if *count > 2 {
                    return Err(invalid(
                        path,
                        format!("more than two {main_role} crew members are unsupported"),
                    ));
                }
                format!("{main_role}{count}")
            }
            "commander" | "driver" => {
                if *count > 1 {
                    return Err(invalid(path, format!("duplicate {main_role} crew member")));
                }
                main_role.to_owned()
            }
            _ => unreachable!(),
        };
        let typed_name = match name.as_str() {
            "commander" => CrewName::Commander,
            "driver" => CrewName::Driver,
            "gunner1" => CrewName::Gunner1,
            "gunner2" => CrewName::Gunner2,
            "loader1" => CrewName::Loader1,
            "loader2" => CrewName::Loader2,
            "radioman1" => CrewName::Radioman1,
            "radioman2" => CrewName::Radioman2,
            _ => unreachable!(),
        };
        roster.push(name);
        profile.push(CrewMemberProfile {
            name: typed_name,
            roles: typed_roles,
        });
    }
    Ok((roster, profile))
}

fn parse_critical_profile(
    root: &Map<String, Value>,
    crew: Vec<CrewMemberProfile>,
) -> Result<CriticalProfile, DescriptorError> {
    let misc = object(required(root, "miscAttrs", "$")?, "$.miscAttrs")?;
    let factor = |name: &str| {
        number(
            required(misc, name, "$.miscAttrs")?,
            &format!("$.miscAttrs.{name}"),
            0.01,
            100.0,
        )
    };
    let repair_speed_factor = factor("repairSpeedFactor")?;
    let ammo_factor = factor("ammoBayHealthFactor")?;
    let engine_factor = factor("engineHealthFactor")?;
    let fuel_factor = factor("fuelTankHealthFactor")?;
    let chassis_factor = factor("chassisHealthFactor")?;

    let engine = object(required(root, "engine", "$")?, "$.engine")?;
    let hull = object(required(root, "hull", "$")?, "$.hull")?;
    let fuel = object(required(root, "fuelTank", "$")?, "$.fuelTank")?;
    let radio = object(required(root, "radio", "$")?, "$.radio")?;
    let chassis = object(required(root, "chassis", "$")?, "$.chassis")?;
    let gun = object(required(root, "gun", "$")?, "$.gun")?;
    let turret = object(required(root, "turret", "$")?, "$.turret")?;

    let mut devices = BTreeMap::new();
    devices.insert(
        DeviceName::EngineHealth,
        critical_health_pool(engine, "$.engine", engine_factor)?,
    );
    devices.insert(
        DeviceName::AmmoBayHealth,
        critical_health_pool(
            object(
                required(hull, "ammoBayHealth", "$.hull")?,
                "$.hull.ammoBayHealth",
            )?,
            "$.hull.ammoBayHealth",
            ammo_factor,
        )?,
    );
    devices.insert(
        DeviceName::FuelTankHealth,
        critical_health_pool(fuel, "$.fuelTank", fuel_factor)?,
    );
    devices.insert(
        DeviceName::RadioHealth,
        critical_health_pool(radio, "$.radio", 1.0)?,
    );
    let track = critical_health_pool(chassis, "$.chassis", chassis_factor)?;
    devices.insert(DeviceName::LeftTrackHealth, track);
    devices.insert(DeviceName::RightTrackHealth, track);
    devices.insert(
        DeviceName::GunHealth,
        critical_health_pool(gun, "$.gun", 1.0)?,
    );
    devices.insert(
        DeviceName::TurretRotatorHealth,
        critical_health_pool(
            object(
                required(turret, "turretRotatorHealth", "$.turret")?,
                "$.turret.turretRotatorHealth",
            )?,
            "$.turret.turretRotatorHealth",
            1.0,
        )?,
    );
    devices.insert(
        DeviceName::SurveyingDeviceHealth,
        critical_health_pool(
            object(
                required(turret, "surveyingDeviceHealth", "$.turret")?,
                "$.turret.surveyingDeviceHealth",
            )?,
            "$.turret.surveyingDeviceHealth",
            1.0,
        )?,
    );
    let engine_fire_starting_chance = number(
        required(engine, "fireStartingChance", "$.engine")?,
        "$.engine.fireStartingChance",
        0.0,
        1.0,
    )?;
    let profile = CriticalProfile {
        devices,
        crew,
        engine_fire_starting_chance,
        repair_speed_factor,
    };
    profile
        .validate()
        .map_err(|error| invalid("$.critical", error.to_string()))?;
    Ok(profile)
}

fn critical_health_pool(
    component: &Map<String, Value>,
    path: &str,
    factor: f64,
) -> Result<DeviceProfile, DescriptorError> {
    let maximum = number(
        required(component, "maxHealth", path)?,
        &format!("{path}.maxHealth"),
        1.0,
        MAX_CRITICAL_DEVICE_HP,
    )?;
    let raw_regen = optional_number(
        component,
        "maxRegenHealth",
        path,
        0.0,
        MAX_CRITICAL_DEVICE_HP,
    )?
    .unwrap_or(0.0);
    let regen = if raw_regen > 0.0 {
        raw_regen
    } else {
        maximum * 0.5
    };
    let maximum = (maximum * factor).round_ties_even();
    let regen = (regen * factor).round_ties_even().clamp(1.0, maximum);
    Ok(DeviceProfile {
        max_hp: maximum,
        regen_hp: regen,
    })
}

fn vehicle_class(tags: &[String]) -> VehicleClass {
    // Preserve ai/planner.py's exact precedence and medium fallback.
    for (tag, class) in [
        ("heavyTank", VehicleClass::HeavyTank),
        ("mediumTank", VehicleClass::MediumTank),
        ("lightTank", VehicleClass::LightTank),
        ("AT-SPG", VehicleClass::TankDestroyer),
        ("SPG", VehicleClass::Spg),
    ] {
        if tags.iter().any(|value| value == tag) {
            return class;
        }
    }
    VehicleClass::MediumTank
}

fn parse_tags(value: &Value, path: &str) -> Result<Vec<String>, DescriptorError> {
    let values = array(value, path, 0, MAX_TAGS)?;
    let mut result = BTreeSet::new();
    for (index, value) in values.iter().enumerate() {
        let item_path = format!("{path}[{index}]");
        let tag = text(value, &item_path, MAX_TAG_BYTES)?;
        if !result.insert(tag.to_owned()) {
            return Err(invalid(item_path, format!("duplicate tag {tag:?}")));
        }
    }
    Ok(result.into_iter().collect())
}

fn validate_tree(
    value: &Value,
    path: &str,
    depth: usize,
    nodes: &mut usize,
) -> Result<(), DescriptorError> {
    if depth > MAX_DESCRIPTOR_DEPTH {
        return Err(invalid(
            path,
            format!("nesting exceeds {MAX_DESCRIPTOR_DEPTH}"),
        ));
    }
    *nodes += 1;
    if *nodes > MAX_DESCRIPTOR_NODES {
        return Err(invalid(
            path,
            format!("descriptor exceeds {MAX_DESCRIPTOR_NODES} JSON nodes"),
        ));
    }
    match value {
        Value::Object(values) => {
            if values.len() > MAX_OBJECT_FIELDS {
                return Err(invalid(
                    path,
                    format!("object exceeds {MAX_OBJECT_FIELDS} fields"),
                ));
            }
            for (name, child) in values {
                if name.len() > MAX_DESCRIPTOR_STRING_BYTES || name.chars().any(char::is_control) {
                    return Err(invalid(
                        path,
                        "object field name is too long or contains controls",
                    ));
                }
                validate_tree(child, &format!("{path}.{name}"), depth + 1, nodes)?;
            }
        }
        Value::Array(values) => {
            if values.len() > MAX_ARRAY_ITEMS {
                return Err(invalid(
                    path,
                    format!("array exceeds {MAX_ARRAY_ITEMS} items"),
                ));
            }
            for (index, child) in values.iter().enumerate() {
                validate_tree(child, &format!("{path}[{index}]"), depth + 1, nodes)?;
            }
        }
        Value::String(value) => {
            if value.len() > MAX_DESCRIPTOR_STRING_BYTES || value.chars().any(char::is_control) {
                return Err(invalid(path, "string is too long or contains controls"));
            }
        }
        Value::Number(value) => {
            if !value.as_f64().is_some_and(f64::is_finite) {
                return Err(invalid(
                    path,
                    "number must be finite and representable as f64",
                ));
            }
        }
        Value::Null | Value::Bool(_) => {}
    }
    Ok(())
}

fn object<'a>(value: &'a Value, path: &str) -> Result<&'a Map<String, Value>, DescriptorError> {
    value
        .as_object()
        .ok_or_else(|| invalid(path, "must be an object"))
}

fn exact_fields(
    object: &Map<String, Value>,
    path: &str,
    expected: &[&str],
) -> Result<(), DescriptorError> {
    for name in expected {
        required(object, name, path)?;
    }
    if let Some(name) = object
        .keys()
        .find(|name| !expected.contains(&name.as_str()))
    {
        return Err(invalid(format!("{path}.{name}"), "unknown field"));
    }
    Ok(())
}

fn required<'a>(
    object: &'a Map<String, Value>,
    name: &str,
    path: &str,
) -> Result<&'a Value, DescriptorError> {
    object
        .get(name)
        .ok_or_else(|| invalid(format!("{path}.{name}"), "required field is missing"))
}

fn boolean(value: &Value, path: &str) -> Result<bool, DescriptorError> {
    value
        .as_bool()
        .ok_or_else(|| invalid(path, "must be a boolean"))
}

fn array<'a>(
    value: &'a Value,
    path: &str,
    minimum: usize,
    maximum: usize,
) -> Result<&'a [Value], DescriptorError> {
    let values = value
        .as_array()
        .ok_or_else(|| invalid(path, "must be an array"))?;
    if !(minimum..=maximum).contains(&values.len()) {
        return Err(invalid(
            path,
            format!(
                "array length must be in {minimum}..={maximum}, got {}",
                values.len()
            ),
        ));
    }
    Ok(values)
}

fn text<'a>(value: &'a Value, path: &str, maximum: usize) -> Result<&'a str, DescriptorError> {
    let value = value
        .as_str()
        .ok_or_else(|| invalid(path, "must be a string"))?;
    validate_text(value, path, maximum)?;
    Ok(value)
}

fn validate_text(value: &str, path: &str, maximum: usize) -> Result<(), DescriptorError> {
    if value.is_empty()
        || value.len() > maximum
        || value.trim() != value
        || value.chars().any(char::is_control)
    {
        return Err(invalid(
            path,
            format!("must be a non-empty trimmed string of at most {maximum} bytes"),
        ));
    }
    Ok(())
}

fn number(value: &Value, path: &str, minimum: f64, maximum: f64) -> Result<f64, DescriptorError> {
    if value.is_boolean() {
        return Err(invalid(path, "boolean is not a number"));
    }
    let number = value
        .as_f64()
        .filter(|number| number.is_finite())
        .ok_or_else(|| invalid(path, "must be a finite number"))?;
    if number < minimum || number > maximum {
        return Err(invalid(
            path,
            format!("must be in {minimum}..={maximum}, got {number}"),
        ));
    }
    Ok(number)
}

fn optional_number(
    object: &Map<String, Value>,
    name: &str,
    path: &str,
    minimum: f64,
    maximum: f64,
) -> Result<Option<f64>, DescriptorError> {
    match object.get(name) {
        None => Ok(None),
        Some(value) => number(value, &format!("{path}.{name}"), minimum, maximum).map(Some),
    }
}

fn integer(value: &Value, path: &str, minimum: f64, maximum: f64) -> Result<u64, DescriptorError> {
    let number = number(value, path, minimum, maximum)?;
    if number.fract() != 0.0 {
        return Err(invalid(path, format!("must be an integer, got {number}")));
    }
    Ok(number as u64)
}

fn numeric_vector3(value: &Value, path: &str) -> Result<[f64; 3], DescriptorError> {
    let values = array(value, path, 3, 3)?;
    Ok([
        number(
            &values[0],
            &format!("{path}[0]"),
            -MAX_BBOX_COORDINATE,
            MAX_BBOX_COORDINATE,
        )?,
        number(
            &values[1],
            &format!("{path}[1]"),
            -MAX_BBOX_COORDINATE,
            MAX_BBOX_COORDINATE,
        )?,
        number(
            &values[2],
            &format!("{path}[2]"),
            -MAX_BBOX_COORDINATE,
            MAX_BBOX_COORDINATE,
        )?,
    ])
}

fn numeric_triplet(
    value: &Value,
    path: &str,
    minimum: f64,
    maximum: f64,
) -> Result<[f64; 3], DescriptorError> {
    let values = array(value, path, 3, 3)?;
    Ok([
        number(&values[0], &format!("{path}[0]"), minimum, maximum)?,
        number(&values[1], &format!("{path}[1]"), minimum, maximum)?,
        number(&values[2], &format!("{path}[2]"), minimum, maximum)?,
    ])
}

fn pair(
    values: &[Value],
    path: &str,
    minimum: f64,
    maximum: f64,
) -> Result<(f64, f64), DescriptorError> {
    Ok((
        number(&values[0], &format!("{path}[0]"), minimum, maximum)?,
        number(&values[1], &format!("{path}[1]"), minimum, maximum)?,
    ))
}

fn numeric_pair(
    value: &Value,
    path: &str,
    first_minimum: f64,
    second_minimum: f64,
    first_maximum: f64,
    second_maximum: f64,
) -> Result<[f64; 2], DescriptorError> {
    let values = array(value, path, 2, 2)?;
    Ok([
        number(
            &values[0],
            &format!("{path}[0]"),
            first_minimum,
            first_maximum,
        )?,
        number(
            &values[1],
            &format!("{path}[1]"),
            second_minimum,
            second_maximum,
        )?,
    ])
}

fn bounded_positive(
    value: f64,
    path: &str,
    minimum: f64,
    maximum: f64,
    label: &str,
) -> Result<(), DescriptorError> {
    if !value.is_finite() || value < minimum || value > maximum {
        return Err(invalid(
            path,
            format!("{label} must be in {minimum}..={maximum}, got {value}"),
        ));
    }
    Ok(())
}

fn invalid(path: impl Into<String>, message: impl Into<String>) -> DescriptorError {
    DescriptorError::InvalidField {
        path: path.into(),
        message: message.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn representative_projection() -> Value {
        json!({
            "name": "ussr:R11_MS-1",
            "level": 1,
            "tags": ["lightTank"],
            "type": {
                "name": "ussr:R11_MS-1",
                "level": 1,
                "tags": ["lightTank"],
                "crewRoles": [
                    ["commander", "gunner", "radioman", "loader"],
                    ["driver"],
                    ["gunner"],
                    ["loader"],
                    ["radioman"]
                ]
            },
            "maxHealth": 1000,
            "maxAmmo": 51.0,
            "gun": {
                "reloadTime": 7.3,
                "clip": [3.0, 0.35],
                "burst": [3, 0.1],
                "shotDispersionAngle": 0.0046,
                "aimingTime": 2.3,
                "shotDispersionFactors": {
                    "turretRotation": 0.3,
                    "afterShot": 1.5,
                    "afterShotInBurst": 0.75
                },
                "rotationSpeed": 0.35,
                "turretYawLimits": [-1.2, 1.1],
                "pitchLimits": {"absolute": [-0.35, 0.15]},
                "maxHealth": 54,
                "shots": [
                    {
                        "speed": 700.0,
                        "gravity": 9.81,
                        "maxDistance": 720.0,
                        "piercingPower": [80.0, 60.0],
                        "shell": {
                            "kind": "ARMOR_PIERCING",
                            "caliber": 45.0,
                            "damage": [110.0, 42.0]
                        }
                    },
                    {
                        "speed": 540.0,
                        "gravity": 143.0,
                        "maxDistance": 650.0,
                        "deadeye": true,
                        "shell": {
                            "kind": "HIGH_EXPLOSIVE",
                            "piercingPower": [35.0, 35.0],
                            "caliber": 122.0,
                            "damage": [175.0, 70.0],
                            "explosionRadius": 1.85,
                            "explosionDamageFactor": 0.5,
                            "explosionDamageAbsorptionFactor": 1.3,
                            "explosionEdgeDamageFactor": 0.15
                        }
                    }
                ]
            },
            "turret": {
                "rotationSpeed": 0.7,
                "turretRotatorHealth": {"maxHealth": 140, "maxRegenHealth": 70},
                "surveyingDeviceHealth": {"maxHealth": 90, "maxRegenHealth": 45}
            },
            "physics": {
                "weight": 8000.0,
                "enginePower": 220000.0,
                "speedLimits": [9.4, 4.0],
                "terrainResistance": [1.1, 1.4, 2.6],
                "rollingFrictionFactors": [1.0, 1.1, 0.9],
                "specificFriction": 0.6867,
                "brakeForce": 64000.0,
                "nativePowerRatio": 1.15
            },
            "chassis": {
                "rotationSpeed": 38.0,
                "shotDispersionFactors": [0.2, 0.4],
                "maxHealth": 170,
                "maxRegenHealth": 130,
                "hullPosition": [0.0, 0.75, 0.0],
                "hitTester": {
                    "bbox": [[-1.55, -0.9, -3.2], [1.45, 0.8, 3.75], null]
                }
            },
            "hull": {
                "hitTester": {"bbox": [[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], null]},
                "heStructuralArmor": [18.0, 16.0],
                "ammoBayHealth": {"maxHealth": 180, "maxRegenHealth": 120}
            },
            "engine": {
                "maxHealth": 105,
                "maxRegenHealth": 52,
                "fireStartingChance": 0.15
            },
            "fuelTank": {"maxHealth": 100, "maxRegenHealth": 50},
            "radio": {"maxHealth": 60, "maxRegenHealth": 30},
            "miscAttrs": {
                "repairSpeedFactor": 1.0,
                "ammoBayHealthFactor": 1.0,
                "engineHealthFactor": 1.0,
                "fuelTankHealthFactor": 1.0,
                "chassisHealthFactor": 1.0
            },
            "repairSettings": {
                "player": {
                    "available": true,
                    "repairFactor": 0.83,
                    "hasBigKit": true
                },
                "botDefault": {
                    "available": true,
                    "repairFactor": 0.57,
                    "hasBigKit": false
                }
            },
            "rammingSettings": {
                "botDefault": {
                    "spall_coefficient": 1.25,
                    "ramming_bonus": 0.0
                }
            },
            "spottingSettings": {
                "player": {
                    "available": true,
                    "observer": {
                        "baseRangeMetres": 400.0,
                        "miscFactor": 1.1,
                        "crewFactor": 1.08,
                        "binocularFactor": 1.25,
                        "hasBinoculars": true,
                        "binocularDelayUs": 3000000
                    },
                    "target": {
                        "moving": 0.12,
                        "stationary": 0.18,
                        "movingAspect": {"additive": 0.0, "multiplier": 1.0},
                        "stationaryAspect": {"additive": 0.1, "multiplier": 1.0},
                        "hasCamouflageNet": true,
                        "camouflageNetDelayUs": 3000000,
                        "invisibilityFactorAtShot": 0.25
                    }
                },
                "botDefault": {
                    "available": true,
                    "observer": {
                        "baseRangeMetres": 400.0,
                        "miscFactor": 1.0,
                        "crewFactor": 0.95,
                        "binocularFactor": 1.0,
                        "hasBinoculars": false,
                        "binocularDelayUs": 0
                    },
                    "target": {
                        "moving": 0.08,
                        "stationary": 0.14,
                        "movingAspect": {"additive": 0.0, "multiplier": 1.0},
                        "stationaryAspect": {"additive": 0.0, "multiplier": 1.0},
                        "hasCamouflageNet": false,
                        "camouflageNetDelayUs": 0,
                        "invisibilityFactorAtShot": 0.25
                    }
                }
            }
        })
    }

    #[test]
    fn representative_1513_projection_maps_every_simulation_surface() {
        let parsed = parse_projection_for("ussr:R11_MS-1", &representative_projection()).unwrap();
        let descriptor = &parsed.descriptor;

        assert_eq!(descriptor.vehicle_key, "ussr:R11_MS-1");
        assert_eq!(parsed.level, 1);
        assert_eq!(parsed.tags, ["lightTank"]);
        assert_eq!(parsed.profile.class, VehicleClass::LightTank);
        assert_eq!(descriptor.max_health, 1_000);
        assert_eq!(descriptor.max_ammo, 51);
        assert_eq!((descriptor.half_length, descriptor.half_width), (3.5, 1.7));
        assert_eq!(
            parsed.ram_shape,
            RamShape::new(1.55, 3.75, -0.9, 2.15).unwrap()
        );
        assert_eq!(descriptor.gun.reload_seconds, 7.3);
        assert_eq!(
            parsed.physical_burst,
            PhysicalBurstDescriptor {
                count: 3,
                interval_seconds: 0.1,
            }
        );
        assert_eq!(
            descriptor.gun.clip,
            Some(ClipDescriptor {
                size: 3,
                intra_reload_seconds: 0.35
            })
        );
        assert_eq!(descriptor.gun.shot_dispersion_angle, 0.0046);
        assert_eq!(
            parsed.player_fire_law,
            PlayerGunDispersionLaw {
                base_dispersion_radians: 0.0046,
                aiming_time_seconds: 2.3,
                movement_bloom_per_mps: 0.2,
                hull_rotation_bloom_per_rad_s: 0.4,
                turret_rotation_bloom_per_rad_s: 0.3,
                after_shot_bloom: 1.5,
                after_shot_in_burst_bloom: 0.75,
            }
        );
        assert_eq!(descriptor.gun.pitch_limits, (-0.35, 0.15));
        assert_eq!(
            descriptor.gun.yaw_limits,
            GunYawLimits {
                minimum: -1.2,
                maximum: 1.1
            }
        );
        assert_eq!(descriptor.gun.shells.len(), 2);
        assert_eq!(descriptor.gun.shells[0].penetration, 70.0);
        assert_eq!(descriptor.gun.shells[0].damage, 76.0);
        assert_eq!(descriptor.gun.shells[1].gravity, 143.0);
        assert_eq!(parsed.profile.shells[1].kind, "HIGH_EXPLOSIVE");
        assert_eq!(parsed.mounted_shots.len(), 2);
        assert_eq!(
            parsed.mounted_shot(0),
            Some(&SourceShot {
                speed: 700.0,
                gravity: 9.81,
                max_distance: 720.0,
                piercing_power: [80.0, 60.0],
                deadeye: false,
                shell: SourceShell {
                    kind: "ARMOR_PIERCING".to_owned(),
                    caliber: 45.0,
                    damage: [110.0, 42.0],
                    explosion_radius: 0.0,
                    explosion_damage_factor: None,
                    explosion_damage_absorption_factor: None,
                    explosion_edge_damage_factor: None,
                },
            })
        );
        assert_eq!(
            parsed.mounted_shot(1),
            Some(&SourceShot {
                speed: 540.0,
                gravity: 143.0,
                max_distance: 650.0,
                piercing_power: [35.0, 35.0],
                deadeye: true,
                shell: SourceShell {
                    kind: "HIGH_EXPLOSIVE".to_owned(),
                    caliber: 122.0,
                    damage: [175.0, 70.0],
                    explosion_radius: 1.85,
                    explosion_damage_factor: Some(0.5),
                    explosion_damage_absorption_factor: Some(1.3),
                    explosion_edge_damage_factor: Some(0.15),
                },
            })
        );
        assert_eq!(parsed.mounted_shot(2), None);
        assert_eq!(parsed.bot_ramming_profile.spall_coefficient(), 1.25);
        assert_eq!(parsed.bot_ramming_profile.controlled_impact_bonus(), 0.0);
        assert_eq!(parsed.hull_materials.len(), 2);
        assert_eq!(parsed.hull_materials[0].armor_mm, 18.0);
        assert_eq!(parsed.hull_materials[1].armor_mm, 16.0);
        assert_eq!(
            parsed.repair_settings.player.input(),
            Some(RepairInput {
                repair_factor: 0.83,
                has_big_kit: true,
            })
        );
        assert_eq!(
            parsed.repair_settings.bot_default.input(),
            Some(RepairInput {
                repair_factor: 0.57,
                has_big_kit: false,
            })
        );
        assert_eq!(
            parsed.spotting_settings.player.input(),
            Some(AuthoritySpottingInput {
                observer: ObserverView {
                    base_range_metres: 400.0,
                    misc_factor: 1.1,
                    crew_factor: 1.08,
                    binocular_factor: 1.25,
                    has_binoculars: true,
                    binocular_delay_us: 3_000_000,
                },
                target: TargetCamouflage {
                    moving: 0.12,
                    stationary: 0.18,
                    moving_aspect: CamouflageAspect::default(),
                    stationary_aspect: CamouflageAspect {
                        additive: 0.1,
                        multiplier: 1.0,
                    },
                    has_camouflage_net: true,
                    camouflage_net_delay_us: 3_000_000,
                    invisibility_factor_at_shot: 0.25,
                    last_fired_at_us: None,
                },
            })
        );
        assert_eq!(
            parsed
                .spotting_settings
                .bot_default
                .input()
                .unwrap()
                .observer,
            ObserverView {
                base_range_metres: 400.0,
                misc_factor: 1.0,
                crew_factor: 0.95,
                binocular_factor: 1.0,
                has_binoculars: false,
                binocular_delay_us: 0,
            }
        );

        assert_eq!(descriptor.physics.mass, 8_000.0);
        assert_eq!(descriptor.physics.power_watts, 220_000.0);
        assert_eq!(descriptor.physics.forward_speed_limit, 9.4);
        assert_eq!(descriptor.physics.reverse_speed_limit, 4.0);
        for (actual, expected) in descriptor
            .physics
            .terrain_resistance
            .iter()
            .zip([1.1, 1.54, 2.34])
        {
            assert!((*actual - expected).abs() < 1e-12);
        }
        assert!((descriptor.physics.rotation_speed - 38.0_f64.to_radians()).abs() < 1e-12);
        assert_eq!(descriptor.physics.brake_deceleration, 8.0);
        assert_eq!(descriptor.physics.native_power_ratio, 1.15);

        assert_eq!(
            descriptor.module_names,
            [
                "engineHealth",
                "ammoBayHealth",
                "fuelTankHealth",
                "radioHealth",
                "leftTrackHealth",
                "rightTrackHealth",
                "gunHealth",
                "turretRotatorHealth",
                "surveyingDeviceHealth"
            ]
        );
        assert_eq!(
            descriptor.crew_roster,
            ["commander", "driver", "gunner1", "loader1", "radioman1"]
        );
    }

    #[test]
    fn trusted_large_module_values_survive_descriptor_parsing() {
        let mut value = representative_projection();
        value["gun"]["maxHealth"] = json!(MAX_CRITICAL_DEVICE_HP);
        value["gun"]["shots"][0]["shell"]["damage"][1] = json!(MAX_CRITICAL_DEVICE_HP);

        let parsed = parse_projection(&value).unwrap();
        assert_eq!(
            parsed.critical_profile.devices[&DeviceName::GunHealth].max_hp,
            MAX_CRITICAL_DEVICE_HP
        );
        assert_eq!(
            parsed.mounted_shot(0).unwrap().shell.damage[1],
            MAX_CRITICAL_DEVICE_HP
        );

        let mut oversized_module = value.clone();
        oversized_module["gun"]["maxHealth"] = json!(MAX_CRITICAL_DEVICE_HP + 1.0);
        assert!(parse_projection(&oversized_module)
            .unwrap_err()
            .to_string()
            .contains("$.gun.maxHealth"));

        let mut oversized_damage = value;
        oversized_damage["gun"]["shots"][0]["shell"]["damage"][1] =
            json!(MAX_CRITICAL_DEVICE_HP + 1.0);
        assert!(parse_projection(&oversized_damage)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].shell.damage[1]"));
    }

    #[test]
    fn ordinary_clip_and_projection_defaults_match_python_runtime() {
        let mut value = representative_projection();
        let root = value.as_object_mut().unwrap();
        root.remove("maxAmmo");
        let gun = root.get_mut("gun").unwrap().as_object_mut().unwrap();
        gun.insert("clip".to_owned(), json!([1.0, 0.0]));
        gun.remove("burst");
        gun.remove("reloadTime");
        gun.remove("rotationSpeed");
        gun.remove("turretYawLimits");
        gun.insert(
            "pitchLimits".to_owned(),
            json!({
                "minPitch": [[-1.0, -0.25], [1.0, -0.4]],
                "maxPitch": [[-1.0, 0.1], [1.0, 0.2]]
            }),
        );
        let turret = root.get_mut("turret").unwrap().as_object_mut().unwrap();
        turret.remove("rotationSpeed");
        root.insert("physics".to_owned(), json!({}));
        root.get_mut("chassis")
            .unwrap()
            .as_object_mut()
            .unwrap()
            .insert("rotationSpeed".to_owned(), json!(0.66));

        let parsed = parse_projection(&value).unwrap();
        assert_eq!(parsed.descriptor.max_ammo, 45);
        assert_eq!(parsed.descriptor.gun.clip, None);
        assert_eq!(
            parsed.physical_burst,
            PhysicalBurstDescriptor {
                count: 1,
                interval_seconds: 0.0,
            }
        );
        assert_eq!(parsed.descriptor.gun.reload_seconds, 3.0);
        assert_eq!(parsed.descriptor.gun.gun_rotation_speed, 0.35);
        assert_eq!(parsed.descriptor.gun.turret_rotation_speed, 0.5);
        assert_eq!(parsed.descriptor.gun.pitch_limits, (-0.4, 0.2));
        assert_eq!(parsed.descriptor.gun.yaw_limits, GunYawLimits::default());
        assert_eq!(
            parsed.descriptor.physics.mass,
            PhysicsProfile::default().mass
        );
    }

    #[test]
    fn physical_burst_descriptor_is_strict_and_transactional() {
        let mut zero_interval = representative_projection();
        zero_interval["gun"]["burst"] = json!([3, 0.0]);
        assert!(parse_projection(&zero_interval)
            .unwrap_err()
            .to_string()
            .contains("$.gun.burst"));

        let mut fractional_count = representative_projection();
        fractional_count["gun"]["burst"] = json!([2.5, 0.1]);
        assert!(parse_projection(&fractional_count)
            .unwrap_err()
            .to_string()
            .contains("$.gun.burst[0]"));
    }

    #[test]
    fn player_fire_law_uses_the_intra_burst_fallback_only_when_absent() {
        let mut value = representative_projection();
        value["gun"]["shotDispersionFactors"]
            .as_object_mut()
            .unwrap()
            .remove("afterShotInBurst");

        let law = parse_projection(&value).unwrap().player_fire_law;
        assert_eq!(law.after_shot_bloom, 1.5);
        assert_eq!(law.after_shot_in_burst_bloom, 1.5);

        value["gun"]["shotDispersionFactors"]["afterShotInBurst"] = json!(0.0);
        assert_eq!(
            parse_projection(&value)
                .unwrap()
                .player_fire_law
                .after_shot_in_burst_bloom,
            0.0
        );
    }

    #[test]
    fn player_fire_law_rejects_missing_non_finite_and_out_of_range_inputs() {
        let mut missing = representative_projection();
        missing["gun"].as_object_mut().unwrap().remove("aimingTime");
        assert!(parse_projection(&missing)
            .unwrap_err()
            .to_string()
            .contains("$.gun.aimingTime"));

        let mut missing_chassis = representative_projection();
        missing_chassis["chassis"]
            .as_object_mut()
            .unwrap()
            .remove("shotDispersionFactors");
        assert!(parse_projection(&missing_chassis)
            .unwrap_err()
            .to_string()
            .contains("$.chassis.shotDispersionFactors"));

        let mut zero_final_bloom = representative_projection();
        zero_final_bloom["gun"]["shotDispersionFactors"]["afterShot"] = json!(0.0);
        assert!(parse_projection(&zero_final_bloom)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shotDispersionFactors.afterShot"));

        let mut out_of_range = representative_projection();
        out_of_range["chassis"]["shotDispersionFactors"][1] = json!(64.000_001);
        assert!(parse_projection(&out_of_range)
            .unwrap_err()
            .to_string()
            .contains("$.chassis.shotDispersionFactors[1]"));

        let mut nan_marker = representative_projection();
        nan_marker["gun"]["shotDispersionFactors"]["turretRotation"] = json!("NaN");
        assert!(parse_projection(&nan_marker)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shotDispersionFactors.turretRotation"));

        assert!(matches!(
            parse_projection_json(r#"{"gun":{"aimingTime":NaN}}"#),
            Err(DescriptorError::InvalidJson(_))
        ));
    }

    #[test]
    fn class_precedence_matches_the_python_planner() {
        let mut value = representative_projection();
        let root = value.as_object_mut().unwrap();
        root.insert("tags".to_owned(), json!(["SPG", "heavyTank"]));
        root.get_mut("type")
            .unwrap()
            .as_object_mut()
            .unwrap()
            .insert("tags".to_owned(), json!(["SPG", "heavyTank"]));

        assert_eq!(
            parse_projection(&value).unwrap().profile.class,
            VehicleClass::HeavyTank
        );
    }

    #[test]
    fn bundle_key_and_nested_identity_mismatches_are_diagnostic() {
        let error = parse_projection_for("germany:G12_Ltraktor", &representative_projection())
            .unwrap_err()
            .to_string();
        assert!(error.contains("$.name"));
        assert!(error.contains("bundle key"));

        let mut value = representative_projection();
        value["type"]["name"] = json!("ussr:R04_T-34");
        let error = parse_projection(&value).unwrap_err().to_string();
        assert!(error.contains("$.type.name"));
        assert!(error.contains("does not match"));
    }

    #[test]
    fn malformed_bbox_ballistics_and_crew_fail_closed_at_the_field_path() {
        let mut bbox = representative_projection();
        bbox["hull"]["hitTester"]["bbox"] = json!([[1.0, -0.2, -3.5], [-1.0, 1.4, 3.5], null]);
        assert!(parse_projection(&bbox)
            .unwrap_err()
            .to_string()
            .contains("$.hull.hitTester.bbox"));

        let mut ballistics = representative_projection();
        ballistics["gun"]["shots"][0]["speed"] = json!(0.0);
        assert!(parse_projection(&ballistics)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].speed"));

        let mut gravity = representative_projection();
        gravity["gun"]["shots"][0]["gravity"] = json!(-9.81);
        assert!(parse_projection(&gravity)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].gravity"));

        let mut crew = representative_projection();
        crew["type"]["crewRoles"][0][0] = json!("wizard");
        assert!(parse_projection(&crew)
            .unwrap_err()
            .to_string()
            .contains("$.type.crewRoles[0][0]"));
    }

    #[test]
    fn ramming_shape_requires_exact_chassis_and_hull_geometry() {
        for path in ["hitTester", "hullPosition"] {
            let mut missing = representative_projection();
            missing["chassis"].as_object_mut().unwrap().remove(path);
            assert!(parse_projection(&missing)
                .unwrap_err()
                .to_string()
                .contains(&format!("$.chassis.{path}")));
        }

        let mut reversed = representative_projection();
        reversed["chassis"]["hitTester"]["bbox"] =
            json!([[-1.55, 0.9, -3.2], [1.45, 0.8, 3.75], null]);
        assert!(parse_projection(&reversed)
            .unwrap_err()
            .to_string()
            .contains("$.chassis.hitTester.bbox"));

        let mut invalid_vertical_span = representative_projection();
        invalid_vertical_span["chassis"]["hitTester"]["bbox"] =
            json!([[-1.55, 2.0, -3.2], [1.45, 2.0, 3.75], null]);
        invalid_vertical_span["chassis"]["hullPosition"] = json!([0.0, 0.0, 0.0]);
        invalid_vertical_span["hull"]["hitTester"]["bbox"] =
            json!([[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], null]);
        assert!(parse_projection(&invalid_vertical_span)
            .unwrap_err()
            .to_string()
            .contains("$.chassis.hitTester.bbox"));
    }

    #[test]
    fn mounted_shot_exact_law_rejects_lossy_or_malformed_shapes() {
        for (path, value) in [
            ("piercingPower", json!([80.0])),
            ("piercingPower", json!([80.0, 60.0, 40.0])),
        ] {
            let mut projection = representative_projection();
            projection["gun"]["shots"][0][path] = value;
            let error = parse_projection(&projection).unwrap_err().to_string();
            assert!(error.contains("$.gun.shots[0].piercingPower"));
        }

        for damage in [json!([110.0]), json!([110.0, 42.0, 7.0])] {
            let mut projection = representative_projection();
            projection["gun"]["shots"][0]["shell"]["damage"] = damage;
            let error = parse_projection(&projection).unwrap_err().to_string();
            assert!(error.contains("$.gun.shots[0].shell.damage"));
        }

        let mut missing_caliber = representative_projection();
        missing_caliber["gun"]["shots"][0]["shell"]
            .as_object_mut()
            .unwrap()
            .remove("caliber");
        assert!(parse_projection(&missing_caliber)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].shell.caliber"));

        let mut unsupported_kind = representative_projection();
        unsupported_kind["gun"]["shots"][0]["shell"]["kind"] = json!("SMOKE");
        assert!(parse_projection(&unsupported_kind)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].shell.kind"));

        let mut malformed_deadeye = representative_projection();
        malformed_deadeye["gun"]["shots"][0]["deadeye"] = json!(1);
        assert!(parse_projection(&malformed_deadeye)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[0].deadeye"));
    }

    #[test]
    fn mounted_he_shot_preserves_descriptor_default_and_override_factors() {
        let defaults = parse_projection(&representative_projection()).unwrap();
        assert_eq!(
            defaults.mounted_shot(1).unwrap().shell.he_tuning(),
            Ok(Some(crate::combat_rules::HeTuning::default()))
        );

        let mut projection = representative_projection();
        let shell = &mut projection["gun"]["shots"][1]["shell"];
        shell["explosionDamageFactor"] = json!(0.6);
        shell["explosionDamageAbsorptionFactor"] = json!(1.0);
        shell["explosionEdgeDamageFactor"] = json!(0.2);
        let parsed = parse_projection(&projection).unwrap();
        assert_eq!(
            parsed.mounted_shot(1).unwrap().shell.he_tuning(),
            Ok(Some(
                crate::combat_rules::HeTuning::new(0.6, 1.0, 0.2).unwrap()
            ))
        );
    }

    #[test]
    fn mounted_he_shot_rejects_partial_and_malformed_factor_sets() {
        let mut partial = representative_projection();
        partial["gun"]["shots"][1]["shell"]
            .as_object_mut()
            .unwrap()
            .remove("explosionEdgeDamageFactor");
        assert!(parse_projection(&partial)
            .unwrap_err()
            .to_string()
            .contains("$.gun.shots[1].shell.explosionEdgeDamageFactor"));

        for (name, value) in [
            ("explosionDamageFactor", json!("0.5")),
            ("explosionDamageAbsorptionFactor", json!(0.0)),
            ("explosionEdgeDamageFactor", json!(1.000_001)),
        ] {
            let mut malformed = representative_projection();
            malformed["gun"]["shots"][1]["shell"][name] = value;
            let error = parse_projection(&malformed).unwrap_err().to_string();
            assert!(error.contains(&format!("$.gun.shots[1].shell.{name}")));
        }
    }

    #[test]
    fn mounted_non_he_shot_rejects_he_only_factor_fields() {
        for name in [
            "explosionDamageFactor",
            "explosionDamageAbsorptionFactor",
            "explosionEdgeDamageFactor",
        ] {
            let mut projection = representative_projection();
            projection["gun"]["shots"][0]["shell"][name] = json!(0.5);
            let error = parse_projection(&projection).unwrap_err().to_string();
            assert!(error.contains(&format!("$.gun.shots[0].shell.{name}")));
            assert!(error.contains("non-HE"));
        }
    }

    #[test]
    fn projection_size_and_collection_limits_are_enforced() {
        let oversized = " ".repeat(MAX_DESCRIPTOR_JSON_BYTES + 1);
        assert!(matches!(
            parse_projection_json(&oversized),
            Err(DescriptorError::JsonTooLarge { .. })
        ));

        let mut value = representative_projection();
        let shot = value["gun"]["shots"][0].clone();
        value["gun"]["shots"] = Value::Array(vec![shot; MAX_SHOTS + 1]);
        let error = parse_projection(&value).unwrap_err().to_string();
        assert!(error.contains("$.gun.shots"));
        assert!(error.contains("array length"));
    }

    #[test]
    fn invalid_present_physics_values_do_not_fall_back_silently() {
        let mut value = representative_projection();
        value["physics"]["nativePowerRatio"] = json!(0.0);
        let error = parse_projection(&value).unwrap_err().to_string();
        assert!(error.contains("$.physics.nativePowerRatio"));

        let mut value = representative_projection();
        value["chassis"]["rotationSpeed"] = json!(null);
        let error = parse_projection(&value).unwrap_err().to_string();
        assert!(error.contains("$.chassis.rotationSpeed"));
    }

    #[test]
    fn bot_ramming_profile_is_required_exact_and_bounded() {
        let mut missing = representative_projection();
        missing.as_object_mut().unwrap().remove("rammingSettings");
        assert!(parse_projection(&missing)
            .unwrap_err()
            .to_string()
            .contains("$.rammingSettings"));

        for (name, value) in [
            ("spall_coefficient", json!(0.999_999)),
            ("spall_coefficient", json!(1.500_001)),
            ("spall_coefficient", json!(true)),
            ("ramming_bonus", json!(-0.000_001)),
            ("ramming_bonus", json!(0.150_001)),
        ] {
            let mut malformed = representative_projection();
            malformed["rammingSettings"]["botDefault"][name] = value;
            assert!(parse_projection(&malformed)
                .unwrap_err()
                .to_string()
                .contains(&format!("$.rammingSettings.botDefault.{name}")));
        }

        let mut unknown = representative_projection();
        unknown["rammingSettings"]["botDefault"]["estimated"] = json!(true);
        let error = parse_projection(&unknown).unwrap_err().to_string();
        assert!(error.contains("$.rammingSettings.botDefault.estimated"));
        assert!(error.contains("unknown field"));
    }

    #[test]
    fn unavailable_repair_inputs_remain_typed_and_fail_closed() {
        let mut value = representative_projection();
        value["repairSettings"]["player"] = json!({"available": false});

        let parsed = parse_projection(&value).unwrap();

        assert_eq!(
            parsed.repair_settings.player,
            DonatedRepairLoadout::Unavailable
        );
        assert_eq!(parsed.repair_settings.player.input(), None);
        assert!(parsed.repair_settings.bot_default.input().is_some());
    }

    #[test]
    fn repair_settings_reject_missing_out_of_range_or_mistyped_inputs() {
        let mut missing = representative_projection();
        missing.as_object_mut().unwrap().remove("repairSettings");
        assert!(parse_projection(&missing)
            .unwrap_err()
            .to_string()
            .contains("$.repairSettings"));

        for value in [json!(0.0), json!(100.000_001), json!(true)] {
            let mut malformed = representative_projection();
            malformed["repairSettings"]["player"]["repairFactor"] = value;
            assert!(parse_projection(&malformed)
                .unwrap_err()
                .to_string()
                .contains("$.repairSettings.player.repairFactor"));
        }

        let mut malformed_kit = representative_projection();
        malformed_kit["repairSettings"]["botDefault"]["hasBigKit"] = json!(0);
        assert!(parse_projection(&malformed_kit)
            .unwrap_err()
            .to_string()
            .contains("$.repairSettings.botDefault.hasBigKit"));
    }

    #[test]
    fn repair_settings_reject_unknown_or_unavailable_payload_fields() {
        let mut outer_unknown = representative_projection();
        outer_unknown["repairSettings"]["future"] = json!({"available": false});
        let error = parse_projection(&outer_unknown).unwrap_err().to_string();
        assert!(error.contains("$.repairSettings.future"));
        assert!(error.contains("unknown field"));

        let mut inner_unknown = representative_projection();
        inner_unknown["repairSettings"]["player"]["repairSkill"] = json!(100);
        let error = parse_projection(&inner_unknown).unwrap_err().to_string();
        assert!(error.contains("$.repairSettings.player.repairSkill"));
        assert!(error.contains("unknown field"));

        let mut unavailable_payload = representative_projection();
        unavailable_payload["repairSettings"]["player"] = json!({
            "available": false,
            "repairFactor": 0.83,
            "hasBigKit": true
        });
        assert!(parse_projection(&unavailable_payload)
            .unwrap_err()
            .to_string()
            .contains("$.repairSettings.player"));
    }

    #[test]
    fn spotting_settings_round_trip_into_typed_law_inputs() {
        let encoded = serde_json::to_string(&representative_projection()).unwrap();
        let parsed = parse_projection_json(&encoded).unwrap();

        let player = parsed.spotting_settings.player.input().unwrap();
        assert_eq!(player.observer.binocular_delay_us, 3_000_000);
        assert_eq!(player.target.camouflage_net_delay_us, 3_000_000);
        assert_eq!(player.target.last_fired_at_us, None);
        assert_eq!(player.target.stationary_aspect.additive, 0.1);
        assert!(parsed.spotting_settings.bot_default.input().is_some());
    }

    #[test]
    fn unavailable_spotting_inputs_remain_typed_and_fail_closed() {
        let mut value = representative_projection();
        value["spottingSettings"]["player"] = json!({"available": false});

        let parsed = parse_projection(&value).unwrap();

        assert_eq!(
            parsed.spotting_settings.player,
            DonatedSpottingLoadout::Unavailable
        );
        assert_eq!(parsed.spotting_settings.player.input(), None);
        assert!(parsed.spotting_settings.bot_default.input().is_some());
    }

    #[test]
    fn player_authority_loadout_is_strict_actor_scoped_input() {
        let projection = representative_projection();
        let loadout = json!({
            "repair": projection["repairSettings"]["player"].clone(),
            "spotting": projection["spottingSettings"]["player"].clone(),
        });

        let parsed = parse_player_authority_loadout(&loadout).unwrap();

        assert_eq!(
            parsed.repair.input(),
            Some(RepairInput {
                repair_factor: 0.83,
                has_big_kit: true,
            })
        );
        assert_eq!(
            parsed.spotting.input().unwrap().observer.base_range_metres,
            400.0
        );

        let unavailable = parse_player_authority_loadout(&json!({
            "repair": {"available": false},
            "spotting": {"available": false},
        }))
        .unwrap();
        assert_eq!(unavailable.repair.input(), None);
        assert_eq!(unavailable.spotting.input(), None);
    }

    #[test]
    fn player_authority_loadout_rejects_unknown_or_partial_inputs() {
        for malformed in [
            json!({"repair": {"available": false}}),
            json!({
                "repair": {"available": false, "borrowDonor": true},
                "spotting": {"available": false},
            }),
            json!({
                "repair": {"available": false},
                "spotting": {"available": false},
                "descriptorDonor": 1,
            }),
        ] {
            assert!(parse_player_authority_loadout(&malformed).is_err());
        }
    }

    #[test]
    fn spotting_settings_reject_missing_mistyped_or_out_of_range_inputs() {
        let mut missing = representative_projection();
        missing.as_object_mut().unwrap().remove("spottingSettings");
        assert!(parse_projection(&missing)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings"));

        for value in [json!(0.0), json!(1_000.000_001), json!(true)] {
            let mut malformed = representative_projection();
            malformed["spottingSettings"]["player"]["observer"]["baseRangeMetres"] = value;
            assert!(parse_projection(&malformed)
                .unwrap_err()
                .to_string()
                .contains("$.spottingSettings.player.observer.baseRangeMetres"));
        }

        let mut fractional_delay = representative_projection();
        fractional_delay["spottingSettings"]["player"]["observer"]["binocularDelayUs"] =
            json!(3_000_000.5);
        assert!(parse_projection(&fractional_delay)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings.player.observer.binocularDelayUs"));

        let mut impossible_absent_device = representative_projection();
        impossible_absent_device["spottingSettings"]["botDefault"]["observer"]
            ["binocularDelayUs"] = json!(1);
        assert!(parse_projection(&impossible_absent_device)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings.botDefault.observer.binocularDelayUs"));

        let mut invalid_aspect = representative_projection();
        invalid_aspect["spottingSettings"]["player"]["target"]["movingAspect"]["multiplier"] =
            json!(-0.001);
        assert!(parse_projection(&invalid_aspect)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings.player.target.movingAspect.multiplier"));

        let mut invalid_shot_factor = representative_projection();
        invalid_shot_factor["spottingSettings"]["player"]["target"]["invisibilityFactorAtShot"] =
            json!(1.000_001);
        assert!(parse_projection(&invalid_shot_factor)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings.player.target.invisibilityFactorAtShot"));
    }

    #[test]
    fn spotting_settings_deny_unknown_and_unavailable_payload_fields() {
        let mut outer_unknown = representative_projection();
        outer_unknown["spottingSettings"]["future"] = json!({"available": false});
        let error = parse_projection(&outer_unknown).unwrap_err().to_string();
        assert!(error.contains("$.spottingSettings.future"));
        assert!(error.contains("unknown field"));

        let mut observer_unknown = representative_projection();
        observer_unknown["spottingSettings"]["player"]["observer"]["estimated"] = json!(true);
        let error = parse_projection(&observer_unknown).unwrap_err().to_string();
        assert!(error.contains("$.spottingSettings.player.observer.estimated"));
        assert!(error.contains("unknown field"));

        let mut aspect_unknown = representative_projection();
        aspect_unknown["spottingSettings"]["player"]["target"]["movingAspect"]["source"] =
            json!("guess");
        let error = parse_projection(&aspect_unknown).unwrap_err().to_string();
        assert!(error.contains("$.spottingSettings.player.target.movingAspect.source"));
        assert!(error.contains("unknown field"));

        let mut unavailable_payload = representative_projection();
        unavailable_payload["spottingSettings"]["player"] = json!({
            "available": false,
            "observer": {}
        });
        assert!(parse_projection(&unavailable_payload)
            .unwrap_err()
            .to_string()
            .contains("$.spottingSettings.player"));
    }
}
