//! Deterministic port of the pinned #1513 armour and high-explosive laws.
//!
//! Native code supplies ordered collision layers. The Rust authority owns the
//! shell's two random factors and passes them in explicitly; this module never
//! samples process-global random state.

use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::BTreeSet;
use thiserror::Error;

pub const RANGE_NEAR_M: f64 = 100.0;
pub const RANGE_FAR_M: f64 = 500.0;
pub const RANGE_SPAN_M: f64 = RANGE_FAR_M - RANGE_NEAR_M;
pub const DEFAULT_HE_SPLASH_FRACTION: f64 = 0.5;
pub const DEFAULT_HE_ARMOR_FACTOR: f64 = 1.3;
pub const DEFAULT_HE_EDGE_FACTOR: f64 = 0.15;
pub const MIN_HE_FACTOR: f64 = 0.000_001;
pub const MAX_HE_FACTOR: f64 = 10_000.0;
pub const MIN_SHOT_FACTOR: f64 = 0.75;
pub const MAX_SHOT_FACTOR: f64 = 1.25;

const MIN_ANGLE_COS: f64 = 0.0001;
const AP_RICOCHET_DEGREES: f64 = 70.0;
const HEAT_RICOCHET_DEGREES: f64 = 85.0;
const HE_RADIUS_CALIBER_DIVISOR: f64 = 5555.0;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ShellKind {
    ArmorPiercing,
    ArmorPiercingCr,
    ArmorPiercingHe,
    HollowCharge,
    HighExplosive,
}

impl ShellKind {
    pub fn is_he(self) -> bool {
        self == Self::HighExplosive
    }

    fn is_normalizing_ap(self) -> bool {
        matches!(self, Self::ArmorPiercing | Self::ArmorPiercingCr)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ShotInfo {
    pub kind: ShellKind,
    pub caliber_mm: f64,
    /// Exact #1513 `(vehicle HP, module HP)` damage tuple. Hull damage always
    /// uses element zero.
    pub damage: [f64; 2],
    pub explosion_radius_m: f64,
    /// Piercing at 100 m and 500 m respectively.
    pub piercing_power: [f64; 2],
    /// Projectile lifetime boundary, not the piercing interpolation endpoint.
    pub max_distance_m: f64,
}

impl ShotInfo {
    pub fn validate(&self) -> Result<(), CombatRuleError> {
        if !self.caliber_mm.is_finite()
            || self.caliber_mm < 0.0
            || self
                .damage
                .into_iter()
                .any(|value| !value.is_finite() || value < 0.0)
            || !self.explosion_radius_m.is_finite()
            || self.explosion_radius_m < 0.0
            || self
                .piercing_power
                .into_iter()
                .any(|value| !value.is_finite() || value < 0.0)
            || !self.max_distance_m.is_finite()
            || self.max_distance_m < 0.0
        {
            return Err(CombatRuleError::InvalidShot);
        }
        Ok(())
    }

    fn penetration_caliber_mm(&self) -> f64 {
        // The copied Python law uses `shell.get('caliber', 100) or 100` here,
        // while the HE-radius fallback deliberately treats zero as zero.
        if self.caliber_mm == 0.0 {
            100.0
        } else {
            self.caliber_mm
        }
    }
}

/// Canonical material facts emitted by the enhanced native-oracle adapter.
///
/// The adapter normalizes WG's historical `checkCaliberForRichet` typo into
/// `check_caliber_for_ricochet`. Serde aliases also permit a direct native
/// fixture to exercise that compatibility without weakening the canonical
/// serialized wire shape.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct MaterialInfo {
    #[serde(alias = "armor")]
    pub armor_mm: f64,
    #[serde(
        default = "default_vehicle_damage_factor",
        alias = "vehicleDamageFactor"
    )]
    pub vehicle_damage_factor: f64,
    #[serde(default)]
    pub kind: Option<i64>,
    /// Stable identity of the native MaterialInfo object for the rare
    /// collide-once material that has no numeric `kind`.
    #[serde(default)]
    pub native_identity: Option<u64>,
    #[serde(default, alias = "collideOnceOnly")]
    pub collide_once_only: bool,
    #[serde(default = "default_true", alias = "useHitAngle")]
    pub use_hit_angle: bool,
    #[serde(default = "default_true", alias = "checkCaliberForHitAngleNorm")]
    pub check_caliber_for_hit_angle_norm: bool,
    #[serde(default = "default_true", alias = "mayRicochet")]
    pub may_ricochet: bool,
    #[serde(
        default = "default_true",
        alias = "checkCaliberForRicochet",
        alias = "checkCaliberForRichet"
    )]
    pub check_caliber_for_ricochet: bool,
}

impl Default for MaterialInfo {
    fn default() -> Self {
        Self {
            armor_mm: 0.0,
            vehicle_damage_factor: default_vehicle_damage_factor(),
            kind: None,
            native_identity: None,
            collide_once_only: false,
            use_hit_angle: true,
            check_caliber_for_hit_angle_norm: true,
            may_ricochet: true,
            check_caliber_for_ricochet: true,
        }
    }
}

impl MaterialInfo {
    pub fn is_external(&self) -> bool {
        self.vehicle_damage_factor == 0.0
    }

    fn validate(&self, layer_index: usize) -> Result<(), CombatRuleError> {
        if !self.armor_mm.is_finite()
            || self.armor_mm < 0.0
            || !self.vehicle_damage_factor.is_finite()
            || self.vehicle_damage_factor < 0.0
            || self.native_identity == Some(0)
        {
            return Err(CombatRuleError::InvalidLayer { layer_index });
        }
        if self.collide_once_only && self.kind.is_none() && self.native_identity.is_none() {
            return Err(CombatRuleError::MissingCollideOnceIdentity { layer_index });
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ArmorLayer {
    #[serde(alias = "dist")]
    pub distance_m: f64,
    #[serde(alias = "hitAngleCos")]
    pub hit_angle_cos: f64,
    #[serde(default, alias = "compName")]
    pub component: Option<String>,
    #[serde(alias = "matInfo")]
    pub material: MaterialInfo,
}

impl ArmorLayer {
    fn validate(&self, layer_index: usize) -> Result<(), CombatRuleError> {
        if !self.distance_m.is_finite() || self.distance_m < 0.0 || !self.hit_angle_cos.is_finite()
        {
            return Err(CombatRuleError::InvalidLayer { layer_index });
        }
        self.material.validate(layer_index)
    }
}

/// The two independent random values owned by one launched shell.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ShotFactors {
    pub penetration_factor: f64,
    pub damage_factor: f64,
}

impl ShotFactors {
    pub const NEUTRAL: Self = Self {
        penetration_factor: 1.0,
        damage_factor: 1.0,
    };

    pub fn new(penetration_factor: f64, damage_factor: f64) -> Result<Self, CombatRuleError> {
        let factors = Self {
            penetration_factor,
            damage_factor,
        };
        factors.validate()?;
        Ok(factors)
    }

    pub fn validate(self) -> Result<(), CombatRuleError> {
        validate_factor(
            self.penetration_factor,
            CombatRuleError::InvalidPenetrationFactor,
        )?;
        validate_factor(self.damage_factor, CombatRuleError::InvalidDamageFactor)
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HeTuning {
    pub splash_fraction: f64,
    pub armor_factor: f64,
    pub edge_factor: f64,
}

impl Default for HeTuning {
    fn default() -> Self {
        Self {
            splash_fraction: DEFAULT_HE_SPLASH_FRACTION,
            armor_factor: DEFAULT_HE_ARMOR_FACTOR,
            edge_factor: DEFAULT_HE_EDGE_FACTOR,
        }
    }
}

impl HeTuning {
    pub fn new(
        splash_fraction: f64,
        armor_factor: f64,
        edge_factor: f64,
    ) -> Result<Self, CombatRuleError> {
        let tuning = Self {
            splash_fraction,
            armor_factor,
            edge_factor,
        };
        tuning.validate()?;
        Ok(tuning)
    }

    pub fn validate(self) -> Result<(), CombatRuleError> {
        if !(MIN_HE_FACTOR..=MAX_HE_FACTOR).contains(&self.splash_fraction)
            || !(MIN_HE_FACTOR..=MAX_HE_FACTOR).contains(&self.armor_factor)
            || !(MIN_HE_FACTOR..=1.0).contains(&self.edge_factor)
        {
            return Err(CombatRuleError::InvalidHeTuning);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum PenetrationVerdict {
    Ricochet = 0,
    NoPenetration = 1,
    Penetration = 2,
}

impl PenetrationVerdict {
    pub const fn code(self) -> u8 {
        self as u8
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PenetrationResult {
    pub verdict: PenetrationVerdict,
    pub effective_armor_mm: f64,
    pub piercing_mm: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct PenetrationInput<'a> {
    pub distance_m: f64,
    pub armor_mm: f64,
    pub hit_angle_cos: f64,
    pub pierce_loss_mm: f64,
    pub penetration_factor: f64,
    pub material: Option<&'a MaterialInfo>,
    pub allow_ricochet: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HullHitResult {
    pub verdict: PenetrationVerdict,
    pub effective_armor_mm: f64,
    pub piercing_mm: f64,
    /// Initial destructible loss plus crossed external effective armour and
    /// any HEAT air-gap loss, immediately before the structural layer.
    pub spaced_loss_mm: f64,
    /// Raw native value, matching the Python tuple rather than the clamped
    /// cosine used for the calculation.
    pub hit_angle_cos: f64,
    pub nominal_armor_mm: f64,
    pub structural_layer_index: usize,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DamageResult {
    /// Truncated shell-owned direct roll. HE splash does not pre-truncate and
    /// therefore exposes no `DamageResult`.
    pub rolled_damage: u32,
    pub damage: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DirectHitResult {
    pub verdict: PenetrationVerdict,
    pub effective_armor_mm: Option<f64>,
    pub piercing_mm: Option<f64>,
    pub spaced_loss_mm: f64,
    pub hit_angle_cos: Option<f64>,
    pub nominal_armor_mm: f64,
    pub structural_layer_index: Option<usize>,
    pub rolled_damage: u32,
    pub damage: u32,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum CombatRuleError {
    #[error("shot descriptor contains an invalid numeric field")]
    InvalidShot,
    #[error("travel distance must be finite and non-negative")]
    InvalidDistance,
    #[error("armour must be finite and non-negative")]
    InvalidArmor,
    #[error("piercing loss must be finite and non-negative")]
    InvalidPierceLoss,
    #[error("penetration factor must be in 0.75..=1.25")]
    InvalidPenetrationFactor,
    #[error("base penetration multiplier must be finite and in (0, 1]")]
    InvalidBasePenetrationMultiplier,
    #[error("damage factor must be in 0.75..=1.25")]
    InvalidDamageFactor,
    #[error("armour layer {layer_index} is invalid")]
    InvalidLayer { layer_index: usize },
    #[error("collide-once armour layer {layer_index} has no stable material identity")]
    MissingCollideOnceIdentity { layer_index: usize },
    #[error("HE tuning contains an invalid numeric field")]
    InvalidHeTuning,
    #[error("HE distance fraction must be finite and in 0..=1")]
    InvalidDistanceFraction,
    #[error("damage result exceeds the authority's u32 range")]
    DamageOverflow,
}

/// Non-randomized #1513 P100/P500 interpolation with the lifetime cutoff.
pub fn range_piercing(shot: &ShotInfo, distance_m: f64) -> Result<f64, CombatRuleError> {
    shot.validate()?;
    validate_distance(distance_m)?;
    let [p100, p500] = shot.piercing_power;
    if distance_m <= RANGE_NEAR_M {
        return Ok(p100);
    }
    if shot.max_distance_m <= 0.0 || distance_m >= shot.max_distance_m {
        return Ok(0.0);
    }
    let t = (distance_m - RANGE_NEAR_M) / RANGE_SPAN_M;
    Ok((p100 + (p500 - p100) * t).max(0.0))
}

/// Reuse the shell-owned penetration factor after external-world losses.
pub fn sampled_piercing(
    shot: &ShotInfo,
    distance_m: f64,
    penetration_factor: f64,
    pierce_loss_mm: f64,
) -> Result<f64, CombatRuleError> {
    sampled_piercing_with_base_multiplier(shot, distance_m, penetration_factor, pierce_loss_mm, 1.0)
}

/// Reuse one shell-owned factor after applying the version-locked retained
/// penetration of a ricochet. The multiplier precedes both the random roll
/// and accumulated external-world loss.
pub fn sampled_piercing_with_base_multiplier(
    shot: &ShotInfo,
    distance_m: f64,
    penetration_factor: f64,
    pierce_loss_mm: f64,
    base_penetration_multiplier: f64,
) -> Result<f64, CombatRuleError> {
    validate_factor(
        penetration_factor,
        CombatRuleError::InvalidPenetrationFactor,
    )?;
    validate_base_penetration_multiplier(base_penetration_multiplier)?;
    validate_pierce_loss(pierce_loss_mm)?;
    Ok(
        (range_piercing(shot, distance_m)? * base_penetration_multiplier * penetration_factor
            - pierce_loss_mm)
            .max(0.0),
    )
}

pub fn nominal_piercing_after_loss(
    shot: &ShotInfo,
    distance_m: f64,
    pierce_loss_mm: f64,
) -> Result<f64, CombatRuleError> {
    validate_pierce_loss(pierce_loss_mm)?;
    Ok((range_piercing(shot, distance_m)? - pierce_loss_mm).max(0.0))
}

/// Resolve one plate with exact #1513 normalization and ricochet boundaries.
pub fn resolve_penetration(
    shot: &ShotInfo,
    input: PenetrationInput<'_>,
) -> Result<PenetrationResult, CombatRuleError> {
    resolve_penetration_with_base_multiplier(shot, input, 1.0)
}

pub fn resolve_penetration_with_base_multiplier(
    shot: &ShotInfo,
    input: PenetrationInput<'_>,
    base_penetration_multiplier: f64,
) -> Result<PenetrationResult, CombatRuleError> {
    shot.validate()?;
    validate_distance(input.distance_m)?;
    if !input.armor_mm.is_finite() || input.armor_mm < 0.0 {
        return Err(CombatRuleError::InvalidArmor);
    }
    if !input.hit_angle_cos.is_finite() {
        return Err(CombatRuleError::InvalidLayer { layer_index: 0 });
    }
    validate_pierce_loss(input.pierce_loss_mm)?;
    validate_factor(
        input.penetration_factor,
        CombatRuleError::InvalidPenetrationFactor,
    )?;
    validate_base_penetration_multiplier(base_penetration_multiplier)?;
    if let Some(material) = input.material {
        material.validate(0)?;
    }

    let piercing_mm = sampled_piercing_with_base_multiplier(
        shot,
        input.distance_m,
        input.penetration_factor,
        input.pierce_loss_mm,
        base_penetration_multiplier,
    )?;
    if input.armor_mm <= 0.0 {
        return Ok(PenetrationResult {
            verdict: PenetrationVerdict::Penetration,
            effective_armor_mm: 0.0,
            piercing_mm,
        });
    }

    let use_hit_angle = input.material.map_or(true, |value| value.use_hit_angle);
    let (angle_cos, angle) = if use_hit_angle {
        let angle_cos = input.hit_angle_cos.abs().clamp(MIN_ANGLE_COS, 1.0);
        (angle_cos, angle_cos.acos())
    } else {
        (1.0, 0.0)
    };
    let caliber_mm = shot.penetration_caliber_mm();
    let normalizing_ap = shot.kind.is_normalizing_ap();
    let mut normalization = match shot.kind {
        ShellKind::ArmorPiercingCr => 2.0_f64.to_radians(),
        ShellKind::ArmorPiercing => 5.0_f64.to_radians(),
        _ => 0.0,
    };
    let checks_two_caliber = input
        .material
        .map_or(true, |value| value.check_caliber_for_hit_angle_norm);
    if use_hit_angle && normalizing_ap && checks_two_caliber && caliber_mm > input.armor_mm * 2.0 {
        normalization *= 1.4 * caliber_mm / (input.armor_mm * 2.0);
    }

    let shell_may_ricochet = normalizing_ap || shot.kind == ShellKind::HollowCharge;
    let material_may_ricochet = input.material.map_or(true, |value| value.may_ricochet);
    let may_ricochet =
        input.allow_ricochet && use_hit_angle && shell_may_ricochet && material_may_ricochet;
    let checks_three_caliber = input
        .material
        .map_or(true, |value| value.check_caliber_for_ricochet);
    let no_ap_ricochet =
        normalizing_ap && checks_three_caliber && caliber_mm > input.armor_mm * 3.0;

    if may_ricochet {
        if normalizing_ap && !no_ap_ricochet && angle_cos <= AP_RICOCHET_DEGREES.to_radians().cos()
        {
            return Ok(PenetrationResult {
                verdict: PenetrationVerdict::Ricochet,
                effective_armor_mm: input.armor_mm / angle_cos.max(MIN_ANGLE_COS),
                piercing_mm,
            });
        }
        if shot.kind == ShellKind::HollowCharge
            && angle_cos <= HEAT_RICOCHET_DEGREES.to_radians().cos()
        {
            return Ok(PenetrationResult {
                verdict: PenetrationVerdict::Ricochet,
                effective_armor_mm: input.armor_mm / angle_cos.max(MIN_ANGLE_COS),
                piercing_mm,
            });
        }
    }

    let effective_angle = (angle - normalization).max(0.0);
    let effective_armor_mm = input.armor_mm / effective_angle.cos().max(MIN_ANGLE_COS);
    Ok(PenetrationResult {
        verdict: if piercing_mm >= effective_armor_mm {
            PenetrationVerdict::Penetration
        } else {
            PenetrationVerdict::NoPenetration
        },
        effective_armor_mm,
        piercing_mm,
    })
}

/// Resolve native vehicle layers in projectile order.
///
/// `None` has the copied Python meaning: an external layer stopped the shell,
/// or no structural plate was reached. Callers treat it as a hull
/// non-penetration; HE may still produce direct blast damage.
pub fn resolve_hull_hit(
    shot: &ShotInfo,
    distance_m: f64,
    layers: &[ArmorLayer],
    initial_pierce_loss_mm: f64,
    penetration_factor: f64,
) -> Result<Option<HullHitResult>, CombatRuleError> {
    resolve_hull_hit_with_base_multiplier(
        shot,
        distance_m,
        layers,
        initial_pierce_loss_mm,
        penetration_factor,
        1.0,
    )
}

pub fn resolve_hull_hit_with_base_multiplier(
    shot: &ShotInfo,
    distance_m: f64,
    layers: &[ArmorLayer],
    initial_pierce_loss_mm: f64,
    penetration_factor: f64,
    base_penetration_multiplier: f64,
) -> Result<Option<HullHitResult>, CombatRuleError> {
    shot.validate()?;
    validate_distance(distance_m)?;
    validate_pierce_loss(initial_pierce_loss_mm)?;
    validate_factor(
        penetration_factor,
        CombatRuleError::InvalidPenetrationFactor,
    )?;
    validate_base_penetration_multiplier(base_penetration_multiplier)?;

    let mut ordered = Vec::with_capacity(layers.len());
    for (index, layer) in layers.iter().enumerate() {
        layer.validate(index)?;
        ordered.push((index, layer));
    }
    ordered.sort_by(|left, right| {
        left.1
            .distance_m
            .partial_cmp(&right.1.distance_m)
            .unwrap_or(Ordering::Equal)
    });

    let mut spaced_loss_mm = initial_pierce_loss_mm;
    let mut base_piercing_mm = None;
    let mut heat_last_plate_distance_m: Option<f64> = None;
    let mut seen_once = BTreeSet::new();

    for (source_index, layer) in ordered {
        let material = &layer.material;
        let once_key = once_key(layer, source_index)?;
        if once_key.as_ref().is_some_and(|key| seen_once.contains(key)) {
            continue;
        }
        if material.armor_mm <= 0.0 {
            continue;
        }
        let base = match base_piercing_mm {
            Some(value) => value,
            None => {
                let value = range_piercing(shot, distance_m)?
                    * base_penetration_multiplier
                    * penetration_factor;
                base_piercing_mm = Some(value);
                value
            }
        };
        if shot.kind == ShellKind::HollowCharge {
            if let Some(last_plate_distance_m) = heat_last_plate_distance_m {
                let gap_m = (layer.distance_m - last_plate_distance_m).max(0.0);
                let remaining_mm = (base - spaced_loss_mm).max(0.0);
                spaced_loss_mm += remaining_mm * (0.5 * gap_m).min(1.0);
            }
        }

        let penetration = resolve_penetration_with_base_multiplier(
            shot,
            PenetrationInput {
                distance_m,
                armor_mm: material.armor_mm,
                hit_angle_cos: layer.hit_angle_cos,
                pierce_loss_mm: spaced_loss_mm,
                penetration_factor,
                material: Some(material),
                allow_ricochet: !(shot.kind == ShellKind::HollowCharge
                    && heat_last_plate_distance_m.is_some()),
            },
            base_penetration_multiplier,
        )?;

        if material.is_external() {
            if penetration.verdict != PenetrationVerdict::Penetration {
                return Ok(None);
            }
            spaced_loss_mm += penetration.effective_armor_mm;
            if shot.kind == ShellKind::HollowCharge {
                // Native preview starts the air gap behind nominal thickness.
                heat_last_plate_distance_m = Some(layer.distance_m + material.armor_mm * 0.001);
            }
            if let Some(key) = once_key {
                seen_once.insert(key);
            }
            continue;
        }

        return Ok(Some(HullHitResult {
            verdict: penetration.verdict,
            effective_armor_mm: penetration.effective_armor_mm,
            piercing_mm: penetration.piercing_mm,
            spaced_loss_mm,
            hit_angle_cos: layer.hit_angle_cos,
            nominal_armor_mm: material.armor_mm,
            structural_layer_index: source_index,
        }));
    }
    Ok(None)
}

/// Nominal thickness of the first structural plate on the blast ray, falling
/// back to the thinnest structural hull material when the ray has no plate.
pub fn he_nominal_armor(
    layers: &[ArmorLayer],
    hull_materials: &[MaterialInfo],
) -> Result<f64, CombatRuleError> {
    let mut first: Option<(f64, f64)> = None;
    for (index, layer) in layers.iter().enumerate() {
        layer.validate(index)?;
        let material = &layer.material;
        if material.is_external() || material.armor_mm <= 0.0 {
            continue;
        }
        if first.is_none_or(|(distance, _)| layer.distance_m < distance) {
            first = Some((layer.distance_m, material.armor_mm));
        }
    }
    if let Some((_, armor_mm)) = first {
        return Ok(armor_mm);
    }
    he_hull_armor(hull_materials)
}

/// Thinnest structural plate carried by the hull descriptor.
pub fn he_hull_armor(materials: &[MaterialInfo]) -> Result<f64, CombatRuleError> {
    let mut best: Option<f64> = None;
    for (index, material) in materials.iter().enumerate() {
        material.validate(index)?;
        if material.is_external() || material.armor_mm <= 0.0 {
            continue;
        }
        best = Some(best.map_or(material.armor_mm, |value| value.min(material.armor_mm)));
    }
    Ok(best.unwrap_or(0.0))
}

/// Apply the direct-hit damage law. The damage factor is shell-owned and is
/// reused independently of any destructible or spaced-armour piercing loss.
pub fn direct_damage(
    shot: &ShotInfo,
    verdict: PenetrationVerdict,
    nominal_armor_mm: f64,
    damage_factor: f64,
    he_tuning: HeTuning,
) -> Result<DamageResult, CombatRuleError> {
    shot.validate()?;
    validate_armor(nominal_armor_mm)?;
    validate_factor(damage_factor, CombatRuleError::InvalidDamageFactor)?;
    he_tuning.validate()?;
    let rolled_damage = checked_trunc_u32(shot.damage[0] * damage_factor)?;
    let damage = if verdict == PenetrationVerdict::Penetration {
        rolled_damage
    } else if shot.kind.is_he() {
        he_damage_value(f64::from(rolled_damage), nominal_armor_mm, 0.0, he_tuning)?
    } else {
        0
    };
    Ok(DamageResult {
        rolled_damage,
        damage,
    })
}

/// Resolve armour and direct hull damage in one call, retaining the exact
/// Python convention that failure to reach structure is result `1`.
pub fn resolve_direct_hit(
    shot: &ShotInfo,
    distance_m: f64,
    layers: &[ArmorLayer],
    hull_materials: &[MaterialInfo],
    initial_pierce_loss_mm: f64,
    factors: ShotFactors,
    he_tuning: HeTuning,
) -> Result<DirectHitResult, CombatRuleError> {
    resolve_direct_hit_with_base_multiplier(
        shot,
        distance_m,
        layers,
        hull_materials,
        initial_pierce_loss_mm,
        factors,
        1.0,
        he_tuning,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn resolve_direct_hit_with_base_multiplier(
    shot: &ShotInfo,
    distance_m: f64,
    layers: &[ArmorLayer],
    hull_materials: &[MaterialInfo],
    initial_pierce_loss_mm: f64,
    factors: ShotFactors,
    base_penetration_multiplier: f64,
    he_tuning: HeTuning,
) -> Result<DirectHitResult, CombatRuleError> {
    factors.validate()?;
    validate_base_penetration_multiplier(base_penetration_multiplier)?;
    let hull = resolve_hull_hit_with_base_multiplier(
        shot,
        distance_m,
        layers,
        initial_pierce_loss_mm,
        factors.penetration_factor,
        base_penetration_multiplier,
    )?;
    let nominal_armor_mm = he_nominal_armor(layers, hull_materials)?;
    let verdict = hull
        .as_ref()
        .map_or(PenetrationVerdict::NoPenetration, |value| value.verdict);
    let damage = direct_damage(
        shot,
        verdict,
        nominal_armor_mm,
        factors.damage_factor,
        he_tuning,
    )?;
    Ok(DirectHitResult {
        verdict,
        effective_armor_mm: hull.as_ref().map(|value| value.effective_armor_mm),
        piercing_mm: hull.as_ref().map(|value| value.piercing_mm),
        spaced_loss_mm: hull
            .as_ref()
            .map_or(initial_pierce_loss_mm, |value| value.spaced_loss_mm),
        hit_angle_cos: hull.as_ref().map(|value| value.hit_angle_cos),
        nominal_armor_mm,
        structural_layer_index: hull.as_ref().map(|value| value.structural_layer_index),
        rolled_damage: damage.rolled_damage,
        damage: damage.damage,
    })
}

pub fn he_radius(shot: &ShotInfo) -> Result<f64, CombatRuleError> {
    shot.validate()?;
    if shot.explosion_radius_m > 0.0 {
        return Ok(shot.explosion_radius_m);
    }
    Ok(if shot.caliber_mm > 0.0 {
        shot.caliber_mm * shot.caliber_mm / HE_RADIUS_CALIBER_DIVISOR
    } else {
        0.0
    })
}

/// Damage to a non-direct vehicle at `distance_fraction` of explosion radius.
/// Unlike the direct path, #1513 does not truncate the random roll before the
/// distance and nominal-armour reductions.
pub fn he_splash_damage(
    shot: &ShotInfo,
    nominal_armor_mm: f64,
    distance_fraction: f64,
    damage_factor: f64,
    he_tuning: HeTuning,
) -> Result<u32, CombatRuleError> {
    shot.validate()?;
    validate_armor(nominal_armor_mm)?;
    validate_factor(damage_factor, CombatRuleError::InvalidDamageFactor)?;
    if !distance_fraction.is_finite() || !(0.0..=1.0).contains(&distance_fraction) {
        return Err(CombatRuleError::InvalidDistanceFraction);
    }
    he_damage_value(
        shot.damage[0] * damage_factor,
        nominal_armor_mm,
        distance_fraction,
        he_tuning,
    )
}

fn he_damage_value(
    base_damage: f64,
    nominal_armor_mm: f64,
    distance_fraction: f64,
    tuning: HeTuning,
) -> Result<u32, CombatRuleError> {
    tuning.validate()?;
    let blast_factor =
        tuning.splash_fraction + (tuning.edge_factor - tuning.splash_fraction) * distance_fraction;
    let value = base_damage * blast_factor - tuning.armor_factor * nominal_armor_mm;
    if value > 0.0 {
        checked_trunc_u32(value)
    } else {
        Ok(0)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum OnceMaterialIdentity {
    Kind(i64),
    Native(u64),
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct OnceKey {
    component: Option<String>,
    material: OnceMaterialIdentity,
}

fn once_key(layer: &ArmorLayer, layer_index: usize) -> Result<Option<OnceKey>, CombatRuleError> {
    if !layer.material.collide_once_only {
        return Ok(None);
    }
    let material = if let Some(kind) = layer.material.kind {
        OnceMaterialIdentity::Kind(kind)
    } else if let Some(identity) = layer.material.native_identity {
        OnceMaterialIdentity::Native(identity)
    } else {
        return Err(CombatRuleError::MissingCollideOnceIdentity { layer_index });
    };
    Ok(Some(OnceKey {
        component: layer.component.clone(),
        material,
    }))
}

fn default_true() -> bool {
    true
}

fn default_vehicle_damage_factor() -> f64 {
    1.0
}

fn validate_distance(value: f64) -> Result<(), CombatRuleError> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(CombatRuleError::InvalidDistance)
    }
}

fn validate_armor(value: f64) -> Result<(), CombatRuleError> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(CombatRuleError::InvalidArmor)
    }
}

fn validate_pierce_loss(value: f64) -> Result<(), CombatRuleError> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(CombatRuleError::InvalidPierceLoss)
    }
}

fn validate_base_penetration_multiplier(value: f64) -> Result<(), CombatRuleError> {
    if value.is_finite() && value > 0.0 && value <= 1.0 {
        Ok(())
    } else {
        Err(CombatRuleError::InvalidBasePenetrationMultiplier)
    }
}

fn validate_factor(value: f64, error: CombatRuleError) -> Result<(), CombatRuleError> {
    if value.is_finite() && (MIN_SHOT_FACTOR..=MAX_SHOT_FACTOR).contains(&value) {
        Ok(())
    } else {
        Err(error)
    }
}

fn checked_trunc_u32(value: f64) -> Result<u32, CombatRuleError> {
    if !value.is_finite() || value < 0.0 || value >= f64::from(u32::MAX) + 1.0 {
        return Err(CombatRuleError::DamageOverflow);
    }
    Ok(value.trunc() as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shot(kind: ShellKind, caliber_mm: f64, piercing: [f64; 2]) -> ShotInfo {
        ShotInfo {
            kind,
            caliber_mm,
            damage: [240.0, 100.0],
            explosion_radius_m: 0.0,
            piercing_power: piercing,
            max_distance_m: 720.0,
        }
    }

    fn material(armor_mm: f64, vehicle_damage_factor: f64) -> MaterialInfo {
        MaterialInfo {
            armor_mm,
            vehicle_damage_factor,
            ..MaterialInfo::default()
        }
    }

    fn layer(
        distance_m: f64,
        hit_angle_cos: f64,
        material: MaterialInfo,
        component: &str,
    ) -> ArmorLayer {
        ArmorLayer {
            distance_m,
            hit_angle_cos,
            component: Some(component.to_owned()),
            material,
        }
    }

    fn penetration_at(
        shot: &ShotInfo,
        armor_mm: f64,
        hit_angle_cos: f64,
        material: Option<&MaterialInfo>,
    ) -> PenetrationResult {
        resolve_penetration(
            shot,
            PenetrationInput {
                distance_m: 50.0,
                armor_mm,
                hit_angle_cos,
                pierce_loss_mm: 0.0,
                penetration_factor: 1.0,
                material,
                allow_ricochet: true,
            },
        )
        .unwrap()
    }

    fn assert_close(left: f64, right: f64) {
        assert!((left - right).abs() <= 1.0e-8, "{left} != {right}");
    }

    #[test]
    fn p100_p500_uses_fixed_slope_until_lifetime_cutoff() {
        let mut value = shot(ShellKind::ArmorPiercing, 90.0, [200.0, 100.0]);
        value.max_distance_m = 900.0;
        assert_eq!(range_piercing(&value, 100.0).unwrap(), 200.0);
        assert_eq!(range_piercing(&value, 300.0).unwrap(), 150.0);
        assert_eq!(range_piercing(&value, 500.0).unwrap(), 100.0);
        assert_eq!(range_piercing(&value, 700.0).unwrap(), 50.0);
        assert_eq!(range_piercing(&value, 899.0).unwrap(), 0.25);
        assert_eq!(range_piercing(&value, 900.0).unwrap(), 0.0);

        value.max_distance_m = 350.0;
        assert_eq!(range_piercing(&value, 300.0).unwrap(), 150.0);
        assert_close(range_piercing(&value, 349.999).unwrap(), 137.50025);
        assert_eq!(range_piercing(&value, 350.0).unwrap(), 0.0);
    }

    #[test]
    fn shell_factor_is_reused_after_external_loss() {
        let value = shot(ShellKind::ArmorPiercing, 90.0, [40.0, 40.0]);
        assert_eq!(sampled_piercing(&value, 10.0, 0.75, 0.0).unwrap(), 30.0);
        let hull = layer(10.0, 1.0, material(20.0, 1.0), "vehicleHull");
        let resolved = resolve_hull_hit(&value, 10.0, &[hull], 5.0, 0.75)
            .unwrap()
            .unwrap();
        assert_eq!(resolved.verdict, PenetrationVerdict::Penetration);
        assert_eq!(resolved.piercing_mm, 25.0);
    }

    #[test]
    fn ricochet_multiplier_precedes_shell_roll_and_accumulated_loss() {
        let value = shot(ShellKind::ArmorPiercing, 90.0, [40.0, 40.0]);
        assert_eq!(
            sampled_piercing_with_base_multiplier(&value, 10.0, 0.8, 5.0, 0.75).unwrap(),
            19.0
        );
        let hull = layer(10.0, 1.0, material(20.0, 1.0), "vehicleHull");
        let resolved = resolve_hull_hit_with_base_multiplier(&value, 10.0, &[hull], 5.0, 0.8, 0.75)
            .unwrap()
            .unwrap();
        assert_eq!(resolved.piercing_mm, 19.0);
        assert_eq!(resolved.verdict, PenetrationVerdict::NoPenetration);
        assert_eq!(
            sampled_piercing_with_base_multiplier(&value, 10.0, 1.0, 0.0, 0.0),
            Err(CombatRuleError::InvalidBasePenetrationMultiplier)
        );
    }

    #[test]
    fn two_caliber_normalization_boundary_is_strict() {
        let armor = 60.0;
        let angle = 60.0_f64.to_radians();
        for (kind, base_degrees) in [
            (ShellKind::ArmorPiercing, 5.0_f64),
            (ShellKind::ArmorPiercingCr, 2.0_f64),
        ] {
            let exact = shot(kind, 120.0, [1000.0, 1000.0]);
            let exact_result = penetration_at(&exact, armor, angle.cos(), None);
            assert_close(
                exact_result.effective_armor_mm,
                armor / (angle - base_degrees.to_radians()).cos(),
            );

            let caliber = 120.001;
            let above = shot(kind, caliber, [1000.0, 1000.0]);
            let above_result = penetration_at(&above, armor, angle.cos(), None);
            let normalization = base_degrees.to_radians() * 1.4 * caliber / (2.0 * armor);
            assert_close(
                above_result.effective_armor_mm,
                armor / (angle - normalization).cos(),
            );
        }
    }

    #[test]
    fn three_caliber_rule_is_strict_and_ap_bounces_at_exactly_70_degrees() {
        let angle_cos = 75.0_f64.to_radians().cos();
        for kind in [ShellKind::ArmorPiercing, ShellKind::ArmorPiercingCr] {
            let exact = shot(kind, 180.0, [1000.0, 1000.0]);
            assert_eq!(
                penetration_at(&exact, 60.0, angle_cos, None).verdict,
                PenetrationVerdict::Ricochet
            );
            let above = shot(kind, 180.001, [1000.0, 1000.0]);
            assert_ne!(
                penetration_at(&above, 60.0, angle_cos, None).verdict,
                PenetrationVerdict::Ricochet
            );

            let ordinary = shot(kind, 90.0, [1000.0, 1000.0]);
            assert_ne!(
                penetration_at(&ordinary, 60.0, 69.999_f64.to_radians().cos(), None).verdict,
                PenetrationVerdict::Ricochet
            );
            assert_eq!(
                penetration_at(&ordinary, 60.0, 70.0_f64.to_radians().cos(), None).verdict,
                PenetrationVerdict::Ricochet
            );
        }
    }

    #[test]
    fn aphe_has_no_normalization_ricochet_or_caliber_rules() {
        let value = shot(ShellKind::ArmorPiercingHe, 180.0, [1000.0, 1000.0]);
        let angle = 75.0_f64.to_radians();
        let result = penetration_at(&value, 60.0, angle.cos(), None);
        assert_eq!(result.verdict, PenetrationVerdict::Penetration);
        assert_close(result.effective_armor_mm, 60.0 / angle.cos());
    }

    #[test]
    fn heat_uses_85_degree_boundary_and_never_gets_three_caliber_relief() {
        let value = shot(ShellKind::HollowCharge, 1000.0, [2000.0, 2000.0]);
        assert_eq!(
            penetration_at(&value, 60.0, 84.999_f64.to_radians().cos(), None).verdict,
            PenetrationVerdict::Penetration
        );
        assert_eq!(
            penetration_at(&value, 60.0, 85.0_f64.to_radians().cos(), None).verdict,
            PenetrationVerdict::Ricochet
        );
        assert_eq!(
            penetration_at(&value, 60.0, 0.30, None).verdict,
            PenetrationVerdict::Penetration
        );
    }

    #[test]
    fn material_flags_override_angle_and_caliber_checks() {
        let value = shot(ShellKind::ArmorPiercing, 200.0, [1000.0, 1000.0]);
        let mut no_bounce = material(60.0, 1.0);
        no_bounce.may_ricochet = false;
        assert_eq!(
            penetration_at(&value, 60.0, 75.0_f64.to_radians().cos(), Some(&no_bounce)).verdict,
            PenetrationVerdict::Penetration
        );

        let mut nominal = material(60.0, 1.0);
        nominal.use_hit_angle = false;
        let nominal_result =
            penetration_at(&value, 60.0, 89.0_f64.to_radians().cos(), Some(&nominal));
        assert_eq!(nominal_result.effective_armor_mm, 60.0);

        let mut retain_bounce = material(60.0, 1.0);
        retain_bounce.check_caliber_for_ricochet = false;
        assert_eq!(
            penetration_at(
                &value,
                60.0,
                75.0_f64.to_radians().cos(),
                Some(&retain_bounce)
            )
            .verdict,
            PenetrationVerdict::Ricochet
        );

        let mut normal_norm = material(60.0, 1.0);
        normal_norm.check_caliber_for_hit_angle_norm = false;
        let result = penetration_at(
            &shot(ShellKind::ArmorPiercing, 150.0, [1000.0, 1000.0]),
            60.0,
            60.0_f64.to_radians().cos(),
            Some(&normal_norm),
        );
        assert_close(
            result.effective_armor_mm,
            60.0 / 55.0_f64.to_radians().cos(),
        );
    }

    #[test]
    fn wire_material_accepts_exact_1513_richet_typo() {
        let decoded: MaterialInfo =
            serde_json::from_str(r#"{"armor_mm":60.0,"checkCaliberForRichet":false}"#).unwrap();
        assert!(!decoded.check_caliber_for_ricochet);
        assert!(decoded.use_hit_angle);
        assert_eq!(decoded.vehicle_damage_factor, 1.0);
        let encoded = serde_json::to_value(decoded).unwrap();
        assert_eq!(encoded["check_caliber_for_ricochet"], false);
        assert!(encoded.get("checkCaliberForRichet").is_none());
    }

    #[test]
    fn spaced_armor_is_paid_before_structure() {
        let value = shot(ShellKind::ArmorPiercing, 90.0, [120.0, 120.0]);
        let layers = [
            layer(5.0, 0.5, material(20.0, 0.0), "vehicleChassis"),
            layer(5.2, 1.0, material(100.0, 1.0), "vehicleHull"),
        ];
        let result = resolve_hull_hit(&value, 50.0, &layers, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(result.verdict, PenetrationVerdict::NoPenetration);
        let normalization = 5.0_f64.to_radians() * 1.4 * 90.0 / 40.0;
        let expected = 20.0 / (60.0_f64.to_radians() - normalization).cos();
        assert_close(result.spaced_loss_mm, expected);
    }

    #[test]
    fn external_plate_must_be_penetrated() {
        let value = shot(ShellKind::ArmorPiercing, 90.0, [20.0, 20.0]);
        let layers = [
            layer(5.0, 1.0, material(30.0, 0.0), "vehicleChassis"),
            layer(5.2, 1.0, material(10.0, 1.0), "vehicleHull"),
        ];
        assert_eq!(
            resolve_hull_hit(&value, 50.0, &layers, 0.0, 1.0).unwrap(),
            None
        );
    }

    #[test]
    fn collide_once_only_uses_component_and_kind_identity() {
        let value = shot(ShellKind::ArmorPiercing, 90.0, [110.0, 110.0]);
        let mut once = material(20.0, 0.0);
        once.kind = Some(7);
        once.collide_once_only = true;
        let layers = [
            layer(5.0, 1.0, once.clone(), "vehicleChassis"),
            layer(5.1, 1.0, once, "vehicleChassis"),
            layer(5.2, 1.0, material(80.0, 1.0), "vehicleHull"),
        ];
        let once_result = resolve_hull_hit(&value, 50.0, &layers, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(once_result.verdict, PenetrationVerdict::Penetration);
        assert_eq!(once_result.spaced_loss_mm, 20.0);

        let repeated_layers = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(5.1, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(5.2, 1.0, material(80.0, 1.0), "vehicleHull"),
        ];
        let repeated = resolve_hull_hit(&value, 50.0, &repeated_layers, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(repeated.verdict, PenetrationVerdict::NoPenetration);
        assert_eq!(repeated.spaced_loss_mm, 40.0);
    }

    #[test]
    fn destructible_loss_accumulates_but_never_changes_damage_roll() {
        let mut value = shot(ShellKind::ArmorPiercing, 90.0, [160.0, 160.0]);
        value.damage = [240.0, 100.0];
        let layers = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(5.2, 1.0, material(100.0, 1.0), "vehicleHull"),
        ];
        let result = resolve_hull_hit(&value, 50.0, &layers, 50.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(result.verdict, PenetrationVerdict::NoPenetration);
        assert_eq!(result.spaced_loss_mm, 70.0);
        assert_eq!(result.piercing_mm, 90.0);

        let clear_damage = direct_damage(
            &value,
            PenetrationVerdict::Penetration,
            100.0,
            1.0,
            HeTuning::default(),
        )
        .unwrap();
        let crossed_damage = direct_damage(
            &value,
            PenetrationVerdict::Penetration,
            100.0,
            1.0,
            HeTuning::default(),
        )
        .unwrap();
        assert_eq!(clear_damage, crossed_damage);
    }

    #[test]
    fn heat_external_plate_and_air_gap_match_1513() {
        let value = shot(ShellKind::HollowCharge, 90.0, [400.0, 400.0]);
        let layers = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(5.2, 1.0, material(100.0, 1.0), "vehicleHull"),
        ];
        let result = resolve_hull_hit(&value, 50.0, &layers, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(result.verdict, PenetrationVerdict::Penetration);
        assert_close(result.spaced_loss_mm, 54.2);
        assert_close(result.piercing_mm, 345.8);

        let low = shot(ShellKind::HollowCharge, 90.0, [19.999, 19.999]);
        assert_eq!(
            resolve_hull_hit(&low, 50.0, &layers, 0.0, 1.0).unwrap(),
            None
        );
    }

    #[test]
    fn heat_gap_costs_half_remaining_penetration_per_meter() {
        let value = shot(ShellKind::HollowCharge, 90.0, [200.0, 200.0]);
        let short = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(5.82, 1.0, material(95.0, 1.0), "vehicleHull"),
        ];
        let short_result = resolve_hull_hit(&value, 50.0, &short, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(short_result.verdict, PenetrationVerdict::Penetration);
        assert_close(short_result.spaced_loss_mm, 92.0);
        assert_close(short_result.piercing_mm, 108.0);

        let one_meter = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(6.02, 1.0, material(95.0, 1.0), "vehicleHull"),
        ];
        let one_meter_result = resolve_hull_hit(&value, 50.0, &one_meter, 0.0, 1.0)
            .unwrap()
            .unwrap();
        assert_eq!(one_meter_result.verdict, PenetrationVerdict::NoPenetration);
        assert_close(one_meter_result.spaced_loss_mm, 110.0);
        assert_close(one_meter_result.piercing_mm, 90.0);
    }

    #[test]
    fn heat_jet_cannot_ricochet_after_external_plate() {
        let value = shot(ShellKind::HollowCharge, 10.0, [2000.0, 2000.0]);
        let layers = [
            layer(5.0, 1.0, material(20.0, 0.0), "vehicleChassis"),
            layer(
                5.2,
                85.0_f64.to_radians().cos(),
                material(60.0, 1.0),
                "vehicleHull",
            ),
        ];
        assert_eq!(
            resolve_hull_hit(&value, 50.0, &layers, 0.0, 1.0)
                .unwrap()
                .unwrap()
                .verdict,
            PenetrationVerdict::Penetration
        );
    }

    #[test]
    fn he_direct_and_splash_damage_preserve_082_truncation() {
        let mut value = shot(ShellKind::HighExplosive, 90.0, [160.0, 120.0]);
        value.damage = [400.0, 165.0];
        value.explosion_radius_m = 10.0;
        let direct = direct_damage(
            &value,
            PenetrationVerdict::NoPenetration,
            100.0,
            1.0,
            HeTuning::default(),
        )
        .unwrap();
        assert_eq!(direct.rolled_damage, 400);
        assert_eq!(direct.damage, 70);
        assert_eq!(
            he_splash_damage(&value, 50.0, 0.5, 1.0, HeTuning::default()).unwrap(),
            65
        );
        assert_eq!(he_radius(&value).unwrap(), 10.0);
    }

    #[test]
    fn descriptor_default_he_factors_match_center_middle_and_edge_law() {
        let mut value = shot(ShellKind::HighExplosive, 122.0, [60.0, 60.0]);
        value.damage = [400.0, 90.0];
        value.explosion_radius_m = 10.0;
        let tuning = HeTuning::new(0.5, 1.3, 0.15).unwrap();

        assert_eq!(he_splash_damage(&value, 0.0, 0.0, 1.0, tuning), Ok(200));
        assert_eq!(he_splash_damage(&value, 50.0, 0.5, 1.0, tuning), Ok(65));
        assert_eq!(he_splash_damage(&value, 0.0, 1.0, 1.0, tuning), Ok(60));
    }

    #[test]
    fn descriptor_override_he_factors_feed_the_same_pure_damage_path() {
        let mut value = shot(ShellKind::HighExplosive, 122.0, [60.0, 60.0]);
        value.damage = [400.0, 90.0];
        value.explosion_radius_m = 10.0;
        let tuning = HeTuning::new(0.6, 1.0, 0.2).unwrap();

        assert_eq!(he_splash_damage(&value, 50.0, 0.5, 1.0, tuning), Ok(110));
    }

    #[test]
    fn malformed_he_factors_fail_closed_before_damage_math() {
        for values in [
            [0.0, 1.3, 0.15],
            [0.5, 0.0, 0.15],
            [0.5, 1.3, 0.0],
            [10_000.000_001, 1.3, 0.15],
            [0.5, 10_000.000_001, 0.15],
            [0.5, 1.3, 1.000_001],
            [f64::NAN, 1.3, 0.15],
        ] {
            assert_eq!(
                HeTuning::new(values[0], values[1], values[2]),
                Err(CombatRuleError::InvalidHeTuning)
            );
        }
    }

    #[test]
    fn solid_shells_use_vehicle_damage_and_quarter_range() {
        for kind in [
            ShellKind::ArmorPiercing,
            ShellKind::ArmorPiercingHe,
            ShellKind::ArmorPiercingCr,
            ShellKind::HollowCharge,
        ] {
            let mut value = shot(kind, 90.0, [160.0, 120.0]);
            value.damage = [400.0, 165.0];
            assert_eq!(
                direct_damage(
                    &value,
                    PenetrationVerdict::Penetration,
                    100.0,
                    0.75,
                    HeTuning::default()
                )
                .unwrap()
                .damage,
                300
            );
            assert_eq!(
                direct_damage(
                    &value,
                    PenetrationVerdict::Penetration,
                    100.0,
                    1.25,
                    HeTuning::default()
                )
                .unwrap()
                .damage,
                500
            );
        }
    }

    #[test]
    fn he_nominal_armor_skips_spaced_layers_and_uses_hull_fallback() {
        let layers = [
            layer(2.0, 1.0, material(40.0, 0.0), "vehicleChassis"),
            layer(2.5, 0.5, material(75.0, 1.0), "vehicleHull"),
        ];
        assert_eq!(he_nominal_armor(&layers, &[]).unwrap(), 75.0);
        let hull = [material(90.0, 1.0), material(35.0, 1.0)];
        assert_eq!(he_nominal_armor(&[], &hull).unwrap(), 35.0);
    }

    #[test]
    fn combined_direct_resolution_exposes_verdict_armor_piercing_and_damage() {
        let mut value = shot(ShellKind::ArmorPiercing, 90.0, [160.0, 160.0]);
        value.damage = [400.0, 165.0];
        let layers = [layer(5.0, 1.0, material(100.0, 1.0), "vehicleHull")];
        let result = resolve_direct_hit(
            &value,
            50.0,
            &layers,
            &[],
            0.0,
            ShotFactors::new(1.0, 0.75).unwrap(),
            HeTuning::default(),
        )
        .unwrap();
        assert_eq!(result.verdict, PenetrationVerdict::Penetration);
        assert_eq!(result.effective_armor_mm, Some(100.0));
        assert_eq!(result.piercing_mm, Some(160.0));
        assert_eq!(result.damage, 300);
    }

    #[test]
    fn invalid_wire_numbers_and_factor_ranges_fail_closed() {
        assert_eq!(
            ShotFactors::new(0.749_999, 1.0),
            Err(CombatRuleError::InvalidPenetrationFactor)
        );
        let mut bad = material(20.0, 0.0);
        bad.collide_once_only = true;
        let layers = [layer(5.0, 1.0, bad, "vehicleChassis")];
        assert_eq!(
            resolve_hull_hit(
                &shot(ShellKind::ArmorPiercing, 90.0, [100.0, 100.0]),
                50.0,
                &layers,
                0.0,
                1.0
            ),
            Err(CombatRuleError::MissingCollideOnceIdentity { layer_index: 0 })
        );
    }
}
