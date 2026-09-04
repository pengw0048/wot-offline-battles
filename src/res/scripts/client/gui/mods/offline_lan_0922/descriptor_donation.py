from __future__ import print_function

"""Freeze only the mounted shot law and lobby vehicle tiers for LAN."""

from gui.mods.offline_lan_0922 import vehicle_blacklist
from gui.mods.offline_lan_0922 import vehicle_configuration


def _value(source, name, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _json_safe(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value != value or abs(value) == float("inf"):
            return None
        return value
    if isinstance(value, str):
        return value
    try:
        text_types = (unicode,)
    except NameError:
        text_types = ()
    if isinstance(value, text_types):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return dict((str(key), _json_safe(item, depth + 1))
                    for key, item in value.items())
    if value is None or isinstance(value, bool):
        return value
    try:
        return [_json_safe(item, depth + 1) for item in list(value)]
    except Exception:
        return None


def _copy_fields(source, names):
    result = {}
    for name in names:
        value = _value(source, name)
        if value is not None:
            safe = _json_safe(value)
            if safe is not None:
                result[name] = safe
    return result


_SHOT_FIELDS = ("speed", "gravity", "maxDistance", "piercingPower")
_HE_FACTOR_DEFAULTS = (
    ("explosionDamageFactor", 0.5),
    ("explosionDamageAbsorptionFactor", 1.3),
    ("explosionEdgeDamageFactor", 0.15),
)
_PROJECTILE_SHELL_FIELDS = (
    "kind", "caliber", "damage", "explosionRadius",
    "explosionDamageFactor", "explosionDamageAbsorptionFactor",
    "explosionEdgeDamageFactor",
)


def _complete_shell_projection(shell, names, default_radius=False):
    projection = _copy_fields(shell, names)
    shell_type = _value(shell, "type")
    if "kind" not in projection:
        kind = _value(shell_type, "name")
        if kind:
            projection["kind"] = str(kind)
    if "explosionRadius" not in projection:
        radius = _json_safe(_value(shell_type, "explosionRadius"))
        if radius is not None:
            projection["explosionRadius"] = radius
        elif default_radius:
            projection["explosionRadius"] = 0.0
    if projection.get("kind") == "HIGH_EXPLOSIVE":
        for name, default in _HE_FACTOR_DEFAULTS:
            value = _json_safe(_value(shell, name))
            if value is None:
                value = _json_safe(_value(shell_type, name))
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                value = default
            if (value != value or abs(value) == float('inf') or
                    value <= 0.0 or
                    (name == "explosionEdgeDamageFactor" and value > 1.0)):
                value = default
            projection[name] = value
    return projection


def project_shot(shot, deadeye=False):
    """Freeze one mounted gun shot for the worker projectile ledger."""
    projection = _copy_fields(shot, _SHOT_FIELDS)
    projection["deadeye"] = bool(deadeye)
    projection["shell"] = _complete_shell_projection(
        _value(shot, "shell", {}), _PROJECTILE_SHELL_FIELDS,
        default_radius=True)
    return projection


def vehicle_catalog(runtime):
    """Return eligible vehicle tiers for the waiting-room roster."""
    rows = []
    nations = runtime.nations
    vehicle_list = runtime.vehicles.g_list
    for nation in nations.AVAILABLE_NAMES:
        nation_id = nations.INDICES[nation]
        values = vehicle_list.getList(nation_id)
        iterator = getattr(values, "itervalues", None)
        entries = iterator() if callable(iterator) else values.values()
        for entry in entries:
            name = str(_value(entry, "name", "") or "")
            if (not name or vehicle_blacklist.is_unusable(name) or
                    not vehicle_configuration.is_standard_battle_vehicle(
                        entry)):
                continue
            try:
                level = int(_value(entry, "level", 1) or 1)
            except (TypeError, ValueError):
                continue
            rows.append({"name": name, "level": level,
                         "tags": sorted(str(tag) for tag in
                                        (_value(entry, "tags", ()) or ()))})
    rows.sort(key=lambda row: row["name"])
    return rows
