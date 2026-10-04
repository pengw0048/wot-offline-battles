"""September 21 navigation report regressions; no native playtest claim."""

import json
import math
import types
import unittest
from unittest import mock
from pathlib import Path

from test_port_0922_bot_runtime import _flat_open_graph, _load
import test_port_0922_navigation as navigation_tests
from test_port_0922_server_bot_ai import (
    BotPlanner, _bot, _capture_defense, _route, _state)
from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator


ROOT = Path(__file__).resolve().parents[1]


class AirfieldPendingEscapeTests(unittest.TestCase):
    # 225339 hidden-worker.log, final stalled authority poses. These are
    # measured vehicle positions, not replacements for the map's geometry.
    REVIEWED_EDGE_POSES = (
        (2, (343.719598721, -0.179998875, -161.890627428),
         (282.741702265, -8.03, -170.944681764)),
        (18, (-296.954205565, -0.179998875, -191.789368014),
         (-313.080650450, -0.18, -134.894427191)),
        (25, (-288.609579134, -0.179998875, -166.602784518),
         (-312.005198857, -5.846, -54.322412940)),
        (26, (-278.062505535, -0.179999352, -144.588467340),
         (-234.0, 0.0, -206.0)),
        (27, (-286.026167075, -0.179998875, -186.384272894),
         (-329.005399570, -6.35, -5.767693003)),
        (30, (-268.175655378, 0.128514290, -137.744136455),
         (-229.339976642, -8.17, -204.187768694)),
    )

    def test_reviewed_airfield_boundary_uses_live_support_for_real_exit(self):
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        for bot_id, current, goal in self.REVIEWED_EDGE_POSES:
            with self.subTest(bot=bot_id):
                probes = []
                def obstacle(start, end, half_width):
                    probes.append((start, end, half_width))
                    return False
                nav = TerrainNavigator(lambda x, z, hint: current[1],
                    obstacle, baked_graph=graph)
                self.assertIsNone(nav.grid._baked_cell_height(
                    nav.grid.cell_for(current)))
                nav.grid.review_native_corridor(current, goal)
                selected = nav._pending_target(
                    bot_id, current, goal, 1.0, {'pending_since': 0.0})
                self.assertGreater(math.hypot(selected[0] - current[0],
                                               selected[2] - current[2]), 1.5)
                self.assertTrue(probes)
                self.assertTrue(any(start == current for start, _, _ in probes))
                self.assertTrue(nav.grid.dry_segment_clear(current, selected, 1.0))

    def test_reviewed_boundary_exit_requires_live_ground_and_collision_proof(self):
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        bot_id, current, goal = self.REVIEWED_EDGE_POSES[2]
        for ground, obstacle in (
                (lambda *args: None, lambda *args: False),
                (lambda *args: float('nan'), lambda *args: False),
                (lambda x, z, hint: current[1], lambda *args: True),
                (lambda x, z, hint: current[1] if (x, z) ==
                 (current[0], current[2]) else current[1] + 5.0,
                 lambda *args: False)):
            with self.subTest(ground=ground, obstacle=obstacle):
                nav = TerrainNavigator(ground, obstacle, baked_graph=graph)
                nav.grid.review_native_corridor(current, goal)
                self.assertEqual(current, nav._pending_target(
                    bot_id, current, goal, 1.0, {'pending_since': 0.0}))

    def test_reviewed_boundary_does_not_eagerly_probe_a_long_shortcut(self):
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        unused_bot, current, goal = self.REVIEWED_EDGE_POSES[2]
        ground, obstacle = mock.Mock(return_value=-.18), mock.Mock(return_value=False)
        nav = TerrainNavigator(ground, obstacle, baked_graph=graph)
        nav.grid.review_native_corridor(current, goal)
        far = (-226.0, -.18, -186.0)
        self.assertTrue(nav.grid._baked_corridor(current, far)[0])
        self.assertFalse(nav.grid.segment_clear(current, far))
        ground.assert_not_called()
        obstacle.assert_not_called()
        selected = nav._pending_target(25, current, goal, 1.0, {'pending_since': 0.0})
        self.assertNotEqual(current, selected)
        ground.reset_mock()
        self.assertTrue(nav.grid.segment_clear(current, selected))
        self.assertLessEqual(ground.call_count, 10)

    def test_raw_hazard_omitted_by_baked_start_snap_still_blocks_escape(self):
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        unused_bot, current, goal = self.REVIEWED_EDGE_POSES[2]
        ground, obstacle = mock.Mock(return_value=0.0), mock.Mock(return_value=False)
        nav = TerrainNavigator(ground, obstacle, baked_graph=graph)
        # Select this precise connector rather than whichever fan candidate
        # ranks first: the regression is the hazard omitted by start snapping.
        cell = nav.grid._nearest_baked_cell(nav.grid.cell_for(current), 2)
        selected = nav.grid.point_for(cell, nav.grid._baked_cell_height(cell))
        self.assertNotEqual(nav.grid.cell_for(current), cell)
        self.assertTrue(nav.grid.segment_clear(current, selected))
        self.assertTrue(ground.called)
        self.assertTrue(obstacle.called)
        ground.reset_mock()
        obstacle.reset_mock()
        # The short escape ends in the cell used as the snapped start, so
        # baked hazards exclude it as "already occupied". The raw hull is
        # still outside that cell and must not enter new water or a cliff.
        self.assertEqual((cell,), nav.grid._baked_segment_cells(current, selected))
        graph['hazards'][cell[1] * graph['width'] + cell[0]] = 1
        nav.grid.review_native_corridor(current, goal)
        self.assertTrue(nav.grid._baked_corridor(current, selected)[0])
        self.assertFalse(nav.grid.segment_clear(current, selected))
        ground.assert_not_called()
        obstacle.assert_not_called()

    def test_reviewed_boundary_cannot_escape_outside_authored_map_bounds(self):
        nav = TerrainNavigator(mock.Mock(return_value=0.0),
            mock.Mock(return_value=False),
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        current, outside = (1.0, 0.0, 1.0), (-100.0, 0.0, -100.0)
        nav.grid.review_native_corridor(current, outside)
        self.assertFalse(nav.grid.segment_clear(current, outside))
        nav.grid.ground_probe.assert_not_called()
        nav.grid.obstacle_probe.assert_not_called()

    def test_reported_airfield_pockets_have_a_proved_rear_exit(self):
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        # 090604 hidden-worker: two hulls were outside the forward
        # routing corridor. A queued A* held them without invoking recovery.
        poses = (
            ((80.732559, -11.624972, -295.808058), (34.0, 0.0, -266.0)),
            ((-319.8, -0.18, -95.5), (-334.0, 0.0, -6.0)),
        )
        for current, goal in poses:
            with self.subTest(current=current):
                # A missing baked start now always requires live support and
                # collision proof, including before any contact review. These
                # flat/clear replies are controlled, not captured native BSP.
                ground = mock.Mock(return_value=current[1])
                obstacle = mock.Mock(return_value=False)
                nav = TerrainNavigator(ground, obstacle, baked_graph=graph)
                state = {'pending_since': 0.0}
                target = nav._pending_target(2, current, goal, 1.0, state)
                self.assertNotEqual(current, target)
                self.assertTrue(nav.grid.dry_segment_clear(current, target, 1.0))
                self.assertTrue(ground.called)
                self.assertTrue(obstacle.called)
                bearing = math.atan2(goal[0] - current[0], goal[2] - current[2])
                exit_bearing = math.atan2(target[0] - current[0], target[2] - current[2])
                offset = (exit_bearing - bearing + math.pi) % (2 * math.pi) - math.pi
                self.assertGreater(abs(offset), 1.75)

    def test_known_forward_exit_keeps_precedence_over_rear_candidates(self):
        nav = TerrainNavigator(lambda *unused: 0.0,
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        current, goal = (40, 0, 40), (40, 0, 60)
        target = nav.grid.safe_local_target(current, goal, 1.0)
        self.assertGreater(target[2], current[2])

    def test_complete_static_enclosure_still_holds(self):
        nav = TerrainNavigator(lambda *unused: 0.0, lambda *unused: True,
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        current, goal = (40, 0, 40), (40, 0, 60)
        nav.grid.review_native_corridor(current, goal)
        self.assertEqual(current, nav._pending_target(
            2, current, goal, 1.0, {'pending_since': 0.0}))


class BlockedPlannerReviewTests(unittest.TestCase):
    def setUp(self):
        self.runtime = object.__new__(_load().BotRuntime)
        self.calls = []
        self.runtime.navigator = types.SimpleNamespace(
            report_blocked_plan=lambda *args: self.calls.append(args) or True)
        self.command = dict(recovery_mode='blocked', movement_intent=True,
                            move_position=(20.0, 0.0, 40.0))

    def test_static_veto_is_reported_without_waiting_for_realised_motion(self):
        self.assertTrue(self.runtime._report_blocked_planner(
            (20, 0, 20), self.command,
            {0: dict(clear=False, collision=True, water=False)}))
        self.assertEqual([((20, 0, 20), (20, 0, 40))], self.calls)

    def test_traffic_holds_and_unavailable_samples_do_not_mark_terrain(self):
        for sample in ({}, dict(clear=False, collision=False),
                       dict(clear=False, collision=True, deferred=True),
                       dict(clear=False, collision=True, probe_failed=True)):
            self.assertFalse(self.runtime._report_blocked_planner(
                (20, 0, 20), self.command, {0: sample}))
        for changes in (dict(recovery_mode='friendly_yield'),
                        dict(movement_intent=False)):
            command = dict(self.command, **changes)
            self.assertFalse(self.runtime._report_blocked_planner(
                (20, 0, 20), command, {0: dict(clear=False, collision=True)}))
        self.assertEqual([], self.calls)

    def test_review_retires_static_shortcut_and_finds_other_route(self):
        def obstacle(start, end, half_width):
            # A wall at z=36 ends at x=36; the previously baked straight
            # corridor is stale, and x>=40 is the measured alternative.
            if abs(end[2] - start[2]) < 1e-9:
                return False
            progress = (36.0 - start[2]) / (end[2] - start[2])
            return (0 <= progress <= 1 and
                    start[0] + progress * (end[0] - start[0]) < 36 + half_width)
        nav = TerrainNavigator(lambda *unused: 0.0, obstacle,
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        start, goal = (20, 0, 20), (20, 0, 60)
        self.assertTrue(nav.grid.segment_clear(start, goal))
        self.assertTrue(nav.report_blocked_plan(start, goal))
        self.assertFalse(nav.grid.segment_clear(start, goal))
        path = nav.grid.plan(start, goal)
        self.assertEqual(goal, path[-1])
        self.assertGreaterEqual(max(point[0] for point in path), 40)
        self.assertFalse(any(obstacle(a, b, 2.15) for a, b in zip(path, path[1:])))

    def test_repeated_contact_reuses_review_region_and_native_edge_result(self):
        probe = mock.Mock(return_value=True)
        nav = TerrainNavigator(lambda *unused: 0.0, probe,
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        start, goal = (20, 0, 20), (20, 0, 40)
        key = ('stale',)
        nav.paths[key] = (start, goal)
        nav.path_times[key] = 0.0
        nav.path_hull_revisions[key] = 0
        self.assertTrue(nav.report_blocked_plan(start, goal))
        self.assertNotIn(key, nav.path_hull_revisions)
        self.assertFalse(nav.grid.segment_clear(start, goal))
        reviewed_cells = len(nav.grid._native_review_cells)
        class NoRepeatedExpansion(set):
            def add(self, cell):
                raise AssertionError('expanded twice')
        nav.grid._native_review_cells = NoRepeatedExpansion(
            nav.grid._native_review_cells)
        self.assertFalse(nav.report_blocked_plan(start, goal))
        self.assertFalse(nav.grid.segment_clear(start, goal))
        self.assertEqual(reviewed_cells, len(nav.grid._native_review_cells))
        probe.assert_called_once()


class FrameOwnedNavigationTests(unittest.TestCase):
    @staticmethod
    def runtime():
        module = _load()
        runtime = module.BotRuntime(1, control_seconds=module.WORKER_CONTROL_SECONDS)
        runtime.authority_id = 1
        runtime.adapter = object()
        runtime.navigator = TerrainNavigator(
            lambda *unused: 0.0, baked_graph=_flat_open_graph())
        return runtime

    def test_queued_route_completes_without_another_target_request(self):
        runtime = self.runtime()
        nav = runtime.navigator
        start, goal = (-56.0, 0.0, -56.0), (56.0, 0.0, 56.0)
        key = nav._cache_key(('local', 1, 'route'), goal)
        search = nav.grid.begin_plan(start, goal)
        nav.searches[key] = search
        nav.search_times[key] = 0.0
        for frame in range(1, 201):
            runtime.update(.05, frame * .05)
            if search.done:
                break
        self.assertTrue(search.done)
        self.assertEqual(goal, search.result[-1])
        self.assertNotIn(key, nav.searches)
        self.assertLessEqual(frame * .05, 1.0)

    def test_frame_and_target_requests_share_one_elapsed_search_budget(self):
        from gui.mods.offline_lan_0922.ai import navigation
        for elapsed in (.1, .2, .45):
            with self.subTest(elapsed=elapsed):
                runtime = self.runtime()
                nav = runtime.navigator
                start, goal = (-56.0, 0.0, -56.0), (56.0, 0.0, 56.0)
                request = ('local', 1, 'route')
                key = nav._cache_key(request, goal)
                def pending():
                    while True:
                        yield None
                search = navigation._TerrainSearch(pending(), 0)
                nav.searches[key] = search
                nav.search_times[key] = 0.0
                # These calls happen inside the opened authority frame, just
                # like multiple staggered Bot decisions and catch-up slices.
                original = runtime._prepare_visibility_frame
                def with_requests(*args):
                    for unused in range(5):
                        nav.next_target(1, start, goal, request, elapsed)
                    return original(*args)
                runtime._prepare_visibility_frame = with_requests
                runtime.update(elapsed, elapsed)
                expected = min(int(elapsed * navigation.SEARCH_EXPANSIONS_PER_SECOND),
                               navigation.MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME)
                self.assertEqual(expected, search.steps)
                self.assertEqual(0.0, nav.search_credit)

    def test_wait_diagnostics_distinguish_unstarted_and_serviced_search(self):
        nav = self.runtime().navigator
        start, goal = (-56.0, 0.0, -56.0), (56.0, 0.0, 56.0)
        request = ('local', 7, 'route')
        key = nav._cache_key(request, goal)
        nav.searches[key] = nav.grid.begin_plan(start, goal)
        nav.search_times[key] = 1.0
        nav.bot_states[7] = {'request_key': key, 'pending_since': 1.5}
        waiting = nav.bot_search_diagnostics(7, start, 2.0)
        self.assertEqual(0, waiting['searches'][0]['steps'])
        self.assertIsNone(waiting['searches'][0]['last_step_age_ms'])
        self.assertEqual(1000, waiting['searches'][0]['age_ms'])
        self.assertEqual(500, waiting['pending_ms'])
        nav.begin_frame(.001)
        nav.search_credit = 1.0
        nav.tick(2.0)
        serviced = nav.bot_search_diagnostics(7, start, 2.25)
        self.assertEqual(1, serviced['searches'][0]['steps'])
        self.assertEqual(250, serviced['searches'][0]['last_step_age_ms'])


class AdvancedCaptureTests(unittest.TestCase):
    def test_contact_loss_after_staging_hands_over_to_actual_circle(self):
        route = _route('lane', [(0, -100, False), (0, 100, False),
                               (0, 300, False)])
        manifest = [_bot(11, 1, 0, route, 'mediumTank')]
        states = [_state(11, 1, 0, 280)]
        planner = BotPlanner()
        order = planner.build_orders(
            manifest, states, [], 1.0, _capture_defense())['orders'][0]
        self.assertEqual('base_capture', order['combat_mode'])
        self.assertEqual((123.5, 456.25),
                         (order['move_position']['x'], order['move_position']['z']))


class ActiveWreckPathTests(unittest.TestCase):
    def test_new_wreck_retires_active_private_join_as_well_as_shared_route(self):
        nav = TerrainNavigator(lambda *unused: 0.0,
            baked_graph=navigation_tests.StaticHullNavigationTests._flat_graph())
        start, goal = (20, 0, 20), (20, 0, 60)
        request = ('route', 1, 'lane', 1)
        nav.next_target(1, start, goal, request, 0.0)
        active = nav._cache_key(('join', 1, (5, 5)) + request, goal)
        nav.paths[active] = (start, (20, 0, 40), goal)
        nav.path_times[active] = 0.0
        nav.path_hull_revisions[active] = nav.grid.static_hull_revision
        nav.bot_states[1].update(path_key=active, index=1,
                                 last_target=(20, 0, 40))
        nav.grid.set_static_hulls(((9, 20, 40, 0, 3.5, 1.7),))
        shared = nav._cache_key(request, goal)
        nav.paths[shared] = (start, (8, 0, 20), (8, 0, 60), goal)
        nav.path_hull_revisions[shared] = nav.grid.static_hull_revision
        target = nav.next_target(1, start, goal, request, 0.1)
        self.assertNotEqual(active, nav.bot_states[1]['path_key'])
        self.assertFalse(nav.grid.path_crosses_static_hull((start, target)))


if __name__ == '__main__':
    unittest.main()
