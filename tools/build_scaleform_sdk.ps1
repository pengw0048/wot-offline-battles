#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)] [string]$SdkRoot,
    [Parameter(Mandatory = $true)] [string]$WorkDirectory,
    [Parameter(Mandatory = $true)] [string]$D3dxRoot,
    [string]$FmodIncludeDirectory,
    [string]$FmodLibrary
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest
if (-not $IsWindows) {
    throw "The Scaleform AS3/D3D9 source build requires Windows and Visual Studio 2022."
}

$SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path
$D3dxRoot = (Resolve-Path -LiteralPath $D3dxRoot).Path
$WithFmod = [bool]($FmodIncludeDirectory -or $FmodLibrary)
if ($WithFmod) {
    if (-not $FmodIncludeDirectory -or -not $FmodLibrary) {
        throw "Supply both FMOD headers and import library to enable the optional backend."
    }
    $FmodIncludeDirectory = (Resolve-Path -LiteralPath $FmodIncludeDirectory).Path
    $FmodLibrary = (Resolve-Path -LiteralPath $FmodLibrary).Path
}
$WorkRoot = [IO.Path]::GetFullPath($WorkDirectory)
if (Test-Path -LiteralPath $WorkRoot) {
    throw "Use a new work directory; existing files will not be replaced: $WorkRoot"
}
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Fragment = Join-Path $RepositoryRoot "native\standalone_client\bigworld\scaleform_sdk.cmake"
foreach ($Required in @($Fragment, (Join-Path $SdkRoot "Include\GFxVersion.h"),
        (Join-Path $D3dxRoot "Include\d3dx9.h"), (Join-Path $D3dxRoot "Lib\x86\d3dx9.lib"))) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Required source-build input is missing: $Required"
    }
}
$VersionHeader = Get-Content -LiteralPath (Join-Path $SdkRoot "Include\GFxVersion.h") -Raw
if ($VersionHeader -notmatch '(?m)^#define\s+GFX_VERSION_STRING\s+"4\.2\.22"') {
    throw "This source manifest was reviewed for Scaleform SDK 4.2.22 only."
}
Get-Command cmake, python | Out-Null

$SourceRoot = Join-Path $WorkRoot "source"
$BuildRoot = Join-Path $WorkRoot "build"
$PreparedSdkRoot = Join-Path $WorkRoot "sdk-source"
$ArtifactRoot = Join-Path $WorkRoot "artifacts"
$LogRoot = Join-Path $ArtifactRoot "logs"
New-Item -ItemType Directory -Path $SourceRoot, $LogRoot | Out-Null

function Invoke-Checked {
    param([string[]]$Arguments, [string]$LogName)
    Write-Host "cmake $($Arguments -join ' ')"
    & cmake @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $LogRoot $LogName)
    if ($LASTEXITCODE -ne 0) {
        throw "cmake failed with exit code $LASTEXITCODE; see $LogName"
    }
}

# Adapt a private source copy; never modify the caller's SDK checkout.
New-Item -ItemType Directory -Path $PreparedSdkRoot | Out-Null
Copy-Item -Path (Join-Path $SdkRoot "*") -Destination $PreparedSdkRoot -Recurse
& python (Join-Path $RepositoryRoot "native\standalone_client\bigworld\patch_scaleform_sdk.py") `
    --sdk-only $PreparedSdkRoot
if ($LASTEXITCODE -ne 0) { throw "Cannot prepare the private SDK source seams." }
$FragmentPath = $Fragment.Replace('\', '/')
if ($FragmentPath.Contains('"') -or $FragmentPath.Contains(';')) {
    throw "The CMake fragment path contains an unsupported quote or semicolon."
}
@"
cmake_minimum_required(VERSION 3.24)
project(wot_scaleform_sdk_source LANGUAGES C CXX)
include("$FragmentPath")
"@ | Set-Content -LiteralPath (Join-Path $SourceRoot "CMakeLists.txt") -Encoding utf8

Start-Transcript -Path (Join-Path $LogRoot "build-transcript.log") | Out-Null
try {
    Invoke-Checked @("--version") "cmake-version.log"
    Invoke-Checked @("-S", $SourceRoot, "-B", $BuildRoot,
        "-G", "Visual Studio 17 2022", "-A", "Win32",
        "-DCMAKE_CONFIGURATION_TYPES=Release",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
        "-DCMAKE_ARCHIVE_OUTPUT_DIRECTORY=$ArtifactRoot/lib",
        "-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=$ArtifactRoot/bin",
        "-DWOT_SCALEFORM_SDK_ROOT=$PreparedSdkRoot",
        "-DWOT_SCALEFORM_D3DX_ROOT=$D3dxRoot",
        "-DWOT_SCALEFORM_FMOD_ADDON=$(if ($WithFmod) { 'ON' } else { 'OFF' })",
        "-DWOT_SCALEFORM_FMOD_INCLUDE_DIR=$FmodIncludeDirectory",
        "-DWOT_SCALEFORM_FMOD_LIBRARY=$FmodLibrary",
        "-DWOT_SCALEFORM_LINK_PROBE=ON") "configure.log"
    Invoke-Checked @("--build", $BuildRoot, "--config", "Release",
        "--target", "wot_scaleform_link_probe", "--parallel") "build.log"
    @{
        sdkVersion = "4.2.22"
        originalClientRuntimeVersion = "4.6.34"
        architecture = "Win32"
        crt = "MD"
        validation = "Source compilation and link only; no movie/render/audio runtime acceptance."
        sourceRoot = $SdkRoot
        optionalFmodBackend = $WithFmod
        fmodIncludeDirectory = $FmodIncludeDirectory
        fmodLibrary = $FmodLibrary
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ArtifactRoot "build-info.json") -Encoding utf8
    Write-Host "Scaleform 4.2.22 AS2/AS3, D3D9, XML and sound source libraries linked at $ArtifactRoot"
} finally {
    Stop-Transcript | Out-Null
}
