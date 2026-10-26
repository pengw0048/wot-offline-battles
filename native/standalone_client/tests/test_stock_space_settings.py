"""Exercise the patched native BWCS reader with its aligned runtime bounds."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('space_settings_patch', ROOT / 'bigworld/patch_stock_space_settings.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <vector>
#define MF_ASSERT(x) assert(x)
#define ASSET_MSG(...) ((void)0)
namespace BW {
using uint32=std::uint32_t;
struct Vector3 { float x,y,z; Vector3(float a=0,float b=0,float c=0):x(a),y(b),z(c){} };
struct alignas(16) AABB {
    Vector3 minimum,maximum;
    AABB(Vector3 a={},Vector3 b={}):minimum(a),maximum(b){}
};
static_assert(sizeof(AABB)==32,"The native AABB is not its 24-byte wire payload");
namespace CompiledSpace {
namespace CompiledSpaceSettingsTypes { const int FORMAT_MAGIC=1,FORMAT_VERSION=1;struct Header{AABB spaceBounds_;}; }
class BinaryFormat {
public:
    unsigned opens=0,closes=0;
    class Stream {
        const std::vector<unsigned char>& bytes_;size_t cursor_=0;
    public:
        explicit Stream(const std::vector<unsigned char>& b):bytes_(b){}
        template<class T> bool read(T& value){if(sizeof(T)>bytes_.size()-cursor_)return false;
            std::memcpy(&value,bytes_.data()+cursor_,sizeof(T));cursor_+=sizeof(T);return true;}
        bool eof()const{return cursor_==bytes_.size();}
    } stream;
    explicit BinaryFormat(const std::vector<unsigned char>& bytes):stream(bytes){}
    Stream* findAndOpenSection(int,int,const char*){++opens;return &stream;}
    void closeSection(Stream* p){assert(p==&stream);++closes;}
    const char* resourceID()const{return "fixture";}
};
class CompiledSpaceSettings {
    BinaryFormat* pReader_;BinaryFormat::Stream* pStream_;
    CompiledSpaceSettingsTypes::Header data_;
public:
    CompiledSpaceSettings();~CompiledSpaceSettings();
    bool read(BinaryFormat&);void close();const AABB& boundingBox()const;
};
}}
@SOURCE@
int main(int argc,char**argv){
    assert(argc==3);std::ifstream in(argv[1],std::ios::binary);std::ofstream out(argv[2],std::ios::binary);
    assert(in&&out);std::uint32_t count;assert(in.read(reinterpret_cast<char*>(&count),4));
    for(std::uint32_t i=0;i<count;++i){
        std::uint32_t size;assert(in.read(reinterpret_cast<char*>(&size),4));
        std::vector<unsigned char> bytes(size);assert(in.read(reinterpret_cast<char*>(bytes.data()),size));
        BW::CompiledSpace::BinaryFormat reader(bytes);
        { BW::CompiledSpace::CompiledSpaceSettings settings;
          const std::uint32_t valid=settings.read(reader);out.write(reinterpret_cast<const char*>(&valid),4);
          if(valid){const auto& box=settings.boundingBox();out.write(reinterpret_cast<const char*>(&box.minimum),12);
              out.write(reinterpret_cast<const char*>(&box.maximum),12);}
          assert(reader.opens==1&&reader.closes==1);settings.close();assert(reader.closes==1); }
        assert(reader.closes==1);
    }
    assert(in.peek()==std::char_traits<char>::eof()&&out);
}
'''


class SpaceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists() or not shutil.which('c++'):
            raise unittest.SkipTest('The pinned public engine archive and C++ compiler are required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-space-settings-')
        cls.root = Path(cls.temporary.name)
        path = cls.root / 'lib/compiled_space/compiled_space_settings.cpp'
        path.parent.mkdir(parents=True)
        with tarfile.open(archive) as source:
            member = next(p for p in source if p.name.endswith('/programming/bigworld/lib/compiled_space/compiled_space_settings.cpp'))
            path.write_bytes(source.extractfile(member).read())
        patch.patch(cls.root)
        patched = path.read_text()
        patch.patch(cls.root)
        assert patched == path.read_text()
        source = '\n'.join(line for line in patched.splitlines() if not line.startswith('#include'))
        cls.source = cls.root / 'probe.cpp'
        cls.source.write_text(HARNESS.replace('@SOURCE@', source))
        cls.binary = cls.root / 'probe'
        subprocess.run(['c++', '-std=c++14', '-Wall', '-Wextra', '-Werror', '-Wno-unused-local-typedefs',
                        '-fsanitize=address,undefined', str(cls.source), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def check_cases(self, cases, publish=False):
        data = struct.pack('<I', len(cases))
        expected = b''
        for raw, valid in cases:
            data += struct.pack('<I', len(raw)) + raw
            expected += struct.pack('<I', valid) + (raw[4:] if valid else b'')
        (self.root / 'inputs.bin').write_bytes(data)
        subprocess.run([str(self.binary), str(self.root / 'inputs.bin'), str(self.root / 'output.bin')], check=True)
        self.assertEqual((self.root / 'output.bin').read_bytes(), expected)
        if publish and os.environ.get('WOT_SPACE_SETTINGS_FIXTURE_OUT'):
            target = Path(os.environ['WOT_SPACE_SETTINGS_FIXTURE_OUT'])
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source, target / 'probe.cpp')
            (target / 'inputs.bin').write_bytes(data)
            (target / 'expected.bin').write_bytes(expected)

    def test_sizes_and_cleanup(self):
        raw = struct.pack('<I6f', 24, -1300, -18, -700, 290, 99, 1250)
        cases = [(raw, True)] + [(raw[:size], False) for size in range(len(raw))]
        cases += [(struct.pack('<I', size) + raw[4:], False) for size in (0, 20, 28, 32, 0xffffffff)]
        cases += [(raw + b'x', False)]
        self.check_cases(cases)

    def test_every_original_map(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for original resources')
        sys.path.insert(0, str(ROOT.parents[1] / 'tools'))
        from space_bin_0922 import CompiledSpace
        cases = []
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if name.endswith('/space.bin'):
                        space = CompiledSpace(archive.read(name))
                        self.assertEqual(space.sections['BWCS'].version, 1)
                        cases.append((space.section_data('BWCS'), True))
        self.assertTrue(cases)
        self.check_cases(cases, publish=True)
        print('Original native BWCS bounds: %d maps preserved exactly' % len(cases))


if __name__ == '__main__':
    unittest.main()
