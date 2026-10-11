"""Pending navigation lifecycle regressions from Airfield report 113632.

The real next_target, blocked-step escalation and _path lifecycle run here.
Zero explicit frame credit deliberately keeps real A* jobs pending; it is a
controlled scheduler fixture, not a claim about Windows frame timing.
"""
import math
import unittest
from unittest import mock

from test_port_0922_navigation import TerrainNavigator
from test_port_0922_bot_runtime import _flat_open_graph
from gui.mods.offline_lan_0922.ai.navigation import BAKED_SHALLOW_WATER


class WallScene:
    """A U-shaped physical wall with the same footprint erased from its bake."""
    def __init__(self):
        self.walls = [(-8.0, -10.0, -8.0, 16.0),
                      (8.0, -10.0, 8.0, 16.0),
                      (-8.0, 16.0, 8.0, 16.0)]
        self.calls = 0

    def blocked(self, start, end, width):
        self.calls += 1
        for x0, z0, x1, z1 in self.walls:
            near, far = 0.0, 1.0
            for position, delta, low, high in (
                    (start[0], end[0] - start[0], x0 - width, x1 + width),
                    (start[2], end[2] - start[2], z0 - width, z1 + width)):
                if abs(delta) < 1e-9:
                    if not low <= position <= high:
                        break
                else:
                    first, last = (low - position) / delta, (high - position) / delta
                    near, far = max(near, min(first, last)), min(far, max(first, last))
                    if near > far:
                        break
            else:
                return True
        return False

    def navigator(self):
        graph = _flat_open_graph()
        grid = TerrainNavigator(lambda *unused: 0.0, baked_graph=graph).grid
        for z in range(graph['height']):
            for x in range(graph['width']):
                point = grid.point_for((x, z), 0.0)
                if self.blocked(point, point, 2.15):
                    graph['heights_mm'][z * graph['width'] + x] = None
        nav = TerrainNavigator(lambda *unused: 0.0, self.blocked, baked_graph=graph)
        nav.grid.review_native_corridor((0.0, 0.0, 0.0), (0.0, 0.0, 40.0))
        self.calls = 0
        return nav


class PendingPrefixTests(unittest.TestCase):
    def setUp(self):
        self.scene = WallScene()
        self.nav = self.scene.navigator()
        self.start = (0.0, 0.0, 0.0)
        self.goal = (0.0, 0.0, 40.0)
        self.request = ('route_join', 27, 'u_wall')

    def target(self, position, now):
        self.nav.begin_frame(0.0)
        try:
            return self.nav.next_target(27, position, self.goal, self.request, now)
        finally:
            self.nav.end_frame()

    def available_prefix(self):
        self.target(self.start, 0.0)
        key = self.nav._cache_key(self.request, self.goal)
        search = self.nav.searches[key]
        while len(search.proved_prefix(self.nav.grid)) < 3:
            search.step(1)
            self.assertFalse(search.done)
        return key, search

    def test_pending_path_retreats_out_of_u_wall_and_complete_path_takes_over(self):
        # A one-credit scheduler deliberately exposes a long pending phase.
        # Every actual movement step is checked against physical walls too;
        # this test cannot pass by declaring all native directions clear.
        current = self.start
        exited_while_pending = False
        completed = False
        visited = set()
        for step in range(1000):
            target = self.target(current, step * 0.1)
            distance = math.hypot(target[0] - current[0], target[2] - current[2])
            if distance > 1.5:
                fraction = min(0.35, distance) / distance
                following = (current[0] + (target[0] - current[0]) * fraction,
                             0.0, current[2] + (target[2] - current[2]) * fraction)
                self.assertFalse(self.scene.blocked(current, following, 2.15))
                current = following
            visited.add(self.nav.grid.cell_for(current))
            if self.nav.searches and current[2] < -12.0:
                exited_while_pending = True
            for key, search in list(self.nav.searches.items()):
                search.step(1)
                if search.done:
                    self.nav._finish_search(key, search, step * 0.1)
                    completed = bool(search.result)
            if math.hypot(current[0] - self.goal[0], current[2] - self.goal[2]) <= 1.5:
                break
        self.assertTrue(exited_while_pending)
        self.assertTrue(completed)
        self.assertLess(step, 999)
        self.assertGreater(len(visited), 15)
        self.assertNotIn('pending_prefix', self.nav.bot_states[27])

    def test_search_expansion_does_not_rebuild_or_replace_an_unreached_prefix(self):
        unused_key, search = self.available_prefix()
        with mock.patch.object(search, 'proved_prefix', wraps=search.proved_prefix) as copy:
            first = self.target(self.start, 0.1)
            for tick in range(1, 30):
                search.step(1)
                self.assertEqual(first, self.target(self.start, 0.1 + tick * 0.01))
            self.assertEqual(1, copy.call_count)

    def test_pending_prefix_rechecks_new_wall_water_and_bot_penalty(self):
        for changed in ('wall', 'water', 'penalty'):
            with self.subTest(changed=changed):
                self.setUp()
                key, search = self.available_prefix()
                first = self.target(self.start, 0.1)
                state = self.nav.bot_states[27]
                if changed == 'wall':
                    self.scene.walls.append((-4.0, 2.0, 4.0, 2.0))
                    self.nav.grid.invalidate_native_review()
                elif changed == 'water':
                    hazards = list(self.nav.grid._baked_hazards)
                    hazards[self.nav.grid._baked_flat_index(
                        self.nav.grid.cell_for(first))] |= BAKED_SHALLOW_WATER
                    self.nav.grid._baked_hazards = hazards
                    self.nav.grid._baked_corridor_cache.clear()
                    self.nav.grid._baked_corridor_order.clear()
                else:
                    self.nav.bot_failed_edges[27] = dict((edge, (100.0, 240.0))
                        for edge in self.nav.grid._edge_keys_for_segment(self.start, first))
                target = self.nav._pending_search_target(
                    27, self.start, self.goal, 0.2, state, key, None)
                self.assertIsNone(target)
                self.assertNotIn('pending_prefix_target', state)
                self.assertIsNone(state.get('controlled_shallow_target'))

    def test_cancel_completion_and_profile_change_retire_prefix_ownership(self):
        for action in ('cancel', 'complete', 'profiles'):
            with self.subTest(action=action):
                self.setUp()
                key, search = self.available_prefix()
                self.target(self.start, 0.1)
                state = self.nav.bot_states[27]
                self.assertIn('pending_prefix', state)
                if action == 'cancel':
                    self.nav._cancel_bot_searches(27)
                elif action == 'complete':
                    while not search.done:
                        search.step(256)
                    self.nav._finish_search(key, search, 1.0)
                else:
                    self.nav.invalidate_native_planning()
                    state = self.nav.bot_states.get(27, {})
                    self.assertTrue(self.nav.grid.prebaked)
                self.assertNotIn('pending_prefix', state)
                self.assertNotIn('last_target', state)
                self.assertNotIn(key, self.nav.searches)

    def test_short_fallback_arrivals_do_not_reset_strategic_stall(self):
        state = {'request_key': ('route', 27), 'replan_generation': 0}
        self.nav._reset_macro_progress(state, self.start, self.goal, 0.0)
        with mock.patch.object(self.nav, '_start_macro_replan', return_value=True) as replan:
            for tick in range(27):
                current = ((-1.0 if tick % 2 else 1.0), 0.0, 0.0)
                # The next tiny point was reached on every observation, while
                # the stable strategic distance never improved.
                state['last_target'] = current
                if tick == 0:
                    self.nav._remember_local_fallback(current, self.goal, state)
                if tick % 2:
                    state['path_key'] = None
                else:
                    state['path_key'] = ('failed', tick)
                self.nav._observe_macro_progress(27, state, current, self.goal, tick * 0.5)
            # A local loop must not cancel a healthy search and replace it
            # with another four-second escape. Keep its deadline, stop only
            # repeated temporary legs and admit its next new-area prefix.
            self.assertFalse(replan.called)
            self.assertTrue(state['temporary_stalled'])
            self.assertEqual(1, state['macro_progress_replans'])

    def test_arrived_fallback_continues_from_its_fixed_endpoint(self):
        nav = TerrainNavigator(lambda *unused: 0.0, lambda *unused: False,
                               baked_graph=_flat_open_graph())
        current, arrived, goal = (1.4, 0.0, 4.0), (0.0, 0.0, 4.0), (0.0, 0.0, 40.0)
        state = {'request_key': ('route', 27), 'last_target': arrived}
        nav._remember_local_fallback(arrived, goal, state)
        expected = nav.grid.safe_local_target(arrived, goal, 0.0)
        self.assertEqual(expected, nav._fallback_target(27, current, goal, 0.1, None, state))
        state['request_key'] = ('route', 27, 'new')
        choose = mock.Mock(wraps=nav.grid.safe_local_target)
        nav.grid.safe_local_target = choose
        nav._fallback_target(27, current, goal, 0.2, None, state)
        self.assertEqual(current, choose.call_args.args[0])

    def test_arrival_connector_keeps_actual_hull_wall_and_water_checks(self):
        for blocked in ('wall', 'water', 'deferred'):
            with self.subTest(blocked=blocked):
                scene = WallScene()
                scene.walls = [(2.5, 4.3, 2.5, 4.3)] if blocked == 'wall' else []
                nav = TerrainNavigator(lambda *unused: 0.0, scene.blocked,
                                       baked_graph=_flat_open_graph())
                current, arrived, goal = (1.4, 0.0, 4.0), (0.0, 0.0, 4.0), (0.0, 0.0, 40.0)
                state = {'request_key': ('route', 27), 'last_target': arrived}
                nav._remember_local_fallback(arrived, goal, state)
                # The candidate is safe from the fixed point; only the hull's
                # realised offset is blocked. Keep that distinction observable.
                candidate = nav.grid.safe_local_target(arrived, goal, 0.0)
                self.assertIsNotNone(candidate)
                if blocked == 'water':
                    hazard = nav.grid.segment_has_baked_hazard
                    nav.grid.segment_has_baked_hazard = lambda start, end, mask: (
                        True if tuple(start) == current else hazard(start, end, mask))
                elif blocked == 'deferred':
                    nav.grid.obstacle_probe = lambda *unused: 'deferred'
                target = nav._fallback_target(27, current, goal, 0.1, None, state)
                self.assertEqual(current, target)
                self.assertNotIn('local_fallback_target', state)
                self.assertIn('local_fallback_episode', state)


class DeferredNavigationProofTests(unittest.TestCase):
    def test_profile_invalidation_inside_probe_cannot_refill_old_receipts(self):
        for baked in (False, True):
            with self.subTest(baked=baked):
                calls = [0]
                nav = TerrainNavigator(lambda *unused: 0.0, cell_size=4.0,
                    baked_graph=_flat_open_graph() if baked else None)
                def obstacle(*unused):
                    calls[0] += 1
                    if calls[0] == 1:
                        nav.invalidate_native_planning()
                        return False
                    return True
                nav.grid.obstacle_probe = obstacle
                start, goal = (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)
                nav.grid.review_native_corridor(start, goal)
                self.assertFalse(nav.grid.segment_clear(start, goal))
                self.assertFalse(nav.grid._native_review_edges)
                self.assertFalse(nav.grid._segment_cache)
                self.assertFalse(nav.grid.segment_clear(start, goal))
                self.assertEqual(2, calls[0])

    def test_deferred_job_spends_one_native_attempt_per_render_frame(self):
        obstacle = mock.Mock(return_value='deferred')
        nav = TerrainNavigator(lambda *unused: 0.0, obstacle,
                               baked_graph=_flat_open_graph())
        start, goal = (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)
        nav.grid.review_native_corridor(start, goal)
        key = nav._cache_key(('route_join', 27), goal)
        nav.searches[key] = nav.grid.begin_plan(start, goal)
        nav.search_times[key] = 0.0
        for frame, elapsed in enumerate((0.1, 1.0)):
            obstacle.reset_mock()
            nav.begin_frame(elapsed)
            nav.tick(float(frame))
            nav.tick(float(frame))
            nav._path(('route_join', 27), start, goal, float(frame), None)
            nav.end_frame()
            self.assertEqual(1, obstacle.call_count)
            self.assertIn(key, nav.searches)
            self.assertNotIn(key, nav.paths)
        obstacle.reset_mock()
        self.assertIsNone(nav.grid.plan(start, goal))
        self.assertEqual(1, obstacle.call_count)

    def test_deferred_native_edge_stays_pending_and_retries_without_negative_cache(self):
        for baked in (False, True):
            with self.subTest(baked=baked):
                ready = [False]
                nav = TerrainNavigator(lambda *unused: 0.0,
                    lambda *unused: False if ready[0] else 'deferred',
                    cell_size=4.0, baked_graph=_flat_open_graph() if baked else None)
                start, goal = (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)
                nav.grid.review_native_corridor(start, goal)
                self.assertFalse(nav.grid.dry_segment_clear(start, goal, 0.0))
                search = nav.grid.begin_plan(start, goal)
                search.step(50)
                self.assertFalse(search.done)
                self.assertFalse(nav.grid._native_review_edges)
                self.assertFalse(nav.grid._edge_cache)
                self.assertFalse(nav.grid._segment_cache)
                self.assertIsNone(nav.grid.plan(start, goal))
                ready[0] = True
                search.step(1000)
                self.assertTrue(search.done)
                self.assertTrue(search.result)
                self.assertTrue(nav.grid.dry_segment_clear(start, goal, 1.0))


class PendingRecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.navigator = TerrainNavigator(lambda *unused: 0.0, cell_size=4.0)
        # Require A* while giving the pending driver a short valid endpoint.
        self.navigator.grid.dry_segment_clear = lambda *unused: False
        self.navigator.grid.safe_local_target = lambda current, *unused: (
            current[0] + 2.08, 0.0, current[2])
        self.current = (0.0, 0.0, 0.0)
        self.goal = (100.0, 0.0, 100.0)
        self.request = ('route_join', 27, 2, 'north_runway', 1, -10000, 0)

    def next_target(self, position, now):
        self.navigator.begin_frame(0.0)
        try:
            return self.navigator.next_target(
                27, position, self.goal, self.request, now)
        finally:
            self.navigator.end_frame()

    def begin_recovery(self):
        self.next_target(self.current, 0.0)
        for now in (0.0, 0.4, 0.8, 1.2):
            self.navigator.report_blocked_step(
                27, self.current, (20.0, 0.0, 0.0), now)
        self.next_target(self.current, 1.3)
        self.assertTrue(self.navigator.bot_states[27]['replan_active'])
        self.assertEqual(1, len(self.navigator.searches))
        recovery_key = next(iter(self.navigator.searches))
        self.assertEqual('recovery', recovery_key[0][0])
        self.assertEqual(0, self.navigator.searches[recovery_key].steps)
        return recovery_key

    def test_displacement_retires_abandoned_recovery_search(self):
        recovery_key = self.begin_recovery()

        # Three metres ends contact recovery. The obsolete recovery must no
        # longer divide the room's search budget with its replacement request.
        self.next_target((3.0, 0.0, 0.0), 1.4)

        self.assertFalse(self.navigator.bot_states[27]['replan_active'])
        self.assertNotIn(recovery_key, self.navigator.searches)
        self.assertNotIn(recovery_key, self.navigator.search_times)
        request_key = self.navigator._cache_key(self.request, self.goal)
        self.assertIn(request_key, self.navigator.searches)

    def test_displacement_preserves_other_live_stages_and_search_owners(self):
        self.begin_recovery()
        retained = {}
        self.navigator.begin_frame(0.0)
        try:
            for request in (
                    ('join', 27, (0, 0)) + self.request,
                    ('route', 2, 'north_runway', 1),
                    ('recovery', 28, 1, 'route_join', 28)):
                key, path = self.navigator._path(
                    request, self.current, self.goal, 1.3, None)
                self.assertIsNone(path)
                retained[key] = self.navigator.searches[key]
        finally:
            self.navigator.end_frame()

        self.next_target((3.0, 0.0, 0.0), 1.4)

        for key, search in retained.items():
            self.assertIs(search, self.navigator.searches.get(key))
            self.assertIn(key, self.navigator.search_times)


if __name__ == '__main__':
    unittest.main()
