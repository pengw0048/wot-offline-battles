"""Differential and contract tests for the native compute experiment."""

import ctypes
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import (  # noqa: E402
    destructibles_sensor, native_compute, native_compute_bridge,
    world_collision, world_collision_prep)


CORE_SOURCE = ROOT / 'native' / 'offline_compute_core.c'
# The extension is built with these flags; the differential build must use
# them too, or a fused multiply-add would silently change the last places.
CORE_FLAGS = ('-std=c99', '-O2', '-ffp-contract=off')


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


def _descriptor():
    bbox = ((-1.6, -0.5, -3.2), (1.6, 1.6, 3.4))
    hit_tester = types.SimpleNamespace(bbox=bbox)
    return types.SimpleNamespace(hull=types.SimpleNamespace(
        hitTester=hit_tester))


def _terrain(x, z):
    cell_x = int(math.floor(x / 4.0))
    cell_z = int(math.floor(z / 4.0))
    return 0.06 * x + 0.04 * z + ((cell_x * 37 + cell_z * 17) % 5) * 0.12


def _scene():
    """Return a BigWorld stand-in that records every query it is given."""
    calls = []

    def collide(space, start, end, mask, collision_filter=None):
        calls.append((space, mask, collision_filter is not None,
                      struct.pack('<6d', start.x, start.y, start.z,
                                  end.x, end.y, end.z)))
        if abs(start.x - end.x) < 1.0e-9 and abs(start.z - end.z) < 1.0e-9:
            height = _terrain(start.x, start.z)
            if height > start.y or height < end.y:
                return None
            return (_Vector(start.x, height, start.z),
                    _Vector(0.0, 1.0, 0.0), 0)
        wall_z = 21.0
        if (start.z - wall_z) * (end.z - wall_z) > 0.0 or start.y > 2.5:
            return None
        span = end.z - start.z
        if abs(span) < 1.0e-9:
            return None
        fraction = (wall_z - start.z) / span
        return (_Vector(start.x + (end.x - start.x) * fraction,
                        start.y + (end.y - start.y) * fraction, wall_z),
                _Vector(0.0, 0.0, -1.0), 0)

    area = types.ModuleType('AreaDestructibles')
    area.g_destructiblesManager = object()
    area.DESTR_TYPE_TREE = 1
    area.DESTR_TYPE_FALLING_ATOM = 2
    area.DESTR_TYPE_FRAGILE = 3
    area.DESTR_TYPE_STRUCTURE = 4
    area.g_cache = types.SimpleNamespace(
        unitVehicleMass=10000.0,
        getDescByFilename=lambda unused_filename: None)
    destructibles = types.ModuleType('DestructiblesCache')
    destructibles.scaledDestructibleHealth = (
        lambda scale, health: scale * health)
    sys.modules['AreaDestructibles'] = area
    sys.modules['DestructiblesCache'] = destructibles
    bigworld = types.ModuleType('BigWorld')
    bigworld.wg_collideSegment = collide
    bigworld.wg_getMatInfoNearPoint = lambda *unused: (
        False, _Vector(), _Vector(), 0, '', 0, 0)
    math_module = types.ModuleType('Math')
    math_module.Vector3 = _Vector
    return bigworld, math_module, calls


def _sweep_cases():
    """Level, pitched, rolled, reverse, turning and airborne sweeps."""
    cases = []
    for index in range(16):
        yaw = index * 0.3926990816987241
        cases.append({
            'pos': _Vector(2.0 + 0.9 * index, 1.3, 6.0 + 1.1 * index),
            'yaw': yaw,
            'vel': 8.0 if index % 3 else -5.0,
            'airborne': index % 7 == 6,
            'pitch': 0.0 if index % 3 else 0.13,
            'roll': 0.0 if index % 4 else -0.09,
            'motion_yaw': None if index % 2 else yaw + 0.4,
        })
    return cases


def _run_sweep(bigworld, math_module, case):
    return world_collision.check_horizontal_collision(
        bigworld, math_module, 1, case['pos'], case['yaw'], case['vel'],
        _descriptor(), case['airborne'], 0.04, True, False, None, True,
        case['motion_yaw'], case['pitch'], case['roll'])


def _prepare_inputs(seed_index):
    """Return one deterministic input vector for the batch."""
    angle = 0.37 * seed_index
    yaw = math.sin(angle) * math.pi
    pitch = 0.0 if seed_index % 3 == 0 else 0.21 * math.cos(angle)
    roll = 0.0 if seed_index % 4 == 0 else -0.17 * math.sin(angle * 1.7)
    if pitch == 0.0 and roll == 0.0:
        pose = (0.0, 1.0, 0.0)
    else:
        pose = (math.cos(pitch) * math.sin(roll),
                math.cos(pitch) * math.cos(roll), -math.sin(pitch))
    motion = None if seed_index % 2 else yaw + 0.9 * math.cos(angle)
    return (
        13.5 * math.cos(angle), 4.0 + 0.5 * seed_index, -22.0 * math.sin(angle),
        math.sin(yaw), math.cos(yaw), pose[0], pose[1], pose[2],
        1.1 + 0.07 * (seed_index % 9), 2.0 + 0.11 * (seed_index % 7),
        2.4 + 0.13 * (seed_index % 5),
        0.0 if seed_index % 11 == 0 else 17.0 * math.cos(angle * 0.7),
        0.04, float(seed_index % 5 == 4),
        0.0 if motion is None else math.sin(motion),
        0.0 if motion is None else math.cos(motion),
        0.0 if motion is None else 1.0)


def _boundary_inputs():
    """Cases that sit exactly on a branch the batch must not round away."""
    epsilon = 1.0e-7
    return (
        # Stationary, level, no motion yaw: the shipped three-lane geometry.
        (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.5, 3.0, 3.0,
         0.0, 0.04, 0.0, 0.0, 0.0, 0.0),
        # Reverse at the sign boundary of the direction and margin choices.
        (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.5, 3.0, 3.0,
         -0.0, 0.04, 0.0, 0.0, 0.0, 0.0),
        # Motion exactly along the hull: every corner projects onto one lane.
        (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.5, 3.0, 3.0,
         6.0, 0.04, 0.0, 0.0, 1.0, 1.0),
        # Two lanes separated by exactly the merge epsilon.
        (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, epsilon, 3.0, 3.0,
         6.0, 0.04, 0.0, 1.0, 0.0, 1.0),
        # Motion perpendicular to the hull, where the centre limit is chosen.
        (0.0, 0.0, 0.0, 1.0, 0.0, 0.02, 0.999, -0.04, 1.5, 3.0, 3.0,
         -9.0, 0.04, 1.0, 1.0, 0.0, 1.0),
    )


def _flatten(plan):
    return world_collision_prep.plan_values(plan)


class BackendSelectionTests(unittest.TestCase):

    def tearDown(self):
        native_compute.select_backend('python', environ={})

    def test_default_backend_is_the_unchanged_inline_path(self):
        self.assertEqual('python', native_compute.select_backend(
            environ={}, backend=None))
        self.assertIsNone(native_compute.preparation())

    def test_environment_selects_the_batch_backend(self):
        environ = {native_compute.BACKEND_ENVIRONMENT: ' Batch '}
        self.assertEqual('batch', native_compute.select_backend(
            environ=environ))
        self.assertIsInstance(
            native_compute.preparation(),
            world_collision_prep.PythonPreparation)

    def test_unknown_backend_records_the_reason_and_stays_inline(self):
        environ = {native_compute.BACKEND_ENVIRONMENT: 'rust'}
        self.assertEqual('python', native_compute.select_backend(
            environ=environ))
        status = native_compute.status()
        self.assertEqual('rust', status['requested'])
        self.assertIn('unknown compute backend', status['error'])

    def test_failed_native_load_cannot_be_reported_as_native(self):
        def loader():
            raise ImportError('native compute bridge is missing')

        self.assertEqual('python', native_compute.select_backend(
            'native', environ={}, loader=loader))
        status = native_compute.status()
        self.assertEqual('native', status['requested'])
        self.assertEqual('python', status['backend'])
        self.assertIn('ImportError', status['error'])
        self.assertIsNone(native_compute.preparation())

    def test_demotion_relabels_the_run_and_keeps_preparing(self):
        module = types.SimpleNamespace()
        native_compute.select_backend(
            'native', environ={}, loader=lambda: module)
        self.assertEqual('native', native_compute.selected_backend())
        native_compute.demote('NativeComputeError(2)')
        self.assertEqual('batch', native_compute.selected_backend())
        self.assertEqual(
            1, native_compute.status()['counts']['native_failures'])
        self.assertIsInstance(
            native_compute.preparation(),
            world_collision_prep.PythonPreparation)


class BridgeValidationTests(unittest.TestCase):

    @staticmethod
    def _module(**overrides):
        values = {
            'layout_self_test': lambda *args: (
                args[0] * 10000 + args[1] * 100 + args[2]),
            'prepare_sweep': lambda *args: 0,
            'buffer_values': lambda: world_collision_prep.BUFFER_VALUES,
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def test_validated_module_is_returned(self):
        module = self._module()
        self.assertIs(module, native_compute_bridge.validate(module))

    def test_missing_method_is_refused(self):
        module = self._module()
        del module.prepare_sweep
        with self.assertRaises(ImportError):
            native_compute_bridge.validate(module)

    def test_unproven_layout_is_refused(self):
        module = self._module(layout_self_test=lambda *args: -15)
        with self.assertRaises(ImportError) as caught:
            native_compute_bridge.validate(module)
        self.assertIn('layout self-test', str(caught.exception))

    def test_buffer_size_disagreement_is_refused(self):
        module = self._module(buffer_values=lambda: 4)
        with self.assertRaises(ImportError) as caught:
            native_compute_bridge.validate(module)
        self.assertIn('buffer values', str(caught.exception))


class SweepParityTests(unittest.TestCase):
    """The batch must reproduce the inline sweep query for query."""

    def setUp(self):
        destructibles_sensor.set_diagnostics(False)
        native_compute.select_backend('python', environ={})

    def tearDown(self):
        native_compute.select_backend('python', environ={})
        destructibles_sensor.set_catalog(None)
        destructibles_sensor.set_diagnostics(False)

    def _record(self, backend):
        native_compute.select_backend(backend, environ={})
        self.assertEqual(backend, native_compute.selected_backend())
        bigworld, math_module, calls = _scene()
        verdicts = [_run_sweep(bigworld, math_module, case)
                    for case in _sweep_cases()]
        return verdicts, calls

    def test_batch_backend_issues_the_same_queries_with_the_same_arguments(
            self):
        inline_verdicts, inline_calls = self._record('python')
        batch_verdicts, batch_calls = self._record('batch')
        self.assertEqual(inline_verdicts, batch_verdicts)
        self.assertEqual(len(inline_calls), len(batch_calls))
        # The recorded endpoints are raw doubles, so an identical list is an
        # exact geometric match, not a rounded one.
        self.assertEqual(inline_calls, batch_calls)

    def test_batch_backend_records_its_own_work(self):
        self._record('batch')
        counts = native_compute.status()['counts']
        self.assertEqual(len(_sweep_cases()), counts['prepare_calls'])
        self.assertGreaterEqual(
            counts['prepare_lanes'], 3 * len(_sweep_cases()))

    def test_a_failing_backend_recovers_through_the_inline_path(self):
        inline_verdicts, inline_calls = self._record('python')

        class _Failing(object):
            name = 'native'

            def prepare_sweep(self, *unused_values):
                raise world_collision_prep.NativeComputeError(2)

        native_compute.select_backend(
            'native', environ={}, loader=lambda: types.SimpleNamespace())
        with mock.patch.object(native_compute, '_preparation', _Failing()):
            bigworld, math_module, calls = _scene()
            verdicts = [_run_sweep(bigworld, math_module, case)
                        for case in _sweep_cases()]
        self.assertEqual(inline_verdicts, verdicts)
        self.assertEqual(inline_calls, calls)
        # The run may not keep calling itself native afterwards, and the
        # first failure retires the failing backend instead of retrying it
        # once per sweep.
        self.assertEqual('batch', native_compute.selected_backend())
        self.assertEqual(
            1, native_compute.status()['counts']['native_failures'])


def _compile_core():
    """Build the shipped core for this host, or return None without a cc."""
    compiler = os.environ.get('CC', 'cc')
    directory = tempfile.mkdtemp(prefix='offline-compute-core-')
    library = os.path.join(directory, 'core.so')
    command = [compiler, '-shared', '-fPIC'] + list(CORE_FLAGS) + [
        '-o', library, str(CORE_SOURCE)]
    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return None
    return library


class ShadowComparisonTests(unittest.TestCase):
    """The shadow mode reports a disagreement instead of hiding it."""

    class _Module(object):
        __file__ = 'shadow'

        def __init__(self, corrupt=False):
            self.corrupt = corrupt

        def prepare_sweep(self, low, high, count):
            del low, high, count
            return 0

    def setUp(self):
        native_compute.select_backend('python', environ={})

    def tearDown(self):
        native_compute.select_backend('python', environ={})

    def _shadow(self, native_plan_source):
        shadow = world_collision_prep.ShadowPreparation(
            types.SimpleNamespace(prepare_sweep=native_plan_source))
        return shadow

    def test_agreeing_backends_report_nothing(self):
        native_compute.select_backend(
            'native-shadow', environ={}, loader=lambda: self._Module())
        shadow = self._shadow(world_collision_prep.prepare_sweep)
        values = _prepare_inputs(5)
        self.assertEqual(
            _flatten(world_collision_prep.prepare_sweep(*values)),
            _flatten(shadow.prepare_sweep(*values)))
        self.assertNotIn(
            'shadow_mismatches', native_compute.status()['counts'])

    def test_a_last_place_disagreement_is_counted(self):
        native_compute.select_backend(
            'native-shadow', environ={}, loader=lambda: self._Module())

        def corrupt(*values):
            plan = world_collision_prep.prepare_sweep(*values)
            lanes = [list(lane) for lane in plan[3]]
            lanes[0][0] = math.nextafter(lanes[0][0], 1.0e9)
            return (plan[0], plan[1], plan[2], lanes)

        shadow = self._shadow(corrupt)
        values = _prepare_inputs(6)
        # The Python batch still owns behaviour, so the sweep is unchanged.
        self.assertEqual(
            _flatten(world_collision_prep.prepare_sweep(*values)),
            _flatten(shadow.prepare_sweep(*values)))
        self.assertEqual(
            1, native_compute.status()['counts']['shadow_mismatches'])


class CoreDifferentialTests(unittest.TestCase):
    """The shipped C core and the Python reference must agree exactly."""

    library = None

    @classmethod
    def setUpClass(cls):
        cls.library = _compile_core()
        if cls.library is None:
            raise unittest.SkipTest('no C compiler for the differential build')
        cls.core = ctypes.CDLL(cls.library)
        cls.core.offline_compute_prepare_sweep.restype = ctypes.c_int
        cls.core.offline_compute_prepare_sweep.argtypes = (
            ctypes.POINTER(ctypes.c_double), ctypes.c_int)
        cls.buffer = (ctypes.c_double * world_collision_prep.BUFFER_VALUES)()

    def _native_plan(self, values):
        for index, value in enumerate(values):
            self.buffer[index] = value
        status = self.core.offline_compute_prepare_sweep(
            self.buffer, world_collision_prep.BUFFER_VALUES)
        self.assertEqual(0, status)
        base = world_collision_prep.INPUT_VALUES
        lane_count = int(self.buffer[base + 10])
        lanes = []
        offset = base + world_collision_prep.HEADER_VALUES
        for unused_index in range(lane_count):
            lanes.append(list(
                self.buffer[offset:offset + world_collision_prep.LANE_VALUES]))
            offset += world_collision_prep.LANE_VALUES
        return (self.buffer[base],
                tuple(self.buffer[base + 1:base + 6]),
                tuple(self.buffer[base + 6:base + 10]), lanes)

    def _assert_identical(self, values):
        expected = world_collision_prep.prepare_sweep(*values)
        actual = self._native_plan(values)
        self.assertEqual(len(expected[3]), len(actual[3]), values)
        for index, (left, right) in enumerate(
                zip(_flatten(expected), _flatten(actual))):
            # Compare the doubles themselves: a decision threshold must not be
            # crossed by a tolerance chosen for convenience.
            self.assertEqual(struct.pack('<d', left), struct.pack('<d', right),
                             'value %d of %r' % (index, values))

    def test_representative_inputs_agree_to_the_last_bit(self):
        for seed_index in range(400):
            self._assert_identical(_prepare_inputs(seed_index))

    def test_boundary_inputs_agree_to_the_last_bit(self):
        for values in _boundary_inputs():
            self._assert_identical(values)

    def test_a_short_buffer_is_refused_without_writing(self):
        buffer_values = (ctypes.c_double * 8)()
        status = self.core.offline_compute_prepare_sweep(buffer_values, 8)
        self.assertEqual(1, status)
        self.assertEqual([0.0] * 8, list(buffer_values))

    def test_a_non_finite_input_is_refused_without_writing(self):
        values = list(_prepare_inputs(3))
        values[1] = float('nan')
        for index, value in enumerate(values):
            self.buffer[index] = value
        marker = 12345.0
        self.buffer[world_collision_prep.INPUT_VALUES] = marker
        status = self.core.offline_compute_prepare_sweep(
            self.buffer, world_collision_prep.BUFFER_VALUES)
        self.assertEqual(2, status)
        self.assertEqual(
            marker, self.buffer[world_collision_prep.INPUT_VALUES])


class NativePreparationTests(unittest.TestCase):
    """The buffer contract the extension is given, without the extension."""

    class _Module(object):
        def __init__(self, core, library):
            self.core = core
            self.__file__ = library

        def prepare_sweep(self, low, high, count):
            address = (int(high) << 16) | int(low)
            return self.core.offline_compute_prepare_sweep(
                ctypes.cast(address, ctypes.POINTER(ctypes.c_double)),
                int(count))

    @classmethod
    def setUpClass(cls):
        cls.library = _compile_core()
        if cls.library is None:
            raise unittest.SkipTest('no C compiler for the differential build')
        cls.core = ctypes.CDLL(cls.library)
        cls.core.offline_compute_prepare_sweep.restype = ctypes.c_int

    def test_split_address_reaches_the_same_buffer(self):
        preparation = world_collision_prep.native_preparation(
            self._Module(self.core, self.library))
        for seed_index in range(24):
            values = _prepare_inputs(seed_index)
            self.assertEqual(
                _flatten(world_collision_prep.prepare_sweep(*values)),
                _flatten(preparation.prepare_sweep(*values)))

    def test_a_refused_preparation_raises_with_its_status(self):
        class _Refusing(object):
            __file__ = 'refusing'

            @staticmethod
            def prepare_sweep(unused_low, unused_high, unused_count):
                return 2

        preparation = world_collision_prep.native_preparation(_Refusing())
        with self.assertRaises(world_collision_prep.NativeComputeError) as bad:
            preparation.prepare_sweep(*_prepare_inputs(1))
        self.assertEqual(2, bad.exception.status)


if __name__ == '__main__':
    unittest.main()
