# Collision regression follow-up, 2026-09-19

The 09:53:51 report was produced by build `colorfulmeans-35413203991-1`
(commit `27cf9f24`), so the continuing Malinovka obstruction is not evidence
of an old client package. The report contains eight Malinovka hard contacts
and six Paris contacts. Native callback identities are candidates, not proof
that a particular callback owns the returned nearest point.

## Findings against the 0.8.4 baseline

The shipped destructible placements and the vehicle skip mask are unchanged.
Restoring all old collision code would discard later mounted-hull and actual
replacement-wall protection. Instead this change removes movement holds and
repairs the native-query boundaries:

- Malinovka's military fence has two module damage boxes. The reported original
  compiled surfaces can lie outside one module box or inside both. The previous
  unique-module resolver therefore misses seven contacts and can recast to an
  exit behind the eighth contact. The full oriented model envelope contains
  these points without increasing the existing 7.5 cm native tolerance.
- Explicit structure hand-off holds, hide deadlines and a first-frame cap-crush
  hold can freeze translation or rotation after a contact is accepted. Native
  geometry now decides movement in that same frame. A speed used to qualify a
  crush is never assigned to the vehicle's actual speed.
- The old soft-static ray helper jumped over the entire original box. A real
  wall or damaged replacement inside that box was never queried. The helper
  now queries the complete segment with an exact original-material filter,
  including when the ray ends inside the box. It adds no box-exit jump.

## Collision boundary

Anonymous compiled original materials 71–86 may be filtered only inside one
registered model envelope and only for an accepted or locally predicted exact
material (an item-wide receipt for fragile props). Intact sibling materials,
damaged materials 87–100 and ordinary walls remain native geometry. Reused
anonymous keys beyond the envelope are queried normally. A later live owner
inside the envelope also ends the filtered query before its boundary.

The player and Bot adapters recast after contact acceptance, including Bot
translation that previously queried only before committing destruction. Rotation
keeps its native replacement check. There is no collision hide deadline or
mandatory first-frame hold. Compatibility readers for the former hide state
always return false; publication retries still retain their own delivery state.

## Regressions and limits

The eight new report rays produce 11 failing original-material cases on the
previous implementation. They now cover immediate release, intact material
restoration, and real walls inside the same bounds. Existing fixtures exercise
five earlier report rays and 750 placed collider variants across 40 maps.
Additional tests cover interior/end-point walls, overlapping live placements,
shared anonymous keys, and same-frame player/Bot movement at physical speed.

These tests simulate the native callbacks using captured rays and shipped map
transforms. They do not replace in-game driving checks. The Paris report has
ordinary material-111 wall contacts too; this change deliberately retains those
surfaces rather than assuming every visible contact is a breakable railing.
