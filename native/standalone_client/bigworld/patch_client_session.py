#!/usr/bin/env python3
"""Add a synchronous identity-publication seam to the pinned local factory."""

import argparse
from pathlib import Path


def patch_engine(root):
    base = Path(root) / 'lib' / 'connection_model'
    patches = {
        'bw_connection.hpp': [(
            '\t\tBinaryIStream & data );\n\n\tBWENTITY_API bool destroyLocalEntity',
            '\t\tBinaryIStream & data,\n'
            '\t\tbool (*beforeCreate)( EntityID, void * ) = NULL,\n'
            '\t\tvoid * context = NULL );\n\n'
            '\tBWENTITY_API bool destroyLocalEntity')],
        'bw_entities.hpp': [(
            '\t\tconst Direction3D & direction, BinaryIStream & data );\n\n'
            '\tBWENTITY_API static bool isLocalEntity',
            '\t\tconst Direction3D & direction, BinaryIStream & data,\n'
            '\t\tbool (*beforeCreate)( EntityID, void * ) = NULL,\n'
            '\t\tvoid * context = NULL );\n\n'
            '\tBWENTITY_API static bool isLocalEntity')],
        'bw_connection.cpp': [(
            '\tBinaryIStream & data )\n{\n'
            '\tBWEntity * pEntity = entities_.createLocalEntity( entityTypeID,\tspaceID,\n'
            '\t\tvehicleID, position, direction, data );',
            '\tBinaryIStream & data,\n'
            '\tbool (*beforeCreate)( EntityID, void * ), void * context )\n{\n'
            '\tBWEntity * pEntity = entities_.createLocalEntity( entityTypeID,\tspaceID,\n'
            '\t\tvehicleID, position, direction, data, beforeCreate, context );')],
        'bw_entities.cpp': [(
            '\tconst Direction3D & direction, BinaryIStream & data )\n{\n'
            '\t// TODO: Wrap if someone creates a billion Local Entities,',
            '\tconst Direction3D & direction, BinaryIStream & data,\n'
            '\tbool (*beforeCreate)( EntityID, void * ), void * context )\n{\n'
            '\t// TODO: Wrap if someone creates a billion Local Entities,'), (
            '\tMF_ASSERT( isLocalEntityID( newID ) );\n\n'
            '\tBWEntityPtr pNewEntity = entityFactory_.create(',
            '\tMF_ASSERT( isLocalEntityID( newID ) );\n\n'
            '\t// Publish identity before construction can synchronously enter Python.\n'
            '\tif (beforeCreate && !beforeCreate( newID, context ))\n'
            '\t{\n\t\treturn NULL;\n\t}\n\n'
            '\tBWEntityPtr pNewEntity = entityFactory_.create(')],
    }
    updates = []
    for name, replacements in patches.items():
        path = base / name
        text = path.read_text(encoding='utf-8')
        for before, after in replacements:
            if before in text:
                if text.count(before) != 1:
                    raise ValueError('ambiguous pinned factory seam: ' + name)
                text = text.replace(before, after)
            elif text.count(after) != 1:
                raise ValueError('unrecognized pinned factory seam: ' + name)
        updates.append((path, text))
    # Validate every seam before changing any file.
    for path, text in updates:
        path.write_text(text, encoding='utf-8')
    print('Local entity identity callback installed in four pinned engine files')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
