import importlib.util
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
                 events=None):
        self.release_status = release_status
        self.hide_result = hide_result
        self.show_result = show_result
        self.events = [] if events is None else events

    def release_client_guard(self):
        self.events.append('release_client_guard')
        return self.release_status

    def hide_process_windows(self):
        self.events.append('hide_process_windows')
        return self.hide_result

    def show_process_windows(self):
        self.events.append('show_process_windows')
        return self.show_result


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
            finally:
                sys.modules.pop(self.module.NATIVE_MODULE_NAME, None)

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
