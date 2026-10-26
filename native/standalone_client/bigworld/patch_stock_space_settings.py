"""Read the original size-prefixed BWCS bounds into the native aligned AABB."""
import argparse
from pathlib import Path


READ = '''    // #1513 006b8a70 reads a 24-byte payload after its size word.
    // Native AABB alignment is not part of the original serialized format.
    uint32 bytes = 0;
    float bounds[6];
    if (!pStream_->read( bytes ) || bytes != sizeof(bounds) ||
        !pStream_->read( bounds[0] ) || !pStream_->read( bounds[1] ) ||
        !pStream_->read( bounds[2] ) || !pStream_->read( bounds[3] ) ||
        !pStream_->read( bounds[4] ) || !pStream_->read( bounds[5] ) ||
        !pStream_->eof())
'''


def patch(root):
    path = Path(root) / 'lib/compiled_space/compiled_space_settings.cpp'
    source = path.read_text(encoding='utf-8-sig')
    original = '\tif (!pStream_->read( data_ ))\n'
    assignment = '''    data_.spaceBounds_ = AABB(
        Vector3( bounds[0], bounds[1], bounds[2] ),
        Vector3( bounds[3], bounds[4], bounds[5] ) );

'''
    anchor = '\t// No need to keep this in memory\n'
    if READ not in source:
        if source.count(original) != 1 or source.count(anchor) != 1:
            raise ValueError('Unrecognized pinned compiled-space bounds reader')
        source = source.replace(original, READ).replace(anchor, assignment + anchor)
    path.write_text(source, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
