# v0.9.4 matchmaking / artillery / contact / radio test repair

Baseline: `599612c1eaa91fc4e9935cccab929f3bbbd5457e`, PR #33.
Report: `wot-error-report-20260924-102359-8d9bd17a08e2.zip`.
Client: Chinese HD 0.9.22.0.1 #1513. No account files are modified.

## Matchmaking

The waiting-room/wire already exposes seven presets, but the restored planner
accepts only five. The server normalizes both +/-2 choices back to random.
The automatic final fill also defaults to zero SPGs. Restore the seven-value
contract, choose one contiguous legal window containing LAN humans, and use
15-player 3/5/7, 5/10, or one-tier slots. Smaller team scaling is synthetic.
Reserve a feasible 0..3 SPG count against real available tiers, counting human
artillery and mirroring the class/tier slots. AT-SPG is a TD, not artillery.
Explicit custom vehicle overrides remain explicit; an impossible/wide human
room is not silently described as an official random battle.

Official 9.18 source: https://worldoftanks.eu/en/news/general-news/918-release-announcement/
Official role extension: https://worldoftanks.eu/en/news/general-news/920-1-matchmaker-improvements/
The latter adds VIII-X combat subroles, not recovered here by invented armour
thresholds. This local generator is NOT the complete private retail queue,
platoon, preferential-vehicle, waiting-time or streak algorithm. Its random
window and 0..3 sampling frequencies are not official population statistics.

## Artillery

A bounded final-trajectory proof can finish after the target has moved. Existing
reproof led using the previous latency, but failed to scale that latency when
the newly led trajectory changed its number of chords. Eight concurrent SPGs
at low frame rates repeatedly missed the unchanged 1.5 m freshness tolerance.
Use the already returned chord count and step to solve a bounded (eight pure
iterations) workload/lead estimate. No extra native rays or weaker collision,
alignment, friendly-fire, dispersion or 20-second projectile gates. A pending
proof keeps the existing controlled hull hold and cannot consume ammunition or
a fire sequence; cancellation and timeout still release it.

The report has 29 Bots; motion diagnostics identify 28 distinct Bot slots, all
non-SPG. It is not a complete native artillery failure trace. The attack cause
above is independently reproduced with an injected ballistic/native-probe test,
not claimed as an in-game reproduction of every reported SPG movement issue.

## Contact mechanics

At 10:15:57.803 the visible client logs a 35,110 kg player, 37,000 kg peer,
and reciprocal physical response. The worker logs no CONTACT worker receipts.
The cumulative tank_pushes producer and server relay survived rollback, but
Bot consumption/acknowledgement and compact-row carry-over did not.

Restore only that mechanical handshake, canonical-mass momentum division,
shared pair response, native-parameter track drag/traverse budget, and actual
swept hull-yaw clipping. Keep motor torque distinct from blocked rendered yaw.
The compact state adds an optional tail and presence bit: old scalar columns
remain byte-for-byte unchanged. Replayed/coalesced impulses are idempotent and
an acknowledgement is carried together with its resulting velocity.
No duplicate armour/HP receipt impulses. No ram-damage constants changed.
The original077 traffic coordinator and driving/recovery strategy remain intact.
This is not a promise of zero clipping under every native mesh, wall sandwich,
network delay or high-speed tunnelling scenario.

## Radio and vision

The rollback retained radio_recipients in the presentation relay but discarded
it from the server planner's own contact records. A disconnected Bot could keep
a target or be redirected by another recipient's intelligence. Retain per-Bot
absolute leases; filter target selection, movement leases, support routes,
commanded focus and contact-based defense decisions. Revoke old route leases
when their recipient loses the contact. Preserve existing public base alarms
and legacy fixtures without recipient metadata. Current worker messages always
include the explicit recipient list, including an empty list.

Unchanged audited boundaries: 50 m proximity, 445 m direct spot cap, surplus
view range reduces camouflage, native 565 m AOI, native six visibility points,
summed two-radio direct link without arbitrary A-B-C forwarding.
Official overview (current, not proof of all 0.9.22 server internals):
https://worldoftanks.com/en/content/guide/newcomers-guide/how_to_survive/
Foliage strength and spot-memory timing still contain documented port
approximations. Passing radius/relay tests does not certify every bush or
hidden-target timing against the retail server.

## Verification limits

The focused suite excludes two independently reproduced baseline mismatches:
- launcher exclusion test expects a removed retired_vehicles alias;
- current deferred-probe test expects the later non-077 motion policy.
No test assertion is weakened, and neither later policy is restored to get a
false full-suite pass. Contact_dynamics AI escape cases likewise belong to the
intentionally rolled-back strategy, not to this mechanical repair.

The dedicated build checks the exact patched source, protected remaining
runtime/assets, full Python 2.7 bytecode, final Windows payload, versions,
simulated installation and actual executable server readiness. None is native
Windows battle/Flash acceptance. Test in one-tier and +/-2 rooms, 0..3 SPGs,
stationary/moving radio-spotted targets, both mass directions at side contact,
and direct radio range/disconnect boundaries. Keep the previous package and
back up saves. No main merge, tag, formal release or game-client redistribution.
