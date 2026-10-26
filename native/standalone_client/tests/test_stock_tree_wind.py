"""Original wind constructor/load/advance instruction audit at explicit CRT imports.

The SDK functions run unmodified. Imported math/allocation/scanning uses the host
services documented by OriginalTreeInstructionOracle. CRT rand receives an
explicit test input sequence; this is not a Windows runtime/rendering claim.
"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RANDOM = (0, 16384, 32767, 32767, 800, 12000, 20000, 30000, 0, 32767, 0)


def original_wind_frames(executable, ini, steps):
    from native.standalone_client.tests.stock_tree_lod_oracle import OriginalTreeInstructionOracle
    oracle = OriginalTreeInstructionOracle(executable)
    emulator = oracle.emulator
    wind = 0x20000200
    oracle.call(0x11dd7f0, ecx=wind)
    source = oracle.allocate(len(ini))
    emulator.mem_write(source, ini)
    oracle.call(0x11deb00, (source, len(ini)), ecx=wind)
    # Provide a serialized CRT thread environment, then invoke the original
    # dynamic initializers for its axis offset and static scratch matrix.
    tls, tls_array = oracle.allocate(0x4000), oracle.allocate(4)
    emulator.mem_write(tls_array, struct.pack('<I', tls))
    emulator.mem_write(0x2c, struct.pack('<I', tls_array))
    emulator.mem_write(tls+0x32f0, struct.pack('<I', 0x7fffffff))
    oracle.call(0x5e9cd0)
    oracle.call(0x11dfb00, ecx=0x21571d8)
    consumed = 0

    def random_input():
        nonlocal consumed
        value = RANDOM[consumed % len(RANDOM)]
        consumed += 1
        return value

    oracle.random_source = random_input
    word = lambda value: struct.unpack('<I', struct.pack('<f', value))[0]
    to_radians = struct.unpack('<f', struct.pack('<f', .017453292))[0]
    output = bytearray()
    for time, strength, x, y, z, reset in steps:
        if reset:
            oracle.call(0x11df2d0, ecx=wind)
        oracle.call(0x11df640, tuple(word(value) for value in (strength, x, y, z)), ecx=wind)
        oracle.call(0x11ddf70, (word(time), 1, 1, word(0.), word(0.), word(1.)), ecx=wind)
        owner = oracle.word(wind+4)
        output.extend(emulator.mem_read(oracle.word(owner+0x104), 6*64))
        rock = struct.unpack('<8f', emulator.mem_read(oracle.word(owner+0x5c), 32))
        rustle = struct.unpack('<8f', emulator.mem_read(oracle.word(owner+0x7c), 32))
        # b06100 publishes two binary32 degree-to-radian products per float4.
        for a, b in zip(rock, rustle):
            output.extend(struct.pack('<4f', a*to_radians, b*to_radians, 0., 0.))
        output.extend(struct.pack('<I', consumed))
    return bytes(output), oracle.import_counts


class StockTreeWindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-tree-wind-')
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'probe'
        subprocess.run(['c++', '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I'+str(ROOT / 'bigworld'), str(ROOT / 'bigworld/stock_tree_wind.cpp'),
                        str(ROOT / 'tests/test_stock_tree_wind.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_original_weather_changes_gust_reset_and_owner_failures(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for original SDK and wind input')
        with zipfile.ZipFile(Path(client) / 'res/packages/misc.pkg') as archive:
            ini = archive.read('system/data/speedwind.ini')
        source, basic = self.directory / 'speedwind.ini', self.directory / 'basic-output'
        source.write_bytes(ini)
        subprocess.run([str(self.binary), str(source), str(basic)], check=True)
        times = (0., .016, .032, .05, .1, 1., 2., 5., 6., 7., 8., 11., 15.)
        baseline = basic.read_bytes()
        initial_steps = [(time, .7, 1., 0., 2., 0) for time in times]
        initial_steps = list(struct.iter_unpack('<5fI', b''.join(
            struct.pack('<5fI', *step) for step in initial_steps)))
        # The original reader returns at its first complete mask, so later
        # duplicate, malformed or unknown lines cannot replace that state.
        for suffix in (b'MaxBendAngle 1\n', b'NumWindMatricesAndLeafAngles 5 8\n',
                       b'WindResponseAndLimit malformed\n', b'Unknown ignored\n'):
            changed = ini+b'\n'+suffix
            source.write_bytes(changed)
            subprocess.run([str(self.binary), str(source), str(basic)], check=True)
            self.assertEqual(basic.read_bytes(), baseline)
            original, unused = original_wind_frames(Path(client) / 'WorldOfTanks.exe', changed, initial_steps)
            self.assertEqual(baseline, b''.join(original[at:at+512] for at in range(0, len(original), 516)))
        source.write_bytes(ini)
        cases = ((.7, 1., 0., 2.), (0., 0., 0., 0.), (1., -2., 0., 1.),
                 (.3, 1., .5, -.75), (2., 0., 0., -1.), (-.5, 0., 1., 0.))
        steps = []
        for strength, x, y, z in cases:
            for number, time in enumerate(times):
                steps.append((time, strength, x, y, z, int(number == 0)))
        steps.extend((16.+i*.01, .1+i*.01, -1., 0., 1., 0) for i in range(20))
        # Serialize first: both readers consume the same binary32 clock/input.
        encoded = b''.join(struct.pack('<5fI', *step) for step in steps)
        steps = list(struct.iter_unpack('<5fI', encoded))
        expected, imports = original_wind_frames(Path(client) / 'WorldOfTanks.exe', ini, steps)
        inputs, output = self.directory / 'steps', self.directory / 'output'
        inputs.write_bytes(encoded)
        subprocess.run([str(self.binary), str(source), str(inputs), str(output)], check=True)
        actual = output.read_bytes()
        self.assertEqual(len(actual), len(expected))
        for frame in range(len(steps)):
            self.assertEqual(actual[frame*516:(frame+1)*516], expected[frame*516:(frame+1)*516],
                             'Wind frame %d input=%s' % (frame, steps[frame]))
        destination = os.environ.get('WOT_TREE_WIND_FIXTURE_OUT')
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'speedwind.ini').write_bytes(ini)
            (destination / 'steps.bin').write_bytes(encoded)
            (destination / 'expected.bin').write_bytes(expected)
        print('Original complete wind calls match %d frames/%d bytes including reset and CRT-rand consumption; '
              'CRT bridges=%s' % (len(steps), len(expected), imports))


if __name__ == '__main__':
    unittest.main()
