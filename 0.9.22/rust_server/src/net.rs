//! Pure in-memory outbound scheduling for LAN connections.
//!
//! Socket ownership and async wakeups live above this module.  Keeping the
//! queue policy independent makes the slow-peer, coalescing, and lifecycle
//! fence rules deterministic and directly testable.

use std::collections::VecDeque;

use serde_json::Value;
use thiserror::Error;

use crate::wire::{encode_line, encode_line_with_limit, WireError, WireObject};

pub const MAX_RELIABLE_OUTBOUND_MESSAGES: usize = 64;
pub const MAX_RELIABLE_OUTBOUND_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeliveryClass {
    Reliable,
    Snapshot,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OutboxLimits {
    pub max_reliable_messages: usize,
    pub max_reliable_bytes: usize,
}

impl Default for OutboxLimits {
    fn default() -> Self {
        Self {
            max_reliable_messages: MAX_RELIABLE_OUTBOUND_MESSAGES,
            max_reliable_bytes: MAX_RELIABLE_OUTBOUND_BYTES,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct OutboundFrame {
    delivery: DeliveryClass,
    message: WireObject,
    encoded: Vec<u8>,
}

impl OutboundFrame {
    fn new(delivery: DeliveryClass, message: WireObject) -> Result<Self, WireError> {
        let encoded = encode_line(&message)?;
        Ok(Self {
            delivery,
            message,
            encoded,
        })
    }

    fn new_with_limit(
        delivery: DeliveryClass,
        message: WireObject,
        max_line_bytes: usize,
    ) -> Result<Self, WireError> {
        let encoded = encode_line_with_limit(&message, max_line_bytes)?;
        Ok(Self {
            delivery,
            message,
            encoded,
        })
    }

    pub fn delivery(&self) -> DeliveryClass {
        self.delivery
    }

    pub fn message(&self) -> &WireObject {
        &self.message
    }

    pub fn encoded(&self) -> &[u8] {
        &self.encoded
    }

    pub fn encoded_len(&self) -> usize {
        self.encoded.len()
    }

    pub fn into_parts(self) -> (WireObject, Vec<u8>) {
        (self.message, self.encoded)
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReliableOffer {
    /// A pending replaceable snapshot was discarded by this state barrier.
    pub fenced_snapshot: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SnapshotOffer {
    /// A previously pending (not currently writing) snapshot was replaced.
    pub replaced_snapshot: bool,
}

#[derive(Debug, Error)]
pub enum OutboxError {
    #[error("outbox is closed")]
    Closed,
    #[error(transparent)]
    Wire(#[from] WireError),
    #[error(
        "reliable outbox overflow: attempted {attempted_messages} messages/{attempted_bytes} bytes, limit {max_messages} messages/{max_bytes} bytes"
    )]
    ReliableOverflow {
        attempted_messages: usize,
        attempted_bytes: usize,
        max_messages: usize,
        max_bytes: usize,
    },
}

/// Reliable FIFO plus one replaceable snapshot slot.
///
/// Reliable overflow is terminal and clears the queue, matching the Python
/// endpoint behavior that disconnects a peer which cannot consume durable
/// state quickly enough.  Snapshot replacement never consumes reliable
/// capacity.
#[derive(Debug)]
pub struct BoundedOutbox {
    limits: OutboxLimits,
    reliable: VecDeque<OutboundFrame>,
    reliable_bytes: usize,
    snapshot: Option<OutboundFrame>,
    closed: bool,
}

impl Default for BoundedOutbox {
    fn default() -> Self {
        Self::new()
    }
}

impl BoundedOutbox {
    pub fn new() -> Self {
        Self::with_limits(OutboxLimits::default())
    }

    pub fn with_limits(limits: OutboxLimits) -> Self {
        Self {
            limits,
            reliable: VecDeque::new(),
            reliable_bytes: 0,
            snapshot: None,
            closed: false,
        }
    }

    pub fn offer_reliable(&mut self, message: WireObject) -> Result<ReliableOffer, OutboxError> {
        self.require_open()?;
        let frame = OutboundFrame::new(DeliveryClass::Reliable, message)?;
        self.offer_reliable_frame(frame, self.limits.max_reliable_bytes)
    }

    /// Queue one explicitly bounded large response without relaxing normal
    /// protocol framing or inbound limits. Used only by launcher overlay data.
    pub fn offer_large_reliable(
        &mut self,
        message: WireObject,
        max_line_bytes: usize,
        max_reliable_bytes: usize,
    ) -> Result<ReliableOffer, OutboxError> {
        self.require_open()?;
        let frame =
            OutboundFrame::new_with_limit(DeliveryClass::Reliable, message, max_line_bytes)?;
        self.offer_reliable_frame(frame, max_reliable_bytes)
    }

    fn offer_reliable_frame(
        &mut self,
        frame: OutboundFrame,
        max_reliable_bytes: usize,
    ) -> Result<ReliableOffer, OutboxError> {
        let fenced_snapshot = self
            .snapshot
            .as_ref()
            .is_some_and(|snapshot| reliable_fences_snapshot(frame.message(), snapshot.message()));
        if fenced_snapshot {
            self.snapshot = None;
        }

        let attempted_messages = self.reliable.len().saturating_add(1);
        let attempted_bytes = self
            .reliable_bytes
            .checked_add(frame.encoded_len())
            .unwrap_or(usize::MAX);
        if attempted_messages > self.limits.max_reliable_messages
            || attempted_bytes > max_reliable_bytes
        {
            let error = OutboxError::ReliableOverflow {
                attempted_messages,
                attempted_bytes,
                max_messages: self.limits.max_reliable_messages,
                max_bytes: max_reliable_bytes,
            };
            self.close();
            return Err(error);
        }

        self.reliable_bytes = attempted_bytes;
        self.reliable.push_back(frame);
        Ok(ReliableOffer { fenced_snapshot })
    }

    pub fn offer_snapshot(&mut self, message: WireObject) -> Result<SnapshotOffer, OutboxError> {
        self.require_open()?;
        let frame = OutboundFrame::new(DeliveryClass::Snapshot, message)?;
        let replaced_snapshot = self.snapshot.replace(frame).is_some();
        Ok(SnapshotOffer { replaced_snapshot })
    }

    /// Returns reliable frames first, even if the snapshot was offered first.
    pub fn pop_next(&mut self) -> Option<OutboundFrame> {
        if self.closed {
            return None;
        }
        if let Some(frame) = self.reliable.pop_front() {
            self.reliable_bytes -= frame.encoded_len();
            return Some(frame);
        }
        self.snapshot.take()
    }

    pub fn close(&mut self) {
        self.closed = true;
        self.reliable.clear();
        self.reliable_bytes = 0;
        self.snapshot = None;
    }

    pub fn is_closed(&self) -> bool {
        self.closed
    }

    pub fn is_empty(&self) -> bool {
        self.reliable.is_empty() && self.snapshot.is_none()
    }

    pub fn reliable_len(&self) -> usize {
        self.reliable.len()
    }

    pub fn reliable_bytes(&self) -> usize {
        self.reliable_bytes
    }

    pub fn has_snapshot(&self) -> bool {
        self.snapshot.is_some()
    }

    pub fn limits(&self) -> OutboxLimits {
        self.limits
    }

    fn require_open(&self) -> Result<(), OutboxError> {
        if self.closed {
            Err(OutboxError::Closed)
        } else {
            Ok(())
        }
    }
}

/// Whether a durable state message invalidates one older unsent snapshot.
///
/// This mirrors `_EndpointSendMixin._reliable_fences_snapshot` in the Python
/// LAN server.  Unknown reliable message types do not fence snapshots.
pub fn reliable_fences_snapshot(message: &WireObject, snapshot: &WireObject) -> bool {
    if !matches!(
        message.kind(),
        "battle_live" | "battle_start" | "events" | "roster" | "snapshot"
    ) {
        return false;
    }

    let Some(message_round) = non_null(message.get("round_id")) else {
        return false;
    };
    let Some(snapshot_round) = non_null(snapshot.get("round_id")) else {
        return false;
    };
    if message_round != snapshot_round {
        return true;
    }

    if matches!(message.kind(), "battle_live" | "battle_start" | "roster") {
        return true;
    }

    let message_tick = non_null(message.get("server_tick"));
    let snapshot_tick = non_null(snapshot.get("server_tick"));
    if message.kind() == "snapshot" && message_tick.is_none() {
        return true;
    }
    if let (Some(message_tick), Some(snapshot_tick)) = (message_tick, snapshot_tick) {
        let Ok(message_tick) = python_int(message_tick) else {
            return true;
        };
        let Ok(snapshot_tick) = python_int(snapshot_tick) else {
            return true;
        };
        if message_tick >= snapshot_tick {
            return true;
        }
    }

    let message_epoch = non_null(message.get("authority_epoch"));
    let snapshot_epoch = non_null(snapshot.get("authority_epoch"));
    if let (Some(message_epoch), Some(snapshot_epoch)) = (message_epoch, snapshot_epoch) {
        let Ok(message_epoch) = python_int(message_epoch) else {
            return true;
        };
        let Ok(snapshot_epoch) = python_int(snapshot_epoch) else {
            return true;
        };
        return message_epoch > snapshot_epoch;
    }
    false
}

fn non_null(value: Option<&Value>) -> Option<&Value> {
    value.filter(|value| !value.is_null())
}

/// The fence source uses Python's `int(value)`.  Internal server messages use
/// JSON integers, but accepting booleans and truncating JSON numbers here
/// preserves its conservative malformed-message behavior.
fn python_int(value: &Value) -> Result<i128, ()> {
    match value {
        Value::Bool(value) => Ok(i128::from(*value)),
        Value::Number(number) => {
            if let Some(value) = number.as_i64() {
                return Ok(i128::from(value));
            }
            if let Some(value) = number.as_u64() {
                return Ok(i128::from(value));
            }
            let value = number.as_f64().ok_or(())?;
            if !value.is_finite() || value < i128::MIN as f64 || value > i128::MAX as f64 {
                return Err(());
            }
            Ok(value.trunc() as i128)
        }
        Value::String(value) => value.parse().map_err(|_| ()),
        _ => Err(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::WireObject;
    use serde_json::{json, Value};

    fn object(value: Value) -> WireObject {
        WireObject::try_from(value).unwrap()
    }

    fn snapshot(round_id: i64, tick: i64) -> WireObject {
        object(json!({
            "type": "snapshot",
            "protocol": 5,
            "round_id": round_id,
            "server_tick": tick,
            "authority_epoch": 1
        }))
    }

    #[test]
    fn reliable_fifo_precedes_and_does_not_replace_latest_snapshot() {
        let mut outbox = BoundedOutbox::new();
        assert!(
            !outbox
                .offer_snapshot(snapshot(1, 1))
                .unwrap()
                .replaced_snapshot
        );
        assert!(
            outbox
                .offer_snapshot(snapshot(1, 2))
                .unwrap()
                .replaced_snapshot
        );
        outbox
            .offer_reliable(object(json!({
                "type": "pong",
                "seq": 1
            })))
            .unwrap();
        outbox
            .offer_reliable(object(json!({
                "type": "pong",
                "seq": 2
            })))
            .unwrap();

        let first = outbox.pop_next().unwrap();
        let second = outbox.pop_next().unwrap();
        let third = outbox.pop_next().unwrap();
        assert_eq!(first.delivery(), DeliveryClass::Reliable);
        assert_eq!(first.message().get("seq"), Some(&json!(1)));
        assert_eq!(second.message().get("seq"), Some(&json!(2)));
        assert_eq!(third.delivery(), DeliveryClass::Snapshot);
        assert_eq!(third.message().get("server_tick"), Some(&json!(2)));
        assert!(outbox.is_empty());
    }

    #[test]
    fn lifecycle_barrier_fences_an_unsent_same_round_snapshot() {
        let mut outbox = BoundedOutbox::new();
        outbox.offer_snapshot(snapshot(1, 2)).unwrap();
        let offered = outbox
            .offer_reliable(object(json!({
                "type": "roster",
                "round_id": 1,
                "players": []
            })))
            .unwrap();
        assert!(offered.fenced_snapshot);
        assert!(!outbox.has_snapshot());
        assert_eq!(outbox.pop_next().unwrap().message().kind(), "roster");
        assert!(outbox.pop_next().is_none());
    }

    #[test]
    fn slow_peer_sequence_matches_python_coalescing_contract() {
        let mut outbox = BoundedOutbox::new();
        outbox.offer_snapshot(snapshot(1, 1)).unwrap();

        // The writer has already claimed tick 1, so it is no longer
        // replaceable while producers continue to publish.
        let writing = outbox.pop_next().unwrap();
        outbox.offer_snapshot(snapshot(1, 2)).unwrap();
        outbox.offer_snapshot(snapshot(1, 3)).unwrap();
        let events = outbox
            .offer_reliable(object(json!({
                "type": "events",
                "round_id": 1,
                "server_tick": 4,
                "events": []
            })))
            .unwrap();
        assert!(events.fenced_snapshot);
        outbox.offer_snapshot(snapshot(1, 4)).unwrap();

        let reliable = outbox.pop_next().unwrap();
        let latest = outbox.pop_next().unwrap();
        assert_eq!(
            [
                (
                    writing.message().kind(),
                    writing.message().get("server_tick")
                ),
                (
                    reliable.message().kind(),
                    reliable.message().get("server_tick")
                ),
                (latest.message().kind(), latest.message().get("server_tick")),
            ],
            [
                ("snapshot", Some(&json!(1))),
                ("events", Some(&json!(4))),
                ("snapshot", Some(&json!(4))),
            ]
        );
    }

    #[test]
    fn tick_and_epoch_fences_match_python_policy() {
        let old_snapshot = snapshot(7, 10);
        assert!(reliable_fences_snapshot(
            &object(json!({"type": "events", "round_id": 8, "server_tick": 1})),
            &old_snapshot
        ));
        assert!(reliable_fences_snapshot(
            &object(json!({"type": "events", "round_id": 7, "server_tick": 10})),
            &old_snapshot
        ));
        assert!(!reliable_fences_snapshot(
            &object(json!({"type": "events", "round_id": 7, "server_tick": 9})),
            &old_snapshot
        ));
        assert!(reliable_fences_snapshot(
            &object(json!({
                "type": "events",
                "round_id": 7,
                "server_tick": 9,
                "authority_epoch": 2
            })),
            &old_snapshot
        ));
        assert!(!reliable_fences_snapshot(
            &object(json!({"type": "pong", "round_id": 8, "server_tick": 99})),
            &old_snapshot
        ));
    }

    #[test]
    fn reliable_snapshot_without_tick_is_a_barrier() {
        assert!(reliable_fences_snapshot(
            &object(json!({"type": "snapshot", "round_id": 1})),
            &snapshot(1, 12)
        ));
    }

    #[test]
    fn reliable_limits_accept_exact_capacity_then_close_on_overflow() {
        let sample = object(json!({"type": "pong", "seq": 1}));
        let frame_bytes = encode_line(&sample).unwrap().len();
        let mut outbox = BoundedOutbox::with_limits(OutboxLimits {
            max_reliable_messages: 2,
            max_reliable_bytes: frame_bytes * 2,
        });
        outbox.offer_reliable(sample.clone()).unwrap();
        outbox.offer_reliable(sample.clone()).unwrap();
        assert_eq!(outbox.reliable_len(), 2);
        assert_eq!(outbox.reliable_bytes(), frame_bytes * 2);

        assert!(matches!(
            outbox.offer_reliable(sample),
            Err(OutboxError::ReliableOverflow {
                attempted_messages: 3,
                ..
            })
        ));
        assert!(outbox.is_closed());
        assert!(outbox.is_empty());
        assert!(outbox.pop_next().is_none());
        assert!(matches!(
            outbox.offer_snapshot(snapshot(1, 1)),
            Err(OutboxError::Closed)
        ));
    }

    #[test]
    fn replaceable_snapshot_does_not_consume_reliable_capacity() {
        let mut outbox = BoundedOutbox::with_limits(OutboxLimits {
            max_reliable_messages: 0,
            max_reliable_bytes: 0,
        });
        outbox.offer_snapshot(snapshot(1, 1)).unwrap();
        outbox.offer_snapshot(snapshot(1, 2)).unwrap();
        assert_eq!(outbox.reliable_len(), 0);
        assert_eq!(outbox.reliable_bytes(), 0);
        assert!(outbox.has_snapshot());
    }

    #[test]
    fn oversized_message_is_rejected_without_closing_outbox() {
        let mut outbox = BoundedOutbox::new();
        let oversized = object(json!({
            "type": "events",
            "payload": "x".repeat(crate::wire::MAX_LINE_BYTES)
        }));
        assert!(matches!(
            outbox.offer_reliable(oversized),
            Err(OutboxError::Wire(WireError::LineTooLong { .. }))
        ));
        assert!(!outbox.is_closed());
        assert!(outbox.is_empty());
    }
}
