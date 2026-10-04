"""Bounded server-admitted mission evidence, shared by receipt readers.

Rows are [kind, combat_elapsed_ms, value, ...]. Damage carries HP and the
pre-hit immobilized state; kills carry reason, pre-hit immobilization and
distance; critical events carry the newly changed native critical mask.
Version 2 adds ram rows containing applied damage in both directions, victim
death, attacker survival and pre-hit immobilization from the same collision.
Version 3 freezes hit distance and view radius, visibility at damage/kill, ignition transitions
and whether a detection preceded the observer's first detection by the enemy.
Version 4 adds each allocated radio-assist share and the observer's visibility
at that instant. Total assistance and end-of-battle visibility cannot replace it.
Version 5 freezes the attacker's full-health state at a kill, after both halves
of a ram or all effects of the same shell have settled. None means unavailable.
The cap is per actor, across targets, keeping receipts below the wire budget.
An incomplete history stays explicitly unknown to the mission evaluator.
"""
import math

try:
    INTEGER_TYPES = (int, long)
except NameError:
    INTEGER_TYPES = (int,)

MAX_EVENTS = 1024
VERSION = 5
_HISTORY_FIELDS = frozenset(('mission_events', 'mission_events_complete'))
FIELDS = _HISTORY_FIELDS | frozenset(('mission_events_version',))


# #1513 device indices: engine, ammo bay, fuel tank, radio and turret drive
# are internal; tracks, gun and observation device are external. Crew occupies
# bits 24..28. A yellow device is damage, not a destroyed-module event.
INTERNAL_DEVICE_MASK = sum(1 << index for index in (0, 1, 2, 3, 6))
INTERNAL_DESTROYED_MASK = (INTERNAL_DEVICE_MASK << 12) | (31 << 24)
INTERNAL_CRITICAL_MASK = INTERNAL_DEVICE_MASK | INTERNAL_DESTROYED_MASK


def internal_destroyed_count(mask):
    return bin(int(mask) & INTERNAL_DESTROYED_MASK).count('1')


def internal_critical_count(mask):
    """Count internal devices affected by one hit, plus knocked-out crew.

    Damage and destruction of the same device in one transition are one
    affected device. Separate accepted transitions remain separate events.
    """
    mask = int(mask)
    devices = (mask | (mask >> 12)) & INTERNAL_DEVICE_MASK
    crew = mask & (31 << 24)
    return bin(devices).count('1') + bin(crew).count('1')


def _integer(value, minimum, maximum):
    return (type(value) in INTEGER_TYPES and minimum <= value <= maximum)


def _distance(value):
    return (value is None or
            not isinstance(value, bool) and
            isinstance(value, INTEGER_TYPES + (float,)) and
            0 <= value <= 100000 and not math.isnan(value) and
            not math.isinf(value))


def valid(row):
    if not FIELDS.intersection(row):
        return True
    events = row.get('mission_events')
    version = row.get('mission_events_version', 1)
    if (not _HISTORY_FIELDS.issubset(row) or
            not _integer(version, 1, VERSION) or
            not isinstance(row.get('mission_events_complete'), bool) or
            not isinstance(events, list) or len(events) > MAX_EVENTS):
        return False
    previous_time = -1
    for event in events:
        if (not isinstance(event, list) or len(event) < 3 or
                not _integer(event[1], 0, 86400000) or event[1] < previous_time):
            return False
        previous_time = event[1]
        kind = event[0]
        if kind == 'damage':
            if (len(event) != (7 if version >= 3 else 4) or
                    not _integer(event[2], 1, 65535) or
                    not isinstance(event[3], bool)):
                return False
        elif kind == 'kill':
            if (len(event) != (7 if version >= 5 else 6 if version >= 3 else 5) or
                    not _integer(event[2], 0, 10) or
                    not isinstance(event[3], bool)):
                return False
            if not _distance(event[4]):
                return False
            if version >= 5 and event[6] is not None and not isinstance(event[6], bool):
                return False
        elif kind == 'critical':
            if len(event) != 3 or not _integer(event[2], 1, 4294967295):
                return False
        elif kind == 'fire':
            if version < 3 or len(event) != 3 or not _integer(event[2], 1, 1):
                return False
        elif kind == 'spot':
            if version < 3 or len(event) != 3 or not isinstance(event[2], bool):
                return False
        elif kind == 'assist_radio':
            if (version < 4 or len(event) != 4 or
                    not _integer(event[2], 1, 100000) or
                    not isinstance(event[3], bool)):
                return False
        elif kind == 'ram':
            if (version < 2 or len(event) != 7 or
                    not _integer(event[2], 1, 65535) or
                    not _integer(event[3], 0, 65535) or
                    not all(isinstance(value, bool) for value in event[4:7])):
                return False
        else:
            return False
        if version >= 3 and kind in ('damage', 'kill'):
            if not _distance(event[4]) or not isinstance(event[5], bool):
                return False
            if kind == 'damage' and not _distance(event[6]):
                return False
    return True
