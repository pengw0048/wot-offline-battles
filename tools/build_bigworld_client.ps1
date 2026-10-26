#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkDirectory
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest

if (-not $IsWindows) {
    throw "The BigWorld client build requires Windows and Visual Studio 2022."
}

$Revision = "3d3cc16ae60bb1d71cb014e9b0539e54e6d8c4fb"
$D3dxVersion = "9.29.952.8"
$ScaleformRevision = "7d92be560bf1b2e4ecae9ac69e340d4cae9a557e"
$ScaleformVersion = "4.2.22"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$StandaloneRoot = Join-Path $RepositoryRoot "native\standalone_client\bigworld"
$StandaloneCmake = Join-Path $StandaloneRoot "CMakeLists.txt"
$ScaleformCmake = Join-Path $StandaloneRoot "scaleform_sdk.cmake"
if (-not (Test-Path -LiteralPath $StandaloneCmake)) {
    throw "The standalone application CMake fragment is missing: $StandaloneCmake"
}
$WorkRoot = [IO.Path]::GetFullPath($WorkDirectory)
if (Test-Path -LiteralPath $WorkRoot) {
    throw "Use a new work directory; existing files will not be replaced: $WorkRoot"
}

$SourceRoot = Join-Path $WorkRoot "source"
$BuildRoot = Join-Path $WorkRoot "build"
$ArtifactRoot = Join-Path $WorkRoot "artifacts"
$LogRoot = Join-Path $ArtifactRoot "logs"
$ClientOutput = Join-Path $ArtifactRoot "client"
New-Item -ItemType Directory -Path $SourceRoot, $LogRoot | Out-Null

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$LogName)
    Write-Host "$Program $($Arguments -join ' ')"
    & $Program @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $LogRoot $LogName)
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE; see $LogName"
    }
}

$PreviousDxSdk = $env:DXSDK_DIR
Start-Transcript -Path (Join-Path $LogRoot "build-transcript.log") | Out-Null
try {
    Get-Command cmake, tar, python | Format-Table Name, Source
    Invoke-Checked "cmake" @("--version") "cmake-version.log"

    $Archive = Join-Path $WorkRoot "bigworld.tar.gz"
    $ArchivePrefix = "BigWorld-Engine-14.4.1-$Revision"
    Write-Host "Downloading BigWorld source at $Revision"
    Invoke-WebRequest -Uri "https://codeload.github.com/v2v3v4/BigWorld-Engine-14.4.1/tar.gz/$Revision" -OutFile $Archive
    $Members = @(& tar -tzf $Archive)
    if ($LASTEXITCODE -ne 0 -or $Members.Count -eq 0) {
        throw "Cannot inspect the BigWorld source archive."
    }
    foreach ($Member in $Members) {
        if (($Member -ne $ArchivePrefix -and
                -not $Member.StartsWith("$ArchivePrefix/")) -or
                (($Member -split '/') -contains '..') -or $Member.Contains('\')) {
            throw "Unexpected source archive member: $Member"
        }
    }
    Invoke-Checked "tar" @("-xzf", $Archive, "-C", $SourceRoot,
        "--strip-components", "1") "source-extract.log"

    $EngineRoot = Join-Path $SourceRoot "programming\bigworld"
    foreach ($RelativePath in @("CMakeLists.txt", "client\winmain.cpp",
            "client\CMakeLists.txt", "lib\moo\CMakeLists.txt")) {
        if (-not (Test-Path -LiteralPath (Join-Path $EngineRoot $RelativePath))) {
            throw "Pinned source is missing $RelativePath"
        }
    }

    # Build the available AS3/D3D9 source SDK, rather than reusing its old /MT
    # binaries or relabelling it as the original client's different 4.6.34.
    $ScaleformArchive = Join-Path $WorkRoot "scaleform-sdk.tar.gz"
    $ScaleformRoot = Join-Path $WorkRoot "scaleform-sdk"
    $ScaleformPrefix = "scaleform_sdk-$ScaleformRevision"
    New-Item -ItemType Directory -Path $ScaleformRoot | Out-Null
    Write-Host "Downloading Scaleform SDK $ScaleformVersion source at $ScaleformRevision"
    Invoke-WebRequest -Uri "https://codeload.github.com/RetiredSDK/scaleform_sdk/tar.gz/$ScaleformRevision" -OutFile $ScaleformArchive
    $ScaleformMembers = @(& tar -tzf $ScaleformArchive)
    if ($LASTEXITCODE -ne 0 -or $ScaleformMembers.Count -eq 0) {
        throw "Cannot inspect the pinned Scaleform source archive."
    }
    foreach ($Member in $ScaleformMembers) {
        if (($Member -ne $ScaleformPrefix -and
                -not $Member.StartsWith("$ScaleformPrefix/")) -or
                (($Member -split '/') -contains '..') -or $Member.Contains('\')) {
            throw "Unexpected Scaleform source archive member: $Member"
        }
    }
    Invoke-Checked "tar" @("-xzf", $ScaleformArchive, "-C", $ScaleformRoot,
        "--strip-components", "1") "scaleform-source-extract.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_scaleform_sdk.py"),
        $EngineRoot, $ScaleformRoot) "scaleform-source-patch.log"

    # The pinned source predates the current SDK's API deprecations and the
    # standard library's nodiscard annotations. Keep /WX for other diagnostics.
    $CompilerOptions = Join-Path $EngineRoot "build\cmake\BWCompilerAndLinkerOptions.cmake"
    $CompilerOptionsText = Get-Content -LiteralPath $CompilerOptions -Raw
    if ([regex]::Matches($CompilerOptionsText, '/WX').Count -ne 1) {
        throw "The pinned compiler warning policy changed."
    }
    $CompilerOptionsText.Replace('/WX', '/WX /wd4996 /wd4834') |
        Set-Content -LiteralPath $CompilerOptions -Encoding utf8
    # This is our application build, not a vendor diagnostic build. Keep
    # native crash reporting local instead of invoking BigWorld's uploader.
    $EngineConfig = Join-Path $EngineRoot "lib\cstdmf\config.hpp"
    $EngineConfigText = Get-Content -LiteralPath $EngineConfig -Raw
    $EngineConfigText -replace '(?m)^#define BUILT_BY_BIGWORLD[\t ]+1[\t ]*$', `
        '#define BUILT_BY_BIGWORLD 0' |
        Set-Content -LiteralPath $EngineConfig -Encoding utf8
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_shader_compiler.py"),
        $EngineRoot) "shader-compiler-patch.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_resource_packages.py"),
        $EngineRoot) "resource-packages-patch.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_vehicle_resources.py"),
        $EngineRoot) "vehicle-resources-patch.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_client_session.py"),
        $EngineRoot) "client-session-patch.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_stock_vehicle_filter.py"),
        $EngineRoot) "stock-vehicle-filter-patch.log"
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "patch_vehicle_reflections.py"),
        $EngineRoot) "vehicle-reflections-patch.log"

    # Microsoft's D3DX package supplies the legacy headers/import libraries
    # alongside the runner's Windows SDK, without installing the old SDK.
    $D3dxArchive = Join-Path $WorkRoot "d3dx.nupkg"
    $D3dxRoot = Join-Path $WorkRoot "d3dx"
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.dxsdk.d3dx/$D3dxVersion/microsoft.dxsdk.d3dx.$D3dxVersion.nupkg" -OutFile $D3dxArchive
    [IO.Compression.ZipFile]::ExtractToDirectory($D3dxArchive, $D3dxRoot)
    $D3dxNative = Join-Path $D3dxRoot "build\native"
    foreach ($RelativePath in @("include\d3dx9.h", "include\d3dx10.h",
            "release\lib\x86\d3dx9.lib", "release\bin\x86\D3DX9_43.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $D3dxNative $RelativePath))) {
            throw "Pinned D3DX package is missing $RelativePath"
        }
    }
    $SdkRoot = Join-Path $WorkRoot "dxsdk"
    New-Item -ItemType Directory -Path (Join-Path $SdkRoot "Include"),
        (Join-Path $SdkRoot "Lib\x86") | Out-Null
    Copy-Item -Path (Join-Path $D3dxNative "include\*") `
        -Destination (Join-Path $SdkRoot "Include") -Recurse
    Copy-Item -Path (Join-Path $D3dxNative "release\lib\x86\*.lib") `
        -Destination (Join-Path $SdkRoot "Lib\x86")
    $env:DXSDK_DIR = "$SdkRoot\"

    # The Windows SDK dxguid.lib omits D3DX9 GUIDs. Follow the D3DX package
    # instructions, including IID_ID3DXEffectStateManager used by BigWorld.
    $GuidSource = Join-Path $EngineRoot "client\d3dx_guids.cpp"
    @'
#undef DEFINE_GUID
#include <initguid.h>
#include <d3dx9.h>
'@ | Set-Content -LiteralPath $GuidSource -Encoding utf8
    # Append after the upstream precompiled-header setup: this small source
    # must define its GUIDs without inheriting the client's precompiled header.
    Add-Content -LiteralPath (Join-Path $EngineRoot "client\CMakeLists.txt") `
        -Value "`nTARGET_SOURCES(bwclient PRIVATE d3dx_guids.cpp)`n" -Encoding utf8
    $StandaloneCmakePath = $StandaloneCmake.Replace('\', '/')
    Add-Content -LiteralPath (Join-Path $EngineRoot "client\CMakeLists.txt") `
        -Value "`nINCLUDE(`"$StandaloneCmakePath`")`n" -Encoding utf8

    @{
        upstream = "https://github.com/v2v3v4/BigWorld-Engine-14.4.1"
        revision = $Revision
        d3dx_package = "Microsoft.DXSDK.D3DX/$D3dxVersion"
        scaleform_upstream = "https://github.com/RetiredSDK/scaleform_sdk"
        scaleform_revision = $ScaleformRevision
        scaleform_source_version = $ScaleformVersion
        original_client_scaleform_version = "4.6.34"
        scaleform_audio = "GFx abstract SoundRenderer; optional FMOD backend disabled"
        generator = "Visual Studio 17 2022"
        toolset = "v142"
        platform = "Win32"
        configuration = "Hybrid"
        runtime_resources = "Public BigWorld SourceForge SVN r10"
        source_adaptation = @(
            "Define D3DX9 GUIDs for the modern Windows SDK",
            "Build pinned Scaleform 4.2.22 AS2/AS3 and D3D9 projects with the application /MD CRT",
            "Use the source SDK filter policy; the unavailable BW setRestrictFlashFilters extension is not emulated",
            "Keep GFx abstract audio support without introducing an unused FMOD runtime",
            "Suppress observed legacy API/nodiscard warnings C4996 and C4834; retain /WX",
            "Link the repository's native standalone application bootstrap",
            "Compile FX sources locally through BWResource and cache native FXO files"
            "Disable the upstream vendor crash uploader for this independent build"
        )
    } | ConvertTo-Json | Set-Content -LiteralPath `
        (Join-Path $ArtifactRoot "build-inputs.json") -Encoding utf8

    $ScaleformCmakePath = $ScaleformCmake.Replace('\', '/')
    Invoke-Checked "cmake" @("-S", $EngineRoot, "-B", $BuildRoot,
        "-G", "Visual Studio 17 2022", "-A", "Win32", "-T", "v142",
        "-DBW_CMAKE_TARGET=client", "-DBW_UNIT_TESTS_ENABLED=OFF",
        "-DWOT_SCALEFORM_SOURCE_CMAKE=$ScaleformCmakePath",
        "-DWOT_SCALEFORM_SDK_ROOT=$ScaleformRoot",
        "-DWOT_SCALEFORM_D3DX_ROOT=$SdkRoot",
        "-DWOT_SCALEFORM_FMOD_ADDON=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5") "configure.log"
    Invoke-Checked "cmake" @("--build", $BuildRoot, "--config", "Hybrid",
        "--target", "bwclient", "--parallel", "4", "--",
        "/bl:$(Join-Path $LogRoot 'msbuild.binlog')") "compile.log"

    $UpstreamOutput = Join-Path $SourceRoot "game\bin\client\win32"
    if (-not (Test-Path -LiteralPath (Join-Path $UpstreamOutput "bwclient_h.exe"))) {
        throw "The build returned success without the expected bwclient_h.exe."
    }
    New-Item -ItemType Directory -Path $ClientOutput | Out-Null
    Copy-Item -Path (Join-Path $UpstreamOutput "*") -Destination $ClientOutput -Recurse
    Copy-Item -LiteralPath (Join-Path $StandaloneRoot "res") `
        -Destination (Join-Path $ClientOutput "res") -Recurse
    Invoke-Checked "python" @((Join-Path $StandaloneRoot "fetch_resources.py"),
        (Join-Path $ClientOutput "engine-res"), "--overlay-resources",
        (Join-Path $ClientOutput "res\resources.xml")) "runtime-resources.log"
    @'
<root>
    <Paths>
        <Path>res</Path>
        <Path>engine-res</Path>
    </Paths>
</root>
'@ | Set-Content -LiteralPath (Join-Path $ClientOutput "paths.xml") -Encoding utf8
    # The standalone package reads prepared resources directly. The upstream
    # developer executable otherwise waits for an asset-conversion server.
    @'
@echo off
cd /d "%~dp0"
bwclient_h.exe -noConversion %*
'@ | Set-Content -LiteralPath (Join-Path $ClientOutput "run-client.cmd") -Encoding ascii
    Copy-Item -Path (Join-Path $D3dxNative "release\bin\x86\*.dll") `
        -Destination $ClientOutput
    # Hybrid uses /MD. Ship the installed redistributable CRT beside the EXE
    # so a clean test machine does not need the build runner's Visual Studio.
    $VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $VsRoot = & $VsWhere -latest -products '*' `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $VsRoot) {
        throw "Cannot locate the installed Visual C++ redistributable."
    }
    $Runtime = Get-ChildItem -Path (Join-Path $VsRoot `
        'VC\Redist\MSVC\*\x86\Microsoft.VC*.CRT\vcruntime140.dll') |
        Sort-Object { [version]::new($_.VersionInfo.FileMajorPart,
            $_.VersionInfo.FileMinorPart, $_.VersionInfo.FileBuildPart,
            $_.VersionInfo.FilePrivatePart) } -Descending |
        Select-Object -First 1
    if (-not $Runtime -or -not (Test-Path (Join-Path $Runtime.DirectoryName 'msvcp140.dll'))) {
        throw "The x86 Visual C++ runtime is incomplete."
    }
    Copy-Item -Path (Join-Path $Runtime.DirectoryName '*.dll') -Destination $ClientOutput
    @"
Microsoft Visual C++ Redistributable, x86, $($Runtime.VersionInfo.FileVersion)
Application-local runtime files from the build machine's Visual Studio redist directory.
https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files
https://visualstudio.microsoft.com/license-terms/
"@ | Set-Content -LiteralPath (Join-Path $ClientOutput 'VC-RUNTIME-NOTICE.txt') -Encoding ascii
    Copy-Item -LiteralPath (Join-Path $SourceRoot "LICENSE") `
        -Destination (Join-Path $ClientOutput "BigWorld-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $SourceRoot "THIRD-PARTY-NOTICES") `
        -Destination (Join-Path $ClientOutput "BigWorld-THIRD-PARTY-NOTICES.txt")
    Copy-Item -LiteralPath (Join-Path $D3dxRoot "LICENSE.txt") `
        -Destination (Join-Path $ClientOutput "D3DX-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $D3dxRoot "NOTICE.md") `
        -Destination (Join-Path $ClientOutput "D3DX-NOTICE.md")
    Write-Host "Built standalone BigWorld engine binary: $ClientOutput"
    Write-Host "Includes the standalone bootstrap and public engine resources; no WoT game resources are included."
}
catch {
    Write-Host ($_ | Out-String)
    throw
}
finally {
    if (Test-Path -LiteralPath (Join-Path $BuildRoot "CMakeCache.txt")) {
        Copy-Item -LiteralPath (Join-Path $BuildRoot "CMakeCache.txt") -Destination $LogRoot
    }
    $env:DXSDK_DIR = $PreviousDxSdk
    Stop-Transcript | Out-Null
}
