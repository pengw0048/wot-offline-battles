import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _install_package_modules():
    created = []
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
            created.append(name)
    return created


def _load(name):
    _install_package_modules()
    full_name = 'gui.mods.offline_lan_0922.' + name
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(full_name,
                                                   PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _ArenaType(object):
    def __init__(self, geometry_name, gameplay='ctf', name=None):
        self.geometryName = geometry_name
        self.gameplayName = gameplay
        self.name = name or geometry_name
        self.maxPlayersInTeam = 15
        self.roundLength = 900


class _Window(object):
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.calls = []
        self.closed = False
        self.close_calls = 0
        self.data_updates = []
        self._TrainingSettingsWindow__arenasCache = None

    def updateTrainingRoom(self, arena, round_length, is_private, comment):
        self.calls.append((arena, round_length, is_private, comment))
        return 'stock'

    def getInfo(self):
        return {'description': 'stock'}

    def getMapsData(self):
        return self._TrainingSettingsWindow__arenasCache.cache

    def as_setDataS(self, info, maps):
        self.data_updates.append((info, maps))

    def onWindowClose(self):
        self.closed = True
        self.close_calls += 1


_WINDOW_INIT = _Window.__init__
_WINDOW_UPDATE = _Window.updateTrainingRoom
_WINDOW_CLOSE = _Window.onWindowClose
_WINDOW_GET_INFO = _Window.getInfo


class _LobbyHeader(object):
    def __init__(self):
        self.calls = []
        self.disabled = []

    def fightClick(self, map_id, action_name):
        self.calls.append((map_id, action_name))
        return 'stock'

    def _updatePrebattleControls(self):
        self.as_disableFightButtonS(True)
        return 'updated'

    def as_disableFightButtonS(self, disabled):
        self.disabled.append(disabled)


_LOBBY_HEADER_FIGHT_CLICK = _LobbyHeader.fightClick
_LOBBY_HEADER_UPDATE_CONTROLS = _LobbyHeader._updatePrebattleControls


class JoinButtonUITests(unittest.TestCase):
    def setUp(self):
        self.queue_ui = _load('queue_ui')
        self.join_calls = []
        self.handled = True
        self.refresh = mock.Mock()
        self.adapter = self.queue_ui.JoinButtonUI(
            self._join, runtime=_LobbyHeader, refresh=self.refresh)

    def tearDown(self):
        self.adapter.uninstall()
        _LobbyHeader.fightClick = _LOBBY_HEADER_FIGHT_CLICK
        _LobbyHeader._updatePrebattleControls = (
            _LOBBY_HEADER_UPDATE_CONTROLS)

    def _join(self, map_id, action_name):
        self.join_calls.append((map_id, action_name))
        return self.handled

    def test_install_routes_one_native_fight_click_to_join(self):
        self.adapter.install()
        header = _LobbyHeader()

        self.assertIsNone(header.fightClick(7, 'random'))

        self.assertEqual([(7, 'random')], self.join_calls)
        self.assertEqual([], header.calls)

    def test_install_refreshes_existing_header_and_forces_button_enabled(self):
        self.adapter.install()
        header = _LobbyHeader()

        self.assertEqual('updated', header._updatePrebattleControls())

        self.refresh.assert_called_once_with()
        self.assertEqual([True, False], header.disabled)

    def test_lan_mode_never_falls_through_to_stock_matchmaking(self):
        self.handled = False
        self.adapter.install()
        header = _LobbyHeader()

        self.assertIsNone(header.fightClick(9, 'ranked'))

        self.assertEqual([(9, 'ranked')], self.join_calls)
        self.assertEqual([], header.calls)

    def test_uninstall_restores_raw_class_function(self):
        original = _LobbyHeader.__dict__['fightClick']
        original_update = _LobbyHeader.__dict__['_updatePrebattleControls']
        self.adapter.install()
        self.assertIsNot(original, _LobbyHeader.__dict__['fightClick'])
        self.assertIsNot(
            original_update,
            _LobbyHeader.__dict__['_updatePrebattleControls'])

        self.adapter.uninstall()

        self.assertIs(original, _LobbyHeader.__dict__['fightClick'])
        self.assertIs(
            original_update,
            _LobbyHeader.__dict__['_updatePrebattleControls'])

    def test_failed_refresh_rolls_back_both_wrappers(self):
        adapter = self.queue_ui.JoinButtonUI(
            self._join, runtime=_LobbyHeader,
            refresh=mock.Mock(side_effect=RuntimeError('refresh failed')))

        with self.assertRaisesRegex(RuntimeError, 'refresh failed'):
            adapter.install()

        self.assertIs(
            _LOBBY_HEADER_FIGHT_CLICK,
            _LobbyHeader.__dict__['fightClick'])
        self.assertIs(
            _LOBBY_HEADER_UPDATE_CONTROLS,
            _LobbyHeader.__dict__['_updatePrebattleControls'])

    def test_uninstall_does_not_clobber_later_wrapper(self):
        self.adapter.install()

        def later_wrapper(header, map_id, action_name):
            return 'later'

        _LobbyHeader.fightClick = later_wrapper
        self.adapter.uninstall()

        self.assertIs(later_wrapper, _LobbyHeader.fightClick)
        self.assertIs(
            _LOBBY_HEADER_UPDATE_CONTROLS,
            _LobbyHeader.__dict__['_updatePrebattleControls'])


class QueueUITests(unittest.TestCase):
    def setUp(self):
        self.catalog = _load('map_catalog')
        self.queue_ui = _load('queue_ui')
        self.arena_type = types.SimpleNamespace(g_cache={
            1: _ArenaType('01_karelia'),
            2: _ArenaType('04_himmelsdorf', gameplay='assault'),
            3: _ArenaType('05_prohorovka'),
        })
        self.started = []
        self.closed = []
        self.adapter = self.queue_ui.QueueUI(
            lambda *args: self.started.append(args),
            lambda: ('05_prohorovka',),
            endpoint=lambda: 'LAN SERVER: 10.0.0.5:28782',
            runtime=(self.arena_type, _Window),
            on_close=lambda: self.closed.append(True))

    def tearDown(self):
        self.adapter.uninstall()
        _Window.__init__ = _WINDOW_INIT
        _Window.updateTrainingRoom = _WINDOW_UPDATE
        _Window.onWindowClose = _WINDOW_CLOSE
        _Window.getInfo = _WINDOW_GET_INFO

    def test_catalog_filters_non_ctf_and_server_pool(self):
        rows = self.catalog.build(self.arena_type.g_cache,
                                  ('05_prohorovka',)).cache
        self.assertEqual(['05_prohorovka'], [row['name'] for row in rows])

    def test_unknown_server_pool_shows_all_local_standard_maps(self):
        rows = self.catalog.build(self.arena_type.g_cache, None).cache

        self.assertEqual(
            ['01_karelia', '05_prohorovka'],
            [row['name'] for row in rows])

    def test_catalog_uses_stock_1513_map_icon_formatter(self):
        formatters = types.ModuleType(
            'gui.Scaleform.daapi.view.lobby.trainings.formatters')
        formatters.getMapIconPath = lambda arena: 'icons/' + arena.geometryName
        trainings = types.ModuleType(
            'gui.Scaleform.daapi.view.lobby.trainings')
        trainings.formatters = formatters
        modules = {
            'gui.Scaleform': types.ModuleType('gui.Scaleform'),
            'gui.Scaleform.daapi': types.ModuleType('gui.Scaleform.daapi'),
            'gui.Scaleform.daapi.view': types.ModuleType(
                'gui.Scaleform.daapi.view'),
            'gui.Scaleform.daapi.view.lobby': types.ModuleType(
                'gui.Scaleform.daapi.view.lobby'),
            'gui.Scaleform.daapi.view.lobby.trainings': trainings,
            'gui.Scaleform.daapi.view.lobby.trainings.formatters': formatters,
        }

        with mock.patch.dict(sys.modules, modules):
            row = self.catalog.build(
                self.arena_type.g_cache, ('05_prohorovka',)).cache[0]

        self.assertEqual('icons/05_prohorovka', row['icon'])

    def test_records_exact_upstream_hook_reference(self):
        self.assertEqual(
            'c0bc550c46deac980194b7b860ee8781d53ec97b',
            self.queue_ui.UPSTREAM_TUXEDO_COMMIT)
        self.assertIn(self.queue_ui.UPSTREAM_TUXEDO_COMMIT,
                      self.queue_ui.UPSTREAM_TUXEDO_URL)

    def test_open_picker_uses_exact_1513_lobby_view_contract(self):
        class ViewLoadParams(object):
            def __init__(self, alias, name=None):
                self.alias = alias
                self.name = name

        app = types.SimpleNamespace(loadView=mock.Mock())
        loaders = types.ModuleType(
            'gui.Scaleform.framework.managers.loaders')
        loaders.ViewLoadParams = ViewLoadParams
        aliases = types.ModuleType(
            'gui.Scaleform.genConsts.PREBATTLE_ALIASES')
        aliases.PREBATTLE_ALIASES = types.SimpleNamespace(
            TRAINING_SETTINGS_WINDOW_PY='trainingSettingsWindow')
        app_loader = types.ModuleType('gui.app_loader')
        app_loader.g_appLoader = types.SimpleNamespace(
            getDefLobbyApp=mock.Mock(return_value=app))
        modules = {
            'gui.Scaleform': types.ModuleType('gui.Scaleform'),
            'gui.Scaleform.framework': types.ModuleType(
                'gui.Scaleform.framework'),
            'gui.Scaleform.framework.managers': types.ModuleType(
                'gui.Scaleform.framework.managers'),
            'gui.Scaleform.framework.managers.loaders': loaders,
            'gui.Scaleform.genConsts': types.ModuleType(
                'gui.Scaleform.genConsts'),
            'gui.Scaleform.genConsts.PREBATTLE_ALIASES': aliases,
            'gui.app_loader': app_loader,
        }

        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(self.queue_ui.open_picker())

        app_loader.g_appLoader.getDefLobbyApp.assert_called_once_with()
        params, context = app.loadView.call_args[0]
        self.assertEqual('trainingSettingsWindow', params.alias)
        self.assertEqual('trainingSettingsWindow', params.name)
        self.assertEqual({
            'isCreateRequest': True,
            'isOfflineLanPicker': True,
        }, context)

    def test_offline_picker_returns_before_owner_closes_native_view(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertEqual(['05_prohorovka'], [
            row['name'] for row in
            window._TrainingSettingsWindow__arenasCache.cache])
        self.assertEqual(
            'LAN SERVER: 10.0.0.5:28782',
            window.getInfo()['description'])
        self.assertIsNone(
            window.updateTrainingRoom(3, 15, False, 'ignored'))
        self.assertEqual([('05_prohorovka', 'ignored')], self.started)
        self.assertEqual([], window.calls)
        self.assertFalse(window.closed)
        self.assertTrue(getattr(window, self.queue_ui._PICKER_MARKER))

        self.assertTrue(self.adapter.close())
        self.assertTrue(window.closed)
        self.assertFalse(getattr(window, self.queue_ui._PICKER_MARKER))

    def test_close_clears_marker_before_stock_window_cleanup(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertTrue(self.adapter.close())
        self.assertFalse(self.adapter.close())
        self.assertTrue(window.closed)
        self.assertEqual(1, window.close_calls)
        self.assertEqual([True], self.closed)
        self.assertFalse(getattr(window, self.queue_ui._PICKER_MARKER))

    def test_stock_close_detaches_picker_and_notifies_owner_once(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        window.onWindowClose()
        window.onWindowClose()

        self.assertIsNone(self.adapter._picker_window)
        self.assertFalse(self.adapter.close())
        self.assertEqual(2, window.close_calls)
        self.assertEqual([True], self.closed)

    def test_offline_picker_rejects_unavailable_map(self):
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})

        self.assertFalse(window.updateTrainingRoom(1, 15, False, 'ignored'))
        self.assertEqual([], self.started)
        self.assertFalse(window.closed)

    def test_refresh_replaces_preconnection_catalog_in_the_same_window(self):
        pool = [None]
        description = ['LAN SERVER: 10.0.0.5:28782\nPLAYERS (0)']
        self.adapter = self.queue_ui.QueueUI(
            lambda *args: self.started.append(args), lambda: pool[0],
            endpoint=lambda: description[0],
            runtime=(self.arena_type, _Window))
        self.adapter.install()
        window = _Window({'isOfflineLanPicker': True})
        self.assertEqual(2, len(window.getMapsData()))

        pool[0] = ('05_prohorovka',)
        description[0] = (
            'LAN SERVER: 10.0.0.5:28782\nPLAYERS (2): Host, Guest')
        self.assertTrue(self.adapter.refresh())

        self.assertIs(window, self.adapter._picker_window)
        self.assertEqual(['05_prohorovka'], [
            row['name'] for row in window.getMapsData()])
        self.assertEqual(1, len(window.data_updates))
        self.assertEqual(
            'LAN SERVER: 10.0.0.5:28782\nPLAYERS (2): Host, Guest',
            window.data_updates[0][0]['description'])

    def test_normal_training_window_fully_forwards(self):
        self.adapter.install()
        window = _Window({'isCreateRequest': True})

        self.assertEqual('stock', window.updateTrainingRoom(1, 15, True, 'x'))
        self.assertEqual([(1, 15, True, 'x')], window.calls)
        self.assertEqual({'description': 'stock'}, window.getInfo())
        self.assertEqual([], self.started)

    def test_uninstall_does_not_clobber_later_wrapper(self):
        self.adapter.install()

        def later_wrapper(*args):
            return 'later'

        _Window.updateTrainingRoom = later_wrapper
        self.adapter.uninstall()
        self.assertIs(later_wrapper, _Window.updateTrainingRoom)

    def test_uninstall_restores_raw_class_functions(self):
        original_init = _Window.__dict__['__init__']
        original_update = _Window.__dict__['updateTrainingRoom']
        original_close = _Window.__dict__['onWindowClose']
        original_get_info = _Window.__dict__['getInfo']
        self.adapter.install()

        self.adapter.uninstall()

        self.assertIs(original_init, _Window.__dict__['__init__'])
        self.assertIs(original_update,
                      _Window.__dict__['updateTrainingRoom'])
        self.assertIs(original_close,
                      _Window.__dict__['onWindowClose'])
        self.assertIs(original_get_info,
                      _Window.__dict__['getInfo'])
