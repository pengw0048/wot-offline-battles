"""Compile actual VehicleAudition state against a recording audio/callback host.

The host covers RTPC routing, original material rules and reentrant teardown;
it is not a Wwise SDK, Python C ABI, or Windows playback acceptance test.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include "stock_detailed_engine_state.hpp"
namespace BW { using string=std::string;template<class T>using vector=std::vector<T>; }
using uint64=std::uint64_t;using uint32=std::uint32_t;
#define bw_snprintf std::snprintf
struct PyObject {
    virtual ~PyObject()=default;int refs=1;float number=0;std::vector<std::string> names;
    std::function<PyObject*()> callback;
};
static PyObject exception;
static PyObject* PyExc_ValueError=&exception;
static bool error=false;
void PyErr_SetString(PyObject*,const char*) {error=true;}
void Py_XDECREF(PyObject* p) {if(p && --p->refs==0)delete p;}
class PyObjectPtr {
    PyObject* p_;
public:
    enum Reference {STEAL_REFERENCE};
    PyObjectPtr(PyObject* p=nullptr):p_(p) {if(p_)++p_->refs;}
    PyObjectPtr(PyObject* p,Reference):p_(p) {}
    PyObjectPtr(const PyObjectPtr& p):PyObjectPtr(p.p_) {}
    ~PyObjectPtr(){Py_XDECREF(p_);}
    PyObjectPtr& operator=(PyObject* p) {if(p)++p->refs;Py_XDECREF(p_);p_=p;return *this;}
    PyObject* get()const{return p_;}explicit operator bool()const{return p_!=nullptr;}
};
PyObject* PyObject_CallObject(PyObject* p,PyObject*) {return p->callback();}
namespace Script {
int setData(PyObject* p,float& value){value=p->number;return 0;}
int setData(PyObject* p,bool& value){value=p->number!=0;return 0;}
int setData(PyObject* p,std::vector<std::string>& value){value=p->names;return 0;}
}
struct StockComponentState:std::enable_shared_from_this<StockComponentState> {
    bool valid_=true;virtual ~StockComponentState()=default;
    bool valid()const{return valid_;}
    virtual void activate()=0;virtual void deactivate()=0;virtual void destroy()=0;
    virtual PyObject* wrap(bool)=0;
};
struct DetailedState:StockComponentState {
    wot::DetailedEngineStateData data;
    void activate()override{}void deactivate()override{}void destroy()override{valid_=false;}
    PyObject* wrap(bool)override{return nullptr;}
};
struct Source {int id;};struct Voice {std::shared_ptr<Source> source;};
using StockAudioSource=std::shared_ptr<Source>;using StockAudioVoice=std::shared_ptr<Voice>;
static bool ready=true;static std::vector<std::string> events;
static std::map<std::string,float> rtpc;
static std::map<std::string,std::string> switches;
StockAudioSource stockAudioVoiceSource(const StockAudioVoice& p){return p?p->source:nullptr;}
bool stockAudioReady(){return ready;}
std::string key(const StockAudioSource& s,const std::string& n){return std::to_string(s->id)+":"+n;}
bool setStockAudioSourceRTPC(const StockAudioSource& s,const std::string& n,float v){rtpc[key(s,n)]=v;return true;}
bool setStockAudioGlobalRTPC(const std::string& n,float v){rtpc[n]=v;return true;}
bool setStockAudioGlobalState(const std::string& g,const std::string& v){switches[g]=v;return true;}
bool setStockAudioSourceSwitch(const StockAudioSource& s,const std::string& g,const std::string& v){switches[key(s,g)]=v;return true;}
bool setStockAudioSourceVolume(const StockAudioSource& s,float v){rtpc[key(s,"volume")]=v;return true;}
bool playStockAudioVoice(StockAudioVoice& s){events.push_back("play"+std::to_string(s->source->id));return true;}
void stopStockAudioVoice(const StockAudioVoice& s){if(s)events.push_back("stop"+std::to_string(s->source->id));}
void stopStockAudioSource(const StockAudioSource& s){if(s)events.push_back("stopall"+std::to_string(s->id));}
bool playStockAudioSource(const StockAudioSource& s,const std::string& n){events.push_back(key(s,n));return true;}
struct App {static App& instance(){static App a;return a;}double now=1.;double getGameTimeFrameStart(){return now;}};
static uint64 nextToken=0;
'''
WRAPPERS = r'''
struct PyTypeObject {};
static PyObject none;static PyObject* Py_None=&none;static PyObject* PyExc_TypeError=&exception;
using Py_ssize_t=std::ptrdiff_t;
#define Py_RETURN_NONE do { return Py_None; } while(0)
#define Py_Header(Name,Base) public:static PyTypeObject s_type_;static bool Check(PyObject* p){return dynamic_cast<Name*>(p)!=nullptr;}
#define PY_FACTORY_DECLARE() static PyObject* pyNew(PyObject*);
#define PY_METHOD_DECLARE(Name) PyObject* Name(PyObject*);
#define PY_TYPEOBJECT(Name) PyTypeObject Name::s_type_;
#define PY_BEGIN_METHODS(Name)
#define PY_METHOD(Name)
#define PY_END_METHODS()
#define PY_BEGIN_ATTRIBUTES(Name)
#define PY_END_ATTRIBUTES()
#define PY_FACTORY(Name,Module)
int PyArg_ParseTuple(PyObject*,const char*,...){return 0;}
int PyTuple_Check(PyObject*){return 0;}
Py_ssize_t PyTuple_GET_SIZE(PyObject*){return 0;}
PyObject* PyTuple_GET_ITEM(PyObject*,size_t){return Py_None;}
int PyCallable_Check(PyObject*){return 0;}
PyObject* PyErr_Occurred(){return error?&exception:nullptr;}
void PyErr_Print(){error=false;}
struct Matrix {};struct Vector3 {float x=0,y=0,z=0;};
using MatrixProviderPtr=std::shared_ptr<Matrix>;
namespace Script {
int setData(PyObject*,BW::string&){return 0;}
int setData(PyObject*,Vector3&){return 0;}
int setData(PyObject*,MatrixProviderPtr&){return 0;}
}
PyObject* PyObject_CallMethod(PyObject*,char*,char*,...){return nullptr;}
bool stockVehicleCompoundNode(PyObject*,const char*,Matrix&,Matrix&,Matrix&){return false;}
struct StockComponentWrapper:PyObject {
    std::shared_ptr<StockComponentState> state_;
    StockComponentWrapper(std::shared_ptr<StockComponentState> s,PyTypeObject*,bool):state_(s){}
    std::shared_ptr<StockComponentState> component()const{return state_;}
};
struct DetailedEngineState:PyObject {
    static bool Check(PyObject*){return false;}
    std::shared_ptr<DetailedState> state(){return nullptr;}
};
StockAudioVoice createStockAudioVoice(const BW::string&,const char*){return nullptr;}
StockAudioSource createStockAudioSource(const char*,bool){return nullptr;}
bool setStockAudioSourceMatrix(const StockAudioSource&,MatrixProviderPtr){return true;}
PyObject* wrapStockAudioSource(const StockAudioSource&){return nullptr;}
namespace Moo {struct RC {uint32 frameTimestamp(){return 0;}};RC& rc(){static RC r;return r;}}
'''
CASES = r'''
PyObjectPtr scalar(float number){auto p=new PyObject;p->callback=[number]{auto r=new PyObject;r->number=number;return r;};return PyObjectPtr(p,PyObjectPtr::STEAL_REFERENCE);}
PyObjectPtr materials(std::vector<std::string> names){auto p=new PyObject;p->callback=[names]{auto r=new PyObject;r->names=names;return r;};return PyObjectPtr(p,PyObjectPtr::STEAL_REFERENCE);}
std::shared_ptr<VehicleAuditionState> make(std::shared_ptr<DetailedState> detail,bool player){
    auto s=std::make_shared<VehicleAuditionState>();s->player=player;s->focused=player;
    s->engine=std::make_shared<Voice>();s->engine->source=std::make_shared<Source>();s->engine->source->id=1;
    s->tracks=std::make_shared<Voice>();s->tracks->source=std::make_shared<Source>();s->tracks->source->id=2;
    s->gun=std::make_shared<Source>();s->gun->id=3;s->detailed=detail;
    s->rotationLimit=2.f;s->updatePeriod=.1f;
    s->underwater=scalar(0).get();s->flying=scalar(1).get();s->inWater=scalar(0).get();
    s->materials=materials({"ground","stone","unknown"}).get();
    s->speed[0]=scalar(.25f).get();s->speed[1]=scalar(2.f).get();
    s->scroll[0]=scalar(2.f).get();s->scroll[1]=scalar(-4.f).get();
    s->scroll[2]=scalar(3.f).get();s->scroll[3]=scalar(8.f).get();
    s->activate();return s;
}
int main(){
    auto d=std::make_shared<DetailedState>();auto& v=d->data;
    v.mode=1;v.rpm=150;v.engine_load=2;v.speed=40;v.relative_speed=2;v.rotation_speed=-2;
    v.relative_rotation_speed=-3;v.physics_load=.75;v.roughness=-.5;v.gear=3;
    v.physics_rpm=.6;v.physics_rpm_absolute=1600;
    auto s=make(d,true);assert(s->active);assert((events==std::vector<std::string>{"play1","play2"}));
    s->shifted=true;assert(s->update());
    assert(rtpc["1:RTPC_ext_rpm"]==100 && rtpc["2:RTPC_ext_rpm"]==100);
    assert(rtpc["1:RTPC_ext_speed_abs"]==30 && rtpc["RTPC_ext_speed_abs_global"]==40);
    assert(rtpc["1:RTPC_ext_physic_gear_3"]==100 && rtpc["2:RTPC_ext_physic_gear_2"]==0);
    assert(!rtpc.count("1:RTPC_ext_gear_1"));
    assert(rtpc["2:RTPC_ext_flying"]==1 && !rtpc.count("1:RTPC_ext_flying"));
    assert(std::fabs(rtpc["2:RTPC_ext_hardness"]-1.1f/3.f)<1e-7f);
    assert(switches["2:SWITCH_ext_surfaceType"]=="SWITCH_ext_surfaceType_ground");
    assert(rtpc["1:RTPC_ext_slide_friction"]==1 && rtpc["2:RTPC_ext_friction"]==.8f);
    assert(std::fabs(rtpc["2:RTPC_ext_treads_sum_affect"]-.66f)<1e-7f);
    assert(rtpc["1:RTPC_ext_speed_scroll"]==2.f);
    assert(rtpc["1:RTPC_ext_acc_abs"]==0);
    v.speed=0;App::instance().now=2;assert(s->update());assert(rtpc["1:RTPC_ext_acc_abs"]==-1.5f);
    s->inWater=scalar(1).get();assert(s->update());
    assert(std::fabs(rtpc["2:RTPC_ext_hardness"]-.2f)<1e-7f);
    assert(switches["2:SWITCH_ext_surfaceType"]=="SWITCH_ext_surfaceType_water");
    s->materials=materials({}).get();assert(s->update());
    assert(rtpc["2:RTPC_ext_hardness"]==0 && s->surface=="ground");
    s->entityID=7;s->camera("arcade",9);assert(s->focused);
    s->camera("postmortem",9);assert(!s->focused);
    assert(switches["1:SWITCH_ext_postMortem"]=="SWITCH_ext_postMortem_npc");
    events.clear();assert(s->transition(3,0));
    assert((events==std::vector<std::string>{"1:eng_restoring","1:eng_repairing_broken"}));
    assert(!s->transition(4,0) && error);error=false;
    s->destroy();s->destroy();assert(!s->valid() && auditions.empty());

    rtpc.clear();events.clear();s=make(d,false);assert(s->update());
    assert(rtpc["1:RTPC_ext_turbine_npc"]==100 && rtpc["1:volume"]==1);
    assert(!rtpc.count("RTPC_ext_engine_load_global"));
    // A real producer may synchronously destroy its component. No later audio
    // write or stale periodic receipt may execute after that callback returns.
    auto callback=new PyObject;callback->callback=[s]{s->destroy();return new PyObject;};
    s->underwater=callback;Py_XDECREF(callback);rtpc.clear();
    assert(!s->update() && !s->valid());assert(!rtpc.count("1:RTPC_ext_submersion"));
    assert(auditions.empty());
    s=std::make_shared<VehicleAuditionState>();s->activate();assert(error && !s->active);
    finiStockVehicleAuditions();finiStockVehicleAuditions();
}
'''


class VehicleAuditionTest(unittest.TestCase):
    def test_original_state_body(self):
        source = (ROOT / 'bigworld/stock_vehicle_audio.cpp').read_text()
        body = source[source.index('class VehicleAuditionState;'):source.rindex('\nBW_END_NAMESPACE')]
        body = 'namespace {\n' + body
        with tempfile.TemporaryDirectory(prefix='stock-vehicle-audio-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(HOST + WRAPPERS + body + CASES)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                            '-I', str(ROOT / 'bigworld'), str(path / 'probe.cpp'),
                            '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
