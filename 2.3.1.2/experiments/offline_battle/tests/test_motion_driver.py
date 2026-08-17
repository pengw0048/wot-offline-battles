import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

# The same inert destructibles stand-in the other tests install, so
# world_collision binds identical fakes whichever test module loads first.
package_stub.stub('destructibles_sensor',
                  _catalog_soft_static_path=lambda *a, **k: False,
                  _diagnostic_static_recast_1513=lambda *a, **k: None,
                  _try_destroy_solid_hit=lambda *a, **k: False,
                  _vehicle_hull_bbox=lambda descriptor: (
                      (-1.4, -0.5, -3.0), (1.4, 1.2, 3.2), 0))
motion_driver = package_stub.load('motion_driver')


class ResolveDriveTests(unittest.TestCase):
    """The 0.9.22 drive gate, checked case by case."""

    def test_idle_coasts_without_a_handbrake(self):
        self.assertEqual(
            motion_driver.resolve_drive(0, 0, False, False, False, 1.0, 1.0),
            (0.0, 0.0, False))

    def test_the_real_handbrake_input_reaches_the_law(self):
        unused_m, unused_r, handbrake = motion_driver.resolve_drive(
            1, 0, True, False, False, 1.0, 1.0)
        self.assertTrue(handbrake)

    def test_a_thrown_track_locks_the_tracks(self):
        self.assertEqual(
            motion_driver.resolve_drive(1, 1, False, True, False, 1.0, 1.0),
            (0.0, 0.0, True))

    def test_a_dead_engine_only_coasts(self):
        self.assertEqual(
            motion_driver.resolve_drive(1, 1, False, False, True, 1.0, 1.0),
            (0.0, 0.0, False))

    def test_damage_scales_the_intent_once(self):
        movement, rotation, handbrake = motion_driver.resolve_drive(
            1, -1, False, False, False, 0.5, 4.0 / 7.0)
        self.assertAlmostEqual(movement, 0.5)
        self.assertAlmostEqual(rotation, -4.0 / 7.0)
        self.assertFalse(handbrake)


class SetInputTests(unittest.TestCase):
    def test_stores_the_handbrake(self):
        driver = motion_driver.MotionDriver.__new__(
            motion_driver.MotionDriver)
        driver.set_input(1, -1, True)
        self.assertEqual((driver._movement, driver._rotation,
                          driver._handbrake), (1, -1, True))
        driver.set_input(0, 0)
        self.assertFalse(driver._handbrake)


if __name__ == '__main__':
    unittest.main()
