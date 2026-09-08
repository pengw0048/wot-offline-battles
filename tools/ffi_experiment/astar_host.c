/* Linux host bridge for computation and conversion measurements only.
 * The #1513 bridge has a separate executable/layout validation boundary. */
#include <Python.h>
#include <pythread.h>
#include <stdint.h>
#include "astar_core.h"
#include "query_bridge.h"
#include <string.h>

static int callback_proven = 0;
static unsigned long callback_thread = 0;

static PyObject *layout_self_test(PyObject *self, PyObject *args)
{
    long a, b, c;
    (void)self;
    if (offline_query_active()) return PyLong_FromLong(-18);
    if (!PyArg_ParseTuple(args, "lll", &a, &b, &c)) return NULL;
    if (a != 11 || b != 22 || c != 33) return PyLong_FromLong(-15);
    return PyLong_FromLong(a * 10000 + b * 100 + c);
}
static PyObject *dispatch(PyObject *self, PyObject *args)
{
    unsigned long low, high;
    int count;
    uintptr_t address;
    (void)self;
    if (!PyArg_ParseTuple(args, "kki", &low, &high, &count)) return NULL;
    if (low > 65535 || count < 1 || count > 12000012 ||
            high > (UINTPTR_MAX >> 16)) {
        PyErr_SetString(PyExc_ValueError, "invalid owned-buffer contract");
        return NULL;
    }
    address = ((uintptr_t)high << 16) | low;
    if (!address || address % sizeof(double)) {
        PyErr_SetString(PyExc_ValueError, "invalid owned-buffer alignment");
        return NULL;
    }
    if (offline_query_active() && callback_thread != (unsigned long)PyThread_get_thread_ident()) return PyLong_FromLong(18);
    if (!offline_query_can_enter((double *)address, count)) return PyLong_FromLong(18);
    return PyLong_FromLong(offline_astar_dispatch((double *)address, count));
}

static PyObject *callback_self_test(PyObject *self, PyObject *args)
{
    PyObject *callback, *arguments, *result;
    long value;
    (void)self;
    if (offline_query_active()) return PyLong_FromLong(-18);
    if (!PyArg_ParseTuple(args, "OO", &callback, &arguments)) return NULL;
    callback_proven = 0;
    if (!PyFunction_Check(callback) || !PyTuple_CheckExact(arguments))
        return PyLong_FromLong(-17);
    result = PyObject_CallObject(callback, arguments);
    if (!result) return NULL;
    value = PyLong_AsLong(result);
    if (PyErr_Occurred()) { Py_DECREF(result); return NULL; }
    if (value != 112233) { Py_DECREF(result); return PyLong_FromLong(-17); }
    callback_proven = 1;
    return result;
}
typedef struct {
    double *packet;
    int capacity;
    PyObject *callback, *arguments;
    int failed;
} QueryOwner;

static int invoke_query(void *opaque, double *packet, int count)
{
    QueryOwner *owner = (QueryOwner *)opaque;
    PyObject *result;
    if (count < 1 || count > owner->capacity) return 18;
    memcpy(owner->packet, packet, (size_t)count * sizeof(double));
    result = PyObject_CallObject(owner->callback, owner->arguments);
    if (!result) { owner->failed = 1; return 18; }
    Py_DECREF(result);
    memcpy(packet, owner->packet, (size_t)count * sizeof(double));
    return 0;
}
static PyObject *dispatch_sync(PyObject *self, PyObject *args)
{
    unsigned long low, high, query_low, query_high;
    int count, query_count, status;
    uintptr_t address, query_address;
    QueryOwner owner;
    unsigned long previous_thread;
    (void)self;
    if (!callback_proven) return PyLong_FromLong(18);
    if (!PyArg_ParseTuple(args, "kkikkiOO", &low, &high, &count,
                         &query_low, &query_high, &query_count,
                         &owner.callback, &owner.arguments)) return NULL;
    if (low > 65535 || query_low > 65535 ||
            high > (UINTPTR_MAX >> 16) || query_high > (UINTPTR_MAX >> 16) ||
            count < 1 || count > 12000012 || query_count < 16 || query_count > 12000012 ||
            !PyFunction_Check(owner.callback) || !PyTuple_CheckExact(owner.arguments))
        return PyLong_FromLong(18);
    address = ((uintptr_t)high << 16) | low;
    query_address = ((uintptr_t)query_high << 16) | query_low;
    if (!address || !query_address || address % sizeof(double) ||
            query_address % sizeof(double)) return PyLong_FromLong(18);
    if (offline_query_active() && callback_thread != (unsigned long)PyThread_get_thread_ident()) return PyLong_FromLong(18);
    if (!offline_query_can_enter((double *)address, count)) return PyLong_FromLong(18);
    owner.packet = (double *)query_address;
    owner.capacity = query_count;
    owner.failed = 0;
    previous_thread = callback_thread;
    callback_thread = (unsigned long)PyThread_get_thread_ident();
    status = offline_query_dispatch((double *)address, count, owner.packet, query_count, invoke_query, &owner);
    callback_thread = previous_thread;
    if (owner.failed) return NULL;
    return PyLong_FromLong(status);
}
static PyMethodDef methods[] = {
    {"layout_self_test", layout_self_test, METH_VARARGS, "Check bridge argument layout."},
    {"dispatch", dispatch, METH_VARARGS, "Execute one owned-buffer command."},
    {"callback_self_test", callback_self_test, METH_VARARGS, "Check synchronous callback ownership."},
    {"dispatch_sync", dispatch_sync, METH_VARARGS, "Run a complete computation with borrowed engine leaves."},
    {NULL, NULL, 0, NULL}
};
#if PY_MAJOR_VERSION >= 3
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "offline_astar_native", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC PyInit_offline_astar_native(void) { return PyModule_Create(&module); }
#else
PyMODINIT_FUNC initoffline_astar_native(void) {
    Py_InitModule3("offline_astar_native", methods, NULL);
}
#endif
