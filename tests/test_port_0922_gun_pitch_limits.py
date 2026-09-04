import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import gun_pitch_limits  # noqa: E402


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
        with self.assertRaisesRegex(ValueError, 'no yaw curves'):
            gun_pitch_limits.calc_pitch_limits(
                0.0, {'absolute': (-0.35, 0.15)})


if __name__ == '__main__':
    unittest.main()
