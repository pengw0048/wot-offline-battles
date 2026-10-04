# v0.9.4 KV-5 / FV201 ramming-contact follow-up

Baseline: `10e08f800d093d0df1eee16344737defcde38fcd` (PR #35).
Report: `wot-error-report-20260924-130605-e917ac8ec72b.zip`.
No private report, account data or game resources are committed.

## Report evidence

The visible client is a KV-5 with a canonical 100,175 kg collision mass. At
13:05:21 it contacts Bot 18 (FV201 (A45), 55,883 kg) at roughly 11.64 m/s
before the collision response. The reciprocal momentum path is active, but the
native ramming proof is discarded:

`RAM native contact unsupported bot_id=18 player_plate=None bot_plate=76.2`

A second proof again has no common structural plate. The server therefore
receives no ramming HP transaction for this high-speed impact. Later contacts
against the same FV201 do obtain common structural plates (for example
180/50.8 mm and 205/50.8 mm) and ramming damage is committed. This matches the
reported position sensitivity and is distinct from the accepted T-44-122,
AT 7 and T71 examples.

## Structural damage point

The synthetic OBB path previously chose exactly one Y coordinate: the midpoint
of the broad chassis vertical overlap. That coordinate is not a native damage
application point and can pass through track-only/empty material on one model.
Failure of either native hit tester made the whole ram fail closed.

The repair keeps the observed X/Z and contact normal frozen, derives the real
shared vertical contact interval (including pitch/roll), and tries the observed
Y followed by nearby interior Y candidates. A receipt is admitted only when
both exact #1513 native hit testers expose structural armour at the SAME
candidate height. Plates from different heights are never mixed and
`primaryArmor` is never substituted. The selected common native height is
published as the contact point.

This follows the historical 9.4 rule that the server determines a damage
application point inside the total contact area and uses nominal armour there.
It is a bounded reconstruction for synthetic player/Bot contacts, not a claim
to have recovered Wargaming's private centre-of-mass point solver.

Official reference:
https://worldoftanks.eu/en/news/general-news/update94-changes-ramming/
The 9.4 notes also explicitly fixed KV-5 side-brush ramming being applied to
underbelly armour:
https://worldoftanks.eu/en/content/docs/release_notes/release-notes-94/

## Physical response

The normal collision solver already applies reciprocal inverse-mass response.
The traverse solver was then run against PRE-contact velocities, making the
same closing velocity available to a second constraint. A small steering
correction could therefore add an extra shove after the normal impact,
especially when the peer had already received its reciprocal ledger impulse.

Both visible-client and hidden-worker paths now feed the normal solver's
resulting velocities into the traverse constraint. Geometry correction remains
owned by the normal solver/world gate; only solved velocity is carried forward.
Mass, descriptor-derived track grip and engine/traverse torque remain the
inputs. No blanket force multiplier is added.

## Deliberately unchanged

`RAM_DAMAGE_COEFFICIENT = 0.25` is unchanged in this follow-up because the
user explicitly reports the previously listed T-44-122 / AT 7 / T71 rams as
correct and the current report proves a contact-point admission failure before
that damage curve is reached. The existing accepted ~5.72 m/s FV201/KV-5 case
remains a regression fixture (64 damage to the KV-5 side of the transaction,
191 to the FV201 side in the current ordering).

No SPG, matchmaking, radio, map, driving/traffic, armour penetration, downhill,
exchange or crew logic is changed. No save reset, main merge, tag or formal
release is performed.

## Acceptance boundary

The dedicated build runs the new structural-point and velocity-order tests plus
the retained collision/battle/physics suites, compiles the actual Python 2.7
payload, verifies installation and starts the packaged Windows executable in
server mode. This is not native map gameplay acceptance. Re-test repeated
high-speed KV-5 -> FV201 impacts at different contact heights and a light Bot
steering beside a heavy tank; new logs retain unsupported-contact diagnostics
if no common native structural point actually exists.
