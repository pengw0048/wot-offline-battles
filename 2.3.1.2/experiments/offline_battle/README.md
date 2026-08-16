# 2.3.1.2 offline battle (playable vertical slice)

This experiment turns the 2.3.1.2 client-only map bootstrap into a playable
battle: real `PlayerAvatar`, real `Vehicle`, the stock
`BattleSessionProvider`, `AvatarInputHandler` and `ArcadeCamera`. It follows
the mature 0.9.22 architecture (`offline_lan_0922`) and changes only the
interfaces that 2.3.1.2 actually changed.

## How it works

1. `helpers.OfflineMode.launch` is routed into the runtime. The stock
   `OfflineMapCreator.create()` still owns the space, the geometry mapping
   and the Avatar entity. The viewer `CursorCamera` setup is replaced by a
   bootstrap camera aimed at the spawn so terrain streams there.
2. `PlayerAvatar.__init__` is preseeded with real arena identity
   (`REGULAR` / `RANDOM`) and the avatar receives a strict
   `AvatarServerBridge` as `fakeServer`. `PlayerAvatar.__getattribute__`
   resolves `base` / `cell` / `server` to it; `Vehicle.cell` resolves to the
   same bridge. The bridge has no catch-all: an unexpected mailbox call
   raises `AttributeError`.
3. `OfflineMapCreator.SetActive(False)` is applied only across the stock
   `onBecomePlayer` call, so the stock battle session, controllers,
   appearance cache and `AvatarInputHandler` are created exactly as in an
   online battle. Ownership returns to the creator afterwards.
4. `Account.g_accountRepository` is the real `_AccountRepository`, so the
   stock `intUserSettings` / `AvatarSyncData` chain runs; the bridge answers
   `CMD_GET_AVATAR_SYNC` and the int-settings commands.
5. Once terrain accepts a `BigWorld.wg_collideSegment` probe at the mature
   Karelia CTF spawn, one real `Vehicle` entity is created through
   `BigWorld.createEntity` with the full modern `Vehicle.def` property set
   (`PUBLIC_VEHICLE_INFO`, wheels, perks and effect fields included). The
   modern roster dict (`VEHICLES_INFO` schema) goes through
   `ClientArena.updateVehiclesList()`, then `playerVehicleID` is delivered
   with the stock `set_playerVehicleID` notifier.
6. The stock `ArenaSpaceLoadListener` calls `onSpaceLoaded()` and the arena
   load controller; the runtime calls `setClientReady()` once init
   completes, then publishes `ARENA_PERIOD.BATTLE` through
   `Avatar.updateArena`. The four init bits and `PLAYER_READY` are set only
   by stock code paths.
7. Movement and aiming stay stock: `AvatarInputHandler.start()` binds the
   arcade control mode, `PlayerAvatar.moveVehicle` feeds
   `Vehicle.notifyInputKeysDown` (native `WGVehicleFilter` prediction) and
   `cell.vehicle_moveWith(flags)` lands in the bridge.
8. Exit: `game.fini` destroys the Vehicle entity, retires the avatar with
   the stock `onBecomeNonPlayer`, then runs `OfflineMapCreator.destroy()`
   and removes every class patch and the account repository.

## Layout

- `src/res/scripts/client/gui/mods/mod_offline_2312_battle.py` — argv
  parsing and route install/restore.
- `.../offline_battle_2312/runtime.py` — bootstrap state machine and
  lifecycle patches.
- `.../offline_battle_2312/avatar_server.py` — strict Avatar/Vehicle
  mailbox bridge.
- `.../offline_battle_2312/entity_setup.py` — Vehicle property, roster and
  spawn schemas (pure data, unit-tested).
- `.../offline_battle_2312/account_setup.py` — real client-side account
  repository install/remove.

## Build and test

```bash
python3 -m unittest discover -s tests
python2.7 build_wotmod.py
python3 tools/validate_wotmod.py dist/org.peng.offline_2312_battle_0.3.0.wotmod
```

## Current state (validated on the 2.3.1.2 Windows client)

Working: the battle starts from the offline map, one real Vehicle entity
spawns on the Karelia CTF spawn with its real appearance, the stock
BattleSessionProvider, AvatarInputHandler, ArcadeCamera, gun rotator and
battle HUD all run, `is_on_arena` is true, the avatar reaches
`init_progress=63`, exit teardown is clean and there is no crash.

Not working: the vehicle does not move. Keyboard input is proven to reach
`Vehicle.notifyInputKeysDown` with the right directions, but the native
`WGTankPhysics` produces no pose samples: position and velocity stay
exactly zero.

This is a known boundary, not a missing call. The mature 0.9.22 offline
port reached the same conclusion on its client and records it in
`battle_runtime.py`:

> A remote #1513 Vehicle has no retail server stream, so treating its
> WGVehiclePhysics as authoritative leaves movement inputs without pose
> samples.

That port therefore integrates vehicle motion itself (`vehicle_physics.py`
plus terrain and collision probes) and owns the pose, using the native
filter only for presentation. Driving in this port needs the same work:
port the mature motion law, probe terrain with
`BigWorld.wg_collideSegment`, and write the resulting pose each tick.

## Native capability, measured on 2.3.1.2

Probed in-game through the real method tables rather than inferred from
another client version.

The native `WGVehicleFilter` is alive and usable for presentation: it
carries `bodyMatrix`, `turretMatrix`, `stabilisedMatrix`,
`groundPlacingMatrix`, track scroll and `interpolateStabilisedMatrix`. It
exposes `notifyInputKeysDown`, `transferInput`, `transferInputAsVehicle`
and `setScriptInputCallback`, but **no position setter**: `setPosition`
is absent from its method table on this client.

The native `WGTankPhysics` can be created, configured and written to, but
it never simulates offline:

| probe | result |
| --- | --- |
| `vehicleID` | 0 after stock setup; writing the entity id succeeds |
| `isFrozen` / `allowFreeze` | false / true; clearing `allowFreeze` changes nothing |
| `staticMode` | already false |
| `movementSignals` after `notifyInputKeysDown(1, 0)` | stays 0 |
| `movementSignals` written directly | reads back 1, vehicle does not move |
| `numLeftTrackContacts` / `numRightTrackContacts` | 0 |
| `touchGround()` | native access violation |
| `speed` getter | native access violation |

Zero track contacts plus a faulting `touchGround` mean the body is not
attached to a physics world. Only the server-driven path attaches it, so
offline the physics accepts state but produces no pose samples.

Conclusion, matching the 0.9.22 port's `native_motion=False`: motion has
to be owned in Python. That port computes the pose with its own
integrator and applies it through the compound model matrix plus an
entity pose overlay, using the native filter only for presentation.

## Scope and known gaps

- Shooting has no projectile authority yet: `vehicle_shoot()` is accepted
  and logged. Bots, damage and battle results are out of scope for this
  slice.
- Only `spaces/01_karelia` has a proven spawn; other maps fall back to the
  stock spawn-point data.
- The battle HUD may be partial: ammo/reload data normally arrives through
  the server-attached `OwnVehicle` component, which does not exist offline.
- Everything above the static tests still requires acceptance on the real
  Windows client.
