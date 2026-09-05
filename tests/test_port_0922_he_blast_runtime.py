import math
from pathlib import Path
import random
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import battle_runtime as battle_runtime_module  # noqa: E402
from gui.mods.offline_lan_0922 import combat_rules, critical_damage  # noqa: E402
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime  # noqa: E402


def _xyz(value):
    return tuple(float(value[index]) for index in range(3))


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

    def __neg__(self):
        return self.scale(-1.0)

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


class _Matrix(object):

    def __init__(self, other=None):
        self.translation = _Vector(getattr(other, 'translation', _Vector()))
        self.yaw = getattr(other, 'yaw', 0.0)
        self.pitch = getattr(other, 'pitch', 0.0)
        self.roll = getattr(other, 'roll', 0.0)

    def setIdentity(self):
        self.translation = _Vector()
        self.yaw = self.pitch = self.roll = 0.0

    def setRotateYPR(self, values):
        self.yaw, self.pitch, self.roll = map(float, values)

    def setRotateY(self, value):
        self.yaw = float(value)

    def setRotateX(self, value):
        self.pitch = float(value)

    def setTranslate(self, value):
        self.translation = _Vector(value)

    def postMultiply(self, unused_other):
        return None

    def preMultiply(self, unused_other):
        return None

    def invert(self):
        self.translation = self.translation.scale(-1.0)

    def applyPoint(self, value):
        return _Vector(value) + self.translation


class _BigWorld(object):

    def __init__(self):
        self.wall_x = None
        self.calls = []

    def wg_collideSegment(self, space_id, start, end, mask,
                          collision_filter=None):
        self.calls.append((space_id, start, end, mask, collision_filter))
        if self.wall_x is None:
            return None
        if abs(end.x - start.x) <= 1.0e-9:
            return None
        low = min(start.x, end.x)
        high = max(start.x, end.x)
        if not low <= self.wall_x <= high:
            return None
        fraction = (self.wall_x - start.x) / (end.x - start.x)
        return (_Vector((
            self.wall_x,
            start.y + (end.y - start.y) * fraction,
            start.z + (end.z - start.z) * fraction)), None)


class _Material(object):

    def __init__(self, armor, factor):
        self.armor = float(armor)
        self.vehicleDamageFactor = float(factor)
        self.useAntifragmentationLining = True


def _collision(distance, armor, factor=1.0, component='vehicleHull'):
    return types.SimpleNamespace(
        dist=float(distance), hitAngleCos=1.0,
        matInfo=_Material(armor, factor), compName=component)


def _descriptor():
    empty_tester = types.SimpleNamespace(
        bbox=(_Vector((-1.0, -1.0, -1.0)),
              _Vector((1.0, 1.0, 1.0)), None),
        localHitTest=lambda unused_start, unused_end: ())
    chassis = types.SimpleNamespace(
        itemTypeName='vehicleChassis', hitTester=empty_tester,
        materials={}, hullPosition=_Vector())
    hull = types.SimpleNamespace(
        itemTypeName='vehicleHull', hitTester=empty_tester,
        materials={1: _Material(20.0, 1.0)},
        turretPositions=(_Vector(),))
    turret = types.SimpleNamespace(
        itemTypeName='vehicleTurret', hitTester=empty_tester,
        materials={1: _Material(30.0, 1.0)}, gunPosition=_Vector())
    gun = types.SimpleNamespace(
        itemTypeName='vehicleGun', hitTester=empty_tester, materials={})
    return types.SimpleNamespace(
        chassis=chassis, hull=hull, turret=turret, gun=gun,
        miscAttrs={}, type=types.SimpleNamespace(crewRoles=()))


def _target(entity_id=55, position=(0.0, 0.0, 0.0)):
    descriptor = _descriptor()
    matrix = _Matrix()
    matrix.translation = _Vector(position)
    return types.SimpleNamespace(
        id=entity_id, isStarted=True, typeDescriptor=descriptor,
        position=_Vector(position), matrix=matrix,
        appearance=types.SimpleNamespace(
            turretMatrix=_Matrix(), gunMatrix=_Matrix()),
        isAlive=lambda: True, health=1000,
        devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
        is_on_fire=False,
        getComponents=lambda: ())


def _shot(kind='HIGH_EXPLOSIVE', radius=10.0, damage=1000.0):
    return {
        'maxDistance': 720.0,
        'piercingPower': (60.0, 60.0),
        'shell': {
            'kind': kind, 'caliber': 122.0,
            'damage': (float(damage), 150.0),
            'explosionRadius': float(radius),
        },
    }


def _runtime():
    bigworld = _BigWorld()
    runtime = types.SimpleNamespace(
        bigworld=bigworld,
        math=types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
    battle = BattleRuntime(runtime)
    battle._avatar = types.SimpleNamespace(spaceID=1)
    battle._destructibles = None
    battle._worker_mode = True
    return battle, bigworld


def _record(network_id=17, native_remote=True):
    return {
        'engine_id': 55, 'network_id': network_id, 'kind': 'bot',
        'local': False, 'native_remote': bool(native_remote), 'ready': True,
        'state': {'health': 1000, 'alive': True},
    }


def _meta(shot=None):
    return {
        'projectile_id': 'player:7:1',
        'shooter_kind': 'player', 'shooter_id': 7,
        'source_shot': shot or _shot(),
        'presentation_offsets': {},
        'base_penetration_multiplier': 1.0,
    }


class HEBlastSurfaceRuntimeTests(unittest.TestCase):

    def test_surface_search_selects_best_visible_real_hit_after_blocked_best(self):
        battle, unused_bigworld = _runtime()
        target = _target()
        record = _record()
        body_matrix = object()
        battle._projectile_vehicle_matrices = mock.Mock(
            return_value=(body_matrix, body_matrix))
        thin = _collision(4.001, 20.0)
        thick = _collision(2.001, 100.0)

        def collide(unused_target, unused_matrix, unused_start, end,
                    unused_math, chassis_matrix=None):
            self.assertIs(body_matrix, chassis_matrix)
            return (thin,) if end.x > 9.0 else (thick,)

        visible = mock.Mock(side_effect=lambda unused_start, point,
                                               unused_burst: point[0] < 1.0)
        with mock.patch.object(
                battle_runtime_module,
                'vehicle_blast_probe_points_at_matrix',
                return_value=(_Vector((1.0, 0.0, 0.0)),
                              _Vector((0.0, 1.0, 0.0)))), \
                mock.patch.object(
                    battle_runtime_module, 'collide_vehicle_at_matrix',
                    side_effect=collide) as native_collide, \
                mock.patch.object(
                    battle, '_projectile_he_world_visible', visible), \
                mock.patch.object(
                    random, 'gauss',
                    side_effect=AssertionError('surface search must not roll')):
            contact = battle._projectile_he_blast_contact(
                _meta(), record, target, _shot(), (0.0, 0.0, 0.0),
                1000.0, state={})

        self.assertIs(thick, contact['collision'])
        self.assertEqual(100.0, contact['nominal_armor'])
        self.assertEqual((thick,), contact['collisions'])
        self.assertEqual(2, native_collide.call_count)
        self.assertEqual(2, visible.call_count)
        self.assertGreater(visible.call_args_list[0].args[1][0], 3.9)
        self.assertLess(visible.call_args_list[1].args[1][0], 0.01)

    def test_surface_search_keeps_screen_prefix_and_never_fabricates_hull_armor(self):
        battle, unused_bigworld = _runtime()
        target = _target()
        record = _record()
        screen = _collision(1.001, 40.0, factor=0.0,
                            component='vehicleChassis')
        hull = _collision(2.001, 35.0)
        seed = (_Vector((-0.001, 0.0, 0.0)),
                _Vector((10.0, 0.0, 0.0)), (screen, hull))

        with mock.patch.object(
                battle_runtime_module,
                'vehicle_blast_probe_points_at_matrix', return_value=()), \
                mock.patch.object(
                    battle, '_projectile_he_world_visible', return_value=True):
            contact = battle._projectile_he_blast_contact(
                _meta(), record, target, _shot(), (0.0, 0.0, 0.0),
                1000.0, state={}, seed=seed)

        self.assertIs(hull, contact['collision'])
        self.assertEqual((screen, hull), contact['collisions'])
        self.assertAlmostEqual(2.0, contact['distance'], places=6)

        with mock.patch.object(
                battle_runtime_module,
                'vehicle_blast_probe_points_at_matrix', return_value=()):
            missing = battle._projectile_he_blast_contact(
                _meta(), record, target, _shot(), (0.0, 0.0, 0.0),
                1000.0, state={}, seed=(seed[0], seed[1], (screen,)))

        self.assertIsNone(missing)

    def test_native_surface_inside_radius_is_hit_when_vehicle_origin_is_outside(self):
        battle, bigworld = _runtime()
        target = _target(position=(5.0, 0.0, 0.0))
        hull = target.typeDescriptor.hull
        hull.materials = {1: _Material(20.0, 1.0)}

        def plane_hit(start, end):
            delta = end - start
            if abs(delta.x) < 1.0e-9:
                return ()
            fraction = (-2.5 - start.x) / delta.x
            if not 0.0 <= fraction <= 1.0:
                return ()
            point = start + delta.scale(fraction)
            if abs(point.y) > 1.0 or abs(point.z) > 1.0:
                return ()
            return ((delta.length * fraction, None, 1.0, 1),)

        hull.hitTester = types.SimpleNamespace(
            bbox=(_Vector((-2.5, -1.0, -1.0)),
                  _Vector((2.5, 1.0, 1.0)), None),
            localHitTest=plane_hit)
        shot = _shot(radius=3.0, damage=390.0)
        record = _record(native_remote=False)

        # Exercise the bbox directions, component adapter, HE law and scenery
        # query together. Only the native localHitTest plane itself is a fake.
        contact = battle._projectile_he_blast_contact(
            _meta(shot), record, target, shot, (0.0, 0.0, 0.0), 390.0)

        self.assertIsNotNone(contact)
        self.assertAlmostEqual(2.5, contact['distance'])
        self.assertGreater(contact['damage'], 0)
        bigworld.wall_x = 2.0
        blocked = battle._projectile_he_blast_contact(
            _meta(shot), record, target, shot, (0.0, 0.0, 0.0), 390.0)
        self.assertIsNone(blocked)
        bigworld.wall_x = None
        hull.hitTester.localHitTest = lambda unused_start, unused_end: ()
        missing = battle._projectile_he_blast_contact(
            _meta(shot), record, target, shot, (0.0, 0.0, 0.0), 390.0)
        self.assertIsNone(missing)

    def test_scene_origin_stays_on_incoming_side_and_real_wall_blocks(self):
        battle, bigworld = _runtime()
        state = {
            'elapsed': 0.5,
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
        }
        burst = _Vector((5.0, 1.0, 0.0))

        scene_start = battle._projectile_he_scene_origin(burst, state)

        self.assertLess(scene_start.x, burst.x)
        self.assertAlmostEqual(0.002, burst.x - scene_start.x, places=9)
        bigworld.wall_x = 6.0
        self.assertFalse(battle._projectile_he_world_visible(
            scene_start, (8.0, 1.0, 0.0), burst))
        bigworld.wall_x = None
        self.assertTrue(battle._projectile_he_world_visible(
            scene_start, (8.0, 1.0, 0.0), burst))
        calls = len(bigworld.calls)
        self.assertTrue(battle._projectile_he_world_visible(
            scene_start, (5.0005, 1.0, 0.0), burst))
        self.assertEqual(calls, len(bigworld.calls))


class HEBlastEffectRuntimeTests(unittest.TestCase):

    def _direct_fixture(self, kind='HIGH_EXPLOSIVE'):
        battle, unused_bigworld = _runtime()
        shot = _shot(kind=kind, damage=400.0)
        meta = _meta(shot)
        source = types.SimpleNamespace(id=41, typeDescriptor=types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[])))
        target = _target()
        record = _record()
        battle._records = {
            'player:7': {
                'engine_id': 41, 'network_id': 7, 'kind': 'player',
                'local': False, 'ready': True,
                'state': {'health': 1000, 'alive': True}},
            'bot:17': record,
        }
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        collision = _collision(5.0, 120.0)
        terminal = {
            'target_key': 'bot:17', 'collisions': (collision,),
            'collision_evidence': (),
            'query': (_Vector((0.0, 1.0, 0.0)),
                      _Vector((8.0, 1.0, 0.0))),
            'impact': (5.0, 1.0, 0.0),
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
        }
        state = {
            'start': (0.0, 1.0, 0.0),
            'payload': {'range_origin': (0.0, 0.0, 0.0)},
            'distance': 5.0, 'elapsed': 0.5,
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
        }
        battle._install_critical_equipment_effects = mock.Mock()
        battle._projectile_damage_sticker = mock.Mock(return_value=None)
        return battle, meta, target, collision, terminal, state

    def test_nonpenetrating_direct_he_uses_blast_contact_geometry(self):
        (battle, meta, target, collision,
         terminal, state) = self._direct_fixture()
        screen = _collision(0.5, 20.0, factor=0.0,
                            component='vehicleChassis')
        weak_hull = _collision(1.5, 25.0)
        blast = {
            'damage': 240, 'nominal_armor': 25.0, 'distance': 1.5,
            'point': (6.5, 1.0, 0.0), 'direction': (1.0, 0.0, 0.0),
            'collision': weak_hull, 'collisions': (screen, weak_hull),
        }
        contact = {
            'result': 1, 'layer': 'structural', 'distance': 5.0,
            'component': 'vehicleHull',
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact', return_value=contact), \
                mock.patch.object(random, 'gauss', return_value=400.0), \
                mock.patch.object(
                    battle, '_projectile_he_blast_contact',
                    return_value=blast) as blast_contact, \
                mock.patch.object(
                    critical_damage, 'propose_explosion',
                    return_value=(240, None, {})) as critical:
            effect = battle._projectile_direct_effect(meta, state, terminal)

        self.assertEqual(240, effect['damage'])
        self.assertEqual(1, effect['shot_result'])
        self.assertEqual(400, effect['potential_damage'])
        self.assertIs(True, effect['structural_armor_hit'])
        args = blast_contact.call_args.args
        self.assertIs(target, args[2])
        self.assertEqual(400.0, args[5])
        self.assertIs(state, blast_contact.call_args.kwargs['state'])
        seed = blast_contact.call_args.kwargs['seed']
        self.assertEqual((collision,), seed[2])
        self.assertEqual(
            combat_rules.collision_layers((screen, weak_hull)),
            critical.call_args.args[1])
        self.assertEqual((5.0, 1.0, 0.0),
                         _xyz(critical.call_args.args[2]))
        self.assertEqual((1.0, 0.0, 0.0),
                         _xyz(critical.call_args.args[3]))
        self.assertIs(True, critical.call_args.kwargs['allow_interior'])

    def test_penetrating_he_keeps_full_direct_damage_without_surface_search(self):
        battle, meta, unused_target, unused_collision, terminal, state = \
            self._direct_fixture()
        contact = {
            'result': 2, 'layer': 'structural', 'distance': 5.0,
            'component': 'vehicleHull',
        }

        with mock.patch.object(
                combat_rules, 'resolve_armor_contact', return_value=contact), \
                mock.patch.object(random, 'gauss', return_value=400.0), \
                mock.patch.object(
                    battle, '_projectile_he_blast_contact',
                    side_effect=AssertionError(
                        'penetrating HE must keep the direct path')), \
                mock.patch.object(
                    critical_damage, 'propose_explosion',
                    return_value=(400, None, {})):
            effect = battle._projectile_direct_effect(meta, state, terminal)

        self.assertEqual(400, effect['damage'])
        self.assertEqual(2, effect['shot_result'])
        self.assertEqual(400, effect['potential_damage'])

    def test_nonpenetrating_he_without_proved_structural_surface_is_zero_damage(self):
        battle, meta, unused_target, collision, terminal, state = \
            self._direct_fixture()
        contact = {
            'result': 1, 'layer': 'external', 'distance': 5.0,
            'component': 'vehicleChassis',
        }
        bigworld_module = types.SimpleNamespace(
            player=lambda: types.SimpleNamespace(playerVehicleID=-1))
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.dict(
                sys.modules,
                {'BigWorld': bigworld_module, 'Math': math_module}), \
                mock.patch.object(
                combat_rules, 'resolve_armor_contact', return_value=contact), \
                mock.patch.object(random, 'gauss', return_value=400.0), \
                mock.patch.object(
                    battle, '_projectile_he_blast_contact', return_value=None), \
                mock.patch.object(
                    critical_damage, '_offh_internal_cone_hits') as cone:
            effect = battle._projectile_direct_effect(meta, state, terminal)

        self.assertIsNotNone(effect)
        self.assertEqual(0, effect['damage'])
        self.assertEqual(1, effect['shot_result'])
        self.assertIs(False, effect['structural_armor_hit'])
        cone.assert_not_called()

    def test_splash_uses_one_roll_and_the_collision_time_target_pose(self):
        battle, unused_bigworld = _runtime()
        shot = _shot(radius=4.0, damage=800.0)
        meta = _meta(shot)
        meta['presentation_offsets'] = {'bot:17': 0.25}
        source = types.SimpleNamespace(id=41, typeDescriptor=types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[])))
        # The live origin is far outside the radius. Only the historical native
        # surface chosen by the worker is eligible.
        target = _target(position=(100.0, 0.0, 0.0))
        record = _record()
        battle._records = {
            'player:7': {
                'engine_id': 41, 'network_id': 7, 'kind': 'player',
                'local': False, 'ready': True,
                'state': {'health': 1000, 'alive': True}},
            'bot:17': record,
        }
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        pose = battle._projectile_plain_pose((3.5, 0.0, 0.0))
        frozen = _target(position=(3.5, 0.0, 0.0))
        battle._projectile_historic_pose = mock.Mock(return_value=pose)
        battle._projectile_frozen_target = mock.Mock(return_value=frozen)
        battle._install_critical_equipment_effects = mock.Mock()
        structural = _collision(3.5, 20.0)
        blast = {
            'damage': 120, 'nominal_armor': 20.0, 'distance': 3.5,
            'point': (3.5, 0.0, 0.0), 'direction': (1.0, 0.0, 0.0),
            'collision': structural, 'collisions': (structural,),
        }
        state = {
            'cursor_time': 2.0, 'elapsed': 2.0,
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
        }

        with mock.patch.object(random, 'gauss', return_value=640.0) as roll, \
                mock.patch.object(
                    battle, '_projectile_he_blast_contact',
                    return_value=blast) as blast_contact, \
                mock.patch.object(
                    critical_damage, 'propose_explosion',
                    return_value=(120, None, {})) as critical:
            effects = battle._projectile_splash_effects(
                meta, (0.0, 0.0, 0.0), None, state=state)

        self.assertEqual(1, roll.call_count)
        self.assertEqual(640.0, blast_contact.call_args.args[5])
        self.assertIs(state, blast_contact.call_args.kwargs['state'])
        self.assertEqual(pose, blast_contact.call_args.kwargs['pose'])
        battle._projectile_historic_pose.assert_called_once_with(
            'bot:17', 1.75)
        battle._projectile_frozen_target.assert_called_with(target, pose)
        self.assertIs(frozen, critical.call_args.args[0])
        self.assertEqual(
            combat_rules.collision_layers((structural,)),
            critical.call_args.args[1])
        self.assertEqual((0.0, 0.0, 0.0),
                         _xyz(critical.call_args.args[2]))
        self.assertEqual((1.0, 0.0, 0.0),
                         _xyz(critical.call_args.args[3]))
        self.assertEqual(120, effects[0]['damage'])
        self.assertEqual(3.5, effects[0]['target_x'])
        self.assertEqual(0.0, effects[0]['target_y'])
        self.assertEqual(0.0, effects[0]['target_z'])

    def test_splash_missing_collision_time_pose_skips_only_that_target(self):
        battle, unused_bigworld = _runtime()
        shot = _shot(radius=4.0)
        meta = _meta(shot)
        source = types.SimpleNamespace(id=41, typeDescriptor=types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[])))
        target = _target(position=(1.0, 0.0, 0.0))
        battle._records = {
            'player:7': {
                'engine_id': 41, 'network_id': 7, 'kind': 'player',
                'local': False, 'ready': True,
                'state': {'health': 1000, 'alive': True}},
            'bot:17': _record(),
        }
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 55 else None)
        battle._projectile_historic_pose = mock.Mock(return_value=None)
        state = {
            'cursor_time': 2.0, 'elapsed': 2.0,
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
        }

        with mock.patch.object(random, 'gauss') as roll, \
                mock.patch.object(
                    battle, '_projectile_he_blast_contact') as blast_contact:
            effects = battle._projectile_splash_effects(
                meta, (0.0, 0.0, 0.0), None, state=state)

        self.assertEqual([], effects)
        roll.assert_not_called()
        blast_contact.assert_not_called()

    def test_moving_target_terminal_shares_combat_burst_and_keeps_visual_impact(self):
        battle, meta, unused_target, collision, data, state = self._direct_fixture()
        meta.update({'is_he': True, 'origin': (0.0, 1.0, 0.0),
                     'max_time_ms': 1000, 'max_distance': 100.0})
        data['impact'] = (6.0, 1.0, 0.0)
        state.update({'key': (meta['projectile_id'], 0),
                      'position': data['impact']})
        battle._projectile_meta = {meta['projectile_id']: meta}
        battle._projectile_terminal_data = {meta['projectile_id']: data}
        battle._submit_projectile_resolution = mock.Mock(return_value=True)
        blast = {'damage': 120, 'collisions': (collision,),
                 'direction': (1.0, 0.0, 0.0)}
        with mock.patch.object(
                battle, '_projectile_he_blast_contact',
                return_value=blast) as direct_blast, \
                mock.patch.object(
                    battle, '_projectile_splash_effects',
                    return_value=[]) as splash, \
                mock.patch.object(
                    critical_damage, 'propose_explosion',
                    return_value=(120, None, {})):
            self.assertTrue(battle._projectile_terminal_impl(
                state, {'reason': 'impact'}))

        self.assertEqual((5.0, 1.0, 0.0), direct_blast.call_args.args[4])
        self.assertEqual(direct_blast.call_args.args[4],
                         splash.call_args.args[1])
        self.assertEqual((6.0, 1.0, 0.0),
                         splash.call_args.kwargs['visual_impact'])
        self.assertEqual(6.0, meta['pending_resolution']['direct']['x'])
        self.assertEqual((6.0, 1.0, 0.0),
                         meta['pending_resolution']['impact'])

    def test_penetrating_he_terminal_does_not_add_external_splash(self):
        battle, unused_bigworld = _runtime()
        projectile_id = 'player:7:1'
        meta = _meta()
        meta.update({
            'is_he': True, 'origin': (0.0, 0.0, 0.0),
            'max_time_ms': 1000, 'max_distance': 100.0,
        })
        data = {
            'impact': (5.0, 1.0, 0.0), 'target_key': 'bot:17',
        }
        state = {
            'key': (projectile_id, 0), 'position': data['impact'],
            'elapsed': 0.5, 'distance': 5.0,
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
        }
        direct = {'damage': 400, 'shot_result': 2}
        battle._projectile_meta = {projectile_id: meta}
        battle._projectile_terminal_data = {projectile_id: data}
        battle._projectile_direct_effect = mock.Mock(return_value=direct)
        battle._projectile_splash_effects = mock.Mock(
            side_effect=AssertionError(
                'penetrating HE must not burst outside the direct victim'))
        battle._submit_projectile_resolution = mock.Mock(return_value=True)

        result = battle._projectile_terminal_impl(
            state, {'reason': 'impact'})

        self.assertIs(True, result)
        battle._projectile_splash_effects.assert_not_called()
        pending = meta['pending_resolution']
        self.assertIs(direct, pending['direct'])
        self.assertEqual([], pending['splash'])


if __name__ == '__main__':
    unittest.main()
