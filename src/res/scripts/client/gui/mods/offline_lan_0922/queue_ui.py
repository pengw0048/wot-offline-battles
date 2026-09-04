from __future__ import print_function

from gui.mods.offline_lan_0922 import map_catalog


OFFLINE_PICKER_FLAG = 'isOfflineLanPicker'
_PICKER_MARKER = '_offline_lan_0922_picker'

# Hook shape adapted from the public 0.9.22 observer implementation:
# https://github.com/the-tuxedo-cat/wot-offline-server/blob/c0bc550c46deac980194b7b860ee8781d53ec97b/sources/scripts/client/gui/mods/mod_observer.py#L73-L80,L138-L139,L231-L244
# Its direct Avatar transition is deliberately not used here.  This adapter
# only replaces the map result with the existing LAN request_start boundary.
UPSTREAM_TUXEDO_COMMIT = 'c0bc550c46deac980194b7b860ee8781d53ec97b'
UPSTREAM_TUXEDO_URL = (
    'https://github.com/the-tuxedo-cat/wot-offline-server/blob/' +
    UPSTREAM_TUXEDO_COMMIT +
    '/sources/scripts/client/gui/mods/mod_observer.py')


def _load_runtime():
    import ArenaType
    from gui.Scaleform.daapi.view.lobby.trainings.TrainingSettingsWindow import \
        TrainingSettingsWindow
    return (ArenaType, TrainingSettingsWindow)


def _load_join_runtime():
    from gui.Scaleform.daapi.view.lobby.header.LobbyHeader import LobbyHeader
    return LobbyHeader


def _refresh_join_button():
    """Ask the existing #1513 lobby header to repaint its fight button."""
    from gui.shared import events, g_eventBus
    from gui.shared.event_bus import EVENT_BUS_SCOPE

    g_eventBus.handleEvent(
        events.FightButtonEvent(events.FightButtonEvent.FIGHT_BUTTON_UPDATE),
        EVENT_BUS_SCOPE.LOBBY)


def open_picker():
    """Open the stock training settings view, following the observer hook."""
    from gui.Scaleform.framework.managers.loaders import ViewLoadParams
    from gui.Scaleform.genConsts.PREBATTLE_ALIASES import PREBATTLE_ALIASES
    from gui.app_loader import g_appLoader

    alias = PREBATTLE_ALIASES.TRAINING_SETTINGS_WINDOW_PY
    app = g_appLoader.getDefLobbyApp()
    if app is None:
        return False
    app.loadView(ViewLoadParams(alias, alias), {
        'isCreateRequest': True,
        OFFLINE_PICKER_FLAG: True,
    })
    return True


class JoinButtonUI(object):
    """A reversible adapter for #1513's native lobby fight button."""

    def __init__(self, on_join, runtime=None, refresh=None):
        self._on_join = on_join
        self._runtime = runtime
        self._refresh = refresh
        self._installed = False
        self._header_type = None
        self._original_fight_click = None
        self._had_own_fight_click = False
        self._fight_click_wrapper = None
        self._original_update_controls = None
        self._had_own_update_controls = False
        self._update_controls_wrapper = None

    def install(self):
        if self._installed:
            return
        default_runtime = self._runtime is None
        header_type = self._runtime or _load_join_runtime()
        self._runtime = header_type
        self._header_type = header_type
        self._had_own_fight_click = 'fightClick' in header_type.__dict__
        self._had_own_update_controls = (
            '_updatePrebattleControls' in header_type.__dict__)
        # Python 2 returns a fresh unbound-method wrapper from getattr(class,
        # method).  Retain the raw member so uninstall can compare identity.
        self._original_fight_click = header_type.__dict__.get(
            'fightClick', getattr(header_type, 'fightClick'))
        self._original_update_controls = header_type.__dict__.get(
            '_updatePrebattleControls',
            getattr(header_type, '_updatePrebattleControls'))
        adapter = self

        def wrapped_fight_click(header, map_id, action_name):
            # LAN mode never falls through to retail matchmaking.  In #1513
            # that path opens the global ``prebattle/join`` Waiting screen and
            # waits for an Account RPC this offline client cannot complete.
            adapter._on_join(map_id, action_name)
            return None

        def wrapped_update_controls(header):
            result = adapter._original_update_controls(header)
            # The native action validators describe retail matchmaking.  The
            # LAN adapter consumes the click before that action boundary, so
            # their disabled result must not make the LAN action unreachable.
            header.as_disableFightButtonS(False)
            return result

        self._fight_click_wrapper = wrapped_fight_click
        self._update_controls_wrapper = wrapped_update_controls
        self._installed = True
        try:
            header_type.fightClick = wrapped_fight_click
            header_type._updatePrebattleControls = wrapped_update_controls
            refresh = self._refresh
            if refresh is None and default_runtime:
                refresh = _refresh_join_button
            if callable(refresh):
                refresh()
        except Exception:
            self.uninstall()
            raise

    def _restore(self, name, original, wrapper, had_own):
        current = self._header_type.__dict__.get(name)
        if current is not wrapper:
            return
        if had_own:
            setattr(self._header_type, name, original)
        else:
            delattr(self._header_type, name)

    def uninstall(self):
        if not self._installed:
            return
        self._restore('fightClick', self._original_fight_click,
                      self._fight_click_wrapper, self._had_own_fight_click)
        self._restore('_updatePrebattleControls',
                      self._original_update_controls,
                      self._update_controls_wrapper,
                      self._had_own_update_controls)
        self._installed = False


class QueueUI(object):
    """A reversible, chain-safe adapter for the stock map picker."""

    def __init__(self, request_start, map_pool, endpoint=None, runtime=None,
                 on_close=None):
        self._request_start = request_start
        self._map_pool = map_pool
        self._endpoint = endpoint or (lambda: '')
        self._on_close = on_close
        self._runtime = runtime
        self._installed = False
        self._window_type = None
        self._original_init = None
        self._original_update = None
        self._original_close = None
        self._original_get_info = None
        self._had_own_init = False
        self._had_own_update = False
        self._had_own_close = False
        self._had_own_get_info = False
        self._init_wrapper = None
        self._update_wrapper = None
        self._close_wrapper = None
        self._get_info_wrapper = None
        self._picker_window = None

    def install(self):
        if self._installed:
            return
        arena_type, window_type = self._runtime or _load_runtime()
        self._runtime = (arena_type, window_type)
        self._window_type = window_type
        self._had_own_init = '__init__' in window_type.__dict__
        self._had_own_update = 'updateTrainingRoom' in window_type.__dict__
        self._had_own_close = 'onWindowClose' in window_type.__dict__
        self._had_own_get_info = 'getInfo' in window_type.__dict__
        # In Python 2, getattr(class, method) returns a fresh unbound-method
        # wrapper.  Keep the raw class members so identity checks during
        # chain-safe uninstall remain meaningful on the target runtime.
        self._original_init = window_type.__dict__.get(
            '__init__', getattr(window_type, '__init__'))
        self._original_update = window_type.__dict__.get(
            'updateTrainingRoom', getattr(window_type, 'updateTrainingRoom'))
        self._original_close = window_type.__dict__.get(
            'onWindowClose', getattr(window_type, 'onWindowClose'))
        self._original_get_info = window_type.__dict__.get(
            'getInfo', getattr(window_type, 'getInfo'))
        adapter = self

        def wrapped_init(window, ctx=None):
            result = adapter._original_init(window, ctx)
            context = ctx or {}
            if bool(context.get(OFFLINE_PICKER_FLAG, False)):
                setattr(window, _PICKER_MARKER, True)
                adapter._picker_window = window
                catalog = map_catalog.build(arena_type.g_cache,
                                            adapter._map_pool())
                setattr(window, '_TrainingSettingsWindow__arenasCache',
                        catalog)
            return result

        def wrapped_get_info(window):
            info = adapter._original_get_info(window)
            if not getattr(window, _PICKER_MARKER, False):
                return info
            result = dict(info or {})
            result['description'] = adapter._endpoint()
            return result

        def wrapped_update(window, arena, round_length, is_private, comment):
            if not getattr(window, _PICKER_MARKER, False):
                return adapter._original_update(
                    window, arena, round_length, is_private, comment)
            map_name = map_catalog.geometry_name(arena_type.g_cache, arena,
                                                 adapter._map_pool())
            if map_name is None:
                return False
            accepted = adapter._request_start(map_name, comment)
            if accepted is False:
                return False
            # This method is entered from Scaleform's native event dispatcher.
            # Destroying the view before that dispatcher regains control leaves
            # native return-value state pointing at a retired window.  The
            # session owns a next-tick close after this Python callback returns.
            # The stock/Tuxedo hook is also void.  Returning a Python value
            # makes Scaleform convert it through that same native window.
            return None

        def wrapped_close(window):
            detach_error = None
            try:
                adapter._detach_picker(window)
            except Exception as error:
                detach_error = error
            try:
                result = adapter._original_close(window)
            except Exception:
                if detach_error is not None:
                    raise detach_error
                raise
            if detach_error is not None:
                raise detach_error
            return result

        self._init_wrapper = wrapped_init
        self._update_wrapper = wrapped_update
        self._close_wrapper = wrapped_close
        self._get_info_wrapper = wrapped_get_info
        window_type.__init__ = wrapped_init
        window_type.updateTrainingRoom = wrapped_update
        window_type.onWindowClose = wrapped_close
        window_type.getInfo = wrapped_get_info
        self._installed = True

    def _detach_picker(self, window):
        """Forget one picker before its stock view destroys itself."""
        owned = self._picker_window is window
        marked = False
        try:
            marked = bool(getattr(window, _PICKER_MARKER, False))
        except ReferenceError:
            pass
        if not owned and not marked:
            return False
        if owned:
            self._picker_window = None
        try:
            setattr(window, _PICKER_MARKER, False)
        except ReferenceError:
            pass
        if callable(self._on_close):
            self._on_close()
        return True

    def close(self):
        """Close only the stock window created by this LAN picker adapter."""
        window = self._picker_window
        if window is None:
            return False
        detach_error = None
        try:
            self._detach_picker(window)
        except Exception as error:
            detach_error = error
        try:
            close = getattr(window, 'onWindowClose', None)
        except ReferenceError:
            if detach_error is not None:
                raise detach_error
            return True
        if callable(close):
            try:
                close()
            except ReferenceError:
                pass
            except Exception:
                if detach_error is not None:
                    raise detach_error
                raise
        if detach_error is not None:
            raise detach_error
        return True

    def refresh(self):
        """Refresh the open stock view after the server publishes its maps."""
        window = self._picker_window
        if window is None:
            return False
        arena_type, unused_window_type = self._runtime
        catalog = map_catalog.build(arena_type.g_cache, self._map_pool())
        try:
            setattr(window, '_TrainingSettingsWindow__arenasCache', catalog)
            # This is the exact #1513 _populate data contract.  Updating the
            # existing native view avoids a destroy/reopen cursor race.
            window.as_setDataS(window.getInfo(), window.getMapsData())
        except ReferenceError:
            self._picker_window = None
            return False
        return True

    def _restore(self, name, original, installed, had_own):
        current = self._window_type.__dict__.get(name)
        if current is not installed:
            return
        if had_own:
            setattr(self._window_type, name, original)
        else:
            delattr(self._window_type, name)

    def uninstall(self):
        if not self._installed:
            return
        self._restore('__init__', self._original_init, self._init_wrapper,
                      self._had_own_init)
        self._restore('updateTrainingRoom', self._original_update,
                      self._update_wrapper, self._had_own_update)
        self._restore('onWindowClose', self._original_close,
                      self._close_wrapper, self._had_own_close)
        self._restore('getInfo', self._original_get_info,
                      self._get_info_wrapper, self._had_own_get_info)
        self._installed = False
        self._picker_window = None
