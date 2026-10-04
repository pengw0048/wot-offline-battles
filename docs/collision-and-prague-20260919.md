# Paris support seams, Malinovka turns and Prague factory gates

The 14:46:29 report ran build `colorfulmeans-35415706931-1` (`18110f50`).
It contains 28 visible-client hard contacts from Malinovka and Paris, including
in-place turns; it contains no Prague round. The compact fixture preserves the
reported poses, rays, normals and native callback candidates. Callback order
does not identify the nearest surface, and the regression worlds below are
analytical stand-ins, not native gameplay captures.

## Changes

- An anonymous original face shared by several catalog envelopes is filtered
  only when every possible owner has accepted destruction for that material.
  The filter stops at the first owner exit and resolves again. An intact
  neighbour, damaged material or ordinary backing wall still blocks.
- Native rotation checks distinguish an existing contact from new penetration.
  A face already inside the starting hull may be crossed
  only when the final pose reduces penetration and the complete analytical
  corner sweep never increases it. Every following face is recast individually;
  the rule neither disables damaged BSPs nor adds a delay.
  A first face outside the starting hull needs a matching native contact from
  a separate exact-start-footprint query before this exception can apply.
  Without that proof it remains solid: the same object may extend into a later
  part of the rotating body even when its first point is outside the sweep.
- Paris contacts 17–21 straddle a raised pavement. The lower longitudinal ray
  hits its vertical side while the other track already has support on top.
  A bounded seam rule requires native drivable support on both sides, a broad
  top no higher than 0.75 m above the lower surface, that top within the existing
  posed track plane, and clear lower/upper corridors raised only by the measured
  step. High walls, a backing wall, narrow rails, unsupported contacts and
  airborne vehicles retain their ordinary collision checks.
- Prague's `valley` lane now approaches and leaves the two east workshop doors
  on their centre lines. The door positions come from the shipped catalog's
  `Workshop_new_doors` material-74 boxes, native placements `32638/48`
  (centre 76.79, -97.70) and `32640/89` (75.77, 102.39). The old north approach
  ran through x=54, beside the door. Authored control points and both deployed
  team routes are updated; the deployed routes remain strict reverses with
  16 points, and every leg reaches its anchor through existing baked links.
- A real Bot hard contact activates shared native checking within 24 m of that
  corridor. A* and shortcut admission use the same measured edge answers,
  retire paths through observed walls, and find openings with vehicle width
  clearance. New A* native queries consume the existing resumable search budget.
  Delivered scenery destruction invalidates those answers. This does not turn
  a traffic jam into a wall: only the native map query can reject an edge.

## Validation

1,447 related local tests passed, including 13 new regression tests. The old
`18110f50` source fails the same five Paris support poses, the overlapping broken
Malinovka face, the actual Prague doorway clearance check and the native
Malinovka rotation integration case. The updated source passes these cases and
the companion wall, intact-neighbour, opposite-turn, second-wall and traffic
checks. Both team paths in the gateway simulation cross the measured opening,
and repeated searches reuse native answers.

The Prague placement check uses real catalog transforms and separately plans
every deployed leg through the shipped graph. It verifies passage width at both
door planes in each team direction. The generic gateway simulation also checks
path smoothing, shared answers and reopening after destruction.

Native #1513 driving remains the acceptance boundary. In particular, the Paris
upper contacts near (23, 168–180) have not been established to be removable
scenery; ordinary native walls there are not blanket-filtered. The supplied
report does not establish which Prague doorway the user observed. The factory
route correction is grounded in the existing central lane and actual catalog
door placements; a native Prague replay is still needed to confirm that case.
