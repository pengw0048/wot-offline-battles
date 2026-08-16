"""Cover scoring, taken from the 0.9.22 port.

The law is unchanged. Version differences belong in the
adapters in this package, never in this file.

Contract, from the original module:
Pure-data cover candidate normalization and scoring.

The module intentionally has no BigWorld or map import.  A map sampler may
produce candidates on either the client or server, then use this common JSON
contract to rank them.  Values are normalized to [0, 1] except for travel
distance and slope, which keep their useful physical units.
"""
from __future__ import absolute_import
# -*- coding: utf-8 -*-


DEFAULT_WEIGHTS = {
    'travel_distance': -0.055,
    'route_alignment': 22.0,
    'enemy_occlusion': 32.0,
    'exposure': -28.0,
    'slope': -1.4,
    'water': -55.0,
    'ally_congestion': -18.0,
    'peek_feasible': 9.0,
    'escape_feasible': 12.0,
}

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    # Avoid math.isfinite: this code is also loaded by Python 2.6.
    if value != value or value == float('inf') or value == float('-inf'):
        return float(default)
    return value


def _clamp(value, low, high):
    return max(low, min(high, value))


def _fraction(value, default=0.0):
    return _clamp(_number(value, default), 0.0, 1.0)


def _boolean(value):
    """Accept JSON booleans and numeric zero/one, never truthy strings."""
    return (value is True or value == 1) and not isinstance(value, _STRING_TYPES)


def _position(raw):
    if isinstance(raw, (tuple, list)):
        if len(raw) < 3:
            return None
        values = (raw[0], raw[1], raw[2])
    elif isinstance(raw, dict):
        if not all(key in raw for key in ('x', 'y', 'z')):
            return None
        values = (raw.get('x'), raw.get('y'), raw.get('z'))
    else:
        return None
    result = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number == float('inf') or number == float('-inf'):
            return None
        result.append(round(number, 3))
    return {'x': result[0], 'y': result[1], 'z': result[2]}


def _candidate_id(value):
    try:
        text = value if isinstance(value, _STRING_TYPES) else str(value)
    except Exception:
        return ''
    # Protocol ids are deliberately ASCII so Python 2 never depends on the
    # process default encoding while serializing them.
    return ''.join(character for character in text if ord(character) < 128)[:80]


def normalize_candidate(raw):
    """Return one safe JSON candidate without scoring it.

    Expected fields: ``id``, ``position``, ``travel_distance``,
    ``route_alignment``, ``enemy_occlusion``, ``exposure``, ``slope``,
    ``water``, ``ally_congestion``, ``peek_feasible`` and ``escape_feasible``.
    ``enemy_occlusion`` expresses how much enemy line of sight is blocked;
    callers must derive it only from observed/known enemy positions.
    """
    raw = raw if isinstance(raw, dict) else {}
    candidate_id = _candidate_id(raw.get('id') or '')
    position = _position(raw.get('position'))
    peek_position = _position(raw.get('peek_position'))
    result = {
        'id': candidate_id,
        'position': position,
        'travel_distance': max(0.0, round(_number(raw.get('travel_distance')), 3)),
        'route_alignment': round(_fraction(raw.get('route_alignment')), 4),
        'enemy_occlusion': round(_fraction(raw.get('enemy_occlusion')), 4),
        'exposure': round(_fraction(raw.get('exposure'), 1.0), 4),
        'slope': max(0.0, round(_number(raw.get('slope')), 3)),
        'water': round(_fraction(raw.get('water')), 4),
        'ally_congestion': round(_fraction(raw.get('ally_congestion')), 4),
        'peek_feasible': _boolean(raw.get('peek_feasible', False)) and peek_position is not None,
        'escape_feasible': _boolean(raw.get('escape_feasible', False)),
    }
    if peek_position is not None:
        result['peek_position'] = peek_position
    return result


def _weights(overrides):
    result = dict(DEFAULT_WEIGHTS)
    if isinstance(overrides, dict):
        for key in result:
            if key in overrides:
                result[key] = _number(overrides[key], result[key])
    return result


def score_candidate(raw, weights=None):
    """Score one candidate and include an explainable contribution breakdown."""
    candidate = normalize_candidate(raw)
    values = candidate
    use_weights = _weights(weights)
    breakdown = {
        'travel_distance': round(values['travel_distance'] * use_weights['travel_distance'], 3),
        'route_alignment': round(values['route_alignment'] * use_weights['route_alignment'], 3),
        'enemy_occlusion': round(values['enemy_occlusion'] * use_weights['enemy_occlusion'], 3),
        'exposure': round(values['exposure'] * use_weights['exposure'], 3),
        'slope': round(values['slope'] * use_weights['slope'], 3),
        'water': round(values['water'] * use_weights['water'], 3),
        'ally_congestion': round(values['ally_congestion'] * use_weights['ally_congestion'], 3),
        'peek_feasible': use_weights['peek_feasible'] if values['peek_feasible'] else 0.0,
        'escape_feasible': use_weights['escape_feasible'] if values['escape_feasible'] else 0.0,
    }
    score = round(sum(breakdown.values()), 3)
    reasons = []
    if values['water'] >= 0.5:
        reasons.append('water_risk')
    if values['slope'] > 18.0:
        reasons.append('steep_slope')
    if values['enemy_occlusion'] < 0.25:
        reasons.append('low_occlusion')
    if values['exposure'] > 0.75:
        reasons.append('high_exposure')
    if not values['escape_feasible']:
        reasons.append('no_escape')
    result = dict(candidate)
    result['score'] = score
    result['breakdown'] = breakdown
    result['reasons'] = reasons
    return result


def score_candidates(candidates, weights=None):
    """Score candidates with a deterministic ordering suitable for replication."""
    scored = [score_candidate(raw, weights) for raw in (candidates or [])]
    scored.sort(key=lambda item: (-item['score'], item['travel_distance'], item['id']))
    for index, item in enumerate(scored):
        item['rank'] = index + 1
    return scored
