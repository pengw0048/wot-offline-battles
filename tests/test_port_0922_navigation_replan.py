"""A completed local escape retires only its consumer's old route work."""
import unittest

from test_port_0922_navigation import TerrainNavigator


class NavigationReplanTests(unittest.TestCase):
    def test_local_escape_restarts_from_real_pose_without_erasing_physical_evidence(self):
        nav = TerrainNavigator(lambda *unused: 0.0,
                               lambda *unused: True, cell_size=4.0)
        start, goal = (0.0, 0.0, 0.0), (0.0, 0.0, 40.0)
        request = ('route', 1, 'shared')
        nav.begin_frame(0.0)
        nav.next_target(7, start, goal, request, 0.0)
        nav.next_target(8, start, goal, request, 0.0)
        shared_key = nav._cache_key(request, goal)
        shared_search = nav.searches[shared_key]
        other_state = dict(nav.bot_states[8])
        own_key, unused = nav._path(('join', 7, (0, 0)), start, goal, 0.0, None)
        other_key, unused = nav._path(('join', 8, (0, 0)), start, goal, 0.0, None)
        other_search = nav.searches[other_key]
        cache_key = nav._cache_key(('local', 7, 'old'), goal)
        nav.paths[cache_key] = (start, goal)
        nav.path_times[cache_key] = 0.0
        nav.path_hull_revisions[cache_key] = 0
        nav.path_native_refusals[cache_key] = {'reason': 'old private path'}
        edge = ((0, 0), (0, 1))
        failed = {edge: (50.0, 240.0)}
        nav.bot_failed_edges[7] = failed
        nav.grid._failed_edges[edge] = (50.0, 240.0)
        nav.bot_states[7].update(
            pending_prefix=((0.0, 0.0, 0.0), (0.0, 0.0, 4.0)),
            pending_prefix_search=nav.searches[own_key],
            pending_prefix_target=(0.0, 0.0, 4.0),
            last_target=(0.0, 0.0, 4.0),
            local_fallback_target=(0.0, 0.0, 4.0),
            temporary_stalled=True,
            temporary_visited_cells={(0, 0)})
        current = (0.0, 0.0, -8.0)
        self.assertTrue(nav.request_replan(7, current, 2.0))
        self.assertNotIn(own_key, nav.searches)
        self.assertNotIn(cache_key, nav.paths)
        self.assertNotIn(cache_key, nav.path_native_refusals)
        self.assertIs(shared_search, nav.searches[shared_key])
        self.assertIs(other_search, nav.searches[other_key])
        self.assertEqual(other_state, nav.bot_states[8])
        self.assertIs(failed, nav.bot_failed_edges[7])
        self.assertEqual((50.0, 240.0), nav.grid._failed_edges[edge])
        state = nav.bot_states[7]
        self.assertNotIn('last_target', state)
        self.assertNotIn('pending_prefix', state)
        self.assertNotIn('temporary_stalled', state)
        self.assertTrue(state['replan_active'])
        nav.next_target(7, current, goal, request, 2.0)
        replacements = [job for key, job in nav.searches.items()
                        if key[0][:2] == ('recovery', 7)]
        self.assertEqual(1, len(replacements))
        replacements[0].step(1)
        self.assertEqual(current, replacements[0].progress['start'])
        self.assertIs(failed, nav.bot_failed_edges[7])
        nav.end_frame()

    def test_unknown_consumer_does_not_change_shared_work(self):
        nav = TerrainNavigator(lambda *unused: 0.0)
        self.assertFalse(nav.request_replan(99, (0.0, 0.0, 0.0), 1.0))
        self.assertFalse(nav.bot_states)


if __name__ == '__main__':
    unittest.main()
