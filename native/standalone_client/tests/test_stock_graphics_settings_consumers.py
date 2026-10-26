"""Run the actual lens/Flora adapter against CPython 2.7 and guarded GPU owners.

This verifies native arguments, pass ordering, failure cleanup and Python/GPU
lifetimes; pixels and Direct3D device reset still require the Windows client.
"""
from pathlib import Path
import importlib.util
import itertools
import os
import shlex
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HOST = r'''
#include <Python.h>
#include "wot/stock_graphics_settings_consumers.hpp"
#include <cassert>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define WOT_STOCK_GRAPHICS_SETTINGS_CONSUMERS_PORTABLE
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define PY_MODULE_FUNCTION(...)
#define ERROR_MSG(...) (++errors)
#define FAILED(hr) ((hr)<0)
using uint=unsigned;
int errors=0;
namespace BW { using string=std::string; }
namespace Script {
int setData(PyObject* object,bool& value,const char* = "") {
    if(PyInt_Check(object)){value=PyInt_AsLong(object)!=0;return 0;}
    if(PyString_Check(object)){
        const char* text=PyString_AsString(object);
        if(!strcasecmp(text,"true")){value=true;return 0;}
        if(!strcasecmp(text,"false")){value=false;return 0;}
    }
    PyErr_SetString(PyExc_TypeError,"expected bool");return -1;
}
int setData(PyObject* object,float& value,const char* = "") {
    if(PyFloat_Check(object)){value=float(PyFloat_AsDouble(object));return 0;}
    if(PyInt_Check(object)){value=float(PyInt_AsLong(object));return 0;}
    if(PyLong_Check(object)){
        auto converted=PyLong_AsUnsignedLong(object);
        if(!PyErr_Occurred()){value=float(converted);return 0;}
    }
    PyErr_SetString(PyExc_TypeError,"expected float");return -1;
}
int setData(PyObject* object,std::string& value) {
    PyObject* utf8=PyUnicode_Check(object)?PyUnicode_AsUTF8String(object):NULL;
    if(PyUnicode_Check(object)&&!utf8)return -1;
    PyObject* text=utf8?utf8:object;
    if(!PyString_Check(text)){Py_XDECREF(utf8);PyErr_SetString(PyExc_TypeError,"expected string");return -1;}
    char* data;Py_ssize_t size;PyString_AsStringAndSize(text,&data,&size);
    value.assign(data,size);Py_XDECREF(utf8);return 0;
}
}
namespace DX { struct BaseTexture { std::string name; }; }
struct D3DSURFACE_DESC { uint Width=101,Height=55;int Format=1; };
bool copy_ok=true,push_ok=true,draw_ok=true,effect_ok=true,target_ok=true,bind_ok=true;
int copies=0,pushes=0,pops=0;
int created_bindings=0;
std::map<DX::BaseTexture*,unsigned> effectTextureReferences;
struct Snapshot {
    std::string technique,target;
    std::map<std::string,DX::BaseTexture*> textures;
    std::map<std::string,float> floats;
    bool lens=false;
};
std::vector<Snapshot> draws;
struct ID3DXEffect {
    bool stock=true;
    Snapshot state;
    ~ID3DXEffect(){for(const auto& binding:state.textures)if(binding.second)--effectTextureReferences[binding.second];}
    int SetFloat(const char* name,float value){state.floats[name]=value;return bind_ok?0:-1;}
    int SetBool(const char*,bool value){state.lens=value;return bind_ok?0:-1;}
    int SetTexture(const char* name,DX::BaseTexture* value){
        auto& stored=state.textures[name];
        if(stored)--effectTextureReferences[stored];stored=value;
        if(stored)++effectTextureReferences[stored];return bind_ok?0:-1;
    }
};
namespace wot { bool is_stock_effect(ID3DXEffect* effect){return effect&&effect->stock;} }
namespace Moo {
template<class T> struct Ptr : std::shared_ptr<T> {
    Ptr()=default;Ptr(T* p):std::shared_ptr<T>(p){}
    T* getObject()const{return this->get();}
};
class DeviceCallback { public:virtual ~DeviceCallback()=default; };
struct ManagedEffect {
    ID3DXEffect definition,binding;
    ID3DXEffect* active=&definition;
    ID3DXEffect* pEffect(){return active;}
    void end(){active=&definition;}
};
class EffectMaterial {
public:
    struct Properties {
        ID3DXEffect* stored=nullptr;
        ID3DXEffect* existingStockBinding()const{return stored;}
        bool select(ManagedEffect& effect){
            if(!bind_ok)return false;
            if(!effect.definition.stock){effect.active=&effect.definition;return true;}
            // Production Properties switches the active ARIE material binding.
            // Callers must retrieve pEffect() after this selection.
            effect.binding.state.technique=effect.active->state.technique;
            if(!stored){stored=&effect.binding;++created_bindings;}
            effect.active=stored;
            return true;
        }
    } props;
    Ptr<ManagedEffect> effect=new ManagedEffect;
    bool setTechnique(const char* value){effect->pEffect()->state.technique=value;return bind_ok;}
    Properties& properties(){return props;}
    Ptr<ManagedEffect>& pEffect(){return effect;}
};
using EffectMaterialPtr=Ptr<EffectMaterial>;
class BaseTexture {
public:DX::BaseTexture texture;explicit BaseTexture(std::string name){texture.name=name;}
    DX::BaseTexture* pTexture(){return &texture;}
};
using BaseTexturePtr=Ptr<BaseTexture>;
class TextureManager {
public:
    static TextureManager* instance(){static TextureManager m;return &m;}
    BaseTexturePtr get(const std::string& name){return name=="missing"?nullptr:new BaseTexture(name);}
};
std::string activeTarget="output";
class RenderTarget {
public:
    DX::BaseTexture texture;
    ~RenderTarget(){assert(effectTextureReferences[&texture]==0);}
    bool push(){++pushes;if(!push_ok)return false;assert(activeTarget=="output");activeTarget=texture.name;return true;}
    void pop(){++pops;assert(activeTarget!="output");activeTarget="output";}
    DX::BaseTexture* pTexture(){return &texture;}
};
using RenderTargetPtr=Ptr<RenderTarget>;
struct FullscreenQuad {
    bool drawChecked(EffectMaterial& material,uint w,uint h){
        assert(w==101&&h==55);auto value=material.effect->pEffect()->state;
        value.target=activeTarget;draws.push_back(value);material.effect->end();return draw_ok;
    }
};
struct RenderContext {
    D3DSURFACE_DESC desc;FullscreenQuad quad;
    const D3DSURFACE_DESC& backBufferDesc(){return desc;}
    FullscreenQuad& fsQuad(){return quad;}
};
RenderContext& rc(){static RenderContext r;return r;}
bool createEffect(EffectMaterialPtr& target,const std::string&){target=effect_ok?new EffectMaterial:nullptr;return effect_ok;}
bool createRenderTarget(RenderTargetPtr& target,uint w,uint h,int,const std::string& name,bool,bool){
    assert(w==101&&h==55);target=target_ok?new RenderTarget:nullptr;
    if(target)target->texture.name=name;return target_ok;
}
bool copyBackBuffer(RenderTarget* target){assert(target&&activeTarget=="output");++copies;return copy_ok;}
class GraphicsSetting {
public:
    using GraphicsSettingPtr=Ptr<GraphicsSetting>;
    std::string label;int active;std::vector<std::string> options;std::vector<bool> advanced;
    GraphicsSetting(const char* name,const char*,int initial,bool,bool):label(name),active(initial){}
    virtual ~GraphicsSetting()=default;
    virtual void onOptionSelected(int)=0;
    void addOption(const char* name,const char*,bool supported,bool isAdvanced=false){assert(supported);options.push_back(name);advanced.push_back(isAdvanced);}
    static std::map<std::string,GraphicsSettingPtr>& registry(){static std::map<std::string,GraphicsSettingPtr> r;return r;}
    static GraphicsSettingPtr getFromLabel(const char* name){return registry()[name];}
    static void add(GraphicsSettingPtr setting){registry()[setting->label]=setting;}
};
}
struct IRendererPipeline { enum EType { TYPE_DEFERRED_SHADING,TYPE_FORWARD_SHADING }; };
struct Renderer {
    IRendererPipeline::EType type=IRendererPipeline::TYPE_FORWARD_SHADING;
    static Renderer& instance(){static Renderer r;return r;}
    IRendererPipeline::EType pipelineType(){return type;}
};
#include "@PRODUCTION@"
static PyObject* get(PyObject*,PyObject* args){return py_wg_binoculars(args);}
static PyMethodDef exports[]={{"get",get,METH_VARARGS,NULL},{NULL,NULL,0,NULL}};
int run(const char* script){int code=PyRun_SimpleString(script);if(code)PyErr_Print();return code;}
int main(){
    Py_Initialize();Py_InitModule("consumers",exports);
    assert(run("import consumers\nassert consumers.get() is None\n")==0);
    assert(initialiseStockGraphicsSettingsConsumers());
    assert(initialiseStockGraphicsSettingsConsumers());
    const int beforeFirstDraw=created_bindings;
    owner->deleteUnmanagedObjects();assert(created_bindings==beforeFirstDraw);
    owner->createUnmanagedObjects();
    assert(initialiseStockSniperFloraSetting());assert(initialiseStockSniperFloraSetting());
    auto setting=Moo::GraphicsSetting::getFromLabel("SNIPER_MODE_GRASS_ENABLED");
    assert(setting->options==std::vector<std::string>({"OFF","ON"}));
    assert(setting->advanced==std::vector<bool>({false,true}));
    // Constructor option zero does not invoke the native callback.
    owner->state.enabled=true;assert(stockSniperFloraVisible());
    setting->onOptionSelected(0);assert(!stockSniperFloraVisible());
    owner->state.enabled=false;assert(stockSniperFloraVisible());
    setting->onOptionSelected(1);owner->state.enabled=true;assert(stockSniperFloraVisible());
    assert(run("b=consumers.get()\nassert b is consumers.get()\n"
        "assert b.enabled is True\nb.enabled=False\n"
        "b.setMaskCenter(.25, -.75)\nb.setMaskCenter(float('nan'),0.)\n"
        "b.setParams(2.,7.,.8,-.04, ignored=True)\n"
        "b.setBackgroundTexture(u'vignette')\nb.setDistortionTexture('distortion')\n"
        "b.setColorGradingTexture('cube')\nb.setEnableLensEffects(True)\n"
        "for call in [lambda:consumers.get(1),lambda:b.setParams(1),lambda:b.setMaskCenter(1),"
        "lambda:b.setEnableLensEffects(),lambda:b.resetTextures(1),lambda:b.setBackgroundTexture(3)]:\n"
        " try: call()\n except TypeError: pass\n else: raise AssertionError('arity/type guard missing')\n")==0);
    assert(owner->state.center_x==.25f&&owner->state.center_y==.75f);
    owner->state.setMaskCenter(1.1f,0.f);assert(owner->state.center_x==.25f);
    owner->state.enabled=true;
    assert(drawStockBinoculars());assert(draws.size()==1);
    assert(draws[0].technique=="SNIPER_LENS_SIMPLE");
    assert(draws[0].textures["g_vignetteTexture"]->name=="vignette");
    assert(!draws[0].textures["g_backgroundTexture"]&&!copies);
    Renderer::instance().type=IRendererPipeline::TYPE_DEFERRED_SHADING;
    owner->createUnmanagedObjects();draws.clear();
    const int unused=created_bindings;
    owner->deleteUnmanagedObjects();assert(created_bindings==unused);
    owner->createUnmanagedObjects();
    assert(drawStockBinoculars());assert(draws.size()==2&&copies==1&&pushes==1&&pops==1);
    assert(draws[0].technique=="RGB_CUBE"&&draws[0].target=="stock/Binoculars colour grading");
    assert(draws[0].textures["g_backgroundTexture"]->name=="stock/Binoculars scene copy");
    assert(draws[0].textures["g_rgbCubeTexture"]->name=="cube");
    assert(draws[1].technique=="SNIPER_LENS"&&draws[1].target=="output");
    assert(draws[1].textures["g_backgroundTexture"]->name=="stock/Binoculars colour grading");
    assert(draws[1].floats["g_offset_blue"]==7.f&&draws[1].floats["centerY"]==.75f);
    assert(draws[1].lens&&!draws[1].textures["g_rgbCubeTexture"]);
    owner->state.lens_effects=false;draws.clear();assert(drawStockBinoculars());
    assert(draws.size()==1&&!draws[0].lens);
    assert(draws[0].textures["g_backgroundTexture"]->name=="stock/Binoculars scene copy");
    owner->state.lens_effects=true;
    draw_ok=false;draws.clear();assert(!drawStockBinoculars());assert(Moo::activeTarget=="output");
    draw_ok=true;push_ok=false;assert(!drawStockBinoculars());assert(Moo::activeTarget=="output");
    push_ok=true;copy_ok=false;assert(!drawStockBinoculars());copy_ok=true;
    bind_ok=false;assert(!drawStockBinoculars());bind_ok=true;
    assert(drawStockBinoculars()); // End closes the actual material pass.
    assert(owner->material->effect->active==&owner->material->effect->definition);
    owner->material->effect->definition.stock=false;
    assert(drawStockBinoculars()); // A legacy reload may coexist with the stored stock Binding.
    const int beforeCleanup=created_bindings;
    owner->deleteUnmanagedObjects();
    assert(created_bindings==beforeCleanup);
    for(const auto& binding:owner->material->properties().existingStockBinding()->state.textures)assert(!binding.second);
    assert(owner->material->effect->active==&owner->material->effect->definition);
    assert(!drawStockBinoculars());
    owner->createUnmanagedObjects();assert(drawStockBinoculars());
    owner->resetTextures();draws.clear();assert(drawStockBinoculars()&&draws.empty());
    auto retained=owner;finaliseStockGraphicsSettingsConsumers();finaliseStockGraphicsSettingsConsumers();
    assert(!retained->material&&!retained->source&&!retained->graded&&!retained->textures[0]);
    assert(run("assert consumers.get() is None\nassert b.enabled is False\n"
        "try: b.setBackgroundTexture('late')\nexcept RuntimeError: pass\n"
        "else: raise AssertionError('closed renderer resurrected')\ndel b\n")==0);
    // In-flight external owners are harmless after Python shutdown.
    Py_Finalize();retained.reset();
}
'''


class GraphicsConsumersTest(unittest.TestCase):
    def test_real_adapter_python_lifetime_and_render_passes(self):
        python = Path(os.environ.get('WOT_PYTHON27', '/Users/peng/.pyenv/versions/2.7.18/bin/python2.7'))
        config = python.with_name('python2.7-config')
        if not config.exists():
            self.skipTest('Pinned CPython 2.7 development files unavailable')
        with tempfile.TemporaryDirectory(prefix='graphics-consumers-') as temp:
            temp = Path(temp)
            source = temp / 'host.cpp'
            source.write_text(HOST.replace('@PRODUCTION@', str(ROOT / 'bigworld/stock_graphics_settings_consumers.cpp')))
            flags = shlex.split(subprocess.check_output([str(config), '--cflags'], text=True))
            flags = [x for x in flags if x != '-DNDEBUG']
            libs = shlex.split(subprocess.check_output([str(config), '--ldflags'], text=True))
            binary = temp / 'host'
            subprocess.run(['c++', '-std=c++14', '-UNDEBUG', '-I' + str(ROOT / 'include'),
                            str(source), *flags, *libs, '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_patch_fresh_composition_and_twice(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Unmodified public-source archive unavailable')
        names = ['graphics_settings_consumers', 'dynamic_resolution', 'decals', 'screenshots']
        patches = []
        for name in names:
            spec = importlib.util.spec_from_file_location(name, ROOT / ('bigworld/patch_stock_' + name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            patches.append(module.patch)
        with tarfile.open(archive) as source:
            prefix = next(n[:-len('client/gui_app.cpp')] for n in source.getnames() if n.endswith('/client/gui_app.cpp'))
            original = {n[len(prefix):]: source.extractfile(n).read() for n in source.getnames()
                        if n.startswith(prefix) and (n[len(prefix):].startswith(('client/', 'lib/romp/', 'lib/moo/')))
                        and source.getmember(n).isfile()}
        # All 24 relative orders cover each shared GUI/render header anchor.
        expected = None
        for order in itertools.permutations(patches):
            with tempfile.TemporaryDirectory(prefix='graphics-consumers-patch-') as temp:
                root = Path(temp)
                for name, data in original.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                for apply in order:
                    apply(root)
                first = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                for apply in reversed(order):
                    apply(root)
                self.assertEqual(first, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})
                consumer_outputs = {name: first[name] for name in
                                    ('client/gui_app.cpp', 'lib/romp/flora.cpp')}
                if expected is None:
                    expected = consumer_outputs
                self.assertEqual(expected, consumer_outputs)
                flora = (root / 'lib/romp/flora.cpp').read_text()
                self.assertEqual(flora.count('!stockSniperFloraVisible()'), 1)
                self.assertNotIn('wg_initCustomSettings', (ROOT / 'bigworld/stock_graphics_settings_consumers.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
