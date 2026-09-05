import json
from collections import OrderedDict
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest import mock


SERVER_ROOT = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_ROOT))

import lan_battle_server as server_module  # noqa: E402
from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, ClientHandler,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY, PROJECTILE_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY, Player, PREBATTLE_SECONDS,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    REPLICA_SNAPSHOT_TICKS,
    SimulationWorker,
    SIMULATION_WORKER_AUTHORITY_ID, SIMULATION_WORKER_CAPABILITY,
    SIMULATION_WORKER_ROLE, TICK_HZ, ThreadedTCPServer,
)
from effective_params_fixture import effective_params


class _Connection(object):
    def __init__(self):
        self.messages = []

    def sendall(self, payload):
        self.messages.append(json.loads(payload.decode('utf-8')))


class _BlockingConnection(object):
    def __init__(self):
        self.messages = []
        self.started = threading.Event()
        self.release = threading.Event()

    def sendall(self, payload):
        message = json.loads(payload.decode('utf-8'))
        if not self.messages:
            self.started.set()
            if not self.release.wait(2.0):
                raise socket.timeout('synthetic slow peer')
        self.messages.append(message)


class _ShortStallConnection(object):
    def __init__(self):
        self.payload = b''
        self.calls = 0

    def send(self, payload):
        self.calls += 1
        if self.calls == 1:
            raise socket.timeout()
        count = min(7, len(payload))
        self.payload += payload[:count]
        return count


class _Peer(object):
    def __init__(self, address):
        self.socket = socket.create_connection(address, timeout=2.0)
        self.socket.settimeout(2.0)
        self.stream = self.socket.makefile('rwb')

    def send(self, message):
        payload = (json.dumps(message, separators=(',', ':')) + '\n')
        self.stream.write(payload.encode('utf-8'))
        self.stream.flush()

    def receive_until(self, kind, limit=32):
        for _unused in range(limit):
            line = self.stream.readline()
            if not line:
                raise AssertionError('connection closed before %s' % kind)
            message = json.loads(line.decode('utf-8'))
            if message.get('type') == kind:
                return message
        raise AssertionError('did not receive %s' % kind)

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.stream.close()
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass


def _worker_hello():
    return {
        'type': 'hello', 'protocol': 5,
        'role': SIMULATION_WORKER_ROLE,
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            SIMULATION_WORKER_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY,
            RICOCHET_CONTINUATION_CAPABILITY],
    }


def _player_hello(name='Human'):
    # Deliberately omit role: protocol-v5 player hellos predate workers.
    return {
        'type': 'hello', 'protocol': 5,
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY,
            RICOCHET_CONTINUATION_CAPABILITY],
        'name': name, 'vehicle': 'ussr:R11_MS-1', 'max_health': 90,
        'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params(),
    }


def _manifest(roster):
    result = []
    for entry in roster:
        team = int(entry['team'])
        slot = int(entry['slot'])
        result.append({
            'id': int(entry['id']), 'team': team, 'slot': slot,
            'name': entry['name'], 'vehicle': 'ussr:R11_MS-1',
            'health': 90, 'max_health': 90,
            'x': float(slot * 12), 'y': 0.0,
            'z': -35.0 if team == 1 else 35.0,
            'yaw': 0.0 if team == 1 else 3.141592,
            'world_pose': True, 'profile': {},
            'reload_time': 3.0, 'reload_duration': 3.0,
            'route': {'id': 'worker-test', 'waypoints': []},
        })
    return result


def _human_profiles(players):
    return [{
        'id': int(player['id']),
        'vehicle': player['vehicle'],
        'mass': 25000.0,
        'shape': [1.5, 3.5, -0.8, 1.6],
        'ram_profile': {
            'spall_coefficient': 1.0,
            'ramming_bonus': 0.0,
        },
    } for player in players]


def _bot_publication(manifest, x_offset=0.0):
    return [{
        'id': entry['id'],
        'x': entry['x'] + x_offset, 'y': entry['y'], 'z': entry['z'],
        'yaw': entry['yaw'], 'health': entry['health'], 'alive': True,
        'fire_seq': 0, 'critical': {},
        'reload_time': entry['reload_time'],
        'reload_duration': entry['reload_duration'],
        'combat_base_revision': 0, 'combat_seq': 0,
        'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        'stun_end_server_time_ms': 0,
    } for entry in manifest]


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('timed out waiting for server state')


class SimulationWorkerStateTests(unittest.TestCase):
    def test_worker_loading_timeout_has_separate_startup_grace(self):
        self.assertGreater(
            server_module.SIMULATION_WORKER_LOADING_TIMEOUT_SECONDS,
            server_module.SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS)

    def test_peer_reset_is_classified_as_close_not_server_fault(self):
        self.assertTrue(server_module._peer_closed_socket(OSError(10054, 'x')))
        self.assertTrue(server_module._peer_closed_socket(OSError(54, 'x')))
        self.assertFalse(server_module._peer_closed_socket(OSError(13, 'x')))

    def test_worker_join_requires_ricochet_capability(self):
        state = BattleState(map_name='01_karelia')
        hello = _worker_hello()
        hello['capabilities'].remove(RICOCHET_CONTINUATION_CAPABILITY)

        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), hello)

        self.assertIsNone(worker)
        self.assertEqual('unsupported_capabilities', error)

    def test_unknown_build_labels_are_rejected_before_mutation(self):
        state = BattleState(map_name='01_karelia')
        worker_hello = _worker_hello()
        worker_hello['client_build'] = 'launcher-local-worker'

        worker, worker_error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), worker_hello)
        player_hello = _player_hello()
        player_hello['client_build'] = 'launcher-local-player'
        player, player_error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), player_hello)

        self.assertIsNone(worker)
        self.assertEqual('unsupported_client_build', worker_error)
        self.assertIsNone(player)
        self.assertEqual('unsupported_client_build', player_error)
        self.assertIsNone(state.simulation_worker)
        self.assertEqual({}, state.players)
        self.assertIsNone(state.client_build)

    def test_modern_player_join_requires_exact_max_health_before_mutation(self):
        invalid_values = (
            ('missing', None), ('bool', True), ('float', 90.0),
            ('string', '90'), ('zero', 0), ('negative', -1),
            ('overflow', 100001),
        )
        for name, value in invalid_values:
            with self.subTest(name=name):
                state = BattleState(map_name='01_karelia')
                hello = _player_hello()
                if name == 'missing':
                    hello.pop('max_health')
                else:
                    hello['max_health'] = value
                revision = state.state_revision

                player, error = state.add_player(
                    _Connection(), ('127.0.0.1', 1000), hello)

                self.assertIsNone(player)
                self.assertEqual('invalid_max_health', error)
                self.assertEqual({}, state.players)
                self.assertEqual(1, state.next_id)
                self.assertIsNone(state.client_build)
                self.assertEqual(revision, state.state_revision)

    def test_retired_client_build_is_rejected_before_mutation(self):
        state = BattleState(map_name='01_karelia')

        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1000), {
                'type': 'hello', 'protocol': 5,
                'client_build': 'wot-0.8.2', 'name': 'Legacy',
                'max_health': 90.75,
            })

        self.assertIsNone(player)
        self.assertEqual('unsupported_client_build', error)
        self.assertEqual({}, state.players)
        self.assertIsNone(state.client_build)

    def test_lifecycle_broadcasts_do_not_wait_for_a_slow_socket(self):
        def assert_nonblocking(publish):
            connection = _BlockingConnection()
            player = Player(1, connection, ('127.0.0.1', 1000))
            player._force_async_outbox = True
            player.participating = True
            state = BattleState(map_name='01_karelia', team_size=1)
            state.client_build = CLIENT_BUILD_0922
            state.phase = 'loading'
            state.players = {player.player_id: player}
            state.host_player_id = player.player_id
            completed = threading.Event()
            errors = []

            def run():
                try:
                    publish(state)
                except Exception as error:
                    errors.append(error)
                finally:
                    completed.set()

            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(connection.started.wait(1.0))
                self.assertTrue(completed.wait(0.25))
            finally:
                connection.release.set()
                thread.join(2.0)
                player.disconnect()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)

        publishers = (
            lambda state: state.broadcast({
                'type': 'roster', 'round_id': state.round_id}),
            lambda state: state.broadcast_current_roster(),
            lambda state: state.broadcast_loading_transition({
                'type': 'battle_start', 'round_id': state.round_id}),
        )
        for publish in publishers:
            assert_nonblocking(publish)

    def test_outbox_resumes_one_frame_after_short_socket_stall(self):
        connection = _ShortStallConnection()
        player = Player(1, connection, ('127.0.0.1', 1000))
        player._force_async_outbox = True

        self.assertTrue(player.offer_reliable({
            'type': 'events', 'round_id': 1, 'server_tick': 1,
            'events': []}))
        _wait_until(lambda: connection.payload.endswith(b'\n'))

        self.assertTrue(player.connected)
        self.assertEqual(
            {'type': 'events', 'round_id': 1, 'server_tick': 1,
             'events': []},
            json.loads(connection.payload.decode('utf-8')))
        player.disconnect()

    def test_outbox_rechecks_disconnect_after_waiting_for_queue_lock(self):
        for method_name in ('offer_reliable', 'offer_snapshot'):
            player = Player(1, _Connection(), ('127.0.0.1', 1000))
            player._force_async_outbox = True
            producer_entered = threading.Event()
            producer_release = threading.Event()
            original_ensure = player._ensure_outbox

            def gated_ensure():
                if threading.current_thread().name == 'racing-offer':
                    producer_entered.set()
                    if not producer_release.wait(1.0):
                        raise AssertionError('outbox race test timed out')
                return original_ensure()

            player._ensure_outbox = gated_ensure
            results = []
            message = {
                'type': 'snapshot' if method_name == 'offer_snapshot'
                else 'events',
                'round_id': 1,
                'server_tick': 1,
            }
            producer = threading.Thread(
                target=lambda: results.append(
                    getattr(player, method_name)(message)),
                name='racing-offer')
            producer.start()
            self.assertTrue(producer_entered.wait(1.0))
            player._mark_disconnected()
            producer_release.set()
            producer.join(1.0)

            self.assertFalse(producer.is_alive())
            self.assertEqual([False], results)
            self.assertEqual([], list(player._outbox_reliable))
            self.assertIsNone(player._outbox_snapshot)

    def test_stale_player_endpoint_cannot_remove_reused_identity(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        stale = Player(1, _Connection(), ('127.0.0.1', 1000))
        current = Player(1, _Connection(), ('127.0.0.1', 1001))
        state.players = {1: current}

        removed, reset = state._remove_endpoint(stale)
        self.assertIsNone(removed)
        self.assertFalse(reset)
        self.assertIs(current, state.players[1])

        removed, reset = state.remove_player(1, expected=stale)
        self.assertIsNone(removed)
        self.assertFalse(reset)
        self.assertIs(current, state.players[1])

    @staticmethod
    def _paused_event_publication():
        entered = threading.Event()
        release = threading.Event()

        def pause(unused_event, unused_players, unused_bots):
            entered.set()
            if not release.wait(2.0):
                raise RuntimeError('event publication test timed out')
            return None

        return entered, release, pause

    def test_worker_failure_roster_fences_captured_old_epoch_events(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        worker = SimulationWorker(
            _Connection(), ('127.0.0.1', 1000))
        state.simulation_worker = worker
        player_connection = _Connection()
        player = Player(1, player_connection, ('127.0.0.1', 1001))
        state.players = {1: player}
        state.host_player_id = 1
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = []
        state.pending_events.append({
            'kind': 'authority',
            'player_id': SIMULATION_WORKER_AUTHORITY_ID,
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
        })
        entered, release, pause = self._paused_event_publication()
        with mock.patch.object(
                server_module, '_server_event_log_message',
                side_effect=pause):
            tick = threading.Thread(
                target=state.tick_once, args=(1.0 / TICK_HZ,))
            tick.start()
            self.assertTrue(entered.wait(1.0))
            state.remove_simulation_worker(worker)
            roster = state.broadcast_current_roster()
            release.set()
            tick.join(2.0)

        self.assertFalse(tick.is_alive())
        roster_index = player_connection.messages.index(roster)
        self.assertFalse(any(
            message.get('type') == 'events' and
            message.get('authority_epoch') < roster['authority_epoch']
            for message in player_connection.messages[roster_index + 1:]))

    def test_membership_roster_fences_captured_old_player_snapshot(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        worker = SimulationWorker(
            _Connection(), ('127.0.0.1', 1000))
        state.simulation_worker = worker
        first_connection = _Connection()
        first = Player(1, first_connection, ('127.0.0.1', 1001))
        second = Player(2, _Connection(), ('127.0.0.1', 1002), team=2)
        state.players = {1: first, 2: second}
        state.host_player_id = 1
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = []
        state.pending_events.append({
            'kind': 'authority',
            'player_id': SIMULATION_WORKER_AUTHORITY_ID,
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
        })
        entered, release, pause = self._paused_event_publication()
        with mock.patch.object(
                server_module, '_server_event_log_message',
                side_effect=pause):
            tick = threading.Thread(
                target=state.tick_once, args=(1.0 / TICK_HZ,))
            tick.start()
            self.assertTrue(entered.wait(1.0))
            state.remove_player(second.player_id)
            roster = state.broadcast_current_roster()
            release.set()
            tick.join(2.0)

        self.assertFalse(tick.is_alive())
        roster_index = first_connection.messages.index(roster)
        self.assertFalse(any(
            message.get('type') == 'snapshot' and
            second.player_id in [
                value.get('id') for value in message.get('players', ())]
            for message in first_connection.messages[roster_index + 1:]))

    def test_outbox_isolates_slow_peer_and_coalesces_unsent_snapshots(self):
        connection = _BlockingConnection()
        player = Player(1, connection, ('127.0.0.1', 1000))
        player._force_async_outbox = True

        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 1}))
        self.assertTrue(connection.started.wait(1.0))
        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 2}))
        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 3}))
        self.assertTrue(player.offer_reliable({
            'type': 'events', 'round_id': 1, 'server_tick': 4,
            'events': []}))
        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 4}))

        connection.release.set()
        _wait_until(lambda: len(connection.messages) == 3)

        self.assertEqual(
            [('snapshot', 1), ('events', 4), ('snapshot', 4)],
            [(message['type'], message['server_tick'])
             for message in connection.messages])
        player.connected = False
        with player._outbox_condition:
            player._outbox_condition.notify_all()

    def test_same_round_roster_fences_an_unsent_snapshot(self):
        connection = _BlockingConnection()
        player = Player(1, connection, ('127.0.0.1', 1000))
        player._force_async_outbox = True

        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 1}))
        self.assertTrue(connection.started.wait(1.0))
        self.assertTrue(player.offer_snapshot({
            'type': 'snapshot', 'round_id': 1, 'server_tick': 2}))
        self.assertTrue(player.offer_reliable({
            'type': 'roster', 'round_id': 1, 'players': []}))

        connection.release.set()
        _wait_until(lambda: len(connection.messages) == 2)
        self.assertEqual(
            ['snapshot', 'roster'],
            [message['type'] for message in connection.messages])
        player.disconnect()

    def test_replicas_are_15_hz_and_worker_loss_terminates_round(self):
        self.assertEqual(2, REPLICA_SNAPSHOT_TICKS)
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        worker_connection = _Connection()
        worker = SimulationWorker(
            worker_connection, ('127.0.0.1', 1000))
        state.simulation_worker = worker
        first_connection = _Connection()
        second_connection = _Connection()
        first = Player(
            1, first_connection, ('127.0.0.1', 1001),
            capabilities=(LEAN_SNAPSHOT_MANIFEST_CAPABILITY,))
        second = Player(
            2, second_connection, ('127.0.0.1', 1002), team=2,
            capabilities=(LEAN_SNAPSHOT_MANIFEST_CAPABILITY,))
        state.players = {1: first, 2: second}
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = []

        for _unused in range(5):
            state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(5, sum(
            message.get('type') == 'snapshot'
            for message in worker_connection.messages))
        self.assertEqual(3, sum(
            message.get('type') == 'snapshot'
            for message in first_connection.messages))
        self.assertEqual(3, sum(
            message.get('type') == 'snapshot'
            for message in second_connection.messages))
        first_snapshots = [
            message for message in first_connection.messages
            if message.get('type') == 'snapshot']
        self.assertIn('bot_manifest', first_snapshots[0])
        self.assertNotIn('bot_manifest', first_snapshots[1])

        removed, round_failed = state.remove_simulation_worker(worker)
        self.assertIs(worker, removed)
        self.assertTrue(round_failed)
        state.tick_once(1.0 / TICK_HZ)

        self.assertIsNone(state.bot_authority_id)
        self.assertEqual('worker_disconnected',
                         state.battle_result['reason'])
        self.assertFalse(state.result_receipts)
        self.assertEqual(4, sum(
            message.get('type') == 'snapshot'
            for message in first_connection.messages))
        self.assertEqual(4, sum(
            message.get('type') == 'snapshot'
            for message in second_connection.messages))

    def test_legacy_v5_replica_keeps_manifest_in_every_snapshot(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        connection = _Connection()
        legacy = Player(1, connection, ('127.0.0.1', 1000))
        state.players = {1: legacy}
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = []

        for _unused in range(3):
            state.tick_once(1.0 / TICK_HZ)

        snapshots = [
            message for message in connection.messages
            if message.get('type') == 'snapshot']
        self.assertEqual(2, len(snapshots))
        self.assertTrue(all(
            'bot_manifest' in message for message in snapshots))

    def test_endpoint_send_clamps_server_time_within_each_round(self):
        player_connection = _Connection()
        worker_connection = _Connection()
        endpoints = (
            (Player(1, player_connection, ('127.0.0.1', 1000)),
             player_connection),
            (SimulationWorker(
                worker_connection, ('127.0.0.1', 1001)),
             worker_connection),
        )

        for endpoint, connection in endpoints:
            self.assertTrue(endpoint.send({
                'type': 'snapshot', 'round_id': 7,
                'server_time_ms': 101}))
            self.assertTrue(endpoint.send({
                'type': 'events', 'round_id': 7,
                'server_time_ms': 100}))
            self.assertTrue(endpoint.send({
                'type': 'roster', 'round_id': 8,
                'server_time_ms': 0}))
            self.assertEqual(
                [101, 101, 0],
                [message['server_time_ms']
                 for message in connection.messages])

    def test_worker_is_not_a_player_and_survives_round_reset(self):
        state = BattleState(
            map_name='01_karelia', max_players=1, team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())

        self.assertIsNone(error)
        self.assertEqual({}, state.players)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        self.assertEqual(1, player.player_id)
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID, state.players)

        extra, error = state.add_player(
            _Connection(), ('127.0.0.1', 1002), _player_hello('Extra'))
        self.assertIsNone(extra)
        self.assertEqual('full', error)
        duplicate, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1003), _worker_hello())
        self.assertIsNone(duplicate)
        self.assertEqual('worker_already_connected', error)

        state._reset_round()

        self.assertIs(worker, state.simulation_worker)
        self.assertTrue(worker.connected)
        self.assertEqual(0, worker.battle_ready_round)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        self.assertEqual([1], [row['id']
                              for row in state.lobby_message()['players']])
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID,
                         state.round_participants)
        self.assertFalse(state.result_receipts)

    def test_worker_ready_is_a_separate_loading_barrier(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))

        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertEqual('loading', state.phase)
        live = state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id})

        self.assertIsNotNone(live)
        self.assertEqual('battle', state.phase)
        recipients = state.pending_live_message['recipients']
        self.assertIn(player, recipients)
        self.assertIn(worker, recipients)
        self.assertEqual([player.account_key],
                         list(state.round_participants))

    def test_loading_worker_disconnect_terminates_without_player_takeover(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))

        old_epoch = state.authority_epoch
        removed, round_failed = state.remove_simulation_worker(worker)

        self.assertIs(worker, removed)
        self.assertTrue(round_failed)
        self.assertEqual('battle', state.phase)
        self.assertIsNone(state.bot_authority_id)
        self.assertEqual(old_epoch + 1, state.authority_epoch)
        self.assertIsNone(state.bot_manifest_authority_id)
        self.assertFalse(state.update_bot_manifest(
            player.player_id,
            {'round_id': state.round_id, 'bots': manifest}))
        self.assertIsNone(state.activate_battle_if_ready())
        self.assertEqual('worker_disconnected',
                         state.battle_result['reason'])
        self.assertFalse(state.result_receipts)

    def test_tick_never_delivers_player_receipts_to_worker(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player_connection = _Connection()
        player, error = state.add_player(
            player_connection, ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        state.phase = 'battle'
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.bot_roster = []
        state.result_receipts = OrderedDict((('receipt-1', {
            'type': 'battle_receipt', 'receipt_id': 'receipt-1',
            'account_key': player.account_key,
        }),))

        state.tick_once(1.0 / TICK_HZ)

        self.assertIn('battle_receipt', [
            message.get('type') for message in player_connection.messages])
        self.assertNotIn('battle_receipt', [
            message.get('type') for message in worker_connection.messages])
        self.assertIs(worker, state.simulation_worker)

    def test_worker_loss_cancels_pending_live_and_publishes_failure(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player_connection = _Connection()
        player, error = state.add_player(
            player_connection, ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.pending_live_message[
                             'message']['bot_authority_id'])
        old_epoch = state.authority_epoch

        removed, round_failed = state.remove_simulation_worker(worker)
        self.assertIs(worker, removed)
        self.assertTrue(round_failed)
        self.assertEqual(old_epoch + 1, state.authority_epoch)
        state.tick_once(1.0 / TICK_HZ)

        live_messages = [
            message for message in player_connection.messages
            if message.get('type') == 'battle_live']
        self.assertEqual([], live_messages)
        snapshots = [
            message for message in player_connection.messages
            if message.get('type') == 'snapshot']
        self.assertTrue(snapshots)
        self.assertIsNone(snapshots[-1]['bot_authority_id'])
        self.assertEqual(state.authority_epoch,
                         snapshots[-1]['authority_epoch'])
        self.assertEqual('failed', snapshots[-1]['worker_status'])
        self.assertEqual('worker_disconnected',
                         snapshots[-1]['worker_failure_reason'])
        self.assertEqual('worker_disconnected',
                         snapshots[-1]['battle_result']['reason'])
        self.assertFalse(any(
            message.get('type') == 'battle_live'
            for message in worker_connection.messages))

    def test_dead_human_leave_adjudicates_and_notifies_worker_once(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker_connection = _Connection()
        worker, error = state.add_simulation_worker(
            worker_connection, ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))
        state.tick_once(1.0 / TICK_HZ)
        player.alive = False
        player.health = 0
        player.death_reason = 2
        state._statistics_row(
            'player', player.player_id)['damage_dealt'] = 123

        accepted = state.leave_battle(
            player.player_id, {'round_id': state.round_id})

        self.assertTrue(accepted)
        self.assertIs(player, state.players[player.player_id])
        self.assertFalse(player.participating)
        self.assertIs(worker, state.simulation_worker)
        self.assertEqual('battle', state.phase)
        self.assertEqual(3 - player.team, state.battle_result['winner'])
        self.assertEqual('team_eliminated', state.battle_result['reason'])
        receipts = [
            receipt for receipt in state.result_receipts.values()
            if receipt.get('account_key') == player.account_key]
        self.assertEqual(1, len(receipts))
        self.assertEqual(2, receipts[0]['death_reason'])
        self.assertEqual(1, receipts[0]['finish_reason'])
        self.assertEqual(123, receipts[0]['stats']['damage'])
        self.assertTrue(receipts[0]['premature_leave'])

        result_events = len([
            event for event in state.pending_events
            if event.get('kind') == 'battle_result'])
        receipt_ids = list(state.result_receipts)
        self.assertFalse(state._finish_abandoned_battle())
        self.assertEqual(result_events, len([
            event for event in state.pending_events
            if event.get('kind') == 'battle_result']))
        self.assertEqual(receipt_ids, list(state.result_receipts))

        state.tick_once(1.0 / TICK_HZ)
        worker_results = [
            message.get('battle_result')
            for message in worker_connection.messages
            if message.get('type') == 'snapshot' and
            message.get('battle_result') is not None]
        self.assertEqual([state.battle_result], worker_results)
        self.assertEqual(1, sum(
            event.get('kind') == 'battle_result'
            for message in worker_connection.messages
            if message.get('type') == 'events'
            for event in message.get('events', ())))

    def test_live_human_disconnect_adjudicates_remaining_bots(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        player, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello())
        self.assertIsNone(error)
        start, error = state.request_start(player.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))
        self.assertIsNone(state.mark_battle_ready(
            player.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))

        removed, reset = state.remove_player(player.player_id)

        self.assertIs(player, removed)
        self.assertFalse(reset)
        self.assertIs(worker, state.simulation_worker)
        self.assertIsNotNone(state.battle_result)
        self.assertEqual(3 - player.team, state.battle_result['winner'])
        self.assertEqual('team_eliminated',
                         state.battle_result['reason'])
        receipt = next(
            value for value in state.result_receipts.values()
            if value.get('account_key') == player.account_key)
        self.assertEqual(1, receipt['finish_reason'])

    def test_live_graceful_leave_adjudicates_remaining_bots(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        worker, error = state.add_simulation_worker(
            _Connection(), ('127.0.0.1', 1000), _worker_hello())
        self.assertIsNone(error)
        first, error = state.add_player(
            _Connection(), ('127.0.0.1', 1001), _player_hello('First'))
        self.assertIsNone(error)
        second, error = state.add_player(
            _Connection(), ('127.0.0.1', 1002), _player_hello('Second'))
        self.assertIsNone(error)
        start, error = state.request_start(first.player_id)
        self.assertIsNone(error)
        manifest = _manifest(start['bots'])
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id, 'bots': manifest,
             'player_collision_profiles':
                 _human_profiles(start['players'])}))
        self.assertIsNone(state.mark_battle_ready(
            first.player_id, {'round_id': state.round_id}))
        self.assertIsNone(state.mark_battle_ready(
            second.player_id, {'round_id': state.round_id}))
        self.assertIsNotNone(state.mark_battle_ready(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'round_id': state.round_id}))

        self.assertTrue(state.leave_battle(
            first.player_id, {'round_id': state.round_id}))
        frozen = state.round_participants[first.account_key]
        self.assertTrue(frozen['alive'])
        removed, reset = state.remove_player(first.player_id)
        self.assertIs(first, removed)
        self.assertFalse(reset)
        self.assertTrue(frozen['alive'])

        second.alive = False
        second.health = 0
        second.death_reason = 2
        self.assertTrue(state.leave_battle(
            second.player_id, {'round_id': state.round_id}))

        self.assertIs(worker, state.simulation_worker)
        self.assertEqual('team_eliminated', state.battle_result['reason'])
        self.assertEqual(2, state.battle_result['winner'])

    def test_remaining_bot_adjudication_is_deterministic(self):
        state = BattleState(map_name='01_karelia')

        def forces(team_1, team_2):
            state.bot_manifest = []
            state.bot_states = {}
            bot_id = 1
            for team, values in ((1, team_1), (2, team_2)):
                for health, maximum in values:
                    state.bot_manifest.append({
                        'id': bot_id, 'team': team,
                        'health': health, 'max_health': maximum,
                    })
                    state.bot_states[bot_id] = {
                        'id': bot_id, 'team': team, 'health': health,
                        'max_health': maximum, 'alive': health > 0,
                    }
                    bot_id += 1

        forces(((10, 100), (10, 100)), ((100, 100), (0, 100)))
        self.assertEqual(1, state._remaining_bot_winner())
        forces(((40, 100),), ((50, 200),))
        self.assertEqual(1, state._remaining_bot_winner())
        forces(((50, 100),), ((100, 200),))
        self.assertEqual(2, state._remaining_bot_winner())
        forces(((50, 100),), ((50, 100),))
        self.assertEqual(2, state._remaining_bot_winner())
        state.round_id = 2
        self.assertEqual(1, state._remaining_bot_winner())

        capture_state = BattleState(map_name='01_karelia')
        capture_state.phase = 'battle'
        capture_state.simulation_worker = SimulationWorker(
            _Connection(), ('127.0.0.1', 1000))
        capture_state.rules_state['bases']['2']['points'] = 100
        self.assertTrue(capture_state._finish_abandoned_battle())
        self.assertEqual(1, capture_state.battle_result['winner'])
        self.assertEqual('base captured',
                         capture_state.battle_result['reason'])
        self.assertEqual(2, capture_state.battle_result['base_team'])


class SimulationWorkerSocketTests(unittest.TestCase):
    def setUp(self):
        self.state = BattleState(
            map_name='01_karelia', max_players=1, team_size=1)
        self.server = ThreadedTCPServer(
            ('127.0.0.1', 0), ClientHandler)
        self.server.game_server = type(
            'GameServer', (), {'state': self.state})()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def _connect(self):
        peer = _Peer(self.server.server_address)
        self.peers.append(peer)
        return peer

    def _enter_worker_countdown(self, worker, player):
        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest,
            'player_collision_profiles':
                _human_profiles(worker_start['players'])})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.phase == 'battle')
        self.assertLess(
            self.state.tick, int(round(PREBATTLE_SECONDS * TICK_HZ)))
        return manifest

    def test_handler_requires_exact_protocol_for_all_handshakes(self):
        incompatible = self._connect()
        incompatible_hello = _player_hello('Incompatible')
        incompatible_hello.update({'role': 'probe', 'protocol': 6})
        incompatible.send(incompatible_hello)
        self.assertEqual(
            'protocol', incompatible.receive_until('error')['code'])

        wrong_build = self._connect()
        wrong_build_hello = _player_hello('WrongBuild')
        wrong_build_hello.update({
            'role': 'probe', 'client_build': 'launcher-local-probe'})
        wrong_build.send(wrong_build_hello)
        self.assertEqual(
            'unsupported_client_build',
            wrong_build.receive_until('error')['code'])

        probe = self._connect()
        probe_hello = _player_hello('Probe')
        probe_hello.update({
            'role': 'probe', 'protocol': 5,
        })
        probe.send(probe_hello)
        probe_welcome = probe.receive_until('welcome')

        worker = self._connect()
        worker_hello = _worker_hello()
        worker_hello['protocol'] = 5
        worker.send(worker_hello)
        worker_welcome = worker.receive_until('welcome')

        player = self._connect()
        player_hello = _player_hello()
        player_hello['protocol'] = 5
        player.send(player_hello)
        player_welcome = player.receive_until('welcome')

        for welcome in (probe_welcome, worker_welcome, player_welcome):
            self.assertEqual(5, welcome['protocol'])
            self.assertEqual(CLIENT_BUILD_0922, welcome['client_build'])
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, worker_welcome['worker_id'])
        self.assertEqual(1, player_welcome['player_id'])

    def test_worker_bad_lines_and_handler_exception_do_not_close_transport(self):
        worker = self._connect()
        worker.send(_worker_hello())
        worker.receive_until('welcome')

        worker.stream.write(b'{invalid-json\n')
        worker.stream.flush()
        with mock.patch.object(
                self.state, 'update_simulation_progress',
                side_effect=RuntimeError('one bad native probe')) as update:
            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': self.state.authority_epoch,
                'frame_seq': 1,
            })
            worker.send({'type': 'ping', 'seq': 41})
            self.assertEqual(41, worker.receive_until('pong')['seq'])

        update.assert_called_once()
        self.assertIsNotNone(self.state.simulation_worker)
        self.assertTrue(self.state.simulation_worker.connected)
        self.assertIsNone(self.state.battle_result)

    def test_player_bad_lines_and_handler_exception_do_not_disconnect_player(self):
        player = self._connect()
        player.send(_player_hello())
        welcome = player.receive_until('welcome')

        player.stream.write(b'not-json\n')
        player.stream.flush()
        with mock.patch.object(
                self.state, 'update_input',
                side_effect=RuntimeError('one bad input row')) as update:
            player.send({
                'type': 'input', 'round_id': self.state.round_id,
                'input_seq': 1,
            })
            player.send({'type': 'ping', 'seq': 42})
            self.assertEqual(42, player.receive_until('pong')['seq'])

        update.assert_called_once()
        self.assertIn(welcome['player_id'], self.state.players)
        self.assertTrue(self.state.players[welcome['player_id']].connected)

    def test_handler_worker_disconnect_never_promotes_visible_client(self):
        worker = self._connect()
        worker.send(_worker_hello())
        welcome = worker.receive_until('welcome')

        self.assertEqual(SIMULATION_WORKER_ROLE, welcome['role'])
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         welcome['worker_id'])
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         welcome['bot_authority_id'])
        self.assertEqual([], self.state.lobby_message()['players'])

        # These are player commands.  The worker dispatcher must ignore all
        # of them, then process ping as an ordered barrier.
        revision = self.state.state_revision
        forbidden = (
            {'type': 'start_battle', 'round_id': self.state.round_id},
            {'type': 'select_vehicle', 'vehicle': 'ussr:R06_T-28'},
            {'type': 'input', 'round_id': self.state.round_id,
             'forward': 1.0},
            {'type': 'leave_battle', 'round_id': self.state.round_id},
            {'type': 'battle_receipt_ack', 'receipt_id': 'not-a-receipt'},
        )
        for message in forbidden:
            worker.send(message)
        worker.send({'type': 'ping', 'seq': 1})
        worker.receive_until('pong')
        self.assertEqual('waiting', self.state.phase)
        self.assertEqual(revision, self.state.state_revision)
        self.assertEqual({}, self.state.players)
        self.assertFalse(self.state.result_receipts)

        player = self._connect()
        player.send(_player_hello())
        player_welcome = player.receive_until('welcome')
        self.assertNotIn('role', player_welcome)
        self.assertEqual(1, player_welcome['player_id'])
        self.assertEqual([1], sorted(self.state.players))

        extra = self._connect()
        extra.send(_player_hello('Extra'))
        self.assertEqual('full', extra.receive_until('error')['code'])

        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player_start = player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         player_start['bot_authority_id'])
        self.assertEqual([1], [entry['id']
                              for entry in worker_start['players']])
        self.assertNotIn(SIMULATION_WORKER_AUTHORITY_ID,
                         self.state.round_participants)

        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest,
            'player_collision_profiles':
                _human_profiles(worker_start['players'])})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.receive_until('snapshot')
        worker.receive_until('snapshot')
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.phase == 'battle')
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))

        publication = _bot_publication(manifest, x_offset=1.0)
        player.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': publication})
        player.send({'type': 'ping', 'seq': 2})
        player.receive_until('pong')
        # Modern visible commands are rejected by the dispatcher before
        # they can reach, or mutate diagnostics inside, the authority path.
        self.assertEqual('', self.state.last_bot_state_reject_code)
        revision = self.state.bot_state_revision

        worker.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': publication})
        worker.send({'type': 'ping', 'seq': 3})
        worker.receive_until('pong')
        self.assertEqual(revision + 1, self.state.bot_state_revision)
        self.assertEqual(1.0 + manifest[0]['x'],
                         self.state.bot_states[manifest[0]['id']]['x'])
        self.state.bot_pending_projectile_launches.add(
            (manifest[0]['id'], 99))

        old_epoch = self.state.authority_epoch
        worker.send({'type': 'leave'})
        _wait_until(lambda: self.state.simulation_worker is None)

        self.assertIsNone(self.state.bot_authority_id)
        self.assertEqual(old_epoch + 1, self.state.authority_epoch)
        self.assertIsNone(self.state.bot_manifest_authority_id)
        self.assertEqual('worker_disconnected',
                         self.state.worker_failure_reason)
        self.assertEqual([1], sorted(self.state.players))
        self.assertFalse(self.state.bot_pending_projectile_launches)
        self.assertFalse(self.state._projectile_authority_matches(
            SIMULATION_WORKER_AUTHORITY_ID,
            {'authority_epoch': old_epoch}))
        self.assertFalse(self.state._projectile_authority_matches(
            1, {'authority_epoch': self.state.authority_epoch}))

        replacement = self._connect()
        replacement.send(_worker_hello())
        self.assertEqual(
            'battle_in_progress',
            replacement.receive_until('error')['code'])

        roster = player.receive_until('roster')
        self.assertIsNone(roster['bot_authority_id'])
        self.assertEqual('failed', roster['worker_status'])
        self.assertEqual('worker_disconnected',
                         roster['worker_failure_reason'])
        player.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest})
        player.send({'type': 'ping', 'seq': 4})
        player.receive_until('pong')
        self.assertIsNone(self.state.bot_manifest_authority_id)

        rejected_publication = _bot_publication(manifest, x_offset=2.0)
        player.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': rejected_publication})
        player.send({'type': 'ping', 'seq': 5})
        player.receive_until('pong')
        self.assertNotEqual(2.0 + manifest[0]['x'],
                            self.state.bot_states[manifest[0]['id']]['x'])

    def test_socket_worker_loss_cancels_queued_live_barrier(self):
        worker = self._connect()
        worker.send(_worker_hello())
        worker.receive_until('welcome')
        player = self._connect()
        player.send(_player_hello())
        player.receive_until('welcome')

        player.send({
            'type': 'start_battle', 'round_id': self.state.round_id})
        player.receive_until('battle_start')
        worker_start = worker.receive_until('battle_start')
        manifest = _manifest(worker_start['bots'])
        worker.send({
            'type': 'bot_manifest', 'round_id': self.state.round_id,
            'bots': manifest,
            'player_collision_profiles':
                _human_profiles(worker_start['players'])})
        _wait_until(lambda: self.state.bot_manifest_authority_id ==
                    SIMULATION_WORKER_AUTHORITY_ID)
        player.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        worker.send({
            'type': 'battle_ready', 'round_id': self.state.round_id})
        _wait_until(lambda: self.state.pending_live_message is not None)
        old_epoch = self.state.authority_epoch

        worker.send({'type': 'leave'})
        _wait_until(lambda: self.state.simulation_worker is None)
        roster = player.receive_until('roster')
        self.assertIsNone(roster['bot_authority_id'])
        self.assertEqual(old_epoch + 1, roster['authority_epoch'])
        self.assertEqual('failed', roster['worker_status'])

        self.state.tick_once(1.0 / TICK_HZ)
        events = player.receive_until('events')
        self.assertEqual(roster['authority_epoch'],
                         events['authority_epoch'])
        self.assertTrue(any(
            event.get('kind') == 'battle_result' and
            event.get('reason') == 'worker_disconnected'
            for event in events['events']))

    def test_recoverable_bot_state_rejection_keeps_worker_and_round(self):
        worker = self._connect()
        worker.send(_worker_hello())
        worker.receive_until('welcome')
        player = self._connect()
        player.send(_player_hello())
        player.receive_until('welcome')
        manifest = self._enter_worker_countdown(worker, player)
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))

        publication = _bot_publication(manifest)
        worker.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': publication,
        })
        worker.send({'type': 'ping', 'seq': 1})
        worker.receive_until('pong')
        _wait_until(lambda: self.state.bot_state_revision == 1)

        rejected = _bot_publication(manifest, x_offset=99.0)
        rejected[0]['combat_seq'] = 2
        worker.send({
            'type': 'bot_state', 'round_id': self.state.round_id,
            'bots': rejected,
        })
        worker.send({'type': 'ping', 'seq': 2})
        self.assertEqual(2, worker.receive_until('pong')['seq'])

        self.assertIsNotNone(self.state.simulation_worker)
        self.assertIsNone(self.state.battle_result)
        self.assertEqual(1, self.state.bot_state_revision)
        self.assertEqual('combat_contract',
                         self.state.last_bot_state_reject_code)
        self.assertNotEqual(99.0 + manifest[0]['x'],
                            self.state.bot_states[manifest[0]['id']]['x'])

    def test_silent_open_worker_timeout_terminates_round(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LOADING_TIMEOUT_SECONDS', 0.1):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            time.sleep(0.65)
            self.assertIsNotNone(self.state.simulation_worker)
            self.assertEqual('waiting', self.state.phase)

            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            old_epoch = self.state.authority_epoch

            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            player.receive_until('battle_start')
            worker.receive_until('battle_start')

            # Keep the client-side socket open but publish no worker messages.
            # This models a native callback loop that stopped while TCP stayed
            # established.
            self.assertNotEqual(-1, worker.socket.fileno())
            _wait_until(
                lambda: self.state.simulation_worker is None,
                timeout=2.0)

            roster = player.receive_until('roster')
            self.assertIsNone(roster['bot_authority_id'])
            self.assertIsNone(self.state.bot_authority_id)
            self.assertEqual(old_epoch + 1, self.state.authority_epoch)
            self.assertEqual('worker_disconnected',
                             self.state.worker_failure_reason)
            self.assertEqual('worker_disconnected',
                             self.state.battle_result['reason'])
            self.assertNotEqual(-1, worker.socket.fileno())

    def test_loading_worker_ping_refreshes_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LOADING_TIMEOUT_SECONDS', 0.35):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player.receive_until('welcome')
            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            player.receive_until('battle_start')
            worker.receive_until('battle_start')

            for sequence in range(1, 5):
                time.sleep(0.2)
                worker.send({'type': 'ping', 'seq': sequence})
                pong = worker.receive_until('pong')
                self.assertEqual(sequence, pong['seq'])

            self.assertIsNotNone(self.state.simulation_worker)
            self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                             self.state.bot_authority_id)

    def test_countdown_progress_refreshes_worker_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)
            epoch = self.state.authority_epoch

            for frame_seq in range(1, 5):
                time.sleep(0.12)
                worker.send({
                    'type': 'simulation_progress',
                    'round_id': self.state.round_id,
                    'authority_epoch': epoch,
                    'frame_seq': frame_seq,
                })
                worker.send({'type': 'ping', 'seq': frame_seq})
                self.assertEqual(
                    frame_seq, worker.receive_until('pong')['seq'])

            endpoint = self.state.simulation_worker
            self.assertIsNotNone(endpoint)
            self.assertEqual(4, endpoint.simulation_progress_frame_seq)
            self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                             self.state.bot_authority_id)

    def test_hidden_worker_observation_is_an_accepted_battle_frame(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3), \
                mock.patch.object(server_module, '_server_log') as log:
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            manifest = self._enter_worker_countdown(worker, player)
            self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))

            worker.send({
                'type': 'bot_state', 'round_id': self.state.round_id,
                'bots': _bot_publication(manifest),
            })
            worker.send({'type': 'ping', 'seq': 0})
            self.assertEqual(0, worker.receive_until('pong')['seq'])
            _wait_until(lambda: self.state.bot_state_revision == 1)

            target = self.state.players[player_welcome['player_id']]
            observing_team = next(
                entry['team'] for entry in manifest
                if entry['team'] != target.team)
            observation = {
                'type': 'bot_observation',
                'round_id': self.state.round_id,
                'contacts': [{
                    'observing_team': observing_team,
                    'target_kind': 'human',
                    'target_id': target.player_id,
                    'target_team': target.team,
                    'visible': False,
                    'fresh': False,
                    'time_left': 0.0,
                    'visible_by_bot_ids': [],
                    'visible_by_player_ids': [],
                    'shootable_by_bot_ids': [],
                    'x': target.x, 'y': target.y, 'z': target.z,
                    'health': target.health,
                    'max_health': target.max_health,
                }],
                'affordances': [],
            }
            for sequence in range(1, 6):
                time.sleep(0.12)
                worker.send(observation)
                worker.send({'type': 'ping', 'seq': sequence})
                self.assertEqual(
                    sequence, worker.receive_until('pong')['seq'])

            self.assertIsNotNone(self.state.simulation_worker)
            rejects = [
                call.args[0] for call in log.call_args_list
                if call.args and
                'WORKER COMMAND rejected type=bot_observation' in
                call.args[0]
            ]
            self.assertEqual([], rejects)

    def test_battle_ping_only_worker_times_out(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.3):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player_welcome = player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)

            for sequence in range(1, 4):
                time.sleep(0.12)
                worker.send({'type': 'ping', 'seq': sequence})
                self.assertEqual(sequence,
                                 worker.receive_until('pong')['seq'])

            _wait_until(
                lambda: self.state.simulation_worker is None, timeout=2.0)
            self.assertIsNone(self.state.bot_authority_id)
            self.assertEqual('worker_disconnected',
                             self.state.battle_result['reason'])

    def test_duplicate_and_stale_progress_do_not_refresh_liveness(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.35):
            worker = self._connect()
            worker.send(_worker_hello())
            worker.receive_until('welcome')
            player = self._connect()
            player.send(_player_hello())
            player.receive_until('welcome')
            self._enter_worker_countdown(worker, player)
            epoch = self.state.authority_epoch
            endpoint = self.state.simulation_worker

            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch,
                'frame_seq': 10,
            })
            worker.send({'type': 'ping', 'seq': 1})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch,
                'frame_seq': 10,
            })
            worker.send({'type': 'ping', 'seq': 2})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({
                'type': 'simulation_progress',
                'round_id': self.state.round_id,
                'authority_epoch': epoch - 1,
                'frame_seq': 11,
            })
            worker.send({'type': 'ping', 'seq': 3})
            worker.receive_until('pong')
            self.assertEqual(10, endpoint.simulation_progress_frame_seq)

            time.sleep(0.12)
            worker.send({'type': 'ping', 'seq': 4})
            worker.receive_until('pong')
            _wait_until(
                lambda: self.state.simulation_worker is None, timeout=2.0)
            self.assertIsNone(self.state.bot_authority_id)
            self.assertEqual('worker_disconnected',
                             self.state.battle_result['reason'])

    def test_player_socket_has_no_worker_liveness_timeout(self):
        with mock.patch.object(
                server_module,
                'SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS', 0.1):
            player = self._connect()
            player.send(_player_hello())
            welcome = player.receive_until('welcome')
            self.assertEqual({
                'type', 'protocol', 'client_build', 'player_id', 'name',
                'vehicle', 'outfits', 'team', 'slot', 'max_health', 'map',
                'map_pool', 'host_player_id', 'phase', 'round_id',
                'state_revision', 'spawn', 'bot_authority_id', 'team_size',
                'authority_epoch', 'capabilities', 'server_capabilities',
                'team_sizes', 'bot_tier_mode', 'worker_status',
            'vehicle_compact_descr',
            'effective_params',
        }, set(welcome))
            roster = player.receive_until('roster')
            self.assertEqual('missing', roster['worker_status'])
            self.assertNotIn('worker_failure_reason', roster)
            player.send({
                'type': 'start_battle',
                'round_id': self.state.round_id,
            })
            denied = player.receive_until('start_denied')
            self.assertEqual('simulation_worker_required', denied['code'])

            time.sleep(0.65)

            self.assertIn(welcome['player_id'], self.state.players)
            self.assertTrue(self.state.players[welcome['player_id']].connected)
            self.assertEqual('waiting', self.state.phase)


if __name__ == '__main__':
    unittest.main()
