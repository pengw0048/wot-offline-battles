#include "pch.hpp"
#include "stock_scene_settings.hpp"
#include "moo/graphics_settings.hpp"
#include "moo/renderer.hpp"

BW_BEGIN_NAMESPACE
namespace
{
// 01a1e460: configured HIGH and the inactive override are separate from the
// graphics registry's active option. Adding an unchanged default has no callback.
int configuredDecorLevel = 0;
int overrideDecorLevel = -1;

class DecorLevelSetting : public Moo::GraphicsSetting
{
public:
    DecorLevelSetting() : Moo::GraphicsSetting("DECOR_LEVEL",
        "Decor rendering level", 0, false, false)
    {
        // 00a38b50: the integer payloads are the option indices.
        addOption("HIGH", "High", true);
        addOption("MEDIUM", "Medium", true);
        addOption("OFF", "Off", true);
    }
    virtual void onOptionSelected(int option)
    {
        // 00a39170 clamps only negative values. Scene consumers query the
        // effective owner on use, so a change is visible to every live scene.
        configuredDecorLevel = option < 0 ? 0 : option;
    }
};
}
int stockDecorLevel()
{
    // 00a38b20: an explicit owner override precedes the forward-renderer rule.
    if (overrideDecorLevel >= 0) return overrideDecorLevel;
    return Renderer::instance().pipelineType() == IRendererPipeline::TYPE_FORWARD_SHADING ?
        0 : configuredDecorLevel;
}
bool initialiseStockSceneSettings()
{
    auto previous = Moo::GraphicsSetting::getFromLabel("DECOR_LEVEL");
    if (previous)
    {
        if (dynamic_cast<DecorLevelSetting*>(previous.getObject())) return true;
        PyErr_SetString(PyExc_RuntimeError, "Decor setting already has another owner");
        return false;
    }
    Moo::GraphicsSetting::add(new DecorLevelSetting);
    return true;
}
BW_END_NAMESPACE
