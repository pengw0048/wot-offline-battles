import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Hit(object):
    def __init__(self, y):
        self.closestPoint = _Vector3(0.0, y, 0.0)


def _install_fakes(collide):
    math_module = types.ModuleType('Math')
    math_module.Vector3 = _Vector3
    bigworld = types.ModuleType('BigWorld')
    bigworld.wg_collideSegment = collide
    sys.modules['Math'] = math_module
    sys.modules['BigWorld'] = bigworld


_install_fakes(lambda *args: None)
_spec = importlib.util.spec_from_file_location(
    'offline_battle_suspension', MODS / 'offline_battle_2312' / 'suspension.py')
suspension = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suspension)


class GroundProbeTests(unittest.TestCase):
    def test_a_near_hit_is_ground(self):
        _install_fakes(lambda *args: _Hit(10.4))
        self.assertAlmostEqual(suspension.ground_y(1, 0.0, 0.0, 10.0), 10.4)

    def test_a_roof_far_above_is_not_ground(self):
        _install_fakes(lambda *args: _Hit(30.0))
        self.assertIsNone(suspension.ground_y(1, 0.0, 0.0, 10.0))

    def test_a_miss_reports_nothing(self):
        _install_fakes(lambda *args: None)
        self.assertIsNone(suspension.ground_y(1, 0.0, 0.0, 10.0))

    def test_support_reports_the_highest_and_the_centre(self):
        heights = iter((12.0, 10.0, 9.0))

        def collide(space, start, end, flags):
            return _Hit(next(heights))

        _install_fakes(collide)
        highest, centre = suspension.support(1, (0.0, 10.0, 0.0), 0.0, 2.0)
        self.assertAlmostEqual(highest, 12.0)
        self.assertAlmostEqual(centre, 10.0)


class HullSpanTests(unittest.TestCase):
    def test_reads_the_hull_bbox(self):
        descriptor = types.SimpleNamespace(
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=((-1.4, 0.0, -2.1), (1.4, 0.0, 2.3), 0))))
        length, width = suspension.hull_span(descriptor)
        self.assertAlmostEqual(length, 4.4)
        self.assertAlmostEqual(width, 2.8)

    def test_a_narrow_hull_keeps_the_track_width_minimum(self):
        descriptor = types.SimpleNamespace(
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=((-0.83, 0.0, -1.45), (0.83, 0.0, 1.63), 0))))
        length, width = suspension.hull_span(descriptor)
        self.assertAlmostEqual(length, 3.08)
        self.assertAlmostEqual(width, suspension.MIN_HULL_WIDTH)


class PoseAngleTests(unittest.TestCase):
    def test_level_ground_is_level(self):
        pitch, roll = suspension.pose_angles(10.0, 10.0, 10.0, 10.0, 4.0, 2.0)
        self.assertAlmostEqual(pitch, 0.0)
        self.assertAlmostEqual(roll, 0.0)

    def test_a_nose_up_slope_gives_a_negative_pitch(self):
        pitch, _unused = suspension.pose_angles(11.0, 10.0, 10.0, 10.0, 4.0,
                                                2.0)
        self.assertLess(pitch, 0.0)

    def test_higher_ground_on_the_right_rolls_the_right_side_up(self):
        _unused, roll = suspension.pose_angles(10.0, 10.0, 11.0, 10.0, 4.0,
                                               2.0)
        self.assertGreater(roll, 0.0)

    def test_higher_ground_on_the_left_rolls_the_other_way(self):
        _unused, roll = suspension.pose_angles(10.0, 10.0, 10.0, 11.0, 4.0,
                                               2.0)
        self.assertLess(roll, 0.0)

    def test_an_extreme_slope_is_clamped(self):
        pitch, roll = suspension.pose_angles(20.0, 10.0, 20.0, 10.0, 4.0, 2.0)
        self.assertLessEqual(math.sqrt(pitch * pitch + roll * roll),
                             suspension.MAX_TILT + 1e-6)


class SettleTests(unittest.TestCase):
    def test_a_rise_is_limited_to_what_the_tick_can_climb(self):
        value = suspension.settle(10.0, 40.0, 8.0, 0.02)
        self.assertAlmostEqual(value - 10.0,
                               suspension.climb_limit(8.0, 0.02))

    def test_a_small_rise_is_taken_completely(self):
        self.assertAlmostEqual(suspension.settle(10.0, 10.2, 8.0, 0.02), 10.2)

    def test_a_descent_is_smoothed(self):
        value = suspension.settle(10.0, 9.0, 8.0, 0.02)
        self.assertGreater(value, 9.0)
        self.assertLess(value, 10.0)

    def test_a_step_higher_than_the_climb_limit_is_an_obstacle(self):
        limit = suspension.climb_limit(8.0, 0.02)
        self.assertTrue(
            suspension.support_rise_is_obstacle(10.0, 10.0 + limit + 0.5,
                                                limit))
        self.assertFalse(
            suspension.support_rise_is_obstacle(10.0, 10.0 + limit - 0.1,
                                                limit))

    def test_a_missing_support_is_not_an_obstacle(self):
        self.assertFalse(suspension.support_rise_is_obstacle(10.0, None, 0.6))


class DrivePitchTests(unittest.TestCase):
    def test_a_rise_ahead_gives_a_nose_up_pitch(self):
        def collide(space, start, end, flags):
            return _Hit(12.0 if start.z > 0.0 else 10.0)

        _install_fakes(collide)
        pitch = suspension.drive_pitch(1, (0.0, 10.0, 0.0), 0.0)
        self.assertAlmostEqual(pitch, -math.atan2(2.0, 4.0))

    def test_a_steep_rise_is_clamped_before_it_reaches_the_law(self):
        def collide(space, start, end, flags):
            return _Hit(13.4 if start.z > 0.0 else 10.0)

        _install_fakes(collide)
        clamped = (suspension.DRIVE_PROBE_DISTANCE *
                   suspension.DRIVE_WALL_GRADIENT)
        pitch = suspension.drive_pitch(1, (0.0, 10.0, 0.0), 0.0)
        self.assertAlmostEqual(pitch, -math.atan2(clamped, 4.0))

    def test_a_wall_above_the_hull_is_skipped_and_probed_again(self):
        def collide(space, start, end, flags):
            if start.z > 0.0 and start.y > 18.0:
                return _Hit(18.0)
            return _Hit(10.0)

        _install_fakes(collide)
        self.assertAlmostEqual(
            suspension.drive_pitch(1, (0.0, 10.0, 0.0), 0.0), 0.0)

    def test_level_ground_gives_no_drive_pitch(self):
        _install_fakes(lambda *args: _Hit(10.0))
        self.assertAlmostEqual(
            suspension.drive_pitch(1, (0.0, 10.0, 0.0), 0.0), 0.0)

    def test_the_median_rejects_a_single_spike(self):
        history = []
        for value in (0.1, 0.1, 0.1, 0.1):
            suspension.median_pitch(history, value)
        self.assertAlmostEqual(suspension.median_pitch(history, 0.9), 0.1)


if __name__ == '__main__':
    unittest.main()
