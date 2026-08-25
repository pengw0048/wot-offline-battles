//! Threaded TCP transport for the external LAN protocol.
//!
//! This layer owns sockets, framing, connection lifecycle, and outbound
//! backpressure.  It does not dispatch game commands or mutate room state.

use std::fmt;
use std::io::{self, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvError, RecvTimeoutError, TryRecvError};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::net::{BoundedOutbox, OutboxError, ReliableOffer, SnapshotOffer};
use crate::wire::{
    ConnectionId, FrameDecoder, HandshakeGate, Hello, ReceivedEnvelope, RecvSequencer, WireObject,
};

pub const HELLO_TIMEOUT: Duration = Duration::from_secs(10);
pub const OUTBOUND_STALL_TIMEOUT: Duration = Duration::from_secs(5);

const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);
const IO_POLL_INTERVAL: Duration = Duration::from_millis(100);
const READ_BUFFER_BYTES: usize = 16 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DisconnectReason {
    PeerClosed,
    ServerShutdown,
    HelloTimeout,
    InvalidHello(String),
    InvalidFrame(String),
    ReadError(String),
    WriteError(String),
    WriteStalled,
    OutboxOverflow,
    ApplicationClosed,
    EventReceiverDropped,
    TransportSetup(String),
    WriterPanicked,
}

#[derive(Clone, Debug)]
pub enum TransportEvent {
    Connected {
        connection_id: ConnectionId,
        peer_addr: SocketAddr,
        hello: Hello,
        sender: SendHandle,
    },
    Message {
        envelope: ReceivedEnvelope,
    },
    Disconnected {
        connection_id: ConnectionId,
        reason: DisconnectReason,
    },
}

/// Cloneable producer for one connection's reliable FIFO and snapshot slot.
#[derive(Clone)]
pub struct SendHandle {
    connection_id: ConnectionId,
    peer_addr: SocketAddr,
    shared: Arc<SharedConnection>,
}

impl SendHandle {
    pub fn connection_id(&self) -> ConnectionId {
        self.connection_id
    }

    pub fn peer_addr(&self) -> SocketAddr {
        self.peer_addr
    }

    pub fn offer_reliable(&self, message: WireObject) -> Result<ReliableOffer, OutboxError> {
        let mut state = lock_unpoisoned(&self.shared.state);
        if self.shared.global_shutdown.load(Ordering::Acquire) {
            state.mark_closed(DisconnectReason::ServerShutdown);
            drop(state);
            self.shared.wake.notify_all();
            let _ = self.shared.control.shutdown(Shutdown::Both);
            return Err(OutboxError::Closed);
        }
        let result = state.outbox.offer_reliable(message);
        let overflowed = matches!(&result, Err(OutboxError::ReliableOverflow { .. }));
        if overflowed {
            state.mark_closed(DisconnectReason::OutboxOverflow);
        }
        let queued = result.is_ok();
        drop(state);

        if queued {
            self.shared.wake.notify_one();
        } else if overflowed {
            self.shared.wake.notify_all();
            let _ = self.shared.control.shutdown(Shutdown::Both);
        }
        result
    }

    pub fn offer_large_reliable(
        &self,
        message: WireObject,
        max_line_bytes: usize,
        max_reliable_bytes: usize,
    ) -> Result<ReliableOffer, OutboxError> {
        let mut state = lock_unpoisoned(&self.shared.state);
        if self.shared.global_shutdown.load(Ordering::Acquire) {
            state.mark_closed(DisconnectReason::ServerShutdown);
            drop(state);
            self.shared.wake.notify_all();
            let _ = self.shared.control.shutdown(Shutdown::Both);
            return Err(OutboxError::Closed);
        }
        let result = state
            .outbox
            .offer_large_reliable(message, max_line_bytes, max_reliable_bytes);
        let overflowed = matches!(&result, Err(OutboxError::ReliableOverflow { .. }));
        if overflowed {
            state.mark_closed(DisconnectReason::OutboxOverflow);
        }
        let queued = result.is_ok();
        drop(state);

        if queued {
            self.shared.wake.notify_one();
        } else if overflowed {
            self.shared.wake.notify_all();
            let _ = self.shared.control.shutdown(Shutdown::Both);
        }
        result
    }

    pub fn offer_snapshot(&self, message: WireObject) -> Result<SnapshotOffer, OutboxError> {
        let mut state = lock_unpoisoned(&self.shared.state);
        if self.shared.global_shutdown.load(Ordering::Acquire) {
            state.mark_closed(DisconnectReason::ServerShutdown);
            drop(state);
            self.shared.wake.notify_all();
            let _ = self.shared.control.shutdown(Shutdown::Both);
            return Err(OutboxError::Closed);
        }
        let result = state.outbox.offer_snapshot(message);
        let queued = result.is_ok();
        drop(state);
        if queued {
            self.shared.wake.notify_one();
        }
        result
    }

    pub fn close(&self) {
        self.shared.close(DisconnectReason::ApplicationClosed);
    }

    pub fn is_closed(&self) -> bool {
        self.shared.global_shutdown.load(Ordering::Acquire)
            || lock_unpoisoned(&self.shared.state).outbox.is_closed()
    }
}

impl fmt::Debug for SendHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SendHandle")
            .field("connection_id", &self.connection_id)
            .field("peer_addr", &self.peer_addr)
            .field("closed", &self.is_closed())
            .finish()
    }
}

/// Running TCP listener and its ordered event receiver.
pub struct TransportServer {
    local_addr: SocketAddr,
    events: Receiver<TransportEvent>,
    shutdown: Arc<AtomicBool>,
    accept_thread: Option<JoinHandle<()>>,
}

impl TransportServer {
    pub fn bind<A: ToSocketAddrs>(address: A) -> io::Result<Self> {
        Self::bind_with_timings(address, TransportTimings::default())
    }

    fn bind_with_timings<A: ToSocketAddrs>(
        address: A,
        timings: TransportTimings,
    ) -> io::Result<Self> {
        let listener = TcpListener::bind(address)?;
        listener.set_nonblocking(true)?;
        let local_addr = listener.local_addr()?;
        let shutdown = Arc::new(AtomicBool::new(false));
        let (event_sender, events) = mpsc::channel();
        let dispatcher = Arc::new(OrderedEventDispatcher::new(event_sender));
        let thread_shutdown = Arc::clone(&shutdown);
        let accept_thread = thread::Builder::new()
            .name("lan-tcp-accept".to_owned())
            .spawn(move || accept_loop(listener, dispatcher, thread_shutdown, timings))?;

        Ok(Self {
            local_addr,
            events,
            shutdown,
            accept_thread: Some(accept_thread),
        })
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    pub fn recv(&self) -> Result<TransportEvent, RecvError> {
        self.events.recv()
    }

    pub fn recv_timeout(&self, timeout: Duration) -> Result<TransportEvent, RecvTimeoutError> {
        self.events.recv_timeout(timeout)
    }

    pub fn try_recv(&self) -> Result<TransportEvent, TryRecvError> {
        self.events.try_recv()
    }

    pub fn event_receiver(&self) -> &Receiver<TransportEvent> {
        &self.events
    }

    pub fn shutdown(&mut self) {
        self.shutdown.store(true, Ordering::Release);
        if let Some(thread) = self.accept_thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for TransportServer {
    fn drop(&mut self) {
        self.shutdown();
    }
}

impl fmt::Debug for TransportServer {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TransportServer")
            .field("local_addr", &self.local_addr)
            .field("shutdown", &self.shutdown.load(Ordering::Acquire))
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Copy, Debug)]
struct TransportTimings {
    hello_timeout: Duration,
    outbound_stall_timeout: Duration,
    accept_poll_interval: Duration,
    io_poll_interval: Duration,
}

impl Default for TransportTimings {
    fn default() -> Self {
        Self {
            hello_timeout: HELLO_TIMEOUT,
            outbound_stall_timeout: OUTBOUND_STALL_TIMEOUT,
            accept_poll_interval: ACCEPT_POLL_INTERVAL,
            io_poll_interval: IO_POLL_INTERVAL,
        }
    }
}

struct ConnectionState {
    outbox: BoundedOutbox,
    reason: Option<DisconnectReason>,
}

impl ConnectionState {
    fn new() -> Self {
        Self {
            outbox: BoundedOutbox::new(),
            reason: None,
        }
    }

    fn mark_closed(&mut self, reason: DisconnectReason) {
        if self.reason.is_none() {
            self.reason = Some(reason);
        }
        self.outbox.close();
    }
}

struct SharedConnection {
    state: Mutex<ConnectionState>,
    wake: Condvar,
    control: TcpStream,
    global_shutdown: Arc<AtomicBool>,
}

impl SharedConnection {
    fn new(control: TcpStream, global_shutdown: Arc<AtomicBool>) -> Self {
        Self {
            state: Mutex::new(ConnectionState::new()),
            wake: Condvar::new(),
            control,
            global_shutdown,
        }
    }

    fn close(&self, reason: DisconnectReason) {
        lock_unpoisoned(&self.state).mark_closed(reason);
        self.wake.notify_all();
        let _ = self.control.shutdown(Shutdown::Both);
    }

    fn reason(&self) -> Option<DisconnectReason> {
        lock_unpoisoned(&self.state).reason.clone()
    }

    fn is_closed(&self) -> bool {
        lock_unpoisoned(&self.state).outbox.is_closed()
    }

    fn wait_for_frame(&self, poll_interval: Duration) -> Option<crate::net::OutboundFrame> {
        let mut state = lock_unpoisoned(&self.state);
        loop {
            if self.global_shutdown.load(Ordering::Acquire) {
                state.mark_closed(DisconnectReason::ServerShutdown);
                return None;
            }
            if state.outbox.is_closed() {
                return None;
            }
            if let Some(frame) = state.outbox.pop_next() {
                return Some(frame);
            }
            state = match self.wake.wait_timeout(state, poll_interval) {
                Ok((state, _)) => state,
                Err(poisoned) => poisoned.into_inner().0,
            };
        }
    }
}

struct OrderedEventState {
    sender: mpsc::Sender<TransportEvent>,
    receive_sequence: RecvSequencer,
}

struct OrderedEventDispatcher {
    state: Mutex<OrderedEventState>,
}

impl OrderedEventDispatcher {
    fn new(sender: mpsc::Sender<TransportEvent>) -> Self {
        Self {
            state: Mutex::new(OrderedEventState {
                sender,
                receive_sequence: RecvSequencer::new(),
            }),
        }
    }

    fn emit(&self, event: TransportEvent) -> bool {
        // Every event uses the same mutex and Sender instance.  In particular,
        // assigning recv_seq and enqueueing Message happen in one critical
        // section, so cross-connection channel order cannot invert sequence.
        lock_unpoisoned(&self.state).sender.send(event).is_ok()
    }

    fn emit_message(&self, connection_id: ConnectionId, message: WireObject) -> bool {
        let state = lock_unpoisoned(&self.state);
        let Ok(envelope) = state.receive_sequence.assign(connection_id, message) else {
            return false;
        };
        state
            .sender
            .send(TransportEvent::Message { envelope })
            .is_ok()
    }
}

fn accept_loop(
    listener: TcpListener,
    dispatcher: Arc<OrderedEventDispatcher>,
    shutdown: Arc<AtomicBool>,
    timings: TransportTimings,
) {
    let mut next_connection_id: ConnectionId = 1;
    let mut connections: Vec<JoinHandle<()>> = Vec::new();

    while !shutdown.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, peer_addr)) => {
                let connection_id = next_connection_id;
                let Some(next_id) = next_connection_id.checked_add(1) else {
                    let _ = stream.shutdown(Shutdown::Both);
                    continue;
                };
                next_connection_id = next_id;
                if let Err(error) = stream.set_nodelay(true) {
                    let _ = dispatcher.emit(TransportEvent::Disconnected {
                        connection_id,
                        reason: DisconnectReason::TransportSetup(error.to_string()),
                    });
                    let _ = stream.shutdown(Shutdown::Both);
                    continue;
                }

                let connection_dispatcher = Arc::clone(&dispatcher);
                let connection_shutdown = Arc::clone(&shutdown);
                match thread::Builder::new()
                    .name(format!("lan-tcp-reader-{connection_id}"))
                    .spawn(move || {
                        connection_loop(
                            stream,
                            peer_addr,
                            connection_id,
                            connection_dispatcher,
                            connection_shutdown,
                            timings,
                        )
                    }) {
                    Ok(thread) => connections.push(thread),
                    Err(error) => {
                        let _ = dispatcher.emit(TransportEvent::Disconnected {
                            connection_id,
                            reason: DisconnectReason::TransportSetup(error.to_string()),
                        });
                    }
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                reap_finished(&mut connections);
                thread::sleep(timings.accept_poll_interval);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => {
                reap_finished(&mut connections);
                thread::sleep(timings.accept_poll_interval);
            }
        }
    }

    for connection in connections {
        let _ = connection.join();
    }
}

fn reap_finished(connections: &mut Vec<JoinHandle<()>>) {
    let mut index = 0;
    while index < connections.len() {
        if connections[index].is_finished() {
            let connection = connections.swap_remove(index);
            let _ = connection.join();
        } else {
            index += 1;
        }
    }
}

fn connection_loop(
    mut reader: TcpStream,
    peer_addr: SocketAddr,
    connection_id: ConnectionId,
    dispatcher: Arc<OrderedEventDispatcher>,
    global_shutdown: Arc<AtomicBool>,
    timings: TransportTimings,
) {
    let writer = match reader.try_clone() {
        Ok(writer) => writer,
        Err(error) => {
            let _ = dispatcher.emit(TransportEvent::Disconnected {
                connection_id,
                reason: DisconnectReason::TransportSetup(error.to_string()),
            });
            return;
        }
    };
    let control = match reader.try_clone() {
        Ok(control) => control,
        Err(error) => {
            let _ = dispatcher.emit(TransportEvent::Disconnected {
                connection_id,
                reason: DisconnectReason::TransportSetup(error.to_string()),
            });
            return;
        }
    };
    let shared = Arc::new(SharedConnection::new(control, global_shutdown));
    let writer_shared = Arc::clone(&shared);
    let writer_panic_shared = Arc::clone(&shared);
    let writer_thread = match thread::Builder::new()
        .name(format!("lan-tcp-writer-{connection_id}"))
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                writer_loop(writer, writer_shared, timings)
            }));
            if result.is_err() {
                writer_panic_shared.close(DisconnectReason::WriterPanicked);
            }
        }) {
        Ok(thread) => thread,
        Err(error) => {
            shared.close(DisconnectReason::TransportSetup(error.to_string()));
            let _ = dispatcher.emit(TransportEvent::Disconnected {
                connection_id,
                reason: shared.reason().unwrap_or_else(|| {
                    DisconnectReason::TransportSetup("writer did not start".to_owned())
                }),
            });
            return;
        }
    };

    let (hello, mut decoder, pending) = match read_hello(&mut reader, &shared, timings) {
        Ok(handshake) => handshake,
        Err(reason) => {
            shared.close(reason);
            finish_connection(connection_id, &dispatcher, &shared, writer_thread);
            return;
        }
    };
    let sender = SendHandle {
        connection_id,
        peer_addr,
        shared: Arc::clone(&shared),
    };
    if !dispatcher.emit(TransportEvent::Connected {
        connection_id,
        peer_addr,
        hello,
        sender,
    }) {
        shared.close(DisconnectReason::EventReceiverDropped);
        let _ = writer_thread.join();
        return;
    }

    for message in pending {
        if !dispatcher.emit_message(connection_id, message) {
            shared.close(DisconnectReason::EventReceiverDropped);
            finish_connection(connection_id, &dispatcher, &shared, writer_thread);
            return;
        }
    }

    let reason = read_messages(
        &mut reader,
        connection_id,
        &dispatcher,
        &shared,
        &mut decoder,
        timings,
    );
    shared.close(reason);
    finish_connection(connection_id, &dispatcher, &shared, writer_thread);
}

fn read_hello(
    stream: &mut TcpStream,
    shared: &SharedConnection,
    timings: TransportTimings,
) -> Result<(Hello, FrameDecoder, Vec<WireObject>), DisconnectReason> {
    let started = Instant::now();
    let mut decoder = FrameDecoder::new();
    let mut gate = HandshakeGate::default();
    let mut buffer = [0_u8; READ_BUFFER_BYTES];

    loop {
        if shared.global_shutdown.load(Ordering::Acquire) {
            return Err(DisconnectReason::ServerShutdown);
        }
        if shared.is_closed() {
            return Err(shared.reason().unwrap_or(DisconnectReason::PeerClosed));
        }
        let elapsed = started.elapsed();
        if elapsed >= timings.hello_timeout {
            return Err(DisconnectReason::HelloTimeout);
        }
        let read_timeout = std::cmp::min(timings.io_poll_interval, timings.hello_timeout - elapsed);
        stream
            .set_read_timeout(Some(read_timeout))
            .map_err(|error| DisconnectReason::TransportSetup(error.to_string()))?;
        match stream.read(&mut buffer) {
            Ok(0) => {
                return match decoder.finish() {
                    Ok(()) => Err(DisconnectReason::PeerClosed),
                    Err(error) => Err(DisconnectReason::InvalidHello(error.to_string())),
                };
            }
            Ok(count) => {
                let mut messages = decoder
                    .push(&buffer[..count])
                    .map_err(|error| DisconnectReason::InvalidHello(error.to_string()))?;
                if messages.is_empty() {
                    continue;
                }
                let first = messages.remove(0);
                let hello = gate
                    .accept_first(first)
                    .map_err(|error| DisconnectReason::InvalidHello(error.to_string()))?;
                return Ok((hello, decoder, messages));
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) => {}
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(DisconnectReason::ReadError(error.to_string())),
        }
    }
}

fn read_messages(
    stream: &mut TcpStream,
    connection_id: ConnectionId,
    dispatcher: &OrderedEventDispatcher,
    shared: &SharedConnection,
    decoder: &mut FrameDecoder,
    timings: TransportTimings,
) -> DisconnectReason {
    if let Err(error) = stream.set_read_timeout(Some(timings.io_poll_interval)) {
        return DisconnectReason::TransportSetup(error.to_string());
    }
    let mut buffer = [0_u8; READ_BUFFER_BYTES];
    loop {
        if shared.global_shutdown.load(Ordering::Acquire) {
            return DisconnectReason::ServerShutdown;
        }
        if shared.is_closed() {
            return shared.reason().unwrap_or(DisconnectReason::PeerClosed);
        }
        match stream.read(&mut buffer) {
            Ok(0) => {
                return match decoder.finish() {
                    Ok(()) => DisconnectReason::PeerClosed,
                    Err(error) => DisconnectReason::InvalidFrame(error.to_string()),
                };
            }
            Ok(count) => match decoder.push(&buffer[..count]) {
                Ok(messages) => {
                    for message in messages {
                        if !dispatcher.emit_message(connection_id, message) {
                            return DisconnectReason::EventReceiverDropped;
                        }
                    }
                }
                Err(error) => return DisconnectReason::InvalidFrame(error.to_string()),
            },
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) => {}
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => {
                return shared
                    .reason()
                    .unwrap_or_else(|| DisconnectReason::ReadError(error.to_string()));
            }
        }
    }
}

fn writer_loop(mut stream: TcpStream, shared: Arc<SharedConnection>, timings: TransportTimings) {
    if let Err(error) = stream.set_write_timeout(Some(timings.io_poll_interval)) {
        shared.close(DisconnectReason::TransportSetup(error.to_string()));
        return;
    }
    while let Some(frame) = shared.wait_for_frame(timings.io_poll_interval) {
        if let Err(reason) =
            write_all_with_stall(&mut stream, frame.encoded(), timings.outbound_stall_timeout)
        {
            shared.close(reason);
            return;
        }
    }
}

fn write_all_with_stall<W: Write>(
    writer: &mut W,
    payload: &[u8],
    stall_timeout: Duration,
) -> Result<(), DisconnectReason> {
    let mut offset = 0;
    let mut stalled_since = None;
    while offset < payload.len() {
        match writer.write(&payload[offset..]) {
            Ok(0) => {
                return Err(DisconnectReason::WriteError(
                    "peer closed during send".to_owned(),
                ));
            }
            Ok(count) if count <= payload.len() - offset => {
                offset += count;
                stalled_since = None;
            }
            Ok(_) => {
                return Err(DisconnectReason::WriteError(
                    "writer reported an invalid byte count".to_owned(),
                ));
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                let started = stalled_since.get_or_insert_with(Instant::now);
                if started.elapsed() >= stall_timeout {
                    return Err(DisconnectReason::WriteStalled);
                }
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(DisconnectReason::WriteError(error.to_string())),
        }
    }
    Ok(())
}

fn finish_connection(
    connection_id: ConnectionId,
    dispatcher: &OrderedEventDispatcher,
    shared: &SharedConnection,
    writer_thread: JoinHandle<()>,
) {
    if writer_thread.join().is_err() {
        shared.close(DisconnectReason::WriterPanicked);
    }
    let _ = dispatcher.emit(TransportEvent::Disconnected {
        connection_id,
        reason: shared.reason().unwrap_or(DisconnectReason::PeerClosed),
    });
}

fn lock_unpoisoned<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader};

    use serde_json::{json, Map, Value};

    use crate::wire::{ConnectionRole, Welcome};

    fn object(value: Value) -> WireObject {
        WireObject::try_from(value).unwrap()
    }

    fn test_timings() -> TransportTimings {
        TransportTimings {
            hello_timeout: Duration::from_millis(150),
            outbound_stall_timeout: Duration::from_millis(150),
            accept_poll_interval: Duration::from_millis(2),
            io_poll_interval: Duration::from_millis(10),
        }
    }

    #[test]
    fn loopback_accepts_fragmented_hello_and_moves_frames_both_directions() {
        let mut server = TransportServer::bind("127.0.0.1:0").unwrap();
        assert_ne!(server.local_addr().port(), 0);
        let mut client = TcpStream::connect(server.local_addr()).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        client.write_all(br#"{"type":"hel"#).unwrap();
        client
            .write_all(b"lo\",\"protocol\":5,\"name\":\"Human\"}\n{\"type\":\"ping\",\"seq\":7}\n")
            .unwrap();

        let (connection_id, sender) = match server.recv_timeout(Duration::from_secs(2)).unwrap() {
            TransportEvent::Connected {
                connection_id,
                hello,
                sender,
                ..
            } => {
                assert_eq!(hello.role(), crate::wire::ConnectionRole::Player);
                assert_eq!(hello.object().get("name"), Some(&json!("Human")));
                (connection_id, sender)
            }
            event => panic!("expected Connected, got {event:?}"),
        };

        let welcome = Welcome::new(ConnectionRole::Player, 1, Map::new())
            .unwrap()
            .into_object();
        sender.offer_reliable(welcome).unwrap();
        let mut response = String::new();
        BufReader::new(client.try_clone().unwrap())
            .read_line(&mut response)
            .unwrap();
        let response: Value = serde_json::from_str(&response).unwrap();
        assert_eq!(response["type"], "welcome");
        assert_eq!(response["protocol"], 5);

        match server.recv_timeout(Duration::from_secs(2)).unwrap() {
            TransportEvent::Message { envelope } => {
                assert_eq!(envelope.recv_seq, 1);
                assert_eq!(envelope.connection_id, connection_id);
                assert_eq!(envelope.message.kind(), "ping");
                assert_eq!(envelope.message.get("seq"), Some(&json!(7)));
            }
            event => panic!("expected Message, got {event:?}"),
        }

        client.shutdown(Shutdown::Both).unwrap();
        match server.recv_timeout(Duration::from_secs(2)).unwrap() {
            TransportEvent::Disconnected {
                connection_id: disconnected,
                ..
            } => assert_eq!(disconnected, connection_id),
            event => panic!("expected Disconnected, got {event:?}"),
        }
        server.shutdown();
    }

    #[test]
    fn concurrent_loopback_messages_arrive_in_global_sequence_order() {
        let mut server = TransportServer::bind("127.0.0.1:0").unwrap();
        let mut clients = Vec::new();
        for index in 0..2 {
            let mut client = TcpStream::connect(server.local_addr()).unwrap();
            writeln!(
                client,
                "{{\"type\":\"hello\",\"protocol\":5,\"name\":\"P{index}\"}}"
            )
            .unwrap();
            clients.push(client);
        }

        let mut connection_ids = Vec::new();
        while connection_ids.len() < 2 {
            match server.recv_timeout(Duration::from_secs(2)).unwrap() {
                TransportEvent::Connected { connection_id, .. } => {
                    connection_ids.push(connection_id)
                }
                event => panic!("expected Connected, got {event:?}"),
            }
        }

        let writers: Vec<_> = clients
            .into_iter()
            .enumerate()
            .map(|(client_index, mut client)| {
                thread::spawn(move || {
                    for message_index in 0..50 {
                        writeln!(
                            client,
                            "{{\"type\":\"input\",\"client\":{client_index},\"index\":{message_index}}}"
                        )
                        .unwrap();
                    }
                    client
                })
            })
            .collect();

        for expected_sequence in 1..=100 {
            loop {
                match server.recv_timeout(Duration::from_secs(2)).unwrap() {
                    TransportEvent::Message { envelope } => {
                        assert_eq!(envelope.recv_seq, expected_sequence);
                        assert!(connection_ids.contains(&envelope.connection_id));
                        break;
                    }
                    TransportEvent::Disconnected { reason, .. } => {
                        panic!("connection closed before all messages: {reason:?}")
                    }
                    TransportEvent::Connected { .. } => {
                        panic!("unexpected additional connection")
                    }
                }
            }
        }

        for writer in writers {
            let client = writer.join().unwrap();
            let _ = client.shutdown(Shutdown::Both);
        }
        server.shutdown();
    }

    #[test]
    fn loopback_rejects_a_non_hello_first_frame() {
        let mut server = TransportServer::bind("127.0.0.1:0").unwrap();
        let mut client = TcpStream::connect(server.local_addr()).unwrap();
        client.write_all(b"{\"type\":\"ping\"}\n").unwrap();

        match server.recv_timeout(Duration::from_secs(2)).unwrap() {
            TransportEvent::Disconnected {
                reason: DisconnectReason::InvalidHello(reason),
                ..
            } => assert!(reason.contains("start with a hello")),
            event => panic!("expected invalid-hello disconnect, got {event:?}"),
        }
        server.shutdown();
    }

    #[test]
    fn loopback_enforces_the_hello_deadline() {
        let mut server = TransportServer::bind_with_timings("127.0.0.1:0", test_timings()).unwrap();
        let _client = TcpStream::connect(server.local_addr()).unwrap();
        match server.recv_timeout(Duration::from_secs(1)).unwrap() {
            TransportEvent::Disconnected {
                reason: DisconnectReason::HelloTimeout,
                ..
            } => {}
            event => panic!("expected hello timeout, got {event:?}"),
        }
        server.shutdown();
    }

    struct ShortWriter {
        bytes: Vec<u8>,
        maximum_write: usize,
    }

    impl Write for ShortWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            let count = bytes.len().min(self.maximum_write);
            self.bytes.extend_from_slice(&bytes[..count]);
            Ok(count)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn short_write_loop_finishes_one_frame_without_duplication() {
        let mut writer = ShortWriter {
            bytes: Vec::new(),
            maximum_write: 3,
        };
        write_all_with_stall(&mut writer, b"one complete frame\n", Duration::from_secs(1)).unwrap();
        assert_eq!(writer.bytes, b"one complete frame\n");
    }

    struct TimedOutWriter;

    impl Write for TimedOutWriter {
        fn write(&mut self, _bytes: &[u8]) -> io::Result<usize> {
            Err(io::Error::new(io::ErrorKind::TimedOut, "blocked"))
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn stalled_write_is_terminal() {
        assert_eq!(
            write_all_with_stall(&mut TimedOutWriter, b"frame", Duration::ZERO),
            Err(DisconnectReason::WriteStalled)
        );
    }

    #[test]
    fn send_handle_wakes_writer_and_observes_close() {
        let mut server = TransportServer::bind("127.0.0.1:0").unwrap();
        let mut client = TcpStream::connect(server.local_addr()).unwrap();
        client
            .write_all(b"{\"type\":\"hello\",\"protocol\":5}\n")
            .unwrap();
        let sender = match server.recv_timeout(Duration::from_secs(2)).unwrap() {
            TransportEvent::Connected { sender, .. } => sender,
            event => panic!("expected Connected, got {event:?}"),
        };
        sender
            .offer_snapshot(object(json!({
                "type": "snapshot",
                "protocol": 5,
                "round_id": 1,
                "server_tick": 1
            })))
            .unwrap();

        let mut line = String::new();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        BufReader::new(client.try_clone().unwrap())
            .read_line(&mut line)
            .unwrap();
        assert_eq!(
            serde_json::from_str::<Value>(&line).unwrap()["server_tick"],
            1
        );

        sender.close();
        assert!(sender.is_closed());
        assert!(matches!(
            sender.offer_reliable(object(json!({"type": "pong"}))),
            Err(OutboxError::Closed)
        ));
        server.shutdown();
    }
}
