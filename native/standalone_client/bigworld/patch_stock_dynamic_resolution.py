"""Restore explicit scene-resolution ownership in the pinned public renderer.

Scene targets opt in at their producer. Target-stack restoration retains both
the actual viewport and its unscaled logical dimensions; a saved viewport is
never scaled twice. Independent shadow/reflection targets retain their default.
"""
from pathlib import Path
import argparse
import re


def patch(root):
    root = Path(root)
    pending = {}

    def read(name):
        return pending.get(name, (root / name).read_text())

    def replace(name, old, new, present=None):
        text = read(name)
        if (present or new) in text:
            return
        # The fullscreen replacement is built in stages below. Its final body
        # deliberately replaces those intermediate anchors as one transaction.
        if name == 'lib/moo/fullscreen_quad.cpp' and '// #1513 99bdc0:' in text:
            return
        if text.count(old) != 1:
            raise ValueError('Dynamic-resolution owner anchor changed: ' + name)
        pending[name] = text.replace(old, new, 1)

    def include(name):
        replace(name, '#include "pch.hpp"',
                '#include "pch.hpp"\n#include "stock_dynamic_resolution.hpp"',
                '#include "stock_dynamic_resolution.hpp"')

    header = 'lib/moo/render_context.hpp'
    replace(header, '\t\tHRESULT\t\t\t\t\t\tsetViewport( DX::Viewport* viewport );',
            '\t\tbool stockSceneResolution() const { return stockSceneResolution_; }\n'
            '\t\tvoid stockSceneResolution(bool applies) { stockSceneResolution_ = applies; }\n'
            '\t\tHRESULT\t\t\t\t\t\tsetViewport( DX::Viewport* viewport );')
    replace(header, '\t\tD3DSURFACE_DESC\t\t\t\tbackBufferDesc_;',
            '\t\tbool stockSceneResolution_ = false;\n'
            '\t\tD3DSURFACE_DESC\t\t\t\tbackBufferDesc_;')
    replace(header, '\t\t\t\tDX::Viewport\t\t\t\t\tviewport_;',
            '\t\t\t\tDX::Viewport\t\t\t\t\tviewport_;\n'
            '\t\t\t\tfloat stockScreenWidth_, stockScreenHeight_;\n'
            '\t\t\t\tbool stockSceneResolution_;')
    source = 'lib/moo/render_context.cpp'
    replace(source, '    rc->getViewport( &si.viewport_ );',
            '    rc->getViewport( &si.viewport_ );\n'
            '    si.stockScreenWidth_ = rc->screenWidth();\n'
            '    si.stockScreenHeight_ = rc->screenHeight();\n'
            '    si.stockSceneResolution_ = rc->stockSceneResolution();')
    replace(source, '    rc->screenWidth( si.viewport_.Width );\n    rc->screenHeight( si.viewport_.Height );',
            '    rc->screenWidth( static_cast<int>(si.stockScreenWidth_) );\n'
            '    rc->screenHeight( static_cast<int>(si.stockScreenHeight_) );\n'
            '    rc->stockSceneResolution(si.stockSceneResolution_);')
    replace('lib/moo/render_context.ipp',
            '\t\treturn device_->SetRenderTarget(index, surface.pComObject());',
            '\t\tconst HRESULT result = device_->SetRenderTarget(index, surface.pComObject());\n'
            '\t\t// A new primary surface needs its own explicit applicability.\n'
            '\t\tif (SUCCEEDED(result) && index == 0) stockSceneResolution_ = false;\n'
            '\t\treturn result;')

    header = 'lib/moo/render_target.hpp'
    replace(header, '\tvoid setRT2( RenderTarget* rt2 ) { pRT2_ = rt2; }',
            '\tvoid stockSceneResolution(bool applies) { stockSceneResolution_ = applies; }\n'
            '\tbool stockSceneResolution() const { return stockSceneResolution_; }\n'
            '\tvoid setRT2( RenderTarget* rt2 ) { pRT2_ = rt2; }')
    replace(header, '\tbool\t\t\t\treuseZ_;',
            '\tbool stockSceneResolution_ = false;\n\tbool\t\t\t\treuseZ_;')
    source = 'lib/moo/render_target.cpp'
    include(source)
    replace(source, '\tfor (uint32 i = 2; i < MAX_CONCURRENT_RTS; ++i) \n'
            '\t{ \n\t\tMoo::rc().setRenderTarget( i, NULL ); \n\t}\n\n\treturn true;',
            '\tfor (uint32 i = 2; i < MAX_CONCURRENT_RTS; ++i) \n'
            '\t{ \n\t\tMoo::rc().setRenderTarget( i, NULL ); \n\t}\n\n'
            '    DX::Viewport viewport = {0, 0, width_, height_, 0.f, 1.f};\n'
            '    if (FAILED(setStockSceneViewport(&viewport, stockSceneResolution_)))\n'
            '    {\n        rc().popRenderTarget();\n        return false;\n    }\n'
            '\treturn true;')

    header = 'lib/moo/renderer.hpp'
    if 'class StockDynamicResolutionSupport;' not in read(header):
        replace(header, 'class SSAOSupport;', 'class SSAOSupport;\nclass StockDynamicResolutionSupport;')
    replace(header, '    bool                                        m_inited;',
            '    std::auto_ptr<StockDynamicResolutionSupport> m_dynamicResolution;\n'
            '    bool                                        m_inited;')
    source = 'lib/moo/renderer.cpp'
    include(source)
    original_constructor = 'Renderer::Renderer() : m_type(IRendererPipeline::TYPE_FORWARD_SHADING), m_inited(false)'
    previous_constructor = ('Renderer::Renderer() : m_dynamicResolution(new StockDynamicResolutionSupport()), '
                            'm_type(IRendererPipeline::TYPE_FORWARD_SHADING), m_inited(false)')
    constructor = ('Renderer::Renderer() : m_dynamicResolution(new StockDynamicResolutionSupport()), '
                   'm_inited(false), m_type(IRendererPipeline::TYPE_FORWARD_SHADING)')
    replace(source, previous_constructor if previous_constructor in read(source) else original_constructor,
            constructor)
    # Destroy before the callback-visible effect context and renderer registry.
    if '    m_dynamicResolution.reset();' not in read(source):
        replace(source, '    g_pipelineMacroSetting = NULL;',
                '    m_dynamicResolution.reset();\n    g_pipelineMacroSetting = NULL;')

    for source in ('lib/moo/forward_pipeline.cpp', 'lib/moo/deferred_pipeline.cpp'):
        include(source)
        replace(source, '    Moo::rc().setViewport(&viewport);',
                '    setStockSceneViewport(&viewport, true);')
    source = 'lib/moo/deferred_pipeline.cpp'
    replace(source, '    //-- Note: clear only color RTs, depth-stencil surface cleared from the main code.',
            '    DX::Viewport sceneViewport;\n'
            '    Moo::rc().getViewport(&sceneViewport);\n'
            '    setStockSceneViewport(&sceneViewport, true);\n\n'
            '    //-- Note: clear only color RTs, depth-stencil surface cleared from the main code.')
    for source, anchor in (
        ('lib/moo/deferred_decals_manager.cpp',
         '        Moo::rc().setRenderTarget(1, Renderer::instance().pipeline()->gbufferSurface(1));'),
        ('lib/moo/forward_decal.cpp',
         '            Moo::rc().setRenderTarget(0, Renderer::instance().pipeline()->gbufferSurface(2));')):
        include(source)
        replace(source, anchor, anchor + '\n'
                '            DX::Viewport sceneViewport;\n'
                '            Moo::rc().getViewport(&sceneViewport);\n'
                '            setStockSceneViewport(&sceneViewport, true);')

    source = 'client/device_app.cpp'
    include(source)
    replace(source, '    Moo::rc().nextFrame();',
            '    Moo::rc().nextFrame();\n    tickStockDynamicResolution(dRenderTime);',
            '    tickStockDynamicResolution(dRenderTime);')
    replace(source, '    Renderer::instance().pipeline()->begin();',
            '    beginStockDynamicResolutionScene();\n    Renderer::instance().pipeline()->begin();')
    replace(source, '    Moo::rc().setViewport( &viewport );',
            '    setStockSceneViewport(&viewport, true);')
    source = 'client/canvas_app.cpp'
    include(source)
    replace(source, '\tPostProcessing::Manager::instance().draw();\t',
            '\tif (!finishStockDynamicResolutionScene())\n'
            '\t\tERROR_MSG("Dynamic resolution: scene resolve failed\\n");\n'
            '\tPostProcessing::Manager::instance().draw();\t')
    for source, anchor in (('client/gui_app.cpp', '    rp().beginGUIDraw();'),
                           ('client/finale_app.cpp', '    Renderer::instance().pipeline()->end();')):
        include(source)
        replace(source, anchor, '    if (!finishStockDynamicResolutionScene())\n'
                '        ERROR_MSG("Dynamic resolution: scene resolve failed\\n");\n' + anchor)

    source = 'lib/moo/fullscreen_quad.cpp'
    include(source)
    replace(source, '#include "stock_dynamic_resolution.hpp"',
            '#include "stock_dynamic_resolution.hpp"\n#include "stock_effect_adapter.hpp"')
    replace('lib/moo/effect_material.hpp',
            '\tconst ManagedEffectPtr& pEffect() { return pManagedEffect_; }',
            '\tconst ManagedEffectPtr& pEffect() { return pManagedEffect_; }\n'
            '\tconst ManagedEffectPtr& pEffect() const { return pManagedEffect_; }')
    old = '''        if (material.begin())
        {
            for (uint32 i = 0; i < material.numPasses(); ++i)
            {
                material.beginPass(i);
                m_FSQuadMesh.drawEffect(m_vecDclr);
                material.endPass();
            }
            material.end();
        }'''
    replace(source, old, '''        this->draw(material, static_cast<uint>(Moo::rc().screenWidth()),
            static_cast<uint>(Moo::rc().screenHeight()));''')
    replace(source, '        buildFSQuadMesh(quadMesh, width, height);',
            '        buildFSQuadMesh(quadMesh, stockSceneDimension(width), stockSceneDimension(height));')
    header = 'lib/moo/fullscreen_quad.hpp'
    replace(header, '\t\tvoid draw(const EffectMaterial& material, uint width, uint height);',
            '\t\tvoid draw(const EffectMaterial& material, uint width, uint height);\n'
            '\t\tbool drawChecked(const EffectMaterial& material, uint width, uint height);')
    replace(source, '''    void FullscreenQuad::draw(const EffectMaterial& material, uint width, uint height)
    {
        BW_GUARD;

        CustomMesh<Moo::VertexXYZUV> quadMesh(D3DPT_TRIANGLESTRIP);
        quadMesh.resize(4);
        buildFSQuadMesh(quadMesh, stockSceneDimension(width), stockSceneDimension(height));

        if (material.begin())
        {
            for (uint32 i = 0; i < material.numPasses(); ++i)
            {
                material.beginPass(i);
                quadMesh.drawEffect(m_vecDclr);
                material.endPass();
            }
            material.end();
        }
    }''', '''    void FullscreenQuad::draw(const EffectMaterial& material, uint width, uint height)
    {
        this->drawChecked(material, width, height);
    }

    bool FullscreenQuad::drawChecked(const EffectMaterial& material, uint width, uint height)
    {
        BW_GUARD;
        if (!m_vecDclr || !width || !height || !material.begin()) return false;
        CustomMesh<Moo::VertexXYZUV> quadMesh(D3DPT_TRIANGLESTRIP);
        quadMesh.resize(4);
        buildFSQuadMesh(quadMesh, stockSceneDimension(width), stockSceneDimension(height));
        bool success = material.numPasses() != 0;
        for (uint32 i = 0; success && i < material.numPasses(); ++i)
        {
            if (!material.beginPass(i)) { success = false; break; }
            success = SUCCEEDED(quadMesh.drawEffect(m_vecDclr));
            success = material.endPass() && success;
        }
        return material.end() && success;
    }''')
    replace(source, 'void buildFSQuadMesh(MeshType & mesh, uint width, uint height)',
            'void buildFSQuadMesh(MeshType & mesh, uint width, uint height, bool adjustVertex = true)')
    replace(source, '''        for (uint32 i = 0; i < 4; ++i)
        {
            mesh[i].pos_ = mesh[i].pos_ + adjust;
        }''', '''        if (adjustVertex) for (uint32 i = 0; i < 4; ++i)
        {
            mesh[i].pos_ = mesh[i].pos_ + adjust;
        }''')
    replace(source, '''        buildFSQuadMesh(quadMesh, stockSceneDimension(width), stockSceneDimension(height));
        bool success = material.numPasses() != 0;''', '''        const uint effectiveWidth = stockSceneDimension(width);
        const uint effectiveHeight = stockSceneDimension(height);
        ID3DXEffect* effect = material.pEffect()->pEffect();
        const bool stock = wot::is_stock_effect(effect);
        if (stock)
        {
            // #1513 99bdc0: stock vertex shaders preserve clip coordinates and
            // add this half-texel offset to UVs. Do not also shift the vertices.
            const float adjust[2] = {0.5f / effectiveWidth, 0.5f / effectiveHeight};
            if (FAILED(effect->SetFloatArray("g_FSQuadTCAdjust", adjust, 2)))
            {
                material.end();
                return false;
            }
        }
        buildFSQuadMesh(quadMesh, effectiveWidth, effectiveHeight, !stock);
        bool success = material.numPasses() != 0;''')

    # Only scene-space textures opt in. Merely matching the window dimensions
    # is deliberately insufficient: reflection and atlas owners use them too.
    def mark_targets(source, names):
        text = read(source)
        for name in names:
            marker = 'if ({0}) {0}->stockSceneResolution(true);'.format(name)
            if marker in text:
                continue
            pattern = r'(createRenderTarget\(\s*' + re.escape(name) + r'\s*,.*?\);)'
            text, count = re.subn(pattern, r'\1\n    ' + marker, text, flags=re.S)
            if count != 1:
                raise ValueError('Dynamic-resolution target producer changed: ' + source + ':' + name)
        pending[source] = text

    mark_targets('lib/romp/hdr_support.cpp', ('m_hdrRT',
        'm_bloomMaps[i].first', 'm_bloomMaps[i].second'))
    mark_targets('lib/romp/ssao_support.cpp', ('m_rt', 'm_rtBlure', 'm_rtDownsampledDepth'))
    mark_targets('lib/moo/shadow_manager.cpp', ('m_screenSpaceShadowMap',))
    # Explicit viewport changes inside scene passes use the target's current
    # applicability. Fixed luminance targets therefore remain unscaled.
    source = 'lib/romp/hdr_support.cpp'
    include(source)
    text = read(source)
    if 'Moo::rc().setViewport(' in text:
        if text.count('Moo::rc().setViewport(') != 9:
            raise ValueError('Dynamic-resolution HDR viewport owners changed')
        pending[source] = text.replace('Moo::rc().setViewport(', 'setStockSceneViewport(')

    source = 'lib/romp/distortion.cpp'
    include(source)
    replace(source, '\ts_pRenderTexture_->create((uint32)Moo::rc().screenWidth(), (uint32)Moo::rc().screenHeight(), true);',
            '\ts_pRenderTexture_->create((uint32)Moo::rc().screenWidth(), (uint32)Moo::rc().screenHeight(), true);\n'
            '\ts_pRenderTexture_->stockSceneResolution(true);')
    replace(source, '\tMoo::rc().setViewport( &viewport );', '\tsetStockSceneViewport(&viewport);')

    # Stage all edits before writing so an upstream mismatch cannot leave a
    # partially patched engine. The surrounding build owns compilation.
    for name, text in pending.items():
        (root / name).write_text(text)
    application = Path(__file__).resolve().parent
    for source, destination in ((application / 'stock_dynamic_resolution.hpp',
                                 root / 'lib/stock_dynamic_resolution.hpp'),
                                (application.parent / 'include/wot/stock_dynamic_resolution.hpp',
                                 root / 'lib/wot/stock_dynamic_resolution.hpp')):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
