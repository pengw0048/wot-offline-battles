#pragma once

#include <Python.h>
#include <memory>
#include <string>

namespace wot
{
// The RenderContext owns the callback. Save tasks retain this owner, rather
// than capturing a callback which may be replaced before saving completes.
class StockScreenshotCallback
{
public:
    StockScreenshotCallback() : callback_(NULL), closed_(false) {}
    ~StockScreenshotCallback() { close(); }
    StockScreenshotCallback(const StockScreenshotCallback &) = delete;
    StockScreenshotCallback &operator=(const StockScreenshotCallback &) = delete;

    bool set(PyObject *callback)
    {
        if (closed_)
            return callback == NULL || callback == Py_None;
        replace(callback == Py_None ? NULL : callback);
        return true;
    }

    void close()
    {
        // Closing first prevents a callback's __del__ from reinstalling a
        // Python reference while the scripting runtime is shutting down.
        closed_ = true;
        replace(NULL);
    }

    bool complete(bool saved, const std::string &path)
    {
        if (!saved || closed_ || !callback_)
            return true;
        PyObject *callback = callback_;
        Py_INCREF(callback); // Pin across argument allocation and reentrancy.
        PyObject *name = PyString_FromStringAndSize(path.data(),
            static_cast<Py_ssize_t>(path.size()));
        PyObject *args = name ? PyTuple_Pack(1, name) : NULL;
        Py_XDECREF(name);
        PyObject *result = args ? PyObject_CallObject(callback, args) : NULL;
        Py_XDECREF(args);
        const bool succeeded = result != NULL;
        Py_XDECREF(result);
        Py_DECREF(callback);
        return succeeded;
    }

private:
    void replace(PyObject *callback)
    {
        Py_XINCREF(callback);
        PyObject *previous = callback_;
        callback_ = callback;
        Py_XDECREF(previous);
    }
    PyObject *callback_;
    bool closed_;
};
}

#ifndef WOT_STOCK_SCREENSHOT_CALLBACK_PORTABLE
#include "cstdmf/bw_namespace.hpp"
#include "cstdmf/bw_string.hpp"
#include "moo/com_object_wrap.hpp"
#include "moo/moo_dx.hpp"

BW_BEGIN_NAMESPACE

bool queueStockScreenshotCapture(const BW::string &format, const BW::string &name);
bool queueStockScreenshotSave(const ComObjectWrap<DX::Surface> &surface,
    D3DXIMAGE_FILEFORMAT format, const BW::string &path,
    const std::shared_ptr<wot::StockScreenshotCallback> &owner);

BW_END_NAMESPACE
#endif
