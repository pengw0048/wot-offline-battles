"""BigWorld triangle exclusion flags for vehicle movement queries.

wg_collideSegment's fourth argument is a SKIP mask, not a selection of
terrain/static object types. Bit 0x80 is PROJECTILENOCOLLIDE: omitting those
faces from suspension queries removes vehicle-only bridge decks and ramps.
Keep them for motion; exclude non-collidable decoration and water instead.
"""

VEHICLE_SKIP_FLAGS = 0x10 | 0x40  # TRIANGLE_NOCOLLIDE | TRIANGLE_WATER
# Preserve the existing static spotting query. This is not a claim that
# projectile penetration and spotting use identical obstacle rules.
SIGHT_SKIP_FLAGS = 0x80  # TRIANGLE_PROJECTILENOCOLLIDE
