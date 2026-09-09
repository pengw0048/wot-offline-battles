"""Exact indexed-surface queries; no native rendering or server parity claim."""
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import internal_mesh as mesh


def box(low, high):
    vertices = tuple((x, y, z) for x in (low[0], high[0])
                     for y in (low[1], high[1]) for z in (low[2], high[2]))
    faces = ((0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5),
             (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3))
    return vertices, faces


def join(*meshes):
    vertices, faces = [], []
    for points, triangles in meshes:
        faces.extend(tuple(i + len(vertices) for i in f) for f in triangles)
        vertices.extend(points)
    return tuple(vertices), tuple(faces)


class IndexedMeshTests(unittest.TestCase):
    def setUp(self):
        self.vertices, self.faces = join(box((-1.5, 0, -1), (-1, 1, 1)),
                                        box((1, 0, -1), (1.5, 1, 1)))
        self.pieces = mesh.prepare(self.vertices, self.faces, 'ammoBay')

    def test_codec_round_trip_is_lossless_and_deterministic(self):
        encoded = mesh.encode(self.vertices, self.faces)
        self.assertEqual((self.vertices, self.faces), mesh.decode(encoded))
        self.assertEqual(encoded, mesh.encode(self.vertices, self.faces))

    def test_separated_racks_keep_their_original_vertices_and_gap(self):
        self.assertEqual(2, len(self.pieces))
        self.assertEqual(set(self.vertices),
                         set(v for p in self.pieces for v in p['vertices']))
        for p in self.pieces:
            self.assertTrue(p['closed'])
            self.assertEqual((), mesh.intervals((0, .5, -3), (0, .5, 3), p))
        hits = sorted(interval for p in self.pieces for interval in
                      mesh.intervals((-2, .5, 0), (2, .5, 0), p))
        self.assertEqual([(0.125, 0.25), (0.75, 0.875)], hits)

    def test_short_finite_segment_and_starts_inside(self):
        left = self.pieces[0]
        self.assertEqual((), mesh.intervals((-2, .5, 0), (-1.6, .5, 0), left))
        self.assertEqual(((0., 1.),), mesh.intervals((-1.4, .5, 0), (-1.1, .5, 0), left))
        self.assertEqual(((0., .5),), mesh.intervals((-1.25, .5, 0), (-.75, .5, 0), left))
        self.assertTrue(mesh.contains((-1.25, .5, 0), left))
        self.assertFalse(mesh.contains((0, .5, 0), left))
        self.assertEqual(((0., 1.),), mesh.intervals((-1.25, .5, 0), (-1.25, .5, 0), left))

    def test_surface_boundary_and_shared_triangle_edge(self):
        left = self.pieces[0]
        self.assertEqual(((.125, .25),), mesh.intervals((-2, .5, 0), (2, .5, 0), left))
        self.assertTrue(mesh.contains((-1., .5, 0), left))
        self.assertEqual(0., mesh.distance((-1., .5, 0), left)[0])

    def test_distance_uses_faces_and_closed_interior(self):
        left = self.pieces[0]
        distance, closest = mesh.distance((0., .5, 0.), left)
        self.assertEqual(1., distance)
        self.assertEqual((-1., .5, 0.), closest)
        self.assertEqual((0., (-1.25, .5, 0.)), mesh.distance((-1.25, .5, 0.), left))
        self.assertAlmostEqual(math.sqrt(2), mesh.distance((0., 2., 0.), left)[0])

    def test_cone_does_not_fill_gap_and_hits_side_rack(self):
        for p in self.pieces:
            self.assertFalse(mesh.intersects_cone(p, (0., .5, -2.), (0., 0., 1.), 2., .2))
        self.assertTrue(mesh.intersects_cone(self.pieces[0], (-2., .5, 0.), (1., 0., 0.), 1., .2))
        self.assertFalse(mesh.intersects_cone(self.pieces[0], (-2., .5, 0.), (1., 0., 0.), .49, 1.))
        self.assertTrue(mesh.intersects_cone(self.pieces[0], (-1.25, .5, 0.), (1., 0., 0.), .01, 1.))

    def test_cone_hits_triangle_interior_even_when_all_vertices_miss(self):
        tri = ((-3., -3., 1.), (3., -3., 1.), (0., 3., 1.))
        self.assertTrue(mesh.triangle_intersects_cone(*tri, (0., 0., 0.), (0., 0., 1.), 1., .1))
        self.assertFalse(mesh.triangle_intersects_cone(*tri, (0., 0., 0.), (0., 0., 1.), .99, .1))
        shifted = tuple((p[0] + 5., p[1], p[2]) for p in tri)
        self.assertFalse(mesh.triangle_intersects_cone(*shifted, (0., 0., 0.), (0., 0., 1.), 1., .1))

    def test_cone_entry_is_first_axial_contact_not_nearest_point_depth(self):
        from gui.mods.offline_lan_0922 import internal_hit_layouts as layouts
        vertices = ((0., 0., 2.), (1.8, 0., 1.9), (1.8, 1., 1.9))
        primitive = mesh.prepare(vertices, ((0, 1, 2),))[0]
        entry = layouts._primitive_cone_entry({}, primitive, (0.,0.,0.),
                                               (0.,0.,1.), 3., 1.)
        self.assertAlmostEqual(1.9, entry, delta=3./16384.)

    def test_open_surface_never_invents_an_inside_volume(self):
        vertices = ((-1., -1., 0.), (1., -1., 0.), (0., 1., 0.))
        p = mesh.prepare(vertices, ((0, 1, 2),))[0]
        self.assertFalse(p['closed'])
        self.assertFalse(mesh.contains((0., 0., 0.), p))
        self.assertEqual(((.5, .5),), mesh.intervals((0., 0., -1.), (0., 0., 1.), p))
        self.assertEqual(1., mesh.distance((0., 0., 1.), p)[0])

    def test_tiny_real_gap_is_not_welded_by_position_tolerance(self):
        vertices, faces = join(box((-1., 0., 0.), (0., 1., 1.)),
                               box((1e-6, 0., 0.), (1., 1., 1.)))
        pieces = mesh.prepare(vertices, faces)
        self.assertEqual(2, len(pieces))
        for p in pieces:
            self.assertEqual((), mesh.intervals((5e-7, .2, .2), (5e-7, .8, .8), p))

    def test_acceleration_preserves_exact_result(self):
        vertices, faces = join(*(box((i * 2., 0., 0.), (i * 2. + 1., 1., 1.))
                                  for i in range(20)))
        pieces = mesh.prepare(vertices, faces)
        count = {}
        hits = [p for p in pieces if mesh.intervals((0.5, .5, -1.), (.5, .5, 2.), p, count)]
        self.assertEqual(1, len(hits))
        self.assertLess(count['triangles'], len(faces))

    def test_export_retains_indexed_geometry_without_acceleration(self):
        exported = mesh.export_primitive(self.pieces[0])
        self.assertNotIn('bvh', exported)
        self.assertEqual(self.pieces[0]['vertices'], exported['vertices'])
        self.assertEqual(self.pieces[0]['triangles'], exported['triangles'])

    def test_connected_hollow_mesh_keeps_two_disjoint_ray_intervals(self):
        # A square tube built from authored surface quads, not a convex hull.
        vertices = tuple((x, y, z) for y in (0., 1.)
                         for x, z in ((-2., -2.), (2., -2.), (2., 2.), (-2., 2.),
                                      (-1., -1.), (1., -1.), (1., 1.), (-1., 1.)))
        quads = []
        for i in range(4):
            j = (i + 1) % 4
            quads.extend(((i, j, j + 8, i + 8),
                          (i + 4, i + 12, j + 12, j + 4),
                          (i, i + 4, j + 4, j),
                          (i + 8, j + 8, j + 12, i + 12)))
        faces = tuple(f for a, b, c, d in quads for f in ((a, b, c), (a, c, d)))
        pieces = mesh.prepare(vertices, faces)
        self.assertEqual(1, len(pieces))
        p = pieces[0]
        self.assertTrue(p['closed'])
        hits = mesh.intervals((-3., .5, 0.), (3., .5, 0.), p)
        self.assertEqual(2, len(hits))
        for got, expected in zip(hits, ((1 / 6., 2 / 6.), (4 / 6., 5 / 6.))):
            for a, b in zip(got, expected):
                self.assertAlmostEqual(a, b)
        self.assertFalse(mesh.contains((0., .5, 0.), p))
        self.assertEqual(1., mesh.distance((0., .5, 0.), p)[0])
        self.assertFalse(mesh.intersects_cone(p, (0., -.5, 0.), (0., 1., 0.), 2., .2))
        self.assertTrue(mesh.intersects_cone(p, (0., -.5, 0.), (0., 1., 0.), 1.5, 1.))

    def test_runtime_consumers_preserve_mesh_hits_and_diagnostic_topology(self):
        from gui.mods.offline_lan_0922 import internal_geometry as geometry
        from gui.mods.offline_lan_0922 import internal_hit_layouts as layouts
        from gui.mods.offline_lan_0922 import internal_layout_store as store
        target = {'entity': 'ammoBay', 'kind': 'module', 'parent': 'hull',
                  'zone_id': 'ammoBay', 'primitives': self.pieces,
                  'center': (0., .5, 0.), 'half_extents': (1.5, .5, 1.),
                  'minimum': (-1.5, 0., -1.), 'maximum': (1.5, 1., 1.)}
        self.assertIsNone(geometry.target_interval((0., .5, -2.), (0., .5, 2.), target))
        layout = {'valid': True, 'targets': (target,)}
        hits = layouts.resolve_segments(layout, {'hull': ((-2., .5, 0.), (2., .5, 0.))}, 150.)
        self.assertEqual(2, len(hits))
        self.assertEqual([True, False], [h['damage_eligible'] for h in hits])
        self.assertTrue(all(h['remaining_penetration_after'] == 150. for h in hits))
        self.assertTrue(all(h['path_length_m'] == .5 for h in hits))
        for mode in ('sphere', 'cone'):
            self.assertEqual((), layouts.resolve_explosion(layout,
                {'hull': {'point': (0., .5, 0.), 'direction': (0., 0., 1.)}}, .5,
                mode=mode, cone_cos=.99))
        signature = layouts.geometry_signature(target, self.pieces[0]['primitive_id'])
        self.assertEqual(self.pieces[0]['triangles'], signature['triangles'])
        changed = dict(signature, triangles=signature['triangles'][:-1])
        self.assertFalse(layouts.compare_geometry_signatures(signature, changed)['synchronized'])
        with self.assertRaises(ValueError):
            store.set_target_geometry(target, (0., 0., 0.), (.1, .1, .1))
        with self.assertRaises(ValueError):
            store.set_target_rotation(target, 45.)
        self.assertIs(target, store.apply_target_override('unused', target))



if __name__ == '__main__':
    unittest.main()
