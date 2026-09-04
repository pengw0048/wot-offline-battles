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
        self.mass = 45000.0
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
        self.callbacks = {}
        _FakePhysics.created.append(self)

    def __setattr__(self, name, value):
        if name.endswith('Cb') or name == 'onVehicleStatusChanged':
            self.__dict__.setdefault('callbacks', {})[name] = value
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
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

    def setArenaBounds(self, low, high):
        self.bounds = (low, high)

    def advance(self, dt):
        if self.movementSignals & 1 and not self.isFrozen:
            self.position[2] += 5.0 * dt
            self.distanceTraveled += 5.0 * dt
            self.speed = 5.0
            self.lastTickMatrix = _Matrix(*self.position)
            self.actualChassisTransform = _Matrix(*self.position)


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


class _FakeFilter(object):
    def __init__(self, physics):
        self._physics = physics
        self.inputs = []
        self.bodyMatrix = _Matrix(*physics.position)

    def getVehiclePhysics(self):
        return self._physics

    def notifyInputKeysDown(self, movement, rotation):
        self.inputs.append((movement, rotation))


class _FakeEntity(object):
    def __init__(self, physics):
        self.filter = _FakeFilter(physics)
        self.typeDescriptor = object()
        self.position = _Vector(*physics.position)
        self.matrix = _Matrix(*physics.position)


class _FakeHost(object):
    def __init__(self, bots, bigworld):
        self._bots = bots
        self._bigworld = bigworld

    def bigworld(self):
        return self._bigworld

    def math_module(self):
        return None

    def constants(self):
        return None

    def bot_entities(self):
        return list(self._bots)


def _fake_physics_shared():
    """A plain module: dir() of a module lists only its __dict__."""
    module = types.ModuleType('physics_shared')
    module.IS_CLIENT = True
    module.NUM_SUBSTEPS = 2

    def initVehiclePhysicsClient(physics, descriptor):
        physics.staticMode = False
        physics.initialised = True
    module.initVehiclePhysicsClient = initVehiclePhysicsClient
    return module


def by_name_last_step(report, name):
    for stage in report['stages']:
        if stage['name'] == name:
            return stage.get('last_step')
    return None


class NativePhysicsProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load('native_physics_probe')
        _FakePhysics.created = []
        self.lines = []
        self.clock = [1000.0]
        self.directory = tempfile.mkdtemp()
        self.simulator = _FakeSimulator()
        bigworld = types.SimpleNamespace(
            WGVehiclePhysics=_FakePhysics,
            WGDynamicsSimulator=lambda: self.simulator,
            WGPhysicalBody=_FakeBody)
        self.bots = []
        for index in range(3):
            physics = _FakePhysics()
            physics.position = [float(index * 10), 10.0, 0.0]
            self.bots.append({'bot_id': 11 + index,
                              'entity': _FakeEntity(physics)})
        self.host = _FakeHost(self.bots, bigworld)
        sys.modules['physics_shared'] = _fake_physics_shared()

    def tearDown(self):
        sys.modules.pop('physics_shared', None)

    def _probe(self, **config):
        base = {'start_delay_seconds': 0.5, 'passive_seconds': 0.2,
                'drive_seconds': 0.3, 'pair_seconds': 0.2,
                'scale_frames': 5,
                'opt_in_stages': ['construct_standalone', 'signatures'],
                'fresh_attribute_reads': True}
        base.update(config)
        return self.module.WorkerPhysicsProbe(
            self.host, self.directory, config=base,
            writer=self.lines.append, clock=lambda: self.clock[0],
            perf_clock=lambda: self.clock[0])

    def _run(self, probe, frames=200, dt=0.05):
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

    def test_all_stages_run_in_order_and_report_is_written(self):
        probe = self._probe()
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        names = [stage['name'] for stage in report['stages']]
        self.assertEqual(list(self.module.STAGE_ORDER), names)
        statuses = set(stage['status'] for stage in report['stages'])
        self.assertEqual({'ok'}, statuses, report['stages'])
        self.assertTrue(report['completed'])
        joined = ''.join(self.lines)
        self.assertIn('NPHYS stage=inventory begin', joined)
        self.assertIn('NPHYS stage=solve_scale end status=ok', joined)
        self.assertIn('NPHYS stage=construct_standalone step='
                      'BigWorld.WGVehiclePhysics()', joined)
        self.assertIn('NPHYS stage=solve_one step=simulator.update', joined)
        self.assertIn('NPHYS done', joined)
        self.assertEqual(
            'physics.setArenaBounds',
            by_name_last_step(report, 'construct_standalone'))
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        inventory = by_name['inventory']['data']
        self.assertIn('WGDynamicsSimulator', inventory['bigworld_names'])
        self.assertIn('initVehiclePhysicsClient',
                      inventory['physics_shared']['functions'])
        existing = by_name['inspect_existing']['data']
        self.assertEqual(3, existing['bot_count'])
        self.assertEqual('_FakePhysics', existing['entities'][0]['physics_type'])
        self.assertIn('mass', existing['entities'][0]['physics_attributes'])
        standalone = by_name['construct_standalone']['data']
        self.assertEqual(
            'ok', standalone['init'][0]['result'], standalone['init'])
        self.assertEqual('ok', standalone['setArenaBounds'])
        signatures = by_name['signatures']['data']
        self.assertIn('wrong arguments', signatures['physics']['configure'])
        self.assertIn('TypeError', signatures['simulator']['update'])
        self.assertEqual('<missing>', signatures['physics']['rollback'])
        solve_one = by_name['solve_one']['data']
        self.assertGreater(solve_one['moved_m'], 0.5)
        self.assertGreater(solve_one['update_ms']['calls'], 3)
        self.assertEqual(0, solve_one['stop']['movementSignals'])
        scale = by_name['solve_scale']['data']
        self.assertEqual(3, scale['body_count'])
        self.assertEqual(5, scale['update_ms']['calls'])
        passive = by_name['passive_drive']['data']
        self.assertEqual(0.0, passive['moved_m'])
        self.assertEqual([1, 0], passive['input']['notifyInputKeysDown'])
        self.assertEqual(
            [11, 12, 13][:2], by_name['solve_pair']['data']['bot_ids'])
        for bot in self.bots:
            physics = bot['entity'].filter.getVehiclePhysics()
            self.assertEqual(0, physics.movementSignals)
            if bot['bot_id'] in (11, 12):
                # Driven bodies end with an explicit zero input.
                self.assertEqual((0, 0), bot['entity'].filter.inputs[-1])
            else:
                # Undriven bodies are never touched.
                self.assertEqual([], bot['entity'].filter.inputs)

    def test_stage_exception_is_recorded_and_probe_continues(self):
        def broken(*unused):
            raise RuntimeError('simulator refused')
        self.simulator.update = broken
        probe = self._probe()
        self._run(probe)
        self.assertTrue(probe.done)
        report = self._report()
        by_name = dict((stage['name'], stage) for stage in report['stages'])
        self.assertEqual('error', by_name['solve_one']['status'])
        self.assertIn('simulator refused', by_name['solve_one']['error'])
        self.assertEqual('ok', by_name['restore']['status'])
        self.assertEqual('ok', by_name['inventory']['status'])
        for bot in self.bots:
            self.assertEqual(
                0, bot['entity'].filter.getVehiclePhysics().movementSignals)

    def test_report_records_last_begun_stage_before_native_calls(self):
        probe = self._probe(stages=['inventory', 'inspect_existing'])
        probe.tick(True, 1, 0.05)
        self.clock[0] += 1.0
        probe.tick(True, 1, 0.05)
        report = self._report()
        self.assertEqual('inventory', report['last_begun'])
        self.assertEqual(['inventory', 'inspect_existing', 'restore'],
                         probe._stages)

    def test_risky_stages_are_opt_in_and_ordered_last_by_default(self):
        probe = self.module.WorkerPhysicsProbe(
            self.host, self.directory, writer=self.lines.append,
            clock=lambda: self.clock[0])
        self.assertEqual(
            ['inventory', 'inspect_existing', 'passive_drive', 'solve_one',
             'solve_scale', 'solve_pair', 'restore'], probe._stages)
        self.assertFalse(probe._config['fresh_attribute_reads'])
        opted = self.module.WorkerPhysicsProbe(
            self.host, self.directory, writer=self.lines.append,
            clock=lambda: self.clock[0],
            config={'opt_in_stages': ['construct_standalone']})
        self.assertEqual('construct_standalone', opted._stages[-1])
        self.assertNotIn('signatures', opted._stages)

    def test_probe_waits_for_live_and_start_delay(self):
        probe = self._probe(start_delay_seconds=5.0)
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
