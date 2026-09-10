from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import spotting


class SpottingTests(unittest.TestCase):

    def test_no_skill_memory_uses_the_guaranteed_ten_second_bound(self):
        self.assertEqual(10.0, spotting.SPOT_MEMORY_SECONDS)

    def test_base_camouflage_matches_computeBaseInvisibility(self):
        # #1513 returns (moving, still) and adds the paint bonus last.
        moving, still = spotting.base_camouflage(
            0.288, 0.300, crew_factor=0.57,
            invisibility_factor=1.0, paint_bonus=0.03)

        self.assertAlmostEqual(0.288 * 0.57 + 0.03, moving)
        self.assertAlmostEqual(0.300 * 0.57 + 0.03, still)

    def test_the_aspect_applies_before_the_shot_and_foliage_terms(self):
        # getInvisibility: (base + additive) * multiplier.
        result = spotting.effective_camouflage(
            (0.20, 0.30), moving=False, additive=0.10, multiplier=1.0,
            shot_factor=0.25, fired_recently=True,
            foliage_bonus=0.15)

        self.assertAlmostEqual((0.30 + 0.10) * 0.25 + 0.15, result)

    def test_firing_scales_paint_and_net_as_the_exact_gui_consumer_does(self):
        # VehicleParams.__getInvisibilityValues consumes the complete
        # getClientInvisibility pair, then applies invisibilityFactorAtShot.
        # 3% paint plus 10% net becomes 3.25% at a 0.25 shot factor.
        result = spotting.effective_camouflage(
            (0.03, 0.03), moving=False, additive=0.10,
            shot_factor=0.25, fired_recently=True)
        self.assertAlmostEqual(0.0325, result)

    def test_geometry_changes_preserve_the_existing_concealment_tuning(self):
        self.assertEqual(0.15, spotting.FOLIAGE_CAMOUFLAGE_PER_VOLUME)
        self.assertEqual(0.60, spotting.FOLIAGE_CAMOUFLAGE_LIMIT)
        self.assertEqual(0.95, spotting.CAMOUFLAGE_LIMIT)
        self.assertAlmostEqual(0.60, spotting.effective_camouflage(
            (0.0, 0.0), foliage_bonus=0.95))
        self.assertAlmostEqual(0.95, spotting.effective_camouflage(
            (0.0, 0.40), additive=0.10, foliage_bonus=0.80))

    def test_one_owner_publishes_the_spotting_ray_geometry(self):
        from gui.mods.offline_lan_0922 import foliage
        self.assertEqual(spotting.OBSERVER_EYE_HEIGHT, foliage.OBSERVER_EYE_HEIGHT)
        self.assertEqual(spotting.TARGET_CHECK_HEIGHT, foliage.TARGET_CHECK_HEIGHT)

    def test_detection_distance_keeps_floor_and_ceiling(self):
        self.assertEqual(67.5, spotting.detection_distance(400.0, 0.95))
        self.assertEqual(225.0, spotting.detection_distance(400.0, 0.5))
        self.assertEqual(445.0, spotting.detection_distance(700.0, 0.0))
        self.assertEqual(565.0, spotting.VEHICLE_AOI_RADIUS)
        self.assertEqual(5.0, spotting.VEHICLE_AOI_HYSTERESIS_MARGIN)
        self.assertTrue(spotting.is_detected(50.0, 50.0, 0.95, False))
        self.assertFalse(spotting.is_detected(445.01, 700.0, 0.0, True))


if __name__ == '__main__':
    unittest.main()
