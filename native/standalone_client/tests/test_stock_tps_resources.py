"""Exercise the production tPS reader with every #1513 PFX and malformed inputs."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def word(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def canonical(data):
    output = bytearray(struct.pack('<I', 1) + data[16:80] + data[12:16])
    cursor = 80
    for unused in range(word(data, 12)):
        assert word(data, cursor) == 1003 and word(data, cursor + 8) == 2
        effect_start = cursor
        count = word(data, cursor + 12)
        assert word(data, cursor + 16) == 140
        output.extend(data[cursor + 20:cursor + 160])
        output.extend(struct.pack('<I', count))
        cursor += 160
        for unused_emitter in range(count):
            emitter_start = cursor
            assert word(data, cursor) == 1001 and word(data, cursor + 8) == 1508
            output.extend(data[cursor + 12:cursor + 1520])
            cursor += 1520
            for components in (1, 1, 1, 1, 1, 1, 4, 1):
                keys = word(data, cursor)
                assert keys <= 2048 and word(data, cursor + 4) == 0x50505050
                output.extend(struct.pack('<II', components, keys))
                length = keys * (components + 1) * 4
                output.extend(data[cursor + 8:cursor + 8 + length])
                cursor += length + 8
            assert cursor - emitter_start == word(data, emitter_start + 4)
        assert cursor - effect_start == word(data, effect_start + 4)
    assert cursor == len(data) == word(data, 4)
    return output


def write_fixture(client, inputs, expected):
    resources = 0
    sample = None
    with zipfile.ZipFile(client / 'res/packages/particles.pkg') as archive:
        for name in archive.namelist():
            if not (name.endswith('.bin') and
                    ('content_forward/PFX/' in name or 'content_deferred/PFX/' in name)):
                continue
            data = archive.read(name)
            assert word(data, 0) == 1004
            inputs.write(struct.pack('<I', len(data)) + data)
            expected.write(canonical(data))
            resources += 1
            sample = sample or data
    assert sample is not None
    mutations = [b'', sample[:79], sample[:-1], sample + b'\0']
    for offset, value in ((0, 1003), (4, len(sample) + 1), (12, 0xffffffff),
                          (80, 1001), (88, 1), (92, 0xffffffff), (96, 141)):
        data = bytearray(sample)
        struct.pack_into('<I', data, offset, value)
        mutations.append(data)
    # Find a real emitter, then mutate its fixed size and first key curve.
    emitter = 80 + 20 + word(sample, 96)
    for offset, value in ((emitter + 8, 1509), (emitter + 4, 1),
                          (emitter + 1520, 2049), (emitter + 1524, 0)):
        data = bytearray(sample)
        struct.pack_into('<I', data, offset, value)
        mutations.append(data)
    for data in mutations:
        inputs.write(struct.pack('<I', len(data)) + data)
        expected.write(struct.pack('<I', 0))
    return resources, len(mutations)


class StockTPSResourceTests(unittest.TestCase):
    def test_original_resources_and_rejections(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for pinned resource tests')
        with tempfile.TemporaryDirectory(prefix='wot-tps-resources-') as directory:
            work = Path(directory)
            with (work / 'inputs.bin').open('wb') as inputs, (work / 'expected.bin').open('wb') as expected:
                resources, rejected = write_fixture(Path(client), inputs, expected)
            executable = work / 'probe'
            subprocess.run(['c++', '-std=c++17', '-DWOT_STOCK_TPS_RESOURCES_PORTABLE',
                            '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-g',
                            str(ROOT / 'bigworld/stock_tps_resources.cpp'),
                            str(ROOT / 'tests/stock_tps_resources_probe.cpp'), '-o', str(executable)], check=True)
            result = subprocess.run([str(executable), str(work / 'inputs.bin'), str(work / 'actual.bin')],
                                    capture_output=True, text=True, check=True)
            self.assertEqual((work / 'actual.bin').read_bytes(), (work / 'expected.bin').read_bytes())
            print('%s; malformed=%d; original=%d' % (result.stdout.strip(), rejected, resources))


if __name__ == '__main__':
    unittest.main()
