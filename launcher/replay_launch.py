"""Read-only preflight and child-environment isolation for offline replays.

Executed by the launcher session thread, never a Tk or game render callback.
This reads JSON/gzip only; it does not load pickle or issue game commands.
"""
from __future__ import annotations
import gzip
import json
import math
import os
import time

FILE_ENV = 'WOT_OFFLINE_REPLAY_FILE'
AUTO_ENV = 'WOT_OFFLINE_REPLAY_AUTOSTART'
MAX_LINE = 2 * 1024 * 1024
MAX_TOTAL = 768 * 1024 * 1024
MAX_DURATION = 14400.0
WIRE_TYPES = frozenset((
    'battle_live', 'snapshot', 'events', 'bot_observation', 'team_chat',
    'team_command', 'team_command_terminal', 'player_destructible_contact_result',
    'landing_observation_result'))


def checked_path(path):
    path = os.path.abspath(os.path.expanduser(str(path or '').strip().strip('"')))
    if not path.lower().endswith('.wotlanreplay'):
        raise ValueError('Select a .wotlanreplay file, not a native .wotreplay.')
    if not os.path.isfile(path):
        raise ValueError('The selected replay file does not exist.')
    return path


def child_environment(environment, path=None):
    """Playback settings belong to one child, never to the launcher/global env."""
    value = dict(environment)
    value.pop(FILE_ENV, None)
    value.pop(AUTO_ENV, None)
    if path:
        value[FILE_ENV] = checked_path(path)
        value[AUTO_ENV] = '1'
    return value


def validate(path, progress=None, cancelled=None):
    path = checked_path(path)
    before = os.stat(path)
    count = total = 0
    stamp = 0.0
    next_log = time.monotonic() + 2
    def bad_constant(unused):
        raise ValueError('The replay contains a non-finite JSON value.')
    with gzip.open(path, 'rb') as stream:
        def row():
            nonlocal total
            if cancelled and cancelled():
                raise ValueError('Replay startup was cancelled.')
            raw = stream.readline(MAX_LINE + 1)
            if not raw or len(raw) > MAX_LINE:
                raise ValueError('The replay is truncated or has an oversized record.')
            total += len(raw)
            if total > MAX_TOTAL:
                raise ValueError('The replay exceeds the decompressed size limit.')
            result = json.loads(raw.decode('utf-8'), parse_constant=bad_constant)
            if not isinstance(result, dict):
                raise ValueError('Invalid replay record.')
            return result
        first = row()
        h = first.get('data')
        if (first.get('type') != 'header' or not isinstance(h, dict) or
                h.get('magic') != 'WOT_OFFLINE_REPLAY' or h.get('schema') != 1 or
                h.get('client') != '0.9.22.0.1-cn-1513'):
            raise ValueError('This is not a supported #1513 offline replay.')
        for key in ('welcome', 'start', 'snapshot', 'config'):
            if not isinstance(h.get(key), dict):
                raise ValueError('Replay header is missing %s.' % key)
        start, snapshot = h['start'], h['snapshot']
        if (start.get('type') != 'battle_start' or
                snapshot.get('type') != 'snapshot' or
                snapshot.get('round_id') != start.get('round_id')):
            raise ValueError('The replay initial round identity is inconsistent.')
        for kind in ('players', 'bots'):
            a = {v.get('id') for v in start.get(kind, ()) if isinstance(v, dict)}
            b = {v.get('id') for v in snapshot.get(kind, ()) if isinstance(v, dict)}
            if a != b or None in a:
                raise ValueError('The replay initial %s lineup is incomplete.' % kind)
        while True:
            value = row()
            t = value.get('t')
            if (isinstance(t, bool) or not isinstance(t, (int, float)) or
                    not math.isfinite(t) or not stamp <= t <= MAX_DURATION):
                raise ValueError('The replay timestamps are invalid.')
            stamp = float(t)
            kind = value.get('type')
            if kind == 'end':
                if value.get('records') != count or stream.read(1):
                    raise ValueError('The replay footer/count is invalid.')
                break
            data = value.get('data')
            if (kind not in ('wire', 'local') or not isinstance(data, dict) or
                    (kind == 'wire' and data.get('type') not in WIRE_TYPES)):
                raise ValueError('The replay contains an unsupported gameplay record.')
            count += 1
            now = time.monotonic()
            if progress and now >= next_log:
                progress('REPLAY_ENTRY validating records=%d logical_bytes=%d' % (count, total))
                next_log = now + 2
    after = os.stat(path)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('The replay file changed during validation. Retry after saving finishes.')
    return dict(path=path, map=start.get('map'), vehicle=h['config'].get('vehicle'),
                players=len(start.get('players') or ()), bots=len(start.get('bots') or ()),
                duration=stamp, records=count, logical_bytes=total, file_bytes=after.st_size)
