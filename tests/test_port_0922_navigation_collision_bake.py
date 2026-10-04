"""Offline geometry regressions; these do not claim native-client acceptance."""
import copy
import json
import math
import struct
import sys
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import bake_navigation_0922 as baker

IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
WALL = (((0., 0., -10.), (0., 8., -10.), (0., 8., 10.)),
        ((0., 0., -10.), (0., 8., 10.), (0., 0., 10.)))
BOUNDS = ((-0.25, 0., -10.), (0.25, 8., 10.))


def bsp(records, version=2, padding=0):
    """Use real binary BSP and primitive tables, not a decoder mock."""
    data = b''.join(struct.pack('<9fHH', *(sum(triangle, ()) + (index, padding)))
                    for triangle, index in records)
    if version == 0:
        return struct.pack('<4I', 0x00505342, len(records), 1, 0) + data
    header = struct.pack('<4I3I6f2I', 0x02505342, 16, 40, 40,
                         len(records), 1, 0, -20., 0., -20., 20., 10., 20., 0, 0)
    return header + data + bytes(40)


def primitives(payload, section_name='bsp2'):
    name = section_name.encode('utf-8')
    table = struct.pack('<6I', len(payload), 0, 0, 0, 0, len(name)) + name
    table += bytes((-len(name)) % 4)
    return (b'\x65\x4e\xa1\x42' + payload + bytes((-len(payload)) % 4)
            + table + struct.pack('<I', len(table)))


class VFS:
    def __init__(self, records, version=2):
        self.files = {'objects/wall.primitives_processed': primitives(bsp(records, version))}
        self.reads = []

    def read(self, name):
        self.reads.append(name)
        return self.files[name]


def compiled(remaps, transforms=None, masks=None, labels=None):
    transforms = transforms or [IDENTITY] * len(remaps)
    masks = [1] * len(remaps) if masks is None else masks
    rows, colliders = [], []
    for remap in remaps:
        first = len(rows)
        rows.extend(dict(material_index=index, flags=flags) for index, flags in remap)
        colliders.append(dict(collision_bounds_min=BOUNDS[0], collision_bounds_max=BOUNDS[1],
                              bsp_section_name_fnv=1, bsp_material_kind_begin=first,
                              bsp_material_kind_end=len(rows) - 1))
    return types.SimpleNamespace(sections={
        'BSMO': types.SimpleNamespace(_data=dict(
            models_colliders=colliders, bsp_material_kinds=rows,
            model_info_items=[{'type': t} for t in (labels or [3] * len(remaps))])),
        'BSMI': types.SimpleNamespace(
            _data=dict(transforms=transforms, visibility_masks=masks),
            model_ids=lambda: iter(range(len(remaps)))),
        'BWST': {1: 'objects/wall.primitives'},
    })


class CollisionBakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = baker._legacy_baker()

    def obstacles(self, flags=73 << 8, records=None, data=None, vfs=None):
        records = [(t, 7) for t in WALL] if records is None else records
        return baker.compiled_navigation_obstacles(
            data or compiled([[(7, flags)]]), vfs or VFS(records), self.legacy)

    def test_all_original_materials_excluded_before_raster_regardless_of_model_type(self):
        for kind in range(71, 86):
            for label in (0, 1, 2, 3):
                with self.subTest(kind=kind, label=label):
                    field = self.obstacles(data=compiled([[(7, kind << 8)]], labels=[label]))
                    self.assertEqual({}, field.cells)
                    self.assertEqual({}, field.surface_cells)
                    self.assertEqual(2, field.collision_stats['original_surfaces_excluded'])
                    self.assertEqual(1, len(field.soft_spawn_obbs))

    def test_hard_and_replacement_materials_stay_in_raster(self):
        for kind in (0, 70, 86, 87, 100, 108):
            with self.subTest(kind=kind):
                field = self.obstacles(kind << 8)
                self.assertTrue(field.blocked(0., 0., 0., margin=2.15))
                self.assertEqual(2, field.collision_stats['hard_surfaces_retained'])

    def test_vehicle_skip_bits_do_not_drop_projectile_only_exclusion(self):
        for low_flags in (0x10, 0x40, 0x50, 0xff):
            with self.subTest(flags=low_flags):
                self.assertFalse(self.obstacles(low_flags).cells)
        self.assertTrue(self.obstacles(0x80).cells)

    def test_noncolliding_original_surfaces_do_not_create_spawn_obstacles(self):
        field = self.obstacles((73 << 8) | 0x10)
        self.assertFalse(field.cells)
        self.assertFalse(field.soft_spawn_obbs)
        self.assertEqual(2, field.collision_stats['nonvehicle_surfaces_excluded'])

    def test_sparse_reordered_material_map_and_nonzero_padding(self):
        vfs = VFS([])
        vfs.files['objects/wall.primitives_processed'] = primitives(
            bsp([(WALL[0], 37), (WALL[1], 2)], padding=0xabcd))
        field = self.obstacles(data=compiled([[(2, 0), (37, 73 << 8)]]), vfs=vfs)
        self.assertEqual(1, field.collision_stats['original_surfaces_excluded'])
        self.assertEqual(1, field.collision_stats['hard_surfaces_retained'])
        self.assertEqual(1, field.collision_stats['mixed_instances'])
        self.assertTrue(field.cells)

    def test_shared_primitive_is_classified_per_model_not_per_resource(self):
        shifted = IDENTITY[:12] + (12., 0., 0., 1.)
        data = compiled([[(7, 73 << 8)], [(7, 0)]], transforms=[IDENTITY, shifted])
        vfs = VFS([(t, 7) for t in WALL])
        field = self.obstacles(data=data, vfs=vfs)
        self.assertFalse(field.blocked(0., 0., 0.))
        self.assertTrue(field.blocked(12., 0., 0.))
        self.assertEqual(['objects/wall.primitives_processed'], vfs.reads)

    def test_ctf_visibility_and_empty_collision_are_not_render_inferences(self):
        data = compiled([[(7, 0)], [(7, 0)]], masks=[2, 1])
        data.sections['BSMO']._data['models_colliders'][1].update(
            bsp_section_name_fnv=0, bsp_material_kind_begin=0xffffffff,
            bsp_material_kind_end=0xffffffff)
        field = self.obstacles(data=data)
        self.assertFalse(field.cells)
        self.assertEqual(1, field.collision_stats['invisible_instances'])
        self.assertEqual(1, field.collision_stats['noncolliding_instances'])
        data.sections['BWSV'] = types.SimpleNamespace(_data={'visibility_masks': [1, 2]})
        self.assertTrue(self.obstacles(data=data).cells)

    def test_missing_mapping_and_conflicting_id_abort_not_keep_as_wall(self):
        for remap in ([(8, 0)], [(7, 0), (7, 73 << 8)], [(7, 0x10000)], []):
            with self.subTest(remap=remap), self.assertRaises(baker.UnsafeBakeInputError):
                self.obstacles(data=compiled([remap]))

    def test_missing_bsp_never_falls_back_to_render_mesh(self):
        vfs = VFS([])
        vfs.files['objects/wall.primitives_processed'] = primitives(bytes(100), 'vertices')
        with self.assertRaisesRegex(baker.UnsafeBakeInputError, 'authored collision'):
            self.obstacles(vfs=vfs)

    def test_component_selects_subset_of_shared_bsp_not_other_modules(self):
        # Actual Airfield buildings share one BSP between base and modules.
        # Selecting the soft upper module must not resurrect the hard base
        # at a second transform, nor treat all omitted entries as hard walls.
        base = tuple((x + 20., y, z) for x, y, z in WALL[0])
        field = self.obstacles(records=[(base, 0), (WALL[0], 1)],
                               data=compiled([[(1, 0x4900)]]))
        self.assertEqual(1, field.collision_stats['original_surfaces_excluded'])
        self.assertEqual(1, field.collision_stats['unselected_component_surfaces'])
        self.assertFalse(field.cells)

    def test_hard_sibling_is_retained_with_its_own_component_selection(self):
        field = self.obstacles(records=[(WALL[0], 0), (WALL[1], 1)],
                               data=compiled([[(0, 0x6f83)], [(1, 0x4900)]]))
        self.assertEqual(1, field.collision_stats['hard_surfaces_retained'])
        self.assertEqual(1, field.collision_stats['original_surfaces_excluded'])
        self.assertEqual(2, field.collision_stats['unselected_component_surfaces'])
        self.assertTrue(field.cells)

    def test_inactive_replacement_in_shared_bsp_is_not_spawned_by_baker(self):
        data = compiled([[(0, 0x4900)], [(1, 0x5700)]], masks=[1, 0])
        field = self.obstacles(records=[(WALL[0], 0), (WALL[1], 1)], data=data)
        self.assertFalse(field.cells)
        self.assertEqual(1, field.collision_stats['invisible_instances'])
        self.assertEqual(1, field.collision_stats['unselected_component_surfaces'])

    def test_declared_empty_named_primitive_has_no_render_fallback(self):
        data = compiled([[(7, 0)]])
        data.sections['BSMO']._data['models_colliders'][0].update(
            bsp_material_kind_begin=0xffffffff, bsp_material_kind_end=0xffffffff)
        vfs = VFS([])
        vfs.files['objects/wall.primitives_processed'] = primitives(bytes(100), 'vertices')
        field = self.obstacles(data=data, vfs=vfs)
        self.assertFalse(field.cells)
        self.assertEqual(1, field.collision_stats['empty_collision_resources'])

    def test_empty_declaration_with_real_bsp_is_not_silently_discarded(self):
        data = compiled([[(7, 0)]])
        data.sections['BSMO']._data['models_colliders'][0].update(
            bsp_material_kind_begin=0xffffffff, bsp_material_kind_end=0xffffffff)
        with self.assertRaisesRegex(baker.UnsafeBakeInputError, 'without a material selection'):
            self.obstacles(data=data)

    def test_half_empty_material_span_is_invalid(self):
        for first, last in ((0xffffffff, 0), (0, 0xffffffff)):
            data = compiled([[(7, 0)]])
            data.sections['BSMO']._data['models_colliders'][0].update(
                bsp_material_kind_begin=first, bsp_material_kind_end=last)
            with self.assertRaisesRegex(baker.UnsafeBakeInputError, 'invalid material range'):
                self.obstacles(data=data)

    def test_selected_id_absent_from_the_shared_bsp_is_invalid(self):
        with self.assertRaisesRegex(baker.UnsafeBakeInputError, 'absent BSP IDs'):
            self.obstacles(records=[(WALL[0], 1), (WALL[1], 2)],
                           data=compiled([[(3, 0x4900)]]))

    def test_offline_spawn_pack_reconsiders_greedy_choice_without_moving_bounds(self):
        # Middle-first greedy accepts one; a complete packing needs the ends.
        nodes = [(0, 0., 0., 0.), (1, -8., 0., 0.), (2, 8., 0., 0.)]
        self.assertEqual([1, 2], baker._pack_spawn_candidates(nodes, 0., 2., 5., 2))
        self.assertIsNone(baker._pack_spawn_candidates(nodes, 0., 2., 5., 3))

    def test_offline_spawn_pack_retains_long_chassis_overlap_check(self):
        nodes = [(0, 0., 0., 0.), (1, 0., 0., 10.6)]
        self.assertIsNone(baker._pack_spawn_candidates(nodes, 0., 2.24, 5.47, 2))
        self.assertEqual([0, 1], baker._pack_spawn_candidates(nodes, 0., 2.24, 5., 2))

    def test_malformed_inputs_abort(self):
        base = compiled([[(7, 0)]])
        cases = []
        data = copy.deepcopy(base)
        data.sections['BSMI']._data['visibility_masks'] = []
        cases.append(data)
        data = copy.deepcopy(base)
        data.sections['BSMI']._data['transforms'][0] = IDENTITY[:12] + (math.nan, 0., 0., 1.)
        cases.append(data)
        data = copy.deepcopy(base)
        data.sections['BSMO']._data['models_colliders'][0]['bsp_section_name_fnv'] = 0
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(baker.UnsafeBakeInputError):
                self.obstacles(data=data)

    def test_v0_material_id_ignores_padding_too(self):
        data = bsp([(WALL[0], 123)], version=0, padding=0xabcd)
        self.assertEqual([(WALL[0], 123)], baker._bsp_triangles_0922(
            data, self.legacy, with_materials=True))
        for broken in (data[:10], data[:-1]):
            with self.assertRaises(ValueError):
                baker._bsp_triangles_0922(broken, self.legacy, with_materials=True)

    def test_disabled_nonfinite_triangle_does_not_become_an_obstacle(self):
        sentinel = ((math.nan, 0., 0.),) * 3
        field = self.obstacles(records=[(sentinel, 65535)])
        self.assertEqual(1, field.invalid_triangles)
        self.assertEqual({}, field.cells)

    def test_short_elevated_hard_collider_is_not_removed_by_local_height(self):
        triangle = ((0., 0., -10.), (0., .5, -10.), (0., .5, 10.))
        raised = IDENTITY[:12] + (0., 1., 0., 1.)
        data = compiled([[(7, 0)]], transforms=[raised])
        data.sections['BSMO']._data['models_colliders'][0].update(
            collision_bounds_min=(-.25, 0., -10.), collision_bounds_max=(.25, .5, 10.))
        self.assertTrue(self.obstacles(records=[(triangle, 7)], data=data).blocked(0., 0., 0.))

    def test_bridge_keeps_deck_when_projectiles_do_not_collide(self):
        deck = ((-10., 2., -10.), (10., 2., -10.), (10., 2., 10.))
        data = compiled([[(7, 0x80)]])
        data.sections['BWST'][1] = 'objects/bridge.primitives'
        vfs = VFS([])
        vfs.files['objects/bridge.primitives_processed'] = primitives(bsp([(deck, 7)]))
        field = self.obstacles(data=data, vfs=vfs)
        self.assertEqual(1, field.bridge_instance_count)
        self.assertTrue(field.surface_cells)
        self.assertAlmostEqual(2., field.surface_height(0., 0.))

    def bake_synthetic(self, field):
        """Exercise actual node/link/clearance/component/route construction."""
        class FlatTerrain:
            chunks = {0: None}
            waters = ()
            @staticmethod
            def height(x, z):
                return 0.
            @staticmethod
            def water_depth(x, z, height=None):
                return 0.
        points = ((-18., 2., False), (18., 2., False))
        routes = tuple(dict(id='crossing', team=team, capacity=15, risk=0.5,
                            role_weights={'medium': 1.0}, points=points)
                       for team in (1, 2))
        config = dict(bounds=(-24., -24., 24., 24.),
                      bases=((-18., 2.), (18., 2.)), anchors=(), routes=routes)
        with patch.object(self.legacy, 'MAPS', {'synthetic': config}), \
                patch.object(self.legacy, 'Terrain', lambda *a: FlatTerrain()), \
                patch.object(self.legacy, 'ObstacleField', lambda *a, **kw: field):
            return self.legacy.bake_graph(None, 'synthetic')

    def test_fresh_graph_has_identical_cells_links_hazards_and_routes_with_soft_scenery(self):
        empty = self.bake_synthetic(self.obstacles(records=[]))
        soft = self.bake_synthetic(self.obstacles())
        hard = self.bake_synthetic(self.obstacles(flags=0))
        for key in ('heights_mm', 'links', 'hazards', 'routes', 'validation'):
            self.assertEqual(empty[key], soft[key], key)
        self.assertNotEqual(empty['heights_mm'], hard['heights_mm'])
        self.assertNotEqual(empty['links'], hard['links'])
        self.assertGreater(hard['bake']['rejected_obstacle_nodes'], 0)
        # Cold JSON round trip has the complete graph, with no engine or live repair.
        cold = json.loads(json.dumps(soft))
        start, _ = self.legacy._nearest_node(cold, (-18., 2.))
        goal, _ = self.legacy._nearest_node(cold, (18., 2.))
        path, distance = self.legacy._graph_path(cold, start, goal)
        self.assertTrue(path)
        self.assertLess(distance, 42.)

    def test_batch_refuses_old_graph_even_with_valid_schema(self):
        import bake_all_navigation_0922 as batch
        old = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        # The checked-in map may already be rebuilt. Explicitly construct a
        # schema-valid legacy policy rather than assuming that asset stays old.
        old['bake'].pop('navigation_collision_policy', None)
        old['bake'].pop('original_destructible_surfaces_excluded', None)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(batch.baker, 'bake_map_graph', return_value=old):
            with self.assertRaisesRegex(ValueError, 'fresh compiled-surface'):
                batch._bake_one('unused-client', directory, '31_airfield')

    def test_batch_accepts_only_the_explicit_clean_policy(self):
        import bake_all_navigation_0922 as batch
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        # This is a schema/policy mock, NOT a rebuilt Airfield artifact.
        graph['bake'].update(navigation_collision_policy=baker.NAVIGATION_COLLISION_POLICY,
                             original_destructible_surfaces_excluded=True)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '31_airfield.json').write_text(json.dumps(graph))
            with patch.object(batch.baker, 'bake_map_graph', return_value=graph):
                name, digest = batch._bake_one('unused-client', directory, '31_airfield')
            self.assertEqual('31_airfield', name)
            self.assertEqual(64, len(digest))

    def test_spawn_only_overlap_does_not_poison_route_raster(self):
        field = self.obstacles()
        self.assertFalse(baker.spawn_obstacle_obb_blocked(
            field, 0., 0., 0., 0., 1., 2., self.legacy))
        self.assertTrue(baker.spawn_soft_destructible_obb_blocked(
            field, 0., 0., 0., 0., 1., 2., self.legacy))


if __name__ == '__main__':
    unittest.main()
