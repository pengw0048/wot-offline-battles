# World of Tanks 2.3.1.2 offline interface POC

This directory is an interface proof for the pinned North American HD client
`v.2.3.1.2 #919` (`client 2598149`). It is deliberately not an offline battle
implementation.

The mod observes the production `helpers.OfflineMode` state transition that is
already invoked by `game.start()`. It does not replace `BigWorld.player`, create
an arena, create a vehicle, install battle GUI, implement physics, or emulate a
server. It also fails closed unless the stock `helpers.getClientVersion()` value
is exactly `v.2.3.1.2 #919`. A successful run proves only:

1. the stock mod loader imports this CPython 2.7 module and calls `init()`;
2. the stock `offline` command reaches `OfflineMode.launch()`;
3. the requested space reaches `OfflineMode.isSpaceLoaded()`.

The formal port must preserve the mature 0.8.2 behavior and code wherever the
target client still supports it. Code is adapted only where an observed
2.3.1.2 interface difference requires it. See
[`PORTING_BASELINE.md`](PORTING_BASELINE.md) for that contract; this POC is not
a substitute for any battle subsystem.

## Build

```sh
cd /path/to/wot-offline-battles/2.3.1.2
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py
PYENV_VERSION=2.7.18 pyenv exec python build_wotmod.py
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py \
  dist/org.peng.offline_2312_poc_0.1.1.wotmod
python3 tools/validate_wotmod.py \
  dist/org.peng.offline_2312_poc_0.1.1.wotmod
```

The result contains stored CPython 2.7 adjacent bytecode and no Python source.
The POC deliberately uses one direct `mod_*.pyc` entry so this loader proof
does not depend on a nested package import.

## Windows runtime proof

Remove the earlier `org.peng.offline_2311_poc_0.1.0.wotmod` and
`org.peng.offline_2312_poc_0.1.0.wotmod`, copy the generated `.wotmod` into the
complete target client at `mods/2.3.1.2/`, then launch the client executable
directly:

```bat
cd /d C:\Games\World_of_Tanks
win64\WorldOfTanks.exe --script-arg offline --script-arg spaces/01_karelia
```

The 2.3.1.2 executable forwards only the single token immediately following
each `--script-arg` (or `-sa`) into Python `sys.argv`. The flag must therefore
be repeated for both `offline` and the space name. Bare positional arguments,
one flag followed by two values, or one quoted combined value do not satisfy
the stock `OfflineMode.onStartup()` contract and lead to the login screen.

Exit the client process to leave the stock one-way offline mode. Do not use an
online account for this proof. Repeat the cold launch once after exiting.

Search `python.log` for `[OFFLINE_2312_POC]`.

- Pass: `module_import`, `init_enter`, `probe_start`,
  `offline_mode_entered`, then `space_loaded` appear in that order; the first
  two markers show Python `argv` containing separate `offline` and
  `spaces/01_karelia` entries; the last marker identifies `OfflineEntity`,
  `FreeCamera`, and matching non-null player/camera space IDs; `spaces=[]` is
  valid for this client-only space and remains diagnostic only; the map is
  visible in the stock free camera; and `probe_stop` appears on exit. Both cold
  launches must produce the same sequence.
- Fail: `probe_bootstrap_failed`, `callback_schedule_failed`, or
  `probe_timeout` appears; `space_loaded_snapshot_incomplete` does not recover
  to the exact success marker; any `unexpected_online_lifecycle` appears; or
  the process crashes before `space_loaded`. An
  `inactive reason=offline_request_missing` marker means the command did not
  forward both script arguments.

The `space_loaded` marker is not evidence of a garage, a controllable tank,
battle UI, combat rules, bots, or normal battle fidelity.
