"""Vehicle-scoped native proofs on one shared grid and real A* jobs.

The finite wall and destructible doorway are controlled collision fixtures.
They prove cache and request ownership, not the stock native crush verdict.
"""
import unittest

from test_port_0922_navigation import TerrainNavigator
from test_port_0922_bot_runtime import _flat_open_graph


LOW = (('stock1513', 10000.0, 2.0), 10000.0, 2.0)
HIGH = (('stock1513', 30000.0, 10.0), 30000.0, 10.0)


class DoorScene:
    """A wall across the map with a crushable, off-axis door at z=4..20."""

    def __init__(self):
        self.calls = []
        self.defer_low = False

    @staticmethod
    def intersects(start, end, width, low_z, high_z):
        near, far = 0.0, 1.0
        for position, delta, low, high in (
                (start[0], end[0] - start[0], 4.0 - width, 4.0 + width),
                (start[2], end[2] - start[2], low_z - width, high_z + width)):
            if abs(delta) < 1e-9:
                if not low <= position <= high:
                    return False
            else:
                first, last = (low - position) / delta, (high - position) / delta
                near, far = max(near, min(first, last)), min(far, max(first, last))
                if near > far:
                    return False
        return True

    def blocked(self, start, end, width, capability=None, evidence=None):
        token = capability[0] if capability is not None else None
        reason = None
        result = False
        if (self.intersects(start, end, width, -26.0, 4.0) or
                self.intersects(start, end, width, 20.0, 26.0)):
            reason, result = 'solid_wall', True
        elif self.intersects(start, end, width, 4.0, 20.0):
            if token == LOW[0] and self.defer_low:
                reason, result = 'door_query_pending', 'deferred'
            elif capability is None or capability[2] < 5.0:
                reason, result = 'door_crush_capability', True
        if evidence is not None and reason:
            evidence.update(reason=reason, model='fixture/door' if 'door' in reason
                            else 'fixture/wall')
        self.calls.append((token, tuple(start), tuple(end), result, reason))
        return result

    def navigator(self, baked=True):
        if not baked:
            return TerrainNavigator(lambda *unused: 0.0, self.blocked,
                                    cell_size=4.0)
        graph = _flat_open_graph()
        width = 13
        directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
        graph.update(origin=(-24.0, -24.0), bounds=(-26.0, -26.0, 26.0, 26.0),
                     width=width, height=width,
                     heights_mm=[0] * (width * width),
                     hazards=[0] * (width * width),
                     links=[sum(1 << i for i, (dx, dz) in enumerate(directions)
                                if 0 <= x + dx < width and 0 <= z + dz < width)
                            for z in range(width) for x in range(width)])
        nav = TerrainNavigator(lambda *unused: 0.0, self.blocked,
                               baked_graph=graph)
        # Every potential wall crossing is in the real native review radius;
        # a baked edge outside review must not create an artificial bypass.
        nav.grid.review_native_corridor((-8.0, 0.0, 0.0), (20.0, 0.0, 0.0))
        return nav


class CapabilityNavigationTests(unittest.TestCase):
    start = (-8.0, 0.0, 0.0)
    goal = (20.0, 0.0, 0.0)

    def setUp(self):
        self.scene = DoorScene()
        self.nav = self.scene.navigator()

    def target(self, bot_id, request, capability, now=0.0):
        # Explicit zero credit exposes ownership while leaving real jobs
        # pending. Search advancement below uses the production step API.
        self.nav.begin_frame(0.0)
        try:
            return self.nav.next_target(bot_id, self.start, self.goal, request,
                                        now, native_capability=capability)
        finally:
            self.nav.end_frame()

    def key(self, request, capability):
        return self.nav._cache_key(
            self.nav._native_path_key(request, capability), self.goal)

    def complete_interleaved(self, jobs):
        for unused in range(20000):
            for job in jobs:
                if not job.done:
                    job.step(1)
            if all(job.done for job in jobs):
                return
        self.fail('bounded wall searches did not terminate')

    def test_interleaved_astar_proofs_are_scoped_and_same_capability_reuses_them(self):
        grid = self.nav.grid
        low = grid.begin_plan(self.start, self.goal, native_capability=LOW)
        high = grid.begin_plan(self.start, self.goal, native_capability=HIGH)
        self.complete_interleaved((low, high))
        self.assertEqual((), low.result)
        self.assertTrue(high.result)
        self.assertEqual(self.goal, high.result[-1])
        self.assertTrue(any(point[2] >= 8.0 for point in high.result))
        for start, end in zip(high.result, high.result[1:]):
            self.assertIs(False, self.scene.blocked(start, end, 2.15, HIGH))
        self.assertEqual(LOW[0], low.progress['native_refusal']['capability'])

        first = grid.cell_for((0.0, 0.0, 12.0))
        second = grid.cell_for((4.0, 0.0, 12.0))
        self.assertFalse(grid._native_edge_clear(first, second, LOW))
        self.assertTrue(grid._native_edge_clear(first, second, HIGH))
        count = len(self.scene.calls)
        proof = {}
        equivalent_low = (tuple(LOW[0]), LOW[1], LOW[2])
        self.assertFalse(grid._native_edge_clear(second, first, equivalent_low, proof))
        self.assertTrue(grid._native_edge_clear(second, first, HIGH))
        self.assertEqual(count, len(self.scene.calls))
        self.assertEqual('door_crush_capability', proof['reason'])
        self.assertEqual(LOW[0], proof['capability'])

    def test_shared_path_cache_separates_failure_success_and_refusal_evidence(self):
        request = ('route', 1, 'door')
        self.target(1, request, LOW)
        self.target(2, request, HIGH)
        low_key, high_key = self.key(request, LOW), self.key(request, HIGH)
        self.assertNotEqual(low_key, high_key)
        low, high = self.nav.searches[low_key], self.nav.searches[high_key]
        self.complete_interleaved((low, high))
        self.nav._finish_search(low_key, low, 0.5)
        self.nav._finish_search(high_key, high, 0.5)
        self.assertEqual((), self.nav.paths[low_key])
        self.assertTrue(self.nav.paths[high_key])
        self.assertEqual(LOW[0],
                         self.nav.path_native_refusals[low_key]['capability'])
        self.assertEqual(HIGH[0],
                         self.nav.path_native_refusals[high_key]['capability'])
        for capability, key in ((LOW, low_key), (HIGH, high_key)):
            returned_key, path = self.nav._path(request, self.start, self.goal,
                                                0.6, None, capability)
            self.assertEqual(key, returned_key)
            self.assertIs(self.nav.paths[key], path)
        self.assertFalse(self.nav.searches)

    def test_runtime_segment_reverse_and_edge_caches_keep_vehicle_scope(self):
        nav = self.scene.navigator(baked=False)
        grid = nav.grid
        start, end = (0.0, 0.0, 12.0), (4.0, 0.0, 12.0)
        for first, second in ((LOW, HIGH), (HIGH, LOW)):
            with self.subTest(first=first[0]):
                grid._segment_cache.clear()
                grid._edge_cache.clear()
                for cap in (first, second):
                    self.assertEqual(cap == HIGH, grid.segment_clear(start, end, cap))
                count = len(self.scene.calls)
                self.assertFalse(grid.segment_clear(end, start, LOW))
                self.assertTrue(grid.segment_clear(end, start, HIGH))
                self.assertEqual(count, len(self.scene.calls))
                first_cell, second_cell = grid.cell_for(start), grid.cell_for(end)
                self.assertIsNone(grid._edge(first_cell, 0.0, second_cell, LOW))
                self.assertEqual(0.0, grid._edge(first_cell, 0.0, second_cell, HIGH))
                count = len(self.scene.calls)
                self.assertIsNone(grid._edge(first_cell, 0.0, second_cell, LOW))
                self.assertEqual(0.0, grid._edge(first_cell, 0.0, second_cell, HIGH))
                self.assertEqual(count, len(self.scene.calls))

    def test_runtime_deferred_segment_and_edge_remain_unknown_for_that_capability(self):
        grid = self.scene.navigator(baked=False).grid
        start, end = (0.0, 0.0, 12.0), (4.0, 0.0, 12.0)
        first, second = grid.cell_for(start), grid.cell_for(end)
        self.scene.defer_low = True
        self.assertIsNone(grid.segment_clear(start, end, LOW))
        self.assertEqual('deferred', grid._edge(first, 0.0, second, LOW))
        self.assertTrue(grid.segment_clear(end, start, HIGH))
        self.assertEqual(0.0, grid._edge(first, 0.0, second, HIGH))
        self.assertEqual('deferred', grid._edge(first, 0.0, second, LOW))
        self.scene.defer_low = False
        self.assertFalse(grid.segment_clear(end, start, LOW))
        self.assertIsNone(grid._edge(first, 0.0, second, LOW))
        self.assertTrue(grid.segment_clear(start, end, HIGH))
        self.assertEqual(0.0, grid._edge(first, 0.0, second, HIGH))

    def test_deferred_low_search_does_not_poison_high_and_resumes_next_frame(self):
        self.scene.defer_low = True
        request = ('route', 1, 'deferred_door')
        self.target(1, request, LOW)
        self.target(2, request, HIGH)
        low_key, high_key = self.key(request, LOW), self.key(request, HIGH)
        low = self.nav.searches[low_key]
        for frame in range(1, 101):
            self.nav.begin_frame(0.1)
            self.nav.tick(frame * 0.1)
            self.nav.end_frame()
            if low.progress.get('deferred') and high_key in self.nav.paths:
                break
        self.assertTrue(low.progress.get('deferred'))
        self.assertIs(low, self.nav.searches[low_key])
        self.assertNotIn(low_key, self.nav.paths)
        self.assertTrue(self.nav.paths[high_key])
        proof = low.progress['native_refusal']
        self.assertEqual('door_query_pending', proof['reason'])
        edge = tuple(sorted((self.nav.grid.cell_for(proof['start']),
                             self.nav.grid.cell_for(proof['end']))))
        self.assertNotIn(self.nav.grid._native_cache_key(edge, LOW),
                         self.nav.grid._native_review_edges)
        self.scene.defer_low = False
        for next_frame in range(frame + 1, frame + 101):
            self.nav.begin_frame(0.1)
            self.nav.tick(next_frame * 0.1)
            self.nav.end_frame()
            if low_key in self.nav.paths:
                break
        self.assertEqual((), self.nav.paths[low_key])
        self.assertTrue(self.nav.paths[high_key])
        self.assertNotIn(low_key, self.nav.searches)

    def test_capability_change_retires_private_search_and_prefix(self):
        request = ('route_join', 7, 'door')
        self.target(7, request, LOW)
        old_key = self.key(request, LOW)
        old_search = self.nav.searches[old_key]
        for unused in range(1000):
            old_search.step(1)
            if len(old_search.proved_prefix(self.nav.grid)) >= 3:
                break
        self.assertFalse(old_search.done)
        self.target(7, request, LOW, 0.1)
        state = self.nav.bot_states[7]
        self.assertIs(old_search, state['pending_prefix_search'])
        self.target(7, request, LOW, 0.15)
        self.assertIs(state, self.nav.bot_states[7])
        self.assertIs(old_search, state['pending_prefix_search'])
        self.nav.bot_failed_edges[7] = {((1, 1), (2, 1)): (100.0, 240.0)}
        self.target(7, request, HIGH, 0.2)
        self.assertNotIn(old_key, self.nav.searches)
        self.assertNotIn(old_key, self.nav.search_times)
        self.assertIsNot(old_search, state.get('pending_prefix_search'))
        state = self.nav.bot_states[7]
        self.assertEqual(HIGH, state['native_capability'])
        self.assertEqual(self.key(request, HIGH), state['request_key'])
        self.assertNotIn(7, self.nav.bot_failed_edges)

    def test_capability_change_preserves_shared_search_until_last_consumer_leaves(self):
        request = ('route', 1, 'shared_door')
        self.target(7, request, LOW)
        self.target(8, request, LOW)
        old_key = self.key(request, LOW)
        old_search = self.nav.searches[old_key]
        self.target(7, request, HIGH, 0.1)
        self.assertIs(old_search, self.nav.searches[old_key])
        self.assertEqual(old_key, self.nav.bot_states[8]['request_key'])
        new_key = self.key(request, HIGH)
        new_search = self.nav.searches[new_key]
        self.target(8, request, HIGH, 0.2)
        self.assertNotIn(old_key, self.nav.searches)
        self.assertNotIn(old_key, self.nav.search_times)
        self.assertIs(new_search, self.nav.searches[new_key])
        self.assertEqual(new_key, self.nav.bot_states[8]['request_key'])

    def test_new_capability_direct_door_retires_pending_jobs_by_consumer(self):
        for kind in ('route_join', 'route'):
            with self.subTest(kind=kind):
                self.setUp()
                request = (kind, 7, 'direct_door')
                self.target(7, request, LOW)
                if kind == 'route':
                    self.target(8, request, LOW)
                old_key = self.key(request, LOW)
                old_search = self.nav.searches[old_key]
                current, goal = (0.0, 0.0, 12.0), (8.0, 0.0, 12.0)
                self.assertFalse(self.nav.grid.dry_segment_clear(current, goal, 0.1, LOW))
                self.assertTrue(self.nav.grid.dry_segment_clear(current, goal, 0.1, HIGH))
                # This is the runtime's direct-edge branch: next_target is
                # bypassed once the new descriptor can crush the door.
                self.nav.observe_direct_target(7, current, goal, request, 0.1,
                                               native_capability=HIGH)
                self.assertNotIn(7, self.nav.bot_states)
                if kind == 'route':
                    self.assertIs(old_search, self.nav.searches[old_key])
                    self.nav.observe_direct_target(8, current, goal, request, 0.2,
                                                   native_capability=HIGH)
                self.assertNotIn(old_key, self.nav.searches)
                self.assertNotIn(old_key, self.nav.search_times)


if __name__ == '__main__':
    unittest.main()
