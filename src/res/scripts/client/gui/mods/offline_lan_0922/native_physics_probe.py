"""Staged, fail-closed probe of #1513's native vehicle physics surface.

Diagnostic evidence only.  Two Windows runs established the ground truth this
version is built on:

* The Python carrier the runtime hands out for a Bot (``RemoteVehicle``) owns
  a Python ``_RemoteFilter``; the native ``Vehicle`` entity behind it owns the
  stock ``WGVehicleFilter``.  Whether that native filter carries a retail
  ``WGVehiclePhysics`` is exactly what ``inspect_existing`` records.
* Reading an attribute of a ``WGVehiclePhysics`` that ``physics_shared`` has
  not initialised dereferences a NULL body (``mass`` getter,
  ``WorldOfTanks.exe+0xb50ad0``).  Bodies are therefore initialised before the
  first attribute read, and the pre-initialisation read is opt-in.

Stages run one per render frame from ``start_delay_seconds`` after the battle
goes live.  Every native call group is announced with
``NPHYS stage=<name> step=<call>`` before it runs, and the JSON report is
rewritten after each stage, so a native crash leaves the last step in
``python.log`` and every earlier stage's data on disk.  A Python exception is
recorded and the probe continues; nothing here may raise into the frame.

Bodies used by the drive/solve stages are retail bodies when the native filter
exposes them, otherwise standalone bodies the probe constructs and initialises
through ``physics_shared`` from the Bot's own descriptor.  Standalone bodies
have no presentation; their read-back matrices are the evidence.  The probe
never calls ``WGVehicleFilter.setVehiclePhysics`` or ``syncGunAngles`` and
never touches human vehicles.
"""

from __future__ import print_function

import json
import math
import os
import sys
import time
import weakref


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
    'scale_bodies': 29,
    'pair_seconds': 2.0,
    'drive_all': False,
    'disable_lsprof': False,
    'opt_in_stages': [],
    'fresh_attribute_reads': False,
    'allow_standalone_bodies': True,
    # 'server' first: WGDynamicsSimulator is the retail server-style solver.
    'init_order': ['initVehiclePhysicsServer', 'initVehiclePhysicsClient'],
    # 'entity' installs weakref(native Vehicle) like retail; 'none' leaves it.
    'owner_mode': 'entity',
}

# Attributes the exe's string table exposes on WGVehiclePhysics.  Every read
# is individually guarded and announced; a missing or write-only attribute is
# recorded as such rather than treated as a failure.
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
# TypeError text.  Only no-argument calls are made.  Methods that would act
# with defaults are excluded here and tried explicitly by _seed_pose.
PHYSICS_SIGNATURE_METHODS = (
    'configure', 'rollback', 'setSignal', 'getTouchedGround',
    'getTouchedMatkind', 'getPointVelocity', 'getRollerPosition',
    'getAggressiveImpacts', 'applyImpulseToCoM', 'setHullAimingAnglesDelta',
    'setArenaBounds', 'enableTurretCollision', 'addDamperSpring',
    'setDamperSpringsLength', 'subscribeBeforeSimulation',
    'subscribeAfterSimulation')
SIMULATOR_SIGNATURE_METHODS = ('update', 'setUseSseSolver')
BODY_SIGNATURE_METHODS = (
    'setup', 'addShape', 'addBoxShape', 'setCoreSegment',
    'getProjectionArea')
FILTER_READ_ATTRIBUTES = (
    'speedInfo', 'bodyMatrix', 'groundPlacingMatrix', 'vehicleWidth',
    'maxMove', 'maxRotate', 'allowStrafe', 'strafeSpeed', 'allowStop',
    'vehicleCollisionMargin', 'isStrafing')
STAGE_ORDER = (
    'inventory', 'inspect_existing', 'construct_standalone',
    'passive_drive', 'solve_one', 'solve_scale', 'solve_pair', 'restore',
    'signatures')
OPT_IN_STAGES = ('signatures',)


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


def _xyz(value):
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except Exception:
        return [float(value[0]), float(value[1]), float(value[2])]


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


def _read_attribute(target, name):
    try:
        return _plain(getattr(target, name))
    except AttributeError as error:
        return '<attribute error: %s>' % str(error)[:120]
    except Exception as error:
        return '<%s: %s>' % (_type_name(error), str(error)[:120])


def _call_no_args(target, name):
    method = getattr(target, name, None)
    if method is None:
        return '<missing>'
    if not callable(method):
        return '<not callable: %s>' % _type_name(method)
    try:
        value = method()
    except TypeError as error:
        return 'TypeError: %s' % str(error)[:200]
    except Exception as error:
        return '%s: %s' % (_type_name(error), str(error)[:200])
    return 'returned %s' % json.dumps(_plain(value))[:200]


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
            'schema': 2, 'diagnostic': 'native_physics_probe',
            'config': dict(self._config), 'stages': [], 'last_begun': None}
        self._current = None
        self._simulator = None
        self._standalone = {}
        self._bodies = []          # acquired body records for drive/solve
        self._bodies_source = None
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
        self._report['bodies_source'] = self._bodies_source
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

    @staticmethod
    def _retail_physics(bot):
        """Return (filter, physics) of the native entity's stock filter."""
        native = bot.get('native')
        vehicle_filter = getattr(native, 'filter', None)
        getter = getattr(vehicle_filter, 'getVehiclePhysics', None)
        if not callable(getter):
            return vehicle_filter, None
        return vehicle_filter, getter()

    def _read_attributes(self, target, names, label):
        result = {}
        for name in names:
            self._step('%s.%s' % (label, name))
            result[name] = _read_attribute(target, name)
        return result

    def _pose_snapshot(self, body):
        math_module = self._math()
        physics = body['physics']
        result = {}
        bot = body.get('bot') or {}
        result['bot_position'] = bot.get('position')
        native = bot.get('native')
        if native is not None:
            try:
                result['native_position'] = _xyz(native.position)
            except Exception:
                result['native_position'] = None
            vehicle_filter = getattr(native, 'filter', None)
            result['filter_body'] = _matrix_translation(
                getattr(vehicle_filter, 'bodyMatrix', None), math_module)
        for name in PHYSICS_MATRIX_ATTRIBUTES:
            try:
                result[name] = _matrix_translation(
                    getattr(physics, name), math_module)
            except Exception as error:
                result[name] = '<%s>' % str(error)[:80]
        for name in ('speed', 'isFrozen', 'movementSignals',
                     'gotTracksContact', 'groundType', 'distanceTraveled'):
            result[name] = _read_attribute(physics, name)
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

    def _set_signals(self, body, signals, movement, rotation):
        physics = body['physics']
        detail = {}
        try:
            physics.movementSignals = int(signals)
            detail['movementSignals'] = _read_attribute(
                physics, 'movementSignals')
        except Exception as error:
            detail['movementSignals'] = '<%s>' % str(error)[:80]
        if signals:
            try:
                physics.isFrozen = False
                detail['isFrozen'] = _read_attribute(physics, 'isFrozen')
            except Exception as error:
                detail['isFrozen'] = '<%s>' % str(error)[:80]
        if body.get('source') == 'retail':
            native = (body.get('bot') or {}).get('native')
            notify = getattr(getattr(native, 'filter', None),
                             'notifyInputKeysDown', None)
            if callable(notify):
                try:
                    notify(int(movement), int(rotation))
                    detail['notifyInputKeysDown'] = [
                        int(movement), int(rotation)]
                except Exception as error:
                    detail['notifyInputKeysDown'] = '<%s>' % str(error)[:80]
        return detail

    def _simulator_for_stage(self):
        if self._simulator is None:
            self._step('BigWorld.WGDynamicsSimulator()')
            simulator = self._bigworld().WGDynamicsSimulator()
            self._configure_simulator(simulator)
            self._simulator = simulator
        return self._simulator

    def _configure_simulator(self, simulator):
        """Apply physics_shared's solver constants like the retail server."""
        try:
            import physics_shared
        except Exception:
            return
        settings = (
            ('numSubsteps', 'NUM_SUBSTEPS', int),
            ('numIterations', 'NUM_ITERATIONS', int),
            ('frictionRatio', 'FRICTION_RATIO', float),
            ('restitution', 'RESTITUTION', float),
            ('allowedPenetration', 'ALLOWED_PENETRATION', float),
            ('midSolvingIterations', 'MID_SOLVING_ITERATIONS', int),
        )
        applied = {}
        for attribute, constant, cast in settings:
            value = getattr(physics_shared, constant, None)
            if value is None:
                continue
            self._step('simulator.%s = %r' % (attribute, value))
            try:
                setattr(simulator, attribute, cast(value))
                applied[attribute] = _read_attribute(simulator, attribute)
            except Exception as error:
                applied[attribute] = '<%s>' % str(error)[:80]
        self._report['simulator_settings'] = applied

    def _timed_update(self, simulator, dt, physics_list):
        started = self._perf()
        simulator.update(float(dt), list(physics_list), [])
        return (self._perf() - started) * 1000.0

    # ------------------------------------------------------- body acquisition
    def _construct_body(self, bot, label):
        """Construct, initialise and pose one standalone body; never raise."""
        record = {'label': label, 'source': 'standalone', 'bot': bot,
                  'physics': None, 'init': [], 'seed': None, 'owner': None}
        bigworld = self._bigworld()
        self._step('%s BigWorld.WGVehiclePhysics()' % label)
        physics = bigworld.WGVehiclePhysics()
        record['physics'] = physics
        descriptor = bot.get('descriptor')
        if descriptor is None:
            record['init'].append({'call': '-', 'result': '<no descriptor>'})
            return record
        try:
            import physics_shared
        except Exception as error:
            record['init'].append({
                'call': 'import physics_shared', 'result': str(error)[:120]})
            return record
        initialised = False
        for name in self._config.get('init_order') or ():
            function = getattr(physics_shared, name, None)
            if function is None:
                record['init'].append({'call': name, 'result': '<missing>'})
                continue
            self._step('%s physics_shared.%s' % (label, name))
            try:
                function(physics, descriptor)
            except Exception as error:
                record['init'].append({
                    'call': name,
                    'result': '%s: %s' % (_type_name(error), str(error)[:200])})
                continue
            record['init'].append({'call': name, 'result': 'ok'})
            initialised = True
            break
        record['initialised'] = initialised
        if not initialised:
            return record
        self._step('%s physics.setArenaBounds' % label)
        try:
            physics.setArenaBounds((-10000, -10000), (10000, 10000))
            record['setArenaBounds'] = 'ok'
        except Exception as error:
            record['setArenaBounds'] = '%s: %s' % (_type_name(error), str(error))
        for name, value in (('staticMode', False), ('movementSignals', 0)):
            self._step('%s physics.%s = %r' % (label, name, value))
            try:
                setattr(physics, name, value)
            except Exception as error:
                record['%s_set' % name] = '<%s>' % str(error)[:80]
        owner_mode = self._config.get('owner_mode', 'entity')
        target = bot.get('native') or bot.get('carrier')
        if owner_mode == 'entity' and target is not None:
            self._step('%s physics.owner = weakref(entity)' % label)
            try:
                physics.owner = weakref.ref(target)
                record['owner'] = _type_name(target)
            except Exception as error:
                record['owner'] = '<%s>' % str(error)[:80]
        self._step('%s install callbacks' % label)
        record['callbacks'] = self._install_callbacks(physics, label)
        record['seed'] = self._seed_pose(physics, bot, label)
        return record

    def _seed_pose(self, physics, bot, label):
        """Try the plausible pose setters; report which one moved the body."""
        position = bot.get('position')
        yaw = float(bot.get('yaw') or 0.0)
        math_module = self._math()
        attempts = []
        if position is None or math_module is None:
            return {'attempts': attempts, 'result': '<no pose or Math>'}
        try:
            matrix = math_module.Matrix()
            matrix.setRotateYPR((yaw, 0.0, 0.0))
            matrix.translation = math_module.Vector3(*position)
            vector = math_module.Vector3(*position)
        except Exception as error:
            return {'attempts': attempts,
                    'result': '<matrix build failed: %s>' % str(error)[:80]}
        candidates = (
            ('lastTickMatrix = Matrix', lambda: setattr(
                physics, 'lastTickMatrix', matrix)),
            ('rollback(Matrix)', lambda: physics.rollback(matrix)),
            ('configure(Matrix)', lambda: physics.configure(matrix)),
            ('rollback(Vector3, yaw)', lambda: physics.rollback(vector, yaw)),
            ('configure(Vector3, yaw)', lambda: physics.configure(
                vector, yaw)),
        )
        for name, call in candidates:
            self._step('%s seed %s' % (label, name))
            try:
                call()
            except Exception as error:
                attempts.append({'call': name, 'result': '%s: %s' % (
                    _type_name(error), str(error)[:160])})
                continue
            self._step('%s seed readback' % label)
            readback = None
            for attribute in PHYSICS_MATRIX_ATTRIBUTES:
                try:
                    readback = _matrix_translation(
                        getattr(physics, attribute), math_module)
                except Exception:
                    readback = None
                if readback is not None:
                    break
            distance = _distance(readback, position)
            attempts.append({'call': name, 'result': 'ok',
                             'readback': readback, 'distance_m': distance})
            if distance is not None and distance < 5.0:
                return {'attempts': attempts, 'result': name,
                        'readback': readback}
        return {'attempts': attempts, 'result': '<no setter moved the body>'}

    def _acquire_bodies(self, count):
        """Retail bodies when the native filter has them, else standalone."""
        needed = int(count)
        if len(self._bodies) >= needed:
            return self._bodies[:needed]
        bots = self._bots()
        if self._bodies_source is None:
            retail = []
            for bot in bots:
                self._step('bot:%s filter.getVehiclePhysics' % bot['bot_id'])
                unused_filter, physics = self._retail_physics(bot)
                if physics is not None:
                    retail.append({'label': 'bot:%s' % bot['bot_id'],
                                   'source': 'retail', 'bot': bot,
                                   'physics': physics})
            if retail:
                self._bodies_source = 'retail'
                self._bodies = retail
                return self._bodies[:needed]
            if not self._config.get('allow_standalone_bodies', True):
                self._bodies_source = 'none'
                return []
            self._bodies_source = 'standalone'
        if self._bodies_source == 'standalone':
            used = set(id(record['bot']) for record in self._bodies)
            for bot in bots:
                if len(self._bodies) >= needed:
                    break
                if id(bot) in used:
                    continue
                label = 'standalone:%s' % bot['bot_id']
                record = self._construct_body(bot, label)
                self._report.setdefault('standalone_bodies', []).append({
                    'label': label, 'init': record.get('init'),
                    'initialised': record.get('initialised'),
                    'owner': record.get('owner'), 'seed': record.get('seed'),
                    'setArenaBounds': record.get('setArenaBounds')})
                if record.get('initialised'):
                    self._bodies.append(record)
        return self._bodies[:needed]

    # ---------------------------------------------------------------- stages
    def _stage_inventory(self, frame_dt):
        data = self._current['data']
        bigworld = self._bigworld()
        data['bigworld_names'] = sorted(
            name for name in dir(bigworld)
            if 'WG' in name or 'Physic' in name or 'Dynamic' in name or
            'Filter' in name)
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
            elif callable(value) and not isinstance(value, type):
                functions[name] = _function_signature(
                    getattr(value, '__func__', value))
            elif isinstance(value, dict) and len(value) <= 64:
                constants[name] = sorted(str(key) for key in value)[:64]
        data['physics_shared'] = {
            'functions': functions, 'constants': constants}
        return True

    def _stage_inspect_existing(self, frame_dt):
        data = self._current['data']
        entries = []
        for bot in self._bots(self._config.get('inspect_entities', 3)):
            native = bot.get('native')
            carrier = bot.get('carrier')
            entry = {
                'bot_id': bot['bot_id'],
                'position': bot.get('position'), 'yaw': bot.get('yaw'),
                'carrier_type': _type_name(carrier),
                'carrier_filter_type': _type_name(
                    getattr(carrier, 'filter', None)),
                'native_type': _type_name(native),
                'native_filter_type': _type_name(
                    getattr(native, 'filter', None)),
                'descriptor_type': _type_name(bot.get('descriptor')),
            }
            if native is not None:
                vehicle_filter = getattr(native, 'filter', None)
                entry['native_filter_attributes'] = self._read_attributes(
                    vehicle_filter, FILTER_READ_ATTRIBUTES,
                    'bot:%s native.filter' % bot['bot_id'])
                self._step('bot:%s filter.getVehiclePhysics' % bot['bot_id'])
                unused_filter, physics = self._retail_physics(bot)
                entry['retail_physics_type'] = _type_name(physics)
                if physics is not None:
                    entry['physics_dir'] = sorted(
                        name for name in dir(physics)
                        if not name.startswith('__'))
                    entry['physics_attributes'] = self._read_attributes(
                        physics, PHYSICS_READ_ATTRIBUTES,
                        'bot:%s retail physics' % bot['bot_id'])
            entries.append(entry)
        data['entities'] = entries
        data['bot_count'] = len(self._bots())
        return True

    def _stage_construct_standalone(self, frame_dt):
        """Build and initialise one throwaway body; read it only afterwards."""
        data = self._current['data']
        bigworld = self._bigworld()
        bots = self._bots(1)
        if not bots:
            data['result'] = '<no ready bot>'
            return True
        record = self._construct_body(bots[0], 'construct')
        self._standalone['physics'] = record['physics']
        data['init'] = record['init']
        data['initialised'] = record.get('initialised')
        data['owner'] = record.get('owner')
        data['setArenaBounds'] = record.get('setArenaBounds')
        data['seed'] = record.get('seed')
        physics = record['physics']
        data['physics_dir'] = sorted(
            name for name in dir(physics) if not name.startswith('__'))
        if record.get('initialised'):
            data['physics_attributes_initialised'] = self._read_attributes(
                physics, PHYSICS_READ_ATTRIBUTES, 'initialised physics')
        elif self._config.get('fresh_attribute_reads'):
            data['physics_attributes_fresh'] = self._read_attributes(
                physics, PHYSICS_READ_ATTRIBUTES, 'fresh physics')
        simulator = self._simulator_for_stage()
        data['simulator_dir'] = sorted(
            name for name in dir(simulator) if not name.startswith('__'))
        data['simulator_attributes'] = self._read_attributes(
            simulator, SIMULATOR_ATTRIBUTES, 'simulator')
        self._step('BigWorld.WGPhysicalBody()')
        body = bigworld.WGPhysicalBody()
        self._standalone['body'] = body
        data['body_dir'] = sorted(
            name for name in dir(body) if not name.startswith('__'))
        data['body_attributes'] = self._read_attributes(
            body, BODY_ATTRIBUTES, 'body')
        return True

    def _stage_signatures(self, frame_dt):
        data = self._current['data']
        physics = self._standalone.get('physics')
        if physics is None:
            data['result'] = '<no initialised standalone body>'
            return True
        data['physics'] = {}
        for name in PHYSICS_SIGNATURE_METHODS:
            self._step('physics.%s()' % name)
            data['physics'][name] = _call_no_args(physics, name)
        simulator = self._simulator_for_stage()
        data['simulator'] = {}
        for name in SIMULATOR_SIGNATURE_METHODS:
            self._step('simulator.%s()' % name)
            data['simulator'][name] = _call_no_args(simulator, name)
        body = self._standalone.get('body')
        if body is not None:
            data['body'] = {}
            for name in BODY_SIGNATURE_METHODS:
                self._step('body.%s()' % name)
                data['body'][name] = _call_no_args(body, name)
        return True

    def _stage_passive_drive(self, frame_dt):
        """Signals only; nobody calls the simulator.  Does anything move?"""
        data = self._current['data']
        state = self._stage_state
        if 'body' not in state:
            bodies = self._acquire_bodies(1)
            if not bodies:
                data['result'] = '<no body available>'
                return True
            body = bodies[0]
            state['body'] = body
            data['body'] = body['label']
            data['source'] = body['source']
            data['before'] = self._pose_snapshot(body)
            self._step('%s movementSignals' % body['label'])
            data['input'] = self._set_signals(body, MOVE_FORWARD_SIGNAL, 1, 0)
            state['start'] = self._clock()
            self._driven.append(body)
            return False
        if self._clock() - state['start'] < float(
                self._config['passive_seconds']):
            return False
        body = state['body']
        data['after'] = self._pose_snapshot(body)
        data['moved_m'] = _distance(
            data['before'].get('lastTickMatrix'),
            data['after'].get('lastTickMatrix'))
        data['stop'] = self._set_signals(body, 0, 0, 0)
        return True

    def _stage_solve_one(self, frame_dt):
        """Drive one body forward through an explicit simulator batch."""
        data = self._current['data']
        state = self._stage_state
        if 'body' not in state:
            bodies = self._acquire_bodies(1)
            if not bodies:
                data['result'] = '<no body available>'
                return True
            body = bodies[0]
            state['body'] = body
            data['body'] = body['label']
            data['source'] = body['source']
            self._simulator_for_stage()
            data['before'] = self._pose_snapshot(body)
            self._step('%s movementSignals' % body['label'])
            data['input'] = self._set_signals(body, MOVE_FORWARD_SIGNAL, 1, 0)
            state['start'] = self._clock()
            state['updates'] = []
            state['samples'] = []
            self._driven.append(body)
            return False
        body = state['body']
        simulator = self._simulator_for_stage()
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if not state['updates']:
            self._step('simulator.update(dt, [%s], [])' % body['label'])
        state['updates'].append(
            self._timed_update(simulator, dt, [body['physics']]))
        if len(state['samples']) < 12:
            state['samples'].append(self._pose_snapshot(body))
        if self._clock() - state['start'] < float(
                self._config['drive_seconds']):
            return False
        data['after'] = self._pose_snapshot(body)
        updates = state['updates']
        data['update_ms'] = {
            'calls': len(updates), 'min': min(updates),
            'avg': sum(updates) / len(updates), 'max': max(updates)}
        data['samples'] = state['samples']
        data['moved_m'] = _distance(
            data['before'].get('lastTickMatrix'),
            data['after'].get('lastTickMatrix'))
        data['stop'] = self._set_signals(body, 0, 0, 0)
        physics = body['physics']
        for name in ('getTouchedGround', 'getAggressiveImpacts'):
            self._step('%s %s()' % (body['label'], name))
            data[name] = _call_no_args(physics, name)
        return True

    def _stage_solve_scale(self, frame_dt):
        """Time one batch update over as many bodies as we can acquire."""
        data = self._current['data']
        state = self._stage_state
        if 'bodies' not in state:
            bodies = self._acquire_bodies(
                int(self._config.get('scale_bodies', 29)))
            state['bodies'] = bodies
            state['updates'] = []
            data['body_count'] = len(bodies)
            data['source'] = self._bodies_source
            data['labels'] = [body['label'] for body in bodies]
            if not bodies:
                data['result'] = '<no bodies>'
                return True
            if self._config.get('drive_all'):
                for body in bodies:
                    self._set_signals(body, MOVE_FORWARD_SIGNAL, 1, 0)
                    self._driven.append(body)
            self._simulator_for_stage()
            return False
        simulator = self._simulator_for_stage()
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if not state['updates']:
            self._step('simulator.update(dt, %d bodies, [])' %
                       len(state['bodies']))
        state['updates'].append(self._timed_update(
            simulator, dt, [body['physics'] for body in state['bodies']]))
        if len(state['updates']) < int(self._config['scale_frames']):
            return False
        updates = sorted(state['updates'])
        count = len(updates)
        data['update_ms'] = {
            'calls': count, 'min': updates[0], 'p50': updates[count // 2],
            'p95': updates[int(count * 0.95)], 'max': updates[-1],
            'avg': sum(updates) / count}
        data['per_body_avg_ms'] = (
            data['update_ms']['avg'] / max(1, data['body_count']))
        data['final_poses'] = [
            self._pose_snapshot(body) for body in state['bodies'][:4]]
        return True

    def _stage_solve_pair(self, frame_dt):
        """Drive two bodies toward each other for contact callbacks."""
        data = self._current['data']
        state = self._stage_state
        if 'bodies' not in state:
            bodies = self._acquire_bodies(2)
            if len(bodies) < 2:
                data['result'] = '<fewer than two bodies>'
                return True
            first, second = bodies[0], bodies[1]
            state['bodies'] = (first, second)
            data['labels'] = [first['label'], second['label']]
            data['before'] = [self._pose_snapshot(first),
                              self._pose_snapshot(second)]
            data['events_before'] = len(self._callback_events)
            self._simulator_for_stage()
            self._step('pair movementSignals')
            data['input'] = [
                self._set_signals(first, MOVE_FORWARD_SIGNAL, 1, 0),
                self._set_signals(second, MOVE_FORWARD_SIGNAL, 1, 0)]
            state['start'] = self._clock()
            state['stepped'] = False
            self._driven.extend([first, second])
            return False
        first, second = state['bodies']
        simulator = self._simulator_for_stage()
        dt = max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))
        if not state['stepped']:
            state['stepped'] = True
            self._step('simulator.update(dt, [%s, %s], [])' % (
                first['label'], second['label']))
        self._timed_update(simulator, dt, [first['physics'],
                                            second['physics']])
        if self._clock() - state['start'] < float(
                self._config['pair_seconds']):
            return False
        data['after'] = [self._pose_snapshot(first),
                         self._pose_snapshot(second)]
        data['events_after'] = len(self._callback_events)
        data['stop'] = [self._set_signals(first, 0, 0, 0),
                        self._set_signals(second, 0, 0, 0)]
        return True

    def _stage_restore(self, frame_dt):
        """Zero every signal this probe set.  Never raises."""
        data = self._current['data'] if self._current else {}
        restored = []
        seen = set()
        for body in self._driven:
            if id(body) in seen:
                continue
            seen.add(id(body))
            try:
                self._set_signals(body, 0, 0, 0)
                restored.append(body['label'])
            except Exception as error:
                restored.append('%s:<%s>' % (body['label'], str(error)[:60]))
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

    def _native_entity(self, engine_id):
        """Resolve the native Vehicle past the stock AOI facade if possible."""
        binding = getattr(self._runtime, '_binding', None)
        resolver = getattr(binding, '_authority_entity_or_fail', None)
        if callable(resolver):
            try:
                return resolver(engine_id)
            except Exception:
                pass
        try:
            return self.bigworld().entity(engine_id)
        except Exception:
            return None

    def bot_entities(self):
        runtime = self._runtime
        factory = getattr(runtime, '_remote_factory', None)
        result = []
        for key, record in sorted(runtime._records.items()):
            if (not isinstance(key, str) or not key.startswith('bot:') or
                    not isinstance(record, dict) or not record.get('ready') or
                    record.get('tombstone')):
                continue
            try:
                bot_id = int(key.split(':', 1)[1])
                engine_id = int(record.get('engine_id'))
            except (TypeError, ValueError):
                continue
            carrier = None
            try:
                carrier = factory.get(engine_id) if factory is not None else None
            except Exception:
                carrier = None
            native = self._native_entity(engine_id)
            if native is carrier:
                native = None
            if native is None and carrier is None:
                continue
            descriptor = getattr(native, 'typeDescriptor', None)
            if descriptor is None:
                descriptor = getattr(carrier, 'typeDescriptor', None)
            position = None
            yaw = None
            for source in (carrier, native):
                if source is None:
                    continue
                try:
                    position = _xyz(source.position)
                except Exception:
                    position = None
                if position is not None:
                    try:
                        yaw = float(getattr(source, 'yaw'))
                    except Exception:
                        yaw = 0.0
                    break
            result.append({
                'bot_id': bot_id, 'engine_id': engine_id,
                'native': native, 'carrier': carrier,
                'descriptor': descriptor, 'position': position, 'yaw': yaw})
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
