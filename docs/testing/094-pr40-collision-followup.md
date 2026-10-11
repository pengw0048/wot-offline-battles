# PR40 collision follow-up, 2026-09-25

Base: draft PR42, targeting PR40 (`a5401f8`). The user's Windows report
`wot-error-report-20260925-184714-1261d4245e1b.zip` was inspected locally;
the report and installed game files are not added to Git.

## Observed missing HP

At 18:46:58 local time the visible client reports a 25,062 kg MT-25 contacting
a 14,700 kg AMX 13 57. The contact solver transfers momentum, but the native
damage probe says `player_plate={'armor': 40.0, ...} bot_plate=None` and does not
emit an HP receipt. At 18:47:02 another contact with the same pair resolves
both native armour plates and the server commits 98 and 47 HP of ram damage.
The first missing HP cannot be explained by a mass or speed threshold: the
visible client's contact speed before response is greater than on the later
damaging contact.

The previous plate probe sampled several vertical heights but only one X/Z
point, the centre of the chassis overlap. That line can meet a track gap on
one vehicle while both mounted chassis bodies still overlap. The new search
also samples the centre and two interior positions across the *shared* OBB
contact width. The two native hit testers must return structural armour at the
same X/Y/Z point; the selected point and actual plate thickness travel in the
existing receipt. Worker Bot/Bot and human/human probes use the same geometry.
Malformed external layers no longer mask valid native structural layers behind
them. The existing mass, normal closing speed, armour absorption, crew and
liner factors, damage scale, and reciprocal impulse remain unchanged.

Wargaming's historical 9.4 explanation says the server selects a damage point
inside the shared contact area, and that mass, speed, nominal armour at that
point, spall liner and Controlled Impact affect HP loss:
https://worldoftanks.eu/en/news/general-news/update94-changes-ramming/
This approximation does not claim to reproduce the unpublished retail point
selection exactly. A true armour-only/track-only contact can still do zero HP
damage, and slow or well-armoured contacts may legitimately absorb the hit.

## Movement and wrecks

The PR40 / 0.8.1 normal solver already splits contact response by inverse
mass and uses engine-powered approach speed and track resistance. The worker
already applies human momentum through cumulative, acknowledged checkpoints.
Regression tests now cover a second human impulse against the same settled
Bot wreck, duplicate checkpoint suppression, heavy/medium/light pushes, and
no HP damage from a wreck. An actual gap in wreck motion was its centre-only
ground check: after the first shove, a narrow unsupported ground column would
undo the next movement and clear its momentum. Wrecks now use the live chassis'
existing two-end bridge check for that case. Both ends must have support;
cliffs, upward steps, static world geometry and turret contacts retain their
existing blocking checks.

The user-requested LT-5 addition is absent: the previous PR42 changed only
HT-5's missing view-range feed and added no LT-5 production code. The existing
LT-5 conditions remain as they were in PR40.
