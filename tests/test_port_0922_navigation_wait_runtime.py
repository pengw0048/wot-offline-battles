"""Exercise pending-navigation recovery through worker control and its cache.

The path producer deliberately stays pending. The real runtime, adapter,
driver, traffic controller, and copied motion integration still run; the
native terrain/collision replies are controlled pure-Python fixtures.
"""
from contextlib import redirect_stdout
import io
import math
import unittest
from unittest import mock

import test_port_0922_bot_runtime as bot_fixture


class NavigationWaitRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def runtime(self, eroded=False, evidence=None, direction_probe=None,
                validated_bake=False):
        graph = bot_fixture._flat_open_graph()
        if validated_bake:
            graph['bake'].update(vehicle_half_width=2.15,
                                 edge_clearance_radii=(3.0,))
        if eroded:
            # The occupied center has no authored support. Native support is
            # still a continuous plane, so a checked short backout can exist.
            graph['heights_mm'][15 * graph['width'] + 15] = None
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: bot_fixture._combat_descriptor(),
            direction_probe=direction_probe or (
                lambda *unused: {'clear': True, 'slope': 0.0}),
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=lambda *unused: ((0.0, 0.0, 0.0), 0.0),
            baked_graph=graph)
        runtime.battle_start(self.fixture.start)
        runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'combat_mode': 'route',
                'move_position': (40.0, 0.0, 0.0),
                'face_position': (40.0, 0.0, 0.0),
                'fire_allowed': False, 'shell_index': 0, 'fire_range': 0.0,
            }], 'bots': [],
        })
        runtime.navigator.bot_states[11] = dict(evidence or {})
        runtime.navigator.next_target = mock.Mock(
            side_effect=lambda bot_id, position, *args, **kwargs: tuple(position))
        runtime.navigator.request_replan = mock.Mock(
            wraps=runtime.navigator.request_replan)
        runtime.adapter.decide_with_order = mock.Mock(
            wraps=runtime.adapter.decide_with_order)
        return runtime

    @staticmethod
    def tick(runtime, frame):
        now = 1.0 + frame * 0.04
        runtime.update(0.04, now)
        return now, dict(runtime._decision_cache[11][3])

    def test_safe_pending_search_holds_pose_after_recovery_wait_expires(self):
        runtime = self.runtime()
        state = runtime.states[11]
        initial = (state['x'], state['y'], state['z'], state['yaw'])
        for frame in range(250):
            unused_now, command = self.tick(runtime, frame)
            self.assertEqual('nav_wait', command['recovery_mode'])
            self.assertEqual((0.0, 0.0), (command['throttle'], command['turn']))
            self.assertTrue(command['brake'])
            self.assertEqual(initial, (
                state['x'], state['y'], state['z'], state['yaw']))
        decisions = runtime.adapter.decide_with_order.call_args_list
        self.assertGreater(len(decisions), 1)
        self.assertLess(len(decisions), 250)
        self.assertTrue(all(not call.args[0]['navigation_recovery_allowed']
                            for call in decisions))
        self.assertGreater(runtime.adapter.driver.states[11][
            'navigation_wait']['age'], 4.0)
        runtime.navigator.request_replan.assert_not_called()

    def test_missing_baked_support_reaches_driver_as_checked_backout(self):
        runtime = self.runtime(eroded=True)
        backing = []
        for frame in range(175):
            unused_now, command = self.tick(runtime, frame)
            if command['throttle'] < 0.0:
                backing.append(command)
        self.assertTrue(backing)
        self.assertTrue(all(command['navigation_recovery'] for command in backing))
        self.assertTrue(all(command['turn'] == 0.0 for command in backing))
        first_state = runtime.adapter.decide_with_order.call_args_list[0].args[0]
        self.assertTrue(first_state['navigation_recovery_allowed'])
        self.assertEqual(1, runtime.navigator.request_replan.call_count)

    def test_physical_contact_evidence_enables_recovery_on_supported_ground(self):
        for evidence in ({'hard_contact_episode': {'position': (0.0, 0.0, 0.0)}},
                         {'blocked_step_tracker': {'position': (0.0, 0.0, 0.0)}}):
            with self.subTest(evidence=evidence):
                runtime = self.runtime(evidence=evidence)
                backed = False
                for frame in range(175):
                    unused_now, command = self.tick(runtime, frame)
                    backed |= command['throttle'] < 0.0
                self.assertTrue(backed)
                first = runtime.adapter.decide_with_order.call_args_list[0].args[0]
                self.assertTrue(first['navigation_recovery_allowed'])
                self.assertEqual(1, runtime.navigator.request_replan.call_count)

    def test_replan_is_consumed_before_cache_and_not_replayed_by_motion_slices(self):
        runtime = self.runtime(eroded=True)
        emitted_frame = None
        for frame in range(200):
            state = runtime.states[11]
            before = (state['x'], state['y'], state['z'])
            now, command = self.tick(runtime, frame)
            self.assertNotIn('navigation_replan', command)
            if runtime.navigator.request_replan.call_count:
                emitted_frame = frame
                break
        self.assertIsNotNone(emitted_frame, 'bounded backout never ended')
        runtime.navigator.request_replan.assert_called_once_with(
            11, before, now)
        cached = runtime._decision_cache[11]
        decisions = runtime.adapter.decide_with_order.call_count
        self.assertGreater(cached[1], now + 0.08)
        for frame in (emitted_frame + 1, emitted_frame + 2):
            self.tick(runtime, frame)
            self.assertIs(cached, runtime._decision_cache[11])
            self.assertEqual(decisions, runtime.adapter.decide_with_order.call_count)
            self.assertEqual(1, runtime.navigator.request_replan.call_count)
        for frame in range(emitted_frame + 3, emitted_frame + 100):
            unused_now, command = self.tick(runtime, frame)
            self.assertNotIn('navigation_replan', command)
        self.assertGreater(runtime.adapter.decide_with_order.call_count, decisions)
        self.assertEqual(1, runtime.navigator.request_replan.call_count)

    def test_unavailable_native_rear_proof_does_not_authorize_recovery(self):
        for result in (None, {'clear': False, 'collision': False,
                              'slope': 0.0, 'deferred': True}):
            with self.subTest(probe_result=result):
                probes = []

                def direction_probe(*args):
                    probes.append(args)
                    return result

                runtime = self.runtime(eroded=True, direction_probe=direction_probe)
                state = runtime.states[11]
                initial = (state['x'], state['y'], state['z'], state['yaw'])
                for frame in range(175):
                    unused_now, command = self.tick(runtime, frame)
                    self.assertEqual('nav_wait', command['recovery_mode'])
                    self.assertEqual((0.0, 0.0), (
                        command['throttle'], command['turn']))
                    self.assertTrue(command['brake'])
                    self.assertNotIn('navigation_recovery', command)
                    self.assertEqual(initial, (
                        state['x'], state['y'], state['z'], state['yaw']))
                self.assertTrue(probes)
                self.assertEqual(1, runtime.navigator.request_replan.call_count)

    def test_short_baked_clear_edge_cannot_replace_full_native_backout_proof(self):
        for result in (None, {'clear': False, 'collision': False,
                              'slope': 0.0, 'deferred': True}):
            with self.subTest(probe_result=result):
                probes = []

                def direction_probe(*args):
                    probes.append(args)
                    return result

                runtime = self.runtime(
                    evidence={'hard_contact_episode': {'position': (0.0, 0.0, 0.0)}},
                    direction_probe=direction_probe, validated_bake=True)
                state = runtime.states[11]
                distance = self.module.ai_driver.recovery_probe_distance(
                    state['half_length'])
                self.assertGreater(distance, runtime.navigator.grid.cell_size)
                # This is the real supported-bake shortcut: it proves only
                # the first four metres of the longer requested backout.
                self.assertIs(True, runtime._planner_corridor_clear(
                    (0.0, 0.0, 0.0), math.pi, 0.0,
                    maximum_distance=distance,
                    native_capability=runtime.navigation_planning_capability(11, -1.0)))
                for frame in range(175):
                    unused_now, command = self.tick(runtime, frame)
                    self.assertEqual((0.0, 0.0), (
                        command['throttle'], command['turn']))
                    self.assertEqual('nav_wait', command['recovery_mode'])
                    self.assertTrue(command['brake'])
                    self.assertEqual((0.0, 0.0, 0.0, 0.0), (
                        state['x'], state['y'], state['z'], state['yaw']))
                rear_probes = [args for args in probes
                               if abs(abs(args[1]) - math.pi) < 1e-8
                               and args[4] is not None]
                self.assertTrue(rear_probes, 'baked shortcut skipped native rear proof')
                self.assertTrue(any(abs(args[4] - distance) < 1e-8
                                    for args in rear_probes))
                self.assertEqual(1, runtime.navigator.request_replan.call_count)


if __name__ == '__main__':
    unittest.main()
