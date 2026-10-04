"""Navigation waits must not turn in place or bypass bounded physical escape."""
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import LocalDriver, recovery_probe_distance


class NavigationWaitRecoveryTests(unittest.TestCase):
    def adapter(self):
        return BotAdapter('31_airfield', 153100,
                          navigation_target=lambda bot, position, *args: position)

    def state(self, **changes):
        state = {'id': 27, 'team': 2, 'slot': 11, 'position': (0.0, 0.0, 0.0),
                 'yaw': 0.0, 'speed': 0.0, 'dt': 0.1,
                 'half_length': 2.8677990436553955,
                 'half_width': 1.5697760581970215}
        state.update(changes)
        return state

    def order(self, **changes):
        order = {'combat_mode': 'route', 'move_position': (200.0, 0.0, 0.0),
                 'face_position': (200.0, 0.0, 0.0)}
        order.update(changes)
        return order

    def test_pending_path_does_not_turn_towards_remote_route_or_combat_facing(self):
        adapter = self.adapter()
        probes = []
        for frame in range(100):
            command = adapter.decide_with_order(
                self.state(dt=1.0),
                self.order(face_position=((1.0 if frame % 2 else -1.0) * 200.0,
                                          0.0, 0.0)),
                lambda *args: probes.append(args) or True)
            self.assertEqual(command['recovery_mode'], 'nav_wait')
            self.assertEqual(command['throttle'], 0.0)
            self.assertEqual(command['turn'], 0.0)
            self.assertEqual(command['target_yaw'], 0.0)
            self.assertTrue(command['brake'])
            self.assertNotIn('navigation_replan', command)
        self.assertEqual(probes, [])
        self.assertEqual(adapter.driver.states[27]['stuck_time'], 0.0)
        self.assertEqual(adapter.driver.states[27]['recovery_count'], 0)

    def test_proved_blockage_backs_once_then_requests_one_replan(self):
        adapter = self.adapter()
        state = self.state(navigation_recovery_allowed=True)
        probes, commands = [], []

        def clear(yaw, maximum_distance=None, drive_direction=1.0):
            probes.append((yaw, maximum_distance, drive_direction))
            return True

        for frame in range(150):
            command = adapter.decide_with_order(state, self.order(), clear)
            commands.append(command)
            # Actual observed displacement, not commanded throttle, bounds the
            # retreat. A denied movement must still expire by its time lease.
            if command['throttle'] < 0.0:
                state['position'] = (0.0, 0.0, state['position'][2] - 0.04)
        backing = [i for i, command in enumerate(commands)
                   if command['throttle'] < 0.0]
        self.assertTrue(backing)
        self.assertGreaterEqual(backing[0], 39)
        self.assertLess(len(backing), 13)
        self.assertEqual(backing, list(range(backing[0], backing[-1] + 1)))
        self.assertTrue(all(commands[i]['navigation_recovery'] for i in backing))
        self.assertTrue(all(commands[i]['turn'] == 0.0 for i in backing))
        self.assertEqual(sum(bool(c.get('navigation_replan')) for c in commands), 1)
        self.assertTrue(all(p[2] == -1.0 for p in probes))
        self.assertTrue(all(abs(p[0] - math.pi) < 1e-8 for p in probes))
        self.assertAlmostEqual(probes[0][1], recovery_probe_distance(state['half_length']))
        self.assertLess(probes[-1][1], probes[0][1])

    def test_pending_search_pauses_existing_recovery_without_resetting_progress(self):
        driver = LocalDriver(stuck_seconds=0.4)
        position = (0.0, 0.0, 0.0)
        for frame in range(10):
            command = driver.drive(
                27, 11, position, 0.0, 0.0, 0.1, (0.0, 0.0, 30.0),
                (), lambda *unused: True)
            if command['recovery_mode'] == 'reverse_turn':
                break
        self.assertEqual(command['recovery_mode'], 'reverse_turn')
        before = dict(driver.states[27])
        for frame in range(5):
            command = driver.wait_for_navigation(
                27, 11, (0.0, 0.0, -0.03), 0.0, 0.0, 0.1, (),
                lambda *unused: self.fail('An ordinary path wait must hold'))
            self.assertEqual(command['recovery_mode'], 'nav_wait')
            self.assertEqual((command['throttle'], command['turn']), (0.0, 0.0))
        for key in ('last_position', 'stuck_time', 'recovery_time',
                    'recovery_side', 'steering_yaw', 'heading_progress_yaw',
                    'steering_age', 'plan_age'):
            self.assertEqual(driver.states[27][key], before[key], key)
        driver.end_navigation_wait(27)
        command = driver.drive(
            27, 11, position, 0.0, 0.0, 0.1, (0.0, 0.0, 30.0),
            (), lambda *unused: True)
        self.assertEqual(command['recovery_mode'], 'reverse_turn')
        self.assertAlmostEqual(driver.states[27]['recovery_time'],
                               before['recovery_time'] - 0.1)
        self.assertEqual(driver.states[27]['recovery_count'],
                         before['recovery_count'])

    def test_unproved_or_vehicle_occupied_rear_never_reverses_or_repeats_replan(self):
        for terrain_clear, neighbours in (
                (False, ()),
                (True, ({'id': 26, 'team': 2, 'position': (0.0, 0.0, -6.0),
                         'yaw': 0.0, 'half_length': 2.8, 'half_width': 1.6},))):
            with self.subTest(terrain_clear=terrain_clear):
                adapter = self.adapter()
                state = self.state(navigation_recovery_allowed=True,
                                   neighbours=neighbours)
                commands = [adapter.decide_with_order(
                    state, self.order(), lambda *unused: terrain_clear)
                            for frame in range(150)]
                self.assertTrue(all(c['throttle'] == c['turn'] == 0.0
                                    and c['brake'] for c in commands))
                self.assertEqual(sum(bool(c.get('navigation_replan'))
                                     for c in commands), 1)

    def test_actual_displacement_and_physical_failure_end_retreat_early(self):
        for ending in ('displacement', 'physical_failure'):
            with self.subTest(ending=ending):
                adapter = self.adapter()
                state = self.state(navigation_recovery_allowed=True)
                command = None
                for frame in range(50):
                    command = adapter.decide_with_order(
                        state, self.order(), lambda *unused: True)
                    if command['throttle'] < 0.0:
                        break
                self.assertLess(command['throttle'], 0.0)
                if ending == 'displacement':
                    state['position'] = (0.0, 0.0,
                        -recovery_probe_distance(state['half_length']) - 0.1)
                else:
                    adapter.driver.remember_failure(27, math.pi)
                command = adapter.decide_with_order(
                    state, self.order(), lambda *unused: True)
                self.assertTrue(command['brake'])
                self.assertEqual(command['throttle'], 0.0)
                self.assertTrue(command['navigation_replan'])

    def test_resumed_path_ends_episode_and_preserves_arrived_combat_facing(self):
        adapter = self.adapter()
        state = self.state(navigation_recovery_allowed=True)
        for frame in range(65):
            adapter.decide_with_order(state, self.order(), lambda *unused: True)
        self.assertTrue(adapter.driver.states[27]['navigation_wait']['completed'])
        adapter.navigation_target = lambda *unused: (0.0, 0.0, 30.0)
        command = adapter.decide_with_order(state, self.order(), lambda *unused: True)
        self.assertNotIn('navigation_wait', adapter.driver.states[27])
        self.assertGreater(command['throttle'], 0.0)
        adapter.navigation_target = lambda bot, position, *args: position
        command = adapter.decide_with_order(state, self.order(), lambda *unused: True)
        self.assertEqual(command['recovery_mode'], 'nav_wait')
        self.assertEqual(command['turn'], 0.0)
        command = adapter.decide_with_order(
            state, self.order(throttle_override=0.0), lambda *unused: True)
        self.assertEqual(command['recovery_mode'], 'arrived')
        self.assertGreater(command['turn'], 0.0)
        self.assertTrue(command['brake'])


if __name__ == '__main__':
    unittest.main()
