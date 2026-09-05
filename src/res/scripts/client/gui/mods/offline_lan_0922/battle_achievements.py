"""Award #1513 post-battle achievements from one finished round.

The thresholds below are copied verbatim from the pinned client's
``scripts/common/arena_achievements.py`` (``ACHIEVEMENT_CONDITIONS``) inside
``res/packages/scripts.pkg`` of World of Tanks HD ``0.9.22.0.1 #1513``.  That
module also proves which table applies: ``getAchievementCondition`` only reads
``ACHIEVEMENT_CONDITIONS_EXT`` when ``ARENA_BONUS_TYPE_CAPS.checkAny`` accepts
the arena bonus type, and #1513 answers ``False`` for every bonus type,
including the regular battles this product packs (``bonusType`` 1).

The full #1513 table holds 62 entries covering every mode this build ever
shipped.  ``scripts/item_defs/achievements.xml`` tags each achievement with a
``mode``, and only ``mode="random"`` belongs to the battles this product packs;
that file also decides which medals exist at all, since a condition without an
entry there is one Wargaming cancelled before release.

The client ships the thresholds but not the retail award predicates, which run
on Wargaming's battle server.  The rest of each rule is in the client's own
description text, ``res/text/LC_MESSAGES/achievements.mo``: every medal there
carries a ``<name>_descr`` summary and a ``<name>_condition`` clause list, and
those clauses are what each predicate below implements.  The clause is quoted
in Chinese beside the code that enforces it so the two cannot drift.  A
threshold alone is never the rule: ``medalCoolBlood`` ships only a distance and
a kill count, while its description also demands light-tank victims and a Tier
IV gun.  Anything that would still need a coefficient this repository cannot
source is not awarded at all; ``UNAWARDED_ACHIEVEMENTS`` records those and why.

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

# The #1513 table holds 62 entries across every game mode this build ever
# shipped.  Copied verbatim below is the subset ``item_defs/achievements.xml``
# marks ``mode="random"`` and this server can decide; names and numbers must
# not be edited without re-reading the pinned client.
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
    "huntsman": {"minKills": 3},
    "ironMan": {"minHits": 10},
    "luckyDevil": {"radius": 10.99},
    # #1513 keys Rock Solid's condition as ``monolith`` while the dossier
    # record and the results layout both call it ``medalMonolith``.
    "monolith": {"maxSpeed_ms": 3.0555555555555554},
    # ``raider`` and ``medalFadin`` carry no numeric threshold in #1513; both
    # awards are purely structural.
    "raider": {},
    "medalFadin": {},
}

# Condition key -> dossier record name, where #1513 disagrees with itself.
CONDITION_RECORD_NAMES = {"monolith": "medalMonolith"}

# Medals #1513 knows about that this server deliberately never awards.  Keeping
# the reason next to the name stops a later change from "fixing" one by
# inventing the missing input.
UNAWARDED_ACHIEVEMENTS = {
    "sniper": "#1513 registers it as DeprecatedAchievement; the client itself "
              "only accepts one already in the dossier",
    "medalWittmann": "#1513 registers it as DeprecatedAchievement",
    "alaric": "no entry in #1513 item_defs/achievements.xml; Wargaming "
              "cancelled it before release",
    "lumberjack": "no entry in #1513 item_defs/achievements.xml; Wargaming "
                  "cancelled it before release",
    "medalBrothersInArms": "platoon award; this product has no platoons",
    "medalCrucialContribution": "platoon award; this product has no platoons",
    "markOfMastery": "needs per-vehicle XP distributions retail computes",
}

# Every name this server can award, under the record name #1513 stores.  The
# wire validators use it as an exact allowlist; the client packer still maps
# each one through the pinned ``dossiers2.custom.records.RECORD_DB_IDS`` table
# before it reaches #1513.
AWARDABLE_ACHIEVEMENTS = tuple(sorted(
    CONDITION_RECORD_NAMES.get(name, name)
    for name in ACHIEVEMENT_CONDITIONS))

# Battle-hero medals go to exactly one actor per battle; the medal's own
# metric also orders that winner.
_UNIQUE_BATTLE_HEROES = (
    "warrior", "invader", "defender", "steelwall", "mainGun",
    "supporter", "scout", "evileye", "sniper2",
)

# "坦克与自行反坦克炮" - every tier-delta medal counts tanks and tank
# destroyers, never artillery.
_DIRECT_FIRE_CLASSES = ("lightTank", "mediumTank", "heavyTank", "AT-SPG")

# "使用至少为4级的自行火炮" - Cold-Blooded's only tier floor, which #1513
# ships in the description rather than in ACHIEVEMENT_CONDITIONS.
_COOL_BLOOD_MIN_TIER = 4

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


def _tier_kills(actor, delta, victim_classes=None):
    """Count kills of victims at least ``delta`` tiers above the actor.

    A tier of zero means the client never catalogued that vehicle, so its
    tier is unknown and no tier-relative medal may read it.
    """
    tier = _int(actor.get("tier"))
    if tier <= 0:
        return 0
    total = 0
    for kill in _kills(actor):
        if (victim_classes is not None and
                kill.get("victim_class") not in victim_classes):
            continue
        victim_tier = _int(kill.get("victim_tier"))
        if victim_tier > 0 and victim_tier - tier >= delta:
            total += 1
    return total


def _class_kills(actor, victim_classes):
    if isinstance(victim_classes, str):
        victim_classes = (victim_classes,)
    return sum(1 for kill in _kills(actor)
               if kill.get("victim_class") in victim_classes)


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


def _kill_band(actor, condition, delta=None, victim_classes=None):
    """Count the qualifying kills and test the medal's inclusive band."""
    if delta is None:
        if victim_classes is None:
            count = _stat(actor, "kills")
        else:
            count = _class_kills(actor, victim_classes)
    else:
        count = _tier_kills(actor, delta, victim_classes=victim_classes)
    minimum = _int(condition.get("minKills"), 0)
    maximum = _int(condition.get("maxKills"), 255)
    return count if minimum <= count <= maximum else 0


def _enemy_team_health(actors, team):
    return sum(max(0, _int(actor.get("max_health")))
               for actor in actors if _int(actor.get("team")) != team)


def _enemy_class_counts(actors, team):
    """Return how many vehicles of each class opposed ``team``."""
    counts = {}
    for actor in actors:
        if _int(actor.get("team")) == team:
            continue
        vehicle_class = actor.get("vehicle_class")
        if not vehicle_class:
            continue
        counts[vehicle_class] = counts.get(vehicle_class, 0) + 1
    return counts


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
        # "此荣誉只颁发给成功占领基地" - only a completed capture qualifies.
        points = _stat(actor, "capture_points")
        captured = context["base_captured_team"] == _int(actor.get("team"))
        return (points if captured and points >= condition["minCapturePts"]
                else None)
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
        # "玩家不能击中盟友坦克" - one friendly hit ends it.
        if _int(actor.get("ally_hits")):
            return None
        damage = _stat(actor, "damage")
        enemy_health = context["enemy_team_health"][_int(actor.get("team"))]
        threshold = condition["minDamageToTotalHealthRatio"] * enemy_health
        return damage if (damage >= condition["minDamage"] and
                          enemy_health > 0 and damage >= threshold) else None
    if name == "supporter":
        # "比其它玩家击伤更多的敌方坦克或击毁他们的履带", with
        # "跳弹未击穿不被计算在内": distinct enemies this actor actually hurt
        # or immobilised, never a bounce and never somebody else's kill.
        assists = _int(actor.get("support_targets"))
        return assists if assists >= condition["minAssists"] else None
    if name == "scout":
        # "必须胜利才可以获得."
        detections = _stat(actor, "spotted")
        return (detections if actor.get("won") and
                detections >= condition["minDetections"] else None)
    if name == "evileye":
        assists = _int(actor.get("exclusive_spot_assists"))
        return assists if assists >= condition["minAssists"] else None
    if name == "sniper2":
        # "自行火炮无法获得", "玩家不得直接射中任何友军", and the damage from
        # 300 m out must beat both 1000 and the shooter's own hit points.
        if (actor.get("vehicle_class") == _SPG or
                _int(actor.get("ally_hits"))):
            return None
        damage = _int(actor.get("sniper_damage"))
        return damage if (
            _stat(actor, "shots") >= condition["minShots"] and
            _accuracy(actor) >= condition["minAccuracy"] and
            _damage_hit_ratio(actor) >=
            condition["minHitsWithDamagePercent"] and
            damage >= condition["minDamage"] and
            damage > _int(actor.get("max_health"))) else None
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
    # class its namesake fought in, and every one of these descriptions says
    # "坦克与自行反坦克炮" - tanks and tank destroyers, never artillery.
    for name, required_class in (
            ("medalOrlik", _LIGHT_TANK),
            ("medalOskin", _MEDIUM_TANK),
            ("medalNikolas", _MEDIUM_TANK),
            ("medalLehvaslaiho", _MEDIUM_TANK),
            ("medalHalonen", _TANK_DESTROYER)):
        condition = ACHIEVEMENT_CONDITIONS[name]
        if vehicle_class == required_class and _kill_band(
                actor, condition, delta=condition["minVictimLevelDelta"],
                victim_classes=_DIRECT_FIRE_CLASSES):
            earned.append(name)

    # Artillery-hunting medals.  A self-propelled gun cannot earn them.
    if vehicle_class != _SPG:
        for name, delta in (("medalBurda", 1), ("medalPascucci", None),
                            ("medalDumitru", None)):
            condition = ACHIEVEMENT_CONDITIONS[name]
            if _kill_band(actor, condition, delta=delta,
                          victim_classes=(_SPG,)):
                earned.append(name)
    if vehicle_class == _LIGHT_TANK and survived:
        condition = ACHIEVEMENT_CONDITIONS["medalTamadaYoshio"]
        if _kill_band(actor, condition,
                      delta=condition["minVictimLevelDelta"],
                      victim_classes=(_SPG,)):
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

    # Naidin's medal (``huntsman``): "一场战斗中击毁敌方所有轻型坦克(至少
    # 三辆)".  The count only means anything when the enemy fielded some.
    condition = ACHIEVEMENT_CONDITIONS["huntsman"]
    enemy_light_tanks = context["enemy_class_counts"][
        _int(actor.get("team"))].get(_LIGHT_TANK, 0)
    if (enemy_light_tanks >= condition["minKills"] and
            _class_kills(actor, _LIGHT_TANK) >= enemy_light_tanks):
        earned.append("huntsman")

    # Cool-Headed (``ironMan``) counts bounces in a row, not bounces in total.
    # "至少连续十次未被敌方击穿或被敌方坦克击中但跳弹" carries no survival
    # clause, unlike Spartan below.
    condition = ACHIEVEMENT_CONDITIONS["ironMan"]
    if _int(actor.get("best_deflection_streak")) >= condition["minHits"]:
        earned.append("ironMan")

    if actor.get("lucky_devil"):
        earned.append("luckyDevil")

    if actor.get("last_shell_finisher"):
        earned.append("medalFadin")

    if vehicle_class == _SPG:
        # Every artillery medal here carries "玩家不能击毁任何盟友坦克".
        # Friendly hits simply do not count toward a total; a friendly kill
        # ends the award outright.
        clean = not _int(actor.get("team_kills"))
        max_health = _int(actor.get("max_health"))
        condition = ACHIEVEMENT_CONDITIONS["medalGore"]
        damage = _stat(actor, "damage")
        if (clean and damage >= condition["minDamage"] and max_health > 0 and
                damage >= condition["minDamageRate"] * max_health):
            earned.append("medalGore")
        # Rock Solid: ram an enemy to death from a crawl while it was the
        # faster of the two, and drive away.
        # "击毁您的敌方坦克速度必须高于您的自行火炮速度."
        condition = ACHIEVEMENT_CONDITIONS["monolith"]
        if (clean and survived and any(
                _int(kill.get("death_reason")) == 2 and
                _float(kill.get("actor_speed"), -1.0) >= 0.0 and
                _float(kill.get("actor_speed")) <= condition["maxSpeed_ms"] and
                _float(kill.get("victim_speed"), -1.0) >
                _float(kill.get("actor_speed"))
                for kill in _kills(actor))):
            earned.append("medalMonolith")
        # "受到的伤害以及装甲伤害必须是自身坦克生命值的三分之二."
        # The description narrows both incoming hits to enemy tanks. Friendly
        # damage still belongs in the public result row, but never in this
        # award input, and Stark carries no friendly-kill disqualifier.
        condition = ACHIEVEMENT_CONDITIONS["medalStark"]
        absorbed = _int(actor.get("enemy_damage_received")) + _stat(
            actor, "damage_blocked")
        if (survived and kills >= condition["minKills"] and
                _int(actor.get("damaging_hits_received")) >=
                condition["hits"] and max_health > 0 and
                3 * absorbed >= 2 * max_health):
            earned.append("medalStark")
        # "在不超过100米的距离内击毁至少2辆敌方轻型坦克" with
        # "使用至少为4级的自行火炮."
        condition = ACHIEVEMENT_CONDITIONS["medalCoolBlood"]
        close_kills = sum(
            1 for kill in _kills(actor)
            if kill.get("victim_class") == _LIGHT_TANK and
            _float(kill.get("distance"), -1.0) >= 0.0 and
            _float(kill.get("distance")) <= condition["maxDistance"])
        if (clean and tier >= _COOL_BLOOD_MIN_TIER and
                close_kills >= condition["minKills"]):
            earned.append("medalCoolBlood")
        # "使用自行火炮击毁敌方所有自行火炮(至少2辆)" - the whole enemy
        # battery, not any two artillery kills.
        condition = ACHIEVEMENT_CONDITIONS["medalAntiSpgFire"]
        enemy_spgs = context["enemy_class_counts"][
            _int(actor.get("team"))].get(_SPG, 0)
        if (clean and enemy_spgs >= condition["minKills"] and
                _class_kills(actor, _SPG) >= enemy_spgs):
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

    # "单独占领敌人基地且在整场战斗中未被敌人发现" - the capture must have
    # completed, this actor must be the only vehicle that ever moved that
    # base's counter, and nobody may have detected it all battle.  Taking
    # damage does not disqualify: "如果坦克被偶然击中或受损并不取消".
    if (actor.get("captured_base") and actor.get("solo_capture") and
            not actor.get("ever_spotted") and
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
        "enemy_class_counts": dict(
            (team, _enemy_class_counts(actors, team)) for team in (1, 2)),
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
