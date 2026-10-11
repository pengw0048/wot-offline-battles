# 0.8.3 test follow-up — September 16, 2026

## Baseline and evidence

The owner supplied `wot-error-report-20260916-063806-ce9e820f1941.zip` and
eight additional gameplay reports. Both installed and bundled payloads in the
archive identify `colorfulmeans-35026220443-1`, source
`4a3c1b5bc97c49a94815ecd588c95f13a158a6cc`. These changes retain that build's
fresh-owner engine-audio lifecycle fix.

The archive records Chinese HD `0.9.22.0.1 #1513`, a low-tier battle on
`114_czech`, and a normal shutdown. It does not capture the reported WT E 100
encounter, Murovanka objects, or the crew-role-change attempt. The visible client
has a tutorial weak-reference error during shutdown; neither worker nor server
contains a Python traceback. There is no repeat of the earlier Svarog ownership
assertion in this archive. One session is not proof that all crash paths are gone.

The worker does record destructible identity misses/ambiguity and repeated Bot
stall diagnostics. Those observations guide the reproduction tests without
treating every stationary artillery vehicle as a navigation failure.

The owner subsequently confirmed that HE explosion effects are already correct.
The remaining HE defect is the non-SPG direct-hit voice. The existing explosion
selection is preserved, with additional tests for its fired-shell identity.
The re-uploaded `(1).zip` has the same SHA-256 as the first archive and does not
add a WT E 100 encounter.

## Owner acceptance cases

| Area | Required in-game behavior |
| --- | --- |
| HE feedback | On any vehicle class, a direct HE hit with HP damage produces the explosion effect and the ordinary successful-hit voice; direct zero-HP hits produce the non-penetration voice; indirect splash damage produces the splash voice. Check changing shell selection while a shot is in flight. |
| Crew role | Changing an occupied crew member's primary role succeeds, with installation or transfer to the barracks handled consistently. Insufficient currency/capacity leaves both money and crew unchanged. |
| Capacity purchases | A garage slot costs 300 gold; a barracks expansion costs 300 gold for 16 bunks. The displayed offer and actual debit agree. |
| Bot congestion | Moving Bots can recover around stationary allies and yielding pairs do not remain mutually stopped in an otherwise passable route. |
| Bot driving near vehicles | Normal navigation slows, waits or avoids another Bot or player; a blocked route must not keep applying throttle and continuously push that vehicle. This is driving policy, separate from the mass-based physical ability to push. |
| Side contact | A sufficiently powerful heavy vehicle can gradually displace a lighter vehicle at side contact. A light vehicle cannot overcome a much heavier vehicle's ground resistance. Displacement continues to respect world collision and support. |
| Spotting | Nearby low-concealment targets are detectable when at least one spotting checkpoint is visible; firing applies the firing concealment penalty. Terrain/buildings and actual bush cover still matter. |
| Shell/scenery | Trees and identified falling lamp/utility poles do not terminate a shell. HE/HEAT still stop on genuine shell-blocking obstacles. |
| Destruction | Verified destructible objects can be knocked down, including Murovanka. Ambiguous native identities must not be guessed or allowed to recreate the prior native ownership crash. |
| Radio | Enemy information is shared according to the observer/recipient radio connection, rather than granting the entire team unrestricted visibility. Personal spotting remains independent of allied radio sharing. |

## Validation boundary

Automated tests exercise the Python rules, message propagation, shop contracts,
and engine-facing adapters. They do not execute the exact Windows #1513 native
client. The final Windows launcher build and its installed build identity must
be used for the acceptance cases above. This remains an unmerged 0.8.3 test
build; it is not a new stable release.

## Changes and regression coverage

- HE voice selection uses the fired shell, not the vehicle class or the shell
  currently selected. The test matrix includes all five vehicle classes,
  damage/no-damage, direct/splash, module damage and changing ammunition while
  a projectile is in flight. Damage/penetration statistics remain canonical.
- Crew role changes use an atomic transaction. A matching empty primary seat
  receives the member, otherwise the member enters the barracks. Existing crew
  are never displaced. Client success extensions preserve install status 0;
  failures after charging also roll back currency, crew, revision and deltas.
  The shop publishes scalar gold prices, as its native consumer expects.
- Side-contact drive torque was weakened twice by chassis inertia, and its
  target angular speed restarted from zero every frame. The corrected bounded
  drive impulse respects traction, mass, available engine power, world walls
  and support. A 130-ton/1,200-hp test vehicle gradually displaces a 25-ton
  vehicle; the light-to-heavy case stays held by ground resistance. This is
  an improvement to the port's planar contact solver, not a replacement with
  the retail server's unavailable complete rigid-body simulation.
- Stationary firing Bots may perform a short, collision-checked yielding
  manoeuvre after sustained friendly blockage, then resume their tactical
  hold. A zero-gap contact moving apart is no longer treated as a new obstacle.
- A final vehicle guard runs after hull aiming on every physics slice, including
  reused decisions. It checks the actual hull sweep and descriptor-based coast
  distance, releases throttle toward a blocked player or Bot, and prevents
  steering torque into an occupied side. Same-direction followers and combat
  movement are included; expiry of a crossing lease cannot override it. Safe
  reverse/separating movement remains possible, and a stopped follower can ask
  a parked ally to clear the route before making physical contact. This changes
  Bot driving policy, not the player's mass/traction-based push capability.
  Vehicle brakes feed a short hull sweep back into local steering so a live
  vehicle cannot remain invisible to path selection. The regression checks a
  Bot passing a stationary player without overlap, both supported player
  coordinate formats, and the original crowded airport/fjord departures at
  15/24 FPS. Cooperative clearance retains its short, checked manoeuvre.
- Radio observations retain the original observing vehicle and its expiry.
  Each recipient must have a direct connection to that observer. The server
  validates recipient identities, teams, uniqueness and bounded lifetimes
  before committing an entire batch. Empty recipient/link snapshots revoke
  old coverage. Per-Bot target selection and temporary route reassignment do
  not consume another recipient's enemy knowledge.

Focused tests are supplemented by the complete client/launcher CI and the
Windows server/launcher artifact checks. Exact final counts and download
identity are recorded in the pull request after the build completes.


## Scenery identity and projectile follow-up

The old projectile adapter applied the AP-only 19 HP traversal limit to
SpeedTree ram health. Trees now preserve every shell family and charge no
penetration loss. Only the exact authored falling utility-pole/lamp families
receive the same treatment; mailboxes, fence end-posts and genuine cover retain
the obstacle law. Exact-identity native recasts preserve a wall even when it
overlaps the pole bounding box. These changes implement the user-specified 9.22
behaviour, not a later HE-through-cover mechanic.

The Murovanka reproduction uses the shipped `11_murovanka.json`, chunk 32124:
a retained empty native slot moves authored wooden fence slot 7 to native slot
8. A successful effect-category query returning -1 means the slot resolved but
has no registered effect handler. The prior remapping scan dropped such model
slots and subsequently excluded them when typed trees triggered a chunk repair.
Recovery now additionally requires one unique full authored transform, a matching
model descriptor, and agreement with any complete native filename list. Unknown
categories alone never establish identity, tree validation stays separate, and
the unsafe scalar filename wrapper remains unused. The regression retains all
48 real placements and leaves only the genuine empty slot excluded.

Targeted verification: 288 destructible tests, 8 existing follow-up tests and
2 new scenery tests passed. The modified runtime module also compiled with
CPython 2.7.18. The current attached report is 114_czech, not Murovanka; actual
Windows map destruction and effects still need the acceptance tests above.
