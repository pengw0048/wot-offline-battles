"""Check the original preference-path consumer through the actual Python 2 C API.

A controlled DeviceApp filename owner replaces only the native OS path resolver
in this test. The adapter body and public Script::getData body are unmodified.
This does not establish Windows filesystem or engine startup acceptance.
"""
from pathlib import Path
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'bigworld/patch_stock_preferences_path.py'
BIGWORLD = Path(os.environ.get('WOT_BIGWORLD_ROOT',
    '/tmp/wot-bigworld-engine-14.4.1/programming/bigworld'))
CLIENT = Path(os.environ['WOT_0922_CLIENT']) if os.environ.get('WOT_0922_CLIENT') else None

HOST = r'''
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string>
namespace BW { using string=std::string; }
static BW::string currentPath;
static int resolves=0;
struct PathedFilename {
    BW::string resolveName() const {++resolves;return currentPath;}
};
class DeviceApp {
    PathedFilename preferencesFilename_;
public:
    static DeviceApp instance;
    BW::string preferencesFilePath() const;
};
DeviceApp DeviceApp::instance;
namespace Script {PyObject* getData(const BW::string& data);}
'''
REGISTER = r'''
static PyObject* setPath(PyObject*,PyObject* args) {
    const char* value;Py_ssize_t length;
    if(!PyArg_ParseTuple(args,"s#",&value,&length))return NULL;
    currentPath.assign(value,length);Py_RETURN_NONE;
}
static PyObject* reads(PyObject*,PyObject*) {return PyInt_FromLong(resolves);}
static PyMethodDef methods[]={
    {"wg_getPreferencesFilePath", [](PyObject*,PyObject* args)->PyObject* {
        return py_wg_getPreferencesFilePath(args);},METH_VARARGS,NULL},
    {"setPath",setPath,METH_VARARGS,NULL},{"reads",reads,METH_NOARGS,NULL},
    {NULL,NULL,0,NULL}};
PyMODINIT_FUNC initpreferences_probe() {Py_InitModule("preferences_probe",methods);}
'''
RUNNER = r'''
from __future__ import unicode_literals
import os,sys,types,zipfile,marshal,opcode
import preferences_probe as native
first = 'C:/Users/\u73a9\u5bb6/AppData/Roaming/Independent/preferences.xml'.encode('utf-8')
native.setPath(first)
assert type(native.wg_getPreferencesFilePath()) is str
assert native.wg_getPreferencesFilePath() == first
assert native.reads() == 2
second = 'D:/new base/\u504f\u597d.xml'.encode('utf-8')
native.setPath(second)
assert native.wg_getPreferencesFilePath() == second
for args in [(1,), ('path',), (None,), (1, 2)]:
    try: native.wg_getPreferencesFilePath(*args)
    except TypeError: pass
    else: raise AssertionError('extra arguments accepted')
assert native.reads() == 3
try: native.wg_getPreferencesFilePath(path='x')
except TypeError: pass
else: raise AssertionError('keyword argument accepted')
# Preserve the exact original module code, including its IS_BOT branch and
# unicode(...,'utf-8',errors='ignore') / dirname / join consumer sequence.
for name in ('AccountCommands','battle_results_shared','debug_utils'):
    sys.modules[name]=types.ModuleType(str(name))
sys.modules['debug_utils'].LOG_CURRENT_EXCEPTION=lambda:None
constants=types.ModuleType(b'constants');constants.IS_BOT=False;sys.modules['constants']=constants
sys.modules['BigWorld']=native
z=zipfile.ZipFile(sys.argv[1])
code=marshal.loads(z.read('scripts/client_common/shared_utils/account_helpers/BattleResultsCache.pyc')[8:])
expected=set((
    ('scripts/client/account_helpers/CustomFilesCache.pyc','__init__'),
    ('scripts/client/account_helpers/DossierCache.pyc','__init__'),
    ('scripts/client/account_helpers/persistent_caches.pyc','cacheFileName'),
    ('scripts/client/helpers/local_cache.pyc','makeFileLocalCachePath'),
    ('scripts/client_common/shared_utils/account_helpers/BattleResultsCache.pyc','<module>')))
seen=set()
def audit(c,member):
    if 'wg_getPreferencesFilePath' in c.co_names:
        seen.add((member,c.co_name))
        assert 'unicode' in c.co_names and 'utf-8' in c.co_consts and 'ignore' in c.co_consts
        instructions=[];offset=0
        while offset<len(c.co_code):
            op=ord(c.co_code[offset]);offset+=1;arg=None
            if op>=opcode.HAVE_ARGUMENT:
                arg=ord(c.co_code[offset]) | ord(c.co_code[offset+1])<<8;offset+=2
            instructions.append((op,arg))
        for i,(op,arg) in enumerate(instructions):
            if op==opcode.opmap['LOAD_ATTR'] and c.co_names[arg]=='wg_getPreferencesFilePath':
                assert instructions[i+1]==(opcode.opmap['CALL_FUNCTION'],0)
    for child in c.co_consts:
        if isinstance(child,types.CodeType):audit(child,member)
for member in z.namelist():
    if member.endswith('.pyc'):audit(marshal.loads(z.read(member)[8:]),member)
assert seen==expected

namespace={'__name__':'audit_BattleResultsCache'}
exec code in namespace
assert type(namespace['CACHE_DIR']) is unicode
assert namespace['CACHE_DIR'] == os.path.join(os.path.dirname(second.decode('utf-8')),'battle_results')
assert native.reads() == 4
constants.IS_BOT=True
namespace={'__name__':'audit_BattleResultsCache_bot'}
exec code in namespace
assert namespace['CACHE_DIR'] == 'battle_results'
assert native.reads() == 4
# An empty filename remains the owner's empty value; the adapter invents no
# working-directory or original-game fallback path.
native.setPath(b'')
assert native.wg_getPreferencesFilePath() == b''
print('Actual CPython 2 byte-str, dynamic owner, arity and stock CACHE_DIR contracts passed')
'''


class StockPreferencesPathTest(unittest.TestCase):
    def test_patch_is_narrow_and_idempotent(self):
        spec = importlib.util.spec_from_file_location('preferences_patch', PATCH)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        source = 'class DeviceApp {\npublic:\n\tbool savePreferences();\nprivate:\n\tPathedFilename preferencesFilename_;\n};\n'
        public = BIGWORLD / 'client/device_app.hpp'
        if public.is_file():
            source = public.read_text().replace('\n\tBW::string preferencesFilePath() const;', '')
        with tempfile.TemporaryDirectory(prefix='preferences-header-') as directory:
            root = Path(directory)
            (root / 'client').mkdir()
            header = root / 'client/device_app.hpp'
            header.write_text(source)
            patch.patch_engine(root)
            once = header.read_text()
            patch.patch_engine(root)
            self.assertEqual(header.read_text(), once)
            self.assertEqual(once.replace('\n\tBW::string preferencesFilePath() const;', ''), source)
            header.write_text('class DifferentOwner {};\n')
            with self.assertRaisesRegex(ValueError, 'owner anchor changed'):
                patch.patch_engine(root)
            self.assertEqual(header.read_text(), 'class DifferentOwner {};\n')

    def test_actual_capi_and_original_nonbot_consumer(self):
        python = os.environ.get('WOT_PY27') or shutil.which('python2.7')
        if not python:
            candidate = Path.home() / '.pyenv/versions/2.7.18/bin/python2.7'
            if candidate.is_file():
                python = str(candidate)
        script = BIGWORLD / 'lib/pyscript/script.cpp'
        package = CLIENT / 'res/packages/scripts.pkg' if CLIENT is not None else None
        if sys.platform != 'darwin' or not python or not script.is_file() or package is None or not package.is_file():
            self.skipTest('Requires the local CPython 2.7/public-source/exact-client audit fixtures')
        config = python + '-config'
        includes = []
        for flag in shlex.split(subprocess.check_output([config, '--includes'], text=True)):
            includes.extend(['-isystem', flag[2:]] if flag.startswith('-I') else [flag])
        source = (ROOT / 'bigworld/stock_preferences_path.cpp').read_text()
        body = source[source.index('BW::string DeviceApp::preferencesFilePath()'):source.index('PY_MODULE_FUNCTION(')]
        public = script.read_text()
        begin = public.index('PyObject * Script::getData( const BW::string & data )')
        end = public.index('\n}', begin) + 2
        with tempfile.TemporaryDirectory(prefix='preferences-capi-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(HOST + public[begin:end] + body + REGISTER)
            (path / 'check.py').write_text(RUNNER)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++14', '-Wall',
                            '-Wextra', '-Werror', '-Wno-missing-field-initializers',
                            '-dynamiclib', '-undefined', 'dynamic_lookup', *includes,
                            str(path / 'probe.cpp'), '-o', str(path / 'preferences_probe.so')], check=True)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            subprocess.run([python, str(path / 'check.py'), str(package)], env=env, check=True)


if __name__ == '__main__':
    unittest.main()
