"""Compile production BWAL/BWST readers and exercise native preload lease contracts."""
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


def load_patch(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ('bigworld/' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patch = load_patch('patch_stock_space_assets')
strings_patch = load_patch('patch_stock_space_strings')

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <utility>
#include <vector>
#define BW_BEGIN_NAMESPACE namespace BW {
#define BW_END_NAMESPACE }
#define COMPILED_SPACE_API
#define MF_ASSERT(x) assert(x)
#define PROFILER_SCOPED(x)
#define PROFILER_SCOPED_DYNAMIC_STRING(x)
#define ASSET_MSG(...) ((void)0)
namespace BW {
using uint32=std::uint32_t; using uint8=std::uint8_t;
struct FourCC { std::string value;explicit FourCC(const char* text):value(text){} };
template<class T> using vector=std::vector<T>;
class ReferenceCount { public: mutable unsigned references=0;virtual ~ReferenceCount(){} };
using SafeReferenceCount=ReferenceCount;
template<class T> class SmartPointer {
    T* pointer_=nullptr;
    void retain(){if(pointer_)++pointer_->references;}
    void release(){if(pointer_&&!--pointer_->references)delete pointer_;}
public:
    SmartPointer(T* p=nullptr):pointer_(p){retain();}
    SmartPointer(const SmartPointer& value):pointer_(value.pointer_){retain();}
    SmartPointer& operator=(const SmartPointer& value){if(this!=&value){release();pointer_=value.pointer_;retain();}return *this;}
    ~SmartPointer(){release();} T* get()const{return pointer_;}
    explicit operator bool()const{return pointer_!=nullptr;}
};
template<class T> class ExternalArray {
    T* data_=nullptr;size_t count_=0;
public:
    void reset(){data_=nullptr;count_=0;}bool valid()const{return data_!=nullptr;}
    size_t size()const{return count_;}bool empty()const{return count_==0;}
    void assignExternalStorage(T* p,size_t n,size_t){data_=p;count_=n;}
    T& operator[](size_t n){assert(n<count_);return data_[n];}
    const T& operator[](size_t n)const{assert(n<count_);return data_[n];}
};
struct StringRef {
    const char* data;size_t length;
    StringRef(const char* p,size_t n):data(p),length(n){assert(data);}
    std::string to_string()const{return std::string(data,length);}
};
struct Request {uint32 type;std::string resource;};
std::vector<Request> requests;
unsigned liveAssets=0;
struct NativeAsset:SafeReferenceCount {
    NativeAsset(){++liveAssets;}~NativeAsset(){--liveAssets;}
};
struct DataSection:NativeAsset{};
using DataSectionPtr=SmartPointer<DataSection>;
namespace BWResource {
DataSectionPtr openSection(const StringRef& resource){
    const auto text=resource.to_string();requests.push_back({1,text});
    return text.empty()||text.find("missing")!=std::string::npos?nullptr:new DataSection;
}}
namespace Moo {
struct BaseTexture:NativeAsset{};
using BaseTexturePtr=SmartPointer<BaseTexture>;
class TextureManager {
public:
    static TextureManager* instance(){static TextureManager value;return &value;}
    BaseTexturePtr get(const StringRef& resource,bool animation,bool mustExist,bool load,const char* allocator){
        assert(animation&&mustExist&&load&&std::string(allocator)=="Art/File Textures/");
        const auto text=resource.to_string();requests.push_back({2,text});
        return text.empty()||text.find("missing")!=std::string::npos?nullptr:new BaseTexture;
    }
};}
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
    Stream stream;uint32 version;unsigned opens=0,closes=0;
    BinaryFormat(std::vector<unsigned char>& data,uint32 version):stream(data),version(version){}
    Stream* findAndOpenSection(FourCC magic,uint32 requested,const char*){
        assert(magic.value=="BWAL");assert(requested==2);++opens;return version==requested?&stream:nullptr;
    }
    const char* resourceID()const{return "fixture";}
    void closeSection(Stream* p){assert(p==&stream);++closes;}
};
}}
@STRING_TYPES@
@ASSET_TYPES@
@STRING_HEADER@
@ASSET_HEADER@
@STRING_SOURCE@
@ASSET_SOURCE@
int main(int argc,char** argv){
    assert(argc==3);std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
    if(!input||!output)return 2;
    auto read=[&](std::uint32_t& value){return bool(input.read(reinterpret_cast<char*>(&value),4));};
    auto write=[&](std::uint32_t value){output.write(reinterpret_cast<const char*>(&value),4);};
    auto blob=[&](std::vector<unsigned char>& data){std::uint32_t bytes;if(!read(bytes)||bytes>16*1024*1024)return false;
        data.resize(bytes);return bool(input.read(reinterpret_cast<char*>(data.data()),bytes));};
    std::uint32_t cases;if(!read(cases))return 2;
    for(std::uint32_t n=0;n<cases;++n){
        std::vector<unsigned char> strings,assets;std::uint32_t version;
        if(!blob(strings)||!read(version)||!blob(assets))return 2;
        BW::requests.clear();assert(BW::liveAssets==0);
        BW::CompiledSpace::BinaryFormat::Stream stringStream(strings);
        BW::CompiledSpace::StringTable table;assert(table.readFromStream(&stringStream));
        BW::CompiledSpace::BinaryFormat reader(assets,version);
        BW::CompiledSpace::AssetList list;const bool valid=list.read(reader);write(valid);
        if(valid){
            write(static_cast<std::uint32_t>(list.size()));
            assert(list.percentLoaded()==(list.empty()?1.f:0.f));
            for(size_t i=0;i<list.size();++i){write(list.assetType(i));write(static_cast<std::uint32_t>(list.stringTableIndex(i)));}
            list.preloadAssets(table);assert(list.percentLoaded()==1.f);
            write(static_cast<std::uint32_t>(BW::requests.size()));
            for(const auto& request:BW::requests){write(request.type);write(static_cast<std::uint32_t>(request.resource.size()));output.write(request.resource.data(),request.resource.size());}
            write(BW::liveAssets);
            list.releasePreloadedAssets();assert(!BW::liveAssets);
            list.releasePreloadedAssets();assert(!BW::liveAssets);
            // Releasing preload leases leaves processing progress settled.
            assert(list.percentLoaded()==1.f);
            BW::requests.clear();list.preloadAssets(table);list.releasePreloadedAssets();assert(!BW::liveAssets);
        }
        list.close();list.close();assert(!list.isValid()&&list.percentLoaded()==0.f&&!BW::liveAssets);
        assert(reader.closes==(version==2?1u:0u));table.close();
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


def assets_bytes(rows):
    return struct.pack('<II', 8, len(rows)) + b''.join(struct.pack('<II', *row) for row in rows)


class StockSpaceAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        compiler = shutil.which('c++')
        if not archive.is_file() or not compiler:
            raise unittest.SkipTest('Pinned public engine archive and C++ compiler are required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-space-assets-')
        cls.root = Path(cls.temporary.name)
        directory = cls.root / 'lib/compiled_space'
        directory.mkdir(parents=True)
        names = ('string_table_types.hpp', 'asset_list_types.hpp', 'string_table.hpp',
                 'asset_list.hpp', 'string_table.cpp', 'asset_list.cpp')
        with tarfile.open(archive) as source:
            members = source.getnames()
            for name in names:
                member = next(n for n in members if n.endswith('/programming/bigworld/lib/compiled_space/' + name))
                (directory / name).write_bytes(source.extractfile(member).read())
        strings_patch.patch(cls.root)
        patch.patch(cls.root)
        before = {name: (directory / name).read_bytes() for name in names}
        patch.patch(cls.root)
        assert before == {name: (directory / name).read_bytes() for name in names}
        source = HARNESS
        markers = ('STRING_TYPES', 'ASSET_TYPES', 'STRING_HEADER', 'ASSET_HEADER', 'STRING_SOURCE', 'ASSET_SOURCE')
        for marker, name in zip(markers, names):
            source = source.replace('@' + marker + '@', without_includes((directory / name).read_text()))
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
        for strings, version, assets, valid, rows in cases:
            inputs += struct.pack('<I', len(strings)) + strings + struct.pack('<II', version, len(assets)) + assets
            expected += struct.pack('<I', valid)
            if not valid:
                continue
            expected += struct.pack('<I', len(rows))
            requests = []
            for kind, key in rows:
                expected += struct.pack('<II', kind, key)
                stride, count = struct.unpack_from('<II', strings)
                pool = 12 + stride * count
                lookup = {}
                for i in range(count):
                    k, offset, length = struct.unpack_from('<III', strings, 8 + i * stride)
                    lookup[k] = strings[pool + offset:pool + offset + length]
                if key and key in lookup and kind in (1, 2):
                    requests.append((kind, lookup[key]))
            expected += struct.pack('<I', len(requests))
            for kind, resource in requests:
                expected += struct.pack('<II', kind, len(resource)) + resource
            expected += struct.pack('<I', sum(bool(resource) and b'missing' not in resource for _, resource in requests))
        source, output = self.root / 'inputs.bin', self.root / 'outputs.bin'
        source.write_bytes(inputs)
        subprocess.run([str(self.binary), str(source), str(output)], check=True)
        self.assertEqual(output.read_bytes(), expected)
        destination = os.environ.get('WOT_SPACE_ASSETS_FIXTURE_OUT') if fixture else None
        if destination:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source, destination / 'probe.cpp')
            (destination / 'inputs.bin').write_bytes(inputs)
            (destination / 'expected.bin').write_bytes(expected)

    def test_resource_keys_types_empty_names_and_native_lease_lifetime(self):
        strings = table_bytes([(7, b'particles/effect.xml'), (99, b''),
            (0x80000001, b'system/sky.dds'), (0xfffffffe, b'missing.xml')])
        rows = [(1, 7), (2, 0x80000001), (6, 7), (5, 7), (3, 7), (4, 7),
                (0x102, 7), (2, 99), (1, 0xfffffffe), (2, 0), (1, 88)]
        self.run_cases([(strings, 2, assets_bytes(rows), True, rows),
                        (strings, 2, assets_bytes([]), True, [])])

    def test_malformed_arrays_fail_without_partial_entries_or_leases(self):
        strings = table_bytes([(7, b'good.xml')])
        assets = assets_bytes([(1, 7), (2, 7)])
        cases = [(strings, 1, assets, False, [])]
        cases.extend((strings, 2, assets[:n], False, []) for n in range(len(assets)))
        cases.append((strings, 2, assets + b'x', False, []))
        for offset, value in ((0, 1), (0, 4), (4, 0xffffffff)):
            data = bytearray(assets)
            struct.pack_into('<I', data, offset, value)
            cases.append((strings, 2, data, False, []))
        self.run_cases(cases)

    def test_all_original_catalogs_resolve_keys_and_preload_native_types(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for original map resources')
        sys.path.insert(0, str(REPOSITORY / 'tools'))
        from space_bin_0922 import CompiledSpace
        cases, count = [], 0
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if not name.endswith('/space.bin'):
                        continue
                    space = CompiledSpace(archive.read(name))
                    assets = space.section_data('BWAL')
                    stride, entries = struct.unpack_from('<II', assets)
                    self.assertEqual(stride, 8)
                    rows = [struct.unpack_from('<II', assets, 8 + i * stride) for i in range(entries)]
                    cases.append((space.section_data('BWST'), 2, assets, True, rows))
                    count += entries
        self.assertTrue(cases)
        self.run_cases(cases, fixture=True)
        print('Production AssetList read %d original BWAL catalogs and resolved %d FNV references' % (len(cases), count))


if __name__ == '__main__':
    unittest.main()
