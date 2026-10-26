"""Compile the patched production StringTable and read original BWST sections."""
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
REPOSITORY = ROOT.parents[1]
spec = importlib.util.spec_from_file_location('stock_space_strings_patch',
    ROOT / 'bigworld/patch_stock_space_strings.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <vector>
#define COMPILED_SPACE_API
#define MF_ASSERT(x) assert(x)
#define PROFILER_SCOPED(x)
#define ASSET_MSG(...) ((void)0)
namespace BW {
using uint32 = std::uint32_t;
struct FourCC { explicit FourCC(const char*) {} };
template<class T> class ExternalArray {
    T* data_ = nullptr; size_t count_ = 0;
public:
    void reset(){data_=nullptr;count_=0;}
    bool valid() const{return data_!=nullptr;}
    size_t size() const{return count_;}
    void assignExternalStorage(T* p,size_t n,size_t){data_=p;count_=n;}
    T& operator[](size_t n){assert(n<count_);return data_[n];}
    const T& operator[](size_t n) const{assert(n<count_);return data_[n];}
};
namespace CompiledSpace {
class BinaryFormat {
public:
    class Stream {
        std::vector<unsigned char>& data_;size_t cursor_=0;
    public:
        explicit Stream(std::vector<unsigned char>& data):data_(data){}
        size_t tell()const{return cursor_;}size_t size()const{return data_.size();}
        bool eof()const{return cursor_==data_.size();}
        void* readRaw(size_t n){if(n>data_.size()-cursor_)return nullptr;
            void* p=data_.data()+cursor_;cursor_+=n;return p;}
        bool read(uint32& n){void* p=readRaw(4);if(!p)return false;std::memcpy(&n,p,4);return true;}
    };
    Stream* findAndOpenSection(FourCC,uint32,const char*){return nullptr;}
    const char* resourceID()const{return "fixture";}
    void closeSection(Stream*){}
};
}}
@TYPES@
@HEADER@
@SOURCE@
int main(int argc,char** argv){
    assert(argc==3);std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
    if(!input||!output)return 2;
    auto read=[&](std::uint32_t& v){return bool(input.read(reinterpret_cast<char*>(&v),4));};
    auto write=[&](std::uint32_t v){output.write(reinterpret_cast<const char*>(&v),4);};
    std::uint32_t cases;if(!read(cases))return 2;
    for(std::uint32_t c=0;c<cases;++c){
        std::uint32_t bytes,count;if(!read(bytes)||bytes>16*1024*1024)return 2;
        std::vector<unsigned char> data(bytes);if(!input.read(reinterpret_cast<char*>(data.data()),bytes))return 2;
        BW::CompiledSpace::BinaryFormat::Stream stream(data);
        BW::CompiledSpace::StringTable table;const bool valid=table.readFromStream(&stream);write(valid);
        if(!read(count))return 2;
        for(std::uint32_t i=0;i<count;++i){
            std::uint32_t key;if(!read(key))return 2;
            const char* value=table.entry(key);const size_t length=table.entryLength(key);
            if(!value){assert(length==0);write(0xffffffffu);}
            else{write(static_cast<std::uint32_t>(length));output.write(value,length);}
        }
        table.close();assert(!table.isValid()&&!table.entry(1)&&table.entryLength(1)==0);
    }
    return input.peek()==std::char_traits<char>::eof()&&output?0:2;
}
'''


def without_includes(text):
    return '\n'.join(line for line in text.splitlines() if not line.startswith('#include'))


def table_bytes(rows):
    records, pool = bytearray(), bytearray()
    for key, value in rows:
        records += struct.pack('<III', key, len(pool), len(value))
        pool += value + b'\0'
    return struct.pack('<II', 12, len(rows)) + records + struct.pack('<I', len(pool)) + pool


class StockSpaceStringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        compiler = shutil.which('c++')
        if not archive.is_file() or not compiler:
            raise unittest.SkipTest('Pinned public engine archive and C++ compiler are required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-space-strings-')
        cls.root = Path(cls.temporary.name)
        directory = cls.root / 'lib/compiled_space'
        directory.mkdir(parents=True)
        names = ('string_table_types.hpp', 'string_table.hpp', 'string_table.cpp')
        with tarfile.open(archive) as source:
            for name in names:
                member = next(n for n in source.getnames() if n.endswith('/programming/bigworld/lib/compiled_space/' + name))
                (directory / name).write_bytes(source.extractfile(member).read())
        patch.patch(cls.root)
        before = {name: (directory / name).read_bytes() for name in names}
        patch.patch(cls.root)
        assert before == {name: (directory / name).read_bytes() for name in names}
        source = HARNESS
        for marker, name in zip(('@TYPES@', '@HEADER@', '@SOURCE@'), names):
            source = source.replace(marker, without_includes((directory / name).read_text()))
        cls.source = cls.root / 'probe.cpp'
        cls.source.write_text(source)
        cls.binary = cls.root / 'probe'
        flags = ['-std=c++14', '-Wall', '-Wextra', '-Werror', '-O1',
                 '-Wno-unused-const-variable', '-Wno-unused-local-typedefs']
        if os.name != 'nt':
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run([compiler] + flags + [str(cls.source), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_cases(self, cases, fixture=False):
        inputs = bytearray(struct.pack('<I', len(cases)))
        expected = bytearray()
        for data, valid, queries in cases:
            inputs += struct.pack('<I', len(data)) + data + struct.pack('<I', len(queries))
            expected += struct.pack('<I', valid)
            for key, value in queries:
                inputs += struct.pack('<I', key)
                expected += struct.pack('<I', 0xffffffff if value is None else len(value))
                if value is not None:
                    expected += value
        source, output = self.root / 'inputs.bin', self.root / 'outputs.bin'
        source.write_bytes(inputs)
        subprocess.run([str(self.binary), str(source), str(output)], check=True)
        self.assertEqual(output.read_bytes(), expected)
        destination = os.environ.get('WOT_SPACE_STRINGS_FIXTURE_OUT') if fixture else None
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source, destination / 'probe.cpp')
            (destination / 'inputs.bin').write_bytes(inputs)
            (destination / 'expected.bin').write_bytes(expected)

    def test_keys_sentinels_bounds_and_failed_load_ownership(self):
        rows = [(7, b'first'), (0x80000001, b'speedtree/\xe6\xa0\x91.dds'), (0xfffffffe, b'')]
        data = table_bytes(rows)
        queries = rows + [(0, None), (6, None), (8, None), (0xffffffff, None)]
        cases = [(data, True, queries), (table_bytes([]), True, [(1, None)])]
        cases.extend((data[:size], False, [(7, None)]) for size in range(len(data)))
        for offset, value in ((0, 8), (4, 0xffffffff), (12, 0xffffffff), (16, 0xffffffff), (20, 7)):
            bad = bytearray(data)
            struct.pack_into('<I', bad, offset, value)
            cases.append((bad, False, [(7, None)]))
        cases.append((data + b'x', False, [(7, None)]))
        bad = bytearray(data)
        bad[-1] = 1
        cases.append((bad, False, [(7, None)]))
        self.run_cases(cases)

    def test_all_original_tables_and_resource_references(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for original map resources')
        sys.path.insert(0, str(REPOSITORY / 'tools'))
        from space_bin_0922 import CompiledSpace
        cases, entries = [], 0
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if not name.endswith('/space.bin'):
                        continue
                    data = CompiledSpace(archive.read(name)).section_data('BWST')
                    stride, count = struct.unpack_from('<II', data)
                    self.assertEqual(stride, 12)
                    pool = 12 + count * 12
                    rows = []
                    for index in range(count):
                        key, offset, length = struct.unpack_from('<III', data, 8 + index * 12)
                        rows.append((key, data[pool + offset:pool + offset + length]))
                    cases.append((data, True, rows + [(0, None)]))
                    entries += count
        self.assertTrue(cases)
        self.run_cases(cases, fixture=True)
        print('Production StringTable read %d original tables and resolved %d resource keys' % (len(cases), entries))


if __name__ == '__main__':
    unittest.main()
