import json
import socket
import sys
from pathlib import Path
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import lan_client as lan_client_module
from gui.mods.offline_lan_0922.authority_worker import (
    AuthorityWorkerLANClient)
from gui.mods.offline_lan_0922.lan_client import LANClient
from effective_params_fixture import effective_params


class RecordingSocket(object):
    def __init__(self, fail=False):
        self.sent = []
        self.closed = False
        self.fail = fail

    def sendall(self, payload):
        if self.fail:
            raise socket.error('blocked transport')
        self.sent.append(payload)

    def close(self):
        self.closed = True


class QueueBigWorld(object):
    def callback(self, unused_delay, unused_callback):
        return 1

    def cancelCallback(self, unused_callback):
        pass


def wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class LanClientQueueTests(unittest.TestCase):
    def activate(self, sock=None, worker=False):
        if worker:
            client = AuthorityWorkerLANClient(
                '127.0.0.1', 28782, bigworld=QueueBigWorld())
        else:
            client = LANClient(
                '127.0.0.1', 28782, 'P', 'ussr:MS-1',
                bigworld=QueueBigWorld())
        client.sock = sock or RecordingSocket()
        client.running = True
        client.connected = True
        client.player_id = (lan_client_module.WORKER_AUTHORITY_ID
                            if worker else 1)
        client._stopping = False
        with client._outbound_lock:
            client._outbound_accepting = True
        return client

    def test_notify_reports_socket_to_main_thread_dispatch_delay(self):
        events = []
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            on_event=lambda kind, message: events.append((kind, message)),
            bigworld=QueueBigWorld())
        message = {
            'type': 'snapshot', '_client_received_time': 10.0}
        original_clock = lan_client_module._monotonic_time
        lan_client_module._monotonic_time = lambda: 10.017
        try:
            client._notify('snapshot', message)
        finally:
            lan_client_module._monotonic_time = original_clock

        self.assertAlmostEqual(
            0.017, events[0][1]['_client_dispatch_delay'])
        self.assertNotIn('_client_dispatch_delay', message)

    def test_receive_overflow_never_evicts_terminal_protocol_messages(self):
        client = self.activate()
        protected = [
            {'type': 'battle_receipt', 'receipt_id': 'server:7:1'},
            {'type': 'fire_intent', 'player_id': 1, 'intent_seq': 2},
            {'type': 'fire_intent_result', 'player_id': 1,
             'intent_seq': 2},
        ]
        original_limit = lan_client_module.MAX_PENDING_MESSAGES
        lan_client_module.MAX_PENDING_MESSAGES = 4
        try:
            client._pending = protected + [{'type': 'pong'}]
            incoming = {
                'type': 'fire_intent_result', 'player_id': 2,
                'intent_seq': 3,
            }

            client._queue_message(incoming)
        finally:
            lan_client_module.MAX_PENDING_MESSAGES = original_limit

        self.assertEqual(protected + [incoming], client._pending)

    def test_receive_overflow_preserves_each_manifest_lineage_barrier(self):
        client = self.activate()
        first_manifest = {
            'type': 'snapshot', 'round_id': 7, 'authority_epoch': 1,
            'bot_authority_id': -1, 'server_tick': 1,
            'bot_manifest': [{'id': 11}],
        }
        first_lean = dict(first_manifest, server_tick=2)
        first_lean.pop('bot_manifest')
        second_manifest = dict(
            first_manifest, round_id=8, authority_epoch=1,
            server_tick=1)
        second_lean = dict(second_manifest, server_tick=2)
        second_lean.pop('bot_manifest')
        incoming = dict(second_lean, server_tick=3)
        original_limit = lan_client_module.MAX_PENDING_MESSAGES
        lan_client_module.MAX_PENDING_MESSAGES = 4
        try:
            client._pending = [
                first_manifest, first_lean, second_manifest, second_lean]

            client._queue_message(incoming)
        finally:
            lan_client_module.MAX_PENDING_MESSAGES = original_limit

        self.assertEqual([
            (7, 1, True), (7, 2, False), (8, 1, True), (8, 3, False),
        ], [(value['round_id'], value['server_tick'],
             'bot_manifest' in value) for value in client._pending])

    def test_receive_overflow_replaces_same_lineage_manifest_barrier(self):
        client = self.activate()
        manifest = {
            'type': 'snapshot', 'round_id': 7, 'authority_epoch': 1,
            'bot_authority_id': -1, 'server_tick': 1,
            'bot_manifest': [{'id': 11}],
        }
        lean = dict(manifest, server_tick=2)
        lean.pop('bot_manifest')
        replacement = dict(manifest, server_tick=3)
        incoming = dict(lean, server_tick=4)
        original_limit = lan_client_module.MAX_PENDING_MESSAGES
        lan_client_module.MAX_PENDING_MESSAGES = 2
        try:
            client._pending = [manifest, lean]

            client._queue_message(replacement)
            client._queue_message(incoming)
        finally:
            lan_client_module.MAX_PENDING_MESSAGES = original_limit

        self.assertEqual([
            (3, True), (4, False),
        ], [(value['server_tick'], 'bot_manifest' in value)
            for value in client._pending])

    def test_receive_overflow_fails_closed_for_each_new_terminal_message(self):
        client = self.activate()
        protected = [
            {'type': 'battle_receipt', 'receipt_id': 'server:7:1'},
            {'type': 'fire_intent', 'player_id': 1, 'intent_seq': 2},
            {'type': 'fire_intent_result', 'player_id': 1,
             'intent_seq': 2},
        ]
        original_limit = lan_client_module.MAX_PENDING_MESSAGES
        lan_client_module.MAX_PENDING_MESSAGES = len(protected)
        try:
            for message_type in (
                    'battle_receipt', 'fire_intent',
                    'fire_intent_result'):
                client._pending = list(protected)
                with self.subTest(message_type=message_type):
                    with self.assertRaises(RuntimeError):
                        client._queue_message({'type': message_type})
                    self.assertEqual(protected, client._pending)
        finally:
            lan_client_module.MAX_PENDING_MESSAGES = original_limit

    def test_reliable_fifo_freezes_all_state_without_coalescing(self):
        client = self.activate()
        first = {'type': 'input', 'nested': {'values': [1, 2]}}

        self.assertTrue(client._send(first))
        first['nested']['values'][0] = 99
        self.assertTrue(client._send({'type': 'hit_report', 'shot_seq': 4}))
        self.assertTrue(client._send({'type': 'input', 'fire_seq': 5}))
        self.assertTrue(client._send({'type': 'bot_state', 'revision': 8}))
        self.assertTrue(client._send({'type': 'bot_state', 'revision': 9}))
        self.assertTrue(client._send({
            'type': 'bot_observation', 'contacts': []}))

        queued = list(client._outbound_queue)
        self.assertEqual(list(range(1, 7)), [item[0] for item in queued])
        self.assertEqual(
            ['input', 'hit_report', 'input', 'bot_state', 'bot_state',
             'bot_observation'],
            [item[1]['type'] for item in queued])
        self.assertEqual((1, 2), queued[0][1]['nested']['values'])

    def test_battle_ready_canonicalizes_spawn_planner_team_keys(self):
        client = self.activate()
        client.ready = True
        client.phase = 'loading'
        client.round_id = 7
        bases = {
            1: ((-10.0, -20.0),),
            2: ((10.0, 20.0),),
        }

        self.assertTrue(client.send_battle_ready(bases))

        queued = client._outbound_queue[0][1]
        self.assertEqual('battle_ready', queued['type'])
        self.assertEqual(7, queued['round_id'])
        self.assertEqual({'1', '2'}, set(queued['bases']))
        self.assertEqual(((-10.0, -20.0),), queued['bases']['1'])
        self.assertEqual(((10.0, 20.0),), queued['bases']['2'])

    def test_battle_receipt_ack_uses_reliable_queue_without_round_scope(self):
        client = self.activate()
        client.ready = True

        self.assertTrue(client.acknowledge_battle_receipt('server:7:1'))

        self.assertEqual({
            'type': 'battle_receipt_ack', 'receipt_id': 'server:7:1'},
            client._outbound_queue[0][1])

    def test_worker_destructible_result_accepts_64_identities_only(self):
        client = self.activate(worker=True)
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = lan_client_module.WORKER_AUTHORITY_ID
        token = [(7, item_index, None) for item_index in range(64)]

        self.assertTrue(client.send_player_destructible_contact_result(
            1, 3, True, token))
        queued = client._outbound_queue[0][1]
        self.assertEqual(64, len(queued['token']))
        self.assertFalse(client.send_player_destructible_contact_result(
            1, 4, True, token + [(7, 64, None)]))
        self.assertEqual(1, len(client._outbound_queue))

    def test_input_destructible_backlog_is_projected_to_bounded_window(self):
        client = self.activate()
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        contacts = [{'seq': seq} for seq in range(1, 18)]

        self.assertTrue(client.send_input(
            0.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0,
            shell_index=0, destructible_contacts=contacts))

        queued = client._outbound_queue[0][1]
        self.assertEqual(
            list(range(1, 17)),
            [row['seq'] for row in queued['destructible_contacts']])
        self.assertEqual(17, len(contacts))

    def test_invalid_battle_receipt_is_acked_without_stopping_waiting_client(self):
        client = self.activate()
        client.ready = True
        client.phase = 'waiting'
        client.account_key = 'a' * 32

        client._handle_message({
            'type': 'battle_receipt',
            'receipt_id': 'server:7:1',
            'account_key': client.account_key,
            'protocol': 'obsolete',
        })

        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertIsNone(client.last_error)
        self.assertEqual({
            'type': 'battle_receipt_ack', 'receipt_id': 'server:7:1'},
            client._outbound_queue[0][1])

    def test_valid_receipt_schema_ignores_stale_protocol_label(self):
        client = self.activate()
        client.ready = True
        client.phase = 'waiting'
        client.account_key = 'a' * 32
        notifications = []
        client.on_event = lambda kind, message: notifications.append(
            (kind, message))
        stats = dict((name, 0) for name in (
            'shots', 'direct_hits', 'piercings', 'damage',
            'damage_received', 'damage_blocked', 'assist_track',
            'assist_radio', 'assist_stun', 'kills', 'spotted',
            'capture_points', 'dropped_capture_points'))
        receipt = {
            'type': 'battle_receipt', 'protocol': 'obsolete',
            'receipt_id': 'server:7:1',
            'account_key': client.account_key,
            'player_name': 'P', 'vehicle': 'ussr:R11_MS-1',
            'map': '01_karelia', 'arena_unique_id': 7001,
            'round_id': 7, 'player_id': 1, 'team': 1, 'winner': 1,
            'duration': 60, 'premature_leave': False, 'death_reason': -1,
            'stats': stats,
            'rewards': {
                'credits': 0, 'xp': 0, 'free_xp': 0,
                'repair_cost': 0, 'ammo_cost': 0,
            },
            'public_results': [{
                'actor_kind': 'player', 'actor_id': 1, 'team': 1,
                'name': 'P', 'vehicle': 'ussr:R11_MS-1',
                'health': 1, 'death_reason': -1, 'xp': 0,
                'is_team_killer': False, 'killer_kind': '', 'killer_id': 0,
                'stats': stats,
            }],
            'interactions': [],
        }

        client._handle_message(receipt)

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual([('battle_receipt', receipt)], notifications)

    def test_bot_state_queue_projects_full_local_state_to_wire_contract(self):
        client = self.activate(worker=True)
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = lan_client_module.WORKER_AUTHORITY_ID
        states = []
        for index in range(29):
            states.append({
                'id': index + 1, 'team': 1 if index < 14 else 2,
                'slot': index % 15, 'name': 'Bot-%d' % index,
                'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
                'x': float(index), 'y': 0.0, 'z': float(index * 2),
                'yaw': 0.1, 'pitch': 0.03, 'roll': -0.02,
                'aim_yaw': 0.2, 'gun_pitch': -0.05,
                'movement_dir': 1, 'rotation_dir': 0,
                'fire_seq': 4, 'shell_index': 0,
                'next_shell_index': 1,
                'ammo_remaining': [28, 19, 9],
                'ammo_reload_pending': True,
                'reload_time': 0.25, 'reload_duration': 0.5,
                'clip': 2, 'clip_size': 3,
                'health': 700, 'alive': True,
                'critical': {
                    'devices': [], 'destroyed': [], 'crew_ko': [],
                    'fire': False, 'ammo_rack_death': False, 'events': []},
                'combat_revision': 8, 'combat_base_revision': 7,
                'combat_ack_seq': 4, 'combat_seq': 5,
                'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
                'death_reason': 0, 'display_health': 700,
                'shot_yaw': 0.21, 'shot_pitch': -0.04,
                'speed': 8.0, 'profile': {'class_tag': 'SPG'},
                'route': {'id': 'long', 'waypoints': [
                    {'x': float(point), 'y': 0.0, 'z': float(point * 2),
                     'hold': False}
                    for point in range(16)]},
                'collision_shape': {'half_length': 3.5, 'half_width': 1.7},
                'shot_origin': (1.0, 2.0, 3.0),
                'shot_velocity': (100.0, 20.0, 0.0),
                'shot_gravity': 9.81, 'shot_max_distance': 500.0,
                'shot_max_time_ms': 20000,
                'shot_proof_key': ('launch', index + 1),
            })
        full_message = {
            'type': 'bot_state', 'round_id': client.round_id,
            'bots': states,
        }
        unused_frozen, full_size = lan_client_module._freeze_outbound(
            full_message, [0])

        self.assertTrue(client.send_bot_state(
            states, sample_time_us=40000,
            source_batch_horizon_us=40000))

        queued = client._outbound_queue[0]
        self.assertEqual(40000, queued[1]['sample_time_us'])
        self.assertEqual(
            40000, queued[1]['source_batch_horizon_us'])
        queued_bots = queued[1]['bots']
        expected = {
            'id', 'x', 'y', 'z', 'yaw', 'pitch', 'roll',
            'aim_yaw', 'gun_pitch', 'speed',
            'movement_dir', 'rotation_dir', 'fire_seq', 'shell_index',
            'next_shell_index', 'ammo_remaining', 'ammo_reload_pending',
            'reload_time', 'reload_duration', 'clip', 'clip_size',
            'health', 'alive', 'critical', 'combat_base_revision',
            'combat_seq', 'combat_fire_elapsed', 'combat_fire_timer',
            'death_reason', 'display_health', 'shot_yaw', 'shot_pitch',
        }
        self.assertEqual(29, len(queued_bots))
        self.assertTrue(all(set(state) == expected for state in queued_bots))
        self.assertLess(queued[2], full_size)
        self.assertIn('profile', states[0])
        self.assertIn('shot_origin', states[0])
        self.assertIn('shot_proof_key', states[0])
        self.assertNotIn('profile', queued_bots[0])
        self.assertNotIn('shot_origin', queued_bots[0])
        self.assertNotIn('shot_proof_key', queued_bots[0])

    def test_bot_state_projection_rejects_half_shot_pair(self):
        client = self.activate(worker=True)
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = lan_client_module.WORKER_AUTHORITY_ID

        self.assertFalse(client.send_bot_state([{
            'id': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 100, 'alive': True, 'fire_seq': 1,
            'shot_yaw': 0.2,
        }]))
        self.assertEqual([], client._outbound_queue)

    def test_bot_state_projection_requires_boolean_atomic_reload_state(self):
        client = self.activate(worker=True)
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = lan_client_module.WORKER_AUTHORITY_ID
        state = {
            'id': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 100, 'alive': True, 'fire_seq': 1,
            'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [2, 1],
        }

        self.assertFalse(client.send_bot_state([state]))
        state['ammo_reload_pending'] = 1
        self.assertFalse(client.send_bot_state([state]))
        self.assertEqual([], client._outbound_queue)

    def test_bot_state_projection_rejects_partial_clip_checkpoint(self):
        state = {
            'id': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 100, 'alive': True, 'fire_seq': 1,
            'reload_time': 0.2, 'reload_duration': 0.5,
            'clip': 1,
        }

        self.assertIsNone(lan_client_module.project_bot_state(state))

    def test_sender_owns_json_encoding_and_socket_write(self):
        client = self.activate()
        generation = client._transport_generation
        calls = []
        original_dumps = lan_client_module.json.dumps

        def recording_dumps(message, *args, **kwargs):
            calls.append((message.get('type'), threading.current_thread()))
            return original_dumps(message, *args, **kwargs)

        lan_client_module.json.dumps = recording_dumps
        sender = threading.Thread(
            target=client._sender_worker,
            args=(client.sock, generation), name='queue-test-sender')
        sender.setDaemon(True)
        client._sender_thread = sender
        sender.start()
        try:
            caller = threading.current_thread()
            self.assertTrue(client._send({'type': 'input', 'value': 1}))
            self.assertTrue(wait_until(lambda: len(client.sock.sent) == 1))
            self.assertEqual(['input'], [kind for kind, unused in calls])
            self.assertTrue(all(thread is not caller
                                for unused, thread in calls))
        finally:
            lan_client_module.json.dumps = original_dumps
            client.stop()

    def test_partial_send_resumes_after_socket_timeout_without_duplication(self):
        class PartialSocket(RecordingSocket):
            def __init__(self):
                RecordingSocket.__init__(self)
                self.bytes = b''
                self.calls = 0

            def send(self, payload):
                self.calls += 1
                if self.calls == 1:
                    self.bytes += payload[:4]
                    return 4
                if self.calls in (2, 3):
                    raise socket.timeout()
                self.bytes += payload
                return len(payload)

        sock = PartialSocket()
        client = self.activate(sock)

        self.assertTrue(client._send_wire(
            {'type': 'input', 'round_id': 7}, sock,
            client._transport_generation))
        self.assertEqual(
            {'type': 'input', 'round_id': 7},
            json.loads(sock.bytes.decode('utf-8')))
        self.assertEqual(4, sock.calls)

    def test_send_stall_has_bounded_transport_failure(self):
        class StalledSocket(RecordingSocket):
            def send(self, unused_payload):
                raise socket.timeout()

        sock = StalledSocket()
        client = self.activate(sock)
        samples = iter((10.0, 15.1))
        original_clock = lan_client_module._monotonic_time
        lan_client_module._monotonic_time = lambda: next(samples)
        try:
            self.assertFalse(client._send_wire(
                {'type': 'input'}, sock,
                client._transport_generation))
        finally:
            lan_client_module._monotonic_time = original_clock

        self.assertIn('did not accept client messages', client.last_error)

    def test_oversized_nested_payload_is_rejected_before_enqueue(self):
        client = self.activate()
        message = {
            'type': 'input',
            'reported_critical': {
                'devices': [{'name': 'x' * lan_client_module.MAX_MESSAGE_BYTES}],
            },
        }

        self.assertFalse(client._send(message))
        self.assertEqual([], client._outbound_queue)
        self.assertTrue(client.running)

    def test_nonfinite_nested_float_is_rejected_before_enqueue(self):
        client = self.activate()

        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.assertFalse(client._send({
                    'type': 'bot_state', 'bots': [{'speed': value}]}))

        self.assertEqual([], client._outbound_queue)
        self.assertTrue(client.running)

    def test_del_character_wire_size_is_bounded_before_enqueue(self):
        text = '\x7f' * 1000
        estimated = lan_client_module._json_text_size(text)
        encoded = json.dumps(text, separators=(',', ':')).encode('utf-8')

        self.assertGreaterEqual(estimated, len(encoded))

        client = self.activate()
        oversized = '\x7f' * (
            lan_client_module.MAX_MESSAGE_BYTES // 6 + 1)
        self.assertFalse(client._send({'type': oversized}))
        self.assertEqual([], client._outbound_queue)
        self.assertTrue(client.running)

    def test_queue_pressure_preserves_transport_and_accepted_fifo(self):
        client = self.activate()
        sock = client.sock
        original_limit = lan_client_module.MAX_OUTBOUND_MESSAGES
        lan_client_module.MAX_OUTBOUND_MESSAGES = 2
        try:
            self.assertTrue(client._send({'type': 'input', 'value': 1}))
            self.assertTrue(client._send({'type': 'input', 'value': 2}))
            self.assertFalse(client._send({'type': 'input', 'value': 3}))
        finally:
            lan_client_module.MAX_OUTBOUND_MESSAGES = original_limit

        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertFalse(sock.closed)
        self.assertEqual([1, 2], [
            item[1]['value'] for item in client._outbound_queue])
        self.assertIsNone(client.last_error)

    def test_peer_eof_is_quiet_only_after_normal_battle_finish(self):
        client = self.activate()
        generation = client._transport_generation
        sock = client.sock
        client.phase = 'battle'

        client.combat_phase = 'finished'
        self.assertFalse(client._record_peer_close(generation, sock))
        self.assertIsNone(client.last_error)

        client.combat_phase = 'battle'
        self.assertTrue(client._record_peer_close(generation, sock))
        self.assertEqual('server closed the connection', client.last_error)

    def test_sender_failure_aborts_transport_and_discards_backlog(self):
        client = self.activate(RecordingSocket(fail=True))
        sock = client.sock
        generation = client._transport_generation
        sender = threading.Thread(
            target=client._sender_worker,
            args=(client.sock, generation), name='queue-failure-sender')
        sender.setDaemon(True)
        client._sender_thread = sender
        sender.start()

        self.assertTrue(client._send({'type': 'input'}))
        self.assertTrue(wait_until(lambda: not client.running))
        self.assertFalse(client.connected)
        self.assertTrue(sock.closed)
        self.assertEqual([], client._outbound_queue)
        self.assertIn('blocked transport', client.last_error)

    def test_failed_fire_enqueue_does_not_consume_fire_sequence(self):
        source_shot = {
            'speed': 100.0, 'gravity': 9.81,
            'maxDistance': 500.0, 'piercingPower': [100.0, 100.0],
            'deadeye': False,
            'shell': {
                'kind': 'ARMOR_PIERCING', 'caliber': 45.0,
                'damage': [110.0, 110.0], 'explosionRadius': 0.0,
            },
        }
        client = self.activate()
        client.ready = True
        client.phase = 'battle'
        client.round_id = 3
        self.assertTrue(client.send_input(
            0.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0,
            shell_index=0))
        original_limit = lan_client_module.MAX_OUTBOUND_MESSAGES
        lan_client_module.MAX_OUTBOUND_MESSAGES = 1
        try:
            self.assertIsNone(client.send_fire(
                position=[0.0, 1.0, 0.0], velocity=[100.0, 0.0, 0.0],
                gravity=9.81, max_distance=500.0, max_time_ms=5000,
                source_shot=source_shot))
        finally:
            lan_client_module.MAX_OUTBOUND_MESSAGES = original_limit
        self.assertEqual(0, client._fire_intent_seq)

        client = self.activate()
        client.ready = True
        client.phase = 'battle'
        client.round_id = 3
        self.assertTrue(client.send_input(
            0.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0,
            shell_index=0))
        self.assertEqual(1, client.send_fire(
            position=[0.0, 1.0, 0.0], velocity=[100.0, 0.0, 0.0],
            gravity=9.81, max_distance=500.0, max_time_ms=5000,
            source_shot=source_shot))
        self.assertEqual(1, client._fire_intent_seq)
        self.assertEqual(
            'fire_intent', client._outbound_queue[1][1]['type'])
        self.assertEqual(1, client._outbound_queue[1][1]['intent_seq'])
        self.assertEqual(1, client._outbound_queue[1][1]['input_seq'])

    def test_stop_sends_best_effort_leave_and_clears_queue(self):
        client = self.activate()
        sock = client.sock
        self.assertTrue(client._send({'type': 'input'}))
        client._queue_message({'type': 'snapshot'})

        client.stop()

        self.assertEqual([{'type': 'leave'}], [
            json.loads(payload.decode('utf-8')) for payload in sock.sent])
        self.assertEqual([], client._outbound_queue)
        self.assertEqual(0, client._outbound_bytes)
        self.assertEqual([], client._pending)
        self.assertTrue(sock.closed)

    def test_stop_never_waits_for_busy_sender_lock(self):
        client = self.activate()
        self.assertTrue(client._send_lock.acquire(False))
        started = time.time()
        try:
            client.stop()
        finally:
            client._send_lock.release()

        self.assertLess(time.time() - started, 0.15)
        self.assertEqual([], client.sock.sent if client.sock else [])

    def test_worker_keeps_hello_first_then_starts_sender(self):
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            bigworld=QueueBigWorld(), effective_params=effective_params())
        outer = self

        class ConnectedSocket(RecordingSocket):
            def __init__(self):
                RecordingSocket.__init__(self)
                self.closed_event = threading.Event()

            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

            def sendall(self, payload):
                message = json.loads(payload.decode('utf-8'))
                if not self.sent:
                    outer.assertEqual('hello', message['type'])
                    outer.assertFalse(client.connected)
                self.sent.append(payload)

            def recv(self, unused_size):
                if self.closed_event.wait(0.01):
                    return b''
                raise socket.timeout()

            def close(self):
                self.closed = True
                self.closed_event.set()

        connected_socket = ConnectedSocket()
        original_socket = lan_client_module.socket.socket
        lan_client_module.socket.socket = (
            lambda *unused_args: connected_socket)
        try:
            self.assertTrue(client.start())
            self.assertTrue(wait_until(lambda: (
                client.connected and client._sender_thread is not None)))
            self.assertTrue(client._send({'type': 'ping', 'seq': 1}))
            self.assertTrue(wait_until(lambda: len(connected_socket.sent) >= 2))
        finally:
            client.stop()
            lan_client_module.socket.socket = original_socket

        messages = [json.loads(payload.decode('utf-8'))
                    for payload in connected_socket.sent]
        self.assertEqual('hello', messages[0]['type'])
        self.assertEqual('ping', messages[1]['type'])
        self.assertEqual(1, messages[1]['seq'])
        self.assertEqual([], client._outbound_queue)

    def test_stale_worker_cannot_replace_reconnected_socket(self):
        client = self.activate()
        new_socket = client.sock
        client._transport_generation = 4

        class StaleSocket(RecordingSocket):
            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

        stale_socket = StaleSocket()
        original_socket = lan_client_module.socket.socket
        lan_client_module.socket.socket = lambda *unused_args: stale_socket
        try:
            client._worker(3)
        finally:
            lan_client_module.socket.socket = original_socket

        self.assertIs(new_socket, client.sock)
        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertTrue(stale_socket.closed)
        self.assertEqual([], stale_socket.sent)

    def test_stop_start_generation_barrier_rejects_old_hello_worker(self):
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            bigworld=QueueBigWorld(), effective_params=effective_params())
        old_at_publish = threading.Event()
        release_old = threading.Event()
        old_finished = threading.Event()

        class ReconnectSocket(RecordingSocket):
            def __init__(self, label):
                RecordingSocket.__init__(self)
                self.label = label
                self.closed_event = threading.Event()

            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

            def recv(self, unused_size):
                if self.closed_event.wait(0.005):
                    return b''
                raise socket.timeout()

            def close(self):
                self.closed = True
                self.closed_event.set()

        old_socket = ReconnectSocket('old')
        new_socket = ReconnectSocket('new')
        sockets = [old_socket, new_socket]
        original_socket = lan_client_module.socket.socket
        original_publish = client._publish_connected_transport

        def socket_factory(*unused_args):
            return sockets.pop(0)

        def publish_with_old_barrier(sock, generation):
            if generation == 1:
                old_at_publish.set()
                release_old.wait(1.0)
                try:
                    return original_publish(sock, generation)
                finally:
                    old_finished.set()
            return original_publish(sock, generation)

        lan_client_module.socket.socket = socket_factory
        client._publish_connected_transport = publish_with_old_barrier
        try:
            self.assertTrue(client.start())
            self.assertTrue(old_at_publish.wait(1.0))
            self.assertIs(old_socket, client.sock)
            self.assertFalse(client.connected)

            client.stop()
            self.assertTrue(old_socket.closed)
            self.assertTrue(client.start())
            self.assertTrue(wait_until(lambda: (
                client._transport_generation == 2 and
                client.sock is new_socket and client.connected and
                client._sender_thread is not None)))
            new_sender = client._sender_thread

            release_old.set()
            self.assertTrue(old_finished.wait(1.0))
            self.assertIs(new_socket, client.sock)
            self.assertTrue(client.connected)
            self.assertTrue(client._outbound_accepting)
            self.assertIs(new_sender, client._sender_thread)
            self.assertIsNone(client.last_error)
            self.assertTrue(client._send({'type': 'ping', 'seq': 2}))
            self.assertTrue(wait_until(lambda: len(new_socket.sent) >= 2))
            self.assertEqual(
                ['hello', 'ping'],
                [json.loads(payload.decode('utf-8'))['type']
                 for payload in new_socket.sent[:2]])
        finally:
            release_old.set()
            client.stop()
            client._publish_connected_transport = original_publish
            lan_client_module.socket.socket = original_socket

    def test_stale_sender_failure_cannot_poison_new_generation(self):
        entered_send = threading.Event()
        release_send = threading.Event()

        class LateFailSocket(RecordingSocket):
            def sendall(self, unused_payload):
                entered_send.set()
                release_send.wait(1.0)
                raise socket.error('late old-generation failure')

        old_socket = LateFailSocket()
        client = self.activate(old_socket)
        old_generation = client._transport_generation
        results = []
        sender = threading.Thread(target=lambda: results.append(
            client._send_wire(
                {'type': 'input'}, old_socket, old_generation)))
        sender.setDaemon(True)
        sender.start()
        self.assertTrue(entered_send.wait(1.0))

        new_socket = RecordingSocket()
        with client._outbound_lock:
            client._transport_generation += 1
            client.sock = new_socket
            client.connected = True
            client.running = True
            client._stopping = False
            client.last_error = None
        release_send.set()
        sender.join(1.0)

        self.assertEqual([None], results)
        self.assertIs(new_socket, client.sock)
        self.assertTrue(client.connected)
        self.assertIsNone(client.last_error)

    def test_message_frozen_during_reconnect_cannot_enter_new_queue(self):
        entered_freeze = threading.Event()
        release_freeze = threading.Event()
        client = self.activate()
        original_freeze = lan_client_module._freeze_outbound

        def blocking_freeze(value, budget, depth=0):
            if depth == 0:
                entered_freeze.set()
                release_freeze.wait(1.0)
            return original_freeze(value, budget, depth)

        results = []
        lan_client_module._freeze_outbound = blocking_freeze
        sender = threading.Thread(target=lambda: results.append(
            client._send({'type': 'input', 'round_id': 1})))
        sender.setDaemon(True)
        try:
            sender.start()
            self.assertTrue(entered_freeze.wait(1.0))
            with client._outbound_lock:
                client._transport_generation += 1
                client.sock = RecordingSocket()
                client.running = True
                client.connected = True
                client._stopping = False
                client._outbound_accepting = True
                client._outbound_queue = []
                client._outbound_bytes = 0
            release_freeze.set()
            sender.join(1.0)
        finally:
            release_freeze.set()
            lan_client_module._freeze_outbound = original_freeze

        self.assertEqual([False], results)
        self.assertEqual([], client._outbound_queue)
        self.assertEqual(0, client._outbound_bytes)


if __name__ == '__main__':
    unittest.main()
