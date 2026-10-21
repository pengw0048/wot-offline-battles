"""Verify authored height tiles with the independent pinned terrain PNG reader."""

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import map_terrain_source as exporter

LEGACY_PATH = (ROOT / 'tools' / 'vendor' / 'navigation_baseline_f5b0173' /
               'tools' / 'bake_navigation.py')
SPEC = importlib.util.spec_from_file_location('map_source_legacy_png_reader', LEGACY_PATH)
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)


def source(chunks=(2, 2)):
    width, height = (chunks[0] * 32 + 5, chunks[1] * 32 + 5)
    return {
        'format': 'wot-map-source', 'version': 1,
        'terrain': {
            'origin': [-100, -200], 'chunks': list(chunks),
            'intervals_per_chunk': 32, 'border': 2,
            'heights_mm': [[-100000 + z * 3125 + x * 1000 for x in range(width)]
                           for z in range(height)],
        },
    }


def read_heights(path):
    width, height, rows = legacy.decode_png_rgba(path.read_bytes())
    return width, height, [list(struct.unpack('<%di' % width, row)) for row in rows]


class MapTerrainSourceTests(unittest.TestCase):
    def test_tiles_preserve_signed_millimetres_and_shared_borders(self):
        authored = source()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tiles'
            manifest = exporter.export_source(authored, output)
            self.assertFalse(manifest['game_loadable'])
            self.assertEqual('authoring-only', manifest['status'])
            self.assertEqual(4, len(manifest['tiles']))
            self.assertEqual([-100, -200], manifest['tiles'][0]['world_origin'])
            self.assertEqual([0, -100], manifest['tiles'][3]['world_origin'])
            tiles = {}
            for tile in manifest['tiles']:
                x, z = tile['tile_index']
                width, height, rows = read_heights(output / tile['file'])
                self.assertEqual((37, 37), (width, height))
                expected = [row[x * 32:x * 32 + 37]
                            for row in authored['terrain']['heights_mm'][z * 32:z * 32 + 37]]
                self.assertEqual(expected, rows)
                self.assertEqual(min(map(min, rows)), tile['minimum_mm'])
                self.assertEqual(max(map(max, rows)), tile['maximum_mm'])
                tiles[x, z] = rows
            self.assertEqual([row[-5:] for row in tiles[0, 0]],
                             [row[:5] for row in tiles[1, 0]])
            self.assertEqual(tiles[0, 0][-5:], tiles[0, 1][:5])
            self.assertLess(tiles[0, 0][0][0], 0)
            self.assertGreater(tiles[1, 1][-1][-1], 32767)
            self.assertEqual({'.json', '.png'}, {p.suffix for p in output.iterdir()})
            self.assertEqual(manifest, json.loads((output / 'manifest.json').read_text()))

    def test_int32_extremes_and_png_crc_are_preserved(self):
        authored = source((1, 1))
        authored['terrain']['heights_mm'][0][:2] = [-(2 ** 31), 2 ** 31 - 1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            exporter.export_source(authored, output)
            path = output / 'height_0_0.png'
            self.assertEqual([-(2 ** 31), 2 ** 31 - 1], read_heights(path)[2][0][:2])
            png = path.read_bytes()
            offset = 8
            while offset < len(png):
                length = struct.unpack_from('>I', png, offset)[0]
                payload = png[offset + 4:offset + 8 + length]
                expected_crc = struct.unpack_from('>I', png, offset + 8 + length)[0]
                self.assertEqual(zlib.crc32(payload) & 0xffffffff, expected_crc)
                offset += length + 12
            self.assertEqual(len(png), offset)

    def test_invalid_source_is_rejected_before_output_creation(self):
        cases = []
        for key, value in [('origin', [float('nan'), 0]), ('origin', [0, float('inf')]),
                           ('origin', [True, 0]), ('origin', [10 ** 400, 0]),
                           ('chunks', [0, 1]), ('chunks', [1, 1.0]),
                           ('chunks', [True, 1]), ('border', 3),
                           ('intervals_per_chunk', 64), ('heights_mm', []),
                           ('heights_mm', [[0]])]:
            invalid = source((1, 1))
            invalid['terrain'][key] = value
            cases.append(invalid)
        for value in (True, 1.0, float('nan'), float('inf'), -(2 ** 31) - 1,
                      2 ** 31, None, '5000'):
            invalid = source((1, 1))
            invalid['terrain']['heights_mm'][0][0] = value
            cases.append(invalid)
        for key, value in [('format', 'map'), ('version', 2), ('version', True),
                           ('terrain', [])]:
            invalid = source((1, 1))
            invalid[key] = value
            cases.append(invalid)
        cases.extend([None, [], {}])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tiles'
            for index, invalid in enumerate(cases):
                with self.subTest(case=index), self.assertRaises(ValueError):
                    exporter.export_source(invalid, output)
                self.assertFalse(output.exists())

    def test_output_refuses_existing_files_nonempty_directories_and_symlinks(self):
        authored = source((1, 1))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            occupied = root / 'occupied'
            occupied.mkdir()
            existing = occupied / 'keep.txt'
            existing.write_text('keep me')
            link = root / 'link'
            link.symlink_to(occupied, target_is_directory=True)
            for output in (occupied, existing, link):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    exporter.export_source(authored, output)
            self.assertEqual('keep me', existing.read_text())
            self.assertEqual([existing], list(occupied.iterdir()))

    def test_cli_converts_json_and_returns_actionable_bad_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / 'source.json'
            input_path.write_text(json.dumps(source((1, 1))))
            command = [sys.executable, str(ROOT / 'tools' / 'map_terrain_source.py'),
                       str(input_path), '--output', str(root / 'tiles')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('not a loadable game map', result.stdout)
            input_path.write_text('{bad json')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('error:', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
