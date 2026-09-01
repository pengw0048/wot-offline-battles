import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import lan_client as lan_client_module
from gui.mods.offline_lan_0922.account_rpc.state import AccountState
from gui.mods.offline_lan_0922.authority_worker import (
    AuthorityWorkerLANClient, WORKER_BUSY_RETRY_SECONDS, WORKER_DUMMY_Y,
    WORKER_RETRY_SECONDS, WORKER_ROLE, WorkerSession, _WorldDrawLease)
from gui.mods.offline_lan_0922.lan_client import (
    CLIENT_BUILD, CLIENT_CAPABILITIES, LANClient, PROTOCOL_VERSION,
    SIMULATION_WORKER_CAPABILITY, WORKER_AUTHORITY_ID)
from effective_params_fixture import effective_params


class _DrawWorld(object):
    _READ = object()

    def __init__(self):
        self.enabled = True
        self.transitions = []

    def worldDrawEnabled(self, value=_READ):
        if value is self._READ:
            return self.enabled
        self.enabled = bool(value)
        self.transitions.append(self.enabled)


class _RecordingSocket(object):
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class _WorkerClient(object):
    def __init__(self):
        self.player_id = WORKER_AUTHORITY_ID
        self.bot_authority_id = WORKER_AUTHORITY_ID
        self.connected = True
        self.phase = 'battle'
        self.round_id = 1
        self.authority_epoch = 1
        self.on_event = None
        self.stopped = False
        self.progress = []

    def is_bot_authority(self):
        return (self.bot_authority_id == WORKER_AUTHORITY_ID and
                self.phase in ('loading', 'battle'))

    def stop(self):
        self.stopped = True

    def send_simulation_progress(self, frame_seq):
        self.progress.append(frame_seq)
        return True


class _WorkerRuntime(object):
    def __init__(self, client=None, world=None, calls=None, fail_stop=False):
        self.client = client
        self.world = world
        self.calls = calls if calls is not None else []
        self.fail_stop = fail_stop
        self.state = 'running'
        self.error = None
        self.draw_ready = True
        self.start_config = None
        self.fire_intent_results = []
        self.player_destructible_contacts = []
        self.sample = {
            'round_finished': False,
            'frame_callbacks': 1,
            'authority_callbacks': 0,
            'bot_state_generated': 0,
            'bot_state_enqueued': 0,
            'bot_state_send_failed': 0,
            'bot_state_revision': 0,
            'bot_probes': {},
            'bot_count': 0,
            'simulation_caps': 0,
            'alive_bot_ticks': 0,
        }

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        del message, on_local_leave
        self.client = lan_client
        self.start_config = dict(config)
        return True

    def stop(self, show_login=False, restore_account=True):
        self.calls.append((
            'runtime_stop', self.client.bot_authority_id,
            self.world.enabled, show_login, restore_account))
        if self.fail_stop:
            raise RuntimeError('native teardown failed')
        self.state = 'stopped'

    def _authority_worker_probe_sample(self):
        return dict(self.sample)

    def authority_worker_ready_for_draw_off(self):
        return self.draw_ready

    def on_fire_intent_result(self, message):
        self.fire_intent_results.append(dict(message))
        return True

    def on_player_destructible_contact(self, message):
        self.player_destructible_contacts.append(dict(message))
        return True


def _human(player_id=1):
    return {
        'id': player_id,
        'name': 'Human',
        'vehicle': 'ussr:R11_MS-1',
        'team': 1,
        'slot': 0,
        'x': 12.0,
        'y': 1.0,
        'z': 24.0,
        'yaw': 0.0,
        'aim_yaw': 0.0,
        'gun_pitch': 0.0,
        'speed': 0.0,
        'world_pose': True,
        'health': 90,
        'max_health': 90,
        'alive': True,
        'critical': {},
        'critical_revision': 0,
        'critical_base_revision': 0,
        'critical_ack_seq': 0,
        'input_seq': 0,
        'up_cosine': 1.0,
        'landing_observation_seq': 0,
        'equipment_states': [],
        'equipment_revision': 0,
        'equipment_intent_seq': 0,
        'equipment_intent_result': {
            'intent_seq': 0, 'accepted': False, 'reason': ''},
        'outfits': {},
        'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params(),
    }


def _projected_bot_state(bot_id=11):
    return {
        'id': bot_id, 'x': 1.0, 'y': 2.0, 'z': 3.0,
        'yaw': 0.1, 'pitch': 0.0, 'roll': 0.0,
        'aim_yaw': 0.2, 'gun_pitch': -0.1,
        'movement_dir': 1, 'rotation_dir': 0, 'fire_seq': 2,
        'shell_index': 0, 'next_shell_index': 1,
        'ammo_remaining': [20, 10], 'ammo_reload_pending': False,
        'reload_time': 0.1, 'reload_duration': 0.2,
        'health': 700, 'alive': True,
        'critical': {
            'devices': [{
                'name': 'engine', 'hp': 100.0, 'max_hp': 100.0,
                'state': 'normal'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': []},
        'combat_base_revision': 1, 'combat_seq': 2,
        'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        'death_reason': 0, 'display_health': 700,
        'world_pose': True,
    }


class AuthorityWorkerClientTests(unittest.TestCase):
    @staticmethod
    def _active_client():
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        client.sock = _RecordingSocket()
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = WORKER_AUTHORITY_ID
        client._stopping = False
        with client._outbound_lock:
            client._outbound_accepting = True
        return client

    def test_client_mode_is_player_by_default_and_worker_only_by_opt_in(self):
        self.assertEqual(
            port_config.PLAYER_MODE,
            port_config.client_mode(port_config.DEFAULT_CONFIG, environ={}))
        self.assertEqual(
            port_config.SIMULATION_WORKER_MODE,
            port_config.client_mode(
                port_config.DEFAULT_CONFIG,
                environ={port_config.CLIENT_MODE_ENV:
                         port_config.SIMULATION_WORKER_MODE}))
        with self.assertRaises(ValueError):
            port_config.client_mode(
                port_config.DEFAULT_CONFIG,
                environ={port_config.CLIENT_MODE_ENV: 'unexpected'})

    def test_session_identity_reports_installed_and_launcher_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'build_identity.json'
            path.write_text(json.dumps({
                'schema': 1,
                'semanticVersion': '0.6.1',
                'buildIdentity': 'installed-build',
            }), encoding='utf-8')

            identity = port_config.session_identity(
                str(path), environ={
                    port_config.BUILD_SEMANTIC_VERSION_ENV: '0.6.1',
                    port_config.BUILD_IDENTITY_ENV: 'launcher-build',
                })

        self.assertEqual('0.6.1', identity['semanticVersion'])
        self.assertEqual('installed-build', identity['buildIdentity'])
        self.assertEqual('0.6.1', identity['launcherSemanticVersion'])
        self.assertEqual('launcher-build', identity['launcherBuildIdentity'])

    def test_missing_session_identity_never_blocks_startup(self):
        identity = port_config.session_identity(
            'does-not-exist.json', environ={
                port_config.BUILD_SEMANTIC_VERSION_ENV: '0.6.1',
                port_config.BUILD_IDENTITY_ENV: 'launcher-build',
            })

        self.assertEqual('unknown', identity['semanticVersion'])
        self.assertEqual('unknown', identity['buildIdentity'])
        self.assertEqual('0.6.1', identity['launcherSemanticVersion'])
        self.assertEqual('launcher-build', identity['launcherBuildIdentity'])

    def test_player_hello_wire_shape_advertises_required_capabilities(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            max_health=90, account_key='account', outfits={},
            vehicle_compact_descr='dGVzdA==',
            effective_params=effective_params())

        self.assertEqual({
            'type': 'hello',
            'protocol': PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES),
            'name': 'Player',
            'vehicle': 'ussr:R11_MS-1',
            'max_health': 90,
            'account_key': 'account',
            'outfits': {},
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': effective_params(),
        }, client._hello_payload())
        self.assertNotIn('role', client._hello_payload())

    def test_worker_hello_has_no_player_or_dummy_fields(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        hello = client._hello_payload()

        self.assertEqual({
            'type': 'hello',
            'protocol': PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES) + [
                SIMULATION_WORKER_CAPABILITY],
            'role': WORKER_ROLE,
        }, hello)
        self.assertTrue(set(hello).isdisjoint({
            'name', 'vehicle', 'max_health', 'account_key', 'outfits',
            'player_id', 'spawn'}))

    def test_worker_publishes_bounded_player_environment_batch(self):
        client = self._active_client()
        client.authority_epoch = 4
        client.capabilities = tuple(CLIENT_CAPABILITIES) + (
            SIMULATION_WORKER_CAPABILITY,)
        client.server_capabilities = tuple(CLIENT_CAPABILITIES)
        client._send = mock.Mock(return_value=True)

        self.assertTrue(client.send_player_environment([{
            'player_id': 2, 'input_seq': 17, 'level': 2,
        }], 9))

        client._send.assert_called_once_with({
            'type': 'player_environment', 'round_id': 7,
            'authority_epoch': 4, 'sample_seq': 9,
            'observations': [{
                'player_id': 2, 'input_seq': 17, 'level': 2,
            }],
        })

    def test_visible_client_cannot_publish_player_environment(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1')
        client.ready = True
        client.phase = 'battle'
        client.player_id = 1
        client.bot_authority_id = WORKER_AUTHORITY_ID
        client.authority_epoch = 1
        client.capabilities = tuple(CLIENT_CAPABILITIES)
        client.server_capabilities = tuple(CLIENT_CAPABILITIES)
        client._send = mock.Mock(return_value=True)

        self.assertFalse(client.send_player_environment([], 1))
        client._send.assert_not_called()

    def test_worker_bot_state_is_encoded_once_and_frozen_as_queue_bytes(self):
        client = self._active_client()
        state = _projected_bot_state()
        dumps_calls = []
        original_dumps = lan_client_module.json.dumps

        def recording_dumps(message, *args, **kwargs):
            dumps_calls.append((message.get('type'), dict(kwargs)))
            return original_dumps(message, *args, **kwargs)

        with mock.patch.object(
                lan_client_module, '_freeze_outbound',
                side_effect=AssertionError('trusted path copied payload')):
            with mock.patch.object(
                    lan_client_module.json, 'dumps',
                    side_effect=recording_dumps):
                self.assertTrue(client.send_projected_bot_state(
                    [state], sample_time_us=40000,
                    source_batch_horizon_us=40000))
                state['x'] = 99.0
                state['critical']['devices'][0]['hp'] = 1.0
                generation = client._transport_generation
                queued = client._dequeue_outbound(generation)
                self.assertIsNotNone(queued)
                self.assertTrue(client._send_wire(
                    queued[1], client.sock, generation))

        self.assertEqual([('bot_state', {
            'separators': (',', ':'), 'allow_nan': False})], dumps_calls)
        self.assertEqual(1, len(client.sock.sent))
        self.assertEqual(queued[2], len(client.sock.sent[0]))
        wire = json.loads(client.sock.sent[0].decode('utf-8'))
        self.assertEqual(40000, wire['sample_time_us'])
        self.assertEqual(40000, wire['source_batch_horizon_us'])
        self.assertEqual(1.0, wire['bots'][0]['x'])
        self.assertEqual(100.0,
                         wire['bots'][0]['critical']['devices'][0]['hp'])

    def test_worker_bot_state_trusted_path_rejects_noncanonical_or_unbounded(self):
        client = self._active_client()
        extra = _projected_bot_state()
        extra['profile'] = {'class_tag': 'mediumTank'}
        self.assertFalse(client.send_projected_bot_state([extra]))

        half_shot = _projected_bot_state()
        half_shot['shot_yaw'] = 0.2
        self.assertFalse(client.send_projected_bot_state([half_shot]))

        nonfinite = _projected_bot_state()
        nonfinite['x'] = float('nan')
        self.assertFalse(client.send_projected_bot_state([nonfinite]))

        oversized = _projected_bot_state()
        oversized['critical']['events'] = [
            'x' * lan_client_module.MAX_MESSAGE_BYTES]
        self.assertFalse(client.send_projected_bot_state([oversized]))

        self.assertEqual([], client._outbound_queue)
        self.assertTrue(client.running)

    def test_worker_replaces_unsent_continuous_bot_checkpoint(self):
        client = self._active_client()
        first = _projected_bot_state()
        second = json.loads(json.dumps(first))
        second['x'] = 8.0
        second['yaw'] = 0.7
        second['reload_time'] = 0.05
        second['combat_fire_elapsed'] = 0.03
        second['combat_fire_timer'] = 0.07

        self.assertTrue(client.send_projected_bot_state(
            [first], sample_time_us=40000,
            source_batch_horizon_us=40000))
        first_size = client._outbound_bytes
        self.assertTrue(client.send_projected_bot_state(
            [second], sample_time_us=80000,
            source_batch_horizon_us=80000))

        self.assertTrue(client.running)
        self.assertEqual(1, len(client._outbound_queue))
        self.assertNotEqual(first_size, 0)
        self.assertEqual(
            client._outbound_queue[0][2], client._outbound_bytes)
        wire = json.loads(
            client._outbound_queue[0][1].payload.decode('utf-8'))
        self.assertEqual(80000, wire['sample_time_us'])
        self.assertEqual(8.0, wire['bots'][0]['x'])
        self.assertEqual(0.05, wire['bots'][0]['reload_time'])

    def test_worker_coalesces_across_queue_without_dropping_discrete_message(self):
        client = self._active_client()
        first = _projected_bot_state()
        fired = json.loads(json.dumps(first))
        fired['fire_seq'] += 1
        fired['ammo_remaining'][0] -= 1
        fired['ammo_reload_pending'] = True

        self.assertTrue(client.send_projected_bot_state(
            [first], sample_time_us=40000,
            source_batch_horizon_us=40000))
        self.assertTrue(client.send_projected_bot_state(
            [fired], sample_time_us=80000,
            source_batch_horizon_us=80000))
        self.assertTrue(client._send({
            'type': 'projectile_launch', 'shot_seq': 3}))
        later = json.loads(json.dumps(fired))
        later['x'] = 9.0
        later['reload_time'] = 0.04
        self.assertTrue(client.send_projected_bot_state(
            [later], sample_time_us=120000,
            source_batch_horizon_us=120000))

        self.assertEqual(3, len(client._outbound_queue))
        self.assertEqual([
            'bot_state', 'bot_state', 'projectile_launch',
        ], [
            (json.loads(item[1].payload.decode('utf-8'))['type']
             if isinstance(item[1], lan_client_module._PreencodedOutbound)
             else item[1]['type'])
            for item in client._outbound_queue])
        wires = [
            json.loads(item[1].payload.decode('utf-8'))
            for item in client._outbound_queue
            if isinstance(item[1], lan_client_module._PreencodedOutbound)]
        self.assertEqual(9.0, wires[-1]['bots'][0]['x'])

    def test_worker_ram_event_preserves_its_preceding_pose_barrier(self):
        client = self._active_client()
        contact = _projected_bot_state()
        contact['x'] = 5.0
        later = json.loads(json.dumps(contact))
        later['x'] = 20.0

        self.assertTrue(client.send_projected_bot_state(
            [contact], sample_time_us=400000,
            source_batch_horizon_us=1000000))
        self.assertTrue(client.send_bot_ram(
            11, 'bot', 12, 1, 1, 1))
        self.assertTrue(client.send_projected_bot_state(
            [later], sample_time_us=1000000,
            source_batch_horizon_us=1000000))

        self.assertEqual([
            'bot_state', 'bot_ram_report', 'bot_state',
        ], [
            (json.loads(item[1].payload.decode('utf-8'))['type']
             if isinstance(item[1], lan_client_module._PreencodedOutbound)
             else item[1]['type'])
            for item in client._outbound_queue])
        states = [
            json.loads(item[1].payload.decode('utf-8'))
            for item in client._outbound_queue
            if isinstance(item[1], lan_client_module._PreencodedOutbound)]
        self.assertEqual([5.0, 20.0], [
            state['bots'][0]['x'] for state in states])

    def test_worker_never_coalesces_damage_or_combat_sequence_edges(self):
        client = self._active_client()
        first = _projected_bot_state()
        damaged = json.loads(json.dumps(first))
        damaged['health'] = 640
        damaged['display_health'] = 640
        damaged['critical']['devices'][0]['hp'] = 80.0
        damaged['combat_seq'] += 1

        self.assertTrue(client.send_projected_bot_state(
            [first], sample_time_us=40000,
            source_batch_horizon_us=40000))
        self.assertTrue(client.send_projected_bot_state(
            [damaged], sample_time_us=80000,
            source_batch_horizon_us=80000))

        self.assertEqual(2, len(client._outbound_queue))
        wires = [json.loads(item[1].payload.decode('utf-8'))
                 for item in client._outbound_queue]
        self.assertEqual([700, 640], [
            wire['bots'][0]['health'] for wire in wires])
        self.assertEqual([2, 3], [
            wire['bots'][0]['combat_seq'] for wire in wires])

    def test_worker_never_coalesces_back_across_a_state_edge(self):
        client = self._active_client()
        first = _projected_bot_state()
        edge = json.loads(json.dumps(first))
        edge['fire_seq'] += 1
        edge['ammo_remaining'][0] -= 1
        reverted_key = json.loads(json.dumps(first))
        reverted_key['x'] = 12.0

        self.assertTrue(client.send_projected_bot_state([first]))
        self.assertTrue(client.send_projected_bot_state([edge]))
        self.assertTrue(client._send({'type': 'projectile_launch'}))
        self.assertTrue(client.send_projected_bot_state([reverted_key]))

        self.assertEqual(4, len(client._outbound_queue))
        self.assertEqual('projectile_launch',
                         client._outbound_queue[2][1]['type'])

    def test_worker_normal_30hz_pose_backlog_stays_bounded(self):
        client = self._active_client()
        states = [_projected_bot_state(bot_id)
                  for bot_id in range(1, 30)]
        original_limit = lan_client_module.MAX_OUTBOUND_MESSAGES
        lan_client_module.MAX_OUTBOUND_MESSAGES = 2
        try:
            for frame in range(600):
                sample = (frame + 1) * 33333
                for state in states:
                    state['x'] = frame * 0.001
                    state['yaw'] = frame * 0.0001
                    state['reload_time'] = max(
                        0.0, 0.1 - (frame % 3) * 0.01)
                self.assertTrue(client.send_projected_bot_state(
                    states, sample_time_us=sample,
                    source_batch_horizon_us=sample))
        finally:
            lan_client_module.MAX_OUTBOUND_MESSAGES = original_limit

        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertEqual(1, len(client._outbound_queue))
        wire = json.loads(
            client._outbound_queue[0][1].payload.decode('utf-8'))
        self.assertEqual(600 * 33333, wire['sample_time_us'])
        self.assertEqual(0.599, wire['bots'][0]['x'])

    def test_worker_bot_state_queue_pressure_keeps_transport_and_fifo(self):
        client = self._active_client()
        sock = client.sock
        original_limit = lan_client_module.MAX_OUTBOUND_MESSAGES
        lan_client_module.MAX_OUTBOUND_MESSAGES = 1
        try:
            self.assertTrue(client.send_projected_bot_state([
                _projected_bot_state(11)]))
            self.assertFalse(client.send_projected_bot_state([
                _projected_bot_state(12)]))
        finally:
            lan_client_module.MAX_OUTBOUND_MESSAGES = original_limit

        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertFalse(sock.closed)
        self.assertEqual(1, len(client._outbound_queue))
        self.assertIsNone(client.last_error)

    def test_worker_queue_reserves_bounded_headroom_for_discrete_edges(self):
        client = self._active_client()
        original_limit = lan_client_module.MAX_OUTBOUND_MESSAGES
        lan_client_module.MAX_OUTBOUND_MESSAGES = 1
        try:
            self.assertTrue(client.send_projected_bot_state([
                _projected_bot_state(11)]))
            self.assertFalse(client._send({'type': 'projectile_progress'}))
            self.assertTrue(client._send({'type': 'projectile_launch'}))
            self.assertFalse(client._send({'type': 'battle_result'}))
        finally:
            lan_client_module.MAX_OUTBOUND_MESSAGES = original_limit

        self.assertTrue(client.running)
        self.assertTrue(client.connected)
        self.assertEqual(2, len(client._outbound_queue))
        self.assertEqual('projectile_launch',
                         client._outbound_queue[1][1]['type'])
        self.assertIsNone(client.last_error)

    def test_worker_bot_state_encoding_cannot_cross_transport_generation(self):
        client = self._active_client()
        old_sock = client.sock
        original_dumps = lan_client_module.json.dumps

        def reconnect_during_encoding(message, *args, **kwargs):
            encoded = original_dumps(message, *args, **kwargs)
            with client._outbound_lock:
                client._transport_generation += 1
                client.sock = _RecordingSocket()
                client.running = True
                client.connected = True
                client._stopping = False
                client._outbound_accepting = True
                client._outbound_queue = []
                client._outbound_bytes = 0
            return encoded

        with mock.patch.object(
                lan_client_module.json, 'dumps',
                side_effect=reconnect_during_encoding):
            self.assertFalse(client.send_projected_bot_state([
                _projected_bot_state()]))

        self.assertIsNot(old_sock, client.sock)
        self.assertEqual([], client._outbound_queue)
        self.assertEqual(0, client._outbound_bytes)

    def test_worker_welcome_requires_ricochet_capability_from_server(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        message = {
            'protocol': PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'role': WORKER_ROLE,
            'worker_id': WORKER_AUTHORITY_ID,
            'capabilities': list(CLIENT_CAPABILITIES) + [
                SIMULATION_WORKER_CAPABILITY],
            'server_capabilities': [
                lan_client_module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                lan_client_module.RAM_CONTACT_LEDGER_CAPABILITY,
                lan_client_module.HUMAN_RAM_TIMELINE_CAPABILITY,
                lan_client_module.PLAYER_FIRE_INTENT_CAPABILITY,
                lan_client_module.PLAYER_ENVIRONMENT_CAPABILITY,
                lan_client_module.EFFECTIVE_PARAMS_CAPABILITY,
            ],
            'state_revision': 1, 'round_id': 0, 'host_player_id': 1,
            'authority_epoch': 0, 'server_time_ms': 10, 'team_size': 15,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'phase': 'waiting', 'map': '01_karelia',
        }

        self.assertFalse(client._handle_worker_welcome(message))
        self.assertEqual('invalid worker welcome', client.last_error)

    def test_worker_welcome_negotiates_protocol_and_build_labels(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        message = {
            'type': 'welcome', 'protocol': PROTOCOL_VERSION + 1,
            'client_build': 'launcher-local-server',
            'role': WORKER_ROLE,
            'worker_id': WORKER_AUTHORITY_ID,
            'capabilities': list(CLIENT_CAPABILITIES) + [
                SIMULATION_WORKER_CAPABILITY],
            'server_capabilities': [
                lan_client_module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                lan_client_module.RAM_CONTACT_LEDGER_CAPABILITY,
                lan_client_module.HUMAN_RAM_TIMELINE_CAPABILITY,
                lan_client_module.PLAYER_FIRE_INTENT_CAPABILITY,
                lan_client_module.PLAYER_ENVIRONMENT_CAPABILITY,
                lan_client_module.EFFECTIVE_PARAMS_CAPABILITY,
                lan_client_module.RICOCHET_CONTINUATION_CAPABILITY,
            ],
            'state_revision': 1, 'round_id': 0, 'host_player_id': 1,
            'authority_epoch': 0, 'server_time_ms': 10, 'team_size': 15,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'phase': 'waiting', 'map': '01_karelia',
        }

        self.assertTrue(client._handle_worker_welcome(message))
        self.assertTrue(client.ready)
        self.assertTrue(client._schema_negotiated)
        self.assertIsNone(client.last_error)

    def test_welcome_roster_and_runtime_projection_keep_dummy_local(self):
        events = []
        human = _human()
        human.update({
            'vehicle': 'germany:G54_E-50',
            'health': 1750,
            'max_health': 1750,
        })
        client = AuthorityWorkerLANClient(
            '127.0.0.1', 28782,
            on_event=lambda kind, message: events.append((kind, message)))
        welcome = {
            'type': 'welcome', 'protocol': PROTOCOL_VERSION,
            'role': WORKER_ROLE, 'worker_id': WORKER_AUTHORITY_ID,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES) + [
                SIMULATION_WORKER_CAPABILITY],
            'server_capabilities': [
                lan_client_module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                lan_client_module.HUMAN_RAM_TIMELINE_CAPABILITY,
                lan_client_module.LEAN_SNAPSHOT_MANIFEST_CAPABILITY,
                lan_client_module.RAM_CONTACT_LEDGER_CAPABILITY,
                lan_client_module.PLAYER_FIRE_INTENT_CAPABILITY,
                lan_client_module.PLAYER_ENVIRONMENT_CAPABILITY,
                lan_client_module.EFFECTIVE_PARAMS_CAPABILITY,
                lan_client_module.RICOCHET_CONTINUATION_CAPABILITY,
                lan_client_module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
                lan_client_module.RANDOM_MAP_CAPABILITY],
            'map': '01_karelia', 'map_pool': ['01_karelia'],
            'host_player_id': 1, 'phase': 'waiting', 'round_id': 0,
            'state_revision': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 0, 'server_time_ms': 10, 'team_size': 15,
        }
        client._handle_message(welcome)
        roster = {
            'type': 'roster', 'protocol': PROTOCOL_VERSION,
            'map': '01_karelia', 'map_pool': ['01_karelia'],
            'host_player_id': 1, 'phase': 'waiting', 'round_id': 0,
            'state_revision': 2,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 0, 'players': [dict(human)],
        }
        client._handle_message(roster)

        self.assertTrue(client.ready)
        self.assertEqual(10, client.server_time_ms)
        self.assertEqual([1], [value['id'] for value in client.roster])

        start_players = [dict(human)]
        start = {
            'type': 'battle_start', 'protocol': PROTOCOL_VERSION,
            'map': '01_karelia', 'phase': 'loading', 'round_id': 1,
            'state_revision': 3, 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 0, 'server_time_ms': 11,
            'players': start_players, 'bots': [],
        }
        client._handle_message(start)
        projected = events[-1][1]

        self.assertEqual('battle_start', events[-1][0])
        self.assertEqual([1], [value['id'] for value in start_players])
        self.assertEqual([1, WORKER_AUTHORITY_ID], [
            value['id'] for value in projected['players']])
        dummy = projected['players'][-1]
        self.assertEqual(WORKER_DUMMY_Y, dummy['y'])
        self.assertTrue(dummy['world_pose'])
        self.assertEqual('germany:G54_E-50', dummy['vehicle'])
        self.assertEqual(1, dummy['health'])
        self.assertEqual(1, dummy['max_health'])
        self.assertEqual(0, dummy['input_seq'])
        self.assertEqual(1.0, dummy['up_cosine'])
        self.assertEqual(0, dummy['landing_observation_seq'])
        self.assertEqual([], dummy['equipment_states'])
        self.assertEqual(0, dummy['equipment_revision'])
        self.assertEqual(0, dummy['equipment_intent_seq'])
        self.assertEqual(
            {'intent_seq': 0, 'accepted': False, 'reason': ''},
            dummy['equipment_intent_result'])
        self.assertEqual(1, client.max_health)
        self.assertEqual(WORKER_AUTHORITY_ID, client.player_id)

        snapshot = {
            'type': 'snapshot', 'protocol': PROTOCOL_VERSION,
            'round_id': 1, 'server_tick': 0, 'server_time_ms': 12,
            'authority_epoch': 0, 'projectile_revision': 0,
            'bot_state_revision': 0,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'bot_manifest': [], 'players': [dict(human)], 'bots': [],
            'projectiles': [],
        }
        client._handle_message(snapshot)

        self.assertEqual('snapshot', events[-1][0])
        self.assertEqual([1, WORKER_AUTHORITY_ID], [
            value['id'] for value in client.last_snapshot['players']])
        snapshot_dummy = client.last_snapshot['players'][-1]
        self.assertEqual('germany:G54_E-50', snapshot_dummy['vehicle'])
        self.assertEqual(1, snapshot_dummy['health'])
        self.assertEqual(1, snapshot_dummy['max_health'])
        self.assertIsNone(client.last_error)

    def test_battle_ready_carries_no_dummy_or_participant_identity(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        client.ready = True
        client.phase = 'loading'
        client.round_id = 7
        sent = []
        client._send = lambda message: sent.append(message) or True

        self.assertTrue(client.send_battle_ready({'1': [(1.0, 2.0, 3.0)]}))

        self.assertEqual({
            'type': 'battle_ready', 'round_id': 7,
            'bases': {'1': [(1.0, 2.0, 3.0)]},
        }, sent[0])
        self.assertTrue(set(sent[0]).isdisjoint(
            {'player_id', 'worker_id', 'players', 'spawn'}))

    def test_simulation_progress_proves_worker_frame_not_network_ping(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        client.ready = True
        client.phase = 'battle'
        client.round_id = 7
        client.bot_authority_id = WORKER_AUTHORITY_ID
        client.authority_epoch = 3
        sent = []
        client._send = lambda message: sent.append(message) or True

        self.assertTrue(client.send_simulation_progress(45))
        self.assertEqual({
            'type': 'simulation_progress',
            'round_id': 7,
            'authority_epoch': 3,
            'frame_seq': 45,
        }, sent[0])

    def test_newer_roster_cannot_demote_or_strip_dummy_from_start(self):
        events = []
        client = AuthorityWorkerLANClient(
            '127.0.0.1', 28782,
            on_event=lambda kind, message: events.append((kind, message)))
        client.ready = True
        client.round_id = 4
        client.state_revision = 8
        client.phase = 'loading'
        client.map_name = '01_karelia'
        client.host_player_id = 1
        client.bot_authority_id = WORKER_AUTHORITY_ID
        client.authority_epoch = 2
        client.server_time_ms = 100
        client.roster = [_human()]
        stale_waiting = {
            'type': 'roster', 'protocol': PROTOCOL_VERSION,
            'round_id': 4, 'state_revision': 8, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 2, 'server_time_ms': 100,
            'players': [_human()],
        }

        client._handle_message(stale_waiting)
        self.assertEqual('loading', client.phase)
        self.assertEqual([], events)

        stale_start = {
            'type': 'battle_start', 'protocol': PROTOCOL_VERSION,
            'round_id': 4, 'state_revision': 7, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 2, 'server_time_ms': 99,
            'players': [_human()], 'bots': [],
        }
        client._handle_message(stale_start)

        self.assertIsNone(client.last_error)
        self.assertEqual('battle_start', events[-1][0])
        self.assertEqual([1, WORKER_AUTHORITY_ID], [
            value['id'] for value in events[-1][1]['players']])
        self.assertEqual(8, events[-1][1]['state_revision'])
        self.assertEqual(100, events[-1][1]['server_time_ms'])

    def test_worker_roster_clamps_regressing_server_time(self):
        events = []
        client = AuthorityWorkerLANClient(
            '127.0.0.1', 28782,
            on_event=lambda kind, message: events.append((kind, message)))
        client.running = True
        client.ready = True
        client.round_id = 4
        client.state_revision = 8
        client.phase = 'battle'
        client.map_name = '01_karelia'
        client.host_player_id = 1
        client.bot_authority_id = WORKER_AUTHORITY_ID
        client.authority_epoch = 2
        client.server_time_ms = 100

        client._handle_message({
            'type': 'roster', 'protocol': PROTOCOL_VERSION,
            'round_id': 4, 'state_revision': 9, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 2, 'server_time_ms': 99,
            'players': [_human()],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(100, client.server_time_ms)
        self.assertEqual(100, events[-1][1]['server_time_ms'])

        client._handle_message({
            'type': 'roster', 'protocol': PROTOCOL_VERSION,
            'round_id': 5, 'state_revision': 0, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 3,
            'players': [_human()],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.server_time_ms)
        self.assertNotIn('server_time_ms', events[-1][1])

        client._handle_message({
            'type': 'battle_start', 'protocol': PROTOCOL_VERSION,
            'round_id': 5, 'state_revision': 1, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': WORKER_AUTHORITY_ID,
            'authority_epoch': 3, 'server_time_ms': 0,
            'players': [_human()], 'bots': [],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(0, client.server_time_ms)
        self.assertEqual(0, events[-1][1]['server_time_ms'])

    def test_world_draw_lease_requires_readback_and_restores(self):
        world = _DrawWorld()
        lease = _WorldDrawLease(world)

        self.assertTrue(lease.acquire())
        self.assertFalse(world.enabled)
        self.assertTrue(lease.restore())
        self.assertTrue(world.enabled)
        self.assertEqual([False, True], world.transitions)

    def test_worker_waits_for_every_native_model_before_draw_off(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        runtime.draw_ready = False
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 6
            session._draw = _WorldDrawLease(world)
            session._callback = lambda unused_delay, unused_callback: 1

            session._monitor()
            self.assertEqual('loading_models', session.state)
            self.assertTrue(world.enabled)
            self.assertEqual([], world.transitions)

            session._monitor_callback_id = None
            runtime.draw_ready = True
            session._monitor()

        self.assertEqual('battle', session.state)
        self.assertFalse(world.enabled)
        self.assertEqual([False], world.transitions)

    def test_worker_progress_only_publishes_an_advancing_frame(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        session = WorkerSession({}, bigworld=world)
        session.client = client

        self.assertTrue(session._publish_simulation_progress(runtime))
        self.assertEqual([1], client.progress)
        session._next_progress_time = 0.0
        self.assertFalse(session._publish_simulation_progress(runtime))
        runtime.sample['frame_callbacks'] = 2
        self.assertTrue(session._publish_simulation_progress(runtime))
        self.assertEqual([1, 2], client.progress)

    def test_worker_progress_stops_as_soon_as_the_result_is_applied(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        session = WorkerSession({}, bigworld=world)
        session.client = client

        self.assertTrue(session._publish_simulation_progress(runtime))
        session._next_progress_time = 0.0
        runtime.sample['frame_callbacks'] = 2
        runtime.sample['round_finished'] = True

        self.assertFalse(session._publish_simulation_progress(runtime))
        self.assertEqual([1], client.progress)

    def test_authority_loss_fences_then_tears_down_while_hidden(self):
        world = _DrawWorld()
        client = _WorkerClient()
        calls = []
        runtime = _WorkerRuntime(client, world, calls)
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 7
            session._draw = _WorldDrawLease(world)
            session._draw.acquire()

            self.assertTrue(session._retire_runtime('authority_lost'))

            self.assertEqual(
                [('runtime_stop', None, False, False, True)], calls)
            self.assertTrue(world.enabled)
            self.assertEqual([False, True], world.transitions)
            self.assertIn(7, session._retired_rounds)
            client.bot_authority_id = WORKER_AUTHORITY_ID
            client.phase = 'loading'
            self.assertFalse(session._start_round({
                'round_id': 7, 'players': [_human(), {
                    'id': WORKER_AUTHORITY_ID,
                    'name': 'SimulationWorker',
                    'vehicle': 'ussr:R11_MS-1',
                    'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0,
                }]}))

    def test_stop_restores_draw_even_when_native_teardown_fails(self):
        world = _DrawWorld()
        client = _WorkerClient()
        calls = []
        runtime = _WorkerRuntime(
            client, world, calls, fail_stop=True)
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 8
            session._draw = _WorldDrawLease(world)
            session._draw.acquire()

            with self.assertRaisesRegex(RuntimeError, 'native teardown'):
                session.stop()

        self.assertEqual(
            [('runtime_stop', None, False, False, False)], calls)
        self.assertTrue(world.enabled)
        self.assertTrue(client.stopped)
        self.assertIs(runtime, session.runtime)
        self.assertIsNone(session.client)

        runtime.fail_stop = False
        session.stop()

        self.assertIsNone(session.runtime)
        self.assertEqual(2, len(calls))

    def test_native_cleanup_failure_fences_worker_without_retry(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world, fail_stop=True)
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 9
            session._draw = _WorldDrawLease(world)
            session._callback = mock.Mock(return_value=123)

            self.assertFalse(session._worker_failure(
                RuntimeError('native event failed')))

        self.assertEqual('failed', session.state)
        self.assertTrue(session._stopped)
        self.assertTrue(client.stopped)
        self.assertIs(runtime, session.runtime)
        self.assertIsNone(session.client)
        self.assertIsNone(session._retry_callback_id)
        session._callback.assert_not_called()

        runtime.fail_stop = False
        session.stop()

        self.assertIsNone(session.runtime)

    def test_busy_worker_rechecks_at_fixed_delay_and_welcome_resets_it(self):
        world = _DrawWorld()
        scheduled = []
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session._callback = lambda delay, callback: (
                scheduled.append((delay, callback)) or len(scheduled))

            self.assertTrue(session._worker_failure(
                RuntimeError('battle already in progress')))
            self.assertEqual(WORKER_BUSY_RETRY_SECONDS, scheduled[-1][0])

            session._retry_callback_id = None
            self.assertTrue(session._worker_failure(
                RuntimeError('battle already in progress')))
            self.assertEqual(WORKER_BUSY_RETRY_SECONDS, scheduled[-1][0])

            client = _WorkerClient()
            client.phase = 'waiting'
            session.client = client
            session._on_event('welcome', {'phase': 'waiting'})

        self.assertEqual(WORKER_RETRY_SECONDS, session._retry_delay)

    def test_retry_polls_unready_lobby_without_growing_network_backoff(self):
        world = _DrawWorld()
        scheduled = []
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world, lobby_ready=lambda: False,
                status_path=str(Path(directory) / 'status.json'))
            session._retry_delay = WORKER_BUSY_RETRY_SECONDS
            session._callback = lambda delay, callback: (
                scheduled.append((delay, callback)) or len(scheduled))

            self.assertTrue(session._schedule_retry(grow=False))
            self.assertEqual(WORKER_BUSY_RETRY_SECONDS, scheduled[-1][0])
            scheduled[-1][1]()

        self.assertEqual(WORKER_RETRY_SECONDS, scheduled[-1][0])
        self.assertEqual(WORKER_BUSY_RETRY_SECONDS, session._retry_delay)

    def test_event_adapter_exception_enters_worker_failure_boundary(self):
        world = _DrawWorld()
        holder = []

        def factory(unused_host, unused_port, on_event=None, bigworld=None):
            del bigworld
            client = _WorkerClient()
            client.on_event = on_event
            client.start = lambda: True
            holder.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, client_factory=factory, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session._callback = mock.Mock(return_value=1)
            session._cancel_callback = mock.Mock()
            session._on_event = mock.Mock(
                side_effect=RuntimeError('native adapter failed'))
            session._worker_failure = mock.Mock()

            self.assertTrue(session._connect())
            holder[0].on_event('battle_live', {})

        session._worker_failure.assert_called_once()
        self.assertIn(
            'native adapter failed',
            str(session._worker_failure.call_args[0][0]))

    def test_worker_routes_terminal_player_launch_result_to_runtime(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        message = {
            'type': 'fire_intent_result', 'round_id': 1,
            'player_id': 2, 'intent_seq': 3, 'accepted': False,
            'reason': 'projectile_launch_rejected',
        }
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 1

            session._on_event('fire_intent_result', message)

        self.assertEqual([message], runtime.fire_intent_results)

    def test_worker_routes_player_destructible_contact_to_runtime(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        message = {
            'type': 'player_destructible_contact', 'round_id': 1,
            'protocol': PROTOCOL_VERSION,
            'authority_epoch': 1, 'player': {
                'id': 2, 'vehicle': 'ussr:R11_MS-1',
                'vehicle_compact_descr': 'dGVzdA==',
                'destructible_contacts': [{
                    'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                    'yaw': 0.0, 'speed': 8.0, 'dt': 0.04,
                    'forward': 1.0, 'token': [[22, 3, None]],
                }],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 1

            session._on_event('player_destructible_contact', message)

        self.assertEqual([message], runtime.player_destructible_contacts)

    def test_worker_inherits_cached_effective_params_for_lean_contact(self):
        received = []
        client = AuthorityWorkerLANClient(
            '127.0.0.1', 28782,
            on_event=lambda kind, message: received.append((kind, message)))
        client._published_player_effective_params[2] = effective_params()
        message = {
            'type': 'player_destructible_contact', 'round_id': 1,
            'protocol': PROTOCOL_VERSION,
            'authority_epoch': 1, 'player': {
                'id': 2, 'vehicle': 'ussr:R11_MS-1',
                'vehicle_compact_descr': 'dGVzdA==',
                'destructible_contacts': [],
            },
        }

        client._handle_message(message)

        self.assertEqual('player_destructible_contact', received[0][0])
        self.assertEqual(
            effective_params(),
            received[0][1]['player']['effective_params'])
        self.assertNotIn('effective_params', message['player'])

    def test_worker_forces_compound_factory_and_track_animation_off(self):
        world = _DrawWorld()
        client = _WorkerClient()
        client.phase = 'loading'
        runtime = _WorkerRuntime(client, world)
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {'native_remote_vehicles': True,
                 'bot_track_animation': True},
                battle_factory=lambda: runtime, bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client

            self.assertTrue(session._start_round({
                'round_id': 9, 'map': '01_karelia',
                'players': [_human(), {
                    'id': WORKER_AUTHORITY_ID,
                    'name': 'SimulationWorker',
                    'vehicle': 'ussr:R11_MS-1',
                    'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0,
                    'yaw': 0.0,
                }]}))

        self.assertTrue(runtime.start_config['worker_mode'])
        self.assertFalse(runtime.start_config['native_remote_vehicles'])
        self.assertFalse(runtime.start_config['bot_track_animation'])

    def test_next_round_waits_for_restored_lobby_before_native_start(self):
        world = _DrawWorld()
        client = _WorkerClient()
        client.phase = 'loading'
        runtime = _WorkerRuntime(client, world)
        ready = [False]
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, battle_factory=lambda: runtime,
                lobby_ready=lambda: ready[0], bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session._callback = lambda unused_delay, unused_callback: 1
            session._draw = _WorldDrawLease(world)
            start = {
                'round_id': 11, 'map': '01_karelia',
                'players': [_human(), {
                    'id': WORKER_AUTHORITY_ID,
                    'name': 'SimulationWorker',
                    'vehicle': 'ussr:R11_MS-1',
                    'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0,
                    'yaw': 0.0,
                }]}

            self.assertTrue(session._start_round(start))
            self.assertEqual('waiting_lobby', session.state)
            self.assertIsNone(session.runtime)
            self.assertIsNone(runtime.start_config)

            ready[0] = True
            session._monitor()

        self.assertIs(session.runtime, runtime)
        self.assertIsNone(session._pending_start)
        self.assertIsNotNone(runtime.start_config)

    def test_new_round_start_can_overtake_previous_waiting_roster(self):
        world = _DrawWorld()
        client = _WorkerClient()
        client.round_id = 12
        client.phase = 'loading'
        old_calls = []
        old_runtime = _WorkerRuntime(client, world, old_calls)
        new_runtime = _WorkerRuntime(client, world)
        ready = [False]
        with tempfile.TemporaryDirectory() as directory:
            session = WorkerSession(
                {}, battle_factory=lambda: new_runtime,
                lobby_ready=lambda: ready[0], bigworld=world,
                status_path=str(Path(directory) / 'status.json'))
            session.client = client
            session.runtime = old_runtime
            session._active_round_id = 11
            session._draw = _WorldDrawLease(world)
            session._callback = lambda unused_delay, unused_callback: 1
            start = {
                'round_id': 12, 'map': '01_karelia',
                'players': [_human(), {
                    'id': WORKER_AUTHORITY_ID,
                    'name': 'SimulationWorker',
                    'vehicle': 'ussr:R11_MS-1',
                    'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0,
                    'yaw': 0.0,
                }]}

            self.assertTrue(session._start_round(start))
            self.assertEqual('waiting_lobby', session.state)
            self.assertIsNone(session.runtime)
            self.assertEqual(12, session._pending_start['round_id'])
            self.assertEqual(
                [('runtime_stop', WORKER_AUTHORITY_ID,
                  True, False, True)], old_calls)

            ready[0] = True
            session._monitor()

        self.assertIs(session.runtime, new_runtime)
        self.assertIsNotNone(new_runtime.start_config)

    def test_status_contains_rolling_publication_rates(self):
        world = _DrawWorld()
        client = _WorkerClient()
        runtime = _WorkerRuntime(client, world)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            session = WorkerSession({}, bigworld=world, status_path=str(path))
            session.client = client
            session.runtime = runtime
            session._active_round_id = 10
            session._draw = _WorldDrawLease(world)
            original_write = port_config.write_json
            durable_values = []

            def write_status(path_value, value_value, durable=True):
                durable_values.append(durable)
                return original_write(
                    path_value, value_value, durable=durable)

            with mock.patch.object(
                    port_config, 'write_json', side_effect=write_status):
                with mock.patch(
                        'gui.mods.offline_lan_0922.authority_worker.time.time',
                        side_effect=(100.0, 102.0, 104.0)):
                    self.assertTrue(session._write_status(force=True))
                    runtime.sample.update({
                        'authority_callbacks': 48,
                        'bot_state_generated': 61,
                        'bot_state_enqueued': 60,
                        'bot_state_send_failed': 1,
                        'bot_state_revision': 52,
                        'bot_probes': {'lane': 17},
                        'bot_count': 29,
                        'alive_bot_ticks': 116,
                    })
                    self.assertTrue(session._write_status(force=True))
                    value = json.loads(path.read_text(encoding='utf-8'))
                    session.runtime = None
                    self.assertTrue(session._write_status(force=True))
                    empty = json.loads(path.read_text(encoding='utf-8'))

        self.assertTrue(value['connected'])
        self.assertEqual('battle', value['phase'])
        self.assertEqual(2.0, value['runtime']['window_seconds'])
        self.assertEqual(24.0, value['runtime']['callback_hz'])
        self.assertEqual(30.0, value['runtime']['bot_publication_hz'])
        self.assertEqual(52, value['runtime']['revision_delta'])
        self.assertEqual(1, value['runtime']['send_failed_delta'])
        self.assertEqual({'lane': 17}, value['runtime']['bot_probes'])
        self.assertEqual({}, empty['runtime'])
        self.assertEqual([False, False, False], durable_values)

    def test_in_memory_account_state_never_calls_json_writer(self):
        state = AccountState(path=None)
        with mock.patch.object(port_config, 'write_json') as writer:
            state.add_int_settings((11, 22))
            state.del_int_settings((11,))
        writer.assert_not_called()

    def test_worker_bootstrap_uses_no_persistent_user_store(self):
        bootstrap_path = (
            CLIENT_ROOT / 'gui' / 'mods' / 'offline_lan_0922' /
            'bootstrap.py')
        bigworld = types.ModuleType('BigWorld')
        bigworld.callback = lambda unused_delay, unused_callback: 1
        bigworld.cancelCallback = lambda unused_id: None
        config = types.ModuleType('gui.mods.offline_lan_0922.config')
        config.SIMULATION_WORKER_MODE = 'simulation_worker'
        config.load = mock.Mock(return_value={
            'enabled': True, 'vehicle': 'ussr:R11_MS-1'})
        config.client_mode = mock.Mock(return_value='simulation_worker')
        compatibility = types.SimpleNamespace(
            fini=mock.Mock(), is_ready=mock.Mock(return_value=True))
        compat = types.ModuleType('gui.mods.offline_lan_0922.compat')
        compat.g_compatibility = compatibility
        blacklist = types.ModuleType(
            'gui.mods.offline_lan_0922.vehicle_blacklist')
        state = AccountState(path=None)
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = mock.Mock(return_value=state)
        constants = types.ModuleType('constants')
        constants.USER_SERVER_SETTINGS = types.SimpleNamespace(
            EULA_VERSION=54)
        eula_loader = types.ModuleType(
            'gui.doc_loaders.EULAVersionLoader')
        eula_loader.EULAVersionLoader = mock.Mock(
            return_value=types.SimpleNamespace(xmlVersion=25))
        package = sys.modules['gui.mods.offline_lan_0922']

        modules = {
            'BigWorld': bigworld,
            'gui.mods.offline_lan_0922.config': config,
            'gui.mods.offline_lan_0922.compat': compat,
            'gui.mods.offline_lan_0922.vehicle_blacklist': blacklist,
            'gui.mods.offline_lan_0922.account_rpc.state': state_module,
            'constants': constants,
            'gui.doc_loaders.EULAVersionLoader': eula_loader,
        }
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(package, 'config', config), \
                mock.patch.object(package, 'vehicle_blacklist', blacklist):
            spec = importlib.util.spec_from_file_location(
                'worker_bootstrap_0922', bootstrap_path)
            bootstrap = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bootstrap)
            bootstrap._selected_vehicle = mock.Mock(
                return_value={'id': 1, 'compDescr': 123})
            bootstrap._garage_store = mock.Mock()
            bootstrap._battle_results_store = mock.Mock()
            bootstrap._wait_for_login_space = mock.Mock()
            bootstrap._client_guard_released = True

            bootstrap._run_once()
            eula_loader.EULAVersionLoader.side_effect = RuntimeError(
                'version read failed')
            with self.assertRaisesRegex(RuntimeError, 'version read failed'):
                bootstrap._worker_account_state()

        state_module.AccountState.assert_called_once_with(path=None)
        self.assertEqual(2, eula_loader.EULAVersionLoader.call_count)
        self.assertEqual({54: 25}, state.snapshot())
        bootstrap._selected_vehicle.assert_called_once_with(
            config.load.return_value, restore_saved=False)
        bootstrap._garage_store.assert_not_called()
        bootstrap._battle_results_store.assert_not_called()
        self.assertEqual({
            'selected_vehicle': {'id': 1, 'compDescr': 123},
            'account_state': state,
        }, bootstrap._account_context)
        bootstrap._wait_for_login_space.assert_called_once_with()

        signal_ready = mock.Mock(return_value=True)
        schedule = mock.Mock()
        bootstrap._signal_worker_ready = signal_ready
        bootstrap._schedule = schedule
        bootstrap._worker_ready_signaled = False
        disconnected = types.SimpleNamespace(
            connected=False, ready=False)
        worker_session = types.SimpleNamespace(
            client=disconnected, state='connecting', start=mock.Mock(
                return_value=True))
        bootstrap._session = worker_session

        app_loader = types.ModuleType('gui.app_loader')
        app_loader.g_appLoader = types.SimpleNamespace(
            getDefLobbyApp=mock.Mock(return_value=object()))
        bootstrap._lobby_is_ready = mock.Mock(return_value=True)
        bootstrap._remove_lobby_listener = mock.Mock()
        with mock.patch.dict(sys.modules, {'gui.app_loader': app_loader}), \
                mock.patch.object(bootstrap.time, 'time', return_value=5.0):
            bootstrap._wait_for_lobby()
        worker_session.start.assert_called_once_with()
        signal_ready.assert_not_called()
        self.assertEqual(35.0, bootstrap._deadline)
        schedule.assert_called_once_with(
            0.10, bootstrap._wait_for_worker_connection)

        schedule.reset_mock()
        bootstrap._deadline = 100.0
        worker_session.state = 'retrying'
        with mock.patch.object(bootstrap.time, 'time', return_value=10.0):
            bootstrap._wait_for_worker_connection()
        signal_ready.assert_not_called()
        schedule.assert_called_once_with(
            0.10, bootstrap._wait_for_worker_connection)

        schedule.reset_mock()
        worker_session.client = types.SimpleNamespace(
            connected=True, ready=True)
        with mock.patch.object(bootstrap.time, 'time', return_value=11.0):
            bootstrap._wait_for_worker_connection()
            bootstrap._wait_for_worker_connection()
        signal_ready.assert_called_once_with()
        schedule.assert_not_called()
        self.assertTrue(bootstrap._worker_ready_signaled)
        self.assertEqual(0.0, bootstrap._deadline)

        bootstrap._worker_ready_signaled = False
        bootstrap._deadline = 12.0
        worker_session.client = disconnected
        bootstrap._fail_startup = mock.Mock()
        with mock.patch.object(bootstrap.time, 'time', return_value=13.0):
            bootstrap._wait_for_worker_connection()
        signal_ready.assert_called_once_with()
        schedule.assert_not_called()
        error = bootstrap._fail_startup.call_args[0][0]
        self.assertEqual(
            'simulation worker connection timed out', str(error))

        cancel_callback = mock.Mock()
        bigworld.cancelCallback = cancel_callback
        pending_session = types.SimpleNamespace(stop=mock.Mock())
        bootstrap._session = pending_session
        bootstrap._callback_id = 91
        bootstrap.fini()
        cancel_callback.assert_called_once_with(91)
        pending_session.stop.assert_called_once_with(
            show_login=False, restore_account=False, release_join=True)


if __name__ == '__main__':
    unittest.main()
