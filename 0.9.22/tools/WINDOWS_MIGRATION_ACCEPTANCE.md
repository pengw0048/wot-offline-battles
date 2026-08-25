# Windows one-round migration acceptance

This is the single manual gate for the Rust LAN-server migration. It exercises
one Rust server, two visible #1513 clients, and the required hidden #1513
native-world oracle in one real LAN round. It does not enable or test the
retired independent Python-server mode.

## Before the round

Use the same built package revision on the host and guest PCs. Both game roots
must be the exact Chinese HD 0.9.22.0.1 #1513 client. Close every existing
World of Tanks, worker-starter, and LAN-server process.

On the host, open PowerShell and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "C:\Games\World_of_Tanks\tools\windows_migration_acceptance.ps1" `
  -GameRoot "C:\Games\World_of_Tanks"
```

The script first checks the pinned client, x64 Rust executable, single wotmod,
all three 41-map data inventories, retired Python-server absence, process state,
and TCP 28782. Use `-PreflightOnly` to stop after those automatic checks.

The full run starts the Rust server directly on `0.0.0.0:28782`, proves its
protocol-v5 welcome, starts the hidden oracle, waits for its ready marker, and
then starts the visible host. The default acceptance map is
`04_himmelsdorf` with three tanks per team so the round contains humans, bots,
solid destructibles, and useful drops. Windows Defender Firewall must allow
the Rust executable on the trusted Private network.

On the guest, use the desktop launcher's Online tab, enter the host address
printed by the script, and start the game. The guest must not start another
server or hidden worker.

## One-round coverage

Complete all of these before ending the same round:

1. Host and guest enter the same room and round. Move, aim, fire, reload, take
   damage, and use a repair kit; the other client must see the same state.
2. Bots move and fire. Let a bot damage a player, then damage or destroy a bot.
3. Land one HE direct hit and one nearby HE splash. Observe launch, impact,
   hit feedback, and verify both resulting HP changes on both visible clients.
4. Ram a tank hard enough to produce pose correction and damage. Both clients
   must converge to the same poses and HP.
5. Drive off a drop and observe one server-replicated fall-damage change. A
   second local-client damage application is a failure.
6. Destroy one solid map object with a shell and another by driving through
   it. Both visible clients must show the same destroyed state.
7. Finish by team elimination. Both clients return to the waiting room and can
   open their battle-result notification. Close the guest first, then host.

The script then asks for these visual observations and writes one JSON report
plus bounded log slices under `%TEMP%\WoTOfflineBattlesAcceptance`.

## Automatic log and state criteria

The report passes only when all of the following are true:

- server stdout contains `LAN battle server listening on 0.0.0.0:28782` and
  server stderr is empty;
- the worker ready marker was observed and the hidden-worker log contains
  `simulation worker connected to 127.0.0.1:28782`;
- the visible log reaches the lobby and contains both `PARAMS source=` and
  `battle ammo garage=`, proving that the visible battle runtime was built;
- neither client log contains the mod's battle-failed, battle-aborted,
  authority-prerequisite, rejected-receipt, or worker-failed markers;
- `postbattle_state.json` advances by exactly one battle and its newest result
  contains at least two player rows and one bot row;
- the visible host exits normally and every manual observation is confirmed.

The hidden oracle is deliberately read-only gameplay evidence. Reaching the
live round without an authority-prerequisite failure is the runtime proof that
the Rust server accepted the oracle's world donation; Python remains only in
the visible client and hidden native integration process.
