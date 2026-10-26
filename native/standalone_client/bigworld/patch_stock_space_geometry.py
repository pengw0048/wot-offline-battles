"""Load original BWSG v2 metadata and its separately owned BSGD vertex bytes."""
import argparse
from pathlib import Path


DETAIL = r'''class StaticGeometry::Detail
{
public:
    template<class T> static bool readArray(BinaryFormat::Stream* stream, BW::vector<T>& output)
    {
        uint32 stride=0,count=0;
        if (!stream->read(stride) || stride!=sizeof(T) || !stream->read(count) ||
            stream->tell()>stream->size() || count>(stream->size()-stream->tell())/sizeof(T)) return false;
        const void* data=stream->readRaw(size_t(count)*sizeof(T));
        if (!data) return false;
        // The variable-length string pool does not align the following arrays.
        output.resize(count);
        if (count) memcpy(output.data(),data,size_t(count)*sizeof(T));
        return true;
    }

    static bool validate(StaticGeometry* owner, BinaryFormat::Stream* data)
    {
        size_t remaining=data->size()-data->tell();
        for (size_t i=0;i<owner->bufferEntries_.size();++i)
        {
            const uint32 size=owner->bufferEntries_[i].size_;
            if (size>remaining) return false;
            remaining-=size;
        }
        if (remaining) return false;
        for (size_t i=0;i<owner->streams_.size();++i)
        {
            const StaticGeometryTypes::StreamEntry& stream=owner->streams_[i];
            if (stream.hostBufferID_>=owner->bufferEntries_.size()) return false;
            const uint32 size=owner->bufferEntries_[stream.hostBufferID_].size_;
            if (stream.hostBufferOffset_>size || stream.size_>size-stream.hostBufferOffset_) return false;
        }
        for (size_t i=0;i<owner->entries_.size();++i)
        {
            const StaticGeometryTypes::Entry& entry=owner->entries_[i];
            const char* resource=owner->stringTable_.entry(entry.resourceID_);
            const char* format=owner->stringTable_.entry(entry.originalVertexFormat_);
            if (!resource || !*resource || !format || !*format ||
                entry.beginStreamIndex_>entry.endStreamIndex_ || entry.endStreamIndex_>owner->streams_.size()) return false;
            bool mainStream=false;
            for (uint32 j=entry.beginStreamIndex_;j<entry.endStreamIndex_;++j)
            {
                const StaticGeometryTypes::StreamEntry& stream=owner->streams_[j];
                if (!stream.stride_ || stream.size_%stream.stride_ ||
                    stream.size_/stream.stride_!=entry.numVertices_) return false;
                if (!stream.streamIndex_)
                {
                    if (mainStream) return false;
                    mainStream=true;
                }
                for (uint32 k=entry.beginStreamIndex_;k<j;++k)
                    if (owner->streams_[k].streamIndex_==stream.streamIndex_) return false;
            }
            if (!mainStream) return false;
        }
        return true;
    }

    static bool readBuffers(StaticGeometry* owner, BinaryFormat::Stream* data,
        BW::vector<Moo::VertexBuffer>& buffers)
    {
        const D3DPOOL pool=Moo::RenderContext::patchD3DPool(D3DPOOL_MANAGED);
        buffers.reserve(owner->bufferEntries_.size());
        for (size_t i=0;i<owner->bufferEntries_.size();++i)
        {
            const uint32 size=owner->bufferEntries_[i].size_;
            void* bytes=data->readRaw(size);
            Moo::VertexBuffer buffer;
            if (!bytes || FAILED(buffer.create(size,0,0,pool,"vertexBuffer/StaticGeometry"))) return false;
            void* destination=NULL;
            if (FAILED(buffer.lock(0,size,&destination,0))) return false;
            if (!destination) { buffer.unlock();return false; }
            memcpy(destination,bytes,size);
            if (FAILED(buffer.unlock())) return false;
            buffer.addToPreloadList();
            buffers.push_back(buffer);
            ++owner->numEntriesPopulated_;
        }
        return data->eof();
    }

    static bool createResource(StaticGeometry* owner, const StaticGeometryTypes::Entry& entry,
        const BW::vector<Moo::VertexBuffer>& buffers, BW::vector<Moo::VerticesPtr>& resources)
    {
        const char* resource=owner->stringTable_.entry(entry.resourceID_);
        const char* format=owner->stringTable_.entry(entry.originalVertexFormat_);
        const BW::StringRef device(Moo::VertexDeclaration::getTargetDevice());
        const Moo::VertexFormat* source=Moo::VertexFormatCache::get(format);
        const Moo::VertexFormat* target=source?Moo::VertexFormatCache::getTarget(*source,device,true):NULL;
        Moo::VertexDeclaration* declaration=target?Moo::VertexDeclaration::get(target->name()):NULL;
        Moo::VertexDeclaration* instancing=Moo::VertexDeclaration::get(
            Moo::VertexFormatCache::getTargetName<Moo::InstancingStream>(device,true));
        if (!declaration || !instancing || !declaration->format().streamCount() ||
            !Moo::VertexDeclaration::combine(declaration,instancing)) return false;
        // Validate the same declaration producers consumed by initFromValues
        // and addStream before their legacy assertion-only pointer accesses.
        Moo::VertexDeclaration* combined=declaration;
        for (uint32 j=entry.beginStreamIndex_;j<entry.endStreamIndex_;++j)
        {
            const StaticGeometryTypes::StreamEntry& stream=owner->streams_[j];
            if (!stream.streamIndex_)
            {
                if (declaration->format().streamStride(0)!=stream.stride_) return false;
                continue;
            }
            const Moo::VertexStreamDesc::StreamDescs& known=Moo::VertexStreamDesc::getKnownStreams();
            const Moo::VertexFormat* extra=NULL;
            for (size_t k=0;k<known.size();++k)
                if (known[k].streamIndex_==stream.streamIndex_) extra=known[k].defaultVertexFormat();
            if (!extra || extra->streamCount()<=stream.streamIndex_ ||
                extra->streamStride(stream.streamIndex_)!=stream.stride_) return false;
            const Moo::VertexFormat* extraTarget=Moo::VertexFormatCache::getTarget(*extra,device,true);
            Moo::VertexDeclaration* extraDeclaration=extraTarget?Moo::VertexDeclaration::get(extraTarget->name()):NULL;
            if (!extraDeclaration) return false;
            combined=Moo::VertexDeclaration::combine(combined,extraDeclaration);
            if (!combined || !Moo::VertexDeclaration::combine(combined,instancing)) return false;
        }
        Moo::VerticesPtr vertices=new Moo::Vertices(resource,0);
        vertices->initFromValues(entry.numVertices_,format);
        for (uint32 j=entry.beginStreamIndex_;j<entry.endStreamIndex_;++j)
        {
            const StaticGeometryTypes::StreamEntry& stream=owner->streams_[j];
            vertices->addStream(stream.streamIndex_,buffers[stream.hostBufferID_],
                stream.hostBufferOffset_,stream.stride_,stream.size_);
        }
        resources.push_back(vertices);
        ++owner->numEntriesPopulated_;
        return true;
    }
};
'''

READ = r'''bool StaticGeometry::read(BinaryFormat& reader)
{
    PROFILER_SCOPED(StaticGeometry_ReadFromSpace);
    MF_ASSERT(pReader_==NULL);
    pReader_=&reader;
    pStream_=reader.findAndOpenSection(StaticGeometryTypes::FORMAT_MAGIC,
        StaticGeometryTypes::FORMAT_VERSION,"StaticGeometry");
    if (!pStream_ || !stringTable_.readFromStream(pStream_) ||
        !Detail::readArray(pStream_,entries_) || !Detail::readArray(pStream_,streams_) ||
        !Detail::readArray(pStream_,bufferEntries_) || !pStream_->eof() || !loadGeometry())
    {
        ASSET_MSG("Failed to load original static geometry '%s'.\n",reader.resourceID());
        close();return false;
    }
    return true;
}
'''

LOAD = r'''bool StaticGeometry::loadGeometry()
{
    PROFILER_SCOPED(StaticGeometry_loadGeometry);
    // #1513 006ced60 opens BSGD v2 after the BWSG metadata arrays. Its bytes
    // are not an inline tail and do not carry a second array-size prefix.
    if (!pReader_ || !pStream_ || !loadedResources_.empty()) return false;
    BinaryFormat::Stream* data=pReader_->findAndOpenSection(FourCC("BSGD"),2,"StaticGeometryData");
    if (!data) return false;
    BW::vector<Moo::VertexBuffer> buffers;
    bool success=Detail::validate(this,data) && Detail::readBuffers(this,data,buffers);
    pReader_->closeSection(data);
    if (!success) return false;
    BW::vector<Moo::VerticesPtr> resources;
    resources.reserve(entries_.size());
    for (size_t i=0;i<entries_.size();++i)
        if (!Detail::createResource(this,entries_[i],buffers,resources)) return false;
    // Publish only a complete set. The original VerticesManager and each
    // Vertices stream retain the actual GPU leases after BSGD is unmapped.
    for (size_t i=0;i<resources.size();++i)
        Moo::VerticesManager::instance()->populateResource(resources[i]->resourceID(),resources[i]);
    loadedResources_.swap(resources);
    return true;
}
'''

CLOSE = r'''void StaticGeometry::close()
{
    loadedResources_.clear();
    entries_.clear();streams_.clear();bufferEntries_.clear();stringTable_.close();
    if (pStream_ && pReader_) pReader_->closeSection(pStream_);
    pReader_=NULL;pStream_=NULL;numEntriesPopulated_=0;
}
'''


def patch(root):
    root = Path(root) / 'lib/compiled_space'
    types = root / 'static_geometry_types.hpp'
    header = types.read_text(encoding='utf-8-sig')
    old, new = 'FORMAT_VERSION = 0x00000001;', 'FORMAT_VERSION = 0x00000002;'
    if new not in header:
        if header.count(old) != 1:
            raise ValueError('Unrecognized pinned static geometry version')
        header = header.replace(old, new)
    interface = root / 'static_geometry.hpp'
    declarations = interface.read_text(encoding='utf-8-sig')
    declarations = declarations.replace('void loadGeometry();', 'bool loadGeometry();')
    declarations = declarations.replace('ExternalArray<StaticGeometryTypes::', 'BW::vector<StaticGeometryTypes::')
    path = root / 'static_geometry.cpp'
    source = path.read_text(encoding='utf-8-sig')
    includes = '#include "moo/vertices.hpp"'
    extended = includes + '\n#include "moo/vertex_declaration.hpp"\n#include "moo/vertex_format_cache.hpp"\n#include "moo/vertex_streams.hpp"'
    if extended not in source:
        if source.count(includes) != 1:
            raise ValueError('Unrecognized pinned static geometry includes')
        source = source.replace(includes, extended)
    start = source.index('class StaticGeometry::Detail')
    end = source.index('// ----------------------------------------------------------------------------\n// ----------------------------------------------------------------------------', start)
    source = source[:start] + DETAIL + '\n' + source[end:]
    for begin, finish, replacement in (
        ('bool StaticGeometry::read(', 'void StaticGeometry::loadGeometry()', READ),
        ('void StaticGeometry::loadGeometry()', 'void StaticGeometry::close()', LOAD),
        ('void StaticGeometry::close()', 'bool StaticGeometry::isValid()', CLOSE),
    ):
        # A repeated build sees the already changed load return type.
        if replacement in source:
            continue
        if finish not in source and finish.startswith('void StaticGeometry::loadGeometry'):
            finish = finish.replace('void ', 'bool ', 1)
        if begin not in source and begin.startswith('void StaticGeometry::loadGeometry'):
            begin = begin.replace('void ', 'bool ', 1)
        start = source.index(begin)
        end = source.rfind('// ----------------------------------------------------------------------------', start, source.index(finish, start))
        source = source[:start] + replacement + '\n\n' + source[end:]
    source = source.replace('return entries_.valid();', 'return pStream_ != NULL;')
    types.write_text(header, encoding='utf-8')
    interface.write_text(declarations, encoding='utf-8')
    path.write_text(source, encoding='utf-8')
    print('Original static geometry metadata and separate vertex-buffer owner installed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
