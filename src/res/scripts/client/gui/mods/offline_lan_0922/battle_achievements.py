"""Award #1513 post-battle achievements from one finished round.

The thresholds below are copied verbatim from the pinned client's
``scripts/common/arena_achievements.py`` (``ACHIEVEMENT_CONDITIONS``) inside
``res/packages/scripts.pkg`` of World of Tanks HD ``0.9.22.0.1 #1513``.  That
module also proves which table applies: ``getAchievementCondition`` only reads
``ACHIEVEMENT_CONDITIONS_EXT`` when ``ARENA_BONUS_TYPE_CAPS.checkAny`` accepts
the arena bonus type, and #1513 answers ``False`` for every bonus type,
including the regular battles this product packs (``bonusType`` 1).

The client ships the thresholds but not the retail award predicates, which run
on Wargaming's battle server.  Each predicate below therefore combines an exact
#1513 constant with the publicly documented shape of that medal.  Anything that
would need a coefficient this repository cannot source is not awarded at all;
``UNAWARDED_ACHIEVEMENTS`` records those and why.

This module is pure data: it takes one finished-round summary and returns the
achievement names for every actor.  It never touches live battle state.  It
lives with the client mod because the exact-client packer needs the same
awardable-name table that the LAN server awards from, and it must therefore
parse and run on the embedded CPython 2.7.7 runtime.
"""

from __future__ import division

import math


# Every statistic one battle receipt carries for one vehicle.  #1513 shows all
# of them on the results screen and the conditions below read them, so this is
# the single wire contract shared by the server, the client receiver and the
# exact-client result packer.
RECEIPT_STAT_NAMES = (
    "shots", "direct_hits", "piercings", "damage", "damage_received",
    "damage_blocked", "assist_track", "assist_radio", "assist_stun",
    "kills", "spotted", "capture_points", "dropped_capture_points",
    "hits_received", "potential_damage_received", "crits_received",
)

# Verbatim #1513 ``arena_achievements.ACHIEVEMENT_CONDITIONS`` subset for the
# medals this module can decide.  Names and numbers must not be edited without
# re-reading the pinned client.
ACHIEVEMENT_CONDITIONS = {
    "warrior": {"minFrags": 6},
    "invader": {"minCapturePts": 80},
    "defender": {"minPoints": 70},
    "sniper2": {
        "minAccuracy": 0.85,
        "minDamage": 1000,
        "minHitsWithDamagePercent": 0.8,
        "minShots": 8,
        "sniperDistance": 300.0,
    },
    "mainGun": {"minDamage": 1000, "minDamageToTotalHealthRatio": 0.2},
    "steelwall": {"minDamage": 1000, "minHits": 11},
    "supporter": {"minAssists": 6},
    "scout": {"minDetections": 9},
    "evileye": {"minAssists": 6},
    "heroesOfRassenay": {"maxKills": 255, "minKills": 14},
    "medalLafayettePool": {"maxKills": 13, "minKills": 10, "minLevel": 5},
    "medalRadleyWalters": {"maxKills": 9, "minKills": 8, "minLevel": 5},
    "medalOrlik": {"minKills": 2, "minVictimLevelDelta": 1},
    "medalOskin": {"maxKills": 3, "minKills": 3, "minVictimLevelDelta": 1},
    "medalNikolas": {"maxKills": 255, "minKills": 4, "minVictimLevelDelta": 1},
    "medalLehvaslaiho": {
        "maxKills": 2, "minKills": 2, "minVictimLevelDelta": 1},
    "medalHalonen": {"minKills": 2, "minVictimLevelDelta": 2},
    "medalBurda": {"maxKills": 255, "minKills": 3, "minVictimLevelDelta": 1},
    "medalPascucci": {"maxKills": 2, "minKills": 2},
    "medalDumitru": {"maxKills": 255, "minKills": 3},
    "medalTamadaYoshio": {
        "maxKills": 255, "minKills": 2, "minVictimLevelDelta": 1},
    "medalBillotte": {
        "cmn_cnds": {"hpPercentage": 20, "minCrits": 5},
        "maxKills": 2, "minKills": 2,
    },
    "medalBrunoPietro": {
        "cmn_cnds": {"hpPercentage": 20, "minCrits": 5},
        "maxKills": 4, "minKills": 3,
    },
    "medalTarczay": {
        "cmn_cnds": {"hpPercentage": 20, "minCrits": 5},
        "maxKills": 255, "minKills": 5,
    },
    "medalKolobanov": {"teamDiff": 5},
    "medalDeLanglade": {"minKills": 4},
    "medalGore": {"minDamage": 2000, "minDamageRate": 8},
    "medalStark": {"hits": 2, "minKills": 2},
    "medalCoolBlood": {"maxDistance": 100, "minKills": 2},
    "medalAntiSpgFire": {"minKills": 2},
    "bombardier": {"minKills": 2},
    "kamikaze": {"levelDelta": 1},
    "sturdy": {"minHealth": 10.0},
    # ``raider`` carries no numeric threshold in #1513; its award is purely
    # structural (capture the enemy base without ever being detected).
    "raider": {},
}

# Medals #1513 knows about that this server deliberately never awards.  Keeping
# the reason next to the name stops a later change from "fixing" one by
# inventing the missing input.
UNAWARDED_ACHIEVEMENTS = {
    "sniper": "retired by Wargaming in 0.8.11; superseded by sniper2",
    "medalWittmann": "no condition entry in #1513 arena_achievements",
    "medalFadin": "needs remaining-ammunition state the server does not own",
    "medalBrothersInArms": "platoon award; offline rounds have no platoons",
    "medalCrucialContribution": "platoon award",
    "medalMonolith": "needs the rammer's impact speed",
    "luckyDevil": "needs splash-radius survival geometry",
    "huntsman": "no sourced #1513 predicate for minKills 3",
    "ironMan": "no sourced #1513 predicate for minHits 10",
    "alaric": "needs per-vehicle monument destruction attribution",
    "lumberjack": "needs per-vehicle felled-tree attribution",
    "markOfMastery": "needs per-vehicle XP distributions retail computes",
}

# Every name this server can award.  The wire validators use it as an exact
# allowlist; the client packer still maps each one through the pinned
# ``dossiers2.custom.records.RECORD_DB_IDS`` table before it reaches #1513.
AWARDABLE_ACHIEVEMENTS = tuple(sorted(ACHIEVEMENT_CONDITIONS))

# Battle-hero medals go to exactly one actor per battle; the medal's own
# metric also orders that winner.
_UNIQUE_BATTLE_HEROES = (
    "warrior", "invader", "defender", "steelwall", "mainGun",
    "supporter", "scout", "evileye", "sniper2",
)

_LIGHT_TANK = "lightTank"
_MEDIUM_TANK = "mediumTank"
_TANK_DESTROYER = "AT-SPG"
_SPG = "SPG"


def _int(value, default=0):
    if isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _float(value, default=0.0):
    if isinstance(value, bool):
        return float(default)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    # CPython 2.7 has no ``math.isfinite``.
    if math.isnan(result) or math.isinf(result):
        return float(default)
    return result


def _identity(actor):
    return (str(actor.get("actor_kind", "")), _int(actor.get("actor_id")))


def _stat(actor, name):
    stats = actor.get("stats")
    return max(0, _int(stats.get(name))) if isinstance(stats, dict) else 0


def _kills(actor):
    rows = actor.get("kills")
    return list(rows) if isinstance(rows, (list, tuple)) else []


def _tier_kills(actor, delta, victim_class=None):
    """Count kills of victims at least ``delta`` tiers above the actor.

    A tier of zero means the client never catalogued that vehicle, so its
    tier is unknown and no tier-relative medal may read it.
    """
    tier = _int(actor.get("tier"))
    if tier <= 0:
        return 0
    total = 0
    for kill in _kills(actor):
        if (victim_class is not None and
                kill.get("victim_class") != victim_class):
            continue
        victim_tier = _int(kill.get("victim_tier"))
        if victim_tier > 0 and victim_tier - tier >= delta:
            total += 1
    return total


def _class_kills(actor, victim_class):
    return sum(1 for kill in _kills(actor)
               if kill.get("victim_class") == victim_class)


def _health_percent(actor):
    maximum = _int(actor.get("max_health"))
    if maximum <= 0:
        return 100.0
    return 100.0 * float(max(0, _int(actor.get("health")))) / float(maximum)


def _billotte_family(actor, condition):
    """Shared Billotte/Bruno Pietro/Tarczay survival-under-fire clause."""
    common = condition.get("cmn_cnds") or {}
    return (actor.get("survived") and actor.get("won") and
            _int(actor.get("crits_received")) >=
            _int(common.get("minCrits"), 0) and
            _health_percent(actor) <=
            _float(common.get("hpPercentage"), 0.0))


def _kill_band(actor, condition, delta=None, victim_class=None):
    """Count the qualifying kills and test the medal's inclusive band."""
    if delta is None:
        if victim_class is None:
            count = _stat(actor, "kills")
        else:
            count = _class_kills(actor, victim_class)
    else:
        count = _tier_kills(actor, delta, victim_class=victim_class)
    minimum = _int(condition.get("minKills"), 0)
    maximum = _int(condition.get("maxKills"), 255)
    return count if minimum <= count <= maximum else 0


def _enemy_team_health(actors, team):
    return sum(max(0, _int(actor.get("max_health")))
               for actor in actors if _int(actor.get("team")) != team)


def _accuracy(actor):
    shots = _stat(actor, "shots")
    if shots <= 0:
        return 0.0
    return float(_stat(actor, "direct_hits")) / float(shots)


def _damage_hit_ratio(actor):
    hits = _stat(actor, "direct_hits")
    if hits <= 0:
        return 0.0
    return float(_int(actor.get("hits_with_damage"))) / float(hits)


def _unique_hero_metric(name, actor, context):
    """Return this actor's battle-hero metric, or None when it does not
    satisfy the medal's #1513 thresholds."""
    condition = ACHIEVEMENT_CONDITIONS[name]
    if name == "warrior":
        kills = _stat(actor, "kills")
        return kills if kills >= condition["minFrags"] else None
    if name == "invader":
        points = _stat(actor, "capture_points")
        return points if points >= condition["minCapturePts"] else None
    if name == "defender":
        points = _stat(actor, "dropped_capture_points")
        return points if points >= condition["minPoints"] else None
    if name == "steelwall":
        potential = _int(actor.get("potential_damage_received"))
        return potential if (
            actor.get("survived") and
            potential >= condition["minDamage"] and
            _int(actor.get("hits_received")) >= condition["minHits"]) else None
    if name == "mainGun":
        damage = _stat(actor, "damage")
        enemy_health = context["enemy_team_health"][_int(actor.get("team"))]
        threshold = condition["minDamageToTotalHealthRatio"] * enemy_health
        return damage if (damage >= condition["minDamage"] and
                          enemy_health > 0 and damage >= threshold) else None
    if name == "supporter":
        assists = len(context["confederate"].get(_identity(actor), ()))
        return assists if assists >= condition["minAssists"] else None
    if name == "scout":
        detections = _int(actor.get("first_spotted"))
        return (detections if detections >= condition["minDetections"]
                else None)
    if name == "evileye":
        assists = _int(actor.get("exclusive_spot_assists"))
        return assists if assists >= condition["minAssists"] else None
    if name == "sniper2":
        damage = _int(actor.get("sniper_damage"))
        return damage if (
            _stat(actor, "shots") >= condition["minShots"] and
            _accuracy(actor) >= condition["minAccuracy"] and
            _damage_hit_ratio(actor) >=
            condition["minHitsWithDamagePercent"] and
            damage >= condition["minDamage"]) else None
    raise KeyError(name)


def _epic_achievements(actor, context):
    """Return every non-unique medal this actor earned."""
    earned = []
    vehicle_class = actor.get("vehicle_class")
    tier = _int(actor.get("tier"))
    kills = _stat(actor, "kills")
    survived = bool(actor.get("survived"))

    condition = ACHIEVEMENT_CONDITIONS["heroesOfRassenay"]
    if condition["minKills"] <= kills <= condition["maxKills"]:
        earned.append("heroesOfRassenay")
    for name in ("medalLafayettePool", "medalRadleyWalters"):
        condition = ACHIEVEMENT_CONDITIONS[name]
        if (tier >= condition["minLevel"] and
                condition["minKills"] <= kills <= condition["maxKills"]):
            earned.append(name)

    # Historical tier-delta medals.  #1513 restricts each one to the vehicle
    # class its namesake fought in; the tier delta itself is the constant.
    for name, required_class in (
            ("medalOrlik", _LIGHT_TANK),
            ("medalOskin", _MEDIUM_TANK),
            ("medalNikolas", _MEDIUM_TANK),
            ("medalLehvaslaiho", _MEDIUM_TANK),
            ("medalHalonen", _TANK_DESTROYER)):
        condition = ACHIEVEMENT_CONDITIONS[name]
        if vehicle_class == required_class and _kill_band(
                actor, condition, delta=condition["minVictimLevelDelta"]):
            earned.append(name)

    # Artillery-hunting medals.  A self-propelled gun cannot earn them.
    if vehicle_class != _SPG:
        for name, delta in (("medalBurda", 1), ("medalPascucci", None),
                            ("medalDumitru", None)):
            condition = ACHIEVEMENT_CONDITIONS[name]
            if _kill_band(actor, condition, delta=delta, victim_class=_SPG):
                earned.append(name)
    if vehicle_class == _LIGHT_TANK and survived:
        condition = ACHIEVEMENT_CONDITIONS["medalTamadaYoshio"]
        if _kill_band(actor, condition,
                      delta=condition["minVictimLevelDelta"],
                      victim_class=_SPG):
            earned.append("medalTamadaYoshio")

    for name in ("medalBillotte", "medalBrunoPietro", "medalTarczay"):
        condition = ACHIEVEMENT_CONDITIONS[name]
        if (_billotte_family(actor, condition) and
                _kill_band(actor, condition)):
            earned.append(name)

    condition = ACHIEVEMENT_CONDITIONS["medalKolobanov"]
    if (actor.get("won") and
            _int(actor.get("lone_stand_enemies")) >= condition["teamDiff"]):
        earned.append("medalKolobanov")

    condition = ACHIEVEMENT_CONDITIONS["medalDeLanglade"]
    base_defence_kills = sum(1 for kill in _kills(actor)
                             if kill.get("defended_base"))
    if base_defence_kills >= condition["minKills"]:
        earned.append("medalDeLanglade")

    if vehicle_class == _SPG:
        condition = ACHIEVEMENT_CONDITIONS["medalGore"]
        damage = _stat(actor, "damage")
        max_health = _int(actor.get("max_health"))
        if (damage >= condition["minDamage"] and max_health > 0 and
                damage >= condition["minDamageRate"] * max_health):
            earned.append("medalGore")
        condition = ACHIEVEMENT_CONDITIONS["medalStark"]
        if (survived and kills >= condition["minKills"] and
                _int(actor.get("damaging_hits_received")) >=
                condition["hits"]):
            earned.append("medalStark")
        condition = ACHIEVEMENT_CONDITIONS["medalCoolBlood"]
        close_kills = sum(
            1 for kill in _kills(actor)
            if _float(kill.get("distance"), -1.0) >= 0.0 and
            _float(kill.get("distance")) <= condition["maxDistance"])
        if close_kills >= condition["minKills"]:
            earned.append("medalCoolBlood")
        condition = ACHIEVEMENT_CONDITIONS["medalAntiSpgFire"]
        if _class_kills(actor, _SPG) >= condition["minKills"]:
            earned.append("medalAntiSpgFire")

    condition = ACHIEVEMENT_CONDITIONS["bombardier"]
    if _int(actor.get("best_multi_kill_shot")) >= condition["minKills"]:
        earned.append("bombardier")

    condition = ACHIEVEMENT_CONDITIONS["kamikaze"]
    if tier > 0 and any(
            _int(kill.get("death_reason")) == 2 and
            _int(kill.get("victim_tier")) > 0 and
            _int(kill.get("victim_tier")) - tier >= condition["levelDelta"]
            for kill in _kills(actor)):
        earned.append("kamikaze")

    # #1513 stores only the health threshold; the server decides at each
    # bounced hit whether the vehicle was already under it.
    if survived and _int(actor.get("deflected_hits_at_low_health")) > 0:
        earned.append("sturdy")

    if (actor.get("captured_base") and not actor.get("ever_spotted") and
            context["base_captured_team"] == _int(actor.get("team"))):
        earned.append("raider")

    return earned


def _confederate_targets(actors):
    """Map each actor to the enemies it damaged that another actor killed."""
    killers = {}
    for actor in actors:
        identity = _identity(actor)
        for kill in _kills(actor):
            victim = (str(kill.get("victim_kind", "")),
                      _int(kill.get("victim_id")))
            killers.setdefault(victim, set()).add(identity)
    result = {}
    for actor in actors:
        identity = _identity(actor)
        damaged = actor.get("damaged_targets")
        targets = set()
        for raw in (damaged if isinstance(damaged, (list, tuple)) else ()):
            victim = (str(raw[0]), _int(raw[1]))
            if killers.get(victim, set()) - {identity}:
                targets.add(victim)
        result[identity] = targets
    return result


def award_battle_achievements(battle):
    """Return ``{(actor_kind, actor_id): [achievement names]}``.

    ``battle`` carries ``actors`` plus ``base_captured_team``.  Every actor
    row is a plain summary; see the module docstring for the contract.
    """
    actors = list(battle.get("actors") or ())
    if not actors:
        return {}
    context = {
        "enemy_team_health": dict(
            (team, _enemy_team_health(actors, team)) for team in (1, 2)),
        "confederate": _confederate_targets(actors),
        "base_captured_team": _int(battle.get("base_captured_team")),
    }
    awards = dict((_identity(actor), []) for actor in actors)

    for name in _UNIQUE_BATTLE_HEROES:
        best_identity = None
        best_key = None
        for actor in actors:
            metric = _unique_hero_metric(name, actor, context)
            if metric is None:
                continue
            identity = _identity(actor)
            # Wargaming breaks a battle-hero tie by earned XP.  An exact
            # remaining tie goes to the human, then to the lower actor id, so
            # the same round always produces the same winner.
            key = (metric, _int(actor.get("xp")),
                   1 if identity[0] == "player" else 0, -identity[1])
            if best_key is None or key > best_key:
                best_key = key
                best_identity = identity
        if best_identity is not None:
            awards[best_identity].append(name)

    for actor in actors:
        awards[_identity(actor)].extend(_epic_achievements(actor, context))
    return awards
