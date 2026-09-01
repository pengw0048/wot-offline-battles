@echo off
setlocal DisableDelayedExpansion

set "GAME_ROOT=%~1"
if not defined GAME_ROOT if exist "%~dp0WorldOfTanks.exe" set "GAME_ROOT=%~dp0."
if not defined GAME_ROOT set /p "GAME_ROOT=Folder containing WorldOfTanks.exe: "
set "WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT=%GAME_ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "%~dp0hidden_worker_profiler_package.ps1" ^
  -Action Install
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
