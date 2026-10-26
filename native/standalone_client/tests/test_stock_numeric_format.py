"""Compile the production NLS owner and (locally) its real CPython 2 boundary.

NLS receipts below are controlled OS responses, not Windows locale acceptance.
The tests verify the exact format structures, call order and failure paths.
"""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'bigworld/stock_numeric_format.cpp'
HEADER = ROOT / 'bigworld/stock_numeric_format.hpp'

NLS_HOST = r'''
#include <cassert>
#include <cstdarg>
#include <cwchar>
#include <vector>
#include <utility>
using UINT=unsigned;using LCTYPE=unsigned;using HRESULT=int;
struct NUMBERFMTW {UINT NumDigits,LeadingZero,Grouping;wchar_t *lpDecimalSep,*lpThousandSep;UINT NegativeOrder;};
struct CURRENCYFMTW {UINT NumDigits,LeadingZero,Grouping;wchar_t *lpDecimalSep,*lpThousandSep;UINT NegativeOrder,PositiveOrder;wchar_t *lpCurrencySymbol;};
#define LOCALE_USER_DEFAULT 0x400
#define LOCALE_RETURN_NUMBER 0x20000000
#define LOCALE_ILZERO 0x12
#define LOCALE_IDIGITS 0x11
#define LOCALE_INEGNUMBER 0x1010
#define LOCALE_SMONDECIMALSEP 0x16
#define LOCALE_SMONTHOUSANDSEP 0x17
#define LOCALE_SMONGROUPING 0x18
#define LOCALE_SDECIMAL 0xe
#define LOCALE_STHOUSAND 0xf
#define LOCALE_SGROUPING 0x10
#define SUCCEEDED(x) ((x)>=0)
static UINT failKey=0,decimalDigits=2;
static bool formatFailure=false,lastNull=false;
static unsigned numberCalls=0,currencyCalls=0;
static std::wstring lastInput,output=L"1,234.50",numberGroups=L"3;0",moneyGroups=L"3;2;0";
static NUMBERFMTW lastNumber={};static CURRENCYFMTW lastCurrency={};
static std::vector<UINT> localeQueries;
static int GetLocaleInfoW(UINT locale,LCTYPE key,wchar_t* out,int size) {
    assert(locale==0x400);localeQueries.push_back(key);
    if(key==failKey)return 0;
    if(key&LOCALE_RETURN_NUMBER) {
        assert(size==4);
        *reinterpret_cast<UINT*>(out)=key==(LOCALE_IDIGITS|LOCALE_RETURN_NUMBER)?decimalDigits:1;
        return 2;
    }
    const std::wstring text=key==LOCALE_SGROUPING?numberGroups:
        key==LOCALE_SMONGROUPING?moneyGroups:
        key==LOCALE_SDECIMAL?L",":key==LOCALE_STHOUSAND?L"\u202f":
        key==LOCALE_SMONDECIMALSEP?L".":L"'";
    assert(static_cast<int>(text.size()+1)<=size);std::wcscpy(out,text.c_str());
    return static_cast<int>(text.size()+1);
}
static int GetNumberFormatW(UINT locale,UINT flags,const wchar_t* input,
        const NUMBERFMTW* fmt,wchar_t* out,int size) {
    assert(locale==0x400 && flags==0 && size==512);++numberCalls;
    lastInput=input;lastNull=!fmt;if(fmt)lastNumber=*fmt;
    if(formatFailure)return 0;
    std::wcscpy(out,output.c_str());return static_cast<int>(output.size()+1);
}
static int GetCurrencyFormatW(UINT locale,UINT flags,const wchar_t* input,
        const CURRENCYFMTW* fmt,wchar_t* out,int size) {
    assert(locale==0x400 && flags==0 && size==512);++currencyCalls;
    lastInput=input;lastNull=!fmt;if(fmt)lastCurrency=*fmt;
    if(formatFailure)return 0;
    std::wcscpy(out,output.c_str());return static_cast<int>(output.size()+1);
}
template<class T> static HRESULT StringCchPrintfW(wchar_t* out,unsigned size,
        const wchar_t* fmt,T value) {
    assert(std::wstring(fmt)==L"%I64d" || std::wstring(fmt)==L"%lf");
    // This fixture supplies only the CRT call; production uses StringCchPrintfW.
    int n=std::wstring(fmt)==L"%I64d"?
        std::swprintf(out,size,L"%lld",static_cast<long long>(value)):
        std::swprintf(out,size,L"%lf",static_cast<double>(value));
    return n<0 || static_cast<unsigned>(n)>=size?-1:0;
}
'''

NLS_CASES = r'''
int main() {
    using namespace wot::stock_numeric_format;
    assert(grouping(L"3;0")==3 && grouping(L"3;2;0")==32);
    assert(grouping(L"3")==30 && grouping(L"3;2")==320);
    assert(grouping(L"0")==0 && grouping(L"3;0;")==30 && grouping(L"abc")==0);
    assert(grouping(L"9;8;7;0")==987 && grouping(L"1;9")==190);
    assert(signedInteger(1.9)==1 && signedInteger(-1.9)==-1);
    const auto low=(std::numeric_limits<std::int64_t>::min)();
    assert(signedInteger(9223372036854775808.0)==low);
    assert(signedInteger(-9223372036854775808.0)==low);
    assert(signedInteger(std::numeric_limits<double>::infinity())==low);
    assert(signedInteger(std::numeric_limits<double>::quiet_NaN())==low);
    assert(niceDigits(.1,4)==1 && niceDigits(.125,4)==3 && niceDigits(.12345,2)==2);
    assert(niceDigits(.125,0)==0 && niceDigits(.1,1)==1);
    for(auto pair: {std::make_pair(L"1.00",L"1"),{L"1,20",L"1,2"},
            {L"0.00",L"0"},{L"1\u066b00",L"1\u066b"},{L"1.00-",L"1.00-"},
            {L"(1.00)",L"(1.00)"},{L"0",L"0"}}) {
        std::wstring value=pair.first;trimDecimal(value);assert(value==pair.second);
    }
    StockNumericFormats formats;
    assert((localeQueries==std::vector<UINT>{
        LOCALE_RETURN_NUMBER|LOCALE_ILZERO,LOCALE_SMONDECIMALSEP,
        LOCALE_SMONTHOUSANDSEP,LOCALE_SMONGROUPING,
        LOCALE_RETURN_NUMBER|LOCALE_IDIGITS,LOCALE_SDECIMAL,
        LOCALE_RETURN_NUMBER|LOCALE_ILZERO,LOCALE_RETURN_NUMBER|LOCALE_INEGNUMBER,
        LOCALE_STHOUSAND,LOCALE_SGROUPING}));
    assert(formats.integral(-1234567)==output);
    assert(lastInput==L"-1234567" && !lastNull);
    assert(lastNumber.NumDigits==0 && lastNumber.LeadingZero==1 && lastNumber.Grouping==3);
    assert(std::wstring(lastNumber.lpDecimalSep).empty());
    assert(std::wstring(lastNumber.lpThousandSep)==L"\u202f" && lastNumber.NegativeOrder==1);
    assert(formats.gold(-1234)==output && lastInput==L"-1234");
    assert(lastCurrency.NumDigits==0 && lastCurrency.LeadingZero==1 && lastCurrency.Grouping==32);
    assert(lastCurrency.NegativeOrder==5 && lastCurrency.PositiveOrder==1);
    assert(std::wstring(lastCurrency.lpCurrencySymbol).empty());
    assert(std::wstring(lastCurrency.lpDecimalSep)==L".");
    assert(std::wstring(lastCurrency.lpThousandSep)==L"'");
    std::wstring result;
    output=L"12,30";assert(formats.nice(12.3,result) && result==L"12,3");
    assert(lastInput==L"12.300000" && lastNumber.NumDigits==1);
    output=L"12,35";assert(formats.nice(12.3456789,result) && result==L"12,35");
    assert(lastInput==L"12.345679" && lastNumber.NumDigits==2);
    assert(formats.nice(-0.0,result) && lastInput==L"0" && lastNumber.NumDigits==0);
    assert(formats.nice(std::numeric_limits<double>::infinity(),result));
    assert(lastInput==L"-9223372036854775808");
    formatFailure=true;
    assert(formats.integral(1).empty() && formats.gold(1).empty());
    assert(formats.nice(.3,result) && result.empty());formatFailure=false;
    // Currency and number initialization are independent and short-circuit
    // their failed query chains. OS defaults are passed through as NULL.
    localeQueries.clear();failKey=LOCALE_SMONDECIMALSEP;
    StockNumericFormats badCurrency;
    assert(localeQueries.size()==8);
    badCurrency.gold(1);assert(lastNull);
    badCurrency.integral(1);assert(!lastNull);
    failKey=LOCALE_SDECIMAL;StockNumericFormats badNumber;
    badNumber.integral(1);assert(lastNull);
    assert(!badNumber.nice(.1,result) && errors==1);
    assert(badNumber.nice(1.0,result) && lastNull);
    failKey=0;decimalDigits=0;StockNumericFormats noDecimals;
    output=L"1";assert(noDecimals.nice(1.5,result));
    assert(lastNumber.NumDigits==0 && std::wstring(lastNumber.lpDecimalSep).empty());
    decimalDigits=4;numberGroups=L"3;2";StockNumericFormats fourDecimals;
    output=L"0,1250";assert(fourDecimals.nice(.125,result) && result==L"0,125");
    assert(lastNumber.NumDigits==3 && lastNumber.Grouping==320);
}
'''

CAPI_REGISTER = r'''
static PyObject* setOutput(PyObject*,PyObject* args) {
    PyObject* value;if(!PyArg_ParseTuple(args,"U",&value))return NULL;
    wchar_t text[512];Py_ssize_t n=PyUnicode_AsWideChar((PyUnicodeObject*)value,text,511);
    if(n<0)return NULL;output.assign(text,n);Py_RETURN_NONE;
}
static PyObject* inputReceipt(PyObject*,PyObject*) {
    return PyUnicode_FromWideChar(lastInput.data(),lastInput.size());
}
static PyMethodDef methods[]={
    {"wg_getIntegralFormat",[](PyObject*,PyObject* a)->PyObject*{return py_wg_getIntegralFormat(a);},METH_VARARGS,NULL},
    {"wg_getGoldFormat",[](PyObject*,PyObject* a)->PyObject*{return py_wg_getGoldFormat(a);},METH_VARARGS,NULL},
    {"wg_getNiceNumberFormat",[](PyObject*,PyObject* a)->PyObject*{return py_wg_getNiceNumberFormat(a);},METH_VARARGS,NULL},
    {"setOutput",setOutput,METH_VARARGS,NULL},{"inputReceipt",inputReceipt,METH_NOARGS,NULL},
    {NULL,NULL,0,NULL}};
PyMODINIT_FUNC initnumeric_probe() {Py_InitModule("numeric_probe",methods);}
'''
CAPI_CHECK = r'''
import numeric_probe as n
class Integer(int):pass
class Long(long):pass
class Float(float):pass
class Convertible(object):
    def __float__(self):return 1.5
    def __int__(self):return 1
n.setOutput(u'\u8d1f1\u202f234,50')
for f in (n.wg_getIntegralFormat,n.wg_getGoldFormat,n.wg_getNiceNumberFormat):
    value=f(1234)
    assert type(value) is str and value==u'\u8d1f1\u202f234,50'.encode('utf-8')
    for args in [(),(1,2),(None,),('1',),(u'1',),(Convertible(),)]:
        try:f(*args)
        except TypeError:pass
        else:raise AssertionError('invalid argument accepted')
    try:f(value=1)
    except TypeError:pass
    else:raise AssertionError('keyword accepted')
for f in (n.wg_getIntegralFormat,n.wg_getGoldFormat):
    for value in [True,False,Integer(-4),Long(-4),Float(-1.9),2**63-1,-2**63]:
        f(value);assert n.inputReceipt()==unicode(long(value))
    for value in [2**63,-2**63-1]:
        try:f(value)
        except TypeError:pass
        else:raise AssertionError('overflow accepted')
    for value in [float('nan'),float('inf'),-float('inf')]:
        f(value);assert n.inputReceipt()==u'-9223372036854775808'
# The real Win32 converter rejects a negative long and long > ULONG_MAX.
# The host Python API uses 64-bit unsigned long, so test its shared valid
# domain here; the full Win32 boundaries are verified by the x86 audit.
for value in [Integer(-4),Long(4),Long(2**32-1),Float(1.125),True]:
    n.wg_getNiceNumberFormat(value)
for value in [Long(-1),Long(2**64)]:
    try:n.wg_getNiceNumberFormat(value)
    except TypeError:pass
    else:raise AssertionError('invalid unsigned long accepted')
n.wg_getNiceNumberFormat(12.3456789)
assert n.inputReceipt()==u'12.345679'
# Run the original direct consumers when the reviewed local package is supplied.
import sys
if len(sys.argv)>1:
    import zipfile,marshal,types
    sys.modules['BigWorld']=n
    z=zipfile.ZipFile(sys.argv[1])
    numbers={'__name__':'audit_numbers'}
    exec marshal.loads(z.read('scripts/client/gui/shared/formatters/numbers.pyc')[8:]) in numbers
    assert numbers['formatInt'](0,'-')=='-'
    assert numbers['formatInt'](-1,'-')=='-'
    assert type(numbers['formatInt'](1)) is str
    assert numbers['makeStringWithThousandSymbol'](10000).endswith('K')
    assert n.inputReceipt()==u'10'
    for name in ('gui','gui.shared','gui.shared.formatters','gui.shared.money','debug_utils'):
        sys.modules[name]=types.ModuleType(name)
    styles=types.ModuleType('text_styles')
    styles.credits=styles.gold=styles.crystal=lambda x:x
    sys.modules['gui.shared.formatters'].text_styles=styles
    class Currency(object):
        CREDITS='credits';GOLD='gold';CRYSTAL='crystal'
    sys.modules['gui.shared.money'].Currency=Currency
    sys.modules['debug_utils'].LOG_WARNING=lambda *a:None
    currency={'__name__':'audit_currency'}
    exec marshal.loads(z.read('scripts/client/gui/shared/formatters/currency.pyc')[8:]) in currency
    assert currency['getBWFormatter']('credits') is n.wg_getIntegralFormat
    assert currency['getBWFormatter']('gold') is n.wg_getGoldFormat
    assert currency['getBWFormatter']('crystal') is n.wg_getIntegralFormat
    assert currency['getBWFormatter']('unknown') is n.wg_getIntegralFormat
print('Actual CPython 2 UTF-8 str, subtype, arity and converter contracts passed')
'''


def body():
    source = SOURCE.read_text()
    return '\n'.join(line for line in source.splitlines()
                     if not line.startswith('#include') and
                     not line.startswith('BW_') and
                     not line.startswith('PY_MODULE_FUNCTION('))


class StockNumericFormatTest(unittest.TestCase):
    def test_nls_owner_and_pure_boundaries(self):
        source = body()
        source = source[:source.index('\nStockNumericFormats &numericFormats()')] + '\n}\n'
        program = ('#include "' + str(HEADER) + '"\n' + NLS_HOST +
                   'static unsigned errors=0;static void* PyExc_RuntimeError=nullptr;\n'
                   'static void PyErr_SetString(void*,const char*) {++errors;}\n' +
                   source + NLS_CASES)
        with tempfile.TemporaryDirectory(prefix='stock-numeric-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                            str(path / 'probe.cpp'), '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)

    def test_actual_python2_capi(self):
        python = os.environ.get('WOT_PY27') or shutil.which('python2.7')
        if not python:
            candidate = Path.home() / '.pyenv/versions/2.7.18/bin/python2.7'
            if candidate.is_file():
                python = str(candidate)
        if sys.platform != 'darwin' or not python:
            self.skipTest('Requires the local CPython 2.7 audit interpreter')
        includes = []
        for flag in shlex.split(subprocess.check_output([python + '-config', '--includes'], text=True)):
            includes.extend(['-isystem', flag[2:]] if flag.startswith('-I') else [flag])
        program = ('#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n#include "' +
                   str(HEADER) + '"\n' + NLS_HOST + r'''
namespace Script {
PyObject* argCountError(const char* name,int first,int last,const char* type) {
    assert(first==1 && last==1);
    PyErr_Format(PyExc_TypeError,"%s() expects 1 argument of type %s",name,type);
    return NULL;
}
}
''' + body() + CAPI_REGISTER)
        with tempfile.TemporaryDirectory(prefix='stock-numeric-capi-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            (path / 'check.py').write_text(CAPI_CHECK)
            subprocess.run([os.environ.get('CXX', 'clang++'), '-std=c++14', '-Wall',
                            '-Wextra', '-Werror', '-Wno-missing-field-initializers',
                            '-dynamiclib', '-undefined', 'dynamic_lookup', *includes,
                            str(path / 'probe.cpp'), '-o', str(path / 'numeric_probe.so')], check=True)
            package_args = []
            if os.environ.get('WOT_0922_CLIENT'):
                package_args = [str(Path(os.environ['WOT_0922_CLIENT']) / 'res/packages/scripts.pkg')]
            subprocess.run([python, str(path / 'check.py'), *package_args],
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)


if __name__ == '__main__':
    unittest.main()
