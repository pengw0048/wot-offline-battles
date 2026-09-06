"""Persistent launcher-side profiles for exact 0.9.22 Bot lineups."""

from __future__ import annotations

import copy
import re


SCHEMA = 1
AUTOMATIC_PROFILE_LABEL = "Automatic lineup"
MAX_PROFILE_NAME_LENGTH = 48
MAX_VEHICLE_TYPE_NAME_LENGTH = 96

# This is the pinned #1513 baked resource blacklist used by the hidden
# worker.  A parity test binds this launcher copy to vehicle_blacklist.py so a
# future catalogue refresh cannot silently make the two selectors diverge.
UNUSABLE_BOT_VEHICLES_0922 = frozenset((
    "germany:G138_VK168_02_Mauerbrecher",
))
# The exact set vehicle_configuration.NON_STANDARD_BATTLE_TAGS applies in the
# mod, bound by a parity test.  ``secret`` is deliberately absent: a hidden
# entry with an honest level and name stays selectable, and only the Bootcamp
# clones below and the ``fallout`` copies are withheld.  ``unrecoverable`` is
# absent for the same reason the mod drops it: #1513 reads that tag only in
# its sold-vehicle and sold-crew restore rules.
NON_STANDARD_BOT_TAGS_0922 = frozenset((
    "event_battles", "premiumIGR", "observer", "fallout",
))
# #1513 publishes every ``_bootcamp`` tutorial copy at level 2 while it keeps
# the original hull, so offering one as a Bot puts a tier 6 tank in a tier 2
# slot.  Stock hides them with ``secret`` and this name suffix alone.
CLONE_BOT_VEHICLE_SUFFIXES_0922 = ("_bootcamp",)
# The tutorial and Bot placeholders, and the artillery strike emitter, are
# the entries no roster may field.  Stock hides them the same way.
NON_BATTLE_ENTITY_BOT_SUFFIXES_0922 = ("_bot", "_training")
NON_BATTLE_ENTITY_BOT_VEHICLES_0922 = frozenset(("germany:Env_Artillery",))
CATALOGUE_VISIBILITY_TAG_0922 = "secret"
# The Bot gunnery tiers bot_gunnery.SKILL_TIERS defines, weakest first, bound
# by a parity test so a new tier cannot appear on only one side.  ``None``
# leaves a pinned slot on the room's own skill preset.
BOT_SKILL_TIERS_0922 = ("rookie", "regular", "veteran", "elite")

_NATION = re.compile(r"^[a-z][a-z0-9_]*$")
_VEHICLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class BotLineupProfileError(ValueError):
    pass


def empty_store():
    return {"schema": SCHEMA, "profiles": []}


def _name(value):
    if not isinstance(value, str):
        raise BotLineupProfileError("The profile name must be text.")
    value = " ".join(value.split())
    if not value:
        raise BotLineupProfileError("Enter a profile name.")
    if len(value) > MAX_PROFILE_NAME_LENGTH:
        raise BotLineupProfileError(
            "Profile names may contain at most %d characters." %
            MAX_PROFILE_NAME_LENGTH)
    if value.casefold() == AUTOMATIC_PROFILE_LABEL.casefold():
        raise BotLineupProfileError("That profile name is reserved.")
    return value


def vehicle_type_name(choice):
    """Return the exact ``nation:vehicle`` name consumed by #1513."""
    if not isinstance(choice, dict):
        raise BotLineupProfileError("The vehicle choice is invalid.")
    nation = choice.get("nation")
    vehicle = choice.get("vehicle")
    if (not isinstance(nation, str) or _NATION.fullmatch(nation) is None or
            not isinstance(vehicle, str) or
            _VEHICLE.fullmatch(vehicle) is None):
        raise BotLineupProfileError("The vehicle choice is invalid.")
    return "%s:%s" % (nation, vehicle)


def _skill(value):
    """Return one supported gunnery tier, or None to follow the room preset."""
    if value is None:
        return None
    if value not in BOT_SKILL_TIERS_0922:
        raise BotLineupProfileError("The Bot skill level is invalid.")
    return value


def _vehicle_type_name(value):
    if (not isinstance(value, str) or
            len(value) > MAX_VEHICLE_TYPE_NAME_LENGTH or
            value.count(":") != 1):
        raise BotLineupProfileError("The Bot vehicle is invalid.")
    nation, vehicle = value.split(":", 1)
    if (_NATION.fullmatch(nation) is None or
            _VEHICLE.fullmatch(vehicle) is None):
        raise BotLineupProfileError("The Bot vehicle is invalid.")
    return value


def vehicle_choice_is_eligible(choice):
    """Mirror the server/hidden-worker admissible stock vehicle set."""
    type_name = vehicle_type_name(choice)
    tags = choice.get("tags") or ()
    if not isinstance(tags, (list, tuple, set, frozenset)):
        raise BotLineupProfileError("The vehicle tags are invalid.")
    tags = set(str(tag) for tag in tags)
    if CATALOGUE_VISIBILITY_TAG_0922 in tags and (
            type_name.endswith(CLONE_BOT_VEHICLE_SUFFIXES_0922) or
            type_name.endswith(NON_BATTLE_ENTITY_BOT_SUFFIXES_0922) or
            type_name in NON_BATTLE_ENTITY_BOT_VEHICLES_0922):
        return False
    return (not NON_STANDARD_BOT_TAGS_0922.intersection(tags) and
            type_name not in UNUSABLE_BOT_VEHICLES_0922)


def eligible_vehicle_choices(choices):
    """Attach canonical names and retain only vehicles both authorities use."""
    result = []
    for raw in choices or ():
        if not vehicle_choice_is_eligible(raw):
            continue
        choice = dict(raw)
        choice["type_name"] = vehicle_type_name(choice)
        result.append(choice)
    return result


def _assignments(value):
    if not isinstance(value, list) or len(value) > 30:
        raise BotLineupProfileError("The Bot lineup is invalid.")
    result, seen = [], set()
    for raw in value:
        if not isinstance(raw, dict):
            raise BotLineupProfileError("The Bot lineup is invalid.")
        try:
            team, slot = int(raw.get("team")), int(raw.get("slot"))
        except (TypeError, ValueError, OverflowError):
            raise BotLineupProfileError("The Bot slot is invalid.")
        raw_vehicle = raw.get("vehicle")
        vehicle = (None if raw_vehicle is None else
                   _vehicle_type_name(raw_vehicle))
        skill = _skill(raw.get("skill"))
        if (team not in (1, 2) or not 0 <= slot < 15 or
                (team, slot) in seen):
            raise BotLineupProfileError("The Bot lineup is invalid.")
        if vehicle is None and skill is None:
            # An entry that pins nothing is not a saved slot at all.
            continue
        seen.add((team, slot))
        entry = {"team": team, "slot": slot}
        if vehicle is not None:
            entry["vehicle"] = vehicle
        if skill is not None:
            entry["skill"] = skill
        result.append(entry)
    return sorted(result, key=lambda item: (item["team"], item["slot"]))


def normalize_store(value):
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return empty_store()
    profiles = value.get("profiles")
    if not isinstance(profiles, list):
        return empty_store()
    result, names = [], set()
    try:
        for raw in profiles:
            if not isinstance(raw, dict):
                raise BotLineupProfileError("invalid profile")
            name = _name(raw.get("name"))
            key = name.casefold()
            if key in names:
                raise BotLineupProfileError("duplicate profile")
            names.add(key)
            result.append({
                "name": name,
                "assignments": _assignments(raw.get("assignments", [])),
            })
    except BotLineupProfileError:
        return empty_store()
    return {
        "schema": SCHEMA,
        "profiles": sorted(result, key=lambda item: item["name"].casefold()),
    }


def names(store):
    return [profile["name"] for profile in normalize_store(store)["profiles"]]


def create(store, raw_name):
    store = normalize_store(store)
    name = _name(raw_name)
    if any(profile["name"].casefold() == name.casefold()
           for profile in store["profiles"]):
        raise BotLineupProfileError(
            "A Bot lineup profile with that name already exists.")
    store["profiles"].append({"name": name, "assignments": []})
    store["profiles"].sort(key=lambda item: item["name"].casefold())
    return store, name


def delete(store, raw_name):
    store = normalize_store(store)
    name = _name(raw_name)
    remaining = [
        profile for profile in store["profiles"]
        if profile["name"].casefold() != name.casefold()
    ]
    if len(remaining) == len(store["profiles"]):
        raise BotLineupProfileError(
            "That Bot lineup profile does not exist.")
    store["profiles"] = remaining
    return store


def assignments_for(store, raw_name):
    if not raw_name or raw_name == AUTOMATIC_PROFILE_LABEL:
        return []
    name = _name(raw_name)
    for profile in normalize_store(store)["profiles"]:
        if profile["name"].casefold() == name.casefold():
            return copy.deepcopy(profile["assignments"])
    raise BotLineupProfileError(
        "That Bot lineup profile does not exist.")


def set_assignment(store, raw_name, team, slot, vehicle=None, skill=None):
    store = normalize_store(store)
    name = _name(raw_name)
    pinned = _assignments([{
        "team": team, "slot": slot, "vehicle": vehicle, "skill": skill,
    }])
    if not pinned:
        return clear_assignment(store, raw_name, team, slot)
    assignment = pinned[0]
    for profile in store["profiles"]:
        if profile["name"].casefold() != name.casefold():
            continue
        values = [
            value for value in profile["assignments"]
            if (value["team"], value["slot"]) !=
            (assignment["team"], assignment["slot"])
        ]
        values.append(assignment)
        profile["assignments"] = _assignments(values)
        return store
    raise BotLineupProfileError(
        "That Bot lineup profile does not exist.")


def clear_assignment(store, raw_name, team, slot):
    store = normalize_store(store)
    name = _name(raw_name)
    for profile in store["profiles"]:
        if profile["name"].casefold() == name.casefold():
            profile["assignments"] = [
                value for value in profile["assignments"]
                if (value["team"], value["slot"]) != (int(team), int(slot))
            ]
            return store
    raise BotLineupProfileError(
        "That Bot lineup profile does not exist.")
