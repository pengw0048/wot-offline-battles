"""Exercise the actual renderer adapter with guarded device/effect owners.

This host checks native call ordering and terminal failure paths. It does not
claim Direct3D rendering or Windows device-loss acceptance.
"""
from pathlib import Path
import importlib.util
import itertools
import os
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BIGWORLD = Path(os.environ.get('WOT_BIGWORLD_ROOT',
    '/tmp/wot-bigworld-engine-14.4.1/programming/bigworld'))


HOST = r'''
#include "wot/stock_dynamic_resolution.hpp"
#include <cassert>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define MF_ASSERT(value) assert(value)
#define ERROR_MSG(...) (++logged_errors)
#define SUCCEEDED(hr) ((hr)>=0)
#define FAILED(hr) ((hr)<0)
using uint32=unsigned;
using uint=unsigned;
using HRESULT=int;
using D3DXHANDLE=const char*;
struct D3DSURFACE_DESC { unsigned Width=101,Height=55;int Format=1; };
namespace DX { struct Viewport { unsigned X,Y,Width,Height;float MinZ,MaxZ; }; }
int logged_errors=0,python_errors=0;
void* PyExc_RuntimeError=nullptr;
void PyErr_SetString(void*,const char*) { ++python_errors; }
struct DataSection {
    std::map<std::string,float> values;
    std::shared_ptr<DataSection> child;
    std::shared_ptr<DataSection> openSection(const char*) { return child; }
    int readInt(const char* key,int value) { auto p=values.find(key);return p==values.end()?value:int(p->second); }
    float readFloat(const char* key,float value) { auto p=values.find(key);return p==values.end()?value:p->second; }
};
using DataSectionPtr=std::shared_ptr<DataSection>;
class AppConfig {
public:
    static AppConfig& instance() { static AppConfig value;return value; }
    DataSectionPtr pRoot() { return root; }
    DataSectionPtr root;
};
bool effect_ok=true,target_ok=true,copy_ok=true,binding_ok=true,draw_ok=true,viewport_ok=true;
bool texture_ok=true;
int copies=0,draws=0,selected=0;
std::vector<std::string> order;
std::vector<float> upsample_parameters;
struct ID3DXEffect {
    std::map<std::string,std::vector<float>> values;
    HRESULT SetValue(const char* handle,const void* data,unsigned bytes) {
        const auto* f=static_cast<const float*>(data);values[handle]={f,f+bytes/4};return 0;
    }
    HRESULT SetTexture(const char* name,void* texture) {
        assert(std::string(name)=="g_bbCopyMap" && texture && selected);
        order.push_back("texture");return 0;
    }
};
namespace Moo {
template<class T> struct Ptr : std::shared_ptr<T> {
    Ptr()=default;Ptr(T* p):std::shared_ptr<T>(p) {}
    T* getObject() const { return this->get(); }
};
class DeviceCallback { public: virtual ~DeviceCallback()=default; };
class EffectConstantValue { public:virtual ~EffectConstantValue()=default;virtual bool operator()(ID3DXEffect*,D3DXHANDLE)=0; };
class ManagedEffect {
public: ID3DXEffect value; ID3DXEffect* pEffect() { return &value; }
};
class EffectMaterial {
public:
    struct Properties {
        bool select(ManagedEffect&) { ++selected;order.push_back("select");return binding_ok; }
    };
    std::string technique;
    Properties properties_;
    Ptr<ManagedEffect> effect=new ManagedEffect;
    void hTechnique(const char* value) { technique=value; }
    Properties& properties() { return properties_; }
    Ptr<ManagedEffect>& pEffect() { return effect; }
};
using EffectMaterialPtr=Ptr<EffectMaterial>;
class RenderTarget { public: void* pTexture() { return texture_ok?this:nullptr; } };
using RenderTargetPtr=Ptr<RenderTarget>;
class EffectVisualContext {
public:
    enum { AUTO_CONSTANT_TYPE_PER_FRAME=0 };
    std::map<std::string,std::shared_ptr<EffectConstantValue>> constants;
    ID3DXEffect captures;
    bool alive=true;
    void registerAutoConstant(int,const char* name,EffectConstantValue* value) {
        assert(alive);constants[name].reset(value);
    }
    void unregisterAutoConstant(int,const char* name) { assert(alive);constants.erase(name); }
    void updateSharedConstants(int flags) {
        assert(alive);if(flags&1)for(auto& p:constants)assert((*p.second)(&captures,p.first.c_str()));
    }
};
enum { CONSTANTS_PER_FRAME=1,CONSTANTS_PER_SCREEN=2 };
class FullscreenQuad { public:bool drawChecked(const EffectMaterial&,uint,uint); };
class RenderContext {
public:
    EffectVisualContext context;FullscreenQuad quad;
    D3DSURFACE_DESC output;
    DX::Viewport actual={0,0,101,55,0.f,1.f};
    unsigned frame=0;float width=101,height=55;bool scene=false;
    unsigned frameTimestamp() const { return frame; }
    const D3DSURFACE_DESC& backBufferDesc() const { return output; }
    EffectVisualContext& effectVisualContext() { return context; }
    FullscreenQuad& fsQuad() { return quad; }
    bool stockSceneResolution() const { return scene; }
    void stockSceneResolution(bool value) { scene=value; }
    HRESULT setViewport(DX::Viewport* value) {
        if(!viewport_ok)return -1;actual=*value;order.push_back("viewport");return 0;
    }
    void screenWidth(unsigned n) { width=float(n); }
    void screenHeight(unsigned n) { height=float(n); }
};
RenderContext& rc() { static RenderContext value;return value; }
bool FullscreenQuad::drawChecked(const EffectMaterial& material,uint width,uint height) {
    assert(!rc().scene && width==rc().output.Width && height==rc().output.Height);
    assert(material.technique=="drr");++draws;order.push_back("draw");
    upsample_parameters=rc().context.captures.values["DRRParams"];return draw_ok;
}
bool createEffect(EffectMaterialPtr& material,const char* name) {
    assert(std::string(name)=="shaders/drr/wg_drr.fx");
    material=effect_ok?new EffectMaterial:nullptr;return effect_ok;
}
bool createRenderTarget(RenderTargetPtr& target,unsigned width,unsigned height,int,
    const char*,bool,bool) {
    assert(width==rc().output.Width && height==rc().output.Height);
    target=target_ok?new RenderTarget:nullptr;return target_ok;
}
bool copyBackBuffer(RenderTarget* target) {
    assert(target && !rc().scene && rc().actual.Width==rc().output.Width);
    ++copies;order.push_back("copy");return copy_ok;
}
class GraphicsSetting {
public:
    using GraphicsSettingPtr=Ptr<GraphicsSetting>;
    static std::map<std::string,GraphicsSettingPtr> registry;
    std::string label;int active;std::vector<std::string> options;
    GraphicsSetting(const char* l,const char*,int v,bool delayed,bool restart):label(l),active(v) { assert(!delayed&&!restart); }
    virtual ~GraphicsSetting()=default;
    virtual void onOptionSelected(int)=0;
    void addOption(const char* n,const char*,bool supported) { assert(supported);options.push_back(n); }
    static GraphicsSettingPtr getFromLabel(const char* n) { return registry[n]; }
    static void add(GraphicsSettingPtr p) { registry[p->label]=p; }
};
std::map<std::string,GraphicsSetting::GraphicsSettingPtr> GraphicsSetting::registry;
}
'''

CASES = r'''
int main() {
    assert(!initialiseStockDynamicResolutionSetting() && python_errors==1);
    { StockDynamicResolutionSupport partial; } // Before any render device/context callback.
    auto root=std::make_shared<DataSection>();root->child=std::make_shared<DataSection>();
    root->child->values={{"criticalFPS",15},{"safeFPSMultiplier",1.15f}};
    AppConfig::instance().root=root;
    {
        StockDynamicResolutionSupport support;
        assert(stockDynamicResolution()==&support && !support.ready());
        assert(support.state().config().critical_fps==15.f);
        assert(support.state().config().safe_fps==15.f*1.15f);
        assert(!initialiseStockDynamicResolutionSetting());
        support.createManagedObjects();support.createUnmanagedObjects();
        assert(support.ready() && Moo::rc().context.constants.size()==2);
        support.createManagedObjects(); // Re-creation replaces, rather than duplicates, providers.
        assert(Moo::rc().context.constants.size()==2);
        assert(initialiseStockDynamicResolutionSetting());
        assert(initialiseStockDynamicResolutionSetting());
        auto setting=Moo::GraphicsSetting::getFromLabel("DRR_AUTOSCALER_ENABLED");
        assert(setting->options==std::vector<std::string>({"OFF","ON"}));
        assert(support.state().enabled()); // Registry construction did not run callback.
        setting->onOptionSelected(0);assert(!support.state().enabled());
        support.state().set_scale(.73f);
        DX::Viewport viewport={2,3,101,55,.2f,.8f};
        assert(SUCCEEDED(setStockSceneViewport(&viewport,true)));
        assert(viewport.Width==101 && viewport.Height==55); // Caller descriptor is immutable.
        assert(Moo::rc().actual.Width==74 && Moo::rc().actual.Height==40);
        assert(Moo::rc().actual.X==2 && Moo::rc().actual.MinZ==.2f);
        assert(stockSceneDimension(101)==74);
        assert(SUCCEEDED(setStockSceneViewport(&viewport,false)));
        assert(Moo::rc().actual.Width==101 && stockSceneDimension(101)==101);
        viewport_ok=false;
        assert(FAILED(setStockSceneViewport(&viewport,true)) && !Moo::rc().scene);
        viewport_ok=true;
        beginStockDynamicResolutionScene();
        assert(finishStockDynamicResolutionScene());
        assert(copies==1 && draws==1 && !Moo::rc().scene);
        assert(order[order.size()-4]=="copy" && order[order.size()-3]=="select");
        auto params=upsample_parameters;
        assert(params.size()==4 && std::fabs(params[0]-74.f/101.f)<.00001f);
        assert(Moo::rc().context.captures.values["DRRParams"]==std::vector<float>({1.f,1.f,0.f,0.f}));
        assert(Moo::rc().context.captures.values["DRRParamsDownsample"].size()==16);
        assert(finishStockDynamicResolutionScene() && copies==1 && draws==1);
        copy_ok=false;beginStockDynamicResolutionScene();
        assert(!finishStockDynamicResolutionScene() && draws==1);copy_ok=true;
        binding_ok=false;beginStockDynamicResolutionScene();
        assert(!finishStockDynamicResolutionScene() && draws==1);binding_ok=true;
        draw_ok=false;beginStockDynamicResolutionScene();
        assert(!finishStockDynamicResolutionScene());draw_ok=true;
        support.state().set_scale(1.f);const int before=copies;
        beginStockDynamicResolutionScene();assert(finishStockDynamicResolutionScene() && copies==before);
        support.deleteUnmanagedObjects();support.deleteUnmanagedObjects();
        assert(!support.ready());
        support.createUnmanagedObjects();assert(support.ready());
        texture_ok=false;assert(!support.ready());texture_ok=true;
        support.deleteManagedObjects();support.deleteManagedObjects();
        assert(!support.ready() && Moo::rc().context.constants.empty());
        effect_ok=false;support.createManagedObjects();assert(!support.ready());effect_ok=true;
        support.createManagedObjects();assert(support.ready());
    }
    assert(stockDynamicResolution()==nullptr && Moo::rc().context.constants.empty());
    Moo::rc().context.alive=false;
    { StockDynamicResolutionSupport partial; } // No callbacks means no context teardown access.
}
'''


class DynamicResolutionTest(unittest.TestCase):
    def test_all_owner_patch_orders_on_fresh_public_archive(self):
        archive = Path(os.environ.get('WOT_PUBLIC_BIGWORLD_ARCHIVE',
            '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.is_file():
            self.skipTest('Requires the unmodified pinned public-source archive')
        names = ('stock_dynamic_resolution', 'stock_decals', 'stock_screenshots',
                 'resource_write_overlay', 'stock_startup_services')
        modules = {}
        files = {'lib/moo/renderer.hpp', 'lib/moo/renderer.cpp', 'lib/moo/sticker_decal.cpp'}
        for name in names:
            path = ROOT / 'bigworld' / ('patch_' + name + '.py')
            spec = importlib.util.spec_from_file_location('combined_' + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
            files.update(re.findall(r"['\"]((?:lib|client)/[^'\"]+\.(?:cpp|hpp|ipp))['\"]", path.read_text()))
        with tarfile.open(archive) as original:
            prefix = original.getmembers()[0].name.split('/')[0] + '/programming/bigworld/'
            available = {member.name: member for member in original.getmembers()}
            contents = {}
            for name in files:
                member = available.get(prefix + name)
                if member:
                    contents[name] = original.extractfile(member).read()
                else:
                    self.assertIn(name, {'lib/stock_dynamic_resolution.hpp',
                                         'lib/wot/stock_dynamic_resolution.hpp'})
        orders = itertools.permutations(names[:4])
        for order in orders:
            for startup_first in (True, False):
                sequence = (names[4],) + order if startup_first else order + (names[4],)
                with self.subTest(order=sequence), tempfile.TemporaryDirectory(prefix='fresh-native-owner-patches-') as directory:
                    root = Path(directory)
                    for name, content in contents.items():
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                    for name in sequence:
                        modules[name].patch(root)
                    header = (root / 'lib/moo/renderer.hpp').read_text()
                    source = (root / 'lib/moo/renderer.cpp').read_text()
                    for declaration in ('class StockDynamicResolutionSupport;', 'class StockTraceTextureOwner;'):
                        self.assertEqual(header.count(declaration), 1)
                    for cleanup in ('m_dynamicResolution.reset();', 'm_stockTraceTextures.reset();'):
                        self.assertEqual(source.count(cleanup), 1)
                    constructor = source[source.index('Renderer::Renderer()'):source.index('Renderer::~Renderer()')]
                    self.assertLess(constructor.index('m_dynamicResolution('), constructor.index('m_inited('))
                    self.assertLess(constructor.index('m_inited('), constructor.index('m_type('))
                    self.assertLess(header.index(' m_dynamicResolution;'), header.index(' m_inited;'))
                    self.assertLess(header.index(' m_inited;'), header.index(' m_type;'))
                    context_header = (root / 'lib/moo/render_context.hpp').read_text()
                    self.assertNotIn('stock_screenshot_callback.hpp', context_header)
                    self.assertNotIn('make_shared<wot::StockScreenshotCallback>', context_header)
                    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                    # A second whole build may run patches in another order.
                    for name in reversed(sequence):
                        modules[name].patch(root)
                    self.assertEqual(before, {p.relative_to(root): p.read_bytes()
                                             for p in root.rglob('*') if p.is_file()})

    def test_actual_fullscreen_draw_half_texel_and_failures(self):
        path = BIGWORLD / 'lib/moo/fullscreen_quad.cpp'
        if not path.is_file() or 'drawChecked' not in path.read_text():
            self.skipTest('Requires the patched pinned fullscreen owner fixture')
        source = path.read_text()

        def function(marker):
            start = source.index(marker)
            end = source.index('{', start)
            depth = 1
            while depth:
                end += 1
                depth += (source[end] == '{') - (source[end] == '}')
            return source[start:end + 1]

        mesh = function('template <typename MeshType>')
        draw = function('bool FullscreenQuad::drawChecked')
        host = r'''
#include <cassert>
#include <cmath>
#include <string>
#include <vector>
#define BW_GUARD
#define SUCCEEDED(value) ((value)>=0)
#define FAILED(value) ((value)<0)
using uint=unsigned;using uint32=unsigned;using HRESULT=int;
enum { D3DPT_TRIANGLESTRIP=1 };
struct Vector3 {
    float x,y,z;Vector3(float a=0,float b=0,float c=0):x(a),y(b),z(c) {}
    void set(float a,float b,float c) { x=a;y=b;z=c; }
    Vector3 operator+(const Vector3& b) const { return {x+b.x,y+b.y,z+b.z}; }
};
struct Vector2 { float x,y;void set(float a,float b) { x=a;y=b; } };
int failure=0,draws=0,passes=1;bool stock=true,applies=true;
std::vector<char> trace;float adjustment[2]={0,0};
struct ID3DXEffect {
    HRESULT SetFloatArray(const char* name,const float* v,unsigned n) {
        assert(std::string(name)=="g_FSQuadTCAdjust" && n==2);trace.push_back('U');
        adjustment[0]=v[0];adjustment[1]=v[1];return failure==2?-1:0;
    }
};
namespace wot { bool is_stock_effect(ID3DXEffect*) { return stock; } }
uint32 stockSceneDimension(uint32 n) { return applies?uint32(n*.73f+.5f):n; }
namespace Moo {
struct VertexDeclaration {};
struct VertexXYZUV { Vector3 pos_;Vector2 uv_; };
std::vector<VertexXYZUV> vertices;
template<class T> class CustomMesh : public std::vector<T> {
public:
    CustomMesh(int) {}
    HRESULT drawEffect(VertexDeclaration*) {
        vertices.assign(this->begin(),this->end());trace.push_back('D');++draws;
        return failure==4?-1:0;
    }
};
class ManagedEffect { public:ID3DXEffect effect;ID3DXEffect* pEffect() { return &effect; } };
class EffectMaterial {
public:
    mutable ManagedEffect effect;
    bool begin() const { trace.push_back('B');return failure!=1; }
    unsigned numPasses() const { return passes; }
    bool beginPass(unsigned) const { trace.push_back('P');return failure!=3; }
    bool endPass() const { trace.push_back('Q');return failure!=5; }
    bool end() const { trace.push_back('E');return failure!=6; }
    ManagedEffect* pEffect() const { return &effect; }
};
class FullscreenQuad {
public:
    VertexDeclaration declaration;VertexDeclaration* m_vecDclr=&declaration;
    bool drawChecked(const EffectMaterial&,uint,uint);
};
}
using namespace Moo;
'''
        cases = r'''
int main() {
    FullscreenQuad quad;EffectMaterial material;
    assert(quad.drawChecked(material,101,55));
    assert(trace==std::vector<char>({'B','U','P','D','Q','E'}));
    assert(std::fabs(adjustment[0]-.5f/74)<.000001f);
    assert(std::fabs(adjustment[1]-.5f/40)<.000001f);
    assert(vertices[0].pos_.x==-1.f && vertices[0].pos_.y==1.f);
    assert(vertices[0].uv_.x==0.f && vertices[0].uv_.y==0.f);
    stock=false;trace.clear();assert(quad.drawChecked(material,101,55));
    assert(trace==std::vector<char>({'B','P','D','Q','E'}));
    assert(std::fabs(vertices[0].pos_.x-(-1.f-1.f/74))<.000001f);
    stock=true;applies=false;assert(quad.drawChecked(material,101,55));
    assert(std::fabs(adjustment[0]-.5f/101)<.000001f);
    for(failure=1;failure<=6;++failure) {
        trace.clear();const int before=draws;
        assert(!quad.drawChecked(material,101,55));
        if(failure<=3)assert(draws==before);
        if(failure==1)assert(trace==std::vector<char>({'B'}));
        else assert(trace.back()=='E');
    }
    failure=0;passes=0;assert(!quad.drawChecked(material,101,55));
    passes=1;quad.m_vecDclr=nullptr;trace.clear();
    assert(!quad.drawChecked(material,101,55) && trace.empty());
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-drr-quad-') as directory:
            root = Path(directory)
            (root / 'probe.cpp').write_text(host + mesh + '\n' + draw + cases)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++14', '-Wall',
                '-Wextra', '-Werror', str(root / 'probe.cpp'), '-o', str(root / 'probe')], check=True)
            subprocess.run([str(root / 'probe')], check=True)

    def test_guarded_renderer_lifecycle_and_actual_consumers(self):
        header = (ROOT / 'bigworld/stock_dynamic_resolution.hpp').read_text()
        source = (ROOT / 'bigworld/stock_dynamic_resolution.cpp').read_text()
        header = '\n'.join(line for line in header.splitlines()
                           if not line.startswith(('#include', '#pragma')))
        source = source[:source.index('static PyObject* py_getDRRScale')]
        source = '\n'.join(line for line in source.splitlines()
                           if not line.startswith('#include'))
        with tempfile.TemporaryDirectory(prefix='stock-drr-owner-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(HOST + header + source + CASES)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++14',
                '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'include'),
                str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)

    def test_actual_python2_capi_and_original_manual_controls(self):
        python = os.environ.get('WOT_PY27') or shutil.which('python2.7')
        if not python:
            candidate = Path.home() / '.pyenv/versions/2.7.18/bin/python2.7'
            if candidate.is_file():
                python = str(candidate)
        client = os.environ.get('WOT_0922_CLIENT')
        package = Path(client) / 'res/packages/scripts.pkg' if client else None
        script_file = BIGWORLD / 'lib/pyscript/script.cpp'
        if sys.platform != 'darwin' or not python or not script_file.is_file() or not package or not package.is_file():
            self.skipTest('Requires CPython 2.7, public conversion source and exact client resources')
        public = script_file.read_text()
        conversions = []
        for marker in ('int Script::setData( PyObject * pObject, bool & rBool,',
                       'int Script::setData( PyObject * pObject, double & rDouble,',
                       'int Script::setData( PyObject * pObject, float & rFloat,',
                       'PyObject * Script::getData( const bool data )',
                       'PyObject * Script::getData( const float data )'):
            start = public.index(marker)
            conversions.append(public[start:public.index('\n}', start) + 2])
        host = HOST.replace('void* PyExc_RuntimeError=nullptr;\n', '').replace(
            'void PyErr_SetString(void*,const char*) { ++python_errors; }\n', '')
        declarations = '''
#include <Python.h>
#include <strings.h>
#define _stricmp strcasecmp
#define PY_MODULE_FUNCTION(name,module)
namespace Script {
int setData(PyObject*,bool&,const char*);
int setData(PyObject*,double&,const char*);
int setData(PyObject*,float&,const char*);
PyObject* getData(bool);
PyObject* getData(float);
}
'''
        header = (ROOT / 'bigworld/stock_dynamic_resolution.hpp').read_text()
        source = (ROOT / 'bigworld/stock_dynamic_resolution.cpp').read_text()
        header = '\n'.join(line for line in header.splitlines()
                           if not line.startswith(('#include', '#pragma')))
        source = '\n'.join(line for line in source.splitlines()
                           if not line.startswith('#include'))
        register = r'''
StockDynamicResolutionSupport* probe_support=nullptr;
static PyObject* start_probe(PyObject*,PyObject*) {
    delete probe_support;probe_support=new StockDynamicResolutionSupport;
    probe_support->createManagedObjects();probe_support->createUnmanagedObjects();Py_RETURN_NONE;
}
static PyObject* stop_probe(PyObject*,PyObject*) {
    delete probe_support;probe_support=nullptr;Moo::GraphicsSetting::registry.clear();Py_RETURN_NONE;
}
static PyObject* enable_probe(PyObject*,PyObject* args) {
    int value;if(!PyArg_ParseTuple(args,"i",&value))return nullptr;
    probe_support->state().enable(value!=0,Moo::rc().frameTimestamp());Py_RETURN_NONE;
}
static PyObject* tick_probe(PyObject*,PyObject* args) {
    float elapsed;unsigned frames;if(!PyArg_ParseTuple(args,"fI",&elapsed,&frames))return nullptr;
    Moo::rc().frame+=frames;tickStockDynamicResolution(elapsed);Py_RETURN_NONE;
}
static PyMethodDef methods[]={
    {"getDRRScale",[](PyObject*,PyObject* args){return py_getDRRScale(args);},METH_VARARGS,nullptr},
    {"setDRRScale",[](PyObject*,PyObject* args){return py_setDRRScale(args);},METH_VARARGS,nullptr},
    {"isDRRAutoscalingEnabled",[](PyObject*,PyObject* args){return py_isDRRAutoscalingEnabled(args);},METH_VARARGS,nullptr},
    {"pauseDRRAutoscaling",[](PyObject*,PyObject* args){return py_pauseDRRAutoscaling(args);},METH_VARARGS,nullptr},
    {"start",start_probe,METH_NOARGS,nullptr},{"stop",stop_probe,METH_NOARGS,nullptr},
    {"enable",enable_probe,METH_VARARGS,nullptr},{"tick",tick_probe,METH_VARARGS,nullptr},
    {nullptr,nullptr,0,nullptr}};
extern "C" void initdrr_probe() { Py_InitModule("drr_probe",methods); }
'''
        runner = r'''
import sys,zipfile,marshal
sys.path.insert(0,sys.argv[1])
import drr_probe as native
def rejects(error,fn,*args):
    try:fn(*args)
    except error:return
    raise AssertionError('Expected %s from %s' % (error,fn))
rejects(RuntimeError,native.getDRRScale)
native.start()
assert type(native.getDRRScale()) is float
assert type(native.isDRRAutoscalingEnabled()) is bool
assert native.isDRRAutoscalingEnabled() is True
for fn in (native.getDRRScale,native.isDRRAutoscalingEnabled):rejects(TypeError,fn,1)
for fn in (native.setDRRScale,native.pauseDRRAutoscaling):
    rejects(TypeError,fn);rejects(TypeError,fn,1,2)
rejects(TypeError,native.setDRRScale,'0.7')
rejects(TypeError,native.pauseDRRAutoscaling,object())
native.setDRRScale(-1);assert abs(native.getDRRScale()-.1)<.00001
native.setDRRScale(2);assert native.getDRRScale()==1.
native.setDRRScale(.73);assert abs(native.getDRRScale()-.73)<.00001
assert native.pauseDRRAutoscaling(True) is None
assert native.getDRRScale()==1.
native.pauseDRRAutoscaling(False)
native.tick(1.,10);assert abs(native.getDRRScale()-.9)<.00001
native.pauseDRRAutoscaling('TRUE');assert native.getDRRScale()==1.
native.pauseDRRAutoscaling('false')
native.enable(False)
sys.modules['BigWorld']=native
z=zipfile.ZipFile(sys.argv[2])
scope={'__name__':'audit_drr_scale'}
exec marshal.loads(z.read('scripts/client/helpers/drr_scale.pyc')[8:]) in scope
assert scope['stepDown']()==.95
assert scope['stepUp']()==1.
native.setDRRScale(.6)
assert scope['stepDown']() is None
assert scope['stepUp']()==.65
native.stop();rejects(RuntimeError,native.pauseDRRAutoscaling,True)
print('Actual Python 2 C API and original manual DRR consumers passed')
'''
        with tempfile.TemporaryDirectory(prefix='stock-drr-capi-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(declarations + host + '\n'.join(conversions) + header + source + register)
            (path / 'check.py').write_text(runner)
            includes = []
            for flag in shlex.split(subprocess.check_output([python + '-config', '--includes'], text=True)):
                includes.extend(['-isystem', flag[2:]] if flag.startswith('-I') else [flag])
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++14', '-Wall', '-Wextra',
                '-Werror', '-Wno-missing-field-initializers', '-dynamiclib', '-undefined', 'dynamic_lookup',
                *includes, '-I', str(ROOT / 'include'), str(path / 'probe.cpp'),
                '-o', str(path / 'drr_probe.so')], check=True)
            subprocess.run([python, str(path / 'check.py'), str(path), str(package)],
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)

    def test_engine_patch_is_idempotent_and_transactional(self):
        if not (BIGWORLD / 'lib/moo/render_target.cpp').is_file():
            self.skipTest('Requires the pinned public engine source fixture')
        patch_file = ROOT / 'bigworld/patch_stock_dynamic_resolution.py'
        spec = importlib.util.spec_from_file_location('dynamic_resolution_patch', patch_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = set(re.findall(r"['\"]((?:lib|client)/[^'\"]+\.(?:cpp|hpp|ipp))['\"]", patch_file.read_text()))
        with tempfile.TemporaryDirectory(prefix='stock-drr-patch-') as directory:
            root = Path(directory)
            for name in names:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(BIGWORLD / name, target)
            module.patch(root)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            module.patch(root)
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            target = root / 'lib/moo/render_target.hpp'
            target.write_text('class UnsupportedRenderTarget {};\n')
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'owner anchor changed'):
                module.patch(root)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
