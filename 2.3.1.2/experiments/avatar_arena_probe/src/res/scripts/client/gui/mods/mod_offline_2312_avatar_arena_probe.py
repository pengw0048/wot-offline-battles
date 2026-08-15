from __future__ import absolute_import, print_function

import sys
import time
import math


LOG_PREFIX = '[OFFLINE_2312_AVATAR_ARENA_PROBE]'
EXPECTED_CLIENT_VERSION = 'v.2.3.1.2 #919'
ACTIVATION_TOKEN = 'avatarArenaProbe'
SPACE_PREFIX = 'spaces/'
POLL_INTERVAL_SECONDS = 0.5
LOAD_TIMEOUT_SECONDS = 180.0
SPACE_LOAD_EPS = 0.0001
INIT_PROGRESS_SPACE_LOADED = 1
INIT_PROGRESS_ENTERED_WORLD = 2
INIT_PROGRESS_REQUIRED = INIT_PROGRESS_ENTERED_WORLD
TERRAIN_COLLISION_MASK = 128
TERRAIN_ONLY_FLAGS = 8
TERRAIN_RAY_HEIGHT = 1000.0
BASE_SPAWN_FORWARD_METRES = 20.0
SPAWN_BOUNDS_MARGIN_METRES = 8.0
CAMERA_PITCH_DEGREES = -25.0
MATURE_CTF_SPAWNS = {
    '01_karelia': ((382.0, 386.0), (-386.0, -386.0)),
}

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


def _vector2_xz(value):
    if value is None:
        return None
    try:
        return float(value.x), float(value.y)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return float(value[0]), float(value[1])
    except (IndexError, TypeError, ValueError):
        return None


def _first_team_point(team_points):
    if not team_points:
        return None
    if isinstance(team_points, dict):
        for key in sorted(team_points):
            point = _vector2_xz(team_points[key])
            if point is not None:
                return point
        return None
    for value in team_points:
        point = _vector2_xz(value)
        if point is not None:
            return point
    return None


def _team_points(value, team_index):
    try:
        return value[team_index]
    except (IndexError, KeyError, TypeError):
        return None


def _camera_spawn_pose(arena_type):
    """Copy the mature CTF spawn rule: stock spawns, then the team base."""
    spawn_points = getattr(arena_type, 'teamSpawnPoints', None)
    base_points = getattr(arena_type, 'teamBasePositions', None)
    geometry_name = getattr(arena_type, 'geometryName', None)
    mature_spawns = MATURE_CTF_SPAWNS.get(geometry_name)
    mature_team_spawn = _vector2_xz(_team_points(mature_spawns, 0))
    team_spawn = (mature_team_spawn or
                  _first_team_point(_team_points(spawn_points, 0)))
    own_base = _first_team_point(_team_points(base_points, 0))
    enemy_base = _first_team_point(_team_points(base_points, 1))

    anchor = team_spawn or own_base
    if anchor is None:
        anchor = (50.0, 50.0)
    x, z = anchor
    heading_anchor = own_base or anchor
    if enemy_base is not None:
        yaw = math.atan2(
            enemy_base[0] - heading_anchor[0],
            enemy_base[1] - heading_anchor[1])
    else:
        yaw = math.atan2(-x, -z)

    source = ('mature_ctf_spawn' if mature_team_spawn is not None else
              'team_spawn')
    if team_spawn is None and own_base is not None:
        source = 'team_base_formation'
        x += math.sin(yaw) * BASE_SPAWN_FORWARD_METRES
        z += math.cos(yaw) * BASE_SPAWN_FORWARD_METRES
    elif team_spawn is None:
        source = 'viewer_fallback'

    bounds = getattr(arena_type, 'boundingBox', None)
    try:
        bottom_left = _vector2_xz(bounds[0])
        upper_right = _vector2_xz(bounds[1])
    except (IndexError, TypeError):
        bottom_left = upper_right = None
    if bottom_left is not None and upper_right is not None:
        x = max(bottom_left[0] + SPAWN_BOUNDS_MARGIN_METRES,
                min(upper_right[0] - SPAWN_BOUNDS_MARGIN_METRES, x))
        z = max(bottom_left[1] + SPAWN_BOUNDS_MARGIN_METRES,
                min(upper_right[1] - SPAWN_BOUNDS_MARGIN_METRES, z))
    return x, z, yaw, source


class AvatarArenaProbe(object):
    """Route stock OfflineMode to stock OfflineMapCreator and observe it."""

    def __init__(self, bigworld, engine_math, creator, arena_cache,
                 requested_space,
                 map_name, avatar_type, arena_bonus_unknown,
                 arena_gui_unknown, logger=None, now=None,
                 poll_interval=POLL_INTERVAL_SECONDS,
                 load_timeout=LOAD_TIMEOUT_SECONDS):
        self._bigworld = bigworld
        self._math = engine_math
        self._creator = creator
        self._arena_cache = arena_cache
        self._requested_space = requested_space
        self._map_name = map_name
        self._avatar_type = avatar_type
        self._arena_bonus_unknown = arena_bonus_unknown
        self._arena_gui_unknown = arena_gui_unknown
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
        self._space_lifecycle_reported = False
        self._reported_errors = set()
        self._arena_type_id = None
        self._arena_type = None
        self._camera_repositioned = False
        self._stage = 'idle'
        self._display_state_reported = False
        self._avatar_init_object = None
        self._avatar_preseed_applied = False
        self._avatar_init_returned = False
        self._has_bonus_cap_calls = 0
        self._has_bonus_cap_exceptions = 0
        self._on_enter_world_calls = 0
        self._on_enter_world_returns = 0
        self._on_enter_world_exceptions = 0
        self._on_become_player_calls = 0
        self._on_become_player_returns = 0
        self._on_become_player_exceptions = 0

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
        self._record_display_state()
        if space_name != self._requested_space:
            self._fail('space_request_changed')
            return None
        self._schedule(0.0)
        return None

    def _optional_native_value(self, name, *args):
        try:
            value = getattr(self._bigworld, name)
            if callable(value):
                value = value(*args)
            text = repr(value).replace('\r', ' ').replace('\n', ' ')
            return text[:160]
        except Exception as error:
            return '<unavailable:%s>' % type(error).__name__

    def _record_display_state(self):
        if self._display_state_reported:
            return
        self._display_state_reported = True
        try:
            window_mode = self._bigworld.getWindowMode()
            window_mode_text = repr(window_mode)
        except Exception as error:
            window_mode = None
            window_mode_text = '<unavailable:%s>' % type(error).__name__
        mode_args = () if window_mode is None else (window_mode,)
        resolution = (
            '<unavailable:window_mode>' if not mode_args else
            self._optional_native_value(
                'wg_getCurrentResolution', *mode_args))
        active_monitor = (
            '<unavailable:window_mode>' if not mode_args else
            self._optional_native_value('getActiveMonitorIndex', *mode_args))
        self._record(
            'info',
            '%s display_state window_mode=%s resolution=%s '
            'video_mode_index=%s active_monitor=%s borderless=%s '
            'vsync=%s triple_buffered=%s drr_scale=%s drr_auto=%s '
            'gamma=%s',
            LOG_PREFIX, window_mode_text, resolution,
            self._optional_native_value('videoModeIndex'), active_monitor,
            self._optional_native_value('getBorderlessParameters'),
            self._optional_native_value('isVideoVSync'),
            self._optional_native_value('isTripleBuffered'),
            self._optional_native_value('getDRRScale'),
            self._optional_native_value('isDRRAutoscalingEnabled'),
            self._optional_native_value('getGammaCorrection'))

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
                    '%s bootstrap_failed reason=%s %s',
                    LOG_PREFIX, reason, context)
            else:
                self._record(
                    'error', '%s bootstrap_failed reason=%s',
                    LOG_PREFIX, reason)
        else:
            try:
                detail = repr(error).replace('\r', ' ').replace('\n', ' ')
            except Exception:
                detail = '<unavailable>'
            self._record(
                'error',
                '%s bootstrap_failed reason=%s stage=%s '
                'error=%s detail=%s',
                LOG_PREFIX, reason, stage or self._stage,
                type(error).__name__, detail[:200])

    def _safe_player(self):
        try:
            return self._bigworld.player()
        except Exception as error:
            self._report_error_once('player', 'player_lookup_failed', error)
            raise

    def _create_with_avatar_preseed(self):
        avatar_type = self._avatar_type
        original_init = avatar_type.__dict__.get('__init__')
        original_has_bonus_cap = avatar_type.__dict__.get('hasBonusCap')
        original_on_enter_world = avatar_type.__dict__.get('onEnterWorld')
        original_on_become_player = avatar_type.__dict__.get(
            'onBecomePlayer')
        if (original_init is None or original_has_bonus_cap is None or
                original_on_enter_world is None or
                original_on_become_player is None):
            self._fail('avatar_route_missing', stage='create')
            return False

        probe = self

        def routed_avatar_init(avatar, *args, **kwargs):
            probe._avatar_init_object = avatar
            avatar.arenaUniqueID = 0
            avatar.arenaTypeID = probe._arena_type_id
            avatar.arenaBonusType = probe._arena_bonus_unknown
            avatar.arenaGuiType = probe._arena_gui_unknown
            avatar.arenaExtraData = {}
            avatar.weatherPresetID = 0
            avatar.bonusCapsOverrides = None
            probe._avatar_preseed_applied = True
            result = original_init(avatar, *args, **kwargs)
            probe._avatar_init_returned = True
            return result

        def routed_has_bonus_cap(avatar, *args, **kwargs):
            probe._has_bonus_cap_calls += 1
            try:
                return original_has_bonus_cap(avatar, *args, **kwargs)
            except Exception:
                probe._has_bonus_cap_exceptions += 1
                raise

        def routed_on_enter_world(avatar, *args, **kwargs):
            probe._on_enter_world_calls += 1
            try:
                result = original_on_enter_world(avatar, *args, **kwargs)
            except Exception:
                probe._on_enter_world_exceptions += 1
                raise
            probe._on_enter_world_returns += 1
            return result

        def routed_on_become_player(avatar, *args, **kwargs):
            probe._on_become_player_calls += 1
            try:
                result = original_on_become_player(avatar, *args, **kwargs)
            except Exception:
                probe._on_become_player_exceptions += 1
                raise
            probe._on_become_player_returns += 1
            return result

        init_installed = False
        bonus_cap_installed = False
        enter_world_installed = False
        become_player_installed = False
        restore_failed = False
        try:
            avatar_type.__init__ = routed_avatar_init
            init_installed = (
                avatar_type.__dict__.get('__init__') is routed_avatar_init)
            if not init_installed:
                raise RuntimeError('avatar init route was not installed')
            avatar_type.hasBonusCap = routed_has_bonus_cap
            bonus_cap_installed = (
                avatar_type.__dict__.get('hasBonusCap') is
                routed_has_bonus_cap)
            if not bonus_cap_installed:
                raise RuntimeError('bonus-cap route was not installed')
            avatar_type.onEnterWorld = routed_on_enter_world
            enter_world_installed = (
                avatar_type.__dict__.get('onEnterWorld') is
                routed_on_enter_world)
            if not enter_world_installed:
                raise RuntimeError('enter-world route was not installed')
            avatar_type.onBecomePlayer = routed_on_become_player
            become_player_installed = (
                avatar_type.__dict__.get('onBecomePlayer') is
                routed_on_become_player)
            if not become_player_installed:
                raise RuntimeError('become-player route was not installed')
            self._creator.create(self._map_name)
        finally:
            if init_installed:
                try:
                    if (avatar_type.__dict__.get('__init__') is
                            routed_avatar_init):
                        avatar_type.__init__ = original_init
                        if (avatar_type.__dict__.get('__init__') is not
                                original_init):
                            restore_failed = True
                    else:
                        restore_failed = True
                except Exception:
                    restore_failed = True
            if enter_world_installed:
                try:
                    if (avatar_type.__dict__.get('onEnterWorld') is
                            routed_on_enter_world):
                        avatar_type.onEnterWorld = original_on_enter_world
                        if (avatar_type.__dict__.get('onEnterWorld') is not
                                original_on_enter_world):
                            restore_failed = True
                    else:
                        restore_failed = True
                except Exception:
                    restore_failed = True
            if become_player_installed:
                try:
                    if (avatar_type.__dict__.get('onBecomePlayer') is
                            routed_on_become_player):
                        avatar_type.onBecomePlayer = original_on_become_player
                        if (avatar_type.__dict__.get('onBecomePlayer') is not
                                original_on_become_player):
                            restore_failed = True
                    else:
                        restore_failed = True
                except Exception:
                    restore_failed = True
            if bonus_cap_installed:
                try:
                    if (avatar_type.__dict__.get('hasBonusCap') is
                            routed_has_bonus_cap):
                        avatar_type.hasBonusCap = original_has_bonus_cap
                        if (avatar_type.__dict__.get('hasBonusCap') is not
                                original_has_bonus_cap):
                            restore_failed = True
                    else:
                        restore_failed = True
                except Exception:
                    restore_failed = True
        if restore_failed:
            self._fail('avatar_route_restore_failed', stage='create')
            return False
        return True

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
        self._arena_type = unused_arena_type
        self._create_requested_at = self._now()
        self._record(
            'info',
            '%s native_create_requested request=%s map=%s '
            'arena_type_id=%s gameplay=ctf',
            LOG_PREFIX, self._requested_space, self._map_name,
            arena_type_id)
        try:
            self._stage = 'create'
            if not self._create_with_avatar_preseed():
                return
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
        self._record(
            'info',
            '%s avatar_init_observed preseed_applied=%s init_returned=%s '
            'has_bonus_cap_calls=%s has_bonus_cap_exceptions=%s',
            LOG_PREFIX, self._avatar_preseed_applied,
            self._avatar_init_returned, self._has_bonus_cap_calls,
            self._has_bonus_cap_exceptions)
        if not self._avatar_preseed_applied:
            self._fail('avatar_preseed_missing', stage='create')
            return
        if not self._avatar_init_returned:
            self._fail('avatar_init_incomplete', stage='create')
            return
        if self._has_bonus_cap_calls <= 0:
            self._fail('bonus_cap_check_missing', stage='create')
            return
        if self._has_bonus_cap_exceptions:
            self._fail('bonus_cap_check_failed', stage='create')
            return
        self._record(
            'info',
            '%s avatar_lifecycle_observed enter_world_calls=%s '
            'enter_world_returns=%s enter_world_exceptions=%s '
            'become_player_calls=%s become_player_returns=%s '
            'become_player_exceptions=%s',
            LOG_PREFIX, self._on_enter_world_calls,
            self._on_enter_world_returns, self._on_enter_world_exceptions,
            self._on_become_player_calls, self._on_become_player_returns,
            self._on_become_player_exceptions)
        if self._on_enter_world_calls <= 0:
            self._fail('enter_world_missing', stage='create')
            return
        if (self._on_enter_world_exceptions or
                self._on_enter_world_returns != self._on_enter_world_calls):
            self._fail('enter_world_failed', stage='create')
            return
        if self._on_become_player_calls <= 0:
            self._fail('become_player_missing', stage='create')
            return
        if (self._on_become_player_exceptions or
                self._on_become_player_returns !=
                self._on_become_player_calls):
            self._fail('become_player_failed', stage='create')
            return
        self._stage = 'poll'
        self._poll_runtime()

    def _read_status(self):
        try:
            return float(self._bigworld.spaceLoadStatus())
        except Exception as error:
            self._report_error_once(
                'space_status', 'space_load_status_failed', error)
            return -1.0

    def _reposition_camera(self, camera):
        if self._camera_repositioned:
            return True
        if camera is None or type(camera).__name__ != 'CursorCamera':
            return False
        space_id = getattr(camera, 'spaceID', None)
        if space_id is None or self._arena_type is None:
            return False
        x, z, yaw, source_name = _camera_spawn_pose(self._arena_type)
        start = self._math.Vector3(x, TERRAIN_RAY_HEIGHT, z)
        end = self._math.Vector3(x, -TERRAIN_RAY_HEIGHT, z)
        collision = self._bigworld.wg_collideSegment(
            int(space_id), start, end, TERRAIN_COLLISION_MASK,
            TERRAIN_ONLY_FLAGS)
        if collision is None:
            return False
        closest = collision.closestPoint
        ground_x = float(closest.x)
        ground_y = float(closest.y)
        ground_z = float(closest.z)

        target = self._math.Matrix()
        target.setTranslate(self._math.Vector3(
            ground_x, ground_y, ground_z))
        camera.target = target

        source = self._math.Matrix()
        source.setRotateYPR((yaw, math.radians(CAMERA_PITCH_DEGREES), 0.0))
        camera.source = source
        camera.forceUpdate()

        self._camera_repositioned = True
        self._record(
            'info',
            '%s camera_repositioned source=%s target=(%.3f,%.3f,%.3f) '
            'yaw=%.6f pitch=%.3f',
            LOG_PREFIX, source_name, ground_x, ground_y, ground_z, yaw,
            CAMERA_PITCH_DEGREES)
        return True

    def _snapshot(self):
        player = self._safe_player()
        missing = object()
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
        arena_unique_id = (None if player is None else
                           getattr(player, 'arenaUniqueID', None))
        arena_bonus_type = (None if player is None else
                            getattr(player, 'arenaBonusType', None))
        arena_gui_type = (None if player is None else
                          getattr(player, 'arenaGuiType', None))
        arena_extra_data = (missing if player is None else
                            getattr(player, 'arenaExtraData', missing))
        bonus_caps_overrides = (missing if player is None else
                                getattr(player, 'bonusCapsOverrides', missing))
        init_progress = (None if player is None else getattr(
            player, '_PlayerAvatar__initProgress', None))
        try:
            init_progress_value = int(init_progress)
        except (TypeError, ValueError):
            init_progress_value = None
        init_progress_ready = (
            init_progress_value is not None and
            init_progress_value & INIT_PROGRESS_REQUIRED ==
            INIT_PROGRESS_REQUIRED)
        space_lifecycle_ready = (
            init_progress_value is not None and
            init_progress_value & INIT_PROGRESS_SPACE_LOADED ==
            INIT_PROGRESS_SPACE_LOADED)
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
            'arena_unique_id': arena_unique_id,
            'arena_bonus_type': arena_bonus_type,
            'arena_gui_type': arena_gui_type,
            'arena_extra_data_empty': arena_extra_data == {},
            'bonus_caps_present': bonus_caps_overrides is not missing,
            'bonus_caps_none': bonus_caps_overrides is None,
            'init_progress': init_progress_value,
            'init_progress_ready': init_progress_ready,
            'space_lifecycle_ready': space_lifecycle_ready,
            'routed_avatar': player is self._avatar_init_object,
            'preseed_applied': self._avatar_preseed_applied,
            'avatar_init_returned': self._avatar_init_returned,
            'has_bonus_cap_calls': self._has_bonus_cap_calls,
            'has_bonus_cap_exceptions': self._has_bonus_cap_exceptions,
            'enter_world_calls': self._on_enter_world_calls,
            'enter_world_returns': self._on_enter_world_returns,
            'enter_world_exceptions': self._on_enter_world_exceptions,
            'become_player_calls': self._on_become_player_calls,
            'become_player_returns': self._on_become_player_returns,
            'become_player_exceptions':
                self._on_become_player_exceptions,
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
            'camera_type=%s camera_space_id=%s routed_avatar=%s '
            'preseed_applied=%s avatar_init_returned=%s '
            'init_progress=%s entered_world_ready=%s '
            'player_space_loaded=%s '
            'has_bonus_cap_calls=%s has_bonus_cap_exceptions=%s '
            'enter_world_calls=%s enter_world_returns=%s '
            'enter_world_exceptions=%s become_player_calls=%s '
            'become_player_returns=%s become_player_exceptions=%s '
            'arena_unique_id=%s arena_bonus_type=%s arena_gui_type=%s '
            'arena_extra_data_empty=%s bonus_caps_present=%s '
            'bonus_caps_none=%s weather_preset_id=%s' % (
                status, snapshot['creator_active'], snapshot['player_type'],
                snapshot['player_in_world'], snapshot['player_space_id'],
                snapshot['arena_type'], snapshot['player_arena_type_id'],
                self._arena_type_id, snapshot['geometry_name'],
                snapshot['gameplay_name'], snapshot['player_vehicle_id'],
                snapshot['input_type'], snapshot['camera_type'],
                snapshot['camera_space_id'], snapshot['routed_avatar'],
                snapshot['preseed_applied'],
                snapshot['avatar_init_returned'],
                snapshot['init_progress'], snapshot['init_progress_ready'],
                snapshot['space_lifecycle_ready'],
                snapshot['has_bonus_cap_calls'],
                snapshot['has_bonus_cap_exceptions'],
                snapshot['enter_world_calls'],
                snapshot['enter_world_returns'],
                snapshot['enter_world_exceptions'],
                snapshot['become_player_calls'],
                snapshot['become_player_returns'],
                snapshot['become_player_exceptions'],
                snapshot['arena_unique_id'], snapshot['arena_bonus_type'],
                snapshot['arena_gui_type'],
                snapshot['arena_extra_data_empty'],
                snapshot['bonus_caps_present'], snapshot['bonus_caps_none'],
                snapshot['weather_preset_id']))

    def _timeout_reason(self, status, snapshot):
        if snapshot['player_type'] != 'PlayerAvatar':
            return 'avatar_timeout'
        if not snapshot['routed_avatar']:
            return 'avatar_instance_mismatch'
        if not snapshot['preseed_applied']:
            return 'avatar_preseed_missing'
        if not snapshot['avatar_init_returned']:
            return 'avatar_init_incomplete'
        if snapshot['has_bonus_cap_calls'] <= 0:
            return 'bonus_cap_check_missing'
        if snapshot['has_bonus_cap_exceptions']:
            return 'bonus_cap_check_failed'
        if snapshot['enter_world_calls'] <= 0:
            return 'enter_world_missing'
        if (snapshot['enter_world_exceptions'] or
                snapshot['enter_world_returns'] !=
                snapshot['enter_world_calls']):
            return 'enter_world_failed'
        if snapshot['become_player_calls'] <= 0:
            return 'become_player_missing'
        if (snapshot['become_player_exceptions'] or
                snapshot['become_player_returns'] !=
                snapshot['become_player_calls']):
            return 'become_player_failed'
        if not snapshot['init_progress_ready']:
            return 'avatar_enter_world_timeout'
        if (snapshot['arena_unique_id'] != 0 or
                snapshot['arena_bonus_type'] != self._arena_bonus_unknown or
                snapshot['arena_gui_type'] != self._arena_gui_unknown or
                not snapshot['arena_extra_data_empty'] or
                not snapshot['bonus_caps_present'] or
                not snapshot['bonus_caps_none'] or
                snapshot['weather_preset_id'] != 0):
            return 'avatar_properties_mismatch'
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
        return 'bootstrap_state_mismatch'

    def _poll_runtime(self):
        if self._stopped or self._completed or self._failed:
            return
        status = self._read_status()
        snapshot = self._snapshot()
        if not snapshot['creator_active']:
            self._fail('creator_became_inactive')
            return

        if status > 1.0 - SPACE_LOAD_EPS:
            try:
                camera = self._bigworld.camera()
                if not self._reposition_camera(camera):
                    elapsed = self._now() - self._create_requested_at
                    if elapsed >= self._load_timeout:
                        self._fail('camera_ground_timeout', stage='camera')
                        return
                    self._schedule()
                    return
            except Exception as error:
                self._fail('camera_reposition_failed', error, stage='camera')
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
                '%s geometry_loaded status=%.6f player_space_id=%s '
                'camera=%s camera_space_id=%s weather_preset_id=%s '
                'player_space_loaded=%s',
                LOG_PREFIX, status, snapshot['player_space_id'],
                snapshot['camera_type'], snapshot['camera_space_id'],
                snapshot['weather_preset_id'],
                snapshot['space_lifecycle_ready'])
        if (status > 1.0 - SPACE_LOAD_EPS and
                not snapshot['space_lifecycle_ready'] and
                not self._space_lifecycle_reported):
            self._space_lifecycle_reported = True
            self._record(
                'info',
                '%s space_lifecycle_missing '
                'reason=offline_battle_session_not_started '
                'init_progress=%s',
                LOG_PREFIX, snapshot['init_progress'])

        passed = (
            snapshot['creator_active'] is True and
            status > 1.0 - SPACE_LOAD_EPS and
            snapshot['player_type'] == 'PlayerAvatar' and
            snapshot['routed_avatar'] and
            snapshot['preseed_applied'] and
            snapshot['avatar_init_returned'] and
            snapshot['has_bonus_cap_calls'] > 0 and
            snapshot['has_bonus_cap_exceptions'] == 0 and
            snapshot['enter_world_calls'] > 0 and
            snapshot['enter_world_returns'] ==
                snapshot['enter_world_calls'] and
            snapshot['enter_world_exceptions'] == 0 and
            snapshot['become_player_calls'] > 0 and
            snapshot['become_player_returns'] ==
                snapshot['become_player_calls'] and
            snapshot['become_player_exceptions'] == 0 and
            snapshot['init_progress_ready'] and
            bool(snapshot['player_in_world']) and
            snapshot['arena_type'] == 'ClientArena' and
            snapshot['geometry_name'] == self._map_name and
            snapshot['gameplay_name'] == 'ctf' and
            snapshot['player_arena_type_id'] == self._arena_type_id and
            snapshot['arena_unique_id'] == 0 and
            snapshot['arena_bonus_type'] == self._arena_bonus_unknown and
            snapshot['arena_gui_type'] == self._arena_gui_unknown and
            snapshot['arena_extra_data_empty'] and
            snapshot['bonus_caps_present'] and
            snapshot['bonus_caps_none'] and
            snapshot['weather_preset_id'] == 0 and
            snapshot['player_vehicle_id'] == 0 and
            snapshot['input_type'] == 'None' and
            snapshot['camera_type'] == 'CursorCamera' and
            snapshot['player_space_id'] is not None and
            snapshot['player_space_id'] == snapshot['camera_space_id'])
        if passed:
            self._completed = True
            self._record(
                'info',
                '%s bootstrap_ready entity_id=%s space_id=%s '
                'arena_type_id=%s geometry=%s gameplay=%s '
                'player_vehicle_id=%s input_handler=%s init_progress=%s '
                'has_bonus_cap_calls=%s enter_world_calls=%s '
                'become_player_calls=%s player_space_loaded=%s',
                LOG_PREFIX, snapshot['player_id'],
                snapshot['player_space_id'], self._arena_type_id,
                snapshot['geometry_name'], snapshot['gameplay_name'],
                snapshot['player_vehicle_id'], snapshot['input_type'],
                snapshot['init_progress'],
                snapshot['has_bonus_cap_calls'],
                snapshot['enter_world_calls'],
                snapshot['become_player_calls'],
                snapshot['space_lifecycle_ready'])
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
        _write_marker('%s bootstrap_failed reason=route_unbound',
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
         arena_cache=None, game_module=None, avatar_type=None,
         arena_bonus_unknown=None, arena_gui_unknown=None, logger=None,
         now=None,
         poll_interval=POLL_INTERVAL_SECONDS,
         load_timeout=LOAD_TIMEOUT_SECONDS, get_client_version=None,
         engine_math=None):
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
        if engine_math is None:
            import Math as engine_math
        if offline_mode is None:
            from helpers import OfflineMode as offline_mode
        if creator is None:
            from OfflineMapCreator import g_offlineMapCreator as creator
        if arena_cache is None:
            from ArenaType import g_cache as arena_cache
        if game_module is None:
            import game as game_module
        if avatar_type is None:
            from Avatar import PlayerAvatar as avatar_type
        if arena_bonus_unknown is None or arena_gui_unknown is None:
            import constants
            if arena_bonus_unknown is None:
                arena_bonus_unknown = constants.ARENA_BONUS_TYPE.UNKNOWN
            if arena_gui_unknown is None:
                arena_gui_unknown = constants.ARENA_GUI_TYPE.UNKNOWN
        _probe = AvatarArenaProbe(
            bigworld, engine_math, creator, arena_cache, requested_space,
            map_name,
            avatar_type, arena_bonus_unknown, arena_gui_unknown,
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
