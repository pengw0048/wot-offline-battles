from __future__ import print_function

"""Bounded atomic transport for oversized server state (#1513 / LAN v5).

Physical frames retain the 256 KiB limit. A negotiated state message can be
split into ordered ASCII envelopes; the receiver releases the ORIGINAL message
only after exact length, scope and SHA-256 validation. No gameplay field is
removed, no partially received state enters a runtime, and no fragment crosses
a connection generation. TCP provides delivery/order; the existing native
battle_ready barrier still owns permission to enter the battle.
"""

import base64
import binascii
import hashlib
import json

CAPABILITY = 'state_transfer_v1'
FRAGMENT_TYPE = 'state_fragment'
MAX_FRAME_BYTES = 256 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
PART_BYTES = 128 * 1024
MAX_PARTS = (MAX_STATE_BYTES + PART_BYTES - 1) // PART_BYTES
TRANSFER_TIMEOUT = 15.0
STATE_TYPES = frozenset(('welcome', 'roster', 'battle_start', 'snapshot',
                         'battle_live'))
SCOPE_FIELDS = ('type', 'protocol', 'round_id', 'authority_epoch',
                'state_revision', 'bot_state_revision', 'map', 'phase')
FRAME_FIELDS = frozenset(('type', 'protocol', 'sha256', 'scope', 'size',
                          'parts', 'index', 'data'))
try:
    _TEXT = (basestring,)
    _INT = (int, long)
except NameError:
    _TEXT = (str,)
    _INT = (int,)


class TransferError(ValueError):
    def __init__(self, code):
        self.code = str(code)
        ValueError.__init__(self, 'LAN state transfer: ' + self.code)


def _integer(value, low, high):
    return (not isinstance(value, bool) and isinstance(value, _INT) and
            low <= value <= high)


def _scope(message):
    result = dict((key, message[key]) for key in SCOPE_FIELDS if key in message)
    if result.get('type') not in STATE_TYPES:
        raise TransferError('unsupported_message_type')
    if not _integer(result.get('protocol'), 1, 2147483647):
        raise TransferError('invalid_protocol')
    for key in SCOPE_FIELDS[2:6]:
        if key in result and not _integer(result[key], 0, 10000000000000000):
            raise TransferError('invalid_scope_' + key)
    for key in ('map', 'phase'):
        if key in result and (not isinstance(result[key], _TEXT) or
                              len(result[key]) > 256):
            raise TransferError('invalid_scope_' + key)
    return result


def json_payload(message):
    try:
        return (json.dumps(message, separators=(',', ':'),
                           ensure_ascii=True) + '\n').encode('utf-8')
    except (TypeError, ValueError, OverflowError, UnicodeError):
        raise TransferError('encode_error')


def frame_payload(message, payload, supported=False):
    """Return an indivisible outbox item comprising one or several lines.

    The caller reserves/charges ALL bytes before queueing; the writer holds
    its normal send lock for the whole item, so pings, another snapshot and a
    battle_live barrier cannot interleave between parts.
    """
    if len(payload) <= MAX_FRAME_BYTES:
        return payload
    if not supported:
        raise TransferError('upgrade_required')
    if len(payload) > MAX_STATE_BYTES:
        raise TransferError('state_size_limit')
    scope = _scope(message)
    digest = hashlib.sha256(payload).hexdigest()
    parts = (len(payload) + PART_BYTES - 1) // PART_BYTES
    lines = []
    for index in range(parts):
        part = payload[index * PART_BYTES:(index + 1) * PART_BYTES]
        line = json_payload(dict(
            type=FRAGMENT_TYPE, protocol=1, sha256=digest,
            scope=scope, size=len(payload), parts=parts, index=index,
            data=base64.b64encode(part).decode('ascii')))
        if len(line) > MAX_FRAME_BYTES:
            raise TransferError('frame_size_limit')
        lines.append(line)
    return b''.join(lines)


def can_frame(message):
    """Budget check for a mandatory state/manifest that cannot be omitted."""
    try:
        _scope(message)
        return len(json_payload(message)) <= MAX_STATE_BYTES
    except TransferError:
        return False


def _diagnostic(callback, stage, **fields):
    if callback is not None:
        try:
            callback(stage, fields)
        except Exception:
            # Diagnostics must never change validity or transport ownership.
            pass


class StateReceiver(object):
    """One bounded in-flight state on one ordered TCP connection."""
    def __init__(self, diagnostic=None):
        self.diagnostic = diagnostic
        self.clear()

    def clear(self):
        self.active = None
        self.data = []
        self.received = 0
        self.started = None

    def _fail(self, code):
        _diagnostic(self.diagnostic, 'reject', reason=code,
                    scope=self.active['scope'] if self.active else None,
                    received=self.received)
        self.clear()
        raise TransferError(code)

    def check_timeout(self, now):
        if self.active is not None and now - self.started >= TRANSFER_TIMEOUT:
            self._fail('incomplete_timeout')

    def finish(self):
        if self.active is not None:
            self._fail('incomplete_eof')

    def accept(self, message, now):
        self.check_timeout(now)
        if not isinstance(message, dict) or message.get('type') != FRAGMENT_TYPE:
            if self.active is not None:
                self._fail('interleaved_message')
            return message
        if (set(message) != FRAME_FIELDS or
                not _integer(message.get('protocol'), 1, 1)):
            self._fail('fragment_schema')
        if isinstance(message['protocol'], bool):
            self._fail('fragment_schema')
        size, parts, index = (message['size'], message['parts'], message['index'])
        if (not _integer(size, MAX_FRAME_BYTES + 1, MAX_STATE_BYTES) or
                not _integer(parts, 2, MAX_PARTS) or
                not _integer(index, 0, parts - 1) or
                parts != (size + PART_BYTES - 1) // PART_BYTES):
            self._fail('fragment_bounds')
        digest, scope = message['sha256'], message['scope']
        if (not isinstance(digest, _TEXT) or len(digest) != 64 or
                any(c not in '0123456789abcdef' for c in digest)):
            self._fail('fragment_digest')
        if not isinstance(scope, dict):
            self._fail('fragment_scope')
        try:
            if _scope(scope) != scope:
                self._fail('fragment_scope')
        except TransferError:
            self._fail('fragment_scope')
        meta = dict(sha256=digest, scope=scope, size=size, parts=parts)
        if self.active is None:
            if index != 0:
                self._fail('missing_first_part')
            self.active, self.started = meta, now
            _diagnostic(self.diagnostic, 'begin', scope=scope,
                        bytes=size, parts=parts, sha256=digest)
        elif self.active != meta:
            self._fail('mixed_transfer')
        expected_size = min(PART_BYTES, size - index * PART_BYTES)
        encoded = message['data']
        if (not isinstance(encoded, _TEXT) or
                len(encoded) != 4 * ((expected_size + 2) // 3)):
            self._fail('fragment_data_length')
        try:
            raw_ascii = encoded.encode('ascii')
            part = base64.b64decode(raw_ascii)
            if base64.b64encode(part) != raw_ascii or len(part) != expected_size:
                self._fail('fragment_data_encoding')
        except (TypeError, ValueError, binascii.Error, UnicodeError):
            self._fail('fragment_data_encoding')
        if index < len(self.data):
            if self.data[index] == part:
                return None  # Exact retransmission cannot count twice.
            self._fail('conflicting_duplicate')
        if index != len(self.data):
            self._fail('part_order')
        self.data.append(part)
        self.received += len(part)
        if self.received > size:
            self._fail('state_length')
        if len(self.data) < parts:
            return None
        payload = b''.join(self.data)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            self._fail('state_integrity')
        try:
            decoded = json.loads(payload.decode('utf-8'))
            if not isinstance(decoded, dict) or _scope(decoded) != scope:
                self._fail('state_scope')
        except (TypeError, ValueError, UnicodeError):
            self._fail('state_payload')
        _diagnostic(self.diagnostic, 'complete', scope=scope, bytes=size,
                    parts=parts, sha256=digest)
        self.clear()
        return decoded


class StreamDecoder(object):
    """Byte framing before UTF-8 decoding, shared by player and worker reads.

    Full state objects alone leave this adapter. The native/main-thread queue
    never sees a state_fragment, and retains its existing lifecycle barriers.
    """
    def __init__(self, diagnostic=None):
        self.buffer = b''
        self.receiver = StateReceiver(diagnostic)

    def check_timeout(self, now):
        self.receiver.check_timeout(now)

    def finish(self):
        self.receiver.finish()
        if self.buffer:
            raise TransferError('truncated_frame')

    def feed(self, chunk, now):
        self.check_timeout(now)
        self.buffer += chunk
        messages = []
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if len(line) + 1 > MAX_FRAME_BYTES:
                raise TransferError('frame_size_limit')
            if not line:
                continue
            try:
                message = json.loads(line.decode('utf-8'))
            except UnicodeError:
                raise TransferError('invalid_utf8')
            except (TypeError, ValueError):
                if self.receiver.active is not None:
                    self.receiver._fail('invalid_fragment_json')
                continue  # Preserve existing ordinary-message rejection.
            message = self.receiver.accept(message, now)
            if isinstance(message, dict):
                messages.append(message)
        if len(self.buffer) >= MAX_FRAME_BYTES:
            raise TransferError('frame_size_limit')
        return messages
