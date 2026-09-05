from pathlib import Path
import math
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import vehicle_physics


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class VehiclePhysicsDescriptorTests(unittest.TestCase):

    @staticmethod
    def _native_descriptor(power_hp, native_power):
        engine_name = 'selected-engine'
        return types.SimpleNamespace(
            physics={'enginePower': power_hp * 735.5},
            engine=_Strict1513Component(name=engine_name),
            type=_Strict1513Component(xphysics={
                'detailed': {'engines': {
                    engine_name: {'smplEnginePower': native_power}}}}),
            chassis=_Strict1513Component(rotationSpeed=0.75))

    def test_rotation_speed_reads_native_1513_chassis_attribute(self):
        descriptor = types.SimpleNamespace(
            physics={},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(0.75, params['rotSpd'])

    def test_zero_native_brake_force_keeps_track_grip_fallback(self):
        descriptor = types.SimpleNamespace(
            physics={'weight': 21000.0, 'brakeForce': 0.0},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(
            vehicle_physics.COHESION * vehicle_physics.GRAVITY,
            params['brakeDecel'])

    def test_selected_1513_xphysics_engine_power_replaces_generic_scaling(self):
        # Exact pinned-client values: Type 62's 430 hp descriptor engine is
        # overridden by physics/detailed/engines/.../smplEnginePower.
        descriptor = self._native_descriptor(430.0, 454.6309)

        params = vehicle_physics.derive_params(descriptor)
        effective_power = (params['powerW'] * vehicle_physics.POWER_FACTOR *
                           params['nativePowerRatio'])

        self.assertAlmostEqual(1.15, params['nativePowerRatio'], places=6)
        self.assertAlmostEqual(454630.9, effective_power, places=3)

    def test_missing_detailed_engine_power_keeps_generic_fallback(self):
        descriptor = types.SimpleNamespace(
            physics={'enginePower': 430.0 * 735.5},
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(1.0, params['nativePowerRatio'])

    def test_server_projection_keeps_donated_native_power_ratio(self):
        descriptor = types.SimpleNamespace(
            physics={
                'enginePower': 430.0 * 735.5,
                'nativePowerRatio': 1.15,
            },
            chassis=_Strict1513Component(rotationSpeed=0.75))

        params = vehicle_physics.derive_params(descriptor)

        self.assertEqual(1.15, params['nativePowerRatio'])


class VehiclePhysicsHardContactTests(unittest.TestCase):

    def test_candidate_yaws_keep_the_shared_glancing_order(self):
        candidates = vehicle_physics.hard_contact_candidate_yaws(0.2)

        for expected, actual in zip((0.75, -0.35, 1.2, -0.8), candidates):
            self.assertAlmostEqual(expected, actual)

    def test_first_glancing_contact_damps_and_advances_on_selected_yaw(self):
        speed, delta_x, delta_z = vehicle_physics.hard_contact_step(
            6.0, 0.04, grinding=False, slide_yaw=-0.55)
        expected = (6.0 * vehicle_physics.HARD_CONTACT_ENTRY_FACTOR *
                    vehicle_physics.HARD_CONTACT_SLIDE_DECAY ** 2.4)

        self.assertAlmostEqual(expected, speed)
        self.assertAlmostEqual(math.sin(-0.55) * expected * 0.04, delta_x)
        self.assertAlmostEqual(math.cos(-0.55) * expected * 0.04, delta_z)

    def test_continuing_glance_skips_the_first_contact_loss(self):
        speed = vehicle_physics.hard_contact_step(
            6.0, 0.04, grinding=True, slide_yaw=0.55)[0]

        self.assertAlmostEqual(
            6.0 * vehicle_physics.HARD_CONTACT_SLIDE_DECAY ** 2.4,
            speed)

    def test_fully_blocked_contact_uses_shared_brake_and_stop_threshold(self):
        speed, delta_x, delta_z = vehicle_physics.hard_contact_step(
            6.0, 0.04)

        self.assertAlmostEqual(
            6.0 * vehicle_physics.HARD_CONTACT_BRAKE_DECAY ** 2.4,
            speed)
        self.assertEqual((0.0, 0.0), (delta_x, delta_z))
        self.assertEqual(
            0.0, vehicle_physics.hard_contact_step(6.0, 0.1)[0])


class VehiclePhysicsPinnedClimbTests(unittest.TestCase):

    PINNED_VEHICLES = (
        # name, mass kg, rated hp, smplEnginePower, speed km/h, firm resistance
        ('Type 62', 21000.0, 430.0, 454.6309, 60.0, 0.5),
        ('T-34-85', 32000.0, 600.0, 634.3687, 54.0, 1.1),
        ('Maus', 188980.0, 1750.0, 1850.242, 20.0, 1.1),
        ('ISU-152', 49300.0, 700.0, 740.0968, 43.0, 1.1),
    )

    @staticmethod
    def _params(row, native_ratio=True):
        unused_name, mass, power_hp, native_power, speed_kmh, resistance = row
        params = dict(vehicle_physics._DEFAULTS)
        params.update({
            'mass': mass,
            'powerW': power_hp * 735.5,
            'speedFwd': speed_kmh / 3.6,
            'terrainResist': (resistance, resistance, resistance),
            'nativePowerRatio': (native_power /
                                 (power_hp * 735.5 * 0.00125)
                                 if native_ratio else 1.0),
        })
        return params

    @staticmethod
    def _climb_speed(params, degrees, seconds=30):
        speed = 0.0
        for unused in range(60 * seconds):
            speed = vehicle_physics.longitudinal_step(
                params, speed, 1.0, False, -math.radians(degrees),
                1.0 / 60.0)
        return speed * 3.6

    def test_native_detailed_power_improves_every_representative_climb(self):
        for row in self.PINNED_VEHICLES:
            with self.subTest(vehicle=row[0]):
                copied = self._climb_speed(self._params(row), 15.0)
                generic = self._climb_speed(
                    self._params(row, native_ratio=False), 15.0)
                self.assertGreater(copied, generic * 1.10)

    def test_pinned_maus_can_launch_below_its_25_degree_chassis_limit(self):
        maus = self.PINNED_VEHICLES[2]

        copied = self._climb_speed(self._params(maus), 24.0)
        generic = self._climb_speed(
            self._params(maus, native_ratio=False), 24.0)

        self.assertGreater(copied, 3.0)
        self.assertLess(generic, 1.0)

    def test_exact_1513_longitudinal_grip_curve_boundaries(self):
        self.assertAlmostEqual(
            1.0, vehicle_physics.longitudinal_slope_grip(0.0), places=12)
        self.assertAlmostEqual(
            1.0,
            vehicle_physics.longitudinal_slope_grip(math.radians(27.5)),
            places=12)
        self.assertAlmostEqual(
            0.1,
            vehicle_physics.longitudinal_slope_grip(math.radians(32.0)),
            places=12)
        self.assertAlmostEqual(
            0.1,
            vehicle_physics.longitudinal_slope_grip(math.radians(45.0)),
            places=12)

        # The executable interpolates the two curve points by normal.y, not
        # directly by the slope angle. The midpoint in normal.y is grip 0.55.
        midpoint_y = (
            vehicle_physics.SLOPE_GRIP_LNG_FULL_Y +
            vehicle_physics.SLOPE_GRIP_LNG_MIN_Y) / 2.0
        midpoint_angle = math.acos(midpoint_y)
        self.assertAlmostEqual(
            0.55,
            vehicle_physics.longitudinal_slope_grip(midpoint_angle),
            places=12)

    def test_exact_1513_full_grip_region_does_not_cut_drive_early(self):
        # The former fixed 0.54 cap stopped these vehicles before 27.5 degrees,
        # while #1513 still supplies full longitudinal grip at this boundary.
        expected_minimum_kmh = {
            'Type 62': 10.0,
            'T-34-85': 8.0,
            'ISU-152': 6.0,
        }
        for row in self.PINNED_VEHICLES:
            if row[0] == 'Maus':
                continue
            with self.subTest(vehicle=row[0]):
                self.assertGreater(
                    self._climb_speed(self._params(row), 27.5),
                    expected_minimum_kmh[row[0]])

        # Once the native curve has fallen to its 0.1 endpoint, the same
        # vehicles cannot continue climbing under the copied force law.
        for row in self.PINNED_VEHICLES:
            if row[0] == 'Maus':
                continue
            with self.subTest(vehicle=row[0]):
                self.assertLess(
                    self._climb_speed(self._params(row), 32.0), 0.0)


class VehiclePhysicsCoastTests(unittest.TestCase):

    def setUp(self):
        self.params = dict(vehicle_physics._DEFAULTS)
        # Exact #1513 Type 62 values used by the current copied integrator.
        self.params.update({
            'mass': 21000.0,
            'speedFwd': 60.0 / 3.6,
            'terrainResist': (0.5, 0.6, 1.3),
            'specificFriction': 0.6867,
        })

    def _coast(self, speed, slope_degrees, dt):
        return vehicle_physics.longitudinal_step(
            self.params, speed, 0.0, False,
            math.radians(slope_degrees), dt)

    def _flat_stop(self, frame_rate):
        speed = self.params['speedFwd']
        dt = 1.0 / frame_rate
        distance = 0.0
        elapsed = 0.0
        while speed > 0.0 and elapsed < 5.0:
            speed = self._coast(speed, 0.0, dt)
            # BattleRuntime integrates the post-step speed into the pose.
            distance += speed * dt
            elapsed += dt
        return elapsed, distance

    def test_type62_flat_release_stops_in_the_conservative_calibrated_window(self):
        results = [self._flat_stop(rate) for rate in (24, 30, 60, 120)]

        for elapsed, distance in results:
            self.assertGreaterEqual(elapsed, 1.50)
            self.assertLessEqual(elapsed, 1.60)
            self.assertGreaterEqual(distance, 12.5)
            self.assertLessEqual(distance, 12.9)
        self.assertLess(
            max(row[1] for row in results) -
            min(row[1] for row in results),
            0.30)

    def test_parkable_descent_brakes_and_a_steeper_one_slides(self):
        # The 2.3-reviewed coast law: every slope the parked hold can keep
        # brakes like the flat; past the perch limit gravity owns the descent.
        self.assertLess(self._coast(5.0, 15.0, 0.1), 5.0)
        self.assertGreater(self._coast(5.0, 15.0, 0.1),
                           self._coast(5.0, 0.0, 0.1))
        self.assertGreater(self._coast(5.0, 28.0, 0.1), 5.0)

    def test_static_hold_and_handbrake_are_unchanged(self):
        self.assertEqual(0.0, self._coast(0.0, 25.0, 0.1))
        self.assertGreater(self._coast(0.0, 30.0, 0.1), 0.0)
        self.assertGreater(
            vehicle_physics.brake_force(self.params, True),
            vehicle_physics.brake_force(self.params, False))
        self.assertEqual(0.0, vehicle_physics.longitudinal_step(
            self.params, 0.0, 0.0, False, math.radians(30.0), 0.1,
            handbrake=True))

    def test_downhill_neutral_coast_is_frame_rate_invariant(self):
        results = []
        for frame_rate in (24, 30, 60, 120):
            speed = 5.0
            dt = 1.0 / frame_rate
            for unused in range(frame_rate):
                speed = self._coast(speed, 28.0, dt)
            results.append(speed)

        self.assertGreater(results[0], 7.0)
        self.assertLess(max(results) - min(results), 1e-9)

    def test_released_throttle_bleeds_the_gravity_overspeed(self):
        speed = self.params['speedFwd'] * 1.04
        elapsed = 0.0
        while speed > 0.0 and elapsed < 6.0:
            speed = self._coast(speed, 4.0, 1.0 / 30.0)
            elapsed += 1.0 / 30.0

        self.assertEqual(0.0, speed)
        self.assertLess(elapsed, 2.5)

    def test_a_driven_descent_keeps_the_gravity_overspeed(self):
        speed = self.params['speedFwd']
        for unused in range(30 * 20):
            speed = vehicle_physics.longitudinal_step(
                self.params, speed, 1.0, False, math.radians(20.0),
                1.0 / 30.0)

        self.assertAlmostEqual(self.params['speedFwd'] * 1.05, speed,
                               places=3)


class VehiclePhysicsAirborneTests(unittest.TestCase):

    def test_fall_damage_has_safe_threshold_and_signed_speed_symmetry(self):
        self.assertEqual(0, vehicle_physics.fall_damage(1000, 10.0))
        self.assertEqual(30, vehicle_physics.fall_damage(1000, 11.0))
        self.assertEqual(30, vehicle_physics.fall_damage(1000, -11.0))

    def test_flat_ledge_follow_gap_does_not_grow_with_road_speed(self):
        slow = vehicle_physics.ground_follow_gap(4.0, 0.0, 0.1)
        fast = vehicle_physics.ground_follow_gap(15.0, 0.0, 0.1)

        self.assertEqual(slow, fast)
        self.assertAlmostEqual(
            vehicle_physics.GROUND_SAMPLE_TOLERANCE +
            (vehicle_physics.GRAVITY +
             vehicle_physics.SUSPENSION_FOLLOW_ACCEL) * 0.1 * 0.1, fast)

    def test_ordinary_cliff_face_leaves_the_ground_at_any_frame_rate(self):
        # The reported bug: a hull driven off a cliff was pulled onto the face
        # every frame instead of flying. Separation must depend on the ground,
        # not on the frame rate, so a 35 degree break outruns the allowance at
        # the visible client's frame steps and at the worker's 0.1 s step.
        for speed in (10.0, 15.0):
            for step in (1.0 / 60.0, 1.0 / 30.0, 0.1):
                gap = vehicle_physics.ground_follow_gap(speed, 0.0, step)
                face_drop = speed * step * math.tan(math.radians(35.0))

                self.assertGreater(face_drop, gap)

    def test_gentle_ground_still_supports_the_hull(self):
        # A five degree undulation must not throw the hull into the air; only
        # ground that curves away faster than the springs can follow ends
        # support.
        speed = 10.0
        for step in (1.0 / 60.0, 1.0 / 30.0, 0.1):
            gap = vehicle_physics.ground_follow_gap(speed, 0.0, step)
            gentle_drop = speed * step * math.tan(math.radians(5.0))

            self.assertLess(gentle_drop, gap)

    def test_continuous_downhill_tangent_extends_the_follow_gap(self):
        pitch = math.atan(0.5)

        forward = vehicle_physics.ground_follow_gap(10.0, pitch, 0.1)
        reverse = vehicle_physics.ground_follow_gap(-10.0, -pitch, 0.1)

        self.assertGreater(
            forward,
            vehicle_physics.ground_follow_gap(10.0, 0.0, 0.1))
        self.assertAlmostEqual(forward, reverse)

    def test_continuous_downhill_drop_fits_supported_frame_steps(self):
        speed = 15.0
        pitch = math.atan(0.5)

        for step in (1.0 / 60.0, 1.0 / 30.0, 0.1, 0.2):
            expected_drop = speed * math.tan(pitch) * step
            self.assertGreaterEqual(
                vehicle_physics.ground_follow_gap(speed, pitch, step),
                expected_drop)

    def test_launch_velocity_handles_forward_and_reverse_uphill_travel(self):
        pitch = math.radians(20.0)

        forward = vehicle_physics.launch_vertical_speed(12.0, -pitch)
        reverse = vehicle_physics.launch_vertical_speed(-12.0, pitch)

        self.assertGreater(forward, 0.0)
        self.assertAlmostEqual(forward, reverse)
        self.assertAlmostEqual(12.0 * math.tan(pitch), forward)

    def test_launch_velocity_keeps_the_descent_rate_of_the_lost_surface(self):
        # Zeroing this made the hull hang at the lip of every drop and then
        # fall from rest; the arc has to continue the slope it left.
        pitch = math.radians(20.0)

        descending = vehicle_physics.launch_vertical_speed(12.0, pitch)

        self.assertAlmostEqual(-12.0 * math.tan(pitch), descending)

    def test_landing_impact_ignores_a_slope_the_hull_merely_follows(self):
        speed = 15.0
        pitch = math.radians(30.0)
        rate = -speed * math.tan(pitch)

        self.assertEqual(0.0, vehicle_physics.landing_impact_speed(
            rate, rate, speed))
        self.assertAlmostEqual(
            abs(rate) * math.cos(pitch),
            vehicle_physics.landing_impact_speed(2.0 * rate, rate, speed))

    def test_landing_impact_on_flat_ground_is_the_vertical_speed(self):
        self.assertAlmostEqual(
            14.0, vehicle_physics.landing_impact_speed(-14.0, 0.0, 20.0))
        self.assertEqual(
            0.0, vehicle_physics.landing_impact_speed(3.0, 0.0, 20.0))

    def test_landing_track_damage_precedes_the_health_threshold(self):
        maximum = 200.0

        self.assertEqual(0.0, vehicle_physics.fall_track_damage(maximum, 5.0))
        self.assertFalse(vehicle_physics.landing_is_damaging(5.0))
        gentle = vehicle_physics.fall_track_damage(maximum, 9.0)

        # A ~3 m drop leaves a damaged suspension and full health.
        self.assertEqual('critical', device_damage.device_state(
            maximum - gentle, maximum))
        self.assertEqual(0, vehicle_physics.fall_damage(1000, 9.0))
        self.assertTrue(vehicle_physics.landing_is_damaging(9.0))
        self.assertEqual(
            maximum, vehicle_physics.fall_track_damage(maximum, 12.0))
        self.assertEqual(
            maximum, vehicle_physics.fall_track_damage(maximum, 40.0))


if __name__ == '__main__':
    unittest.main()
