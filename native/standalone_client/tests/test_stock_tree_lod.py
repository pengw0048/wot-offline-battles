"""Original old-tree LOD arithmetic, with the imported CRT pow kept explicit.

This compares original x86 instructions with the portable owner. The PE imports
pow from UCRT: the emulation resolves that one import to host double math.pow.
It is not a claim that the complete SpeedTree SDK or Windows renderer ran here.
"""
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class StockTreeLodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-tree-lod-')
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'probe'
        subprocess.run(['c++', '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I'+str(ROOT / 'bigworld'),
                        str(ROOT / 'bigworld/stock_tree_lod.cpp'),
                        str(ROOT / 'bigworld/stock_tree_materials.cpp'),
                        str(ROOT / 'tests/test_stock_tree_lod.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_failures_and_part_boundaries(self):
        subprocess.run([str(self.binary)], check=True)

    def test_every_original_spt_against_complete_original_reader(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original SPT resources')
        from native.standalone_client.tests.stock_tree_lod_oracle import original_spt_states
        resources = {}
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if name.endswith(('.spt', '.ctree')):
                        resources[name] = archive.read(name)
        names = [name for name in resources if name.endswith('.spt')]
        self.assertTrue(names)
        files = [resources[name] for name in names]
        inputs, outputs = self.directory / 'spt-inputs', self.directory / 'spt-states'
        source = b''.join(struct.pack('<I', len(data))+data for data in files)
        inputs.write_bytes(source)
        subprocess.run([str(self.binary), str(inputs), str(outputs), '--decode'], check=True)
        expected, imports = original_spt_states(Path(client) / 'WorldOfTanks.exe', files)
        self.assertEqual(outputs.read_bytes(), expected)
        # Cache parts with no vertices are absent draw inputs. Their original
        # source LOD count is still retained, rather than overwritten with zero.
        for number, name in enumerate(names):
            cached = resources[name[:-4]+'.ctree']
            at = 36
            state_counts = struct.unpack_from('<3I', expected, number*32+20)
            for part, stride in enumerate((52, 52, 88, 68)):
                vertices = struct.unpack_from('<I', cached, at)[0]
                at += 4+vertices*stride
                lods = struct.unpack_from('<I', cached, at)[0]
                at += 4
                if vertices and part < 3:
                    self.assertEqual(lods, state_counts[part], (name, part))
                for unused in range(lods):
                    indices = struct.unpack_from('<I', cached, at)[0]
                    at += 4+indices*4
                for unused in range(2):
                    length = struct.unpack_from('<I', cached, at)[0]
                    at += 4+length
                self.assertLessEqual(at, len(cached))
            self.assertEqual(at, len(cached))
        destination = os.environ.get('WOT_TREE_LOD_FIXTURE_OUT')
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'spt-inputs.bin').write_bytes(source)
            (destination / 'spt-expected.bin').write_bytes(expected)
        print('All %d complete SPT configurations match original constructor/reader instructions; '
              'CRT bridges=%s' % (len(files), imports))

    def test_original_selection_instructions_and_crt_pow_boundary(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original instruction oracle')
        import pefile
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
        from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_ECX,
                                       UC_X86_REG_MXCSR, UC_X86_REG_FPCW)
        pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
        self.assertEqual(pe.FILE_HEADER.Machine, 0x14c)
        image = pe.get_memory_mapped_image()
        emulator = Uc(UC_ARCH_X86, UC_MODE_32)
        emulator.mem_map(pe.OPTIONAL_HEADER.ImageBase, (len(image)+4095)&~4095)
        emulator.mem_write(pe.OPTIONAL_HEADER.ImageBase, image)
        emulator.mem_map(0x10000000, 0x10000)
        emulator.mem_map(0x20000000, 0x10000)
        emulator.mem_map(0x30000000, 0x2000)
        stack, state, output, stop, power = 0x10008000, 0x20000000, 0x20001000, 0x30000000, 0x30001000
        emulator.mem_write(0x1491d60, struct.pack('<I', power))
        # Return a real binary64 result through the original x87 ABI.
        emulator.mem_write(power, b'\xdd\x05'+struct.pack('<I', power+256)+b'\xc3')
        calls = []

        def crt_pow(uc, address, size, data):
            at = uc.reg_read(UC_X86_REG_ESP)
            base, exponent = struct.unpack('<dd', uc.mem_read(at+4, 16))
            calls.append((base, exponent))
            uc.mem_write(power+256, struct.pack('<d', math.pow(base, exponent)))

        emulator.hook_add(UC_HOOK_CODE, crt_pow, begin=power, end=power)
        inputs, expected = bytearray(), bytearray()
        f32 = lambda v: struct.unpack('<f', struct.pack('<f', v))[0]
        # Varied actual SPT parameter domains plus endpoints and each adjacent
        # representable float at the billboard and leaf-transition boundaries.
        for method in (0, 1, 3):
            for count in (1, 2, 3, 5, 6):
                for transition in (.07, .1, .125, 1/6):
                    for exponent in (.7, 1., 1.4, 1.6):
                        for billboard in (0., .15, .2, .225, .25, .3, .325, .5, .1475):
                            for drop in (0, 1):
                                transition, exponent = f32(transition), f32(exponent)
                                values = {0., 1., .0001, .01, .05, .1, .2, .3, .49, .5, .7, .9, .9999}
                                for boundary in (transition, f32(transition*2), f32(f32(transition*2)*f32(1-billboard))):
                                    bits = struct.unpack('<I', struct.pack('<f', boundary))[0]
                                    values.update(struct.unpack('<f', struct.pack('<I', bits+d))[0] for d in (-1, 0, 1))
                                for level in sorted(values):
                                    level = f32(level)
                                    native = bytearray(0x100)
                                    struct.pack_into('<Iff', native, 0x20, method, transition, exponent)
                                    struct.pack_into('<f', native, 0x30, billboard)
                                    struct.pack_into('<f', native, 0x4c, 84.)
                                    struct.pack_into('<3I', native, 0xc4, count, count, count)
                                    overlap = f32(transition*2)
                                    interval = count-1
                                    plateau = f32(f32(1-f32(f32(interval*transition)*2))/interval) if interval else 1.
                                    billboard_plateau = f32(f32(1-f32(count*overlap))/interval) if interval else 1.
                                    struct.pack_into('<5f', native, 0xd0, overlap, plateau, billboard_plateau,
                                                     f32(overlap+plateau), f32(overlap+billboard_plateau))
                                    emulator.mem_write(state, bytes(native))
                                    emulator.mem_write(output, struct.pack('<ifif2i3f', -1, -1., -1, -1., -1, -1, -1., -1., 0.))
                                    emulator.mem_write(0x2157171, bytes([drop]))
                                    emulator.mem_write(stack, struct.pack('<IIf', stop, output, level))
                                    emulator.reg_write(UC_X86_REG_ESP, stack)
                                    emulator.reg_write(UC_X86_REG_ECX, state)
                                    emulator.reg_write(UC_X86_REG_FPCW, 0x37f)
                                    emulator.reg_write(UC_X86_REG_MXCSR, 0x1f80)
                                    emulator.emu_start(0x11da360, stop, count=10000)
                                    expected.extend(emulator.mem_read(output, 36))
                                    inputs.extend(struct.pack('<i4f3ifI', method, transition, exponent, billboard,
                                                              84., count, count, count, level, drop))
        source, actual = self.directory / 'input', self.directory / 'output'
        source.write_bytes(inputs)
        subprocess.run([str(self.binary), str(source), str(actual)], check=True)
        data = actual.read_bytes()
        self.assertEqual(len(data), len(expected))
        for case in range(len(data)//36):
            self.assertEqual(data[case*36:(case+1)*36], expected[case*36:(case+1)*36],
                             'LOD case %d input=%s' % (case, inputs[case*40:(case+1)*40].hex()))
        self.assertTrue(calls)
        destination = os.environ.get('WOT_TREE_LOD_FIXTURE_OUT')
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'selection-inputs.bin').write_bytes(inputs)
            (destination / 'selection-expected.bin').write_bytes(expected)
        print('Original x86 LOD selector matches %d cases; %d explicit host CRT-pow imports' %
              (len(data)//36, len(calls)))


if __name__ == '__main__':
    unittest.main()
