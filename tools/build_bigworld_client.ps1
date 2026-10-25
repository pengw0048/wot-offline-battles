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
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$StandaloneRoot = Join-Path $RepositoryRoot "native\standalone_client\bigworld"
$StandaloneCmake = Join-Path $StandaloneRoot "CMakeLists.txt"
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
    Get-Command cmake, tar | Format-Table Name, Source
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

    # The pinned source predates the current SDK's API deprecations and the
    # standard library's nodiscard annotations. Keep /WX for other diagnostics.
    $CompilerOptions = Join-Path $EngineRoot "build\cmake\BWCompilerAndLinkerOptions.cmake"
    $CompilerOptionsText = Get-Content -LiteralPath $CompilerOptions -Raw
    if ([regex]::Matches($CompilerOptionsText, '/WX').Count -ne 1) {
        throw "The pinned compiler warning policy changed."
    }
    $CompilerOptionsText.Replace('/WX', '/WX /wd4996 /wd4834') |
        Set-Content -LiteralPath $CompilerOptions -Encoding utf8

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
        generator = "Visual Studio 17 2022"
        toolset = "v142"
        platform = "Win32"
        configuration = "Hybrid"
        source_adaptation = @(
            "Define D3DX9 GUIDs for the modern Windows SDK",
            "Suppress observed legacy API/nodiscard warnings C4996 and C4834; retain /WX",
            "Link the repository's native standalone application bootstrap"
        )
    } | ConvertTo-Json | Set-Content -LiteralPath `
        (Join-Path $ArtifactRoot "build-inputs.json") -Encoding utf8

    Invoke-Checked "cmake" @("-S", $EngineRoot, "-B", $BuildRoot,
        "-G", "Visual Studio 17 2022", "-A", "Win32", "-T", "v142",
        "-DBW_CMAKE_TARGET=client", "-DBW_UNIT_TESTS_ENABLED=OFF",
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
    Copy-Item -Path (Join-Path $D3dxNative "release\bin\x86\*.dll") `
        -Destination $ClientOutput
    Copy-Item -LiteralPath (Join-Path $SourceRoot "LICENSE") `
        -Destination (Join-Path $ClientOutput "BigWorld-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $SourceRoot "THIRD-PARTY-NOTICES") `
        -Destination (Join-Path $ClientOutput "BigWorld-THIRD-PARTY-NOTICES.txt")
    Copy-Item -LiteralPath (Join-Path $D3dxRoot "LICENSE.txt") `
        -Destination (Join-Path $ClientOutput "D3DX-LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $D3dxRoot "NOTICE.md") `
        -Destination (Join-Path $ClientOutput "D3DX-NOTICE.md")
    Write-Host "Built standalone BigWorld engine binary: $ClientOutput"
    Write-Host "Includes the standalone bootstrap resource overlay; no WoT game resources are included."
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
