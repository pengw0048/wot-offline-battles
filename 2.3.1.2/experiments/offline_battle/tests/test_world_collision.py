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
    def __init__(self, point, normal):
        self.closestPoint = point
        self.normal = normal


def _install_fakes(collide):
    math_module = types.ModuleType('Math')
    math_module.Vector3 = _Vector3
    bigworld = types.ModuleType('BigWorld')
    bigworld.wg_collideSegment = collide
    sys.modules['Math'] = math_module
    sys.modules['BigWorld'] = bigworld


_install_fakes(lambda *args: None)
_spec = importlib.util.spec_from_file_location(
    'offline_battle_world_collision',
    MODS / 'offline_battle_2312' / 'world_collision.py')
world_collision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_collision)

EXTENTS = (1.4, 3.2, 3.0)
DT = 0.02


def _blocked(collide, velocity=5.0, yaw=0.0, position=(0.0, 10.0, 0.0)):
    _install_fakes(collide)
    return world_collision.blocked(1, position, yaw, velocity, EXTENTS, DT)


class HullExtentsTests(unittest.TestCase):
    def test_reads_the_hull_hit_tester_bbox(self):
        descriptor = types.SimpleNamespace(
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=((-1.5, -0.5, -3.4), (1.5, 1.2, 3.6), 0))))
        half_width, front, back = world_collision.hull_extents(descriptor)
        self.assertAlmostEqual(half_width, 1.4)
        self.assertAlmostEqual(front, 3.6)
        self.assertAlmostEqual(back, 3.4)

    def test_falls_back_when_the_bsp_model_is_not_loaded(self):
        descriptor = types.SimpleNamespace(
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=None)))
        self.assertEqual(world_collision.hull_extents(descriptor),
                         (world_collision.DEFAULT_HALF_WIDTH,
                          world_collision.DEFAULT_HALF_LENGTH,
                          world_collision.DEFAULT_HALF_LENGTH))


class BlockedTests(unittest.TestCase):
    def test_open_ground_is_clear(self):
        self.assertFalse(_blocked(lambda *args: None))

    def test_a_vertical_wall_ahead_blocks(self):
        def collide(space, start, end, flags):
            if end.z <= start.z:
                return None
            return _Hit(_Vector3(start.x, start.y, start.z + 1.0),
                        _Vector3(0.0, 0.0, -1.0))

        self.assertTrue(_blocked(collide))

    def test_a_wall_behind_does_not_block_forward_travel(self):
        def collide(space, start, end, flags):
            if end.z >= start.z:
                return None
            return _Hit(_Vector3(start.x, start.y, start.z - 1.0),
                        _Vector3(0.0, 0.0, 1.0))

        self.assertTrue(_blocked(collide, velocity=-5.0))
        self.assertFalse(_blocked(collide, velocity=5.0))

    def test_a_wall_behind_blocks_reverse(self):
        def collide(space, start, end, flags):
            if end.z >= start.z:
                return None
            return _Hit(_Vector3(start.x, start.y, start.z - 1.0),
                        _Vector3(0.0, 0.0, 1.0))

        self.assertTrue(_blocked(collide, velocity=-5.0))

    def test_a_drivable_slope_is_not_a_wall(self):
        """A 20 degree rise reached by the lower ray must stay drivable."""
        gradient = math.tan(math.radians(20.0))
        normal = _Vector3(0.0, math.cos(math.radians(20.0)),
                          -math.sin(math.radians(20.0)))

        def collide(space, start, end, flags):
            if end.y < start.y - 1.0:
                return _Hit(_Vector3(end.x, 10.0 + end.z * gradient, end.z),
                            _Vector3(0.0, 1.0, 0.0))
            if end.z <= start.z:
                return None
            if start.y > 10.0 + world_collision.LOWER_RAY_HEIGHT:
                return None
            reach = (10.0 + world_collision.LOWER_RAY_HEIGHT -
                     start.y) / gradient
            return _Hit(_Vector3(start.x, start.y, start.z + reach), normal)

        self.assertFalse(_blocked(collide))

    def test_a_wall_above_a_slope_still_blocks(self):
        gradient = math.tan(math.radians(20.0))
        slope_normal = _Vector3(0.0, math.cos(math.radians(20.0)),
                                -math.sin(math.radians(20.0)))

        def collide(space, start, end, flags):
            if end.y < start.y - 1.0:
                return _Hit(_Vector3(end.x, 10.0 + end.z * gradient, end.z),
                            _Vector3(0.0, 1.0, 0.0))
            if end.z <= start.z:
                return None
            if start.y > 10.0 + world_collision.LOWER_RAY_HEIGHT:
                return _Hit(_Vector3(start.x, start.y, start.z + 0.8),
                            _Vector3(0.0, 0.0, -1.0))
            reach = (10.0 + world_collision.LOWER_RAY_HEIGHT -
                     start.y) / gradient
            return _Hit(_Vector3(start.x, start.y, start.z + reach),
                        slope_normal)

        self.assertTrue(_blocked(collide))

    def test_a_distant_wall_does_not_block(self):
        def collide(space, start, end, flags):
            if end.z <= start.z:
                return None
            return _Hit(_Vector3(start.x, start.y, start.z + 50.0),
                        _Vector3(0.0, 0.0, -1.0))

        self.assertFalse(_blocked(collide))

    def test_the_lane_offsets_follow_the_yaw(self):
        seen = []

        def collide(space, start, end, flags):
            seen.append((round(start.x, 3), round(start.z, 3)))
            return None

        _blocked(collide, yaw=math.pi / 2.0)
        self.assertTrue(any(abs(x) > 0.1 for x, _unused in seen))
        self.assertTrue(any(abs(z) > 0.1 for _unused, z in seen))


class HullContactTests(unittest.TestCase):
    def test_open_space_has_no_contact(self):
        _install_fakes(lambda *args: None)
        self.assertEqual(
            world_collision.hull_contacts(1, (0.0, 10.0, 0.0), 0.0, EXTENTS),
            0)

    def test_a_wall_on_the_right_catches_the_right_corners(self):
        def collide(space, start, end, flags):
            if end.x <= start.x:
                return None
            return _Hit(_Vector3(start.x + 0.2, start.y, start.z),
                        _Vector3(-1.0, 0.0, 0.0))

        _install_fakes(collide)
        self.assertEqual(
            world_collision.hull_contacts(1, (0.0, 10.0, 0.0), 0.0, EXTENTS),
            2)

    def test_a_far_hit_is_not_a_contact(self):
        def collide(space, start, end, flags):
            return _Hit(_Vector3(start.x + 50.0, start.y, start.z),
                        _Vector3(-1.0, 0.0, 0.0))

        _install_fakes(collide)
        self.assertEqual(
            world_collision.hull_contacts(1, (0.0, 10.0, 0.0), 0.0, EXTENTS),
            0)

    def test_the_corners_follow_the_yaw(self):
        def collide(space, start, end, flags):
            if end.z <= start.z:
                return None
            return _Hit(_Vector3(start.x, start.y, start.z + 0.2),
                        _Vector3(0.0, 0.0, -1.0))

        _install_fakes(collide)
        self.assertEqual(
            world_collision.hull_contacts(1, (0.0, 10.0, 0.0),
                                          math.pi / 2.0, EXTENTS),
            2)


if __name__ == '__main__':
    unittest.main()
