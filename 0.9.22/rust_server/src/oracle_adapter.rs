//! Deterministic adaptation between bot simulation intents and oracle-v1.
//!
//! The bot simulation deliberately describes native facts at a semantic level
//! (`Ground`, `Motion`, `Visibility`, `Ballistic`, and `Lane`).  Oracle-v1 is a
//! lower-level, fixed-latency batch of BigWorld primitives.  This module owns
//! the reversible mapping and retains the metadata that is intentionally not
//! sent back by the native client.

use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::PI;

use thiserror::Error;

use crate::bot_sim::{
    BallisticQuery, BallisticReceipt, BallisticSolution, GroundQuery, GroundReceipt, LaneQuery,
    LaneReceipt, MotionQuery, MotionReceipt, MotionStatus, OracleQueryId, OracleQueryIntent,
    OracleQueryKind, OracleReceipts, TargetKind, Vec3 as BotVec3, VisibilityQuery,
    VisibilityReceipt, ORACLE_LATENCY_TICKS,
};
use crate::protocol::{
    BatchSequence, EntityRef, OracleLineage, OracleOperation, OracleV1BatchKey, OracleV1BatchReply,
    OracleV1BatchRequest, OracleV1Query, OracleV1Result, OracleV1ResultStatus, QueryGeneration,
    QueryId, QueryOutcome, RayHit, SegmentCastPrimitive, SurfaceSample, Tick, Vec3 as ProtocolVec3,
    WorldRevision, MAX_ORACLE_BATCH_QUERIES, MAX_ORACLE_PRIMITIVE_OPERATIONS,
    ORACLE_PIPELINE_TICKS, ORACLE_PROTOCOL_VERSION,
};
use crate::validator::{
    validate_oracle_v1_reply, validate_oracle_v1_request, OracleV1ValidationError,
};

const MAX_FLIGHT_SECONDS: f64 = 20.0;
const MOTION_MINIMUM_REACH: f64 = 1.0;
const MOTION_BASE_REACH: f64 = 1.5;
const GROUND_NORMAL_MINIMUM_LENGTH: f64 = 0.5;
const GROUND_NORMAL_MAXIMUM_LENGTH: f64 = 1.5;
const QUERY_LANES: Tick = ORACLE_PIPELINE_TICKS + 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum SimulationEntity {
    Bot(u32),
    Human(u32),
}

impl SimulationEntity {
    fn from_target(kind: TargetKind, id: u32) -> Self {
        match kind {
            TargetKind::Bot => Self::Bot(id),
            TargetKind::Human => Self::Human(id),
        }
    }

    fn id(self) -> u32 {
        match self {
            Self::Bot(id) | Self::Human(id) => id,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct OracleEntityMap {
    entities: BTreeMap<SimulationEntity, EntityRef>,
}

impl OracleEntityMap {
    pub fn insert(
        &mut self,
        entity: SimulationEntity,
        native: EntityRef,
    ) -> Result<Option<EntityRef>, OracleAdapterError> {
        if entity.id() == 0 || native.entity_id <= 0 || native.generation == 0 {
            return Err(OracleAdapterError::InvalidEntityFence { entity, native });
        }
        Ok(self.entities.insert(entity, native))
    }

    pub fn insert_bot(
        &mut self,
        bot_id: u32,
        native: EntityRef,
    ) -> Result<Option<EntityRef>, OracleAdapterError> {
        self.insert(SimulationEntity::Bot(bot_id), native)
    }

    pub fn insert_human(
        &mut self,
        player_id: u32,
        native: EntityRef,
    ) -> Result<Option<EntityRef>, OracleAdapterError> {
        self.insert(SimulationEntity::Human(player_id), native)
    }

    pub fn get(&self, entity: SimulationEntity) -> Option<EntityRef> {
        self.entities.get(&entity).copied()
    }

    fn require(&self, entity: SimulationEntity) -> Result<EntityRef, OracleAdapterError> {
        self.get(entity)
            .ok_or(OracleAdapterError::MissingEntityFence { entity })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct OracleAdapterConfig {
    /// The native bridge defines the concrete collision groups represented by
    /// these masks.  The conservative default asks it to include everything.
    pub motion_collision_mask: u32,
    pub visibility_collision_mask: u32,
    pub projectile_collision_mask: u32,
    pub trajectory_step_seconds: f64,
    pub muzzle_node: String,
    pub muzzle_origin_tolerance: f64,
}

impl Default for OracleAdapterConfig {
    fn default() -> Self {
        Self {
            motion_collision_mask: u32::MAX,
            visibility_collision_mask: u32::MAX,
            projectile_collision_mask: u32::MAX,
            trajectory_step_seconds: 0.12,
            muzzle_node: "HP_gunFire".to_owned(),
            muzzle_origin_tolerance: 0.75,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FailedOracleIntent {
    pub id: OracleQueryId,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DecodedOracleBatch {
    pub key: OracleV1BatchKey,
    pub apply_tick: Tick,
    pub receipts: BTreeMap<u32, OracleReceipts>,
    pub failed: Vec<FailedOracleIntent>,
}

impl DecodedOracleBatch {
    /// Merge one decoded wire batch into the tick-wide receipt map.  An intent
    /// is atomic and must therefore never appear in two batches.
    pub fn merge_into(
        self,
        target: &mut BTreeMap<u32, OracleReceipts>,
    ) -> Result<Vec<FailedOracleIntent>, OracleAdapterError> {
        for (bot_id, incoming) in self.receipts {
            let current = target.entry(bot_id).or_default();
            merge_slot(&mut current.ground, incoming.ground, bot_id, "ground")?;
            merge_slot(&mut current.motion, incoming.motion, bot_id, "motion")?;
            merge_slot(
                &mut current.visibility,
                incoming.visibility,
                bot_id,
                "visibility",
            )?;
            merge_slot(
                &mut current.ballistic,
                incoming.ballistic,
                bot_id,
                "ballistic",
            )?;
            merge_slot(&mut current.lane, incoming.lane, bot_id, "lane")?;
        }
        Ok(self.failed)
    }
}

#[derive(Debug, Error)]
pub enum OracleAdapterError {
    #[error("oracle adapter lineage must have non-zero round, authority, and oracle generations")]
    InvalidLineage,
    #[error("oracle adapter configuration field {field} is invalid")]
    InvalidConfig { field: &'static str },
    #[error("oracle entity {entity:?} has invalid native fence {native:?}")]
    InvalidEntityFence {
        entity: SimulationEntity,
        native: EntityRef,
    },
    #[error("oracle entity fence is unavailable for {entity:?}")]
    MissingEntityFence { entity: SimulationEntity },
    #[error("cannot build an empty oracle intent batch")]
    EmptyIntents,
    #[error("oracle intent {id:?} is invalid: {reason}")]
    InvalidIntent {
        id: OracleQueryId,
        reason: &'static str,
    },
    #[error("duplicate oracle intent for bot {bot_id}, kind {kind:?}")]
    DuplicateIntent { bot_id: u32, kind: OracleQueryKind },
    #[error(
        "oracle intent {id:?} requires {queries} queries and {primitives} primitives, exceeding one batch"
    )]
    IntentExceedsBatch {
        id: OracleQueryId,
        queries: usize,
        primitives: usize,
    },
    #[error("oracle {counter} counter is exhausted")]
    CounterExhausted { counter: &'static str },
    #[error("oracle batch sequence floor regressed from {current} to {requested}")]
    BatchSequenceRegression {
        current: BatchSequence,
        requested: BatchSequence,
    },
    #[error(transparent)]
    Protocol(#[from] OracleV1ValidationError),
    #[error("oracle reply references unknown adapter batch {key:?}")]
    UnknownBatch { key: OracleV1BatchKey },
    #[error("oracle request does not match adapter plan for {key:?}")]
    RequestMismatch { key: OracleV1BatchKey },
    #[error("duplicate decoded {kind} receipt for bot {bot_id}")]
    DuplicateDecodedReceipt { bot_id: u32, kind: &'static str },
}

#[derive(Clone, Debug)]
struct PrimitiveSpec {
    key: String,
    entity: EntityRef,
    operation: OracleOperation,
}

#[derive(Clone, Debug)]
enum IntentTemplate {
    Ground {
        query: GroundQuery,
        has_water: bool,
    },
    Motion(MotionQuery),
    Visibility {
        query: VisibilityQuery,
        target: EntityRef,
    },
    Ballistic {
        query: BallisticQuery,
        target: EntityRef,
        solution: Option<BallisticSolution>,
    },
    Lane {
        query: LaneQuery,
        target: EntityRef,
        provable: bool,
    },
}

#[derive(Clone, Debug)]
struct ExpandedIntent {
    id: OracleQueryId,
    primitives: Vec<PrimitiveSpec>,
    template: IntentTemplate,
}

impl ExpandedIntent {
    fn primitive_count(&self) -> usize {
        self.primitives
            .iter()
            .map(|primitive| primitive.operation.primitive_count())
            .sum()
    }
}

#[derive(Clone, Debug)]
struct IntentPlan {
    primitive_ids: Vec<QueryId>,
    template: IntentTemplate,
}

#[derive(Clone, Debug)]
struct BatchPlan {
    request: OracleV1BatchRequest,
    intents: Vec<IntentPlan>,
}

#[derive(Clone, Debug)]
pub struct OracleAdapter {
    lineage: OracleLineage,
    world_revision: WorldRevision,
    config: OracleAdapterConfig,
    next_batch_seq: BatchSequence,
    next_query_id: QueryId,
    query_generations: BTreeMap<String, QueryGeneration>,
    pending: BTreeMap<OracleV1BatchKey, BatchPlan>,
}

impl OracleAdapter {
    pub fn new(
        lineage: OracleLineage,
        world_revision: WorldRevision,
    ) -> Result<Self, OracleAdapterError> {
        Self::with_config(lineage, world_revision, OracleAdapterConfig::default())
    }

    pub fn with_config(
        lineage: OracleLineage,
        world_revision: WorldRevision,
        config: OracleAdapterConfig,
    ) -> Result<Self, OracleAdapterError> {
        validate_lineage(lineage)?;
        validate_config(&config)?;
        Ok(Self {
            lineage,
            world_revision,
            config,
            next_batch_seq: 1,
            next_query_id: 1,
            query_generations: BTreeMap::new(),
            pending: BTreeMap::new(),
        })
    }

    pub fn lineage(&self) -> OracleLineage {
        self.lineage
    }

    pub fn world_revision(&self) -> WorldRevision {
        self.world_revision
    }

    pub fn pending_batches(&self) -> usize {
        self.pending.len()
    }

    /// Next sequence reserved for a bot-intent batch. A shared oracle
    /// multiplexer advances this floor after assigning intervening projectile
    /// batches so every request on the one native connection is monotonic.
    pub fn next_batch_sequence(&self) -> BatchSequence {
        self.next_batch_seq
    }

    pub fn advance_batch_sequence_floor(
        &mut self,
        next: BatchSequence,
    ) -> Result<(), OracleAdapterError> {
        if next < self.next_batch_seq {
            return Err(OracleAdapterError::BatchSequenceRegression {
                current: self.next_batch_seq,
                requested: next,
            });
        }
        self.next_batch_seq = next;
        Ok(())
    }

    pub fn set_world_revision(&mut self, revision: WorldRevision) {
        self.world_revision = revision;
    }

    /// Install a strictly newer incarnation and abandon every old plan.
    pub fn activate_lineage(
        &mut self,
        lineage: OracleLineage,
        world_revision: WorldRevision,
    ) -> Result<usize, OracleAdapterError> {
        validate_lineage(lineage)?;
        if lineage <= self.lineage {
            return Err(OracleAdapterError::InvalidLineage);
        }
        let abandoned = self.pending.len();
        self.lineage = lineage;
        self.world_revision = world_revision;
        self.next_batch_seq = 1;
        self.next_query_id = 1;
        self.query_generations.clear();
        self.pending.clear();
        Ok(abandoned)
    }

    /// Expand and deterministically pack all intents issued by one simulation
    /// tick.  High-level intents remain atomic across wire batches.
    pub fn build_batches(
        &mut self,
        issued_tick: Tick,
        intents: &[OracleQueryIntent],
        entities: &OracleEntityMap,
    ) -> Result<Vec<OracleV1BatchRequest>, OracleAdapterError> {
        if intents.is_empty() {
            return Err(OracleAdapterError::EmptyIntents);
        }
        let apply_tick = issued_tick.checked_add(ORACLE_PIPELINE_TICKS).ok_or(
            OracleAdapterError::CounterExhausted {
                counter: "apply_tick",
            },
        )?;

        let mut ordered = intents.to_vec();
        ordered.sort_by_key(|intent| {
            let id = intent_id(intent);
            (id.bot_id, id.kind)
        });
        let mut seen = BTreeSet::new();
        for intent in &ordered {
            let id = intent_id(intent);
            validate_intent_id(id, intent_kind(intent), issued_tick, apply_tick)?;
            if !seen.insert((id.bot_id, id.kind)) {
                return Err(OracleAdapterError::DuplicateIntent {
                    bot_id: id.bot_id,
                    kind: id.kind,
                });
            }
            validate_intent(intent)?;
        }

        let mut expanded = Vec::with_capacity(ordered.len());
        for intent in ordered {
            expanded.push(expand_intent(intent, entities, &self.config)?);
        }

        let mut groups: Vec<Vec<ExpandedIntent>> = Vec::new();
        let mut current = Vec::new();
        let mut current_queries = 0usize;
        let mut current_primitives = 0usize;
        for intent in expanded {
            let queries = intent.primitives.len();
            let primitives = intent.primitive_count();
            if queries > MAX_ORACLE_BATCH_QUERIES || primitives > MAX_ORACLE_PRIMITIVE_OPERATIONS {
                return Err(OracleAdapterError::IntentExceedsBatch {
                    id: intent.id,
                    queries,
                    primitives,
                });
            }
            if !current.is_empty()
                && (current_queries + queries > MAX_ORACLE_BATCH_QUERIES
                    || current_primitives + primitives > MAX_ORACLE_PRIMITIVE_OPERATIONS)
            {
                groups.push(std::mem::take(&mut current));
                current_queries = 0;
                current_primitives = 0;
            }
            current_queries += queries;
            current_primitives += primitives;
            current.push(intent);
        }
        if !current.is_empty() {
            groups.push(current);
        }

        // Build transactionally: a failed validation must not consume a
        // sequence, query id, or stable-key generation.
        let mut next_batch_seq = self.next_batch_seq;
        let mut next_query_id = self.next_query_id;
        let mut generations = self.query_generations.clone();
        let mut requests = Vec::with_capacity(groups.len());
        let mut new_plans = Vec::with_capacity(groups.len());

        for group in groups {
            let batch_seq = next_batch_seq;
            next_batch_seq =
                next_batch_seq
                    .checked_add(1)
                    .ok_or(OracleAdapterError::CounterExhausted {
                        counter: "batch_seq",
                    })?;
            let mut wire_queries = Vec::new();
            let mut plans = Vec::with_capacity(group.len());
            for intent in group {
                let mut primitive_ids = Vec::with_capacity(intent.primitives.len());
                for primitive in intent.primitives {
                    let query_id = next_query_id;
                    next_query_id = next_query_id.checked_add(1).ok_or(
                        OracleAdapterError::CounterExhausted {
                            counter: "query_id",
                        },
                    )?;
                    let previous = generations.get(&primitive.key).copied().unwrap_or(0);
                    let query_generation =
                        previous
                            .checked_add(1)
                            .ok_or(OracleAdapterError::CounterExhausted {
                                counter: "query_generation",
                            })?;
                    generations.insert(primitive.key.clone(), query_generation);
                    primitive_ids.push(query_id);
                    wire_queries.push(OracleV1Query {
                        query_id,
                        key: primitive.key,
                        query_generation,
                        entity: primitive.entity,
                        operation: primitive.operation,
                    });
                }
                plans.push(IntentPlan {
                    primitive_ids,
                    template: intent.template,
                });
            }
            let request = OracleV1BatchRequest {
                protocol_version: ORACLE_PROTOCOL_VERSION,
                round_id: self.lineage.round_id,
                authority_epoch: self.lineage.authority_epoch,
                oracle_generation: self.lineage.oracle_generation,
                batch_seq,
                issued_tick,
                apply_tick,
                world_revision: self.world_revision,
                queries: wire_queries,
            };
            validate_oracle_v1_request(&request)?;
            let key = request.key();
            new_plans.push((
                key,
                BatchPlan {
                    request: request.clone(),
                    intents: plans,
                },
            ));
            requests.push(request);
        }

        self.next_batch_seq = next_batch_seq;
        self.next_query_id = next_query_id;
        self.query_generations = generations;
        for (key, plan) in new_plans {
            debug_assert!(!self.pending.contains_key(&key));
            self.pending.insert(key, plan);
        }
        Ok(requests)
    }

    /// Decode one broker-applied reply.  The plan is consumed before any
    /// validation so malformed or partial traffic can never be retried into
    /// gameplay state.
    pub fn decode_reply(
        &mut self,
        request: &OracleV1BatchRequest,
        reply: &OracleV1BatchReply,
    ) -> Result<DecodedOracleBatch, OracleAdapterError> {
        let key = request.key();
        let plan = self
            .pending
            .remove(&key)
            .ok_or(OracleAdapterError::UnknownBatch { key })?;
        if &plan.request != request {
            return Err(OracleAdapterError::RequestMismatch { key });
        }
        validate_oracle_v1_reply(reply, request)?;
        let results: BTreeMap<_, _> = reply
            .results
            .iter()
            .map(|result| (result.query_id, result))
            .collect();
        let mut decoded = DecodedOracleBatch {
            key,
            apply_tick: request.apply_tick,
            receipts: BTreeMap::new(),
            failed: Vec::new(),
        };
        for intent in &plan.intents {
            decode_intent(intent, &results, &self.config, &mut decoded)?;
        }
        Ok(decoded)
    }

    /// Forget a timed-out broker request.  No receipt is synthesized: absence
    /// is the fail-closed result for every due intent.
    pub fn discard_request(
        &mut self,
        request: &OracleV1BatchRequest,
    ) -> Result<Vec<OracleQueryId>, OracleAdapterError> {
        let key = request.key();
        let plan = self
            .pending
            .remove(&key)
            .ok_or(OracleAdapterError::UnknownBatch { key })?;
        if &plan.request != request {
            return Err(OracleAdapterError::RequestMismatch { key });
        }
        Ok(plan
            .intents
            .iter()
            .map(|intent| template_id(&intent.template))
            .collect())
    }
}

fn expand_intent(
    intent: OracleQueryIntent,
    entities: &OracleEntityMap,
    config: &OracleAdapterConfig,
) -> Result<ExpandedIntent, OracleAdapterError> {
    match intent {
        OracleQueryIntent::Ground(query) => {
            let source = entities.require(SimulationEntity::Bot(query.id.bot_id))?;
            let positions = ground_positions(&query)?;
            let mut primitives = vec![PrimitiveSpec {
                key: stable_key(query.id, "ground", "surface"),
                entity: source,
                operation: OracleOperation::GroundSampleBatch { positions },
            }];
            if query.include_water_depth {
                primitives.push(PrimitiveSpec {
                    key: stable_key(query.id, "ground", "water"),
                    entity: source,
                    operation: OracleOperation::WaterSample {
                        position: protocol_vec(query.position, query.id, "position")?,
                    },
                });
            }
            Ok(ExpandedIntent {
                id: query.id,
                primitives,
                template: IntentTemplate::Ground {
                    has_water: query.include_water_depth,
                    query,
                },
            })
        }
        OracleQueryIntent::Motion(query) => {
            let source = entities.require(SimulationEntity::Bot(query.id.bot_id))?;
            let segments = motion_segments(&query, config.motion_collision_mask)?;
            Ok(ExpandedIntent {
                id: query.id,
                primitives: vec![PrimitiveSpec {
                    key: stable_key(query.id, "motion", "corridor"),
                    entity: source,
                    operation: OracleOperation::SegmentCastBatch { segments },
                }],
                template: IntentTemplate::Motion(query),
            })
        }
        OracleQueryIntent::Visibility(query) => {
            let target_key = SimulationEntity::from_target(query.target_kind, query.target_id);
            let target = entities.require(target_key)?;
            let start = protocol_vec(query.source_position, query.id, "source_position")?;
            let end = protocol_vec(query.target_position, query.id, "target_position")?;
            ensure_segment(start, end, query.id, "visibility")?;
            Ok(ExpandedIntent {
                id: query.id,
                primitives: vec![
                    PrimitiveSpec {
                        key: stable_key(query.id, "visibility", "world"),
                        entity: target,
                        operation: OracleOperation::SegmentCast {
                            start,
                            end,
                            collision_mask: config.visibility_collision_mask,
                        },
                    },
                    PrimitiveSpec {
                        key: stable_key(query.id, "visibility", "target"),
                        entity: target,
                        operation: OracleOperation::VehicleHitTest { start, end, target },
                    },
                ],
                template: IntentTemplate::Visibility { query, target },
            })
        }
        OracleQueryIntent::Ballistic(query) => {
            let target_key = SimulationEntity::from_target(query.target_kind, query.target_id);
            let target = entities.require(target_key)?;
            let solution = ballistic_intercept(&query);
            let segments = match &solution {
                Some(solution) => ballistic_segments(
                    query.source_position,
                    solution.yaw,
                    solution.pitch,
                    query.shell_speed,
                    query.gravity,
                    solution.flight_time,
                    config.trajectory_step_seconds,
                    config.projectile_collision_mask,
                    query.id,
                )?,
                None => vec![direct_segment(
                    query.source_position,
                    query.target_position,
                    config.projectile_collision_mask,
                    query.id,
                    "ballistic_fallback",
                )?],
            };
            Ok(ExpandedIntent {
                id: query.id,
                primitives: vec![PrimitiveSpec {
                    key: stable_key(query.id, "ballistic", "trajectory"),
                    entity: target,
                    operation: OracleOperation::SegmentCastBatch { segments },
                }],
                template: IntentTemplate::Ballistic {
                    query,
                    target,
                    solution,
                },
            })
        }
        OracleQueryIntent::Lane(query) => {
            let source = entities.require(SimulationEntity::Bot(query.id.bot_id))?;
            let target_key = SimulationEntity::from_target(query.target_kind, query.target_id);
            let target = entities.require(target_key)?;
            let mut primitives = vec![PrimitiveSpec {
                key: stable_key(query.id, "lane", "muzzle"),
                entity: source,
                operation: OracleOperation::NodeTransform {
                    node: config.muzzle_node.clone(),
                },
            }];
            let provable = if query.shell_speed * query.flight_time > query.max_distance + 1.0e-9 {
                false
            } else {
                let segments = ballistic_segments(
                    query.source_position,
                    query.shot_yaw,
                    -query.shot_pitch,
                    query.shell_speed,
                    query.gravity,
                    query.flight_time,
                    config.trajectory_step_seconds,
                    config.projectile_collision_mask,
                    query.id,
                )?;
                primitives.push(PrimitiveSpec {
                    key: stable_key(query.id, "lane", "trajectory"),
                    entity: target,
                    operation: OracleOperation::SegmentCastBatch { segments },
                });
                true
            };
            Ok(ExpandedIntent {
                id: query.id,
                primitives,
                template: IntentTemplate::Lane {
                    query,
                    target,
                    provable,
                },
            })
        }
    }
}

fn decode_intent(
    plan: &IntentPlan,
    results: &BTreeMap<QueryId, &OracleV1Result>,
    config: &OracleAdapterConfig,
    decoded: &mut DecodedOracleBatch,
) -> Result<(), OracleAdapterError> {
    match &plan.template {
        IntentTemplate::Ground { query, has_water } => {
            let surface = ok_outcome(results, plan.primitive_ids[0]);
            let water = has_water.then(|| ok_outcome(results, plan.primitive_ids[1]));
            match decode_ground(query, surface, water) {
                Ok(receipt) => insert_ground(decoded, receipt)?,
                Err(reason) => decoded.failed.push(failed(query.id, reason)),
            }
        }
        IntentTemplate::Motion(query) => {
            match ok_outcome(results, plan.primitive_ids[0])
                .and_then(|outcome| decode_motion(query, outcome))
            {
                Ok(receipt) => insert_motion(decoded, receipt)?,
                Err(reason) => decoded.failed.push(failed(query.id, reason)),
            }
        }
        IntentTemplate::Visibility { query, target } => {
            let result = ok_outcome(results, plan.primitive_ids[0]).and_then(|world| {
                let target_hit = ok_outcome(results, plan.primitive_ids[1])?;
                decode_visibility(query, *target, world, target_hit)
            });
            let (receipt, failure) = match result {
                Ok(receipt) => (receipt, None),
                Err(reason) => (
                    VisibilityReceipt {
                        id: query.id,
                        target_kind: query.target_kind,
                        target_id: query.target_id,
                        source_position: query.source_position,
                        target_position: query.target_position,
                        visible: false,
                    },
                    Some(reason),
                ),
            };
            insert_visibility(decoded, receipt)?;
            if let Some(reason) = failure {
                decoded.failed.push(failed(query.id, reason));
            }
        }
        IntentTemplate::Ballistic {
            query,
            target,
            solution,
        } => {
            let result = ok_outcome(results, plan.primitive_ids[0])
                .and_then(|outcome| decode_ballistic(query, *target, solution.clone(), outcome));
            let (receipt, failure) = match result {
                Ok(receipt) => (receipt, None),
                Err(reason) => (
                    BallisticReceipt {
                        id: query.id,
                        target_kind: query.target_kind,
                        target_id: query.target_id,
                        shell_index: query.shell_index,
                        source_position: query.source_position,
                        target_position: query.target_position,
                        target_velocity: query.target_velocity,
                        solution: None,
                    },
                    Some(reason),
                ),
            };
            insert_ballistic(decoded, receipt)?;
            if let Some(reason) = failure {
                decoded.failed.push(failed(query.id, reason));
            }
        }
        IntentTemplate::Lane {
            query,
            target,
            provable,
        } => {
            let result = if !provable {
                Err("lane has no same-tick ballistic shell context".to_owned())
            } else {
                ok_outcome(results, plan.primitive_ids[0]).and_then(|node| {
                    let trajectory = ok_outcome(results, plan.primitive_ids[1])?;
                    decode_lane(query, *target, node, trajectory, config)
                })
            };
            let (receipt, failure) = match result {
                Ok(receipt) => (receipt, None),
                Err(reason) => (
                    LaneReceipt {
                        id: query.id,
                        target_kind: query.target_kind,
                        target_id: query.target_id,
                        fire_seq: query.fire_seq,
                        shell_index: query.shell_index,
                        source_position: query.source_position,
                        target_position: query.target_position,
                        clear: false,
                        origin: query.source_position,
                        shot_yaw: query.shot_yaw,
                        shot_pitch: query.shot_pitch,
                        flight_time: query.flight_time,
                    },
                    Some(reason),
                ),
            };
            insert_lane(decoded, receipt)?;
            if let Some(reason) = failure {
                decoded.failed.push(failed(query.id, reason));
            }
        }
    }
    Ok(())
}

fn decode_ground(
    query: &GroundQuery,
    surface: Result<&QueryOutcome, String>,
    water: Option<Result<&QueryOutcome, String>>,
) -> Result<GroundReceipt, String> {
    let samples = match surface? {
        QueryOutcome::GroundSampleBatch { samples } => samples,
        _ => return Err("ground surface reply has the wrong outcome".to_owned()),
    };
    if samples.len() != 5 || samples.iter().any(Option::is_none) {
        return Err("ground surface reply is incomplete".to_owned());
    }
    let samples: Vec<_> = samples
        .iter()
        .map(|sample| sample.as_ref().unwrap())
        .collect();
    for sample in &samples {
        validate_surface_for_pose(sample)?;
    }
    let centre = samples[0];
    let length = (2.0 * query.half_length).max(3.0);
    let slope_pitch =
        -((f64::from(samples[1].height) - f64::from(samples[2].height)).atan2(length)) * 0.9;
    let supported = centre.normal.y > 0.05;
    let water_depth = match water {
        None => None,
        Some(result) => match result? {
            QueryOutcome::WaterSample { height } => Some(
                height
                    .map(|height| (f64::from(height) - f64::from(centre.height)).max(0.0))
                    .unwrap_or(0.0),
            ),
            _ => return Err("water reply has the wrong outcome".to_owned()),
        },
    };
    Ok(GroundReceipt {
        id: query.id,
        sample_position: query.position,
        sample_yaw: query.yaw,
        contains_pose: true,
        supported,
        ground_height: f64::from(centre.height),
        slope_pitch,
        water_depth,
    })
}

fn decode_motion(query: &MotionQuery, outcome: &QueryOutcome) -> Result<MotionReceipt, String> {
    let hits = match outcome {
        QueryOutcome::SegmentCastBatch { hits } => hits,
        _ => return Err("motion reply has the wrong outcome".to_owned()),
    };
    if hits.len() != 3 {
        return Err("motion corridor reply is incomplete".to_owned());
    }
    // Generic ray hits cannot prove crushability or a soft cap.  Any hit is
    // therefore a hard obstacle; only three explicit misses authorize motion.
    let status = if hits.iter().all(Option::is_none) {
        MotionStatus::Clear
    } else {
        MotionStatus::Hard
    };
    Ok(MotionReceipt {
        id: query.id,
        sample_position: query.position,
        contains_pose: true,
        travel_yaw: query.travel_yaw,
        status,
    })
}

fn decode_visibility(
    query: &VisibilityQuery,
    target: EntityRef,
    world: &QueryOutcome,
    target_hit: &QueryOutcome,
) -> Result<VisibilityReceipt, String> {
    let world_clear = match world {
        QueryOutcome::SegmentCast { hit } => hit
            .as_ref()
            .is_none_or(|hit| hit.hit_entity == Some(target)),
        _ => return Err("visibility world reply has the wrong outcome".to_owned()),
    };
    let target_proved = match target_hit {
        QueryOutcome::VehicleHitTest { hit } => hit.is_some(),
        _ => return Err("visibility target reply has the wrong outcome".to_owned()),
    };
    Ok(VisibilityReceipt {
        id: query.id,
        target_kind: query.target_kind,
        target_id: query.target_id,
        source_position: query.source_position,
        target_position: query.target_position,
        visible: world_clear && target_proved,
    })
}

fn decode_ballistic(
    query: &BallisticQuery,
    target: EntityRef,
    solution: Option<BallisticSolution>,
    outcome: &QueryOutcome,
) -> Result<BallisticReceipt, String> {
    let hits = match outcome {
        QueryOutcome::SegmentCastBatch { hits } => hits,
        _ => return Err("ballistic reply has the wrong outcome".to_owned()),
    };
    let solution = solution.filter(|_| ray_path_clear(hits, target));
    Ok(BallisticReceipt {
        id: query.id,
        target_kind: query.target_kind,
        target_id: query.target_id,
        shell_index: query.shell_index,
        source_position: query.source_position,
        target_position: query.target_position,
        target_velocity: query.target_velocity,
        solution,
    })
}

fn decode_lane(
    query: &LaneQuery,
    target: EntityRef,
    node: &QueryOutcome,
    trajectory: &QueryOutcome,
    config: &OracleAdapterConfig,
) -> Result<LaneReceipt, String> {
    let transform = match node {
        QueryOutcome::NodeTransform {
            transform: Some(transform),
        } => transform,
        QueryOutcome::NodeTransform { transform: None } => {
            return Err("native muzzle node is unavailable".to_owned());
        }
        _ => return Err("muzzle reply has the wrong outcome".to_owned()),
    };
    let hits = match trajectory {
        QueryOutcome::SegmentCastBatch { hits } => hits,
        _ => return Err("lane trajectory reply has the wrong outcome".to_owned()),
    };
    let origin = bot_vec(transform.position);
    let origin_delta = distance(origin, query.source_position);
    if origin_delta > config.muzzle_origin_tolerance {
        return Err(format!(
            "native muzzle moved {origin_delta:.3} m beyond the admitted source pose"
        ));
    }
    let clear = ray_path_clear(hits, target);
    Ok(LaneReceipt {
        id: query.id,
        target_kind: query.target_kind,
        target_id: query.target_id,
        fire_seq: query.fire_seq,
        shell_index: query.shell_index,
        source_position: query.source_position,
        target_position: query.target_position,
        clear,
        origin,
        shot_yaw: query.shot_yaw,
        shot_pitch: query.shot_pitch,
        flight_time: query.flight_time,
    })
}

fn ok_outcome<'a>(
    results: &'a BTreeMap<QueryId, &OracleV1Result>,
    query_id: QueryId,
) -> Result<&'a QueryOutcome, String> {
    let result = results
        .get(&query_id)
        .ok_or_else(|| format!("oracle result {query_id} is missing"))?;
    match &result.status {
        OracleV1ResultStatus::Ok { outcome } => Ok(outcome),
        OracleV1ResultStatus::Unavailable { code, message } => {
            Err(format!("native oracle unavailable: {code}: {message}"))
        }
    }
}

fn validate_surface_for_pose(sample: &SurfaceSample) -> Result<(), String> {
    let normal = sample.normal;
    let length =
        (f64::from(normal.x).powi(2) + f64::from(normal.y).powi(2) + f64::from(normal.z).powi(2))
            .sqrt();
    if !(GROUND_NORMAL_MINIMUM_LENGTH..=GROUND_NORMAL_MAXIMUM_LENGTH).contains(&length) {
        return Err("ground normal is not a usable native unit vector".to_owned());
    }
    Ok(())
}

fn ray_path_clear(hits: &[Option<RayHit>], target: EntityRef) -> bool {
    hits.iter().all(|hit| {
        hit.as_ref()
            .is_none_or(|hit| hit.hit_entity == Some(target))
    })
}

fn ground_positions(query: &GroundQuery) -> Result<Vec<ProtocolVec3>, OracleAdapterError> {
    let sine = query.yaw.sin();
    let cosine = query.yaw.cos();
    let centre = query.position;
    let front = BotVec3::new(
        centre.x + sine * query.half_length,
        centre.y,
        centre.z + cosine * query.half_length,
    );
    let rear = BotVec3::new(
        centre.x - sine * query.half_length,
        centre.y,
        centre.z - cosine * query.half_length,
    );
    let right = BotVec3::new(
        centre.x + cosine * query.half_width,
        centre.y,
        centre.z - sine * query.half_width,
    );
    let left = BotVec3::new(
        centre.x - cosine * query.half_width,
        centre.y,
        centre.z + sine * query.half_width,
    );
    [centre, front, rear, right, left]
        .into_iter()
        .map(|position| protocol_vec(position, query.id, "ground_position"))
        .collect()
}

fn motion_segments(
    query: &MotionQuery,
    collision_mask: u32,
) -> Result<Vec<SegmentCastPrimitive>, OracleAdapterError> {
    let dt = query.dt_us as f64 / 1_000_000.0;
    let reach = (query.speed.abs() * dt * 3.0 + MOTION_BASE_REACH).max(MOTION_MINIMUM_REACH);
    let sine = query.travel_yaw.sin();
    let cosine = query.travel_yaw.cos();
    let lateral_x = cosine;
    let lateral_z = -sine;
    let mut segments = Vec::with_capacity(3);
    for offset in [-query.half_width, 0.0, query.half_width] {
        let start = BotVec3::new(
            query.position.x + sine * query.half_length + lateral_x * offset,
            query.position.y,
            query.position.z + cosine * query.half_length + lateral_z * offset,
        );
        let end = BotVec3::new(start.x + sine * reach, start.y, start.z + cosine * reach);
        segments.push(direct_segment(
            start,
            end,
            collision_mask,
            query.id,
            "motion_corridor",
        )?);
    }
    Ok(segments)
}

fn ballistic_intercept(query: &BallisticQuery) -> Option<BallisticSolution> {
    let mut aim = query.target_position;
    for _ in 0..4 {
        let roots = ballistic_roots(
            query.source_position,
            aim,
            query.shell_speed,
            query.gravity,
            query.pitch_limits,
        );
        let root = if query.prefer_high_arc {
            roots.last().copied()
        } else {
            roots.first().copied()
        }?;
        aim = BotVec3::new(
            query.target_position.x + query.target_velocity.x * root.1,
            query.target_position.y + query.target_velocity.y * root.1,
            query.target_position.z + query.target_velocity.z * root.1,
        );
    }
    let roots = ballistic_roots(
        query.source_position,
        aim,
        query.shell_speed,
        query.gravity,
        query.pitch_limits,
    );
    let (pitch, flight_time) = if query.prefer_high_arc {
        roots.last().copied()
    } else {
        roots.first().copied()
    }?;
    if query.shell_speed * flight_time > query.max_distance + 1.0e-9 {
        return None;
    }
    let delta_x = aim.x - query.source_position.x;
    let delta_z = aim.z - query.source_position.z;
    let yaw = delta_x.atan2(delta_z);
    Some(BallisticSolution {
        aim_position: aim,
        yaw,
        pitch,
        flight_time,
    })
}

fn ballistic_roots(
    source: BotVec3,
    target: BotVec3,
    speed: f64,
    gravity: f64,
    pitch_limits: (f64, f64),
) -> Vec<(f64, f64)> {
    let delta_x = target.x - source.x;
    let delta_z = target.z - source.z;
    let horizontal = delta_x.hypot(delta_z);
    if horizontal <= 0.1 {
        return Vec::new();
    }
    let delta_y = target.y - source.y;
    let acceleration = gravity.abs();
    let speed_sq = speed * speed;
    let discriminant = speed_sq * speed_sq
        - acceleration * (acceleration * horizontal * horizontal + 2.0 * delta_y * speed_sq);
    if discriminant < 0.0 {
        return Vec::new();
    }
    let root = discriminant.max(0.0).sqrt();
    let mut result = Vec::new();
    for numerator in [speed_sq - root, speed_sq + root] {
        let elevation = (numerator / (acceleration * horizontal)).atan();
        let pitch = -elevation;
        if pitch < pitch_limits.0 - 0.0001 || pitch > pitch_limits.1 + 0.0001 {
            continue;
        }
        let horizontal_speed = speed * elevation.cos();
        if horizontal_speed <= 0.01 {
            continue;
        }
        let flight_time = horizontal / horizontal_speed;
        if flight_time <= 0.0 || flight_time > MAX_FLIGHT_SECONDS {
            continue;
        }
        if result
            .last()
            .is_none_or(|previous: &(f64, f64)| (previous.0 - pitch).abs() > 0.000_01)
        {
            result.push((pitch, flight_time));
        }
    }
    result.sort_by(|left, right| left.1.total_cmp(&right.1));
    result
}

#[allow(clippy::too_many_arguments)]
fn ballistic_segments(
    source: BotVec3,
    yaw: f64,
    rendered_pitch: f64,
    speed: f64,
    gravity: f64,
    flight_time: f64,
    step: f64,
    collision_mask: u32,
    id: OracleQueryId,
) -> Result<Vec<SegmentCastPrimitive>, OracleAdapterError> {
    let count = (flight_time / step).ceil().max(1.0) as usize;
    let mut points = Vec::with_capacity(count + 1);
    for index in 0..=count {
        let time = flight_time * index as f64 / count as f64;
        let horizontal_speed = rendered_pitch.cos() * speed;
        points.push(BotVec3::new(
            source.x + yaw.sin() * horizontal_speed * time,
            source.y - rendered_pitch.sin() * speed * time - 0.5 * gravity.abs() * time * time,
            source.z + yaw.cos() * horizontal_speed * time,
        ));
    }
    points
        .windows(2)
        .map(|window| direct_segment(window[0], window[1], collision_mask, id, "trajectory"))
        .collect()
}

fn direct_segment(
    start: BotVec3,
    end: BotVec3,
    collision_mask: u32,
    id: OracleQueryId,
    field: &'static str,
) -> Result<SegmentCastPrimitive, OracleAdapterError> {
    let start = protocol_vec(start, id, field)?;
    let end = protocol_vec(end, id, field)?;
    ensure_segment(start, end, id, field)?;
    Ok(SegmentCastPrimitive {
        start,
        end,
        collision_mask,
    })
}

fn protocol_vec(
    value: BotVec3,
    id: OracleQueryId,
    _field: &'static str,
) -> Result<ProtocolVec3, OracleAdapterError> {
    if !value.x.is_finite()
        || !value.y.is_finite()
        || !value.z.is_finite()
        || !f32_finite(value.x)
        || !f32_finite(value.y)
        || !f32_finite(value.z)
    {
        return Err(invalid_intent(id, "position is not finite f32"));
    }
    Ok(ProtocolVec3 {
        x: value.x as f32,
        y: value.y as f32,
        z: value.z as f32,
    })
}

fn ensure_segment(
    start: ProtocolVec3,
    end: ProtocolVec3,
    id: OracleQueryId,
    _field: &'static str,
) -> Result<(), OracleAdapterError> {
    let dx = f64::from(end.x - start.x);
    let dy = f64::from(end.y - start.y);
    let dz = f64::from(end.z - start.z);
    if dx * dx + dy * dy + dz * dz <= 1.0e-12 {
        return Err(invalid_intent(id, "native segment is degenerate"));
    }
    Ok(())
}

fn validate_intent(intent: &OracleQueryIntent) -> Result<(), OracleAdapterError> {
    let id = intent_id(intent);
    match intent {
        OracleQueryIntent::Ground(query) => {
            validate_bot_vec(query.position, id)?;
            finite_range(query.yaw, -PI * 4.0, PI * 4.0, id, "ground yaw")?;
            finite_range(query.half_length, 0.1, 20.0, id, "ground half length")?;
            finite_range(query.half_width, 0.1, 20.0, id, "ground half width")?;
        }
        OracleQueryIntent::Motion(query) => {
            validate_bot_vec(query.position, id)?;
            finite_range(query.travel_yaw, -PI * 4.0, PI * 4.0, id, "travel yaw")?;
            finite_range(query.speed, -100.0, 100.0, id, "speed")?;
            finite_range(query.throttle, -1.0, 1.0, id, "throttle")?;
            finite_range(query.turn, -1.0, 1.0, id, "turn")?;
            if query.dt_us == 0 || query.dt_us > 1_000_000 {
                return Err(invalid_intent(id, "motion dt_us is outside 1..=1000000"));
            }
            finite_range(query.half_length, 0.1, 20.0, id, "motion half length")?;
            finite_range(query.half_width, 0.1, 20.0, id, "motion half width")?;
        }
        OracleQueryIntent::Visibility(query) => {
            validate_target(query.target_id, id)?;
            validate_bot_vec(query.source_position, id)?;
            validate_bot_vec(query.target_position, id)?;
        }
        OracleQueryIntent::Ballistic(query) => {
            validate_target(query.target_id, id)?;
            validate_bot_vec(query.source_position, id)?;
            validate_bot_vec(query.target_position, id)?;
            validate_bot_vec(query.target_velocity, id)?;
            finite_range(query.shell_speed, 1.0, 10_000.0, id, "shell speed")?;
            finite_range(query.gravity.abs(), 0.01, 1_000.0, id, "gravity")?;
            finite_range(query.max_distance, 1.0, 100_000.0, id, "max distance")?;
            finite_range(
                query.pitch_limits.0,
                -PI / 2.0,
                PI / 2.0,
                id,
                "minimum pitch",
            )?;
            finite_range(
                query.pitch_limits.1,
                -PI / 2.0,
                PI / 2.0,
                id,
                "maximum pitch",
            )?;
            if query.pitch_limits.0 > query.pitch_limits.1 {
                return Err(invalid_intent(id, "minimum pitch exceeds maximum pitch"));
            }
        }
        OracleQueryIntent::Lane(query) => {
            validate_target(query.target_id, id)?;
            validate_bot_vec(query.source_position, id)?;
            validate_bot_vec(query.target_position, id)?;
            finite_range(query.shot_yaw, -PI * 4.0, PI * 4.0, id, "shot yaw")?;
            finite_range(query.shot_pitch, -PI / 2.0, PI / 2.0, id, "shot pitch")?;
            finite_range(
                query.flight_time,
                0.000_001,
                MAX_FLIGHT_SECONDS,
                id,
                "flight time",
            )?;
            finite_range(query.shell_speed, 1.0, 10_000.0, id, "shell speed")?;
            finite_range(query.gravity.abs(), 0.01, 1_000.0, id, "gravity")?;
            finite_range(query.max_distance, 1.0, 100_000.0, id, "max distance")?;
            if query.fire_seq == 0 {
                return Err(invalid_intent(id, "fire sequence must be non-zero"));
            }
        }
    }
    Ok(())
}

fn validate_intent_id(
    id: OracleQueryId,
    expected_kind: OracleQueryKind,
    issued_tick: Tick,
    apply_tick: Tick,
) -> Result<(), OracleAdapterError> {
    if id.bot_id == 0 {
        return Err(invalid_intent(id, "bot id must be non-zero"));
    }
    if id.kind != expected_kind {
        return Err(invalid_intent(
            id,
            "query kind does not match intent variant",
        ));
    }
    if id.issued_tick != issued_tick {
        return Err(invalid_intent(id, "issued tick does not match batch tick"));
    }
    if id.apply_tick != apply_tick
        || id.issued_tick.checked_add(ORACLE_LATENCY_TICKS) != Some(id.apply_tick)
    {
        return Err(invalid_intent(id, "intent does not use the T+3 apply tick"));
    }
    Ok(())
}

fn validate_target(target_id: u32, id: OracleQueryId) -> Result<(), OracleAdapterError> {
    if target_id == 0 {
        return Err(invalid_intent(id, "target id must be non-zero"));
    }
    Ok(())
}

fn validate_bot_vec(value: BotVec3, id: OracleQueryId) -> Result<(), OracleAdapterError> {
    if !value.x.is_finite() || !value.y.is_finite() || !value.z.is_finite() {
        return Err(invalid_intent(id, "vector must be finite"));
    }
    Ok(())
}

fn finite_range(
    value: f64,
    minimum: f64,
    maximum: f64,
    id: OracleQueryId,
    reason: &'static str,
) -> Result<(), OracleAdapterError> {
    if !value.is_finite() || value < minimum || value > maximum {
        return Err(invalid_intent(id, reason));
    }
    Ok(())
}

fn validate_lineage(lineage: OracleLineage) -> Result<(), OracleAdapterError> {
    if lineage.round_id == 0 || lineage.authority_epoch == 0 || lineage.oracle_generation == 0 {
        return Err(OracleAdapterError::InvalidLineage);
    }
    Ok(())
}

fn validate_config(config: &OracleAdapterConfig) -> Result<(), OracleAdapterError> {
    if !config.trajectory_step_seconds.is_finite()
        || !(0.04..=0.25).contains(&config.trajectory_step_seconds)
    {
        return Err(OracleAdapterError::InvalidConfig {
            field: "trajectory_step_seconds",
        });
    }
    if config.muzzle_node.is_empty()
        || config.muzzle_node.len() > 128
        || config.muzzle_node.chars().any(char::is_control)
    {
        return Err(OracleAdapterError::InvalidConfig {
            field: "muzzle_node",
        });
    }
    if !config.muzzle_origin_tolerance.is_finite()
        || !(0.0..=5.0).contains(&config.muzzle_origin_tolerance)
    {
        return Err(OracleAdapterError::InvalidConfig {
            field: "muzzle_origin_tolerance",
        });
    }
    Ok(())
}

fn stable_key(id: OracleQueryId, kind: &str, part: &str) -> String {
    // Four rotating lanes keep T, T+1, T+2 and T+3 simultaneously pending.
    // Reusing a lane only at T+4 makes query_generation a true replacement
    // fence instead of invalidating an older result before its apply tick.
    let lane = id.issued_tick % QUERY_LANES;
    format!("bot/{}/{kind}/{part}/l{lane}", id.bot_id)
}

fn intent_id(intent: &OracleQueryIntent) -> OracleQueryId {
    match intent {
        OracleQueryIntent::Ground(query) => query.id,
        OracleQueryIntent::Motion(query) => query.id,
        OracleQueryIntent::Visibility(query) => query.id,
        OracleQueryIntent::Ballistic(query) => query.id,
        OracleQueryIntent::Lane(query) => query.id,
    }
}

fn intent_kind(intent: &OracleQueryIntent) -> OracleQueryKind {
    match intent {
        OracleQueryIntent::Ground(_) => OracleQueryKind::Ground,
        OracleQueryIntent::Motion(_) => OracleQueryKind::Motion,
        OracleQueryIntent::Visibility(_) => OracleQueryKind::Visibility,
        OracleQueryIntent::Ballistic(_) => OracleQueryKind::Ballistic,
        OracleQueryIntent::Lane(_) => OracleQueryKind::Lane,
    }
}

fn template_id(template: &IntentTemplate) -> OracleQueryId {
    match template {
        IntentTemplate::Ground { query, .. } => query.id,
        IntentTemplate::Motion(query) => query.id,
        IntentTemplate::Visibility { query, .. } => query.id,
        IntentTemplate::Ballistic { query, .. } => query.id,
        IntentTemplate::Lane { query, .. } => query.id,
    }
}

fn invalid_intent(id: OracleQueryId, reason: &'static str) -> OracleAdapterError {
    OracleAdapterError::InvalidIntent { id, reason }
}

fn f32_finite(value: f64) -> bool {
    (value as f32).is_finite()
}

fn bot_vec(value: ProtocolVec3) -> BotVec3 {
    BotVec3::new(f64::from(value.x), f64::from(value.y), f64::from(value.z))
}

fn distance(left: BotVec3, right: BotVec3) -> f64 {
    ((left.x - right.x).powi(2) + (left.y - right.y).powi(2) + (left.z - right.z).powi(2)).sqrt()
}

fn failed(id: OracleQueryId, reason: String) -> FailedOracleIntent {
    FailedOracleIntent { id, reason }
}

fn merge_slot<T>(
    current: &mut Option<T>,
    incoming: Option<T>,
    bot_id: u32,
    kind: &'static str,
) -> Result<(), OracleAdapterError> {
    if incoming.is_none() {
        return Ok(());
    }
    if current.is_some() {
        return Err(OracleAdapterError::DuplicateDecodedReceipt { bot_id, kind });
    }
    *current = incoming;
    Ok(())
}

fn insert_ground(
    decoded: &mut DecodedOracleBatch,
    receipt: GroundReceipt,
) -> Result<(), OracleAdapterError> {
    let bot_id = receipt.id.bot_id;
    merge_slot(
        &mut decoded.receipts.entry(bot_id).or_default().ground,
        Some(receipt),
        bot_id,
        "ground",
    )
}

fn insert_motion(
    decoded: &mut DecodedOracleBatch,
    receipt: MotionReceipt,
) -> Result<(), OracleAdapterError> {
    let bot_id = receipt.id.bot_id;
    merge_slot(
        &mut decoded.receipts.entry(bot_id).or_default().motion,
        Some(receipt),
        bot_id,
        "motion",
    )
}

fn insert_visibility(
    decoded: &mut DecodedOracleBatch,
    receipt: VisibilityReceipt,
) -> Result<(), OracleAdapterError> {
    let bot_id = receipt.id.bot_id;
    merge_slot(
        &mut decoded.receipts.entry(bot_id).or_default().visibility,
        Some(receipt),
        bot_id,
        "visibility",
    )
}

fn insert_ballistic(
    decoded: &mut DecodedOracleBatch,
    receipt: BallisticReceipt,
) -> Result<(), OracleAdapterError> {
    let bot_id = receipt.id.bot_id;
    merge_slot(
        &mut decoded.receipts.entry(bot_id).or_default().ballistic,
        Some(receipt),
        bot_id,
        "ballistic",
    )
}

fn insert_lane(
    decoded: &mut DecodedOracleBatch,
    receipt: LaneReceipt,
) -> Result<(), OracleAdapterError> {
    let bot_id = receipt.id.bot_id;
    merge_slot(
        &mut decoded.receipts.entry(bot_id).or_default().lane,
        Some(receipt),
        bot_id,
        "lane",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{
        OracleV1ResultStatus, TransformSample, VehicleHit, VehicleHitLayer, VehicleHitMaterial,
    };

    fn lineage() -> OracleLineage {
        OracleLineage {
            round_id: 7,
            authority_epoch: 2,
            oracle_generation: 3,
        }
    }

    fn id(bot_id: u32, tick: u64, kind: OracleQueryKind) -> OracleQueryId {
        OracleQueryId {
            bot_id,
            issued_tick: tick,
            apply_tick: tick + ORACLE_LATENCY_TICKS,
            kind,
        }
    }

    fn entities(bot_count: u32) -> OracleEntityMap {
        let mut entities = OracleEntityMap::default();
        for bot_id in 1..=bot_count {
            entities
                .insert_bot(
                    bot_id,
                    EntityRef {
                        entity_id: 1000 + i64::from(bot_id),
                        generation: 1,
                    },
                )
                .unwrap();
        }
        entities
            .insert_human(
                9,
                EntityRef {
                    entity_id: 2009,
                    generation: 4,
                },
            )
            .unwrap();
        entities
    }

    fn ground(bot_id: u32, tick: u64) -> OracleQueryIntent {
        OracleQueryIntent::Ground(GroundQuery {
            id: id(bot_id, tick, OracleQueryKind::Ground),
            position: BotVec3::new(bot_id as f64 * 2.0, 3.0, 10.0),
            yaw: 0.0,
            half_length: 3.5,
            half_width: 1.7,
            include_water_depth: true,
        })
    }

    fn five_intents(tick: u64) -> Vec<OracleQueryIntent> {
        let target = BotVec3::new(0.0, 3.0, 100.0);
        vec![
            ground(1, tick),
            OracleQueryIntent::Motion(MotionQuery {
                id: id(1, tick, OracleQueryKind::Motion),
                position: BotVec3::new(0.0, 3.0, 0.0),
                travel_yaw: 0.0,
                speed: 8.0,
                throttle: 1.0,
                turn: 0.0,
                dt_us: 33_333,
                half_length: 3.5,
                half_width: 1.7,
            }),
            OracleQueryIntent::Visibility(VisibilityQuery {
                id: id(1, tick, OracleQueryKind::Visibility),
                target_kind: TargetKind::Human,
                target_id: 9,
                source_position: BotVec3::new(0.0, 4.5, 0.0),
                target_position: target,
            }),
            OracleQueryIntent::Ballistic(BallisticQuery {
                id: id(1, tick, OracleQueryKind::Ballistic),
                target_kind: TargetKind::Human,
                target_id: 9,
                shell_index: 0,
                source_position: BotVec3::new(0.0, 4.5, 0.0),
                target_position: target,
                target_velocity: BotVec3::new(0.0, 0.0, 0.0),
                shell_speed: 700.0,
                gravity: 9.81,
                max_distance: 720.0,
                pitch_limits: (-0.35, 0.15),
                prefer_high_arc: false,
            }),
            OracleQueryIntent::Lane(LaneQuery {
                id: id(1, tick, OracleQueryKind::Lane),
                target_kind: TargetKind::Human,
                target_id: 9,
                fire_seq: 1,
                shell_index: 0,
                source_position: BotVec3::new(0.0, 4.5, 0.0),
                target_position: target,
                shot_yaw: 0.0,
                shot_pitch: 0.01,
                flight_time: 0.2,
                shell_speed: 700.0,
                gravity: 9.81,
                max_distance: 720.0,
            }),
        ]
    }

    fn sample(height: f32) -> SurfaceSample {
        SurfaceSample {
            height,
            normal: ProtocolVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            material_id: Some(1),
        }
    }

    fn successful_reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        OracleV1BatchReply {
            protocol_version: request.protocol_version,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: request.batch_seq,
            results: request
                .queries
                .iter()
                .map(|query| {
                    let outcome = match &query.operation {
                        OracleOperation::GroundSampleBatch { positions } => {
                            QueryOutcome::GroundSampleBatch {
                                samples: positions
                                    .iter()
                                    .enumerate()
                                    .map(|(index, _)| Some(sample(2.0 + index as f32 * 0.1)))
                                    .collect(),
                            }
                        }
                        OracleOperation::WaterSample { .. } => {
                            QueryOutcome::WaterSample { height: Some(3.0) }
                        }
                        OracleOperation::SegmentCast { .. } => {
                            QueryOutcome::SegmentCast { hit: None }
                        }
                        OracleOperation::SegmentCastBatch { segments } => {
                            QueryOutcome::SegmentCastBatch {
                                hits: vec![None; segments.len()],
                            }
                        }
                        OracleOperation::VehicleHitTest { start, end, .. } => {
                            let fraction = 0.95_f32;
                            let dx = f64::from(end.x) - f64::from(start.x);
                            let dy = f64::from(end.y) - f64::from(start.y);
                            let dz = f64::from(end.z) - f64::from(start.z);
                            let distance_m =
                                (dx * dx + dy * dy + dz * dz).sqrt() * f64::from(fraction);
                            QueryOutcome::VehicleHitTest {
                                hit: Some(VehicleHit {
                                    fraction,
                                    position: ProtocolVec3 {
                                        x: start.x + (end.x - start.x) * fraction,
                                        y: start.y + (end.y - start.y) * fraction,
                                        z: start.z + (end.z - start.z) * fraction,
                                    },
                                    normal: ProtocolVec3 {
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
                                        critical_target: None,
                                        chance_to_hit_by_projectile: None,
                                        chance_to_hit_by_explosion: None,
                                    }],
                                    internal_hits: None,
                                }),
                            }
                        }
                        OracleOperation::NodeTransform { .. } => QueryOutcome::NodeTransform {
                            transform: Some(TransformSample {
                                position: ProtocolVec3 {
                                    x: 0.0,
                                    y: 4.5,
                                    z: 0.0,
                                },
                                basis: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                            }),
                        },
                        other => panic!("unexpected test operation: {other:?}"),
                    };
                    OracleV1Result {
                        query_id: query.query_id,
                        key: query.key.clone(),
                        query_generation: query.query_generation,
                        entity: query.entity,
                        status: OracleV1ResultStatus::Ok { outcome },
                    }
                })
                .collect(),
        }
    }

    #[test]
    fn batches_carry_t_plus_three_lineage_and_monotonic_generations() {
        let mut adapter = OracleAdapter::new(lineage(), 11).unwrap();
        let entities = entities(1);

        let first = adapter
            .build_batches(10, &[ground(1, 10)], &entities)
            .unwrap();
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].lineage(), lineage());
        assert_eq!(first[0].issued_tick, 10);
        assert_eq!(first[0].apply_tick, 13);
        assert_eq!(first[0].world_revision, 11);
        assert!(first[0]
            .queries
            .iter()
            .all(|query| query.query_generation == 1));
        let first_keys: Vec<_> = first[0]
            .queries
            .iter()
            .map(|query| query.key.clone())
            .collect();

        let second = adapter
            .build_batches(11, &[ground(1, 11)], &entities)
            .unwrap();
        assert_eq!(second[0].batch_seq, first[0].batch_seq + 1);
        assert_eq!(second[0].apply_tick, 14);
        assert!(second[0]
            .queries
            .iter()
            .all(|query| query.query_generation == 1));
        let second_keys: Vec<_> = second[0]
            .queries
            .iter()
            .map(|query| query.key.clone())
            .collect();
        assert_ne!(second_keys, first_keys);

        let reused_lane = adapter
            .build_batches(14, &[ground(1, 14)], &entities)
            .unwrap();
        assert!(reused_lane[0]
            .queries
            .iter()
            .all(|query| query.query_generation == 2));
        let reused_keys: Vec<_> = reused_lane[0]
            .queries
            .iter()
            .map(|query| query.key.clone())
            .collect();
        assert_eq!(reused_keys, first_keys);
    }

    #[test]
    fn five_intents_round_trip_to_the_exact_bot_receipt_slots() {
        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let requests = adapter
            .build_batches(20, &five_intents(20), &entities(1))
            .unwrap();
        assert_eq!(requests.len(), 1);
        let reply = successful_reply(&requests[0]);
        let decoded = adapter.decode_reply(&requests[0], &reply).unwrap();
        assert!(decoded.failed.is_empty());
        let receipts = &decoded.receipts[&1];
        assert_eq!(receipts.ground.as_ref().unwrap().id.apply_tick, 23);
        assert_eq!(receipts.ground.as_ref().unwrap().water_depth, Some(1.0));
        assert_eq!(
            receipts.motion.as_ref().unwrap().status,
            MotionStatus::Clear
        );
        assert!(receipts.visibility.as_ref().unwrap().visible);
        assert!(receipts.ballistic.as_ref().unwrap().solution.is_some());
        assert!(receipts.lane.as_ref().unwrap().clear);
        assert_eq!(
            receipts.lane.as_ref().unwrap().origin,
            BotVec3::new(0.0, 4.5, 0.0)
        );
    }

    #[test]
    fn final_lane_is_self_contained_without_a_same_tick_ballistic_refresh() {
        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let lane = five_intents(20).pop().unwrap();
        let request = adapter
            .build_batches(20, &[lane], &entities(1))
            .unwrap()
            .remove(0);
        assert_eq!(request.queries.len(), 2);
        assert!(request
            .queries
            .iter()
            .any(|query| matches!(query.operation, OracleOperation::NodeTransform { .. })));
        assert!(request
            .queries
            .iter()
            .any(|query| matches!(query.operation, OracleOperation::SegmentCastBatch { .. })));

        let decoded = adapter
            .decode_reply(&request, &successful_reply(&request))
            .unwrap();
        assert!(decoded.failed.is_empty());
        assert!(decoded.receipts[&1].lane.as_ref().unwrap().clear);
    }

    #[test]
    fn missing_reply_is_rejected_and_consumed_without_any_receipt() {
        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let request = adapter
            .build_batches(20, &[ground(1, 20)], &entities(1))
            .unwrap()
            .remove(0);
        let mut reply = successful_reply(&request);
        reply.results.pop();

        assert!(matches!(
            adapter.decode_reply(&request, &reply),
            Err(OracleAdapterError::Protocol(
                OracleV1ValidationError::MissingResult { .. }
            ))
        ));
        assert_eq!(adapter.pending_batches(), 0);
    }

    #[test]
    fn unavailable_and_incomplete_results_become_negative_or_absent_receipts() {
        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let intents = vec![
            ground(1, 20),
            five_intents(20)
                .into_iter()
                .find(|intent| matches!(intent, OracleQueryIntent::Visibility(_)))
                .unwrap(),
        ];
        let request = adapter
            .build_batches(20, &intents, &entities(1))
            .unwrap()
            .remove(0);
        let mut reply = successful_reply(&request);
        for (query, result) in request.queries.iter().zip(reply.results.iter_mut()) {
            if query.key.contains("/ground/surface/") {
                result.status = OracleV1ResultStatus::Ok {
                    outcome: QueryOutcome::GroundSampleBatch {
                        samples: vec![Some(sample(2.0)), None, None, None, None],
                    },
                };
            }
            if query.key.contains("/visibility/world/") {
                result.status = OracleV1ResultStatus::Unavailable {
                    code: "budget_exhausted".to_owned(),
                    message: "native frame budget exhausted".to_owned(),
                };
            }
        }
        let decoded = adapter.decode_reply(&request, &reply).unwrap();
        let receipts = &decoded.receipts[&1];
        assert!(receipts.ground.is_none());
        assert_eq!(receipts.visibility.as_ref().unwrap().visible, false);
        assert_eq!(decoded.failed.len(), 2);
    }

    #[test]
    fn many_intents_split_without_crossing_wire_limits() {
        let mut adapter = OracleAdapter::new(lineage(), 1).unwrap();
        let entities = entities(40);
        let intents: Vec<_> = (1..=40).map(|bot_id| ground(bot_id, 8)).collect();
        let batches = adapter.build_batches(8, &intents, &entities).unwrap();

        assert_eq!(batches.len(), 2);
        assert_eq!(batches[0].batch_seq + 1, batches[1].batch_seq);
        assert!(batches.iter().all(|batch| {
            batch.queries.len() <= MAX_ORACLE_BATCH_QUERIES
                && batch
                    .queries
                    .iter()
                    .map(|query| query.operation.primitive_count())
                    .sum::<usize>()
                    <= MAX_ORACLE_PRIMITIVE_OPERATIONS
        }));
        assert_eq!(
            batches
                .iter()
                .map(|batch| batch.queries.len())
                .sum::<usize>(),
            80
        );
    }

    #[test]
    fn wrong_lineage_and_query_generation_fail_before_decode() {
        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let request = adapter
            .build_batches(20, &[ground(1, 20)], &entities(1))
            .unwrap()
            .remove(0);
        let mut reply = successful_reply(&request);
        reply.oracle_generation += 1;
        assert!(matches!(
            adapter.decode_reply(&request, &reply),
            Err(OracleAdapterError::Protocol(
                OracleV1ValidationError::ReplyHeaderMismatch { .. }
            ))
        ));

        let mut adapter = OracleAdapter::new(lineage(), 5).unwrap();
        let request = adapter
            .build_batches(21, &[ground(1, 21)], &entities(1))
            .unwrap()
            .remove(0);
        let mut reply = successful_reply(&request);
        reply.results[0].query_generation += 1;
        assert!(matches!(
            adapter.decode_reply(&request, &reply),
            Err(OracleAdapterError::Protocol(
                OracleV1ValidationError::ResultFenceMismatch { .. }
            ))
        ));
    }
}
