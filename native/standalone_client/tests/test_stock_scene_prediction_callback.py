"""Exercise the bridge's exact callback body through real CPython 2.7 C API.

Scene metadata and BigWorld queries are not simulated by this ABI check.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / 'bigworld/stock_scene_prediction.cpp'

PRELUDE = r'''
#include <Python.h>
#include <cmath>
#include <cstdint>
#include <string>
using PyEntity=PyObject;
using int32=std::int32_t;
class PyObjectPtr {
public:
    static const bool STEAL_REFERENCE=true;
    PyObjectPtr(PyObject* object,bool steal=false):object_(object) { if (!steal) Py_XINCREF(object_); }
    ~PyObjectPtr() { Py_XDECREF(object_); }
    PyObject* get() const { return object_; }
    explicit operator bool() const { return object_!=NULL; }
private:
    PyObject* object_;
};
struct StockObstacleIdentity { std::uint32_t chunkID=0,itemIndex=0;std::string filename;float scale=0; };
struct Vector3 { float x,y,z;float length() const { return std::sqrt((x*x+y*y)+z*z); } };
'''

SUFFIX = r'''
PyObject* query(PyObject*,PyObject* args) {
    PyObject* owner;
    unsigned long chunk,item;
    unsigned material;
    const char* filename;
    float scale;
    Vector3 velocity;
    if (!PyArg_ParseTuple(args,"OkkIsf(fff)",&owner,&chunk,&item,&material,&filename,
        &scale,&velocity.x,&velocity.y,&velocity.z)) return NULL;
    StockObstacleIdentity identity;
    identity.chunkID=static_cast<std::uint32_t>(chunk);
    identity.itemIndex=static_cast<std::uint32_t>(item);
    identity.filename=filename;identity.scale=scale;
    bool allowed=false;
    if (!mayBreakDestructible(owner,identity,material,velocity,allowed)) return NULL;
    return PyBool_FromLong(allowed);
}
PyMethodDef methods[]={{"query",query,METH_VARARGS,NULL},{NULL,NULL,0,NULL}};
PyMODINIT_FUNC init_stock_scene_callback_probe() { Py_InitModule("_stock_scene_callback_probe",methods); }
'''

RUNNER = r'''
import sys,types
sys.path.insert(0,sys.argv[1])
import _stock_scene_callback_probe as probe
module=types.ModuleType('Vehicle')
class Vehicle(object):
    result=False
    calls=[]
    def _isDestructibleMayBeBroken(self,*args):
        self.calls.append(args)
        if isinstance(self.result,Exception):raise self.result
        return self.result
module.Vehicle=Vehicle
sys.modules['Vehicle']=module
owner=Vehicle()
for result,expected in [(False,False),(True,True),(None,True),(0,True),([],True)]:
    owner.result=result
    assert probe.query(owner,0xffffffff,0x80000000,71,'objects/fragile.model',2.5,(3,4,12)) is expected
    assert owner.calls[-1]==(-1,-2147483648,71,'objects/fragile.model',2.5,13.0)
owner.result=RuntimeError('callback failed')
try:probe.query(owner,1,2,71,'objects/fragile.model',1,(3,4,12))
except RuntimeError as error:assert str(error)=='callback failed'
else:raise AssertionError('callback error became clearance')
print('Actual CPython 2.7 destruction callback ABI and explicit error boundary checks passed')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python27', required=True)
    args = parser.parse_args()
    source = SOURCE.read_text()
    helper = source[source.index('bool mayBreakDestructible('):source.index('class PredictionEdgeHit')]
    include = subprocess.check_output([args.python27, '-c',
        'from distutils.sysconfig import get_python_inc; print(get_python_inc())'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='wot-scene-callback-') as directory:
        path = Path(directory)
        (path / 'probe.cpp').write_text(PRELUDE + helper + SUFFIX)
        (path / 'verify.py').write_text(RUNNER)
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wno-register',
            '-shared', '-fPIC', '-undefined', 'dynamic_lookup', '-Wall', '-Wextra', '-Werror',
            '-I' + include, str(path / 'probe.cpp'), '-o',
            str(path / '_stock_scene_callback_probe.so')], check=True)
        subprocess.run([args.python27, str(path / 'verify.py'), directory],
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)


if __name__ == '__main__':
    main()
