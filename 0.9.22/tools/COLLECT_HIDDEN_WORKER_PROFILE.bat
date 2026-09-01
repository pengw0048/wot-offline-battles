@echo off
setlocal DisableDelayedExpansion

set "GAME_ROOT=%~1"
set "CAPTURE_SECONDS=%~2"
if not defined GAME_ROOT if exist "%~dp0WorldOfTanks.exe" set "GAME_ROOT=%~dp0."
if not defined GAME_ROOT set /p "GAME_ROOT=Folder containing WorldOfTanks.exe: "
if not defined CAPTURE_SECONDS set "CAPTURE_SECONDS=60"
set "WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT=%GAME_ROOT%"
set "WOT_HIDDEN_WORKER_PROFILER_OUTPUT_ROOT=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "%~dp0collect_hidden_worker_profile.ps1" ^
  -Seconds %CAPTURE_SECONDS%
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
