use crate::protocol::{OracleMessage, SimulationScope, Tick};
use crate::validator::{OracleValidator, ValidationError, ValidationOutcome};
use serde::{Deserialize, Serialize};
use std::io::{self, BufRead};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TraceDirection {
    ServerToOracle,
    OracleToServer,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TraceRecord {
    pub direction: TraceDirection,
    pub monotonic_ns: u64,
    pub message: OracleMessage,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum TraceValidationError {
    #[error("trace monotonic time regressed from {previous_ns} to {received_ns}")]
    MonotonicTimeRegression { previous_ns: u64, received_ns: u64 },
    #[error("{direction:?} is incompatible with {message_type}")]
    DirectionMismatch {
        direction: TraceDirection,
        message_type: &'static str,
    },
    #[error(transparent)]
    Oracle(#[from] ValidationError),
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct TraceSummary {
    pub records: u64,
    pub requests: u64,
    pub replies: u64,
    pub active_scope: Option<SimulationScope>,
    pub last_tick: Option<Tick>,
    pub pending_batches: usize,
}

#[derive(Default)]
pub struct TraceValidator {
    oracle: OracleValidator,
    last_monotonic_ns: Option<u64>,
    records: u64,
    requests: u64,
    replies: u64,
}

impl TraceValidator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn validate_record(
        &mut self,
        record: TraceRecord,
    ) -> Result<ValidationOutcome, TraceValidationError> {
        if let Some(previous_ns) = self.last_monotonic_ns {
            if record.monotonic_ns < previous_ns {
                return Err(TraceValidationError::MonotonicTimeRegression {
                    previous_ns,
                    received_ns: record.monotonic_ns,
                });
            }
        }

        match (&record.direction, &record.message) {
            (TraceDirection::ServerToOracle, OracleMessage::BatchRequest(_)) => {}
            (TraceDirection::OracleToServer, OracleMessage::BatchReply(_)) => {}
            (direction, OracleMessage::BatchRequest(_)) => {
                return Err(TraceValidationError::DirectionMismatch {
                    direction: *direction,
                    message_type: "batch_request",
                });
            }
            (direction, OracleMessage::BatchReply(_)) => {
                return Err(TraceValidationError::DirectionMismatch {
                    direction: *direction,
                    message_type: "batch_reply",
                });
            }
        }

        let is_request = matches!(&record.message, OracleMessage::BatchRequest(_));
        let outcome = self.oracle.process(record.message)?;
        self.last_monotonic_ns = Some(record.monotonic_ns);
        self.records += 1;
        if is_request {
            self.requests += 1;
        } else {
            self.replies += 1;
        }
        Ok(outcome)
    }

    pub fn summary(&self) -> TraceSummary {
        TraceSummary {
            records: self.records,
            requests: self.requests,
            replies: self.replies,
            active_scope: self.oracle.active_scope(),
            last_tick: self.oracle.last_tick(),
            pending_batches: self.oracle.pending_batches(),
        }
    }
}

#[derive(Debug, Error)]
pub enum ReplayError {
    #[error("failed to read trace: {0}")]
    Io(#[from] io::Error),
    #[error("line {line}: invalid JSON: {source}")]
    Json {
        line: usize,
        source: serde_json::Error,
    },
    #[error("line {line}: {source}")]
    Validation {
        line: usize,
        source: TraceValidationError,
    },
}

pub fn validate_reader<R: BufRead>(reader: R) -> Result<TraceSummary, ReplayError> {
    let mut validator = TraceValidator::new();
    for (index, line) in reader.lines().enumerate() {
        let line_number = index + 1;
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let record: TraceRecord =
            serde_json::from_str(&line).map_err(|source| ReplayError::Json {
                line: line_number,
                source,
            })?;
        validator
            .validate_record(record)
            .map_err(|source| ReplayError::Validation {
                line: line_number,
                source,
            })?;
    }
    Ok(validator.summary())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{
        BatchReply, BatchRequest, EntityRef, OracleOperation, OracleQuery, OracleResult,
        QueryOutcome, Vec3, ORACLE_PROTOCOL_VERSION,
    };
    use std::io::Cursor;

    fn request_message() -> OracleMessage {
        OracleMessage::BatchRequest(BatchRequest {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: 1,
            epoch: 0,
            tick: 1,
            batch_id: 1,
            queries: vec![OracleQuery {
                query_id: 1,
                entity: EntityRef {
                    entity_id: 42,
                    generation: 1,
                },
                operation: OracleOperation::GroundSample {
                    position: Vec3 {
                        x: 0.0,
                        y: 0.0,
                        z: 0.0,
                    },
                },
            }],
        })
    }

    fn reply_message() -> OracleMessage {
        OracleMessage::BatchReply(BatchReply {
            protocol_version: ORACLE_PROTOCOL_VERSION,
            round_id: 1,
            epoch: 0,
            tick: 1,
            batch_id: 1,
            results: vec![OracleResult {
                query_id: 1,
                entity: EntityRef {
                    entity_id: 42,
                    generation: 1,
                },
                outcome: QueryOutcome::GroundSample { sample: None },
            }],
        })
    }

    fn record(direction: TraceDirection, monotonic_ns: u64, message: OracleMessage) -> TraceRecord {
        TraceRecord {
            direction,
            monotonic_ns,
            message,
        }
    }

    #[test]
    fn validates_a_jsonl_request_reply_stream() {
        let request = serde_json::to_string(&record(
            TraceDirection::ServerToOracle,
            100,
            request_message(),
        ))
        .unwrap();
        let reply = serde_json::to_string(&record(
            TraceDirection::OracleToServer,
            200,
            reply_message(),
        ))
        .unwrap();
        let input = format!("{request}\n\n{reply}\n");
        let summary = validate_reader(Cursor::new(input)).unwrap();
        assert_eq!(
            summary,
            TraceSummary {
                records: 2,
                requests: 1,
                replies: 1,
                active_scope: Some(SimulationScope {
                    round_id: 1,
                    epoch: 0,
                }),
                last_tick: Some(1),
                pending_batches: 0,
            }
        );
    }

    #[test]
    fn rejects_direction_mismatch_without_advancing_summary() {
        let mut validator = TraceValidator::new();
        let error = validator
            .validate_record(record(
                TraceDirection::OracleToServer,
                100,
                request_message(),
            ))
            .unwrap_err();
        assert!(matches!(
            error,
            TraceValidationError::DirectionMismatch { .. }
        ));
        assert_eq!(validator.summary().records, 0);
    }

    #[test]
    fn rejects_monotonic_time_regression() {
        let mut validator = TraceValidator::new();
        validator
            .validate_record(record(
                TraceDirection::ServerToOracle,
                200,
                request_message(),
            ))
            .unwrap();
        let error = validator
            .validate_record(record(TraceDirection::OracleToServer, 199, reply_message()))
            .unwrap_err();
        assert_eq!(
            error,
            TraceValidationError::MonotonicTimeRegression {
                previous_ns: 200,
                received_ns: 199,
            }
        );
    }

    #[test]
    fn reports_the_jsonl_line_for_protocol_errors() {
        let request = serde_json::to_string(&record(
            TraceDirection::ServerToOracle,
            100,
            request_message(),
        ))
        .unwrap();
        let duplicate = serde_json::to_string(&record(
            TraceDirection::ServerToOracle,
            200,
            request_message(),
        ))
        .unwrap();
        let error = validate_reader(Cursor::new(format!("{request}\n{duplicate}\n"))).unwrap_err();
        assert!(matches!(error, ReplayError::Validation { line: 2, .. }));
    }
}
