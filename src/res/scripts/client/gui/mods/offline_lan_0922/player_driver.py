"""Visible-client owner of the process-long private player-driver bridge.

The bridge survives garage returns and new LANClient/BattleRuntime instances.
This module never starts a product, opens a room connection or applies a pose.
Its main-thread poll only queues receipts; BattleRuntime drains them in order
at its frame boundary before publishing the corresponding input/gun state.
"""

import os
import weakref

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import driver_transport, driver_codec
from gui.mods.offline_lan_0922.lan_client import PROTOCOL_VERSION


POLL_SECONDS = 0.01
MAX_PENDING_MESSAGES = 128
MAX_PENDING_BYTES = 8 * 1024 * 1024
_MANAGER = None
try:
    _INTEGER_TYPES = (int, long)
    _STRING_TYPES = (basestring,)
except NameError:
    _INTEGER_TYPES = (int,)
    _STRING_TYPES = (str,)


def _integer(value):
    return value if type(value) in _INTEGER_TYPES else None


def _freeze(value):
    """Freeze startup/pending data only; connected sends freeze in transport."""
    encoded = driver_codec.encode(value)
    if len(encoded) > driver_transport.MAX_FRAME_BYTES:
        raise ValueError('player driver message exceeds frame limit')
    return driver_codec.decode(encoded), len(encoded)


def _endpoint():
    port = os.environ.get(port_config.PLAYER_DRIVER_PORT_ENV)
    token = os.environ.get(port_config.PLAYER_DRIVER_TOKEN_ENV)
    if port is None and token is None:
        return None
    if (not port or not token or not isinstance(token, _STRING_TYPES) or
            len(token) > 256):
        raise ValueError('incomplete player driver endpoint')
    try:
        parsed = int(port)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('invalid player driver port')
    if not 1 <= parsed <= 65535:
        raise ValueError('invalid player driver port')
    return parsed, token


def _bind_message(runtime, generation):
    """Replay reviewed readers from the actual accepted player/start state."""
    client = runtime.client
    start = runtime._start_message
    config = runtime._config
    if (client is None or not isinstance(start, dict) or
            start.get('type') != 'battle_start' or not isinstance(config, dict)):
        raise ValueError('player driver start state is unavailable')
    round_id = _integer(start.get('round_id'))
    player_id = _integer(client.player_id)
    if (round_id is None or round_id <= 0 or player_id is None or player_id <= 0 or
            _integer(client.round_id) != round_id):
        raise ValueError('player driver start identity is invalid')
    rows = start.get('players')
    if not isinstance(rows, (list, tuple)):
        raise ValueError('player driver start players are unavailable')
    local = next((row for row in rows if isinstance(row, dict) and
                  _integer(row.get('id')) == player_id), None)
    if (local is None or not local.get('vehicle_compact_descr') or
            not isinstance(local.get('effective_params'), dict)):
        raise ValueError('player driver mounted vehicle is unavailable')
    if not isinstance(client.roster, (list, tuple)) or not client.roster:
        raise ValueError('player driver roster is unavailable')
    # The synthetic waiting phase is only the mirror reader's bootstrap.
    # Vehicle data comes from the accepted start row, never garage defaults.
    welcome = {
        'type': 'welcome', 'protocol': PROTOCOL_VERSION, 'phase': 'waiting',
        'player_id': player_id, 'round_id': round_id,
        'state_revision': start['state_revision'],
        'host_player_id': start['host_player_id'],
        'bot_authority_id': start['bot_authority_id'],
        'authority_epoch': start['authority_epoch'],
        'map': start['map'],
        'map_pool': list(client.map_pool),
        'spawn': dict((key, local[key]) for key in ('x', 'y', 'z', 'yaw')),
        'capabilities': list(client.capabilities),
        'server_capabilities': list(client.server_capabilities),
        'team_sizes': dict(client.team_sizes),
        'bot_tier_mode': client.bot_tier_mode,
        'bot_skill_mode': client.bot_skill_mode,
    }
    for key in ('name', 'vehicle', 'team', 'slot', 'max_health', 'outfits',
                'vehicle_compact_descr', 'effective_params'):
        welcome[key] = local[key]
    if client.server_time_ms is not None:
        welcome['server_time_ms'] = client.server_time_ms
    roster = dict(start)
    roster['type'] = 'roster'
    roster['players'] = list(client.roster)
    return _freeze({
        'type': 'driver_bind', 'generation': generation,
        'round_id': round_id, 'player_id': player_id,
        'welcome': welcome, 'roster': roster, 'start': start, 'config': config,
    })[0]


class Frontend(object):
    """One round's fenced owner; closing it does not close the shared socket."""

    def __init__(self, manager, runtime, generation):
        self._manager = manager
        self._runtime_ref = weakref.ref(runtime)
        self._client_ref = weakref.ref(runtime.client) if runtime.client is not None else None
        self._runtime_generation = getattr(runtime, '_generation', None)
        self.generation = generation
        start = runtime._start_message
        self.round_id = _integer(start.get('round_id')) if isinstance(start, dict) else None
        self.player_id = _integer(getattr(runtime.client, 'player_id', None))
        self.ready = False
        self.error = None
        self.bases = None
        self._closed = False
        self._bind = None
        self._bound = False
        self._control_seq = 0
        self._pending = []
        self._pending_bytes = 0
        self._receipts = []
        self._receipt_bytes = 0

    def _current(self):
        runtime = self._runtime_ref()
        return bool(
            not self._closed and self._manager.owner is self and
            runtime is not None and self._client_ref is not None and
            runtime.client is self._client_ref() and
            getattr(runtime, '_generation', None) == self._runtime_generation and
            _integer((runtime._start_message or {}).get('round_id')) == self.round_id and
            _integer(getattr(runtime.client, 'round_id', None)) == self.round_id)

    def _fail(self, reason):
        if self.error is None:
            self.error = reason
        self.ready = False
        self._pending = []
        self._pending_bytes = 0

    def _envelope(self, kind):
        return {'type': kind, 'generation': self.generation,
                'round_id': self.round_id, 'player_id': self.player_id}

    def _send(self, message):
        bridge = self._manager.bridge
        if bridge is None or not bridge.connected:
            if bridge is not None and bridge.error is not None:
                self._manager.fail(bridge.error)
            return False
        if not bridge.send(message):
            self._manager.fail(bridge.error or 'player driver send failed')
            return False
        return True

    def send_control(self, payload):
        if not self._current() or not self.ready or self.error is not None:
            return False
        if not isinstance(payload, dict):
            self._fail('invalid player driver control')
            return False
        sequence = self._control_seq + 1
        message = self._envelope('driver_control')
        message.update(control_seq=sequence, payload=payload)
        if not self._send(message):
            return False
        self._control_seq = sequence
        return True

    def forward_message(self, message):
        """Forward accepted room state reliably, including every snapshot.

        Sparse orders, destruction and transition fields can ride a snapshot.
        This first bridge intentionally does not classify or coalesce them.
        """
        if (not self._current() or self.error is not None or
                not isinstance(message, dict) or
                _integer(message.get('round_id')) != self.round_id):
            return False
        outgoing = self._envelope('driver_message')
        outgoing['message'] = message
        if self._bound:
            return self._send(outgoing)
        try:
            frozen, size = _freeze(outgoing)
        except (TypeError, ValueError, OverflowError, RuntimeError):
            self._fail('invalid pending player driver message')
            return False
        if (len(self._pending) >= MAX_PENDING_MESSAGES or
                self._pending_bytes + size > MAX_PENDING_BYTES):
            self._fail('player driver pending messages exceeded limit')
            return False
        self._pending.append((frozen, size))
        self._pending_bytes += size
        return True

    def _receive(self, message, wire_bytes):
        if (not self._current() or not self._bound or self.error is not None or
                not isinstance(message, dict) or
                _integer(message.get('generation')) != self.generation or
                _integer(message.get('round_id')) != self.round_id or
                ('player_id' in message and
                 _integer(message['player_id']) != self.player_id)):
            return
        kind = message.get('type')
        if kind == 'driver_ready':
            if self._bound:
                self.bases = message.get('bases')
                self.ready = True
        elif kind == 'driver_failed':
            reason = message.get('reason')
            if not isinstance(reason, _STRING_TYPES) or not reason:
                reason = 'player driver failed'
            self._fail(reason[:256])
        elif kind == 'driver_state':
            receipt = message.get('receipt')
            if not isinstance(receipt, dict):
                self._fail('invalid player driver receipt')
                return
            # The IO thread already decoded/validated the envelope. Preserve
            # its wire size across polls instead of reserializing on this
            # frame callback solely to enforce the receipt queue budget.
            size = _integer(wire_bytes)
            if size is None or not 4 < size <= driver_transport.MAX_FRAME_BYTES + 4:
                self._fail('invalid player driver receipt size')
                return
            if (len(self._receipts) >= MAX_PENDING_MESSAGES or
                    self._receipt_bytes + size > MAX_PENDING_BYTES):
                self._fail('player driver receipts exceeded limit')
                return
            self._receipts.append(receipt)
            self._receipt_bytes += size

    def drain(self):
        """Transfer receipts in receive order, including those preceding EOF."""
        receipts = self._receipts if self._current() else []
        self._receipts = []
        self._receipt_bytes = 0
        return receipts

    def close(self, reason='round ended'):
        if self._closed:
            return
        self._closed = True
        self.ready = False
        self._pending = []
        self._pending_bytes = 0
        self._manager.release(self, reason)


class _Manager(object):
    def __init__(self, endpoint=None, error=None):
        self.endpoint = endpoint
        self.fatal = error
        self.bridge = None
        self.owner = None
        self._generation = 0
        self._bigworld = None
        self._callback_id = None
        self._callback_token = None
        self._was_connected = False

    def fail(self, reason):
        if self.fatal is None:
            self.fatal = reason
        if self.owner is not None:
            self.owner._fail(self.fatal)

    def acquire(self, runtime):
        current = self.owner
        if current is not None and current._runtime_ref() is runtime and current._current():
            return current
        if current is not None:
            current.close('owner replaced')
        self._generation += 1
        owner = Frontend(self, runtime, self._generation)
        self.owner = owner
        if self.fatal is not None:
            owner._fail(self.fatal)
            return owner
        try:
            owner._bind = _bind_message(runtime, owner.generation)
            self._bigworld = runtime._runtime.bigworld
            if (not callable(getattr(self._bigworld, 'callback', None)) or
                    not callable(getattr(self._bigworld, 'cancelCallback', None))):
                raise ValueError('player driver main-thread callbacks unavailable')
            if self.bridge is None:
                self.bridge = driver_transport.connect(
                    '127.0.0.1', self.endpoint[0], self.endpoint[1])
            if self.bridge.error is not None:
                self.fail(self.bridge.error)
                return owner
            self._poll(owner)
        except (AttributeError, KeyError, TypeError, ValueError,
                OverflowError, RuntimeError):
            owner._fail('player driver binding failed')
        return owner

    def _schedule(self, owner):
        if not owner._current() or owner.error is not None or self._callback_token is not None:
            return
        token = object()
        self._callback_token = token

        def poll():
            if self._callback_token is not token or self.owner is not owner:
                return
            self._callback_token = None
            self._callback_id = None
            self._poll(owner)

        try:
            callback_id = self._bigworld.callback(POLL_SECONDS, poll)
            if self._callback_token is token:
                self._callback_id = callback_id
        except Exception:
            self._callback_token = None
            self.fail('player driver poll scheduling failed')

    def _poll(self, owner):
        if not owner._current():
            if self.owner is owner:
                owner.close('owner changed')
            return
        try:
            bridge = self.bridge
            if bridge.connected:
                self._was_connected = True
                if not owner._bound and owner.error is None:
                    if owner._send(owner._bind):
                        owner._bound = True
                if owner._bound and owner.error is None:
                    while owner._pending:
                        message, size = owner._pending[0]
                        if not owner._send(message):
                            break
                        owner._pending.pop(0)
                        owner._pending_bytes -= size
            for message, wire_bytes in bridge.poll_records():
                owner._receive(message, wire_bytes)
            if bridge.error is not None:
                self.fail(bridge.error)
            elif self._was_connected and not bridge.connected:
                self.fail('player driver connection lost')
        except Exception:
            self.fail('player driver poll failed')
        self._schedule(owner)

    def release(self, owner, reason):
        if self.owner is not owner:
            return
        if owner._bound and self.bridge is not None and self.bridge.connected:
            message = owner._envelope('driver_unbind')
            message['reason'] = (reason[:128] if isinstance(reason, _STRING_TYPES)
                                 else 'round ended')
            if not self.bridge.send(message):
                self.fail(self.bridge.error or 'player driver unbind failed')
        self._callback_token = None
        callback_id = self._callback_id
        self._callback_id = None
        if callback_id is not None and self._bigworld is not None:
            try:
                self._bigworld.cancelCallback(callback_id)
            except Exception:
                pass
        self.owner = None


def attach(runtime):
    """Return a fenced frontend, or None only for an unpaired/hidden client."""
    global _MANAGER
    config = runtime._config or {}
    if (getattr(runtime, '_worker_mode', False) or
            getattr(runtime, '_replay_mode', False) or
            config.get('worker_mode') or config.get('player_driver_mode') or
            config.get('client_mode', port_config.PLAYER_MODE) != port_config.PLAYER_MODE):
        return None
    endpoint = None
    error = None
    try:
        endpoint = _endpoint()
    except ValueError as failure:
        error = str(failure)
    if endpoint is None and error is None and _MANAGER is None:
        return None
    if _MANAGER is None:
        _MANAGER = _Manager(endpoint, error)
    elif error is not None or endpoint != _MANAGER.endpoint:
        _MANAGER.fail(error or 'player driver endpoint changed')
    return _MANAGER.acquire(runtime)
