# 2.3.1.1 formal porting baseline

This document is the source-of-truth rule for the formal World of Tanks
2.3.1.1 port. The interface POC in this directory is intentionally disposable;
it must not grow into a second battle implementation.

## Non-negotiable source order

1. **Behavior source:** the latest mature implementation under
   `0.8.2/scripts/client/gui/mods/offhangar/`.
2. **Modern structural template:** the real-Avatar/Vehicle split under
   `0.9.22/src/res/scripts/client/gui/mods/offline_lan_0922/`.
3. **Target adapters only:** exact 2.3.1.1 lifecycle, entity property stream,
   descriptor, GUI/input, native physics, presentation and resource APIs.

Copy working battle law unchanged whenever its inputs and outputs still match.
Change imports and place a thin adapter at an incompatible boundary. Replace a
subsystem only when client evidence proves that its old contract no longer
exists. Every formal-port module must record its 0.8.2 source, classification,
changed interface surface and required Windows proof.

The 0.9.22 `BATTLE_SOURCE_AUDIT.md` is the process precedent: undocumented
parallel laws and convenience replacements are not accepted.

Before the first formal runtime module is admitted, freeze the reviewed 0.8.2
source commit and per-module source manifest. Later 0.8.2 changes enter only
through an explicit parity review; "latest" is never a floating build input.

## What the current POC is allowed to prove

The POC may answer only these interface questions:

- whether the stock `.wotmod` loader imports CPython 2.7 bytecode and calls
  `init()` before `game.start()`;
- whether the stock `offline <space>` path loads a client-only space;
- whether the resulting `OfflineEntity`, `FreeCamera`, callback and space state
  can be observed;
- in later separate probes, whether a complete PlayerAvatar/ClientArena
  context, one real Vehicle, native control and deterministic cleanup work.

It must not provide game rules, synthetic movement, fake vehicles, battle UI,
combat, bots or LAN behavior. Its free camera, fake player and polling loop are
not formal-runtime architecture.

`OfflineMode.isSpaceLoaded()` means only that stock geometry loading exceeded
`0.9999`. `OfflineMapCreator` is also insufficient: its offline branch keeps
`playerVehicleID == 0` and deliberately skips the battle GUI session and
`AvatarInputHandler`.

## Formal subsystem matrix

| Responsibility | Copy from mature behavior | Adapt only at the 2.3.1.1 boundary | Replace or exclude | Required runtime proof |
| --- | --- | --- | --- | --- |
| Bot roles, tactics, cover and driving | `bot_ai.py`, `bot_ai_cover.py`, `bot_ai_driver.py`, `bot_ai_navigation.py`, plus the mature map-independent planner laws | Modern descriptor reads and target collision, terrain, water and cover probes | No nearest-target or fixed-route simplification | 29 bots retain roles, routes, traffic handling, cover cycles, aiming and bounded frame work |
| Vehicle motion and tank contact | `physics.py` longitudinal/traverse/fall/ram law and `vehicle_collision.py` spatial index, OBB contact and pair response | Parameter extraction and one proven owner wired to `WGVehicleFilter`, `WGTankPhysics` or `WGWheeledPhysics` | Replace the 0.8.2 x86 `native_filter_bridge.py` and `offhangar_native_seed.pyd`; no Python pose may masquerade as native control | Create, control, wall/slope/contact/landing/stop behavior, then release with exactly one motion owner |
| Map, CTF and spawning | Mature spawn candidates, ground/roof checks, enemy-base facing, formation and fail-closed lineup state | Modern `ArenaType`, CTF spawn/base records and typed space visibility | Replace the 0.8.2 map pool, XML shapes and far-plane workarounds | Only selected CTF objects are visible; player plus 29 bots spawn on drivable ground |
| Navigation and foliage | `foliage.py`, A*, hazards, shallow-water, slope, recovery and prebaked-data validation law | Rebuild the map catalogue, navigation and foliage data from the pinned 2.3.1.1 client | Never relabel 0.8.2 coordinates as current data | Multiple maps show no wall, deep-water or cliff shortcuts and correct sight-line foliage |
| Projectiles and armour | `projectile_runtime.py` flight and moving-target sweeps plus mature penetration, normalization, ricochet, overmatch, spaced-armour and HE law | Modern shell descriptors, collision results, tracer/effects and feedback presenters | No instant damage or effect-only firing | Elapsed flight, gravity, dodging targets, shell classes, penetration falloff, visuals and HUD agree |
| Artillery | `artillery_arc_queue.py` and mature low/high ballistic solutions, lead and ray budget | Modern SPG descriptors, muzzle matrices, collision query and tracer presenter | A rear route or straight shell is not artillery support | Low/high arcs, obstruction, moving lead, fixed launch trajectory and bounded probes |
| Modules, crew, fire and consumables | `device_damage.py`, `internal_geometry.py`, `internal_hit_layouts.py`, `internal_layout_profiles.py`, `internal_layout_store.py` | Modern component shapes and state/HUD presentation | Do not invent a new random critical-hit model; new vehicles use the mature fallback until profiled | Ammo rack, tank, engine, track, crew, fire, repair/med/extinguisher and death transactions |
| Spotting and camouflage | `spotting.py`, `foliage.py` and delayed Sixth Sense behavior | Modern descriptor camouflage/view fields and model/marker/minimap/auto-aim visibility gates | Exclude the 0.8.2 static vehicle camouflage table as a modern source | All consumers share one visibility result; proximity, ceiling, memory and Sixth Sense timing hold |
| Standard battle and results | `capture_rules.py`, mature countdown/elimination/timeout/capture/result/stat law | Modern `ClientArena`, period events, GUI and detailed-result contracts | Stock OfflineMode has no rules loop and cannot substitute for this row | Countdown, capture interruption, elimination, timeout, stats, result and a clean second round |
| Entity and appearance ownership | Preserve gameplay-ID versus visual-carrier separation, remote pose/aim semantics and teardown ordering | Start from 0.9.22 `entities/bigworld_binding.py`, `remote_vehicle.py`, `runtime.py`; replace only property stream, appearance assembler and matrix provider seams | Exclude `_MockVeh`, hand-built model parts, `FakeAppearance` and legacy `filter.set` signatures from production | Real PlayerAvatar, real own Vehicle, remote visuals/collision/damage/death and deterministic removal |
| Camera, input and HUD | Preserve arcade/sniper/SPG, reticle, reload, speed, death spectator and visibility behavior | Modern `AvatarInputHandler`, `VehicleGunRotator`, `ConsistentMatrices` and session provider | Exclude all old `fix_*`, `inject_*`, `dis_*` and private-field forcing scripts | Movement, aim, fire, zoom, SPG camera, spectator, HUD and entity pose remain coherent |
| Destructibles | `destructibles_authority.py` ownership, deduplication and commit law | Modern encoder/manager/controller, chunk identity, collision callback and current-map data | Replace 0.8.2 native callback and hard-coded entity shapes | Ram/shot/fall/despawn/pass-through work while unrelated static walls still block |
| LAN, after local parity | External Python 3 room, clock, revision fences, shared rules and authority failover | Modern client roster, descriptors, maps, entity/presentation seam and build ID | The LAN transport must never become a competing battle simulator | Two clients share roster, countdown, damage, deaths, capture, result and failover |
| Cleanup and repeated battle | Generation fencing, full ownership inventory, and stop-owner-before-visual/entity/space teardown | Exact 2.3.1.1 lifecycle and callbacks; mod-owned cleanup must precede stock entity clearing | Exclude 0.8.2 process termination and stock OfflineMode's one-way lifecycle | No callbacks, entities, models or physics owners remain; a second in-process round is complete |

## Old shell that must be replaced

The following are client-specific shell code, not battle law:

- `mod_offhangar.py` login/account/connect monkey patches and lifecycle guards;
- `server.py:FakeServer`, `EXrequests.py`, `command_handlers.py`,
  `command_router.py`, `data.py` and `session_guards.py`;
- `lan_settings.py`, `lan_waiting_room.py`, `OfflineEntity.py` and
  `CameraNode.py` old-client interfaces;
- every `fix_*`, `inject_*`, `dis_*`, `patch_manual_cam.py`, `bw_script.py` and
  `test_matrix.py` experiment;
- the 0.8.2 x86 native bridge and seed binary.

Their user-visible intent remains required where applicable: offline inventory,
vehicle selection, a real Battle action, acknowledged waiting-room state,
settings persistence and safe teardown. Only their obsolete contracts are
discarded.

## `offline_battle.py` extraction rule

The mature 21,270-line `offline_battle.py` cannot be copied as one modern
module and cannot be rewritten wholesale. Preserve its law-bearing functions,
then connect them through thin target adapters. Important source anchors are:

- `_offh_resolve_hull_hit` at line 681 and `_offh_penetration` at line 1788;
- `_offh_battle_callback` at line 2036 and `_offh_live_projectile_tick` at
  line 2445;
- `_offh_update_sixth_sense` at line 3444 and `_offh_battle_sweep` at line
  5395;
- `_offh_finish_battle`, `_offh_check_battle_end`, `_capture_tick` and
  `_aih_tick` at lines 9589-10097;
- `_tank_resolve` at line 10675;
- `_offh_he_splash`, `_apply_module_damage` and `_mock_shoot` at lines
  16948-17998;
- `_auto_spawn_teams` at line 19521 and `begin_offline_battle_queue` at line
  21143.

`_try_spawn_battle_avatar_stub` and `_MockVeh` preserve useful bootstrap and
carrier intent, but their mock implementations are not copied. The 0.9.22
split into `battle_runtime.py`, `bot_runtime.py`, `combat_rules.py`,
`critical_damage.py`, `gun_mechanics.py`, `world_collision.py` and `entities/`
is the starting structural template.

## Formal vertical acceptance gate

The first formal slice is not merely "the game starts". It must pass:

```text
loader -> selected CTF map -> real ClientArena/PlayerAvatar
       -> real own Vehicle -> native control -> stock camera/HUD
       -> fire and damage -> cleanup -> second in-process round
```

Only after this exact chain passes on the pinned Windows client should the
copied bots, combat law and full presentation layer be connected. Static ABI
inspection and mocked tests cannot satisfy a native runtime item in the table.
