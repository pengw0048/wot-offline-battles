#pragma once

#include "wot/stock_deferred_buffers.hpp"
#include "moo/device_callback.hpp"
#include "moo/effect_material.hpp"
#include "moo/render_target.hpp"
#include "moo/com_object_wrap.hpp"
#include "resmgr/datasection.hpp"

BW_BEGIN_NAMESPACE
class IRendererPipeline;

void loadStockDeferredMaterialSettings(DataSectionPtr source,
    wot::StockDeferredMaterialSettings& settings);
void activateStockDeferredMaterialSettings(const wot::StockDeferredMaterialSettings& settings);
void loadStockScreenSpaceShadowSettings(DataSectionPtr source,wot::StockScreenSpaceShadowSettings& settings);
void activateStockScreenSpaceShadowSettings(const wot::StockScreenSpaceShadowSettings& settings);
bool setStockDeferredDecalConversion(ID3DXEffect* effect);
bool bindStockDeferredLegacyDecalInputs(ID3DXEffect* effect);
void clearStockDeferredLegacyDecalInputs(const Moo::EffectMaterialPtr& material);
bool setStockDeferredTextureScale(ID3DXEffect* effect,const char* parameter,uint width,uint height);
bool drawStockDeferredSunLight(const Moo::EffectMaterialPtr& material);

// One owner belongs to the actual deferred pipeline. Its depth texture and
// linear-depth semantic are shared by scene reconstruction and decal draws.
class StockDeferredBuffers : public Moo::DeviceCallback
{
public:
    explicit StockDeferredBuffers(IRendererPipeline& pipeline);
    ~StockDeferredBuffers();
    bool initialise();
    bool begin();
    bool prepare();
    bool end();
    bool ready() const;
    void setLightingQuality(int option) { lightingSelection_.quality=option;depthQuality_.lighting=option; }
    void setShadowQuality(int option) { depthQuality_.shadows=option; }
    int lightingQuality() const { return lightingSelection_.quality; }
    int transmissionType() { return lightingSelection_.transmissionType(); }
    bool prepared() const { return prepared_; }
    DX::Texture* linearDepth() const;
    DX::BaseTexture* depthEffectsTexture() const;
    bool ambientOcclusionEnabled() const;
    bool contactShadowsEnabled() const;
    DX::BaseTexture* objectKindCopy();
    virtual void createManagedObjects();
    virtual void deleteManagedObjects();
    virtual void createUnmanagedObjects();
    virtual void deleteUnmanagedObjects();
    virtual bool recreateForD3DExDevice() const { return true; }
private:
    bool select(const Moo::EffectMaterialPtr& material,const char* technique);
    bool draw(const Moo::EffectMaterialPtr& material);
    bool convertMaterials();
    bool fillLinearDepth();
    bool fillDepthEffects();
    bool blurDepthEffects(const Moo::RenderTargetPtr& input,const Moo::RenderTargetPtr& scratch,
        bool bilateral,bool lowQuality,bool opaqueOnly,float scale,float sharpness);
    IRendererPipeline& pipeline_;
    wot::StockDeferredLightingSelection lightingSelection_;
    wot::StockDepthEffectQuality depthQuality_;
    ComObjectWrap<DX::Texture> hardwareDepth_;
    ComObjectWrap<DX::Surface> depthSurface_;
    ComObjectWrap<DX::Surface> previousDepth_;
    ComObjectWrap<DX::Texture> linearDepth_;
    ComObjectWrap<DX::Surface> linearDepthSurface_;
    ComObjectWrap<DX::Surface> reducedDepthMip_;
    Moo::RenderTargetPtr reducedDepth_;
    Moo::RenderTargetPtr converted_[2];
    Moo::RenderTargetPtr kindCopy_;
    Moo::RenderTargetPtr lowPrecisionDepth_;
    Moo::RenderTargetPtr depthEffectsHalf_;
    Moo::RenderTargetPtr depthEffectsFull_;
    Moo::RenderTargetPtr depthEffectsScratch_;
    Moo::RenderTargetPtr depthEffectsHalfScratch_;
    Moo::EffectMaterialPtr depthMaterial_;
    Moo::EffectMaterialPtr conversionMaterial_;
    Moo::EffectMaterialPtr kindCopyMaterial_;
    Moo::EffectMaterialPtr aoMaterial_;
    Moo::EffectMaterialPtr contactShadowMaterial_;
    Moo::EffectMaterialPtr bilateralUpscaleMaterial_;
    Moo::EffectMaterialPtr bilateralBlurMaterial_;
    Moo::BaseTexturePtr aoNoise_,horizonNoise_;
    bool constantsRegistered_=false;
    bool frameActive_=false;
    bool prepared_=false;
    bool kindCopied_=false;
};
StockDeferredBuffers* stockDeferredBuffers();
BW_END_NAMESPACE
