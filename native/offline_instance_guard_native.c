/*
 * Exact-build CPython 2.7 bridge for World of Tanks 0.9.22 #1513.
 *
 * The embedded interpreter does not ship _ctypes and does not export its
 * Python C API.  This module resolves the two required C API functions from
 * validated RVAs in the main executable, then exposes a deliberately small
 * native surface.  The gameplay-mapping opcode is changed only between one
 * Python apply/restore pair; WGC handles are never closed directly.  The
 * atmosphere owner repair lasts for the process, including lobby re-entry.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <tlhelp32.h>


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
#define RVA_ENVIRO_TICK 0x00636360U
#define RVA_ATMOSPHERE_TICK_SIGNATURE 0x006363f8U
#define RVA_ATMOSPHERE_UPDATE_CALL 0x00636419U
#define RVA_ATMOSPHERE_UPDATE 0x006854f0U

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

#define ATMOSPHERE_STATUS_SIGNATURE_CHANGED 201L
#define ATMOSPHERE_STATUS_PROTECT_FAILED 202L
#define ATMOSPHERE_STATUS_FLUSH_FAILED 203L
#define ATMOSPHERE_STATUS_RESTORE_FAILED 204L
#define ATMOSPHERE_STATUS_ROLLBACK_FAILED 205L

#define MAX_HIDDEN_WINDOWS 16U

#define EXCEPTION_TRAIL_PATH_ENV L"WOT_OFFLINE_EXCEPTION_TRAIL_PATH"
#define TRAIL_MAX_RECORDS 512L
#define TRAIL_MAX_FRAMES 24U
#define TRAIL_MAX_MODULES 256U
#define TRAIL_NAME_CHARS 40U
#define TRAIL_TEXT_CHARS 96U
#define TRAIL_BUFFER_BYTES 4096U

#define TRAIL_STATUS_NOT_CONFIGURED 301L
#define TRAIL_STATUS_PATH_INVALID 302L
#define TRAIL_STATUS_MODULES_UNAVAILABLE 303L
#define TRAIL_STATUS_TLS_UNAVAILABLE 304L
#define TRAIL_STATUS_HANDLER_REFUSED 305L


typedef struct HiddenWindow {
	HWND handle;
	WINDOWPLACEMENT placement;
} HiddenWindow;

typedef struct HideContext {
	DWORD process_id;
	DWORD error_code;
	unsigned int first_new_index;
} HideContext;

typedef struct TrailModule {
	uintptr_t base;
	uintptr_t end;
	int local;
	char name[TRAIL_NAME_CHARS];
} TrailModule;


static unsigned char *g_image_base = 0;
static PyIntFromLongFn g_py_int_from_long = 0;
static HiddenWindow g_hidden_windows[MAX_HIDDEN_WINDOWS];
static unsigned int g_hidden_window_count = 0;
static int g_mapping_mask_active = 0;
static DWORD g_mapping_original_protection = 0;
static int g_atmosphere_owner_active = 0;
static unsigned char g_atmosphere_call[5];
/* Referenced by the x86 tail jump below, outside the compiler's C analysis. */
static uintptr_t g_atmosphere_update_target __attribute__((used)) = 0;
static TrailModule g_trail_modules[TRAIL_MAX_MODULES];
static unsigned int g_trail_module_count = 0;
static HANDLE g_trail_file = INVALID_HANDLE_VALUE;
static DWORD g_trail_slot = TLS_OUT_OF_INDEXES;
static PVOID g_trail_handler = 0;
static volatile LONG g_trail_sequence = 0;
static volatile LONG g_trail_writing = 0;
static unsigned long g_trail_repeats = 0;
static DWORD g_trail_last_code = 0;
static uintptr_t g_trail_last_address = 0;
static LARGE_INTEGER g_trail_record_start;
static char g_trail_buffer[TRAIL_BUFFER_BYTES];

static const unsigned char ENVIRO_TICK_SIGNATURE[] = {
	0x55, 0x8b, 0xec, 0x83, 0xec, 0x0c, 0x56, 0x8b,
	0xf1, 0x8b, 0x86, 0x14, 0x05, 0x00, 0x00
};

/* ESI is the live EnviroMinder. Its settings dirty flag gates this call;
 * ECX is DeferredPipeline::AtmosphereSupport[+0x24]. The suffix clears the
 * same live settings flag only after the update returns.
 */
static const unsigned char ATMOSPHERE_TICK_SIGNATURE[] = {
	0x8b, 0x86, 0x10, 0x05, 0x00, 0x00, 0x80, 0xb8,
	0xf4, 0x00, 0x00, 0x00, 0x00, 0x74, 0x24, 0xe8,
	0x04, 0xb6, 0xf0, 0xff, 0x8b, 0xc8, 0x8b, 0x10,
	0xff, 0x92, 0x80, 0x00, 0x00, 0x00, 0x8b, 0x48,
	0x24, 0xe8, 0xd2, 0xf0, 0x04, 0x00, 0x8b, 0x86,
	0x10, 0x05, 0x00, 0x00, 0xc6, 0x80, 0xf4, 0x00,
	0x00, 0x00, 0x00
};

/* The stock update takes only ECX, saves nonvolatile registers and reads
 * its settings from [ECX+0x10]. No input value in EAX is consumed.
 */
static const unsigned char ATMOSPHERE_UPDATE_SIGNATURE[] = {
	0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0x48, 0x4f,
	0x41, 0x01, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00,
	0x50, 0x83, 0xec, 0x14, 0x53, 0x56, 0x57, 0xa1,
	0x70, 0x35, 0xce, 0x01, 0x33, 0xc5, 0x50, 0x8d,
	0x45, 0xf4, 0x64, 0xa3, 0x00, 0x00, 0x00, 0x00,
	0x8b, 0xf1, 0x89, 0x75, 0xf0, 0x8b, 0x5e, 0x10
};

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


/* #1513 binds this borrowed pointer during EnviroMinder::load(), but a new
 * environment can tick before load after the previous space was destroyed.
 * Rebind at the consumer to the live owner already used by this exact tick.
 * Never inspect or dereference the old pointer (it can be freed/reused).
 *
 * This replaces one CALL, not a function prologue. Tail-jumping preserves
 * its return address, ECX thiscall argument, flags and nonvolatile registers.
 * The extension stays loaded until process exit, so lobby rendering also
 * retains the repair after Python battle teardown.
 */
static void __attribute__((naked, used)) atmosphere_owner_thunk(void)
{
	__asm__(
		"movl 0x510(%esi), %eax\n\t"
		"movl %eax, 0x10(%ecx)\n\t"
		"jmp *_g_atmosphere_update_target\n\t"
	);
}


static long install_atmosphere_owner_guard_internal(void)
{
	unsigned char expected[sizeof(ATMOSPHERE_TICK_SIGNATURE)];
	unsigned char *site = g_image_base + RVA_ATMOSPHERE_UPDATE_CALL;
	unsigned char *signature = g_image_base + RVA_ATMOSPHERE_TICK_SIGNATURE;
	const unsigned int call_offset =
		RVA_ATMOSPHERE_UPDATE_CALL - RVA_ATMOSPHERE_TICK_SIGNATURE;
	DWORD original_protection = 0;
	DWORD unused_protection = 0;
	uint32_t displacement;
	int flushed;
	int restored;
	long status;

	CopyMemory(expected, ATMOSPHERE_TICK_SIGNATURE, sizeof(expected));
	if (g_atmosphere_owner_active) {
		CopyMemory(expected + call_offset, g_atmosphere_call, 5U);
	}
	if (!readable_region(signature, sizeof(expected)) ||
			!bytes_equal(signature, expected, sizeof(expected)) ||
			!readable_region(g_image_base + RVA_ENVIRO_TICK,
				sizeof(ENVIRO_TICK_SIGNATURE)) ||
			!bytes_equal(g_image_base + RVA_ENVIRO_TICK,
				ENVIRO_TICK_SIGNATURE, sizeof(ENVIRO_TICK_SIGNATURE)) ||
			!readable_region(g_image_base + RVA_ATMOSPHERE_UPDATE,
				sizeof(ATMOSPHERE_UPDATE_SIGNATURE)) ||
			!bytes_equal(g_image_base + RVA_ATMOSPHERE_UPDATE,
				ATMOSPHERE_UPDATE_SIGNATURE,
				sizeof(ATMOSPHERE_UPDATE_SIGNATURE))) {
		return ATMOSPHERE_STATUS_SIGNATURE_CHANGED;
	}
	if (g_atmosphere_owner_active) {
		return 0;
	}
	/* Installed by the client Python main thread before offline callbacks.
	 * That thread also owns the native tick, so it cannot execute a partly
	 * written CALL. No executable file on disk is modified.
	 */
	g_atmosphere_update_target =
		(uintptr_t)(g_image_base + RVA_ATMOSPHERE_UPDATE);
	g_atmosphere_call[0] = 0xe8U;
	displacement = (uint32_t)((uintptr_t)atmosphere_owner_thunk -
		(uintptr_t)(site + 5U));
	CopyMemory(g_atmosphere_call + 1, &displacement, sizeof(displacement));
	if (!VirtualProtect(site, 5U, PAGE_EXECUTE_READWRITE,
			&original_protection)) {
		return ATMOSPHERE_STATUS_PROTECT_FAILED;
	}
	CopyMemory(site, g_atmosphere_call, 5U);
	g_atmosphere_owner_active = 1;
	flushed = FlushInstructionCache(GetCurrentProcess(), site, 5U) != 0;
	restored = VirtualProtect(site, 5U, original_protection,
		&unused_protection) != 0;
	if (flushed && restored) {
		return 0;
	}
	status = flushed ? ATMOSPHERE_STATUS_RESTORE_FAILED :
		ATMOSPHERE_STATUS_FLUSH_FAILED;
	/* Roll back completely before reporting a recoverable installation error.
	 * If Windows refuses rollback, leave the live thunk/target allocated and
	 * report that distinct failure; never leave a CALL into released code.
	 */
	if (!VirtualProtect(site, 5U, PAGE_EXECUTE_READWRITE,
			&unused_protection)) {
		return ATMOSPHERE_STATUS_ROLLBACK_FAILED;
	}
	CopyMemory(site, ATMOSPHERE_TICK_SIGNATURE + call_offset, 5U);
	flushed = FlushInstructionCache(GetCurrentProcess(), site, 5U) != 0;
	restored = VirtualProtect(site, 5U, original_protection,
		&unused_protection) != 0;
	if (!flushed || !restored) {
		return ATMOSPHERE_STATUS_ROLLBACK_FAILED;
	}
	g_atmosphere_owner_active = 0;
	return status;
}


static PyObject *install_atmosphere_owner_guard(PyObject *unused_self,
		PyObject *unused_args)
{
	(void)unused_self;
	(void)unused_args;
	return python_int(install_atmosphere_owner_guard_internal());
}


/* First-chance exception trail.
 *
 * #1513 wraps its whole main loop in its own __try/__except, so an unhandled
 * exception is consumed by the engine's crash reporter, which formats its
 * message on the faulting stack and calls abort().  Nothing ever reaches
 * second chance, so ProcDump's -e trigger cannot fire and every collected
 * dump is a termination dump whose faulting thread has usually already left.
 *
 * A vectored handler runs before any frame-based handler, so it observes the
 * exception with the faulting thread's own registers and stack still intact.
 * It only records: it never changes the exception disposition, never touches
 * the context, and restores the thread's last-error value so first-chance
 * exceptions used as control flow behave exactly as before.
 */

static int trail_module_is_local(const WCHAR *name)
{
	return lstrcmpiW(name, L"WorldOfTanks.exe") == 0 ||
		lstrcmpiW(name, L"msvcp140.dll") == 0 ||
		lstrcmpiW(name, L"vcruntime140.dll") == 0;
}


static void trail_copy_name(char *destination, const WCHAR *source)
{
	unsigned int index = 0;
	while (index + 1U < TRAIL_NAME_CHARS && source[index] != L'\0') {
		WCHAR value = source[index];
		destination[index] = (value >= 0x20 && value < 0x7f) ?
			(char)value : '?';
		++index;
	}
	destination[index] = '\0';
}


/* Snapshot the module table once, while the caller is a normal Python call.
 * Resolving names inside the handler would take the loader lock, which the
 * faulting thread may already own.
 */
static int capture_trail_modules(void)
{
	MODULEENTRY32W entry;
	HANDLE snapshot = INVALID_HANDLE_VALUE;
	unsigned int attempt;
	unsigned int count = 0;

	for (attempt = 0; attempt < 4U; ++attempt) {
		snapshot = CreateToolhelp32Snapshot(
			TH32CS_SNAPMODULE, GetCurrentProcessId());
		if (snapshot != INVALID_HANDLE_VALUE) {
			break;
		}
		if (GetLastError() != ERROR_BAD_LENGTH) {
			return 0;
		}
	}
	if (snapshot == INVALID_HANDLE_VALUE) {
		return 0;
	}
	ZeroMemory(&entry, sizeof(entry));
	entry.dwSize = (DWORD)sizeof(entry);
	if (Module32FirstW(snapshot, &entry)) {
		do {
			TrailModule *record = &g_trail_modules[count];
			record->base = (uintptr_t)entry.modBaseAddr;
			record->end = record->base + (uintptr_t)entry.modBaseSize;
			record->local = trail_module_is_local(entry.szModule);
			trail_copy_name(record->name, entry.szModule);
			++count;
			entry.dwSize = (DWORD)sizeof(entry);
		} while (count < TRAIL_MAX_MODULES &&
			Module32NextW(snapshot, &entry));
	}
	CloseHandle(snapshot);
	g_trail_module_count = count;
	return count != 0;
}


static const TrailModule *trail_module_for(uintptr_t address)
{
	unsigned int index;
	for (index = 0; index < g_trail_module_count; ++index) {
		const TrailModule *record = &g_trail_modules[index];
		if (address >= record->base && address < record->end) {
			return record;
		}
	}
	return 0;
}


static unsigned int trail_put(unsigned int used, const char *text)
{
	while (*text != '\0' && used + 1U < TRAIL_BUFFER_BYTES) {
		g_trail_buffer[used++] = *text++;
	}
	return used;
}


static unsigned int trail_put_hex32(unsigned int used, unsigned long value)
{
	static const char DIGITS[] = "0123456789ABCDEF";
	unsigned int shift = 32U;
	while (shift > 0U && used + 1U < TRAIL_BUFFER_BYTES) {
		shift -= 4U;
		g_trail_buffer[used++] = DIGITS[(value >> shift) & 0xfU];
	}
	return used;
}


static unsigned int trail_put_uint(unsigned int used, unsigned long value,
		unsigned int width)
{
	char scratch[12];
	unsigned int count = 0;
	do {
		scratch[count++] = (char)('0' + (int)(value % 10UL));
		value /= 10UL;
	} while (value != 0UL && count < sizeof(scratch));
	while (width > count && used + 1U < TRAIL_BUFFER_BYTES) {
		g_trail_buffer[used++] = '0';
		--width;
	}
	while (count > 0U && used + 1U < TRAIL_BUFFER_BYTES) {
		g_trail_buffer[used++] = scratch[--count];
	}
	return used;
}


static unsigned int trail_put_address(unsigned int used, uintptr_t address)
{
	const TrailModule *module = trail_module_for(address);
	used = trail_put(used, "0x");
	used = trail_put_hex32(used, (unsigned long)address);
	if (module != 0) {
		used = trail_put(used, " (");
		used = trail_put(used, module->name);
		used = trail_put(used, "+0x");
		used = trail_put_hex32(used,
			(unsigned long)(address - module->base));
		used = trail_put(used, ")");
	}
	return used;
}


/* A C++ throw carries its ThrowInfo in the third parameter. Only exceptions
 * thrown by the exact client image or its C++ runtime are recorded; the
 * Chinese IME, the display driver and the WGC client all throw and catch
 * their own C++ exceptions during normal play.
 */
static int trail_cxx_is_local(const EXCEPTION_RECORD *record)
{
	const TrailModule *module;
	uintptr_t throw_info;
	if (record->NumberParameters < 3U ||
			(uintptr_t)record->ExceptionInformation[0] != 0x19930520U) {
		return 0;
	}
	throw_info = (uintptr_t)record->ExceptionInformation[2];
	if (throw_info == 0) {
		return 0;
	}
	module = trail_module_for(throw_info);
	return module != 0 && module->local;
}


static int trail_is_recorded(const EXCEPTION_RECORD *record)
{
	switch (record->ExceptionCode) {
	case 0xc0000005UL: /* access violation */
	case 0xc0000006UL: /* in-page error */
	case 0xc000001dUL: /* illegal instruction */
	case 0xc0000094UL: /* integer divide by zero */
	case 0xc0000096UL: /* privileged instruction */
	case 0xc00000fdUL: /* stack overflow */
	case 0xc0000374UL: /* heap corruption */
	case 0xc0000409UL: /* security check failure */
		return 1;
	case 0xe06d7363UL:
		return trail_cxx_is_local(record);
	default:
		return 0;
	}
}


/* Best effort only: a std::exception keeps its message pointer at +4. The
 * text is emitted only when every byte up to its terminator is printable, so
 * an object of another shape produces no field instead of an invented one.
 */
static int trail_exception_text(uintptr_t object, char *destination)
{
	const char *text;
	unsigned int span = TRAIL_TEXT_CHARS;
	unsigned int index;
	if (!readable_region((const void *)object, 8U)) {
		return 0;
	}
	if (!ReadProcessMemory(GetCurrentProcess(), (const void *)(object + 4U),
			&text, sizeof(text), 0)) {
		return 0;
	}
	if (text == 0) {
		return 0;
	}
	while (span > 8U && !readable_region(text, span)) {
		span /= 2U;
	}
	if (span <= 8U && !readable_region(text, span)) {
		return 0;
	}
	if (!ReadProcessMemory(GetCurrentProcess(), text, destination, span, 0)) {
		return 0;
	}
	for (index = 0; index + 1U < span; ++index) {
		char value = destination[index];
		if (value == '\0') {
			destination[index] = '\0';
			return index != 0U;
		}
		if (value < 0x20 || value >= 0x7f) {
			return 0;
		}
		destination[index] = value;
	}
	return 0;
}


/* The destination is opened once, while this is still a normal Python call.
 * Opening it from the handler would allocate on the process heap, which the
 * faulting thread may already hold locked - a heap corruption report is one
 * of the faults worth recording.
 */
static void trail_flush(unsigned int used)
{
	DWORD written = 0;
	if (used == 0U || g_trail_file == INVALID_HANDLE_VALUE) {
		return;
	}
	WriteFile(g_trail_file, g_trail_buffer, (DWORD)used, &written, 0);
}


static unsigned int trail_put_time(unsigned int used)
{
	SYSTEMTIME now;
	GetSystemTime(&now);
	used = trail_put_uint(used, now.wYear, 4U);
	used = trail_put(used, "-");
	used = trail_put_uint(used, now.wMonth, 2U);
	used = trail_put(used, "-");
	used = trail_put_uint(used, now.wDay, 2U);
	used = trail_put(used, "T");
	used = trail_put_uint(used, now.wHour, 2U);
	used = trail_put(used, ":");
	used = trail_put_uint(used, now.wMinute, 2U);
	used = trail_put(used, ":");
	used = trail_put_uint(used, now.wSecond, 2U);
	used = trail_put(used, ".");
	used = trail_put_uint(used, now.wMilliseconds, 3U);
	return trail_put(used, "Z");
}


static LONG CALLBACK exception_trail_handler(PEXCEPTION_POINTERS pointers)
{
	DWORD saved_error = GetLastError();
	const EXCEPTION_RECORD *record;
	const CONTEXT *context;
	char text[TRAIL_TEXT_CHARS];
	unsigned int used = 0;
	unsigned int frame;
	uintptr_t cursor;
	LONG sequence;
	int replace_record;
	LARGE_INTEGER zero;

	if (pointers == 0 || g_trail_slot == TLS_OUT_OF_INDEXES) {
		SetLastError(saved_error);
		return EXCEPTION_CONTINUE_SEARCH;
	}
	record = pointers->ExceptionRecord;
	context = pointers->ContextRecord;
	if (record == 0 || context == 0 || !trail_is_recorded(record) ||
			TlsGetValue(g_trail_slot) != 0) {
		SetLastError(saved_error);
		return EXCEPTION_CONTINUE_SEARCH;
	}
	/* One writer at a time, and never recursively: the shared buffer keeps
	 * this handler off the faulting stack, which a stack overflow has
	 * already exhausted, and serialises the repeat and sequence state.
	 */
	if (InterlockedCompareExchange(&g_trail_writing, 1, 0) != 0) {
		SetLastError(saved_error);
		return EXCEPTION_CONTINUE_SEARCH;
	}
	TlsSetValue(g_trail_slot, (LPVOID)(uintptr_t)1);

	/* RaiseException is shared by different C++ throws. Even a repeated
	 * code/address must refresh the final record's message and context.
	 * Keep the first 511 slots and overwrite the last slot after the budget
	 * fills, so bounded storage never disables the recorder before a crash.
	 */
	replace_record = g_trail_sequence != 0 &&
		record->ExceptionCode == g_trail_last_code &&
		(uintptr_t)record->ExceptionAddress == g_trail_last_address;
	if (replace_record) {
		++g_trail_repeats;
	} else {
		g_trail_repeats = 0;
		if (g_trail_sequence < TRAIL_MAX_RECORDS) {
			++g_trail_sequence;
		} else {
			replace_record = 1;
		}
	}
	zero.QuadPart = 0;
	if (!(replace_record ?
			SetFilePointerEx(g_trail_file, g_trail_record_start, 0, FILE_BEGIN) :
			SetFilePointerEx(g_trail_file, zero, &g_trail_record_start,
				FILE_CURRENT))) {
		TlsSetValue(g_trail_slot, 0);
		InterlockedExchange(&g_trail_writing, 0);
		SetLastError(saved_error);
		return EXCEPTION_CONTINUE_SEARCH;
	}
	sequence = g_trail_sequence;
	g_trail_last_code = record->ExceptionCode;
	g_trail_last_address = (uintptr_t)record->ExceptionAddress;

	used = trail_put(used, "EXC seq=");
	used = trail_put_uint(used, (unsigned long)sequence, 0U);
	used = trail_put(used, " time=");
	used = trail_put_time(used);
	used = trail_put(used, " tid=");
	used = trail_put_uint(used, GetCurrentThreadId(), 0U);
	used = trail_put(used, " code=0x");
	used = trail_put_hex32(used, record->ExceptionCode);
	used = trail_put(used, " flags=0x");
	used = trail_put_hex32(used, record->ExceptionFlags);
	used = trail_put(used, " at=");
	used = trail_put_address(used, (uintptr_t)record->ExceptionAddress);
	if (g_trail_repeats != 0UL) {
		used = trail_put(used, " repeats=");
		used = trail_put_uint(used, g_trail_repeats, 0U);
	}
	used = trail_put(used, "\r\n");

	if (record->ExceptionCode == 0xc0000005UL &&
			record->NumberParameters >= 2U) {
		used = trail_put(used, "EXC access kind=");
		used = trail_put_uint(used,
			(unsigned long)record->ExceptionInformation[0], 0U);
		used = trail_put(used, " address=0x");
		used = trail_put_hex32(used,
			(unsigned long)record->ExceptionInformation[1]);
		used = trail_put(used, "\r\n");
	} else if (record->ExceptionCode == 0xe06d7363UL) {
		uintptr_t object = (uintptr_t)record->ExceptionInformation[1];
		used = trail_put(used, "EXC cxx throwinfo=");
		used = trail_put_address(used,
			(uintptr_t)record->ExceptionInformation[2]);
		used = trail_put(used, " object=0x");
		used = trail_put_hex32(used, (unsigned long)object);
		if (trail_exception_text(object, text)) {
			used = trail_put(used, " what=\"");
			used = trail_put(used, text);
			used = trail_put(used, "\"");
		}
		used = trail_put(used, "\r\n");
	}

	used = trail_put(used, "EXC reg eip=0x");
	used = trail_put_hex32(used, context->Eip);
	used = trail_put(used, " esp=0x");
	used = trail_put_hex32(used, context->Esp);
	used = trail_put(used, " ebp=0x");
	used = trail_put_hex32(used, context->Ebp);
	used = trail_put(used, " eax=0x");
	used = trail_put_hex32(used, context->Eax);
	used = trail_put(used, " ebx=0x");
	used = trail_put_hex32(used, context->Ebx);
	used = trail_put(used, " ecx=0x");
	used = trail_put_hex32(used, context->Ecx);
	used = trail_put(used, " edx=0x");
	used = trail_put_hex32(used, context->Edx);
	used = trail_put(used, " esi=0x");
	used = trail_put_hex32(used, context->Esi);
	used = trail_put(used, " edi=0x");
	used = trail_put_hex32(used, context->Edi);
	used = trail_put(used, " efl=0x");
	used = trail_put_hex32(used, context->EFlags);
	used = trail_put(used, "\r\n");

	cursor = (uintptr_t)context->Ebp;
	for (frame = 0; frame < TRAIL_MAX_FRAMES; ++frame) {
		uintptr_t next;
		uintptr_t caller;
		uintptr_t pair[2];
		if ((cursor & 3U) != 0U ||
				!readable_region((const void *)cursor, 8U)) {
			break;
		}
		if (!ReadProcessMemory(GetCurrentProcess(), (const void *)cursor,
				pair, sizeof(pair), 0)) {
			break;
		}
		next = pair[0];
		caller = pair[1];
		if (caller == 0) {
			break;
		}
		used = trail_put(used, "EXC frame ");
		used = trail_put_uint(used, frame, 2U);
		used = trail_put(used, " ");
		used = trail_put_address(used, caller);
		used = trail_put(used, "\r\n");
		if (next <= cursor) {
			break;
		}
		cursor = next;
	}
	used = trail_put(used, "EXC end seq=");
	used = trail_put_uint(used, (unsigned long)sequence, 0U);
	used = trail_put(used, "\r\n");
	trail_flush(used);
	if (replace_record) {
		SetEndOfFile(g_trail_file);
	}

	TlsSetValue(g_trail_slot, 0);
	InterlockedExchange(&g_trail_writing, 0);
	SetLastError(saved_error);
	return EXCEPTION_CONTINUE_SEARCH;
}


static void write_trail_header(void)
{
	unsigned int used = 0;
	used = trail_put(used, "EXC session time=");
	used = trail_put_time(used);
	used = trail_put(used, " pid=");
	used = trail_put_uint(used, GetCurrentProcessId(), 0U);
	used = trail_put(used, " image=0x");
	used = trail_put_hex32(used, (unsigned long)(uintptr_t)g_image_base);
	used = trail_put(used, " modules=");
	used = trail_put_uint(used, g_trail_module_count, 0U);
	used = trail_put(used, " limit=");
	used = trail_put_uint(used, (unsigned long)TRAIL_MAX_RECORDS, 0U);
	used = trail_put(used, "\r\n");
	trail_flush(used);
}


static void close_trail_file(void)
{
	if (g_trail_file != INVALID_HANDLE_VALUE) {
		CloseHandle(g_trail_file);
		g_trail_file = INVALID_HANDLE_VALUE;
	}
}


static long install_exception_trail_internal(void)
{
	WCHAR path[MAX_PATH];
	DWORD length;
	LARGE_INTEGER zero;

	if (g_trail_handler != 0) {
		return 0;
	}
	length = GetEnvironmentVariableW(
		EXCEPTION_TRAIL_PATH_ENV, path, MAX_PATH);
	if (length == 0) {
		return TRAIL_STATUS_NOT_CONFIGURED;
	}
	if (length >= MAX_PATH) {
		return TRAIL_STATUS_PATH_INVALID;
	}
	if (!capture_trail_modules()) {
		return TRAIL_STATUS_MODULES_UNAVAILABLE;
	}
	if (g_trail_slot == TLS_OUT_OF_INDEXES) {
		g_trail_slot = TlsAlloc();
		if (g_trail_slot == TLS_OUT_OF_INDEXES) {
			return TRAIL_STATUS_TLS_UNAVAILABLE;
		}
	}
	/* Sharing the file keeps it readable while the process still runs, and
	 * a worker that restarts inside one launcher session appends instead of
	 * discarding the records that explain why it restarted.
	 */
	g_trail_file = CreateFileW(path, GENERIC_WRITE,
		FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, 0,
		OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
	if (g_trail_file == INVALID_HANDLE_VALUE) {
		return TRAIL_STATUS_PATH_INVALID;
	}
	zero.QuadPart = 0;
	if (!SetFilePointerEx(g_trail_file, zero, 0, FILE_END)) {
		close_trail_file();
		return TRAIL_STATUS_PATH_INVALID;
	}
	/* Finish using the shared buffer before another thread can enter. */
	write_trail_header();
	g_trail_handler =
		AddVectoredExceptionHandler(1UL, exception_trail_handler);
	if (g_trail_handler == 0) {
		close_trail_file();
		return TRAIL_STATUS_HANDLER_REFUSED;
	}
	return 0;
}


static PyObject *install_exception_trail(PyObject *unused_self,
		PyObject *unused_args)
{
	(void)unused_self;
	(void)unused_args;
	return python_int(install_exception_trail_internal());
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
		"install_atmosphere_owner_guard", install_atmosphere_owner_guard,
		METH_NOARGS,
		"Bind each #1513 atmosphere update to its live environment owner."
	},
	{
		"install_exception_trail", install_exception_trail, METH_NOARGS,
		"Record first-chance faults the #1513 crash reporter would consume."
	},
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
