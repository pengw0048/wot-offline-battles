import math
from pathlib import Path
import random
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.entities.native_remote_vehicle import \
    NativeRemoteVehicleFactory
from gui.mods.offline_lan_0922.projectile_manager import InFlightProjectiles
from gui.mods.offline_lan_0922 import combat_rules, critical_damage


class _Vector(object):
    def __init__(self, value=(0.0, 0.0, 0.0), y=None, z=None):
        if y is not None and z is not None:
            value = (value, y, z)
        try:
            value = (value.x, value.y, value.z)
        except AttributeError:
            pass
        self.x, self.y, self.z = map(float, value)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector((self.x + other.x, self.y + other.y,
                        self.z + other.z))

    def __sub__(self, other):
        return _Vector((self.x - other.x, self.y - other.y,
                        self.z - other.z))

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def scale(self, value):
        return _Vector((self.x * value, self.y * value, self.z * value))

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


class _BigWorld(object):
    def __init__(self):
        self.now = 0.0
        self.wall_x = None

    def time(self):
        return self.now

    def wg_collideSegment(self, unused_space, start, end, unused_mask):
        if self.wall_x is None:
            return None
        low = min(start.x, end.x)
        high = max(start.x, end.x)
        if not low <= self.wall_x <= high or abs(end.x - start.x) < 1e-9:
            return None
        fraction = (self.wall_x - start.x) / (end.x - start.x)
        return (_Vector((
            self.wall_x,
            start.y + (end.y - start.y) * fraction,
            start.z + (end.z - start.z) * fraction)), None)


class _Client(object):
    def __init__(self):
        self.authority_epoch = 1
        self.server_time_ms = 0
        self.resolutions = []
        self.ricochets = []
        self.progress = []
        self.launches = []

    def is_bot_authority(self):
        return True

    def send_projectile_resolve(self, *args, **kwargs):
        self.resolutions.append((args, kwargs))
        return True

    def send_projectile_ricochet(self, *args, **kwargs):
        self.ricochets.append((args, kwargs))
        return True

    def send_projectile_progress(self, epoch, cursors):
        self.progress.append((epoch, cursors))
        return True

    def send_projectile_launch(self, *args, **kwargs):
        self.launches.append((args, kwargs))
        return int(args[2])


def _battle(now=0.0):
    bigworld = _BigWorld()
    bigworld.now = now
    runtime = types.SimpleNamespace(
        bigworld=bigworld,
        math=types.SimpleNamespace(Vector3=_Vector))
    battle = BattleRuntime(runtime)
    battle.client = _Client()
    battle._avatar = types.SimpleNamespace(spaceID=1)
    battle._projectiles = InFlightProjectiles(initial_time=now)
    battle._projectile_meta = {}
    battle._projectile_visual_meta = {}
    battle._projectile_terminal_data = {}
    battle._projectile_target_positions = {}
    battle._projectile_position_history = []
    battle._projectile_server_time_ms = 0
    battle._projectile_server_local_time = now
    battle._projectile_epoch = 1
    battle._next_projectile_progress_time = now
    shot = types.SimpleNamespace(
        shell=types.SimpleNamespace(
            kind='ARMOR_PIERCING', caliber=75.0,
            damage=(135.0, 100.0), explosionRadius=0.0,
            effectsIndex=0),
        maxDistance=100.0)
    descriptor = types.SimpleNamespace(
        gun=types.SimpleNamespace(shots=[shot]))
    source = types.SimpleNamespace(
        id=41, isStarted=True, typeDescriptor=descriptor,
        position=_Vector((0.0, 0.0, 0.0)), isAlive=lambda: True)
    battle._records = {
        'player:7': {
            'engine_id': 41, 'network_id': 7, 'kind': 'player',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}}
    battle._server_entity = lambda entity_id: source if entity_id == 41 else None
    return battle, bigworld


def _event():
    return {
        'kind': 'shot', 'attacker': 7,
        'projectile_id': 'player:7:1',
        'shooter_kind': 'player', 'shooter_id': 7,
        'source_vehicle': 'ussr:R11_MS-1',
        'source_shot': {
            'speed': 10.0, 'gravity': 0.000001,
            'maxDistance': 100.0, 'piercingPower': [220.0, 200.0],
            'deadeye': False,
            'shell': {
                'kind': 'ARMOR_PIERCING', 'caliber': 105.0,
                'damage': [390.0, 150.0], 'explosionRadius': 0.0,
            },
        },
        'shot_seq': 1,
        'burst_group_seq': 1, 'burst_index': 0, 'burst_count': 1,
        'shell_index': 0,
        'fire_intent_seq': 1, 'fire_input_seq': 1,
        'origin': [0.0, 1.0, 0.0],
        'velocity': [10.0, 0.0, 0.0],
        'range_origin': [0.0, 0.0, 0.0],
        'segment_origin': [0.0, 1.0, 0.0],
        'segment_velocity': [10.0, 0.0, 0.0],
        'segment_start_time_ms': 0,
        'ricochet_count': 0,
        'base_penetration_multiplier': 1.0,
        'gravity': 0.000001, 'maxDistance': 100.0,
        'max_time_ms': 20000, 'is_he': False,
        'splash_radius': 0.0, 'penetration_factor': 1.0,
        'launch_server_time_ms': 0, 'authority_epoch': 1,
    }


class BattleProjectileTests(unittest.TestCase):
    def test_native_factory_exposes_unblended_projectile_matrix(self):
        canonical_matrix = object()
        rendered_provider = object()
        factory = object.__new__(NativeRemoteVehicleFactory)
        factory._states = {
            42: types.SimpleNamespace(
                entity=object(), matrix=canonical_matrix,
                provider=rendered_provider),
        }

        result = factory.projectile_collision_matrix(42)

        self.assertIs(canonical_matrix, result)
        self.assertIsNot(rendered_provider, result)

    def test_vehicle_trace_counts_spaced_layer_against_ten_calibre_budget(self):
        collisions = [
            types.SimpleNamespace(dist=0.20),
            types.SimpleNamespace(dist=1.19),
            types.SimpleNamespace(dist=1.21),
        ]
        shot = {'shell': {'caliber': 100.0}}

        limited, trace_start, trace_end = BattleRuntime._vehicle_trace(
            shot, _Vector(), _Vector((0.0, 0.0, 2.0)), collisions)

        self.assertIs(trace_start.__class__, _Vector)
        self.assertEqual(collisions[:2], list(limited))
        self.assertAlmostEqual(1.20, trace_end.z)

    def test_vehicle_trace_keeps_hit_at_point_nine_nine_not_one_point_zero_one(self):
        collisions = [
            types.SimpleNamespace(dist=5.00),
            types.SimpleNamespace(dist=5.99),
            types.SimpleNamespace(dist=6.01),
        ]

        limited, unused_start, trace_end = BattleRuntime._vehicle_trace(
            {'shell': {'caliber': 100.0}}, _Vector(),
            _Vector((10.0, 0.0, 0.0)), collisions)

        self.assertEqual(collisions[:2], list(limited))
        self.assertAlmostEqual(6.0, trace_end.x)

    def test_vehicle_trace_extends_a_full_calibre_past_a_late_first_hit(self):
        collisions = [
            types.SimpleNamespace(dist=9.80),
            types.SimpleNamespace(dist=10.79),
            types.SimpleNamespace(dist=10.81),
        ]

        limited, unused_start, trace_end = BattleRuntime._vehicle_trace(
            {'shell': {'caliber': 100.0}}, _Vector(),
            _Vector((10.0, 0.0, 0.0)), collisions)

        self.assertEqual(collisions[:2], list(limited))
        self.assertAlmostEqual(10.8, trace_end.x)

    def test_projectile_takeover_cannot_lower_the_penetration_roll(self):
        battle, unused_bigworld = _battle()
        normalized = battle._projectile_wire_meta(_event())
        battle._install_projectile_meta(normalized)
        lowered = dict(normalized, penetration_factor=0.999999)

        with self.assertRaisesRegex(
                RuntimeError, 'canonical projectile launch changed'):
            battle._install_projectile_meta(lowered)

    def test_bot_authority_change_invalidates_artillery_proofs_once(self):
        battle, unused_bigworld = _battle()

        class _Bots(object):
            def __init__(self):
                self.authority_id = 1

            def battle_start(self, start):
                self.authority_id = start.get('bot_authority_id')
                return []

            def is_authority(self):
                return False

        battle._bots = _Bots()
        battle._artillery = types.SimpleNamespace(reset=mock.Mock())
        battle._start_message = {
            'round_id': 1, 'bot_authority_id': 1, 'bot_manifest': []}
        battle._last_snapshot = {'bots': []}

        self.assertFalse(battle._reconcile_bot_authority(1))
        battle._artillery.reset.assert_not_called()
        self.assertTrue(battle._reconcile_bot_authority(2))
        battle._artillery.reset.assert_called_once_with()
        self.assertFalse(battle._reconcile_bot_authority(2))
        battle._artillery.reset.assert_called_once_with()

    def test_artillery_final_probe_uses_exact_native_muzzle(self):
        battle, unused_bigworld = _battle()
        battle._bot_barrel_point = mock.Mock(
            return_value=(3.0, 4.0, 5.0))
        battle._barrel_under_water = mock.Mock(return_value=False)
        muzzle = _Vector((3.0, 4.0, 5.0))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=object(),
            model=types.SimpleNamespace(node=lambda unused: types.SimpleNamespace(
                translation=muzzle)))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)
        battle._runtime.math.Matrix = lambda node: node
        receipt = {'proof_key': ('launch',)}
        battle._artillery = types.SimpleNamespace(
            request_launch=mock.Mock(return_value=(True, receipt)))

        result = battle._bot_artillery_launch(
            {'id': 11}, {'kind': 'player', 'network_id': 7}, object(),
            0, 4, 0.25, 0.15, 2.0, 10.0)

        self.assertIs(receipt, result)
        args = battle._artillery.request_launch.call_args[0]
        self.assertEqual((3.0, 4.0, 5.0), args[5])
        self.assertEqual((4, 0.25, 0.15, 2.0, 10.0), args[4:5] + args[6:])

    def test_artillery_cancel_discards_the_controller_launch_slot(self):
        battle, unused_bigworld = _battle()
        battle._artillery = types.SimpleNamespace(
            cancel_launch=mock.Mock(return_value=True))
        source = {'id': 11, 'x': 1.0, 'y': 2.0, 'z': 3.0}

        self.assertTrue(battle._bot_artillery_cancel(source))
        battle._artillery.cancel_launch.assert_called_once_with(source)

    def test_bot_launch_without_frozen_muzzle_fails_closed(self):
        battle, unused_bigworld = _battle()
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(
                kind='ARMOR_PIERCING', caliber=105.0,
                damage=(390.0, 150.0), explosionRadius=0.0),
            speed=100.0, gravity=9.81, maxDistance=500.0,
            piercingPower=(220.0, 200.0))
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=descriptor,
            position=_Vector((4.0, 5.0, 6.0)),
            model=types.SimpleNamespace(node=mock.Mock(
                side_effect=RuntimeError('native muzzle unavailable'))))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)

        self.assertFalse(battle._launch_bot_projectile({
            'id': 11, 'profile': {'class_tag': 'MT'},
            'shot_yaw': 0.0, 'shot_pitch': 0.0, 'shell_index': 0,
        }, 1))
        self.assertEqual([], battle.client.launches)

    def test_direct_launch_reuses_muzzle_frozen_before_pose_update(self):
        battle, unused_bigworld = _battle()
        battle._bot_barrel_point = mock.Mock(
            return_value=(4.0, 5.0, 6.0))
        battle._barrel_under_water = mock.Mock(return_value=False)
        speed = 100.0
        muzzle = [_Vector((4.0, 5.0, 6.0))]
        moved_origin = _Vector((40.0, 50.0, 60.0))
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(
                kind='ARMOR_PIERCING', caliber=105.0,
                damage=(390.0, 150.0), explosionRadius=0.0),
            speed=speed, gravity=9.81, maxDistance=500.0,
            piercingPower=(220.0, 200.0))
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=descriptor,
            model=types.SimpleNamespace(node=mock.Mock(
                side_effect=lambda unused: types.SimpleNamespace(
                    translation=muzzle[0]))))
        battle._records['bot:11'] = {
            'engine_id': 77, 'kind': 'bot', 'network_id': 11,
            'ready': True, 'local': False,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)
        battle._runtime.math.Matrix = lambda node: node
        frozen_origin = battle._bot_direct_launch_origin(
            {'id': 11}, descriptor, 0, 1, 0.0, 0.0, 0.5)
        launch = {
            'fire_seq': 1, 'shell_index': 0,
            'shot_yaw': 0.0, 'shot_pitch': 0.0,
            'flight_time': 0.5, 'origin': frozen_origin,
        }
        self.assertTrue(battle._bot_friendly_firing_lane(
            {'id': 11, 'team': 2, 'fire_seq': 0}, {}, descriptor, 0,
            launch)['clear'])

        muzzle[0] = moved_origin
        source.model.node.reset_mock()
        state = {
            'id': 11, 'profile': {'class_tag': 'MT'},
            'shot_yaw': 0.0, 'shot_pitch': 0.0, 'shell_index': 0,
            'shot_origin': frozen_origin,
            'launch_time_us': 1000000,
            'launch_pose': (1.0, 2.0, 3.0, 0.0, 0.0, 0.0),
        }

        self.assertTrue(battle._launch_bot_projectile(state, 1))
        args, kwargs = battle.client.launches[-1]
        self.assertEqual(list(frozen_origin), args[4])
        self.assertEqual([0.0, 0.0, speed], args[5])
        self.assertEqual(
            [390.0, 150.0], kwargs['source_shot']['shell']['damage'])
        source.model.node.assert_not_called()

    def test_spg_launch_reuses_the_proved_origin_and_velocity(self):
        battle, unused_bigworld = _battle()
        speed = 10.0
        yaw = 0.25
        pitch = 0.15
        horizontal = math.cos(pitch)
        origin = (3.0, 4.0, 5.0)
        velocity = (
            math.sin(yaw) * horizontal * speed,
            math.sin(pitch) * speed,
            math.cos(yaw) * horizontal * speed)
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(
                kind='ARMOR_PIERCING', caliber=105.0,
                damage=(390.0, 150.0), explosionRadius=0.0),
            speed=speed, gravity=9.81, maxDistance=100.0,
            piercingPower=(220.0, 200.0))
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=descriptor,
            model=types.SimpleNamespace(node=mock.Mock(
                side_effect=AssertionError('SPG launch re-sampled muzzle'))))
        battle._records['bot:11'] = {
            'engine_id': 77, 'network_id': 11, 'kind': 'bot'}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)
        proof = (
            'launch', 11, 'player', 7, 0, 4, origin,
            yaw, pitch, speed, 9.81, 100.0, 2.0)
        state = {
            'id': 11, 'profile': {'class_tag': 'SPG'},
            'fire_seq': 4, 'shell_index': 0,
            'shot_yaw': yaw, 'shot_pitch': pitch,
            'shot_origin': origin, 'shot_velocity': velocity,
            'shot_gravity': 9.81, 'shot_max_distance': 100.0,
            'shot_max_time_ms': 20000, 'shot_proof_key': proof,
            'launch_time_us': 1000000,
            'launch_pose': (1.0, 2.0, 3.0, 0.0, 0.0, 0.0),
        }

        self.assertTrue(battle._launch_bot_projectile(state, 4))
        args, kwargs = battle.client.launches[-1]
        self.assertEqual(list(origin), args[4])
        self.assertEqual(list(velocity), args[5])
        self.assertEqual(20000, args[8])
        self.assertEqual(10.0, kwargs['source_shot']['speed'])
        first_kwargs = dict(kwargs)
        source.model.node.assert_not_called()

        state['shot_velocity'] = (velocity[0] + 0.01,) + velocity[1:]
        battle.client.launches = []
        self.assertTrue(battle._launch_bot_projectile(state, 4))
        retry_args, retry_kwargs = battle.client.launches[-1]
        self.assertEqual(list(origin), retry_args[4])
        self.assertEqual(list(velocity), retry_args[5])
        self.assertEqual(first_kwargs, retry_kwargs)

    def test_spg_without_final_path_receipt_never_launches(self):
        battle, unused_bigworld = _battle()
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(
                kind='ARMOR_PIERCING', caliber=105.0,
                damage=(390.0, 150.0), explosionRadius=0.0),
            speed=10.0, gravity=9.81, maxDistance=100.0,
            piercingPower=(220.0, 200.0))
        source = types.SimpleNamespace(
            isStarted=True,
            typeDescriptor=types.SimpleNamespace(
                gun=types.SimpleNamespace(shots=[shot])))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)

        self.assertFalse(battle._launch_bot_projectile({
            'id': 11, 'profile': {'class_tag': 'SPG'},
            'shot_yaw': 0.0, 'shot_pitch': 0.1, 'shell_index': 0,
        }, 1))
        self.assertEqual([], battle.client.launches)

    def test_receive_timestamp_preserves_launch_age_across_main_thread_stall(self):
        battle, bigworld = _battle(now=11.0)
        with mock.patch.object(
                sys.modules[BattleRuntime.__module__].time,
                'monotonic', return_value=101.0):
            self.assertTrue(battle._observe_projectile_message({
                'server_time_ms': 10000,
                'authority_epoch': 1,
                '_client_received_time': 100.0,
            }))

        self.assertEqual(10.0, battle._projectile_server_local_time)
        self.assertEqual(11000, battle._projectile_estimated_server_time(11.0))

    def test_delayed_tracer_starts_at_server_confirmed_cursor(self):
        battle, bigworld = _battle(now=1.0)
        source = battle._server_entity(41)
        source.showShooting = mock.Mock(return_value=True)
        battle._remote_factory = types.SimpleNamespace(
            play_projectile_tracer=mock.Mock(return_value=1000000))
        event = dict(
            _event(), maxDistance=1000.0, max_time_ms=20000,
            velocity=[100.0, 20.0, 0.0], gravity=10.0,
            launch_server_time_ms=0)
        battle._projectile_server_time_ms = 1000
        battle._projectile_server_local_time = 1.0

        self.assertTrue(battle._show_shot(event))

        arguments = battle._remote_factory.play_projectile_tracer.call_args[0]
        self.assertEqual('player:7:1', arguments[7])
        self.assertEqual((0.0, 1.0, 0.0), arguments[8])
        self.assertEqual((100.0, 20.0, 0.0), arguments[9])
        source.showShooting.assert_called_once_with(1, False)

    def test_snapshot_tracer_catches_up_only_to_confirmed_cursor(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_server_time_ms = 1000
        battle._projectile_server_local_time = 1.0
        battle._remote_factory = types.SimpleNamespace(
            admit_projectile_visual=mock.Mock(return_value=True),
            play_projectile_tracer=mock.Mock(return_value=1000000))
        source_shot = dict(_event()['source_shot'])
        source_shot.update({
            'speed': math.sqrt(10400.0),
            'gravity': 10.0,
            'maxDistance': 1000.0,
        })
        event = dict(
            _event(), maxDistance=1000.0, max_time_ms=20000,
            source_shot=source_shot,
            velocity=[100.0, 20.0, 0.0],
            segment_velocity=[100.0, 20.0, 0.0], gravity=10.0,
            launch_server_time_ms=0, checked_through_ms=400,
            checked_distance=40.0, piercing_loss=0.0)
        normalized = battle._projectile_wire_meta(event)
        normalized['source_descriptor'] = battle._server_entity(
            41).typeDescriptor

        self.assertTrue(battle._ensure_projectile_visual(normalized, 1.0))

        arguments = battle._remote_factory.play_projectile_tracer.call_args[0]
        self.assertEqual((40.0, 8.2, 0.0), arguments[8])
        self.assertEqual((100.0, 16.0, 0.0), arguments[9])

    def test_denied_cosmetic_still_installs_authoritative_projectile(self):
        battle, unused_bigworld = _battle(now=1.0)
        source = battle._server_entity(41)
        source.showShooting = mock.Mock(return_value=True)
        battle._remote_factory = types.SimpleNamespace(
            admit_projectile_visual=mock.Mock(return_value=False),
            play_projectile_tracer=mock.Mock(return_value=1000000))

        self.assertTrue(battle._show_shot(_event()))

        self.assertIn('player:7:1', battle._projectile_meta)
        self.assertFalse(
            battle._projectile_visual_meta['player:7:1']['admitted'])
        source.showShooting.assert_not_called()
        battle._remote_factory.play_projectile_tracer.assert_not_called()

    def test_native_muzzle_exception_is_cosmetic_and_disables_retries(self):
        battle, unused_bigworld = _battle(now=1.0)
        source = battle._server_entity(41)
        source.showShooting = mock.Mock(
            side_effect=RuntimeError('native muzzle failed'))
        battle._remote_factory = types.SimpleNamespace(
            admit_projectile_visual=mock.Mock(return_value=True),
            play_projectile_tracer=mock.Mock(return_value=1000000))

        self.assertTrue(battle._show_shot(_event()))
        second = dict(_event(), projectile_id='player:7:2', shot_seq=2)
        self.assertTrue(battle._show_shot(second))

        self.assertEqual(1, source.showShooting.call_count)
        self.assertEqual(
            2, battle._remote_factory.play_projectile_tracer.call_count)
        self.assertIn(
            'shot muzzle presentation', battle._disabled_optional_features)

    def test_terminal_event_hides_visual_at_authoritative_impact_once(self):
        battle, unused_bigworld = _battle()
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        self.assertTrue(battle._accept_projectile_event(_event()))
        event = {
            'kind': 'projectile_impact',
            'projectile_id': 'player:7:1',
            'outcome': 'impact', 'resolved_time_ms': 500,
            'impact': [5.0, 0.875, 0.0],
        }

        self.assertTrue(battle._apply_projectile_terminal_event(event))

        # A terminal whose ground/vehicle verdict is not ours carries no
        # explosion, so the armour-hit effect is never doubled up.
        battle._remote_factory.stop_projectile_tracer.assert_called_once_with(
            'player:7:1', [5.0, 0.875, 0.0], explosion=None)

    def test_native_terminal_exception_does_not_block_authority_cleanup(self):
        battle, unused_bigworld = _battle()
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(
                side_effect=RuntimeError('native retire failed')))
        self.assertTrue(battle._accept_projectile_event(_event()))

        self.assertTrue(battle._apply_projectile_terminal_event({
            'kind': 'projectile_impact',
            'projectile_id': 'player:7:1',
            'outcome': 'impact', 'resolved_time_ms': 500,
            'impact': [5.0, 0.875, 0.0],
        }))

        self.assertNotIn('player:7:1', battle._projectile_meta)
        self.assertNotIn('player:7:1', battle._projectile_visual_meta)
        self.assertFalse(battle._projectiles.contains('player:7:1'))

    def test_non_authority_snapshot_keeps_metadata_for_ground_terminal(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle.client.is_bot_authority = lambda: False
        battle._remote_factory = types.SimpleNamespace(
            play_projectile_tracer=mock.Mock(return_value=True),
            stop_projectile_tracer=mock.Mock(return_value=True))
        snapshot_row = dict(_event())
        snapshot_row['max_distance'] = snapshot_row.pop('maxDistance')
        for name in ('kind', 'attacker', 'authority_epoch'):
            snapshot_row.pop(name, None)
        snapshot = {'projectiles': [snapshot_row]}

        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        battle._projectile_explosion = mock.Mock(return_value='ground-fx')
        self.assertTrue(battle._apply_projectile_terminal_event({
            'kind': 'projectile_impact',
            'projectile_id': 'player:7:1', 'outcome': 'impact',
            'resolved_time_ms': 500, 'impact': [5.0, 0.875, 0.0],
            'hit_vehicle': False,
        }))

        battle._projectile_explosion.assert_called_once_with(
            'player:7:1', [5.0, 0.875, 0.0])
        battle._remote_factory.stop_projectile_tracer.assert_called_once_with(
            'player:7:1', [5.0, 0.875, 0.0],
            explosion='ground-fx')

    def test_a_world_terminal_carries_the_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        battle._projectile_meta['p1'] = {
            'hit_vehicle': False, 'terminal_velocity': (0.0, -100.0, 10.0),
            'shooter_kind': 'player', 'shooter_id': 7, 'shell_index': 0}
        battle._projectile_shot = lambda meta: {
            'shell': {'effectsIndex': 3}}
        battle._surface_effect_material = lambda impact: 'ground'
        battle._runtime.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(shotEffects=[
                {}, {}, {}, {'groundHit': ('kp', 'fx', set())}]))

        bundle = battle._projectile_explosion('p1', (1.0, 2.0, 3.0))

        self.assertIsNotNone(bundle)
        effects_descr, material, velocity = bundle
        self.assertEqual({'groundHit': ('kp', 'fx', set())}, effects_descr)
        self.assertEqual('ground', material)
        # __addExplosionEffect places the effect at position +/- velocityDir,
        # so a raw muzzle velocity would stretch it over a kilometre.
        self.assertAlmostEqual(1.0, velocity.length)
        self.assertAlmostEqual(-100.0 / math.sqrt(100.0 ** 2 + 10.0 ** 2),
                               velocity[1])

    def test_denied_projectile_has_no_ground_explosion_work(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {
            'hit_vehicle': False, 'terminal_velocity': (0.0, -100.0, 10.0),
            'shooter_kind': 'player', 'shooter_id': 7, 'shell_index': 0}
        battle._projectile_visual_meta['p1'] = {'admitted': False}

        self.assertIsNone(
            battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_a_vehicle_terminal_carries_no_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {'hit_vehicle': True}

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_a_visible_wreck_terminal_plays_only_the_armour_hit(self):
        battle, unused_bigworld = _battle(now=1.0)
        add_effect = mock.Mock()
        battle._avatar.terrainEffects = types.SimpleNamespace(
            addNew=add_effect)
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        target = types.SimpleNamespace(
            isStarted=True, isAlive=lambda: False,
            position=_Vector((5.0, 0.0, 0.0)))
        source_entity = battle._server_entity
        battle._server_entity = lambda entity_id: (
            target if entity_id == 55 else source_entity(entity_id))
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'spot_visible': False,
            'state': {'health': 0, 'alive': False}}
        battle._projectile_meta['p1'] = {
            'shooter_kind': 'player', 'shooter_id': 7,
            'shell_index': 0, 'terminal_velocity': (10.0, 0.0, 0.0)}
        battle._projectile_shot = lambda unused_meta: {
            'shell': {'effectsIndex': 0}}
        battle._runtime.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(shotEffects=[{
                'armorHit': ('wreckStages', 'wreckFx', None)}]))
        event = {
            'kind': 'projectile_impact', 'projectile_id': 'p1',
            'outcome': 'impact', 'resolved_time_ms': 500,
            'impact': [5.0, 1.0, 0.0], 'hit_vehicle': True,
            'wreck_hit': {'target_kind': 'bot', 'target_id': 17},
        }

        self.assertFalse(battle._present_projectile_wreck_hit('p1', event))
        add_effect.assert_not_called()
        battle._records['bot:17']['spot_visible'] = True
        self.assertTrue(battle._apply_projectile_terminal_event(event))

        effect = add_effect.call_args
        self.assertEqual(('wreckFx', 'wreckStages'),
                         (effect.args[1], effect.args[2]))
        battle._remote_factory.stop_projectile_tracer.assert_called_once_with(
            'p1', [5.0, 1.0, 0.0], explosion=None)

    def test_a_blind_live_vehicle_terminal_has_no_impact_effect(self):
        battle, unused_bigworld = _battle(now=1.0)
        add_effect = mock.Mock()
        battle._avatar.terrainEffects = types.SimpleNamespace(
            addNew=add_effect)
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        battle._projectile_meta['p1'] = {
            'shooter_kind': 'player', 'shooter_id': 7,
            'shell_index': 0, 'terminal_velocity': (10.0, 0.0, 0.0)}

        self.assertTrue(battle._apply_projectile_terminal_event({
            'kind': 'projectile_impact', 'projectile_id': 'p1',
            'outcome': 'impact', 'resolved_time_ms': 500,
            'impact': [5.0, 1.0, 0.0], 'hit_vehicle': True,
        }))

        add_effect.assert_not_called()
        battle._remote_factory.stop_projectile_tracer.assert_called_once_with(
            'p1', [5.0, 1.0, 0.0], explosion=None)

    def test_an_unknown_verdict_carries_no_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {}

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_an_unresolvable_surface_carries_no_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {
            'hit_vehicle': False, 'terminal_velocity': (0.0, -1.0, 0.0)}
        battle._projectile_shot = lambda meta: {'shell': {'effectsIndex': 0}}
        battle._runtime.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(shotEffects=[{'groundHit': ()}]))
        battle._surface_effect_material = lambda impact: None

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_snapshot_ensures_tracer_even_when_client_is_not_authority(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle.client.is_bot_authority = lambda: False
        battle._resolve_descriptor = lambda unused_name: (
            battle._server_entity(41).typeDescriptor)
        battle._remote_factory = types.SimpleNamespace(
            play_projectile_tracer=mock.Mock(return_value=1000000))
        row = dict(_event(), max_distance=100.0)
        row.pop('maxDistance')
        row.pop('kind')
        row.pop('attacker')
        row.update({
            'checked_through_ms': 0, 'checked_distance': 0.0,
            'piercing_loss': 0.0,
        })

        self.assertTrue(battle._reconcile_projectile_snapshot({
            'projectiles': [row]}))

        self.assertEqual(1, battle._remote_factory.
                         play_projectile_tracer.call_count)
        self.assertFalse(battle._projectiles.contains('player:7:1'))

    def test_canonical_launch_waits_for_trajectory_impact(self):
        battle, bigworld = _battle()
        bigworld.wall_x = 5.0
        self.assertTrue(battle._accept_projectile_event(_event()))

        bigworld.now = 0.4
        self.assertTrue(battle._advance_projectiles(0.4))
        self.assertEqual([], battle.client.resolutions)
        self.assertTrue(battle._projectiles.contains('player:7:1'))
        cursor = battle.client.progress[-1][1][0]
        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': cursor['checked_through_ms'],
            'checked_distance': cursor['checked_distance'],
            'piercing_loss': cursor['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))

        bigworld.now = 0.6
        self.assertTrue(battle._advance_projectiles(0.6))
        self.assertEqual(1, len(battle.client.resolutions))
        args, kwargs = battle.client.resolutions[0]
        self.assertEqual('player:7:1', args[1])
        self.assertEqual('impact', args[3])
        self.assertGreaterEqual(args[4], 499)
        self.assertLessEqual(args[4], 501)
        self.assertAlmostEqual(5.0, args[5][0], places=5)
        self.assertIsNone(args[6])
        self.assertEqual([], args[7])
        self.assertAlmostEqual(5.0, kwargs['checked_distance'], places=4)

    def test_first_ricochet_relaunches_once_and_second_ricochet_terminates(self):
        battle, bigworld = _battle(now=0.5)
        battle._projectile_server_time_ms = 500
        battle._projectile_server_local_time = 0.5
        event = _event()
        self.assertTrue(battle._accept_projectile_event(event))
        meta = battle._projectile_meta['player:7:1']
        meta['destructibles_pending'] = [{
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 3, 'x': 1.0, 'y': 0.5, 'z': 0.0,
            'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
        }]
        state = battle._projectiles.get('player:7:1')
        state.update({
            'cursor_time': 0.5,
            'elapsed': 0.5,
            'position': (5.0, 1.0, 0.0),
            'distance': 5.0,
        })
        terminal_data = {
            'impact': (5.0, 1.0, 0.0),
            'target_key': 'bot:17',
        }
        battle._projectile_terminal_data['player:7:1'] = terminal_data

        def ricochet_effect(unused_meta, unused_state, data):
            data['world_normal'] = (1.0, 0.0, 0.0)
            return {
                'target_kind': 'bot', 'target_id': 17,
                'damage': 0, 'shot_result': 0,
                'x': 5.0, 'y': 1.0, 'z': 0.0,
            }

        battle._projectile_direct_effect = ricochet_effect
        self.assertTrue(battle._projectile_terminal(
            state, {'reason': 'impact'}))
        self.assertEqual(1, len(battle.client.ricochets))
        args, kwargs = battle.client.ricochets[0]
        self.assertEqual(500, args[3])
        self.assertAlmostEqual(-10.0, args[6][0], places=6)
        self.assertEqual(0.75, args[7])
        self.assertAlmostEqual(5.0, kwargs['checked_distance'])
        first_request = battle.client.ricochets[0]
        self.assertTrue(meta['awaiting_ricochet'])
        self.assertIsNotNone(meta['pending_ricochet'])
        self.assertTrue(meta['destructibles_pending'])

        # The first-segment snapshot is the negative acknowledgement for the
        # retained CAS request. Retry the byte-equivalent frozen proposal.
        battle._projectiles.remove('player:7:1')
        snapshot_row = dict(event)
        snapshot_row['max_distance'] = snapshot_row.pop('maxDistance')
        snapshot_row.pop('kind')
        snapshot_row.pop('attacker')
        snapshot_row.update({
            'checked_through_ms': 0, 'checked_distance': 0.0,
            'piercing_loss': 0.0,
        })
        self.assertTrue(battle._reconcile_projectile_snapshot({
            'projectiles': [snapshot_row]}))
        self.assertEqual(2, len(battle.client.ricochets))
        self.assertEqual(first_request, battle.client.ricochets[1])

        canonical = dict(event)
        canonical.update({
            'kind': 'projectile_ricochet',
            'max_distance': canonical.pop('maxDistance'),
            'checked_through_ms': 500,
            'checked_distance': 5.0,
            'piercing_loss': 0.0,
            'segment_origin': list(args[5]),
            'segment_velocity': list(args[6]),
            'segment_start_time_ms': 500,
            'ricochet_count': 1,
            'base_penetration_multiplier': 0.75,
            'resolved_time_ms': 500,
            'impact': [5.0, 1.0, 0.0],
            'direct': args[8],
        })
        self.assertTrue(battle._apply_projectile_ricochet_event(canonical))
        self.assertIsNone(meta['pending_ricochet'])
        self.assertFalse(meta['awaiting_ricochet'])
        self.assertEqual([], meta['destructibles_pending'])
        manager_key = ('player:7:1', 1)
        self.assertFalse(battle._projectiles.contains('player:7:1'))
        self.assertTrue(battle._projectiles.contains(manager_key))
        second = battle._projectiles.get(manager_key)
        self.assertEqual((0.0, 0.0, 0.0),
                         second['payload']['range_origin'])
        self.assertEqual(0.75,
                         second['payload']['base_penetration_multiplier'])

        battle._projectile_terminal_data['player:7:1'] = {
            'impact': tuple(second['position']),
            'target_key': 'bot:18',
        }
        self.assertTrue(battle._projectile_terminal(
            second, {'reason': 'impact'}))
        self.assertEqual(2, len(battle.client.ricochets))
        self.assertEqual(1, len(battle.client.resolutions))
        self.assertEqual('impact', battle.client.resolutions[0][0][3])

    def test_over_limit_reflection_falls_back_to_terminal_resolution(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_server_time_ms = 1000
        battle._projectile_server_local_time = 1.0
        self.assertTrue(battle._accept_projectile_event(_event()))
        state = battle._projectiles.get('player:7:1')
        state.update({
            'cursor_time': 1.0, 'elapsed': 1.0,
            'position': (10.0, 1.0, 0.0), 'distance': 10.0,
            'velocity': (3000.0, 0.0, 0.0),
            'gravity': (0.0, -100.0, 0.0),
        })
        battle._projectile_terminal_data['player:7:1'] = {
            'impact': (10.0, 1.0, 0.0), 'target_key': 'bot:17',
        }

        def ricochet_effect(unused_meta, unused_state, data):
            data['world_normal'] = (1.0, 0.0, 0.0)
            return {
                'target_kind': 'bot', 'target_id': 17,
                'damage': 0, 'shot_result': 0,
                'x': 10.0, 'y': 1.0, 'z': 0.0,
            }

        battle._projectile_direct_effect = ricochet_effect
        self.assertTrue(battle._projectile_terminal(
            state, {'reason': 'impact'}))
        self.assertEqual([], battle.client.ricochets)
        self.assertEqual(1, len(battle.client.resolutions))

    def test_far_records_skip_native_entity_lookup_before_exact_broadphase(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target_ids = []
        current = {'player:7': (0.0, 0.0, 0.0)}
        for index in range(29):
            key = 'bot:%d' % index
            engine_id = 1000 + index
            target_ids.append(engine_id)
            battle._records[key] = {
                'engine_id': engine_id, 'network_id': index, 'kind': 'bot',
                'local': False, 'ready': True,
                'state': {'health': 100, 'alive': True}}
            current[key] = (1000.0 + index, 0.0, 1000.0)
        queried = []

        def server_entity(entity_id):
            queried.append(entity_id)
            return source if entity_id == 41 else None

        battle._server_entity = server_entity
        battle._projectile_current_positions = current
        battle._projectile_position_history = []
        poses = dict(
            (key, battle._projectile_plain_pose(position))
            for key, position in current.items())
        battle._sample_projectile_positions(0.0, poses)
        battle._sample_projectile_positions(0.05, poses)
        battle._projectile_scan_count = 0
        battle._projectile_candidate_count = 0
        state = battle._projectiles.get('player:7:1')

        self.assertIsNone(battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 0.0, 0.05))
        self.assertEqual(30, battle._projectile_scan_count)
        self.assertEqual(0, battle._projectile_candidate_count)
        self.assertTrue(queried)
        self.assertFalse(set(target_ids).intersection(queried))

    def test_spatial_index_keeps_old_debt_target_after_it_moves_away(self):
        battle, unused_bigworld = _battle()
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        for sample_time, target_x in ((0.0, 5.0), (0.5, 100.0),
                                      (1.0, 200.0)):
            battle._sample_projectile_positions(sample_time, {
                'player:7': source,
                'bot:8': battle._projectile_plain_pose(
                    (target_x, 0.0, 0.0)),
            })

        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 1.0))
        candidates = dict(battle._projectile_chord_records(
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), 0.0, 0.05))

        self.assertIn('bot:8', candidates)

    def test_spatial_index_keeps_target_near_only_at_middle_sample(self):
        battle, unused_bigworld = _battle()
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        for sample_time, target_x in ((0.0, 200.0), (0.5, 5.0),
                                      (1.0, 200.0)):
            battle._sample_projectile_positions(sample_time, {
                'player:7': source,
                'bot:8': battle._projectile_plain_pose(
                    (target_x, 0.0, 0.0)),
            })

        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 1.0))
        candidates = dict(battle._projectile_chord_records(
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), 0.45, 0.55))

        self.assertNotIn('bot:8', battle._projectile_spatial_fallback_keys)
        self.assertIn('bot:8', candidates)

    def test_spatial_index_keeps_incomplete_history_as_full_scan_candidate(
            self):
        battle, unused_bigworld = _battle()
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(0.0, {'player:7': source})
        battle._sample_projectile_positions(1.0, {
            'player:7': source,
            'bot:8': battle._projectile_plain_pose(
                (1000.0, 0.0, 1000.0)),
        })

        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 1.0))
        candidates = dict(battle._projectile_chord_records(
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 0.0, 0.05))

        self.assertIn('bot:8', battle._projectile_spatial_fallback_keys)
        self.assertIn('bot:8', candidates)

    def test_spatial_index_falls_back_when_history_or_records_change(self):
        battle, unused_bigworld = _battle()
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(1.0, {'player:7': source})
        battle._sample_projectile_positions(2.0, {'player:7': source})

        self.assertFalse(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.5},), 2.0))
        self.assertEqual(
            set(battle._records),
            set(dict(battle._projectile_chord_records(
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 0.5, 0.55))))

        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 1.0},), 2.0))
        battle._records['bot:9'] = {
            'engine_id': 43, 'network_id': 9, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._records_revision += 1
        candidates = dict(battle._projectile_chord_records(
            (1000.0, 0.0, 1000.0), (1001.0, 0.0, 1000.0),
            1.0, 1.05))
        self.assertEqual(set(battle._records), set(candidates))

    def test_spatial_index_candidates_cover_relative_motion_broadphase(self):
        battle, unused_bigworld = _battle()
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        generator = random.Random(1513)
        for unused_index in range(300):
            projectile_start = (
                generator.uniform(-100.0, 100.0), 0.0,
                generator.uniform(-100.0, 100.0))
            projectile_end = (
                generator.uniform(-100.0, 100.0), 0.0,
                generator.uniform(-100.0, 100.0))
            relative_x = generator.uniform(20.0, 80.0)
            relative_z = generator.uniform(20.0, 80.0)
            target_start = (
                projectile_start[0] + relative_x, 0.0,
                projectile_start[2] + relative_z)
            target_end = (
                projectile_end[0] - relative_x, 0.0,
                projectile_end[2] - relative_z)
            battle._projectile_position_history = []
            battle._sample_projectile_positions(0.0, {
                'player:7': source,
                'bot:8': battle._projectile_plain_pose(target_start),
            })
            battle._sample_projectile_positions(1.0, {
                'player:7': source,
                'bot:8': battle._projectile_plain_pose(target_end),
            })

            self.assertTrue(battle._build_projectile_spatial_bins(
                ({'cursor_time': 0.0},), 1.0))
            self.assertNotIn(
                'bot:8', battle._projectile_spatial_fallback_keys)
            candidates = dict(battle._projectile_chord_records(
                projectile_start, projectile_end, 0.0, 1.0))
            self.assertIn('bot:8', candidates)

    def test_spatial_index_excessive_target_span_uses_fallback(self):
        battle, unused_bigworld = _battle()
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(0.0, {
            'player:7': source,
            'bot:8': battle._projectile_plain_pose((-64.0, 0.0, -64.0)),
        })
        battle._sample_projectile_positions(1.0, {
            'player:7': source,
            'bot:8': battle._projectile_plain_pose((64.0, 0.0, 64.0)),
        })
        module = sys.modules[BattleRuntime.__module__]

        with mock.patch.object(
                module, 'PROJECTILE_SPATIAL_MAX_TARGET_CELLS', 1):
            self.assertTrue(battle._build_projectile_spatial_bins(
                ({'cursor_time': 0.0},), 1.0))
            candidates = dict(battle._projectile_chord_records(
                (1000.0, 0.0, 1000.0),
                (1001.0, 0.0, 1000.0), 0.0, 0.05))

        self.assertIn('bot:8', battle._projectile_spatial_fallback_keys)
        self.assertIn('bot:8', candidates)

    def test_spatial_index_skips_more_expensive_history_build(self):
        battle, unused_bigworld = _battle()
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        for index in range(101):
            battle._sample_projectile_positions(
                float(index) / 100.0, {'player:7': source})

        self.assertFalse(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 1.0, maximum_chords=1))
        self.assertIsNone(battle._projectile_spatial_bins)

    def test_spatial_index_cell_budgets_fall_back_to_all_records(self):
        battle, unused_bigworld = _battle()
        source = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(0.0, {'player:7': source})
        battle._sample_projectile_positions(1.0, {'player:7': source})
        module = sys.modules[BattleRuntime.__module__]

        with mock.patch.object(
                module, 'PROJECTILE_SPATIAL_MAX_TOTAL_CELLS', 1):
            self.assertFalse(battle._build_projectile_spatial_bins(
                ({'cursor_time': 0.0},), 1.0))
        self.assertIsNone(battle._projectile_spatial_bins)

        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 1.0))
        with mock.patch.object(
                module, 'PROJECTILE_SPATIAL_MAX_QUERY_CELLS', 1):
            candidates = dict(battle._projectile_chord_records(
                (1000.0, 0.0, 1000.0),
                (1200.0, 0.0, 1000.0), 0.0, 0.05))
        self.assertEqual(set(battle._records), set(candidates))

    def test_worker_player_projectile_uses_hydraulic_body_and_chassis(self):
        battle, unused_bigworld = _battle()
        battle._worker_mode = True
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        rendered_matrix = object()
        canonical_matrix = object()
        chassis_matrix = object()
        target = types.SimpleNamespace(
            id=42, isStarted=True, matrix=rendered_matrix,
            position=_Vector((5.0, 1.0, 0.0)), isAlive=lambda: True)
        target.typeDescriptor = types.SimpleNamespace(
            isPitchHullAimingAvailable=True)
        battle._records['player:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'player',
            'native_remote': True, 'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        battle._remote_factory = types.SimpleNamespace(
            projectile_collision_matrices=mock.Mock(
                return_value=(canonical_matrix, chassis_matrix)))
        battle._projectile_current_positions = {
            'player:7': (0.0, 0.0, 0.0),
            'player:8': (5.0, 1.0, 0.0),
        }
        battle._projectile_position_history = []
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        target_pose = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        for sample_time in (0.0, 0.1):
            battle._sample_projectile_positions(sample_time, {
                'player:7': source_pose, 'player:8': target_pose})
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })
        collision = types.SimpleNamespace(dist=5.0)
        evidence = types.SimpleNamespace(
            collision=collision, worldNormal=None)
        module = sys.modules[BattleRuntime.__module__]
        state = battle._projectiles.get('player:7:1')

        with mock.patch.object(
                module, '_collide_vehicle_evidence_at_matrix',
                return_value=(evidence,)) as collide:
            terminal = battle._projectile_chord(
                state, (0.0, 1.0, 0.0), (10.0, 1.0, 0.0),
                0.0, 0.1)

        self.assertEqual({'reason': 'impact', 'fraction': 0.5}, terminal)
        battle._remote_factory.projectile_collision_matrices.\
            assert_called_once_with(42, None)
        self.assertIs(canonical_matrix, collide.call_args.args[1])
        self.assertIs(
            chassis_matrix,
            collide.call_args.kwargs['chassis_matrix'])
        self.assertIsNot(rendered_matrix, collide.call_args.args[1])

    def test_destroyed_vehicle_still_owns_projectile_collision(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        wreck = types.SimpleNamespace(
            id=42, isStarted=True,
            position=_Vector((5.0, 1.0, 0.0)), isAlive=lambda: False,
            collideSegmentExt=mock.Mock(return_value=(
                types.SimpleNamespace(dist=5.0),)))
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 0, 'alive': False}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else wreck if entity_id == 42 else None)
        battle._projectile_current_positions = {
            'player:7': (0.0, 0.0, 0.0),
            'bot:8': (5.0, 1.0, 0.0),
        }
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        wreck_pose = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        for sample_time in (0.0, 0.1):
            battle._sample_projectile_positions(sample_time, {
                'player:7': source_pose, 'bot:8': wreck_pose})
        self.assertTrue(battle._build_projectile_spatial_bins(
            ({'cursor_time': 0.0},), 0.1))
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })
        collision = types.SimpleNamespace(dist=5.0)
        battle._projectile_vehicle_collisions = mock.Mock(
            return_value=((collision,), ()))
        state = battle._projectiles.get('player:7:1')

        terminal = battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (10.0, 1.0, 0.0), 0.0, 0.1)

        self.assertEqual({'reason': 'impact', 'fraction': 0.5}, terminal)
        self.assertEqual(
            'bot:8', battle._projectile_terminal_data[
                'player:7:1']['target_key'])
        battle._projectile_vehicle_collisions.assert_called_once()
        wreck.collideSegmentExt.assert_not_called()

        self.assertTrue(battle._projectile_terminal(
            state, {'reason': 'impact'}))
        args, kwargs = battle.client.resolutions[-1]
        self.assertIsNone(args[6])
        self.assertTrue(kwargs['hit_vehicle'])
        self.assertEqual(
            {'target_kind': 'bot', 'target_id': 8},
            kwargs['wreck_hit'])

    def test_stock_max_29_projectile_debt_bounds_frame_and_reduces_scans(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        entities = {41: source}
        for index in range(29):
            engine_id = 1000 + index
            entities[engine_id] = types.SimpleNamespace(
                id=engine_id, isStarted=True,
                position=_Vector((1000.0 + index, 0.0, 1000.0)),
                isAlive=lambda: True,
                collideSegmentExt=mock.Mock(return_value=[]))
            battle._records['bot:%d' % index] = {
                'engine_id': engine_id, 'network_id': index, 'kind': 'bot',
                'local': False, 'ready': True,
                'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: entities.get(entity_id)
        for shot_seq in range(1, 30):
            event = dict(_event())
            event.update({
                'projectile_id': 'player:7:%d' % shot_seq,
                'shot_seq': shot_seq,
                'burst_group_seq': shot_seq,
                'gravity': 190.0,
                'maxDistance': 10000.0,
            })
            event['source_shot'] = dict(event['source_shot'])
            event['source_shot'].update(
                gravity=190.0, maxDistance=10000.0)
            self.assertTrue(battle._accept_projectile_event(event))

        bigworld.now = 1.0
        totals = {'chords': 0, 'scans': 0}
        invocations = 0
        while True:
            self.assertTrue(battle._advance_projectiles(1.0))
            perf = battle._projectile_perf
            totals['chords'] += perf['chords']
            totals['scans'] += perf['scans']
            invocations += 1
            if perf['debt'] <= 1e-9:
                break
            self.assertLess(invocations, 11)

        self.assertEqual(11, invocations)
        self.assertEqual(638, totals['chords'])
        # The advance-scoped spatial index keeps the source record as the only
        # nearby broad-phase entry instead of rescanning all 30 records for
        # every chord.
        self.assertEqual(638, totals['scans'])
        self.assertEqual(58, battle._projectile_perf['chords'])
        self.assertEqual(58, battle._projectile_perf['scans'])
        self.assertEqual(0, battle._projectile_perf['candidates'])
        self.assertIsNone(battle._projectile_spatial_bins)
        for entity_id, entity in entities.items():
            if entity_id != 41:
                entity.collideSegmentExt.assert_not_called()

    def test_projectile_advance_memoizes_repeated_historic_pose_queries(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        entities = {41: source}
        for index in range(29):
            engine_id = 1000 + index
            entities[engine_id] = types.SimpleNamespace(
                id=engine_id, isStarted=True,
                position=_Vector((1000.0 + index, 0.0, 1000.0)),
                isAlive=lambda: True,
                collideSegmentExt=mock.Mock(return_value=[]))
            battle._records['bot:%d' % index] = {
                'engine_id': engine_id, 'network_id': index, 'kind': 'bot',
                'local': False, 'ready': True,
                'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: entities.get(entity_id)
        for shot_seq in range(1, 3):
            event = dict(_event())
            event.update({
                'projectile_id': 'player:7:%d' % shot_seq,
                'shot_seq': shot_seq,
                'burst_group_seq': shot_seq,
                'gravity': 190.0,
                'maxDistance': 10000.0,
            })
            event['source_shot'] = dict(event['source_shot'])
            event['source_shot'].update(
                gravity=190.0, maxDistance=10000.0)
            self.assertTrue(battle._accept_projectile_event(event))
        uncached = mock.Mock(
            wraps=battle._projectile_historic_pose_uncached)
        battle._projectile_historic_pose_uncached = uncached
        # Isolate the historic-pose memoization contract from the independent
        # spatial broad phase exercised by the preceding stress case.
        battle._build_projectile_spatial_bins = mock.Mock(return_value=False)

        bigworld.now = 1.0
        self.assertTrue(battle._advance_projectiles(1.0))

        self.assertEqual(32, battle._projectile_perf['chords'])
        self.assertEqual(960, battle._projectile_perf['scans'])
        self.assertEqual(0, battle._projectile_perf['candidates'])
        # Two identical trajectories share 33 exact time samples for every
        # one of the 29 non-source records instead of recomputing all
        # 32 * 29 * 3 chord queries.
        self.assertEqual(957, uncached.call_count)
        self.assertIsNone(battle._projectile_historic_pose_cache)

    def test_projectile_pose_cache_is_advance_scoped_and_detached(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((10.0, 0.0, 0.0)),
            isAlive=lambda: True)
        entities = {41: source, 42: target}
        battle._server_entity = lambda entity_id: entities.get(entity_id)
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        uncached = mock.Mock(
            wraps=battle._projectile_historic_pose_uncached)
        battle._projectile_historic_pose_uncached = uncached
        observed = []

        def advance(now, unused_chord, unused_terminal,
                    maximum_chords=None):
            self.assertEqual(32, maximum_chords)
            self.assertIsInstance(
                battle._projectile_historic_pose_cache, dict)
            first = battle._projectile_historic_pose('bot:8', 2.0)
            second = battle._projectile_historic_pose('bot:8', 2.0)
            if first is None:
                self.assertIsNone(second)
            else:
                first['x'] = -999.0
                observed.append(second['x'])
            return True

        battle._projectiles.advance = advance
        bigworld.now = 1.0
        self.assertTrue(battle._advance_projectiles(1.0))
        self.assertEqual(1, uncached.call_count)
        self.assertIsNone(battle._projectile_historic_pose_cache)

        target.position = _Vector((20.0, 0.0, 0.0))
        bigworld.now = 2.0
        self.assertTrue(battle._advance_projectiles(2.0))

        self.assertEqual([20.0], observed)
        self.assertEqual(2, uncached.call_count)
        self.assertIsNone(battle._projectile_historic_pose_cache)

    def test_projectile_pose_cache_has_a_hard_entry_limit(self):
        battle, unused_bigworld = _battle()
        module = sys.modules[BattleRuntime.__module__]
        self.assertEqual(4096, module.PROJECTILE_POSE_CACHE_ENTRIES)
        uncached = mock.Mock(side_effect=lambda unused_key, wanted: {
            'x': wanted, 'y': 0.0, 'z': 0.0})
        battle._projectile_historic_pose_uncached = uncached
        battle._projectile_historic_pose_cache = {}
        try:
            with mock.patch.object(
                    module, 'PROJECTILE_POSE_CACHE_ENTRIES', 2):
                first = battle._projectile_historic_pose('bot:8', 1.0)
                first['x'] = -999.0
                self.assertEqual(
                    1.0,
                    battle._projectile_historic_pose('bot:8', 1.0)['x'])
                battle._projectile_historic_pose('bot:8', 2.0)
                battle._projectile_historic_pose('bot:8', 3.0)
                battle._projectile_historic_pose('bot:8', 3.0)
                self.assertEqual(2, len(
                    battle._projectile_historic_pose_cache))
                self.assertEqual(4, uncached.call_count)
        finally:
            battle._projectile_historic_pose_cache = None

    def test_budgeted_catchup_uses_historic_target_pose_for_relative_sweep(self):
        battle, bigworld = _battle()
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((10.0, 0.0, 0.0)),
            isAlive=lambda: True)
        observed = []

        def collide(unused_record, unused_target, start, end, pose=None):
            observed.append((tuple(start), tuple(end), dict(pose)))
            return (), ()

        battle._projectile_vehicle_collisions = collide
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._server_entity(41)
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        battle._projectile_record_positions = mock.Mock(side_effect=[
            {'player:7': (0.0, 0.0, 0.0), 'bot:8': (10.0, 0.0, 0.0)},
            {'player:7': (0.0, 0.0, 0.0), 'bot:8': (20.0, 0.0, 0.0)},
            {'player:7': (0.0, 0.0, 0.0), 'bot:8': (20.0, 0.0, 0.0)},
        ])
        self.assertTrue(battle._accept_projectile_event(_event()))
        module = sys.modules[BattleRuntime.__module__]
        old_budget = module.PROJECTILE_CHORDS_PER_FRAME
        old_maximum = module.PROJECTILE_MAX_CHORDS_PER_FRAME
        module.PROJECTILE_CHORDS_PER_FRAME = 1
        module.PROJECTILE_MAX_CHORDS_PER_FRAME = 1
        try:
            bigworld.now = 1.0
            battle._advance_projectiles(1.0)
            bigworld.now = 1.1
            battle._advance_projectiles(1.1)
        finally:
            module.PROJECTILE_CHORDS_PER_FRAME = old_budget
            module.PROJECTILE_MAX_CHORDS_PER_FRAME = old_maximum

        self.assertGreaterEqual(len(observed), 2)
        # The second delayed chord still belongs near launch time. Its collision
        # pose must remain near that time instead of using the render target at
        # x=20 from the newest authority sample.
        self.assertGreater(observed[-1][2]['x'], 10.0)
        self.assertLess(observed[-1][2]['x'], 12.0)

    def test_collision_pose_history_interpolates_angles_and_steps_siege(self):
        battle, unused_bigworld = _battle()
        near_pi = math.radians(179.0)
        first = {
            'x': 0.0, 'y': 1.0, 'z': 2.0,
            'yaw': near_pi, 'pitch': near_pi, 'roll': near_pi,
            'turret_yaw': near_pi, 'gun_pitch': near_pi,
            'siege_state': 1,
        }
        second = {
            'x': 10.0, 'y': 3.0, 'z': 4.0,
            'yaw': -near_pi, 'pitch': -near_pi, 'roll': -near_pi,
            'turret_yaw': -near_pi, 'gun_pitch': -near_pi,
            'siege_state': 2,
        }
        battle._sample_projectile_positions(1.0, {'bot:8': first})
        battle._sample_projectile_positions(2.0, {'bot:8': second})

        self.assertIsNone(battle._projectile_historic_pose('bot:8', 0.999))
        middle = battle._projectile_historic_pose('bot:8', 1.5)
        self.assertEqual((5.0, 2.0, 3.0), (
            middle['x'], middle['y'], middle['z']))
        for name in ('yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch'):
            self.assertAlmostEqual(math.pi, middle[name])
        self.assertEqual(1, middle['siege_state'])
        self.assertEqual(
            1, battle._projectile_historic_pose('bot:8', 1.999)[
                'siege_state'])
        self.assertEqual(
            2, battle._projectile_historic_pose('bot:8', 2.0)[
                'siege_state'])
        self.assertIsNone(battle._projectile_historic_pose('bot:8', 2.001))

    def test_record_pose_does_not_mix_frozen_xyz_with_render_interpolation(
            self):
        battle, unused_bigworld = _battle()
        target = types.SimpleNamespace(id=42, isStarted=True)
        battle._server_entity = lambda entity_id: (
            target if entity_id == 42 else None)
        record = {
            'engine_id': 42,
            'state': {'x': 8.0, 'y': 2.0, 'z': 9.0},
            'projectile_collision_pose': {
                'x': 7.0, 'y': 2.0, 'z': 9.0,
                'yaw': 0.75, 'pitch': 0.2, 'roll': -0.3,
                'turret_yaw': 0.15, 'gun_pitch': -0.1,
                'siege_state': 2,
            },
        }

        pose = battle._projectile_record_pose(
            'bot:8', record, (8.5, 2.0, 9.0))

        self.assertEqual(
            (7.0, 2.0, 9.0), (pose['x'], pose['y'], pose['z']))
        self.assertEqual(2, pose['siege_state'])

    def test_rotating_target_chord_queries_angle_bounded_pose_segments(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((5.0, 1.0, 0.0)))
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        target_start = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        target_end = dict(target_start)
        target_end['yaw'] = math.radians(4.0)
        battle._projectile_position_history = []
        battle._sample_projectile_positions(0.0, {
            'player:7': source_pose, 'bot:8': target_start})
        battle._sample_projectile_positions(0.05, {
            'player:7': source_pose, 'bot:8': target_end})
        observed = []

        def collide(unused_record, unused_target, unused_start, unused_end,
                    pose=None):
            observed.append(dict(pose))
            return (), ()

        battle._projectile_vehicle_collisions = collide
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })

        result = battle._projectile_chord(
            battle._projectiles.get('player:7:1'),
            (0.0, 1.0, 0.0), (10.0, 1.0, 0.0), 0.0, 0.05)

        self.assertIsNone(result)
        self.assertEqual(4, len(observed))
        for index, pose in enumerate(observed):
            self.assertAlmostEqual(
                math.radians(index + 0.5), pose['yaw'])

    def test_pose_sweep_bounds_composed_hull_turret_and_gun_rotation(self):
        battle, unused_bigworld = _battle()
        first = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        second = dict(first)
        second.update({
            'yaw': math.radians(0.6),
            'turret_yaw': math.radians(0.6),
            'gun_pitch': math.radians(0.6),
        })
        battle._sample_projectile_positions(0.0, {'bot:8': first})
        battle._sample_projectile_positions(0.05, {'bot:8': second})

        fractions = battle._projectile_pose_sweep_fractions(
            'bot:8', 0.0, 0.05)

        self.assertEqual(3, len(fractions))
        self.assertEqual((0.0, 1.0), (fractions[0], fractions[-1]))

    def test_pose_sweep_splits_at_intermediate_rotation_reversal(self):
        battle, unused_bigworld = _battle()
        first = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        middle = dict(first)
        middle['yaw'] = math.radians(4.0)
        battle._sample_projectile_positions(0.0, {'bot:8': first})
        battle._sample_projectile_positions(0.025, {'bot:8': middle})
        battle._sample_projectile_positions(0.05, {'bot:8': first})

        fractions = battle._projectile_pose_sweep_fractions(
            'bot:8', 0.0, 0.05)

        self.assertEqual(9, len(fractions))
        self.assertAlmostEqual(0.5, fractions[4])

    def test_pose_sweep_does_not_count_stationary_high_rate_samples_as_steps(
            self):
        battle, unused_bigworld = _battle()
        pose = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        for index in range(101):
            battle._sample_projectile_positions(
                float(index) / 2000.0, {'bot:8': pose})

        fractions = battle._projectile_pose_sweep_fractions(
            'bot:8', 0.0, 0.05)

        self.assertEqual((0.0, 1.0), fractions)

    def test_pose_history_interpolates_between_known_long_gap_endpoints(self):
        battle, unused_bigworld = _battle()
        first = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        second = battle._projectile_plain_pose((15.0, 1.0, 0.0))
        battle._sample_projectile_positions(0.0, {'bot:8': first})
        battle._sample_projectile_positions(2.0, {'bot:8': second})

        self.assertEqual(
            10.0, battle._projectile_historic_pose('bot:8', 1.0)['x'])
        self.assertEqual(
            15.0, battle._projectile_historic_pose('bot:8', 2.0)['x'])
        self.assertIsNone(battle._projectile_historic_pose('bot:8', 2.001))

    def test_two_second_render_stall_keeps_projectile_elapsed_time(self):
        battle, bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((5.0, 1.0, 0.0)),
            isAlive=lambda: True)
        target_pose = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'projectile_collision_pose': target_pose,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._projectile_position_history = []
        battle._sample_projectile_positions(0.0, {
            'player:7': source_pose, 'bot:8': target_pose})
        observed = []

        def collide(unused_record, unused_target, unused_start, unused_end,
                    pose=None):
            observed.append(dict(pose))
            return (), ()

        battle._projectile_vehicle_collisions = collide
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })
        target.position = _Vector((15.0, 1.0, 0.0))
        battle._records['bot:8']['projectile_collision_pose'] = \
            battle._projectile_plain_pose((15.0, 1.0, 0.0))
        bigworld.now = 2.0

        invocations = 0
        for unused_index in range(8):
            self.assertTrue(battle._advance_projectiles(2.0))
            invocations += 1
            if battle._projectile_perf['debt'] <= 1.0e-9:
                break
        else:
            self.fail('projectile debt did not drain after a render stall')

        state = battle._projectiles.get('player:7:1')
        self.assertEqual(2, invocations)
        self.assertAlmostEqual(2.0, state['cursor_time'])
        self.assertEqual([], battle.client.resolutions)
        self.assertTrue(observed)
        self.assertLess(min(pose['x'] for pose in observed), 6.0)
        self.assertGreater(max(pose['x'] for pose in observed), 14.0)

    def test_excessive_angular_target_sweep_fails_closed(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((5.0, 1.0, 0.0)))
        record = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._records['bot:8'] = record
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        target_start = battle._projectile_plain_pose((5.0, 1.0, 0.0))
        target_end = dict(target_start)
        target_end['yaw'] = math.radians(17.0)
        battle._projectile_position_history = []
        battle._sample_projectile_positions(0.0, {
            'player:7': source_pose, 'bot:8': target_start})
        battle._sample_projectile_positions(0.05, {
            'player:7': source_pose, 'bot:8': target_end})
        battle._projectile_vehicle_collisions = mock.Mock()

        result = battle._projectile_chord(
            battle._projectiles.get('player:7:1'),
            (0.0, 1.0, 0.0), (10.0, 1.0, 0.0), 0.0, 0.05)

        self.assertEqual(
            {'reason': 'callback_error', 'fraction': 0.0}, result)
        self.assertEqual(
            'angular_sweep_limit_exceeded',
            record['projectile_collision_pose_boundary'])
        battle._projectile_vehicle_collisions.assert_not_called()

    def test_chord_before_pose_history_coverage_fails_closed(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target = types.SimpleNamespace(id=42, isStarted=True)
        state = battle._projectiles.get('player:7:1')
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else
            target if entity_id == 42 else None)

        result = battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), -0.05, 0.0)

        self.assertEqual(
            {'reason': 'callback_error', 'fraction': 0.0}, result)

    def test_pending_record_without_pose_history_does_not_retire_projectile(
            self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(0.05, {
            'player:7': source_pose})
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': False,
            'state': {'health': 100, 'alive': True}}
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })

        result = battle._projectile_chord(
            battle._projectiles.get('player:7:1'),
            (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 0.0, 0.05)

        self.assertIsNone(result)

    def test_worker_private_carrier_is_not_a_projectile_target(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        battle._sample_projectile_positions(0.05, {
            'player:7': source_pose})
        battle._worker_mode = True
        battle._records['player:-1'] = {
            'engine_id': 42, 'network_id': -1, 'kind': 'player',
            'local': True, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })

        result = battle._projectile_chord(
            battle._projectiles.get('player:7:1'),
            (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 0.0, 0.05)

        self.assertIsNone(result)

    def test_historic_component_collision_uses_one_frozen_pose_and_four_fields(
            self):
        battle, unused_bigworld = _battle()

        class _PoseMatrix(object):
            def __init__(self):
                self.ypr = None
                self.translation = None

            def setRotateYPR(self, value):
                self.ypr = tuple(value)

        battle._runtime.math.Matrix = _PoseMatrix
        descriptor = types.SimpleNamespace(
            hasSiegeMode=False, isPitchHullAimingAvailable=False)
        target = types.SimpleNamespace(typeDescriptor=descriptor)
        record = {}
        collision = (0.25, 0.75, object(), 'vehicleHull')
        evidence = types.SimpleNamespace(
            collision=collision, worldNormal=_Vector((0.0, 0.0, 1.0)))
        pose = {
            'x': 4.0, 'y': 5.0, 'z': 6.0,
            'yaw': 0.1, 'pitch': 0.2, 'roll': 0.3,
            'turret_yaw': 0.4, 'gun_pitch': -0.5,
            'siege_state': 0,
        }
        module = sys.modules[BattleRuntime.__module__]

        with mock.patch.object(
                module, '_collide_vehicle_evidence_at_matrix',
                return_value=(evidence,)) as collide:
            collisions, returned_evidence = \
                battle._projectile_vehicle_collisions(
                    record, target, _Vector(), _Vector((10.0, 0.0, 0.0)),
                    pose)

        frozen_target, matrix = collide.call_args[0][:2]
        self.assertIs(descriptor, frozen_target.typeDescriptor)
        self.assertEqual((0.1, 0.2, 0.3), matrix.ypr)
        self.assertEqual((4.0, 5.0, 6.0), tuple(matrix.translation))
        self.assertEqual((0.4, 0.0, 0.0),
                         frozen_target.appearance.turretMatrix.ypr)
        self.assertEqual((0.0, -0.5, 0.0),
                         frozen_target.appearance.gunMatrix.ypr)
        self.assertEqual((collision,), collisions)
        self.assertEqual(4, len(collisions[0]))
        self.assertEqual((evidence,), returned_evidence)

    def test_frozen_collision_uses_active_descriptor_static_gun_angles(self):
        battle, unused_bigworld = _battle()

        class _PoseMatrix(object):
            def __init__(self):
                self.ypr = None
                self.translation = None

            def setRotateYPR(self, value):
                self.ypr = tuple(value)

        battle._runtime.math.Matrix = _PoseMatrix
        default = types.SimpleNamespace(
            gun=types.SimpleNamespace(
                staticTurretYaw=None, staticPitch=None))
        siege = types.SimpleNamespace(
            gun=types.SimpleNamespace(
                staticTurretYaw=0.0,
                staticPitch=math.radians(-1.0)))
        descriptor = types.SimpleNamespace(
            hasSiegeMode=True, defaultVehicleDescr=default,
            siegeVehicleDescr=siege)
        target = types.SimpleNamespace(typeDescriptor=descriptor)
        pose = {
            'x': 4.0, 'y': 5.0, 'z': 6.0,
            'yaw': 0.1, 'pitch': 0.2, 'roll': 0.3,
            'turret_yaw': 0.4, 'gun_pitch': -0.5,
            'siege_state': 2,
        }

        frozen = battle._projectile_frozen_target(target, pose)

        self.assertIs(siege, frozen.typeDescriptor)
        self.assertEqual((0.0, 0.0, 0.0),
                         frozen.appearance.turretMatrix.ypr)
        self.assertEqual(
            (0.0, math.radians(-1.0), 0.0),
            frozen.appearance.gunMatrix.ypr)
        self.assertEqual(0.4, pose['turret_yaw'])
        self.assertEqual(-0.5, pose['gun_pitch'])

    def test_unsupported_historic_pose_contracts_keep_live_collision_fallback(
            self):
        cases = (
            (True, False,
             'pitch_hull_body_ground_unavailable_live_fallback'),
            (False, True,
             'turret_attachment_history_unavailable_live_fallback'),
        )
        for pitch_hull, detached, boundary in cases:
            with self.subTest(boundary=boundary):
                battle, unused_bigworld = _battle()
                self.assertTrue(battle._accept_projectile_event(_event()))
                source = battle._server_entity(41)
                collision = types.SimpleNamespace(dist=5.0)
                target = types.SimpleNamespace(
                    id=42, isStarted=True,
                    typeDescriptor=types.SimpleNamespace(
                        hasSiegeMode=False,
                        isPitchHullAimingAvailable=pitch_hull),
                    isTurretDetached=detached, matrix=None,
                    position=_Vector((5.0, 1.0, 0.0)),
                    collideSegmentExt=mock.Mock(
                        return_value=(collision,)))
                record = {
                    'engine_id': 42, 'network_id': 8, 'kind': 'bot',
                    'local': False, 'ready': True,
                    'state': {'health': 100, 'alive': True}}
                battle._records['bot:8'] = record
                battle._server_entity = lambda entity_id: (
                    source if entity_id == 41 else
                    target if entity_id == 42 else None)
                source_pose = battle._projectile_plain_pose(
                    (0.0, 0.0, 0.0))
                target_pose = battle._projectile_plain_pose(
                    (5.0, 1.0, 0.0))
                battle._projectile_position_history = []
                battle._sample_projectile_positions(0.0, {
                    'player:7': source_pose, 'bot:8': target_pose})
                battle._sample_projectile_positions(0.05, {
                    'player:7': source_pose, 'bot:8': target_pose})
                battle._projectile_current_positions = {
                    'bot:8': (5.0, 1.0, 0.0)}
                battle._resolve_shot_scene = mock.Mock(return_value={
                    'piercing_loss': 0.0, 'penetration_factor': 1.0,
                    'world_distance': 99999.0,
                    'stopped_by_destructible': False,
                })

                result = battle._projectile_chord(
                    battle._projectiles.get('player:7:1'),
                    (0.0, 1.0, 0.0), (10.0, 1.0, 0.0), 0.0, 0.05)

                self.assertEqual('impact', result['reason'])
                self.assertAlmostEqual(0.5, result['fraction'])
                target.collideSegmentExt.assert_called_once()
                self.assertEqual(
                    boundary, record['projectile_collision_pose_boundary'])
                self.assertIsNone(battle._projectile_terminal_data[
                    'player:7:1']['collision_pose'])

    def test_historic_collision_never_falls_back_to_live_matrix(self):
        battle, unused_bigworld = _battle()
        target = types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(
                hasSiegeMode=False, isPitchHullAimingAvailable=False),
            matrix=object(), collideSegmentExt=mock.Mock())
        record = {}

        collisions, evidence = battle._projectile_vehicle_collisions(
            record, target, _Vector(), _Vector((1.0, 0.0, 0.0)),
            battle._projectile_plain_pose((1.0, 2.0, 3.0)))

        self.assertIsNone(collisions)
        self.assertIsNone(evidence)
        self.assertEqual(
            'historic_component_matrix_unavailable',
            record['projectile_collision_pose_boundary'])
        target.collideSegmentExt.assert_not_called()

    def test_snapshot_restores_only_after_checked_cursor(self):
        battle, bigworld = _battle(now=10.0)
        battle.client.authority_epoch = 2
        snapshot = {
            'server_time_ms': 10000, 'authority_epoch': 2,
            'projectile_revision': 4,
            'projectiles': [dict(
                _event(), max_distance=100.0,
                launch_server_time_ms=8000,
                checked_through_ms=1000, checked_distance=10.0,
                piercing_loss=3.0)]}
        snapshot['projectiles'][0].pop('maxDistance')
        snapshot['projectiles'][0].pop('kind')
        snapshot['projectiles'][0].pop('attacker')
        snapshot['projectiles'][0].pop('authority_epoch')
        snapshot['projectiles'][0]['authority_epoch'] = 2

        battle._observe_projectile_message(snapshot)
        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        state = battle._projectiles.get('player:7:1')
        self.assertEqual(1.0, state['elapsed'])
        self.assertEqual(10.0, state['distance'])
        self.assertEqual(9.0, state['cursor_time'])
        self.assertEqual(3.0, battle._projectile_meta[
            'player:7:1']['piercing_loss'])

    def test_takeover_keeps_pose_history_for_restored_old_vehicle_cursor(self):
        battle, unused_bigworld = _battle(now=10.0)
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True,
            typeDescriptor=types.SimpleNamespace(
                hasSiegeMode=False,
                isPitchHullAimingAvailable=False),
            position=_Vector((0.5, 1.0, 0.0)))
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else
            target if entity_id == 42 else None)
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        target_pose = battle._projectile_plain_pose((0.5, 1.0, 0.0))
        battle._sample_projectile_positions(8.0, {
            'player:7': source_pose, 'bot:8': target_pose})
        battle._sample_projectile_positions(9.0, {
            'player:7': source_pose, 'bot:8': target_pose})
        battle._sample_projectile_positions(10.0, {
            'player:7': source_pose, 'bot:8': target_pose})
        battle._projectile_target_positions = {
            'player:7': (0.0, 0.0, 0.0),
            'bot:8': (0.5, 1.0, 0.0),
        }
        battle._prune_projectile_position_history()
        snapshot = {
            'server_time_ms': 10000, 'authority_epoch': 2,
            'projectile_revision': 4,
            'projectiles': [dict(
                _event(), max_distance=100.0,
                launch_server_time_ms=7000,
                checked_through_ms=1500, checked_distance=15.0,
                piercing_loss=0.0, authority_epoch=2)]}
        snapshot['projectiles'][0].pop('maxDistance')
        snapshot['projectiles'][0].pop('kind')
        snapshot['projectiles'][0].pop('attacker')
        battle.client.authority_epoch = 2

        battle._observe_projectile_message(snapshot)
        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        state = battle._projectiles.get('player:7:1')
        battle._projectile_vehicle_collisions = mock.Mock(
            return_value=((), ()))
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })

        result = battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 8.5, 8.55)

        self.assertIsNone(result)
        self.assertEqual(3, len(battle._projectile_position_history))
        battle._projectile_vehicle_collisions.assert_called_once()

    def test_idle_authority_keeps_history_for_delayed_first_launch(self):
        battle, unused_bigworld = _battle(now=10.0)
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=42, isStarted=True,
            typeDescriptor=types.SimpleNamespace(
                hasSiegeMode=False,
                isPitchHullAimingAvailable=False),
            position=_Vector((0.5, 1.0, 0.0)))
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else
            target if entity_id == 42 else None)
        source_pose = battle._projectile_plain_pose((0.0, 0.0, 0.0))
        target_pose = battle._projectile_plain_pose((0.5, 1.0, 0.0))
        for sample_time in (8.0, 9.0, 10.0):
            battle._sample_projectile_positions(sample_time, {
                'player:7': source_pose, 'bot:8': target_pose})
        battle._prune_projectile_position_history()
        battle._projectile_server_time_ms = 10000
        battle._projectile_server_local_time = 10.0
        event = _event()
        event['launch_server_time_ms'] = 8500

        self.assertTrue(battle._accept_projectile_event(event))
        state = battle._projectiles.get('player:7:1')
        self.assertAlmostEqual(8.5, state['cursor_time'])
        battle._projectile_vehicle_collisions = mock.Mock(
            return_value=((), ()))
        battle._resolve_shot_scene = mock.Mock(return_value={
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'world_distance': 99999.0,
            'stopped_by_destructible': False,
        })

        result = battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 8.5, 8.55)

        self.assertIsNone(result)
        self.assertEqual(3, len(battle._projectile_position_history))
        battle._projectile_vehicle_collisions.assert_called_once()

    def test_takeover_restores_disconnected_shooter_from_frozen_vehicle(self):
        battle, bigworld = _battle(now=10.0)
        descriptor = battle._server_entity(41).typeDescriptor
        battle._records = {}
        battle._server_entity = lambda unused_entity_id: None
        battle._resolve_descriptor = lambda vehicle: (
            descriptor if vehicle == 'ussr:R11_MS-1' else None)
        battle.client.authority_epoch = 2
        snapshot = {
            'server_time_ms': 10000, 'authority_epoch': 2,
            'projectile_revision': 4,
            'projectiles': [dict(
                _event(), max_distance=100.0,
                launch_server_time_ms=10000,
                checked_through_ms=0, checked_distance=0.0,
                piercing_loss=0.0, authority_epoch=2)]}
        snapshot['projectiles'][0].pop('maxDistance')
        snapshot['projectiles'][0].pop('kind')
        snapshot['projectiles'][0].pop('attacker')

        battle._observe_projectile_message(snapshot)
        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        self.assertTrue(battle._projectiles.contains('player:7:1'))
        self.assertIs(descriptor, battle._projectile_meta[
            'player:7:1']['source_descriptor'])

        bigworld.wall_x = 5.0
        bigworld.now = 10.6
        battle._next_projectile_progress_time = 99.0
        self.assertTrue(battle._advance_projectiles(10.6))
        self.assertEqual('impact', battle.client.resolutions[0][0][3])

    def test_destructible_receipt_retries_with_same_progress_until_ack(self):
        battle, bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        receipt = {
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 3, 'x': 1.0, 'y': 0.5, 'z': 0.0,
            'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
        }
        battle._projectile_destructible_context = 'player:7:1'
        try:
            self.assertTrue(battle._report_destructible(receipt))
        finally:
            battle._projectile_destructible_context = None

        bigworld.now = 0.1
        battle._projectiles.advance(
            0.1, lambda *_unused: None, lambda *_unused: None)
        self.assertTrue(battle._publish_projectile_progress())
        first = battle.client.progress[-1][1][0]
        self.assertEqual([receipt], first['destructibles'])
        meta = battle._projectile_meta['player:7:1']
        self.assertEqual([], meta['destructibles_pending'])
        self.assertEqual(first, meta['progress_pending'])

        # Enqueue success is not an acknowledgement. A lost socket write or
        # authority handoff must retry the exact same cursor and receipt.
        self.assertTrue(battle._publish_projectile_progress())
        self.assertEqual(first, battle.client.progress[-1][1][0])

        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': first['checked_through_ms'],
            'checked_distance': first['checked_distance'],
            'piercing_loss': first['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))
        self.assertIsNone(meta['progress_pending'])

    def test_scene_carries_loss_and_uses_total_distance_for_falloff(self):
        battle, unused_bigworld = _battle()

        class _Destructibles(object):
            def __init__(self):
                self.calls = 0

            def shot_world_distance(self, *unused_args):
                self.calls += 1
                if self.calls == 1:
                    return {
                        'piercing_loss': 5.0,
                        'loss_distance': 0.5,
                        'continue_from': 1.0,
                    }
                return {
                    'piercing_loss': 0.0,
                    'world_distance': 999999.0,
                }

        battle._destructibles = _Destructibles()
        start = _Vector((0.0, 0.0, 0.0))
        end = _Vector((2.0, 0.0, 0.0))
        direction = _Vector((1.0, 0.0, 0.0))
        with mock.patch.object(
                combat_rules, 'sampled_piercing', return_value=100.0) as sampled:
            scene = battle._resolve_shot_scene(
                start, end, direction, {}, penetration_factor=1.0,
                initial_piercing_loss=2.0, distance_offset=10.0)

        self.assertEqual(7.0, scene['piercing_loss'])
        self.assertEqual(10.5, sampled.call_args[0][1])
        self.assertEqual(7.0, sampled.call_args[0][3])

    def test_nonimpact_resolution_sends_no_impact_position(self):
        battle, unused_bigworld = _battle()
        meta = battle._projectile_wire_meta(_event())
        meta['pending_resolution'] = {
            'state': {'elapsed': 10.0, 'distance': 100.0},
            'outcome': 'miss', 'impact': (100.0, 0.0, 0.0),
            'direct': None, 'splash': [],
        }
        battle._projectile_meta[meta['projectile_id']] = meta

        self.assertTrue(battle._submit_projectile_resolution(meta))
        args, unused_kwargs = battle.client.resolutions[0]
        self.assertEqual('miss', args[3])
        self.assertIsNone(args[5])
        self.assertIsNotNone(meta['pending_resolution'])
        self.assertTrue(meta['awaiting_resolution'])

    def test_terminal_retries_exactly_until_snapshot_acknowledges_it(self):
        battle, unused_bigworld = _battle()
        meta = battle._projectile_wire_meta(_event())
        meta['destructibles_pending'] = [{
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 3, 'x': 1.0, 'y': 0.5, 'z': 0.0,
            'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
        }]
        meta['pending_resolution'] = {
            'state': {'elapsed': 0.5, 'distance': 5.0},
            'outcome': 'impact', 'impact': (5.0, 1.0, 0.0),
            'direct': None, 'splash': [], 'hit_vehicle': False,
            'wreck_hit': None,
        }
        battle._projectile_meta[meta['projectile_id']] = meta
        battle._ensure_projectile_visual = lambda unused_meta, unused_now: True

        self.assertTrue(battle._submit_projectile_resolution(meta))
        first = battle.client.resolutions[0]
        self.assertTrue(meta['awaiting_resolution'])
        self.assertIsNotNone(meta['pending_resolution'])

        snapshot_row = dict(_event())
        snapshot_row['max_distance'] = snapshot_row.pop('maxDistance')
        snapshot_row.pop('kind')
        snapshot_row.pop('attacker')
        snapshot_row.update({
            'checked_through_ms': 0, 'checked_distance': 0.0,
            'piercing_loss': 0.0,
        })
        self.assertTrue(battle._reconcile_projectile_snapshot({
            'projectiles': [snapshot_row]}))
        self.assertEqual(2, len(battle.client.resolutions))
        self.assertEqual(first, battle.client.resolutions[1])
        self.assertIsNotNone(meta['pending_resolution'])

        self.assertTrue(battle._reconcile_projectile_snapshot({
            'projectiles': []}))
        self.assertNotIn('player:7:1', battle._projectile_meta)

    def test_human_record_normalizes_to_player_wire_kind(self):
        battle, unused_bigworld = _battle()
        effect = battle._projectile_effect(
            {'kind': 'human', 'network_id': 9, 'state': {}},
            12, 2, (1.0, 2.0, 3.0), None, 12, None)
        self.assertEqual('player', effect['target_kind'])

    def test_terminal_event_retires_server_expired_projectile(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        self.assertTrue(battle._projectiles.contains('player:7:1'))

        event = {
            'kind': 'projectile_impact', 'projectile_id': 'player:7:1'}
        battle._prepare_ordered_event(event)
        self.assertTrue(battle._apply_projectile_terminal_event(event))
        self.assertFalse(battle._projectiles.contains('player:7:1'))
        self.assertNotIn('player:7:1', battle._projectile_meta)

    def test_progress_holds_exact_cas_until_snapshot_acknowledges_it(self):
        battle, bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))

        bigworld.now = 0.1
        battle._advance_projectiles(0.1)
        first = battle.client.progress[-1][1][0]
        self.assertEqual(0, first['base_checked_ms'])
        self.assertEqual(100, first['checked_through_ms'])

        bigworld.now = 0.2
        battle._advance_projectiles(0.2)
        second = battle.client.progress[-1][1][0]
        self.assertEqual(first, second)

        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': 100,
            'checked_distance': first['checked_distance'],
            'piercing_loss': first['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))
        bigworld.now = 0.31
        battle._advance_projectiles(0.31)
        third = battle.client.progress[-1][1][0]
        self.assertEqual(100, third['base_checked_ms'])
        self.assertEqual(310, third['checked_through_ms'])

    def test_projectile_still_resolves_after_shooter_dies(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        self.assertTrue(battle._accept_projectile_event(_event()))
        source.isAlive = lambda: False
        bigworld.wall_x = 5.0
        battle._next_projectile_progress_time = 99.0

        bigworld.now = 0.6
        battle._advance_projectiles(0.6)
        self.assertEqual(1, len(battle.client.resolutions))
        self.assertEqual('impact', battle.client.resolutions[0][0][3])

    def test_launch_shot_stays_frozen_while_active_selection_changes(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        first = source.typeDescriptor.gun.shots[0]
        second = types.SimpleNamespace(shell='second', maxDistance=50.0)
        source.typeDescriptor.gun.shots.append(second)
        source.typeDescriptor.activeGunShotIndex = 0
        self.assertTrue(battle._accept_projectile_event(_event()))
        source.typeDescriptor.activeGunShotIndex = 1

        meta = battle._projectile_meta['player:7:1']
        resolved = battle._projectile_shot(meta)
        self.assertIsNot(first, resolved)
        self.assertEqual([390.0, 150.0], resolved['shell']['damage'])

    def test_human_105mm_damage_overrides_remote_stock_75mm_descriptor(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        self.assertEqual(
            (135.0, 100.0), source.typeDescriptor.gun.shots[0].shell.damage)
        target = types.SimpleNamespace(
            id=55, isStarted=True, typeDescriptor=types.SimpleNamespace(),
            position=_Vector((10.0, 0.0, 0.0)), isAlive=lambda: True,
            collideSegmentExt=mock.Mock())
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        meta = battle._projectile_wire_meta(_event())
        collision = types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0, matInfo=object(), compName='hull')
        target.collideSegmentExt.return_value = (collision,)
        terminal = {
            'target_key': 'bot:17',
            'collisions': [collision],
            'query': (_Vector((0.0, 1.0, 0.0)),
                      _Vector((10.0, 1.0, 0.0))),
            'impact': (10.0, 1.0, 0.0),
            'piercing_loss': 0.0,
            'penetration_factor': 1.0,
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact',
                return_value={'result': 2}), \
                mock.patch.object(
                    combat_rules, 'he_nominal_armor', return_value=100.0), \
                mock.patch.object(
                    combat_rules.random, 'uniform',
                    side_effect=lambda low, high: (low + high) / 2.0), \
                mock.patch.object(
                    critical_damage, 'propose_direct',
                    side_effect=lambda unused_target, unused_collisions,
                    unused_start, unused_end, damage, unused_shell,
                    unused_attacker, **unused_kwargs: (
                        damage, None, None)) as critical:
            effect = battle._projectile_direct_effect(
                meta, {'start': (0.0, 1.0, 0.0), 'distance': 10.0},
                terminal)

        self.assertEqual(390, effect['damage'])
        self.assertEqual(2, effect['shot_result'])
        self.assertEqual(
            [390.0, 150.0], critical.call_args.args[5]['damage'])
        self.assertFalse(critical.call_args.kwargs['deadeye'])

    def test_ricochet_contact_keeps_world_normal_and_has_no_critical(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=55, isStarted=True, typeDescriptor=types.SimpleNamespace(),
            position=_Vector((10.0, 0.0, 0.0)), isAlive=lambda: True)
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        meta = battle._projectile_wire_meta(_event())
        meta['base_penetration_multiplier'] = 0.75
        collision = types.SimpleNamespace(
            dist=10.0, hitAngleCos=0.1, matInfo=object(), compName='hull')
        evidence = types.SimpleNamespace(
            collision=collision, worldNormal=_Vector((1.0, 0.0, 0.0)))
        terminal = {
            'target_key': 'bot:17', 'collisions': [collision],
            'collision_evidence': [evidence],
            'query': (_Vector((0.0, 1.0, 0.0)),
                      _Vector((12.0, 1.0, 0.0))),
            'impact': (10.0, 1.0, 0.0),
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact', return_value={
                    'result': 0, 'component': 'hull', 'distance': 10.0,
                }) as resolved, mock.patch.object(
                    combat_rules, 'he_nominal_armor', return_value=100.0), \
                mock.patch.object(
                    critical_damage, 'propose_direct') as critical:
            effect = battle._projectile_direct_effect(meta, {
                'start': (0.0, 1.0, 0.0),
                'payload': {'range_origin': (0.0, 0.0, 0.0)},
                'distance': 10.0,
            }, terminal)

        self.assertEqual(0, effect['damage'])
        self.assertEqual(0, effect['shot_result'])
        self.assertEqual((1.0, 0.0, 0.0), terminal['world_normal'])
        self.assertEqual(
            0.75,
            resolved.call_args.kwargs['base_penetration_multiplier'])
        critical.assert_not_called()

    def test_ap_direct_hit_requeries_the_full_late_hit_trace_budget(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        first = types.SimpleNamespace(
            dist=9.80, hitAngleCos=1.0, matInfo=object(), compName='hull')
        inside = types.SimpleNamespace(
            dist=10.79, hitAngleCos=1.0, matInfo=object(), compName='inside')
        outside = types.SimpleNamespace(
            dist=10.81, hitAngleCos=1.0, matInfo=object(), compName='outside')
        target = types.SimpleNamespace(
            id=55, isStarted=True, typeDescriptor=types.SimpleNamespace(),
            position=_Vector((10.0, 0.0, 0.0)), isAlive=lambda: True,
            collideSegmentExt=mock.Mock(
                return_value=(first, inside, outside)))
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        event = _event()
        event['source_shot']['shell']['caliber'] = 100.0
        meta = battle._projectile_wire_meta(event)
        terminal = {
            'target_key': 'bot:17', 'collisions': [first],
            'query': (_Vector(), _Vector((10.0, 0.0, 0.0))),
            'impact': (9.8, 0.0, 0.0),
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact',
                return_value={'result': 2}), \
                mock.patch.object(
                    combat_rules, 'he_nominal_armor', return_value=100.0), \
                mock.patch.object(
                    combat_rules, 'damage', return_value=390), \
                mock.patch.object(
                    critical_damage, 'propose_direct',
                    return_value=(390, None, None)) as critical:
            effect = battle._projectile_direct_effect(
                meta, {'start': (0.0, 0.0, 0.0), 'distance': 9.8},
                terminal)

        self.assertEqual(390, effect['damage'])
        self.assertAlmostEqual(
            10.8, target.collideSegmentExt.call_args.args[1].x)
        self.assertEqual(2, len(critical.call_args.args[1]))
        self.assertAlmostEqual(10.8, critical.call_args.args[3].x)

    def test_direct_hit_uses_same_frozen_target_for_armor_and_modules(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        live_descriptor = types.SimpleNamespace()
        frozen_descriptor = types.SimpleNamespace()
        target = types.SimpleNamespace(
            id=55, isStarted=True, typeDescriptor=live_descriptor,
            position=_Vector((10.0, 0.0, 0.0)), isAlive=lambda: True)
        frozen_target = types.SimpleNamespace(
            id=55, typeDescriptor=frozen_descriptor)
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        battle._projectile_frozen_target = mock.Mock(
            return_value=frozen_target)
        meta = battle._projectile_wire_meta(_event())
        collision = types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0, matInfo=object(), compName='hull')
        collision_pose = battle._projectile_plain_pose((10.0, 0.0, 0.0))
        terminal = {
            'target_key': 'bot:17', 'collisions': [collision],
            'query': (_Vector(), _Vector((10.0, 0.0, 0.0))),
            'impact': (5.0, 0.0, 0.0),
            'collision_pose': collision_pose,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact', return_value={
                    'result': 2, 'distance': 5.0}), \
                mock.patch.object(
                    combat_rules, 'he_nominal_armor',
                    return_value=100.0) as nominal, \
                mock.patch.object(
                    combat_rules, 'damage', return_value=390), \
                mock.patch.object(
                    critical_damage, 'propose_direct',
                    return_value=(390, None, None)) as critical:
            effect = battle._projectile_direct_effect(
                meta, {'start': (0.0, 0.0, 0.0), 'distance': 5.0},
                terminal)

        self.assertEqual(390, effect['damage'])
        battle._projectile_frozen_target.assert_called_once_with(
            target, collision_pose)
        self.assertIs(frozen_descriptor, nominal.call_args.args[1])
        self.assertIs(frozen_target, critical.call_args.args[0])

    def test_he_direct_hit_uses_the_finite_explosion_cone(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        target = types.SimpleNamespace(
            id=55, isStarted=True, typeDescriptor=types.SimpleNamespace(),
            position=_Vector((10.0, 0.0, 0.0)), isAlive=lambda: True)
        battle._records['bot:17'] = {
            'engine_id': 55, 'network_id': 17, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        event = _event()
        event['source_shot']['deadeye'] = True
        event['source_shot']['shell'].update({
            'kind': 'HIGH_EXPLOSIVE', 'caliber': 100.0,
            'explosionRadius': 4.0,
        })
        event['is_he'] = True
        event['splash_radius'] = 4.0
        meta = battle._projectile_wire_meta(event)
        collision = types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0, matInfo=object(), compName='hull')
        terminal = {
            'target_key': 'bot:17', 'collisions': [collision],
            'query': (_Vector((0.0, 1.0, 0.0)),
                      _Vector((10.0, 1.0, 0.0))),
            # Visual impact stays on the actual projectile path. The critical
            # cone must instead start at the collision point in query space.
            'impact': (11.0, 1.0, 0.0),
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact',
                return_value={'result': 1}), \
                mock.patch.object(
                    combat_rules, 'he_nominal_armor', return_value=100.0), \
                mock.patch.object(
                    combat_rules, 'damage', return_value=200), \
                mock.patch.object(
                    critical_damage, 'propose_direct',
                    side_effect=AssertionError(
                        'HE must not use the solid internal ray')), \
                mock.patch.object(
                    critical_damage, 'propose_explosion',
                    side_effect=lambda unused_target, unused_collisions,
                    unused_burst, unused_direction, damage, unused_shell,
                    unused_attacker, **unused_kwargs: (
                        damage, None, None)) as cone:
            effect = battle._projectile_direct_effect(
                meta, {'start': (0.0, 1.0, 0.0), 'distance': 10.0},
                terminal)

        self.assertEqual(200, effect['damage'])
        self.assertEqual(1, effect['shot_result'])
        self.assertEqual(11.0, effect['x'])
        self.assertEqual((10.0, 1.0, 0.0), tuple(
            cone.call_args.args[2][index] for index in range(3)))
        self.assertGreater(cone.call_args.args[3].length, 0.0)
        self.assertTrue(cone.call_args.kwargs['deadeye'])


if __name__ == '__main__':
    unittest.main()
