import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import spotting


class _Vector(object):

    def __init__(self, *values):
        self._values = values

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        return self._values[index]


class _HitTester(object):

    def __init__(self, minimum, maximum):
        # Exact #1513 bboxes include a third derived value after min/max.
        self.bbox = (_Vector(*minimum), _Vector(*maximum), None)


def _descriptor(gun_bounds=((-0.2, -0.2, -1.0),
                            (0.2, 0.2, 4.0))):
    return types.SimpleNamespace(
        chassis=types.SimpleNamespace(
            hullPosition=_Vector(0.2, 0.7, -0.1),
            hitTester=_HitTester(
                (-2.0, -0.5, -3.0), (2.0, 0.8, 3.0))),
        hull=types.SimpleNamespace(
            turretPositions=(_Vector(0.1, 1.1, 0.3),),
            hitTester=_HitTester(
                (-3.0, -0.2, -2.4), (1.5, 1.4, 4.0))),
        turret=types.SimpleNamespace(
            gunPosition=_Vector(0.05, 0.2, 1.0),
            hitTester=_HitTester(
                (-0.9, -0.3, -4.0), (3.0, 0.9, 0.8))),
        gun=types.SimpleNamespace(hitTester=_HitTester(*gun_bounds)))


def _pose(position=(0.0, 0.0, 0.0), yaw=0.0, pitch=0.0, roll=0.0,
          turret_yaw=0.0, gun_pitch=0.0):
    return (position, yaw, pitch, roll, turret_yaw, gun_pitch)


class SpottingTests(unittest.TestCase):

    def assertPointAlmostEqual(self, expected, actual, places=12):
        self.assertEqual(3, len(actual))
        for expected_value, actual_value in zip(expected, actual):
            self.assertAlmostEqual(
                expected_value, actual_value, places=places)

    def test_no_skill_memory_uses_the_guaranteed_ten_second_bound(self):
        self.assertEqual(10.0, spotting.SPOT_MEMORY_SECONDS)

    def test_base_camouflage_matches_computeBaseInvisibility(self):
        # #1513 returns (moving, still) and adds the paint bonus last.
        moving, still = spotting.base_camouflage(
            0.288, 0.300, crew_factor=0.57,
            invisibility_factor=1.0, paint_bonus=0.03)

        self.assertAlmostEqual(0.288 * 0.57 + 0.03, moving)
        self.assertAlmostEqual(0.300 * 0.57 + 0.03, still)

    def test_the_aspect_applies_before_the_shot_and_foliage_terms(self):
        # getInvisibility: (base + additive) * multiplier.
        result = spotting.effective_camouflage(
            (0.20, 0.30), moving=False, additive=0.10, multiplier=1.0,
            shot_factor=0.25, fired_recently=True,
            foliage_bonus=0.15)

        self.assertAlmostEqual((0.30 + 0.10) * 0.25 + 0.15, result)

    def test_detection_distance_keeps_floor_and_ceiling(self):
        self.assertEqual(67.5, spotting.detection_distance(400.0, 0.95))
        self.assertEqual(225.0, spotting.detection_distance(400.0, 0.5))
        self.assertEqual(445.0, spotting.detection_distance(700.0, 0.0))
        self.assertEqual(565.0, spotting.VEHICLE_AOI_RADIUS)
        self.assertEqual(5.0, spotting.VEHICLE_AOI_HYSTERESIS_MARGIN)
        self.assertTrue(spotting.is_detected(50.0, 50.0, 0.95, False))
        self.assertFalse(spotting.is_detected(445.01, 700.0, 0.0, True))

    def test_target_checkpoints_use_mounted_body_bounds_without_the_gun(self):
        descriptor = _descriptor(gun_bounds=(
            (-100.0, -100.0, -100.0), (100.0, 100.0, 100.0)))

        points = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose(turret_yaw=math.pi * 0.5, gun_pitch=-0.6))

        self.assertEqual(7, len(points))
        expected = (
            (0.25, 2.7, 0.05),       # overall top
            (0.25, 1.1, 3.9),        # mounted hull front
            (0.25, 1.1, -3.8),       # mounted turret back
            (-2.8, 1.1, 0.05),       # mounted hull left
            (3.3, 1.1, 0.05),        # mounted turret right
            (1.3, 2.0, 0.15),        # current gun pivot
            (0.35, 2.0, 1.2),        # fixed-forward gun pivot
        )
        for expected_point, actual_point in zip(expected, points):
            self.assertPointAlmostEqual(expected_point, actual_point)

    def test_view_ports_are_the_overall_top_and_current_gun_pivot(self):
        ports = spotting.vehicle_view_range_ports(
            _descriptor(),
            _pose(position=(10.0, 20.0, -30.0), yaw=math.pi * 0.5,
                  turret_yaw=math.pi * 0.5, gun_pitch=0.9))

        self.assertEqual(2, len(ports))
        self.assertPointAlmostEqual((10.05, 22.7, -30.25), ports[0])
        self.assertPointAlmostEqual((10.15, 22.0, -31.3), ports[1])

    def test_fixed_and_current_pivots_coincide_only_straight_ahead(self):
        descriptor = _descriptor()

        straight = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose(turret_yaw=0.0, gun_pitch=-0.4))
        traversed = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose(turret_yaw=0.25, gun_pitch=-0.4))

        self.assertEqual(straight[5], straight[6])
        self.assertNotEqual(traversed[5], traversed[6])

    def test_gun_pitch_does_not_move_either_pivot(self):
        descriptor = _descriptor()

        raised = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose(turret_yaw=0.4, gun_pitch=-0.7))
        depressed = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose(turret_yaw=0.4, gun_pitch=0.3))

        self.assertEqual(raised[5:], depressed[5:])

    def test_hull_pitch_and_roll_transform_static_ports(self):
        descriptor = _descriptor()

        pitched = spotting.vehicle_view_range_ports(
            descriptor,
            _pose(position=(10.0, 20.0, -30.0), pitch=math.pi * 0.5))
        rolled = spotting.vehicle_view_range_ports(
            descriptor,
            _pose(position=(10.0, 20.0, -30.0), roll=math.pi * 0.5))

        self.assertPointAlmostEqual((10.25, 19.95, -27.3), pitched[0])
        self.assertPointAlmostEqual((7.3, 20.25, -29.95), rolled[0])

    def test_pose_mapping_and_six_tuple_have_identical_geometry(self):
        descriptor = _descriptor()
        tuple_pose = _pose(
            position=(7.0, 8.0, 9.0), yaw=0.2, pitch=-0.1, roll=0.05,
            turret_yaw=-0.4, gun_pitch=0.3)
        mapping_pose = {
            'position': (7.0, 8.0, 9.0),
            'yaw': 0.2, 'pitch': -0.1, 'roll': 0.05,
            'turret_yaw': -0.4, 'gun_pitch': 0.3,
        }

        self.assertEqual(
            spotting.vehicle_visibility_checkpoints(
                descriptor, tuple_pose),
            spotting.vehicle_visibility_checkpoints(
                descriptor, mapping_pose))

    def test_bounds_follow_the_current_loaded_descriptor(self):
        descriptor = _descriptor()
        original = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose())[:5]
        descriptor.chassis.hitTester.bbox = (
            _Vector(-50.0, -50.0, -50.0),
            _Vector(50.0, 50.0, 50.0), None)

        updated = spotting.vehicle_visibility_checkpoints(
            descriptor, _pose())[:5]

        self.assertNotEqual(original, updated)

    def test_invalid_or_unloaded_exact_geometry_fails_closed(self):
        descriptor = _descriptor()
        descriptor.turret.hitTester.bbox = None

        with self.assertRaisesRegex(ValueError, 'bbox is unavailable'):
            spotting.vehicle_visibility_checkpoints(descriptor, _pose())
        with self.assertRaisesRegex(ValueError, 'six-component'):
            spotting.vehicle_view_range_ports(descriptor, ((0.0, 0.0, 0.0),))
        with self.assertRaisesRegex(ValueError, 'not a finite number'):
            spotting.vehicle_view_range_ports(
                _descriptor(), _pose(yaw=float('nan')))

    def test_degenerate_component_bbox_is_rejected(self):
        descriptor = _descriptor()
        descriptor.hull.hitTester.bbox = (
            _Vector(-1.0, -0.2, -2.0),
            _Vector(-1.0, 1.0, 2.0), None)

        with self.assertRaisesRegex(ValueError, 'bbox is degenerate'):
            spotting.vehicle_visibility_checkpoints(descriptor, _pose())

    def test_trim_visibility_ray_moves_both_ends_by_equal_clearance(self):
        ray = spotting.trim_visibility_ray(
            (0.0, 0.0, 0.0), (3.0, 4.0, 0.0), clearance=1.0)

        self.assertPointAlmostEqual((0.6, 0.8, 0.0), ray[0])
        self.assertPointAlmostEqual((2.4, 3.2, 0.0), ray[1])

    def test_trim_visibility_ray_skips_short_or_invalid_segments(self):
        self.assertIsNone(spotting.trim_visibility_ray(
            (0.0, 0.0, 0.0), (0.2, 0.0, 0.0), clearance=0.1))
        self.assertIsNone(spotting.trim_visibility_ray(
            (1.0, 2.0, 3.0), (1.0, 2.0, 3.0), clearance=0.0))
        with self.assertRaisesRegex(ValueError, 'clearance is negative'):
            spotting.trim_visibility_ray(
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), clearance=-0.1)
        with self.assertRaisesRegex(ValueError, 'not a finite number'):
            spotting.trim_visibility_ray(
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                clearance=float('inf'))

    def test_visibility_rays_are_target_major_and_port_minor(self):
        observer_descriptor = _descriptor()
        observer_descriptor.turret.gunPosition = _Vector(-0.3, 0.4, 0.2)
        target_descriptor = _descriptor()
        observer_pose = _pose(turret_yaw=math.pi * 0.5)
        target_pose = _pose(
            position=(100.0, 0.0, 0.0), turret_yaw=math.pi * 0.5)
        ports = spotting.vehicle_view_range_ports(
            observer_descriptor, observer_pose)
        targets = spotting.vehicle_visibility_checkpoints(
            target_descriptor, target_pose)

        rays = spotting.visibility_rays(
            observer_descriptor, observer_pose,
            target_descriptor, target_pose,
            clearance=0.0)

        self.assertEqual(14, len(rays))
        self.assertEqual((ports[0], targets[0]), rays[0])
        self.assertEqual((ports[1], targets[0]), rays[1])
        self.assertEqual((ports[0], targets[1]), rays[2])
        self.assertEqual((ports[1], targets[6]), rays[-1])

    def test_visibility_rays_deduplicate_coincident_fixed_pivot(self):
        descriptor = _descriptor()
        observer_pose = _pose(turret_yaw=0.4)
        target_pose = _pose(
            position=(100.0, 0.0, 0.0), turret_yaw=0.0)

        observer_layout = spotting.vehicle_visibility_layout(
            descriptor, observer_pose)
        target_layout = spotting.vehicle_visibility_layout(
            descriptor, target_pose)

        rays = spotting.visibility_rays(
            descriptor, observer_pose, descriptor, target_pose,
            clearance=0.0)

        self.assertEqual(
            14, spotting.visibility_ray_count(
                observer_layout, target_layout))
        self.assertIsNone(spotting.visibility_ray_at(
            observer_layout, target_layout, 12, clearance=0.0))
        self.assertIsNone(spotting.visibility_ray_at(
            observer_layout, target_layout, 13, clearance=0.0))
        self.assertEqual(12, len(rays))
        self.assertEqual(len(rays), len(set(rays)))

    def test_visibility_rays_apply_default_clearance(self):
        descriptor = _descriptor()
        observer_pose = _pose(turret_yaw=math.pi * 0.5)
        target_pose = _pose(
            position=(100.0, 0.0, 0.0), turret_yaw=math.pi * 0.5)
        raw = spotting.visibility_rays(
            descriptor, observer_pose, descriptor, target_pose,
            clearance=0.0)[0]

        trimmed = spotting.visibility_rays(
            descriptor, observer_pose, descriptor, target_pose)[0]

        raw_length = math.sqrt(sum(
            (raw[1][axis] - raw[0][axis]) ** 2 for axis in range(3)))
        trimmed_length = math.sqrt(sum(
            (trimmed[1][axis] - trimmed[0][axis]) ** 2
            for axis in range(3)))
        self.assertAlmostEqual(raw_length - 8.0, trimmed_length, places=12)


if __name__ == '__main__':
    unittest.main()
