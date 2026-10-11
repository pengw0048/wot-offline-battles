param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'MSVC build tools are required for the native test fixture' }
$testRoot = Join-Path $env:RUNNER_TEMP 'release097-lifetime'
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$setup = Join-Path $visualStudio 'Common7/Tools/VsDevCmd.bat'
$fixture = Join-Path $testRoot 'exit-fixture.exe'
$source = Join-Path $repository 'tests/release097/exit_fixture.c'
$batch = Join-Path $testRoot 'compile.cmd'
@"
@echo off
call "$setup" -arch=x86 -host_arch=x64
if errorlevel 1 exit /b 1
cl /nologo /O1 /W4 /WX /D_CRT_SECURE_NO_WARNINGS /DUNICODE /D_UNICODE "$source" /Fe:"$fixture" /Fo:"$testRoot/fixture.obj" /link /SUBSYSTEM:WINDOWS user32.lib
"@ | Set-Content -LiteralPath $batch -Encoding ascii
& cmd.exe /c $batch
if ($LASTEXITCODE -ne 0) { throw 'Native fake-game fixture build failed' }
$env:WOT_NATIVE_TEST_ROOT = Join-Path $testRoot 'cases'
$env:WOT_EXIT_FIXTURE_EXE = $fixture
$env:WOT_BASELINE_STARTER = Join-Path $testRoot 'baseline-starter.exe'
@'
from pathlib import Path
import os, subprocess
data = subprocess.check_output(['git', 'show', 'v0.9.6:native/offline_worker_starter.exe'])
Path(os.environ['WOT_BASELINE_STARTER']).write_bytes(data)
'@ | & $Python -
if ($LASTEXITCODE -ne 0) { throw 'Baseline starter extraction failed' }
$env:PYTHONDONTWRITEBYTECODE = '1'
& $Python (Join-Path $repository 'tests/release097/test_native_exit.py')
if ($LASTEXITCODE -ne 0) { throw 'Linked shutdown native tests failed' }
