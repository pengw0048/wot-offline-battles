param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'MSVC build tools are required for the native test fixture' }
$testRoot = Join-Path $env:RUNNER_TEMP 'starter-lifetime'
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$setup = Join-Path $visualStudio 'Common7/Tools/VsDevCmd.bat'
$fixture = Join-Path $testRoot 'fake-game.exe'
$source = Join-Path $repository 'tests/native_starter_fake_game.c'
$batch = Join-Path $testRoot 'compile.cmd'
@"
@echo off
call "$setup" -arch=x86 -host_arch=x64
if errorlevel 1 exit /b 1
cl /nologo /O1 /W4 /WX /D_CRT_SECURE_NO_WARNINGS /DUNICODE /D_UNICODE "$source" /Fe:"$fixture" /Fo:"$testRoot/fixture.obj" /link /SUBSYSTEM:WINDOWS user32.lib
"@ | Set-Content -LiteralPath $batch -Encoding ascii
& cmd.exe /c $batch
if ($LASTEXITCODE -ne 0) { throw 'Native fake-game fixture build failed' }
$env:WOT_STARTER_FAKE_GAME = $fixture
$env:PYTHONDONTWRITEBYTECODE = '1'
& $Python -m unittest discover -s (Join-Path $repository 'tests') -p 'test_port_0922_*starter*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Native starter lifetime tests failed' }
