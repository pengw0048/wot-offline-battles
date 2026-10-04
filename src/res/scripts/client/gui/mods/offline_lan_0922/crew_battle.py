"""Conditional crew effects donated by the installed 0.9.22 client.

The native crew processor resolves training, vehicle eligibility, bonuses and
casualties. Keep its results alongside each frozen crew/fire state rather than
reconstructing skill levels in the worker or hard-coding a second skill table.
"""

import math


DEFAULTS = {
    'adrenaline_health_fraction': 0.0,
    'adrenaline_reload_factor': 1.0,
    'engine_fire_factor': 1.0,
    'damaged_gun_factor': 1.0,
}


def from_native(factors):
    return dict((name, float(factors.get('offline/' + name, default)))
                for name, default in DEFAULTS.items())


def canonical(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        return None
    result = {}
    for name in DEFAULTS:
        number = value[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return None
        number = float(number)
        if math.isnan(number) or math.isinf(number) or not 0.0 <= number <= 1.0:
            return None
        if name in ('adrenaline_reload_factor', 'damaged_gun_factor') and not number:
            return None
        result[name] = number
    return result


def for_critical(snapshot, critical):
    """Select the current physical crew's immutable, native-derived row."""
    dynamic = ((snapshot or {}).get('crew') or {}).get('dynamic_spotting') or {}
    critical = critical or {}
    knocked_out = set(critical.get('crew_ko') or ())
    mask = sum(1 << index for index, name in enumerate(dynamic.get('crew') or ())
               if name in knocked_out)
    row = (dynamic.get('states') or {}).get(
        '%d:%d' % (mask, int(bool(critical.get('fire', False))))) or {}
    return row.get('battle_factors') or DEFAULTS


def critical_from_vehicle(vehicle):
    return {'crew_ko': tuple(getattr(vehicle, '_crew_ko', ()) or ()),
            'fire': bool(getattr(vehicle, 'is_on_fire', False))}


def stat_multiplier(factors, stat, health, maximum, module_factor=1.0):
    if stat == 'reload' and 0 < health < maximum * factors['adrenaline_health_fraction']:
        return factors['adrenaline_reload_factor']
    if stat == 'dispersion' and module_factor > 1.0:
        # Armorer reduces the damaged gun's dispersion, without improving a
        # healthy gun or removing the separate injured-gunner penalty.
        return max(1.0, module_factor * factors['damaged_gun_factor']) / module_factor
    return 1.0
