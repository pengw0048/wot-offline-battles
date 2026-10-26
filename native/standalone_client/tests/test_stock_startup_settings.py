"""Compile the actual script-owned settings and getter publication bodies.

The host exposes the public registry's relevant add/selection contract. It
does not prove Python C ABI, preference file IO, or Windows rendering.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupSettingsTest(unittest.TestCase):
    def test_script_consumers_keep_one_registry_owner(self):
        source = (ROOT / 'bigworld/stock_startup_services.cpp').read_text()
        source = '\n'.join(line for line in source.splitlines()
                           if not line.startswith('#include'))
        host = r'''
#include <cassert>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define PY_MODULE_FUNCTION(name, module)
#define PY_AUTO_MODULE_FUNCTION(...)
namespace BW { using string=std::string; }
struct BWResource {
    static BW::string version;
    static BW::string appProductVersion() { return version; }
};
BW::string BWResource::version;
struct PyObject { long value; };
PyObject* PyExc_RuntimeError=nullptr;
int errors=0;
bool PyArg_ParseTuple(PyObject* args,const char*) { return args->value==0; }
PyObject* PyInt_FromLong(long n) { static PyObject result;result.value=n;return &result; }
void PyErr_Format(PyObject*,const char*,const char*) { ++errors; }
namespace Moo {
template<class T> struct Ptr : std::shared_ptr<T> {
    Ptr()=default;Ptr(T* p):std::shared_ptr<T>(p) {}
    T* getObject() const { return this->get(); }
};
class GraphicsSetting {
public:
    using GraphicsSettingPtr=Ptr<GraphicsSetting>;
    struct Option { std::string name,description;bool supported; };
    std::string label;int active;
    std::vector<Option> options;
    GraphicsSetting(const char* l,const char*,int value,bool delayed,bool restart)
        :label(l),active(value) { assert(!delayed && !restart); }
    virtual ~GraphicsSetting()=default;
    virtual void onOptionSelected(int)=0;
    void addOption(const char* n,const char* d,bool supported) {
        options.push_back({n,d,supported});
    }
    static std::map<std::string,GraphicsSettingPtr> registry;
    static std::map<std::string,int> latent;
    static GraphicsSettingPtr getFromLabel(const char* n) { return registry[n]; }
    static void add(GraphicsSettingPtr p) {
        auto previous=latent.find(p->label);
        // Public add preserves the already selected option without calling
        // onOptionSelected; a different loaded preference is published.
        if(previous!=latent.end() && previous->second!=p->active) {
            p->active=previous->second;p->onOptionSelected(p->active);
        }
        registry[p->label]=p;
    }
};
std::map<std::string,GraphicsSetting::GraphicsSettingPtr> GraphicsSetting::registry;
std::map<std::string,int> GraphicsSetting::latent;
}
'''
        cases = r'''
int main() {
    assert(wg_getProductVersion().empty());
    BWResource::version="9.8.7-native-test";
    assert(wg_getProductVersion()==BWResource::version);
    PyObject zero{0},one{1};
    assert(py_wg_isVehicleDustEnabled(&zero)->value==1);
    assert(py_wg_isSniperModeSwingingEnabled(&zero)->value==1);
    assert(py_wg_isVehicleDustEnabled(&one)==nullptr);
    assert(py_wg_isSniperModeSwingingEnabled(&one)==nullptr);
    assert(initialiseStockScriptGraphicsSettings());
    auto dust=Moo::GraphicsSetting::getFromLabel("VEHICLE_DUST_ENABLED");
    auto swing=Moo::GraphicsSetting::getFromLabel("SNIPER_MODE_SWINGING_ENABLED");
    assert(dust && swing && dust->active==0 && swing->active==0);
    assert(dust->options.size()==2 && dust->options[0].name=="OFF");
    assert(dust->options[1].name=="ON" && dust->options[1].supported);
    assert(py_wg_isVehicleDustEnabled(&zero)->value==1);
    dust->onOptionSelected(0);
    assert(py_wg_isVehicleDustEnabled(&zero)->value==0);
    assert(py_wg_isSniperModeSwingingEnabled(&zero)->value==1);
    swing->onOptionSelected(0);
    dust->onOptionSelected(1);
    assert(py_wg_isVehicleDustEnabled(&zero)->value==1);
    assert(py_wg_isSniperModeSwingingEnabled(&zero)->value==0);
    assert(initialiseStockScriptGraphicsSettings());
    assert(Moo::GraphicsSetting::getFromLabel("VEHICLE_DUST_ENABLED")==dust);
    assert(Moo::GraphicsSetting::getFromLabel("SNIPER_MODE_SWINGING_ENABLED")==swing);
    assert(errors==0);
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-startup-settings-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(host + source + cases)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', str(path / 'probe.cpp'),
                            '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
