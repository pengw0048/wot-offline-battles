/* Linux host bridge for computation and conversion measurements only.
 * The #1513 bridge has a separate executable/layout validation boundary. */
#include <Python.h>
#include <stdint.h>
#include "astar_core.h"

static PyObject *layout_self_test(PyObject *self, PyObject *args)
{
    long a, b, c;
    (void)self;
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
    return PyLong_FromLong(offline_astar_dispatch((double *)address, count));
}
static PyMethodDef methods[] = {
    {"layout_self_test", layout_self_test, METH_VARARGS, "Check bridge argument layout."},
    {"dispatch", dispatch, METH_VARARGS, "Execute one owned-buffer command."},
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
