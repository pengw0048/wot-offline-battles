"""Attach the original GasCloud owner to native world, draw and teardown lifecycles."""
import argparse
import re
from pathlib import Path


def insert(source, anchor, addition):
    if addition in source:
        return source
    if source.count(anchor) != 1:
        raise RuntimeError('The reviewed BattlegroundElements anchor changed: ' + repr(anchor))
    return source.replace(anchor, anchor + addition)


def replace(source, anchor, replacement):
    if replacement in source:
        return source
    if source.count(anchor) != 1:
        raise RuntimeError('The reviewed BattlegroundElements draw anchor changed: ' + repr(anchor))
    return source.replace(anchor, replacement)


def patch(root):
    root = Path(root)
    pending = []
    path = root / 'client/world_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    source = insert(source, '        GlobalEmbodiments::tick( dGameTime );', '\n        tickStockBattlegroundElements();')
    pending.append((path, source))
    path = root / 'client/script_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    anchor = '\tBigWorldClientScript::fini();'
    addition = '\tfiniStockBattlegroundElements();\n'
    if addition not in source:
        if source.count(anchor) != 1:
            raise RuntimeError('The reviewed BattlegroundElements teardown changed')
        source = source.replace(anchor, addition + anchor)
    pending.append((path, source))
    path = root / 'client/facade_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    flush = '        App::instance().drawContext( App::COLOUR_DRAW_CONTEXT ).flush( Moo::DrawContext::TRANSPARENT_CHANNEL_MASK );'
    source = source.replace('        beginStockBattlegroundParticles();\n', '')
    source = source.replace('\n\tif (!drawStockBattlegroundElements())\n\t\tERROR_MSG("BattlegroundElements: original fire storm draw failed\\n");', '')
    begin = '        beginStockBattlegroundParticles( &App::instance().drawContext( App::COLOUR_DRAW_CONTEXT ) );\n'
    if begin not in source:
        if source.count(flush) != 1:
            raise RuntimeError('The reviewed main particle pass changed')
        source = source.replace(flush, begin + flush)
    source = insert(source, flush, '\n        finishStockBattlegroundParticles();')
    pending.append((path, source))
    path = root / 'client/canvas_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    anchor = '\tPostProcessing::Manager::instance().draw();'
    draw = '\tif (!drawStockBattlegroundElements())\n\t\tERROR_MSG("BattlegroundElements: original fire storm draw failed\\n");\n'
    if draw not in source:
        if source.count(anchor) != 1:
            raise RuntimeError('The reviewed fire storm post effect pass changed')
        source = source.replace(anchor, draw + anchor)
    pending.append((path, source))
    path = root / 'lib/particle/renderers/particle_system_draw_item.hpp'
    source = insert(path.read_text(), '\tvoid draw();', '\n\tbool isBattlegroundSprite() const;')
    source = insert(source, '\tbool isBattlegroundSprite() const;', '\n\tvoid drawBattlegroundSplit();')
    pending.append((path, source))
    path = root / 'lib/particle/renderers/particle_system_draw_item.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    anchor = '\trenderer_->realDraw( worldTransform_, beg_, end_ );'
    replacement = '\tif (!drawStockBattlegroundParticles( renderer_, worldTransform_, beg_, end_ ))\n\t' + anchor
    # Replace only our generated methods, preserving the ordinary draw entry.
    source = re.sub(r'\nvoid ParticleSystemDrawItem::drawBattlegroundSplit\(\)\n\{.*?\n\}\n', '', source, flags=re.S)
    source = re.sub(r'\nbool ParticleSystemDrawItem::isBattlegroundSprite\(\) const\n\{.*?\n\}\n', '', source, flags=re.S)
    source = source.replace('void ParticleSystemDrawItem::draw()\n{\n\tBW_GUARD;\n' + replacement,
        'void ParticleSystemDrawItem::draw()\n{\n\tBW_GUARD;\n' + anchor)
    source = re.sub(r'BW_BEGIN_NAMESPACE\n+', 'BW_BEGIN_NAMESPACE', source, count=1)
    source = insert(source, 'BW_BEGIN_NAMESPACE', '''

void ParticleSystemDrawItem::drawBattlegroundSplit()
{
\tif (!drawStockBattlegroundParticles( renderer_, worldTransform_, beg_, end_ ))
\t\trenderer_->realDraw( worldTransform_, beg_, end_ );
}

bool ParticleSystemDrawItem::isBattlegroundSprite() const
{
    return dynamic_cast<SpriteParticleRenderer *>( renderer_ ) != NULL;
}


''')
    pending.append((path, source))
    path = root / 'lib/moo/draw_context.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_battleground_elements.hpp"')
    source = insert(source, '#include "stock_battleground_elements.hpp"', '\n#include <set>')
    start = source.index('void\tDrawContext::flushTransparentChannel( bool isClearing )')
    end = source.index('void\tDrawContext::flushOpaqueChannel()', start)
    original = source[start:end]
    anchor = '''\tropProcessor_->start();
\t// flush distance sorted ops
\tfor (DynamicArray<DistanceSortedOp>::iterator it = transparentOps_.begin();
\t\tit != transparentOps_.end(); ++it)
\t{
\t\tropProcessor_->flushSingleWrappedOp( *it, isClearing );
\t}
\tropProcessor_->stop();'''
    replacement = '''\t// #1513 00632b3c/00632b74: sprite bucket 0, transparent models,
\t// then sprite bucket 1. The fire storm post effect runs later in Canvas.
\tconst bool gasPartition = stockBattlegroundParticlePhase( this );
\tstd::set<void *> gasSprites;
\tif (gasPartition)
\t{
\t\tropProcessor_->start();
\t\tfor (DynamicArray<DistanceSortedOp>::iterator it = transparentOps_.begin();
\t\t\tit != transparentOps_.end(); ++it)
\t\t{
\t\t\tif (it->type_ != WrapperOp::USER_DRAW_ITEM || gasSprites.find( it->payload_ ) != gasSprites.end()) continue;
\t\t\tDrawContext::UserDrawItem * item = static_cast<DrawContext::UserDrawItem *>( it->payload_ );
\t\t\tif (!isStockBattlegroundParticleItem( item )) continue;
\t\t\tgasSprites.insert( it->payload_ );
\t\t\tropProcessor_->stop();
\t\t\tdrawStockBattlegroundParticleItem( item );
\t\t\tif (isClearing) item->fini();
\t\t\tropProcessor_->start();
\t\t}
\t\tropProcessor_->stop();
\t}
\tropProcessor_->start();
\tfor (DynamicArray<DistanceSortedOp>::iterator it = transparentOps_.begin();
\t\tit != transparentOps_.end(); ++it)
\t{
\t\t// fini() may destroy a user item. Never inspect it again after consumption.
\t\tif (gasSprites.find( it->payload_ ) != gasSprites.end()) continue;
\t\tropProcessor_->flushSingleWrappedOp( *it, isClearing );
\t}
\tropProcessor_->stop();
\tif (gasPartition) finishStockBattlegroundParticles();'''
    marker = '\t// #1513 00632b3c/00632b74'
    if marker in original:
        original = original[:original.index(marker)] + anchor + '\n}\n\n'
    source = source[:start] + replace(original, anchor, replacement) + source[end:]
    pending.append((path, source))
    for path, source in pending:
        path.write_text(source)
    # Particle renderers and Moo include lib/ rather than client/.
    header = Path(__file__).with_name('stock_battleground_elements.hpp').read_text()
    (root / 'lib/stock_battleground_elements.hpp').write_text(header)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
