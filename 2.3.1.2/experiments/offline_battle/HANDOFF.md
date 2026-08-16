# Where this stands

Current candidate: `dist/org.peng.offline_2312_battle_0.12.7.wotmod`,
built and validated.

## Deploy and run

```bash
V='{f3b03401-2c79-4bba-bfe9-75b1bcbf7f66}'
M=C:\\Games\\World_of_Tanks_NA
cp dist/org.peng.offline_2312_battle_0.12.7.wotmod ~/Downloads/
prlctl exec $V cmd /c del /q $M\\mods\\2.3.1.2\\org.peng.offline_2312_battle_*.wotmod
prlctl exec $V cmd /c copy /y \\\\Mac\\Home\\Downloads\\org.peng.offline_2312_battle_0.12.7.wotmod $M\\mods\\2.3.1.2\\
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

- 0.10.3: the 0.10.2 run died in combat setup:
  `vehicle_collision.prepare` raised because this client's BigWorld has
  no `WGBspCollisionModel`, so `ModelHitTester.loadBspModel` is dead
  code here and descriptor-local hit tests can never work. The stock
  `Vehicle.collideSegmentExt` is Python on 2.3.1.2 and runs
  `appearance.collisions.collideAllWorld` at the drawn compound-model
  pose, the pose this runtime owns, so the whole owned-pose detour was
  unnecessary. `vehicle_collision.py` is removed and every shell hit
  test uses the stock route again. The earlier claim that the native
  hit test sits at the spawn pose was wrong; the idler miss is back on
  the open list without an explanation.

## What 0.11.0 adds

The 0.10.3 run proved both fire directions and moving bots. This batch
answers its four field reports:

- Bots integrate every render frame with the real frame dt; decisions
  and probes keep the mature 0.0975 s cadence. The 10 Hz pose stepping
  was the visible jitter.
- Enemy `matrix`/`position` reads now resolve to the runtime-owned live
  matrices through the same class patch that already serves the player,
  so the minimap and every stock reader follow the driven hulls.
- Enemy shells scatter with the mature per-shot seeded gaussian
  (`dispersed_angles`, from bot_runtime), so they no longer land on the
  aim point every time. The turret presentation keeps the aimed pose.
- Player module damage runs the copied `critical_damage` law:
  `apply_direct` on every enemy shell, a 0.5 s `tick_repair` /
  `tick_fire` loop, DAMAGE_INFO publication through the stock
  `showVehicleDamageInfo`, and stat factors from the copied law. A
  broken track now shows on the damage panel, repairs itself over the
  copied repair time, and the repaired transition restores mobility.
  Fire burns one health tick per second and stops by the copied clock.
- `collideSegmentExt` part identities are TankPartIndexes ints on this
  client; `damage.part_name` translates them, so a track or idler hit
  finally counts as a chassis hit. This is the likely answer to the old
  idler report.

Known gaps kept small: no repair-progress percent bar yet
(`updateDestroyedDevicesIsRepairing` semantics unproven), no consumable
kits, and the ammo-rack death event is not yet a special explosion.

## What 0.11.1 adds

The 0.11.0 run validated smooth bot motion, both-way hits, the broken
track on the player's damage panel and the dispersion. This batch takes
its reports:

- Enemy minimap icons: the live matrix now exists from spawn, so the
  minimap binds the matrix the bots move instead of the native spawn
  transform it bound before the bots started.
- Enemy modules: shells on an enemy now run the same copied
  critical_damage law as on the player. A tracked or engine-dead bot
  stops (the mature bot rule), partial damage scales its throttle, its
  repair clock runs at the decision cadence, and fire burns its health.
- The player's wreck stops: death zeroes the drive input and the stat
  factors, so the corpse no longer turns.
- Enemy turret binding: the stock TurretGunRotationAssembler lines
  (`turretMatrix.localMatrix = compoundModel.node(TURRET)`) are applied
  once per enemy, in case the assembler never ran for a client-created
  vehicle. `turret_provider` traces now log the actual node yaw, so the
  next log says whether the model consumes the provider.
- Enemies stop shooting a dead player.

Known open, deliberately deferred: HE through a moving enemy exploding
behind it. The whole flight is precomputed at fire time against the
poses of that moment; the copied `projectile_manager` is the fix and is
the next wiring step.

## What 0.11.2 adds

The 0.11.1 log answered three questions with hard evidence:

- The turret provider carries our aim (`read_back`) but the node yaw
  equals the hull yaw: nothing consumes the provider for a
  client-created enemy, and `.localMatrix` does not exist on this
  build's provider, so the assembly-manager binding is editor-only
  machinery. The visible joints are now driven the way the stock
  hangar SimpleTurretRotator does it:
  `compoundModel.node(TankNodeNames.TURRET_JOINT).local = matrix`,
  where the matrix carries the full joint transform (turret pitch and
  position, rotation pre-multiplied). Same for the gun joint.
- Enemy track hits never arrived: collision part identities past GUN
  (track pairs, wheels) fold to `chassis` now. The old idler report is
  most likely this same missing fold.
- Downhill coasting keeps speed on a 13-degree slope by the copied
  law's own design (`coast_step` shows it); braking with S needs its
  own field test before touching any law.

The always-on 0s status icon is instrumented: `_updateStun` and
`_updateDebuff` on the damage panel log their payloads (`ui_stun`,
`ui_debuff`), so the next log names the pusher and the values.

## What 0.12.0 adds

The 0.11.2 log: `.local` binding succeeded but the node still follows
only the hull, and the DamagePanel stun/debuff probes never fired, so
the 0s icon is fed elsewhere. This batch:

- Consumables. The player carries a small repair kit, a small first
  aid kit and a hand extinguisher; the panel learns them through
  updateVehicleAmmo, using one sends
  cell.vehicle_changeSetting(ACTIVATE_EQUIPMENT, id) into the bridge,
  and the effect is the copied law (repair_device, restore_crew,
  use_extinguisher) with events on the damage panel. This answers the
  field report: a dead driver or broken track was permanent without a
  kit.
- Enemy tracks scroll while the bots drive (updateTracksScroll from
  the copied track_scroll law).
- The status probe moved down to _ActionScriptTimer.showStatus, which
  every shown status passes through (`ui_status_show`).
- An own-turret diagnostic samples the player's gun rotator yaw, the
  provider yaw and the actual TURRET_JOINT node yaw five times
  (`own_turret`), deciding whether the provider chain works for anyone
  offline. The kill-cam SimulatedVehicle proves client-only provider
  consumption works in retail, so this narrows what our battle lacks.

## What 0.12.1 adds

The 0.12.0 log settled the turret mystery: rotator and provider agree
while the node stays at hull yaw, for the player too, so offline
nothing consumes the providers and the node.local drive is the only
visible route (the choppy enemy rotation confirmed it works). This
batch:

- `turret_rig.py` drives every vehicle's TURRET_JOINT and GUN_JOINT at
  render rate: the player snaps to the gun rotator each frame, the
  enemies slew toward their aim at the descriptor's real turret
  rotation speed, so their rotation stops stepping.
- Running-gear stand-in: when the stock collision component reports
  nothing (it carries no chassis geometry on this client), the shell is
  tested against the chassis bbox at the drawn pose and reports a
  chassis layer with a real track material. An idler shot finally
  registers, absorbs by the copied track law, and can break the track.
- Unpenetrated high-explosive hits roll module and crew damage through
  the copied by_explosion saving throws, both directions.
- The hit-direction indicator now receives the world yaw the cell
  normally sends; the UI subtracts the camera itself, every frame.
- The status probe installs at onBecomePlayer, early enough to catch
  the push that shows the always-on 0s icon at panel start.

## What 0.12.2 adds

The 0.12.1 log: the chassis stand-in registers (`part=chassis`), but
every material this client returns carries `extra=None`, so the copied
track saving throw never saw a device. And the 0s icon's feeder is the
panel's `_updateThunderStrike` stun source: a THUNDER_STRIKE state with
duration 1.0 exists at panel start; the probe now logs its data and
caller (`ui_thunder`).

- Chassis layers now carry a `_TrackMaterial`: the real armor plus the
  track device in `extra`, left or right by the hit side, the same
  stand-in pattern the law uses for interior devices. A track or idler
  hit can finally break a track, by shell or by HE blast, both
  directions. The damage panel folds the law's track names back to this
  client's one chassis extra.
- The player's turret rig slews like the enemies' instead of snapping
  to the rotator's stepped value, so its rotation stops twitching.

Hit direction stays under investigation: world yaw was still reported
wrong; the `hit_direction` log lines carry every candidate frame, so
one controlled observation (attacker dead ahead, camera straight) will
name the right one.

## What 0.12.3 adds

The 0.12.2 log explained the death and the icon:

- The player burned to death: the first hit lit the fuel tank, the fire
  burned about half the health pool by the copied clock, and this
  client's 50 DAMAGE_INFO codes carry no fire entries, so no warning
  ever showed. `Vehicle.isOnFire` reads a server-fed dynamic component,
  so critical_control now pushes VEHICLE_VIEW_STATE.FIRE directly and
  the fire warning shows and clears.
- The always-on 0s stun icon: BigWorld.serverTime() is -1 offline, and
  the panel's stun duration is max(0 - serverTime, 0) = 1.0 with an
  empty source list. The getter is patched to report 0 when no stun
  source exists. Neither _updateStun nor _updateThunderStrike ever
  fired; the arithmetic was the whole story.
- The hit arrow was flipped half a turn per the field observation; the
  sent yaw now carries + pi.
- Track breaks still unproven: the run's logged hits never crossed
  chassis geometry. The layer budget is 24 now and a chassis crossing
  logs `track_layer_present`, so the next deliberate idler shot names
  itself either way.

## What 0.12.4 adds

The 0.12.3 run confirmed the arrow flip and the stun fix in play, and
one log line ended the track mystery:

    hit_layer part=chassis dist=9.83 armor=5.0 factor=0.0
    extra=rightTrack0Health

The real track materials DO carry their device, but this client numbers
them (`rightTrack0Health`) while the copied law tables know
`rightTrackHealth`. `damage.law_track_name` folds the numbered names,
so a track hit finally rolls a real saving throw with the material's
own chance. The box stand-in keeps covering segments the collision
component misses entirely.

Enemy track animation stays open: `updateTracksScroll` no-ops without a
`trackScrollController`, which nothing creates offline. The own-turret
diagnostic now logs a `scroll_probe` line with the filter's scroll
attribute names and the controller value, deciding the route next run.

## What 0.12.5 adds

The 0.12.4 log proved the track crit lands (`crits=['rightTrackHealth']`)
— a hit damages the track pool and several hits sever it, which is the
law; the log now prints the target's device pools per hit so the drain
is visible. The other three reports each had a mechanism:

- The extinguisher key never sent anything: the panel's canActivate
  reads Vehicle.isOnFire, a server-fed dynamic component that is never
  present offline. isOnFire is Python and now also reports the copied
  law's own is_on_fire, so the extinguisher activates and the stock
  fire consumers agree.
- Auto-repair had no countdown: critical_control now publishes the
  stock updateDestroyedDevicesIsRepairing with progress and time left
  while a destroyed device regenerates to critical.
- A yellow engine at half mobility plus an unhealed driver is the
  copied law itself; the medkit is the cure, and both kits now have
  working gates.
- Track animation: scroll_probe showed the controller exists
  (PyTrackScroll) and the filter carries writable-looking
  leftTrackScroll/rightTrackScroll; both routes are fed now.

## What 0.12.6 adds

The field report was right and the law was innocent: the player's
mobility factor was multiplied into the velocity every 0.1 s tick, so a
yellow engine's 0.5 decayed the speed geometrically to a crawl within a
second. The factor now scales the drive intent once, the same rule the
bots already used, so a yellow engine costs half the drive power, not
the whole tank.

## What 0.12.7 adds

A deliberate divergence from the copied file, on field evidence: the
0.12.6 log showed a damaged track regenerating 20 -> 40 within three
seconds, which made severing nearly impossible at range, and retail
never regenerates a damaged module. `critical_damage.tick_repair` now
repairs only devices in the destroyed set (up to critical, as before);
a damaged one keeps its damage until a repair kit. This is the one
edit inside a copied law file, made because the copied behavior
contradicts the retail rule the port is meant to reproduce.

## What to check on the next run

Check in this order and stop at the first failure; the step trace will
name the vehicle and step.

1. The battle starts and three enemies appear, then drive off along a
   route instead of standing.
2. Bots do not pile into each other or into you; a blocked bot backs
   out instead of grinding a wall.
3. Enemy fire starts after they decide to engage, and their shells hit
   your hull where it actually is. Also shoot a moving enemy: a hit on
   a moved hull proves appearance.collisions follows the driven model.
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
- A hit on the rear idler did not register in the 0.9.x runs. The
  old spawn-pose explanation is withdrawn; needs fresh evidence
  from hit_layer logs on a deliberate idler shot.
- No fire, no crew injury effects, no ramming damage, no HE splash
  beyond the direct-hit law.
- No battle result, so a finished fight has no ending.
- Borderless-window flicker and colour, untouched and deliberately
  deferred.
