/*
 * Host-interpreter build of the compute bridge, for differential tests and
 * the local benchmark only.
 *
 * It exposes exactly the methods offline_compute_native.c exposes and calls
 * the same core, but it reaches the interpreter through the ordinary Python C
 * API instead of #1513's resolved RVAs and object layout.  It is never
 * packaged and never loaded by the game: it can prove the computation and the
 * buffer contract, and it proves nothing about the exact-client loader.
 */

#include <Python.h>

#include "offline_compute_core.h"


#define SELF_TEST_FIRST 11L
#define SELF_TEST_SECOND 22L
#define SELF_TEST_THIRD 33L

static int g_layout_proven = 0;


static PyObject *host_layout_self_test(PyObject *unused_self, PyObject *args)
{
	long first = 0, second = 0, third = 0;
	(void)unused_self;
	if (!PyArg_ParseTuple(args, "lll", &first, &second, &third)) {
		g_layout_proven = 0;
		PyErr_Clear();
		return Py_BuildValue("l", (long)-OFFLINE_COMPUTE_ARGUMENT_COUNT);
	}
	if (first != SELF_TEST_FIRST || second != SELF_TEST_SECOND ||
			third != SELF_TEST_THIRD) {
		g_layout_proven = 0;
		return Py_BuildValue("l", (long)-OFFLINE_COMPUTE_LAYOUT_UNPROVEN);
	}
	g_layout_proven = 1;
	return Py_BuildValue("l", first * 10000L + second * 100L + third);
}


static PyObject *host_prepare_sweep(PyObject *unused_self, PyObject *args)
{
	long low = 0, high = 0, count = 0;
	unsigned long address;
	(void)unused_self;
	if (!g_layout_proven) {
		return Py_BuildValue("l", (long)OFFLINE_COMPUTE_LAYOUT_UNPROVEN);
	}
	if (!PyArg_ParseTuple(args, "lll", &low, &high, &count)) {
		PyErr_Clear();
		return Py_BuildValue("l", (long)OFFLINE_COMPUTE_ARGUMENT_COUNT);
	}
	if (low < 0 || low > 0xffffL || high < 0) {
		return Py_BuildValue("l", (long)OFFLINE_COMPUTE_ADDRESS_UNREADABLE);
	}
	if (count < OFFLINE_COMPUTE_BUFFER_VALUES || count > 4096L) {
		return Py_BuildValue("l", (long)OFFLINE_COMPUTE_BUFFER_TOO_SMALL);
	}
	address = ((unsigned long)high << 16) | (unsigned long)low;
	return Py_BuildValue("l", (long)offline_compute_prepare_sweep(
		(double *)(Py_uintptr_t)address, (int)count));
}


static PyObject *host_buffer_values(PyObject *unused_self,
		PyObject *unused_args)
{
	(void)unused_self;
	(void)unused_args;
	return Py_BuildValue("l", (long)OFFLINE_COMPUTE_BUFFER_VALUES);
}


static PyMethodDef HOST_METHODS[] = {
	{"layout_self_test", host_layout_self_test, METH_VARARGS,
	 "Prove the argument contract before computing."},
	{"prepare_sweep", host_prepare_sweep, METH_VARARGS,
	 "Prepare one world-collision sweep inside a caller-owned buffer."},
	{"buffer_values", host_buffer_values, METH_VARARGS,
	 "Return the number of doubles the preparation buffer must hold."},
	{0, 0, 0, 0}
};


#if PY_MAJOR_VERSION >= 3
static struct PyModuleDef HOST_MODULE = {
	PyModuleDef_HEAD_INIT, "offline_compute_native",
	"Host build of the offline LAN sweep preparation.", -1, HOST_METHODS,
	0, 0, 0, 0
};

PyMODINIT_FUNC PyInit_offline_compute_native(void)
{
	return PyModule_Create(&HOST_MODULE);
}
#else
PyMODINIT_FUNC initoffline_compute_native(void)
{
	Py_InitModule3(
		"offline_compute_native", HOST_METHODS,
		"Host build of the offline LAN sweep preparation.");
}
#endif
