/*
 * Windowless starter for the exact World of Tanks 0.9.22 #1513 clients.
 *
 * #1513 hard-codes ShowWindow(SW_SHOW) for its main HWND, so STARTUPINFO's
 * wShowWindow cannot suppress the first frame.  Starting it on a private,
 * never-switched desktop keeps that HWND and every child window off the
 * player's desktop without patching or copying the client.
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <strsafe.h>
#include <wchar.h>


#define WORKER_MUTEX_NAME L"Local\\offline_lan_0922_worker"
#define WORKER_MODE_ENV L"OFFLINE_LAN_0922_CLIENT_MODE"
#define WORKER_MODE_VALUE L"simulation_worker"
#define PLAYER_MODE_VALUE L"player"
#define MULTI_CLIENT_ENV L"OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS"
#define MULTI_CLIENT_VALUE L"1"
#define HIDDEN_DESKTOP_ENV L"OFFLINE_LAN_0922_HIDDEN_DESKTOP"
#define HIDDEN_DESKTOP_VALUE L"1"
#define WORKER_READY_MARKER_ENV L"OFFLINE_LAN_0922_WORKER_READY_MARKER"
#define WORKER_INTERNAL_READY_MARKER_ENV \
	L"OFFLINE_LAN_0922_WORKER_INTERNAL_READY_MARKER"
#define PLAYER_READY_MARKER_ENV L"OFFLINE_LAN_0922_PLAYER_READY_MARKER"
#define WORKER_READY_MARKER_FILE L"offline-worker.ready"
#define WORKER_INTERNAL_READY_MARKER_FILE L"offline-worker.internal-ready"
#define PLAYER_READY_MARKER_FORMAT L"offline-player-%lu.ready"
#define SERVER_HOST_ENV L"OFFLINE_LAN_0922_SERVER_HOST"
#define SERVER_PORT_ENV L"OFFLINE_LAN_0922_SERVER_PORT"
#define PLAYER_MODE L"--player"
#define PAIRED_PLAYER_MODE L"--paired-player"
#define WORKER_ONLY_MODE L"--worker-only"
#define WORKER_READY_TIMEOUT_MS 60000
#define WORKER_READY_POLL_MS 50
#define PLAYER_HANDOFF_GRACE_MS 10000
#define PLAYER_HANDOFF_POLL_MS 100
#define MAX_GAME_PROCESS_IDS 32
#define PROCDUMP_PATH_ENV L"WOT_OFFLINE_PROCDUMP_PATH"
#define CRASH_DUMP_PATH_ENV L"WOT_OFFLINE_CRASH_DUMP_PATH"
#define CRASH_DUMP_MODE_ENV L"WOT_OFFLINE_CRASH_DUMP_MODE"
#define PROCDUMP_ATTACH_TIMEOUT_MS 10000
#define PROCDUMP_ATTACH_POLL_MS 25
#define PROCDUMP_FINISH_TIMEOUT_MS 20000
#define PROCDUMP_CANCEL_TIMEOUT_MS 3000
#define TARGET_STOP_TIMEOUT_MS 6000
#define STARTER_STOP_COMMAND L"--stop-starter "
#define STARTER_STOP_EVENT_PREFIX L"Local\\WoTOfflineBattlesStarterStop_"


static WCHAR g_root[MAX_PATH];
static WCHAR g_ready_marker[MAX_PATH];
static WCHAR g_internal_ready_marker[MAX_PATH];


typedef struct JobProcessSet {
	DWORD assigned_count;
	DWORD count;
	ULONG_PTR ids[MAX_GAME_PROCESS_IDS];
} JobProcessSet;


typedef struct TrackedGameProcess {
	DWORD id;
	HANDLE process;
	HANDLE procdump_process;
	DWORD exit_code;
	FILETIME exit_time;
	WCHAR dump_path[MAX_PATH];
	BOOL procdump_attempted;
	BOOL exited;
	BOOL dump_complete;
} TrackedGameProcess;


typedef struct PlayerProcessTracker {
	DWORD count;
	TrackedGameProcess processes[MAX_GAME_PROCESS_IDS];
	WCHAR procdump_path[MAX_PATH];
	WCHAR final_dump_path[MAX_PATH];
	int last_exit_index;
	BOOL procdump_configured;
} PlayerProcessTracker;


static void log_status(const char *stage, const char *field, DWORD value)
{
	WCHAR log_path[MAX_PATH];
	char message[256];
	DWORD written = 0;
	HANDLE file;
	if (FAILED(StringCchCopyW(log_path, MAX_PATH, g_root)) ||
			FAILED(StringCchCatW(log_path, MAX_PATH,
				L"offline-worker-starter.log"))) {
		return;
	}
	file = CreateFileW(log_path, FILE_APPEND_DATA,
		FILE_SHARE_READ | FILE_SHARE_WRITE, 0,
		OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
	if (file == INVALID_HANDLE_VALUE) {
		return;
	}
	if (FAILED(StringCchPrintfA(message, 256,
			"stage=%s %s=%lu\r\n", stage, field,
			(unsigned long)value))) {
		CloseHandle(file);
		return;
	}
	WriteFile(file, message, (DWORD)lstrlenA(message), &written, 0);
	CloseHandle(file);
}


static void log_failure(const char *stage, DWORD error_code)
{
	log_status(stage, "win32_error", error_code);
}


static void log_process_exit(const char *stage, DWORD exit_code)
{
	log_status(stage, "exit_code", exit_code);
}


static void clear_failure_log(void)
{
	WCHAR log_path[MAX_PATH];
	if (SUCCEEDED(StringCchCopyW(log_path, MAX_PATH, g_root)) &&
			SUCCEEDED(StringCchCatW(
				log_path, MAX_PATH, L"offline-worker-starter.log"))) {
		DeleteFileW(log_path);
	}
}


static int resolve_game_root(WCHAR *game_path, size_t game_path_count)
{
	WCHAR starter_path[MAX_PATH];
	DWORD length;
	int index;
	length = GetModuleFileNameW(0, starter_path, MAX_PATH);
	if (length == 0 || length >= MAX_PATH) {
		return 0;
	}
	for (index = (int)length - 1; index >= 0; --index) {
		if (starter_path[index] == L'\\' || starter_path[index] == L'/') {
			starter_path[index + 1] = L'\0';
			break;
		}
	}
	if (index < 0 || FAILED(StringCchCopyW(g_root, MAX_PATH,
			starter_path)) || FAILED(StringCchCopyW(game_path,
			game_path_count, starter_path)) ||
			FAILED(StringCchCatW(game_path, game_path_count,
				L"WorldOfTanks.exe"))) {
		return 0;
	}
	return GetFileAttributesW(game_path) != INVALID_FILE_ATTRIBUTES;
}


static int configure_kill_job(HANDLE job)
{
	JOBOBJECT_EXTENDED_LIMIT_INFORMATION info;
	ZeroMemory(&info, sizeof(info));
	info.BasicLimitInformation.LimitFlags =
		JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
	return SetInformationJobObject(job, JobObjectExtendedLimitInformation,
		&info, sizeof(info)) != FALSE;
}


static void terminate_process_bounded(HANDLE process, DWORD exit_code,
		DWORD timeout_ms)
{
	if (process == 0) {
		return;
	}
	TerminateProcess(process, exit_code);
	if (WaitForSingleObject(process, timeout_ms) == WAIT_TIMEOUT) {
		log_failure("TerminateProcess timeout", WAIT_TIMEOUT);
	}
}


static int close_finished_procdump(HANDLE *procdump_process,
		BOOL *completed)
{
	DWORD exit_code;
	DWORD wait_state;
	if (completed != 0) {
		*completed = FALSE;
	}
	if (procdump_process == 0 || *procdump_process == 0) {
		return 1;
	}
	wait_state = WaitForSingleObject(*procdump_process, 0);
	if (wait_state == WAIT_TIMEOUT) {
		return 0;
	}
	if (wait_state == WAIT_FAILED) {
		log_failure("WaitForSingleObject(procdump)", GetLastError());
	} else if (!GetExitCodeProcess(*procdump_process, &exit_code)) {
		log_failure("GetExitCodeProcess(procdump)", GetLastError());
	} else {
		/* ProcDump uses non-zero process exit codes after successfully
		 * handling some triggers (v12.01 returns -2 for a termination dump).
		 * The exact fixed output file is validated before promotion below, so
		 * process completion -- not ProcDump's status code -- is the reliable
		 * boundary here. */
		if (completed != 0) {
			*completed = TRUE;
		}
	}
	CloseHandle(*procdump_process);
	*procdump_process = 0;
	return 1;
}


static HANDLE start_procdump_cancel(const WCHAR *procdump_path,
		DWORD target_process_id)
{
	WCHAR command[2 * MAX_PATH];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	if (FAILED(StringCchPrintfW(command, 2 * MAX_PATH,
			L"\"%s\" -accepteula -cancel %lu", procdump_path,
			(unsigned long)target_process_id))) {
		return 0;
	}
	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	startup.dwFlags = STARTF_USESHOWWINDOW;
	startup.wShowWindow = SW_HIDE;
	ZeroMemory(&process, sizeof(process));
	if (!CreateProcessW(procdump_path, command, 0, 0, FALSE,
			CREATE_NO_WINDOW, 0, g_root, &startup, &process)) {
		log_failure("CreateProcessW(procdump cancel)", GetLastError());
		return 0;
	}
	CloseHandle(process.hThread);
	return process.hProcess;
}


static BOOL wait_for_procdump(HANDLE *procdump_process,
		const WCHAR *procdump_path, DWORD target_process_id)
{
	HANDLE cancel_process;
	DWORD wait_state;
	BOOL completed = FALSE;
	BOOL timed_out = FALSE;
	if (procdump_process == 0 || *procdump_process == 0) {
		return FALSE;
	}
	wait_state = WaitForSingleObject(
		*procdump_process, PROCDUMP_FINISH_TIMEOUT_MS);
	if (wait_state == WAIT_TIMEOUT) {
		timed_out = TRUE;
		log_failure("procdump_finish_timeout", WAIT_TIMEOUT);
		cancel_process = start_procdump_cancel(
			procdump_path, target_process_id);
		if (cancel_process != 0) {
			wait_state = WaitForSingleObject(
				cancel_process, PROCDUMP_CANCEL_TIMEOUT_MS);
			if (wait_state == WAIT_TIMEOUT) {
				terminate_process_bounded(cancel_process, ERROR_TIMEOUT,
					PROCDUMP_CANCEL_TIMEOUT_MS);
			}
			CloseHandle(cancel_process);
		}
		wait_state = WaitForSingleObject(
			*procdump_process, PROCDUMP_CANCEL_TIMEOUT_MS);
		if (wait_state == WAIT_TIMEOUT) {
			terminate_process_bounded(*procdump_process, ERROR_TIMEOUT,
				PROCDUMP_CANCEL_TIMEOUT_MS);
		}
	}
	(void)close_finished_procdump(procdump_process, &completed);
	if (*procdump_process != 0) {
		CloseHandle(*procdump_process);
		*procdump_process = 0;
	}
	return !timed_out && completed;
}


static void cancel_procdump_now(HANDLE *procdump_process,
		const WCHAR *procdump_path, DWORD target_process_id)
{
	HANDLE cancel_process;
	DWORD wait_state;
	BOOL unused_completed;
	if (procdump_process == 0 || *procdump_process == 0 ||
			close_finished_procdump(
				procdump_process, &unused_completed)) {
		return;
	}
	cancel_process = start_procdump_cancel(
		procdump_path, target_process_id);
	if (cancel_process != 0) {
		wait_state = WaitForSingleObject(
			cancel_process, PROCDUMP_CANCEL_TIMEOUT_MS);
		if (wait_state == WAIT_TIMEOUT) {
			terminate_process_bounded(cancel_process, ERROR_TIMEOUT,
				PROCDUMP_CANCEL_TIMEOUT_MS);
		}
		CloseHandle(cancel_process);
	}
	wait_state = WaitForSingleObject(
		*procdump_process, PROCDUMP_CANCEL_TIMEOUT_MS);
	if (wait_state == WAIT_TIMEOUT) {
		terminate_process_bounded(*procdump_process, ERROR_TIMEOUT,
			PROCDUMP_CANCEL_TIMEOUT_MS);
	}
	(void)close_finished_procdump(
		procdump_process, &unused_completed);
	if (*procdump_process != 0) {
		CloseHandle(*procdump_process);
		*procdump_process = 0;
	}
}


static int load_procdump_configuration(WCHAR *procdump_path,
		size_t procdump_path_count, WCHAR *dump_path, size_t dump_path_count)
{
	DWORD procdump_path_length;
	DWORD dump_path_length;
	procdump_path_length = GetEnvironmentVariableW(
		PROCDUMP_PATH_ENV, procdump_path, (DWORD)procdump_path_count);
	dump_path_length = GetEnvironmentVariableW(
		CRASH_DUMP_PATH_ENV, dump_path, (DWORD)dump_path_count);
	if (procdump_path_length == 0 && dump_path_length == 0) {
		return 0;
	}
	if (procdump_path_length == 0 || dump_path_length == 0) {
		log_failure("procdump_environment", ERROR_ENVVAR_NOT_FOUND);
		return -1;
	}
	if (procdump_path_length >= procdump_path_count ||
			dump_path_length >= dump_path_count) {
		log_failure("procdump_environment", ERROR_INSUFFICIENT_BUFFER);
		return -1;
	}
	if (GetFileAttributesW(procdump_path) == INVALID_FILE_ATTRIBUTES) {
		log_failure("procdump_missing", GetLastError());
		return -1;
	}
	return 1;
}


static int monitor_dump_path(const WCHAR *final_dump_path, DWORD slot,
		WCHAR *path, size_t path_count)
{
	WCHAR suffix[48];
	size_t length = lstrlenW(final_dump_path);
	if (slot >= MAX_GAME_PROCESS_IDS ||
			FAILED(StringCchCopyW(path, path_count, final_dump_path))) {
		return 0;
	}
	if (length >= 4 &&
			lstrcmpiW(final_dump_path + length - 4, L".dmp") == 0) {
		path[length - 4] = L'\0';
	}
	if (FAILED(StringCchPrintfW(suffix, 48,
			L".monitor-%02lu.tmp.dmp", (unsigned long)slot)) ||
			FAILED(StringCchCatW(path, path_count, suffix))) {
		return 0;
	}
	return 1;
}


static void cleanup_monitor_dump_slots(const WCHAR *final_dump_path)
{
	WCHAR path[MAX_PATH];
	DWORD slot;
	for (slot = 0; slot < MAX_GAME_PROCESS_IDS; ++slot) {
		if (monitor_dump_path(
				final_dump_path, slot, path, MAX_PATH)) {
			DeleteFileW(path);
		}
	}
}


static int complete_regular_dump_file(const WCHAR *path)
{
	DWORD attributes;
	HANDLE file;
	LARGE_INTEGER size;
	attributes = GetFileAttributesW(path);
	if (attributes == INVALID_FILE_ATTRIBUTES ||
			(attributes & (FILE_ATTRIBUTE_DIRECTORY |
				FILE_ATTRIBUTE_REPARSE_POINT)) != 0) {
		return 0;
	}
	file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, 0,
		OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, 0);
	if (file == INVALID_HANDLE_VALUE) {
		return 0;
	}
	if (!GetFileSizeEx(file, &size)) {
		CloseHandle(file);
		return 0;
	}
	CloseHandle(file);
	return size.QuadPart > 0;
}


static HANDLE start_procdump_configured(HANDLE target_process,
		DWORD target_process_id, const WCHAR *procdump_path,
		const WCHAR *dump_path)
{
	WCHAR command[4 * MAX_PATH];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	DWORD elapsed = 0;
	DWORD exit_code;
	DWORD wait_state;
	BOOL debugger_present = FALSE;
	WCHAR dump_mode[16];
	const WCHAR *dump_option = L"-mm";
	if (GetEnvironmentVariableW(
			CRASH_DUMP_MODE_ENV, dump_mode, 16) > 0 &&
			lstrcmpiW(dump_mode, L"full") == 0) {
		dump_option = L"-ma";
	}
	if (FAILED(StringCchPrintfW(command, 4 * MAX_PATH,
			L"\"%s\" -accepteula %s -n 1 -e -t %lu \"%s\"",
			procdump_path, dump_option,
			(unsigned long)target_process_id, dump_path))) {
		log_failure("procdump_command", ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}

	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	startup.dwFlags = STARTF_USESHOWWINDOW;
	startup.wShowWindow = SW_HIDE;
	ZeroMemory(&process, sizeof(process));
	if (!CreateProcessW(procdump_path, command, 0, 0, FALSE,
			CREATE_NO_WINDOW, 0, g_root, &startup, &process)) {
		log_failure("CreateProcessW(procdump)", GetLastError());
		return 0;
	}
	CloseHandle(process.hThread);

	while (elapsed <= PROCDUMP_ATTACH_TIMEOUT_MS) {
		wait_state = WaitForSingleObject(process.hProcess, 0);
		if (wait_state != WAIT_TIMEOUT) {
			if (wait_state == WAIT_FAILED) {
				log_failure(
					"WaitForSingleObject(procdump attach)",
					GetLastError());
			} else if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
				log_failure(
					"GetExitCodeProcess(procdump attach)",
					GetLastError());
			} else {
				log_process_exit(
					"procdump_exited_before_attach", exit_code);
			}
			CloseHandle(process.hProcess);
			return 0;
		}
		if (!CheckRemoteDebuggerPresent(
				target_process, &debugger_present)) {
			log_failure("CheckRemoteDebuggerPresent", GetLastError());
			terminate_process_bounded(process.hProcess,
				ERROR_PROCESS_ABORTED, PROCDUMP_CANCEL_TIMEOUT_MS);
			CloseHandle(process.hProcess);
			return 0;
		}
		if (debugger_present) {
			return process.hProcess;
		}
		if (elapsed == PROCDUMP_ATTACH_TIMEOUT_MS) {
			break;
		}
		Sleep(PROCDUMP_ATTACH_POLL_MS);
		elapsed += PROCDUMP_ATTACH_POLL_MS;
	}

	log_failure("procdump_attach_timeout", WAIT_TIMEOUT);
	terminate_process_bounded(process.hProcess, ERROR_TIMEOUT,
		PROCDUMP_CANCEL_TIMEOUT_MS);
	CloseHandle(process.hProcess);
	return 0;
}


static int starter_stop_event_name(DWORD process_id, WCHAR *name,
		size_t name_count)
{
	return SUCCEEDED(StringCchPrintfW(name, name_count, L"%s%lu",
		STARTER_STOP_EVENT_PREFIX, (unsigned long)process_id));
}


static int parse_starter_stop_command(const WCHAR *command_line,
		DWORD *process_id)
{
	const WCHAR *cursor;
	DWORD value = 0;
	size_t prefix_length = lstrlenW(STARTER_STOP_COMMAND);
	if (wcsncmp(command_line, STARTER_STOP_COMMAND, prefix_length) != 0) {
		return 0;
	}
	cursor = command_line + prefix_length;
	if (*cursor == L'\0') {
		return -1;
	}
	while (*cursor != L'\0') {
		DWORD digit;
		if (*cursor < L'0' || *cursor > L'9') {
			return -1;
		}
		digit = (DWORD)(*cursor - L'0');
		if (value > (MAXDWORD - digit) / 10) {
			return -1;
		}
		value = value * 10 + digit;
		++cursor;
	}
	if (value == 0) {
		return -1;
	}
	*process_id = value;
	return 1;
}


static int signal_starter_stop(DWORD process_id)
{
	WCHAR event_name[96];
	HANDLE stop_event;
	DWORD error_code;
	if (!starter_stop_event_name(process_id, event_name, 96)) {
		return 26;
	}
	stop_event = OpenEventW(EVENT_MODIFY_STATE, FALSE, event_name);
	if (stop_event == 0) {
		return 27;
	}
	if (!SetEvent(stop_event)) {
		error_code = GetLastError();
		CloseHandle(stop_event);
		SetLastError(error_code);
		return 28;
	}
	CloseHandle(stop_event);
	return 0;
}


static HANDLE create_starter_stop_event(void)
{
	WCHAR event_name[96];
	if (!starter_stop_event_name(GetCurrentProcessId(), event_name, 96)) {
		SetLastError(ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}
	return CreateEventW(0, TRUE, FALSE, event_name);
}


static int configure_ready_markers(void)
{
	if (FAILED(StringCchCopyW(g_ready_marker, MAX_PATH, g_root)) ||
			FAILED(StringCchCatW(g_ready_marker, MAX_PATH,
				WORKER_READY_MARKER_FILE)) ||
			FAILED(StringCchCopyW(
				g_internal_ready_marker, MAX_PATH, g_root)) ||
			FAILED(StringCchCatW(
				g_internal_ready_marker, MAX_PATH,
				WORKER_INTERNAL_READY_MARKER_FILE))) {
		return 0;
	}
	return 1;
}


static int configure_player_ready_marker(WCHAR *path, size_t path_count)
{
	return SUCCEEDED(StringCchPrintfW(path, path_count,
		L"%s" PLAYER_READY_MARKER_FORMAT, g_root,
		(unsigned long)GetCurrentProcessId()));
}


static int remove_marker_path(const WCHAR *marker_path)
{
	WCHAR temporary_path[MAX_PATH];
	DWORD error_code;
	if (!DeleteFileW(marker_path)) {
		error_code = GetLastError();
		if (error_code != ERROR_FILE_NOT_FOUND &&
				error_code != ERROR_PATH_NOT_FOUND) {
			SetLastError(error_code);
			return 0;
		}
	}
	if (FAILED(StringCchCopyW(temporary_path, MAX_PATH, marker_path)) ||
			FAILED(StringCchCatW(temporary_path, MAX_PATH, L".tmp"))) {
		SetLastError(ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}
	if (!DeleteFileW(temporary_path)) {
		error_code = GetLastError();
		if (error_code != ERROR_FILE_NOT_FOUND &&
				error_code != ERROR_PATH_NOT_FOUND) {
			SetLastError(error_code);
			return 0;
		}
	}
	return 1;
}


static int remove_ready_markers(void)
{
	return remove_marker_path(g_ready_marker) &&
		remove_marker_path(g_internal_ready_marker);
}


static int publish_ready_marker(const WCHAR *marker_path)
{
	static const char payload[] = "ready\n";
	WCHAR temporary_path[MAX_PATH];
	HANDLE file;
	DWORD written = 0;
	DWORD error_code;
	if (FAILED(StringCchCopyW(temporary_path, MAX_PATH, marker_path)) ||
			FAILED(StringCchCatW(temporary_path, MAX_PATH, L".tmp"))) {
		SetLastError(ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}
	file = CreateFileW(temporary_path, GENERIC_WRITE, FILE_SHARE_READ, 0,
		CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
	if (file == INVALID_HANDLE_VALUE) {
		return 0;
	}
	if (!WriteFile(file, payload, sizeof(payload) - 1, &written, 0) ||
			written != sizeof(payload) - 1 || !FlushFileBuffers(file)) {
		error_code = GetLastError();
		CloseHandle(file);
		DeleteFileW(temporary_path);
		SetLastError(error_code);
		return 0;
	}
	CloseHandle(file);
	if (!MoveFileExW(temporary_path, marker_path,
			MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
		error_code = GetLastError();
		DeleteFileW(temporary_path);
		SetLastError(error_code);
		return 0;
	}
	return 1;
}


static int wait_for_worker_ready(HANDLE worker_process, HANDLE stop_event)
{
	DWORD elapsed = 0;
	DWORD marker_attributes;
	DWORD worker_exit_code;
	DWORD worker_state;
	while (elapsed <= WORKER_READY_TIMEOUT_MS) {
		if (stop_event != 0 &&
				WaitForSingleObject(stop_event, 0) == WAIT_OBJECT_0) {
			return -1;
		}
		worker_state = WaitForSingleObject(worker_process, 0);
		if (worker_state != WAIT_TIMEOUT) {
			if (worker_state == WAIT_FAILED) {
				log_failure(
					"WaitForSingleObject(worker before ready)",
					GetLastError());
			} else if (!GetExitCodeProcess(
					worker_process, &worker_exit_code)) {
				log_failure(
					"GetExitCodeProcess(worker before ready)",
					GetLastError());
			} else {
				log_process_exit(
					"worker_exited_before_ready", worker_exit_code);
			}
			return 0;
		}
		marker_attributes = GetFileAttributesW(g_internal_ready_marker);
		if (marker_attributes != INVALID_FILE_ATTRIBUTES &&
				!(marker_attributes & FILE_ATTRIBUTE_DIRECTORY)) {
			/* Reject a marker published immediately before worker death. */
			worker_state = WaitForSingleObject(worker_process, 0);
			if (worker_state == WAIT_TIMEOUT) {
				return 1;
			}
			if (worker_state == WAIT_FAILED) {
				log_failure(
					"WaitForSingleObject(worker after ready)",
					GetLastError());
			} else if (!GetExitCodeProcess(
					worker_process, &worker_exit_code)) {
				log_failure(
					"GetExitCodeProcess(worker after ready)",
					GetLastError());
			} else {
				log_process_exit(
					"worker_exited_after_ready", worker_exit_code);
			}
			return 0;
		}
		if (elapsed == WORKER_READY_TIMEOUT_MS) {
			break;
		}
		Sleep(WORKER_READY_POLL_MS);
		elapsed += WORKER_READY_POLL_MS;
	}
	log_failure("wait_for_worker_ready", WAIT_TIMEOUT);
	return 0;
}


static int same_filetime(const FILETIME *left, const FILETIME *right)
{
	return left->dwHighDateTime == right->dwHighDateTime &&
		left->dwLowDateTime == right->dwLowDateTime;
}


static int later_filetime(const FILETIME *left, const FILETIME *right)
{
	if (left->dwHighDateTime != right->dwHighDateTime) {
		return left->dwHighDateTime > right->dwHighDateTime;
	}
	return left->dwLowDateTime > right->dwLowDateTime;
}


static int initialize_player_tracker(PlayerProcessTracker *tracker)
{
	int configured;
	ZeroMemory(tracker, sizeof(*tracker));
	tracker->last_exit_index = -1;
	configured = load_procdump_configuration(
		tracker->procdump_path, MAX_PATH,
		tracker->final_dump_path, MAX_PATH);
	if (configured == 1) {
		tracker->procdump_configured = TRUE;
		DeleteFileW(tracker->final_dump_path);
		cleanup_monitor_dump_slots(tracker->final_dump_path);
	}
	return configured >= 0;
}


static int tracker_contains_process(const PlayerProcessTracker *tracker,
		DWORD process_id)
{
	DWORD index;
	for (index = 0; index < tracker->count; ++index) {
		if (tracker->processes[index].id == process_id) {
			return 1;
		}
	}
	return 0;
}


static HANDLE open_matching_game_process(DWORD process_id,
		const WCHAR *game_path)
{
	HANDLE process;
	WCHAR process_path[MAX_PATH];
	DWORD process_path_count = MAX_PATH;
	process = OpenProcess(PROCESS_QUERY_INFORMATION |
		PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE,
		FALSE, process_id);
	if (process == 0) {
		return 0;
	}
	if (!QueryFullProcessImageNameW(
			process, 0, process_path, &process_path_count) ||
			lstrcmpiW(process_path, game_path) != 0) {
		CloseHandle(process);
		SetLastError(ERROR_FILE_NOT_FOUND);
		return 0;
	}
	return process;
}


static int track_player_process(PlayerProcessTracker *tracker,
		DWORD process_id, const WCHAR *game_path)
{
	TrackedGameProcess *tracked;
	HANDLE process;
	if (tracker_contains_process(tracker, process_id)) {
		return 1;
	}
	process = open_matching_game_process(process_id, game_path);
	if (process == 0) {
		/* A Job may also contain browser helpers. A vanished PID or a different
		 * executable is not a player-monitor failure. */
		return 1;
	}
	if (tracker->count >= MAX_GAME_PROCESS_IDS) {
		CloseHandle(process);
		log_failure("track_player_process", ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}
	tracked = &tracker->processes[tracker->count++];
	ZeroMemory(tracked, sizeof(*tracked));
	tracked->id = process_id;
	tracked->process = process;
	tracked->exit_code = STILL_ACTIVE;
	if (tracker->procdump_configured) {
		if (!monitor_dump_path(tracker->final_dump_path,
				tracker->count - 1, tracked->dump_path, MAX_PATH)) {
			log_failure("player_dump_path", ERROR_INSUFFICIENT_BUFFER);
			tracked->dump_path[0] = L'\0';
		} else {
			DeleteFileW(tracked->dump_path);
		}
	}
	return 1;
}


static int attach_ready_player_procdumps(PlayerProcessTracker *tracker,
		const WCHAR *ready_marker)
{
	DWORD marker_attributes;
	DWORD index;
	BOOL marker_consumed = FALSE;
	if (!tracker->procdump_configured) {
		return 1;
	}
	marker_attributes = GetFileAttributesW(ready_marker);
	if (marker_attributes == INVALID_FILE_ATTRIBUTES ||
			(marker_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
		return 1;
	}
	for (index = 0; index < tracker->count; ++index) {
		TrackedGameProcess *tracked = &tracker->processes[index];
		if (tracked->exited || tracked->process == 0 ||
				tracked->procdump_attempted ||
				tracked->dump_path[0] == L'\0') {
			continue;
		}
		tracked->procdump_attempted = TRUE;
		marker_consumed = TRUE;
		tracked->procdump_process = start_procdump_configured(
			tracked->process, tracked->id, tracker->procdump_path,
			tracked->dump_path);
		/* Capture setup is diagnostic only. A ProcDump installation failure
		 * must not close a fully initialized visible game client. */
		if (tracked->procdump_process == 0 &&
				WaitForSingleObject(tracked->process, 0) == WAIT_TIMEOUT) {
			log_failure("player_procdump_unavailable", ERROR_OPEN_FAILED);
		}
	}
	/* One ready publication belongs to the currently tracked client set.  A
	 * replacement WorldOfTanks process inherits the same marker path and must
	 * publish it again after its own loader reaches the Hangar; retaining this
	 * file would attach ProcDump to that replacement during native startup. */
	if (marker_consumed && !remove_marker_path(ready_marker)) {
		log_failure("consume_player_ready_marker", GetLastError());
	}
	return 1;
}


static int track_player_job_processes(PlayerProcessTracker *tracker,
		HANDLE player_job, const WCHAR *game_path)
{
	JobProcessSet processes;
	DWORD index;
	ZeroMemory(&processes, sizeof(processes));
	if (!QueryInformationJobObject(player_job, JobObjectBasicProcessIdList,
			&processes, sizeof(processes), 0)) {
		log_failure("QueryInformationJobObject(player pids)", GetLastError());
		return 0;
	}
	for (index = 0; index < processes.count; ++index) {
		if (!track_player_process(
				tracker, (DWORD)processes.ids[index], game_path)) {
			return 0;
		}
	}
	return 1;
}


static int update_tracked_player_exits(PlayerProcessTracker *tracker)
{
	DWORD index;
	for (index = 0; index < tracker->count; ++index) {
		TrackedGameProcess *tracked = &tracker->processes[index];
		DWORD wait_state;
		FILETIME creation_time;
		FILETIME kernel_time;
		FILETIME user_time;
		if (tracked->exited || tracked->process == 0) {
			continue;
		}
		wait_state = WaitForSingleObject(tracked->process, 0);
		if (wait_state == WAIT_TIMEOUT) {
			continue;
		}
		if (wait_state == WAIT_FAILED) {
			log_failure("WaitForSingleObject(tracked player)", GetLastError());
			return 0;
		}
		if (!GetExitCodeProcess(tracked->process, &tracked->exit_code)) {
			tracked->exit_code = ERROR_PROCESS_ABORTED;
			log_failure("GetExitCodeProcess(tracked player)", GetLastError());
		}
		ZeroMemory(&tracked->exit_time, sizeof(tracked->exit_time));
		if (!GetProcessTimes(tracked->process, &creation_time,
				&tracked->exit_time, &kernel_time, &user_time)) {
			log_failure("GetProcessTimes(tracked player)", GetLastError());
		}
		tracked->exited = TRUE;
		CloseHandle(tracked->process);
		tracked->process = 0;
		if (tracker->last_exit_index < 0 || later_filetime(
				&tracked->exit_time,
				&tracker->processes[tracker->last_exit_index].exit_time) ||
				same_filetime(&tracked->exit_time,
				&tracker->processes[tracker->last_exit_index].exit_time)) {
			tracker->last_exit_index = (int)index;
		}
	}
	return 1;
}


static DWORD active_tracked_player_count(
		const PlayerProcessTracker *tracker)
{
	DWORD index;
	DWORD active = 0;
	for (index = 0; index < tracker->count; ++index) {
		if (!tracker->processes[index].exited &&
				tracker->processes[index].process != 0) {
			++active;
		}
	}
	return active;
}


static int latest_nonzero_player_exit(
		const PlayerProcessTracker *tracker)
{
	DWORD index;
	int latest = -1;
	for (index = 0; index < tracker->count; ++index) {
		const TrackedGameProcess *tracked = &tracker->processes[index];
		if (!tracked->exited || tracked->exit_code == 0 ||
				tracked->exit_code == STILL_ACTIVE) {
			continue;
		}
		if (latest < 0 || later_filetime(
				&tracked->exit_time, &tracker->processes[latest].exit_time) ||
				same_filetime(
					&tracked->exit_time,
					&tracker->processes[latest].exit_time)) {
			latest = (int)index;
		}
	}
	return latest;
}


static void terminate_tracked_players(PlayerProcessTracker *tracker)
{
	DWORD index;
	for (index = 0; index < tracker->count; ++index) {
		TrackedGameProcess *tracked = &tracker->processes[index];
		if (!tracked->exited && tracked->process != 0 &&
				WaitForSingleObject(tracked->process, 0) == WAIT_TIMEOUT) {
			TerminateProcess(tracked->process, ERROR_PROCESS_ABORTED);
		}
	}
}


static void cancel_player_procdumps(PlayerProcessTracker *tracker)
{
	DWORD index;
	if (!tracker->procdump_configured) {
		return;
	}
	for (index = 0; index < tracker->count; ++index) {
		cancel_procdump_now(
			&tracker->processes[index].procdump_process,
			tracker->procdump_path, tracker->processes[index].id);
	}
	cleanup_monitor_dump_slots(tracker->final_dump_path);
}


static int wait_for_tracked_players(PlayerProcessTracker *tracker)
{
	DWORD elapsed = 0;
	DWORD index;
	while (active_tracked_player_count(tracker) != 0) {
		if (!update_tracked_player_exits(tracker)) {
			return 0;
		}
		if (active_tracked_player_count(tracker) != 0) {
			if (elapsed >= TARGET_STOP_TIMEOUT_MS) {
				log_failure("tracked_player_stop_timeout", WAIT_TIMEOUT);
				for (index = 0; index < tracker->count; ++index) {
					TrackedGameProcess *tracked = &tracker->processes[index];
					if (!tracked->exited && tracked->process != 0) {
						terminate_process_bounded(tracked->process,
							ERROR_TIMEOUT, PROCDUMP_CANCEL_TIMEOUT_MS);
						CloseHandle(tracked->process);
						tracked->process = 0;
						tracked->exited = TRUE;
						tracked->exit_code = ERROR_TIMEOUT;
					}
				}
				return 0;
			}
			Sleep(PROCDUMP_ATTACH_POLL_MS);
			elapsed += PROCDUMP_ATTACH_POLL_MS;
		}
	}
	return 1;
}


static DWORD finish_player_tracker(PlayerProcessTracker *tracker,
		BOOL stopped, BOOL use_tracked_exit, DWORD fallback_exit_code,
		int preferred_exit_index)
{
	DWORD index;
	DWORD result = fallback_exit_code;
	TrackedGameProcess *last = 0;
	for (index = 0; index < tracker->count; ++index) {
		TrackedGameProcess *tracked = &tracker->processes[index];
		if (stopped || !tracked->exited || tracked->exit_code == 0 ||
				tracked->exit_code == STILL_ACTIVE) {
			cancel_procdump_now(&tracked->procdump_process,
				tracker->procdump_path, tracked->id);
		} else {
			tracked->dump_complete = wait_for_procdump(
				&tracked->procdump_process,
				tracker->procdump_path, tracked->id);
		}
	}
	if (preferred_exit_index >= 0) {
		last = &tracker->processes[preferred_exit_index];
		result = last->exit_code;
	} else if (use_tracked_exit && tracker->last_exit_index >= 0) {
		last = &tracker->processes[tracker->last_exit_index];
		result = last->exit_code;
	}
	if (tracker->procdump_configured) {
		DeleteFileW(tracker->final_dump_path);
		if (!stopped && last != 0 && result != 0) {
			if (!last->dump_complete || last->dump_path[0] == L'\0' ||
					!complete_regular_dump_file(last->dump_path)) {
				log_failure("player_dump_missing", ERROR_FILE_NOT_FOUND);
			} else if (!MoveFileExW(
					last->dump_path, tracker->final_dump_path,
					MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
				log_failure("promote_player_dump", GetLastError());
			}
		}
		for (index = 0; index < tracker->count; ++index) {
			if (tracker->processes[index].dump_path[0] != L'\0') {
				DeleteFileW(tracker->processes[index].dump_path);
			}
		}
		cleanup_monitor_dump_slots(tracker->final_dump_path);
	}
	return stopped ? 0 : result;
}


static int launch_player(const WCHAR *game_path, BOOL paired_worker,
		HANDLE stop_event)
{
	WCHAR child_command[2 * MAX_PATH];
	WCHAR player_ready_marker[MAX_PATH];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting;
	HANDLE player_job = 0;
	DWORD exit_code = 1;
	DWORD quiet_ms = 0;
	DWORD wait_state;
	PlayerProcessTracker tracker;
	BOOL completed_normally = FALSE;
	BOOL stop_failed = FALSE;
	BOOL stopped = FALSE;
	int preserved_crash_exit = -1;
	int result = 1;
	player_ready_marker[0] = L'\0';
	if (paired_worker) {
		if (!SetEnvironmentVariableW(MULTI_CLIENT_ENV, MULTI_CLIENT_VALUE)) {
			log_failure("SetEnvironmentVariableW", GetLastError());
			return 20;
		}
	} else {
		SetEnvironmentVariableW(MULTI_CLIENT_ENV, 0);
		/* The launcher supplies the selected LAN endpoint to this process.
		 * Keep it for the visible client that inherits our environment. */
	}
	if (!SetEnvironmentVariableW(WORKER_MODE_ENV, PLAYER_MODE_VALUE)) {
		log_failure("SetEnvironmentVariableW(player_mode)", GetLastError());
		return 21;
	}
	SetEnvironmentVariableW(HIDDEN_DESKTOP_ENV, 0);
	SetEnvironmentVariableW(WORKER_READY_MARKER_ENV, 0);
	SetEnvironmentVariableW(WORKER_INTERNAL_READY_MARKER_ENV, 0);
	if (FAILED(StringCchPrintfW(child_command, 2 * MAX_PATH,
			L"\"%s\" --config engine_config.offline-player.xml "
			L"--logFilePrefix offline-player-", game_path))) {
		log_failure("player_command", ERROR_INSUFFICIENT_BUFFER);
		return 21;
	}
	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	ZeroMemory(&process, sizeof(process));
	(void)initialize_player_tracker(&tracker);
	player_job = CreateJobObjectW(0, 0);
	if (player_job == 0 || !configure_kill_job(player_job)) {
		log_failure("CreateJobObjectW(player)", GetLastError());
		if (player_job != 0) {
			CloseHandle(player_job);
		}
		return 22;
	}
	if (!configure_player_ready_marker(player_ready_marker, MAX_PATH) ||
			!remove_marker_path(player_ready_marker) ||
			!SetEnvironmentVariableW(
				PLAYER_READY_MARKER_ENV, player_ready_marker)) {
		log_failure("player_ready_marker", GetLastError());
		CloseHandle(player_job);
		return 22;
	}
	if (!CreateProcessW(game_path, child_command, 0, 0, FALSE,
			CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, 0,
			g_root, &startup, &process)) {
		log_failure("CreateProcessW(player)", GetLastError());
		CloseHandle(player_job);
		SetEnvironmentVariableW(PLAYER_READY_MARKER_ENV, 0);
		(void)remove_marker_path(player_ready_marker);
		return 22;
	}
	if (!AssignProcessToJobObject(player_job, process.hProcess)) {
		log_failure("AssignProcessToJobObject(player)", GetLastError());
		TerminateProcess(process.hProcess, 22);
		result = 22;
		goto player_cleanup;
	}
	if (ResumeThread(process.hThread) == (DWORD)-1) {
		log_failure("ResumeThread(player)", GetLastError());
		TerminateJobObject(player_job, 22);
		result = 22;
		goto player_cleanup;
	}
	/* The client publishes its ready marker only after Python, Scaleform and the
	 * Hangar are live. Track the Job now, but defer debugger attachment until
	 * that native-startup boundary has passed. */
	if (!track_player_process(&tracker, process.dwProcessId, game_path)) {
		log_failure("track_player_process(initial)", GetLastError());
	}
	CloseHandle(process.hThread);
	process.hThread = 0;
	/* The initial process is assigned before ResumeThread, so its descendants
	 * and replacement clients remain in this Job. Only Job-owned PIDs belong
	 * to this launch; scanning every same-path process can claim another game. */
	for (;;) {
		if (!track_player_job_processes(&tracker, player_job, game_path) ||
				!update_tracked_player_exits(&tracker) ||
				!attach_ready_player_procdumps(
					&tracker, player_ready_marker)) {
			TerminateJobObject(player_job, 23);
			result = 23;
			goto player_cleanup;
		}
		if (stop_event != 0 &&
				WaitForSingleObject(stop_event, 0) == WAIT_OBJECT_0) {
			/* Polling exits precedes this branch. Preserve an already-observed
			 * crash, but always retire the complete Job, including helper-only
			 * states where no WorldOfTanks PID is currently active. */
			preserved_crash_exit = latest_nonzero_player_exit(&tracker);
			stopped = preserved_crash_exit < 0;
			if (stopped) {
				cancel_player_procdumps(&tracker);
			}
			if (!TerminateJobObject(
					player_job, ERROR_PROCESS_ABORTED)) {
				log_failure("TerminateJobObject(player stop)", GetLastError());
				stop_failed = TRUE;
			}
			break;
		}
		ZeroMemory(&accounting, sizeof(accounting));
		if (!QueryInformationJobObject(
				player_job, JobObjectBasicAccountingInformation,
				&accounting, sizeof(accounting), 0)) {
			log_failure("QueryInformationJobObject(player)", GetLastError());
			TerminateJobObject(player_job, 23);
			result = 23;
			goto player_cleanup;
		}
		if (accounting.ActiveProcesses == 0 &&
				active_tracked_player_count(&tracker) == 0) {
			quiet_ms += PLAYER_HANDOFF_POLL_MS;
			if (quiet_ms >= PLAYER_HANDOFF_GRACE_MS) {
				break;
			}
		} else {
			quiet_ms = 0;
		}
		if (process.hProcess == 0) {
			Sleep(PLAYER_HANDOFF_POLL_MS);
			continue;
		}
		wait_state = WaitForSingleObject(
			process.hProcess, PLAYER_HANDOFF_POLL_MS);
		if (wait_state == WAIT_FAILED) {
			log_failure("WaitForSingleObject(player)", GetLastError());
			TerminateJobObject(player_job, 23);
			result = 23;
			goto player_cleanup;
		}
		if (wait_state == WAIT_OBJECT_0) {
			if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
				exit_code = 24;
				log_failure("GetExitCodeProcess(player)", GetLastError());
			}
			/* The original handle is no longer needed after its exit code is
			 * saved. Release it while the job keeps tracking replacement
			 * descendants. */
			CloseHandle(process.hProcess);
			process.hProcess = 0;
		}
	}
	result = (int)exit_code;
	completed_normally = TRUE;

player_cleanup:
	SetEnvironmentVariableW(PLAYER_READY_MARKER_ENV, 0);
	if (player_ready_marker[0] != L'\0') {
		(void)remove_marker_path(player_ready_marker);
	}
	if (process.hThread != 0) {
		CloseHandle(process.hThread);
	}
	if (process.hProcess != 0) {
		CloseHandle(process.hProcess);
	}
	CloseHandle(player_job);
	terminate_tracked_players(&tracker);
	if (!wait_for_tracked_players(&tracker)) {
		result = 23;
	}
	result = (int)finish_player_tracker(
		&tracker, stopped, completed_normally, (DWORD)result,
		preserved_crash_exit);
	if (stop_failed && preserved_crash_exit < 0) {
		result = 23;
	}
	return result;
}


int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous_instance,
		LPWSTR command_line, int show_command)
{
	WCHAR game_path[MAX_PATH];
	WCHAR child_command[2 * MAX_PATH];
	WCHAR desktop_name[96];
	WCHAR full_desktop_name[128];
	WCHAR worker_procdump_path[MAX_PATH];
	WCHAR worker_final_dump_path[MAX_PATH];
	WCHAR worker_monitor_dump_path[MAX_PATH];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	HANDLE singleton = 0;
	HANDLE desktop = 0;
	HANDLE job = 0;
	HANDLE procdump_process = 0;
	HANDLE stop_event = 0;
	HANDLE wait_handles[2];
	DWORD error_code = ERROR_SUCCESS;
	DWORD child_exit_code = 1;
	DWORD stop_target_process_id = 0;
	DWORD wait_state;
	BOOL child_created = FALSE;
	BOOL worker_only = FALSE;
	BOOL worker_crashed = FALSE;
	BOOL worker_dump_complete = FALSE;
	BOOL worker_procdump_configured = FALSE;
	BOOL worker_stopped = FALSE;
	int ready_state;
	int result = 1;
	int stop_command_state;
	(void)instance;
	(void)previous_instance;
	(void)show_command;

	ZeroMemory(&process, sizeof(process));
	worker_procdump_path[0] = L'\0';
	worker_final_dump_path[0] = L'\0';
	worker_monitor_dump_path[0] = L'\0';
	g_root[0] = L'\0';
	g_ready_marker[0] = L'\0';
	g_internal_ready_marker[0] = L'\0';
	stop_command_state = parse_starter_stop_command(
		command_line, &stop_target_process_id);
	if (stop_command_state < 0) {
		return 29;
	}
	if (stop_command_state > 0) {
		return signal_starter_stop(stop_target_process_id);
	}
	if (!resolve_game_root(game_path, MAX_PATH)) {
		error_code = GetLastError();
		if (error_code == ERROR_SUCCESS) {
			error_code = ERROR_FILE_NOT_FOUND;
		}
		log_failure("resolve_game_root", error_code);
		return 4;
	}
	if (!configure_ready_markers()) {
		log_failure("ready_marker", ERROR_INSUFFICIENT_BUFFER);
		return 5;
	}
	stop_event = create_starter_stop_event();
	if (stop_event == 0) {
		log_failure("CreateEventW(starter stop)", GetLastError());
		return 29;
	}
	if (lstrcmpiW(command_line, PLAYER_MODE) == 0) {
		result = launch_player(game_path, FALSE, stop_event);
		CloseHandle(stop_event);
		return result;
	}
	if (lstrcmpiW(command_line, PAIRED_PLAYER_MODE) == 0) {
		result = launch_player(game_path, TRUE, stop_event);
		CloseHandle(stop_event);
		return result;
	}
	worker_only = lstrcmpiW(command_line, WORKER_ONLY_MODE) == 0;
	if (!worker_only) {
		log_failure("unsupported_mode", ERROR_INVALID_PARAMETER);
		CloseHandle(stop_event);
		return 30;
	}

	singleton = CreateMutexW(0, TRUE, WORKER_MUTEX_NAME);
	if (singleton == 0) {
		log_failure("CreateMutexW", GetLastError());
		CloseHandle(stop_event);
		return 2;
	}
	if (GetLastError() == ERROR_ALREADY_EXISTS) {
		CloseHandle(singleton);
		CloseHandle(stop_event);
		return 3;
	}
	clear_failure_log();
	/* Only the mutex owner may replace the shared ready marker.  Otherwise a
	 * rapid second launch can erase the live worker's just-published marker. */
	if (!remove_ready_markers()) {
		log_failure("remove_ready_marker", GetLastError());
		result = 5;
		goto worker_cleanup;
	}

	job = CreateJobObjectW(0, 0);
	if (job == 0 || !configure_kill_job(job)) {
		error_code = GetLastError();
		log_failure("CreateJobObjectW", error_code);
		if (job != 0) {
			CloseHandle(job);
			job = 0;
		}
		result = 9;
		goto worker_cleanup;
	}


	if (FAILED(StringCchPrintfW(desktop_name, 96,
			L"OfflineLanWorker_%lu",
			(unsigned long)GetCurrentProcessId()))) {
		log_failure("desktop_name", ERROR_INSUFFICIENT_BUFFER);
		result = 5;
		goto worker_cleanup;
	}
	if (FAILED(StringCchPrintfW(full_desktop_name, 128,
			L"WinSta0\\%s", desktop_name))) {
		log_failure("desktop_name", ERROR_INSUFFICIENT_BUFFER);
		result = 5;
		goto worker_cleanup;
	}
	desktop = CreateDesktopW(desktop_name, 0, 0, 0, GENERIC_ALL, 0);
	if (desktop == 0) {
		error_code = GetLastError();
		log_failure("CreateDesktopW", error_code);
		result = 6;
		goto worker_cleanup;
	}

	if (!SetEnvironmentVariableW(WORKER_MODE_ENV, WORKER_MODE_VALUE) ||
			!SetEnvironmentVariableW(MULTI_CLIENT_ENV,
				MULTI_CLIENT_VALUE) ||
			!SetEnvironmentVariableW(HIDDEN_DESKTOP_ENV,
				HIDDEN_DESKTOP_VALUE) ||
			!SetEnvironmentVariableW(WORKER_READY_MARKER_ENV, 0) ||
			!SetEnvironmentVariableW(WORKER_INTERNAL_READY_MARKER_ENV,
				g_internal_ready_marker) ||
			!SetEnvironmentVariableW(PLAYER_READY_MARKER_ENV, 0)) {
		error_code = GetLastError();
		log_failure("SetEnvironmentVariableW", error_code);
		result = 7;
		goto worker_cleanup;
	}

	if (FAILED(StringCchPrintfW(child_command, 2 * MAX_PATH,
			L"\"%s\" --config engine_config.offline-worker.xml "
			L"--logFilePrefix offline-worker-", game_path))) {
		log_failure("worker_command", ERROR_INSUFFICIENT_BUFFER);
		result = 8;
		goto worker_cleanup;
	}
	ready_state = load_procdump_configuration(
		worker_procdump_path, MAX_PATH,
		worker_final_dump_path, MAX_PATH);
	if (ready_state == 1) {
		worker_procdump_configured = TRUE;
		DeleteFileW(worker_final_dump_path);
		cleanup_monitor_dump_slots(worker_final_dump_path);
		if (!monitor_dump_path(worker_final_dump_path, 0,
				worker_monitor_dump_path, MAX_PATH)) {
			worker_procdump_configured = FALSE;
			log_failure("worker_dump_path", ERROR_INSUFFICIENT_BUFFER);
		}
	}

	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	startup.lpDesktop = full_desktop_name;
	ZeroMemory(&process, sizeof(process));
	child_created = CreateProcessW(game_path, child_command, 0, 0, FALSE,
		CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, 0, g_root,
		&startup, &process);
	if (!child_created) {
		error_code = GetLastError();
		log_failure("CreateProcessW", error_code);
		result = 10;
		goto worker_cleanup;
	}
	if (!AssignProcessToJobObject(job, process.hProcess)) {
		error_code = GetLastError();
		log_failure("AssignProcessToJobObject", error_code);
		TerminateProcess(process.hProcess, 11);
		WaitForSingleObject(process.hProcess, INFINITE);
		result = 11;
		goto worker_cleanup;
	}
	if (ResumeThread(process.hThread) == (DWORD)-1) {
		error_code = GetLastError();
		log_failure("ResumeThread", error_code);
		TerminateProcess(process.hProcess, 12);
		WaitForSingleObject(process.hProcess, INFINITE);
		result = 12;
	} else {
		CloseHandle(process.hThread);
		process.hThread = 0;
		/* #1513 can fault if a debugger attaches during its loader/native
		 * startup. Python publishes only the private internal marker. The
		 * starter waits for that full Hangar+LAN boundary, attaches ProcDump,
		 * rechecks liveness, and only then publishes the marker the launcher
		 * observes. */
		ready_state = wait_for_worker_ready(process.hProcess, stop_event);
		if (ready_state <= 0) {
			if (ready_state < 0 && WaitForSingleObject(
					process.hProcess, 0) == WAIT_TIMEOUT) {
				worker_stopped = TRUE;
				result = 0;
			} else if (WaitForSingleObject(
					process.hProcess, 0) == WAIT_OBJECT_0 &&
					GetExitCodeProcess(
						process.hProcess, &child_exit_code)) {
				log_process_exit(
					"worker_process_exit_before_ready",
					child_exit_code);
				worker_crashed = child_exit_code != 0;
				result = (int)child_exit_code;
			} else {
				result = 23;
			}
			goto worker_cleanup;
		}
		if (worker_procdump_configured) {
			procdump_process = start_procdump_configured(
				process.hProcess, process.dwProcessId,
				worker_procdump_path, worker_monitor_dump_path);
			if (procdump_process == 0) {
				result = 25;
				goto worker_cleanup;
			}
		}
		wait_state = WaitForSingleObject(process.hProcess, 0);
		if (wait_state != WAIT_TIMEOUT) {
			if (wait_state == WAIT_FAILED || !GetExitCodeProcess(
					process.hProcess, &child_exit_code)) {
				log_failure("worker_post_attach_liveness",
					wait_state == WAIT_FAILED ? GetLastError() :
					ERROR_PROCESS_ABORTED);
				result = 23;
			} else {
				log_process_exit(
					"worker_post_attach_exit", child_exit_code);
				worker_crashed = child_exit_code != 0;
				result = (int)child_exit_code;
			}
			goto worker_cleanup;
		}
		if (!publish_ready_marker(g_ready_marker)) {
			log_failure("publish_worker_ready", GetLastError());
			result = 25;
			goto worker_cleanup;
		}
		wait_handles[0] = process.hProcess;
		wait_handles[1] = stop_event;
		wait_state = WaitForMultipleObjects(
			2, wait_handles, FALSE, INFINITE);
		if (wait_state == WAIT_OBJECT_0 + 1) {
			/* A process exit wins if it raced the stop event. */
			if (WaitForSingleObject(
					process.hProcess, 0) == WAIT_OBJECT_0) {
				if (!GetExitCodeProcess(
						process.hProcess, &child_exit_code)) {
					child_exit_code = 13;
					log_failure(
						"GetExitCodeProcess", GetLastError());
				} else {
					log_process_exit(
						"worker_process_exit", child_exit_code);
				}
				worker_crashed = child_exit_code != 0;
				result = (int)child_exit_code;
			} else {
				worker_stopped = TRUE;
				cancel_procdump_now(&procdump_process,
					worker_procdump_path, process.dwProcessId);
				if (worker_procdump_configured) {
					cleanup_monitor_dump_slots(
						worker_final_dump_path);
				}
				TerminateJobObject(job, ERROR_PROCESS_ABORTED);
				if (WaitForSingleObject(process.hProcess,
						TARGET_STOP_TIMEOUT_MS) == WAIT_TIMEOUT) {
					log_failure(
						"worker_stop_timeout", WAIT_TIMEOUT);
				}
				result = 0;
			}
		} else if (wait_state == WAIT_OBJECT_0) {
			if (!GetExitCodeProcess(process.hProcess, &child_exit_code)) {
				child_exit_code = 13;
				log_failure("GetExitCodeProcess", GetLastError());
			} else {
				log_process_exit("worker_process_exit", child_exit_code);
			}
			worker_crashed = child_exit_code != 0;
			result = (int)child_exit_code;
		} else {
			log_failure(
				"WaitForMultipleObjects(worker)", GetLastError());
			worker_stopped = TRUE;
			result = 13;
		}
	}


worker_cleanup:
	SetEnvironmentVariableW(SERVER_HOST_ENV, 0);
	SetEnvironmentVariableW(SERVER_PORT_ENV, 0);
	SetEnvironmentVariableW(WORKER_READY_MARKER_ENV, 0);
	SetEnvironmentVariableW(WORKER_INTERNAL_READY_MARKER_ENV, 0);
	SetEnvironmentVariableW(PLAYER_READY_MARKER_ENV, 0);
	/* A live worker is about to be terminated intentionally by the Job. Cancel
	 * its -t monitor first so normal shutdown cannot create a large dump. */
	if (worker_procdump_configured &&
			(worker_stopped || !worker_crashed)) {
		cancel_procdump_now(&procdump_process,
			worker_procdump_path, process.dwProcessId);
	}
	/* Closing the kill-on-close job retires any browser child still using the
	 * private desktop before the desktop handle itself is released. */
	if (job != 0) {
		CloseHandle(job);
	}
	if (worker_procdump_configured) {
		if (worker_crashed) {
			worker_dump_complete = wait_for_procdump(
				&procdump_process, worker_procdump_path,
				process.dwProcessId);
		}
		DeleteFileW(worker_final_dump_path);
		if (worker_crashed) {
			if (!worker_dump_complete || !complete_regular_dump_file(
					worker_monitor_dump_path)) {
				log_failure("worker_dump_missing", ERROR_FILE_NOT_FOUND);
			} else if (!MoveFileExW(worker_monitor_dump_path,
					worker_final_dump_path,
					MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
				log_failure("promote_worker_dump", GetLastError());
			}
		}
		cleanup_monitor_dump_slots(worker_final_dump_path);
	} else if (procdump_process != 0) {
		terminate_process_bounded(procdump_process, ERROR_PROCESS_ABORTED,
			PROCDUMP_CANCEL_TIMEOUT_MS);
		CloseHandle(procdump_process);
		procdump_process = 0;
	}
	if (process.hThread != 0) {
		CloseHandle(process.hThread);
	}
	if (process.hProcess != 0) {
		CloseHandle(process.hProcess);
	}
	if (desktop != 0) {
		CloseDesktop(desktop);
	}
	(void)remove_ready_markers();
	if (singleton != 0) {
		CloseHandle(singleton);
	}
	if (stop_event != 0) {
		CloseHandle(stop_event);
	}
	return result;
}
