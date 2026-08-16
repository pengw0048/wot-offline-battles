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
    def __init__(self, x, y, z, mat_kind=3):
        self.closestPoint = _Vector3(x, y, z)
        self.matKind = mat_kind


def _install_fakes(collide):
    math_module = types.ModuleType('Math')
    math_module.Vector3 = _Vector3
    bigworld = types.ModuleType('BigWorld')
    bigworld.wg_collideSegment = collide
    sys.modules['Math'] = math_module
    sys.modules['BigWorld'] = bigworld


_install_fakes(lambda *args: None)
_spec = importlib.util.spec_from_file_location(
    'offline_battle_projectiles',
    MODS / 'offline_battle_2312' / 'projectiles.py')
projectiles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projectiles)


class TrajectoryTests(unittest.TestCase):
    def test_a_flat_shot_drops_by_the_gravity_term(self):
        point = projectiles.trajectory_position((0.0, 10.0, 0.0),
                                                (0.0, 0.0, 800.0), 9.81, 0.5)
        self.assertAlmostEqual(point[2], 400.0)
        self.assertAlmostEqual(point[1], 10.0 - 0.5 * 9.81 * 0.25)

    def test_flight_time_is_the_range_over_the_speed(self):
        self.assertAlmostEqual(
            projectiles.flight_seconds((0.0, 0.0, 400.0), 9.81, 800.0), 2.0)

    def test_flight_time_is_capped(self):
        self.assertAlmostEqual(
            projectiles.flight_seconds((0.0, 0.0, 1.0), 9.81, 1e6),
            projectiles.MAX_FLIGHT_SECONDS)

    def test_a_still_shell_never_flies(self):
        self.assertAlmostEqual(
            projectiles.flight_seconds((0.0, 0.0, 0.0), 9.81, 800.0), 0.0)


class ImpactTests(unittest.TestCase):
    def test_open_ground_reports_no_impact(self):
        _install_fakes(lambda *args: None)
        self.assertIsNone(projectiles.impact(
            1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0))

    def test_a_wall_reports_its_point_time_and_material(self):
        def collide(space, start, end, flags):
            if end.z < 100.0:
                return None
            return _Hit(0.0, 10.0, 100.0, mat_kind=7)

        _install_fakes(collide)
        point, elapsed, mat_kind = projectiles.impact(
            1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0)
        self.assertAlmostEqual(point.z, 100.0)
        self.assertEqual(mat_kind, 7)
        self.assertAlmostEqual(elapsed, 0.25, places=2)

    def test_the_first_chord_hit_wins(self):
        hits = []

        def collide(space, start, end, flags):
            hits.append(start.z)
            return _Hit(0.0, 10.0, start.z + 1.0)

        _install_fakes(collide)
        point, elapsed, _unused = projectiles.impact(
            1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(point.z, 1.0)
        self.assertLess(elapsed, projectiles.STEP_SECONDS)

    def test_a_falling_shell_meets_the_ground_it_flew_over(self):
        def collide(space, start, end, flags):
            if end.y > 0.0:
                return None
            return _Hit(0.0, 0.0, end.z)

        _install_fakes(collide)
        landing = projectiles.impact(1, (0.0, 20.0, 0.0),
                                     (0.0, 0.0, 100.0), 9.81, 800.0)
        self.assertIsNotNone(landing)
        point, elapsed, _unused = landing
        self.assertAlmostEqual(elapsed, math.sqrt(2.0 * 20.0 / 9.81),
                               places=1)


if __name__ == '__main__':
    unittest.main()
