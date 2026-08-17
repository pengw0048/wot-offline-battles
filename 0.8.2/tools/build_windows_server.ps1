$ErrorActionPreference = "Stop"

$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionRoot = Split-Path -Parent $ToolsRoot
$DistRoot = Join-Path $VersionRoot "dist\server"
$WorkRoot = Join-Path $VersionRoot "dist\.pyinstaller\windows-server"
$SpecRoot = Join-Path $WorkRoot "spec"
$NavGraphs = "scripts\client\gui\mods\offhangar\navgraphs"

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
    --name "WoT-0.8.2-LAN-Server" `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --paths $VersionRoot `
    --add-data "$(Join-Path $VersionRoot $NavGraphs);$NavGraphs" `
    (Join-Path $VersionRoot "lan_battle_server.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$ExpectedFiles = @("WoT-0.8.2-LAN-Server.exe")
$UnexpectedFiles = Get-ChildItem -LiteralPath $DistRoot -Force |
    Where-Object { $_.Name -notin $ExpectedFiles }
if ($UnexpectedFiles) {
    throw "Unexpected file in Windows server delivery directory: $($UnexpectedFiles.Name -join ', ')"
}

Write-Host "Built $DistRoot\WoT-0.8.2-LAN-Server.exe"
