"""Bounded presentation continuation of admitted driver poses, without physics."""

import math

try:
    _INTS = (int, long)
except NameError:
    _INTS = (int,)

_TAU = 2.0 * math.pi


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, _INTS + (float,)):
        raise ValueError('presentation value is not numeric')
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError('presentation value is not finite')
    return value


def _pose(value):
    if not isinstance(value, dict):
        raise ValueError('presentation pose is not an object')
    position = value['position']
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise ValueError('presentation position is not a triple')
    return {'position': tuple(_finite(item) for item in position),
            'yaw': _finite(value['yaw']), 'pitch': _finite(value['pitch']),
            'roll': _finite(value['roll'])}


def _copy(value):
    return dict(value) if value is not None else None


def _angle(value):
    return (value + math.pi) % _TAU - math.pi


class DriverPresentation(object):
    """Keep canonical samples separate from the last guarded display pose.

    The caller admits canonical identity/state and guards every candidate's
    geometry. A successful accept replaces any prior display prediction.
    candidate never integrates from a previous candidate or display pose.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._canonical = None
        self._display = None
        self._source_time_us = None
        self._last_arrival_time = None
        self._last_candidate_time = None
        self._disable_continuation()

    @property
    def canonical(self):
        return _copy(self._canonical)

    @property
    def source_interval(self):
        return self._source_interval

    @property
    def last_arrival_time(self):
        return self._last_arrival_time

    def _disable_continuation(self):
        self._source_interval = 0.0
        self._displacement = None
        self._yaw_delta = 0.0

    def accept(self, pose, source_time_us, arrival_time):
        """Copy a new canonical pose; invalid/stale samples only stop prediction.

        A zero source time is a valid stationary baseline, without a motion
        pair. Positive source times must strictly increase. Arrival times may
        coincide when several ordered receipts are drained in one callback.
        """
        try:
            value = _pose(pose)
            arrival = _finite(arrival_time)
            if (isinstance(source_time_us, bool) or
                    not isinstance(source_time_us, _INTS) or
                    not 0 <= source_time_us <= 2 ** 63 - 1):
                raise ValueError('invalid presentation source time')
            if (self._last_arrival_time is not None and
                    arrival < self._last_arrival_time):
                raise ValueError('presentation arrival time moved backwards')
            if (source_time_us and self._source_time_us and
                    source_time_us <= self._source_time_us):
                raise ValueError('presentation source time did not advance')
        except (KeyError, TypeError, ValueError, OverflowError):
            self._disable_continuation()
            return False

        previous = self._canonical
        previous_time = self._source_time_us
        self._canonical = value
        self._display = _copy(value)
        self._source_time_us = source_time_us
        self._last_arrival_time = arrival
        self._last_candidate_time = arrival
        self._disable_continuation()
        if previous is not None and previous_time and source_time_us:
            try:
                displacement = tuple(_finite(current - old)
                                     for current, old in zip(
                                         value['position'],
                                         previous['position']))
                yaw_delta = _angle(_angle(value['yaw']) -
                                   _angle(previous['yaw']))
                interval = (source_time_us - previous_time) / 1000000.0
                self._displacement = displacement
                self._yaw_delta = yaw_delta
                self._source_interval = interval
            except (ValueError, OverflowError):
                self._disable_continuation()
        return True

    def candidate(self, now, allow_continuation=True):
        """Return a fresh pose, holding the last committed pose after expiry.

        The continuation covers at most one source interval after arrival.
        A disabled gate holds the last guarded display pose; it does not
        promote an unguarded candidate. Clock failures retire the motion pair.
        """
        try:
            now = _finite(now)
            if (self._last_candidate_time is not None and
                    now < self._last_candidate_time):
                raise ValueError('presentation clock moved backwards')
        except (TypeError, ValueError, OverflowError):
            self._disable_continuation()
            return _copy(self._display)
        self._last_candidate_time = now
        if not self._source_interval:
            return _copy(self._display)
        if now > self._last_arrival_time + self._source_interval:
            self._disable_continuation()
            return _copy(self._display)
        if not allow_continuation:
            return _copy(self._display)
        elapsed = now - self._last_arrival_time
        if elapsed == 0.0:
            return _copy(self._canonical)
        fraction = min(1.0, elapsed / self._source_interval)
        try:
            value = _copy(self._canonical)
            value['position'] = tuple(_finite(position + delta * fraction)
                                      for position, delta in zip(
                                          value['position'],
                                          self._displacement))
            value['yaw'] = _angle(_angle(value['yaw']) +
                                  self._yaw_delta * fraction)
            return value
        except (ValueError, OverflowError):
            self._disable_continuation()
            return _copy(self._display)

    def commit_display(self, pose):
        """Remember only the pose actually accepted by the caller's guard."""
        if self._canonical is None:
            return False
        try:
            value = _pose(pose)
        except (KeyError, TypeError, ValueError, OverflowError):
            self._disable_continuation()
            return False
        self._display = value
        return True
