#include "pch.hpp"
#include "stock_deferred_buffers.hpp"
#include "stock_dynamic_resolution.hpp"
#include "stock_effect_adapter.hpp"
#include "moo/effect_visual_context.hpp"
#include "moo/fullscreen_quad.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/render_context.hpp"
#include "moo/renderer.hpp"
#include "moo/texture_manager.hpp"
#include "romp/ssao_support.hpp"
#include <algorithm>
#include <cstdlib>

BW_BEGIN_NAMESPACE
namespace
{
StockDeferredBuffers* owner=NULL;
wot::StockDeferredMaterialSettings activeSettings;
wot::StockScreenSpaceShadowSettings activeScreenShadows;
const char* const categoryNames[]={"Terrain","Flora","SpeedTree","Objects","Decal","Overlay"};
const char* const conversionNames[]={"CONVERSION_TERRAIN","CONVERSION_FLORA",
    "CONVERSION_SPEEDTREE","CONVERSION_OBJECTS"};
const char* const copyNames[]={"COPY_TERRAIN","COPY_FLORA","COPY_SPEEDTREE","COPY_OBJECTS"};

bool setConversions(ID3DXEffect* effect,const wot::StockDeferredMaterialConversion& settings)
{
    // a54fa0 is also the real decal material producer; it does not apply the
    // scene conversion passes or manufacture PBS textures from absent maps.
    return effect && SUCCEEDED(effect->SetFloatArray("g_albedoConversions",settings.albedo.data(),4)) &&
        SUCCEEDED(effect->SetFloatArray("g_glossConversions",settings.gloss.data(),4)) &&
        SUCCEEDED(effect->SetFloatArray("g_metallicConversions",settings.metallic.data(),4));
}

class LinearDepthConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()(ID3DXEffect* effect,D3DXHANDLE handle)
    {
        return SUCCEEDED(effect->SetTexture(handle,owner?owner->linearDepth():NULL));
    }
};

class LightingQualityConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()(ID3DXEffect* effect,D3DXHANDLE handle)
    {
        return owner && SUCCEEDED(effect->SetInt(handle,owner->lightingQuality()));
    }
};

class TransmissionTypeConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()(ID3DXEffect* effect,D3DXHANDLE handle)
    {
        return owner && SUCCEEDED(effect->SetInt(handle,owner->transmissionType()));
    }
};

class DepthEffectsConstant : public Moo::EffectConstantValue
{
public:
    virtual bool operator()(ID3DXEffect* effect,D3DXHANDLE handle)
    {
        return owner && SUCCEEDED(effect->SetTexture(handle,owner->depthEffectsTexture()));
    }
};

class SceneProjectionConstant : public Moo::EffectConstantValue
{
public:
    explicit SceneProjectionConstant(bool viewProjection) : viewProjection_(viewProjection) {}
    virtual bool operator()(ID3DXEffect* effect,D3DXHANDLE handle)
    {
        // 99dd20/99ddb0 start from the actual view's projection and only
        // postmultiply a jitter matrix when the main temporal owner is
        // enabled with >1 samples. This backend exposes no temporal AA option.
        const Matrix& matrix=viewProjection_?Moo::rc().viewProjection():Moo::rc().projection();
        return SUCCEEDED(effect->SetMatrix(handle,&matrix));
    }
private:
    bool viewProjection_;
};

class TargetScope
{
public:
    TargetScope() : saved_(Moo::rc().pushRenderTarget()) {}
    ~TargetScope()
    {
        if (saved_)
        {
            Moo::rc().popRenderTarget();
            Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_SCREEN);
        }
    }
    bool bind(const ComObjectWrap<DX::Surface>& surface,DX::Surface* depth,uint width,uint height,bool scene=true)
    {
        if (!saved_ || !surface) return false;
        auto* device=Moo::rc().device();
        if (FAILED(device->SetDepthStencilSurface(depth)) ||
            FAILED(Moo::rc().setRenderTarget(0,surface))) return false;
        for(unsigned i=1;i!=4;++i)
            if (FAILED(Moo::rc().setRenderTarget(i,NULL))) return false;
        DX::Viewport viewport={0,0,width,height,0.f,1.f};
        if (FAILED(setStockSceneViewport(&viewport,scene))) return false;
        Moo::rc().screenWidth(width);Moo::rc().screenHeight(height);
        Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_SCREEN);
        return true;
    }
    bool saved() const { return saved_; }
private:
    bool saved_;
};

Vector4 sceneProjection()
{
    const auto* resolution=stockDynamicResolution();
    const auto& size=Moo::rc().backBufferDesc();
    const auto value=resolution?resolution->shaderProjection():
        wot::StockDynamicResolution().project(size.Width,size.Height);
    return Vector4(value.parameters[0],value.parameters[1],value.parameters[2],value.parameters[3]);
}

Vector4 targetProjection(uint width,uint height)
{
    const auto* resolution=stockDynamicResolution();
    const auto value=resolution?resolution->state().project(width,height):
        wot::StockDynamicResolution().project(width,height);
    return Vector4(value.parameters[0],value.parameters[1],value.parameters[2],value.parameters[3]);
}

void clearTexture(const Moo::EffectMaterialPtr& material,const char* name)
{
    if (!material || !material->pEffect())return;
    ID3DXEffect* binding=material->properties().existingStockBinding();
    if(binding)binding->SetTexture(name,NULL);
    ID3DXEffect* current=material->pEffect()->pEffect();
    if(current && current!=binding && !wot::is_stock_effect(current))current->SetTexture(name,NULL);
}
}

void loadStockDeferredMaterialSettings(DataSectionPtr source,wot::StockDeferredMaterialSettings& settings)
{
    DataSectionPtr root=source?source->openSection("PBSConversions"):NULL;
    if (!root) return;
    settings.useEnvironmentDefaults();
    for(unsigned i=0;i!=6;++i)
    {
        DataSectionPtr category=root->openSection(categoryNames[i]);
        if (!category) continue;
        auto& output=settings.categories[i];
        output.enabled=category->readBool("EnabledConversion",output.enabled);
        const auto read=[&](const char* name,std::array<float,4>& destination)
        {
            const Vector4 fallback(destination[0],destination[1],destination[2],destination[3]);
            const Vector4 value=category->readVector4(name,fallback);
            destination={{value.x,value.y,value.z,value.w}};
        };
        read("AlbedoParams",output.albedo);read("GlossParams",output.gloss);
        read("MetallicParams",output.metallic);
    }
    settings.objects_albedo_multiplier=root->readFloat("ObjectsAlbedoConversion",settings.objects_albedo_multiplier);
}

void activateStockDeferredMaterialSettings(const wot::StockDeferredMaterialSettings& settings)
{
    activeSettings=settings;
}

void loadStockScreenSpaceShadowSettings(DataSectionPtr source,wot::StockScreenSpaceShadowSettings& settings)
{
    DataSectionPtr section=source?source->openSection("SSShadows"):NULL;
    if(!section)return;
    settings.enabled=section->readBool("enable",settings.enabled);
    settings.amplify=section->readFloat("amplify",settings.amplify);
    settings.distance_near=section->readFloat("distanceNear",settings.distance_near);
    settings.distance_far=section->readFloat("distanceFar",settings.distance_far);
    settings.noise=section->readFloat("noise",settings.noise);
}

void activateStockScreenSpaceShadowSettings(const wot::StockScreenSpaceShadowSettings& settings)
{
    activeScreenShadows=settings;
}

bool setStockDeferredDecalConversion(ID3DXEffect* effect)
{
    return setConversions(effect,activeSettings.categories[wot::stock_decal]);
}

bool bindStockDeferredLegacyDecalInputs(ID3DXEffect* effect)
{
    DX::BaseTexture* kinds=owner?owner->objectKindCopy():NULL;
    return kinds && effect && SUCCEEDED(effect->SetTexture("stockDecalObjectKinds",kinds)) &&
        setStockDeferredDecalConversion(effect);
}

void clearStockDeferredLegacyDecalInputs(const Moo::EffectMaterialPtr& material)
{
    clearTexture(material,"stockDecalObjectKinds");
}

bool setStockDeferredTextureScale(ID3DXEffect* effect,const char* parameter,uint width,uint height)
{
    if(!effect || !width || !height)return false;
    const auto* resolution=stockDynamicResolution();
    const wot::StockDynamicResolution state=resolution?resolution->state():wot::StockDynamicResolution();
    const float scale[]={static_cast<float>(state.scene_dimension(static_cast<float>(width),true))/width,
        static_cast<float>(state.scene_dimension(static_cast<float>(height),true))/height};
    return SUCCEEDED(effect->SetFloatArray(parameter,scale,2));
}

bool drawStockDeferredSunLight(const Moo::EffectMaterialPtr& material)
{
    if(!owner || !owner->prepared() || !material ||
        !material->setTechnique("SUN_ST4__FL_ORIG") ||
        !material->properties().select(*material->pEffect()))return false;
    // This backend renders the public ST4/Flora shader families. It has no
    // ST6/Flora-ST6 or screen-space reflection owner/capability. No supported
    // option for those absent renderer families is registered here.
    ID3DXEffect* effect=material->pEffect()->pEffect();
    // The exact DX9 SUN binary optimises this native write out of its layout.
    D3DXHANDLE importance=effect->GetParameterByName(NULL,"g_importanceSamplerEnabled");
    if((importance && FAILED(effect->SetBool(importance,false))) ||
        FAILED(effect->SetBool("g_enableSSAO",owner->ambientOcclusionEnabled())) ||
        FAILED(effect->SetBool("g_enableSSshadows",owner->lightingQuality()<3 && owner->contactShadowsEnabled())) ||
        FAILED(effect->SetBool("g_enableSSR",false)))return false;
    return Moo::rc().fsQuad().drawChecked(*material,
        static_cast<uint>(Moo::rc().screenWidth()),static_cast<uint>(Moo::rc().screenHeight()));
}

StockDeferredBuffers::StockDeferredBuffers(IRendererPipeline& pipeline) : pipeline_(pipeline)
{
    MF_ASSERT(!owner);
    owner=this;
}

StockDeferredBuffers::~StockDeferredBuffers()
{
    deleteUnmanagedObjects();deleteManagedObjects();
    if(owner==this)owner=NULL;
}

bool StockDeferredBuffers::initialise()
{
    createManagedObjects();createUnmanagedObjects();
    return ready();
}

bool StockDeferredBuffers::ready() const
{
    return hardwareDepth_ && depthSurface_ && linearDepth_ && linearDepthSurface_ && reducedDepthMip_ &&
        reducedDepth_ && converted_[0] && converted_[1] && kindCopy_ &&
        depthMaterial_ && conversionMaterial_ && kindCopyMaterial_ &&
        lowPrecisionDepth_ && depthEffectsHalf_ && depthEffectsFull_ &&
        depthEffectsScratch_ && depthEffectsHalfScratch_ && aoMaterial_ &&
        contactShadowMaterial_ && bilateralUpscaleMaterial_ && bilateralBlurMaterial_ &&
        aoNoise_ && aoNoise_->status()==Moo::BaseTexture::STATUS_READY && aoNoise_->pTexture() &&
        horizonNoise_ && horizonNoise_->status()==Moo::BaseTexture::STATUS_READY && horizonNoise_->pTexture();
}

DX::Texture* StockDeferredBuffers::linearDepth() const { return linearDepth_.pComObject(); }

DX::BaseTexture* StockDeferredBuffers::depthEffectsTexture() const
{
    const auto& target=depthQuality_.directHalfResolution()?depthEffectsHalf_:depthEffectsFull_;
    return target?target->pTexture():NULL;
}

bool StockDeferredBuffers::ambientOcclusionEnabled() const
{
    return depthQuality_.ambientOcclusionEnabled() && pipeline_.ssaoSupport() && pipeline_.ssaoSupport()->enable();
}

bool StockDeferredBuffers::contactShadowsEnabled() const
{
    return activeScreenShadows.enabled && depthQuality_.contactShadowsEnabled();
}

void StockDeferredBuffers::createManagedObjects()
{
    if (!Moo::createEffect(depthMaterial_,"shaders/auxiliary/hierarchical_z.fx") ||
        !Moo::createEffect(conversionMaterial_,"shaders/std_effects/g_buffer_old2pbs_conversion.fx") ||
        !Moo::createEffect(kindCopyMaterial_,"shaders/std_effects/wg_deferred_decal.fx") ||
        !Moo::createEffect(aoMaterial_,"shaders/ssao/ao.fx") ||
        !Moo::createEffect(contactShadowMaterial_,"shaders/std_effects/ss_shadows.fx") ||
        !Moo::createEffect(bilateralUpscaleMaterial_,"shaders/auxiliary/bilateral_upsample.fx") ||
        !Moo::createEffect(bilateralBlurMaterial_,"shaders/auxiliary/bilateral_blur.fx"))
        ERROR_MSG("Deferred buffers: original scene effects could not be loaded\n");
    aoNoise_=Moo::TextureManager::instance()->get("system/maps/points_on_sphere.dds",true,false,true);
    horizonNoise_=Moo::TextureManager::instance()->get("system/maps/noise3x3.dds",true,false,true);
    if (!constantsRegistered_)
    {
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"HierarchicalZMap",new LinearDepthConstant);
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"LightingQuality",new LightingQualityConstant);
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"TransmissionType",new TransmissionTypeConstant);
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"DepthBasedEffectsTexture",new DepthEffectsConstant);
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_VIEW,"JitteredProjection",new SceneProjectionConstant(false));
        Moo::rc().effectVisualContext().registerAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_VIEW,"JitteredViewProjection",new SceneProjectionConstant(true));
        constantsRegistered_=true;
    }
}

void StockDeferredBuffers::deleteManagedObjects()
{
    if(constantsRegistered_)
    {
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"HierarchicalZMap");
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"LightingQuality");
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"TransmissionType");
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME,"DepthBasedEffectsTexture");
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_VIEW,"JitteredProjection");
        Moo::rc().effectVisualContext().unregisterAutoConstant(
            Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_VIEW,"JitteredViewProjection");
        constantsRegistered_=false;
    }
    depthMaterial_=NULL;conversionMaterial_=NULL;kindCopyMaterial_=NULL;
    aoMaterial_=NULL;contactShadowMaterial_=NULL;bilateralUpscaleMaterial_=NULL;bilateralBlurMaterial_=NULL;
    aoNoise_=NULL;horizonNoise_=NULL;
}

void StockDeferredBuffers::createUnmanagedObjects()
{
    deleteUnmanagedObjects();
    auto* device=Moo::rc().device();
    const auto& size=Moo::rc().backBufferDesc();
    if(!device || size.Width<2 || size.Height<2)return;
    D3DDEVICE_CREATION_PARAMETERS creation={};
    ComObjectWrap<IDirect3D9> direct3d;
    if(FAILED(device->GetCreationParameters(&creation)) || FAILED(device->GetDirect3D(&direct3d)))return;
    // b2e7c0 maps Wgfx D24S8 + sampled-depth usage to INTZ. The modern
    // DX9 deferred path requires that actual capability; colour-buffer depth
    // is not a valid substitute for the original material channels.
    const D3DFORMAT intz=static_cast<D3DFORMAT>(MAKEFOURCC('I','N','T','Z'));
    if(FAILED(direct3d->CheckDeviceFormat(creation.AdapterOrdinal,creation.DeviceType,size.Format,
        D3DUSAGE_DEPTHSTENCIL,D3DRTYPE_TEXTURE,intz)) ||
        FAILED(device->CreateTexture(size.Width,size.Height,1,D3DUSAGE_DEPTHSTENCIL,intz,
            D3DPOOL_DEFAULT,&hardwareDepth_,NULL)) ||
        FAILED(hardwareDepth_->GetSurfaceLevel(0,&depthSurface_)))
    {
        ERROR_MSG("Deferred buffers: the device could not create the original INTZ scene depth\n");
        deleteUnmanagedObjects();return;
    }
    linearDepth_=Moo::rc().createTexture(size.Width,size.Height,2,D3DUSAGE_RENDERTARGET,
        D3DFMT_R32F,D3DPOOL_DEFAULT,"stock/hierarchical-z");
    reducedDepth_=new Moo::RenderTarget("stock/hierarchical-z reduction");
    if(!linearDepth_ || FAILED(linearDepth_->GetSurfaceLevel(0,&linearDepthSurface_)) ||
        FAILED(linearDepth_->GetSurfaceLevel(1,&reducedDepthMip_)) ||
        !reducedDepth_->create(size.Width/2,size.Height/2,false,D3DFMT_R32F,NULL,D3DFMT_D24S8,false) ||
        !Moo::createRenderTarget(converted_[0],size.Width,size.Height,D3DFMT_A8R8G8B8,
            "stock/converted material",true,false) ||
        !Moo::createRenderTarget(converted_[1],size.Width,size.Height,D3DFMT_A8R8G8B8,
            "stock/converted albedo",true,false))
    {
        ERROR_MSG("Deferred buffers: original scene targets could not be created\n");
        deleteUnmanagedObjects();return;
    }
    const bool alpha=SUCCEEDED(direct3d->CheckDeviceFormat(creation.AdapterOrdinal,creation.DeviceType,
        size.Format,D3DUSAGE_RENDERTARGET,D3DRTYPE_TEXTURE,D3DFMT_A8));
    if(!Moo::createRenderTarget(kindCopy_,size.Width,size.Height,alpha?D3DFMT_A8:D3DFMT_A8R8G8B8,
        "stock/object kind copy",true,false) || !kindCopy_->pTexture() ||
        !converted_[0]->pTexture() || !converted_[1]->pTexture() ||
        !reducedDepth_->pTexture() || !reducedDepth_->depthBuffer())
    {
        ERROR_MSG("Deferred buffers: object-kind target could not be created\n");
        deleteUnmanagedObjects();
    }
    if(reducedDepth_)reducedDepth_->stockSceneResolution(true);
    if(!kindCopy_)return;
    if(!Moo::createRenderTarget(lowPrecisionDepth_,size.Width/2,size.Height/2,D3DFMT_R16F,
            "stock/depth effects low precision",true,false) ||
        !Moo::createRenderTarget(depthEffectsHalf_,size.Width/2,size.Height/2,D3DFMT_A8R8G8B8,
            "stock/depth effects accumulate",true,false) ||
        !Moo::createRenderTarget(depthEffectsHalfScratch_,size.Width/2,size.Height/2,D3DFMT_A8R8G8B8,
            "stock/depth effects half scratch",true,false) ||
        !Moo::createRenderTarget(depthEffectsFull_,size.Width,size.Height,D3DFMT_A8R8G8B8,
            "stock/depth effects upscale",true,false) ||
        !Moo::createRenderTarget(depthEffectsScratch_,size.Width,size.Height,D3DFMT_A8R8G8B8,
            "stock/depth effects scratch",true,false))
    {
        ERROR_MSG("Deferred buffers: depth effects targets could not be created\n");
        deleteUnmanagedObjects();return;
    }
    for(const auto& target:{lowPrecisionDepth_,depthEffectsHalf_,depthEffectsHalfScratch_,
        depthEffectsFull_,depthEffectsScratch_})
        if(!target->pTexture())
        {
            ERROR_MSG("Deferred buffers: depth effects texture could not be allocated\n");
            deleteUnmanagedObjects();return;
        }
}

void StockDeferredBuffers::deleteUnmanagedObjects()
{
    end();
    clearTexture(depthMaterial_,"g_hardwareDepthMap");clearTexture(depthMaterial_,"g_prevMipMap");
    clearTexture(conversionMaterial_,"g_buffer0");clearTexture(conversionMaterial_,"g_buffer2");
    clearTexture(kindCopyMaterial_,"g_texSource");
    clearTexture(aoMaterial_,"g_LowPrecisionDepthTexture");
    clearTexture(contactShadowMaterial_,"g_LowPrecisionDepthTexture");
    clearTexture(bilateralUpscaleMaterial_,"g_halfResTexture");
    clearTexture(bilateralUpscaleMaterial_,"g_maskTexture");
    clearTexture(bilateralBlurMaterial_,"g_srcMap");clearTexture(bilateralBlurMaterial_,"g_maskTexture");
    lowPrecisionDepth_=NULL;depthEffectsHalf_=NULL;depthEffectsHalfScratch_=NULL;
    depthEffectsFull_=NULL;depthEffectsScratch_=NULL;
    kindCopy_=NULL;converted_[0]=NULL;converted_[1]=NULL;reducedDepth_=NULL;
    reducedDepthMip_=NULL;linearDepthSurface_=NULL;linearDepth_=NULL;
    depthSurface_=NULL;hardwareDepth_=NULL;previousDepth_=NULL;
}

bool StockDeferredBuffers::begin()
{
    if(frameActive_)return true;
    if(!ready() || FAILED(Moo::rc().device()->GetDepthStencilSurface(&previousDepth_)))return false;
    if(FAILED(Moo::rc().device()->SetDepthStencilSurface(depthSurface_.pComObject())))
    {
        previousDepth_=NULL;return false;
    }
    frameActive_=true;
    prepared_=false;
    kindCopied_=false;
    return true;
}

bool StockDeferredBuffers::end()
{
    if(!frameActive_)return true;
    frameActive_=false;
    prepared_=false;
    kindCopied_=false;
    const bool restored=SUCCEEDED(Moo::rc().device()->SetDepthStencilSurface(previousDepth_.pComObject()));
    previousDepth_=NULL;
    return restored;
}

bool StockDeferredBuffers::select(const Moo::EffectMaterialPtr& material,const char* technique)
{
    return material && material->setTechnique(technique) && material->properties().select(*material->pEffect());
}

bool StockDeferredBuffers::draw(const Moo::EffectMaterialPtr& material)
{
    return Moo::rc().fsQuad().drawChecked(*material,
        static_cast<uint>(Moo::rc().screenWidth()),static_cast<uint>(Moo::rc().screenHeight()));
}

bool StockDeferredBuffers::fillLinearDepth()
{
    const auto& size=Moo::rc().backBufferDesc();
    const Vector4 projection=sceneProjection();
    {
        TargetScope target;
        if(!target.bind(linearDepthSurface_,depthSurface_.pComObject(),size.Width,size.Height) ||
            !select(depthMaterial_,"UNPACK"))return false;
        ID3DXEffect* effect=depthMaterial_->pEffect()->pEffect();
        if(FAILED(effect->SetTexture("g_hardwareDepthMap",hardwareDepth_.pComObject())) ||
            FAILED(effect->SetVector("g_hardwareDepthMapDRRParams",&projection)) || !draw(depthMaterial_))return false;
    }
    {
        if(!reducedDepth_->push())return false;
        // a9d5bd0 downsamples through a distinct target and copies to mip 1;
        // sampling and rendering different mips of one D3D9 texture aliases.
        bool success=select(depthMaterial_,"DOWNSAMPLE");
        if(success)
        {
            ID3DXEffect* effect=depthMaterial_->pEffect()->pEffect();
            success=SUCCEEDED(effect->SetTexture("g_prevMipMap",linearDepth_.pComObject())) &&
                SUCCEEDED(effect->SetVector("g_prevMipMapDRRParams",&projection)) &&
                SUCCEEDED(Moo::rc().device()->Clear(0,NULL,D3DCLEAR_TARGET|D3DCLEAR_ZBUFFER|D3DCLEAR_STENCIL,
                    0xffffffffu,1.f,0)) && draw(depthMaterial_);
        }
        reducedDepth_->pop();
        Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_SCREEN);
        ComObjectWrap<DX::Surface> reduced;
        if(!success || FAILED(reducedDepth_->pSurface(reduced)) ||
            FAILED(Moo::rc().device()->StretchRect(reduced.pComObject(),NULL,
                reducedDepthMip_.pComObject(),NULL,D3DTEXF_NONE)))return false;
    }
    return true;
}

bool StockDeferredBuffers::convertMaterials()
{
    if(!activeSettings.sceneConversionEnabled())return true;
    const auto& size=Moo::rc().backBufferDesc();
    ComObjectWrap<DX::Surface> material,albedo;
    if(FAILED(converted_[0]->pSurface(material)) || FAILED(converted_[1]->pSurface(albedo)))return false;
    {
        TargetScope target;
        if(!target.bind(material,depthSurface_.pComObject(),size.Width,size.Height) ||
            FAILED(Moo::rc().setRenderTarget(1,albedo)))return false;
        for(unsigned category=0;category!=4;++category)
        {
            const auto& settings=activeSettings.categories[category];
            if(!settings.enabled)continue;
            if(!select(conversionMaterial_,conversionNames[category]))return false;
            ID3DXEffect* effect=conversionMaterial_->pEffect()->pEffect();
            std::array<float,12> params;
            std::copy(settings.albedo.begin(),settings.albedo.end(),params.begin());
            std::copy(settings.gloss.begin(),settings.gloss.end(),params.begin()+4);
            std::copy(settings.metallic.begin(),settings.metallic.end(),params.begin()+8);
            if(FAILED(effect->SetFloatArray("g_params",params.data(),12)) ||
                (category==wot::stock_objects && FAILED(effect->SetFloat("gAlbedoMultiplier",
                    activeSettings.objects_albedo_multiplier))) || !draw(conversionMaterial_))return false;
        }
    }
    {
        TargetScope target;
        ComObjectWrap<DX::Surface> first=pipeline_.gbufferSurface(0),last=pipeline_.gbufferSurface(2);
        if(!target.bind(first,depthSurface_.pComObject(),size.Width,size.Height) ||
            FAILED(Moo::rc().setRenderTarget(1,last)))return false;
        bool all=true;
        for(unsigned i=0;i!=4;++i)all=all&&activeSettings.categories[i].enabled;
        for(unsigned i=0;i!=(all?1u:4u);++i)
        {
            if(!all && !activeSettings.categories[i].enabled)continue;
            if(!select(conversionMaterial_,all?"COPY_ALL_OPAQUE":copyNames[i]))return false;
            ID3DXEffect* effect=conversionMaterial_->pEffect()->pEffect();
            if(FAILED(effect->SetTexture("g_buffer0",converted_[0]->pTexture())) ||
                FAILED(effect->SetTexture("g_buffer2",converted_[1]->pTexture())) || !draw(conversionMaterial_))return false;
        }
    }
    return true;
}

bool StockDeferredBuffers::blurDepthEffects(const Moo::RenderTargetPtr& input,
    const Moo::RenderTargetPtr& scratch,bool bilateral,bool lowQuality,bool opaqueOnly,float scale,float sharpness)
{
    const uint width=input->width(),height=input->height();
    DX::Surface* depth=width==Moo::rc().backBufferDesc().Width?
        depthSurface_.pComObject():reducedDepth_->depthBuffer();
    const Vector4 screen(width/scale,height/scale,scale/width,scale/height);
    const Vector4 projection=targetProjection(width,height);
    const Moo::RenderTargetPtr sources[]={input,scratch};
    const Moo::RenderTargetPtr destinations[]={scratch,input};
    const char* techniques[]={"HORIZONTAL_PASS","VERTICAL_PASS"};
    for(unsigned pass=0;pass!=2;++pass)
    {
        ComObjectWrap<DX::Surface> surface;
        if(FAILED(destinations[pass]->pSurface(surface)))return false;
        TargetScope target;
        if(!target.bind(surface,depth,width,height) || !select(bilateralBlurMaterial_,techniques[pass]))return false;
        ID3DXEffect* effect=bilateralBlurMaterial_->pEffect()->pEffect();
        // 9d62e0: the non-TAA path has no prepared depth/mask target. Both
        // passes consume the actual HZ semantic and preserve the same layout.
        if(FAILED(effect->SetInt("g_depthMode",2)) ||
            FAILED(effect->SetBool("g_useMaskRT",false)) ||
            FAILED(effect->SetBool("g_useBilateralFilter",bilateral)) ||
            FAILED(effect->SetBool("g_bilateralFilterLowQuality",lowQuality)) ||
            FAILED(effect->SetBool("g_bilateralFilterConsiderSurfacePlane",bilateral)) ||
            FAILED(effect->SetBool("g_processOnlyOpaque",opaqueOnly)) ||
            FAILED(effect->SetFloat("g_sharpness",sharpness)) ||
            FAILED(effect->SetFloat("g_blurMip",0.f)) ||
            FAILED(effect->SetVector("g_blurScreen",&screen)) ||
            FAILED(effect->SetVector("g_srcMapDRRParams",&projection)) ||
            FAILED(effect->SetTexture("g_maskTexture",NULL)) ||
            FAILED(effect->SetTexture("g_srcMap",sources[pass]->pTexture())) || !draw(bilateralBlurMaterial_))return false;
    }
    return true;
}

bool StockDeferredBuffers::fillDepthEffects()
{
    const uint width=depthEffectsHalf_->width(),height=depthEffectsHalf_->height();
    const Vector4 halfProjection=targetProjection(width,height);
    ComObjectWrap<DX::Surface> surface;
    // COPY_DEPTH is a physical downsample of the complete HZ allocation.
    // It does not apply DRR to its source UVs. The active rectangle survives
    // in the R16F texture with the same physical coordinate convention.
    if(FAILED(lowPrecisionDepth_->pSurface(surface)))return false;
    {
        TargetScope target;
        if(!target.bind(surface,reducedDepth_->depthBuffer(),width,height,false) ||
            !select(aoMaterial_,"COPY_DEPTH") ||
            !Moo::rc().fsQuad().drawChecked(*aoMaterial_,width,height,true))return false;
    }
    surface=NULL;
    if(FAILED(depthEffectsHalf_->pSurface(surface)))return false;
    {
        TargetScope target;
        if(!target.bind(surface,reducedDepth_->depthBuffer(),width,height) ||
            FAILED(Moo::rc().device()->Clear(0,NULL,D3DCLEAR_TARGET,0x00ff00ffu,1.f,0)))return false;
        // a6b110's original neutral RGBA=(1,0,1,0). AO writes R; contact
        // shadows write B. ScalarTAA's master defaults disabled (99d860).
        if(ambientOcclusionEnabled())
        {
            const auto& settings=pipeline_.ssaoSupport()->settings();
            const bool horizon=depthQuality_.horizonBasedOcclusion();
            const bool low=depthQuality_.lowQualityOcclusion();
            const Vector4 packed0(settings.m_radius*(low&&!horizon?.65f:1.f),
                settings.m_fade*(low&&horizon?.5f:1.f),0.f,0.f);
            const Vector4 packed1(settings.m_amplify*(low?.65f:1.f),0.f,0.f,0.f);
            if(!select(aoMaterial_,horizon?"HBAO":"SSAO"))return false;
            ID3DXEffect* effect=aoMaterial_->pEffect()->pEffect();
            if(FAILED(effect->SetBool("g_halfResolution",true)) ||
                FAILED(effect->SetBool("g_lowQuality",low)) ||
                FAILED(effect->SetBool("g_useLowPrecisionDepth",true)) ||
                FAILED(effect->SetBool("g_enableTAA",false)) ||
                FAILED(effect->SetFloat("g_taaSampleIndex",0.f)) ||
                FAILED(effect->SetVector("g_packedParams0",&packed0)) ||
                FAILED(effect->SetVector("g_packedParams1",&packed1)) ||
                FAILED(effect->SetTexture("g_LowPrecisionDepthTexture",lowPrecisionDepth_->pTexture())) ||
                FAILED(effect->SetTexture("g_noiseMapSSAO",(horizon?horizonNoise_:aoNoise_)->pTexture())) ||
                !draw(aoMaterial_))return false;
        }
        if(contactShadowsEnabled())
        {
            const Vector4 parameters(activeScreenShadows.amplify,0.f,
                static_cast<float>(std::rand())*3.051851e-05f,0.f);
            if(!select(contactShadowMaterial_,"TRACE"))return false;
            ID3DXEffect* effect=contactShadowMaterial_->pEffect()->pEffect();
            if(FAILED(effect->SetBool("g_lowQuality",depthQuality_.lowQualityContactShadows())) ||
                FAILED(effect->SetBool("g_enableTAA",false)) ||
                FAILED(effect->SetVector("g_ssShadowsParams",&parameters)) ||
                FAILED(effect->SetTexture("g_LowPrecisionDepthTexture",lowPrecisionDepth_->pTexture())) ||
                !draw(contactShadowMaterial_))return false;
        }
    }
    if(depthQuality_.directHalfResolution())
        return blurDepthEffects(depthEffectsHalf_,depthEffectsHalfScratch_,false,false,false,1.f,0.f);
    surface=NULL;
    if(FAILED(depthEffectsFull_->pSurface(surface)))return false;
    const auto& size=Moo::rc().backBufferDesc();
    {
        TargetScope target;
        if(!target.bind(surface,depthSurface_.pComObject(),size.Width,size.Height) ||
            !select(bilateralUpscaleMaterial_,"BILATERAL_UPSCALE"))return false;
        ID3DXEffect* effect=bilateralUpscaleMaterial_->pEffect()->pEffect();
        // a76af0 usage mask 3 includes both AO and contact shadows. The
        // highest lighting option alone enables the bilateral reconstruction.
        if(FAILED(effect->SetInt("g_usageMask",3)) ||
            FAILED(effect->SetBool("g_writeDepth",true)) ||
            FAILED(effect->SetBool("g_useBilinearWeights",false)) ||
            FAILED(effect->SetBool("g_useBilateralUpscale",depthQuality_.lighting==0)) ||
            FAILED(effect->SetBool("g_processOnlyOpaque",true)) ||
            FAILED(effect->SetBool("g_useMaskRT",false)) ||
            FAILED(effect->SetTexture("g_maskTexture",NULL)) ||
            FAILED(effect->SetTexture("g_halfResTexture",depthEffectsHalf_->pTexture())) ||
            FAILED(effect->SetVector("g_halfResTextureDRRParams",&halfProjection)) ||
            !draw(bilateralUpscaleMaterial_))return false;
    }
    // a447d0 / a451f0: high options use a two-pass depth-aware filter,
    // sharpness .1*400, scale 1.2. The direct half path above is Gaussian.
    return blurDepthEffects(depthEffectsFull_,depthEffectsScratch_,true,
        depthQuality_.lowQualityOcclusion(),true,1.2f,40.f);
}

bool StockDeferredBuffers::prepare()
{
    // 9c3d60 builds depth first, converts legacy resources next, then draws
    // deferred decals. All three consumers see the same completed scene.
    prepared_=frameActive_ && ready() && fillLinearDepth() && convertMaterials() && fillDepthEffects();
    return prepared_;
}

DX::BaseTexture* StockDeferredBuffers::objectKindCopy()
{
    if(!frameActive_ || !ready())return NULL;
    // Deferred decals preserve alpha in every scene target. Both public
    // decals and traces therefore consume the same immutable kind snapshot.
    if(kindCopied_)return kindCopy_->pTexture();
    const auto& size=Moo::rc().backBufferDesc();
    ComObjectWrap<DX::Surface> surface;
    if(FAILED(kindCopy_->pSurface(surface)))return NULL;
    TargetScope target;
    if(!target.bind(surface,depthSurface_.pComObject(),size.Width,size.Height) ||
        !select(kindCopyMaterial_,"COPY_TEXTURE"))return NULL;
    ID3DXEffect* effect=kindCopyMaterial_->pEffect()->pEffect();
    const Vector4 projection=sceneProjection();
    if(FAILED(effect->SetTexture("g_texSource",pipeline_.gbufferTexture(0))) ||
        FAILED(effect->SetVector("g_texSourceDRRParams",&projection)) || !draw(kindCopyMaterial_))return NULL;
    kindCopied_=true;
    return kindCopy_->pTexture();
}

StockDeferredBuffers* stockDeferredBuffers() { return owner; }
BW_END_NAMESPACE
