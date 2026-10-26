"""Exercise the production BWSG/BSGD reader at explicit resource/GPU boundaries."""
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

from native.standalone_client.tests.test_stock_space_assets import (
    HARNESS as ASSET_HARNESS, load_patch, without_includes, table_bytes)

ROOT = Path(__file__).resolve().parents[1]
patch = load_patch('patch_stock_space_geometry')
strings_patch = load_patch('patch_stock_space_strings')

HARNESS = ASSET_HARNESS.split('struct Request')[0] + r'''
using string=std::string;
using D3DPOOL=int;
const D3DPOOL D3DPOOL_MANAGED=1;
#define FAILED(value) ((value)<0)
unsigned fault=0,liveVertices=0;
namespace Moo {
std::vector<std::shared_ptr<std::vector<unsigned char>>> buffers;
struct VertexBuffer {
    std::shared_ptr<std::vector<unsigned char>> bytes;
    int create(uint32 size,int,int,D3DPOOL,const char*) {
        if(fault==1)return -1;
        bytes=std::make_shared<std::vector<unsigned char>>(size);buffers.push_back(bytes);return 0;
    }
    int lock(uint32,uint32,void** pointer,int){
        if(fault==2)return -1;*pointer=fault==8?nullptr:bytes->data();return 0;
    }
    int unlock(){return fault==3?-1:0;}
    void addToPreloadList(){}
};
struct RenderContext {static D3DPOOL patchD3DPool(D3DPOOL p){return p;}};
struct VertexFormat {
    std::string name_;uint32 index,stride;
    unsigned streamCount()const{return index+1;}
    uint32 streamStride(uint32 stream)const{return stream==index?stride:0;}
    const std::string& name()const{return name_;}
};
VertexFormat formats[]={ {"xyznuvtb",0,32},{"xyznuv",0,24},{"xyznuviiiwwtb",0,40},
    {"uv2",10,8},{"colour",11,4},{"instancing",9,64} };
struct InstancingStream{};
struct VertexFormatCache {
    static const VertexFormat* get(const StringRef& name){
        if(fault==4)return nullptr;
        for(const auto& format:formats)if(format.name_==name.to_string())return &format;
        return nullptr;
    }
    static const VertexFormat* getTarget(const VertexFormat& source,const StringRef&,bool){return &source;}
    template<class T> static StringRef getTargetName(const StringRef&,bool){return {"instancing",10};}
};
struct VertexDeclaration {
    const VertexFormat* value;
    const VertexFormat& format()const{return *value;}
    static const char* getTargetDevice(){return "DX9";}
    static VertexDeclaration* get(const StringRef& name){
        if(fault==5)return nullptr;
        static VertexDeclaration values[6];
        for(unsigned i=0;i<6;++i)if(formats[i].name_==name.to_string()){
            values[i].value=&formats[i];return &values[i];}
        return nullptr;
    }
    static VertexDeclaration* combine(VertexDeclaration* a,VertexDeclaration* b){return fault==7||!b?nullptr:a;}
};
struct VertexStreamDesc {
    uint32 streamIndex_;unsigned formatIndex;
    using StreamDescs=std::vector<VertexStreamDesc>;
    const VertexFormat* defaultVertexFormat()const{return fault==6?nullptr:&formats[formatIndex];}
    static const StreamDescs& getKnownStreams(){static StreamDescs value{{10,3},{11,4}};return value;}
};
class Vertices:public ReferenceCount {
public:
    std::string resource;uint32 count=0;
    struct Stream {uint32 index,offset,stride,size;VertexBuffer buffer;};
    std::vector<Stream> streams;
    Vertices(const std::string& value,int):resource(value){++liveVertices;}
    ~Vertices(){--liveVertices;}
    const std::string& resourceID()const{return resource;}
    void initFromValues(uint32 size,const char* format){assert(format&&*format);count=size;}
    void addStream(uint32 i,const VertexBuffer& b,uint32 o,uint32 stride,uint32 size){
        assert(b.bytes&&o+size<=b.bytes->size());streams.push_back({i,o,stride,size,b});}
};
using VerticesPtr=SmartPointer<Vertices>;
struct VerticesManager {
    std::vector<VerticesPtr> published;
    static VerticesManager* instance(){static VerticesManager value;return &value;}
    void populateResource(const std::string& resource,const VerticesPtr& value){assert(value.get()->resource==resource);published.push_back(value);}
};
}
namespace CompiledSpace {
class BinaryFormat {
public:
    class Stream {
        std::vector<unsigned char>& bytes_;size_t cursor_=0;
    public:
        explicit Stream(std::vector<unsigned char>& bytes):bytes_(bytes){}
        size_t tell()const{return cursor_;}size_t size()const{return bytes_.size();}
        bool eof()const{return cursor_==bytes_.size();}
        void* readRaw(size_t n){if(cursor_>bytes_.size()||n>bytes_.size()-cursor_)return nullptr;
            void* p=bytes_.data()+cursor_;cursor_+=n;return p;}
        bool read(uint32& value){void* p=readRaw(4);if(!p)return false;std::memcpy(&value,p,4);return true;}
    };
    Stream metadata,data;unsigned version,dataVersion,metaOpen=0,dataOpen=0,metaClose=0,dataClose=0;
    BinaryFormat(std::vector<unsigned char>& m,std::vector<unsigned char>& d,unsigned a,unsigned b):metadata(m),data(d),version(a),dataVersion(b){}
    Stream* findAndOpenSection(FourCC magic,uint32 requested,const char*) {
        assert(requested==2);
        if(magic.value=="BWSG"){if(version!=2)return nullptr;++metaOpen;return &metadata;}
        assert(magic.value=="BSGD");if(dataVersion!=2)return nullptr;++dataOpen;return &data;
    }
    void closeSection(Stream* stream){if(stream==&metadata)++metaClose;else{assert(stream==&data);++dataClose;}}
    const char* resourceID()const{return "fixture";}
};
}}
@STRING_TYPES@
@GEOMETRY_TYPES@
@STRING_HEADER@
@GEOMETRY_HEADER@
@STRING_SOURCE@
@GEOMETRY_SOURCE@
int main(int argc,char** argv){
    assert(argc==3);std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
    if(!input||!output)return 2;
    auto read=[&](std::uint32_t& value){return bool(input.read(reinterpret_cast<char*>(&value),4));};
    auto write=[&](std::uint32_t value){output.write(reinterpret_cast<char*>(&value),4);};
    auto blob=[&](std::vector<unsigned char>& data){std::uint32_t bytes;if(!read(bytes)||bytes>512*1024*1024)return false;
        data.resize(bytes);return bool(input.read(reinterpret_cast<char*>(data.data()),bytes));};
    std::uint32_t cases;if(!read(cases))return 2;
    for(std::uint32_t n=0;n<cases;++n){
        std::uint32_t version,dataVersion;if(!read(version)||!read(dataVersion)||!read(BW::fault))return 2;
        std::vector<unsigned char> metadata,data;if(!blob(metadata)||!blob(data))return 2;
        assert(!BW::liveVertices);BW::Moo::buffers.clear();
        BW::CompiledSpace::BinaryFormat reader(metadata,data,version,dataVersion);
        BW::CompiledSpace::StaticGeometry owner;bool valid=owner.read(reader);write(valid);
        auto& published=BW::Moo::VerticesManager::instance()->published;
        if(valid){
            assert(owner.isValid()&&owner.percentLoaded()==1.f);
            write(static_cast<std::uint32_t>(published.size()));
            size_t offset=0;
            for(const auto& buffer:BW::Moo::buffers){
                assert(offset+buffer->size()<=data.size());
                assert(std::memcmp(data.data()+offset,buffer->data(),buffer->size())==0);offset+=buffer->size();}
            assert(offset==data.size());
            for(const auto& value:published){
                const auto& resource=value.get()->resource;
                write(static_cast<std::uint32_t>(resource.size()));output.write(resource.data(),resource.size());
                write(value.get()->count);write(static_cast<std::uint32_t>(value.get()->streams.size()));
                for(const auto& stream:value.get()->streams){write(stream.index);write(stream.offset);write(stream.stride);write(stream.size);}
            }
            // A second load cannot republish while existing native leases exist.
            if(!published.empty())assert(!owner.loadGeometry());
        }else{assert(published.empty());assert(!BW::liveVertices);}
        owner.close();owner.close();assert(!owner.isValid()&&owner.percentLoaded()==0.f);
        assert(reader.metaOpen==reader.metaClose&&reader.dataOpen==reader.dataClose);
        // Other scene owners can retain the real resources after mapping teardown.
        for(const auto& value:published)assert(!value.get()->streams.empty());
        published.clear();assert(!BW::liveVertices);BW::Moo::buffers.clear();
    }
    return input.peek()==std::char_traits<char>::eof()&&output?0:2;
}
'''
# The production StringRef accepts C strings and BW strings implicitly.
HARNESS = HARNESS.replace('#include <map>', '#include <map>\n#include <memory>')
HARNESS = HARNESS.replace('T* get()const{return pointer_;}',
    'T* get()const{return pointer_;} T* operator->()const{return pointer_;}')
HARNESS = HARNESS.replace('const char* data;size_t length;',
    'const char* data;size_t length;\n    StringRef(const char* p):data(p),length(std::strlen(p)){}\n'
    '    StringRef(const std::string& p):data(p.data()),length(p.size()){}')


def array(rows, fmt):
    stride = struct.calcsize(fmt)
    return struct.pack('<II', stride, len(rows)) + b''.join(struct.pack(fmt, *row) for row in rows)


def fixture():
    strings = [(7, b'objects/test.primitives/vertices'), (8, b'xyznuvtb')]
    data = bytes(range(80))
    entries = [(7, 0, 2, 2, 8)]
    streams = [(0, 32, 64, 0, 0), (10, 8, 16, 0, 64)]
    metadata = table_bytes(strings) + array(entries, '<5I') + array(streams, '<5I') + array([(80,)], '<I')
    return metadata, data


def describe(metadata):
    stride, count = struct.unpack_from('<II', metadata)
    offset = 8 + stride * count
    length, = struct.unpack_from('<I', metadata, offset)
    pool = offset + 4
    strings = {key: metadata[pool + start:pool + start + size]
               for key, start, size in struct.iter_unpack('<III', metadata[8:offset])}
    offset = pool + length
    arrays = []
    for fmt in ('<5I', '<5I', '<I'):
        stride, count = struct.unpack_from('<II', metadata, offset)
        offset += 8
        arrays.append(list(struct.iter_unpack(fmt, metadata[offset:offset + stride * count])))
        offset += stride * count
    assert offset == len(metadata)
    entries, streams, buffers = arrays
    result = bytearray(struct.pack('<II', 1, len(entries)))
    for key, begin, end, vertices, unused_format in entries:
        resource = strings[key]
        result += struct.pack('<I', len(resource)) + resource + struct.pack('<II', vertices, end - begin)
        for index, stride, size, unused_buffer, position in streams[begin:end]:
            result += struct.pack('<4I', index, position, stride, size)
    return result, len(entries), len(streams), len(buffers)


class StaticGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get('WOT_PUBLIC_ENGINE_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.is_file():
            raise unittest.SkipTest('Pinned public engine source archive is required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='stock-space-geometry-')
        cls.root = Path(cls.temporary.name)
        directory = cls.root / 'lib/compiled_space'
        directory.mkdir(parents=True)
        names = ('string_table_types.hpp', 'static_geometry_types.hpp', 'string_table.hpp',
                 'static_geometry.hpp', 'string_table.cpp', 'static_geometry.cpp')
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
        text = HARNESS
        markers = ('STRING_TYPES', 'GEOMETRY_TYPES', 'STRING_HEADER', 'GEOMETRY_HEADER', 'STRING_SOURCE', 'GEOMETRY_SOURCE')
        for marker, name in zip(markers, names):
            text = text.replace('@' + marker + '@', without_includes((directory / name).read_text()))
        cls.source = cls.root / 'probe.cpp'
        cls.source.write_text(text)
        cls.binary = cls.root / 'probe'
        subprocess.run(['c++', '-std=c++14', '-O1', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-const-variable', '-Wno-unused-local-typedefs',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                        str(cls.source), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_cases(self, cases):
        inputs, expected = bytearray(struct.pack('<I', len(cases))), bytearray()
        for version, data_version, fault, metadata, data, valid in cases:
            inputs += struct.pack('<4I', version, data_version, fault, len(metadata)) + metadata
            inputs += struct.pack('<I', len(data)) + data
            expected += describe(metadata)[0] if valid else struct.pack('<I', 0)
        source, output = self.root / 'inputs.bin', self.root / 'outputs.bin'
        source.write_bytes(inputs)
        subprocess.run([str(self.binary), str(source), str(output)], check=True)
        self.assertEqual(output.read_bytes(), expected)

    def test_gpu_native_resource_ownership_and_failure_transactions(self):
        metadata, data = fixture()
        self.run_cases([(2, 2, 0, metadata, data, True)] +
                       [(2, 2, fault, metadata, data, False) for fault in range(1, 9)])

    def test_invalid_section_versions_arrays_and_stream_ranges(self):
        metadata, data = fixture()
        cases = [(1, 2, 0, metadata, data, False), (2, 1, 0, metadata, data, False)]
        cases += [(2, 2, 0, metadata[:length], data, False) for length in range(len(metadata))]
        cases += [(2, 2, 0, metadata, data[:-1], False), (2, 2, 0, metadata, data + b'x', False),
                  (2, 2, 0, metadata + b'x', data, False)]
        table_size = len(table_bytes([(7, b'objects/test.primitives/vertices'), (8, b'xyznuvtb')]))
        for position, value in ((table_size, 4), (table_size + 4, 0xffffffff),
                (table_size + 8, 99), (table_size + 12, 10), (table_size + 16, 10),
                (table_size + 20, 0xffffffff), (table_size + 24, 99),
                (table_size + 28, 4), (table_size + 36 + 4, 0),
                (table_size + 36 + 12, 99), (table_size + 36 + 16, 80)):
            altered = bytearray(metadata)
            struct.pack_into('<I', altered, position, value)
            cases.append((2, 2, 0, altered, data, False))
        self.run_cases(cases)

    def test_all_original_vertex_buffers_and_resource_streams(self):
        client = os.environ.get('WOT_0922_CLIENT')
        if not client:
            self.skipTest('WOT_0922_CLIENT is required for original compiled geometry')
        sys.path.insert(0, str(ROOT.parents[1] / 'tools'))
        from space_bin_0922 import CompiledSpace
        total = [0, 0, 0, 0]
        for package in (Path(client) / 'res/packages').glob('*.pkg'):
            with zipfile.ZipFile(package) as archive:
                for name in archive.namelist():
                    if not name.endswith('/space.bin'):
                        continue
                    space = CompiledSpace(archive.read(name))
                    metadata, data = space.section_data('BWSG'), space.section_data('BSGD')
                    self.run_cases([(2, 2, 0, metadata, data, True)])
                    counts = describe(metadata)[1:]
                    total[0] += 1
                    for i, value in enumerate(counts, 1):
                        total[i] += value
        self.assertGreater(total[0], 0)
        print('Production static geometry consumed %d original maps, %d resources, %d streams, %d exact vertex buffers' % tuple(total))


if __name__ == '__main__':
    unittest.main()
