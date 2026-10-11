"""Descriptor-driven 9.22 stun calculation and shared stat multipliers.

Only a shell carrying the native Stun component can stun. The worker freezes
that component at launch and uses the installed items.stun.g_cfg at impact.
No shell calibre, vehicle name or current-version balance table is guessed.
"""
from __future__ import division

import math

try:
    _NUMBER_TYPES = (int, long, float)
except NameError:
    _NUMBER_TYPES = (int, float)

SHELL_FIELDS = (
    'stunRadius', 'stunDuration', 'stunFactor', 'guaranteedStunDuration',
    'damageDurationCoeff', 'guaranteedStunEffect', 'damageEffectCoeff')
STAT_CONFIG = {
    'mobility': 'stunFactorEnginePower',
    'traverse': 'stunFactorVehicleRotationSpeed',
    'turret_speed': 'stunFactorTurretTraverse',
    'vision': 'stunFactorViewDistance',
    'speed': 'stunFactorMaxSpeed',
    'reload': 'stunFactorReloadTime',
    'aim_time': 'stunFactorAimingTime',
    'bloom_move': 'stunFactorVehicleMovementShotDispersion',
    'bloom_rotation': 'stunFactorVehicleRotationShotDispersion',
    'bloom_turret': 'stunFactorTurretRotationShotDispersion',
    'dispersion': 'stunFactorMinShotDispersion',
}
INCREASING_STATS = frozenset((
    'reload', 'aim_time', 'bloom_move', 'bloom_rotation', 'bloom_turret',
    'dispersion'))
RESISTANCE_FIELDS = ('stunResistanceDuration', 'stunResistanceEffect')


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(
        value, name, default)


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, _NUMBER_TYPES):
        raise ValueError('invalid stun number')
    value = float(value)
    if math.isnan(value) or math.isinf(value) or not low <= value <= high:
        raise ValueError('invalid stun number')
    return value


def shell_component(value):
    if value is None:
        return None
    if isinstance(value, dict) and set(value) != set(SHELL_FIELDS):
        raise ValueError('invalid stun shell fields')
    return dict((name, round(_number(
        _field(value, name), 0.0,
        100.0 if name == 'stunRadius' else
        900.0 if name == 'stunDuration' else 1.0), 6))
        for name in SHELL_FIELDS)


def canonical_factors(value):
    if not isinstance(value, dict) or set(value) != set(STAT_CONFIG):
        raise ValueError('invalid stun factor fields')
    return dict((name, round(_number(
        value[name], 1.0 if name in INCREASING_STATS else 0.0,
        100.0 if name in INCREASING_STATS else 1.0), 6))
        for name in STAT_CONFIG)


def resistance(descriptor, equipments):
    misc = _field(descriptor, 'miscAttrs', {}) or {}
    result = dict((name, float(_field(misc, name, 0.0)))
                  for name in RESISTANCE_FIELDS)
    for raw in equipments or ():
        contract = _field(raw, 'contract', None)
        if contract is None:
            contract = _field(raw, 'equipment', raw)
        uses = _field(raw, 'uses_left', _field(raw, 'usesLeft', -1))
        if uses == 0:
            continue
        for name in RESISTANCE_FIELDS:
            result[name] += float(_field(contract, name, 0.0))
    return dict((name, max(0.0, min(1.0, value)))
                for name, value in result.items())


def impact(shell, damage, distance, config, resistances=None):
    component = shell_component(_field(shell, 'stun'))
    if component is None or _field(shell, 'kind') != 'HIGH_EXPLOSIVE':
        return None
    if distance < 0.0 or distance > component['stunRadius']:
        return None
    nominal = float(_field(shell, 'damage')[0])
    if nominal <= 0.0:
        return None
    resistances = resistances or {}
    fraction = max(0.0, min(1.0, float(damage) / nominal))
    duration = component['stunDuration'] * (
        component['guaranteedStunDuration'] +
        component['damageDurationCoeff'] * fraction)
    duration *= 1.0 - resistances.get('stunResistanceDuration', 0.0)
    minimum = _number(config['minStunDuration'], 0.0, 900.0)
    if duration <= 0.0 or duration < minimum:
        return None
    strength = component['stunFactor'] * (
        component['guaranteedStunEffect'] +
        component['damageEffectCoeff'] * fraction)
    strength *= 1.0 - resistances.get('stunResistanceEffect', 0.0)
    strength = max(0.0, min(1.0, strength))
    factors = canonical_factors(dict((stat, 1.0 + (
        _number(config[key], 0.0, 100.0) - 1.0) * strength)
        for stat, key in STAT_CONFIG.items()))
    return duration, factors


def factor(state, stat):
    """A cleared stun never applies a leftover factor from an old snapshot."""
    if not state or not state.get('stun_end_server_time_ms', 0):
        return 1.0
    return (state.get('stun_factors') or {}).get(stat, 1.0)
