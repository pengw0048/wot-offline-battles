"""Read all pinned cached-tree resources with independent format validation.

Byte preservation and native core probes do not prove rendering, wind or LOD.
"""
from pathlib import Path
import math
import os
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def inspect(data):
    cursor = 0

    def word():
        nonlocal cursor
        if len(data)-cursor < 4:
            raise ValueError('Truncated cached-tree header')
        value = struct.unpack_from('<I',data,cursor)[0]
        cursor += 4
        return value

    if word() != 106:
        raise ValueError('Unexpected cached-tree version')
    header = struct.unpack_from('<8f',data,cursor)
    cursor += 32
    if not all(math.isfinite(value) for value in header) or any(header[i]>header[3+i] for i in range(3)):
        raise ValueError('Invalid cached-tree bounds/scalars')
    for stride in (52,52,88,68):
        vertices = word()
        if vertices > (len(data)-cursor)//stride:
            raise ValueError('Vertex count exceeds input')
        cursor += vertices*stride
        lods = word()
        if lods > (len(data)-cursor)//4:
            raise ValueError('LOD count exceeds input')
        for _ in range(lods):
            count = word()
            if count > (len(data)-cursor)//4:
                raise ValueError('Index count exceeds input')
            if any(value[0]>=vertices for value in struct.iter_unpack('<I',data[cursor:cursor+count*4])):
                raise ValueError('Index exceeds its vertex slot')
            cursor += count*4
        for _ in range(2):
            length = word()
            if length > len(data)-cursor:
                raise ValueError('Texture path exceeds input')
            path = data[cursor:cursor+length]
            path.decode('utf8')
            if b'\0' in path:
                raise ValueError('Texture path contains NUL')
            cursor += length
    if cursor != len(data):
        raise ValueError('Trailing cached-tree bytes')


class StockTreeGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-tree-geometry-')
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'probe'
        subprocess.run(['c++','-std=c++14','-O2','-Wall','-Wextra','-Werror','-UNDEBUG',
                        '-I'+str(ROOT / 'bigworld'),str(ROOT / 'bigworld/stock_tree_geometry.cpp'),
                        str(ROOT / 'tests/test_stock_tree_geometry.cpp'),'-o',str(cls.binary)],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_bounds_failures_ownership_and_unicode_paths(self):
        subprocess.run([str(self.binary)],check=True)

    def test_every_pinned_cached_tree_to_exact_eof(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for exact cached-tree resources')
        cases = []
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if name.endswith('.ctree'):
                        data = archive.read(name)
                        inspect(data)
                        cases.append(data)
        self.assertTrue(cases)
        source = b''.join(struct.pack('<I',len(data))+data for data in cases)
        inputs, outputs = self.directory / 'inputs.bin', self.directory / 'outputs.bin'
        inputs.write_bytes(source)
        subprocess.run([str(self.binary),str(inputs),str(outputs)],check=True)
        self.assertEqual(outputs.read_bytes(),source)
        destination = os.environ.get('WOT_TREE_GEOMETRY_FIXTURE_OUT')
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True,exist_ok=True)
            (destination / 'inputs.bin').write_bytes(source)
            (destination / 'expected.bin').write_bytes(source)
        print('All %d actual version-106 cached trees preserve every vertex/index/path byte to exact EOF' % len(cases))


if __name__ == '__main__':
    unittest.main()
