"""Entry point: routes `offlineBattle offline spaces/<map>` into the runtime."""
from __future__ import absolute_import, print_function

import sys

LOG_PREFIX = '[OFFLINE_2312_BATTLE]'
EXPECTED_CLIENT_VERSION = 'v.2.3.1.2 #919'
ACTIVATION_TOKEN = 'offlineBattle'
SPACE_PREFIX = 'spaces/'

_runtime = None
_offline_mode = None
_original_launch = None
_game_module = None
_original_game_fini = None


def _write_marker(message, *args):
    line = message % args if args else message
    try:
        print(line)
        sys.stdout.flush()
    except Exception:
        pass


def parse_request(argv):
    argv = list(argv)
    if ACTIVATION_TOKEN not in argv:
        return None
    try:
        index = argv.index('offline')
        space_name = argv[index + 1]
    except (ValueError, IndexError):
        return None
    if not space_name.startswith(SPACE_PREFIX):
        return None
    map_name = space_name[len(SPACE_PREFIX):]
    if not map_name or '/' in map_name or '\\' in map_name:
        return None
    return space_name, map_name


def _routed_launch(space_name):
    if _runtime is None:
        _write_marker('%s bootstrap_failed reason=route_unbound', LOG_PREFIX)
        return None
    return _runtime.route_launch(space_name)


def _restore_routes():
    global _offline_mode, _original_launch
    global _game_module, _original_game_fini
    if (_offline_mode is not None and
            getattr(_offline_mode, 'launch', None) is _routed_launch and
            _original_launch is not None):
        _offline_mode.launch = _original_launch
        _write_marker('%s route_restored target=helpers.OfflineMode.launch',
                      LOG_PREFIX)
    if (_game_module is not None and
            getattr(_game_module, 'fini', None) is _routed_game_fini and
            _original_game_fini is not None):
        _game_module.fini = _original_game_fini
        _write_marker('%s route_restored target=game.fini', LOG_PREFIX)


def _routed_game_fini(*args, **kwargs):
    original = _original_game_fini
    runtime = _runtime
    _restore_routes()
    if runtime is not None:
        try:
            runtime.shutdown('game_fini')
        except Exception as error:
            detail = repr(error).replace('\n', ' ')[:200]
            _write_marker('%s cleanup_failed stage=runtime_shutdown '
                          'error=%s detail=%s', LOG_PREFIX,
                          type(error).__name__, detail)
    if original is None:
        _write_marker('%s cleanup_failed stage=game_fini error=unbound',
                      LOG_PREFIX)
        return None
    result = original(*args, **kwargs)
    _write_marker('%s cleanup_original_fini_returned', LOG_PREFIX)
    return result


def init():
    global _runtime, _offline_mode, _original_launch
    global _game_module, _original_game_fini
    _write_marker('%s init_enter argv=%r', LOG_PREFIX, sys.argv)
    if _runtime is not None:
        return _runtime
    request = parse_request(sys.argv)
    if request is None:
        _write_marker('%s inactive reason=explicit_request_missing',
                      LOG_PREFIX)
        return None
    requested_space, map_name = request
    try:
        from helpers import getClientVersion
        actual_version = getClientVersion().strip()
        if actual_version != EXPECTED_CLIENT_VERSION:
            _write_marker('%s version_differs expected=%s actual=%s',
                          LOG_PREFIX, EXPECTED_CLIENT_VERSION,
                          actual_version)
        from helpers import OfflineMode as offline_mode
        import game as game_module
        from gui.mods.offline_battle_2312.runtime import OfflineBattleRuntime
        _runtime = OfflineBattleRuntime(requested_space, map_name,
                                        _write_marker)
        _offline_mode = offline_mode
        _original_launch = offline_mode.launch
        _game_module = game_module
        _original_game_fini = game_module.fini
        offline_mode.launch = _routed_launch
        game_module.fini = _routed_game_fini
        _write_marker('%s route_installed target=helpers.OfflineMode.launch '
                      'request=%s', LOG_PREFIX, requested_space)
        _write_marker('%s route_installed target=game.fini', LOG_PREFIX)
        return _runtime
    except Exception as error:
        detail = repr(error).replace('\n', ' ')[:200]
        _write_marker('%s bootstrap_failed reason=init_error error=%s '
                      'detail=%s', LOG_PREFIX, type(error).__name__, detail)
        _restore_routes()
        _runtime = None
        return None


def fini():
    global _runtime
    runtime = _runtime
    _restore_routes()
    if runtime is not None:
        try:
            runtime.shutdown('mod_fini')
        except Exception:
            pass
    _runtime = None
