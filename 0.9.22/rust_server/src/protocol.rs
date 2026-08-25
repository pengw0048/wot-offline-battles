use serde::{Deserialize, Deserializer, Serialize};

pub const ORACLE_PROTOCOL_VERSION: u16 = 1;
pub const ORACLE_PIPELINE_TICKS: Tick = 3;
pub const MAX_ORACLE_BATCH_QUERIES: usize = 64;
pub const MAX_ORACLE_PRIMITIVE_OPERATIONS: usize = 256;
pub const MAX_ORACLE_LINE_BYTES: usize = 256 * 1024;
pub const MAX_ORACLE_QUERY_KEY_BYTES: usize = 128;
pub const MAX_ORACLE_ERROR_CODE_BYTES: usize = 64;
pub const MAX_ORACLE_ERROR_MESSAGE_BYTES: usize = 256;
pub const MAX_ORACLE_REPLY_HISTORY: usize = 256;
pub const MAX_VEHICLE_HIT_LAYERS: usize = 128;
pub const MAX_VEHICLE_INTERNAL_HITS: usize = 64;
pub const MAX_VEHICLE_HIT_TEXT_BYTES: usize = 128;
pub const MAX_VEHICLE_HIT_DISTANCE_M: f64 = 10_000.0;
pub const MAX_VEHICLE_HIT_ARMOR_MM: f64 = 1_000_000_000.0;
pub const MAX_VEHICLE_DAMAGE_FACTOR: f64 = 1_000_000_000.0;
pub const MAX_FOLIAGE_CAMOUFLAGE_BONUS: f64 = 0.60;
pub const MAX_RAM_CONTACT_COORDINATE_M: f64 = 5_000.0;
pub const MAX_RAM_CONTACT_POSE_DISTANCE_M: f64 = 100.0;
pub const MAX_RAM_CONTACT_POSE_ANGLE_RAD: f64 = 1.0e6;
pub const RAM_CONTACT_NORMAL_TOLERANCE: f64 = 1.0e-3;
pub const MAX_EXPLOSION_WORLD_COORDINATE_M: f64 = 5_000.0;
pub const MAX_EXPLOSION_RAY_DISTANCE_M: f64 = 101.0;
pub const MAX_EXPLOSION_POSE_ANGLE_RAD: f64 = 1.0e6;
pub const MAX_EXPLOSION_CALIBER_MM: f64 = 1_000.0;
pub const EXPLOSION_DIRECTION_TOLERANCE: f64 = 1.0e-3;
pub const MAX_DESTRUCTIBLE_WORLD_COORDINATE_M: f64 = 100_000.0;
pub const MAX_DESTRUCTIBLE_SEGMENT_M: f64 = 10_000.0;
pub const MAX_DESTRUCTIBLE_FRAME_TRAVEL_M: f64 = 100.0;
pub const MAX_DESTRUCTIBLE_KINETIC_SPEED_MPS: f64 = 500.0;
pub const MAX_DESTRUCTIBLE_VEHICLE_MASS_KG: f64 = 10_000_000.0;
pub const MAX_DESTRUCTIBLE_ITEM_SCALE: f64 = 1_000.0;
pub const MAX_DESTRUCTIBLE_KINETIC_CORRECTION: f64 = 16.0;
pub const MAX_DESTRUCTIBLE_CANDIDATES: usize = 64;
pub const MAX_DESTRUCTIBLE_HULL_CANDIDATES: usize = 32;
pub const MAX_DESTRUCTIBLE_SKIPPED: u64 = 256;
pub const DESTRUCTIBLE_POINT_EPSILON_M: f64 = 0.001;
pub const DESTRUCTIBLE_AMBIGUITY_EPSILON_M: f64 = 0.075;
pub const DESTRUCTIBLE_AP_THROUGH_MAX_HP: f64 = 19.0;
pub const DESTRUCTIBLE_AP_PIERCING_LOSS_MM: f64 = 25.0;

pub type RoundId = u64;
pub type Epoch = u64;
pub type Tick = u64;
pub type BatchId = u64;
pub type QueryId = u64;
pub type EntityId = i64;
pub type EntityGeneration = u64;
pub type AuthorityEpoch = u64;
pub type OracleGeneration = u64;
pub type QueryGeneration = u64;
pub type BatchSequence = u64;
pub type OracleFrameSequence = u64;
pub type WorldRevision = u64;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct SimulationScope {
    pub round_id: RoundId,
    pub epoch: Epoch,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct BatchKey {
    pub round_id: RoundId,
    pub epoch: Epoch,
    pub tick: Tick,
    pub batch_id: BatchId,
}

impl BatchKey {
    pub fn scope(self) -> SimulationScope {
        SimulationScope {
            round_id: self.round_id,
            epoch: self.epoch,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
pub struct EntityRef {
    pub entity_id: EntityId,
    pub generation: EntityGeneration,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Vec3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SegmentCastPrimitive {
    pub start: Vec3,
    pub end: Vec3,
    pub collision_mask: u32,
}

/// Frozen observer/target pair evaluated by the loaded #1513 battle world.
///
/// Root positions remain explicit because the visibility ray and barrel lane
/// use different endpoint heights and trimming laws. `target` is duplicated
/// from the query entity fence deliberately, while `observer` gives the other
/// half of the pair its own native generation fence.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpottingEvidenceQuery {
    pub observer: EntityRef,
    pub target: EntityRef,
    pub observer_position: Vec3,
    pub target_position: Vec3,
    pub collision_mask: u32,
    pub evaluated_for_recent_fire: bool,
}

/// Independent direct-fire static-world probe for the same frozen pair.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FiringLaneEvidenceQuery {
    pub observer: EntityRef,
    pub target: EntityRef,
    pub observer_position: Vec3,
    pub target_position: Vec3,
    pub collision_mask: u32,
}

/// One issue-tick pose used to rebuild a vehicle's native collision compound
/// without consulting its later render transform at the T+3 apply boundary.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RamContactPose {
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub turret_yaw: f64,
    pub gun_pitch: f64,
    pub siege_state: u8,
}

/// Atomic two-sided structural-armour probe at one frozen hull contact.
/// `first` is duplicated by the query entity fence; `second` carries its own
/// exact native generation. A successful outcome always contains both sides.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RamContactArmorEvidenceQuery {
    pub first: EntityRef,
    pub second: EntityRef,
    pub first_pose: RamContactPose,
    pub second_pose: RamContactPose,
    pub contact_point: Vec3,
    /// Canonical second-to-first unit normal.
    pub contact_normal: Vec3,
}

/// One issue-tick vehicle pose sufficient to rebuild the #1513 hull, turret,
/// and gun collision component matrices without consulting a later entity.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExplosionTargetPose {
    pub position: Vec3,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub turret_yaw: f64,
    pub gun_pitch: f64,
    pub siege_state: u8,
}

/// Read-only native facts requested for one HE target at a frozen impact.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExplosionEvidenceQuery {
    pub target: EntityRef,
    pub impact: Vec3,
    /// Unit incoming shell direction; this is the axis of the internal cone.
    pub incoming_direction: Vec3,
    pub caliber_mm: f64,
    pub target_pose: ExplosionTargetPose,
}

/// Exact #1513 shell categories accepted by the hidden native oracle.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DestructibleShellKind {
    ArmorPiercing,
    ArmorPiercingHe,
    ArmorPiercingCr,
    HollowCharge,
    HighExplosive,
}

impl DestructibleShellKind {
    pub fn is_ap(self) -> bool {
        matches!(
            self,
            Self::ArmorPiercing | Self::ArmorPiercingHe | Self::ArmorPiercingCr
        )
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleShotEvidenceQuery {
    pub space_id: i64,
    pub start: Vec3,
    pub end: Vec3,
    pub shell_kind: DestructibleShellKind,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleHullEvidenceQuery {
    pub space_id: i64,
    pub position: Vec3,
    pub yaw: f64,
    pub frame_travel: f64,
}

/// Fixed native evidence requested for one player trigger edge.
///
/// The node is deliberately not caller-selectable: the #1513 worker always
/// samples `HP_gunFire` and the water probe at that exact frozen position.
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PlayerMuzzleEvidenceQuery {}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "operation", content = "arguments", rename_all = "snake_case")]
pub enum OracleOperation {
    GroundSample {
        position: Vec3,
    },
    SegmentCast {
        start: Vec3,
        end: Vec3,
        collision_mask: u32,
    },
    SegmentCastBatch {
        segments: Vec<SegmentCastPrimitive>,
    },
    GroundSampleBatch {
        positions: Vec<Vec3>,
    },
    WaterSampleBatch {
        positions: Vec<Vec3>,
    },
    WaterSample {
        position: Vec3,
    },
    VehicleHitTest {
        start: Vec3,
        end: Vec3,
        target: EntityRef,
    },
    ExplosionEvidence(ExplosionEvidenceQuery),
    NodeTransform {
        node: String,
    },
    PlayerMuzzleEvidence(PlayerMuzzleEvidenceQuery),
    SpottingEvidence(SpottingEvidenceQuery),
    FiringLaneEvidence(FiringLaneEvidenceQuery),
    RamContactArmorEvidence(RamContactArmorEvidenceQuery),
    DestructibleShotEvidence(DestructibleShotEvidenceQuery),
    DestructibleHullEvidence(DestructibleHullEvidenceQuery),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OracleOperationKind {
    GroundSample,
    SegmentCast,
    SegmentCastBatch,
    WaterSample,
    GroundSampleBatch,
    WaterSampleBatch,
    VehicleHitTest,
    ExplosionEvidence,
    NodeTransform,
    PlayerMuzzleEvidence,
    SpottingEvidence,
    FiringLaneEvidence,
    RamContactArmorEvidence,
    DestructibleShotEvidence,
    DestructibleHullEvidence,
}

impl OracleOperation {
    pub fn kind(&self) -> OracleOperationKind {
        match self {
            Self::GroundSample { .. } => OracleOperationKind::GroundSample,
            Self::SegmentCast { .. } => OracleOperationKind::SegmentCast,
            Self::SegmentCastBatch { .. } => OracleOperationKind::SegmentCastBatch,
            Self::GroundSampleBatch { .. } => OracleOperationKind::GroundSampleBatch,
            Self::WaterSampleBatch { .. } => OracleOperationKind::WaterSampleBatch,
            Self::WaterSample { .. } => OracleOperationKind::WaterSample,
            Self::VehicleHitTest { .. } => OracleOperationKind::VehicleHitTest,
            Self::ExplosionEvidence(..) => OracleOperationKind::ExplosionEvidence,
            Self::NodeTransform { .. } => OracleOperationKind::NodeTransform,
            Self::PlayerMuzzleEvidence(..) => OracleOperationKind::PlayerMuzzleEvidence,
            Self::SpottingEvidence(..) => OracleOperationKind::SpottingEvidence,
            Self::FiringLaneEvidence(..) => OracleOperationKind::FiringLaneEvidence,
            Self::RamContactArmorEvidence(..) => OracleOperationKind::RamContactArmorEvidence,
            Self::DestructibleShotEvidence(..) => OracleOperationKind::DestructibleShotEvidence,
            Self::DestructibleHullEvidence(..) => OracleOperationKind::DestructibleHullEvidence,
        }
    }

    pub fn primitive_count(&self) -> usize {
        match self {
            Self::SegmentCastBatch { segments } => segments.len(),
            Self::GroundSampleBatch { positions } | Self::WaterSampleBatch { positions } => {
                positions.len()
            }
            // Spotting combines one world ray with one prebaked-foliage
            // lookup; a direct firing lane may cast both target-height rays.
            Self::SpottingEvidence(..)
            | Self::FiringLaneEvidence(..)
            | Self::RamContactArmorEvidence(..)
            | Self::ExplosionEvidence(..) => 2,
            Self::DestructibleShotEvidence(..) => MAX_DESTRUCTIBLE_CANDIDATES,
            Self::DestructibleHullEvidence(..) => MAX_DESTRUCTIBLE_HULL_CANDIDATES,
            _ => 1,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleQuery {
    pub query_id: QueryId,
    pub entity: EntityRef,
    pub operation: OracleOperation,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BatchRequest {
    pub protocol_version: u16,
    pub round_id: RoundId,
    pub epoch: Epoch,
    pub tick: Tick,
    pub batch_id: BatchId,
    pub queries: Vec<OracleQuery>,
}

impl BatchRequest {
    pub fn key(&self) -> BatchKey {
        BatchKey {
            round_id: self.round_id,
            epoch: self.epoch,
            tick: self.tick,
            batch_id: self.batch_id,
        }
    }

    pub fn scope(&self) -> SimulationScope {
        self.key().scope()
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SurfaceSample {
    pub height: f32,
    pub normal: Vec3,
    pub material_id: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RayHit {
    pub fraction: f32,
    pub position: Vec3,
    pub normal: Vec3,
    pub material_id: Option<u32>,
    pub hit_entity: Option<EntityRef>,
}

/// Exact native material facts carried by an oracle receipt.
///
/// This wire type deliberately has no defaults or dependency on the combat
/// rules' convenience types: a missing native fact must fail deserialization.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct VehicleHitMaterial {
    pub armor_mm: f64,
    pub vehicle_damage_factor: f64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub kind: Option<i64>,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub native_identity: Option<u64>,
    pub collide_once_only: bool,
    pub use_hit_angle: bool,
    pub check_caliber_for_hit_angle_norm: bool,
    pub may_ricochet: bool,
    pub check_caliber_for_ricochet: bool,
}

/// Strict oracle-wire spelling of the nine #1513 device health extras.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum VehicleCriticalDeviceName {
    #[serde(rename = "ammoBayHealth")]
    AmmoBayHealth,
    #[serde(rename = "engineHealth")]
    EngineHealth,
    #[serde(rename = "fuelTankHealth")]
    FuelTankHealth,
    #[serde(rename = "gunHealth")]
    GunHealth,
    #[serde(rename = "leftTrackHealth")]
    LeftTrackHealth,
    #[serde(rename = "radioHealth")]
    RadioHealth,
    #[serde(rename = "rightTrackHealth")]
    RightTrackHealth,
    #[serde(rename = "surveyingDeviceHealth")]
    SurveyingDeviceHealth,
    #[serde(rename = "turretRotatorHealth")]
    TurretRotatorHealth,
}

/// Strict oracle-wire spelling of the crew instances supported by the
/// authority. Python admits one of these only when the current descriptor has
/// the matching `<instance>Health` extra.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum VehicleCriticalCrewName {
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

/// Independent strict wire type; conversion into `critical_damage` is kept at
/// the projectile boundary rather than deserializing gameplay-rule types.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(
    tag = "kind",
    content = "name",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum VehicleCriticalTarget {
    Device(VehicleCriticalDeviceName),
    Crew(VehicleCriticalCrewName),
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct VehicleInternalCriticalHit {
    pub distance_m: f64,
    pub target: VehicleCriticalTarget,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct VehicleHitLayer {
    pub distance_m: f64,
    pub hit_angle_cos: f64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub component: Option<String>,
    pub material: VehicleHitMaterial,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub critical_target: Option<VehicleCriticalTarget>,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub chance_to_hit_by_projectile: Option<f64>,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub chance_to_hit_by_explosion: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct VehicleHit {
    pub fraction: f32,
    pub position: Vec3,
    pub normal: Vec3,
    pub hit_part: String,
    /// Every native collision in nondecreasing `distance_m` order.
    pub layers: Vec<VehicleHitLayer>,
    /// `None` means the validated per-vehicle layout was unavailable;
    /// `Some([])` proves it was available but this ray crossed no target.
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub internal_hits: Option<Vec<VehicleInternalCriticalHit>>,
}

/// One ordered structural/native-extra fact on the frozen blast ray.
/// Projectile-only critical chances are deliberately absent from this HE-only
/// receipt.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExplosionHitLayer {
    pub distance_m: f64,
    pub hit_angle_cos: f64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub component: Option<String>,
    pub material: VehicleHitMaterial,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub critical_target: Option<VehicleCriticalTarget>,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub chance_to_hit_by_explosion: Option<f64>,
}

/// A present value proves the frozen burst-to-target-root ray crossed at least
/// one native vehicle layer. `None` is distinct from an unavailable query.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExplosionVehicleRay {
    pub layers: Vec<ExplosionHitLayer>,
}

/// Native facts only: no damage, occlusion, or gameplay verdict may cross this
/// boundary. The full pose is echoed so Rust can reject mixed-instant facts.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExplosionEvidence {
    pub target_pose: ExplosionTargetPose,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub vehicle_ray: Option<ExplosionVehicleRay>,
    /// `None` means the validated internal layout was unavailable;
    /// `Some([])` proves it existed and the cone crossed no eligible target.
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub internal_hits: Option<Vec<VehicleInternalCriticalHit>>,
}

fn deserialize_explicit_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TransformSample {
    pub position: Vec3,
    /// Row-major 3x3 orientation basis from the loaded native model node.
    pub basis: [f32; 9],
}

/// Frozen native muzzle facts only. Rust remains responsible for deciding
/// whether these facts admit a physical shot.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PlayerMuzzleEvidence {
    pub transform: TransformSample,
    pub barrel_under_water: bool,
}

/// Native evidence for exactly one observer/target pair.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpottingEvidence {
    pub line_of_sight: bool,
    pub foliage_bonus: f64,
    /// Echoes the request branch so a stale pre-fire foliage result cannot be
    /// consumed after a target shot.
    pub evaluated_for_recent_fire: bool,
}

/// Native static-world barrel lane, never inferred from visibility LOS.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FiringLaneEvidence {
    pub clear: bool,
}

/// Both first structural plates returned atomically for one frozen contact.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RamContactArmorEvidence {
    pub first_armor_mm: f64,
    pub second_armor_mm: f64,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum DestructibleKind {
    Fragile,
    Structure,
    Falling,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleShotCandidate {
    pub chunk_id: i64,
    pub item_index: i64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub mat_kind: Option<i64>,
    pub kind: DestructibleKind,
    pub entry_distance: f64,
    pub exit_distance: f64,
    pub impact_position: Vec3,
    pub item_scale: f64,
    pub scaled_health: f64,
    pub ap_through: bool,
    pub piercing_loss: f64,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleStaticCollision {
    pub distance: f64,
    pub position: Vec3,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub normal: Option<Vec3>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleShotEvidence {
    pub candidates: Vec<DestructibleShotCandidate>,
    pub destroyed_skipped: u64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub static_collision: Option<DestructibleStaticCollision>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleHullCandidate {
    pub chunk_id: i64,
    pub item_index: i64,
    #[serde(deserialize_with = "deserialize_explicit_option")]
    pub mat_kind: Option<i64>,
    pub kind: DestructibleKind,
    pub obb_center: Vec3,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DestructibleHullEvidence {
    pub candidates: Vec<DestructibleHullCandidate>,
    pub frame_travel: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "result", content = "value", rename_all = "snake_case")]
pub enum QueryOutcome {
    GroundSample {
        sample: Option<SurfaceSample>,
    },
    SegmentCast {
        hit: Option<RayHit>,
    },
    SegmentCastBatch {
        hits: Vec<Option<RayHit>>,
    },
    WaterSample {
        height: Option<f32>,
    },
    GroundSampleBatch {
        samples: Vec<Option<SurfaceSample>>,
    },
    WaterSampleBatch {
        heights: Vec<Option<f32>>,
    },
    VehicleHitTest {
        #[serde(deserialize_with = "deserialize_explicit_option")]
        hit: Option<VehicleHit>,
    },
    ExplosionEvidence(ExplosionEvidence),
    NodeTransform {
        transform: Option<TransformSample>,
    },
    PlayerMuzzleEvidence(PlayerMuzzleEvidence),
    SpottingEvidence(SpottingEvidence),
    FiringLaneEvidence(FiringLaneEvidence),
    RamContactArmorEvidence(RamContactArmorEvidence),
    DestructibleShotEvidence(DestructibleShotEvidence),
    DestructibleHullEvidence(DestructibleHullEvidence),
    Error {
        code: String,
        message: String,
    },
}

impl QueryOutcome {
    pub fn kind(&self) -> Option<OracleOperationKind> {
        match self {
            Self::GroundSample { .. } => Some(OracleOperationKind::GroundSample),
            Self::SegmentCast { .. } => Some(OracleOperationKind::SegmentCast),
            Self::SegmentCastBatch { .. } => Some(OracleOperationKind::SegmentCastBatch),
            Self::GroundSampleBatch { .. } => Some(OracleOperationKind::GroundSampleBatch),
            Self::WaterSampleBatch { .. } => Some(OracleOperationKind::WaterSampleBatch),
            Self::WaterSample { .. } => Some(OracleOperationKind::WaterSample),
            Self::VehicleHitTest { .. } => Some(OracleOperationKind::VehicleHitTest),
            Self::ExplosionEvidence(..) => Some(OracleOperationKind::ExplosionEvidence),
            Self::NodeTransform { .. } => Some(OracleOperationKind::NodeTransform),
            Self::PlayerMuzzleEvidence(..) => Some(OracleOperationKind::PlayerMuzzleEvidence),
            Self::SpottingEvidence(..) => Some(OracleOperationKind::SpottingEvidence),
            Self::FiringLaneEvidence(..) => Some(OracleOperationKind::FiringLaneEvidence),
            Self::RamContactArmorEvidence(..) => Some(OracleOperationKind::RamContactArmorEvidence),
            Self::DestructibleShotEvidence(..) => {
                Some(OracleOperationKind::DestructibleShotEvidence)
            }
            Self::DestructibleHullEvidence(..) => {
                Some(OracleOperationKind::DestructibleHullEvidence)
            }
            Self::Error { .. } => None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleResult {
    pub query_id: QueryId,
    pub entity: EntityRef,
    pub outcome: QueryOutcome,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BatchReply {
    pub protocol_version: u16,
    pub round_id: RoundId,
    pub epoch: Epoch,
    pub tick: Tick,
    pub batch_id: BatchId,
    pub results: Vec<OracleResult>,
}

impl BatchReply {
    pub fn key(&self) -> BatchKey {
        BatchKey {
            round_id: self.round_id,
            epoch: self.epoch,
            tick: self.tick,
            batch_id: self.batch_id,
        }
    }

    pub fn scope(&self) -> SimulationScope {
        self.key().scope()
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum OracleMessage {
    BatchRequest(BatchRequest),
    BatchReply(BatchReply),
}

impl OracleMessage {
    pub fn key(&self) -> BatchKey {
        match self {
            Self::BatchRequest(request) => request.key(),
            Self::BatchReply(reply) => reply.key(),
        }
    }
}

/// Exact simulation and native-space incarnation for oracle-v1 traffic.
///
/// `authority_epoch` fences a restarted authority within one round, while
/// `oracle_generation` fences a reconnected client or reloaded BigWorld space.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct OracleLineage {
    pub round_id: RoundId,
    pub authority_epoch: AuthorityEpoch,
    pub oracle_generation: OracleGeneration,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct OracleV1BatchKey {
    pub lineage: OracleLineage,
    pub batch_seq: BatchSequence,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleV1Query {
    pub query_id: QueryId,
    pub key: String,
    pub query_generation: QueryGeneration,
    pub entity: EntityRef,
    pub operation: OracleOperation,
}

/// One atomic, fixed-latency native query batch.
///
/// The authority emits a batch after `issued_tick`. A reply may arrive early,
/// but gameplay may consume it only while advancing `apply_tick`.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleV1BatchRequest {
    pub protocol_version: u16,
    pub round_id: RoundId,
    pub authority_epoch: AuthorityEpoch,
    pub oracle_generation: OracleGeneration,
    pub batch_seq: BatchSequence,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub world_revision: WorldRevision,
    pub queries: Vec<OracleV1Query>,
}

impl OracleV1BatchRequest {
    pub fn lineage(&self) -> OracleLineage {
        OracleLineage {
            round_id: self.round_id,
            authority_epoch: self.authority_epoch,
            oracle_generation: self.oracle_generation,
        }
    }

    pub fn key(&self) -> OracleV1BatchKey {
        OracleV1BatchKey {
            lineage: self.lineage(),
            batch_seq: self.batch_seq,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleV1Result {
    pub query_id: QueryId,
    pub key: String,
    pub query_generation: QueryGeneration,
    pub entity: EntityRef,
    pub status: OracleV1ResultStatus,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum OracleV1ResultStatus {
    Ok { outcome: QueryOutcome },
    Unavailable { code: String, message: String },
}

impl OracleV1ResultStatus {
    pub fn kind(&self) -> Option<OracleOperationKind> {
        match self {
            Self::Ok { outcome } => outcome.kind(),
            Self::Unavailable { .. } => None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OracleV1BatchReply {
    pub protocol_version: u16,
    pub round_id: RoundId,
    pub authority_epoch: AuthorityEpoch,
    pub oracle_generation: OracleGeneration,
    pub batch_seq: BatchSequence,
    pub issued_tick: Tick,
    pub apply_tick: Tick,
    pub world_revision: WorldRevision,
    pub oracle_frame_seq: OracleFrameSequence,
    pub results: Vec<OracleV1Result>,
}

impl OracleV1BatchReply {
    pub fn lineage(&self) -> OracleLineage {
        OracleLineage {
            round_id: self.round_id,
            authority_epoch: self.authority_epoch,
            oracle_generation: self.oracle_generation,
        }
    }

    pub fn key(&self) -> OracleV1BatchKey {
        OracleV1BatchKey {
            lineage: self.lineage(),
            batch_seq: self.batch_seq,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum OracleV1Message {
    QueryBatch(OracleV1BatchRequest),
    QueryReply(OracleV1BatchReply),
}

impl OracleV1Message {
    pub fn key(&self) -> OracleV1BatchKey {
        match self {
            Self::QueryBatch(request) => request.key(),
            Self::QueryReply(reply) => reply.key(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> OracleMessage {
        OracleMessage::BatchRequest(BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: 7,
            epoch: 2,
            tick: 30,
            batch_id: 9,
            queries: vec![OracleQuery {
                query_id: 1,
                entity: EntityRef {
                    entity_id: 42,
                    generation: 3,
                },
                operation: OracleOperation::GroundSample {
                    position: Vec3 {
                        x: 1.0,
                        y: 2.0,
                        z: 3.0,
                    },
                },
            }],
        })
    }

    #[test]
    fn message_round_trips_through_json() {
        let message = request();
        let json = serde_json::to_string(&message).unwrap();
        let decoded: OracleMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, message);
    }

    #[test]
    fn wire_format_is_explicitly_tagged() {
        let value = serde_json::to_value(request()).unwrap();
        assert_eq!(value["type"], "batch_request");
        assert_eq!(
            value["payload"]["queries"][0]["operation"]["operation"],
            "ground_sample"
        );
    }

    #[test]
    fn keys_include_every_staleness_dimension() {
        let message = request();
        assert_eq!(
            message.key(),
            BatchKey {
                round_id: 7,
                epoch: 2,
                tick: 30,
                batch_id: 9,
            }
        );
    }

    #[test]
    fn unknown_struct_fields_are_rejected() {
        let json = r#"{"entity_id":42,"generation":3,"unexpected":true}"#;
        assert!(serde_json::from_str::<EntityRef>(json).is_err());
    }

    #[test]
    fn vehicle_hit_layers_have_one_exact_canonical_wire_shape() {
        let hit = VehicleHit {
            fraction: 0.25,
            position: Vec3 {
                x: 2.5,
                y: 0.0,
                z: 0.0,
            },
            normal: Vec3 {
                x: -1.0,
                y: 0.0,
                z: 0.0,
            },
            hit_part: "vehicleChassis".to_owned(),
            layers: vec![VehicleHitLayer {
                distance_m: 2.5,
                hit_angle_cos: 0.75,
                component: Some("vehicleChassis".to_owned()),
                material: VehicleHitMaterial {
                    armor_mm: 40.0,
                    vehicle_damage_factor: 0.0,
                    kind: None,
                    native_identity: Some(1234),
                    collide_once_only: true,
                    use_hit_angle: true,
                    check_caliber_for_hit_angle_norm: true,
                    may_ricochet: false,
                    check_caliber_for_ricochet: false,
                },
                critical_target: Some(VehicleCriticalTarget::Device(
                    VehicleCriticalDeviceName::EngineHealth,
                )),
                chance_to_hit_by_projectile: Some(0.45),
                chance_to_hit_by_explosion: Some(0.15),
            }],
            internal_hits: Some(vec![VehicleInternalCriticalHit {
                distance_m: 3.0,
                target: VehicleCriticalTarget::Crew(VehicleCriticalCrewName::Commander),
            }]),
        };

        let value = serde_json::to_value(&hit).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "fraction": 0.25,
                "position": {"x": 2.5, "y": 0.0, "z": 0.0},
                "normal": {"x": -1.0, "y": 0.0, "z": 0.0},
                "hit_part": "vehicleChassis",
                "layers": [{
                    "distance_m": 2.5,
                    "hit_angle_cos": 0.75,
                    "component": "vehicleChassis",
                    "material": {
                        "armor_mm": 40.0,
                        "vehicle_damage_factor": 0.0,
                        "kind": null,
                        "native_identity": 1234,
                        "collide_once_only": true,
                        "use_hit_angle": true,
                        "check_caliber_for_hit_angle_norm": true,
                        "may_ricochet": false,
                        "check_caliber_for_ricochet": false
                    },
                    "critical_target": {"kind": "device", "name": "engineHealth"},
                    "chance_to_hit_by_projectile": 0.45,
                    "chance_to_hit_by_explosion": 0.15
                }],
                "internal_hits": [{
                    "distance_m": 3.0,
                    "target": {"kind": "crew", "name": "commander"}
                }]
            })
        );
        assert_eq!(serde_json::from_value::<VehicleHit>(value).unwrap(), hit);
    }

    #[test]
    fn vehicle_hit_layers_reject_missing_nullable_material_and_unknown_fields() {
        let canonical = serde_json::json!({
            "distance_m": 2.5,
            "hit_angle_cos": 0.75,
            "component": null,
            "material": {
                "armor_mm": 40.0,
                "vehicle_damage_factor": 1.0,
                "kind": null,
                "native_identity": null,
                "collide_once_only": false,
                "use_hit_angle": true,
                "check_caliber_for_hit_angle_norm": true,
                "may_ricochet": true,
                "check_caliber_for_ricochet": true
            },
            "critical_target": null,
            "chance_to_hit_by_projectile": null,
            "chance_to_hit_by_explosion": null
        });
        assert!(serde_json::from_value::<VehicleHitLayer>(canonical.clone()).is_ok());

        for field in [
            "component",
            "material",
            "critical_target",
            "chance_to_hit_by_projectile",
            "chance_to_hit_by_explosion",
        ] {
            let mut malformed = canonical.clone();
            malformed.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<VehicleHitLayer>(malformed).is_err());
        }
        for field in ["kind", "native_identity", "may_ricochet"] {
            let mut malformed = canonical.clone();
            malformed["material"].as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<VehicleHitLayer>(malformed).is_err());
        }
        let mut malformed = canonical;
        malformed["material"]["checkCaliberForRichet"] = serde_json::json!(true);
        assert!(serde_json::from_value::<VehicleHitLayer>(malformed).is_err());
        assert!(
            serde_json::from_value::<VehicleCriticalTarget>(serde_json::json!({
                "kind": "device",
                "name": "unknownHealth"
            }))
            .is_err()
        );
        assert!(
            serde_json::from_value::<VehicleCriticalTarget>(serde_json::json!({
                "kind": "crew",
                "name": "commander",
                "extra": true
            }))
            .is_err()
        );
        assert!(serde_json::from_value::<QueryOutcome>(serde_json::json!({
            "result": "vehicle_hit_test",
            "value": {}
        }))
        .is_err());
        assert!(serde_json::from_value::<VehicleHit>(serde_json::json!({
            "fraction": 0.25,
            "position": {"x": 2.5, "y": 0.0, "z": 0.0},
            "normal": {"x": -1.0, "y": 0.0, "z": 0.0},
            "hit_part": "vehicleChassis",
            "layers": [],
        }))
        .is_err());
    }

    #[test]
    fn oracle_v1_exposes_every_fixed_latency_fence() {
        let request = OracleV1Message::QueryBatch(OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: 7,
            authority_epoch: 4,
            oracle_generation: 2,
            batch_seq: 9,
            issued_tick: 30,
            apply_tick: 33,
            world_revision: 5,
            queries: vec![OracleV1Query {
                query_id: 1,
                key: "ground:bot:42".to_owned(),
                query_generation: 6,
                entity: EntityRef {
                    entity_id: 42,
                    generation: 3,
                },
                operation: OracleOperation::GroundSample {
                    position: Vec3 {
                        x: 1.0,
                        y: 2.0,
                        z: 3.0,
                    },
                },
            }],
        });

        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["type"], "query_batch");
        assert_eq!(value["payload"]["authority_epoch"], 4);
        assert_eq!(value["payload"]["oracle_generation"], 2);
        assert_eq!(value["payload"]["issued_tick"], 30);
        assert_eq!(value["payload"]["apply_tick"], 33);
        assert_eq!(value["payload"]["queries"][0]["query_generation"], 6);
        let decoded: OracleV1Message = serde_json::from_value(value).unwrap();
        assert_eq!(decoded, request);
    }

    #[test]
    fn player_muzzle_evidence_has_a_fixed_fact_only_wire_shape() {
        let operation = OracleOperation::PlayerMuzzleEvidence(PlayerMuzzleEvidenceQuery::default());
        let operation_value = serde_json::to_value(&operation).unwrap();
        assert_eq!(
            operation_value,
            serde_json::json!({
                "operation": "player_muzzle_evidence",
                "arguments": {}
            })
        );
        assert_eq!(
            serde_json::from_value::<OracleOperation>(operation_value.clone()).unwrap(),
            operation
        );
        assert_eq!(operation.kind(), OracleOperationKind::PlayerMuzzleEvidence);
        assert_eq!(operation.primitive_count(), 1);

        let outcome = QueryOutcome::PlayerMuzzleEvidence(PlayerMuzzleEvidence {
            transform: TransformSample {
                position: Vec3 {
                    x: 11.0,
                    y: 12.0,
                    z: 13.0,
                },
                basis: [1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            },
            barrel_under_water: true,
        });
        let outcome_value = serde_json::to_value(&outcome).unwrap();
        assert_eq!(
            outcome_value,
            serde_json::json!({
                "result": "player_muzzle_evidence",
                "value": {
                    "transform": {
                        "position": {"x": 11.0, "y": 12.0, "z": 13.0},
                        "basis": [1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0]
                    },
                    "barrel_under_water": true
                }
            })
        );
        assert_eq!(
            serde_json::from_value::<QueryOutcome>(outcome_value.clone()).unwrap(),
            outcome
        );

        let mut malformed_operation = operation_value;
        malformed_operation["arguments"]["node"] = serde_json::json!("HP_gunFire");
        assert!(serde_json::from_value::<OracleOperation>(malformed_operation).is_err());
        for forbidden in ["can_fire", "damage", "legal", "verdict"] {
            let mut malformed_outcome = outcome_value.clone();
            malformed_outcome["value"][forbidden] = serde_json::json!(false);
            assert!(serde_json::from_value::<QueryOutcome>(malformed_outcome).is_err());
        }
    }

    #[test]
    fn spotting_and_firing_lane_have_distinct_strict_wire_shapes() {
        let observer = EntityRef {
            entity_id: 41,
            generation: 2,
        };
        let target = EntityRef {
            entity_id: 42,
            generation: 3,
        };
        let pair = SpottingEvidenceQuery {
            observer,
            target,
            observer_position: Vec3 {
                x: 1.0,
                y: 2.0,
                z: 3.0,
            },
            target_position: Vec3 {
                x: 10.0,
                y: 2.0,
                z: 3.0,
            },
            collision_mask: 128,
            evaluated_for_recent_fire: true,
        };
        let spotting = OracleOperation::SpottingEvidence(pair.clone());
        let lane = OracleOperation::FiringLaneEvidence(FiringLaneEvidenceQuery {
            observer,
            target,
            observer_position: pair.observer_position,
            target_position: pair.target_position,
            collision_mask: 128,
        });

        let spotting_value = serde_json::to_value(&spotting).unwrap();
        let lane_value = serde_json::to_value(&lane).unwrap();
        assert_eq!(spotting_value["operation"], "spotting_evidence");
        assert_eq!(lane_value["operation"], "firing_lane_evidence");
        assert_eq!(
            spotting_value["arguments"]["evaluated_for_recent_fire"],
            true
        );
        assert!(lane_value["arguments"]
            .get("evaluated_for_recent_fire")
            .is_none());
        assert_eq!(spotting.primitive_count(), 2);
        assert_eq!(lane.primitive_count(), 2);

        let evidence = QueryOutcome::SpottingEvidence(SpottingEvidence {
            line_of_sight: true,
            foliage_bonus: 0.25,
            evaluated_for_recent_fire: true,
        });
        assert_eq!(
            serde_json::to_value(evidence).unwrap(),
            serde_json::json!({
                "result": "spotting_evidence",
                "value": {
                    "line_of_sight": true,
                    "foliage_bonus": 0.25,
                    "evaluated_for_recent_fire": true
                }
            })
        );

        let mut malformed = spotting_value;
        malformed["arguments"]["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<OracleOperation>(malformed).is_err());
        assert!(serde_json::from_value::<QueryOutcome>(serde_json::json!({
            "result": "spotting_evidence",
            "value": {
                "line_of_sight": true,
                "foliage_bonus": 0.25
            }
        }))
        .is_err());
    }

    #[test]
    fn ram_contact_armor_has_one_atomic_two_entity_wire_shape() {
        let first = EntityRef {
            entity_id: 41,
            generation: 2,
        };
        let second = EntityRef {
            entity_id: 42,
            generation: 3,
        };
        let operation = OracleOperation::RamContactArmorEvidence(RamContactArmorEvidenceQuery {
            first,
            second,
            first_pose: RamContactPose {
                position: Vec3 {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                },
                yaw: 0.25,
                pitch: 0.1,
                roll: -0.05,
                turret_yaw: 0.2,
                gun_pitch: -0.1,
                siege_state: 0,
            },
            second_pose: RamContactPose {
                position: Vec3 {
                    x: -1.0,
                    y: 2.0,
                    z: 3.0,
                },
                yaw: -0.2,
                pitch: 0.0,
                roll: 0.03,
                turret_yaw: -0.15,
                gun_pitch: 0.05,
                siege_state: 0,
            },
            contact_point: Vec3 {
                x: 0.0,
                y: 2.5,
                z: 3.0,
            },
            contact_normal: Vec3 {
                x: 1.0,
                y: 0.0,
                z: 0.0,
            },
        });

        let value = serde_json::to_value(&operation).unwrap();
        assert_eq!(value["operation"], "ram_contact_armor_evidence");
        assert_eq!(value["arguments"]["first"]["entity_id"], 41);
        assert_eq!(value["arguments"]["second"]["generation"], 3);
        assert_eq!(value["arguments"]["first_pose"]["yaw"], 0.25);
        assert_eq!(operation.primitive_count(), 2);
        assert_eq!(
            serde_json::to_value(QueryOutcome::RamContactArmorEvidence(
                RamContactArmorEvidence {
                    first_armor_mm: 70.0,
                    second_armor_mm: 45.0,
                },
            ))
            .unwrap(),
            serde_json::json!({
                "result": "ram_contact_armor_evidence",
                "value": {
                    "first_armor_mm": 70.0,
                    "second_armor_mm": 45.0
                }
            })
        );

        let mut malformed = value;
        malformed["arguments"]["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<OracleOperation>(malformed).is_err());
    }

    #[test]
    fn explosion_evidence_is_pose_frozen_fact_only_and_preserves_layout_state() {
        let target = EntityRef {
            entity_id: 42,
            generation: 3,
        };
        let pose = ExplosionTargetPose {
            position: Vec3 {
                x: 5.0,
                y: 0.0,
                z: 1.0,
            },
            yaw: 0.25,
            pitch: -0.1,
            roll: 0.05,
            turret_yaw: 0.4,
            gun_pitch: -0.2,
            siege_state: 2,
        };
        let operation = OracleOperation::ExplosionEvidence(ExplosionEvidenceQuery {
            target,
            impact: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            incoming_direction: Vec3 {
                x: 1.0,
                y: 0.0,
                z: 0.0,
            },
            caliber_mm: 122.0,
            target_pose: pose,
        });
        let operation_value = serde_json::to_value(&operation).unwrap();
        assert_eq!(operation_value["operation"], "explosion_evidence");
        assert_eq!(operation_value["arguments"]["target"]["generation"], 3);
        assert_eq!(
            operation_value["arguments"]["target_pose"]["turret_yaw"],
            0.4
        );
        assert_eq!(operation.primitive_count(), 2);

        let outcome = QueryOutcome::ExplosionEvidence(ExplosionEvidence {
            target_pose: pose,
            vehicle_ray: None,
            internal_hits: Some(Vec::new()),
        });
        let outcome_value = serde_json::to_value(&outcome).unwrap();
        assert_eq!(outcome_value["result"], "explosion_evidence");
        assert!(outcome_value["value"]["vehicle_ray"].is_null());
        assert_eq!(
            outcome_value["value"]["internal_hits"],
            serde_json::json!([])
        );
        let encoded = serde_json::to_string(&outcome_value).unwrap();
        for forbidden in ["damage", "occluded", "verdict"] {
            assert!(!encoded.contains(forbidden));
        }
        for forbidden in ["damage", "occluded", "verdict"] {
            let mut malformed = outcome_value.clone();
            malformed["value"][forbidden] = serde_json::json!(false);
            assert!(serde_json::from_value::<QueryOutcome>(malformed).is_err());
        }

        let unavailable_layout = QueryOutcome::ExplosionEvidence(ExplosionEvidence {
            target_pose: pose,
            vehicle_ray: None,
            internal_hits: None,
        });
        assert!(
            serde_json::to_value(unavailable_layout).unwrap()["value"]["internal_hits"].is_null()
        );
        assert!(serde_json::from_value::<QueryOutcome>(serde_json::json!({
            "result": "explosion_evidence",
            "value": {
                "target_pose": operation_value["arguments"]["target_pose"],
                "vehicle_ray": null
            }
        }))
        .is_err());
    }

    #[test]
    fn destructible_operations_have_exact_wire_shapes_and_bounded_costs() {
        let shot = OracleOperation::DestructibleShotEvidence(DestructibleShotEvidenceQuery {
            space_id: 7,
            start: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            end: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 10.0,
            },
            shell_kind: DestructibleShellKind::ArmorPiercing,
        });
        let hull = OracleOperation::DestructibleHullEvidence(DestructibleHullEvidenceQuery {
            space_id: 7,
            position: Vec3 {
                x: 1.0,
                y: 2.0,
                z: 3.0,
            },
            yaw: 0.25,
            frame_travel: 0.3,
        });

        let shot_value = serde_json::to_value(&shot).unwrap();
        assert_eq!(
            shot_value,
            serde_json::json!({
                "operation": "destructible_shot_evidence",
                "arguments": {
                    "space_id": 7,
                    "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "end": {"x": 0.0, "y": 0.0, "z": 10.0},
                    "shell_kind": "ARMOR_PIERCING"
                }
            })
        );
        assert_eq!(
            serde_json::from_value::<OracleOperation>(shot_value).unwrap(),
            shot
        );
        assert_eq!(shot.kind(), OracleOperationKind::DestructibleShotEvidence);
        assert_eq!(shot.primitive_count(), MAX_DESTRUCTIBLE_CANDIDATES);

        let hull_value = serde_json::to_value(&hull).unwrap();
        assert_eq!(hull_value["operation"], "destructible_hull_evidence");
        assert_eq!(hull_value["arguments"]["space_id"], 7);
        assert_eq!(hull_value["arguments"]["frame_travel"], 0.3);
        assert_eq!(
            serde_json::from_value::<OracleOperation>(hull_value).unwrap(),
            hull
        );
        assert_eq!(hull.kind(), OracleOperationKind::DestructibleHullEvidence);
        assert_eq!(hull.primitive_count(), MAX_DESTRUCTIBLE_HULL_CANDIDATES);

        for (kind, spelling) in [
            (DestructibleShellKind::ArmorPiercing, "ARMOR_PIERCING"),
            (DestructibleShellKind::ArmorPiercingHe, "ARMOR_PIERCING_HE"),
            (DestructibleShellKind::ArmorPiercingCr, "ARMOR_PIERCING_CR"),
            (DestructibleShellKind::HollowCharge, "HOLLOW_CHARGE"),
            (DestructibleShellKind::HighExplosive, "HIGH_EXPLOSIVE"),
        ] {
            assert_eq!(serde_json::to_value(kind).unwrap(), spelling);
        }
    }

    #[test]
    fn destructible_success_outcomes_preserve_explicit_nullable_evidence() {
        let shot = QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
            candidates: vec![DestructibleShotCandidate {
                chunk_id: 22,
                item_index: 37,
                mat_kind: None,
                kind: DestructibleKind::Fragile,
                entry_distance: 4.0,
                exit_distance: 5.0,
                impact_position: Vec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 4.0,
                },
                item_scale: 0.5,
                scaled_health: 15.0,
                ap_through: true,
                piercing_loss: 25.0,
            }],
            destroyed_skipped: 1,
            static_collision: Some(DestructibleStaticCollision {
                distance: 6.0,
                position: Vec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 6.0,
                },
                normal: Some(Vec3 {
                    x: 0.0,
                    y: 0.0,
                    z: -1.0,
                }),
            }),
        });
        let value = serde_json::to_value(&shot).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "result": "destructible_shot_evidence",
                "value": {
                    "candidates": [{
                        "chunk_id": 22,
                        "item_index": 37,
                        "mat_kind": null,
                        "kind": "fragile",
                        "entry_distance": 4.0,
                        "exit_distance": 5.0,
                        "impact_position": {"x": 0.0, "y": 0.0, "z": 4.0},
                        "item_scale": 0.5,
                        "scaled_health": 15.0,
                        "ap_through": true,
                        "piercing_loss": 25.0
                    }],
                    "destroyed_skipped": 1,
                    "static_collision": {
                        "distance": 6.0,
                        "position": {"x": 0.0, "y": 0.0, "z": 6.0},
                        "normal": {"x": 0.0, "y": 0.0, "z": -1.0}
                    }
                }
            })
        );
        assert_eq!(serde_json::from_value::<QueryOutcome>(value).unwrap(), shot);

        let hull = QueryOutcome::DestructibleHullEvidence(DestructibleHullEvidence {
            candidates: vec![DestructibleHullCandidate {
                chunk_id: 22,
                item_index: 38,
                mat_kind: Some(73),
                kind: DestructibleKind::Structure,
                obb_center: Vec3 {
                    x: 0.0,
                    y: 0.5,
                    z: 3.75,
                },
            }],
            frame_travel: 0.3,
        });
        let value = serde_json::to_value(&hull).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "result": "destructible_hull_evidence",
                "value": {
                    "candidates": [{
                        "chunk_id": 22,
                        "item_index": 38,
                        "mat_kind": 73,
                        "kind": "structure",
                        "obb_center": {"x": 0.0, "y": 0.5, "z": 3.75}
                    }],
                    "frame_travel": 0.3
                }
            })
        );
        assert_eq!(serde_json::from_value::<QueryOutcome>(value).unwrap(), hull);
    }

    #[test]
    fn destructible_wire_rejects_unknown_or_implicit_nullable_fields() {
        let mut operation = serde_json::json!({
            "operation": "destructible_shot_evidence",
            "arguments": {
                "space_id": 7,
                "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                "end": {"x": 0.0, "y": 0.0, "z": 10.0},
                "shell_kind": "ARMOR_PIERCING"
            }
        });
        operation["arguments"]["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<OracleOperation>(operation).is_err());

        let canonical = serde_json::json!({
            "result": "destructible_shot_evidence",
            "value": {
                "candidates": [{
                    "chunk_id": 22,
                    "item_index": 37,
                    "mat_kind": null,
                    "kind": "fragile",
                    "entry_distance": 4.0,
                    "exit_distance": 6.0,
                    "impact_position": {"x": 0.0, "y": 0.0, "z": 4.0},
                    "item_scale": 0.5,
                    "scaled_health": 15.0,
                    "ap_through": true,
                    "piercing_loss": 25.0
                }],
                "destroyed_skipped": 0,
                "static_collision": null
            }
        });
        assert!(serde_json::from_value::<QueryOutcome>(canonical.clone()).is_ok());
        for path in ["mat_kind", "static_collision"] {
            let mut malformed = canonical.clone();
            if path == "mat_kind" {
                malformed["value"]["candidates"][0]
                    .as_object_mut()
                    .unwrap()
                    .remove(path);
            } else {
                malformed["value"].as_object_mut().unwrap().remove(path);
            }
            assert!(serde_json::from_value::<QueryOutcome>(malformed).is_err());
        }
    }
}
