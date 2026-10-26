"""Read #1513 BWST resources through the actual compiled-space StringTable."""
import argparse
from pathlib import Path


READ = '''bool StringTable::readFromStream( BinaryFormat::Stream* pStream )
{
    typedef StringTableTypes::Entry Entry;
    // #1513 006ce550: a size-prefixed array of (FNV, offset, length),
    // followed by the size-prefixed, NUL-terminated byte pool.
    entries_.reset();
    pStringData_ = NULL;
    uint32 stride = 0, count = 0, bytes = 0;
    if (!pStream || !pStream->read( stride ) || stride != sizeof(Entry) ||
        !pStream->read( count ) || pStream->tell() > pStream->size() ||
        count > (pStream->size() - pStream->tell()) / sizeof(Entry))
        return false;
    Entry* entries = static_cast<Entry*>( pStream->readRaw( size_t(count) * sizeof(Entry) ) );
    if (!entries || !pStream->read( bytes ) || pStream->tell() > pStream->size() ||
        bytes > pStream->size() - pStream->tell())
        return false;
    char* strings = static_cast<char*>( pStream->readRaw( bytes ) );
    if (!strings || !pStream->eof()) return false;
    size_t accumulated = 0;
    for (uint32 i = 0; i < count; ++i)
    {
        const Entry& entry = entries[i];
        if (entry.offset_ >= bytes || entry.length_ >= bytes - entry.offset_ ||
            strings[entry.offset_ + entry.length_] != '\\0' ||
            (i && entries[i - 1].key_ >= entry.key_) ||
            entry.length_ + size_t(1) > bytes - accumulated)
            return false;
        accumulated += entry.length_ + size_t(1);
    }
    if (accumulated != bytes) return false;
    entries_.assignExternalStorage( entries, count, count );
    pStringData_ = strings;
    return true;
}
'''

LOOKUP = '''namespace
{
const StringTableTypes::Entry* stockStringEntry(
    const ExternalArray<StringTableTypes::Entry>& entries, size_t key )
{
    // #1513 006ce4c0 uses an unsigned lower_bound, with zero as the sentinel.
    if (!key || key > uint32(~0) || !entries.valid()) return NULL;
    size_t first = 0, count = entries.size();
    while (count)
    {
        const size_t step = count / 2;
        const size_t candidate = first + step;
        if (entries[candidate].key_ < key)
        {
            first = candidate + 1;
            count -= step + 1;
        }
        else count = step;
    }
    // Authored references name existing keys. Contain a corrupt reference
    // instead of resolving it to the next unrelated resource.
    return first < entries.size() && entries[first].key_ == key ? &entries[first] : NULL;
}
}

const char* StringTable::entry( size_t key ) const
{
    const StringTableTypes::Entry* found = stockStringEntry( entries_, key );
    return found ? pStringData_ + found->offset_ : NULL;
}

size_t StringTable::entryLength( size_t key ) const
{
    const StringTableTypes::Entry* found = stockStringEntry( entries_, key );
    return found ? found->length_ : 0;
}
'''


def replace_once(source, before, after):
    if source.count(after) == 1:
        return source
    if source.count(before) != 1:
        raise ValueError('Unrecognized pinned compiled-space string-table seam')
    return source.replace(before, after)


def patch(root):
    root = Path(root) / 'lib/compiled_space'
    types = root / 'string_table_types.hpp'
    header = types.read_text(encoding='utf-8-sig')
    header = replace_once(header, 'FORMAT_VERSION = 0x00000001;', 'FORMAT_VERSION = 0x00000002;')
    header = replace_once(header, '\t\t\tuint32 offset_; // From beginning of string data',
                          '\t\t\tuint32 key_; // Original FNV resource identity\n'
                          '\t\t\tuint32 offset_; // From beginning of string data')
    implementation = root / 'string_table.cpp'
    source = implementation.read_text(encoding='utf-8-sig')
    if READ not in source:
        start = source.index('bool StringTable::readFromStream(')
        end = source.index('// ----------------------------------------------------------------------------\nvoid StringTable::close()', start)
        source = source[:start] + READ + '\n\n' + source[end:]
    if LOOKUP not in source:
        start = source.index('const char* StringTable::entry(')
        end = source.index('} // namespace CompiledSpace', start)
        source = source[:start] + LOOKUP + '\n' + source[end:]
    types.write_text(header, encoding='utf-8')
    implementation.write_text(source, encoding='utf-8')
    print('Original compiled-space FNV string-table reader installed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
