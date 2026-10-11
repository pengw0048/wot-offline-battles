# Spotting and radio follow-up, 2026-09-16

## Evidence and limits

The attached `wot-error-report-20260916-063806-ce9e820f1941.zip`
contains a `114_czech` match with a low-tier player. It does not contain the
reported Waffentrager E 100 encounter. This patch fixes a demonstrable geometry
error; it does not claim to replay or identify the exact cause of that encounter.

The locally inspected 0.9.22 reference `common/items/vehicles.py`, in
`VehicleDescr`'s geometry preparation, supplies six `visibilityCheckPoints`,
`observerPosOnChassis`, and `observerPosOnTurret`. The old implementation used
one fixed-height 2.0 m -> 1.5 m segment, so exposing a tall turret or a hull edge
while hiding that particular endpoint could never spot the vehicle. The runtime
now consumes each *installed client's* descriptor geometry, including native
hull mounting, turret rotation, and body yaw/pitch/roll. The reference is generic
0.9.22 and is not represented as an independently verified CN #1513 server.

Relevant official-domain material searched on 2026-09-16:

- [Wargaming Wiki: View Range & Camouflage](https://wiki.wargaming.net/cs/View_Range_%26_Camouflage_%28WoT%29): the search-index extract explicitly describes adding both vehicles' radio ranges.
- [Wargaming Wiki: Battle Mechanics](https://wiki.wargaming.net/en/Battle_Mechanics): the search-index extract explicitly excludes retransmission of information received from other friendly vehicles.
- [Wargaming: How it Works — Vehicle Spotting (2016)](https://worldoftanks.eu/en/media/4/video-guide-sotting-system/): historical official video landing page; the video itself was not transcribed in this task.

The Wiki body requests returned cookie/JavaScript interstitials. The two radio
rules above were verified from the indexed extracts, not by reading a historical
2018 page snapshot. No server-side legacy implementation was available.

Local 0.9.22 `common/items/utils.py:getRadioDistance` supplies the mounted radio
range multiplied by the crew/loadout factor. The reference
`item_defs/tankmen/tankmen.xml:radioman_retransmitter` supplies 0.001 range bonus
per skill level. The implementation uses the admitted human factors and live
crew state, the Bot's native default crew factors, the existing radio module
state factors, and the strongest eligible Relaying bonus (without recursive
bonus propagation).

## Behavior

- Six native target checkpoints replace the single invented target height. One
  of the two native observing ports is used per two-second phase, synchronized
  through the existing round-relative server clock.
- A clear checkpoint without foliage returns immediately. A fully blocked pair
  uses at most six native rays, within the existing observer-pair work budget.
  Foliage is sampled on the same checkpoint ray, so an uncovered upper/side
  checkpoint is not hidden by a bush intersecting only the former center ray.
- The existing 50 m proximity rule and 445 m detection ceiling remain in force.
  Nearby foliage still loses its existing penalty when firing; distant foliage
  remains relevant. The 0.75-second firing concealment window and per-asset
  foliage strengths are retained approximations, not newly claimed retail
  calibrations. A gun firing does not guarantee detection through solid cover
  or sufficiently distant foliage.
- Radio uses direct allied links and the sum of current effective ranges. It
  never traverses a chain of allies to forward enemy intelligence.
- Every direct observer owns its own expiring target lease and remembered pose.
  A receiver can use only its own observations or a currently connected direct
  observer. Losing radio contact cannot retain another observer's team-wide
  lease, nor can another disconnected observer refresh its known pose.
- Recipient-specific deadlines reach the server planner and visible client.
  Bots outside the recipient set cannot acquire that target from a team order.
  Local direct spotting still works without a radio link.
- Allied minimap knowledge also uses the current radio links or the player's
  own direct vision. The team roster remains intact. Postmortem presentation
  follows the team's view.
- Missing radio data grants no shared contact. Legacy messages without the new
  recipient list preserve only an explicitly identified self-observation.

## Validation

`test_port_0922_spotting_radio.py` covers the tall exposed turret regression,
all-six blocked rays, rotating observer ports, proximity/ceiling, combined radio
range, no multihop contact, dropped/dead links, unavailable radios, receiver
pose isolation, Relaying bonus, recipient-specific client leases, and clearing
old allied minimap coverage. Existing spotting, foliage, battle runtime,
worker observation/lane scheduling, and SPG launch tests are also exercised.

This is an engine-free regression check. Windows native geometry, dense map
performance, and the original Waffentrager encounter still need a client test.
