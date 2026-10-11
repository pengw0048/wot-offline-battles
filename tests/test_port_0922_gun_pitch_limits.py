import math
import os
import struct
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SCRIPTS = os.path.join(ROOT, 'src', 'res', 'scripts', 'client')
sys.path.insert(0, os.path.join(
    CLIENT_SCRIPTS, 'gui', 'mods', 'offline_lan_0922'))

import gun_pitch_limits  # noqa: E402


def _project(raw_points):
    """Apply #1513's Packed XML fraction/degree projection."""
    return tuple(
        (2.0 * math.pi * fraction, math.radians(pitch_degrees))
        for fraction, pitch_degrees in raw_points)


def _limits(minimum, maximum):
    return {
        'minPitch': _project(minimum),
        'maxPitch': _project(maximum),
        # This deliberately wrong envelope proves it is not consumed here.
        'absolute': (12.0, 13.0),
    }


def _reference_limits(turret_yaw, limits):
    """Retain the original per-operand x86 float rounding as an oracle."""
    def single(value):
        return struct.unpack('<f', struct.pack('<f', float(value)))[0]

    def add(left, right):
        return single(single(left) + single(right))

    def subtract(left, right):
        return single(single(left) - single(right))

    def multiply(left, right):
        return single(single(left) * single(right))

    def divide(left, right):
        return single(single(left) / single(right))

    yaw = single(turret_yaw)
    if yaw < 0.0:
        yaw = add(yaw, single(2.0 * math.pi))
    result = []
    for name in ('minPitch', 'maxPitch'):
        points = tuple((single(x), single(y)) for x, y in limits[name])
        lower, upper = 0, len(points) - 1
        while upper - lower > 1:
            middle = (lower + upper) // 2
            if yaw > points[middle][0]:
                lower = middle
            else:
                upper = middle
        span = subtract(points[upper][0], points[lower][0])
        fraction = divide(subtract(yaw, points[lower][0]), span)
        result.append(add(
            multiply(points[lower][1], subtract(1.0, fraction)),
            multiply(points[upper][1], fraction)))
    return tuple(result)


class GunPitchLimits1513OracleTests(unittest.TestCase):
    def test_type59_front_rear_transition_and_negative_yaw(self):
        limits = _limits(
            ((0.0, -20.0), (1.0, -20.0)),
            ((0.0, 7.0), (0.366894, 7.0),
             (0.420751, 7.0), (0.430556, 6.0),
             (0.569444, 6.0), (0.584101, 7.0),
             (0.633106, 7.0), (1.0, 7.0)))

        self.assertEqual(
            (-0.3490658402442932, 0.12217304855585098),
            gun_pitch_limits.calc_pitch_limits(0.0, limits))
        self.assertEqual(
            (-0.3490658402442932, 0.10471975803375244),
            gun_pitch_limits.calc_pitch_limits(math.pi, limits))
        self.assertEqual(
            (-0.3490658402442932, 0.10471975803375244),
            gun_pitch_limits.calc_pitch_limits(-math.pi, limits))
        self.assertEqual(
            (-0.3490658402442932, 0.11344636976718903),
            gun_pitch_limits.calc_pitch_limits(
                2.0 * math.pi * 0.4256535, limits))
        self.assertEqual(
            (-0.3490658402442932, 0.12217304855585098),
            gun_pitch_limits.calc_pitch_limits(2.0 * math.pi, limits))

    def test_chinese_t34_1_rear_zero_elevation(self):
        limits = _limits(
            ((0.0, -18.0), (1.0, -18.0)),
            ((0.0, 5.0), (0.297449, 5.0), (0.361111, 0.0),
             (0.638889, 0.0), (0.702551, 5.0), (1.0, 5.0)))

        self.assertEqual(
            (-0.3141592741012573, 0.0),
            gun_pitch_limits.calc_pitch_limits(math.pi, limits))
        self.assertEqual(
            (-0.3141592741012573, 0.0872664600610733),
            gun_pitch_limits.calc_pitch_limits(0.0, limits))

    def test_waffentrager_independent_minimum_and_maximum_node_grids(self):
        limits = _limits(
            ((0.0, -45.0), (0.333333, -45.0),
             (0.347222, -14.0), (0.652778, -14.0),
             (0.666667, -45.0), (1.0, -45.0)),
            ((0.0, 2.0), (0.138889, 2.0), (0.152778, 5.0),
             (0.847222, 5.0), (0.861111, 2.0), (1.0, 2.0)))

        self.assertEqual(
            (-0.24434609711170197, 0.0872664600610733),
            gun_pitch_limits.calc_pitch_limits(math.pi, limits))
        self.assertEqual(
            (-0.5148729085922241, 0.0872664675116539),
            gun_pitch_limits.calc_pitch_limits(
                2.0 * math.pi * ((0.333333 + 0.347222) * 0.5),
                limits))
        self.assertEqual(
            (-0.7853981852531433, 0.06108652055263519),
            gun_pitch_limits.calc_pitch_limits(
                2.0 * math.pi * ((0.138889 + 0.152778) * 0.5),
                limits))

    def test_absolute_envelope_cannot_replace_the_two_curves(self):
        with self.assertRaises(ValueError) as caught:
            gun_pitch_limits.calc_pitch_limits(
                0.0, {'absolute': (-0.35, 0.15)})
        self.assertIn('no yaw curves', str(caught.exception))

    def test_per_operation_rounding_at_curve_knots_and_adjacent_float32_yaws(self):
        limits = _limits(
            ((0.0, -45.0), (0.333333, -45.0),
             (0.347222, -14.0), (0.652778, -14.0),
             (0.666667, -45.0), (1.0, -45.0)),
            ((0.0, 2.0), (0.138889, 2.0), (0.152778, 5.0),
             (0.847222, 5.0), (0.861111, 2.0), (1.0, 2.0)))
        yaws = [index * math.pi / 180.0 for index in range(-360, 361)]
        for name in ('minPitch', 'maxPitch'):
            for yaw, unused_pitch in limits[name]:
                bits = struct.unpack('<I', struct.pack('<f', yaw))[0]
                for neighbor in (max(0, bits - 1), bits, bits + 1):
                    adjacent = struct.unpack('<f', struct.pack('<I', neighbor))[0]
                    yaws.extend((adjacent, adjacent - 2.0 * math.pi))
        for yaw in yaws:
            expected = _reference_limits(yaw, limits)
            actual = gun_pitch_limits.calc_pitch_limits(yaw, limits)
            self.assertEqual(struct.pack('<ff', *expected),
                             struct.pack('<ff', *actual), repr(yaw))

    def test_mutable_curve_nodes_are_read_again_on_every_solve(self):
        limits = {
            'minPitch': [[0.0, -0.3], [2.0 * math.pi, -0.3]],
            'maxPitch': [[0.0, 0.1], [2.0 * math.pi, 0.1]],
        }
        first = gun_pitch_limits.calc_pitch_limits(0.5, limits)
        limits['maxPitch'][0][1] = 0.2
        changed = gun_pitch_limits.calc_pitch_limits(0.5, limits)
        self.assertNotEqual(first, changed)
        self.assertEqual(_reference_limits(0.5, limits), changed)

    def test_distinct_double_nodes_that_collapse_to_float32_still_fail(self):
        limits = {
            'minPitch': ((1.0, -0.3), (1.0 + 1.0e-9, -0.2)),
            'maxPitch': ((0.0, 0.1), (2.0 * math.pi, 0.1)),
        }
        with self.assertRaises(ValueError) as caught:
            gun_pitch_limits.calc_pitch_limits(1.0, limits)
        self.assertIn('duplicate yaw nodes', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
