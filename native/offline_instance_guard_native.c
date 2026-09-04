/*
 * Exact-build CPython 2.7 bridge for World of Tanks 0.9.22 #1513.
 *
 * The embedded interpreter does not ship _ctypes and does not export its
 * Python C API.  This module resolves the two required C API functions from
 * validated RVAs in the main executable, then exposes a deliberately small
 * native surface.  The gameplay-mapping opcode is changed only between one
 * Python apply/restore pair; WGC handles are never closed directly.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>


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
typedef void (__attribute__((thiscall)) *WgcCleanupThunkFn)(void *);


#define PYTHON_API_VERSION_27 1013
#define METH_NOARGS 0x0004

#define EXPECTED_PE_TIMESTAMP 0x5a6edca4U
#define EXPECTED_IMAGE_BASE 0x00400000U
#define EXPECTED_IMAGE_SIZE 0x0206a000U

#define RVA_PY_INIT_MODULE4 0x00be1940U
#define RVA_PY_INT_FROM_LONG 0x00be1180U
#define RVA_WGC_CLEANUP_THUNK 0x004b7180U
#define RVA_WGC_HOLDER 0x019351ecU
#define RVA_WGC_WRAPPER_VTABLE 0x010ef788U
#define RVA_MAPPING_SIGNATURE 0x00254fb9U
#define RVA_MAPPING_MASK_IMMEDIATE 0x00254fc2U

#define CLIENT_MUTEX_NAME L"wot_client_mutex"

#define GUARD_STATUS_HOLDER_UNREADABLE 1L
#define GUARD_STATUS_WRAPPER_MISSING 2L
#define GUARD_STATUS_WRAPPER_INVALID 3L
#define GUARD_STATUS_STATE_INVALID 4L
#define GUARD_STATUS_API_MISSING 5L
#define GUARD_STATUS_WGC_MODULE_MISSING 6L
#define GUARD_STATUS_API_INVALID 7L
#define GUARD_STATUS_CHILD_INVALID 8L
#define GUARD_STATUS_HOLDER_CHANGED 9L
#define GUARD_STATUS_API_NOT_CLEARED 10L
#define GUARD_STATUS_CHILD_NOT_CLEARED 11L
#define GUARD_STATUS_STATE_NOT_DISABLED 12L
#define GUARD_STATUS_MUTEX_STILL_EXISTS 13L
#define GUARD_STATUS_MUTEX_PROBE_FAILED 14L

#define MAPPING_STATUS_ALREADY_ACTIVE 101L
#define MAPPING_STATUS_NOT_ACTIVE 102L
#define MAPPING_STATUS_SIGNATURE_CHANGED 103L
#define MAPPING_STATUS_PROTECT_ENABLE_FAILED 104L
#define MAPPING_STATUS_CACHE_FLUSH_FAILED 105L
#define MAPPING_STATUS_PROTECT_RESTORE_FAILED 106L
#define MAPPING_STATUS_VERIFY_FAILED 107L
#define MAPPING_STATUS_ROLLBACK_FAILED 108L

#define MAX_HIDDEN_WINDOWS 16U


typedef struct HiddenWindow {
	HWND handle;
	WINDOWPLACEMENT placement;
} HiddenWindow;

typedef struct HideContext {
	DWORD process_id;
	DWORD error_code;
	unsigned int first_new_index;
} HideContext;


static unsigned char *g_image_base = 0;
static PyIntFromLongFn g_py_int_from_long = 0;
static HiddenWindow g_hidden_windows[MAX_HIDDEN_WINDOWS];
static unsigned int g_hidden_window_count = 0;
static int g_mapping_mask_active = 0;
static DWORD g_mapping_original_protection = 0;

static const unsigned char MAPPING_ORIGINAL_SIGNATURE[] = {
	0xc6, 0x45, 0xfc, 0x06, 0x85, 0xf6, 0x74, 0x44,
	0x6a, 0xff, 0x57, 0x8d, 0x45, 0xb0, 0x8b, 0xce,
	0x50, 0xff, 0xb5, 0x24, 0xff, 0xff, 0xff, 0xff,
	0xb5, 0x20, 0xff, 0xff, 0xff, 0xe8, 0xc5, 0xda,
	0x46, 0x00
};

static const unsigned char MAPPING_PATCHED_SIGNATURE[] = {
	0xc6, 0x45, 0xfc, 0x06, 0x85, 0xf6, 0x74, 0x44,
	0x6a, 0x01, 0x57, 0x8d, 0x45, 0xb0, 0x8b, 0xce,
	0x50, 0xff, 0xb5, 0x24, 0xff, 0xff, 0xff, 0xff,
	0xb5, 0x20, 0xff, 0xff, 0xff, 0xe8, 0xc5, 0xda,
	0x46, 0x00
};


static int bytes_equal(const unsigned char *actual,
		const unsigned char *expected, unsigned int count)
{
	unsigned int index;
	for (index = 0; index < count; ++index) {
		if (actual[index] != expected[index]) {
			return 0;
		}
	}
	return 1;
}


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


static int executable_region(const void *address)
{
	MEMORY_BASIC_INFORMATION info;
	DWORD protection;
	if (VirtualQuery(address, &info, sizeof(info)) != sizeof(info) ||
			info.State != MEM_COMMIT) {
		return 0;
	}
	protection = info.Protect & 0xffU;
	return protection == PAGE_EXECUTE ||
		protection == PAGE_EXECUTE_READ ||
		protection == PAGE_EXECUTE_READWRITE ||
		protection == PAGE_EXECUTE_WRITECOPY;
}


static int address_in_module(const void *address, HMODULE module)
{
	MEMORY_BASIC_INFORMATION info;
	if (module == 0 ||
			VirtualQuery(address, &info, sizeof(info)) != sizeof(info)) {
		return 0;
	}
	return info.AllocationBase == (void *)module;
}


static int valid_virtual_method(void *object, unsigned int byte_offset,
		HMODULE expected_module)
{
	void **vtable;
	void *method;
	if (!readable_region(object, sizeof(void *))) {
		return 0;
	}
	vtable = *(void ***)object;
	if (!readable_region((unsigned char *)vtable + byte_offset,
			sizeof(void *))) {
		return 0;
	}
	method = *(void **)((unsigned char *)vtable + byte_offset);
	return executable_region(method) &&
		(expected_module == 0 || address_in_module(method, expected_module));
}


static int validate_host(unsigned char *base)
{
	IMAGE_DOS_HEADER *dos;
	IMAGE_NT_HEADERS32 *nt;
	static const unsigned char py_init_signature[] = {
		0x55, 0x8b, 0xec, 0x81, 0xec, 0x14, 0x02, 0x00,
		0x00, 0xa1, 0x70, 0x35, 0xce, 0x01, 0x33, 0xc5
	};
	static const unsigned char py_int_signature[] = {
		0x55, 0x8b, 0xec, 0x56, 0x8b, 0x75, 0x08, 0x8d,
		0x46, 0x05, 0x3d, 0x05, 0x01, 0x00, 0x00, 0x77
	};
	static const unsigned char wgc_cleanup_signature[] = {
		0x8b, 0x09, 0xe9, 0x09, 0x00, 0x00, 0x00, 0xcc,
		0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc
	};
	if ((uintptr_t)base != EXPECTED_IMAGE_BASE ||
			!readable_region(base, sizeof(IMAGE_DOS_HEADER))) {
		return 0;
	}
	dos = (IMAGE_DOS_HEADER *)base;
	if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 ||
			dos->e_lfanew > 0x1000) {
		return 0;
	}
	nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
	if (!readable_region(nt, sizeof(IMAGE_NT_HEADERS32)) ||
			nt->Signature != IMAGE_NT_SIGNATURE ||
			nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
			nt->FileHeader.TimeDateStamp != EXPECTED_PE_TIMESTAMP ||
			nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC ||
			nt->OptionalHeader.ImageBase != EXPECTED_IMAGE_BASE ||
			nt->OptionalHeader.SizeOfImage != EXPECTED_IMAGE_SIZE) {
		return 0;
	}
	if (!readable_region(base + RVA_PY_INIT_MODULE4,
			sizeof(py_init_signature)) ||
			!readable_region(base + RVA_PY_INT_FROM_LONG,
				sizeof(py_int_signature)) ||
			!readable_region(base + RVA_WGC_CLEANUP_THUNK,
				sizeof(wgc_cleanup_signature)) ||
			!readable_region(base + RVA_MAPPING_SIGNATURE,
				sizeof(MAPPING_ORIGINAL_SIGNATURE)) ||
			!bytes_equal(base + RVA_PY_INIT_MODULE4, py_init_signature,
				sizeof(py_init_signature)) ||
			!bytes_equal(base + RVA_PY_INT_FROM_LONG, py_int_signature,
				sizeof(py_int_signature)) ||
			!bytes_equal(base + RVA_WGC_CLEANUP_THUNK,
				wgc_cleanup_signature, sizeof(wgc_cleanup_signature)) ||
			!bytes_equal(base + RVA_MAPPING_SIGNATURE,
				MAPPING_ORIGINAL_SIGNATURE,
				sizeof(MAPPING_ORIGINAL_SIGNATURE))) {
		return 0;
	}
	return executable_region(base + RVA_PY_INIT_MODULE4) &&
		executable_region(base + RVA_PY_INT_FROM_LONG) &&
		executable_region(base + RVA_WGC_CLEANUP_THUNK) &&
		executable_region(base + RVA_MAPPING_SIGNATURE);
}


static PyObject *python_int(long value)
{
	if (g_py_int_from_long == 0) {
		return 0;
	}
	return g_py_int_from_long(value);
}


static long verify_client_mutex_absent(void)
{
	HANDLE probe;
	DWORD error_code;
	SetLastError(ERROR_SUCCESS);
	probe = OpenMutexW(SYNCHRONIZE, FALSE, CLIENT_MUTEX_NAME);
	if (probe != 0) {
		/* This is our probe handle, never WGC's borrowed handle. */
		CloseHandle(probe);
		return GUARD_STATUS_MUTEX_STILL_EXISTS;
	}
	error_code = GetLastError();
	if (error_code != ERROR_FILE_NOT_FOUND) {
		return GUARD_STATUS_MUTEX_PROBE_FAILED;
	}
	return 0;
}


static PyObject *release_client_guard(PyObject *unused_self,
		PyObject *unused_args)
{
	unsigned char **holder;
	unsigned char *wrapper;
	void *api;
	void *child;
	unsigned int state;
	HMODULE wgc_module;
	WgcCleanupThunkFn cleanup;
	(void)unused_self;
	(void)unused_args;

	holder = (unsigned char **)(g_image_base + RVA_WGC_HOLDER);
	if (!readable_region(holder, sizeof(*holder))) {
		return python_int(GUARD_STATUS_HOLDER_UNREADABLE);
	}
	wrapper = *holder;
	if (wrapper == 0) {
		return python_int(GUARD_STATUS_WRAPPER_MISSING);
	}
	if (!readable_region(wrapper, 0x54U) ||
			*(void **)wrapper !=
				(void *)(g_image_base + RVA_WGC_WRAPPER_VTABLE)) {
		return python_int(GUARD_STATUS_WRAPPER_INVALID);
	}
	api = *(void **)(wrapper + 0x44U);
	child = *(void **)(wrapper + 0x48U);
	state = *(unsigned int *)(wrapper + 0x50U);
	if (state > 6U) {
		return python_int(GUARD_STATUS_STATE_INVALID);
	}
	if (api == 0) {
		if (child == 0 && state == 4U) {
			return python_int(verify_client_mutex_absent());
		}
		return python_int(GUARD_STATUS_API_MISSING);
	}
	wgc_module = GetModuleHandleW(L"wgc_api.dll");
	if (wgc_module == 0) {
		return python_int(GUARD_STATUS_WGC_MODULE_MISSING);
	}
	if (!valid_virtual_method(api, 0U, wgc_module)) {
		return python_int(GUARD_STATUS_API_INVALID);
	}
	if (child != 0 && !valid_virtual_method(child, 0x0cU, 0)) {
		return python_int(GUARD_STATUS_CHILD_INVALID);
	}

	/* This is the same thiscall thunk used by #1513's normal engine cleanup.
	 * It clears the child and API fields, invokes the WGC API destructor, and
	 * leaves the wrapper allocated for its idempotent process-exit destructor.
	 */
	cleanup = (WgcCleanupThunkFn)(g_image_base + RVA_WGC_CLEANUP_THUNK);
	cleanup(holder);

	if (*holder != wrapper) {
		return python_int(GUARD_STATUS_HOLDER_CHANGED);
	}
	if (*(void **)(wrapper + 0x44U) != 0) {
		return python_int(GUARD_STATUS_API_NOT_CLEARED);
	}
	if (*(void **)(wrapper + 0x48U) != 0) {
		return python_int(GUARD_STATUS_CHILD_NOT_CLEARED);
	}
	if (*(unsigned int *)(wrapper + 0x50U) != 4U) {
		return python_int(GUARD_STATUS_STATE_NOT_DISABLED);
	}
	return python_int(verify_client_mutex_absent());
}


static long restore_standard_gameplay_mask_internal(void)
{
	unsigned char *signature = g_image_base + RVA_MAPPING_SIGNATURE;
	unsigned char *mask = g_image_base + RVA_MAPPING_MASK_IMMEDIATE;
	DWORD current_protection = 0;
	DWORD unused_protection = 0;
	int flush_succeeded;
	int protection_restored;
	long status = 0;

	if (!g_mapping_mask_active) {
		return MAPPING_STATUS_NOT_ACTIVE;
	}
	if (!readable_region(signature,
			sizeof(MAPPING_PATCHED_SIGNATURE)) ||
			(*mask != 0x01U && *mask != 0xffU)) {
		return MAPPING_STATUS_SIGNATURE_CHANGED;
	}
	if (!VirtualProtect(mask, 1U, PAGE_EXECUTE_READWRITE,
			&current_protection)) {
		return MAPPING_STATUS_PROTECT_ENABLE_FAILED;
	}
	if (*mask == 0x01U) {
		*mask = 0xffU;
	}
	flush_succeeded = FlushInstructionCache(
		GetCurrentProcess(), mask, 1U) != 0;
	protection_restored = VirtualProtect(
		mask, 1U, g_mapping_original_protection,
		&unused_protection) != 0;

	if (!flush_succeeded) {
		status = MAPPING_STATUS_CACHE_FLUSH_FAILED;
	} else if (!protection_restored) {
		status = MAPPING_STATUS_PROTECT_RESTORE_FAILED;
	} else if (!readable_region(signature,
			sizeof(MAPPING_ORIGINAL_SIGNATURE)) || *mask != 0xffU) {
		status = MAPPING_STATUS_VERIFY_FAILED;
	} else if (!bytes_equal(signature, MAPPING_ORIGINAL_SIGNATURE,
			sizeof(MAPPING_ORIGINAL_SIGNATURE))) {
		/* A neighbouring opcode changed.  Our immediate is restored, but
		 * report the exact-build boundary violation to the Python caller.
		 */
		status = MAPPING_STATUS_SIGNATURE_CHANGED;
	}
	if (*mask == 0xffU && flush_succeeded && protection_restored) {
		g_mapping_mask_active = 0;
		g_mapping_original_protection = 0;
	}
	return status;
}


static PyObject *apply_standard_gameplay_mask(PyObject *unused_self,
		PyObject *unused_args)
{
	unsigned char *signature = g_image_base + RVA_MAPPING_SIGNATURE;
	unsigned char *mask = g_image_base + RVA_MAPPING_MASK_IMMEDIATE;
	DWORD old_protection = 0;
	DWORD unused_protection = 0;
	int flush_succeeded;
	int protection_restored;
	long status = 0;
	long rollback_status;
	(void)unused_self;
	(void)unused_args;

	if (g_mapping_mask_active) {
		return python_int(MAPPING_STATUS_ALREADY_ACTIVE);
	}
	if (!readable_region(signature,
			sizeof(MAPPING_ORIGINAL_SIGNATURE)) ||
			!bytes_equal(signature, MAPPING_ORIGINAL_SIGNATURE,
				sizeof(MAPPING_ORIGINAL_SIGNATURE))) {
		return python_int(MAPPING_STATUS_SIGNATURE_CHANGED);
	}
	if (!VirtualProtect(mask, 1U, PAGE_EXECUTE_READWRITE,
			&old_protection)) {
		return python_int(MAPPING_STATUS_PROTECT_ENABLE_FAILED);
	}
	g_mapping_original_protection = old_protection;
	*mask = 0x01U;
	g_mapping_mask_active = 1;
	flush_succeeded = FlushInstructionCache(
		GetCurrentProcess(), mask, 1U) != 0;
	protection_restored = VirtualProtect(
		mask, 1U, old_protection, &unused_protection) != 0;

	if (!flush_succeeded) {
		status = MAPPING_STATUS_CACHE_FLUSH_FAILED;
	} else if (!protection_restored) {
		status = MAPPING_STATUS_PROTECT_RESTORE_FAILED;
	} else if (!readable_region(signature,
			sizeof(MAPPING_PATCHED_SIGNATURE)) ||
			!bytes_equal(signature, MAPPING_PATCHED_SIGNATURE,
				sizeof(MAPPING_PATCHED_SIGNATURE))) {
		status = MAPPING_STATUS_VERIFY_FAILED;
	}
	if (status != 0) {
		rollback_status = restore_standard_gameplay_mask_internal();
		if (rollback_status != 0) {
			return python_int(MAPPING_STATUS_ROLLBACK_FAILED);
		}
	}
	return python_int(status);
}


static PyObject *restore_standard_gameplay_mask(PyObject *unused_self,
		PyObject *unused_args)
{
	(void)unused_self;
	(void)unused_args;
	return python_int(restore_standard_gameplay_mask_internal());
}


static int hidden_window_index(HWND handle)
{
	unsigned int index;
	for (index = 0; index < g_hidden_window_count; ++index) {
		if (g_hidden_windows[index].handle == handle) {
			return (int)index;
		}
	}
	return -1;
}


static BOOL CALLBACK hide_window_callback(HWND handle, LPARAM parameter)
{
	HideContext *context = (HideContext *)parameter;
	DWORD process_id = 0;
	WINDOWPLACEMENT placement;
	GetWindowThreadProcessId(handle, &process_id);
	if (process_id != context->process_id || !IsWindowVisible(handle) ||
			hidden_window_index(handle) >= 0) {
		return TRUE;
	}
	if (g_hidden_window_count >= MAX_HIDDEN_WINDOWS) {
		context->error_code = ERROR_INSUFFICIENT_BUFFER;
		return FALSE;
	}
	ZeroMemory(&placement, sizeof(placement));
	placement.length = sizeof(placement);
	if (!GetWindowPlacement(handle, &placement)) {
		context->error_code = GetLastError();
		if (context->error_code == ERROR_SUCCESS) {
			context->error_code = ERROR_GEN_FAILURE;
		}
		return FALSE;
	}
	g_hidden_windows[g_hidden_window_count].handle = handle;
	g_hidden_windows[g_hidden_window_count].placement = placement;
	++g_hidden_window_count;
	ShowWindow(handle, SW_HIDE);
	return TRUE;
}


static void restore_hidden_range(unsigned int first_index)
{
	while (g_hidden_window_count > first_index) {
		HiddenWindow *record =
			&g_hidden_windows[g_hidden_window_count - 1];
		if (IsWindow(record->handle)) {
			SetWindowPlacement(record->handle, &record->placement);
			ShowWindow(record->handle, record->placement.showCmd);
		}
		--g_hidden_window_count;
	}
}


static PyObject *hide_process_windows(PyObject *unused_self,
		PyObject *unused_args)
{
	HideContext context;
	unsigned int previous_count = g_hidden_window_count;
	BOOL enumerated;
	(void)unused_self;
	(void)unused_args;

	context.process_id = GetCurrentProcessId();
	context.error_code = ERROR_SUCCESS;
	context.first_new_index = previous_count;
	SetLastError(ERROR_SUCCESS);
	enumerated = EnumWindows(hide_window_callback, (LPARAM)&context);
	if (!enumerated) {
		if (context.error_code == ERROR_SUCCESS) {
			context.error_code = GetLastError();
		}
		if (context.error_code == ERROR_SUCCESS) {
			context.error_code = ERROR_GEN_FAILURE;
		}
		restore_hidden_range(context.first_new_index);
		return python_int(-(long)context.error_code);
	}
	return python_int((long)(g_hidden_window_count - previous_count));
}


static PyObject *show_process_windows(PyObject *unused_self,
		PyObject *unused_args)
{
	unsigned int index;
	unsigned int remaining = 0;
	unsigned int restored = 0;
	DWORD process_id;
	DWORD current_process_id = GetCurrentProcessId();
	DWORD first_error = ERROR_SUCCESS;
	(void)unused_self;
	(void)unused_args;

	for (index = 0; index < g_hidden_window_count; ++index) {
		HiddenWindow *record = &g_hidden_windows[index];
		process_id = 0;
		if (!IsWindow(record->handle)) {
			continue;
		}
		GetWindowThreadProcessId(record->handle, &process_id);
		if (process_id != current_process_id) {
			continue;
		}
		if (!SetWindowPlacement(record->handle, &record->placement)) {
			if (first_error == ERROR_SUCCESS) {
				first_error = GetLastError();
				if (first_error == ERROR_SUCCESS) {
					first_error = ERROR_GEN_FAILURE;
				}
			}
			if (remaining != index) {
				g_hidden_windows[remaining] = *record;
			}
			++remaining;
			continue;
		}
		ShowWindow(record->handle, record->placement.showCmd);
		++restored;
	}
	/* Keep failed records so a later show call can retry recovery. */
	g_hidden_window_count = remaining;
	if (first_error != ERROR_SUCCESS) {
		return python_int(-(long)first_error);
	}
	return python_int((long)restored);
}


static PyMethodDef MODULE_METHODS[] = {
	{
		"release_client_guard", release_client_guard, METH_NOARGS,
		"Run #1513's complete WGC teardown for this client process."
	},
	{
		"apply_standard_gameplay_mask", apply_standard_gameplay_mask,
		METH_NOARGS,
		"Temporarily select standard CTF items for one geometry mapping."
	},
	{
		"restore_standard_gameplay_mask", restore_standard_gameplay_mask,
		METH_NOARGS,
		"Restore #1513's original all-gameplay geometry mapping mask."
	},
	{
		"hide_process_windows", hide_process_windows, METH_NOARGS,
		"Hide visible top-level windows owned by the current process."
	},
	{
		"show_process_windows", show_process_windows, METH_NOARGS,
		"Restore top-level windows previously hidden by this module."
	},
	{0, 0, 0, 0}
};


__declspec(dllexport) void __cdecl initoffline_instance_guard_native(void)
{
	PyInitModule4Fn init_module;
	unsigned char *base = (unsigned char *)GetModuleHandleW(0);
	if (base == 0 || !validate_host(base)) {
		return;
	}
	g_image_base = base;
	g_py_int_from_long =
		(PyIntFromLongFn)(g_image_base + RVA_PY_INT_FROM_LONG);
	init_module = (PyInitModule4Fn)(g_image_base + RVA_PY_INIT_MODULE4);
	init_module(
		"offline_instance_guard_native", MODULE_METHODS,
		"Exact-build Win32 bridge for offline LAN startup.", 0,
		PYTHON_API_VERSION_27);
}
