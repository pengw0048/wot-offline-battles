from pathlib import Path
import math
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

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


class VehiclePhysicsSuspensionTrialTests(unittest.TestCase):

    @staticmethod
    def _descriptor():
        chassis_name = 'trial-chassis'
        detailed_chassis = {
            'roadWheelPositions': (-2.0, -1.0, 0.0, 1.0, 2.0),
            'stiffnessFactors': (1.0, 1.0, 1.0, 1.0, 1.0),
            'stiffness0': 1.0,
            'stiffness1': 1.0,
            'damping': 0.2,
            'bodyHeight': 0.95,
            'hullInertiaFactors': (1.0, 1.0, 1.8),
            'wheelRadius': 0.35,
        }
        return types.SimpleNamespace(
            physics={
                'weight': 21000.0,
                'enginePower': 430.0 * 735.5,
                'trackCenterOffset': 1.2,
            },
            type=_Strict1513Component(xphysics={
                'detailed': {'chassis': {
                    chassis_name: detailed_chassis,
                }},
            }),
            chassis=_Strict1513Component(
                name=chassis_name,
                rotationSpeed=0.75,
                hullPosition=(0.0, 1.0, 0.0),
                hitTester=_Strict1513Component(bbox=(
                    (-1.4, -0.5, -2.6), (1.4, 0.7, 2.6)))),
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.2, -0.65, -2.2), (1.2, 0.65, 2.2)))))

    @staticmethod
    def _state(**overrides):
        state = {
            'height': 0.0,
            'vertical_velocity': 0.0,
            'pitch': 0.0,
            'pitch_velocity': 0.0,
            'roll': 0.0,
            'roll_velocity': 0.0,
        }
        state.update(overrides)
        return state

    @staticmethod
    def _plane_samples(params, x_gradient=0.0, z_gradient=0.0):
        ground = tuple(
            x_gradient * spring['x'] + z_gradient * spring['z']
            for spring in params['springs'])
        pseudo_ground = tuple(
            x_gradient * contact['x'] + z_gradient * contact['z']
            for contact in params['pseudo_contacts'])
        return ground, pseudo_ground

    def setUp(self):
        self.params = vehicle_physics.derive_suspension_params(
            self._descriptor())

    def test_parameter_projection_uses_ten_springs_and_twelve_contacts(self):
        self.assertEqual(10, len(self.params['springs']))
        self.assertEqual(12, len(self.params['pseudo_contacts']))
        self.assertEqual(
            {-1.2, 1.2},
            {spring['x'] for spring in self.params['springs']})
        self.assertEqual(
            8,
            sum(contact['kind'] == 'track'
                for contact in self.params['pseudo_contacts']))
        self.assertEqual(
            4,
            sum(contact['kind'] == 'body'
                for contact in self.params['pseudo_contacts']))

        points = vehicle_physics.suspension_world_points(
            self.params, (10.0, 99.0, 20.0), math.pi * 0.5)
        self.assertAlmostEqual(8.0, points[0][0])
        self.assertAlmostEqual(21.2, points[0][1])
        self.assertEqual(
            12,
            len(vehicle_physics.suspension_pseudo_world_points(
                self.params, (10.0, 99.0, 20.0), math.pi * 0.5)))

    def test_missing_client_geometry_or_detailed_values_are_rejected(self):
        cases = []
        descriptor = self._descriptor()
        descriptor.physics.pop('weight')
        cases.append(('weight', descriptor))
        descriptor = self._descriptor()
        descriptor.physics.pop('trackCenterOffset')
        cases.append(('trackCenterOffset', descriptor))
        descriptor = self._descriptor()
        descriptor.hull = _Strict1513Component()
        cases.append(('hitTester', descriptor))
        descriptor = self._descriptor()
        del descriptor.type.xphysics['detailed']['chassis'][
            'trial-chassis']['roadWheelPositions']
        cases.append(('roadWheelPositions', descriptor))

        for expected, descriptor in cases:
            with self.subTest(field=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    vehicle_physics.derive_suspension_params(descriptor)

    def test_flat_support_stays_at_static_equilibrium(self):
        ground, pseudo_ground = self._plane_samples(self.params)

        solved = vehicle_physics.damper_suspension_step(
            self.params, self._state(), ground, 1.0 / 30.0,
            pseudo_ground)

        self.assertAlmostEqual(0.0, solved['height'], places=12)
        self.assertAlmostEqual(0.0, solved['vertical_velocity'], places=12)
        self.assertAlmostEqual(0.0, solved['pitch'], places=12)
        self.assertAlmostEqual(0.0, solved['roll'], places=12)
        self.assertFalse(solved['airborne'])
        self.assertEqual(18, solved['contact_count'])

    def test_longitudinal_plane_converges_to_pitch(self):
        gradient = 0.16
        ground, pseudo_ground = self._plane_samples(
            self.params, z_gradient=gradient)
        state = self._state()

        for unused in range(240):
            state = vehicle_physics.damper_suspension_step(
                self.params, state, ground, 1.0 / 60.0,
                pseudo_ground)

        self.assertAlmostEqual(
            -math.asin(gradient), state['pitch'], delta=0.015)
        self.assertFalse(state['airborne'])

    def test_one_side_plane_converges_to_roll(self):
        gradient = 0.14
        ground, pseudo_ground = self._plane_samples(
            self.params, x_gradient=gradient)
        state = self._state()

        for unused in range(240):
            state = vehicle_physics.damper_suspension_step(
                self.params, state, ground, 1.0 / 60.0,
                pseudo_ground)

        self.assertAlmostEqual(
            math.asin(gradient), state['roll'], delta=0.015)
        self.assertFalse(state['airborne'])

    def test_hard_limit_projects_excess_without_a_time_step(self):
        ground = [None] * len(self.params['springs'])
        ground[0] = 1.0
        before = vehicle_physics.suspension_limit_excess(
            self.params, self._state(), ground)

        solved = vehicle_physics.damper_suspension_step(
            self.params, self._state(), ground, 0.0)

        self.assertGreater(before, 0.5)
        self.assertLess(solved['max_limit_excess'], before * 0.01)

    def test_multi_contact_projection_converges_independent_of_order(self):
        gradient_x = 0.11178082363228886
        gradient_z = 0.09193766567849293
        center_y = 0.13635492625737228
        ground = tuple(
            center_y + gradient_x * spring['x'] +
            gradient_z * spring['z']
            for spring in self.params['springs'])
        pseudo_ground = tuple(
            center_y + gradient_x * contact['x'] +
            gradient_z * contact['z']
            for contact in self.params['pseudo_contacts'])
        state = self._state(
            height=-0.11332251894566656,
            vertical_velocity=-17.82973166273263,
            pitch=-0.2283978856788303,
            pitch_velocity=0.4857510890352834,
            roll=0.008904000398357759,
            roll_velocity=-0.05963721979826242)

        solved = vehicle_physics.damper_suspension_step(
            self.params, state, ground, 1.0 / 60.0, pseudo_ground)
        reversed_params = dict(self.params)
        reversed_params['springs'] = tuple(reversed(self.params['springs']))
        reversed_params['pseudo_contacts'] = tuple(
            reversed(self.params['pseudo_contacts']))
        reversed_solved = vehicle_physics.damper_suspension_step(
            reversed_params, state, tuple(reversed(ground)), 1.0 / 60.0,
            tuple(reversed(pseudo_ground)))

        self.assertLessEqual(solved['max_limit_excess'], 1.0e-6)
        self.assertLessEqual(reversed_solved['max_limit_excess'], 1.0e-6)
        for name in ('height', 'vertical_velocity', 'pitch',
                     'pitch_velocity', 'roll', 'roll_velocity'):
            self.assertAlmostEqual(
                solved[name], reversed_solved[name], places=12,
                msg=name)

    def test_airborne_state_falls_and_damps_angular_velocity(self):
        ground = (None,) * len(self.params['springs'])
        pseudo_ground = (None,) * len(self.params['pseudo_contacts'])

        solved = vehicle_physics.damper_suspension_step(
            self.params,
            self._state(height=5.0, pitch_velocity=2.0,
                        roll_velocity=-2.0),
            ground, 0.1, pseudo_ground)

        self.assertTrue(solved['airborne'])
        self.assertLess(solved['height'], 5.0)
        self.assertLess(solved['vertical_velocity'], 0.0)
        self.assertLess(abs(solved['pitch_velocity']), 0.6)
        self.assertLess(abs(solved['roll_velocity']), 0.6)

    def test_within_step_touch_is_distinct_from_final_airborne_state(self):
        ground, pseudo_ground = self._plane_samples(self.params)

        solved = vehicle_physics.damper_suspension_step(
            self.params,
            self._state(vertical_velocity=10.0),
            ground, 1.0 / 60.0, pseudo_ground)

        self.assertTrue(solved['contacted_this_step'])
        self.assertGreater(solved['touched_contact_count'], 0)
        self.assertEqual(0, solved['contact_count'])
        self.assertTrue(solved['airborne'])
        self.assertTrue(solved['left_flying'])
        self.assertTrue(solved['right_flying'])
        self.assertIsNone(solved['impact_speed'])

    def test_symmetric_five_metre_drop_reports_one_unbiased_impact(self):
        ground, pseudo_ground = self._plane_samples(self.params)
        state = self._state(height=5.0)
        impact = None

        for unused_tick in range(12):
            state = vehicle_physics.damper_suspension_step(
                self.params, state, ground, 0.1, pseudo_ground)
            if state['impact_speed'] is not None:
                impact = state['impact_speed']
                break

        self.assertIsNotNone(impact)
        self.assertLessEqual(impact, -9.8)
        self.assertTrue(state['contacted_this_step'])
        self.assertFalse(state['airborne'])
        self.assertAlmostEqual(0.0, state['pitch'], places=10)
        self.assertAlmostEqual(0.0, state['pitch_velocity'], places=10)
        self.assertAlmostEqual(0.0, state['roll'], places=10)
        self.assertAlmostEqual(0.0, state['roll_velocity'], places=10)

    def test_final_substep_touch_preserves_threshold_crossing_speed(self):
        ground, pseudo_ground = self._plane_samples(self.params)

        solved = vehicle_physics.damper_suspension_step(
            self.params,
            self._state(height=1.1, vertical_velocity=-9.5),
            ground, 0.1, pseudo_ground)

        expected_impact = -9.5 - vehicle_physics.GRAVITY * 0.1
        self.assertFalse(solved['airborne'])
        self.assertEqual(18, solved['contact_count'])
        self.assertTrue(solved['contacted_this_step'])
        self.assertEqual(18, solved['touched_contact_count'])
        self.assertAlmostEqual(expected_impact, solved['impact_speed'],
                               places=12)
        self.assertEqual(
            0, vehicle_physics.fall_damage(1000, -9.5))
        self.assertGreater(
            vehicle_physics.fall_damage(1000, solved['impact_speed']), 0)

    def test_soft_touch_records_the_first_negative_contact_speed(self):
        ground, pseudo_ground = self._plane_samples(self.params)

        solved = vehicle_physics.damper_suspension_step(
            self.params,
            self._state(height=0.1, vertical_velocity=-0.2),
            ground, 0.1, pseudo_ground)

        self.assertTrue(solved['contacted_this_step'])
        self.assertFalse(solved['airborne'])
        self.assertAlmostEqual(-0.2, solved['impact_speed'], places=12)
        self.assertAlmostEqual(0.0, solved['pitch'], places=10)
        self.assertAlmostEqual(0.0, solved['roll'], places=10)

    def test_support_filter_and_contact_memory_are_bounded(self):
        self.assertTrue(vehicle_physics.suspension_support_allowed(
            1.0, 1.0, 1.0))
        self.assertFalse(vehicle_physics.suspension_support_allowed(
            1.2, 1.0, 1.0))
        self.assertTrue(vehicle_physics.suspension_support_allowed(
            1.2, 0.9, 1.0))
        self.assertFalse(vehicle_physics.suspension_support_allowed(
            1.0, 0.5, 1.0))

        ground, memory = vehicle_physics.retained_ground_contact(
            (1.0, 2.0), 3.0, None, 0.5)
        self.assertEqual(3.0, ground)
        retained, stationary_miss = vehicle_physics.retained_ground_contact(
            (1.0, 2.0), None, memory, 0.5)
        self.assertEqual(3.0, retained)
        self.assertEqual(
            (None, None),
            vehicle_physics.retained_ground_contact(
                (1.0, 2.0), None, stationary_miss, 0.5))
        retained, moved_memory = vehicle_physics.retained_ground_contact(
            (1.2, 2.2), None, memory, 0.5)
        self.assertEqual(3.0, retained)
        self.assertNotEqual(memory, moved_memory)
        self.assertEqual(
            (None, None),
            vehicle_physics.retained_ground_contact(
                (1.2, 2.2), None, moved_memory, 0.5))
        self.assertEqual(
            (None, None),
            vehicle_physics.retained_ground_contact(
                (1.6, 2.0), None, memory, 0.5))

        unused_ground, memory = vehicle_physics.retained_ground_contact(
            (0.0, 0.0), 4.0, None, 0.5)
        for point in ((0.1, 0.0), (0.1, 0.1), (0.0, 0.1),
                      (0.0, 0.0), (-0.1, 0.0)):
            retained, memory = vehicle_physics.retained_ground_contact(
                point, None, memory, 0.5)
            self.assertEqual(4.0, retained)
        self.assertEqual(
            (None, None),
            vehicle_physics.retained_ground_contact(
                (-0.1, -0.1), None, memory, 0.5))

    def test_ground_plane_uses_contacts_and_rejects_a_discontinuity(self):
        gradient_x = 0.12
        gradient_z = -0.08
        ground = tuple(
            2.5 + gradient_x * spring['x'] +
            gradient_z * spring['z']
            for spring in self.params['springs'])

        plane = vehicle_physics.suspension_ground_plane(
            self.params, ground, maximum_residual=0.01)

        self.assertAlmostEqual(2.5, plane['center_y'])
        self.assertAlmostEqual(gradient_x, plane['x_gradient'])
        self.assertAlmostEqual(gradient_z, plane['z_gradient'])
        self.assertEqual(10, plane['contact_count'])
        broken = list(ground)
        broken[0] += 1.0
        self.assertIsNone(vehicle_physics.suspension_ground_plane(
            self.params, broken, maximum_residual=0.35))

    def test_world_ground_plane_projects_height_and_checks_continuity(self):
        gradient_x = 0.12
        gradient_z = -0.08
        ground = tuple(
            2.5 + gradient_x * spring['x'] +
            gradient_z * spring['z']
            for spring in self.params['springs'])
        start = (10.0, 99.0, 20.0)
        end = (12.0, 99.0, 21.0)

        plane = vehicle_physics.suspension_world_ground_plane(
            self.params, ground, start, math.pi * 0.5,
            maximum_residual=0.01)

        self.assertEqual((10.0, 20.0),
                         (plane['center_x'], plane['center_z']))
        self.assertAlmostEqual(2.5, plane['center_y'])
        self.assertAlmostEqual(-0.08, plane['gradient_x'])
        self.assertAlmostEqual(-0.12, plane['gradient_z'])
        expected = 2.5 - 0.08 * 2.0 - 0.12
        self.assertAlmostEqual(
            expected,
            vehicle_physics.suspension_plane_height(plane, 12.0, 21.0))

        current = dict(plane)
        current.update(center_x=12.0, center_z=21.0, center_y=expected)
        self.assertTrue(vehicle_physics.suspension_ground_planes_continuous(
            plane, current, start, end, 0.01, 0.01))
        raised = dict(current, center_y=expected + 0.2)
        self.assertFalse(vehicle_physics.suspension_ground_planes_continuous(
            plane, raised, start, end, 0.05, 0.01))
        tilted = dict(current, gradient_x=current['gradient_x'] + 0.1)
        self.assertFalse(vehicle_physics.suspension_ground_planes_continuous(
            plane, tilted, start, end, 0.5, 0.05))

        self.assertIsNone(vehicle_physics.suspension_plane_height(
            {'center_x': float('nan')}, 0.0, 0.0))
        self.assertFalse(vehicle_physics.suspension_ground_planes_continuous(
            plane, {}, start, end, 0.1, 0.1))

    def test_contact_correction_uses_bounded_interior_path_probes(self):
        self.assertEqual(
            (), vehicle_physics.suspension_path_probe_fractions(0.2))
        fractions = vehicle_physics.suspension_path_probe_fractions(5.0)
        self.assertEqual(3, len(fractions))
        self.assertTrue(all(0.0 < value < 1.0 for value in fractions))
        self.assertTrue(all(
            fractions[index] > fractions[index - 1]
            for index in range(1, len(fractions))))
        self.assertEqual(
            vehicle_physics.SUSPENSION_PATH_MAX_PROBES,
            len(vehicle_physics.suspension_path_probe_fractions(100.0)))

    def test_side_grip_curve_releases_a_cross_slope_progressively(self):
        self.assertEqual(1.0, vehicle_physics.lateral_slope_grip(1.0))
        self.assertAlmostEqual(
            0.1,
            vehicle_physics.lateral_slope_grip(
                vehicle_physics.SLOPE_GRIP_SDW_MIN_Y))
        midpoint_y = (
            vehicle_physics.SLOPE_GRIP_SDW_FULL_Y +
            vehicle_physics.SLOPE_GRIP_SDW_MIN_Y) * 0.5
        self.assertAlmostEqual(
            0.55, vehicle_physics.lateral_slope_grip(midpoint_y))
        self.assertEqual(
            0.0,
            vehicle_physics.slope_slide_speed(
                0.0, math.tan(math.radians(24.0)), 0.1))
        self.assertGreater(
            vehicle_physics.slope_slide_speed(
                0.0, math.tan(math.radians(25.0)), 0.1),
            0.0)


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
        self.assertEqual(vehicle_physics.GROUND_FOLLOW_MIN, fast)

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
        self.assertEqual(
            0.0, vehicle_physics.launch_vertical_speed(12.0, pitch))


if __name__ == '__main__':
    unittest.main()
