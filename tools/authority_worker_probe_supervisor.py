#!/usr/bin/env python3
"""External window supervisor for the opt-in #1513 authority probe.

Run this process before enabling the client's ``window_hidden`` stage.  It
watches the client's JSONL report, matches an exact process id, hides only a
windowed top-level window owned by that process, and restores the original
window placement from an external finally block.  Fullscreen windows are
rejected conservatively.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time


DEFAULT_REPORT = os.path.join(
    '.', 'mods', 'configs', 'offline_lan_0922',
    'authority_worker_probe.jsonl')


class RECT(ctypes.Structure):
    _fields_ = [
        ('left', wintypes.LONG), ('top', wintypes.LONG),
        ('right', wintypes.LONG), ('bottom', wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ('length', wintypes.UINT), ('flags', wintypes.UINT),
        ('showCmd', wintypes.UINT), ('ptMinPosition', POINT),
        ('ptMaxPosition', POINT), ('rcNormalPosition', RECT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('rcMonitor', RECT),
        ('rcWork', RECT), ('dwFlags', wintypes.DWORD),
    ]


class WindowSupervisor(object):
    GW_OWNER = 4
    GWL_STYLE = -16
    WS_CHILD = 0x40000000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    MONITOR_DEFAULTTONEAREST = 2
    SW_HIDE = 0
    SW_RESTORE = 9

    def __init__(self, process_id, user32=None):
        if os.name != 'nt' and user32 is None:
            raise RuntimeError('the window supervisor requires Windows')
        self.process_id = int(process_id)
        if self.process_id <= 0:
            raise ValueError('process id must be positive')
        self.user32 = user32 or ctypes.windll.user32
        self.window = None
        self.placement = None
        self.hidden = False
        self._configure_api()

    def _configure_api(self):
        if not hasattr(self.user32, 'EnumWindows'):
            return
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.GetWindow.restype = wintypes.HWND
        self.user32.GetWindowLongW.restype = wintypes.LONG
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetWindowPlacement.restype = wintypes.BOOL
        self.user32.SetWindowPlacement.restype = wintypes.BOOL
        self.user32.ShowWindowAsync.restype = wintypes.BOOL
        self.user32.MonitorFromWindow.restype = wintypes.HMONITOR

    def _window_process_id(self, window):
        process_id = wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(
            window, ctypes.byref(process_id))
        return int(process_id.value)

    def _window_rect(self, window):
        rect = RECT()
        if not self.user32.GetWindowRect(window, ctypes.byref(rect)):
            raise RuntimeError('GetWindowRect failed')
        return rect

    @staticmethod
    def _area(rect):
        return max(0, int(rect.right - rect.left)) * max(
            0, int(rect.bottom - rect.top))

    def find_window(self):
        matches = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(window, unused_lparam):
            if (self._window_process_id(window) != self.process_id or
                    not self.user32.IsWindowVisible(window) or
                    self.user32.GetWindow(window, self.GW_OWNER)):
                return True
            style = int(self.user32.GetWindowLongW(window, self.GWL_STYLE))
            if style & self.WS_CHILD:
                return True
            try:
                rect = self._window_rect(window)
            except RuntimeError:
                return True
            matches.append((self._area(rect), window, rect, style))
            return True

        visitor = callback_type(visit)
        if not self.user32.EnumWindows(visitor, 0):
            raise RuntimeError('EnumWindows failed')
        if not matches:
            raise RuntimeError('no visible top-level window belongs to pid %s' %
                               self.process_id)
        matches.sort(key=lambda value: value[0], reverse=True)
        unused_area, window, rect, style = matches[0]
        if self.user32.IsIconic(window):
            raise RuntimeError('the target game window is minimised')
        if not (style & (self.WS_CAPTION | self.WS_THICKFRAME)):
            raise RuntimeError('fullscreen or borderless windows are refused')
        monitor = self.user32.MonitorFromWindow(
            window, self.MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not self.user32.GetMonitorInfoW(
                monitor, ctypes.byref(info)):
            raise RuntimeError('monitor geometry is unavailable')
        if (rect.left <= info.rcMonitor.left and
                rect.top <= info.rcMonitor.top and
                rect.right >= info.rcMonitor.right and
                rect.bottom >= info.rcMonitor.bottom):
            raise RuntimeError('fullscreen-sized windows are refused')
        return window

    def hide(self):
        window = self.find_window()
        time.sleep(0.1)
        if self.find_window() != window:
            raise RuntimeError('target window handle was not stable')
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(placement)
        if not self.user32.GetWindowPlacement(
                window, ctypes.byref(placement)):
            raise RuntimeError('GetWindowPlacement failed')
        # ShowWindowAsync's return value does not prove the queued operation
        # ran. Visibility readback below is the actual acceptance boundary.
        self.user32.ShowWindowAsync(window, self.SW_HIDE)
        self.window = window
        self.placement = placement
        self.hidden = True
        self._wait_visibility(window, False, 2.0)

    def restore(self):
        window = self.window
        placement = self.placement
        self.hidden = False
        self.window = None
        self.placement = None
        if not window or not self.user32.IsWindow(window):
            return False
        if self._window_process_id(window) != self.process_id:
            raise RuntimeError(
                'target window ownership changed before restore')
        if placement is not None and not self.user32.SetWindowPlacement(
                window, ctypes.byref(placement)):
            raise RuntimeError('SetWindowPlacement failed')
        show_command = int(getattr(placement, 'showCmd', self.SW_RESTORE))
        if show_command == self.SW_HIDE:
            show_command = self.SW_RESTORE
        self.user32.ShowWindowAsync(window, show_command)
        self._wait_visibility(window, True, 3.0)
        return True

    def _wait_visibility(self, window, visible, timeout):
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            if not self.user32.IsWindow(window):
                raise RuntimeError('target window ceased to exist')
            if self._window_process_id(window) != self.process_id:
                raise RuntimeError('target window ownership changed')
            if bool(self.user32.IsWindowVisible(window)) == bool(visible):
                return
            time.sleep(0.05)
        raise RuntimeError(
            'window did not become %s' % ('visible' if visible else 'hidden'))


def append_record(path, record):
    record = dict(record)
    record.setdefault('schema', 1)
    record.setdefault('probe', 'authority_worker')
    record.setdefault('wall_time_epoch', time.time())
    payload = json.dumps(record, sort_keys=True, separators=(',', ':'))
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(payload + '\n')
        stream.flush()


def _read_records(path, offset):
    records = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as stream:
            stream.seek(offset)
            while True:
                line_start = stream.tell()
                line = stream.readline()
                if not line:
                    return records, line_start
                if not line.endswith('\n'):
                    return records, line_start
                offset = stream.tell()
                try:
                    records.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
    except FileNotFoundError:
        return records, offset


def wait_for_hidden_stage(path, process_id, started_at, timeout):
    deadline = time.monotonic() + float(timeout)
    offset = 0
    while time.monotonic() < deadline:
        records, offset = _read_records(path, offset)
        for record in records:
            if (record.get('probe') == 'authority_worker' and
                    record.get('event') == 'stage_start' and
                    record.get('stage') == 'window_hidden' and
                    int(record.get('process_id', -1)) == process_id and
                    record.get('run_id') and
                    float(record.get('wall_time_epoch', 0.0)) >=
                    started_at - 1.0):
                return record, offset
        time.sleep(0.1)
    raise RuntimeError('timed out waiting for window_hidden stage')


def monitor_hidden_stage(path, offset, process_id, run_id, duration,
                         heartbeat_timeout):
    started = time.monotonic()
    deadline = started + float(duration)
    last_heartbeat = started
    while time.monotonic() < deadline:
        records, offset = _read_records(path, offset)
        for record in records:
            if (record.get('probe') != 'authority_worker' or
                    int(record.get('process_id', -1)) != process_id or
                    record.get('run_id') != run_id or
                    record.get('stage') != 'window_hidden'):
                continue
            if record.get('event') == 'stage_heartbeat':
                last_heartbeat = time.monotonic()
            elif record.get('event') == 'stage_result':
                return 'client_stage_ended', offset
        if time.monotonic() - last_heartbeat > heartbeat_timeout:
            raise RuntimeError(
                'client callback heartbeat stalled while hidden')
        time.sleep(0.1)
    return 'duration_complete', offset


def run(args):
    if os.name != 'nt':
        raise RuntimeError('the authority worker supervisor requires Windows')
    started_at = time.time()
    stage, offset = wait_for_hidden_stage(
        args.report, args.pid, started_at, args.timeout)
    run_id = str(stage['run_id'])
    duration = (float(args.duration) if args.duration is not None else
                float(stage.get('stage_seconds', 15.0)))
    duration = max(0.1, min(duration, 60.0))
    supervisor = WindowSupervisor(args.pid)
    result = {
        'event': 'supervisor_stage_result',
        'stage': 'window_hidden',
        'process_id': args.pid,
        'run_id': run_id,
        'requested_seconds': duration,
        'hidden': False,
        'restored': False,
        'status': 'failed',
    }
    try:
        supervisor.hide()
        result['hidden'] = True
        append_record(args.report, {
            'event': 'supervisor_stage_start',
            'stage': 'window_hidden',
            'process_id': args.pid,
            'run_id': run_id,
            'requested_seconds': duration,
        })
        completion, offset = monitor_hidden_stage(
            args.report, offset, args.pid, run_id, duration,
            max(1.0, float(args.heartbeat_timeout)))
        result['monitor_completion'] = completion
        result['status'] = 'completed'
    except BaseException as error:
        result['error'] = str(error)
        if isinstance(error, KeyboardInterrupt):
            result['status'] = 'interrupted'
    finally:
        try:
            result['restored'] = bool(supervisor.restore())
        except BaseException as error:
            result['restore_error'] = str(error)
            result['status'] = 'failed'
        append_record(args.report, result)
    return 0 if result['status'] == 'completed' and result['restored'] else 2


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Externally supervise the #1513 hidden-window probe stage.')
    parser.add_argument('--pid', type=int, required=True,
                        help='Exact WorldOfTanks.exe process id.')
    parser.add_argument('--report', default=DEFAULT_REPORT,
                        help='Client authority_worker_probe.jsonl path.')
    parser.add_argument('--timeout', type=float, default=120.0,
                        help='Seconds to wait for the client stage request.')
    parser.add_argument('--duration', type=float,
                        help='Override the requested hide duration (max 60s).')
    parser.add_argument(
        '--heartbeat-timeout', type=float, default=2.5,
        help='Restore immediately after this many seconds without a matching heartbeat.')
    return parser.parse_args(argv)


def main(argv=None):
    try:
        return run(parse_args(argv))
    except (OSError, RuntimeError, ValueError) as error:
        sys.stderr.write('authority worker probe supervisor: %s\n' % error)
        return 2


if __name__ == '__main__':
    sys.exit(main())
