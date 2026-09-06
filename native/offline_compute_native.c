/*
 * Exact-build compute bridge for World of Tanks 0.9.22 #1513.
 *
 * The embedded interpreter ships no _ctypes and exports no Python C API, so
 * this module resolves the same two validated RVAs the instance-guard bridge
 * already uses, and reads its arguments through the exact object layout of
 * that executable:
 *
 *   PyInt_FromLong   +0x00be1180   ob_refcnt +0, ob_type +4, ob_ival +8
 *   Py_InitModule4   +0x00be1940
 *   PyInt_Type       +0x01664bf0   tp_basicsize 12
 *   PyTuple_Type     +0x0165c398   tp_basicsize 12, tp_itemsize 4
 *
 * No new C API function is resolved.  Arguments are plain ints, the result is
 * a status int, and every double crosses inside one caller-owned buffer whose
 * address the caller passes as two 16-bit halves.  #1513 is large-address
 * aware, so a whole address can exceed the signed 32-bit range that
 * PyInt_FromLong round-trips.
 *
 * The layout is proven inside this process before any computation runs:
 * layout_self_test() reads a known argument tuple and returns what it read.
 * Until the Python loader has confirmed that value, prepare_sweep() refuses.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>

#include "offline_compute_core.h"


typedef struct _PyObject {
	long ob_refcnt;
	void *ob_type;
} PyObject;

typedef PyObject *(__cdecl *PyCFunction)(PyObject *, PyObject *);

typedef struct _PyMethodDef {
	const char *ml_name;
	PyCFunction ml_meth;
	int ml_flags;
	const char *ml_doc;
} PyMethodDef;

typedef PyObject *(__cdecl *PyInitModule4Fn)(
	const char *, PyMethodDef *, const char *, PyObject *, int);
typedef PyObject *(__cdecl *PyIntFromLongFn)(long);


#define PYTHON_API_VERSION_27 1013
#define METH_VARARGS 0x0001

#define EXPECTED_PE_TIMESTAMP 0x5a6edca4U
#define EXPECTED_IMAGE_BASE 0x00400000U
#define EXPECTED_IMAGE_SIZE 0x0206a000U

#define RVA_PY_INIT_MODULE4 0x00be1940U
#define RVA_PY_INT_FROM_LONG 0x00be1180U
#define RVA_PY_INT_TYPE 0x01664bf0U

/* PyObject header, then PyIntObject.ob_ival; PyVarObject.ob_size, then items. */
#define OFFSET_OB_TYPE 4U
#define OFFSET_OB_IVAL 8U
#define OFFSET_OB_SIZE 8U
#define OFFSET_OB_ITEM 12U

#define SELF_TEST_FIRST 11L
#define SELF_TEST_SECOND 22L
#define SELF_TEST_THIRD 33L
#define SELF_TEST_RESULT 112233L

#define MAXIMUM_ARGUMENTS 4


static unsigned char *g_image_base = 0;
static PyIntFromLongFn g_py_int_from_long = 0;
static void *g_py_int_type = 0;
static int g_layout_proven = 0;


static int readable_region(const void *address, SIZE_T bytes)
{
	MEMORY_BASIC_INFORMATION info;
	uintptr_t cursor = (uintptr_t)address;
	uintptr_t end;
	uintptr_t previous;
	DWORD protection;
	if (address == 0 || bytes == 0 ||
			bytes > (SIZE_T)((uintptr_t)-1 - cursor)) {
		return 0;
	}
	end = cursor + bytes;
	while (cursor < end) {
		if (VirtualQuery((const void *)cursor, &info, sizeof(info)) !=
				sizeof(info) || info.State != MEM_COMMIT) {
			return 0;
		}
		if ((info.Protect & PAGE_GUARD) != 0) {
			return 0;
		}
		protection = info.Protect & 0xffU;
		if (protection != PAGE_READONLY &&
				protection != PAGE_READWRITE &&
				protection != PAGE_WRITECOPY &&
				protection != PAGE_EXECUTE_READ &&
				protection != PAGE_EXECUTE_READWRITE &&
				protection != PAGE_EXECUTE_WRITECOPY) {
			return 0;
		}
		if (info.RegionSize > (SIZE_T)((uintptr_t)-1 -
				(uintptr_t)info.BaseAddress)) {
			return 0;
		}
		previous = cursor;
		cursor = (uintptr_t)info.BaseAddress + info.RegionSize;
		if (cursor <= previous) {
			return 0;
		}
	}
	return 1;
}


static int writable_region(const void *address, SIZE_T bytes)
{
	MEMORY_BASIC_INFORMATION info;
	uintptr_t cursor = (uintptr_t)address;
	uintptr_t end;
	uintptr_t previous;
	DWORD protection;
	if (address == 0 || bytes == 0 ||
			bytes > (SIZE_T)((uintptr_t)-1 - cursor)) {
		return 0;
	}
	end = cursor + bytes;
	while (cursor < end) {
		if (VirtualQuery((const void *)cursor, &info, sizeof(info)) !=
				sizeof(info) || info.State != MEM_COMMIT) {
			return 0;
		}
		if ((info.Protect & PAGE_GUARD) != 0) {
			return 0;
		}
		protection = info.Protect & 0xffU;
		if (protection != PAGE_READWRITE &&
				protection != PAGE_WRITECOPY &&
				protection != PAGE_EXECUTE_READWRITE &&
				protection != PAGE_EXECUTE_WRITECOPY) {
			return 0;
		}
		previous = cursor;
		cursor = (uintptr_t)info.BaseAddress + info.RegionSize;
		if (cursor <= previous) {
			return 0;
		}
	}
	return 1;
}


static int validate_host(unsigned char *base)
{
	IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
	IMAGE_NT_HEADERS32 *nt;
	if (!readable_region(base, sizeof(IMAGE_DOS_HEADER)) ||
			dos->e_magic != IMAGE_DOS_SIGNATURE ||
			dos->e_lfanew <= 0 ||
			(DWORD)dos->e_lfanew > EXPECTED_IMAGE_SIZE) {
		return 0;
	}
	nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
	if (!readable_region(nt, sizeof(IMAGE_NT_HEADERS32)) ||
			nt->Signature != IMAGE_NT_SIGNATURE ||
			nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
			nt->FileHeader.TimeDateStamp != EXPECTED_PE_TIMESTAMP ||
			nt->OptionalHeader.ImageBase != EXPECTED_IMAGE_BASE ||
			nt->OptionalHeader.SizeOfImage != EXPECTED_IMAGE_SIZE) {
		return 0;
	}
	return (unsigned char *)EXPECTED_IMAGE_BASE == base;
}


static PyObject *python_int(long value)
{
	if (g_py_int_from_long == 0) {
		return 0;
	}
	return g_py_int_from_long(value);
}


/*
 * Read up to MAXIMUM_ARGUMENTS plain ints out of a METH_VARARGS tuple.
 * Every dereference is bounds- and type-checked first: the exact tuple and
 * int layouts above are the only assumption, and layout_self_test() proves
 * them inside this interpreter before any caller relies on a result.
 */
static int read_int_arguments(PyObject *args, long *out, int wanted)
{
	unsigned char *tuple = (unsigned char *)args;
	long size;
	int index;
	if (wanted < 0 || wanted > MAXIMUM_ARGUMENTS) {
		return OFFLINE_COMPUTE_ARGUMENT_COUNT;
	}
	if (!readable_region(tuple, OFFSET_OB_ITEM +
			(SIZE_T)wanted * sizeof(void *))) {
		return OFFLINE_COMPUTE_ADDRESS_UNREADABLE;
	}
	size = *(long *)(tuple + OFFSET_OB_SIZE);
	if (size != (long)wanted) {
		return OFFLINE_COMPUTE_ARGUMENT_COUNT;
	}
	for (index = 0; index < wanted; ++index) {
		unsigned char *item = *(unsigned char **)(
			tuple + OFFSET_OB_ITEM + (unsigned)index * sizeof(void *));
		if (!readable_region(item, OFFSET_OB_IVAL + sizeof(long))) {
			return OFFLINE_COMPUTE_ADDRESS_UNREADABLE;
		}
		if (*(void **)(item + OFFSET_OB_TYPE) != g_py_int_type) {
			return OFFLINE_COMPUTE_ARGUMENT_TYPE;
		}
		out[index] = *(long *)(item + OFFSET_OB_IVAL);
	}
	return OFFLINE_COMPUTE_OK;
}


static PyObject *layout_self_test(PyObject *unused_self, PyObject *args)
{
	long values[3];
	int status;
	(void)unused_self;
	status = read_int_arguments(args, values, 3);
	if (status != OFFLINE_COMPUTE_OK) {
		g_layout_proven = 0;
		return python_int(-status);
	}
	if (values[0] != SELF_TEST_FIRST || values[1] != SELF_TEST_SECOND ||
			values[2] != SELF_TEST_THIRD) {
		g_layout_proven = 0;
		return python_int(-OFFLINE_COMPUTE_LAYOUT_UNPROVEN);
	}
	g_layout_proven = 1;
	/* Every argument reaches the result, so the caller's comparison with
	 * SELF_TEST_RESULT proves the tuple size, the item pointers and the
	 * integer field were all read correctly. */
	return python_int(
		values[0] * 10000L + values[1] * 100L + values[2]);
}


static PyObject *prepare_sweep(PyObject *unused_self, PyObject *args)
{
	long values[3];
	unsigned long address;
	double *buffer;
	long count;
	int status;
	(void)unused_self;
	if (g_image_base == 0 || g_py_int_type == 0) {
		return python_int(OFFLINE_COMPUTE_HOST_UNVALIDATED);
	}
	if (!g_layout_proven) {
		return python_int(OFFLINE_COMPUTE_LAYOUT_UNPROVEN);
	}
	status = read_int_arguments(args, values, 3);
	if (status != OFFLINE_COMPUTE_OK) {
		return python_int(status);
	}
	if (values[0] < 0 || values[0] > 0xffffL ||
			values[1] < 0 || values[1] > 0xffffL) {
		return python_int(OFFLINE_COMPUTE_ADDRESS_UNREADABLE);
	}
	count = values[2];
	if (count < OFFLINE_COMPUTE_BUFFER_VALUES ||
			count > 4096L) {
		return python_int(OFFLINE_COMPUTE_BUFFER_TOO_SMALL);
	}
	address = ((unsigned long)values[1] << 16) | (unsigned long)values[0];
	buffer = (double *)(uintptr_t)address;
	if (((uintptr_t)buffer % sizeof(double)) != 0 ||
			!writable_region(buffer, (SIZE_T)count * sizeof(double))) {
		return python_int(OFFLINE_COMPUTE_ADDRESS_UNREADABLE);
	}
	return python_int(offline_compute_prepare_sweep(buffer, (int)count));
}


static PyObject *buffer_values(PyObject *unused_self, PyObject *unused_args)
{
	(void)unused_self;
	(void)unused_args;
	return python_int((long)OFFLINE_COMPUTE_BUFFER_VALUES);
}


static PyMethodDef MODULE_METHODS[] = {
	{
		"layout_self_test", layout_self_test, METH_VARARGS,
		"Prove this interpreter's int and tuple layout before computing."
	},
	{
		"prepare_sweep", prepare_sweep, METH_VARARGS,
		"Prepare one world-collision sweep inside a caller-owned buffer."
	},
	{
		"buffer_values", buffer_values, METH_VARARGS,
		"Return the number of doubles the preparation buffer must hold."
	},
	{0, 0, 0, 0}
};


__declspec(dllexport) void __cdecl initoffline_compute_native(void)
{
	PyInitModule4Fn init_module;
	PyObject *probe;
	unsigned char *base = (unsigned char *)GetModuleHandleW(0);
	if (base == 0 || !validate_host(base)) {
		return;
	}
	g_image_base = base;
	g_py_int_from_long =
		(PyIntFromLongFn)(g_image_base + RVA_PY_INT_FROM_LONG);
	/* Derive the int type from a real object, then confirm the reviewed RVA.
	 * The probe is a cached small int, so the retained reference is the
	 * interpreter's own and nothing is leaked per call. */
	probe = g_py_int_from_long(64);
	if (probe == 0 || !readable_region(probe, OFFSET_OB_IVAL + sizeof(long))) {
		g_py_int_from_long = 0;
		g_image_base = 0;
		return;
	}
	g_py_int_type = *(void **)((unsigned char *)probe + OFFSET_OB_TYPE);
	if (g_py_int_type != (void *)(g_image_base + RVA_PY_INT_TYPE) ||
			*(long *)((unsigned char *)probe + OFFSET_OB_IVAL) != 64L) {
		g_py_int_type = 0;
		g_py_int_from_long = 0;
		g_image_base = 0;
		return;
	}
	init_module = (PyInitModule4Fn)(g_image_base + RVA_PY_INIT_MODULE4);
	init_module(
		"offline_compute_native", MODULE_METHODS,
		"Exact-build sweep preparation for the offline LAN hidden worker.",
		0, PYTHON_API_VERSION_27);
}
