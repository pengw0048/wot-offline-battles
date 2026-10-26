"""Exercise original preset resources, production callbacks and public registry hooks."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]


def load_patch():
    spec = importlib.util.spec_from_file_location('stock_presets_patch', ROOT/'bigworld/patch_stock_graphics_presets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end+1]


def fresh_engine(root):
    archive = Path(os.environ.get('WOT_PUBLIC_BIGWORLD_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
    with tarfile.open(archive) as original:
        prefix = original.getmembers()[0].name.split('/')[0] + '/programming/bigworld/'
        for name in ('lib/moo/graphics_settings.hpp', 'lib/moo/graphics_settings.cpp', 'client/bw_winmain.cpp'):
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.extractfile(prefix+name).read())


class GraphicsPresetsTests(unittest.TestCase):
    def test_patch_is_idempotent_transactional_and_keeps_public_registration(self):
        patch = load_patch()
        with tempfile.TemporaryDirectory(prefix='presets-public-patch-') as directory:
            root = Path(directory)
            fresh_engine(root)
            original = (root/'lib/moo/graphics_settings.cpp').read_text()
            patch.apply(root)
            source = (root/'lib/moo/graphics_settings.cpp').read_text()
            self.assertIn('else if (!stockDesired && setIt != latentSettings.end())',source)
            self.assertIn('if (existingSettings)',source)
            self.assertIn('master->addSlave(setting);',source)
            self.assertLess(source.index('publishStockGraphicsPreferences(tempSettings);'),
                            source.index('GraphicsSetting::commitSettings(tempSettings);'))
            # Ordinary supported-option, delayed and slave dispatch is the actual
            # public implementation, with no preset-specific replacement.
            for marker in ('bool GraphicsSetting::selectOptionPriv(', 'bool GraphicsSetting::selectOption('):
                self.assertEqual(function(source,marker),function(original,marker))
            before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            patch.apply(root)
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            (root/'client/bw_winmain.cpp').write_text('unsupported')
            before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError,'presets owner anchor changed'):
                patch.apply(root)
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_actual_python2_owner_with_original_catalogue_and_registry_callbacks(self):
        python = os.environ.get('WOT_PY27') or str(Path.home()/'.pyenv/versions/2.7.18/bin/python2.7')
        client = os.environ.get('WOT_0922_CLIENT')
        if sys.platform != 'darwin' or not Path(python).is_file() or not client:
            self.skipTest('Requires pinned CPython 2.7, exact client data and the public source archive')
        sys.path.insert(0,str(REPOSITORY))
        from tools.packed_xml import read_packed_xml, PackedElement, TYPE_COMPRESSED_STRING
        with zipfile.ZipFile(Path(client)/'res/packages/misc.pkg') as package:
            resource = read_packed_xml(package.read('system/data/graphics_settings_presets.xml'))

        def element(value):
            return value.value if isinstance(value.value,PackedElement) else value

        def scalar(value):
            if value.value_type == TYPE_COMPRESSED_STRING:
                # PackedSection::asString(TYPE_BLOB) returns Base64::encode.
                return base64.b64encode(value.value).decode('ascii')
            return value.value.decode('utf8') if isinstance(value.value,bytes) else value.value

        profiles = []
        fixture = ['auto root=std::make_shared<DataSection>();']
        for index,(name,value) in enumerate(resource.children):
            if name != b'graphicsPreferences':
                continue
            preset = element(value)
            label = scalar(preset.value)
            settings = []
            fixture.append('auto preset{0}=std::make_shared<DataSection>();preset{0}->text={1};'.format(index,json.dumps(label)))
            fixture.append('root->children.emplace_back("graphicsPreferences",preset{0});'.format(index))
            for entry_index,(entry_name,entry_value) in enumerate(preset.children):
                if entry_name != b'entry':
                    continue
                entry = element(entry_value)
                fields = {n.decode('ascii'):scalar(v) for n,v in entry.children}
                settings.append((fields['label'],fields['activeOption']))
                fixture.append('auto e{0}_{1}=std::make_shared<DataSection>();e{0}_{1}->label={2};e{0}_{1}->option={3};preset{0}->children.emplace_back("entry",e{0}_{1});'.format(index,entry_index,json.dumps(fields['label']),fields['activeOption']))
            profiles.append((label,settings))
        self.assertEqual([p[0] for p in profiles],['MAX','HIGH','MEDIUM','LOW','MIN'])
        self.assertTrue(all(len(p[1])==19 for p in profiles))
        fixture.append('return root;')
        patch = load_patch()
        with tempfile.TemporaryDirectory(prefix='actual-presets-python2-') as directory:
            root = Path(directory)
            fresh_engine(root)
            patch.apply(root)
            public = (root/'lib/moo/graphics_settings.cpp').read_text()
            actual_registry = '\n'.join(function(public,marker) for marker in (
                'bool GraphicsSetting::selectOption(', 'bool GraphicsSetting::selectOptionPriv(',
                'void GraphicsSetting::add(', 'GraphicsSetting::SettingsVector GraphicsSetting::stockCurrentSettings()',
                'void GraphicsSetting::captureStockPresetBaseline()',
                'void GraphicsSetting::applyStockPreset(', 'bool GraphicsSetting::needsRestart()'))
            production = '\n'.join(line for line in (ROOT/'bigworld/stock_graphics_presets.cpp').read_text().splitlines()
                                   if not line.startswith('#include'))
            interface = '\n'.join(line for line in (ROOT/'bigworld/stock_graphics_presets.hpp').read_text().splitlines()
                                  if not line.startswith(('#include','#pragma')))
            host = HOST.replace('@FIXTURE@','\n'.join(fixture)).replace('@INTERFACE@',interface).replace('@REGISTRY@',actual_registry)
            methods = re.findall(r'PY_MODULE_FUNCTION\((\w+),BigWorld\)',production)
            registration = '\n'.join('{"'+name+'",wrap_py_'+name+',METH_VARARGS,NULL},' for name in methods)
            (root/'probe.cpp').write_text(host+production+FOOTER.replace('@METHODS@',registration))
            (root/'check.py').write_text(RUNNER)
            includes = []
            for flag in shlex.split(subprocess.check_output([python+'-config','--includes'],text=True)):
                includes.extend(['-isystem',flag[2:]] if flag.startswith('-I') else [flag])
            compile_result = subprocess.run([os.environ.get('CXX','clang++'),'-std=c++14','-dynamiclib','-undefined','dynamic_lookup',
                *includes,'-I',str(ROOT/'include'),str(root/'probe.cpp'),'-o',str(root/'presets_probe.so')],capture_output=True,text=True)
            self.assertEqual(compile_result.returncode,0,compile_result.stderr)
            result = subprocess.run([python,str(root/'check.py'),str(root),json.dumps(profiles)],capture_output=True,text=True,
                                    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


HOST = r'''
#include <Python.h>
#include "wot/stock_graphics_presets.hpp"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>
#define BW_BEGIN_NAMESPACE
#define BW_END_NAMESPACE
#define BW_GUARD
#define MF_ASSERT(value) assert(value)
#define ERROR_MSG(...) ((void)0)
#define PY_MODULE_FUNCTION(name,module) static PyObject* wrap_py_##name(PyObject*,PyObject* args){return py_##name(args);}
using uint32=std::uint32_t;
namespace BW{using string=std::string;template<class T>using vector=std::vector<T>;}
template<class T>struct Ptr:std::shared_ptr<T>{
using std::shared_ptr<T>::shared_ptr;Ptr(T* value):std::shared_ptr<T>(value){}Ptr()=default;
T* getObject()const{return this->get();}bool exists()const{return bool(*this);}};
struct DataSection;
using DataSectionPtr=std::shared_ptr<DataSection>;
struct DataSection{
std::string text,label;int option=0;std::vector<std::pair<std::string,DataSectionPtr>> children;
void openSections(const char* n,std::vector<DataSectionPtr>& out){for(const auto& c:children)if(c.first==n)out.push_back(c.second);}
std::string asString()const{return text;}
std::string readString(const char* n,const char*)const{assert(std::string(n)=="label");return label;}
int readInt(const char* n,int)const{assert(std::string(n)=="activeOption");return option;}
};
DataSectionPtr fixture(){@FIXTURE@}
struct BWResource{static DataSectionPtr openSection(const char* name){assert(std::string(name)=="system/data/graphics_settings_presets.xml");return fixture();}};
struct PyObjectPtr{
enum ReferenceType{STEAL_REFERENCE};PyObject* value;
PyObjectPtr(PyObject* p,ReferenceType):value(p){}~PyObjectPtr(){Py_XDECREF(value);}
explicit operator bool()const{return value!=nullptr;}PyObject* get()const{return value;}};
namespace Script{int setData(PyObject* value,std::string& output){
char* data;Py_ssize_t size;if(PyString_AsStringAndSize(value,&data,&size)<0)return -1;output.assign(data,size);return 0;}}
namespace Moo{
struct GraphicsSetting{
using GraphicsSettingPtr=Ptr<GraphicsSetting>;using GraphicsSettingVector=std::vector<GraphicsSettingPtr>;
using SettingsVector=std::vector<std::pair<std::string,uint32>>;using StringIntMap=std::map<std::string,int>;
struct Option{std::string m_label,m_desc;bool m_supported,m_advanced;};
std::string label_,desc_;int activeOption_;bool delayed_,needsRestart_,advanced_;std::vector<Option> options_;GraphicsSettingVector slaves_;
GraphicsSetting(const char* label,const char* desc,int active,bool delay,bool restart,bool advanced=false):
label_(label),desc_(desc),activeOption_(active),delayed_(delay),needsRestart_(restart),advanced_(advanced){}
virtual ~GraphicsSetting(){}virtual void onOptionSelected(int)=0;
void addOption(const char* l,const char* d,bool s,bool a=false){options_.push_back({l,d,s,a});}
const std::string& label()const{return label_;}int activeOption()const{return activeOption_;}
bool requiresRestart()const{return needsRestart_;}
const std::vector<Option>& options()const{return options_;}
void addSlave(GraphicsSettingPtr p){slaves_.push_back(p);}
struct Data{GraphicsSettingVector s_settings;StringIntMap s_latentSettings;SettingsVector s_oldSettings;bool s_needsRestart=false,s_advanced=true;};
static Data& getSettingsData(){static Data d;return d;}
static const GraphicsSettingVector& settings(){return getSettingsData().s_settings;}
static GraphicsSettingPtr getFromLabel(const std::string& label){for(const auto& s:settings())if(s->label()==label)return s;return {};}
static bool isPending(GraphicsSettingPtr,int&){return false;}static bool addPending(GraphicsSetting*,int){return true;}
static void add(GraphicsSettingPtr);bool selectOption(int,bool=false);bool selectOptionPriv(int,bool=false);
static SettingsVector stockCurrentSettings();static void applyStockPreset(const SettingsVector&);
static void captureStockPresetBaseline();
static bool needsRestart();
};
}
@INTERFACE@
namespace Moo{@REGISTRY@}
'''

FOOTER = r'''
std::vector<std::pair<std::string,int>> calls;
struct ProbeSetting:Moo::GraphicsSetting{
ProbeSetting(const char* label,int initial,bool delayed):GraphicsSetting(label,label,initial,delayed,false){
for(int i=0;i!=5;++i)addOption("value","value",true);}
void onOptionSelected(int n){calls.emplace_back(label(),n);}
};
static PyObject* seed(PyObject*,PyObject* args){
PyObject* input;if(!PyArg_ParseTuple(args,"O",&input))return NULL;
Moo::GraphicsSetting::SettingsVector settings;PyObject *key,*value;Py_ssize_t pos=0;
while(PyDict_Next(input,&pos,&key,&value)){char* text=PyString_AsString(key);if(!text)return NULL;
long index=PyInt_AsLong(value);if(PyErr_Occurred())return NULL;
settings.emplace_back(text,uint32(index));Moo::GraphicsSetting::getSettingsData().s_latentSettings[text]=index;}
publishStockGraphicsPreferences(settings);Py_RETURN_NONE;}
static PyObject* add(PyObject*,PyObject* args){const char* label;int initial,delay;
if(!PyArg_ParseTuple(args,"sii",&label,&initial,&delay))return NULL;
Moo::GraphicsSetting::add(new ProbeSetting(label,initial,delay!=0));Py_RETURN_NONE;}
static PyObject* initialise(PyObject*,PyObject* args){if(!PyArg_ParseTuple(args,""))return NULL;
if(!initialiseStockGraphicsPresets())return NULL;Py_RETURN_NONE;}
static PyObject* selectOption(PyObject*,PyObject* args){const char* label;int option;
if(!PyArg_ParseTuple(args,"si",&label,&option))return NULL;
auto s=Moo::GraphicsSetting::getFromLabel(label);assert(s);s->selectOption(option);Py_RETURN_NONE;}
static PyObject* getCalls(PyObject*,PyObject*){PyObject* result=PyList_New(calls.size());for(std::size_t i=0;i<calls.size();++i)
PyList_SET_ITEM(result,i,Py_BuildValue("si",calls[i].first.c_str(),calls[i].second));calls.clear();return result;}
static PyObject* current(PyObject*,PyObject*){return dictionary(toStock(Moo::GraphicsSetting::stockCurrentSettings()));}
static PyObject* previous(PyObject*,PyObject*){return dictionary(toStock(Moo::GraphicsSetting::getSettingsData().s_oldSettings));}
static PyObject* desired(PyObject*,PyObject*){return dictionary(catalogue.desired());}
static PyObject* overload(PyObject*,PyObject* args){const char* name;if(!PyArg_ParseTuple(args,"s",&name))return NULL;
setStockGraphicsPresetOverload(name);Py_RETURN_NONE;}
static PyObject* restartFlag(PyObject*,PyObject* args){const char* label;int value;if(!PyArg_ParseTuple(args,"si",&label,&value))return NULL;
auto s=Moo::GraphicsSetting::getFromLabel(label);assert(s);s->needsRestart_=value!=0;Py_RETURN_NONE;}
static PyObject* needsRestart(PyObject*,PyObject*){return PyBool_FromLong(Moo::GraphicsSetting::needsRestart());}
PyMODINIT_FUNC initpresets_probe(){
static PyMethodDef methods[]={
@METHODS@
{"_seed",seed,METH_VARARGS,NULL},{"_register",add,METH_VARARGS,NULL},{"_initialise",initialise,METH_VARARGS,NULL},
{"_select",selectOption,METH_VARARGS,NULL},{"_calls",getCalls,METH_NOARGS,NULL},{"_current",current,METH_NOARGS,NULL},
{"_previous",previous,METH_NOARGS,NULL},{"_desired",desired,METH_NOARGS,NULL},{"_overload",overload,METH_VARARGS,NULL},
{"_restartFlag",restartFlag,METH_VARARGS,NULL},{"_needsRestart",needsRestart,METH_NOARGS,NULL},{NULL,NULL,0,NULL}};
Py_InitModule("presets_probe",methods);}
'''

RUNNER = r'''
import json,sys
sys.path.insert(0,sys.argv[1]);import presets_probe as p
profiles=json.loads(sys.argv[2]);by_name=dict((name,dict(values))for name,values in profiles)
assert p.getGraphicsPresets()==[] and p.isCustomGraphicPreset() is True
p._seed(by_name['MEDIUM'])
assert p.getGraphicsPresets()==[]
for label in ('VEHICLE_DUST_ENABLED','VEHICLE_TRACES_ENABLED','LIGHTING_QUALITY'):
 p._register(label,0,0)
p._calls();p._initialise();p._initialise();assert p._calls()==[] and p._previous()==p._current()
assert p.currentGraphicPresetKey()=='MEDIUM'
assert p.getGraphicsPresetsIndices()=={'CUSTOM':0,'HIGH':1,'LOW':2,'MAX':3,'MEDIUM':4,'MIN':5}
assert p.getGraphicsPreset(0)=={} and p.getGraphicsPreset(3)==by_name['MAX']
assert p.getGraphicsPreset(-1) is None and p.getGraphicsPreset(6) is None
assert p.getGraphicsPresets()==[{},by_name['HIGH'],by_name['LOW'],by_name['MAX'],by_name['MEDIUM'],by_name['MIN']]
assert p.getGraphicsPresetPropertyNames()==('LIGHTING_QUALITY','VEHICLE_DUST_ENABLED','VEHICLE_TRACES_ENABLED')
assert p.isCustomGraphicPreset() is False
before=p._current();p._select('presets',4)
assert p._calls()==[] and p._current()==before and p._desired()==by_name['MIN']
assert p.currentGraphicPresetKey()=='MIN'
p.setGraphicPresetByIndex(0);assert len(p._calls())==3
assert p._previous()==before
assert p._current()==dict((k,by_name['MAX'][k])for k in before)
assert p._desired()==by_name['MIN']
p.setGraphicPresetByName('LOW');assert len(p._calls())==3
low=dict((k,by_name['LOW'][k])for k in before);assert p._current()==low
assert p.applyGraphicPreset('MIN') is True and p._current()==low and p._calls()==[]
p.updateCurrentPresetIndex();assert p._calls()==[] and p.currentGraphicPresetKey()=='CUSTOM' and p._desired()=={}
assert p.applyGraphicPreset('MAX') is True and len(p._calls())==3
p.setGraphicPresetByName('MAX');assert len(p._calls())==3 # Force callbacks even at the same option.
p.setGraphicPresetByIndex(-1);p.setGraphicPresetByIndex(999);assert p._calls()==[]
p.setGraphicPresetByName('UNLISTED');assert p._calls()==[] and 'UNLISTED' in p.getGraphicsPresetsIndices()
assert p.canToDowngradePreset() is True
p.setGraphicPresetByName('MIN');p._calls();assert p.canToDowngradePreset() is True # Missing live properties do not match.
# Future constructors consume a published preset without invoking callbacks.
p._select('presets',0);p._calls();p._register('TEXTURE_QUALITY',4,1)
assert p._calls()==[] and p._current()['TEXTURE_QUALITY']==0
for label in sorted(by_name['MAX']):
 if label not in p._current():p._register(label,4,0)
assert p._calls()==[]
p.setGraphicPresetByName('MIN');assert ('TEXTURE_QUALITY',3) in p._calls()
assert p.canToDowngradePreset() is False
p._overload('LOW');before=p._current();p._seed(by_name['MAX'])
assert p._desired()==by_name['LOW'] and p._current()==before and p._calls()==[]
p._overload('none');p._seed(by_name['MAX']);assert p._desired()==by_name['LOW']
p._overload('');p._seed(by_name['MAX']);assert p._desired()==by_name['MAX']
p._restartFlag('LIGHTING_QUALITY',1)
p.setGraphicPresetByName('MAX');p._calls();assert p._needsRestart() is True
p.setGraphicPresetByName('MAX');p._calls();assert p._needsRestart() is False
p._select('LIGHTING_QUALITY',4);assert p._needsRestart() is True
p._select('LIGHTING_QUALITY',0);assert p._needsRestart() is False
# Non-string and wrong-arity inputs follow the actual Python 2 C API guards.
for name,args in [('applyGraphicPreset',(3,)),('getGraphicsPreset',('x',)),('getGraphicsPresets',(1,))]:
 try:getattr(p,name)(*args)
 except (TypeError,ValueError):pass
 else:raise AssertionError(name)
print('Actual preset owner, registry callbacks, original XML and Python 2 API passed')
'''

if __name__ == '__main__':
    unittest.main()
