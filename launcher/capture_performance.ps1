# A bounded, session-owned WPR trace. The launcher starts this with no console.
# No elevation is requested here and no other WPR recording is cancelled.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SessionState,
    [Parameter(Mandatory=$true)][string]$SessionId,
    [ValidateRange(5,120)][int]$Seconds = 60,
    [ValidateRange(5,300)][int]$WaitSeconds = 180
)
$ErrorActionPreference = 'Stop'
$session = Get-Content -LiteralPath $SessionState -Raw | ConvertFrom-Json
if ($session.id -ne $SessionId -or $SessionId -notmatch '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$') { exit 2 }
$root = Split-Path -Parent ([IO.Path]::GetFullPath($SessionState))
$directory = Join-Path (Join-Path $root 'session-dumps') $SessionId
if ([IO.Path]::GetFullPath($session.dumpDirectory) -ne $directory) { exit 2 }
foreach ($path in @($root, (Split-Path -Parent $directory), $directory)) {
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { exit 2 }
}
$logPath = Join-Path $directory 'performance-trace.txt'
if (Test-Path -LiteralPath $logPath) { exit 0 }
$log = [IO.StreamWriter]::new([IO.File]::Open($logPath, [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write, [IO.FileShare]::Read))
$log.AutoFlush = $true
$instance = 'WotOffline-' + $SessionId
$started = $false
$pending = Join-Path $directory 'performance-trace.pending.etl'
$final = Join-Path $directory 'performance-trace.etl'
function Write-Status([string]$text) {
    $log.WriteLine(('{0:o} {1}' -f [DateTime]::UtcNow, $text))
}
function Same-Session {
    $current = Get-Content -LiteralPath $SessionState -Raw | ConvertFrom-Json
    return ($current.id -eq $SessionId -and -not $current.endedAt)
}
try {
    Write-Status "session=$SessionId requested_seconds=$Seconds"
    Write-Status 'Scope: system-wide CPU scheduling/stacks and GPU events; no heap or network payload capture.'
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Status 'unavailable: WPR requires administrator rights. Start the launcher as administrator for the diagnostic run.'
        exit 0
    }
    $wpr = (Get-Command wpr.exe -ErrorAction Stop).Source
    $source = $session.sources.'visible-client'
    if ($source.blocked) { throw 'Session client log is blocked.' }
    $position = [long]$source.offset
    $carry = ''
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    $live = $false
    while ([DateTime]::UtcNow -lt $deadline -and (Same-Session)) {
        if (Test-Path -LiteralPath $source.path) {
            $stream = [IO.File]::Open($source.path, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                if ($stream.Length -lt $position) { $position = 0 }
                [void]$stream.Seek($position, [IO.SeekOrigin]::Begin)
                # Bound each poll and preserve a suffix across partial lines.
                $buffer = [byte[]]::new(65536)
                $count = $stream.Read($buffer, 0, $buffer.Length)
                $position += $count
                $text = $carry + [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
                $live = $text -match 'PERF summary [^\r\n]*phase=live'
                $carry = $text.Substring([Math]::Max(0, $text.Length - 2048))
            } finally { $stream.Dispose() }
        }
        if ($live) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $live -or -not (Same-Session)) {
        Write-Status 'not_started: no live battle before deadline or session ended.'
        exit 0
    }
    $output = & $wpr -start CPU -start GPU -filemode -instancename $instance 2>&1
    Write-Status ($output -join "`n")
    if ($LASTEXITCODE -ne 0) {
        Write-Status 'unavailable: WPR CPU/GPU start failed; no global recording was stopped.'
        exit 0
    }
    $started = $true
    Write-Status "recording: instance=$instance"
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline -and (Same-Session)) {
        Start-Sleep -Milliseconds 500
    }
    $output = & $wpr -stop $pending -instancename $instance 2>&1
    Write-Status ($output -join "`n")
    if ($LASTEXITCODE -ne 0) { throw 'WPR stop failed.' }
    $started = $false
    $current = Get-Content -LiteralPath $SessionState -Raw | ConvertFrom-Json
    if ($current.id -ne $SessionId) {
        Remove-Item -LiteralPath $pending
        Write-Status 'superseded: trace discarded after session replacement.'
        exit 0
    }
    if ((Get-Item -LiteralPath $pending).Length -gt 512MB) {
        Write-Status 'omitted: ETL exceeds the 512 MiB report limit; pending file retained for manual inspection.'
        exit 0
    }
    Move-Item -LiteralPath $pending -Destination $final
    Write-Status "complete: $final"
} catch {
    Write-Status ('unavailable: ' + $_.Exception.Message)
} finally {
    if ($started) {
        # This instance was successfully started by this invocation only.
        & $wpr -cancel -instancename $instance 2>&1 | ForEach-Object { Write-Status "$_" }
    }
    $log.Dispose()
}
