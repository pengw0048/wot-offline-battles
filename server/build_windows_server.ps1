$ErrorActionPreference = "Stop"

$ServerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PortRoot = Split-Path -Parent $ServerRoot
$ClientScripts = Join-Path $PortRoot "src\res\scripts\client"
$DistRoot = Join-Path $PortRoot "dist\server"
$WorkRoot = Join-Path $PortRoot "dist\.pyinstaller\windows-server"
$SpecRoot = Join-Path $WorkRoot "spec"

if (Test-Path -LiteralPath $DistRoot) {
    Remove-Item -LiteralPath $DistRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
New-Item -ItemType Directory -Force -Path $SpecRoot | Out-Null

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --noupx `
    --name "WoT-0.9.22-LAN-Server" `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --paths $ServerRoot `
    --paths $ClientScripts `
    (Join-Path $ServerRoot "windows_server.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -Force `
    (Join-Path $ServerRoot "WINDOWS_SERVER_README.txt") `
    (Join-Path $DistRoot "README.txt")

$ExpectedFiles = @("README.txt", "WoT-0.9.22-LAN-Server.exe")
$UnexpectedFiles = Get-ChildItem -LiteralPath $DistRoot -Force |
    Where-Object { $_.Name -notin $ExpectedFiles }
if ($UnexpectedFiles) {
    throw "Unexpected file in Windows server delivery directory: $($UnexpectedFiles.Name -join ', ')"
}

Write-Host "Built $DistRoot\WoT-0.9.22-LAN-Server.exe"
