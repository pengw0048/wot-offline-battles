import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


PACKAGE_ROOT = (Path(__file__).resolve().parents[1] / 'src' / 'res' /
                'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load(name):
    module_name = 'gui.mods.offline_lan_0922.' + name
    for package in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[package] = module
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Vector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Matrix(object):
    def __init__(self, x, y, z, yaw=0.0):
        self.translation = _Vector(x, y, z)
        self.yaw = yaw


class _FakePhysics(object):
    """Mimic the exe's WGVehiclePhysics Python surface closely enough."""

    created = []
    reject_configure = False
    # When set, the solver ignores the seeded lastTickMatrix (body stays at
    # the origin) until actualChassisTransform is written.
    ignore_last_tick_matrix = False

    def __init__(self):
        self.__dict__['callbacks'] = {}
        self.__dict__['initialised'] = False
        self.__dict__['subscribers'] = {'before': [], 'after': []}
        self.__dict__['coast'] = 0
        self.staticMode = True
        self.isFrozen = True
        self.movementSignals = 0
        self.speed = 0.0
        self.yaw = 0.3
        self.gotTracksContact = True
        self.groundType = 1
        self.distanceTraveled = 0.0
        self.position = ([0.0, 0.0, 0.0] if _FakePhysics.ignore_last_tick_matrix
                         else [0.0, 10.0, 0.0])
        self._refresh()
        _FakePhysics.created.append(self)

    def _refresh(self):
        matrix = _Matrix(self.position[0], self.position[1], self.position[2],
                         self.yaw)
        self.__dict__['lastTickMatrix'] = matrix
        self.__dict__['actualChassisTransform'] = matrix
        self.__dict__['stabilisedMatrixWithLatency'] = matrix

    def __setattr__(self, name, value):
        if name.endswith('Cb') or name == 'onVehicleStatusChanged':
            self.__dict__['callbacks'][name] = value
            return
        if name in ('lastTickMatrix', 'actualChassisTransform',
                    'stabilisedMatrixWithLatency') and hasattr(
                        value, 'translation') and self.__dict__.get('initialised'):
            honoured = (name == 'actualChassisTransform'
                        if _FakePhysics.ignore_last_tick_matrix
                        else name == 'lastTickMatrix')
            if honoured:
                self.__dict__['position'] = _xyz_list(value.translation)
                self.__dict__['yaw'] = float(getattr(value, 'yaw', 0.0))
                self._refresh()
                return
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name in ('mass', 'hullCOMZ'):
            if not self.__dict__.get('initialised'):
                raise RuntimeError(
                    'simulated native crash: %s before init' % name)
            return 45000.0 if name == 'mass' else 0.15
        if name in ('forceApplied', 'torqueApplied'):
            return _Vector(0.0, 0.0, 0.0)
        if name.endswith('Cb'):
            raise AttributeError(
                'Sorry, the attribute %s in WGVehiclePhysics is write-only'
                % name)
        raise AttributeError(name)

    def getTouchedGround(self, index):
        return index == 0

    def getTouchedMatkind(self, index):
        return 6

    def getAggressiveImpacts(self):
        return []

    def subscribeBeforeSimulation(self, callback):
        self.__dict__['subscribers']['before'].append(callback)

    def subscribeAfterSimulation(self, callback):
        self.__dict__['subscribers']['after'].append(callback)

    def applyImpulseToCoM(self, impulse):
        self.__dict__['coast'] = 4
        self.speed = 3.0

    def configure(self, *args):
        if len(args) != 1 or not isinstance(args[0], dict):
            raise TypeError('WGVehiclePhysics::configure: wrong arguments.')
        if _FakePhysics.reject_configure or 'modes' not in args[0]:
            return False
        self.__dict__['configured_cfg'] = args[0]
        self.__dict__['initialised'] = True
        return True

    def rollback(self, *args):
        raise TypeError('WGVehiclePhysics::rollback: wrong arguments.')

    def setArenaBounds(self, low, high):
        self.bounds = (low, high)

    def advance(self, dt):
        for callback in self.__dict__['subscribers']['before']:
            callback()
        signals = self.movementSignals
        if self.staticMode:
            signals = 0
        import math
        forward = (math.sin(self.yaw), math.cos(self.yaw))
        if signals & 1:
            self.speed = 5.0
        elif signals & 2:
            self.speed = -3.0
        elif self.__dict__['coast'] > 0:
            self.__dict__['coast'] -= 1
        else:
            self.speed = 0.0
        self.position[0] += forward[0] * self.speed * dt
        self.position[2] += forward[1] * self.speed * dt
        self.distanceTraveled += abs(self.speed) * dt
        if signals & 4:
            self.yaw += 0.5 * dt
        if signals & 8:
            self.yaw -= 0.5 * dt
        self.isFrozen = (signals == 0 and self.speed == 0.0)
        self._refresh()
        for callback in self.__dict__['subscribers']['after']:
            callback()


def _xyz_list(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


class _FakeEntity(object):
    """Stands in for BigWorld.Entity; only native entities may own a body."""


class _FakeSimulator(object):
    def __init__(self):
        self.numSubsteps = 2
        self.numIterations = 10
        self.updates = []

    def update(self, dt, physics, bodies, collision_models):
        # The exe registers update(float, PyObject, PyObject, PyObject).
        for sequence in (physics, bodies, collision_models):
            if not isinstance(sequence, list):
                raise TypeError('WGDynamicsSimulator.update: wrong arguments.')
        self.updates.append((dt, len(physics), len(bodies)))
        for item in list(physics) + list(bodies):
            item.advance(dt)


class _FakeBody(object):
    """WGPhysicalBody stand-in: a box that falls onto the fake ground (y=9.5)."""

    GROUND_Y = 9.5

    def __init__(self):
        self.__dict__['callbacks'] = {}
        self.isCollidingWithWorld = False
        self.isFrozen = True
        self.staticMode = True
        self.visibilityMask = 0
        self.mass = 0.0
        self.gravity = 9.81
        self.half = [0.5, 0.5, 0.5]
        self.position = [0.0, 0.0, 0.0]
        self.yaw = 0.0
        self.vel = [0.0, 0.0, 0.0]
        self.externalForce = _Vector(0.0, 0.0, 0.0)
        self.staticCollisionPoint = _Vector(0.0, 0.0, 0.0)
        self.staticCollisionNormal = _Vector(0.0, 1.0, 0.0)
        self.staticCollisionEnergy = 0.0
        self.staticCollisionReaction = 0.0
        self.staticCollisionSelfPoint = _Vector(0.0, 0.0, 0.0)
        self.angVelocity = _Vector(0.0, 0.0, 0.0)
        self.forceApplied = _Vector(0.0, 0.0, 0.0)
        self.torqueApplied = _Vector(0.0, 0.0, 0.0)
        self.isCwwThresholdFactor = 0.0
        self.staticSceneFriction = 0.5
        self.isUnderWater = False
        self.freezePosErrorEpsilon = 0.0

    def __setattr__(self, name, value):
        if name.endswith('Cb'):
            self.__dict__['callbacks'][name] = value
            return
        if name == 'matrix':
            self.__dict__['position'] = _xyz_list(value.translation)
            self.__dict__['yaw'] = float(getattr(value, 'yaw', 0.0))
            return
        object.__setattr__(self, name, value)

    @property
    def matrix(self):
        return _Matrix(self.position[0], self.position[1], self.position[2],
                       self.yaw)

    @property
    def velocity(self):
        return _Vector(*self.vel)

    def setup(self, mass, half):
        if not isinstance(mass, float):
            raise TypeError('WGPhysicalBody::setup: wrong arguments')
        self.mass = mass
        self.half = _xyz_list(half)

    def addBoxShape(self, low, high):
        self.box = (_xyz_list(low), _xyz_list(high))

    def advance(self, dt):
        if self.staticMode:
            return
        force = self.externalForce
        self.vel[0] += float(force.x) / max(1.0, self.mass) * dt
        self.vel[2] += float(force.z) / max(1.0, self.mass) * dt
        self.vel[1] -= self.gravity * dt
        for axis in range(3):
            self.position[axis] += self.vel[axis] * dt
        floor = self.GROUND_Y + self.half[1]
        if self.position[1] <= floor:
            self.position[1] = floor
            self.vel[1] = 0.0
            self.isCollidingWithWorld = True
            self.staticCollisionPoint = _Vector(
                self.position[0], self.GROUND_Y, self.position[2])
            self.staticCollisionEnergy = 1.0
        else:
            self.isCollidingWithWorld = False
        self.isFrozen = all(abs(v) < 1e-6 for v in self.vel)


class _FakeNativeFilter(object):
    """Stock WGVehicleFilter stand-in; may or may not carry physics."""

    def __init__(self, physics):
        self._physics = physics
        self.inputs = []
        self.bodyMatrix = _Matrix(0.0, 10.0, 0.0)

    def getVehiclePhysics(self):
        return self._physics

    def notifyInputKeysDown(self, movement, rotation):
        self.inputs.append((movement, rotation))


class _Named(object):
    def __init__(self, name):
        self.name = name


class _FakeVehicleType(object):
    def __init__(self):
        self.name = 'china:Ch02_Type62'
        self.speedLimits = (16.666, 6.388)
        self.xphysics = {
            'engines': {'_12150L-3_V-12': {'smplEnginePower': 454.6309}},
            'chassis': {'Chassis_Ch02_Type62': {'grounds': {
                'ground': {'rollingFriction': 0.0483},
                'stone': {'rollingFriction': 0.0483}}}}}
        self.useHullZ = False
        self.hullAimingParams = {'pitch': {'isAvailable': False},
                                 'yaw': {'isAvailable': False}}


class _FakeDescriptor(object):
    def __init__(self, siege=False):
        self.type = _FakeVehicleType()
        self.engine = _Named('_12150L-3_V-12')
        self.chassis = _Named('Chassis_Ch02_Type62')
        self.hasSiegeMode = siege
        self.physics = {'weight': 21500.0}


class _FakeNativeEntity(_FakeEntity):
    def __init__(self, physics, position):
        self.filter = _FakeNativeFilter(physics)
        self.typeDescriptor = _FakeDescriptor()
        self.position = _Vector(*position)
        self.yaw = 0.3


class _FakeCarrier(object):
    def __init__(self, position):
        self.filter = object()
        self.position = _Vector(*position)
        self.yaw = 0.3
        self.typeDescriptor = object()


class _FakeMath(object):
    Matrix = _Matrix.__class__  # replaced below

    class Vector3(_Vector):
        pass


class _MatrixFactory(object):
    def __call__(self):
        matrix = _Matrix(0.0, 0.0, 0.0)
        matrix.setRotateYPR = lambda ypr: setattr(matrix, 'yaw', float(ypr[0]))
        return matrix


class _FakeMathModule(object):
    def __init__(self):
        self.Matrix = _MatrixFactory()
        self.Vector3 = _Vector


class _FakeHost(object):
    def __init__(self, bots, bigworld):
        self._bots = bots
        self._bigworld = bigworld
        self._math = _FakeMathModule()

    def bigworld(self):
        return self._bigworld

    def math_module(self):
        return self._math

    def constants(self):
        return None

    def factory_info(self):
        return {'factory_type': 'FakeFactory', 'native_entities': False,
                'native_remote_vehicles_config': False, 'worker_mode': True}

    def bot_entities(self):
        return list(self._bots)


def _fake_physics_shared():
    """A plain module: dir() of a module lists only its __dict__."""
    module = types.ModuleType('physics_shared')
    module.IS_CLIENT = True
    module.NUM_SUBSTEPS = 2
    module.NUM_ITERATIONS = 10
    module.FRICTION_RATIO = 1.0

    module.CONTACT_ENERGY_POW = 3.0
    module.CONTACT_FRICTION_TERRAIN = 1.0
    module.g_defaultTankXPhysicsCfg = {'clearance': 0.7, 'engine': {}}
    module.calls = []

    def configurePhysics(physics, baseCfg, typeDesc, gravityFactor):
        module.calls.append(('configurePhysics', typeDesc.type.name,
                             typeDesc.engine.name, gravityFactor))
        detailed = typeDesc.type.xphysics['detailed']
        if detailed is not baseCfg:
            raise AssertionError('proxy must expose the same detailed dict')
        cfg = {'modes': {'normal': {
            'fullMass': typeDesc.physics['weight'] * 0.001,
            'engine': dict(detailed['engines'][typeDesc.engine.name]),
            'chassis': dict(detailed['chassis'][typeDesc.chassis.name])}},
            'vehicleType': 0}
        if not physics.configure(cfg):
            module.calls.append(('LOG_ERROR', 'configure failed'))
        physics.centerOfMass = _Vector(0.0, 1.25, physics.hullCOMZ)
        physics.isFrozen = False
        physics.movementSignals = 0

    def initVehiclePhysicsClient(physics, descriptor):
        module.calls.append(('initVehiclePhysicsClient',))
        physics.staticMode = False
        physics.__dict__['initialised'] = True
    module.configurePhysics = configurePhysics
    module.initVehiclePhysicsClient = initVehiclePhysicsClient
    return module


class NativePhysicsProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('native_physics_probe')
        _FakePhysics.created = []
        _FakePhysics.ignore_last_tick_matrix = False
        self.player = _FakeEntity()
        self.player.arenaTypeID = (5 << 16) | 7
        self.player.spaceID = 3
        self.lines = []
        self.clock = [1000.0]
        self.directory = tempfile.mkdtemp()
        self.simulator = _FakeSimulator()
        self.physics_params = []
        _FakePhysics.reject_configure = False
        self.bigworld = types.SimpleNamespace(
            WGVehiclePhysics=_FakePhysics,
            WGDynamicsSimulator=lambda: self.simulator,
            WGPhysicalBody=_FakeBody,
            Entity=_FakeEntity,
            player=lambda: self.player,
            wg_collideSegment=lambda space, top, bottom, flags: (
                _Vector(top.x, _FakeBody.GROUND_Y, top.z), None, None),
            wg_setupPhysicsParam=lambda name, value: self.physics_params.append(
                (name, value)))
        self.physics_shared = _fake_physics_shared()
        sys.modules['physics_shared'] = self.physics_shared

    def tearDown(self):
        sys.modules.pop('physics_shared', None)

    def _bots(self, retail):
        bots = []
        for index in range(3):
            position = [float(index * 10), 10.0, 0.0]
            physics = None
            if retail:
                physics = _FakePhysics()
                physics.__dict__['initialised'] = True
                physics.staticMode = False
                physics.position = list(position)
            bots.append({
                'bot_id': 11 + index, 'engine_id': 100 + index,
                'native': _FakeNativeEntity(physics, position),
                'carrier': _FakeCarrier(position),
                'descriptor': _FakeDescriptor(), 'position': position,
                'yaw': 0.3})
        return bots

    def _probe(self, bots, **config):
        base = {'start_delay_seconds': 0.5, 'passive_seconds': 0.2,
                'drive_seconds': 0.3, 'rotate_seconds': 0.2,
                'reverse_seconds': 0.2, 'settle_seconds': 0.2,
                'pair_seconds': 0.2, 'scale_frames': 5, 'scale_bodies': 3,
                'impulse_frames': 3, 'vehicle_solver': True,
                'physical_body_seconds': 1.0,
                'physical_body_push_seconds': 0.3,
                'physical_body_release_seconds': 0.2}
        base.update(config)
        host = _FakeHost(bots, self.bigworld)
        return self.module.WorkerPhysicsProbe(
            host, self.directory, config=base,
            writer=self.lines.append, clock=lambda: self.clock[0],
            perf_clock=lambda: self.clock[0])

    def _run(self, probe, frames=300, dt=0.05):
        for _ in range(frames):
            if probe.done:
                break
            self.clock[0] += dt
            probe.tick(True, 1, dt)

    def _report(self):
        path = os.path.join(
            self.directory, 'offline-worker-native-physics-probe-round1.json')
        with open(path, 'rb') as stream:
            return json.loads(stream.read().decode('utf-8'))

    def test_default_stage_order_keeps_signatures_opt_in(self):
        probe = self._probe(self._bots(retail=True))
        self.assertEqual(
            ['inventory', 'inspect_existing', 'construct_standalone',
             'passive_drive', 'solve_one', 'solve_pair', 'solve_scale',
             'extras', 'physical_body', 'restore'], probe._stages)
        opted = self._probe(self._bots(retail=True),
                            opt_in_stages=['signatures'])
        self.assertEqual('signatures', opted._stages[-1])

    def test_vehicle_solver_stages_are_off_by_default(self):
        bots = self._bots(retail=False)
        host = _FakeHost(bots, self.bigworld)
        probe = self.module.WorkerPhysicsProbe(
            host, self.directory, config={'start_delay_seconds': 0.5,
                                          'physical_body_seconds': 1.0,
                                          'physical_body_push_seconds': 0.3,
                                          'physical_body_release_seconds': 0.2},
            writer=self.lines.append, clock=lambda: self.clock[0],
            perf_clock=lambda: self.clock[0])
        self.assertEqual(
            ['inventory', 'inspect_existing', 'construct_standalone',
             'physical_body', 'restore'], probe._stages)
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        self.assertEqual(
            {'ok'}, set(stage['status'] for stage in report['stages']),
            report['stages'])
        self.assertIn('collidePolyhedra UNIMPLEMENTED',
                      report['skipped_stages']['solve_one'])
        self.assertEqual(5, len(report['skipped_stages']))
        self.assertNotIn('simulator.update(dt, [standalone', ''.join(self.lines))
        # No WGVehiclePhysics was ever handed to the simulator.
        self.assertTrue(all(count == 0 for _, count, _ in self.simulator.updates))

    def test_physical_body_drops_pushes_and_releases(self):
        bots = self._bots(retail=False)
        probe = self._probe(bots, stages=['physical_body'])
        self._run(probe)
        report = self._report()
        stage = [s for s in report['stages'] if s['name'] == 'physical_body'][0]
        self.assertEqual('ok', stage['status'], stage)
        data = stage['data']
        self.assertEqual('returned null', data['body']['setup'])
        self.assertEqual('returned null', data['body']['addBoxShape'])
        self.assertEqual({'matrix': 'ok', 'staticMode': 'ok', 'isFrozen': 'ok',
                          'visibilityMask': 'ok'}, data['body']['writes'])
        self.assertEqual(20.0, data['body']['attributes']['mass'])
        self.assertEqual(9.5, data['ground_y'])
        self.assertEqual(12.5, data['start'][1])
        self.assertEqual(['drop', 'push', 'release'],
                         [phase['name'] for phase in data['phases']])
        drop, push, release = data['phases']
        self.assertAlmostEqual(1.0, drop['min_h'], places=3)
        self.assertGreater(drop['colliding_frames'], 0)
        self.assertEqual(9.5, drop['end']['staticCollisionPoint'][1])
        self.assertEqual('ok', push['force_set'])
        self.assertGreater(push['max_speed'], 0.0)
        self.assertGreater(push['end']['p'][2], drop['end']['p'][2])
        self.assertEqual('ok', release['force_set'])
        self.assertGreater(data['update_ms']['calls'], 10)
        joined = ''.join(self.lines)
        self.assertIn('step=body.setup(20.0, Vector3(1.5, 1.0, 3.0))', joined)
        self.assertIn('step=simulator.update(dt, [], [physical_body], [])', joined)

    def test_retail_bodies_are_driven_when_preferred(self):
        bots = self._bots(retail=True)
        probe = self._probe(bots, opt_in_stages=['signatures'],
                            prefer_standalone=False,
                            read_retail_physics_attributes=True)
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual(
            {'ok'}, set(stage['status'] for stage in report['stages']),
            report['stages'])
        self.assertEqual('retail', report['bodies_source'])
        existing = by_name['inspect_existing']['data']['entities'][0]
        self.assertEqual('_FakeNativeFilter', existing['native_filter_type'])
        self.assertEqual('_FakePhysics', existing['retail_physics_type'])
        self.assertEqual(45000.0, existing['physics_attributes']['mass'])
        solve_one = by_name['solve_one']['data']
        self.assertEqual('retail', solve_one['source'])
        self.assertGreater(solve_one['moved_m'], 0.5)
        self.assertEqual(
            [1, 0], solve_one['segments'][0]['input']['notifyInputKeysDown'])
        scale = by_name['solve_scale']['data']
        self.assertEqual(3, scale['body_count'])
        self.assertEqual(['idle', 'static', 'driving'],
                         [phase['name'] for phase in scale['phases']])
        self.assertEqual(5, scale['phases'][0]['update_ms']['calls'])
        for bot in bots:
            physics = bot['native'].filter.getVehiclePhysics()
            self.assertEqual(0, physics.movementSignals)
            self.assertFalse(physics.staticMode)
        throwaway = construct_throwaway = by_name['construct_standalone']['data']['throwaway']
        self.assertEqual('ok', throwaway['owner=weakref(player)'])
        self.assertEqual('ok', throwaway['owner=None'])
        self.assertEqual('returned true', throwaway['getTouchedGround(0,)'])
        self.assertEqual('returned 6', throwaway['getTouchedMatkind(1,)'])
        self.assertEqual('returned null', throwaway['subscribeBeforeSimulation'])
        self.assertEqual('ok', throwaway['actualChassisTransform']['set'])
        self.assertEqual('ok', throwaway['staticMode=False']['set'])
        self.assertEqual('returned null', throwaway['applyImpulseToCoM'])
        joined_steps = ''.join(self.lines)
        self.assertIn('step=throwaway owner = weakref(BigWorld.player())', joined_steps)
        self.assertIn('step=throwaway owner = None', joined_steps)
        # construct_standalone never reads a fresh body before init.
        construct = by_name['construct_standalone']['data']
        self.assertTrue(construct['initialised'])
        self.assertEqual('detailed', construct['init_mode'])
        detailed = construct['init'][0]
        self.assertEqual('detailed', detailed['call'])
        self.assertEqual('ok', detailed['result'])
        self.assertEqual(True, detailed['configure'])
        self.assertEqual(0.15, detailed['hullCOMZ'])
        self.assertEqual(['centerOfMass', 'isFrozen', 'movementSignals'],
                         detailed['writes'])
        self.assertEqual(
            {'smplEnginePower': 454.6309, 'smplFwMaxSpeed': 16.666,
             'smplBkMaxSpeed': 6.388},
            detailed['base_cfg']['engines']['_12150L-3_V-12'])
        self.assertEqual({}, detailed['base_cfg']['chassis']['Chassis_Ch02_Type62'])
        self.assertEqual(21.5, detailed['cfg_normal']['fullMass'])
        self.assertEqual(
            45000.0, construct['physics_attributes_initialised']['mass'])
        self.assertNotIn('physics_attributes_fresh', construct)
        self.assertEqual('lastTickMatrix = Matrix', construct['seed']['result'])
        self.assertEqual(32, construct['visibilityMask'])
        # The recorded centerOfMass carries the native hullCOMZ, not 0.0.
        physics = _FakePhysics.created[-1]
        self.assertEqual(0.15, physics.centerOfMass.z)
        self.assertEqual('ok', report['common_conf']['CONTACT_ENERGY_POW'])
        self.assertEqual('<constant missing>',
                         report['common_conf']['WARMSTARTING_THRESHOLD'])
        self.assertEqual([('CONTACT_ENERGY_POW', 3.0),
                          ('CONTACT_FRICTION_TERRAIN', 1.0)],
                         self.physics_params)
        joined = ''.join(self.lines)
        self.assertIn('step=construct BigWorld.WGVehiclePhysics()', joined)
        self.assertIn('step=construct physics_shared.configurePhysics(recorder)',
                      joined)
        self.assertIn('step=construct physics.configure(cfg)', joined)
        self.assertIn('step=construct physics.hullCOMZ', joined)
        self.assertIn('step=construct physics.visibilityMask = 32', joined)
        self.assertIn('step=BigWorld.wg_setupPhysicsParam(CONTACT_ENERGY_POW, 3.0)',
                      joined)
        # configure() is never tried as a pose setter.
        self.assertNotIn('seed configure(', joined)
        self.assertIn('stage=signatures end status=ok', joined)
        signatures = by_name['signatures']['data']
        self.assertIn('wrong arguments', signatures['physics']['rollback'])

    def test_standalone_bodies_are_preferred_over_retail_bodies(self):
        bots = self._bots(retail=True)
        probe = self._probe(bots)
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual(
            {'ok'}, set(stage['status'] for stage in report['stages']),
            report['stages'])
        self.assertEqual('standalone', report['bodies_source'])
        existing = by_name['inspect_existing']['data']['entities'][0]
        self.assertEqual('_FakePhysics', existing['retail_physics_type'])
        self.assertEqual('<skipped: retail never reads them>',
                         existing['physics_attributes'])
        solve_one = by_name['solve_one']['data']
        self.assertEqual('standalone', solve_one['source'])
        self.assertGreater(solve_one['moved_m'], 0.5)
        for bot in bots:
            self.assertEqual(0, bot['native'].filter.getVehiclePhysics().movementSignals)
        self.assertEqual(
            ['detailed'] * 3,
            [body['init_mode'] for body in report['standalone_bodies']])

    def test_rejected_configure_falls_back_without_reading_the_body(self):
        _FakePhysics.reject_configure = True
        bots = self._bots(retail=False)
        probe = self._probe(bots, init_order=['detailed'])
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual('none', report['bodies_source'])
        construct = by_name['construct_standalone']['data']
        self.assertFalse(construct['initialised'])
        self.assertEqual('<configure returned False>',
                         construct['init'][0]['result'])
        self.assertNotIn('physics_attributes_initialised', construct)
        joined = ''.join(self.lines)
        self.assertNotIn('physics.hullCOMZ', joined)
        self.assertNotIn('before init', joined)
        self.assertEqual('<no body available>',
                         by_name['solve_one']['data']['result'])
        # The client recipe still works as the second choice.
        _FakePhysics.reject_configure = True
        probe = self._probe(bots)
        self.lines[:] = []
        self._run(probe)
        report = self._report()
        self.assertEqual('standalone', report['bodies_source'])
        self.assertEqual(
            ['client'] * 3,
            [body['init_mode'] for body in report['standalone_bodies']])
        self.assertEqual('<configure returned False>',
                         report['standalone_bodies'][0]['init'][0]['result'])
        # The client recipe ran on a fresh object, never on the rejected one:
        # construct stage + 3 Bots = 4 objects in the first probe (detailed
        # only), 2 per body in the second (detailed rejected, then client).
        self.assertEqual(12, len(_FakePhysics.created))
        rejected = [physics for physics in _FakePhysics.created
                    if physics.__dict__.get('initialised') is not True]
        self.assertEqual(8, len(rejected))
        self.assertTrue(all('centerOfMass' not in physics.__dict__
                            for physics in rejected))

    def test_siege_descriptors_are_skipped(self):
        bots = self._bots(retail=False)
        bots[0]['descriptor'] = _FakeDescriptor(siege=True)
        probe = self._probe(bots)
        self._run(probe)
        report = self._report()
        skipped = [body for body in report['standalone_bodies']
                   if body['skipped'] == 'hasSiegeMode']
        self.assertEqual(['standalone:11'], [body['label'] for body in skipped])
        self.assertEqual(2, len([body for body in report['standalone_bodies']
                                 if body['initialised']]))

    def test_derived_xphysics_shapes(self):
        derived = self.module.derived_xphysics(_FakeDescriptor())
        self.assertEqual(
            {'gravityFactor': 1.0,
             'engines': {'_12150L-3_V-12': {
                 'smplEnginePower': 454.6309, 'smplFwMaxSpeed': 16.666,
                 'smplBkMaxSpeed': 6.388}},
             'chassis': {'Chassis_Ch02_Type62': {}}}, derived)
        shipped = self.module.derived_xphysics(_FakeDescriptor(), 'shipped')
        grounds = shipped['chassis']['Chassis_Ch02_Type62']['grounds']
        self.assertEqual(['ground', 'soft', 'stone'], sorted(grounds))
        self.assertEqual({'medium': {'rollingFriction': 0.0483}},
                         grounds['ground'])
        self.assertEqual({'rollingFriction': 0.0483}, grounds['soft'])

    def test_standalone_bodies_are_built_when_retail_bodies_are_absent(self):
        bots = self._bots(retail=False)
        probe = self._probe(bots)
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual(
            {'ok'}, set(stage['status'] for stage in report['stages']),
            report['stages'])
        self.assertEqual('standalone', report['bodies_source'])
        existing = by_name['inspect_existing']['data']['entities'][0]
        self.assertEqual('NoneType', existing['retail_physics_type'])
        solve_one = by_name['solve_one']['data']
        self.assertEqual('standalone', solve_one['source'])
        self.assertEqual('standalone:11', solve_one['body'])
        self.assertGreater(solve_one['moved_m'], 0.5)
        self.assertEqual('lastTickMatrix = Matrix', solve_one['pose_method'])
        self.assertFalse(solve_one['pose_lost'])
        self.assertEqual(1, len(solve_one['pose_attempts']))
        segments = solve_one['segments']
        self.assertEqual(['forward', 'rotate_left', 'backward', 'stop'],
                         [segment['name'] for segment in segments])
        self.assertNotIn('notifyInputKeysDown', segments[0]['input'])
        self.assertEqual(5.0, segments[0]['max_speed'])
        self.assertGreater(segments[0]['moved_m'], 0.5)
        self.assertGreater(segments[1]['yaw_delta'], 0.0)
        self.assertEqual(3.0, segments[2]['max_speed'])
        self.assertGreater(segments[3]['frozen_frames'], 0)
        self.assertEqual(0.5, segments[0]['samples'][0]['h'])
        self.assertEqual(0.5, segments[0]['min_height'])
        self.assertEqual(0.5, solve_one['before']['height'])
        self.assertGreater(
            report['subscriptions']['standalone:11']['before'], 0)
        self.assertEqual(
            report['subscriptions']['standalone:11']['before'],
            report['subscriptions']['standalone:11']['after'])
        pair = by_name['solve_pair']['data']
        self.assertTrue(pair['pair_seed']['ok'])
        self.assertEqual('lastTickMatrix = Matrix', pair['pair_seed']['method'])
        self.assertEqual('ok', pair['pair_seed']['apply'])
        self.assertLess(pair['min_distance_m'], 20.0)
        self.assertGreater(len(pair['samples']), 1)
        self.assertEqual({}, by_name['solve_pair']['callbacks'])
        scale = by_name['solve_scale']['data']
        self.assertEqual(3, scale['body_count'])
        self.assertEqual(['idle', 'static', 'driving'],
                         [phase['name'] for phase in scale['phases']])
        for phase in scale['phases']:
            self.assertEqual(5, phase['update_ms']['calls'])
            self.assertEqual(2, len(phase['sample_poses']))
        self.assertEqual(3, len(scale['final_poses'][0]['lastTickMatrix']))
        self.assertEqual(0.5, scale['final_poses'][0]['height'])
        extras = by_name['extras']['data']
        self.assertEqual('returned true', extras['getTouchedGround(0,)'])
        self.assertEqual('returned null', extras['impulse'])
        self.assertEqual(45000.0 * 5.0, extras['impulse_magnitude'])
        self.assertEqual(3, len(extras['samples']))
        self.assertGreater(extras['moved_m'], 0.0)
        self.assertEqual(3, len(report['standalone_bodies']))
        existing = by_name['inspect_existing']['data']
        self.assertEqual(False, existing['entities'][0]['carrier_is_entity'])
        self.assertIn('_FakeCarrier', existing['entities'][0]['carrier_mro'])
        self.assertEqual('FakeFactory', existing['factory']['factory_type'])
        self.assertEqual('_FakeEntity', existing['player_type'])
        for index, body in enumerate(report['standalone_bodies']):
            self.assertTrue(body['initialised'])
            self.assertEqual('detailed', body['init_mode'])
            self.assertEqual('_FakeNativeEntity', body['owner'])
            self.assertEqual(32, body['visibilityMask'])
            self.assertEqual(100 + index, body['vehicleID'])
            self.assertEqual('lastTickMatrix = Matrix', body['seed']['result'])
        # Common conf is applied exactly once for the whole probe.
        self.assertEqual(2, len(self.physics_params))
        self.assertEqual(self.module.UPDATE_SIGNATURE, report['update_signature'])
        joined = ''.join(self.lines)
        self.assertIn('step=simulator.update(dt, [standalone:11], [], [])', joined)
        self.assertNotIn('seed rollback', joined)
        self.assertEqual(4, self.simulator.update.__code__.co_argcount - 1)
        self.assertEqual(
            ['standalone:11', 'standalone:12'],
            by_name['solve_pair']['data']['labels'])
        self.assertEqual(2, report['simulator_settings']['numSubsteps'])

    def test_pose_fallback_when_solver_ignores_last_tick_matrix(self):
        _FakePhysics.ignore_last_tick_matrix = True
        bots = self._bots(retail=False)
        probe = self._probe(bots, stages=['solve_one', 'solve_pair'])
        self._run(probe)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        solve_one = by_name['solve_one']['data']
        self.assertEqual('actualChassisTransform = Matrix',
                         solve_one['pose_method'])
        self.assertFalse(solve_one['pose_lost'])
        methods = [attempt['method'] for attempt in solve_one['pose_attempts']]
        self.assertEqual(['lastTickMatrix = Matrix',
                          'actualChassisTransform = Matrix',
                          'actualChassisTransform = Matrix'], methods)
        self.assertEqual([0.0, 0.0, 0.0],
                         solve_one['pose_attempts'][0]['after_update']['lastTickMatrix'])
        self.assertGreater(solve_one['moved_m'], 0.5)
        pair = by_name['solve_pair']['data']
        self.assertEqual('actualChassisTransform = Matrix',
                         pair['pair_seed']['method'])
        self.assertTrue(pair['pair_seed']['ok'])
        self.assertIn('step=standalone:11 pose actualChassisTransform = Matrix',
                      ''.join(self.lines))

    def test_plain_carrier_never_becomes_owner(self):
        bots = self._bots(retail=False)
        for bot in bots:
            bot['native'] = None
        probe = self._probe(bots, stages=['construct_standalone'])
        self._run(probe)
        report = self._report()
        construct = [s for s in report['stages']
                     if s['name'] == 'construct_standalone'][0]['data']
        self.assertTrue(construct['initialised'])
        self.assertEqual('<skipped: _FakeCarrier is not a BigWorld.Entity>',
                         construct['owner'])
        self.assertEqual(100, construct['vehicleID'])
        self.assertNotIn('physics.owner', ''.join(self.lines))

    def test_stage_exception_is_recorded_and_probe_continues(self):
        def broken(*unused):
            raise RuntimeError('simulator refused')
        self.simulator.update = broken
        probe = self._probe(self._bots(retail=True))
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual('error', by_name['solve_one']['status'])
        self.assertIn('simulator refused', by_name['solve_one']['error'])
        self.assertEqual('ok', by_name['restore']['status'])
        self.assertEqual('ok', by_name['inventory']['status'])

    def test_report_records_last_begun_stage_and_step(self):
        probe = self._probe(self._bots(retail=True),
                            stages=['inventory', 'inspect_existing'])
        probe.tick(True, 1, 0.05)
        self.clock[0] += 1.0
        probe.tick(True, 1, 0.05)
        report = self._report()
        self.assertEqual('inventory', report['last_begun'])
        self.assertEqual(['inventory', 'inspect_existing', 'restore'],
                         probe._stages)
        self.clock[0] += 0.1
        probe.tick(True, 1, 0.05)
        self.assertIn('stage=inspect_existing step=bot:11 native.filter.speedInfo',
                      ''.join(self.lines))

    def test_probe_waits_for_live_and_start_delay(self):
        probe = self._probe(self._bots(retail=True), start_delay_seconds=5.0)
        probe.tick(False, 1, 0.05)
        self.clock[0] += 10.0
        probe.tick(False, 1, 0.05)
        self.assertEqual([], self._lines_with('stage='))
        probe.tick(True, 1, 0.05)
        self.clock[0] += 4.0
        probe.tick(True, 1, 0.05)
        self.assertEqual([], self._lines_with('stage='))
        self.clock[0] += 1.5
        probe.tick(True, 1, 0.05)
        self.assertEqual(1, len(self._lines_with('stage=inventory begin')))

    def _lines_with(self, text):
        return [line for line in self.lines if text in line]

    def test_config_loader_merges_section_and_disables(self):
        path = os.path.join(self.directory, 'worker_diagnostics.json')
        with open(path, 'wb') as stream:
            stream.write(json.dumps({
                'native_physics_probe': {
                    'enabled': False, 'drive_seconds': 4.5,
                    'unknown': 1}}).encode('utf-8'))
        config, explicit = self.module.load_config(self.directory)
        self.assertTrue(explicit)
        self.assertFalse(config['enabled'])
        self.assertEqual(4.5, config['drive_seconds'])
        self.assertNotIn('unknown', config)
        config, explicit = self.module.load_config(
            os.path.join(self.directory, 'missing'))
        self.assertFalse(explicit)
        self.assertTrue(config['enabled'])

    def test_lsprof_windows_follow_worker_diagnostics(self):
        lsprof = _load('worker_lsprof')
        path = os.path.join(self.directory, 'worker_diagnostics.json')
        self.assertEqual(
            lsprof.DEFAULT_WINDOWS, lsprof.load_windows(self.directory))
        with open(path, 'wb') as stream:
            stream.write(json.dumps({
                'native_physics_probe': {'disable_lsprof': True}}).encode(
                    'utf-8'))
        self.assertEqual((), lsprof.load_windows(self.directory))
        with open(path, 'wb') as stream:
            stream.write(json.dumps({
                'lsprof': {'windows': [[5, 10], [40, 20]]}}).encode('utf-8'))
        self.assertEqual(
            ((5.0, 10.0), (40.0, 20.0)), lsprof.load_windows(self.directory))
        with open(path, 'wb') as stream:
            stream.write(json.dumps({
                'lsprof': {'windows': [[-1, 10]]}}).encode('utf-8'))
        self.assertEqual(
            lsprof.DEFAULT_WINDOWS, lsprof.load_windows(self.directory))


if __name__ == '__main__':
    unittest.main()
