#!/usr/bin/env python3
"""Connect original ARIE shaders to public Moo's existing material/draw owners."""
import argparse
from pathlib import Path


def replace_once(text, old, new, label):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError("Unexpected public engine source for " + label)
    return text.replace(old, new, 1)


def apply_file(root, relative, changes):
    path = root / relative
    text = path.read_text()
    for old, new, label in changes:
        text = replace_once(text, old, new, label)
    path.write_text(text)


PROPERTIES = r"""class Properties : public BW::map< D3DXHANDLE, EffectPropertyPtr >
    {
    public:
        Properties() : definition_(NULL), compileMark_(0) {}
        Properties(const Properties& other)
            : BW::map<D3DXHANDLE, EffectPropertyPtr>(other), definition_(NULL), compileMark_(0) {}
        Properties& operator=(const Properties& other)
        {
            if (this != &other)
            {
                BW::map<D3DXHANDLE, EffectPropertyPtr>::operator=(other);
                resetBinding();
            }
            return *this;
        }
        void clear()
        {
            BW::map<D3DXHANDLE, EffectPropertyPtr>::clear();
            resetBinding();
        }
        bool select(ManagedEffect& effect) const
        {
            return effect.selectMaterialBinding(binding_, definition_, compileMark_);
        }
    private:
        void resetBinding()
        {
            binding_ = NULL;
            definition_ = NULL;
            compileMark_ = 0;
        }
        mutable ComObjectWrap<ID3DXEffect> binding_;
        mutable ComObjectWrap<ID3DXEffect> definition_;
        mutable ManagedEffect::CompileMark compileMark_;
    };"""

SELECT_BINDING = r"""
// The draw queue can switch materials inside an open pass. Each Properties owns
// its private ARIE Binding; the immutable Definition and shared context are reused.
bool ManagedEffect::selectMaterialBinding(ComObjectWrap<ID3DXEffect>& binding,
    ComObjectWrap<ID3DXEffect>& definition, CompileMark& mark)
{
    stockBindingValid_ = false;
    if (!wot::is_stock_effect(pEffect_.pComObject()))
    {
        stockBindingValid_ = true;
        return true;
    }
    if (!binding || definition.pComObject() != pEffect_.pComObject() || mark != compileMark_)
    {
        ID3DXEffect* instance = NULL;
        HRESULT hr = wot::create_stock_effect_material(pEffect_.pComObject(), &instance);
        if (FAILED(hr))
        {
            ERROR_MSG("ManagedEffect: material binding clone failed for %s (%08lx)\n",
                resourceID_.c_str(), static_cast<unsigned long>(hr));
            return false;
        }
        binding = instance;
        instance->Release();
        definition = pEffect_;
        mark = compileMark_;
    }
    if (activeBinding_.pComObject() == binding.pComObject())
    {
        stockBindingValid_ = true;
        return true;
    }
    const bool wasBegun = numPasses_ != 0;
    const bool hadPass = stockPass_ != UINT_MAX;
    const uint32 pass = stockPass_;
    if (wasBegun)
    {
        if (hadPass && FAILED(pEffect()->EndPass())) return false;
        if (FAILED(pEffect()->End())) return false;
    }
    activeBinding_ = binding;
    if (wasBegun)
    {
        if (FAILED(pEffect()->SetTechnique(currentTechnique())) ||
            FAILED(pEffect()->Begin(&numPasses_, D3DXFX_DONOTSAVESTATE)))
        {
            numPasses_ = 0;
            stockPass_ = UINT_MAX;
            return false;
        }
        if (hadPass)
        {
            stockPass_ = pass;
            // Properties and object constants are written before CommitChanges.
            if (FAILED(pEffect()->BeginPass(pass))) return false;
        }
    }
    stockBindingValid_ = true;
    return true;
}

"""


def patch(root):
    moo = Path("lib/moo")
    apply_file(root, moo / "effect_material.hpp", [
        ("typedef BW::map< D3DXHANDLE, EffectPropertyPtr > Properties;", PROPERTIES, "material binding owner"),
    ])
    apply_file(root, moo / "managed_effect.hpp", [
        ("ID3DXEffect* pEffect();", "ID3DXEffect* pEffect();\n\tbool selectMaterialBinding(ComObjectWrap<ID3DXEffect>& binding,\n\t\tComObjectWrap<ID3DXEffect>& definition, CompileMark& mark);", "binding selection API"),
        ("pEffect_->SetValue( h, &val, sizeof(T) );", "pEffect()->SetValue( h, &val, sizeof(T) );", "typed object constant destination"),
        ("ComObjectWrap<ID3DXEffect>\tpEffect_;", "ComObjectWrap<ID3DXEffect>\tpEffect_;\n\tComObjectWrap<ID3DXEffect> activeBinding_;\n\tuint32 stockPass_ = UINT_MAX;\n\tbool stockBindingValid_ = true;", "active binding lifecycle"),
    ])
    apply_file(root, moo / "managed_effect.ipp", [
        ("return pEffect_.pComObject();", "return activeBinding_ ? activeBinding_.pComObject() : pEffect_.pComObject();", "active effect access"),
    ])
    path = root / moo / "managed_effect.ipp"
    text = path.read_text()
    if "pEffect_->" in text:
        if text.count("pEffect_->") != 12:
            raise RuntimeError("Unexpected ManagedEffect inline API calls")
        text = text.replace("pEffect_->", "pEffect()->")
        path.write_text(text)
    path = root / moo / "managed_effect.cpp"
    text = path.read_text()
    text = replace_once(text, '#include "effect_visual_context.hpp"', '#include "effect_visual_context.hpp"\n#include "stock_effect_adapter.hpp"', "adapter include")
    # patch_shader_compiler.py already supplies the real legacy compiler and
    # diagnostics. Keep that path for actual upstream source FX resources.
    old = ""
    if "HRESULT hr = D3DXCreateEffect(" in text:
        start = text.index("HRESULT hr = D3DXCreateEffect(")
        end = text.index(";", start) + 1
        old = text[start:end]
    if "ComObjectWrap<ID3DXBuffer> creationErrors;" not in text:
        text = replace_once(text, "ID3DXEffect* pEffect = NULL;",
            "ID3DXEffect* pEffect = NULL;\n\tComObjectWrap<ID3DXBuffer> creationErrors;", "effect creation diagnostics")
    new = r'''HRESULT hr;
    if (bin->len() >= 8 && memcmp(bin->cdata(), "ARIEDX9 ", 8) == 0)
    {
        std::string error;
        hr = wot::create_stock_effect(rc().device(), EffectManager::instance().pStateManager(),
            bin->cdata(), bin->len(), rc().effectVisualContext().stockEffectContext(), &pEffect, error);
        if (FAILED(hr)) ERROR_MSG("ARIE effect %s: %s\n", resourceID_.c_str(), error.c_str());
    }
    else
    {
        hr = D3DXCreateEffect(rc().device(), bin->cdata(), bin->len(), NULL, NULL,
            0, rc().effectVisualContext().pEffectPool(), &pEffect, &creationErrors);
    }'''
    text = replace_once(text, old, new, "real ARIE factory")
    text = replace_once(text, " \tpEffect_ =  pEffect;", "\tactiveBinding_ = NULL;\n\tstockPass_ = UINT_MAX;\n \tpEffect_ =  pEffect;", "reload binding reset")
    text = replace_once(text, "(*pValue)( pEffect_.pComObject(), it->first );", "(*pValue)( pEffect(), it->first );", "automatic constants destination")
    start = text.index("bool ManagedEffect::begin( bool setAutoConstants )")
    end = text.index("/**", text.index("bool ManagedEffect::commitChanges()", start))
    block = text[start:end]
    block = block.replace("pEffect_->", "pEffect()->")
    block = replace_once(block, "\tMF_ASSERT( pEffect_ != NULL );\n\n\tD3DXHANDLE hCurrentTechnique",
        "\tMF_ASSERT( pEffect_ != NULL );\n\tif (!stockBindingValid_) { activeBinding_ = NULL; stockBindingValid_ = true; return false; }\n\n\tD3DXHANDLE hCurrentTechnique", "binding begin failure")
    block = replace_once(block, "return SUCCEEDED( pEffect()->CommitChanges() );",
        "return stockBindingValid_ && SUCCEEDED( pEffect()->CommitChanges() );", "binding commit failure")
    if "stockPass_ = pass;" not in block:
        block = block.replace("return SUCCEEDED( pEffect()->BeginPass( pass ) );", "stockPass_ = pass;\n\treturn SUCCEEDED( pEffect()->BeginPass( pass ) );")
        block = block.replace("return SUCCEEDED( pEffect()->EndPass() );", "stockPass_ = UINT_MAX;\n\treturn SUCCEEDED( pEffect()->EndPass() );")
        block = block.replace("return SUCCEEDED( pEffect()->End() );", "const HRESULT result = pEffect()->End();\n\tactiveBinding_ = NULL;\n\tstockPass_ = UINT_MAX;\n\tstockBindingValid_ = true;\n\treturn SUCCEEDED(result);")
    text = text[:start] + block + text[end:]
    if SELECT_BINDING not in text:
        text = text.replace("bool ManagedEffect::begin( bool setAutoConstants )", SELECT_BINDING + "bool ManagedEffect::begin( bool setAutoConstants )", 1)
    path.write_text(text)
    apply_file(root, moo / "effect_material.cpp", [
        ("ID3DXEffect*\t\t\t\tpEffect = pManagedEffect_->pEffect();", "if (!properties_.select(*pManagedEffect_)) return;\n\tID3DXEffect*\t\t\t\tpEffect = pManagedEffect_->pEffect();", "immediate material owner"),
    ])
    path = root / moo / "complex_effect_material.hpp"
    text = path.read_text()
    old = "BW::map< D3DXHANDLE, SmartPointer<EffectProperty> >*& outMaterialProperties"
    new = "EffectMaterial::Properties*& outMaterialProperties"
    if old in text:
        if text.count(old) != 2:
            raise RuntimeError("Unexpected complex material signatures")
        text = text.replace(old, new)
    elif text.count(new) != 2:
        raise RuntimeError("Missing complex material signatures")
    path.write_text(text)
    apply_file(root, moo / "draw_context.cpp", [
        ("typedef BW::map< D3DXHANDLE, SmartPointer<EffectProperty> > MaterialProperties;",
         "typedef EffectMaterial::Properties MaterialProperties;", "queued material owner"),
        ("ID3DXEffect*\tpEffect = effect->pEffect();",
         "if (!properties->select(*effect)) return;\n\t\t\tID3DXEffect*\tpEffect = effect->pEffect();", "queue binding switch"),
        ("if (active_.materialProperties_ != rop->materialProperties_)",
         "if (isEffectChanged || active_.materialProperties_ != rop->materialProperties_)", "rebind after technique close"),
        ("rop->effect_->commitChanges();", "if (!rop->effect_->commitChanges()) return;", "failed shader draw containment"),
    ])
    patch_context(root)
    patch_environment(root)
    patch_instancing(root)
    patch_resource_names(root)


def patch_context(root):
    moo = Path("lib/moo")
    apply_file(root, moo / "effect_visual_context.hpp", [
        ('#include "moo/image_texture.hpp"', '#include "moo/image_texture.hpp"\n#include "stock_effect_adapter.hpp"', "shared context include"),
        ("void updateSharedConstants(uint32 flags);",
         "void updateSharedConstants(uint32 flags);\n\tconst std::shared_ptr<wot::StockEffectSharedContext>& stockEffectContext() const { return stockEffectContext_; }", "context access"),
        ("ComObjectWrap<ID3DXEffectPool>\tpEffectPoolHandle_;",
         "std::shared_ptr<wot::StockEffectSharedContext> stockEffectContext_;\n\tComObjectWrap<ID3DXEffectPool>\tpEffectPoolHandle_;", "context ownership"),
    ])
    path = root / moo / "effect_visual_context.cpp"
    text = path.read_text()
    for kind in ("FRAME", "SCREEN", "VIEW"):
        old = "\t\tpEffectPools_[AUTO_CONSTANT_TYPE_PER_%s]->setAutoConstants();" % kind
        text = replace_once(text, old, "\t\t// ARIE shared buffers are updated together below (%s)." % kind, "shared " + kind)
    old = """\t\t// ARIE shared buffers are updated together below (VIEW).
\t}
}"""
    new = """\t\t// ARIE shared buffers are updated together below (VIEW).
\t}
    std::string error;
    if (stockEffectContext_ && !wot::update_stock_effect_shared_constants(*stockEffectContext_, flags, error))
        ERROR_MSG("ARIE shared constants: %s\\n", error.c_str());
}"""
    text = replace_once(text, old, new, "shared update dispatch")
    start_marker = "\t//-- create effect pool."
    end_marker = "\tsuccess &= createNoiseMap();"
    if start_marker in text:
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        text = text[:start] + r'''    // One real UniformContext serves every material in this visual context.
    stockEffectContext_ = std::make_shared<wot::StockEffectSharedContext>(
        [this](const std::string& semantic, ID3DXEffect* effect, D3DXHANDLE handle) -> bool
        {
            EffectConstantValuePtr* mapping = getMapping(BW::StringRef(semantic.data(), semantic.size()), false);
            if (!mapping || !mapping->exists()) return false;
            return !effect || (**mapping)(effect, handle);
        }, [](const std::string& error) { ERROR_MSG("ARIE draw: %s\n", error.c_str()); });

''' + text[end:]
        text = text.replace("\tEffectManager& em = EffectManager::instance();\n", "", 1)
    text = replace_once(text, "EffectVisualContext::~EffectVisualContext()\n{\n\tinitConstants();",
        "EffectVisualContext::~EffectVisualContext()\n{\n\tif (stockEffectContext_) stockEffectContext_->detach_providers();\n\tinitConstants();", "shared provider teardown")
    path.write_text(text)


def patch_environment(root):
    # These types are included by public engine headers. The pinned build
    # flattens library linkage into bwclient, so target usage requirements do
    # not reach all header consumers. Publish the owned interface headers
    # under the engine's existing shared lib include root.
    application = Path(__file__).resolve().parent
    for source, destination in (
        (application.parent / 'include/wot/stock_fog.hpp',
         root / 'lib/wot/stock_fog.hpp'),
        (application / 'stock_environment_constants.hpp',
         root / 'lib/stock_environment_constants.hpp'),
        (application / 'stock_effect_adapter.hpp',
         root / 'lib/stock_effect_adapter.hpp'),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text())
    apply_file(root, Path("lib/moo/fog_helper.hpp"), [
        ("#define FOG_HELPER_HPP", '#define FOG_HELPER_HPP\n#include "wot/stock_fog.hpp"', "native fog ABI"),
        ("FogParams m_fogParams;", "FogParams m_fogParams;\n\t\twot::StockFogSettings stockFogSettings_;", "native fog current environment"),
        ("const FogParams& fogParams() const;", "const FogParams& fogParams() const;\n\t\tconst wot::StockFogSettings& stockFogSettings() const { return stockFogSettings_; }\n\t\tvoid stockFogSettings(const wot::StockFogSettings& settings) { stockFogSettings_ = settings; }", "fog settings publication"),
    ])
    apply_file(root, Path("lib/romp/enviro_minder.hpp"), [
        ('#define ENVIRO_MINDER_HPP', '#define ENVIRO_MINDER_HPP\n#include "stock_environment_constants.hpp"', "environment fog reader"),
        ("void loadHDR(const DataSectionPtr& data, bool loadFromExternal);", "wot::StockFogSettings stockFogSettings_;\n    void loadStockFog(const DataSectionPtr& data, bool loadFromExternal);\n    void loadHDR(const DataSectionPtr& data, bool loadFromExternal);", "environment settings owner"),
    ])
    path = root / "lib/romp/enviro_minder.cpp"
    text = path.read_text()
    text = replace_once(text, '#include "moo/renderer.hpp"', '#include "moo/renderer.hpp"\n#include "moo/fog_helper.hpp"', "fog helper owner include")
    text = replace_once(text, "        loadTimeOfDay(pDS, loadFromExternal);", "        loadTimeOfDay(pDS, loadFromExternal);\n        loadStockFog(pDS, loadFromExternal);", "load original fog environment")
    text = replace_once(text, "    s_activatedEM_ = this;", "    s_activatedEM_ = this;\n    Moo::FogHelper::pInstance()->stockFogSettings(stockFogSettings_);", "activate fog owner")
    marker = "void EnviroMinder::loadHDR(const DataSectionPtr& data, bool loadFromExternal)"
    reader = r'''// #1513 0x00a35a90..0x00a35b03 loads the original skyGradientDome root;
// 0x00a83a60 selects its Fog child, with 0x00a55bd0 field/default semantics.
void EnviroMinder::loadStockFog(const DataSectionPtr& data, bool loadFromExternal)
{
    const BW::string resource = data->readString("skyGradientDome");
    if (!resource.empty() && loadFromExternal)
    {
        DataSectionPtr source = BWResource::openSection(resource);
        if (source) loadStockFogSettings(source, stockFogSettings_);
        else ERROR_MSG("EnviroMinder: cannot load original fog environment '%s'\n", resource.c_str());
    }
    else loadStockFogSettings(data, stockFogSettings_);
    if (s_activatedEM_ == this)
        Moo::FogHelper::pInstance()->stockFogSettings(stockFogSettings_);
}

'''
    if reader not in text:
        text = replace_once(text, marker, reader + marker, "environment fog resource loader")
    path.write_text(text)
    apply_file(root, Path("lib/moo/effect_visual_context.cpp"), [
        ('#include "fog_helper.hpp"', '#include "fog_helper.hpp"\n#include "renderer.hpp"', "actual render pipeline"),
        ("FogParams fog = FogHelper::pInstance()->fogParams();", r'''if (wot::is_stock_effect(pEffect))
        {
            const bool forward = Renderer::instance().pipelineType() == IRendererPipeline::TYPE_FORWARD_SHADING;
            const auto selected = wot::stock_fog_selected(FogHelper::pInstance()->stockFogSettings(), forward);
            const auto fog = wot::stock_fog_parameters(selected);
            return SUCCEEDED(pEffect->SetValue(constantHandle, fog.data(), static_cast<UINT>(sizeof(fog))));
        }
        FogParams fog = FogHelper::pInstance()->fogParams();''', "native nine-vector fog producer"),
        ("TimeConstant(): time_( 0.0f ) {}", "TimeConstant(): time_( 0.0f ), previousTime_( 0.0f ) {}", "time previous frame storage"),
        ("\t\ttime_ += dTime;", "\t\tpreviousTime_ = time_;\n\t\ttime_ += dTime;", "native time tick order"),
        ("pEffect->SetFloat( constantHandle, time_ );", r'''if (wot::is_stock_effect(pEffect))
        {
            // #1513 0x009b0cb0 / 0x009b04e0: current, previous, zero, zero.
            const Vector4 time(time_, previousTime_, 0, 0);
            return SUCCEEDED(pEffect->SetVector(constantHandle, &time));
        }
        pEffect->SetFloat( constantHandle, time_ );''', "native time vector producer"),
        ("\tfloat time_;", "\tfloat time_;\n\tfloat previousTime_;", "time owner fields"),
        ("pEffect->SetArrayRange( constantHandle, 0, nConstants );", "if (!wot::is_stock_effect(pEffect))\n\t\t\t\tpEffect->SetArrayRange( constantHandle, 0, nConstants );", "native palette byte write"),
    ])


INSTANCE_PROVIDER = r'''// #1513 0x009aff50 consumes the full 64-byte matrices published by the
// indexed-instance draw packet. Skinning remains the separate WorldPalette ABI.
class StockInstancingStreamConstant : public EffectConstantValue
{
public:
    bool operator()(ID3DXEffect* effect, D3DXHANDLE handle)
    {
        const auto& context = rc().effectVisualContext();
        const Matrix* matrices = context.stockInstancingMatrices();
        if (!matrices) return true;
        return SUCCEEDED(effect->SetMatrixArray(handle, matrices, context.stockInstancingCount()));
    }
};

// #1513 0x009af3f0 packs the four view-space corner XY values into two rows.
class StockCameraDirsVSConstant : public EffectConstantValue
{
public:
    bool operator()(ID3DXEffect* effect, D3DXHANDLE handle)
    {
        const Moo::Camera& camera = rc().camera();
        const float scale = camera.farPlane() / camera.nearPlanePoint(0, 0).length();
        const Vector3 tl = scale * camera.nearPlanePoint(-1, 1);
        const Vector3 tr = scale * camera.nearPlanePoint(1, 1);
        const Vector3 bl = scale * camera.nearPlanePoint(-1, -1);
        const Vector3 br = scale * camera.nearPlanePoint(1, -1);
        const Matrix packed(Vector4(tl.x, tl.y, tr.x, tr.y),
            Vector4(bl.x, bl.y, br.x, br.y), Vector4(0,0,0,0), Vector4(0,0,0,0));
        return SUCCEEDED(effect->SetMatrix(handle, &packed));
    }
};

'''


def patch_instancing(root):
    apply_file(root, Path("lib/moo/effect_visual_context.hpp"), [
        ("const Matrix*\t\tworldMatrix() const { return worldMatrix_; }", r'''const Matrix*        worldMatrix() const { return worldMatrix_; }
    void stockInstancingStream(const Matrix* matrices, uint32 count)
    {
        stockInstancingMatrices_ = matrices;
        stockInstancingCount_ = count > 16 ? 16 : count;
    }
    const Matrix* stockInstancingMatrices() const { return stockInstancingMatrices_; }
    uint32 stockInstancingCount() const { return stockInstancingCount_; }''', "matrix stream context publication"),
        ("const Matrix*\t\t\tworldMatrix_;", "const Matrix*\t\t\tworldMatrix_;\n\tconst Matrix* stockInstancingMatrices_ = NULL;\n\tuint32 stockInstancingCount_ = 0;", "matrix stream transient owner"),
    ])
    apply_file(root, Path("lib/moo/effect_visual_context.cpp"), [
        ("class EffectVisualContext::TimeConstant : public EffectConstantValue", INSTANCE_PROVIDER + "class EffectVisualContext::TimeConstant : public EffectConstantValue", "native instance and camera producers"),
        ('''\tDEFINE_AUTO_CONST(PER_OBJECT,\t"World")''', '''    DEFINE_AUTO_CONST(PER_OBJECT, "InstancingStream") = new StockInstancingStreamConstant;
\tDEFINE_AUTO_CONST(PER_OBJECT,\t"World")''', "instance semantic registration"),
        ('''\tDEFINE_AUTO_CONST(PER_VIEW,\t\t"CameraDirs")''', '''    DEFINE_AUTO_CONST(PER_VIEW, "CameraDirsWS") = new CameraDirsConstant;
    DEFINE_AUTO_CONST(PER_VIEW, "CameraDirsVS") = new StockCameraDirsVSConstant;
\tDEFINE_AUTO_CONST(PER_VIEW,\t\t"CameraDirs")''', "native corner semantic registration"),
    ])
    apply_file(root, Path("lib/moo/draw_context.cpp"), [
        ("VertexBuffer\t\t\tinstancedVB_;", "VertexBuffer\t\t\tinstancedVB_;\n\tVertexBuffer stockInstanceIndexVB_;\n\tBW::vector<Matrix> stockInstanceMatrices_;", "indexed instance payload owners"),
        ("*nextInstanceMatrix_++ = *rop->instanceData_->matrix();", "stockInstanceMatrices_.push_back(*rop->instanceData_->matrix());\n\t\t\t*nextInstanceMatrix_++ = *rop->instanceData_->matrix();", "capture complete instance matrices"),
        ("\t\t\tinstancingChainRef_ = NULL;\n\t\t}\n\t}\n\n\tvoid flushInstancedRenderOps()",
         "\t\t\tinstancingChainRef_ = NULL;\n\t\t\tstockInstanceMatrices_.clear();\n\t\t}\n\t}\n\n\tvoid flushInstancedRenderOps()", "instance capture lifetime"),
        ("\t\t\tprocessRop( irop->reference_, irop->numInstances_, offset );", r'''            if (wot::is_stock_effect(irop->reference_->effect_->pEffect()))
            {
                // #1513 0x0093da30: every matrix is retained and consumed in
                // packets of at most 16, with zero-based FLOAT1 indices in stream 9.
                for (uint32 consumed = 0; consumed < irop->numInstances_; consumed += 16)
                {
                    const uint32 remaining = irop->numInstances_ - consumed;
                    const uint32 count = remaining > 16 ? 16 : remaining;
                    effectVisualContext_.stockInstancingStream(&stockInstanceMatrices_[offset + consumed], count);
                    processRop(irop->reference_, count, offset + consumed);
                    effectVisualContext_.stockInstancingStream(NULL, 0);
                }
            }
            else processRop( irop->reference_, irop->numInstances_, offset );''', "bounded native matrix packets"),
        ("\t\tinstancedRenderOps_.clear();", "\t\tinstancedRenderOps_.clear();\n\t\tstockInstanceMatrices_.clear();", "instance packet teardown"),
        ("\t\t\tinstancedVB_.set( InstancingStream::STREAM_NUMBER,\n\t\t\t\toffset * InstancingStream::ELEMENT_SIZE,\n\t\t\t\tInstancingStream::ELEMENT_SIZE );", r'''            if (wot::is_stock_effect(rop->effect_->pEffect()))
            {
                if (!stockInstanceIndexVB_.valid()) return;
                stockInstanceIndexVB_.set(InstancingStream::STREAM_NUMBER, 0, sizeof(float));
            }
            else instancedVB_.set( InstancingStream::STREAM_NUMBER,
                offset * InstancingStream::ELEMENT_SIZE,
                InstancingStream::ELEMENT_SIZE );''', "original FLOAT1 index stream"),
    ])
    path = root / "lib/moo/draw_context.cpp"
    text = path.read_text()
    old = "\t\tBW_GUARD;\n\t\tif (!instancedVB_.valid() && rc().isHWInstancingAvailable())"
    new = r'''        BW_GUARD;
        if (!stockInstanceIndexVB_.valid() && rc().isHWInstancingAvailable())
        {
            if (SUCCEEDED(stockInstanceIndexVB_.create(16 * sizeof(float), D3DUSAGE_WRITEONLY,
                0, D3DPOOL_DEFAULT, "vertex buffer/original instance indices")))
            {
                float* indices = NULL;
                if (SUCCEEDED(stockInstanceIndexVB_.lock(0, 16 * sizeof(float), reinterpret_cast<void**>(&indices), 0)))
                {
                    for (uint32 i = 0; i < 16; ++i) indices[i] = static_cast<float>(i);
                    stockInstanceIndexVB_.unlock();
                }
                else stockInstanceIndexVB_.release();
            }
        }
        if (!instancedVB_.valid() && rc().isHWInstancingAvailable())'''
    text = replace_once(text, old, new, "native index GPU allocation")
    old = "\t\tBW_GUARD;\n\t\tif (instancedVB_.valid())"
    new = "\t\tBW_GUARD;\n\t\tif (stockInstanceIndexVB_.valid()) stockInstanceIndexVB_.release();\n\t\tif (instancedVB_.valid())"
    text = replace_once(text, old, new, "native index device teardown")
    path.write_text(text)


def patch_resource_names(root):
    apply_file(root, Path("lib/moo/effect_helpers.cpp"), [
        ("\t\treturn objectName;", r'''        // #1513 0x009c9690 adds the active backend before the FXO suffix.
        // Keep the macro-produced infix; never choose another quality variant.
        const BW::string nativeName = BWResource::removeExtension(objectName) + ".dx9.fxo";
        if (BWResource::fileExists(nativeName)) return nativeName;
        return objectName;''', "backend effect resource suffix"),
        ("BW::StringRef fileName = BWResource::removeExtension( resourceID );", r'''BW::StringRef fileName = BWResource::removeExtension( resourceID );
        if (BWResource::getExtension(fileName) == "dx9")
            fileName = BWResource::removeExtension(fileName);''', "reverse backend resource name"),
    ])
    path = root / "lib/moo/effect_compiler.cpp"
    text = path.read_text()
    old = "    SimpleMutexHolder guard( s_runtimeCompileMutex_ );\n    EffectCompiler compiler( true );"
    new = r'''    SimpleMutexHolder guard( s_runtimeCompileMutex_ );
    // Original compiled shaders have no distributable FX source. Decode their
    // actual ARIE member, leaving legacy source freshness/compilation intact.
    BinaryPtr original = EffectHelpers::loadEffectBinary(resourceID);
    if (original && original->len() >= 8 && memcmp(original->cdata(), "ARIEDX9 ", 8) == 0)
        return original;
    EffectCompiler compiler( true );'''
    # loadOrCompile is installed by patch_shader_compiler.py. A pristine-source
    # contract probe may omit that separate patch; the production build does not.
    if "BinaryPtr EffectCompiler::loadOrCompile(" in text:
        text = replace_once(text, old, new, "original compiled shader source owner")
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine_root", type=Path)
    args = parser.parse_args()
    patch(args.engine_root)


if __name__ == "__main__":
    main()
