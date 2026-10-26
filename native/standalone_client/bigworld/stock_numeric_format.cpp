#include "pch.hpp"
#include "stock_numeric_format.hpp"
#include "pyscript/script.hpp"

#include <windows.h>
#include <strsafe.h>

BW_BEGIN_NAMESPACE
int StockNumericFormat_token = 1;

namespace
{
class StockNumericFormats
{
public:
    StockNumericFormats()
    {
        // 006f2300: the single NLSFormats owner snapshots the user's actual
        // Windows locale. A failed query makes integral/currency calls pass
        // NULL to NLS; it does not select an invented locale or separator.
        currency_.NumDigits = 0;
        currency_.NegativeOrder = 5;
        currency_.PositiveOrder = 1;
        currency_.lpCurrencySymbol = const_cast<wchar_t *>(L"");
        currency_.lpDecimalSep = currencyDecimal_;
        currency_.lpThousandSep = currencyThousands_;
        wchar_t groups[10] = {};
        validCurrency_ = numericLocale(LOCALE_ILZERO, currency_.LeadingZero) &&
            textLocale(LOCALE_SMONDECIMALSEP, currencyDecimal_, 4) &&
            textLocale(LOCALE_SMONTHOUSANDSEP, currencyThousands_, 4) &&
            textLocale(LOCALE_SMONGROUPING, groups, 10);
        if (validCurrency_)
            currency_.Grouping = wot::stock_numeric_format::grouping(groups);

        integral_.NumDigits = 0;
        integral_.lpDecimalSep = const_cast<wchar_t *>(L"");
        decimal_.lpDecimalSep = decimalSeparator_;
        validNumber_ = numericLocale(LOCALE_IDIGITS, decimal_.NumDigits) &&
            textLocale(LOCALE_SDECIMAL, decimalSeparator_, 4) &&
            numericLocale(LOCALE_ILZERO, integral_.LeadingZero);
        decimal_.LeadingZero = integral_.LeadingZero;
        validNumber_ = validNumber_ &&
            numericLocale(LOCALE_INEGNUMBER, integral_.NegativeOrder);
        decimal_.NegativeOrder = integral_.NegativeOrder;
        integral_.lpThousandSep = decimal_.lpThousandSep = thousands_;
        validNumber_ = validNumber_ &&
            textLocale(LOCALE_STHOUSAND, thousands_, 4) &&
            textLocale(LOCALE_SGROUPING, groups, 10);
        if (validNumber_)
            integral_.Grouping = decimal_.Grouping =
                wot::stock_numeric_format::grouping(groups);
    }

    std::wstring integral(std::int64_t value) const
    {
        wchar_t input[512] = {}, output[512] = {};
        // 006f4120 -> 006d5d90 is StringCchPrintfW, not a stream formatter.
        if (SUCCEEDED(StringCchPrintfW(input, 512, L"%I64d", value)) &&
            GetNumberFormatW(LOCALE_USER_DEFAULT, 0, input,
                validNumber_ ? &integral_ : NULL, output, 512))
            return output;
        return std::wstring();
    }

    std::wstring gold(std::int64_t value) const
    {
        wchar_t input[318] = {}, output[512] = {};
        if (SUCCEEDED(StringCchPrintfW(input, 317, L"%I64d", value)) &&
            GetCurrencyFormatW(LOCALE_USER_DEFAULT, 0, input,
                validCurrency_ ? &currency_ : NULL, output, 512))
            return output;
        return std::wstring();
    }

    bool nice(double value, std::wstring &result) const
    {
        double integralPart;
        if (std::modf(value, &integralPart) == 0.0)
        {
            result = integral(wot::stock_numeric_format::signedInteger(integralPart));
            return true;
        }
        // 006f4bd6 dereferences the decimal format after its validity check.
        // A failed Windows locale query is a local error here, rather than
        // recreating that native null dereference or guessing decimal digits.
        if (!validNumber_)
        {
            PyErr_SetString(PyExc_RuntimeError, "Windows numeric locale is unavailable");
            return false;
        }
        const unsigned digits = wot::stock_numeric_format::niceDigits(
            value, decimal_.NumDigits);
        NUMBERFMTW format = decimal_;
        format.NumDigits = digits;
        if (digits == 0)
            format.lpDecimalSep = const_cast<wchar_t *>(L"");
        wchar_t input[317] = {}, output[512] = {};
        // The original literal at 014bf240 is "%lf": six input decimals.
        // A local copy preserves the original temporary NumDigits mutation
        // and restoration without introducing a second locale owner.
        if (SUCCEEDED(StringCchPrintfW(input, 317, L"%lf", value)) &&
            GetNumberFormatW(LOCALE_USER_DEFAULT, 0, input, &format, output, 512))
        {
            result = output;
            if (digits > 0)
                wot::stock_numeric_format::trimDecimal(result);
        }
        else
            result.clear();
        return true;
    }

private:
    static bool numericLocale(LCTYPE key, UINT &result)
    {
        return GetLocaleInfoW(LOCALE_USER_DEFAULT, key | LOCALE_RETURN_NUMBER,
            reinterpret_cast<wchar_t *>(&result), 4) != 0;
    }
    static bool textLocale(LCTYPE key, wchar_t *result, int size)
    {
        return GetLocaleInfoW(LOCALE_USER_DEFAULT, key, result, size) != 0;
    }
    CURRENCYFMTW currency_ = {};
    NUMBERFMTW integral_ = {}, decimal_ = {};
    wchar_t currencyDecimal_[4] = {}, currencyThousands_[4] = {};
    wchar_t decimalSeparator_[4] = {}, thousands_[4] = {};
    bool validCurrency_ = false, validNumber_ = false;
};

StockNumericFormats &numericFormats()
{
    static StockNumericFormats formats;
    return formats;
}

bool readInteger(PyObject *value, std::int64_t &result)
{
    // 006903c0 accepts actual int/long/float subtypes, including bool, but
    // never arbitrary objects merely implementing __int__ or __float__.
    if (PyLong_Check(value))
    {
        result = PyLong_AsLongLong(value);
        if (!PyErr_Occurred())
            return true;
    }
    if (PyInt_Check(value))
    {
        result = PyInt_AsLong(value);
        return true;
    }
    if (PyFloat_Check(value))
    {
        result = wot::stock_numeric_format::signedInteger(PyFloat_AsDouble(value));
        return true;
    }
    PyErr_Format(PyExc_TypeError, "%s must be set to a long", "() argument 1");
    return false;
}

bool readDouble(PyObject *value, double &result)
{
    // 0068fc80's long branch is specifically Win32 unsigned long, not
    // PyLong_AsDouble. Negative Python ints and negative Python longs differ.
    if (PyFloat_Check(value))
    {
        result = PyFloat_AsDouble(value);
        return true;
    }
    if (PyInt_Check(value))
    {
        result = static_cast<double>(PyInt_AsLong(value));
        return true;
    }
    if (PyLong_Check(value))
    {
        result = static_cast<double>(PyLong_AsUnsignedLong(value));
        if (!PyErr_Occurred())
            return true;
    }
    PyErr_Format(PyExc_TypeError, "%s must be set to a float", "() argument 1");
    return false;
}

PyObject *utf8Result(const std::wstring &value)
{
    // 006f2990/006f2be0/006f33d0: wide -> unicode -> strict UTF-8 byte str.
    PyObject *unicode = PyUnicode_FromWideChar(value.data(),
        static_cast<Py_ssize_t>(value.size()));
    if (!unicode)
        return NULL;
    PyObject *bytes = PyUnicode_AsEncodedString(unicode, "utf_8", "strict");
    Py_DECREF(unicode);
    return bytes;
}
}

PyObject *py_wg_getIntegralFormat(PyObject *args)
{
    if (PyTuple_Size(args) != 1)
        return Script::argCountError("wg_getIntegralFormat", 1, 1, "int64");
    std::int64_t number;
    if (!readInteger(PyTuple_GetItem(args, 0), number))
        return NULL;
    return utf8Result(numericFormats().integral(number));
}
PY_MODULE_FUNCTION(wg_getIntegralFormat, BigWorld)

PyObject *py_wg_getGoldFormat(PyObject *args)
{
    if (PyTuple_Size(args) != 1)
        return Script::argCountError("wg_getGoldFormat", 1, 1, "int64");
    std::int64_t number;
    if (!readInteger(PyTuple_GetItem(args, 0), number))
        return NULL;
    return utf8Result(numericFormats().gold(number));
}
PY_MODULE_FUNCTION(wg_getGoldFormat, BigWorld)

PyObject *py_wg_getNiceNumberFormat(PyObject *args)
{
    if (PyTuple_Size(args) != 1)
        return Script::argCountError("wg_getNiceNumberFormat", 1, 1, "double");
    double number;
    if (!readDouble(PyTuple_GetItem(args, 0), number))
        return NULL;
    std::wstring result;
    if (!numericFormats().nice(number, result))
        return NULL;
    return utf8Result(result);
}
PY_MODULE_FUNCTION(wg_getNiceNumberFormat, BigWorld)
BW_END_NAMESPACE
