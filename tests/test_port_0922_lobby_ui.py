import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / 'src' / 'res' / 'scripts' /
               'client' / 'gui' / 'mods' / 'offline_lan_0922' /
               'lobby_ui.py')


def _load():
    name = 'test_offline_lan_0922_lobby_ui'
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ChinaController(object):
    def __init__(self):
        self.lobby_calls = []
        self.manual_calls = 0

    def onLobbyInited(self, event):
        self.lobby_calls.append(event)
        self.showBrowser()
        return 'stock-result'

    def showBrowser(self):
        self.manual_calls += 1


_ORIGINAL_LOBBY_INITED = _ChinaController.onLobbyInited


class ServerAnnouncementUITests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController,
            auto_due=lambda unused_controller: True)

    def tearDown(self):
        self.adapter.uninstall()
        _ChinaController.onLobbyInited = _ORIGINAL_LOBBY_INITED

    def test_suppresses_automatic_entry_without_patching_manual_browser(self):
        self.adapter.install()
        controller = _ChinaController()

        self.assertIsNone(controller.onLobbyInited('ready'))
        controller.showBrowser()

        self.assertEqual([], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_nonautomatic_lobby_entry_keeps_stock_behavior(self):
        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController,
            auto_due=lambda unused_controller: False)
        self.adapter.install()
        controller = _ChinaController()

        self.assertEqual('stock-result', controller.onLobbyInited('ready'))

        self.assertEqual(['ready'], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_due_checker_failure_does_not_block_stock_lobby(self):
        def broken_due(unused_controller):
            raise RuntimeError('controller unavailable')

        self.adapter = self.module.ServerAnnouncementUI(
            runtime=_ChinaController, auto_due=broken_due)
        self.adapter.install()
        controller = _ChinaController()

        self.assertEqual('stock-result', controller.onLobbyInited('ready'))

        self.assertEqual(['ready'], controller.lobby_calls)
        self.assertEqual(1, controller.manual_calls)

    def test_uninstall_restores_exact_method(self):
        original = _ChinaController.__dict__['onLobbyInited']
        self.adapter.install()

        self.adapter.uninstall()

        self.assertIs(original, _ChinaController.__dict__['onLobbyInited'])

    def test_failed_uninstall_retains_hook_owner_for_retry(self):
        self.adapter.install()
        restore = self.adapter._restore
        self.adapter._restore = mock.Mock(
            side_effect=RuntimeError('hook restore failed'))

        with self.assertRaisesRegex(RuntimeError, 'hook restore failed'):
            self.adapter.uninstall()

        self.assertTrue(self.adapter._installed)
        self.adapter._restore = restore
        self.adapter.uninstall()
        self.assertFalse(self.adapter._installed)
        self.assertIs(_ORIGINAL_LOBBY_INITED,
                      _ChinaController.__dict__['onLobbyInited'])

    def test_uninstall_does_not_clobber_later_wrapper(self):
        self.adapter.install()

        def later_wrapper(controller, event):
            return 'later'

        _ChinaController.onLobbyInited = later_wrapper
        self.adapter.uninstall()

        self.assertIs(later_wrapper,
                      _ChinaController.__dict__['onLobbyInited'])


class _Module(object):
    def __init__(self, value):
        self.isShowStartupVideo = value


class _FailingRestoreModule(_Module):
    def __init__(self, value):
        object.__setattr__(self, '_restore_value', value)
        object.__setattr__(self, 'fail_restore', False)
        _Module.__init__(self, value)

    def __setattr__(self, name, value):
        if (name == 'isShowStartupVideo' and self.fail_restore and
                value is self._restore_value):
            object.__setattr__(self, 'fail_restore', False)
            raise RuntimeError('startup hook restore failed')
        object.__setattr__(self, name, value)


class IntroVideoSkipTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    @staticmethod
    def _stock():
        return True

    def test_the_startup_video_check_reports_no_video(self):
        helpers = _Module(self._stock)
        states = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers, states))

        self.assertTrue(skip.install())

        self.assertFalse(helpers.isShowStartupVideo())
        self.assertFalse(states.isShowStartupVideo())

    def test_uninstall_restores_every_replaced_name(self):
        helpers = _Module(self._stock)
        states = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers, states))
        skip.install()

        skip.uninstall()

        self.assertIs(self._stock, helpers.isShowStartupVideo)
        self.assertIs(self._stock, states.isShowStartupVideo)

    def test_failed_hook_restore_retains_entry_for_retry(self):
        helpers = _FailingRestoreModule(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers,))
        skip.install()
        helpers.fail_restore = True

        with self.assertRaisesRegex(
                RuntimeError, 'startup hook restore failed'):
            skip.uninstall()

        self.assertEqual(1, len(skip._replaced))
        self.assertTrue(skip.uninstall())
        self.assertEqual([], skip._replaced)
        self.assertIs(self._stock, helpers.isShowStartupVideo)

    def test_a_module_without_the_check_is_left_alone(self):
        helpers = _Module(self._stock)
        other = object()
        skip = self.module.IntroVideoSkip(runtime=(helpers, other))

        self.assertTrue(skip.install())

        self.assertFalse(helpers.isShowStartupVideo())
        self.assertFalse(hasattr(other, 'isShowStartupVideo'))

    def test_uninstall_does_not_clobber_another_replacement(self):
        helpers = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers,))
        skip.install()

        def later(*unused):
            return True

        helpers.isShowStartupVideo = later
        skip.uninstall()

        self.assertIs(later, helpers.isShowStartupVideo)

    def test_installing_twice_keeps_one_replacement(self):
        helpers = _Module(self._stock)
        skip = self.module.IntroVideoSkip(runtime=(helpers,))
        skip.install()
        skip.install()

        skip.uninstall()

        self.assertIs(self._stock, helpers.isShowStartupVideo)


if __name__ == '__main__':
    unittest.main()
