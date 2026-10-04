/* Controlled client stand-in for the Windows starter lifetime regression. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static void mark(const char *name)
{
    FILE *stream = fopen(name, "wb");
    if (stream != NULL) {
        fprintf(stream, "%lu", (unsigned long)GetCurrentProcessId());
        fclose(stream);
    }
}


static int publish_worker_ready(DWORD process_id)
{
    WCHAR path[MAX_PATH], temporary[MAX_PATH];
    HANDLE marker;
    DWORD written;
    if (GetEnvironmentVariableW(L"OFFLINE_LAN_0922_WORKER_INTERNAL_READY_MARKER",
            path, MAX_PATH) == 0) {
        return 0;
    }
    _snwprintf(temporary, MAX_PATH, L"%s.tmp", path);
    marker = CreateFileW(temporary, GENERIC_WRITE, 0, NULL,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (marker == INVALID_HANDLE_VALUE) {
        return 0;
    }
    if (!WriteFile(marker, &process_id, sizeof(process_id), &written, NULL) ||
            written != sizeof(process_id)) {
        CloseHandle(marker);
        return 0;
    }
    FlushFileBuffers(marker);
    CloseHandle(marker);
    return MoveFileExW(temporary, path,
        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE;
}


int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous,
        LPWSTR command, int show)
{
    WCHAR mode[32], executable[MAX_PATH], child_command[MAX_PATH + 32];
    WCHAR parent_id[32];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD began;
    int worker, stale_published = 0;
    (void)instance;
    (void)previous;
    (void)show;
    mode[0] = L'\0';
    GetEnvironmentVariableW(L"WOT_STARTER_FAKE_MODE", mode, 32);
    worker = wcsncmp(mode, L"worker-", 7) == 0;
    if (lstrcmpW(command, L"--child") == 0) {
        mark("child-ready");
        began = GetTickCount();
        while (worker) {
            if (GetTickCount() - began > 30000) {
                return 41;
            }
            if (GetFileAttributesW(L"exit-child-before-ready") != INVALID_FILE_ATTRIBUTES) {
                return 43;
            }
            if (!stale_published && GetFileAttributesW(L"stale-worker-ready") != INVALID_FILE_ATTRIBUTES) {
                if (!GetEnvironmentVariableW(L"WOT_STARTER_FAKE_PARENT_PID", parent_id, 32) ||
                        !publish_worker_ready((DWORD)wcstoul(parent_id, NULL, 10))) {
                    return 45;
                }
                stale_published = 1;
            }
            if (GetFileAttributesW(L"publish-worker-ready") != INVALID_FILE_ATTRIBUTES) {
                if (!publish_worker_ready(GetCurrentProcessId())) {
                    return 45;
                }
                break;
            }
            Sleep(10);
        }
        while (GetFileAttributesW(L"release-child") == INVALID_FILE_ATTRIBUTES) {
            if (GetTickCount() - began > 30000) {
                return 41;
            }
            Sleep(10);
        }
        return lstrcmpW(mode, L"worker-child-failure") == 0 ? 43 : 0;
    }
    if (lstrcmpW(mode, L"handoff") == 0 ||
            (worker && lstrcmpW(mode, L"worker-normal") != 0)) {
        GetModuleFileNameW(NULL, executable, MAX_PATH);
        if (lstrcmpW(mode, L"worker-helper") == 0) {
            WCHAR *leaf = wcsrchr(executable, L'\\');
            if (leaf == NULL) {
                return 46;
            }
            wcscpy(leaf + 1, L"helper.exe");
        }
        _snwprintf(parent_id, 32, L"%lu", (unsigned long)GetCurrentProcessId());
        SetEnvironmentVariableW(L"WOT_STARTER_FAKE_PARENT_PID", parent_id);
        _snwprintf(child_command, MAX_PATH + 32, L"\"%s\" --child", executable);
        ZeroMemory(&startup, sizeof(startup));
        startup.cb = sizeof(startup);
        startup.dwFlags = STARTF_USESHOWWINDOW;
        startup.wShowWindow = SW_HIDE;
        ZeroMemory(&process, sizeof(process));
        if (!CreateProcessW(executable, child_command, NULL, NULL, FALSE,
                CREATE_NO_WINDOW, NULL, NULL, &startup, &process)) {
            return 42;
        }
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        began = GetTickCount();
        while (GetFileAttributesW(L"child-ready") == INVALID_FILE_ATTRIBUTES) {
            if (GetTickCount() - began > 10000) {
                return 47;
            }
            Sleep(10);
        }
    }
    mark("parent-exiting");
    return lstrcmpW(mode, L"worker-parent-failure") == 0 ? 44 : 0;
}
