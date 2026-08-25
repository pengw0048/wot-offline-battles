use crate::protocol::{
    BatchSequence, OracleFrameSequence, OracleLineage, OracleV1BatchKey, OracleV1BatchReply,
    OracleV1BatchRequest, QueryGeneration, Tick, MAX_ORACLE_REPLY_HISTORY,
};
use crate::validator::{
    validate_oracle_v1_reply, validate_oracle_v1_request, OracleV1ValidationError,
};
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet, VecDeque};
use thiserror::Error;

#[derive(Clone, Debug)]
struct PendingBatch {
    request: OracleV1BatchRequest,
    reply: Option<OracleV1BatchReply>,
}

#[derive(Clone, Debug)]
enum TerminalBatch {
    Applied(OracleV1BatchReply),
    TimedOut,
    Invalidated,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OracleReplyDropReason {
    StaleLineage,
    SupersededQueryGeneration,
    Late,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum OracleReplyDisposition {
    Buffered {
        key: OracleV1BatchKey,
        apply_tick: Tick,
    },
    DuplicateIgnored {
        key: OracleV1BatchKey,
    },
    Dropped {
        key: OracleV1BatchKey,
        reason: OracleReplyDropReason,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OracleRequestRegistration {
    pub key: OracleV1BatchKey,
    pub invalidated_batches: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AppliedOracleBatch {
    pub request: OracleV1BatchRequest,
    pub reply: OracleV1BatchReply,
}

#[derive(Clone, Debug, PartialEq)]
pub struct TimedOutOracleBatch {
    pub request: OracleV1BatchRequest,
}

#[derive(Clone, Debug, PartialEq)]
pub struct OracleTickOutcome {
    pub tick: Tick,
    pub applied: Vec<AppliedOracleBatch>,
    pub timed_out: Vec<TimedOutOracleBatch>,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum OracleBrokerError {
    #[error(transparent)]
    Validation(#[from] OracleV1ValidationError),
    #[error("oracle lineage must have non-zero round_id and oracle_generation")]
    InvalidLineage,
    #[error("cannot activate oracle lineage {received:?} after {active:?}")]
    RegressedLineage {
        active: OracleLineage,
        received: OracleLineage,
    },
    #[error("request lineage {received:?} does not match active lineage {active:?}")]
    RequestLineageMismatch {
        active: OracleLineage,
        received: OracleLineage,
    },
    #[error("reply lineage {received:?} is newer than active lineage {active:?}")]
    FutureReplyLineage {
        active: OracleLineage,
        received: OracleLineage,
    },
    #[error("request issued at tick {issued_tick}, but broker is at tick {current_tick}")]
    RequestTickMismatch {
        current_tick: Tick,
        issued_tick: Tick,
    },
    #[error("batch sequence {received} did not advance past {last}")]
    BatchSequenceDidNotAdvance {
        last: BatchSequence,
        received: BatchSequence,
    },
    #[error(
        "query key {key:?} generation {received} did not equal the required generation {expected}"
    )]
    QueryGenerationDidNotAdvance {
        key: String,
        expected: QueryGeneration,
        received: QueryGeneration,
    },
    #[error("reply references unknown current-lineage batch {key:?}")]
    UnknownBatch { key: OracleV1BatchKey },
    #[error("reply for batch {key:?} conflicts with the already accepted reply")]
    ConflictingDuplicate { key: OracleV1BatchKey },
    #[error("broker tick regressed from {current_tick} to {received_tick}")]
    RegressedTick {
        current_tick: Tick,
        received_tick: Tick,
    },
    #[error("broker tick skipped from {current_tick} to {received_tick}")]
    SkippedTick {
        current_tick: Tick,
        received_tick: Tick,
    },
    #[error("broker tick counter is exhausted at {current_tick}")]
    TickCounterExhausted { current_tick: Tick },
}

/// Pure in-memory fixed-latency broker between the Rust authority and #1513.
///
/// Replies never mutate simulation state directly. They are buffered until the
/// request's exact `apply_tick`, when `advance_to` returns the immutable batch
/// to the simulation loop. Missing replies become explicit timeouts at that
/// same boundary.
#[derive(Clone, Debug)]
pub struct OracleBroker {
    active_lineage: OracleLineage,
    current_tick: Tick,
    last_batch_seq: Option<BatchSequence>,
    last_oracle_frame_seq: Option<OracleFrameSequence>,
    latest_query_generations: HashMap<String, QueryGeneration>,
    query_batches: HashMap<String, OracleV1BatchKey>,
    pending: HashMap<OracleV1BatchKey, PendingBatch>,
    terminal: HashMap<OracleV1BatchKey, TerminalBatch>,
    terminal_order: VecDeque<OracleV1BatchKey>,
    terminal_batch_floor: Option<BatchSequence>,
}

impl OracleBroker {
    pub fn new(
        active_lineage: OracleLineage,
        current_tick: Tick,
    ) -> Result<Self, OracleBrokerError> {
        validate_lineage(active_lineage)?;
        Ok(Self {
            active_lineage,
            current_tick,
            last_batch_seq: None,
            last_oracle_frame_seq: None,
            latest_query_generations: HashMap::new(),
            query_batches: HashMap::new(),
            pending: HashMap::new(),
            terminal: HashMap::new(),
            terminal_order: VecDeque::new(),
            terminal_batch_floor: None,
        })
    }

    pub fn active_lineage(&self) -> OracleLineage {
        self.active_lineage
    }

    pub fn current_tick(&self) -> Tick {
        self.current_tick
    }

    pub fn pending_batches(&self) -> usize {
        self.pending.len()
    }

    pub fn last_oracle_frame_seq(&self) -> Option<OracleFrameSequence> {
        self.last_oracle_frame_seq
    }

    /// Install a newer authority/native-space incarnation and abandon all old
    /// work. Active-battle policy (normally terminating rather than reloading)
    /// remains the room state machine's responsibility.
    pub fn activate_lineage(
        &mut self,
        lineage: OracleLineage,
        current_tick: Tick,
    ) -> Result<usize, OracleBrokerError> {
        validate_lineage(lineage)?;
        if lineage <= self.active_lineage {
            return Err(OracleBrokerError::RegressedLineage {
                active: self.active_lineage,
                received: lineage,
            });
        }
        let abandoned = self.pending.len();
        self.active_lineage = lineage;
        self.current_tick = current_tick;
        self.last_batch_seq = None;
        self.last_oracle_frame_seq = None;
        self.latest_query_generations.clear();
        self.query_batches.clear();
        self.pending.clear();
        self.terminal.clear();
        self.terminal_order.clear();
        self.terminal_batch_floor = None;
        Ok(abandoned)
    }

    pub fn register_request(
        &mut self,
        request: OracleV1BatchRequest,
    ) -> Result<OracleRequestRegistration, OracleBrokerError> {
        validate_oracle_v1_request(&request)?;
        let received_lineage = request.lineage();
        if received_lineage != self.active_lineage {
            return Err(OracleBrokerError::RequestLineageMismatch {
                active: self.active_lineage,
                received: received_lineage,
            });
        }
        if request.issued_tick != self.current_tick {
            return Err(OracleBrokerError::RequestTickMismatch {
                current_tick: self.current_tick,
                issued_tick: request.issued_tick,
            });
        }
        if let Some(last) = self.last_batch_seq {
            if request.batch_seq <= last {
                return Err(OracleBrokerError::BatchSequenceDidNotAdvance {
                    last,
                    received: request.batch_seq,
                });
            }
        }

        for query in &request.queries {
            let expected = self
                .latest_query_generations
                .get(&query.key)
                .copied()
                .unwrap_or(0)
                .checked_add(1)
                .ok_or_else(|| OracleBrokerError::QueryGenerationDidNotAdvance {
                    key: query.key.clone(),
                    expected: QueryGeneration::MAX,
                    received: query.query_generation,
                })?;
            if query.query_generation != expected {
                return Err(OracleBrokerError::QueryGenerationDidNotAdvance {
                    key: query.key.clone(),
                    expected,
                    received: query.query_generation,
                });
            }
        }

        let superseded: HashSet<_> = request
            .queries
            .iter()
            .filter_map(|query| self.query_batches.get(&query.key).copied())
            .collect();
        for key in &superseded {
            self.invalidate_pending(*key);
        }

        let key = request.key();
        for query in &request.queries {
            self.latest_query_generations
                .insert(query.key.clone(), query.query_generation);
            self.query_batches.insert(query.key.clone(), key);
        }
        self.last_batch_seq = Some(request.batch_seq);
        self.pending.insert(
            key,
            PendingBatch {
                request,
                reply: None,
            },
        );
        Ok(OracleRequestRegistration {
            key,
            invalidated_batches: superseded.len(),
        })
    }

    pub fn accept_reply(
        &mut self,
        reply: OracleV1BatchReply,
    ) -> Result<OracleReplyDisposition, OracleBrokerError> {
        let key = reply.key();
        match reply.lineage().cmp(&self.active_lineage) {
            Ordering::Less => {
                return Ok(OracleReplyDisposition::Dropped {
                    key,
                    reason: OracleReplyDropReason::StaleLineage,
                });
            }
            Ordering::Greater => {
                return Err(OracleBrokerError::FutureReplyLineage {
                    active: self.active_lineage,
                    received: reply.lineage(),
                });
            }
            Ordering::Equal => {}
        }

        if let Some(terminal) = self.terminal.get(&key) {
            return match terminal {
                TerminalBatch::Applied(accepted) if accepted == &reply => {
                    Ok(OracleReplyDisposition::DuplicateIgnored { key })
                }
                TerminalBatch::Applied(_) => Err(OracleBrokerError::ConflictingDuplicate { key }),
                TerminalBatch::TimedOut => Ok(OracleReplyDisposition::Dropped {
                    key,
                    reason: OracleReplyDropReason::Late,
                }),
                TerminalBatch::Invalidated => Ok(OracleReplyDisposition::Dropped {
                    key,
                    reason: OracleReplyDropReason::SupersededQueryGeneration,
                }),
            };
        }
        if self
            .terminal_batch_floor
            .is_some_and(|floor| reply.batch_seq <= floor)
        {
            return Ok(OracleReplyDisposition::Dropped {
                key,
                reason: OracleReplyDropReason::Late,
            });
        }

        let pending = self
            .pending
            .get_mut(&key)
            .ok_or(OracleBrokerError::UnknownBatch { key })?;
        if reply.apply_tick <= self.current_tick {
            let removed = self.remove_pending(key).expect("pending batch disappeared");
            self.remember_terminal(key, TerminalBatch::TimedOut);
            drop(removed);
            return Ok(OracleReplyDisposition::Dropped {
                key,
                reason: OracleReplyDropReason::Late,
            });
        }
        validate_oracle_v1_reply(&reply, &pending.request)?;
        if let Some(accepted) = &pending.reply {
            return if accepted == &reply {
                Ok(OracleReplyDisposition::DuplicateIgnored { key })
            } else {
                Err(OracleBrokerError::ConflictingDuplicate { key })
            };
        }
        self.last_oracle_frame_seq = Some(
            self.last_oracle_frame_seq
                .map_or(reply.oracle_frame_seq, |last| {
                    last.max(reply.oracle_frame_seq)
                }),
        );
        let apply_tick = reply.apply_tick;
        pending.reply = Some(reply);
        Ok(OracleReplyDisposition::Buffered { key, apply_tick })
    }

    /// Advance exactly one simulation tick and release only work scheduled for
    /// that tick. The caller drains socket replies before invoking this method.
    pub fn advance_to(&mut self, tick: Tick) -> Result<OracleTickOutcome, OracleBrokerError> {
        if tick <= self.current_tick {
            return Err(OracleBrokerError::RegressedTick {
                current_tick: self.current_tick,
                received_tick: tick,
            });
        }
        let expected =
            self.current_tick
                .checked_add(1)
                .ok_or(OracleBrokerError::TickCounterExhausted {
                    current_tick: self.current_tick,
                })?;
        if tick != expected {
            return Err(OracleBrokerError::SkippedTick {
                current_tick: self.current_tick,
                received_tick: tick,
            });
        }

        let mut due: Vec<_> = self
            .pending
            .iter()
            .filter_map(|(key, pending)| (pending.request.apply_tick == tick).then_some(*key))
            .collect();
        due.sort_by_key(|key| key.batch_seq);

        let mut applied = Vec::new();
        let mut timed_out = Vec::new();
        for key in due {
            let pending = self.remove_pending(key).expect("due batch disappeared");
            if let Some(reply) = pending.reply {
                self.remember_terminal(key, TerminalBatch::Applied(reply.clone()));
                applied.push(AppliedOracleBatch {
                    request: pending.request,
                    reply,
                });
            } else {
                self.remember_terminal(key, TerminalBatch::TimedOut);
                timed_out.push(TimedOutOracleBatch {
                    request: pending.request,
                });
            }
        }
        self.current_tick = tick;
        Ok(OracleTickOutcome {
            tick,
            applied,
            timed_out,
        })
    }

    fn invalidate_pending(&mut self, key: OracleV1BatchKey) {
        if self.remove_pending(key).is_some() {
            self.remember_terminal(key, TerminalBatch::Invalidated);
        }
    }

    fn remove_pending(&mut self, key: OracleV1BatchKey) -> Option<PendingBatch> {
        let pending = self.pending.remove(&key)?;
        for query in &pending.request.queries {
            if self.query_batches.get(&query.key) == Some(&key) {
                self.query_batches.remove(&query.key);
            }
        }
        Some(pending)
    }

    fn remember_terminal(&mut self, key: OracleV1BatchKey, state: TerminalBatch) {
        if self.terminal.insert(key, state).is_none() {
            self.terminal_order.push_back(key);
        }
        while self.terminal_order.len() > MAX_ORACLE_REPLY_HISTORY {
            if let Some(expired) = self.terminal_order.pop_front() {
                self.terminal.remove(&expired);
                self.terminal_batch_floor = Some(
                    self.terminal_batch_floor
                        .map_or(expired.batch_seq, |floor| floor.max(expired.batch_seq)),
                );
            }
        }
    }
}

fn validate_lineage(lineage: OracleLineage) -> Result<(), OracleBrokerError> {
    if lineage.round_id == 0 || lineage.oracle_generation == 0 {
        return Err(OracleBrokerError::InvalidLineage);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{
        EntityRef, OracleOperation, OracleV1Query, OracleV1Result, OracleV1ResultStatus,
        QueryOutcome, SurfaceSample, Vec3, ORACLE_PIPELINE_TICKS, ORACLE_PROTOCOL_VERSION,
    };

    fn lineage(epoch: u64, oracle_generation: u64) -> OracleLineage {
        OracleLineage {
            round_id: 7,
            authority_epoch: epoch,
            oracle_generation,
        }
    }

    fn request(
        lineage: OracleLineage,
        issued_tick: Tick,
        batch_seq: BatchSequence,
        query_generation: QueryGeneration,
    ) -> OracleV1BatchRequest {
        OracleV1BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: lineage.round_id,
            authority_epoch: lineage.authority_epoch,
            oracle_generation: lineage.oracle_generation,
            batch_seq,
            issued_tick,
            apply_tick: issued_tick + ORACLE_PIPELINE_TICKS,
            world_revision: 4,
            queries: vec![OracleV1Query {
                query_id: batch_seq,
                key: "ground:bot:42".to_owned(),
                query_generation,
                entity: EntityRef {
                    entity_id: 42,
                    generation: 1,
                },
                operation: OracleOperation::GroundSample {
                    position: Vec3 {
                        x: 1.0,
                        y: 2.0,
                        z: 3.0,
                    },
                },
            }],
        }
    }

    fn reply(request: &OracleV1BatchRequest, frame: u64) -> OracleV1BatchReply {
        OracleV1BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: request.round_id,
            authority_epoch: request.authority_epoch,
            oracle_generation: request.oracle_generation,
            batch_seq: request.batch_seq,
            issued_tick: request.issued_tick,
            apply_tick: request.apply_tick,
            world_revision: request.world_revision,
            oracle_frame_seq: frame,
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

    #[test]
    fn early_reply_is_cached_and_applied_once_at_exact_tick() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        let request = request(active, 30, 1, 1);
        let reply = reply(&request, 10);
        broker.register_request(request.clone()).unwrap();
        assert_eq!(
            broker.accept_reply(reply.clone()).unwrap(),
            OracleReplyDisposition::Buffered {
                key: request.key(),
                apply_tick: 33,
            }
        );
        assert!(broker.advance_to(31).unwrap().applied.is_empty());
        assert!(broker.advance_to(32).unwrap().applied.is_empty());
        let outcome = broker.advance_to(33).unwrap();
        assert_eq!(outcome.applied.len(), 1);
        assert_eq!(outcome.applied[0].reply, reply.clone());
        assert_eq!(
            broker.accept_reply(reply).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { key: request.key() }
        );
    }

    #[test]
    fn missing_reply_times_out_without_blocking_tick() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        let request = request(active, 30, 1, 1);
        broker.register_request(request.clone()).unwrap();
        broker.advance_to(31).unwrap();
        broker.advance_to(32).unwrap();
        let outcome = broker.advance_to(33).unwrap();
        assert_eq!(outcome.timed_out.len(), 1);
        assert_eq!(broker.current_tick(), 33);
        assert_eq!(
            broker.accept_reply(reply(&request, 11)).unwrap(),
            OracleReplyDisposition::Dropped {
                key: request.key(),
                reason: OracleReplyDropReason::Late,
            }
        );
    }

    #[test]
    fn old_epoch_and_oracle_generation_are_stale_but_future_is_fatal() {
        let active = lineage(3, 2);
        let broker_request = request(active, 30, 1, 1);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        broker.register_request(broker_request).unwrap();

        let old_epoch = request(lineage(2, 99), 30, 1, 1);
        assert!(matches!(
            broker.accept_reply(reply(&old_epoch, 1)).unwrap(),
            OracleReplyDisposition::Dropped {
                reason: OracleReplyDropReason::StaleLineage,
                ..
            }
        ));
        let old_oracle = request(lineage(3, 1), 30, 1, 1);
        assert!(matches!(
            broker.accept_reply(reply(&old_oracle, 2)).unwrap(),
            OracleReplyDisposition::Dropped {
                reason: OracleReplyDropReason::StaleLineage,
                ..
            }
        ));
        let future = request(lineage(3, 3), 30, 1, 1);
        assert!(matches!(
            broker.accept_reply(reply(&future, 3)).unwrap_err(),
            OracleBrokerError::FutureReplyLineage { .. }
        ));
    }

    #[test]
    fn newer_query_generation_invalidates_the_whole_older_batch() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        let old = request(active, 30, 1, 1);
        broker.register_request(old.clone()).unwrap();
        broker.advance_to(31).unwrap();
        let newer = request(active, 31, 2, 2);
        let registered = broker.register_request(newer).unwrap();
        assert_eq!(registered.invalidated_batches, 1);
        assert_eq!(
            broker.accept_reply(reply(&old, 4)).unwrap(),
            OracleReplyDisposition::Dropped {
                key: old.key(),
                reason: OracleReplyDropReason::SupersededQueryGeneration,
            }
        );
    }

    #[test]
    fn exact_duplicate_is_idempotent_but_conflicting_duplicate_is_fatal() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        let request = request(active, 30, 1, 1);
        let accepted = reply(&request, 7);
        broker.register_request(request.clone()).unwrap();
        broker.accept_reply(accepted.clone()).unwrap();
        assert!(matches!(
            broker.accept_reply(accepted.clone()).unwrap(),
            OracleReplyDisposition::DuplicateIgnored { .. }
        ));
        let mut conflict = accepted;
        conflict.results[0].status = OracleV1ResultStatus::Unavailable {
            code: "native_unavailable".to_owned(),
            message: "synthetic conflict".to_owned(),
        };
        assert_eq!(
            broker.accept_reply(conflict).unwrap_err(),
            OracleBrokerError::ConflictingDuplicate { key: request.key() }
        );
    }

    #[test]
    fn non_finite_request_and_incomplete_reply_are_fatal_validation_errors() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        let mut invalid = request(active, 30, 1, 1);
        invalid.queries[0].operation = OracleOperation::GroundSample {
            position: Vec3 {
                x: f32::NAN,
                y: 0.0,
                z: 0.0,
            },
        };
        assert!(matches!(
            broker.register_request(invalid).unwrap_err(),
            OracleBrokerError::Validation(OracleV1ValidationError::NonFiniteNumber { .. })
        ));

        let valid = request(active, 30, 1, 1);
        broker.register_request(valid.clone()).unwrap();
        let mut incomplete = reply(&valid, 8);
        incomplete.results.clear();
        assert!(matches!(
            broker.accept_reply(incomplete).unwrap_err(),
            OracleBrokerError::Validation(OracleV1ValidationError::MissingResult { .. })
        ));
    }

    #[test]
    fn broker_rejects_tick_skips() {
        let active = lineage(3, 2);
        let mut broker = OracleBroker::new(active, 30).unwrap();
        assert_eq!(
            broker.advance_to(32).unwrap_err(),
            OracleBrokerError::SkippedTick {
                current_tick: 30,
                received_tick: 32,
            }
        );
    }
}
