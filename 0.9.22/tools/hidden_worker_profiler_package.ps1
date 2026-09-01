[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Uninstall")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$GameRoot
)

$ErrorActionPreference = "Stop"
$ModPattern = "org.peng.offline_lan_0922*.wotmod"
$DiagnosticMarkerName = "hidden_worker_profiler_build.json"
$BackupDirectoryName = ".offline-hidden-worker-profiler-backup"
$BackupManifestName = "backup_manifest.json"

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return ([System.BitConverter]::ToString(
                $algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $algorithm.Dispose()
    }
}

function Read-JsonFile([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Assert-ExactClient([string]$Root) {
    if (-not [System.IO.File]::Exists((Join-Path $Root "WorldOfTanks.exe"))) {
        throw "WorldOfTanks.exe is missing from: $Root"
    }
    $versionPath = Join-Path $Root "version.xml"
    if (-not [System.IO.File]::Exists($versionPath)) {
        throw "version.xml is missing from: $Root"
    }
    $version = Get-Content -LiteralPath $versionPath -Raw
    if ($version -notmatch "0\.9\.22\.0\.1" -or $version -notmatch "#1513") {
        throw "This diagnostic package supports only 0.9.22.0.1 #1513."
    }
    if (@(Get-Process -Name "WorldOfTanks" -ErrorAction SilentlyContinue).Count -ne 0 -or
            @(Get-Process -Name "WoT-Offline-Battles-Launcher" -ErrorAction SilentlyContinue).Count -ne 0) {
        throw "Close the launcher, every visible client and hidden worker before changing the mod."
    }
}

function Read-DiagnosticMarker([string]$Path) {
    $marker = Read-JsonFile $Path
    if ($marker.schema -ne 1 -or
            $marker.diagnostic -ne "hidden_worker_profiler" -or
            $marker.baseModId -ne "org.peng.offline_lan_0922" -or
            $marker.baseSemanticVersion -ne "0.6.2" -or
            [string]::IsNullOrWhiteSpace([string]$marker.diagnosticBuildIdentity) -or
            [string]$marker.packageFile -ne
                "org.peng.offline_lan_0922_0.6.2.wotmod" -or
            [string]$marker.packageSha256 -notmatch "^[0-9a-f]{64}$") {
        throw "The hidden-worker profiler build marker is invalid."
    }
    return $marker
}

function Install-Profiler(
        [string]$Root,
        [string]$ModDirectory,
        [string]$MarkerPath,
        [string]$BackupRoot) {
    if ([System.IO.Directory]::Exists($BackupRoot) -or
            [System.IO.File]::Exists($BackupRoot)) {
        throw "A profiler backup already exists. Uninstall it before installing another build: $BackupRoot"
    }

    $sourceMarkerPath = Join-Path $PSScriptRoot $DiagnosticMarkerName
    $marker = Read-DiagnosticMarker $sourceMarkerPath
    $sourcePackage = Join-Path (
        Join-Path (Join-Path $PSScriptRoot "payload") "mods\0.9.22.0.1") (
        [string]$marker.packageFile)
    if (-not [System.IO.File]::Exists($sourcePackage)) {
        throw "The diagnostic WOTMOD is missing: $sourcePackage"
    }
    if ((Get-Sha256 $sourcePackage) -ne [string]$marker.packageSha256) {
        throw "The diagnostic WOTMOD does not match its build marker."
    }

    $packages = @()
    if ([System.IO.Directory]::Exists($ModDirectory)) {
        $packages = @(Get-ChildItem -LiteralPath $ModDirectory -Filter $ModPattern -File)
    }
    if ($packages.Count -ne 1 -or $packages[0].Name -ne
            "org.peng.offline_lan_0922_0.6.2.wotmod") {
        throw "Expected exactly one v0.6.2 org.peng.offline_lan_0922 WOTMOD. Install or repair v0.6.2 with the launcher first."
    }

    $backupPackages = Join-Path $BackupRoot "packages"
    New-Item -ItemType Directory -Path $backupPackages -Force | Out-Null
    $hadMarker = [System.IO.File]::Exists($MarkerPath)
    $backupMarker = Join-Path $BackupRoot "previous_diagnostic_marker.json"
    $movedPackages = @()
    $installedPackage = Join-Path $ModDirectory ([string]$marker.packageFile)
    $packageTemporary = Join-Path $ModDirectory (
        ".hidden-worker-profiler-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $markerDirectory = Split-Path -Parent $MarkerPath
    $markerTemporary = Join-Path $markerDirectory (
        ".hidden-worker-profiler-marker-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $markerBackedUp = $false
    $diagnosticPackageInstalled = $false
    $diagnosticMarkerInstalled = $false
    try {
        foreach ($package in $packages) {
            $backup = Join-Path $backupPackages $package.Name
            Move-Item -LiteralPath $package.FullName -Destination $backup
            $movedPackages += $backup
        }
        if ($hadMarker) {
            Move-Item -LiteralPath $MarkerPath -Destination $backupMarker
            $markerBackedUp = $true
        }
        if (-not [System.IO.Directory]::Exists($ModDirectory)) {
            New-Item -ItemType Directory -Path $ModDirectory -Force | Out-Null
        }
        if (-not [System.IO.Directory]::Exists($markerDirectory)) {
            New-Item -ItemType Directory -Path $markerDirectory -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourcePackage -Destination $packageTemporary
        Move-Item -LiteralPath $packageTemporary -Destination $installedPackage
        $diagnosticPackageInstalled = $true
        Copy-Item -LiteralPath $sourceMarkerPath -Destination $markerTemporary
        Move-Item -LiteralPath $markerTemporary -Destination $MarkerPath
        $diagnosticMarkerInstalled = $true
        @{
            schema = 1
            diagnosticBuildIdentity = [string]$marker.diagnosticBuildIdentity
            packageFile = [string]$marker.packageFile
            packageSha256 = [string]$marker.packageSha256
            previousPackages = @($packages | ForEach-Object { $_.Name })
            previousDiagnosticMarker = $hadMarker
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (
            Join-Path $BackupRoot $BackupManifestName) -Encoding UTF8
    }
    catch {
        Remove-Item -LiteralPath $packageTemporary -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $markerTemporary -Force -ErrorAction SilentlyContinue
        if ($diagnosticPackageInstalled) {
            Remove-Item -LiteralPath $installedPackage -Force -ErrorAction SilentlyContinue
        }
        if ($diagnosticMarkerInstalled) {
            Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
        }
        foreach ($backup in $movedPackages) {
            if ([System.IO.File]::Exists($backup)) {
                Move-Item -LiteralPath $backup -Destination (
                    Join-Path $ModDirectory (Split-Path -Leaf $backup))
            }
        }
        if ($markerBackedUp -and [System.IO.File]::Exists($backupMarker)) {
            Move-Item -LiteralPath $backupMarker -Destination $MarkerPath
        }
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
    Write-Host "Installed hidden-worker profiler build $($marker.diagnosticBuildIdentity)."
    Write-Host "Backup: $BackupRoot"
}

function Uninstall-Profiler(
        [string]$Root,
        [string]$ModDirectory,
        [string]$MarkerPath,
        [string]$BackupRoot) {
    $backupManifestPath = Join-Path $BackupRoot $BackupManifestName
    if (-not [System.IO.File]::Exists($backupManifestPath)) {
        throw "No complete hidden-worker profiler backup was found: $BackupRoot"
    }
    $backup = Read-JsonFile $backupManifestPath
    if ($backup.schema -ne 1 -or
            [string]$backup.packageFile -ne
                "org.peng.offline_lan_0922_0.6.2.wotmod" -or
            [string]$backup.packageSha256 -notmatch "^[0-9a-f]{64}$") {
        throw "The hidden-worker profiler backup manifest is invalid."
    }
    $installedPackage = Join-Path $ModDirectory ([string]$backup.packageFile)
    if ([System.IO.File]::Exists($installedPackage) -and
            (Get-Sha256 $installedPackage) -ne [string]$backup.packageSha256) {
        throw "The installed WOTMOD changed after profiling. Recovery files were preserved."
    }
    $otherPackages = @()
    if ([System.IO.Directory]::Exists($ModDirectory)) {
        $otherPackages = @(Get-ChildItem -LiteralPath $ModDirectory -Filter $ModPattern -File |
            Where-Object { $_.FullName -ne $installedPackage })
    }
    if ($otherPackages.Count -ne 0) {
        throw "Another org.peng.offline_lan_0922 WOTMOD appeared after install. Recovery files were preserved."
    }
    if ([System.IO.File]::Exists($MarkerPath)) {
        $installedMarker = Read-DiagnosticMarker $MarkerPath
        if ([string]$installedMarker.diagnosticBuildIdentity -ne
                [string]$backup.diagnosticBuildIdentity -or
                [string]$installedMarker.packageSha256 -ne
                [string]$backup.packageSha256) {
            throw "The diagnostic marker changed after install. Recovery files were preserved."
        }
    }

    $savedCurrent = Join-Path $BackupRoot "installed_diagnostic.wotmod"
    $savedMarker = Join-Path $BackupRoot "installed_diagnostic_marker.json"
    if ([System.IO.File]::Exists($savedCurrent) -or
            [System.IO.File]::Exists($savedMarker)) {
        throw "An earlier uninstall did not finish cleanly. Recovery files were preserved."
    }
    $restoredPackages = @()
    try {
        if ([System.IO.File]::Exists($installedPackage)) {
            Move-Item -LiteralPath $installedPackage -Destination $savedCurrent
        }
        if ([System.IO.File]::Exists($MarkerPath)) {
            Move-Item -LiteralPath $MarkerPath -Destination $savedMarker
        }
        foreach ($name in @($backup.previousPackages)) {
            $leaf = [System.IO.Path]::GetFileName([string]$name)
            if ($leaf -ne [string]$name -or $leaf -notlike $ModPattern) {
                throw "The profiler backup contains an invalid package name."
            }
            $source = Join-Path (Join-Path $BackupRoot "packages") $leaf
            if (-not [System.IO.File]::Exists($source)) {
                throw "The profiler backup is incomplete: $leaf"
            }
            $target = Join-Path $ModDirectory $leaf
            Move-Item -LiteralPath $source -Destination $target
            $restoredPackages += $target
        }
        if ([bool]$backup.previousDiagnosticMarker) {
            $previousMarker = Join-Path $BackupRoot "previous_diagnostic_marker.json"
            if (-not [System.IO.File]::Exists($previousMarker)) {
                throw "The previous diagnostic marker backup is missing."
            }
            Move-Item -LiteralPath $previousMarker -Destination $MarkerPath
        }
    }
    catch {
        foreach ($target in $restoredPackages) {
            if ([System.IO.File]::Exists($target)) {
                Move-Item -LiteralPath $target -Destination (
                    Join-Path (Join-Path $BackupRoot "packages") (
                        Split-Path -Leaf $target))
            }
        }
        if ([System.IO.File]::Exists($savedCurrent)) {
            Move-Item -LiteralPath $savedCurrent -Destination $installedPackage
        }
        if ([System.IO.File]::Exists($savedMarker)) {
            Move-Item -LiteralPath $savedMarker -Destination $MarkerPath
        }
        throw
    }
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    Write-Host "Removed hidden-worker profiler build $($backup.diagnosticBuildIdentity)."
    Write-Host "Restored $($restoredPackages.Count) previous WOTMOD file(s)."
}

try {
    $resolvedRoot = [System.IO.Path]::GetFullPath($GameRoot.Trim().Trim('"'))
    Assert-ExactClient $resolvedRoot
    $modDirectory = Join-Path $resolvedRoot "mods\0.9.22.0.1"
    $markerPath = Join-Path $resolvedRoot (
        "mods\configs\offline_lan_0922\" + $DiagnosticMarkerName)
    $backupRoot = Join-Path $resolvedRoot $BackupDirectoryName
    if ($Action -eq "Install") {
        Install-Profiler $resolvedRoot $modDirectory $markerPath $backupRoot
    }
    else {
        Uninstall-Profiler $resolvedRoot $modDirectory $markerPath $backupRoot
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine([string]$_.Exception.Message)
    exit 1
}
