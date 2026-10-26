"""Compile actual Svarog ownership bodies against a small callback host.

This proves transfer/reclaim and callback ordering, including reentrancy. The
host deliberately does not claim Python C ABI or Windows engine validation.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HOST = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>
namespace BW { using string = std::string; }
using uint64 = std::uint64_t;
struct PyObject { int refs=1;virtual ~PyObject()=default; };
struct PyTypeObject {};
PyTypeObject type;
struct PyObjectPlusWithWeakReference : PyObject {
    explicit PyObjectPlusWithWeakReference(PyTypeObject*) {}
};
static PyObject none;
static PyObject* Py_None=&none;
static PyObject* PyExc_ValueError=&none;
static bool error=false;
void Py_INCREF(PyObject* p) { ++p->refs; }
void Py_XINCREF(PyObject* p) { if(p) Py_INCREF(p); }
void Py_DECREF(PyObject* p) { if(--p->refs==0 && p!=Py_None) delete p; }
void PyErr_SetString(PyObject*,const char*) { error=true; }
void PyErr_Clear() { error=false; }
int PyArg_ParseTuple(PyObject*,const char*,...) { return 1; }
void PyErr_Fetch(PyObject** type,PyObject** value,PyObject** trace) {
    *type=error?Py_None:nullptr;*value=nullptr;*trace=nullptr;error=false;
}
void PyErr_Restore(PyObject* type,PyObject*,PyObject*) { error=type!=nullptr; }
#define Py_RETURN_NONE do { Py_INCREF(Py_None);return Py_None; } while(0)
#define Py_Header(Name,Base) public:static bool Check(PyObject* p) { return dynamic_cast<Name*>(p)!=nullptr; }
#define PY_RO_ATTRIBUTE_DECLARE(Member,Name)
#define PY_METHOD_DECLARE(Name) PyObject* Name(PyObject*);
class PyObjectPtr {
public:
    PyObjectPtr(PyObject* p=nullptr):p_(p) { Py_XINCREF(p_); }
    PyObjectPtr(const PyObjectPtr& p):PyObjectPtr(p.p_) {}
    ~PyObjectPtr() { if(p_) Py_DECREF(p_); }
    PyObjectPtr& operator=(PyObject* p) { Py_XINCREF(p);if(p_) Py_DECREF(p_);p_=p;return *this; }
    PyObjectPtr& operator=(const PyObjectPtr& p) { return *this=p.p_; }
    explicit operator bool() const { return p_!=nullptr; }
    PyObject* get() const { return p_; }
private:PyObject* p_;
};
struct Text : PyObject { explicit Text(std::string s):value(s){}std::string value; };
struct Callable : PyObject { explicit Callable(std::function<void()> f):fn(f){}std::function<void()> fn; };
struct ScriptObject : PyObject { std::map<std::string,std::function<void()>> methods; };
PyObject* PyTuple_New(int) { return new PyObject(); }
PyObject* PyObject_GetAttrString(PyObject* p,const char* name) {
    auto script=dynamic_cast<ScriptObject*>(p);
    return script && script->methods.count(name)?new Callable(script->methods.at(name)):nullptr;
}
namespace Script {
int setData(PyObject* p,std::string& value) {
    if(auto text=dynamic_cast<Text*>(p)) { value=text->value;return 0; }
    error=true;return -1;
}
bool call(PyObject* fn,PyObject* args,const char*) {
    auto callable=dynamic_cast<Callable*>(fn);if(callable) callable->fn();
    if(fn) Py_DECREF(fn);Py_DECREF(args);return callable!=nullptr;
}
}
'''

CASES = r'''
class StockComponentSystem : public StockComponentWrapper {
public:StockComponentSystem(const std::shared_ptr<StockComponentState>& state,bool own):
    StockComponentWrapper(state,&type,own) {}
};
PyObject* SystemState::wrap(bool own) { return new StockComponentSystem(shared_from_this(),own); }
struct NativeState : StockComponentState {
    bool active=false;unsigned starts=0,stops=0,deletes=0;
    void activate() override { if(valid_ && !active) {active=true;++starts;} }
    void deactivate() override { if(active) {active=false;++stops;} }
    void destroy() override { if(valid_) {valid_=false;++deletes;} }
    PyObject* wrap(bool own) override { return new StockComponentWrapper(shared_from_this(),&type,own); }
};
int main() {
    auto native=std::make_shared<NativeState>();
    auto original=new StockComponentWrapper(native,&type,true);
    auto owner=std::make_shared<SystemState>();
    assert(owner->add(original->transfer(),"native"));
    assert(original->component()==native);
    assert(!original->transfer() && error);PyErr_Clear();
    auto found=static_cast<StockComponentWrapper*>(owner->find("native"));
    assert(found->component()==native);
    owner->activate();assert(native->starts==1);
    Text name("native");
    auto reclaimed=static_cast<StockComponentWrapper*>(owner->remove(&name));
    assert(reclaimed->component()==native);
    owner->destroy();assert(native->valid());
    delete original;delete found;assert(native->deletes==0);
    delete reclaimed;assert(native->deletes==1 && native->stops==1);

    // Exact owning-system teardown: deactivate the complete system, then
    // each entry's owner deactivates and destroys it, in insertion order.
    std::vector<std::string> events;
    auto system=std::make_shared<SystemState>();
    ScriptObject a,b;
    for(auto pair:{std::make_pair(&a,std::string("a")),std::make_pair(&b,std::string("b"))})
        for(const auto& method:{"activate","deactivate","destroy"})
            pair.first->methods[method]=[&,id=pair.second,m=std::string(method)] { events.push_back(id+"."+m); };
    system->add(std::make_shared<ScriptComponentState>(&a),"a");
    system->add(std::make_shared<ScriptComponentState>(&b),"b");
    auto wrapper=new StockComponentWrapper(system,&type,true);
    system->activate();delete wrapper;
    assert((events==std::vector<std::string>{"a.activate","b.activate","a.deactivate","b.deactivate",
        "a.deactivate","a.destroy","b.deactivate","b.destroy"}));

    // A synchronous callback removes a later component. The retired receipt
    // must not be activated after removal, and removal returns its identity.
    events.clear();system=std::make_shared<SystemState>();
    a.methods["activate"]=[&] {
        events.push_back("a.activate");PyObject* removed=system->remove(&b);
        assert(removed==&b);Py_DECREF(removed);
    };
    system->add(std::make_shared<ScriptComponentState>(&a),"a");
    system->add(std::make_shared<ScriptComponentState>(&b),"b");
    system->activate();
    assert((events==std::vector<std::string>{"a.activate","b.deactivate","b.destroy"}));
    system->deactivate();system->destroy();

    // Destroy re-enters the same wrapper from a real script lifecycle call.
    // The callback-visible owner must already be empty at that boundary.
    events.clear();system=std::make_shared<SystemState>();
    wrapper=new StockComponentWrapper(system,&type,true);bool reentered=false;
    a.methods["deactivate"]=[&] {
        events.push_back("a.deactivate");
        if(!reentered) {
            reentered=true;assert(!wrapper->component());
            Py_DECREF(wrapper->py_destroy(nullptr));
        }
    };
    system->add(std::make_shared<ScriptComponentState>(&a),"a");
    Py_DECREF(wrapper->py_destroy(nullptr));
    assert((events==std::vector<std::string>{"a.deactivate","a.deactivate","a.destroy"}));
    delete wrapper;

    // Destruction also preserves the Python exception that triggered cleanup.
    auto last=std::make_shared<NativeState>();
    auto lastWrapper=new StockComponentWrapper(last,&type,true);
    error=true;delete lastWrapper;
    assert(error && last->deletes==1);PyErr_Clear();
}
'''


class StockComponentOwnershipTest(unittest.TestCase):
    def test_public_manual_method_macro_names(self):
        # The SDK's PY_METHOD_DECLARE declares its literal argument, while
        # PY_METHOD(foo) references _py_foo. It does not prepend py_ itself.
        # Numeric body extraction cannot catch a mismatched class declaration.
        for name in ('stock_vehicle_components.hpp', 'stock_vehicle_components.cpp',
                     'stock_vehicle_visuals.cpp'):
            source = (ROOT / 'bigworld' / name).read_text()
            for method in re.findall(r'PY_METHOD_DECLARE\(\s*(\w+)\s*\)', source):
                self.assertTrue(method.startswith('py_') or method == 'pyCall',
                                '%s declares %s without the public SDK prefix' % (name, method))

    def test_actual_owner_and_callback_bodies(self):
        header = (ROOT / 'bigworld/stock_vehicle_components.hpp').read_text()
        classes = header[header.index('class StockComponentState'):header.index('\nextern int')]
        source = (ROOT / 'bigworld/stock_vehicle_components.cpp').read_text()
        owners = source[source.index('StockComponentWrapper::StockComponentWrapper('):
                        source.index('\nPY_TYPEOBJECT( StockComponentWrapper )')]
        components = source[source.index('namespace\n{\nclass ScriptComponentState'):
                            source.index('\nclass StockComponentSystem')]
        program = HOST + classes + owners + components + CASES
        with tempfile.TemporaryDirectory(prefix='stock-components-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', str(path / 'probe.cpp'),
                            '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
