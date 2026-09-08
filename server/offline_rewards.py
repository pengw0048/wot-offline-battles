"""Deterministic offline reward reconstruction for LAN battles.

This is not Wargaming's proprietary 0.9.22 server formula, which runs on the
cell app and was never shipped: ``scripts/common/items/vehicles.py`` of the
pinned client reads ``xpFactor``, ``creditsFactor`` and ``freeXpFactor`` only
under ``if not IS_CLIENT and not IS_BOT``, and no shipped vehicle definition
carries them.  What *is* published is the structure of both payments, and this
module follows it.

Credits, from Wargaming/Lesta support material:

* a base amount of ``X * vehicle tier``, and only that amount is multiplied by
  1.85 on a win;
* ``Y`` per point of enemy durability destroyed, independent of vehicle tier;
* ``Z`` for each enemy vehicle detected first, and ``2 * Z`` when it is an SPG;
* one capture payment for a base capture that actually completed, split
  equally between the vehicles that took part in it.

Experience, from Wargaming support material: damage and kills count with the
difference in vehicle tiers taken into account, spotting, base capture and
capture defence count, a win adds 50 percent, and Free XP is five percent of
the Combat XP.  A kill is therefore paid by the durability of the vehicle
destroyed rather than as a flat amount per frag; see
``KILL_XP_DURABILITY_DIVISOR``.

``X``, ``Y``, ``Z``, the capture payment and each vehicle's own profitability
coefficient are not published, so the named values below remain an explicit
offline policy. The tier curve (``XP_TIER_PERMILLE``) and durability-based
kill payment are offline balance proxies. Percentile ratios do not identify retail XP
coefficients, and durability does not uniquely determine vehicle tier. The
client settles ammunition, repair and consumable costs using garage prices;
server receipts leave their cost fields at zero to avoid charging twice.
"""


# Offline-only coefficients. They make the published reward categories useful
# in an isolated progression loop; they are not retail 0.9.22 economy
# constants.
OFFLINE_PARTICIPATION_CREDITS_PER_TIER = 1000
OFFLINE_DAMAGE_CREDITS_PER_POINT = 2
OFFLINE_SPOTTING_CREDITS = 100
# Retail pays double for detecting an SPG.
OFFLINE_SPG_SPOTTING_MULTIPLIER = 2
# One completed capture is worth this much, split between its participants.
OFFLINE_CAPTURE_CREDITS = 1000
OFFLINE_PARTICIPATION_XP = 100
# Wargaming's support material says a kill counts "with the difference in
# vehicle tiers taken into account". This implementation uses victim
# durability as a balance proxy, not an exact tier-difference calculation.
# The divisor is pinned the same way
# ``XP_TIER_PERMILLE`` is pinned: the median stock durability of the client's
# Tier VIII vehicles is 1400, so dividing by 14 leaves a Tier VIII kill worth
# the 100 XP it was worth under the previous flat rule, and every other tier
# moves relative to it.  Median stock durability per tier, measured from the
# pinned client: 115, 152, 215, 310, 420, 720, 960, 1400, 1610, 2000.
KILL_XP_DURABILITY_DIVISOR = 14
OFFLINE_WIN_CREDITS_FACTOR_100 = 185
OFFLINE_WIN_XP_FACTOR_100 = 150
OFFLINE_FREE_XP_PERCENT = 5

# Offline tier scaling uses a ratio from the two captured retail tables in
# ``mastery_catalog``: for every vehicle carrying both rows, the Ace base-XP
# threshold divided by the three-mark average combined damage, then the median
# per tier, normalised at Tier VIII so this changes the shape of the curve
# rather than the magnitude of the reward.  The measurement mixes a
# single-battle XP percentile with a 100-battle damage percentile. It cannot
# identify retail XP coefficients; the pivot only anchors this offline curve.
#
# ``tests/test_port_0922_offline_rewards.py`` recomputes these from the baked
# catalog, so a re-bake that moves them fails rather than drifting silently.
# Retail publishes no mark data below Tier V, so the four lowest tiers hold
# the lowest measured value instead of extrapolating past the data.
XP_TIER_PERMILLE = {
    1: 1547, 2: 1547, 3: 1547, 4: 1547,
    5: 1547, 6: 1356, 7: 1176, 8: 1000, 9: 763, 10: 582,
}
XP_TIER_PIVOT = 8


def compute_offline_rewards(statistics, won, participated=True,
                            vehicle_tier=1, spotted_spgs=0,
                            capture_participants=0, killed_durability=0):
    """Return one actor's Credits, XP and Free XP for a finished battle.

    ``spotted_spgs`` is how many of the actor's own first detections were
    SPGs; it is a subset of ``spotted``.  ``capture_participants`` is zero
    unless a capture by this actor's team completed with this actor taking
    part, in which case it is the number of vehicles the payment is split
    between.  ``killed_durability`` is the total maximum durability of the
    vehicles this actor destroyed, and it is what pays kill XP: the plain
    ``kills`` count carries no XP of its own, because a kill is worth the
    vehicle killed.  All three are decided by the server, which owns the
    detection, capture and kill ledgers, and none is persisted as a battle
    statistic.
    """
    statistics = statistics if isinstance(statistics, dict) else {}

    def value(name):
        try:
            return max(0, int(statistics.get(name, 0) or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    damage = value("damage_dealt")
    assist = (value("damage_assisted_track") +
              value("damage_assisted_radio") +
              value("damage_assisted_stun"))
    spotted = value("spotted")
    capture = value("capture_points")
    dropped_capture = value("dropped_capture_points")
    participation = OFFLINE_PARTICIPATION_XP if participated else 0
    try:
        vehicle_tier = max(1, min(10, int(vehicle_tier)))
    except (TypeError, ValueError, OverflowError):
        vehicle_tier = 1
    try:
        spotted_spgs = max(0, min(spotted, int(spotted_spgs)))
    except (TypeError, ValueError, OverflowError):
        spotted_spgs = 0
    try:
        capture_participants = max(0, int(capture_participants))
    except (TypeError, ValueError, OverflowError):
        capture_participants = 0
    try:
        killed_durability = max(0, int(killed_durability))
    except (TypeError, ValueError, OverflowError):
        killed_durability = 0

    # Damage, assisted damage and kills carry the tier relationship; integer
    # permille arithmetic keeps the server's reward deterministic.
    combat_xp_terms = (damage // 5 + assist // 10 +
                       killed_durability // KILL_XP_DURABILITY_DIVISOR)
    tier_permille = XP_TIER_PERMILLE.get(
        vehicle_tier, XP_TIER_PERMILLE[XP_TIER_PIVOT])
    base_xp = (participation +
               combat_xp_terms * tier_permille // 1000 +
               spotted * 20 + capture * 2 + dropped_capture * 2)
    combat_xp = (base_xp * OFFLINE_WIN_XP_FACTOR_100 // 100 if won
                 else base_xp)

    # Only the participation payment carries the victory multiplier, and only
    # it scales with vehicle tier.
    participation_credits = (
        OFFLINE_PARTICIPATION_CREDITS_PER_TIER * vehicle_tier
        if participated else 0)
    if won:
        participation_credits = (
            participation_credits * OFFLINE_WIN_CREDITS_FACTOR_100 // 100)
    spotting_credits = OFFLINE_SPOTTING_CREDITS * (
        spotted + spotted_spgs * (OFFLINE_SPG_SPOTTING_MULTIPLIER - 1))
    capture_credits = (OFFLINE_CAPTURE_CREDITS // capture_participants
                       if capture_participants else 0)
    # A frag is an XP event, not a separate credits event, and assisted damage
    # is not in the published credits list.  Any damage that led to either is
    # already represented by ``damage_dealt`` above.
    credits = (
        participation_credits +
        damage * OFFLINE_DAMAGE_CREDITS_PER_POINT +
        spotting_credits +
        capture_credits)
    return {
        "credits": int(credits),
        "xp": int(combat_xp),
        "free_xp": int(combat_xp * OFFLINE_FREE_XP_PERCENT // 100),
        "repair_cost": 0,
        "ammo_cost": 0,
    }
