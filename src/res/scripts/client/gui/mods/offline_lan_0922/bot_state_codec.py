"""Compact positional wire codec for the periodic Bot publication.

The hidden worker publishes every Bot's complete canonical checkpoint several
times per second.  Encoding that batch as one JSON object per Bot spent most of
its bytes on repeated field names and on per-vehicle constants that never change
inside a round, and it forced the server to re-derive finiteness, ranges and
rounding for every field of every Bot on every publication.

This module owns the one wire representation used instead: a fixed-order list of
integers per Bot.  Every real-valued field is carried as a fixed-point integer,
so the encoded value *is* the rounded, clamped value the server used to compute
defensively.  A row therefore cannot express a non-finite number, a value out of
contract range, or a missing field, and the decoder does not check for any of
them.

Both peers import this module: the worker through the client script root inside
the game, the server through the same path on ``sys.path``.  It is the single
source of truth for the layout, and it must parse and run on CPython 2.7.

The immutable consumable contracts ride ``bot_manifest`` once per round instead
of every row, because they were the largest part of the old encoding and never
change; ``decode_row`` rejoins them. The critical ledger stays self-contained:
its device maxima and crew roster feed a publication signature that both peers
compare, so a row carries them rather than depending on a second message.
"""

import math


# Canonical orders. A row carries slot indices into these tuples, never names.
DEVICE_NAMES = (
    'ammoBayHealth', 'engineHealth', 'fuelTankHealth', 'gunHealth',
    'leftTrackHealth', 'radioHealth', 'rightTrackHealth',
    'surveyingDeviceHealth', 'turretRotatorHealth')
CREW_NAMES = (
    'commander', 'driver', 'gunner1', 'gunner2', 'loader1', 'loader2',
    'radioman1', 'radioman2')
DEVICE_STATES = ('normal', 'critical', 'destroyed')

MAX_SHELL_TYPES = 5
MAX_EQUIPMENT = 3

# Fixed-point scales. Each one matches the decimal place the server used to
# round that field to, so no precision is lost against the previous contract.
POSITION_SCALE = 10000        # round(v, 4)
ANGLE_SCALE = 100000          # round(v, 5)
SPEED_SCALE = 10000           # round(v, 4)
SECONDS_SCALE = 1000000       # reload, burst and consumable clocks
FIRE_CLOCK_SCALE = 1000000    # round(v, 6)
DEVICE_HP_SCALE = 1000        # round(v, 3)

# Flag bits.
F_ALIVE = 1 << 0
F_WORLD_POSE = 1 << 1
F_AMMO_RELOAD_PENDING = 1 << 2
F_BURST_ACTIVE = 1 << 3
F_FIRE = 1 << 4
F_AMMO_RACK_DEATH = 1 << 5
F_HAS_SHOT_ANGLES = 1 << 6
F_HAS_CRITICAL = 1 << 7
F_HAS_EQUIPMENT = 1 << 8
F_HAS_AMMO = 1 << 9
F_MOVING_FORWARD = 1 << 10
F_MOVING_BACKWARD = 1 << 11
F_TURNING_LEFT = 1 << 12
F_TURNING_RIGHT = 1 << 13
F_HAS_BURST = 1 << 14
F_HAS_CLIP = 1 << 15
F_HAS_SIEGE = 1 << 16

# Groups the previous mapping contract could leave out entirely, which meant
# "keep the state the server already admitted". A positional row always has the
# columns, so a presence bit carries that distinction instead.
OPTIONAL_GROUPS = (
    (F_HAS_AMMO, ('ammo_remaining', 'next_shell_index',
                  'ammo_reload_pending')),
    (F_HAS_BURST, ('burst_active', 'burst_group_seq', 'burst_count',
                   'burst_next_index', 'burst_shell_index',
                   'burst_interval', 'burst_time_left')),
    (F_HAS_CLIP, ('clip', 'clip_size')),
    (F_HAS_SIEGE, ('siege_state', 'siege_time_left_ms',
                   'siege_transition_total_ms')),
)

# Scalar columns, in row order, as (name, scale). ``scale`` None means the
# field is already an exact integer.
SCALARS = (
    ('id', None),
    ('_flags', None),
    ('x', POSITION_SCALE),
    ('y', POSITION_SCALE),
    ('z', POSITION_SCALE),
    ('yaw', ANGLE_SCALE),
    ('pitch', ANGLE_SCALE),
    ('roll', ANGLE_SCALE),
    ('aim_yaw', ANGLE_SCALE),
    ('gun_pitch', ANGLE_SCALE),
    ('speed', SPEED_SCALE),
    ('fire_seq', None),
    ('shell_index', None),
    ('next_shell_index', None),
    ('clip', None),
    ('clip_size', None),
    ('reload_time', SECONDS_SCALE),
    ('reload_duration', SECONDS_SCALE),
    ('burst_group_seq', None),
    ('burst_count', None),
    ('burst_next_index', None),
    ('burst_shell_index', None),
    ('burst_interval', SECONDS_SCALE),
    ('burst_time_left', SECONDS_SCALE),
    ('siege_state', None),
    ('siege_time_left_ms', None),
    ('siege_transition_total_ms', None),
    ('health', None),
    ('display_health', None),
    ('combat_base_revision', None),
    ('combat_seq', None),
    ('combat_fire_elapsed', FIRE_CLOCK_SCALE),
    ('combat_fire_timer', FIRE_CLOCK_SCALE),
    ('death_reason', None),
    ('stun_end_server_time_ms', None),
    ('shot_yaw', ANGLE_SCALE),
    ('shot_pitch', ANGLE_SCALE),
)
SCALAR_COUNT = len(SCALARS)
_SCALAR_INDEX = dict(
    (name, index) for index, (name, unused) in enumerate(SCALARS))

# Clamps the previous contract applied before rounding. The encoder applies
# them so an out-of-range value is impossible on the wire instead of rejected
# after it arrives.
CLAMPS = {
    'x': (-2000.0, 2000.0),
    'y': (-1000.0, 1000.0),
    'z': (-2000.0, 2000.0),
    'pitch': (-0.61, 0.61),
    'roll': (-0.61, 0.61),
    'gun_pitch': (-1.2, 1.2),
    'speed': (-80.0, 80.0),
    'shot_pitch': (-1.2, 1.2),
}

MISSING = -1
_INFINITY = float('inf')

# The coarsest quantum any second-valued column is carried at.
SECONDS_QUANTUM = 1.0 / SECONDS_SCALE


class BotStateCodecError(ValueError):
    """One row could not be encoded or decoded against this layout."""


def _fixed(value, scale, bounds=None):
    """Round half away from zero so 2.7 and 3.x encode the same integer."""
    number = float(value)
    if number != number or number in (_INFINITY, -_INFINITY):
        raise BotStateCodecError('bot state column is not finite')
    if bounds is not None:
        number = max(bounds[0], min(number, bounds[1]))
    scaled = number * scale
    if scaled >= 0.0:
        return int(math.floor(scaled + 0.5))
    return -int(math.floor(-scaled + 0.5))


def _real(units, scale):
    return float(units) / float(scale)


def _wrapped_angle(value):
    """Normalise one shot angle exactly as the previous server contract did."""
    return ((float(value) + math.pi) % (2.0 * math.pi)) - math.pi


def _exact(value):
    result = int(value)
    if isinstance(value, bool):
        raise BotStateCodecError('boolean is not an integer column')
    return result


def _crew_mask(names):
    mask = 0
    for name in names or ():
        name = str(name)
        if name not in CREW_NAMES:
            raise BotStateCodecError('unknown crew member')
        mask |= 1 << CREW_NAMES.index(name)
    return mask


def _crew_names(mask):
    if mask < 0 or mask >> len(CREW_NAMES):
        raise BotStateCodecError('crew mask is out of range')
    return [name for index, name in enumerate(CREW_NAMES)
            if mask & (1 << index)]


def encode_row(state):
    """Encode one worker-owned Bot state as a positional integer row."""
    if not isinstance(state, dict):
        raise BotStateCodecError('bot state must be a mapping')
    critical = state.get('critical')
    equipment = state.get('equipment_states')
    ammo = state.get('ammo_remaining')
    has_yaw = 'shot_yaw' in state
    if has_yaw != ('shot_pitch' in state):
        raise BotStateCodecError('bot shot angles must be an atomic pair')
    has_shot = has_yaw
    movement = float(state.get('movement_dir', 0.0) or 0.0)
    rotation = float(state.get('rotation_dir', 0.0) or 0.0)
    flags = 0
    if state.get('alive', True):
        flags |= F_ALIVE
    if state.get('world_pose', True):
        flags |= F_WORLD_POSE
    if state.get('ammo_reload_pending', False):
        flags |= F_AMMO_RELOAD_PENDING
    if state.get('burst_active', False):
        flags |= F_BURST_ACTIVE
    if isinstance(critical, dict):
        flags |= F_HAS_CRITICAL
        if critical.get('fire', False):
            flags |= F_FIRE
        if critical.get('ammo_rack_death', False):
            flags |= F_AMMO_RACK_DEATH
    if equipment is not None:
        flags |= F_HAS_EQUIPMENT
    if has_shot:
        flags |= F_HAS_SHOT_ANGLES
    for bit, names in OPTIONAL_GROUPS:
        present = [name in state for name in names]
        if all(present):
            flags |= bit
        elif any(present):
            raise BotStateCodecError('bot state group is incomplete')
    if movement > 0.01:
        flags |= F_MOVING_FORWARD
    elif movement < -0.01:
        flags |= F_MOVING_BACKWARD
    if rotation > 0.01:
        flags |= F_TURNING_LEFT
    elif rotation < -0.01:
        flags |= F_TURNING_RIGHT

    row = []
    for name, scale in SCALARS:
        if name == '_flags':
            row.append(flags)
            continue
        if name in ('shot_yaw', 'shot_pitch'):
            if not has_shot:
                row.append(0)
                continue
            raw = state[name]
            if name == 'shot_yaw':
                raw = _wrapped_angle(raw)
            row.append(_fixed(raw, scale, CLAMPS.get(name)))
            continue
        value = state.get(name, 0)
        if scale is None:
            row.append(_exact(value))
        else:
            row.append(_fixed(value, scale, CLAMPS.get(name)))

    if flags & F_HAS_AMMO:
        shells = list(ammo)
        if len(shells) > MAX_SHELL_TYPES:
            raise BotStateCodecError('bot carries too many shell types')
        row.append(len(shells))
        for count in shells:
            row.append(_exact(count))

    if isinstance(critical, dict):
        records = []
        for record in critical.get('devices') or ():
            name = str(record.get('name', ''))
            if name not in DEVICE_NAMES:
                raise BotStateCodecError('unknown critical device')
            state_name = str(record.get('state', 'normal'))
            if state_name not in DEVICE_STATES:
                raise BotStateCodecError('unknown critical device state')
            records.append((
                DEVICE_NAMES.index(name),
                _fixed(record.get('hp', 0.0), DEVICE_HP_SCALE),
                _fixed(record.get('max_hp', 1.0), DEVICE_HP_SCALE),
                DEVICE_STATES.index(state_name)))
        records.sort()
        row.append(len(records))
        for slot, hp, maximum, device_state in records:
            row.append(slot)
            row.append(hp)
            row.append(maximum)
            row.append(device_state)
        row.append(_crew_mask(critical.get('crew_ko')))
        roster = critical.get('crew_roster')
        row.append(-1 if roster is None else _crew_mask(roster))

    if equipment is not None:
        snapshots = list(equipment)
        if len(snapshots) > MAX_EQUIPMENT:
            raise BotStateCodecError('bot carries too many consumables')
        row.append(len(snapshots))
        for snapshot in snapshots:
            row.append(_exact(snapshot.get('usesLeft', 0)))
            row.append(_fixed(
                snapshot.get('cooldownTimeLeft', 0.0), SECONDS_SCALE))
            row.append(1 if snapshot.get('active', False) else 0)
            for field in ('autoPendingElapsed', 'aiPendingElapsed'):
                elapsed = snapshot.get(field)
                row.append(MISSING if elapsed is None else
                           _fixed(elapsed, SECONDS_SCALE))
    return row


class _Cursor(object):
    """Read one row strictly in layout order and prove it was consumed."""

    __slots__ = ('_row', '_index')

    def __init__(self, row):
        self._row = row
        self._index = SCALAR_COUNT

    def take(self):
        if self._index >= len(self._row):
            raise BotStateCodecError('bot state row is truncated')
        value = self._row[self._index]
        self._index += 1
        return value

    def finish(self):
        if self._index != len(self._row):
            raise BotStateCodecError('bot state row has trailing columns')


def decode_row(row, static):
    """Rebuild one canonical Bot mapping from a row plus its round constants."""
    if not isinstance(row, (list, tuple)) or len(row) < SCALAR_COUNT:
        raise BotStateCodecError('bot state row is truncated')
    for column in row:
        if isinstance(column, bool) or not isinstance(column, int):
            raise BotStateCodecError('bot state row must be integers')

    result = {}
    for index, (name, scale) in enumerate(SCALARS):
        if name == '_flags':
            continue
        if scale is None:
            result[name] = row[index]
        else:
            result[name] = _real(row[index], scale)
    flags = row[_SCALAR_INDEX['_flags']]
    result['alive'] = bool(flags & F_ALIVE)
    result['world_pose'] = bool(flags & F_WORLD_POSE)
    result['ammo_reload_pending'] = bool(flags & F_AMMO_RELOAD_PENDING)
    result['burst_active'] = bool(flags & F_BURST_ACTIVE)
    result['movement_dir'] = (
        1 if flags & F_MOVING_FORWARD else
        -1 if flags & F_MOVING_BACKWARD else 0)
    result['rotation_dir'] = (
        1 if flags & F_TURNING_LEFT else
        -1 if flags & F_TURNING_RIGHT else 0)
    if not flags & F_HAS_SHOT_ANGLES:
        del result['shot_yaw']
        del result['shot_pitch']
    for bit, names in OPTIONAL_GROUPS:
        if not flags & bit:
            for name in names:
                result.pop(name, None)

    cursor = _Cursor(row)
    if flags & F_HAS_AMMO:
        count = cursor.take()
        if not 0 <= count <= MAX_SHELL_TYPES:
            raise BotStateCodecError('bot shell type count is out of range')
        result['ammo_remaining'] = [cursor.take() for unused in range(count)]

    if flags & F_HAS_CRITICAL:
        count = cursor.take()
        if not 0 <= count <= len(DEVICE_NAMES):
            raise BotStateCodecError('bot device count is out of range')
        devices = []
        destroyed = []
        for unused in range(count):
            slot = cursor.take()
            hp = cursor.take()
            maximum = cursor.take()
            device_state = cursor.take()
            if (not 0 <= slot < len(DEVICE_NAMES) or
                    not 0 <= device_state < len(DEVICE_STATES)):
                raise BotStateCodecError('bot device row is out of range')
            name = DEVICE_NAMES[slot]
            state_name = DEVICE_STATES[device_state]
            devices.append({
                'name': name,
                'hp': _real(hp, DEVICE_HP_SCALE),
                'max_hp': _real(maximum, DEVICE_HP_SCALE),
                'state': state_name,
            })
            if state_name == 'destroyed':
                destroyed.append(name)
        critical = {
            'devices': devices,
            'destroyed': destroyed,
            'crew_ko': _crew_names(cursor.take()),
            'fire': bool(flags & F_FIRE),
            'ammo_rack_death': bool(flags & F_AMMO_RACK_DEATH),
            'events': [],
        }
        roster_mask = cursor.take()
        if roster_mask >= 0:
            critical['crew_roster'] = _crew_names(roster_mask)
        result['critical'] = critical

    if flags & F_HAS_EQUIPMENT:
        contracts = (static or {}).get('equipment_contracts') or ()
        count = cursor.take()
        if not 0 <= count <= MAX_EQUIPMENT:
            raise BotStateCodecError('bot consumable count is out of range')
        if count and count != len(contracts):
            raise BotStateCodecError(
                'bot consumable count does not match the round manifest')
        snapshots = []
        for index in range(count):
            uses_left = cursor.take()
            cooldown = cursor.take()
            active = cursor.take()
            pending = []
            for unused in range(2):
                elapsed = cursor.take()
                pending.append(
                    None if elapsed == MISSING else
                    _real(elapsed, SECONDS_SCALE))
            snapshots.append({
                'equipment': contracts[index],
                'usesLeft': uses_left,
                'cooldownTimeLeft': _real(cooldown, SECONDS_SCALE),
                'active': bool(active),
                'autoPendingElapsed': pending[0],
                'aiPendingElapsed': pending[1],
            })
        result['equipment_states'] = snapshots
    cursor.finish()
    return result
