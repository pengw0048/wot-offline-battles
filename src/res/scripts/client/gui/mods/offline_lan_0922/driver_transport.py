"""Bounded, single-connection loopback transport for the hidden player driver.

Only the IO thread touches the wire; ``poll`` transfers complete plain dicts to
the caller. ``send`` synchronously binary-encodes once to freeze caller-owned
data. That cost belongs to the calling thread, not the IO thread. Round and
generation fencing, readiness and physical ownership belong to the consumer.

Ordinary messages are ordered barriers. A nonempty ``replace_key`` explicitly
permits a newer state to replace an unsent/unpolled state with the same key,
only after the last barrier. A partly written frame is never replaced. Queue
overflow fails this bridge visibly; it never drops a reliable message silently
or operates the room connection. There is no reconnect or authority fallback.
"""

import struct

from gui.mods.offline_lan_0922 import driver_codec
import select
import socket
import threading
import time


SCHEMA = 2
MAX_FRAME_BYTES = 1024 * 1024
MAX_QUEUED_MESSAGES = 128
MAX_QUEUED_BYTES = 8 * 1024 * 1024
IO_WAIT_SECONDS = 0.005
HANDSHAKE_SECONDS = 5.0
STOP_JOIN_SECONDS = 0.5
_CLOCK = getattr(time, 'monotonic', time.time)
try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


def _encode(value):
    body = driver_codec.encode(value)
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError('driver frame is too large')
    return struct.pack('!I', len(body)) + body


def _decode(body):
    return driver_codec.decode(body)


def _frame(buffer):
    if len(buffer) < 4:
        return None
    length = struct.unpack('!I', buffer[:4])[0]
    if not 5 <= length <= MAX_FRAME_BYTES:
        raise ValueError('invalid driver frame length')
    if len(buffer) < 4 + length:
        return None
    return buffer[4:4 + length], buffer[4 + length:]


def _valid_key(value):
    return (value is None or
            (isinstance(value, _STRING_TYPES) and 0 < len(value) <= 128))


def _replace_index(queue, key, active=None):
    if key is not None:
        for index in range(len(queue) - 1, -1, -1):
            row = queue[index]
            if row[0] is None:
                break
            if row[0] == key and row is not active:
                return index
    return None


class Bridge(object):
    """Use ``listen``/``connect``; inspect ``error`` after a rejected send.

    ``connected`` becomes true only after token/schema acknowledgement.
    ``listening`` and ``port`` expose listener startup without game callbacks.
    ``error`` is a stable reason code and never includes payloads or the token.
    Messages queued before EOF remain available to ``poll``; the caller must
    apply its lifecycle fences. ``stop`` is harmless after partial startup.
    """

    def __init__(self, host, port, token, listener):
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._connected = False
        self._listening = False
        self._error = None
        self._socket = None
        self._listener = None
        self._thread = None
        self._port = port
        self._outgoing = []
        self._incoming = []
        self._outgoing_bytes = 0
        self._incoming_bytes = 0
        self._sending = None
        if (host != '127.0.0.1' or type(port) is not int or
                not 1 <= port <= 65535 or
                not isinstance(token, _STRING_TYPES) or
                not 1 <= len(token) <= 256):
            self._fail('invalid_endpoint')
            return
        try:
            driver_codec.check_available()
        except (ImportError, AttributeError, RuntimeError):
            self._fail('codec_unavailable')
            return
        try:
            if listener:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._listener = sock
                sock.bind((host, port))
                sock.listen(1)
                sock.settimeout(IO_WAIT_SECONDS)
                self._listening = True
            thread = threading.Thread(
                target=self._run, args=(host, port, token, listener),
                name='WoTPlayerDriverIO')
            thread.daemon = True
            self._thread = thread
            thread.start()
        except (socket.error, RuntimeError):
            self._fail('listen_failed' if listener else 'start_failed')

    @property
    def connected(self):
        with self._lock:
            return self._connected

    @property
    def listening(self):
        with self._lock:
            return self._listening

    @property
    def port(self):
        return self._port

    @property
    def error(self):
        with self._lock:
            return self._error

    def _close_sockets(self):
        with self._lock:
            sockets = (self._socket, self._listener)
            self._socket = self._listener = None
            self._connected = self._listening = False
        for sock in sockets:
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except socket.error:
                    pass
                try:
                    sock.close()
                except socket.error:
                    pass

    def _fail(self, reason):
        with self._lock:
            if self._error is None and not self._stopped.is_set():
                self._error = reason
            self._stopped.set()
        self._close_sockets()

    def stop(self):
        self._stopped.set()
        self._close_sockets()
        thread = self._thread
        if (thread is not None and thread.is_alive() and
                thread is not threading.current_thread()):
            thread.join(STOP_JOIN_SECONDS)

    def send(self, message, replace_key=None):
        """Freeze and admit one message, or return false with no admission.

        A false result before connection is ready is retryable by the owner.
        Invalid data or overflow instead sets ``error`` and closes this bridge.
        An admitted frame can still fail in transit; the owner observes that
        through ``error`` and must terminate the corresponding operation.
        """
        if not self.connected:
            return False
        if not isinstance(message, dict) or not _valid_key(replace_key):
            self._fail('invalid_message')
            return False
        try:
            frame = _encode({'schema': SCHEMA, 'kind': 'message',
                             'replace_key': replace_key, 'payload': message})
        except (TypeError, ValueError, OverflowError, RuntimeError,
                ImportError, AttributeError):
            self._fail('invalid_message')
            return False
        if len(frame) - 4 > MAX_FRAME_BYTES:
            self._fail('frame_too_large')
            return False
        with self._lock:
            if not self._connected or self._stopped.is_set():
                return False
            index = _replace_index(
                self._outgoing, replace_key, self._sending)
            removed_bytes = (len(self._outgoing[index][1])
                             if index is not None else 0)
            count = len(self._outgoing) + (0 if index is not None else 1)
            size = self._outgoing_bytes - removed_bytes + len(frame)
            overflow = (count > MAX_QUEUED_MESSAGES or
                        size > MAX_QUEUED_BYTES)
            if not overflow:
                if index is not None:
                    self._outgoing.pop(index)
                self._outgoing.append((replace_key, frame))
                self._outgoing_bytes = size
                return True
        self._fail('outbound_overflow')
        return False

    def poll_records(self):
        """Transfer payloads with their original frame byte counts."""
        with self._lock:
            rows = [(row[1], row[2]) for row in self._incoming]
            self._incoming = []
            self._incoming_bytes = 0
        return rows

    def poll(self):
        """Compatibility projection for consumers without a byte budget."""
        return [row[0] for row in self.poll_records()]

    def _admit_incoming(self, envelope, frame_bytes):
        if (not isinstance(envelope, dict) or
                set(envelope) != set(('schema', 'kind', 'replace_key', 'payload')) or
                type(envelope.get('schema')) is not int or
                envelope['schema'] != SCHEMA or
                envelope.get('kind') != 'message' or
                not isinstance(envelope.get('payload'), dict) or
                not _valid_key(envelope.get('replace_key'))):
            self._fail('invalid_frame')
            return False
        key = envelope['replace_key']
        with self._lock:
            if self._stopped.is_set():
                return False
            index = _replace_index(self._incoming, key)
            removed_bytes = (self._incoming[index][2]
                             if index is not None else 0)
            count = len(self._incoming) + (0 if index is not None else 1)
            size = self._incoming_bytes - removed_bytes + frame_bytes
            overflow = (count > MAX_QUEUED_MESSAGES or
                        size > MAX_QUEUED_BYTES)
            if not overflow:
                if index is not None:
                    self._incoming.pop(index)
                self._incoming.append((key, envelope['payload'], frame_bytes))
                self._incoming_bytes = size
                return True
        self._fail('inbound_overflow')
        return False

    def _handshake_write(self, sock, message, deadline):
        frame = _encode(message)
        offset = 0
        while not self._stopped.is_set() and _CLOCK() < deadline:
            try:
                sent = sock.send(frame[offset:])
            except socket.timeout:
                continue
            if sent <= 0:
                raise socket.error('closed')
            offset += sent
            if offset == len(frame):
                return True
        return False

    def _handshake_read(self, sock, deadline):
        buffer = b''
        while not self._stopped.is_set() and _CLOCK() < deadline:
            try:
                block = sock.recv(65536)
            except socket.timeout:
                continue
            if not block:
                raise socket.error('closed')
            buffer += block
            frame = _frame(buffer)
            if frame is not None:
                body, remainder = frame
                return _decode(body), remainder
        return None, b''

    def _handshake(self, sock, token, listener):
        deadline = _CLOCK() + HANDSHAKE_SECONDS
        if not listener and not self._handshake_write(
                sock, {'schema': SCHEMA, 'kind': 'hello', 'token': token}, deadline):
            self._fail('handshake_timeout')
            return None
        message, remainder = self._handshake_read(sock, deadline)
        if message is None:
            self._fail('handshake_timeout')
            return None
        expected = ({'schema': SCHEMA, 'kind': 'hello', 'token': token}
                    if listener else {'schema': SCHEMA, 'kind': 'ready'})
        if (message != expected or not isinstance(message, dict) or
                type(message.get('schema')) is not int):
            self._fail('authentication_failed' if listener else 'invalid_handshake')
            return None
        if listener and not self._handshake_write(
                sock, {'schema': SCHEMA, 'kind': 'ready'}, deadline):
            self._fail('handshake_timeout')
            return None
        with self._lock:
            if self._stopped.is_set():
                return None
            self._connected = True
        return remainder

    def _read_frames(self, buffer):
        while True:
            try:
                frame = _frame(buffer)
                if frame is None:
                    return buffer
                body, buffer = frame
                envelope = _decode(body)
            except (TypeError, ValueError, OverflowError, RuntimeError,
                    ImportError, AttributeError):
                self._fail('invalid_frame')
                return None
            if not self._admit_incoming(envelope, len(body) + 4):
                return None

    def _transfer(self, sock, buffer):
        offset = 0
        while not self._stopped.is_set():
            buffer = self._read_frames(buffer)
            if buffer is None:
                return
            with self._lock:
                if self._sending is None and self._outgoing:
                    self._sending = self._outgoing[0]
                row = self._sending
            readable, writable, unused = select.select(
                [sock], [sock] if row is not None else [], [], IO_WAIT_SECONDS)
            if writable:
                try:
                    sent = sock.send(row[1][offset:])
                except socket.timeout:
                    sent = None
                if sent is not None:
                    if sent <= 0:
                        self._fail('peer_closed')
                        return
                    offset += sent
                    if offset == len(row[1]):
                        with self._lock:
                            self._outgoing.pop(0)
                            self._outgoing_bytes -= len(row[1])
                            self._sending = None
                        offset = 0
            if readable:
                try:
                    block = sock.recv(65536)
                except socket.timeout:
                    continue
                if not block:
                    self._fail('truncated_frame' if buffer else 'peer_closed')
                    return
                buffer += block

    def _run(self, host, port, token, listener):
        try:
            if listener:
                server = self._listener
                while not self._stopped.is_set():
                    try:
                        sock, unused_address = server.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                with self._lock:
                    self._listener = None
                    self._listening = False
                server.close()
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self._lock:
                if self._stopped.is_set():
                    sock.close()
                    return
                self._socket = sock
            if not listener:
                sock.settimeout(HANDSHAKE_SECONDS)
                try:
                    sock.connect((host, port))
                except socket.error:
                    self._fail('connect_failed')
                    return
            sock.settimeout(IO_WAIT_SECONDS)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            buffer = self._handshake(sock, token, listener)
            if buffer is not None:
                self._transfer(sock, buffer)
        except (socket.error, select.error, ValueError, TypeError,
                OverflowError, RuntimeError, ImportError, AttributeError):
            self._fail('io_failed')
        finally:
            self._close_sockets()


def listen(host, port, token):
    """Bind a loopback listener now and authenticate one peer on its IO thread."""
    return Bridge(host, port, token, True)


def connect(host, port, token):
    """Connect and authenticate asynchronously; no retry or reconnect."""
    return Bridge(host, port, token, False)
