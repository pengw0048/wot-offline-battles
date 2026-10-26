"""Exercise the real display adapter and patch ownership with guarded native fakes.

The host fixture proves Python schemas, mode-index identity and transition
ownership. It does not prove Windows display reset or rendered pixels.
"""
from pathlib import Path
import importlib.util
import itertools
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]

HOST = r'''
#include <Python.h>
#include <cassert>
#include <cstdint>
#include <cwchar>
#include <cstring>
#include <string>
#include <vector>
#define WOT_STOCK_DISPLAY_MODES_PORTABLE
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define PY_MODULE_FUNCTION(...)
#define PY_MODULE_ATTRIBUTE(...)
#define FAILED(x) ((x)<0)
using HWND=void*;using HMONITOR=void*;using LONG=int;using DWORD=unsigned;using UINT=unsigned;
const int FALSE=0,GWL_STYLE=0,GWL_EXSTYLE=1,WS_CAPTION=1,WS_OVERLAPPEDWINDOW=3,WS_POPUP=4;
const int WS_EX_WINDOWEDGE=8,WS_EX_CLIENTEDGE=16,SWP_NOZORDER=1,SWP_NOACTIVATE=2,SWP_FRAMECHANGED=4;
const int MONITOR_DEFAULTTONEAREST=2,MONITORINFOF_PRIMARY=1,CP_UTF8=65001;
const int DISPLAY_DEVICE_ACTIVE=1,DISPLAY_DEVICE_MIRRORING_DRIVER=8;
struct RECT { LONG left,top,right,bottom; };
struct MONITORINFOEXW { DWORD cbSize=0;RECT rcMonitor={},rcWork={};DWORD dwFlags=0;wchar_t szDevice[32]={}; };
struct DISPLAY_DEVICEW { DWORD cb=0,StateFlags=0;wchar_t DeviceString[128]={}; };
RECT outer={100,100,916,739};LONG style=WS_OVERLAPPEDWINDOW,extended=0;
bool positionOK=true,resetOK=true,changeOK=true,deviceReady=true;
int resets=0,changes=0,releases=0;unsigned activeDevice=1,activeMode=0;bool windowed=true;
int monitorIndex(HMONITOR m){return int(reinterpret_cast<uintptr_t>(m))-1;}
HMONITOR monitorHandle(int index){return reinterpret_cast<void*>(uintptr_t(index+1));}
bool GetMonitorInfoW(HMONITOR m,MONITORINFOEXW* out){
    int i=monitorIndex(m);if(i<0||i>1)return false;
    out->rcMonitor=i?RECT{0,0,2560,1440}:RECT{-1920,0,0,1080};
    out->rcWork=i?RECT{0,0,2560,1400}:RECT{-1920,0,0,1040};
    out->dwFlags=i?MONITORINFOF_PRIMARY:0;wcscpy(out->szDevice,i?L"DISPLAY2":L"DISPLAY1");return true;
}
HMONITOR MonitorFromWindow(HWND,int){return monitorHandle((outer.left+outer.right)/2<0?0:1);}
LONG GetWindowLong(HWND,int field){return field==GWL_STYLE?style:extended;}
LONG SetWindowLong(HWND,int field,LONG value){LONG old=GetWindowLong(nullptr,field);(field==GWL_STYLE?style:extended)=value;return old;}
bool GetWindowRect(HWND,RECT* out){*out=outer;return true;}
bool GetClientRect(HWND,RECT* out){
    bool frame=(style&WS_CAPTION)!=0;*out={0,0,outer.right-outer.left-(frame?16:0),outer.bottom-outer.top-(frame?39:0)};return true;
}
bool AdjustWindowRectEx(RECT* out,LONG value,int,LONG){
    if(value&WS_CAPTION){out->left-=8;out->right+=8;out->top-=31;out->bottom+=8;}return true;
}
bool SetWindowPos(HWND,HWND,int x,int y,int w,int h,UINT){
    if(!positionOK)return false;outer={x,y,x+w,y+h};return true;
}
bool OffsetRect(RECT* out,int x,int y){out->left+=x;out->right+=x;out->top+=y;out->bottom+=y;return true;}
int WideCharToMultiByte(int,int,const wchar_t* text,int,char* output,int,void*,void*){
    int n=int(wcslen(text))+1;if(output)for(int i=0;i<n;++i)output[i]=char(text[i]);return n;
}
bool EnumDisplayDevicesW(const wchar_t* device,DWORD index,DISPLAY_DEVICEW* output,int){
    if(index)return false;output->StateFlags=DISPLAY_DEVICE_ACTIVE;
    wcscpy(output->DeviceString,wcscmp(device,L"DISPLAY1")==0?L"Left panel":L"Main panel");return true;
}
enum D3DFORMAT { D3DFMT_A2R10G10B10,D3DFMT_A2B10G10R10,D3DFMT_A8R8G8B8,D3DFMT_X8R8G8B8,
 D3DFMT_A8B8G8R8,D3DFMT_X8B8G8R8,D3DFMT_A1R5G5B5,D3DFMT_X1R5G5B5,D3DFMT_R5G6B5 };
struct D3DDISPLAYMODE { unsigned Width,Height,RefreshRate;D3DFORMAT Format; };
struct IDirect3D9 { HMONITOR GetAdapterMonitor(unsigned i){return monitorHandle(i);}void Release(){++releases;} };
struct Device { int GetDirect3D(IDirect3D9** out){static IDirect3D9 api;*out=&api;return 0;} };
namespace Moo {
struct DeviceInfo { unsigned adapterID_;D3DDISPLAYMODE windowedDisplayMode_;std::vector<D3DDISPLAYMODE> displayModes_; };
struct RenderContext {
    Device gpu;std::vector<DeviceInfo> adapters={
        {0,{1920,1080,60,D3DFMT_X8R8G8B8},{{640,480,60,D3DFMT_R5G6B5},{1920,1080,60,D3DFMT_X8R8G8B8},{1920,1080,75,D3DFMT_X8R8G8B8}}},
        {1,{2560,1440,144,D3DFMT_X8R8G8B8},{{2560,1440,144,D3DFMT_X8R8G8B8}}}};
    Device* device(){return deviceReady?&gpu:nullptr;}HWND windowHandle(){return reinterpret_cast<void*>(7);}
    unsigned nDevices(){return unsigned(adapters.size());}const DeviceInfo& deviceInfo(unsigned i){return adapters.at(i);}
    unsigned deviceIndex(){return activeDevice;}unsigned modeIndex(){return activeMode;}bool windowed(){return ::windowed;}
    bool changeMode(unsigned index,bool target){++changes;if(!changeOK)return false;activeMode=index;::windowed=target;return true;}
    bool resetDevice(){++resets;return resetOK;}
};
RenderContext& rc(){static RenderContext value;return value;}
}
#include "@PRODUCTION@"
PyObject* modeIndex(PyObject*,PyObject*){return PyInt_FromLong(activeMode);}
#define METHOD(name) {#name,[](PyObject*,PyObject* args)->PyObject*{return py_##name(args);},METH_VARARGS,NULL}
PyMethodDef methods[]={METHOD(listVideoModesAllMonitors),METHOD(listBorderlessResolutionsAllMonitors),
METHOD(wg_getMaxWindowedResolution),METHOD(wg_getMaxBorderlessResolution),METHOD(getWindowMode),
METHOD(getActiveMonitorIndex),METHOD(getExclusiveFullscreenMonitorIndex),METHOD(wg_getCurrentResolution),
METHOD(wg_getMonitorNames),METHOD(getBorderlessParameters),METHOD(setBorderlessFixedSize),METHOD(setActiveMonitorIndex),
{"videoModeIndex",modeIndex,METH_VARARGS,NULL},{NULL,NULL,0,NULL}};
void run(const char* code){if(PyRun_SimpleString(code)){PyErr_Print();fputs(code,stderr);assert(false);}}
int main(){
    deviceReady=false;stockRememberDisplayWindow();deviceReady=true;
    Py_Initialize();auto module=Py_InitModule("BigWorld",methods);assert(module);
    PyModule_AddIntConstant(module,"WindowModeWindowed",WINDOWED);
    PyModule_AddIntConstant(module,"WindowModeExclusiveFullscreen",EXCLUSIVE);
    PyModule_AddIntConstant(module,"WindowModeBorderless",BORDERLESS);
    windowed=false;style=WS_POPUP;outer={0,0,2560,1440};
    run("import BigWorld as B\nassert B.getWindowMode()==1\n"
        "assert B.wg_getCurrentResolution(0)==(800.,600.,144.)\n"
        "assert B.wg_getMaxWindowedResolution(0)==(1904,1001,144)\n");
    windowed=true;style=WS_OVERLAPPEDWINDOW;outer={100,100,916,739};
    run("import BigWorld as B\n"
        "m=B.listVideoModesAllMonitors()\n"
        "assert len(m)==2 and len(m[0])==3 and m[0][2]==(2,1920,1080,32,'1920x1080x32@75Hz',75)\n"
        "assert m[0][0][3]==16\n"
        "assert B.wg_getMonitorNames()==['1 - Left panel','2 - Main panel (primary)']\n"
        "assert B.getActiveMonitorIndex(0)==1 and B.getExclusiveFullscreenMonitorIndex()==1\n"
        "assert B.wg_getMaxWindowedResolution(0)==(1904,1001,144)\n"
        "assert B.wg_getMaxBorderlessResolution(0)==(1920,1080,144)\n"
        "assert B.wg_getMaxWindowedResolution(99)==(1024,768,144)\n"
        "assert B.wg_getCurrentResolution(0)==(800.,600.,144.), B.wg_getCurrentResolution(0)\n"
        "assert len(B.listBorderlessResolutionsAllMonitors()[0])==1\n"
        "B.setBorderlessFixedSize(1200,800)\n"
        "assert B.getBorderlessParameters()==(1,0,0,1200,800,1)\n"
        "assert B.listBorderlessResolutionsAllMonitors()[0][0][1:3]==(1200,800)\n"
        "for f,args in ((B.getActiveMonitorIndex,(9,)),(B.setActiveMonitorIndex,(99,0)),(B.setBorderlessFixedSize,(0,1)),(B.wg_getCurrentResolution,(3,))):\n"
        " try: f(*args);assert False\n"
        " except ValueError: pass\n");
    // -1 must still apply the requested enum; it is not the public bool API.
    assert(stockChangeVideoMode(-1,BORDERLESS));assert(windowMode()==BORDERLESS);assert(resets==1);
    assert(outer.right-outer.left==1200&&outer.bottom-outer.top==800);
    changeOK=false;assert(!stockChangeVideoMode(-1,WINDOWED));assert(windowMode()==BORDERLESS);
    assert(outer.right-outer.left==1200);changeOK=true;
    assert(stockChangeVideoMode(-1,WINDOWED));assert(windowMode()==WINDOWED);
    assert(outer.right-outer.left==816&&outer.bottom-outer.top==639);
    run("B.setActiveMonitorIndex(0,0)\nassert B.getActiveMonitorIndex(0)==0\n");
    stockResizeWindow(900,700);assert(outer.left<0);assert(outer.right-outer.left==916);
    run("B.setActiveMonitorIndex(0,1)\n");
    int before=changes;assert(!stockChangeVideoMode(0,EXCLUSIVE));assert(changes==before);
    assert(!stockChangeVideoMode(9,WINDOWED));assert(!stockChangeVideoMode(0,3));
    resetOK=false;assert(!stockChangeVideoMode(-1,BORDERLESS));assert(windowMode()==WINDOWED);resetOK=true;
    deviceReady=false;PyObject* args=PyTuple_New(0);assert(!py_listVideoModesAllMonitors(args));
    assert(PyErr_ExceptionMatches(PyExc_RuntimeError));PyErr_Clear();Py_DECREF(args);deviceReady=true;
    // Optional exact-bytecode integration uses the real production wrappers.
    run("import os\n"
        "if os.environ.get('DISPLAY_GRAPHICS_PYC'):\n"
        " import marshal,sys,types\n"
        " debug=types.ModuleType('debug_utils');debug.LOG_WARNING=lambda *a:None;sys.modules['debug_utils']=debug\n"
        " shared=types.ModuleType('shared_utils');shared.CONST_CONTAINER=object;shared.findFirst=lambda predicate,items:next(iter(items),None);sys.modules['shared_utils']=shared\n"
        " sys.modules['GUI']=types.ModuleType('GUI')\n"
        " graphics=types.ModuleType('display_graphics_contract')\n"
        " eval(marshal.loads(open(os.environ['DISPLAY_GRAPHICS_PYC'],'rb').read()[8:]),graphics.__dict__)\n"
        " assert graphics.g_monitorSettings.activeMonitor==0\n"
        " assert graphics.g_monitorSettings.videoModes[-1].refreshRate==75\n");
    assert(releases>0);Py_Finalize();
}
'''


class DisplayModesTest(unittest.TestCase):
    def test_real_adapter_schemas_transitions_and_exact_consumer(self):
        python = Path(os.environ.get('WOT_PYTHON27', '/Users/peng/.pyenv/versions/2.7.18/bin/python2.7'))
        config = python.with_name('python2.7-config')
        if not config.exists():
            self.skipTest('Pinned CPython 2.7 development files unavailable')
        with tempfile.TemporaryDirectory(prefix='display-modes-') as directory:
            folder = Path(directory)
            source = folder / 'host.cpp'
            source.write_text(HOST.replace('@PRODUCTION@', str(ROOT / 'bigworld/stock_display_modes.cpp')))
            flags = [item for item in shlex.split(subprocess.check_output([str(config), '--cflags'], text=True))
                     if item != '-DNDEBUG']
            libs = shlex.split(subprocess.check_output([str(config), '--ldflags'], text=True))
            binary = folder / 'host'
            subprocess.run(['c++', '-std=c++14', '-Wno-deprecated-register', '-UNDEBUG', str(source), *flags, *libs, '-o', str(binary)], check=True)
            environment = dict(os.environ)
            client = environment.get('WOT_0922_CLIENT')
            if client:
                with zipfile.ZipFile(Path(client) / 'res/packages/scripts.pkg') as archive:
                    pyc = folder / 'graphics.pyc'
                    pyc.write_bytes(archive.read('scripts/client/gui/shared/utils/graphics.pyc'))
                environment['DISPLAY_GRAPHICS_PYC'] = str(pyc)
            subprocess.run([str(binary)], env=environment, check=True)

    def test_pristine_patch_idempotence_and_atomic_anchor_failure(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Unmodified public-source archive unavailable')
        spec = importlib.util.spec_from_file_location('display_patch', ROOT / 'bigworld/patch_stock_display_modes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = ('client/script_bigworld.cpp', 'lib/moo/render_context.cpp')
        with tarfile.open(archive) as original:
            prefix = next(name[:-len(names[0])] for name in original.getnames() if name.endswith('/' + names[0]))
            contents = {name: original.extractfile(prefix + name).read() for name in names}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in contents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            module.patch(root)
            first = {name: (root / name).read_bytes() for name in names}
            module.patch(root)
            self.assertEqual(first, {name: (root / name).read_bytes() for name in names})
            for name, data in contents.items():
                (root / name).write_bytes(data)
            render = root / names[1]
            render.write_text(render.read_text().replace('mode.RefreshRate = 0;', 'mode.RefreshRate = 1;'))
            before = {name: (root / name).read_bytes() for name in names}
            with self.assertRaises(RuntimeError):
                module.patch(root)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in names})

    def test_patch_composes_with_display_and_script_owners(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Unmodified public-source archive unavailable')
        patches, names = [], set()
        for name in ('display_modes', 'dynamic_resolution', 'script_paths', 'xmpp'):
            path = ROOT / ('bigworld/patch_stock_' + name + '.py')
            names.update(re.findall(r'''['"]((?:client|lib/moo|lib/romp)/[^'"\s]+\.(?:cpp|hpp|ipp))['"]''', path.read_text()))
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            patches.append(module.patch)
        with tarfile.open(archive) as source:
            prefix = next(n[:-len('client/script_bigworld.cpp')] for n in source.getnames()
                          if n.endswith('/client/script_bigworld.cpp'))
            original = {name: source.extractfile(prefix + name).read() for name in names}
        for order in itertools.permutations(patches):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name, contents in original.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(contents)
                for patch in order:
                    patch(root)
                before = {name: (root / name).read_bytes() for name in names}
                for patch in reversed(order):
                    patch(root)
                self.assertEqual(before, {name: (root / name).read_bytes() for name in names})
                script = (root / 'client/script_bigworld.cpp').read_text()
                self.assertEqual(script.count('#include "stock_display_modes.hpp"'), 1)
                self.assertEqual(script.count('stockChangeVideoMode( modeIndex, windowMode )'), 1)


if __name__ == '__main__':
    unittest.main()
