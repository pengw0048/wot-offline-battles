import copy
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(
    Path(__file__).resolve().parents[1] / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import player_driver
from gui.mods.offline_lan_0922.driver_session import DriverMirrorClient
from player_driver_fixture import driver_bind, room_message


class FakeBigWorld:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0

    def callback(self, delay, function):
        assert delay == player_driver.POLL_SECONDS
        self.next_id += 1
        self.callbacks[self.next_id] = function
        return self.next_id

    def cancelCallback(self, callback_id):
        self.callbacks.pop(callback_id, None)

    def step(self):
        callback_id = min(self.callbacks)
        self.callbacks.pop(callback_id)()


class FakeBridge:
    def __init__(self, connected=True):
        self.connected = connected
        self.error = None
        self.sent = []
        self.incoming = []
        self.fail_send = False
        self.stop_calls = 0

    def send(self, message, replace_key=None):
        assert replace_key is None, 'Driver messages must remain reliable'
        if self.fail_send or not self.connected:
            return False
        self.sent.append(json.loads(json.dumps(message)))
        return True

    def poll_records(self):
        value = self.incoming
        self.incoming = []
        return [(row, len(player_driver.driver_transport._encode(row)))
                for row in value]

    def stop(self):
        self.stop_calls += 1
        self.connected = False


class FakeRuntime:
    def __init__(self, round_id=2, player_id=3, bigworld=None):
        data = driver_bind(round_id=round_id, player_id=player_id)
        self._runtime = types.SimpleNamespace(bigworld=bigworld or FakeBigWorld())
        self._generation = 8
        self._start_message = data['start']
        self._config = {'client_mode': 'player', 'physics_tuning': {'value': 3}}
        self._worker_mode = False
        self._replay_mode = False
        self._local_position = (100, 200, 300)
        self.client = DriverMirrorClient(
            data['start']['players'][0], None, lambda *unused: True,
            bigworld=self._runtime.bigworld)
        for key in ('welcome', 'roster', 'start'):
            assert self.client.admit(copy.deepcopy(data[key]))


class PlayerDriverTests(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge()
        self.env = mock.patch.dict(os.environ, {
            port_config.PLAYER_DRIVER_PORT_ENV: '31555',
            port_config.PLAYER_DRIVER_TOKEN_ENV: 'private-test-token',
        }, clear=True)
        self.env.start()
        self.manager_patch = mock.patch.object(player_driver, '_MANAGER', None)
        self.manager_patch.start()
        self.factory_patch = mock.patch.object(
            player_driver.driver_transport, 'connect', return_value=self.bridge)
        self.factory = self.factory_patch.start()

    def tearDown(self):
        manager = player_driver._MANAGER
        if manager is not None and manager.owner is not None:
            manager.owner.close('test finished')
        self.factory_patch.stop()
        self.manager_patch.stop()
        self.env.stop()

    def ready(self, frontend, runtime):
        self.bridge.incoming.append({
            'type': 'driver_ready', 'generation': frontend.generation,
            'round_id': frontend.round_id, 'player_id': frontend.player_id,
            'bases': [{'team': 1, 'x': 0, 'y': 0, 'z': 0}],
        })
        runtime._runtime.bigworld.step()
        self.assertTrue(frontend.ready)

    def receipt(self, frontend, sequence, **changes):
        value = {'type': 'driver_state', 'generation': frontend.generation,
                 'round_id': frontend.round_id, 'player_id': frontend.player_id,
                 'receipt': {'sample_seq': sequence, 'landing': [{'seq': sequence}]}}
        value.update(changes)
        return value

    def test_receipt_admission_uses_wire_bytes_without_reencoding(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.ready(frontend, runtime)
        message = {'type': 'driver_state', 'generation': frontend.generation,
                   'round_id': frontend.round_id, 'player_id': frontend.player_id,
                   'receipt': {'sample_seq': 1, 'pose': [1, 2, 3]}}
        with mock.patch.object(player_driver.driver_codec, 'encode',
                               side_effect=AssertionError('main-thread reencode')):
            frontend._receive(message, 100)
        self.assertEqual(100, frontend._receipt_bytes)
        self.assertEqual([message['receipt']], frontend.drain())
        self.assertEqual(0, frontend._receipt_bytes)

    def test_identical_control_is_not_resent_but_edges_and_acks_are(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.ready(frontend, runtime)
        payload = {'forward': 1.0, 'turn': 0.0,
                   'critical': {'health': 100}, 'published_sample_seq': 0}
        self.assertTrue(frontend.send_control(payload))
        self.assertTrue(frontend.send_control(copy.deepcopy(payload)))
        self.assertEqual(1, frontend._control_seq)
        payload['critical']['health'] = 90
        self.assertTrue(frontend.send_control(payload))
        payload['forward'] = 0.0
        self.assertTrue(frontend.send_control(payload))
        payload['published_sample_seq'] = 3
        self.assertTrue(frontend.send_control(payload))
        rows = [row for row in self.bridge.sent
                if row['type'] == 'driver_control']
        self.assertEqual([1, 2, 3, 4], [row['control_seq'] for row in rows])
        self.assertEqual([1.0, 1.0, 0.0, 0.0],
                         [row['payload']['forward'] for row in rows])
        self.assertEqual(100, rows[0]['payload']['critical']['health'])
        self.assertEqual(3, rows[-1]['payload']['published_sample_seq'])

    def test_explicit_siege_requests_remain_ordered_even_when_identical(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.ready(frontend, runtime)
        payload = {'forward': 0.0, 'siege_request': True}
        self.assertTrue(frontend.send_control(payload))
        self.assertTrue(frontend.send_control(payload))
        self.assertEqual(2, frontend._control_seq)

    def test_unpaired_visible_and_hidden_modes_do_not_connect_or_schedule(self):
        runtime = FakeRuntime()
        os.environ.clear()
        self.assertIsNone(player_driver.attach(runtime))
        self.assertEqual({}, runtime._runtime.bigworld.callbacks)
        self.factory.assert_not_called()
        os.environ[port_config.PLAYER_DRIVER_PORT_ENV] = '31555'
        os.environ[port_config.PLAYER_DRIVER_TOKEN_ENV] = 'private-test-token'
        for mode in ('simulation_worker', 'player_driver'):
            runtime._config['client_mode'] = mode
            self.assertIsNone(player_driver.attach(runtime))
        runtime._config['client_mode'] = 'player'
        runtime._replay_mode = True
        self.assertIsNone(player_driver.attach(runtime))
        self.factory.assert_not_called()

    def test_half_environment_is_an_error_frontend_and_never_a_fallback(self):
        os.environ.pop(port_config.PLAYER_DRIVER_TOKEN_ENV)
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.assertIsNotNone(frontend)
        self.assertFalse(frontend.ready)
        self.assertIn('endpoint', frontend.error)
        self.factory.assert_not_called()
        os.environ.clear()
        later = player_driver.attach(FakeRuntime(round_id=3))
        self.assertIsNotNone(later)
        self.assertIsNotNone(later.error)
        self.factory.assert_not_called()

    def test_bootstrap_is_frozen_and_passes_the_real_mirror_readers(self):
        runtime = FakeRuntime()
        runtime.client.vehicle = 'stale:garage-vehicle'
        runtime._config['vehicle'] = 'stale:garage-vehicle'
        frontend = player_driver.attach(runtime)
        self.assertIsNone(frontend.error)
        message = self.bridge.sent[0]
        self.assertEqual('driver_bind', message['type'])
        local = message['start']['players'][0]
        self.assertEqual(local['vehicle'], message['welcome']['vehicle'])
        self.assertNotEqual(runtime.client.vehicle, message['welcome']['vehicle'])
        other = DriverMirrorClient(local, None, lambda *unused: True)
        for key in ('welcome', 'roster', 'start'):
            self.assertTrue(other.admit(copy.deepcopy(message[key])), other.last_error)
        self.assertEqual(runtime.client.player_id, other.player_id)
        self.assertEqual(runtime.client.round_id, other.round_id)
        self.assertEqual('loading', other.phase)
        self.assertIsNone(other.sock)
        self.assertIsNone(other.thread)
        runtime._start_message['players'][0]['x'] = -999
        runtime._config['physics_tuning']['value'] = -999
        self.assertEqual(12.0, message['start']['players'][0]['x'])
        self.assertEqual(3, message['config']['physics_tuning']['value'])

    def test_missing_mounted_vehicle_rejects_binding_before_connection(self):
        runtime = FakeRuntime()
        runtime._start_message['players'][0].pop('vehicle_compact_descr')
        frontend = player_driver.attach(runtime)
        self.assertIsNotNone(frontend)
        self.assertIsNotNone(frontend.error)
        self.assertFalse(frontend.ready)
        self.factory.assert_not_called()
        self.assertTrue(runtime.client.connected)

    def test_handshake_pending_messages_are_frozen_and_follow_bind_in_order(self):
        self.bridge.connected = False
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        snapshot = room_message('snapshot')
        events = room_message('events', tick=2)
        self.assertTrue(frontend.forward_message(snapshot))
        self.assertTrue(frontend.forward_message(events))
        snapshot['server_tick'] = -100
        self.assertEqual([], self.bridge.sent)
        self.assertFalse(frontend.send_control({'forward': 1}))
        self.bridge.connected = True
        runtime._runtime.bigworld.step()
        self.assertEqual(['driver_bind', 'driver_message', 'driver_message'],
                         [r['type'] for r in self.bridge.sent])
        self.assertEqual([1, 2], [r['message']['server_tick'] for r in self.bridge.sent[1:]])

    def test_ready_gate_and_control_sequence_commit_only_admitted_commands(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.assertFalse(frontend.send_control({'forward': 1}))
        self.ready(frontend, runtime)
        self.assertEqual(1, frontend.bases[0]['team'])
        self.assertTrue(frontend.send_control({'forward': 1}))
        self.assertTrue(frontend.send_control({'forward': 0}))
        controls = [r for r in self.bridge.sent if r['type'] == 'driver_control']
        self.assertEqual([1, 2], [r['control_seq'] for r in controls])
        self.assertEqual([{'forward': 1}, {'forward': 0}], [r['payload'] for r in controls])
        self.bridge.fail_send = True
        self.assertFalse(frontend.send_control({'forward': -1}))
        self.assertEqual(2, frontend._control_seq)
        self.assertIsNotNone(frontend.error)
        self.assertTrue(runtime.client.connected)

    def test_poll_retains_receipts_in_order_without_applying_physical_state(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.ready(frontend, runtime)
        self.bridge.incoming.extend([self.receipt(frontend, 1), self.receipt(frontend, 2)])
        runtime._runtime.bigworld.step()
        self.assertEqual((100, 200, 300), runtime._local_position)
        self.assertEqual([1, 2], [r['sample_seq'] for r in frontend.drain()])
        self.assertEqual([], frontend.drain())

    def test_stale_round_generation_player_and_boolean_identity_are_ignored(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.bridge.incoming.extend([
            self.receipt(frontend, 1, generation=frontend.generation - 1),
            self.receipt(frontend, 2, round_id=frontend.round_id + 1),
            self.receipt(frontend, 3, player_id=frontend.player_id + 1),
            self.receipt(frontend, 4, generation=True),
            self.receipt(frontend, 5),
        ])
        runtime._runtime.bigworld.step()
        self.assertEqual([5], [r['sample_seq'] for r in frontend.drain()])

    def test_round_close_new_client_and_new_round_reuse_one_connection(self):
        runtime = FakeRuntime()
        first = player_driver.attach(runtime)
        self.assertIs(first, player_driver.attach(runtime))
        old_callback = next(iter(runtime._runtime.bigworld.callbacks.values()))
        first.close('garage')
        first.close('garage again')
        self.assertEqual({}, runtime._runtime.bigworld.callbacks)
        self.assertEqual('driver_unbind', self.bridge.sent[-1]['type'])
        new_runtime = FakeRuntime(round_id=3, bigworld=runtime._runtime.bigworld)
        second = player_driver.attach(new_runtime)
        self.assertGreater(second.generation, first.generation)
        self.assertEqual(0, self.bridge.stop_calls)
        self.factory.assert_called_once_with('127.0.0.1', 31555, 'private-test-token')
        sent = list(self.bridge.sent)
        old_callback()
        self.assertEqual(sent, self.bridge.sent)
        self.assertIs(player_driver._MANAGER.owner, second)
        self.assertEqual(['driver_bind', 'driver_unbind', 'driver_bind'],
                         [r['type'] for r in self.bridge.sent])

    def test_owner_generation_change_cancels_stale_callback_and_receipts(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.bridge.incoming.append(self.receipt(frontend, 1))
        runtime._generation += 1
        runtime._runtime.bigworld.step()
        self.assertTrue(frontend._closed)
        self.assertEqual([], frontend.drain())
        self.assertIsNone(player_driver._MANAGER.owner)
        self.assertEqual({}, runtime._runtime.bigworld.callbacks)

    def test_transport_eof_is_sticky_across_attach_and_keeps_preceding_receipts(self):
        runtime = FakeRuntime()
        first = player_driver.attach(runtime)
        self.bridge.incoming.append(self.receipt(first, 1))
        self.bridge.connected = False
        self.bridge.error = 'peer_closed'
        runtime._runtime.bigworld.step()
        self.assertEqual('peer_closed', first.error)
        self.assertEqual([1], [r['sample_seq'] for r in first.drain()])
        self.assertTrue(runtime.client.connected)
        second = player_driver.attach(FakeRuntime(round_id=3))
        self.assertEqual('peer_closed', second.error)
        self.assertEqual(1, self.factory.call_count)
        self.assertEqual(0, self.bridge.stop_calls)

    def test_driver_failure_marks_only_frontend_without_touching_room_client(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        self.ready(frontend, runtime)
        self.bridge.incoming.append({
            'type': 'driver_failed', 'generation': frontend.generation,
            'round_id': frontend.round_id, 'reason': 'native body missing',
        })
        runtime._runtime.bigworld.step()
        self.assertEqual('native body missing', frontend.error)
        self.assertFalse(frontend.ready)
        self.assertTrue(runtime.client.connected)
        self.assertEqual(0, self.bridge.stop_calls)

    def test_all_snapshots_and_intervening_events_are_reliable(self):
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        messages = [room_message('snapshot'), room_message('snapshot', tick=2),
                    room_message('events', tick=3), room_message('snapshot', tick=4)]
        for message in messages:
            self.assertTrue(frontend.forward_message(message))
        self.assertEqual(messages, [r['message'] for r in self.bridge.sent[1:]])
        self.assertFalse(frontend.forward_message(room_message('snapshot', round_id=99)))

    def test_pending_and_receipt_overflow_fail_instead_of_dropping_accepted_data(self):
        self.bridge.connected = False
        runtime = FakeRuntime()
        frontend = player_driver.attach(runtime)
        with mock.patch.object(player_driver, 'MAX_PENDING_MESSAGES', 1):
            self.assertTrue(frontend.forward_message(room_message('snapshot')))
            self.assertFalse(frontend.forward_message(room_message('events')))
        self.assertIsNotNone(frontend.error)
        frontend.close()
        self.bridge.connected = True
        new_runtime = FakeRuntime(round_id=3)
        next_frontend = player_driver.attach(new_runtime)
        self.bridge.incoming.extend([self.receipt(next_frontend, 1),
                                     self.receipt(next_frontend, 2)])
        with mock.patch.object(player_driver, 'MAX_PENDING_MESSAGES', 1):
            new_runtime._runtime.bigworld.step()
        self.assertIsNotNone(next_frontend.error)
        self.assertEqual([1], [r['sample_seq'] for r in next_frontend.drain()])
        self.assertTrue(new_runtime.client.connected)


if __name__ == '__main__':
    unittest.main()
