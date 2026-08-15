# World of Tanks 2.3.1.2 PlayerAvatar and ClientArena probe

This is not an offline battle. It is the next isolated interface experiment
after the stock `OfflineEntity + FreeCamera` loader proof.

On the pinned North American HD client `v.2.3.1.2 #919`, this package routes
the explicit stock `offline` request through the client's own
`OfflineMapCreator`. The stock creator owns space and entity creation. The
probe does not construct an Avatar, Arena or Vehicle itself.

The single gate answers whether the exact client can reach:

```text
game.start -> helpers.OfflineMode.onStartup
           -> routed helpers.OfflineMode.launch
           -> stock OfflineMapCreator.create
           -> real PlayerAvatar -> real ClientArena -> loaded space
```

The expected stock offline-map boundary is deliberately part of the verdict:
`playerVehicleID == 0`, no `AvatarInputHandler`, and a `CursorCamera`. A pass
therefore proves a real Avatar/Arena context but does not prove a tank, input,
battle GUI, native control, firing, damage, bots, results or a second round.

## Build and test

```sh
cd /path/to/wot-offline-battles/2.3.1.2/experiments/avatar_arena_probe
python3 -m unittest discover -s tests -p 'test_*.py'
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py
PYENV_VERSION=2.7.18 pyenv exec python build_wotmod.py
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py \
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.0.wotmod
python3 tools/validate_wotmod.py \
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.0.wotmod
```

The release contains one direct CPython 2.7 `mod_*.pyc` loader entry and no
Python source.

## Windows experiment

Remove the earlier offline probe packages before installing this one. Copy
`org.peng.offline_2312_avatar_arena_probe_0.1.0.wotmod` to
`mods/2.3.1.2/`, then run exactly:

```bat
cd /d C:\Games\World_of_Tanks
win64\WorldOfTanks.exe --script-arg avatarArenaProbe --script-arg offline --script-arg spaces/01_karelia
```

Every token needs its own `--script-arg` because the executable forwards only
the next token into Python `sys.argv`. The `avatarArenaProbe` token prevents
this package from changing an ordinary stock offline launch.

Search `python.log` for `[OFFLINE_2312_AVATAR_ARENA_PROBE]`.

A pass has these milestones in order:

1. `route_installed target=helpers.OfflineMode.launch`;
2. `native_create_requested ... gameplay=ctf` is represented by the selected
   CTF `arena_type_id`;
3. `avatar_seen ... in_world=True`;
4. `client_arena_seen geometry=01_karelia gameplay=ctf`;
5. `space_loaded ... camera=CursorCamera`;
6. exactly one `gate_pass gate=player_arena`, with `player_vehicle_id=0` and
   `input_handler=None`.

The Avatar/Arena verdict and the shutdown verdict are separate. Any
`gate_fail`, `probe_bootstrap_failed`, native crash or transition to the login
screen fails the Avatar/Arena gate.

On process exit, the wrapper stops its callback, restores both routes, calls
the stock creator's `destroy()`, and then calls the original `game.fini()`.
For a run that reached `gate_pass`, the cleanup observation must include all
of these markers:

1. `probe_stop reason=game_fini`;
2. `cleanup_destroy_begin creator_active=True`;
3. `cleanup_destroy_returned creator_active=False`;
4. `cleanup_original_fini_returned`.

These markers alone do not prove clean native teardown. The stock
`OfflineMapCreator.destroy()` catches its own exceptions, so the same log must
also contain no `OfflineMapCreator.destroy(): FAILED`, `cleanup_failed`,
`Traceback`, `AttributeError`, `VehicleDeinitFailure` or native crash. Validate
three cold create-to-exit cycles. If creation passes but exit does not, report
`Avatar/Arena gate PASS, cleanup gate FAIL`; do not hide the failure with a
synthetic input handler or teardown bypass. The original global shutdown is
the final cleanup owner. Do not use an online account for this experiment.

## Sky-flicker comparison

This probe is also a controlled renderer comparison. Unlike the stock free
camera, `PlayerAvatar.onSpaceLoaded()` follows the real Arena weather path.
Record whether the bright-sky flicker is unchanged, reduced or gone, but do
not make renderer behavior part of the Avatar/Arena gate.

For the stock free-camera baseline, use a cold launch with the exact CTF mask:

```bat
win64\WorldOfTanks.exe --script-arg offline --script-arg spaces/01_karelia --script-arg gameMode --script-arg ctf
```

If that baseline still flickers, use a fresh process for each single-variable
test: press `T` once, then in a new process `P` once, then in another new
process `C` once. These are the stock FreeCamera toggles for temporal AA,
post-processing and cinematic post-processing. Do not use the mouse wheel;
the #919 stock `OfflineMode.adjustSpeed()` watcher conversion raises a
`TypeError` and is unrelated to the Avatar/Arena gate.

Disabling a render feature is diagnostic only. It must not become the formal
battle port's default fix; the real battle lifecycle may initialize the same
renderer state correctly.

## Next gate

Do not add a synthetic Vehicle to this package. The exact offline-map branch
intentionally does not start `guiSessionProvider`, while modern Vehicle
appearance requires its dynamic appearance cache. The next independent gate
must prove the missing normal battle-session boundary before creating one real
own `Vehicle`.
