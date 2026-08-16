import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

tank_collision = package_stub.load('tank_collision')

SHAPE = (1.2, 2.0, -0.5, 1.5)


def _body(vehicle_id, x, z, yaw=0.0, vx=0.0, vz=0.0, mass=5000.0):
    return {'id': vehicle_id, 'x': x, 'y': 0.0, 'z': z, 'yaw': yaw,
            'mass': mass, 'vx': vx, 'vz': vz, 'alive': True, 'shape': SHAPE}


class ChassisShapeTests(unittest.TestCase):
    def test_the_shape_comes_from_the_chassis_bbox(self):
        descriptor = types.SimpleNamespace(
            chassis=types.SimpleNamespace(
                hitTester=types.SimpleNamespace(
                    bbox=((-1.3, -0.4, -2.2), (1.3, 0.9, 2.2), 0)),
                hullPosition=(0.0, 0.4, 0.0)),
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=((-1.0, 0.0, -2.0), (1.0, 1.4, 2.0), 0))))
        half_width, half_length, lower, upper = (
            tank_collision.chassis_shape(descriptor))
        self.assertAlmostEqual(half_width, 1.3)
        self.assertAlmostEqual(half_length, 2.2)
        self.assertLess(lower, upper)


class ContactTests(unittest.TestCase):
    def test_two_separated_hulls_do_not_touch(self):
        self.assertIsNone(tank_collision.obb_contact(
            0.0, 0.0, 0.0, SHAPE, 0.0, 20.0, 0.0, SHAPE))

    def test_two_overlapping_hulls_report_a_normal(self):
        contact = tank_collision.obb_contact(
            0.0, 0.0, 0.0, SHAPE, 0.0, 3.0, 0.0, SHAPE)
        self.assertIsNotNone(contact)
        normal_x, normal_z, penetration = contact
        self.assertGreater(penetration, 0.0)
        self.assertLess(normal_z, 0.0)


class ResolveTests(unittest.TestCase):
    def test_a_clear_hull_is_left_alone(self):
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0),
                                             [_body(2, 0.0, 40.0)])
        self.assertEqual(result['correction'], (0.0, 0.0))
        self.assertEqual(result['delta_velocity'], (0.0, 0.0))

    def test_an_overlapping_hull_is_pushed_apart(self):
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0, vz=5.0),
                                             [_body(2, 0.0, 3.0)])
        correction_x, correction_z = result['correction']
        self.assertLess(correction_z, 0.0)
        self.assertAlmostEqual(correction_x, 0.0, places=6)

    def test_a_hull_driving_into_another_loses_speed(self):
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0, vz=8.0),
                                             [_body(2, 0.0, 3.0)])
        self.assertLess(result['delta_velocity'][1], 0.0)

    def test_a_dead_hull_is_not_an_obstacle(self):
        other = _body(2, 0.0, 3.0)
        other['alive'] = False
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0), [other])
        self.assertEqual(result['correction'], (0.0, 0.0))

    def test_a_hull_on_another_level_is_not_an_obstacle(self):
        other = _body(2, 0.0, 3.0)
        other['y'] = 20.0
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0), [other])
        self.assertEqual(result['correction'], (0.0, 0.0))

    def test_a_hull_never_collides_with_itself(self):
        result = tank_collision.resolve_tank(_body(1, 0.0, 0.0),
                                             [_body(1, 0.0, 0.0)])
        self.assertEqual(result['correction'], (0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
