import json
import math
import sys
import unittest
from pathlib import Path


PORT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid, TerrainNavigator
from gui.mods.offline_lan_0922.ai.driver import LocalDriver


class ClimbApproachNavigationTests(unittest.TestCase):
    @staticmethod
    def _fjord_route():
        graph = json.loads((PORT_ROOT / 'navgraphs' / '33_fjord.json').read_text())
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        grid = navigator.grid
        route = next(route for route in graph['routes']['1']
                     if route['id'] == 'north_ridge')
        endpoints = []
        for raw in route['waypoints'][:2]:
            cell = grid.cell_for((raw[0], 0.0, raw[1]))
            endpoints.append(grid.point_for(cell, grid._baked_cell_height(cell)))
        start, goal = endpoints
        path = grid.plan(start, goal, prefer_clearance=True)
        pivot = next(index for index, point in enumerate(path)
                     if point[0] == 366.0 and point[2] == 14.0)
        key = ('route', 1, 'north_ridge', 1)
        cache_key = navigator._cache_key(key, goal)
        navigator.paths[cache_key] = path
        navigator.path_times[cache_key] = 0.0
        return graph, navigator, path, pivot, key

    @staticmethod
    def _open_grid():
        grid = TerrainGrid(lambda x, z, hint: 0.0, cell_size=10.0)
        grid.segment_clear = lambda start, end: True
        grid.segment_penalty = lambda start, end, now: 0.0
        grid.segment_has_baked_hazard = lambda start, end, mask: False
        return grid

    def test_smoothing_keeps_turning_setup_point_before_steep_climb(self):
        grid = self._open_grid()
        path = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 3.0, 10.0),
            (20.0, 3.0, 10.0),
        )

        self.assertEqual((path[0], path[1], path[3]), grid._smooth(path))

    def test_flat_corner_and_straight_climb_smoothing_are_unchanged(self):
        grid = self._open_grid()
        flat = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
        )
        straight_climb = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (0.0, 3.0, 20.0),
        )

        self.assertEqual((flat[0], flat[-1]), grid._smooth(flat))
        self.assertEqual(
            (straight_climb[0], straight_climb[-1]),
            grid._smooth(straight_climb),
        )

    def test_degenerate_segments_do_not_block_shortcuts(self):
        duplicate_incoming = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 3.0, 10.0),
        )
        duplicate_outgoing = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 10.0),
        )

        self.assertTrue(TerrainGrid.shortcut_preserves_climb_approach(
            duplicate_incoming, 0, 2))
        self.assertTrue(TerrainGrid.shortcut_preserves_climb_approach(
            duplicate_outgoing, 0, 2))

    def test_lookahead_does_not_skip_turning_setup_point(self):
        navigator = TerrainNavigator(lambda x, z, hint: 0.0)
        navigator.grid.segment_clear = lambda start, end: True
        navigator.grid.segment_penalty = lambda start, end, now: 0.0
        path = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 3.0, 10.0),
            (20.0, 3.0, 10.0),
        )
        navigator._path = lambda key, start, goal, now, avoid: (key, path)

        selected = navigator.next_target(
            7, (0.0, 0.0, -11.0), path[-1], ('slope',), 1.0)

        self.assertEqual(path[1], selected)

    def test_flat_lookahead_keeps_original_bounded_target(self):
        navigator = TerrainNavigator(lambda x, z, hint: 0.0)
        navigator.grid.segment_clear = lambda start, end: True
        navigator.grid.segment_penalty = lambda start, end, now: 0.0
        path = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (20.0, 0.0, 10.0),
        )
        navigator._path = lambda key, start, goal, now, avoid: (key, path)

        selected = navigator.next_target(
            8, (0.0, 0.0, -11.0), path[-1], ('flat',), 1.0)

        self.assertEqual(path[2], selected)

    def test_speed_horizon_advances_farther_along_a_proved_flat_corridor(self):
        navigator = TerrainNavigator(
            lambda x, z, hint: 0.0, cell_size=4.0)
        navigator.grid.segment_clear = lambda start, end: True
        navigator.grid.segment_penalty = lambda start, end, now: 0.0
        path = tuple((0.0, 0.0, float(index * 4)) for index in range(8))
        navigator._path = lambda key, start, goal, now, avoid: (key, path)

        selected = navigator.next_target(
            81, (0.0, 0.0, -1.0), path[-1], ('flat-speed',), 1.0,
            lookahead_distance=18.0)

        self.assertEqual(path[4], selected)

    def test_near_waypoint_advance_still_keeps_climb_setup_point(self):
        navigator = TerrainNavigator(lambda x, z, hint: 0.0)
        navigator.grid.segment_clear = lambda start, end: True
        navigator.grid.segment_penalty = lambda start, end, now: 0.0
        path = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 3.0, 10.0),
            (20.0, 3.0, 10.0),
        )
        navigator._path = lambda key, start, goal, now, avoid: (key, path)

        selected = navigator.next_target(
            9, (0.0, 0.0, 1.0), path[-1], ('near-slope',), 1.0)

        self.assertEqual(path[1], selected)

    def test_continuation_lookahead_keeps_climb_setup_point(self):
        navigator = TerrainNavigator(lambda x, z, hint: 0.0)
        navigator.grid.segment_clear = lambda start, end: True
        navigator.grid.segment_penalty = lambda start, end, now: 0.0
        current = (0.0, 0.0, 0.0)
        goal = (30.0, 3.0, 10.0)
        initial = (current, (0.0, 0.0, 1.0))
        continued = (
            current,
            (0.0, 0.0, 10.0),
            (10.0, 3.0, 10.0),
            (20.0, 3.0, 10.0),
        )

        def path_for(key, start, requested_goal, now, avoid):
            if key and key[0] == 'continue':
                return key, continued
            return key, initial

        navigator._path = path_for

        selected = navigator.next_target(
            10, current, goal, ('continued-slope',), 1.0)

        self.assertEqual(continued[1], selected)

    def test_fjord_reached_climb_setup_advances_without_target_reversal(self):
        from gui.mods.offline_lan_0922.bot_runtime import BotRuntime

        graph, navigator, path, pivot, unused_key = self._fjord_route()
        runtime = BotRuntime(1)
        runtime.navigator = navigator
        runtime.baked_graph = graph
        runtime.states = {7: {'id': 7, 'team': 1}}
        strategic = {
            'combat_mode': 'route', 'route_id': 'north_ridge',
            'route_index': 1, 'route_anchor': path[0],
        }
        # A real baked bend climbs 1.256 m over 8.944 m. At 1.4 m from
        # its setup, the old navigator issued a 3.12 m safe-local step,
        # then targeted the setup behind the hull on the next decision.
        for now, z in ((1.0, 12.6), (1.1, 15.72)):
            current = (366.0, path[pivot][1], z)
            self.assertGreater(math.hypot(
                path[-1][0] - current[0], path[-1][2] - current[2]), 15.0)
            selected = runtime._navigation_target(
                7, current, path[-1], strategic, {'now': now, 'speed': 0.0})

            self.assertEqual(path[pivot + 1], selected)
            self.assertEqual(pivot + 1, navigator.bot_states[7]['index'])
            self.assertNotEqual('safe_local', navigator.fallback_modes.get(7))
            self.assertGreater(selected[2], current[2])
            driver = LocalDriver()
            aligning = driver.drive(
                7, 0, current, 0.0, 0.0, 0.1,
                selected, [], lambda yaw: True)
            aligned = driver.drive(
                7, 0, current, aligning['target_yaw'], 0.0, 0.1,
                selected, [], lambda yaw: True)
            self.assertEqual(0.0, aligning['throttle'])
            self.assertGreater(aligned['throttle'], 0.0)

    def test_fjord_unreached_climb_setup_is_not_skipped_within_grid_radius(self):
        unused_graph, navigator, path, pivot, key = self._fjord_route()
        # Inside the grid's 2.2 m advancement radius but outside the driver's
        # 1.5 m arrival radius: the uphill setup has not been reached yet.
        current = (366.0, path[pivot][1], 12.4)

        selected = navigator.next_target(7, current, path[-1], key, 1.0)

        self.assertEqual(path[pivot], selected)
        self.assertEqual(pivot, navigator.bot_states[7]['index'])

    def test_reached_climb_setup_keeps_terrain_and_penalty_checks(self):
        for blocker in ('collision', 'penalty'):
            with self.subTest(blocker=blocker):
                unused_graph, navigator, path, pivot, key = self._fjord_route()
                current = (366.0, path[pivot][1], 12.6)
                next_point = path[pivot + 1]
                if blocker == 'collision':
                    original = navigator.grid.segment_clear
                    navigator.grid.segment_clear = (
                        lambda start, end: end != next_point and
                        original(start, end))
                else:
                    navigator.grid.segment_penalty = (
                        lambda start, end, now: 240.0
                        if start == current and end == next_point else 0.0)

                selected = navigator.next_target(7, current, path[-1], key, 1.1)

                self.assertNotEqual(next_point, selected)
                self.assertNotEqual(pivot + 1, navigator.bot_states[7]['index'])

    def test_selected_uphill_ford_survives_passing_its_consumed_setup(self):
        graph, navigator, path, pivot, key = self._fjord_route()
        # A shallow variant of the real bend exercises the same grade and
        # link checks, with the next A* edge explicitly chosen as the ford.
        next_point = path[pivot + 1]
        cell = navigator.grid.cell_for(next_point)
        graph['hazards'][cell[1] * graph['width'] + cell[0]] = 4
        navigator.grid._install_baked_graph(graph)
        selected = navigator.next_target(7, path[pivot], path[-1], key, 1.0)
        self.assertEqual(next_point, selected)
        self.assertEqual(next_point,
                         navigator.bot_states[7]['controlled_shallow_target'])
        current = (366.0, path[pivot][1], 16.5)

        selected = navigator.next_target(7, current, path[-1], key, 1.1)

        self.assertEqual(next_point, selected)
        self.assertEqual(pivot + 1, navigator.bot_states[7]['index'])
        self.assertEqual(next_point,
                         navigator.bot_states[7]['controlled_shallow_target'])

    def test_reached_climb_setup_cannot_admit_an_unplanned_shallow_offset(self):
        navigator = TerrainNavigator(lambda *unused: 0.0, cell_size=4.0)
        current = (0.0, 0.0, 0.0)
        path = ((1.0, 0.0, 0.0), (8.0, 2.0, 8.0), (8.0, 2.0, 40.0))
        navigator.grid.segment_clear = lambda *unused: True
        # The live offset enters water, but the path's planned uphill edge
        # stays dry. Reaching the setup must not grant permission for it.
        navigator.grid.segment_has_baked_hazard = (
            lambda start, end, mask: end == path[1] and start != path[0])
        navigator._path = lambda key, *unused: (key, path)

        selected = navigator.next_target(
            7, current, path[-1], ('unplanned-water',), 1.0)

        self.assertNotEqual(path[1], selected)
        self.assertEqual(0, navigator.bot_states[7]['index'])
        self.assertNotIn('controlled_shallow_target', navigator.bot_states[7])

    def test_new_route_does_not_inherit_an_old_fords_consumed_approach(self):
        graph, navigator, path, pivot, key = self._fjord_route()
        next_point = path[pivot + 1]
        cell = navigator.grid.cell_for(next_point)
        graph['hazards'][cell[1] * graph['width'] + cell[0]] = 4
        navigator.grid._install_baked_graph(graph)
        self.assertEqual(next_point, navigator.next_target(
            7, path[pivot], path[-1], key, 1.0))

        # The new route shares the ford destination but has a different
        # approach. Its nearest node must not borrow the old route's arrival.
        replacement = (path[pivot - 1], next_point, path[-1])
        replacement_key = ('route', 1, 'different-approach', 1)
        cache_key = navigator._cache_key(replacement_key, path[-1])
        navigator.paths[cache_key] = replacement
        navigator.path_times[cache_key] = 1.0
        current = (366.0, path[pivot][1], 16.5)
        self.assertFalse(navigator.grid.live_shortcut_preserves_climb_approach(
            current, replacement, 0, 1))

        selected = navigator.next_target(
            7, current, path[-1], replacement_key, 1.1)

        self.assertNotEqual(next_point, selected)
        self.assertNotEqual(cache_key, navigator.bot_states[7]['path_key'])

    def test_near_path_node_cannot_report_arrival_while_goal_is_far(self):
        current = (0.0, 0.0, 0.0)
        goal = (0.0, 0.0, 60.0)
        fallback = (5.0, 0.0, 0.0)

        for distance in (0.6, 1.2):
            with self.subTest(distance=distance):
                navigator = TerrainNavigator(lambda x, z, hint: 0.0)
                navigator.grid.segment_clear = (
                    lambda start, end: end != (0.0, 3.0, 10.0))
                navigator.grid.segment_penalty = (
                    lambda start, end, now: 0.0)
                navigator.grid.safe_local_target = (
                    lambda *unused, **unused_kwargs: fallback)
                path = (
                    (0.0, 0.0, distance),
                    (0.0, 3.0, 10.0),
                )
                navigator._path = (
                    lambda key, start, requested, now, avoid: (key, path))

                selected = navigator.next_target(
                    40, current, goal, ('parked-node',), 1.0)

                self.assertEqual(fallback, selected)
                self.assertTrue(TerrainNavigator.navigation_paused(
                    current, goal, path[0]))

        self.assertFalse(TerrainNavigator.navigation_paused(
            current, goal, (0.0, 0.0, 1.6)))


if __name__ == '__main__':
    unittest.main()
