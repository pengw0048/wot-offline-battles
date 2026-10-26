#pragma once

#include "wot/stock_dynamic_resolution.hpp"
#include "moo/device_callback.hpp"
#include "moo/effect_material.hpp"
#include "moo/render_target.hpp"

BW_BEGIN_NAMESPACE
extern int StockDynamicResolution_token;

// The renderer owns the service. Render targets opt into scene resolution;
// shadow atlases, reflections, luminance reductions and GUI do not opt in.
class StockDynamicResolutionSupport : public Moo::DeviceCallback
{
public:
    StockDynamicResolutionSupport();
    ~StockDynamicResolutionSupport();
    void tick(float elapsed);
    void beginScene();
    bool finishScene();
    wot::StockDynamicResolution& state() { return state_; }
    const wot::StockDynamicResolution& state() const { return state_; }
    wot::StockDynamicResolutionProjection shaderProjection() const;
    bool ready() const { return material_ && copy_ && copy_->pTexture() != NULL; }
    virtual void createManagedObjects();
    virtual void deleteManagedObjects();
    virtual void createUnmanagedObjects();
    virtual void deleteUnmanagedObjects();
    virtual bool recreateForD3DExDevice() const { return true; }

private:
    wot::StockDynamicResolution state_;
    Moo::EffectMaterialPtr material_;
    Moo::RenderTargetPtr copy_;
    bool framePending_;
    bool constantsRegistered_;
    bool sceneConstantsActive_;
};

StockDynamicResolutionSupport* stockDynamicResolution();
bool initialiseStockDynamicResolutionSetting();
void tickStockDynamicResolution(float elapsed);
void beginStockDynamicResolutionScene();
bool finishStockDynamicResolutionScene();
HRESULT setStockSceneViewport(DX::Viewport* viewport, bool applies);
HRESULT setStockSceneViewport(DX::Viewport* viewport);
uint32 stockSceneDimension(uint32 dimension);
BW_END_NAMESPACE
