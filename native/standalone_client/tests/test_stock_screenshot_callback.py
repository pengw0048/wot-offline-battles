"""Exercise the production screenshot owner with the real CPython 2.7 C API."""
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'stock_screenshot_patch', ROOT / 'bigworld/patch_stock_screenshots.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)

class StockScreenshotCallbackTest(unittest.TestCase):
    def test_real_python_callback_replacement_reentrancy_and_shutdown(self):
        python = Path(os.environ.get('WOT_PYTHON27',
            '/Users/peng/.pyenv/versions/2.7.18/bin/python2.7'))
        config = python.with_name('python2.7-config')
        if not config.is_file():
            self.skipTest('Pinned CPython 2.7 development headers are unavailable')
        source = r'''
#define WOT_STOCK_SCREENSHOT_CALLBACK_PORTABLE
#include "stock_screenshot_callback.hpp"
#include <cassert>
static std::shared_ptr<wot::StockScreenshotCallback> owner=
    std::make_shared<wot::StockScreenshotCallback>();
static PyObject* setCallback(PyObject*,PyObject* args){
    PyObject* callback=NULL;
    if(!PyArg_UnpackTuple(args,"set",1,1,&callback))return NULL;
    if(!owner->set(callback)){
        PyErr_SetString(PyExc_RuntimeError,"owner closed");return NULL;
    }
    Py_RETURN_NONE;
}
static PyObject* complete(PyObject*,PyObject* args){
    int saved=0;const char* path=NULL;
    if(!PyArg_ParseTuple(args,"is",&saved,&path))return NULL;
    if(!owner->complete(saved!=0,path))return NULL;
    Py_RETURN_NONE;
}
static PyObject* closeOwner(PyObject*,PyObject*){owner->close();Py_RETURN_NONE;}
static PyMethodDef methods[]={
    {"set",setCallback,METH_VARARGS,NULL},
    {"complete",complete,METH_VARARGS,NULL},
    {"close",closeOwner,METH_NOARGS,NULL},
    {NULL,NULL,0,NULL}
};
PyMODINIT_FUNC init_screenshot_owner(){Py_InitModule("_screenshot_owner",methods);}
int main(int argc,char** argv){
    assert(argc==2);
    PyImport_AppendInittab("_screenshot_owner",init_screenshot_owner);
    Py_Initialize();
    FILE* script=fopen(argv[1],"rb");assert(script);
    const int result=PyRun_SimpleFile(script,argv[1]);fclose(script);
    if(result!=0){PyErr_Print();return 1;}
    // A save task may release its native owner after Python has stopped.
    auto delayedTaskOwner=owner;
    owner->close();
    Py_Finalize();
    owner.reset();delayedTaskOwner.reset();
}
'''
        script = r'''
import gc
import weakref
import _screenshot_owner as native
events = []
class Callback(object):
    def __init__(self, label): self.label = label
    def __call__(self, path): events.append((self.label, path))
first = Callback('first')
reference = weakref.ref(first)
native.set(first)
del first
gc.collect()
assert reference() is not None
native.complete(False, 'failed.png')
assert events == []
native.set(Callback('replacement'))
gc.collect()
assert reference() is None
native.complete(True, 'screenshots/\xe6\x88\x98\xe6\x96\x97.png')
assert events == [('replacement', 'screenshots/\xe6\x88\x98\xe6\x96\x97.png')]

class Reentrant(object):
    def __call__(self, path):
        native.set(Callback('nested'))
        gc.collect()
        native.complete(True, 'nested.png')
        events.append(('reentrant', path))
    def __del__(self): events.append(('released', 'reentrant'))
native.set(Reentrant())
native.complete(True, 'outer.png')
assert events[-3:] == [('nested', 'nested.png'), ('reentrant', 'outer.png'), ('released', 'reentrant')]

class ReplaceOnDelete(object):
    def __call__(self, path): raise AssertionError('replaced callback invoked')
    def __del__(self): native.set(Callback('destructor replacement'))
native.set(ReplaceOnDelete())
native.set(Callback('temporary'))
native.complete(True, 'current.png')
assert events[-1] == ('destructor replacement', 'current.png')
native.set(None)
before = list(events)
native.complete(True, 'cleared.png')
assert events == before
native.set(42) # Registration accepts an arbitrary PyObjectPtr.
try:
    native.complete(True, 'noncallable.png')
except TypeError:
    pass
else:
    raise AssertionError('non-callable did not fail when invoked')
native.set(None)

class CloseOnDelete(object):
    def __call__(self, path): pass
    def __del__(self):
        try: native.set(Callback('must not survive shutdown'))
        except RuntimeError: events.append(('closed', 'reentrant setter'))
native.set(CloseOnDelete())
native.close()
native.close()
native.set(None) # Personality.fini clears it after ordinary FiniTimeJobs.
assert events[-1] == ('closed', 'reentrant setter')
before = list(events)
native.complete(True, 'late.png')
assert events == before
try:
    native.set(Callback('late'))
except RuntimeError:
    pass
else:
    raise AssertionError('closed owner accepted a Python reference')
'''
        includes = shlex.split(subprocess.check_output(
            [str(config), '--includes'], text=True))
        linker = shlex.split(subprocess.check_output(
            [str(config), '--ldflags'], text=True))
        with tempfile.TemporaryDirectory(prefix='stock-screenshot-owner-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(source)
            (path / 'probe.py').write_text(script)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                '-Wall', '-Wextra', '-Werror', '-Wno-register',
                '-I', str(ROOT / 'bigworld')] + includes +
                [str(path / 'probe.cpp'), '-o', str(path / 'probe')] + linker,
                check=True)
            subprocess.run([str(path / 'probe'), str(path / 'probe.py')], check=True)

    def test_public_gpu_capture_and_both_entrypoints_share_save_completion(self):
        engine = Path(os.environ.get('WOT_PUBLIC_BIGWORLD',
            '/tmp/wot-bigworld-engine-14.4.1/programming/bigworld'))
        if not (engine / 'lib/moo/render_context.cpp').is_file():
            self.skipTest('Pinned public BigWorld source is unavailable')
        files = ('lib/moo/render_context.hpp', 'lib/moo/render_context.cpp',
                 'client/script_bigworld.cpp', 'client/app.cpp')
        with tempfile.TemporaryDirectory(prefix='stock-screenshot-patch-') as directory:
            root = Path(directory)
            for name in files:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(engine / name, target)
            PATCH.patch(root)
            first = {name: (root / name).read_text() for name in files}
            PATCH.patch(root)
            self.assertEqual(first, {name: (root / name).read_text() for name in files})
            capture = first['lib/moo/render_context.cpp']
            self.assertIn('D3DXLoadSurfaceFromSurface', capture)
            self.assertIn('queueStockScreenshotSave(systemMemorySurface', capture)
            self.assertNotIn(PATCH.PUBLIC_SAVE, capture)
            self.assertIn('queueStockScreenshotCapture( format, fullName );',
                first['client/script_bigworld.cpp'])
            self.assertNotIn('Screenshot saved: ', first['client/app.cpp'])
            self.assertIn('stockScreenshotCallback_->close();', capture)
            header = first['lib/moo/render_context.hpp']
            self.assertNotIn('#include "stock_screenshot_callback.hpp"', header)
            self.assertNotIn('Python.h', header)
            self.assertNotIn('make_shared<wot::StockScreenshotCallback>', header)
            self.assertIn('namespace wot { class StockScreenshotCallback; }', header)
            self.assertIn('stockScreenshotCallback_(std::make_shared<wot::StockScreenshotCallback>())', capture)
            self.assertIn('#include "stock_screenshot_callback.hpp"', capture)

            # Header consumers may copy the shared pointer while its pointee
            # remains incomplete. Construction and destruction are out of line.
            probe_header = '''#include <memory>
namespace wot { class StockScreenshotCallback; }
class RenderContext {
public:
    RenderContext();
    ~RenderContext();
    std::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback() const
        { return stockScreenshotCallback_; }
private:
    std::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback_;
};
'''
            (root / 'opaque.hpp').write_text(probe_header)
            (root / 'consumer.cpp').write_text('''#include "opaque.hpp"
#ifdef Py_PYTHON_H
#error Python C API leaked through the renderer header
#endif
int main() { RenderContext context;return context.stockScreenshotCallback()?0:1; }
''')
            (root / 'owner.cpp').write_text('''#include "opaque.hpp"
namespace wot { class StockScreenshotCallback {}; }
RenderContext::RenderContext()
    : stockScreenshotCallback_(std::make_shared<wot::StockScreenshotCallback>()) {}
RenderContext::~RenderContext() = default;
''')
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                '-Wall', '-Wextra', '-Werror', str(root / 'consumer.cpp'),
                str(root / 'owner.cpp'), '-o', str(root / 'opaque-probe')], check=True)
            subprocess.run([str(root / 'opaque-probe')], check=True)

if __name__ == '__main__':
    unittest.main()
