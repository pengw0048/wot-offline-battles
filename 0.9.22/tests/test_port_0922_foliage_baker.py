import hashlib
import importlib.util
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '0.9.22' / 'tools' / 'bake_foliage_0922.py'
DATA_ROOT = ROOT / '0.9.22' / 'foliage'
CLIENT_SCRIPTS = (ROOT / '0.9.22' / 'src' / 'res' /
                  'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import prebaked_foliage


def load_baker():
    spec = importlib.util.spec_from_file_location(
        'offline_lan_0922_foliage_baker', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FoliageBaker0922Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baker = load_baker()

    def test_contract_is_pinned_to_client_1513(self):
        self.assertEqual('offline-lan-0922-foliage',
                         self.baker.FORMAT_NAME)
        self.assertEqual(4, self.baker.FORMAT_VERSION)
        self.assertEqual('offline-lan-0922-foliage-manifest',
                         self.baker.MANIFEST_FORMAT)
        self.assertEqual('0.9.22.0.1-cn-1513', self.baker.GAME_VERSION)

    def test_taxonomy_matches_client_asset_name_fragments(self):
        tokens = ('bush', 'cedar', 'shrub')
        self.assertTrue(self.baker.is_bush_resource(
            'speedtree/07_Lakeville/Shrub.spt', tokens))
        self.assertTrue(self.baker.is_bush_resource(
            'SPEEDTREE/31_AIRFIELD/CEDAR_02.SPT', tokens))
        self.assertFalse(self.baker.is_bush_resource(
            'speedtree/07_Lakeville/Oak.spt', tokens))

    def test_ctree_106_header_supplies_exact_local_bounds(self):
        data = struct.pack('<I6f', 106, -2.0, -1.0, -3.0,
                           4.0, 5.0, 6.0)
        self.assertEqual(
            ((-2.0, -1.0, -3.0), (4.0, 5.0, 6.0)),
            self.baker.ctree_bounds(data))
        with self.assertRaisesRegex(ValueError, 'version 105'):
            self.baker.ctree_bounds(
                struct.pack('<I6f', 105, -2.0, -1.0, -3.0,
                            4.0, 5.0, 6.0))

    def test_world_transform_is_invertible_and_has_no_chunk_offset(self):
        transform = (
            2.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 3.0, 0.0,
            25.0, 4.0, 75.0, 1.0,
        )
        row, bounds = self.baker.foliage_instance(
            ((-1.0, 0.0, -1.0), (1.0, 4.0, 1.0)), transform)
        self.assertEqual([25.0, 4.0, 75.0, 8.0], row[:4])
        self.assertAlmostEqual(0.5, row[4])
        self.assertAlmostEqual(1.0 / 3.0, row[7], places=4)
        self.assertEqual((23.0, 72.0, 27.0, 78.0), bounds)

    def test_sideways_bush_uses_its_horizontal_yz_projection(self):
        transform = (
            0.0, 1.0, 0.0, 0.0,
            -1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 2.0, 0.0, 1.0,
        )
        row, bounds = self.baker.foliage_instance(
            ((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0)), transform)
        self.assertEqual((-2.0, -3.0, 2.0, 3.0), bounds)
        self.assertEqual([0.0, 1.0, 0.0, 3.0], row[:4])

    def test_casefold_vfs_prefers_map_package(self):
        with tempfile.TemporaryDirectory() as directory:
            map_path = Path(directory) / 'map.pkg'
            shared_path = Path(directory) / 'shared_content.pkg'
            with zipfile.ZipFile(map_path, 'w') as package:
                package.writestr('SpeedTree/Map/Bush.ctree', b'map')
            with zipfile.ZipFile(shared_path, 'w') as package:
                package.writestr('speedtree/map/bush.ctree', b'shared')
            with self.baker.CaseFoldZipResources(
                    (str(map_path), str(shared_path))) as resources:
                self.assertEqual(
                    b'map', resources.read('SPEEDTREE\\MAP\\BUSH.CTREE'))

    def test_pure_bake_emits_ten_value_rows_and_spatial_cells(self):
        payload = struct.pack('<I6f', 106, -1.0, 0.0, -1.0,
                              1.0, 4.0, 1.0)

        class Resources(object):
            @staticmethod
            def read(name):
                if name.lower() != 'speedtree/test/bush.ctree':
                    raise KeyError(name)
                return payload

        transform = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            31.5, 2.0, 31.5, 1.0,
        )
        data = self.baker.bake_speedtrees(
            Resources(), 'test_map', ('bush',),
            [('speedtree/test/Bush.spt', transform)], 32.0)
        self.assertEqual(1, len(data['instances']))
        self.assertEqual(10, len(data['instances'][0]))
        self.assertEqual({'0,0': [0], '0,1': [0],
                          '1,0': [0], '1,1': [0]}, data['cells'])
        self.assertEqual([], data['fallen_trees'])

    def test_pure_bake_maps_fallable_trees_to_exact_native_wires(self):
        payload = struct.pack('<I6f', 106, -2.0, 0.0, -1.0,
                              2.0, 10.0, 1.0)

        class Resources(object):
            @staticmethod
            def read(name):
                if name.lower() not in (
                        'speedtree/test/oak.ctree',
                        'speedtree/test/shrubsouth.ctree'):
                    raise KeyError(name)
                return payload

        transform = (
            2.0, 0.0, 0.0, 0.0,
            0.0, 2.0, 0.0, 0.0,
            0.0, 0.0, 2.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        )
        data = self.baker.bake_speedtrees(
            Resources(), 'test_map', ('shrubsouth',), [
                ('speedtree/test/Oak.spt', transform),
                ('speedtree/test/ShrubSouth.spt', transform),
            ], 32.0, {
                'speedtree/test/oak.spt': {
                    'health': 20.0, 'density': 0.3},
                'speedtree/test/shrubsouth.spt': {
                    'health': 20.0, 'density': 0.3},
            }, {0: (12, 7), 1: (12, 8)})

        self.assertEqual(1, len(data['instances']))
        self.assertEqual([
            [12, 7, -2.0, 0.0, -1.0, 2.0, 10.0, 1.0, None],
            [12, 8, -2.0, 0.0, -1.0, 2.0, 10.0, 1.0, 0],
        ], data['fallen_trees'])

    def test_complete_real_batch_matches_manifest_checksums(self):
        manifest_path = DATA_ROOT / 'manifest.json'
        self.assertTrue(manifest_path.is_file(),
                        'complete #1513 foliage batch is missing')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(self.baker.MANIFEST_FORMAT, manifest['format'])
        self.assertEqual(self.baker.FORMAT_VERSION, manifest['version'])
        self.assertEqual(self.baker.GAME_VERSION, manifest['game_version'])
        self.assertEqual(
            list(self.baker.SUPPORTED_MAPS),
            [record['map'] for record in manifest['maps']])
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
            self.assertGreater(len(data['instances']), 0)
            self.assertTrue(all(len(row) == 10 for row in data['instances']))
            self.assertGreater(len(data['fallen_trees']), 0)
            self.assertTrue(all(
                len(row) == 9 for row in data['fallen_trees']))

    def test_fallen_tree_wires_are_unique_and_dense_per_chunk(self):
        """A fallen tree owns one native item index, never a shared one.

        A fallen tree's wire comes from the same WGDE enumeration as the
        destructible catalog, where an empty row references no scene instance
        and consumes no streamed item index.  A duplicate wire inside one map
        would mean two trees claim one native item.
        """
        for map_name in self.baker.SUPPORTED_MAPS:
            data = json.loads(
                (DATA_ROOT / (map_name + '.json')).read_text(
                    encoding='utf-8'))
            wires = [tuple(row[:2]) for row in data['fallen_trees']]
            self.assertEqual(len(wires), len(set(wires)), map_name)
            for chunk_id, item_index in wires:
                self.assertGreaterEqual(chunk_id, 0, map_name)
                self.assertGreaterEqual(item_index, 0, map_name)

    def test_runtime_loader_only_validates_selected_foliage_record(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'foliage'
            target.mkdir()
            manifest_path = target / 'manifest.json'
            shutil.copy2(DATA_ROOT / 'manifest.json', manifest_path)
            shutil.copy2(DATA_ROOT / '06_ensk.json',
                         target / '06_ensk.json')
            data_path = target / '06_ensk.json'
            data = json.loads(data_path.read_text())
            data['game_version'] = 'locally-repacked-client'
            data_path.write_text(json.dumps(data))

            loaded = prebaked_foliage.load_foliage(
                '06_ensk', base_dir=directory)
            self.assertEqual('06_ensk', loaded.map_name)

            manifest = json.loads(manifest_path.read_text())
            selected = next(
                record for record in manifest['maps']
                if record['map'] == '06_ensk')
            selected['sha256'] = 'stale build metadata'
            manifest['game_version'] = 'locally-repacked-client'
            manifest_path.write_text(json.dumps(manifest))
            loaded = prebaked_foliage.load_foliage(
                '06_ensk', base_dir=directory)
            self.assertEqual('06_ensk', loaded.map_name)

    def test_runtime_loader_rejects_shared_standing_foliage_reference(self):
        data = {
            'format': self.baker.FORMAT_NAME,
            'version': self.baker.FORMAT_VERSION,
            'game_version': self.baker.GAME_VERSION,
            'map': '06_ensk', 'cell_size': 32.0,
            'instances': [[
                0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0,
                0.15, 1.0]],
            'cells': {},
            'fallen_trees': [
                [1, 1, -1.0, 0.0, -1.0, 1.0, 2.0, 1.0, 0],
                [1, 2, -1.0, 0.0, -1.0, 1.0, 2.0, 1.0, 0],
            ],
        }

        with self.assertRaisesRegex(ValueError, 'standing foliage'):
            prebaked_foliage._validate(data, '06_ensk')


if __name__ == '__main__':
    unittest.main()
