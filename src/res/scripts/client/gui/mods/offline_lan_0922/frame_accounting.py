"""Bounded accounting for work between render callbacks.

Wall time is not CPU time. Win32 CPU counters have coarse resolution and are
reported only as window totals. Neither counter separates Ready from Waiting,
nor identifies GPU work; the companion WPR capture supplies that evidence.
"""

import functools
import math
import os
import sys

from . import visible_diagnostics


def _finite(value):
    return not (math.isnan(value) or math.isinf(value))


def merge_costs(target, rows):
    for name, values in rows.items():
        calls, total, own, maximum = values
        row = target.setdefault(name, [0, 0.0, 0.0, 0.0])
        row[0] += calls
        row[1] += total
        row[2] += own
        row[3] = max(row[3], maximum)


class PollCosts(object):
    """Main-thread counters; never retain a message or touch transport locks."""

    def __init__(self, clock):
        self.clock = clock
        self.sequence = 0
        self.profile_active = False
        self.drain()

    def drain(self):
        previous = getattr(self, 'data', {})
        self.data = {
            'polls': 0, 'wall_seconds': 0.0, 'max_poll_seconds': 0.0,
            'batch_messages': 0, 'max_batch_messages': 0,
            'handled_messages': 0, 'snapshot_messages': 0,
            'event_messages': 0, 'coalesced_snapshots': 0,
            'age_samples': 0, 'age_seconds': 0.0, 'max_age_seconds': 0.0,
            'sampled_polls': 0, 'failed_samples': 0, 'costs': {},
            'diagnostic_failures': 0,
        }
        return previous

    def batch(self, messages, now):
        data = self.data
        data['batch_messages'] += len(messages)
        data['max_batch_messages'] = max(data['max_batch_messages'], len(messages))
        for message in messages:
            if not isinstance(message, dict):
                continue
            received = message.get('_client_received_time')
            if isinstance(received, (int, float)) and _finite(received):
                age = max(0.0, now - received)
                data['age_samples'] += 1
                data['age_seconds'] += age
                data['max_age_seconds'] = max(data['max_age_seconds'], age)


def poll(method):
    @functools.wraps(method)
    def measured(self, *args, **kwargs):
        observer = getattr(self, '_poll_costs', None)
        if observer is None:
            return method(self, *args, **kwargs)
        previous = self._visible_frame_costs
        try:
            observer.sequence += 1
            costs = (None if observer.profile_active else
                     visible_diagnostics.new_frame(observer.sequence, observer.clock))
            started = observer.clock()
            if not _finite(started):
                raise ValueError('invalid poll clock')
        except Exception:
            observer.data['diagnostic_failures'] += 1
            self._poll_costs = None
            return method(self, *args, **kwargs)
        self._visible_frame_costs = costs
        try:
            return visible_diagnostics.call(
                self, 'net.poll', method, self, *args, **kwargs)
        finally:
            self._visible_frame_costs = previous
            # A callback that ends/replaces its round must not publish into
            # the new owner's counters (or subtract its loading time there).
            try:
                if getattr(self, '_poll_costs', None) is observer:
                    elapsed = observer.clock() - started
                    if not _finite(elapsed) or elapsed < 0:
                        raise ValueError('invalid poll clock')
                    data = observer.data
                    data['polls'] += 1
                    data['wall_seconds'] += elapsed
                    data['max_poll_seconds'] = max(data['max_poll_seconds'], elapsed)
                    if costs is not None:
                        snapshot = costs.snapshot()
                        if snapshot['failed']:
                            data['failed_samples'] += 1
                        else:
                            data['sampled_polls'] += 1
                            merge_costs(data['costs'], snapshot['stages'])
            except Exception:
                observer.data['diagnostic_failures'] += 1
                if getattr(self, '_poll_costs', None) is observer:
                    self._poll_costs = None
    return measured


def message(method):
    @functools.wraps(method)
    def measured(self, payload, *args, **kwargs):
        observer = getattr(self, '_poll_costs', None)
        kind = payload.get('type') if isinstance(payload, dict) else None
        if observer is not None:
            observer.data['handled_messages'] += 1
            if kind in ('snapshot', 'events'):
                key = 'snapshot_messages' if kind == 'snapshot' else 'event_messages'
                observer.data[key] += 1
        name = 'net.' + kind if kind in ('snapshot', 'events') else 'net.other'
        return visible_diagnostics.call(self, name, method, self, payload,
                                        *args, **kwargs)
    return measured


class CpuClock(object):
    """Use the already loaded #1513 bridge, without a new Python C API RVA."""

    def __init__(self):
        bridge = sys.modules.get('offline_instance_guard_native')
        self.reader = getattr(bridge, 'current_thread_cpu_ms', None)
        self.process_reader = getattr(bridge, 'current_process_cpu_ms', None)
        self.thread_id = None
        self.status = 'native_counters_unavailable'
        identity = getattr(bridge, 'current_thread_id', None)
        if callable(self.reader) and callable(self.process_reader) and callable(identity):
            try:
                self.thread_id = int(identity()) & 0xffffffff
                self.status = 'available'
            except Exception:
                pass

    def read(self):
        if self.status != 'available':
            return None
        try:
            thread = int(self.reader())
            process = int(self.process_reader())
            if thread < 0 or process < 0:
                raise ValueError('native CPU counter failed')
            return thread, process
        except Exception:
            self.status = 'native_counter_failed'
            return None


class Window(object):
    """Accumulate the same sealed entry-to-entry intervals as PERF summary."""

    def __init__(self):
        self.frames = 0
        self.wall = 0.0
        self.phases = {}
        self.scene = {}
        self.cpu_frames = 0
        self.cpu_wall = 0.0
        self.thread_cpu = 0
        self.callback_cpu = 0
        self.between_cpu = 0
        self.process_cpu = 0
        self.network = {}
        self.network_costs = {}
        self.profile_frames = 0
        self.clean = [0, 0.0, 0.0, 0.0]
        self.clean_phases = {}

    def add(self, row):
        self.frames += 1
        self.wall += row['wall_gap']
        context = row.get('context', {})
        phase = context.get('phase', 'unknown')
        self.phases[phase] = self.phases.get(phase, 0) + 1
        if context.get('function_profile'):
            self.profile_frames += 1
        else:
            clean_phase = self.clean_phases.setdefault(phase, [0, 0.0, 0.0, 0.0])
            self.clean[0] += 1
            clean_phase[0] += 1
            for index, key in enumerate(('wall_gap', 'exec', 'outside'), 1):
                self.clean[index] += row[key]
                clean_phase[index] += row[key]
        for name, value in context.get('scene', {}).items():
            entry = self.scene.setdefault(name, [0, 0.0, value, value])
            entry[0] += 1
            entry[1] += value
            entry[2] = min(entry[2], value)
            entry[3] = max(entry[3], value)
        first, end, following = (row.get(name) for name in
                                 ('cpu_start', 'cpu_end', 'cpu_next'))
        if (first is not None and end is not None and following is not None
                and first[0] <= end[0] <= following[0]
                and first[1] <= end[1] <= following[1]):
            self.cpu_frames += 1
            self.cpu_wall += row['wall_gap']
            self.thread_cpu += following[0] - first[0]
            self.callback_cpu += end[0] - first[0]
            self.between_cpu += following[0] - end[0]
            self.process_cpu += following[1] - first[1]
        for name, value in row.get('network', {}).items():
            if name == 'costs':
                merge_costs(self.network_costs, value)
            elif name.startswith('max_'):
                self.network[name] = max(self.network.get(name, 0), value)
            else:
                self.network[name] = self.network.get(name, 0) + value

    def snapshot(self, clock):
        frames = max(1, self.frames)
        polls = max(1, self.network.get('sampled_polls', 0))
        result = {
            'schema': 1, 'pid': os.getpid(), 'thread_id': clock.thread_id,
            'frames': self.frames, 'seconds': self.wall,
            'phase_frames': self.phases,
            'scene_samples': dict((name, {
                'samples': value[0], 'mean': value[1] / value[0],
                'min': value[2], 'max': value[3]})
                for name, value in self.scene.items()),
            'network': dict(self.network),
            'function_profile_frames': self.profile_frames,
            'unprofiled_phase_totals': self.clean_phases,
            'phase_total_fields': ['frames', 'seconds', 'exec_seconds', 'outside_seconds'],
            'unprofiled': {
                'frames': self.clean[0], 'seconds': self.clean[1],
                'fps': self.clean[0] / self.clean[1] if self.clean[1] else None,
                'exec_ms_per_frame': self.clean[2] * 1000 / max(1, self.clean[0]),
                'outside_ms_per_frame': self.clean[3] * 1000 / max(1, self.clean[0]),
            },
            'cpu': {'status': clock.status, 'valid_frames': self.cpu_frames,
                    'valid_wall_ms': self.cpu_wall * 1000,
                    'thread_ms': self.thread_cpu if self.cpu_frames else None,
                    'callback_ms': self.callback_cpu if self.cpu_frames else None,
                    'between_callbacks_ms': self.between_cpu if self.cpu_frames else None,
                    'process_ms': self.process_cpu if self.cpu_frames else None,
                    'resolution': 'OS_accounting_quantized_to_ms'},
            'outside_definition': 'gap_minus_frame_minus_known_callbacks',
            'between_cpu_includes': 'LAN_poll_other_Python_native_and_diagnostics',
        }
        result['network']['wall_ms_per_frame'] = (
            self.network.get('wall_seconds', 0) * 1000 / frames)
        result['network']['sampled_costs'] = dict((name, {
            'calls_per_sample': float(row[0]) / polls,
            'total_ms_per_sample': row[1] * 1000 / polls,
            'self_ms_per_sample': row[2] * 1000 / polls,
            'max_call_ms': row[3] * 1000})
            for name, row in self.network_costs.items())
        return result


class FunctionProfiles(object):
    """Two short main-thread profiles spanning *all* Python callbacks.

    These deliberately perturb the measured workload. Their frame intervals
    are tagged separately; use them for function attribution, never FPS gains.
    Native engine work with no Python call boundary is not visible to _lsprof.
    """

    WINDOWS = ((35.0, 2.0), (95.0, 2.0))

    def __init__(self, clock, writer, factory=None):
        self.clock = clock
        self.writer = writer
        self.factory = factory
        self.live_since = None
        self.index = 0
        self.active = None
        self.failed = False
        self.started = 0.0
        self.round_id = None

    def tick(self, live, round_id):
        was_active = self.active is not None
        if self.failed:
            return was_active
        try:
            now = self.clock()
            if not live:
                self.stop('battle_ended')
                return was_active
            if self.round_id is not None and self.round_id != round_id:
                self.stop('round_changed')
                self.failed = True
                return was_active
            if self.live_since is None:
                self.live_since = now
                self.round_id = round_id
            elapsed = now - self.live_since
            if self.active is not None:
                if now - self.started >= self.WINDOWS[self.index - 1][1]:
                    self.stop('complete')
            elif self.index < len(self.WINDOWS) and elapsed >= self.WINDOWS[self.index][0]:
                if sys.getprofile() is not None:
                    raise RuntimeError('another Python profiler is active')
                if self.factory is None:
                    import _lsprof
                    self.factory = _lsprof.Profiler
                self.active = self.factory()
                self.index += 1
                self.started = now
                self.writer('function_profile_begin', {
                    'schema': 1, 'round': round_id, 'window': self.index,
                    'pid': os.getpid(), 'duration_seconds': self.WINDOWS[self.index - 1][1]})
                self.active.enable(subcalls=True, builtins=True)
        except Exception as error:
            self.stop('failed')
            self.failed = True
            self.writer('function_profile_unavailable', {
                'schema': 1, 'reason': str(error)[:240]})
        return was_active or self.active is not None

    def stop(self, reason):
        profiler = self.active
        if profiler is None:
            return
        self.active = None
        try:
            profiler.disable()
            elapsed = max(0.0, self.clock() - self.started)
            entries = profiler.getstats()
            selected = sorted(entries, key=lambda row: row.inlinetime, reverse=True)[:40]
            for entry in sorted(entries, key=lambda row: row.totaltime, reverse=True)[:20]:
                if entry not in selected:
                    selected.append(entry)
            rows = []
            for entry in selected:
                code = entry.code
                name = ('%s:%d:%s' % (code.co_filename, code.co_firstlineno, code.co_name)
                        if hasattr(code, 'co_filename') else str(code))
                rows.append([name[-240:], entry.callcount, entry.reccallcount,
                             entry.inlinetime * 1000, entry.totaltime * 1000])
            self.writer('function_profile', {
                'schema': 1, 'round': self.round_id, 'window': self.index,
                'reason': reason, 'seconds': elapsed, 'function_count': len(entries),
                'fields': ['function', 'calls', 'recursive_calls', 'self_ms', 'total_ms'],
                'rows': rows})
        except Exception as error:
            self.failed = True
            self.writer('function_profile_unavailable', {
                'schema': 1, 'reason': str(error)[:240]})
