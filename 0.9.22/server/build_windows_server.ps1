$ErrorActionPreference = "Stop"

$ServerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PortRoot = Split-Path -Parent $ServerRoot
$RustRoot = Join-Path $PortRoot "rust_server"
$DistRoot = Join-Path $PortRoot "dist\server"
$Target = "x86_64-pc-windows-msvc"
$BuiltExe = Join-Path $RustRoot `
    "target\$Target\release\offline-rust-server.exe"
$PackagedExe = Join-Path $DistRoot "WoT-0.9.22-LAN-Server.exe"

if (Test-Path -LiteralPath $DistRoot) {
    Remove-Item -LiteralPath $DistRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null

cargo build `
    --manifest-path (Join-Path $RustRoot "Cargo.toml") `
    --locked `
    --release `
    --target $Target

if ($LASTEXITCODE -ne 0) {
    throw "Rust server build failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $BuiltExe -PathType Leaf)) {
    throw "Rust server build did not produce $BuiltExe"
}

Copy-Item -LiteralPath $BuiltExe -Destination $PackagedExe -Force
Copy-Item -Force `
    (Join-Path $ServerRoot "WINDOWS_SERVER_README.txt") `
    (Join-Path $DistRoot "README.txt")

$ExpectedFiles = @("README.txt", "WoT-0.9.22-LAN-Server.exe")
$UnexpectedFiles = Get-ChildItem -LiteralPath $DistRoot -Force |
    Where-Object { $_.Name -notin $ExpectedFiles }
if ($UnexpectedFiles) {
    throw "Unexpected file in Windows server delivery directory: $($UnexpectedFiles.Name -join ', ')"
}

Write-Host "Built $PackagedExe"
