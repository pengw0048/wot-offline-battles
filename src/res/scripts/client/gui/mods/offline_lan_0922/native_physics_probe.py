"""Staged, fail-closed probe of #1513's native vehicle physics surface.

Diagnostic evidence only.  The hidden worker already owns one stock
``Vehicle`` entity per Bot, and the pinned client's complete
``Vehicle.__startWGPhysics`` still runs for each of them, so every Bot filter
carries a ``WGVehiclePhysics`` created by retail code.  This probe inspects
those objects, constructs standalone ``WGVehiclePhysics`` /
``WGDynamicsSimulator`` / ``WGPhysicalBody`` instances, harvests method
signatures from the binding's own argument errors, drives one Bot body through
``movementSignals`` and an explicit ``WGDynamicsSimulator.update`` batch, and
times a full-roster batch.

Each stage runs on its own render frame, logs ``NPHYS stage=<name> begin``
before its first native call, and rewrites the JSON report after it finishes.
A native crash therefore leaves the last ``begin`` line in ``python.log`` and
every earlier stage's data on disk.  A Python exception is recorded and the
probe moves to the next stage; nothing here may raise into the frame.

The probe never calls ``WGVehicleFilter.setVehiclePhysics`` or
``syncGunAngles`` (the documented #1513 crash class) and never touches human
vehicles.  It changes no gameplay rule; it does move at most a few Bot bodies
for a couple of seconds while a stage is active.
"""

from __future__ import print_function

import json
import math
import os
import sys
import time


LOG_PREFIX = '[Offline LAN 0.9.22] NPHYS'
REPORT_PREFIX = 'offline-worker-native-physics-probe'
CONFIG_FILENAME = 'worker_diagnostics.json'

_CLOCK = getattr(time, 'perf_counter', None)
if not callable(_CLOCK):
    _CLOCK = time.clock

# Retail Avatar vehicle movement bit field (VEHICLE_MOVEMENT_FLAGS).
MOVE_FORWARD_SIGNAL = 1
MOVE_BACKWARD_SIGNAL = 2
ROTATE_LEFT_SIGNAL = 4
ROTATE_RIGHT_SIGNAL = 8

DEFAULT_CONFIG = {
    'enabled': True,
    'start_delay_seconds': 8.0,
    'stages': 'all',
    'inspect_entities': 3,
    'passive_seconds': 1.0,
    'drive_seconds': 2.0,
    'scale_frames': 60,
    'pair_seconds': 2.0,
    'drive_all': False,
    'disable_lsprof': False,
    'opt_in_stages': [],
    'fresh_attribute_reads': False,
}

# Attributes the exe's string table exposes on WGVehiclePhysics.  Every read
# is individually guarded; a missing or write-only attribute is recorded as
# such rather than treated as a failure.
PHYSICS_READ_ATTRIBUTES = (
    'mass', 'centerOfMass', 'staticMode', 'isFrozen', 'isFrozenDuringFrame',
    'allowFreeze', 'movementSignals', 'cruiseSignals', 'handbrake',
    'speed', 'rspeed', 'speedFromPreviousTick', 'angVelocity',
    'acceleration', 'angAcceleration', 'gravity', 'gotTracksContact',
    'gotCarcassContact', 'groundType', 'hullCOMZ', 'hullContactPt',
    'vehicleID', 'distanceTraveled', 'simulationYBound',
    'isLeftTrackBroken', 'isRightTrackBroken', 'leftTrackBrakeForce',
    'rightTrackBrakeForce', 'ticksFromLastCollision', 'timeAfterLanding',
    'badState', 'drownWarning', 'isOutOfControl', 'normalisedRPM',
    'maxUnaidedRPM', 'enginePowerMode', 'auxEnginePowerScale',
    'siegeModeState', 'hydroResistanceFactor', 'hydroResistanceRotFactor',
    'freezeAccelEpsilon', 'freezeAngAccelEpsilon', 'freezeVelEpsilon',
    'freezeAngVelEpsilon', 'stabilisedMatrixLatency',
    'hullPitchCorrection', 'isPitchHullAimingEnabled',
    'isYawHullAimingEnabled', 'quietRotationEnabled',
    'environmentEnergyCbThreshold', 'majorDestructible',
    'isSpeedtreeDestroyed', 'forceApplied', 'torqueApplied',
    'hullDamageMp', 'hullCollisionReaction', 'auxDataForClient',
    'lastTickMatrix', 'actualChassisTransform',
    'stabilisedMatrixWithLatency', 'groundResistances',
    'auxGroundRotFactors',
)
PHYSICS_MATRIX_ATTRIBUTES = (
    'lastTickMatrix', 'actualChassisTransform',
    'stabilisedMatrixWithLatency')
PHYSICS_CALLBACK_ATTRIBUTES = (
    'onEnvironmentCollisionCb', 'onRammingCb', 'onFrictionWithVehicleCb',
    'onBecameFrozenCb', 'onBecameStillCb', 'onKinematicsChangedCb',
    'onMiscDataChangedCb', 'onSteeringAngleChangedCb',
    'onSideMovementChangedCb', 'onEngineModeChangedCb',
    'onVehicleStatusChanged', 'destructibleRequestCb',
    'destructibleImpactCb')
SIMULATOR_ATTRIBUTES = (
    'numSubsteps', 'numIterations', 'numIterationsAccurate',
    'frictionRatio', 'restitution', 'allowedPenetration',
    'midSolvingIterations')
BODY_ATTRIBUTES = (
    'staticCollisionEnergy', 'staticCollisionReaction',
    'staticCollisionNormal', 'staticCollisionPoint',
    'staticCollisionSelfPoint', 'freezePosErrorEpsilon',
    'staticSceneFriction', 'isCollidingWithWorld', 'isCwwThresholdFactor')
# Methods whose argument contract is harvested from the binding's own
# TypeError text.  Only no-argument calls are made; a BigWorld binding checks
# its argument tuple before touching native state.
PHYSICS_SIGNATURE_METHODS = (
    'configure', 'rollback', 'setSignal', 'fireEngine', 'touchGround',
    'getTouchedGround', 'getTouchedMatkind', 'getPointVelocity',
    'getRollerPosition', 'getAggressiveImpacts', 'applyImpulseToCoM',
    'setHullAimingAnglesDelta', 'setArenaBounds', 'enableTurretCollision',
    'addDamperSpring', 'setDamperSpringsLength', 'removeAllDamperSprings',
    'subscribeBeforeSimulation', 'subscribeAfterSimulation',
    'removeAllSubscriptions')
SIMULATOR_SIGNATURE_METHODS = ('update', 'setUseSseSolver')
BODY_SIGNATURE_METHODS = (
    'setup', 'addShape', 'addBoxShape', 'setCoreSegment',
    'getProjectionArea', 'removeShapes')
FILTER_READ_ATTRIBUTES = (
    'speedInfo', 'bodyMatrix', 'vehicleWidth', 'maxMove', 'maxRotate',
    'allowStrafe', 'strafeSpeed', 'allowStop', 'vehicleCollisionMargin',
    'isStrafing')
STAGE_ORDER = (
    'inventory', 'inspect_existing', 'passive_drive', 'solve_one',
    'solve_scale', 'solve_pair', 'restore', 'construct_standalone',
    'signatures')
# Stages that are skipped unless worker_diagnostics.json opts in.  The first
# Windows run died with EXCEPTION_ACCESS_VIOLATION (read @ 0x638) inside
# construct_standalone, so anything that touches a body the retail client did
# not configure is opt-in until the crash address has been attributed.
OPT_IN_STAGES = ('construct_standalone', 'signatures')


def load_config(config_dir):
    """Merge ``worker_diagnostics.json`` over the defaults; never raise."""
    merged = dict(DEFAULT_CONFIG)
    path = os.path.join(config_dir, CONFIG_FILENAME)
    try:
        with open(path, 'rb') as stream:
            payload = json.loads(stream.read().decode('utf-8'))
    except Exception:
        return merged, False
    section = payload.get('native_physics_probe') if isinstance(
        payload, dict) else None
    if isinstance(section, dict):
        for name in DEFAULT_CONFIG:
            if name in section:
                merged[name] = section[name]
    return merged, True


def _type_name(value):
    try:
        return type(value).__name__
    except Exception:
        return '<unknown>'


def _plain(value, depth=0):
    """Reduce a native value to JSON-serialisable plain data."""
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or
                                         math.isinf(value)):
            return repr(value)
        return value
    try:
        string_types = (str, unicode)  # noqa: F821  (Python 2)
    except NameError:
        string_types = (str,)
    if isinstance(value, string_types):
        return value[:200]
    if depth > 2:
        return _type_name(value)
    translation = getattr(value, 'translation', None)
    if translation is not None:
        try:
            result = {'type': _type_name(value),
                      'translation': _xyz(translation)}
            yaw = getattr(value, 'yaw', None)
            if yaw is not None:
                result['yaw'] = float(yaw)
            return result
        except Exception:
            pass
    if all(hasattr(value, name) for name in ('x', 'y', 'z')):
        try:
            return _xyz(value)
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in list(value)[:16]]
    if isinstance(value, dict):
        return dict((str(key), _plain(item, depth + 1))
                    for key, item in list(value.items())[:64])
    return '%s:%s' % (_type_name(value), repr(value)[:120])


def _xyz(value):
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except Exception:
        return [float(value[0]), float(value[1]), float(value[2])]


def _matrix_translation(matrix, math_module=None):
    """Return the translation of a matrix or matrix provider, or None."""
    if matrix is None:
        return None
    translation = getattr(matrix, 'translation', None)
    if translation is None and math_module is not None:
        try:
            translation = math_module.Matrix(matrix).translation
        except Exception:
            translation = None
    if translation is None:
        return None
    try:
        return _xyz(translation)
    except Exception:
        return None


def _distance(first, second):
    if first is None or second is None:
        return None
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(first, second)))


def _read_attributes(target, names):
    result = {}
    for name in names:
        try:
            result[name] = _plain(getattr(target, name))
        except AttributeError as error:
            result[name] = '<attribute error: %s>' % str(error)[:120]
        except Exception as error:
            result[name] = '<%s: %s>' % (_type_name(error), str(error)[:120])
    return result


def _harvest_signatures(target, names):
    """Call each method with no arguments and keep the binding's complaint."""
    result = {}
    for name in names:
        method = getattr(target, name, None)
        if method is None:
            result[name] = '<missing>'
            continue
        if not callable(method):
            result[name] = '<not callable: %s>' % _type_name(method)
            continue
        try:
            value = method()
        except TypeError as error:
            result[name] = 'TypeError: %s' % str(error)[:200]
        except Exception as error:
            result[name] = '%s: %s' % (_type_name(error), str(error)[:200])
        else:
            result[name] = 'returned %s' % json.dumps(_plain(value))[:200]
    return result


def _function_signature(function):
    code = getattr(function, 'func_code', getattr(function, '__code__', None))
    if code is None:
        return '<builtin>'
    names = list(code.co_varnames[:code.co_argcount])
    defaults = getattr(function, 'func_defaults',
                       getattr(function, '__defaults__', None)) or ()
    if defaults:
        offset = len(names) - len(defaults)
        names = names[:offset] + [
            '%s=%r' % (name, value)
            for name, value in zip(names[offset:], defaults)]
    return '(%s)' % ', '.join(names)


class WorkerPhysicsProbe(object):
    """Run the staged probe from the hidden worker's render callback."""

    def __init__(self, host, output_dir, config=None, writer=None,
                 clock=None, perf_clock=None):
        self._host = host
        self._output_dir = output_dir
        self._config = dict(DEFAULT_CONFIG)
        if config:
            self._config.update(config)
        self._writer = writer or sys.stdout.write
        self._clock = clock or time.time
        self._perf = perf_clock or _CLOCK
        self._round_id = None
        self._live_since = None
        self._done = False
        self._stage_index = 0
        self._stage_state = None
        self._report = {
            'schema': 1, 'diagnostic': 'native_physics_probe',
            'config': dict(self._config), 'stages': [], 'last_begun': None}
        self._current = None
        self._simulator = None
        self._standalone = {}
        self._driven = []
        self._callback_events = []
        stages = self._config.get('stages')
        opted = set(self._config.get('opt_in_stages') or ())
        if stages == 'all' or not stages:
            self._stages = [name for name in STAGE_ORDER
                            if name not in OPT_IN_STAGES or name in opted]
        else:
            self._stages = [name for name in STAGE_ORDER if name in stages]
        if 'restore' not in self._stages:
            self._stages.append('restore')

    # ------------------------------------------------------------------ frame
    @property
    def done(self):
        return self._done

    def tick(self, battle_live, round_id=None, frame_dt=0.0):
        """Advance at most one stage step; never raise."""
        if self._done or not self._config.get('enabled', True):
            return False
        try:
            return self._tick(bool(battle_live), round_id, float(frame_dt))
        except Exception as error:
            # The stage machine itself failed.  Record, restore, stop.
            self._log('machine failure error=%r' % (error,))
            try:
                self._stage_restore(0.0)
            except Exception:
                pass
            self._report['machine_error'] = repr(error)
            self._write_report()
            self._done = True
            return False

    def _tick(self, battle_live, round_id, frame_dt):
        now = self._clock()
        if round_id != self._round_id:
            self._round_id = round_id
            self._live_since = None
        if not battle_live:
            self._live_since = None
            return False
        if self._live_since is None:
            self._live_since = now
            self._log('armed round=%s stages=%s start_delay=%.1f' % (
                round_id, ','.join(self._stages),
                float(self._config['start_delay_seconds'])))
            return False
        if now - self._live_since < float(self._config['start_delay_seconds']):
            return False
        if self._stage_index >= len(self._stages):
            self._finish()
            return False
        name = self._stages[self._stage_index]
        if self._current is None:
            self._current = {
                'name': name, 'status': 'running', 'frames': 0,
                'begun_at': now, 'data': {}, 'error': None}
            self._report['stages'].append(self._current)
            self._report['last_begun'] = name
            self._stage_state = {}
            self._log('stage=%s begin' % name)
            self._write_report()
        self._current['frames'] += 1
        handler = getattr(self, '_stage_' + name)
        try:
            complete = handler(frame_dt)
        except Exception as error:
            self._current['status'] = 'error'
            self._current['error'] = '%s: %s' % (
                _type_name(error), str(error)[:300])
            self._log('stage=%s error=%s' % (name, self._current['error']))
            complete = True
        if complete:
            if self._current['status'] == 'running':
                self._current['status'] = 'ok'
            self._current['wall_ms'] = (
                self._clock() - self._current['begun_at']) * 1000.0
            self._log('stage=%s end status=%s frames=%d' % (
                name, self._current['status'], self._current['frames']))
            self._current = None
            self._stage_index += 1
            self._write_report()
        return True

    def _finish(self):
        self._done = True
        self._report['completed'] = True
        self._report['callback_events'] = self._callback_events[:200]
        self._write_report()
        self._log('done report=%s' % self._report_path())

    # --------------------------------------------------------------- helpers
    def _log(self, text):
        try:
            self._writer('%s %s\n' % (LOG_PREFIX, text))
        except Exception:
            pass

    def _step(self, text):
        """Announce the next native call so a crash names it in python.log."""
        name = self._current['name'] if self._current else '-'
        self._log('stage=%s step=%s' % (name, text))
        if self._current is not None:
            self._current['last_step'] = text

    def _report_path(self):
        return os.path.join(self._output_dir, '%s-round%s.json' % (
            REPORT_PREFIX, self._round_id))

    def _write_report(self):
        try:
            if not os.path.isdir(self._output_dir):
                os.makedirs(self._output_dir)
            payload = json.dumps(
                self._report, indent=1, sort_keys=True, default=_plain)
            with open(self._report_path(), 'wb') as stream:
                stream.write(payload.encode('utf-8'))
        except Exception as error:
            self._log('report write failed error=%r' % (error,))

    def _bigworld(self):
        return self._host.bigworld()

    def _math(self):
        return self._host.math_module()

    def _bots(self, limit=None):
        entries = list(self._host.bot_entities())
        if limit is not None:
            entries = entries[:int(limit)]
        return entries

    def _physics_of(self, entity):
        vehicle_filter = getattr(entity, 'filter', None)
        getter = getattr(vehicle_filter, 'getVehiclePhysics', None)
        if not callable(getter):
            return vehicle_filter, None
        return vehicle_filter, getter()

    def _pose_snapshot(self, entity, physics):
        math_module = self._math()
        result = {}
        try:
            result['entity_position'] = _xyz(entity.position)
        except Exception:
            result['entity_position'] = None
        result['entity_matrix'] = _matrix_translation(
            getattr(entity, 'matrix', None), math_module)
        vehicle_filter = getattr(entity, 'filter', None)
        result['filter_body'] = _matrix_translation(
            getattr(vehicle_filter, 'bodyMatrix', None), math_module)
        if physics is not None:
            for name in PHYSICS_MATRIX_ATTRIBUTES:
                try:
                    result[name] = _matrix_translation(
                        getattr(physics, name), math_module)
                except Exception as error:
                    result[name] = '<%s>' % str(error)[:80]
            for name in ('speed', 'isFrozen', 'movementSignals',
                         'gotTracksContact', 'groundType',
                         'distanceTraveled'):
                try:
                    result[name] = _plain(getattr(physics, name))
                except Exception:
                    result[name] = None
        return result

    def _install_callbacks(self, physics, label):
        installed = []
        for name in PHYSICS_CALLBACK_ATTRIBUTES:
            def _make(callback_name):
                def _callback(*args):
                    if len(self._callback_events) < 200:
                        self._callback_events.append({
                            'body': label, 'callback': callback_name,
                            'args': [_plain(value) for value in args[:6]]})
                    return None
                return _callback
            try:
                setattr(physics, name, _make(name))
                installed.append(name)
            except Exception:
                pass
        return installed

    def _set_signals(self, entity, physics, signals, movement, rotation):
        detail = {}
        try:
            physics.movementSignals = int(signals)
            detail['movementSignals'] = _plain(physics.movementSignals)
        except Exception as error:
            detail['movementSignals'] = '<%s>' % str(error)[:80]
        if signals:
            try:
                physics.isFrozen = False
                detail['isFrozen'] = _plain(physics.isFrozen)
            except Exception as error:
                detail['isFrozen'] = '<%s>' % str(error)[:80]
        vehicle_filter = getattr(entity, 'filter', None)
        notify = getattr(vehicle_filter, 'notifyInputKeysDown', None)
        if callable(notify):
            try:
                notify(int(movement), int(rotation))
                detail['notifyInputKeysDown'] = [int(movement), int(rotation)]
            except Exception as error:
                detail['notifyInputKeysDown'] = '<%s>' % str(error)[:80]
        return detail

    def _simulator_for_stage(self):
        if self._simulator is None:
            self._simulator = self._bigworld().WGDynamicsSimulator()
        return self._simulator

    def _timed_update(self, simulator, dt, physics_list):
        started = self._perf()
        simulator.update(float(dt), list(physics_list), [])
        return (self._perf() - started) * 1000.0

    # ---------------------------------------------------------------- stages
    def _stage_inventory(self, frame_dt):
        data = self._current['data']
        bigworld = self._bigworld()
        names = sorted(
            name for name in dir(bigworld)
            if 'WG' in name or 'Physic' in name or 'Dynamic' in name or
            'Filter' in name)
        data['bigworld_names'] = names
        data['movement_flags'] = None
        try:
            constants = self._host.constants()
            flags = getattr(constants, 'VEHICLE_MOVEMENT_FLAGS', None)
            data['movement_flags'] = dict(
                (name, getattr(flags, name))
                for name in dir(flags) if name.isupper()) if flags else None
        except Exception as error:
            data['movement_flags'] = '<%s>' % str(error)[:80]
        try:
            import physics_shared
        except Exception as error:
            data['physics_shared'] = '<import failed: %s>' % str(error)[:120]
            return True
        functions = {}
        constants = {}
        for name in sorted(dir(physics_shared)):
            if name.startswith('__'):
                continue
            value = getattr(physics_shared, name)
            if isinstance(value, (bool, int, float)):
                constants[name] = value
                continue
            if callable(value) and not isinstance(value, type):
                functions[name] = _function_signature(
                    getattr(value, '__func__', value))
            elif isinstance(value, (bool, int, float)):
                constants[name] = value
            elif isinstance(value, dict) and len(value) <= 64:
                constants[name] = sorted(str(key) for key in value)[:64]
        data['physics_shared'] = {
            'functions': functions, 'constants': constants}
        return True

    def _stage_inspect_existing(self, frame_dt):
        data = self._current['data']
        entries = []
        for bot in self._bots(self._config.get('inspect_entities', 3)):
            entity = bot['entity']
            self._step('bot:%s filter.getVehiclePhysics' % bot['bot_id'])
            vehicle_filter, physics = self._physics_of(entity)
            entry = {
                'bot_id': bot['bot_id'],
                'entity_type': _type_name(entity),
                'filter_type': _type_name(vehicle_filter),
            }
            self._step('bot:%s filter attributes' % bot['bot_id'])
            entry['filter_attributes'] = _read_attributes(
                vehicle_filter, FILTER_READ_ATTRIBUTES)
            entry['physics_type'] = _type_name(physics)
            if physics is not None:
                entry['physics_dir'] = sorted(
                    name for name in dir(physics) if not name.startswith('__'))
                entry['physics_attributes'] = {}
                for name in PHYSICS_READ_ATTRIBUTES:
                    self._step('bot:%s physics.%s' % (bot['bot_id'], name))
                    entry['physics_attributes'].update(
                        _read_attributes(physics, (name,)))
                self._step('bot:%s pose snapshot' % bot['bot_id'])
                entry['pose'] = self._pose_snapshot(entity, physics)
            entries.append(entry)
        data['entities'] = entries
        data['bot_count'] = len(self._bots())
        return True

    def _stage_construct_standalone(self, frame_dt):
        data = self._current['data']
        bigworld = self._bigworld()
        self._step('BigWorld.WGVehiclePhysics()')
        physics = bigworld.WGVehiclePhysics()
        self._standalone['physics'] = physics
        data['physics_dir'] = sorted(
            name for name in dir(physics) if not name.startswith('__'))
        if self._config.get('fresh_attribute_reads'):
            # Reading an unconfigured body is exactly where the first Windows
            # run died; keep it opt-in and one attribute per logged step.
            data['physics_attributes_fresh'] = {}
            for name in PHYSICS_READ_ATTRIBUTES:
                self._step('fresh physics.%s' % name)
                data['physics_attributes_fresh'].update(
                    _read_attributes(physics, (name,)))
        self._step('BigWorld.WGDynamicsSimulator()')
        simulator = self._simulator_for_stage()
        data['simulator_dir'] = sorted(
            name for name in dir(simulator) if not name.startswith('__'))
        self._step('simulator attributes')
        data['simulator_attributes'] = _read_attributes(
            simulator, SIMULATOR_ATTRIBUTES)
        self._step('BigWorld.WGPhysicalBody()')
        body = bigworld.WGPhysicalBody()
        self._standalone['body'] = body
        data['body_dir'] = sorted(
            name for name in dir(body) if not name.startswith('__'))
        self._step('body attributes')
        data['body_attributes'] = _read_attributes(body, BODY_ATTRIBUTES)
        # Initialise the standalone body exactly as the retail client does for
        # its own vehicle: through physics_shared, on a real descriptor.
        bots = self._bots(1)
        if not bots:
            data['init'] = '<no ready bot descriptor>'
            return True
        descriptor = getattr(bots[0]['entity'], 'typeDescriptor', None)
        attempts = []
        try:
            import physics_shared
        except Exception as error:
            data['init'] = '<physics_shared import failed: %s>' % str(error)
            return True
        candidates = (
            ('initVehiclePhysicsClient', (physics, descriptor)),
            ('initVehiclePhysics', (physics, descriptor)),
            ('initVehiclePhysics', (physics, descriptor, None, True)),
        )
        for name, args in candidates:
            function = getattr(physics_shared, name, None)
            if function is None:
                attempts.append({'call': name, 'result': '<missing>'})
                continue
            self._step('physics_shared.%s/%d' % (name, len(args)))
            try:
                function(*args)
            except Exception as error:
                attempts.append({
                    'call': '%s/%d' % (name, len(args)),
                    'result': '%s: %s' % (_type_name(error), str(error)[:200])})
                continue
            attempts.append({'call': '%s/%d' % (name, len(args)),
                             'result': 'ok'})
            break
        data['init'] = attempts
        initialised = any(item['result'] == 'ok' for item in attempts)
        data['physics_attributes_initialised'] = {}
        if initialised:
            for name in PHYSICS_READ_ATTRIBUTES:
                self._step('initialised physics.%s' % name)
                data['physics_attributes_initialised'].update(
                    _read_attributes(physics, (name,)))
        self._step('physics.setArenaBounds')
        try:
            physics.setArenaBounds((-10000, -10000), (10000, 10000))
            data['setArenaBounds'] = 'ok'
        except Exception as error:
            data['setArenaBounds'] = '%s: %s' % (_type_name(error), str(error))
        return True

    def _stage_signatures(self, frame_dt):
        data = self._current['data']
        physics = self._standalone.get('physics')
        if physics is None:
            self._step('BigWorld.WGVehiclePhysics()')
            physics = self._bigworld().WGVehiclePhysics()
            self._standalone['physics'] = physics
        data['physics'] = {}
        for name in PHYSICS_SIGNATURE_METHODS:
            self._step('physics.%s()' % name)
            data['physics'].update(_harvest_signatures(physics, (name,)))
        simulator = self._simulator_for_stage()
        data['simulator'] = {}
        for name in SIMULATOR_SIGNATURE_METHODS:
            self._step('simulator.%s()' % name)
            data['simulator'].update(_harvest_signatures(simulator, (name,)))
        body = self._standalone.get('body')
        if body is None:
            self._step('BigWorld.WGPhysicalBody()')
            body = self._bigworld().WGPhysicalBody()
            self._standalone['body'] = body
        data['body'] = {}
        for name in BODY_SIGNATURE_METHODS:
            self._step('body.%s()' % name)
            data['body'].update(_harvest_signatures(body, (name,)))
        return True

    def _drive_target(self, index):
        bots = self._bots()
        if len(bots) <= index:
            return None, None, None
        bot = bots[index]
        vehicle_filter, physics = self._physics_of(bot['entity'])
        return bot, vehicle_filter, physics

    def _stage_passive_drive(self, frame_dt):
        """Signals and filter input only; nobody calls the simulator."""
        data = self._current['data']
        state = self._stage_state
        bot, unused_filter, physics = self._drive_target(0)
        if physics is None:
            data['result'] = '<bot 0 has no physics>'
            return True
        if 'start' not in state:
            data['bot_id'] = bot['bot_id']
            self._step('bot:%s install callbacks' % bot['bot_id'])
            data['callbacks_installed'] = self._install_callbacks(
                physics, 'bot:%s' % bot['bot_id'])
            self._step('bot:%s pose snapshot' % bot['bot_id'])
            data['before'] = self._pose_snapshot(bot['entity'], physics)
            self._step('bot:%s movementSignals/notifyInputKeysDown' %
                       bot['bot_id'])
            data['input'] = self._set_signals(
                bot['entity'], physics, MOVE_FORWARD_SIGNAL, 1, 0)
            state['start'] = self._clock()
            self._driven.append(bot['bot_id'])
            return False
        if self._clock() - state['start'] < float(
                self._config['passive_seconds']):
            return False
        data['after'] = self._pose_snapshot(bot['entity'], physics)
        data['moved_m'] = _distance(
            (data['before'] or {}).get('lastTickMatrix'),
            (data['after'] or {}).get('lastTickMatrix'))
        data['entity_moved_m'] = _distance(
            (data['before'] or {}).get('entity_position'),
            (data['after'] or {}).get('entity_position'))
        data['stop'] = self._set_signals(bot['entity'], physics, 0, 0, 0)
        return True

    def _stage_solve_one(self, frame_dt):
        """Drive Bot 0 forward through an explicit simulator batch."""
        data = self._current['data']
        state = self._stage_state
        bot, unused_filter, physics = self._drive_target(0)
        if physics is None:
            data['result'] = '<bot 0 has no physics>'
            return True
        if 'start' not in state:
            self._step('BigWorld.WGDynamicsSimulator()')
            self._simulator_for_stage()
            data['bot_id'] = bot['bot_id']
            data['before'] = self._pose_snapshot(bot['entity'], physics)
            self._step('bot:%s movementSignals/notifyInputKeysDown' %
                       bot['bot_id'])
            data['input'] = self._set_signals(
                bot['entity'], physics, MOVE_FORWARD_SIGNAL, 1, 0)
            state['start'] = self._clock()
            state['updates'] = []
            state['samples'] = []
            self._driven.append(bot['bot_id'])
            return False
        simulator = self._simulator_for_stage()
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if not state['updates']:
            self._step('simulator.update(dt, [bot:%s], [])' % bot['bot_id'])
        state['updates'].append(self._timed_update(simulator, dt, [physics]))
        if len(state['samples']) < 12:
            state['samples'].append(
                self._pose_snapshot(bot['entity'], physics))
        if self._clock() - state['start'] < float(
                self._config['drive_seconds']):
            return False
        data['after'] = self._pose_snapshot(bot['entity'], physics)
        updates = state['updates']
        data['update_ms'] = {
            'calls': len(updates),
            'min': min(updates) if updates else None,
            'avg': (sum(updates) / len(updates)) if updates else None,
            'max': max(updates) if updates else None,
        }
        data['samples'] = state['samples']
        data['moved_m'] = _distance(
            (data['before'] or {}).get('lastTickMatrix'),
            (data['after'] or {}).get('lastTickMatrix'))
        data['entity_moved_m'] = _distance(
            (data['before'] or {}).get('entity_position'),
            (data['after'] or {}).get('entity_position'))
        data['stop'] = self._set_signals(bot['entity'], physics, 0, 0, 0)
        try:
            data['touched_ground'] = _plain(physics.getTouchedGround())
        except Exception as error:
            data['touched_ground'] = '<%s>' % str(error)[:80]
        try:
            data['aggressive_impacts'] = _plain(physics.getAggressiveImpacts())
        except Exception as error:
            data['aggressive_impacts'] = '<%s>' % str(error)[:80]
        return True

    def _stage_solve_scale(self, frame_dt):
        """Time one batch update over every ready Bot body, no drive."""
        data = self._current['data']
        state = self._stage_state
        if 'bodies' not in state:
            bodies = []
            ids = []
            for bot in self._bots():
                unused_filter, physics = self._physics_of(bot['entity'])
                if physics is not None:
                    bodies.append(physics)
                    ids.append(bot['bot_id'])
                    if self._config.get('drive_all'):
                        self._set_signals(
                            bot['entity'], physics, MOVE_FORWARD_SIGNAL, 1, 0)
                        self._driven.append(bot['bot_id'])
            state['bodies'] = bodies
            state['ids'] = ids
            state['updates'] = []
            data['body_count'] = len(bodies)
            data['bot_ids'] = ids
            if not bodies:
                data['result'] = '<no physics bodies>'
                return True
            return False
        simulator = self._simulator_for_stage()
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if not state['updates']:
            self._step('simulator.update(dt, %d bodies, [])' %
                       len(state['bodies']))
        state['updates'].append(
            self._timed_update(simulator, dt, state['bodies']))
        if len(state['updates']) < int(self._config['scale_frames']):
            return False
        updates = sorted(state['updates'])
        count = len(updates)
        data['update_ms'] = {
            'calls': count, 'min': updates[0],
            'p50': updates[count // 2], 'p95': updates[int(count * 0.95)],
            'max': updates[-1], 'avg': sum(updates) / count,
        }
        data['per_body_avg_ms'] = (
            data['update_ms']['avg'] / max(1, data['body_count']))
        return True

    def _stage_solve_pair(self, frame_dt):
        """Drive Bots 0 and 1 toward each other for contact callbacks."""
        data = self._current['data']
        state = self._stage_state
        first, unused_a, physics_a = self._drive_target(0)
        second, unused_b, physics_b = self._drive_target(1)
        if physics_a is None or physics_b is None:
            data['result'] = '<fewer than two physics bodies>'
            return True
        simulator = self._simulator_for_stage()
        if 'start' not in state:
            data['bot_ids'] = [first['bot_id'], second['bot_id']]
            data['before'] = [
                self._pose_snapshot(first['entity'], physics_a),
                self._pose_snapshot(second['entity'], physics_b)]
            self._install_callbacks(physics_b, 'bot:%s' % second['bot_id'])
            data['input'] = [
                self._set_signals(
                    first['entity'], physics_a, MOVE_FORWARD_SIGNAL, 1, 0),
                self._set_signals(
                    second['entity'], physics_b, MOVE_FORWARD_SIGNAL, 1, 0)]
            data['events_before'] = len(self._callback_events)
            state['start'] = self._clock()
            self._driven.extend([first['bot_id'], second['bot_id']])
            return False
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if 'stepped' not in state:
            state['stepped'] = True
            self._step('simulator.update(dt, [bot:%s, bot:%s], [])' % (
                first['bot_id'], second['bot_id']))
        self._timed_update(simulator, dt, [physics_a, physics_b])
        if self._clock() - state['start'] < float(
                self._config['pair_seconds']):
            return False
        data['after'] = [
            self._pose_snapshot(first['entity'], physics_a),
            self._pose_snapshot(second['entity'], physics_b)]
        data['events_after'] = len(self._callback_events)
        data['stop'] = [
            self._set_signals(first['entity'], physics_a, 0, 0, 0),
            self._set_signals(second['entity'], physics_b, 0, 0, 0)]
        return True

    def _stage_restore(self, frame_dt):
        """Zero every signal this probe set.  Never raises."""
        data = self._current['data'] if self._current else {}
        restored = []
        for bot in self._bots():
            if bot['bot_id'] not in self._driven:
                continue
            try:
                unused_filter, physics = self._physics_of(bot['entity'])
                if physics is not None:
                    self._set_signals(bot['entity'], physics, 0, 0, 0)
                restored.append(bot['bot_id'])
            except Exception as error:
                restored.append('%s:<%s>' % (bot['bot_id'], str(error)[:60]))
        data['restored'] = restored
        self._driven = []
        return True


class _RuntimeHost(object):
    """Narrow read-only view of BattleRuntime for the probe."""

    def __init__(self, runtime):
        self._runtime = runtime

    def bigworld(self):
        return self._runtime._runtime.bigworld

    def math_module(self):
        return getattr(self._runtime._runtime, 'math', None)

    def constants(self):
        return getattr(self._runtime._runtime, 'constants', None)

    def bot_entities(self):
        runtime = self._runtime
        result = []
        for key, record in sorted(runtime._records.items()):
            if (not isinstance(key, str) or not key.startswith('bot:') or
                    not isinstance(record, dict) or not record.get('ready') or
                    record.get('tombstone')):
                continue
            try:
                entity = runtime._server_entity(record.get('engine_id'))
            except Exception:
                entity = None
            if entity is None or getattr(entity, 'filter', None) is None:
                continue
            try:
                bot_id = int(key.split(':', 1)[1])
            except ValueError:
                continue
            result.append({'bot_id': bot_id, 'entity': entity})
        return result


class DisabledProbe(object):
    done = True

    def tick(self, battle_live, round_id=None, frame_dt=0.0):
        return False


def create_for_worker(runtime, output_dir=None, writer=None):
    """Build the probe for one hidden worker, or a no-op on any failure."""
    writer = writer or sys.stdout.write
    try:
        if output_dir is None:
            from gui.mods.offline_lan_0922 import config as port_config
            output_dir = os.path.dirname(port_config.CONFIG_PATH)
        config, explicit = load_config(output_dir)
        if not config.get('enabled', True):
            writer('%s disabled by %s\n' % (LOG_PREFIX, CONFIG_FILENAME))
            return DisabledProbe()
        probe = WorkerPhysicsProbe(_RuntimeHost(runtime), output_dir,
                                   config=config, writer=writer)
        writer('%s created config=%s source=%s\n' % (
            LOG_PREFIX, json.dumps(config, sort_keys=True),
            'file' if explicit else 'defaults'))
        return probe
    except Exception as error:
        try:
            writer('%s unavailable: %r\n' % (LOG_PREFIX, error))
        except Exception:
            pass
        return DisabledProbe()
