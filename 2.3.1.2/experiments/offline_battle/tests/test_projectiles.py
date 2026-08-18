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

    def __sub__(self, other):
        return _Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5


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


def _stub_damage():
    """projectiles.py imports its siblings by the client package path."""
    package = 'gui.mods.offline_battle_2312'
    for name in ('gui', 'gui.mods', package):
        sys.modules.setdefault(name, types.ModuleType(name))
    module = types.ModuleType(package + '.damage')
    module.nearest_vehicle = lambda targets, start, end: None
    sys.modules[package + '.damage'] = module
    setattr(sys.modules[package], 'damage', module)
    runtime_path = (MODS / 'offline_battle_2312' / 'projectile_runtime.py')
    spec = importlib.util.spec_from_file_location(
        package + '.projectile_runtime', runtime_path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    sys.modules[package + '.projectile_runtime'] = runtime
    setattr(sys.modules[package], 'projectile_runtime', runtime)
    return module


_damage = _stub_damage()
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

    def test_a_vehicle_in_the_way_is_hit_before_the_ground(self):
        vehicle = object()

        def collide(space, start, end, flags):
            return _Hit(0.0, 10.0, start.z + 15.0)

        _install_fakes(collide)
        _damage.nearest_vehicle = lambda targets, start, end: (
            vehicle, 4.0, ['layer'])
        try:
            landing = projectiles.impact(
                1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0,
                targets=[vehicle])
        finally:
            _damage.nearest_vehicle = lambda targets, start, end: None
        self.assertIs(landing.vehicle, vehicle)
        self.assertEqual(landing.collisions, ['layer'])
        self.assertAlmostEqual(landing.travelled, 4.0)

    def test_a_wall_reports_its_point_time_and_material(self):
        def collide(space, start, end, flags):
            if end.z < 100.0:
                return None
            return _Hit(0.0, 10.0, 100.0, mat_kind=7)

        _install_fakes(collide)
        landing = projectiles.impact(
            1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0)
        self.assertAlmostEqual(landing.point.z, 100.0)
        self.assertEqual(landing.mat_kind, 7)
        self.assertAlmostEqual(landing.elapsed, 0.25, places=2)
        self.assertAlmostEqual(landing.travelled, 100.0, places=1)

    def test_the_first_chord_hit_wins(self):
        hits = []

        def collide(space, start, end, flags):
            hits.append(start.z)
            return _Hit(0.0, 10.0, start.z + 1.0)

        _install_fakes(collide)
        landing = projectiles.impact(
            1, (0.0, 10.0, 0.0), (0.0, 0.0, 400.0), 9.81, 800.0)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(landing.point.z, 1.0)
        self.assertLess(landing.elapsed, projectiles.STEP_SECONDS)

    def test_a_falling_shell_meets_the_ground_it_flew_over(self):
        def collide(space, start, end, flags):
            if end.y > 0.0:
                return None
            return _Hit(0.0, 0.0, end.z)

        _install_fakes(collide)
        landing = projectiles.impact(1, (0.0, 20.0, 0.0),
                                     (0.0, 0.0, 100.0), 9.81, 800.0)
        self.assertIsNotNone(landing)
        self.assertAlmostEqual(landing.elapsed,
                               math.sqrt(2.0 * 20.0 / 9.81), places=1)


if __name__ == '__main__':
    unittest.main()


class ScatterTests(unittest.TestCase):
    def test_sigma_is_a_third_of_the_angle(self):
        seen = []

        def gauss(mean, sigma):
            seen.append(sigma)
            return 0.0

        projectiles.scattered_direction((0.0, 0.0, 1.0), 0.09, gauss)
        self.assertAlmostEqual(seen[0], 0.03)

    def test_the_scattered_direction_stays_unit_length(self):
        direction = projectiles.scattered_direction(
            (0.0, 0.0, 1.0), 0.2, lambda mean, sigma: 0.1)
        size = sum(axis * axis for axis in direction) ** 0.5
        self.assertAlmostEqual(size, 1.0)

    def test_zero_dispersion_keeps_the_aim(self):
        self.assertEqual(
            projectiles.scattered_direction((0.0, 0.0, 1.0), 0.0),
            (0.0, 0.0, 1.0))
