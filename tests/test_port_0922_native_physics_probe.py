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
    def __init__(self, x, y, z):
        self.translation = _Vector(x, y, z)
        self.yaw = 0.0


class _FakePhysics(object):
    """Mimic the exe's WGVehiclePhysics Python surface closely enough."""

    created = []

    def __init__(self):
        self.__dict__['callbacks'] = {}
        self.__dict__['initialised'] = False
        self.staticMode = True
        self.isFrozen = True
        self.movementSignals = 0
        self.speed = 0.0
        self.gotTracksContact = True
        self.groundType = 1
        self.distanceTraveled = 0.0
        self.position = [0.0, 10.0, 0.0]
        self.lastTickMatrix = _Matrix(*self.position)
        self.actualChassisTransform = _Matrix(*self.position)
        _FakePhysics.created.append(self)

    def __setattr__(self, name, value):
        if name.endswith('Cb') or name == 'onVehicleStatusChanged':
            self.__dict__['callbacks'][name] = value
            return
        if name == 'lastTickMatrix' and hasattr(value, 'translation') and \
                self.__dict__.get('initialised'):
            # A retail-like pose setter: seeding through the matrix moves it.
            self.__dict__['position'] = _xyz_list(value.translation)
            self.__dict__['actualChassisTransform'] = _Matrix(*self.position)
        object.__setattr__(self, name, value)

    reject_configure = False

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

    def getTouchedGround(self):
        return True

    def getAggressiveImpacts(self):
        return []

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
        if self.movementSignals & 1 and not self.isFrozen:
            self.position[2] += 5.0 * dt
            self.distanceTraveled += 5.0 * dt
            self.speed = 5.0
            self.__dict__['lastTickMatrix'] = _Matrix(*self.position)
            self.__dict__['actualChassisTransform'] = _Matrix(*self.position)


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
        self.updates.append((dt, len(physics)))
        for item in physics:
            item.advance(dt)


class _FakeBody(object):
    def __init__(self):
        self.isCollidingWithWorld = False

    def setup(self, *args):
        if not args:
            raise TypeError('WGPhysicalBody::setup: wrong arguments')


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
        matrix.setRotateYPR = lambda ypr: None
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
            player=lambda: types.SimpleNamespace(arenaTypeID=(5 << 16) | 7),
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
                'drive_seconds': 0.3, 'pair_seconds': 0.2,
                'scale_frames': 5, 'scale_bodies': 3}
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
             'passive_drive', 'solve_one', 'solve_scale', 'solve_pair',
             'restore'], probe._stages)
        opted = self._probe(self._bots(retail=True),
                            opt_in_stages=['signatures'])
        self.assertEqual('signatures', opted._stages[-1])

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
        self.assertEqual([1, 0], solve_one['input']['notifyInputKeysDown'])
        scale = by_name['solve_scale']['data']
        self.assertEqual(3, scale['body_count'])
        self.assertEqual(5, scale['update_ms']['calls'])
        for bot in bots:
            physics = bot['native'].filter.getVehiclePhysics()
            self.assertEqual(0, physics.movementSignals)
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
        self.assertNotIn('notifyInputKeysDown', solve_one['input'])
        scale = by_name['solve_scale']['data']
        self.assertEqual(3, scale['body_count'])
        self.assertEqual(3, len(report['standalone_bodies']))
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
