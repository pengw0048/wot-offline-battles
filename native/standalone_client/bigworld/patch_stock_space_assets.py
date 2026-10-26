"""Restore the #1513 BWAL v2 resource keys and native preload ownership."""
import argparse
from pathlib import Path


READ = '''bool AssetList::read( BinaryFormat& reader )
{
    PROFILER_SCOPED( AssetList_ReadFromSpace );
    typedef AssetListTypes::Entry Entry;
    MF_ASSERT( pReader_ == NULL );
    pReader_ = &reader;
    pStream_ = pReader_->findAndOpenSection( AssetListTypes::FORMAT_MAGIC,
        AssetListTypes::FORMAT_VERSION, "AssetList" );
    uint32 stride = 0, count = 0;
    if (!pStream_ || !pStream_->read( stride ) || stride != sizeof(Entry) ||
        !pStream_->read( count ) || pStream_->tell() > pStream_->size() ||
        count > (pStream_->size() - pStream_->tell()) / sizeof(Entry))
    {
        this->close();
        return false;
    }
    Entry* entries = static_cast<Entry*>( pStream_->readRaw( size_t(count) * sizeof(Entry) ) );
    if (!entries || !pStream_->eof())
    {
        this->close();
        return false;
    }
    // #1513 006ccd50 consumes two DWORDs per entry: type and BWST FNV key.
    entries_.assignExternalStorage( entries, count, count );
    return true;
}
'''

PRELOAD = '''void AssetList::preloadAssets( const StringTable& stringTable )
{
    PROFILER_SCOPED( CompiledSpace_AssetList_PreloadAssets );
    if (entries_.empty()) return;
    MF_ASSERT( this->isValid() );
    MF_ASSERT( preloadedAssets_.empty() );
    preloadedAssets_.reserve( this->size() );
    numPreloadsProcessed_ = 0;
    for (size_t assetIdx = 0; assetIdx < this->size(); ++assetIdx)
    {
        const size_t key = this->stringTableIndex( assetIdx );
        const char* resource = stringTable.entry( key );
        // 006ccc30 skips a missing key rather than indexing the table or
        // constructing a StringRef from NULL. An authored empty string is valid.
        if (resource)
        {
            const StringRef resourceID( resource, stringTable.entryLength( key ) );
            preloadedAssets_.push_back( preloadAsset( this->assetType( assetIdx ), resourceID ) );
        }
        else
            ASSET_MSG( "AssetList: missing BWST resource key %u.\\n", uint32(key) );
        // The original loading percentage measures processing progress, not
        // successful resource leases. Failed and deferred entries still settle.
        ++numPreloadsProcessed_;
    }
}
'''

DISPATCH = '''AssetList::IAssetReference* AssetList::preloadAsset(
    AssetListTypes::AssetType type, const StringRef& resourceID )
{
    PROFILER_SCOPED_DYNAMIC_STRING( resourceID.to_string().c_str() );
    IAssetReference* asset = NULL;
    switch (type)
    {
    case AssetListTypes::ASSET_TYPE_DATASECTION:
        asset = Detail::makeAssetReference( BWResource::openSection( resourceID ) );
        if (!asset)
            ASSET_MSG( "Failed to preload '%s' as DATASECTION.\\n", resourceID.to_string().c_str() );
        break;
    case AssetListTypes::ASSET_TYPE_TEXTURE:
        asset = Detail::makeAssetReference( Moo::TextureManager::instance()->get(
            resourceID, true, true, true, "Art/File Textures/" ) );
        if (!asset)
            ASSET_MSG( "Failed to preload '%s' as TEXTURE.\\n", resourceID.to_string().c_str() );
        break;
    case AssetListTypes::ASSET_TYPE_MODEL:
        // 006cc9e4..006cca47 explicitly returns NULL for MODEL. Static scene
        // loaders own model geometry; this list does not instantiate Models.
        break;
    default:
        // The original authored catalog also contains VISUAL entries. They
        // follow this native diagnostic path, not an invented Visual preload.
        ASSET_MSG( "AssetList: unknown resource type %u ('%s').\\n", type, resourceID.to_string().c_str() );
        break;
    }
    return asset;
}
'''


def replace_once(source, before, after):
    if source.count(after) == 1:
        return source
    if source.count(before) != 1:
        raise ValueError('Unrecognized pinned compiled-space asset-list seam: ' + repr(before))
    return source.replace(before, after)


def patch(root):
    root = Path(root) / 'lib/compiled_space'
    types = root / 'asset_list_types.hpp'
    header = types.read_text(encoding='utf-8-sig')
    header = replace_once(header, 'FORMAT_VERSION = 0x00000001;', 'FORMAT_VERSION = 0x00000002;')
    header = replace_once(header, 'typedef uint8 AssetType;', 'typedef uint32 AssetType;')
    header = replace_once(header, 'Index stringTableIndex_;',
        'Index stringTableIndex_; // BWST v2 FNV resource key, not an ordinal index')
    implementation = root / 'asset_list.cpp'
    source = implementation.read_text(encoding='utf-8-sig')
    for signature, end_signature, replacement in (
        ('bool AssetList::read(', 'void AssetList::close()', READ),
        ('void AssetList::preloadAssets(', 'AssetList::IAssetReference* AssetList::preloadAsset(', PRELOAD),
        ('AssetList::IAssetReference* AssetList::preloadAsset(', 'void AssetList::releasePreloadedAssets()', DISPATCH),
    ):
        if replacement not in source:
            start = source.index(signature)
            end = source.rfind('// ----------------------------------------------------------------------------', start, source.index(end_signature, start))
            source = source[:start] + replacement + '\n\n' + source[end:]
    source = source.replace('#include "model/model.hpp"\n', '')
    source = replace_once(source, 'return new AssetReference<BaseAssetType>( pAsset );',
        'return pAsset ? new AssetReference<BaseAssetType>( pAsset ) : NULL;')
    source = replace_once(source, 'return new AssetReference<BaseAssetType>( pAsset.get() );',
        'return pAsset ? new AssetReference<BaseAssetType>( pAsset.get() ) : NULL;')
    types.write_text(header, encoding='utf-8')
    implementation.write_text(source, encoding='utf-8')
    print('Original compiled-space asset-list reader and native preload leases installed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
