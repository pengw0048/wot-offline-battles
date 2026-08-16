# Where this stands

Current candidate: `dist/org.peng.offline_2312_battle_0.8.0.wotmod`,
built and validated, **not deployed**: the VM was off.

## Deploy and run

```bash
V='{f3b03401-2c79-4bba-bfe9-75b1bcbf7f66}'
M=C:\\Games\\World_of_Tanks_NA
cp dist/org.peng.offline_2312_battle_0.8.0.wotmod ~/Downloads/
prlctl exec $V cmd /c del /q $M\\mods\\2.3.1.2\\org.peng.offline_2312_battle_0.7.1.wotmod
prlctl exec $V cmd /c copy /y \\\\Mac\\Home\\Downloads\\org.peng.offline_2312_battle_0.8.0.wotmod $M\\mods\\2.3.1.2\\
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

Copied but not wired: `spotting` (nothing is hidden by view range) and
`gun_mechanics` (dispersion still comes from the stock gun rotator).

Deliberately not copied: `critical_damage` and the internal hit layouts,
because they rest on 251 per-vehicle interior geometries built for the
0.9.22 vehicle set. That is version data, not law.

## What to check on the next run

The batch is large, so check in this order and stop at the first
failure; the step trace will name the vehicle and step.

1. The battle starts and three enemies appear.
2. Enemy turrets track you as you move.
3. Their shells reach you rather than passing over.
4. You cannot drive through an enemy hull.
5. Shooting an enemy takes health, and the hit sound matches the result.
6. Some hits report a crit: look for `crits=[...]` in the log.
7. Killing an enemy does not end the client.
8. Being killed does not end the client.
9. Taking an engine or track crit slows you down.

`python.log` markers: `enemies_spawned`, `enemy_ai_started`,
`shell_hit ... crits=`, `enemy_killed`, `player_hit ... crits=`,
`motion_state ... contacts=`.

## If a death still ends the client

The next suspect is the death effect itself: `currentState.effect`
played by `CompoundAppearance.onVehicleHealthChanged`, and
`inputHandler.onVehicleDeath`. The step trace will show which was
running. Neither is patched, because there is no evidence against them.

## Still open

- Enemy vehicles do not drive.
- No fire, no crew injury effects, no ramming damage, no HE splash
  beyond the direct-hit law.
- No battle result, so a finished fight has no ending.
- Borderless-window flicker and colour, untouched and deliberately
  deferred.
