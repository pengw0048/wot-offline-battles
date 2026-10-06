from __future__ import print_function

"""Private native lifecycle for one visible player's physical vehicle.

The visible process is the only LAN player. This process mirrors accepted
room state over a paired local channel and owns no room socket, Bot authority,
garage persistence, gun transaction or player projectile.
"""

import os
import sys
import time

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922.authority_worker import _WorldDrawLease
from gui.mods.offline_lan_0922.lan_client import (
    LANClient, MAX_PENDING_MESSAGES, POLL_INTERVAL, _exact_int, _finite_float,
    _monotonic_time,
    _canonical_effective_params, _canonical_vehicle_compact_descr)


_ROOM_MESSAGE_TYPES = frozenset((
    'roster', 'snapshot', 'events', 'battle_live',
    'player_destructible_contact_result'))


def _load_battle_runtime():
    from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
    return BattleRuntime


class DriverMirrorClient(LANClient):
    """Reuse reviewed room readers without starting any LAN transport."""

    def __init__(self, local, on_event, publish, bigworld=None):
        LANClient.__init__(
            self, '127.0.0.1', 0, local.get('name'), local.get('vehicle'),
            max_health=local.get('max_health'), on_event=on_event,
            bigworld=bigworld, outfits=local.get('outfits'),
            vehicle_compact_descr=local.get('vehicle_compact_descr'),
            effective_params=local.get('effective_params'),
            marks_on_gun=local.get('marks_on_gun', 0))
        self._publish = publish
        self.running = True
        self.connected = True

    def start(self):
        return False

    def stop(self):
        self.running = False
        self.connected = False
        self.ready = False
        self.phase = 'disconnected'

    def _send(self, message):
        # All inherited room commands terminate here. There is deliberately
        # no socket, transport thread or forwarding fallback to the room.
        return False

    def _send_preencoded_trusted(self, message, coalesce_key=None):
        return False

    def is_bot_authority(self):
        return False

    def _notify_message(self, kind, message, replay_hint):
        # The visible process owns replay recording. Publish only elapsed
        # delay after the reader uses this process's re-anchored receive clock.
        if self.on_event is not None and kind is not None:
            if isinstance(message, dict) and '_client_received_time' in message:
                message = dict(message)
                received = message.pop('_client_received_time')
                message['_client_dispatch_delay'] = max(
                    0.0, _monotonic_time() - received)
            self.on_event(kind, message)

    def admit(self, message):
        if not self.running or not isinstance(message, dict):
            return False
        message = dict(message)
        message.pop('_client_received_time', None)
        if '_client_dispatch_delay' in message:
            # Room deadlines use the receive clock. Re-anchor the duration
            # already spent in the visible queue onto this process's epoch;
            # otherwise a delayed frame postpones the countdown a second time.
            delay = max(0.0, _finite_float(
                message.get('_client_dispatch_delay'), 0.0))
            message['_client_received_time'] = _monotonic_time() - delay
        self._handle_message(message)
        return self.running and self.last_error is None

    def publish_driver_state(self, receipt):
        if not self.running or not isinstance(receipt, dict):
            return False
        # Receipts can carry landing/contact transitions. Do not coalesce
        # this boundary unless their owner has explicitly retained them.
        return self._publish(self, 'driver_state', {'receipt': receipt})

    def send_battle_ready(self, bases=None):
        if not self.running:
            return False
        message = {}
        if bases is not None:
            message['bases'] = bases
        return self._publish(self, 'driver_ready', message)


class DriverSession(object):
    """Own the private listener and the bound native Account/Avatar cycle."""

    def __init__(self, config, bridge_factory=None, client_factory=None,
                 battle_factory=None, lobby_ready=None, callback=None,
                 cancel_callback=None, bigworld=None, environ=None,
                 clock=None):
        self._config = dict(config or {})
        self._bridge_factory = bridge_factory
        self._client_factory = client_factory or DriverMirrorClient
        self._battle_factory = battle_factory
        self._lobby_ready = lobby_ready or (lambda: True)
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._bigworld = bigworld
        self._environ = os.environ if environ is None else environ
        self._clock = clock or time.time
        self.bridge = None
        self.client = None
        self.runtime = None
        self.state = 'idle'
        self.error = None
        self._draw = None
        self._stopped = False
        self._cleanup_failed = False
        self._handling_failure = False
        self._callback_id = None
        self._callback_generation = 0
        self._binding = None
        self._last_generation = 0
        self._last_control_seq = 0
        self._pending_start = None
        self._pending_deadline = None
        self._pending_messages = []
        self._was_connected = False

    @property
    def listening(self):
        return bool(not self._stopped and self.bridge is not None and
                    self.bridge.listening and self.bridge.error is None)

    def start(self):
        if self._stopped:
            return False
        if self.bridge is not None:
            return self.listening
        if self._bigworld is None:
            import BigWorld
            self._bigworld = BigWorld
        if self._callback is None:
            self._callback = self._bigworld.callback
        if self._cancel_callback is None:
            self._cancel_callback = self._bigworld.cancelCallback
        if self._draw is None:
            self._draw = _WorldDrawLease(self._bigworld)
        factory = self._bridge_factory
        if factory is None:
            from gui.mods.offline_lan_0922.driver_transport import listen
            factory = listen
        port = self._environ.get(port_config.PLAYER_DRIVER_PORT_ENV)
        token = self._environ.get(port_config.PLAYER_DRIVER_TOKEN_ENV)
        try:
            port = int(port)
            if not 1 <= port <= 65535 or not token:
                raise ValueError('invalid player driver endpoint')
            self.bridge = factory('127.0.0.1', port, token)
            if not self.listening:
                raise RuntimeError(
                    self.bridge.error or 'player driver listener failed')
            self.state = 'listening'
            self._schedule_poll()
            return True
        except Exception as error:
            self.error = str(error)
            self.state = 'failed'
            if self.bridge is not None:
                self.bridge.stop()
            return False

    def _schedule_poll(self):
        if self._stopped or self._callback_id is not None:
            return False
        generation = self._callback_generation

        def poll():
            if self._stopped or generation != self._callback_generation:
                return
            self._callback_id = None
            self._poll()

        self._callback_id = self._callback(POLL_INTERVAL, poll)
        return True

    def _poll(self):
        if self._stopped:
            return
        try:
            for message in self.bridge.poll():
                self._handle_message(message)
            if self._cleanup_failed:
                return
            if self.bridge.error is not None:
                self._driver_failure(RuntimeError(self.bridge.error))
                return
            if self._was_connected and not self.bridge.connected:
                self._driver_failure(RuntimeError('player driver peer left'))
                return
            self._was_connected = bool(self.bridge.connected)
            if self._pending_start is not None:
                if self._clock() >= self._pending_deadline:
                    raise RuntimeError('player driver lobby restoration timed out')
                if self._lobby_ready():
                    self._start_pending_round()
            runtime = self.runtime
            if runtime is not None:
                driver_error = getattr(runtime, '_driver_error', None)
                if driver_error:
                    raise RuntimeError(driver_error)
                if runtime.state == 'failed':
                    raise RuntimeError(runtime.error or 'player driver failed')
                if runtime.state == 'running':
                    ready = getattr(
                        runtime, 'player_driver_ready_for_draw_off', None)
                    if not callable(ready):
                        raise RuntimeError(
                            'player driver model readiness is unavailable')
                    if ready():
                        self._draw.acquire()
                        self.state = 'battle'
                    else:
                        self.state = 'loading_models'
        except Exception as error:
            self._driver_failure(error)
        if not self._cleanup_failed:
            self._schedule_poll()

    def _matches(self, message):
        return bool(self._binding is not None and isinstance(message, dict) and
                    _exact_int(message.get('generation')) ==
                    self._binding['generation'] and
                    _exact_int(message.get('round_id')) ==
                    self._binding['round_id'] and
                    ('player_id' not in message or
                     _exact_int(message.get('player_id')) ==
                     self._binding['player_id']))

    def _publish(self, client, kind, value):
        if (self._stopped or client is not self.client or
                self._binding is None or not client.running):
            return False
        message = dict(value)
        message.update(self._binding)
        message['type'] = kind
        return self.bridge.send(message)

    def _handle_message(self, message):
        if not isinstance(message, dict):
            return False
        kind = message.get('type')
        if kind == 'driver_bind':
            return self._bind(message)
        if not self._matches(message):
            return False
        if kind == 'driver_unbind':
            self._retire_runtime(restore_account=True)
            self.state = 'listening'
            return True
        if kind == 'driver_control':
            sequence = _exact_int(message.get('control_seq'))
            payload = message.get('payload')
            if (sequence is None or sequence <= self._last_control_seq or
                    not isinstance(payload, dict)):
                return False
            payload = dict(payload)
            payload['control_seq'] = sequence
            self._last_control_seq = sequence
            return self._deliver('control', payload)
        if kind == 'driver_message':
            room = message.get('message')
            if (not isinstance(room, dict) or
                    room.get('type') not in _ROOM_MESSAGE_TYPES or
                    _exact_int(room.get('round_id')) !=
                    self._binding['round_id']):
                return False
            client = self.client
            if not client.admit(room) and self.client is client:
                raise RuntimeError(
                    client.last_error or 'driver room mirror rejected state')
            return True
        return False

    def _reject_bind(self, message, reason):
        # A malformed future binding must not retire the currently valid
        # actor. Report the failure only to that attempted generation.
        generation = message['generation']
        self._last_generation = max(self._last_generation, generation)
        self.bridge.send({
            'type': 'driver_failed', 'generation': generation,
            'round_id': message['round_id'], 'player_id': message['player_id'],
            'reason': reason})
        return False

    def _bind(self, message):
        generation = _exact_int(message.get('generation'))
        round_id = _exact_int(message.get('round_id'))
        player_id = _exact_int(message.get('player_id'))
        if (self._stopped or self._cleanup_failed or generation is None or
                generation <= self._last_generation or round_id is None or
                round_id <= 0 or player_id is None or player_id <= 0):
            return False
        welcome, roster, start = (message.get(name)
                                  for name in ('welcome', 'roster', 'start'))
        config = message.get('config')
        if (not isinstance(config, dict) or
                not isinstance(welcome, dict) or
                welcome.get('type') != 'welcome' or
                _exact_int(welcome.get('player_id')) != player_id or
                not isinstance(roster, dict) or
                roster.get('type') != 'roster' or
                _exact_int(roster.get('round_id')) != round_id or
                not isinstance(start, dict) or
                start.get('type') != 'battle_start' or
                _exact_int(start.get('round_id')) != round_id):
            return self._reject_bind(
                message, 'invalid player driver bind envelope')
        players = start.get('players')
        if not isinstance(players, (list, tuple)):
            return self._reject_bind(
                message, 'invalid player driver roster')
        local = next((value for value in players
                      if isinstance(value, dict) and
                      _exact_int(value.get('id')) == player_id), None)
        if (local is None or not _canonical_vehicle_compact_descr(
                local.get('vehicle_compact_descr')) or
                _canonical_effective_params(local.get('effective_params'))
                is None):
            return self._reject_bind(
                message, 'player driver mounted vehicle is unavailable')
        admitted = []
        client = self._client_factory(
            local, lambda kind, value: admitted.append((kind, value)),
            self._publish, bigworld=self._bigworld)
        for value in (welcome, roster, start):
            if not client.admit(value):
                error = client.last_error or 'player driver bind state was rejected'
                client.on_event = None
                client.stop()
                return self._reject_bind(message, error)
        if not any(kind == 'battle_start' for kind, value in admitted):
            client.on_event = None
            client.stop()
            return self._reject_bind(message, 'player driver start was not admitted')
        if self.runtime is not None or self.client is not None:
            try:
                self._retire_runtime(restore_account=True)
            except Exception as error:
                client.on_event = None
                client.stop()
                self._cleanup_failed = True
                self.error = str(error)
                self.state = 'failed'
                return self._reject_bind(message, self.error)
        self._last_generation = generation
        self._binding = dict(generation=generation, round_id=round_id,
                             player_id=player_id)
        self._last_control_seq = 0
        self.error = None
        bind_config = dict(self._config)
        bind_config.update(config)
        self._round_config = bind_config

        def on_event(kind, value):
            if (not self._stopped and self._binding is not None and
                    self._binding['generation'] == generation):
                self._on_event(kind, value)

        client.on_event = on_event
        self.client = client
        self.state = 'binding'
        for kind, value in admitted:
            self._on_event(kind, value)
        return True

    def _on_event(self, kind, message):
        if kind == 'battle_start':
            if self._pending_start is not None or self.runtime is not None:
                return
            self._pending_start = dict(message)
            self._pending_deadline = self._clock() + float(
                self._config.get('startupTimeoutSeconds', 30.0))
            self.state = 'waiting_lobby'
            if self._lobby_ready():
                self._start_pending_round()
        elif kind in ('snapshot', 'events', 'battle_live',
                      'player_destructible_contact_result'):
            self._deliver(kind, message)
        elif kind == 'roster' and message.get('phase') == 'waiting':
            if self.runtime is not None or self._pending_start is not None:
                self._retire_runtime(restore_account=True)
                self.state = 'listening'
        elif kind in ('battle_failed', 'error', 'connection_lost', 'disconnected'):
            self._driver_failure(RuntimeError(
                message.get('message') or 'player driver runtime failed'))

    def _start_pending_round(self):
        message = self._pending_start
        if message is None or self.client is None or self._binding is None:
            return False
        local = next(value for value in message['players']
                     if value['id'] == self._binding['player_id'])
        config = dict(self._round_config)
        config.update({
            'client_mode': port_config.PLAYER_DRIVER_MODE,
            'player_driver_mode': True,
            'worker_mode': False,
            'native_remote_vehicles': False,
            'bot_track_animation': False,
            'map': message['map'],
            'vehicle': local['vehicle'],
            'name': local['name'],
            'spawn': dict((key, local[key]) for key in ('x', 'y', 'z', 'yaw')),
        })
        self._pending_start = None
        self._pending_deadline = None
        factory = self._battle_factory or _load_battle_runtime()
        runtime = factory()
        self.runtime = runtime
        self.state = 'loading'
        # Identity and callback-visible ownership precede native creation,
        # which may synchronously call send_battle_ready or battle_failed.
        accepted = runtime.start(
            config, message=message, lan_client=self.client,
            on_local_leave=None)
        if self.runtime is not runtime:
            return False
        if not accepted:
            raise RuntimeError(runtime.error or 'player driver start rejected')
        pending = self._pending_messages
        self._pending_messages = []
        for kind, value in pending:
            self._deliver(kind, value)
        return True

    def _deliver(self, kind, message):
        runtime = self.runtime
        if runtime is None:
            if self._pending_start is None:
                return False
            if len(self._pending_messages) >= MAX_PENDING_MESSAGES:
                raise RuntimeError('player driver pending state exceeded limit')
            self._pending_messages.append((kind, message))
            return True
        if kind == 'control':
            return runtime.apply_driver_control(message)
        callback = getattr(runtime, 'on_' + kind)
        return callback(message)

    def _retire_runtime(self, restore_account):
        # Fence state publication and input before the first native cleanup.
        self._binding = None
        self._pending_start = None
        self._pending_deadline = None
        self._pending_messages = []
        client = self.client
        if client is not None:
            client.on_event = None
            client.stop()
        runtime = self.runtime
        if runtime is not None:
            runtime.stop(show_login=False,
                         restore_account=bool(restore_account))
        # A failed native stop retains both the runtime and draw lease so a
        # later explicit stop can retry without revealing live native models.
        if self._draw is not None:
            self._draw.restore()
        if self.runtime is runtime:
            self.runtime = None
        if self.client is client:
            self.client = None
        return runtime is not None

    def _driver_failure(self, error):
        if self._handling_failure or self._stopped:
            return False
        self._handling_failure = True
        self.error = str(error)
        self.state = 'failed'
        binding = self._binding
        if binding is not None and self.bridge is not None:
            message = dict(binding)
            message.update(type='driver_failed', reason=self.error)
            self.bridge.send(message)
        sys.stdout.write(
            '[Offline LAN 0.9.22] player driver failed: %s\n' % self.error)
        try:
            self._retire_runtime(restore_account=True)
        except Exception as cleanup_error:
            self._cleanup_failed = True
            sys.stdout.write(
                '[Offline LAN 0.9.22] player driver cleanup failed: %s\n' %
                cleanup_error)
        finally:
            self._handling_failure = False
        return False

    def stop(self, show_login=False, restore_account=False,
             release_join=False):
        del show_login, release_join
        self._stopped = True
        self._callback_generation += 1
        callback_id = self._callback_id
        self._callback_id = None
        if callback_id is not None and self._cancel_callback is not None:
            try:
                self._cancel_callback(callback_id)
            except Exception:
                pass
        try:
            self._retire_runtime(restore_account=restore_account)
        finally:
            if self.bridge is not None:
                self.bridge.stop()
            self.state = 'stopped'
