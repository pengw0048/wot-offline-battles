"""Deterministic offline reward reconstruction for LAN battles.

This is not Wargaming's proprietary 0.9.22 server formula. Public support
material documents the rewarded action categories and these relationships:
participation Credits scale with vehicle tier and use 1.85X on a win, frags do
not independently award Credits, wins add 50 percent XP, and Free XP is five
percent of Combat XP. The private X/Y/Z and balance coefficients remain
unavailable, so the named values below are an explicit offline policy. Offline
battles have zero ammunition and repair costs.
"""


# Offline-only coefficients. They make the public reward categories useful in
# an isolated progression loop; they are not retail 0.9.22 economy constants.
OFFLINE_PARTICIPATION_CREDITS_PER_TIER = 1000
OFFLINE_DAMAGE_CREDITS_PER_POINT = 2
OFFLINE_ASSIST_CREDITS_PER_POINT = 1
OFFLINE_SPOTTING_CREDITS = 100
OFFLINE_CAPTURE_CREDITS_PER_POINT = 10


def compute_offline_rewards(statistics, won, participated=True,
                            vehicle_tier=1):
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
    kills = value("kills")
    spotted = value("spotted")
    capture = value("capture_points")
    dropped_capture = value("dropped_capture_points")
    participation = 100 if participated else 0
    try:
        vehicle_tier = max(1, min(10, int(vehicle_tier)))
    except (TypeError, ValueError, OverflowError):
        vehicle_tier = 1

    base_xp = (participation + damage // 5 + assist // 10 +
               kills * 100 + spotted * 20 + capture * 2 +
               dropped_capture * 2)
    combat_xp = (base_xp * 3 // 2) if won else base_xp
    # A frag is an XP event, not a separate credits event.  Any damage that led
    # to the frag is already represented by ``damage_dealt`` above.
    participation_credits = (
        OFFLINE_PARTICIPATION_CREDITS_PER_TIER * vehicle_tier
        if participated else 0)
    if won:
        participation_credits = participation_credits * 185 // 100
    credits = (
        participation_credits +
        damage * OFFLINE_DAMAGE_CREDITS_PER_POINT +
        assist * OFFLINE_ASSIST_CREDITS_PER_POINT +
        spotted * OFFLINE_SPOTTING_CREDITS +
        capture * OFFLINE_CAPTURE_CREDITS_PER_POINT)
    return {
        "credits": int(credits),
        "xp": int(combat_xp),
        "free_xp": int(combat_xp * 5 // 100),
        "repair_cost": 0,
        "ammo_cost": 0,
    }
