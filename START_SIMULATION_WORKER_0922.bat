@echo off
setlocal

set "GAME_ROOT=%~dp0"
if not exist "%GAME_ROOT%WorldOfTanks.exe" (
    echo Extract this file and the mods folder into the exact 0.9.22.0.1 #1513 game directory.
    pause
    exit /b 2
)
if not exist "%GAME_ROOT%offline_worker_starter.exe" (
    echo Missing offline_worker_starter.exe beside this file.
    pause
    exit /b 3
)

set "OFFLINE_LAN_0922_CLIENT_MODE=simulation_worker"
set "OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS=1"

pushd "%GAME_ROOT%"
start "" "%GAME_ROOT%offline_worker_starter.exe" --worker-only
set "GAME_EXIT=%ERRORLEVEL%"
popd

endlocal & exit /b %GAME_EXIT%
