# Where this stands

Current candidate: `dist/org.peng.offline_2312_battle_0.10.2.wotmod`,
built and validated.

## Deploy and run

```bash
V='{f3b03401-2c79-4bba-bfe9-75b1bcbf7f66}'
M=C:\\Games\\World_of_Tanks_NA
cp dist/org.peng.offline_2312_battle_0.10.2.wotmod ~/Downloads/
prlctl exec $V cmd /c del /q $M\\mods\\2.3.1.2\\org.peng.offline_2312_battle_*.wotmod
prlctl exec $V cmd /c copy /y \\\\Mac\\Home\\Downloads\\org.peng.offline_2312_battle_0.10.2.wotmod $M\\mods\\2.3.1.2\\
prlctl exec $V cmd /c del /q $M\\python.log
```

Then launch from the client root:

```
win64\WorldOfTanks.exe --script-arg offlineBattle --script-arg offline --script-arg spaces/01_karelia
```

## The crash chain, and what closed it

One root cause explains both the 0.7.1 startup crash and both death
crashes.

`BigWorld.createEntity` gives a Vehicle no server-fed interpolation
chain. `filter.syncGunAngles` and `filter.syncStabilisedYPR` submit
their first sample into that missing chain and fault the client. This
holds for every vehicle, the player's and every enemy.

- **0.7.1** let remote vehicles run the stock `set_gunAnglesPacked`,
  which reaches that call. The client died at the first enemy's
  `startVisual`, exactly where the log stops.
- **0.7.2** put the filter proxy back in front of every vehicle, and
  animates enemy turrets through the appearance turret and gun matrix
  providers instead, the way the 0.9.22 port animates a remote turret.
- **0.7.3** removed the last unscoped caller,
  `CompoundAppearance.__onModelsRefresh`, the destroyed-model load. That
  is why both deaths ended the client. A destroyed vehicle now keeps its
  intact model.

The README carries the full caller table. Check any new native call
against it.

## What 0.8.0 adds

Every reusable module of the 0.9.22 port is now here, law unchanged:
`ballistics`, `projectile_runtime`, `tank_collision`, `device_damage`,
`spotting`, `gun_mechanics`, on top of `motion`, `world_collision`,
`suspension` and `combat_rules`.

Newly wired:

- Hulls collide with hulls. You cannot drive through an enemy.
- Module crits: engine, tracks, gun, ammo bay, optics, crew. The
  attacker sees the chassis, gun or device hit flag.
- A shot-out engine or track costs the player mobility and traverse.
- The shell trajectory runs through the copied ballistics.

Copied but not wired: `spotting` (nothing is hidden by view range),
`gun_mechanics` (dispersion still comes from the stock gun rotator),
`critical_damage`, `projectile_manager`, `foliage` and
`destructibles_sensor`.

## What 0.10.x adds

- 0.10.0: the whole `ai/` package is copied, and `bot_control.py`
  drives the enemies with it.
- 0.10.1 is a self-review pass over that wiring, against the mature
  caller `bot_runtime.py`:
  - The port script had skipped `ai/reviewed_routes_20260811.py`, which
    `ai/maps.py` imports at module level, so 0.10.0 would have died at
    mod load. A test now walks every internal import.
  - The player now rides in each bot's `neighbours`, so the driver's
    separation steering sees the human.
  - The mature `_traffic_throttle` is copied: a follower yields to the
    vehicle ahead, the lower id has right of way at a crossing, and
    every bot yields to a human.
  - A blocked travel direction now calls `driver.remember_failure`, so
    the copied stuck recovery can run; a reversing bot probes the rear.
  - Enemy fire is gated by the planner's `fire_allowed`.
  - Enemy shells hit-test the player through the pose this runtime
    owns, not through the native filter left at the spawn pose. This is
    the same route that fixed idler hits on the enemies.
  - Bot velocities feed the tank-contact law, so a moving bot pushes
    with momentum.
- 0.10.2 fixes the two faults the first 0.10.1 run measured, which
  between them froze the bots, latched W, and ate every shell hit:
  - `wg_getMatInfoNearPoint` returns five items on this client
    (`collided, hitPoint, surfNormal, matKind, fileName`, proved from
    the stock EffectMaterialCalculation bytecode), not the #1513 seven.
    The strict decoder raised on every probe that touched a solid,
    1145 times in one session, aborting bot and motion ticks mid-loop.
    Both item identities now decode as None and the registries fail
    closed, so nothing is crushable until real destructibles wiring.
  - `ModelHitTester.localHitTest` returns nothing until the part BSPs
    are loaded. `vehicle_collision.prepare` now runs each descriptor's
    `getHitTesterManagers()[i].loadHitTesters()` at combat start, the
    step the mature port does in `prepare_descriptor`; without it every
    owned-pose hit test raised and no shell could hit any vehicle.

## What to check on the next run

Check in this order and stop at the first failure; the step trace will
name the vehicle and step.

1. The battle starts and three enemies appear, then drive off along a
   route instead of standing.
2. Bots do not pile into each other or into you; a blocked bot backs
   out instead of grinding a wall.
3. Enemy fire starts after they decide to engage, and their shells hit
   your hull where it actually is, tracks and idler included.
4. Shooting an enemy takes health, and the hit sound matches the result.
5. Killing an enemy does not end the client; the wreck stays put.
6. Being killed does not end the client.
7. Do the enemy minimap icons and markers follow their moving hulls?
   The entity position is never written offline, so this observation
   decides whether a position publish path is needed.

`python.log` markers: `enemies_spawned`, `bot_control_started`,
`bot_command`, `enemy_ai_started`, `shell_hit ... crits=`,
`enemy_killed`, `player_hit ... crits=`, `motion_state ... contacts=`.

## If a death still ends the client

The next suspect is the death effect itself: `currentState.effect`
played by `CompoundAppearance.onVehicleHealthChanged`, and
`inputHandler.onVehicleDeath`. The step trace will show which was
running. Neither is patched, because there is no evidence against them.

## Still open

- Enemy turret models still do not rotate visually; the providers hold
  the aim.
- The coasting glide after releasing W, the permanent stun panel and
  the hit-arrow frame, all carried over from the 0.9.x runs.
- No fire, no crew injury effects, no ramming damage, no HE splash
  beyond the direct-hit law.
- No battle result, so a finished fight has no ending.
- Borderless-window flicker and colour, untouched and deliberately
  deferred.
