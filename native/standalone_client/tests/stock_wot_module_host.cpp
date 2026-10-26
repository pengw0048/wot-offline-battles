// Host boundary fixture: real CPython 2.7, recorded DirectX delegation only.
#include <Python.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <array>
#include <vector>
#define WOT_STOCK_WOT_MODULE_PORTABLE
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define PY_MODULE_FUNCTION(...)
using uint64 = std::uint64_t;
using uint32 = std::uint32_t;
struct Matrix {
    float m[4][4] = {};
    float& operator()(unsigned a,unsigned b){return m[a][b];}
    float operator()(unsigned a,unsigned b)const{return m[a][b];}
};
struct StockAuxVehiclePhysicsData {float yaw,pitch,roll,leftScroll,rightScroll,normalisedRPM;};
std::vector<std::array<float,3>> rotations;
void D3DXMatrixRotationYawPitchRoll(Matrix* out,float y,float p,float r){
    rotations.push_back({{y,p,r}});*out=Matrix();
    (*out)(0,0)=y;(*out)(1,1)=p;(*out)(2,2)=r;(*out)(3,3)=1;
}
struct Provider {Matrix value;int reads=0;};
void dispose(PyObject* object){delete static_cast<Provider*>(PyCapsule_GetPointer(object,"MatrixProvider"));}
PyObject* provider(const Matrix& value){return PyCapsule_New(new Provider{value,0},"MatrixProvider",dispose);}
namespace Script {
int setData(PyObject* object,Matrix& value,const char* name){
    if(!PyCapsule_IsValid(object,"MatrixProvider")){PyErr_Format(PyExc_TypeError,"%s must be a MatrixProvider",name);return -1;}
    auto* owner=static_cast<Provider*>(PyCapsule_GetPointer(object,"MatrixProvider"));++owner->reads;value=owner->value;return 0;
}
// Mirror public pyscript/script.cpp's uint64 overload, not PyArg's masking K.
int setData(PyObject* object,uint64& value,const char* name){
    if(PyLong_Check(object)){value=PyLong_AsUnsignedLongLong(object);if(!PyErr_Occurred())return 0;}
    if(PyInt_Check(object)){
        long n=PyInt_AsLong(object);if(n>=0){value=static_cast<uint64>(n);return 0;}
        PyErr_Format(PyExc_ValueError,"Cannot set %s of type unsigned long to %d",name,int(n));return -1;
    }
    if(PyFloat_Check(object)){value=static_cast<uint64>(PyFloat_AsDouble(object));return 0;}
    PyErr_Format(PyExc_TypeError,"%s must be set to a unsigned long",name);return -1;
}
PyObject* getData(const Matrix& value){return provider(value);}
}
#include "../bigworld/stock_wot_module.cpp"
PyObject* decode(PyObject*,PyObject* args){return py_unpackAuxVehiclePhysicsData(args);}
PyObject* stabilise(PyObject*,PyObject* args){return py_computeStabilisedVehicleMatrixU64(args);}
PyMethodDef methods[]={
    {"unpackAuxVehiclePhysicsData",decode,METH_VARARGS,NULL},
    {"computeStabilisedVehicleMatrixU64",stabilise,METH_VARARGS,NULL},
    {NULL,NULL,0,NULL}
};
void python(const char* code){if(PyRun_SimpleString(code)!=0){fputs(code,stderr);assert(false);}}
int main(int argc,char** argv){
    if(argc==4&&std::strcmp(argv[1],"--decode")==0){
        std::ifstream input(argv[2],std::ios::binary);std::ofstream output(argv[3],std::ios::binary);
        if(!input||!output)return 2;
        uint64 packed;
        while(input.read(reinterpret_cast<char*>(&packed),sizeof(packed))){
            auto d=unpackStockAuxVehiclePhysicsData(packed);
            const float fields[]={d.yaw,d.pitch,d.roll,d.leftScroll,d.rightScroll,d.normalisedRPM};
            output.write(reinterpret_cast<const char*>(fields),sizeof(fields));
        }
        return input.eof()&&output?0:2;
    }
    assert(argc==1);Py_NoSiteFlag=1;Py_Initialize();
    PyObject* module=Py_InitModule("WoT",methods);assert(module);
    Matrix source;for(unsigned i=0;i<4;++i)for(unsigned j=0;j<4;++j)source(i,j)=float(1+i*4+j);
    PyObject* input=provider(source);auto* owner=static_cast<Provider*>(PyCapsule_GetPointer(input,"MatrixProvider"));
    const Py_ssize_t refs=Py_REFCNT(input);const uint64 packed=0xe7c53a65432abcdeULL;
    auto decoded=unpackStockAuxVehiclePhysicsData(packed);
    PyObject* number=PyLong_FromUnsignedLongLong(packed);PyObject* args=PyTuple_Pack(2,input,number);
    PyObject* result=py_computeStabilisedVehicleMatrixU64(args);Py_DECREF(args);assert(result&&result!=input);
    auto* output=static_cast<Provider*>(PyCapsule_GetPointer(result,"MatrixProvider"));
    assert(owner->reads==1&&Py_REFCNT(input)==refs&&!std::memcmp(&owner->value,&source,sizeof(source)));
    assert((rotations.back()==std::array<float,3>{{decoded.yaw,decoded.pitch,decoded.roll}}));
    for(unsigned i=0;i<3;++i){assert(output->value(3,i)==source(3,i));assert(output->value(i,3)==0);}
    assert(output->value(3,3)==1);owner->value(3,0)=99;assert(output->value(3,0)==source(3,0));owner->value=source;
    Py_DECREF(result);Py_DECREF(number);
    PyModule_AddObject(module,"source",input);
    python(R"PY(
import WoT
assert len(WoT.unpackAuxVehiclePhysicsData(0))==6
assert all(type(x) is float for x in WoT.unpackAuxVehiclePhysicsData(0))
assert WoT.unpackAuxVehiclePhysicsData(True)==WoT.unpackAuxVehiclePhysicsData(1)
assert WoT.unpackAuxVehiclePhysicsData(1.9)==WoT.unpackAuxVehiclePhysicsData(1)
assert WoT.unpackAuxVehiclePhysicsData(0xffffffffffffffffL)[5]>1.19
for call in (lambda:WoT.unpackAuxVehiclePhysicsData(),lambda:WoT.unpackAuxVehiclePhysicsData(0,1),
             lambda:WoT.computeStabilisedVehicleMatrixU64(),lambda:WoT.computeStabilisedVehicleMatrixU64(WoT.source),
             lambda:WoT.computeStabilisedVehicleMatrixU64(WoT.source,0,1),
             lambda:WoT.computeStabilisedVehicleMatrixU64(None,0)):
    try:call()
    except TypeError:pass
    else:raise AssertionError('Invalid arity/provider accepted')
for value,kind in ((-1,ValueError),(-1L,TypeError),(1L<<64,TypeError),('1',TypeError),(None,TypeError),([],TypeError)):
    for call in (lambda:WoT.unpackAuxVehiclePhysicsData(value),lambda:WoT.computeStabilisedVehicleMatrixU64(WoT.source,value)):
        try:call()
        except kind:pass
        else:raise AssertionError('Invalid uint64 accepted')
import os
if os.environ.get('STOCK_AVATAR_PYC'):
    import marshal,types
    def find(code,name):
        if code.co_name==name:return code
        for child in code.co_consts:
            if isinstance(child,types.CodeType):
                found=find(child,name)
                if found is not None:return found
    class Value(object):pass
    root=marshal.loads(open(os.environ['STOCK_AVATAR_PYC'],'rb').read()[8:])
    code=find(root,'__onSetOwnVehicleAuxPhysicsData');assert code is not None
    events=[];vehicle=Value();vehicle.isStarted=True;vehicle.appearance=Value();vehicle.filter=Value()
    vehicle.appearance.updateTracksScroll=lambda *v:events.append(('tracks',v))
    vehicle.filter.syncStabilisedYPR=lambda *v:events.append(('ypr',v))
    player=Value();player.playerVehicleID=7;player.ownVehicleAuxPhysicsData=0xe7c53a65432abcdeL
    player.guiSessionProvider=Value();player.guiSessionProvider.invalidateVehicleState=lambda *v:events.append(('state',v))
    world=Value();world.entity=lambda identifier:vehicle if identifier==7 else None
    view=Value();view.RPM=23
    callback=types.FunctionType(code,{'WoT':WoT,'BigWorld':world,'VEHICLE_VIEW_STATE':view,'__builtins__':__builtins__})
    decoded=WoT.unpackAuxVehiclePhysicsData(player.ownVehicleAuxPhysicsData)
    callback(player,0)
    assert events==[('tracks',decoded[3:5]),('state',(23,decoded[5])),('ypr',decoded[:3])]
    vehicle.isStarted=False;events[:]=[];callback(player,0);assert events==[]
    vehicle.isStarted=True;del vehicle.filter.syncStabilisedYPR;callback(player,0)
    assert events==[('tracks',decoded[3:5]),('state',(23,decoded[5]))]
    world.entity=lambda identifier:None;events[:]=[];callback(player,0);assert events==[]
    root=marshal.loads(open(os.environ['STOCK_MODEL_ASSEMBLER_PYC'],'rb').read()[8:])
    code=find(root,'assembleDetailedEngineState')
    rpm=next(c for c in code.co_consts if isinstance(c,types.CodeType) and 'unpackAuxVehiclePhysicsData' in c.co_names)
    def cell(value):return (lambda:value).func_closure[0]
    getter=types.FunctionType(rpm,{'WoT':WoT},closure=(cell(player),))
    assert getter()==decoded[5]
)PY");
    Py_Finalize();std::cout<<"Real CPython wrapper conversion and recorded MatrixProvider ownership passed"<<std::endl;return 0;
}
