# Where this stands

Current candidate: `dist/org.peng.offline_2312_battle_0.7.3.wotmod`,
built and validated, **not deployed**: the VM was off.

## Deploy and run

```bash
V='{f3b03401-2c79-4bba-bfe9-75b1bcbf7f66}'
M=C:\\Games\\World_of_Tanks_NA
cp dist/org.peng.offline_2312_battle_0.7.3.wotmod ~/Downloads/
prlctl exec $V cmd /c del /q $M\\mods\\2.3.1.2\\org.peng.offline_2312_battle_0.7.1.wotmod
prlctl exec $V cmd /c copy /y \\\\Mac\\Home\\Downloads\\org.peng.offline_2312_battle_0.7.3.wotmod $M\\mods\\2.3.1.2\\
prlctl exec $V cmd /c del /q $M\\python.log
```

Then launch from the client root:

```
win64\WorldOfTanks.exe --script-arg offlineBattle --script-arg offline --script-arg spaces/01_karelia
```

## What 0.7.2 and 0.7.3 fix

Both fixes come from one root cause found in the 0.7.1 log.

`BigWorld.createEntity` gives a Vehicle no server-fed interpolation
chain. `filter.syncGunAngles` and `filter.syncStabilisedYPR` submit
their first sample into that missing chain and fault the client. This
holds for every vehicle, the player's and every enemy.

0.7.1 let remote vehicles run the stock `set_gunAnglesPacked`, which
reaches that call. The client died at the first enemy's `startVisual`,
which is exactly where the log stops.

- 0.7.2 puts the filter proxy back in front of every vehicle, and
  animates enemy turrets through the appearance turret and gun matrix
  providers instead, the way the 0.9.22 port animates a remote turret.
- 0.7.3 removes the last unscoped caller:
  `CompoundAppearance.__onModelsRefresh`, the destroyed-model load.
  That is why both deaths ended the client. A destroyed vehicle now
  keeps its intact model.

The README carries the full caller table. Check any new native call
against it.

## What to look for on the next run

1. The battle starts and the enemies appear (this regressed in 0.7.1).
2. Enemy turrets track you.
3. Their shells reach you rather than passing over.
4. Killing an enemy does not end the client.
5. Being killed does not end the client.

`python.log` markers, in order: `enemies_spawned`, `enemy_ai_started`,
`shell_hit`, `enemy_killed`, `player_hit`. The step trace now names the
entity each step runs on and covers `Vehicle.onHealthChanged`,
`Vehicle.setIsCrewActive`, `Vehicle.onDeath` and
`Arena.updateVehicleIsAlive`, so a native fault in the death path points
at one vehicle and one step instead of ending the log in silence.

## If a death still ends the client

The next suspect is the death effect itself: `currentState.effect`
played by `CompoundAppearance.onVehicleHealthChanged`, and
`inputHandler.onVehicleDeath`. The step trace will show which one was
running. Neither is patched yet, because there is no evidence against
them.

## Still open

- Enemy vehicles do not drive.
- No fire, module or crew damage; no ramming; no HE splash beyond the
  direct-hit law.
- No battle result, so a finished fight has no ending.
- Borderless-window flicker and colour, untouched and deliberately
  deferred.
