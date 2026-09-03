import copy
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / '0.9.22' / 'tools' /
          'bake_destructibles_0922.py')
DATA_ROOT = ROOT / '0.9.22' / 'destructibles'
CLIENT_SCRIPTS = (ROOT / '0.9.22' / 'src' / 'res' /
                  'scripts' / 'client')


def load_baker():
    spec = importlib.util.spec_from_file_location(
        'offline_lan_0922_destructibles_baker', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNTH_FRAGILE = 'content/Test/Fragile/normal/lod0/Fragile.model'
SYNTH_SHED = 'content/Test/Shed/normal/lod0/Shed.model'
SYNTH_POLE = 'content/Test/Pole/normal/lod0/Pole.model'


class _FakeSection:
    def __init__(self, data):
        self._data = data


class _FakeStrings:
    def __init__(self, table):
        self._table = dict(table)

    def get(self, key):
        return self._table.get(key)


class _FakeBSMI:
    def __init__(self, model_ids, transforms):
        self._ids = list(model_ids)
        self._data = {'transforms': [tuple(row) for row in transforms]}

    def model_ids(self):
        return list(self._ids)


def _transform(x, y, z, y_scale=1.0):
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, y_scale, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        x, y, z, 1.0,
    )


def _synthetic_scene():
    """One SpeedTree item, one empty item, one fragile, one two-module shed
    and one falling pole across two WGDE chunks."""
    descriptors = {
        SYNTH_FRAGILE: {'kind': 'fragile', 'modules': ()},
        SYNTH_SHED: {'kind': 'structure', 'modules': ('mod_a', 'mod_b')},
        SYNTH_POLE: {'kind': 'falling', 'modules': ()},
    }
    strings = _FakeStrings({
        1: 'content/Test/Fragile/normal/lod0/Fragile.primitives',
        2: 'content/Test/Shed/normal/lod0/Shed.primitives',
        3: 'content/Test/Pole/normal/lod0/Pole.primitives',
    })
    bsmo = {
        'model_info_items': [
            {'type': 2, 'info_index': 0},
            {'type': 2, 'info_index': 1},
            {'type': 2, 'info_index': 2},
            {'type': 1, 'info_index': 0},
        ],
        'fragile_model_info_items': [
            {'entry_type': 0}, {'entry_type': 1}, {'entry_type': 1}],
        'falling_model_info_items': [{}],
        'models_colliders': [
            {'bsp_section_name_fnv': 1,
             'collision_bounds_min': [-1.0, 0.0, -1.0],
             'collision_bounds_max': [1.0, 2.0, 1.0],
             'bsp_material_kind_begin': 0, 'bsp_material_kind_end': 0},
            {'bsp_section_name_fnv': 2,
             'collision_bounds_min': [-2.0, 0.0, -2.0],
             'collision_bounds_max': [0.0, 3.0, 2.0],
             'bsp_material_kind_begin': 1, 'bsp_material_kind_end': 1},
            {'bsp_section_name_fnv': 2,
             'collision_bounds_min': [0.0, 0.0, -2.0],
             'collision_bounds_max': [2.0, 3.0, 2.0],
             'bsp_material_kind_begin': 2, 'bsp_material_kind_end': 2},
            {'bsp_section_name_fnv': 3,
             'collision_bounds_min': [-0.3, 0.0, -0.3],
             'collision_bounds_max': [0.3, 9.0, 0.3],
             'bsp_material_kind_begin': 3, 'bsp_material_kind_end': 3},
        ],
        'bsp_material_kinds': [
            {'flags': 0 << 8}, {'flags': 73 << 8},
            {'flags': 74 << 8}, {'flags': 0 << 8}],
    }
    transforms = [
        _transform(2.0, 0.0, 4.0),
        _transform(10.0, 0.0, 10.0),
        _transform(10.0, 0.0, 10.0),
        _transform(-3.0, 0.0, 6.0),
    ]
    wgde = {
        '1': [(100, 0, 3), (200, 3, 2)],
        '2': [(0, 0), (1, 0), (1, 1), (2, 3), (4, 4)],
        '3': [0x80000000, 0, 1, 2, 3],
    }
    sections = {
        'BWST': strings,
        'BSMI': _FakeBSMI([0, 1, 2, 3], transforms),
        'BSMO': _FakeSection(bsmo),
        'WGDE': _FakeSection(wgde),
        'SpTr': _FakeSection({'speedtree_list': [{'transform': [0.0] * 16}]}),
    }
    return sections, descriptors


class DestructiblesBaker0922Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baker = load_baker()

    def test_contract_is_pinned_to_client_1513(self):
        self.assertEqual('offline-lan-0922-destructible-catalog',
                         self.baker.FORMAT_NAME)
        self.assertEqual(7, self.baker.FORMAT_VERSION)
        self.assertEqual(
            'offline-lan-0922-destructible-catalog-manifest',
            self.baker.MANIFEST_FORMAT)
        self.assertEqual('0.9.22.0.1-cn-1513', self.baker.GAME_VERSION)
        self.assertEqual(73, self.baker.NORMAL_MATERIAL_KIND_MIN)
        self.assertEqual(1000, self.baker.LOCATOR_QUANTIZATION)

    def test_every_catalog_version_pin_agrees(self):
        """A half-updated pin ships two catalogs that disagree about identity.

        The destructible and foliage batches derive their native wires from the
        same WGDE enumeration, so their versions must move together.  Crossing a
        pre-change foliage batch with a post-change destructible batch makes 29
        native item indices claim both a destructible instance and a fallen
        tree across the six maps that contain empty WGDE rows, which is exactly
        the corruption these pins exist to prevent.
        """
        def pinned(path, name):
            text = path.read_text(encoding='utf-8')
            match = re.search(
                r'(?m)^%s = (\d+)$' % re.escape(name), text)
            self.assertIsNotNone(match, '%s in %s' % (name, path.name))
            return int(match.group(1))

        builder = ROOT / '0.9.22' / 'build_wotmod.py'
        foliage_source = ROOT / '0.9.22' / 'tools' / 'bake_foliage_0922.py'
        destructible_loader = (
            CLIENT_SCRIPTS / 'gui' / 'mods' / 'offline_lan_0922' /
            'prebaked_destructibles.py')
        foliage_loader = (
            CLIENT_SCRIPTS / 'gui' / 'mods' / 'offline_lan_0922' /
            'prebaked_foliage.py')

        self.assertEqual(
            [self.baker.FORMAT_VERSION] * 3,
            [pinned(destructible_loader, 'FORMAT_VERSION'),
             pinned(builder, 'DESTRUCTIBLE_VERSION'),
             json.loads((DATA_ROOT / 'manifest.json').read_text(
                 encoding='utf-8'))['version']])

        foliage_version = pinned(foliage_source, 'FORMAT_VERSION')
        self.assertEqual(
            [foliage_version] * 3,
            [pinned(foliage_loader, 'FORMAT_VERSION'),
             pinned(builder, 'FOLIAGE_VERSION'),
             json.loads(
                 (ROOT / '0.9.22' / 'foliage' / 'manifest.json').read_text(
                     encoding='utf-8'))['version']])

    def test_locator_signature_is_world_origin_plus_basis_and_symmetric(self):
        transform = (
            2.0, 0.25, 0.0, 0.0,
            -0.5, 3.0, 0.0, 0.0,
            0.0, 0.0, -4.0, 0.0,
            12.3456, 0.0, -7.8904, 1.0,
        )
        self.assertEqual(
            (12346, 0, -7890, 2000, 250, 0, -500, 3000, 0,
             0, 0, -4000),
            self.baker._locator_signature(transform))
        self.assertEqual(1, self.baker._quantize_locator_value(0.0005))
        self.assertEqual(-1, self.baker._quantize_locator_value(-0.0005))

    def test_conflicting_locator_signature_fails_closed(self):
        first = (0.0,) * 16
        second = list(first)
        second[12] = 0.0004
        signature = self.baker._locator_signature(first)
        self.assertEqual(signature, self.baker._locator_signature(second))
        located = {}
        self.baker._record_locator(located, signature, 0, 'fragile.model')
        # An indistinguishable placement sharing the same box is safe.
        self.baker._record_locator(located, signature, 0, 'fragile.model')
        with self.assertRaisesRegex(ValueError, 'multiple boxes'):
            self.baker._record_locator(
                located, signature, 1, 'fragile.model')

    def _bake_synthetic(self, sections, descriptors):
        class _FakeCompiledSpace:
            def __init__(self, stream, version, realm, wanted):
                self.sections = sections

        with mock.patch.object(self.baker, 'CompiledSpace',
                               _FakeCompiledSpace):
            return self.baker.bake_compiled_map(
                'synthetic', b'pkg', b'space', b'xml', descriptors)

    def test_synthetic_wgde_wires_skip_empty_item_slots(self):
        sections, descriptors = _synthetic_scene()
        data = self._bake_synthetic(sections, descriptors)
        by_file = {row[12]: row for row in data['instances']}
        self.assertEqual(3, len(data['instances']))
        # Chunk 100 holds a SpeedTree item and an empty WGDE row before the
        # fragile. An empty row references no scene instance and is not a
        # streamed native item, so the fragile's itemIndex is 1 rather than its
        # table-2 position 2.
        self.assertEqual([100, 1], by_file[SYNTH_FRAGILE][14:16])
        # Both shed module rows form one multi-reference native item.
        self.assertEqual([200, 0], by_file[SYNTH_SHED][14:16])
        self.assertIsNone(by_file[SYNTH_SHED][13])
        self.assertEqual([200, 1], by_file[SYNTH_POLE][14:16])
        self.assertEqual(1.0, by_file[SYNTH_FRAGILE][16])
        self.assertEqual(1.0, by_file[SYNTH_SHED][16])

        compiled = types.SimpleNamespace(sections=sections)
        unused_rows, unused_wire_rows, speedtree_wires = \
            self.baker.native_wires(compiled, 4)
        self.assertEqual({0: (100, 0)}, speedtree_wires)

    def test_speedtree_wire_after_empty_wgde_row_skips_the_empty_slot(self):
        compiled = types.SimpleNamespace(sections={
            'WGDE': _FakeSection({
                '1': [(100, 0, 3)],
                '2': [(0, 0), (1, 0), (1, 1)],
                '3': [0x80000000, 0x80000001],
            }),
            'SpTr': _FakeSection({
                'speedtree_list': [object(), object()],
            }),
        })
        unused_rows, unused_wire_rows, speedtree_wires = \
            self.baker.native_wires(compiled, 0)
        # A SpeedTree row is a scene instance and consumes an item index just
        # like a BSMI row, so the second tree follows the first immediately.
        self.assertEqual({0: (100, 0), 1: (100, 1)}, speedtree_wires)

    def test_wgde_row_classes_consume_exactly_one_item_index_each(self):
        """Audit every slot-consuming WGDE row class in one scene.

        Chunk 100: empty row, BSMI row, two empty rows, SpTr row, a shared
        two-reference row, then a mixed BSMI+SpTr row.  Chunk 200 proves the
        item index restarts at zero and that a trailing empty row is legal.
        """
        compiled = types.SimpleNamespace(sections={
            'WGDE': _FakeSection({
                '1': [(200, 7, 3), (100, 0, 7)],
                '2': [
                    (0, -1),      # empty row before any instance
                    (0, 0),       # BSMI row 0
                    (1, 0),       # empty row between instances
                    (1, 0),       # a second consecutive empty row
                    (1, 1),       # SpTr row 0
                    (2, 3),       # two BSMI rows sharing one native item
                    (4, 5),       # BSMI row 3 plus SpTr row 1
                    (6, 6),       # chunk 200 BSMI row 4
                    (7, 7),       # chunk 200 SpTr row 2
                    (8, 7),       # trailing empty row
                ],
                '3': [0, 0x80000000, 1, 2, 3, 0x80000001, 4,
                      0x80000002],
            }),
            'SpTr': _FakeSection({
                'speedtree_list': [object(), object(), object()],
            }),
        })
        row_wires, wire_rows, speedtree_wires = self.baker.native_wires(
            compiled, 5)
        self.assertEqual({
            0: (100, 0),
            1: (100, 2), 2: (100, 2),
            3: (100, 3),
            4: (200, 0),
        }, row_wires)
        self.assertEqual({
            (100, 0): frozenset([0]),
            (100, 2): frozenset([1, 2]),
            (100, 3): frozenset([3]),
            (200, 0): frozenset([4]),
        }, wire_rows)
        self.assertEqual({0: (100, 1), 1: (100, 3), 2: (200, 1)},
                         speedtree_wires)

    def test_wgde_rejects_an_empty_row_that_is_not_a_backward_span(self):
        compiled = types.SimpleNamespace(sections={
            'WGDE': _FakeSection({
                '1': [(100, 0, 2)],
                '2': [(0, 0), (1, -3)],
                '3': [0],
            }),
            'SpTr': _FakeSection({'speedtree_list': []}),
        })
        with self.assertRaises(ValueError):
            self.baker.native_wires(compiled, 1)

    def test_synthetic_wgde_rejects_broken_chunk_ranges_and_spans(self):
        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['1'] = [(100, 1, 3), (200, 4, 1)]
        with self.assertRaisesRegex(ValueError, 'ranges are not contiguous'):
            self._bake_synthetic(sections, descriptors)

        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['1'] = [(100, 0, 3), (200, 3, 4)]
        with self.assertRaisesRegex(ValueError,
                                    'do not cover the item table'):
            self._bake_synthetic(sections, descriptors)

        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['2'][2] = (2, 2)
        with self.assertRaisesRegex(ValueError,
                                    'references are not contiguous'):
            self._bake_synthetic(sections, descriptors)

        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['2'][1] = (1, -1)
        with self.assertRaisesRegex(ValueError, 'empty item span is invalid'):
            self._bake_synthetic(sections, descriptors)

    def test_synthetic_wgde_rejects_bad_and_duplicate_references(self):
        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['3'][1] = 9
        with self.assertRaisesRegex(ValueError, 'BSMI reference is invalid'):
            self._bake_synthetic(sections, descriptors)

        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['3'][2] = 0
        with self.assertRaisesRegex(ValueError, 'BSMI reference is invalid'):
            self._bake_synthetic(sections, descriptors)

        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['3'][0] = 0x80000000 | 5
        with self.assertRaisesRegex(ValueError, 'SpTr reference is invalid'):
            self._bake_synthetic(sections, descriptors)

    def test_synthetic_instance_must_resolve_to_exactly_one_wire(self):
        # Splitting the shed module rows across two native items leaves the
        # emitted structure instance without a single wire.
        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['1'] = [(100, 0, 3), (200, 3, 3)]
        sections['WGDE']._data['2'] = [
            (0, 0), (1, 0), (1, 1), (2, 2), (3, 3), (4, 4)]
        with self.assertRaisesRegex(ValueError,
                                    'does not resolve to one native item'):
            self._bake_synthetic(sections, descriptors)

        # One native item spanning the fragile and the shed rows references
        # rows outside the fragile instance.
        sections, descriptors = _synthetic_scene()
        sections['WGDE']._data['1'] = [(100, 0, 3), (200, 3, 1)]
        sections['WGDE']._data['2'] = [(0, 0), (1, 0), (1, 3), (4, 4)]
        with self.assertRaisesRegex(ValueError,
                                    'references rows outside its instance'):
            self._bake_synthetic(sections, descriptors)

    def test_synthetic_instance_scales_must_agree(self):
        sections, descriptors = _synthetic_scene()
        transforms = sections['BSMI']._data['transforms']
        transforms[2] = _transform(10.0, 0.0, 10.0, y_scale=1.0000004)
        with self.assertRaisesRegex(ValueError, 'scales disagree'):
            self._bake_synthetic(sections, descriptors)

    def test_model_filename_join_is_separator_only_and_fail_closed(self):
        self.assertEqual(
            'content/Test/normal/lod0/Test.model',
            self.baker.model_filename_from_primitive(
                r'content\Test\normal\lod0\Test.primitives'))
        self.assertEqual(
            'content/Test/normal/lod0/Test.model',
            self.baker.model_filename_from_primitive(
                'content/Test/normal/lod2/Test.primitives'))
        with self.assertRaisesRegex(ValueError, 'not a primitives resource'):
            self.baker.model_filename_from_primitive(
                'content/Test/normal/lod0/Test.visual')
        with self.assertRaisesRegex(ValueError, 'invalid destructible'):
            self.baker.normalize_model_filename('../Test.model')

    def test_complete_real_batch_matches_manifest_checksums_and_schema(self):
        manifest_path = DATA_ROOT / 'manifest.json'
        self.assertTrue(manifest_path.is_file(),
                        'complete #1513 destructible batch is missing')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(self.baker.MANIFEST_FORMAT, manifest['format'])
        self.assertEqual(self.baker.FORMAT_VERSION, manifest['version'])
        self.assertEqual(self.baker.GAME_VERSION, manifest['game_version'])
        self.assertEqual(self.baker.LOCATOR_QUANTIZATION,
                         manifest['locator_quantization'])
        self.assertEqual(len(self.baker.SUPPORTED_MAPS),
                         manifest['census']['maps'])
        self.assertEqual(
            list(self.baker.SUPPORTED_MAPS),
            [record['map'] for record in manifest['maps']])
        aggregate = {
            key: 0 for key in (
                'resources', 'falling_resources', 'fragile_resources',
                'structure_resources', 'boxes', 'falling_boxes',
                'fragile_boxes', 'structure_boxes', 'instances',
                'falling_instances', 'fragile_instances',
                'structure_instances', 'variant_resources',
                'locator_resources', 'locators',
                'falling_locator_resources', 'falling_locators',
                'fragile_locator_resources', 'fragile_locators')}
        for key in (
                'instance_signatures', 'falling_instance_signatures',
                'fragile_instance_signatures',
                'structure_instance_signatures',
                'ambiguous_instance_signatures',
                'ambiguous_instance_candidates'):
            aggregate[key] = 0
        max_boxes = 0
        fragile_locator_instance_count = 0
        falling_locator_instance_count = 0
        for record in manifest['maps']:
            path = DATA_ROOT / record['file']
            self.assertTrue(path.is_file(), record['map'])
            self.assertEqual(record['sha256'],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['map'], data['map'])
            self.assertEqual(self.baker.FORMAT_NAME, data['format'])
            self.assertEqual(self.baker.FORMAT_VERSION, data['version'])
            self.assertEqual(self.baker.GAME_VERSION, data['game_version'])
            self.assertEqual(self.baker.LOCATOR_QUANTIZATION,
                             data['locator_quantization'])
            self.assertEqual(
                manifest['destructibles_xml_sha256'],
                data['source']['destructibles_xml_sha256'])
            self.assertEqual(record['map_package_sha256'],
                             data['source']['map_package_sha256'])
            self.assertEqual(record['space_bin_sha256'],
                             data['source']['space_bin_sha256'])
            self.assertGreater(len(data['resources']), 0)
            for filename, resource in data['resources'].items():
                self.assertTrue(filename.endswith('.model'))
                self.assertIn(resource['kind'],
                              ('falling', 'fragile', 'structure'))
                self.assertGreater(resource['instance_count'], 0)
                self.assertEqual(sorted(resource['bsmo_model_ids']),
                                 resource['bsmo_model_ids'])
                self.assertGreater(len(resource['boxes']), 0)
                for box in resource['boxes']:
                    self.assertEqual(7, len(box))
                    self.assertLess(box[0], box[3])
                    self.assertLess(box[1], box[4])
                    self.assertLess(box[2], box[5])
                    if resource['kind'] != 'structure':
                        self.assertIsNone(box[6])
                    else:
                        self.assertIsInstance(box[6], int)
                        self.assertGreaterEqual(box[6], 73)
                locators = resource.get('locators')
                if (resource['kind'] != 'structure' and
                        len(resource['boxes']) > 1):
                    self.assertTrue(locators)
                    if resource['kind'] == 'fragile':
                        fragile_locator_instance_count += resource[
                            'instance_count']
                    else:
                        falling_locator_instance_count += resource[
                            'instance_count']
                else:
                    self.assertIsNone(locators)
                seen_signatures = set()
                for locator in locators or ():
                    self.assertEqual(13, len(locator))
                    self.assertTrue(all(type(value) is int
                                        for value in locator))
                    self.assertNotIn(tuple(locator[:12]), seen_signatures)
                    seen_signatures.add(tuple(locator[:12]))
                    self.assertGreaterEqual(locator[12], 0)
                    self.assertLess(locator[12], len(resource['boxes']))
            seen_instance_signatures = set()
            seen_wires = set()
            instance_kinds = {kind: 0 for kind in (
                'falling', 'fragile', 'structure')}
            self.assertEqual(
                sorted(data['instances'], key=lambda row: tuple(row[:12])),
                data['instances'])
            for row in data['instances']:
                self.assertEqual(17, len(row))
                self.assertTrue(all(type(value) is int
                                    for value in row[:12]))
                signature = tuple(row[:12])
                self.assertNotIn(signature, seen_instance_signatures)
                seen_instance_signatures.add(signature)
                resource = data['resources'][row[12]]
                if resource['kind'] == 'structure':
                    self.assertIsNone(row[13])
                else:
                    self.assertIsInstance(row[13], int)
                    self.assertGreaterEqual(row[13], 0)
                    self.assertLess(row[13], len(resource['boxes']))
                chunk_id, item_index, item_scale = row[14:]
                self.assertIsInstance(chunk_id, int)
                self.assertIsInstance(item_index, int)
                self.assertGreaterEqual(chunk_id, 0)
                self.assertLessEqual(chunk_id, 0xFFFFFFFF)
                self.assertGreaterEqual(item_index, 0)
                self.assertNotIn((chunk_id, item_index), seen_wires)
                seen_wires.add((chunk_id, item_index))
                self.assertIsInstance(item_scale, float)
                self.assertGreater(item_scale, 0.0)
                instance_kinds[resource['kind']] += 1
            self.assertEqual(len(seen_wires), len(data['instances']))
            self.assertEqual(
                sorted(data['ambiguous_instances'],
                       key=lambda row: tuple(row[:12])),
                data['ambiguous_instances'])
            ambiguous_candidates = 0
            for row in data['ambiguous_instances']:
                self.assertEqual(13, len(row))
                signature = tuple(row[:12])
                self.assertNotIn(signature, seen_instance_signatures)
                seen_instance_signatures.add(signature)
                self.assertGreaterEqual(len(row[12]), 2)
                self.assertEqual(
                    sorted(row[12], key=lambda candidate: (
                        candidate[0], -1 if candidate[1] is None
                        else candidate[1])), row[12])
                ambiguous_candidates += len(row[12])
                for filename, box_index in row[12]:
                    resource = data['resources'][filename]
                    if resource['kind'] == 'structure':
                        self.assertIsNone(box_index)
                    else:
                        self.assertIsInstance(box_index, int)
                        self.assertGreaterEqual(box_index, 0)
                        self.assertLess(box_index, len(resource['boxes']))
            self.assertEqual(len(data['instances']),
                             data['census']['instance_signatures'])
            self.assertEqual(instance_kinds['falling'],
                             data['census']['falling_instance_signatures'])
            self.assertEqual(instance_kinds['fragile'],
                             data['census']['fragile_instance_signatures'])
            self.assertEqual(instance_kinds['structure'],
                             data['census']['structure_instance_signatures'])
            self.assertEqual(len(data['ambiguous_instances']),
                             data['census'][
                                 'ambiguous_instance_signatures'])
            self.assertEqual(ambiguous_candidates,
                             data['census'][
                                 'ambiguous_instance_candidates'])
            for key in aggregate:
                aggregate[key] += data['census'][key]
            max_boxes = max(max_boxes,
                            data['census']['max_boxes_per_resource'])
        self.assertEqual(aggregate, dict(
            (key, manifest['census'][key]) for key in aggregate))
        self.assertEqual(max_boxes,
                         manifest['census']['max_boxes_per_resource'])
        self.assertEqual(18,
                         manifest['census']['fragile_locator_resources'])
        self.assertEqual(534, manifest['census']['fragile_locators'])
        self.assertEqual(1,
                         manifest['census']['falling_locator_resources'])
        self.assertEqual(103, manifest['census']['falling_locators'])
        # One D-Day haystack placement is duplicated at the same transform
        # (world Y differs only 7.6e-06) and safely shares the same box index.
        self.assertEqual(535, fragile_locator_instance_count)
        self.assertEqual(103, falling_locator_instance_count)
        self.assertEqual(61625, manifest['census']['instance_signatures'])
        self.assertEqual(5754,
                         manifest['census']['falling_instance_signatures'])
        self.assertEqual(52853,
                         manifest['census']['fragile_instance_signatures'])
        self.assertEqual(3018,
                         manifest['census']['structure_instance_signatures'])
        self.assertEqual(11,
                         manifest['census'][
                             'ambiguous_instance_signatures'])
        self.assertEqual(28,
                         manifest['census'][
                             'ambiguous_instance_candidates'])

        eiffel = json.loads(
            (DATA_ROOT / '112_eiffel_tower_ctf.json').read_text(
                encoding='utf-8'))
        self.assertEqual(1, len(eiffel['ambiguous_instances']))
        self.assertEqual(5, len(eiffel['ambiguous_instances'][0][12]))
        self.assertTrue(all(
            'env_112_04_TrocaderoFountain_' in candidate[0]
            for candidate in eiffel['ambiguous_instances'][0][12]))

        dday = json.loads(
            (DATA_ROOT / '101_dday.json').read_text(encoding='utf-8'))
        repeated_structure = next(
            row for row in dday['ambiguous_instances']
            if len(row[12]) == 2 and row[12][0] == row[12][1] and
            dday['resources'][row[12][0][0]]['kind'] == 'structure')
        self.assertIsNone(repeated_structure[12][0][1])

    def test_real_catalogs_match_live_native_wires_1513(self):
        """Pin the exact live #1513 wires recovered on the Windows client.

        Each entry below was reported by the runtime as the live native item
        whose ``wg_getDestructibleMatrix`` quantized to a unique baked locator
        signature, so the live item index is external evidence rather than
        copied catalog output.  All three mismatching reports came from maps
        that contain empty WGDE rows, and each live index equals the non-empty
        row count instead of the table-2 position.
        """
        live = {
            # wot-error-report-20260901-212938: live=(32124, 7) baked=(32124, 8)
            '11_murovanka': (
                (32124, 7),
                'content/GatesAndFences/gaf001_WoodFence/normal/lod0/'
                'gaf001_WoodFence.model'),
            # wot-error-report-20260901-213957: live=(32893, 6) baked=(32893, 7)
            '18_cliff': (
                (32893, 6),
                'content/Buildings/bld704_shed/normal/lod0/bld704_shed.model'),
            # wot-error-report-20260902-124651: live=(33148, 58) baked=(33148, 59)
            '34_redshire': (
                (33148, 58),
                'content/Environment/env422_WallLamp/normal/lod0/'
                'env422_WallLamp.model'),
            # wot-error-report-20260902-210514 positive control: the requested
            # and actual live wire agreed through native destruction.
            '01_karelia': (
                (31610, 0),
                'content/MilitaryEnvironment/mle033_WatchTower/normal/lod0/'
                'mle033_WatchTower1.model'),
        }
        for map_name, (wire, filename) in live.items():
            data = json.loads(
                (DATA_ROOT / (map_name + '.json')).read_text(
                    encoding='utf-8'))
            matches = [row for row in data['instances']
                       if tuple(row[14:16]) == wire]
            self.assertEqual(1, len(matches), map_name)
            self.assertEqual(filename, matches[0][12], map_name)

    def test_real_catalogs_keep_empty_wgde_maps_compacted(self):
        """Regression guard for every map that contains empty WGDE rows.

        These six maps are the complete affected class: they are the only
        standard maps whose baked wires depend on the empty-row rule.  The three
        live-verified slots above are the evidence; these signatures only keep
        the remaining three maps from drifting back silently.
        """
        sentinels = {
            '07_lakeville': (
                (204385, -4826, 156370, -442, 0, 897,
                 0, 1000, 0, -897, 0, -442),
                'content/Environment/env414_Pole/normal/lod0/'
                'env414_Pole4.model', (33152, 169)),
            '11_murovanka': (
                (-544200, 10219, -84800, 465, 0, 192,
                 0, 503, 0, -192, 0, 465),
                'content/GatesAndFences/gaf004_FactoryFence/normal/lod0/'
                'gaf004_FactoryFenceEnd1.model', (31102, 47)),
            '18_cliff': (
                (-398922, -15130, 150940, -999, 0, -44,
                 0, 1000, 0, 44, 0, -999),
                'content/Environment/env009_FirewoodStack/normal/lod0/'
                'env009_FirewoodStack1.model', (31616, 18)),
            '23_westfeld': (
                (-272304, 58182, 42155, 777, -19, -629,
                 10, 1000, -18, 629, 7, 777),
                'content/Environment/env208_Log_Firewood/normal/lod0/'
                'env208_Log_Firewood04.model', (31871, 110)),
            '34_redshire': (
                (101444, 4652, -102045, 671, 0, -741,
                 0, 1000, 0, 741, 0, 671),
                'content/Environment/env413_StreetLamp/normal/lod0/'
                'env413_StreetLamp4.model', (32893, 199)),
            '36_fishing_bay': (
                (98938, 9842, 46403, 118, -31, -993,
                 51, 998, -26, 992, -48, 119),
                'content/GatesAndFences/gafBR_002_FieldFence/normal/lod0/'
                'gafBR_002_FieldFence1.model', (32895, 44)),
        }
        for map_name, (signature, filename, expected_wire) in \
                sentinels.items():
            data = json.loads(
                (DATA_ROOT / (map_name + '.json')).read_text(
                    encoding='utf-8'))
            row = next(
                row for row in data['instances']
                if tuple(row[:12]) == signature)
            self.assertEqual(filename, row[12], map_name)
            self.assertEqual(expected_wire, tuple(row[14:16]), map_name)

    def test_no_native_slot_is_both_a_destructible_and_a_fallen_tree(self):
        """One native item index cannot be a BSMI instance and a SpeedTree.

        Both catalogs derive their wires from the same WGDE enumeration, so a
        collision would prove that one of the two index spaces is wrong.  The
        pinned #1513 provider exposes one item array per chunk, shared by
        ``wg_getDestructibleMatrix``, the per-item name and the no-module
        effect category.
        """
        foliage_root = ROOT / '0.9.22' / 'foliage'
        checked = 0
        for map_name in self.baker.SUPPORTED_MAPS:
            data = json.loads(
                (DATA_ROOT / (map_name + '.json')).read_text(
                    encoding='utf-8'))
            foliage = json.loads(
                (foliage_root / (map_name + '.json')).read_text(
                    encoding='utf-8'))
            destructible_wires = set(
                tuple(row[14:16]) for row in data['instances'])
            tree_wires = [tuple(row[:2]) for row in foliage['fallen_trees']]
            self.assertEqual(len(tree_wires), len(set(tree_wires)), map_name)
            self.assertEqual(
                set(), destructible_wires & set(tree_wires), map_name)
            checked += 1
        self.assertEqual(len(self.baker.SUPPORTED_MAPS), checked)

    def test_highway_contains_exact_poles_fence_truck_and_shed(self):
        data = json.loads(
            (DATA_ROOT / '45_north_america.json').read_text(
                encoding='utf-8'))
        resources = data['resources']
        pole = resources[
            'content/Environment/envAM_009_Poles/normal/lod0/'
            'envAM_009_Poles_01.model']
        self.assertEqual('falling', pole['kind'])
        self.assertEqual([1], pole['bsmo_model_ids'])
        self.assertEqual(88, pole['instance_count'])
        self.assertGreater(pole['boxes'][0][4], 9.04)
        self.assertIsNone(pole['boxes'][0][6])
        fence = resources[
            'content/GatesAndFences/gafNW_001_VillageFance/normal/lod0/'
            'gafNW_001_VillageFance_gate_new.model']
        truck = resources[
            'content/Environment/envAM_011_Truck/normal/lod0/'
            'envAM_011_Truck01.model']
        shed = resources[
            'content/Buildings/bldAM_002_SmallShed/normal/lod0/'
            'bldAM_002_SmallShed.model']
        self.assertEqual('fragile', fence['kind'])
        self.assertEqual('fragile', truck['kind'])
        self.assertEqual('fragile', shed['kind'])
        self.assertEqual(277, fence['instance_count'])
        self.assertEqual(12, truck['instance_count'])
        self.assertEqual(9, shed['instance_count'])

    def test_ensk_contains_exact_shed_modules_and_long_fence(self):
        data = json.loads(
            (DATA_ROOT / '06_ensk.json').read_text(encoding='utf-8'))
        resources = data['resources']
        shed = resources[
            'content/Buildings/bld002_MiddleWoodShed/normal/lod0/'
            'bld002_MiddleWoodShed.model']
        self.assertEqual('structure', shed['kind'])
        self.assertEqual([73, 74], [box[6] for box in shed['boxes']])
        self.assertEqual([354, 356], shed['bsmo_model_ids'])
        little_shed = resources[
            'content/Buildings/bld003_LittleWoodShed/normal/lod0/'
            'bld003_LittleWoodShed.model']
        self.assertEqual([73], [box[6] for box in little_shed['boxes']])
        fence = resources[
            'content/GatesAndFences/gaf011_Fence/normal/lod0/'
            'gaf011_FenceTile1.model']
        self.assertEqual('fragile', fence['kind'])
        self.assertLess(fence['boxes'][0][2], -8.29)
        self.assertIsNone(fence['boxes'][0][6])

    def test_runtime_loader_accepts_real_ensk_and_rejects_bad_hash_and_box(self):
        package_names = ('gui', 'gui.mods', 'gui.mods.offline_lan_0922')
        saved = dict((name, sys.modules.get(name)) for name in package_names)
        saved_schema = sys.modules.get(
            'gui.mods.offline_lan_0922.navigation_graph_schema')
        saved_navigation = sys.modules.get(
            'gui.mods.offline_lan_0922.prebaked_navigation')
        try:
            for name in package_names:
                module = types.ModuleType(name)
                module.__path__ = []
                sys.modules[name] = module
            schema_path = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                           'offline_lan_0922' /
                           'navigation_graph_schema.py')
            schema_spec = importlib.util.spec_from_file_location(
                'gui.mods.offline_lan_0922.navigation_graph_schema',
                schema_path)
            schema = importlib.util.module_from_spec(schema_spec)
            schema_spec.loader.exec_module(schema)
            sys.modules[schema_spec.name] = schema
            with tempfile.TemporaryDirectory() as directory:
                navigation = types.ModuleType(
                    'gui.mods.offline_lan_0922.prebaked_navigation')
                navigation.mod_dir = lambda: directory
                sys.modules[navigation.__name__] = navigation
                loader_path = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                               'offline_lan_0922' /
                               'prebaked_destructibles.py')
                loader_spec = importlib.util.spec_from_file_location(
                    'gui.mods.offline_lan_0922.prebaked_destructibles_test',
                    loader_path)
                loader = importlib.util.module_from_spec(loader_spec)
                loader_spec.loader.exec_module(loader)
                target = Path(directory) / 'destructibles'
                target.mkdir()
                for filename in ('manifest.json', '06_ensk.json'):
                    path = DATA_ROOT / filename
                    (target / filename).write_bytes(path.read_bytes())
                manifest_path = target / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                manifest['game_version'] = 'locally-repacked-client'
                next(record for record in manifest['maps']
                     if record['map'] == '06_ensk')['sha256'] = \
                    'stale metadata'
                manifest_path.write_text(json.dumps(manifest))
                catalog_path = target / '06_ensk.json'
                catalog = json.loads(catalog_path.read_text())
                catalog['game_version'] = 'locally-repacked-client'
                catalog_path.write_text(json.dumps(catalog))
                loaded = loader.load_catalog('spaces/06_ensk')
                self.assertEqual('06_ensk', loaded['map'])
                self.assertIn(
                    'content/GatesAndFences/gaf011_Fence/normal/lod0/'
                    'gaf011_FenceTile1.model', loaded['resources'])

                original_manifest = json.loads(manifest_path.read_text())
                manifest = copy.deepcopy(original_manifest)
                selected = next(
                    record for record in manifest['maps']
                    if record['map'] == '06_ensk')
                selected['file'] = 'missing.json'
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(
                        ValueError, 'manifest record is invalid'):
                    loader.load_catalog('06_ensk')

                manifest = copy.deepcopy(original_manifest)
                manifest['locator_quantization'] = 999
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError,
                                             'manifest is incompatible'):
                    loader.load_catalog('01_karelia')

                manifest_path.unlink()
                with self.assertRaisesRegex(ValueError,
                                             'manifest is missing'):
                    loader.load_catalog('06_ensk')

                # A wholly absent optional catalog remains a normal None;
                # battle startup owns the supported-map requirement.
                for path in target.glob('*.json'):
                    path.unlink()
                target.rmdir()
                self.assertIsNone(loader.load_catalog('06_ensk'))

                no_census = copy.deepcopy(loaded)
                del no_census['census']
                self.assertIs(
                    no_census, loader._validate(no_census, '06_ensk'))

                unsorted_instances = copy.deepcopy(loaded)
                unsorted_instances['instances'][0:2] = reversed(
                    unsorted_instances['instances'][0:2])
                self.assertIs(
                    unsorted_instances,
                    loader._validate(unsorted_instances, '06_ensk'))

                duplicate_signature = copy.deepcopy(loaded)
                duplicate_signature['instances'][1][:12] = (
                    duplicate_signature['instances'][0][:12])
                with self.assertRaisesRegex(
                        ValueError, 'signature is invalid'):
                    loader._validate(duplicate_signature, '06_ensk')

                ambiguous_index = json.loads(
                    (DATA_ROOT / '101_dday.json').read_text())
                self.assertGreater(
                    len(ambiguous_index['ambiguous_instances']), 1)
                ambiguous_index['ambiguous_instances'][0:2] = reversed(
                    ambiguous_index['ambiguous_instances'][0:2])
                self.assertIs(
                    ambiguous_index,
                    loader._validate(ambiguous_index, '101_dday'))
                ambiguous_index['ambiguous_instances'][0][12].reverse()
                self.assertIs(
                    ambiguous_index,
                    loader._validate(ambiguous_index, '101_dday'))

                # Direct validation also fails closed on a degenerate box.
                bad = copy.deepcopy(loaded)
                first = next(iter(bad['resources'].values()))
                first['boxes'][0][3] = first['boxes'][0][0]
                with self.assertRaisesRegex(ValueError, 'box is invalid'):
                    loader._validate(bad, '06_ensk')

                for material in (71, 72, 86, 87, 100, 128):
                    bad_material = copy.deepcopy(loaded)
                    target_resource = next(
                        resource for resource in
                        bad_material['resources'].values()
                        if resource['kind'] == 'structure')
                    target_resource['boxes'][0][6] = material
                    with self.assertRaisesRegex(
                            ValueError,
                            'structure module material is invalid'):
                        loader._validate(bad_material, '06_ensk')

                ambiguous = json.loads(
                    (DATA_ROOT / '35_steppes.json').read_text())
                bad_locator = copy.deepcopy(ambiguous)
                target_resource = next(
                    resource for resource in
                    bad_locator['resources'].values()
                    if resource.get('locators'))
                target_resource['locators'][0] = [0] * 12 + [999]
                with self.assertRaisesRegex(ValueError,
                                             'locator is invalid'):
                    loader._validate(bad_locator, '35_steppes')

                missing_locator = copy.deepcopy(ambiguous)
                target_resource = next(
                    resource for resource in
                    missing_locator['resources'].values()
                    if resource.get('locators'))
                del target_resource['locators']
                with self.assertRaisesRegex(
                        ValueError, 'has no instance locators'):
                    loader._validate(missing_locator, '35_steppes')

                bad_wire = copy.deepcopy(loaded)
                bad_wire['instances'][1][14] = bad_wire['instances'][0][14]
                bad_wire['instances'][1][15] = bad_wire['instances'][0][15]
                with self.assertRaisesRegex(ValueError,
                                            'wire is duplicated'):
                    loader._validate(bad_wire, '06_ensk')

                bad_scale = copy.deepcopy(loaded)
                bad_scale['instances'][0][16] = 0.0
                with self.assertRaisesRegex(ValueError, 'scale is invalid'):
                    loader._validate(bad_scale, '06_ensk')

                bad_scale['instances'][0][16] = float('nan')
                with self.assertRaisesRegex(ValueError, 'scale is invalid'):
                    loader._validate(bad_scale, '06_ensk')

                short_row = copy.deepcopy(loaded)
                short_row['instances'][0] = short_row['instances'][0][:14]
                with self.assertRaisesRegex(ValueError, 'row is invalid'):
                    loader._validate(short_row, '06_ensk')
        finally:
            for name, value in saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
            for name, value in (
                    ('gui.mods.offline_lan_0922.navigation_graph_schema',
                     saved_schema),
                    ('gui.mods.offline_lan_0922.prebaked_navigation',
                     saved_navigation)):
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


if __name__ == '__main__':
    unittest.main()
