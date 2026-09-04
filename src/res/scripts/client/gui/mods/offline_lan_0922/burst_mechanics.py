from __future__ import print_function

"""Physical multi-projectile burst clocks for the pinned #1513 gun law.

``gun.burst`` is already decoded by the client as ``(count, interval)``.
Every element in the group is a real shell: it has its own ammunition debit,
dispersion sample, projectile ledger entry and terminal.  ``shot_seq`` remains
a strictly increasing physical-shell sequence for the existing LAN fences;
``group_seq`` plus ``burst_index`` preserves the native trigger grouping used
by presentation and server-side projectile reconstruction.
"""

import math


MAX_BURST_COUNT = 64
MAX_BURST_INTERVAL_SECONDS = 10.0
_EPSILON = 1.0e-9


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def descriptor_burst(gun):
    """Return the validated ``(count, interval)`` for one installed gun."""
    raw = _field(gun, 'burst', (1, 0.0)) or (1, 0.0)
    try:
        count = int(raw[0])
        interval = float(raw[1]) if len(raw) > 1 else 0.0
    except (TypeError, ValueError, IndexError, OverflowError):
        raise ValueError('installed gun burst is malformed')
    if (isinstance(raw[0], bool) or count < 1 or
            count > MAX_BURST_COUNT or float(raw[0]) != count or
            math.isnan(interval) or math.isinf(interval) or
            interval < 0.0 or interval > MAX_BURST_INTERVAL_SECONDS or
            (count > 1 and interval <= 0.0)):
        raise ValueError('installed gun burst is invalid')
    return count, interval if count > 1 else 0.0


def planned_count(gun, ammunition, loaded_clip):
    """Clamp one trigger to the real carried and currently loaded rounds."""
    count, interval = descriptor_burst(gun)
    try:
        ammunition = int(ammunition)
        loaded_clip = int(loaded_clip)
    except (TypeError, ValueError, OverflowError):
        return 0, interval
    return max(0, min(count, ammunition, loaded_clip)), interval


def after_shot_factor(gun, final_round):
    """Return #1513's bloom term for this physical round in a group."""
    factors = _field(gun, 'shotDispersionFactors', {}) or {}
    name = 'afterShot' if final_round else 'afterShotInBurst'
    fallback = _field(factors, 'afterShot', 1.5)
    try:
        value = float(_field(factors, name, fallback))
    except (TypeError, ValueError, OverflowError):
        raise ValueError('installed gun burst dispersion is malformed')
    if math.isnan(value) or math.isinf(value) or value < 0.0:
        raise ValueError('installed gun burst dispersion is invalid')
    return value


class BurstClock(object):
    """Sub-frame schedule and wire state for one physical burst group."""

    WIRE_FIELDS = (
        'burst_active', 'burst_group_seq', 'burst_count',
        'burst_next_index', 'burst_interval', 'burst_time_left',
        'burst_shell_index',
    )

    def __init__(self):
        self.active = False
        self.group_seq = 0
        self.count = 0
        self.next_index = 0
        self.interval = 0.0
        self.time_left = 0.0
        self.shell_index = 0

    def start(self, first_shot_seq, count, interval, shell_index):
        """Arm a group whose index zero is due immediately."""
        if self.active:
            return False
        try:
            first_shot_seq = int(first_shot_seq)
            count = int(count)
            interval = float(interval)
            shell_index = int(shell_index)
        except (TypeError, ValueError, OverflowError):
            return False
        if (first_shot_seq <= 0 or count < 1 or
                count > MAX_BURST_COUNT or shell_index < 0 or
                math.isnan(interval) or math.isinf(interval) or
                interval < 0.0 or interval > MAX_BURST_INTERVAL_SECONDS or
                (count > 1 and interval <= 0.0)):
            return False
        self.active = True
        self.group_seq = first_shot_seq
        self.count = count
        self.next_index = 0
        self.interval = interval if count > 1 else 0.0
        self.time_left = 0.0
        self.shell_index = shell_index
        return True

    def advance(self, dt):
        """Return every subshot due in ``dt``, retaining fractional cadence.

        A slow render callback may cross more than one 0.1-second native gun
        interval.  Returning all crossed indices avoids lowering the physical
        rate; callers publish each returned shell independently.
        """
        if not self.active:
            return ()
        try:
            dt = max(0.0, float(dt))
        except (TypeError, ValueError, OverflowError):
            return ()
        if math.isnan(dt) or math.isinf(dt):
            return ()
        due_offset = max(0.0, self.time_left)
        self.time_left -= dt
        due = []
        while self.active and self.time_left <= _EPSILON:
            index = self.next_index
            due.append({
                'shot_seq': self.group_seq + index,
                'burst_group_seq': self.group_seq,
                'burst_index': index,
                'burst_count': self.count,
                'shell_index': self.shell_index,
                'final': index + 1 >= self.count,
                # This is the physical edge inside the caller's elapsed
                # interval, not the render callback receipt time.
                'due_offset': min(dt, due_offset),
            })
            self.next_index += 1
            if self.next_index >= self.count:
                self.active = False
                self.time_left = 0.0
            else:
                self.time_left += self.interval
                due_offset += self.interval
        return tuple(due)

    def cancel(self, launched_count=None):
        """Cancel only the still-unlaunched tail of a group.

        ``advance`` can expose several overdue indices at once.  If a final
        physical gate closes part-way through that tuple, ``launched_count``
        rewinds the speculative tail to the number actually committed.
        """
        changed = self.active or self.count > 0
        if launched_count is None:
            launched_count = self.next_index
        try:
            launched_count = int(launched_count)
        except (TypeError, ValueError, OverflowError):
            return False
        if launched_count < 0 or launched_count > self.next_index:
            return False
        self.active = False
        self.next_index = launched_count
        if launched_count == 0:
            self.group_seq = 0
            self.count = 0
            self.interval = 0.0
            self.shell_index = 0
        self.time_left = 0.0
        return changed

    def publish(self, target):
        target.update({
            'burst_active': bool(self.active),
            'burst_group_seq': int(self.group_seq),
            'burst_count': int(self.count),
            'burst_next_index': int(self.next_index),
            'burst_interval': round(float(self.interval), 6),
            'burst_time_left': round(max(0.0, float(self.time_left)), 6),
            'burst_shell_index': int(self.shell_index),
        })

    def restore(self, raw, fire_seq):
        """Validate and load one complete server-sanitized wire snapshot."""
        present = tuple(name in raw for name in self.WIRE_FIELDS)
        if not any(present):
            return False
        if not all(present):
            raise ValueError('burst wire snapshot is incomplete')
        try:
            active = raw['burst_active']
            group_seq = int(raw['burst_group_seq'])
            count = int(raw['burst_count'])
            next_index = int(raw['burst_next_index'])
            interval = float(raw['burst_interval'])
            time_left = float(raw['burst_time_left'])
            shell_index = int(raw['burst_shell_index'])
            fire_seq = int(fire_seq)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('burst wire snapshot is malformed')
        exact_integers = (
            (raw['burst_group_seq'], group_seq),
            (raw['burst_count'], count),
            (raw['burst_next_index'], next_index),
            (raw['burst_shell_index'], shell_index),
        )
        if (not isinstance(active, bool) or
                any(isinstance(raw_value, bool) or
                    float(raw_value) != parsed
                    for raw_value, parsed in exact_integers) or
                group_seq < 0 or count < 0 or count > MAX_BURST_COUNT or
                next_index < 0 or next_index > count or shell_index < 0 or
                math.isnan(interval) or math.isinf(interval) or
                math.isnan(time_left) or math.isinf(time_left) or
                interval < 0.0 or interval > MAX_BURST_INTERVAL_SECONDS or
                time_left < 0.0 or
                (count > 1 and interval <= 0.0) or
                (active and (count < 2 or next_index < 1 or
                             next_index >= count or
                             time_left > interval + _EPSILON)) or
                (not active and time_left != 0.0) or
                (count == 0 and (group_seq != 0 or next_index != 0)) or
                (count > 0 and fire_seq != group_seq + next_index - 1)):
            raise ValueError('burst wire snapshot is invalid')
        self.active = active
        self.group_seq = group_seq
        self.count = count
        self.next_index = next_index
        self.interval = interval
        self.time_left = time_left
        self.shell_index = shell_index
        return True
