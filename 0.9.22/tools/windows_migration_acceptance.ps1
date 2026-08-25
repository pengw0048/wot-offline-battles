[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$GameRoot = (Split-Path -Parent $PSScriptRoot),

    [ValidateRange(2, 15)]
    [int]$TeamSize = 3,

    [ValidateSet(
        "01_karelia", "02_malinovka", "04_himmelsdorf", "05_prohorovka",
        "06_ensk", "07_lakeville", "08_ruinberg", "10_hills", "11_murovanka",
        "13_erlenberg", "14_siegfried_line", "17_munchen", "18_cliff",
        "19_monastery", "22_slough", "23_westfeld", "28_desert",
        "29_el_hallouf", "31_airfield", "33_fjord", "34_redshire",
        "35_steppes", "36_fishing_bay", "37_caucasus",
        "38_mannerheim_line", "44_north_america", "45_north_america",
        "47_canada_a", "59_asia_great_wall", "63_tundra", "73_asia_korea",
        "83_kharkiv", "84_winter", "86_himmelsdorf_winter", "92_stalingrad",
        "95_lost_city", "100_thepit", "101_dday", "103_ruinberg_winter",
        "112_eiffel_tower_ctf", "114_czech")]
    [string]$Map = "04_himmelsdorf",

    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProtocolVersion = 5
$ClientBuild = "wot-0.9.22.0.1-cn-1513"
$ProbeCapabilities = @(
    "projectile_ledger_v2",
    "ricochet_continuation_v1",
    "destructible_catalog_v5",
    "lean_snapshot_manifest_v1",
    "ram_contact_ledger_v3",
    "human_ram_timeline_v1",
    "player_fire_intent_v4",
    "player_environment_v2",
    "effective_params_v1",
    "player_ammo_authority_v1",
    "player_authority_loadout_v1"
)
$RequiredServerCapabilities = @(
    "destructible_catalog_v5",
    "lean_snapshot_manifest_v1",
    "ram_contact_ledger_v3",
    "human_ram_timeline_v1",
    "he_explosion_evidence_v1",
    "player_fire_intent_v4",
    "player_environment_v2",
    "effective_params_v1",
    "ricochet_continuation_v1",
    "player_ammo_authority_v1",
    "player_authority_loadout_v1",
    "projectile_hit_vehicle_v1",
    "projectile_wreck_hit_v1",
    "random_map_v1",
    "team_selection_v1",
    "team_size_selection_v1",
    "oracle_backed_server_v1",
    "native_oracle_v1"
)
$Port = 28782
$ServerName = "WoT-0.9.22-LAN-Server.exe"
$StarterName = "offline_worker_starter.exe"
$ReadyMarkerName = "offline-worker.ready"
$DataSets = @("navgraphs", "foliage", "destructibles")
$LegacyServerFiles = @(
    "descriptor_projection.py",
    "lan_battle_server.py",
    "offline_rewards.py",
    "server_battle_authority.py",
    "server_bot_ai.py",
    "server_world.py",
    "vehicle_overlay_store.py",
    "windows_server.py"
)

function Write-Step([string]$Message) {
    Write-Host "[acceptance] $Message"
}

function Assert-Acceptance([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
        ConvertFrom-Json
}

function Get-FileLength([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [int64]0
    }
    return [int64](Get-Item -LiteralPath $Path).Length
}

function Read-LogSlice([string]$Path, [int64]$Offset) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite)
    try {
        if ($Offset -gt $stream.Length) {
            $Offset = 0
        }
        [void]$stream.Seek($Offset, [System.IO.SeekOrigin]::Begin)
        $reader = [System.IO.StreamReader]::new(
            $stream, [System.Text.Encoding]::UTF8, $true, 4096, $true)
        try {
            return $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Get-PostBattlePath([string]$Root) {
    $external = Join-Path $env:APPDATA (
        "Wargaming.net\WorldOfTanks\offline_lan_0922\postbattle_state.json")
    $legacy = Join-Path $Root (
        "mods\configs\offline_lan_0922\postbattle_state.json")
    if (Test-Path -LiteralPath $external -PathType Leaf) {
        return $external
    }
    if (Test-Path -LiteralPath $legacy -PathType Leaf) {
        return $legacy
    }
    return $external
}

function Get-BattleCount([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return 0
    }
    try {
        $state = Read-Json $Path
        if ($null -eq $state.progress -or $null -eq $state.progress.battles) {
            return 0
        }
        return [int]$state.progress.battles
    }
    catch {
        return 0
    }
}

function Get-LatestReceipt([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $state = Read-Json $Path
    $receipts = @()
    if ($null -ne $state.history) {
        $receipts += @($state.history)
    }
    if ($null -ne $state.pending) {
        $receipts += @($state.pending)
    }
    if ($receipts.Count -eq 0) {
        return $null
    }
    $ordered = @($receipts | Sort-Object {
        if ($null -eq $_.arena_unique_id) { -1 }
        else { [int64]$_.arena_unique_id }
    })
    return $ordered[$ordered.Count - 1]
}

function Get-PeMachine([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $reader = [System.IO.BinaryReader]::new($stream)
    try {
        [void]$stream.Seek(0x3c, [System.IO.SeekOrigin]::Begin)
        $peOffset = $reader.ReadUInt32()
        [void]$stream.Seek($peOffset + 4, [System.IO.SeekOrigin]::Begin)
        return $reader.ReadUInt16()
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Test-LocalPortAccepting([int]$TcpPort, [int]$TimeoutMs = 250) {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $null
    try {
        $async = $client.BeginConnect("127.0.0.1", $TcpPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $async) {
            $async.AsyncWaitHandle.Close()
        }
        $client.Dispose()
    }
}

function Test-ServerProtocol([int]$TcpPort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.ReceiveTimeout = 5000
        $client.SendTimeout = 5000
        $client.Connect("127.0.0.1", $TcpPort)
        $stream = $client.GetStream()
        $writer = [System.IO.StreamWriter]::new($stream)
        $reader = [System.IO.StreamReader]::new($stream)
        try {
            $writer.NewLine = "`n"
            $writer.AutoFlush = $true
            $hello = [ordered]@{
                type = "hello"
                protocol = $ProtocolVersion
                client_build = $ClientBuild
                role = "probe"
                capabilities = $ProbeCapabilities
            } | ConvertTo-Json -Compress
            $writer.WriteLine($hello)
            $line = $reader.ReadLine()
            if ([string]::IsNullOrWhiteSpace($line)) {
                return $false
            }
            $reply = $line | ConvertFrom-Json
            $echoedCapabilities = @($reply.capabilities)
            $serverCapabilities = @($reply.server_capabilities)
            $valid = (
                $reply.type -eq "welcome" -and
                [int]$reply.protocol -eq $ProtocolVersion -and
                $reply.client_build -eq $ClientBuild -and
                $echoedCapabilities.Count -eq $ProbeCapabilities.Count -and
                $serverCapabilities.Count -eq $RequiredServerCapabilities.Count
            )
            foreach ($capability in $ProbeCapabilities) {
                $valid = $valid -and ($echoedCapabilities -contains $capability)
            }
            foreach ($capability in $RequiredServerCapabilities) {
                $valid = $valid -and ($serverCapabilities -contains $capability)
            }
            return $valid
        }
        finally {
            $writer.Dispose()
            $reader.Dispose()
            $stream.Dispose()
        }
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Stop-OwnedProcess($Process, [string]$Label) {
    if ($null -eq $Process) {
        return
    }
    try {
        if (-not $Process.HasExited) {
            Write-Step "Stopping $Label."
            Stop-Process -Id $Process.Id -Force -ErrorAction Stop
            $Process.WaitForExit()
        }
    }
    catch {
        Write-Warning "Could not stop $Label process $($Process.Id): $_"
    }
}

function Confirm-Observation([string]$Prompt) {
    while ($true) {
        $answer = (Read-Host "$Prompt [y/n]").Trim().ToLowerInvariant()
        if ($answer -eq "y" -or $answer -eq "yes") {
            return $true
        }
        if ($answer -eq "n" -or $answer -eq "no") {
            return $false
        }
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "This acceptance entry must run on Windows."
}

$GameRoot = [System.IO.Path]::GetFullPath($GameRoot)
$WorldOfTanks = Join-Path $GameRoot "WorldOfTanks.exe"
$VersionFile = Join-Path $GameRoot "version.xml"
$ServerExe = Join-Path $GameRoot $ServerName
$StarterExe = Join-Path $GameRoot $StarterName
$DataRoot = Join-Path $GameRoot "mods\configs\offline_lan_0922"
$PlayerLog = Join-Path $GameRoot "offline-player-python.log"
$WorkerLog = Join-Path $GameRoot "offline-worker-python.log"
$StarterFailureLog = Join-Path $GameRoot "offline-worker-starter.log"
$ReadyMarker = Join-Path $GameRoot $ReadyMarkerName
$PostBattlePath = Get-PostBattlePath $GameRoot

$runStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$ReportRoot = Join-Path $env:TEMP "WoTOfflineBattlesAcceptance\$runStamp"
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$ReportPath = Join-Path $ReportRoot "acceptance-report.json"
$ServerStdout = Join-Path $ReportRoot "server.stdout.log"
$ServerStderr = Join-Path $ReportRoot "server.stderr.log"
$VisibleSlicePath = Join-Path $ReportRoot "visible-client.log"
$WorkerSlicePath = Join-Path $ReportRoot "hidden-worker.log"

Write-Step "Running package and machine preflight."
Assert-Acceptance (Test-Path -LiteralPath $WorldOfTanks -PathType Leaf) (
    "WorldOfTanks.exe is missing from $GameRoot")
Assert-Acceptance (Test-Path -LiteralPath $VersionFile -PathType Leaf) (
    "version.xml is missing from $GameRoot")
$versionText = Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8
Assert-Acceptance ($versionText -match 'v\.0\.9\.22\.0\.1\s+#1513') (
    "The game root is not the exact 0.9.22.0.1 #1513 client.")

foreach ($required in @($ServerExe, $StarterExe)) {
    Assert-Acceptance (Test-Path -LiteralPath $required -PathType Leaf) (
        "Required migration executable is missing: $required")
}
Assert-Acceptance ((Get-PeMachine $ServerExe) -eq 0x8664) (
    "$ServerName is not an x64 PE executable.")

$wotmods = @(Get-ChildItem -LiteralPath (Join-Path $GameRoot "mods\0.9.22.0.1") `
    -Filter "org.peng.offline_lan_0922*.wotmod" -File -ErrorAction Stop)
Assert-Acceptance ($wotmods.Count -eq 1) (
    "Exactly one org.peng.offline_lan_0922 wotmod must be installed; found $($wotmods.Count).")

foreach ($dataset in $DataSets) {
    $datasetRoot = Join-Path $DataRoot $dataset
    $manifestPath = Join-Path $datasetRoot "manifest.json"
    Assert-Acceptance (Test-Path -LiteralPath $manifestPath -PathType Leaf) (
        "$dataset manifest is missing.")
    $manifest = Read-Json $manifestPath
    $records = @($manifest.maps)
    Assert-Acceptance ($records.Count -eq 41) (
        "$dataset must contain all 41 supported maps; found $($records.Count).")
    foreach ($record in $records) {
        Assert-Acceptance (-not [string]::IsNullOrWhiteSpace($record.file)) (
            "$dataset manifest contains an empty file entry.")
        $dataFile = Join-Path $datasetRoot ([string]$record.file)
        Assert-Acceptance (Test-Path -LiteralPath $dataFile -PathType Leaf) (
            "$dataset payload is missing: $dataFile")
    }
}

$legacyFound = @()
foreach ($name in $LegacyServerFiles) {
    foreach ($candidate in @(
            (Join-Path $GameRoot $name),
            (Join-Path (Join-Path $GameRoot "server") $name))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $legacyFound += $candidate
        }
    }
}
Assert-Acceptance ($legacyFound.Count -eq 0) (
    "Retired independent Python server files are present: $($legacyFound -join ', ')")
$legacyAuthority = [Environment]::GetEnvironmentVariable(
    "WOT_LAN_AUTHORITY", [EnvironmentVariableTarget]::Process)
Assert-Acceptance ($null -eq $legacyAuthority) (
    "Remove WOT_LAN_AUTHORITY from this shell; the retired authority mode is not part of this test.")
Assert-Acceptance ([string]::IsNullOrEmpty($env:WOT_0922_LOOPBACK_ONLY)) (
    "Remove WOT_0922_LOOPBACK_ONLY so the guest can connect over the LAN.")

$running = @(Get-Process -Name @(
        "WorldOfTanks", "offline_worker_starter", "WoT-0.9.22-LAN-Server") `
    -ErrorAction SilentlyContinue)
Assert-Acceptance ($running.Count -eq 0) (
    "Close existing game, worker starter, and LAN server processes before acceptance.")
Assert-Acceptance (-not (Test-LocalPortAccepting $Port)) (
    "TCP $Port is already in use.")

$preflight = [ordered]@{
    exactClient = $true
    rustServerX64 = $true
    oneClientPackage = $true
    completeDataSets = $true
    retiredPythonServerAbsent = $true
    cleanProcessState = $true
    tcpPortFree = $true
    map = $Map
    teamSize = $TeamSize
}
Write-Step "Preflight passed."

if ($PreflightOnly) {
    $report = [ordered]@{
        schema = 1
        mode = "preflight"
        passed = $true
        gameRoot = $GameRoot
        completedAtUtc = [DateTime]::UtcNow.ToString("o")
        preflight = $preflight
    }
    $report | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Step "Preflight report: $ReportPath"
    return
}

$playerOffset = Get-FileLength $PlayerLog
$workerOffset = Get-FileLength $WorkerLog
$baselineBattles = Get-BattleCount $PostBattlePath
$managedEnvironment = @(
    "WOT_0922_TEAM_SIZE",
    "WOT_0922_TEAM1_SIZE",
    "WOT_0922_TEAM2_SIZE",
    "WOT_0922_LOOPBACK_ONLY",
    "OFFLINE_LAN_0922_SERVER_HOST",
    "OFFLINE_LAN_0922_SERVER_PORT",
    "OFFLINE_LAN_0922_PREFERRED_TEAM"
)
$savedEnvironment = @{}
foreach ($name in $managedEnvironment) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name, [EnvironmentVariableTarget]::Process)
}

$server = $null
$worker = $null
$hostPlayer = $null
$hostExitCode = $null
$runError = $null
$workerMarkerObserved = $false

try {
    $env:WOT_0922_TEAM_SIZE = [string]$TeamSize
    $env:WOT_0922_TEAM1_SIZE = [string]$TeamSize
    $env:WOT_0922_TEAM2_SIZE = [string]$TeamSize
    Remove-Item Env:\WOT_0922_LOOPBACK_ONLY -ErrorAction SilentlyContinue
    $env:OFFLINE_LAN_0922_SERVER_HOST = "127.0.0.1"
    $env:OFFLINE_LAN_0922_SERVER_PORT = [string]$Port
    $env:OFFLINE_LAN_0922_PREFERRED_TEAM = "1"

    Write-Step "Starting the Rust LAN server on 0.0.0.0:$Port."
    $server = Start-Process -FilePath $ServerExe `
        -ArgumentList @("serve", "--map", $Map) `
        -WorkingDirectory $GameRoot `
        -RedirectStandardOutput $ServerStdout `
        -RedirectStandardError $ServerStderr `
        -PassThru

    $serverReady = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($server.HasExited) {
            throw "The Rust LAN server exited before its protocol was ready."
        }
        if (Test-ServerProtocol $Port) {
            $serverReady = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    Assert-Acceptance $serverReady (
        "The Rust LAN server did not answer protocol v5 within 30 seconds.")
    Write-Step "Rust protocol v5 answered with native-oracle and HE-evidence capabilities."

    Write-Step "Starting the required hidden #1513 native oracle."
    Remove-Item -LiteralPath $ReadyMarker -Force -ErrorAction SilentlyContinue
    $worker = Start-Process -FilePath $StarterExe `
        -ArgumentList "--worker-only" `
        -WorkingDirectory $GameRoot `
        -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($worker.HasExited) {
            $failure = ""
            if (Test-Path -LiteralPath $StarterFailureLog -PathType Leaf) {
                $failure = Get-Content -LiteralPath $StarterFailureLog -Raw
            }
            throw "The hidden oracle starter exited before ready. $failure"
        }
        if (Test-Path -LiteralPath $ReadyMarker -PathType Leaf) {
            $workerMarkerObserved = $true
            break
        }
        Start-Sleep -Milliseconds 100
    }
    Assert-Acceptance $workerMarkerObserved (
        "The hidden native oracle did not publish its ready marker within 60 seconds.")
    Write-Step "Hidden native oracle is connected and ready."

    $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and $_.IPAddress -ne "0.0.0.0"
        } | Select-Object -ExpandProperty IPAddress -Unique)
    Write-Host ""
    Write-Host "ONE-ROUND MANUAL COVERAGE" -ForegroundColor Cyan
    Write-Host "1. On the guest PC, use the same package revision and exact #1513 client."
    Write-Host "2. In the desktop launcher choose Online, enter one host address below, and start the guest:"
    foreach ($address in $addresses) {
        Write-Host "     $address`:$Port"
    }
    if ($addresses.Count -eq 0) {
        Write-Host "     <host-LAN-address>`:$Port"
    }
    Write-Host "3. Put host and guest in the room, then start $Map with $TeamSize tanks per team."
    Write-Host "4. During this single round cover every item:"
    Write-Host "   - both visible players move, aim, fire, reload, take damage, and use a repair kit;"
    Write-Host "   - bots move, fire, damage a player, and can be damaged or killed;"
    Write-Host "   - land one HE direct hit and one nearby HE splash; verify both resulting HP changes on both visible clients;"
    Write-Host "   - collide/ram a tank and observe correction plus server-replicated damage;"
    Write-Host "   - drive off a drop and observe server-replicated fall damage;"
    Write-Host "   - destroy one map object with a shell and one by driving through it;"
    Write-Host "   - finish by elimination and open the battle-result notification on both clients."
    Write-Host "5. Return both clients to the waiting room, close the guest, then close the host."
    Write-Host ""

    Write-Step "Starting the visible host client. This script waits for it to close."
    $hostPlayer = Start-Process -FilePath $StarterExe `
        -ArgumentList "--paired-player" `
        -WorkingDirectory $GameRoot `
        -PassThru
    $hostPlayer.WaitForExit()
    $hostExitCode = $hostPlayer.ExitCode
}
catch {
    $runError = $_.Exception.Message
}
finally {
    Stop-OwnedProcess $hostPlayer "visible host"
    Stop-OwnedProcess $worker "hidden native oracle"
    Stop-OwnedProcess $server "Rust LAN server"
    foreach ($name in $managedEnvironment) {
        $oldValue = $savedEnvironment[$name]
        if ($null -eq $oldValue) {
            Remove-Item ("Env:\" + $name) -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable(
                $name, [string]$oldValue,
                [EnvironmentVariableTarget]::Process)
        }
    }
}

$visibleSlice = Read-LogSlice $PlayerLog $playerOffset
$workerSlice = Read-LogSlice $WorkerLog $workerOffset
$visibleSlice | Set-Content -LiteralPath $VisibleSlicePath -Encoding UTF8
$workerSlice | Set-Content -LiteralPath $WorkerSlicePath -Encoding UTF8
$serverOutText = if (Test-Path -LiteralPath $ServerStdout) {
    Get-Content -LiteralPath $ServerStdout -Raw
} else { "" }
$serverErrText = if (Test-Path -LiteralPath $ServerStderr) {
    Get-Content -LiteralPath $ServerStderr -Raw
} else { "" }

$postBattlePathAfter = Get-PostBattlePath $GameRoot
$battleCountAfter = Get-BattleCount $postBattlePathAfter
$latestReceipt = $null
try {
    $latestReceipt = Get-LatestReceipt $postBattlePathAfter
}
catch {
    $latestReceipt = $null
}
$publicResults = @()
if ($null -ne $latestReceipt -and $null -ne $latestReceipt.public_results) {
    $publicResults = @($latestReceipt.public_results)
}
$playerRows = @($publicResults | Where-Object { $_.actor_kind -eq "player" })
$botRows = @($publicResults | Where-Object { $_.actor_kind -eq "bot" })

$failurePatterns = @(
    "[Offline LAN 0.9.22] battle failed:",
    "[Offline LAN 0.9.22] battle aborted",
    "[Offline LAN 0.9.22] LAN server ended round",
    "[Offline LAN 0.9.22] battle receipt was rejected",
    "[Offline LAN 0.9.22] simulation worker failed:"
)
$failureHits = @()
foreach ($pattern in $failurePatterns) {
    if ($visibleSlice.Contains($pattern) -or $workerSlice.Contains($pattern)) {
        $failureHits += $pattern
    }
}

$automatic = [ordered]@{
    rustServerListened = $serverOutText.Contains(
        "LAN battle server listening on 0.0.0.0:$Port")
    rustServerStderrEmpty = [string]::IsNullOrWhiteSpace($serverErrText)
    hiddenOracleReadyMarkerObserved = $workerMarkerObserved
    hiddenOracleConnectedLog = $workerSlice.Contains(
        "[Offline LAN 0.9.22] simulation worker connected to 127.0.0.1:$Port")
    visibleClientReachedLobby = $visibleSlice.Contains(
        "[Offline LAN 0.9.22] lobby ready; click Battle to join")
    visibleClientBuiltBattle = (
        $visibleSlice.Contains("[Offline LAN 0.9.22] PARAMS source=") -and
        $visibleSlice.Contains("[Offline LAN 0.9.22] battle ammo garage="))
    noMigrationFailureLog = ($failureHits.Count -eq 0)
    hostExitedNormally = ($hostExitCode -eq 0)
    resultPersisted = ($battleCountAfter -eq ($baselineBattles + 1))
    resultContainsHostAndGuest = ($playerRows.Count -ge 2)
    resultContainsBots = ($botRows.Count -ge 1)
}

$manual = [ordered]@{}
if ([string]::IsNullOrEmpty($runError)) {
    Write-Host ""
    Write-Host "Record the observations from the round:" -ForegroundColor Cyan
    $manual['hostAndGuest'] = Confirm-Observation (
        "Did host and guest enter the same round and replicate movement/fire/HP?")
    $manual['bots'] = Confirm-Observation (
        "Did bots move, fire, deal damage, and accept damage or death?")
    $manual['projectiles'] = Confirm-Observation (
        "Did one HE direct hit and one nearby HE splash each produce matching HP on both clients?")
    $manual['collision'] = Confirm-Observation (
        "Did a tank collision/ram produce correction and replicated damage?")
    $manual['environment'] = Confirm-Observation (
        "Did a fall produce one server-replicated HP change without a local duplicate?")
    $manual['destructibles'] = Confirm-Observation (
        "Did shell and hull destruction each replicate the same map object state?")
    $manual['result'] = Confirm-Observation (
        "Did elimination return both clients to the room with battle results?")
}

$allAutomatic = $true
foreach ($value in $automatic.Values) {
    $allAutomatic = $allAutomatic -and [bool]$value
}
$allManual = ($manual.Count -gt 0)
foreach ($value in $manual.Values) {
    $allManual = $allManual -and [bool]$value
}
$passed = (
    [string]::IsNullOrEmpty($runError) -and $allAutomatic -and $allManual)

$report = [ordered]@{
    schema = 1
    mode = "one-round-migration"
    passed = $passed
    gameRoot = $GameRoot
    completedAtUtc = [DateTime]::UtcNow.ToString("o")
    runError = $runError
    preflight = $preflight
    automatic = $automatic
    manual = $manual
    evidence = [ordered]@{
        reportDirectory = $ReportRoot
        serverStdout = $ServerStdout
        serverStderr = $ServerStderr
        visibleClientLog = $VisibleSlicePath
        hiddenWorkerLog = $WorkerSlicePath
        postBattleState = $postBattlePathAfter
        battleCountBefore = $baselineBattles
        battleCountAfter = $battleCountAfter
        publicPlayerRows = $playerRows.Count
        publicBotRows = $botRows.Count
        failurePatterns = $failureHits
    }
}
$report | ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath $ReportPath -Encoding UTF8

if ($passed) {
    Write-Step "PASS: the one-round Rust migration acceptance is complete."
    Write-Step "Report: $ReportPath"
}
else {
    Write-Step "FAIL: one or more automatic or manual criteria did not pass."
    Write-Step "Report: $ReportPath"
    throw "Windows migration acceptance failed. Keep the report directory for diagnosis."
}
