import sys
import unittest
from pathlib import Path


PORT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid, TerrainNavigator


class ClimbApproachNavigationTests(unittest.TestCase):
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

    def test_near_path_node_cannot_report_arrival_while_goal_is_far(self):
        current = (0.0, 0.0, 0.0)
        goal = (0.0, 0.0, 60.0)
        fallback = (5.0, 0.0, 0.0)

        for distance in (0.6, 1.2):
            with self.subTest(distance=distance):
                navigator = TerrainNavigator(lambda x, z, hint: 0.0)
                navigator.grid.segment_clear = lambda start, end: True
                navigator.grid.segment_penalty = (
                    lambda start, end, now: 0.0)
                navigator.grid.live_shortcut_preserves_climb_approach = (
                    lambda *unused: False)
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
