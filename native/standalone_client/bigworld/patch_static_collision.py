#!/usr/bin/env python3
"""Retain the #1513 compiled model's BSP material selection in public BW."""
import argparse
from pathlib import Path


RESOURCE_BEFORE = '''\t\tDataSectionPtr pPrimitivesSection =
\t\t\t\tPrimitiveFile::get( primitivesResID_ );'''
RESOURCE_AFTER = '''\t\tDataSectionPtr pPrimitivesSection =
\t\t\t\tPrimitiveFile::get( pVisualSection_ ? pVisualSection_->readString(
\t\t\t\t\t"stockBsp/resource", primitivesResID_ ) : primitivesResID_ );'''
DISABLED_BEFORE = '''\tbool \t\tshouldRemapFlags = false;

\t// Try loading _bsp.visual'''
DISABLED_AFTER = '''\tbool \t\tshouldRemapFlags = false;
\tif (pVisualSection_ && pVisualSection_->readBool( "stockBsp/disabled", false ))
\t{
\t\tpBSPTree = NULL;
\t\treturn false;
\t}

\t// Try loading _bsp.visual'''
LOAD_BEFORE = '''\t\t\tpBSPTree = BSPTreeTool::loadBSP( pBSPData );'''
LOAD_AFTER = '''\t\t\tBSPTree * source = BSPTreeTool::loadBSP( pBSPData );
\t\t\tDataSectionPtr compiled = pVisualSection_ ?
\t\t\t\tpVisualSection_->openSection( "stockBsp" ) : NULL;
\t\t\tif (source && compiled)
\t\t\t{
\t\t\t\t// #1513 StaticModel loader 0x006d4230 selects triangles in
\t\t\t\t// material-record order, then rebuilds the actual collision BSP.
\t\t\t\t// Omitted materials belong to another model state, not to this BSP.
\t\t\t\tif (!compiled->readBool( "raw", false ))
\t\t\t\t{
\t\t\t\t\tRealWTriangleSet selected;
\t\t\t\t\tBW::vector<DataSectionPtr> materials;
\t\t\t\t\tcompiled->openSections( "material", materials );
\t\t\t\t\tfor (size_t material = 0; material < materials.size(); ++material)
\t\t\t\t\t{
\t\t\t\t\t\tconst uint32 index = materials[material]->readInt( "index" );
\t\t\t\t\t\tconst WorldTriangle::Flags flags = static_cast<WorldTriangle::Flags>(
\t\t\t\t\t\t\tmaterials[material]->readInt( "flags" ) );
\t\t\t\t\t\tfor (uint triangle = 0; triangle < source->numTriangles(); ++triangle)
\t\t\t\t\t\t{
\t\t\t\t\t\t\tWorldTriangle copy = source->triangles()[triangle];
\t\t\t\t\t\t\tif (copy.flags() == index)
\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\tcopy.flags( flags );
\t\t\t\t\t\t\t\tselected.push_back( copy );
\t\t\t\t\t\t\t}
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t\tBSPTree * filtered = BSPTreeTool::buildBSP( selected );
\t\t\t\t\tdelete source;
\t\t\t\t\tsource = filtered;
\t\t\t\t}
\t\t\t\tshouldRemapFlags = false;
\t\t\t}
\t\t\tpBSPTree = source;'''


def patch_engine(root):
    path = Path(root) / 'lib/moo/visual_common.hpp'
    source = path.read_text(encoding='utf-8-sig')
    for before, after in ((RESOURCE_BEFORE, RESOURCE_AFTER),
                          (DISABLED_BEFORE, DISABLED_AFTER), (LOAD_BEFORE, LOAD_AFTER)):
        if source.count(after) == 1:
            continue
        if source.count(before) != 1:
            raise ValueError('Unrecognized pinned static collision seam')
        source = source.replace(before, after)
    path.write_text(source, encoding='utf-8')
    print('Compiled static-model BSP selection installed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
