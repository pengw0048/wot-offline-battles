from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import foliage


BUSH = foliage.FOLIAGE_CAMOUFLAGE_PER_VOLUME
# Every observer below stands well outside the 15 m transparency radius, the
# distance at which the published rule makes cover see-through for the tank
# next to it.
FAR_OBSERVER = (-30.0, 0.0, 0.0)


def _row(x, strength=BUSH):
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

    def test_transparency_measures_the_nearest_sheared_volume_edge(self):
        # Corners are (-2,-1), (0,-1), (2,1), (0,1). The nearest point
        # to (16.5,0) is (2,1), not the inverse-coordinate clamp at (1,0).
        row = [0.0, -1.0, 0.0, 3.0, 1.0, -1.0, 0.0, 1.0,
               BUSH, 5.0 ** 0.5]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 64.0,
            'instances': [row], 'cells': {'0,0': [0], '-1,0': [0]}})

        self.assertAlmostEqual((14.5 ** 2 + 1.0) ** 0.5,
                               foliage._horizontal_distance(row, (16.5, 0, 0)))
        self.assertEqual(0.0, foliage_map.camouflage_bonus(
            (16.5, 0.0, 0.0), FAR_OBSERVER))
        self.assertEqual(BUSH, foliage_map.camouflage_bonus(
            (17.0, 0.0, 0.0), FAR_OBSERVER))

    def test_transparency_radius_bounds_the_baked_ensk_volume(self):
        # Exact #1513 06_ensk instance 580. Its baked parallelogram extends
        # beyond the original 8-vertex radius stored in the final column.
        row = [93.2151, 0.2816, 318.4103, 12.5746,
               -0.0835, 0.2205, -0.1622, -0.0329, BUSH, 6.8914]
        observer = (112.07278061544648, 4.4281, 329.7411376480212)
        self.assertLess(foliage._horizontal_distance(row, observer), 15.0)
        self.assertTrue(foliage._within_transparency_radius(row, observer))

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
               BUSH, 1.0]

        self.assertTrue(foliage._intersects(
            row, (0.0, 10.0, 0.0), (10.0, 0.0, 0.0)))

    def test_pair_specific_segment_intersects_or_misses_same_bush(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(5.0)], 'cells': {'0,0': [0]}})

        self.assertEqual(
            BUSH, foliage_map.camouflage_bonus(
                FAR_OBSERVER, (10.0, 0.0, 0.0)))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(
                (-30.0, 0.0, 5.0), (10.0, 0.0, 5.0)))

    def test_stacked_bushes_keep_the_existing_vegetation_limit(self):
        rows = [_row(x) for x in (2.0, 4.0, 6.0, 8.0, 10.0)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': list(range(len(rows)))}})

        self.assertEqual(
            foliage.FOLIAGE_CAMOUFLAGE_LIMIT,
            foliage_map.camouflage_bonus(
                FAR_OBSERVER, (12.0, 0.0, 0.0)))

    def test_static_and_fallen_foliage_share_the_vegetation_cap(self):
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

        self.assertEqual(
            foliage.FOLIAGE_CAMOUFLAGE_LIMIT,
            foliage_map.camouflage_bonus(
                FAR_OBSERVER, (14.0, 0.0, 0.0)))

    def test_recent_shot_removes_the_cover_at_the_target(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(8.0)], 'cells': {'0,0': [0]}})

        self.assertAlmostEqual(
            0.0,
            foliage_map.camouflage_bonus(
                FAR_OBSERVER, (10.0, 0.0, 0.0), True))

    def test_recent_shot_removes_every_nearby_cover_volume(self):
        rows = [_row(6.0, 0.25), _row(8.0), _row(9.0, 0.25)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': [0, 1, 2]}})

        self.assertAlmostEqual(
            0.0,
            foliage_map.camouflage_bonus(
                FAR_OBSERVER, (10.0, 0.0, 0.0), True))

    def test_recent_shot_leaves_cover_away_from_the_target_alone(self):
        # x=-10 is 20 m from the target and keeps its whole bonus; x=8 is two
        # metres away and remains fully transparent after firing.
        rows = [_row(-10.0), _row(8.0)]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': rows, 'cells': {'0,0': [0, 1], '-1,0': [0]}})

        self.assertAlmostEqual(
            BUSH,
            foliage_map.camouflage_bonus(
                FAR_OBSERVER, (10.0, 0.0, 0.0), True))

    def test_cover_inside_the_observer_radius_is_transparent_to_it(self):
        # The row spans x-1..x+1, so an observer at the origin is 13 m from
        # the near face of the first and 15.5 m from the second.
        near = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(14.0)], 'cells': {'0,0': [0]}})
        far = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [_row(16.5)], 'cells': {'0,0': [0]}})

        self.assertEqual(0.0, near.camouflage_bonus(
            (0.0, 0.0, 0.0), (40.0, 0.0, 0.0)))
        self.assertEqual(BUSH, far.camouflage_bonus(
            (0.0, 0.0, 0.0), (40.0, 0.0, 0.0)))

    def test_the_observer_radius_measures_the_volume_not_its_centre(self):
        # A ten metre wide cluster twelve metres away: its centre is outside
        # the radius and its near face is well inside it.
        wide = [0.0, -1.0, 0.0, 3.0, 0.2, 0.0, 0.0, 1.0, BUSH, 5.1]
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [wide], 'cells': {'0,0': [0]}})

        self.assertEqual(0.0, foliage_map.camouflage_bonus(
            (-17.0, 0.0, 0.0), (40.0, 0.0, 0.0)))
        self.assertEqual(BUSH, foliage_map.camouflage_bonus(
            (-25.0, 0.0, 0.0), (40.0, 0.0, 0.0)))

    def test_fallen_tree_activates_and_follows_native_pose_once(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 4.0,
            'instances': [], 'cells': {},
            'fallen_trees': [_fallen_profile()],
        })

        observer = (0.0, 0.0, -30.0)
        target = (0.0, 0.0, 25.0)
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.activate_fallen_tree(7, 3))
        self.assertFalse(foliage_map.settle_fallen_tree(7, 3))
        self.assertEqual(
            0.0, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_z()))
        self.assertEqual(
            BUSH, foliage_map.camouflage_bonus(observer, target))
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
        observer = FAR_OBSERVER
        target = (10.0, 0.0, 0.0)

        self.assertEqual(
            BUSH, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.activate_fallen_tree(7, 3))
        self.assertNotIn(0, foliage_map.inactive_instances)
        self.assertEqual(
            BUSH, foliage_map.camouflage_bonus(observer, target))
        self.assertTrue(foliage_map.update_fallen_tree_pose(
            7, 3, *_fallen_pose_x()))
        self.assertIn(0, foliage_map.inactive_instances)
        self.assertEqual(
            BUSH, foliage_map.camouflage_bonus(observer, target))
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
        self.assertEqual(BUSH, foliage_map.camouflage_bonus(
            (20.0, 0.0, 0.0), (60.0, 0.0, 0.0)))

    def test_sloped_fallen_tree_uses_full_3d_angle_not_vertical_prism(self):
        # The transparency radius would hide this crown from both observers,
        # so exercise the intersection the slope actually decides.
        instance, unused_bounds = foliage._dynamic_instance(
            (5.0, 5.0, 0.0), (
                (0.0, 0.0, 1.0),
                (5.0, 5.0, 0.0),
                (-0.5, 0.5, 0.0),
            ))

        self.assertTrue(foliage._intersects(
            instance, (0.0, 2.0, 0.0), (4.0, 1.5, 0.0)))
        self.assertFalse(foliage._intersects(
            instance, (8.0, 2.0, 0.0), (10.0, 1.5, 0.0)))

    def test_recent_shot_removes_an_activated_tree_at_the_target(self):
        foliage_map = foliage.FoliageMap({
            'map': 'test', 'cell_size': 32.0,
            'instances': [], 'cells': {},
            'fallen_trees': [_fallen_profile()],
        })
        foliage_map.activate_fallen_tree(7, 3)
        foliage_map.update_fallen_tree_pose(7, 3, *_fallen_pose_z())

        self.assertAlmostEqual(
            0.0,
            foliage_map.camouflage_bonus(
                (0.0, 0.0, -30.0), (0.0, 0.0, 15.0), True))


if __name__ == '__main__':
    unittest.main()
