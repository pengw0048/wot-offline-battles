"""Original SPT lighting reader and atlas arithmetic oracles.

Fixture extraction is an audit of the pinned version's unique lighting block;
the production decoder requires the real SPT owner's structural stream cursor.
It does not guess tree types or allocate material handles from resource names.
"""
from pathlib import Path
import os
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class StockTreeMaterialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-tree-materials-')
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'probe'
        subprocess.run(['c++', '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror', '-UNDEBUG',
                        '-I'+str(ROOT / 'bigworld'), str(ROOT / 'bigworld/stock_tree_materials.cpp'),
                        str(ROOT / 'tests/test_stock_tree_materials.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_bounds_failures_and_actual_slot_handles(self):
        subprocess.run([str(self.binary)], check=True)

    def test_actual_spt_files_against_complete_native_reader_and_pixels(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original instruction oracle')
        try:
            import pefile
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
            from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI,
                                          UC_X86_REG_ECX, UC_X86_REG_XMM1, UC_X86_REG_XMM2,
                                          UC_X86_REG_MXCSR, UC_X86_REG_FPCW)
        except ImportError:
            self.skipTest('pefile and Unicorn are required for the original instruction oracle')
        client = Path(client)
        sections, inputs, prefix_ends = [], [], []
        widths = {8002:4,8003:52,8004:4,8005:52,8006:4,8007:4,8008:4,8009:52}
        for package in (client / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if not name.endswith('.spt'):
                        continue
                    data = archive.read(name)
                    self.assertEqual(data[:20], struct.pack('<II',1000,12)+b'__IdvSpt_02_')
                    candidates, start = [], 0
                    while True:
                        at = data.find(struct.pack('<3I',1012,1001,8000),start)
                        if at < 0:
                            break
                        start = at + 1
                        cursor, fields = at+12, set()
                        while cursor+4 <= len(data):
                            token = struct.unpack_from('<I',data,cursor)[0]
                            cursor += 4
                            if token == 8001:
                                if fields == set(widths):
                                    candidates.append((data[at+8:cursor],cursor))
                                break
                            if token not in widths or cursor+widths[token] > len(data):
                                break
                            fields.add(token)
                            cursor += widths[token]
                    self.assertEqual(len(candidates),1,name)
                    sections.append(candidates[0][0])
                    prefix_ends.append(candidates[0][1])
                    inputs.append(data)
        self.assertTrue(sections)
        actual_count = len(sections)
        # Exercise the original x87 load/store and CVTTSS2SI edge behaviour as
        # well as ordinary material colours. No clamping is implied by uint8.
        patterns = (0,0x80000000,0x3f000000,0x3f800000,0x3f99999a,0xbf800000,
                    0x4f000000,0xcf000000,0x7f800000,0xff800000,0x7fc01234,0x7f801234)
        for offset in range(len(patterns)):
            section = bytearray(struct.pack('<I',8000))
            for token in range(8002,8010):
                section.extend(struct.pack('<I',token))
                if token in (8003,8005,8009):
                    section.extend(struct.pack('<13I',*[patterns[(i+offset)%len(patterns)] for i in range(13)]))
                elif token == 8006:
                    section.extend(struct.pack('<I',patterns[offset]))
                else:
                    section.extend(struct.pack('<i',-2))
            section.extend(struct.pack('<I',8001))
            sections.append(bytes(section))
            inputs.append(bytes(section))
            prefix_ends.append(len(section))
        pe = pefile.PE(str(client / 'WorldOfTanks.exe'))
        self.assertEqual(pe.FILE_HEADER.Machine,0x14c)
        image = pe.get_memory_mapped_image()
        emulator = Uc(UC_ARCH_X86,UC_MODE_32)
        emulator.mem_map(pe.OPTIONAL_HEADER.ImageBase,(len(image)+4095)&~4095)
        emulator.mem_write(pe.OPTIONAL_HEADER.ImageBase,image)
        emulator.mem_map(0,4096)  # Original x86 SEH link at FS:0.
        emulator.mem_map(0x10000000,0x10000)
        emulator.mem_map(0x20000000,0x10000)
        emulator.mem_map(0x30000000,4096)
        frame, stream, record, data, pixels = 0x10008000,0x20000000,0x20000100,0x20001000,0x20002000
        expected = bytearray()
        for case, section in enumerate(sections):
            emulator.mem_write(data,section[4:])
            emulator.mem_write(stream,struct.pack('<II',0,data))
            emulator.mem_write(record,b'\xcc'*176)
            emulator.mem_write(frame,struct.pack('<II',0x30000000,stream))
            emulator.reg_write(UC_X86_REG_ESP,frame)
            emulator.reg_write(UC_X86_REG_ECX,record)
            emulator.reg_write(UC_X86_REG_FPCW,0x37f)
            emulator.reg_write(UC_X86_REG_MXCSR,0x1f80)
            # Full 11f2fe0, original integer/float readers, original SEH and
            # security-cookie path. No parser callbacks or instruction patches.
            emulator.emu_start(0x11f2fe0,0x30000000,count=100000)
            consumed = struct.unpack('<I',emulator.mem_read(stream,4))[0]+4
            self.assertEqual(consumed,len(section))
            decoded = bytes(emulator.mem_read(record,176))
            expected.extend(struct.pack('<I',prefix_ends[case])+decoded)
            emulator.mem_write(frame-0x420,struct.pack('<4I',record+4,record+124,record+60,record+60))
            emulator.reg_write(UC_X86_REG_EBP,frame)
            emulator.reg_write(UC_X86_REG_ESI,0)
            emulator.reg_write(UC_X86_REG_ECX,pixels+1)
            emulator.reg_write(UC_X86_REG_XMM1,struct.unpack('<I',struct.pack('<f',255.))[0])
            emulator.reg_write(UC_X86_REG_XMM2,struct.unpack_from('<I',decoded,112)[0])
            emulator.emu_start(0xb03a40,0xb03acd,count=1000)
            expected.extend(emulator.mem_read(pixels,32))
        input_file, outputs = self.directory / 'sections', self.directory / 'outputs'
        input_file.write_bytes(b''.join(struct.pack('<I',len(item))+item for item in inputs))
        subprocess.run([str(self.binary),str(input_file),str(outputs)],check=True)
        self.assertEqual(outputs.read_bytes(),expected)
        destination = os.environ.get('WOT_TREE_MATERIAL_FIXTURE_OUT')
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True,exist_ok=True)
            (destination / 'inputs.bin').write_bytes(input_file.read_bytes())
            (destination / 'expected.bin').write_bytes(expected)
        print('Original complete x86 lighting reader and pixel loop match %d actual SPT files and %d edge cases' %
              (actual_count,len(sections)-actual_count))


if __name__ == '__main__':
    unittest.main()
