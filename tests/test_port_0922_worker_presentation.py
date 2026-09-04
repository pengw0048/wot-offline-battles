import importlib.util
from pathlib import Path
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / 'src' / 'res' / 'scripts' /
               'client' / 'gui' / 'mods' / 'offline_lan_0922' /
               'worker_presentation.py')


def _load():
    name = 'test_offline_lan_0922_worker_presentation'
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Native(object):
    def __init__(self, hidden=1, hide_error=None, show_error=None):
        self.hidden = hidden
        self.hide_error = hide_error
        self.show_error = show_error
        self.hide_calls = 0
        self.show_calls = 0

    def hide_process_windows(self):
        self.hide_calls += 1
        if self.hide_error is not None:
            raise self.hide_error
        return self.hidden

    def show_process_windows(self):
        self.show_calls += 1
        if self.show_error is not None:
            raise self.show_error
        return self.hidden


class _WWISE(types.SimpleNamespace):
    def __init__(self):
        self.volumes = []

        def set_master(volume):
            self.volumes.append(volume)
            return volume

        super().__init__(WW_setMasterVolume=set_master)


class WorkerPresentationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.native = _Native()
        self.wwise = _WWISE()
        self.original_set_master = self.wwise.WW_setMasterVolume
        self.sound_groups = types.SimpleNamespace(
            _SoundGroups__masterVolume=0.65)
        self.presentation = self.module.WorkerPresentation(
            runtime=(self.native, self.wwise, self.sound_groups),
            environ={})

    def test_activation_hides_window_and_keeps_later_volume_changes_muted(self):
        self.assertTrue(self.presentation.activate())

        self.assertTrue(self.presentation.active)
        self.assertEqual(1, self.native.hide_calls)
        self.assertEqual([0.0], self.wwise.volumes)

        self.wwise.WW_setMasterVolume(0.8)

        self.assertEqual([0.0, 0.0], self.wwise.volumes)

    def test_deactivate_restores_only_owned_audio_hook_and_window(self):
        self.presentation.activate()

        self.assertTrue(self.presentation.deactivate())

        self.assertFalse(self.presentation.active)
        self.assertEqual(1, self.native.show_calls)
        self.assertIs(self.original_set_master,
                      self.wwise.WW_setMasterVolume)
        self.assertEqual([0.0, 0.65], self.wwise.volumes)

    def test_failed_window_restore_retains_owner_for_exact_retry(self):
        self.presentation.activate()
        self.native.show_error = RuntimeError('window restore failed')

        with self.assertRaisesRegex(RuntimeError, 'window restore failed'):
            self.presentation.deactivate()

        self.assertTrue(self.presentation.active)
        self.assertIs(self.native, self.presentation._native)
        self.native.show_error = None
        self.assertTrue(self.presentation.deactivate())
        self.assertFalse(self.presentation.active)
        self.assertEqual(2, self.native.show_calls)

    def test_missing_window_keeps_audio_muted_for_fail_closed_exit(self):
        self.native.hidden = 0

        with self.assertRaises(self.module.WorkerPresentationError):
            self.presentation.activate()

        self.assertFalse(self.presentation.active)
        self.assertEqual(0, self.native.show_calls)
        self.assertIsNot(self.original_set_master,
                         self.wwise.WW_setMasterVolume)
        self.assertEqual([0.0], self.wwise.volumes)

    def test_native_failure_keeps_audio_muted_for_fail_closed_exit(self):
        self.native.hide_error = RuntimeError('hide failed')

        with self.assertRaisesRegex(RuntimeError, 'hide failed'):
            self.presentation.activate()

        self.assertEqual(0, self.native.show_calls)
        self.assertIsNot(self.original_set_master,
                         self.wwise.WW_setMasterVolume)
        self.assertEqual([0.0], self.wwise.volumes)

    def test_uninstall_does_not_clobber_a_later_audio_wrapper(self):
        self.presentation.activate()

        def later_wrapper(volume):
            return volume

        self.wwise.WW_setMasterVolume = later_wrapper
        self.presentation.deactivate()

        self.assertIs(later_wrapper, self.wwise.WW_setMasterVolume)
        self.assertEqual([0.0], self.wwise.volumes)
        self.assertEqual(1, self.native.show_calls)

    def test_activation_is_idempotent(self):
        self.presentation.activate()
        self.presentation.activate()

        self.assertEqual(1, self.native.hide_calls)
        self.assertEqual([0.0], self.wwise.volumes)

    def test_exit_cleanup_does_not_show_window_or_restore_sound(self):
        self.presentation.activate()

        self.assertTrue(self.presentation.deactivate(restore=False))

        self.assertFalse(self.presentation.active)
        self.assertEqual(0, self.native.show_calls)
        self.assertIsNot(self.original_set_master,
                         self.wwise.WW_setMasterVolume)
        self.assertEqual([0.0], self.wwise.volumes)

    def test_private_desktop_keeps_its_game_window_visible_but_muted(self):
        self.native.hidden = 0
        presentation = self.module.WorkerPresentation(
            runtime=(self.native, self.wwise, self.sound_groups),
            environ={self.module.HIDDEN_DESKTOP_ENV: '1'})

        self.assertTrue(presentation.activate())
        self.assertEqual(0, self.native.hide_calls)
        self.assertEqual([0.0], self.wwise.volumes)

        self.assertTrue(presentation.deactivate())
        self.assertEqual(0, self.native.show_calls)
        self.assertEqual([0.0, 0.65], self.wwise.volumes)

    def test_ready_marker_replaces_stale_state_after_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'offline-worker.ready'
            temporary = Path(str(marker) + '.tmp')
            marker.write_bytes(b'stale')
            temporary.write_bytes(b'partial')

            self.assertTrue(self.module.signal_worker_ready({
                self.module.WORKER_READY_MARKER_ENV: str(marker)}))

            self.assertEqual(b'ready\n', marker.read_bytes())
            self.assertFalse(temporary.exists())

    def test_ready_marker_path_is_required(self):
        with self.assertRaisesRegex(
                self.module.WorkerPresentationError, 'marker'):
            self.module.signal_worker_ready({})

    def test_player_ready_marker_uses_its_own_environment_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'offline-player.ready'

            self.assertTrue(self.module.signal_player_ready({
                self.module.PLAYER_READY_MARKER_ENV: str(marker)}))

            self.assertEqual(b'ready\n', marker.read_bytes())

    def test_player_ready_marker_path_is_required(self):
        with self.assertRaisesRegex(
                self.module.WorkerPresentationError, 'visible player'):
            self.module.signal_player_ready({})


if __name__ == '__main__':
    unittest.main()
