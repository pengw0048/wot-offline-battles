"""Forward-only recording and playback data for the exact offline LAN client.

This is an explicitly versioned .wotlanreplay stream, NOT a native .wotreplay.
It records received gameplay messages and actual local presentation state, not
AI inputs to be simulated again. Playback has no game server and cannot settle
rewards. Native BigWorld rendering still needs Windows acceptance.
"""
from __future__ import print_function

import copy
import datetime
import gzip
import json
import math
import os
import sys
import time
import uuid

MAGIC = 'WOT_OFFLINE_REPLAY'
SCHEMA = 1
CLIENT = '0.9.22.0.1-cn-1513'
EXTENSION = '.wotlanreplay'
ENVIRONMENT = 'WOT_OFFLINE_REPLAY_FILE'
MAX_LINE = 2 * 1024 * 1024
MAX_TOTAL = 768 * 1024 * 1024
MAX_DURATION = 4 * 3600.0
# No account, inventory, receipt or command acknowledgement can be replayed.
MESSAGE_TYPES = frozenset((
    'battle_live', 'snapshot', 'events', 'bot_observation', 'team_chat',
    'team_command', 'team_command_terminal', 'player_destructible_contact_result', 'landing_observation_result',
))
LOCAL_SCALARS = (
    '_local_yaw', '_local_pitch', '_local_roll', '_local_speed',
    '_local_turn_speed', '_local_vertical_speed', '_local_airborne',
    '_local_left_flying', '_local_right_flying', '_local_siege_aim_pitch',
    '_local_drive_turn', '_local_drive_throttle',
)
CONFIG_KEYS = ('map', 'spawn', 'vehicle', 'name', 'physics_tuning', 'he_tuning',
               'native_remote_vehicles', 'prebattleCountdownSeconds',
               'battleDurationSeconds')


def log(message):
    sys.stdout.write('[Offline LAN 0.9.22] OFFLINE_REPLAY %s\n' % message)


def native_recording_mode():
    # BattleReplay.enableAutoRecordingBattles uses 0=off, 1=last, 2=all.
    # Read the owner that the stock settings selector already updates.
    try:
        import BattleReplay
        value = int(BattleReplay.g_replayCtrl.isAutoRecordingEnabled)
        return value if value in (0, 1, 2) else 0
    except Exception as error:
        log('settings_unavailable error=%s' % error)
        return 0


def replay_request():
    return os.environ.get(ENVIRONMENT, '').strip().strip('"')


def clean_message(message):
    value = copy.deepcopy(message)
    # Socket receive epochs are process-local; the player recalibrates timing.
    value.pop('_client_received_time', None)
    value.pop('_client_dispatch_delay', None)
    return value


def _json_line(value):
    data = (json.dumps(value, ensure_ascii=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')
    if len(data) > MAX_LINE:
        raise ValueError('replay record exceeds size bound')
    return data


def _publish(temp, destination):
    # Preserve the previous last-battle recording until a complete new stream
    # has been closed. On Windows rename cannot replace an existing file.
    previous = destination + '.previous'
    had_old = os.path.exists(destination)
    if had_old:
        if os.path.exists(previous):
            os.remove(previous)
        os.rename(destination, previous)
    try:
        os.rename(temp, destination)
    except Exception:
        if had_old and not os.path.exists(destination):
            os.rename(previous, destination)
        raise
    if had_old and os.path.exists(previous):
        os.remove(previous)


class Recorder(object):
    def __init__(self, directory, mode, header, clock=time.time):
        if mode not in (1, 2):
            raise ValueError('recording mode must be last or all')
        self.clock = clock
        self.started = float(clock())
        self.last_time = 0.0
        self.bytes = 0
        self.count = 0
        self.closed = False
        self.last_pose_at = -1.0
        self.failed = False
        if not os.path.isdir(directory):
            os.makedirs(directory)
        basename = ('replay_last_battle' if mode == 1 else
                    datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
        self.path = os.path.abspath(os.path.join(directory, basename + EXTENSION))
        self.temp = self.path + '.' + uuid.uuid4().hex[:8] + '.part'
        self.stream = gzip.open(self.temp, 'wb', compresslevel=3)
        value = dict(header, magic=MAGIC, schema=SCHEMA, client=CLIENT,
                     created=datetime.datetime.now().isoformat())
        self._write({'type': 'header', 'data': value})
        log('record_start mode=%s path=%s' % (mode, self.path))

    def _write(self, value):
        if self.closed:
            raise ValueError('recording is closed')
        data = _json_line(value)
        if self.bytes + len(data) > MAX_TOTAL:
            raise ValueError('recording exceeded total byte limit')
        self.stream.write(data)
        self.bytes += len(data)

    def append(self, kind, data, moment=None):
        if self.closed:
            return False
        elapsed = (float(self.clock()) - self.started if moment is None else float(moment))
        if math.isnan(elapsed) or math.isinf(elapsed) or elapsed < 0 or elapsed > MAX_DURATION:
            raise ValueError('invalid recording timestamp')
        elapsed = max(self.last_time, elapsed)
        self.last_time = elapsed
        self._write({'type': kind, 't': elapsed, 'data': data})
        self.count += 1
        return True

    def close(self, reason='leave'):
        if self.closed:
            return self.path if not self.failed else None
        try:
            self._write({'type': 'end', 't': self.last_time,
                         'records': self.count, 'reason': str(reason)[:80]})
            self.stream.close()
            _publish(self.temp, self.path)
            log('record_saved records=%s bytes=%s path=%s' % (self.count, self.bytes, self.path))
            self.closed = True
            return self.path
        except Exception:
            self.abort()
            raise

    def abort(self):
        self.failed = True
        self.closed = True
        try:
            self.stream.close()
        except Exception:
            pass
        # Retain the .part for diagnosis; it is not advertised as playable.


class Reader(object):
    def __init__(self, path):
        if not path.lower().endswith(EXTENSION):
            raise ValueError('select an offline .wotlanreplay, not a native replay')
        self.stream = gzip.open(path, 'rb')
        self.bytes = 0
        self.last_time = 0.0
        self.count = 0
        self.ended = False
        try:
            first = self._read()
            if first.get('type') != 'header':
                raise ValueError('replay has no header')
            self.header = first.get('data')
            if (not isinstance(self.header, dict) or self.header.get('magic') != MAGIC or
                    self.header.get('schema') != SCHEMA or self.header.get('client') != CLIENT):
                raise ValueError('unsupported offline replay format/client')
            for key in ('welcome', 'start', 'snapshot', 'config'):
                if not isinstance(self.header.get(key), dict):
                    raise ValueError('replay header lacks %s' % key)
            if self.header['start'].get('type') != 'battle_start':
                raise ValueError('invalid replay battle start')
        except Exception:
            self.stream.close()
            raise

    def _read(self):
        raw = self.stream.readline(MAX_LINE + 1)
        if not raw or len(raw) > MAX_LINE:
            raise ValueError('truncated or oversized replay record')
        self.bytes += len(raw)
        if self.bytes > MAX_TOTAL:
            raise ValueError('replay exceeds decompressed size limit')
        def constant(value):
            raise ValueError('non-finite JSON constant')
        value = json.loads(raw.decode('utf-8'), parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError('invalid replay row')
        return value

    def next(self):
        if self.ended:
            return None
        row = self._read()
        kind = row.get('type')
        stamp = row.get('t')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            raise ValueError('invalid replay clock')
        stamp = float(stamp)
        if not self.last_time <= stamp <= MAX_DURATION:
            raise ValueError('non-monotonic replay clock')
        self.last_time = stamp
        if kind == 'end':
            if row.get('records') != self.count:
                raise ValueError('incomplete replay record count')
            # Force gzip footer/CRC validation before claiming successful EOF.
            if self.stream.read(1):
                raise ValueError('data follows replay end')
            self.ended = True
            return row
        data = row.get('data')
        if kind not in ('wire', 'local') or not isinstance(data, dict):
            raise ValueError('unsupported replay record')
        if kind == 'wire' and data.get('type') not in MESSAGE_TYPES:
            raise ValueError('non-gameplay message in replay')
        self.count += 1
        return row

    def close(self):
        self.stream.close()


# Only foreground callbacks access these references. Process/pipe threads never
# call BigWorld, game objects, or the Python-to-native game logger.
_FINISHING = []
_FINAL_POLL_SCHEDULED = False


def _process_log(row):
    sys.stdout.write('[Offline LAN 0.9.22] REPLAY_PROCESS %s\n' %
                     json.dumps(row, ensure_ascii=True, sort_keys=True,
                                separators=(',', ':')))


def _drain_process(recorder):
    for row in recorder.poll_messages():
        _process_log(row)


def _poll_finishing():
    global _FINAL_POLL_SCHEDULED
    _FINAL_POLL_SCHEDULED = False
    for recorder in list(_FINISHING):
        _drain_process(recorder)
        if recorder.done:
            _process_log(recorder.stats())
            _FINISHING.remove(recorder)
    if _FINISHING:
        _schedule_finishing()


def _schedule_finishing():
    global _FINAL_POLL_SCHEDULED
    if _FINAL_POLL_SCHEDULED:
        return
    try:
        import BigWorld
        BigWorld.callback(0.25, _poll_finishing)
        _FINAL_POLL_SCHEDULED = True
    except (ImportError, AttributeError):
        # Contract tests without an engine explicitly drain the process.
        pass


def poll_process_status(client):
    recorder = getattr(client, '_offline_replay_recorder', None)
    if recorder is not None and hasattr(recorder, 'poll_messages'):
        _drain_process(recorder)
        if recorder.failed:
            client._offline_replay_recorder = None
            if recorder not in _FINISHING:
                _FINISHING.append(recorder)
            _schedule_finishing()


def sparse_hint(message):
    """Remember which static fields the accepted snapshot inherited.

    Called before the existing decoder attaches cached manifest/player trees.
    This is presence metadata, not an identity cache guessing whether mutable
    objects changed. Explicit sections (including empty replacements) are
    recorded. The header starts with a complete accepted state.
    """
    if not isinstance(message, dict) or message.get('type') != 'snapshot':
        return None
    missing = {}
    for row in message.get('players') or ():
        if isinstance(row, dict) and row.get('id') is not None:
            missing[row['id']] = tuple(name for name in ('effective_params', 'outfits')
                                       if name not in row)
    return ('bot_manifest' not in message, missing)


def capture_message(message, hint=None):
    """Make only a shallow projection; the immediate C binary freeze owns it.

    No gameplay dictionary is changed. Retain dynamic fields and every event;
    omit only static fields that the existing LAN decoder can inherit exactly
    from the recorded complete header/earlier explicit update.
    """
    value = dict(message)
    value.pop('_client_received_time', None)
    value.pop('_client_dispatch_delay', None)
    if hint is not None and value.get('type') == 'snapshot':
        manifest_missing, player_missing = hint
        if manifest_missing:
            value.pop('bot_manifest', None)
        if 'players' in value:
            players = []
            for source in value['players']:
                row = dict(source)
                for name in player_missing.get(source.get('id'), ()):
                    row.pop(name, None)
                players.append(row)
            value['players'] = players
    return value


def observe_wire(client, message, hint=None):
    if getattr(client, 'is_offline_replay', False) or not isinstance(message, dict):
        return
    poll_process_status(client)
    kind = message.get('type')
    if kind == 'welcome':
        # One handshake per connection, not one deep copy per motion frame.
        client._offline_replay_welcome = clean_message(message)
        return
    if kind == 'battle_live':
        client._offline_replay_live = clean_message(message)
    if kind == 'battle_start':
        client._offline_replay_live = None
    recorder = getattr(client, '_offline_replay_recorder', None)
    if recorder is None:
        return
    try:
        if kind in MESSAGE_TYPES:
            recorder.append('wire', capture_message(message, hint))
        elif kind == 'roster' and message.get('phase') == 'waiting':
            finish(client, 'round_finished')
    except Exception as error:
        recorder.abort()
        client._offline_replay_recorder = None
        if hasattr(recorder, 'poll_messages'):
            _FINISHING.append(recorder)
            _schedule_finishing()
        log('record_failed error=%s part=%s' % (error, recorder.temp))


def ensure_recording(runtime):
    client = runtime.client
    if client is not None:
        poll_process_status(client)
    if (getattr(runtime, '_worker_mode', False) or client is None or
            getattr(client, 'is_offline_replay', False) or
            not getattr(runtime, '_client_ready_received', False) or
            not getattr(runtime, '_ready_sent', False)):
        return
    round_id = (runtime._start_message or {}).get('round_id')
    if getattr(client, '_offline_replay_attempted', None) == round_id:
        return
    client._offline_replay_attempted = round_id
    mode = native_recording_mode()
    if not mode:
        return
    try:
        from gui.mods.offline_lan_0922.replay_process import ProcessRecorder
        header = {
            'welcome': getattr(client, '_offline_replay_welcome', None),
            'start': capture_message(runtime._start_message),
            'snapshot': capture_message(client.last_snapshot or {}),
            'config': dict((key, runtime._config[key]) for key in CONFIG_KEYS if key in runtime._config),
            'player_id': int(client.player_id),
            'live': getattr(client, '_offline_replay_live', None),
        }
        if not isinstance(header['welcome'], dict):
            raise ValueError('recording has no negotiated welcome')
        header['snapshot'].setdefault('type', 'snapshot')
        root = os.getcwdu() if hasattr(os, 'getcwdu') else os.getcwd()
        from gui.mods.offline_lan_0922 import replay_presentation, replay_resources
        header['local_descriptor_contract'] = replay_presentation.descriptor_contract(runtime._local_descriptor)
        header['local_crew_group'] = int(runtime._garage_loadout_snapshot().get('crew_group', 0))
        header['overlay_signature'] = replay_resources.active_signature(root)
        client._offline_replay_recorder = ProcessRecorder(
            os.path.join(root, 'replays', 'offline'), mode, header,
            clock=runtime._clock)
        log('record_start_requested mode=%s independent_process=True path=%s' %
            (mode, client._offline_replay_recorder.path))
    except Exception as error:
        log('record_start_failed error=%s' % error)


def local_state(runtime):
    result = {'position': list(runtime._local_position)}
    for name in LOCAL_SCALARS:
        result[name] = getattr(runtime, name, 0.0)
    entity = runtime._server_entity(runtime._server.vehicle_id) if runtime._server is not None else None
    result['health'] = int(getattr(entity, 'health', 0))
    result['gun_angles'] = int(getattr(entity, 'gunAngles', 0))
    rotator = getattr(runtime._avatar, 'gunRotator', None)
    if rotator is not None:
        result['turret_yaw'] = float(getattr(rotator, 'turretYaw', 0.0))
        result['gun_pitch'] = float(getattr(rotator, 'gunPitch', 0.0))
        result['dispersion_angle'] = float(getattr(rotator, 'dispersionAngle', 0.0))
    gun = getattr(runtime, '_gun_state', None)
    if gun is not None:
        result['gun'] = dict((name, copy.deepcopy(getattr(gun, name))) for name in
                             ('ammo', 'shot_index', 'clip', 'reload_time', 'reload_duration', 'load_started', 'pending_index', 'dispersion')
                             if hasattr(gun, name))
    # Replay must use the original observer's visibility decisions, not rerun
    # camouflage/LOS against new clock phases and camera directions.
    result['visibility'] = dict((key, [bool(record.get('spot_visible', True)),
                                       bool(record.get('spot_marker_visible', True))])
                                for key, record in runtime._records.items()
                                if record.get('kind') == 'bot')
    return result


def record_local(runtime):
    recorder = getattr(runtime.client, '_offline_replay_recorder', None)
    if recorder is None or recorder.closed:
        return
    elapsed = float(runtime._clock()) - recorder.started
    if elapsed - recorder.last_pose_at < 1.0 / 30.0:
        return
    try:
        recorder.append('local', local_state(runtime))
        recorder.last_pose_at = elapsed
    except Exception as error:
        recorder.abort()
        runtime.client._offline_replay_recorder = None
        if hasattr(recorder, 'poll_messages'):
            _FINISHING.append(recorder)
            _schedule_finishing()
        log('local_record_failed error=%s' % error)


def finish(client, reason):
    recorder = getattr(client, '_offline_replay_recorder', None)
    if recorder is not None:
        client._offline_replay_recorder = None
        try:
            recorder.close(reason)
        except Exception as error:
            recorder.abort()
            log('record_finish_failed error=%s' % error)
        if hasattr(recorder, 'poll_messages'):
            _drain_process(recorder)
            if recorder not in _FINISHING:
                _FINISHING.append(recorder)
            _schedule_finishing()


def validate_local(value):
    """Validate a bounded typed presentation frame before native assignment."""
    if not isinstance(value, dict):
        raise ValueError('invalid local replay state')
    def number(raw, low, high):
        n = float(raw)
        if math.isnan(n) or math.isinf(n) or not low <= n <= high:
            raise ValueError('local replay state outside range')
        return n
    position = value.get('position')
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise ValueError('invalid recorded position')
    result = {'position': [number(v, -10000, 10000) for v in position]}
    boolean_fields = ('_local_airborne', '_local_left_flying', '_local_right_flying')
    for name in LOCAL_SCALARS:
        if name in value:
            if name in boolean_fields:
                if not isinstance(value[name], bool):
                    raise ValueError('invalid recorded contact flag')
                result[name] = value[name]
            else:
                result[name] = number(value[name], -1000, 1000)
    for name in ('turret_yaw', 'gun_pitch', 'dispersion_angle'):
        if name in value:
            result[name] = number(value[name], -1000, 1000)
    if '_replay_t' in value:
        result['_replay_t'] = number(value['_replay_t'], 0, MAX_DURATION)
    if 'health' in value:
        result['health'] = int(number(value['health'], -1000000, 1000000))
    result['gun_angles'] = int(number(value.get('gun_angles', 0), 0, 4294967295))
    visibility = value.get('visibility', {})
    if not isinstance(visibility, dict) or len(visibility) > 64:
        raise ValueError('invalid replay visibility set')
    result['visibility'] = {}
    for key, item in visibility.items():
        if (not isinstance(key, (str, type(u''))) or len(key) > 80 or
                not isinstance(item, list) or len(item) != 2 or
                not all(isinstance(v, bool) for v in item)):
            raise ValueError('invalid replay visibility row')
        result['visibility'][key] = list(item)
    gun = value.get('gun', {})
    if not isinstance(gun, dict):
        raise ValueError('invalid replay gun state')
    result['gun'] = {}
    for name in ('shot_index', 'clip'):
        if name in gun:
            result['gun'][name] = int(number(gun[name], 0, 10000))
    for name in ('reload_time', 'reload_duration', 'dispersion'):
        if name in gun:
            result['gun'][name] = number(gun[name], 0, 100000)
    if 'load_started' in gun:
        if type(gun['load_started']) is not bool:
            raise ValueError('invalid recorded load_started')
        result['gun']['load_started'] = gun['load_started']
    if 'pending_index' in gun:
        result['gun']['pending_index'] = None if gun['pending_index'] is None else int(number(gun['pending_index'], 0, 19))
    if 'ammo' in gun:
        if not isinstance(gun['ammo'], list) or len(gun['ammo']) > 20:
            raise ValueError('invalid replay ammunition')
        result['gun']['ammo'] = [int(number(n, 0, 100000)) for n in gun['ammo']]
    return result
