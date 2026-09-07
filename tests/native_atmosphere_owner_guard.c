/* Run with a 32-bit MinGW build on Windows. This exercises the production
 * installer and x86 thunk against a guarded stale pointer, without the game.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <assert.h>
#include <stdio.h>

static int protect_calls;
static int fail_protect_call;
static int fail_flush_once;

static BOOL test_protect(LPVOID address, SIZE_T size, DWORD protection,
		PDWORD previous)
{
	++protect_calls;
	if (protect_calls == fail_protect_call) {
		SetLastError(ERROR_ACCESS_DENIED);
		return FALSE;
	}
	return VirtualProtect(address, size, protection, previous);
}

static BOOL test_flush(HANDLE process, LPCVOID address, SIZE_T size)
{
	if (fail_flush_once) {
		fail_flush_once = 0;
		SetLastError(ERROR_ACCESS_DENIED);
		return FALSE;
	}
	return FlushInstructionCache(process, address, size);
}

#define VirtualProtect test_protect
#define FlushInstructionCache test_flush
#include "../native/offline_instance_guard_native.c"
#undef VirtualProtect
#undef FlushInstructionCache

static unsigned int stale_reads;
static unsigned char *update_stub;

static LONG CALLBACK expected_stale_read(EXCEPTION_POINTERS *exception)
{
	if (exception->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION &&
			exception->ContextRecord->Eip == (DWORD)(uintptr_t)(update_stub + 8)) {
		++stale_reads;
		/* Negative control: skip only the deliberately faulting stub read. */
		exception->ContextRecord->Eip = (DWORD)(uintptr_t)(update_stub + 13);
		return EXCEPTION_CONTINUE_EXECUTION;
	}
	return EXCEPTION_CONTINUE_SEARCH;
}

/* Call the exact patched CALL with its stock register contract. Return one
 * only when the nonvolatile registers and stack survive the complete call.
 */
static int __attribute__((naked)) invoke_update(
		void *site __attribute__((unused)),
		void *environment __attribute__((unused)),
		void *atmosphere __attribute__((unused)))
{
	__asm__(
		"pushl %ebp\n\tpushl %ebx\n\tpushl %esi\n\tpushl %edi\n\t"
		"movl $0x13579bdf, %ebp\n\tmovl $0x2468ace0, %ebx\n\t"
		"movl $0x12345678, %edi\n\tmovl 24(%esp), %esi\n\t"
		"movl 28(%esp), %ecx\n\tstc\n\tcall *20(%esp)\n\t"
		"xorl %eax, %eax\n\tcmpl $0x13579bdf, %ebp\n\tjne 1f\n\t"
		"cmpl $0x2468ace0, %ebx\n\tjne 1f\n\t"
		"cmpl $0x12345678, %edi\n\tjne 1f\n\t"
		"cmpl 24(%esp), %esi\n\tjne 1f\n\tincl %eax\n\t"
		"1: popl %edi\n\tpopl %esi\n\tpopl %ebx\n\tpopl %ebp\n\tret\n\t"
	);
}

static void write_code(void *address, const void *data, SIZE_T size)
{
	DWORD previous, unused;
	assert(VirtualProtect(address, size, PAGE_EXECUTE_READWRITE, &previous));
	CopyMemory(address, data, size);
	assert(FlushInstructionCache(GetCurrentProcess(), address, size));
	assert(VirtualProtect(address, size, previous, &unused));
}

static void assert_original(unsigned char *site)
{
	MEMORY_BASIC_INFORMATION info;
	assert(bytes_equal(site, ATMOSPHERE_TICK_SIGNATURE + 33U, 5U));
	assert(!g_atmosphere_owner_active);
	assert(VirtualQuery(site, &info, sizeof(info)) == sizeof(info));
	assert(info.Protect == PAGE_EXECUTE_READ);
}

int main(void)
{
	/* Save flags, consume settings through [ECX+0x10], publish a marker.
	 * The stale read is at byte 8, and byte 13 is RET.
	 */
	static const unsigned char stub[] = {
		0x9c, 0x5a, 0x89, 0x51, 0x4c,
		0x8b, 0x41, 0x10, 0x8b, 0x00, 0x89, 0x41, 0x48, 0xc3
	};
	unsigned char environment[0x520] = {0};
	unsigned char atmosphere[0xc0] = {0};
	unsigned char settings[2][0x124] = {{0}};
	unsigned char *site;
	void *stale;
	void *handler;
	DWORD previous;
	unsigned int index;
	unsigned char changed, original, ret = 0xc3;

	g_image_base = VirtualAlloc(0, EXPECTED_IMAGE_SIZE,
		MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
	assert(g_image_base != 0);
	/* The real initializer must reject this synthetic, unpinned host. */
	assert(!validate_host(g_image_base));
	CopyMemory(g_image_base + RVA_ENVIRO_TICK,
		ENVIRO_TICK_SIGNATURE, sizeof(ENVIRO_TICK_SIGNATURE));
	CopyMemory(g_image_base + RVA_ATMOSPHERE_TICK_SIGNATURE,
		ATMOSPHERE_TICK_SIGNATURE, sizeof(ATMOSPHERE_TICK_SIGNATURE));
	update_stub = g_image_base + RVA_ATMOSPHERE_UPDATE;
	CopyMemory(update_stub, ATMOSPHERE_UPDATE_SIGNATURE,
		sizeof(ATMOSPHERE_UPDATE_SIGNATURE));
	CopyMemory(g_image_base + RVA_ATMOSPHERE_TICK_SIGNATURE +
		sizeof(ATMOSPHERE_TICK_SIGNATURE), &ret, 1U);
	assert(VirtualProtect(g_image_base, EXPECTED_IMAGE_SIZE,
		PAGE_EXECUTE_READ, &previous));
	site = g_image_base + RVA_ATMOSPHERE_UPDATE_CALL;

	original = site[-1];
	changed = original ^ 1U;
	write_code(site - 1, &changed, 1U);
	assert(install_atmosphere_owner_guard_internal() ==
		ATMOSPHERE_STATUS_SIGNATURE_CHANGED);
	write_code(site - 1, &original, 1U);
	assert_original(site);

	protect_calls = 0;
	fail_protect_call = 1;
	assert(install_atmosphere_owner_guard_internal() ==
		ATMOSPHERE_STATUS_PROTECT_FAILED);
	assert_original(site);
	fail_protect_call = 0;
	fail_flush_once = 1;
	assert(install_atmosphere_owner_guard_internal() ==
		ATMOSPHERE_STATUS_FLUSH_FAILED);
	assert_original(site);
	protect_calls = 0;
	fail_protect_call = 2;
	assert(install_atmosphere_owner_guard_internal() ==
		ATMOSPHERE_STATUS_RESTORE_FAILED);
	assert_original(site);
	fail_protect_call = 0;

	stale = VirtualAlloc(0, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS);
	assert(stale != 0);
	*(void **)(environment + 0x510) = settings[0];
	*(void **)(atmosphere + 0x10) = stale;
	handler = AddVectoredExceptionHandler(1, expected_stale_read);
	assert(handler != 0);
	write_code(update_stub, stub, sizeof(stub));
	assert(invoke_update(site, environment, atmosphere) == 1);
	assert(stale_reads == 1);
	assert(*(unsigned int *)(atmosphere + 0x48) == 0);
	write_code(update_stub, ATMOSPHERE_UPDATE_SIGNATURE,
		sizeof(ATMOSPHERE_UPDATE_SIGNATURE));

	assert(install_atmosphere_owner_guard_internal() == 0);
	assert(install_atmosphere_owner_guard_internal() == 0);
	write_code(update_stub, stub, sizeof(stub));
	for (index = 0; index < 2U; ++index) {
		*(void **)(environment + 0x510) = settings[index];
		*(unsigned int *)settings[index] = 0x12340000U + index;
		settings[index][0xf4] = 1;
		*(void **)(atmosphere + 0x10) = stale;
		assert(invoke_update(site, environment, atmosphere) == 1);
		assert(stale_reads == 1);
		assert(*(void **)(atmosphere + 0x10) == settings[index]);
		assert(*(unsigned int *)(atmosphere + 0x48) == 0x12340000U + index);
		assert((*(unsigned int *)(atmosphere + 0x4c) & 1U) != 0);
		assert(settings[index][0xf4] == 0);
		/* Also cover an already correct binding, with no replacement. */
		assert(invoke_update(site, environment, atmosphere) == 1);
		assert(stale_reads == 1);
	}
	RemoveVectoredExceptionHandler(handler);
	VirtualFree(stale, 0, MEM_RELEASE);
	VirtualFree(g_image_base, 0, MEM_RELEASE);
	puts("PASS: stale read reproduced; owner rebound; x86 ABI, flags, dirty clear,"
		" replacement, repeat install, signature refusal and rollback verified");
	return 0;
}
