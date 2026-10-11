/* Controlled client stand-in for the Windows starter lifetime regression. */
#include <windows.h>
#include <stdio.h>

static void mark(const char *name)
{
    FILE *stream = fopen(name, "wb");
    if (stream != NULL) {
        fprintf(stream, "%lu", (unsigned long)GetCurrentProcessId());
        fclose(stream);
    }
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous,
        LPWSTR command, int show)
{
    WCHAR mode[32], executable[MAX_PATH], child_command[MAX_PATH + 32];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD began;
    (void)instance;
    (void)previous;
    (void)show;
    if (lstrcmpW(command, L"--child") == 0) {
        mark("child-ready");
        began = GetTickCount();
        while (GetFileAttributesW(L"release-child") == INVALID_FILE_ATTRIBUTES) {
            if (GetTickCount() - began > 30000) {
                return 41;
            }
            Sleep(10);
        }
        return 0;
    }
    if (GetEnvironmentVariableW(L"WOT_STARTER_FAKE_MODE", mode, 32) != 0 &&
            lstrcmpW(mode, L"handoff") == 0) {
        GetModuleFileNameW(NULL, executable, MAX_PATH);
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
    }
    mark("parent-exiting");
    return 0;
}
