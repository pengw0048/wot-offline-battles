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

    def __getattr__(self, name):
        if name == 'mass':
            if not self.__dict__.get('initialised'):
                raise RuntimeError('simulated native crash: mass before init')
            return 45000.0
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
        if not args:
            raise TypeError('WGVehiclePhysics::configure: wrong arguments.')

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


class _FakeSimulator(object):
    def __init__(self):
        self.numSubsteps = 2
        self.numIterations = 10
        self.updates = []

    def update(self, dt, physics, bodies):
        if not isinstance(physics, list):
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


class _FakeNativeEntity(object):
    def __init__(self, physics, position):
        self.filter = _FakeNativeFilter(physics)
        self.typeDescriptor = object()
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

    def initVehiclePhysicsServer(physics, descriptor):
        physics.staticMode = False
        physics.__dict__['initialised'] = True

    def initVehiclePhysicsClient(physics, descriptor):
        raise RuntimeError('client init should not be reached first')
    module.initVehiclePhysicsServer = initVehiclePhysicsServer
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
        self.bigworld = types.SimpleNamespace(
            WGVehiclePhysics=_FakePhysics,
            WGDynamicsSimulator=lambda: self.simulator,
            WGPhysicalBody=_FakeBody)
        sys.modules['physics_shared'] = _fake_physics_shared()

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
                'descriptor': object(), 'position': position, 'yaw': 0.3})
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

    def test_retail_bodies_are_preferred_and_driven(self):
        bots = self._bots(retail=True)
        probe = self._probe(bots, opt_in_stages=['signatures'])
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
        self.assertEqual('initVehiclePhysicsServer', construct['init'][0]['call'])
        self.assertEqual(
            45000.0, construct['physics_attributes_initialised']['mass'])
        self.assertNotIn('physics_attributes_fresh', construct)
        self.assertEqual('lastTickMatrix = Matrix', construct['seed']['result'])
        joined = ''.join(self.lines)
        self.assertIn('step=construct BigWorld.WGVehiclePhysics()', joined)
        self.assertIn('step=construct physics_shared.initVehiclePhysicsServer',
                      joined)
        self.assertIn('stage=signatures end status=ok', joined)
        signatures = by_name['signatures']['data']
        self.assertIn('wrong arguments', signatures['physics']['rollback'])

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
        for body in report['standalone_bodies']:
            self.assertTrue(body['initialised'])
            self.assertEqual('_FakeNativeEntity', body['owner'])
            self.assertEqual('lastTickMatrix = Matrix', body['seed']['result'])
        self.assertEqual(
            ['standalone:11', 'standalone:12'],
            by_name['solve_pair']['data']['labels'])
        self.assertEqual(2, report['simulator_settings']['numSubsteps'])

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
