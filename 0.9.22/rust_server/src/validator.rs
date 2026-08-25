use crate::protocol::{
    BatchId, BatchKey, BatchReply, BatchRequest, DestructibleHullCandidate,
    DestructibleHullEvidence, DestructibleHullEvidenceQuery, DestructibleKind,
    DestructibleShotEvidence, DestructibleShotEvidenceQuery, EntityGeneration, EntityId, EntityRef,
    ExplosionEvidence, ExplosionEvidenceQuery, ExplosionHitLayer, ExplosionTargetPose,
    OracleMessage, OracleOperation, OracleOperationKind, OracleQuery, OracleV1BatchKey,
    OracleV1BatchReply, OracleV1BatchRequest, OracleV1Query, OracleV1ResultStatus, QueryId,
    QueryOutcome, RamContactArmorEvidence, RamContactArmorEvidenceQuery, RamContactPose,
    SimulationScope, Tick, Vec3, VehicleHit, DESTRUCTIBLE_AMBIGUITY_EPSILON_M,
    DESTRUCTIBLE_AP_PIERCING_LOSS_MM, DESTRUCTIBLE_AP_THROUGH_MAX_HP, DESTRUCTIBLE_POINT_EPSILON_M,
    EXPLOSION_DIRECTION_TOLERANCE, MAX_DESTRUCTIBLE_CANDIDATES, MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
    MAX_DESTRUCTIBLE_HULL_CANDIDATES, MAX_DESTRUCTIBLE_ITEM_SCALE, MAX_DESTRUCTIBLE_SEGMENT_M,
    MAX_DESTRUCTIBLE_SKIPPED, MAX_DESTRUCTIBLE_WORLD_COORDINATE_M, MAX_EXPLOSION_CALIBER_MM,
    MAX_EXPLOSION_POSE_ANGLE_RAD, MAX_EXPLOSION_RAY_DISTANCE_M, MAX_EXPLOSION_WORLD_COORDINATE_M,
    MAX_FOLIAGE_CAMOUFLAGE_BONUS, MAX_ORACLE_BATCH_QUERIES, MAX_ORACLE_ERROR_CODE_BYTES,
    MAX_ORACLE_ERROR_MESSAGE_BYTES, MAX_ORACLE_LINE_BYTES, MAX_ORACLE_PRIMITIVE_OPERATIONS,
    MAX_ORACLE_QUERY_KEY_BYTES, MAX_RAM_CONTACT_COORDINATE_M, MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    MAX_RAM_CONTACT_POSE_DISTANCE_M, MAX_VEHICLE_DAMAGE_FACTOR, MAX_VEHICLE_HIT_ARMOR_MM,
    MAX_VEHICLE_HIT_DISTANCE_M, MAX_VEHICLE_HIT_LAYERS, MAX_VEHICLE_HIT_TEXT_BYTES,
    MAX_VEHICLE_INTERNAL_HITS, ORACLE_PIPELINE_TICKS, ORACLE_PROTOCOL_VERSION,
    RAM_CONTACT_NORMAL_TOLERANCE,
};
use std::collections::{BTreeSet, HashMap, HashSet};
use thiserror::Error;

#[derive(Clone, Debug)]
struct PendingBatch {
    queries: HashMap<QueryId, OracleQuery>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ValidationOutcome {
    RequestAccepted {
        key: BatchKey,
        scope_changed: bool,
        abandoned_pending: usize,
        invalidated_pending: usize,
    },
    ReplyAccepted {
        key: BatchKey,
    },
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ValidationError {
    #[error("unsupported oracle protocol version {received}; expected {expected}")]
    UnsupportedProtocolVersion { expected: u16, received: u16 },
    #[error("oracle request batch {key:?} contains no queries")]
    EmptyBatch { key: BatchKey },
    #[error("request scope {received:?} is older than active scope {active:?}")]
    StaleRequestScope {
        active: SimulationScope,
        received: SimulationScope,
    },
    #[error("reply scope {received:?} is older than active scope {active:?}")]
    StaleReplyScope {
        active: SimulationScope,
        received: SimulationScope,
    },
    #[error("reply scope {received:?} is newer than active scope {active:?}")]
    FutureReplyScope {
        active: SimulationScope,
        received: SimulationScope,
    },
    #[error("reply {key:?} arrived before any request established an active scope")]
    ReplyWithoutActiveScope { key: BatchKey },
    #[error("request tick {received} regressed behind active tick {last}")]
    RegressedTick { last: Tick, received: Tick },
    #[error("batch {key:?} was already submitted")]
    DuplicateBatch { key: BatchKey },
    #[error("batch id {batch_id} was already used at tick {prior_tick} in {scope:?}")]
    ReusedBatchId {
        scope: SimulationScope,
        batch_id: BatchId,
        prior_tick: Tick,
    },
    #[error("batch {key:?} contains duplicate query id {query_id}")]
    DuplicateQueryId { key: BatchKey, query_id: QueryId },
    #[error(
        "batch {key:?} uses entity {entity_id} with conflicting generations {first} and {second}"
    )]
    ConflictingEntityGeneration {
        key: BatchKey,
        entity_id: EntityId,
        first: EntityGeneration,
        second: EntityGeneration,
    },
    #[error("entity {entity_id} generation {received} is stale; current generation is {current}")]
    StaleEntityGeneration {
        entity_id: EntityId,
        current: EntityGeneration,
        received: EntityGeneration,
    },
    #[error("reply for batch {key:?} was already accepted")]
    DuplicateReply { key: BatchKey },
    #[error("reply for batch {key:?} belongs to entity work invalidated by a newer generation")]
    InvalidatedBatchReply { key: BatchKey },
    #[error("reply references unknown pending batch {key:?}")]
    UnknownBatch { key: BatchKey },
    #[error("reply for batch {key:?} contains duplicate result for query {query_id}")]
    DuplicateResult { key: BatchKey, query_id: QueryId },
    #[error("reply for batch {key:?} contains unknown query {query_id}")]
    UnexpectedResult { key: BatchKey, query_id: QueryId },
    #[error("reply for batch {key:?} is missing query {query_id}")]
    MissingResult { key: BatchKey, query_id: QueryId },
    #[error(
        "reply for batch {key:?}, query {query_id} changed entity from {expected:?} to {received:?}"
    )]
    EntityReferenceMismatch {
        key: BatchKey,
        query_id: QueryId,
        expected: EntityRef,
        received: EntityRef,
    },
    #[error("reply for batch {key:?}, query {query_id} returned {received:?} for {expected:?}")]
    OutcomeKindMismatch {
        key: BatchKey,
        query_id: QueryId,
        expected: OracleOperationKind,
        received: OracleOperationKind,
    },
}

/// Stateful validation for batched native-oracle traffic.
///
/// Requests are atomic batches. Advancing a round or epoch abandons all older
/// pending work. Advancing an entity generation invalidates any pending batch
/// that still refers to the prior generation.
#[derive(Default)]
pub struct OracleValidator {
    active_scope: Option<SimulationScope>,
    last_tick: Option<Tick>,
    pending: HashMap<BatchKey, PendingBatch>,
    completed: HashSet<BatchKey>,
    invalidated: HashSet<BatchKey>,
    batch_ids: HashMap<(SimulationScope, BatchId), Tick>,
    entity_generations: HashMap<EntityId, EntityGeneration>,
}

impl OracleValidator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn active_scope(&self) -> Option<SimulationScope> {
        self.active_scope
    }

    pub fn last_tick(&self) -> Option<Tick> {
        self.last_tick
    }

    pub fn pending_batches(&self) -> usize {
        self.pending.len()
    }

    pub fn process(
        &mut self,
        message: OracleMessage,
    ) -> Result<ValidationOutcome, ValidationError> {
        match message {
            OracleMessage::BatchRequest(request) => self.accept_request(request),
            OracleMessage::BatchReply(reply) => self.accept_reply(reply),
        }
    }

    pub fn accept_request(
        &mut self,
        request: BatchRequest,
    ) -> Result<ValidationOutcome, ValidationError> {
        check_protocol_version(request.protocol_version)?;
        let key = request.key();
        let scope = request.scope();
        let generations = validate_request_shape(&request)?;

        let scope_changed = self.active_scope != Some(scope);
        if let Some(active) = self.active_scope {
            if scope < active {
                return Err(ValidationError::StaleRequestScope {
                    active,
                    received: scope,
                });
            }
            if scope == active {
                if let Some(last) = self.last_tick {
                    if request.tick < last {
                        return Err(ValidationError::RegressedTick {
                            last,
                            received: request.tick,
                        });
                    }
                }
                if self.pending.contains_key(&key) || self.completed.contains(&key) {
                    return Err(ValidationError::DuplicateBatch { key });
                }
                if let Some(prior_tick) = self.batch_ids.get(&(scope, request.batch_id)) {
                    return Err(ValidationError::ReusedBatchId {
                        scope,
                        batch_id: request.batch_id,
                        prior_tick: *prior_tick,
                    });
                }
                for (&entity_id, &generation) in &generations {
                    if let Some(&current) = self.entity_generations.get(&entity_id) {
                        if generation < current {
                            return Err(ValidationError::StaleEntityGeneration {
                                entity_id,
                                current,
                                received: generation,
                            });
                        }
                    }
                }
            }
        }

        let abandoned_pending = if scope_changed {
            let abandoned = self.pending.len();
            self.reset_for_scope(scope);
            abandoned
        } else {
            0
        };

        let invalidated_pending = self.advance_entity_generations(&generations);
        let queries = request
            .queries
            .into_iter()
            .map(|query| (query.query_id, query))
            .collect();
        self.pending.insert(key, PendingBatch { queries });
        self.batch_ids
            .insert((scope, request.batch_id), request.tick);
        self.last_tick = Some(
            self.last_tick
                .map_or(request.tick, |last| last.max(request.tick)),
        );

        Ok(ValidationOutcome::RequestAccepted {
            key,
            scope_changed,
            abandoned_pending,
            invalidated_pending,
        })
    }

    pub fn accept_reply(
        &mut self,
        reply: BatchReply,
    ) -> Result<ValidationOutcome, ValidationError> {
        check_protocol_version(reply.protocol_version)?;
        let key = reply.key();
        let scope = reply.scope();
        let active = self
            .active_scope
            .ok_or(ValidationError::ReplyWithoutActiveScope { key })?;
        if scope < active {
            return Err(ValidationError::StaleReplyScope {
                active,
                received: scope,
            });
        }
        if scope > active {
            return Err(ValidationError::FutureReplyScope {
                active,
                received: scope,
            });
        }
        if self.completed.contains(&key) {
            return Err(ValidationError::DuplicateReply { key });
        }
        if self.invalidated.contains(&key) {
            return Err(ValidationError::InvalidatedBatchReply { key });
        }
        let pending = self
            .pending
            .get(&key)
            .ok_or(ValidationError::UnknownBatch { key })?;

        let mut seen = HashSet::with_capacity(reply.results.len());
        for result in &reply.results {
            if !seen.insert(result.query_id) {
                return Err(ValidationError::DuplicateResult {
                    key,
                    query_id: result.query_id,
                });
            }
            let expected =
                pending
                    .queries
                    .get(&result.query_id)
                    .ok_or(ValidationError::UnexpectedResult {
                        key,
                        query_id: result.query_id,
                    })?;
            if result.entity != expected.entity {
                return Err(ValidationError::EntityReferenceMismatch {
                    key,
                    query_id: result.query_id,
                    expected: expected.entity,
                    received: result.entity,
                });
            }
            if let Some(received) = result.outcome.kind() {
                let expected_kind = expected.operation.kind();
                if received != expected_kind {
                    return Err(ValidationError::OutcomeKindMismatch {
                        key,
                        query_id: result.query_id,
                        expected: expected_kind,
                        received,
                    });
                }
            }
        }
        if let Some(missing) = pending
            .queries
            .keys()
            .copied()
            .find(|query_id| !seen.contains(query_id))
        {
            return Err(ValidationError::MissingResult {
                key,
                query_id: missing,
            });
        }

        self.pending.remove(&key);
        self.completed.insert(key);
        Ok(ValidationOutcome::ReplyAccepted { key })
    }

    fn reset_for_scope(&mut self, scope: SimulationScope) {
        self.active_scope = Some(scope);
        self.last_tick = None;
        self.pending.clear();
        self.completed.clear();
        self.invalidated.clear();
        self.batch_ids.clear();
        self.entity_generations.clear();
    }

    fn advance_entity_generations(
        &mut self,
        generations: &HashMap<EntityId, EntityGeneration>,
    ) -> usize {
        let advanced: HashMap<_, _> = generations
            .iter()
            .filter_map(|(&entity_id, &generation)| {
                let current = self.entity_generations.get(&entity_id).copied();
                if current.is_none() || current.is_some_and(|value| generation > value) {
                    Some((entity_id, generation))
                } else {
                    None
                }
            })
            .collect();
        if advanced.is_empty() {
            return 0;
        }

        let stale_keys: Vec<_> = self
            .pending
            .iter()
            .filter_map(|(&key, batch)| {
                batch
                    .queries
                    .values()
                    .any(|query| {
                        advanced
                            .get(&query.entity.entity_id)
                            .is_some_and(|generation| query.entity.generation < *generation)
                    })
                    .then_some(key)
            })
            .collect();
        for key in &stale_keys {
            self.pending.remove(key);
            self.invalidated.insert(*key);
        }
        self.entity_generations.extend(advanced);
        stale_keys.len()
    }
}

fn check_protocol_version(received: u16) -> Result<(), ValidationError> {
    if received != ORACLE_PROTOCOL_VERSION {
        return Err(ValidationError::UnsupportedProtocolVersion {
            expected: ORACLE_PROTOCOL_VERSION,
            received,
        });
    }
    Ok(())
}

fn validate_request_shape(
    request: &BatchRequest,
) -> Result<HashMap<EntityId, EntityGeneration>, ValidationError> {
    let key = request.key();
    if request.queries.is_empty() {
        return Err(ValidationError::EmptyBatch { key });
    }
    let mut query_ids = HashSet::with_capacity(request.queries.len());
    let mut generations = HashMap::new();
    for query in &request.queries {
        if !query_ids.insert(query.query_id) {
            return Err(ValidationError::DuplicateQueryId {
                key,
                query_id: query.query_id,
            });
        }
        if let Some(&first) = generations.get(&query.entity.entity_id) {
            if first != query.entity.generation {
                return Err(ValidationError::ConflictingEntityGeneration {
                    key,
                    entity_id: query.entity.entity_id,
                    first,
                    second: query.entity.generation,
                });
            }
        } else {
            generations.insert(query.entity.entity_id, query.entity.generation);
        }
    }
    Ok(generations)
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum OracleV1ValidationError {
    #[error("unsupported oracle-v1 protocol version {received}; expected {expected}")]
    UnsupportedProtocolVersion { expected: u16, received: u16 },
    #[error("oracle-v1 field {field} must be non-zero")]
    ZeroIdentifier { field: &'static str },
    #[error(
        "oracle-v1 batch {key:?} must apply {pipeline_ticks} ticks after {issued_tick}, not at {apply_tick}"
    )]
    InvalidPipelineWindow {
        key: OracleV1BatchKey,
        issued_tick: Tick,
        apply_tick: Tick,
        pipeline_ticks: Tick,
    },
    #[error("oracle-v1 batch {key:?} contains no queries")]
    EmptyBatch { key: OracleV1BatchKey },
    #[error("oracle-v1 batch {key:?} has {received} queries; maximum is {maximum}")]
    TooManyQueries {
        key: OracleV1BatchKey,
        received: usize,
        maximum: usize,
    },
    #[error("oracle-v1 batch {key:?} has {received} primitive operations; maximum is {maximum}")]
    TooManyPrimitiveOperations {
        key: OracleV1BatchKey,
        received: usize,
        maximum: usize,
    },
    #[error("oracle-v1 batch {key:?} encodes to {received} bytes; maximum is {maximum}")]
    EncodedBatchTooLarge {
        key: OracleV1BatchKey,
        received: usize,
        maximum: usize,
    },
    #[error("oracle-v1 batch {key:?} could not be encoded after validation")]
    EncodingFailure { key: OracleV1BatchKey },
    #[error("oracle-v1 batch {key:?} contains duplicate query id {query_id}")]
    DuplicateQueryId {
        key: OracleV1BatchKey,
        query_id: QueryId,
    },
    #[error("oracle-v1 batch {key:?} contains duplicate stable query key {query_key:?}")]
    DuplicateQueryKey {
        key: OracleV1BatchKey,
        query_key: String,
    },
    #[error("oracle-v1 {field} is empty, too long, or contains a control character")]
    InvalidText { field: &'static str },
    #[error("oracle-v1 numeric field {field} is not finite")]
    NonFiniteNumber { field: String },
    #[error("oracle-v1 segment {field} has zero length")]
    DegenerateSegment { field: String },
    #[error("oracle-v1 query {query_id} contains an empty primitive batch")]
    EmptyPrimitiveBatch { query_id: QueryId },
    #[error("oracle-v1 fraction {field} is outside [0, 1]")]
    InvalidFraction { field: String },
    #[error("oracle-v1 vehicle hit for query {query_id} has invalid field {field}")]
    InvalidVehicleHit { query_id: QueryId, field: String },
    #[error(
        "oracle-v1 vehicle hit for query {query_id} has {received} layers; maximum is {maximum}"
    )]
    TooManyVehicleHitLayers {
        query_id: QueryId,
        received: usize,
        maximum: usize,
    },
    #[error(
        "oracle-v1 vehicle hit for query {query_id} has {received} internal hits; maximum is {maximum}"
    )]
    TooManyVehicleInternalHits {
        query_id: QueryId,
        received: usize,
        maximum: usize,
    },
    #[error("oracle-v1 query {query_id} vehicle target does not match its entity fence")]
    VehicleTargetMismatch { query_id: QueryId },
    #[error("oracle-v1 explosion query {query_id} has invalid field {field}")]
    InvalidExplosionQuery { query_id: QueryId, field: String },
    #[error("oracle-v1 explosion evidence for query {query_id} has invalid field {field}")]
    InvalidExplosionEvidence { query_id: QueryId, field: String },
    #[error("oracle-v1 query {query_id} observation target does not match its entity fence")]
    ObservationTargetMismatch { query_id: QueryId },
    #[error("oracle-v1 query {query_id} observer and target use the same native entity")]
    ObservationEntityAlias { query_id: QueryId },
    #[error("oracle-v1 ram contact query {query_id} has invalid field {field}")]
    InvalidRamContactQuery {
        query_id: QueryId,
        field: &'static str,
    },
    #[error("oracle-v1 ram contact evidence for query {query_id} has invalid field {field}")]
    InvalidRamContactEvidence {
        query_id: QueryId,
        field: &'static str,
    },
    #[error("oracle-v1 spotting evidence for query {query_id} has invalid field {field}")]
    InvalidSpottingEvidence {
        query_id: QueryId,
        field: &'static str,
    },
    #[error("oracle-v1 destructible query {query_id} has invalid field {field}")]
    InvalidDestructibleQuery { query_id: QueryId, field: String },
    #[error("oracle-v1 destructible evidence for query {query_id} has invalid field {field}")]
    InvalidDestructibleEvidence { query_id: QueryId, field: String },
    #[error(
        "oracle-v1 destructible evidence for query {query_id} has {received} candidates; maximum is {maximum}"
    )]
    TooManyDestructibleCandidates {
        query_id: QueryId,
        received: usize,
        maximum: usize,
    },
    #[error("oracle-v1 reply query {query_id} returned {received} values; expected {expected}")]
    ResultCardinalityMismatch {
        query_id: QueryId,
        expected: usize,
        received: usize,
    },
    #[error("oracle-v1 reply header {field} does not echo request {key:?}")]
    ReplyHeaderMismatch {
        key: OracleV1BatchKey,
        field: &'static str,
    },
    #[error("oracle-v1 reply for {key:?} contains duplicate result {query_id}")]
    DuplicateResult {
        key: OracleV1BatchKey,
        query_id: QueryId,
    },
    #[error("oracle-v1 reply for {key:?} contains unexpected result {query_id}")]
    UnexpectedResult {
        key: OracleV1BatchKey,
        query_id: QueryId,
    },
    #[error("oracle-v1 reply for {key:?} is missing result {query_id}")]
    MissingResult {
        key: OracleV1BatchKey,
        query_id: QueryId,
    },
    #[error("oracle-v1 reply for {key:?}, query {query_id} changed {field}")]
    ResultFenceMismatch {
        key: OracleV1BatchKey,
        query_id: QueryId,
        field: &'static str,
    },
    #[error(
        "oracle-v1 reply for {key:?}, query {query_id} returned {received:?} for {expected:?}"
    )]
    OutcomeKindMismatch {
        key: OracleV1BatchKey,
        query_id: QueryId,
        expected: OracleOperationKind,
        received: OracleOperationKind,
    },
    #[error("oracle-v1 reply for {key:?}, query {query_id} used the legacy error outcome")]
    LegacyErrorOutcome {
        key: OracleV1BatchKey,
        query_id: QueryId,
    },
}

pub fn validate_oracle_v1_request(
    request: &OracleV1BatchRequest,
) -> Result<(), OracleV1ValidationError> {
    validate_v1_protocol(request.protocol_version)?;
    let key = request.key();
    require_non_zero(request.round_id, "round_id")?;
    require_non_zero(request.oracle_generation, "oracle_generation")?;
    require_non_zero(request.batch_seq, "batch_seq")?;
    let expected_apply = request
        .issued_tick
        .checked_add(ORACLE_PIPELINE_TICKS)
        .ok_or(OracleV1ValidationError::InvalidPipelineWindow {
            key,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            pipeline_ticks: ORACLE_PIPELINE_TICKS,
        })?;
    if request.apply_tick != expected_apply {
        return Err(OracleV1ValidationError::InvalidPipelineWindow {
            key,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            pipeline_ticks: ORACLE_PIPELINE_TICKS,
        });
    }
    if request.queries.is_empty() {
        return Err(OracleV1ValidationError::EmptyBatch { key });
    }
    if request.queries.len() > MAX_ORACLE_BATCH_QUERIES {
        return Err(OracleV1ValidationError::TooManyQueries {
            key,
            received: request.queries.len(),
            maximum: MAX_ORACLE_BATCH_QUERIES,
        });
    }

    let mut query_ids = HashSet::with_capacity(request.queries.len());
    let mut query_keys = HashSet::with_capacity(request.queries.len());
    let mut primitive_operations = 0usize;
    for query in &request.queries {
        if !query_ids.insert(query.query_id) {
            return Err(OracleV1ValidationError::DuplicateQueryId {
                key,
                query_id: query.query_id,
            });
        }
        if !query_keys.insert(query.key.as_str()) {
            return Err(OracleV1ValidationError::DuplicateQueryKey {
                key,
                query_key: query.key.clone(),
            });
        }
        validate_v1_query(query)?;
        primitive_operations =
            primitive_operations.saturating_add(query.operation.primitive_count());
    }
    if primitive_operations > MAX_ORACLE_PRIMITIVE_OPERATIONS {
        return Err(OracleV1ValidationError::TooManyPrimitiveOperations {
            key,
            received: primitive_operations,
            maximum: MAX_ORACLE_PRIMITIVE_OPERATIONS,
        });
    }
    validate_encoded_size(request, key)
}

pub fn validate_oracle_v1_reply(
    reply: &OracleV1BatchReply,
    request: &OracleV1BatchRequest,
) -> Result<(), OracleV1ValidationError> {
    validate_v1_protocol(reply.protocol_version)?;
    let key = request.key();
    if reply.key() != key {
        return Err(OracleV1ValidationError::ReplyHeaderMismatch {
            key,
            field: "lineage_or_batch_seq",
        });
    }
    if reply.issued_tick != request.issued_tick {
        return Err(OracleV1ValidationError::ReplyHeaderMismatch {
            key,
            field: "issued_tick",
        });
    }
    if reply.apply_tick != request.apply_tick {
        return Err(OracleV1ValidationError::ReplyHeaderMismatch {
            key,
            field: "apply_tick",
        });
    }
    if reply.world_revision != request.world_revision {
        return Err(OracleV1ValidationError::ReplyHeaderMismatch {
            key,
            field: "world_revision",
        });
    }
    require_non_zero(reply.oracle_frame_seq, "oracle_frame_seq")?;
    if reply.results.len() > MAX_ORACLE_BATCH_QUERIES {
        return Err(OracleV1ValidationError::TooManyQueries {
            key,
            received: reply.results.len(),
            maximum: MAX_ORACLE_BATCH_QUERIES,
        });
    }

    let expected: HashMap<_, _> = request
        .queries
        .iter()
        .map(|query| (query.query_id, query))
        .collect();
    let mut seen = HashSet::with_capacity(reply.results.len());
    for result in &reply.results {
        if !seen.insert(result.query_id) {
            return Err(OracleV1ValidationError::DuplicateResult {
                key,
                query_id: result.query_id,
            });
        }
        let query =
            expected
                .get(&result.query_id)
                .ok_or(OracleV1ValidationError::UnexpectedResult {
                    key,
                    query_id: result.query_id,
                })?;
        if result.key != query.key {
            return Err(OracleV1ValidationError::ResultFenceMismatch {
                key,
                query_id: result.query_id,
                field: "key",
            });
        }
        if result.query_generation != query.query_generation {
            return Err(OracleV1ValidationError::ResultFenceMismatch {
                key,
                query_id: result.query_id,
                field: "query_generation",
            });
        }
        if result.entity != query.entity {
            return Err(OracleV1ValidationError::ResultFenceMismatch {
                key,
                query_id: result.query_id,
                field: "entity",
            });
        }
        validate_v1_result_status(&result.status, key, query)?;
    }
    if let Some(missing) = request
        .queries
        .iter()
        .map(|query| query.query_id)
        .find(|query_id| !seen.contains(query_id))
    {
        return Err(OracleV1ValidationError::MissingResult {
            key,
            query_id: missing,
        });
    }
    validate_encoded_size(reply, key)
}

fn validate_v1_protocol(received: u16) -> Result<(), OracleV1ValidationError> {
    if received != ORACLE_PROTOCOL_VERSION {
        return Err(OracleV1ValidationError::UnsupportedProtocolVersion {
            expected: ORACLE_PROTOCOL_VERSION,
            received,
        });
    }
    Ok(())
}

fn require_non_zero(value: u64, field: &'static str) -> Result<(), OracleV1ValidationError> {
    if value == 0 {
        return Err(OracleV1ValidationError::ZeroIdentifier { field });
    }
    Ok(())
}

fn validate_text(
    value: &str,
    maximum: usize,
    field: &'static str,
) -> Result<(), OracleV1ValidationError> {
    if value.is_empty() || value.len() > maximum || value.chars().any(char::is_control) {
        return Err(OracleV1ValidationError::InvalidText { field });
    }
    Ok(())
}

fn validate_v1_query(query: &OracleV1Query) -> Result<(), OracleV1ValidationError> {
    require_non_zero(query.query_id, "query_id")?;
    require_non_zero(query.query_generation, "query_generation")?;
    if query.entity.entity_id <= 0 {
        return Err(OracleV1ValidationError::ZeroIdentifier { field: "entity_id" });
    }
    require_non_zero(query.entity.generation, "entity_generation")?;
    validate_text(&query.key, MAX_ORACLE_QUERY_KEY_BYTES, "query_key")?;
    match &query.operation {
        OracleOperation::GroundSample { position } => {
            validate_vec3(position, &format!("query[{}].position", query.query_id))?;
        }
        OracleOperation::SegmentCast { start, end, .. } => {
            validate_segment(start, end, &format!("query[{}].segment", query.query_id))?;
        }
        OracleOperation::SegmentCastBatch { segments } => {
            if segments.is_empty() {
                return Err(OracleV1ValidationError::EmptyPrimitiveBatch {
                    query_id: query.query_id,
                });
            }
            for (index, segment) in segments.iter().enumerate() {
                validate_segment(
                    &segment.start,
                    &segment.end,
                    &format!("query[{}].segments[{index}]", query.query_id),
                )?;
            }
        }
        OracleOperation::GroundSampleBatch { positions }
        | OracleOperation::WaterSampleBatch { positions } => {
            if positions.is_empty() {
                return Err(OracleV1ValidationError::EmptyPrimitiveBatch {
                    query_id: query.query_id,
                });
            }
            for (index, position) in positions.iter().enumerate() {
                validate_vec3(
                    position,
                    &format!("query[{}].positions[{index}]", query.query_id),
                )?;
            }
        }
        OracleOperation::WaterSample { position } => {
            validate_vec3(position, &format!("query[{}].position", query.query_id))?;
        }
        OracleOperation::VehicleHitTest { start, end, target } => {
            validate_segment(
                start,
                end,
                &format!("query[{}].vehicle_trace", query.query_id),
            )?;
            if target != &query.entity {
                return Err(OracleV1ValidationError::VehicleTargetMismatch {
                    query_id: query.query_id,
                });
            }
        }
        OracleOperation::ExplosionEvidence(arguments) => {
            validate_explosion_evidence_query(query, arguments)?;
        }
        OracleOperation::NodeTransform { node } => {
            validate_text(node, MAX_ORACLE_QUERY_KEY_BYTES, "node")?;
        }
        OracleOperation::PlayerMuzzleEvidence(_) => {}
        OracleOperation::SpottingEvidence(arguments) => {
            validate_observation_pair(
                query,
                arguments.observer,
                arguments.target,
                &arguments.observer_position,
                &arguments.target_position,
            )?;
        }
        OracleOperation::FiringLaneEvidence(arguments) => {
            validate_observation_pair(
                query,
                arguments.observer,
                arguments.target,
                &arguments.observer_position,
                &arguments.target_position,
            )?;
        }
        OracleOperation::RamContactArmorEvidence(arguments) => {
            validate_ram_contact_query(query, arguments)?;
        }
        OracleOperation::DestructibleShotEvidence(arguments) => {
            validate_destructible_shot_query(query.query_id, arguments)?;
        }
        OracleOperation::DestructibleHullEvidence(arguments) => {
            validate_destructible_hull_query(query.query_id, arguments)?;
        }
    }
    Ok(())
}

fn validate_explosion_evidence_query(
    query: &OracleV1Query,
    arguments: &ExplosionEvidenceQuery,
) -> Result<(), OracleV1ValidationError> {
    let query_id = query.query_id;
    if arguments.target != query.entity {
        return Err(invalid_explosion_query(query_id, "target_entity_fence"));
    }
    validate_explosion_arguments(arguments, query_id)
}

fn validate_explosion_arguments(
    arguments: &ExplosionEvidenceQuery,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    validate_explosion_pose(&arguments.target_pose, query_id)?;
    validate_explosion_vec3(&arguments.impact, query_id, "impact")?;
    validate_explosion_vec3(
        &arguments.incoming_direction,
        query_id,
        "incoming_direction",
    )?;
    let direction_length = vec3_length(&arguments.incoming_direction);
    if (direction_length - 1.0).abs() > EXPLOSION_DIRECTION_TOLERANCE {
        return Err(invalid_explosion_query(query_id, "incoming_direction_unit"));
    }
    if !bounded_number(
        arguments.caliber_mm,
        f64::MIN_POSITIVE,
        MAX_EXPLOSION_CALIBER_MM,
    ) {
        return Err(invalid_explosion_query(query_id, "caliber_mm"));
    }
    let ray_end = explosion_ray_end(&arguments.target_pose.position, query_id)?;
    let ray_distance = vec3_distance(&arguments.impact, &ray_end);
    if !bounded_number(
        ray_distance,
        f64::MIN_POSITIVE,
        MAX_EXPLOSION_RAY_DISTANCE_M,
    ) {
        return Err(invalid_explosion_query(query_id, "vehicle_ray_distance"));
    }
    Ok(())
}

fn validate_explosion_pose(
    pose: &ExplosionTargetPose,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    validate_explosion_vec3(&pose.position, query_id, "target_pose.position")?;
    for (field, value) in [
        ("target_pose.yaw", pose.yaw),
        ("target_pose.pitch", pose.pitch),
        ("target_pose.roll", pose.roll),
        ("target_pose.turret_yaw", pose.turret_yaw),
        ("target_pose.gun_pitch", pose.gun_pitch),
    ] {
        if !bounded_number(
            value,
            -MAX_EXPLOSION_POSE_ANGLE_RAD,
            MAX_EXPLOSION_POSE_ANGLE_RAD,
        ) {
            return Err(invalid_explosion_query(query_id, field));
        }
    }
    if pose.siege_state > 3 {
        return Err(invalid_explosion_query(query_id, "target_pose.siege_state"));
    }
    Ok(())
}

fn validate_explosion_vec3(
    value: &Vec3,
    query_id: QueryId,
    field: impl Into<String>,
) -> Result<(), OracleV1ValidationError> {
    if destructible_vec3_is_bounded(value, MAX_EXPLOSION_WORLD_COORDINATE_M) {
        Ok(())
    } else {
        Err(invalid_explosion_query(query_id, field))
    }
}

fn explosion_ray_end(
    target_position: &Vec3,
    query_id: QueryId,
) -> Result<Vec3, OracleV1ValidationError> {
    let ray_end = Vec3 {
        x: target_position.x,
        y: target_position.y + 1.0,
        z: target_position.z,
    };
    validate_explosion_vec3(&ray_end, query_id, "target_pose.ray_end")?;
    Ok(ray_end)
}

fn invalid_explosion_query(query_id: QueryId, field: impl Into<String>) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidExplosionQuery {
        query_id,
        field: field.into(),
    }
}

fn validate_ram_contact_query(
    query: &OracleV1Query,
    arguments: &RamContactArmorEvidenceQuery,
) -> Result<(), OracleV1ValidationError> {
    let query_id = query.query_id;
    if arguments.first != query.entity {
        return Err(invalid_ram_contact_query(query_id, "first_entity_fence"));
    }
    if arguments.second == arguments.first {
        return Err(invalid_ram_contact_query(query_id, "entity_alias"));
    }
    if arguments.second.entity_id <= 0 || arguments.second.generation == 0 {
        return Err(invalid_ram_contact_query(query_id, "second_entity_fence"));
    }

    validate_ram_contact_pose(&arguments.first_pose, query_id, "first_pose")?;
    validate_ram_contact_pose(&arguments.second_pose, query_id, "second_pose")?;
    validate_ram_contact_vec3(&arguments.contact_point, query_id, "contact_point")?;
    validate_ram_contact_vec3(&arguments.contact_normal, query_id, "contact_normal")?;

    let normal = &arguments.contact_normal;
    let normal_length_squared = f64::from(normal.x) * f64::from(normal.x)
        + f64::from(normal.y) * f64::from(normal.y)
        + f64::from(normal.z) * f64::from(normal.z);
    if (normal_length_squared.sqrt() - 1.0).abs() > RAM_CONTACT_NORMAL_TOLERANCE {
        return Err(invalid_ram_contact_query(query_id, "contact_normal_unit"));
    }
    if vec3_distance(&arguments.first_pose.position, &arguments.contact_point)
        > MAX_RAM_CONTACT_POSE_DISTANCE_M
    {
        return Err(invalid_ram_contact_query(
            query_id,
            "first_contact_distance",
        ));
    }
    if vec3_distance(&arguments.second_pose.position, &arguments.contact_point)
        > MAX_RAM_CONTACT_POSE_DISTANCE_M
    {
        return Err(invalid_ram_contact_query(
            query_id,
            "second_contact_distance",
        ));
    }
    Ok(())
}

fn validate_ram_contact_pose(
    pose: &RamContactPose,
    query_id: QueryId,
    field: &'static str,
) -> Result<(), OracleV1ValidationError> {
    validate_ram_contact_vec3(&pose.position, query_id, field)?;
    if !bounded_number(
        pose.yaw,
        -MAX_RAM_CONTACT_POSE_ANGLE_RAD,
        MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    ) || !bounded_number(
        pose.pitch,
        -MAX_RAM_CONTACT_POSE_ANGLE_RAD,
        MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    ) || !bounded_number(
        pose.roll,
        -MAX_RAM_CONTACT_POSE_ANGLE_RAD,
        MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    ) || !bounded_number(
        pose.turret_yaw,
        -MAX_RAM_CONTACT_POSE_ANGLE_RAD,
        MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    ) || !bounded_number(
        pose.gun_pitch,
        -MAX_RAM_CONTACT_POSE_ANGLE_RAD,
        MAX_RAM_CONTACT_POSE_ANGLE_RAD,
    ) {
        return Err(invalid_ram_contact_query(query_id, "pose_orientation"));
    }
    if pose.siege_state > 3 {
        return Err(invalid_ram_contact_query(query_id, "pose_siege_state"));
    }
    Ok(())
}

fn validate_ram_contact_vec3(
    value: &Vec3,
    query_id: QueryId,
    field: &'static str,
) -> Result<(), OracleV1ValidationError> {
    if destructible_vec3_is_bounded(value, MAX_RAM_CONTACT_COORDINATE_M) {
        Ok(())
    } else {
        Err(invalid_ram_contact_query(query_id, field))
    }
}

fn invalid_ram_contact_query(query_id: QueryId, field: &'static str) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidRamContactQuery { query_id, field }
}

fn validate_observation_pair(
    query: &OracleV1Query,
    observer: EntityRef,
    target: EntityRef,
    observer_position: &Vec3,
    target_position: &Vec3,
) -> Result<(), OracleV1ValidationError> {
    if target != query.entity {
        return Err(OracleV1ValidationError::ObservationTargetMismatch {
            query_id: query.query_id,
        });
    }
    if observer == target {
        return Err(OracleV1ValidationError::ObservationEntityAlias {
            query_id: query.query_id,
        });
    }
    if observer.entity_id <= 0 {
        return Err(OracleV1ValidationError::ZeroIdentifier {
            field: "observer_entity_id",
        });
    }
    require_non_zero(observer.generation, "observer_entity_generation")?;
    validate_vec3(
        observer_position,
        &format!("query[{}].observer_position", query.query_id),
    )?;
    validate_vec3(
        target_position,
        &format!("query[{}].target_position", query.query_id),
    )?;
    Ok(())
}

fn validate_destructible_shot_query(
    query_id: QueryId,
    arguments: &DestructibleShotEvidenceQuery,
) -> Result<(), OracleV1ValidationError> {
    if arguments.space_id <= 0 {
        return Err(invalid_destructible_query(query_id, "space_id"));
    }
    validate_destructible_query_vec3(&arguments.start, query_id, "start")?;
    validate_destructible_query_vec3(&arguments.end, query_id, "end")?;
    let length = vec3_distance(&arguments.start, &arguments.end);
    if !bounded_number(length, 1.0e-9, MAX_DESTRUCTIBLE_SEGMENT_M) {
        return Err(invalid_destructible_query(query_id, "segment"));
    }
    Ok(())
}

fn validate_destructible_hull_query(
    query_id: QueryId,
    arguments: &DestructibleHullEvidenceQuery,
) -> Result<(), OracleV1ValidationError> {
    if arguments.space_id <= 0 {
        return Err(invalid_destructible_query(query_id, "space_id"));
    }
    validate_destructible_query_vec3(&arguments.position, query_id, "position")?;
    if !bounded_number(
        arguments.yaw,
        -2.0 * std::f64::consts::PI,
        2.0 * std::f64::consts::PI,
    ) {
        return Err(invalid_destructible_query(query_id, "yaw"));
    }
    if !bounded_number(
        arguments.frame_travel,
        -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
        MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
    ) {
        return Err(invalid_destructible_query(query_id, "frame_travel"));
    }
    Ok(())
}

fn validate_destructible_query_vec3(
    value: &Vec3,
    query_id: QueryId,
    field: &str,
) -> Result<(), OracleV1ValidationError> {
    if destructible_vec3_is_bounded(value, MAX_DESTRUCTIBLE_WORLD_COORDINATE_M) {
        Ok(())
    } else {
        Err(invalid_destructible_query(query_id, field))
    }
}

fn invalid_destructible_query(
    query_id: QueryId,
    field: impl Into<String>,
) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidDestructibleQuery {
        query_id,
        field: field.into(),
    }
}

fn validate_v1_result_status(
    status: &OracleV1ResultStatus,
    key: OracleV1BatchKey,
    query: &OracleV1Query,
) -> Result<(), OracleV1ValidationError> {
    match status {
        OracleV1ResultStatus::Unavailable { code, message } => {
            validate_text(code, MAX_ORACLE_ERROR_CODE_BYTES, "unavailable.code")?;
            validate_text(
                message,
                MAX_ORACLE_ERROR_MESSAGE_BYTES,
                "unavailable.message",
            )?;
        }
        OracleV1ResultStatus::Ok { outcome } => {
            if matches!(outcome, QueryOutcome::Error { .. }) {
                return Err(OracleV1ValidationError::LegacyErrorOutcome {
                    key,
                    query_id: query.query_id,
                });
            }
            if let Some(received) = outcome.kind() {
                let expected = query.operation.kind();
                if received != expected {
                    return Err(OracleV1ValidationError::OutcomeKindMismatch {
                        key,
                        query_id: query.query_id,
                        expected,
                        received,
                    });
                }
            }
            validate_query_outcome(outcome, query)?;
        }
    }
    Ok(())
}

fn validate_query_outcome(
    outcome: &QueryOutcome,
    query: &OracleV1Query,
) -> Result<(), OracleV1ValidationError> {
    let query_id = query.query_id;
    match outcome {
        QueryOutcome::GroundSample { sample } => {
            if let Some(sample) = sample {
                validate_surface_sample(sample, query_id, "sample")?;
            }
        }
        QueryOutcome::SegmentCast { hit } => {
            if let Some(hit) = hit {
                validate_ray_hit(hit, query_id, "hit")?;
            }
        }
        QueryOutcome::SegmentCastBatch { hits } => {
            let expected = match &query.operation {
                OracleOperation::SegmentCastBatch { segments } => segments.len(),
                _ => 0,
            };
            require_cardinality(query_id, expected, hits.len())?;
            for (index, hit) in hits.iter().enumerate() {
                if let Some(hit) = hit {
                    validate_ray_hit(hit, query_id, &format!("hits[{index}]"))?;
                }
            }
        }
        QueryOutcome::WaterSample { height } => {
            if let Some(height) = height {
                validate_finite(*height, format!("result[{query_id}].height"))?;
            }
        }
        QueryOutcome::GroundSampleBatch { samples } => {
            let expected = match &query.operation {
                OracleOperation::GroundSampleBatch { positions } => positions.len(),
                _ => 0,
            };
            require_cardinality(query_id, expected, samples.len())?;
            for (index, sample) in samples.iter().enumerate() {
                if let Some(sample) = sample {
                    validate_surface_sample(sample, query_id, &format!("samples[{index}]"))?;
                }
            }
        }
        QueryOutcome::WaterSampleBatch { heights } => {
            let expected = match &query.operation {
                OracleOperation::WaterSampleBatch { positions } => positions.len(),
                _ => 0,
            };
            require_cardinality(query_id, expected, heights.len())?;
            for (index, height) in heights.iter().enumerate() {
                if let Some(height) = height {
                    validate_finite(*height, format!("result[{query_id}].heights[{index}]"))?;
                }
            }
        }
        QueryOutcome::VehicleHitTest { hit } => {
            if let Some(hit) = hit {
                let (start, end) = match &query.operation {
                    OracleOperation::VehicleHitTest { start, end, .. } => (start, end),
                    _ => unreachable!("outcome kind was checked before value validation"),
                };
                validate_vehicle_hit_receipt(hit, start, end, query_id)?;
            }
        }
        QueryOutcome::ExplosionEvidence(evidence) => {
            let OracleOperation::ExplosionEvidence(arguments) = &query.operation else {
                unreachable!("outcome kind was checked before value validation")
            };
            validate_explosion_evidence_receipt(arguments, evidence, query_id)?;
        }
        QueryOutcome::NodeTransform { transform } => {
            if let Some(transform) = transform {
                validate_vec3(
                    &transform.position,
                    &format!("result[{query_id}].transform.position"),
                )?;
                for (index, value) in transform.basis.iter().enumerate() {
                    validate_finite(
                        *value,
                        format!("result[{query_id}].transform.basis[{index}]"),
                    )?;
                }
            }
        }
        QueryOutcome::PlayerMuzzleEvidence(evidence) => {
            validate_vec3(
                &evidence.transform.position,
                &format!("result[{query_id}].transform.position"),
            )?;
            for (index, value) in evidence.transform.basis.iter().enumerate() {
                validate_finite(
                    *value,
                    format!("result[{query_id}].transform.basis[{index}]"),
                )?;
            }
        }
        QueryOutcome::SpottingEvidence(evidence) => {
            if !evidence.foliage_bonus.is_finite() {
                return Err(OracleV1ValidationError::NonFiniteNumber {
                    field: format!("result[{query_id}].foliage_bonus"),
                });
            }
            if !(0.0..=MAX_FOLIAGE_CAMOUFLAGE_BONUS).contains(&evidence.foliage_bonus) {
                return Err(OracleV1ValidationError::InvalidSpottingEvidence {
                    query_id,
                    field: "foliage_bonus",
                });
            }
            let OracleOperation::SpottingEvidence(arguments) = &query.operation else {
                unreachable!("outcome kind was checked before value validation")
            };
            if evidence.evaluated_for_recent_fire != arguments.evaluated_for_recent_fire {
                return Err(OracleV1ValidationError::InvalidSpottingEvidence {
                    query_id,
                    field: "evaluated_for_recent_fire",
                });
            }
            if !evidence.line_of_sight && evidence.foliage_bonus != 0.0 {
                return Err(OracleV1ValidationError::InvalidSpottingEvidence {
                    query_id,
                    field: "occluded_foliage_bonus",
                });
            }
        }
        QueryOutcome::FiringLaneEvidence(..) => {}
        QueryOutcome::RamContactArmorEvidence(evidence) => {
            validate_ram_contact_evidence(evidence, query_id)?;
        }
        QueryOutcome::DestructibleShotEvidence(evidence) => {
            let OracleOperation::DestructibleShotEvidence(arguments) = &query.operation else {
                unreachable!("outcome kind was checked before value validation")
            };
            validate_destructible_shot_evidence(evidence, arguments, query_id)?;
        }
        QueryOutcome::DestructibleHullEvidence(evidence) => {
            let OracleOperation::DestructibleHullEvidence(arguments) = &query.operation else {
                unreachable!("outcome kind was checked before value validation")
            };
            validate_destructible_hull_evidence(evidence, arguments, query_id)?;
        }
        QueryOutcome::Error { .. } => {}
    }
    Ok(())
}

fn validate_ram_contact_evidence(
    evidence: &RamContactArmorEvidence,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    if !bounded_number(
        evidence.first_armor_mm,
        f64::MIN_POSITIVE,
        MAX_VEHICLE_HIT_ARMOR_MM,
    ) {
        return Err(OracleV1ValidationError::InvalidRamContactEvidence {
            query_id,
            field: "first_armor_mm",
        });
    }
    if !bounded_number(
        evidence.second_armor_mm,
        f64::MIN_POSITIVE,
        MAX_VEHICLE_HIT_ARMOR_MM,
    ) {
        return Err(OracleV1ValidationError::InvalidRamContactEvidence {
            query_id,
            field: "second_armor_mm",
        });
    }
    Ok(())
}

fn validate_destructible_shot_evidence(
    evidence: &DestructibleShotEvidence,
    arguments: &DestructibleShotEvidenceQuery,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    if evidence.candidates.len() > MAX_DESTRUCTIBLE_CANDIDATES {
        return Err(OracleV1ValidationError::TooManyDestructibleCandidates {
            query_id,
            received: evidence.candidates.len(),
            maximum: MAX_DESTRUCTIBLE_CANDIDATES,
        });
    }
    if evidence.destroyed_skipped > MAX_DESTRUCTIBLE_SKIPPED {
        return Err(invalid_destructible_evidence(query_id, "destroyed_skipped"));
    }

    let segment_length = vec3_distance(&arguments.start, &arguments.end);
    let mut seen = BTreeSet::new();
    let mut previous_entry = None;
    for (index, candidate) in evidence.candidates.iter().enumerate() {
        validate_destructible_candidate_identity(
            candidate.chunk_id,
            candidate.item_index,
            candidate.mat_kind,
            candidate.kind,
            query_id,
            &format!("candidates[{index}].identity"),
        )?;
        let identity = (candidate.chunk_id, candidate.item_index, candidate.mat_kind);
        if !seen.insert(identity) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].duplicate_identity"),
            ));
        }
        if !bounded_number(candidate.entry_distance, 0.0, segment_length) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].entry_distance"),
            ));
        }
        if !bounded_number(
            candidate.exit_distance,
            candidate.entry_distance,
            segment_length,
        ) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].exit_distance"),
            ));
        }
        if previous_entry.is_some_and(|entry: f64| {
            candidate.entry_distance < entry
                || (candidate.entry_distance - entry).abs() <= DESTRUCTIBLE_AMBIGUITY_EPSILON_M
        }) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].entry_order"),
            ));
        }
        previous_entry = Some(candidate.entry_distance);
        validate_destructible_evidence_vec3(
            &candidate.impact_position,
            MAX_DESTRUCTIBLE_WORLD_COORDINATE_M,
            query_id,
            &format!("candidates[{index}].impact_position"),
        )?;
        let fraction = candidate.entry_distance / segment_length;
        let impact_error = [
            (
                f64::from(candidate.impact_position.x),
                f64::from(arguments.start.x)
                    + (f64::from(arguments.end.x) - f64::from(arguments.start.x)) * fraction,
            ),
            (
                f64::from(candidate.impact_position.y),
                f64::from(arguments.start.y)
                    + (f64::from(arguments.end.y) - f64::from(arguments.start.y)) * fraction,
            ),
            (
                f64::from(candidate.impact_position.z),
                f64::from(arguments.start.z)
                    + (f64::from(arguments.end.z) - f64::from(arguments.start.z)) * fraction,
            ),
        ]
        .into_iter()
        .map(|(actual, expected)| (actual - expected).powi(2))
        .sum::<f64>()
        .sqrt();
        if impact_error > DESTRUCTIBLE_POINT_EPSILON_M {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].impact_position"),
            ));
        }
        if !bounded_number(candidate.item_scale, 1.0e-9, MAX_DESTRUCTIBLE_ITEM_SCALE) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].item_scale"),
            ));
        }
        if !bounded_number(candidate.scaled_health, 0.0, MAX_VEHICLE_DAMAGE_FACTOR) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].scaled_health"),
            ));
        }
        let expected_ap_through = arguments.shell_kind.is_ap()
            && candidate.scaled_health <= DESTRUCTIBLE_AP_THROUGH_MAX_HP;
        let expected_loss = if expected_ap_through {
            DESTRUCTIBLE_AP_PIERCING_LOSS_MM
        } else {
            0.0
        };
        if candidate.ap_through != expected_ap_through
            || !bounded_number(candidate.piercing_loss, 0.0, MAX_VEHICLE_HIT_ARMOR_MM)
            || (candidate.piercing_loss - expected_loss).abs() > 1.0e-6
        {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].penetration_verdict"),
            ));
        }
    }

    if let Some(collision) = evidence.static_collision {
        if !bounded_number(collision.distance, 0.0, segment_length) {
            return Err(invalid_destructible_evidence(
                query_id,
                "static_collision.distance",
            ));
        }
        validate_destructible_evidence_vec3(
            &collision.position,
            MAX_DESTRUCTIBLE_WORLD_COORDINATE_M,
            query_id,
            "static_collision.position",
        )?;
        if (vec3_distance(&arguments.start, &collision.position) - collision.distance).abs()
            > DESTRUCTIBLE_POINT_EPSILON_M
        {
            return Err(invalid_destructible_evidence(
                query_id,
                "static_collision.position",
            ));
        }
        if let Some(normal) = collision.normal {
            validate_destructible_evidence_vec3(&normal, 1.0, query_id, "static_collision.normal")?;
            let normal_length_squared = f64::from(normal.x) * f64::from(normal.x)
                + f64::from(normal.y) * f64::from(normal.y)
                + f64::from(normal.z) * f64::from(normal.z);
            if normal_length_squared <= 1.0e-18 {
                return Err(invalid_destructible_evidence(
                    query_id,
                    "static_collision.normal",
                ));
            }
        }
    }
    Ok(())
}

fn validate_destructible_hull_evidence(
    evidence: &DestructibleHullEvidence,
    arguments: &DestructibleHullEvidenceQuery,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    if evidence.candidates.len() > MAX_DESTRUCTIBLE_HULL_CANDIDATES {
        return Err(OracleV1ValidationError::TooManyDestructibleCandidates {
            query_id,
            received: evidence.candidates.len(),
            maximum: MAX_DESTRUCTIBLE_HULL_CANDIDATES,
        });
    }
    if !bounded_number(
        evidence.frame_travel,
        -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
        MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
    ) || (evidence.frame_travel - arguments.frame_travel).abs() > 1.0e-6
    {
        return Err(invalid_destructible_evidence(query_id, "frame_travel"));
    }

    let mut seen = BTreeSet::new();
    let mut previous_key = None;
    for (index, candidate) in evidence.candidates.iter().enumerate() {
        let key = validate_destructible_candidate_identity(
            candidate.chunk_id,
            candidate.item_index,
            candidate.mat_kind,
            candidate.kind,
            query_id,
            &format!("candidates[{index}].identity"),
        )?;
        if !seen.insert(key) || previous_key.is_some_and(|previous| key < previous) {
            return Err(invalid_destructible_evidence(
                query_id,
                format!("candidates[{index}].identity_order"),
            ));
        }
        previous_key = Some(key);
        validate_destructible_hull_candidate(candidate, arguments, query_id, index)?;
    }
    Ok(())
}

fn validate_destructible_hull_candidate(
    candidate: &DestructibleHullCandidate,
    _arguments: &DestructibleHullEvidenceQuery,
    query_id: QueryId,
    index: usize,
) -> Result<(), OracleV1ValidationError> {
    let field = |name: &str| format!("candidates[{index}].{name}");
    validate_destructible_evidence_vec3(
        &candidate.obb_center,
        MAX_DESTRUCTIBLE_WORLD_COORDINATE_M,
        query_id,
        &field("obb_center"),
    )?;
    Ok(())
}

fn validate_destructible_candidate_identity(
    chunk_id: i64,
    item_index: i64,
    mat_kind: Option<i64>,
    kind: DestructibleKind,
    query_id: QueryId,
    field: &str,
) -> Result<(i64, i64, i64), OracleV1ValidationError> {
    let valid_material = mat_kind.map_or(true, |value| (71..=130).contains(&value));
    if chunk_id < 0
        || item_index < 0
        || !valid_material
        || (kind == DestructibleKind::Structure) != mat_kind.is_some()
    {
        return Err(invalid_destructible_evidence(query_id, field));
    }
    Ok((chunk_id, item_index, mat_kind.unwrap_or(-1)))
}

fn validate_destructible_evidence_vec3(
    value: &Vec3,
    maximum: f64,
    query_id: QueryId,
    field: &str,
) -> Result<(), OracleV1ValidationError> {
    if destructible_vec3_is_bounded(value, maximum) {
        Ok(())
    } else {
        Err(invalid_destructible_evidence(query_id, field))
    }
}

fn destructible_vec3_is_bounded(value: &Vec3, maximum: f64) -> bool {
    [value.x, value.y, value.z]
        .into_iter()
        .all(|component| bounded_number(f64::from(component), -maximum, maximum))
}

fn vec3_distance(first: &Vec3, second: &Vec3) -> f64 {
    let dx = f64::from(second.x) - f64::from(first.x);
    let dy = f64::from(second.y) - f64::from(first.y);
    let dz = f64::from(second.z) - f64::from(first.z);
    (dx * dx + dy * dy + dz * dz).sqrt()
}

fn vec3_length(value: &Vec3) -> f64 {
    let x = f64::from(value.x);
    let y = f64::from(value.y);
    let z = f64::from(value.z);
    (x * x + y * y + z * z).sqrt()
}

fn invalid_destructible_evidence(
    query_id: QueryId,
    field: impl Into<String>,
) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidDestructibleEvidence {
        query_id,
        field: field.into(),
    }
}

fn require_cardinality(
    query_id: QueryId,
    expected: usize,
    received: usize,
) -> Result<(), OracleV1ValidationError> {
    if expected != received {
        return Err(OracleV1ValidationError::ResultCardinalityMismatch {
            query_id,
            expected,
            received,
        });
    }
    Ok(())
}

fn validate_surface_sample(
    sample: &crate::protocol::SurfaceSample,
    query_id: QueryId,
    field: &str,
) -> Result<(), OracleV1ValidationError> {
    validate_finite(sample.height, format!("result[{query_id}].{field}.height"))?;
    validate_vec3(
        &sample.normal,
        &format!("result[{query_id}].{field}.normal"),
    )
}

fn validate_ray_hit(
    hit: &crate::protocol::RayHit,
    query_id: QueryId,
    field: &str,
) -> Result<(), OracleV1ValidationError> {
    validate_fraction(hit.fraction, format!("result[{query_id}].{field}.fraction"))?;
    validate_vec3(
        &hit.position,
        &format!("result[{query_id}].{field}.position"),
    )?;
    validate_vec3(&hit.normal, &format!("result[{query_id}].{field}.normal"))?;
    if let Some(entity) = hit.hit_entity {
        if entity.entity_id <= 0 {
            return Err(OracleV1ValidationError::ZeroIdentifier {
                field: "hit_entity.entity_id",
            });
        }
        require_non_zero(entity.generation, "hit_entity.generation")?;
    }
    Ok(())
}

pub(crate) fn validate_explosion_evidence_receipt(
    query: &ExplosionEvidenceQuery,
    evidence: &ExplosionEvidence,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    validate_explosion_arguments(query, query_id)?;
    if evidence.target_pose != query.target_pose {
        return Err(invalid_explosion_evidence(query_id, "target_pose_echo"));
    }
    let ray_end = explosion_ray_end(&query.target_pose.position, query_id)?;
    let ray_length = vec3_distance(&query.impact, &ray_end);
    let mut external_targets = BTreeSet::new();

    if let Some(vehicle_ray) = &evidence.vehicle_ray {
        if vehicle_ray.layers.is_empty() {
            return Err(invalid_explosion_evidence(query_id, "vehicle_ray.layers"));
        }
        if vehicle_ray.layers.len() > MAX_VEHICLE_HIT_LAYERS {
            return Err(OracleV1ValidationError::TooManyVehicleHitLayers {
                query_id,
                received: vehicle_ray.layers.len(),
                maximum: MAX_VEHICLE_HIT_LAYERS,
            });
        }
        let mut prior_distance = None;
        for (index, layer) in vehicle_ray.layers.iter().enumerate() {
            validate_explosion_layer(layer, index, ray_length, query_id, &mut external_targets)?;
            if prior_distance.is_some_and(|distance| layer.distance_m < distance) {
                return Err(invalid_explosion_evidence(
                    query_id,
                    format!("vehicle_ray.layers[{index}].distance_m_order"),
                ));
            }
            prior_distance = Some(layer.distance_m);
        }
    }

    if let Some(internal_hits) = &evidence.internal_hits {
        if internal_hits.len() > MAX_VEHICLE_INTERNAL_HITS {
            return Err(OracleV1ValidationError::TooManyVehicleInternalHits {
                query_id,
                received: internal_hits.len(),
                maximum: MAX_VEHICLE_INTERNAL_HITS,
            });
        }
        let cone_depth = query.caliber_mm / 100.0;
        let mut prior_distance = None;
        let mut targets = BTreeSet::new();
        for (index, hit) in internal_hits.iter().enumerate() {
            let field = |name: &str| format!("internal_hits[{index}].{name}");
            if !hit.distance_m.is_finite()
                || hit.distance_m < 0.0
                || hit.distance_m > cone_depth + vehicle_position_tolerance(cone_depth)
            {
                return Err(invalid_explosion_evidence(query_id, field("distance_m")));
            }
            if prior_distance.is_some_and(|distance| hit.distance_m < distance) {
                return Err(invalid_explosion_evidence(
                    query_id,
                    field("distance_m_order"),
                ));
            }
            prior_distance = Some(hit.distance_m);
            if external_targets.contains(&hit.target) {
                return Err(invalid_explosion_evidence(
                    query_id,
                    field("covered_target"),
                ));
            }
            if !targets.insert(hit.target) {
                return Err(invalid_explosion_evidence(
                    query_id,
                    field("duplicate_target"),
                ));
            }
        }
    }
    Ok(())
}

fn validate_explosion_layer(
    layer: &ExplosionHitLayer,
    index: usize,
    ray_length: f64,
    query_id: QueryId,
    external_targets: &mut BTreeSet<crate::protocol::VehicleCriticalTarget>,
) -> Result<(), OracleV1ValidationError> {
    let field = |name: &str| format!("vehicle_ray.layers[{index}].{name}");
    if !bounded_number(layer.distance_m, 0.0, MAX_VEHICLE_HIT_DISTANCE_M)
        || layer.distance_m > ray_length + vehicle_position_tolerance(ray_length)
    {
        return Err(invalid_explosion_evidence(query_id, field("distance_m")));
    }
    if !bounded_number(layer.hit_angle_cos, -1.0, 1.0) {
        return Err(invalid_explosion_evidence(query_id, field("hit_angle_cos")));
    }
    if let Some(component) = &layer.component {
        validate_text(
            component,
            MAX_VEHICLE_HIT_TEXT_BYTES,
            "explosion_evidence.layer.component",
        )?;
    }
    let material = &layer.material;
    if !bounded_number(material.armor_mm, 0.0, MAX_VEHICLE_HIT_ARMOR_MM) {
        return Err(invalid_explosion_evidence(
            query_id,
            field("material.armor_mm"),
        ));
    }
    if !bounded_number(
        material.vehicle_damage_factor,
        0.0,
        MAX_VEHICLE_DAMAGE_FACTOR,
    ) {
        return Err(invalid_explosion_evidence(
            query_id,
            field("material.vehicle_damage_factor"),
        ));
    }
    if material.native_identity == Some(0) {
        return Err(invalid_explosion_evidence(
            query_id,
            field("material.native_identity"),
        ));
    }
    if material.collide_once_only && material.kind.is_none() && material.native_identity.is_none() {
        return Err(invalid_explosion_evidence(
            query_id,
            field("material.collide_once_identity"),
        ));
    }
    match (layer.critical_target, layer.chance_to_hit_by_explosion) {
        (None, None) => {}
        (Some(target), Some(chance)) if bounded_number(chance, 0.0, 1.0) => {
            if !external_targets.insert(target) {
                return Err(invalid_explosion_evidence(
                    query_id,
                    field("duplicate_critical_target"),
                ));
            }
        }
        (Some(_), Some(_)) => {
            return Err(invalid_explosion_evidence(
                query_id,
                field("chance_to_hit_by_explosion"),
            ));
        }
        _ => {
            return Err(invalid_explosion_evidence(
                query_id,
                field("critical_target_chance"),
            ));
        }
    }
    Ok(())
}

fn invalid_explosion_evidence(
    query_id: QueryId,
    field: impl Into<String>,
) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidExplosionEvidence {
        query_id,
        field: field.into(),
    }
}

pub(crate) fn validate_vehicle_hit_receipt(
    hit: &VehicleHit,
    start: &Vec3,
    end: &Vec3,
    query_id: QueryId,
) -> Result<(), OracleV1ValidationError> {
    validate_fraction(hit.fraction, format!("result[{query_id}].fraction"))?;
    validate_vec3(&hit.position, &format!("result[{query_id}].position"))?;
    validate_vec3(&hit.normal, &format!("result[{query_id}].normal"))?;
    validate_text(
        &hit.hit_part,
        MAX_VEHICLE_HIT_TEXT_BYTES,
        "vehicle_hit.hit_part",
    )?;
    if hit.layers.is_empty() {
        return Err(invalid_vehicle_hit(query_id, "layers"));
    }
    if hit.layers.len() > MAX_VEHICLE_HIT_LAYERS {
        return Err(OracleV1ValidationError::TooManyVehicleHitLayers {
            query_id,
            received: hit.layers.len(),
            maximum: MAX_VEHICLE_HIT_LAYERS,
        });
    }

    let dx = f64::from(end.x) - f64::from(start.x);
    let dy = f64::from(end.y) - f64::from(start.y);
    let dz = f64::from(end.z) - f64::from(start.z);
    let segment_length = (dx * dx + dy * dy + dz * dz).sqrt();
    if !segment_length.is_finite() || segment_length <= f64::EPSILON {
        return Err(invalid_vehicle_hit(query_id, "segment"));
    }

    let mut prior_distance = None;
    let mut native_critical_targets = BTreeSet::new();
    for (index, layer) in hit.layers.iter().enumerate() {
        let field = |name: &str| format!("layers[{index}].{name}");
        if !bounded_number(layer.distance_m, 0.0, MAX_VEHICLE_HIT_DISTANCE_M)
            || layer.distance_m > segment_length + vehicle_position_tolerance(segment_length)
        {
            return Err(invalid_vehicle_hit(query_id, field("distance_m")));
        }
        if !bounded_number(layer.hit_angle_cos, -1.0, 1.0) {
            return Err(invalid_vehicle_hit(query_id, field("hit_angle_cos")));
        }
        if prior_distance.is_some_and(|distance| layer.distance_m < distance) {
            return Err(invalid_vehicle_hit(query_id, field("distance_m_order")));
        }
        prior_distance = Some(layer.distance_m);
        if let Some(component) = &layer.component {
            validate_text(
                component,
                MAX_VEHICLE_HIT_TEXT_BYTES,
                "vehicle_hit.layer.component",
            )?;
        }

        let material = &layer.material;
        if !bounded_number(material.armor_mm, 0.0, MAX_VEHICLE_HIT_ARMOR_MM) {
            return Err(invalid_vehicle_hit(query_id, field("material.armor_mm")));
        }
        if !bounded_number(
            material.vehicle_damage_factor,
            0.0,
            MAX_VEHICLE_DAMAGE_FACTOR,
        ) {
            return Err(invalid_vehicle_hit(
                query_id,
                field("material.vehicle_damage_factor"),
            ));
        }
        if material.native_identity == Some(0) {
            return Err(invalid_vehicle_hit(
                query_id,
                field("material.native_identity"),
            ));
        }
        if material.collide_once_only
            && material.kind.is_none()
            && material.native_identity.is_none()
        {
            return Err(invalid_vehicle_hit(
                query_id,
                field("material.collide_once_identity"),
            ));
        }
        match (
            layer.critical_target,
            layer.chance_to_hit_by_projectile,
            layer.chance_to_hit_by_explosion,
        ) {
            (None, None, None) => {}
            (Some(target), Some(projectile), Some(explosion))
                if bounded_number(projectile, 0.0, 1.0) && bounded_number(explosion, 0.0, 1.0) =>
            {
                native_critical_targets.insert(target);
            }
            (Some(_), Some(projectile), Some(_)) if !bounded_number(projectile, 0.0, 1.0) => {
                return Err(invalid_vehicle_hit(
                    query_id,
                    field("chance_to_hit_by_projectile"),
                ));
            }
            (Some(_), Some(_), Some(explosion)) if !bounded_number(explosion, 0.0, 1.0) => {
                return Err(invalid_vehicle_hit(
                    query_id,
                    field("chance_to_hit_by_explosion"),
                ));
            }
            _ => {
                return Err(invalid_vehicle_hit(
                    query_id,
                    field("critical_target_chances"),
                ));
            }
        }
    }

    if let Some(internal_hits) = &hit.internal_hits {
        if internal_hits.len() > MAX_VEHICLE_INTERNAL_HITS {
            return Err(OracleV1ValidationError::TooManyVehicleInternalHits {
                query_id,
                received: internal_hits.len(),
                maximum: MAX_VEHICLE_INTERNAL_HITS,
            });
        }
        let mut prior_internal_distance = None;
        let mut internal_targets = BTreeSet::new();
        for (index, internal) in internal_hits.iter().enumerate() {
            let field = |name: &str| format!("internal_hits[{index}].{name}");
            if !bounded_number(internal.distance_m, 0.0, MAX_VEHICLE_HIT_DISTANCE_M)
                || internal.distance_m > segment_length + vehicle_position_tolerance(segment_length)
            {
                return Err(invalid_vehicle_hit(query_id, field("distance_m")));
            }
            if prior_internal_distance.is_some_and(|distance| internal.distance_m < distance) {
                return Err(invalid_vehicle_hit(query_id, field("distance_m_order")));
            }
            prior_internal_distance = Some(internal.distance_m);
            if native_critical_targets.contains(&internal.target) {
                return Err(invalid_vehicle_hit(query_id, field("covered_target")));
            }
            if !internal_targets.insert(internal.target) {
                return Err(invalid_vehicle_hit(query_id, field("duplicate_target")));
            }
        }
    }

    let first = &hit.layers[0];
    if first.component.as_deref() != Some(hit.hit_part.as_str()) {
        return Err(invalid_vehicle_hit(query_id, "hit_part"));
    }
    let expected_fraction = first.distance_m / segment_length;
    if (f64::from(hit.fraction) - expected_fraction).abs() > 4.0 * f64::from(f32::EPSILON) {
        return Err(invalid_vehicle_hit(query_id, "fraction"));
    }
    let expected_position = [
        f64::from(start.x) + dx * expected_fraction,
        f64::from(start.y) + dy * expected_fraction,
        f64::from(start.z) + dz * expected_fraction,
    ];
    for (axis, (actual, expected)) in [hit.position.x, hit.position.y, hit.position.z]
        .into_iter()
        .zip(expected_position)
        .enumerate()
    {
        if (f64::from(actual) - expected).abs() > vehicle_position_tolerance(expected) {
            return Err(invalid_vehicle_hit(query_id, format!("position[{axis}]")));
        }
    }
    Ok(())
}

fn invalid_vehicle_hit(query_id: QueryId, field: impl Into<String>) -> OracleV1ValidationError {
    OracleV1ValidationError::InvalidVehicleHit {
        query_id,
        field: field.into(),
    }
}

fn bounded_number(value: f64, minimum: f64, maximum: f64) -> bool {
    value.is_finite() && (minimum..=maximum).contains(&value)
}

fn vehicle_position_tolerance(value: f64) -> f64 {
    0.001_f64.max(value.abs() * f64::from(f32::EPSILON) * 4.0)
}

fn validate_vec3(value: &Vec3, field: &str) -> Result<(), OracleV1ValidationError> {
    validate_finite(value.x, format!("{field}.x"))?;
    validate_finite(value.y, format!("{field}.y"))?;
    validate_finite(value.z, format!("{field}.z"))?;
    Ok(())
}

fn validate_segment(start: &Vec3, end: &Vec3, field: &str) -> Result<(), OracleV1ValidationError> {
    validate_vec3(start, &format!("{field}.start"))?;
    validate_vec3(end, &format!("{field}.end"))?;
    let dx = f64::from(end.x) - f64::from(start.x);
    let dy = f64::from(end.y) - f64::from(start.y);
    let dz = f64::from(end.z) - f64::from(start.z);
    if dx * dx + dy * dy + dz * dz <= f64::EPSILON {
        return Err(OracleV1ValidationError::DegenerateSegment {
            field: field.to_owned(),
        });
    }
    Ok(())
}

fn validate_fraction(value: f32, field: String) -> Result<(), OracleV1ValidationError> {
    validate_finite(value, field.clone())?;
    if !(0.0..=1.0).contains(&value) {
        return Err(OracleV1ValidationError::InvalidFraction { field });
    }
    Ok(())
}

fn validate_finite(value: f32, field: String) -> Result<(), OracleV1ValidationError> {
    if !value.is_finite() {
        return Err(OracleV1ValidationError::NonFiniteNumber { field });
    }
    Ok(())
}

fn validate_encoded_size<T: serde::Serialize>(
    value: &T,
    key: OracleV1BatchKey,
) -> Result<(), OracleV1ValidationError> {
    let encoded =
        serde_json::to_vec(value).map_err(|_| OracleV1ValidationError::EncodingFailure { key })?;
    let received = encoded.len().saturating_add(1);
    if received > MAX_ORACLE_LINE_BYTES {
        return Err(OracleV1ValidationError::EncodedBatchTooLarge {
            key,
            received,
            maximum: MAX_ORACLE_LINE_BYTES,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{OracleOperation, OracleQuery, OracleResult, QueryOutcome, Vec3};

    fn entity(generation: u64) -> EntityRef {
        EntityRef {
            entity_id: 42,
            generation,
        }
    }

    fn query(query_id: u64, generation: u64) -> OracleQuery {
        OracleQuery {
            query_id,
            entity: entity(generation),
            operation: OracleOperation::GroundSample {
                position: Vec3 {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                },
            },
        }
    }

    fn request(round_id: u64, epoch: u64, tick: u64, batch_id: u64) -> BatchRequest {
        BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id,
            epoch,
            tick,
            batch_id,
            queries: vec![query(1, 1)],
        }
    }

    fn reply_for(request: &BatchRequest) -> BatchReply {
        BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: request.round_id,
            epoch: request.epoch,
            tick: request.tick,
            batch_id: request.batch_id,
            results: request
                .queries
                .iter()
                .map(|query| OracleResult {
                    query_id: query.query_id,
                    entity: query.entity,
                    outcome: QueryOutcome::GroundSample { sample: None },
                })
                .collect(),
        }
    }

    #[test]
    fn accepts_a_complete_request_reply_pair() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 5);
        validator.accept_request(request.clone()).unwrap();
        let outcome = validator.accept_reply(reply_for(&request)).unwrap();
        assert_eq!(
            outcome,
            ValidationOutcome::ReplyAccepted { key: request.key() }
        );
        assert_eq!(validator.pending_batches(), 0);
    }

    #[test]
    fn rejects_duplicate_batches() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 5);
        validator.accept_request(request.clone()).unwrap();
        assert_eq!(
            validator.accept_request(request.clone()).unwrap_err(),
            ValidationError::DuplicateBatch { key: request.key() }
        );
    }

    #[test]
    fn rejects_reused_batch_ids_on_another_tick() {
        let mut validator = OracleValidator::new();
        validator.accept_request(request(1, 0, 10, 5)).unwrap();
        let error = validator.accept_request(request(1, 0, 11, 5)).unwrap_err();
        assert!(matches!(error, ValidationError::ReusedBatchId { .. }));
    }

    #[test]
    fn rejects_regressed_request_ticks() {
        let mut validator = OracleValidator::new();
        validator.accept_request(request(1, 0, 10, 1)).unwrap();
        assert_eq!(
            validator.accept_request(request(1, 0, 9, 2)).unwrap_err(),
            ValidationError::RegressedTick {
                last: 10,
                received: 9,
            }
        );
    }

    #[test]
    fn advancing_epoch_abandons_pending_and_rejects_old_reply_as_stale() {
        let mut validator = OracleValidator::new();
        let old = request(1, 0, 10, 1);
        validator.accept_request(old.clone()).unwrap();
        let outcome = validator.accept_request(request(1, 1, 1, 1)).unwrap();
        assert!(matches!(
            outcome,
            ValidationOutcome::RequestAccepted {
                scope_changed: true,
                abandoned_pending: 1,
                ..
            }
        ));
        assert!(matches!(
            validator.accept_reply(reply_for(&old)).unwrap_err(),
            ValidationError::StaleReplyScope { .. }
        ));
    }

    #[test]
    fn rejects_a_request_from_an_older_scope() {
        let mut validator = OracleValidator::new();
        validator.accept_request(request(2, 0, 1, 1)).unwrap();
        assert!(matches!(
            validator.accept_request(request(1, 99, 1, 1)).unwrap_err(),
            ValidationError::StaleRequestScope { .. }
        ));
    }

    #[test]
    fn advancing_entity_generation_invalidates_older_pending_batch() {
        let mut validator = OracleValidator::new();
        let old = request(1, 0, 10, 1);
        validator.accept_request(old.clone()).unwrap();
        let mut new = request(1, 0, 11, 2);
        new.queries[0] = query(2, 2);
        let outcome = validator.accept_request(new).unwrap();
        assert!(matches!(
            outcome,
            ValidationOutcome::RequestAccepted {
                invalidated_pending: 1,
                ..
            }
        ));
        assert_eq!(
            validator.accept_reply(reply_for(&old)).unwrap_err(),
            ValidationError::InvalidatedBatchReply { key: old.key() }
        );
    }

    #[test]
    fn rejects_stale_entity_generation_after_advance() {
        let mut validator = OracleValidator::new();
        let mut current = request(1, 0, 10, 1);
        current.queries[0] = query(1, 3);
        validator.accept_request(current).unwrap();
        let stale = request(1, 0, 11, 2);
        assert!(matches!(
            validator.accept_request(stale).unwrap_err(),
            ValidationError::StaleEntityGeneration {
                current: 3,
                received: 1,
                ..
            }
        ));
    }

    #[test]
    fn rejects_duplicate_results() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 1);
        validator.accept_request(request.clone()).unwrap();
        let mut reply = reply_for(&request);
        reply.results.push(reply.results[0].clone());
        assert!(matches!(
            validator.accept_reply(reply).unwrap_err(),
            ValidationError::DuplicateResult { .. }
        ));
    }

    #[test]
    fn rejects_missing_results() {
        let mut validator = OracleValidator::new();
        let mut request = request(1, 0, 10, 1);
        request.queries.push(query(2, 1));
        validator.accept_request(request.clone()).unwrap();
        let mut reply = reply_for(&request);
        reply.results.pop();
        assert!(matches!(
            validator.accept_reply(reply).unwrap_err(),
            ValidationError::MissingResult { query_id: 2, .. }
        ));
    }

    #[test]
    fn rejects_entity_generation_changed_in_reply() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 1);
        validator.accept_request(request.clone()).unwrap();
        let mut reply = reply_for(&request);
        reply.results[0].entity.generation = 2;
        assert!(matches!(
            validator.accept_reply(reply).unwrap_err(),
            ValidationError::EntityReferenceMismatch { .. }
        ));
    }

    #[test]
    fn rejects_an_outcome_for_the_wrong_operation() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 1);
        validator.accept_request(request.clone()).unwrap();
        let mut reply = reply_for(&request);
        reply.results[0].outcome = QueryOutcome::WaterSample { height: None };
        assert!(matches!(
            validator.accept_reply(reply).unwrap_err(),
            ValidationError::OutcomeKindMismatch { .. }
        ));
    }

    #[test]
    fn allows_an_explicit_error_for_any_operation() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 1);
        validator.accept_request(request.clone()).unwrap();
        let mut reply = reply_for(&request);
        reply.results[0].outcome = QueryOutcome::Error {
            code: "native_query_failed".to_owned(),
            message: "query unavailable".to_owned(),
        };
        validator.accept_reply(reply).unwrap();
    }

    #[test]
    fn rejects_a_second_reply_to_completed_batch() {
        let mut validator = OracleValidator::new();
        let request = request(1, 0, 10, 1);
        let reply = reply_for(&request);
        validator.accept_request(request.clone()).unwrap();
        validator.accept_reply(reply.clone()).unwrap();
        assert_eq!(
            validator.accept_reply(reply).unwrap_err(),
            ValidationError::DuplicateReply { key: request.key() }
        );
    }

    #[test]
    fn rejects_unsupported_protocol_versions_without_mutating_state() {
        let mut validator = OracleValidator::new();
        let mut request = request(1, 0, 10, 1);
        request.protocol_version = 99;
        assert!(matches!(
            validator.accept_request(request).unwrap_err(),
            ValidationError::UnsupportedProtocolVersion { .. }
        ));
        assert_eq!(validator.active_scope(), None);
        assert_eq!(validator.pending_batches(), 0);
    }
}

#[cfg(test)]
mod oracle_v1_tests {
    use super::*;
    use crate::protocol::{
        DestructibleHullCandidate, DestructibleShellKind, DestructibleShotCandidate,
        DestructibleStaticCollision, EntityRef, FiringLaneEvidence, FiringLaneEvidenceQuery,
        OracleV1Result, SegmentCastPrimitive, SpottingEvidence, SpottingEvidenceQuery,
        SurfaceSample, VehicleCriticalCrewName, VehicleCriticalDeviceName, VehicleCriticalTarget,
        VehicleHitLayer, VehicleHitMaterial, VehicleInternalCriticalHit, ORACLE_PIPELINE_TICKS,
    };

    fn query(query_id: u64) -> OracleV1Query {
        OracleV1Query {
            query_id,
            key: format!("ground:bot:{query_id}"),
            query_generation: 1,
            entity: EntityRef {
                entity_id: query_id as i64,
                generation: 1,
            },
            operation: OracleOperation::GroundSample {
                position: Vec3 {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                },
            },
        }
    }

    fn request() -> OracleV1BatchRequest {
        OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: 7,
            authority_epoch: 3,
            oracle_generation: 2,
            batch_seq: 9,
            issued_tick: 30,
            apply_tick: 30 + ORACLE_PIPELINE_TICKS,
            world_revision: 5,
            queries: vec![query(1)],
        }
    }

    fn reply(request: &OracleV1BatchRequest) -> OracleV1BatchReply {
        OracleV1BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: 11,
            results: request
                .queries
                .iter()
                .map(|query| OracleV1Result {
                    query_id: query.query_id,
                    key: query.key.clone(),
                    query_generation: query.query_generation,
                    entity: query.entity,
                    status: OracleV1ResultStatus::Ok {
                        outcome: QueryOutcome::GroundSample {
                            sample: Some(SurfaceSample {
                                height: 2.0,
                                normal: Vec3 {
                                    x: 0.0,
                                    y: 1.0,
                                    z: 0.0,
                                },
                                material_id: None,
                            }),
                        },
                    },
                })
                .collect(),
        }
    }

    fn vehicle_layer(distance_m: f64) -> VehicleHitLayer {
        VehicleHitLayer {
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
        }
    }

    fn vehicle_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        request.queries[0].operation = OracleOperation::VehicleHitTest {
            start: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            end: Vec3 {
                x: 10.0,
                y: 0.0,
                z: 0.0,
            },
            target: request.queries[0].entity,
        };
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::VehicleHitTest {
                hit: Some(VehicleHit {
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
                    hit_part: "vehicleHull".to_owned(),
                    layers: vec![vehicle_layer(2.5), vehicle_layer(3.0)],
                    internal_hits: Some(vec![VehicleInternalCriticalHit {
                        distance_m: 2.75,
                        target: VehicleCriticalTarget::Crew(VehicleCriticalCrewName::Commander),
                    }]),
                }),
            },
        };
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        hit.layers[0].critical_target = Some(VehicleCriticalTarget::Device(
            VehicleCriticalDeviceName::EngineHealth,
        ));
        hit.layers[0].chance_to_hit_by_projectile = Some(0.45);
        hit.layers[0].chance_to_hit_by_explosion = Some(0.15);
        (request, reply)
    }

    fn explosion_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        request.queries[0].key = "explosion/b2".to_owned();
        let target = request.queries[0].entity;
        let pose = ExplosionTargetPose {
            position: Vec3 {
                x: 5.0,
                y: 0.0,
                z: 0.0,
            },
            yaw: 0.25,
            pitch: -0.1,
            roll: 0.05,
            turret_yaw: 0.4,
            gun_pitch: -0.2,
            siege_state: 2,
        };
        request.queries[0].operation = OracleOperation::ExplosionEvidence(ExplosionEvidenceQuery {
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
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::ExplosionEvidence(ExplosionEvidence {
                target_pose: pose,
                vehicle_ray: Some(crate::protocol::ExplosionVehicleRay {
                    layers: vec![ExplosionHitLayer {
                        distance_m: 2.5,
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
                        chance_to_hit_by_explosion: Some(0.15),
                    }],
                }),
                internal_hits: Some(vec![VehicleInternalCriticalHit {
                    distance_m: 0.5,
                    target: VehicleCriticalTarget::Crew(VehicleCriticalCrewName::Commander),
                }]),
            }),
        };
        (request, reply)
    }

    fn spotting_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        let target = request.queries[0].entity;
        request.queries[0].operation = OracleOperation::SpottingEvidence(SpottingEvidenceQuery {
            observer: EntityRef {
                entity_id: 77,
                generation: 4,
            },
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
        });
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::SpottingEvidence(SpottingEvidence {
                line_of_sight: true,
                foliage_bonus: 0.25,
                evaluated_for_recent_fire: true,
            }),
        };
        (request, reply)
    }

    fn ram_contact_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        request.queries[0].key = "ram/h1-b1".to_owned();
        let first = request.queries[0].entity;
        request.queries[0].operation =
            OracleOperation::RamContactArmorEvidence(RamContactArmorEvidenceQuery {
                first,
                second: EntityRef {
                    entity_id: 42,
                    generation: 3,
                },
                first_pose: RamContactPose {
                    position: Vec3 {
                        x: 1.0,
                        y: 0.0,
                        z: 0.0,
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
                        y: 0.0,
                        z: 0.0,
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
                    y: 0.5,
                    z: 0.0,
                },
                contact_normal: Vec3 {
                    x: 1.0,
                    y: 0.0,
                    z: 0.0,
                },
            });
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::RamContactArmorEvidence(RamContactArmorEvidence {
                first_armor_mm: 70.0,
                second_armor_mm: 45.0,
            }),
        };
        (request, reply)
    }

    fn destructible_shot_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        request.queries[0].key = "destructible-shot:42".to_owned();
        request.queries[0].operation =
            OracleOperation::DestructibleShotEvidence(DestructibleShotEvidenceQuery {
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
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(DestructibleShotEvidence {
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
            }),
        };
        (request, reply)
    }

    fn destructible_hull_exchange() -> (OracleV1BatchRequest, OracleV1BatchReply) {
        let mut request = request();
        request.queries[0].key = "destructible-hull:42".to_owned();
        request.queries[0].operation =
            OracleOperation::DestructibleHullEvidence(DestructibleHullEvidenceQuery {
                space_id: 7,
                position: Vec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 0.0,
                },
                yaw: 0.0,
                frame_travel: 0.3,
            });
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleHullEvidence(DestructibleHullEvidence {
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
            }),
        };
        (request, reply)
    }

    #[test]
    fn accepts_one_complete_finite_oracle_v1_batch() {
        let request = request();
        let reply = reply(&request);
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();
    }

    #[test]
    fn accepts_atomic_ram_contact_armor_evidence() {
        let (request, reply) = ram_contact_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();
    }

    #[test]
    fn rejects_unfenced_or_geometrically_invalid_ram_contact_queries() {
        let (request, _unused_reply) = ram_contact_exchange();
        let mut cases = Vec::new();

        let mut first_mismatch = request.clone();
        let OracleOperation::RamContactArmorEvidence(arguments) =
            &mut first_mismatch.queries[0].operation
        else {
            unreachable!()
        };
        arguments.first.entity_id += 1;
        cases.push(first_mismatch);

        let mut alias = request.clone();
        let OracleOperation::RamContactArmorEvidence(arguments) = &mut alias.queries[0].operation
        else {
            unreachable!()
        };
        arguments.second = arguments.first;
        cases.push(alias);

        let mut non_unit = request.clone();
        let OracleOperation::RamContactArmorEvidence(arguments) =
            &mut non_unit.queries[0].operation
        else {
            unreachable!()
        };
        arguments.contact_normal.x = 0.5;
        cases.push(non_unit);

        let mut distant = request.clone();
        let OracleOperation::RamContactArmorEvidence(arguments) = &mut distant.queries[0].operation
        else {
            unreachable!()
        };
        arguments.contact_point.x = 102.0;
        cases.push(distant);

        for invalid in cases {
            assert!(matches!(
                validate_oracle_v1_request(&invalid).unwrap_err(),
                OracleV1ValidationError::InvalidRamContactQuery { .. }
            ));
        }
    }

    #[test]
    fn rejects_missing_or_invalid_structural_ram_armor() {
        let (request, reply) = ram_contact_exchange();
        for armor in [0.0, -1.0, f64::NAN, MAX_VEHICLE_HIT_ARMOR_MM + 1.0] {
            let mut invalid = reply.clone();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::RamContactArmorEvidence(evidence),
            } = &mut invalid.results[0].status
            else {
                unreachable!()
            };
            evidence.second_armor_mm = armor;
            assert!(matches!(
                validate_oracle_v1_reply(&invalid, &request).unwrap_err(),
                OracleV1ValidationError::InvalidRamContactEvidence { .. }
            ));
        }
    }

    #[test]
    fn rejects_non_pipeline_apply_tick() {
        let mut request = request();
        request.apply_tick += 1;
        assert!(matches!(
            validate_oracle_v1_request(&request).unwrap_err(),
            OracleV1ValidationError::InvalidPipelineWindow { .. }
        ));
    }

    #[test]
    fn rejects_incomplete_reply() {
        let request = request();
        let mut reply = reply(&request);
        reply.results.clear();
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::MissingResult { query_id: 1, .. }
        ));
    }

    #[test]
    fn rejects_non_finite_reply_value() {
        let request = request();
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::GroundSample {
                sample: Some(SurfaceSample {
                    height: f32::INFINITY,
                    normal: Vec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0,
                    },
                    material_id: None,
                }),
            },
        };
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::NonFiniteNumber { .. }
        ));
    }

    #[test]
    fn enforces_batch_query_limit() {
        let mut request = request();
        request.queries = (1..=(MAX_ORACLE_BATCH_QUERIES as u64 + 1))
            .map(query)
            .collect();
        assert!(matches!(
            validate_oracle_v1_request(&request).unwrap_err(),
            OracleV1ValidationError::TooManyQueries { .. }
        ));
    }

    #[test]
    fn batched_rays_count_each_native_primitive() {
        let mut request = request();
        let segment = SegmentCastPrimitive {
            start: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            end: Vec3 {
                x: 1.0,
                y: 0.0,
                z: 0.0,
            },
            collision_mask: 1,
        };
        request.queries[0].operation = OracleOperation::SegmentCastBatch {
            segments: vec![segment; MAX_ORACLE_PRIMITIVE_OPERATIONS + 1],
        };
        assert!(matches!(
            validate_oracle_v1_request(&request).unwrap_err(),
            OracleV1ValidationError::TooManyPrimitiveOperations { .. }
        ));
    }

    #[test]
    fn batched_reply_must_echo_one_result_per_primitive() {
        let mut request = request();
        request.queries[0].operation = OracleOperation::GroundSampleBatch {
            positions: vec![
                Vec3 {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                },
                Vec3 {
                    x: 4.0,
                    y: 5.0,
                    z: 6.0,
                },
            ],
        };
        validate_oracle_v1_request(&request).unwrap();
        let mut reply = reply(&request);
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::GroundSampleBatch {
                samples: vec![None],
            },
        };
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::ResultCardinalityMismatch {
                expected: 2,
                received: 1,
                ..
            }
        ));
    }

    #[test]
    fn accepts_complete_ordered_vehicle_armor_layers() {
        let (request, reply) = vehicle_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();
    }

    #[test]
    fn rejects_nan_regressed_and_summary_mismatched_vehicle_layers() {
        let mutators: [fn(&mut VehicleHit); 3] = [
            |hit: &mut VehicleHit| hit.layers[0].material.armor_mm = f64::NAN,
            |hit: &mut VehicleHit| hit.layers[1].distance_m = 2.0,
            |hit: &mut VehicleHit| hit.fraction = 0.5,
        ];
        for mutate in mutators {
            let (request, mut reply) = vehicle_exchange();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
            } = &mut reply.results[0].status
            else {
                unreachable!()
            };
            mutate(hit);
            assert!(matches!(
                validate_oracle_v1_reply(&reply, &request).unwrap_err(),
                OracleV1ValidationError::InvalidVehicleHit { .. }
            ));
        }
    }

    #[test]
    fn rejects_too_many_vehicle_layers_and_missing_collide_once_identity() {
        let (request, mut reply) = vehicle_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        hit.layers = vec![vehicle_layer(2.5); MAX_VEHICLE_HIT_LAYERS + 1];
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::TooManyVehicleHitLayers { .. }
        ));

        let (request, mut reply) = vehicle_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        hit.layers[0].material.collide_once_only = true;
        hit.layers[0].material.kind = None;
        hit.layers[0].material.native_identity = None;
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidVehicleHit { .. }
        ));
    }

    #[test]
    fn rejects_malformed_critical_chances_and_internal_trace() {
        let mutators: [fn(&mut VehicleHit); 4] = [
            |hit: &mut VehicleHit| hit.layers[0].chance_to_hit_by_projectile = None,
            |hit: &mut VehicleHit| hit.layers[0].chance_to_hit_by_projectile = Some(f64::NAN),
            |hit: &mut VehicleHit| {
                hit.internal_hits.as_mut().unwrap()[0].distance_m = f64::INFINITY
            },
            |hit: &mut VehicleHit| {
                hit.internal_hits.as_mut().unwrap()[0].target =
                    VehicleCriticalTarget::Device(VehicleCriticalDeviceName::EngineHealth)
            },
        ];
        for mutate in mutators {
            let (request, mut reply) = vehicle_exchange();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
            } = &mut reply.results[0].status
            else {
                unreachable!()
            };
            mutate(hit);
            assert!(matches!(
                validate_oracle_v1_reply(&reply, &request).unwrap_err(),
                OracleV1ValidationError::InvalidVehicleHit { .. }
            ));
        }

        let (request, mut reply) = vehicle_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::VehicleHitTest { hit: Some(hit) },
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        hit.internal_hits = Some(vec![
            VehicleInternalCriticalHit {
                distance_m: 2.75,
                target: VehicleCriticalTarget::Crew(VehicleCriticalCrewName::Commander),
            };
            MAX_VEHICLE_INTERNAL_HITS + 1
        ]);
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::TooManyVehicleInternalHits { .. }
        ));
    }

    #[test]
    fn accepts_frozen_explosion_evidence_with_explicit_layout_states() {
        let (request, reply) = explosion_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();

        let (request, mut reply) = explosion_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::ExplosionEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.vehicle_ray = None;
        evidence.internal_hits = None;
        validate_oracle_v1_reply(&reply, &request).unwrap();

        let (request, mut reply) = explosion_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::ExplosionEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.vehicle_ray = None;
        evidence.internal_hits = Some(Vec::new());
        validate_oracle_v1_reply(&reply, &request).unwrap();
    }

    #[test]
    fn rejects_unfenced_or_malformed_explosion_queries() {
        let mutators: [fn(&mut ExplosionEvidenceQuery); 5] = [
            |query| query.target.generation += 1,
            |query| query.incoming_direction.x = 0.5,
            |query| query.caliber_mm = f64::NAN,
            |query| query.target_pose.turret_yaw = f64::INFINITY,
            |query| query.target_pose.siege_state = 4,
        ];
        for mutate in mutators {
            let (mut request, _) = explosion_exchange();
            let OracleOperation::ExplosionEvidence(arguments) = &mut request.queries[0].operation
            else {
                unreachable!()
            };
            mutate(arguments);
            assert!(matches!(
                validate_oracle_v1_request(&request).unwrap_err(),
                OracleV1ValidationError::InvalidExplosionQuery { .. }
            ));
        }
    }

    #[test]
    fn rejects_mixed_pose_unordered_layers_and_invalid_cone_trace() {
        let mutators: [fn(&mut ExplosionEvidence); 5] = [
            |evidence| evidence.target_pose.yaw += 0.01,
            |evidence| {
                let ray = evidence.vehicle_ray.as_mut().unwrap();
                let mut second = ray.layers[0].clone();
                second.distance_m = 2.0;
                second.critical_target = None;
                second.chance_to_hit_by_explosion = None;
                ray.layers.push(second);
            },
            |evidence| {
                evidence.vehicle_ray.as_mut().unwrap().layers[0].chance_to_hit_by_explosion = None
            },
            |evidence| evidence.internal_hits.as_mut().unwrap()[0].distance_m = 2.0,
            |evidence| {
                evidence.internal_hits.as_mut().unwrap()[0].target =
                    VehicleCriticalTarget::Device(VehicleCriticalDeviceName::EngineHealth)
            },
        ];
        for mutate in mutators {
            let (request, mut reply) = explosion_exchange();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::ExplosionEvidence(evidence),
            } = &mut reply.results[0].status
            else {
                unreachable!()
            };
            mutate(evidence);
            assert!(matches!(
                validate_oracle_v1_reply(&reply, &request).unwrap_err(),
                OracleV1ValidationError::InvalidExplosionEvidence { .. }
            ));
        }
    }

    #[test]
    fn spotting_pair_and_recent_fire_branch_are_strictly_fenced() {
        let (request, reply) = spotting_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();

        let mut mismatched_target = request.clone();
        let OracleOperation::SpottingEvidence(arguments) =
            &mut mismatched_target.queries[0].operation
        else {
            unreachable!()
        };
        arguments.target.entity_id += 1;
        assert!(matches!(
            validate_oracle_v1_request(&mismatched_target).unwrap_err(),
            OracleV1ValidationError::ObservationTargetMismatch { .. }
        ));

        let mut mismatched_recent = reply.clone();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::SpottingEvidence(evidence),
        } = &mut mismatched_recent.results[0].status
        else {
            unreachable!()
        };
        evidence.evaluated_for_recent_fire = false;
        assert!(matches!(
            validate_oracle_v1_reply(&mismatched_recent, &request).unwrap_err(),
            OracleV1ValidationError::InvalidSpottingEvidence {
                field: "evaluated_for_recent_fire",
                ..
            }
        ));
    }

    #[test]
    fn spotting_foliage_is_finite_bounded_and_zero_when_occluded() {
        for foliage_bonus in [-0.01, 0.61, f64::NAN] {
            let (request, mut reply) = spotting_exchange();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::SpottingEvidence(evidence),
            } = &mut reply.results[0].status
            else {
                unreachable!()
            };
            evidence.foliage_bonus = foliage_bonus;
            assert!(validate_oracle_v1_reply(&reply, &request).is_err());
        }

        let (request, mut reply) = spotting_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::SpottingEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.line_of_sight = false;
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidSpottingEvidence {
                field: "occluded_foliage_bonus",
                ..
            }
        ));
    }

    #[test]
    fn firing_lane_outcome_cannot_substitute_for_visibility() {
        let (mut request, mut reply) = spotting_exchange();
        let OracleOperation::SpottingEvidence(arguments) = request.queries[0].operation.clone()
        else {
            unreachable!()
        };
        request.queries[0].operation =
            OracleOperation::FiringLaneEvidence(FiringLaneEvidenceQuery {
                observer: arguments.observer,
                target: arguments.target,
                observer_position: arguments.observer_position,
                target_position: arguments.target_position,
                collision_mask: arguments.collision_mask,
            });
        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::SpottingEvidence(SpottingEvidence {
                line_of_sight: true,
                foliage_bonus: 0.0,
                evaluated_for_recent_fire: true,
            }),
        };
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::OutcomeKindMismatch {
                expected: OracleOperationKind::FiringLaneEvidence,
                received: OracleOperationKind::SpottingEvidence,
                ..
            }
        ));

        reply.results[0].status = OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::FiringLaneEvidence(FiringLaneEvidence { clear: false }),
        };
        validate_oracle_v1_reply(&reply, &request).unwrap();
    }

    #[test]
    fn destructible_requests_keep_t_plus_three_and_charge_bounded_candidate_work() {
        let (shot, _) = destructible_shot_exchange();
        let (hull, _) = destructible_hull_exchange();
        validate_oracle_v1_request(&shot).unwrap();
        validate_oracle_v1_request(&hull).unwrap();
        assert_eq!(shot.apply_tick, shot.issued_tick + ORACLE_PIPELINE_TICKS);
        assert_eq!(
            shot.queries[0].operation.primitive_count(),
            MAX_DESTRUCTIBLE_CANDIDATES
        );
        assert_eq!(
            hull.queries[0].operation.primitive_count(),
            MAX_DESTRUCTIBLE_HULL_CANDIDATES
        );

        let mut over_budget = shot;
        let template = over_budget.queries[0].clone();
        over_budget.queries = (1..=5)
            .map(|query_id| {
                let mut query = template.clone();
                query.query_id = query_id;
                query.key = format!("destructible-shot:{query_id}");
                query
            })
            .collect();
        assert!(matches!(
            validate_oracle_v1_request(&over_budget).unwrap_err(),
            OracleV1ValidationError::TooManyPrimitiveOperations {
                received: 320,
                maximum: MAX_ORACLE_PRIMITIVE_OPERATIONS,
                ..
            }
        ));
    }

    #[test]
    fn destructible_request_numeric_bounds_fail_closed() {
        let (mut shot, _) = destructible_shot_exchange();
        let OracleOperation::DestructibleShotEvidence(arguments) = &mut shot.queries[0].operation
        else {
            unreachable!()
        };
        arguments.end.z = 10_001.0;
        assert!(matches!(
            validate_oracle_v1_request(&shot).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleQuery { .. }
        ));

        let (mut shot, _) = destructible_shot_exchange();
        let OracleOperation::DestructibleShotEvidence(arguments) = &mut shot.queries[0].operation
        else {
            unreachable!()
        };
        arguments.start.x = 100_001.0;
        assert!(matches!(
            validate_oracle_v1_request(&shot).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleQuery { .. }
        ));

        let (mut hull, _) = destructible_hull_exchange();
        let OracleOperation::DestructibleHullEvidence(arguments) = &mut hull.queries[0].operation
        else {
            unreachable!()
        };
        arguments.yaw = 2.0 * std::f64::consts::PI + 0.01;
        assert!(matches!(
            validate_oracle_v1_request(&hull).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleQuery { .. }
        ));
    }

    #[test]
    fn destructible_shot_accepts_ap_through_and_preserves_backing_collision() {
        let (request, reply) = destructible_shot_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();

        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &reply.results[0].status
        else {
            unreachable!()
        };
        assert!(evidence.candidates[0].ap_through);
        assert_eq!(evidence.candidates[0].piercing_loss, 25.0);
        assert!(
            evidence.static_collision.as_ref().unwrap().distance
                > evidence.candidates[0].exit_distance
        );
    }

    #[test]
    fn destructible_shot_rejects_ambiguous_verdicts_and_inconsistent_static_hits() {
        let (request, mut reply) = destructible_shot_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        let mut ambiguous = evidence.candidates[0];
        ambiguous.item_index += 1;
        ambiguous.entry_distance = 4.05;
        ambiguous.exit_distance = 5.05;
        ambiguous.impact_position.z = 4.05;
        evidence.candidates.push(ambiguous);
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleEvidence { .. }
        ));

        let (request, mut reply) = destructible_shot_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.candidates[0].ap_through = false;
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleEvidence { .. }
        ));

        let (request, mut reply) = destructible_shot_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.static_collision.as_mut().unwrap().position.z = 7.0;
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleEvidence { .. }
        ));
    }

    #[test]
    fn destructible_candidate_and_destroyed_skip_caps_match_the_bridge() {
        let (request, mut reply) = destructible_shot_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.candidates = vec![evidence.candidates[0]; MAX_DESTRUCTIBLE_CANDIDATES + 1];
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::TooManyDestructibleCandidates {
                maximum: MAX_DESTRUCTIBLE_CANDIDATES,
                ..
            }
        ));

        let (request, mut reply) = destructible_hull_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleHullEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.candidates = vec![evidence.candidates[0]; MAX_DESTRUCTIBLE_HULL_CANDIDATES + 1];
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::TooManyDestructibleCandidates {
                maximum: MAX_DESTRUCTIBLE_HULL_CANDIDATES,
                ..
            }
        ));

        let (request, mut reply) = destructible_shot_exchange();
        let OracleV1ResultStatus::Ok {
            outcome: QueryOutcome::DestructibleShotEvidence(evidence),
        } = &mut reply.results[0].status
        else {
            unreachable!()
        };
        evidence.destroyed_skipped = MAX_DESTRUCTIBLE_SKIPPED + 1;
        assert!(matches!(
            validate_oracle_v1_reply(&reply, &request).unwrap_err(),
            OracleV1ValidationError::InvalidDestructibleEvidence { .. }
        ));
    }

    #[test]
    fn destructible_hull_accepts_only_frozen_identity_geometry_and_travel() {
        let (request, reply) = destructible_hull_exchange();
        validate_oracle_v1_request(&request).unwrap();
        validate_oracle_v1_reply(&reply, &request).unwrap();

        let mutators: [fn(&mut DestructibleHullEvidence); 3] = [
            |evidence| evidence.candidates[0].obb_center.x = f32::INFINITY,
            |evidence| evidence.candidates[0].mat_kind = None,
            |evidence| evidence.frame_travel += 0.01,
        ];
        for mutate in mutators {
            let (request, mut reply) = destructible_hull_exchange();
            let OracleV1ResultStatus::Ok {
                outcome: QueryOutcome::DestructibleHullEvidence(evidence),
            } = &mut reply.results[0].status
            else {
                unreachable!()
            };
            mutate(evidence);
            assert!(matches!(
                validate_oracle_v1_reply(&reply, &request).unwrap_err(),
                OracleV1ValidationError::InvalidDestructibleEvidence { .. }
            ));
        }
    }

    #[test]
    fn destructible_unavailable_status_remains_an_explicit_fail_closed_result() {
        for (request, mut reply) in [destructible_shot_exchange(), destructible_hull_exchange()] {
            reply.results[0].status = OracleV1ResultStatus::Unavailable {
                code: "destructible_evidence_unavailable".to_owned(),
                message: "native evidence failed closed".to_owned(),
            };
            validate_oracle_v1_request(&request).unwrap();
            validate_oracle_v1_reply(&reply, &request).unwrap();
        }
    }
}
