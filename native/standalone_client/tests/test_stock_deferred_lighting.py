"""Check recovered SH packing against original instructions and source wiring.

These host tests do not establish native deferred rendering acceptance.
"""
from pathlib import Path
import importlib.util
import os
import random
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('patch_lighting', ROOT / 'bigworld/patch_stock_deferred_lighting.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
PUBLIC_FILES = ('lib/romp/enviro_minder.hpp', 'lib/romp/enviro_minder.cpp', 'lib/moo/deferred_lights_manager.cpp')

HOST = r'''
#include <iostream>
#define WOT_STOCK_DEFERRED_LIGHTING_PORTABLE
#define WOT_STOCK_DEFERRED_LIGHTING_CORE_ONLY
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
struct Vector3 {float v[3];float operator[](unsigned i)const{return v[i];}};
#include "@PRODUCTION@"
int main(){
    Vector3 source[9];float result[28];
    while(std::cin.read(reinterpret_cast<char*>(source),sizeof(source))){
        packStockDeferredSphericalHarmonics(source,result);
        std::cout.write(reinterpret_cast<const char*>(result),sizeof(result));
    }
    return std::cin.eof()&&std::cout?0:2;
}
'''


class StockDeferredLightingTests(unittest.TestCase):
    def test_production_producer_state_and_failures(self):
        with tempfile.TemporaryDirectory(prefix='stock-lighting-producers-') as temporary:
            binary = Path(temporary) / 'probe'
            subprocess.run(['c++', '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'tests/test_stock_deferred_lighting.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def fixture(self, root):
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Pristine public engine archive is required')
        with tarfile.open(archive) as source:
            names = source.getnames()
            for relative in PUBLIC_FILES:
                suffix = '/programming/bigworld/' + relative
                member = next(n for n in names if n.endswith(suffix))
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())

    def test_patcher_lifecycle_idempotence_and_failure_transaction(self):
        with tempfile.TemporaryDirectory(prefix='stock-lighting-patch-') as temporary:
            root = Path(temporary)
            self.fixture(root)
            PATCH.apply(root)
            baseline = {name: (root / name).read_text() for name in PUBLIC_FILES}
            PATCH.apply(root)
            self.assertEqual(baseline, {name: (root / name).read_text() for name in PUBLIC_FILES})
            cpp = root / 'lib/romp/enviro_minder.cpp'
            text = cpp.read_text().replace('    s_activatedEM_ = this;', '    s_activatedEM_ = this;\n    otherEnvironmentOwner();')
            text = text.replace('        loadTimeOfDay(pDS, loadFromExternal);',
                                '        loadTimeOfDay(pDS, loadFromExternal);\n        loadOtherEnvironmentOwner();')
            text = text.replace('void EnviroMinder::loadHDR(', 'void unrelatedEnvironmentReader() {}\n\nvoid EnviroMinder::loadHDR(')
            cpp.write_text(text)
            PATCH.apply(root)
            self.assertEqual(cpp.read_text(), text)
            self.assertEqual(text.count('void EnviroMinder::loadStockDeferredLighting('), 1)
            self.assertLess(text.index('    deactivateStockDeferredLightingSettings();'), text.index('    s_activatedEM_ = NULL;'))
            self.assertIn('loadStockDeferredLightingSettings(source, stockDeferredLightingSettings_);', text)
            self.assertIn('if (s_activatedEM_ == this)', text)
            managed = (root / 'lib/moo/deferred_lights_manager.cpp').read_text()
            self.assertEqual(managed.count('if (!initialiseStockDeferredLightingConstants())'), 1)
            self.assertEqual(managed.count('    finaliseStockDeferredLightingConstants();'), 1)
        with tempfile.TemporaryDirectory(prefix='stock-lighting-failure-') as temporary:
            root = Path(temporary)
            self.fixture(root)
            broken = root / PUBLIC_FILES[-1]
            broken.write_text(broken.read_text().replace('void LightsManager::createManagedObjects()', 'void missingManagedOwner()'))
            before = {name: (root / name).read_bytes() for name in PUBLIC_FILES}
            with self.assertRaises(RuntimeError):
                PATCH.apply(root)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in PUBLIC_FILES})
            self.assertFalse((root / 'lib/stock_deferred_lighting.hpp').exists())

    def test_original_sh_instructions_and_pinned_resources(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for the original instruction oracle')
        try:
            import pefile
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
            from unicorn.x86_const import UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_MXCSR
        except ImportError:
            self.skipTest('pefile and Unicorn are required for the original instruction oracle')
        from tools.packed_xml import read_packed_xml, TYPE_VECTOR
        packages = Path(client) / 'res/packages'
        rows, resources = [], 0
        for package in packages.glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if name.endswith('/REM_SH.xml'):
                        fields = dict(read_packed_xml(archive.read(name)).children)
                        coefficients = []
                        for i in range(9):
                            value = fields[('sh%d' % i).encode()]
                            self.assertEqual((value.value_type, len(value.value)), (TYPE_VECTOR, 12))
                            coefficients.extend(struct.unpack('<3f', value.value))
                        rows.append(coefficients)
                        resources += 1
        self.assertGreater(resources, 0)
        with zipfile.ZipFile(packages / 'misc.pkg') as archive:
            texture = archive.read('system/maps/env_brdf_lut.dds')
            self.assertEqual(texture[:4], b'DDS ')
            self.assertEqual(struct.unpack_from('<2I', texture, 12), (128, 128))
            self.assertEqual(struct.unpack_from('<I', texture, 84)[0], 113)  # A16B16G16R16F
        rows.extend([[0.0]*27, [float(i)-13 for i in range(27)]])
        for component in range(27):
            row = [0.0]*27
            row[component] = 1.0
            rows.append(row)
        generator = random.Random(1513)
        rows.extend([[generator.uniform(-10, 10) for _ in range(27)] for _ in range(1024)])
        pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
        self.assertEqual(pe.FILE_HEADER.Machine, 0x14c)
        image = pe.get_memory_mapped_image()
        emulator = Uc(UC_ARCH_X86, UC_MODE_32)
        emulator.mem_map(pe.OPTIONAL_HEADER.ImageBase, (len(image)+4095)&~4095)
        emulator.mem_write(pe.OPTIONAL_HEADER.ImageBase, image)
        frame, owner = 0x10008000, 0x20000000
        emulator.mem_map(frame-0x8000, 0x10000)
        emulator.mem_map(owner, 0x10000)
        expected = bytearray()
        inputs = bytearray()
        # Exact stack slots written by the original DataSection readVector3 calls.
        offsets = (0x68, 0x74, 0x80, 0x38, 0x48, 0x8c, 0x28, 0x58, 0x98)
        for row in rows:
            encoded = struct.pack('<27f', *row)
            inputs.extend(encoded)
            for i, offset in enumerate(offsets):
                emulator.mem_write(frame-offset, encoded[i*12:(i+1)*12])
            emulator.reg_write(UC_X86_REG_EBP, frame)
            emulator.reg_write(UC_X86_REG_ESI, owner)
            emulator.reg_write(UC_X86_REG_MXCSR, 0x1f80)
            # Execute the unmodified native arithmetic/copy region, with no
            # external callbacks, ending before DataSection cleanup.
            emulator.emu_start(0xa750cb, 0xa75258, count=1000)
            expected.extend(emulator.mem_read(owner+0x5c, 112))
        with tempfile.TemporaryDirectory(prefix='stock-lighting-sh-') as temporary:
            root = Path(temporary)
            source = root / 'probe.cpp'
            source.write_text(HOST.replace('@PRODUCTION@', str(ROOT / 'bigworld/stock_deferred_lighting.cpp')))
            binary = root / 'probe'
            subprocess.run(['c++', '-std=c++14', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-ffp-contract=off', str(source), '-o', str(binary)], check=True)
            actual = subprocess.check_output([str(binary)], input=bytes(inputs))
            self.assertEqual(actual, expected)
        print('Original #1513 SH packing matches %d cases including %d actual resource files' % (len(rows), resources))


if __name__ == '__main__':
    unittest.main()
