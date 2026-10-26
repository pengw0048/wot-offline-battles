#!/usr/bin/env python3
"""Restore #1513 decal resource registration on the public native owners."""

import argparse
from pathlib import Path


def replace(source, old, new, label):
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError('Pinned decal owner anchor changed: ' + label)
    return source.replace(old, new, 1)


def patch(root):
    folder = Path(root) / 'lib/moo'
    header = folder / 'renderer.hpp'
    source = header.read_text()
    if 'class StockTraceTextureOwner;' not in source:
        source = replace(source, 'class SSAOSupport;',
            'class SSAOSupport;\nclass StockTraceTextureOwner;', 'owner declaration')
    source = replace(source,
        '    IRendererPipeline*          pipeline() const;',
        '    IRendererPipeline*          pipeline() const;\n'
        '    StockTraceTextureOwner* traceTextureOwner() const\n'
        '        { return m_stockTraceTextures.get(); }', 'owner access')
    source = replace(source,
        '    std::auto_ptr<IRendererPipeline>            m_pipeline;',
        '    std::auto_ptr<IRendererPipeline>            m_pipeline;\n'
        '    std::auto_ptr<StockTraceTextureOwner> m_stockTraceTextures;', 'owner storage')
    header.write_text(source)

    implementation = folder / 'renderer.cpp'
    source = implementation.read_text()
    source = replace(source, '#include "renderer.hpp"',
        '#include "renderer.hpp"\n#include "stock_decal_resources.hpp"', 'complete owner')
    source = replace(source,
        '    //-- ToDo (b_sviglo): Reconsider passing intial options',
        '    // #1513 ForwardPipeline has no trace owner; deferred owns it\n'
        '    // before game.init and independently of camera space.\n'
        '    if (m_type == IRendererPipeline::TYPE_DEFERRED_SHADING)\n'
        '        m_stockTraceTextures.reset(new StockTraceTextureOwner());\n\n'
        '    //-- ToDo (b_sviglo): Reconsider passing intial options', 'owner startup')
    if '    m_stockTraceTextures.reset();' not in source:
        source = replace(source, '    g_pipelineMacroSetting = NULL;',
            '    m_stockTraceTextures.reset();\n    g_pipelineMacroSetting = NULL;', 'owner shutdown')
    implementation.write_text(source)

    sticker = folder / 'sticker_decal.cpp'
    source = sticker.read_text()
    source = replace(source, 'float PyStickerModel::s_criticalAngle = 20.0f;',
        '// #1513 1a1a4d0 stores radians; DecalMap later sets authored degrees.\n'
        'float PyStickerModel::s_criticalAngle = 0.7853f;', 'angle initial state')
    sticker.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
