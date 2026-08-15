# World of Tanks 2.3.1.2 PlayerAvatar and ClientArena probe

This is not an offline battle. It is the next isolated interface experiment
after the stock `OfflineEntity + FreeCamera` loader proof.

On the pinned North American HD client `v.2.3.1.2 #919`, this package routes
the explicit stock `offline` request through the client's own
`OfflineMapCreator`. The stock creator owns space and entity creation. The
probe does not construct an Avatar, Arena or Vehicle itself.

Version 0.1.0 is superseded. Windows evidence showed that #919's stock creator
passes an empty initial-property dictionary to `BigWorld.createEntity()` and
assigns the Arena fields only after native component initialization has
already called `PlayerAvatar.hasBonusCap()`. Native creation logged the
resulting `AttributeError` but continued, so 0.1.0 could emit a false
`gate_pass`. Version 0.1.1 temporarily applies the same pre-super property
preparation boundary used by the mature 0.9.22 port, limited to the synchronous
stock `creator.create()` call. It restores all four class routes in `finally`
and requires the native bonus-cap check, `onEnterWorld()` and
`onBecomePlayer()` to be observed and exception-free through their complete
Python methods.

The single gate answers whether the exact client can reach:

```text
game.start -> helpers.OfflineMode.onStartup
           -> routed helpers.OfflineMode.launch
           -> stock OfflineMapCreator.create
           -> real PlayerAvatar -> real ClientArena -> loaded geometry
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
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.1.wotmod
python3 tools/validate_wotmod.py \
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.1.wotmod
```

The release contains one direct CPython 2.7 `mod_*.pyc` loader entry and no
Python source.

## Windows experiment

Remove the earlier offline probe packages before installing this one. Copy
`org.peng.offline_2312_avatar_arena_probe_0.1.1.wotmod` to
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
2. `display_state ...` records the read-only native display mode;
3. `native_create_requested ... gameplay=ctf` is represented by the selected
   CTF `arena_type_id`;
4. `avatar_init_observed preseed_applied=True init_returned=True`, with
   `has_bonus_cap_calls` greater than zero and
   `has_bonus_cap_exceptions=0`;
5. `avatar_lifecycle_observed` reports positive, matching call/return counts
   and zero exceptions for both `onEnterWorld` and `onBecomePlayer`;
6. `avatar_seen ... in_world=True`;
7. `client_arena_seen geometry=01_karelia gameplay=ctf`;
8. `geometry_loaded ... camera=CursorCamera`;
9. exactly one `gate_pass gate=player_arena`, with `player_vehicle_id=0`,
   `input_handler=None`, and the observed `player_space_loaded` value. The
   untouched #919 stock offline-map path is expected to report `False`.

`space_lifecycle_missing reason=offline_battle_session_not_started` is an
expected boundary marker when that value is `False`, not a success claim. The
stock offline-map branch deliberately skips `guiSessionProvider.start()`; the
normal battle arena-load controller therefore never calls
`PlayerAvatar.onSpaceLoaded()`. If a runtime instead reports `True`, preserve
the log and investigate the additional lifecycle rather than failing this
narrow gate automatically. The next independent gate must enter the stock
battle-session path instead of calling `onSpaceLoaded()` directly or
fabricating its progress bit.

The Avatar/Arena verdict and the shutdown verdict are separate. Any
`gate_fail`, `probe_bootstrap_failed`, Traceback, AttributeError, native crash
or transition to the login screen fails the Avatar/Arena gate even if a later
marker says `gate_pass`.

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

## Camera and borderless rendering observations

The #919 stock creator hard-codes the Avatar and CursorCamera target at
`(50, 0, 50)`. Karelia terrain at that horizontal point is about 19.6 metres
above the target, so the current view is below the terrain and is not an
upside-down framebuffer. Version 0.1.1 records this as a known stock-viewer
boundary and does not move the camera. Camera ground placement belongs in a
separate experiment after geometry load.

Use exclusive fullscreen as the stable visual baseline for this interface
probe. If exclusive fullscreen is stable but borderless flickers, treat that
as an independent presentation-path issue. Compare one variable per cold
launch: first in-game VSync, then the per-application Windows 11 windowed-game
optimization (if present), then windowed VRR/G-SYNC/FreeSync. Do not interpret
a single screenshot as flicker evidence. The `display_state` line records
window mode, resolution, video-mode index, monitor, borderless parameters,
VSync, triple buffering, DRR and gamma without changing them.

For the stock free-camera baseline, use a cold launch with the exact CTF mask:

```bat
win64\WorldOfTanks.exe --script-arg offline --script-arg spaces/01_karelia --script-arg gameMode --script-arg ctf
```

Do not use the mouse wheel; the #919 stock `OfflineMode.adjustSpeed()` watcher
conversion raises a `TypeError` and is unrelated to either gate.

## Next gate

Do not add a synthetic Vehicle to this package. The exact offline-map branch
intentionally does not start `guiSessionProvider`, while modern Vehicle
appearance requires its dynamic appearance cache. The next independent gate
must prove the missing normal battle-session boundary before creating one real
own `Vehicle`.
