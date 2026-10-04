"""Shared static routes ignore soft objects; contact keeps real kinetic inputs."""
import copy
import math
import unittest

import test_port_0922_bot_runtime as bot_fixture


class NavigationCapabilityProviderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.runtime = self.fixture.runtime
        self.travel = bot_fixture._combat_descriptor()
        self.travel.physics.update(weight=10000.0, speedLimits=(20.0, 10.0))
        self.siege = copy.deepcopy(self.travel)
        self.siege.physics['speedLimits'] = (5.0 / 3.6, 5.0 / 3.6)
        self.composite = bot_fixture.types.SimpleNamespace(
            defaultVehicleDescr=self.travel, siegeVehicleDescr=self.siege,
            hasSiegeMode=True)
        self.runtime.descriptor_resolver = lambda unused: self.composite
        self.start = copy.deepcopy(self.fixture.start)
        self.start['bots'].append(
            {'id': 12, 'team': 2, 'slot': 1, 'name': 'Other Bot'})

    @staticmethod
    def capability(mass, cap):
        return (('stock1513', mass, cap), mass, cap)

    def begin(self):
        return self.runtime.battle_start(self.start)

    def test_planning_policy_is_shared_across_skills_gears_and_vehicle_modes(self):
        self.begin()
        policy = self.runtime.navigation_planning_capability()
        self.assertEqual((('ignore_destructibles', 1), None, None), policy)
        for rating in (0.0, 0.34, 0.67, 1.0):
            self.runtime._bot_ratings[11] = rating
            for bot_id in (11, 12, 999):
                for direction in (-1.0, 1.0):
                    with self.subTest(rating=rating, bot=bot_id,
                                      direction=direction):
                        self.assertIs(policy,
                                      self.runtime.navigation_planning_capability(
                                          bot_id, direction))
        self.runtime._install_bot_descriptor(11, self.runtime.states[11], 2)
        self.assertIs(policy, self.runtime.navigation_planning_capability(11))
        self.assertEqual(self.capability(10000.0, -10.0),
                         self.runtime.navigation_crush_capability(11, -1.0))

    def test_manifest_publishes_directional_caps_and_reuses_immutable_values(self):
        self.assertIsNone(self.runtime.navigation_crush_capability(11))
        self.begin()
        forward = self.runtime.navigation_crush_capability(11)
        reverse = self.runtime.navigation_crush_capability(11, -1.0)
        self.assertEqual(self.capability(10000.0, 20.0), forward)
        self.assertEqual(self.capability(10000.0, -10.0), reverse)
        self.assertIsNone(self.runtime.navigation_crush_capability(999))
        for unused in range(10):
            self.assertIs(forward, self.runtime.navigation_crush_capability(11))
            self.assertIs(reverse, self.runtime.navigation_crush_capability(11, -2))

    def test_siege_uses_mounted_travel_gear_without_changing_another_consumer(self):
        self.begin()
        forward = self.runtime.navigation_crush_capability(11)
        reverse = self.runtime.navigation_crush_capability(11, -1)
        other = self.runtime.navigation_crush_capability(12)
        self.runtime._install_bot_descriptor(11, self.runtime.states[11], 2)
        self.assertIs(self.siege, self.runtime._descriptors[11])
        self.assertIs(forward, self.runtime.navigation_crush_capability(11))
        self.assertIs(reverse, self.runtime.navigation_crush_capability(11, -1))
        self.assertIs(other, self.runtime.navigation_crush_capability(12))
        self.runtime._install_bot_descriptor(11, self.runtime.states[11], 0)
        self.assertIs(self.travel, self.runtime._descriptors[11])
        self.assertIs(forward, self.runtime.navigation_crush_capability(11))

    def test_active_descriptor_mass_and_speed_change_only_its_snapshot(self):
        self.begin()
        original = self.runtime.navigation_crush_capability(11)
        other = self.runtime.navigation_crush_capability(12)
        replacement = copy.deepcopy(self.travel)
        replacement.physics.update(weight=8000.0, speedLimits=(16.0, 6.0))
        self.runtime._descriptor_pairs[11] = (replacement, None)
        self.runtime._install_bot_descriptor(11, self.runtime.states[11], 0)
        self.assertEqual(self.capability(8000.0, 16.0),
                         self.runtime.navigation_crush_capability(11))
        self.assertEqual(self.capability(8000.0, -6.0),
                         self.runtime.navigation_crush_capability(11, -1))
        self.assertEqual(self.capability(10000.0, 20.0), original)
        self.assertIs(other, self.runtime.navigation_crush_capability(12))

    def test_unrelated_invalid_or_partial_bot_cannot_revoke_a_valid_capability(self):
        self.begin()
        original = self.runtime.navigation_crush_capability(11)
        for invalid in (None, 0.0, float('nan'), float('inf')):
            with self.subTest(weight=invalid):
                replacement = copy.deepcopy(self.travel)
                if invalid is None:
                    replacement.physics.pop('weight')
                else:
                    replacement.physics['weight'] = invalid
                self.runtime._descriptor_pairs[12] = (replacement, None)
                self.runtime._install_bot_descriptor(
                    12, self.runtime.states[12], 0)
                self.assertIsNone(self.runtime.navigation_crush_capability(12))
                self.assertIs(original,
                              self.runtime.navigation_crush_capability(11))
        self.runtime.states[13] = {'id': 13}
        self.runtime._refresh_navigation_crush_profiles()
        self.assertIsNone(self.runtime.navigation_crush_capability(13))
        self.assertIs(original, self.runtime.navigation_crush_capability(11))

    def test_new_round_removes_departed_bot_and_replaces_old_numeric_inputs(self):
        self.begin()
        original = self.runtime.navigation_crush_capability(11)
        replacement = copy.deepcopy(self.travel)
        replacement.physics.update(weight=6000.0, speedLimits=(12.0, 4.0))
        self.runtime.descriptor_resolver = lambda unused: replacement
        next_round = copy.deepcopy(self.start)
        next_round['round_id'] += 1
        next_round['bots'] = next_round['bots'][:1]
        self.runtime.battle_start(next_round)
        self.assertEqual(self.capability(6000.0, 12.0),
                         self.runtime.navigation_crush_capability(11))
        self.assertIsNone(self.runtime.navigation_crush_capability(12))
        self.assertEqual(self.capability(10000.0, 20.0), original)

    def test_authority_manifest_installs_current_descriptors_before_proof_reuse(self):
        manifest = self.begin()[0]['bots']
        original = self.runtime.navigation_crush_capability(11)
        self.runtime.battle_start(dict(self.start, bot_authority_id=2))
        replacement = copy.deepcopy(self.travel)
        replacement.physics.update(weight=7000.0, speedLimits=(15.0, 5.0))
        self.runtime.descriptor_resolver = lambda unused: replacement
        self.runtime.battle_start(dict(self.start, bot_manifest=manifest))
        self.assertEqual(self.capability(7000.0, 15.0),
                         self.runtime.navigation_crush_capability(11))
        self.assertEqual(self.capability(10000.0, 20.0), original)


class NavigationCapabilityDirectionABITests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        self.arguments = ((1.0, 2.0, 3.0), 0.25, 7.5,
                          bot_fixture._combat_descriptor(), 4.0, 2.15,
                          (('stock1513', 10000.0, 20.0), 10000.0, 20.0))

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

    def test_native_seventh_argument_is_preserved_without_retrying_body_error(self):
        calls = []

        def direction(position, yaw, speed, descriptor, maximum_distance,
                      corridor_half_width, native_capability):
            calls.append((position, yaw, speed, descriptor, maximum_distance,
                          corridor_half_width, native_capability))
            raise TypeError('native callback failed after observable work')

        runtime = self.module.BotRuntime(1, direction_probe=direction)
        result = runtime._probe_direction(*self.arguments)
        self.assertFalse(result['clear'])
        self.assertTrue(result['probe_failed'])
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

        def direction(yaw, maximum_distance=None, drive_direction=1.0):
            calls.append((yaw, maximum_distance, drive_direction))
            raise TypeError('native query failed after observable work')

        self.assertFalse(LocalDriver()._clear(
            direction, math.pi, 5.6, drive_direction=-1.0))
        self.assertEqual([(math.pi, 5.6, -1.0)], calls)

    def test_runtime_candidate_intent_uses_same_static_policy_for_both_gears(self):
        calls, verdicts = [], []

        class CandidateAdapter(bot_fixture._FixedAdapter):
            def decide(self, state, clear):
                # The route is behind the hull: pivot then drive forwards.
                # The same world heading can separately be a backing escape.
                verdicts.append((clear(0.0, 5.6),
                                 clear(0.0, 5.6, -1.0),
                                 clear(0.0, 5.6)))
                return dict(self.command)

        def direction(position, yaw, speed, descriptor, maximum_distance,
                      corridor_half_width, native_capability):
            calls.append((yaw, maximum_distance, native_capability))
            clear = native_capability == (('ignore_destructibles', 1), None, None)
            return {'clear': clear, 'collision': not clear, 'slope': 0.0}

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
        self.assertEqual(2, len(candidate_calls))
        self.assertTrue(all(entry[2] is runtime.navigation_planning_capability()
                            for entry in candidate_calls))


class NavigationCapabilityGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        self.descriptor = bot_fixture._combat_descriptor()
        self.descriptor.physics.update(weight=10000.0, speedLimits=(20.0, 10.0))
        self.native_calls = []
        self.world_blocked = False

        def obstacle(start, end, width, native_capability=None, evidence=None):
            # An empty native world; the real grid still performs edge lookup,
            # capability scoping, receipts and caching around this boundary.
            self.native_calls.append((start, end, width, native_capability))
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

    def assert_consumer_reached_native(self, bot_id, direction=1.0):
        expected = self.runtime.navigation_planning_capability(bot_id, direction)
        self.assertTrue(self.native_calls)
        self.assertTrue(all(call[3] is expected for call in self.native_calls),
                        self.native_calls)

    def reset_receipts(self):
        self.runtime.navigator.grid.invalidate_native_review()
        self.native_calls[:] = []

    def test_offset_lane_goal_uses_shared_static_policy(self):
        for bot_id in (11, 12):
            with self.subTest(bot_id=bot_id):
                self.reset_receipts()
                result = self.runtime._safe_route_lane_goal(
                    self.runtime.states[bot_id], (0.0, 0.0, 8.0),
                    (0.0, 1.0), 4.0, 0.0, 1.0)
                self.assertEqual((-4.0, 0.0, 8.0), result)
                self.assert_consumer_reached_native(bot_id)

    def test_direct_navigation_target_shares_receipt_identity_between_vehicles(self):
        goal = (0.0, 0.0, 8.0)
        for bot_id in (11, 12):
            with self.subTest(bot_id=bot_id):
                self.reset_receipts()
                result = self.runtime._navigation_target(
                    bot_id, (0.0, 0.0, 0.0), goal,
                    {'combat_mode': 'hold'}, self.runtime.states[bot_id])
                self.assertEqual(goal, result)
                self.assert_consumer_reached_native(bot_id)
        progress = self.runtime.navigator.bot_direct_progress
        self.assertEqual({11, 12}, set(progress))
        self.assertEqual(progress[11]['path_key'], progress[12]['path_key'])

    def test_local_route_lane_offset_uses_shared_static_policy(self):
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
        self.assert_consumer_reached_native(12)

    def test_same_map_new_round_reproves_identical_capability_against_new_world(self):
        navigator = self.runtime.navigator
        capability = self.runtime.navigation_planning_capability(11)
        start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 24.0)
        for frame in range(10):
            navigator.begin_frame(0.1)
            try:
                navigator.next_target(
                    11, start, goal, ('route', 2, 'forest', 1),
                    1.0 + frame * 0.1, native_capability=capability)
            finally:
                navigator.end_frame()
            if navigator.paths:
                break
        self.assertTrue(navigator.paths)
        self.assertTrue(navigator.bot_states)
        self.assertTrue(navigator.grid._native_review_edges)
        self.world_blocked = True
        self.native_calls[:] = []
        self.runtime.battle_start(dict(self.fixture.start, round_id=6))
        self.assertIs(navigator, self.runtime.navigator)
        self.assertEqual(capability,
                         self.runtime.navigation_planning_capability(11))
        self.assertFalse(navigator.paths)
        self.assertFalse(navigator.bot_states)
        self.assertFalse(navigator.searches)
        self.assertFalse(navigator.grid._native_review_edges)
        navigator.grid.review_native_corridor(start, goal)
        self.assertFalse(navigator.grid.segment_clear(
            start, (0.0, 0.0, 8.0),
            self.runtime.navigation_planning_capability(11)))
        self.assert_consumer_reached_native(11)

    def test_corridor_probe_uses_shared_policy_in_forward_and_reverse(self):
        for bot_id, yaw, direction in ((11, 0.0, 1.0),
                                      (11, math.pi, -1.0), (12, 0.0, 1.0)):
            with self.subTest(bot_id=bot_id, yaw=yaw, direction=direction):
                self.reset_receipts()
                capability = self.runtime.navigation_planning_capability(
                    bot_id, direction)
                self.assertTrue(self.runtime._planner_corridor_clear(
                    (0.0, 0.0, 0.0), yaw, 0.0,
                    native_capability=capability))
                self.assert_consumer_reached_native(bot_id, direction)

    def test_same_segment_reuses_native_proof_across_vehicle_and_gear_changes(self):
        self.reset_receipts()
        grid = self.runtime.navigator.grid
        start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 8.0)
        first_policy = self.runtime.navigation_planning_capability(11)
        self.assertTrue(grid.segment_clear(start, goal, first_policy))
        self.assert_consumer_reached_native(11)
        calls = tuple(self.native_calls)
        for bot_id, direction in ((12, 1.0), (11, -1.0), (12, -1.0)):
            policy = self.runtime.navigation_planning_capability(bot_id, direction)
            self.assertTrue(grid.segment_clear(start, goal, policy))
            self.assertEqual(calls, tuple(self.native_calls))

    def test_queued_cover_callback_uses_shared_static_policy(self):
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
            expected = self.runtime.navigation_planning_capability(bot_id)
            self.assertTrue(all(call[3] is expected for call in native_calls),
                            native_calls)
