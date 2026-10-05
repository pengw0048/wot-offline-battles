"""Static navigation consumers share world proofs and preserve probe ownership."""
import copy
import math
import unittest

import test_port_0922_bot_runtime as bot_fixture


class NavigationDirectionABITests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        self.arguments = ((1.0, 2.0, 3.0), 0.25, 7.5,
                          bot_fixture._combat_descriptor(), 4.0, 2.15)

    def test_legacy_arities_receive_their_original_arguments_once(self):
        for count in range(2, 7):
            with self.subTest(argument_count=count):
                calls = []
                namespace = {'calls': calls}
                arguments = ', '.join('value%d' % i for i in range(count))
                exec('def direction(%s):\n'
                     '    calls.append((%s))\n'
                     '    return {"clear": True}\n' %
                     (arguments, arguments), namespace)
                runtime = self.module.BotRuntime(
                    1, direction_probe=namespace['direction'])
                result = runtime._probe_direction(*self.arguments)
                self.assertTrue(result['clear'])
                self.assertEqual([self.arguments[:count]], calls)

    def test_native_six_argument_body_error_is_not_retried(self):
        calls = []

        def direction(position, yaw, speed, descriptor, maximum_distance,
                      corridor_half_width):
            calls.append((position, yaw, speed, descriptor, maximum_distance,
                          corridor_half_width))
            raise TypeError('native callback failed after observable work')

        runtime = self.module.BotRuntime(1, direction_probe=direction)
        result = runtime._probe_direction(*self.arguments)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        self.assertEqual([self.arguments], calls)

    def test_bound_six_argument_callback_is_adapted_before_execution(self):
        calls = []

        class NativeHarness(object):
            def direction(self, position, yaw, speed, descriptor,
                          maximum_distance, corridor_half_width):
                calls.append((position, yaw, speed, descriptor,
                              maximum_distance, corridor_half_width))
                return {'clear': True}

        runtime = self.module.BotRuntime(
            1, direction_probe=NativeHarness().direction)
        self.assertTrue(runtime._probe_direction(*self.arguments)['clear'])
        self.assertEqual([self.arguments[:6]], calls)

    def test_driver_probe_body_type_error_is_not_retried(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        calls = []

        def direction(yaw, maximum_distance=None):
            calls.append((yaw, maximum_distance))
            raise TypeError('native query failed after observable work')

        self.assertFalse(LocalDriver()._clear(
            direction, math.pi, 5.6))
        self.assertEqual([(math.pi, 5.6)], calls)

    def test_driver_adapts_each_function_or_bound_method_before_execution(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        calls = []
        driver = LocalDriver()

        def legacy(yaw):
            calls.append(('legacy', yaw))
            return True

        def bounded(yaw, distance=None):
            calls.append(('bounded', yaw, distance))
            return True

        class Probe(object):
            def legacy(self, yaw):
                calls.append(('method', yaw))
                return True

            def bounded(self, yaw, distance=None):
                calls.append(('method_bounded', yaw, distance))
                return True

            def variadic(self, *values):
                calls.append(('variadic',) + values)
                return True

        probe = Probe()
        for callback in (legacy, bounded, probe.legacy, probe.bounded, probe.variadic):
            self.assertTrue(driver._clear(callback, .25, 5.6))
        self.assertEqual([
            ('legacy', .25), ('bounded', .25, 5.6), ('method', .25),
            ('method_bounded', .25, 5.6), ('variadic', .25, 5.6)], calls)

    def test_driver_bound_and_opaque_body_errors_are_local_single_calls(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        calls = []

        class Probe(object):
            def bounded(self, yaw, distance=None):
                calls.append(('method', yaw, distance))
                raise TypeError('native query failed after observable work')

            def __call__(self, yaw, distance=None):
                calls.append(('opaque', yaw, distance))
                raise TypeError('native query failed after observable work')

        probe = Probe()
        driver = LocalDriver()
        self.assertFalse(driver._clear(probe.bounded, .25, 5.6))
        self.assertFalse(driver._clear(probe, .25, 5.6))
        self.assertEqual([('method', .25, 5.6), ('opaque', .25, 5.6)], calls)

    def test_runtime_repeated_candidate_shares_one_static_query(self):
        calls, verdicts = [], []

        class CandidateAdapter(bot_fixture._FixedAdapter):
            def decide(self, state, clear):
                # Repeated advisory checks share one sample for this decision.
                verdicts.append((clear(0.0, 5.6),
                                 clear(0.0, 5.6),
                                 clear(0.0, 5.6)))
                return dict(self.command)

        def direction(position, yaw, speed, descriptor, maximum_distance,
                      corridor_half_width):
            calls.append((yaw, maximum_distance, descriptor))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        descriptor = bot_fixture._combat_descriptor()
        descriptor.physics.update(weight=10000.0, speedLimits=(20.0, 10.0))
        adapter = CandidateAdapter(self.fixture._stationary_command())
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=direction,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._flat_open_graph())
        runtime.battle_start(self.fixture.start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=math.pi,
                                  speed=0.0, grounded_once=True)
        # Exercise the native fallback and its real per-decision sample cache.
        runtime._planner_corridor_clear = lambda *unused, **kwargs: None
        runtime.update(0.1, 1.0)
        self.assertEqual([(True, True, True)], verdicts)
        candidate_calls = [entry for entry in calls
                           if entry[0] == 0.0 and entry[1] == 5.6]
        self.assertEqual(1, len(candidate_calls))
        self.assertTrue(all(entry[2] is descriptor for entry in candidate_calls))


class NavigationWorldReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        self.descriptor = bot_fixture._combat_descriptor()
        self.descriptor.physics.update(weight=10000.0, speedLimits=(20.0, 10.0))
        self.native_calls = []
        self.world_blocked = False

        def obstacle(start, end, width):
            # The real grid performs edge lookup, receipts and caching.
            self.native_calls.append((start, end, width))
            return self.world_blocked

        graph = bot_fixture._flat_open_graph()
        graph['bake'].update(vehicle_half_width=2.15,
                             edge_clearance_radii=(3.0, 6.0))
        self.runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: self.descriptor,
            adapter_factory=lambda *unused, **kwargs: bot_fixture._Adapter(),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=bot_fixture._spawn_resolver,
            obstacle_probe=obstacle, baked_graph=graph)
        start = copy.deepcopy(self.fixture.start)
        start['bots'].append(
            {'id': 12, 'team': 2, 'slot': 1, 'name': 'Light Bot'})
        self.runtime.battle_start(start)
        light = copy.deepcopy(self.descriptor)
        light.physics.update(weight=2000.0, speedLimits=(7.0, 3.0))
        self.runtime._descriptor_pairs[12] = (light, None)
        self.runtime._install_bot_descriptor(12, self.runtime.states[12], 0)
        for bot_id in (11, 12):
            self.runtime.states[bot_id].update(
                x=0.0, y=0.0, z=0.0, yaw=0.0, now=1.0, speed=0.0)
        self.runtime.navigator.grid.review_native_corridor(
            (0.0, 0.0, 0.0), (0.0, 0.0, 20.0))

    def assert_consumer_reached_native(self):
        self.assertTrue(self.native_calls)
        self.assertTrue(all(call[2] == 2.15 for call in self.native_calls),
                        self.native_calls)

    def reset_receipts(self):
        self.runtime.navigator.grid._native_review_cache.clear()
        self.native_calls[:] = []

    def test_offset_lane_goal_reviews_static_world(self):
        for bot_id in (11, 12):
            with self.subTest(bot_id=bot_id):
                self.reset_receipts()
                result = self.runtime._safe_route_lane_goal(
                    self.runtime.states[bot_id], (0.0, 0.0, 8.0),
                    (0.0, 1.0), 4.0, 0.0, 1.0)
                self.assertEqual((-4.0, 0.0, 8.0), result)
                self.assert_consumer_reached_native()

    def test_direct_navigation_target_shares_receipt_identity_between_vehicles(self):
        goal = (0.0, 0.0, 8.0)
        for bot_id in (11, 12):
            with self.subTest(bot_id=bot_id):
                self.reset_receipts()
                result = self.runtime._navigation_target(
                    bot_id, (0.0, 0.0, 0.0), goal,
                    {'combat_mode': 'hold'}, self.runtime.states[bot_id])
                self.assertEqual(goal, result)
                self.assert_consumer_reached_native()
        progress = self.runtime.navigator.bot_direct_progress
        self.assertEqual({11, 12}, set(progress))
        self.assertEqual(progress[11]['path_key'], progress[12]['path_key'])

    def test_local_route_lane_offset_reviews_static_world(self):
        route = {'id': 'forest', 'waypoints': (
            (0.0, 0.0, False), (0.0, 40.0, False))}
        strategic = {'route_id': 'forest', 'route_index': 1, 'route_join': False,
                     'route_anchor': (0.0, 0.0, 0.0)}
        for bot_id, x in ((11, -8.0), (12, 8.0)):
            self.runtime.states[bot_id].update(x=x, route=route)
            self.runtime._server_orders[bot_id] = dict(strategic)
        # The authored two-Bot layout gives one centre and one offset lane.
        # The lighter Bot's nonzero lane uses the same static-world policy.
        self.reset_receipts()
        x = self.runtime.states[12]['x']
        selected = (x, 0.0, 8.0)
        target = self.runtime._route_lane_target(
            12, (x, 0.0, 0.0), (0.0, 0.0, 40.0),
            selected, strategic, 1.0)
        self.assertNotEqual(selected, target)
        self.assert_consumer_reached_native()

    def test_same_map_new_round_reproves_corridor_against_new_world(self):
        navigator = self.runtime.navigator
        start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 24.0)
        for frame in range(10):
            navigator.begin_frame(0.1)
            try:
                navigator.next_target(
                    11, start, goal, ('route', 2, 'forest', 1),
                    1.0 + frame * 0.1)
            finally:
                navigator.end_frame()
            if navigator.paths:
                break
        self.assertTrue(navigator.paths)
        self.assertTrue(navigator.bot_states)
        self.assertTrue(navigator.grid._native_review_cache)
        self.world_blocked = True
        self.native_calls[:] = []
        self.runtime.battle_start(dict(self.fixture.start, round_id=6))
        self.assertIsNot(navigator, self.runtime.navigator)
        navigator = self.runtime.navigator
        self.assertFalse(navigator.paths)
        self.assertFalse(navigator.bot_states)
        self.assertFalse(navigator.searches)
        self.assertFalse(navigator.grid._native_review_cache)
        navigator.grid.review_native_corridor(start, goal)
        self.assertFalse(navigator.grid.segment_clear(
            start, (0.0, 0.0, 8.0)))
        self.assert_consumer_reached_native()

    def test_corridor_probe_reviews_both_travel_directions(self):
        for bot_id, yaw, direction in ((11, 0.0, 1.0),
                                      (11, math.pi, -1.0), (12, 0.0, 1.0)):
            with self.subTest(bot_id=bot_id, yaw=yaw, direction=direction):
                self.reset_receipts()
                state = self.runtime.states[bot_id]
                state.update(yaw=yaw, speed=direction)
                self.assertTrue(self.runtime._planner_corridor_clear(
                    (state['x'], state['y'], state['z']), yaw, state['speed']))
                self.assert_consumer_reached_native()

    def test_same_segment_reuses_native_proof_across_vehicle_and_gear_changes(self):
        self.reset_receipts()
        grid = self.runtime.navigator.grid
        start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 8.0)
        self.assertTrue(grid.segment_clear(start, goal))
        self.assert_consumer_reached_native()
        calls = tuple(self.native_calls)
        for bot_id, direction in ((12, 1.0), (11, -1.0), (12, -1.0)):
            state = self.runtime.states[bot_id]
            state.update(speed=direction, yaw=0.0 if direction > 0.0 else math.pi)
            target = self.runtime._navigation_target(
                bot_id, start, goal, {'combat_mode': 'hold'}, state)
            self.assertEqual(goal, target)
            self.assertEqual(calls, tuple(self.native_calls))

    def test_queued_cover_callback_reviews_static_world(self):
        calls = []

        def cover(source, target, route, allies, segment_clear):
            self.reset_receipts()
            result = segment_clear((0.0, 0.0, 0.0), (0.0, 0.0, 8.0))
            calls.append((source['id'], result, tuple(self.native_calls)))
            return ({'source_id': source['id']},)

        self.runtime.cover_probe = cover
        self.runtime.visibility_probe = lambda *unused: True
        self.runtime.firing_lane_probe = lambda *unused: True
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'take_cover',
            'aim_position': (0.0, 1.0, 20.0),
            'face_position': (0.0, 1.0, 20.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        self.runtime.adapter = bot_fixture._FixedAdapter(command)
        self.runtime.states[12]['x'] = -20.0
        player = bot_fixture._admit_player({
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 20.0,
            'health': 1000, 'max_health': 1000})
        for frame in range(60):
            self.runtime.update(1.0 / 30.0, 1.0 + frame / 30.0,
                                players=[player])
            if len(calls) >= 2:
                break
        self.assertEqual([11, 12], [call[0] for call in calls[:2]])
        for bot_id, clear, native_calls in calls[:2]:
            self.assertTrue(clear)
            self.assertTrue(native_calls)
            self.assertTrue(all(call[2] == 2.15 for call in native_calls),
                            native_calls)
