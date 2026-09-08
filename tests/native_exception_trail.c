/* Run with a 32-bit MinGW build on Windows. This exercises the production
 * vectored handler, its module filter and its record format without the game.
 *
 * The trail exists because #1513 consumes its own unhandled exceptions, so
 * the only way to know what killed a process is to observe the fault at
 * first chance. That makes the handler's own behaviour the thing under test:
 * it must record the exceptions the engine hides, ignore the ones other
 * modules raise as control flow, and leave the thread exactly as it found it.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../native/offline_instance_guard_native.c"

#define PROBE_SENTINEL_ERROR 0x0badc0deUL

static char probe_trail[2 * 1024 * 1024];
static unsigned int probe_trail_length;


static volatile LONG probe_expecting;


/* Registered last, so the production handler observes every raise first.
 * Continuing execution lets RaiseException return to this probe instead of
 * ending the process, which is the only way to assert on a live handler.
 * It resumes only the deliberately raised codes, so a genuine fault in the
 * probe still terminates instead of looping.
 */
static LONG CALLBACK probe_resume(PEXCEPTION_POINTERS pointers)
{
	if (probe_expecting != 0 &&
			pointers->ExceptionRecord->ExceptionCode ==
				(DWORD)probe_expecting) {
		return EXCEPTION_CONTINUE_EXECUTION;
	}
	return EXCEPTION_CONTINUE_SEARCH;
}


static void probe_read_trail(const WCHAR *path)
{
	HANDLE file;
	DWORD read = 0;
	probe_trail_length = 0;
	probe_trail[0] = '\0';
	file = CreateFileW(path, GENERIC_READ,
		FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_EXISTING,
		FILE_ATTRIBUTE_NORMAL, 0);
	assert(file != INVALID_HANDLE_VALUE);
	assert(ReadFile(file, probe_trail, sizeof(probe_trail) - 1U, &read, 0));
	CloseHandle(file);
	probe_trail_length = (unsigned int)read;
	probe_trail[read] = '\0';
}


static unsigned int probe_count(const char *needle)
{
	const char *cursor = probe_trail;
	unsigned int total = 0;
	for (;;) {
		cursor = strstr(cursor, needle);
		if (cursor == 0) {
			return total;
		}
		++total;
		++cursor;
	}
}


static void probe_raise(DWORD code, DWORD count, const ULONG_PTR *parameters)
{
	InterlockedExchange(&probe_expecting, (LONG)code);
	RaiseException(code, 0, count, parameters);
	InterlockedExchange(&probe_expecting, 0);
}


/* Call the shipped handler directly for the two guarantees that a raise
 * through ntdll cannot isolate: it never changes the disposition, and it
 * leaves the thread's last-error value exactly as it found it. Calling it
 * directly is also the only way to vary the faulting address, which
 * RaiseException always reports as its own.
 */
static LONG probe_call_handler(DWORD code, uintptr_t address)
{
	EXCEPTION_RECORD record;
	EXCEPTION_POINTERS pointers;
	CONTEXT context;
	LONG disposition;

	ZeroMemory(&record, sizeof(record));
	ZeroMemory(&context, sizeof(context));
	RtlCaptureContext(&context);
	record.ExceptionCode = code;
	record.ExceptionAddress = (PVOID)address;
	record.NumberParameters = 2;
	record.ExceptionInformation[0] = 0;
	record.ExceptionInformation[1] = (ULONG_PTR)0xdeadbeefUL;
	pointers.ExceptionRecord = &record;
	pointers.ContextRecord = &context;

	SetLastError(PROBE_SENTINEL_ERROR);
	disposition = exception_trail_handler(&pointers);
	assert(GetLastError() == PROBE_SENTINEL_ERROR);
	return disposition;
}


static void probe_handler_is_transparent(void)
{
	assert(probe_call_handler(0xc0000005UL, 0x00401000U) ==
		EXCEPTION_CONTINUE_SEARCH);
	assert(probe_call_handler(0x40010006UL, 0x00402000U) ==
		EXCEPTION_CONTINUE_SEARCH);
}


static void probe_fill_records(void)
{
	unsigned int index;
	for (index = 0; index < (unsigned int)TRAIL_MAX_RECORDS + 8U; ++index) {
		assert(probe_call_handler(
			0xc0000005UL, 0x00500000U + (uintptr_t)index * 16U) ==
			EXCEPTION_CONTINUE_SEARCH);
	}
}


static void probe_mark_local(int local)
{
	uintptr_t self = (uintptr_t)GetModuleHandleW(0);
	unsigned int index;
	int found = 0;
	for (index = 0; index < g_trail_module_count; ++index) {
		if (g_trail_modules[index].base == self) {
			g_trail_modules[index].local = local;
			found = 1;
		}
	}
	assert(found);
}


int main(void)
{
	WCHAR directory[MAX_PATH];
	WCHAR path[MAX_PATH];
	PVOID resume;
	ULONG_PTR parameters[4];
	static const char message[] = "string too long";
	uintptr_t object[3];
	uintptr_t self;
	uintptr_t foreign;
	long status;
	unsigned int index;

	/* A failed assertion must end this probe with a non-zero exit code on
	 * an unattended runner, never with a dialog nobody can dismiss.
	 */
	SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);

	assert(GetTempPathW(MAX_PATH, directory) != 0);
	assert(GetTempFileNameW(directory, L"trl", 0, path) != 0);
	assert(DeleteFileW(path) || GetLastError() == ERROR_FILE_NOT_FOUND);
	assert(SetEnvironmentVariableW(EXCEPTION_TRAIL_PATH_ENV, path));

	/* The shipped module list must name the exact client and its C++ runtime
	 * and nothing else; every other module throws C++ exceptions in play.
	 */
	assert(trail_module_is_local(L"WorldOfTanks.exe"));
	assert(trail_module_is_local(L"worldoftanks.exe"));
	assert(trail_module_is_local(L"msvcp140.dll"));
	assert(trail_module_is_local(L"vcruntime140.dll"));
	assert(!trail_module_is_local(L"SogouPY.ime"));
	assert(!trail_module_is_local(L"nvgpucomp32.dll"));
	assert(!trail_module_is_local(L"wgc_api.dll"));

	g_image_base = (unsigned char *)GetModuleHandleW(0);
	status = install_exception_trail_internal();
	assert(status == 0);
	assert(g_trail_handler != 0);
	assert(g_trail_module_count != 0);
	/* Idempotent: a second Python call must not stack handlers. */
	assert(install_exception_trail_internal() == 0);

	resume = AddVectoredExceptionHandler(0UL, probe_resume);
	assert(resume != 0);

	self = (uintptr_t)GetModuleHandleW(0);
	foreign = (uintptr_t)GetModuleHandleW(L"ntdll.dll");
	assert(foreign != 0);
	assert(trail_module_for(self) != 0);
	assert(trail_module_for(foreign) != 0);

	/* A throw from a module that is not the client or its runtime is the
	 * common case in a real session and must leave no record.
	 */
	probe_mark_local(0);
	object[0] = (uintptr_t)0;
	object[1] = (uintptr_t)message;
	object[2] = 1;
	parameters[0] = (ULONG_PTR)0x19930520U;
	parameters[1] = (ULONG_PTR)object;
	parameters[2] = (ULONG_PTR)foreign;
	probe_raise(0xe06d7363UL, 3, parameters);
	parameters[2] = (ULONG_PTR)self;
	probe_raise(0xe06d7363UL, 3, parameters);

	probe_read_trail(path);
	assert(probe_count("EXC session ") == 1);
	assert(probe_count("EXC seq=") == 0);

	/* The same throw from the client image is what the engine hides. */
	probe_mark_local(1);
	parameters[2] = (ULONG_PTR)self;
	probe_raise(0xe06d7363UL, 3, parameters);

	probe_read_trail(path);
	assert(probe_count("EXC seq=1 ") == 1);
	assert(probe_count("EXC cxx throwinfo=") == 1);
	assert(probe_count("what=\"string too long\"") == 1);
	assert(probe_count("EXC end seq=1") == 1);
	assert(probe_count("EXC reg eip=0x") == 1);
	assert(probe_count("EXC frame 00 ") == 1);

	/* All C++ throws share RaiseException's address. Keep the latest
	 * message even when the type/address match an earlier caught throw.
	 */
	object[1] = (uintptr_t)"late failure";
	probe_raise(0xe06d7363UL, 3, parameters);
	probe_read_trail(path);
	assert(probe_count("what=\"late failure\"") == 1);
	assert(probe_count("what=\"string too long\"") == 0);
	assert(probe_count("EXC end seq=") == 1);

	/* A rethrow carries no ThrowInfo and names no type; it must be ignored
	 * rather than recorded as an unattributed fault.
	 */
	parameters[2] = 0;
	probe_raise(0xe06d7363UL, 3, parameters);
	/* A foreign magic value is not the MSVC C++ protocol at all. */
	parameters[0] = (ULONG_PTR)0x19930521U;
	parameters[2] = (ULONG_PTR)self;
	probe_raise(0xe06d7363UL, 3, parameters);
	probe_read_trail(path);
	assert(probe_count("EXC seq=2 ") == 0);

	/* An access violation is recorded whatever module raised it, with the
	 * operation and target the crash reporter never writes down.
	 */
	parameters[0] = 0;
	parameters[1] = (ULONG_PTR)0xdeadbeefUL;
	probe_raise(0xc0000005UL, 2, parameters);
	probe_read_trail(path);
	assert(probe_count("EXC seq=2 ") == 1);
	assert(probe_count("code=0xC0000005") == 1);
	assert(probe_count("EXC access kind=0 address=0xDEADBEEF") == 1);

	/* Codes the engine handles as control flow stay out of the trail. */
	probe_raise(0x40010006UL, 0, 0);
	probe_raise(0x406d1388UL, 0, 0);
	probe_read_trail(path);
	assert(probe_count("EXC seq=3 ") == 0);

	/* Repeated addresses must retain the latest context, even when no
	 * distinct exception follows before the engine aborts.
	 */
	for (index = 0; index < 5U; ++index) {
		parameters[0] = 0;
		parameters[1] = (ULONG_PTR)0xdeadbeefUL;
		probe_raise(0xc0000005UL, 2, parameters);
	}
	probe_read_trail(path);
	assert(probe_count("EXC seq=3 ") == 0);
	assert(probe_count("repeats=5") == 1);

	probe_handler_is_transparent();

	probe_read_trail(path);
	assert(probe_count("EXC seq=3 ") == 1);
	assert(probe_count("repeats=5") == 1);

	/* The limit bounds a process that keeps producing distinct faults. */
	probe_fill_records();
	probe_read_trail(path);
	assert(probe_count("EXC end seq=") == (unsigned int)TRAIL_MAX_RECORDS);
	assert(probe_count("at=0x00502070") == 1);
	assert(probe_trail_length < sizeof(probe_trail));

	assert(RemoveVectoredExceptionHandler(resume) != 0);
	assert(DeleteFileW(path));
	printf("native exception trail probe passed\n");
	return 0;
}
