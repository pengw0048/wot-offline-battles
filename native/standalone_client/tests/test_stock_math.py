"""Verify the recovered MatrixProvider/slerp boundary through the real wrapper.

Host fakes record DirectX delegation and provider ownership. Native camera
interpolation and rendering still require the Windows client.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]

HOST = r'''
#include <Python.h>
#include <cassert>
#include <array>
#include <vector>
#define WOT_STOCK_MATH_PORTABLE
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define PY_MODULE_FUNCTION(...)
struct Matrix {
    float y=0,p=0,r=0,translation=0,scale=1;
    float yaw()const{return y;}float pitch()const{return p;}float roll()const{return r;}
};
struct D3DXQUATERNION { float y,p,r; };
std::vector<std::array<float,3>> rotations;
std::vector<float> amounts;
void D3DXQuaternionRotationYawPitchRoll(D3DXQUATERNION* out,float y,float p,float r){
    rotations.push_back({{y,p,r}});*out={y,p,r};
}
void D3DXQuaternionSlerp(D3DXQUATERNION* out,const D3DXQUATERNION* a,const D3DXQUATERNION* b,float t){
    amounts.push_back(t);*out={a->y+(b->y-a->y)*t,a->p+(b->p-a->p)*t,a->r+(b->r-a->r)*t};
}
void D3DXMatrixRotationQuaternion(Matrix* out,const D3DXQUATERNION* q){*out=Matrix();out->y=q->y;out->p=q->p;out->r=q->r;}
struct Provider { Matrix value;int reads=0; };
void dispose(PyObject* object){delete static_cast<Provider*>(PyCapsule_GetPointer(object,"MatrixProvider"));}
PyObject* provider(const Matrix& value){return PyCapsule_New(new Provider{value,0},"MatrixProvider",dispose);}
namespace Script {
int setData(PyObject* object,Matrix& output,const char* name){
    if(!PyCapsule_IsValid(object,"MatrixProvider")){PyErr_Format(PyExc_TypeError,"%s must be a MatrixProvider",name);return -1;}
    auto input=static_cast<Provider*>(PyCapsule_GetPointer(object,"MatrixProvider"));++input->reads;output=input->value;return 0;
}
int setData(PyObject* object,float& output,const char* name){
    if(PyFloat_Check(object)){output=float(PyFloat_AsDouble(object));return 0;}
    if(PyInt_Check(object)){output=float(PyInt_AsLong(object));return 0;}
    if(PyLong_Check(object)){auto value=PyLong_AsUnsignedLong(object);if(!PyErr_Occurred()){output=float(value);return 0;}}
    PyErr_Format(PyExc_TypeError,"%s must be set to a float",name);return -1;
}
PyObject* getData(const Matrix& value){return provider(value);}
}
#include "@PRODUCTION@"
PyObject* invoke(PyObject* a,PyObject* b,PyObject* t){
    PyObject* args=PyTuple_Pack(3,a,b,t);auto result=py_slerp(args);Py_DECREF(args);return result;
}
PyObject* exposed(PyObject*,PyObject* args){return py_slerp(args);}
PyMethodDef methods[]={{"slerp",exposed,METH_VARARGS,NULL},{NULL,NULL,0,NULL}};
void run(const char* code){if(PyRun_SimpleString(code)){fputs(code,stderr);assert(false);}}
int main(){
    Py_Initialize();auto math=Py_InitModule("Math",methods);assert(math);
    Matrix a,b;a.y=1;a.p=2;a.r=3;a.translation=100;a.scale=7;b.y=4;b.p=5;b.r=6;b.translation=-200;b.scale=3;
    PyObject* first=provider(a);PyObject* last=provider(b);PyObject* coefficient=PyFloat_FromDouble(.25);
    auto firstOwner=static_cast<Provider*>(PyCapsule_GetPointer(first,"MatrixProvider"));
    auto lastOwner=static_cast<Provider*>(PyCapsule_GetPointer(last,"MatrixProvider"));
    auto firstRefs=Py_REFCNT(first),lastRefs=Py_REFCNT(last);
    PyObject* output=invoke(first,last,coefficient);assert(output&&output!=first&&output!=last);
    auto value=static_cast<Provider*>(PyCapsule_GetPointer(output,"MatrixProvider"));
    assert(value->value.translation==0&&value->value.scale==1);
    assert((rotations==std::vector<std::array<float,3>>{{{1,2,3}},{{4,5,6}}}));
    assert(firstOwner->reads==1&&lastOwner->reads==1&&Py_REFCNT(first)==firstRefs&&Py_REFCNT(last)==lastRefs);
    assert(firstOwner->value.translation==100&&lastOwner->value.translation==-200);
    firstOwner->value.y=99;assert(value->value.y==1.75f);firstOwner->value=a;
    Py_DECREF(output);
    for(float t:{-1.f,0.f,1.f,2.f}){
        PyObject* amount=PyFloat_FromDouble(t);output=invoke(first,last,amount);assert(output&&amounts.back()==t);Py_DECREF(output);Py_DECREF(amount);
    }
    PyObject* wrong=Py_BuildValue("(iii)",1,2,3);
    auto before=rotations.size();output=invoke(wrong,last,coefficient);assert(!output&&PyErr_ExceptionMatches(PyExc_TypeError));PyErr_Clear();
    output=invoke(first,wrong,coefficient);assert(!output&&PyErr_ExceptionMatches(PyExc_TypeError));PyErr_Clear();
    output=invoke(first,last,wrong);assert(!output&&PyErr_ExceptionMatches(PyExc_TypeError));PyErr_Clear();
    assert(rotations.size()==before);
    PyObject* empty=PyTuple_New(0);assert(!py_slerp(empty)&&PyErr_ExceptionMatches(PyExc_TypeError));PyErr_Clear();Py_DECREF(empty);
    PyObject* four=PyTuple_Pack(4,first,last,coefficient,wrong);assert(!py_slerp(four)&&PyErr_ExceptionMatches(PyExc_TypeError));PyErr_Clear();Py_DECREF(four);
    Py_DECREF(wrong);
    PyModule_AddObject(math,"source",first);PyModule_AddObject(math,"target",last);
    // The exact consumer owns interpolation bounds and target translation.
    run("import os\n"
        "if os.environ.get('STOCK_ARTY_PYC'):\n"
        " import marshal,types,Math\n"
        " root=marshal.loads(open(os.environ['STOCK_ARTY_PYC'],'rb').read()[8:])\n"
        " klass=next(c for c in root.co_consts if isinstance(c,types.CodeType) and c.co_name=='ArtyCamera')\n"
        " code=next(c for c in klass.co_consts if isinstance(c,types.CodeType) and c.co_name=='__interpolateStates')\n"
        " class Utils(object):\n"
        "  clamp=staticmethod(lambda a,b,v:max(a,min(b,v)))\n"
        "  lerp=staticmethod(lambda a,b,t:a+(b-a)*t)\n"
        " class Value(object):pass\n"
        " instance=Value();instance._ArtyCamera__cfg={'interpolationSpeed':2}\n"
        " instance._ArtyCamera__sourceMatrix=Math.source\n"
        " instance._ArtyCamera__targetMatrix=Value();instance._ArtyCamera__targetMatrix.translation=10\n"
        " original=instance._ArtyCamera__targetMatrix\n"
        " function=types.FunctionType(code,{'mathUtils':Utils,'slerp':Math.slerp})\n"
        " source,target=function(instance,.25,30,Math.target)\n"
        " assert source is instance._ArtyCamera__sourceMatrix and source is not Math.source\n"
        " assert target is original and target.translation==20\n"
        " root=marshal.loads(open(os.environ['STOCK_STRATEGIC_PYC'],'rb').read()[8:])\n"
        " klass=next(c for c in root.co_consts if isinstance(c,types.CodeType) and c.co_name=='StrategicCamerasInterpolator')\n"
        " code=next(c for c in klass.co_consts if isinstance(c,types.CodeType) and c.co_name=='__cameraUpdate')\n"
        " def state(source,position,pivot):\n"
        "  result=Value();result.source=source;result.target=Value();result.target.b=Value()\n"
        "  result.target.b.translation=position;result.pivotPosition=pivot;return result\n"
        " instance=Value();prefix='_StrategicCamerasInterpolator__'\n"
        " for name,value in {'elapsedTime':0,'prevTime':0,'totalInterpolationTime':2,'easingMethod':lambda t,b,d:t/d,'initialState':state(Math.source,10,30),'finalState':state(Math.target,30,50),'cam':state(None,0,0),'initialFov':.5,'finalFov':1.}.items():setattr(instance,prefix+name,value)\n"
        " world=Value();world.timeExact=lambda:1.;projection=Value();world.projection=lambda:projection\n"
        " function=types.FunctionType(code,{'BigWorld':world,'Math':Math,'mathUtils':Utils})\n"
        " assert function(instance)==0.\n"
        " camera=getattr(instance,prefix+'cam')\n"
        " assert camera.source is not Math.source and camera.source is not Math.target\n"
        " assert camera.target.b.translation==20 and camera.pivotPosition==40 and projection.fov==.75\n");
    Py_DECREF(coefficient);Py_Finalize();
}
'''


class StockMathTest(unittest.TestCase):
    def test_matrix_provider_sampling_errors_and_exact_camera_consumer(self):
        python = Path(os.environ.get('WOT_PYTHON27', '/Users/peng/.pyenv/versions/2.7.18/bin/python2.7'))
        config = python.with_name('python2.7-config')
        if not config.exists():
            self.skipTest('Pinned CPython 2.7 development files unavailable')
        with tempfile.TemporaryDirectory(prefix='stock-math-') as directory:
            root = Path(directory)
            source = root / 'host.cpp'
            source.write_text(HOST.replace('@PRODUCTION@', str(ROOT / 'bigworld/stock_math.cpp')))
            flags = [x for x in shlex.split(subprocess.check_output([str(config), '--cflags'], text=True)) if x != '-DNDEBUG']
            libs = shlex.split(subprocess.check_output([str(config), '--ldflags'], text=True))
            binary = root / 'host'
            subprocess.run(['c++', '-std=c++14', '-Wno-deprecated-register', '-UNDEBUG', str(source),
                            *flags, *libs, '-o', str(binary)], check=True)
            environment = dict(os.environ)
            client = environment.get('WOT_0922_CLIENT')
            if client:
                with zipfile.ZipFile(Path(client) / 'res/packages/scripts.pkg') as archive:
                    pyc = root / 'ArtyCamera.pyc'
                    pyc.write_bytes(archive.read('scripts/client/AvatarInputHandler/DynamicCameras/ArtyCamera.pyc'))
                    strategic = root / 'StrategicCamerasInterpolator.pyc'
                    strategic.write_bytes(archive.read('scripts/client/AvatarInputHandler/StrategicCamerasInterpolator.pyc'))
                environment['STOCK_ARTY_PYC'] = str(pyc)
                environment['STOCK_STRATEGIC_PYC'] = str(strategic)
            subprocess.run([str(binary)], env=environment, check=True)


if __name__ == '__main__':
    unittest.main()
