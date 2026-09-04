import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import hull_aiming  # noqa: E402


class HullAiming1513RulesTests(unittest.TestCase):
    @staticmethod
    def _descriptor(minimum=-4.0, maximum=2.0):
        gun = {'pitchLimits': {'absolute': (
            math.radians(minimum), math.radians(maximum))}}
        return {'gun': gun}

    def test_static_yaw_uses_destroyed_movement_and_switching_gates(self):
        static = 0.0
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, engine_destroyed=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, track_destroyed=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, overturned=True))
        self.assertTrue(hull_aiming.static_yaw_locked(static, moving=True))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, siege_state=hull_aiming.SWITCHING_ON))
        self.assertTrue(hull_aiming.static_yaw_locked(
            static, siege_state=hull_aiming.SWITCHING_OFF))
        # A critical (yellow) engine and stationary hull rotation do not set
        # any of the native destroyed/moving inputs.
        self.assertFalse(hull_aiming.static_yaw_locked(static))
        self.assertFalse(hull_aiming.static_yaw_locked(None, moving=True))

    def test_static_pitch_ignores_tracks_and_movement(self):
        static = math.radians(-1.0)
        self.assertFalse(hull_aiming.static_pitch_locked(static))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, engine_destroyed=True))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, overturned=True))
        self.assertTrue(hull_aiming.static_pitch_locked(
            static, siege_state=hull_aiming.SWITCHING_ON))

    def test_four_vehicle_flat_combined_envelopes_are_derived(self):
        strv_min, strv_min_ok = hull_aiming.minimal_correction(
            math.radians(-15.0), math.radians(-4.0), math.radians(2.0),
            math.radians(-11.0), math.radians(11.0))
        strv_max, strv_max_ok = hull_aiming.minimal_correction(
            math.radians(13.0), math.radians(-4.0), math.radians(2.0),
            math.radians(-11.0), math.radians(11.0))
        udes_max, udes_max_ok = hull_aiming.minimal_correction(
            math.radians(14.0), math.radians(-20.0), 0.0,
            0.0, math.radians(14.0))

        self.assertTrue(strv_min_ok)
        self.assertTrue(strv_max_ok)
        self.assertTrue(udes_max_ok)
        self.assertAlmostEqual(math.radians(-11.0), strv_min)
        self.assertAlmostEqual(math.radians(11.0), strv_max)
        self.assertAlmostEqual(math.radians(14.0), udes_max)
        unused, reachable = hull_aiming.minimal_correction(
            math.radians(14.1), math.radians(-20.0), 0.0,
            0.0, math.radians(14.0))
        self.assertFalse(reachable)

    def test_active_descriptor_exposes_relative_gun_pitch_limits(self):
        minimum, maximum = hull_aiming.absolute_pitch_limits(
            self._descriptor())

        self.assertAlmostEqual(math.radians(-4.0), minimum)
        self.assertAlmostEqual(math.radians(2.0), maximum)

    def test_flat_target_uses_gun_travel_before_hydraulic_travel(self):
        minimum, maximum = hull_aiming.absolute_pitch_limits(
            self._descriptor())

        correction, reachable = hull_aiming.world_target_correction(
            math.radians(-3.0), 0.0, minimum, maximum,
            math.radians(-11.0), math.radians(11.0))

        self.assertTrue(reachable)
        self.assertEqual(0.0, correction)

    def test_slope_is_removed_before_combined_pitch_is_resolved(self):
        minimum, maximum = hull_aiming.absolute_pitch_limits(
            self._descriptor())

        correction, reachable = hull_aiming.world_target_correction(
            math.radians(-10.0), math.radians(5.0), minimum, maximum,
            math.radians(-11.0), math.radians(11.0))

        self.assertTrue(reachable)
        self.assertAlmostEqual(math.radians(-11.0), correction)

    def test_combined_pitch_edge_clamps_and_reports_reachability(self):
        minimum, maximum = hull_aiming.absolute_pitch_limits(
            self._descriptor())

        edge, edge_ok = hull_aiming.world_target_correction(
            math.radians(-15.0), 0.0, minimum, maximum,
            math.radians(-11.0), math.radians(11.0))
        beyond, beyond_ok = hull_aiming.world_target_correction(
            math.radians(-15.1), 0.0, minimum, maximum,
            math.radians(-11.0), math.radians(11.0))

        self.assertTrue(edge_ok)
        self.assertAlmostEqual(math.radians(-11.0), edge)
        self.assertFalse(beyond_ok)
        self.assertAlmostEqual(math.radians(-11.0), beyond)

    def test_slew_uses_descriptor_radians_per_second(self):
        self.assertAlmostEqual(
            math.radians(7.5),
            hull_aiming.slew(
                0.0, math.radians(11.0), math.radians(7.5), 1.0))
        self.assertAlmostEqual(
            math.radians(11.0),
            hull_aiming.slew(
                math.radians(7.5), math.radians(11.0),
                math.radians(7.5), 1.0))

    def test_static_pitch_crossing_doubles_then_obeys_turret_time(self):
        static = math.radians(-1.0)
        current = math.radians(-3.0)
        desired = math.radians(1.0)
        crossed = hull_aiming.gun_pitch_step(
            current, desired, static, math.radians(1.0), 1.0)
        coordinated = hull_aiming.gun_pitch_step(
            current, desired, static, math.radians(10.0), 0.5, 2.0)

        self.assertAlmostEqual(math.radians(-1.0), crossed)
        self.assertAlmostEqual(math.radians(-2.0), coordinated)

    def test_pitch_slew_clamps_raw_target_after_the_native_epsilon_gate(self):
        limits = (math.radians(-4.0), math.radians(2.0))
        self.assertEqual(
            limits[1], hull_aiming.gun_pitch_step(
                math.radians(20.0), math.radians(20.0), None,
                math.radians(1.0), 0.01, angle_limits=limits))
        self.assertEqual(
            math.radians(-3.0), hull_aiming.gun_pitch_step(
                math.radians(-3.0), math.radians(1.0), None,
                0.0, 10.0, angle_limits=limits))

    def test_pitch_slew_returns_limit_instead_of_raw_target(self):
        limits = (math.radians(-20.0), math.radians(5.0))
        self.assertAlmostEqual(
            limits[1], hull_aiming.gun_pitch_step(
                math.radians(4.5), math.radians(23.0), None,
                math.radians(30.0), 1.0 / 30.0, angle_limits=limits))

    def test_pitch_slew_remains_bounded_across_repeated_steps(self):
        limits = (math.radians(-20.0), math.radians(5.0))
        pitch = 0.0
        for unused in range(30):
            pitch = hull_aiming.gun_pitch_step(
                pitch, math.radians(23.0), None, math.radians(20.0),
                1.0 / 30.0, angle_limits=limits)
            self.assertGreaterEqual(pitch, limits[0])
            self.assertLessEqual(pitch, limits[1])
        self.assertAlmostEqual(limits[1], pitch)


if __name__ == '__main__':
    unittest.main()
