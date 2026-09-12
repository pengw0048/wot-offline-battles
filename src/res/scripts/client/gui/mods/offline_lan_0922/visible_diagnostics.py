"""Sample the visible client's extra sync and local-update work.

Only synchronous calls inside those two frame phases bind this observer.
Rows contain numbers and fixed stage names, never vehicles or call arguments.
Inclusive time contains children; self time removes instrumented children.
Neither the sampler nor a diagnostic failure may change gameplay work.
"""

import functools
import math


FRAME_STRIDE = 8
STAGES = (
    'sync', 'sync.apply', 'sync.pose', 'sync.aim', 'sync.tracks',
    'local', 'local.motion', 'local.world', 'local.contacts',
    'local.support', 'local.ground', 'local.solver',
    'local.present', 'local.tracks',
)
MAX_DEPTH = 16


def new_frame(frame_id, clock):
    """Sample one frame per block, rotating across periodic track feeds."""
    try:
        index = int(frame_id) - 1
    except (TypeError, ValueError, OverflowError):
        return None
    if index < 0 or index % FRAME_STRIDE != (index // FRAME_STRIDE) % FRAME_STRIDE:
        return None
    return FrameCosts(clock)


class FrameCosts(object):

    def __init__(self, clock):
        self.clock = clock
        self.rows = {}
        self.stack = []
        self.failed = False

    def _fail(self):
        self.failed = True
        self.rows.clear()
        self.stack[:] = []

    def start(self, name):
        if self.failed:
            return None
        try:
            if name not in STAGES or len(self.stack) >= MAX_DEPTH:
                raise ValueError('invalid visible timing scope')
            started = float(self.clock())
            if math.isnan(started) or math.isinf(started):
                raise ValueError('invalid visible timing clock')
            token = [name, started, 0.0]
            self.stack.append(token)
            return token
        except Exception:
            self._fail()
            return None

    def stop(self, token):
        if token is None or self.failed:
            return
        try:
            if not self.stack or self.stack[-1] is not token:
                raise ValueError('unbalanced visible timing scope')
            elapsed = float(self.clock()) - token[1]
            if math.isnan(elapsed) or math.isinf(elapsed) or elapsed < 0.0:
                raise ValueError('invalid visible timing interval')
            self.stack.pop()
            own = max(0.0, elapsed - token[2])
            row = self.rows.setdefault(token[0], [0, 0.0, 0.0, 0.0])
            row[0] += 1
            row[1] += elapsed
            row[2] += own
            row[3] = max(row[3], elapsed)
            if self.stack:
                self.stack[-1][2] += elapsed
        except Exception:
            self._fail()

    def snapshot(self):
        if self.stack:
            self._fail()
        return {
            'failed': self.failed,
            'stages': dict((name, tuple(row)) for name, row in self.rows.items()),
        }


def call(owner, name, function, *args, **kwargs):
    costs = owner._visible_frame_costs
    if costs is None:
        return function(*args, **kwargs)
    token = costs.start(name)
    try:
        return function(*args, **kwargs)
    finally:
        costs.stop(token)


def measured(name):
    """Observe a runtime method only while its frame phase owns a sample."""
    def decorate(method):
        @functools.wraps(method)
        def measured_call(self, *args, **kwargs):
            costs = self._visible_frame_costs
            if costs is None:
                return method(self, *args, **kwargs)
            token = costs.start(name)
            try:
                return method(self, *args, **kwargs)
            finally:
                costs.stop(token)
        return measured_call
    return decorate
