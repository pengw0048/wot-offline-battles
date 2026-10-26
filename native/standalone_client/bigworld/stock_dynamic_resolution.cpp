#include "pch.hpp"
#include "stock_dynamic_resolution.hpp"
#include "stock_effect_adapter.hpp"
#include "app_config.hpp"

#include "moo/effect_visual_context.hpp"
#include "moo/fullscreen_quad.hpp"
#include "moo/graphics_settings.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/render_context.hpp"
#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
int StockDynamicResolution_token = 1;
namespace
{
StockDynamicResolutionSupport* owner = NULL;

class ResolutionConstant : public Moo::EffectConstantValue
{
public:
    ResolutionConstant(const StockDynamicResolutionSupport& support, bool downsample)
        : support_(support), downsample_(downsample) {}
    virtual bool operator()(ID3DXEffect* effect, D3DXHANDLE handle)
    {
        const D3DSURFACE_DESC& output = Moo::rc().backBufferDesc();
        if (!output.Width || !output.Height) return false;
        const auto value = support_.shaderProjection();
        return SUCCEEDED(effect->SetValue(handle,
            downsample_ ? value.downsample_parameters.data() : value.parameters.data(),
            downsample_ ? sizeof(float) * 16 : sizeof(float) * 4));
    }
private:
    const StockDynamicResolutionSupport& support_;
    bool downsample_;
};

class AutoscaleSetting : public Moo::GraphicsSetting
{
public:
    AutoscaleSetting() : Moo::GraphicsSetting("DRR_AUTOSCALER_ENABLED",
        "DRR Autoscaler Enabled", 0, false, false)
    {
        this->addOption("OFF", "Off", true);
        this->addOption("ON", "On", true);
    }
    virtual void onOptionSelected(int option)
    {
        owner->state().enable(option != 0, Moo::rc().frameTimestamp());
    }
};

bool requireOwner()
{
    if (owner && owner->ready()) return true;
    PyErr_SetString(PyExc_RuntimeError, "Dynamic resolution renderer is unavailable");
    return false;
}
}

StockDynamicResolutionSupport::StockDynamicResolutionSupport()
    : framePending_(false), constantsRegistered_(false), sceneConstantsActive_(false)
{
    MF_ASSERT(owner == NULL);
    owner = this;
    DataSectionPtr root = AppConfig::instance().pRoot();
    DataSectionPtr section = root ? root->openSection("renderer/DRRAutoscaler") : NULL;
    if (section)
    {
        auto config = state_.config();
        config.critical_fps = static_cast<float>(section->readInt("criticalFPS", 24));
        config.safe_fps = config.critical_fps * section->readFloat("safeFPSMultiplier", 1.25f);
        config.time_step = section->readFloat("timeStep", 1.f);
        config.correction_step = section->readFloat("correctionStep", 0.05f);
        config.correction_big_step = section->readFloat("correctionBigStep", 0.1f);
        config.minimum_scale = section->readFloat("minScale", 0.5f);
        state_.configure(config);
    }
}

StockDynamicResolutionSupport::~StockDynamicResolutionSupport()
{
    this->deleteUnmanagedObjects();
    this->deleteManagedObjects();
    owner = NULL;
}

StockDynamicResolutionSupport* stockDynamicResolution() { return owner; }

void StockDynamicResolutionSupport::createManagedObjects()
{
    this->deleteManagedObjects();
    const auto type = Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME;
    Moo::rc().effectVisualContext().registerAutoConstant(type, "DRRParams",
        new ResolutionConstant(*this, false));
    Moo::rc().effectVisualContext().registerAutoConstant(type, "DRRParamsDownsample",
        new ResolutionConstant(*this, true));
    constantsRegistered_ = true;
    if (!Moo::createEffect(material_, "shaders/drr/wg_drr.fx"))
        ERROR_MSG("Dynamic resolution: original upsample effect could not be loaded\n");
    if (material_) material_->hTechnique("drr");
}

void StockDynamicResolutionSupport::deleteManagedObjects()
{
    if (constantsRegistered_)
    {
        const auto type = Moo::EffectVisualContext::AUTO_CONSTANT_TYPE_PER_FRAME;
        Moo::rc().effectVisualContext().unregisterAutoConstant(type, "DRRParams");
        Moo::rc().effectVisualContext().unregisterAutoConstant(type, "DRRParamsDownsample");
        constantsRegistered_ = false;
    }
    material_ = NULL;
}

void StockDynamicResolutionSupport::createUnmanagedObjects()
{
    const D3DSURFACE_DESC& output = Moo::rc().backBufferDesc();
    if (output.Width && output.Height && !Moo::createRenderTarget(copy_,
        output.Width, output.Height, output.Format, "stock/DRR back buffer copy", true, true))
        ERROR_MSG("Dynamic resolution: back buffer copy could not be created\n");
}

void StockDynamicResolutionSupport::deleteUnmanagedObjects()
{
    framePending_ = false;
    sceneConstantsActive_ = false;
    // SetTexture retains a COM reference. Release it before the default-pool
    // render target is retired for a device reset or renderer teardown.
    if (material_ && material_->pEffect())
    {
        // end() returns ManagedEffect::pEffect() to its Definition. The
        // Properties still own the actual (possibly pre-reload) Binding.
        ID3DXEffect* binding=material_->properties().existingStockBinding();
        if (binding) binding->SetTexture("g_bbCopyMap",NULL);
        ID3DXEffect* current=material_->pEffect()->pEffect();
        if (current && current!=binding && !wot::is_stock_effect(current))
            current->SetTexture("g_bbCopyMap",NULL);
    }
    copy_ = NULL;
}

void StockDynamicResolutionSupport::tick(float elapsed)
{
    state_.tick(elapsed, Moo::rc().frameTimestamp());
}

void StockDynamicResolutionSupport::beginScene()
{
    framePending_ = true;
    sceneConstantsActive_ = true;
    Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_FRAME);
}

wot::StockDynamicResolutionProjection StockDynamicResolutionSupport::shaderProjection() const
{
    const D3DSURFACE_DESC& output = Moo::rc().backBufferDesc();
    if (sceneConstantsActive_) return state_.project(output.Width, output.Height);
    // The public script post-processing chain owns arbitrary output targets.
    // It starts after this scene has been resolved to the full output surface.
    return wot::StockDynamicResolution().project(output.Width, output.Height);
}

bool StockDynamicResolutionSupport::finishScene()
{
    if (!framePending_) return true;
    framePending_ = false;
    struct CompleteConstants
    {
        bool& active;
        ~CompleteConstants()
        {
            active = false;
            Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_FRAME);
        }
    } complete = {sceneConstantsActive_};
    const D3DSURFACE_DESC& output = Moo::rc().backBufferDesc();
    DX::Viewport viewport = {0, 0, output.Width, output.Height, 0.f, 1.f};
    // Complete the scene before SimpleGUI/Scaleform and present. The copy is a
    // GPU transfer only; the original drr technique performs the upsampling.
    if (FAILED(setStockSceneViewport(&viewport, false))) return false;
    Moo::rc().screenWidth(output.Width);
    Moo::rc().screenHeight(output.Height);
    Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_SCREEN);
    if (state_.scale() == 1.f) return true;
    if (!this->ready() || !Moo::copyBackBuffer(copy_.getObject())) return false;
    if (!material_->properties().select(*material_->pEffect())) return false;
    ID3DXEffect* effect = material_->pEffect()->pEffect();
    if (FAILED(effect->SetTexture("g_bbCopyMap", copy_->pTexture()))) return false;
    Moo::rc().effectVisualContext().updateSharedConstants(Moo::CONSTANTS_PER_FRAME);
    return Moo::rc().fsQuad().drawChecked(*material_, output.Width, output.Height);
}

HRESULT setStockSceneViewport(DX::Viewport* viewport, bool applies)
{
    DX::Viewport actual = *viewport;
    if (applies && owner)
    {
        actual.Width = owner->state().scene_dimension(static_cast<float>(actual.Width), true);
        actual.Height = owner->state().scene_dimension(static_cast<float>(actual.Height), true);
    }
    const HRESULT result = Moo::rc().setViewport(&actual);
    if (SUCCEEDED(result)) Moo::rc().stockSceneResolution(applies);
    return result;
}

HRESULT setStockSceneViewport(DX::Viewport* viewport)
{
    return setStockSceneViewport(viewport, Moo::rc().stockSceneResolution());
}

uint32 stockSceneDimension(uint32 dimension)
{
    return owner && Moo::rc().stockSceneResolution() ?
        owner->state().scene_dimension(static_cast<float>(dimension), true) : dimension;
}

void tickStockDynamicResolution(float elapsed) { if (owner) owner->tick(elapsed); }
void beginStockDynamicResolutionScene() { if (owner) owner->beginScene(); }
bool finishStockDynamicResolutionScene() { return !owner || owner->finishScene(); }

bool initialiseStockDynamicResolutionSetting()
{
    if (!requireOwner()) return false;
    auto previous = Moo::GraphicsSetting::getFromLabel("DRR_AUTOSCALER_ENABLED");
    if (previous)
    {
        if (dynamic_cast<AutoscaleSetting*>(previous.getObject())) return true;
        PyErr_SetString(PyExc_RuntimeError, "Dynamic resolution setting already has another owner");
        return false;
    }
    Moo::GraphicsSetting::add(new AutoscaleSetting());
    return true;
}

static PyObject* py_getDRRScale(PyObject* args)
{
    if (!PyArg_ParseTuple(args, ":getDRRScale") || !requireOwner()) return NULL;
    return Script::getData(owner->state().scale());
}
PY_MODULE_FUNCTION(getDRRScale, BigWorld)

static PyObject* py_isDRRAutoscalingEnabled(PyObject* args)
{
    if (!PyArg_ParseTuple(args, ":isDRRAutoscalingEnabled") || !requireOwner()) return NULL;
    return Script::getData(owner->state().enabled());
}
PY_MODULE_FUNCTION(isDRRAutoscalingEnabled, BigWorld)

static PyObject* py_setDRRScale(PyObject* args)
{
    PyObject* object;
    float value;
    if (!PyArg_ParseTuple(args, "O:setDRRScale", &object) ||
        Script::setData(object, value, "setDRRScale argument 1") != 0 || !requireOwner()) return NULL;
    owner->state().set_scale(value);
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(setDRRScale, BigWorld)

static PyObject* py_pauseDRRAutoscaling(PyObject* args)
{
    PyObject* object;
    bool value;
    if (!PyArg_ParseTuple(args, "O:pauseDRRAutoscaling", &object) ||
        Script::setData(object, value, "pauseDRRAutoscaling argument 1") != 0 || !requireOwner()) return NULL;
    owner->state().pause(value, Moo::rc().frameTimestamp());
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(pauseDRRAutoscaling, BigWorld)
BW_END_NAMESPACE
