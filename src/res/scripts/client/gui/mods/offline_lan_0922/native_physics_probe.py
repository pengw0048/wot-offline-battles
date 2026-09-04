"""Staged, fail-closed probe of #1513's native vehicle physics surface.

Diagnostic evidence only.  Ground truth this version is built on (two Windows
runs plus the exact client ``scripts.pkg`` bytecode and data):

* The Python carrier the runtime hands out for a Bot (``RemoteVehicle``) owns
  a Python ``_RemoteFilter``; the native ``Vehicle`` entity behind it owns the
  stock ``WGVehicleFilter``.  Whether that native filter carries a retail
  ``WGVehiclePhysics`` is exactly what ``inspect_existing`` records.
* Reading an attribute of a ``WGVehiclePhysics`` that has not been configured
  dereferences a NULL body (``mass`` getter, ``WorldOfTanks.exe+0xb50ad0``).
  The native ``configure(cfg)`` return value is therefore checked before the
  first attribute read, and the pre-initialisation read is opt-in.
* Retail ``Vehicle.__startWGPhysics`` initialises every vehicle's filter body
  with ``physics_shared.initVehiclePhysicsClient`` (damper springs, no drive
  parameters).  The drive-capable recipe is the server one:
  ``physics_shared.configurePhysics`` builds the cfg from
  ``g_defaultTankXPhysicsCfg`` plus ``typeDesc.type.xphysics['detailed']`` and
  hands it to ``WGVehiclePhysics.configure``.  The client package ships only
  two fields of that block per vehicle, so ``derived_xphysics`` builds it from
  the client descriptor and a read-only proxy supplies it to ``configurePhysics``
  without mutating the shared ``VehicleType``.  Run 3 (2026-09-04) accepted
  that cfg for all 29 Bots (``configure`` returned True, every getter readable
  afterwards, nothing moves without a simulator).
* The exe's method registrations (read from the binary, not guessed):
  ``WGDynamicsSimulator.update(float, seq<WGVehiclePhysics>,
  seq<WGPhysicalBody>, seq<WGBspCollisionModel>)`` (four arguments; run 3
  failed with three), ``WGVehiclePhysics.rollback(uint, uint)`` (not a pose
  setter), ``setSignal(int)``, ``applyImpulseToCoM(Vector3)``,
  ``setArenaBounds(Vector2, Vector2)``, ``getTouchedGround(uint)``,
  ``subscribeBefore/AfterSimulation(callable)``.  The ``owner`` setter accepts
  only a weakref whose referent subclasses the native ``PyEntity``; the
  worker's Bot carrier is a plain Python object, so ``owner`` stays unset
  there.  ``vehicleID`` has a setter.
* Run 4 (2026-09-04): the first ``WGDynamicsSimulator.update`` with a
  ``WGVehiclePhysics`` terminated the worker (exit code 3) with the BigWorld
  critical ``SceneObstaclesCollider::collidePolyhedra: UNIMPLEMENTED``.  The
  #1513 client's ``BW::WGPhysics::SceneObstaclesCollider`` vtable has nine
  slots; ``collidePolyhedra``, ``collideCompositeShape``,
  ``getTerrainMatKind``, ``getGroundType`` and ``getTerrainHeight`` are
  message-and-abort stubs.  The vehicle solver therefore cannot see terrain or
  obstacles in this binary and every vehicle solve stage is off by default
  (``vehicle_solver``).
* Run 5 (2026-09-04): the first ``update`` with a ``WGPhysicalBody`` box
  aborted the same way with ``SceneObstaclesCollider::collideCompositeShape:
  UNIMPLEMENTED``.  No dynamics body of either kind can be stepped against
  the world in this binary; ``physical_body`` is off by default as well.
  What remains usable without native patching is construction, configuration
  and the attribute/method surface, which the first three stages record.

Stages run one per render frame from ``start_delay_seconds`` after the battle
goes live.  Every native call group is announced with
``NPHYS stage=<name> step=<call>`` before it runs, and the JSON report is
rewritten after each stage, so a native crash leaves the last step in
``python.log`` and every earlier stage's data on disk.  A Python exception is
recorded and the probe continues; nothing here may raise into the frame.

Bodies used by the drive/solve stages are standalone bodies the probe
constructs from the Bot's own descriptor (retail filter bodies are the
fallback; they cannot self-propel).  Standalone bodies have no presentation;
their read-back matrices are the evidence.  The probe never calls
``WGVehicleFilter.setVehiclePhysics`` or ``syncGunAngles`` and never touches
human vehicles.
"""

from __future__ import print_function

import collections
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
    'passive_seconds': 0.5,
    # solve_one drive segments on body A: forward, rotate left, backward,
    # stop (freeze check).
    'drive_seconds': 3.0,
    'rotate_seconds': 1.5,
    'reverse_seconds': 1.5,
    'settle_seconds': 2.0,
    # solve_scale: three phases (idle / staticMode / all driving) of this
    # many batch updates each, over up to scale_bodies bodies.
    'scale_frames': 60,
    'scale_bodies': 29,
    # solve_pair: body B is re-seeded pair_gap_m ahead of body A, facing it.
    'pair_seconds': 3.0,
    'pair_gap_m': 20.0,
    # extras: applyImpulseToCoM(Vector3(0, 0, mass * impulse_scale)) on A.
    'impulse_scale': 5.0,
    'impulse_frames': 10,
    # Throwaway-body acceptance tests (setters/methods on a body that is
    # never simulated) and the owner self-test with BigWorld.player().
    'throwaway_tests': True,
    'owner_self_test': True,
    # Stepping a WGVehiclePhysics is fatal in #1513 (run 4); opt in only to
    # reproduce that crash deliberately.
    'vehicle_solver': False,
    # physical_body: drop a box body onto the terrain, push it along the
    # Bot's heading, release it.  Fatal in #1513 (run 5); opt in only to
    # reproduce that crash deliberately.
    'physical_body': False,
    'physical_body_mass': 20.0,
    'physical_body_half_extents': [1.5, 1.0, 3.0],
    'physical_body_drop_m': 3.0,
    'physical_body_seconds': 3.0,
    'physical_body_push_seconds': 3.0,
    'physical_body_push_g': 0.5,
    'physical_body_release_seconds': 1.0,
    'disable_lsprof': False,
    'opt_in_stages': [],
    'fresh_attribute_reads': False,
    'allow_standalone_bodies': True,
    # Standalone bodies are the design target; retail filter bodies are the
    # client-side damper-spring model and cannot self-propel.
    'prefer_standalone': True,
    # 'detailed' = the retail server recipe (physics_shared.configurePhysics
    # over g_defaultTankXPhysicsCfg + descriptor-derived fields, then the
    # native configure(cfg)); 'client' = retail initVehiclePhysicsClient.
    'init_order': ['detailed', 'client'],
    # 'omit' leaves chassis grounds to the native defaults.  'shipped' feeds
    # the per-material rollingFriction the client package carries plus a
    # PLACEHOLDER 'soft' block copied from 'medium' (the retail 'soft' block
    # is not shipped); use it only to test whether configure() accepts it.
    'grounds_mode': 'omit',
    # Mirror physics_shared.updateCommonConf() once (22 process-global
    # wg_setupPhysicsParam calls, each guarded) before the first configure.
    'apply_common_conf': True,
    # Retail never reads attributes off a client-initialised body; keep
    # those getters off unless explicitly requested.
    'read_retail_physics_attributes': False,
    # 'entity' installs weakref(native Vehicle) like retail; 'none' leaves it.
    'owner_mode': 'entity',
}

# physics_shared.updateCommonConf() in retail order; every name is a module
# constant of physics_shared and a BigWorld.wg_setupPhysicsParam key.
COMMON_CONF_PARAMS = (
    'CONTACT_ENERGY_POW', 'CONTACT_ENERGY_POW2', 'SLOPE_FRICTION_FUNC_DEF',
    'SLOPE_FRICTION_FUNC_VAL', 'SLOPE_FRICTION_MODELS_FUNC_VAL',
    'CONTACT_FRICTION_TERRAIN', 'CONTACT_FRICTION_STATICS',
    'CONTACT_FRICTION_STATICS_VERT', 'CONTACT_FRICTION_DESTRUCTIBLES',
    'CONTACT_FRICTION_VEHICLES', 'VEHICLE_ON_BODY_DEFAULT_FRICTION',
    'ROLLER_FRICTION_GAIN_MIN', 'ROLLER_FRICTION_GAIN_MAX',
    'ROLLER_FRICTION_ANGLE_MIN', 'ROLLER_FRICTION_ANGLE_MAX',
    'ARENA_BOUNDS_FRICTION_HOR', 'ARENA_BOUNDS_FRICTION_VERT',
    'USE_PSEUDO_CONTACTS', 'CONTACT_PENETRATION',
    'WARMSTARTING_VEHICLE_VEHICLE', 'WARMSTARTING_VEHICLE_STATICS',
    'WARMSTARTING_THRESHOLD')
# Vehicle.__startWGPhysics passes these to setArenaBounds.
RETAIL_ARENA_BOUNDS = ((-10000, -10000), (10000, 10000))
# WGDynamicsSimulator.update as registered in the #1513 exe.
UPDATE_SIGNATURE = ('update(float dt, seq<WGVehiclePhysics>, '
                    'seq<WGPhysicalBody>, seq<WGBspCollisionModel>)')

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
    'passive_drive', 'solve_one', 'solve_pair', 'solve_scale', 'extras',
    'physical_body', 'restore', 'signatures')
VEHICLE_SOLVER_STAGES = (
    'passive_drive', 'solve_one', 'solve_pair', 'solve_scale', 'extras')
VEHICLE_SOLVER_FATAL = (
    'WGDynamicsSimulator.update with a WGVehiclePhysics aborts the #1513 '
    'client: SceneObstaclesCollider::collidePolyhedra UNIMPLEMENTED (run 4)')
PHYSICAL_BODY_FATAL = (
    'WGDynamicsSimulator.update with a WGPhysicalBody aborts the #1513 '
    'client: SceneObstaclesCollider::collideCompositeShape UNIMPLEMENTED '
    '(run 5)')
PHYSICAL_BODY_READ_ATTRIBUTES = (
    'mass', 'gravity', 'staticMode', 'isFrozen', 'velocity', 'angVelocity',
    'forceApplied', 'torqueApplied', 'externalForce', 'visibilityMask',
    'isCollidingWithWorld', 'isCwwThresholdFactor', 'staticSceneFriction',
    'staticCollisionEnergy', 'staticCollisionReaction',
    'staticCollisionNormal', 'staticCollisionPoint',
    'staticCollisionSelfPoint', 'isUnderWater', 'freezePosErrorEpsilon')
# Pose setters tried, in order, when the solver ignores the seeded
# lastTickMatrix (decided by where body A is after its first update).
POSE_METHODS = (
    'lastTickMatrix = Matrix',
    'actualChassisTransform = Matrix',
    'stabilisedMatrixWithLatency = Matrix',
    'staticMode toggle + lastTickMatrix',
    'configure(cfg) again',
)
CALLBACK_EVENT_CAP = 400
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


def _matrix_yaw(matrix):
    try:
        return float(matrix.yaw)
    except Exception:
        return None


def _finite_xyz(value):
    if not value or len(value) != 3:
        return False
    for item in value:
        try:
            item = float(item)
        except (TypeError, ValueError):
            return False
        if math.isnan(item) or math.isinf(item):
            return False
    return True


def _call_with(target, name, args):
    method = getattr(target, name, None)
    if method is None:
        return '<missing>'
    if not callable(method):
        return '<not callable: %s>' % _type_name(method)
    try:
        value = method(*args)
    except Exception as error:
        return '%s: %s' % (_type_name(error), str(error)[:200])
    return 'returned %s' % json.dumps(_plain(value))[:200]


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


class _AttributeProxy(object):
    """Forward attribute reads to a target, overriding a few names.

    physics_shared only reads from typeDesc (it mutates the xphysics dict,
    never the descriptor), so a read-only proxy keeps the shared VehicleType
    untouched.
    """

    def __init__(self, target, **overrides):
        self.__dict__['_target'] = target
        self.__dict__['_overrides'] = overrides

    def __getattr__(self, name):
        overrides = self.__dict__['_overrides']
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__['_target'], name)


class _ConfigureRecorder(object):
    """Stand in for WGVehiclePhysics while configurePhysics runs in Python.

    Captures the cfg dict it would hand to the native configure() and the
    attribute writes it performs afterwards, so the native call can be made
    separately and its return value checked before any getter is touched.
    """

    def __init__(self):
        self.__dict__['writes'] = []
        self.__dict__['cfg'] = None
        self.__dict__['hullCOMZ'] = 0.0

    def configure(self, cfg):
        self.__dict__['cfg'] = cfg
        return True

    def __setattr__(self, name, value):
        self.__dict__['writes'].append((name, value))


def derived_xphysics(descriptor, grounds_mode='omit'):
    """Build the xphysics['detailed'] block for one client descriptor.

    The #1513 client package ships only two fields of the retail block per
    vehicle (engine smplEnginePower and per-material medium rollingFriction),
    so everything else comes from physics_shared.g_defaultTankXPhysicsCfg.
    The single substitution is smplFwMaxSpeed/smplBkMaxSpeed, taken from the
    vehicle-level type.speedLimits the client reads; retail reads them per
    engine from the unstripped XML.
    """
    vehicle_type = descriptor.type
    engine_name = descriptor.engine.name
    chassis_name = descriptor.chassis.name
    shipped = getattr(vehicle_type, 'xphysics', None) or {}
    engine = {}
    shipped_engine = (shipped.get('engines') or {}).get(engine_name) or {}
    if 'smplEnginePower' in shipped_engine:
        engine['smplEnginePower'] = float(shipped_engine['smplEnginePower'])
    limits = getattr(vehicle_type, 'speedLimits', None)
    if limits:
        engine['smplFwMaxSpeed'] = float(limits[0])
        engine['smplBkMaxSpeed'] = float(limits[1])
    chassis = {}
    if grounds_mode == 'shipped':
        shipped_chassis = (shipped.get('chassis') or {}).get(chassis_name)
        grounds = {}
        for ground, entry in sorted(
                ((shipped_chassis or {}).get('grounds') or {}).items()):
            grounds[str(ground)] = {'medium': dict(entry)}
        if grounds:
            # PLACEHOLDER: retail's 'soft' block is not in the client package.
            grounds['soft'] = dict(grounds[sorted(grounds)[0]]['medium'])
            chassis['grounds'] = grounds
    # A non-empty chassis entry makes updatePhysicsCfg require 'grounds'
    # with a 'soft' block, so 'omit' leaves the entry empty on purpose.
    return {'gravityFactor': 1.0,
            'engines': {engine_name: engine},
            'chassis': {chassis_name: chassis}}


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
            'schema': 4, 'diagnostic': 'native_physics_probe',
            'config': dict(self._config), 'stages': [], 'last_begun': None,
            'update_signature': UPDATE_SIGNATURE}
        self._current = None
        self._simulator = None
        self._standalone = {}
        self._bodies = []          # acquired body records for drive/solve
        self._bodies_source = None
        self._standalone_attempted = set()   # bot ids tried once, ever
        self._driven = []
        self._callback_events = []
        self._common_conf = None
        self._space_id = None
        self._pose_method = None      # adopted by solve_one, reused later
        self._subscriptions = {}      # label -> {'before': n, 'after': n}
        stages = self._config.get('stages')
        opted = set(self._config.get('opt_in_stages') or ())
        if stages == 'all' or not stages:
            self._stages = [name for name in STAGE_ORDER
                            if name not in OPT_IN_STAGES or name in opted]
        else:
            self._stages = [name for name in STAGE_ORDER if name in stages]
        skipped = {}
        if not self._config.get('vehicle_solver'):
            for name in VEHICLE_SOLVER_STAGES:
                if name in self._stages:
                    self._stages.remove(name)
                    skipped[name] = VEHICLE_SOLVER_FATAL
        if not self._config.get('physical_body'):
            if 'physical_body' in self._stages:
                self._stages.remove('physical_body')
                skipped['physical_body'] = PHYSICAL_BODY_FATAL
        self._report['skipped_stages'] = skipped
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
                'begun_at': now, 'data': {}, 'error': None,
                'events_at_begin': len(self._callback_events)}
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
            self._current['callbacks'] = self._callback_summary(
                self._current.get('events_at_begin', 0))
            self._log('stage=%s end status=%s frames=%d' % (
                name, self._current['status'], self._current['frames']))
            self._current = None
            self._stage_index += 1
            self._write_report()
        return True

    def _finish(self):
        self._done = True
        self._report['completed'] = True
        self._report['callback_events'] = self._callback_events[:CALLBACK_EVENT_CAP]
        self._report['callbacks_total'] = self._callback_summary(0)
        self._report['subscriptions'] = self._subscriptions
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
                matrix = getattr(physics, name)
                result[name] = _matrix_translation(matrix, math_module)
                if name == 'lastTickMatrix':
                    result['yaw'] = _matrix_yaw(matrix)
            except Exception as error:
                result[name] = '<%s>' % str(error)[:80]
        position = result.get('lastTickMatrix')
        if _finite_xyz(position):
            ground = self._ground_y(position[0], position[1], position[2])
            result['ground_y'] = ground
            result['height'] = (
                position[1] - ground if ground is not None else None)
        for name in ('speed', 'isFrozen', 'movementSignals',
                     'gotTracksContact', 'groundType', 'distanceTraveled'):
            result[name] = _read_attribute(physics, name)
        return result

    def _ground_y(self, x, y, z):
        """Terrain/static height under (x, z), the runtime's own probe shape."""
        bigworld = self._bigworld()
        math_module = self._math()
        collide = getattr(bigworld, 'wg_collideSegment', None)
        if not callable(collide) or math_module is None:
            return None
        if self._space_id is None:
            try:
                self._space_id = int(bigworld.player().spaceID)
            except Exception:
                self._space_id = -1
        if self._space_id < 0:
            return None
        try:
            hit = collide(self._space_id,
                          math_module.Vector3(x, y + 20.0, z),
                          math_module.Vector3(x, y - 60.0, z), 128)
        except Exception:
            return None
        if hit is None:
            return None
        try:
            return float(hit[0].y)
        except Exception:
            return None

    def _callback_summary(self, since):
        summary = {}
        for event in self._callback_events[since:]:
            entry = summary.setdefault(event['callback'], {
                'count': 0, 'first': []})
            entry['count'] += 1
            if len(entry['first']) < 3:
                entry['first'].append(
                    {'body': event['body'], 'args': event['args']})
        return summary

    def _seed_matrix(self, position, yaw):
        math_module = self._math()
        matrix = math_module.Matrix()
        matrix.setRotateYPR((float(yaw), 0.0, 0.0))
        matrix.translation = math_module.Vector3(*position)
        return matrix

    def _apply_pose(self, body, position, yaw, method):
        """Apply one POSE_METHODS entry; return 'ok' or an error string."""
        physics = body['physics']
        try:
            matrix = self._seed_matrix(position, yaw)
        except Exception as error:
            return '<matrix build failed: %s>' % str(error)[:80]
        self._step('%s pose %s' % (body['label'], method))
        try:
            if method == 'lastTickMatrix = Matrix':
                physics.lastTickMatrix = matrix
            elif method == 'actualChassisTransform = Matrix':
                physics.actualChassisTransform = matrix
            elif method == 'stabilisedMatrixWithLatency = Matrix':
                physics.stabilisedMatrixWithLatency = matrix
            elif method == 'staticMode toggle + lastTickMatrix':
                physics.staticMode = True
                physics.lastTickMatrix = matrix
                physics.staticMode = False
            elif method == 'configure(cfg) again':
                cfg = body.get('cfg')
                if cfg is None:
                    return '<no cfg kept for this body>'
                physics.lastTickMatrix = matrix
                if not physics.configure(cfg):
                    return '<configure returned False>'
            else:
                return '<unknown method>'
        except Exception as error:
            return '%s: %s' % (_type_name(error), str(error)[:120])
        return 'ok'

    def _pose_ok(self, snapshot, reference):
        position = snapshot.get('lastTickMatrix')
        if not _finite_xyz(position):
            return False
        if position == [0.0, 0.0, 0.0]:
            return False
        distance = _distance(position, reference)
        return distance is not None and distance < 30.0

    @staticmethod
    def _ahead(position, yaw, metres):
        return [position[0] + math.sin(yaw) * metres, position[1],
                position[2] + math.cos(yaw) * metres]

    def _install_callbacks(self, physics, label):
        installed = []
        for name in PHYSICS_CALLBACK_ATTRIBUTES:
            def _make(callback_name):
                def _callback(*args):
                    if len(self._callback_events) < CALLBACK_EVENT_CAP:
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

    def _timed_update(self, simulator, dt, physics_list, bodies=()):
        started = self._perf()
        # (dt, vehicle physics, physical bodies, BSP collision models)
        simulator.update(float(dt), list(physics_list), list(bodies), [])
        return (self._perf() - started) * 1000.0

    # ------------------------------------------------------- body acquisition
    def _apply_common_conf(self, physics_shared):
        """Mirror physics_shared.updateCommonConf() once, one guarded call each."""
        if self._common_conf is not None or not self._config.get(
                'apply_common_conf', True):
            return
        setter = getattr(self._bigworld(), 'wg_setupPhysicsParam', None)
        results = collections.OrderedDict()
        if not callable(setter):
            results['wg_setupPhysicsParam'] = '<missing>'
        else:
            for name in COMMON_CONF_PARAMS:
                value = getattr(physics_shared, name, None)
                if value is None:
                    results[name] = '<constant missing>'
                    continue
                self._step('BigWorld.wg_setupPhysicsParam(%s, %r)' % (
                    name, value))
                try:
                    setter(name, value)
                    results[name] = 'ok'
                except Exception as error:
                    results[name] = '<%s: %s>' % (
                        _type_name(error), str(error)[:80])
        self._common_conf = results
        self._report['common_conf'] = results

    def _init_detailed(self, physics, physics_shared, descriptor, label):
        """Retail server recipe with the native configure() checked first."""
        entry = {'call': 'detailed'}
        configure_physics = getattr(physics_shared, 'configurePhysics', None)
        if not callable(configure_physics):
            entry['result'] = '<physics_shared.configurePhysics missing>'
            return entry
        try:
            base = derived_xphysics(
                descriptor, self._config.get('grounds_mode', 'omit'))
        except Exception as error:
            entry['result'] = 'derive: %s: %s' % (
                _type_name(error), str(error)[:160])
            return entry
        entry['base_cfg'] = _plain(base)
        recorder = _ConfigureRecorder()
        proxy = _AttributeProxy(
            descriptor,
            type=_AttributeProxy(descriptor.type, xphysics={'detailed': base}))
        self._step('%s physics_shared.configurePhysics(recorder)' % label)
        try:
            configure_physics(recorder, base, proxy, float(base['gravityFactor']))
        except Exception as error:
            entry['result'] = 'configurePhysics: %s: %s' % (
                _type_name(error), str(error)[:160])
            return entry
        cfg = recorder.cfg
        if not isinstance(cfg, dict):
            entry['result'] = '<configurePhysics never called configure>'
            return entry
        modes = cfg.get('modes') or {}
        entry['cfg_modes'] = sorted(str(name) for name in modes)
        entry['cfg_normal'] = _plain(modes.get('normal'))
        self._step('%s physics.configure(cfg)' % label)
        try:
            accepted = physics.configure(cfg)
        except Exception as error:
            entry['result'] = 'configure: %s: %s' % (
                _type_name(error), str(error)[:160])
            return entry
        entry['configure'] = _plain(accepted)
        if not accepted:
            entry['result'] = '<configure returned %r>' % (accepted,)
            return entry
        entry['_cfg'] = cfg
        self._step('%s physics.hullCOMZ' % label)
        hull_com_z = _read_attribute(physics, 'hullCOMZ')
        entry['hullCOMZ'] = hull_com_z
        math_module = self._math()
        writes = []
        for name, value in recorder.writes:
            if (name == 'centerOfMass' and math_module is not None and
                    isinstance(hull_com_z, float)):
                try:
                    value = math_module.Vector3(
                        float(value.x), float(value.y), hull_com_z)
                except Exception:
                    pass
            self._step('%s physics.%s = %s' % (label, name, _plain(value)))
            try:
                setattr(physics, name, value)
                writes.append(name)
            except Exception as error:
                writes.append('%s:<%s>' % (name, str(error)[:60]))
        entry['writes'] = writes
        entry['result'] = 'ok'
        return entry

    def _init_client(self, physics, physics_shared, descriptor, label):
        """Retail client recipe: proves creation only, the body cannot drive."""
        entry = {'call': 'client', 'drive_capable': False}
        function = getattr(physics_shared, 'initVehiclePhysicsClient', None)
        if not callable(function):
            entry['result'] = '<physics_shared.initVehiclePhysicsClient missing>'
            return entry
        self._step('%s physics_shared.initVehiclePhysicsClient' % label)
        try:
            function(physics, descriptor)
        except Exception as error:
            entry['result'] = '%s: %s' % (_type_name(error), str(error)[:200])
            return entry
        entry['result'] = 'ok'
        return entry

    def _set_visibility_mask(self, physics, label, record):
        """Retail: ArenaType.getVisibilityMask(player.arenaTypeID >> 16)."""
        try:
            arena_type_id = int(self._bigworld().player().arenaTypeID)
        except Exception as error:
            record['visibilityMask'] = '<arenaTypeID: %s>' % str(error)[:60]
            return
        try:
            import ArenaType
            mask = ArenaType.getVisibilityMask(arena_type_id >> 16)
        except Exception:
            mask = 1 << (arena_type_id >> 16)
        self._step('%s physics.visibilityMask = %r' % (label, mask))
        try:
            physics.visibilityMask = mask
            record['visibilityMask'] = mask
        except Exception as error:
            record['visibilityMask'] = '<%s>' % str(error)[:80]

    def _construct_body(self, bot, label):
        """Construct, initialise and pose one standalone body; never raise."""
        record = {'label': label, 'source': 'standalone', 'bot': bot,
                  'physics': None, 'init': [], 'seed': None, 'owner': None,
                  'initialised': False, 'init_mode': None}
        bigworld = self._bigworld()
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
        self._apply_common_conf(physics_shared)
        if getattr(descriptor, 'hasSiegeMode', False):
            # configurePhysics wants defaultVehicleDescr and siegeVehicleDescr
            # proxied as well; the probe does not need siege vehicles.
            record['skipped'] = 'hasSiegeMode'
            record['init'].append({'call': '-', 'result': '<skipped: hasSiegeMode>'})
            return record
        # One fresh object per recipe: no retail path ever runs the client
        # init on a body the detailed parser has already touched.  Rejected
        # objects are kept referenced until the probe ends rather than
        # destroyed mid-frame.
        physics = None
        for name in self._config.get('init_order') or ():
            if physics is not None:
                record.setdefault('discarded', []).append(physics)
            self._step('%s BigWorld.WGVehiclePhysics()' % label)
            physics = bigworld.WGVehiclePhysics()
            record['physics'] = physics
            if name == 'detailed':
                entry = self._init_detailed(
                    physics, physics_shared, descriptor, label)
            elif name == 'client':
                entry = self._init_client(
                    physics, physics_shared, descriptor, label)
            else:
                entry = {'call': name, 'result': '<unknown init mode>'}
            record['cfg'] = entry.pop('_cfg', None)
            record['init'].append(entry)
            if entry.get('result') == 'ok':
                record['initialised'] = True
                record['init_mode'] = name
                break
        if not record['initialised']:
            return record
        # Vehicle.__startWGPhysics order: bounds, owner, static, signals, mask.
        self._step('%s physics.setArenaBounds' % label)
        try:
            physics.setArenaBounds(*RETAIL_ARENA_BOUNDS)
            record['setArenaBounds'] = 'ok'
        except Exception as error:
            record['setArenaBounds'] = '%s: %s' % (_type_name(error), str(error))
        owner_mode = self._config.get('owner_mode', 'entity')
        target = bot.get('native') or bot.get('carrier')
        entity_type = getattr(bigworld, 'Entity', None)
        if owner_mode == 'entity' and target is not None:
            if isinstance(entity_type, type) and not isinstance(
                    target, entity_type):
                # The setter accepts only a weakref to a native PyEntity.
                record['owner'] = '<skipped: %s is not a BigWorld.Entity>' % (
                    _type_name(target),)
            else:
                self._step('%s physics.owner = weakref(entity)' % label)
                try:
                    physics.owner = weakref.ref(target)
                    record['owner'] = _type_name(target)
                except Exception as error:
                    record['owner'] = '<%s>' % str(error)[:80]
        for name, value in (('staticMode', False), ('movementSignals', 0)):
            self._step('%s physics.%s = %r' % (label, name, value))
            try:
                setattr(physics, name, value)
            except Exception as error:
                record['%s_set' % name] = '<%s>' % str(error)[:80]
        self._set_visibility_mask(physics, label, record)
        engine_id = bot.get('engine_id')
        if engine_id is not None:
            self._step('%s physics.vehicleID = %r' % (label, engine_id))
            try:
                physics.vehicleID = int(engine_id)
                record['vehicleID'] = _read_attribute(physics, 'vehicleID')
            except Exception as error:
                record['vehicleID'] = '<%s>' % str(error)[:80]
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
        # configure() is the detailed cfg parser and rollback() is registered
        # as (uint, uint) in the exe; neither is a pose setter.  Whether the
        # solver honours lastTickMatrix is what the solve stages' pose
        # snapshots show.
        candidates = (
            ('lastTickMatrix = Matrix', lambda: setattr(
                physics, 'lastTickMatrix', matrix)),
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

    def _retail_bodies(self, bots):
        retail = []
        for bot in bots:
            self._step('bot:%s filter.getVehiclePhysics' % bot['bot_id'])
            unused_filter, physics = self._retail_physics(bot)
            if physics is not None:
                retail.append({'label': 'bot:%s' % bot['bot_id'],
                               'source': 'retail', 'bot': bot,
                               'physics': physics})
        return retail

    def _build_standalone(self, bots, needed):
        """Construct at most one body per Bot, ever; failures are not retried."""
        for bot in bots:
            if len(self._bodies) >= needed:
                break
            if bot['bot_id'] in self._standalone_attempted:
                continue
            self._standalone_attempted.add(bot['bot_id'])
            label = 'standalone:%s' % bot['bot_id']
            record = self._construct_body(bot, label)
            self._report.setdefault('standalone_bodies', []).append({
                'label': label, 'init': record.get('init'),
                'initialised': record.get('initialised'),
                'init_mode': record.get('init_mode'),
                'skipped': record.get('skipped'),
                'owner': record.get('owner'), 'seed': record.get('seed'),
                'setArenaBounds': record.get('setArenaBounds'),
                'visibilityMask': record.get('visibilityMask'),
                'vehicleID': record.get('vehicleID')})
            if record.get('initialised'):
                self._bodies.append(record)

    def _acquire_bodies(self, count):
        """Standalone bodies first (the design target), retail as fallback."""
        needed = int(count)
        if len(self._bodies) >= needed:
            return self._bodies[:needed]
        bots = self._bots()
        if self._bodies_source is None:
            allow_standalone = self._config.get('allow_standalone_bodies', True)
            order = ['retail']
            if allow_standalone:
                if self._config.get('prefer_standalone', True):
                    order = ['standalone', 'retail']
                else:
                    order = ['retail', 'standalone']
            for source in order:
                if source == 'retail':
                    retail = self._retail_bodies(bots)
                    if retail:
                        self._bodies_source = 'retail'
                        self._bodies = retail
                        break
                else:
                    self._bodies_source = 'standalone'
                    self._build_standalone(bots, needed)
                    if self._bodies:
                        break
                    self._bodies_source = None
            if self._bodies_source is None:
                self._bodies_source = 'none'
                return []
        elif self._bodies_source == 'standalone':
            self._build_standalone(bots, needed)
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
            entity_type = getattr(self._bigworld(), 'Entity', None)
            entry = {
                'bot_id': bot['bot_id'],
                'position': bot.get('position'), 'yaw': bot.get('yaw'),
                'carrier_type': _type_name(carrier),
                'carrier_mro': [klass.__name__ for klass in
                                type(carrier).__mro__] if carrier is not None
                else None,
                'carrier_is_entity': (
                    isinstance(carrier, entity_type)
                    if isinstance(entity_type, type) else '<no BigWorld.Entity>'),
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
                    if self._config.get('read_retail_physics_attributes'):
                        entry['physics_attributes'] = self._read_attributes(
                            physics, PHYSICS_READ_ATTRIBUTES,
                            'bot:%s retail physics' % bot['bot_id'])
                    else:
                        entry['physics_attributes'] = '<skipped: retail never reads them>'
            entries.append(entry)
        data['entities'] = entries
        data['bot_count'] = len(self._bots())
        try:
            data['factory'] = self._host.factory_info()
        except Exception as error:
            data['factory'] = '<%s>' % str(error)[:80]
        try:
            player = self._bigworld().player()
            data['player_type'] = _type_name(player)
            data['player_mro'] = [
                klass.__name__ for klass in type(player).__mro__]
        except Exception as error:
            data['player_type'] = '<%s>' % str(error)[:80]
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
        data['init_mode'] = record.get('init_mode')
        data['skipped'] = record.get('skipped')
        data['owner'] = record.get('owner')
        data['setArenaBounds'] = record.get('setArenaBounds')
        data['visibilityMask'] = record.get('visibilityMask')
        data['vehicleID'] = record.get('vehicleID')
        data['seed'] = record.get('seed')
        physics = record['physics']
        if physics is not None:
            data['physics_dir'] = sorted(
                name for name in dir(physics) if not name.startswith('__'))
        if record.get('initialised'):
            data['physics_attributes_initialised'] = self._read_attributes(
                physics, PHYSICS_READ_ATTRIBUTES, 'initialised physics')
        elif self._config.get('fresh_attribute_reads') and physics is not None:
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
        if record.get('initialised') and self._config.get('throwaway_tests'):
            data['throwaway'] = self._throwaway_tests(record)
        return True

    def _throwaway_tests(self, record):
        """Setter/method acceptance on a body that is never simulated."""
        physics = record['physics']
        bot = record['bot']
        math_module = self._math()
        results = collections.OrderedDict()
        position = bot.get('position') or [0.0, 0.0, 0.0]
        yaw = float(bot.get('yaw') or 0.0)
        for name in ('actualChassisTransform', 'stabilisedMatrixWithLatency',
                     'matrix'):
            self._step('throwaway %s = Matrix' % name)
            try:
                setattr(physics, name, self._seed_matrix(position, yaw))
                readback = _matrix_translation(
                    getattr(physics, name), math_module)
                results[name] = {'set': 'ok', 'readback': readback,
                                 'distance_m': _distance(readback, position)}
            except Exception as error:
                results[name] = {'set': '%s: %s' % (
                    _type_name(error), str(error)[:100])}
        for name, value in (('handbrake', False), ('cruiseSignals', 0),
                            ('allowFreeze', True), ('staticMode', True),
                            ('staticMode', False)):
            self._step('throwaway %s = %r' % (name, value))
            before = _read_attribute(physics, name)
            try:
                setattr(physics, name, value)
                results['%s=%r' % (name, value)] = {
                    'before': before, 'set': 'ok',
                    'readback': _read_attribute(physics, name)}
            except Exception as error:
                results['%s=%r' % (name, value)] = {
                    'before': before, 'set': '%s: %s' % (
                        _type_name(error), str(error)[:100])}
        counters = {'before': 0, 'after': 0}
        def _before(*unused):
            counters['before'] += 1
        def _after(*unused):
            counters['after'] += 1
        for name, callback in (('subscribeBeforeSimulation', _before),
                               ('subscribeAfterSimulation', _after)):
            self._step('throwaway %s(callable)' % name)
            results[name] = _call_with(physics, name, (callback,))
        for name, args in (('getTouchedGround', (0,)), ('getTouchedGround', (1,)),
                           ('getTouchedMatkind', (0,)),
                           ('getTouchedMatkind', (1,)),
                           ('getAggressiveImpacts', ())):
            self._step('throwaway %s%r' % (name, args))
            results['%s%r' % (name, args)] = _call_with(physics, name, args)
        if math_module is not None:
            self._step('throwaway applyImpulseToCoM(Vector3(0, 0, 1))')
            results['applyImpulseToCoM'] = _call_with(
                physics, 'applyImpulseToCoM',
                (math_module.Vector3(0.0, 0.0, 1.0),))
        if self._config.get('owner_self_test'):
            # The setter demands a weakref to a native PyEntity; the worker's
            # own avatar is one.  Cleared again at once: this body is never
            # simulated and the avatar must not stay linked to it.
            try:
                player = self._bigworld().player()
            except Exception as error:
                player = None
                results['owner=weakref(player)'] = '<player: %s>' % str(
                    error)[:80]
            if player is not None:
                self._step('throwaway owner = weakref(BigWorld.player())')
                try:
                    physics.owner = weakref.ref(player)
                    results['owner=weakref(player)'] = 'ok'
                except Exception as error:
                    results['owner=weakref(player)'] = '%s: %s' % (
                        _type_name(error), str(error)[:100])
                self._step('throwaway owner = None')
                try:
                    physics.owner = None
                    results['owner=None'] = 'ok'
                except Exception as error:
                    results['owner=None'] = '%s: %s' % (
                        _type_name(error), str(error)[:100])
        return results

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

    def _frame_dt(self, frame_dt):
        return max(0.001, min(0.1, frame_dt if frame_dt > 0.0 else 0.033))

    def _subscribe(self, body):
        """Count before/after-simulation callbacks on a stepped body."""
        label = body['label']
        counters = {'before': 0, 'after': 0, 'registered': {}}
        self._subscriptions[label] = counters
        def _before(*unused):
            counters['before'] += 1
        def _after(*unused):
            counters['after'] += 1
        for name, callback in (('subscribeBeforeSimulation', _before),
                               ('subscribeAfterSimulation', _after)):
            self._step('%s %s(callable)' % (label, name))
            counters['registered'][name] = _call_with(
                body['physics'], name, (callback,))
        return counters

    def _stage_solve_one(self, frame_dt):
        """Body A: where is it after one update, then drive segments."""
        data = self._current['data']
        state = self._stage_state
        dt = self._frame_dt(frame_dt)
        if 'body' not in state:
            bodies = self._acquire_bodies(1)
            if not bodies:
                data['result'] = '<no body available>'
                return True
            body = bodies[0]
            state['body'] = body
            data['body'] = body['label']
            data['source'] = body['source']
            data['seed_position'] = (body.get('bot') or {}).get('position')
            data['base_cfg'] = (body['init'][0].get('base_cfg')
                                if body.get('init') else None)
            self._simulator_for_stage()
            data['subscriptions'] = self._subscribe(body)
            data['before'] = self._pose_snapshot(body)
            state['updates'] = []
            state['pose_index'] = 0
            state['pose_attempts'] = []
            state['phase'] = 'pose_check'
            self._driven.append(body)
            return False
        body = state['body']
        simulator = self._simulator_for_stage()
        reference = data.get('seed_position') or data['before'].get(
            'lastTickMatrix')
        if state['phase'] == 'pose_check':
            if not state['updates']:
                self._step('simulator.update(dt, [%s], [], [])' % body['label'])
            state['updates'].append(
                self._timed_update(simulator, dt, [body['physics']]))
            snapshot = self._pose_snapshot(body)
            attempt = {'method': POSE_METHODS[state['pose_index']],
                       'after_update': snapshot}
            state['pose_attempts'].append(attempt)
            if self._pose_ok(snapshot, reference):
                self._pose_method = attempt['method']
                data['pose_method'] = attempt['method']
                data['pose_lost'] = False
            else:
                state['pose_index'] += 1
                if state['pose_index'] < len(POSE_METHODS):
                    attempt_next = POSE_METHODS[state['pose_index']]
                    result = self._apply_pose(
                        body, reference,
                        float((body.get('bot') or {}).get('yaw') or 0.0),
                        attempt_next)
                    state['pose_attempts'].append(
                        {'method': attempt_next, 'apply': result})
                    data['pose_attempts'] = state['pose_attempts']
                    return False
                self._pose_method = POSE_METHODS[0]
                data['pose_method'] = None
                data['pose_lost'] = True
            data['pose_attempts'] = state['pose_attempts']
            state['phase'] = 'segments'
            state['segments'] = [
                ('forward', MOVE_FORWARD_SIGNAL, 1, 0,
                 float(self._config['drive_seconds'])),
                ('rotate_left', ROTATE_LEFT_SIGNAL, 0, -1,
                 float(self._config['rotate_seconds'])),
                ('backward', MOVE_BACKWARD_SIGNAL, -1, 0,
                 float(self._config['reverse_seconds'])),
                ('stop', 0, 0, 0, float(self._config['settle_seconds'])),
            ]
            state['segment'] = None
            data['segments'] = []
            return False
        segment = state['segment']
        if segment is None:
            if not state['segments']:
                data['after'] = self._pose_snapshot(body)
                updates = state['updates']
                data['update_ms'] = {
                    'calls': len(updates), 'min': min(updates),
                    'avg': sum(updates) / len(updates), 'max': max(updates)}
                data['moved_m'] = _distance(
                    data['before'].get('lastTickMatrix'),
                    data['after'].get('lastTickMatrix'))
                data['stop'] = self._set_signals(body, 0, 0, 0)
                return True
            name, signals, movement, rotation, seconds = state['segments'].pop(0)
            self._step('%s segment %s movementSignals=%d' % (
                body['label'], name, signals))
            segment = {
                'name': name, 'signals': signals, 'seconds': seconds,
                'start': self._clock(), 'frames': 0,
                'input': self._set_signals(body, signals, movement, rotation),
                'begin': self._pose_snapshot(body),
                'samples': [], 'max_speed': 0.0, 'min_height': None,
                'max_height': None, 'frozen_frames': 0}
            state['segment'] = segment
            data['segments'].append(segment)
        state['updates'].append(
            self._timed_update(simulator, dt, [body['physics']]))
        segment['frames'] += 1
        snapshot = self._pose_snapshot(body)
        try:
            speed = abs(float(snapshot.get('speed')))
        except (TypeError, ValueError):
            speed = 0.0
        segment['max_speed'] = max(segment['max_speed'], speed)
        height = snapshot.get('height')
        if isinstance(height, float):
            segment['min_height'] = (height if segment['min_height'] is None
                                     else min(segment['min_height'], height))
            segment['max_height'] = (height if segment['max_height'] is None
                                     else max(segment['max_height'], height))
        if snapshot.get('isFrozen') is True:
            segment['frozen_frames'] += 1
        if segment['frames'] <= 12 or segment['frames'] % 5 == 0:
            segment['samples'].append({
                't': round(self._clock() - segment['start'], 3),
                'p': snapshot.get('lastTickMatrix'),
                'h': height, 'v': snapshot.get('speed'),
                'yaw': snapshot.get('yaw'), 'frozen': snapshot.get('isFrozen'),
                'tracks': snapshot.get('gotTracksContact'),
                'ground': snapshot.get('groundType')})
        if self._clock() - segment['start'] < segment['seconds']:
            return False
        segment['end'] = snapshot
        segment['moved_m'] = _distance(
            segment['begin'].get('lastTickMatrix'),
            snapshot.get('lastTickMatrix'))
        begin_yaw = segment['begin'].get('yaw')
        end_yaw = snapshot.get('yaw')
        if isinstance(begin_yaw, float) and isinstance(end_yaw, float):
            delta = end_yaw - begin_yaw
            while delta > math.pi:
                delta -= 2.0 * math.pi
            while delta < -math.pi:
                delta += 2.0 * math.pi
            segment['yaw_delta'] = delta
        state['segment'] = None
        return False

    def _stage_solve_pair(self, frame_dt):
        """Body B re-seeded pair_gap_m ahead of A, facing it; both drive."""
        data = self._current['data']
        state = self._stage_state
        dt = self._frame_dt(frame_dt)
        if 'bodies' not in state:
            bodies = self._acquire_bodies(2)
            if len(bodies) < 2:
                data['result'] = '<fewer than two bodies>'
                return True
            first, second = bodies[0], bodies[1]
            state['bodies'] = (first, second)
            data['labels'] = [first['label'], second['label']]
            self._simulator_for_stage()
            anchor = self._pose_snapshot(first)
            position = anchor.get('lastTickMatrix')
            yaw = anchor.get('yaw')
            if not _finite_xyz(position) or not isinstance(yaw, float):
                position = (first.get('bot') or {}).get('position')
                yaw = float((first.get('bot') or {}).get('yaw') or 0.0)
            gap = float(self._config['pair_gap_m'])
            target = self._ahead(position, yaw, gap)
            ground = self._ground_y(target[0], target[1], target[2])
            if ground is not None:
                height = anchor.get('height')
                target[1] = ground + (height if isinstance(height, float) and
                                      0.0 <= height <= 3.0 else 0.5)
            data['pair_seed'] = {'anchor': position, 'anchor_yaw': yaw,
                                 'target': target, 'target_yaw': yaw + math.pi,
                                 'method': self._pose_method or POSE_METHODS[0]}
            data['pair_seed']['apply'] = self._apply_pose(
                second, target, yaw + math.pi,
                self._pose_method or POSE_METHODS[0])
            state['phase'] = 'seed_check'
            return False
        first, second = state['bodies']
        simulator = self._simulator_for_stage()
        if state['phase'] == 'seed_check':
            self._step('simulator.update(dt, [%s, %s], [], [])' % (
                first['label'], second['label']))
            self._timed_update(simulator, dt, [first['physics'],
                                               second['physics']])
            snapshot = self._pose_snapshot(second)
            data['pair_seed']['after_update'] = snapshot
            data['pair_seed']['ok'] = self._pose_ok(
                snapshot, data['pair_seed']['target'])
            data['before'] = [self._pose_snapshot(first), snapshot]
            self._step('pair movementSignals')
            data['input'] = [
                self._set_signals(first, MOVE_FORWARD_SIGNAL, 1, 0),
                self._set_signals(second, MOVE_FORWARD_SIGNAL, 1, 0)]
            state['start'] = self._clock()
            state['samples'] = []
            state['min_distance'] = None
            self._driven.extend([first, second])
            state['phase'] = 'drive'
            return False
        self._timed_update(simulator, dt, [first['physics'],
                                           second['physics']])
        a = self._pose_snapshot(first)
        b = self._pose_snapshot(second)
        distance = _distance(a.get('lastTickMatrix'), b.get('lastTickMatrix'))
        if distance is not None:
            state['min_distance'] = (
                distance if state['min_distance'] is None
                else min(state['min_distance'], distance))
        state['samples'].append({
            't': round(self._clock() - state['start'], 3), 'd': distance,
            'va': a.get('speed'), 'vb': b.get('speed'),
            'ha': a.get('height'), 'hb': b.get('height')})
        if self._clock() - state['start'] < float(
                self._config['pair_seconds']):
            return False
        data['after'] = [a, b]
        data['samples'] = state['samples']
        data['min_distance_m'] = state['min_distance']
        data['stop'] = [self._set_signals(first, 0, 0, 0),
                        self._set_signals(second, 0, 0, 0)]
        return True

    def _stage_solve_scale(self, frame_dt):
        """Batch cost: idle bodies, staticMode bodies, all bodies driving."""
        data = self._current['data']
        state = self._stage_state
        dt = self._frame_dt(frame_dt)
        if 'bodies' not in state:
            bodies = self._acquire_bodies(
                int(self._config.get('scale_bodies', 29)))
            state['bodies'] = bodies
            data['body_count'] = len(bodies)
            data['source'] = self._bodies_source
            data['labels'] = [body['label'] for body in bodies]
            data['note'] = ('bodies[0] and bodies[1] start where solve_one '
                            'and solve_pair left them')
            if not bodies:
                data['result'] = '<no bodies>'
                return True
            self._simulator_for_stage()
            state['phases'] = ['idle', 'static', 'driving']
            state['phase'] = None
            data['phases'] = []          # execution order, not key order
            return False
        bodies = state['bodies']
        simulator = self._simulator_for_stage()
        phase = state['phase']
        if phase is None:
            if not state['phases']:
                for body in bodies:
                    self._set_static(body, False)
                    self._set_signals(body, 0, 0, 0)
                data['final_poses'] = [
                    self._pose_snapshot(body) for body in bodies[:4]]
                return True
            name = state['phases'].pop(0)
            self._step('scale phase %s over %d bodies' % (name, len(bodies)))
            for body in bodies:
                if name == 'idle':
                    self._set_static(body, False)
                    self._set_signals(body, 0, 0, 0)
                elif name == 'static':
                    self._set_signals(body, 0, 0, 0)
                    self._set_static(body, True)
                else:
                    self._set_static(body, False)
                    self._set_signals(body, MOVE_FORWARD_SIGNAL, 1, 0)
                    if body not in self._driven:
                        self._driven.append(body)
            phase = {'name': name, 'updates': [],
                     'events_at_begin': len(self._callback_events)}
            state['phase'] = phase
            self._step('simulator.update(dt, %d bodies, [], [])' % len(bodies))
            return False
        phase['updates'].append(self._timed_update(
            simulator, dt, [body['physics'] for body in bodies]))
        if len(phase['updates']) < int(self._config['scale_frames']):
            return False
        updates = sorted(phase['updates'])
        count = len(updates)
        data['phases'].append({
            'name': phase['name'],
            'update_ms': {
                'calls': count, 'min': updates[0], 'p50': updates[count // 2],
                'p95': updates[int(count * 0.95)], 'max': updates[-1],
                'avg': sum(updates) / count},
            'per_body_avg_ms': sum(updates) / count / max(1, len(bodies)),
            'callbacks': self._callback_summary(phase['events_at_begin']),
            'sample_poses': [self._pose_snapshot(body) for body in bodies[:2]],
        })
        state['phase'] = None
        return False

    def _set_static(self, body, value):
        try:
            body['physics'].staticMode = bool(value)
        except Exception:
            pass

    def _stage_extras(self, frame_dt):
        """Impulse response and post-drive ground queries on body A."""
        data = self._current['data']
        state = self._stage_state
        dt = self._frame_dt(frame_dt)
        if 'body' not in state:
            bodies = self._acquire_bodies(1)
            if not bodies:
                data['result'] = '<no body available>'
                return True
            body = bodies[0]
            state['body'] = body
            data['body'] = body['label']
            physics = body['physics']
            math_module = self._math()
            for name, args in (('getTouchedGround', (0,)),
                               ('getTouchedGround', (1,)),
                               ('getTouchedMatkind', (0,)),
                               ('getTouchedMatkind', (1,)),
                               ('getAggressiveImpacts', ())):
                self._step('%s %s%r' % (body['label'], name, args))
                data['%s%r' % (name, args)] = _call_with(physics, name, args)
            mass = _read_attribute(physics, 'mass')
            data['mass'] = mass
            if math_module is None or not isinstance(mass, float):
                data['impulse'] = '<no Math or mass>'
                return True
            magnitude = mass * float(self._config['impulse_scale'])
            data['before'] = self._pose_snapshot(body)
            self._step('%s applyImpulseToCoM(Vector3(0, 0, %.3f))' % (
                body['label'], magnitude))
            data['impulse'] = _call_with(
                physics, 'applyImpulseToCoM',
                (math_module.Vector3(0.0, 0.0, magnitude),))
            data['impulse_magnitude'] = magnitude
            state['samples'] = []
            self._simulator_for_stage()
            return False
        body = state['body']
        self._timed_update(self._simulator_for_stage(), dt, [body['physics']])
        snapshot = self._pose_snapshot(body)
        state['samples'].append({'v': snapshot.get('speed'),
                                 'h': snapshot.get('height'),
                                 'p': snapshot.get('lastTickMatrix')})
        if len(state['samples']) < int(self._config['impulse_frames']):
            return False
        data['samples'] = state['samples']
        data['after'] = snapshot
        data['moved_m'] = _distance(data['before'].get('lastTickMatrix'),
                                    snapshot.get('lastTickMatrix'))
        return True

    def _physical_body_snapshot(self, body):
        math_module = self._math()
        result = {}
        try:
            matrix = body.matrix
            result['p'] = _matrix_translation(matrix, math_module)
            result['yaw'] = _matrix_yaw(matrix)
        except Exception as error:
            result['p'] = '<%s>' % str(error)[:60]
        position = result.get('p')
        if _finite_xyz(position):
            ground = self._ground_y(position[0], position[1], position[2])
            result['ground_y'] = ground
            result['h'] = position[1] - ground if ground is not None else None
        for name in ('velocity', 'isFrozen', 'isCollidingWithWorld',
                     'staticCollisionEnergy', 'staticCollisionPoint',
                     'staticCollisionNormal'):
            result[name] = _read_attribute(body, name)
        return result

    def _stage_physical_body(self, frame_dt):
        """Box body: drop onto terrain, push along the heading, release."""
        data = self._current['data']
        state = self._stage_state
        dt = self._frame_dt(frame_dt)
        math_module = self._math()
        if 'body' not in state:
            bots = self._bots(1)
            if not bots or math_module is None:
                data['result'] = '<no bot or Math>'
                return True
            bot = bots[0]
            position = list(bot.get('position') or [0.0, 0.0, 0.0])
            yaw = float(bot.get('yaw') or 0.0)
            ground = self._ground_y(position[0], position[1], position[2])
            base_y = ground if ground is not None else position[1]
            start = [position[0],
                     base_y + float(self._config['physical_body_drop_m']),
                     position[2]]
            half = [float(v) for v in
                    self._config['physical_body_half_extents']]
            mass = float(self._config['physical_body_mass'])
            record = collections.OrderedDict()
            self._step('BigWorld.WGPhysicalBody()')
            body = self._bigworld().WGPhysicalBody()
            self._step('body.setup(%r, Vector3%r)' % (mass, tuple(half)))
            record['setup'] = _call_with(
                body, 'setup', (mass, math_module.Vector3(*half)))
            self._step('body.addBoxShape(Vector3(-half), Vector3(half))')
            record['addBoxShape'] = _call_with(
                body, 'addBoxShape',
                (math_module.Vector3(-half[0], -half[1], -half[2]),
                 math_module.Vector3(half[0], half[1], half[2])))
            writes = collections.OrderedDict()
            for name, value in (('matrix', self._seed_matrix(start, yaw)),
                                ('staticMode', False), ('isFrozen', False),
                                ('visibilityMask', 1)):
                self._step('body.%s = %s' % (name, _plain(value)))
                try:
                    setattr(body, name, value)
                    writes[name] = 'ok'
                except Exception as error:
                    writes[name] = '%s: %s' % (
                        _type_name(error), str(error)[:80])
            record['writes'] = writes
            record['attributes'] = self._read_attributes(
                body, PHYSICAL_BODY_READ_ATTRIBUTES, 'body')
            record['callbacks'] = self._install_callbacks(body, 'physical_body')
            data['body'] = record
            data['start'] = start
            data['ground_y'] = ground
            data['yaw'] = yaw
            data['phases'] = []
            state['body'] = body
            state['mass'] = mass
            state['yaw'] = yaw
            state['updates'] = []
            state['phases'] = [
                ('drop', float(self._config['physical_body_seconds'])),
                ('push', float(self._config['physical_body_push_seconds'])),
                ('release', float(self._config['physical_body_release_seconds'])),
            ]
            state['phase'] = None
            self._simulator_for_stage()
            return False
        body = state['body']
        simulator = self._simulator_for_stage()
        phase = state['phase']
        if phase is None:
            if not state['phases']:
                updates = state['updates']
                data['update_ms'] = {
                    'calls': len(updates), 'min': min(updates),
                    'avg': sum(updates) / len(updates), 'max': max(updates)}
                data['after'] = self._physical_body_snapshot(body)
                state['done'] = True
                return True
            name, seconds = state['phases'].pop(0)
            force = None
            if name == 'push':
                magnitude = state['mass'] * 9.81 * float(
                    self._config['physical_body_push_g'])
                force = math_module.Vector3(
                    math.sin(state['yaw']) * magnitude, 0.0,
                    math.cos(state['yaw']) * magnitude)
            phase = {'name': name, 'seconds': seconds, 'start': self._clock(),
                     'frames': 0, 'samples': [], 'force': force,
                     'force_set': None, 'colliding_frames': 0,
                     'max_speed': 0.0, 'min_h': None, 'max_h': None}
            if name in ('push', 'release'):
                value = force if force is not None else math_module.Vector3(
                    0.0, 0.0, 0.0)
                self._step('body.externalForce = %s' % _plain(value))
                try:
                    body.externalForce = value
                    phase['force_set'] = 'ok'
                except Exception as error:
                    phase['force_set'] = '%s: %s' % (
                        _type_name(error), str(error)[:80])
            state['phase'] = phase
            data['phases'].append(phase)
            self._step('simulator.update(dt, [], [physical_body], [])')
            return False
        if phase['force'] is not None and phase['force_set'] == 'ok':
            try:
                body.externalForce = phase['force']
            except Exception:
                pass
        state['updates'].append(
            self._timed_update(simulator, dt, [], [body]))
        phase['frames'] += 1
        snapshot = self._physical_body_snapshot(body)
        velocity = snapshot.get('velocity')
        if _finite_xyz(velocity):
            speed = math.sqrt(sum(v * v for v in velocity))
            phase['max_speed'] = max(phase['max_speed'], speed)
        height = snapshot.get('h')
        if isinstance(height, float):
            phase['min_h'] = height if phase['min_h'] is None else min(
                phase['min_h'], height)
            phase['max_h'] = height if phase['max_h'] is None else max(
                phase['max_h'], height)
        if snapshot.get('isCollidingWithWorld') is True:
            phase['colliding_frames'] += 1
        if phase['frames'] <= 12 or phase['frames'] % 5 == 0:
            phase['samples'].append(dict(
                snapshot, t=round(self._clock() - phase['start'], 3)))
        if self._clock() - phase['start'] < phase['seconds']:
            return False
        phase['end'] = snapshot
        state['phase'] = None
        return False

    def _stage_restore(self, frame_dt):
        """Zero every signal this probe set.  Never raises."""
        data = self._current['data'] if self._current else {}
        restored = []
        seen = set()
        for body in self._driven + list(self._bodies):
            if id(body) in seen:
                continue
            seen.add(id(body))
            try:
                self._set_signals(body, 0, 0, 0)
                self._set_static(body, False)
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

    def factory_info(self):
        runtime = self._runtime
        factory = getattr(runtime, '_remote_factory', None)
        config = getattr(runtime, '_config', None) or {}
        return {
            'factory_type': _type_name(factory),
            'native_entities': bool(getattr(factory, 'native_entities', False)),
            'native_remote_vehicles_config': config.get(
                'native_remote_vehicles') if hasattr(config, 'get') else None,
            'worker_mode': bool(getattr(runtime, '_worker_mode', False))}

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
