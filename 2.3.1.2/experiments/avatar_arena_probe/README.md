# World of Tanks 2.3.1.2 offline map bootstrap

This development build uses the client's stock `OfflineMapCreator`, real
`PlayerAvatar`, real `ClientArena`, and real CTF arena data. It does not yet
create a `Vehicle` or start the complete battle session.

Version 0.1.2 fixes the visible camera problem found on Karelia. The stock
viewer targets `(50, 0, 50)`, about 19.6 metres below the terrain. This build
copies the mature Karelia CTF team-one spawn `(382, 386)` and base-to-base
heading, waits for geometry streaming, obtains the current terrain point with
the 2.3.1.2 collision API (`collision.closestPoint`), and updates the existing
`CursorCamera.target` and `CursorCamera.source`. The terrain height is read at
runtime rather than copied from an older client.

The constructor adapter also supplies the Arena properties before native
components call `PlayerAvatar.hasBonusCap()`. This fixes the earlier
`arenaBonusType` initialization error without replacing the stock Avatar or
Arena implementation.

## Build

```sh
cd /path/to/wot-offline-battles/2.3.1.2/experiments/avatar_arena_probe
python3 -m unittest discover -s tests -p 'test_*.py'
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py
PYENV_VERSION=2.7.18 pyenv exec python build_wotmod.py
PYENV_VERSION=2.7.18 pyenv exec python tests/smoke_probe_py27.py \
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.2.wotmod
python3 tools/validate_wotmod.py \
  dist/org.peng.offline_2312_avatar_arena_probe_0.1.2.wotmod
```

## Run

Remove earlier `offline_2311`, `offline_2312_poc`, and
`offline_2312_avatar_arena_probe` packages. Copy
`org.peng.offline_2312_avatar_arena_probe_0.1.2.wotmod` to
`mods/2.3.1.2/`, then run:

```bat
win64\WorldOfTanks.exe --script-arg avatarArenaProbe --script-arg offline --script-arg spaces/01_karelia
```

The useful new log line is:

```text
camera_repositioned source=mature_ctf_spawn target=(382.000,<terrain-y>,386.000) yaw=<base-heading> pitch=-25.000
```

If terrain collision has not streamed yet, the build keeps waiting instead of
showing the underground view.

## Remaining implementation work

The current `OfflineMapCreator.Active()` branch deliberately skips
`guiSessionProvider.start(BattleSessionSetup(...))`, `AvatarInputHandler`, and
the ArenaLoadController callback that invokes `PlayerAvatar.onSpaceLoaded()`.
It also starts without `Account.g_accountRepository`, so `intUserSettings` is
absent. The next implementation step is the mature Account-to-Avatar battle
transition, followed by the modern ArenaInfo/TeamInfo vehicle roster and one
real `Vehicle`. Once that exists, the normal ArcadeCamera replaces this
temporary CursorCamera correction.

The latest windowed run was D3D11 mode 0 at 1280x768/120 Hz with VSync and
triple buffering enabled. Exclusive fullscreen was stable. Because the
current viewer has not completed the normal four Avatar initialization steps,
the battle render lifecycle must be restored before changing Windows or GPU
presentation settings in code.
