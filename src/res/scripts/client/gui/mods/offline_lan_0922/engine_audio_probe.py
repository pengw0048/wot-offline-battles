"""Opt-in, bounded R8 ENGINE/GUN diagnostics. No speculative sound playback.

A observes R8. B selects direct stock callbacks only for the local vehicle and
visible friendly appearances (also after reveal). D preserves only live known
friendly sound owners across draw hides; enemies keep the normal mute gate. C omits ONLY R8's extra attachToModel in
bind_motion. Never touch hidden-enemy gating or native engine inputs.
"""
from __future__ import print_function
import collections
import json
import math
import os
import re
import sys
import threading
import time
import uuid
import weakref

try:
    _STRINGS = (basestring,)
    _NUMBERS = (int, long, float)
except NameError:
    _STRINGS = (str,)
    _NUMBERS = (int, float)

_MODE = os.environ.get('WOT_OFFLINE_ENGINE_DIAG_MODE', '').upper()
if _MODE not in ('A', 'B', 'C', 'D') or os.environ.get('OFFLINE_LAN_0922_CLIENT_MODE') == 'simulation_worker':
    _MODE = ''
_SESSION = None
_NEXT_SAMPLE = 1.0
_MAX_RECORD = 32768
_MAX_LOG = 16 * 1024 * 1024
_MAX_QUEUE = 256
_MAX_APPEARANCES = 128
_MISSING = object()


def enabled():
    return bool(_MODE)


def mode():
    return _MODE or 'OFF'


def ident(obj):
    if obj is None:
        return None
    return '%s.%s@%x' % (type(obj).__module__, type(obj).__name__, id(obj))


# CPython 2.7 has no math.isfinite. Keep conversion local, not a math monkeypatch.
def _finite(x):
    return not math.isnan(float(x)) and not math.isinf(float(x))


def primitive(obj, depth=0):
    if isinstance(obj, _NUMBERS) and not isinstance(obj, bool):
        return obj if _finite(obj) else str(obj)
    if obj is None or isinstance(obj, (bool,) + _STRINGS):
        return obj[:300] if isinstance(obj, _STRINGS) else obj
    if isinstance(obj, (list, tuple)):
        if depth >= 3:
            return {'truncated_container': type(obj).__name__}
        return [primitive(x, depth+1) for x in obj[:12]]
    if isinstance(obj, dict):
        if depth >= 3:
            return {'truncated_container': 'dict'}
        return dict((str(k)[:100], primitive(v, depth+1)) for k, v in list(obj.items())[:20])
    return {'type_only': ident(obj)}


# Retain the public name, with no dependency on Python3-only math functions.
value = primitive


def optional(obj, name):
    if obj is None:
        return {'available': False, 'reason': 'no_object'}
    try:
        v = getattr(obj, name, _MISSING)
        if v is _MISSING:
            return {'available': False}
        if callable(v):
            return {'available': True, 'callable_not_invoked': True}
        return {'available': True, 'value': primitive(v)}
    except Exception as error:
        return {'available': False, 'read_error': type(error).__name__}


def position(provider):
    if provider is None:
        return {'available': False}
    try:
        xyz = [float(provider.x), float(provider.y), float(provider.z)]
    except (AttributeError, TypeError, ValueError):
        try:
            module = sys.modules.get('Math')
            if module is None:
                return {'available': False, 'reason': 'Math_unavailable'}
            v = module.Matrix(provider).translation
            xyz = [float(v.x), float(v.y), float(v.z)]
        except Exception as error:
            return {'available': False, 'read_error': type(error).__name__}
    return {'available': True, 'xyz': xyz} if all(_finite(x) for x in xyz) else {'available': False, 'reason': 'nonfinite'}


def _player():
    module = sys.modules.get('BigWorld')
    get = getattr(module, 'player', None)
    return get() if callable(get) else None


def role(appearance):
    """Only accept teams from the current arena; unknown never means ally."""
    try:
        p = _player()
        vid = getattr(appearance, 'id', None)
        mine = getattr(p, 'playerVehicleID', None)
        if (mine is not None and mine == vid) or getattr(appearance, '_engineDiagPlayerHint', False):
            return 'local'
        rows = getattr(getattr(p, 'arena', None), 'vehicles', {})
        mine_row = rows.get(mine, {})
        row = rows.get(vid, {})
        team = mine_row.get('team', getattr(p, 'team', None))
        other_team = row.get('team')
        if team is None or other_team is None or team not in (1, 2) or other_team not in (1, 2):
            return 'unknown'
        return 'ally' if other_team == team else 'enemy'
    except Exception:
        return 'unknown'


def direct_initial(appearance):
    """B: choose on EVERY binding, not only before the first hide."""
    if _MODE != 'B' or appearance is None:
        return False
    if getattr(appearance, '_offlineLANMutedEngine', None) is not None:
        return False
    return role(appearance) in ('local', 'ally')


def skip_extra_attach():
    return _MODE == 'C'


def preserve_friendly_owner(entity, audible):
    """D only: isolate removal/rebuild for confirmed LIVE friendlies.

    This intentionally keeps same-team engine sound during a draw hide. Visual
    visibility and all enemy audio gates are unchanged. Never reinsert wrappers.
    """
    if _MODE != 'D' or audible or entity is None:
        return False
    a = getattr(entity, 'appearance', None)
    if a is None or role(a) != 'ally':
        return False
    if not getattr(entity, 'inWorld', False) or not getattr(entity, 'isStarted', False):
        return False
    alive = getattr(entity, 'isAlive', None)
    if not callable(alive) or not alive():
        return False
    if (getattr(a, '_offlineLANMutedEngine', None) is not None or
            getattr(a, 'engineAudition', None) is None):
        return False
    return True


try:
    _TEXT = unicode
    _BINARY = str
    _INTEGERS = (int, long)
except NameError:
    _TEXT = str
    _BINARY = bytes
    _INTEGERS = (int,)


def _error_text(error):
    try:
        return _TEXT(error)[:240]
    except Exception:
        return '<unreadable %s>' % type(error).__name__


def _ascii_notice(text):
    """Independent, low-volume output, never routed back to our own queue."""
    try:
        text = _TEXT(text).encode('ascii', 'backslashreplace')
        if sys.version_info[0] >= 3:
            text = text.decode('ascii')
        sys.stdout.write('[Offline LAN 0.9.22] ENGINE_DIAG_HEALTH ' + text + '\n')
    except Exception:
        pass


def _json_safe(obj, changes, depth=0, seen=None):
    """Worker-only normalization; do not call any native object's methods.

    Invalid UTF8 remains explicitly tagged raw hex, not guessed or discarded.
    Unknown objects are tagged by type only. Bounds protect the writer itself.
    """
    if seen is None:
        seen = set()
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, _INTEGERS):
        return obj
    if isinstance(obj, float):
        if _finite(obj):
            return obj
        changes[0] += 1
        return {'unavailable': 'nonfinite', 'value': repr(obj)}
    if isinstance(obj, _BINARY):
        try:
            obj = obj.decode('utf-8')
        except UnicodeDecodeError:
            import binascii
            changes[0] += 1
            return {'invalid_utf8_hex': binascii.hexlify(obj[:4096]).decode('ascii'),
                    'original_bytes': len(obj), 'truncated': len(obj) > 4096}
    if isinstance(obj, _TEXT):
        if len(obj) <= 8192:
            return obj
        changes[0] += 1
        return {'text_prefix': obj[:8192], 'original_chars': len(obj), 'truncated': True}
    if isinstance(obj, (dict, list, tuple)):
        if depth >= 12 or id(obj) in seen:
            changes[0] += 1
            return {'unavailable': 'cyclic_or_deep', 'type': type(obj).__name__}
        seen.add(id(obj))
        try:
            if isinstance(obj, dict):
                out = {}
                for i, (k, v) in enumerate(obj.items()):
                    if i >= 128:
                        out['_diagnostic_truncated_keys'] = len(obj) - 128
                        changes[0] += 1
                        break
                    if isinstance(k, _BINARY):
                        try:
                            k = k.decode('utf-8')
                        except UnicodeDecodeError:
                            import binascii
                            k = '<invalid_utf8_key:%s>' % binascii.hexlify(k[:100]).decode('ascii')
                            changes[0] += 1
                    elif not isinstance(k, _TEXT):
                        k = '<key_type:%s:%d>' % (type(k).__name__, i)
                        changes[0] += 1
                    out[k] = _json_safe(v, changes, depth + 1, seen)
                return out
            result = [_json_safe(x, changes, depth + 1, seen) for x in obj[:256]]
            if len(obj) > 256:
                changes[0] += 1
                result.append({'truncated_items': len(obj) - 256})
            return result
        finally:
            seen.remove(id(obj))
    changes[0] += 1
    return {'unavailable': 'nonprimitive', 'native_type': '%s.%s' %
            (type(obj).__module__, type(obj).__name__)}


class _Writer(object):
    """Isolate record errors; report IO failures out of band; bounded async IO."""
    def __init__(self, directory):
        if not os.path.isdir(directory):
            os.makedirs(directory)
        leaf = 'engine-%s-pid%d-%s.jsonl' % (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()), os.getpid(), uuid.uuid4().hex[:8])
        self.path = os.path.join(directory, leaf)
        self.error_path = self.path[:-6] + '.errors.txt'
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, 'wb')
        self.items = collections.deque()
        self.lock = threading.Lock()
        self.signal = threading.Event()
        self.stopping = False
        self.dropped = 0
        self.bytes = 0
        self.failed = None
        self.limit = False
        self.accepted = self.written = self.sample_rows = 0
        self.record_errors = self.normalized_fields = 0
        self.error_notices = 0
        self.finished = False
        self.last_write_wall = 0.0
        self.thread = threading.Thread(target=self._run, name='WoTEngineDiagnosticWriter')
        self.thread.daemon = True
        self.thread.start()

    def put(self, row):
        with self.lock:
            if self.stopping or self.limit or self.failed or len(self.items) >= _MAX_QUEUE:
                self.dropped += 1
                return False
            self.items.append(row)
            self.accepted = getattr(self, 'accepted', 0) + 1
        self.signal.set()
        return True

    def _failure_notice(self, stage, error):
        # Independently stored even if the main JSONL is no longer writable.
        self.error_notices += 1
        if self.error_notices > 8:
            return
        text = 'stage=%s type=%s message=%s' % (stage, type(error).__name__, _error_text(error))
        _ascii_notice(text)
        try:
            data = (_TEXT(text) + '\n').encode('utf-8', 'backslashreplace')
            with open(self.error_path, 'ab') as f:
                f.write(data)
        except Exception:
            _ascii_notice('stage=error_sidecar_unwritable original_stage=' + stage)

    def _encode(self, row):
        changes = [0]
        safe = _json_safe(row, changes)
        self.normalized_fields += changes[0]
        if changes[0] and isinstance(safe, dict):
            safe['_diagnostic_normalized_fields'] = changes[0]
        return (json.dumps(safe, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')

    def _write(self, row):
        # Bad data affects only this record, never every later snapshot.
        sample_written = isinstance(row, dict) and row.get('kind') == 'sample'
        try:
            data = self._encode(row)
        except Exception as error:
            sample_written = False
            self.record_errors += 1
            self._failure_notice('record_encode', error)
            data = (json.dumps({'kind': 'record_error', 'error_type': type(error).__name__,
                                'sequence': self.written + 1}) + '\n').encode('ascii')
        if len(data) > _MAX_RECORD:
            sample_written = False
            data = (json.dumps({'kind': 'record_too_large', 'bytes': len(data)}) + '\n').encode('ascii')
            self.dropped += 1
        if self.bytes + len(data) > _MAX_LOG:
            if not self.limit:
                marker = (json.dumps({'kind': 'log_limit_reached', 'limit_bytes': _MAX_LOG}) + '\n').encode('ascii')
                self.stream.write(marker)
                self.bytes += len(marker)
            self.limit = True
            self.dropped += 1
            return
        self.stream.write(data)  # IO error is fatal and separately reported.
        self.bytes += len(data)
        self.written += 1
        if sample_written:
            self.sample_rows += 1
        self.last_write_wall = time.time()

    def status(self):
        return dict(alive=self.thread.is_alive(), failed=self.failed, limit=self.limit,
                    accepted=self.accepted, written=self.written, samples=self.sample_rows,
                    pending=len(self.items), dropped=self.dropped,
                    record_errors=self.record_errors, normalized_fields=self.normalized_fields,
                    finished=self.finished, last_write_wall=self.last_write_wall)

    def _run(self):
        try:
            next_flush = time.time() + 1.0
            while True:
                self.signal.wait(0.25)
                self.signal.clear()
                with self.lock:
                    batch = []
                    while self.items and len(batch) < 64:
                        batch.append(self.items.popleft())
                    stopping = self.stopping and not self.items
                    if self.items:
                        self.signal.set()  # Do not sleep between queued batches, especially at exit.
                for row in batch:
                    self._write(row)
                if time.time() >= next_flush or stopping:
                    self.stream.flush()
                    next_flush = time.time() + 1.0
                if stopping:
                    footer = {'kind': 'writer_end', 'wall_time': time.time(),
                              'dropped_records': self.dropped, 'limit': self.limit,
                              'bytes_before_footer': self.bytes, 'accepted': self.accepted,
                              'written': self.written, 'sample_rows': self.sample_rows,
                              'record_errors': self.record_errors,
                              'normalized_fields': self.normalized_fields}
                    self.stream.write((json.dumps(footer) + '\n').encode('ascii'))
                    self.stream.flush()
                    self.finished = True
                    break
        except Exception as error:
            self.failed = '%s: %s' % (type(error).__name__, _error_text(error))
            self._failure_notice('writer_io_or_loop', error)
        finally:
            try:
                self.stream.close()
            except Exception as error:
                self.failed = self.failed or 'close_failed:' + type(error).__name__
                self._failure_notice('writer_close', error)

    def close(self):
        with self.lock:
            self.stopping = True
        self.signal.set()
        self.thread.join(0.25)  # Same bounded exit wait as R9D.
        _ascii_notice('writer_close finished=%s alive=%s records=%s samples=%s errors=%s failed=%s' %
                      (self.finished, self.thread.is_alive(), self.written, self.sample_rows,
                       self.record_errors, self.failed))


class Probe(object):
    def __init__(self, directory):
        self.writer = _Writer(directory)
        self.appearances = {}
        self.callback_id = None
        self.closed = False
        self.hooks = []
        self.capabilities = set()
        self.event_counts = collections.Counter()
        self.next_state_event = {}
        self.control_count = 0
        self.samples = 0
        self.max_sample_ms = 0.0
        self.sample_total_ms = 0.0
        self.emit('session_start', mode=_MODE, schema=2, pid=os.getpid(), python=sys.version.split()[0],
                  native_mixer_state_exposed=False, native_bank_inventory_exposed=False,
                  camera_is_not_native_listener=True, sample_period_seconds=_NEXT_SAMPLE,
                  max_sampled_vehicles=3, log_limit_bytes=_MAX_LOG, queue_limit=_MAX_QUEUE,
                  run=os.environ.get('WOT_OFFLINE_ENGINE_DIAG_RUN', '')[:100],
                  gameplay_changed=False, diagnostics_revision='R9D2',
                  friendly_audio_hide_bypassed=(_MODE == 'D'))
        self._hook_controls()
        self._schedule()

    def emit(self, kind, **fields):
        if self.closed:
            return
        row = {'kind': kind, 'mode': _MODE, 'wall_time': time.time(), 'pid': os.getpid()}
        row.update(fields)
        self.writer.put(row)

    def _schedule(self):
        b = sys.modules.get('BigWorld')
        callback = getattr(b, 'callback', None)
        if callable(callback) and not self.closed:
            self.callback_id = callback(_NEXT_SAMPLE, self._tick)

    def _health(self):
        now = time.time()
        if now < getattr(self, 'next_health', 0.0):
            return
        self.next_health = now + 5.0
        if hasattr(self.writer, 'status'):
            state = self.writer.status()
            _ascii_notice('mode=%s polls=%d status=%r' % (_MODE, self.samples, state))
            self.emit('probe_health', **state)

    def _tick(self):
        self.callback_id = None  # retire before the callback can request quit
        if self.closed:
            return
        try:
            self.sample()
        except Exception as error:
            self.emit('sample_error', error=type(error).__name__, message=_error_text(error))
        finally:
            self._health()
            self._schedule()

    def track(self, appearance, is_player=None):
        if is_player is not None:
            appearance._engineDiagPlayerHint = bool(is_player)
        aid = id(appearance)
        if aid not in self.appearances:
            if len(self.appearances) >= _MAX_APPEARANCES:
                for key, ref in list(self.appearances.items()):
                    if ref() is None:
                        self.appearances.pop(key, None)
                if len(self.appearances) >= _MAX_APPEARANCES:
                    return
            self.appearances[aid] = weakref.ref(appearance)
            self.emit('appearance_seen', appearance=ident(appearance), vehicle_id=getattr(appearance, 'id', None), role=role(appearance), is_player_hint=is_player)

    def note(self, appearance, phase, **fields):
        self.track(appearance)
        key = (id(appearance), phase)
        self.event_counts[key] += 1
        if phase in ('state', 'motion', 'D_preserve_friendly_owner'):
            now = time.time()
            if now < self.next_state_event.get(key, 0.0):
                return
            self.next_state_event[key] = now + 2.0
        self.emit('lifecycle', lifecycle=self._lifecycle_state(appearance),
                  phase=phase, occurrence=self.event_counts[key],
                  appearance=ident(appearance), vehicle_id=getattr(appearance, 'id', None),
                  role=role(appearance), fields=primitive(fields))

    def _lifecycle_state(self, a):
        d = getattr(a, 'detailedEngineState', None)
        sink = getattr(a, 'engineAudition', None)
        model = getattr(a, 'compoundModel', None)
        comps = getattr(a, '_components', ())
        b = sys.modules.get('BigWorld')
        e = getattr(b, 'entities', {}).get(getattr(a, 'id', None))
        callbacks = {}
        for name in ('onEngineStart', 'onStateChanged'):
            cb = getattr(d, name, None)
            target = getattr(cb, '__self__', getattr(cb, 'im_self', None))
            callbacks[name] = {'target': ident(target), 'type': type(cb).__name__,
                               'native_sink_target': target is sink and sink is not None}
        return {'audition': ident(sink), 'state': ident(d), 'model': ident(model),
                'appearance_active': optional(a, 'activated'),
                'audition_member': sink is not None and any(c is sink for c in comps),
                'component_count': len(comps), 'callbacks': callbacks,
                'entity_current': e is not None and getattr(e, 'appearance', None) is a,
                'draw_visible': optional(e, '_offlineNativeDrawVisible'),
                'mute_marker': getattr(a, '_offlineLANMutedEngine', None) is not None,
                'direct': bool(getattr(a, '_engineDiagDirect', False)),
                'ever_muted': bool(getattr(a, '_engineDiagEverMuted', False)),
                'no_sound_source_query_at_hide': True}

    def _capabilities(self, obj, kind):
        if obj is None:
            return
        key = (type(obj).__module__, type(obj).__name__, kind)
        if key in self.capabilities:
            return
        self.capabilities.add(key)
        try:
            names = sorted(set(str(x) for x in dir(obj) if not str(x).startswith('__')))
        except Exception:
            names = []
        self.emit('capabilities', object_kind=kind, native_type='.'.join(key[:2]),
                  attributes=names[:180], truncated=len(names) > 180,
                  note='Listed callables are NOT invoked by the probe')

    def _sound(self, audition, index, label):
        # Exact #1513 accessor used by stock GUN effects. Query only the LIVE
        # current audition owned by an engine-owned Vehicle. Never create sound.
        result = {'index': index, 'kind': label}
        try:
            obj = audition.getSoundObject(index)
            result['exists'] = obj is not None
            if obj is None:
                return result
            result['object'] = ident(obj)
            self._capabilities(obj, label + '_sound_object')
            for name in ('isPlaying', 'isActive', 'isVirtual', 'playingID', 'name'):
                result[name] = optional(obj, name)
            matrix = getattr(obj, 'matrixProvider', None)
            result['matrix'] = ident(matrix)
            result['matrix_position'] = position(matrix)
            # Do not call unverified native query methods with guessed args.
        except Exception as error:
            result['query_error'] = type(error).__name__
            result['message'] = str(error)[:160]
        return result

    def _snapshot(self, a, e, group, camera):
        d = getattr(a, 'detailedEngineState', None)
        sink = getattr(a, 'engineAudition', None)
        model = getattr(a, 'compoundModel', None)
        components = getattr(a, '_components', ())
        row = {'appearance': ident(a), 'vehicle_id': getattr(a, 'id', None), 'role': group,
               'appearance_activated': optional(a, 'activated'),
               'engine_state': ident(d), 'audition': ident(sink),
               'audition_in_component_list': any(x is sink for x in components) if sink is not None else False,
               'state_in_component_list': any(x is d for x in components) if d is not None else False,
               'model': ident(model), 'model_root': ident(getattr(model, 'root', None)),
               'model_position': position(getattr(model, 'matrix', None)),
               'entity_position': position(getattr(e, 'position', None)),
               'camera': camera, 'muted': getattr(a, '_offlineLANMutedEngine', None) is not None,
               'ever_muted': bool(getattr(a, '_engineDiagEverMuted', False)),
               'extra_attach_omitted': skip_extra_attach(),
               'direct_callback_selected': bool(getattr(a, '_engineDiagDirect', False))}
        self._capabilities(sink, 'audition')
        self._capabilities(d, 'detailed_engine_state')
        for name in ('rpm', 'gearNum', 'mode', 'state', 'isStarted', 'isActive'):
            row['state_' + name] = optional(d, name)
        row['audition_active'] = optional(sink, 'isActive')
        row['state_matrix_position'] = position(getattr(d, 'vehicleMatrixLink', None)) if d is not None else {'available': False}
        for name in ('onEngineStart', 'onStateChanged'):
            cb = getattr(d, name, None) if d is not None else None
            target = getattr(cb, '__self__', getattr(cb, 'im_self', None))
            row[name] = {'callable': callable(cb), 'target': ident(target), 'callback_type': type(cb).__name__ if cb is not None else None}
        desc = getattr(a, 'typeDescriptor', None)
        row['vehicle_name'] = primitive(getattr(desc, 'name', None))
        engine_desc = getattr(desc, 'engine', None)
        row['rpm_min'] = optional(engine_desc, 'rpm_min')
        row['rpm_max'] = optional(engine_desc, 'rpm_max')
        physics = getattr(desc, 'physics', {})
        row['vehicle_physics_inputs'] = dict((k, primitive(physics.get(k))) for k in ('enginePower', 'weight', 'speedLimits')) if isinstance(physics, dict) else {'available': False}
        vehicle_filter = getattr(a, 'filter', None)
        row['native_filter_speed'] = optional(vehicle_filter, 'averageSpeed')
        row['native_filter_turn_speed'] = optional(vehicle_filter, 'averageRotationSpeed')
        row['replay_requested'] = bool(os.environ.get('WOT_OFFLINE_REPLAY_FILE'))
        for name in ('engine', 'chassis'):
            sounds = getattr(getattr(desc, name, None), 'sounds', None)
            try:
                row[name + '_event'] = sounds.getWWPlayerSound(group == 'local')
            except Exception:
                row[name + '_event'] = None
        sensor = getattr(a, 'waterSensor', None)
        row['underwater'] = optional(sensor, 'isUnderWater')
        row['in_water'] = optional(sensor, 'isInWater')
        if sink is not None:
            row['sound_objects'] = [self._sound(sink, idx, name) for idx, name in ((0, 'CHASSIS'), (1, 'ENGINE'), (2, 'GUN'), (3, 'HIT'))]
        else:
            row['sound_objects'] = []
        self.emit('sample', **row)

    def sample(self):
        started = time.time()
        b = sys.modules.get('BigWorld')
        entities = getattr(b, 'entities', {})
        camera = {'available': False, 'native_listener_not_exposed': True}
        try:
            cam = b.camera()
            camera = {'position': position(getattr(cam, 'position', None)), 'native_listener_not_exposed': True}
            camera['mode'] = primitive(getattr(getattr(_player(), 'inputHandler', None), 'ctrlModeName', None))
        except Exception:
            pass
        chosen = {}
        for key, ref in list(self.appearances.items()):
            a = ref()
            if a is None:
                self.appearances.pop(key, None)
                continue
            # Look up by known ID FIRST. Do not dereference a retired Vehicle
            # wrapper retained by a Python appearance or test helper.
            vid = getattr(a, 'id', None)
            entity = entities.get(vid)
            if entity is None:
                continue
            if getattr(entity, 'appearance', None) is not a or not getattr(entity, 'inWorld', False) or not getattr(entity, 'isStarted', False):
                continue
            alive = getattr(entity, 'isAlive', None)
            if not callable(alive) or not alive():
                continue
            group = role(a)
            if group not in ('local', 'ally', 'enemy'):
                continue
            if getattr(a, '_offlineLANMutedEngine', None) is not None or not getattr(entity, '_offlineNativeDrawVisible', True):
                continue  # no sound-source probing or location logging for hidden enemies
            distance = 0.0
            ep = position(getattr(entity, 'position', None))
            cp = camera.get('position', {})
            if ep.get('available') and cp.get('available'):
                distance = sum((x-y)**2 for x,y in zip(ep['xyz'], cp['xyz']))
            old = chosen.get(group)
            if old is None or distance < old[0]:
                chosen[group] = (distance, a, entity)
        for group in ('local', 'ally', 'enemy'):
            entry = chosen.get(group)
            if entry is not None:
                try:
                    self._snapshot(entry[1], entry[2], group, camera)
                except Exception as error:
                    self.emit('vehicle_sample_error', role=group, error=type(error).__name__,
                              message=_error_text(error))
        self.samples += 1
        duration = max(0., (time.time()-started)*1000.)
        self.max_sample_ms = max(self.max_sample_ms, duration)
        self.sample_total_ms += duration
        if self.samples % 10 == 0 or duration > 20.:
            self.emit('probe_budget', sampled_groups=sorted(chosen), samples=self.samples,
                      current_ms=duration, max_ms=self.max_sample_ms,
                      mean_ms=self.sample_total_ms/self.samples, dropped=self.writer.dropped,
                      writer_error=self.writer.failed)

    def _control_log(self, kind, name, args, kwargs=None, result=None, error=None):
        # Keep diagnostic failures in a separate frame. In CPython 2.7 a nested
        # except before a bare raise can replace the original native exception.
        try:
            self.emit(kind, api=name, args=primitive(args), kwargs=primitive(kwargs),
                      result=primitive(result), error=error,
                      coverage='Python calls only; native internal calls are not intercepted')
        except Exception:
            pass

    def _wrap(self, obj, name, kind, limit=200):
        original = getattr(obj, name, None)
        if not callable(original):
            self.emit('control_unavailable', api=name)
            return
        def observed(*args, **kwargs):
            try:
                result = original(*args, **kwargs)
            except Exception as error:
                self._control_log(kind, name, args, kwargs, error=type(error).__name__)
                raise
            self.control_count += 1
            if self.control_count <= limit:
                self._control_log(kind, name, args, kwargs, result=result)
            return result
        try:
            setattr(obj, name, observed)
        except (AttributeError, TypeError) as error:
            self.emit('control_hook_unavailable', api=name, error=type(error).__name__)
            return
        self.hooks.append((obj, name, original, observed))

    def _hook_controls(self):
        w = sys.modules.get('WWISE')
        if w is not None:
            for name in ('WW_setRTPCBus', 'WW_setRTCPGlobal', 'WW_setState', 'WWsetCameraShift', 'WW_setMasterVolume'):
                self._wrap(w, name, 'audio_control')
            for name in ('WG_loadSoundBank', 'WG_unLoadSoundBank'):
                self._wrap(w, name, 'bank_call')
            self.emit('wwise_status', enabled=optional(w, 'enabled'))
        sounds = getattr(sys.modules.get('SoundGroups'), 'g_instance', None)
        volumes = {}
        if sounds is not None:
            for key in ('vehicles', 'effects', 'gui', 'ambient'):
                try:
                    volumes[key] = sounds.getVolume(key)
                except Exception:
                    volumes[key] = None
        self.emit('saved_volumes', values=volumes, not_a_native_mixer_query=True)
        self.emit('limits', unknown_fields=['native_final_gain', 'native_listener_position', 'native_bank_membership', 'native_voice_virtualization_if_not_exposed'],
                  no_force_play=True, no_bank_reload=True, no_volume_write=True,
                  no_native_query_method_guessing=True)

    def close(self):
        if self.closed:
            return
        self.emit('session_end', samples=self.samples, max_sample_ms=self.max_sample_ms,
                  mean_sample_ms=self.sample_total_ms/max(1,self.samples), dropped_records=self.writer.dropped)
        self.closed = True
        callback = self.callback_id
        self.callback_id = None
        if callback is not None:
            b = sys.modules.get('BigWorld')
            cancel = getattr(b, 'cancelCallback', None)
            if callable(cancel):
                try:
                    cancel(callback)
                except (ValueError, RuntimeError):
                    pass
        for obj,name,old,new in reversed(self.hooks):
            if getattr(obj,name,None) is new:
                setattr(obj,name,old)
        self.hooks = []
        self.appearances.clear()
        self.writer.close()


def start():
    global _SESSION
    if not enabled() or _SESSION is not None:
        return
    directory = os.environ.get('WOT_OFFLINE_ENGINE_DIAG_DIR', '')
    if not directory:
        base = os.environ.get('LOCALAPPDATA') or os.getcwd()
        directory = os.path.join(base, 'WoTOfflineBattles', 'engine-diagnostics')
    try:
        _SESSION = Probe(os.path.abspath(directory))
        sys.stdout.write('[Offline LAN 0.9.22] ENGINE_DIAG mode=%s file=%s\n' % (_MODE, _SESSION.writer.path))
    except Exception as error:
        sys.stdout.write('[Offline LAN 0.9.22] ENGINE_DIAG init_failed=%s\n' % type(error).__name__)


def track(appearance, is_player=None):
    if _SESSION is not None:
        try:
            _SESSION.track(appearance, is_player)
        except Exception:
            pass


def note(appearance, phase, **fields):
    if _SESSION is not None:
        try:
            _SESSION.note(appearance, phase, **fields)
        except Exception as error:
            _ascii_notice('note_failed type=' + type(error).__name__)


def stop():
    global _SESSION
    session = _SESSION
    _SESSION = None
    if session is not None:
        session.close()
