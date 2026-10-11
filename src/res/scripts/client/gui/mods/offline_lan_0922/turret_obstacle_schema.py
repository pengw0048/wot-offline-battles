"""Bounded plain-data contract for worker-resolved detached turrets."""

import math

from gui.mods.offline_lan_0922 import turret_detachment
from gui.mods.offline_lan_0922 import turret_contact_ledger


MAX_ACTIVE_TURRETS = 12
MAX_SEGMENTS = turret_detachment.MAX_WALL_DEFLECTIONS + 1
MAX_FLIGHT_SECONDS = turret_detachment.MAX_FLIGHT_SECONDS
MAX_ACTOR_ID = 2147483647
# Match the room's projectile position/velocity envelope. These are wire
# safety bounds, not a second implementation of the native flight solver.
MAX_POSITION = 5000.0
MAX_VELOCITY = 3000.0
MAX_ANGULAR_COMPONENT = 1000.0
MAX_ENERGY = 1.5 * MAX_VELOCITY * MAX_VELOCITY

try:
    _number_types = (int, long, float)
except NameError:
    _number_types = (int, float)


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, _number_types):
        raise ValueError('expected a number')
    result = float(value)
    if math.isnan(result) or math.isinf(result) or not low <= result <= high:
        raise ValueError('number outside the wire envelope')
    return result


def _integer(value, low, high):
    number = _number(value, low, high)
    result = int(number)
    if result != number:
        raise ValueError('expected an integer')
    return result


def _vector(value, bound):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('expected a three-component vector')
    return [_number(part, -bound, bound) for part in value]


def _flight(value):
    if not isinstance(value, dict):
        raise ValueError('expected a flight record')
    segments = value['segments']
    if (not isinstance(segments, (list, tuple)) or
            not 1 <= len(segments) <= MAX_SEGMENTS):
        raise ValueError('invalid flight segments')
    parts = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('expected a segment')
        start = _number(segment['start'], 0.0, MAX_FLIGHT_SECONDS)
        duration = _number(segment['duration'], 0.0, MAX_FLIGHT_SECONDS)
        if start + duration > MAX_FLIGHT_SECONDS + 1e-9:
            raise ValueError('segment exceeds flight window')
        parts.append({
            'origin': _vector(segment['origin'], MAX_POSITION),
            'velocity': _vector(segment['velocity'], MAX_VELOCITY),
            'start': start, 'duration': duration,
        })
    landed = value['landed']
    contact = value['contact']
    if (not isinstance(landed, bool) or
            landed != (contact is not None)):
        raise ValueError('invalid landing outcome')
    result = {
        'origin': _vector(value['origin'], MAX_POSITION),
        'velocity': _vector(value['velocity'], MAX_VELOCITY),
        'segments': parts,
        'duration': _number(value['duration'], 0.0, MAX_FLIGHT_SECONDS),
        'contact': (_vector(contact, MAX_POSITION)
                    if contact is not None else None),
        'rest': _vector(value['rest'], MAX_POSITION),
        'impact_velocity': _vector(value['impact_velocity'], MAX_VELOCITY),
        'energy': _number(value['energy'], 0.0, MAX_ENERGY),
        'landed': landed,
    }
    if 'rest_attitude' in value:
        result['rest_attitude'] = _vector(value['rest_attitude'], MAX_ANGULAR_COMPONENT)
    if 'body' in value:
        body = value['body']
        if not isinstance(body, dict):
            raise ValueError('invalid detached rigid body')
        if not all(isinstance(body.get(k), bool) for k in ('grounded', 'sleeping')):
            raise ValueError('invalid detached contact state')
        frame = dict((k, _vector(body[k], bound)) for k, bound in (
            ('position', MAX_POSITION), ('attitude', MAX_ANGULAR_COMPONENT),
            ('centre', MAX_POSITION), ('velocity', MAX_VELOCITY),
            ('angular_velocity', MAX_ANGULAR_COMPONENT)))
        frame.update(grounded=body['grounded'], sleeping=body['sleeping'],
                     impact_serial=_integer(body['impact_serial'], 0, MAX_ACTOR_ID),
                     acks=list(turret_contact_ledger.normalize(body['acks']).values()), impact=None)
        if body.get('impact') is not None:
            impact = body['impact']
            frame['impact'] = {
                'point': _vector(impact['point'], MAX_POSITION),
                'normal': _vector(impact['normal'], 1.0),
                'velocity': _vector(impact['velocity'], MAX_VELOCITY),
                'energy': _number(impact['energy'], 0.0, MAX_ENERGY),
            }
        result['body'] = frame
    return result


def normalize_proposal(value):
    """Return an owned wire row, or None for one invalid proposal."""
    if not isinstance(value, dict):
        return None
    try:
        kind = value['actor_kind']
        if kind not in ('bot', 'player'):
            return None
        result = {
            'actor_kind': kind,
            'actor_id': _integer(value['actor_id'], 1, MAX_ACTOR_ID),
            'flight': _flight(value['flight']),
            'attitude': _vector(value['attitude'], MAX_ANGULAR_COMPONENT),
            'spin': _vector(value['spin'], MAX_ANGULAR_COMPONENT),
        }
        if 'motion_seq' in value:
            result['motion_seq'] = _integer(value['motion_seq'], 1, MAX_ACTOR_ID)
            result['motion_time_ms'] = _integer(value['motion_time_ms'], 0, MAX_ACTOR_ID)
            support = value.get('support_key')
            if support is not None:
                if not isinstance(support, type('')) and not isinstance(support, type(u'')):
                    return None
                parts = support.split(':')
                if len(parts) != 2 or parts[0] not in ('bot', 'player'):
                    return None
                if not 1 <= int(parts[1]) <= MAX_ACTOR_ID:
                    return None
            result['support_key'] = support
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def normalize_record(value):
    """Accept only an echoed row with the server-owned launch clock."""
    result = normalize_proposal(value)
    if result is None:
        return None
    try:
        result['created_time_ms'] = _integer(
            value['created_time_ms'], 0, MAX_ACTOR_ID)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return result


def row_key(value):
    """Return the round-local identity of an already normalized row."""
    return '%s:%d' % (value['actor_kind'], value['actor_id'])
