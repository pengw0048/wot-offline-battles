"""Direct native geometry dispatch, local fallback, and optional host conformance."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))
from gui.mods.offline_lan_0922 import tank_collision

MODULE_PATH = CLIENT_SCRIPTS / 'gui' / 'mods' / 'offline_lan_0922' / 'native_math.py'
METHODS = ('translation_fraction', 'slide_translation', 'rotation_fraction')


def _fresh_dispatch():
    spec = importlib.util.spec_from_file_location('_native_math_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, 'platform', 'linux'):
        spec.loader.exec_module(module)
    return module


def _body(identity, x=0.0, z=0.0, **changes):
    result = dict(id=identity, x=x, y=0.0, z=z, yaw=0.0,
                  shape=(1.5, 3.5, -0.8, 2.0))
    result.update(changes)
    return result


class NativeMathDispatchTests(unittest.TestCase):
    def setUp(self):
        self.native = _fresh_dispatch()
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_non_windows_does_not_attempt_loading(self):
        loader = mock.Mock()
        with mock.patch.dict(sys.modules, {'imp': types.SimpleNamespace(load_dynamic=loader)}):
            self.assertIsNone(self.native.call('translation_fraction', {}, (), []))
        loader.assert_not_called()
        self.assertEqual(self.native.snapshot(), dict(
            translation_fraction=0, slide_translation=0, rotation_fraction=0,
            contact_roster=0, world_run=0, world_failures=0,
            loaded=False, fallbacks=0))

    def test_windows_loads_once_from_the_client_executable_directory(self):
        backend = types.SimpleNamespace(translation_fraction=mock.Mock(return_value=0.0))
        loader = mock.Mock(return_value=backend)
        self.native._attempted = False
        with mock.patch.object(sys, 'executable', os.path.join('client', 'WorldOfTanks.exe')), \
                mock.patch.dict(sys.modules, {'imp': types.SimpleNamespace(load_dynamic=loader)}):
            self.assertEqual(self.native.call('translation_fraction', {}, (), []), 0.0)
            self.assertEqual(self.native.call('translation_fraction', {}, (), []), 0.0)
            self.assertIs(sys.modules[self.native.MODULE_NAME], backend)
        loader.assert_called_once_with(self.native.MODULE_NAME, os.path.join(
            os.path.abspath('client'), 'mods', '0.9.22.0.1', self.native.MODULE_NAME + '.pyd'))
        self.assertEqual(self.native.snapshot()['translation_fraction'], 2)
        self.assertEqual(self.output.getvalue().count('active:'), 1)

    def test_failed_load_is_contained_and_not_retried(self):
        loader = mock.Mock(side_effect=ImportError('not available'))
        self.native._attempted = False
        with mock.patch.dict(sys.modules, {'imp': types.SimpleNamespace(load_dynamic=loader)}):
            for method in METHODS:
                self.assertIsNone(self.native.call(method))
        loader.assert_called_once()
        self.assertEqual(self.output.getvalue().count('unavailable'), 1)
        self.assertFalse(self.native.snapshot()['loaded'])

    def test_passes_live_objects_and_returns_the_owned_result_unchanged(self):
        body, movement, peers = _body(1), [2.0, 3.0], [_body(2, z=20.0)]
        result = (1.25, 2.5)
        operation = mock.Mock(return_value=result)
        self.native._backend = types.SimpleNamespace(slide_translation=operation)
        self.assertIs(self.native.call('slide_translation', body, movement, peers, 0.25), result)
        args = operation.call_args.args
        self.assertIs(args[0], body)
        self.assertIs(args[1], movement)
        self.assertIs(args[2], peers)
        body['yaw'] = 0.5
        peers[0]['z'] = 4.0
        self.native.call('slide_translation', body, movement, peers, None)
        self.assertEqual(operation.call_args.args[0]['yaw'], 0.5)
        self.assertEqual(operation.call_args.args[2][0]['z'], 4.0)

    def test_none_and_exception_fall_back_only_the_current_operation(self):
        self.native._backend = types.SimpleNamespace(
            translation_fraction=mock.Mock(side_effect=[None, 0.75]),
            slide_translation=mock.Mock(side_effect=ValueError('unsupported')),
            rotation_fraction=mock.Mock(return_value=0.5))
        self.assertEqual(self.native.call('rotation_fraction'), 0.5)
        self.assertIsNone(self.native.call('translation_fraction'))
        self.assertIsNone(self.native.call('slide_translation'))
        self.assertEqual(self.native.call('translation_fraction'), 0.75)
        self.assertEqual(self.native.snapshot(), dict(
            translation_fraction=1, slide_translation=0, rotation_fraction=1,
            contact_roster=0, world_run=0, world_failures=0,
            loaded=True, fallbacks=2))
        self.assertEqual(self.output.getvalue().count('unavailable'), 1)

    def test_missing_backend_method_is_a_local_fallback(self):
        self.native._backend = types.SimpleNamespace(rotation_fraction=lambda: 1.0)
        self.assertIsNone(self.native.call('slide_translation'))
        self.assertEqual(self.native.call('rotation_fraction'), 1.0)
        self.assertEqual(self.native.snapshot()['fallbacks'], 1)

    def test_snapshot_cannot_mutate_internal_counters(self):
        snapshot = self.native.snapshot()
        snapshot['translation_fraction'] = 999
        snapshot['loaded'] = True
        self.assertEqual(self.native.snapshot()['translation_fraction'], 0)
        self.assertFalse(self.native.snapshot()['loaded'])

    def test_public_geometry_keeps_python_law_on_none_and_exception(self):
        owner, peers = _body(1), [_body(2, 3.0), _body(3, z=12.0)]
        operations = (
            lambda: tank_collision.translation_fraction(owner, (8.0, 8.0), peers),
            lambda: tank_collision.slide_translation(owner, (8.0, 8.0), peers),
            lambda: tank_collision.rotation_fraction(
                (0.0, 0.0, 0.0), 0.0, 0.5, owner['shape'], peers))
        with mock.patch.object(tank_collision, 'native_math', self.native):
            expected = [operation() for operation in operations]
            for failure in (None, ValueError('operation failed')):
                backend = types.SimpleNamespace()
                for method in METHODS:
                    setattr(backend, method, mock.Mock(
                        side_effect=failure if isinstance(failure, Exception) else None,
                        return_value=None))
                self.native._backend = backend
                self.assertEqual([operation() for operation in operations], expected)
        self.assertEqual([self.native.snapshot()[method] for method in METHODS], [0, 0, 0])
        self.assertGreaterEqual(self.native.snapshot()['fallbacks'], 6)

    def test_public_fast_paths_do_not_dispatch(self):
        owner = _body(1)
        with mock.patch.object(tank_collision.native_math, 'call') as call:
            self.assertEqual(tank_collision.translation_fraction(owner, (0.0, 0.0), []), 1.0)
            self.assertEqual(tank_collision.rotation_fraction(
                (0.0, 0.0, 0.0), 0.0, 0.0, owner['shape'], []), 1.0)
            self.assertEqual(tank_collision.slide_translation(
                owner, (4.0, 5.0), [], first_fraction=1.0), (4.0, 5.0))
            self.assertEqual(tank_collision.slide_translation(owner, (0.0, 0.0), []), (0.0, 0.0))
        call.assert_not_called()


# The extension uses the supported Python 2 ABI. Run the optional conformance
# under that interpreter, rather than loading it into the Python 3 test runner.


@unittest.skipUnless(os.environ.get('WOT_NATIVE_MATH_EXTENSION'),
                     'Set WOT_NATIVE_MATH_EXTENSION for optional Python 2 host conformance')
class NativeMathHostConformanceTests(unittest.TestCase):
    def test_representative_direct_calls_and_borrowed_reference_lifetime(self):
        interpreter = os.environ.get('WOT_NATIVE_MATH_PYTHON27') or shutil.which('python2.7')
        self.assertIsNotNone(interpreter, 'Set WOT_NATIVE_MATH_PYTHON27 to the Python 2 host interpreter')
        completed = subprocess.run(
            [interpreter, str(ROOT / 'tools' / 'check_native_math.py'),
             os.environ['WOT_NATIVE_MATH_EXTENSION'],
             str(CLIENT_SCRIPTS)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn('conformance passed:', completed.stdout)


@unittest.skipUnless(os.environ.get('WOT_0922_CLIENT'),
                     'Set WOT_0922_CLIENT for the exact #1513 native layout audit')
class NativeMathExactClientLayoutTests(unittest.TestCase):
    def test_registered_api_signatures_and_plain_value_layouts_match_exact_client(self):
        from test_port_0922_native_instance_guard import _PeImage

        image = _PeImage((Path(os.environ['WOT_0922_CLIENT']) /
                          'WorldOfTanks.exe').read_bytes())
        self.assertEqual(0x014c, image.machine)
        self.assertEqual(0x010b, image.optional_magic)
        self.assertEqual(0x5a6edca4, image.timestamp)
        self.assertFalse(any(name.startswith(('Py', '_Py')) for name in image.exports()))
        source = (ROOT / 'native/offline_math_batch_native.cpp').read_text()
        signatures = {
            name: bytes(int(value.strip(), 16) for value in values.split(','))
            for name, values in re.findall(
                r'static const unsigned char (\w+)\[\] = \{([^}]+)\};', source)}
        checked = set()
        for address, name in re.findall(
                r'signature\(base \+ (0x[0-9a-fA-F]+)U, (\w+)\)', source):
            expected = signatures[name]
            offset = image.rva_offset(int(address, 16))
            self.assertEqual(expected, image.data[offset:offset + len(expected)], name)
            checked.add(name)
        self.assertTrue({
            'string_layout_bytes', 'unicode_layout_bytes', 'unicode_unit_bytes',
            'long_layout_bytes', 'long_digit_bytes', 'dict_layout_bytes',
            'dict_entry_bytes', 'dict_value_bytes', 'dict_used_bytes',
        }.issubset(checked))
        layouts = re.findall(
            r'type_layout\(base, (0x[0-9a-fA-F]+)U, (\d+), (\d+)\)', source)
        self.assertIn(('0x01661400', '21', '1'), layouts)
        self.assertIn(('0x0166b290', '24', '0'), layouts)
        self.assertIn(('0x0166c7c8', '12', '2'), layouts)
        self.assertIn(('0x01664d30', '124', '0'), layouts)
        for address, basicsize, itemsize in layouts:
            offset = image.rva_offset(int(address, 16))
            self.assertEqual(0x01a5ff18, struct.unpack_from('<I', image.data, offset + 4)[0])
            self.assertEqual((int(basicsize), int(itemsize)),
                             struct.unpack_from('<ii', image.data, offset + 16))


if __name__ == '__main__':
    unittest.main()
