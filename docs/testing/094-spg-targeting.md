# SPG target / muzzle-proof feedback repair

Base: `c675b21611afc20f008b832740cd3f8559319cb9` (PR #34).
Client: Chinese HD 0.9.22.0.1 #1513.

## Report evidence and its limits

The new 11:53 report has matching visible-client, worker and server build
identity `colorfulmeans-v094-mechanics-35949861559-1`; this is not attributed to
an old installation. The rounds are 08_ruinberg at 11:45:36 and 35_steppes at
11:48:25. Motion samples include two SPGs in the first round and six in the
second. In the second round FV3805 (Bot 13) turns toward -1.620 radians at
11:50:05 with hull_aim=True, returns to the same -0.616-radian base direction
at 11:50:23, turns toward -1.629 at 11:50:41, and returns again by 11:51:18.
These are actual sampled motions, not a reconstructed per-shot target trace.

The first-round FV3805 (Bot 30) holds approximately (10, -221) in
artillery_hold/arrived; sampled collision=True does not independently identify
which wall or which trajectory blocked a shot. Movement path_clear=True is
not evidence of an unobstructed firing arc. The old report did not log all SPG
fire gates, so it cannot prove every native missed shot has one cause.

## Earlier-than-final-proof fault

The server required a positive muzzle-dependent artillery lane before selecting
an enemy. Turning the hull invalidates that advisory proof (0.001 radians),
then the generic two-second movement lease expires. The SPG faces its base
again, clears a new proof from that pose and repeats the cycle. The final
launch-latency repair in PR #34 cannot fix a cycle before final launch.

SPGs now track their own received, visible, in-range contacts independently of
that advisory. Other classes retain direct-lane target admission. Unproved SPGs
do not reserve a ready shooter's focus budget. The order fire flag permits a
local attempt, not a shell: the worker still requires the ballistic solution,
actual gun alignment, ammo/reload/critical checks, gunner reaction, selected
lane, full exact native arc and friendly clearance. An advisory expiry cannot
revoke a still-valid selected target's pending exact proof. Lost/dead or
radio-disconnected targets are not admitted.

The regression uses the real BotPlanner, BotAdapter, LocalDriver, BotRuntime
and both ArtilleryController queues. Only descriptors, native geometry and
clock are controlled; no fixed fire_allowed=True or fabricated receipt. The
unmodified server fails with zero shots in 40 seconds; the corrected chain
fires at least twice. A confirmed wall still produces zero launch receipts.
This is an integration test, not gameplay against a native map mesh.

## Close-wall positions and diagnostics

A completed failed proof records its actual sampled hit/chord/arc. Only when
all available arcs are confirmed blocked within 25 m, for at least three
seconds at a stable pose, may a stationary SPG choose another existing rear
route waypoint (16-80 m away, rear 30 percent). Up to three such changes are
allowed. Unknown/pending probes, expired evidence, distant walls and invalid
physics do not trigger relocation. The original navigator, movement collision
and driver perform the move; no teleport or world-collision bypass is added.
Once reached, the new position is retained rather than reverting to the bad
anchor. Base-defense orders take precedence. Missing safe candidates remain
explicitly blocked instead of inventing geometry.

New bounded `SPG FIRE GATE` records capture target, order/local readiness,
reload/ammunition, actual/desired angles, queue state, checked chord counts,
world hit coordinates, final proof/receipt and fire sequence. They use existing
probe results and never issue extra native rays. Serialization failure cannot
cost a shot. These records are needed to confirm remaining native cases.

## Scope and acceptance

Five production files change: server_bot_ai.py, artillery_arc_queue.py,
artillery_controller.py, battle_runtime.py, bot_runtime.py. Original driver,
traffic, adapter, all 41 maps, matchmaking, radio geometry, contact mechanics,
armor, downhill speed, exchange and crew fixes are retained. No save mutation,
main merge, tag or official release. Whole-package replacement is required for
the hidden worker and server as well as the visible client.

Validation: 14 new regressions plus the existing 1,308-case focused runner,
whose one conditional skip and two documented baseline exclusions remain.
Actual Python 2.7 compilation/bytecode comparison, packaged source/asset
identity, Windows launcher tests and server readiness are separate gates.
Native acceptance remains: SPGs engaging stationary/moving received targets on
Steppes, Ruinberg nearby walls and alternate-position behavior, no shooting
through solid cover or friendlies, and removal of targets after radio loss.
