@echo off
setlocal

set "GAME_ROOT=%~dp0"
if not exist "%GAME_ROOT%WorldOfTanks.exe" (
    echo Extract this file and the mods folder into the directory that contains WorldOfTanks.exe.
    pause
    exit /b 2
)
if not exist "%GAME_ROOT%offline_worker_starter.exe" (
    echo Missing offline_worker_starter.exe beside this file.
    pause
    exit /b 3
)

pushd "%GAME_ROOT%"
rem The Launcher owns the room's hidden worker. This entry point starts only
rem the visible player client with the isolated offline preferences.
start "" "%GAME_ROOT%offline_worker_starter.exe" --player
set "GAME_EXIT=%ERRORLEVEL%"
popd

endlocal & exit /b %GAME_EXIT%
