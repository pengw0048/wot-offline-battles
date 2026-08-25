//! Deterministic server-owned projectile flight and native-oracle planning.
//!
//! The integrator owns the ballistic cursor. It emits typed native-world
//! queries at simulation tick `T`, then accepts only the matching brokered
//! result at `T + 3`. There is intentionally no API for accepting a visible
//! client's hit verdict. Capacity and terminal idempotency remain the
//! responsibility of [`crate::projectile::ProjectileLedger`].

use crate::combat::{VehicleKey, MAX_COMBAT_ID};
use crate::combat_rules::{
    he_nominal_armor, he_radius, he_splash_damage, sampled_piercing_with_base_multiplier,
    ArmorLayer, CombatRuleError, HeTuning, MaterialInfo, ShellKind, ShotInfo,
};
use crate::critical_damage::{
    CrewName, CriticalLayer, CriticalTarget, CriticalTrace, DeviceName, InternalCriticalHit,
};
use crate::destructible::{
    DestructibleAuthority, DestructibleError, DestructibleKey, DestructibleReceipt,
};
use crate::oracle::{AppliedOracleBatch, TimedOutOracleBatch};
use crate::projectile::{
    first_ricochet_penetration_multiplier, ProjectileCursor, ProjectileOutcome, ProjectileRecord,
    ProjectileResolution, ProjectileVec3,
};
use crate::protocol::{
    DestructibleShellKind, DestructibleShotEvidence, DestructibleShotEvidenceQuery,
    DestructibleStaticCollision, EntityRef, ExplosionEvidence, ExplosionEvidenceQuery,
    ExplosionHitLayer, ExplosionVehicleRay, OracleLineage, OracleOperation, OracleV1BatchRequest,
    OracleV1Query, OracleV1ResultStatus, QueryOutcome, RayHit, VehicleCriticalCrewName,
    VehicleCriticalDeviceName, VehicleCriticalTarget, VehicleHit, VehicleHitLayer, WorldRevision,
    MAX_DESTRUCTIBLE_CANDIDATES, MAX_ORACLE_BATCH_QUERIES, MAX_ORACLE_PRIMITIVE_OPERATIONS,
    ORACLE_PIPELINE_TICKS, ORACLE_PROTOCOL_VERSION,
};
use crate::sim::delta_us_for_tick;
use crate::validator::{validate_explosion_evidence_receipt, validate_vehicle_hit_receipt};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

pub const PROJECTILE_MAX_SUBSTEP_SECONDS: f64 = 0.05;
pub const PROJECTILE_MAX_CHORD_ERROR_METERS: f64 = 0.05;
pub const PROJECTILE_MIN_SUBSTEP_SECONDS: f64 = 0.001;
pub const MAX_FROZEN_HE_TARGETS: usize = 30;

const MICROS_PER_SECOND: f64 = 1_000_000.0;
const QUERY_LANES: u64 = ORACLE_PIPELINE_TICKS + 1;
const EPSILON: f64 = 1.0e-9;
const COLLISION_TIE_EPSILON: f64 = 1.0e-6;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct ProjectilePlanId {
    pub issued_tick: u64,
    pub projectile_ordinal: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FlightTarget {
    pub vehicle: VehicleKey,
    pub entity: EntityRef,
    pub wreck: bool,
}

/// Exact target pose and native explosion-ray facts frozen at the projectile
/// terminal boundary. The target pose is the vehicle root used by #1513 for
/// splash distance; hull materials come from the donated descriptor and
/// `damage_factor` is selected by Rust rules.
#[derive(Clone, Debug, PartialEq)]
pub struct FrozenHeTarget {
    pub target: VehicleKey,
    pub entity: EntityRef,
    pub target_position: ProjectileVec3,
    pub vehicle_ray: Option<ExplosionVehicleRay>,
    pub critical_trace: CriticalTrace,
    pub hull_materials: Vec<MaterialInfo>,
    pub damage_factor: f64,
}

/// Pure Rust splash verdict retaining the immutable native critical trace that
/// justified it. Mutation of hull/module/crew state remains a battle transaction.
#[derive(Clone, Debug, PartialEq)]
pub struct FrozenHeSplash {
    pub target: VehicleKey,
    pub entity: EntityRef,
    pub target_position: ProjectileVec3,
    pub distance_m: f64,
    pub distance_fraction: f64,
    pub nominal_armor_mm: f64,
    pub hull_damage: u32,
    pub critical_trace: CriticalTrace,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FlightSegment {
    pub index: usize,
    pub start: ProjectileVec3,
    pub end: ProjectileVec3,
    pub start_elapsed_us: f64,
    pub end_elapsed_us: f64,
    pub start_distance: f64,
    pub end_distance: f64,
}

impl FlightSegment {
    pub fn length(&self) -> f64 {
        distance(self.start, self.end)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LimitTerminal {
    MaxDistance,
    MaxTime,
}

#[derive(Clone, Debug, PartialEq)]
enum QueryBinding {
    Destructibles {
        segment_index: usize,
    },
    Vehicle {
        segment_index: usize,
        target: FlightTarget,
    },
}

/// One projectile's complete native-oracle work for one 30 Hz tick.
///
/// A plan fits in one oracle-v1 batch. `request` assigns transport-owned batch
/// sequence and world revision fields without weakening any flight fence.
#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileFlightPlan {
    pub id: ProjectilePlanId,
    pub lineage: OracleLineage,
    pub projectile_id: String,
    pub issued_tick: u64,
    pub apply_tick: u64,
    pub segments: Vec<FlightSegment>,
    pub queries: Vec<OracleV1Query>,
    terminal_on_clear: Option<LimitTerminal>,
    bindings: BTreeMap<u64, QueryBinding>,
}

impl ProjectileFlightPlan {
    pub fn request(&self, batch_seq: u64, world_revision: WorldRevision) -> OracleV1BatchRequest {
        OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: self.lineage.round_id,
            authority_epoch: self.lineage.authority_epoch,
            oracle_generation: self.lineage.oracle_generation,
            batch_seq,
            issued_tick: self.issued_tick,
            apply_tick: self.apply_tick,
            world_revision,
            queries: self.queries.clone(),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum ProjectileTerminalCause {
    Direct {
        target: VehicleKey,
        entity: EntityRef,
        native_hit: VehicleHit,
    },
    Wreck {
        target: VehicleKey,
        entity: EntityRef,
        native_hit: VehicleHit,
    },
    Terrain {
        native_hit: RayHit,
    },
    /// Backing static geometry returned after the oracle skipped only the
    /// exact destructible identities considered by the same query.
    DestructibleBacking {
        native_hit: DestructibleStaticCollision,
    },
    Destructible {
        receipt: DestructibleReceipt,
    },
    MaxDistance,
    MaxTime,
    OracleTimeout,
}

impl ProjectileTerminalCause {
    /// Converts the immutable native receipt carried by a vehicle terminal
    /// directly into the gameplay type consumed by `critical_damage`.
    pub fn critical_trace(&self) -> Option<CriticalTrace> {
        match self {
            Self::Direct { native_hit, .. } | Self::Wreck { native_hit, .. } => {
                Some(critical_trace_from_vehicle_hit(native_hit))
            }
            Self::Terrain { .. }
            | Self::DestructibleBacking { .. }
            | Self::Destructible { .. }
            | Self::MaxDistance
            | Self::MaxTime
            | Self::OracleTimeout => None,
        }
    }
}

pub fn critical_trace_from_vehicle_hit(hit: &VehicleHit) -> CriticalTrace {
    CriticalTrace {
        native_layers: hit
            .layers
            .iter()
            .map(|layer| CriticalLayer {
                distance_m: layer.distance_m,
                armor_mm: layer.material.armor_mm,
                vehicle_damage_factor: layer.material.vehicle_damage_factor,
                target: layer.critical_target.map(critical_target_from_wire),
                chance_to_hit_by_projectile: layer.chance_to_hit_by_projectile,
                chance_to_hit_by_explosion: layer.chance_to_hit_by_explosion,
            })
            .collect(),
        internal_hits: hit.internal_hits.as_ref().map(|hits| {
            hits.iter()
                .map(|hit| InternalCriticalHit {
                    distance_m: hit.distance_m,
                    target: critical_target_from_wire(hit.target),
                })
                .collect()
        }),
    }
}

/// Convert the strict HE-only oracle receipt into the critical law's immutable
/// trace without importing projectile-only critical chances.
pub fn critical_trace_from_explosion_evidence(evidence: &ExplosionEvidence) -> CriticalTrace {
    CriticalTrace {
        native_layers: evidence
            .vehicle_ray
            .as_ref()
            .map(|ray| {
                ray.layers
                    .iter()
                    .map(|layer| CriticalLayer {
                        distance_m: layer.distance_m,
                        armor_mm: layer.material.armor_mm,
                        vehicle_damage_factor: layer.material.vehicle_damage_factor,
                        target: layer.critical_target.map(critical_target_from_wire),
                        chance_to_hit_by_projectile: None,
                        chance_to_hit_by_explosion: layer.chance_to_hit_by_explosion,
                    })
                    .collect()
            })
            .unwrap_or_default(),
        internal_hits: evidence.internal_hits.as_ref().map(|hits| {
            hits.iter()
                .map(|hit| InternalCriticalHit {
                    distance_m: hit.distance_m,
                    target: critical_target_from_wire(hit.target),
                })
                .collect()
        }),
    }
}

/// Resolve one validated, generation-fenced native receipt into the pure Rust
/// facts consumed by HE splash math. The full pose echo is checked here again
/// so a caller cannot accidentally combine two simulation instants.
pub fn frozen_he_target_from_explosion_evidence(
    target: VehicleKey,
    query: &ExplosionEvidenceQuery,
    evidence: ExplosionEvidence,
    hull_materials: Vec<MaterialInfo>,
    damage_factor: f64,
) -> Result<FrozenHeTarget, ProjectileFlightError> {
    validate_explosion_evidence_receipt(query, &evidence, 1)
        .map_err(|_| ProjectileFlightError::InvalidFrozenHeFacts)?;
    validate_entity(query.target)?;
    let target_position = ProjectileVec3 {
        x: f64::from(evidence.target_pose.position.x),
        y: f64::from(evidence.target_pose.position.y),
        z: f64::from(evidence.target_pose.position.z),
    };
    let critical_trace = critical_trace_from_explosion_evidence(&evidence);
    Ok(FrozenHeTarget {
        target,
        entity: query.target,
        target_position,
        vehicle_ray: evidence.vehicle_ray,
        critical_trace,
        hull_materials,
        damage_factor,
    })
}

fn critical_target_from_wire(target: VehicleCriticalTarget) -> CriticalTarget {
    match target {
        VehicleCriticalTarget::Device(name) => CriticalTarget::Device(match name {
            VehicleCriticalDeviceName::AmmoBayHealth => DeviceName::AmmoBayHealth,
            VehicleCriticalDeviceName::EngineHealth => DeviceName::EngineHealth,
            VehicleCriticalDeviceName::FuelTankHealth => DeviceName::FuelTankHealth,
            VehicleCriticalDeviceName::GunHealth => DeviceName::GunHealth,
            VehicleCriticalDeviceName::LeftTrackHealth => DeviceName::LeftTrackHealth,
            VehicleCriticalDeviceName::RadioHealth => DeviceName::RadioHealth,
            VehicleCriticalDeviceName::RightTrackHealth => DeviceName::RightTrackHealth,
            VehicleCriticalDeviceName::SurveyingDeviceHealth => DeviceName::SurveyingDeviceHealth,
            VehicleCriticalDeviceName::TurretRotatorHealth => DeviceName::TurretRotatorHealth,
        }),
        VehicleCriticalTarget::Crew(name) => CriticalTarget::Crew(match name {
            VehicleCriticalCrewName::Commander => CrewName::Commander,
            VehicleCriticalCrewName::Driver => CrewName::Driver,
            VehicleCriticalCrewName::Gunner1 => CrewName::Gunner1,
            VehicleCriticalCrewName::Gunner2 => CrewName::Gunner2,
            VehicleCriticalCrewName::Loader1 => CrewName::Loader1,
            VehicleCriticalCrewName::Loader2 => CrewName::Loader2,
            VehicleCriticalCrewName::Radioman1 => CrewName::Radioman1,
            VehicleCriticalCrewName::Radioman2 => CrewName::Radioman2,
        }),
    }
}

/// Resolve #1513 HE splash only from a terminal record and immutable facts
/// sampled at that same native-world boundary.
pub fn resolve_frozen_he_splash(
    record: &ProjectileRecord,
    resolution: &ProjectileResolution,
    direct_target: Option<VehicleKey>,
    targets: &[FrozenHeTarget],
    he_tuning: HeTuning,
) -> Result<Vec<FrozenHeSplash>, ProjectileFlightError> {
    if !record.launch.is_he {
        return Ok(Vec::new());
    }
    if resolution.round_id != record.launch.round_id
        || resolution.authority_epoch != record.launch.authority_epoch
        || resolution.projectile_id != record.projectile_id
        || resolution.outcome != ProjectileOutcome::Impact
        || resolution.base_checked_ms != record.checked_through_ms
        || resolution.resolved_time_ms < resolution.base_checked_ms
        || resolution.resolved_time_ms > record.launch.max_time_ms
        || !resolution.checked_distance.is_finite()
        || resolution.checked_distance + EPSILON < record.checked_distance
        || resolution.checked_distance > record.launch.max_distance + EPSILON
        || !resolution.piercing_loss.is_finite()
        || resolution.piercing_loss + EPSILON < record.piercing_loss
        || resolution.penetration_factor != record.launch.penetration_factor
        || targets.len() > MAX_FROZEN_HE_TARGETS
    {
        return Err(if targets.len() > MAX_FROZEN_HE_TARGETS {
            ProjectileFlightError::FrozenHeCapacity
        } else {
            ProjectileFlightError::InvalidFrozenHeFacts
        });
    }
    let impact = resolution
        .impact
        .filter(|value| valid_world_position(*value))
        .ok_or(ProjectileFlightError::InvalidFrozenHeFacts)?;
    let shot = projectile_shot_info(record)?;
    let radius = he_radius(&shot)?;
    if radius <= 0.0 {
        return Ok(Vec::new());
    }

    let mut normalized = targets.to_vec();
    normalized.sort_by_key(|target| {
        (
            target.target,
            target.entity.entity_id,
            target.entity.generation,
        )
    });
    let mut vehicles = BTreeSet::new();
    let mut entities = BTreeSet::new();
    let mut resolved = Vec::new();
    for target in normalized {
        validate_entity(target.entity)?;
        if target.target.id == 0
            || target.target.id > MAX_COMBAT_ID
            || !valid_world_position(target.target_position)
            || !target.damage_factor.is_finite()
            || !(0.75..=1.25).contains(&target.damage_factor)
            || !vehicles.insert(target.target)
            || !entities.insert((target.entity.entity_id, target.entity.generation))
        {
            return Err(ProjectileFlightError::InvalidFrozenHeFacts);
        }
        if Some(target.target) == direct_target {
            continue;
        }
        let distance_m = distance(impact, target.target_position);
        if !distance_m.is_finite() || distance_m > radius + EPSILON {
            continue;
        }
        let layers = target
            .vehicle_ray
            .as_ref()
            .map(|ray| {
                ray.layers
                    .iter()
                    .map(armor_layer_from_explosion_hit)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let nominal_armor_mm = he_nominal_armor(&layers, &target.hull_materials)?;
        let distance_fraction = (distance_m / radius).clamp(0.0, 1.0);
        let hull_damage = he_splash_damage(
            &shot,
            nominal_armor_mm,
            distance_fraction,
            target.damage_factor,
            he_tuning,
        )?;
        if hull_damage == 0 {
            continue;
        }
        resolved.push(FrozenHeSplash {
            target: target.target,
            entity: target.entity,
            target_position: target.target_position,
            distance_m: round6(distance_m),
            distance_fraction: round6(distance_fraction),
            nominal_armor_mm: round6(nominal_armor_mm),
            hull_damage,
            critical_trace: target.critical_trace,
        });
    }
    Ok(resolved)
}

pub fn armor_layer_from_vehicle_hit(source: &VehicleHitLayer) -> ArmorLayer {
    ArmorLayer {
        distance_m: source.distance_m,
        hit_angle_cos: source.hit_angle_cos,
        component: source.component.clone(),
        material: MaterialInfo {
            armor_mm: source.material.armor_mm,
            vehicle_damage_factor: source.material.vehicle_damage_factor,
            kind: source.material.kind,
            native_identity: source.material.native_identity,
            collide_once_only: source.material.collide_once_only,
            use_hit_angle: source.material.use_hit_angle,
            check_caliber_for_hit_angle_norm: source.material.check_caliber_for_hit_angle_norm,
            may_ricochet: source.material.may_ricochet,
            check_caliber_for_ricochet: source.material.check_caliber_for_ricochet,
        },
    }
}

pub fn armor_layer_from_explosion_hit(source: &ExplosionHitLayer) -> ArmorLayer {
    ArmorLayer {
        distance_m: source.distance_m,
        hit_angle_cos: source.hit_angle_cos,
        component: source.component.clone(),
        material: MaterialInfo {
            armor_mm: source.material.armor_mm,
            vehicle_damage_factor: source.material.vehicle_damage_factor,
            kind: source.material.kind,
            native_identity: source.material.native_identity,
            collide_once_only: source.material.collide_once_only,
            use_hit_angle: source.material.use_hit_angle,
            check_caliber_for_hit_angle_norm: source.material.check_caliber_for_hit_angle_norm,
            may_ricochet: source.material.may_ricochet,
            check_caliber_for_ricochet: source.material.check_caliber_for_ricochet,
        },
    }
}

/// Physics-only terminal proposal. Combat damage, critical modules, splash,
/// and destructible transactions are deliberately filled by the battle layer.
#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileTerminalProposal {
    pub plan_id: ProjectilePlanId,
    pub issued_tick: u64,
    pub applied_tick: u64,
    pub cause: ProjectileTerminalCause,
    pub resolution: ProjectileResolution,
    /// Canonical server decisions produced from the same exact-T+3 evidence.
    pub destructibles: Vec<DestructibleReceipt>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ProjectileFlightDecision {
    Progress {
        plan_id: ProjectilePlanId,
        cursor: ProjectileCursor,
        destructibles: Vec<DestructibleReceipt>,
    },
    Terminal(ProjectileTerminalProposal),
    IgnoredAfterTerminal {
        plan_id: ProjectilePlanId,
    },
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ProjectileFlightSnapshot {
    pub planned_elapsed_us: f64,
    pub committed_elapsed_us: f64,
    pub planned_distance: f64,
    pub committed_distance: f64,
    pub planned_position: ProjectileVec3,
    pub committed_position: ProjectileVec3,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ProjectileFlightError {
    #[error("projectile flight lineage is invalid")]
    InvalidLineage,
    #[error("projectile record does not form a valid active flight cursor")]
    InvalidRecord,
    #[error("projectile {projectile_id} is already tracked with conflicting state")]
    ConflictingTrack { projectile_id: String },
    #[error("projectile {projectile_id} was already retired")]
    RetiredIdentity { projectile_id: String },
    #[error("projectile {projectile_id} has no frozen first-impact continuation")]
    MissingRicochetContinuation { projectile_id: String },
    #[error("projectile {projectile_id} ricochet continuation conflicts with frozen state")]
    ConflictingRicochetContinuation { projectile_id: String },
    #[error("flight tick {received} does not follow {current}")]
    TickSequence { current: u64, received: u64 },
    #[error("native entity reference is invalid")]
    InvalidEntity,
    #[error("native target list contains a duplicate vehicle or entity")]
    DuplicateTarget,
    #[error("projectile {projectile_id} exceeds one oracle-v1 batch")]
    QueryCapacity { projectile_id: String },
    #[error("oracle query generation is exhausted")]
    QueryGenerationExhausted,
    #[error("flight tick counter is exhausted")]
    TickCounterExhausted,
    #[error("unknown projectile flight plan {plan_id:?}")]
    UnknownPlan { plan_id: ProjectilePlanId },
    #[error("flight plan {plan_id:?} was applied out of projectile order")]
    OutOfOrder { plan_id: ProjectilePlanId },
    #[error("flight plan {plan_id:?} must apply at tick {expected}, not {received}")]
    ApplyTick {
        plan_id: ProjectilePlanId,
        expected: u64,
        received: u64,
    },
    #[error("brokered oracle batch does not match flight plan {plan_id:?}")]
    BatchMismatch { plan_id: ProjectilePlanId },
    #[error("native oracle returned malformed projectile geometry")]
    MalformedReceipt,
    #[error("native oracle returned invalid destructible evidence")]
    InvalidDestructibleEvidence,
    #[error("frozen HE target facts are malformed or do not match the projectile terminal")]
    InvalidFrozenHeFacts,
    #[error("frozen HE target facts exceed 30 targets")]
    FrozenHeCapacity,
    #[error("native destructible space must be installed before projectile planning")]
    MissingDestructibleSpace,
    #[error("native destructible space is invalid or conflicts with the active installation")]
    InvalidDestructibleSpace,
    #[error(transparent)]
    Destructible(#[from] DestructibleError),
    #[error(transparent)]
    CombatRule(#[from] CombatRuleError),
}

#[derive(Clone, Debug)]
struct FlightState {
    record: ProjectileRecord,
    launch_tick: u64,
    ordinal: u64,
    planned_elapsed_us: f64,
    committed_elapsed_us: f64,
    planned_distance: f64,
    committed_distance: f64,
    planned_position: ProjectileVec3,
    committed_position: ProjectileVec3,
    limit_planned: bool,
    encountered_destructibles: BTreeSet<DestructibleKey>,
    query_generations: BTreeMap<String, u64>,
    continuation_receipt: Option<ProjectileRecord>,
}

impl FlightState {
    fn snapshot(&self) -> ProjectileFlightSnapshot {
        ProjectileFlightSnapshot {
            planned_elapsed_us: self.planned_elapsed_us,
            committed_elapsed_us: self.committed_elapsed_us,
            planned_distance: self.planned_distance,
            committed_distance: self.committed_distance,
            planned_position: self.planned_position,
            committed_position: self.committed_position,
        }
    }
}

/// Server-owned deterministic flight state.
///
/// This type intentionally does not enforce an active-projectile limit. A
/// launch must already have passed `ProjectileLedger::admit_launch`, which owns
/// the global and per-shooter caps.
#[derive(Clone, Debug)]
pub struct ProjectileFlightIntegrator {
    lineage: OracleLineage,
    current_tick: u64,
    next_ordinal: u64,
    active: BTreeMap<String, FlightState>,
    continuable: BTreeMap<String, FlightState>,
    pending: BTreeMap<ProjectilePlanId, ProjectileFlightPlan>,
    retired: BTreeSet<String>,
    retired_ordinals: BTreeSet<u64>,
    destructible_native_space_id: Option<i64>,
}

impl ProjectileFlightIntegrator {
    pub fn new(lineage: OracleLineage, current_tick: u64) -> Result<Self, ProjectileFlightError> {
        if lineage.round_id == 0 || lineage.oracle_generation == 0 {
            return Err(ProjectileFlightError::InvalidLineage);
        }
        Ok(Self {
            lineage,
            current_tick,
            next_ordinal: 1,
            active: BTreeMap::new(),
            continuable: BTreeMap::new(),
            pending: BTreeMap::new(),
            retired: BTreeSet::new(),
            retired_ordinals: BTreeSet::new(),
            destructible_native_space_id: None,
        })
    }

    pub fn lineage(&self) -> OracleLineage {
        self.lineage
    }

    pub fn current_tick(&self) -> u64 {
        self.current_tick
    }

    pub fn active_len(&self) -> usize {
        self.active.len()
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }

    pub fn continuable_len(&self) -> usize {
        self.continuable.len()
    }

    /// Install the immutable native space used by read-only destructible
    /// evidence. A space reload must create a new oracle generation/runtime.
    pub fn install_destructible_native_space_id(
        &mut self,
        native_space_id: i64,
    ) -> Result<bool, ProjectileFlightError> {
        if native_space_id <= 0 {
            return Err(ProjectileFlightError::InvalidDestructibleSpace);
        }
        match self.destructible_native_space_id {
            None => {
                self.destructible_native_space_id = Some(native_space_id);
                Ok(true)
            }
            Some(active) if active == native_space_id => Ok(false),
            Some(_) => Err(ProjectileFlightError::InvalidDestructibleSpace),
        }
    }

    pub fn snapshot(&self, projectile_id: &str) -> Option<ProjectileFlightSnapshot> {
        self.active.get(projectile_id).map(FlightState::snapshot)
    }

    /// Attach a ledger-admitted projectile at a simulation boundary.
    pub fn track(
        &mut self,
        record: ProjectileRecord,
        launch_tick: u64,
    ) -> Result<bool, ProjectileFlightError> {
        let projectile_id = record.projectile_id.clone();
        if self.retired.contains(&projectile_id) {
            return Err(ProjectileFlightError::RetiredIdentity { projectile_id });
        }
        if record.launch.round_id != self.lineage.round_id
            || record.launch.authority_epoch != self.lineage.authority_epoch
        {
            return Err(ProjectileFlightError::InvalidLineage);
        }
        if launch_tick > self.current_tick || record.ricochet_count != 0 || !valid_record(&record) {
            return Err(ProjectileFlightError::InvalidRecord);
        }
        if let Some(active) = self.active.get(&projectile_id) {
            return if active.record == record && active.launch_tick == launch_tick {
                Ok(false)
            } else {
                Err(ProjectileFlightError::ConflictingTrack { projectile_id })
            };
        }

        let elapsed_us = record.checked_through_ms as f64 * 1_000.0;
        let position = trajectory_position(&record, elapsed_us);
        let ordinal = self.next_ordinal;
        self.next_ordinal = self
            .next_ordinal
            .checked_add(1)
            .ok_or(ProjectileFlightError::TickCounterExhausted)?;
        self.active.insert(
            projectile_id,
            FlightState {
                planned_elapsed_us: elapsed_us,
                committed_elapsed_us: elapsed_us,
                planned_distance: record.checked_distance,
                committed_distance: record.checked_distance,
                planned_position: position,
                committed_position: position,
                record,
                launch_tick,
                ordinal,
                limit_planned: false,
                encountered_destructibles: BTreeSet::new(),
                query_generations: BTreeMap::new(),
                continuation_receipt: None,
            },
        );
        Ok(true)
    }

    /// Re-arm the exact projectile retained at its first direct vehicle
    /// impact after the canonical projectile ledger commits a ricochet.
    pub fn continue_ricochet(
        &mut self,
        record: ProjectileRecord,
    ) -> Result<bool, ProjectileFlightError> {
        let projectile_id = record.projectile_id.clone();
        if let Some(active) = self.active.get(&projectile_id) {
            return if active.continuation_receipt.as_ref() == Some(&record) {
                Ok(false)
            } else {
                Err(ProjectileFlightError::ConflictingRicochetContinuation { projectile_id })
            };
        }
        let frozen = self.continuable.get(&projectile_id).ok_or_else(|| {
            ProjectileFlightError::MissingRicochetContinuation {
                projectile_id: projectile_id.clone(),
            }
        })?;
        if record.launch != frozen.record.launch
            || record.team != frozen.record.team
            || record.source_vehicle != frozen.record.source_vehicle
            || record.launch_server_time_ms != frozen.record.launch_server_time_ms
            || record.ricochet_count != 1
            || record.segment_start_time_ms != record.checked_through_ms
            || record.checked_through_ms != frozen.record.checked_through_ms
            || record.checked_distance != frozen.record.checked_distance
            || record.piercing_loss != frozen.record.piercing_loss
            || distance(record.segment_origin, frozen.committed_position)
                > crate::projectile::RICOCHET_ORIGIN_TOLERANCE_M
            || !valid_record(&record)
        {
            return Err(ProjectileFlightError::ConflictingRicochetContinuation { projectile_id });
        }

        let ordinal = self.next_ordinal;
        let next_ordinal = self
            .next_ordinal
            .checked_add(1)
            .ok_or(ProjectileFlightError::TickCounterExhausted)?;
        let mut state = self
            .continuable
            .remove(&projectile_id)
            .expect("the continuation was validated above");
        let elapsed_us = record.segment_start_time_ms as f64 * 1_000.0;
        state.continuation_receipt = Some(record.clone());
        state.record = record;
        state.launch_tick = self.current_tick;
        state.ordinal = ordinal;
        state.planned_elapsed_us = elapsed_us;
        state.committed_elapsed_us = elapsed_us;
        state.planned_distance = state.record.checked_distance;
        state.committed_distance = state.record.checked_distance;
        state.planned_position = state.record.segment_origin;
        state.committed_position = state.record.segment_origin;
        state.limit_planned = false;
        self.next_ordinal = next_ordinal;
        self.retired.remove(&projectile_id);
        self.active.insert(projectile_id, state);
        Ok(true)
    }

    /// Reconcile a projectile removed by the canonical ledger.
    pub fn retire(&mut self, projectile_id: &str) -> bool {
        let removed = self
            .active
            .remove(projectile_id)
            .or_else(|| self.continuable.remove(projectile_id));
        if let Some(state) = removed.as_ref() {
            self.retired.insert(projectile_id.to_owned());
            self.retired_ordinals.insert(state.ordinal);
            self.pending
                .retain(|_, plan| plan.projectile_id != projectile_id);
        }
        removed.is_some() || self.retired.contains(projectile_id)
    }

    /// Plan exactly one fixed simulation tick for every eligible projectile.
    pub fn plan_tick(
        &mut self,
        tick: u64,
        oracle_space_entity: EntityRef,
        targets: &[FlightTarget],
        static_collision_mask: u32,
    ) -> Result<Vec<ProjectileFlightPlan>, ProjectileFlightError> {
        let expected = self
            .current_tick
            .checked_add(1)
            .ok_or(ProjectileFlightError::TickCounterExhausted)?;
        if tick != expected {
            return Err(ProjectileFlightError::TickSequence {
                current: self.current_tick,
                received: tick,
            });
        }
        validate_entity(oracle_space_entity)?;
        let targets = normalize_targets(targets)?;
        let apply_tick = tick
            .checked_add(ORACLE_PIPELINE_TICKS)
            .ok_or(ProjectileFlightError::TickCounterExhausted)?;
        let mut ids: Vec<_> = self
            .active
            .iter()
            .map(|(projectile_id, state)| (state.ordinal, projectile_id.clone()))
            .collect();
        ids.sort_by_key(|(ordinal, _)| *ordinal);
        let mut staged = Vec::new();

        for (_, projectile_id) in ids {
            let existing = self
                .active
                .get(&projectile_id)
                .expect("the stable key came from active state");
            let eligible = tick > existing.launch_tick && !existing.limit_planned;
            if !eligible {
                continue;
            }

            // Build against a detached state. No projectile advances unless
            // every active projectile fits this tick's oracle contract.
            let mut state = existing.clone();
            let destructible_native_space_id = self
                .destructible_native_space_id
                .ok_or(ProjectileFlightError::MissingDestructibleSpace)?;
            let plan = Self::build_plan(
                self.lineage,
                tick,
                apply_tick,
                &mut state,
                oracle_space_entity,
                destructible_native_space_id,
                &targets,
                static_collision_mask,
            )?;
            staged.push((projectile_id, state, plan));
        }

        let mut plans = Vec::with_capacity(staged.len());
        for (projectile_id, state, plan) in staged {
            self.active.insert(projectile_id, state);
            self.pending.insert(plan.id, plan.clone());
            plans.push(plan);
        }
        self.current_tick = tick;
        Ok(plans)
    }

    /// Apply an exact native-oracle reply already released by `OracleBroker`.
    pub fn apply_native_batch(
        &mut self,
        current_tick: u64,
        plan_id: ProjectilePlanId,
        batch: &AppliedOracleBatch,
    ) -> Result<ProjectileFlightDecision, ProjectileFlightError> {
        if self.is_retired_plan(plan_id) {
            return Ok(ProjectileFlightDecision::IgnoredAfterTerminal { plan_id });
        }
        let plan = self.pending_plan(plan_id)?.clone();
        self.validate_apply_tick(&plan, current_tick)?;
        validate_applied_batch(&plan, batch)?;
        self.ensure_oldest_plan(&plan)?;

        let state = self
            .active
            .get(&plan.projectile_id)
            .ok_or(ProjectileFlightError::UnknownPlan { plan_id })?;
        let parsed = parse_collision(&plan, batch, state)?;
        self.finish_plan(plan, current_tick, parsed)
    }

    /// Convert an exact broker timeout at T+3 into a fail-closed projectile
    /// terminal. The battle layer may additionally terminate the whole round.
    pub fn apply_native_timeout(
        &mut self,
        current_tick: u64,
        plan_id: ProjectilePlanId,
        timed_out: &TimedOutOracleBatch,
    ) -> Result<ProjectileFlightDecision, ProjectileFlightError> {
        if self.is_retired_plan(plan_id) {
            return Ok(ProjectileFlightDecision::IgnoredAfterTerminal { plan_id });
        }
        let plan = self.pending_plan(plan_id)?.clone();
        self.validate_apply_tick(&plan, current_tick)?;
        validate_request(&plan, &timed_out.request)?;
        self.ensure_oldest_plan(&plan)?;
        let state = self
            .active
            .get(&plan.projectile_id)
            .ok_or(ProjectileFlightError::UnknownPlan { plan_id })?;
        self.finish_plan(
            plan,
            current_tick,
            ParsedCollision {
                collision: Collision::OracleTimeout,
                destructibles: Vec::new(),
                piercing_loss: state.record.piercing_loss,
                encountered: state.encountered_destructibles.clone(),
            },
        )
    }

    fn build_plan(
        lineage: OracleLineage,
        tick: u64,
        apply_tick: u64,
        state: &mut FlightState,
        oracle_space_entity: EntityRef,
        destructible_native_space_id: i64,
        targets: &[FlightTarget],
        _static_collision_mask: u32,
    ) -> Result<ProjectileFlightPlan, ProjectileFlightError> {
        let start_elapsed_us = state.planned_elapsed_us;
        let max_elapsed_us = state.record.launch.max_time_ms as f64 * 1_000.0;
        let desired_end_us =
            (start_elapsed_us + delta_us_for_tick(tick) as f64).min(max_elapsed_us);
        if desired_end_us <= start_elapsed_us + EPSILON {
            return Err(ProjectileFlightError::InvalidRecord);
        }

        let maximum_step_us = curvature_limited_substep_us(state.record.launch.gravity);
        let mut cursor_us = start_elapsed_us;
        let mut cursor_distance = state.planned_distance;
        let mut segments = Vec::new();
        let mut terminal_on_clear = None;

        while cursor_us + EPSILON < desired_end_us {
            let unconstrained_end_us = desired_end_us.min(cursor_us + maximum_step_us);
            let start = trajectory_position(&state.record, cursor_us);
            let unconstrained_end = trajectory_position(&state.record, unconstrained_end_us);
            let chord_length = distance(start, unconstrained_end);
            let remaining_distance = (state.record.launch.max_distance - cursor_distance).max(0.0);
            let distance_fraction = if chord_length > remaining_distance {
                terminal_on_clear = Some(LimitTerminal::MaxDistance);
                if chord_length <= EPSILON {
                    0.0
                } else {
                    remaining_distance / chord_length
                }
            } else if remaining_distance <= EPSILON {
                terminal_on_clear = Some(LimitTerminal::MaxDistance);
                0.0
            } else {
                1.0
            };
            let end = lerp(start, unconstrained_end, distance_fraction);
            let end_us = cursor_us + (unconstrained_end_us - cursor_us) * distance_fraction;
            let end_distance = (cursor_distance + chord_length * distance_fraction)
                .min(state.record.launch.max_distance);
            segments.push(FlightSegment {
                index: segments.len(),
                start,
                end,
                start_elapsed_us: cursor_us,
                end_elapsed_us: end_us,
                start_distance: cursor_distance,
                end_distance,
            });
            cursor_us = end_us;
            cursor_distance = end_distance;
            if terminal_on_clear.is_some() {
                break;
            }
        }

        if terminal_on_clear.is_none() && desired_end_us + EPSILON >= max_elapsed_us {
            terminal_on_clear = Some(LimitTerminal::MaxTime);
        }
        if segments.is_empty() {
            return Err(ProjectileFlightError::InvalidRecord);
        }

        let target_count = targets
            .iter()
            .filter(|target| target.vehicle != state.record.launch.shooter)
            .count();
        let query_count = segments
            .len()
            .saturating_mul(1usize.saturating_add(target_count));
        let primitive_count = segments
            .len()
            .saturating_mul(MAX_DESTRUCTIBLE_CANDIDATES.saturating_add(target_count));
        if query_count > MAX_ORACLE_BATCH_QUERIES
            || primitive_count > MAX_ORACLE_PRIMITIVE_OPERATIONS
        {
            return Err(ProjectileFlightError::QueryCapacity {
                projectile_id: state.record.projectile_id.clone(),
            });
        }

        let lane = tick % QUERY_LANES;
        let identity = compact_identity(&state.record);
        let mut queries = Vec::with_capacity(query_count);
        let mut bindings = BTreeMap::new();
        let shell_kind = destructible_shell_kind(&state.record)?;
        let mut query_id = 1u64;
        let shooter = state.record.launch.shooter;
        for segment in &segments {
            let key = format!("pf:{identity}:d:s{}:l{lane}", segment.index);
            let generation = next_query_generation(state, &key)?;
            queries.push(OracleV1Query {
                query_id,
                key,
                query_generation: generation,
                entity: oracle_space_entity,
                operation: OracleOperation::DestructibleShotEvidence(
                    DestructibleShotEvidenceQuery {
                        space_id: destructible_native_space_id,
                        start: protocol_vec(segment.start),
                        end: protocol_vec(segment.end),
                        shell_kind,
                    },
                ),
            });
            bindings.insert(
                query_id,
                QueryBinding::Destructibles {
                    segment_index: segment.index,
                },
            );
            query_id += 1;
            for target in targets.iter().filter(|target| target.vehicle != shooter) {
                let key = format!(
                    "pf:{identity}:v:{}:{}:s{}:l{lane}",
                    target.entity.entity_id, target.entity.generation, segment.index
                );
                let generation = next_query_generation(state, &key)?;
                queries.push(OracleV1Query {
                    query_id,
                    key,
                    query_generation: generation,
                    entity: target.entity,
                    operation: OracleOperation::VehicleHitTest {
                        start: protocol_vec(segment.start),
                        end: protocol_vec(segment.end),
                        target: target.entity,
                    },
                });
                bindings.insert(
                    query_id,
                    QueryBinding::Vehicle {
                        segment_index: segment.index,
                        target: *target,
                    },
                );
                query_id += 1;
            }
        }

        state.planned_elapsed_us = cursor_us;
        state.planned_distance = cursor_distance;
        state.planned_position = segments.last().expect("non-empty segments").end;
        state.limit_planned = terminal_on_clear.is_some();
        let id = ProjectilePlanId {
            issued_tick: tick,
            projectile_ordinal: state.ordinal,
        };
        Ok(ProjectileFlightPlan {
            id,
            lineage,
            projectile_id: state.record.projectile_id.clone(),
            issued_tick: tick,
            apply_tick,
            segments,
            queries,
            terminal_on_clear,
            bindings,
        })
    }

    fn pending_plan(
        &self,
        plan_id: ProjectilePlanId,
    ) -> Result<&ProjectileFlightPlan, ProjectileFlightError> {
        self.pending
            .get(&plan_id)
            .ok_or(ProjectileFlightError::UnknownPlan { plan_id })
    }

    fn validate_apply_tick(
        &self,
        plan: &ProjectileFlightPlan,
        current_tick: u64,
    ) -> Result<(), ProjectileFlightError> {
        if current_tick != plan.apply_tick {
            return Err(ProjectileFlightError::ApplyTick {
                plan_id: plan.id,
                expected: plan.apply_tick,
                received: current_tick,
            });
        }
        Ok(())
    }

    fn ensure_oldest_plan(&self, plan: &ProjectileFlightPlan) -> Result<(), ProjectileFlightError> {
        let oldest = self
            .pending
            .values()
            .filter(|pending| pending.projectile_id == plan.projectile_id)
            .map(|pending| pending.issued_tick)
            .min();
        if oldest != Some(plan.issued_tick) {
            return Err(ProjectileFlightError::OutOfOrder { plan_id: plan.id });
        }
        Ok(())
    }

    fn finish_plan(
        &mut self,
        plan: ProjectileFlightPlan,
        current_tick: u64,
        parsed: ParsedCollision,
    ) -> Result<ProjectileFlightDecision, ProjectileFlightError> {
        let state = self
            .active
            .get_mut(&plan.projectile_id)
            .ok_or(ProjectileFlightError::UnknownPlan { plan_id: plan.id })?;
        let first = plan.segments.first().expect("plans always have segments");
        if !close(state.committed_elapsed_us, first.start_elapsed_us)
            || !close(state.committed_distance, first.start_distance)
        {
            return Err(ProjectileFlightError::OutOfOrder { plan_id: plan.id });
        }
        state.record.piercing_loss = parsed.piercing_loss;
        state.encountered_destructibles = parsed.encountered;

        let terminal = match parsed.collision {
            Collision::Hit(hit) => Some(terminal_from_hit(
                state,
                &plan,
                current_tick,
                hit,
                parsed.destructibles.clone(),
            )),
            Collision::OracleTimeout => Some(terminal_at(
                state,
                &plan,
                current_tick,
                first.start_elapsed_us,
                first.start_distance,
                first.start,
                ProjectileTerminalCause::OracleTimeout,
                ProjectileOutcome::Expired,
                false,
                Vec::new(),
            )),
            Collision::Clear => match plan.terminal_on_clear {
                Some(LimitTerminal::MaxDistance) => {
                    let last = plan.segments.last().expect("plans always have segments");
                    Some(terminal_at(
                        state,
                        &plan,
                        current_tick,
                        last.end_elapsed_us,
                        last.end_distance,
                        last.end,
                        ProjectileTerminalCause::MaxDistance,
                        ProjectileOutcome::Miss,
                        false,
                        parsed.destructibles.clone(),
                    ))
                }
                Some(LimitTerminal::MaxTime) => {
                    let last = plan.segments.last().expect("plans always have segments");
                    Some(terminal_at(
                        state,
                        &plan,
                        current_tick,
                        last.end_elapsed_us,
                        last.end_distance,
                        last.end,
                        ProjectileTerminalCause::MaxTime,
                        ProjectileOutcome::Expired,
                        false,
                        parsed.destructibles.clone(),
                    ))
                }
                None => None,
            },
        };

        self.pending.remove(&plan.id);
        if let Some(proposal) = terminal {
            let ordinal = state.ordinal;
            let retain_first_direct = state.record.ricochet_count == 0
                && matches!(proposal.cause, ProjectileTerminalCause::Direct { .. });
            if retain_first_direct {
                state.record.checked_through_ms = proposal.resolution.resolved_time_ms;
                state.record.checked_distance = proposal.resolution.checked_distance;
                state.record.piercing_loss = proposal.resolution.piercing_loss;
                let elapsed_us = proposal.resolution.resolved_time_ms as f64 * 1_000.0;
                state.planned_elapsed_us = elapsed_us;
                state.committed_elapsed_us = elapsed_us;
                state.planned_distance = proposal.resolution.checked_distance;
                state.committed_distance = proposal.resolution.checked_distance;
                if let Some(impact) = proposal.resolution.impact {
                    state.planned_position = impact;
                    state.committed_position = impact;
                }
                state.limit_planned = false;
            }
            let removed = self
                .active
                .remove(&plan.projectile_id)
                .expect("terminal projectile remained active");
            if retain_first_direct {
                self.continuable.insert(plan.projectile_id.clone(), removed);
            }
            self.retired.insert(plan.projectile_id.clone());
            self.retired_ordinals.insert(ordinal);
            self.pending
                .retain(|_, pending| pending.projectile_id != plan.projectile_id);
            return Ok(ProjectileFlightDecision::Terminal(proposal));
        }

        let last = plan.segments.last().expect("plans always have segments");
        let base_checked_ms = state.record.checked_through_ms;
        let checked_through_ms = rounded_millis(last.end_elapsed_us).max(base_checked_ms);
        state.committed_elapsed_us = last.end_elapsed_us;
        state.committed_distance = last.end_distance;
        state.committed_position = last.end;
        state.record.checked_through_ms = checked_through_ms;
        state.record.checked_distance = round6(last.end_distance);
        Ok(ProjectileFlightDecision::Progress {
            plan_id: plan.id,
            cursor: ProjectileCursor {
                projectile_id: plan.projectile_id,
                base_checked_ms,
                checked_through_ms,
                checked_distance: round6(last.end_distance),
                piercing_loss: round6(state.record.piercing_loss),
                penetration_factor: round6(state.record.launch.penetration_factor),
            },
            destructibles: parsed.destructibles,
        })
    }

    fn is_retired_plan(&self, plan_id: ProjectilePlanId) -> bool {
        self.retired_ordinals.contains(&plan_id.projectile_ordinal)
    }
}

fn next_query_generation(state: &mut FlightState, key: &str) -> Result<u64, ProjectileFlightError> {
    let generation = state
        .query_generations
        .get(key)
        .copied()
        .unwrap_or(0)
        .checked_add(1)
        .ok_or(ProjectileFlightError::QueryGenerationExhausted)?;
    state.query_generations.insert(key.to_owned(), generation);
    Ok(generation)
}

#[derive(Clone, Debug)]
enum Collision {
    Clear,
    Hit(CollisionHit),
    OracleTimeout,
}

#[derive(Clone, Debug)]
struct ParsedCollision {
    collision: Collision,
    destructibles: Vec<DestructibleReceipt>,
    piercing_loss: f64,
    encountered: BTreeSet<DestructibleKey>,
}

#[derive(Clone, Debug)]
struct CollisionHit {
    segment_index: usize,
    fraction: f64,
    kind: CollisionKind,
}

#[derive(Clone, Debug)]
enum CollisionKind {
    DestructibleBacking(DestructibleStaticCollision),
    Destructible(DestructibleReceipt),
    Vehicle {
        target: FlightTarget,
        hit: VehicleHit,
    },
}

fn validate_applied_batch(
    plan: &ProjectileFlightPlan,
    batch: &AppliedOracleBatch,
) -> Result<(), ProjectileFlightError> {
    validate_request(plan, &batch.request)?;
    if batch.reply.protocol_version != ORACLE_PROTOCOL_VERSION
        || batch.reply.lineage() != plan.lineage
        || batch.reply.batch_seq != batch.request.batch_seq
        || batch.reply.issued_tick != plan.issued_tick
        || batch.reply.apply_tick != plan.apply_tick
        || batch.reply.world_revision != batch.request.world_revision
        || batch.reply.oracle_frame_seq == 0
    {
        return Err(ProjectileFlightError::BatchMismatch { plan_id: plan.id });
    }
    Ok(())
}

fn validate_request(
    plan: &ProjectileFlightPlan,
    request: &OracleV1BatchRequest,
) -> Result<(), ProjectileFlightError> {
    if request.protocol_version != ORACLE_PROTOCOL_VERSION
        || request.batch_seq == 0
        || request.lineage() != plan.lineage
        || request.issued_tick != plan.issued_tick
        || request.apply_tick != plan.apply_tick
        || request.queries != plan.queries
    {
        return Err(ProjectileFlightError::BatchMismatch { plan_id: plan.id });
    }
    Ok(())
}

fn parse_collision(
    plan: &ProjectileFlightPlan,
    batch: &AppliedOracleBatch,
    state: &FlightState,
) -> Result<ParsedCollision, ProjectileFlightError> {
    let results: BTreeMap<_, _> = batch
        .reply
        .results
        .iter()
        .map(|result| (result.query_id, result))
        .collect();
    if batch.reply.results.len() != plan.queries.len() || results.len() != plan.queries.len() {
        return Err(ProjectileFlightError::MalformedReceipt);
    }

    let mut native_candidates = Vec::new();
    let mut destructible_evidence = BTreeMap::<usize, DestructibleShotEvidence>::new();
    for query in &plan.queries {
        let result = results
            .get(&query.query_id)
            .ok_or(ProjectileFlightError::MalformedReceipt)?;
        if result.key != query.key
            || result.query_generation != query.query_generation
            || result.entity != query.entity
        {
            return Err(ProjectileFlightError::MalformedReceipt);
        }
        let outcome = match &result.status {
            OracleV1ResultStatus::Unavailable { .. } => {
                return Ok(ParsedCollision {
                    collision: Collision::OracleTimeout,
                    destructibles: Vec::new(),
                    piercing_loss: state.record.piercing_loss,
                    encountered: state.encountered_destructibles.clone(),
                });
            }
            OracleV1ResultStatus::Ok { outcome } => outcome,
        };
        match (
            plan.bindings
                .get(&query.query_id)
                .ok_or(ProjectileFlightError::MalformedReceipt)?,
            outcome,
        ) {
            (
                QueryBinding::Destructibles { segment_index },
                QueryOutcome::DestructibleShotEvidence(evidence),
            ) => {
                let Some(segment) = plan.segments.get(*segment_index) else {
                    return Err(ProjectileFlightError::MalformedReceipt);
                };
                if destructible_evidence
                    .insert(*segment_index, evidence.clone())
                    .is_some()
                {
                    return Err(ProjectileFlightError::MalformedReceipt);
                }
                if let Some(hit) = evidence.static_collision {
                    let length = segment.length();
                    if length <= EPSILON {
                        return Err(ProjectileFlightError::InvalidDestructibleEvidence);
                    }
                    native_candidates.push(CollisionHit {
                        segment_index: *segment_index,
                        fraction: hit.distance / length,
                        kind: CollisionKind::DestructibleBacking(hit),
                    });
                }
            }
            (
                QueryBinding::Vehicle {
                    segment_index,
                    target,
                },
                QueryOutcome::VehicleHitTest { hit },
            ) => {
                if let Some(hit) = hit {
                    let (start, end) = match &query.operation {
                        OracleOperation::VehicleHitTest { start, end, .. } => (start, end),
                        _ => return Err(ProjectileFlightError::MalformedReceipt),
                    };
                    validate_vehicle_hit(hit, start, end, query.query_id)?;
                    native_candidates.push(CollisionHit {
                        segment_index: *segment_index,
                        fraction: hit.fraction as f64,
                        kind: CollisionKind::Vehicle {
                            target: *target,
                            hit: hit.clone(),
                        },
                    });
                }
            }
            _ => return Err(ProjectileFlightError::MalformedReceipt),
        }
    }

    if destructible_evidence.len() != plan.segments.len() {
        return Err(ProjectileFlightError::MalformedReceipt);
    }

    native_candidates.sort_by(compare_collision);
    let shot = projectile_shot_info(&state.record)?;
    let mut piercing_loss = state.record.piercing_loss;
    let mut encountered = state.encountered_destructibles.clone();
    let mut receipts = Vec::new();
    for segment in &plan.segments {
        let nearest_native = native_candidates
            .iter()
            .find(|candidate| candidate.segment_index == segment.index)
            .cloned();
        let native_distance = nearest_native
            .as_ref()
            .map(|candidate| candidate.fraction * segment.length());
        let evidence = destructible_evidence
            .get(&segment.index)
            .expect("every planned segment has exact destructible evidence");
        let fall_yaw = shot_fall_yaw(segment);
        for candidate in &evidence.candidates {
            if native_distance.is_some_and(|distance| candidate.entry_distance > distance + EPSILON)
            {
                break;
            }
            let receipt = DestructibleAuthority::shot_receipt(candidate, fall_yaw)?;
            if !encountered.insert(receipt.key) {
                continue;
            }
            receipts.push(receipt.clone());
            piercing_loss += candidate.piercing_loss;
            if !piercing_loss.is_finite() || piercing_loss > 100_000.0 {
                return Err(ProjectileFlightError::InvalidDestructibleEvidence);
            }
            let distance_from_launch = segment.start_distance + candidate.entry_distance;
            let can_continue = candidate.ap_through
                && sampled_piercing_with_base_multiplier(
                    &shot,
                    distance_from_launch,
                    state.record.launch.penetration_factor,
                    piercing_loss,
                    state.record.base_penetration_multiplier,
                )
                .map_err(|_| ProjectileFlightError::InvalidDestructibleEvidence)?
                    >= 1.0;
            if !can_continue {
                let length = segment.length();
                if length <= EPSILON {
                    return Err(ProjectileFlightError::InvalidDestructibleEvidence);
                }
                return Ok(ParsedCollision {
                    collision: Collision::Hit(CollisionHit {
                        segment_index: segment.index,
                        fraction: candidate.entry_distance / length,
                        kind: CollisionKind::Destructible(receipt),
                    }),
                    destructibles: receipts,
                    piercing_loss,
                    encountered,
                });
            }
        }
        if let Some(hit) = nearest_native {
            return Ok(ParsedCollision {
                collision: Collision::Hit(hit),
                destructibles: receipts,
                piercing_loss,
                encountered,
            });
        }
    }
    Ok(ParsedCollision {
        collision: Collision::Clear,
        destructibles: receipts,
        piercing_loss,
        encountered,
    })
}

fn compare_collision(left: &CollisionHit, right: &CollisionHit) -> Ordering {
    left.segment_index
        .cmp(&right.segment_index)
        .then_with(|| {
            if (left.fraction - right.fraction).abs() <= COLLISION_TIE_EPSILON {
                Ordering::Equal
            } else {
                left.fraction.total_cmp(&right.fraction)
            }
        })
        // Python gives static world collision the tie.
        .then_with(|| collision_priority(&left.kind).cmp(&collision_priority(&right.kind)))
        .then_with(|| collision_target_key(&left.kind).cmp(&collision_target_key(&right.kind)))
}

fn collision_priority(kind: &CollisionKind) -> u8 {
    match kind {
        CollisionKind::DestructibleBacking(_) => 0,
        CollisionKind::Destructible(_) => 1,
        CollisionKind::Vehicle { .. } => 2,
    }
}

fn collision_target_key(kind: &CollisionKind) -> Option<(VehicleKey, (i64, u64))> {
    match kind {
        CollisionKind::DestructibleBacking(_) | CollisionKind::Destructible(_) => None,
        CollisionKind::Vehicle { target, .. } => Some((
            target.vehicle,
            (target.entity.entity_id, target.entity.generation),
        )),
    }
}

fn terminal_from_hit(
    state: &FlightState,
    plan: &ProjectileFlightPlan,
    current_tick: u64,
    hit: CollisionHit,
    destructibles: Vec<DestructibleReceipt>,
) -> ProjectileTerminalProposal {
    let segment = &plan.segments[hit.segment_index];
    let elapsed_us = lerp_scalar(
        segment.start_elapsed_us,
        segment.end_elapsed_us,
        hit.fraction,
    );
    let checked_distance = lerp_scalar(segment.start_distance, segment.end_distance, hit.fraction);
    let position = lerp(segment.start, segment.end, hit.fraction);
    let cause = match hit.kind {
        CollisionKind::DestructibleBacking(native_hit) => {
            ProjectileTerminalCause::DestructibleBacking { native_hit }
        }
        CollisionKind::Destructible(receipt) => ProjectileTerminalCause::Destructible { receipt },
        CollisionKind::Vehicle { target, hit } if target.wreck => ProjectileTerminalCause::Wreck {
            target: target.vehicle,
            entity: target.entity,
            native_hit: hit,
        },
        CollisionKind::Vehicle { target, hit } => ProjectileTerminalCause::Direct {
            target: target.vehicle,
            entity: target.entity,
            native_hit: hit,
        },
    };
    terminal_at(
        state,
        plan,
        current_tick,
        elapsed_us,
        checked_distance,
        position,
        cause,
        ProjectileOutcome::Impact,
        true,
        destructibles,
    )
}

#[allow(clippy::too_many_arguments)]
fn terminal_at(
    state: &FlightState,
    plan: &ProjectileFlightPlan,
    current_tick: u64,
    elapsed_us: f64,
    checked_distance: f64,
    position: ProjectileVec3,
    cause: ProjectileTerminalCause,
    outcome: ProjectileOutcome,
    has_impact: bool,
    destructibles: Vec<DestructibleReceipt>,
) -> ProjectileTerminalProposal {
    let base_checked_ms = state.record.checked_through_ms;
    ProjectileTerminalProposal {
        plan_id: plan.id,
        issued_tick: plan.issued_tick,
        applied_tick: current_tick,
        cause,
        destructibles,
        resolution: ProjectileResolution {
            round_id: state.record.launch.round_id,
            authority_epoch: state.record.launch.authority_epoch,
            projectile_id: state.record.projectile_id.clone(),
            base_checked_ms,
            outcome,
            resolved_time_ms: rounded_millis(elapsed_us).max(base_checked_ms),
            checked_distance: round6(checked_distance),
            piercing_loss: round6(state.record.piercing_loss),
            penetration_factor: round6(state.record.launch.penetration_factor),
            impact: has_impact.then(|| rounded_vec(position)),
        },
    }
}

fn destructible_shell_kind(
    record: &ProjectileRecord,
) -> Result<DestructibleShellKind, ProjectileFlightError> {
    match record.launch.source_shot.shell.kind.as_str() {
        "ARMOR_PIERCING" => Ok(DestructibleShellKind::ArmorPiercing),
        "ARMOR_PIERCING_HE" => Ok(DestructibleShellKind::ArmorPiercingHe),
        "ARMOR_PIERCING_CR" => Ok(DestructibleShellKind::ArmorPiercingCr),
        "HOLLOW_CHARGE" => Ok(DestructibleShellKind::HollowCharge),
        "HIGH_EXPLOSIVE" => Ok(DestructibleShellKind::HighExplosive),
        _ => Err(ProjectileFlightError::InvalidRecord),
    }
}

fn projectile_shot_info(record: &ProjectileRecord) -> Result<ShotInfo, ProjectileFlightError> {
    let source = &record.launch.source_shot;
    let kind = match destructible_shell_kind(record)? {
        DestructibleShellKind::ArmorPiercing => ShellKind::ArmorPiercing,
        DestructibleShellKind::ArmorPiercingHe => ShellKind::ArmorPiercingHe,
        DestructibleShellKind::ArmorPiercingCr => ShellKind::ArmorPiercingCr,
        DestructibleShellKind::HollowCharge => ShellKind::HollowCharge,
        DestructibleShellKind::HighExplosive => ShellKind::HighExplosive,
    };
    let shot = ShotInfo {
        kind,
        caliber_mm: source.shell.caliber,
        damage: source.shell.damage,
        explosion_radius_m: source.shell.explosion_radius,
        piercing_power: source.piercing_power,
        max_distance_m: source.max_distance,
    };
    shot.validate()
        .map_err(|_| ProjectileFlightError::InvalidRecord)?;
    Ok(shot)
}

fn shot_fall_yaw(segment: &FlightSegment) -> f64 {
    let x = segment.end.x - segment.start.x;
    let z = segment.end.z - segment.start.z;
    x.atan2(z)
}

pub fn ballistic_position(
    origin: ProjectileVec3,
    velocity: ProjectileVec3,
    gravity: f64,
    elapsed_seconds: f64,
) -> ProjectileVec3 {
    let elapsed = elapsed_seconds.max(0.0);
    let half_time_squared = 0.5 * elapsed * elapsed;
    ProjectileVec3 {
        x: origin.x + velocity.x * elapsed,
        y: origin.y + velocity.y * elapsed - gravity * half_time_squared,
        z: origin.z + velocity.z * elapsed,
    }
}

pub fn parabolic_chord_error(gravity: f64, duration_seconds: f64) -> f64 {
    gravity.abs() * duration_seconds.max(0.0).powi(2) / 8.0
}

pub fn curvature_limited_substep(gravity: f64) -> f64 {
    if gravity.abs() <= 1.0e-12 {
        return PROJECTILE_MAX_SUBSTEP_SECONDS;
    }
    let error_limited = (8.0 * PROJECTILE_MAX_CHORD_ERROR_METERS / gravity.abs()).sqrt();
    PROJECTILE_MIN_SUBSTEP_SECONDS.max(PROJECTILE_MAX_SUBSTEP_SECONDS.min(error_limited))
}

fn curvature_limited_substep_us(gravity: f64) -> f64 {
    // Flooring the integer-microsecond chord keeps the sagitta at or below the
    // requested bound while making replay boundaries platform-independent.
    (curvature_limited_substep(gravity) * MICROS_PER_SECOND)
        .floor()
        .max(PROJECTILE_MIN_SUBSTEP_SECONDS * MICROS_PER_SECOND)
}

fn trajectory_position(record: &ProjectileRecord, elapsed_us: f64) -> ProjectileVec3 {
    let segment_elapsed_us = elapsed_us - record.segment_start_time_ms as f64 * 1_000.0;
    ballistic_position(
        record.segment_origin,
        record.segment_velocity,
        record.launch.gravity,
        segment_elapsed_us / MICROS_PER_SECOND,
    )
}

fn valid_record(record: &ProjectileRecord) -> bool {
    let max_time_us = record.launch.max_time_ms as f64 * 1_000.0;
    let segment_speed = record.segment_velocity.magnitude();
    let expected_multiplier = if record.ricochet_count == 0 {
        Some(1.0)
    } else if record.ricochet_count == 1 {
        first_ricochet_penetration_multiplier(record.launch.source_shot.shell.kind.as_str())
    } else {
        None
    };
    record.projectile_id.len() <= 96
        && !record.projectile_id.is_empty()
        && record.launch.gravity.is_finite()
        && record.launch.gravity > 0.0
        && record.launch.max_distance.is_finite()
        && record.launch.max_distance > 0.0
        && record.checked_through_ms < record.launch.max_time_ms
        && record.segment_start_time_ms <= record.checked_through_ms
        && record.segment_start_time_ms < record.launch.max_time_ms
        && valid_world_position(record.segment_origin)
        && record.segment_velocity.x.is_finite()
        && record.segment_velocity.y.is_finite()
        && record.segment_velocity.z.is_finite()
        && (0.001..=3_000.0).contains(&segment_speed)
        && expected_multiplier == Some(record.base_penetration_multiplier)
        && (record.ricochet_count != 0
            || (record.segment_origin == record.launch.origin
                && record.segment_velocity == record.launch.velocity
                && record.segment_start_time_ms == 0))
        && record.checked_distance.is_finite()
        && record.checked_distance >= 0.0
        && record.checked_distance + EPSILON < record.launch.max_distance
        && (record.checked_through_ms as f64 * 1_000.0) < max_time_us
}

fn valid_world_position(value: ProjectileVec3) -> bool {
    value.x.is_finite()
        && value.y.is_finite()
        && value.z.is_finite()
        && (-5_000.0..=5_000.0).contains(&value.x)
        && (-1_000.0..=3_000.0).contains(&value.y)
        && (-5_000.0..=5_000.0).contains(&value.z)
}

fn validate_entity(entity: EntityRef) -> Result<(), ProjectileFlightError> {
    if entity.entity_id <= 0 || entity.generation == 0 {
        return Err(ProjectileFlightError::InvalidEntity);
    }
    Ok(())
}

fn normalize_targets(targets: &[FlightTarget]) -> Result<Vec<FlightTarget>, ProjectileFlightError> {
    let mut normalized = targets.to_vec();
    normalized.sort_by_key(|target| {
        (
            target.vehicle,
            target.entity.entity_id,
            target.entity.generation,
        )
    });
    let mut vehicles = BTreeSet::new();
    let mut entities = BTreeSet::new();
    for target in &normalized {
        validate_entity(target.entity)?;
        if target.vehicle.id == 0
            || target.vehicle.id > MAX_COMBAT_ID
            || !vehicles.insert(target.vehicle)
            || !entities.insert((target.entity.entity_id, target.entity.generation))
        {
            return Err(ProjectileFlightError::DuplicateTarget);
        }
    }
    Ok(normalized)
}

fn validate_vehicle_hit(
    hit: &VehicleHit,
    start: &crate::protocol::Vec3,
    end: &crate::protocol::Vec3,
    query_id: u64,
) -> Result<(), ProjectileFlightError> {
    validate_vehicle_hit_receipt(hit, start, end, query_id)
        .map_err(|_| ProjectileFlightError::MalformedReceipt)
}

fn compact_identity(record: &ProjectileRecord) -> String {
    let kind = match record.launch.shooter.kind {
        crate::combat::VehicleKind::Player => 'p',
        crate::combat::VehicleKind::Bot => 'b',
    };
    format!(
        "{}:{kind}:{}:{}",
        record.launch.round_id, record.launch.shooter.id, record.launch.shot_seq
    )
}

fn protocol_vec(value: ProjectileVec3) -> crate::protocol::Vec3 {
    crate::protocol::Vec3 {
        x: value.x as f32,
        y: value.y as f32,
        z: value.z as f32,
    }
}

fn distance(first: ProjectileVec3, second: ProjectileVec3) -> f64 {
    let x = second.x - first.x;
    let y = second.y - first.y;
    let z = second.z - first.z;
    (x * x + y * y + z * z).sqrt()
}

fn lerp(first: ProjectileVec3, second: ProjectileVec3, fraction: f64) -> ProjectileVec3 {
    let value = fraction.clamp(0.0, 1.0);
    ProjectileVec3 {
        x: lerp_scalar(first.x, second.x, value),
        y: lerp_scalar(first.y, second.y, value),
        z: lerp_scalar(first.z, second.z, value),
    }
}

fn lerp_scalar(first: f64, second: f64, fraction: f64) -> f64 {
    first + (second - first) * fraction.clamp(0.0, 1.0)
}

fn rounded_millis(elapsed_us: f64) -> u64 {
    (elapsed_us / 1_000.0).round().max(0.0) as u64
}

fn rounded_vec(value: ProjectileVec3) -> ProjectileVec3 {
    ProjectileVec3 {
        x: round6(value.x),
        y: round6(value.y),
        z: round6(value.z),
    }
}

fn round6(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn close(left: f64, right: f64) -> bool {
    (left - right).abs() <= 1.0e-6_f64.max(right.abs() * 1.0e-9)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::combat::{VehicleKey, VehicleKind};
    use crate::projectile::{ProjectileLaunch, SourceShell, SourceShot};
    use crate::protocol::{
        DestructibleKind as WireDestructibleKind, DestructibleShotCandidate, OracleV1BatchReply,
        OracleV1Result, OracleV1ResultStatus, QueryOutcome, Vec3, VehicleHitLayer,
        VehicleHitMaterial, VehicleInternalCriticalHit,
    };

    fn lineage() -> OracleLineage {
        OracleLineage {
            round_id: 7,
            authority_epoch: 3,
            oracle_generation: 2,
        }
    }

    fn shooter() -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id: 1,
        }
    }

    fn launch_with(
        shot_seq: u64,
        origin: ProjectileVec3,
        velocity: ProjectileVec3,
        gravity: f64,
        max_distance: f64,
        max_time_ms: u64,
    ) -> ProjectileLaunch {
        let speed = velocity.magnitude();
        ProjectileLaunch {
            round_id: 7,
            authority_epoch: 3,
            shooter: shooter(),
            shot_seq,
            shell_index: 0,
            origin,
            velocity,
            gravity,
            max_distance,
            max_time_ms,
            is_he: false,
            splash_radius: 0.0,
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot: SourceShot {
                speed,
                gravity,
                max_distance,
                piercing_power: [100.0, 80.0],
                deadeye: false,
                shell: SourceShell {
                    kind: "ARMOR_PIERCING".to_owned(),
                    caliber: 75.0,
                    damage: [100.0, 0.0],
                    explosion_radius: 0.0,
                    explosion_damage_factor: None,
                    explosion_damage_absorption_factor: None,
                    explosion_edge_damage_factor: None,
                },
            },
            fire_intent_seq: Some(shot_seq),
            fire_input_seq: Some(4),
        }
    }

    fn admitted_record(
        origin: ProjectileVec3,
        velocity: ProjectileVec3,
        gravity: f64,
        max_distance: f64,
        max_time_ms: u64,
    ) -> ProjectileRecord {
        admitted_record_with_shot(1, origin, velocity, gravity, max_distance, max_time_ms)
    }

    fn admitted_record_with_shot(
        shot_seq: u64,
        origin: ProjectileVec3,
        velocity: ProjectileVec3,
        gravity: f64,
        max_distance: f64,
        max_time_ms: u64,
    ) -> ProjectileRecord {
        use crate::projectile::{LaunchAdmission, LaunchContext, ProjectileLedger};
        let launch = launch_with(
            shot_seq,
            origin,
            velocity,
            gravity,
            max_distance,
            max_time_ms,
        );
        let mut ledger = ProjectileLedger::new();
        match ledger
            .admit_launch(
                launch,
                LaunchContext {
                    round_id: 7,
                    authority_epoch: 3,
                    shooter: shooter(),
                    team: 1,
                    source_vehicle: "ussr:R11_MS-1".to_owned(),
                    expected_shot_seq: shot_seq,
                    server_time_ms: 0,
                },
            )
            .unwrap()
        {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        }
    }

    fn admitted_he_record() -> ProjectileRecord {
        use crate::projectile::{LaunchAdmission, LaunchContext, ProjectileLedger};
        let mut launch = launch_with(
            1,
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 100.0,
                y: 0.0,
                z: 0.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        launch.is_he = true;
        launch.splash_radius = 10.0;
        launch.source_shot.shell.kind = "HIGH_EXPLOSIVE".to_owned();
        launch.source_shot.shell.damage = [500.0, 150.0];
        launch.source_shot.shell.explosion_radius = 10.0;
        launch.source_shot.shell.explosion_damage_factor =
            Some(crate::combat_rules::DEFAULT_HE_SPLASH_FRACTION);
        launch.source_shot.shell.explosion_damage_absorption_factor =
            Some(crate::combat_rules::DEFAULT_HE_ARMOR_FACTOR);
        launch.source_shot.shell.explosion_edge_damage_factor =
            Some(crate::combat_rules::DEFAULT_HE_EDGE_FACTOR);
        let mut ledger = ProjectileLedger::new();
        match ledger
            .admit_launch(
                launch,
                LaunchContext {
                    round_id: 7,
                    authority_epoch: 3,
                    shooter: shooter(),
                    team: 1,
                    source_vehicle: "ussr:R11_MS-1".to_owned(),
                    expected_shot_seq: 1,
                    server_time_ms: 0,
                },
            )
            .unwrap()
        {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        }
    }

    fn space() -> EntityRef {
        EntityRef {
            entity_id: 900,
            generation: 1,
        }
    }

    fn target(id: u64, entity_id: i64, wreck: bool) -> FlightTarget {
        FlightTarget {
            vehicle: VehicleKey {
                kind: VehicleKind::Bot,
                id,
            },
            entity: EntityRef {
                entity_id,
                generation: 1,
            },
            wreck,
        }
    }

    fn vehicle_hit(operation: &OracleOperation, fraction: f32) -> VehicleHit {
        let (start, end) = match operation {
            OracleOperation::VehicleHitTest { start, end, .. } => (start, end),
            _ => panic!("vehicle hit fixture requires a vehicle query"),
        };
        let dx = f64::from(end.x) - f64::from(start.x);
        let dy = f64::from(end.y) - f64::from(start.y);
        let dz = f64::from(end.z) - f64::from(start.z);
        let distance_m = (dx * dx + dy * dy + dz * dz).sqrt() * f64::from(fraction);
        VehicleHit {
            fraction,
            position: Vec3 {
                x: start.x + (end.x - start.x) * fraction,
                y: start.y + (end.y - start.y) * fraction,
                z: start.z + (end.z - start.z) * fraction,
            },
            normal: Vec3 {
                x: 0.0,
                y: 0.0,
                z: -1.0,
            },
            hit_part: "vehicleHull".to_owned(),
            layers: vec![VehicleHitLayer {
                distance_m,
                hit_angle_cos: 0.75,
                component: Some("vehicleHull".to_owned()),
                material: VehicleHitMaterial {
                    armor_mm: 60.0,
                    vehicle_damage_factor: 1.0,
                    kind: Some(1),
                    native_identity: Some(1001),
                    collide_once_only: false,
                    use_hit_angle: true,
                    check_caliber_for_hit_angle_norm: true,
                    may_ricochet: true,
                    check_caliber_for_ricochet: true,
                },
                critical_target: Some(VehicleCriticalTarget::Device(
                    VehicleCriticalDeviceName::EngineHealth,
                )),
                chance_to_hit_by_projectile: Some(0.45),
                chance_to_hit_by_explosion: Some(0.15),
            }],
            internal_hits: Some(vec![VehicleInternalCriticalHit {
                distance_m: distance_m + 0.01,
                target: VehicleCriticalTarget::Crew(VehicleCriticalCrewName::Commander),
            }]),
        }
    }

    fn explosion_query(
        impact: ProjectileVec3,
        target_position: ProjectileVec3,
        entity: EntityRef,
    ) -> ExplosionEvidenceQuery {
        ExplosionEvidenceQuery {
            target: entity,
            impact: protocol_vec(impact),
            incoming_direction: crate::protocol::Vec3 {
                x: 1.0,
                y: 0.0,
                z: 0.0,
            },
            caliber_mm: 122.0,
            target_pose: crate::protocol::ExplosionTargetPose {
                position: protocol_vec(target_position),
                yaw: 0.25,
                pitch: -0.1,
                roll: 0.05,
                turret_yaw: 0.4,
                gun_pitch: -0.2,
                siege_state: 0,
            },
        }
    }

    fn explosion_evidence(query: &ExplosionEvidenceQuery) -> ExplosionEvidence {
        let mut hit = vehicle_hit(
            &OracleOperation::VehicleHitTest {
                start: query.impact,
                end: crate::protocol::Vec3 {
                    x: query.target_pose.position.x,
                    y: query.target_pose.position.y + 1.0,
                    z: query.target_pose.position.z,
                },
                target: query.target,
            },
            0.5,
        );
        if let Some(internal_hits) = &mut hit.internal_hits {
            internal_hits[0].distance_m = 0.5;
        }
        ExplosionEvidence {
            target_pose: query.target_pose,
            vehicle_ray: Some(ExplosionVehicleRay {
                layers: hit
                    .layers
                    .into_iter()
                    .map(|layer| ExplosionHitLayer {
                        distance_m: layer.distance_m,
                        hit_angle_cos: layer.hit_angle_cos,
                        component: layer.component,
                        material: layer.material,
                        critical_target: layer.critical_target,
                        chance_to_hit_by_explosion: layer.chance_to_hit_by_explosion,
                    })
                    .collect(),
            }),
            internal_hits: hit.internal_hits,
        }
    }

    fn no_hit_batch(plan: &ProjectileFlightPlan, batch_seq: u64) -> AppliedOracleBatch {
        batch_for(plan, batch_seq, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => empty_destructible_evidence(),
            OracleOperation::VehicleHitTest { .. } => QueryOutcome::VehicleHitTest { hit: None },
            _ => unreachable!(),
        })
    }

    fn empty_destructible_evidence() -> QueryOutcome {
        QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
            candidates: Vec::new(),
            destroyed_skipped: 0,
            static_collision: None,
        })
    }

    fn backing_collision(operation: &OracleOperation, fraction: f64) -> QueryOutcome {
        let OracleOperation::DestructibleShotEvidence(arguments) = operation else {
            panic!("backing collision fixture requires destructible shot evidence");
        };
        let dx = f64::from(arguments.end.x - arguments.start.x);
        let dy = f64::from(arguments.end.y - arguments.start.y);
        let dz = f64::from(arguments.end.z - arguments.start.z);
        let length = (dx * dx + dy * dy + dz * dz).sqrt();
        QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
            candidates: Vec::new(),
            destroyed_skipped: 0,
            static_collision: Some(DestructibleStaticCollision {
                distance: length * fraction,
                position: Vec3 {
                    x: arguments.start.x + (arguments.end.x - arguments.start.x) * fraction as f32,
                    y: arguments.start.y + (arguments.end.y - arguments.start.y) * fraction as f32,
                    z: arguments.start.z + (arguments.end.z - arguments.start.z) * fraction as f32,
                },
                normal: Some(Vec3 {
                    x: 0.0,
                    y: 1.0,
                    z: 0.0,
                }),
            }),
        })
    }

    fn shot_candidate(
        operation: &OracleOperation,
        item_index: i64,
        entry_fraction: f64,
        exit_fraction: f64,
        ap_through: bool,
    ) -> DestructibleShotCandidate {
        let OracleOperation::DestructibleShotEvidence(arguments) = operation else {
            panic!("candidate fixture requires destructible shot evidence");
        };
        let dx = f64::from(arguments.end.x - arguments.start.x);
        let dy = f64::from(arguments.end.y - arguments.start.y);
        let dz = f64::from(arguments.end.z - arguments.start.z);
        let length = (dx * dx + dy * dy + dz * dz).sqrt();
        DestructibleShotCandidate {
            chunk_id: 7,
            item_index,
            mat_kind: None,
            kind: WireDestructibleKind::Fragile,
            entry_distance: length * entry_fraction,
            exit_distance: length * exit_fraction,
            impact_position: Vec3 {
                x: arguments.start.x
                    + (arguments.end.x - arguments.start.x) * entry_fraction as f32,
                y: arguments.start.y
                    + (arguments.end.y - arguments.start.y) * entry_fraction as f32,
                z: arguments.start.z
                    + (arguments.end.z - arguments.start.z) * entry_fraction as f32,
            },
            item_scale: 1.0,
            scaled_health: if ap_through { 10.0 } else { 30.0 },
            ap_through,
            piercing_loss: if ap_through { 25.0 } else { 0.0 },
        }
    }

    fn destructible_outcome(
        operation: &OracleOperation,
        candidate: DestructibleShotCandidate,
        static_fraction: Option<f64>,
    ) -> QueryOutcome {
        let static_collision =
            static_fraction.map(|fraction| match backing_collision(operation, fraction) {
                QueryOutcome::DestructibleShotEvidence(evidence) => evidence
                    .static_collision
                    .expect("fixture has a backing collision"),
                _ => unreachable!(),
            });
        QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
            candidates: vec![candidate],
            destroyed_skipped: 0,
            static_collision,
        })
    }

    fn batch_for<F>(
        plan: &ProjectileFlightPlan,
        batch_seq: u64,
        mut outcome: F,
    ) -> AppliedOracleBatch
    where
        F: FnMut(&OracleV1Query, &OracleOperation) -> QueryOutcome,
    {
        let request = plan.request(batch_seq, 11);
        let results = request
            .queries
            .iter()
            .map(|query| OracleV1Result {
                query_id: query.query_id,
                key: query.key.clone(),
                query_generation: query.query_generation,
                entity: query.entity,
                status: OracleV1ResultStatus::Ok {
                    outcome: outcome(query, &query.operation),
                },
            })
            .collect();
        let reply = OracleV1BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: batch_seq,
            results,
        };
        AppliedOracleBatch { request, reply }
    }

    fn base_integrator(record: ProjectileRecord) -> ProjectileFlightIntegrator {
        let mut flight = ProjectileFlightIntegrator::new(lineage(), 0).unwrap();
        assert!(flight.install_destructible_native_space_id(91).unwrap());
        assert!(flight.track(record, 0).unwrap());
        flight
    }

    #[test]
    fn python_golden_ballistic_position_matches_projectile_runtime() {
        // Generated with high-latency projectile_runtime.py:
        // trajectory_position((2,5,-3),(80,15,12),(0,-9.81,0),1.0)
        let position = ballistic_position(
            ProjectileVec3 {
                x: 2.0,
                y: 5.0,
                z: -3.0,
            },
            ProjectileVec3 {
                x: 80.0,
                y: 15.0,
                z: 12.0,
            },
            9.81,
            1.0,
        );
        assert_eq!(position.x, 82.0);
        assert!((position.y - 15.095).abs() < 1e-12);
        assert_eq!(position.z, 9.0);
        assert!((curvature_limited_substep(190.0) - 0.045_883_146_774).abs() < 1e-12);
    }

    #[test]
    fn python_golden_thirty_hz_position_and_chord_distance_match() {
        // Generated by advancing InFlightProjectiles at 30 Hz for one second:
        // position=(82,15.095,9), distance=81.57079961163362.
        let record = admitted_record(
            ProjectileVec3 {
                x: 2.0,
                y: 5.0,
                z: -3.0,
            },
            ProjectileVec3 {
                x: 80.0,
                y: 15.0,
                z: 12.0,
            },
            9.81,
            5_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let mut plans = Vec::new();
        for tick in 1..=30 {
            plans.extend(flight.plan_tick(tick, space(), &[], 0x01).unwrap());
        }
        for (index, plan) in plans.iter().enumerate() {
            let batch = no_hit_batch(plan, index as u64 + 1);
            assert!(matches!(
                flight
                    .apply_native_batch(plan.apply_tick, plan.id, &batch)
                    .unwrap(),
                ProjectileFlightDecision::Progress { .. }
            ));
        }
        let snapshot = flight.snapshot("7:p:1:1").unwrap();
        assert_eq!(round6(snapshot.committed_position.x), 82.0);
        assert_eq!(round6(snapshot.committed_position.y), 15.095);
        assert_eq!(round6(snapshot.committed_position.z), 9.0);
        assert!((snapshot.committed_distance - 81.570_799_611_633_62).abs() < 2e-6);
    }

    #[test]
    fn max_distance_is_clipped_before_oracle_and_becomes_a_miss() {
        // Python golden: distance=1.1, elapsed=0.11, position.x=1.1.
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 10.0,
                y: 0.0,
                z: 0.0,
            },
            9.81,
            1.1,
            10_000,
        );
        let mut flight = base_integrator(record);
        let mut terminal = None;
        for tick in 1..=4 {
            let plan = flight.plan_tick(tick, space(), &[], 1).unwrap().remove(0);
            let batch = no_hit_batch(&plan, tick);
            match flight
                .apply_native_batch(plan.apply_tick, plan.id, &batch)
                .unwrap()
            {
                ProjectileFlightDecision::Terminal(value) => terminal = Some(value),
                ProjectileFlightDecision::Progress { .. } => {}
                ProjectileFlightDecision::IgnoredAfterTerminal { .. } => unreachable!(),
            }
        }
        let terminal = terminal.unwrap();
        assert_eq!(terminal.cause, ProjectileTerminalCause::MaxDistance);
        assert_eq!(terminal.resolution.outcome, ProjectileOutcome::Miss);
        assert_eq!(terminal.resolution.resolved_time_ms, 110);
        assert_eq!(terminal.resolution.checked_distance, 1.1);
        assert_eq!(terminal.resolution.impact, None);
    }

    #[test]
    fn max_time_is_exact_and_becomes_expired() {
        // Python golden with gravity=-9.81: elapsed=.073,
        // position=(1.46,-0.026138745,0), chord distance=1.460297024514365.
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 20.0,
                y: 0.0,
                z: 0.0,
            },
            9.81,
            100.0,
            73,
        );
        let mut flight = base_integrator(record);
        let mut terminal = None;
        for tick in 1..=3 {
            let plan = flight.plan_tick(tick, space(), &[], 1).unwrap().remove(0);
            let batch = no_hit_batch(&plan, tick);
            match flight
                .apply_native_batch(plan.apply_tick, plan.id, &batch)
                .unwrap()
            {
                ProjectileFlightDecision::Terminal(value) => terminal = Some(value),
                ProjectileFlightDecision::Progress { .. } => {}
                ProjectileFlightDecision::IgnoredAfterTerminal { .. } => unreachable!(),
            }
        }
        let terminal = terminal.unwrap();
        assert_eq!(terminal.cause, ProjectileTerminalCause::MaxTime);
        assert_eq!(terminal.resolution.outcome, ProjectileOutcome::Expired);
        assert_eq!(terminal.resolution.resolved_time_ms, 73);
        assert_eq!(round6(terminal.resolution.checked_distance), 1.460_297);
    }

    #[test]
    fn query_plan_is_typed_bounded_and_uses_four_pipeline_lanes() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            500.0,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let targets = [target(2, 102, false), target(3, 103, true)];
        let mut destructible_keys = Vec::new();
        let mut generations = Vec::new();
        for tick in 1..=5 {
            let plan = flight
                .plan_tick(tick, space(), &targets, 0x55)
                .unwrap()
                .remove(0);
            assert_eq!(plan.segments.len(), 2);
            assert_eq!(plan.queries.len(), 6);
            match &plan.queries[0].operation {
                OracleOperation::DestructibleShotEvidence(arguments) => {
                    assert_eq!(arguments.space_id, 91);
                    assert_eq!(arguments.shell_kind, DestructibleShellKind::ArmorPiercing);
                }
                _ => panic!("first query must be exact destructible evidence"),
            }
            assert!(matches!(
                plan.queries[3].operation,
                OracleOperation::DestructibleShotEvidence(..)
            ));
            assert!(plan
                .queries
                .iter()
                .enumerate()
                .filter(|(index, _)| !matches!(index, 0 | 3))
                .all(|(_, query)| matches!(
                    query.operation,
                    OracleOperation::VehicleHitTest { .. }
                )));
            crate::validator::validate_oracle_v1_request(&plan.request(tick, 11)).unwrap();
            destructible_keys.push(plan.queries[0].key.clone());
            generations.push(plan.queries[0].query_generation);
        }
        assert_ne!(destructible_keys[0], destructible_keys[1]);
        assert_eq!(destructible_keys[0], destructible_keys[4]);
        assert_eq!(generations, vec![1, 1, 1, 1, 2]);
    }

    #[test]
    fn destructible_space_installation_is_explicit_immutable_and_fail_closed() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = ProjectileFlightIntegrator::new(lineage(), 0).unwrap();
        flight.track(record, 0).unwrap();
        assert_eq!(
            flight.plan_tick(1, space(), &[], 1),
            Err(ProjectileFlightError::MissingDestructibleSpace)
        );
        assert_eq!(
            flight.install_destructible_native_space_id(0),
            Err(ProjectileFlightError::InvalidDestructibleSpace)
        );
        assert!(flight.install_destructible_native_space_id(91).unwrap());
        assert!(!flight.install_destructible_native_space_id(91).unwrap());
        assert_eq!(
            flight.install_destructible_native_space_id(92),
            Err(ProjectileFlightError::InvalidDestructibleSpace)
        );
        assert_eq!(flight.plan_tick(1, space(), &[], 1).unwrap().len(), 1);
    }

    #[test]
    fn ap_through_destructible_commits_on_progress_and_is_not_charged_twice() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let first = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let batch = batch_for(&first, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => destructible_outcome(
                operation,
                shot_candidate(operation, 3, 0.2, 0.3, true),
                None,
            ),
            _ => unreachable!(),
        });
        match flight.apply_native_batch(4, first.id, &batch).unwrap() {
            ProjectileFlightDecision::Progress {
                cursor,
                destructibles,
                ..
            } => {
                assert_eq!(cursor.piercing_loss, 25.0);
                assert_eq!(destructibles.len(), 1);
                assert_eq!(destructibles[0].key.item_index, 3);
            }
            other => panic!("unexpected decision {other:?}"),
        }

        let second = flight.plan_tick(2, space(), &[], 1).unwrap().remove(0);
        let stale_retry = batch_for(&second, 2, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => destructible_outcome(
                operation,
                shot_candidate(operation, 3, 0.2, 0.3, true),
                None,
            ),
            _ => unreachable!(),
        });
        match flight
            .apply_native_batch(5, second.id, &stale_retry)
            .unwrap()
        {
            ProjectileFlightDecision::Progress {
                cursor,
                destructibles,
                ..
            } => {
                assert_eq!(cursor.piercing_loss, 25.0);
                assert!(destructibles.is_empty());
            }
            other => panic!("unexpected decision {other:?}"),
        }
    }

    #[test]
    fn destructible_stops_non_through_or_spent_ap_before_backing_static() {
        for low_piercing in [false, true] {
            let mut record = admitted_record(
                ProjectileVec3 {
                    x: 0.0,
                    y: 1.0,
                    z: 0.0,
                },
                ProjectileVec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 100.0,
                },
                9.81,
                1_000.0,
                10_000,
            );
            if low_piercing {
                record.launch.source_shot.piercing_power = [20.0, 20.0];
            }
            let mut flight = base_integrator(record);
            let plan = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
            let batch = batch_for(&plan, 1, |_query, operation| match operation {
                OracleOperation::DestructibleShotEvidence(..) => destructible_outcome(
                    operation,
                    shot_candidate(operation, 4, 0.2, 0.3, low_piercing),
                    Some(0.8),
                ),
                _ => unreachable!(),
            });
            match flight.apply_native_batch(4, plan.id, &batch).unwrap() {
                ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
                    cause: ProjectileTerminalCause::Destructible { receipt },
                    destructibles,
                    resolution,
                    ..
                }) => {
                    assert_eq!(receipt.key.item_index, 4);
                    assert_eq!(destructibles, vec![receipt]);
                    assert_eq!(
                        resolution.piercing_loss,
                        if low_piercing { 25.0 } else { 0.0 }
                    );
                }
                other => panic!("unexpected decision {other:?}"),
            }
        }
    }

    #[test]
    fn ap_through_destructible_precedes_and_preserves_backing_static() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let batch = batch_for(&plan, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => destructible_outcome(
                operation,
                shot_candidate(operation, 5, 0.2, 0.3, true),
                Some(0.8),
            ),
            _ => unreachable!(),
        });
        match flight.apply_native_batch(4, plan.id, &batch).unwrap() {
            ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
                cause: ProjectileTerminalCause::DestructibleBacking { .. },
                destructibles,
                resolution,
                ..
            }) => {
                assert_eq!(destructibles.len(), 1);
                assert_eq!(destructibles[0].key.item_index, 5);
                assert_eq!(resolution.piercing_loss, 25.0);
            }
            other => panic!("unexpected decision {other:?}"),
        }
    }

    #[test]
    fn earliest_vehicle_hit_distinguishes_wreck_and_direct() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight
            .plan_tick(
                1,
                space(),
                &[target(2, 102, false), target(3, 103, true)],
                1,
            )
            .unwrap()
            .remove(0);
        let batch = batch_for(&plan, 1, |query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => empty_destructible_evidence(),
            OracleOperation::VehicleHitTest { target, .. } => QueryOutcome::VehicleHitTest {
                hit: Some(vehicle_hit(
                    operation,
                    if target.entity_id == 103 { 0.2 } else { 0.4 },
                )),
            },
            _ => unreachable!("unexpected query {}", query.query_id),
        });
        let decision = flight.apply_native_batch(4, plan.id, &batch).unwrap();
        match decision {
            ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
                cause:
                    ProjectileTerminalCause::Wreck {
                        target, native_hit, ..
                    },
                resolution,
                ..
            }) => {
                assert_eq!(target.id, 3);
                assert_eq!(resolution.outcome, ProjectileOutcome::Impact);
                assert!(resolution.impact.is_some());
                let trace = critical_trace_from_vehicle_hit(&native_hit);
                assert_eq!(trace.native_layers.len(), 1);
                assert_eq!(trace.native_layers[0].armor_mm, 60.0);
                assert_eq!(
                    trace.native_layers[0].target,
                    Some(CriticalTarget::Device(DeviceName::EngineHealth))
                );
                assert_eq!(
                    trace.native_layers[0].chance_to_hit_by_projectile,
                    Some(0.45)
                );
                assert_eq!(
                    trace.internal_hits.as_ref().unwrap()[0].target,
                    CriticalTarget::Crew(CrewName::Commander)
                );
            }
            other => panic!("unexpected decision {other:?}"),
        }
    }

    #[test]
    fn backing_static_wins_an_exact_fraction_tie() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight
            .plan_tick(1, space(), &[target(2, 102, false)], 1)
            .unwrap()
            .remove(0);
        let batch = batch_for(&plan, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => backing_collision(operation, 0.4),
            OracleOperation::VehicleHitTest { .. } => QueryOutcome::VehicleHitTest {
                hit: Some(vehicle_hit(operation, 0.4)),
            },
            _ => unreachable!(),
        });
        assert!(matches!(
            flight.apply_native_batch(4, plan.id, &batch).unwrap(),
            ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
                cause: ProjectileTerminalCause::DestructibleBacking { .. },
                ..
            })
        ));
    }

    #[test]
    fn malformed_vehicle_layers_are_rejected_again_at_projectile_boundary() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight
            .plan_tick(1, space(), &[target(2, 102, false)], 1)
            .unwrap()
            .remove(0);
        let batch = batch_for(&plan, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => empty_destructible_evidence(),
            OracleOperation::VehicleHitTest { .. } => {
                let mut hit = vehicle_hit(operation, 0.4);
                hit.layers[0].hit_angle_cos = f64::NAN;
                QueryOutcome::VehicleHitTest { hit: Some(hit) }
            }
            _ => unreachable!(),
        });

        assert!(matches!(
            flight.apply_native_batch(4, plan.id, &batch),
            Err(ProjectileFlightError::MalformedReceipt)
        ));
        assert_eq!(flight.active_len(), 1);
    }

    #[test]
    fn exact_t_plus_three_and_request_fences_are_enforced() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let batch = no_hit_batch(&plan, 1);
        assert_eq!(
            flight.apply_native_batch(3, plan.id, &batch),
            Err(ProjectileFlightError::ApplyTick {
                plan_id: plan.id,
                expected: 4,
                received: 3,
            })
        );
        let mut wrong = batch.clone();
        wrong.request.oracle_generation += 1;
        assert_eq!(
            flight.apply_native_batch(4, plan.id, &wrong),
            Err(ProjectileFlightError::BatchMismatch { plan_id: plan.id })
        );
        assert!(matches!(
            flight.apply_native_batch(4, plan.id, &batch).unwrap(),
            ProjectileFlightDecision::Progress { .. }
        ));
    }

    #[test]
    fn broker_timeout_fails_closed_without_an_impact() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let plan = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let timeout = TimedOutOracleBatch {
            request: plan.request(1, 11),
        };
        match flight.apply_native_timeout(4, plan.id, &timeout).unwrap() {
            ProjectileFlightDecision::Terminal(proposal) => {
                assert_eq!(proposal.cause, ProjectileTerminalCause::OracleTimeout);
                assert_eq!(proposal.resolution.outcome, ProjectileOutcome::Expired);
                assert_eq!(proposal.resolution.impact, None);
                assert_eq!(proposal.resolution.resolved_time_ms, 0);
            }
            other => panic!("unexpected decision {other:?}"),
        }
    }

    #[test]
    fn later_pipeline_receipt_is_ignored_after_an_earlier_terminal() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut flight = base_integrator(record);
        let first = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let second = flight.plan_tick(2, space(), &[], 1).unwrap().remove(0);
        let hit = batch_for(&first, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => backing_collision(operation, 0.5),
            _ => unreachable!(),
        });
        assert!(matches!(
            flight.apply_native_batch(4, first.id, &hit).unwrap(),
            ProjectileFlightDecision::Terminal(_)
        ));
        assert_eq!(
            flight
                .apply_native_batch(5, second.id, &no_hit_batch(&second, 2))
                .unwrap(),
            ProjectileFlightDecision::IgnoredAfterTerminal { plan_id: second.id }
        );
    }

    #[test]
    fn retiring_one_projectile_does_not_hide_another_projectiles_receipt() {
        let origin = ProjectileVec3 {
            x: 0.0,
            y: 1.0,
            z: 0.0,
        };
        let velocity = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 100.0,
        };
        let first_record = admitted_record_with_shot(1, origin, velocity, 9.81, 1_000.0, 10_000);
        let second_record = admitted_record_with_shot(2, origin, velocity, 9.81, 1_000.0, 10_000);
        let mut flight = base_integrator(first_record);
        assert!(flight.track(second_record, 0).unwrap());
        let plans = flight.plan_tick(1, space(), &[], 1).unwrap();
        let first = plans
            .iter()
            .find(|plan| plan.projectile_id == "7:p:1:1")
            .unwrap();
        let second = plans
            .iter()
            .find(|plan| plan.projectile_id == "7:p:1:2")
            .unwrap();
        let first_hit = batch_for(first, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => backing_collision(operation, 0.5),
            _ => unreachable!(),
        });
        assert!(matches!(
            flight.apply_native_batch(4, first.id, &first_hit).unwrap(),
            ProjectileFlightDecision::Terminal(_)
        ));
        assert!(matches!(
            flight
                .apply_native_batch(4, second.id, &no_hit_batch(second, 2))
                .unwrap(),
            ProjectileFlightDecision::Progress { .. }
        ));
    }

    #[test]
    fn a_multi_projectile_plan_tick_is_atomic_on_query_capacity_failure() {
        let origin = ProjectileVec3 {
            x: 0.0,
            y: 1.0,
            z: 0.0,
        };
        let velocity = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 100.0,
        };
        let first_record = admitted_record_with_shot(1, origin, velocity, 9.81, 1_000.0, 10_000);
        let second_record = admitted_record_with_shot(2, origin, velocity, 500.0, 1_000.0, 10_000);
        let mut flight = base_integrator(first_record);
        assert!(flight.track(second_record, 0).unwrap());
        let before_first = flight.snapshot("7:p:1:1").unwrap();
        let before_second = flight.snapshot("7:p:1:2").unwrap();
        let targets: Vec<_> = (0..32)
            .map(|index| target(index + 2, 1_000 + index as i64, false))
            .collect();

        assert!(matches!(
            flight.plan_tick(1, space(), &targets, 1),
            Err(ProjectileFlightError::QueryCapacity { projectile_id })
                if projectile_id == "7:p:1:2"
        ));
        assert_eq!(flight.current_tick(), 0);
        assert_eq!(flight.pending_len(), 0);
        assert_eq!(flight.snapshot("7:p:1:1"), Some(before_first));
        assert_eq!(flight.snapshot("7:p:1:2"), Some(before_second));
    }

    #[test]
    fn duplicate_targets_and_oversized_native_work_fail_before_mutation() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            500.0,
            1_000.0,
            10_000,
        );
        let duplicate = target(2, 102, false);
        let mut flight = base_integrator(record.clone());
        assert_eq!(
            flight.plan_tick(1, space(), &[duplicate, duplicate], 1),
            Err(ProjectileFlightError::DuplicateTarget)
        );
        assert_eq!(flight.current_tick(), 0);

        let mut flight = base_integrator(record);
        let targets: Vec<_> = (0..32)
            .map(|index| target(index + 2, 1_000 + index as i64, false))
            .collect();
        assert!(matches!(
            flight.plan_tick(1, space(), &targets, 1),
            Err(ProjectileFlightError::QueryCapacity { .. })
        ));
        assert_eq!(flight.current_tick(), 0);
    }

    #[test]
    fn first_direct_terminal_can_continue_one_ricochet_with_new_ordinal() {
        use crate::projectile::{
            build_first_ricochet, LaunchAdmission, LaunchContext, ProjectileLedger,
            RicochetAdmission,
        };

        let launch = launch_with(
            1,
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let mut ledger = ProjectileLedger::new();
        let record = match ledger
            .admit_launch(
                launch,
                LaunchContext {
                    round_id: 7,
                    authority_epoch: 3,
                    shooter: shooter(),
                    team: 1,
                    source_vehicle: "ussr:R11_MS-1".to_owned(),
                    expected_shot_seq: 1,
                    server_time_ms: 0,
                },
            )
            .unwrap()
        {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        };
        let mut flight = base_integrator(record.clone());
        let target = target(2, 102, false);
        let first = flight
            .plan_tick(1, space(), &[target], 1)
            .unwrap()
            .remove(0);
        let old_second = flight
            .plan_tick(2, space(), &[target], 1)
            .unwrap()
            .remove(0);
        let direct = batch_for(&first, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => empty_destructible_evidence(),
            OracleOperation::VehicleHitTest { .. } => QueryOutcome::VehicleHitTest {
                hit: Some(vehicle_hit(operation, 0.4)),
            },
            _ => unreachable!(),
        });
        let terminal = match flight.apply_native_batch(4, first.id, &direct).unwrap() {
            ProjectileFlightDecision::Terminal(terminal) => terminal,
            other => panic!("unexpected decision {other:?}"),
        };
        assert_eq!(flight.active_len(), 0);
        assert_eq!(flight.continuable_len(), 1);
        assert!(matches!(
            terminal.cause,
            ProjectileTerminalCause::Direct { .. }
        ));

        let request = build_first_ricochet(
            &record,
            &terminal.resolution,
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: -1.0,
            },
        )
        .unwrap();
        let continued = match ledger.ricochet(request, 1_000).unwrap() {
            RicochetAdmission::Applied { record } => record,
            RicochetAdmission::ExactRetry => unreachable!(),
        };
        assert!(flight.continue_ricochet(continued.clone()).unwrap());
        assert!(!flight.continue_ricochet(continued.clone()).unwrap());
        assert_eq!(flight.continuable_len(), 0);
        assert_eq!(flight.active_len(), 1);

        let continuation = flight
            .plan_tick(3, space(), &[target], 1)
            .unwrap()
            .remove(0);
        assert_ne!(
            continuation.id.projectile_ordinal,
            first.id.projectile_ordinal
        );
        assert_eq!(continuation.segments[0].start, continued.segment_origin);
        assert!(continuation.segments[0].end.z < continuation.segments[0].start.z);
        assert!(continuation.segments[0].start_distance > 0.0);

        assert_eq!(
            flight
                .apply_native_batch(5, old_second.id, &no_hit_batch(&old_second, 2))
                .unwrap(),
            ProjectileFlightDecision::IgnoredAfterTerminal {
                plan_id: old_second.id,
            }
        );
    }

    #[test]
    fn frozen_he_splash_uses_frozen_pose_native_armor_and_critical_facts() {
        let record = admitted_he_record();
        let impact = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 0.0,
        };
        let resolution = ProjectileResolution {
            round_id: 7,
            authority_epoch: 3,
            projectile_id: record.projectile_id.clone(),
            base_checked_ms: 0,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 50,
            checked_distance: 5.0,
            piercing_loss: 0.0,
            penetration_factor: 1.0,
            impact: Some(impact),
        };
        let entity = EntityRef {
            entity_id: 102,
            generation: 4,
        };
        let target_position = ProjectileVec3 {
            x: 5.0,
            y: 0.0,
            z: 0.0,
        };
        let target = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        let query = explosion_query(impact, target_position, entity);
        let facts = frozen_he_target_from_explosion_evidence(
            target,
            &query,
            explosion_evidence(&query),
            vec![MaterialInfo {
                armor_mm: 20.0,
                vehicle_damage_factor: 1.0,
                ..MaterialInfo::default()
            }],
            1.0,
        )
        .unwrap();
        let splash = resolve_frozen_he_splash(
            &record,
            &resolution,
            None,
            std::slice::from_ref(&facts),
            HeTuning::default(),
        )
        .unwrap();
        assert_eq!(splash.len(), 1);
        assert_eq!(splash[0].target_position, target_position);
        assert_eq!(splash[0].distance_fraction, 0.5);
        assert_eq!(splash[0].nominal_armor_mm, 60.0);
        assert_eq!(splash[0].hull_damage, 84);
        assert_eq!(
            splash[0].critical_trace.native_layers[0].chance_to_hit_by_explosion,
            Some(0.15)
        );
        assert_eq!(
            splash[0].critical_trace.native_layers[0].chance_to_hit_by_projectile,
            None
        );
        assert_eq!(
            splash[0].critical_trace.internal_hits.as_ref().unwrap()[0].target,
            CriticalTarget::Crew(CrewName::Commander)
        );

        assert!(resolve_frozen_he_splash(
            &record,
            &resolution,
            Some(target),
            std::slice::from_ref(&facts),
            HeTuning::default(),
        )
        .unwrap()
        .is_empty());
        assert_eq!(
            resolve_frozen_he_splash(
                &record,
                &resolution,
                None,
                &[facts.clone(), facts],
                HeTuning::default(),
            ),
            Err(ProjectileFlightError::InvalidFrozenHeFacts)
        );
    }

    #[test]
    fn frozen_he_without_structural_native_layer_uses_hull_fallback_without_inventing_criticals() {
        let record = admitted_he_record();
        let impact = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 0.0,
        };
        let resolution = ProjectileResolution {
            round_id: 7,
            authority_epoch: 3,
            projectile_id: record.projectile_id.clone(),
            base_checked_ms: 0,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 40,
            checked_distance: 4.0,
            piercing_loss: 0.0,
            penetration_factor: 1.0,
            impact: Some(impact),
        };
        let entity = EntityRef {
            entity_id: 104,
            generation: 1,
        };
        let target_position = ProjectileVec3 {
            x: 4.0,
            y: 0.0,
            z: 0.0,
        };
        let query = explosion_query(impact, target_position, entity);
        let mut evidence = explosion_evidence(&query);
        evidence.vehicle_ray = None;
        evidence.internal_hits = Some(Vec::new());
        let target = frozen_he_target_from_explosion_evidence(
            VehicleKey {
                kind: VehicleKind::Player,
                id: 4,
            },
            &query,
            evidence,
            vec![MaterialInfo {
                armor_mm: 30.0,
                vehicle_damage_factor: 1.0,
                ..MaterialInfo::default()
            }],
            1.0,
        )
        .unwrap();
        let splash =
            resolve_frozen_he_splash(&record, &resolution, None, &[target], HeTuning::default())
                .unwrap();
        assert_eq!(splash.len(), 1);
        assert_eq!(splash[0].nominal_armor_mm, 30.0);
        assert!(splash[0].critical_trace.native_layers.is_empty());
        assert_eq!(splash[0].critical_trace.internal_hits, Some(Vec::new()));
    }

    #[test]
    fn frozen_he_evidence_resolver_rejects_mixed_pose_and_preserves_no_layout() {
        let impact = ProjectileVec3 {
            x: 0.0,
            y: 0.0,
            z: 0.0,
        };
        let position = ProjectileVec3 {
            x: 5.0,
            y: 0.0,
            z: 0.0,
        };
        let entity = EntityRef {
            entity_id: 105,
            generation: 2,
        };
        let target = VehicleKey {
            kind: VehicleKind::Bot,
            id: 5,
        };
        let query = explosion_query(impact, position, entity);
        let mut mismatched = explosion_evidence(&query);
        mismatched.target_pose.turret_yaw += 0.01;
        assert_eq!(
            frozen_he_target_from_explosion_evidence(target, &query, mismatched, Vec::new(), 1.0,),
            Err(ProjectileFlightError::InvalidFrozenHeFacts)
        );

        let evidence = ExplosionEvidence {
            target_pose: query.target_pose,
            vehicle_ray: None,
            internal_hits: None,
        };
        let frozen =
            frozen_he_target_from_explosion_evidence(target, &query, evidence, Vec::new(), 1.0)
                .unwrap();
        assert!(frozen.vehicle_ray.is_none());
        assert!(frozen.critical_trace.native_layers.is_empty());
        assert_eq!(frozen.critical_trace.internal_hits, None);
    }

    #[test]
    fn retire_is_idempotent_for_a_known_terminal_identity() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let projectile_id = record.projectile_id.clone();
        let mut flight = base_integrator(record);
        let plan = flight.plan_tick(1, space(), &[], 1).unwrap().remove(0);
        let timeout = TimedOutOracleBatch {
            request: plan.request(1, 11),
        };

        assert!(matches!(
            flight
                .apply_native_timeout(plan.apply_tick, plan.id, &timeout)
                .unwrap(),
            ProjectileFlightDecision::Terminal(_)
        ));
        assert!(flight.retire(&projectile_id));
        assert!(flight.retire(&projectile_id));
        assert!(!flight.retire("7:p:1:unknown"));
    }

    #[test]
    fn retire_removes_a_first_direct_ricochet_continuation() {
        let record = admitted_record(
            ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            9.81,
            1_000.0,
            10_000,
        );
        let projectile_id = record.projectile_id.clone();
        let mut flight = base_integrator(record);
        let target = target(2, 102, false);
        let plan = flight
            .plan_tick(1, space(), &[target], 1)
            .unwrap()
            .remove(0);
        let direct = batch_for(&plan, 1, |_query, operation| match operation {
            OracleOperation::DestructibleShotEvidence(..) => empty_destructible_evidence(),
            OracleOperation::VehicleHitTest { .. } => QueryOutcome::VehicleHitTest {
                hit: Some(vehicle_hit(operation, 0.4)),
            },
            _ => unreachable!(),
        });

        assert!(matches!(
            flight
                .apply_native_batch(plan.apply_tick, plan.id, &direct)
                .unwrap(),
            ProjectileFlightDecision::Terminal(ProjectileTerminalProposal {
                cause: ProjectileTerminalCause::Direct { .. },
                ..
            })
        ));
        assert_eq!(flight.continuable_len(), 1);
        assert!(flight.retire(&projectile_id));
        assert_eq!(flight.continuable_len(), 0);
        assert!(flight.retire(&projectile_id));
    }
}
