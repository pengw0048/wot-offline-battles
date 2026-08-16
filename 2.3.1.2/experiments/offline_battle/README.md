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
7. Input stays stock: `AvatarInputHandler.start()` binds the arcade
   control mode, `PlayerAvatar.moveVehicle` feeds
   `Vehicle.notifyInputKeysDown` and `cell.vehicle_moveWith(flags)` lands
   in the bridge. The runtime reads the direction from
   `notifyInputKeysDown` and integrates the pose itself, because the
   native body never simulates offline.
8. The runtime also publishes what the cell owns: the targeting speeds
   that start `VehicleGunRotator`, and the ammo that lets the client
   shoot.
9. Exit: `game.fini` destroys the Vehicle entity, retires the avatar with
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
- `.../offline_battle_2312/motion.py` — the 0.9.22 motion law, copied.
- `.../offline_battle_2312/motion_driver.py` — owns the pose each tick and
  publishes it to the model, the camera and the speedometer.
- `.../offline_battle_2312/world_collision.py` — the 0.9.22 horizontal
  collision law, copied.
- `.../offline_battle_2312/targeting.py` — the targeting parameters the
  cell normally sends, read from the vehicle descriptor.
- `.../offline_battle_2312/gunnery.py` — ammo publication and the shot
  answer the cell normally gives.
- `.../offline_battle_2312/suspension.py` — the 0.9.22 ground probes and
  four-point hull pose, copied.
- `.../offline_battle_2312/projectiles.py` — shell flight and the impact
  the cell normally reports.
- `.../offline_battle_2312/enemies.py` — enemy vehicles, their roster
  entries and their health.
- `.../offline_battle_2312/combat_rules.py` — the 0.9.22 armour and
  damage law, copied.
- `.../offline_battle_2312/damage.py` — the 2.3.1.2 input adapter for
  that law.
- `.../offline_battle_2312/enemy_ai.py` — enemy turret aim and return
  fire, gated by the planner's fire decision.
- `.../offline_battle_2312/bot_control.py` — the 2.3.1.2 side of the
  copied bot adapter: state dicts in, throttle and turn integrated by
  the copied motion law, plus the mature caller's traffic throttle and
  failure memory.

Law copied from the 0.9.22 port, unchanged, reached through the adapters
above: `motion.py`, `world_collision.py`, `suspension.py`,
`combat_rules.py`, `tank_collision.py`, `device_damage.py`,
`ballistics.py`, `projectile_runtime.py`, `spotting.py`,
`gun_mechanics.py`, and the whole `ai/` package (planner, driver,
navigation, cover, tactical maps and reviewed routes).

## Build and test

```bash
python3 -m unittest discover -s tests
python2.7 build_wotmod.py
python3 tools/validate_wotmod.py dist/org.peng.offline_2312_battle_0.12.3.wotmod
```

## Current state

Validated on the 2.3.1.2 Windows client: the battle starts from the
offline map, one real Vehicle entity spawns on the Karelia CTF spawn with
its real appearance, the stock BattleSessionProvider, AvatarInputHandler,
ArcadeCamera, gun rotator and battle HUD all run, `is_on_arena` is true,
the avatar reaches `init_progress=63`, and exit teardown is clean.

Also validated in play: the tank drives with W/A/S/D, follows the terrain,
the camera follows it, the speedometer reads the real speed, the aim
circle tracks the mouse, and the gun fires and reloads.

Also validated in play: the hull turns beside a rock without sinking into
it, a blocked hull slides along the obstacle, and the shell flies and
explodes where it lands.

Also validated in play: enemy vehicles stand on the map, the shell hits
them and the hit result plays its sound.

Also validated in play: shells damage enemies, shell switching works, and
the enemies shoot back.

Not yet validated in play: a kill without a crash, enemy turret rotation,
and enemy fire that reaches the player.

## Native calls a client-only vehicle cannot take

`BigWorld.createEntity` gives a Vehicle no server-fed interpolation
chain. Two filter calls submit their first sample into that missing
chain and fault the client, for the player's vehicle and for every
remote one:

- `filter.syncGunAngles`
- `filter.syncStabilisedYPR`

Every caller in the client is accounted for:

| caller | handling |
| --- | --- |
| `Vehicle.set_gunAnglesPacked` | scoped filter proxy |
| `Vehicle.__startWGPhysics` | scoped filter proxy |
| `CompoundAppearance.__onModelsRefresh` | unreachable: the model refresh is skipped |
| `Avatar.__onSetOwnVehicleAuxPhysicsData` | deferred, then scoped |

## Device names that differ from the copied law

2.3.1.2 carries one `chassisHealth` device where the 0.9.22 law tables
expect `leftTrackHealth` and `rightTrackHealth`. Everything else, crew
included, uses the same names. `damage.law_devices` translates, so the
copied stat tables keep working without being edited.

Enemy turrets are animated through the appearance turret and gun matrix
providers instead, which is what the 0.9.22 port does for a remote
vehicle.

The native body never simulates offline, so this port owns the pose. That
matches the conclusion the mature 0.9.22 port reached on its own client,
which also integrates vehicle motion itself and uses the native filter
only for presentation. `motion.py` and `world_collision.py` are copies of
that law.

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

- Enemy vehicles drive with the copied planner and driver, but no play
  session has validated it yet. They still do not miss on purpose.
  Battle results are out of scope for this slice.
- Damage covers direct hits and module crits. No fire, no crew injury
  effects, no ramming damage, and no HE splash beyond the direct-hit
  law.
- `spotting.py` and `gun_mechanics.py` are copied but not wired: nothing
  is hidden by view range yet, and dispersion still comes from the stock
  gun rotator.
- Crew injuries and module repair are not wired: a crit is reported and
  costs the stat, but nothing repairs it and no crew message appears.
- A destroyed vehicle keeps its intact model. The destroyed model arrives
  through CompoundAppearance.__onModelsRefresh, which calls
  filter.syncGunAngles directly, and a client-only vehicle faults on that
  call. The death effect, the markers and the feedback all still run.
- The kill camera is off: it replays the killing shot from server
  simulation data this battle never records.
- Only `spaces/01_karelia` has a proven spawn; other maps fall back to the
  stock spawn-point data.
- The ammo bay is a full `gun.maxAmmo` load shared across the gun's shot
  types, because there is no account loadout offline.
- Everything above the static tests still requires acceptance on the real
  Windows client.
