# Compatibility review: World of Tanks 0.9.22.0.1 #1513

This review is pinned to the Chinese HD client whose `version.xml` reports
`v.0.9.22.0.1 #1513`. The executable is 32-bit x86. Packaged client modules use
CPython 2.7 bytecode magic `03 f3 0d 0a`; the embedded build identifies itself
as Python 2.7.7.

Version 0.4.0 adds only release, local-configuration and lobby-presentation
adapters around the existing battle runtime. The copy-ready configuration
always begins at `127.0.0.1:28782`; a user edit is atomically stored in
`mods/configs/offline_lan_0922/server_endpoint.json`, outside the files shipped
by later overlays. Malformed user data fails safely to loopback. Exact #1513's
CN lobby opens an automatic server-announcement browser at zero battles; the
scoped adapter suppresses only that `onLobbyInited` automatic call before
creation. It does not replace explicit `showBrowser`, disable
`BrowserController`, affect the training-settings picker or intercept browsers
opened later by the player.

The x64 Windows server artifact is a PyInstaller deployment of the same Python
3 service. Its launcher fixes `0.0.0.0:28782`, `server_random` and 30 players;
the Windows CI gate checks the PE architecture, listener and v5 welcome. This
does not change the client/server protocol or the 32-bit x86 game client. The
artifact is currently unsigned, so SmartScreen trust remains a distribution
boundary rather than a compatibility claim.

Version 0.3.76 restored exact #1513's frozen PREBATTLE aiming boundary. After
one initial camera/gun alignment, the physical gun, stock marker and server
marker remain frozen until the single native BATTLE period transition starts
stock aiming and opens the existing movement/fire fence.

Exact resources expose vehicle mass, speed and terrain resistance, but not the
native C++ W-release curve. The neutral-coast share therefore moves
conservatively from `0.55` to `0.65`, without an exact-retail claim. Type 62
regression covers 30, 60 and 120 FPS; native feel remains a Windows #1513
acceptance item.

Version 0.3.75 repairs a lifecycle mismatch introduced by deliberately usable
PREBATTLE camera controls. Exact #1513's period transition clears the private
`PlayerAvatar.__isOnArena` flag and stops `VehicleGunRotator`; the enabled
input handler could therefore move the gun marker while the physical turret
remained at its spawn angle. The port now supplies native targeting parameters
before calling the exact rotator `start()` surface, temporarily sets only the
guard flag, restores it in `finally`, and verifies the rotator's private
started/maximum-turret-speed state. It does not call the full arena-start
gameplay transition. `_battle_live` continues to fence movement and fire until
the server's ordered BATTLE barrier.

The navigation change is confined to shared strategic route A*. It adds a
small cost derived from the baked cell's existing link completeness, and its
smoother rejects a shortcut that would increase mean missing-link exposure by
more than 0.25. Spawn joins and local recovery do not request the preference.
No collision, shallow-water, grade or link-validity predicate is weakened, so
an unavoidable one-cell passage remains the same passage rather than being
made artificially wider.

The full-pair observation period is 0.40 seconds and its phased lane-refresh
window is 0.20 seconds. The ordinary no-query envelope is 585 m: the server's
560 m assignment ceiling plus a conservative 25 m two-vehicle travel margin
across the longer window and presentation phases. This does not change
`SHOT_LANE_SECONDS = 0.20`; a selected target must still have an independent
final-fire lane result within that freshness bound. At the compatibility
boundary, lower periodic probe frequency is therefore allowed to delay shared
tactical knowledge but not to authorize fire from an older proof.

Bot inventory uses the installed descriptor capacity and at most five
descriptor-order shell summaries already admitted by protocol v5. Because the
stable descriptor seam does not expose store price, the first non-HE round is
the standard baseline and a later non-HE round is classified as premium only
when its representative penetration is at least 1.03 times the baseline.
Category weights are
`3:2:1` for ordinary vehicles and `1:1:4` for SPGs, redistributing any absent
category. Server planning prefers standard, selects HE only for a safely soft
or bounded finishing case, selects premium when normal penetration is below
the target-armor margin, and never requests an exhausted category. Human
contacts obtain armor/class from `build_vehicle_profile()` over the installed
descriptor and cache that result by vehicle name. Render-frame live overlays
update pose, health and team fields only, so they cannot replace this immutable
profile with an authority-wire armor claim.

Bot `shell_index`, `next_shell_index`, `ammo_reload_pending` and
`ammo_remaining` form one atomic snapshot. The authority consumes the
physically loaded round at launch and may promote only the previously planned
round at a completed reload boundary.
The server checks inventory shape, exact one-round conservation and the
loaded/next transition; canonical snapshots preserve all fields for authority
takeover. This is trusted-LAN Bot admission, not a new player reload validator
or a reconstruction of retail store economics.

Lakeville's compiled space contains CTF and assault2 base instances with
different visibility masks. The initial client-only space selection correctly
writes CTF bit `0x00000001`, but exact #1513's late
`ClientVisibilityFlags.SERVER_MASK` update may overwrite it with `0x000fffff`
before deferred client readiness completes. `_finish_entity_startup()` now
idempotently reapplies the selected gameplay bit after that stock boundary. In
the exact Lakeville data, CTF mask `0xffffff89` intersects bit 0 while assault2
mask `0xffffffc0` does not, so the observed write sequence
`1 -> SERVER_MASK -> 1` leaves only the CTF base visible. XML, capture rules,
minimap, team assignment
and the one-base-per-team CTF objective are unchanged. Windows #1513 remains
required to accept native prebattle turret motion, realised corridor traffic,
sustained frame pacing, base visibility and ammunition presentation.

Version 0.3.74 keeps full authority-Bot state inside the client but projects the
v5 `bot_state` wire copy to the server sanitizer's consumed fields before the
immutable outbound snapshot. This does not move projectile launch or Bot
simulation to the server: `BattleRuntime` continues to consume the original
complete update locally. The optional shot yaw/pitch pair remains atomic at the
projection boundary.

The short 0.0975-second generic planning cache remains a steering and slope
refresh. A typed exact 3x3 receipt has an independent containment contract and
may cross that refresh only under its exact origin, yaw, travel sign and
actual-`dt` forward-coverage checks. Any vertical, lateral or angular drift,
coverage exhaustion or sign change restores proof. The navigation adapter now
uses the driver's 1.5-metre arrival radius when rejecting a parked near target,
and an intentional traffic wait suppresses recovery for no more than 1.5
seconds. These are pure Python control boundaries; realised native frame pacing
and congested movement still require Windows #1513 acceptance.

Version 0.3.73 fixes one compatibility gap between the local spawn planner and
the 0.3.71 asynchronous sender. The planner intentionally returns a dictionary
keyed by integer team ids `1` and `2`, while the immutable sender rejects any
mapping whose keys are not already JSON text. `LANClient.send_battle_ready`
now converts only those known team keys to `"1"` and `"2"` at the protocol
boundary. The formation payload and server load-barrier contract are otherwise
unchanged.

Version 0.3.72 replaces the previous fire-time terminal ray with a shared
elapsed-time projectile law for player and authority-Bot shells. The canonical
launch records origin, velocity, gravity and lifetime. Its parabola is tested
in adaptive chords no longer than 50 ms and with at most 5 cm sagitta,
including a relative sweep for each moving vehicle, so an already-fired shell
does not follow a target and the target may dodge it.
Direct fire and SPG fire use moving-target lead before launch. The same launch
record drives local and relayed tracer presentation.

The matching server advertises and requires `projectile_ledger_v2`. Version 2
also freezes the mounted shell law in every launch, so clients and servers
with the older mandatory launch shape fail during capability negotiation
instead of accepting a battle and rejecting its first shot. It owns
launch identity, checked-through progress, active snapshots, authority epochs
and terminal tombstones. A launched shell remains live if its shooter leaves;
an elected successor restores active records and continues only from the
server-accepted cursor. One terminal resolution validates and commits direct
or bounded splash HP effects and shot-destructible receipts atomically. The
server still trusts the map-aware authority client for proprietary BSP,
destructible, vehicle and armor intersection results; the durable ledger does
not turn those local geometry queries into server simulation.

SPGs retain their server-owned rear deployment anchor. A bounded authority-
client controller evaluates low and high ballistic families against exact
world collision through a fair queue capped at four native rays per rendered
frame. It freezes a proved moving-target aim/flight intent through the matching
native muzzle launch, preventing target motion from replacing the pending proof
every tick. A receipt that finishes more than 1.5 metres from the target's
newly projected impact pose may wait only while the same identity and full 3-D
velocity will cross its proved endpoint, with that condition rechecked every
frame; otherwise it is rejected and re-led through another frozen exact proof.
The whole proof lifecycle has a 120-second absolute bound. The exact descriptor
survey finds 52 SPGs, 133 installed shell entries
and 43 distinct physical tuples. Speed spans 265--510 m/s and gravity spans
125--190 m/s2. Across installed elevation limits and the baked maps' 89.106 m
maximum terrain drop, the longest reachable grounded trajectory is the
FV3805's 440 m/s, 146 m/s2, 70-degree high arc at 5.872907831 seconds. Stun
remains disabled because the pinned client fragments do not
supply this port with a complete canonical penalty/duration ledger and
medical-kit recovery transaction. Python verification does not prove #1513
tracer visuals, projectile/arc-probe frame pacing, artillery feel or native
round cleanup; those remain Windows acceptance items.

Version 0.3.71 imports a constrained set of proven 0.8.2 mechanisms without
changing #1513 native ownership. The caller-facing LAN path freezes plain JSON
and appends every accepted message to a bounded reliable FIFO; it neither
serializes nor calls `sendall` on the game thread and it never coalesces ordered
input, Bot-state or combat payloads. Hello remains the synchronous first wire
message. The sender and receiver are fenced by one transport generation.
Invalid input is rejected before admission; overflow or sender failure closes
that transport rather than losing an ordered message silently.

The copied vertical integrators now distinguish first terrain placement from a
later centre-support jump. A rise above `min(frame climb, 0.85 m) + 0.02 m`
rolls the current tick back and reuses the established hard-wall response rather
than lifting the hull onto a wagon, roof or large prop. Bot support rejection,
realised navigation rollback and hard motion resolution all invalidate the
affected decision and typed motion receipt before remembering the attempted
yaw. The driver chooses one finite escape side around a broad obstacle and
aligns before applying forward torque to a meaningful ascent. The navigation
guard preserves a turn immediately before a climb in baked smoothing, live
reach, lookahead and partial-path continuation.

No native 0.8.2 `WGVehicleFilter`/physics experiment is present in this port.
The SPG boundary also remains unchanged from 0.3.70: a rear route anchor and
arrival hold are implemented, but open-sky proof, ballistic trajectory and arc
collision, indirect-hit resolution and stun are not. Exact Windows #1513
remains the acceptance boundary for native motion/contact feel, viewpoint
switching and repeated-round lifecycle safety.

The goal of version 0.3.70 is a complete playable vertical path, not another
login-only probe: local Account -> stock Lobby/join/map selection -> native map and
Avatar -> native local Vehicle plus remote presentations -> local movement/aim/fire -> synchronized
humans and bots -> damage/death/result -> cleanup -> a second round.

Version 0.3.70 narrows the copied horizontal-collision fast path to a
continuous bounded height profile whose actual collision normal is ground-
like. Unlike the previous one-direction rise test, the predicate is direction-
neutral, so a continuous downhill profile is not reclassified as a wall. Level
streets, step discontinuities, flat walls and raised walls still reach the
original hull rays. In the copied longitudinal law, neutral coasting preserves
the established flat-road drivetrain share and progressively unloads only that
share when current motion is downhill. At or beyond the static-hold tangent,
only descriptor rolling resistance remains. Uphill coasting gets no downhill
relief; opposite throttle, handbrake and the zero-speed hold still apply their
existing laws.

The destructible contact seam retains the exact descriptor filename and #1513
mass/speed/health gate. At physical speed, exact swept-hull/OBB contact supplies
the real kinetic input and an accepted fragile/module crush can advance without
the hard-wall speed response. At low speed or from rest under matching drive,
the forward/reverse descriptor top speed is only gate evidence. It can trigger
native submission only when the exact leading hull face, a 0.075-m margin and
this frame's real travel intersect the item. That submission holds the current
pose and restores pre-step real speed; the cap never enters copied vehicle,
LAN or ram state. On following ticks, a pending native skin can clear only by
advancing through its unique registered OBB exit and recasting the remainder.
Falling items, backing walls, expired-but-still-solid skins, ambiguous identity,
native rejection and under-threshold contacts remain blocking.

Authority-Bot planning uses the same strict catalog boundary without moving
authority to a distance probe. The existing staggered driver cadence retains
its three-lane 15-metre low-speed and 20-metre above-5-m/s corridor. A pure read
may classify a segment as soft only by resolving unique stock-crushable OBBs
and advancing past each exact exit. At most four adjacent items may be skipped;
a fifth fails closed. Generic planner alternatives retain their six horizontal
rays. Only the finally selected flat, straight, powered motion sample adds an
exact read-only 3x3 receipt: commit-width lateral lanes, all three commit heights
and 15 metres of forward coverage, bound to origin, yaw and direction. An
ordinary straight frame may skip a fresh world query only when its actual-`dt`
leading-hull sweep remains strictly inside that typed receipt and no catalog
OBB touches the hull. A hard proof blocks; a deferred proof is not cached.
Missing or stale proof, vertical/lateral/yaw drift, catalog contact, coasting,
braking, turning and airborne motion remain world-first. Destruction and LAN
publication occur only at exact hull contact, and the directional cap remains
gate-only. Final-motion receipts have a hard 13-job render-frame budget. The
waiting rotation retains only Bots that actually made the eligible final-motion
request; idle, hard-blocked, turning or airborne Bots drop out. Unattempted
receiptless work keeps initial-backlog priority over refreshes. Once its native
callback itself defers, it loses that priority and rotates behind the other
enrolled requests, so neither a persistent callback deferral nor a refresh can
starve the other. Initial deadlines cover the full 0.0975-second decision
interval. A deferred eligible Bot pauses for that frame at pre-step real speed,
does not call route-failure recovery, does not cache the deferred result and
does not substitute an authoritative world sweep. The strict 24-FPS scheduler
test drains 29 startup
jobs as 13/13/3 and grows the receipt cache as 13/26/29; native Windows frame
time remains outside this deterministic proof. A bounded
low-rate zero-speed scan merely registers the streamed chunk. This supplements
the older native sensor without restoring the permissive 0.8.2 pivot workaround.

The server macro planner now stages each SPG at one cached rear-side point
chosen from direction-neutral own/enemy route geometry, then emits a zero-
throttle hold inside the arrival radius. This is a portable server order, not
proof of an open ballistic corridor. The client-side arc solver, arc collision
budget and indirect-hit loop are not yet claimed by this review; native Windows
#1513 placement remains a release acceptance item.

Version 0.3.69 adds two constrained adapters without moving proprietary
terrain or camera law into the Python server. The canonical base state gives
the server macro planner an `invaders` trigger and the exact threatened base.
It retains a stable one-to-three-Bot response chosen by distance/profile-speed
ETA, normally leaving one living Bot on its previous task. Responders keep the
normal contact and firing-lane gates; capture-contributor identity can rank
only an already visible and individually shootable contact, so no hidden pose
crosses this boundary.

The postmortem switch mailbox now delegates to a local server-style attachment
only after the stock postmortem delay. It admits living friendlies, changes
the attached matrix, and invokes the exact `PlayerAvatar.onSwitchViewpoint`
callback transactionally. A selected synthetic remote entity is exposed to
native lookup only for that observation; death/removal selects the nearest
living ally and cleanup revokes the exposure. Windows #1513 remains required
to accept the native switch controls, camera continuity and repeated-round
teardown.

The 0.3.68 destructible boundary is pinned to a shipped schema-v7 destructible
catalog and schema-v4 foliage catalog baked from all 41 exact #1513 map
packages; the exact-instance runtime shape starts at schema version 4. A
checksum-pinned whole-map directory maps
61,625 unique world-matrix signatures to fragile, falling and structure-module
resources plus transformed BSMO bounds. This recovers identity for native
chunk slots whose name is absent from the compacted native list while
preserving the engine's chunk/item index. Eleven ambiguous signatures covering
28 candidates fail closed.

The baker follows the exact WGDE row contract. Table 1 partitions table 2 into
per-chunk ranges and resets the native item index for each chunk. Each non-empty
table-2 row owns an inclusive table-3 reference span; an authored empty span
references no scene instance and does not consume a native index. SpeedTree and
BSMI references share that one index sequence. Multiple references in one row
collapse structure modules into one item, while a referenced BSMO entry whose
effect type the gameplay baker ignores still consumes the row's native index.
Correcting the old empty-row count changed 1,455 destructible wires and 357
foliage wires across the only six affected maps: `07_lakeville`,
`11_murovanka`, `18_cliff`, `23_westfeld`, `34_redshire` and
`36_fishing_bay`; the other 35 map censuses and wires are unchanged.

Live #1513 matrix/signature reports pin the corrected non-empty-row reading at
Murovanka `(32124, 7)`, Cliff `(32893, 6)` and Redshire `(33148, 58)`, with
Karelia `(31610, 0)` as a control that is identical under either reading.
Lakeville, Westfield and Fishing Bay still require exact-Windows confirmation.
Local-player movement does not infer a dynamic prop from a nearby pivot: its
swept OBB must intersect the exact item OBB, then the stock mass/speed/health
kinetic gate decides whether native destruction may be requested. Native
fragile/module acceptance retains a synthetic block through the stock
0.2-second hiding delay. Falling items refresh their catalog OBB from the
native animator only until the first touchdown callback; that coarse OBB then
retires while the moving/final native BSP and ground support remain
authoritative. Static world rays and backing collision remain authoritative
after a hiding interval; the catalog is a strict contact/identity source, not
permission to bypass an intact wall. The broad object-origin proximity workaround used by
the legacy 0.8.2 implementation is deliberately not transplanted.
Authority Bots retain the existing streamed proximity/native contact sensor;
the new dynamic-only OBB supplement is not wired into Bot movement.

The catalog retains normalized keys for deterministic indexing, but native
`DestructiblesCache.getDescByFilename` receives the resource's case-preserved
descriptor filename. This is an exact-build ABI requirement: the cache lookup
is case-sensitive and rejecting a lowercase synthetic filename occurs before
the unchanged stock mass/speed/health kinetic gate. Version 0.3.68 neither
lowers that gate nor restores the broad 0.8.2 pivot-proximity workaround.

The shot path also retains native material identity as its first choice. If a
#1513 native slot is anonymous, only the nearest unique catalog OBB on the
bounded shot segment may supply identity. The first static collision and
nearest vehicle cap the search, while ambiguity fails closed. Traversal resumes
from the exact registered OBB exit plus a small epsilon, not a fixed jump that
could skip a thick structure or its backing geometry.

The exact #1513 `destructibles.xml` supplies both numeric shooting-through
contracts: `maxHpForShootingThrough` is `19`, and every listed material has
`projectilePiercingPowerReduction` factor/minimum values `(0, 25)`. Version
0.3.68 therefore lets AP, APCR and APHE continue only through an item whose
scale-adjusted health is at most 19. Each accepted item leaves damage unchanged
and adds a fixed 25 mm penetration loss; multiple items accumulate. The first
operation that actually needs penetration lazily samples one shell factor and
reuses it, while the range-dependent mean is evaluated at each tested obstacle
distance and again at the vehicle. A pure miss or HE/HEAT stopped by a
destructible consumes no penetration RNG. A sampled remainder below 1 mm makes
the shell disappear at that
obstacle. An above-threshold item may be destroyed but stops traversal. Under
the pre-1.13 HE mechanics used by #1513, HE and HEAT stop at the first
destructible, and HE explodes at that point.

The threshold and material reduction are exact pinned-resource evidence.
Official same-family mechanics descriptions support the shell-family split,
cumulative penetration reduction and unchanged damage. The proprietary retail
0.9.22 server implementation and its exact operation order are not published,
however. The lazy one-factor, per-tested-hit-range, then cumulative-reduction
order above is therefore documented as a high-confidence reconstruction rather than an exact
server-source copy. The resulting fragile/module payload preserves its encoded
shot bit, but the local manager order is unsynchronized: the copied projectile
path does not deliver the retail server's later `damagedDestructibles` payload
required to release a projectile-synchronized native order.

The complete streamed-slot boundary comes from the exact #1513 native path:
`game.onChunkLoad(spaceID, chunkID, numDestructibles, isOutside)` writes
`numDestructibles` into the active `DestructiblesManager`. Version 0.3.68 reads
that manager count and enumerates every native index.

`wg_getChunkDestrFilenames` is not a per-slot surface, and the earlier
"filename prefix" reading of it was wrong. Read from the exact module
(`WorldOfTanks.exe`, x86 PE timestamp `0x5a6edca4`, image size `0x206a000`,
PE checksum `0x019a5229`), its implementation at `0x006b1a10` walks item
indices `0 .. numDestructibles(chunk) - 1` and appends one name per item only
when the item resolves, its native type owns a name handler, and that handler
returns a non-NULL pointer. The code does not test the first byte: a non-NULL
pointer to `\0` becomes the legal Python string `''`. An unresolved item, a
missing handler or a NULL pointer appends nothing, so the returned list is only
*possibly* compacted in item order. When its length equals the exact native
item count, the one-append-per-item bound and loop order prove that every
position is that native item, with `''` carrying no filename evidence. When
the list is shorter, its positions are not native item indices; indexing it by
the item index can therefore return another item's resource.
The two old `05_prohorovka` reports captured only that direct lookup at list
position `70`, not the complete name list and per-item categories needed for
reconstruction. They prove the old lookup was unsound; they do not prove that
the reconstructed native name of chunk `31875` item `70` is `poplar.spt`, or
that the live item truly conflicts with `env014_Toilet.model`. The unit
regression using those two filenames is consequently a generic synthetic
conflict test. The real Prokhorovka `(31875, 70)` identity and its crash
correlation remain a bounded exact-Windows diagnostic boundary. The retained
`+0x6bb0aa` crash site is consistent with an engine-side null read in a
`(uint32, uint8)` keyed lookup whose sibling entry point checks for a missing
record, but static executable review does not prove which gameplay object
caused that miss or that the old misindexed name caused the crash.

`wg_getDestructibleMatrix` (`0x006b2a90` through `0x006b3f90`) and
`wg_getDestructibleEffectCategory` (`0x006b1f10`, module index below zero)
resolve an item through the same provider entry (vtable `+0x10`) that the name
loop uses, so matrix, per-item name and effect category share exactly one item
index space of length `numDestructibles(chunk)`. That call fails for an
unresolved item and returns `-1` through `or eax, 0xffffffff` at `0x006b20b8`
for a resolved item whose native type owns no handler - precisely the two
cases the name loop skips. Enumerating it reconstructs the name list after
every live item is typed. A full-width list is aligned by position, while each
usable non-empty name's descriptor type must still equal that slot's live
effect category. Its positional proof also contains the first per-name
descriptor lookup or shape failure, malformed per-item category, and
descriptor/category mismatch to that exact slot; that slot is isolated while
its neighbours finish alignment. A previously recorded slot-local catalog,
matrix, or descriptor quarantine likewise stays local when the mapping is
rebuilt. A missing shared descriptor-cache or category-query surface still
invalidates the chunk contract. For a shorter list, empty strings carry no
descriptor evidence and the remaining names are reconstructed per native
type: an item is typed by its live effect category and a name by the client
descriptor it resolves to,
then both filtered sequences are paired in item order. This is sound only
under the pinned-client bridge that a descriptor's `type` is the same native
category returned for that item, and only after every resolvable native item
has been enumerated. A whole category with zero non-empty names is known to be
unnamed. A nonzero unequal name/item count is only a partial alignment,
however, so the complete chunk is isolated; no otherwise aligned category is
admitted from it. Because a compacted name position has no item identity, an
unknown or malformed non-empty name descriptor, per-name lookup exception,
malformed category result, or descriptor/category disagreement also isolates
the complete chunk. A category-query exception is the native resolver's
skipped-item case:
that exact slot is isolated before any matrix, effect or destruction call,
while other resolvable slots may finish alignment. Native category `-1` is a
resolved handlerless item, not that exception case: alignment leaves it
resolved and unnamed and does not isolate it. Filename reconstruction does
not reinterpret `-1` as a category mismatch. A registered native effect
category must match the exact admitted descriptor; `-1` leaves only that
effect channel unverified, so admission still requires the exact live matrix
and wire plus the exact native filename when one is present.

`wg_getDestructibleFilename` (`0x006b2580`) is deliberately not used as a
per-item probe. It resolves the same item and returns `Py_None` for an
unresolved one, but for a resolved item whose type owns no name handler it
reaches `PyString_FromString(NULL)` at `0x006b270c` and faults natively, which
no Python handler can contain.

The reconstruction is incremental and cached by `(space, chunk)` plus the
native count/name-list fingerprint. `wg_getChunkDestrFilenames` itself walks
the complete native chunk, so the first call is not constant-time; one
validated list snapshot is shared by all human, Bot and streamed-shot callers
until unload or a known native mutation invalidates it. Those callers then
share one battle-local allowance of at most 16 category probes for each exact
`BigWorld.time()` value. One focused incomplete chunk consumes the allowance;
completion or a terminal result releases it, and an abandoned focus becomes
replaceable after an intervening tick. Exhaustion returns
`pending_alignment`; the whole chunk stays solid and outside every registry
until a later tick completes it. The LRU cache prefers evicting completed
entries but can evict the oldest abandoned incomplete entry, so a full cache
cannot permanently starve a newly active chunk. A changed fingerprint
restarts reconstruction and a completed mapping is reused. This adds bounded
native category traffic; it does not support the earlier claim that ordinary
catalog matching adds none. Exact Windows observation still has to confirm that
all Python callers in one render tick observe the same `BigWorld.time()` value,
and measure both the first whole-chunk name snapshot and bounded category-probe
cost under real streaming load.

With an exact per-item name available, a live/catalog filename disagreement is
real evidence rather than an alignment artefact. Equal normalized names match;
an unnamed item has no filename evidence. Every different exact filename is a
conflict by default, even when both descriptors share the same broad kind,
because kind equality does not prove identical geometry, modules or health.
There is currently no data-proven alias allowlist. A conflict is isolated
before native effect or destroy calls. The unique matrix signature, exact
native wire, exact filename when present and native effect category remain the
fail-closed identity boundary. A missing native count or an incomplete shared-
budget alignment is retried after streaming rather than guessed. Direct
material-hit and shell paths cannot bypass that admission with a globally
known same-kind resource; a structure hit must also name a material module
present in that exact admitted instance.

The matrix boundary is contained at the scope of its evidence. A thrown chunk-
matrix query isolates that chunk, while a successfully returned matrix whose
translation is temporarily `None` remains solid and retries after streaming.
Once the chunk transform exists, an item-matrix, signature, OBB or scale
failure isolates only that exact slot. Ambiguous signatures, unnamed misses in
an exact-instance catalog, catalog-governed named non-tree placement misses,
named non-tree resources absent from the catalog, and non-empty native
filenames without a descriptor are terminal slot evidence. A legal exact-named
tree absent from the catalog may continue through the native tree path. An
empty filename is different: #1513 can legally return an anonymous
destructible material, so it remains solid until an exact registered
matrix/wire can supply identity.

Contact and shell descriptor reads use the same slot-local boundary. An
exception or malformed descriptor for a non-empty native filename isolates
that exact wire; an anonymous lookup failure remains retryable. If a second
descriptor/health read fails after native destruction was already accepted,
the typed shell result conservatively stops at that destructible instead of
escaping the projectile callback.

For Windows verification, bounded `DESTR` lines report one aggregate for each
newly scanned chunk plus each first distinct contact stage. The logger itself
reuses the same enumeration/contact result; the reconstruction queries are
separately constrained by the shared per-tick budget above. Logging caps
chunk/contact identities per battle and emits at most one line every 0.25
seconds. Isolation lines also include the catalog map and the first divergent
operation, wire, resource/kind and native result when those fields exist. Frame
diagnostics retain callback-stage timing and logical probe counts, but version
0.3.68 does not install the optional per-query Bot probe clock. Removing those
two clock calls per native probe is behavior-preserving: the probe sequence,
return values, freshness windows, deadlines and 110-pair safety budget are
unchanged. Straight-line Windows driving remains the frame-pacing acceptance
test; this source review cannot claim that the visible hitch is eliminated.

The previous 0.3.65 schema-v2 catalog supplied transformed OBBs but joined
runtime slots by native filename taken from the chunk list. A slot may be
present as `''`, while an unresolved, handlerless or NULL-name slot is absent;
therefore only a full-width list preserves native indices. Indexing a shorter
list by the item index silently returned a neighbour's resource. The
exact-instance runtime shape introduced at schema v4 closes that identity gap
with the whole-map matrix signature, and the per-item name is now recovered
from the full-width or reconstructed compacted alignment. The coherent shipped
batches are destructible format v7 and foliage format v4.

The stock `BigWorld.entity`/`entities` facade is an AOI surface, not the LAN
authority registry. Unspotted or dead synthetic vehicles remain private there;
only the injected pose/aim resolver reads them for simulation. Native visual
startup, local Avatar binding, drive and readiness continue through the stock
facade, so an internal update cannot accidentally reveal an enemy.

The local Account inventory is derived from the pinned client's initialized
vehicle catalogue. Only definitions that can produce a complete stock vehicle,
crew, module and ammunition record are published; event, IGR-only and observer
types are excluded. Inventory ids start at one, tankman ids are globally unique,
and every crew foreign key, installed item, unlock and shop-price entry is
validated before native lobby consumers receive the snapshot.

## Exact-build evidence reviewed

The following groups were extracted from the local `scripts.pkg` and reviewed
at their call sites and lifecycle boundaries:

- connection and Account: `connection_mgr.py`, `Account.py`, `PlayerEvents.py`,
  the Account sync helpers and lobby requesters listed in the consumer matrix
  below, server settings and lobby context;
- lobby and map selection: `gui/app_loader`, `LobbyHeader.fightClick`, Scaleform
  view loaders, `TrainingSettingsWindow`, arena cache and the generated
  prebattle aliases;
- battle entry and exit: `OfflineMapCreator.py`, `Avatar.py`,
  `AvatarInputHandler.py`, battle session/controller repositories and arena
  listeners;
- entity contracts: `Avatar.def`, `Vehicle.def`, `Vehicle.py`,
  `ClientArena.py`, `constants.py`, filters, gun rotation and item descriptors;
- presentation and combat calls: vehicle ammo/reload/targeting callbacks,
  `getCurShotPosition`, `showShooting`, health callbacks, kill arena updates
  and collision methods;
- copied-motion camera consumers: `AccelerationSmoother.update` plus arcade
  and sniper `__calcCurOscillatorAcceleration`; these read filter velocity
  and acceleration independently of the compound root matrix;
- resources: all standard arena definitions exposed by the local cache and the
  tank descriptors/models used by the playable and bot vehicle pools.

This matters because commonly available public 0.9.22 decompilations are from
different builds. For example, a similarly named API may exist in another
revision while being absent in `#1513`.

## Account and lobby lifecycle

The mod installs narrow, reversible adapters around the exact Account, Avatar,
Vehicle and connection boundaries. The fake connection constructs a real
`PlayerAccount`, supplies the server settings and RPC shapes consumed by the
lobby repositories, and then calls the native player and GUI lifecycle.

Exact build `#1513` unconditionally calls `BigWorld.clearAllSpaces()` at the
start of `PlayerAccount.onBecomePlayer()`. A client-only Account created inside
its own temporary space would therefore delete itself during promotion. The
offline wrapper suppresses only that one call while the native method executes
and restores the engine function in `finally`; online Accounts and later space
cleanup retain stock behavior. Delayed Account RPC callbacks also re-check the
current player identity before delivery, so a retired Account cannot receive a
late response during a battle/lobby transition. The lifecycle regression fake
uses destructive `clearAllSpaces()` semantics rather than a logging-only stub.

BigWorld entity destruction also clears the Python Entity's entire instance
dictionary. The exact Account repository survives across replacement Account
entities, while `AccountSyncData.setAccount()` saves its persistent cache
through the old weak proxy before rebinding that cache to the new Account. The
offline constructor therefore prebinds that one cache before native repository
reuse. The initialization sentinel is set only after the native constructor
returns. A separate retirement token is opened immediately before native
`onBecomePlayer()`, because that method can attach global helpers and chat and
then fail; the ready sentinel is set only after the complete promotion passes
validation. FakeServer and uncancellable Avatar resource callbacks require the
ready sentinel and current-player identity, so a zombie object cannot receive
a late mailbox callback even during the destruction tick.

The LOGGED_ON notification, Account construction and promotion are one
transaction. Any listener or constructor failure clears client-only spaces,
resets connection status, invokes every disconnect boundary independently and
deletes the retained Account repository even if an earlier event listener
raises. Shutdown restores every patched class and host entry in `finally`.

Before any bulk entity clear, the current offline Account or Avatar now runs
its complete native `onBecomeNonPlayer()` method exactly once. This detaches
`ChatManager.playerProxy` and every Account/Avatar helper while the entity
fields still exist; the later engine callback is ignored by the closed
retirement token. If native retirement itself raises before its late chat
detach, the wrapper still clears `ChatManager.playerProxy` and preserves the
first error. The regression fake clears the retired object's entire `__dict__`,
exercises Account -> Avatar -> replacement Account, and injects failures after
partial Account/Avatar promotion, reproducing the native failure mode in which
`ChatManager.switchPlayerProxy()` first cleans the old proxy.

The Account surface was checked consumer-first against the local `#1513` PYC,
not inferred from another 0.9.22 build:

| Producer | Exact consumer contract covered |
| --- | --- |
| `CMD_SYNC_DATA` / `AccountSyncData` | `rev` and `prevRev`; every initial `Account._update` subscriber receives an explicit cache value instead of depending on a missing-key fallback. |
| `Stats` / `StatsRequester` / lobby controllers | Zeroed money and account scalars; mapping-shaped restrictions, referral data and clan locks; a non-empty `dailyPlayHours`; and full daily/weekly `playLimits`. Zero periods mean exhausted parental-control time in this build. `mayConsumeWalletResources` starts true because false is the native wallet's `SYNCING` state, and `tutorialsCompleted` carries the completed offline bitmask. |
| `Inventory` / `InventoryRequester` | All item-type indices exist; vehicle `compDescr` and crew maps exist; `repair` is a two-item tuple and `shellsLayout` is a mapping. |
| `QuestProgress` / personal-mission requesters | `quests`, `tokens`, and `potapovQuests`; both `regular` and `training` contain `slots`, `selected`, and `lastIDs`, while `compDescr` is always present. |
| goodies, vehicle rotation, recycle bin, ranked, badges, New Year | Readable empty caches exist. `groupLocks` contains both directly indexed lists, the ranked helper can directly index an empty `ranked` cache, and `ClientNewYear` plus the New Year controller accept the empty sync/goodie mappings without fabricated event data. |
| `Shop` / `ShopRequester` / `RefSystem` | Mandatory `sellPriceFactor`; all directly read item/goodie collections; currency-mapped `paidRemovalCost`; exact berth, slot, and free-XP tuple arities; and the four-key disabled referral configuration, including integer `posByXPinTeam = 0`. |
| `DossierCache` / `DossierRequester` | The stream body is the exact `(revision, dossierChanges)` pair; an empty change list completes synchronization without fabricating dossier data. |
| `ClientChat` and BW Chat2 | Both client mailboxes exist. Offline chat commands are accepted as one-way no-ops: `CHAT_COMMANDS` indices are never echoed as `CHAT_ACTIONS`, and no malformed partial action is delivered to `ClientChat.onChatAction`. |
| initial server settings / lobby controllers / `ClientRanked` | `file_server`, regional settings, the four-item roaming tuple and the directly indexed two-item `wallet` retain their native shapes; roaming item 3 is the host list consumed by `predefined_hosts`, while `ranked_config` is present and explicitly disabled because `ClientRanked` indexes it directly. `elenSettings` and the server-owned tutorial are explicitly disabled because their exact missing-section defaults start unsupported event-board or tutorial GUI lifecycles. |

The controller chain itself was enumerated from `game_control.__init__` and
`new_year.__init__`. `NewYearController` is invoked first, followed by the
registered stock controllers through `GameStateTracker.onLobbyStarted`; among
that complete chain, `wallet` is the only direct `serverSettings[...]` lookup.
The later lobby-loaded consumers read Trade-In and restore configuration through
`ShopRequester` objects whose exact `#1513` defaults are disabled and complete,
so the offline producer deliberately does not invent those optional schemas.

The machine-readable source for these assertions is
`tools/account_lobby_consumer_contract.json`. Consumer-contract tests
deserialize the same extended and compressed payloads used by the fake mailbox
and exercise its direct keys, tuple arities, mailbox arities and callback
ordering. This is static and simulated Python coverage; it is not a claim that
every optional stock lobby view or server command outside the map-picker path
is implemented.

The EULA save path uses the exact `CMD_ADD_INT_USER_SETTINGS = 1600` and
`CMD_DEL_INT_USER_SETTINGS = 1601` commands. The offline Account persists those
integer settings in `mods/configs/offline_lan_0922/account_state.json` and
returns them in the next `syncData.intUserSettings`, so accepting the EULA is
not lost across client restarts. A malformed settings request fails without
mutating the last valid state.

`tools/audit_lobby_consumers.py` also scans every code object in the exact
`scripts.pkg` for literal subscripts rooted at the raw Account
`serverSettings` mapping. The build fails if that complete consumer inventory
changes or if a hard producer path is absent. This caught the separate
`predefined_hosts` use of `serverSettings['roaming'][3]`; the typed
`ServerSettings` wrapper only consumes the first three values, so a three-item
test fixture was insufficient even though the wrapper itself initialized.

It also caught a multi-round boundary. `OfflineMapCreator.destroy()` calls
`BigWorld.clearEntitiesAndSpaces()`, which removes the fake Account as well as
the battle entities. Its broad exception handler can fall back to `cancel()`,
which resets ids without clearing entities or spaces. Cleanup now records every
map-create attempt, runs the stronger stock destroy even after a rejected map,
verifies that no Avatar remains, and retries the engine clear directly before
it considers ownership released.

After a clean teardown, the offline Account is recreated through the same
patched constructor. The native `Account.showGUI` synchronization coroutine,
not BattleRuntime, owns the eventual `g_appLoader.showLobby()` call. The next
picker waits for native Lobby space, HangarSpace and the current vehicle model;
opening it synchronously after Account construction would race cursor and
Scaleform ownership. A server-initiated next `battle_start` uses the same gate:
the message is retained and fenced by round id until the native lobby is ready,
so a waiting roster and next start delivered in one network poll cannot replace
an Account while its hangar is still assembling.

`PlayerAvatar.onBecomePlayer()` removes the prebattle dispatcher. On a failed
battle start, exact `#1513` broadcasts IGR state to the still-live Hangar before
normal `Account.onAccountShowGUI` would recreate that dispatcher. Recovery now
creates and verifies the stock dispatcher before constructing the replacement
Account, closing that observable `getFunctionalState()` gap. During final game
shutdown, `guiModsFini` still runs before `SoundGroups.destroy`; a one-shot
instance guard hides only a retired Account/Avatar missing `inputHandler` for
that late call and then removes itself.

The required order is:

```text
OfflineMapCreator.destroy()
  -> restore_lobby_account()
  -> Account.showGUI() / native synchronization
  -> native g_appLoader.showLobby()
  -> wait for Lobby + HangarSpace + vehicle model
  -> if local player is room host, open the next TrainingSettingsWindow
```

## Stock map-selection lifecycle

Before the local Account creates the lobby, a chain-safe adapter intercepts the
exact `LobbyHeader.fightClick(self, mapID, actionName)` boundary. Exact `#1513`
Flash stores that Python callback when `LobbyHeaderMeta` first binds its script;
patching the class after `HANGAR_READY` can repaint the button but leaves Flash
calling the old bound function. The first click now joins the LAN waiting room
rather than calling the stock prebattle dispatcher. While LAN mode owns the
button it never falls through to retail matchmaking: that unsupported path
opens `Waiting('prebattle/join')` and cannot receive its server completion. The
server elects the first connected 0.9.22 player as room host and includes that
id in `welcome`, `roster` and `battle_start`; only the host opens
`TRAINING_SETTINGS_WINDOW_PY` through the exact
`ViewLoadParams(alias, alias)` contract. A scoped wrapper replaces only that
window instance's arena cache with server-offered standard maps, puts the
editable `LAN SERVER: host:port` endpoint in the native description field and
sends the chosen geometry. Guests remain in the hangar and wait for the
server-owned start. A guest request is rejected before map validation or map
mutation. Waiting-room host departure elects the lowest connected id and the
new host then receives the picker. Unmarked stock training windows continue
down their original methods.

There is one explicit pre-welcome settings path. The first **Battle!** click
starts the connection without opening a window. A failed connection opens the
same native form automatically while retry continues; another click while
connecting can also open it. Its provisional map choice does not confer host
authority: after `welcome`, a guest selection is discarded and only the
elected host may request the start. Manually closing a host picker leaves it
closed until that host clicks **Battle!** again, avoiding asynchronous cursor
recapture while Scaleform is retiring the view.

Before creating the first Account, bootstrap waits until the exact app loader
has entered `GUI_GLOBAL_SPACE_ID.LOGIN` for two consecutive engine ticks.
`personality.init()` loads mods before `personality.start()` starts the native
Start/IntroVideo-to-Login state machine; creating an Account in that interval
lets `LoginState.init()` destroy it with `clearEntitiesAndSpaces()`. The same
clear invalidates an in-flight hangar CompoundAssembler, which explains the
observed `R11_MS-1` resource-dictionary KeyError despite complete vehicle
resources. No vehicle-specific exception or resource replacement is needed.

The Battle adapter is installed immediately before Account promotion, while
the client is still in stable Login space, so the first Scaleform binding sees
the LAN callback. The wrapper then waits for the exact public
`LOBBY_VIEW_LOADED` event, Lobby GUI
space, initialized hangar space and (when present) a completed hangar vehicle
model before declaring the lobby ready. Merely finding an initialized
Scaleform application is insufficient because that object already exists in
the login/EULA space. The hangar timeout starts only after the lobby event, so
first-run EULA interaction is not treated as a startup failure. Raw class
members are preserved so Python 2 unbound-method identity is restored
correctly. A chain-safe `onWindowClose` adapter releases picker ownership when
the user presses Cancel, and programmatic close is idempotent even if Scaleform
has already retired the weak view. The stock window remains
responsible for mouse and cursor behavior; no transparent hotkey overlay or
F12/`0` handler is installed.

A first-chance Windows dump identified a stricter boundary in the picker
action. `updateTrainingRoom` was synchronously closing its own Scaleform view
and then returning `True`. The native dispatcher still attempted to convert
that non-`None` result through the view whose display-object pointer had just
been cleared, producing a `NULL + 0x0c` access violation before battle setup
began. The accepted action is now void, matching the stock/public observer
shape, and the owner closes the picker with `BigWorld.callback(0.0, ...)` only
after the current Scaleform event returns. If `battle_start` arrives first,
the network poll cancels that callback, closes the picker once, and only then
crosses the Account-to-Avatar boundary. There is no synchronous-close fallback.

## Self-drawn LAN waiting room

The LAN room is now presented with the port's own native components. The stock
map window described above remains the fallback for a client that cannot build
them. The room carries the reviewed 0.8.2 waiting-room presentation: the live
room status, one map selector limited to the server map pool, one start button
for the host and one close control. It also presents the players who wait for
the host, which the stock window cannot do. The desktop launcher owns the
server address before the client starts, so the room never edits an endpoint.

Every native call is proved in exact build #1513:

| Interface | Exact evidence |
| --- | --- |
| `GUI.Simple(texture)`, `GUI.Window(texture)`, `GUI.Text(value)` and the component properties used here | `scripts/client/PostProcessing/ChainView.pyc`, `scripts/client/bwobsolete_tests/GUITest.pyc`, `scripts/client/bwobsolete_helpers/PyGUI/Utils.pyc` |
| Texture `system/maps/col_white.dds` on every drawn rectangle | `misc.pkg` member; see the two rendering facts below |
| Font `default_small.font` | `system/fonts/default_small.font` package member |
| `GUI.addRoot`, `GUI.delRoot`, `GUI.reSort` and an overlay at `position.z = 0.1` with `focus` and `moveFocus` | `scripts/client/new_year/fade_window.pyc` |
| `handleMouseClickEvent`, `handleMouseEnterEvent`, `handleMouseLeaveEvent`, `handleMouseButtonEvent` | `scripts/client/PostProcessing/ChainView.pyc` |
| The lobby already attaches `GUI.mcursor` through `BigWorld.setCursor` | `scripts/client/gui/Scaleform/managers/Cursor.pyc` `attachCursor` |

Two rendering facts govern how this room may look. Neither is derivable from
the client scripts; both were established on the real #1513 client and both
contradict what the source reads suggested:

1. An **untextured** `GUI.Simple` or `GUI.Window` draws nothing. `GUI.Window('')`
   does appear in `ChainView.pyc`, so an empty texture is a legal state, but it
   is not a visible one. A build that drew the panel, the buttons and the
   pointer as untextured flat colour rendered only its `GUI.Text`; the buttons
   still worked because hit testing does not depend on drawing.
2. Vertex `colour` is **never applied** to a textured component. A row of test
   quads varying `materialFX` (`SOLID`, `BLEND`, `ADD`), `colour` (white, dark
   blue, green) and texture name (`.dds`, `.bmp`) all drew the same white.

So every visible rectangle carries `col_white.dds` and is white, and all
readable contrast comes from `GUI.Text`, whose `colour` **is** honoured: the
room uses dark labels on the white buttons and light labels over the hangar.
Hover feedback recolours the label rather than the button. The panel itself
stays untextured and therefore invisible, which keeps the hangar visible behind
the floating text.

A child component's `position` in `CLIP` mode is relative to its **parent**
rect, not the screen. A pointer parented to the 680 px panel therefore tracked
at exactly half the mouse displacement in a 1360 px window. The drawn arrow is
a set of `GUI.addRoot` components at absolute clip coordinates, sized in
`PIXEL`, so it follows the cursor one-to-one at any resolution.

`shadow` and `dropShadow` appear in no #1513 client script, so the room does not
set them. `wg_inputKeyMode` is proved only for the Scaleform overlay component,
so the room sets it optionally and logs a skip.

Static inspection cannot prove that a native component receives mouse events
while the Scaleform lobby is displayed. The room logs the surface it built, and
a client that raises during construction keeps the stock map window.

## Battle and entity lifecycle

The client delegates space, mapping, Avatar construction, camera setup and
teardown to the exact `OfflineMapCreator`. It temporarily selects the normal
battle branch while `PlayerAvatar.onBecomePlayer` runs, but preserves the one
native `AvatarFilter` established before world entry. A strict local mailbox
implements only the exact early Account/Avatar/Vehicle server calls needed by
this client.

The Lobby-to-Avatar transition also follows the exact `#1513` native ownership
order. It requires a fully initialized HangarSpace, calls
`PlayerAccount.onBecomeNonPlayer()` so chat, all Account helpers, current and
preview vehicles, HangarSpace, camera, input handlers, callbacks and geometry
are retired by their native owners, verifies both HangarSpace readiness flags
are false, and only then calls `BigWorld.clearEntitiesAndSpaces()`. The reverse
Avatar-to-Account transition runs `PlayerAvatar.onBecomeNonPlayer()` before
`OfflineMapCreator.destroy()` for the same reason. Calling the bulk clear first
leaves global managers holding an object whose instance dictionary has already
been erased. Every cleanup boundary remains best-effort if an earlier one
fails; if neither a clean Avatar teardown nor a replacement Account can be
proved, the fake WoT connection is retired instead of leaving a LOGGED_ON
client without a valid player. During synchronous map creation,
`game.abort()` is scoped to a recoverable Python failure so a rejected arena
cannot silently schedule process shutdown; the original function is restored
without overwriting a newer third-party wrapper.

`AvatarObserver.remoteCamera` is not a Python helper object in this build. Its
exact `REMOTE_CAMERA_DATA` alias is a fixed dictionary with `time` (`FLOAT64`),
`shotPoint` (`VECTOR3`), and `zoom` (`UINT8`); the producer now supplies that
mapping with a zero `Math.Vector3`. The inspector pins the hashes of
`alias.xml`, `Avatar.def`, and `AvatarObserver.def`, while the property test
rejects the previously accepted object/`None` shape.

`PlayerAvatar.leaveArena()` calls its base mailbox before the rest of its
native cleanup. The local bridge therefore schedules runtime teardown for the
next engine tick instead of destroying the Avatar reentrantly. LANSession then
retires that participant from only the active server round, restores the local
Account and keeps the waiting-room socket. The server transfers bot authority
to another participating client, or records a draw when no simulator remains;
the departed client cannot consume a duplicate start for the same round and is
re-enabled only by the next waiting roster. Local failure events are accepted
only for the synchronously starting or currently active round; duplicates from
the departed round and delayed failures from an older round cannot retire a
newer Avatar or send a second leave request. Explicit VOIP queries used by
vehicle markers are present and conservatively disabled. The postmortem switch
bridge reproduces the Python-visible outcome of the retail cell attachment
locally: it validates a living friendly target, updates
`ConsistentMatrices.attachedVehicleMatrix`, exposes only that selected
synthetic entity to native lookup and invokes the stock viewpoint callback. It
is deliberately limited to an active postmortem control after the delay;
enemy, dead, absent and not-ready vehicles fail closed.

Local Vehicle creation is gated by the complete pinned `Vehicle.def` SHA-256
`e585c59235ebb2cfbb7857645878ed095360a8efe5df666c055e59a74e6a55c5`,
uses all of its client properties, and publishes the exact
18-item compressed `VEHICLE_ADDED` tuple, native descriptors and native local
entity creation. Exact bytecode shows that `Vehicle.prerequisites()` builds appearance
resources asynchronously: the id returned from client-only `createEntity` can
exist before `BigWorld.entity(id)` is available. The bridge therefore separates
metadata from readiness. Immediately after Avatar creation it creates the local
Vehicle, publishes `VEHICLE_ADDED`, selects `playerVehicleID` while the entity
is not yet in-world, and invokes the native
`ArenaLoadController.invalidateArenaInfo()`. This establishes
`Lobby(4) -> BattleLoading(5)` before a completed space can request
`Battle(6)`. A scoped AppLoader guard makes both callback orders idempotent: a
premature battle-page request first establishes loading, while a late loading
request cannot regress an active battle. The Avatar name/team are seeded from
the same server roster, so `ArenaDataProvider` can resolve the local entry by id
or name. The compatibility wrapper does not repeat the player-id notifier from
inside `PlayerAvatar.vehicle_onEnterWorld`: in exact `#1513`, doing so can mark
`VEHICLE_ENTERED` and start visuals before the native handler initializes its
own-vehicle matrices. Stock `vehicle_onEnterWorld` and its `setClientReady`
mailbox therefore run in their original order; only a later BigWorld callback
accepts the entity after registry presence, `inWorld`, `isStarted`, and a
descriptor are all true. `onVehicleChanged`, client attributes,
`AVATAR_READY`, and `PERIOD` then publish exactly once.

The final `PERIOD` publication is itself a synchronous mailbox boundary in
exact `#1513`: `PlayerAvatar.__onArenaPeriodChange()` calls
`__setIsOnArena(True)`, which immediately calls `moveVehicle(..., False)` and
then `Avatar.base.vehicle_moveWith(flags)` before `updateArena()` returns. The
bridge opens `_client_ready` only after every materialization gate and the
preceding ready publications have passed, but before entering that period
callback. If period publication raises, the input gate is closed again and the
first failure remains latched. The lifecycle audit pins all three stock methods
and their synchronous call order.

Two full Windows dumps isolated the complete client-created Vehicle filter
boundary. The first access violation was in `WGVehicleFilter.syncGunAngles`
inside `Vehicle.__startWGPhysics`; the second run passed that address and
failed in `WGVehicleFilter.syncStabilisedYPR` inside
`PlayerAvatar.__onSetOwnVehicleAuxPhysicsData`. Both native methods reach the
same absent retail server-connection/filter chain. A complete exact-`#1513`
bytecode scan inventories every Python reference to those two methods and finds
four call sites: `Vehicle.__startWGPhysics`, `Vehicle.set_gunAnglesPacked`,
`CompoundAppearance.__onModelsRefresh`, and the Avatar auxiliary-physics
handler. The build audit rejects a missing or additional call site instead of
silently widening this compatibility seam. Reviewed public 0.9.22 observer
layers omit the initial and auxiliary calls; the packed-angle path is specific
to this LAN snapshot implementation, while damaged-model refresh is a stock
late path that must also be safe. During each exact handler only, the
compatibility layer presents a transparent filter proxy whose unsafe method is
a no-op. Physics creation, descriptor initialization, arena bounds, ownership,
`setVehiclePhysics`, visibility, speed providers, packed property values, model
refresh, auxiliary track/RPM updates and filter identity outside the scoped
stacks remain stock. Every scope is removed in `finally`, including when the
original handler raises; normal online execution delegates untouched.

Remote presentations have a separate readiness gate. Their newest health and
pose are coalesced while the #1513 compound assembler loads. A removal drops
the synthetic identity immediately; its late resource callback observes the
missing identity and cannot create an untracked visual. Map loading and local
Vehicle readiness have independent timeouts, and callback handles carry
generation tokens so an uncancellable callback from an earlier attempt cannot
clear a newer round's handle. Two false cross-version assumptions were removed
during review:

- build `#1513` calls `Vehicle.cell.trackRelativePointWithGun(point)`; the
  bridge now exposes that exact mailbox;
- `ARENA_UPDATE` has no `VEHICLE_REMOVED` value and `ClientArena` has no
  corresponding handler. Individual removal destroys the entity, while kill
  state uses the native `VEHICLE_KILLED` update and full cleanup uses the
  arena teardown.

Local input follows the exact stock path: `PlayerAvatar.moveVehicle` calls
`WGVehicleFilter.notifyInputKeysDown` before the explicit Avatar mailbox
relays the same flags. The mailbox must not notify the filter a second time or
bypass the stock movement guards. The client-created Vehicle has no retail
game-server transform stream, so its installed `WGVehiclePhysics` cannot be
the authoritative pose source. The copied 0.8.2 longitudinal, traverse,
terrain and collision
integrator owns the player pose and publishes it through the exact #1513
`Vehicle.model.matrix`, `ConsistentMatrices.__setTarget`,
`PlayerAvatar.getOwnVehicleSpeeds` and `PlayerAvatar.updateOwnVehiclePosition`
boundaries. The exact consumer audit proves that both `_SpeedStateHandler` and
stock shot-dispersion calculation read `getOwnVehicleSpeeds`; overriding only
`Vehicle.getSpeed` leaves the speedometer and movement bloom at zero. This is one pose owner,
not a second integrator layered over native server motion. The adapter now
leaves `PlayerAvatar.getOwnVehicleShotDispersionAngle` untouched: #1513 owns
the visible movement/traverse/turret/shot bloom, with all three motion
coefficients scaled to 25% so a fast light tank remains usable offline. The
trusted local shot samples the same read-only
`VehicleGunRotator.dispersionAngle` before firing, so the smaller HUD circle is
also the actual shot cone. The copied matrix is installed before the native
input handler starts and linked into both the attached and own
`ConsistentMatrices` sources; rebinding only
`_PlayerAvatar__ownVehicleStabMProv` leaves camera-direction and minimap
consumers at the spawn translation. During arcade/sniper changes the adapter
supplies the copied source before the new control's `enable()` and
`focusOnPos()` calculations run. The post-transition listener only verifies
that identity and raises on a stale provider. The exact fixed-turret gun path
receives the same pose through a caller-scoped filter proxy, without replacing
the native `WGVehicleFilter` object.
Remote humans and
bots are different:
retail `WGVehicleFilter` expects game-server pose samples after its input state,
and the offline connection has no such stream. The adapter therefore restores
the 0.8.2 carrier boundary: a Python gameplay vehicle owns authoritative
pose/health/collision and a separate `OfflineEntity` owns the rendered model.
The only version-specific substitution is #1513's verified
`prepareCompoundAssembler` resource path. This restores the map-base formation
and copied bot integrator without feeding a second physics owner.
`BigWorld.Entity.teleport` remains forbidden; #1513 rejects it for an in-world
client Vehicle as `Operation is not allowed`.

LAN pose samples retain the fractional remainder of the nominal 30 Hz
publication interval. Clearing the entire accumulator quantised a 40 FPS
render loop to 20 Hz and 45/50/75 FPS to 22.5/25/25 Hz. At most one current
pose is sent per rendered frame, so recovery from a slow frame never bursts
stale samples.

The player-visible spotting path now copies the 0.8.2 50-metre proximity,
two-height static LOS and allied observer relay. Its deterministic no-skill
memory uses the historical 5--10 second rule's guaranteed ten-second
disappearance bound. Enemy
compound models and their stock marker/minimap visuals cross one visibility
boundary, so an unspotted vehicle cannot remain visible in only one UI layer.
Authority Bot snapshots also retain the 0.8.2 no-rewind rule: the client that
integrates a Bot never reapplies its older server echo pose, while other
clients continue to interpolate those canonical snapshots.

Reload presentation follows #1513's event contract rather than its simulation
tick. The runtime sends `updateVehicleGunReloadTime` once when a reload starts
and once when it completes; the stock HUD derives the continuous remaining
time from `BigWorld.timeExact()`. Re-sending a decreasing value every 100 ms
restarted the client interpolation on each tick and produced a stepped
countdown.

The runtime publishes the exact `PREBATTLE` period tuple before enabling the
round and changes to `BATTLE` only after the countdown. `battle_live` is queued
as the tick-zero wire barrier and the tick thread publishes it before advancing
or emitting a snapshot. The client rejects an older timing tick, records the
receive time in the network thread, and projects the deadline on a monotonic
clock with half the measured RTT; main-thread stalls and wall-clock corrections
therefore cannot rewind the period. The authority's first
canonical bot manifest creates local bots without a server round trip, while
all bot `createEntity` calls are staggered during that countdown. Pose-less
`battle_start.bots` reservations are never inserted into `SnapshotSync`; doing
so allowed an empty map-loading snapshot to tombstone the entire lineup before
the authority manifest arrived.

## Aiming, shooting, health and death

The exact relative-aim call treats the point as relative coordinates. Stopping
gun tracking reconstructs world aim from the current hull yaw. A local shot
uses the public `gunRotator.getCurShotPosition()` boundary, performs client
map/vehicle collision and armor checks, and reports the proposed hit to the
server. The echoed server shot event calls `Vehicle.showShooting()` with the
descriptor's positive `gun.burst[0]` and the authoritative flag. Exact #1513
then cancels the local Avatar's shot-wait callback; zero is not a single-shot
sentinel and leaves the native firing extra unbounded. Remote events use the
same finite presentation without claiming prediction.

Critical-hit calculation follows the same proposal/commit boundary. The
firing client runs the copied 0.8.2 device law against an explicit detached
snapshot of the target descriptor, pose, collision components and critical
state. That calculation cannot change the live target or invoke native kill
and damage-panel callbacks. The proposal carries the target's exact base/ack
token and its pre-critical hull damage separately. If the target was repaired,
extinguished or otherwise revised before the report arrives, the server keeps
the ordinary hull damage and shot feedback but rejects the stale module state
and any obsolete ammo-rack damage amplification explicitly. A monotonic server
event is delivered before the snapshot containing its new HP/critical state;
the client presents stock shot results and battle events, then installs the
accepted revision exactly once.
Repair reports remain pending until the server acknowledges their proposal
revision, so a successful socket write or an older snapshot cannot rewind the
HUD state.

The stock debug controller reads `BigWorld.statPing()` and
`statLagDetected()`, which report the unavailable retail transport in this
client-only battle. A scoped, identity-safe `DebugPanel.updateDebugInfo`
wrapper substitutes only the attached LAN client's measured RTT and connection
state while the offline battle is active, and restores the stock method during
shutdown. The hello frame is sent atomically before `connected` becomes visible
to the poller, so a ping can never precede the required first protocol frame.

Server health is applied through the entity's health callback and the native
Avatar vehicle-health path. Crossing zero publishes `VEHICLE_KILLED`; a dead
local vehicle cannot move or fire, and a dead bot stops movement, targeting and
late fire events. The elimination result freezes all inputs until the server
returns the room to waiting.

Exact #1513 creates one `ArenaDataProvider`, exposes it through
`BattleSessionProvider.getArenaDP()`, and stores a `weakref.proxy` to that same
provider inside `BattleFeedbackAdaptor`. A proxy and its referent cannot pass
an object-identity comparison. The compatibility check therefore verifies the
public shared feedback adaptor, active marker provider and real
`FROM_PLAYER` classifier without inspecting feedback's private proxy identity.
The ABI audit pins both the weak-proxy construction and the setup property's
public forwarding chain.

Ordered player input separates three concepts so one recoverable rejected
frame cannot poison the rest of the round. The processed frontier is the
contiguous sequence that reached an idempotent terminal decision, applied or
not. The last applied input is the frame whose controls, pose, shell
selection and gun checkpoint were committed, and it remains what a fire
intent, pose sample, contact receipt and landing observation bind to. A
per-sequence record keeps a bounded fingerprint plus the typed outcome, so an
exact retry folds, a changed payload at the same sequence conflicts, a future
gap consumes nothing, and an evicted sequence can never become new state. A
recoverable validation failure records a rejected decision and advances only
the processed frontier: no field is applied and no gun checkpoint is
installed, so a fire intent bound to that sequence receives one typed
terminal rejection rather than firing from stale muzzle state. A message that
does not identify the current player, round or an exact sequence consumes no
frontier at all. Both frontiers retire together on a round transition, and
the snapshot publishes them so a reconnecting client resumes at the next
eligible sequence. The shipping client canonicalizes the same envelope before
queueing a frame, normalizing periodic yaw instead of clipping it, so normal
#1513 values never produce an avoidable rejection.

## AI, room and round boundaries

Humans take real team slots first. The first waiting 0.9.22 player owns map
selection and start; guests cannot race a second map choice into the room. Bots
fill the unoccupied slots so each team has exactly 15 vehicles. Battle-time late joins are rejected to prevent a
16th slot or an incomplete local manifest. A waiting-room membership is not
published to other handlers until its own `welcome` has been sent under the
same state lock, so another player cannot start a battle whose first message to
the new client would arrive before its identity and round assignment.

The elected authority client runs tactical bots using standard-map annotations,
vehicle roles, persistent randomized personalities, bounded line-of-sight
caching and local avoidance of terrain, water, steep slopes, obstacles and
nearby vehicles. The Python server remains canonical for room phase, HP, shot
events, elimination, capture, timeout and the copied five-second result
interval. The next waiting roster
is a synchronization barrier: the previous battle runtime is destroyed before
either the map picker or a queued next battle can cross the native Lobby/Hangar
readiness gate. Per-round phase is monotonic, so a delayed same-round waiting
roster or start denial cannot cancel an accepted battle, and snapshots cannot
be reordered across that barrier.

The pure-data server planner emits revisioned global `bot_orders`, which the
0.9.22 authority now uses for macro targets after reporting bounded visibility
observations. BigWorld terrain, collision, water and slope probes remain local,
and the client planner is a fallback when no server order is available. The
server copies the 0.8.2 standard-mode capture law: a 50-metre radius, one
update per second, at most three capture points per update, defender stop,
empty-base reset and victory at 100 points. Standard battles end by
elimination, capture or the server-owned 15-minute timeout.

The same canonical update drives a narrow defense context for the server
planner. One, two or three invaders request at most one, two or three eligible
responders respectively, selected by distance and vehicle profile speed and
sent only to a base that is currently invaded. The selection is stable across
updates and retains a short clear grace; dead, ungrounded, engine-destroyed or
double-tracked Bots are replaced. Travel overrides route movement but preserves
ordinary visible-target aim and fire admission. No unspotted invader position
is included in a Bot order.

This source wiring is not the same as final bot-behavior acceptance. The
finalized 0.8.2 spawn-congestion/OBB, reverse-steering and baked-route changes
are migrated as source-derived changes; real-client acceptance still has to
check them against #1513 terrain and presentation timing.

## Hidden-worker authority

Every 0.9.22 LAN room has one mandatory, room-owned hidden native worker. The
only simulation path is visible client -> LAN server -> hidden worker -> LAN
server -> replicas. Visible clients submit player input and fire intent, and
render server-admitted snapshots; they never become bot or projectile authority.

The launcher starts the server first, then the hidden worker, and advertises the
room only after both are ready. A missing worker refuses battle start. A worker
loss ends the active round as a technical failure without battle receipts and
leaves the room unavailable until the owner stops and starts a new room. The
server retains shared roster, timing, hit, receipt and result-ledger admission.

Native BigWorld worker code owns bot movement, map collision, projectile
progress, water sensing and native critical-state proposals. The server keeps the
ten-second drowning timer, then validates and commits the worker proposal; it
does not reconstruct vehicle descriptors, map collision, destructible identities
or a BigWorld-equivalent simulation.

The former pure-Python server authority, baked world, descriptor-projection
donation and destructible-map donation paths have been removed. The remaining
vehicle catalog is waiting-room metadata for vehicle tiers and does not provide
combat descriptors.

## Known deterministic parity gaps

The source audit deliberately keeps the following differences visible:

- the offline garage now publishes the complete optional-device and equipment
  catalogue (`items/__init__` item types 9 and 11) with shop prices, unlocks and
  owned stock, and the battle law consumes the same attribute factors the
  garage panel consumes, as recorded in the section above. What is
  the account command surface that MOUNTS them is now implemented in
  `account_rpc/garage.py`, which keeps one mutable copy of the bootstrap
  snapshot so the fitting writers share a single live record. The handled
  commands, all verified against this build's `AccountCommands.pyc`, are
  `CMD_EQUIP` 101 (module and gun swap), `CMD_EQUIP_OPTDEV` 102,
  `CMD_EQUIP_SHELLS` 103, `CMD_EQUIP_EQS` 104,
  `CMD_SET_AND_FILL_LAYOUTS` 108, `CMD_TMAN_ADD_SKILL` 151 and
  `CMD_TMAN_DROP_SKILLS` 152. Optional devices and modules are rebuilt through
  `VehicleDescr.installOptionalDevice`/`removeOptionalDevice`/`installComponent`
  and `makeCompactDescr`, crew skills through `TankmanDescr.addSkill`, and each
  accepted mutation is pushed with `PlayerAccount.update`, which unpickles its
  argument into the normal `_update` event path. A gun swap refills the default
  ammunition, because the new gun's shells would otherwise disagree with the
  shell inventory that `data._validate_selected_vehicle` cross-checks;
- purchases are implemented: `CMD_BUY_ITEM` 302 carries
  `(cacheRev, intCompactDescr, count, goldForCredits)` and
  `CMD_BUY_AND_EQUIP_ITEM` 308 carries
  `[cacheRev, compDescr, vehInvID, slotIdx, isPaidRemoval, gunCompDescr]`;
  `CMD_VEH_SETTINGS` 107 is the per-vehicle settings mask, not a purchase.
  Balances are unlimited by choice: the offline shop publishes every item at
  zero price, so a deduction would always subtract nothing, and ownership is the
  only part of a purchase with an observable effect. Buying a VEHICLE is a
  separate surface that is not implemented: `Shop.buy` routes a vehicle to
  `buyVehicle`, which needs its own command plus a new inventory record, crew
  and slot;
- the garage now persists to `mods/configs/offline_lan_0922/garage_state.json`,
  a sibling of `account_state.json` so each file keeps one owner. It stores
  mounted devices and modules through the vehicle's compact descriptor, plus
  consumables, shells, layouts, settings, learned crew skills and owned stock.
  Records are keyed on `vehicleTypeCompactDescr` and on the crew slot index,
  never on inventory ids, because those are renumbered whenever the vehicle in
  `config.json` changes. The file is never shipped in the overlay, is written
  atomically after each accepted change, and any unreadable or wrong-schema
  content logs one line and falls back to the stock garage;
- every module a vehicle type lists is published as owned and unlocked, so the
  research tree can mount any gun, turret, engine, chassis or radio. The lists
  come from the vehicle's own type, so a premium hull still offers only its own
  modules;
- the battle uses the garage loadout: the player's shells come from the mounted
  layout mapped onto the gun's shot order, and the consumables come from the
  mounted slots, so an empty slot carries nothing. Bots keep a synthetic
  loadout by design;
- the spotting law now applies the situational devices and the vision and
  concealment crew skills for the player and for authority bots. Coated optics
  stay implicit through `miscAttrs['circularVisionRadiusFactor']`, which
  `StaticFactorDevice.updateVehicleDescrAttrs` folds into the descriptor;
  binoculars and the camouflage net are applied explicitly because #1513 gives
  them only `updateVehicleAttrFactors`, which writes a caller-owned dict this
  port does not build. Binoculars replace the optics factor rather than stacking
  with it, exactly as `Stereoscope.updateVehicleAttrFactors` does, and the
  camouflage net adds `type.invisibilityDeltas['camouflageNetBonus']` to the
  stationary branch only. Both wait the descriptor's own
  `activateWhenStillSec`. Commander qualification, Recon
  (`commander_eagleEye`, best single crewman), Situational Awareness
  (`radioman_finder`, best single crewman) and crew Camouflage (averaged over
  the whole crew) are read from the garage crew;
- what remains unwired on that path: the stationary predicate itself is server
  law in retail, published through
  `Avatar.updateVehicleOptionalDeviceStatus`, and this client ships no cell
  script, so the port uses its own speed threshold with the client's 3.0 second
  delay; the camouflage paint bonus
  (`invisibilityDeltas['camouflageBonus']`) and `invisibilityDeltas`
  `firePenalty` are still not applied;
- the server publishes terminal winner/reason/base team plus live frags and the
  human team-killer flag, but not the complete 0.8.2
  `personal`/`players`/`vehicles` battle-result record;
- the complete stun penalty/medical-kit loop remains open. Bot movement and
  both Bot and human projectile trajectories run in the mandatory hidden
  native worker, while the LAN server admits their ordered results and shared
  ledgers. Each human client still originates its own input, pose and gun-state
  checkpoint. This is a trusted-LAN architecture, not an anti-cheat design or
  a claim that every calculation runs inside the Python server.

The local player path does include server-relayed critical state, fire,
drowning, exact fall/landing attribution, small repair/medkit/extinguisher
activation, native frag/team-killer updates, durable killer/reason metadata,
and server-deduplicated destructible results for collision and shots.
Each module states its own origin in its docstring.

## Reference implementations reviewed

The migration compared the local build with several public offline layers,
including the Tuxedo 0.9.22 observer, WOTClassicReborn's later observer fork,
the full `webiumsk/WOT-0.9.20.0` client source and
`Fedar459/WoTOfflineHangar0.9.22`. Tuxedo's useful pattern is the separation of
the training-window selection action from the later observer start; its direct
entity clear starts from Login rather than a fully initialized Hangar and is
therefore not copied into this Lobby path. The 0.9.20 source was useful for
checking Account, HangarSpace and Avatar ownership order, then every adopted
name and ordering was verified against local `#1513` bytecode. Broad
login-view replacements, blanket exception handling, forced process exit,
global entity-clear bypasses and development hotkeys were not carried into
this runtime.

## Automated and package verification

The test suites cover configuration, protocol ordering, fake Account RPC data,
stock picker installation/restoration, exact battle mailboxes, Vehicle property
packing, local movement, aiming, shooting, health/death, snapshot barriers,
active-round leave and authority transfer, same-poll lobby/start interleaving,
bot authority, tactical maps, 15-per-team allocation, elimination and
multi-round reset.

The release build additionally:

1. inspects the exact client version, build, executable architecture, required
   resource archives and pinned Avatar entity-definition hashes;
2. reads exact code objects from `scripts.pkg` and compares every stock method
   signature, direct-consumer literal, lifecycle name and `AccountCommands`
   constant used by the port, including variadic flags on the stock view
   loader, and inventories the complete exact-client call-site set for the two
   unsafe retail filter sync methods;
3. checks the ordered lifecycle contracts and inventories every exact
   Account-helper `setAccount` implementation, including the native
   Account-to-Hangar-to-Avatar retirement order, chat-proxy detachment and
   callback-registry initialization;
4. compiles every packaged source with CPython 2.7;
5. removes source and stale Python 3 bytecode;
6. requires the packaged PYC manifest to match every current source module and
   checks every PYC magic value;
7. rejects duplicate or corrupt archive members, `.pyo` and `__pycache__`;
8. requires Store compression and explicit directory entries for all wotmod
   members; and
9. produces a checksum and hash-named copy-ready client overlay.

The ABI gate is intentionally paired with consumer-contract tests. Python code
object signatures cannot describe Entity `.def` flags, mailbox wire arities,
dictionary keys or tuple lengths, so those are checked separately against the
actual producer data. LAN tests likewise reject malformed messages and stale
round identifiers before they cross a battle lifecycle barrier.

## Remaining empirical boundary

Static exact-bytecode review and simulated lifecycle tests cannot execute the
Windows BigWorld engine. The first real-client run still has to verify:

- the local engine accepts the complete native Vehicle property set and all
  30 entity presentations on the selected graphics/content configuration;
- the stock picker owns and releases the visible mouse correctly;
- map-specific collision/water queries produce sensible local steering on the
  real spaces;
- low- and high-speed contact on multiple maps destroys the intended fragile,
  falling and structure-module objects; low-speed cap admission holds its
  submission tick without publishing synthetic momentum, pending skins clear
  only through their exact registered OBB exit and a backing-ray recast, falling
  objects track and retain their native final collision pose, a five-item soft
  chain fails closed, and surviving backing collision remains solid;
- the kinematic layer, bot update budget and HUD remain usable at the target
  frame rate; this source validation does not claim that all visible movement
  hitches are resolved; and
- a full result -> lobby -> picker -> second battle cycle completes without a
  new traceback.

SPG/strategic camera movement, battle-settings capture-device enumeration and
combat-equipment placement remain outside the current standard vehicle-control
slice. Their exact mailboxes are not generalized into silent no-ops.

No additional Python mismatch is known in the consumer matrix above. Optional
lobby features outside that matrix and all BigWorld-side behavior still remain
empirical. If a real-client check fails, preserve `python.log`; the package
intentionally avoids noisy per-frame tracing so the first actionable traceback
remains visible.
