import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
    'mods' / 'offline_lan_0922' / 'instance_guard.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'test_offline_lan_0922_instance_guard', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NativeBridge(object):
    def __init__(self, release_status=0, hide_result=1, show_result=1,
                 events=None, atmosphere_status=0):
        self.release_status = release_status
        self.hide_result = hide_result
        self.show_result = show_result
        self.events = [] if events is None else events
        self.atmosphere_status = atmosphere_status

    def install_atmosphere_owner_guard(self):
        self.events.append('install_atmosphere_owner_guard')
        return self.atmosphere_status

    def release_client_guard(self):
        self.events.append('release_client_guard')
        return self.release_status

    def hide_process_windows(self):
        self.events.append('hide_process_windows')
        return self.hide_result

    def show_process_windows(self):
        self.events.append('show_process_windows')
        return self.show_result


class _TrailBridge(_NativeBridge):
    def __init__(self, trail_status=0, **keywords):
        _NativeBridge.__init__(self, **keywords)
        self.trail_status = trail_status

    def install_exception_trail(self):
        self.events.append('install_exception_trail')
        if isinstance(self.trail_status, Exception):
            raise self.trail_status
        return self.trail_status


def _load_bridge(module, bridge):
    class ImpModule(object):
        def load_dynamic(self, name, path):
            sys.modules[name] = bridge
            return bridge

    output = io.StringIO()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / module.NATIVE_FILENAME
        path.write_bytes(b'PE sidecar')
        try:
            with contextlib.redirect_stdout(output):
                loaded = module._load_native_bridge(
                    path=str(path), imp_module=ImpModule())
        finally:
            sys.modules.pop(module.NATIVE_MODULE_NAME, None)
            module._native_bridge = None
    return loaded, output.getvalue()


class ExceptionTrailTests(unittest.TestCase):
    """The trail records faults #1513's own reporter consumes, so it must
    never be able to stop the game from starting."""

    def setUp(self):
        self.module = _load_module()

    def test_trail_is_installed_after_the_atmosphere_guard(self):
        bridge = _TrailBridge()

        loaded, output = _load_bridge(self.module, bridge)

        self.assertIs(bridge, loaded)
        self.assertEqual(
            ['install_atmosphere_owner_guard', 'install_exception_trail'],
            bridge.events)
        self.assertIn('installed #1513 exception trail', output)

    def test_unconfigured_trail_is_silent_and_still_publishes(self):
        bridge = _TrailBridge(
            trail_status=self.module.TRAIL_STATUS_NOT_CONFIGURED)

        loaded, output = _load_bridge(self.module, bridge)

        self.assertIs(bridge, loaded)
        self.assertNotIn('exception trail', output)

    def test_refused_trail_is_reported_but_never_fatal(self):
        for status in (self.module.TRAIL_STATUS_NOT_CONFIGURED + 1, -1):
            bridge = _TrailBridge(trail_status=status)

            loaded, output = _load_bridge(self.module, bridge)

            self.assertIs(bridge, loaded)
            self.assertIn('exception trail unavailable', output)

    def test_raising_trail_is_contained_to_the_recorder(self):
        bridge = _TrailBridge(trail_status=RuntimeError('no handler'))

        loaded, output = _load_bridge(self.module, bridge)

        self.assertIs(bridge, loaded)
        self.assertIn('exception trail refused: no handler', output)

    def test_older_sidecar_without_the_trail_still_loads(self):
        bridge = _NativeBridge()

        loaded, output = _load_bridge(self.module, bridge)

        self.assertIs(bridge, loaded)
        self.assertEqual(['install_atmosphere_owner_guard'], bridge.events)
        self.assertNotIn('exception trail', output)

    def test_a_failed_atmosphere_guard_still_refuses_the_whole_bridge(self):
        bridge = _TrailBridge(atmosphere_status=201)

        with self.assertRaises(self.module.ClientInstanceGuardError):
            _load_bridge(self.module, bridge)

        self.assertEqual(['install_atmosphere_owner_guard'], bridge.events)


class ClientInstanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.environ = {
            self.module.ALLOW_MULTIPLE_CLIENTS_ENV: '1',
        }

    def test_normal_launch_does_not_touch_the_guard(self):
        calls = []

        self.assertFalse(self.module.release_if_requested(
            environ={}, releaser=lambda: calls.append('release') or True))

        self.assertEqual([], calls)
        self.assertFalse(self.module._attempted)

    def test_opt_in_release_runs_once(self):
        calls = []
        releaser = lambda: calls.append('release') or True

        self.assertTrue(self.module.release_if_requested(
            environ=self.environ, releaser=releaser))
        self.assertTrue(self.module.release_if_requested(
            environ=self.environ, releaser=releaser))

        self.assertEqual(['release'], calls)

    def test_release_error_is_cached(self):
        calls = []

        def fail():
            calls.append('release')
            raise RuntimeError('failed')

        with self.assertRaises(RuntimeError):
            self.module.release_if_requested(
                environ=self.environ, releaser=fail)
        with self.assertRaises(RuntimeError):
            self.module.release_if_requested(
                environ=self.environ, releaser=fail)

        self.assertEqual(['release'], calls)

    def test_native_bridge_path_is_a_loose_sidecar_beside_the_wotmod(self):
        self.assertEqual(
            str(Path('/games/wot/mods/0.9.22.0.1') /
                self.module.NATIVE_FILENAME),
            self.module._native_bridge_path(
                '/games/wot/WorldOfTanks.exe'))

    def test_loader_uses_explicit_path_and_publishes_the_native_module(self):
        bridge = _NativeBridge()

        class _ImpModule(object):
            def __init__(self):
                self.calls = []

            def load_dynamic(self, name, path):
                self.calls.append((name, path))
                return bridge

        imp_module = _ImpModule()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / self.module.NATIVE_FILENAME)
            Path(path).write_bytes(b'PE sidecar')
            try:
                self.assertIs(
                    bridge,
                    self.module._load_native_bridge(
                        path=path, imp_module=imp_module))
                self.assertEqual(
                    [(self.module.NATIVE_MODULE_NAME, path)],
                    imp_module.calls)
                self.assertIs(
                    bridge,
                    sys.modules[self.module.NATIVE_MODULE_NAME])
                self.assertEqual(['install_atmosphere_owner_guard'],
                                 bridge.events)
                self.assertIs(bridge, self.module._load_native_bridge())
                self.assertEqual(['install_atmosphere_owner_guard'],
                                 bridge.events)
            finally:
                sys.modules.pop(self.module.NATIVE_MODULE_NAME, None)

    def test_failed_atmosphere_patch_is_not_published_or_cached(self):
        bridge = _NativeBridge(atmosphere_status=201)

        class ImpModule(object):
            def load_dynamic(self, name, path):
                sys.modules[name] = bridge
                return bridge

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.module.NATIVE_FILENAME
            path.write_bytes(b'PE sidecar')
            with self.assertRaises(self.module.ClientInstanceGuardError):
                self.module._load_native_bridge(
                    path=str(path), imp_module=ImpModule())
        self.assertIsNone(self.module._native_bridge)
        self.assertNotIn(self.module.NATIVE_MODULE_NAME, sys.modules)
        self.assertEqual(['install_atmosphere_owner_guard'], bridge.events)

    def test_missing_bridge_fails_before_wgc_is_touched(self):
        def fail_load():
            raise ImportError('missing bridge')

        self.module._load_native_bridge = fail_load
        with self.assertRaises(ImportError):
            self.module._release_native()

    def test_native_path_runs_the_complete_engine_guard_teardown(self):
        events = []
        native_bridge = _NativeBridge(events=events)

        self.assertTrue(self.module._release_native(
            native_bridge=native_bridge))

        self.assertEqual(['release_client_guard'], events)

    def test_release_failure_decodes_native_guard_status(self):
        status = 10
        native_bridge = _NativeBridge(release_status=status)

        with self.assertRaises(
                self.module.ClientInstanceGuardError) as raised:
            self.module._release_native(
                native_bridge=native_bridge)

        self.assertEqual(
            'WGC API teardown postcondition', raised.exception.operation)
        self.assertEqual(status, raised.exception.error_code)

    def test_window_bridge_is_reversible_and_reports_native_errors(self):
        native_bridge = _NativeBridge(hide_result=2, show_result=2)
        self.assertEqual(
            2, self.module.hide_process_windows(native_bridge))
        self.assertEqual(
            2, self.module.show_process_windows(native_bridge))
        self.assertEqual(
            ['hide_process_windows', 'show_process_windows'],
            native_bridge.events)

        failing_bridge = _NativeBridge(hide_result=-5)
        with self.assertRaises(
                self.module.ClientInstanceGuardError) as raised:
            self.module.hide_process_windows(failing_bridge)
        self.assertEqual('hide_process_windows', raised.exception.operation)
        self.assertEqual(5, raised.exception.error_code)


if __name__ == '__main__':
    unittest.main()
