import math
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __sub__(self, other):
        return _Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5


class _Result(object):
    """A 2.3.1.2 collision result, which the shim reshapes for the law."""

    def __init__(self, point, normal, mat_kind=0):
        self.closestPoint = point
        self.normal = normal
        self.matKind = mat_kind


def _install(collide):
    math_module = types.ModuleType('Math')
    math_module.Vector3 = _Vector3
    bigworld = types.ModuleType('BigWorld')
    bigworld.wg_collideSegment = collide
    sys.modules['Math'] = math_module
    sys.modules['BigWorld'] = bigworld
    return math_module


_install(lambda *args: None)
package_stub.stub('destructibles_sensor',
                  _catalog_soft_static_path=lambda *a, **k: False,
                  _diagnostic_static_recast_1513=lambda *a, **k: None,
                  _try_destroy_solid_hit=lambda *a, **k: False,
                  _vehicle_hull_bbox=lambda descriptor: (
                      (-1.4, -0.5, -3.0), (1.4, 1.2, 3.2), 0))
world_collision = package_stub.load('world_collision')
engine_shim = package_stub.load('engine_shim')


class _Descriptor(object):
    hull = types.SimpleNamespace(hitTester=types.SimpleNamespace(
        bbox=((-1.4, -0.5, -3.0), (1.4, 1.2, 3.2), 0)))


def _blocked(collide, velocity=5.0, yaw=0.0, position=(0.0, 10.0, 0.0)):
    math_module = _install(collide)
    return world_collision.check_horizontal_collision(
        engine_shim.wrap(sys.modules['BigWorld']), math_module, 1,
        _Vector3(*position), yaw,
        velocity, _Descriptor(), False, 0.02)


class BlockedTests(unittest.TestCase):
    def test_open_ground_is_clear(self):
        self.assertFalse(_blocked(lambda *args: None))

    def test_a_vertical_wall_ahead_blocks(self):
        def collide(space, start, end, flags):
            if end.z <= start.z:
                return None
            return _Result(_Vector3(start.x, start.y, start.z + 1.0),
                           _Vector3(0.0, 0.0, -1.0))

        self.assertTrue(_blocked(collide))

    def test_a_wall_behind_does_not_block_forward_travel(self):
        def collide(space, start, end, flags):
            if end.z >= start.z:
                return None
            return _Result(_Vector3(start.x, start.y, start.z - 1.0),
                           _Vector3(0.0, 0.0, 1.0))

        self.assertFalse(_blocked(collide, velocity=5.0))

    def test_a_wall_behind_blocks_reverse(self):
        def collide(space, start, end, flags):
            if end.z >= start.z:
                return None
            return _Result(_Vector3(start.x, start.y, start.z - 1.0),
                           _Vector3(0.0, 0.0, 1.0))

        self.assertTrue(_blocked(collide, velocity=-5.0))

    def test_a_distant_wall_does_not_block(self):
        def collide(space, start, end, flags):
            if end.z <= start.z:
                return None
            return _Result(_Vector3(start.x, start.y, start.z + 50.0),
                           _Vector3(0.0, 0.0, -1.0))

        self.assertFalse(_blocked(collide))

    def test_a_drivable_slope_is_not_a_wall(self):
        """A rising lane whose surface is a slope stays drivable."""
        gradient = math.tan(math.radians(20.0))
        normal = _Vector3(0.0, math.cos(math.radians(20.0)),
                          -math.sin(math.radians(20.0)))

        def collide(space, start, end, flags):
            if end.y < start.y - 1.0:
                return _Result(_Vector3(end.x, 10.0 + end.z * gradient, end.z),
                               _Vector3(0.0, 1.0, 0.0))
            if end.z <= start.z or start.y > 10.6:
                return None
            reach = (10.6 - start.y) / gradient
            return _Result(_Vector3(start.x, start.y, start.z + reach),
                           normal)

        self.assertFalse(_blocked(collide))

    def test_the_lane_offsets_follow_the_yaw(self):
        seen = []

        def collide(space, start, end, flags):
            seen.append((round(start.x, 3), round(start.z, 3)))
            return None

        _blocked(collide, yaw=math.pi / 2.0)
        self.assertTrue(any(abs(x) > 0.1 for x, _unused in seen))
        self.assertTrue(any(abs(z) > 0.1 for _unused, z in seen))


class ShimTests(unittest.TestCase):
    def test_the_shim_presents_a_point_and_a_normal_by_index(self):
        point, normal = _Vector3(1.0, 2.0, 3.0), _Vector3(0.0, 1.0, 0.0)
        _install(lambda *args: _Result(point, normal, 7))
        result = engine_shim.wrap(sys.modules['BigWorld']).wg_collideSegment(
            1, _Vector3(0, 0, 0), _Vector3(0, 0, 1), 128)
        self.assertIs(result[0], point)
        self.assertIs(result[1], normal)
        self.assertEqual(result[2], 7)

    def test_the_shim_keeps_the_names_this_client_uses(self):
        point, normal = _Vector3(1.0, 2.0, 3.0), _Vector3(0.0, 1.0, 0.0)
        _install(lambda *args: _Result(point, normal, 7))
        result = engine_shim.wrap(sys.modules['BigWorld']).wg_collideSegment(
            1, _Vector3(0, 0, 0), _Vector3(0, 0, 1), 128)
        self.assertIs(result.closestPoint, point)
        self.assertIs(result.normal, normal)

    def test_a_miss_stays_a_miss(self):
        _install(lambda *args: None)
        self.assertIsNone(engine_shim.wrap(sys.modules['BigWorld']).wg_collideSegment(
            1, _Vector3(0, 0, 0), _Vector3(0, 0, 1), 128))


if __name__ == '__main__':
    unittest.main()
