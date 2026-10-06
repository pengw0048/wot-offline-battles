# Compatibility review: World of Tanks 0.9.22.0.1 #1513

## September 24 exchange confirmation and elite-notification audit

Report `20260924-003508-5e4675963357` runs the v0.9.4 original-Bot077
payload, identity `colorfulmeans-v094-bot077-original-35874200209-1`.
The published Windows artifact was inspected: its garage/request producers
match the a3e4c593 behavioral baseline. This is not a stale full-set sender.

The exact #1513 Python resource contracts establish that
`ExchangeXpMeta.submit` enumerates `FULLY_ELITE` catalog vehicles, excluding
the research parent but not zero-XP/unowned entries. `Vehicle` treats an empty
`unlocksDescrs` as elite without an account elite flag. The offline conversion
loop wrote `vehicleXP[cd] = 0` for every empty candidate encountered before
its positive-XP source. `data.stats` includes XP-history keys among its elite
candidates. Consequently those new zero keys become new incremental elite
entries; native `Account._update` raises one elite event per entry. A synthetic
128-empty-candidate case reproduces the pollution and popup-producing delta.

The correction skips zero debits, preserving genuine positive/sold-vehicle
experience, existing zero history, source de-duplication, gold charging, and
real research transitions. It does not suppress elite dialogs or clear saved
research. A conversion followed by 65,600-XP research now produces only the
research parent's genuine elite notification, never the empty catalog entries.

The credit screenshot has 9,833,700 gold, a 400 rate, a 6,090,000-credit
shortfall and a correct 15,225-gold default. The unbounded maximum product is
3,933,480,000, exceeding signed 32-bit range. The exact Python meta publishes
`maxGoldValue = actualGold`. A presentation-only wrapper on the shared
`_ExchangeDialogMeta.makeVO` caps the maximum at floor(INT32_MAX / live rate)
without rewriting the wallet, rate, needed/default amount, or transaction.
It also bounds the same shared XP/restore control when applicable; ordinary
values, including the reported 2,624-gold XP dialog, remain identical.

Evidence limit: the original #1513 SWF was not supplied. The public 1.13 AS3
ConfirmExchangeBlock confirms the maxGold * rate dataflow but is not #1513
and uses Number for convertGold. Therefore the signed-overflow rendering
explanation is a strong screenshot-bound hypothesis, not recovered #1513
Flash proof. The range guard is covered by VO and explicit int32-model tests;
only a Windows retest can confirm that the credit field now displays/accepts
values. Do not describe that model as a native UI reproduction.

The adapter uses the existing pre-lobby service installation and rollback.
No Bot, map, penetration, movement, crew-requalification, price or research
rules are changed by this patch. PR #32 remains independently applicable.


This review is pinned to the Chinese HD client whose `version.xml` reports
`v.0.9.22.0.1 #1513`. The executable is 32-bit x86. Packaged client modules use
CPython 2.7 bytecode magic `03 f3 0d 0a`; the embedded build identifies itself
as Python 2.7.7.

The repository, launcher, server payload and installable package support only
this #1513 client. References below to the retired 0.8.2 line identify
historical algorithm provenance only; no code, assets, payload, installer path
or runtime for that client remains. Repository-relative paths use the current
root layout, including `client_overlay/`, `server/`, `src/`, `tools/` and
`tests/`. Paths under `mods/` and `res_mods/` below describe the installed
client or package layout.

## September 20 MT-11 ramming mission follow-up

The user reports that MT-11 cannot complete. The available 0.9.22 RU #788
reference definitions reproduce three unsupported evaluator boundaries:
operation 1 uses `vehicleDamage/attackReason=2`, operation 3 adds
`rammingInfo=stayedAlive`, and operation 4 requires `lvlDiff=1`. Operation 2's
ram-kill condition already has a supported death-reason counter. The four
main/honours expressions are retained in `mt11_conditions_0922.json`; this is
reference XML evidence, not a newly audited #1513 archive. Production still
reads the installed #1513 mission definitions and changes no quest resource,
reward, collision force, or matchmaking rule.

The evaluator now consumes per-collision ramming evidence for damage and
survival-qualified kills, and compares the actual attacker/target descriptor
levels for a nonnegative minimum `lvlDiff`. The shared `stayedAlive` and
`dealtMoreDamage` modifiers require the same killing collision; surviving an
earlier ram or dying later in battle cannot substitute for its outcome.
Likewise, a later shell kill cannot erase already earned ramming damage.
The generic modifiers also serve other installed missions using those fields.
The September 21 follow-up below adds recorded `fireStarted` evidence;
other unrecorded filters remain explicitly unsupported.

Version 2 of the existing bounded mission history adds
`[ram, elapsed_ms, damage_dealt, damage_received, killed, survived, immobilized]`.
Both server-owned human contacts and worker-proved Bot/human or Bot/Bot
contacts record these facts only after both HP changes settle, before battle
completion. Damage is the applied, HP-capped amount. Existing operation
deduplication and friendly-contact no-op gates precede publication. Ram rows
share the existing per-actor event budget; overflow remains incomplete.
Client validation, receipt persistence and post-battle normalization use the
same field contract. Legacy histories remain readable but do not fabricate
ramming evidence. Existing ordinary damage and kill rows are unchanged.

Regressions cover all four main/honours conditions, nonlethal rams, later
shell kills, simultaneous destruction, later death, same/higher/lower target
tiers, strictly greater damage, missing/legacy/capped evidence, all three
contact pairings, duplicate worker reports, receipt restart and exactly-once
mission settlement. Actual completion and rewards on the user's #1513 client
remain the Windows acceptance boundary.

The preceding full CI also found that the visible pivot adapter forwarded a
negative reverse drive cap where its existing contract expects a magnitude.
It now passes the selected directional cap's absolute value, preserving the
real signed movement speed and the shared sweep's existing magnitude policy.
The original forward/reverse pivot regression is retained.

## September 20 barracks sorting follow-up

Report `20260920-193111-5b5930c0e65c`, build
`colorfulmeans-35488817004-1`, records 18 failures in
`Barracks.__showActiveTankmen`: `TypeError: comparison function must return
int, not long`. Four inventory publications agree on 222 seated crew and 7
barracks crew, with 229 descriptors and vehicle references, no missing or
extra foreign keys, and 30 berths. This is an observed sorting failure, not
evidence of a crew-count limit or a failed inventory transfer.

The port deliberately publishes shop currency amounts as Python 2 `long`
for the native price formatter. The reference Python call chain forwards
`FittingItem.__cmp__` price subtraction through `Vehicle.__cmp__` and
`TankmenComparator` into the barracks `sorted` call. That reference is an
investigation lead, not a new exact-archive audit; the supplied #1513 runtime
trace establishes the failing comparison boundary. The regression executes
the port's real price conversion and the new comparison adapter on Python 3
with strict integer-type checking, and on real CPython 2.7.18 in packaging CI.

The existing reversible lobby-service adapter now normalizes the original
fitting comparator's result to the integer sign -1, 0 or 1. It preserves all
stock comparison decisions, exceptions, crew identities and native price
longs. Existing vehicle subclasses and direct fitting-item sorts share the
fix. Installation precedes account/lobby creation, survives battle/garage
transitions, is idempotent through the service installer, and is rolled back
with the other service hooks. The affected Windows save still needs a
barracks-open/filter retest; local tests do not establish Scaleform acceptance.

## September 20 19:49 tree registry and Siege contact follow-up

Report `20260920-194949-1aadaa199835` runs the preceding tree diagnostic build,
`colorfulmeans-35507658229-1`, on CN HD #1513. The new marked minimap locates
the same railside row near C6--D6. Poplars in chunk 32642 have exact native
commit and presentation receipts. Those in chunks 32641, 32640 and 32639
instead report `name_status=isolated_item`, no registered chunk and no cached
health. The vehicle reaches 0.637 m from `(32641, 63)` and 0.451 m from
`(32640, 110)` without a tree contact token. The indexed-vector fix therefore
did not resolve this separate registration defect.

For a compacted filename list, v9's capability to repair layouts was mistaken
for an actually pending layout repair. An authored mode-excluded slot then
terminated enumeration as an isolated native object, preventing valid trees
and models later in that chunk from registering. The compacted-list path now
uses the same absent-slot rule as the full-width path: skip an authored absent
item unless this particular chunk has a pending remap. It does not query an
excluded scene object, infer a filename, bypass a quarantine, or destroy a
nearby object by position. A regression reproduces the lost tree proposal and
requires the valid tree and fence behind the absent slot to register while the
excluded slot receives no native category or matrix call.

The Paris S1 capture at 19:46:40 shows stable ENABLED state, no pending switch,
no handbrake, 2.22224 m/s directional limits, and `path=brake`,
`world=hard`, `kinds=falling,fragile`. The powered contact policy used that
low-gear limit as crush eligibility, and excluded falling columns altogether.
The shared player/worker/Bot path now retains the mounted travel descriptor's
directional limit for powered contact eligibility, including pivot contacts;
the active mode still owns actual velocity, traverse, geometry and mass.
Columns now share exact-contact cap admission with fragile and structure
parts. Their native order and replicated event still carry real impact speed.
The stock scale/health test, unpowered/traverse-disabled gates, exact overlap
and native backing-wall recasts remain. This is a correction to the existing
offline powered-contact policy, not a recovered retail low-gear force law or
a map-specific collision exception.

The user also reports continuous hitches in both modes. One later slowdown
has a native material-111/item-11/chunk-33154 wall witness; nearby destructibles
do not prove ownership of that face, so it remains blocking. Existing 30 s
visible frame timing is responsive, but stall-only records cannot establish
the cause of every small repeated correction or native visual hitch. Bounded
`HYDRAULIC MOTION` windows now summarize all moving slices in either mode and
retain seven worst witnesses: integration interval, drive/horizontal/settling
speed loss, missing travel, height change and attitude change. They include
input changes, airborne/support counts and the already sampled support face.
The two-second reporting interval only batches diagnostics; it never gates
motion, performs new native probes or waits for a worker acknowledgement.
Exact-client tree falling, low-speed/pivot destruction and continuous hydraulic
travel remain Windows acceptance items.

## September 20 front-wheel and hull damage investigation

The user reports losing the simultaneous track-break/HP-damage outcome. The
ordered material resolver already continues from a penetrated external track
to a reached structural plate; a track material alone never proves hull damage.
A new integrated regression uses real armour resolution, HP rolling and track
critical damage for both player and Bot launches. It covers a front-wheel hit
followed by a penetrated hull (both losses), no hull (track only), a hull beyond
available penetration (track only), and a lower-damage shell (HP loss without
breaking a fresh track). These establish the code path, not a native collision
mesh or the user's particular shot. No penetration or track-damage law changes.

The existing bounded track diagnostic now also records a shot-correlated
`TRACK OUTCOME`: native track/structural distances, whether structural contacts
survived the existing trace budget, the terminal armour verdict, and proposed
vehicle/track HP losses. These are proposals, not server-commit receipts. The
diagnostic has no new native queries, collision mutation or retry behaviour;
logging failures cannot cost a hit. The historical ten-calibre budget is left
unchanged pending exact evidence about a missed shot, rather than extending
projectiles through armour on the basis of a generic aiming guide.

## September 20 Prokhorovka railside tree follow-up

The user's two screenshots locate the pass-through report beside
the railway near C6. The matching north/south row includes six authored
Poplar/Poplar_1 placements at X 93.366--96.783, Z 257.074--361.773, including
`(32641, 64)` at `(94.984, 6.043, 274.026)`. All six have shipped fall-foliage
profiles. Screenshot coordinates identify the row, not a measured vehicle pose
or a uniquely proved individual contact. The earlier unnamed item at
`(-80.100, 6.997, 337.100)` is elsewhere and does not diagnose this report.
The user clarifies that the vehicle passes through without contact handling.

The continuous tree sweep sliced each hit-tester corner with `corner[:3]`.
An indexed coordinate vector that does not support list slicing made the
entire sweep return `None`; the sensor then returned `hard`, which the visible
adapter intentionally treats as unavailable tree evidence rather than a wall.
This representation-dependent loss is reproduced by a strict indexed-vector
fixture, both directly and through tree proposal/registration. Corners now use
indices 0, 1 and 2, matching the other hull consumers. Geometry tests cover all
six shipped placements and a separate lane that must not knock them down.
This establishes the local failure and fix; it does not prove that the corner
objects in the user's Windows session caused this particular pass-through.

Ordinary reports now include bounded `LOCAL TREE` records without requiring
debug mode or additional native queries. They preserve the raw tree verdict
before the visible adapter, the actual sweep endpoints/yaws and corner type,
nearby authored identities/positions, registry position, cached tree health,
name-alignment progress, isolation/layout state, and contact/publication state.
The original native order's presentation observation and fall-pitch constraint
remain labeled as acceptance-time evidence; reading them does not replay an
order or restart an animation. A fresh local commit is recorded immediately;
unchanged vicinity samples are suppressed. The 0.5 s limit belongs only to
diagnostic sampling, never movement, collision admission or destruction.
Index caches follow space and proved layout changes, and observer failures do
not change motion. Exact #1513 Windows retesting is still needed to establish
that this fixes the reported trees. No map object is destroyed merely because
it is near a screenshot coordinate.

## September 20 Paris ledge, Prague doors and Mittengard follow-up

Reports `172803-3d53240d5fa3` and `173230-78dde2547f94` identify installed
build `colorfulmeans-35500571635-1` on the Chinese HD #1513 client. The
accompanying `100_thepit.pkg` contains the compiled WTCP v2 control points;
its two CTF flags both author a 30 m radius, at the already-shipped objective
centres. The baker now matches circle radii by team and objective coordinates,
and the spawn planner carries those circles through worker readiness to the
server. Capture occupancy and defense threats use the same radius. Only this
supplied map catalog is updated; old catalogs without a decoded radius retain
the previous 50 m behavior. No repair-point radius or estimated visual size is
used. Tests cover the 30 m boundary for humans and Bots, both bases, malformed
explicit radii, and ambiguous/missing authored control points.

Prague captures four original-material-73 contacts against two workshop door
placements, `(32640, 89)` and `(32384, 67)`. Every witness is inside that live
item's already-broken material-74 panel and outside its intact 73/75 parts.
The new exact-key recast is bounded by the broken component, stops before any
intact component, and retains replacement materials and backing walls. It
requires live, non-isolated ownership; it does not infer a global material
translation or clear the complete building. All four native witnesses are
stored in the regression fixture, including their original owner bounds.

Paris records pitch `-0.566140128737696`, roll `-0.3627829348825807`, and
`plane=null` unchanged in travel and siege, while the blocking native terrain
normal has Y approximately 0.993. The logs explicitly exclude hydraulic tanks
from the ten-spring trial; these are legacy support contacts, not spring
solver failures. Legacy height previously followed the centre column while
attitude rejected non-planar five-point samples and retained the old tilt.
The legacy player and hydraulic Bot paths now derive height and attitude from
one supporting face of the sampled chassis footprint. The face covers the
centre and does not penetrate any sampled point. Equal-height ridge faces
share their gradients rather than choosing an arbitrary diagonal. A face
bridging different surfaces is not published as a continuous grade for slope
slide. The existing gravity/reachability and raised-obstacle gates remain;
there are no added waits or drive/brake coefficient changes. The player reuses
its five support columns for attitude; ordinary Bot probe budgets are
unchanged. Local hard-contact reports include the accepted legacy support
sample. This geometry fixes the reproduced stale-attitude/centre-height
mismatch, but cannot prove native Paris ledge feel or hydraulic rendering.
Windows travel/siege acceptance at the reported edge is still required.

Prohorovka trees remain unproved: the report has anonymous placement and name
alignment gaps, but no contact identity tying a failed tree to one of them.
In particular `(32386, 3)` has an unnamed transform absent from the shipped
placement catalog; the log does not establish that it is the reported tree.
Do not assign it a neighboring tree's name or relax the native identity guard.
The affected tree's position and native resource/placement evidence are still
needed. The new Murovanka end-face witnesses remain outside the recorded owner
boxes, so no additional blanket exclusion is introduced. Paris E-line traces
include active turning and safe navigation with zero throttle, followed by
progress, as well as deliberate tactical holds; this does not prove that every
reported opening stall is fixed. No AI timing or performance change is made.
The owner currently cannot reproduce the black stun-assist display; its UI
remains unchanged. TD2/LT5/HT4 fixes from the preceding revisions are retained.

## September 20 additional HT4 follow-up

HT4's four regular definitions request `innerModuleCritCount`, whereas TD2
requests `innerModuleDestrCount`. The former was missing from the mission
evaluator, so a qualifying battle remained unknown rather than completing.
HT4 now counts damaged or destroyed internal devices and knocked-out crew
from complete accepted critical-event histories. Damage and destruction bits
for the same device in one transition count once; subsequent recorded
transitions after repair still count. External devices, friendly targets,
unchanged repeated critical-state publication and incomplete histories cannot
award progress. TD2 retains its destruction-only condition.

The four main thresholds (1, 3, 5, 6) and their distinct honor requirements
are captured in `ht4_conditions_0922.json` from the public 0.9.22 definitions,
not a new exact #1513 archive audit. Tests cover exact thresholds, one below,
server receipt persistence, client normalization, mission selection and honor
rejection. No thresholds or rewards were changed.

The later Paris/Prague/Mittengard follow-up above supersedes the missing
map-circle evidence and records the new native reports. Murovanka's unproved
end face still needs its owner geometry.
The stock reference reader obtains only base centers from teamBasePositions;
repair/resource-point radii in ArenaType are unrelated to base capture.

## September 20 v0.9.1 follow-up: downhill contact, missions and Murovanka

Reports `125847-f5293210b1b9`, `132016-abb5404de554`,
`135507-8b1562002689` and `143656-a905f3fed979` run the v0.9.1 build
`colorfulmeans-35488817004-1`. This follow-up has no added movement delay,
performance policy change, map-specific driving coefficient or capture-radius
guess. The changes below are logic fixes pending exact Windows acceptance.

The Strv S1 travel reports show disabled Siege, no handbrake and no Siege
drive lock. An allegedly airborne hull repeatedly contacts upward-facing
ground. The legacy vertical integrator omitted the downward tangent velocity
while following a slope and eased reachable support Y, opening another gap on
the next tick. Player and Bot legacy paths now preserve signed tangent
velocity from the supported chassis pitch and commit reachable support
directly. The ahead-looking drive probe is not a momentum source: it can see
a drop while the tracks still rest on a rim. Their ballistic reach check
still rejects remote cliff floors. Player physical pitch/roll now immediately
match an accepted ground plane: easing the physical pose after settling Y
buried the nose at a slope-to-flat transition and fed the tilted shape into
the next collision sweep. Drive-gravity smoothing remains separate. Continuous
forward/reverse downhill, cliff departure and the reported Paris flat-ground
pose have focused regressions. They do not prove every Paris sinking report
is resolved in native gameplay.

TD2 lacked `innerModuleDestrCount` and the final own internal-critical state.
Count only destroyed internal devices and knocked-out crew from complete
accepted critical-event histories; external tracks/gun/observation devices and
yellow-only internal damage do not satisfy the destruction requirement.
The secondary condition reads current final state, including repairs, rather
than accumulated damage history. Missing historical end-state evidence stays
unknown through persistence and client normalization. LT5 now records a
per-victim radio-assisted kill for the observers eligible at the canonical
kill, including a crew knockout without HP loss or a final HP share rounded
to zero. Earlier spotting damage alone
does not award a later kill, and repeated publication cannot double this
per-victim count. The receipt field survives server storage, client validation
and account normalization. Fixtures contain the four campaigns' TD2/LT5
conditions transcribed from the public 0.9.22 personal-mission definitions;
they are version reference evidence, not a fresh #1513 archive audit.

Murovanka's chunk 32635 reports a completed native layout repair (63 proved
items, 67 native slots, 30 remapped). Four captured contacts have original
material 73, flags 0 and a callback item different from the registered,
already-broken stone-fence placement containing the witness. These exact keys
can now be recast only inside the proved owner envelope in the same repaired
chunk. This does not extend the anonymous material/flags wildcard. Live owners,
unknown layouts, trees, damaged replacements and backing walls remain solid.
The fifth captured end face lies outside all recorded owner boxes and remains
unresolved; the test deliberately preserves it. Exact native collider/owner
evidence is needed before excluding that face safely.

Paris E-line AI is not declared fixed. Captured movement orders can select a
nearby point behind the hull, while other stationary vehicles have deliberate
`support_hold` orders. A baked-graph replay of the recorded poses advances to
the next corridor point; the live obstruction/planner state is not present in
the old diagnostic. Existing rate-limited Bot stall output now includes the
selected path index, nearby path points, planned goal and navigation status,
without additional native probes or changed planner timing.

At this earlier checkpoint Mittengard lacked the original circle asset.
The subsequently supplied WTCP data resolves that gap as described above.
Numeric stun-assist fields and the SPG redesign flag were already populated;
the latest owner report no longer reproduces the color issue.

Performance diagnosis only, from live PERF windows:

| Map / report | Visible FPS range | Worker FPS range | Maximum worker execution |
| --- | ---: | ---: | ---: |
| Paris / 125847 | 66.75-74.42 | 5.69-31.62 | 337.473 ms |
| Ruinberg / 132016 | 58.60-82.25 | 0.88-26.66 | 1259.174 ms |
| Swamp / 132016 | 67.02-75.71 | 1.95-27.12 | 858.159 ms |
| Highway / 135507 | 99.42-109.69 | 35.85-85.45 | 164.371 ms |

Windows and hardware differ, and the first live window can include prebattle
samples, so these ranges are not comparable benchmarks. The worst Paris
sample spends 328.593 ms of 337.473 ms in `bots_update`; the worst Ruinberg
sample spends 1222.600 ms of 1259.174 ms there. Detailed traces include dense
motion, support and collision queries. Visible rendering remains responsive
while the localhost authority falls behind, which explains delayed AI/shot
feedback without attributing it to internet ping. Murovanka's fence report has
no equivalent worker slowdown (maximum execution 16.565 ms). No performance
optimization is included, as requested.

## September 20 follow-up: remaining fence normals and concrete support

Report `wot-error-report-20260920-111808-ab28648cb0ee.zip` runs
`colorfulmeans-35485084742-1` (`03acfc7e`). The owner reports another substantial
improvement, a few remaining Malinovka fence blockers, and a Paris concrete
surface that the chassis sinks into and cannot cross.

The fourteen Malinovka witnesses still belong to accepted broken original
components. Their normalized native-normal/authored-up dot products are
approximately `1.065e-6` to `1.537e-6`, just outside the previous `1e-6`
parallelism tolerance. Accept float32 transform/normal disagreement with a
`1e-5` angular tolerance. This changes neither the authored ownership footprint
nor the native ray budget, and cannot bypass live modules or backing walls.
All fourteen captured witnesses replay with both full and nearest-first native
callback traversal; the previous predicate fails 56 replay subcases.

Paris contacts around X -12 to +4, Z 195 to 201 identify vehicle-only material
111, not a destroyed prop. The captured spring layers show the real concrete
top near Y 2.62 rejected as `above_flat_limit`, followed by terrain near Y 1.8
to 2.0 underneath it. A tilted chassis's low carriers were allowed to sample
an incline in their existing penetration band, but could not acquire a flat
deck inside the height range already reached by the high carriers. Share the
highest posed carrier compression ceiling for grounded suspension queries in
both player and Bot adapters. Each original column's vertical interval still
bounds the query; airborne queries retain their individual compression limit,
and a roof above the whole compression envelope still cannot become support.
No extra suspension columns or solver iterations are introduced.

Two horizontal checks complete that support correction. A gently sloping
concrete top must not become a wall merely because the lane's net height
change is below 0.15 m: require an upward native face inside the posed track
height range, the exact native top, a continuous bounded profile, a clear
remainder and the existing upper hull lanes. The established supported-step
check also accepts a bevel as its outside face while retaining two broad,
nearly level inside support columns, its existing height limits and a clear
lifted body corridor. Neither check is keyed to a map, material or filename.

The new fixtures retain the report's 34 hard-contact records. Concrete tests
use captured deck/bevel planes and three road samples, with explicitly
controlled unrecorded geometry and suspension descriptors; they do not claim
to reconstruct the complete native map or vehicle. They cover recovery from
the reported poses, continuous forward/reverse deck crossing, player/Bot
support parity, airborne limits, roofs and backing walls. The previous
support/world code fails sixteen concrete replay subcases. Existing cross-map
fence, wall, bridge, slope, pivot and Bot checks remain part of validation.

The report also has a Malinovka worker window near 4.8 FPS with roughly 180 ms
per frame in Bot updates, while the visible client remains around 90 FPS.
That is retained as a Windows performance comparison point, not attributed
to the fence normal or declared fixed by these logic tests. Exact Windows
support, collision feel and frame pacing remain the acceptance boundary.

## September 20 follow-up: tilted fence skins and powered pivot contact

Report `wot-error-report-20260920-102444-ea19226db90e.zip` runs the restored
`colorfulmeans-35483012880-1` build (`842a3fe2`). The owner reports much better
Malinovka collision, with remaining invisible fence blockers, and Paris props
which yield to forward movement but not chassis rotation.

All thirteen Malinovka hard-contact records identify anonymous original
material 73/74 on accepted broken military-fence components. The witnesses
sit above their damage boxes. These placements are tilted: their native side
normals are perpendicular to the model's authored up axis, but their world-Y
normal components are nonzero. The old world-vertical test therefore misses
them. A neighbouring model's union can also contain the witness while its
same-material component does not; that must not suppress the actual owner's
projected search.

Clip the ownership footprint along the authored up axis, using the box's
dual face axes, and require a native side-face normal in that same frame.
Keep live, isolated and unregistered stacked owners blocking. Bound the
exclusion before live components and retain the existing native recast budget.
This changes ownership evidence only, without enlarging destruction geometry
or filtering damaged/vehicle-only materials. The new fixture keeps all thirteen
rays, normals, nearby component states and native witness records. Their
before-fix replay fails 52 subcases; the corrected replays preserve backing
walls one millimetre behind the original skin and restore blocking when
destruction is revoked. Existing cross-map coverage still exercises 40 maps
and 750 placed collider variants.

Paris logs five zero-speed `turn_contact` stops at four distinct positions.
The rotating path qualifies crushes with only angular limit times hull radius,
whereas powered translation already admits exact contact using the directional
drive limit. A prop above the angular gate can therefore reject every first
turn and still crush when driven into. Pass the same effective directional
drive limit to the pivot proposal and commit, for the visible client, worker
revalidation and Bots. A disabled traverse cap cannot grant this admission.
The stock mass, scale and live descriptor-health law remains the final gate;
actual impact speed, sweep geometry and published motion remain unchanged.
The ordinary native world check still owns real walls and replacement BSPs.

Tests reconstruct the four report positions against the shipped Paris catalog
and exercise the real sensor/kinetic gate, proposal and commit with controlled
descriptor health. The report does not contain the rejected candidate yaw or
live health cache; these tests establish the adapter defect and its correction,
not those missing native values. Controls cover high-health props, insufficient
vehicle mass, disabled traverse, distant/no-turn hulls, one-time exact commits,
directional limits, and player/worker/Bot forwarding. No movement law, recursive
rotation refinement or per-query logging is introduced. Actual Windows #1513
destruction, remaining collision and frame pacing require another playtest.

## September 20: restore the requested baseline and correct fence ownership

The owner reports that `ead138e3` still freezes and leaves fence air walls.
The requested screenshot names report
`wot-error-report-20260919-202347-ee921fa2d946.zip`. Its installed build
`colorfulmeans-35441583755-1` and the corresponding workflow run both identify
`f84001a4f7bc8d80c92e648f7d0cacb98703af0c` as the baseline.

The new `094514-d77cc4339add` report confirms the previously delivered
`colorfulmeans-35481400764-1` build. Its player capture records 3,679 rotation
envelope refinements at one body origin, reaching about 3.49e-17 radians.
The worker's last complete performance window averages 475.1 ms of Python
execution per frame and 2.06 frames/second. The two clients emit 466,503 valid
structured physics rows; their raw logs total 475,441,607 bytes. This disproves
the earlier implication that the query reuse and encoding correction were
sufficient to resolve the reported freeze. The final worker disconnect alone
does not identify a native crash.

Restore the complete f84001a4 implementation before applying the focused
ownership correction below. This removes the later rotation refinement,
full-query diagnostic emission and broad contact/drive/suspension changes,
including their follow-up patches and tests. The older movement laws and
diagnostic cadence are those of the explicitly requested baseline. The
rollback is a new branch commit; it does not rewrite published history.

The baseline report contains five Malinovka fence witnesses for original
compiled material 73. The contacted component has already been accepted as
destroyed, while the next placement's material-73 half remains intact farther
along the fence. Their whole-model envelopes overlap. The previous ownership
predicate let that unrelated live half veto the destroyed component at the
actual hit, so its invisible original BSP remained solid.

Resolve each anonymous original material against the components containing
the native witness. Use the unanimous whole-model proof only when no module
box contains that original face. Bound the filtered segment before a later
live component, including another component in a model already considered at
the first hit. Recast the complete bounded segment so replacement material
88 and vehicle-only material 111 still block, including a wall one millimetre
behind the removed skin. No map/model-name exceptions are used.

The fixture preserves all five complete contact records and their component
states. Before this correction, every reported original face remains blocked;
afterward each clears in at most four native replay calls. Revoking accepted
destruction restores the block. Controls retain live neighbouring components,
damaged replacements and vehicle-only backing walls with both reordered and
pruned native callbacks. Existing cross-map railing replays cover 40 maps and
750 placed collider variants. These establish the ownership correction in the
shared query adapter. Actual fence traversal and frame pacing still require
the restored build on Windows #1513.

The post-0.8.3 gameplay follow-up addresses nine reported paths. A hidden remote
vehicle retires its engine-audition component and detailed-engine callbacks.
Report `83fea4595275` from the owner's #1513 client records an abort on Lakeville
at 05:14:18 on September 16: `MF_ASSERT_DEV FAILED: isOwning() && "This wrapper
own nothing"`, `wot_svarog/py_wrappers/py_systems.cpp(46)`. The termination dump
retains that native assertion banner, but no live Python traceback. The former
hide/reveal code retained and re-added a removed native wrapper, which violates
the one-time ownership transfer implicated by that assertion. The earlier
SimpleNamespace test did not model this guard and incorrectly accepted reuse.

Reveal now calls the installed client's `model_assembler.assembleVehicleAudition`
to build a fresh NPC sound owner, restores the water-sensor links, weapon energy
and model attachment, then subscribes fresh detailed-engine callbacks. This
follows the stock assembly/start sequence reviewed in the 9.22 reference;
the exact #1513 executable and scripts archive were unavailable for a new
bytecode audit in this environment. The still-live detailed engine state keeps
its LAN motion links while muted. Death, world exit and a changed appearance
generation cannot restore a removed owner. A partial Python-side binding
failure retires the partial sound component and allows a later reveal to retry.
Guarded tests cover repeated ownership transfer, callback retirement, model
replacement, startVisual deferral and assembly reentry. Native audible
silence/restart and crash-free repeated spotting still require Windows #1513
acceptance of the corrected build.

The crash-text scanner now retains real assertion banners containing a
BuildAgent source path. That path incorrectly classified this report's
assertion as a static template, leaving its useful message only inside the
dump. Unexpanded printf templates remain excluded, and the repaired scanner
extracts this report's complete assertion from the original dump.

The owner confirmed that the published build already releases an SPG's lock
when its target becomes unspotted. Its auto-aim behavior is unchanged in this
follow-up. A third-party plugin conflict remains a hypothesis for the group's
report; no particular plugin or failing session has been identified.

HE direct impact presentation selects `armorHit` when HP damage is positive
and `armorResisted` otherwise; the original physical penetration result stays
in the damage/statistics ledger. AP and HE near-miss presentation retain their
existing groups. Ordinary paid equipment demounting now publishes and charges
10 gold in both economy modes; the descriptor still determines freely
removable equipment, and improved equipment retains its 200-bond cost.

Native shell `Stun` fields now survive descriptor donation, effective-parameter
validation and frozen projectile transport. The mandatory worker attaches
duration and stat multipliers to established direct/visible blast contacts,
including zero-HP blast contacts. It reads the installed `items.stun.g_cfg`
and target resistance rather than class names or modern balance constants.
The existing server stun state, expiry, assistance and medical-kit flow carries
those multipliers to human movement/aim/reload/vision and Bot combat state.
An already elapsed stun does not discard an otherwise valid delayed damage
batch; a shorter follow-up does not truncate an existing stun. This restores
the previously missing generation/penalty path, not the unavailable retail
cell implementation: exact overlap-strength/assistance sharing, partial-cover
stun weighting and native visual/audio acceptance remain boundaries.

Destructible controllers now spawn at the centre of the chunk encoded by the
admitted native identity, using the stock 100-metre/127-offset mapping. A
straddling object's impact/bounds position must not put its controller in a
neighbouring chunk and trigger replacement on subsequent contacts. Name
alignment retains each successful category proof before a later per-slot
catalog/matrix failure, so rebuilding an evicted compact-name mapping does
not quarantine the entire chunk. Unknown isolated slots remain solid and
unqueried. Regression tests prove the lifecycle/rebuild cases; no measured FPS
gain or identification of every reported scenery object is claimed.

The follow-up also applies the save earnings multiplier to bonds at the
existing atomic garage settlement. It does not apply premium coefficients or
scale directive service charges. Result packing and lifetime counters now
respect the durable award; scaled medal rows apportion integer rounding
without changing the server's historical medal schedule. A retried receipt
keeps its original settled multiplier, even after editing the save setting.

Successful damage to a still-destroyed device resets its repair progress to
zero. Failed module rolls and hull-only hits do not restart repair. Bot
projectile damage now rebases its successful device/crew operations over the
canonical state, as human-target damage already does, instead of dropping
module damage when a repair publication overtakes the shot. A fresh damage
lineage is opened even for a zero-to-zero hit on a repairing device, preventing
an older repair checkpoint from undoing the hit. The native countdown is
repopulated from the new progress. Per-device retail repair rates remain the
previously documented reconstruction; this does not claim exact retail timing.

Vent Purge's reported purchase failure was not reproduced by the owner.
All fifteen directive prices, depot purchases, fourth-slot layout fills and
automatic resupply paths have regression coverage, including a 12-bond Vent
Purge purchase refused at an 11-bond balance with no inventory mutation.
No general purchase defect was established, so the buying policy is unchanged.

The owner's September 15 report `c293714d6eed` records one HE projectile,
`3:p:1:3`, charging the same allied Bot 334 HP three times (1002 total) after
a `projectile_resolve` ValueError. The archive contains neither that exception's
stack nor the complete proposal/pose evidence: it establishes the duplicate
settlement, but not the original exception cause or whether the initial close
contact was physically correct. Admission formerly retired a projectile only
after applying all victims, allowing a later exception to replay earlier HP
and statistics. A fully admitted terminal now retires before any victim
mutation; failures remain local to one victim. An unverifiable critical profile
discards module/ammo-rack augmentation while retaining the independently
established hull damage. Tests reproduce a failing self-splash after a direct
ally hit, unexpected post-hit failure, duplicates and zero-damage contacts.
Native result `teamHitsDamage` pairs `tkills` with `tdamageDealt`; its zero
count is allied kills, not the number of allies damaged.

Eligible friendly HP loss now has a per-victim ledger shared by public XP,
private receipts and client settlement. Self damage and already-blue victims
are excluded from economic penalties. The published
[official guide](https://wotgame.cn/zh-cn/content/guide/general/teamkill/)
specifies repair compensation, an additional 10% credit fine, payment before
maintenance, victim compensation independent of offender funds, and XP
penalties. That retained guide is not an archived 0.9.22 server formula. Native
victim `VehicleDescr.type.repairCost` prices hull damage just as the existing
garage repair bill does; module/stun-only costs and unpublished XP coefficients
are unavailable. XP therefore reverses this product's existing offline
damage/kill valuation, capped at the battle's gross XP; free XP derives from
the remainder. No new coefficient is presented as retail. Credits use the
offline wallet's available funds without creating debt; a victim is still
compensated in full. No new automatic ban or team-killer rating is invented.

Gross credit awards, actual compensation/fines and the signed net credit delta
share the garage receipt's atomic write. Failure/retry and restart cannot
multiply either payments or deductions. Penalties and compensation are not
save-multiplied; XP is penalized before account bonuses. The native result
breakdown uses `originalXPPenalty`, `originalCreditsPenalty` and
`originalCreditsContributionIn/Out` through subtraction/addition replay steps.
Its net values and garage balances reconcile even when old garage credits pay
the charge. The public 9.22 result consumer is orientation for these fields;
the exact Windows #1513 breakdown and the report's original close-range
collision remain gameplay acceptance items. This change does not rewrite
historical receipts or balances absent from the submitted archive.
Real UI/plugin-specific failures still require that affected session's evidence.

Tactical authoring contains route sketches, not a duplicate set of base
coordinates. The navigation baker applies the reviewed route overlay once to
the original sketches, using #1513 arena-decoded team starts for orientation
and hard-gate selection. Arena-decoded `objective_bases` remain a separate
capture contract; only the pinned baker's private adapter exposes them under
its historical `bases` field. Production BotRuntime consumes validated baked
routes, never the provisional import-time sketches. A complete 41-map rebake
changed geometry on 15 routes across eight maps and synchronized pre-existing
route metadata on two others. This proves the data path and graph contracts,
not the cause of an observed stationary Bot or exact-client driving behavior.

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

A Bot checkpoint that cannot be encoded now publishes an identity-only
`[bot_id]` row. The complete manifest roster and unique identities remain
mandatory. The server retains that actor's last admitted pose and combat ACK;
other rows advance normally. Frozen launches and ram reports involving that
actor stay in the worker outbox until its checkpoint becomes encodable. The
Bot/Bot ram report carries both frozen contact positions so recovery movement
cannot invalidate an already observed collision. The server checks bounded
finite coordinates, contact proximity, and immutable retry identity. An actor
with an unavailable checkpoint holds new fire triggers until recovery, while
its accepted burst continues; this preserves the burst identity needed to
admit its frozen launches. If recovery spans the completed reload, the server
validates the shot debit and reload completion as two ordinary ammunition
transactions. The worker logs the round, actor and codec reason at a bounded
cadence. Integration tests cover failure, retained ACK/state, frozen launch
and contact recovery, and duplicate delivery.
This contains the `223753` field report's whole-round failure; its discarded
original codec reason cannot be reconstructed from the report. Windows #1513
acceptance of the recovery path remains outstanding.

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

Version 0.3.71 imports a constrained set of mechanisms proven in the retired
predecessor source history without changing #1513 native ownership. The
caller-facing LAN path freezes plain JSON and appends every accepted message
to a bounded reliable FIFO; it neither
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

No native predecessor `WGVehicleFilter`/physics experiment is present here.
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
the older native sensor without restoring the retired predecessor's permissive
pivot workaround.

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

The destructible boundary is pinned to a schema-v8 destructible
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
the retired predecessor is deliberately not transplanted.
Authority Bots retain the existing streamed proximity/native contact sensor;
the new dynamic-only OBB supplement is not wired into Bot movement.

The catalog retains normalized keys for deterministic indexing, but native
`DestructiblesCache.getDescByFilename` receives the resource's case-preserved
descriptor filename. This is an exact-build ABI requirement: the cache lookup
is case-sensitive and rejecting a lowercase synthetic filename occurs before
the unchanged stock mass/speed/health kinetic gate. Version 0.3.68 neither
lowers that gate nor restores the predecessor's broad pivot-proximity
workaround.

The shot path also retains native material identity as its first choice. If a
#1513 native slot is anonymous, only the nearest unique catalog OBB on the
bounded shot segment may supply identity. The first static collision and
nearest vehicle cap the search, while ambiguity fails closed. Traversal resumes
from the exact registered OBB exit plus a small epsilon, not a fixed jump that
could skip a thick structure or its backing geometry.

An item the round has already broken is not part of the collision scene. #1513
`Vehicle._isDestructibleMayBeBroken` reports an item as broken as soon as its
chunk controller does, whatever the delayed hide callback still draws, and a
falling atom keeps its native skin in the world for the whole round. Vehicle
movement and the HE blast rays already hide those skins through the exact
`(chunk, item, material)` native keep-callback. The solid-shell ray now uses
the same law: a proved-broken surface is filtered out and the ray is re-cast,
so a felled pole or a broken wall panel no longer stops later shells while
intact sibling modules and backing walls stay authoritative. A shell that has
just destroyed an admitted item and cannot resolve that item's registered OBB
exit resumes before the nearest remaining native or catalog surface rather
than ending on debris. The filtered re-cast is also capped by the live catalog
intersection, because a clear mask-128 ray does not exclude a dynamic-only
prop. Every scenery stop now carries a `stop_reason`, so a
Windows report can distinguish the legal `above_threshold_hp` and
`shell_family` refusals from an identity failure such as `catalog_miss`,
`catalog_ambiguous` or `native_reject`.

The exact #1513 `destructibles.xml` supplies both numeric shooting-through
contracts: `maxHpForShootingThrough` is `19`, and every listed material has
`projectilePiercingPowerReduction` factor/minimum values `(0, 25)`. Version
0.3.68 therefore lets AP, APCR and APHE continue only through an item whose
scale-adjusted health is at most 19. Each accepted item leaves damage unchanged
and adds a fixed 25 mm penetration loss; multiple items accumulate. Each launch
freezes one shell factor and reuses it, while the range-dependent mean is
evaluated at each tested obstacle distance and again at the vehicle. Scene
queries without an admitted projectile sample lazily only when penetration is
needed. A sampled remainder below 1 mm makes
the shell disappear at that
obstacle. An above-threshold item may be destroyed but stops traversal. Under
the pre-1.13 HE mechanics used by #1513, HE and HEAT stop at the first
destructible, and HE explodes at that point. Stock
`vehicle_items.Shell.isAmmoPercingType` names exactly the same
ARMOR_PIERCING/ARMOR_PIERCING_HE/ARMOR_PIERCING_CR family, so the split is the
client's own set rather than a guess.

The local `DESTR_TYPE_TREE` adapter currently applies the same numeric law.
This is an implementation policy, not a proved retail tree-projectile contract.
The shared XML proves the threshold and material values, and
`DestructiblesCache.scaledDestructibleHealth` proves the scale calculation.
However, `Vehicle._isDestructibleMayBeBroken` consumes vehicle speed and mass:
it is a vehicle-ram path and cannot establish which tree health the original
server uses for shell traversal. Large-tree AP behavior therefore still needs
independent #1513 projectile evidence; the presence of a shared XML table does
not prove that every tree must stop AP.

The official [8.10 release notes](https://worldoftanks.eu/uk/content/docs/release_notes/release-notes-810/)
establish AP/APCR traversal through some small objects with penetration loss.
The [1.13 release notes](https://worldoftanks.com/en/content/docs/release_notes/update-1-13-list-of-changes/)
introduce non-SPG HE traversal through destructible objects. Stopping an old HE
shell does not preclude explosion damage to nearby scenery. The exact #1513
`AreaDestructibles.DestructiblesManager.onProjectileExploded` receives an
already selected destruction list; it does not implement the server's radius,
occlusion or damage selection. The local projectile path currently destroys
directly hit scenery but lacks authoritative HE area destruction of nearby
scenery, so tree-root splash parity is not established.

Trees own no catalog OBB, so neither their item scale nor their exit distance
can come from the baked catalog. Both come from the native item:
`wg_getDestructibleMatrix` shares one native index space with
`wg_getChunkDestrFilenames` and `wg_getDestructibleEffectCategory`, so a slot
already proved resolved and named by the tree identity gate resolves there
too, and `_matrix_item_scale_1513` reads the stock scale convention from it.
The scale is frozen while the tree still stands, before the native fall can
move its matrix, and the shell family is tested first so HE and HEAT never pay
for the query. A tree the round has already felled keeps the broken-skin rule:
it is not collision, costs no penetration and is never felled twice.

Two boundaries remain unproved on this path. A native matrix query that fails
for a resolved, named tree leaves no scale, which stops the shell and records
`health_unavailable` rather than guessing a scale. And soft vegetation below
health `10` -- 177 of the 498 entries, all bushes, shrubs and ferns -- is still
excluded by the existing vegetation gate, so it is never felled and never
tested against the cap; whether such an item can produce a mask-128 shell
contact at all has not been observed on the exact client.

The threshold and material reduction are exact pinned-resource evidence.
Official same-family mechanics descriptions support the shell-family split,
cumulative penetration reduction and unchanged damage. The proprietary retail
0.9.22 server implementation and its exact operation order are not published,
however. The one-factor, per-tested-hit-range, then cumulative-reduction
order above is therefore documented as a high-confidence reconstruction rather than an exact
server-source copy. The resulting fragile/module payload preserves its encoded
shot bit, but the local manager order is unsynchronized: the copied projectile
path does not deliver the retail server's later `damagedDestructibles` payload
required to release a projectile-synchronized native order.

Shell penetration and vehicle-damage rolls use a reconstruction of the public
normal-distribution rule. The former uniform draw inside +/-25% overproduced
both high and low rolls. [WG's May 2013 explanation for 8.6](https://wot-news.com/track/post/ru/HuKoguM/1369231089)
explicitly changes damage and penetration from two to three sigma;
the [North American 8.6 release notes](https://worldoftanks.com/en/news/general-news/86-update-notes/)
confirm the reduction of extreme damage and penetration rolls. That entry is
absent from the European release-notes page. The earlier 2011 recollection
of 2.5 sigma does not establish the law for a client released after 8.6.
[MrConway's June 2017 answer](https://wot-news.com/track/post/eu/MrConway/1497292760)
still explicitly describes nonuniform penetration and damage. Accordingly,
the shell value has mean equal to its descriptor value, standard deviation
equal to one twelfth of that value, and limits of +/-25%. Out-of-interval
samples are redrawn. **Resampling is an explicit reconstruction choice:**
the published three-sigma cutoff does not specify the generator's exact
outlier algorithm. The versioned announcement and release notes support this
reconstruction, but do not expose the proprietary #1513 server. No claim of
proprietary-server RNG equivalence
follows from local distribution tests. This change covers the armour-damage
channel even when a track material selects it; the separate devices-damage
channel and aiming dispersion retain their existing laws.

Aiming dispersion is a separate unresolved version gap. The implemented
radial two-sigma law follows the [8.6 accuracy explanation](https://worldoftanks.com/en/news/general-news/some-changes-coming-86-update/),
including uniform radial redistribution of outliers. It gives about 16.3%
of shots within the innermost tenth of the radius. The [9.6 accuracy update](https://worldoftanks.com/en/news/general-news/9-6-accuracy-changes/)
reduces central hits; the [retained Russian support explanation](https://lesta.ru/support/ru/products/mt/article/35718/)
states the historical change from 16% to 10%. The official 3,000-shot diagrams
do not publish a complete sampling law. The 9.6 reweighting is not implemented;
the current scatter model must not be described as verified #1513 accuracy.
Matching the client-owned cone angle and armour preview cannot establish
matching weak-spot hit probabilities. No guessed ring weights have been added.

One accepted penetration factor remains frozen across range, obstacles,
ordered vehicle layers and a ricochet continuation. AP/APCR continuations
preserve penetration spent on external plates, use the selected ricochet
plate's actual trajectory position/time/distance, and apply their retained
base multiplier to later destructibles. The native query may be in a moving
target's frozen frame; its selected collision fraction is mapped onto the
corresponding projectile chord before creating the reflected segment.

Independent affine and moving-plane tests exercise the real component adapter,
chord, armour resolver and wire-effect parser for ordinary player/Bot AP/APCR
hits. They check component transforms, impact points, material identity,
relative incidence and reuse of the frozen roll. They establish local geometry
and data flow, not the exact Windows native BSP implementation, presentation
timing or parity with a live retail server. The stock penetration preview and
AP/APCR normalization/material laws are unchanged.

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
unnamed. A nonzero unequal name/item count is only a partial alignment.
The schema-v9 catalog also supplies exact WGDE/SpTr tree wires, resource
names, and quantized initial transforms for every supported map. A compacted
category can use those authored identities when its surviving names form an
ordered subsequence; each consumer still verifies the live slot and matrix.
Standard-battle admission now follows bit `0x1` in every SpTr and BSMI
visibility mask, after assigning the original WGDE item indices. Mode-excluded
slots remain explicit in `excluded_instances`, but never enter descriptor
recovery, native query/animation admission, collision boxes, or standing/fallen
concealment. Name alignment skips those absent scene objects without shifting
later native indices. The exact #1513 crash at `0x00ABB0AA` accessed a missing
tree scene object for Prohorovka `(32639, 31)`, whose SpTr mask is `0x7fff4000`.
The matched dump's scene registry omitted exactly the trees excluded by bit 0.
All 41 source maps have been audited against that mask; Windows replay remains
the acceptance boundary for the repaired native animation lifecycle.
An unresolved category is isolated only at its own item indices, preserving
independently aligned types in the same chunk. An unknown or malformed
non-empty descriptor or malformed category payload that prevents typing the
compacted sequences still invalidates their shared alignment contract. A category-query exception is the native resolver's
skipped-item case:
that exact slot is isolated before any matrix, effect or destruction call,
while other resolvable slots may finish alignment. Native category `-1` is a
resolved handlerless item, not that exception case: alignment leaves it
resolved and unnamed and does not isolate it. Filename reconstruction does
not reinterpret `-1` as a category mismatch. A registered native effect
category must match the exact admitted descriptor; `-1` leaves only that
effect channel unverified, so admission still requires the exact live matrix
and wire plus the exact native filename when one is present.

Stock `__launchTreeFallEffect` and `_DestructiblesAnimator.showFallTree`
return immediately when `getDestructibleDesc` yields `None`; they do not wait
for an incremental chunk-name scan. The safe descriptor adapter therefore
resolves a catalog tree synchronously from its exact wire, live native tree
category, and initial matrix. It keeps this identity through native animation
and invalidates both name and descriptor caches on chunk unload. The XML
spelling is retained for the case-sensitive Python descriptor dictionary,
including compiled resource names that differ only by case. The original
scalar filename wrapper remains unused. All-map resource joins and local
lifecycle tests establish coverage and guards, not Windows animation or
contact acceptance; those still require exact-client playtesting.

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

Damage does not imply removal of collision. The compiled BSMO destroyed-model
reference identifies modules with a solid replacement BSP; map catalogs retain
that per-box fact, not the replacement's shape. Such contacts cannot use an
original whole-item OBB exit to skip a native hit, and destroyed-model materials
87–100 remain eligible for native motion, support and shell queries even after
an item-wide destruction receipt. Once replaced, both structures and fragile
props relinquish their intact motion envelopes: keeping a source box creates
an invisible wall around a lower or narrower wreck. Translation and rotation
query the actual native BSP, including vehicle-only surfaces, for players and
Bots. Intact sibling modules and unrelated walls retain their own guards.
Collision-free destroyed modules also avoid the forced hiding-delay hold.
Solid replacement swaps release as soon as the matching physical-contact
callback completes; only unfinished callbacks retain the bounded stock hand-off.
The native segment sweeps are not a volume-overlap proof for every narrow
replacement feature. Exact Windows driving and pivot acceptance remains the
boundary for those shapes; an intact source box is not replacement geometry.

The September 18 v0.9.0 Ruinberg Winter report records soft holds at the catalog's
`bld000_base` and `bld707_shed` instances, whose destroyed modules have no solid
replacement. The tractor next to the shed retains collision, but its intact
box is not the replacement shape. Other native hard contacts in that report
cannot be identified from unordered collision-filter candidates alone. The
existing rate-limited stall report now includes two read-only material probes,
their returned points and distance from the actual hard hit, to distinguish
a remaining map collider from a nearby destructible without changing motion.

The same-day Malinovka report supplies a second counterexample to retaining
source envelopes: `mil203_MilitaryDefences01.model` is a two-module structure,
with both boxes marked as retaining collision. Chunk 32636 items 23–26 receive
accepted destruction for materials 73 and 74, but repeated catalog hard results
continue without a native hard-hit reason. The regression uses the reported
positions and exact baked placements of those barriers. Native-scene tests
exercise both adapters, forward/reverse travel and both turn signs, allowing a
lower replacement while preserving damaged faces, vehicle-only obstacles,
unbroken sibling materials and unrelated walls. These tests establish adapter
behavior, not the exact client's destroyed mesh or gameplay feel.

Report `20260919-000847-92a20042a102` also runs the original v0.9.0 build
`colorfulmeans-35356845247-1`. Stalingrad's reported hard/deflected positions
intersect the retained two-module warehouse at chunk 31614/item 49 and
four-module sheds at items 23 and 6. Those exact poses reproduce the obsolete
envelope block and pass once the modules are destroyed and swapped. A second
regression sweeps all 18 retained fragile prop models in this map in both
directions, including GazMM trucks, SdKfz251, trams and railway vehicles.
These tests establish catalogue release, not permission to drive through a
solid part of a native wreck. The separate native hit near (-301, 0.84, -228)
has only unordered material candidates in this old build; it cannot be
identified as a destroyed object from that evidence. The new point-distance
diagnostics cover it without suppressing unidentified or solid map geometry.

For physical fragile/module crushing, the exact stock manager starts effects
before scheduling its collision replacement after 0.2 seconds. The adapter
completes only the matching current-space bound callback immediately, then
cancels its scheduled copy. Exact callback arguments, module identity and
non-shot/non-Havok guards are required; failure preserves the scheduled owner.
Projectile timing and falling-body animation retain their stock lifecycle.
Pinned-bytecode ABI checks and focused tests cover these contracts; exact
Windows playtesting remains necessary for effect continuity and contact feel.

Physical-contact destruction reports retain their original identity, position,
yaw and speed until transport accepts them. A refused report does not repeat
native destruction or turn an already accepted crush into a motion hold for
that vehicle or unrelated vehicles. Space changes retire the old backlog
before the scanner retries it. Shot destruction receipts remain inside their
projectile transaction and never enter the standalone contact retry ledger.
Focused tests prove publication and motion-result isolation, not native feel.

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

The current hidden worker also supports bounded combat timing beyond its
five-second startup probe sample. `worker_diagnostics.py` captures up to three
30-second windows per round, with a 30-second cooldown. A live battle frame
with active projectiles or pending supplemental shot lanes can arm a window;
slow loading or travel alone cannot exhaust the captures before combat.
Owned Python stages report call
count, inclusive total, self time excluding measured children, and maximum
single-call duration. `bot.probe.*` measures logical probe boundaries;
`native.projectile.armour/world/material/sticker` counts the actual calls at the
instrumented `localHitTest`, `wg_collideSegment`, and material-query seams,
including calls that raise. These are not a census of all BigWorld calls.
The existing `PERF` records remain; `PERF combat_trace` adds JSON detail for
the three slowest intervals and up to three neighbouring frames on either
side of the worst interval. Schema 2 emits each frame separately, identifying
its `relation` (focus/previous/following) and `index` within that relation.
Each line stays below 7168 ASCII bytes: an oversized trace or summary becomes
ordered `combat_trace_part` or `combat_summary_part` records. Concatenate their
`data` strings in `part` order (checking `parts`) before parsing the complete
JSON record. This preserves evidence across the observed #1513 8 KiB native
log-line limit. Neighbours at a reporting or round boundary may
be incomplete. `PERF combat_summary` reports each closed capture's stage
totals, frame/time span, queue maxima, and counters. Detailed clocks are
inactive between captures; the small frame ring remains available.

The deeper Bot probes sample one callback per group of four control callbacks,
rotating the selected slot across groups and including every Bot and catch-up
slice in that callback. Selection follows actual Bot control work, so
render-frame cadence cannot alias away all samples; rotation also spreads
samples across slower decision phases. Existing
coarse stages and lane-ledger accounting remain active throughout each capture.
`detail_sampled` labels individual frames; a capture's `detail.frames`,
`detail.stages` and `detail.counts` describe only that matched sample. Use these
matched totals for the detailed cost breakdown; do not compare a sampled child
total directly to an all-frame parent total or treat sampled counts as a census.
The sampling reduces average observer cost; it cannot remove observer overhead
from an individual sampled frame. An unsampled slow frame retains coarse timing.

The new scopes separate Bot state/critical/parameter preparation, pose copying,
longitudinal and traverse integration, publication, route selection, shared A*
work, local driving, world-collision preparation, ground profiles, and
destructible registration/candidate scans. `native.motion.ray/ground` and
`native.destructible.*` time existing native calls with their original arguments
and exception behavior. Reason counters distinguish expired or geometrically
invalid motion receipts, superseded/failed/retained path requests, A* expansions,
streaming misses, and empty-cell reuse. Same-geometry counters identify repeated
ray or hull-box inputs within a Bot slice; they do not prove that intervening
world mutations made reuse safe.

`slices` records elapsed authority time, control refresh and publication intent
at slice entry; a later contact barrier can still require an extra publication.
The bounded `actors` rows identify Bot IDs and slices, with at most six reported
per frame and 64 Bots in a capture summary. A frame tracks at most 128 Bot/slice
pairs and 2048 geometry keys; overflow counters expose dropped diagnostic detail.
`bot.actor` self time is residual work inside that Bot's block. Shared navigator
batch time is attributed to its initiating caller, not exclusively to that
Bot's own search. Pure-helper observation is bound only during synchronous owned
calls on the current thread and restored on return, exception or reentry; it
installs no native hooks and retains no entities or native vectors.

`tools/benchmark_bot_workload.py --scenario combat` runs the real copied Bot,
world-collision, ground and destructible-scan Python paths with deterministic
native test seams, a finite hard wall, and nearby opposing teams. It includes
spotting, lanes and acknowledged fire admission; projectile terminal processing
has separate human/Bot pipeline parity tests. `--diagnostics` enables the shipped
one-in-four detail sample; `--diagnostic-stride 1` measures full-detail overhead.
Identical snapshot output includes every native collision call's geometry. This
synthetic workload is not a captured-battle replay or a Windows performance test.

The supplemental-lane shadow ledger retains at most 1024 identities and
records actual enqueue-to-completion wait separately from phase-deadline
overdue time and the existing whole-refresh-cycle overdue metric. It counts
admission, deduplication, cache/probe/distance completion, invalid retirement,
materialization, budget deferral, and cover-work pauses. Selected-target waits
and oldest pending identities are separate. With cover work blocking service,
`eligibility_checked=false` means the pending gauge includes identities whose
current eligibility was not checked. Capture wait percentiles cover the last
512 completions; `count` and `kept` expose that sampling boundary. Positive
receipt publications distinguish repeated exports, distinct captured results,
selected targets, replacement, and expiry before export. Publication means
inclusion in worker observations, not acknowledgement or proof of a server
planner decision; `shootable_by_bot_ids` feeds server target/position choices.
The diagnostic cannot label every unselected result wasted, since alternative
lanes also inform movement. Identical-input tests cover queue order, Bot
outputs, player/Bot volleys, progress acknowledgements, and local query or
clock failure without changing simulation outcomes. Timings include diagnostic
overhead; exact #1513 Windows logs are still required to attribute a real
combat stall or measure that overhead in the embedded runtime.

Supplemental lane service rejects due, currently out-of-range pairs before
copying full source/target jobs or spending the bounded service cohort. It
uses the same ordinary/SPG distance rule as the final shot gate and records
the negative receipt at service time. The independent incoming enemy-to-own
probe retains its one-job budget even when the outgoing source cannot reach
the enemy. Selected-first service and round-robin completion remain bounded
by the existing native query budget. A pending cover job reserves a lane
window only when its phase is due and its source/remembered target are still
current; a future or stale cover job no longer pauses the entire queue.

Countdown frames may prepare one ready, non-local worker vehicle's internal
hit layout. All component hit-tester bounds must exist before the ordinary
layout builder may populate its configuration cache; incomplete descriptors
stay eligible and do not poison that cache. The normal hit path retains the
same builder, configuration key, damage rules, and failure handling. Combat
timings separately report armour resolution, sticker validation, equipment
projection, and direct/explosion critical proposals. Cold preparation can be
moved out of a first hit, but this does not establish the cause of a captured
Windows terminal spike.

`tools/benchmark_bot_workload.py` exercises the copied 29-Bot control loop
with real local drivers and a selected shipped navgraph. It can record complete
outputs for cross-checkout parity and optional cProfile data. Native queries
use deterministic test seams, so its CPU results do not measure Windows FPS
or native collision cost. Baked A* reads each expanded cell's link mask once,
preserving neighbour order, costs, hazards and expansion budgets; empty
failed-edge tables no longer require per-edge key construction.
Hidden contact projections reuse the removal of live pose fields only within
one authority slice and template/remembered-pose identity. Each observer keeps
its own contact object and visibility flags; new poses and slices invalidate
that reuse.

The previous 0.3.65 schema-v2 catalog supplied transformed OBBs but joined
runtime slots by native filename taken from the chunk list. A slot may be
present as `''`, while an unresolved, handlerless or NULL-name slot is absent;
therefore only a full-width list preserves native indices. Indexing a shorter
list by the item index silently returned a neighbour's resource. The
exact-instance runtime shape introduced at schema v4 closes that identity gap
with the whole-map matrix signature, and the per-item name is now recovered
from the full-width or reconstructed compacted alignment. The coherent shipped
batches are destructible format v8 and foliage format v4.

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
| `CMD_SYNC_DATA` / `AccountSyncData` | Complete snapshots carry `rev` and omit `prevRev`; every initial `Account._update` subscriber receives an explicit cache value instead of depending on a missing-key fallback. The exact `AccountSyncData.__onSyncResponse` enables events after first sync, and `Account._update` treats any non-`None` `prevRev` as incremental, replaying all supplied `eliteVehicles`. Only actual pushed deltas carry `prevRev`; research captures its post-command stats before deferred publication so queued commands cannot repeat later unlock/elite events. |
| `Stats` / `StatsRequester` / lobby controllers | Zeroed money and account scalars; mapping-shaped restrictions, referral data and clan locks; a non-empty `dailyPlayHours`; and full daily/weekly `playLimits`. Zero periods mean exhausted parental-control time in this build. `mayConsumeWalletResources` starts true because false is the native wallet's `SYNCING` state, and `tutorialsCompleted` carries the completed offline bitmask. |
| `Inventory` / `InventoryRequester` | All item-type indices exist; vehicle `compDescr` and crew maps exist; `repair` is a two-item tuple and `shellsLayout` is a mapping. |
| `QuestProgress` / personal-mission requesters | `quests`, `tokens`, and `potapovQuests`; both `regular` and `training` contain `slots`, `selected`, and `lastIDs`, while `compDescr` is always present. |
| goodies, vehicle rotation, recycle bin, ranked, badges, New Year | Readable empty caches exist. `groupLocks` contains both directly indexed lists, the ranked helper can directly index an empty `ranked` cache, and `ClientNewYear` plus the New Year controller accept the empty sync/goodie mappings without fabricated event data. |
| `Shop` / `ShopRequester` / `RefSystem` | Mandatory `sellPriceFactor`; all directly read item/goodie collections; currency-mapped `paidRemovalCost`; exact berth, slot, and free-XP tuple arities; and the four-key disabled referral configuration, including integer `posByXPinTeam = 0`. |
| `DossierCache` / `DossierRequester` | The stream body is the exact `(revision, dossierChanges)` pair; an empty change list completes synchronization without fabricating dossier data. |
| `ClientChat` and BW Chat2 | The legacy `ClientChat.chatCommandFromClient` mailbox remains a one-way no-op so `CHAT_COMMANDS` indices are never misreported as `CHAT_ACTIONS`. The battle Avatar's BW Chat2 mailbox translates stock team text and all 18 fixed battle messages to reliable LAN messages, then returns validated same-team broadcasts through the stock Chat2 receive path. |
| initial server settings / lobby controllers / `ClientRanked` | `file_server`, regional settings, the four-item roaming tuple and the directly indexed two-item `wallet` retain their native shapes; roaming item 3 is the host list consumed by `predefined_hosts`, while `ranked_config` is present and explicitly disabled because `ClientRanked` indexes it directly. `elenSettings` and the server-owned tutorial are explicitly disabled because their exact missing-section defaults start unsupported event-board or tutorial GUI lifecycles. |

The BW Chat2 adapter covers the complete fixed-message range in the reviewed
archive: `HELPME=23`, `FOLLOWME=24`, `ATTACK=25`, `BACKTOBASE=26`,
`POSITIVE=27`, `NEGATIVE=28`, `ATTENTIONTOCELL=29`, `SPG_AIM_AREA=30`,
`ATTACKENEMY=31`, `TURNBACK=32`, `HELPMEEX=33`,
`SUPPORTMEWITHFIRE=34`, `RELOADINGGUN=35`, `STOP=36`,
`RELOADING_CASSETE=37`, `RELOADING_READY=38`,
`RELOADING_READY_CASSETE=39`, and `RELOADING_UNAVAILABLE=40`. These are
stock messages; the adapter adds neither arbitrary command names nor speech
recognition. CPython 2.7 marshal and disassembly confirm the outgoing chain
from `RadialMenu.onAction` through `ChatCommandsController` and
`BattleChatCommandHandler.send` to
`Avatar.base.messenger_onActionByClient_chat2(actionID, requestID, args)`, and
the inverse server path through
`Avatar.messenger_onActionByServer_chat2(actionID, requestID, args)`. The
argument mapping has exactly `int32Arg1`, `int64Arg1`, `floatArg1`, `strArg1`,
and `strArg2`; `int32Arg1` carries the target vehicle entity id or minimap cell,
while `int64Arg1` carries the sender account DBID on reception.

The SPG area producer packs exactly `<fffif` into the 20-byte `strArg1`:
desired world `x/y/z`, integer cell index, and reload time. Its consumer
unpacks the same layout for the stun-area marker, minimap cell text and optional
reloading text. The SPG target form of `ATTACKENEMY` carries the target entity
id in `int32Arg1` and reload time in `floatArg1`. Reload messages use
`floatArg1` for positive remaining seconds and `int32Arg1` for positive
cassette quantity: `RELOADING_CASSETE` has both,
`RELOADING_READY_CASSETE` has quantity only, and the ordinary ready/unavailable
forms have default arguments. These status messages are same-team information
broadcasts and do not create Bot orders.

Targeted commands retain their stock relationship: `FOLLOWME`, `TURNBACK`,
`HELPMEEX`, and `STOP` address a living ally, while `ATTACKENEMY` and
`SUPPORTMEWITHFIRE` address a living enemy. `FOLLOWME` asks that ally to follow
the sender. The four ally-targeted actions retain the stock private filter, so
only sender and receiver render them after the same-team relay; enemy-targeted
actions retain their public markers. Every reviewed command has a five-second
stock cooldown except `ATTENTIONTOCELL`, whose cooldown is 0.5 seconds. The
adapter resolves the stock BigWorld entity id to a separate LAN identity kind
and logical id before transmission, then performs the reverse mapping for
display; account DBIDs are resolved independently and are never assumed to
equal entity ids. A same-team server broadcast owns the original stock command
display, marker, and sound. Its acknowledgement closes the stock request and
displays each assigned Bot's stock `POSITIVE` reply, up to three distinct
responders; an empty assignment produces no Bot reply. Expiry, issuer death or
departure, round completion, and replacement at the bounded command-history
limit are cancellation terminals, not claims that a Bot completed the order.

The stock BW Chat2 provider enables and flushes its action queue only in battle
scope, and disables and clears it on scope exit. The stock chat-command
controller registers its event consumers on `startControl` and removes them on
`stopControl`. The LAN adapter adds current-Avatar and current-round fences,
clears pending request identities during Avatar teardown, and ignores duplicate
or late acknowledgements and relays.

In the inspected Chinese HD `0.9.22.0.1 #1513` bytecode,
`TeamChannelController.isEnabled()` additionally requires
`sessionProvider.getArenaDP().getAlliesVehiclesNumber() > 1`; its base guard
keeps `g_settings.userPrefs.disableBattleChat` effective for random battles.
The runtime therefore waits for the real `ArenaDP` reader after
`VEHICLE_ADDED` before issuing action 19, and retries on subsequent frames
until that native roster condition is visible. It does not write the user chat
preference.

Team text uses stock actions `INIT_BATTLE_CHAT=19`,
`DEINIT_BATTLE_CHAT=20`, `BROADCAST_BATTLE_MESSAGE=21`, and
`ON_BATTLE_MESSAGE_BROADCAST=22`. Outgoing TEAM messages carry channel marker
`int32Arg1=0` and text in `strArg1`; `int32Arg1=1` is COMMON and is rejected
locally because the LAN service is same-team only. The Chinese client applies
a three-second text cooldown. Its outgoing filter strips, performs its own
runtime's NFKC normalization, slices to 140 characters, UTF-8 encodes, and
collapses byte-string whitespace. On the supported 32-bit Windows Python 2.7
runtime that slice counts UTF-16 code units. LAN validation checks valid
non-blank Unicode and the 140-unit bound without applying NFKC a second time:
Python 2.7's Unicode database and a modern server runtime can normalize the
same character differently. Incoming `ArenaMessageVO` preserves Unicode, and
the locked stock incoming filter performs the single HTML escape for `&`, `<`,
`>`, double quote and apostrophe. The server relays raw plaintext; the adapter
passes Unicode rather than invoking the consumer's lossy byte decode fallback.

Action 19 carries a level-1 zlib-compressed protocol-2 pickle of an empty
history list. It creates both TEAM and COMMON channel controllers. A second
stock gate is also required: `_EntityChatHandler` starts disabled and queues
every action 22 message until `onUsersListReceived` contains `IGNORED` or
`IGNORED_TMP`. Retail produces that event from XMPP cache restore, successful
contacts sequence initialization, or its battle retry fallback. The offline
account has no dependable XMPP producer, so the adapter publishes only those
two roster-ready tags after action 19. It does not clear or replace users,
ignored state, or mute state. Teardown sends action 20 before stock GUI exit;
partial initialization retains teardown ownership and both start/close paths
are idempotent.

The stock provider calls the base mailbox synchronously and only appends the
request id to its response deque after that call returns. A local rejection or
LAN admission failure is therefore returned through a zero-delay BigWorld
callback. The callback validates the current Avatar and adapter generation;
chat teardown rotates the generation so a late response cannot reach the
retiring UI or a replacement battle. A later LAN acknowledgement can respond
directly because the stock request has already been registered.

For `ATTENTIONTOCELL`, the exact archive computes
`cellIndex = column * 10 + row`. With arena bounds
`(bottomLeft(x, z), upperRight(x, z))`, its stock marker point is
`x = bottomLeft.x + column * width / 10` and
`z = upperRight.z - row * height / 10`; this is the cell's northwest boundary,
with a placeholder `y = 0`. Bot navigation instead targets the center of the
same proven cell bounds. The worker selects a passable point within that cell,
preferring dry ground, and obtains its height from the baked navigation data. The stock minimap feedback path creates the cell flash, runs its
animation, and plays the `minimap_attention` sound.

The complete Chinese HD `0.9.22.0.1 #1513` client root was inspected with
`tools/inspect_client.py`. It passed the regional build, 32-bit x86 executable,
mod-path, CPython 2.7 bytecode, entity-definition, representative asset, and
reviewed native-method checks. The bytecode results above were then reproduced
from that root's packaged `res/packages/scripts.pkg`, so the Chat2 contracts
belong to the same client installation. Static inspection still does not prove
native UI rendering, audio, lifecycle behavior, or frame pacing on Windows.

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

### Atmosphere ownership across space replacement

The visible-client reports `20260906-024450-e765af0dd37c` and
`20260906-154953-e243bc3cc617` contain the same native failure after lobby
Account restoration, before the hangar geometry finishes loading. The latter
full termination dump retains the original exception context and heap:
`AtmosphereSupport[+0x24][+0x10]` still points to `0x453BE640`, while the
environment being ticked owns settings at `0x44BCDA40`. The stale block is
interpreted as a texture-name string with size `0x1F726428`, producing a
527,246,376-byte `memcpy` and an access violation. This is a stale-owner
failure, not evidence of an ordinary allocation failure.

In the exact EXE (image base `0x00400000`), `0x00A35DC2` binds the atmosphere
settings during environment load. The destructor frees its settings at
`0x00A34B65`. A new environment has dirty settings before load completes;
its tick at `0x00A363F8` tests `[ESI+0x510]+0xF4`, but the atmosphere update
at `0x00A854F0` consumes the renderer's separate borrowed pointer. Deferring
Account restoration by a callback does not establish this pointer's validity.
Stock `OfflineMapCreator.destroy()` already resets the camera before clearing
spaces; our retained-space cleanup also calls space APIs, so the absence of
Python frames in this crash does not exclude the offline transition as a
trigger.

The existing exact-build bridge replaces only the call at `0x00A36419` with
a process-lived x86 thunk. Before tail-jumping to the stock update, it copies
the current tick's `[ESI+0x510]` into `[ECX+0x10]`. It never dereferences the
old pointer, does not fabricate settings, and leaves the stock material update
and subsequent dirty-flag clear in control. The caller prologue, complete
dirty-test/call/clear sequence, and callee prologue are byte-validated in
addition to the existing PE identity gate. The thunk preserves the thiscall
stack, ECX, flags and nonvolatile registers; EAX is scratch at this seam.
Repeated installation verifies the existing patch. Protection/cache failures
roll back the call and report a distinct status. Installation precedes offline
callbacks on the native tick's main thread. The patch and its extension remain
alive through lobby transitions until process exit; the EXE on disk is unchanged.

`tests/native_atmosphere_owner_guard.c` executes the production installer and
thunk in a 32-bit Windows process: an inaccessible old owner faults without
the patch and is untouched with it, including replacement and already-current
owners. It also checks calling convention, dirty clearing, signature refusal,
repeat installation and injected protection/cache failures. The native harness
and package checks do not substitute for repeated battle-to-hangar acceptance
on the exact Windows game client.

### Direct native collision geometry

`offline_math_batch_native.pyd` implements complete translation, sliding and
rotation sweeps. Its three synchronous methods read the caller's existing
dict/list/tuple objects under the GIL and return ordinary floats or tuples.
The C++ geometry core owns only copied scalar values; BigWorld queries,
simulation ordering and authoritative state remain with their existing owners.
There is no serialized body packet, shared mutable body cache or background
callback. Clear and zero-motion operations retain the Python fast return.

The `contact_roster` method handles the entire frozen roster in one synchronous
call: four ordered normal/contact passes, post-contact velocities, traverse
torque, spatial candidates and first-impact ram geometry. Physics retains actor
ID order; ram candidates retain the caller's owner and spatial-bucket order.
The main thread still performs armor probes, admits damage episodes only after
nonzero damage, and applies motion through the existing world gates. Human ram
receipts keep their separate authoritative settlement path. A contact batch
does not introduce a delayed physics step or invoke an engine callback.

The synchronous `world_run` method moves the complete horizontal world law
into C++: posed/perimeter lanes, support profiles, terrain recognition and
ordered contact resolution. Its Python dispatcher retains live descriptors,
Math vectors, collision filters, original engine hits, recasts and destruction
effects on the invoking main thread. Independent ray frontiers contain at most
seven queries; destructive resolution remains ordered and cannot be replayed.
Unsupported input may use the Python law only before the first dispatcher call.
The bridge preserves callback exceptions and rejects invalid responses without
repeating engine effects. The public motion check contains such failures as a
hard result for that operation, records a failure counter/diagnostic and leaves
the backend available for subsequent actors. Already committed destruction is
retained; the failure does not replay the law or terminate the battle.

The embedded interpreter does not export its C API. The bridge binds the
reviewed #1513 `Py_InitModule4`, `PyDict_GetItem`, `PyString_FromString`,
`PyFloat_FromDouble`, `PyTuple_New`, `PyInt_FromLong` and `PyObject_Call` entry points after one load-time check
of the executable and required object layouts. Exact built-in containers and
numeric objects can be read without invoking conversion or equality callbacks.
Borrowed object storage is consumed before allocating results; a new private
tuple takes ownership of its two new float references. Unsupported existing
representations use the Python operation locally.

The exact executable's `PyObject_Call` at RVA `0x00bca730` accepts three cdecl
arguments (callable, tuple, nullable kwargs). Reviewed Python and engine callers
retain/decrement the argument tuple themselves and consume the returned owned
reference. The implementation forwards to `tp_call` under the existing thread's
recursion guard and preserves a NULL result's Python exception. The bridge
checks its entry signature, holds arguments across reentry and uses this API
only from the synchronous world dispatcher, never from a background job.

Host conformance covers physical outcomes, unchanged inputs, live-pose reuse,
ordered armor/damage settlement, world query/effect order, exception non-replay
and reference ownership. Ordinary floating-point rounding differences
are permitted; bit identity across CRT implementations is not required. These
checks and complete-caller timing do not establish Windows frame pacing or
embedded-process lifetime safety; those require the installed #1513 test build.

### Background navigation and spotting

The same x86 extension runs complete baked A* searches, path smoothing and
spotting geometry/foliage computation on two shared C++ threads. Each map is
copied into owned native storage once. Job submission copies plain numeric
inputs; background tasks never retain a Python object, acquire the GIL or call
BigWorld. Navigation and spotting contexts belong to the current map/round;
replacement, cancellation and teardown invalidate pending results and queries.
Closing a context never waits for worker completion. The process-wide executor
and service owners deliberately have no DLL-unload destructors, avoiding a
thread join under the Windows loader lock. The existing launcher architecture
and the game's 32-bit process boundary are unchanged.

Baked navigation first searches a candidate route, then requests a batch of
live native corridor proofs for its reviewed edges and prospective shortcuts.
A denied edge is retained as a local proof and the candidate is searched
again. No unproved route reaches the driver. This uses extra inexpensive
background searches when obstacles reject a candidate, while avoiding one
render-frame round trip per A* expansion. The main thread services queries
fairly within its existing search budget and rejects newly obstructed wreck
paths before publication. Non-baked engine-free callers keep their existing
resumable Python search seam.

Spotting projects the exact descriptor-local checkpoints and evaluates all
foliage volumes for an admitted observer/target pair in the background. Static
foliage is shared; fallen-tree changes produce coherent revision snapshots.
The main thread retains the existing ordered sight queries, broken-surface
filter and contact reports, including the first clear zero-cover early exit.
A small native reduction completes camouflage and detection after those rays.
Preparation and sight-query consumption have separate bounded cohorts, so
submitting a CPU job does not also consume a later engine-query slot. Pending
jobs retain their fair queue position; a worker that has not finished never
makes the frame wait or spends an unused native-query credit.
Results retain their sample time for cache and spotting-memory leases. Actor,
descriptor, firing/camouflage state or foliage changes reject obsolete jobs.
The maximum observation age is the existing 0.75-second shot-camouflage window;
using the shorter 6 Hz cache interval here would starve observations whenever
one loaded worker callback exceeds that interval. This is explicitly delayed
spotting, not an assertion of synchronous observation timing.

`tools/check_native_workers.py` exercises the actual Python 2 bridge, background
progress while Python holds the GIL, immutable inputs, batched query proofs,
late cancellation/answers, map replacement and ordered spotting reduction.
Pure-data regressions also cover the Python owners' pending/result lifecycle.
These prove the computation and ownership contracts; exact Windows callback
latency, frame pacing and gameplay acceptance remain separate evidence.

## First-chance exception trail

#1513 installs its own `__try/__except` around the whole main loop at
`0x00602F00`. Its filter formats the crash text on the faulting stack
(`"The BigWorld Client has encountered an unhandled exception ..."`), routes
it through the registered debug-message handlers at `0x00687E80`, then calls
`_set_abort_behavior` and `abort` at `0x00685D6A`/`0x00685D73`, which is where
this port's `exit code 3` comes from. Because the engine handles the exception
itself, nothing reaches second chance: ProcDump's `-e` trigger cannot fire, so
every collected dump is a `-t` termination dump. Whether it is usable is then
luck: the 2026-09-08 16:00 worker report kept the main thread and its crash
text, while the 16:16 report kept only an audio worker thread and no evidence
at all.

The sidecar therefore records the fault itself. `install_exception_trail`
snapshots the module table with `CreateToolhelp32Snapshot` while it is still a
normal Python call — resolving names inside the handler would take the loader
lock the faulting thread may already hold — then installs a vectored handler
as first in the chain. Vectored handlers run before any frame-based handler,
so it observes the exception with the faulting thread's own registers and
frames intact.

The handler only records. It always returns `EXCEPTION_CONTINUE_SEARCH`, never
writes to the context, and restores the thread's last-error value, so
first-chance exceptions used as control flow behave exactly as before. It
records the fatal status codes unconditionally, and a C++ throw
(`0xE06D7363`) only when `ExceptionInformation[0]` is the MSVC magic
`0x19930520` and its ThrowInfo lies inside `WorldOfTanks.exe`, `msvcp140.dll`
or `vcruntime140.dll`: `SogouPY.ime`, `nvgpucomp32.dll` and `wgc_api.dll` all
throw and catch their own C++ exceptions while the game runs normally. Each
record carries the code, faulting address, thread, registers, an EBP frame
walk resolved to module and RVA, and — only when every byte up to its
terminator is printable — the `std::exception` message. A shared buffer and a
try-lock keep the handler off a stack that a stack overflow has already
exhausted.

The trail retains at most 512 record slots per process. A repeated code and
address refreshes the last slot, including its latest context and message,
with a `repeats=` count: different C++ throws can share RaiseException's
address. Once all slots are used, later faults replace the final slot rather
than being discarded. The launcher keeps the tail of an oversized trail.
Arbitrary object and frame reads use `ReadProcessMemory` so a stale address
can fail the read without raising another access violation in this handler.
The session header is written before the handler is published, avoiding a
race over its shared output buffer. This remains best-effort evidence:
concurrent faults can lose the try-lock, writes can fail, and fast-fail paths
may bypass vectored handlers entirely.

The trail is diagnostics, so `instance_guard` never fails startup over it: an
older sidecar without the method, an unconfigured path, and a refusing handler
all leave the atmosphere guard and the game untouched.

`tests/native_exception_trail.c` runs the production handler in a 32-bit
Windows process built by `tools/build_native_exception_trail_probe.sh`, and CI
executes it on `windows-latest`. It asserts the module filter, the ThrowInfo
gate, rethrow and foreign-magic rejection, the access-violation fields, the
record limit, and that the handler leaves both the disposition and the
last-error value unchanged. None of that proves what the recorder writes
during a real #1513 crash; only a Windows session that produces a report can.

## Battle-result presentation

The exact #1513 `gui/battle_results/context.pyc` constructor takes
`(arenaUniqueID, showImmediately, showIfPosted, resetCache)`.
`BattleResultsService.requestResults` opens the window before yielding the
result fetch, so retrying a failed fetch must not repeat `showImmediately`.
The LAN session grants that flag once, for a receipt belonging to its live
round and transport, after the waiting barrier naturally returns it to the
garage. Login recovery, early departure, reconnect and an explicit Battle
click cannot inherit that permission. Durable receipt facts such as
`premature_leave` never grant popup permission by themselves. Recovery still
caches the result and publishes its clickable notification without reapplying
the settlement. Pure-data lifecycle tests cover this request contract;
Windows acceptance remains necessary for the actual window transition.

## Post-battle achievements

Wargaming's battle server, not the client, decides which medals a battle
awards, so `res/packages/scripts.pkg` ships the thresholds without the
predicates. `scripts/common/arena_achievements.py` of `#1513` holds
`ACHIEVEMENT_CONDITIONS`, and `getAchievementCondition` only consults
`ACHIEVEMENT_CONDITIONS_EXT` when `ARENA_BONUS_TYPE_CAPS.checkAny` accepts the
arena bonus type. That build answers `False` for every bonus type, so the
regular battles this product packs (`bonusType` 1) use the base table. Those
numbers are copied verbatim into
`src/res/scripts/client/gui/mods/offline_lan_0922/battle_achievements.py`; the
predicates around them combine one exact constant with the documented shape of
the medal. The full table holds 62 entries across every mode this build ever
shipped, so `scripts/item_defs/achievements.xml` selects the subset that
matters: each achievement there carries a `mode`, and only `mode="random"`
belongs to the battles this product packs.

A threshold is not an award rule. The rest of each rule is in the client's own
description text, `res/text/LC_MESSAGES/achievements.mo`, where every medal
carries a `<name>_descr` summary and a `<name>_condition` clause list. Those
clauses decide the class fences, the friendly-fire exclusions, the win and
survival requirements and the tier floors that no constant carries: Cold-Blooded
ships only a distance and a kill count while its description also demands
light-tank victims and a Tier IV gun, and Rock Solid's constant bounds the
rammer's speed while its description also requires the victim to have been
faster. Each predicate quotes the clause it enforces, so the code and the
client cannot drift apart.

The same text settles two questions a threshold cannot. Cool-Headed has no
survival clause, so a vehicle that dies still keeps a ten-bounce run. The
Billotte family says "击毁数将在受到所有伤害后计算" — frags are counted after
all damage is received — which makes the final totals the rule and rules out
any per-event ordering requirement.

`UNAWARDED_ACHIEVEMENTS` in the same module records every medal this product
deliberately does not award and why, so no later change closes a gap by
inventing a coefficient. Every one of those reasons comes from the pinned
client rather than from documentation:

- `gui/shared/gui_items/dossier/factories.pyc` registers `sniper` and
  `medalWittmann` as `DeprecatedAchievement`, whose `checkIsValid` returns
  `validators.alreadyAchieved`. The client itself treats both as historical
  and will not accept a newly earned one.
- `alaric` and `lumberjack` have conditions and record IDs but no entry at all
  in `item_defs/achievements.xml`, and no name, description or condition in
  `achievements.mo` either. They were cancelled before release and no account
  has ever held one.

The deprecation is stated in the text as well: Sniper reads "从0.8.11版本后将
无法获得" and Bölter's Medal "从0.8.0版本后将无法获得".

The remaining three are product decisions, not missing data: the two platoon
medals have no platoon to award to, and Mark of Mastery needs the per-vehicle
experience distributions retail computes.

The LAN server owns the decision. `_finish_battle` freezes the complete public
roster first, awards from it, and stores the resulting names on every roster
row, humans and Bots alike. The three consumers of that wire field — the
server's persisted-receipt validator, the client receiver and the durable
post-battle store — accept only names from that table, and a receipt written
before achievements shipped still loads with an empty list.

| Producer | Exact consumer contract covered |
| --- | --- |
| `VEH_FULL_RESULTS.achievements` / `VEH_PUBLIC_RESULTS.achievements` | Record database IDs from `dossiers2.custom.records.RECORD_DB_IDS`. `gui/battle_results/reusable/shared.py` maps each one through `DB_ID_TO_RECORD`, so a name the pinned client does not register is dropped before packing rather than raising inside the results window. |
| `VEH_FULL_RESULTS.dossierPopUps` | `makeAchievementFromPersonal` reads `(recordDBID, value)` pairs for the personal results medals. `AchievementBlock.setRecord` renders `value` as the badge counter for every non-series achievement, so the value carried is the account's running total after the battle. |
| `VEH_FULL_RESULTS` / `VEH_PUBLIC_RESULTS` `directHitsReceived`, `potentialDamageReceived` | The results screen shows both columns, and Steel Wall reads them. The server accumulates the hit count and each hit's `potential_damage` where it already resolves the shot. |
| `VehicleInteractionDetails.crits` | `gui/shared/crits_mask_parser.py` reads bits 0-7 as critically damaged devices, 12-23 as destroyed devices and 24-31 as destroyed crew, indexed by `VEHICLE_DEVICE_TYPE_NAMES` and `VEHICLE_TANKMAN_TYPE_NAMES`. Both server track devices map onto the single `track` slot and each numbered crew role onto its base type. The field is a mask, so distinct modules accumulate with `or` and a repeated crit stays one bit. |
| vehicle and account `achievements` dossier blocks | `getVehicleDossierDescr` and `getAccountDossierDescr` carry one counter per medal plus the aggregate `battleHeroes`. An unregistered name raises `KeyError` inside the native block, so the writer skips it. `StatsRequester.accountDossier` reads the `stats` cache key `dossier`, which the account snapshot and the post-battle diff now publish. |

Two statistics the server never recorded are now canonical round state, because
`#1513` displays both and the conditions read them: each vehicle's own
accumulated `capturePoints`, and the `droppedCapturePoints` credited to the
enemy whose damage reset a capture. Without them Invader and Defender could
never be awarded and both results columns stayed at zero.

`spotted` is a detection count, not a sighting count. A vehicle is detected
when it becomes visible to a team that could not see it a moment earlier, and
every direct observer on that team at that instant detected it. The statistic
counts distinct enemies per observer, so re-acquiring a target that observer
already revealed adds nothing, while an enemy that goes dark and reappears
credits whoever finds it that time. This replaces a count of every enemy the
vehicle had ever directly seen, which had no team fence and could never drop.
Patrol Duty (`scout`) reads the same number, and earned experience moves with
it. `scripts/common/battle_results_shared.py` fixes the per-enemy half of that
rule exactly: `VEH_INTERACTION_DETAILS` declares `('spotted', 'B', 1, 0)`, so a
detail row cannot carry a second detection of the same vehicle, and the
in-battle ribbon agrees because `_MultiVehicleRibbon.getCount` is
`len(self._hits)` keyed by vehicle id.

The same decision owns the in-battle ribbon. The visible client used to raise
it from a local presentation edge — the enemy's model appearing while this
client's own line of sight was clear — but that edge is the 565 m entity AOI,
which an enemy a teammate revealed across the map crosses long after the team
detected it. The ribbon therefore appeared for detections the results column
never counted. A client cannot close that gap alone: it knows its own line of
sight and the team's merged spot lease, never whether its own sighting is what
revealed the enemy. `_commit_detections` now publishes a `detection` event to
the human observer it credited, and the client draws the stock `SPOTTED` and
`TARGET_VISIBILITY` pair from that event alone. `TARGET_VISIBILITY` reaches
only `TriggersManager.PLAYER_DETECT_ENEMY` in #1513 — `feedback_adaptor` does
not forward it to `onPlayerFeedbackReceived` — so no damage-log or ribbon
consumer loses anything by moving with it.

Ammunition belongs to whoever owns the gun, so Fadin's medal takes the shell
total from the producer rather than inferring it. The visible client puts
`shells_before_shot` on its owned projectile launch, read before the local gun
debits the round; the worker puts the same field on a Bot launch, computed from the
inventory it has just debited. The server freezes the flag on the projectile
and awards the medal only when that shot also left no living enemy.

The launch publisher and `launch_projectile` allowlist carry that optional
field for both owners. `_local_launch_record` preserves it when projecting a
Bot round to the publisher. Unknown launch fields remain rejected.

Battle-hero medals go to one actor per battle, ordered by the medal's own
metric and broken by earned experience, which is Wargaming's documented
tie-break. Bots are ordinary participants and can take one.

This is static and pure-data coverage. It proves which fields reach the native
packers with which values; only acceptance on the exact Windows client can show
the results window rendering those ribbons, counters and tooltips.

### Critical-hit ribbons and shot-result voices

The pinned `Avatar.PlayerAvatar.showShotResults` selects voices independently
of `onBattleEvents` ribbons. Its `IS_ANY_PIERCING_MASK` includes
`DEVICE_PIERCED_BY_PROJECTILE` and `DEVICE_PIERCED_BY_EXPLOSION`, but not
`DEVICE_DAMAGED_*`. Confirmed device/crew damage supplies the piercing bit;
otherwise a module hit followed by a ricochet selects the ricochet voice.
External explosions set the positive-damage-factor material bit only when
they damage vehicle HP, so a module-only explosion selects the no-HP-damage
voice instead. The exact extracted method was executed under CPython 2.7
with old/new flags to verify both selections, empty splash and killing shots.

`BATTLE_EVENT_TYPE.packCrits` packs a critical count. The adapter counts device
damage transitions and crew knockouts, excluding repair, fire-state and
ammo-rack-death effects. Both outgoing and received critical ribbons use this
count. Stock `ribbons_aggregator` excludes `CRITS` when the same target has a
destruction ribbon; the adapter leaves that filtering and voice priority to
the client. These checks prove RPC input and Python voice selection, not
audible Chinese voice playback or the original server's hidden module-HP
notification thresholds.

### Mastery badges and Marks of Excellence

Both awards rank one player against every other player who drove the same
vehicle, so #1513 receives only the outcome. The client carries every field
needed to render them. `battle_results_shared.VEH_FULL_RESULTS_UPDATE` holds
`prevMarkOfMastery`, `markOfMastery`, `marksOnGun`, `movingAvgDamage`,
`damageRating` and `battleNum`, all typed `int` with aggregation `skip`.
`dossiers2/custom/records.py` registers the four durable records in the vehicle
`achievements` block: `markOfMastery` (`B`, max 4), `marksOnGun` (`B`, max 3),
`damageRating` (`H`, max 10000, hundredths of a percent) and `movingAvgDamage`
(`H`, max 60001). `arena_bonus_type_caps.REGULAR` — this product's `bonusType`
1 — already grants `DOSSIER_MARK_OF_MASTERY` and `DOSSIER_MARKS_ON_GUN`.

The two badges reach the results window by different paths.
`gui/battle_results/reusable/personal.py` calls
`shared.makeMarkOfMasteryFromPersonal(results)`, which needs only
`markOfMastery`, `prevMarkOfMastery` and `typeCompDescr`;
`MarkOfMasteryAchievement._getIconName` then picks the `markOfMastery%drecord`
icon when the previous best is lower. A new gun mark instead rides
`dossierPopUps`, because `dossiers2/ui/layouts.py` puts only
`MARK_OF_MASTERY_RECORD` in `IGNORED_BY_BATTLE_RESULTS` and leaves
`marksOnGun` (record 295) on that path, where `makeAchievementFromPersonal`
also reads `damageRating` for the badge tooltip.
`DictPackers.DictPacker.pack` coerces each value with its transport type, so
`damageRating` crosses the wire as whole percent while the dossier keeps
hundredths — the same split retail produces, since its dossier updater applies
`int(results['damageRating'] * 100)` to the unpacked float.

The marks are also drawn in the world, and that path is separate from both
results and the garage. `vehicle_systems/CompoundAppearance.__createStickers`
reads `self.__vehicle.publicInfo['marksOnGun']` and passes it to
`VehicleStickers(typeDescriptor, insigniaRank, outfit)`; the hangar's
`ClientHangarSpace._VehicleAppearance.__setupEmblems` builds the same object
from `itemsCache.items.getVehicleDossier(...).getRandomStats().getAchievement(
MARK_ON_GUN_RECORD).getValue()`, and the carousel card reads the same record
through `getTotalStats`. This port supplies that field from its account store:
`PostBattleStore.marks_on_gun`
returns the row `account_rpc.data.dossiers` already publishes as the garage
badge, the LAN client carries it in `hello` and `select_vehicle`, and
`_public_player` republishes it on every roster row — including the lean rows
that omit the much larger outfit and effective-parameter blocks — so a remote
human's replica decals the same count. Bots have no account and publish zero.
The selection is republished immediately before every start request, so a mark
earned in the previous round reaches the next round's vehicle properties.
These checks establish the value supplied to the stock sticker constructor;
the exact Windows client must still verify that the gun decal actually draws.

The rules are the client's own text in `res/text/LC_MESSAGES/achievements.mo`.
`markOfMasteryContent` gives the mastery classes as more battle XP than 50, 80,
95 and 99 percent of the players who drove that vehicle in the previous seven
days. `marksOnGun0_descr` through `marksOnGun2_descr` and
`marksOnGun_condition` give the marks as an average above 65, 85 and 95 percent
over the previous fourteen days, computed from the last 100 battles, updated
every battle, never lost once earned, Tiers V-X, standard battles only.
Wargaming's support material supplies the one part the client text omits: the
average counts damage dealt plus the *largest* of the track, spotting and stun
assist values, not their sum.

"The last 100 battles" is not a mean of a hundred stored results. Retail keeps
one number and advances it as an exponential moving average with the standard
`2 / (N + 1)` smoothing for `N = 100`: the Marks of Excellence mods players use
to predict their next mark compute `k * (damage + largest assist) +
(1 - k) * movingAvgDamage` with `k = 2 / 101`, against the same
`movingAvgDamage` record the dossier carries. A vehicle with no history starts
at zero, so one battle moves the average by about two percent of the gap and a
first battle cannot reach a mark however good it was - which is exactly what
happened when this port briefly averaged only the battles it had: one strong
opening game on a Type 59 awarded three marks. Sustained combined damage of
3000 on that vehicle now reaches its first mark after 33 battles, its second
after 62 and its third after 109. Keeping the average instead of a window also
means the save file holds one integer per vehicle rather than a hundred.

What the client cannot supply is the population distribution, which Wargaming
recomputes daily per region and never shipped. No 0.9.22-era table survives:
XVM's dated expected-value archive now begins in 2024, the Internet Archive
holds none of the 2017-2018 files, and no per-vehicle mastery or mark table
from that period is archived on the community sites that published them.
`tools/bake_mastery_thresholds_0922.py` therefore captures the current retail
tables into `mastery_catalog.py`, joined to the pinned client by integer
compact descriptor: mastery base XP from `protanki.eu/en/stats/masters` and
combined damage per percentile from the `poliroid.me/gunmarks` service behind
the Marks of Excellence mods. The module records each source's version stamp
and the fetch date. Of the client's 544 playable vehicles, 537 have a mastery
row of their own and 386 of the 393 at Tier V or above have a marks row; the
seven Chinese-server exclusives and since-removed vehicles fall back to the
median of the retail rows for the same tier and class, which the baker computes
and emits, and the baker also bakes the client's own tier and class map so
neither the Tier V gate nor that fallback depends on loaded item definitions.

Because the reference population is retail players rather than this
installation's Bots, the bar is the real one, which makes this product's own
reward policy part of the same question: a retail threshold only means what it
means in retail if the currency behind it behaves like retail's.

### What the published economy actually fixes

Wargaming's battle payments are cell-app code. The pinned client proves it
cannot know the per-vehicle part: `scripts/common/items/vehicles.py` reads
`xpFactor`, `creditsFactor` and `freeXpFactor` only under
`if not IS_CLIENT and not IS_BOT`, and none of the 694 shipped vehicle
definitions carries any of them. What the client *does* ship, and what
`server/offline_rewards.py` may therefore use, is `repairCost` (with the exact
`maxHealth * type.repairCost` structure), `crewXpFactor` on 684 vehicles and
`premiumVehicleXPFactor` on 200.

The published structure is followed rather than invented. Credits are a base
`X * vehicle tier` that alone carries the 1.85 victory multiplier, `Y` per
point of enemy durability destroyed independent of tier, `Z` per enemy
detected first with `2 * Z` for an SPG, and one capture payment for a capture
that actually completed, split equally between its participants; the published
list carries no assisted-damage payment, so this build no longer pays one. XP
counts damage and kills with the tier difference taken into account, spotting,
capture and capture defence, adds 50 percent on a win, and yields five percent
of the Combat XP as Free XP. `X`, `Y`, `Z`, the capture payment and every
vehicle's own profitability coefficient stay this product's declared values,
because no source publishes them.

The offline tier curve is a balance approximation. Dividing each
vehicle's captured Ace base-XP threshold by its captured three-mark combined
damage and taking the median per tier gives 0.785 at Tier V falling smoothly
to 0.295 at Tier X - a factor of 2.66 that the previous flat policy did not
have at all. `XP_TIER_PERMILLE` is that curve normalised at Tier VIII, so the
shape follows this proxy and the magnitude is unchanged at the pivot; a test
recomputes it from the baked tables so a re-bake cannot move it silently. Two
caveats belong with it: the measurement pairs a single-battle XP percentile
with a 100-battle damage percentile, which is why it is used as a ratio rather
than an absolute level, and retail publishes no mark data below Tier V, so the
four lowest tiers hold the Tier V value instead of extrapolating.

The premium-vehicle bonus sits outside the badge. `premiumVehicleXPFactor`,
which 200 shipped vehicles carry, is applied to the banked XP and Free XP and
never to the number the mastery badge ranks: `originalXP` stays the bare battle
XP the badge reads, while `xp`, `factualXP` and `subtotalXP` carry the bonus.
Crew training stays on the bare battle XP with only `crewXpFactor` applied
after the save earnings multiplier. The garage settlement owns the final
vehicle XP and Free XP bonus once; a durable `awarded` receipt is never
multiplied again by the results cache. Mastery continues to use unscaled
battle XP.

Every row of the two detail tables is a `ValueReplay` record, keyed by the
name of the value its step applied. `ValueReplay.__iter__` yields
`(op, (param1, value), (recordName, runningTotal))` and
`gui.battle_results.reusable.records.ReplayRecords` stores each step under
`param1`, so the record names are the chain's first parameters and nothing
else. `MoneyDetailsBlock.__getBaseCredits` reads `originalCredits`,
`XPDetailsBlock.__getBaseXPs` reads `originalXP` and `originalFreeXP`, the
boosters rows read `boosterCredits`, `boosterXP` and `boosterFreeXP`, and a
name no step applied reads as zero. Three consequences are load-bearing for
this port. A chain must start at `original*`, or the results screen draws the
battle's own income as zero while the total row stays right.
`addMultipliedValue(startName, factor)` records its step under `startName`,
which *replaces* the base record with the bonus rather than adding a row -
only `__mul__` and `applyFactorToTag`, whose first parameter is the factor,
write a factor-named record, which is why the premium-vehicle row and
`_XPReplayRecords`' `xpToShow = xp - premiumVehicleXPFactor100` both read one.
And a factor step's packed field is therefore the *total* multiplier the chain
applies, not the descriptor's bonus: `premiumVehicleXPFactor` defaults to
`DEFAULT_PREMIUM_VEHICLE_XP_FACTOR = 0.0` and is a bonus fraction, so a 0.5
vehicle packs 150.

This port therefore presents its two account-side coefficients where retail
keeps them. A premium vehicle's credit income has no retail row - vehicle
profitability is inside the base credits the server pays - so
`PREMIUM_VEHICLE_CREDITS_PERCENT` is folded into `originalCredits`. Its
experience bonus has one, `details/calculations/premiumVehicleXP`, so it is a
`__mul__` step by `premiumVehicleXPFactor100`; one visible consequence is that
`xpToShow`, which the summary panel draws, then excludes it exactly as #1513
computes. Whatever the save's earnings multiplier adds above those is packed
as `boosterCredits`, `boosterXP` and `boosterFreeXP` and added with one `ADD`
step, the single #1513 row for an account-owned multiplier on a finished
battle. `__mul__` and `__add__` both write the running total back through the
connector, so the packed total, the breakdown and the wallet agree; a sweep
over 13,680 combinations of battle XP, save percentage and vehicle factor
confirms the chain lands exactly on the banked amount. An award below the
battle's own income has no #1513 row that honestly names the reduction, so
`original*` reports the reduced amount instead.

Kill XP uses victim durability as an offline balance proxy. This does not
implement an exact tier-difference rule: equal-tier vehicles can have different
durability, and these tables do not identify retail reward coefficients.
Before applying the offline tier curve, kill XP is `victim durability / 14`; the plain
frag count pays nothing by itself. The divisor is pinned by the same pivot rule:
the median stock durability of the client's Tier VIII vehicles is 1400, so a
Tier VIII kill still pays the 100 XP the previous flat rule paid, while a kill
is worth 148 damage-equivalent at Tier V and 704 at Tier X instead of a flat
500 everywhere. `_killed_durability` reads the kill ledger's own per-target
rows, so nothing new is persisted or put on the wire.

Tested against the captured mastery thresholds, a winning battle with two kills
and 300 assisted damage now reaches an Ace at 1380 damage on a Tier V, 2770 on
a Tier VIII and 3950 on a Tier X, against captured three-mark averages of 1347,
2659 and 3948. The old flat policy needed 1675, 2765 and 2060 for the same
badge, so the whole tree now sits within a few percent of the retail bar
instead of only the middle of it. That agreement is a consistency check on the
two anchors, not evidence that the retail reward formula or curve was recovered.
Only Windows play can say how the resulting pace feels.

## Stock map-selection lifecycle

The September 18 Create Platoon report exposed another unadapted retail
entry. Public 0.9.22 Python shows `LobbyHeader.showSquad()` and
`SquadTypeSelectPopover.selectFight(actionName)` independently dispatching
`doSelectAction`; the squad entry creates a `prebattle/create` waiting context
and calls `unitMgr.createSquad()`. The offline server has no retail unit reply.
The existing pre-lobby adapter now consumes both entries and calls
`LANSession.join(None, 'random')`, before either native waiting request starts.
Repeated clicks reuse the configured connection; host election, team choice,
connection errors and explicit battle start retain their existing LAN owners.
Training remains a separate selector action. No new native API or network
protocol is introduced, and no shared Waiting state is forcibly dismissed.
Uninstall restores only the adapter's own functions, including inherited
members; partial installation and reinstall are covered.

This callback investigation used the public regional 0.9.22 source as
orientation, plus current adapter/session regression tests. The exact China
1513 `scripts.pkg` was unavailable for a new bytecode audit here. The tests
prove the routing and lifecycle logic, not live Flash binding or dropdown
presentation; both Create Platoon entry points still require Windows play.

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
them. The room carries the waiting-room presentation design reviewed in the
retired predecessor source history: the live room status, a map choice limited
to the server map pool, one start button for the host and one close control.
It also presents the players who wait for
the host, which the stock window cannot do. The desktop launcher owns the
server address before the client starts, so the room never edits an endpoint.

The map choice itself is made in the stock window, because this native surface
cannot draw the client's map images. The room offers `RANDOM MAP`, which
selects the server's own random option, and `MAP`, which hands the browse to
that window. The battle time the window publishes travels with the next start
request and becomes the round length the server counts down.

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

### Handing the screen to the stock map window

The lobby movie draws at z 0.5 and this room at z 0.1, so the room covers every
Scaleform view inside that movie, and it owns the native cursor while it is
open. The room therefore never opens the map window itself: it asks its owner,
which closes the room first - releasing its roots and restoring exactly the
cursor state the lobby had - and only then calls `loadView`. The window's own
close hook runs before the Scaleform view finishes tearing itself down, so the
room is reopened from a `BigWorld.callback(0.0, ...)` after that, fenced by the
client generation and the waiting state. Cancelling that reopen is part of
every teardown: a battle start, a leave and a stop each retire the window and
drop the pending return to the room.

The window is the exact `TrainingSettingsWindow` already described above, with
`isCreateRequest` still true, so its stock `getInfo` never asks for a prebattle
entity this client does not have. The adapter then reports `canChangeComment`,
`canMakeOpenedClosed`, `privacy` and `create` as false, and opens the view on
the room's current arena and battle time. Whether that stock view hides or only
disables the three fixed controls is a property of `gui.pkg`, not of any client
script, and remains unproved until it is seen on the exact Windows client.

The packaged picker replaces only the exact #1513 `trainingWindow.swf` resource.
Its `maxPlayers` value is `DefineEditText` id 10 at sprite 16 depth 12, while
the adjacent `#menu:training/create/maxPlayers` label is the unnamed id 14 at
depth 16. The build keeps both objects and their layout but changes their text
color alpha from 255 to zero. Exact `lobby.swf` bytecode only assigns
`maxPlayers.text` when the map changes; it does not replace the text format or
alpha, so repeated map selection cannot restore either string.

`updateTrainingRoom` is the single Scaleform-to-Python call of that view
(`TrainingWindowMeta`). In this mode it records the map and the battle time and
returns to the room instead of starting the battle. The battle time arrives in
whole minutes inside the exact `getTrainingBattleRoundLimits` range: 300..1800
seconds for a plain account and 60..14400 seconds for one carrying
`ACCOUNT_ATTR.DAILY_BONUS_1`. The LAN server accepts that same range for one
round, denies anything else as `invalid_round_length`, and keeps 900 seconds
for a start request that carries no round length.

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

Exact `#1513` destroys the battle GUI before its late
`BigWorld.target.clear()` in `PlayerAvatar.onBecomeNonPlayer()`. That clear
synchronously enters `PlayerAvatar.targetBlur()`, which removes the target
edge and unconditionally reads `TriggersManager.g_manager`. A hidden native
target may already be absent from the callable `PyTarget` result while its
pending blur still retains the entity. The shared presentation-quiesce boundary
therefore clears native target focus once per battle before postmortem, local
pose, outline or remote-entity owners are retired. ABI and lifecycle audits pin
the target-blur signature and this stock teardown order; only Windows `#1513`
acceptance can prove the native result-screen behavior.

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
the authoritative pose source. The longitudinal, traverse, terrain and
collision integrator derived from the retired predecessor owns the player pose
and publishes it through the exact #1513 `Vehicle.model.matrix`,
`ConsistentMatrices.__setTarget`,
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
the carrier boundary derived from the retired predecessor: a Python gameplay
vehicle owns authoritative pose/health/collision and a separate
`OfflineEntity` owns the rendered model.
The only version-specific substitution is #1513's verified
`prepareCompoundAssembler` resource path. This restores the map-base formation
and copied bot integrator without feeding a second physics owner.
`BigWorld.Entity.teleport` remains forbidden; #1513 rejects it for an in-world
client Vehicle as `Operation is not allowed`.

### Hull autorotation and the sniper hull lock

Every part of retail's hull lock except the cell itself is stock #1513 code the
port already runs. `AvatarInputHandler.start` seeds `__isAutorotation` from the
arcade control mode, which prefers nothing, so a round starts autorotating.
`onControlModeChanged` then asks the new control mode for
`getPreferredAutorotationMode()`; a mode that returns a boolean saves the
previous setting, forces its own, and publishes it through
`PlayerAvatar.enableOwnVehicleAutorotation`, which both invalidates
`VEHICLE_VIEW_STATE.AUTO_ROTATION` for the lower-left damage-panel indicator
and sends `VEHICLE_SETTING.AUTOROTATION_ENABLED` down the vehicle mailbox that
this port answers. Leaving that mode restores the saved setting.

`SniperControlMode.getPreferredAutorotationMode` returns
`isYawHullAimingAvailable or (chassis.rotationIsAroundCenter and gun
.turretYawLimits is None)`. Running that exact code object against a stub
`BigWorld` gives `False` for a limited-traverse gun on either chassis, `True`
once the vehicle has yaw hull aiming, `True` for a fully rotating turret on a
centre-pivot chassis, `False` for one on a track-pivot chassis, and `None`
when the player vehicle is not yet in `BigWorld.entities`. Entering sniper on
a limited-traverse vehicle therefore forces autorotation off, and
`enableSwitchAutorotationMode` — `preferred is not False` — makes both the
`CMD_CM_VEHICLE_SWITCH_AUTOROTATION` key and `PlayerAvatar.moveVehicle`'s
re-enable no-ops for every vehicle the mode prefers `False` for, which is both
the limited-traverse case and a fully rotating turret on a track-pivot
chassis. Only the first of those has an arc to notice it. Outside sniper the
key toggles the lock and any key-down movement command without
`_MOVEMENT_FLAGS.BLOCK_TRACKS` turns it back on.
`SiegeModeControl.handleKeyEvent` consumes that same key first on a siege
vehicle. The whole package writes `__isAutorotation` in five places, all in
`AvatarInputHandler`, so nothing else can release the lock while sniper is
active. The ABI audit pins the signatures, the code names and the control flow
of all five methods.

Modern retail behaves differently, and the difference is a version boundary,
not a defect here. Wargaming added both the in-battle `X` toggle for sniper
hull lock and the game setting for its default state in Update 1.12.1 of April
2021, describing the behaviour it replaced as "when you enter Sniper mode, the
hull is automatically locked and you cannot aim outside of the aiming angles",
which is exactly what this January 2018 build does. Reproducing the 1.12.1
convenience would be a deliberate product deviation from #1513, not a parity
fix.

Wargaming's own newcomer guide states the mode rule and no other condition:
a turretless vehicle's gun "can only move horizontally up to a limit, after
which the hull has to be turned to move it further", SPGs auto-turn the hull in
every aiming mode, and tank destroyers do not auto-turn it in Sniper mode. It
is silent on the throttle, so it neither supports nor refutes a drive-input
condition.

Only the cell behaviour is ours. The copied local physics reads the stock
`getAutorotation()` and, when the unclamped mouse target leaves the installed
`gun.turretYawLimits`, feeds one binary rotation direction into the single pose
integrator; the descriptor, native gun rotator and copied traverse physics keep
owning the arc, gun speed and dispersion. A live A/D command,
`CMD_BLOCK_TRACKS` and any live throttle — including the native R/F cruise
presets — all suppress it, so the mouse turns the hull only while the player
issues no movement command at all.

The pinned executable's own flags are what place the composition there.
`WGGunRotatorImpl` computes the direction in `0x00f5ad40` from elapsed time,
the desired yaw, the current turret yaw (`+0xcc`), the installed yaw limits
(`+0x28`/`+0x2c`, valid per `+0x30`) and the turret and vehicle rotation speeds
(`+0x60`/`+0x68`), clears `autorotationFlags` (`+0xd8`) whenever the clamped
rotation reaches the desired yaw, and otherwise publishes `5` or `9` —
`_MOVEMENT_FLAGS.FORWARD` beside one rotation bit, through
`lea eax, [eax*4 + 5]`, and never a bare rotation bit. Those flags are shaped
like a whole movement command rather than a rotation contribution: ORing `5`
into a player's `BACKWARD` yields `FORWARD | BACKWARD`, which the stock
`PlayerAvatar.moveVehicle` decode resolves as forward. That reading is
consistent with a cell that applies them while the player issues none, but it
does not exclude a cell that masks the rotation bits out of them under a live
drive command, and the `FORWARD` bit alone therefore proves neither rule. What
it does rule out is the inference this section previously drew: that the
direction routine reading no drive input means a driving hull follows the
mouse. It only says where the composition lives.

How the retail cell merges those flags is server Python that no client build
ships, so the composition remains an inference. Windows play is the evidence
that selected this one, and it is the rule this port shipped before the
throttle-independent reading replaced it: a limited-traverse hull that kept
following the mouse under throttle was reported as wrong, because it steered
the vehicle off the driver's heading whenever the camera moved. Two gaps stay
open against a retail cell. The port applies only the rotation half of the
command, never its `FORWARD` bit, so a parked retail hull may creep forward
while it aligns where this port pivots, and native track animation sees that
same bare rotation. A coasting hull under no drive command also autorotates
here, because this port gates on input intent rather than measured speed.
Neither difference has been measured on Windows.

LAN pose samples retain the fractional remainder of the nominal 30 Hz
publication interval. Clearing the entire accumulator quantised a 40 FPS
render loop to 20 Hz and 45/50/75 FPS to 22.5/25/25 Hz. At most one current
pose is sent per rendered frame, so recovery from a slow frame never bursts
stale samples.

The player-visible spotting path derives its 50-metre proximity, static LOS
and allied observer relay from the retired predecessor. Its
deterministic no-skill memory uses the historical 5--10 second rule's
guaranteed ten-second
disappearance bound. Enemy
compound models and their stock marker/minimap visuals cross one visibility
boundary, so an unspotted vehicle cannot remain visible in only one UI layer.

The exact #1513 `gui/shared/items_parameters/params.pyc` consumer
`VehicleParams.__getInvisibilityValues` (source lines 599--610) calls
`items.utils.getClientInvisibility` and then multiplies both returned values
by `gun.invisibilityFactorAtShot`. `getClientInvisibility` already includes
`computeBaseInvisibility`'s paint bonus and the resolved camouflage-net aspect.
The port keeps that complete-value shot factor and effective-parameters schema
v1. A published formula that exempts paint or the net is not substituted for
this directly observed client consumer. This proves the client parameter
composition, not the unavailable cell-app detection implementation.

The worker and visible client use the same existing worker LOS endpoints and
end tolerance. Both carry the exact-identity broken-skin filter, so an accepted
broken fence skin can yield while an unrelated wall or surviving replacement
surface still blocks. The prepared filter is reused for at most 0.25 seconds;
round teardown clears it. Target stationary clocks are sampled each visibility
frame even when an observer-target pair is deferred by the native-ray budget.

Cover within 15 metres of the observer is transparent to that observer,
following [WG's spotting guidance](https://wargaming.net/support/en/products/wot/article/10222/?redirect_lang=en).
The guidance is current and does not establish an exact #1513 server contract.
For static foliage, the distance and its early-rejection radius use the actual
baked horizontal parallelogram: projected axes can be non-orthogonal, and the
source box radius need not enclose that footprint. Fallen-tree proximity still
uses a conservative horizontal-radius approximation. Vegetation coefficients
remain the existing 0.15 per volume, 0.60 combined limit, 0.95 total concealment
limit and complete removal of nearby foliage after firing. These are retained
port settings, not claimed retail constants. [The official 7.5 update notes](https://worldoftanks.com/en/news/general-news/75-update-note/)
confirm that bush density matters; they do not justify assigning every volume
the same maximum coefficient. [WG's later Berlin map-development article](https://worldoftanks.eu/en/news/general-news/berlin-map-development/)
distinguishes dense vegetation at 50 percent from sparse vegetation at 25
percent; it is not evidence that every #1513 asset should receive 50 percent.
The exact tree cache also reads a per-resource `density` with values from 0
to 0.50, separately from falling-tree mass and other physics parameters.
Its use as the final spotting addend has not been established by the reviewed
Python consumers, so it is not substituted for that addend here.
Foliage catalogs remain schema v4. Single-ray
coverage, per-asset camouflage, native filtering and actual Windows spotting
behavior remain outside the local contract evidence.

That single boundary was not sufficient. Windows playtesting reported a green
penetration indicator, ground dust and a visible silhouette for an unspotted
enemy, and two further stock surfaces explain it. Exact #1513 bytecode confirms
that `ProjectileMover.getCollidableEntities` filters `arena.vehicles` only by
`BigWorld.entity` presence, `isStarted` and `segmentMayHitEntity`. It also
confirms that the trajectory and SPG hit-marker modules retain directly
imported copies of that query, while their copies still resolve the canonical
segment prefilter at call time. Retail relies on the server AOI to remove an
unspotted enemy from the facade; a client-created LAN remote never leaves it,
so the port gates both the canonical query and prefilter for an undrawn remote.
This is a visible-client presentation rule only: hidden-worker projectile
collision enumerates the runtime records and calls each target directly, so a
geometric blind hit still resolves and damages the target.

Exact #1513 bytecode also confirms that `Vehicle.show(False)` selects
`ShadowPassBit`, rather than fully hiding the compound. The initial enemy gate
therefore follows the stock `startVisual` call with
`CompoundAppearance.changeVisibility(False)`; that method writes
`compoundModel.visible`, `showStickers` and the crashed-track controller.
`ProjectileMover.add` independently sets `visible` and `visibleAttachments` on
its projectile model, while the fire extra attaches through
`appearance.boundEffects`. The port mirrors the attachment flag when the
native compound exposes it as writable, stops the fire extra on the hide edge,
settles native belt speed immediately, and stops feeding later belt/engine
presentation while hidden. Static package inspection does not prove that
`PyCompoundModel` inherits the plain model's `visibleAttachments` property, so
that one flag still requires exact Windows runtime acceptance; the runtime logs
once when the attachment gate is absent or read-only.

Windows playtesting in an SPG aiming camera then reported a ground shadow and
exhaust under an unspotted enemy, and exact #1513 bytecode names both owners.
`CompoundAppearance.__onPeriodicTimer` runs every `_PERIODIC_TIME` (0.25 s) for
a living vehicle and calls `__updateEffectsLOD`, which enables the
`CustomEffectManager` dust selector within `_LOD_DISTANCE_TRAIL_PARTICLES`
(100 m) and the exhaust selector within `_LOD_DISTANCE_EXHAUST` (200 m) of
`BigWorld.camera()`. Neither test reads a draw flag, and the distance is
measured from the camera rather than the player, so a strategic or arty camera
parked over its aim point enables both effects for every hidden enemy beneath
it. `CustomEffectManager.deactivate` is not a usable hide because it also
clears the manager's vehicle; its selector loop is, and
`MainSelectorBase.update` returns immediately once a selector is stopped. The
port therefore stops both selectors on the hide edge and wraps
`__updateEffectsLOD` in the existing compat install/uninstall discipline so the
periodic timer cannot restart them for an undrawn LAN remote. Only two selector
classes override `settingsFlags`, `MainCustomSelector` for `SETTING_DUST` and
`ExhaustMainSelector` for `SETTING_EXHAUST`, so stopping every selector is the
complete gate.

The second owner is the ground occlusion geometry. `CompoundAppearance.activate`
attaches its `VehicleDecal` to the compound root, hull and turret nodes, and
`__setupModels` attaches a `BigWorld.Splodge` built from `chassis.AODecals[0]`
to the hull node whenever `MAX_DISTANCE` (500) is positive. Both are node
attachments carrying `maps/spots/TankOcclusion/TankOcclusionMap.dds`, so they
are exactly what the unproven `visibleAttachments` flag would have covered.
`VehicleDecal.attach`/`detach` are the exact stock pair and both are idempotent
through the decal's own `__attached` flag; `__attach` resolves the hull node as
`compoundModel.node(TankPartNames.HULL)`, which is the same node
`__attachSplodge` used, so the runtime reads it from the decal before detaching
and re-attaches the splodge to it on the reveal edge. `VehicleDecal.__reattach`
restores every decal when `onSettingsChanged` sees a new `SHADOWS_QUALITY`,
which is outside any spotting edge, so the wrapped `__updateEffectsLOD` re-
asserts the decal gate on the same bounded cadence; both stock calls are
idempotent, so the steady-state cost is a flag read. Which of these layers
draws the artefact a tester photographs is still an exact-Windows question; all
of them are now closed together.

The muzzle effect is the same class of leak, and the entity definitions say
why. `scripts/entity_defs/Vehicle.def` declares `showShooting` under
`ClientMethods`, so it is a cell-to-client RPC on the Vehicle entity, and that
entity carries `IsManualAoI` together with the `receiveVisibilityUpdate`,
`onDetectedByEnemy` and `onConcealedFromEnemy` cell methods that drive its
membership. A retail client that has not spotted an enemy is not in that AoI
and never receives the call. `Vehicle.showShooting` itself guards only on
`isStarted` and the siege state - the `isPlayerVehicle` branches around it are
the local waiting-for-shot handshake - and the `shoot` extra it starts is
`ShowShooting`, whose `_start` plays `gunDescr.effects` through
`EffectsListPlayer` bound to the vehicle's own compound model: the muzzle
flash, its smoke and the gun sound. This runtime receives every LAN shot
instead, so the runtime skips that presentation for a remote whose draw pass is
closed, which reproduces the retail delivery rule.

The tracer is a different entity and must not follow it. `Avatar.def` declares
`showTracer` and `stopTracer` under its own `ClientMethods`, and the player's
Avatar is always inside its own AoI, so retail still draws the tracer of a shot
fired by a vehicle the client cannot see. The runtime's projectile keeps its own
owner, so a blind shot still draws its tracer, still resolves and still damages;
this also matches the impact-effect gate the runtime already applies to a
terminal event on an unspotted target.

The entity AOI that owns the world model follows the observed vehicle, not the
player's own hull. The local integrator stops the moment the player dies, so
`_local_position` freezes at the wreck; centring the 565 m AOI there hid the
ally the postmortem camera had just switched to, and every vehicle around it,
whenever the wreck was far away. The runtime resolves the AOI origin from the
currently spectated entity instead and re-evaluates spotting on the next frame
after a viewpoint switch.

Authority Bot snapshots also retain the retired predecessor's no-rewind rule:
the client that integrates a Bot never reapplies its older server echo pose,
while other clients continue to interpolate those canonical snapshots.

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
freezes the public `gunRotator.getCurShotPosition()` ray, native dispersion and
mounted shell once, then starts flight and collision on the firing client.
The local gun consumes ammunition and starts reload when the ordered launch
enters its transport queue. `Vehicle.showShooting()` runs on the next local
callback, after the triggering `PlayerAvatar.shoot` has installed its wait
token. It receives the descriptor's positive burst count and the authoritative
flag; zero is not a single-shot sentinel and leaves the native firing extra
unbounded. Canonical echoes never debit ammunition or play the muzzle twice.
Remote events use the same finite presentation. Only the LAN server applies
the resulting damage and destruction to shared state.

Shell visuals use the stock #1513 `ProjectileMover`: an owned player launch
starts locally, while remote launches arrive through the canonical ledger. `PlayerAvatar.__startWaitingForShot` still owns the stock
120--200 ms predicted-muzzle timeout; a tracer uses the owned local launch, a
canonical remote launch or an active snapshot, with its frozen muzzle/velocity
and the current
on-screen `HP_gunFire` as stock's separate, 20 m-guarded visual start. A late
first snapshot seeds the checked trajectory pose. Once started, the stock
`PyBallisticsSimulator` motor owns cosmetic flight: progress snapshots neither
clamp nor rewrite it, and no Python Servo competes for its pose. Each shot owner
computes collision, damage, ricochet and terminal proposals; the LAN server
commits their shared outcomes.

The presenter binds the mover to the loaded space before `add`, reserves its
logical shot identity before native creation can re-enter, and deduplicates that
identity even if the native motor expires before the canonical result. Managed
rows disable stock `fireMissedTrigger`; their `__notifyProjectileHit` path is
scoped out so only the admitted terminal emits the existing input/flock/missed
feedback once. Terminal events call stock `hide` or `explode`, while a ricochet
retires the old native id and starts a new one with stock `hold`. `hide` owns its
negative-id row and particle tail; capacity counts both active and tail rows
without detaching a live native motor. A late world terminal can use stock's
unknown-id explosion without recreating a tracer. Round reset and teardown
destroy the mover before forgetting its ids. A failed native teardown retains
that owner and stops further cosmetic admission. The ABI/lifecycle audits pin
the relevant calls and private-row consumers. Exact Windows acceptance must
still establish native rendering, collision appearance, tail lifetime, late
terminal correction and frame pacing; local tests only establish the adapter's
presentation and per-shot ownership boundaries.

A hit vehicle also receives retail's hull shot impulse. Exact #1513
`Vehicle.showDamageFromShot` builds the first decoded hit point's world-space
axis from `compoundModel.node(componentName)` and calls
`appearance.receiveShotImpulse(dir, shotEffects[effectsIndex]['targetImpulse'])`
only inside the decoded direct-hit branch, so an HE near miss presents
`armorSplashHit` with no hull reaction; the impulse is scaled by neither damage
nor calibre. `CompoundAppearance.receiveShotImpulse` skips a damaged model,
forwards to `swingingAnimator.receiveShotImpulse` without a `None` guard, and
also forwards to `CrashedTracksController.receiveShotImpulse`, which is
`return None` in this build. `model_assembler.createSwingingAnimator` calls
`setupShotSwinging(hull.swinging.sensitivityToImpulse)`, and
`vehicle_assembler._assembleSwinging` installs the animator as the HULL node's
provider. That assembly runs from `__assembleNonDamagedOnly` for every vehicle
that starts a non-damaged visual, alongside `createWheelsAnimator`,
`assembleSuspensionIfNeed`/`assembleLeveredSuspensionIfNeed` and
`assembleSuspensionController`, so it is not limited to the player's tank and
the port checks both suspension variants. The animator's provider role keeps it
live while `CompoundAppearance.__linkCompound` keeps the compound root on
the entity matrix; this port's pose-provider swap therefore leaves the animator
in the transform chain, and its `worldMatrix` rebind supplies the same input
retail passes. Disassembly of the exact executable shows the native animator
method accumulating `dir * impulse` into three floats of the animator object
itself, reading no filter, physics body, node or model, so the only live
requirement is a stock animator. Every vehicle definition in the pinned
package carries `sensitivityToImpulse`, and `vehicles/common/shot_effects.xml`
carries `targetImpulse`, so both inputs come from shipped client data rather
than a substituted constant. The port keeps the engine-ownership and
proven-rebind gates, rejects an absent animator instead of raising inside stock
code, normalises its contact direction, and presents no impulse for a splash,
killing or dead-target hit. Retail's companion `inputHandler.onVehicleShaken`
camera shake is not ported. Whether the resulting rocking magnitude matches
retail still needs exact Windows acceptance.

Non-penetrating HE and nearby splash now require a real structural collision
inside the shell's blast sphere. The previous victim-origin radius check and
whole-hull minimum-armour fallback could respectively miss a large vehicle's
near surface and invent damage when no plate was hit. Loaded component
`hitTester.bbox` values now provide at most thirteen directions per structural
component; the existing four-field native collision adapter establishes the
first structural plate on each ray. Selection maximizes the existing HE damage
law over reachable candidates, sharing one victim damage roll and using the
actual burst-to-plate distance and nominal armour. External screens remain in
the collision prefix; their gap to the structural plate now contributes to
blast distance. This change retains the existing structural armour absorption
policy and does not add an unverified screen absorption formula.

Blast candidates also require a clear static scenery segment through the
existing mask-128 query and broken-destructible filter. A surface burst starts
that visibility query on the incoming side so a wall cannot be bypassed by
starting inside it. Direct HE uses the established collision-query burst;
nearby victims use the projectile cursor and the same per-target presentation
offset as direct collision. Hull/ground split and detached-turret histories
retain the direct-collision path's explicitly recorded live-pose boundary.
Missing geometry never supplies armour or an interior critical cone, and a
failed nearby victim cannot discard another victim's effect. Penetrating HE
keeps full direct damage and does not add an external splash explosion.

These changes follow the historical [Wargaming HE damage explanation](https://worldoftanks.com/en/news/general-news/high-explosive-damage-explanation/),
which describes damage through reachable armour in an explosion sphere. The
finite component directions are a local implementation, not recovered retail
server sampling. Dynamic vehicle/wreck occlusion and exhaustive weak-spot
coverage are not established by this static-scene check. AP/APCR normalization,
HEAT gap accounting and the stock penetration preview remain unchanged.
Pure-data and adapter tests establish the stated local geometry and terminal
contracts; exact Chinese HD #1513 Windows gameplay and frame pacing still need
runtime acceptance.

The same animator needs that rebind on the player's own tank.
`CompoundAppearance.activate` is the only stock writer of
`swingingAnimator.placingCompensationMatrix` and `swingingAnimator.worldMatrix`
in the pinned package: it copies the compensation off the native vehicle filter
and links the animator to the compound root's matrix provider object that
exists at that moment. This port replaces that root with the copied pose after
the native lifecycle completes, so both stock links go stale on the player
vehicle exactly as they do on a native remote. The port repeats the same two
writes against the live provider, with an identity compensation because the
copied pose already carries authoritative terrain pitch and roll, restores the
stock pair when it detaches, and re-asserts the pair only when the animator
object itself changed. `__prepareSystemsForDamagedVehicle` clears
`swingingAnimator`, so a missing animator is a normal damaged-model state and
is recorded rather than raised. No stock Python code in the package writes
`accelSwingingDirection`, and this build's `changeEngineMode` no longer arms
acceleration swing; `stopSwinging` is the only remaining Python writer of
`accelSwingingPeriod`. The exact executable nevertheless exposes both fields
as writable floats. Its native animator reads root translation across updates,
derives acceleration, gates its sign with `accelSwingingDirection`, and runs
the shipped pitch/roll response while a positive `accelSwingingPeriod` counts
down. This reversible experiment restores only the retired stock input-edge
law: two seconds for a forward, reverse, or stop transition, one second for a
stationary steering transition, and directions `-1`, `1`, and `0` for forward,
reverse, and neutral respectively. Sparse `SWING_ARM` and `SWING_FRAME` lines
record the live period/direction plus copied-root and stock-HULL orientation
across the window. Static inspection proves the property and owner contracts;
only exact Windows acceptance can prove that the native HULL output is visible
and that its magnitude feels correct.

Critical-hit calculation follows the same proposal/commit boundary. The
hidden worker runs a device law derived from the retired predecessor against an
explicit detached snapshot of the target descriptor, pose, collision
components and critical state. That calculation cannot change the live target
or invoke native kill
and damage-panel callbacks. The proposal carries the target's exact base/ack
token and its pre-critical hull damage separately. If the target was repaired,
extinguished or otherwise revised before the report arrives, the server applies
the successful module/crew damage operations over the latest canonical state
for both players and Bots. It retains unrelated progress and recomputes lethal
module consequences instead of installing a stale full state. A monotonic server
event is delivered before the snapshot containing its new HP/critical state;
the client presents stock shot results and battle events, then installs the
accepted revision exactly once.
Repair reports remain pending until the server acknowledges their proposal
revision, so a successful socket write or an older snapshot cannot rewind the
HUD state.

The internal-module model now retains indexed Console collision surfaces in
component-local metres. It is not the recovered #1513 PC-server model.
`internal_layout_console.py` supplies the source triangles; the exact PC
9.22 `collision_client` bounds and installed `models.undamaged` part select the
registered frame. The #1513 bytecode chain is `shared_readers.readModels` ->
`_readHull`/`_readTurret` -> `ModelStatesPaths.undamaged`; the direct model
consumer is `tankStructure.getPartModelsFromDesc`. Destroyed/shared model
paths cannot select an installed variant. No new native API is introduced.
Native gun and track contacts retain their existing ownership. Vehicle,
turret and gun transforms remain the current collision pose's transforms.

HKX decoding uses big-endian words with numerical low-to-high x/y/z fields,
section-local offset/scale, shared vertices, data fixups and indexed quad
triangulation. The old bounds-only decoder's bit order could reproduce an
AABB while moving individual vertices by over one metre. As independent
resource checks, 310 shared surfaces on IS-7, Type 59, T1 Cunningham, Maus,
FV215b (183) and M103 match across HKX/BigWorld formats after ignoring
zero-area triangles. This supports the reviewed Console format, not a claim
that other Havok platforms or all later vehicle revisions are identical.
[Smithbox HKX2](https://github.com/vawser/Smithbox/tree/main/src/Havok/HKX2)
is a format lead; the Console resources supply the validation evidence.

Mesh bounds accelerate queries but never replace occupied geometry. Exact
edge-connected pieces and their gaps survive baking, ray/starts-inside,
distance and HE-cone queries. Open/nonmanifold pieces provide surface contacts
only; no hole repair or invented solid is applied. Source meshes bypass
physical caps, template shapes, relocation and saved calibration overrides.
Invalid payloads and unmatched component variants are reported per target;
an incomplete crew does not replace the remaining decoded interior with an
archetype. A small per-piece BVH is built lazily with the cached layout.

`BattleRuntime._vehicle_trace` still limits solid-shell travel to ten calibres
from the first vehicle material. HE uses its separate finite interior cone.
For non-penetrating HE and nearby explosions, that cone starts at the proved
structural contact selected by the blast search, not at the outside explosion
position; visuals and other victims still use the original world burst. If no
structural surface is reachable, only native device contacts up to the shell's
stopping point survive. The historical official
[HE explanation](https://worldoftanks.com/en/news/general-news/high-explosive-damage-explanation/)
describes internal damage for penetrations and near misses, so both retain the
explosion path. It does not establish the precise module-damage attenuation
formula or whether its stated 45-degree cone uses a full or half angle. The
existing angle, depth, and device roll remain reconstruction boundaries; hull
HP loss is not evidence for a new proportional module-damage multiplier.
The critical loop scores each reached device once using `damage[1]`; this
change does not alter saving throws, ammunition bookkeeping or damage rolls.
The current device roll is uniform within +/-25%; available client contracts
do not establish that server-side distribution. The common ammo-bay material
specifies 0.27 for projectile and explosion hit chances. Deadeye adds three
percentage points for AP/APCR/HEAT. A successful hit reducing the rack to zero
destroys the vehicle without a second detonation roll.

The launcher editor writes `damage/devices`, and the mounted-shell snapshot
and projectile launch preserve a value of 2000. Tests cover AP, APCR, HEAT,
APHE and HE, player and Bot victims, the 27% saving-throw boundary and duplicate
contacts with one rack. Even the low 1500 damage roll destroys a reached
ordinary rack after its saving throw. This proves the numerical path once a
module contact exists; it does not prove that a retail aiming point intersects
the selected Console mesh. A missing/invalid profile supplies no internal contact,
so raising damage cannot fix missing geometry. Run the read-only inventory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/audit_internal_layouts.py \
  "$WOT_0922_CLIENT/res/packages/scripts.pkg"
```

The reproducible source bake contains 650 decoded vehicles out of 680 listed
definitions. The other 30 comprise 20 with no Console source and 10 with no
registered usable hull/interior. Eight retain explicitly identified authored
reconstructions; 22 have no profile. Within decoded entries, missing targets
remain explicit (including two incomplete crew rosters). The audit covers
870 available turret configurations; it does not claim completeness for an
unavailable variant. Aliases retain reviewed archive identity, component or
content evidence and crew mapping, rather than suffix/name guesses.

Reproduce the bake into temporary output, then compare before replacing the
tracked catalog. The password file is private and must not enter output:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/bake_internal_layout_console_0922.py \
  "$WOT_0922_CLIENT" --cache "$MESH_CACHE" --password-file "$PRIVATE_PASSWORD_FILE" \
  --output "$MESH_OUTPUT/internal_layout_console.py" --report "$MESH_OUTPUT/bake.json"
PYTHONDONTWRITEBYTECODE=1 python3 tools/audit_internal_layouts.py \
  "$WOT_0922_CLIENT/res/packages/scripts.pkg" --verify-meshes --cache "$MESH_CACHE" \
  --bake-report "$MESH_OUTPUT/bake.json" > "$MESH_OUTPUT/audit.json"
python3 tools/internal_geometry_evidence.py \
  --scripts-package "$WOT_0922_CLIENT/res/packages/scripts.pkg" --cache "$MESH_CACHE" \
  --baseline-root "$BASELINE_CHECKOUT" --output "$MESH_OUTPUT/evidence"
```

Review the checked-in [four-view X-ray](tools/evidence/internal_mesh_0922/is7-runtime-xray.png),
[roster audit](tools/evidence/internal_mesh_0922/roster-audit.json) and
[query evidence](tools/evidence/internal_mesh_0922/query-evidence.json).
The audit's `--compact` option reproduces the review inventory.
`tools/audit_internal_mesh_formats.py` reproduces the cross-format probes
with `--cache`, `--password-file` and `--output` arguments.

The evidence renderer requires NumPy/Matplotlib; the bake requires pyzipper.
Its baseline checkout should be the pre-correction `f436f298` source. It
exports both revisions through `build_layout`, uses the same PC frames and
renders top/side/front/oblique views with a source legend. Its front-shoulder
ray grid records coordinates, angles and 100/130/152 mm finite hull-only
budgets; tracks/spaced plates may shorten that budget. The old screenshot's
exact ray was unavailable, so this is a defined geometric experiment, not
reproduction of that aiming point or a damage probability.

The OCI run used an extracted `scripts.pkg` with pinned entity-definition
and PYC contracts (`--scripts-package` bake mode). A full #1513 installation
was unavailable: `inspect_client.py` could not verify it. Resource contracts,
870 layout snapshots, analytic regression tests and 35,277 BVH-versus-flat
queries establish source/logic evidence only. The workload uses 7,035 unique
meshes and 11,759 pieces; standalone Linux timings do not establish Windows
frame pacing. Native physics, rendering, lifecycle and gameplay feel still
require acceptance on Chinese HD 0.9.22.0.1 #1513.

Ammo-rack death also has a separate presentation contract. LAN health remains
zero, but the stock Vehicle, marker feedback and local
`PlayerAvatar.updateVehicleHealth` receive
`SPECIAL_VEHICLE_HEALTH.AMMO_BAY_DESTROYED` (-5). The marker consumer preserves
that special negative value, while it normalizes ordinary negative health to
zero. The Avatar writes its raw argument back into the Vehicle at local death,
so passing zero there would erase an earlier correction. A late critical cause
corrects the marker without repeating the death/kill/postmortem callbacks.
The ABI audit pins the negative constants and marker consumer; regression
tests fail against the old zero-only presentation for local, remote and late
ammo-rack deaths.

Turret detachment uses one worker-authored flight accepted by the LAN server. Every
half of the presentation is stock. `SPECIAL_VEHICLE_HEALTH.TURRET_DETACHED`
(-13) is `AMMO_BAY_DESTROYED` (-5) with one further bit cleared, so a
detached wreck is also ammo-bay destroyed; `vehicle_damage_state` maps -13 to
the `ammoBayExplosion` state and the `exploded` model chain, and
`CompoundAppearance.__requestModelsRefresh` drops the turret from the
assembler once `Vehicle.isTurretDetached` is true. The flying half is the
stock `DetachedTurret` client entity, whose `__prepareModelAssembler` builds
`turret.models.exploded` plus `gun.models.exploded`, whose
`_TurretDetachmentEffects` plays the shipped `turret_flying_*`,
`turret_touchdown_*` and `flamingOnGround` chains from the turret's
`turretDetachmentEffects` descriptor, and whose `VehicleStickers` reattach the
vehicle's own marks. `Vehicle.showAmmoBayEffect` is unchanged: it still only
forwards mode and fireball volume, and its projected-speed argument is still
unused.

Three deliberate divergences, each because the retail owner does not exist
here:

- **Launch remains a product choice; subsequent motion is worker physics.**
  `DetachedTurret`'s velocity, angularVelocity and applyForceToCOM are cell-side;
  the client WGTurretFilter cannot be fed by Python. The initial seeded throw
  keeps the existing product launch speed and stock specific-energy units
  (`0.5 * speed ** 2`). Subsequent `rigid_turret` frames use mounted turret and
  gun weights, component bounds, compound centre of mass and box inertia.
  This is a compound-box solver, not recovered retail cell code. Scenery
  impact is inelastic with a maximum-dissipation no-slip constraint; the port
  does not assert a recovered steel/material friction or restitution value.
  The earlier statement that wg_collideSegment returns no normal was wrong:
  the same #1513 query already used by suspension returns hit[0] and hit[1].
  The new worker adapter preserves both point and normal, including the
  existing destroyed-skin filter and query flag 128.
- **One authority publishes motion and acknowledgements.** The server accepts
  only a confirmed ammo-rack wreck from the current worker/round/epoch.
  Monotonic motion_seq revisions carry pose, linear and angular velocity,
  contact/sleep state, impact serial, and cumulative contact acknowledgements
  atomically. A player's own integrator sends cumulative opposite linear and
  angular momentum; coalescing/reordering cannot repeat an acknowledged shove.
  Bots and scenery contacts are integrated by the worker. A resting body sleeps
  only with its centre of mass over a real support polygon; support removal
  resumes gravity. Final deaths still publish through the terminal tail.
- **Contact no longer lifts every overlap to a roof.** Actual component SAT
  faces, inverse masses and contact-point inertia replace vertical-only
  support correction. A ground turret hit from the side retains a horizontal
  entry face even when a delayed pose overlaps deeply. Geometric recovery is
  scenery-swept and never converted into launch velocity. A roof can support a
  falling turret, and its carrier can drive away. Movable body revisions bypass
  the old immovable movement/navigation gate, so the native visual cannot trap
  a tank through a competing collision owner. Exact turret/gun hit testers
  still own shell queries in the accepted frame; missing component geometry
  never becomes a generic obstacle. Native presentation reuses one entity and
  emits touchdown once per new impact serial. Continuous crushing HP for
  stacked hulls or turret debris remains unimplemented. Exact Windows #1513
  acceptance is still needed for frame pacing, native presentation and feel.

The server admits at most twelve detached turrets per round, matching the
32-bit client's resident model budget. Every accepted record can be displayed
when the camera enters range, even if that client never saw the original
explosion. Preparation waits for the source Vehicle's normal started and
detached lifecycle, and creation uses the accepted elapsed flight/rest pose
without local collision queries. A failed asynchronous attempt retains its
identity until safe native retirement; retries have a bounded cadence and
never allocate a second unresolved entity for the same actor.

The `20260913-062334-85d112e438d5` Windows report tested
`colorfulmeans-34719995064-1` and exposed a missed numerical/performance case. Its
worker fell from 64.25 FPS before the first detached turret to 1.01 FPS in
the last window, with a 4,340.903 ms critical-update maximum. A turret first
lost terrain support and later reached Y = -107,613; synchronous elapsed-time
catch-up then amplified the slow callbacks. At 06:23:23 the server terminated
the battle after a 5.05-second worker heartbeat timeout. The visible client
still reported 84.51 FPS in its last window. These logs establish worker
starvation and session termination, not a diagnosed native process crash.

The old fixed 1e-6 scenery-ray skin was smaller than native binary32 coordinate
resolution on sloping ground. Twelve deterministic throws with binary32 ray
endpoints/hits reproduced penetration in the prior code; all twelve retain
terrain contact with a coordinate-scaled four-ULP skin. This is numerical
query tolerance, not a new rest-height or material constant. Per-pose corner
and world-inverse-inertia caches, explicit three-component vector operations,
and conservative swept vehicle bounds remove repeated work; the bounds are
refreshed after contacts change a trajectory. The existing live destruction
filter is prepared once per body envelope, with the original query outside
that envelope. Every native hit still supplies its point and normal.

The subsequent `074651-0dd0f1dc4f20` playtest retained 73-104 visible FPS while
the hidden worker reached 10.85 FPS. A fixed 40 ms slice per callback therefore
slowed simulation time even when a body could afford more work. Updates now
consume real elapsed time within a shared 4 ms soft CPU budget, measured with
the existing profiling clock. Every body gets at least one 10 ms substep;
unconsumed time remains debt, now included in body diagnostics. The initial
proposal freezes that body directly instead of synchronously pre-casting an
entire throw that the body immediately supersedes. Query failure rolls back body pose,
momentum acknowledgements and vehicle response together. The 10 ms contact
substeps and eight solver passes remain unchanged. Regression fixtures cover
binary32 slopes, skipped far-body contact work, prepared-filter bounds,
multi-body catch-up fairness and failure retries. They do not establish
Windows native frame pacing; the next exact-client playtest must do that.

The same report includes another turret falling below terrain after a shove.
A binary32 sloping-ground fixture reproduces the unskinned translation path
pushing a resting corner into the ground. Contact recovery now uses the same
coordinate-scaled ray skin as flight; intersecting scenery normals are solved
iteratively. Visible turrets retain confirmed body samples and interpolate
COM/orientation with a monotonic adaptive cursor instead of freezing when a
40 ms extrapolation expires before the next packet. The buffer reuses vehicle
presentation delay constants, applies impact/sleep events at playback time,
and never supplies authoritative collision or a second physics simulation.

The handshake order is load-bearing. `SynchronousDetachment._onDirectTick`
runs synchronously inside `createEntity` and, while
`isTurretDetachmentConfirmationNeeded` is true, calls `transferInputs` ->
`turret.filter.transferInputAsVehicle(vehicle.filter, ...)` on the vehicle's
own never-fed `WGVehicleFilter`. The runtime therefore writes -13 and
pre-sets `_Vehicle__turretDetachmentConfirmed` -- whose only writer in #1513
is `confirmTurretDetachment`, which is that flag plus a models refresh --
before calling `onHealthChanged`; the accepted turret is created afterward.
That collapses retail's two refreshes into the one `onHealthChanged` already performs, so a
turretless assembler cannot
lose a background-load race against a turreted one for the same `exploded`
model state, and it keeps that native call from happening at all. Only
`Vehicle.__init__` and `confirmTurretDetachment` write that flag in #1513, so
nothing later in a wreck's life can put its turret back on.

The detachment itself is not presentation and is not optional. `health` is
ALL_CLIENTS in retail, so every peer -- the hidden worker included -- sees a
turretless hull, and `Vehicle.getComponents` publishes exactly that as a
collision fact: it returns `(compDescr, compMatrix, isAttached)` triples with
`isAttached = not self.isTurretDetached` for the turret and the gun, and
`Vehicle.__collideSegment` begins its loop with `if not isAttached: continue`.
This port's own component enumeration is now the same triple, and every
consumer of it -- armour collision, HE blast probes, damage-sticker encoding
and the interior-module geometry -- skips an unattached part. Without that,
an ammo-bay wreck kept a full-armour turret and gun hanging in the air above
a hull that no longer had either. A missing exploded model or a full accepted
record budget prevents a new flight proposal, while transient visual creation
failures remain retryable. None of these conditions restores armour to the
source wreck.

Live interior probes use the same LAN body and chassis matrices as exterior
armour queries. Stock `getComponents` includes transforms through the native
model/filter frame, which the LAN adapter does not drive. Reusing that frame
after accepting its attachment triples displaced AP rays and HE cones on
hydraulic vehicles. The live proxy now retains the separate chassis frame;
historical proxies keep their frozen frame, and missing geometry produces no
interior hit. Directed local/remote AP and HE tests reproduce the old mismatch.

The synchronous constructor handshake is separate from asynchronous world
entry. Exact `DetachedTurret.prerequisites` returns a `CompoundAssembler` and
the vehicle descriptor's resources; `onEnterWorld(prereqs)` installs the
assembled model. As with client-created Vehicles, a returned id can therefore
precede `BigWorld.entity(id)`. Presentation retains this pending id until it
appears, then binds the model at the elapsed point on the frozen arc. Only an
already observed entity disappearing retires its animation; a reused id never
authorizes writes to or destruction of a different entity. Closing presentation
retains pending retirement records, retries them during the existing teardown
poll, and leaves remaining prerequisite loads to battle-space retirement. It
never calls `destroyEntity` for an id the engine does not yet own. One `TURRET`
creation line and one binding line record the vehicle, entity and load delay.
Regressions reproduce the old first-frame loss with an id that becomes visible
only after loading. The reported FV4005 match's installed bytecode matched the
reviewed source, but its logs lacked these lifecycle transitions; this confirms
a reproducible adapter defect, not native Windows flight acceptance.

A late ammo-bay cause can arrive after the ordinary death edge. It now admits
one detachment from the wreck's admitted terminal pose, even though the health
signature is already terminal. The immutable actor record and native
detached-health flag suppress duplicate throws and repeated wreck refreshes. Detachment
updates `appearance.damageState` with the special health, crew state and water
state even if the flying entity cannot be created, then calls
`Vehicle.confirmTurretDetachment` for its single
model refresh. Exact #1513 `CompoundAppearance.onVehicleHealthChanged` also
calls the input-handler death hook and `processVehicleDeath`; the late path
must not call it or replay `Vehicle.onHealthChanged`, kill credit or death
feedback. A failed visual creation leaves the wreck detached and retries
through the presentation owner. The original reported match did not log
terminal-cause ordering, so this repairs a reproduced ordering gap without
claiming that match's root cause is established.

The worker's arc uses the same per-column broken-skin filter as motion
probes, so a turret does not rest on an already destroyed fence skin. The
worker keeps descriptor collision geometry without loading a visual compound.
Regressions cover attachment exclusion, canonical replay, final-death delivery,
late visibility, asynchronous retirement, separate component hit tests,
continuous motion blocking and human/Bot projectile parity.

The ABI audit pins all of it against `scripts.pkg`: the 22 `DetachedTurret`
signatures, `Vehicle.confirmTurretDetachment`, both special health constants,
the three `AMMOBAY_DESTRUCTION_MODE` values and the effect's energy window.
None of that proves the arc looks right, that the two compounds swap without
a visible seam, or that the turret rests convincingly -- that is Windows
acceptance.

WG's [Update 9.0 notes](https://worldoftanks.com/en/content/docs/release_notes/90-update-notes/)
establish the intended turret-detachment feature. Its later
[Object 277 explanation](https://worldoftanks.com/en/news/general-news/3-soviet-tanks-get-adjustments/)
also confirms that changing internal module geometry changes ammo-rack
exposure; those later vehicle values are not imported into #1513.

The resource inventory and CPython 2.7 bytecode audit can run on an isolated
`scripts.pkg`. That is only resource/contract evidence. The investigation did
not have a complete Chinese HD installation passing `inspect_client.py`, all
vehicle collision assets, a retail server model, or Windows gameplay evidence.
The repaired marker still needs exact #1513 rendering acceptance; the missing
layouts remain an explicit product gap, and the detached turret's launch
impulse remains an uncalibrated product number.

Track damage follows the detailed model Update 6.4 introduced. A track
material's live `damageKind` selects the shell damage channel:
`common/vehicle.xml` ships both tracks as `damageKind=auto`, and
`vehicles.py::_readArmor()` resolves `auto` to armour damage whenever the
material armour is nonzero. A
direct solid hit on the leading or rearmost configured driving wheel takes the
full roll; the ordinary middle run divides it by the target chassis'
`bulkHealthFactor`. The zone is classified from the chassis-local contact of
the exact collision the shot resolved against, carried privately beside the
retail four-field collision value so the stock gun-marker and `ProjectileMover`
consumers keep their exact ABI. Because the client exposes only driving-wheel
NAMES and radii and never their node positions, the two configured wheel sizes
from `chassis.drivingWheelsSizes` are anchored to the two ends of
`chassis.topRightCarryingPoint`; that is this product's documented 0.9.x
endpoint convention, not a recovered native equation. `_readChassis()` reads
`bulkHealthFactor` only when `not IS_CLIENT and not IS_BOT`, so the value is
resolved once per vehicle/chassis identity from the client's own raw
`scripts/item_defs/vehicles/<nation>/<name>.xml` section through `ResMgr` and
cached. That raw read is unproved on #1513: if it fails, the one middle track
hit keeps the previous device-damage result and writes one bounded diagnostic
instead of guessing a divisor. HE direct hits and splash stay on the previous
device-damage law, because no reviewed evidence covers the wheel zones for a
blast. Both shipped `usa:A107_T1_HMC` chassis are shorter than their combined
configured endpoint zones. Those two widths are reduced by the same factor to
fill the carrying span, preserving their front/rear ratio and leaving no
ordinary middle run instead of returning that valid geometry to the previous
device-damage law. This proportional split remains part of the documented
product convention, not a recovered native server equation.

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
selection and gun checkpoint were committed, and it remains what an owned
launch, pose sample, contact receipt and landing observation bind to. A
per-sequence record keeps a bounded fingerprint plus the typed outcome, so an
exact retry folds, a changed payload at the same sequence conflicts, a future
gap consumes nothing, and an evicted sequence can never become new state. A
recoverable validation failure records a rejected decision and advances only
the processed frontier: no field is applied and no gun checkpoint is
installed, so an owned launch bound to that sequence receives one typed
terminal rejection rather than being admitted against stale input. A message that
does not identify the current player, round or an exact sequence consumes no
frontier at all. Both frontiers retire together on a round transition, and
the snapshot publishes them so a reconnecting client resumes at the next
eligible sequence. The shipping client canonicalizes the same envelope before
queueing a frame, normalizing periodic yaw instead of clipping it, so normal
#1513 values never produce an avoidable rejection.

`player_projectile_owner_v1` assigns each human projectile to its firing
visible client and each Bot projectile to the mandatory native worker. The
human owner freezes the mounted shell, source vehicle, muzzle, scattered
velocity, penetration roll, range origin, burst identity, input sequence and
estimated server launch time once. It preinstalls the local manager entry
before publishing the launch and advances from the actual local trigger
clock. Target history is sampled from the already displayed native poses;
there is no second worker rewind or presentation ledger. Idle visible clients
do not build projectile target history, and visible clients never simulate
Bot shots or another player's shots.

The LAN server checks owner, round, increasing shot sequence and the accepted
input, then records the launch. Human progress, ricochet and terminal proposals
carry the immutable launch proof, rounded by the shared six-decimal
integer-rational half-even rule. A conflicting proof or another player's
message has no health, critical-state or destruction effects. Human progress
uses the last successfully queued cursor as its next base; reliable FIFO
preserves launch, progress, ricochet and terminal order without an echo wait.
A ricochet starts its second local segment as soon as its frozen transition
has been queued. Bot progress retains its existing acknowledgement path.

A matching launch echo confirms metadata without replacing the local manager
or changing gun state. Stale first-segment echoes cannot rewind a local
ricochet; malformed or conflicting own-shot snapshots are contained to that
projectile. Each accepted launch ends with a canonical terminal or an explicit
local rejection. Delayed rejection retires that projectile while preserving
the already fired round's ammunition and reload. Disconnect or leave expires
only that player's outstanding ledger entries; no worker or peer takes them
over. Destructible results are computed by the shot owner, and the server
deduplicates their native identities together with terminal damage before
broadcasting shared results.

These owner, FIFO, duplicate, rejection, displayed-pose and burst contracts are
covered by local runtime and protocol tests. They do not establish #1513
Windows frame pacing, firing feel, native memory safety or two-client LAN
acceptance; those require the exact client with this payload.

`PlayerAvatar.showOwnVehicleHitDirection(hitDirYaw, attackerID, damage, crits,
isBlocked, isShellHE, damagedID)` is the only producer of the damage
indicator, and `gui/battle_control/hit_data.pyc` keeps `damage`, `IS_BLOCKED`
and `IS_HIGH_EXPLOSIVE` as independent fields. In
`gui/Scaleform/daapi/view/battle/shared/indicators.pyc`,
`_MarkerData.__getMarkerType` reads `HitData.isBlocked()` before anything
else. Its blocked branch is numeric: `_ExtendedMarkerVOBuilder` prints
`str(HitData.getDamage())` as the label and selects
`DAMAGEINDICATOR.BLOCKED_SMALL`/`BLOCKED_MEDIUM`/`BLOCKED_BIG` from
`damage / playerVehMaxHP`. Every other zero-damage hit falls through to
`CRITICAL_DAMAGE`, whose three sizes all map to the single `CRIT` frame and
whose `_getDamageLabel` is the empty string below two criticals. The exact
client therefore draws either a blocked marker carrying a real value or an
unlabelled critical marker; a blocked marker worth `0` is unreachable, so
`isBlocked` must mean a shell this vehicle's armour stopped rather than
merely a hit that removed no hit points. `HitData.__buildFlags` sets
`HP_DAMAGE` from `damage > 0` alone, so once a blocked marker carries a value
`HitDirectionController.__findHit` matches an earlier marker from the same
attacker and `HitData.extend` sums the two -- retail's own aggregation, and
also what makes a blocked hit visible under the `WITHOUT_CRITS` preset that
`_isValidHit` uses to drop zero-damage criticals. `isShellHE` reaches
`HitData` but no #1513 view reads it.

The damage log panel's blocked rows and its running total come from one
number, the `TANKING` battle event, whose totals
`PersonalEfficiencyController._onPlayerFeedbackReceived` accumulates
client-side from `Avatar.onBattleEvents`; `_BET.ARMOR` maps the same event to
the `BATTLE_EVENTS.BLOCKED_DAMAGE` ribbon.

`res/text/LC_MESSAGES/battle_results.mo` states the ledger rule the client
itself shows. `EfficiencyTooltipData` binds `BATTLE_EFFICIENCY_TYPES.ARMOR` to
`ArmorItemPacker` (`gui/shared/tooltips/efficiency.pyc`), whose header is
`common/tooltip/armor/header` (装甲抵挡) and whose description is
`common/tooltip/armor/description`: "计数: / • 跳弹 / • 未击穿 / HE与HESH炮弹不
包含在内。" -- the counter takes ricochets and non-penetrations, and HE and
HESH shells are not included. #1513 has a single `HIGH_EXPLOSIVE` kind for
both, and `combat_rules.is_he` already reads exactly that kind, so `HEAT`
(`HOLLOW_CHARGE`) and `APHE` (`ARMOR_PIERCING_HE`) keep their blocked credit
even though `ingame_gui.mo` abbreviates `ARMOR_PIERCING_HE` and
`HIGH_EXPLOSIVE` to the same `damageLog/shellType` label, `HE`. The battle
server owns the ledger but holds no descriptors, so the worker publishes the
shell fact beside `structural_armor_hit` and the server applies the rule.
`IS_HIGH_EXPLOSIVE` does reach `HitData`, but no #1513 view reads it, so
`isBlocked` is the only place the same rule can be expressed on the indicator.

The remaining choice is the blocked value itself, which no reviewed file
fixes: the cell app that packs retail's `damage` argument and blocked ledger
is not in the package. This port reports the shell's published
`shell.damage[0]`, because a shell that never pierced never drew a damage
roll; a penetration keeps the roll it actually spent. Splash is excluded from
both surfaces -- its damage falls off with distance before armour absorbs the
rest -- so an absorbed near miss stays an unlabelled critical marker and
credits nothing. The separate `potentialDamageReceived` column carries no such
exclusion in any reviewed text, so it still accumulates every direct hit; that
asymmetry is the client's rule, not a derived identity.

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
server derives its standard-mode capture law from the retired predecessor: a
50-metre radius, one update per second, at most three capture points per
update, defender stop, empty-base reset and victory at 100 points. Standard
battles end by
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
spawn-congestion/OBB, reverse-steering and baked-route changes proven in the
retired predecessor are retained only as algorithm-derived changes;
real-client acceptance still has to check them against #1513 terrain and
presentation timing.

## Hidden-worker authority

Every 0.9.22 LAN room has one mandatory, room-owned hidden native worker.
The worker owns Bot motion and Bot projectiles. Each visible player owns the
projectiles they fire, including native collision and destructible proposals.
The LAN server validates ownership and frozen launch identity, commits shared
outcomes once, and broadcasts them to every replica.

The launcher starts the server first, then the hidden worker, and advertises the
room only after both are ready. A missing worker refuses battle start. A worker
loss ends the active round as a technical failure without battle receipts and
leaves the room unavailable until the owner stops and starts a new room. The
server retains shared roster, timing, hit, receipt and result-ledger admission.

Native BigWorld worker code owns Bot movement, map collision, Bot projectile
progress, water sensing and Bot native critical-state proposals. The server keeps the
ten-second drowning timer, then validates and commits the worker proposal; it
does not reconstruct vehicle descriptors, map collision, destructible identities
or a BigWorld-equivalent simulation.

The former pure-Python server authority, baked world, descriptor-projection
donation and destructible-map donation paths have been removed. The remaining
vehicle catalog is waiting-room metadata for vehicle tiers and does not provide
combat descriptors.

## Offline progression and account settlement

The garage ledger owns spendable vehicle XP, free XP, credits, gold, research,
crew descriptors and item stock. Battle receipt application persists these
changes with an idempotence marker before publishing the Account update.

The native transaction validators also consume account availability fields.
`vehicleSellsLeft` reflects the number of owned vehicles that may be sold while
retaining the final garage vehicle, and changes with purchases and sales.
`freeTMenLeft` and `freeVehiclesLeft` permit the existing free recruitment and
zero-price vehicle purchases; neither has a daily quota offline. Native
capacity and money validators still apply. Zero in these fields means an
operation is forbidden, not unlimited.

- Ordinary battle XP goes both to the vehicle and independently to every
  seated crew member. Accelerated training is opt-in and requires the current
  vehicle research tree to be elite; only then does the vehicle award become
  extra XP for the least experienced crew member. Empty seats earn nothing
  and cannot prevent the remaining earnings from settling. Lifetime dossier
  XP counts the battle award even when accelerated training spends it.
- Crew advancement uses `TankmanDescr.addXP`. Mentor uses the pinned
  `tankmen.commanderTutorXpBonusFactorForCrew(crew, ammo)` factor, evaluated
  before XP advances skills or service consumes equipment, and applies only
  to other crew members. Qualification percentages are integer levels: the
  shipped level-cost function requires 500 XP for 50% to become 51%.
  `VEH_FULL_RESULTS.xpByTmen` carries the actual award per current crew ID
  for `VehicleProgressHelper` to detect a newly available skill. These IDs
  stay in session memory because garage inventory IDs are rebuilt on restart.
- Lifetime vehicle records persist battles, wins, losses, draws, survival,
  damage, assistance, spotting, shots, hits, penetration, capture, XP and
  single-battle maxima independently of vehicle ownership. Account statistics
  sum those records. The native `a15x15Cut` maps each vehicle type to
  `(battlesCount, wins, xp)`; `a15x15`, `a15x15_2` and `max15x15` supply the
  stock averages and best-vehicle labels. Dossier cache schema changes request
  a full refresh even when the old cache's battle watermark is unchanged.
  Previously discarded results cannot reconstruct an unknown historical
  maximum.
- `DossierCache` persists those vehicle rows between sessions. It names its
  `.dat` file `b32encode('%s;%s;%s' % (BigWorld.server(), accountName,
  accountClassName))` and uses `accountName` for nothing else; `__readCache`
  restores `__maxChangeTime` as the highest `changeTime` in that file, and
  `__sendSyncRequest` then asks `CMD_SYNC_DOSSIERS` only for rows newer than
  it. Nothing else ever lowers that watermark: a version mismatch in
  `__onSyncComplete` clears the cached rows but leaves it standing. All three
  key parts are constant offline, so every save slot and both processes would
  share one file, and a career whose battle ordinal sits below another's
  watermark would receive no vehicle row at all while its battles kept
  settling. `compat.pin_dossier_cache` therefore scopes `accountName` by a
  fixed-width digest of the save slot, process role and complete post-battle
  account key before any `PlayerAccount` exists. Hashing the complete identity
  preserves separate career namespaces without letting a valid 64-character
  slot push the stock base32 cache path beyond Windows `MAX_PATH` under the
  normal preferences directory. The account dossier is unaffected:
  `account_rpc/server.py` pushes it in the post-battle diff rather than through
  this cache. Receipts now carry HE explosion hit counts, the existing sniper
  damage ledger, friendly hits/damage/kills, travelled metres and time alive.
  Mileage sums accepted world-pose segments, including reverse travel, while
  excluding spawn placement, replicated wreck motion and source-clock rebases.
  It has the spatial resolution of those admitted samples. Time alive freezes
  at the first canonical death/leave tick and excludes the countdown; it does
  not grow while the player watches the rest of the battle. Stunning-vehicle
  eligibility remains unmeasured.
- Selling and rebuying a vehicle preserves its XP, including across restart.
  Elite vehicles with stored XP remain conversion candidates after sale.
  Conversion counts each selected vehicle once. Researching a vehicle also
  unlocks its `autounlockedItems` without requiring a purchase.
- Selling follows `Shop.getSellPrice`: the published sell-for-gold set is
  empty, so gold-priced vehicles and items refund credits. Each unit rounds
  up before the stack count is applied. `Vehicle._calcSellPrice` replaces the
  stock-module values with the installed-module values; those installed
  copies leave with the hull. Keeping a complex device charges its published
  dismantling price. The whole sale rolls back if any component fails.
- Buy-and-install preserves `Shop.buyAndEquipItem`'s `isPaidRemoval` field.
  A failed native install restores the old fitting, item stock and currencies.
  Automatic repair and resupply use the saved layout currency and publish
  their actual credit/gold debits in the native battle-results cost fields.
  Those costs share the durable receipt marker so a retry neither charges
  twice nor loses the original cost display.
- The durable item ledger counts all owned copies; the native inventory
  snapshot and changes expose only unmounted copies. The exact
  `VehicleLayoutProcessor` subtracts both warehouse stock and the current
  vehicle load when calculating a purchase, so publishing the owned total
  would underprice resupply and advertise an installed item as a spare.
- Incremental Account updates publish current balances, changed inventory,
  crew and XP before command completion; growing unlock/elite sets carry only
  additions, matching `Stats.synchronize` and avoiding repeated notifications.
- Crew placement checks nation and primary role, while preserving the original
  training specialization across vehicle transfers and save restoration.
  Moving a seated crew member records the source seat in `lastCrew` so the
  stock return-crew action can find them again.
- In #1513, `ItemsCache.__invalidateData.cbWrapper` invokes `onSyncCompleted`
  before the adisp completion callback. An exception in that event can strand
  a waiting generator. Account publication exceptions now return command
  failure; the cache-refresh fallback also has a ten-second failure deadline.
  Late callbacks and callbacks from a replaced account cannot report success
  or refresh the new account's views. Accepted inventory mutations remain
  saved when presentation fails; the failure does not claim a successful
  refresh or roll back a potentially published change.

This is a functional offline progression loop, not the proprietary retail
server economy. Reward coefficients and premium-vehicle credit bonuses remain
explicit offline policy. Premium-account time, first-win/calendar bonuses,
personal reserves, missions, rentals and the retail restore-vehicle service
are not implemented. Customization ownership and outfits persist, but their
catalogue remains free; it is not a reconstruction of the retail cosmetic
store. Crew-school and skill-reset costs/losses are the published offline
policy, not evidence of the historical server tables. The fixes above have
logic, persistence and exact-bytecode contract tests; native Windows UI and
battle acceptance must still exercise the resulting package.

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
  argument into the normal `_update` event path. A gun swap carries the new gun's default
  layout with an empty rack; the player buys the rounds through resupply;
- purchases are implemented: `CMD_BUY_ITEM` 302 carries
  `(cacheRev, intCompactDescr, count, goldForCredits)` and
  `CMD_BUY_AND_EQUIP_ITEM` 308 carries
  `[cacheRev, compDescr, vehInvID, slotIdx, isPaidRemoval, gunCompDescr]`;
  `CMD_VEH_SETTINGS` 107 is the per-vehicle settings mask, not a purchase.
  Purchases debit the saved account ledger and publish changed balances with
  the inventory before acknowledging the command. For maintenance command 108,
  the exact `account_shared.LayoutIterator` returns
  `(abs(compDescr), count, compDescr < 0)`. `VehicleLayoutProcessor` selects
  `buyPrices.itemAltPrice` for a negative descriptor and `itemPrice` otherwise.
  The adapter preserves that sign in saved layouts and automatic resupply,
  while loaded item IDs remain positive. A combined ammunition/consumable
  purchase commits both parts or rolls back both parts;
- the garage now persists to `mods/configs/offline_lan_0922/garage_state.json`,
  a sibling of `account_state.json` so each file keeps one owner. It stores
  mounted devices and modules through the vehicle's compact descriptor, plus
  consumables, shells, layouts, settings, learned crew skills and owned stock.
  Records are keyed on `vehicleTypeCompactDescr` and on the crew slot index,
  never on inventory ids, because those are renumbered whenever the vehicle in
  `config.json` changes. The file is never shipped in the overlay, is written
  atomically after each accepted change, and any unreadable or wrong-schema
  content logs one line and falls back to the stock garage;
- the sandbox owns and unlocks its module catalogue. Career saves instead
  research and purchase modules through the live vehicle tree; installed
  copies and depot spares are counted separately;
- the battle uses the garage loadout: the player's shells come from the mounted
  layout mapped onto the gun's shot order, and the consumables come from the
  mounted slots, so an empty slot carries nothing. Bots keep a synthetic
  loadout by design;
- `PlayerAvatar.__startVehicleVisual` calls `EquipmentsController.clear(False)`
  for the own vehicle. Loading snapshots must retain their equipment state
  without publishing or caching HUD echoes until native client readiness,
  after that clear. The normal item-transition deduplication then remains
  necessary to preserve the expanded repair/medical selector. Garage inventory
  updates publish the room loadout after the asynchronous stock cache refresh;
  starting a round checks that refresh has finished and queues the current
  loadout before the start request;
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
  (`invisibilityDeltas['camouflageBonus']`) is included in the client's own
  `computeBaseInvisibility` pair and keeps the exact GUI consumer's shot
  factor, while `invisibilityDeltas` `firePenalty` and radio-range gating of
  the team's shared intelligence are still not applied;
- the server publishes terminal winner/reason/base team plus live frags and the
  human team-killer flag, but not the retired predecessor's complete
  `personal`/`players`/`vehicles` battle-result record;
- the post-0.8.3 follow-up wires stun generation, penalties and the existing
  medical-kit loop, subject to the retail-parity boundaries documented above.
  Bot movement and Bot projectile trajectories run in the mandatory hidden
  native worker. Each human client originates its own input, pose and gun-state
  checkpoint, and simulates its own projectile trajectories; the LAN server
  admits both owners' ordered results and shared ledgers. This is a trusted-LAN architecture, not an anti-cheat design or
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

## Mod display language

The launcher passes its resolved `en`/`zh` selection as
`WOT_OFFLINE_UI_LANGUAGE` to the visible game starter, whose child inherits the
process environment. It is presentation state only: map geometry keys, team
requests, endpoint syntax, persisted choices and LAN messages do not change.
Mod-owned waiting-room labels and lobby notifications use a Python 2 Unicode
catalog; UTF-8 client map names and player names are decoded before formatting.
In particular, the waiting-room refresh no longer calls Python 2 `str()` on
Unicode roster text. Stock Scaleform UI retains the client's language.

The waiting room uses the packaged `system/fonts/offline_lan_cjk.font` in both
languages so Chinese player names also work with English labels. It requests
Windows Microsoft YaHei at size 16 without a pre-generated Latin-only atlas.
The [BigWorld Client Programming Guide, Fonts](https://howarduong.github.io/github.io/doc/html/client_programming_guide/ch18.html)
documents dynamic glyph caching, installed TrueType fonts and CJK font support.
This is engine documentation, not proof of #1513 native rendering. No font
binary is redistributed. The font is assigned before text; load errors reach
the existing session fallback to the stock training window.

Local regression coverage checks launcher environment propagation, translated
host/guest labels, Unicode names, map wire identity, denial notifications and
catalog formatting. The available loose client files did not pass
`tools/inspect_client.py` (missing version.xml, paths.xml and the client package
layout); they are not a newly verified complete #1513 resource set. Exact
Windows #1513 acceptance must still check font availability, Chinese glyphs,
notification rendering and panel clipping at supported resolutions, in both
launcher languages. A Chinese stock garage alone does not validate GUI.Text.

Human/Bot contact momentum is separate from the armour-damage receipt ledger.
The visible integrator reports cumulative opposite momentum from its own
mass-weighted contact, and the worker divides only the unseen increment by
the canonical Bot mass. Its acknowledgement travels atomically with Bot
road speed and residual push, including combat-containment and takeover paths.
The visible contact solver includes only the unacknowledged momentum in its
peer velocity and does not differentiate positional separation into kinetic
velocity. Input/snapshot coalescing therefore preserves physical impulses
without replaying them or waiting for a native armour plate. The ram HP formula remains unchanged. Static contact first tests the
receiver's own track force budget before assigning positional mobility;
engine power enters through the existing longitudinal integrator's incoming
speed. Both owners bleed residual velocity before displacement using the
same descriptor-derived longitudinal/lateral track laws. Bot driving intent
comes from the existing movement_dir field, including zero-speed throttle.
Hull contact uses the audited CONTACT_FRICTION_VEHICLES value 0.3, and an
unordered-pair worker sweep avoids cancelling frozen momentum once for every
neighbour. Stock Avatar collision presentation receives forwarding views with
the integrated velocities because the unfed native filters report zero; this
presentation call never fabricates an armour proof. These contracts
have pure-data coverage; native collision feel and turret contact timing still
require acceptance on the exact Windows client.

Crowded spawn regression now observes one minute instead of thirty seconds:
finite side grip and hull friction reduce queue throughput. It retains every
vehicle's departure requirement and the existing parking/recovery episode
bounds. Final Himmelsdorf occupancy is checked against the actual initial hull
OBB rather than an eight-metre circle. The driver permits a straight reverse
out of an existing front/side overlap while preserving new rear blockers;
contact jitter cannot indefinitely renew the brief waiting lease. Navigation
and head-on coordination use that bounded lease too. The short-target check
still forbids avoidance steering beside a parallel hull; a hull across the
approach may require a detour, but the Bot must reach the same target and stop.
These are copied-driver checks, not a claim that Windows crowd timing matches
retail.

The same report showed that adding linear contact friction did not constrain
kinematic traverse: a hull could rotate into its neighbour, then use overlap
separation to move that neighbour without spending drive force. Both the
visible player and worker Bot now sweep the commanded angle to the first
legal OBB contact and clear blocked turn speed. Corner sampling is bounded by
half the existing penetration slop and the first blocked interval is refined;
testing only the final angle would miss an intermediate collision. Existing
overlap may decrease, and translating clear immediately releases the turn.
No player/Bot identity changes this rule. Blocked yaw now retains its motor
command and spends a traction/power-limited track couple at the occupied
corner. Planar box inertia limits that impulse; the existing track resistance
can hold it, or both real masses receive the reciprocal response. Player/Bot
momentum uses the existing acknowledged transport, without a second worker
impulse for the same human contact. Driving consumes engine power and track
traction before the remaining budget can load a turn. This is a constrained
planar chassis approximation, not recovered retail cell angular dynamics.
Conservative SAT interval bounds prune clear portions of recovery arcs while
retaining the previous corner spacing and first-contact refinement.

Rotation blockage feeds the existing bounded traffic lease. Recovery checks
live hull rotation as well as scenery, keeping a clear reverse straight when
its turn arc is occupied; a clear forward sweep can release a tank whose rear
and both pivots are blocked. It cannot create clearance through a free pivot.
The friendly head-on coordinator also checks swept hull poses. If both lateral
exits are denied, the original 1.5-second hold is followed by at most one
6-second backing attempt: the higher-ID Bot reverses with terrain and full
rear-hull checks, while the other advances straight. This is a navigation
choice using the existing 0.72 recovery throttle, not extra physical force.
Backing continues across velocity sign changes, releases on separation or an
explicit hold, and cannot renew its deadline without a new physical encounter.
It resolves the Great Wall gate fixture without changing its 30-second exit
requirement or 5-second blocked/broadside limits.
Both adapter turn signs, repeated side hugs, corner escape and intermediate
angle collisions have regression coverage. Existing crowded-departure gates
are retained without another extension or lowered departure requirement.
Hostile hull contact overrides a tactical firing hold while preserving target
and fire intent: the driver continues forward/reverse recovery, and limited
gun traverse cannot reset that escape to throttle zero. Tangential scraping
along an existing face is permitted when centre distance initially stays
constant; an offset contact prefers the nearer end. Other rear blockers and
world hazards still veto. Supplied neighbour positions retain their tuple or
XYZ wire coordinates in the traffic snapshot instead of defaulting to origin.
Continuous stacked-hull/debris crushing HP remains unimplemented. See the
2026-09-20 release-pause investigation below for the requested state rules and
the missing retail calibration evidence.

## Gameplay follow-up: 2026-09-14

- The battle HUD's ping slot displays the hidden worker's mean render-frame
  interval: 1000 divided by FPS over the recent one-second sample window.
  Frames are observed at the existing zero-delay battle callback, independently
  of diagnostic logging. A correlated worker reply carries this millisecond
  value. Transport RTT remains a separate measurement, and a missing/stale
  reply never turns an unresponsive worker into a low-latency reading.
- SPG strategic arc keys include hull pose but exclude the aiming turret and
  barrel angles. Pending arc refreshes retain the last same-target elevation
  only for aiming continuity; they cannot authorize fire. Final native-muzzle
  and dispersed-path checks still gate each shot. Integration coverage runs the
  actual Bot update, gun slew and both bounded queues through one launch.
- Limited turrets slew through the installed legal interval instead of wrapping
  across a forbidden rear gap. A stationary limited-traverse firing hold owns
  hull steering once the target enters the arc; navigation and contact recovery
  retain ownership while travelling or escaping. Asymmetric and fixed arcs use
  their actual interval midpoint rather than assuming zero is reachable.
- Version-9 destructible catalogs prove native transforms during the first
  bounded category scan, before compacted-name mismatches can quarantine a
  shifted group. Every remapped item still needs a unique full transform and
  compatible native category. No scalar native filename query is introduced.
- Shell speed uses the stock tooltip's parameter font spans, line separator
  and native number formatter. The supported GunShot attribute contract and
  optional-enrichment exception boundary remain intact.

These tests prove source logic and queue/adapter contracts. Exact Windows
#1513 rendering, native destruction, frame pacing and combat remain gameplay
acceptance boundaries.

### 2026-09-14 repeated gameplay report and reference ammo screenshots

The next follow-up maps the accepted distinct enemy-damage set to native
`damaged` in both personal and public vehicle results, alongside `kills`.
Primary non-penetrating HE damage now contributes to explosion-hit statistics;
the protocol's secondary-target `splash` flag is not the complete blast taxonomy.
The launch ledger's admitted HE identity owns this classification. Direct-hit
counts, accepted HP, replay idempotence and existing secondary-hit semantics
remain independent.

The four user-provided current-client screenshots define presentation only.
Damage, both installed GunShot penetration endpoints, muzzle velocity and HE
radius come from this #1513 vehicle's mounted ammunition. Chinese labels,
inline units, comma-separated integer speed and distance notes follow those
references. Stock extra rows, including HE stun duration, remain present.
Native GunShot access remains attribute-only; NoLegacyStuff is not bypassed.

Strategic artillery family work retains its original anchor while source
settling remains within 5 cm and 0.001 radians, matching the existing Bot
intent motion envelope. Cumulative movement is measured from that retained
anchor. This advisory cache cannot authorize fire: the exact final native
muzzle, dispersed angles and every trajectory chord still receive their own
unchanged proof. Tests now include settling while the high arc is queued and a
real server-planner/local-driver path with a pose-dependent barrel endpoint.

The uploaded report ZIP could not be inspected because this session's runtime
could not open attachments. The four latest PNGs are visible directly in the
conversation. Engine-free regressions and CI packaging are not native Windows
battle validation; live collision and remaining hydraulic/limited-turret
behavior require further evidence.

Limited-traverse Bot hull aiming now evaluates the gun direction in the same
stabilized pitch/roll basis as gun slew, reusing a matching ballistic solution
for lead and elevation. A flat compass bearing alone can lie inside the yaw
interval while the physical gun remains beyond its stop on a cross slope.
Normal route/recovery ownership and actual gun limits remain intact.

Tank contact height culling projects the oriented descriptor body onto world Y.
The previous unrotated interval could reject a nose-to-hull overlap on a slope
before the contact solver ran. Local candidate culling and both contact solver
owners consume pitch/roll; native armor proof still owns any ram HP. Remote
human contact bodies also use the interpolated presentation pose, just as Bots
already did, without rewriting the accepted network state. Regression fixtures
check a point contained in both tilted bodies, an overhead non-contact, all
eight vertical corners, and the local presentation-to-contact boundary.

The SPG hull controller also retains the same last admitted world aim as the
gun while its strategic refresh is pending. Reverting just the hull to a
direct compass ray on a cross slope can turn against a high-arc gun, repeatedly
invalidate the family job and cause visible oscillation. The retained aim is
signature-bound and cannot supply a missing launch proof. The regression
includes a low-path wall, a cross slope, a limited gun and real server/local
planning through the high-arc launch.

The real high-arc test also exposed a cadence mismatch: strategic successes
expired after 0.35 seconds while the Bot lane owner polls on a one-second
tactical cycle. A completion could repeatedly disappear before any observation
used it, leaving the server without a shootable target. Strategic success
retention is now 2.5 seconds (two polling cycles plus callback margin); source
anchor invalidation and current-family re-leading remain active, and final
native trajectory proof is unchanged. The prior expiry regression now checks
that a result survives the next tactical poll and still expires thereafter.

A further complete-cycle regression showed that the two-second generic target
lease could drop an acquired SPG contact while chassis alignment invalidated
and requeued its high arc. SPGs now retain an already acquired, still visible
and in-range aim target while no replacement firing lane exists. This does not
acquire unseen/never-shootable contacts, grant fire, or consume a focus slot.
A shootable alternative, loss of visibility/range or an unrecoverable weapon
still releases the hold. This retention is confined to artillery's existing
rear-anchor behavior; ordinary tank route leases are unchanged.


## Artillery feedback, Expert, directives and bonds

Direct SPG hits with positive HP damage now use the direct-projectile
penetration sound flags. A direct zero-HP hit uses non-penetration flags;
only a splash event uses external-explosion flags. The physical shot result,
decal and statistics remain unchanged. Module damage is still published in
critical feedback; its alternative voice does not replace this SPG rule.

Expert's four-second lock now publishes to the stock shared feedback adapter.
The Avatar wrapper rechecks `BigWorld.target()` against a stock Vehicle, which
cannot accept the synthetic entity selected by the offline outline ray.
The offline path retains target identity, visibility and alive guards, hides
old module icons on a target change, and revokes the view when the commander
loses the active perk. Destroyed crew extras join the module list.

The regular three consumable slots and the fourth directive slot have separate
layout and auto-resupply handling. A directive travels in the frozen effective
parameters, and its compact descriptor is retained in the round participant
receipt so later garage edits cannot change which directive that battle used.
Settlement, the wallet update and its receipt marker share the existing durable
transaction. Old three-slot records and wallets without crystal remain readable.

Equipment directives enter the native attribute-factor chain. Crew directives
enter `VehicleDescrCrew.boostSkillBy`; the battle projection also grants the
completed discrete perk where required. Native processors which the garage
leaves empty for Smooth Ride, Snap Shot and Safe Stowage now have offline
consumers using the native level, eligibility and skill config arguments.
The modified timing for trained Sixth Sense, Designated Target and Last Effort
is donated separately; an untrained perk receives its ordinary completed effect.
The improved stabilizer uses the descriptor's actual additive dispersion
factor, and its directive applies the native additional factor.

Adrenaline Rush (`loader_desperado`), Preventative Maintenance
(`driver_tidyPerson`) and Armorer (`gunner_gunsmith`) now also use native
skill-config values. Each physical crew/fire mask carries its own conditional
factors in the immutable round snapshot. Adrenaline Rush applies below the
descriptor's HP fraction and rescales only the remaining reload progress;
Armorer changes only the damaged gun's dispersion factor. The authority's
engine-fire proposal combines the target's live crew row with consumable
passives. Crew injury and recovery select another donated row. Old snapshots
without this optional extension remain readable; incomplete or nonfinite
extensions are rejected.

This audit does not establish complete crew-skill parity. Relaying
(`radioman_retransmitter`) still has no battle consumer. The current team-wide
spotting broadcast also does not model radio contact distance, so Signal
Boosting (`radioman_inventor`) is numeric-only in this respect. Existing
numeric, spotting, repair, XP and discrete-perk tests do not prove untested
skill behavior.

Bond currency survives catalogue parsing, account shop/stats updates, garage
persistence, launcher balance editing, battle receipts, medal replay details,
consumption and service costs. The 21 affected catalogue entries retain their
existing baked amounts and gain the omitted crystal marker; this is a targeted
currency correction, not a claim of rebaking the entire #1513 client. The
baker now preserves `<crystal/>` when run again. The medal schedule comes from
WG's [9.20.1 announcement](https://worldoftanks.eu/en/news/general-news/920-1-bonds-and-medals/)
and its [reward table](https://eu-wotp.wgcdn.co/dcont/fb/image/medals_en.jpg).
The all-Tier-X base-XP bond conversion is not reconstructed. No guessed
XP-to-bonds formula or later weekly-cap system is introduced.

Grand Battles remain unavailable. The current 15-slot team layout, bot identity
ranges, 30-row result validators and baked map data need a coordinated change.
The [historical Grand Battle rules](https://worldoftanks.eu/en/news/general-news/update-920-grand-battles/)
require 30 Tier-X vehicles per side, three matched spawn groups, a 15-minute
limit and at most four SPGs per team. The exact #1513 large-map navigation,
spawn and native 60-vehicle acceptance data are not available in this workspace.

Static reference for the newly connected directive and feedback consumers was
also compared with public 0.9.22.0.1 #788 scripts (StranikS-Scan's decompiled
archive, commit 487396ac2bec27127b8e03abca33bc961dc67021). This different regional
build is orientation, not #1513 ABI proof. Only a run of the resulting package
on the supported China HD #1513 Windows client can establish native voice,
Expert overlay, fourth-slot UI, timing and frame-pacing acceptance.

## September 17 test-report follow-up

The three supplied reports identify version 0.8.4 with build identity
`colorfulmeans-35196027324-1`. Ruinberg reports include live but unidentified
placements, a 52-name/84-slot disagreement, and catalog signature misses.
These are distinct outcomes; the logs do not prove that every reported object
has one cause. Moving actors no longer preempt the shared bounded name scan.
Reviewed chunk-load/loss hooks retire that chunk's quarantine when its native
lifetime ends. Ordinary cache invalidation still preserves quarantine, and
ambiguous identities still cannot authorize destruction. Regression coverage
includes a large name scan under competing actor requests and chunk reload.

Expert now has one owner for the offline four-second focus state. Enabling the
stock target monitor previously allowed its target-blur callback to clear the
shared overlay despite continued offline silhouette focus. Its native monitor
is disabled while the existing visibility/alive/perk guards remain. Pivot RPM
uses the same descriptor-derived left/right track velocities as animation,
instead of only center-of-hull speed. Audible turning and sustained Expert
display still require Windows gameplay evidence.

The bond vehicle shop did not exist in 0.9.22. WG's
[October 28, 2019 announcement](https://worldoftanks.com/en/news/specials/tanks-for-bonds/)
provides the first assortment, prices, included slot and trained crew. Eight
definitions exist in the old client and are offered with those prices; the
two later vehicles are omitted. The five explicitly requested retired
definitions use offline tier brackets, not their residual tech-tree/placeholder
prices. The tier VII/IX/X prices are 6000/12000/15000 bonds, respectively;
these are not described as official 0.9.22 bond prices.

The native store retains its card surface; purchases use the Account command,
GarageState transaction, common vehicle purchase and durable garage ledger.
Slot and crew entitlements apply to purchases charged the bond offer.
Insufficient funds, duplicate purchases and failed save writes roll back.
Premium purchases use the native six durations and stock price templates.

The September 18 zero-gold reports for 121B and Panzer 58 Mutz came from
publishing all bond offers into the shared `shopItemPrices`. Native tech-tree
consumers read gold there, so a crystal-only override became zero gold. Offer
publication now restores the eight non-retired entries' baked native prices
and `notInShop` flags, including 32000 gold for `Ch25_121_mod_1971B` and 9000
for `G119_Pz58_Mutz`. This does not make every reward or unavailable vehicle
an ordinary purchase. The five explicitly retired offline bond definitions
keep their bond-only catalogue override.

Special Offers supplies its own complete `ItemPrices(ItemPrice(Money(...)))`
quote and bond affordability check to the native vehicle-row producer; it
does not mutate the shared Vehicle object. Its Account service selects the
published offer explicitly for the common GarageState purchase, which charges
bonds and grants the included slot/100% crew. An ordinary gold purchase or
credit recovery keeps its own price and normal slot/crew terms. Merely being
listed in Special Offers no longer changes either transaction. Regressions
cover every published offer, republishing stale bond overrides, native card
prices/affordability, both currencies, recovery precedence and atomic failures.
The row hooks were checked against public 0.9.22 Python; exact 1513 native
card rendering and the tech-tree values still need Windows acceptance.

No friendly-fire locale key is overridden. Public 0.9.22 result calculations
use `details/calculations/friendlyFirePenalty` for credits and XP, and
`details/calculations/friendlyFireCompensation` for credits only. Exact China
0.9.22 text was not independently recovered in this workspace; the installed
client owns those translations. No XP compensation field or row is added.

Training mode travels from the selector through the host's explicit start to
the authority, native arena types and durable receipt. Bot fill is optional;
the mandatory hidden native worker remains the authority even without Bots.
Training settlements preserve spent ammunition and consumables, repair for
free, and cannot award XP, currency, medals, daily progress or lifetime stats.

The reserve surface reuses the room's cursor, callback and root ownership,
with explicit Close/Escape teardown. Reserve purchase and activation use
durable transactions. Four legacy types remain separate, at most three can
run, and bonuses use base earnings and battle-start eligibility. Expired
activation intervals survive newer activations for late receipt replay.
The [WG reserve guide](https://na.wargaming.net/support/en/products/wot/article/18943/)
describes reserve types and acquisition via missions/events, but is updated
after 0.9.22. The purchase prices and daily missions here are explicitly
offline extensions, not a reconstruction of a particular historical event.
Daily grants share the once-only receipt commit and cannot be re-awarded by
retry or by replaying an older day after newer progress.

The reports' Vivox failure reflects an unavailable online account service.
The sound tab no longer requests reinitialization and reports offline voice
unavailability. This does not implement a replacement voice server.
Native store card rendering, reserve surface layering/closing, training arena
transitions and sound-tab presentation require the exact Windows client.

The subsequent `20260917-214608-a4c3f99dd823` report identified a startup
regression in build `colorfulmeans-35227105547-1`: the server was listening,
but the worker's initial Account sync rejected its vehicle catalogue before
the launcher could open the visible client. Bond-offer publication had added
unowned definitions to `vehicleTypeCompactDescrs`, which must exactly match
complete garage records. Offers now only extend shop prices and purchase
entitlements. The common purchase still establishes ownership when successful;
the inventory validator is unchanged. The regression test follows the actual
bootstrap -> full Account sync path with an available offer absent from the
garage, for both account modes and with/without saved-state restoration.

The same startup audit reproduced a second failure in the visible lobby
adapter: the stock settings package re-exports `SettingsWindow` as a class,
but the adapter treated that export as its module. Importing the class and
its tab helpers explicitly from the module fixes installation. A focused
test preserves that package/class distinction, exercises the sound tab and
ordinary tab delegation, and verifies uninstall restores the original method.
The older isolated bootstrap contract now includes the services adapter and
asserts installation before Account connection and cleanup on shutdown. The
combined bootstrap, Account RPC, services, economy and garage regression run
passes 667 tests. Native Windows client startup remains a user retest boundary.

The `20260917-223308-04b90c87b1e8` report reached the native hangar on build
`colorfulmeans-35230599345-1`. Its screenshots show why the service overlays
were insufficient: the retail mission empty-state text and controls remained
underneath them, and native reserve slots still consumed an empty goodies
cache. This follow-up removes the reserve overlay and restores the retail
BoostersWindow/TabsContainer lifecycle. The shop now publishes four mod-owned
GoodieData definitions (nine fields including nested target/resource tuples),
and Account sync and paid-service updates publish their three-field
GoodieVariable values. Native GoodiesCache, booster tooltips, filters and both
BoostersPanelComponent instances therefore consume the same IDs, counts and
expiration timestamps. Expiry retires the active interval while retaining its
battle-start entitlement history. System messages follow durable purchase or
activation acknowledgement; a failed save cannot emit a purchase success.

The offers tab selects the existing ShopUI linkage and binds its component ID
to a Shop subclass before Flash registration. Rows use the original vehicle
wrapper and item.icon, crystal prices, separate offer filters, and ownership
sorting with disabled owned rows at the bottom. Ownership is checked before
opening the confirmation, again after confirmation, and inside the transaction.
Factory settings and filter defaults are restored on teardown. Regular Shop
and Inventory components retain their classes and filter values.

The account popover retains AbstractPopOverView's hide/destroy lifecycle while
populating local account/badge data without online clan/tutorial initialization.
The native badge page now gates selection on the installed Badge.isAchieved
dossier result; merely existing in badges.xml is insufficient. Selection uses
the same persistent Account transaction and badges sync field, so a failed
write cannot visually equip an unsaved badge. The daily page no longer builds
retail mission tabs; its separate opaque panel owns its close/cursor lifecycle.
Three daily templates are selected deterministically from a hashed day and
template ID, with one battle-count, damage and victory goal. Rewards belong to
the templates, not a second reward roll. Existing same-day fixed missions keep
their progress/claims; the next day gets the new selection.

The additional native UI producer/consumer contracts were reviewed in public
0.9.22 reference Python (regional #788), alongside the #1513 screenshots/logs;
the exact client's scripts.pkg is not available in this workspace. Regression
coverage checks native row/slot wire shapes, controller lifecycle preservation,
duplicate clicks, confirmation races, durable badge publication, notification
ordering, daily rollover and legacy receipt replay. These checks do not prove
Flash layout, component binding or input behavior on #1513; that remains the
next Windows gameplay acceptance boundary.

The `20260917-225717-1dc3e2c245c8` report and new screenshots show a 90-day
premium account with identical standard/premium results columns. The public
0.9.22 reference defines account factors 10 and 15 and replays both columns
from the applied premium factor. The supplied historical screenshot agrees:
39157/58736 credits and 918/1377 battle XP. New receipts now freeze premium
eligibility at battle start and persist the same factor inputs used to bank
income. Native ValueReplay chains consume those inputs for credits, XP and
free XP, with the client's rounding at each step. Premium also reaches crew
training; it does not multiply bonds, repair costs or ammunition costs.
Reserve income remains additive to the daily first-win bonus. The default
first victory is x2, not the historical video's temporary x5 event.

First wins are tracked per vehicle and UTC day in the garage transaction.
A failed write consumes neither the reward nor its entitlement. Receipt
retries retain the original premium/first-win inputs, and older receipts
cannot reset a newer day's marker. Account updates replace (rather than
union) multipliedXPVehs with its stock tuple-key protocol, so the carousel
can recover its x2 markers at midnight or upon returning to the lobby.
The retail carousel maps an available dailyXPFactor to its existing bonus_x2
asset and clears xpImgSource after consumption. The account's native daily-XP
attribute also enables the matching tooltip. A focused Account test verifies
initial entitlement, removal for only the winning tank and next-day set
replacement, alongside the existing durable-settlement and lobby-timer tests.
Existing archived results are not retroactively repriced or re-awarded.

Daily completion IDs are stored with the settlement receipt. They produce
native lower-left result rows and a single combined reward dialog through
the existing once-only result-notification path. This is the offline daily
goal system, separate from the original campaign personal missions. Campaign
reward settlement and supported condition evaluation were added in the
2026-09-18 follow-up documented below. Badge selection alone does not award
campaign medals.

The same report repeatedly blocks Ruinberg's old Mercedes (chunk 33151,
item 3), motorcycle (33151/89), bench (32385/7) and other small objects while
waiting for full-chunk name alignment. A focused registration path now
checks a contacted catalog model independently under the existing query
budget. It requires the exact wire ID, unique complete authored transform,
supported catalog revision and compatible native category. Unresolved,
isolated or remapped layouts continue through the existing conservative path.
It never calls the unsafe native filename helper or weakens crush strength.
The proof cache is discarded on chunk unload, layout invalidation and arena
teardown. A regression uses the report's actual Ruinberg catalog records,
including a stale-transform rejection after unload. Native #1513 contact and
projectile behavior still requires a Windows gameplay retest.

Regression coverage additionally checks premium/first-win replay totals against
durable awards, rounding with XP penalties and reserve bonuses, per-vehicle
and cross-day first wins, disk-failure rollback, late receipt replay and
combined mission notifications. Public Python contracts and historical
screenshots support these changes; an exact official China 0.9.22 web copy of
the premium/help pages was not recovered. The package does not import modern
WoT Premium Account features or claim native gameplay validation from mocks.

Full CI exposed one additional regression in the friendly-fire settlement
test: save-owned extra XP had moved into originalXP. Settlement now persists
the battle basis separately from extra earnings and reserves; the latter
share the existing booster row, with the same stepwise account-factor rounding
in banking and native replays. The original 667 gross / 67 penalty / 600 extra
fixture again displays 1200 total XP across save failure and restart. The
premium/first-win replay matrix also covers 50/100/150/200 percent save income.

## September 18 follow-up: completed battles, recovery and collision ownership

Reports `20260918-002345-1049c84c783b` and
`20260918-003256-afb94deeac69` identify the current launcher build, so this is
not explained by a stale mod installation. Bootstrap forwards the durable
receipt's `premature_leave` fact to daily policy, and settlement preserves the
first-win entitlement for abandoned battles. The September 18 follow-up
separates abandonment from returning to the garage after destruction: the
server freezes voluntary, live, non-overturned abandonment at the leave
boundary instead of treating every departed participant as a deserter.
`watched_battle_to_end` separately owns result auto-opening and survival
statistics; old receipts retain their historical presentation fallback. A
destroyed participant can advance ordinary daily goals when the round settles.
Connection loss and failed startup are not a confirmed abandonment warning. Hashed daily
selection and reset remain at 00:00 UTC / 08:00 Beijing; receipt replay cannot
grant the same reward again.

Badge cards use native achievement ownership and show unearned badges in the
locked collection. The Account command validates ownership independently of
the page. A saved verification marker distinguishes newly validated selection
from the earlier unrestricted cosmetics; it does not manufacture campaign or
ranked achievements.

Premium sales now save a recovery entitlement with the hull's credit selling
value plus 10%, independent of ammunition, equipment and crew sold with it.
The rule and vacant-slot requirement follow WG's
[restoration policy](https://wargaming.net/support/en/products/wot/article/23829/).
The mod uses its own published 0.9.22 selling values, not modern vehicle price
tables. The native `restore_config.vehicles` and
`recycleBin.vehicles.buffer` carry PREMIUM=0 with a 72-hour timestamp, or
ACTION=1 with timestamp zero for a premium outside the shop assortment.
Purchase and sale persist wallet, inventory and recovery state together;
failure rolls back and successful restoration removes the native buffer row
using an explicit None diff. All price consumers use the same saved credit
quote. Ordinary, rental and unrecoverable vehicles receive no entitlement.
No historical sale record can be invented for an older save.

Shop vehicle extras now select the union of the checked categories, with
normal available purchases when nothing is checked. Class, nation and tier
criteria remain native. Bond offers stay in Special Offers, whose inventory
and rental filters now work, and are excluded from the regular purchase tab.
The native Python component identity must remain `shop`: Flash's
`VehicleView.onFitsArrayRequest` compares that exact string to include the
owned checkbox. Bond-only persistence now overrides the StoreComponent filter
read/update boundaries rather than changing that identity. The native vehicle
view hides purchase/restore/trade-in/unresearched controls after its own
visibility pass. The accordion hides and disables non-vehicle buttons,
including keyboard selection; its native controller retains disposal ownership.
It does not mutate the shared `FITTING_TYPES.STORE_SLOTS` array.

The September 18 reports still showed the ordinary shop in Special Offers.
The reference `StoreView` enables `ViewStack.cache`, whose key is the linkage,
not the tab ID. Sharing `ShopUI` therefore suppressed `NEED_UPDATE` on a cache
hit and prevented registration of `BondShop`. The adapter now disables this
parent view's cache before `as_initS` creates its first component. Native
`clearCurrentVew` unregisters and disposes the old tab before creating the next
one. Regression coverage checks shop/offers/shop and offers/shop/offers;
original tank artwork and duplicate-purchase protection remain in use.

The public Flash SDK at `CH4MPi/GUIFlash` commit
`78d711d336cf55d73e62d0ab996fc18bbfbd893f` provides the reviewed
`StoreComponent`, `ShopVehicleView`, `VehicleView`, `Accordion` and
`ButtonBarEx` members. The historical SDK is orientation, not an exact #1513
asset audit. `DataProvider` extends Array, and the published BigWorld
`PyGFxValue` bridge converts arrays to Python lists; editing that list would
not change the native provider. The implementation instead edits the referenced
entry VOs and their public buttons, resynchronizing filter controls after native
layout. Focused fakes preserve this array-copy distinction and exercise repeated
refresh, independent saved filters, owned rows and disabled category buttons.
Actual rendering and resize behavior remain a #1513 Windows acceptance item.

Personal reserves now include all 44 distinct manual, non-expiring combinations
in the [WG API response captured on April 26, 2016](https://github.com/victor-lyan/wotwrap/blob/931feede6d2f86e7f46973a25b6df6b233e261bd/tests/json/encyclopedia.boosters.json).
The source has 61 IDs: expired 2015 events are omitted and equal resource,
bonus and duration variants are deduplicated. Percentages and 1/2/4/6-hour
durations are preserved; the installed client's `Booster.quality` and GUI
settings classify them. The four old keys/IDs remain stable, so saved counts,
active clocks and daily rewards survive the expansion. Gold prices remain an
explicit offline extension, scaled from the existing four offers. Activation
allows three resource types, and a stronger same-type reserve replaces the old
one after the native confirmation. Replacement truncates the old historical
interval, preserving late battle-start entitlement without future overlap.
Catalogue shape, expiry, saved-state reload, replacement, rollback and
cross-variant exclusion are covered. The three-slot policy is also installed
into all three native imported `MAX_ACTIVE_BOOSTERS_COUNT` copies and the
panel's prebuilt `_GUI_SLOTS_PROPS`; changing only the transaction limit would
leave a regional one-slot UI at 1/1. Uninstall restores the original copies.
The [2017 official guide](https://wargaming.net/support/en/products/wot/article/18943/)
describes four resource types; the [three-type redesign](https://worldoftanks.com/en/news/updates/1-18-1-improved-personal-reserves/)
arrived in 2022 and does not define this client's catalogue. Size depends on
bonus strength, with duration independent; actual classification remains the
installed client's `GUI_SETTINGS` rule, not a guessed universal threshold. This preserved international catalogue
does not prove completeness for later China-only 0.9.22 event offers.

The Ruinberg logs show a compacted-name rebuild failing with
`status=isolated_item`, followed by independently position-proved cars and
fences remaining blocked. That status no longer quarantines the entire chunk.
Other terminal ABI/descriptor failures retain quarantine. Contact proof has
its own four-query render-tick budget, cannot acquire or release the
background scan's focus, and still requires exact unique placement, unchanged
wire, native category and installed descriptor. Regression coverage uses the
reported Mercedes and motorcycle while another chunk owns exhausted scan
budget and an unrelated slot is isolated.

The stock `__setFragileDestroyed` callback records native delivery for that
space/chunk/item, but its return does not prove that every remaining collision
face belongs to a replacement. September 18 report coordinates intersect
Ruinberg `env406_Flowerbed` items 123/106/102 in chunk 32637 and Westfield
`gaf019_StoneFenceTile` items in chunks 33153/33409. The previous item-wide
exception made original normal materials solid again after destruction. Both
ground and horizontal filters now keep accepted 71--86 original materials
hidden, retaining native damaged-module materials 87--100 and ordinary
replacement materials after delivery. Unrelated walls and intact neighbours
still block; no car-shaped obstacle or fabricated support height is added.
Chunk unload and battle reset clear the delivery marker. Bounded hard-contact
diagnostics observe existing callbacks and record up to 16 material/flags/item/
chunk/keep candidates, without adding native queries or asserting callback
order. The supplied reports did not include material IDs, so exact Windows
traversal and retained wreck support remain required gameplay checks.

Copied local Siege pose now snapshots body/ground relative offsets once at
attachment. The previous live products kept importing two unsynchronized
providers from a client-only WGVehicleFilter without cell physics; the copied
terrain/aim matrices now own subsequent movement. Gun, shot ray and rendered
body still share one copied provider. This removes a plausible drift path,
but neither attached report records Strv S1 sinking, so reproduction and
native acceptance remain outstanding. Ruinberg destruction, residual car
support and Swedish Siege motion require the exact #1513 Windows client.

The initial Siege-mode hint uses a separate GUI-ready cache seed. The vehicle
starts in DISABLED without a change event; skipping that physical no-op must
not leave `vehicleState` without a `SIEGE_MODE` value. A single local seed
after `setClientReady` lets the indicator consume `(DISABLED, 0.0)` immediately
or when it populates later. It does not invoke a fake hydraulic or descriptor
transition. A reversible offline indicator adapter also prevents the original
ten-transition tutorial budget from suppressing the requested persistent key
hint; the saved account counter is preserved, and switching, death and
observer visibility stay with the native implementation. The reference
Python is regional #788 and the available Flash SDK is a historical reference;
no complete Chinese #1513 installation is available for native acceptance.

## September 18 playtest: Siege timing and worker failure

Reports `20260918-070333-f76f320bea1c` and
`20260918-070836-29c3e83712cd` both identify the installed
`colorfulmeans-35282925222-1` package. The premium screenshot proves the
bundled 90-day texture loads, but its three-game ribbons do not match the
other World of Tanks duration emblems. This is an artwork mismatch rather
than evidence of a missing resource in that package.

The premium-window resource rule is
`gui/maps/icons/windows/prem/icon_prem{days}_98.png`. The user's subsequent
extracted-resource screenshot confirms 1, 3, 7, 30, 180 and 360 days. The
erroneously added 90-day offer, duration/image monkey patches and bundled
artwork have been removed. The shared shop catalogue now offers exactly
360, 180, 30, 7, 3 and 1 day; the Account command rejects 90 without charging
or changing existing premium expiry. One day costs the retained 250 gold.

Report `20260918-083042-e3e156565014` identifies build
`colorfulmeans-35288390043-1`. Three car-support records have current wheel
coordinates and retain every direct support sample (world-height spans
approximately 0.17, 0.49 and 0.35 metres). These are wheel-height differences,
not measurements of the wreck's height above terrain. Two later records
incorrectly reuse old spring traces after switching to hydraulic suspension;
these are invalid height evidence. Suspension reset/disable paths now clear
that diagnostic trace. Collision geometry and support calculations remain
unchanged, consistent with the user's improved visual acceptance.

The Strv S1 report records a Python exception at 07:03:19, not an unexplained
native process crash: `_present_authority_bot_poses` reaches
`RemoteVehicle._write_pose`, where the native orientation setter raises
`TypeError: () argument 1 element 0 must be a valid angle`. The exception
escapes the frame, closes the mandatory simulation worker connection and
ends the round with `worker_disconnected`. The rendering failure and the
reported travel-mode motion need separate treatment; a display failure is
not evidence that the authoritative round itself has become invalid.

Both copied and native remote presentations now convert finite angles to an
equivalent principal rotation at the native matrix boundary. Shortest-arc
interpolation remains unwrapped internally so turn-speed differences stay
continuous across the seam. The captured exception does not record its
rejected value, so multiple-turn accumulation is a covered failure path,
not a claim about the exact observed value. A per-actor presentation boundary
logs a failed matrix/aim update, leaves its successful-write cache unset and
allows other actors and combat events to continue; the next frame retries.
Finite authoritative geometry still feeds projectiles independently of
rendering. Non-finite samples are rejected before writes and preserve the
last coherent collision pose. Tests use a matrix fake that rejects invalid
angles rather than an always-successful setter.

The Siege reticle's stable states carry zero remaining transition time. The
reference Flash `SiegeModePanel.setEngineAndTime` renders this second time
argument as `- -`, even though the native Python indicator separately holds
the descriptor's next-switch duration. The battle-scoped adapter now uses
`_switchTimeTable[state][engineState]` only while rendering a stable-mode
indicator and restores `_switchTime` immediately afterwards. Switching
states retain their authoritative remaining time; engine critical/destroyed
handling, observers, postmortem and disposal remain native. This displays
2.0/1.3 seconds for Strv S1/103/103B and 2.0/2.0 for UDES without changing
their physical mode transitions or hard-coding a shared display duration.

The native Shop's upper-right `actionsFilterView` sends its selection through
`requestTableData`; it is not a separate hyperlink callback. In Special Offers
the redundant selector is hidden and its mouse interaction disabled. In the
regular Shop a selected request preserves the normal shop filters, saves the
discount flag as false and schedules navigation to the native Offers tab with
its independent filters reset. Navigation runs after the Flash callback has
finished accessing `storeTable`, avoiding disposal of an in-use component.
Repeated clicks coalesce; disposal, account changes or adapter removal retire
the queued navigation without changing filters.

Report `20260918-212147-84a3849a93a9` exercises matching launcher/client build
`colorfulmeans-35348086780-1` on Chinese HD `0.9.22.0.1 #1513`. Switching from
vehicle recovery to buying reaches native `Shop.requestTableData`, then
`StoreComponent._setTableData` and `ShopVehicleTab._getRequestCriteria`, which
raises `KeyError: 'extra'`. This is a recorded Python failure, not a network
timeout. The regional 0.9.22 Python references show that recovery/trade-in
defaults omit `extra`, whereas buying indexes it directly. Shop and Inventory
both save the received filter before building their table and close their
waiting overlay only on success. Thus a failed request can also persist the
incomplete filter for the next opening. The available Flash reference shows
the shared vehicle view switching its obtaining type; it does not establish
the exact #1513 serialization step that omitted the key.

The store adapter now completes each incoming filter from that category's
saved values and the running client's `AccountSettings.getFilterDefault`.
Explicit empty selections remain empty, and the requested category owns its
obtaining type. It repairs incomplete saved category filters before native
`StoreComponent._populate` reads them. Regular Shop and Inventory retain their
native table builders, scroll targets and persistence; an exception releases
that request's waiting entry and remains visible in the exception log. Offers'
separate filter-option and request overrides use the same completion helper,
with their existing independent settings and deferred navigation retained.

Regression coverage reproduces the missing-key exception before installing
the fix, then checks repeated recovery/buy/trade-in transitions, missing/null
fields across all fourteen native Shop/Inventory categories, empty and saved
selections, initialization after a failed save, Flash-object conversion,
exception cleanup without double-hiding another wait, next-request recovery,
and reversible hooks under Python 2 method binding. This identifies a shared
failure class; it does not claim every category failed in the supplied report.
The references and fakes are contract guidance, not a new exact #1513 bytecode
audit. Actual menu rendering and transitions still require Windows playtest.

The later Ruinberg report locates the crushed-car observation at
`env418_OldGMercedes1.model`, chunk 33407 / item 36, around
`(340.126, 13.579, 46.447)`. Its suspension-plane residuals of 0.129 and
0.091 metres are not measurements of visual penetration. Existing support
layers were logged only for a hard contact, while this traversal remained
clear. `LOCAL PROP SUPPORT` now samples at a separate two-second cadence,
including constant-speed travel, only over a locally identified,
authoritatively destroyed road vehicle with native destruction delivered.
It reuses the existing wheel samples and local catalogue; it adds no native
queries or guessed geometry and preserves the original stall-report cadence.
The supplied reports and local resource references do not contain the
damaged visual/BSP or this car's wheel support layers, so a contour-matching
collision change remains unproved and is not claimed by this patch. Restoring
the original intact-car bounds would reintroduce an invisible obstacle.

The Strv S1 travel complaint remains a native acceptance item. The report
contains intermittent `world=pending`/`soft_hold` intervals while destructible
structure models change; it does not prove one continuous half-second stall
or a bad engine coefficient. Repeated stable Siege snapshots already skip
descriptor/velocity resets, and a destroyed structure's hold deadline is
written once rather than extended by each query. New Siege request, ack and
state-edge diagnostics record input sequence, drive input, pending mode,
actual speed and descriptor limits, capped at 128 records per round. Existing
throttled movement diagnostics provide the contact context. This adds
evidence for the next Windows run without changing physical coefficients,
collision safety or the model-switch wait interval.


## Launcher campaign, orders and badge editing (2026-09-18)

The launcher garage retains its original labels and standard-resource filter.
All five supported retired vehicles are included even when their original
entry has a credit price instead of gold or a zero-price reward flag. The
Bot exclusion and in-client bond shop remain independent of this catalogue.

The completion editor uses regular mission IDs 1..300, four operations of
five fifteen-mission chains in LT/HT/MT/TD/SPG order. The ledger stores these
under `personalMissions.completed` as 1 (main complete) or 2 (honors), while
`personalMissions.regular` remains the selection list. Omission is incomplete.
The editor closes the required-unlock graph without granting honors:
tasks 1..14 are unordered, finals require their fourteen tasks, and later
operations require the previous operation's five finals. Resetting a main
completion clears its chain's final and every class in every later operation,
even when a sparse old save omits an intermediate final. Downgrading honors
changes no other task and does not fill skipped prerequisites. A reset mission replaces an older
selection of the same vehicle class so it can be replayed. The producer uses
installed `PMStorage`/`PM_STATE`, retaining native reward-needed states for
unclaimed female crew choices.

`personal_campaign` reads reward definitions from installed resources and
settles missing stages on garage startup and after authoritative battle
receipts. `rewarded` records paid main/additional stages independently of
`completed`; `tankwomen` records delayed crew claims. The launcher writes
`requestedCompleted` and `requestedRegular`, not committed progress. The client
withdraws the actual recorded stage and dependent operation payouts on a
detached transaction before committing an edit. Markers are cleared only after
successful withdrawal, allowing replay to grant the reward once again. A
rejected withdrawal clears the pending request, preserves the old progress and
property, and publishes `resetError` to the editor and a durable notification.
All quantity withdrawals stop at the remaining balance or stock; notices
record only the actual debit, including duplicate-vehicle compensation,
consumables, camouflage, dossier increments and free orders. Mounted stock
and occupied slots/bunks are excluded. Other missions' order pledges remain
recorded and cannot create free orders after their earning source is reset.
Unresolvable legacy provenance still rejects the withdrawal.
Elapsed premium time is not reversible; only the remaining earned interval is
removed without consuming separately purchased time.

Crew provenance survives inventory-ID reconstruction across restarts. A
permanent dismissal recorded at recycle-bin eviction/expiry, or the prior
serializer's explicit missing-owner location with its reward descriptor,
allows reset without another crew removal. Unverified or ambiguous provenance
still rejects the edit; no similar-descriptor search selects unrelated crew.
Reward vehicles also carry a persisted
source marker. Withdrawal returns crew and fitted items to storage and keeps
a reversible record of the hull. A later purchase of the same type is not
silently removed in place of a sold mission vehicle. Old operation claims
without a vehicle source marker cannot distinguish a granted tank from a
previously owned vehicle that received compensation. Such resets are refused
rather than parking a purchased tank while retaining an unknown cash payout.

Before every tank grant, existing ownership is checked. Compensation uses the
full original catalogue price in credits (gold at the existing account exchange
rate), without `sellPriceFactor` or the bond-shop override in `shopItemPrices`.
The exact compensation is journaled and withdrawn on reset, leaving the
pre-existing vehicle untouched. This is the requested offline policy rather
than a claimed historical regional rule. Tokens are rebuilt from current
progress. Currency, inventory, premium time, vehicles/slots, badges, camouflage
and native female-crew rewards share the persisted account transaction.
Existing saves containing only completion flags receive their missing rewards.

The battle evaluator uses the installed conditions and existing receipt facts.
It supports the reported Object 260 MT-15 through per-target tank-destroyer
damage; honors additionally evaluates distinct damaged targets and victory.
Training, early unfinished exits, duplicate receipts, unmet prerequisites and
wrong vehicle classes cannot complete it. Event-history conditions without
the required telemetry remain explicitly unevaluated, rather than treating
missing evidence as success. This does not claim full 300-mission combat
coverage.

Orders are `personalMissions.orders`, published as the stock
`tokens['free_award_list'] = (4104777660, count)` tuple. The standalone launcher
quantity editor was removed at the user's request. The available balance is
derived from unique honored-final reward claims minus current pawns. Old
manually supplied extras are removed; old excessive pawns retain their mission
state but provide no free balance until covered or cancelled. Resource-defined
order counts are used, yielding 20 earned orders for the four regular
operations. Command 10019 records a one-order
ordinary or four-order final pawn and publishes its native marker. Honors
completion refunds that pawn; resetting it in the editor refunds it once.
The regional reference's unused constant 21 is not treated as a token balance
limit. Account badge ownership is `ledger.accountBadges`,
a badge-ID/acquisition-time map, written into the native account dossier's
`playerBadges` block. Native Badge objects consequently expose acquisition to
both the gallery and selection validation. No battle medal counters or combat
statistics are fabricated. The launcher parses `scripts/item_defs/badges.xml`
from the installed `scripts.pkg` using a dedicated read-only accessor, not the
vehicle-edit path whitelist, and reads `res/text/LC_MESSAGES/badge.mo`.
Removing the selected badge also removes its saved selection.

Report `20260918-112918-91f01e7610db` identifies a startup failure in build
`colorfulmeans-35301539662-1`: `personal mission rewards could not be published:
global name 'data' is not defined`. The account data import was local to other
functions, so the startup validator rejected the staged edit before any flush;
the same omission affected post-battle reward-vehicle research publication.
Bootstrap now imports that dependency at module scope. Regression coverage
executes save restoration, resource parsing, settlement, native-shaped garage
validation, durable flush, publication and restart, including a refused flush.

Report `20260918-114203-26519828ce40` confirms the same missing import during
post-battle reward-vehicle research publication: the server ends the round at
11:41:44 and the visible client rejects its receipt at 11:41:49.813. Pending
launcher rewards were consequently retried during battle settlement. This is
a plausible contributor to the reported end-of-battle stall, but the server's
five-second round-reset delay is not itself proof of a five-second Python
stall. Receipt diagnostics now record settlement elapsed time and rejection
tracebacks. The client restores its lobby Account at 11:41:50.590 and enters
`game.fini` at 11:42:00; the report has no native exception event or dump. The
exit cause and final frame pacing still require Windows reproduction. No Bot
cadence, physics coefficient or rendering setting is changed for this report.

The [official 9.20.1 release notes](https://worldoftanks.eu/en/content/docs/release_notes/release-notes-9201/)
document the five final-mission components, one order per honored final,
one-/four-order skip costs and refunds after honors. They explicitly allow
spending four orders on a final before its fourteen preceding tasks. The
launcher's cascading reset is a user-requested offline editing rule; it is
not a retail operation. Vehicle ownership does not replace completion flags
when deciding whether later missions remain available.

Before the first garage, corresponding initial values live in `save.json`;
restoring an existing garage ledger takes priority. Writes reject a running
game and use the existing atomic replacement helper. Completion writes preserve
badges, wallet, vehicles, daily goals and unrelated save fields.

Producer/consumer orientation for PMStorage, states, tokens and Badge comes
from regional #788 Python; the existing #1513 contract pins `potapovQuests`
and its requester keys. Command 10019 and crew reward command 125 are oriented
from that regional producer/consumer pair; they are not claimed as new exact
#1513 bytecode audit evidence. New native serialization, reward selection,
badge rendering and the Tk dialog's final Windows layout still require
#1513 Windows acceptance.
Local tests cover storage round trips, checkbox dependencies, all 300 IDs,
invalid/running writes, atomic failure, badge removal and publication fields.


## Personal-mission display and reward withdrawal (2026-09-18)

The battle roster's existing 18-field vehicle tuple now carries active personal
mission IDs at index 15. Selection is captured before Account retirement,
filtered through installed mission class, tier and prerequisite definitions,
and retained through Avatar/arena creation. A main-complete mission remains
eligible for honors; fully honored, wrong-class, locked and training missions
do not populate that field. This uses the original TAB description, not the
live progress widget introduced in Update 1.1.

Each durable battle receipt captures mission state before/after evaluation,
actual paid stages, localized quest identifiers and reward details. Native
`questsProgress` is constructed from those recorded transitions for the result
screen, including incomplete conditions. Result reopening and receipt replay
use saved metadata rather than current campaign state. System notices use one
native `SystemMessages.pushMessage` call per settlement. Personal-notice retry
does not replay an already accepted battle or daily notice. Launcher settlement
queues messages in the same garage save as the assets; successful delivery is
acknowledged persistently.

Launcher vehicle delivery now stages the vehicle and an `account_changes`
notice together, preserves the inbox on a failed save, and consumes it only
after commit. Badge and wallet editors append actual deltas through the same
durable notification queue; initial-save metadata hands it off once to the
garage ledger. Native badge and vehicle names are resolved for presentation.
The existing Account-ready publisher and session acknowledgement suppress
duplicate notices after unchanged edits, reconnects and failed ack writes.

The dismissed-crew buffer already had a limit of 100. Expired entries now
leave a terminal campaign crew receipt, and equal-time eviction always keeps
the newly dismissed member. The confirmed offline policy retains an immediate
100-gold charge and seven-day lifetime in both the shop and mutation owner.
Region-specific `ShopRequester.tankmenRestoreConfig`,
`getTankmenRestoreInfo` and `RecycleBinRequester.getTankmen` confirm the field
roles as source orientation. The old Chinese server's numerical configuration
is not audited: official guide pages could not be read in this investigation.
Clock-controlled tests cover immediate/last-second/expired recovery and 101 dismissals;
they do not prove native dialog rendering or real-time callback behavior.

Badge eligibility is recomputed from the enabled token-quest dependency graph,
not permanent historical token-reward markers. Missing main or honors
requirements remove their mission badges and equipped selection; unrelated
badges remain owned. Restoration of eligibility restores the badge without
replaying unrelated economic rewards.

The [9.16 release notes](https://worldoftanks.eu/en/content/docs/release_notes/release_note-9_16/)
describe revised reward presentation and personal-mission information. The
[1.1 release notes](https://worldoftanks.eu/en/content/docs/release_notes/release-notes-11/)
identify the later in-battle progress changes and reworked TAB descriptions.
The existing regional `QuestsProgressBlock` supplies lower-left result contract
orientation; these references do not prove new #1513 native rendering. Daily
missions here remain an offline extension, not the later retail Daily Missions
feature. Reward withdrawal on launcher edits is likewise a custom policy.

Report `20260918-124129-4822f788f955` contains successful campaign-bearing battle
receipts and no recurrence of the earlier missing-`data` failure. Its shutdown
cleanup traceback does not establish a native crash cause. The report and
screenshots establish the missing mission presentation, while exact Windows
TAB/result layout, notice timing and final-frame behavior still need gameplay
acceptance on #1513.

### Completed daily rows, battle commendations and commander voices

The September 18 screenshots show daily reward text under an orange warning,
missing small battle-result commendations, and female commanders using male
voices. `offline_services_ui` incorrectly populated `alertMsg`, the stock
post-battle quest warning field. Completed daily rows now leave that field
empty, carry `MISSIONS_STATES.COMPLETED`, omit progress bars, and render their
actual reserve through `GoodiesBonus.formattedList` and the stock simple-bonus
block. No replacement icon is drawn over Scaleform.

The entire `approachableAchieves` group was absent from the award allowlist.
Its eleven records now flow through battle settlement, results and dossiers.
The 0.9.22 reference `arena_achievements`, `achievements.xml` and dossier
layouts supply the group, numeric conditions and record/block orientation.
Descriptions were cross-checked against the client text resources mirrored at
`izeberg/wot-src` commit `0ff1890d1a24d43cf186b86a295bbc7dec63de56`,
`sources/res/text/lc_messages/achievements.po`; this later resource is not
presented as #1513 evidence. In particular, Spotter is spotting assist on a
win, not a shooting streak. Its flag belongs in `singleAchievements`, with
`maxAimerSeries` in `achievements`. Battle Buddy has an account-wide 50-battle
series and no vehicle record. Admitted module transitions include zero-HP
hits and fire; module-only friendly damage interrupts Battle Buddy. Existing
receipt deduplication and save rollback own these awards too. Prior battles
without the missing combat facts are not retroactively re-awarded.

The local arena producer previously hard-coded vehicle-list slot 16 to zero.
The reviewed 18-field roster and regional 0.9.22 `ClientArena` reader identify
that slot as `crewGroup`; `TankmanDescr.group` packs gender, premium and group
identity. Freeze the mounted commander's actual group with the garage loadout
before Account retirement and publish it before native vehicle presentation.
The native `SoundModes.setCurrentNation` owner selects the language and writes
the gender switch. The scoped offline adapter supplies attached-vehicle
gender there so settings and postmortem callbacks cannot overwrite it; lobby
previews use the selected crew. Special voices that call `setMode` directly
remain native. Applying commander gender to Standard/localized mode as well
as national mode is the requested offline extension.

Report `20260918-204953-158db2d626ed` uses build
`colorfulmeans-35342782450-1`; the player reports that changing Standard to
Commander during battle leaves Standard active. The client-only Avatar's
engine `vehicle` attribute is empty, as already handled by
`BigWorldBinding.avatar_vehicle_entered`. Regional `AltVoicesSetting`
changes its mapping in `setSystemValue`, but `clearPreviewSound` refreshes
only the engine `player.vehicle` and skips a present-but-empty attachment.
The offline adapter now refreshes the live, started `getVehicleAttached()`
after successful mode application and native preview cleanup. It delegates
to `Vehicle.refreshNationalVoice`, preserving nation mapping, special crew
modes, native preview stopping, return values and teardown. The patch has no
retained vehicle or deferred callback, and leaves ordinary engine attachments
and online players alone. Focused tests reproduce the missing-attachment guard,
both switch directions, male/female national and Chinese modes, preview
cleanup, failed settings, startup/teardown and special native voices. This is
source/lifecycle evidence; the report has no bank-selection trace proving the
audible result of the new patch.

Focused tests cover award boundaries, module/fire bookkeeping, result packing,
career reload/deduplication, the roster field and voice resets. Regional source
orientation and tests do not replace a new #1513 bytecode audit or Windows
acceptance: the final Scaleform layout and audible Chinese/national female
banks must still be checked in the actual supported client.

## September 19 Strv S1 downhill ground-exit report

Report `20260919-002743-39ef021b304a` identifies released v0.9.0 build
`colorfulmeans-35356845247-1`. On `18_cliff`, Strv S1 remains at approximately
`(-185.979, -1.128, -124.409)` from 00:24:45 through 00:24:52 while receiving
forward and reverse input. Both stable Siege states (0 and 2) occur;
`siege_drive_locked` is false and `siege_pending` is null. Five hard-contact
records identify `ground_profile`, not a destructible or a transition lock.

The captured lower hull ray crosses outward through a drivable terrain top.
Its seven forward samples descend monotonically, but a later, steeper segment
exceeds the existing descending gradient limit. The Python collision owner
incorrectly uses that later drop to block departure from the earlier surface.
The reported body pitch also remains nearly level and its five-point support
plane is absent; the report does not contain the individual support samples
needed to independently diagnose that pose. Hydraulic vehicles intentionally
use the legacy support path rather than the ten-spring trial.

The shared horizontal collision owner now admits this bounded departure only
when the sampled lane is descending, the actual native normal is drivable,
the ray crosses outward, a vertical query confirms the exact hit is the top,
and a recast of the remaining same-height segment is clear. Occupied upper
hull lanes require the same proof for their terrain contacts. A backing wall,
low obstacle, beam, inward hit, unconfirmed top, or mixed rise/drop stays solid.
Extra queries retain the original vehicle mask and destruction filter. No
Siege speed, hydraulic provider, support/gravity law or terrain gradient limit
changes; player and worker adapters use the same corrected owner.

The regression scene reconstructs the recorded airborne and grounded lanes
from their positions, normals and seven samples. The previous implementation
reproduces `ground_profile` stops in forward and reverse departure; the fix
clears those scenes while native backing walls and upper/lower obstructions
still block. Adapter tests cover both stable Siege states and retain hydraulic
trial exclusion. This is local Python/geometric evidence, not an exact Cliff
mesh or Windows playtest. Actual #1513 Strv S1/UDES 03/Strv 103 downhill motion,
body pose and feel remain native acceptance work.

## September 19 personal-mission event settlement reports

Reports `20260919-010918-4729b816ce4d` (build
`colorfulmeans-35367691860-1`) and `20260919-012209-5bf66ea2381c`
(build `colorfulmeans-35356845247-1`) identify the supported Chinese HD
0.9.22.0.1 #1513 client. The first selects mission 61 (SPG-1), including
the completed Lakeville round. The second selects mission 32 (MT-2) in
rounds 4 and 5; their server logs contain respectively 24 and 16 positive
enemy HP-damage events, with four and five kills. These are selected missions,
not absent selections. The reports do not contain complete receipt/save
bodies or individual stun-duration evidence, so they cannot reconstruct
missing stun totals or justify retroactive reward grants.

Two shared omissions prevented settlement: the authoritative server never
incremented its already-declared interaction `stun_num`/`stun_duration`
fields, and the campaign evaluator rejected damage `eventCount` and the
stun conditions. Total HP damage, direct hits and distinct damaged vehicles
cannot substitute for damage events. This affected MT-2 and other chains,
as well as SPG-1 and several additional/honours conditions.

The worker now carries its descriptor-computed imposed stun milliseconds
with the existing absolute end timer. Only admitted internal-authority
terminals record the event, and only against living enemies surviving the
hit. The existing projectile tombstones and duplicate-target admission own
replay protection. Per-target counts, fractional seconds, distinct targets,
and two-/three-target shot counts survive receipt validation and JSON
persistence. Each qualifying shot counts once at each recorded threshold;
separate single-target hits cannot form a multi-target shot. Shorter overlap
still leaves the existing live stun/assist owner untouched, while retaining
the new hit's statistical evidence. Healing/expiry do not erase imposed hit
duration. Late effects whose stun has already elapsed remain harmless to HP
settlement and do not create a live stun or stun event. An older terminal
without duration metadata records only its known remaining interval.

Positive enemy HP changes now count damage events. Live track/stun owners
also receive assisted-kill facts once per target, including a zero-HP-loss
crew knockout. Existing critical-transition totals and ever-spotted state
are projected into receipts for the remaining SPG conditions. Native result
packing retains its existing interaction serializer and carries stun totals;
receipt-only event fields are never written into that native serializer.
Durations preserve milliseconds, and malformed/nonfinite values are rejected.
Older receipts remain readable but missing event evidence is not invented.
Task thresholds, tiers, prerequisites, wins and honours continue to come from
the installed mission resources; the existing atomic reward/replay owner is
unchanged. Unsupported-condition reasons now appear in the error-report log.

An audit of all 300 regional 0.9.22 reference main/add expressions finds a
supported solo main path for 203 (previously 149), and both main/honours for
198 (previously 136). The 60 SPG expression pairs have supported solo paths.
This is grammar/evidence coverage, not 300 actual #1513 completions, and the
regional source is not promoted to an exact-client API contract. The new
fixture stores only reference condition expressions, never production rules.
Remaining gaps include distance and limited-time filters, invisibility and
full-health event history, immobilized/ignited/higher-tier victim filters,
internal-module events and received critical history, penetration streaks,
spotting-before-detection/invisible spotting assistance, radio-assisted kills,
own-HP comparisons and mandatory native platoon aggregation. Such conditions
remain explicitly unsupported; this change does not claim all personal
missions are repaired. The reports also contain rejected critical proposals
with `critical crew roster changed mid-round`; that separate combat-profile
issue is not repaired or masked by counting unaccepted critical effects.

Focused regression coverage includes the real worker-to-server stun adapter,
wire/durable/native result projections, all 60 reference SPG pairs, all four
MT-2 thresholds, repeated-target vs distinct-target/multi-shot semantics,
fractional boundaries, zero HP, friendly/dead/expired targets, overlap and
healing, assisted kills, spotting history, malformed and legacy receipts,
and MT-2 rewards across duplicate delivery and restart. 875 related local
tests pass. Exact Windows #1513 SPG-1/MT-2 completion, result-card appearance,
and further native gameplay acceptance remain to be checked with the new
package. Existing completed receipts are not replayed as new battles.

## September 19 follow-up: Malinovka, hard walls, detached guns and mission events

Reports `073337`, `073639` and `080804` use build
`colorfulmeans-35375700188-1`. They supersede the earlier assumption that
releasing the catalog envelope alone resolved the reported railing contacts.
All five captured Malinovka native hit points resolve to unique registered
modules of chunk 32636, items 23/24/26, `mil203_MilitaryDefences01.model`.
Both modules of each encountered item were already accepted as destroyed.
The native callback also exposes original materials 73/74 under anonymous
compiled IDs with flags 131. The exact live `(chunk,item,material)` filter
cannot remove those original surfaces. Nearby material probes sometimes hit
another fence module and cannot identify the nearest ray result.

Motion/support queries now retain the native callback's candidates and
resolve the returned point against one exact registered destroyed module.
A bounded recast removes only matching anonymous original-material keys up
to that module's OBB exit. It still tests replacement material 88 and any
wall inside the box; beyond the exit it restores the ordinary callback.
Ambiguous or unaccepted modules and unknown materials remain solid. This
does not change the global vehicle mask or add a destruction wait. Horizontal
player/Bot queries and suspension/downward queries share the same operation.
The fixture preserves all five captured rays and tests reversed callback
order, an inside-box backing wall, a retained replacement, overlapping live
modules and a merged key reused beyond the destroyed module.

The Mannerheim report's material-111 contacts were followed by `deflect`
motion. A sparse ray in another heading can miss the first wall between its
new lanes. Both player and Bot hard-contact response now retain the primary
normal and reject deflections that move farther into that blocking plane.
The captured normals are regression inputs; reverse and outward glancing
motion remain covered. No destructible whitelist is used to clear real walls.

Detached bodies already contained separate descriptor turret/gun boxes.
A reproduced thin-barrel contact was nevertheless discarded: the debris and
chassis had the same ground height, the whole-body side test failed, and a
shallow vertical/track-roof contact masked the barrel/hull side. Contact
selection now classifies the individual component pairs and resolves an
intersecting grounded side before a tangent roof contact. The shared rigid
body law serves visible-player impulses and worker-owned responses. Existing
landing-on-vehicle, gravity release, momentum and wall regressions remain
required. First body creation also records its real component bounds so the
next exact-client report can verify the exploded visual's alignment.

MT-3, MT-4 and HT-2 failed because `limittedTime`, `enemyImmobilized`, destroyed
track events and kill distance were unsupported. The server now records
compact admitted damage, critical-transition and kill events with the combat
clock, pre-hit immobilization, changed critical mask and available distance.
Repeated track breaks after repair remain distinct; unchanged states, friendly
damage and replayed projectile terminals create no additional enemy evidence.
Final death-state module destruction does not fabricate pre-hit immobilization.
The evaluator reads thresholds and honours from installed mission resources.
The fixture covers main/add expressions for these three tasks in all four
operations, including time and distance boundary failures.

The optional history survives all three receipt readers: server restart,
client wire validation and durable post-battle storage. It is excluded from
the native result serializer. Its per-actor cap is explicit; overflow leaves
an incomplete history rather than inventing success or exceeding the wire
budget. Old receipts retain absent evidence. Server restart validation also
now accepts the already shipped fractional stun seconds and optional older
interaction counters. Settlement remains under the existing exactly-once
owner; the regression delivers the same receipt twice across server recovery.
Other unimplemented mission predicates remain explicitly unsupported.

UDES 03 (Bot 26) in `080804` remains a diagnostic boundary. During the long
reported stationary interval its worker ground position/pitch is stable;
the report lacks Siege transitions and hydraulic-angle evidence. No native
mode oscillation or hardware fault is established. State-edge and existing
stall records now include mode, remaining switch time, intent, terrain pitch,
hydraulic pitch and gun pitch. Hydraulic laws are unchanged in this follow-up.
Shell/impact delay likewise remains diagnosis-only as requested.

The new focused collision, turret and mission regressions pass locally.
Full subsystem/CI and package results are recorded in PR #12. This remains a
0.9.0 test build, not a release. Actual #1513 Windows traversal, barrel visual
alignment and UDES mode/pose behaviour still require the new build in game;
pure geometry fixtures cannot establish those native runtime outcomes.

### All-map railing mechanism follow-up

The user clarified that railings also fail in Paris and across other maps.
The original compiled-skin recast was shared code, but its structure-only
guard still excluded item-wide fragile fences. Paris's bridge end, slope and
tile railings are fragile resources, not structure modules. The same bounded
recast now accepts registered fragile and falling objects as well as structure
modules. Item-wide acceptance removes only anonymous original destructible
materials 71--86 inside the exact object bounds; structures still require the
specific accepted module material. No map names or model families control the
production collision rule. Damaged materials, independent walls, overlapping
live objects, unrecognized materials and merged keys outside the accepted
object remain solid. Revoking a local prediction restores its collision.

The Paris fixture failed all four original-material variants before this
extension and passes afterwards. Catalog-driven tests now cover 750 placed
collider variants across all 40 shipped maps containing fence/gate/barrier
resources, including Paris bridge railings. Each uses a simulated native
callback with the shipped transform and bounds; these are mechanism tests,
not captured gameplay in all 40 maps. All five original Malinovka report rays
and their backing-wall/replacement regressions remain covered. A player
ground-query integration test also proves that the recast finds real support
below an accepted original skin and retains intact support.

The three interrupted actor-suite failures were unconfigured generic mocks
inventing the newly added optional ray adapter. Their legacy fixtures now
explicitly omit that adapter; no assertions were removed, and the real-adapter
ground integration is covered separately. The related collision, physics,
destructible, turret, rotation and mission suites pass 560 tests locally.
The complete client/launcher suites and Windows packaging run on PR #12.

### September 19 18:21 wooden-fence follow-up

Report `20260919-182128-7408d7bbfcb2` used build
`colorfulmeans-35429387931-1`. The user confirmed that the pavement seam now
works, but wooden barriers remain blocked in both tested maps. The report
contains six Paris and nine Malinovka hard-contact samples. Malinovka's
worker accepted destruction of the contacted fence modules, yet subsequent
rays still blocked. Paris's upper contacts do not fit a registered original
module box in the shipped catalog. Callback candidate lists contain ordinary,
original-destructible and damaged materials, but do not identify which one
produced the nearest hit. They are insufficient evidence for deleting a
replacement collider, enlarging an object bound, or relaxing a map's walls.

The shared compiled-skin traversal had a separate reproducible early-return
defect: after excluding one accepted original key, it returned the next hit
as solid without classifying it. A native traversal is allowed to prune
farther callbacks until the nearer surface is excluded, so the next key need
not have appeared in the first query. The traversal now processes bounded
intervals in near-to-far order, reclassifies newly revealed keys, and restores
the previous filter outside each owner's interval. All intervals share the
existing recast budget. Unknown or intact geometry and actual damaged/backing
walls still stop the ray. No map parameters or collision timers change.

A regression with pruned callbacks fails on the preceding implementation and
passes with this traversal. It also retains a real wall or damaged collider
behind two accepted skins and verifies the shared budget. This proves the
local early-return correction; it does not prove that all reported wooden
fence contacts have the same cause.

The existing stalled-motion diagnostic now replays the exact filtered ray
and tests surviving callback keys individually. It records their hit points,
distance from the reported contact, actual nearby instance boxes, and each
module's accepted/predicted state. Query stages and per-owner exclusions are
included. The diagnostic runs only at the existing two-second reporting
cadence, has bounded surface/owner counts, never destroys an object, and is
never consulted for a movement verdict. Its errors are contained to logging.
Further #1513 Windows evidence is needed to distinguish the remaining native
original-skin, damaged-geometry and catalog-placement cases.

### September 19 19:10 actual-contact follow-up

The user explicitly rejects all added vehicle collision clearance and collision
waiting. The `190613` upload contains launcher information only. The `191002`
report runs the previous `9baedc10` build (`colorfulmeans-35438296692-1`) and
contains five Paris hard-contact rays. Malinovka's improvement and remaining
contacts are user-observed evidence; this report contains no Malinovka round.

All five Paris replay rays hit original material 74 with flags 131 beside an
already accepted module. The replay agrees with the reported point, but its
anonymous item/chunk callback slots differ from the original query. Matching
the complete old callback tuple therefore fails to exclude the same original
surface. Four contacts belong to registered `(32384, 24)`, the fifth to
`(32640, 64)`. Their native vertical side faces lie inside the authored XZ
footprint but above its baked module bounds. The fixture retains all five
reported poses, hit points, ray endpoints and nearby owner states.

The common traversal now recognizes a transient original surface by its
material/flags only inside a proved accepted owner's interval. Registered and
baked wire identities never enter that alias class. When a vertical side face
has no 3-D owner, its exact authored XZ footprint can establish ownership;
every possible stacked owner must be registered and accepted for that material.
Unregistered baked owners, live neighbours, ground-facing normals and vertical
ground rays cannot use this fallback. It changes no collider or destruction
bound and invokes no new native API. Unknown, damaged and backing-wall hits
remain solid. This is a mechanism fix, without map names or coordinate gates.

The hull/contact paths now use the authored asymmetric body bounds and actual
frame travel. Removed physical padding includes the 0.5 m side guard, 0.075 m
contact skin, `max(0.4, abs(speed)*dt + 0.2)` native/catalog lead, airborne
0.2 m lead and the background scanner's 0.8--2.0 m proximity reach. The contact
helper no longer accepts an optional padding parameter. The native lateral
lanes preserve distinct left/right limits instead of mirroring the larger
side. No collision wait is added. The prior immediate accepted-collider swap
remains in place. Rotation uses the same unpadded contact helper; its true
edge speed still feeds the existing destruction eligibility law.

On the preceding implementation the new captured-face, transient-slot and
rotation-gap repros fail eight cases. The updated related suites pass 1,413
tests locally. Old tests requiring padding now assert actual body/frame bounds,
clear gaps and retained physical contacts. Additional controls cover walls
behind accepted skins, stacked/live owners, both turn directions, asynchronous
proposal/commit ownership, rolled body corners and asymmetric lateral travel.
Blocked turns at zero throttle now use the existing bounded stall diagnostic;
the two-second log cadence is not a movement timer. Windows package/CI results
are recorded on PR #12. Gameplay clearance in Paris/Malinovka and across all
maps still needs the new build on #1513; fixtures do not prove that acceptance.

### User-requested physics-parameter review (no additional tuning applied)

The `v0.8.4` tag already contains the old explicit clearance and many of the
physics approximations below. `vehicle_physics.py` differs from that tag only in the
later contact-normal filtering of deflection headings; `tank_collision.py` is
unchanged. This does not negate the user's later runtime regression, but it
does rule out calling all these constants newly introduced after 0.8.4.
The old building swap-hold was added in `624ead6a` and removed in `18110f50`;
the uploaded `9baedc10` build already includes that removal.

| Item | Current code behaviour | Review concern |
| --- | --- | --- |
| Pitched/rolled body projection | `_vehicle_pose_axes` changes Y with pitch/roll but retains yaw-only XZ; added in `c1dc3888` after v0.8.4 | This shear is not a rigid rotation and can overstate projected occupancy. The catalog and native lane paths must be reviewed together. |
| Native lane heights | All vehicles use 0.6/1.1/1.6 m probe heights | Sparse common-height probes are not each descriptor's full physical surface. This may miss a real surface or sample outside a smaller body. |
| Destruction eligibility speed | Powered contact can use descriptor top speed; a turn can use maximum traverse speed times the farthest-corner radius | These are eligibility shortcuts, not actual point-contact velocity. They do not overwrite vehicle speed, but can create drive/turn differences. The native damage formula itself is unchanged. |
| Hard-contact response | First slide speed is multiplied by 0.60, then by `0.85 ** (dt*60)`; a blocked speed uses `0.35 ** (dt*60)` | Tangential momentum and contact friction are replaced by fixed decay factors. Four grind ticks control repeated entry damping, not a four-tick movement wait. |
| Deflection directions | Try yaw offsets +/-0.55 and +/-1.0 radians in a fixed order | The wall tangent can lie between all four probes; grazing contact can become a stop. The newer normal filter prevents inward escape probes but does not derive the actual tangent. |
| Steep uphill drag | Above 27.5 degrees and 0.5 m/s, add `10*(tan(grade)-tan(27.5deg))*12.2625` m/s2 of braking | This is about 7 m/s2 at 30 degrees and 22 m/s2 at 35 degrees, additional to slope gravity/rolling resistance. It is an offline calibration, not a recovered native force law. |
| Downhill overspeed | Limit to 105% of descriptor speed; grow surplus by only `0.20*sin(grade)` m/s each second, conditional on throttle | Explicit cap and throttle-dependent surplus replace part of the gravity-integrated result. |
| Neutral braking | Apply 65% of grip braking in addition to rolling resistance, fading near the parked slope limit | The release curve is an offline approximation. It needs original-client comparison rather than being labelled a collision fix. |
| Suspension contact/freeze | Pseudo contacts count with up to 0.10 m separation; small vertical/angular speeds are zeroed under acceleration thresholds | Can affect hovering/settling feel. These are ground-support rules, not the removed horizontal fence padding; changing them requires suspension evidence. |
| Rotation interval box | At most 5 degrees per slice; use enclosing axis bounds of all rotated corners | An enclosing rectangle includes empty corners outside the exact rotational swept set. It is not an explicit clearance constant, but can produce a conservative false contact; a precise narrow-phase review is warranted. |
| Tank-to-tank shape/receipt | Symmetric chassis half-extents have minima 0.8 m/1.0 m; ram receipt matching allows 0.75 m | Asymmetric bodies can be enlarged; receipt tolerance is not the physical collision shape. These paths are separate from map-object contact and remain unchanged for the user's decision. |
| Soft-skin traversal budget | Four newly excluded original-surface classes per traversal | Exhaustion retains the unresolved native hit as hard. This is a bounded-query policy, not elapsed-time waiting, but dense accepted skins merit an adversarial traversal review. |

Numerical SAT tolerance (1e-7 m), spatial-index broadphase padding and callback
identity matching tolerances are not automatically physical clearance. Their
consumers must be checked before changing them. The recovered 1.25 g arcade
gravity and client-authored grip curves likewise must not be called accidental
physics bugs merely because they differ from real-world SI behaviour. The user
will choose any further physics changes after reviewing these findings.

The first full CI run on `0054dc70` ran 5,998 tests and found ten assertion
failures, all in seven downhill-departure tests whose synthetic rays retained
the previous lead. No production padding was restored. Those controls now
exercise both the original short physical frame (clear without a spurious
ground graze) and a longer actual step reconstructing the captured lane; all
original ground-top, backing-wall and upper/low-wall assertions remain. The
1,413 related cases and these seven departure cases pass locally. The follow-up
commit changes tests/documentation only; full CI/package evidence is pending.

## Release-pause investigation: 2026-09-20

The user paused v0.9.2 publication to investigate track direction, both-way
Siege transitions, drowning warnings and sustained stacked-body damage.
The release notes remain a draft and no release/tag is authorized while that
pause remains in effect.

### Track direction and braking before mode switches

The hull transform uses forward `(sin(yaw), cos(yaw))`, so positive yaw is a
right turn: the left side advances and the right side retreats. The shared
`vehicle_physics.track_scroll` previously applied the opposite signs to the
yaw contribution. It now returns `v + omega * trackCenter` on the left and
`v - omega * trackCenter` on the right, retaining the existing scroll cap.
Local `updateTracksScroll`, auxiliary physics, Bot `setExternal` and remote
pose-derived turns all consume that same ordered pair. The finite-difference
regression independently transforms the left/right hull points through an
actual rotation, including forward/reverse motion and multiple track gauges.
All four Siege-capable vehicles also exercise both active descriptors and
both presentation feeds. This proves the supplied speeds, not native animated
rendering on Windows.

Previously the local switch request immediately set the pending-drive lock;
the next motion step therefore zeroed speed before any braking took place.
The local pending brake intent is now separate from the sent request. It
suppresses drive/turn input while the existing longitudinal handbrake and
traverse deceleration settle motion. At zero longitudinal and yaw rate, one
input contains the stopped pose and mode request. Only then does the existing
acknowledgement lock and server-owned transition timer apply. Cancellation,
death and teardown clear the unsent intent, and failed enqueue can retry.
The server checks the speed in that same admitted input, not an older packet.
Bots likewise retain the current descriptor and normal motion integration
while braking after their existing intent debounce. Neither switch direction
adds a new braking coefficient, delay or wire field. Airborne motion remains
governed by the existing physics. Regression coverage includes all four
vehicle types in both directions, reverse travel, a pivot, cancellation,
failed enqueue, death and Bot behaviour.

### Drowning evidence and vehicle-specific warning thresholds

[Wargaming Wiki's Battle Mechanics](https://wiki.wargaming.net/en/Battle_Mechanics)
describes the icon as a warning for water deep enough to enter the crew or
engine compartment. It does not specify the warning sensor's exact point or
height. The Wiki is community-maintained material hosted by Wargaming, not
proof of the private 0.9.22 server implementation. The
[official 9.14 physics article](https://worldoftanks.eu/en/news/general-news/version-914-sounds-physics/)
and [official 9.14 patch notes](https://worldoftanks.com/en/content/docs/release_notes/914-updatenotes/)
confirm the server-owned physics change and reworked vehicle collisions;
neither supplies a drowning-warning height or continuous crushing HP formula.
The pages were read in the browser because the text fetch exposed only their
loading page. Forum searches and the Wiki discussion did not provide a
verifiable 0.9.22 experiment establishing those missing values.

The user's subsequent September 21 retail-server experiment supplies the
missing product rule: no warning below half the hull, CAUTION above half,
and the existing ten-second DANGER countdown above the hull top. This is
recorded gameplay evidence and an explicit implementation instruction, not
a claim that a public article disclosed a private server constant.

`water_geometry` now derives those two heights from the actual descriptor's
`hull.hitTester.bbox` and `chassis.hullPosition`. It transforms all eight
corners through the current yaw, pitch and roll, including the existing
descriptor-owned hydraulic body pose, and samples the water above the
resulting world-space hull bottom. The visible player, copied human
worker pose and Bots share that geometry and classification. Neither a
turret mount nor a universal metre threshold determines the vehicle's hull
height. Missing or invalid hull geometry does not invent a threshold.

The appearance effect's `isInWater` and `isUnderwater` flags no longer override
these gameplay thresholds. The stock Avatar receives `VEHICLE_DROWN_WARNING`
from the server; a splash effect is not the authority for that event. The
`0.5` in `assembleWaterSensor` remains a minimum heavy-splash depth and was
not used as evidence for the user's independently observed half-hull rule.
The existing ten-second danger duration and worker/server authority remain.

### Requested sustained crushing states

These are the user's requested acceptance cases, not independently verified
retail formulas. "Damage" below means sustained pressure damage after contact;
it does not replace an initial landing impact or horizontal ramming event.

| Upper body | Lower body | Required sustained recipients |
| --- | --- | --- |
| Live vehicle | Live vehicle | Both vehicles |
| Wreck | Live vehicle | Lower vehicle only |
| Detached turret | Live vehicle | Lower vehicle only |
| Live vehicle | Wreck | Neither |
| Live vehicle | Detached turret | Neither |

When the upper vehicle dies, its still-supported wreck must continue damaging
the live lower vehicle. When the lower vehicle dies, damage to the live upper
vehicle must stop. A detached turret must use its actual supported contact,
not an arbitrary nearby wreck or horizontal overlap. Removing that support
must stop sustained damage. Vehicle death, turret detachment, repeated worker
publications and a new battle must not duplicate a damage interval.

Current hull ramming uses horizontal closing velocity and deliberately admits
no wreck ram events. Detached turrets already have worker-owned compound-box
contact and support, but neither path produces sustained crushing HP. Applying
an invented minimum impact speed to `ram_damage`, or treating `mass * gravity`
as HP per second, would not recover the missing retail law. The exact pressure
damage rate, mass/armour dependence and any initial grace period still need
0.9.22 source or controlled replay/video evidence before this can be claimed
as an official-mechanics repair. No new crushing coefficient is enabled here.

### September 21 post-0.9.2 report follow-up

The eight submitted report archives contain four distinct 0.9.2 sessions
and an older 0.9.1 session; repeated archives from the same session are
cumulative evidence, not independent reproductions. The following repairs
are based on source contracts and report replay. None constitutes native
Windows gameplay acceptance.

- Unsupported hulls no longer obtain track-powered yaw from A/D. Existing
  angular momentum is retained, while rebasing forward/lateral components
  preserves the world-space flight trajectory.
- Local Siege presentation no longer freezes an unsynchronized native
  body/ground world translation as a permanent local offset. Copied chassis
  placement and the descriptor's hydraulic pivot own the rendered and
  collision poses. Autorotation checks the gun's local target direction in
  the pitched/rolled hull, retaining stock autorotation/X-lock ownership.
- The first candidate removed the existing neutral drivetrain brake. The
  user's subsequent retail test confirmed that releasing the accelerator
  does decelerate the vehicle. The follow-up below restores that behavior
  while correcting the separately identified downhill speed ceiling.
- Bot arrivals, tactical holds and traffic yields now request active braking
  explicitly rather than depending on released-throttle drag. Stopping
  distance integrates that same brake law with signed speed and slope;
  checked forward/reverse escape commands clear any inherited brake request.
  Close-target and crowded-departure regressions retain their original
  stopping-distance and recovery-duration limits.
- Airfield's recorded trapped pose has no exit in the original forward
  fallback fan. A rear exit is now considered after the entire forward fan
  fails, using the same terrain, hazard and collision checks. A driver's
  static-probe refusal also reviews the affected baked corridor even when
  zero throttle prevents an actual movement contact. Failed probes and
  traffic holds are not evidence for marking solid terrain.
- Newly published wrecks invalidate private join/recovery paths as well as
  shared paths. A Bot that has reached a route's final segment can resume
  advancing to the real capture objective after contact ends without first
  returning to the penultimate waypoint. No speculative aggression or map
  coordinate changes are included.
- The Westfield 11:41 report contains eleven retained contact witnesses.
  Ten lie inside exact, already-broken component boxes in a proved remapped
  chunk. Their bevel/top normals no longer disqualify that ownership proof.
  The remaining face lies outside all recorded component boxes and remains
  blocking. No inflated box or nearest-owner guess makes it passable; bounded
  diagnostics now include its live slot signature/category and mapping state.
- Wreck pushing has no accumulated-distance cap. Pure worker-path tests
  continue pushing through successive intervals beyond ten metres and query
  new obstacles from the current wreck position. New bounded diagnostics
  distinguish track hold, a world blocker, missing support, a support step
  and actual movement. They do not change unverified friction coefficients.
- Hydraulic diagnostics retain bounded takeoff/landing transition samples
  with preceding and current support/motion evidence, alongside worst-frame
  samples. The submitted aggregate windows omit the actual airborne frames,
  so they do not yet distinguish all downhill stutter from real steps.

The official [9.14 physics description](https://worldoftanks.eu/en/news/general-news/version-914-sounds-physics/)
distinguishes released drive input from SPACE braking and describes
SPACE-plus-turn single-track manoeuvres. It supplies no numerical coast
coefficient or pressure-damage law. Those limits must not be described as
restored official physics merely because the pure-data regressions pass.

The personal-mission follow-up adds the missing evidence used by LT-6,
LT-9, MT-12, HT-5 and TD-4 across all four operations. Reference expressions
are labelled as 0.9.22 RU #788, not a replacement for the installed #1513
mission resources. LT-6's prebattle optional-device check reads the compact
vehicle descriptor frozen when the battle starts, including normal optics,
bond optics and binoculars. A later garage edit cannot change that check.
LT-9 records detection before the observer has ever been spotted, with both
teams' simultaneous visibility transitions considered before counting.
MT-12 records each admitted fire ignition once. TD-4 records invisibility at
the relevant damage/kill event, using enemy visibility rather than the
sixth-sense display delay. HT-5's `distance=0` means damage inside the current
vehicle view range, not unlimited range: worker observations donate the same
effective radius already used for visibility, including crew/equipment
state. The newest admitted radius and actual distance are frozen in the
mission event. The visibility sampling boundary (up to one normal 0.4-second
observation interval around a loadout-state change) remains a native
acceptance limitation.

Version 3 extends the existing bounded event history and preserves legacy
v1/v2 receipt reads without inventing missing event-time evidence. Tests
exercise reference conditions for every operation, event boundaries,
re-ignition after extinguishing, loadout freezing, server restart and client
receipt persistence. Mission thresholds and rewards still come from the
installed client; this change does not edit them.

The official [9.18 matchmaking article](https://worldoftanks.eu/en/news/general-news/matchmaking-918/)
allows three-tier battles in which the player is at the top, middle or bottom.
Waiting-room options now include `0/+1/+2` and `-2/-1/0`. Random selection
admits a wider candidate pool but chooses only one contiguous one-, two- or
three-tier window, considering all configured human tiers before selecting
it. Automatic substitutions cannot escape that window. Existing manual
presets and explicitly selected human vehicles remain user-owned. The
existing offline tier/class proportions are unchanged and are not claimed
to implement the complete retail 3/5/7 matchmaker.

### September 21 retail-observation corrections

The subsequent user tests supersede the earlier neutral-drive assumption.
Released throttle once again applies the existing offline drivetrain brake,
including its slope unloading near the static hold limit. Its 0.65 grip share
is retained calibration, not a newly established retail coefficient. Explicit
braking remains stronger and airborne motion remains inertial.

Restoring that brake exposed a finite Bot-yield deadlock in the recorded
Himmelsdorf departure case. A yielding vehicle repeatedly requested a fresh
full escape corridor after most of its admitted manoeuvre was complete; a
wall beyond the intended endpoint stopped it before it could clear the lane.
Traffic checks now use the remaining translation of that existing yield.
Dynamic checks retain the entire swept hull, and native motion receipts still
cover the leading hull plus the current slice's reach. The original 15/24 FPS
departure distances and parking/recovery duration limits remain unchanged.

The former 1.05 downhill envelope and separate artificial overspeed build/drag
are replaced by force-integrated gravity and a **user-authorized approximate
1.35 envelope**. Engine acceleration remains limited by the installed
descriptor's rated speed; gravity can carry a grounded vehicle to 1.35 times
the current directional limit. Forward, reverse and the active Siege-mode
descriptor use their own limits. Thus a configured 45 km/h limit admits
60.75 km/h downhill; no T-34-85 vehicle limit is hard-coded. An official
[T71 article](https://worldoftanks.com/en/news/general-news/tank-month-t71/)
documents downhill travel above its rated speed, but predates the 9.14 physics
revision and supplies no universal multiplier. Neither that article nor the
user's single-vehicle experiment proves that 1.35 is the retail server law.

The increased moving-target speed also exposed an SPG proof prediction error.
A new lead can shorten the ballistic flight and require fewer collision-query
segments than the preceding proof. The prediction now uses observed time per
segment and the new arc's actual workload. The existing four-query shared
frame budget, 1.5-metre stale-target limit, frozen launch trajectory and random
dispersion are unchanged. Eight-SPG tests retain their original 20-second
completion bound at 20, 24, 30 and 60 FPS.

#### Descriptor-owned track pivot

Single-track low-speed steering is not inferred from a vehicle-name list.
The 0.9.22 chassis reader exposes `rotationIsAroundCenter`, consumed by the
reviewed #1513 sniper autorotation contract. Public
[0.7.0 source](https://github.com/StranikS-Scan/WorldOfTanks-Decompiled/blob/0.7.0/source/res/scripts/common/items/vehicles.py)
already contains that field; this establishes that the distinction predates
9.22, not its first release date. The later
[9.14 SPACE-plus-turn manoeuvre](https://worldoftanks.eu/en/news/general-news/version-914-sounds-physics/)
describes a separate moving handbrake turn and is not evidence that this flag
was introduced in 9.14.

For a chassis whose flag is false, the hull centre now follows the stationary
inner track when the ordinary differential motion would reverse that track.
The pivot width comes from `physics.trackCenterOffset`; a missing width does
not fall back to a guessed hull width. Vehicles whose flag is true retain
centre rotation. Track animation, local movement and Bot movement share this
geometry. Airborne yaw cannot create the new ground-driven translation.
Collision sweeps cover the curved root path, including trees, catalog/native
obstacles, vehicles and detached turrets. This does not claim to complete the
separate moving handbrake-drift simulation.

#### Local projectile ownership and the stock aim setting

With player-owned projectiles, the native local marker is the trajectory
source whether the stored `useServerAim` preference is enabled or disabled.
The exact #1513 `VehicleGunRotator.start` and `applySettings` both write the
`showServerMarker` property. During an offline battle the compatibility layer
passes `False` to that property's original setter and returns client-only
selection from `gun_marker_ctrl.useClientGunMarker/useServerGunMarker`.
The stock arena-start handler reads both helpers; its later visibility writes
must agree with the rotator, including when the property was already false.
Teardown restores the original property and helpers without writing settings
persistence. The setter keeps
the audited `enableServerAim` and `AvatarInputHandler.showGunMarker2(False)`
lifecycle; the latter selects the client marker in the release single-marker
mode. Target lock still owns `clientMode`.

The worker no longer computes player gun markers. Inputs no longer publish
marker-only stabilised-pose checkpoints, and snapshots carry no delayed marker
reply. Firing still freezes the current native barrel ray and dispersion once,
including the native hydraulic pose supplied by the rotator. No worker reply
writes the mutable dispersion list or competes with native convergence.
Source/contract tests cover startup, settings changes, restart and restoration;
actual Windows marker presentation remains an exact-client acceptance boundary.


### September 21 Airfield Bot approach: cold props and roof support

The reported symptom is a stationary Bot in front of a destructible house.
Two approach-gate defects can produce it independently of actual destruction:
`_catalog_soft_static_path` previously searched only proximity-registered items,
while the Bot proximity body scan requires motion; `_direction_probe` treated the
first vertical hit at its lookahead endpoint as ground and could veto an
unreachable house roof before considering the horizontal soft-wall path.

The shared planning resolver now selects bounded candidates from the existing
baked spatial bins and reuses `_stream_baked_shot_instance_1513` for the exact
live wire, name, matrix, descriptor and effect-category checks. Baked geometry
alone cannot authorize clearance. Registration and extra casts use the existing
shared soft-recast budget; an unavailable stream/budget yields a retryable result.

Only an unreachable roof owned by a live-validated, kinetically crushable item
can be excluded from a planning ground column. The recast covers the complete
original column with the existing vehicle flags and exact original-material
filter, not a jump to the OBB exit. Ground absence, slopes, deep water, uncrushable
items and backing/replacement geometry remain vetoes. Reachable decks and
physical suspension probes are not softened. Actual contact still performs the
original destruction law; planning never calls a destruction RPC.

Regression geometry uses the shipped `31_airfield` small village house's boxes,
with controlled placement, health and native query responses. It proves the
Python approach/driver boundary, including forward commands at 60/15/5 Hz,
not the user's exact unlogged position or native Windows #1513 gameplay.

### Post-0.9.3 Airfield navigation and directive purchases

The `20260921-225339-7e2477662ca8` and
`20260921-234319-c7cf068c7df3` reports both identify semantic version 0.9.3,
build `colorfulmeans-35611837404-1`. The published ZIP's navigation source
matches the current release tree after Windows newline normalization; this
follow-up is not based on an assumed older installation.

In the Airfield report, 379 of 412 motion samples have pending navigation and
403 have clear world-motion checks. Six recorded stationary positions lie in
cells without a baked height. The baked corridor can snap its start to a
nearby supported cell, but its native-review gate previously checked the
original unsupported cell and rejected the candidate before consulting live
ground and collision evidence. Report-position regressions reproduce this
failure even with a clear native corridor.

Reviewed boundary joins now check the actual world-space start and proposed
endpoint through the existing live support, slope and obstacle probes.
Missing support, real walls and excessive slopes remain vetoes. No map
coordinates or physical collision laws are changed. The regression's live
query responses are controlled fixtures; they do not establish that every
native obstacle at those positions is passable.

A separate deterministic lifecycle regression shows that queued searches can
stop progressing while every Bot reuses a cached/direct movement decision.
The authority control refresh now advances the existing navigation queue once
per frame. The original elapsed-time credit and same-frame idempotence remain
in force; this does not increase its search budget. The report itself records
ongoing search work, so this independent defect is not claimed as the sole
cause of its stationary vehicles. Bounded motion diagnostics now include the
actual grid cell, available baked height, pending age and search progress.

The second report identifies CMD 308 and `BattleBooster<intCD:27131>` rejected
by the ordinary-equipment slot validator. The six-field command width is
already audited above, but the report does not retain the slot field's value.
The repair therefore uses the installed descriptor's `equipmentType` to select
the unique directive slot in the existing four-slot representation. Ordinary
consumables retain their requested slot and all requests retain slot bounds
and descriptor validation. It does not claim a newly audited GUI-local index.

Buying and mounting one item also previously replaced the entire desired
consumable layout with the currently loaded items. That erased unrelated
consumed items' resupply targets and signed currency choices. Only the chosen
slot's desired layout now changes. Transaction, inventory publication and
save/restart regressions cover direct purchases, existing stock, replacement,
unmounting, insufficient bonds and invalid requests; failed transactions
retain the original balances, stock, fitting and layout.

### Shared spotting: verified behavior and historical limits

The official [Spotting and Concealment support article](https://wargaming.net/support/en/products/wot/article/10222/)
confirms that allied position sharing requires radio contact. It does not
specify assistance attribution at shell launch versus impact. Search-index
excerpts of the Wargaming-hosted [Battle Mechanics Wiki](https://wiki.wargaming.net/en/Battle_Mechanics)
describe splitting spotting XP between spotters when the shooter cannot spot
the target itself; its full article was blocked by a verification page during
this review. That excerpt is not evidence for the exact historical HP or
integer-rounding algorithm.

Existing server regressions confirm that 240 damage is shared as 120/120
between two direct spotters, a single spotter receives the entire assistance,
and a shooter spotting its own target grants no radio assistance to others.
For 241 damage, the current stable-order 121/120 split is an offline accounting
choice, not a recovered official rounding contract. Existing in-flight shell
and assisted-kill behavior remains unchanged.

No spotting behavior is changed by this follow-up. A proposed per-shooter
radio filter was not retained because a newer observation after the shooter's
death removed its radio relationships before an in-flight shell hit; public
sources did not resolve the correct historical attribution time. Other
unverified boundaries are shared initial-discovery counts, assistance during
retained visibility without direct observation, and simultaneous tracking and
spotting assistance. The existing implementation must not be described as a
complete reconstruction of the 0.9.22 proprietary reward rules.

### September 22 report follow-up: hydraulics, navigation, missions and sight

The five supplied September 21/22 reports identify the released 0.9.3 payload,
`colorfulmeans-35611837404-1`. They do not contain the post-release Airfield and
directive repairs described above. This candidate includes those repairs and
the continuous, native-proven occupied-cell egress from the parallel Airfield
follow-up; it does not change the release version.

The `040924` traceback reaches `_drive_local_step` before the first hydraulic
ground sample. `_local_legacy_support_sample` now belongs to the constructor,
start and stop lifecycle. The first frame has no prior support; a new round
cannot reuse the previous map's support. Regressions set the descriptor's real
`isPitchHullAimingAvailable` guard and exercise all four retained Swedish TD
descriptors in both travel and siege modes.

The Highway report repeatedly rolls T71 back at a hull height near -11.103 m
while the sampled centre support is near -5.151 m. The Bot physical callback
was using the broad placement column, which could select the overhead bridge.
It now uses the existing near-body support column shared with player physics.
This changes support-layer selection, not allowed climbing grades or collision
clearance. Physical support and final-pose rejection must also reach the
navigator: a clear horizontal sweep cannot clear a blocked-contact episode
before the full pose has been accepted. Missing baked occupied cells retain
bounded native support, slope, hazard and collision proof before joining an
existing graph cell; no vehicle is snapped or teleported onto a route.

The LT-12 report explicitly rejects `damageAssistedRadioWhileInvisible`; the
HT-12 report rejects `compareWithMaxHealth`. Radio-assistance evidence now
freezes each awarded share and whether the observer is visible to the enemy
at that event. The mission evaluator uses this history for LT-12 and the
frozen battle-start maximum health for HT-12. It does not substitute total
assistance or remaining HP. LT-12's first-campaign secondary condition also
requires matching seasonal camouflage as well as a camouflage net; fitting
evidence is taken from the battle loadout. Historical receipts missing the
necessary evidence remain unknown rather than retroactively inventing it.

Some accepted destructible removals leave their original compiled BSP skin
under a different item slot from the repaired live WGDE instance. Movement
already had bounded ownership checks for this case; spotting previously used
only the destruction ledger callback. Sight rays now use the same proved
original-skin recast, retaining their existing 0x80 skip mask. Movement keeps
0x50. Intact surfaces, unidentified geometry and replacement/backing walls
remain blockers. This does not assert that every small prop is transparent or
that shell collision and spotting have identical rules.

The apparent obstacle-width report is not resolved by globally shrinking
collision geometry. Catalog contacts use compiled collision bounds, not a
triangle-level narrow phase; the supplied evidence does not identify a safe
per-model correction. Rate-limited `CATALOG CONTACT` and `SIGHT CONTACT`
records now include the actual hull sweep or sight ray, object identity and
bounds, and native surface evidence where available. They distinguish a
catalog-box contact from a native surface and do not alter destruction state.

The focused fixtures establish Python lifecycle, accounting and collision
filtering behavior. Native #1513 entry into battle, bridge/wreck driving,
obstacle outlines, spotting and frame pacing still require Windows gameplay.

### September 22 Airfield follow-up: moving fallback targets

Report `20260922-104150-b9961a81ef4a` runs the delivered candidate
`colorfulmeans-35678689450-1`, not the earlier release. Its first round is
Airfield; the later Himmelsdorf round reuses Bot IDs and must be analyzed
separately. The reported pair is Object 244 (18) and SU-122-44 (27).

The shipped Airfield graph and the SU's recorded start reproduce its exact
first fallback endpoint, `(-287.264106, -0.18, -188.348202)`, under a stated
flat, clear native-query fixture. After a failed search the endpoint was
reselected on every decision: moving the hull 0.2 m also moved the endpoint
0.2 m, keeping the short target 2.08 m away and allowing its direction to
change at cell boundaries. Pending searches already retained a usable target;
the failed-search branch did not. Local fallback endpoints now belong to the
route and recovery intent that selected them and remain fixed until arrival
or invalidation. Current geometry, hazards and Bot-specific edge penalties
still constrain reuse. A new strategic intent, normal path or recovery must
not inherit an unrelated fallback.

The 10:34:23 Object 244 sample has no physical contact pair, but replaying the
two recorded hull boxes through the production traffic guard rejects its
requested positive rotation. The old stall record printed the planner's
`traffic=none`, hiding this later `vehicle_brake` verdict. Diagnostics now
retain the already computed controls before and after the traffic guard,
the final motion controls and the actual traffic mode. Recording this adds
no native queries and retains the existing per-hull cadence.

Moving in a small circle previously reset the stationary log after each
0.5 m displacement, so a long loop could leave almost no motion evidence.
An additional movement-intent diagnostic samples at most once per 15 seconds
while the hull remains within a 12 m local region. Straight departure resets
that region, tactical holds do not arm it, and stationary reports retain their
existing three-second cadence. This observer does not change driving.

The report also records a live-worker window at 8.30 FPS. Sampled navigation
fallback work is only part of its cost; physical motion queries dominate the
slow frames. This change is not a demonstrated frame-rate repair. Positive
native answers can precede chunk streaming, so failed-search retries retain
their existing geometry revalidation. The report-position fixtures establish
target ownership and traffic attribution. A continuous two-Bot fixture with
real A* can leave the area after delayed planning; forcing A* to fail forever
still exposes local minima in the short fallback. Thus this repairs the
reproduced moving-target defect, not every cause of circling. Only a new
exact-client Windows playtest can establish that both vehicles leave the
native scene reliably.

### September 22 Airfield follow-up: circling at spawn

Report `20260922-113632-50a443198d84` runs the next delivered candidate,
`colorfulmeans-35681498572-1`. All three rounds are Airfield; Bot IDs repeat
between them. Of 68 local-motion observations, 67 have no traffic restriction,
all have clear or crushed world motion, and none records a support/pose
rollback or contact pair. The new evidence therefore cannot be explained only
by mutual vehicle avoidance. Local-motion observations establish slow local
progress, not necessarily a closed circle.

Two Object 730 samples retain the same nearby target while driving and
turning continuously. Their recorded movement, speed and timestep reproduce
26 degrees/second hull traverse. The target's arrival disk lies inside the
current turning circle, so full throttle can orbit it. LocalDriver now uses
the installed traverse limit, including stun, to recognize this geometry and
brake through alignment until the forward ray intersects the arrival disk.
No vehicle coefficients change. Hold, reverse, recovery, a changed target or
unavailable physics clear the alignment state. Ordinary steering probes are
bounded by the selected waypoint plus the leading hull and decision travel;
explicit recovery probes and remembered live-vehicle blockers retain their
own checks. The realised motion gate remains authoritative.

A recovery search used to survive the two-metre displacement that ended its
recovery, then consume search credit alongside the replacement route search.
That private job now retires with its recovery. A pending A* search also
exposes already admitted dry parent edges as a stable prefix, allowing a
validated detour before the complete route is available. The prefix belongs
to its search and request, checks the unsnapped current position and penalties,
and is retired on cancellation, completion or invalidation. Reaching another
temporary greedy waypoint no longer continually resets strategic no-progress
detection. An arrived short fallback point anchors the next leg, rather than
re-centering every leg on a hull still inside the same eroded cell; the actual
hull-to-new-target connector must pass the complete safety checks. Search
limits and native-query budgets are unchanged.

Shared native navigation previously rejected intact crushable props while
the driving probe admitted them using the stock kinetic gate. A shared edge
now requires the stock proof for every distinct physical profile in the
complete roster, including both siege modes and both directional speed caps.
It reuses one unique live-object identity check and an exact material-filtered
recast, retaining unknown objects and backing walls. This is a conservative
common planning capability: a slow or incomplete profile can keep the edge
blocked for everyone, and actual motion still owns impact and destruction.
Roster or descriptor changes invalidate dependent navigation proofs. A spent
recast budget defers the search edge until the next frame without caching a
false wall or repeatedly probing that same edge in one frame.

The LAN poll also discarded a sparse `bot_orders` section when a later lean
snapshot arrived in the same batch. The server had already marked the section
sent, so the worker could keep an old reached waypoint until a later resend.
Coalescing now carries the newest complete order section into the latest
physical snapshot within the same round, authority and map. Explicit empty
orders, revision ordering, event boundaries and bounded overflow are covered.
The report has no complete order-delivery trace; this independently reproduced
loss is not asserted to be the only cause of its delayed target updates.

Regression coverage includes recorded-pose driving, slow and fast traverse,
left/right targets, track-centred pivots, real blocked corridors requiring an
initial detour, deferred geometry, retained backing walls and the actual LAN
poll/overflow path. These establish local control and lifecycle behavior.
The report's 8.35 FPS window is still dominated by physical motion queries;
this change does not establish a frame-rate repair or native Airfield gameplay
acceptance. Exact #1513 Windows driving remains the acceptance boundary.

### September 22 Airfield follow-up: remaining local turn loops

Report `20260922-122105-7421ade854f4` runs `colorfulmeans-35685281060-1`.
The user confirms more Bots now leave spawn, while a few keep turning locally.
The report supports both observations: T71 and Achilles travel hundreds of
metres, while Panther II (7), M36 (9) and SU-122-44 (27) repeatedly receive
nearby changing targets. Of the 152 motion diagnostics, 138 are world-clear
and 12 record successful crushing; no support/pose rollback or baked veto is
recorded. These are stall-triggered samples, not a representative estimate of
the proportion of all vehicles that stop.

The history does not support reverting the entire AI to 0.9.0. The Airfield
bake and its reader are unchanged since that release. `d3fbfa5d` added native
review of contact regions, and `d6038e00` extended the triggers for that review.
Those changes make pending and failed searches more consequential.
`17ce88f6` made a requested stop apply the copied brake, while `40d1b5ef`
introduced traversal-aware near-target alignment and consumption of pending
A* prefixes. Isolating the driver against 32 fixed-target samples from the
new report makes the current driver reach all 32; older drivers do worse in
the same controlled physics fixture. This does not reconstruct old native
gameplay; it supports preserving the effective alignment repair while fixing
the navigation targets.

Two navigation ownership errors are independently reproducible. A private
route-join retry still used the old spawn anchor after its vehicle had moved.
Consuming the retry's early prefix therefore sent the vehicle back toward
spawn: a hull at z=20 targeting z=100 received z=4 after two search steps from
the old z=0 anchor. Private jobs now start from the actual vehicle position
when created or retried; existing usable paths are still reused. Shared route
geometry retains its authored anchor, but its unfinished exploration is not a
vehicle movement command. A private exploration tree that has not reached a
moving hull must likewise not pull it back to the tree's trailing endpoint.

The second error treated each new pending prefix as a new progress episode.
Ordinary failed-search retries could occur before the existing twelve-second
no-progress interval, repeatedly renewing it without actual departure. A
closed-room fixture remained stationary for sixty seconds through seven
failed attempts with no macro recovery. Temporary prefix/fallback progress
now belongs to the strategic request and measures actual entry into new
navigation cells. Repeated exploration of the already visited local area
cannot renew the interval or keep issuing a local turn loop; a newly proved
exit or a completed path can immediately resume travel. Healthy A* work is
retained rather than canceled by another short escape lease.

A separate real-callback fixture proves that deferred native review retired
an otherwise complete cached route and queued a new search. Normal destruction
events and failed-search retries can invalidate native receipts, so this is
not limited to manually clearing a cache. Unknown review now retains the
route object and holds its current index/target without creating another job;
fresh proof resumes that route, while a definite stock-physics rejection
still retires it. This does not authorize motion through an unproved edge.
The report lacks per-event deferred-review evidence, so this independent
defect is not asserted to explain every recorded turn loop.

The closed-loop regression uses the report's three vehicle mobility samples,
the real adapter/driver and copied physics at five and fifteen control frames
per second. A fixed U-shaped wall blocks both planning probes and integrated
hull motion. Three genuinely bounded A* failures precede ordinary search
capacity; the wall never opens or changes. All six runs with the preceding
navigation fail to reach the goal within 120 seconds; the repaired navigation
leaves the mouth and reaches the goal in all six, without crossing the wall.
The wall and unreported speed limits are controlled fixtures, not a recreation
of every native Airfield contact.

The new report's two steady windows are about fourteen worker FPS, compared
with about eight in the preceding report. Different rosters and scenes prevent
treating that as a controlled benchmark. It does not support a global budget
starvation explanation: completed searches increase and the pending count
falls. The remaining native driving and frame-pacing boundary is unchanged.

### September 22 Airfield follow-up: reached slope points and obstacle ownership

Report `20260922-133334-f5eb61b94d18` identifies the installed and bundled
`colorfulmeans-35688896773-1` payload. It contains one Airfield round and
854 stall-triggered motion samples. Twenty-five samples record successful
crushing and twelve record hard contact; none records a support rollback,
pose rollback or baked motion veto. These samples do not measure the fleet's
overall stop rate. Multiple vehicles travel hundreds of metres, while several
others remain local or return after initially leaving.

E25 (25) repeatedly receives a point it has already reached. At the recorded
position `(-142.690, -13.463, -125.663)`, its next issued point
`(-142, -13.353, -126)` is only 0.768 metres away. The following baked edge is
dry and climbs at a grade of about 0.171. The pending-prefix follower still
applied an unreached-corner alignment check to this consumed point. The
complete-path follower already handles that arrival correctly. Using its
adjacent-edge arrival rule for pending prefixes allows the next checked leg;
unfinished searches must still not grant a shallow-water ford or skip an
unreached climbing turn.

Panther II (7) and M36 (9) have a separate pattern: repeated failed searches
and the temporary-loop hold, with the held target equal to the hull position.
Their requested target and the E25 target remain connected to the recorded
positions in the actual baked graph, including its existing shoreline
hazards. Real A* on that graph finds paths within the normal expansion limit
when no additional native obstruction is injected. A disconnected baked
island is therefore not the explanation; live obstacle classification matters.

The shared stock-crushability intersection can turn a vehicle's own soft
obstacle into a navigation wall. Controlled real-callback fixtures reproduce
both a forward-capable vehicle rejected by its lower reverse cap and a heavier
vehicle rejected after a lighter vehicle joins the room. The fixture material
health and speed values are explicit controls, not measurements of an object
in this report. The report alone does not identify each failed native edge.

Native planning now receives the consuming vehicle's immutable mass and
directional powered-contact cap. The existing mounted travel-mode cap is
shared with the local direction probe and final contact gate; actual impact,
health, scale and destruction still use the existing stock laws. Native edge,
segment and route receipts carry that capability, while baked topology and
ground samples remain shared. Resumable A* captures its capability rather than
reading a mutable current-vehicle field. A lighter teammate cannot invalidate
the heavier vehicle's permission, and a heavier teammate cannot grant a light
vehicle permission it lacks. Unidentified surfaces and solid geometry behind
a crushable object still block travel; deferred proof remains deferred.

The existing room-wide search credits, native recast budget and bounded
receipt caches still limit work. Distinct kinetic capabilities can require
additional cold proofs, so these bounds do not establish unchanged native
search latency or frame rate with a full room. Capability changes also retire
the affected vehicle's old private work when its new request takes the direct
path; a new round invalidates the previous world's native receipts even when
the map and vehicle capabilities are unchanged.

The existing stalled-motion diagnostics retain the last native refusal from
the actual search query, including its edge, capability, available catalog
identity/model and classification reason. Recording this evidence performs no
additional native collision queries. It is needed to distinguish a stock
kinetic rejection, unidentified backing geometry and unfinished proof in a
subsequent native report.

Tiger (17) and Lorraine (6) also repeat a recovery direction that final motion
has rejected as hard. The realised-motion feedback records the failed yaw,
but the driver's recovery branches can select it again after a longer probe
reports clear. This is a mismatch between final contact evidence and recovery
selection; the report does not identify every hard native surface as terrain.

Recovery now consults the existing finite failed-heading memory before
straight reverse, forward escape and swept angled reverse. A translation
failure does not itself forbid an in-place pivot, and all existing hull,
terrain and vehicle checks still apply. A fixed analytic alley reproduces the
low-surface planner/final-sweep disagreement through the real world-collision,
runtime feedback, adapter, driver and copied-physics loop. At five and ten
control frames per second, the old driver stays at its initial position for
45 seconds and repeats 75 and 146 hard contacts. The repaired driver receives
one initial hard contact, leaves through the open front and reaches the goal.
A closed front, expired failure memory and failed angled recovery also have
regressions. This analytic scene proves the feedback contract, not the exact
native BSP of the reported Airfield slope.

Final review also reproduced a directional planning mismatch: a normal route
candidate behind the current hull used the reverse kinetic cap even though
the driver first pivots and then drives forward. Local probes now receive
explicit drive intent. Forward candidates retain the forward cap at every
heading; straight/angled recovery and contact escapes request their actual
drive direction. Per-decision probe receipts include that direction. The
real stock-material callback regression accepts the forward route through a
crushable house and still rejects backing through it when reverse capability
is insufficient. Three-argument probe failures execute only once.

The reached-waypoint regression uses both recorded E25 stopping poses, the
actual Airfield A* graph and reported E25 mobility inputs. The old pending
follower keeps the consumed point; the repaired follower proceeds up the next
dry leg with actual copied-physics motion. These controlled regressions do
not establish full-fleet native gameplay or frame-pacing acceptance.

### September 22 Airfield: destructible-free planning and bounded wait recovery

Reports `20260922-153100-624e3253cb0c` and
`20260922-154804-361a82ba99c6` use build `colorfulmeans-35697098489-1`.
SU-122-44 id 27 waits at spawn while native rays report ambiguous prop
identity. The logged clay-stove hits overlap clay-fence boxes in the shipped
Airfield catalog. SP I C id 29 waits in cell (67, 70), whose baked height is
absent and hazard is the dry terrain-edge bit. Its stationary yaw converges
to the distant strategic goal, approximately 160 degrees away from its
recorded first detour waypoint. The initial powered turn into that position
is not captured; the logs do prove the later wait/facing conflict.

Following the requested route policy, confirmed original destructible skins
no longer determine permanent route obstruction using vehicle mass, speed,
crew or gear. Planning registers exact catalog identities, permits overlapping
members, and recasts the complete segment with only their original materials
filtered. Unknown surfaces, indestructible backing walls and solid replacement
geometry remain visible. Per-vehicle physical crushing, contact admission and
final motion receipts remain separate. Planning also samples terrain beneath
confirmed destructible roofs. A single immutable planning-policy key lets all
vehicles reuse the same static edge proofs without changing the actual hull
width or the live vehicle collision checks.

A pending route no longer rotates the hull toward a distant face target.
When local physical blockage or an unsupported baked start establishes a
reason to recover, a four-second staggered wait may attempt a bounded straight
backout. The complete rear corridor and nearby vehicles must pass their
checks. Completion or a blocked rear requests one local replan; cached
commands cannot repeatedly restart it. Ordinary queued A* work alone does not
claim a collision. Physical failed-edge evidence survives replanning, while
other vehicles retain their shared routes and searches.

A missing baked start now always needs a live support, grade and obstacle
proof before joining a nearby safe cell, regardless of whether a previous
hard contact requested native review. Neither a coarse snap nor a missing
height alone grants that motion. These are general planner/driver changes,
not Airfield coordinate exceptions or difficulty changes.

Tests use the recorded prop geometry and SP I C position together with
controlled native probe responses. They cover overlapping originals, backing
walls, replacement geometry, deferred budgets, shared route policy, wait
heading, rear traffic, bounded recovery and per-vehicle replan ownership.
Windows native gameplay remains the acceptance boundary for the reported
initial collision and complete spawn departure.

The crowded-departure regression also exposed two recovery state problems.
A short navigation wait must preserve the driver's accumulated progress and
held avoidance/recovery state rather than repeatedly restarting them. A hull
boxed in by vehicles ahead and behind must publish both proved blockers;
otherwise the finite friendly-reposition system can request movement only
from a rear vehicle whose own rear is occupied. The same existing bounded,
collision-checked reposition mechanism handles the added forward request.
The departure monitor now separates ordinary recovery episodes by their
existing recovery count. Consecutive blocked commands can straddle two
independent manoeuvres; combining them incorrectly reports one oversized
lease. The two-second per-episode bound and the independent sixty-second
whole-roster departure and original-slot occupancy assertions remain.

### September 22 mission and terrain follow-up

Reports `20260922-154331-fce52b57beab` and
`20260922-160746-63a9c9a7de48` identify builds
`colorfulmeans-35520243588-1` (0.9.2) and
`colorfulmeans-35611837404-1` (0.9.3), respectively. The latter records HT12's
unsupported `compareWithMaxHealth` condition. LT12's hidden assistance and
loadout evidence, and the maximum-health comparisons needed by HT12 and TD8,
are already implemented in `3e4f3052`. The new audit checks the actual #1513
definitions for all four operations instead of treating the old report as a
failure of every later change.

TD7 and TD9 do expose missing current conditions. TD7's `whileFullHealth`
requires hull HP at the kill, not remaining HP at battle end or a damage
total. Mission-event version 5 freezes that state for each enemy kill. Both
halves of a ram and all effects of one shell settle before the health fact is
finalized, so self-splash and reciprocal ram damage cannot depend on target
iteration order. Fire kills use health when the victim dies. Old or incomplete
event histories do not acquire guessed full-health evidence.

TD9's `inBattleMaxPiercingSeries` requires consecutive admitted shots. The
server assigns per-shooter ordinals at launch and merges only verified
penetration intervals. Delayed impacts cannot turn arrival order into a false
series; misses, bounces and expired projectiles leave gaps. Direct penetration
of a living enemy qualifies, while splash, friendly hits and wrecks do not.
The maximum is transported with the existing durable battle receipt; missing
legacy evidence remains missing.

The Live Oaks report also identifies a player movement problem independent
of Bot difficulty. From 16:07:01 through 16:07:28 the WT E 100 remains around
`(-396.713, 1.898, 356.690)`, with forward and reverse attempts stopped by
different faces of building item 102 in chunk 31618, material 111. Ground is
flat and the reported support veto is false. The existing diagonal slide
path sweeps four corner trajectories and a centre lane; a building corner
can enter the middle of a long hull side without crossing those rays.
Destination perimeter lanes close that reproducible gap, using translated
body pose and the existing terrain, upper-wall and destructible rules.

An already intersecting near-vertical wall may release only when the actual
translation reduces penetration, the hull centre remains on the wall's
outside, and the returned point lies in the original occupied body. Recasting
continues through every later surface. This does not grant inward motion,
an exit through the far side of a building, or permission to ignore a second
wall. The extra perimeter queries are limited to nonzero cross-heading
translations. They do not turn the sparse native-ray model into a complete
continuous volume sweep for arbitrary thin features.

The ordinary Bot support path has a separate gradual-descent defect. A
controlled 3-metre-wide chassis crossing a 1.4-metre-wide, 1-metre-deep V
trench can descend about 0.143 metres each step. Every step stays below the
old 0.8-metre support-check trigger, so seven steps sink the centre almost a
metre while both track supports remain at the bank height. This reproduces
the defect on the pre-change HEAD; it is not a reconstruction of every native
triangle in the Stalingrad screenshot.

Paired support is now checked after one centimetre of accumulated centre
descent. Accumulation prevents smaller high-frame-rate steps bypassing the
same check. The existing admissible support-height range still excludes a
nearby roof and a one-sided cliff. Symmetric endpoints support the centre at
their mean height rather than the higher endpoint; the result can arrest a
drop but cannot lift a tank to a different surface. Level ground retains one
column query, and a triggered descent uses at most five. The change does not
enable the full spring solver for every Bot or teleport a deeply sunk hull.

Focused regressions cover actual mission definitions and durable settlement,
shot order and interrupted series, wall corners and outward-only release,
and longitudinal/lateral shallow trenches at 15, 24 and 60 Hz. The native
Live Oaks vehicle is already wedged against two faces: this prevention and
conservative release fix does not prove that exact saved pose can leave.
Windows gameplay still has to establish that new entries into these reported
locations remain clear and that the full fleet's native frame rate is usable.

### September 27 priority follow-up on the 0.9.5 release line

The requested downhill policy is a maximum of 110% of the descriptor's
directional speed limit. The existing ground overspeed limiter now uses
1.10 instead of 1.35; propulsion, airborne gravity and reverse-limit selection
keep their existing owners. A 45 km/h forward limit therefore caps at
49.5 km/h. This is an explicit product choice, not a retail coefficient claim.

Cursor focus previously had two writers: the current-pose ray in the offline
runtime and the engine picker enabled by native `Vehicle.targetCaps`. Report
`20260927-151657` contains differing native TARGETING and offline TARGET
vehicle IDs. In #1513, `PlayerAvatar.vehicle_onEnterWorld` enables target caps;
`targetFocus` then independently calls `drawEdge` and `setTargetInFocus`.
Offline vehicle entry, visual startup, visibility updates and model relinks
now keep those caps empty. The offline ray is the sole cursor owner. It
requires an actual component hit before scenery, retained wreck geometry or
a landed turret; a candidate cone alone cannot acquire an outline. A ray
through empty space within a wreck's bounding box may reach an exposed part
of the live vehicle. Once acquired, the stock Highlighter draws the full
outline and still owns removal during model replacement and teardown.

The same acquisition and removal publish `avatar.target` and
`BattleFeedbackAdaptor.setTargetInFocus(vehicleID, isInFocus)`. The exact
`TargetDistancePlugin` consumes that feedback to start/stop its distance
tracking. This addresses the competing target source behind the distance
report as well as the outline. It does not establish native rendering or
distance acceptance without a Windows battle.

The offers page now checks the live Scaleform component registration rather
than treating its saved rollback setting as proof that the replacement is
still installed. Stock lobby package registration after a battle can replace
that controller. Rebinding restores the offers controller while preserving
one original setting for reversible cleanup; tests repeat this across three
lobby registrations.

Direct HE impacts choose `armorHit` when actual HP damage is positive and
`armorResisted` otherwise. Damaging direct impacts also play the existing
bound explosion path. The exact #1513 `shot_effects.xml` binds HE `armorHit`
to its HE impact particles and penetration-impact sound, and `armorResisted`
to the non-penetration impact sound. Physical shot results and crew-voice
feedback remain unchanged. Tests separately assert impact effects and the
unchanged voice flags, including damaging non-penetration and zero-damage
direct hits.

The default unlocked save was located in the launcher's default slot. The
September 27 visible-client log records `OtherModuleInstaller` rejecting
`BattleBooster` 27387 in `ModuleTypeValidator` before an Account request can
be sent. Exact #1513 `getInstallerProcessor` does not branch on GUI item type
`BATTLE_BOOSTER`; both direct consumers in `items_actions.actions` import
that factory by name. The narrow offline wrapper patches both references
and routes only that type through
`VehicleBattleBoosterLayoutProcessor(vehicle, booster, layout, skipConfirm)`.
`EquipmentLayoutHelper(vehicle, None, (cd, 1))` preserves regular consumables;
`(0, 0)` and a `None` booster remove the directive through the same native
four-slot `Inventory.setAndFillLayouts` request. Other module fitting and
the native confirmation/response path are retained. Cleanup restores both
factory references and is idempotent.

New unlocked saves seed 1,000,000 bonds in both the client and launcher.
Career saves still seed zero. Existing balances, including missing legacy
bond fields (zero), remain intact; depleted saves are not silently refilled.
Regression coverage verifies directive removal at zero bonds and reinstalling
existing inventory without another charge.

Validation on this change includes the battle runtime, physics, garage,
offline services UI, economy, compatibility and launcher save-ledger suites.
All 146 client sources compile with CPython 2.7.18. The exact installed CH
#1513 ABI audit passes, including the new fitting/layout/focus signatures.
The separate gameplay-followup suite has seven failures and two errors in
both this change and an unmodified archive of its release-branch parent;
these are existing Bot aiming/target-retention failures, not a passing CI
claim. Native Windows gameplay, effects audio, repeated battle-to-lobby
transitions and real directive fitting still need acceptance.

Read-only checks requested alongside this batch made no mission changes.
The four exact LT-14 definitions require solo radio assistance of
1500/2500/5000/6000, with survival for honours; the current evaluator passes
at those thresholds and awards main-only completion after death. This proves
the condition path with supplied receipts, not a reported player's actual
spotting attribution or installed version. Per the user's scope correction,
all 0.8.4 reports are invalid evidence for this task. Report `20260927-024817`
is excluded; no current-version ping conclusion or change is derived from it.

### September 28 roster, route editor, projectile and spotting follow-up

The existing retired-vehicle list was not consumed by the Bot lineup paths.
The launcher profile normalizer, server catalog and client automatic roster
now exclude Aufkl. Panther, WT E 100, SU-122-54, Object 263B and Object 430B.
Exact slot overrides drop only their retired vehicle selection, retaining
their crew/difficulty choice. Human garage eligibility remains separate and
still accepts these stock-loadable vehicles. A retired human vehicle cannot
become the substitute for an unloadable Bot descriptor.

Accelerated crew XP now selects the lowest-total-XP trainable crew member,
with vehicle-slot order breaking ties. Exact #1513
`Tankman.getSkillsToLearn` combines common skills and the crew member's
combined vehicle roles. `TankmanDescr.skillLevel` returns None for an
unlearned skill and the level for a learned skill. Fully trained members are
excluded from the additional accelerated award; if all members are complete,
vehicle XP is retained. Ordinary crew XP and the existing Mentor calculation
are unchanged. The new exact calls are included in the ABI audit.

SPG rear placement permutes the existing fifteen validated spawn poses on
each team. Rear depth is measured away from the opposing spawn centroid,
independent of slot numbering. Human and Bot SPGs share one complete roster
mapping; displaced vehicles receive the vacated slots. No new coordinates,
spawn spacing, steering or collision coefficients are introduced. Tests cover
three SPGs on both sides, reversed slot numbering, human/Bot parity and reset
between battles. Congestion on Ensk, Lakeville and Mitterrand still requires
native departure acceptance.

The route editor defaults to heavy-tank routes and can display one class or
all classes. Switching the filter clears the selection, not saved routes.
SPGs can receive authored routes as well as parking zones. Double-clicking a
waypoint sets wait seconds: zero passes through, a positive value starts at
physical arrival, and -1 holds indefinitely. The optional fourth point field
stays in the canonical tactics profile; the existing manifest retains its
three-field geometry. The server prevents forward-corridor shortcuts from
skipping a parking instruction, preserves the opening authored waypoint and
advances after its wait. Scripted movement overrides tactical movement while
retaining aim/fire decisions. The worker's initial-SPG-position adapter only
handles its ordinary artillery modes, so it does not restore an old parking
goal over an authored route. Actual Tk tests cover filtering, persistence and
condition editing; server-order tests cover arrival, timed departure,
permanent hold and SPG movement.

Bot direct and SPG physical launch origins previously used HP_gunFire, which
may lie beyond a wall while the hull stays outside. Both now use the same
descriptor gun-pivot transform as player shot geometry. SPG planning, final
arc proof and the frozen launch receipt share that origin. Muzzle cosmetics
retain their native node, and submerged-barrel and started-entity guards
remain. A regression places the cosmetic muzzle beyond a wall and verifies
that the first physical projectile chord stops at the wall before its target.

Exact `items/vehicles.pyc:VehicleDescriptor.__updateAttributes` constructs
`visibilityCheckPoints`, `observerPosOnChassis` and `observerPosOnTurret`
only in its IS_CELLAPP branch. Reading that field on a client descriptor
therefore fell back to one fixed-height ray even after six-point support was
introduced. The adapter now reproduces the original six-point construction
from the loaded hull/turret collision bounding boxes. The fixed chassis port
is above the hull centre; the second port follows the turret's gun pivot.
Rear-mounted turrets do not move the first port to the back. Target front,
rear, sides, top and gun-pivot checkpoints share the original hull/turret
transforms. Tests use client-shaped descriptors without the cell-only field,
including a rear turret, exposed ridge target and client/worker parity. The
existing phased visible-client check is bounded by three targets times six
blocked rays per update; native full-room query cost remains unmeasured.

Outstanding items are not claimed fixed by this change. Native BattleReplay
recording still has no offline lifecycle/stream integration. The human fire
intent path still commits one shell per intent despite the existing Bot burst
clock. Map route/collision work and the remaining physical-contact reports
need separate implementation and native evidence. In particular, Ghost
Town's baked route endpoints are (-2, +/-350), while its exact WTCP standard
capture centres and `objective_bases` agree at approximately (290.979,
-100.311) and (-291.095, 100.010); this discrepancy is retained as a route
investigation, not silently resolved by moving native capture circles.
Report 232418 is a valid 0.9.5 Himmelsdorf session. Report 215741 is also
0.9.5, but contains startup logs without the reported collision or a server
log, so it cannot calibrate mass transfer or ram damage. No physics formula,
LT-14 condition or ping behavior is changed here.

### September 28 Prague, bridge gravity and player burst follow-up

Reports 021030 and 021934 both identify `test-20260928-cabd5a4` on the
supported #1513 client. Prague records repeated hard movement refusals near
(55, 13, 100), including a stopped planner whose movement intent remains
active. Previously those refusals could not retire the baked-clear corridor:
the 4 m graph remained authoritative for the next search and shortcut.
Actual hard contacts and blocked planner collision samples now request native
terrain and hull-width checks in a bounded local region. A* edges and path
smoothing share that check; affected paths and in-flight searches are retired.
Repeated reports reuse the marked region and measured segment results.
Traffic holds and unavailable probes do not invent static obstacles. The
regression proves a fresh route around a measured wall; the supplied Prague
coordinates still need an in-client departure check.

The bridge report gives an unsupported body at y=0.5155965 and a forward
upper-hull hit at y=0.8602448, with an almost exactly upward normal. Projecting
the full velocity onto that forward contact erased downward speed each frame.
Horizontal sweeps now constrain only horizontal velocity; suspension remains
the owner of vertical support and landing. Float32 noise in an upward normal
cannot create a horizontal collision normal. Repeated 30/120 Hz fixture
contacts preserve gravity without phantom fall damage. The Bot legacy path
also uses reachable support instead of pulling a hull down to a nearby lower
floor, and consumes the actual airborne wall witness for impact HP before
alternative-direction probes replace it. This changes no ram coefficient,
mass transfer formula or fall-damage coefficient.

One human trigger schedules the loaded descriptor burst on the firing client.
Each physical round freezes the current native gun ray, publishes its own
ordered launch and immediately consumes ammunition. Duplicate clicks cannot
start overlapping groups. Partial clips, gun destruction, queued reloads and
round generation changes retain their local gun owner. Exact
`Vehicle.showShooting` requires the Avatar's initial shot-wait token, so the
first local callback starts one native effect group after the mailbox returns;
later rounds do not replay that group. Cancellation stops remaining effects.
Existing `ShowShooting.__doShot` owns the native afterShotInBurst/afterShot
transition. The worker simulates only Bot projectiles.

Capture staging incorrectly required proximity to the penultimate waypoint
even after the route cursor passed it. Losing enemy contact then left a Bot
at the route endpoint instead of handing over to the exact capture circle.
Passing that waypoint now satisfies staging. This addresses the confirmed
route-to-circle handover defect relevant to Ghost Town without moving bases
or bypassing authored waypoint waits.

Validation: 1,011 runtime/burst/gravity/contact-review checks and 318 server-AI,
capture, tank-contact, world-collision and vehicle-physics checks pass. The
pinned client inspection and Python 2.7 ABI audit pass. Broader Bot/navigation
suites retain pre-existing failures; their failure identities are compared
with the previous source, not represented as a green full suite. Native
rendering, Prague doorway navigation, bridge falls and one-click bursts still
require #1513 gameplay acceptance.

Remaining requested work is explicit: playable offline replay recording has
no native recorded-stream lifecycle yet; fall module/crew injuries have no
implemented canonical rule. The available client contracts do not establish
the official server's injury-selection law. The other reported map air walls,
repeated wreck pushing and light/heavy contact observations are not claimed
fixed by these changes. Report 232418's final Python exception is tutorial
teardown, not proof of an air-wall cause; 215741 contains no battle contact
trace. LT-14's reviewed conditions remain unchanged.

### September 28 intermittent KV-5 contact and bridge support follow-up

The centre-of-mass integration and its validation conclusion in this section
are superseded by report 040026 below. The ram-contact changes remain active.

Reports 031859 and 032344 both identify `test-20260928-e249724` and the
supported Chinese #1513 client. The user clarified that the missing damage
is on enemies hit by KV-5, including T26E5; some contacts hurt them and others
do not. The first report records an unsupported proof at 03:18:05 against
Bot 20, despite separately finding 180 mm and 80 mm plates, while later
contacts in the same battle successfully damage enemies. This is not evidence
for increasing the global ram coefficient.

The contact sampler now searches the intersection of both mounted hull height
bounds in addition to the wider track-to-roof contact envelope. A thin shared
hull band can lie between every old sample. Bounds only place rays: both
native hit testers must still return structural plates on the same ray, and
the receipt stays inside the shared contact envelope. Different heights are
never combined and no primary/minimum armour fallback is introduced. A
terminally unproved contact no longer consumes the overlap episode; a later
contact can submit its own current pose and pre-separation velocity. The
presentation timestamp is frozen with the matrices, so a next-frame retry
cannot attach old geometry to a newer Bot snapshot. Tilted contact broad phase
uses both bodies' pitch and roll. Enemy RAM scaling remains 0.25.

Report 032344 shows one supported track column at bridge height 1.109309,
the other unsupported, and a rolled body near (4.94, 0.56, 111.79). This differs
from the earlier unsupported-body/downward-speed cancellation. Inspection
found the reduced suspension solving torque about the ground-level model
origin. Exact `physics_shared.initVehiclePhysicsClient` instead places its
centre of mass at mounted hull midpoint plus the hull-height-scaled power
curve. The native curve uses enginePower/weight knots 9.5/13/21 and shifts
-0.15/-0.2/-0.3. These constants, function signatures and consumers are now
audited against the pinned PYC. The suspension integrates translated contact
points and heave about that centre, then returns the model-origin pose and
velocity. Cached points are refreshed when tipping adds rigid hull/turret
contacts. The reduced three-axis model still is not native six-axis physics;
this corrects a provable torque-origin defect but does not establish that all
reported bridge sticking is resolved in game.

Tests cover a narrow shared hull band, failed proof retirement, immutable
contact time, tilted supported mass tipping, no gravity-induced rotation in
free fall, curve control points and rigid-contact cache refresh. Existing
ballistic checks now measure the physical centre rather than a rotating model
origin; slope checks allow the spring-travel-bounded load transfer instead of
requiring a perfectly uncompressed hull attitude. 1,335 focused runtime,
suspension, contact, server-AI, capture and collision checks pass. The broader
Bot run has the same 26 pre-existing failures and 14 errors after updating
the two centre-of-mass expectations and rerunning those checks. All 146 client
sources compile under CPython 2.7.18; exact-client inspection and ABI audit
pass. Actual KV-5/T26E5 contact and bridge departure still need #1513 playtesting.

The requested investigations have explicit limits. Falling still changes
canonical vehicle HP without a module/crew injury operation. Searching the
exact client PYC finds projectile/explosion saving throws, not an official
falling injury-selection/damage law, so no invented probability or HP ratio
is installed. Offline replay also remains unimplemented: stock BattleReplay
auto-recording belongs to Account/native replay-controller lifecycle, whereas
this port's offline entity and LAN event stream has no recording/playback
adapter. Enabling the stock option alone does not supply that missing path.
No empty or non-playable file is presented as a replay fix.

The Stratford report 173316 ends on `34_redshire`, on the older 0.9.5 build
`colorfulmeans-36137978481-1`. Its final witnesses include (125.94767, 4.58314,
-144.74382), material 73/flags 0/item 8/chunk 32893, and a later material 73/
flags 131/item 203 contact. The first slot is excluded/quarantined after a
layout repair; nearby destroyed fences do not contain the witness in their
recorded component bounds. The evidence does not establish a safe owner or
filter extent. No blanket material, chunk or excluded-slot collision bypass
is added, and this map is not claimed fixed by this patch.

### September 28 bridge regression from report 040026

Report `20260928-040026-f99b63f0ab92` confirms `test-20260928-041eab2`
in launcher, visible client, hidden worker and server. Its battle is KV-5 on
`37_caucasus`; the user observes repeated tipping without leaving the bridge.
There is no continuous pose/support record for that idle bridge interval:
the previous diagnostics required drive throttle or a blocked steering input.
The report must not be interpreted as successful bridge acceptance. The
unrelated tutorial weakref exception occurs on exit, after this gameplay.

The preceding patch translated contact points, height and vertical velocity
to a raised mass centre, but both motion adapters still retained model-origin
X/Z. Rotation therefore changed the horizontal centre position without the
corresponding translation/velocity/world-contact integration. Correct native
mass-centre constants do not validate that incomplete reduced solver. Remove
the translation wrapper and restore the coherent model-origin suspension for
both player and Bot paths, including the previous ballistic and slope test
contracts. Keep the exact-client ABI evidence as research, not as an active
mass-centre integration claim. No extra torque, angular clamp or forced fall
has been introduced. KV-5 armour sampling, repeat-contact retries, timestamp
freezing and the 0.25 RAM coefficient are unchanged.

A new finite-deck test uses the real player ground-sampling and integration
adapter, real missing columns, an initially non-penetrating tipped body and
zero input/angular velocity. Eight mirrored cases at 25/60 Hz run for ten
seconds. All eight fail on 041eab2: the body repeatedly rises and rocks above
the deck. After removing the incomplete transform, they fall or settle and
the final two-second motion converges. This is a synthetic regression, not a
replay of the reported bridge mesh. It does not prove that every older
side-support or ledge-hanging defect is fixed.

Idle tipping now emits pose, angular rates, support plane and existing wheel
probe evidence at the established two-second diagnostic cadence. It reuses
the rigid-contact tipping threshold and cached samples without additional
native collision queries. Upright idle vehicles remain silent. Coverage
checks pitch and roll, the cadence, and absence of extra world probes.

Validation: 1,334 focused runtime, bridge, suspension, ram-contact, Bot slope
and cliff, server-AI, capture and collision checks pass. All 146 client files
compile with CPython 2.7.18; pinned #1513 inspection and ABI audit pass. The
existing unrelated broader-suite failures are not represented as green.
Actual bridge departure and absence of repeated flipping on the exact client
remain unverified until Windows gameplay acceptance. This patch withdraws
the regressing mass-centre change; it is not a claim of native six-axis parity
or completion of the original bridge-sticking report.

### September 28 bridge hang and reciprocal separation (044826)

The positional-checkpoint conclusion below was contradicted by report
115952 and is superseded by the following section. Its synthetic tests
assumed the worker accepted every requested displacement.

Report `20260928-044826-c53da28eff15` confirms `test-20260928-7bc3842`
on #1513. At the `37_caucasus` bridge, KV-5 repeatedly has one missing track
column, no fitted support plane, roll around 0.55-0.65 and zero angular
velocity. The reduced solver's sleep test ignored rigid-contact impulses and
allowed a one-sided body to sleep. Its ground-memory helper also continued
supplying old deck heights without a current plane. Those retained columns
could alternately arrest the falling body as it rotated outside the deck.

Sleep now requires an actual contact polygon surrounding the reduced body's
origin and includes hard-projection impulses in the acceleration test.
Missing columns are retained only with a proved terrain gradient; a known
flat gradient still covers bounded query holes. Both adapters use these shared
laws. No raised-centre transform, artificial tipping torque or forced fall
timer is added. The eight earlier overturn regressions still pass. Twenty-four
additional mirrored finite-edge cases at 25/60/100/144 Hz now fall below the
deck; twenty-two failed on the parent. These use synthetic geometry through
the player adapter, not a reconstruction of the native bridge mesh.

The report already carries correct masses (KV-5 100575 kg, the early light
contact 31370 kg, HWK-12 12495 kg). There are two independent physical response
defects. Visible contact summed independently solved impulses for multiple
neighbours, unlike the worker's sequential solver. It now uses that same
solver restricted to player-owned pairs, preserving one reciprocal momentum
budget; Bot/Bot pairs remain worker-owned.

More importantly, the contact ledger transported momentum but not reciprocal
positional separation. Between worker updates, each render frame moved the
player again against the unchanged remote pose. A one-metre overlap against
31370 kg should move the 100575-kg player by 0.23537 m after the existing slop.
Across a 200-ms update gap, the parent instead moved it 0.71475/0.94414/0.98924 m
at 25/60/144 Hz. The new tests retain the mass share at every rate, including
the 12495-kg peer and an ACK arriving before render interpolation catches up.

The cumulative checkpoint now carries `[actor, sequence, momentumX,
momentumZ, separationX, separationZ]`. Server relay and compact Bot encoding
preserve both totals; worker ACKs travel with the canonical pose/velocity.
Unacknowledged separation shifts only the physical prediction. Historical
presented poses still supply armour/HP evidence, while physical contact uses
the coherent current pose plus pending response. The worker applies unseen
separation through its existing world-collision guard once, and does not
separately solve that human pair again. Retries and coalescing cannot replay
it; a blocked separation remains blocked. Momentum and positional correction
are separate quantities, so separation never creates ram damage or velocity.
The launcher must install the matching client/server/worker bundle together.

Validation: 1,442 focused tests run, one environment-dependent test skipped,
with no failures. The focused selection excludes six unchanged navigation
failures reproduced on the parent; the existing hydraulic Bot slope failure
was also observed and is outside this selection. New coverage includes
reciprocal separation under snapshot delay, codec/queue/relay round trips,
worker world blockers, retry/coalescing, crowd momentum, and installed mass
plus engine-power inputs through actual worker receipt application. All 146
client files compile under CPython 2.7.18; #1513 inspection and ABI audit pass.
Actual bridge geometry, KV-5/light-tank pushing feel and Windows timing still
require gameplay acceptance; automated results do not establish retail parity.

### September 28 occupied-space regression (115952)

Report `20260928-115952-c7d54bb27c55` consistently identifies
`test-20260928-50c1e6a` across launcher, server and both clients. The first
`37_caucasus` round uses KV-5, 100575 kg. The reported player/Bot/wreck
penetration contradicts the previous positional-ledger acceptance claim.
Logs include separation-only worker checkpoints increasing while the
transferred velocity is zero; they do not prove that the requested space
was cleared. Three-body contact and native obstruction were absent from
the earlier delayed-pose regression.

The visible adapter previously added all unacknowledged positional requests
to a canonical Bot pose, even while its renderer still displayed the old
hull. The worker could reject those requests and acknowledge them anyway.
Its contact displacement used planning rays rather than the injected native
hull-motion resolver, and checked no intervening vehicles. Finally, current
human checkpoints disabled the complete worker pair instead of only the
already-delivered impulse.

Physical momentum still uses descriptor mass, engine-derived drive velocity,
track hold, and the cumulative receipt/ACK path. Position is constrained
separately: the remote body occupies its presented/accepted pose until its
owner moves it. The visible client sends no positional requests, and the
worker consumes momentum without teleporting for the final two retained
wire fields. A current player remains a positional obstacle on the worker;
the checkpoint disables only duplicate impulse application. No second
writer of Bot presentation or authority is introduced.

A four-axis swept OBB interval test now bounds drive, residual shove and
separation travel against other hulls, including wrecks. It permits escape
from existing overlap and tangential travel, uses the existing one-centimetre
contact slop, and cannot skip a vehicle merely because its endpoint is clear.
Both integrators retain incoming velocity for the mass-weighted impact solve.
Worker contact motion also calls the existing passive native hull resolver
with the actual descriptor, hull attitude, movement direction and exact
distance, without active-drive crush. Existing world, detached-turret and
terrain guards remain in place. Bounded diagnostics record requested and
accepted contact moves and whether native geometry rejected them.

Four regression scenarios reproduce nine failing cases on the old runtime:
unaccepted separation at two mass ratios and three render rates, a wreck
crossing another vehicle, native obstruction missed by planning rays, and a
live player omitted from worker separation. They pass after the change.
Additional coverage checks one second of player driving into live/wreck
bodies at 25/60/144 Hz, opposing drive endpoints, repeated wreck/Bot pushes
against a wall, oblique/long sweeps, vertical separation, escape directions,
and unchanged reciprocal mass-weighted momentum.

Bridge evidence is improved but does not establish retail parity. At
11:51:36 the native contact is a real vertical side face (normal Y=0), and
by 11:51:38 the player is airborne at Y=-4.234 with vertical speed -8.654.
At 11:57:47 the one-sided support is still rotating (roll velocity 1.24767),
and by 11:57:49 the vehicle is airborne at Y=-6.787 with vertical speed
-12.642. The samples show departure rather than indefinite attachment.
Two-second diagnostics cannot determine whether the brief visible side
contact has the exact retail duration. This follow-up does not change
suspension, gravity, support retention or tipping coefficients.

Validation runs 1,464 focused checks with one environment-dependent skip
and no failures, excluding the same six previously reproduced navigation
failures. The complete 528-test Bot module remains red (26 failures and
14 errors); the existing gameplay gate remains at seven failures and two
errors. Full-suite success is not claimed. All 146 client files compile
under CPython 2.7.18, and the exact installed #1513 inspection and ABI audit
pass. Actual crowded KV-5 pushing, native map obstruction and presentation
timing remain Windows gameplay acceptance boundaries for the test package.

### September 28 first-impact chassis-corner armour (131321)

Report `20260928-131321-61e174bddd87` identifies `test-20260928-79cafb7`
in all roles. In the first `37_caucasus` round the 100575-kg KV-5 transfers
momentum to the 23496-kg enemy M41 Bulldog at 13:04:55, with incoming
horizontal velocity `(9.4165, -5.8926)`. No RAM HP receipt accompanies it.
The visible client reports an unsupported contact while independently seeing
180-mm and 31.8-mm plates. The first accepted ram is at 13:05:06.830 and
reaches the worker/server at 13:05:07. The earlier spawn contacts are friendly
and correctly have no ram HP damage. The old failure message did not separate
a missing historical revision from a missing same-ray plate pair, so it alone
cannot prove which condition rejected that exact frame.

The exact installed #1513 `Hull.primitives_processed` meshes and packed XML
mounts reproduce a geometry defect at the reported contact location. Sweeping
the solid chassis envelopes to their existing 0.01-m slop gives a very narrow
corner patch. Across M41 yaw samples 0.414683, 0.44 and 0.4719746 (bracketing
the nearby logged target/actual attitudes), the old normal-only pair search
finds KV-5 `armor_3` and M41 `armor_2` independently but no common sample. A
contact-to-interior query recovers those structural materials at the same
point and height. These are offline triangle intersections with captured
nearby poses, not a native replay of the missing exact frame.

Both visible receipts and worker-owned armour queries now finish the entire
original contact-normal search first. Only if it finds no pair do they query
missing plates from the same contact sample toward each body's centre. Each
existing native hit tester still supplies the first positive structural
material, with its ray terminating at the centre plane. Missing geometry,
separated height bands and far-side queries fail closed; no primary/thinnest
armour value is substituted. The original contact normal, incoming velocity,
historical timestamp, receipt identity and worker HP authority are unchanged.
This geometric contact-envelope adapter is not a claim about the official
server's unpublished ram material-selection implementation.

The correction preserves solid hull/wreck sweeps, actual mass response,
enemy RAM scale 0.25, friendly damage disablement and bridge suspension.
Bounded failure diagnostics now distinguish history, normal and geometry
and include whether a pair was found, contact point and historical identity.

Validation: both new first-contact/receipt regressions fail on the parent's
unchanged pair search and pass here. Six new tests use finite structural-box
ray intersections to cover corner plates, centre-plane termination, immutable
pre-impact speed/normal, no duplicate receipt, original-pair priority,
disjoint heights and missing geometry. All 1,470 selected checks pass with
one environment-dependent skip, excluding the same six previously reproduced
navigation failures. All 146 client files compile under CPython 2.7.18; exact
#1513 inspection and ABI audit pass. Existing full-suite failures are not
claimed fixed. Actual Windows KV-5 first-impact HP/timing still requires the
user's gameplay acceptance of the matching test package.

### September 28 repeat wreck pushing (134227)

Report `20260928-134227-c7b301f7839c` runs `test-20260928-faedc54` in
all roles. KV-5 remains 100575 kg and M41 Bulldog remains 23496 kg. The
first enemy contact at 13:40:59 produces 382 RAM damage; M41 dies on the
next impact at 13:41:07. Later visible contact diagnostics still assign
that wreck `(3.5360, -5.5498)` horizontal velocity at 13:41:35, despite the
reported stationary obstruction. This is not a lost-mass conversion.

`SnapshotSync._upsert` emitted the first dead state, then accepted only pose
targets for subsequent wreck snapshots. Its render chase therefore moved
the corpse while the visible battle record retained the death-frame
`push_x`, `push_z` and `contact_push_acks`. A visible non-authority's Bot
runtime has no simulated Bot states, so `_contact_tanks` reads exactly that
stale record. Spent impulses then remain pending and stopped wrecks appear
to keep moving away, suppressing later reciprocal contact impulses.

The dead-state branch now emits a state-only update when the canonical
state changes. Velocity and acknowledgements advance together, including
an acknowledgement at an unchanged position when geometry rejected motion.
The render chase remains the only displayed-pose writer. Identical settled
states emit nothing, nested ledgers are copied for comparison, destruction
is not reissued, and snapshot sequence/round fences remain unchanged. The
same state transport covers retained Bot and human corpses; it does not
assign a new pose authority to human wrecks or change their existing
immovable-owner policy. The reported Bot corpse retains original descriptor
mass, existing track resistance, world guards and the worker integrator.

Three new snapshot tests and a visible-to-worker repeated-shove test produce
five failures on the unchanged parent. All pass after the fix. The integrated
case sends three consecutive shove/settle/ack cycles for 23496-kg and
100575-kg wrecks, verifies original mass, repeatable displacement, stopped
velocity, one-time momentum consumption and no RAM HP on corpses. A lighter
wreck travels farther under the same incoming KV-5 speed. Existing heavy,
equal-mass, medium and light engine/mass pushing checks also pass.

Validation runs 1,471 focused checks plus 42 snapshot/mass checks, one
environment-dependent skip and no failures. The same six previously
reproduced navigation failures remain excluded. All 146 client files
compile under CPython 2.7.18. The correction adds no native API or physical
coefficient; actual Windows repeat pushing remains the gameplay acceptance
boundary for the test package.

### September 28 passive wreck dynamics and head-on pushing (143607)

Report `20260928-143607-cd4830c88085` runs `test-20260928-9f0485a` in
all roles on `37_caucasus`. Repeated M41/T-43 corpse impulses are consumed,
so the previous stale-ACK defect is not the cause of these new symptoms.
The corpse integrator has no yaw response and explicitly rolls back a move
whose support drops more than 0.6 m. This explains both non-rotating corpses
and their refusal to leave an edge.

At 14:33:15--19 the 100575-kg player KV-5 reports roughly 2.2 m/s while its
position barely advances against friendly Bot 9, a 32000-kg SU-100M1 with
382460 W. The worker records clear world/path probes; the later nearby
170-ton Bot does not explain the earlier pair. Incoming human momentum was
consumed after the worker's drive step, and a reverse shove was stored apart
from the signed drive speed. Automatic braking also added a second traction
budget to engine force; when engine force already crossed zero, its negative
brake erased the remaining drive work for that slice. These are code defects,
not evidence that the original vehicle masses should be multiplied.

The worker now consumes cumulative human momentum before driving; the final
contact pass sees the same ACK and cannot consume it twice. Both live owners
put the complete longitudinal impulse into signed road speed. Engine power,
installed mass, terrain resistance and the existing longitudinal grip curve
remain the force inputs. Automatic braking shares that curve's ground-force
budget with the engine and cannot apply a negative brake to cancel forward
work after zero crossing. First-impact RAM proofs and the enemy 0.25 damage
scale remain separate and unchanged.

For passive Bot hulls the planar contact constraint now includes yaw inertia
in its effective mass before choosing a normal/friction impulse. The impulse
uses the common clipped footprint contact, so opposite offsets turn opposite
ways, a centred hit produces no yaw, and rotation is not added as free energy
after a translational solve. Motor corner contacts also transfer a bounded
yaw impulse to a wreck. Original mass and mounted chassis dimensions define
the uniform-box planar inertia, consistent with the existing copied rigid-hull
trial. Uniform track loading projects the existing parked longitudinal and
lateral Coulomb budgets into yaw resistance. This is an explicit planar
approximation; neither native retail inertia nor full three-dimensional tank
contact has been recovered or established by these tests.

The matched client/server/worker ledger now has seven fields: actor, sequence,
cumulative X/Z momentum, two retained inert position totals, and cumulative
yaw momentum. `push_yaw` and its ACK travel in the same compact Bot row and
server snapshot. The visible client predicts only the unseen angular share;
the worker never reconstructs its lever from a later pose. Numeric validation,
coalescing, duplicate/reordered messages and ACK quantisation are covered.
No mixed-version wire mode is introduced; distribute all roles together.

A wreck advances yaw only through the existing vehicle rotation sweep,
arena boundary, detached-turret and native/catalog rotation guards. Its
native rotation query includes static world geometry even before a structure
has broken; the live motor's damaged-BSP-only shortcut cannot admit a rotating
corpse corner into an ordinary rock or wall. Its
vertical owner is now the existing suspension/ballistic integrator, including
partial support, pitch/roll, airborne gravity and landing. It continues after
horizontal momentum stops and consumes no ground-friction budget in flight.
The centre-column fallback requires opposing support across a hole; one
remote supported end no longer holds a hull beyond a cliff. A corpse's landing
cannot create another death/HP event. This does not implement module/crew
injuries for live falling tanks, nor assign a new owner to human corpses.
Bounded `WRECK motion` diagnostics expose actual mass, pose, yaw momentum,
vertical speed and airborne state for the next native acceptance report.

Validation: 1,471 focused checks plus 57 wreck/snapshot/mass/spawn checks pass with
one environment-dependent skip. The six previously reproduced navigation
failures remain excluded from that focused selection. New coverage includes
visible offset contact through worker consumption, yaw ACK retries, powered
corner torque, energy/momentum checks, world-blocked rotation, repeated
shove/settle cycles, ten-spring edge departure, airborne momentum, and landing
after horizontal motion stops. The head-on owner loop uses a 0.1-second worker
cadence against 30/60/144-Hz visible motion and checks KV-5/SU-100M1 with both
player/Bot assignments and a weaker-engine counterexample. Tests do not prove
retail feel or equal native timing. Exact installed #1513 inspection, ABI
audit and Python 2.7 source compilation pass; no new native method is added.
The packaged Windows build still requires actual edge/rotation/head-on play
acceptance. Existing unrelated gameplay/CI failures are not claimed repaired.
The broader Bot run reproduced the previously recorded 26 failure occurrences
and 14 errors, plus three differences resolved by preserving one-time spawn
support and observing total signed collision velocity. Those three cases pass
in the final focused rerun. The gameplay gate still has the same seven failures
and two errors as the parent.

### Report 152755: delayed pushing, passive wreck support and tilted track patches

All three logs identify `test-20260928-91b5b9c` on `37_caucasus`.
At 15:20:40-48 the 100575-kg KV-5 applies reverse input with about -2 m/s
reported speed but almost no accepted travel against the 35500-kg T-54 first
prototype. World probes are clear. The previous head-on test copied worker
publications immediately; adding 100 ms of delivery delay reproduces zero
progress. Pending contact momentum was treated as undamped velocity until
its ACK, hiding the worker's intervening engine and ground reaction.

The visible contact adapter now timestamps its accepted impulses on the
existing motion clock, removes acknowledged entries, and replays the remaining
impulses against the latest canonical velocity with the same drive/track laws.
It predicts no positions and does not write Bot poses. The worker still applies
each cumulative checkpoint once and remains the only remote motion owner.
The delayed owner regression covers 30/90/144 Hz, 100/200/300 ms delivery,
reverse KV-5 versus the report's T-54 prototype mass/power, reversed vehicle
ownership, and the weaker-engine counterexample. These are synthetic adapter
tests, not a native network recording or proof of retail calibration.

The report also records rejected wreck nudges with a clear native sweep, and
almost entirely zero yaw motion. Passive displacement no longer consults
navigation slope/water avoidance after an exact world sweep; cliffs and deep
water are not solid geometry. Vehicle, static-world and detached-turret guards
remain. Wreck translation and yaw now spend one shared anisotropic friction
budget instead of independent full linear and angular budgets. The existing
uniform-track approximation is discretized at the mean arms of the two halves
of each track; constrained impulses use the original mass/footprint inertia
and cannot add kinetic energy. This is still an offline planar approximation.

The earlier ten-spring wreck test did not match production wiring: production
provided only the legacy centre support probe. The native bounded suspension
column is now wired specifically for destroyed Bot hulls. Live Bot navigation
retains its existing support owner. Active wrecks use ten-spring/body support,
tip/fall after a push, and continue vertical motion after horizontal motion
stops. Bot wire/server pose bounds now retain full wrapped pitch/roll instead
of clipping them to 0.61 rad; an airborne flag survives publication so the
contact predictor does not apply ground friction in flight.

At 15:25:33.887 the player has pitch 1.082 rad and recovered bridge support
at columns whose direct probe missed. The recovery patch previously stayed
horizontal even when its track rotated. Patch X/Z and its height ceiling now
follow the track pitch/roll, so a near-vertical patch cannot reach back across
its old horizontal width. Existing inclined-plane transport and support
memory rules remain; no artificial outward force or forced fall is added.

Validation includes delayed live/wreck contacts through real visible/worker
adapters and PYC wire roundtrips, original-mass push counterexamples, combined
friction energy/direction, production wreck-probe activation, departed track
footprints, finite-deck falling, native world vetoes, and snapshot lifecycle.
The focused selection passes 1,472 checks (one environment skip; the same six
previously reproduced navigation cases are excluded), plus 60 wreck/snapshot/
mass checks. The gameplay gate still reproduces the parent's seven failures
and two errors. Exact installed #1513 inspection, ABI audit and all 146 Python
2.7 source compilations pass. No new native API is introduced. Actual bridge
release, native wreck pose presentation and crowded delayed pushing require
another #1513 gameplay acceptance run; the reduced suspension is not a full
retail rigid-body solver. Module/crew fall injuries, replay recording and the
other previously listed open work are not claimed repaired here.
The complete 528-case Bot module run reproduces the 26 failure occurrences
and 14 errors previously documented; its failure identifiers add no new case.

### September 28 report 160951: compact mass and forced hazard entry

Both rounds in report `20260928-160951-9ed1131f4b7e` run
`test-20260928-caa555b` on `37_caucasus`. At 16:09:15 and 16:09:42 the
visible client's local drive aborts with `KeyError: 'mass'` in
`_predict_bot_contact_velocity`. The worker keeps running; the server's
`player_left` death follows the client's exception and is not its cause.
The preceding regression introduced a read from the compact motion mapping,
which deliberately omits descriptor-derived mass. The predictor now uses the
already loaded physics parameters for that Bot. Both live and dead compact
rows are tested without adding worker-only fields back to decoded snapshots.
The delayed visible-contact integration test previously did just that and
masked this missing-field failure; its boundary now matches production.

Passive displacement and autonomous hazard avoidance remain distinct. A live
Bot's received longitudinal impulse is still part of its signed physical
speed. Its surviving external share is identified by comparing the same
combined and drive-only integration law, bounded by the actual signed speed;
a braking collision cannot invent a reverse shove. When a navigation veto or
unproved drive corridor withholds autonomous movement, that share advances
through the existing passive native sweep instead of being deleted by AI
safety damping. This adds no traction, mass bonus or second friction budget.

The realised navigation hazard guard now runs after autonomous motion settles
and before contact displacement. Native geometry, vehicle/turret sweeps and
the authored arena rectangle still constrain passive travel. Rechecking the
accepted contact endpoint with zero elapsed time also updates legacy vertical
support, so a live Bot pushed beyond a ledge can become airborne without the
navigation guard restoring it. Existing worker fall-HP and continuous drowning
laws retain their terminal-event ownership; no new kill attribution, crew or
module fall-injury law is introduced.

Regression scenarios exercise the complete worker update and acknowledged
human momentum: unforced Bots reject a fatal corridor, while longitudinal,
reverse and lateral shoves can depart support and suffer a lethal landing.
A deep-water shove reaches the existing drowning terminal state; a physical
wall or arena boundary still blocks it. Additional checks distinguish braking
from reverse forcing and retain delayed head-on, wreck-rotation and bridge
regressions. The focused 1,472-case selection passes with one environment
skip and the same six known navigation exclusions; 69 additional wreck,
snapshot and mass checks pass. The gameplay gate retains its seven baseline
failures and two errors. All 146 client sources compile under CPython 2.7.18,
and the installed #1513 inspection and ABI audit pass. These checks establish
the adapter and synthetic scenarios, not real-client loading or map physics
acceptance; the replacement package still requires Windows gameplay testing.
The broader 528-case Bot run has the same 26 baseline failures and 14 errors,
plus one final map-boundary guard regression. Retaining that last rectangle
guard with navigation hazards disabled resolves the new case; its final
rerun is included in the 69 passing checks above.

### September 28 report 164103: oblique pushing and occupied bridge faces

Report `20260928-164103-4bfcb01dfef0` runs `test-20260928-78a74fe`
on `37_caucasus`. The worker receives repeated momentum for wreck 29
(23,380 kg), but records accepted displacement `(0, 0)` with
`vehicle_fraction=1` and `native_world=True`. After the initial motion it
stays near `(-19.76053, 0.496, 112.73646)`. This proves a world-motion veto,
not missing momentum or a globally missing wreck yaw field. The same report
has a 100,575 kg KV-5 against the 34,080 kg STA-1. Logged mass and engine
power are present; their presence alone does not prove correct native pushing.

Contact integration previously clipped engine travel before adding the
forward/lateral impulse components. At oblique contact, separately constraining
those components can reject a legal combined displacement. Visible and worker
owners now re-sweep their complete post-contact travel. The vehicle constraint
retains an unblocked tangent after first contact, re-sweeping each projected
segment against every nearby hull. Remote replicas remain position-fixed;
their physical mass and reciprocal momentum still belong to their owner.
No player multiplier, extra engine force, mass change or friction coefficient
change is introduced. First-impact armour and ram HP remain separate.

A passive wreck can pivot away from a corner while its centre moves towards
the pusher. Its translation and yaw now have a combined swept-pose admission
path. The complete trajectory must pass vehicle, arena, detached-turret,
catalog and native-world checks before either pose component commits. The
native adapter sweeps each enclosing angular slice along its centre travel;
a missing rotation adapter falls back to the existing guarded path. The
original installed mass and footprint inertia continue to determine response.

The native departing-face predicate also required the hull centre to have
already crossed an exposed bridge side. A partly overhanging track can occupy
that face while the centre is still over the deck, making departure impossible.
Departure now requires a native face inside the original mounted chassis/hull
volume and non-increasing penetration, without that centre-side requirement.
An already occupied upward support face may admit tangential movement. Later
hits can reuse only a witnessed coincident plane; new walls, inward travel and
ceilings still block. The exact installed bridge mesh
`env043_CaucasusBridge_part1.primitives_processed` contains the side/top
geometry in the reported area. An offline raycast using an approximate hull
clears some tested attitudes but still blocks others; it is not an exact
native reproduction or proof that all bridge-edge traps are solved. Rejected
contact diagnostics now include the authoritative pose and world trace.

Validation adds oblique tangent/corner constraints, the visible post-impulse
travel invariant, coupled wreck yaw, native translated-slice call arguments,
atomic world vetoes and partly overhanging finite-deck departure with a backing
wall. The 1,473-case subsystem selection passes with one environment skip and
the same six known navigation exclusions; 74 wreck/snapshot/mass checks pass.
The complete 528-case Bot module has the same 26 failure occurrences and 14
errors as the prior baseline, and the 27-case gameplay gate retains seven
failures and two errors. All 146 client sources compile under CPython 2.7.18;
the installed #1513 inspection and ABI audit pass. No native API is added.
Actual delayed pushing, native bridge departure and wreck presentation still
require Windows gameplay acceptance of the replacement package.

### September 28 report 180736: owner recovery, passive rays and turret debt

The coherent three-round `20260928-180736-67aa6cf49729` session runs
`test-20260928-9232d11` on `37_caucasus`. The player masses are 100,575 kg
and 35,500 kg. The light contact peer logged in the third round is
`Ch16_WZ_131` at 21,100 kg; the second round also records `GB52_A45` at
55,883 kg. These contacts do not prove a native reproduction of the user's
specific 59-16/AT 15A examples. The user confirms improved wreck turning.

Replica pose ownership previously zeroed its inverse mass for penetration
recovery, assigning the complete positional correction to the local actor
even when its physical mass was much larger. The private constraint roster
now solves both physical shares, publishes only the owned correction and
retains the peer's actual occupied pose for every subsequent motion sweep.
Inverse mass, existing track hold and reciprocal impulses are unchanged;
no player-only force or altered engine/friction constants are introduced.
Recovery of a pre-existing overlap converges without opening unaccepted
remote space. First-impact ram HP inputs remain separate.

Wreck 29's native veto around 18:04:39 includes a ray from
`(71.34747, -5.06367, 83.68707)` to `(71.06791, -8.22347, 86.97670)`;
its endpoint had been lowered to future ground at -8.82347. A passive
fixed-attitude translation must not bend down to that future terrain:
the chord can hit a cliff below the body and prevent departure. Explicit
passive sweeps now retain the occupied body's pitch/roll height. Autonomous
terrain look-ahead, inward/new wall rejection and the arena rectangle remain.
Received live longitudinal travel always follows the autonomous pose guard,
including when a clear forecast reaches a fatal baked cell or a distant
planning collision does not intersect the short physical displacement.

At 18:02:44 Bot 22 loses a 5,640 kg turret. Recorded simulation debt grows
from 196 ms to 19,634 ms by 18:03:07; this is real worker lag, not just a
render interpolation symptom. The planner constructed angular vehicle sweeps
before discarding dynamic turret rows. Filtering those rows first removes
that unused work while leaving the canonical body contact solver active.
A 1,200-query synthetic comparison removes 2,400 discarded component sweeps
(321 ms to 4.9 ms in this local run). This is not a native frame-rate claim.
The body solver also stops repeating unchanged blocked recovery, skips empty
Bot responses and continues iterating when either body's velocity or geometry
actually changes. Substeps, world queries, time debt and safety budget remain.

The 271-case targeted run passes; the additional landed-turret pipeline check
passes in its seven-case module. It verifies visible reciprocal momentum,
canonical displacement and exactly-once receipt handling, not native visual
mesh alignment. The 1,473-case subsystem selection passes with one environment
skip and six known navigation exclusions. The full 528-case Bot run retains
26 failures and 14 errors; the 27-case gameplay gate retains seven failures
and two errors. Both failure-identifier lists match the parent baseline.
All 146 sources compile under CPython 2.7.18;
the installed #1513 inspection and ABI audit pass. No native API is added.

An extra two-way publication-delay experiment still shows timing-sensitive
head-on pushing. It uses simplified common descriptors and cannot certify
the named vehicle matchups or retail parity. These repairs address proven
positional, navigation and redundant-work defects; they do not establish
that all Bot/player asymmetry, loaded-map ledges or turret frame pacing and
visible collision alignment are solved. Those remain native test-package
acceptance items. Crew/module fall injury and playable offline replay remain
open from the earlier requests.

### Reports 184737 / 185906: reciprocal shove timing and incomplete interiors

Both reports run test-20260928-3f9b29a on 37_caucasus. Human momentum reaches
the worker, including the 28,000 kg live hull near the bank; it is not a
missing transport receipt. Two reproducible defects remained. Opposite
physical velocity automatically engaged additional braking even when the
driver had never reversed input, letting a pushed weaker engine claim nearly
full track holding force. Powered opposition now uses the installed engine
and traction law; explicit handbrake and released-drive braking remain.
Consequently reversing a moving tank with the drive key alone may brake more
slowly than before. This applies equally to human and Bot integration.

Contact velocity prediction also omitted upstream receipt latency: it added
unacknowledged momentum without the corresponding engine/ground reaction
interval. Newly observed ACKs measure that interval using the recorded local
impulse time and checkpoint time. A repeated ACK never grows the estimate;
with no pending impulses there is no extra reaction interval. This remains
an approximate velocity prediction; actual remote poses, sweeps, mass and
exactly-once cumulative momentum consumption remain worker-owned. Tests now
delay both input and publication by 100-300 ms at 30/60/144 Hz, including
swapped masses, opposing engines and a parked target. The formerly stationary
head-on cases advance; a separate two-way delayed off-centre wreck test
retains translation and rotation. This is not proof of native retail parity.

The live Bot previously retained centre-only placement after a shove even
though wrecks used descriptor-derived track support. Received contact now
enables that existing support solver for the affected live hull as well.
Untouched autonomous Bots keep their previous sampling cost; hydraulic
exclusions and descriptor failure boundaries remain. A passive grounded
world sweep follows a continuous supporting bank only after current/middle/
destination ground samples prove it. Future lower floors, missing support,
airborne motion, new walls, actual vehicle bodies and the arena boundary do
not receive that exemption. This addresses the reported uphill-facing bank
veto and centre-only cliff perch, not a claim that every loaded-map ledge or
the additional native probe cost has been accepted in Windows.

The user's profile 1 changes all three KV-5 107 mm rounds' device damage to
5,000, a representable value, not an integer overflow. Internal layout cache
keys previously ignored asynchronously loaded hit-tester bounds: an incomplete
first query (e.g. missing gun bounds) could keep the entire interior invalid
after the same descriptor finished loading. The cache now includes all four
component bounds. A regression reproduces invalid-to-valid recovery and a
reached-rack test exercises 5,000 damage across shell kinds and owners,
retaining the single 27% saving throw. The reports do not contain per-shot
interior evidence, so this is not proof that caching caused every reported
miss. Compact unavailable-layout and high-device-damage strike witnesses now
distinguish effective damage, missing geometry, unreached modules, exit/stop
filters, saving throws and applied loss without restoring per-triangle logs.

Validation: 304 targeted cases pass; the 1,474-case subsystem selection passes
with one environment skip and the same six explicit navigation exclusions.
The full 528-case Bot suite retains 26 failures and 14 errors; the 27-case
gameplay gate retains seven failures and two errors, with both complete
failure-identifier multisets matching the parent. All 146 client sources
compile with CPython 2.7.18 and exact Chinese HD #1513 inspection/ABI audits
pass. No native API was added. Native shove feel, bank departure and modified
shell hit locations remain test-package acceptance items; no game session was
run by the agent. Earlier outstanding replay/fall-injury work is not claimed
fixed by this change.

### Report 194317: replica prediction used an empty authority cache

The report identifies test-20260928-254db07 in all three processes. The visible
client is deliberately not Bot authority: battle_start returns before creating
states, descriptors or physics caches. Contact velocity prediction nevertheless
called _physics_params_for on that non-authority runtime. Its missing descriptor
silently derived the 5,730 kg / 45 hp defaults. Pending momentum was divided by
that mass instead of the installed Bot mass, exaggerating the predicted escape
velocity and suppressing subsequent reciprocal pushes. The previous coupled
tests shared the worker object, concealing this lifecycle defect.

The replica now derives parameters from the presented entity's installed
descriptor, published rating and round tactics through the same crew/physics
adapter as the worker. A per-record descriptor/rating/slot cache avoids repeated
native crew evaluation. Prediction, ground reaction and powered traverse share
those parameters; a compact first-contact witness records mass and power.
The replica creates no authority state, changes no vehicle coefficients and
still treats only the displayed hull as occupied space. The worker retains
canonical momentum, swept movement and HP ownership.

The regression starts an actual non-authority round with empty caches and makes
any authority-cache lookup fail. A 35,500 kg vehicle receiving (71,000, -35,500)
momentum yields (2, -1) m/s for both live and dead wire states. Coupled tests now
use separate owners, the real contact-body adapter and worker update_once rather
than a shared cache or an abbreviated force loop. At 150 ms delay in each
direction, reinstating the old default cache reproduces zero KV-5/AT-15A head-on
travel; the corrected six-second synthetic scenario advances 8.88 m. Swapped
owners, weaker engines, reverse input and 30/60/144 Hz remain covered. Both a
live and a dead hull can be pushed beyond a supported ledge into a 30 m drop
with the existing ten-spring support solver. These are regression scenarios,
not measured native vehicle performance or retail calibration.

The report also records real native bridge-side vetoes. Wreck 19 eventually
falls from about 0.85 to -14.90 m, while live Bot 6 remains blocked near the
bridge. The source change above repairs deficient sustained push, but native
loaded-map bridge departure is not certified by the synthetic ledge test.
New-wall and occupied-hull guards remain intact; no unproved bridge-normal
exception ships in this change.

High-damage strike witnesses show the configured 5,000 device damage arriving,
including applied engine and surveying-device losses. Five reached ammoBay
candidates were all rejected by the existing single 27% damage-chance roll;
there is no evidence of overflow or universally disabled rack damage. A larger
device-damage number changes loss after a successful roll, not hit geometry or
the roll probability. Tests still cover success and rejection with that value.
The report also proves unavailable internal layouts for R98_T44_85M,
J30_Edelweiss and R121_KV4_KTT. The read-only catalog audit finds 650 decoded
console meshes, eight reconstructed profiles and 22 unavailable vehicle layouts.
Those missing interiors are a real unresolved coverage limitation; generic
invented boxes or another vehicle's layout were not substituted.

Validation: 313 focused cases pass. The 1,474-case subsystem selection passes
with one environment skip and the same six documented navigation exclusions.
All 146 client sources compile under CPython 2.7.18, and exact Chinese HD #1513
inspection and ABI audit pass. Existing test doubles were updated to supply
explicit contact parameters; the new lifecycle regression uses the real replica.
No native API was added and no native game session was run. Earlier replay,
fall-injury and missing-layout work is not claimed fixed here.

### Report 203327: bank slip, visual debris and module outcomes

All three processes identify test-20260928-1e4501d on 37_caucasus. Contact
parameter witnesses now agree with the mounted masses (WZ-120-1G 36,000 kg,
M41 23,496 kg). WZ-120-1G Bot 11 reaches the lower level, but the sparse old
motion witnesses cannot prove a continuous rendered descent. M41 wreck 29
stops around (-27.217, -1.84, 113.370); its preceding native bank normal is
(-0.30123034, 0.861268997, -0.40923822).

The worker's suspension path omitted the visible player's existing passive
side-slip integration. It could settle height and attitude on a steep bank
without ever advancing down it. Worker live hulls now apply the same lateral
slide law; passive wrecks have no drivetrain-owned forward gravity and use
the complete fall line. Motion still passes actual vehicle, world and arena
bounds guards. The autonomous hazard check uses the driven endpoint before
passive slip, so gravity is not reclassified as a forbidden driving command.
Both owners retain already accepted horizontal slide momentum when partial
edge support cannot fit a plane. Timed integration and zero-time contact
projection remain separate; only the former advances slip. Rollback includes
the slide/carry state. Active slope-bound wrecks continue ticking after the
pusher separates. Bounded EDGE witnesses now record worker descent and support.

Tests use the report bank normal, a finite bank and a lower floor, then the
real worker battle_start/update_once lifecycle with independently installed
descriptors. Live and dead hulls slide off and fall; a wall or another hull
still blocks them. Separate tests cover the final driver-hazard boundary,
combined contact resweep and zero-time projection. These synthetic scenes do
not certify the loaded native bridge mesh or visible snapshot interpolation.

The captured worst post-explosion frame spends 196.5 ms in navigation search,
109.2 ms in vertical integration and 15.5 ms in turret update. The worker is
already slow before the explosion. Turret rigid-body debt subsequently grows
beyond 12 seconds, explaining delayed flight independently of GPU rendering.
Gameplay now uses a frozen, worker-authored visual flight with stock models
and explosion/touchdown effects. Detached debris temporarily registers no
vehicle, shell, reticle or navigation obstacle and does not run the continuing
rigid-body solver. It cannot be pushed or used as cover. The single bounded
world arc still stops at scenery; this is a deliberate temporary performance
tradeoff, not a claim that all native lag has disappeared. Existing rigid-body
code and its independent law tests are retained for later measured work.

Navigation no longer probes edges that cannot improve an A* candidate, shares
unknown exact-segment failures only within the current callback (retrying
streamed geometry next callback), evicts one receipt instead of clearing all
4,096 entries, and changes wreck path revisions only when occupied graph edges
change. No search, projectile or collision safety budget was reduced.

The high-damage report has no unavailable-layout witness. Jagdpanther Bot 24
receives a penetrated, reached ammoBay hit with 5,962.514 rolled device damage;
the successful existing saving throw destroys its rack. E75 has two reached
ammoBay candidates rejected by the saving throw, another penetrated ray with
no interior contact, and separate non-penetrations. Tortoise mostly receives
non-penetrations; two external track hits apply device loss. These are not
overflow symptoms. Earlier missing per-vehicle interior meshes remain a real
limitation (650 decoded, eight reconstructed, 22 unavailable in the catalog
audit); no guessed interior geometry was added. High-damage diagnostics now
include the actual probability/draw, attacker and crew outcome, without an
extra RNG call or any change to hit probability, HP or voice selection.

Validation: 1,474 subsystem cases pass with one environment skip and the six
previously recorded navigation exclusions. The focused selection has one
pre-existing overturned-roof assertion, reproduced from parent 1e4501d with
the identical 2.5402 versus 2.775 m result. Navigation/replan/route selection
retains the parent's eight missing-adapter errors; the three new navigation
tests pass. Exact Chinese HD #1513 inspection, ABI audit and Python 2.7.18
compile pass. No native API was added and no native game session was run.
Native bridge departure, frame pacing and visual debris remain test-package
acceptance items rather than certified runtime outcomes.

### Report 212552: align worker bridge support with the player adapter

Report ae6a8b89d74f runs test-20260928-28c2c0a in all three processes.
M41 wreck 29 holds X/Z (3.86856, 96.95354) while its roll changes from about
1.0 to 2.24 radians and its origin drops below the deck. T71 Bot 15 later
stays at (5.83559, 0.31036, 99.74456), roll 0.5292, with eight contacts but
no fitted plane. Its native translation trace hits upward deck material 108
at y=0.97408. The report establishes a conflicting support/motion state, not
an engine-power shortage. It does not contain every native support column,
so a complete reconstruction of that bridge's collision mesh is not claimed.

The worker was missing three existing player-adapter contracts: recovery of
fresh support inside each posed continuous track footprint, rigid hull/turret
contacts while tipping, and an upward-only predicted query-window shift.
It could acquire the lower beam through a missing carrier, apply track springs
after overturning, omit roof/side support, and move its next column window
below a deck by extrapolating a mixed-layer plane. The worker now uses the
same geometry helpers, posed turret yaw, angular/vertical sweep, rigid-contact
freshness and near-inverted spring suppression as the player. No collision
budget, mass, power, friction or damage coefficient is changed. Normal flat
support still has 22 probes; only missing track patches and tipping add the
existing geometry recovery work. Physical translation still passes world,
vehicle, arena and detached-obstacle guards independently of navigation.

Parent 28c2c0a reproduces the sparse-deck sink for both living/dead states at
30 and 120 Hz, the below-deck query-window error, and the previously recorded
overturned-roof failure (2.5402 versus 2.775 m). These now pass. A complete
worker update/receipt lifecycle additionally pushes living and dead hulls over
a finite deck using actual segment-based world sweeps; a separate wall still
blocks the shove. The focused 251 cases pass. The 1,474-case subsystem set
passes with its existing one environment skip and six named navigation
exclusions. Exact installed #1513 inspection, ABI audit and all 146 client
sources compiled under Python 2.7.18 pass. These are offline regressions;
the reported native bridge and frame pacing still require package acceptance.

The full installed roster was audited again with mesh topology verification:
680 entries, 650 decoded Console layouts, eight authored reconstructions,
22 unavailable layouts. The checked-in original bake evidence distinguishes
19 unavailable source entries from three unregistered/no-interior entries
(Nameless, Edelweiss and T23). Another 176 decoded entries list observation
device and/or turret-rotator holes, including two incomplete crew rosters;
these are not 176 missing ammo racks, and non-applicable fixed-gun components
must not be labelled missing gameplay modules without checking the descriptor.
A user-facing Chinese inventory, exact IDs and remediation notes accompany
the test output; no guessed module geometry was added.

This report also proves why high device damage alone does not force criticals:
M41's HE ammo candidate has chance 0.27/draw 0.34806; T-44-122's AP ammo
candidate has chance 0.30/draw 0.77299. Both are saved. T-43's engine candidate
has chance 0.48/draw 0.08289 and applies 6181.364 damage. These vehicles have
internal contacts; this evidence does not support numeric overflow. The
probability law, crew outcomes and existing visual-only turret policy remain
unchanged.

## Reports 220344 and 221207: posed departures and directional service braking

Both reports identify test-20260928-96b43dc in the server, visible client and
worker. Report 221207 uses AMX 13 57 on Prokhorovka with both speed limits at
27.778 m/s. The user identifies W-to-S and S-to-W reversal as slow braking;
the handbrake already stops promptly. The old throttle branch uses only
power-limited reverse engine force while neutral adds a 65% track-grip brake.
Neutral now retains descriptor rolling resistance and slope gravity. Explicit
opposite direction changes latch the existing installed service brake until
zero speed, then engage drive in the requested direction. Held throttle being
back-driven by contact/gravity does not latch brakes. The Bot owner excludes
its already-accounted external velocity when identifying its own reversal;
resuming drive while pushed must not turn a weak engine into a parking lock.
A flag carries brake intent through the compact state codec, normal/contained
server publication, adoption and replica contact prediction. Input-edge logs
record brake intent and before/after speed without per-frame log spam. Static
hold, handbrake, airborne momentum and the directional 110% cap remain intact.
This uses the existing copied brake law, not a claimed recovered retail curve.

Report 220344 records dead T-44-122 at (46.33971,-3.46723,59.41939),
pitch 1.40818, roll -0.06863. Its outward 0.090 m displacement was rejected
by a native solid lane on the bridge/slope face. The passive sweep previously
posed only Y, leaving flat XZ extents even for this nearly vertical hull.
Passive lanes now apply the full existing BigWorld YPR transform, including
the displaced destination perimeter and prepared collision-filter envelope.
Departure containment uses the rigid inverse and the exact swept material
interval; inward surfaces and later independent walls still block. A tipped
body on an undrivable face no longer extrapolates its attitude as a roadway.
Navigation hazards remain autonomous planning constraints; world, other hulls
and arena bounds still constrain physical displacement. The conservative
active-drive terrain lookahead remains separate from the posed passive sweep.

The captured-plane regression fails on parent 96b43dc and passes here; its
fixture explicitly uses the captured XZ extents and a test height band, not
an invented reconstruction of the complete map. It includes a later blocking
wall. Near-vertical inverse, transformed perimeter and upper-wall tests pass.
Both 100 km/h direction changes, intervening neutral, airborne input, installed
brake force, live local/worker paths, compact/server/replica publication and
owner-reversed head-on contacts are covered. The 242 focused cases and the
251 bridge/turret preservation cases pass (one environment skip in the former).
The 1,480-case subsystem suite passes with its existing one environment skip
and six named navigation exclusions. Exact #1513 inspection/ABI audit and all
146 client source files compile under CPython 2.7.18. Existing unrelated full
suite failures are reported separately; this is not an all-green CI claim.
Detached ammo-rack turrets remain visual-only, as the user confirms markedly
better frame pacing. No native game session was run for this change: continuous
bridge/cliff departures and driving feel require the generated package test.

## Report 224302: restore release braking and requery the known deck

This report identifies test-20260928-64c2cd4 in all three processes. The user
explicitly rejects the removal of automatic deceleration: released drive was
already correct; only intentional forward/reverse braking needed repair.
Restore the exact 96b43dc neutral law, its existing 0.65 grip share, tuning key,
and steep-descent relief. A 1,134-case comparison against that commit matches
exactly across both directions, terrain, steering, timestep and slope. This
supersedes the preceding section's rolling-resistance-only neutral behavior.
No new coast coefficient or retail stopping-distance claim is introduced.

Keep the intentional service-brake latch. The report already shows it active:
at 22:38:59.223 forward speed 28.9342 becomes 28.8043 m/s under command -1;
at 22:42:34.130 reverse speed -2.3011 becomes -2.0582 under command +1.
These observations establish the deployed path, not numerical retail parity.
The restored neutral law and full service brake are tested through the real
input sender, including manual movement flags, both native cruise flags and
the cruise mailbox, with both speed limits tuned to 100 km/h. Braking stops at
zero before opposite drive begins. Releasing either input clears the latch
and recovers the previous neutral drag. A held input pushed backward still
cannot acquire an automatic parking lock.

The exact installed #1513 Avatar.pyc was inspected under CPython 2.7.18:
handleKey changes __cruiseControlMode by one or selects the end mode on a
double press, updates the stock panel, and issues moveVehicle only when no
manual W/S command is held. makeVehicleMovementCommandByKeys emits FORWARD
or BACKWARD plus CRUISE_CONTROL25/50; moveVehicle forwards those flags to
base.vehicle_moveWith. R/F therefore retain stock cruise-preset semantics;
an actual opposite-direction command engages the same service brake as W/S.
No raw-key override replaces stock input/HUD ownership. Official controls and
the 2015 physics-test article confirm cruise controls and distinct braking
behavior, but do not expose the retail C++ brake curve:
https://worldoftanks.eu/en/content/guide/newcomers-guide/game_controls/
https://worldoftanks.eu/en/news/general-news/public-test-new-physics-2015/

The bridge issue remains observed, not accepted as fixed. Bot 19 is at
(32.239887,0.393629,92.344363), roll -0.37283, while its solid sweep hits
upward support at y=0.909833. Bot 15 later receives an inward vertical-face
hit after its origin has descended below the bridge. Those hits must not be
blindly ignored. A reproducible support-query defect is corrected: the shared
flat support ceiling was allowed by the layer filter but did not raise a low
carrier's actual ray start, so it could see only the lower beam after tilting.
Both motion owners now widen that ray up to the previous proved support plane,
capped by the existing legal carrier envelope. Fresh native geometry must
still provide the height; the old plane alone creates no contact. Airborne
queries and unrelated roofs retain their existing limits.

A finite layered-deck regression fails on 64c2cd4 for both sides and owners,
then passes with this change; the synthetic chassis is explicitly not a full
reconstruction of the report's native mesh/descriptor. Missing deck columns
and an unrelated roof cannot become support. The 243 targeted cases and 252
bridge/turret preservation cases pass. The 1,480-case subsystem selection
passes with its existing one environment skip and six named navigation
exclusions. Exact installed-client inspection/ABI audit and Python 2.7.18
compilation of all 146 client modules pass. The reduced suspension model and
loaded-map edge departure still require #1513 playtesting; the report does
not prove that navigation avoidance is the blocker. Detached turrets remain
visual-only, retaining the user-confirmed frame-pacing improvement.

## Report 231925: user-selected half-strength release braking trial

All three report processes identify test-20260928-c2f7507. On near-flat ground,
KV-5 released drive decelerates at approximately 11.33 m/s^2; intentional
opposite-direction braking and the handbrake retain approximately 15.94 m/s^2.
The old 0.65 share was added to rolling resistance, making total release drag
about 71% of the active brake for this 100,575 kg installation. It was never
a recovered retail coefficient. Searches of official movement-physics notes
and player discussions did not establish a numerical 0.9.22 coast/brake ratio:
https://worldoftanks.com/en/news/general-news/public-test-improvements/
https://worldoftanks.eu/en/news/general-news/public-test-physics-september/
https://arstechnica.com/civis/threads/world-of-tanks-ask-about-invite-codes-before-signing-up.1112547/page-1314
The official material describes release/turn behavior and sharp handbraking;
the forum is qualitative experience, not a force measurement.

The user explicitly requests a 50% trial when no reliable published ratio is
available. Set total released-drive resistance to half the installed, grip-
limited service brake, including rolling resistance rather than adding it a
second time. Physical rolling resistance remains a floor. Preserve the existing
steep-descent brake relief, gravity, parked hold, handbrake, intentional reversal
latch, airborne momentum and contact pushing. Hence 50% describes flat-ground
braking, not net acceleration on every slope or arbitrarily high rolling drag.
At the ordinary full-grip limit the new release deceleration is 7.970625 m/s^2
and the unchanged active brake is 15.94125 m/s^2. For the report's KV-5 these
are about 801,645 N and 1,603,290 N, respectively. R/F still select native cruise
presets; releasing a cruise key alone does not cancel an active preset.

The 245 targeted direction/physics/bridge/world/wreck tests pass. Ratio coverage
includes both directions, 100 km/h limits, masses, installed brake capacities,
terrain and timestep; the real manual-input and cruise-cancellation paths are
also exercised. A 1,800-case comparison with the parent matches unchanged drive,
service braking, handbrake, airborne and static behavior exactly. Four siege
subtest failures and the removed-traffic-symbol import error encountered in an
additional selection reproduce unchanged on the parent; they are not claimed
fixed by this trial.

The bridge report contains real progressive falls: Bot 9 descends from y=0.644
at 23:13:16.229 to y=-14.495 at 23:13:18.332 with intermediate airborne positions
and increasing downward speed. This does not prove replica presentation is
smooth. Worker frame pacing also remains poor in parts of this session (4.56
frames/s in round 2 window 5). Collision/support queries still report blockers,
and Bot 15 has support rollback after a fall. Neither the apparent teleport nor
every invisible blocker is isolated to one proved cause by these logs. This
package changes release braking only; it does not claim another bridge fix or
disable any native wall contact. Native driving feel remains a Windows test.

## Reports 234547, 235110 and 235422: rollover replication and death messages

All three sessions identify test-20260928-29d2fea. The user accepts the driving
feel; this change preserves vehicle_physics.py and both braking adapters.
In 235422 on 37_caucasus, Bot 29 (M41 Bulldog) actually leaves the deck: y=0.5641,
roll=0.41632 at 23:53:35.942, y=-0.17095, roll=1.40432 at 23:53:36.253, and
eventually y=-14.687445, pitch=-15.797617, roll=9.398418. No navigation rollback
is reported in that fall. However, LANClient's shared runtime-row validator
rejects pitch or roll above 0.61 radians and substitutes the complete old row,
including position and health. The worker and visible client repeatedly log
one retained row. The Bot encoder separately clips accumulated rotations to
pi, which is not an equivalent orientation after a full turn.

Normalize periodic Bot angles before fixed-point encoding and in the server's
mapping publication path. Accept the full principal angle interval at the
replica boundary. Human input, its server admission/update, and ram-contact
pose validation now carry the same full interval; human angles are normalized
instead of clipped. Keep the continuous suspension state, integration, contact
queries, wall collisions and navigation rules unchanged. Regression coverage
replays the report's falling/overturned attitudes through both publication
forms, the server and replica validator, for live and dead Bots, plus human
sender/server/replica round trips. This fixes a proven frozen-presentation
cause; it does not establish that every bridge blocker or fall is correct.

Exact #1513 scripts.pkg bytecode audit: ClientArena.__onVehicleKilled consumes
(victim, attacker, equipment, reason), marks the roster dead and dispatches the
arena event. PlayerAvatar.__onArenaVehicleKilled returns for its own vehicle
after death info/camera handling. BattleMessagesController.showVehicleKilledMessage
also returns for the currently observed vehicle after the player dies. Those
guards intentionally omit PlayerMessages' right-side text. The LAN binding
now supplements just these suppressed messages after the unchanged arena
dispatch, using msgs_ctrl.__getKillInfo and onShowPlayerMessageByCode's exact
five-argument event. It does not replay sounds or alter either vehicle ID.
gui/player_messages_panel.xml has no SHOT_*_SELF template, so local victims use
the existing allied-victim wording and real roster names. The PlayerMessages
consumer and FadingMessages postfix lookup were audited as well. Guard-aware
tests cover own death, suicide, an observed LAN ally, and ordinary remote death
without duplicate text; existing death-edge/postmortem tests remain intact.
The reports contain one localhost human, so a second LAN participant remains
a native acceptance case rather than a reproduced session in this evidence.

The displayed ping is not pure network RTT: LANClient.worker_ping_display uses
the worker's rolling frame interval, while worker_rtt_ms is a separate value.
In 234547's worst 30-second window, the worker has 122 frames / 30.114 seconds
(4.05 FPS), mean gap 246.833 ms and mean execution 225.401 ms; the corresponding
visible window is about 76 FPS. Bot vertical motion costs 10.119 seconds of that
window, including ground-query work; its own Python cost is 6.436 seconds.
Ground probes account for 127,130 logical calls and 3.693 seconds. Other large
costs include the Bot update loop, visibility, driving and planning. Slow
frames then execute multiple simulation slices to catch up. This identifies
backend simulation pressure, not a measured 230 ms LAN hop or a proved graphics
failure. No cadence, collision safety, physical constants or performance claim
is changed by this patch; native profiling/optimization remains separate.

Validation: 1,181 protocol/codec/entity/battle/direction/bridge tests pass with
one unavailable-interpreter skip; 160 server/lineage/contact/snapshot tests pass.
An independent CPython 2.7.18 run compiles all 146 client modules and matches
Python 3.12.14 rollover wire rows exactly. Installed-client inspection passes.
These are logic and ABI checks, not a claim of new native gameplay acceptance.
The additional 617-case Bot/physics run has 26 failures and 14 errors; rerunning
all 35 affected methods with the parent server/codec/client modules reproduces
the same failure/error counts. These pre-existing navigation, water and stale
interface expectations are not presented as passing or repaired here.

Final consumer audit also found that SnapshotSync blended pitch/roll linearly
while yaw already used periodic deltas. Normalizing an overturn across +/-pi
would otherwise turn it back through upright for one presentation segment.
Apply the same shortest-angle delta to body attitude in timed interpolation,
ordinary live chase, wreck chase and wreck-settlement error. Native remote
matrix mirroring already treats all three axes periodically. A new regression
fails in all 16 combinations of player/Bot, live/dead, timed/untimed and both
seam directions before the change, then passes; gun articulation is unchanged.
The final 1,198-case snapshot/battle/entity/protocol/codec selection passes with
the same optional interpreter skip. All 146 client modules compile under 2.7.

## Report 004720: squad presentation, own death cause and bridge-side suspension

The report identifies test-20260929-5bd525e on 37_caucasus (Victory Gate).
In round 2, Bot 22 (A-43) tips below the bridge and then remains at
(23.32924, 0.149746, 83.84564), pitch=-0.891759 and roll=-1.280716, including
after destruction. Its last progressing sample had downward speed -1.1213.
Nearby contact queries see the upper deck at y=0.9067 with an upward normal.
This is not the already-fixed replica angle guard. After every posed track
and hull support sample misses, the worker's extra broad centre ray can still
hit that upper deck. The hidden-raised-support guard then restores the old
pose and cancels gravity, even without horizontal movement. Remove that
unsupported rollback and its now-unused probe; retain actual posed supports,
supported step/rise rejection, invalid-pose rejection and world wall sweeps.
A regression using the report's attitude/height and an overhead-deck return
fails on b07cad8 for both live and dead vehicles, then falls continuously with
this patch. A real deck below the hull still catches it. The fixture uses a
test descriptor, not native A-43 geometry; Windows bridge acceptance remains
required. No braking coefficients or accepted driving feel change.

Public PC guides and the supplied screenshots establish the intended visual
context, but do not specify the old #1513 own-death message contract. The
installed #1513 scripts.pkg, player_messages_panel.xml, postmortem_panel.xml
and Chinese ingame_gui.mo were therefore audited directly. Client inspection
confirms the exact supported x86 build. Native FadingMessages.__formatEntitiesEx
colors each attacker/target name using isSquadMan and the native squad color.
ArenaDataProvider.getPlayerGuiProps feeds both VehicleMarkerPlugin and
ArenaVehiclesPlugin; squad status also reaches the roster presentation flags.
Both local and remote humans now publish the team's nonzero prebattleID in
publicInfo and the stock 18-field arena roster (index 10) when at least two
humans share that team. Bots and solo humans keep zero; opposing teams never
share an ID. This is a LAN presentation group, not a fabricated WG unit.
The native squad finders read SQUAD_RANGE_TO_SHOW at call time and otherwise
discard groups outside 2-3 members. During offline compatibility installation,
extend that presentation range to the room's 30-human limit; restore the
original object on rollback/fini. Native green/red message bodies remain
intact, with only actual squad participants using the native gold color.

PlayerAvatar's own-kill branch sends DEATH_INFO, which only the special summary
postmortem panel consumes. The regular PostmortemPanel instead listens to the
five-argument onShowVehicleMessageByCode event from server damage info. Supply
that missing vehicle-only event after the existing arena death dispatch,
without replaying the paired player-message event or voice. Preserve shot,
fire, ammo-rack explosion and unknown cause codes. The Chinese shot resource
means "Tank destroyed by this player:"; an unknown killer uses "Your tank was
destroyed". The right-side own/observed-vehicle supplement requested earlier
remains an offline addition using stock allied-victim templates: #1513 itself
intentionally suppresses that ordinary own-kill line. Do not describe this
addition as stock retail behavior. Guard-aware tests cover the own/observed
split and cause selection. Local/remote entity tests check squad identity at
the actual producer boundary as well as group sizes and round team changes.

Validation on Python 3.12.14: the 1,198-case compatibility/entity/battle suite
passes. All 146 client modules compile with independent CPython 2.7.18.
The 270-case selected Bot/support/fall/physics/wreck/world suite has 3 failures
and 1 error. Re-executing the affected methods against b07cad8 reproduces all
four existing diagnostics (slot-height expectation, both directions of legacy
downhill contact, and a stale TerrainNavigator.clear_blocked_contact fixture).
The two new unsupported-fall subcases fail only on the parent; the below-hull
support control passes on both. These results do not establish native LAN UI
rendering, all bridge positions, or improved frame time. The reported worker
performance issue remains separate; no collision safety was removed for speed.

## Report 014503: environmental death feed and T71 bank contact

The session uses test-20260929-e5bd274 on 37_caucasus, with one local KV-5
player. The player's recorded death is a reciprocal ram at 01:44:34. The log
does not independently demonstrate a second LAN human or a fatal environment
event, so the reported missing categories were traced through their producers
and exact installed consumers rather than claimed as captured render evidence.
Server environment admission retains attacker zero and reason 3/5/7 for
world collision/drowning/overturn; the common health edge dispatches each
death once. These authority, HP, credit and replay-deduplication paths remain
unchanged.

Reinspection confirms Chinese HD #1513 x86. The installed msgs_ctrl
__getKillInfo produces UNKNOWN_ALLY/UNKNOWN_ENEMY for attacker zero, while
player_messages_panel.xml contains no such collision/drowning/overturn
variants. FadingMessages.showMessage checks the extended key then the base
key and returns without displaying anything when both are absent. Its
unassisted SUICIDE variants instead name the victim, with red/purple for an
ally and green for an enemy. Select those display variants without replacing
either arena ID or assigning self-kill credit. Ordinary SHOT_UNKNOWN keys
exist in XML but their Chinese ingame_gui.mo translations are absent: render
the installed neutral SHOT_SELF_ENEMY victim text with the UNKNOWN entry's
team colors, restoring the instance template in finally after the synchronous
native formatter returns. Do not label an unknown shot killer as suicide.

PlayerMessages.showMessage now has a scoped offline adapter. Personal SELF
templates omit the local name and select the native whole-line self style;
named-ally templates retain the victim-relative red/green body. The adapter
colors only complete personal or squad name/vehicle fields using the native
color manager's squad RGBA and _EXTRA_COLOR_FORMAT, including a solo local
player. Other names keep native formatting. Preformatted fields are removed
from the remaining native extra list to avoid nested color tags. The stock
PlayerMessages event consumer, FadingMessages formatter/render dispatch and
actual Chinese XML/MO templates were audited. This adapter adds no message
event, sound, voice, damage or kill-credit changes. Installation is idempotent;
teardown restores either the original owned method or inherited lookup, using
the class dictionary to avoid Python 2 unbound-method identity mistakes.

An independent CPython 2.7 audit executes the actual installed __getKillInfo,
PlayerMessages consumer and all three FadingMessages display/format methods.
The native no-adapter baseline drops three unknown-attacker environmental
deaths. With the adapter, 210 combinations of seven supported attack reasons,
five personal/squad/ally/enemy participants and six killer identities each
produce one localized message with the expected team body and personal/squad
name colors. This reaches the mocked Flash dispatch, not native rendering.
Pure-data tests cover the missing-template guard, no double wrapping, unchanged
non-death messages, neutral unknown-shot text and restoration on errors, plus
compatibility install/reinstall/fini for owned and inherited methods.

T71 (Bot 15, usa:A103_T71E1) initially falls from y=0.763 at 01:42:08.070,
with subsequent bank contacts. At 01:42:12.513 the measured support plane has
normal (0.396413, 0.564967, -0.723650), gradient (-0.701657, 1.280872) and ten
samples; a following world query also hits the actual steep bank. Such
contact can legitimately rotate the hull. However, by 01:42:15.819 the Bot
remains exactly at (-21.345632, -4.404956, 112.986892), pitch=13.854776 and
roll=-7.435818, repeatedly reporting support_rollback through 01:42:43.309.
The Bot's anti-step guard incorrectly classifies model-origin correction from
a rigid hull/turret contact as a driven step. The player adapter already
excludes rigid_contact_count from this guard; apply that same exclusion to
live/dead Bots. Real spring-supported steps, invalid solver jumps and world
collision sweeps retain their guards. No physical coefficient changes.

A report-pose/bank-plane regression with the fixture descriptor reproduces
the frozen rollback for live and dead vehicles on e5bd274. Its solver produces
one rigid contact and zero limit excess, but the old adapter discards it every
time; the new adapter accepts the correction and continues integrating. This
proves the guard defect, not exact T71 geometry or all visual behavior during
the preceding tumble. Native same-position bridge acceptance remains needed.

Validation: all 1,203 compatibility/entity/battle cases pass on Python 3.12.14;
all 146 client modules compile with CPython 2.7.18. The selected 271-case
Bot/support/fall/physics/wreck/world suite retains three failures and one error.
The affected methods reproduce identically against e5bd274 (slot-height
expectation, two legacy downhill subcases and stale clear_blocked_contact
fixture). The two new rigid-bank subcases fail on that parent and pass after
the fix. The accepted braking calibration, native physical contacts and
existing performance limitations remain unchanged.

## Report 021924: personal/squad messages and the governor switch

The report identifies test-20260929-a614d83, including 37_caucasus, with one
local human. The user's clarification supersedes the previous personal-feed
presentation: own deaths use the central PostmortemPanel only, own enemy kills
retain native SELF templates and their whole-line self color, and only other
LAN squad members receive gold name/vehicle fields inside team-colored text.
Remove the extra own-death PlayerMessages event while preserving its reviewed
five-argument VehicleMessage event. Suppress an own-victim feed at the scoped
formatter too. The observed-teammate supplement and missing environmental
templates remain. No credit, sound, voice or physical behavior changes.

The exact installed BattleMessagesController.showAllyHitMessage emits
ALLY_HIT with an entity argument and an extra (entity, vehicleID) tuple, not
the death handler's target field. Extend squad-only formatting to that native
shape. An ordinary ally retains the native red message; a LAN squad ally has
a gold name/vehicle, including when the stock formatter would prefer its
team-killer color. Preserve all non-squad fields and avoid double wrapping.
The independent CPython 2.7 execution audit covers the actual controller,
PlayerMessages and FadingMessages methods, Chinese XML/MO resources, 210 death
combinations and four friendly-hit/squad/team-killer combinations. Own deaths
are intentionally absent; all other expected lines reach the Flash boundary.

Reinspection of Chinese HD #1513 confirms the removedRpmLimiter descriptor:
item ID 12, trigger tag, 1.1 engine-power factor and 1.5 engine HP lost per
second. The previous active echo (quantity=1, PREPARING, time=0) is incorrect.
Native _EquipmentItem.isAvailableToUse requires READY; _TriggerItem uses zero
remaining time to generate the activate flag (65536 + ID), and nonzero time
to generate the deactivate ID. EquipmentsController treats PREPARING as a
targeted combat item and deactivates it when another item is used. Echo an
active governor as (1, READY, -1), preserving native indefinite activation,
availability and the next off command. Deduplication includes the indefinite
activity bit without republishing every cooldown tick. Actual installed
canActivate/activate/deactivate/getActivationCode and controller methods pass
five alternating states and unrelated-item isolation; the old echo reproduces
the failures. These checks do not claim native Flash rendering acceptance.

The user supplied this first-hand 2024 experiment:
https://www.reddit.com/r/WorldofTanks/comments/1b1go9f/how_the_speed_governor_works_in_wot/
Apply its below-half-base-forward-speed gate and one-second wear pulses to
the authoritative human critical-state path, using the accepted physics speed
and the descriptor's rate. Absolute reverse speed uses the same forward
threshold. Off or cruising states discard an incomplete pulse, as does an
accepted switch edge; new rounds and new equipment installations reset it.
Late ticks preserve complete elapsed seconds. The 60 HP example describes
engine module durability, not horsepower: 30 HP are lost in 20 seconds.
The 1.1/1.5 values are pinned-client evidence; the speed/pulse rule is the
user's supplied modern experimental reference, not recovered #1513 server
code. Do not infer an exception for health-boosting equipment from the disputed
comment alone; the existing module health profile remains authoritative.

Official WG descriptions call the governor permanent and never consumed,
including https://worldoftanks.eu/uk/news/specials/weekly-offers-4-february2022/ .
Further historical searches did not verify the user's active-at-death loss
exception. Implement it as the explicitly requested offline settlement rule,
not a certified retail fact: sample the canonical switch on the first actual
player death and charge one item only when active. Activation, survival,
inactive death and a live departure do not charge it. Existing receipt
deduplication and depot-first auto-resupply own the subsequent inventory
change. No visible replica becomes an inventory or combat authority.

Validation: 1,839 tests pass across compatibility, entities, battle runtime,
consumable audit, equipment mechanics, critical damage, postbattle, garage,
server projectiles and combat lineage on Python 3.12.14. Coverage includes
toggle retries, native command flags, power factors, speed boundaries,
one-second wear, terminal-state sampling, environment deaths, repeat receipts
after reopening the garage, and auto-resupply from stock or credits. All 146
client modules compile under CPython 2.7.18. Real Windows switch highlighting,
multiplayer message rendering and gameplay timing still need playtesting.

## Report 031122: central environment notices and governor wear evidence

The report contains three rounds on 37_caucasus using test-20260929-8599c30
with one human in a KV-5. Unassisted environmental deaths have attacker ID
zero. The exact #1513 BattleMessagesController classifies that as UNKNOWN_SELF,
but the installed postmortem_panel.xml contains neither the corresponding
environment UNKNOWN_SELF entries nor their generic base entries. Its central
consumer silently drops the message after both lookups fail. Select the stock
SELF_SUICIDE postfix for this presentation-only case: world collision,
drowning, overturn, death zone and crew loss in a world collision. Preserve
the arena event's zero attacker and all kill credit, and retain own-victim
suppression in the right-hand feed. Assisted deaths retain native attribution.
Fire, ammunition explosion and unknown shot causes keep their existing order.

The exact installed Chinese ingame_gui.mo provides the wording. Current online
survival/postmortem guides do not establish the historical Chinese strings.
An independent CPython 2.7 audit executes the installed controller, postmortem
code lookup, reason formatter and template renderer with installed XML/MO
data. The four principal zero-attacker causes reproduce a missing message
before the adapter correction and reach the central display boundary once
after it, without changing the arena attacker or sending a right-side feed.
The existing native 210 death combinations and four ally-hit combinations also
pass. Native Flash rendering remains a Windows acceptance boundary.

The installed KV-5 M500 has 360 engine module HP and 180 regeneration HP.
At the current 1.5 HP per eligible second, a healthy engine requires 120
seconds below half base forward speed with the governor active before it
becomes yellow. The previous 60 HP example cannot establish KV-5 timing.
A canonical equipment-install/activate/critical-tick/public-snapshot test at
the real 30 Hz cadence reproduces a one-tick delay: per-tick decimal rounding
leaves HP at 181.5 at nominal 120 seconds. Retain the floating accumulator and
allow only 1e-9 boundary noise; the same test now reaches critical 180 HP.
This small correction does not establish the cause of an indefinitely healthy
engine in native play. The old report has no accepted-switch or engine-HP
trace. Add accepted governor edges and five-second canonical wear records
(rate, speed, threshold, HP, maximum, state and fractional pulse) for mounted
governors, including inactive ones, to distinguish activation, eligibility and
presentation without additional native queries.

The first-round Bot 29 wreck briefly settles near (-17.15, 0.47, 111.06),
with 11-13 reported contacts and approximately 0.48 radians roll, before
falling to y=-13.88 at 03:01:57 and settling near -14.39. The third-round
Bot 29 wreck falls from y=1.08 to -10.47 between 03:08:55 and 03:08:57.
These are actual falls, but do not prove the earlier pause was physically
correct. The old EDGE record lacks separate spring/body heights. Second-round
live Bot 8 settles on a measured inclined plane near y=-6.06; this is distinct
from the reported dead wrecks. No sampled BOT MOTION record reports a support
or pose rollback. Preserve physical behavior and extend the existing EDGE
record with rigid-contact count and already-computed spring/body heights,
at its existing cadence. No extra collision query, braking change or
unverified bridge coefficient is introduced.

Validation: 1,841 tests pass across compatibility, entities, battle runtime,
consumable audit, equipment mechanics, critical damage, postbattle, garage,
server projectiles and combat lineage on Python 3.12.14. The new full-cadence
KV-5 test failed before the accumulator correction and passes afterward.
Native governor timing and bridge contact geometry still require a report
from the instrumented Windows build; neither symptom is claimed fully fixed.
All 146 client modules compile under CPython 2.7.18. Three existing bridge
support/bank regression methods also pass with EDGE logging explicitly
enabled, exercising zero-contact, spring-only and rigid-contact formatting.

## v0.9.6: confirmed governor wear and safe shutdown

Report 035714 uses e3d430e and records MT-25 engine HP reaching 129.5/260
and critical state at 03:53:05; high-speed intervals leave HP unchanged.
The KV-5 interval reaches 264/360, above its 180 HP yellow threshold.
Remove the temporary governor diagnostics without changing its accepted
switch, wear, power, settlement or resupply behavior.

The report's game.fini fails at tutorialLoaderFini after gui_personality.fini
has destroyed the Scaleform app. Exact #1513 ApplicationEffect accesses a
dead weak proxy; SetTriggerEffect.stop already supports a None layout.
At mod fini, arm a one-shot wrapper on game's cached tutorial finalizer.
Only during that finalizer, the native layout accessor maps ReferenceError
to None. Keep every effect's cleanup and restore both wrappers in finally.
Do not load GUI modules during partial startup or swallow other exceptions.
An independent Python 2.7 audit executes the installed accessor, trigger
stop and stopAll code: it reproduces the stock failure and completes cleanup
with the adapter, including live-layout trigger clearing and hook restoration.
The retained late sound guard then remains available for SoundGroups.destroy.
183 compatibility tests pass and all 146 client files compile under Python 2.7.

The native starter also waited ten seconds after both its Job and tracked
players were empty. Every descendant belongs to that Job: a live replacement
keeps its process count nonzero; an empty Job has no parent left to start
another descendant. Exit at the empty-Job boundary. Preserve stop events,
process tracking, exit codes and bounded crash-monitor handling. Rebuild the
x86 GUI executable with LLVM-MinGW 20260922 (MSVCRT), with warnings as errors.
Real Windows tests with a controlled native client reproduce the old delay
in both normal and handoff cases; the rebuilt starter completes in 0-0.1 s
after the final child exits and keeps a live replacement running after its
parent exits. The 24 starter tests pass. These are process-lifetime tests,
not a claim of a measured full #1513 game shutdown speedup.

Maintenance tests previously inherited the host APPDATA and could reach real
saves. Pin their default environment to the test's temporary/fallback paths;
external-save cases explicitly supply a temporary APPDATA. A regression
exercises normal install, forced reinstall and startup repair against default
and named external slots, including legacy state and rotated backups. The
746 launcher tests pass (14 platform/environment skips). Product reset stays
behind its separate confirmation and is not invoked by installation or repair.

## Persistent hidden-worker simulation state

The existing x86 extension now owns one simulation context per round and
native-authority generation. Control, perception, radio, driving, physical
motion, navigation state, gun/reload/ammunition and burst clocks retain typed
values in that context. BigWorld queries, native effects, damage publication
and projectile admission remain synchronous engine-thread frontiers. The
hidden worker remains the sole Bot simulation owner; this native migration adds
no process or wire format.
Actor order, accepted-shot records and one-shot effects cross explicit commit
boundaries. Partial construction, authority transfer and repeated close retire
the old context and detach its Python views before reuse. A motion query failure
is local to its actor and does not replay an already committed destruction.

The bridge uses exact numeric double payloads for the 34-bit state-presence
mask and microsecond timestamps, with integer/range validation before typed
storage. Win32 CPython 2.7 PyInt is signed 32-bit; using it here would reject
spawn-state masks and clocks past 2147 seconds. Python restores integer values
at the receiving boundary. Actor identities and handles retain their strict
integer guards. Regression checks require float payloads explicitly so an
LP64 development interpreter cannot hide this Win32 failure.

The driver's stopping-distance input also preserves positive infinity from the
coast integrator when a grade prevents a finite stop. The original braking law
consumes that value as an unbounded distance; it is not replaced with a made-up
finite limit. NaN, negative infinity and nonfinite poses, peer geometry or other
driver scalars remain rejected before persistent state or engine queries change.

The five native simulation checkers exercise the real CPython 2.7 bridge,
ordered analytic engine frontiers, persistent state, effect receipts and
lifecycle rejection. An integrated 29-Bot caller comparison also preserves
state, outgoing messages, ordered engine queries and projectile terminal
results, including authority loss/recovery and duplicate manifests. This
fixture uses synthetic descriptors and engine responses; it does not prove
human-fire parity, retail physics, native memory safety or Windows frame pacing.
The current physical migration covers the enabled rigid-support path; the
existing disabled detailed-suspension path remains outside this acceptance.

Initial paired host trials regressed after moving the state; removing redundant
full-state reads and batching radio summaries recovered that overhead. Later
alternating trials show only a modest total improvement. No Windows frame-time
or sub-50-ms result is claimed. The near-target driver also stops powering a
turn whose measured speed and descriptor turn limit cannot intersect the
existing arrival circle, then resumes when the forward path can reach it;
closed-loop tests retain the original arrival radius and vehicle parameters.

### Native query ownership

The motion bridge retains ordered ray iteration, candidate filtering, departing
contact checks and bounded soft-static recasts inside one synchronous C++ call.
Its capabilities still invoke the existing #1513 Math operations and BigWorld
query on the engine thread. Raw hit objects remain strongly owned until their
original effect boundary; no Python object enters the background pool. Owner,
round, space and thread guards fence engine reentry. Live destruction evidence
is read when each query runs, so an earlier committed break affects later rays.

The visibility service also owns pair jobs and prepared rays through reduction.
Actor snapshots distinguish current observer poses from each target's ordered
motion phase. Identity replacement, target death, fire, detection parameters,
foliage changes and the existing age limits retain their invalidation behavior.
A dead human with Last Effort can remain an observer without becoming a target.
The synchronous engine stage preserves the six-point ray order, Math-based end
tolerance and destruction reports. Queue counters belong to the native jobs;
the former Python callback duration is no longer a measure of this stage.

The destructible sensor retains streamed native identities and effect commits,
while a persistent numeric index owns body, tree and catalog candidate geometry.
Registry removal, isolation, falling poses and proved chunk-layout changes
update that index before reuse. Cold Bot control projections are reused only
while their descriptor, spotting profile, crew inputs and critical payload are
unchanged. These changes preserve the existing cadence and collision checks;
a subsequent Lakeville capture still spent most control time in Python/engine
callbacks and outside native scopes. Moving the loops alone did not produce a
material whole-update improvement or meet the 50 ms target. The exact-client
capture, rather than native code coverage, remains the performance criterion.

### Native boundary diagnostics

Normal startup retains frame-level PERF statistics and does not attach the
fine combat observer. Frame intervals, whole-callback and Bot-update durations,
and main-thread CPU time remain available. Detailed Python scopes have
measurable observer cost: selected Lakeville slices were slower than unselected
slices, and a same-workload host profile attributed a substantial increase to
the observer's start/stop and aggregation helpers. Neither comparison gives an
exact Windows overhead correction. Fine captures must be explicitly attached
for a diagnostic experiment, and their timings are not an uninstrumented
performance baseline.

When attached, selected combat-control callbacks carry a native timing ledger.
It starts after the existing rotating detail sampler selects a control callback
and ends after navigation-frame cleanup. Native entry rows separate input
parsing, core body work and output packing; callback rows separate Python/engine
work from nested native reentry. Only entry parse/body-self/pack and callback
self are disjoint within this ledger. Inclusive columns explain nesting and
must not be added to those costs or to the surrounding Python stage tree.
Native body time still includes C++ allocation and is not automatically
parallelizable computation. Internal `stage.` rows distinguish pure substeps
from actual Python-to-native entries.

Python stages separately measure motion/weapon mirrors, control configuration,
target projection and navigation-receipt copying. Fixed counters record cache
expiry lateness, output rows and configuration reuse. Frozen-roster sweeps and
contact pair construction/solving have independent native stages. Contact
component counts derive from the already-built pair set only during capture;
they describe that solver stage, not independence of an entire vehicle tick.
Inactive native scopes do not read a clock or construct a component graph.

The bounded native observer owns no gameplay state or Python/engine references.
Ending a sample during callback reentry cancels the incomplete ledger rather
than retaining stack pointers. Periodic `combat_checkpoint` records preserve
completed samples when the worker exits before the capture deadline. These are
cumulative: use the latest checkpoint or final summary for a capture, never
sum both. A diagnostic failure does not replay or reject simulation work.
