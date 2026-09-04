from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import foliage


def _row(x, strength=0.15):
    # Unit horizontal OBB, y=-1..3, identity inverse transform.
    return [x, -1.0, 0.0, 3.0, 1.0, 0.0, 0.0, 1.0,
            strength, 1.0]


def _fallen_profile(standing_instance_id=None):
    return [7, 3, -2.0, 0.0, -1.0, 2.0, 10.0, 1.0,
            standing_instance_id]


def _fallen_pose_z(center=(0.0, 1.0, 0.0)):
    return center, (
        (2.0, 0.0, 0.0),
        (0.0, 0.0, 5.0),
        (0.0, 1.0, 0.0),
    )


def _fallen_pose_x(center=(5.0, 1.0, 0.0)):
    return center, (
        (0.0, 0.0, 2.0),
        (5.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )


class FoliageTests(unittest.TestCase):

    def test_segment_cells_supercovers_a_grid_corner(self):
        cells = set(foliage._segment_cells(
            (0.1, 0.0, 0.1), (7.9, 0.0, 7.9), 4.0))

        self.assertEqual({(0, 0), (1, 0), (0, 1), (1, 1)}, cells)

    def test_segment_cells_supercovers_grid_lines_and_negative_corners(self):
        grid_line = set(foliage._segment_cells(
            (0.1, 0.0, 0.0), (7.9, 0.0, 0.0), 4.0))
        negative_corner = set(foliage._segment_cells(
            (-7.9, 0.0, -7.9), (-0.1, 0.0, -0.1), 4.0))

        self.assertEqual({(0, 0), (1, 0), (0, -1), (1, -1)},
                         grid_line)
        self.assertEqual({
            (-2, -2), (-1, -2), (-2, -1), (-1, -1),
        }, negative_corner)

    def test_segment_cells_stops_at_boundary_endpoints_in_both_directions(self):
        forward = set(foliage._segment_cells(
            (4.0, 0.0, -4.0), (-4.0, 0.0, 4.0), 1.0))
        reverse = set(foliage._segment_cells(
            (-4.0, 0.0, 4.0), (4.0, 0.0, -4.0), 1.0))

        self.assertEqual(forward, reverse)
        self.assertIn((-4, 4), forward)
        self.assertIn((4, -4), forward)
        self.assertLessEqual(len(forward), 40)

    def test_static_foliage_uses_the_complete_vertical_slab_interval(self):
        row = [5.0, 4.0, 0.0, 4.5, 1.0, 0.0, 0.0, 1.0,
               0.15, 1.0]

        self.assertTrue(foliage._intersects(
            row, (0.0, 10.0, 0.0), (10.0, 0.0, 0.0)))

    def test_pair_specific_segment_intersects_or_misses_same_bush(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(5.0)], 'cells': {'0,0': [0]}})

        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(
                (0.0, 0.0, 5.0), (10.0, 0.0, 5.0)))

    def test_stacked_bushes_cap_at_sixty_percent(self):
        rows = [_row(x) for x in (2.0, 4.0, 6.0, 8.0, 10.0)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': list(range(len(rows)))}})

        self.assertEqual(
            0.60, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (12.0, 0.0, 0.0)))

    def test_static_and_fallen_foliage_share_the_sixty_percent_cap(self):
        rows = [_row(x) for x in (2.0, 4.0, 6.0, 8.0)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': list(range(len(rows)))},
            'fallen_trees': [_fallen_profile()],
        })
        self.assertFalse(foliage_map.activate_fallen_tree(8, 3))
        self.assertEqual(set(), foliage_map.activated_fallen_trees)
        self.assertTrue(foliage_map.activate_fallen_tree(7, 3))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_x((10.0, 1.0, 0.0))))

        self.assertEqual(0.60, foliage_map.camouflage_bonus(
            (0.0, 0.0, 0.0), (14.0, 0.0, 0.0)))

    def test_recent_shot_ignores_bush_near_target(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(8.0)], 'cells': {'0,0': [0]}})

        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), True))

    def test_fallen_tree_activates_and_follows_native_pose_once(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 4.0,
            'instances': [], 'cells': {},
            'fallen_trees': [_fallen_profile()],
        })

        observer = (0.0, 0.0, -6.0)
        target = (0.0, 0.0, 6.0)
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.activate_fallen_tree(7, 3))
        self.assertFalse(foliage_map.settle_fallen_tree(7, 3))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_z()))
        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(observer, target))
        self.assertFalse(foliage_map.activate_fallen_tree(7, 3))
        self.assertEqual(1, len(foliage_map.fallen_tree_instances))
        self.assertEqual(((7, 3),),
                         foliage_map.refreshing_fallen_tree_wires())
        self.assertTrue(foliage_map.settle_fallen_tree(7, 3))
        self.assertEqual((), foliage_map.refreshing_fallen_tree_wires())
        self.assertFalse(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_z((20.0, 1.0, 0.0))))

    def test_fallen_tree_pose_updates_cells_and_replaces_standing_volume(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 8.0,
            'instances': [_row(5.0)], 'cells': {'0,0': [0]},
            'fallen_trees': [_fallen_profile(0)],
        })
        observer = (0.0, 0.0, 0.0)
        target = (10.0, 0.0, 0.0)

        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.activate_fallen_tree(7, 3))
        self.assertNotIn(0, foliage_map.inactive_instances)
        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_x()))
        self.assertIn(0, foliage_map.inactive_instances)
        self.assertEqual(
            0.15, foliage_map.camouflage_bonus(observer, target))
        dynamic_id = foliage_map.fallen_tree_instances[(7, 3)]
        old_cells = set(foliage_map.fallen_tree_cells[(7, 3)])
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_x((45.0, 1.0, 0.0))))
        new_cells = set(foliage_map.fallen_tree_cells[(7, 3)])
        for cell in old_cells - new_cells:
            self.assertNotIn(dynamic_id, foliage_map.cells.get(cell, ()))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_x((45.0, 1.0, 0.0))))
        for cell in new_cells:
            self.assertEqual(
                1, foliage_map.cells[cell].count(dynamic_id))
        previous_row = foliage_map.instances[dynamic_id]
        previous_cells = dict(
            (cell, list(foliage_map.cells[cell])) for cell in new_cells)
        with self.assertRaisesRegex(ValueError, 'degenerate'):
            foliage_map.update_fallen_tree_pose(
                7, 3, (45.0, 1.0, 0.0), (
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0)))
        self.assertIs(previous_row, foliage_map.instances[dynamic_id])
        self.assertEqual(previous_cells, dict(
            (cell, list(foliage_map.cells[cell])) for cell in new_cells))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(observer, target))
        self.assertEqual(0.15, foliage_map.camouflage_bonus(
            (40.0, 0.0, 0.0), (50.0, 0.0, 0.0)))

    def test_sloped_fallen_tree_uses_full_3d_angle_not_vertical_prism(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 8.0,
            'instances': [], 'cells': {},
            'fallen_trees': [_fallen_profile()],
        })
        foliage_map.activate_fallen_tree(7, 3)
        foliage_map.update_fallen_tree_pose(
            7, 3, (5.0, 5.0, 0.0), (
                (0.0, 0.0, 1.0),
                (5.0, 5.0, 0.0),
                (-0.5, 0.5, 0.0),
            ))

        self.assertEqual(0.15, foliage_map.camouflage_bonus(
            (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)))
        self.assertEqual(0.0, foliage_map.camouflage_bonus(
            (8.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_recent_shot_ignores_activated_tree_near_target(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [], 'cells': {},
            'fallen_trees': [_fallen_profile()],
        })
        foliage_map.activate_fallen_tree(7, 3)
        foliage_map.update_fallen_tree_pose(7, 3, *_fallen_pose_z())

        self.assertEqual(0.0, foliage_map.camouflage_bonus(
            (0.0, 0.0, -6.0), (0.0, 0.0, 6.0), True))


if __name__ == '__main__':
    unittest.main()
