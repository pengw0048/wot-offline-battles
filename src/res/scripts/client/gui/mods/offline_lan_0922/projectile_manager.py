# -*- coding: utf-8 -*-
"""Deterministic, engine-free lifetime manager for in-flight projectiles.

The manager owns only trajectory time and distance.  Callers provide the
collision adapter and terminal sink, keeping BigWorld and LAN protocol details
outside this module.  All public snapshots and callback states are detached
copies; mutating them cannot alter the frozen launch or cursor state.
"""

import copy
import math
from collections import deque

try:
    from .projectile_runtime import curvature_limited_substep
    from .projectile_runtime import lerp3
    from .projectile_runtime import substep_boundaries
    from .projectile_runtime import trajectory_position
    from .projectile_runtime import PROJECTILE_MAX_SUBSTEP_SECONDS
except (ImportError, ValueError):
    from projectile_runtime import curvature_limited_substep
    from projectile_runtime import lerp3
    from projectile_runtime import substep_boundaries
    from projectile_runtime import trajectory_position
    from projectile_runtime import PROJECTILE_MAX_SUBSTEP_SECONDS


DEFAULT_MAX_ACTIVE_PROJECTILES = 128
_EPSILON = 1e-9

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)

try:
    _TEXT_TYPES = (str, unicode)
except NameError:
    _TEXT_TYPES = (str,)


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('expected a finite number')
    if math.isnan(number) or math.isinf(number):
        raise ValueError('expected a finite number')
    return number


def _vector3(value):
    if value is None or len(value) != 3:
        raise ValueError('expected a three-component vector')
    return (
        _finite_number(value[0]),
        _finite_number(value[1]),
        _finite_number(value[2]),
    )


def _freeze_key(value):
    """Freeze common protocol keys without retaining mutable caller data."""
    if value is None or isinstance(value, _TEXT_TYPES + _INTEGER_TYPES):
        return value
    if isinstance(value, float):
        return _finite_number(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_key(item) for item in value)
    raise ValueError('projectile key must be an immutable protocol value')


def _distance(first, second):
    delta_x = float(second[0]) - float(first[0])
    delta_y = float(second[1]) - float(first[1])
    delta_z = float(second[2]) - float(first[2])
    return math.sqrt(
        delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)


def _safe_copy(value):
    return copy.deepcopy(value)


class _ProjectileState(object):
    """Internal mutable cursor with detached public snapshots."""

    def __init__(self, key, start, velocity, gravity, launch_time, max_time,
                 max_distance, payload, maximum_substep):
        self.key = key
        self.start = start
        self.velocity = velocity
        self.gravity = gravity
        self.launch_time = launch_time
        self.max_time = max_time
        self.max_distance = max_distance
        self.payload = payload
        self.maximum_step = curvature_limited_substep(
            gravity, maximum_substep)
        self.cursor_time = launch_time
        self.position = start
        self.distance = 0.0

    def snapshot(self):
        """Return a detached, deterministic representation of current state."""
        return {
            'key': _safe_copy(self.key),
            'start': tuple(self.start),
            'velocity': tuple(self.velocity),
            'gravity': tuple(self.gravity),
            'launch_time': float(self.launch_time),
            'max_time': float(self.max_time),
            'max_distance': float(self.max_distance),
            'payload': _safe_copy(self.payload),
            'cursor_time': float(self.cursor_time),
            'elapsed': max(0.0, float(
                self.cursor_time - self.launch_time)),
            'position': tuple(self.position),
            'distance': float(self.distance),
        }


class InFlightProjectiles(object):
    """Own bounded, absolute-time projectile advancement.

    ``chord_callback`` is called as ``callback(state, start, end,
    absolute_start, absolute_end)``.  ``state`` is a detached snapshot.  The
    callback returns ``None`` to continue or a terminal ``dict``.  A terminal
    dict may contain ``fraction`` in ``[0, 1]`` to stop part-way through the
    chord.

    ``terminal_callback`` receives ``(state, terminal)`` after the cursor has
    been moved to its exact terminal point.  Callback failures fail closed: the
    affected projectile is retired, and other projectiles still advance.
    """

    def __init__(self, maximum_active=DEFAULT_MAX_ACTIVE_PROJECTILES,
                 initial_time=0.0,
                 maximum_substep=PROJECTILE_MAX_SUBSTEP_SECONDS):
        maximum_active = int(maximum_active)
        if maximum_active <= 0:
            raise ValueError('maximum_active must be positive')
        maximum_substep = _finite_number(maximum_substep)
        if maximum_substep <= 0.0:
            raise ValueError('maximum_substep must be positive')
        self._maximum_active = maximum_active
        self._maximum_substep = min(
            PROJECTILE_MAX_SUBSTEP_SECONDS, maximum_substep)
        self._now = _finite_number(initial_time)
        if self._now < 0.0:
            raise ValueError('initial_time must not be negative')
        self._states = {}
        self._order = []
        self._round_robin = deque()
        self._known_keys = set()
        self._advancing = False
        self._pending_operations = []
        self._pending_launch_keys = set()
        self._pending_remove_keys = set()
        self._current_callback_key = None
        self._cancel_current_keys = set()
        self._advance_chords = 0
        self._advance_terminals = 0
        self._last_advance_metrics = {
            'active': 0,
            'chords': 0,
            'terminals': 0,
            'debt_before': 0.0,
            'debt_after': 0.0,
        }

    @property
    def now(self):
        return self._now

    def __len__(self):
        return len(self._states) + len(self._pending_launch_keys)

    def contains(self, key):
        try:
            frozen_key = _freeze_key(key)
        except (TypeError, ValueError, OverflowError):
            return False
        return (frozen_key in self._states or
                frozen_key in self._pending_launch_keys)

    def snapshot(self):
        """Return active states in stable launch order as detached copies."""
        result = []
        for key in self._order:
            state = self._states.get(key)
            if state is not None:
                result.append(state.snapshot())
        return result

    def get(self, key):
        """Return one detached state snapshot, or ``None`` when inactive."""
        try:
            frozen_key = _freeze_key(key)
        except (TypeError, ValueError, OverflowError):
            return None
        state = self._states.get(frozen_key)
        if state is None:
            return None
        return state.snapshot()

    def last_advance_metrics(self):
        """Return detached work counters for the latest valid advance."""
        return dict(self._last_advance_metrics)

    def sustainable_chord_budget(self, interval):
        """Return chords needed to cover ``interval`` for every active shot."""
        try:
            interval = _finite_number(interval)
        except (TypeError, ValueError, OverflowError):
            return 0
        if interval <= 0.0:
            return 0
        total = 0
        for state in self._states.values():
            total += int(math.ceil(max(
                0.0, interval - _EPSILON) / state.maximum_step))
        return total

    def launch(self, key, start, velocity, gravity, launch_time, max_time,
               max_distance, payload=None):
        """Freeze and register one unique launch, returning acceptance."""
        try:
            frozen_key = _freeze_key(key)
            hash(frozen_key)
            frozen_start = _vector3(start)
            frozen_velocity = _vector3(velocity)
            frozen_gravity = _vector3(gravity)
            frozen_launch_time = _finite_number(launch_time)
            frozen_max_time = _finite_number(max_time)
            frozen_max_distance = _finite_number(max_distance)
            frozen_payload = _safe_copy(payload)
        except Exception:
            return False
        if frozen_launch_time < 0.0:
            return False
        if frozen_launch_time > self._now + _EPSILON:
            return False
        if frozen_max_time <= 0.0 or frozen_max_distance <= 0.0:
            return False
        if frozen_key in self._known_keys:
            return False
        if len(self) >= self._maximum_active:
            return False
        state = _ProjectileState(
            frozen_key, frozen_start, frozen_velocity, frozen_gravity,
            frozen_launch_time, frozen_max_time, frozen_max_distance,
            frozen_payload, self._maximum_substep)
        self._known_keys.add(frozen_key)
        if self._advancing:
            self._pending_launch_keys.add(frozen_key)
            self._pending_operations.append(('launch', state))
        else:
            self._install(state)
        return True

    def restore(self, snapshot):
        """Restore one active cursor from a detached takeover snapshot.

        The launch trajectory remains authoritative.  Any supplied ``position``
        or ``elapsed`` value is ignored and recomputed from ``cursor_time`` so a
        takeover cannot introduce a discontinuity or rescan an elapsed chord.
        """
        if self._advancing or not isinstance(snapshot, dict):
            return False
        try:
            frozen_key = _freeze_key(snapshot['key'])
            hash(frozen_key)
            frozen_start = _vector3(snapshot['start'])
            frozen_velocity = _vector3(snapshot['velocity'])
            frozen_gravity = _vector3(snapshot['gravity'])
            frozen_launch_time = _finite_number(snapshot['launch_time'])
            frozen_max_time = _finite_number(snapshot['max_time'])
            frozen_max_distance = _finite_number(snapshot['max_distance'])
            frozen_cursor_time = _finite_number(snapshot['cursor_time'])
            frozen_distance = _finite_number(snapshot['distance'])
            frozen_payload = _safe_copy(snapshot.get('payload'))
        except Exception:
            return False
        if frozen_launch_time < 0.0:
            return False
        if frozen_max_time <= 0.0 or frozen_max_distance <= 0.0:
            return False
        if frozen_cursor_time < frozen_launch_time:
            return False
        if frozen_cursor_time > self._now:
            return False
        if frozen_cursor_time > frozen_launch_time + frozen_max_time:
            return False
        if frozen_distance < 0.0 or frozen_distance > frozen_max_distance:
            return False
        if frozen_key in self._known_keys:
            return False
        if len(self) >= self._maximum_active:
            return False

        state = _ProjectileState(
            frozen_key, frozen_start, frozen_velocity, frozen_gravity,
            frozen_launch_time, frozen_max_time, frozen_max_distance,
            frozen_payload, self._maximum_substep)
        state.cursor_time = frozen_cursor_time
        state.position = trajectory_position(
            state.start, state.velocity, state.gravity,
            state.cursor_time - state.launch_time)
        state.distance = frozen_distance
        self._known_keys.add(frozen_key)
        self._install(state)
        return True

    def remove(self, key):
        """Retire a projectile without a terminal callback."""
        try:
            frozen_key = _freeze_key(key)
        except (TypeError, ValueError, OverflowError):
            return False
        if (frozen_key not in self._states and
                frozen_key not in self._pending_launch_keys):
            return False
        if self._advancing:
            if frozen_key in self._pending_remove_keys:
                return False
            self._pending_remove_keys.add(frozen_key)
            self._pending_operations.append(('remove', frozen_key))
            if frozen_key == self._current_callback_key:
                self._cancel_current_keys.add(frozen_key)
        else:
            self._remove_active(frozen_key)
        return True

    def reset(self, now=None):
        """Clear active and retired identities while preserving clock safety."""
        if self._advancing:
            return False
        if now is not None:
            try:
                reset_time = _finite_number(now)
            except (TypeError, ValueError, OverflowError):
                return False
            if reset_time + _EPSILON < self._now:
                return False
            self._now = reset_time
        self._states.clear()
        self._order = []
        self._round_robin.clear()
        self._known_keys.clear()
        self._pending_operations = []
        self._pending_launch_keys.clear()
        self._pending_remove_keys.clear()
        self._current_callback_key = None
        self._cancel_current_keys.clear()
        self._advance_chords = 0
        self._advance_terminals = 0
        self._last_advance_metrics = {
            'active': 0,
            'chords': 0,
            'terminals': 0,
            'debt_before': 0.0,
            'debt_after': 0.0,
        }
        return True

    def advance(self, now, chord_callback, terminal_callback,
                maximum_chords=None):
        """Advance active projectiles toward one absolute clock value.

        The default remains the original unlimited catch-up behavior.  A
        non-negative integer ``maximum_chords`` bounds the total collision
        callbacks in this invocation.  Bounded work is scheduled one chord at
        a time in persistent round-robin order, so repeated calls at the same
        absolute ``now`` eventually catch every projectile up without losing
        elapsed trajectory time or starving later launches.
        """
        try:
            target_time = _finite_number(now)
        except (TypeError, ValueError, OverflowError):
            return False
        if target_time + _EPSILON < self._now:
            return False
        if not callable(chord_callback) or not callable(terminal_callback):
            return False
        if (maximum_chords is not None and
                (isinstance(maximum_chords, bool) or
                 not isinstance(maximum_chords, _INTEGER_TYPES) or
                 maximum_chords < 0)):
            return False
        if self._advancing:
            return False

        active_before = len(self._states)
        debt_before = self._maximum_debt(target_time)
        self._advance_chords = 0
        self._advance_terminals = 0
        self._now = max(self._now, target_time)
        self._advancing = True
        try:
            if maximum_chords is None:
                self._advance_unbounded(
                    target_time, chord_callback, terminal_callback)
            elif maximum_chords > 0:
                self._advance_bounded(
                    target_time, chord_callback, terminal_callback,
                    maximum_chords)
        finally:
            self._advancing = False
            self._flush_pending_operations()
        self._last_advance_metrics = {
            'active': active_before,
            'chords': self._advance_chords,
            'terminals': self._advance_terminals,
            'debt_before': debt_before,
            'debt_after': self._maximum_debt(target_time),
        }
        return True

    def _maximum_debt(self, target_time):
        maximum = 0.0
        for state in self._states.values():
            lifetime_end = state.launch_time + state.max_time
            desired = min(float(target_time), lifetime_end)
            maximum = max(
                maximum, max(0.0, desired - state.cursor_time))
        return maximum

    def _advance_unbounded(self, target_time, chord_callback,
                           terminal_callback):
        """Preserve the original stable launch-order catch-up contract."""
        for key in list(self._order):
            state = self._states.get(key)
            if state is None:
                continue
            self._advance_safely(
                state, target_time, chord_callback, terminal_callback, None)

    def _advance_bounded(self, target_time, chord_callback,
                         terminal_callback, maximum_chords):
        """Spend one global chord budget fairly across active states."""
        remaining = maximum_chords
        while remaining > 0 and self._round_robin:
            round_size = len(self._round_robin)
            used_this_round = 0
            for unused_index in range(round_size):
                if remaining <= 0 or not self._round_robin:
                    break
                key = self._round_robin.popleft()
                state = self._states.get(key)
                if state is None:
                    continue
                used = self._advance_safely(
                    state, target_time, chord_callback, terminal_callback, 1)
                if (state.key in self._states and
                        state.key not in self._pending_remove_keys):
                    self._round_robin.append(state.key)
                used_this_round += used
                remaining -= used
            if used_this_round <= 0:
                break

    def _advance_safely(self, state, target_time, chord_callback,
                        terminal_callback, maximum_chords):
        try:
            return self._advance_one(
                state, target_time, chord_callback, terminal_callback,
                maximum_chords)
        except Exception:
            if state.key in self._states:
                self._finish(state, {
                    'reason': 'callback_error',
                    'fraction': 0.0,
                }, terminal_callback)
            return 0

    def _install(self, state):
        self._states[state.key] = state
        self._order.append(state.key)
        self._round_robin.append(state.key)

    def _remove_active(self, key):
        if key in self._states:
            del self._states[key]
        try:
            self._order.remove(key)
        except ValueError:
            pass
        if self._round_robin:
            self._round_robin = deque(
                value for value in self._round_robin if value != key)
        self._pending_launch_keys.discard(key)
        self._pending_remove_keys.discard(key)

    def _flush_pending_operations(self):
        operations = self._pending_operations
        self._pending_operations = []
        for operation, value in operations:
            if operation == 'launch':
                self._pending_launch_keys.discard(value.key)
                self._install(value)
            else:
                self._remove_active(value)
        self._pending_launch_keys.clear()
        self._pending_remove_keys.clear()
        self._cancel_current_keys.clear()

    def _advance_one(self, state, target_time, chord_callback,
                     terminal_callback, maximum_chords=None):
        if state.distance >= state.max_distance:
            self._finish(state, {
                'reason': 'max_distance',
                'fraction': 1.0,
            }, terminal_callback)
            return 0
        lifetime_end = state.launch_time + state.max_time
        end_time = min(target_time, lifetime_end)
        if end_time + _EPSILON < state.cursor_time:
            return 0

        used = 0
        for absolute_start, absolute_end in substep_boundaries(
                state.cursor_time, end_time, state.maximum_step):
            if maximum_chords is not None and used >= maximum_chords:
                break
            start = trajectory_position(
                state.start, state.velocity, state.gravity,
                absolute_start - state.launch_time)
            unconstrained_end = trajectory_position(
                state.start, state.velocity, state.gravity,
                absolute_end - state.launch_time)
            chord_distance = _distance(start, unconstrained_end)
            remaining_distance = max(
                0.0, state.max_distance - state.distance)
            distance_fraction = 1.0
            distance_terminal = False
            if chord_distance > remaining_distance:
                distance_fraction = remaining_distance / chord_distance
                distance_terminal = True
            elif remaining_distance <= 0.0:
                distance_fraction = 0.0
                distance_terminal = True

            end = lerp3(start, unconstrained_end, distance_fraction)
            constrained_absolute_end = (
                absolute_start +
                (absolute_end - absolute_start) * distance_fraction)
            result = self._call_chord(
                chord_callback, state, start, end, absolute_start,
                constrained_absolute_end)
            used += 1
            if result is not None:
                fraction = self._terminal_fraction(result)
                self._move_cursor(
                    state, start, end, absolute_start,
                    constrained_absolute_end, fraction)
                result['fraction'] = fraction
                self._finish(state, result, terminal_callback)
                return used

            self._move_cursor(
                state, start, end, absolute_start,
                constrained_absolute_end, 1.0)
            if state.key in self._cancel_current_keys:
                return used
            if distance_terminal or state.distance >= state.max_distance:
                self._finish(state, {
                    'reason': 'max_distance',
                    'fraction': 1.0,
                }, terminal_callback)
                return used

        if (state.key in self._states and
                state.key not in self._pending_remove_keys and
                target_time + _EPSILON >= lifetime_end and
                state.cursor_time + _EPSILON >= lifetime_end):
            state.cursor_time = lifetime_end
            state.position = trajectory_position(
                state.start, state.velocity, state.gravity, state.max_time)
            self._finish(state, {
                'reason': 'max_time',
                'fraction': 1.0,
            }, terminal_callback)
        return used

    def _call_chord(self, callback, state, start, end, absolute_start,
                    absolute_end):
        self._advance_chords += 1
        self._current_callback_key = state.key
        try:
            result = callback(
                state.snapshot(), tuple(start), tuple(end),
                float(absolute_start), float(absolute_end))
        except Exception:
            return {'reason': 'callback_error', 'fraction': 0.0}
        finally:
            self._current_callback_key = None
        if result is None:
            return None
        if not isinstance(result, dict):
            return {'reason': 'callback_error', 'fraction': 0.0}
        try:
            return _safe_copy(result)
        except Exception:
            return {'reason': 'callback_error', 'fraction': 0.0}

    def _terminal_fraction(self, result):
        try:
            fraction = _finite_number(result.get('fraction', 1.0))
        except (TypeError, ValueError, OverflowError):
            result.clear()
            result.update({'reason': 'callback_error', 'fraction': 0.0})
            return 0.0
        return max(0.0, min(1.0, fraction))

    def _move_cursor(self, state, start, end, absolute_start, absolute_end,
                     fraction):
        fraction = max(0.0, min(1.0, float(fraction)))
        terminal_position = lerp3(start, end, fraction)
        state.distance = min(
            state.max_distance,
            state.distance + _distance(start, end) * fraction)
        state.cursor_time = (
            absolute_start + (absolute_end - absolute_start) * fraction)
        state.position = terminal_position

    def _finish(self, state, result, terminal_callback):
        if self._advancing:
            self._advance_terminals += 1
        self._remove_active(state.key)
        snapshot = state.snapshot()
        try:
            terminal_callback(snapshot, _safe_copy(result))
        except Exception:
            pass
