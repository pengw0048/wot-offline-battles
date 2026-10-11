#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

static void mark(const char *name) {
    FILE *f = fopen(name, "wb");
    if (f) { fprintf(f, "%lu", (unsigned long)GetCurrentProcessId()); fclose(f); }
}
static void finished(DWORD flags, BOOL bad) {
    WCHAR path[MAX_PATH]; DWORD data[2], written; HANDLE f;
    if (!GetEnvironmentVariableW(L"OFFLINE_LAN_0922_PLAYER_FINISHED_MARKER", path, MAX_PATH)) return;
    data[0] = bad ? 1 : GetCurrentProcessId(); data[1] = flags;
    f = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, 0, CREATE_ALWAYS, 0, 0);
    if (f != INVALID_HANDLE_VALUE) { WriteFile(f, data, sizeof(data), &written, 0); CloseHandle(f); }
}
static int child(BOOL helper, const WCHAR *argument) {
    WCHAR path[MAX_PATH], line[MAX_PATH + 64], *slash;
    STARTUPINFOW s; PROCESS_INFORMATION p;
    GetModuleFileNameW(0, path, MAX_PATH);
    if (helper) { slash = wcsrchr(path, L'\\'); wcscpy(slash + 1, L"Helper.exe"); }
    _snwprintf(line, MAX_PATH + 64, L"\"%s\" %s", path, argument);
    ZeroMemory(&s, sizeof(s)); s.cb = sizeof(s);
    s.dwFlags = STARTF_USESHOWWINDOW; s.wShowWindow = SW_HIDE;
    if (!CreateProcessW(path, line, 0, 0, FALSE, CREATE_NO_WINDOW, 0, 0, &s, &p)) return 42;
    CloseHandle(p.hThread); CloseHandle(p.hProcess); return 0;
}
int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, LPWSTR command, int show) {
    WCHAR mode[32]; DWORD started;
    (void)a; (void)b; (void)show;
    if (wcscmp(command,L"--helper") == 0) { mark("helper-ready"); Sleep(60000); return 0; }
    if (wcscmp(command,L"--recording") == 0) { mark("helper-ready"); Sleep(8000); mark("recording-saved"); return 0; }
    if (wcscmp(command,L"--replacement") == 0 || wcscmp(command,L"--outside") == 0) {
        mark(wcscmp(command,L"--outside")==0 ? "outside-ready" : "replacement-ready");
        started=GetTickCount();
        while (GetFileAttributesW(L"release") == INVALID_FILE_ATTRIBUTES && GetTickCount()-started<60000) Sleep(10);
        return 0;
    }
    GetEnvironmentVariableW(L"WOT_EXIT_FIXTURE",mode,32);
    mark("parent-ready");
    if (wcscmp(mode,L"helper")==0 || wcscmp(mode,L"crash")==0) child(TRUE,L"--helper");
    if (wcscmp(mode,L"recording")==0) child(TRUE,L"--recording");
    if (wcscmp(mode,L"loading")!=0) finished(wcscmp(mode,L"recording")==0, wcscmp(mode,L"bad")==0);
    if (wcscmp(mode,L"handoff")==0) { Sleep(500); return child(FALSE,L"--replacement"); }
    if (wcscmp(mode,L"crash")==0) ExitProcess(0xc0000005);
    if (wcscmp(mode,L"hang")==0 || wcscmp(mode,L"loading")==0 || wcscmp(mode,L"bad")==0) Sleep(60000);
    return 0;
}
