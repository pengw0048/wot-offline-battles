"""Function-level Python profiles of the hidden worker's main thread.

Diagnostic evidence only.  The profiler drives the interpreter's builtin
``_lsprof`` module (the engine behind ``cProfile``) directly, so it does not
depend on the pure-Python ``cProfile``/``pstats`` modules being shipped in the
client library.  Windows are scheduled relative to the first live battle frame
observed by the worker.  Each window writes one human-readable text report and
one ``pstats``-compatible marshal file next to the worker status file.

A failure anywhere in this module disables further profiling for the process
and is reported once; it never propagates into the frame that called it.
"""

from __future__ import print_function

import marshal
import os
import sys
import time


LOG_PREFIX = '[Offline LAN 0.9.22] LSPROF'
REPORT_PREFIX = 'offline-worker-lsprof'
# (seconds after the first live frame, window duration in seconds)
DEFAULT_WINDOWS = ((25.0, 30.0), (85.0, 30.0), (145.0, 30.0))
TOP_ROWS = 200
TOP_MODULE_ROWS = 40
_PLATFORM_VARIABLES = (
    'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432',
    'PROCESSOR_IDENTIFIER', 'NUMBER_OF_PROCESSORS')

_CLOCK = getattr(time, 'perf_counter', None)
if not callable(_CLOCK):
    _CLOCK = time.clock


def _default_profiler_factory():
    import _lsprof
    return _lsprof.Profiler()


def platform_summary(environ=None):
    """Describe the interpreter and CPU without importing optional modules."""
    environ = os.environ if environ is None else environ
    parts = []
    for name in _PLATFORM_VARIABLES:
        parts.append('%s=%s' % (name.lower(), environ.get(name, '-') or '-'))
    machine = '-'
    try:
        import platform
        machine = platform.machine() or '-'
    except Exception:
        pass
    parts.append('machine=%s' % machine)
    parts.append('python=%s' % sys.version.split()[0])
    parts.append('maxsize=%d' % sys.maxsize)
    parts.append('platform=%s' % sys.platform)
    return ' '.join(parts)


def _label(code):
    """Return the pstats key for one ``_lsprof`` code reference."""
    if isinstance(code, str):
        return ('~', 0, code)
    return (code.co_filename, code.co_firstlineno, code.co_name)


def _short_label(key):
    filename, line, name = key
    if filename == '~':
        return name
    base = filename.replace('\\', '/')
    parts = base.split('/')
    if len(parts) > 3:
        base = '/'.join(parts[-3:])
    return '%s:%d(%s)' % (base, line, name)


def _module_label(key):
    filename = key[0]
    if filename == '~':
        return '<builtins>'
    base = filename.replace('\\', '/')
    parts = base.split('/')
    if len(parts) > 2:
        base = '/'.join(parts[-2:])
    return base


def pstats_payload(entries):
    """Convert ``_lsprof`` entries into cProfile's ``pstats`` dictionary."""
    stats = {}
    callers_by_code = {}
    for entry in entries:
        key = _label(entry.code)
        callcount = int(entry.callcount)
        primitive = callcount - int(entry.reccallcount)
        callers = {}
        callers_by_code[id(entry.code)] = callers
        stats[key] = (primitive, callcount, float(entry.inlinetime),
                      float(entry.totaltime), callers)
    for entry in entries:
        if not entry.calls:
            continue
        caller = _label(entry.code)
        for subentry in entry.calls:
            callers = callers_by_code.get(id(subentry.code))
            if callers is None:
                continue
            callcount = int(subentry.callcount)
            primitive = callcount - int(subentry.reccallcount)
            inline = float(subentry.inlinetime)
            total = float(subentry.totaltime)
            previous = callers.get(caller)
            if previous is not None:
                callcount += previous[0]
                primitive += previous[1]
                inline += previous[2]
                total += previous[3]
            callers[caller] = (callcount, primitive, inline, total)
    return stats


def format_report(entries, header, top_rows=TOP_ROWS,
                  top_module_rows=TOP_MODULE_ROWS):
    """Render one text report from ``_lsprof`` entries."""
    rows = []
    module_inline = {}
    total_inline = 0.0
    total_calls = 0
    for entry in entries:
        key = _label(entry.code)
        callcount = int(entry.callcount)
        inline = float(entry.inlinetime)
        total = float(entry.totaltime)
        rows.append((key, callcount, inline, total))
        total_inline += inline
        total_calls += callcount
        module = _module_label(key)
        module_inline[module] = module_inline.get(module, 0.0) + inline
    lines = []
    for name in sorted(header):
        lines.append('%s: %s' % (name, header[name]))
    lines.append('profiled_functions: %d' % len(rows))
    lines.append('profiled_calls: %d' % total_calls)
    lines.append('profiled_inline_seconds: %.3f' % total_inline)
    lines.append('')
    lines.append('=== inline time by module (top %d) ===' % top_module_rows)
    lines.append('%12s %8s  module' % ('inline_s', 'share'))
    for module, inline in sorted(
            module_inline.items(), key=lambda item: -item[1])[:top_module_rows]:
        share = (inline / total_inline * 100.0) if total_inline > 0.0 else 0.0
        lines.append('%12.3f %7.2f%%  %s' % (inline, share, module))
    for title, index in (('inline (self) time', 2), ('total (cumulative) time', 3)):
        lines.append('')
        lines.append('=== functions by %s (top %d) ===' % (title, top_rows))
        lines.append('%10s %10s %10s %10s %10s  function' % (
            'ncalls', 'tottime', 'percall', 'cumtime', 'percall'))
        for key, callcount, inline, total in sorted(
                rows, key=lambda row: -row[index])[:top_rows]:
            per_inline = inline / callcount if callcount else 0.0
            per_total = total / callcount if callcount else 0.0
            lines.append('%10d %10.4f %10.6f %10.4f %10.6f  %s' % (
                callcount, inline, per_inline, total, per_total,
                _short_label(key)))
    lines.append('')
    return '\n'.join(lines) + '\n'


class WorkerProfiler(object):
    """Schedule bounded ``_lsprof`` windows across one worker's live frames."""

    def __init__(self, output_dir, windows=DEFAULT_WINDOWS, writer=None,
                 clock=None, profiler_factory=None, environ=None):
        self._output_dir = output_dir
        self._windows = tuple(
            (float(start), float(duration)) for start, duration in windows)
        self._writer = writer or sys.stdout.write
        self._clock = clock or _CLOCK
        self._profiler_factory = (
            profiler_factory or _default_profiler_factory)
        self._environ = environ
        self._failed = False
        self._platform_logged = False
        self._live_since = None
        self._round_id = None
        self._window_index = 0
        self._active = None
        self._active_started = 0.0
        self._active_frames = 0
        self.reports = []

    @property
    def active(self):
        return self._active is not None

    def tick(self, battle_live, round_id=None):
        """Advance the schedule from the start of one worker frame."""
        if self._failed:
            return False
        try:
            return self._tick(bool(battle_live), round_id)
        except Exception as error:
            self._failed = True
            try:
                if self._active is not None:
                    self._active.disable()
            except Exception:
                pass
            self._active = None
            self._writer('%s disabled after error: %r\n' % (LOG_PREFIX, error))
            return False

    def _tick(self, battle_live, round_id):
        now = self._clock()
        if not self._platform_logged:
            self._platform_logged = True
            self._writer('%s platform %s\n' % (
                LOG_PREFIX, platform_summary(self._environ)))
        if round_id != self._round_id:
            if self._active is not None:
                self._finish(now, 'round_changed')
            self._round_id = round_id
            self._live_since = None
            self._window_index = 0
        if not battle_live:
            if self._active is not None:
                self._finish(now, 'battle_ended')
            self._live_since = None
            return False
        if self._live_since is None:
            self._live_since = now
            self._writer('%s live round=%s windows=%s\n' % (
                LOG_PREFIX, round_id, ','.join(
                    '%g+%g' % window for window in self._windows)))
        elapsed = now - self._live_since
        if self._active is not None:
            self._active_frames += 1
            start, duration = self._windows[self._window_index - 1]
            if elapsed >= start + duration:
                self._finish(now, 'complete')
            return True
        if self._window_index >= len(self._windows):
            return False
        start, duration = self._windows[self._window_index]
        if elapsed < start:
            return False
        self._window_index += 1
        profiler = self._profiler_factory()
        self._active = profiler
        self._active_started = now
        self._active_frames = 0
        self._writer('%s begin window=%d round=%s live_elapsed=%.1f '
                     'duration=%.1f\n' % (
                         LOG_PREFIX, self._window_index, round_id,
                         elapsed, duration))
        profiler.enable(subcalls=True, builtins=True)
        return True

    def _finish(self, now, reason):
        profiler = self._active
        self._active = None
        profiler.disable()
        entries = profiler.getstats()
        wall = max(0.0, now - self._active_started)
        header = {
            'reason': reason,
            'round_id': self._round_id,
            'window': self._window_index,
            'wall_seconds': '%.3f' % wall,
            'frames': self._active_frames,
            'frames_per_second': '%.2f' % (
                self._active_frames / wall if wall > 0.0 else 0.0),
            'platform': platform_summary(self._environ),
        }
        stem = '%s-round%s-w%d' % (
            REPORT_PREFIX, self._round_id, self._window_index)
        text_path = os.path.join(self._output_dir, stem + '.txt')
        stats_path = os.path.join(self._output_dir, stem + '.pstats')
        report = format_report(entries, header)
        self._write_bytes(text_path, report.encode('utf-8'))
        stats_written = True
        try:
            with open(stats_path, 'wb') as stream:
                marshal.dump(pstats_payload(entries), stream)
        except Exception:
            stats_written = False
        self.reports.append(text_path)
        self._writer('%s end window=%d reason=%s wall=%.1fs frames=%d '
                     'report=%s pstats=%s\n' % (
                         LOG_PREFIX, self._window_index, reason, wall,
                         self._active_frames, text_path,
                         stats_path if stats_written else 'unavailable'))

    def _write_bytes(self, path, payload):
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, 'wb') as stream:
            stream.write(payload)


class DisabledProfiler(object):
    """Stand-in used when the profiler cannot be constructed."""

    active = False
    reports = ()

    def tick(self, battle_live, round_id=None):
        return False


def create_for_worker(output_dir=None, writer=None):
    """Build the worker profiler, or a no-op when the runtime lacks support."""
    writer = writer or sys.stdout.write
    try:
        if output_dir is None:
            from gui.mods.offline_lan_0922 import config as port_config
            output_dir = os.path.dirname(port_config.CONFIG_PATH)
        import _lsprof  # noqa: F401  (proves the builtin exists)
        return WorkerProfiler(output_dir, writer=writer)
    except Exception as error:
        try:
            writer('%s unavailable: %r\n' % (LOG_PREFIX, error))
        except Exception:
            pass
        return DisabledProfiler()
