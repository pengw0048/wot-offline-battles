$ErrorActionPreference = "Stop"

$LauncherRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $LauncherRoot
$DistRoot = Join-Path $LauncherRoot "dist"
$BuildRoot = Join-Path $LauncherRoot "build"
$WorkRoot = Join-Path $BuildRoot "pyinstaller"
$SpecRoot = Join-Path $BuildRoot "spec"
# PyInstaller owns its work directory, so the payload is staged beside it.
$PayloadRoot = Join-Path $BuildRoot "payload"
$AppName = "WoT-Offline-Battles-Launcher"
$PackedXmlModule = Join-Path $RepoRoot "0.9.22\tools\packed_xml.py"

if (-not (Test-Path -LiteralPath $PackedXmlModule)) {
    throw "Launcher build dependency is missing: $PackedXmlModule"
}

foreach ($directory in @($DistRoot, $BuildRoot)) {
    if (Test-Path -LiteralPath $directory) {
        Remove-Item -LiteralPath $directory -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
New-Item -ItemType Directory -Force -Path $SpecRoot | Out-Null

python (Join-Path $LauncherRoot "stage_payload.py") `
    --output $PayloadRoot `
    --source $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Payload staging failed with exit code $LASTEXITCODE"
}

foreach ($entry in @("servers\0.9.22\dist\server\WoT-0.9.22-LAN-Server.exe",
                     "client\0.9.22.zip")) {
    if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot $entry))) {
        throw "Payload is incomplete: $entry"
    }
}

# A one-folder build keeps the payload on disk instead of extracting it on
# every launch and on every server start.
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --noupx `
    --name $AppName `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --paths (Join-Path $RepoRoot "0.9.22\tools") `
    --hidden-import packed_xml `
    --add-data "$PayloadRoot\servers;servers" `
    --add-data "$PayloadRoot\client;client" `
    (Join-Path $LauncherRoot "wot_launcher.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$AppRoot = Join-Path $DistRoot $AppName
$ClientPayloadRoot = Join-Path $AppRoot "_internal\client"
$ServerPayloadRoot = Join-Path $AppRoot "_internal\servers"
$ClientPayloads = @(
    Get-ChildItem -LiteralPath $ClientPayloadRoot -File -Force)
$ServerPayloads = @(
    Get-ChildItem -LiteralPath $ServerPayloadRoot -Directory -Force)
if ($ClientPayloads.Count -ne 1 -or
        $ClientPayloads[0].Name -ne "0.9.22.zip") {
    throw "Launcher must carry only the 0.9.22 client payload"
}
if ($ServerPayloads.Count -ne 1 -or
        $ServerPayloads[0].Name -ne "0.9.22") {
    throw "Launcher must carry only the 0.9.22 server payload"
}
$ExpectedServerExecutable = Join-Path $ServerPayloadRoot `
    "0.9.22\dist\server\WoT-0.9.22-LAN-Server.exe"
$ServerPayloadFiles = @(
    Get-ChildItem -LiteralPath $ServerPayloadRoot -File -Recurse -Force)
if ($ServerPayloadFiles.Count -ne 1 -or
        $ServerPayloadFiles[0].FullName -ne $ExpectedServerExecutable) {
    throw "Launcher must carry only the Rust 0.9.22 server executable"
}
$ForbiddenEntries = @(
    Get-ChildItem -LiteralPath $AppRoot -Recurse -Force |
        Where-Object {
            $RelativePath = $_.FullName.Substring($AppRoot.Length)
            $PathSegments = $RelativePath -split '[\\/]'
            ($PathSegments -contains "0.8.2") -or
                (($RelativePath -replace
                    '[^A-Za-z0-9]', '').ToLowerInvariant()).Contains(
                        "mapstudio")
        })
if ($ForbiddenEntries.Count -ne 0) {
    throw "0.8.2 and Map Studio must remain outside the launcher distribution"
}

Copy-Item -Force `
    (Join-Path $LauncherRoot "LAUNCHER_README.txt") `
    (Join-Path $DistRoot "$AppName\README.txt")
Copy-Item -Force `
    (Join-Path $RepoRoot "LICENSE") `
    (Join-Path $DistRoot "$AppName\LICENSE")
Copy-Item -Force `
    (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.md") `
    (Join-Path $DistRoot "$AppName\THIRD_PARTY_NOTICES.md")
$LicenseRoot = Join-Path $DistRoot "$AppName\licenses"
New-Item -ItemType Directory -Force -Path $LicenseRoot | Out-Null
Copy-Item -Force `
    (Join-Path $RepoRoot "licenses\Boost-1.0.txt") `
    (Join-Path $LicenseRoot "Boost-1.0.txt")

foreach ($entry in @("$AppName.exe", "README.txt", "LICENSE",
                     "THIRD_PARTY_NOTICES.md", "licenses\Boost-1.0.txt")) {
    if (-not (Test-Path -LiteralPath (Join-Path $DistRoot "$AppName\$entry"))) {
        throw "Launcher distribution is incomplete: $entry"
    }
}

Compress-Archive -Force `
    -Path (Join-Path $DistRoot $AppName) `
    -DestinationPath (Join-Path $DistRoot "$AppName-Windows.zip")

Write-Host "Built $DistRoot\$AppName\$AppName.exe"
