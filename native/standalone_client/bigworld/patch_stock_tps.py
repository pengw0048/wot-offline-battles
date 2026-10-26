"""Restore #1513 native tPS owners without routing Influx through Pixie quality."""
import argparse
from pathlib import Path


def replace(source, anchor, replacement):
    if replacement in source:
        return source
    if source.count(anchor) != 1:
        raise RuntimeError('The reviewed tPS owner anchor changed: ' + repr(anchor))
    return source.replace(anchor, replacement)


def patch(root):
    root = Path(root)
    pending = []
    path = root / 'lib/particle/meta_particle_system.hpp'
    source = replace(path.read_text(), '\tParticleSystems\t\tsystems_;',
                     '\tParticleSystems\t\tsystems_;\n\tuint32 inverseRenderGroups_ = 0;')
    pending.append((path, source))
    path = root / 'lib/particle/meta_particle_system.cpp'
    source = replace(path.read_text(), '#include "pch.hpp"',
                     '#include "pch.hpp"\n#include "stock_tps_quality.hpp"')
    source = replace(source, '''MetaParticleSystemPtr MetaParticleSystem::clone() const
{
    BW_GUARD;
\tMetaParticleSystemPtr result = new MetaParticleSystem();''', '''MetaParticleSystemPtr MetaParticleSystem::clone() const
{
    BW_GUARD;
    // 009e3a70 rejects disabled inverse render groups before allocating.
    if (stockTPSGroupDisabled( inverseRenderGroups_ )) return NULL;
\tMetaParticleSystemPtr result = new MetaParticleSystem();
    result->inverseRenderGroups_ = inverseRenderGroups_;''')
    source = replace(source, '''bool MetaParticleSystem::tick( float dTime )
{
\tBW_GUARD;
\tbool updated = false;''', '''bool MetaParticleSystem::tick( float dTime )
{
\tBW_GUARD;
    // 009e5b10 releases disabled systems before their next native update.
    if (stockTPSGroupDisabled( inverseRenderGroups_ )) removeAllSystems();
\tbool updated = false;''')
    source = replace(source, '\t// save cache to file\n\tpDS->save();',
                     '''    // 009e5480 writes this field. The original 009e42a0 loader
    // does not read it; restoring an XML reader would change #1513 behavior.
    pDS->writeUInt( "addition/inv_render_groups", inverseRenderGroups_ );
\t// save cache to file
\tpDS->save();''')
    pending.append((path, source))
    for path, source in pending:
        path.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
