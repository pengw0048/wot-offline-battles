from __future__ import absolute_import, print_function

import sys
import time


LOG_PREFIX = '[OFFLINE_2312_AVATAR_ARENA_PROBE]'
EXPECTED_CLIENT_VERSION = 'v.2.3.1.2 #919'
ACTIVATION_TOKEN = 'avatarArenaProbe'
SPACE_PREFIX = 'spaces/'
POLL_INTERVAL_SECONDS = 0.5
LOAD_TIMEOUT_SECONDS = 180.0
SPACE_LOAD_EPS = 0.0001

_probe = None
_offline_mode = None
_original_launch = None
_game_module = None
_original_game_fini = None
_creator = None


def _write_marker(message, *args):
    line = message % args if args else message
    try:
        print(line)
        sys.stdout.flush()
    except Exception:
        pass


_write_marker('%s module_import argv=%r', LOG_PREFIX, sys.argv)


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


def _find_arena_type(arena_cache, map_name):
    matches = []
    for arena_type_id, arena_type in arena_cache.items():
        if (getattr(arena_type, 'geometryName', None) == map_name and
                getattr(arena_type, 'gameplayName', None) == 'ctf'):
            matches.append((arena_type_id, arena_type))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0]


class AvatarArenaProbe(object):
    """Route stock OfflineMode to stock OfflineMapCreator and observe it."""

    def __init__(self, bigworld, creator, arena_cache, requested_space,
                 map_name, logger=None, now=None,
                 poll_interval=POLL_INTERVAL_SECONDS,
                 load_timeout=LOAD_TIMEOUT_SECONDS):
        self._bigworld = bigworld
        self._creator = creator
        self._arena_cache = arena_cache
        self._requested_space = requested_space
        self._map_name = map_name
        self._logger = logger
        self._now = now or time.time
        self._poll_interval = float(poll_interval)
        self._load_timeout = float(load_timeout)
        self._callback_id = None
        self._started_at = None
        self._create_requested_at = None
        self._stopped = False
        self._completed = False
        self._failed = False
        self._avatar_seen = False
        self._arena_seen = False
        self._space_seen = False
        self._reported_errors = set()
        self._arena_type_id = None
        self._stage = 'idle'

    @property
    def callback_id(self):
        return self._callback_id

    @property
    def completed(self):
        return self._completed

    @property
    def failed(self):
        return self._failed

    def _record(self, level, message, *args):
        if self._logger is None:
            _write_marker(message, *args)
            return
        getattr(self._logger, level)(message, *args)

    def _report_error_once(self, key, marker, error):
        if key in self._reported_errors:
            return
        self._reported_errors.add(key)
        self._record('error', '%s %s error=%s', LOG_PREFIX, marker,
                     type(error).__name__)

    def route_launch(self, space_name):
        if self._started_at is not None:
            self._record('info', '%s route_reentered request=%s',
                         LOG_PREFIX, space_name)
            return None
        self._started_at = self._now()
        self._record('info', '%s route_enter request=%s map=%s', LOG_PREFIX,
                     space_name, self._map_name)
        if space_name != self._requested_space:
            self._fail('space_request_changed')
            return None
        self._schedule(0.0)
        return None

    def stop(self, reason='fini'):
        if self._stopped:
            return
        self._stopped = True
        callback_id = self._callback_id
        self._callback_id = None
        if callback_id is not None:
            try:
                self._bigworld.cancelCallback(callback_id)
            except Exception as error:
                self._report_error_once(
                    'cancel_callback', 'callback_cancel_failed', error)
        self._record(
            'info', '%s probe_stop reason=%s completed=%s failed=%s',
            LOG_PREFIX, reason, self._completed, self._failed)

    def _schedule(self, delay=None):
        if (self._stopped or self._completed or self._failed or
                self._callback_id is not None):
            return
        delay = self._poll_interval if delay is None else float(delay)
        try:
            self._callback_id = self._bigworld.callback(
                delay, self._on_callback)
        except Exception as error:
            self._fail('callback_schedule_failed', error)

    def _on_callback(self):
        self._callback_id = None
        try:
            if self._create_requested_at is None:
                self._stage = 'preflight'
                self._try_native_create()
            else:
                self._stage = 'poll'
                self._poll_runtime()
        except Exception as error:
            self._fail('native_exception', error, stage=self._stage)

    def _fail(self, reason, error=None, stage=None, context=None):
        if self._failed or self._completed:
            return
        self._failed = True
        self._stopped = True
        if error is None:
            if context:
                self._record(
                    'error',
                    '%s gate_fail gate=player_arena reason=%s %s',
                    LOG_PREFIX, reason, context)
            else:
                self._record(
                    'error', '%s gate_fail gate=player_arena reason=%s',
                    LOG_PREFIX, reason)
        else:
            try:
                detail = repr(error).replace('\r', ' ').replace('\n', ' ')
            except Exception:
                detail = '<unavailable>'
            self._record(
                'error',
                '%s gate_fail gate=player_arena reason=%s stage=%s '
                'error=%s detail=%s',
                LOG_PREFIX, reason, stage or self._stage,
                type(error).__name__, detail[:200])

    def _safe_player(self):
        try:
            return self._bigworld.player()
        except Exception as error:
            self._report_error_once('player', 'player_lookup_failed', error)
            raise

    def _try_native_create(self):
        if self._stopped or self._completed or self._failed:
            return

        try:
            creator_active = bool(self._creator.Active())
        except Exception as error:
            self._fail('creator_state_failed', error)
            return
        if creator_active:
            self._fail('creator_already_active')
            return
        if self._safe_player() is not None:
            self._fail('player_already_present')
            return

        try:
            arena_match = _find_arena_type(
                self._arena_cache, self._map_name)
        except Exception as error:
            self._report_error_once(
                'arena_cache', 'arena_cache_read_failed', error)
            arena_match = None
        if arena_match is None:
            reason = ('arena_cache_empty' if not self._arena_cache else
                      'arena_not_found')
            self._fail(reason)
            return

        arena_type_id, unused_arena_type = arena_match
        self._arena_type_id = arena_type_id
        self._create_requested_at = self._now()
        self._record(
            'info',
            '%s native_create_requested request=%s map=%s '
            'arena_type_id=%s gameplay=ctf',
            LOG_PREFIX, self._requested_space, self._map_name,
            arena_type_id)
        try:
            self._stage = 'create'
            self._creator.create(self._map_name)
        except Exception as error:
            self._fail('native_exception', error, stage='create')
            return
        try:
            if not self._creator.Active():
                self._fail('creator_inactive_after_create')
                return
        except Exception as error:
            self._fail('creator_state_failed', error)
            return
        self._poll_runtime()

    def _read_status(self):
        try:
            return float(self._bigworld.spaceLoadStatus())
        except Exception as error:
            self._report_error_once(
                'space_status', 'space_load_status_failed', error)
            return -1.0

    def _snapshot(self):
        player = self._safe_player()
        player_type = 'None' if player is None else type(player).__name__
        player_id = None if player is None else getattr(player, 'id', None)
        player_in_world = (None if player is None else
                           getattr(player, 'inWorld', None))
        player_space_id = (None if player is None else
                           getattr(player, 'spaceID', None))
        player_vehicle_id = (None if player is None else
                             getattr(player, 'playerVehicleID', None))
        player_arena_type_id = (None if player is None else
                                getattr(player, 'arenaTypeID', None))
        weather_preset_id = (None if player is None else
                             getattr(player, 'weatherPresetID', None))
        input_handler = (None if player is None else
                         getattr(player, 'inputHandler', None))
        input_type = ('None' if input_handler is None else
                      type(input_handler).__name__)

        arena = None if player is None else getattr(player, 'arena', None)
        arena_type_name = 'None' if arena is None else type(arena).__name__
        arena_type = None if arena is None else getattr(arena, 'arenaType', None)
        geometry_name = (None if arena_type is None else
                         getattr(arena_type, 'geometryName', None))
        gameplay_name = (None if arena_type is None else
                         getattr(arena_type, 'gameplayName', None))

        try:
            camera = self._bigworld.camera()
        except Exception as error:
            self._report_error_once('camera', 'camera_lookup_failed', error)
            camera = None
        camera_type = 'None' if camera is None else type(camera).__name__
        camera_space_id = (None if camera is None else
                           getattr(camera, 'spaceID', None))
        try:
            creator_active = bool(self._creator.Active())
        except Exception as error:
            self._report_error_once(
                'creator_state_poll', 'creator_state_failed', error)
            raise

        return {
            'player_type': player_type,
            'player_id': player_id,
            'player_in_world': player_in_world,
            'player_space_id': player_space_id,
            'player_vehicle_id': player_vehicle_id,
            'player_arena_type_id': player_arena_type_id,
            'weather_preset_id': weather_preset_id,
            'input_type': input_type,
            'arena_type': arena_type_name,
            'geometry_name': geometry_name,
            'gameplay_name': gameplay_name,
            'camera_type': camera_type,
            'camera_space_id': camera_space_id,
            'creator_active': creator_active,
        }

    def _format_snapshot(self, status, snapshot):
        return (
            'status=%.6f creator_active=%s player_type=%s '
            'player_in_world=%s player_space_id=%s arena_type=%s '
            'arena_type_id=%s expected_arena_type_id=%s geometry=%s '
            'gameplay=%s player_vehicle_id=%s input_handler=%s '
            'camera_type=%s camera_space_id=%s' % (
                status, snapshot['creator_active'], snapshot['player_type'],
                snapshot['player_in_world'], snapshot['player_space_id'],
                snapshot['arena_type'], snapshot['player_arena_type_id'],
                self._arena_type_id, snapshot['geometry_name'],
                snapshot['gameplay_name'], snapshot['player_vehicle_id'],
                snapshot['input_type'], snapshot['camera_type'],
                snapshot['camera_space_id']))

    def _timeout_reason(self, status, snapshot):
        if snapshot['player_type'] != 'PlayerAvatar':
            return 'avatar_timeout'
        if not bool(snapshot['player_in_world']):
            return 'avatar_not_in_world'
        if snapshot['arena_type'] != 'ClientArena':
            return 'arena_missing'
        if (snapshot['geometry_name'] != self._map_name or
                snapshot['gameplay_name'] != 'ctf' or
                snapshot['player_arena_type_id'] != self._arena_type_id):
            return 'arena_mismatch'
        if snapshot['player_vehicle_id'] != 0:
            return 'player_vehicle_id_unexpected'
        if snapshot['input_type'] != 'None':
            return 'input_handler_unexpected'
        if snapshot['camera_type'] != 'CursorCamera':
            return 'camera_mismatch'
        if (snapshot['player_space_id'] is None or
                snapshot['player_space_id'] != snapshot['camera_space_id']):
            return 'space_id_mismatch'
        if status <= 1.0 - SPACE_LOAD_EPS:
            return 'space_timeout'
        return 'gate_contract_mismatch'

    def _poll_runtime(self):
        if self._stopped or self._completed or self._failed:
            return
        status = self._read_status()
        snapshot = self._snapshot()
        if not snapshot['creator_active']:
            self._fail('creator_became_inactive')
            return

        if (not self._avatar_seen and
                snapshot['player_type'] == 'PlayerAvatar'):
            self._avatar_seen = True
            self._record(
                'info',
                '%s avatar_seen entity_id=%s in_world=%s space_id=%s',
                LOG_PREFIX, snapshot['player_id'],
                snapshot['player_in_world'], snapshot['player_space_id'])

        if (not self._arena_seen and
                snapshot['arena_type'] == 'ClientArena'):
            self._arena_seen = True
            self._record(
                'info',
                '%s client_arena_seen geometry=%s gameplay=%s',
                LOG_PREFIX, snapshot['geometry_name'],
                snapshot['gameplay_name'])

        if (not self._space_seen and status > 1.0 - SPACE_LOAD_EPS):
            self._space_seen = True
            self._record(
                'info',
                '%s space_loaded status=%.6f player_space_id=%s '
                'camera=%s camera_space_id=%s weather_preset_id=%s',
                LOG_PREFIX, status, snapshot['player_space_id'],
                snapshot['camera_type'], snapshot['camera_space_id'],
                snapshot['weather_preset_id'])

        passed = (
            snapshot['creator_active'] is True and
            status > 1.0 - SPACE_LOAD_EPS and
            snapshot['player_type'] == 'PlayerAvatar' and
            bool(snapshot['player_in_world']) and
            snapshot['arena_type'] == 'ClientArena' and
            snapshot['geometry_name'] == self._map_name and
            snapshot['gameplay_name'] == 'ctf' and
            snapshot['player_arena_type_id'] == self._arena_type_id and
            snapshot['player_vehicle_id'] == 0 and
            snapshot['input_type'] == 'None' and
            snapshot['camera_type'] == 'CursorCamera' and
            snapshot['player_space_id'] is not None and
            snapshot['player_space_id'] == snapshot['camera_space_id'])
        if passed:
            self._completed = True
            self._record(
                'info',
                '%s gate_pass gate=player_arena entity_id=%s space_id=%s '
                'arena_type_id=%s geometry=%s gameplay=%s '
                'player_vehicle_id=%s '
                'input_handler=%s',
                LOG_PREFIX, snapshot['player_id'],
                snapshot['player_space_id'], self._arena_type_id,
                snapshot['geometry_name'], snapshot['gameplay_name'],
                snapshot['player_vehicle_id'], snapshot['input_type'])
            return

        elapsed = self._now() - self._create_requested_at
        if elapsed >= self._load_timeout:
            self._fail(
                self._timeout_reason(status, snapshot),
                context=self._format_snapshot(status, snapshot))
            return
        self._schedule()


def _routed_launch(space_name):
    if _probe is None:
        _write_marker('%s gate_fail gate=player_arena reason=route_unbound',
                      LOG_PREFIX)
        return None
    return _probe.route_launch(space_name)


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
    probe = _probe
    creator = _creator
    if probe is not None:
        probe.stop('game_fini')
    _restore_routes()
    if creator is not None:
        try:
            creator_active = bool(creator.Active())
            if creator_active:
                _write_marker('%s cleanup_destroy_begin creator_active=True',
                              LOG_PREFIX)
                creator.destroy()
                _write_marker(
                    '%s cleanup_destroy_returned creator_active=%s',
                    LOG_PREFIX, bool(creator.Active()))
            else:
                _write_marker(
                    '%s cleanup_destroy_skipped creator_active=False',
                    LOG_PREFIX)
        except Exception as error:
            try:
                detail = repr(error).replace('\r', ' ').replace('\n', ' ')
            except Exception:
                detail = '<unavailable>'
            _write_marker(
                '%s cleanup_failed stage=offline_creator_destroy error=%s '
                'detail=%s', LOG_PREFIX, type(error).__name__, detail[:200])
    if original is None:
        _write_marker('%s cleanup_failed stage=game_fini error=unbound',
                      LOG_PREFIX)
        return None
    try:
        result = original(*args, **kwargs)
    except Exception as error:
        try:
            detail = repr(error).replace('\r', ' ').replace('\n', ' ')
        except Exception:
            detail = '<unavailable>'
        _write_marker(
            '%s cleanup_failed stage=original_game_fini error=%s detail=%s',
            LOG_PREFIX, type(error).__name__, detail[:200])
        raise
    _write_marker('%s cleanup_original_fini_returned', LOG_PREFIX)
    return result


def init(argv=None, bigworld=None, offline_mode=None, creator=None,
         arena_cache=None, game_module=None, logger=None, now=None,
         poll_interval=POLL_INTERVAL_SECONDS,
         load_timeout=LOAD_TIMEOUT_SECONDS, get_client_version=None):
    global _probe, _offline_mode, _original_launch
    global _game_module, _original_game_fini, _creator
    effective_argv = sys.argv if argv is None else argv
    _write_marker('%s init_enter argv=%r', LOG_PREFIX, effective_argv)
    if _probe is not None:
        return _probe

    request = parse_request(effective_argv)
    if request is None:
        _write_marker('%s inactive reason=explicit_request_missing',
                      LOG_PREFIX)
        return None
    requested_space, map_name = request

    try:
        if get_client_version is None:
            from helpers import getClientVersion as get_client_version
        actual_version = get_client_version().strip()
        if actual_version != EXPECTED_CLIENT_VERSION:
            _write_marker(
                '%s version_mismatch expected=%s actual=%s', LOG_PREFIX,
                EXPECTED_CLIENT_VERSION, actual_version)
            return None
        if bigworld is None:
            import BigWorld as bigworld
        if offline_mode is None:
            from helpers import OfflineMode as offline_mode
        if creator is None:
            from OfflineMapCreator import g_offlineMapCreator as creator
        if arena_cache is None:
            from ArenaType import g_cache as arena_cache
        if game_module is None:
            import game as game_module
        _probe = AvatarArenaProbe(
            bigworld, creator, arena_cache, requested_space, map_name,
            logger=logger, now=now, poll_interval=poll_interval,
            load_timeout=load_timeout)
        _offline_mode = offline_mode
        _original_launch = offline_mode.launch
        _game_module = game_module
        _original_game_fini = game_module.fini
        _creator = creator
        offline_mode.launch = _routed_launch
        game_module.fini = _routed_game_fini
        _write_marker(
            '%s route_installed target=helpers.OfflineMode.launch '
            'request=%s', LOG_PREFIX, requested_space)
        _write_marker('%s route_installed target=game.fini', LOG_PREFIX)
        return _probe
    except Exception as error:
        _restore_routes()
        _probe = None
        _offline_mode = None
        _original_launch = None
        _game_module = None
        _original_game_fini = None
        _creator = None
        _write_marker('%s probe_bootstrap_failed error=%s', LOG_PREFIX,
                      type(error).__name__)
        return None


def fini():
    global _probe, _offline_mode, _original_launch
    global _game_module, _original_game_fini, _creator
    probe = _probe
    if probe is not None:
        probe.stop('fini')
    _restore_routes()
    _probe = None
    _offline_mode = None
    _original_launch = None
    _game_module = None
    _original_game_fini = None
    _creator = None
