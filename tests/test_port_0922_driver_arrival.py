import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import LocalDriver, WAYPOINT_ARRIVAL_RADIUS


class DriverArrivalTests(unittest.TestCase):
    @staticmethod
    def _params(rate=26.0):
        # Airfield 20260922-113632 Object 730 motion rows publish these
        # installed mass/power/resistance values. Their flat clear translation
        # gives atan2(dx, dz), and (new_yaw-old_yaw)/dt is exactly -26 deg/s.
        # Unpublished speed caps/pivot settings use the copied-physics fixture
        # defaults; the short report replays stay below either speed cap.
        return dict(vehicle_physics._DEFAULTS, mass=49045.0, powerW=514850.0,
                    terrainResist=(1.3422818563357068, 1.4381591562799616,
                                   2.2051773272447597),
                    rotSpd=math.radians(rate))

    def _follow(self, target, position=(0.0, 0.0, 0.0), yaw=0.0,
                speed=0.0, rate=26.0, duration=12.0, known_rate=True,
                control_step=0.1, around_center=True):
        params = self._params(rate)
        params['rotationIsAroundCenter'] = around_center
        adapter = BotAdapter('airfield', 11,
                             navigation_target=lambda *unused: target)
        order = {'combat_mode': 'route', 'move_position': (0.0, 0.0, 200.0)}
        omega = 0.0
        elapsed = 0.0
        commands = []
        maximum_speed = abs(speed)
        while elapsed < duration:
            distance = math.hypot(target[0] - position[0], target[2] - position[2])
            if distance <= WAYPOINT_ARRIVAL_RADIUS:
                return elapsed, distance, maximum_speed, commands, adapter
            state = {'id': 17, 'team': 2, 'slot': 1, 'position': position,
                     'yaw': yaw, 'speed': speed, 'dt': control_step,
                     'half_length': 3.409619092941284,
                     'half_width': 1.6861989498138428,
                     'turn_speed_limit': params['rotSpd'] if known_rate else None}
            command = adapter.decide_with_order(state, order, lambda *unused: True)
            commands.append(command)
            remaining = control_step
            while remaining > 0.000001:
                step = min(remaining, 0.032)
                # Follow the production order: traverse first, then drive and
                # semi-implicit translation; no native obstacle or traffic veto.
                omega = vehicle_physics.traverse_step(
                    params, omega, command['turn'], speed, step,
                    drive_intent=command['throttle'])
                old_yaw = yaw
                pivot_offset = vehicle_physics.track_pivot_offset(
                    params, speed, omega, drive_intent=command['throttle'])
                yaw = (yaw + omega * step + math.pi) % (2.0 * math.pi) - math.pi
                position = vehicle_physics.track_pivot_position(
                    position, old_yaw, yaw, pivot_offset)
                speed = vehicle_physics.longitudinal_step(
                    params, speed, command['throttle'], command['turn'],
                    0.0, step, handbrake=command['brake'])
                position = (position[0] + math.sin(yaw) * speed * step,
                            position[1],
                            position[2] + math.cos(yaw) * speed * step)
                maximum_speed = max(maximum_speed, abs(speed))
                elapsed += step
                remaining -= step
        distance = math.hypot(target[0] - position[0], target[2] - position[2])
        return None, distance, maximum_speed, commands, adapter

    def test_report_clear_object_730_reaches_fixed_waypoint_instead_of_orbit(self):
        target = (-318.0, -0.18, -162.0)
        samples = (
            ((-315.9337480894199, -0.18, -164.84366244795356),
             0.9814697507451752, 1.7495827038679135),
            ((-315.42137096440297, -0.1800001859664917, -162.87952283341613),
             0.4442300992425158, 2.900211921504135),
        )
        for position, yaw, speed in samples:
            with self.subTest(position=position):
                reached, distance, maximum, commands, adapter = self._follow(
                    target, position, yaw, speed)
                self.assertIsNotNone(reached)
                self.assertLess(reached, 7.0)
                self.assertLessEqual(distance, WAYPOINT_ARRIVAL_RADIUS)
                self.assertLess(maximum, self._params()['speedFwd'])
                self.assertTrue(any(c['brake'] and c['turn'] for c in commands))
                self.assertEqual(adapter.driver.states[17]['recovery_count'], 0)

    def test_clear_short_routes_converge_at_slow_and_fast_traverse_rates(self):
        for rate in (20.0, 26.0, 38.0, 60.0):
            for distance in (2.0, 3.0, 5.0, 15.0):
                for side in (-1.0, 1.0):
                    with self.subTest(rate=rate, distance=distance, side=side):
                        result = self._follow((side * distance, 0.0, 0.0), rate=rate,
                                              control_step=0.2)
                        self.assertIsNotNone(result[0])
                        self.assertLessEqual(result[1], WAYPOINT_ARRIVAL_RADIUS)

    def test_track_pivot_chassis_can_reach_short_route_target(self):
        for side in (-1.0, 1.0):
            result = self._follow((side * 3.0, 0.0, 0.0), around_center=False)
            self.assertIsNotNone(result[0])
            self.assertLessEqual(result[1], WAYPOINT_ARRIVAL_RADIUS)

    def test_unknown_traverse_preserves_existing_route_behavior(self):
        # This same fixed, unobstructed point still has no arrival after the
        # observation window when the caller cannot supply physical curvature.
        result = self._follow((-5.0, 0.0, 0.0), rate=20.0, duration=20.0,
                              known_rate=False)
        self.assertIsNone(result[0])
        self.assertGreater(result[1], WAYPOINT_ARRIVAL_RADIUS)

    def test_aligned_route_does_not_stop_at_every_waypoint(self):
        reached, unused_distance, unused_maximum, commands, unused_adapter = (
            self._follow((0.0, 0.0, 40.0)))
        self.assertIsNotNone(reached)
        self.assertTrue(commands)
        self.assertTrue(all(c['throttle'] == 1.0 and not c['brake']
                            for c in commands))

    @staticmethod
    def _orbit_command(driver, **overrides):
        args = dict(bot_id=17, team_slot=1, position=(0.0, 0.0, 0.0),
                    yaw=0.0, speed=3.0, dt=0.1, target=(-3.0, 0.0, 0.0),
                    neighbours=(), direction_clear=lambda *unused: True,
                    stop_at_target=False, turn_speed_limit=math.radians(26.0))
        args.update(overrides)
        return driver.drive(**args)

    def test_alignment_brake_survives_zero_speed_then_releases_when_facing_goal(self):
        driver = LocalDriver()
        self.assertTrue(self._orbit_command(driver)['brake'])
        self.assertTrue(self._orbit_command(driver, speed=0.0)['brake'])
        aligned = self._orbit_command(driver, speed=0.0, yaw=-math.pi / 2.0)
        self.assertEqual(aligned['throttle'], 1.0)
        self.assertFalse(aligned['brake'])

    def test_hold_reverse_new_goal_and_missing_physics_clear_alignment(self):
        for override in ({'movement_intent': False}, {'speed': -1.0},
                         {'target': (0.0, 0.0, 40.0)},
                         {'turn_speed_limit': None}):
            with self.subTest(override=override):
                driver = LocalDriver()
                self._orbit_command(driver)
                self.assertIsNotNone(driver.states[17].get('alignment_target'))
                self._orbit_command(driver, **override)
                self.assertIsNone(driver.states[17].get('alignment_target'))

    def test_terrain_avoidance_retains_its_checked_exit(self):
        driver = LocalDriver()
        self._orbit_command(driver)
        # The route becomes blocked; the first positive fan offset is clear.
        command = self._orbit_command(
            driver, direction_clear=lambda heading, *unused: heading > -1.4)
        self.assertEqual(command['recovery_mode'], 'avoid')
        self.assertEqual(command['throttle'], 1.0)
        self.assertIsNone(driver.states[17].get('alignment_target'))

    def test_local_target_probe_keeps_leading_hull_and_decision_travel(self):
        target = (0.0, 0.0, 2.0)
        adapter = BotAdapter('airfield', 11,
                             navigation_target=lambda *unused: target)
        state = {'id': 17, 'team': 2, 'slot': 1, 'position': (0.0, 0.0, 0.0),
                 'half_length': 3.4, 'speed': 0.0, 'dt': 0.1,
                 'decision_horizon': 0.2}
        order = {'combat_mode': 'route', 'move_position': (0.0, 0.0, 200.0)}
        probes = []

        def wall_at_six_metres(yaw, maximum_distance=None):
            # The range is callback-visible before LocalDriver asks its first
            # candidate. Runtime can keep its ordinary vehicle-blocker check,
            # then bound the native ray instead of seeing the wall past arrival.
            self.assertIsNone(maximum_distance)
            distance = state.get('navigation_probe_distance', 15.0)
            probes.append(distance)
            return math.cos(yaw) * distance < 6.0

        command = adapter.decide_with_order(state, order, wall_at_six_metres)
        self.assertEqual(command['recovery_mode'], 'drive')
        self.assertEqual(command['turn'], 0.0)
        self.assertAlmostEqual(probes[0], 3.9)
        state['speed'] = 20.0
        adapter.decide_with_order(state, order, lambda *unused: True)
        self.assertAlmostEqual(state['navigation_probe_distance'], 7.4)
        order['throttle_override'] = 0.0
        adapter.decide_with_order(state, order, lambda *unused: True)
        self.assertNotIn('navigation_probe_distance', state)


if __name__ == '__main__':
    unittest.main()
