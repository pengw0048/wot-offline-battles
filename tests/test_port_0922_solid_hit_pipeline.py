"""End-to-end AP/APCR collision checks against an analytic moving plane.

This deliberately reaches BattleRuntime's chord and direct-effect paths.  The
only fake collision primitive is a declared local BSP-plane boundary; its
intersection is checked independently in world space below.
"""

import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
TESTS = ROOT / 'tests'
sys.path.insert(0, str(CLIENT_SCRIPTS))
sys.path.insert(0, str(TESTS))

from gui.mods.offline_lan_0922 import combat_rules, critical_damage, lan_client
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
import test_port_0922_solid_collision_oracle as row_oracle


Vector = row_oracle.Vector
Matrix = row_oracle.Matrix

START = (0.0, 1.0, 0.0)
END = (40.0, 1.0, 0.0)
YAW = 0.63
HULL_OFFSET = (0.9, -0.2, 0.7)
PLANE_POINT = (0.4, 0.35, -0.6)
PLANE_NORMAL = (0.51, -0.22, 0.83)
PENETRATION_FACTOR = 1.19
DAMAGE_ROLL = 333.7


class _PipelineVector(Vector):
    """The production chord also needs the native Vector3 scale helper."""

    def __add__(self, other):
        return _PipelineVector((self.x + other.x, self.y + other.y,
                                self.z + other.z))

    def __sub__(self, other):
        return _PipelineVector((self.x - other.x, self.y - other.y,
                                self.z - other.z))

    def scale(self, value):
        return _PipelineVector((self.x * value, self.y * value,
                                self.z * value))


def _subtract(left, right):
    return tuple(left[index] - right[index] for index in range(3))


def _add(left, right):
    return tuple(left[index] + right[index] for index in range(3))


def _scale(vector, amount):
    return tuple(value * amount for value in vector)


def _dot(left, right):
    return sum(left[index] * right[index] for index in range(3))


def _length(vector):
    return math.sqrt(_dot(vector, vector))


def _normalised(vector):
    length = _length(vector)
    return tuple(value / length for value in vector)


class _PlaneHitTester(object):
    """Analytic stand-in for one native component-local BSP plane."""

    def __init__(self, normal, point, material_kind):
        self.normal = _normalised(normal)
        self.offset = _dot(self.normal, point)
        self.material_kind = material_kind
        self.calls = []

    def localHitTest(self, start, end):
        start = tuple(start)
        end = tuple(end)
        self.calls.append((start, end))
        direction = _subtract(end, start)
        divisor = _dot(self.normal, direction)
        if abs(divisor) <= 1.0e-10:
            return ()
        fraction = (self.offset - _dot(self.normal, start)) / divisor
        if fraction < 0.0 or fraction > 1.0:
            return ()
        return ((fraction * _length(direction), Vector(self.normal),
                 abs(divisor) / _length(direction), self.material_kind),)


class _BigWorld(object):

    def wg_collideSegment(self, unused_space, unused_start, unused_end,
                          unused_mask):
        return None


def _plain_pose(position, yaw):
    return BattleRuntime._projectile_plain_pose(
        position, {'yaw': yaw, 'pitch': 0.0, 'roll': 0.0,
                   'turret_yaw': 0.0, 'gun_pitch': 0.0})


def _world_plane(yaw):
    """Return the plane's root offset and world normal without test Matrix."""
    rotation = row_oracle._oracle_ypr(yaw, 0.0, 0.0)
    inverse = row_oracle._oracle_inverse_rigid(rotation)
    root_point = _add(HULL_OFFSET, PLANE_POINT)
    rotated_point = row_oracle._oracle_point(rotation, root_point)
    local_normal = _normalised(PLANE_NORMAL)
    # For row-vector positions p_world = p_local * R, plane normals use
    # R^-1 * n.  This is intentionally not Matrix.applyVector().
    world_normal = tuple(sum(inverse[row][column] * local_normal[column]
                             for column in range(3)) for row in range(3))
    return rotated_point, _normalised(world_normal)


def _moving_plane_solution(target_start, target_delta, yaw):
    """Solve n·(P0+f*dP-Q0-f*dQ-R*p)=0 for the projectile fraction."""
    plane_offset, normal = _world_plane(yaw)
    projectile_delta = _subtract(END, START)
    relative_origin = _subtract(_subtract(START, target_start), plane_offset)
    relative_delta = _subtract(projectile_delta, target_delta)
    fraction = -_dot(normal, relative_origin) / _dot(normal, relative_delta)
    impact = _add(START, _scale(projectile_delta, fraction))
    relative_cosine = abs(_dot(normal, relative_delta)) / _length(relative_delta)
    return fraction, impact, relative_cosine


def _material(armor):
    return types.SimpleNamespace(
        armor=float(armor), vehicleDamageFactor=1.0,
        checkCaliberForRichet=False, collideOnceOnly=False,
        kind=71, useAntifragmentationLining=True)


def _descriptor(tester, material):
    chassis = types.SimpleNamespace(
        itemTypeName='vehicleChassis', hullPosition=Vector(HULL_OFFSET))
    hull = types.SimpleNamespace(
        itemTypeName='vehicleHull',
        turretPositions=(Vector((0.0, 0.0, 0.0)),), hitTester=tester,
        materials={71: material})
    turret = types.SimpleNamespace(itemTypeName='vehicleTurret',
                                   gunPosition=Vector((0.0, 0.0, 0.0)))
    gun = types.SimpleNamespace(itemTypeName='vehicleGun',
                                staticTurretYaw=None, staticPitch=None)
    return types.SimpleNamespace(
        chassis=chassis, hull=hull, turret=turret, gun=gun,
        hasSiegeMode=False, isPitchHullAimingAvailable=False)


class SolidHitPipelineTests(unittest.TestCase):

    def _battle(self):
        runtime = types.SimpleNamespace(
            bigworld=_BigWorld(), math=types.SimpleNamespace(
                Vector3=_PipelineVector, Matrix=Matrix))
        battle = object.__new__(BattleRuntime)
        battle._runtime = runtime
        battle._avatar = types.SimpleNamespace(spaceID=1)
        battle._records = {}
        battle._projectile_meta = {}
        battle._projectile_terminal_data = {}
        battle._projectile_position_history = []
        battle._projectile_historic_pose_cache = {}
        battle._projectile_spatial_bins = None
        battle._projectile_destructible_context = None
        battle._projectile_scan_count = 0
        battle._projectile_candidate_count = 0
        battle._worker_mode = True
        battle._projectile_current_positions = {}
        battle._destructibles = None
        battle._equipment_state = None
        return battle

    def _run_first_hit(self, shooter_kind, shell_kind, target_delta):
        expected_fraction, expected_impact, expected_cosine = \
            _moving_plane_solution(TARGET_START, target_delta, YAW)
        self.assertGreater(expected_fraction, 0.1)
        self.assertLess(expected_fraction, 0.9)

        # The fixed factor crosses this plate; an implicit 1.0 factor does
        # not.  The contact's reported piercing value below catches a second
        # random draw or a lost launch factor as well.
        expected_piercing = 200.0 * PENETRATION_FACTOR
        material = _material(expected_piercing * 0.90 * expected_cosine)
        tester = _PlaneHitTester(PLANE_NORMAL, PLANE_POINT, 71)
        descriptor = _descriptor(tester, material)
        target = types.SimpleNamespace(
            id=42, isStarted=True, isTurretDetached=False,
            typeDescriptor=descriptor, position=Vector(TARGET_START),
            isAlive=lambda: True)
        source = types.SimpleNamespace(id=41, typeDescriptor=None)
        battle = self._battle()
        source_key = '%s:7' % shooter_kind
        target_key = 'bot:8'
        projectile_id = '%s:7:1' % shooter_kind
        battle._records[source_key] = {
            'engine_id': 41, 'network_id': 7, 'kind': shooter_kind,
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        battle._records[target_key] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 1000, 'alive': True}}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        shot = {
            'maxDistance': 100.0, 'piercingPower': (200.0, 200.0),
            'deadeye': False,
            'shell': {'kind': shell_kind, 'caliber': 105.0,
                      'damage': (300.0, 100.0), 'explosionRadius': 0.0},
        }
        battle._projectile_meta[projectile_id] = {
            'projectile_id': projectile_id, 'shooter_kind': shooter_kind,
            'shooter_id': 7, 'source_shot': shot, 'shell_index': 0,
            'penetration_factor': PENETRATION_FACTOR, 'piercing_loss': 0.0,
            'base_penetration_multiplier': 1.0, 'is_he': False,
        }
        source_pose = _plain_pose(START, 0.0)
        for fraction in (0.0, 1.0):
            target_position = _add(TARGET_START,
                                   _scale(target_delta, fraction))
            battle._sample_projectile_positions(
                fraction, {source_key: source_pose,
                           target_key: _plain_pose(target_position, YAW)})
        state = {
            'key': projectile_id, 'start': START,
            'payload': {'range_origin': START}, 'distance': 0.0,
            'elapsed': 0.0,
        }

        terminal = battle._projectile_chord(
            state, START, END, 0.0, 1.0)
        self.assertEqual('impact', terminal['reason'])
        terminal_data = battle._projectile_terminal_data[projectile_id]
        for axis in range(3):
            self.assertAlmostEqual(expected_impact[axis],
                                   terminal_data['impact'][axis], places=6)
        collision = terminal_data['collisions'][0]
        self.assertEqual('vehicleHull', collision.compName)
        self.assertIs(material, collision.matInfo)
        self.assertAlmostEqual(expected_cosine, collision.hitAngleCos,
                               places=7)
        self.assertEqual(1, len(tester.calls))

        # Current/spawn pose and yaw mutations have different analytic roots.
        # The moving case proves the chord solves against relative travel,
        # rather than pinning collision to either endpoint pose.
        if _length(target_delta) > 0.0:
            spawn_fraction, unused_impact, unused_cosine = \
                _moving_plane_solution(TARGET_START, (0.0, 0.0, 0.0), YAW)
            self.assertGreater(abs(expected_fraction - spawn_fraction), 0.01)
        wrong_yaw_fraction, unused_impact, unused_cosine = \
            _moving_plane_solution(TARGET_START, target_delta, 0.0)
        self.assertGreater(abs(expected_fraction - wrong_yaw_fraction), 0.02)

        with mock.patch('random.gauss', return_value=DAMAGE_ROLL) as roll, \
                mock.patch.object(
                    critical_damage, 'propose_direct',
                    side_effect=lambda unused_target, unused_layers,
                    unused_start, unused_end, damage, unused_shell,
                    unused_attacker, **unused_kwargs: (damage, None, {})):
            effect = battle._projectile_direct_effect(
                battle._projectile_meta[projectile_id], state, terminal_data)

        self.assertEqual(2, effect['shot_result'])
        self.assertEqual(int(DAMAGE_ROLL), effect['damage'])
        self.assertEqual(int(DAMAGE_ROLL), effect['potential_damage'])
        roll.assert_called_once_with(300.0, 30.0)
        self.assertTrue(effect['structural_armor_hit'])
        self.assertIsNotNone(lan_client._strict_projectile_effect(effect))
        contact = terminal_data['armor_contact']
        self.assertIs(material, contact['material'])
        self.assertEqual('vehicleHull', contact['component'])
        self.assertAlmostEqual(expected_cosine, contact['angle_cos'], places=7)
        self.assertAlmostEqual(expected_piercing, contact['piercing'], places=7)
        self.assertEqual(2, contact['result'])

    def test_ap_and_apcr_first_hit_pipeline_for_player_and_bot(self):
        for shooter_kind in ('player', 'bot'):
            for shell_kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
                for label, target_delta in (
                        ('stationary', (0.0, 0.0, 0.0)),
                        ('constant_velocity', (0.0, 0.0, 4.0))):
                    with self.subTest(shooter=shooter_kind, shell=shell_kind,
                                      target_motion=label):
                        self._run_first_hit(
                            shooter_kind, shell_kind, target_delta)


# The root position is selected from the independent analytic equation so
# every parameterized target is hit at the same in-chord fraction.
_OFFSET, _NORMAL = _world_plane(YAW)
_WANTED_FRACTION = 0.47
_PROJECTILE_DELTA = _subtract(END, START)
TARGET_START = _subtract(
    _subtract(_add(START, _scale(_PROJECTILE_DELTA, _WANTED_FRACTION)),
              _scale((0.0, 0.0, 4.0), _WANTED_FRACTION)), _OFFSET)


if __name__ == '__main__':
    unittest.main()
