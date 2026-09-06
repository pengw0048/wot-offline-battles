import sys
import unittest
from pathlib import Path


PORT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai.navigation import (
    BAKED_SHALLOW_WATER, MAX_BAKED_CORRIDOR_CACHE,
    TerrainGrid, TerrainNavigator,
)


DIRECTIONS = (
    (-1, -1), (0, -1), (1, -1), (-1, 0),
    (1, 0), (-1, 1), (0, 1), (1, 1),
)


def _graph(width, height, open_cells):
    open_cells = set(open_cells)
    heights = []
    links = []
    for z in range(height):
        for x in range(width):
            cell = (x, z)
            heights.append(0 if cell in open_cells else None)
            mask = 0
            if cell in open_cells:
                for index, (dx, dz) in enumerate(DIRECTIONS):
                    neighbour = (x + dx, z + dz)
                    if neighbour not in open_cells:
                        continue
                    if dx and dz:
                        if ((x + dx, z) not in open_cells or
                                (x, z + dz) not in open_cells):
                            continue
                    mask |= 1 << index
            links.append(mask)
    return {
        'format': 'offline-lan-0922-navgraph',
        'version': 2,
        'bounds': (0.0, 0.0, float((width - 1) * 4),
                   float((height - 1) * 4)),
        'origin': (0.0, 0.0),
        'cell_size': 4.0,
        'width': width,
        'height': height,
        'heights_mm': tuple(heights),
        'links': tuple(links),
        'hazards': (0,) * (width * height),
        'bake': {'max_grade': 0.38},
    }


class BakedClearanceNavigationTests(unittest.TestCase):
    def test_baked_corridor_reuse_keeps_world_bounds_and_live_penalties(self):
        graph = _graph(4, 1, ((x, 0) for x in range(4)))
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)
        scans = []
        original = grid._baked_segment_cells

        def scan(start, end):
            scans.append((start, end))
            return original(start, end)

        grid._baked_segment_cells = scan
        start, end = (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)
        self.assertTrue(grid.dry_segment_clear(start, end, 1.0))
        self.assertTrue(grid.dry_segment_clear(
            (0.2, 4.0, 0.0), (11.9, 6.0, 0.0), 1.1))
        self.assertEqual(1, len(scans))
        # This point still rounds into the same end cell but is outside the
        # authored world rectangle, so a cached clear corridor cannot admit it.
        self.assertFalse(grid.segment_clear(start, (12.1, 0.0, 0.0)))
        grid._failed_edges[((0, 0), (1, 0))] = (3.0, 100.0)
        self.assertFalse(grid.dry_segment_clear(start, end, 2.0))
        self.assertTrue(grid.dry_segment_clear(start, end, 3.0))
        self.assertEqual(1, len(scans))

    def test_baked_corridor_reuse_keeps_directional_links_and_ford_exit(self):
        graph = _graph(4, 1, ((x, 0) for x in range(4)))
        graph['hazards'] = (BAKED_SHALLOW_WATER, 0, 0, 0)
        links = list(graph['links'])
        links[3] = 0
        graph['links'] = tuple(links)
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)
        start, end = (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)
        for unused in range(2):
            self.assertTrue(grid.dry_segment_clear(start, end, 1.0))
            self.assertFalse(grid.segment_clear(end, start))
            self.assertTrue(grid.segment_has_baked_hazard(
                end, start, BAKED_SHALLOW_WATER))
            self.assertFalse(grid.segment_has_baked_hazard(
                start, end, BAKED_SHALLOW_WATER))
        # Installing another map must not inherit the old map's clear result.
        replacement = _graph(4, 1, ((0, 0), (3, 0)))
        grid._install_baked_graph(replacement)
        self.assertFalse(grid.segment_clear(start, end))
        self.assertTrue(grid.segment_has_baked_hazard(start, end, 0))

    def test_baked_corridor_cache_stays_bounded_as_routes_change(self):
        graph = _graph(48, 48, (
            (x, z) for z in range(48) for x in range(48)))
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)
        start = (0.0, 0.0, 0.0)
        for z in range(48):
            for x in range(48):
                end = (x * 4.0, 0.0, z * 4.0)
                self.assertTrue(grid.dry_segment_clear(start, end, 1.0))
        self.assertEqual(MAX_BAKED_CORRIDOR_CACHE,
                         len(grid._baked_corridor_cache))
        self.assertEqual(MAX_BAKED_CORRIDOR_CACHE,
                         len(grid._baked_corridor_order))
        self.assertTrue(grid.dry_segment_clear(start, (4.0, 0.0, 0.0), 2.0))
        self.assertEqual(MAX_BAKED_CORRIDOR_CACHE,
                         len(grid._baked_corridor_cache))

    def test_wide_corridor_path_moves_off_the_exposed_edge(self):
        graph = _graph(21, 5, (
            (x, z) for z in range(5) for x in range(21)))
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)

        path = grid.plan((0.0, 0.0, 0.0), (80.0, 0.0, 0.0),
                         max_expansions=4096)

        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 0.0)
        self.assertTrue(grid.segment_clear(path[0], path[1]))
        self.assertTrue(grid.segment_clear(path[-2], path[-1]))

    def test_one_cell_corridor_does_not_invent_a_centre_detour(self):
        graph = _graph(21, 5, ((x, 2) for x in range(21)))
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)

        path = grid.plan((0.0, 0.0, 8.0), (80.0, 0.0, 8.0),
                         max_expansions=4096)

        self.assertTrue(path)
        self.assertEqual({8.0}, set(point[2] for point in path))
        for first, second in zip(path, path[1:]):
            self.assertTrue(grid.segment_clear(first, second))

    def test_clearance_smoothing_is_opt_in_for_shared_routes(self):
        graph = _graph(21, 5, (
            (x, z) for z in range(5) for x in range(21)))
        grid = TerrainGrid(lambda *unused: 0.0, baked_graph=graph)
        centred = (
            (0.0, 0.0, 0.0),
            (8.0, 0.0, 8.0),
            (72.0, 0.0, 8.0),
            (80.0, 0.0, 0.0),
        )

        self.assertFalse(grid.shortcut_preserves_baked_clearance(
            centred, 0, len(centred) - 1))
        self.assertEqual(
            (centred[0], centred[-1]),
            grid._smooth(centred, prefer_clearance=False))
        self.assertGreater(
            len(grid._smooth(centred, prefer_clearance=True)), 2)

    def test_only_shared_strategic_routes_request_clearance_bias(self):
        self.assertTrue(TerrainNavigator._prefers_baked_clearance(
            ('route', 1, 'lake_road', 7)))
        self.assertTrue(TerrainNavigator._prefers_baked_clearance(
            ('continue', 4, (7, 8), 'route', 1, 'lake_road', 7)))
        for key in (
                ('route_join', 4, 1, 'lake_road', 1),
                ('continue', 4, (7, 8), 'route_join', 4, 1),
                ('local', 4, 'engage', 12),
                ('join', 4, (7, 8), 'route', 1, 'lake_road', 7)):
            self.assertFalse(
                TerrainNavigator._prefers_baked_clearance(key), key)


if __name__ == '__main__':
    unittest.main()
