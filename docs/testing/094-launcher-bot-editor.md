# v0.9.4 launcher Bot tactics editor

Baseline: `5fda392c88c0987ed110a097e3c668d0be011fa2` (PR #36).
This branch also integrates the previously supplied, unpublished initial-SPG
library patch. It does not contain a new native driving or collision policy.

## Launcher entry and ownership

Open Tools -> Bot tactics -> Edit behavior, routes and artillery positions.
The existing exact-lineup editor remains separate and unchanged. The new
Tk editor has behavior and map tabs. It uses Pillow 12.3.0 to read original
minimap DDS artwork from the selected #1513 game installation; no original
artwork is redistributed. A clearly labelled navigation raster is used when
that artwork cannot be read. A user may import a north-up, full-boundary image
as the current editing background; background images are not part of exports.

Profiles live below `%LOCALAPPDATA%/WoTOfflineBattles/bot_tactics`, outside the
application and game folders. Profile names are hashed into safe filenames.
Save writes a named draft. Apply also atomically replaces `active.json` with a
validated copy. The previous version is retained as `.bak`. Invalid input does
not replace the current active configuration. Import/default/new actions are
only drafts until Apply. Export is plain, versioned JSON, never executable code.

A launcher-owned server receives the active file path through
`WOT_0922_BOT_TACTICS_PATH`. Only the host reads it when accepting a new battle.
The round gets one immutable, map-scoped copy, transmitted through every real
battle-start/restart/current-message path. Mid-battle edits cannot change the
current round. A following round reads the next active version without another
EXE build. Joining another host does not override its configuration. Custom
configuration is refused for a separately running server rather than falsely
claiming that it was applied there.

## Behavior controls

Rules support all Bots, either team, one vehicle class, or one team/slot.
More specific rules override inherited fields; blank fields inherit. The
editor previews resolved values. Explicitly add/update a rule to keep it.

The first version exposes the actual connected gunnery controls: skill preset,
crew level (75/90/100), reaction delay, aiming patience, convergence threshold,
aim-point bias, and lead error. Skill and crew can be set separately. These
are fed into real Bot initialization and gunnery functions. No fake sliders
for unused tactical capabilities are offered, and no hidden change to damage,
penetration, native dispersion, spotting or reload formulas is introduced.

## Map authoring

All 41 pinned maps have exact arena bounds, real team base coordinates and the
SHA-256 of their original navigation resource. Team numbers are accompanied by
actual map-side coordinates; team 1 is not assumed always north. Only the
currently supported regular battle mode is authored. Window zoom/pan/resize
never changes stored world X/Z coordinates. Himmelsdorf's non-centred bounds
are covered explicitly.

Original routes are visible but read-only. Copy one to customize it, or create
an empty route and click to add points. Drag points, Shift-click to insert,
Delete to remove, and use the hold action to mark a hold waypoint. Undo/redo
stores document edits, not view changes. Route attributes include allowed
classes, optional 1-based UI slots, capacity, sampling weight, and preferred or
fixed policy. SPGs are deliberately excluded from ordinary attack-route class
assignments and use their position library instead.

Route points are macro intent, not a request to bypass terrain. Initial route
selection checks baked connectivity and uses a deterministic weighted draw.
Fixed routes suppress autonomous lane rebalancing; emergency/combat orders and
real obstacle avoidance still take precedence. The existing navigator and
driver execute the route. An unavailable custom route is logged and does not
invent a passage through a wall.

Create an SPG position by clicking a centre, then set an allowed parking radius,
initial heading (0 degrees north) and priority. The circle is a deployment
region, not shell splash or range. Actual vehicle-sized candidates are selected
inside that circle from the pinned baked grid, with ground/clearance/reachability
checks and team reservations. No spawn is teleported. The final selected plan
has a two-metre arrival radius and cannot be replaced by the old arbitrary rear
route-waypoint heuristic. Its identity is stable across target changes and
worker restoration.

Manual positions take priority over the integrated community source for that
team. An unusable manual set is explicitly logged as
`manual_no_reachable_parking_space`; no distant invented parking coordinate is
substituted. Uncovered maps/teams retain the old fallback. The community source
itself still covers only Ruinberg and Steppes (10 zones, 15 cells); editable map
coverage must not be confused with 41 validated community recommendation sets.

## Validation is not native acceptance

The editor's map check proves only baked connectivity and existence of generic
parking space. Vehicle-sized parking is checked by the actual worker at round
preparation. Full per-gun ballistic coverage is not certified by either check.
Every shot still goes through the existing aiming, ammunition, exact native
trajectory and friendly-obstruction gates. A library-position obstruction is
logged; automatic switching between library positions is not implemented in
this phase. The prior no-target/proof-loop fix remains intact.

The config contract rejects unknown versions/fields, stale map fingerprints,
out-of-bounds coordinates, NaN/infinity/Boolean numerics, invalid class/slot
selectors, duplicates, overlarge files and overlarge map-scoped wire documents.
Only current-map data is sent at battle start; complete 41-map profiles do not
enlarge every snapshot. Server logs record both profile and round hashes, plus
the bounded round document, for reproducibility.

Tests cover the real launcher button, actual Tk Canvas events, dirty/default
semantics, named save/import/export, invalid form protection, effective behavior,
route assignment, manual-plan validation, server roster admission, restored
worker plans, fixed-route policy, real host battle-start freezing, and the
next-round reload. The packaged EXE additionally runs
`--verify-bot-editor <receipt.json>` in an isolated temporary profile: it opens
Tk, draws route/position edits, applies them, and has the real plan builder read
them. A separate EXE server-readiness check is required. Neither is native WoT
map/gameplay acceptance.

No main merge, tag, account/save reset or official release is performed.
