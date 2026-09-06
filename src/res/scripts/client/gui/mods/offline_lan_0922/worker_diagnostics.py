# -*- coding: utf-8 -*-
"""Bounded, observational timings for the hidden worker's combat workload.

All durations use the injected wall clock. Queue timestamps use authority
time. Neither clock, a diagnostic failure, nor a captured value controls
simulation work. Stage totals include children; self time excludes them.
"""

import collections
import functools
import math
import threading


CAPTURE_SECONDS = 30.0
CAPTURE_COOLDOWN_SECONDS = 30.0
MAX_CAPTURES = 3
MAX_QUEUE_IDENTITIES = 1024
MAX_WAIT_SAMPLES = 512
MAX_ACTOR_SLICES = 128
MAX_CAPTURE_ACTORS = 64
MAX_FRAME_ACTORS_REPORTED = 6
MAX_GEOMETRY_KEYS = 2048

# Only synchronous, instrumented Python calls bind this observer. No native
# hook, entity, scheduled callback or process-global simulation state is owned.
_observer = threading.local()


def current():
    diagnostic = getattr(_observer, 'current', None)
    return (diagnostic if diagnostic is not None and diagnostic.active and
            diagnostic.detail_active else None)


def count(name, value=1):
    diagnostic = current()
    if diagnostic is not None:
        diagnostic.count(name, value)


def observed_call(stage, function, *args, **kwargs):
    diagnostic = current()
    if diagnostic is None:
        return function(*args, **kwargs)
    return _invoke(diagnostic, stage, function, args, kwargs)


def observed_ray(stage, function, space, start, end, *args):
    diagnostic = current()
    if diagnostic is None:
        return function(space, start, end, *args)
    try:
        diagnostic.geometry(stage, (
            space, (start.x, start.y, start.z), (end.x, end.y, end.z)))
    except Exception:
        diagnostic.count('ray_geometry_unavailable')
    return _invoke(diagnostic, stage, function, (space, start, end) + args, {})


def observed(stage):
    """Observe a pure helper only inside its synchronous worker owner."""
    def decorate(function):
        @functools.wraps(function)
        def measured(*args, **kwargs):
            diagnostic = current()
            if diagnostic is None:
                return function(*args, **kwargs)
            return _invoke(diagnostic, stage, function, args, kwargs)
        return measured
    return decorate


def _invoke(diagnostic, stage, function, args, kwargs):
    previous = getattr(_observer, 'current', None)
    _observer.current = diagnostic
    token = diagnostic.start()
    try:
        return function(*args, **kwargs)
    finally:
        try:
            diagnostic.stop(stage, token)
        finally:
            _observer.current = previous


def call(diagnostic, stage, function, *args, **kwargs):
    """Measure an injected adapter without changing its callable identity."""
    if diagnostic is None or not diagnostic.active:
        return function(*args, **kwargs)
    return _invoke(diagnostic, stage, function, args, kwargs)


def timed(stage):
    """Time an owned Python boundary only during a combat capture."""
    def decorate(method):
        @functools.wraps(method)
        def measured(self, *args, **kwargs):
            diagnostic = getattr(self, '_combat_diagnostics', None)
            if diagnostic is None or not diagnostic.active:
                return method(self, *args, **kwargs)
            return _invoke(diagnostic, stage, method, (self,) + args, kwargs)
        return measured
    return decorate


class WorkerCombatDiagnostics(object):
    """Capture three combat windows without retaining native objects."""

    def __init__(self, clock, capture_seconds=CAPTURE_SECONDS,
                 cooldown_seconds=CAPTURE_COOLDOWN_SECONDS,
                 maximum_captures=MAX_CAPTURES, detail_stride=1):
        self.clock = clock
        self.capture_seconds = float(capture_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.maximum_captures = int(maximum_captures)
        self.detail_stride = max(1, int(detail_stride))
        self.reset()

    def reset(self):
        self.enabled = True
        self.active = False
        self.detail_active = False
        self._detail_ticks = 0
        self.capture = 0
        self._deadline = None
        self._next_capture = 0.0
        self._reason = None
        self._frame = 0
        self._now = 0.0
        self._stack = []
        self._stages = {}
        self._counts = {}
        self._queue = {}
        self._receipts = {}
        self._waits = []
        self._selected_waits = []
        self._queue_snapshot = {}
        self._capture_totals = {}
        self._capture_counts = {}
        self._capture_queue_maxima = {}
        self._capture_started = 0.0
        self._capture_last = 0.0
        self._capture_frames = 0
        self._capture_waits = collections.deque(maxlen=MAX_WAIT_SAMPLES)
        self._capture_selected_waits = collections.deque(
            maxlen=MAX_WAIT_SAMPLES)
        self._capture_wait_count = 0
        self._capture_selected_wait_count = 0
        self._completed = []
        self._capture_actors = {}
        self._capture_detail_stages = {}
        self._capture_detail_counts = {}
        self._capture_detail_frames = 0
        self._reset_actor_frame()

    def _reset_actor_frame(self):
        self._actor_key = None
        self._actor_token = None
        self._phase_token = None
        self._phase_name = None
        self._actors = {}
        self._slices = []
        self._slice = 0
        self._control_selected = False
        self._geometry = set()

    def _fail(self):
        self.enabled = False
        self.active = False
        self._stack = []
        self._queue.clear()
        self._receipts.clear()
        self._reset_actor_frame()

    def begin_control(self):
        if not self.active or self._control_selected:
            return
        # Sample complete control callbacks, never every N render frames:
        # render/modulo sampling can alias the 10 Hz control cadence and miss
        # every Bot update. Rotate the selected slot within each group so
        # slower decision cadences do not always select the same Bot cohort.
        # All catch-up slices keep this decision.
        self._control_selected = True
        self._detail_ticks += 1
        tick = self._detail_ticks - 1
        self.detail_active = (tick % self.detail_stride ==
                              (tick // self.detail_stride) % self.detail_stride)

    def begin_slice(self, elapsed, refresh_control, publish):
        if not self.active:
            return
        self.actor(None)
        self.begin_control()
        self._slice += 1
        if len(self._slices) < MAX_ACTOR_SLICES:
            self._slices.append({
                'slice': self._slice, 'elapsed_ms': round(elapsed * 1000.0, 3),
                'control': bool(refresh_control),
                'publish_requested': bool(publish),
                'detail': self.detail_active,
            })

    def actor(self, bot_id):
        """Attribute a synchronous Bot work block, including its residual time.

        Explicitly close between roster loops. The enclosing timed scope also
        closes its unfinished actor on exception, before unwinding itself.
        """
        if not self.active or not self.detail_active:
            return
        self.phase(None)
        token = self._actor_token
        self._actor_token = None
        if token is not None:
            self.stop('bot.actor', token)
        self._actor_key = None
        if bot_id is None or not self.active:
            return
        key = (self._slice, int(bot_id))
        if key not in self._actors:
            if len(self._actors) >= MAX_ACTOR_SLICES:
                self.count('actor_tracking_overflow')
                return
            self._actors[key] = {'stages': {}, 'counts': {}}
        self._actor_key = key
        self._actor_token = self.start()

    def phase(self, name):
        """Partition inline Bot work without moving simulation statements."""
        if not self.active or not self.detail_active:
            return
        token, previous_name = self._phase_token, self._phase_name
        self._phase_token = None
        self._phase_name = None
        if token is not None:
            self.stop(previous_name, token)
        if name is not None and self._actor_key is not None and self.active:
            self._phase_name = name
            self._phase_token = self.start()

    def geometry(self, name, key):
        """Count repeated geometry, without asserting unchanged world state."""
        if not self.active:
            return
        self.count(name + '_queries')
        try:
            signature = (self._actor_key, name, key)
            if signature in self._geometry:
                self.count(name + '_same_geometry_in_slice')
            elif len(self._geometry) < MAX_GEOMETRY_KEYS:
                self._geometry.add(signature)
            else:
                self.count('geometry_tracking_overflow')
        except Exception:
            self.count('geometry_tracking_unavailable')

    @staticmethod
    def _finite(value):
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            raise ValueError('non-finite diagnostic clock')
        return value

    def begin_frame(self, frame, now, trigger=None):
        """Arm at frame entry; never turn timing on midway through a call."""
        if not self.enabled:
            return False
        try:
            now = self._finite(now)
            if self._deadline is not None and now >= self._deadline:
                self._complete_capture('deadline')
                self._next_capture = now + self.cooldown_seconds
                self._deadline = None
            if (self._deadline is None and trigger and
                    now >= self._next_capture and
                    self.capture < self.maximum_captures):
                self.capture += 1
                self._reason = str(trigger)
                self._deadline = now + self.capture_seconds
                self._capture_totals = {}
                self._capture_counts = {}
                self._capture_queue_maxima = {}
                self._capture_started = now
                self._capture_last = now
                self._capture_frames = 0
                self._capture_waits.clear()
                self._capture_selected_waits.clear()
                self._capture_wait_count = 0
                self._capture_selected_wait_count = 0
                self._receipts.clear()
                self._capture_actors = {}
                self._capture_detail_stages = {}
                self._capture_detail_counts = {}
                self._capture_detail_frames = 0
            self._frame = int(frame)
            self._now = now
            self.active = self._deadline is not None
            self.detail_active = self.active and self.detail_stride == 1
            self._stack = []
            self._stages = {}
            self._counts = {}
            self._waits = []
            self._selected_waits = []
            self._queue_snapshot = {}
            self._reset_actor_frame()
            return self.active
        except Exception:
            self._fail()
            return False

    def start(self):
        if not self.active:
            return None
        try:
            token = [self._finite(self.clock()), 0.0, self._actor_key]
            self._stack.append(token)
            return token
        except Exception:
            self._fail()
            return None

    def call(self, stage, function, *args, **kwargs):
        return call(self, stage, function, *args, **kwargs)

    def stop(self, stage, token):
        if token is None or not self.active:
            return
        try:
            if (self._phase_token is not None and len(self._stack) >= 3 and
                    self._stack[-1] is self._phase_token and
                    self._stack[-2] is self._actor_token and
                    self._stack[-3] is token):
                self.actor(None)
                if not self.active:
                    return
            if (self._actor_token is not None and len(self._stack) >= 2 and
                    self._stack[-1] is self._actor_token and
                    self._stack[-2] is token):
                self.actor(None)
                if not self.active:
                    return
            elapsed = max(0.0, self._finite(self.clock()) - token[0])
            if not self._stack or self._stack[-1] is not token:
                raise ValueError('diagnostic scope ownership changed')
            self._stack.pop()
            if self._stack:
                self._stack[-1][1] += elapsed
            own = max(0.0, elapsed - token[1])
            self._add_stage(self._stages, stage, elapsed, own)
            if token[2] in self._actors:
                self._add_stage(
                    self._actors[token[2]]['stages'], stage, elapsed, own)
        except Exception:
            self._fail()

    def count(self, name, value=1):
        if self.active:
            self._counts[name] = self._counts.get(name, 0) + int(value)
            if self._actor_key in self._actors:
                counts = self._actors[self._actor_key]['counts']
                counts[name] = counts.get(name, 0) + int(value)

    @staticmethod
    def _add_stage(stages, name, elapsed, own):
        row = stages.setdefault(name, [0, 0.0, 0.0, 0.0])
        row[0] += 1
        row[1] += elapsed
        row[2] += own
        row[3] = max(row[3], elapsed)

    def queue_added(self, key, now, ready_at):
        """Keep enqueue times outside captures so waits are not truncated."""
        if not self.enabled:
            return
        if key in self._queue:
            self.count('lane_deduplicated')
            return
        if len(self._queue) >= MAX_QUEUE_IDENTITIES:
            self.count('lane_tracking_overflow')
            return
        self._queue[key] = (now, max(now, ready_at))
        self.count('lane_added')

    def queue_reset(self):
        self.count('lane_cycle_retired', len(self._queue))
        self._queue.clear()

    def queue_retired(self, key, now, reason, selected=False):
        stamp = self._queue.pop(key, None)
        if not self.active:
            return
        self.count('lane_retired_' + reason)
        if stamp is not None and reason in ('probe', 'cache', 'distance'):
            wait = max(0.0, now - stamp[0])
            self._waits.append(wait)
            if selected:
                self._selected_waits.append(wait)

    def queue_state(self, now, eligible_keys, selected_keys,
                    cover_blocked=False):
        if not self.active:
            return
        due = 0
        oldest_wait = 0.0
        oldest_due = 0.0
        selected_due = 0.0
        selected_count = 0
        oldest_key = None
        selected_key = None
        tracked = 0
        eligibility_checked = eligible_keys is not None
        if eligible_keys is None:
            eligible_keys = self._queue
        for key in eligible_keys:
            stamp = self._queue.get(key)
            if stamp is None:
                continue
            tracked += 1
            wait = max(0.0, now - stamp[0])
            overdue = max(0.0, now - stamp[1])
            if oldest_key is None or wait > oldest_wait:
                oldest_key = key
                oldest_wait = wait
            oldest_due = max(oldest_due, overdue)
            due += int(now >= stamp[1])
            if key in selected_keys:
                selected_count += 1
                if selected_key is None or overdue > selected_due:
                    selected_key = key
                selected_due = max(selected_due, overdue)
        self._queue_snapshot = {
            'identities': len(self._queue), 'eligible_tracked': tracked,
            'due': due, 'selected_pending': selected_count,
            'oldest_wait_ms': round(oldest_wait * 1000.0, 3),
            'oldest_due_ms': round(oldest_due * 1000.0, 3),
            'selected_oldest_due_ms': round(selected_due * 1000.0, 3),
            'cover_blocked': bool(cover_blocked),
            'eligibility_checked': eligibility_checked,
            'oldest_job': oldest_key,
            'selected_oldest_job': selected_key,
        }
        self.count('lane_service_cover_blocked', int(cover_blocked))

    def receipt_stored(self, key, now, clear):
        if not self.active:
            return
        self.count('lane_receipts_positive' if clear else 'lane_receipts_blocked')
        old = self._receipts.get(key)
        if old is not None and old[1] and not old[2]:
            self.count('lane_positive_replaced_unpublished')
        if key in self._receipts or len(self._receipts) < MAX_QUEUE_IDENTITIES:
            self._receipts[key] = [now, bool(clear), False]

    def receipt_published(self, key, sampled_at, selected):
        if not self.active:
            return
        self.count('lane_positive_publications')
        self.count('lane_selected_positive_publications', int(selected))
        receipt = self._receipts.get(key)
        if receipt is not None and receipt[0] == sampled_at:
            if not receipt[2]:
                self.count('lane_distinct_positive_published')
            receipt[2] = True

    def receipt_expired(self, key, sampled_at):
        if not self.active:
            return
        receipt = self._receipts.get(key)
        if receipt is not None and receipt[0] == sampled_at:
            self._receipts.pop(key, None)
            if receipt[1]:
                self.count('lane_positive_expired')
                if not receipt[2]:
                    self.count('lane_positive_expired_unpublished')

    @staticmethod
    def _wait_summary(values, count=None):
        ordered = sorted(values)
        def percentile(fraction):
            if not ordered:
                return 0.0
            index = (len(ordered) - 1) * fraction
            low = int(index)
            high = min(low + 1, len(ordered) - 1)
            return round(1000.0 * (
                ordered[low] + (ordered[high] - ordered[low]) *
                (index - low)), 3)
        return {
            'count': len(values) if count is None else count,
            'kept': len(values), 'p50_ms': percentile(0.50),
            'p95_ms': percentile(0.95), 'max_kept_ms': percentile(1.0),
        }

    @staticmethod
    def _stage_rows(stages):
        return dict((name, {
            'calls': row[0], 'total_ms': round(row[1] * 1000.0, 3),
            'self_ms': round(row[2] * 1000.0, 3),
            'max_ms': round(row[3] * 1000.0, 3),
        }) for name, row in stages.items())

    def finish_frame(self):
        if not self.active:
            return None
        try:
            if self._stack:
                raise ValueError('unfinished diagnostic scope')
            result = {
                'capture': self.capture, 'trigger': self._reason,
                'frame': self._frame, 'authority_time': self._now,
                'stages': self._stage_rows(self._stages),
                'counts': dict(self._counts),
                'slices': list(self._slices),
                'actors_tracked': len(self._actors),
                'detail_sampled': self.detail_active,
                'actors': self._actor_rows(
                    self._actors, MAX_FRAME_ACTORS_REPORTED),
                'queue': dict(self._queue_snapshot),
                'completed_wait': self._wait_summary(self._waits),
                'selected_completed_wait': self._wait_summary(
                    self._selected_waits),
            }
            for name, row in self._stages.items():
                total = self._capture_totals.setdefault(
                    name, [0, 0.0, 0.0, 0.0])
                for index in (0, 1, 2):
                    total[index] += row[index]
                total[3] = max(total[3], row[3])
            for name, count in self._counts.items():
                self._capture_counts[name] = (
                    self._capture_counts.get(name, 0) + count)
            if self.detail_active:
                self._capture_detail_frames += 1
                for name, row in self._stages.items():
                    total = self._capture_detail_stages.setdefault(
                        name, [0, 0.0, 0.0, 0.0])
                    for index in (0, 1, 2):
                        total[index] += row[index]
                    total[3] = max(total[3], row[3])
                for name, count in self._counts.items():
                    self._capture_detail_counts[name] = (
                        self._capture_detail_counts.get(name, 0) + count)
            for (unused_slice, bot_id), actor in self._actors.items():
                key = (0, bot_id)
                if key not in self._capture_actors:
                    if len(self._capture_actors) >= MAX_CAPTURE_ACTORS:
                        continue
                    self._capture_actors[key] = {'stages': {}, 'counts': {}}
                total = self._capture_actors[key]
                for name, row in actor['stages'].items():
                    summed = total['stages'].setdefault(
                        name, [0, 0.0, 0.0, 0.0])
                    for index in (0, 1, 2):
                        summed[index] += row[index]
                    summed[3] = max(summed[3], row[3])
                for name, count in actor['counts'].items():
                    total['counts'][name] = total['counts'].get(name, 0) + count
            for name, value in self._queue_snapshot.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._capture_queue_maxima[name] = max(
                        self._capture_queue_maxima.get(name, 0), value)
            self._capture_last = self._now
            self._capture_frames += 1
            self._capture_waits.extend(self._waits)
            self._capture_selected_waits.extend(self._selected_waits)
            self._capture_wait_count += len(self._waits)
            self._capture_selected_wait_count += len(self._selected_waits)
            self.active = False
            return result
        except Exception:
            self._fail()
            return None

    def _actor_rows(self, actors, limit=None):
        ordered = sorted(actors.items(), key=lambda item: (
            -item[1]['stages'].get('bot.actor', (0, 0.0))[1], item[0]))
        if limit is not None:
            ordered = ordered[:limit]
        return [{'slice': key[0], 'bot': key[1],
                 'stages': self._stage_rows(row['stages']),
                 'counts': dict(row['counts'])}
                for key, row in ordered]

    def _complete_capture(self, reason):
        self._completed.append({
            'capture': self.capture, 'trigger': self._reason,
            'end_reason': reason,
            'authority_start': self._capture_started,
            'authority_last_frame': self._capture_last,
            'frames': self._capture_frames,
            'stages': self._stage_rows(self._capture_totals),
            'counts': dict(self._capture_counts),
            'actors': self._actor_rows(self._capture_actors),
            'detail': {
                'stride': self.detail_stride,
                'selection': 'rotating_control_slot',
                'frames': self._capture_detail_frames,
                'stages': self._stage_rows(self._capture_detail_stages),
                'counts': dict(self._capture_detail_counts),
            },
            'queue_maxima': dict(self._capture_queue_maxima),
            'completed_wait': self._wait_summary(
                self._capture_waits, self._capture_wait_count),
            'selected_completed_wait': self._wait_summary(
                self._capture_selected_waits,
                self._capture_selected_wait_count),
        })

    def drain_completed(self):
        completed = self._completed
        self._completed = []
        return completed

    def close(self):
        if self._deadline is not None:
            self._complete_capture('round_end')
        self._deadline = None
        self.active = False
        self._stack = []
        self._queue.clear()
        self._receipts.clear()
        self._reset_actor_frame()
