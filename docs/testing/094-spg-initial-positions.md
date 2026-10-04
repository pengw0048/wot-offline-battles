# Community SPG position library and initial deployment

## Baseline and delivery status

This source patch targets the runtime of commit
`5fda392c88c0987ed110a097e3c668d0be011fa2` (PR #36, v0.9.4,
Chinese HD 0.9.22.0.1 #1513). It is not a new release or Windows binary.
The local review checkout was reconstructed from the shipped runtime and
matching source/test evidence; its local Git commit is not an upstream commit.
Apply-time checks use individual source hashes rather than that local commit.
No upstream branch, PR, tag, account or saved game was changed in this turn.

## Source policy and first-batch coverage

The catalog contains **2 maps, 10 source areas, 15 explicit minimap cells**.
All entries are community recommendations, not official firing coordinates.

Hawg's forum thread names Maps Tactics and Guru maps among its sources and
records a 9.22.0.1 update on 15 February 2018:
https://koreanrandom.com/forum/topic/43190-hawgs-spg-td-passive-scout-tactical-minimaps/

The exact historical archive was not retrieved. Consequently no position is
claimed to be extracted from its unseen images or archive. The first batch
uses explicit text from archived copies of the older authored Guru guides:

- Ruinberg: https://game.lhg100.com/Article/onlinegame/WorldofTanks/201512/22093.html
- Steppes: https://game.lhg100.com/Article/onlinegame/WorldofTanks/201512/22097.html

The mirror path indicates a December 2015 archive, not a verified original
publication day. The catalog preserves these URLs, provenance and caveats.
No third-party minimap image, mod archive or original game file is distributed.

| Map | Actual spawn side | Source cells included |
| --- | --- | --- |
| 08_ruinberg | North / team 1 | A1; A3/A4; A5/A6/A7; conditional A0/B0 |
| 08_ruinberg | South / team 2 | K1; K5/K6 |
| 35_steppes | South / team 1 | K5; K7; K0 |
| 35_steppes | North / team 2 | D1, explicitly an alternative position |

Ruinberg's A0/B0 option requires friendly field support in the source. The
implementation conservatively requires at least three friendly non-SPG planned
routes toward columns 7/8/9/0; this is a local interpretation, not an official
count or a guarantee of future protection. Steppes' northern railway positions
are described but not assigned guessed grid cells. D1 does not claim to be the
best northern position for every gun or situation.

The other 39 shipped map graphs have the typed status `source_not_catalogued`.
Their previous deployment behavior remains a **legacy fallback**, explicitly
logged as such, not represented as a forum-derived position. Likewise an
unusable area, unsupported battle mode or missing graph cannot abort a round.

## Data and responsibilities

Editable source: `spg_positions/positions_0922.json`.
Bundled generated module: `gui/mods/offline_lan_0922/spg_position_data.py`.
Pure selector and validator: `gui/mods/offline_lan_0922/spg_positions.py`.

The index includes internal map name, #1513 resource version, regular battle
mode and actual geographical spawn side. A map's team 1 is not assumed to be
north. Rows use `ABCDEFGHJK`; columns use `1234567890`. Bounds and spawn bases
are checked against the loaded historical map graph.

The community grid cell is an area, not a parking coordinate. Before publishing
the initial manifest, the worker reads existing baked cell centers and terrain
heights inside that area. It rejects hazardous water/edge cells, incompatible
graphs and insufficient full-turn/parking clearance. Candidate connectivity
uses existing baked directed edges, starting in the actor's own grid square;
there is no nearest-free snap across a wall. The existing navigator and actual
movement collision remain the owners of vehicle movement and corridor checks.
A baked grid is not an exact native mesh/vehicle swept-volume proof.

For each side, at most three SPGs receive distinct reserved destinations. The
reservation uses each vehicle's supplied collision footprint. Initial scoring
prefers different support areas, then source caveats/local priority, then dry
graph path length. No hidden enemy positions or global target list is used.
The initial facing direction is a public map sector, never a target or a fire
permission. Selection runs once, not once per target change or every frame.

Only three existing production files change:

1. `bot_runtime.py`: create initial plans, carry them in the manifest, execute
   the selected destination through the existing navigator, and preserve it
   across target changes and authority restoration.
2. `server/lan_battle_server.py`: validate and retain optional `spg_initial`
   metadata. Invalid optional data is omitted, not a fatal roster rejection.
3. `server/server_bot_ai.py`: prefer that canonical plan over the old ordinary
   route-derived anchor and use its parking radius.

Two new production modules carry the data and pure selection logic. The
existing module packagers discover them; this patch does not modify binary
formats, compact state scalar columns, launchers or account transactions.

## Arrival and single-plan ownership

Library-managed deployment uses a **2 m final arrival radius**, not the old
15 m rear-route staging radius. Candidate clearance includes this parking
radius and the vehicle's full turn radius. This is a local parking tolerance,
not a recovered retail server constant. The original LocalDriver's terminal
behavior remains unchanged.

The worker's navigation key refers to the round plan identity, not the current
enemy or hold/deploy transition. The adapter's reduced state projection does
not carry private round metadata; the navigation callback therefore retrieves
the plan from the authoritative Bot state. Integration tests cover this real
boundary and a vehicle still moving when 14 m short of the goal.

The server and worker consume the same plan. No vehicle is spawned or
teleported into the selected cell. Base-defense and explicit team orders retain
priority. An authority handover restores the canonical plan without rerolling
positions or moving the restored vehicle.

## Firing and relocation limits

This phase implements the requested **library and initial deployment**, not a
complete firing-position optimization/relocation planner. Candidates have
baked terrain/clearance/connectivity checks; they do **not** yet have precomputed
per-gun native ballistic coverage. Every actual shot still needs the preceding
SPG target, gun alignment, ammunition, exact arc and friendly-fire gates.

For a library-managed plan, the former local 16-80 m ordinary-route relocation
heuristic cannot replace it with an unrelated rear waypoint. Confirmed firing
obstruction is exposed as `library_position_fire_obstructed`. This first phase
does not automatically choose another catalog position after such obstruction.
On maps without a library plan, the previous close-wall relocation remains.
This boundary is deliberate and must not be reported as unchanged relocation
behavior on both catalogued maps or as a guarantee of a clear firing lane.

`SPG INITIAL` logs include map, Bot ID, outcome, side, zone, cell, chosen point,
source and `fire=runtime_required`. Existing `SPG FIRE GATE` logging remains.

## Extending the catalog

Add a source-backed map entry with its actual #1513 bounds, regular-mode
spawn-side mapping and explicit recommended cells. Preserve source caveats;
do not extrapolate an entire row or guess a historical image's coordinates.
Then run from the source checkout, using Python 3:

```sh
python tools/spg_position_catalog.py --generate --check
python tools/spg_position_catalog.py --check --audit build-evidence/spg-catalog-audit.json
PYTHONPATH=tests:server:tools python -m unittest test_port_0922_spg_initial_positions -v
```

The generated module is checked against the editable JSON. The catalog tool's
all-map audit uses a clearly labelled generic 1.8 m half-width / 4 m half-length
SPG footprint. It is not an actual FV3805 descriptor or a native gameplay test.
Rebuild the whole matching server/worker/client package after applying sources;
do not place Python 3-compiled bytecode into a Python 2.7 game client.

## Verification performed

- 31 new catalog/geometry/planner/manifest/authority/navigation/driver tests:
  all pass on the final source.
- 14 retained SPG-targeting tests plus 5 retained ram-contact tests: pass.
- 97 existing server tactical/ram tests: pass.
- Existing 1,308-case focused mechanics runner: passes with one existing
  conditional skip and its previously documented baseline exclusions.
- Both actual shipped #1513 map graphs produce six distinct legal-area plans
  in the generic fixture (three per side); all 41 graphs are coverage-audited.
- Final Python 3 syntax check passes for all five production files.
- All existing runtime sources outside the three listed files, the original
  driver/traffic/adapter and all 41 navigation graphs remain byte-identical to
  the reviewed local baseline. The baseline runtime is independently compared
  with the previous shipped Windows package in the delivery evidence.

This is not a full-current-suite pass, an actual Python 2.7 bytecode build,
a Windows executable build, or native Ruinberg/Steppes gameplay acceptance.
No complete 41-map Hawg catalog or per-gun firing coverage is claimed.
