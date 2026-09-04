import json
import math
import socket
import sys
import types
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import lan_client as module
from gui.mods.offline_lan_0922 import descriptor_donation
from gui.mods.offline_lan_0922.authority_worker import (
    AuthorityWorkerLANClient)
from gui.mods.offline_lan_0922.lan_client import LANClient
from effective_params_fixture import effective_params


class RecordingSocket(object):
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, payload):
        self.sent.append(payload)

    def settimeout(self, unused_timeout):
        pass

    def connect(self, unused_address):
        pass

    def setsockopt(self, *unused_args):
        pass

    def close(self):
        self.closed = True


def wire_copy(value):
    return json.loads(json.dumps(value, separators=(',', ':')))


def source_shot(speed, gravity, maximum, is_he=False, radius=0.0,
                damage=(390.0, 150.0), deadeye=False):
    return {
        'speed': speed,
        'gravity': gravity,
        'maxDistance': maximum,
        'piercingPower': [220.0, 200.0],
        'deadeye': bool(deadeye),
        'shell': {
            'kind': 'HIGH_EXPLOSIVE' if is_he else 'ARMOR_PIERCING',
            'caliber': 105.0,
            'damage': list(damage),
            'explosionRadius': radius,
        },
    }


class ProjectileWireTests(unittest.TestCase):

    def test_projected_he_shot_freezes_descriptor_owned_factors(self):
        shell_type = types.SimpleNamespace(
            name='HIGH_EXPLOSIVE', explosionRadius=4.5,
            explosionDamageFactor=0.55,
            explosionDamageAbsorptionFactor=1.4,
            explosionEdgeDamageFactor=0.2)
        shot = types.SimpleNamespace(
            speed=720.0, gravity=9.81, maxDistance=500.0,
            piercingPower=(53.0, 53.0),
            shell=types.SimpleNamespace(
                type=shell_type, caliber=122.0, damage=(450.0, 90.0)))

        projected = descriptor_donation.project_shot(shot)

        self.assertEqual('HIGH_EXPLOSIVE', projected['shell']['kind'])
        self.assertEqual(4.5, projected['shell']['explosionRadius'])
        self.assertEqual(0.55,
                         projected['shell']['explosionDamageFactor'])
        self.assertEqual(
            1.4, projected['shell']['explosionDamageAbsorptionFactor'])
        self.assertEqual(0.2,
                         projected['shell']['explosionEdgeDamageFactor'])

    @staticmethod
    def gun_checkpoint(reload_time=0.0, clip=1, dispersion=0.02):
        return {
            'reload_time': reload_time, 'reload_duration': 5.0,
            'clip': clip, 'clip_size': 1,
            'dispersion': dispersion,
        }
    def test_player_siege_snapshot_pair_is_strict_when_present(self):
        self.assertTrue(module._valid_player_siege_contract({}))
        self.assertTrue(module._valid_player_siege_contract({
            'siege_state': 1, 'siege_time_left_ms': 2000}))
        self.assertTrue(module._valid_player_siege_contract({
            'siege_state': 2, 'siege_time_left_ms': 0}))
        self.assertFalse(module._valid_player_siege_contract({
            'siege_state': 1, 'siege_time_left_ms': 0}))
        self.assertFalse(module._valid_player_siege_contract({
            'siege_state': 2, 'siege_time_left_ms': 1}))
        self.assertFalse(module._valid_player_siege_contract({
            'siege_state': True, 'siege_time_left_ms': 0}))
        self.assertFalse(module._valid_player_siege_contract({
            'siege_state': 0}))

    def test_player_gun_checkpoint_pair_is_strict_when_present(self):
        checkpoint = self.gun_checkpoint()
        self.assertTrue(module._valid_player_gun_checkpoint_contract({}))
        self.assertTrue(module._valid_player_gun_checkpoint_contract({
            'input_seq': 3, 'gun_checkpoint_seq': 3,
            'gun_checkpoint': checkpoint}))
        self.assertFalse(module._valid_player_gun_checkpoint_contract({
            'input_seq': 3, 'gun_checkpoint_seq': 2,
            'gun_checkpoint': checkpoint}))
        self.assertFalse(module._valid_player_gun_checkpoint_contract({
            'input_seq': 3, 'gun_checkpoint': checkpoint}))

    def active_client(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.sock = RecordingSocket()
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = 'battle'
        client.round_id = 3
        client.player_id = 7
        client.bot_authority_id = module.WORKER_AUTHORITY_ID
        client.authority_epoch = 4
        client.capabilities = list(module.CLIENT_CAPABILITIES)
        client.server_capabilities = [
            module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            module.RICOCHET_CONTINUATION_CAPABILITY,
            module.RAM_CONTACT_LEDGER_CAPABILITY,
            module.HUMAN_RAM_TIMELINE_CAPABILITY,
            module.PLAYER_FIRE_INTENT_CAPABILITY,
            module.PLAYER_ENVIRONMENT_CAPABILITY,
            module.EFFECTIVE_PARAMS_CAPABILITY,
            module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
            module.PROJECTILE_WRECK_HIT_CAPABILITY,
            module.RANDOM_MAP_CAPABILITY,
        ]
        with client._outbound_lock:
            client._outbound_accepting = True
        return client

    def active_worker_client(self):
        client = AuthorityWorkerLANClient('127.0.0.1', 28782)
        client.sock = RecordingSocket()
        client.running = True
        client.connected = True
        client.ready = True
        client.phase = 'battle'
        client.round_id = 3
        client.player_id = module.WORKER_AUTHORITY_ID
        client.bot_authority_id = module.WORKER_AUTHORITY_ID
        client.authority_epoch = 4
        client.capabilities = list(module.CLIENT_CAPABILITIES) + [
            module.SIMULATION_WORKER_CAPABILITY]
        client.server_capabilities = [
            module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            module.RICOCHET_CONTINUATION_CAPABILITY,
            module.RAM_CONTACT_LEDGER_CAPABILITY,
            module.HUMAN_RAM_TIMELINE_CAPABILITY,
            module.PLAYER_FIRE_INTENT_CAPABILITY,
            module.PLAYER_ENVIRONMENT_CAPABILITY,
            module.EFFECTIVE_PARAMS_CAPABILITY,
            module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
            module.PROJECTILE_WRECK_HIT_CAPABILITY,
            module.RANDOM_MAP_CAPABILITY,
        ]
        with client._outbound_lock:
            client._outbound_accepting = True
        return client

    @staticmethod
    def launch(client, shooter_kind='player', shooter_id=7,
               shot_seq=None, authority_epoch=None, fire_intent_seq=None,
               fire_input_seq=None, **overrides):
        values = {
            'shell_index': 2,
            'origin': [1.0, 2.0, 3.0],
            'velocity': [100.0, 20.0, -30.0],
            'gravity': 9.81,
            'max_distance': 720.0,
            'max_time_ms': 5000,
            'is_he': True,
            'splash_radius': 4.5,
            'penetration_factor': 1.25,
        }
        values.update(overrides)
        frozen_shot = values.get('source_shot')
        if frozen_shot is None:
            frozen_shot = source_shot(
                math.sqrt(sum(component * component
                              for component in values['velocity'])),
                values['gravity'], values['max_distance'],
                values['is_he'], values['splash_radius'])
        return client.send_projectile_launch(
            shooter_kind, shooter_id, shot_seq, values['shell_index'],
            values['origin'], values['velocity'], values['gravity'],
            values['max_distance'], values['max_time_ms'], values['is_he'],
            values['splash_radius'], authority_epoch=authority_epoch,
            penetration_factor=values['penetration_factor'],
            source_shot=frozen_shot,
            fire_intent_seq=fire_intent_seq,
            fire_input_seq=fire_input_seq,
            burst_group_seq=values.get('burst_group_seq'),
            burst_index=values.get('burst_index'),
            burst_count=values.get('burst_count'),
            launch_time_us=(values.get('launch_time_us', 100000)
                            if shooter_kind == 'bot' else None),
            launch_pose=(values.get(
                'launch_pose', [1.0, 0.0, 3.0, 0.0, 0.0, 0.0])
                if shooter_kind == 'bot' else None))

    @staticmethod
    def send_player_input(client, shell_index=2):
        return client.send_input(
            0.5, -0.25, aim_yaw=0.3, gun_pitch=-0.1,
            position=[1.0, 2.0, 3.0], yaw=0.2, speed=4.0,
            shell_index=shell_index, next_shell_index=shell_index,
            shell_change_pending=False,
            gun_checkpoint=ProjectileWireTests.gun_checkpoint(),
            pose_time_us=1000)

    def test_worker_player_launch_is_frozen_fifo_wire_with_intent_identity(self):
        client = self.active_worker_client()
        origin = [1.0, 2.0, 3.0]

        self.assertIsNone(self.launch(
            client, shot_seq=1, origin=origin, authority_epoch=4,
            fire_input_seq=9))
        self.assertIsNone(self.launch(
            client, shot_seq=1, origin=origin, authority_epoch=4,
            fire_intent_seq=6))
        self.assertEqual(1, self.launch(
            client, shot_seq=1, origin=origin, authority_epoch=4,
            fire_intent_seq=6, fire_input_seq=9))
        origin[0] = 999.0
        self.assertEqual(1, len(client._outbound_queue))
        frozen = client._outbound_queue[0][1]
        self.assertEqual((1.0, 2.0, 3.0), frozen['origin'])

        self.assertTrue(client._send_wire(
            frozen, client.sock, client._transport_generation))
        self.assertTrue(client.sock.sent[0].endswith(b'\n'))
        message = json.loads(client.sock.sent[0].decode('utf-8'))
        self.assertEqual({
            'type', 'round_id', 'shooter_kind', 'shooter_id', 'shot_seq',
            'shell_index', 'origin', 'velocity', 'gravity', 'max_distance',
            'max_time_ms', 'is_he', 'splash_radius', 'penetration_factor',
            'source_shot', 'authority_epoch', 'fire_intent_seq',
            'fire_input_seq', 'burst_group_seq', 'burst_index',
            'burst_count',
        }, set(message))
        self.assertEqual('player', message['shooter_kind'])
        self.assertEqual(7, message['shooter_id'])
        self.assertEqual(1, message['shot_seq'])
        self.assertEqual(6, message['fire_intent_seq'])
        self.assertEqual(9, message['fire_input_seq'])
        self.assertEqual((1, 0, 1), (
            message['burst_group_seq'], message['burst_index'],
            message['burst_count']))
        self.assertEqual([1.0, 2.0, 3.0], message['origin'])

    def test_failed_fire_intent_enqueue_rolls_back_sequence(self):
        client = self.active_client()
        self.assertTrue(self.send_player_input(client))
        client._send = lambda unused_message: False

        self.assertIsNone(client.send_fire_intent(
            2, [1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.01))
        self.assertEqual(0, client._fire_intent_seq)

        client._send = lambda unused_message: True
        self.assertEqual(1, client.send_fire_intent(
            2, [1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.01))
        self.assertEqual(1, client._fire_intent_seq)

    def test_visible_fire_intent_requires_input_and_sequences_monotonically(self):
        client = self.active_client()

        self.assertIsNone(client.send_fire_intent(
            2, [1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.01))
        self.assertTrue(self.send_player_input(client))
        self.assertEqual(1, client.send_fire_intent(
            2, [1.0, 2.0, 3.0], [0.0, 0.0, 1.0], 0.01))
        self.assertTrue(self.send_player_input(client, shell_index=1))
        self.assertEqual(2, client.send_fire_intent(
            1, [1.0, 2.0, 3.0], [1.0, 0.0, 0.0], 0.02))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'intent_seq', 'input_seq', 'shell_index',
            'shot_origin', 'shot_direction', 'dispersion_angle',
        }, set(message))
        self.assertEqual('fire_intent', message['type'])
        self.assertEqual(2, message['intent_seq'])
        self.assertEqual(2, message['input_seq'])
        self.assertEqual([1.0, 2.0, 3.0], message['shot_origin'])
        self.assertEqual([1.0, 0.0, 0.0], message['shot_direction'])
        self.assertEqual(0.02, message['dispersion_angle'])

    def test_player_input_carries_one_atomic_queued_shell_selection(self):
        client = self.active_client()

        self.assertTrue(client.send_input(
            0.0, 0.0, position=[1.0, 2.0, 3.0], yaw=0.0,
            shell_index=0, next_shell_index=1,
            shell_change_pending=True,
            gun_checkpoint=self.gun_checkpoint(), pose_time_us=1000))

        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(0, message['shell_index'])
        self.assertEqual(1, message['next_shell_index'])
        self.assertTrue(message['shell_change_pending'])
        self.assertEqual(self.gun_checkpoint(), message['gun_checkpoint'])
        self.assertFalse(client.send_input(
            0.0, 0.0, shell_index=0, next_shell_index=0,
            shell_change_pending=False, pose_time_us=1001))
        self.assertFalse(client.send_input(
            0.0, 0.0, shell_index=0, next_shell_index=0,
            shell_change_pending=False, pose_time_us=1001,
            gun_checkpoint=dict(self.gun_checkpoint(), clip=2)))
        self.assertFalse(client.send_input(
            0.0, 0.0, shell_index=0, next_shell_index=1))
        self.assertFalse(client.send_input(
            0.0, 0.0, shell_index=0, next_shell_index=0,
            shell_change_pending=1))

    def test_visible_input_carries_full_world_up_without_expanding_euler(self):
        client = self.active_client()
        up_cosine = math.cos(math.radians(85.0))

        self.assertTrue(client.send_input(
            0.0, 0.0, position=[1.0, 2.0, 3.0], yaw=0.0,
            pitch=1.5, roll=-1.5, up_cosine=up_cosine,
            pose_time_us=1000, shell_index=0, next_shell_index=0,
            shell_change_pending=False,
            gun_checkpoint=self.gun_checkpoint()))

        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(0.61, message['pitch'])
        self.assertEqual(-0.61, message['roll'])
        self.assertAlmostEqual(up_cosine, message['up_cosine'])
        count = len(client._outbound_queue)
        for invalid in (True, '0.5', float('nan'), float('inf'), -1.01, 1.01):
            with self.subTest(up_cosine=invalid):
                self.assertFalse(client.send_input(
                    0.0, 0.0, position=[1.0, 2.0, 3.0], yaw=0.0,
                    up_cosine=invalid, pose_time_us=1001,
                    shell_index=0, next_shell_index=0,
                    shell_change_pending=False,
                    gun_checkpoint=self.gun_checkpoint()))
        self.assertEqual(count, len(client._outbound_queue))

    def test_landing_observations_are_sequenced_and_acknowledged(self):
        client = self.active_client()
        self.assertTrue(self.send_player_input(client))

        self.assertEqual(1, client.send_landing_observation(20.1256789))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'observation_seq',
            'input_seq', 'impact_speed'}, set(message))
        self.assertEqual(1, message['observation_seq'])
        self.assertEqual(1, message['input_seq'])
        self.assertEqual(20.125679, message['impact_speed'])

        self.assertTrue(self.send_player_input(client))
        self.assertEqual(1, client.send_landing_observation(25.0))
        self.assertTrue(client._handle_landing_observation_result({
            'type': 'landing_observation_result', 'round_id': 3,
            'authority_epoch': 4, 'observation_seq': 1,
            'input_seq': 1, 'committed_seq': 1,
            'accepted': True, 'reason': '',
        }))
        queued = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(2, queued['observation_seq'])
        self.assertEqual(2, queued['input_seq'])
        self.assertEqual(25.0, queued['impact_speed'])

    def test_landing_result_accepts_only_receive_timestamp_metadata(self):
        client = self.active_client()
        self.assertTrue(self.send_player_input(client))
        self.assertEqual(1, client.send_landing_observation(20.0))
        result = {
            'type': 'landing_observation_result', 'round_id': 3,
            'authority_epoch': 4, 'observation_seq': 1,
            'input_seq': 1, 'committed_seq': 1,
            'accepted': True, 'reason': '',
            '_client_received_time': 10.0,
        }

        client._handle_message(result)

        self.assertTrue(client.running)
        self.assertIsNone(client._landing_observation_pending)
        invalid = dict(result)
        invalid['unexpected'] = True
        self.assertFalse(client._handle_landing_observation_result(invalid))

    def test_invalid_landing_result_preserves_pending_observation(self):
        client = self.active_client()
        self.assertTrue(self.send_player_input(client))
        self.assertEqual(1, client.send_landing_observation(20.0))
        pending = client._landing_observation_pending
        client._handle_message({
            'type': 'landing_observation_result', 'round_id': 3,
            'authority_epoch': 4, 'observation_seq': 1,
            'input_seq': 1, 'committed_seq': 1,
            'accepted': True, 'reason': '', 'unexpected': True,
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertIs(pending, client._landing_observation_pending)

    def test_failed_landing_enqueue_retries_same_physical_observation(self):
        client = self.active_client()
        self.assertTrue(self.send_player_input(client))
        attempts = []

        def send(message):
            attempts.append(wire_copy(message))
            return len(attempts) > 1

        client._send = send
        self.assertFalse(client.send_landing_observation(18.0))
        self.assertTrue(client.send_input(
            0.0, 0.0, position=[1.0, 2.0, 3.0], yaw=0.0,
            pose_time_us=2000, shell_index=0, next_shell_index=0,
            shell_change_pending=False,
            gun_checkpoint=self.gun_checkpoint()))
        self.assertEqual(1, client.send_landing_observation(18.0))
        self.assertEqual(
            ['landing_observation', 'input', 'landing_observation'],
            [value['type'] for value in attempts])
        self.assertEqual(1, attempts[0]['observation_seq'])
        self.assertEqual(1, attempts[2]['observation_seq'])
        self.assertEqual(2, attempts[2]['input_seq'])

    def test_visible_client_cannot_publish_player_projectile_launch(self):
        client = self.active_client()

        self.assertIsNone(self.launch(
            client, shot_seq=1, authority_epoch=4,
            fire_intent_seq=1, fire_input_seq=1))
        self.assertEqual([], client._outbound_queue)

    def test_bot_launch_requires_current_authority_and_does_not_use_player_seq(self):
        client = self.active_worker_client()
        client._fire_seq = 9

        client.bot_authority_id = 7
        self.assertIsNone(self.launch(
            client, 'bot', 17, 3, authority_epoch=4))
        client.bot_authority_id = module.WORKER_AUTHORITY_ID
        self.assertIsNone(self.launch(
            client, 'bot', 17, 3, authority_epoch=3))
        self.assertEqual(3, self.launch(
            client, 'bot', 17, 3, authority_epoch=4))
        self.assertEqual(9, client._fire_seq)
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(4, message['authority_epoch'])
        self.assertEqual('bot', message['shooter_kind'])
        self.assertEqual(100000, message['launch_time_us'])
        self.assertEqual(
            [1.0, 0.0, 3.0, 0.0, 0.0, 0.0], message['launch_pose'])

    def test_launch_rejects_non_plain_nonfinite_and_out_of_bounds_physics(self):
        client = self.active_worker_client()
        default_speed = math.sqrt(11300.0)
        valid_source = source_shot(
            default_speed, 9.81, 720.0, True, 4.5)
        extra_source = wire_copy(valid_source)
        extra_source['shell']['unknown'] = 1
        tuple_source = wire_copy(valid_source)
        tuple_source['piercingPower'] = (220.0, 200.0)

        invalid = (
            {'origin': (1.0, 2.0, 3.0)},
            {'velocity': [float('nan'), 0.0, 0.0]},
            {'velocity': [0.0, 0.0, 0.0]},
            {'gravity': float('inf')},
            {'gravity': '9.81'},
            {'gravity': 0.0},
            {'max_time_ms': 20001},
            {'penetration_factor': 101.0},
            {'is_he': False, 'splash_radius': 1.0},
            {'source_shot': dict(valid_source, speed=700.0)},
            {'source_shot': dict(valid_source, gravity=10.0)},
            {'source_shot': extra_source},
            {'source_shot': tuple_source},
        )
        for values in invalid:
            with self.subTest(values=values):
                self.assertIsNone(self.launch(
                    client, shot_seq=1, authority_epoch=4,
                    fire_intent_seq=1, fire_input_seq=1, **values))
        self.assertEqual([], client._outbound_queue)

    def test_stock_b4_gravity_is_within_the_wire_contract(self):
        client = self.active_worker_client()

        self.assertEqual(1, self.launch(
            client, shot_seq=1, authority_epoch=4,
            fire_intent_seq=1, fire_input_seq=1,
            gravity=143.0, velocity=[0.0, 100.0, 425.0]))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual(143.0, message['gravity'])

    def test_launch_preserves_complete_he_factor_contract(self):
        client = self.active_worker_client()
        frozen = source_shot(
            math.sqrt(11300.0), 9.81, 720.0, True, 4.5)
        frozen['shell'].update({
            'explosionDamageFactor': 0.55,
            'explosionDamageAbsorptionFactor': 1.4,
            'explosionEdgeDamageFactor': 0.2,
        })

        self.assertEqual(1, self.launch(
            client, shot_seq=1, authority_epoch=4,
            fire_intent_seq=1, fire_input_seq=1,
            source_shot=frozen))

        shell = client._outbound_queue[-1][1]['source_shot']['shell']
        self.assertEqual(0.55, shell['explosionDamageFactor'])
        self.assertEqual(1.4, shell['explosionDamageAbsorptionFactor'])
        self.assertEqual(0.2, shell['explosionEdgeDamageFactor'])

    def test_large_finite_module_damage_survives_client_wires(self):
        amount = 500000000.0
        frozen = source_shot(
            math.sqrt(11300.0), 9.81, 720.0,
            damage=(390.0, amount))

        parsed = module._strict_projectile_source_shot(frozen)
        delta = module._strict_critical_delta({
            'devices': [{
                'name': 'ammoBayHealth', 'hp_loss': amount,
            }],
            'crew_ko': [], 'ignite': False,
        })

        self.assertEqual(amount, parsed['shell']['damage'][1])
        self.assertEqual(amount, delta['devices'][0]['hp_loss'])

    def test_send_fire_never_falls_back_to_instant_input(self):
        client = self.active_client()
        self.assertIsNone(client.send_fire(shell_index=1))
        self.assertEqual([], client._outbound_queue)

        self.assertTrue(self.send_player_input(client, shell_index=1))
        self.assertEqual(1, client.send_fire(
            shell_index=1, position=[1.0, 2.0, 3.0],
            velocity=[100.0, 0.0, 0.0], gravity=9.81,
            max_distance=500.0, max_time_ms=5000,
            source_shot=source_shot(100.0, 9.81, 500.0)))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual('fire_intent', message['type'])
        self.assertEqual({
            'type', 'round_id', 'intent_seq', 'input_seq', 'shell_index',
            'shot_origin', 'shot_direction', 'dispersion_angle',
        }, set(message))

    def test_progress_shape_is_exact_and_duplicate_ids_fail_closed(self):
        client = self.active_worker_client()
        cursor = {
            'projectile_id': 'player:7:1',
            'base_checked_ms': 100,
            'checked_through_ms': 150,
            'checked_distance': 52.5,
            'piercing_loss': 4.0,
            'penetration_factor': 0.8,
            'destructibles': [],
        }

        self.assertTrue(client.send_projectile_progress(4, [cursor]))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'cursors'}, set(message))
        self.assertEqual(cursor, message['cursors'][0])
        cursor['checked_distance'] = 999.0
        self.assertEqual(52.5, message['cursors'][0]['checked_distance'])

        self.assertFalse(client.send_projectile_progress(4, [
            dict(message['cursors'][0]), dict(message['cursors'][0])]))
        self.assertFalse(client.send_projectile_progress(
            4, [dict(message['cursors'][0]) for unused in range(31)]))
        bad = dict(message['cursors'][0])
        bad['unknown'] = 1
        self.assertFalse(client.send_projectile_progress(4, [bad]))
        bad = dict(message['cursors'][0])
        bad['checked_through_ms'] = '150'
        self.assertFalse(client.send_projectile_progress(4, [bad]))
        self.assertFalse(client.send_projectile_progress(3, [cursor]))

    @staticmethod
    def effect(kind, target_id, x=10.0, target_pose=None):
        effect = {
            'target_kind': kind,
            'target_id': target_id,
            'damage': 120,
            'shot_result': 1,
            'x': x,
            'y': 2.0,
            'z': 3.0,
        }
        if target_pose is not None:
            effect.update({
                'target_x': target_pose[0],
                'target_y': target_pose[1],
                'target_z': target_pose[2],
            })
        return effect

    def test_resolve_is_atomic_and_rejects_duplicate_targets(self):
        client = self.active_worker_client()
        direct = self.effect('player', 8)
        direct['damage_sticker'] = module.MAX_PROJECTILE_DAMAGE_STICKER
        splash = [self.effect(
            'bot', 17, 12.0, target_pose=(12.0, 2.0, 3.0))]

        self.assertTrue(client.send_projectile_resolve(
            4, 'player:7:1', 150, 'impact', 180,
            [11.0, 2.0, 3.0], direct, splash,
            checked_distance=61.0, piercing_loss=3.0,
            penetration_factor=0.75))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'projectile_id',
            'base_checked_ms', 'outcome', 'resolved_time_ms',
            'checked_distance', 'piercing_loss', 'penetration_factor',
            'hit_vehicle', 'impact', 'direct', 'splash', 'destructibles'},
            set(message))
        self.assertEqual('player:7:1', message['projectile_id'])
        self.assertTrue(message['hit_vehicle'])
        self.assertEqual(
            module.MAX_PROJECTILE_DAMAGE_STICKER,
            message['direct']['damage_sticker'])

        duplicate = self.effect('player', 8)
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], direct, [duplicate]))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0],
            self.effect('player', 8, target_pose=(1.0, 2.0, 3.0)), []))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], direct,
            [self.effect('bot', 17, 12.0)]))
        splash_with_sticker = self.effect(
            'bot', 17, 12.0, target_pose=(12.0, 2.0, 3.0))
        splash_with_sticker['damage_sticker'] = 1
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], direct, [splash_with_sticker]))
        for invalid in (True, 1.0, -1,
                        module.MAX_PROJECTILE_DAMAGE_STICKER + 1):
            with self.subTest(damage_sticker=invalid):
                self.assertFalse(client.send_projectile_resolve(
                    4, 'player:7:2', 0, 'impact', 10,
                    [0.0, 0.0, 0.0],
                    dict(direct, damage_sticker=invalid), []))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'miss', 10,
            [0.0, 0.0, 0.0], direct, []))

    def test_first_ricochet_wire_is_harmless_and_strict(self):
        client = self.active_worker_client()
        direct = self.effect('bot', 17, 11.0)
        direct['damage'] = 0
        direct['shot_result'] = 0
        direct['damage_sticker'] = 12345678901234567890

        self.assertTrue(client.send_projectile_ricochet(
            4, 'player:7:1', 150, 180,
            [11.0, 2.0, 3.0], [11.002, 2.0, 3.0],
            [-100.0, 20.0, 0.0], 0.75, direct,
            checked_distance=61.0, piercing_loss=3.0,
            penetration_factor=0.75))
        message = wire_copy(client._outbound_queue[-1][1])
        self.assertEqual({
            'type', 'round_id', 'authority_epoch', 'projectile_id',
            'base_checked_ms', 'resolved_time_ms', 'checked_distance',
            'piercing_loss', 'penetration_factor', 'impact',
            'segment_origin', 'segment_velocity',
            'base_penetration_multiplier', 'direct', 'destructibles'},
            set(message))
        self.assertEqual(
            12345678901234567890, message['direct']['damage_sticker'])

        damaging = dict(direct, damage=1)
        self.assertFalse(client.send_projectile_ricochet(
            4, 'player:7:2', 150, 180,
            [11.0, 2.0, 3.0], [11.002, 2.0, 3.0],
            [-100.0, 20.0, 0.0], 0.75, damaging))
        self.assertFalse(client.send_projectile_ricochet(
            4, 'player:7:2', 150, 180,
            [11.0, 2.0, 3.0], [11.2, 2.0, 3.0],
            [-100.0, 20.0, 0.0], 0.75, direct))
        critical = dict(direct, critical={'fire': True},
                        critical_target_base_revision=3,
                        critical_target_ack_seq=4, hull_damage=0,
                        critical_delta={
                            'devices': [], 'crew_ko': [], 'ignite': True})
        forbidden = (
            critical,
            dict(direct, stun_end_server_time_ms=500),
            dict(direct, target_x=11.0, target_y=2.0, target_z=3.0),
            dict(direct, damage_sticker=True),
            dict(direct, damage_sticker=-1),
            dict(direct, damage_sticker=
                 module.MAX_PROJECTILE_DAMAGE_STICKER + 1),
        )
        for proposal in forbidden:
            with self.subTest(proposal=proposal):
                self.assertFalse(client.send_projectile_ricochet(
                    4, 'player:7:2', 150, 180,
                    [11.0, 2.0, 3.0], [11.002, 2.0, 3.0],
                    [-100.0, 20.0, 0.0], 0.75, proposal))

    def test_optional_terminal_field_is_omitted_without_server_support(self):
        client = self.active_worker_client()
        client.server_capabilities = [
            module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY]

        self.assertTrue(client.send_projectile_resolve(
            4, 'player:7:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle=True))

        self.assertNotIn('hit_vehicle', client._outbound_queue[-1][1])

    def test_wreck_impact_is_a_strict_presentation_only_field(self):
        client = self.active_worker_client()
        wreck_hit = {'target_kind': 'bot', 'target_id': 17}

        self.assertTrue(client.send_projectile_resolve(
            4, 'player:7:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle=True,
            wreck_hit=wreck_hit))
        message = client._outbound_queue[-1][1]
        self.assertEqual(wreck_hit, message['wreck_hit'])
        self.assertIsNone(message['direct'])

        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], self.effect('bot', 17), [],
            hit_vehicle=True, wreck_hit=wreck_hit))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle=False,
            wreck_hit=wreck_hit))
        self.assertFalse(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle=True,
            wreck_hit={'target_kind': 'bot', 'target_id': True}))

        client.server_capabilities.remove(
            module.PROJECTILE_WRECK_HIT_CAPABILITY)
        self.assertTrue(client.send_projectile_resolve(
            4, 'player:7:2', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle=True,
            wreck_hit=wreck_hit))
        self.assertNotIn('wreck_hit', client._outbound_queue[-1][1])

    def test_random_map_requires_an_advertised_server_capability(self):
        client = self.active_client()
        client.phase = 'waiting'
        client.host_player_id = client.player_id
        client.map_pool = ['01_karelia']
        client.server_capabilities = [
            module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY]

        self.assertFalse(client.request_start(module.RANDOM_MAP_OPTION))
        client.server_capabilities.append(module.RANDOM_MAP_CAPABILITY)
        self.assertTrue(client.request_start(module.RANDOM_MAP_OPTION))

    def test_resolve_critical_contract_and_plain_impact_are_strict(self):
        client = self.active_worker_client()
        incomplete = self.effect('bot', 17)
        incomplete['critical'] = {'fire': True}
        self.assertFalse(client.send_projectile_resolve(
            4, 'bot:17:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], incomplete, []))

        complete = dict(incomplete)
        complete.update({
            'critical_target_base_revision': 3,
            'critical_target_ack_seq': 4,
            'hull_damage': 120,
            'critical_delta': {
                'devices': [], 'crew_ko': [], 'ignite': True},
        })
        self.assertTrue(client.send_projectile_resolve(
            4, 'bot:17:1', 0, 'impact', 10,
            [0.0, 0.0, 0.0], complete, []))
        self.assertFalse(client.send_projectile_resolve(
            4, 'bot:17:2', 0, 'expired', 10,
            (0.0, 0.0, 0.0), None, []))
        self.assertTrue(client.send_projectile_resolve(
            4, 'bot:17:2', 0, 'expired', 10,
            None, None, [], checked_distance=12.0))
        self.assertFalse(client.send_projectile_resolve(
            4, 'bot:17:3', 0, 'impact', 10,
            [0.0, 0.0, 0.0], None, [], hit_vehicle='yes'))

    def test_hello_advertises_ledger_before_transport_is_published(self):
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            effective_params=effective_params())
        fake = RecordingSocket()
        original_socket = module.socket.socket
        module.socket.socket = lambda *unused_args: fake
        client.running = True
        client._publish_connected_transport = (
            lambda unused_sock, unused_generation: False)
        try:
            client._worker(client._transport_generation)
        finally:
            module.socket.socket = original_socket

        hello = json.loads(fake.sent[0].decode('utf-8'))
        self.assertEqual('hello', hello['type'])
        self.assertEqual(
            list(module.CLIENT_CAPABILITIES), hello['capabilities'])

    @staticmethod
    def welcome(capabilities=None, authority_epoch=2,
                server_capabilities=None):
        return {
            'type': 'welcome',
            'protocol': module.PROTOCOL_VERSION,
            'client_build': module.CLIENT_BUILD,
            'capabilities': (list(module.CLIENT_CAPABILITIES)
                             if capabilities is None else capabilities),
            'server_capabilities': ([
                module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                module.RICOCHET_CONTINUATION_CAPABILITY,
                module.RAM_CONTACT_LEDGER_CAPABILITY,
                module.HUMAN_RAM_TIMELINE_CAPABILITY,
                module.PLAYER_FIRE_INTENT_CAPABILITY,
                module.PLAYER_ENVIRONMENT_CAPABILITY,
                module.EFFECTIVE_PARAMS_CAPABILITY,
                module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
                module.RANDOM_MAP_CAPABILITY,
            ] if server_capabilities is None else server_capabilities),
            'player_id': 7,
            'host_player_id': 7,
            'bot_authority_id': module.WORKER_AUTHORITY_ID,
            'authority_epoch': authority_epoch,
            'name': 'P',
            'vehicle': 'ussr:MS-1',
            'max_health': 100,
            'team': 1,
            'slot': 0,
            'map': '01_karelia',
            'phase': 'waiting',
            'round_id': 3,
            'state_revision': 1,
            'spawn': {'x': 0, 'y': 0, 'z': 0},
            'effective_params': effective_params(),
        }

    def test_welcome_requires_server_echoed_capability(self):
        self.assertEqual(
            'projectile_ledger_v2', module.PROJECTILE_LEDGER_CAPABILITY)
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome([]))
        self.assertFalse(client.ready)
        self.assertEqual(
            'required LAN capability mismatch', client.last_error)

        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome([
            'projectile_ledger_v1',
            module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY]))
        self.assertFalse(client.ready)
        self.assertEqual(
            'required LAN capability mismatch', client.last_error)

        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome([
            capability for capability in module.CLIENT_CAPABILITIES
            if capability != module.HUMAN_RAM_TIMELINE_CAPABILITY]))
        self.assertFalse(client.ready)
        self.assertEqual(
            'required LAN capability mismatch', client.last_error)

        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome(server_capabilities=[]))
        self.assertFalse(client.ready)
        self.assertEqual(
            'required LAN capability mismatch', client.last_error)

        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        self.assertTrue(client.ready)
        self.assertEqual(2, client.authority_epoch)
        self.assertTrue(client.has_projectile_ledger())

    def test_bot_burst_group_is_exact_for_every_physical_shell(self):
        client = self.active_worker_client()

        for index in range(3):
            self.assertEqual(index + 1, self.launch(
                client, shooter_kind='bot', shooter_id=11,
                shot_seq=index + 1, authority_epoch=4,
                burst_group_seq=1, burst_index=index, burst_count=3))
        self.assertEqual([1, 2, 3], [
            row[1]['shot_seq'] for row in client._outbound_queue])
        self.assertEqual([0, 1, 2], [
            row[1]['burst_index'] for row in client._outbound_queue])

    def test_bot_burst_rejects_partial_or_inconsistent_group(self):
        client = self.active_worker_client()
        invalid = (
            {'burst_group_seq': 1},
            {'burst_group_seq': 1, 'burst_index': 0},
            {'burst_group_seq': 1, 'burst_index': 1, 'burst_count': 3},
            {'burst_group_seq': 2, 'burst_index': 0, 'burst_count': 3},
            {'burst_group_seq': 1, 'burst_index': 3, 'burst_count': 3},
            {'burst_group_seq': 1, 'burst_index': True, 'burst_count': 3},
        )
        for values in invalid:
            with self.subTest(values=values):
                self.assertIsNone(self.launch(
                    client, shooter_kind='bot', shooter_id=11,
                    shot_seq=1, authority_epoch=4, **values))
        self.assertEqual([], client._outbound_queue)

    def test_waiting_room_accepts_idle_without_an_authority_id(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        welcome = self.welcome()
        welcome['bot_authority_id'] = None
        welcome['authority_status'] = 'idle'

        client._handle_message(welcome)

        self.assertTrue(client.ready)
        self.assertIsNone(client.bot_authority_id)
        self.assertIsNone(client.last_error)

        client._handle_message({
            'type': 'roster', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'state_revision': 2, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 7,
            'bot_authority_id': None, 'authority_status': 'idle',
            'authority_epoch': 2,
            'players': [{
                'id': 7, 'outfits': {},
                'effective_params': effective_params(),
                'equipment_states': [], 'equipment_revision': 0,
                'equipment_intent_seq': 0,
                'equipment_intent_result': {
                    'intent_seq': 0, 'accepted': False, 'reason': ''}}],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)

    @staticmethod
    def active_projectile(epoch=2):
        return {
            'projectile_id': 'player:7:1',
            'shooter_kind': 'player',
            'shooter_id': 7,
            'source_vehicle': 'ussr:R11_MS-1',
            'source_shot': source_shot(
                math.sqrt(10100.0), 9.81, 500.0),
            'shot_seq': 1,
            'burst_group_seq': 1,
            'burst_index': 0,
            'burst_count': 1,
            'fire_intent_seq': 4,
            'fire_input_seq': 8,
            'shell_index': 0,
            'team': 1,
            'origin': [0.0, 2.0, 0.0],
            'velocity': [100.0, 10.0, 0.0],
            'range_origin': [0.0, 0.0, 0.0],
            'segment_origin': [0.0, 2.0, 0.0],
            'segment_velocity': [100.0, 10.0, 0.0],
            'segment_start_time_ms': 0,
            'ricochet_count': 0,
            'base_penetration_multiplier': 1.0,
            'gravity': 9.81,
            'max_distance': 500.0,
            'max_time_ms': 5000,
            'is_he': False,
            'splash_radius': 0.0,
            'penetration_factor': 1.0,
            'launch_server_time_ms': 900,
            'checked_through_ms': 50,
            'checked_distance': 5.0,
            'piercing_loss': 0.0,
            'authority_epoch': epoch,
        }

    def test_snapshot_preserves_and_validates_ledger_then_failover_epoch(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        snapshot = {
            'type': 'snapshot',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 10,
            'bot_state_revision': 0,
            'bot_authority_id': module.WORKER_AUTHORITY_ID,
            'server_time_ms': 1000,
            'authority_epoch': 2,
            'projectile_revision': 1,
            'projectiles': [self.active_projectile()],
            'bot_manifest': [],
            'players': [{
                'id': 7,
                'input_seq': 0,
                'up_cosine': 1.0,
                'landing_observation_seq': 0,
                'critical_revision': 0,
                'critical_base_revision': 0,
                'critical_ack_seq': 0,
                'equipment_states': [],
                'equipment_revision': 0,
                'equipment_intent_seq': 0,
                'equipment_intent_result': {
                    'intent_seq': 0, 'accepted': False, 'reason': ''},
            }],
            'bots': [],
        }
        client._handle_message(snapshot)

        self.assertEqual(snapshot['projectiles'],
                         client.last_snapshot['projectiles'])
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual('player:7:1',
                         client.last_snapshot['projectiles'][0]['projectile_id'])
        client._handle_message({
            'type': 'events',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 11,
            'server_time_ms': 1010,
            'authority_epoch': 3,
            'events': [{
                'kind': 'authority',
                'player_id': module.WORKER_AUTHORITY_ID,
                'authority_epoch': 3,
            }],
        })
        self.assertEqual(3, client.authority_epoch)
        self.assertEqual(1010, client.server_time_ms)

    def test_snapshot_recovers_equipment_intent_sequence_for_reconnect(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        client._handle_message({
            'type': 'snapshot', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 10,
            'bot_state_revision': 0,
            'bot_authority_id': module.WORKER_AUTHORITY_ID,
            'server_time_ms': 1000, 'authority_epoch': 2,
            'projectile_revision': 0, 'projectiles': [],
            'bot_manifest': [], 'bots': [],
            'players': [{
                'id': 7,
                'input_seq': 0, 'up_cosine': 1.0,
                'landing_observation_seq': 0,
                'critical_revision': 0, 'critical_base_revision': 0,
                'critical_ack_seq': 0,
                'equipment_states': [], 'equipment_revision': 4,
                'equipment_intent_seq': 5,
                'equipment_intent_result': {
                    'intent_seq': 5, 'accepted': False,
                    'reason': 'equipment_ineligible'},
            }],
        })

        self.assertTrue(client.running)
        self.assertEqual(5, client._equipment_intent_seq)
        self.assertEqual(
            'equipment_ineligible',
            client.last_snapshot['players'][0][
                'equipment_intent_result']['reason'])

    def test_player_shot_event_preserves_fire_intent_identity(self):
        received = []
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            on_event=lambda kind, message: received.append((kind, message)))
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        client._handle_message({
            'type': 'events',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 11,
            'server_time_ms': 1001,
            'authority_epoch': 2,
            'events': [{
                'kind': 'shot',
                'projectile_id': 'player:7:1',
                'shooter_kind': 'player',
                'shooter_id': 7,
                'shot_seq': 1,
                'fire_intent_seq': 4,
                'fire_input_seq': 8,
            }],
        })

        self.assertTrue(client.running)
        event = received[-1][1]['events'][0]
        self.assertEqual(4, event['fire_intent_seq'])
        self.assertEqual(8, event['fire_input_seq'])

    def test_events_require_monotonic_time_and_epoch_envelope(self):
        def client_at(time_ms=1000, epoch=2):
            client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client._handle_message(self.welcome(authority_epoch=epoch))
            client.phase = 'battle'
            client.server_time_ms = time_ms
            return client

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'authority_epoch': 2, 'events': [],
        })
        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(2, client.authority_epoch)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'events': [],
        })
        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(2, client.authority_epoch)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 1, 'events': [],
        })
        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(2, client.authority_epoch)

        client = client_at()
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 3, 'events': [],
        })
        self.assertTrue(client.running)
        self.assertEqual(1001, client.server_time_ms)
        self.assertEqual(3, client.authority_epoch)

    def test_authority_events_must_not_advance_past_envelope_epoch(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome(authority_epoch=2))
        client.phase = 'battle'
        client.server_time_ms = 1000

        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': 1001, 'authority_epoch': 3,
            'events': [{
                'kind': 'authority',
                'player_id': module.WORKER_AUTHORITY_ID,
                'authority_epoch': 4,
            }],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(2, client.authority_epoch)

    def test_invalid_snapshot_ledger_keeps_last_good_state(self):
        invalid_projectiles = []
        bad_origin = self.active_projectile()
        bad_origin['origin'] = (0.0, 2.0, 0.0)
        invalid_projectiles.append(bad_origin)
        missing_intent = self.active_projectile()
        del missing_intent['fire_intent_seq']
        invalid_projectiles.append(missing_intent)
        missing_input = self.active_projectile()
        del missing_input['fire_input_seq']
        invalid_projectiles.append(missing_input)

        for projectile in invalid_projectiles:
            with self.subTest(fields=set(projectile)):
                client = LANClient(
                    '127.0.0.1', 28782, 'P', 'ussr:MS-1')
                client.running = True
                client._handle_message(self.welcome())
                client.phase = 'battle'
                client._handle_message({
                    'type': 'snapshot',
                    'protocol': module.PROTOCOL_VERSION,
                    'round_id': 3,
                    'server_tick': 10,
                    'bot_state_revision': 0,
                    'bot_authority_id': module.WORKER_AUTHORITY_ID,
                    'server_time_ms': 1000,
                    'authority_epoch': 2,
                    'projectile_revision': 1,
                    'projectiles': [projectile],
                    'bot_manifest': [],
                    'players': [],
                    'bots': [],
                })
                self.assertTrue(client.running)
                self.assertIsNone(client.last_error)
                self.assertIsNone(client.last_snapshot)

    def test_regressing_wire_time_is_clamped_without_dropping_events(self):
        received = []
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            on_event=lambda kind, message: received.append((kind, message)))
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        client.server_time_ms = 1000
        client._handle_message({
            'type': 'events',
            'protocol': module.PROTOCOL_VERSION,
            'round_id': 3,
            'server_tick': 11,
            'server_time_ms': 999,
            'authority_epoch': 2,
            'events': [],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)
        self.assertEqual(1000, received[-1][1]['server_time_ms'])

    def test_malformed_runtime_server_time_drops_only_that_message(self):
        client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        client.running = True
        client._handle_message(self.welcome())
        client.phase = 'battle'
        client.server_time_ms = 1000
        client._handle_message({
            'type': 'events', 'protocol': module.PROTOCOL_VERSION,
            'round_id': 3, 'server_tick': 11,
            'server_time_ms': -1, 'authority_epoch': 2, 'events': [],
        })

        self.assertTrue(client.running)
        self.assertIsNone(client.last_error)
        self.assertEqual(1000, client.server_time_ms)


if __name__ == '__main__':
    unittest.main()
