import math
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT
CLIENT_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import shot_geometry  # noqa: E402


SIEGE_VEHICLES = (
    'sweden:S10_Strv_103_0_Series',
    'sweden:S11_Strv_103B',
    'sweden:S21_UDES_03',
    'sweden:S22_Strv_S1',
)


class _Vector(object):
    """Match the iterable Math.Vector3 surface used by #1513 descriptors."""

    def __init__(self, *values):
        self._values = values

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        return self._values[index]


class _Tester(object):
    def __init__(self, minimum, maximum):
        self.bbox = (_Vector(*minimum), _Vector(*maximum), None)


def _mode_descriptor(name, active_turret=1, barrel_z=3.75):
    shell_type = types.SimpleNamespace(
        name='ARMOR_PIERCING', explosionRadius=0.0)
    shell = types.SimpleNamespace(
        type=shell_type, kind='ARMOR_PIERCING', caliber=105.0,
        damage=(390.0, 390.0), piercingPower=(250.0, 230.0),
        effectsIndex=1, isTracer=True)
    shot = types.SimpleNamespace(
        shell=shell, speed=1100.0, gravity=9.81, maxDistance=720.0,
        piercingPower=(250.0, 230.0))
    return types.SimpleNamespace(
        type=types.SimpleNamespace(
            name=name, level=8, tags=('tankDestroyer',), crewRoles=()),
        maxHealth=1000,
        activeTurretPosition=active_turret,
        chassis=types.SimpleNamespace(
            hullPosition=_Vector(0.2, 0.7, -0.1),
            hitTester=_Tester((-1.5, -0.8, -3.5), (1.5, 0.8, 3.5))),
        hull=types.SimpleNamespace(
            turretPositions=(
                _Vector(0.15, 1.1, 0.3),
                _Vector(2.0, -0.4, 0.8)),
            hitTester=_Tester((-1.7, -0.2, -3.5), (1.7, 1.4, 3.5))),
        turret=types.SimpleNamespace(
            gunPosition=_Vector(0.05, 0.22, 1.4),
            hitTester=_Tester((-0.9, -0.3, -0.9), (0.9, 0.8, 0.9))),
        gun=types.SimpleNamespace(
            shots=(shot,), reloadTime=5.0, clip=(1, 0.0),
            hitTester=_Tester((-0.2, -0.2, -1.0),
                              (0.2, 0.2, barrel_z))),
        physics={'weight': 39000.0},
    )


class ShotGeometryTest(unittest.TestCase):
    POSITION = (10.0, 20.0, -30.0)
    YAW = 0.73
    PITCH = -0.21
    ROLL = 0.17
    TURRET_YAW = -0.46
    GUN_PITCH = -0.28

    def assertVectorAlmostEqual(self, expected, actual, places=12):
        self.assertEqual(3, len(actual))
        for expected_value, actual_value in zip(expected, actual):
            self.assertAlmostEqual(expected_value, actual_value, places=places)

    def test_compound_stabilised_pose_matches_pinned_transform_order(self):
        descriptor = _mode_descriptor(SIEGE_VEHICLES[0])

        origin, direction = shot_geometry.shot_origin_and_direction(
            descriptor, self.POSITION, self.YAW, self.PITCH, self.ROLL,
            self.TURRET_YAW, self.GUN_PITCH)

        self.assertVectorAlmostEqual(
            (10.270505691803578, 22.217454922678797,
             -28.850182018782874), origin)
        self.assertVectorAlmostEqual(
            (0.18564081088070922, 0.3753073443106598,
             0.9081199737050345), direction)
        self.assertAlmostEqual(
            1.0, math.sqrt(sum(value * value for value in direction)),
            places=12)

    def test_shot_origin_is_independent_of_gun_pitch(self):
        descriptor = _mode_descriptor(SIEGE_VEHICLES[0])

        raised = shot_geometry.shot_origin_and_direction(
            descriptor, self.POSITION, self.YAW, self.PITCH, self.ROLL,
            self.TURRET_YAW, -0.6)
        depressed = shot_geometry.shot_origin_and_direction(
            descriptor, self.POSITION, self.YAW, self.PITCH, self.ROLL,
            self.TURRET_YAW, 0.4)

        self.assertEqual(raised[0], depressed[0])
        self.assertNotEqual(raised[1], depressed[1])

    def test_world_direction_round_trips_to_local_turret_and_gun_angles(self):
        descriptor = _mode_descriptor(SIEGE_VEHICLES[0])
        unused_origin, direction = shot_geometry.shot_origin_and_direction(
            descriptor, self.POSITION, self.YAW, self.PITCH, self.ROLL,
            self.TURRET_YAW, self.GUN_PITCH)

        turret_yaw, gun_pitch = \
            shot_geometry.world_direction_to_local_gun_angles(
                direction, self.YAW, self.PITCH, self.ROLL)

        self.assertAlmostEqual(self.TURRET_YAW, turret_yaw, places=12)
        self.assertAlmostEqual(self.GUN_PITCH, gun_pitch, places=12)

    def test_flat_world_direction_keeps_legacy_local_angles(self):
        world_yaw = 0.41
        world_pitch = -0.19
        direction = (
            math.sin(world_yaw) * math.cos(world_pitch),
            -math.sin(world_pitch),
            math.cos(world_yaw) * math.cos(world_pitch),
        )

        turret_yaw, gun_pitch = \
            shot_geometry.world_direction_to_local_gun_angles(
                direction, 0.0, 0.0, 0.0)

        self.assertAlmostEqual(world_yaw, turret_yaw, places=12)
        self.assertAlmostEqual(world_pitch, gun_pitch, places=12)

    def test_barrel_endpoint_uses_bbox_max_z_and_active_turret_mount(self):
        descriptor = _mode_descriptor(
            SIEGE_VEHICLES[0], active_turret=1, barrel_z=3.75)

        local = shot_geometry.compute_barrel_local_point(
            descriptor, self.TURRET_YAW, self.GUN_PITCH)
        world = shot_geometry.barrel_world_point(
            descriptor, self.POSITION, self.YAW, self.PITCH, self.ROLL,
            self.TURRET_YAW, self.GUN_PITCH)

        self.assertVectorAlmostEqual(
            (0.02330499064835667, 1.5563336821154263,
             5.206006373479716), local)
        self.assertVectorAlmostEqual(
            (13.002581360777969, 22.589300157173078,
             -26.284458332711136), world)

        longer = _mode_descriptor(
            SIEGE_VEHICLES[0], active_turret=1, barrel_z=4.75)
        longer_local = shot_geometry.compute_barrel_local_point(
            longer, self.TURRET_YAW, self.GUN_PITCH)
        delta = tuple(longer_local[index] - local[index]
                      for index in range(3))
        expected_direction = (-0.4266587425269846,
                              0.27635564856411376,
                              0.8611561257588546)
        self.assertVectorAlmostEqual(expected_direction, delta)

    def test_pivot_uses_first_mount_but_barrel_uses_active_mount(self):
        descriptor = _mode_descriptor(
            SIEGE_VEHICLES[0], active_turret=1, barrel_z=3.75)

        origin, unused = shot_geometry.shot_origin_and_direction(
            descriptor, (0.0, 0.0, 0.0), 0.0, 0.0, 0.0, 0.0, 0.0)
        barrel = shot_geometry.compute_barrel_local_point(
            descriptor, 0.0, 0.0)

        self.assertVectorAlmostEqual((0.4, 2.02, 1.6), origin)
        self.assertVectorAlmostEqual((2.25, 0.52, 5.85), barrel)

    def test_fractional_active_turret_position_is_not_silently_truncated(self):
        descriptor = _mode_descriptor(SIEGE_VEHICLES[0])
        descriptor.activeTurretPosition = 0.5

        with self.assertRaisesRegex(
                ValueError, 'invalid activeTurretPosition'):
            shot_geometry.compute_barrel_local_point(descriptor, 0.0, 0.0)


if __name__ == '__main__':
    unittest.main()
