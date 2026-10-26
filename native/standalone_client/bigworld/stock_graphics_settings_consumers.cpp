#ifndef WOT_STOCK_GRAPHICS_SETTINGS_CONSUMERS_PORTABLE
#include "pch.hpp"
#include "stock_graphics_settings_consumers.hpp"
#include "wot/stock_graphics_settings_consumers.hpp"
#include "moo/base_texture.hpp"
#include "moo/device_callback.hpp"
#include "moo/effect_material.hpp"
#include "moo/fullscreen_quad.hpp"
#include "moo/graphics_settings.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/render_context.hpp"
#include "moo/render_target.hpp"
#include "moo/renderer.hpp"
#include "moo/texture_manager.hpp"
#include "pyscript/script.hpp"
#endif
#include <array>
#include <memory>

BW_BEGIN_NAMESPACE
int StockGraphicsSettingsConsumers_token = 1;
namespace
{
class Binoculars : public Moo::DeviceCallback
{
public:
    wot::StockBinocularsState state;
    bool closed = false;
    std::array<BW::string, 3> textureNames;
    std::array<Moo::BaseTexturePtr, 3> textures;
    Moo::EffectMaterialPtr material;
    Moo::RenderTargetPtr source;
    Moo::RenderTargetPtr graded;

    ~Binoculars() { close(); }
    void close()
    {
        closed = true;
        state.enabled = false;
        deleteUnmanagedObjects();
        deleteManagedObjects();
        resetTextures();
    }
    void resetTextures()
    {
        // 006f7cd0 releases all three references, including resource names.
        for (size_t i = 0; i != textures.size(); ++i)
        {
            textures[i] = NULL;
            textureNames[i].clear();
        }
    }
    void setTexture(size_t index, const BW::string& name)
    {
        textureNames[index] = name;
        textures[index] = Moo::TextureManager::instance()->get(name);
    }
    virtual void createManagedObjects()
    {
        if (closed) return;
        if (!Moo::createEffect(material, "shaders/post_processing/wg_sniper_lens.fx"))
            ERROR_MSG("Binoculars: original lens effect could not be loaded\n");
    }
    virtual void deleteManagedObjects() { material = NULL; }
    virtual void createUnmanagedObjects()
    {
        deleteUnmanagedObjects();
        if (closed || Renderer::instance().pipelineType() == IRendererPipeline::TYPE_FORWARD_SHADING)
            return;
        const auto& desc = Moo::rc().backBufferDesc();
        if (!desc.Width || !desc.Height) return;
        if (!Moo::createRenderTarget(source, desc.Width, desc.Height, desc.Format,
                "stock/Binoculars scene copy", true, true) ||
            !Moo::createRenderTarget(graded, desc.Width, desc.Height, desc.Format,
                "stock/Binoculars colour grading", true, true))
            ERROR_MSG("Binoculars: scene render targets could not be created\n");
    }
    virtual void deleteUnmanagedObjects()
    {
        // ID3DXEffect::SetTexture retains COM references. Unbind before the
        // default-pool targets are released, including a lost-device reset.
        if (material && material->pEffect() && material->pEffect()->pEffect())
        {
            ID3DXEffect* fx = material->pEffect()->pEffect();
            fx->SetTexture("g_backgroundTexture", NULL);
            fx->SetTexture("g_vignetteTexture", NULL);
            fx->SetTexture("g_distortNormalMap", NULL);
            fx->SetTexture("g_rgbCubeTexture", NULL);
        }
        source = NULL;
        graded = NULL;
    }
    virtual bool recreateForD3DExDevice() const { return true; }

    bool select(const char* technique)
    {
        if (!material || !material->setTechnique(technique)) return false;
        return material->properties().select(*material->pEffect());
    }
    bool drawQuad()
    {
        const auto& desc = Moo::rc().backBufferDesc();
        return Moo::rc().fsQuad().drawChecked(*material, desc.Width, desc.Height);
    }
    bool draw()
    {
        // 006f87d0: no background/vignette or disabled is a legitimate no-draw.
        if (closed || !state.enabled || !textures[0]) return true;
        if (!material) return false;
        if (Renderer::instance().pipelineType() == IRendererPipeline::TYPE_FORWARD_SHADING)
        {
            if (!select("SNIPER_LENS_SIMPLE")) return false;
            ID3DXEffect* fx = material->pEffect()->pEffect();
            if (FAILED(fx->SetFloat("centerX", state.center_x)) ||
                FAILED(fx->SetFloat("centerY", state.center_y)) ||
                FAILED(fx->SetTexture("g_vignetteTexture", textures[0]->pTexture())) ||
                FAILED(fx->SetTexture("g_backgroundTexture", NULL)) ||
                FAILED(fx->SetTexture("g_rgbCubeTexture", NULL)) ||
                FAILED(fx->SetTexture("g_distortNormalMap", NULL))) return false;
            return drawQuad();
        }
        if (!source || !graded || !textures[1] ||
            !source->pTexture() || !graded->pTexture()) return false;
        if (!Moo::copyBackBuffer(source.getObject())) return false;
        DX::BaseTexture* background = source->pTexture();
        if (state.lens_effects)
        {
            if (!textures[2] || !select("RGB_CUBE")) return false;
            ID3DXEffect* fx = material->pEffect()->pEffect();
            if (FAILED(fx->SetTexture("g_vignetteTexture", NULL)) ||
                FAILED(fx->SetTexture("g_backgroundTexture", source->pTexture())) ||
                FAILED(fx->SetTexture("g_rgbCubeTexture", textures[2]->pTexture())) ||
                FAILED(fx->SetTexture("g_distortNormalMap", NULL))) return false;
            if (!graded->push()) return false;
            const bool drawn = drawQuad();
            graded->pop();
            if (!drawn) return false;
            background = graded->pTexture();
        }
        if (!select("SNIPER_LENS")) return false;
        ID3DXEffect* fx = material->pEffect()->pEffect();
        if (FAILED(fx->SetFloat("g_aberrationRadius", state.aberration_radius)) ||
            FAILED(fx->SetFloat("g_distortAmount", state.distortion_amount)) ||
            FAILED(fx->SetFloat("g_offset_green", state.green_offset)) ||
            FAILED(fx->SetFloat("g_offset_blue", state.blue_offset)) ||
            FAILED(fx->SetFloat("centerX", state.center_x)) ||
            FAILED(fx->SetFloat("centerY", state.center_y)) ||
            FAILED(fx->SetBool("g_enableLensEffects", state.lens_effects)) ||
            FAILED(fx->SetTexture("g_backgroundTexture", background)) ||
            FAILED(fx->SetTexture("g_vignetteTexture", textures[0]->pTexture())) ||
            FAILED(fx->SetTexture("g_distortNormalMap", textures[1]->pTexture())) ||
            FAILED(fx->SetTexture("g_rgbCubeTexture", NULL))) return false;
        return drawQuad();
    }
};
std::shared_ptr<Binoculars> owner;
PyObject* singleton = NULL;
bool sniperGrassEnabled = true; // FloraSettings constructor +4; callback 00970ff0.

struct BinocularsWrapper
{
    PyObject_HEAD
    std::shared_ptr<Binoculars>* value;
};
PyTypeObject binocularsType = { PyObject_HEAD_INIT(NULL) 0 };
Binoculars* live(PyObject* object)
{
    auto* wrapper = reinterpret_cast<BinocularsWrapper*>(object);
    Binoculars* value = wrapper->value->get();
    if (value->closed)
    {
        PyErr_SetString(PyExc_RuntimeError, "Binoculars renderer has shut down");
        return NULL;
    }
    return value;
}
void destroyWrapper(PyObject* object)
{
    delete reinterpret_cast<BinocularsWrapper*>(object)->value;
    PyObject_Del(object);
}
PyObject* getEnabled(PyObject* object, void*)
{
    return PyBool_FromLong((*reinterpret_cast<BinocularsWrapper*>(object)->value)->state.enabled);
}
int setEnabled(PyObject* object, PyObject* value, void*)
{
    Binoculars* current = live(object);
    if (!current) return -1;
    if (!value)
    {
        PyErr_SetString(PyExc_TypeError, "Cannot delete enabled");
        return -1;
    }
    return Script::setData(value, current->state.enabled, "enabled");
}
PyObject* setMaskCenter(PyObject* object, PyObject* args)
{
    Binoculars* current = live(object);
    if (!current) return NULL;
    PyObject *a, *b;
    float x, y;
    if (!PyArg_UnpackTuple(args, "setMaskCenter", 2, 2, &a, &b) ||
        Script::setData(a, x, "() argument 1") ||
        Script::setData(b, y, "() argument 2")) return NULL;
    current->state.setMaskCenter(x, y);
    Py_RETURN_NONE;
}
PyObject* setParams(PyObject* object, PyObject* args)
{
    Binoculars* current = live(object);
    if (!current) return NULL;
    PyObject *a, *b, *c, *d;
    float green, blue, radius, distortion;
    if (!PyArg_UnpackTuple(args, "setParams", 4, 4, &a, &b, &c, &d) ||
        Script::setData(a, green, "() argument 1") ||
        Script::setData(b, blue, "() argument 2") ||
        Script::setData(c, radius, "() argument 3") ||
        Script::setData(d, distortion, "() argument 4")) return NULL;
    current->state.setParams(green, blue, radius, distortion);
    Py_RETURN_NONE;
}
PyObject* setLensEffects(PyObject* object, PyObject* args)
{
    Binoculars* current = live(object);
    if (!current) return NULL;
    PyObject* value;
    if (!PyArg_UnpackTuple(args, "setEnableLensEffects", 1, 1, &value) ||
        Script::setData(value, current->state.lens_effects)) return NULL;
    Py_RETURN_NONE;
}
PyObject* setTexture(PyObject* object, PyObject* args, size_t index, const char* method)
{
    Binoculars* current = live(object);
    if (!current) return NULL;
    PyObject* value;
    BW::string name;
    if (!PyArg_UnpackTuple(args, method, 1, 1, &value) ||
        Script::setData(value, name)) return NULL;
    current->setTexture(index, name);
    Py_RETURN_NONE;
}
PyObject* setBackground(PyObject* o, PyObject* a) { return setTexture(o, a, 0, "setBackgroundTexture"); }
PyObject* setDistortion(PyObject* o, PyObject* a) { return setTexture(o, a, 1, "setDistortionTexture"); }
PyObject* setColour(PyObject* o, PyObject* a) { return setTexture(o, a, 2, "setColorGradingTexture"); }
PyObject* resetTextures(PyObject* object, PyObject* args)
{
    Binoculars* current = live(object);
    if (!current || !PyArg_ParseTuple(args, ":resetTextures")) return NULL;
    current->resetTextures();
    Py_RETURN_NONE;
}
// #1513 method flags are METH_VARARGS | METH_KEYWORDS. Its wrappers use
// only the positional tuple; retain that dispatch contract here as well.
template<PyObject* (*Method)(PyObject*, PyObject*)>
PyObject* withKeywords(PyObject* object, PyObject* args, PyObject*)
{
    return Method(object, args);
}
#define BINOCULARS_METHOD(name, method) \
    {name, reinterpret_cast<PyCFunction>(withKeywords<method>), METH_VARARGS | METH_KEYWORDS, NULL}
PyMethodDef methods[] = {
    BINOCULARS_METHOD("setMaskCenter", setMaskCenter),
    BINOCULARS_METHOD("setBackgroundTexture", setBackground),
    BINOCULARS_METHOD("setDistortionTexture", setDistortion),
    BINOCULARS_METHOD("setColorGradingTexture", setColour),
    BINOCULARS_METHOD("resetTextures", resetTextures),
    BINOCULARS_METHOD("setParams", setParams),
    BINOCULARS_METHOD("setEnableLensEffects", setLensEffects),
    {NULL, NULL, 0, NULL}
};
#undef BINOCULARS_METHOD
PyGetSetDef attributes[] = {
    {const_cast<char*>("enabled"), getEnabled, setEnabled, NULL, NULL},
    {NULL, NULL, NULL, NULL, NULL}
};
class SniperFloraSetting : public Moo::GraphicsSetting
{
public:
    SniperFloraSetting() : Moo::GraphicsSetting("SNIPER_MODE_GRASS_ENABLED",
        "Sniper mode grass", 0, false, false)
    {
        addOption("OFF", "Off", true);
        addOption("ON", "On", true);
    }
    virtual void onOptionSelected(int option) { sniperGrassEnabled = option != 0; }
};
}

bool initialiseStockSniperFloraSetting()
{
    auto existing = Moo::GraphicsSetting::getFromLabel("SNIPER_MODE_GRASS_ENABLED");
    if (existing)
    {
        if (dynamic_cast<SniperFloraSetting*>(existing.getObject())) return true;
        PyErr_SetString(PyExc_RuntimeError, "Sniper grass setting already has another owner");
        return false;
    }
    Moo::GraphicsSetting::add(new SniperFloraSetting);
    return true;
}
bool initialiseStockGraphicsSettingsConsumers()
{
    if (owner) return true;
    if (!binocularsType.tp_name)
    {
        binocularsType.tp_name = const_cast<char*>("WGBinoculars");
        binocularsType.tp_basicsize = sizeof(BinocularsWrapper);
        binocularsType.tp_flags = Py_TPFLAGS_DEFAULT;
        binocularsType.tp_dealloc = destroyWrapper;
        binocularsType.tp_methods = methods;
        binocularsType.tp_getset = attributes;
    }
    if (PyType_Ready(&binocularsType) < 0) return false;
    auto* wrapper = PyObject_New(BinocularsWrapper, &binocularsType);
    if (!wrapper) return false;
    owner = std::make_shared<Binoculars>();
    wrapper->value = new std::shared_ptr<Binoculars>(owner);
    singleton = reinterpret_cast<PyObject*>(wrapper);
    owner->createManagedObjects();
    owner->createUnmanagedObjects();
    return true;
}
void finaliseStockGraphicsSettingsConsumers()
{
    if (owner) owner->close();
    owner.reset();
    PyObject* previous = singleton;
    singleton = NULL;
    Py_XDECREF(previous);
}
bool drawStockBinoculars() { return !owner || owner->draw(); }
bool stockSniperFloraVisible()
{
    return wot::stockSniperFloraVisible(sniperGrassEnabled, owner && owner->state.enabled);
}
static PyObject* py_wg_binoculars(PyObject* args)
{
    if (!PyArg_ParseTuple(args, ":wg_binoculars")) return NULL;
    // 00633270 returns the retained native object, or None before GUI init.
    if (!singleton) Py_RETURN_NONE;
    Py_INCREF(singleton);
    return singleton;
}
PY_MODULE_FUNCTION(wg_binoculars, BigWorld)
BW_END_NAMESPACE
