[CmdletBinding()]
param(
    [string]$GameRoot = $env:WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT,

    [ValidateRange(5, 3600)]
    [int]$Seconds = 90,

    [ValidateRange(1, 30)]
    [int]$IntervalSeconds = 2,

    [string]$OutputRoot = $env:WOT_HIDDEN_WORKER_PROFILER_OUTPUT_ROOT
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Read-JsonFile([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-JsonFile([string]$Path, $Value) {
    $payload = ($Value | ConvertTo-Json -Depth 16) + "`n"
    [System.IO.File]::WriteAllText($Path, $payload, $Utf8NoBom)
}

function Add-JsonLine([string]$Path, $Value) {
    $line = ($Value | ConvertTo-Json -Depth 16 -Compress) + "`n"
    [System.IO.File]::AppendAllText($Path, $line, $Utf8NoBom)
}

function Get-HeartbeatAge($Status) {
    try {
        $epoch = [double]$Status.heartbeat_epoch
        if ([double]::IsNaN($epoch) -or [double]::IsInfinity($epoch)) {
            return $null
        }
        $unixNow = ([DateTime]::UtcNow - [DateTime]::SpecifyKind(
            [DateTime]"1970-01-01", "Utc")).TotalSeconds
        return $unixNow - $epoch
    }
    catch {
        return $null
    }
}

function Read-WorkerStatus([string]$Path) {
    try {
        $status = Read-JsonFile $Path
        $processId = [int]$status.process_id
        if ($status.schema -ne 1 -or $status.role -ne "simulation_worker" -or
                $processId -le 0) {
            throw "invalid status shape"
        }
        return $status
    }
    catch {
        return $null
    }
}

function Read-GpuCounters([int]$ProcessId) {
    if ($null -eq (Get-Command Get-Counter -ErrorAction SilentlyContinue)) {
        return @{
            available = $false
            source = "windows_gpu_engine_counter"
            reason = "Get-Counter is unavailable"
            engines = @()
        }
    }
    try {
        $counterPath = "\GPU Engine(*pid_${ProcessId}_*)\Utilization Percentage"
        $sample = Get-Counter -Counter $counterPath -ErrorAction Stop
        $engines = @($sample.CounterSamples | Where-Object {
            $_.InstanceName -like "*pid_${ProcessId}_*"
        } | ForEach-Object {
            @{
                instance = [string]$_.InstanceName
                cookedValue = [double]$_.CookedValue
                path = [string]$_.Path
            }
        })
        if ($engines.Count -eq 0) {
            return @{
                available = $false
                source = "windows_gpu_engine_counter"
                reason = "no PID-scoped GPU Engine counter instance"
                engines = @()
            }
        }
        return @{
            available = $true
            source = "windows_gpu_engine_counter"
            reason = $null
            engines = $engines
        }
    }
    catch {
        return @{
            available = $false
            source = "windows_gpu_engine_counter"
            reason = [string]$_.Exception.Message
            engines = @()
        }
    }
}

function Copy-Evidence(
        [string]$Source,
        [string]$DestinationName,
        [System.Collections.ArrayList]$Included,
        [System.Collections.ArrayList]$Missing,
        [string]$CaptureRoot) {
    if ([string]::IsNullOrWhiteSpace($Source) -or
            -not [System.IO.File]::Exists($Source)) {
        [void]$Missing.Add(@{ name = $DestinationName; source = $Source })
        return
    }
    try {
        Copy-Item -LiteralPath $Source -Destination (
            Join-Path $CaptureRoot $DestinationName)
        [void]$Included.Add(@{ name = $DestinationName; source = $Source })
    }
    catch {
        [void]$Missing.Add(@{
            name = $DestinationName
            source = $Source
            reason = [string]$_.Exception.Message
        })
    }
}

function New-ReportZip([string]$CaptureRoot, [string]$ZipPath) {
    if ($null -ne (Get-Command Compress-Archive -ErrorAction SilentlyContinue)) {
        Compress-Archive -Path (Join-Path $CaptureRoot "*") `
            -DestinationPath $ZipPath -Force
        return
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $CaptureRoot, $ZipPath,
        [System.IO.Compression.CompressionLevel]::Optimal, $false)
}

function Resolve-FullPath([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Label path is empty."
    }
    $candidate = $Value.Trim().Trim([char]34)
    try {
        return [System.IO.Path]::GetFullPath($candidate)
    }
    catch {
        throw "$Label path is invalid: $candidate"
    }
}

try {
    $resolvedRoot = Resolve-FullPath $GameRoot "Game folder"
    if (-not [System.IO.File]::Exists((Join-Path $resolvedRoot "WorldOfTanks.exe"))) {
        throw "WorldOfTanks.exe is missing from: $resolvedRoot"
    }
    $statusPath = Join-Path $resolvedRoot (
        "mods\configs\offline_lan_0922\authority_worker_status.json")
    $initialStatus = Read-WorkerStatus $statusPath
    if ($null -eq $initialStatus) {
        throw "A live authority_worker_status.json with a worker PID was not found. Start a battle first."
    }
    $workerPid = [int]$initialStatus.process_id
    $heartbeatAge = Get-HeartbeatAge $initialStatus
    if ($null -eq $heartbeatAge -or $heartbeatAge -lt -5 -or
            $heartbeatAge -gt 10) {
        throw "authority_worker_status.json is stale; start a live battle before collecting."
    }
    $initialProcess = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
    if ($null -eq $initialProcess -or
            [string]$initialProcess.ProcessName -ne "WorldOfTanks") {
        throw "The worker PID in authority_worker_status.json is not running: $workerPid"
    }
    $expectedProcessPath = [System.IO.Path]::GetFullPath(
        (Join-Path $resolvedRoot "WorldOfTanks.exe"))
    $observedProcessPath = $null
    try {
        $observedProcessPath = [string]$initialProcess.Path
    }
    catch {
        # Some Windows process policies deny Path while still exposing PID and
        # ProcessName. The exact name and fresh status remain mandatory.
    }
    if (-not [string]::IsNullOrWhiteSpace($observedProcessPath)) {
        $observedProcessPath = [System.IO.Path]::GetFullPath(
            $observedProcessPath)
        if (-not [string]::Equals(
                $expectedProcessPath, $observedProcessPath,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "The status PID belongs to a WorldOfTanks.exe in another game folder."
        }
    }

    $resolvedOutput = Resolve-FullPath $OutputRoot "Report folder"
    if (-not [System.IO.Directory]::Exists($resolvedOutput)) {
        New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
    }
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $captureBase = Join-Path $resolvedOutput ("hidden-worker-profile-" + $stamp)
    $captureRoot = $captureBase
    while ([System.IO.Directory]::Exists($captureRoot) -or
            [System.IO.File]::Exists($captureRoot) -or
            [System.IO.File]::Exists($captureRoot + ".zip")) {
        $captureRoot = $captureBase + "-" + (
            [Guid]::NewGuid().ToString("N").Substring(0, 8))
    }
    New-Item -ItemType Directory -Path $captureRoot | Out-Null
    $samplesPath = Join-Path $captureRoot "process-and-status-samples.jsonl"
    $startedUtc = [DateTime]::UtcNow
    $deadline = $startedUtc.AddSeconds($Seconds)
    $previousCpuSeconds = $null
    $previousSampleUtc = $null
    $lastStatus = $initialStatus
    $sampleCount = 0
    $collectionFailure = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $sampleStarted = [DateTime]::UtcNow
        $status = Read-WorkerStatus $statusPath
        if ($null -eq $status) {
            $collectionFailure = "worker status became unreadable"
            Add-JsonLine $samplesPath @{
                schema = 1
                sampledAtUtc = $sampleStarted.ToString("o")
                workerPid = $workerPid
                terminalFailure = $collectionFailure
            }
            break
        }
        if ([int]$status.process_id -ne $workerPid) {
            $collectionFailure = "worker status changed PID"
        }
        else {
            $currentHeartbeatAge = Get-HeartbeatAge $status
            if ($null -eq $currentHeartbeatAge -or
                    $currentHeartbeatAge -lt -5 -or
                    $currentHeartbeatAge -gt 10) {
                $collectionFailure = "worker status heartbeat became stale"
            }
        }
        if ($null -ne $collectionFailure) {
            Add-JsonLine $samplesPath @{
                schema = 1
                sampledAtUtc = $sampleStarted.ToString("o")
                workerPid = $workerPid
                terminalFailure = $collectionFailure
                workerStatus = $status
            }
            break
        }
        $lastStatus = $status
        $processRecord = @{
            available = $false
            source = "Get-Process"
            cpuSeconds = $null
            cpuCorePercent = $null
            cpuMachinePercent = $null
            logicalProcessors = [Environment]::ProcessorCount
            workingSetBytes = $null
            privateBytes = $null
        }
        $process = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
        if ($null -eq $process -or
                [string]$process.ProcessName -ne "WorldOfTanks") {
            $collectionFailure = "worker process exited or changed identity"
        }
        else {
            $currentProcessPath = $null
            try {
                $currentProcessPath = [string]$process.Path
            }
            catch {
            }
            if (-not [string]::IsNullOrWhiteSpace($currentProcessPath) -and
                    -not [string]::Equals(
                        $expectedProcessPath,
                        [System.IO.Path]::GetFullPath($currentProcessPath),
                        [System.StringComparison]::OrdinalIgnoreCase)) {
                $collectionFailure = "worker process changed game folder"
            }
            else {
                $process.Refresh()
                $cpuSeconds = [double]$process.TotalProcessorTime.TotalSeconds
                $processRecord.available = $true
                $processRecord.cpuSeconds = $cpuSeconds
                $processRecord.workingSetBytes = [Int64]$process.WorkingSet64
                $processRecord.privateBytes = [Int64]$process.PrivateMemorySize64
                if ($null -ne $previousCpuSeconds -and $null -ne $previousSampleUtc) {
                    $wallSeconds = ($sampleStarted - $previousSampleUtc).TotalSeconds
                    if ($wallSeconds -gt 0 -and $cpuSeconds -ge $previousCpuSeconds) {
                        $corePercent = 100.0 * (
                            $cpuSeconds - $previousCpuSeconds) / $wallSeconds
                        $processRecord.cpuCorePercent = $corePercent
                        $processRecord.cpuMachinePercent = $corePercent / [Math]::Max(
                            1, [Environment]::ProcessorCount)
                    }
                }
                $previousCpuSeconds = $cpuSeconds
                $previousSampleUtc = $sampleStarted
            }
        }
        $gpuRecord = @{
            available = $false
            source = "windows_gpu_engine_counter"
            reason = "worker process identity is unavailable"
            engines = @()
        }
        if ($null -eq $collectionFailure) {
            $gpuRecord = Read-GpuCounters $workerPid
        }
        Add-JsonLine $samplesPath @{
            schema = 1
            sampledAtUtc = $sampleStarted.ToString("o")
            workerPid = $workerPid
            process = $processRecord
            gpu = $gpuRecord
            workerStatus = $status
            terminalFailure = $collectionFailure
        }
        $sampleCount += 1
        if ($null -ne $collectionFailure) {
            break
        }
        $remaining = $IntervalSeconds - (
            [DateTime]::UtcNow - $sampleStarted).TotalSeconds
        if ($remaining -gt 0) {
            Start-Sleep -Milliseconds ([int]($remaining * 1000.0))
        }
    }

    Write-JsonFile (Join-Path $captureRoot "authority_worker_status.json") $lastStatus
    $included = New-Object System.Collections.ArrayList
    $missing = New-Object System.Collections.ArrayList
    Copy-Evidence (Join-Path $resolvedRoot "offline-worker-python.log") `
        "hidden-worker.log" $included $missing $captureRoot
    Copy-Evidence (Join-Path $resolvedRoot "offline-worker-starter.log") `
        "hidden-worker-starter.log" $included $missing $captureRoot
    Copy-Evidence (Join-Path $resolvedRoot "offline-player-python.log") `
        "visible-client.log" $included $missing $captureRoot
    Copy-Evidence (Join-Path $resolvedRoot (
        "mods\configs\offline_lan_0922\hidden_worker_profiler_build.json")) `
        "hidden_worker_profiler_build.json" $included $missing $captureRoot

    $launcherRoot = $null
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $launcherRoot = Join-Path $env:LOCALAPPDATA "WoTOfflineBattles"
    }
    $launcherLog = $null
    $latestSessionPath = $null
    if ($null -ne $launcherRoot) {
        $launcherLog = Join-Path $launcherRoot "launcher.log"
        $latestSessionPath = Join-Path $launcherRoot "latest-error-report-session.json"
    }
    Copy-Evidence $launcherLog "launcher.log" $included $missing $captureRoot
    Copy-Evidence $latestSessionPath "latest-error-report-session.json" `
        $included $missing $captureRoot
    if ($null -ne $latestSessionPath -and [System.IO.File]::Exists($latestSessionPath)) {
        try {
            $session = Read-JsonFile $latestSessionPath
            $sessionId = [string]$session.id
            $sessionGameRoot = [System.IO.Path]::GetFullPath(
                [string]$session.gameRoot)
            if (-not [string]::Equals(
                    $resolvedRoot, $sessionGameRoot,
                    [System.StringComparison]::OrdinalIgnoreCase)) {
                [void]$missing.Add(@{
                    name = "server.log"
                    source = $latestSessionPath
                    reason = "latest launcher session belongs to another game root"
                })
            }
            elseif ($sessionId -match "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$") {
                $sessionServerLog = Join-Path $launcherRoot (
                    "session-logs\" + $sessionId + "\server.log")
                Copy-Evidence $sessionServerLog "server.log" `
                    $included $missing $captureRoot
            }
            else {
                [void]$missing.Add(@{
                    name = "server.log"
                    source = $latestSessionPath
                    reason = "latest launcher session id is unavailable"
                })
            }
        }
        catch {
            [void]$missing.Add(@{
                name = "server.log"
                source = "latest-error-report-session.json"
                reason = [string]$_.Exception.Message
            })
        }
    }
    else {
        [void]$missing.Add(@{ name = "server.log"; source = $null })
    }

    Write-JsonFile (Join-Path $captureRoot "collection_manifest.json") @{
        schema = 1
        diagnostic = "hidden_worker_profiler_collection"
        gameRoot = $resolvedRoot
        workerPid = $workerPid
        startedAtUtc = $startedUtc.ToString("o")
        endedAtUtc = [DateTime]::UtcNow.ToString("o")
        requestedSeconds = $Seconds
        intervalSeconds = $IntervalSeconds
        samples = $sampleCount
        terminatedEarly = ($null -ne $collectionFailure)
        terminalFailure = $collectionFailure
        included = @($included)
        missing = @($missing)
        runtimeEvidence = @(
            "authority_worker_status.json runtime.frame_performance with detailed phase timings and work counters",
            "hidden-worker.log PERF and slow-frame lines with native-query and Python phase summaries"
        )
        gpuRule = "PID-scoped Windows GPU Engine counters only; unavailable is not estimated"
    }
    $zipPath = $captureRoot + ".zip"
    New-ReportZip $captureRoot $zipPath
    Write-Host "Collected hidden-worker profile: $zipPath"
    Write-Host "Review the report before sharing it; logs can contain local paths and session addresses."
    if ($null -ne $collectionFailure) {
        [Console]::Error.WriteLine("Collection stopped early: $collectionFailure")
        exit 2
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine([string]$_.Exception.Message)
    exit 1
}
