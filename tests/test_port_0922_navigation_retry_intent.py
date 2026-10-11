"""Keep one route intent across real A* retries and partial-search targets.

These engine-free scenes use real searches and explicit one-step scheduling.
Their walls and zero-credit frames are controlled fixtures, not Windows
collision or frame-timing acceptance.
"""
import unittest

from test_port_0922_navigation import TerrainNavigator
from test_port_0922_bot_runtime import _flat_open_graph


class RetryOriginTests(unittest.TestCase):
    @staticmethod
    def retry_target(request):
        # The distant wall prevents a direct shortcut while allowing the
        # first few A* nodes on either side of the vehicle's current pose.
        nav = TerrainNavigator(
            lambda *unused: 0.0,
            lambda start, end, unused_width:
                (start[2] - 50.0) * (end[2] - 50.0) <= 0.0,
            cell_size=4.0)
        anchor = (0.0, 0.0, 0.0)
        current = (0.0, 0.0, 20.0)
        goal = (0.0, 0.0, 100.0)
        key = nav._cache_key(request, goal)
        # The vehicle advanced during the previous unsuccessful search.
        # Its same-request failed cache is now old enough for a real retry.
        nav.paths[key] = ()
        nav.path_times[key] = 0.0

        def target(now):
            nav.begin_frame(0.0)
            try:
                return nav.next_target(
                    22, current, goal, request, now, anchor=anchor)
            finally:
                nav.end_frame()

        target(9.0)
        search = nav.searches[key]
        search.step(2)
        assert not search.done
        return current, anchor, search.progress['start'], target(9.1)

    def test_private_join_retry_does_not_send_vehicle_back_to_spawn(self):
        for kind in ('local', 'route_join', 'join', 'recovery', 'continue'):
            with self.subTest(kind=kind):
                current, unused_anchor, search_start, target = self.retry_target(
                    (kind, 22, 2, 'spawn_join', 1, 0, 0))

                # A prefix of the old spawn search would select z=4 and
                # command a sixteen-metre return after reaching 20.
                self.assertGreater(target[2], current[2])
                self.assertEqual(current, search_start)

    def test_shared_route_keeps_its_authored_anchor(self):
        current, anchor, search_start, target = self.retry_target(
            ('route', 2, 'shared_leg', 1))

        # Shared route geometry must not acquire one consuming vehicle's
        # current position when fixing a private spawn-join retry.
        self.assertEqual(anchor, search_start)
        self.assertEqual(current, target)

    def test_pending_private_tree_behind_the_hull_is_not_a_return_order(self):
        nav = TerrainNavigator(lambda *unused: 0.0,
            lambda start, end, unused_width:
                (start[2] - 50.0) * (end[2] - 50.0) <= 0.0, cell_size=4.0)
        goal = (0.0, 0.0, 100.0)
        request = ('route_join', 22, 'moving_hull')
        nav.begin_frame(0.0)
        nav.next_target(22, (0.0, 0.0, 0.0), goal, request, 0.0)
        nav.end_frame()
        search = nav.searches[nav._cache_key(request, goal)]
        search.step(2)
        current = (0.0, 0.0, 20.0)
        nav.begin_frame(0.0)
        target = nav.next_target(22, current, goal, request, 0.1)
        nav.end_frame()
        self.assertEqual(current, target)
        self.assertIs(search, nav.searches[nav._cache_key(request, goal)])


class RetryProgressTests(unittest.TestCase):
    def test_failed_search_prefixes_do_not_renew_a_stationary_route_intent(self):
        def closed_room(start, end, unused_width):
            # A connected interior with no exit: every attempt completes
            # with a genuine failed result after exploring several nodes.
            return any(abs(point[axis]) > 6.0
                       for point in (start, end) for axis in (0, 2))

        nav = TerrainNavigator(lambda *unused: 0.0, closed_room, cell_size=4.0)
        current = (0.0, 0.0, 0.0)
        goal = (0.0, 0.0, 100.0)
        request = ('route_join', 27, 'closed_room')
        issued = set()
        for tick in range(300):
            now = tick * 0.1
            nav.begin_frame(0.0)
            try:
                issued.add(nav.next_target(27, current, goal, request, now))
            finally:
                nav.end_frame()
            # One real search step per controlled frame exposes the pending
            # prefixes between the ordinary eight-second failure cooldowns.
            for key, search in list(nav.searches.items()):
                search.step(1)
                if search.done:
                    nav._finish_search(key, search, now)

        self.assertGreaterEqual(nav.search_failed, 2)
        self.assertGreater(len(issued), 1)
        self.assertGreaterEqual(nav.bot_states[27]['macro_progress_replans'], 1)

    def test_temporary_history_counts_actual_new_cells_and_allows_retreat(self):
        nav = TerrainNavigator(lambda *unused: 0.0, cell_size=4.0)
        goal = (0.0, 0.0, 100.0)
        state = {'request_key': ('route_join', 27), 'last_target': goal}
        nav._remember_local_fallback(goal, goal, state)
        for now, z in ((0.0, 0.0), (11.0, -4.0), (22.0, -8.0)):
            nav._observe_macro_progress(27, state, (0.0, 0.0, z), goal, now)
        self.assertFalse(state['temporary_stalled'])
        self.assertEqual(22.0, state['macro_progress_at'])
        nav._observe_macro_progress(27, state, (0.0, 0.0, -4.0), goal, 34.1)
        self.assertTrue(state['temporary_stalled'])
        self.assertTrue(nav._temporary_path_repeats(state,
            ((0.0, 0.0, 0.0), (0.0, 0.0, -8.0))))
        # A real exit may require retracing the visited leg before its new
        # final leg. The temporary suppression is not a forbidden-cell map.
        self.assertFalse(nav._temporary_path_repeats(state,
            ((0.0, 0.0, 0.0), (0.0, 0.0, -8.0), (4.0, 0.0, -8.0))))
        self.assertNotIn(nav.grid.cell_for((4.0, 0.0, -8.0)),
                         state['temporary_visited_cells'])
        nav._observe_macro_progress(27, state, (4.0, 0.0, -8.0), goal, 34.2)
        self.assertFalse(state['temporary_stalled'])

    def test_temporary_history_is_bounded_and_retired_by_complete_path(self):
        nav = TerrainNavigator(lambda *unused: 0.0, cell_size=4.0)
        goal = (0.0, 0.0, 10000.0)
        state = {'request_key': ('route_join', 27), 'last_target': goal}
        nav.bot_states[27] = state
        nav._remember_local_fallback(goal, goal, state)
        for step in range(2100):
            nav._observe_macro_progress(
                27, state, (step * 4.0, 0.0, 0.0), goal, step * 0.1)
        self.assertEqual(2048, len(state['temporary_visited_cells']))
        self.assertEqual(2048, len(state['temporary_visited_order']))
        nav._set_fallback_mode(27, None)
        self.assertNotIn('temporary_visited_cells', state)
        self.assertNotIn('local_fallback_episode', state)


class DeferredCachedPathTests(unittest.TestCase):
    def test_active_join_and_actual_connector_unknown_keep_existing_route(self):
        for source in ('active_join', 'actual_connector'):
            with self.subTest(source=source):
                deferred = [False]

                def obstacle(start, end, unused_width):
                    if deferred[0] and max(start[0], end[0]) >= 8.0:
                        return 'deferred'
                    return False

                nav = TerrainNavigator(lambda *unused: 0.0, obstacle,
                                       baked_graph=_flat_open_graph())
                start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 20.0)
                request = ('route', 2, 'shared_leg')
                nav.grid.review_native_corridor(start, goal)
                nav.begin_frame(0.0)
                self.assertEqual(goal, nav.next_target(27, start, goal, request, 0.0))
                nav.end_frame()
                key = nav._cache_key(request, goal)
                path = nav.paths[key]
                state = nav.bot_states[27]
                current = (8.0, 0.0, 0.0)
                if source == 'active_join':
                    current = start
                    key = nav._cache_key(('join', 27, 'detour'), goal)
                    path = (start, (8.0, 0.0, 8.0), goal)
                    nav.paths[key] = path
                    nav.path_times[key] = 0.0
                    nav.path_hull_revisions[key] = nav.grid.static_hull_revision
                    state.update(path_key=key, index=1, last_target=path[1])
                old_index, old_target = state['index'], state['last_target']
                deferred[0] = True
                nav.grid.invalidate_native_review()
                nav.begin_frame(0.0)
                self.assertEqual(current, nav.next_target(
                    27, current, goal, request, 0.1))
                nav.end_frame()
                self.assertIs(path, nav.paths[key])
                self.assertEqual(old_index, state['index'])
                self.assertEqual(old_target, state['last_target'])
                self.assertFalse(nav.searches)
                self.assertEqual('pending', state['navigation_status'])
                deferred[0] = False
                nav.begin_frame(0.0)
                self.assertNotEqual(current, nav.next_target(
                    27, current, goal, request, 0.2))
                nav.end_frame()
                self.assertIs(path, nav.paths[key])
                self.assertFalse(nav.searches)


if __name__ == '__main__':
    unittest.main()
