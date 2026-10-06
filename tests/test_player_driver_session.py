import copy
import json
from pathlib import Path
import socket
import sys
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import driver_session
from gui.mods.offline_lan_0922 import player_driver
from gui.mods.offline_lan_0922 import snapshot_delta
from gui.mods.offline_lan_0922.driver_session import (
    DriverMirrorClient, DriverSession)
from player_driver_fixture import driver_bind, envelope, room_message


class Callbacks:
    def __init__(self):
        self.pending = {}
        self.history = []
        self.next_id = 0

    def callback(self, delay, function):
        self.next_id += 1
        self.pending[self.next_id] = function
        self.history.append(function)
        return self.next_id

    def cancel(self, callback_id):
        self.pending.pop(callback_id, None)

    def fire(self):
        callback_id = min(self.pending)
        self.pending.pop(callback_id)()


class DrawWorld:
    READ = object()

    def __init__(self):
        self.enabled = True
        self.transitions = []

    def worldDrawEnabled(self, value=READ):
        if value is self.READ:
            return self.enabled
        self.enabled = bool(value)
        self.transitions.append(self.enabled)


class Bridge:
    def __init__(self):
        self.listening = True
        self.connected = False
        self.error = None
        self.messages = []
        self.sent = []
        self.stopped = False

    def send(self, message, replace_key=None):
        if not self.connected:
            return False
        self.sent.append((copy.deepcopy(message), replace_key))
        return True

    def poll(self):
        values, self.messages = self.messages, []
        return values

    def stop(self):
        self.stopped = True
        self.listening = self.connected = False


class Runtime:
    def __init__(self):
        self.state = 'loading'
        self.error = None
        self.calls = []
        self.draw_ready = False
        self.fail_stop = False
        self.accept_start = True
        self.fail_event = False
        self.ready_on_start = False

    def start(self, config, message, lan_client, on_local_leave):
        self.config, self.message, self.client = config, message, lan_client
        self.calls.append(('start', config['spawn']))
        self.state = 'running'
        if self.ready_on_start:
            self.client.send_battle_ready([{'team': 1, 'x': 12, 'z': 24}])
        return self.accept_start

    def player_driver_ready_for_draw_off(self):
        return self.draw_ready

    def stop(self, **kwargs):
        self.calls.append(('stop', kwargs))
        # Native stop can synchronously re-enter a publisher. It must already
        # be fenced before any Entity teardown starts.
        self.teardown_publish = self.client.publish_driver_state({'sample_seq': 999})
        if self.fail_stop:
            raise RuntimeError('native stop failed')
        self.state = 'stopped'

    def apply_driver_control(self, payload):
        self.calls.append(('control', payload))
        return True

    def on_snapshot(self, value):
        self.calls.append(('snapshot', value))
        return True

    def on_events(self, value):
        if self.fail_event:
            raise RuntimeError('native event failed')
        self.calls.append(('events', value))
        return True

    def on_battle_live(self, value):
        self.calls.append(('battle_live', value))
        return True

    def on_player_destructible_contact_result(self, value):
        self.calls.append(('player_destructible_contact_result', value))
        return True


class DriverSessionTests(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.world, self.bridge = Callbacks(), DrawWorld(), Bridge()
        self.lobby = True
        self.now = 100.0
        self.created = []
        self.factory = mock.Mock(side_effect=lambda *args: self.bridge)

        def create():
            value = Runtime()
            self.created.append(value)
            return value

        self.session = DriverSession(
            {'startupTimeoutSeconds': 30.0}, bridge_factory=self.factory,
            battle_factory=create, lobby_ready=lambda: self.lobby,
            callback=self.callbacks.callback, cancel_callback=self.callbacks.cancel,
            bigworld=self.world, clock=lambda: self.now, environ={
                port_config.PLAYER_DRIVER_PORT_ENV: '29000',
                port_config.PLAYER_DRIVER_TOKEN_ENV: 'private-test-pair',
            })
        self.assertTrue(self.session.start())
        self.addCleanup(self.session.stop)

    def receive(self, *messages):
        self.bridge.connected = True
        self.bridge.messages.extend(messages)
        self.callbacks.fire()

    def bind(self, generation=1, round_id=2):
        self.receive(driver_bind(generation, round_id))
        return self.session.runtime

    def control(self, sequence=1, generation=1, round_id=2):
        return dict(type='driver_control', generation=generation,
                    round_id=round_id, control_seq=sequence,
                    payload={'forward': 1.0})

    def test_listener_readiness_does_not_wait_for_visible_or_create_runtime(self):
        self.factory.assert_called_once_with('127.0.0.1', 29000, 'private-test-pair')
        self.assertTrue(self.session.listening)
        self.assertFalse(self.bridge.connected)
        self.assertIsNone(self.session.client)
        self.assertIsNone(self.session.runtime)
        self.assertEqual('listening', self.session.state)

    def test_bound_runtime_uses_real_player_and_mounted_descriptor(self):
        runtime = self.bind()
        self.assertEqual(3, runtime.client.player_id)
        self.assertFalse(runtime.client.is_bot_authority())
        self.assertEqual([3], [value['id'] for value in runtime.message['players']])
        self.assertEqual('dGVzdA==', runtime.client.vehicle_compact_descr)
        self.assertEqual({'x': 12.0, 'y': 1.0, 'z': 24.0, 'yaw': 0.0},
                         runtime.config['spawn'])
        self.assertTrue(runtime.config['player_driver_mode'])
        self.assertFalse(runtime.config['worker_mode'])
        self.assertFalse(runtime.config['native_remote_vehicles'])
        self.assertFalse(runtime.config['bot_track_animation'])
        self.assertIsNone(runtime.client.sock)
        self.assertIsNone(runtime.client.thread)
        self.assertIsNone(runtime.client._sender_thread)
        self.assertFalse(runtime.client.start())

    def test_draw_waits_for_native_models_and_restores_after_stop(self):
        runtime = self.bind()
        self.assertTrue(self.world.enabled)
        self.assertEqual('loading_models', self.session.state)
        runtime.draw_ready = True
        self.callbacks.fire()
        self.assertFalse(self.world.enabled)
        self.receive(dict(type='driver_unbind', generation=1, round_id=2))
        self.assertTrue(self.world.enabled)
        self.assertFalse(runtime.teardown_publish)
        self.assertEqual([False, True], self.world.transitions)

    def test_synchronous_start_ready_has_bound_identity(self):
        runtime = Runtime()
        runtime.ready_on_start = True
        self.session._battle_factory = lambda: runtime
        self.bind()
        self.assertEqual({'type': 'driver_ready', 'generation': 1,
                          'round_id': 2, 'player_id': 3,
                          'bases': [{'team': 1, 'x': 12, 'z': 24}]},
                         self.bridge.sent[0][0])

    def test_control_sequence_and_old_generation_are_rejected(self):
        runtime = self.bind()
        self.receive(self.control(4), self.control(4), self.control(3),
                     self.control(5, generation=2), self.control(5, round_id=3),
                     dict(self.control(5), player_id=4))
        applied = [value for kind, value in runtime.calls if kind == 'control']
        self.assertEqual([{'forward': 1.0, 'control_seq': 4}], applied)

    def test_duplicate_bind_does_not_restart_native_lifecycle(self):
        runtime = self.bind()
        self.receive(driver_bind())
        self.assertIs(runtime, self.session.runtime)
        self.assertEqual(1, len(self.created))

    def test_invalid_future_bind_preserves_current_runtime(self):
        runtime = self.bind()
        bad = driver_bind(generation=2, round_id=3)
        bad['welcome']['server_capabilities'] = []
        self.receive(bad)
        self.assertIs(runtime, self.session.runtime)
        self.assertTrue(runtime.client.running)
        self.assertEqual('driver_failed', self.bridge.sent[-1][0]['type'])
        self.assertEqual(2, self.bridge.sent[-1][0]['generation'])
        self.assertFalse(any(kind == 'stop' for kind, value in runtime.calls))

    def test_no_worker_or_missing_mounted_identity_can_bind(self):
        self.receive(driver_bind(player_id=-1))
        self.assertIsNone(self.session.runtime)
        bad = driver_bind()
        bad['start']['players'][0].pop('vehicle_compact_descr')
        self.receive(bad)
        self.assertIsNone(self.session.runtime)
        self.assertEqual('driver_failed', self.bridge.sent[-1][0]['type'])

    def test_new_round_waits_for_lobby_and_replays_ordered_events(self):
        old = self.bind()
        old_client = old.client
        self.lobby = False
        self.receive(driver_bind(generation=2, round_id=3),
                     envelope(room_message('events', round_id=3, tick=1), 2, 3),
                     self.control(1, generation=2, round_id=3),
                     envelope(room_message('snapshot', round_id=3, tick=2), 2, 3),
                     envelope(room_message('battle_live', round_id=3, tick=3), 2, 3))
        self.assertIsNone(self.session.runtime)
        self.assertEqual('waiting_lobby', self.session.state)
        self.assertFalse(old_client.publish_driver_state({'sample_seq': 1}))
        self.assertEqual(1, len(self.created))
        self.lobby = True
        self.callbacks.fire()
        self.assertEqual(['start', 'events', 'control', 'snapshot', 'battle_live'],
                         [kind for kind, value in self.session.runtime.calls])

    def test_lobby_timeout_reports_only_current_driver_failure(self):
        self.lobby = False
        self.bind()
        self.now += 31.0
        self.callbacks.fire()
        self.assertIsNone(self.session.runtime)
        self.assertEqual('failed', self.session.state)
        self.assertEqual('driver_failed', self.bridge.sent[-1][0]['type'])
        self.assertFalse(self.bridge.stopped)

    def test_snapshot_receipt_and_destructible_result_use_current_binding(self):
        runtime = self.bind()
        room = room_message('snapshot')
        room['_client_received_time'] = 987654321.0
        room['_client_dispatch_delay'] = 0.03
        self.receive(envelope(room), envelope(room_message(
            'player_destructible_contact_result', tick=2)))
        snapshot = next(value for kind, value in runtime.calls if kind == 'snapshot')
        self.assertNotIn('_client_received_time', snapshot)
        self.assertAlmostEqual(0.03, snapshot['_client_dispatch_delay'], delta=0.01)
        self.assertEqual('player_destructible_contact_result', runtime.calls[-1][0])
        receipt = {'sample_seq': 3, 'landings': [{'seq': 1, 'impact_speed': 12.0}]}
        self.assertTrue(runtime.client.publish_driver_state(receipt))
        sent, replace_key = self.bridge.sent[-1]
        self.assertEqual(receipt, sent['receipt'])
        self.assertEqual((1, 2, 3), (sent['generation'], sent['round_id'], sent['player_id']))
        self.assertIsNone(replace_key)

    def test_countdown_reanchors_only_elapsed_duration_between_process_clocks(self):
        runtime = self.bind()
        live = room_message('battle_live')
        live['_client_received_time'] = 987654321.0
        live['_client_dispatch_delay'] = 0.4
        with mock.patch.object(driver_session, '_monotonic_time', return_value=80.0):
            self.receive(envelope(live))
        self.assertAlmostEqual(89.6, runtime.client.combat_deadline)
        received = next(value for kind, value in runtime.calls if kind == 'battle_live')
        self.assertNotIn('_client_received_time', received)
        self.assertAlmostEqual(0.4, received['_client_dispatch_delay'])

    def test_canonical_materialized_delta_survives_private_json_roundtrip(self):
        runtime = self.bind()
        accepted = []
        source = DriverMirrorClient(
            driver_bind()['start']['players'][0],
            lambda kind, value: accepted.append((kind, value)), lambda *args: False)
        binding = driver_bind()
        for name in ('welcome', 'roster', 'start'):
            self.assertTrue(source.admit(binding[name]))
        encoded_baseline = decoded_baseline = None
        for sequence in (1, 2):
            snapshot = room_message('snapshot', tick=sequence)
            snapshot['map'] = '07_lakeville'
            snapshot['players'][0]['x'] = float(sequence * 20)
            if sequence == 2:
                snapshot['players'][0].pop('effective_params')
                snapshot['players'][0].pop('outfits')
                snapshot.pop('bot_manifest')
            wire, encoded_baseline = snapshot_delta.encode(
                snapshot, encoded_baseline, sequence)
            pending, decoded_baseline = snapshot_delta.decode(
                wire, decoded_baseline, return_pending_actors=True)
            self.assertTrue(source._dispatch_message(pending))
            kind, canonical = accepted[-1]
            self.assertEqual('snapshot', kind)
            self.assertIsInstance(canonical['players'], list)
            self.assertIn('effective_params', canonical['players'][0])
            self.assertIn('bot_manifest', canonical)
            self.receive(envelope(json.loads(json.dumps(canonical))))
        received = [value for kind, value in runtime.calls if kind == 'snapshot']
        self.assertEqual([20.0, 40.0], [value['players'][0]['x'] for value in received])
        self.assertTrue(runtime.client.running)

    def test_late_same_round_waiting_roster_preserves_active_native_owner(self):
        runtime = self.bind()
        roster = driver_bind()['roster']
        roster.update(phase='waiting', state_revision=6)
        self.receive(envelope(roster))
        self.assertIs(runtime, self.session.runtime)
        self.assertIsNone(self.session.error)
        self.assertFalse(any(value['type'] == 'driver_failed' for value, key in self.bridge.sent))
        self.receive(dict(type='driver_unbind', generation=1, round_id=2))
        self.assertIsNone(self.session.runtime)
        self.assertIsNone(self.session.error)
        self.assertEqual('listening', self.session.state)
        self.assertFalse(any(value['type'] == 'driver_failed' for value, key in self.bridge.sent))

    def test_native_event_exception_is_local_and_preserves_stop_retry(self):
        runtime = self.bind()
        runtime.draw_ready = True
        self.callbacks.fire()
        runtime.fail_event = runtime.fail_stop = True
        self.receive(envelope(room_message('events')))
        self.assertEqual('failed', self.session.state)
        self.assertIs(runtime, self.session.runtime)
        self.assertFalse(self.world.enabled)
        self.assertFalse(runtime.client.running)
        self.assertFalse(self.callbacks.pending)
        runtime.fail_stop = False
        self.session.stop()
        self.assertIsNone(self.session.runtime)
        self.assertTrue(self.world.enabled)

    def test_failed_native_start_retires_partial_owner_and_reports_failure(self):
        runtime = Runtime()
        runtime.accept_start = False
        self.session._battle_factory = lambda: runtime
        self.bind()
        self.assertIsNone(self.session.runtime)
        self.assertFalse(runtime.client.running)
        self.assertEqual('driver_failed', self.bridge.sent[-1][0]['type'])

    def test_running_hidden_driver_error_reports_failure_and_retires_only_actor(self):
        runtime = self.bind()
        client = runtime.client
        runtime.draw_ready = True
        self.callbacks.fire()
        self.assertFalse(self.world.enabled)
        runtime._driver_error = 'native control sample failed'
        self.assertEqual('running', runtime.state)
        with mock.patch.object(driver_session.LANClient, 'leave_battle') as leave, \
                mock.patch.object(driver_session.LANClient, 'stop') as room_stop:
            self.callbacks.fire()
        leave.assert_not_called()
        room_stop.assert_not_called()
        self.assertIsNone(self.session.runtime)
        self.assertIsNone(self.session.client)
        self.assertIsNone(self.session._binding)
        self.assertFalse(client.running)
        self.assertFalse(runtime.teardown_publish)
        self.assertTrue(self.world.enabled)
        self.assertEqual('failed', self.session.state)
        self.assertEqual('native control sample failed', self.session.error)
        self.assertEqual([({'type': 'driver_failed', 'generation': 1,
                           'round_id': 2, 'player_id': 3,
                           'reason': 'native control sample failed'}, None)],
                         self.bridge.sent)
        self.assertIn(('stop', {'show_login': False, 'restore_account': True}),
                      runtime.calls)
        self.assertFalse(self.bridge.stopped)

    def test_disconnect_fences_driver_without_a_room_leave_or_bot_failure(self):
        runtime = self.bind()
        self.bridge.connected = False
        self.callbacks.fire()
        self.assertIsNone(self.session.runtime)
        self.assertFalse(runtime.client.running)
        self.assertEqual('failed', self.session.state)
        self.assertFalse(any(value['type'] in ('leave', 'error', 'bot_state')
                             for value, key in self.bridge.sent))

    def test_stopped_callbacks_and_queued_messages_cannot_recreate_runtime(self):
        runtime = self.bind()
        callback = self.callbacks.history[-1]
        self.session.stop()
        self.session.stop()
        self.bridge.messages = [driver_bind(generation=2)]
        callback()
        self.assertIsNone(self.session.runtime)
        self.assertEqual(1, sum(kind == 'stop' for kind, value in runtime.calls))
        self.assertFalse(self.callbacks.pending)

    def test_mirror_room_writes_never_create_a_socket_or_queue(self):
        runtime = self.bind()
        client = runtime.client
        with mock.patch.object(driver_session.LANClient, 'start') as start:
            self.assertFalse(client.start())
        start.assert_not_called()
        self.assertFalse(client._send({'type': 'hello'}))
        self.assertFalse(client._send_preencoded_trusted(object()))
        self.assertEqual([], client._outbound_queue)
        self.assertEqual([], self.bridge.sent)

    def test_player_driver_mode_is_explicit_and_does_not_change_default(self):
        self.assertEqual('player', port_config.client_mode({}, environ={}))
        self.assertEqual('player_driver', port_config.client_mode({}, environ={
            port_config.CLIENT_MODE_ENV: 'player_driver'}))


class DriverBridgeIntegrationTests(unittest.TestCase):
    def test_real_private_socket_survives_two_rounds_and_lobby_restoration(self):
        # Only the native runtime is a fake. Both session owners and the
        # authenticated, threaded JSON transport are the production classes.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        driver_callbacks, visible_callbacks = Callbacks(), Callbacks()
        world, lobby, created = DrawWorld(), [True], []

        def create():
            runtime = Runtime()
            runtime.ready_on_start = runtime.draw_ready = True
            created.append(runtime)
            return runtime

        driver = DriverSession(
            {}, battle_factory=create, lobby_ready=lambda: lobby[0],
            callback=driver_callbacks.callback,
            cancel_callback=driver_callbacks.cancel, bigworld=world,
            environ={port_config.PLAYER_DRIVER_PORT_ENV: str(port),
                     port_config.PLAYER_DRIVER_TOKEN_ENV: 'integration-pair'})
        self.addCleanup(driver.stop)
        self.assertTrue(driver.start())
        self.assertTrue(driver.listening)
        manager = player_driver._Manager((port, 'integration-pair'))

        def close_manager():
            if manager.owner is not None:
                manager.owner.close()
            if manager.bridge is not None:
                manager.bridge.stop()

        self.addCleanup(close_manager)

        def visible(round_id):
            binding = driver_bind(round_id=round_id)
            client = DriverMirrorClient(
                binding['start']['players'][0], lambda *args: None,
                lambda *args: False)
            for name in ('welcome', 'roster', 'start'):
                self.assertTrue(client.admit(binding[name]))
            class VisibleRuntime:
                pass
            owner = VisibleRuntime()
            owner._generation = round_id
            owner._config = binding['config']
            owner._start_message = binding['start']
            owner.client = client
            owner._runtime = types.SimpleNamespace(bigworld=types.SimpleNamespace(
                callback=visible_callbacks.callback,
                cancelCallback=visible_callbacks.cancel))
            return owner

        def until(predicate):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if driver_callbacks.pending:
                    driver_callbacks.fire()
                if visible_callbacks.pending:
                    visible_callbacks.fire()
                if predicate():
                    return
                # The assertion waits for the actual protocol acknowledgement;
                # this yield only allows the socket thread to make progress.
                time.sleep(0.001)
            self.fail('private driver transition timed out: %s / %s' % (
                driver.error, manager.fatal))

        visible_one = visible(2)
        first = manager.acquire(visible_one)
        until(lambda: first.ready)
        first_runtime, first_client = driver.runtime, driver.client
        self.assertFalse(world.enabled)
        self.assertTrue(first.send_control({'forward': 1.0, 'turn': 0.0}))
        until(lambda: any(kind == 'control' for kind, value in first_runtime.calls))
        self.assertTrue(first_client.publish_driver_state({'sample_seq': 1}))
        until(lambda: bool(first._receipts))
        self.assertEqual([{'sample_seq': 1}], first.drain())

        lobby[0] = False
        first.close()
        visible_two = visible(3)
        second = manager.acquire(visible_two)
        until(lambda: driver.state == 'waiting_lobby' and
              driver._binding['generation'] == second.generation)
        self.assertIsNone(driver.runtime)
        self.assertTrue(world.enabled)
        self.assertEqual(1, len(created))
        self.assertFalse(first_client.publish_driver_state({'sample_seq': 2}))
        self.assertTrue(second.forward_message(room_message('events', round_id=3)))
        until(lambda: bool(driver._pending_messages))
        lobby[0] = True
        until(lambda: second.ready)
        self.assertEqual(['start', 'events'],
                         [kind for kind, value in driver.runtime.calls])
        self.assertEqual(2, len(created))
        self.assertTrue(manager.bridge.connected)
        self.assertFalse(world.enabled)
        self.assertTrue(driver.bridge.send({
            'type': 'driver_state', 'generation': first.generation,
            'round_id': first.round_id, 'player_id': first.player_id,
            'receipt': {'sample_seq': 999}}))
        self.assertTrue(driver.client.publish_driver_state({'sample_seq': 1}))
        until(lambda: bool(second._receipts))
        self.assertEqual([{'sample_seq': 1}], second.drain())
        second.close()
        until(lambda: driver.runtime is None)
        self.assertEqual('listening', driver.state)
        self.assertTrue(world.enabled)
        self.assertTrue(manager.bridge.connected)


if __name__ == '__main__':
    unittest.main()
