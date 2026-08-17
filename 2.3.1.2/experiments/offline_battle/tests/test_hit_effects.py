import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

package_stub.load('combat_rules')
damage = package_stub.load('damage')
hit_effects = package_stub.load('hit_effects')


class EffectGroupTests(unittest.TestCase):
    def test_a_ricochet(self):
        self.assertEqual(hit_effects.effect_group(damage.RICOCHET, []),
                         'armorRicochet')

    def test_a_crit_wins_over_the_pierce(self):
        self.assertEqual(
            hit_effects.effect_group(damage.PIERCED, ['rightTrackHealth']),
            'armorCriticalHit')

    def test_a_plain_pierce(self):
        self.assertEqual(hit_effects.effect_group(damage.PIERCED, []),
                         'armorHit')

    def test_an_unpierced_hit(self):
        self.assertEqual(hit_effects.effect_group(damage.NOT_PIERCED, []),
                         'armorResisted')

    def test_a_track_absorb_with_a_crit(self):
        self.assertEqual(
            hit_effects.effect_group(damage.TRACK_ABSORBED,
                                     ['leftTrackHealth']),
            'armorCriticalHit')

    def test_no_result_no_effect(self):
        self.assertIsNone(hit_effects.effect_group(None, []))


if __name__ == '__main__':
    unittest.main()
