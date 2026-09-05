"""Check the public shell-roll reconstruction against an independent CDF.

These tests establish the chosen truncated normal law. They cannot identify
the unpublished #1513 server's precise generator or outlier policy.
"""

import math
from pathlib import Path
import random
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import combat_rules, device_damage


def _normal_cdf(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _factor_cdf(value):
    lower, upper = _normal_cdf(-3.0), _normal_cdf(3.0)
    return (_normal_cdf((value - 1.0) * 12.0) - lower) / (upper - lower)


class ShellRandomizationTests(unittest.TestCase):

    def test_public_interval_and_nonuniform_density(self):
        generator = random.Random(1513)
        factors = [combat_rules.sample_penetration_factor(generator.gauss)
                   for unused in range(100000)]
        self.assertGreaterEqual(min(factors), 0.75)
        self.assertLessEqual(max(factors), 1.25)
        for lower, upper in ((0.75, 0.80), (0.95, 1.05), (1.20, 1.25)):
            with self.subTest(interval=(lower, upper)):
                observed = sum(lower <= value < upper for value in factors)
                expected = _factor_cdf(upper) - _factor_cdf(lower)
                self.assertAlmostEqual(expected, observed / len(factors),
                                       delta=0.004)
        # Old uniform sampling gives 20%, whereas the specified normal
        # interval gives about 45.3%. This catches a flat RNG regression.
        central = sum(0.95 <= value <= 1.05 for value in factors)
        self.assertGreater(central / len(factors), 0.44)

    def test_outliers_are_resampled_without_endpoint_pileup(self):
        draws = mock.Mock(side_effect=(0.5, 1.5, 1.02))
        self.assertEqual(1.02, combat_rules.sample_penetration_factor(draws))
        self.assertEqual([mock.call(1.0, 1.0 / 12.0)] * 3, draws.call_args_list)
        for invalid in (float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                combat_rules.sample_shell_value(invalid)
            with self.assertRaises(ValueError):
                combat_rules.sample_penetration_factor(
                    lambda unused_mean, unused_sigma: invalid)

    def test_vehicle_damage_and_track_armor_channel_share_the_same_roll_law(self):
        shell = {'damage': (400.0, 165.0), 'kind': 'ARMOR_PIERCING'}
        shot = {'shell': shell}
        # Two discarded outliers, then a legal full precision damage roll.
        with mock.patch.object(combat_rules.random, 'gauss',
                               side_effect=(200.0, 600.0, 333.7)) as draw:
            rolled = combat_rules.shell_damage_roll(shot)
        self.assertEqual(333.7, rolled)
        self.assertEqual([mock.call(400.0, 400.0 / 12.0)] * 3, draw.call_args_list)
        with mock.patch.object(combat_rules.random, 'gauss',
                               side_effect=AssertionError('must reuse roll')):
            self.assertEqual(333, combat_rules.damage(
                shot, 2, 100.0, rolled_damage=rolled))
            self.assertEqual(0, combat_rules.damage(
                shot, 1, 100.0, rolled_damage=rolled))
        with mock.patch.object(combat_rules.random, 'gauss',
                               return_value=333.7) as draw:
            self.assertEqual(333.7, device_damage.module_damage_roll(shell, 0))
        draw.assert_called_once_with(400.0, 400.0 / 12.0)

    def test_he_direct_and_splash_damage_draw_from_the_same_distribution(self):
        shot = {'shell': {'damage': (400.0, 165.0),
                          'kind': 'HIGH_EXPLOSIVE'}}
        for direct in (True, False):
            with self.subTest(direct=direct), mock.patch.object(
                    combat_rules.random, 'gauss', return_value=400.0) as draw:
                damage = (combat_rules.damage(shot, 1, 100.0) if direct else
                          combat_rules.he_splash_damage(shot, 100.0, 0.0))
                self.assertEqual(70, damage)
                draw.assert_called_once_with(400.0, 400.0 / 12.0)

    def test_type59_track_and_sloped_hull_show_both_probability_errors(self):
        # Descriptor-derived Type 59 values: 20 mm track ignores hit angle,
        # followed by a 100 mm hull at 60 degrees. Both verdicts go through
        # the production layer resolver; probabilities use the independent CDF.
        track = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=0.0, useHitAngle=False,
            mayRicochet=False, kind=10, collideOnceOnly=True)
        hull = types.SimpleNamespace(armor=100.0, vehicleDamageFactor=1.0)
        collisions = [types.SimpleNamespace(
            dist=distance, hitAngleCos=0.5, matInfo=material, compName=component)
            for distance, material, component in (
                (1.0, track, 'vehicleChassis'), (1.2, hull, 'vehicleHull'))]
        for kind, mean, normalization in (
                ('ARMOR_PIERCING', 181.0, 5.0),
                ('ARMOR_PIERCING_CR', 241.0, 2.0)):
            with self.subTest(kind=kind):
                effective = 100.0 / math.cos(math.radians(60 - normalization))
                threshold = (20.0 + effective) / mean
                shot = {'piercingPower': (mean, mean), 'maxDistance': 720.0,
                        'shell': {'kind': kind, 'caliber': 100.0}}
                for factor, expected in ((threshold - 1e-5, 1),
                                         (threshold + 1e-5, 2)):
                    contact = combat_rules.resolve_armor_contact(
                        shot, 50.0, collisions, penetration_factor=factor)
                    self.assertEqual(expected, contact['result'])
                corrected = 1.0 - _factor_cdf(threshold)
                old_uniform = (1.25 - threshold) / 0.5
                if kind == 'ARMOR_PIERCING':
                    self.assertGreater(old_uniform - corrected, 0.12)
                else:
                    self.assertGreater(corrected - old_uniform, 0.14)


if __name__ == '__main__':
    unittest.main()
