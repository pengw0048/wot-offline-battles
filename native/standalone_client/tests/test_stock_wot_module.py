"""CPython boundary checks; optional original x86 instruction oracle.

The host Matrix fixture records D3DX calls; it does not validate DirectX math.
"""
from pathlib import Path
import os
import random
import shlex
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class StockWotModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        python = Path(os.environ.get('WOT_TEST_PY27', '/Users/peng/.pyenv/versions/2.7.18/bin/python2.7'))
        config = python.with_name('python2.7-config')
        if not config.exists():
            raise unittest.SkipTest('CPython 2.7 development runtime is required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-wot-module-')
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'probe'
        flags = [f for f in shlex.split(subprocess.check_output([str(config), '--cflags'], text=True)) if f != '-DNDEBUG']
        libs = shlex.split(subprocess.check_output([str(config), '--ldflags'], text=True))
        subprocess.run(['c++', '-std=c++14', '-Wno-deprecated-register', '-UNDEBUG',
                        '-ffp-contract=off', str(ROOT / 'tests/stock_wot_module_host.cpp'),
                        *flags, *libs, '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_python_conversion_and_provider_ownership(self):
        environment = dict(os.environ)
        if environment.get('WOT_0922_CLIENT'):
            with zipfile.ZipFile(Path(environment['WOT_0922_CLIENT']) / 'res/packages/scripts.pkg') as archive:
                for name, variable in (('Avatar', 'STOCK_AVATAR_PYC'),
                                       ('vehicle_systems/model_assembler', 'STOCK_MODEL_ASSEMBLER_PYC')):
                    target = self.directory / (variable + '.pyc')
                    target.write_bytes(archive.read('scripts/client/' + name + '.pyc'))
                    environment[variable] = str(target)
        subprocess.run([str(self.binary)], env=environment, check=True)

    def test_original_x86_decoder(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original instruction oracle')
        try:
            import pefile
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
            from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_MXCSR
        except ImportError:
            self.skipTest('pefile and Unicorn are required for the original instruction oracle')
        pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
        self.assertEqual(pe.FILE_HEADER.Machine, 0x14c)
        image = pe.get_memory_mapped_image()
        emulator = Uc(UC_ARCH_X86, UC_MODE_32)
        emulator.mem_map(pe.OPTIONAL_HEADER.ImageBase, (len(image)+4095)&~4095)
        emulator.mem_write(pe.OPTIONAL_HEADER.ImageBase, image)
        stack, scratch, returned = 0x10000000, 0x20000000, 0x30000000
        for address in (stack, scratch, returned):
            emulator.mem_map(address, 0x10000)
        values = [0, (1 << 64)-1, 0xe7c53a65432abcde, 0x123456789abcdef0]
        for shift, width in ((0,15),(15,12),(27,13),(40,8),(48,8),(56,8)):
            for value in (0,1,2,(1 << (width-1))-1,1 << (width-1),(1 << width)-2,(1 << width)-1):
                values.append(value << shift)
        generator = random.Random(1513)
        values += [generator.getrandbits(64) for _ in range(4096)]
        expected = bytearray()
        for value in values:
            emulator.mem_write(scratch, struct.pack('<Q', value))
            emulator.mem_write(stack+0x8000, struct.pack('<8I', returned, scratch, *[scratch+8+i*4 for i in range(6)]))
            emulator.reg_write(UC_X86_REG_ESP, stack+0x8000)
            emulator.reg_write(UC_X86_REG_MXCSR, 0x1f80)
            # Run the original helper and its actual CRT uint64->double callee.
            emulator.emu_start(0xf5b920, returned, count=1000)
            expected += emulator.mem_read(scratch+8, 24)
        inputs, outputs = self.directory / 'inputs', self.directory / 'outputs'
        inputs.write_bytes(b''.join(struct.pack('<Q', value) for value in values))
        subprocess.run([str(self.binary), '--decode', str(inputs), str(outputs)], check=True)
        self.assertEqual(outputs.read_bytes(), expected)
        print('Original #1513 x86 decoder matches %d cases / %d float bits' % (len(values), len(values)*6))


if __name__ == '__main__':
    unittest.main()
