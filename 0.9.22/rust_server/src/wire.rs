//! External LAN protocol-v5 framing and handshake primitives.
//!
//! The battle protocol is newline-delimited JSON.  This module deliberately
//! validates only the transport envelope and the initial hello/welcome
//! identity.  Battle message schemas remain the responsibility of the state
//! modules that consume [`WireObject`].

use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde_json::{Map, Value};
use thiserror::Error;

pub const LAN_PROTOCOL_VERSION: u64 = 5;
pub const MAX_LINE_BYTES: usize = 256 * 1024;
pub const SIMULATION_WORKER_ROLE: &str = "simulation_worker";

pub type ConnectionId = u64;

#[derive(Debug, Error)]
pub enum WireError {
    #[error("LAN frame is {actual} bytes; the limit is {limit}")]
    LineTooLong { limit: usize, actual: usize },
    #[error("LAN frame is not valid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("LAN frame must be a JSON object")]
    NonObject,
    #[error("LAN frame is missing a non-empty string type")]
    MissingType,
    #[error("connection must start with a hello message, got {actual}")]
    ExpectedHello { actual: String },
    #[error("expected a welcome message, got {actual}")]
    ExpectedWelcome { actual: String },
    #[error("LAN protocol mismatch: expected {expected}, got {actual}")]
    ProtocolMismatch { expected: u64, actual: String },
    #[error("unsupported LAN connection role: {0}")]
    UnsupportedRole(String),
    #[error("welcome is missing a valid {field}")]
    MissingEndpointId { field: &'static str },
    #[error("connection handshake has already consumed its first message")]
    HandshakeAlreadyConsumed,
    #[error("connection closed with {buffered} bytes of an incomplete frame")]
    TruncatedFrame { buffered: usize },
    #[error("global receive sequence is exhausted")]
    ReceiveSequenceExhausted,
}

/// A JSON object with a validated, non-empty string `type` field.
#[derive(Clone, Debug, PartialEq)]
pub struct WireObject {
    fields: Map<String, Value>,
}

impl WireObject {
    pub fn new(kind: impl Into<String>) -> Result<Self, WireError> {
        Self::with_fields(kind, Map::new())
    }

    pub fn with_fields(
        kind: impl Into<String>,
        mut fields: Map<String, Value>,
    ) -> Result<Self, WireError> {
        let kind = kind.into();
        if kind.is_empty() {
            return Err(WireError::MissingType);
        }
        fields.insert("type".to_owned(), Value::String(kind));
        Ok(Self { fields })
    }

    pub fn kind(&self) -> &str {
        // Construction and decoding both enforce this invariant.
        self.fields
            .get("type")
            .and_then(Value::as_str)
            .expect("WireObject type invariant")
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.fields.get(key)
    }

    pub fn fields(&self) -> &Map<String, Value> {
        &self.fields
    }

    pub fn into_fields(self) -> Map<String, Value> {
        self.fields
    }

    pub fn into_value(self) -> Value {
        Value::Object(self.fields)
    }

    pub fn protocol(&self) -> Option<u64> {
        self.get("protocol").and_then(exact_u64)
    }
}

impl TryFrom<Value> for WireObject {
    type Error = WireError;

    fn try_from(value: Value) -> Result<Self, Self::Error> {
        let fields = value.as_object().ok_or(WireError::NonObject)?.clone();
        match fields.get("type") {
            Some(Value::String(kind)) if !kind.is_empty() => Ok(Self { fields }),
            _ => Err(WireError::MissingType),
        }
    }
}

impl From<WireObject> for Value {
    fn from(value: WireObject) -> Self {
        value.into_value()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConnectionRole {
    Player,
    SimulationWorker,
}

impl ConnectionRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Player => "player",
            Self::SimulationWorker => SIMULATION_WORKER_ROLE,
        }
    }

    fn from_optional_value(value: Option<&Value>) -> Result<Self, WireError> {
        match value {
            None => Ok(Self::Player),
            Some(Value::String(value)) if value == "player" => Ok(Self::Player),
            // The launcher probe never joins the room. The server recognizes
            // this raw role before player admission; treating it as a
            // transport-level player keeps Welcome's endpoint identity type
            // limited to real players and the hidden oracle.
            Some(Value::String(value)) if value == "probe" => Ok(Self::Player),
            Some(Value::String(value)) if value == SIMULATION_WORKER_ROLE => {
                Ok(Self::SimulationWorker)
            }
            Some(value) => Err(WireError::UnsupportedRole(display_json(value))),
        }
    }
}

/// The validated first message received from a player or simulation worker.
#[derive(Clone, Debug, PartialEq)]
pub struct Hello {
    role: ConnectionRole,
    object: WireObject,
}

impl Hello {
    pub fn role(&self) -> ConnectionRole {
        self.role
    }

    pub fn object(&self) -> &WireObject {
        &self.object
    }

    pub fn into_object(self) -> WireObject {
        self.object
    }
}

impl TryFrom<WireObject> for Hello {
    type Error = WireError;

    fn try_from(object: WireObject) -> Result<Self, Self::Error> {
        if object.kind() != "hello" {
            return Err(WireError::ExpectedHello {
                actual: object.kind().to_owned(),
            });
        }
        // Capability-aware admission happens after the transport has parsed
        // the role-specific hello.  Keep the version marker bounded and
        // positive here, but do not reject a capability-complete peer merely
        // because its diagnostic protocol label moved past v5.
        require_positive_protocol(&object)?;
        let role = ConnectionRole::from_optional_value(object.get("role"))?;
        Ok(Self { role, object })
    }
}

/// The transport-level identity carried by a protocol-v5 welcome.
///
/// Map, lobby, capability, and battle fields are intentionally retained in
/// `object` without being interpreted here so their migration can proceed
/// independently.
#[derive(Clone, Debug, PartialEq)]
pub struct Welcome {
    role: ConnectionRole,
    endpoint_id: i64,
    object: WireObject,
}

impl Welcome {
    pub fn new(
        role: ConnectionRole,
        endpoint_id: i64,
        mut fields: Map<String, Value>,
    ) -> Result<Self, WireError> {
        fields.insert(
            "protocol".to_owned(),
            Value::Number(LAN_PROTOCOL_VERSION.into()),
        );
        match role {
            ConnectionRole::Player => {
                fields.remove("role");
                fields.insert("player_id".to_owned(), Value::Number(endpoint_id.into()));
            }
            ConnectionRole::SimulationWorker => {
                fields.insert(
                    "role".to_owned(),
                    Value::String(SIMULATION_WORKER_ROLE.to_owned()),
                );
                fields.insert("worker_id".to_owned(), Value::Number(endpoint_id.into()));
            }
        }
        let object = WireObject::with_fields("welcome", fields)?;
        Ok(Self {
            role,
            endpoint_id,
            object,
        })
    }

    pub fn role(&self) -> ConnectionRole {
        self.role
    }

    pub fn endpoint_id(&self) -> i64 {
        self.endpoint_id
    }

    pub fn object(&self) -> &WireObject {
        &self.object
    }

    pub fn into_object(self) -> WireObject {
        self.object
    }
}

impl TryFrom<WireObject> for Welcome {
    type Error = WireError;

    fn try_from(object: WireObject) -> Result<Self, Self::Error> {
        if object.kind() != "welcome" {
            return Err(WireError::ExpectedWelcome {
                actual: object.kind().to_owned(),
            });
        }
        require_protocol(&object)?;
        let role = ConnectionRole::from_optional_value(object.get("role"))?;
        let id_field = match role {
            ConnectionRole::Player => "player_id",
            ConnectionRole::SimulationWorker => "worker_id",
        };
        let endpoint_id = object
            .get(id_field)
            .and_then(exact_i64)
            .ok_or(WireError::MissingEndpointId { field: id_field })?;
        Ok(Self {
            role,
            endpoint_id,
            object,
        })
    }
}

/// Ensures exactly the first decoded object is interpreted as a hello.
#[derive(Debug, Default)]
pub struct HandshakeGate {
    consumed: bool,
}

impl HandshakeGate {
    pub fn accept_first(&mut self, object: WireObject) -> Result<Hello, WireError> {
        if self.consumed {
            return Err(WireError::HandshakeAlreadyConsumed);
        }
        self.consumed = true;
        Hello::try_from(object)
    }

    pub fn is_consumed(&self) -> bool {
        self.consumed
    }
}

/// Incrementally decodes newline-delimited JSON without buffering an
/// attacker-controlled oversized line.
#[derive(Debug)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    max_line_bytes: usize,
}

impl Default for FrameDecoder {
    fn default() -> Self {
        Self::new()
    }
}

impl FrameDecoder {
    pub fn new() -> Self {
        Self::with_max_line_bytes(MAX_LINE_BYTES)
    }

    pub fn with_max_line_bytes(max_line_bytes: usize) -> Self {
        Self {
            buffer: Vec::new(),
            max_line_bytes,
        }
    }

    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<WireObject>, WireError> {
        let mut decoded = Vec::new();
        let mut offset = 0;
        while offset < chunk.len() {
            let remaining = &chunk[offset..];
            if let Some(newline) = remaining.iter().position(|byte| *byte == b'\n') {
                let segment = &remaining[..newline];
                self.extend_checked(segment)?;
                if !self.buffer.iter().all(u8::is_ascii_whitespace) {
                    decoded.push(decode_object(&self.buffer)?);
                }
                self.buffer.clear();
                offset += newline + 1;
            } else {
                self.extend_checked(remaining)?;
                break;
            }
        }
        Ok(decoded)
    }

    pub fn finish(&mut self) -> Result<(), WireError> {
        if self.buffer.iter().all(u8::is_ascii_whitespace) {
            self.buffer.clear();
            return Ok(());
        }
        let buffered = self.buffer.len();
        self.buffer.clear();
        Err(WireError::TruncatedFrame { buffered })
    }

    pub fn buffered_len(&self) -> usize {
        self.buffer.len()
    }

    fn extend_checked(&mut self, bytes: &[u8]) -> Result<(), WireError> {
        let actual = self
            .buffer
            .len()
            .checked_add(bytes.len())
            .unwrap_or(usize::MAX);
        if actual > self.max_line_bytes {
            self.buffer.clear();
            return Err(WireError::LineTooLong {
                limit: self.max_line_bytes,
                actual,
            });
        }
        self.buffer.extend_from_slice(bytes);
        Ok(())
    }
}

pub fn decode_object(line: &[u8]) -> Result<WireObject, WireError> {
    WireObject::try_from(serde_json::from_slice::<Value>(line)?)
}

/// Encodes one compact JSON frame including its newline terminator.
///
/// The encoded-size check matches the Python server's outbound contract: the
/// newline itself is part of the 256 KiB limit.
pub fn encode_line(object: &WireObject) -> Result<Vec<u8>, WireError> {
    encode_line_with_limit(object, MAX_LINE_BYTES)
}

pub(crate) fn encode_line_with_limit(
    object: &WireObject,
    max_line_bytes: usize,
) -> Result<Vec<u8>, WireError> {
    let mut encoded = serde_json::to_vec(object.fields())?;
    let actual = encoded.len().checked_add(1).unwrap_or(usize::MAX);
    if actual > max_line_bytes {
        return Err(WireError::LineTooLong {
            limit: max_line_bytes,
            actual,
        });
    }
    encoded.push(b'\n');
    Ok(encoded)
}

/// Process-wide monotonically increasing receive order.
///
/// Clone this value into each connection task.  The sequence is internal
/// scheduling metadata and is not added to the protocol-v5 JSON object.
#[derive(Clone, Debug)]
pub struct RecvSequencer {
    next: Arc<AtomicU64>,
}

impl Default for RecvSequencer {
    fn default() -> Self {
        Self::new()
    }
}

impl RecvSequencer {
    pub fn new() -> Self {
        Self::with_first(1)
    }

    pub fn with_first(first: u64) -> Self {
        Self {
            next: Arc::new(AtomicU64::new(first)),
        }
    }

    pub fn assign(
        &self,
        connection_id: ConnectionId,
        message: WireObject,
    ) -> Result<ReceivedEnvelope, WireError> {
        let recv_seq = self
            .next
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                current.checked_add(1)
            })
            .map_err(|_| WireError::ReceiveSequenceExhausted)?;
        Ok(ReceivedEnvelope {
            recv_seq,
            connection_id,
            message,
        })
    }

    pub fn next_sequence(&self) -> u64 {
        self.next.load(Ordering::SeqCst)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReceivedEnvelope {
    pub recv_seq: u64,
    pub connection_id: ConnectionId,
    pub message: WireObject,
}

fn require_protocol(object: &WireObject) -> Result<(), WireError> {
    if object.protocol() == Some(LAN_PROTOCOL_VERSION) {
        return Ok(());
    }
    Err(WireError::ProtocolMismatch {
        expected: LAN_PROTOCOL_VERSION,
        actual: object
            .get("protocol")
            .map(display_json)
            .unwrap_or_else(|| "missing".to_owned()),
    })
}

fn require_positive_protocol(object: &WireObject) -> Result<(), WireError> {
    if object.protocol().is_some_and(|protocol| protocol > 0) {
        return Ok(());
    }
    Err(WireError::ProtocolMismatch {
        expected: LAN_PROTOCOL_VERSION,
        actual: object
            .get("protocol")
            .map(display_json)
            .unwrap_or_else(|| "missing".to_owned()),
    })
}

fn exact_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number.as_u64(),
        // The Python server used int(value), so retain compatibility with the
        // plausible legacy spelling while rejecting fractional numbers.
        Value::String(value) => value.parse().ok(),
        _ => None,
    }
}

fn exact_i64(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number.as_i64(),
        Value::String(value) => value.parse().ok(),
        _ => None,
    }
}

fn display_json(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "<invalid>".to_owned())
}

impl fmt::Display for ConnectionRole {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn object(value: Value) -> WireObject {
        WireObject::try_from(value).unwrap()
    }

    #[test]
    fn decoder_handles_fragmented_and_multiple_frames() {
        let mut decoder = FrameDecoder::new();
        assert!(decoder.push(br#"{"type":"hel"#).unwrap().is_empty());
        let messages = decoder
            .push(b"lo\",\"protocol\":5}\n\n {\"type\":\"ping\",\"seq\":2}\r\n")
            .unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].kind(), "hello");
        assert_eq!(messages[1].kind(), "ping");
        assert_eq!(messages[1].get("seq"), Some(&json!(2)));
        assert_eq!(decoder.buffered_len(), 0);
    }

    #[test]
    fn decoder_rejects_non_objects_and_missing_types() {
        let mut decoder = FrameDecoder::new();
        assert!(matches!(decoder.push(b"[]\n"), Err(WireError::NonObject)));

        let mut decoder = FrameDecoder::new();
        assert!(matches!(
            decoder.push(b"{\"protocol\":5}\n"),
            Err(WireError::MissingType)
        ));

        let mut decoder = FrameDecoder::new();
        assert!(matches!(
            decoder.push(b"{\"type\":7}\n"),
            Err(WireError::MissingType)
        ));
    }

    #[test]
    fn decoder_rejects_an_oversized_partial_line_before_buffering_it() {
        let mut decoder = FrameDecoder::with_max_line_bytes(8);
        let error = decoder.push(b"123456789").unwrap_err();
        assert!(matches!(
            error,
            WireError::LineTooLong {
                limit: 8,
                actual: 9
            }
        ));
        assert_eq!(decoder.buffered_len(), 0);
    }

    #[test]
    fn encoder_includes_newline_in_the_size_limit() {
        let message = object(json!({"type": "ping"}));
        let encoded = serde_json::to_vec(message.fields()).unwrap();
        assert!(encode_line_with_limit(&message, encoded.len()).is_err());
        let frame = encode_line_with_limit(&message, encoded.len() + 1).unwrap();
        assert_eq!(frame.last(), Some(&b'\n'));
    }

    #[test]
    fn handshake_defaults_legacy_hello_to_player_and_is_single_use() {
        let mut gate = HandshakeGate::default();
        let hello = gate
            .accept_first(object(json!({"type": "hello", "protocol": 5})))
            .unwrap();
        assert_eq!(hello.role(), ConnectionRole::Player);
        assert!(gate.is_consumed());
        assert!(matches!(
            gate.accept_first(object(json!({"type": "hello", "protocol": 5}))),
            Err(WireError::HandshakeAlreadyConsumed)
        ));
    }

    #[test]
    fn handshake_accepts_worker_and_defers_positive_protocol_compatibility() {
        let worker = Hello::try_from(object(json!({
            "type": "hello",
            "protocol": 5,
            "role": "simulation_worker"
        })))
        .unwrap();
        assert_eq!(worker.role(), ConnectionRole::SimulationWorker);

        assert!(matches!(
            Hello::try_from(object(json!({"type": "ping", "protocol": 5}))),
            Err(WireError::ExpectedHello { .. })
        ));
        assert!(Hello::try_from(object(json!({"type": "hello", "protocol": 4}))).is_ok());
        for protocol in [json!(0), json!(-1), json!(true), json!(null)] {
            assert!(matches!(
                Hello::try_from(object(json!({"type": "hello", "protocol": protocol}))),
                Err(WireError::ProtocolMismatch { .. })
            ));
        }
    }

    #[test]
    fn welcome_round_trips_player_and_worker_transport_identity() {
        let player = Welcome::new(ConnectionRole::Player, 7, Map::new()).unwrap();
        assert_eq!(player.object().kind(), "welcome");
        assert_eq!(player.object().get("protocol"), Some(&json!(5)));
        assert_eq!(player.object().get("player_id"), Some(&json!(7)));
        assert!(player.object().get("role").is_none());
        assert_eq!(
            Welcome::try_from(player.into_object()).unwrap().role(),
            ConnectionRole::Player
        );

        let worker = Welcome::new(ConnectionRole::SimulationWorker, -1, Map::new()).unwrap();
        let parsed = Welcome::try_from(worker.into_object()).unwrap();
        assert_eq!(parsed.role(), ConnectionRole::SimulationWorker);
        assert_eq!(parsed.endpoint_id(), -1);
    }

    #[test]
    fn cloned_receive_sequencers_share_one_process_wide_order() {
        let first_connection = RecvSequencer::new();
        let second_connection = first_connection.clone();
        let first = first_connection
            .assign(10, object(json!({"type": "hello", "protocol": 5})))
            .unwrap();
        let second = second_connection
            .assign(20, object(json!({"type": "hello", "protocol": 5})))
            .unwrap();
        assert_eq!((first.recv_seq, first.connection_id), (1, 10));
        assert_eq!((second.recv_seq, second.connection_id), (2, 20));
        assert!(first.message.get("recv_seq").is_none());
        assert_eq!(first_connection.next_sequence(), 3);
    }

    #[test]
    fn receive_sequence_is_unique_and_contiguous_across_connection_threads() {
        let sequencer = RecvSequencer::new();
        let handles: Vec<_> = (0..4)
            .map(|connection_id| {
                let sequencer = sequencer.clone();
                std::thread::spawn(move || {
                    (0..100)
                        .map(|_| {
                            sequencer
                                .assign(connection_id, WireObject::new("ping").unwrap())
                                .unwrap()
                                .recv_seq
                        })
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        let mut sequences: Vec<_> = handles
            .into_iter()
            .flat_map(|handle| handle.join().unwrap())
            .collect();
        sequences.sort_unstable();
        assert_eq!(sequences, (1..=400).collect::<Vec<_>>());
        assert_eq!(sequencer.next_sequence(), 401);
    }

    #[test]
    fn finish_rejects_a_non_whitespace_partial_frame() {
        let mut decoder = FrameDecoder::new();
        decoder.push(br#"{"type":"ping"}"#).unwrap();
        assert!(matches!(
            decoder.finish(),
            Err(WireError::TruncatedFrame { buffered: 15 })
        ));
    }
}
