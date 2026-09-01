from __future__ import print_function

import sys
import traceback

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle


OFFLINE_SERVER_ADDRESS = 'offline-lan.local:0'
_OFFLINE_ACCOUNT_NAME = 'offline_account'
_OFFLINE_INIT_COMPLETE = '_offlineLANInitComplete'
_OFFLINE_PLAYER_READY = '_offlineLANPlayerReady'
_OFFLINE_RETIRE_PENDING = '_offlineLANRetirePending'
_account_settings_pinned = False
# AccountSettings.DEFAULT_VALUES keys whose sections this port adopts once.
_SETTINGS_KEYS = ('settings', 'filters', 'counters', 'notifications')


def _entity_bytes(value, default=''):
    """Return the exact byte-string shape expected by BigWorld STRING."""
    if value is None:
        value = default
    if isinstance(value, bytes):
        return value
    try:
        return value.encode('utf-8')
    except AttributeError:
        return str(value)

_SERVER_SETTINGS = {
    'file_server': {
        'clan_emblems': {'url_template': '', 'cache_life_time': 0},
        'clan_emblems_small': {'url_template': '', 'cache_life_time': 0},
    },
    'regional_settings': {
        'starting_time_of_a_new_day': 0,
        'starting_day_of_a_new_week': 0,
        'starting_time_of_a_new_game_day': 3,
    },
    # ServerSettings consumes the first three values, while the exact #1513
    # predefined-host list directly indexes the fourth roaming-host list.
    'roaming': (0, 0, [], []),
    'wallet': (1, 1),
    # The #1513 Vehicle entity ignores stunInfo and emits one warning per
    # vehicle when this server-owned feature is absent or disabled.  The
    # offline battle authority already publishes canonical SPG stun state.
    'spgRedesignFeatures': {
        'stunEnabled': True,
        'markTargetAreaEnabled': False,
    },
    # ClientRanked indexes this setting directly even when ranked battles are
    # disabled.  Presence, rather than truthiness, is the native contract.
    'ranked_config': {'isEnabled': False},
    # The exact #1513 default is enabled when this section is absent.  The
    # event-board client then starts HTTP-backed clan/event synchronization,
    # which has no producer in the offline account service.
    'elenSettings': {
        'isElenEnabled': False,
        'elenUpdateInterval': 60,
    },
    'isEncyclopediaEnabled': 'all',
    'isVehiclesCompareEnabled': True,
    'isCustomizationEnabled': True,
    # The retail default is enabled.  Offline accounts have no tutorial
    # service, and letting the hints player start leaves weak GUI proxies that
    # raise during game.fini().  Public 0.9.x offline servers disable the same
    # server-owned feature and publish a completed tutorial bitmask.
    'isTutorialEnabled': False,
}

_LOBBY_GUI_CONTEXT = {
    'databaseID': 1,
    'logUXEvents': False,
    'aogasStartedAt': 0,
    'sessionStartedAt': 0,
    'isAogasEnabled': False,
    'collectUiStats': False,
    'isLongDisconnectedFromCenter': False,
}


def _account_settings_module():
    # account_helpers/__init__ shadows the submodule name with the class,
    # so resolve the module the way the interpreter recorded it.
    import account_helpers.AccountSettings  # noqa: F401
    return sys.modules['account_helpers.AccountSettings']


def _account_sections(settings_type):
    """Return the preferences node that holds every ``<account>`` section."""
    import Settings
    return settings_type._AccountSettings__readSection(
        Settings.g_instance.userPrefs, Settings.KEY_ACCOUNT_SETTINGS)


def _named_account_section(accounts, login):
    for key, candidate in accounts.items():
        if key == 'account' and candidate.readString('login') == login:
            return candidate
    return None


def _adopt_stray_settings(settings_type, accounts, section):
    """Copy settings written before the pin into the pinned section, once.

    Everything the player saved while the client had no name landed under an
    empty ``<login>``; without this they would have to set it all again.
    """
    stray = _named_account_section(accounts, '')
    if stray is None:
        return 0
    adopted = 0
    for key in (_SETTINGS_KEYS or ()):
        source = stray[key] if stray.has_key(key) else None
        if source is None:
            continue
        target = settings_type._AccountSettings__readSection(section, key)
        for name, value in source.items():
            if target.has_key(name):
                continue
            target.writeString(name, value.asString)
            adopted += 1
    if adopted:
        print('[Offline LAN 0.9.22] adopted %d saved interface setting(s) '
              'from the unnamed profile' % adopted)
    return adopted


def _offline_user_section(account_settings):
    """Return the single ``<account>`` preferences section this port owns.

    #1513 ``AccountSettings.__readUserSection`` keys the section on
    ``BigWorld.player().name``, and both ``__getValue`` and ``__setValue`` go
    through it.  Offline that name is the account in the lobby, the LAN roster
    name in battle and empty with no player at all, so saved settings scatter
    across profiles and read back as defaults.
    """
    settings_type = account_settings.AccountSettings
    if settings_type._AccountSettings__isFirstRun:
        settings_type.convert()
        settings_type.invalidateNewSettingsCounter()
        settings_type._AccountSettings__isFirstRun = False
    cache = settings_type._AccountSettings__cache
    if cache['login'] != _OFFLINE_ACCOUNT_NAME:
        accounts = _account_sections(settings_type)
        section = _named_account_section(accounts, _OFFLINE_ACCOUNT_NAME)
        if section is None:
            section = accounts.createSection('account')
            section.writeString('login', _OFFLINE_ACCOUNT_NAME)
        _adopt_stray_settings(settings_type, accounts, section)
        cache['login'] = _OFFLINE_ACCOUNT_NAME
        cache['section'] = section
    return cache['section']


def pin_account_settings(account_settings=None):
    """Pin every AccountSettings read and write to one offline profile.

    This must outlive the account: an earlier build installed it with the rest
    of the compatibility layer, so every disconnect removed it and the settings
    the player saved after a battle went back to an unnamed profile.
    """
    global _account_settings_pinned
    if _account_settings_pinned:
        return False
    if account_settings is None:
        account_settings = _account_settings_module()
    settings_type = account_settings.AccountSettings

    def offline_user_section():
        return _offline_user_section(account_settings)

    settings_type._AccountSettings__readUserSection = staticmethod(
        offline_user_section)
    _account_settings_pinned = True
    print('[Offline LAN 0.9.22] interface settings pinned to the %r profile'
          % _OFFLINE_ACCOUNT_NAME)
    return True


def _sanitize_account_filters(account_settings=None):
    """Make every keyed lobby filter carry exactly the default keys.

    An empty default declares no key schema.  #1513 stores a set of shown
    promo URLs under such a default, so those filters keep their saved value.
    """
    import copy
    if account_settings is None:
        account_settings = _account_settings_module()
    settings_type = account_settings.AccountSettings
    defaults = account_settings.DEFAULT_VALUES[account_settings.KEY_FILTERS]
    repaired = []
    for name, default in defaults.items():
        if not isinstance(default, dict) or not default:
            continue
        saved = settings_type.getFilter(name)
        if isinstance(saved, dict):
            unknown = sorted(key for key in saved if key not in default)
            missing = sorted(key for key in default if key not in saved)
            if not unknown and not missing:
                continue
            value = dict((key, saved[key]) for key in saved
                         if key in default)
            for key in missing:
                value[key] = copy.deepcopy(default[key])
            print('[Offline LAN 0.9.22] repaired saved lobby filter %s: '
                  'dropped %r, added %r' % (name, unknown, missing))
        else:
            value = copy.deepcopy(default)
            print('[Offline LAN 0.9.22] replaced non-mapping saved lobby '
                  'filter %s (%r)' % (name, type(saved).__name__))
        settings_type.setFilter(name, value)
        repaired.append(name)
    return repaired


def _load_runtime():
    import Account
    import Avatar
    import AvatarInputHandler
    import AvatarInputHandler.control_modes as ControlModes
    import AvatarPositionControl
    import BigWorld
    import ChatManager
    import Math
    import ProjectileMover
    import SoundGroups
    import Vehicle
    import VehicleGunRotator
    from AvatarInputHandler.DynamicCameras import AccelerationSmoother
    from AvatarInputHandler.DynamicCameras.ArcadeCamera import ArcadeCamera
    from AvatarInputHandler.DynamicCameras.SniperCamera import SniperCamera
    from AvatarInputHandler.DynamicCameras.StrategicCamera import \
        StrategicCamera
    import AvatarInputHandler.AimingSystems.steady_vehicle_matrix as \
        SteadyVehicleMatrix
    import vehicle_systems.CompoundAppearance as CompoundAppearanceModule
    from vehicle_systems.components.CrashedTracks import \
        CrashedTrackController
    import constants
    from OfflineMapCreator import g_offlineMapCreator
    from PlayerEvents import g_playerEvents
    from connection_mgr import LOGIN_STATUS
    from gui.battle_control import avatar_getter
    from gui.battle_control.controllers.consumables.ammo_ctrl import \
        AmmoController
    from gui.Scaleform.daapi.view.battle.shared.debug_panel import DebugPanel
    from gui.Scaleform.daapi.view.battle.shared.markers2d.plugins import \
        VehicleMarkerPlugin
    from gui.Scaleform.daapi.view.battle.shared.markers2d import settings as \
        VehicleMarkerSettings
    from gui.prb_control.dispatcher import g_prbLoader
    from helpers import dependency
    from predefined_hosts import g_preDefinedHosts
    from skeletons.connection_mgr import IConnectionManager
    from gui.mods.offline_lan_0922.entities.remote_vehicle import (
        _RemoteFilter, collide_vehicle_at_matrix)

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.account_module = Account
    runtime.avatar_module = Avatar
    runtime.avatar_input_handler = AvatarInputHandler
    runtime.avatar_getter = avatar_getter
    runtime.ammo_controller_type = AmmoController
    runtime.control_modes = ControlModes
    runtime.avatar_position_control = AvatarPositionControl
    runtime.acceleration_smoother_type = AccelerationSmoother
    runtime.arcade_camera_type = ArcadeCamera
    runtime.bigworld = BigWorld
    runtime.chat_manager = ChatManager.chatManager
    runtime.compound_appearance_module = CompoundAppearanceModule
    runtime.crashed_tracks_controller_type = CrashedTrackController
    runtime.constants = constants
    runtime.connection_manager = dependency.instance(IConnectionManager)
    runtime.debug_panel_type = DebugPanel
    runtime.login_status = LOGIN_STATUS
    runtime.math = Math
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.player_events = g_playerEvents
    runtime.projectile_mover_module = ProjectileMover
    runtime.predefined_hosts = g_preDefinedHosts
    runtime.prb_loader = g_prbLoader
    runtime.remote_filter_type = _RemoteFilter
    runtime.segment_collision_result_type = Vehicle.SegmentCollisionResult
    runtime.sound_groups_module = SoundGroups
    runtime.sniper_camera_type = SniperCamera
    runtime.strategic_camera_type = StrategicCamera
    runtime.steady_vehicle_matrix = SteadyVehicleMatrix
    runtime.vehicle_module = Vehicle
    runtime.vehicle_marker_plugin_type = VehicleMarkerPlugin
    runtime.vehicle_marker_damage_type = VehicleMarkerSettings.DAMAGE_TYPE
    runtime.vehicle_gun_rotator = VehicleGunRotator
    runtime.visible_vehicle_collision = collide_vehicle_at_matrix
    return runtime


class _FallbackServer(object):

    def __getattr__(self, name):
        def ignored(*args, **kwargs):
            return None

        return ignored


class _DeferredAvatarServer(object):
    """Exact early Avatar requests before the entity binding is available."""

    def __init__(self):
        self._target = None
        self._pending = []

    @property
    def voipController(self):
        return self

    def attach(self, target):
        if self._target is not None and self._target is not target:
            raise RuntimeError('Avatar server is already attached')
        self._target = target
        pending = self._pending
        self._pending = []
        for name, args in pending:
            getattr(target, name)(*args)

    def invalidateMicrophoneMute(self):
        if self._target is not None:
            return self._target.invalidateMicrophoneMute()
        return None

    def switchObserverFPV(self, enabled):
        if self._target is not None:
            return self._target.switchObserverFPV(enabled)
        return None

    def setClientReady(self):
        # BigWorld may finish Vehicle prerequisites and the stock Avatar init
        # from inside createEntity(), before BattleRuntime receives the id and
        # attaches its concrete bridge.  Preserve that readiness barrier.
        return self._defer('setClientReady', ())

    def autoAim(self, vehicle_id):
        return self._defer('autoAim', (vehicle_id,))

    def doCmdStr(self, *args):
        return self._defer('doCmdStr', args)

    def doCmdIntArr(self, *args):
        return self._defer('doCmdIntArr', args)

    def _defer(self, name, args):
        if self._target is not None:
            return getattr(self._target, name)(*args)
        self._pending.append((name, args))
        return None

    def __getattr__(self, name):
        if self._target is None:
            raise AttributeError(
                'Avatar server is not attached for mailbox %s' % name)
        return getattr(self._target, name)


class _OfflineInputHandler(object):

    def prerequisites(self):
        return []

    def start(self):
        return None

    def stop(self):
        return None

    def handleKeyEvent(self, event):
        return False

    def handleMouseEvent(self, dx, dy, dz):
        return False


class _OfflineCameraColliderHandler(object):
    """Late #1513 appearance teardown after AvatarInputHandler.stop()."""

    def __init__(self):
        self.onCameraChanged = _OfflineEventSink()

    def addVehicleToCameraCollider(self, vehicle):
        return None

    def removeVehicleFromCameraCollider(self, vehicle):
        return None


class _OfflineEventSink(object):
    """Minimal Event surface used by exact #1513 native teardown."""

    def __iadd__(self, callback):
        return self

    def __isub__(self, callback):
        return self


class _OfflineVehicleFilterSyncProxy(object):
    """Delegate WGVehicleFilter except for unsafe retail-only syncs."""

    __slots__ = (
        '_vehicle_filter', '_pose_matrix', '_velocity', '_acceleration')

    def __init__(self, vehicle_filter, pose_matrix=None, velocity=None,
                 acceleration=None):
        self._vehicle_filter = vehicle_filter
        self._pose_matrix = pose_matrix
        self._velocity = velocity
        self._acceleration = acceleration

    def __getattr__(self, name):
        return getattr(self._vehicle_filter, name)

    def syncGunAngles(self, yaw, pitch):
        # A client-only Vehicle has no retail interpolation filter behind its
        # initial gun-angle sample.  Exact #1513 asserts a null native filter
        # here, so the sample cannot be submitted through this native path.
        return None

    def syncStabilisedYPR(self, yaw, pitch, roll):
        # PlayerAvatar's auxiliary-physics property handler forwards its
        # first stabilised sample into the same missing retail server/filter
        # chain.  Keep the stock handler, including track and RPM updates, but
        # omit only this native sync while that exact handler is running.
        return None

    @property
    def velocity(self):
        if self._velocity is not None:
            return self._velocity
        return self._vehicle_filter.velocity

    @property
    def acceleration(self):
        if self._acceleration is not None:
            return self._acceleration
        return self._vehicle_filter.acceleration

    @property
    def groundPlacingMatrix(self):
        """Place detached presentation models at the copied live pose."""
        if self._pose_matrix is not None:
            return self._pose_matrix
        return self._vehicle_filter.groundPlacingMatrix

    def interpolateStabilisedMatrix(self, timestamp):
        """Expose the canonical copied pose to the fixed-turret aim path."""
        if self._pose_matrix is not None:
            return self._pose_matrix
        return self._vehicle_filter.interpolateStabilisedMatrix(timestamp)


class _OfflineInitialVehicleVisualProvider(object):
    """Suppress only stock's first visible enemy marker registration."""

    __slots__ = ('_provider',)

    def __init__(self, provider):
        self._provider = provider

    def __getattr__(self, name):
        return getattr(self._provider, name)

    def startVehicleVisual(self, unused_proxy, unused_is_immediate):
        # The LAN spotting edge registers the marker and minimap entry later.
        # Letting stock add it here and removing it after startVisual returns
        # still queues one visible UI frame in exact #1513.
        return None


class OfflineCompatibility(object):

    def __init__(self, runtime=None):
        self._runtime = runtime
        self._installed = False
        self._connecting = False
        self._fake_connected = False
        self._host = None
        self._host_added = False
        self._account_context = {}
        self._account_state = None
        self._garage_state = None
        self._show_lobby = False
        self._battle_active = False
        self._native_battle = False
        self._battle_gui_type = None
        self._battle_bonus_type = None
        self._battle_player_name = 'OfflinePlayer'
        self._battle_player_team = 1
        self._battle_arena_type_id = 0
        self._battle_network_client = None
        self._original_account_init = None
        self._original_account_getattribute = None
        self._original_account_become_player = None
        self._original_account_become_non_player = None
        self._original_avatar_init = None
        self._original_avatar_getattribute = None
        self._original_avatar_become_player = None
        self._original_avatar_become_non_player = None
        self._original_avatar_enter_world = None
        self._original_avatar_leave_world = None
        self._original_avatar_vehicle_enter = None
        self._avatar_vehicle_enter_code = None
        self._avatar_start_vehicle_visual_code = None
        self._original_avatar_prereqs_loaded = None
        self._original_avatar_aux_physics = None
        self._original_avatar_get_speeds = None
        self._original_avatar_auto_aim = None
        self._original_ammo_change_setting = None
        self._original_arcade_handle_key_event = None
        self._original_strategic_camera_update = None
        self._strategic_camera_update_wrapper = None
        self._strategic_camera_failure_reported = False
        self._original_sniper_handle_key_event = None
        self._arcade_handle_key_event_code = None
        self._sniper_handle_key_event_code = None
        self._original_control_mode_changed = None
        self._original_consistent_link_own_vehicle = None
        self._original_steady_relink_sources = None
        self._avatar_aux_physics_code = None
        self._original_vehicle_getattribute = None
        self._original_vehicle_setattr = None
        self._original_vehicle_get_speed = None
        self._original_vehicle_marker_start = None
        self._original_vehicle_marker_stop = None
        self._original_vehicle_enter_world = None
        self._original_vehicle_start_visual = None
        self._vehicle_start_visual_code = None
        self._original_vehicle_leave_world = None
        self._original_vehicle_start_wg_physics = None
        self._vehicle_start_wg_physics_code = None
        self._original_vehicle_set_gun_angles = None
        self._original_vehicle_collide_segment = None
        self._original_vehicle_collide_segment_ext = None
        self._vehicle_set_gun_angles_code = None
        self._gun_rotator_stabilised_code = None
        self._gun_rotator_predict_locked_target_code = None
        self._projectile_segment_may_hit_code = None
        self._camera_acceleration_update_code = None
        self._arcade_oscillator_acceleration_code = None
        self._sniper_oscillator_acceleration_code = None
        self._crashed_track_setup_assembler_code = None
        self._crashed_track_model_loaded_code = None
        self._original_compound_getattribute = None
        self._original_compound_deactivate = None
        self._original_compound_models_refresh = None
        self._compound_models_refresh_code = None
        self._original_connect = None
        self._original_disconnect = None
        self._original_server_time = None
        self._original_target = None
        self._original_debug_update = None
        self._account_init_wrapper = None
        self._account_getattribute_wrapper = None
        self._account_become_player_wrapper = None
        self._account_become_non_player_wrapper = None
        self._avatar_init_wrapper = None
        self._avatar_getattribute_wrapper = None
        self._avatar_become_player_wrapper = None
        self._avatar_become_non_player_wrapper = None
        self._avatar_enter_world_wrapper = None
        self._avatar_leave_world_wrapper = None
        self._avatar_vehicle_enter_wrapper = None
        self._avatar_prereqs_loaded_wrapper = None
        self._avatar_aux_physics_wrapper = None
        self._avatar_get_speeds_wrapper = None
        self._avatar_auto_aim_wrapper = None
        self._ammo_change_setting_wrapper = None
        self._arcade_handle_key_event_wrapper = None
        self._sniper_handle_key_event_wrapper = None
        self._control_mode_changed_wrapper = None
        self._consistent_link_own_vehicle_wrapper = None
        self._steady_relink_sources_wrapper = None
        self._vehicle_getattribute_wrapper = None
        self._vehicle_setattr_wrapper = None
        self._vehicle_get_speed_wrapper = None
        self._vehicle_marker_start_wrapper = None
        self._vehicle_marker_stop_wrapper = None
        self._vehicle_enter_world_wrapper = None
        self._vehicle_start_visual_wrapper = None
        self._vehicle_leave_world_wrapper = None
        self._vehicle_start_wg_physics_wrapper = None
        self._vehicle_set_gun_angles_wrapper = None
        self._vehicle_collide_segment_wrapper = None
        self._vehicle_collide_segment_ext_wrapper = None
        self._compound_getattribute_wrapper = None
        self._compound_deactivate_wrapper = None
        self._compound_models_refresh_wrapper = None
        self._vehicle_starting_visual = None
        self._vehicle_starting_wg_physics = None
        self._vehicle_syncing_gun_angles = None
        self._avatar_syncing_aux_physics = None
        self._avatar_entering_vehicle = None
        self._compound_refreshing_models = None
        self._connect_wrapper = None
        self._disconnect_wrapper = None
        self._server_time_wrapper = None
        self._debug_update_wrapper = None
        self._battle_server_time_origin = None
        self._battle_clock_origin = None
        self._vehicle_property_overlays = {}
        self._vehicle_marker_plugins = {}
        self._battle_player_vehicle_id = 0
        self._postmortem_vehicle_id = 0
        self._control_mode_listener = None
        self._target_lock_candidate = None
        self._target_lock_input_pending = False
        self._target_lock_input_avatar = None

    def install(self):
        if self._installed:
            return
        self._runtime = self._runtime or _load_runtime()
        if self._account_state is None:
            from gui.mods.offline_lan_0922.account_rpc.state import AccountState
            self._account_state = AccountState()
        runtime = self._runtime
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        ammo_controller_type = getattr(
            runtime, 'ammo_controller_type', None)
        vehicle_type = getattr(
            getattr(runtime, 'vehicle_module', None), 'Vehicle', None)
        vehicle_marker_type = getattr(
            runtime, 'vehicle_marker_plugin_type', None)
        compound_type = getattr(
            getattr(runtime, 'compound_appearance_module', None),
            'CompoundAppearance', None)
        debug_panel_type = getattr(runtime, 'debug_panel_type', None)
        input_handler_type = getattr(
            getattr(runtime, 'avatar_input_handler', None),
            'AvatarInputHandler', None)
        arcade_control_type = getattr(
            getattr(runtime, 'control_modes', None),
            'ArcadeControlMode', None)
        sniper_control_type = getattr(
            getattr(runtime, 'control_modes', None),
            'SniperControlMode', None)
        consistent_matrices_type = getattr(
            getattr(runtime, 'avatar_position_control', None),
            'ConsistentMatrices', None)
        steady_matrix_type = getattr(
            getattr(runtime, 'steady_vehicle_matrix', None),
            'SteadyVehicleMatrixCalculator', None)
        gun_rotator_type = getattr(
            getattr(runtime, 'vehicle_gun_rotator', None),
            'VehicleGunRotator', None)
        acceleration_smoother_type = getattr(
            runtime, 'acceleration_smoother_type', None)
        arcade_camera_type = getattr(runtime, 'arcade_camera_type', None)
        sniper_camera_type = getattr(runtime, 'sniper_camera_type', None)
        crashed_tracks_controller_type = getattr(
            runtime, 'crashed_tracks_controller_type', None)
        strategic_camera_type = getattr(runtime, 'strategic_camera_type', None)
        self._original_strategic_camera_update = getattr(
            strategic_camera_type, '_StrategicCamera__cameraUpdate', None)
        if not callable(self._original_strategic_camera_update):
            raise RuntimeError('#1513 strategic camera update is unavailable')
        self._original_account_init = account_type.__dict__.get(
            '__init__', account_type.__init__)
        self._original_account_getattribute = account_type.__dict__.get(
            '__getattribute__', account_type.__getattribute__)
        self._original_account_become_player = account_type.__dict__.get(
            'onBecomePlayer', getattr(account_type, 'onBecomePlayer', None))
        self._original_account_become_non_player = account_type.__dict__.get(
            'onBecomeNonPlayer',
            getattr(account_type, 'onBecomeNonPlayer', None))
        self._original_avatar_init = avatar_type.__dict__.get(
            '__init__', avatar_type.__init__)
        self._original_avatar_getattribute = avatar_type.__dict__.get(
            '__getattribute__', avatar_type.__getattribute__)
        self._original_avatar_become_player = avatar_type.__dict__.get(
            'onBecomePlayer', avatar_type.onBecomePlayer)
        self._original_avatar_become_non_player = avatar_type.__dict__.get(
            'onBecomeNonPlayer',
            getattr(avatar_type, 'onBecomeNonPlayer', None))
        self._original_avatar_enter_world = avatar_type.__dict__.get(
            'onEnterWorld', getattr(avatar_type, 'onEnterWorld', None))
        self._original_avatar_leave_world = avatar_type.__dict__.get(
            'onLeaveWorld', getattr(avatar_type, 'onLeaveWorld', None))
        self._original_avatar_vehicle_enter = avatar_type.__dict__.get(
            'vehicle_onEnterWorld',
            getattr(avatar_type, 'vehicle_onEnterWorld', None))
        if self._original_avatar_vehicle_enter is not None:
            self._avatar_vehicle_enter_code = getattr(
                self._original_avatar_vehicle_enter,
                'func_code', getattr(
                    self._original_avatar_vehicle_enter, '__code__', None))
        avatar_start_visual = avatar_type.__dict__.get(
            '_PlayerAvatar__startVehicleVisual', getattr(
                avatar_type, '_PlayerAvatar__startVehicleVisual', None))
        if avatar_start_visual is not None:
            self._avatar_start_vehicle_visual_code = getattr(
                avatar_start_visual, 'func_code', getattr(
                    avatar_start_visual, '__code__', None))
        self._original_avatar_prereqs_loaded = avatar_type.__dict__.get(
            'onPrereqsLoaded', getattr(avatar_type, 'onPrereqsLoaded', None))
        self._original_avatar_get_speeds = avatar_type.__dict__.get(
            'getOwnVehicleSpeeds',
            getattr(avatar_type, 'getOwnVehicleSpeeds', None))
        self._original_avatar_auto_aim = avatar_type.__dict__.get(
            'autoAim', getattr(avatar_type, 'autoAim', None))
        if self._original_avatar_auto_aim is None:
            raise RuntimeError('#1513 Avatar.autoAim is unavailable')
        if ammo_controller_type is not None:
            self._original_ammo_change_setting = (
                ammo_controller_type.__dict__.get(
                    'changeSetting', getattr(
                        ammo_controller_type, 'changeSetting', None)))
            if (self._original_ammo_change_setting is None or
                    getattr(runtime, 'avatar_getter', None) is None):
                raise RuntimeError(
                    '#1513 AmmoController shell-setting boundary is '
                    'unavailable')
        if arcade_control_type is None or sniper_control_type is None:
            raise RuntimeError('#1513 target-lock control modes are unavailable')
        self._original_arcade_handle_key_event = (
            arcade_control_type.__dict__.get(
                'handleKeyEvent',
                getattr(arcade_control_type, 'handleKeyEvent', None)))
        self._original_sniper_handle_key_event = (
            sniper_control_type.__dict__.get(
                'handleKeyEvent',
                getattr(sniper_control_type, 'handleKeyEvent', None)))
        if (self._original_arcade_handle_key_event is None or
                self._original_sniper_handle_key_event is None):
            raise RuntimeError(
                '#1513 target-lock input boundary is unavailable')
        self._arcade_handle_key_event_code = getattr(
            self._original_arcade_handle_key_event, 'func_code', getattr(
                self._original_arcade_handle_key_event, '__code__', None))
        self._sniper_handle_key_event_code = getattr(
            self._original_sniper_handle_key_event, 'func_code', getattr(
                self._original_sniper_handle_key_event, '__code__', None))
        if (self._arcade_handle_key_event_code is None or
                self._sniper_handle_key_event_code is None):
            raise RuntimeError(
                '#1513 target-lock input code boundary is unavailable')
        self._original_avatar_aux_physics = avatar_type.__dict__.get(
            '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData',
            getattr(
                avatar_type,
                '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData', None))
        if self._original_avatar_aux_physics is not None:
            self._avatar_aux_physics_code = getattr(
                self._original_avatar_aux_physics,
                'func_code', getattr(
                    self._original_avatar_aux_physics, '__code__', None))
        if input_handler_type is None:
            raise RuntimeError('#1513 AvatarInputHandler is unavailable')
        self._original_control_mode_changed = (
            input_handler_type.__dict__.get(
                'onControlModeChanged',
                getattr(input_handler_type, 'onControlModeChanged', None)))
        if self._original_control_mode_changed is None:
            raise RuntimeError(
                '#1513 control-mode transition boundary is unavailable')
        if consistent_matrices_type is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        if steady_matrix_type is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        self._original_consistent_link_own_vehicle = (
            consistent_matrices_type.__dict__.get(
                '_ConsistentMatrices__linkOwnVehicle',
                getattr(
                    consistent_matrices_type,
                    '_ConsistentMatrices__linkOwnVehicle', None)))
        self._original_steady_relink_sources = (
            steady_matrix_type.__dict__.get(
                'relinkSources',
                getattr(steady_matrix_type, 'relinkSources', None)))
        if self._original_consistent_link_own_vehicle is None:
            raise RuntimeError(
                '#1513 own-vehicle matrix link boundary is unavailable')
        if self._original_steady_relink_sources is None:
            raise RuntimeError(
                '#1513 steady matrix relink boundary is unavailable')
        if gun_rotator_type is None:
            raise RuntimeError('#1513 VehicleGunRotator is unavailable')
        gun_rotator_stabilised = getattr(
            gun_rotator_type, 'getAvatarOwnVehicleStabilisedMatrix', None)
        if gun_rotator_stabilised is None:
            raise RuntimeError(
                '#1513 fixed-turret stabilised matrix boundary is unavailable')
        self._gun_rotator_stabilised_code = getattr(
            gun_rotator_stabilised, 'func_code', getattr(
                gun_rotator_stabilised, '__code__', None))
        if self._gun_rotator_stabilised_code is None:
            raise RuntimeError(
                '#1513 fixed-turret stabilised matrix code is unavailable')
        gun_rotator_predict_locked_target = getattr(
            gun_rotator_type, 'predictLockedTargetShotPoint', None)
        if gun_rotator_predict_locked_target is None:
            raise RuntimeError(
                '#1513 target-lock prediction boundary is unavailable')
        self._gun_rotator_predict_locked_target_code = getattr(
            gun_rotator_predict_locked_target, 'func_code', getattr(
                gun_rotator_predict_locked_target, '__code__', None))
        if self._gun_rotator_predict_locked_target_code is None:
            raise RuntimeError(
                '#1513 target-lock prediction code is unavailable')
        projectile_segment_may_hit = getattr(
            getattr(runtime, 'projectile_mover_module', None),
            'segmentMayHitEntity', None)
        if projectile_segment_may_hit is None:
            raise RuntimeError(
                '#1513 projectile collision prefilter is unavailable')
        self._projectile_segment_may_hit_code = getattr(
            projectile_segment_may_hit, 'func_code', getattr(
                projectile_segment_may_hit, '__code__', None))
        if self._projectile_segment_may_hit_code is None:
            raise RuntimeError(
                '#1513 projectile collision prefilter code is unavailable')
        if (acceleration_smoother_type is None or
                arcade_camera_type is None or sniper_camera_type is None):
            raise RuntimeError('#1513 dynamic-camera motion ABI is unavailable')
        camera_acceleration_update = acceleration_smoother_type.__dict__.get(
            'update', getattr(acceleration_smoother_type, 'update', None))
        arcade_oscillator_acceleration = arcade_camera_type.__dict__.get(
            '_ArcadeCamera__calcCurOscillatorAcceleration', getattr(
                arcade_camera_type,
                '_ArcadeCamera__calcCurOscillatorAcceleration', None))
        sniper_oscillator_acceleration = sniper_camera_type.__dict__.get(
            '_SniperCamera__calcCurOscillatorAcceleration', getattr(
                sniper_camera_type,
                '_SniperCamera__calcCurOscillatorAcceleration', None))
        camera_motion_methods = (
            camera_acceleration_update, arcade_oscillator_acceleration,
            sniper_oscillator_acceleration)
        if not all(callable(method) for method in camera_motion_methods):
            raise RuntimeError('#1513 dynamic-camera motion methods are unavailable')
        camera_motion_codes = tuple(
            getattr(method, 'func_code', getattr(method, '__code__', None))
            for method in camera_motion_methods)
        if any(code is None for code in camera_motion_codes):
            raise RuntimeError('#1513 dynamic-camera motion code is unavailable')
        (self._camera_acceleration_update_code,
         self._arcade_oscillator_acceleration_code,
         self._sniper_oscillator_acceleration_code) = camera_motion_codes
        if crashed_tracks_controller_type is None:
            raise RuntimeError('#1513 crashed-track controller is unavailable')
        crashed_track_setup_assembler = getattr(
            crashed_tracks_controller_type,
            '_CrashedTrackController__setupTrackAssembler', None)
        crashed_track_model_loaded = getattr(
            crashed_tracks_controller_type,
            '_CrashedTrackController__onModelLoaded', None)
        if (not callable(crashed_track_setup_assembler) or
                not callable(crashed_track_model_loaded)):
            raise RuntimeError(
                '#1513 crashed-track pose boundaries are unavailable')
        self._crashed_track_setup_assembler_code = getattr(
            crashed_track_setup_assembler, 'func_code', getattr(
                crashed_track_setup_assembler, '__code__', None))
        self._crashed_track_model_loaded_code = getattr(
            crashed_track_model_loaded, 'func_code', getattr(
                crashed_track_model_loaded, '__code__', None))
        if (self._crashed_track_setup_assembler_code is None or
                self._crashed_track_model_loaded_code is None):
            raise RuntimeError(
                '#1513 crashed-track pose code is unavailable')
        if vehicle_type is not None:
            self._original_vehicle_getattribute = vehicle_type.__dict__.get(
                '__getattribute__', vehicle_type.__getattribute__)
            self._original_vehicle_setattr = vehicle_type.__dict__.get(
                '__setattr__', vehicle_type.__setattr__)
            self._original_vehicle_get_speed = vehicle_type.__dict__.get(
                'getSpeed', getattr(vehicle_type, 'getSpeed', None))
            self._original_vehicle_enter_world = (
                vehicle_type.__dict__.get(
                    'onEnterWorld',
                    getattr(vehicle_type, 'onEnterWorld', None)))
            self._original_vehicle_start_visual = (
                vehicle_type.__dict__.get(
                    'startVisual',
                    getattr(vehicle_type, 'startVisual', None)))
            if self._original_vehicle_start_visual is not None:
                self._vehicle_start_visual_code = getattr(
                    self._original_vehicle_start_visual,
                    'func_code', getattr(
                        self._original_vehicle_start_visual,
                        '__code__', None))
            self._original_vehicle_leave_world = (
                vehicle_type.__dict__.get(
                    'onLeaveWorld',
                    getattr(vehicle_type, 'onLeaveWorld', None)))
            self._original_vehicle_start_wg_physics = (
                vehicle_type.__dict__.get(
                    '_Vehicle__startWGPhysics',
                    getattr(vehicle_type, '_Vehicle__startWGPhysics', None)))
            if self._original_vehicle_start_wg_physics is not None:
                self._vehicle_start_wg_physics_code = getattr(
                    self._original_vehicle_start_wg_physics,
                    'func_code', getattr(
                        self._original_vehicle_start_wg_physics,
                        '__code__', None))
            self._original_vehicle_set_gun_angles = (
                vehicle_type.__dict__.get(
                    'set_gunAnglesPacked',
                    getattr(vehicle_type, 'set_gunAnglesPacked', None)))
            if self._original_vehicle_set_gun_angles is not None:
                self._vehicle_set_gun_angles_code = getattr(
                    self._original_vehicle_set_gun_angles,
                    'func_code', getattr(
                        self._original_vehicle_set_gun_angles,
                        '__code__', None))
            self._original_vehicle_collide_segment = (
                vehicle_type.__dict__.get(
                    'collideSegment',
                    getattr(vehicle_type, 'collideSegment', None)))
            self._original_vehicle_collide_segment_ext = (
                vehicle_type.__dict__.get(
                    'collideSegmentExt',
                    getattr(vehicle_type, 'collideSegmentExt', None)))
            if (not callable(self._original_vehicle_collide_segment) or
                    not callable(
                        self._original_vehicle_collide_segment_ext)):
                raise RuntimeError(
                    '#1513 vehicle collision methods are unavailable')
        if vehicle_marker_type is None:
            raise RuntimeError('#1513 vehicle-marker plugin is unavailable')
        self._original_vehicle_marker_start = \
            vehicle_marker_type.__dict__.get(
                'start', getattr(vehicle_marker_type, 'start', None))
        self._original_vehicle_marker_stop = \
            vehicle_marker_type.__dict__.get(
                'stop', getattr(vehicle_marker_type, 'stop', None))
        if (not callable(self._original_vehicle_marker_start) or
                not callable(self._original_vehicle_marker_stop)):
            raise RuntimeError(
                '#1513 vehicle-marker lifecycle is unavailable')
        if compound_type is not None:
            self._original_compound_getattribute = (
                compound_type.__dict__.get(
                    '__getattribute__', compound_type.__getattribute__))
            self._original_compound_deactivate = (
                compound_type.__dict__.get(
                    'deactivate', getattr(compound_type, 'deactivate', None)))
            self._original_compound_models_refresh = (
                compound_type.__dict__.get(
                    '_CompoundAppearance__onModelsRefresh',
                    getattr(
                        compound_type,
                        '_CompoundAppearance__onModelsRefresh', None)))
            if self._original_compound_models_refresh is not None:
                self._compound_models_refresh_code = getattr(
                    self._original_compound_models_refresh,
                    'func_code', getattr(
                        self._original_compound_models_refresh,
                        '__code__', None))
        self._original_connect = runtime.bigworld.connect
        self._original_disconnect = runtime.bigworld.disconnect
        self._original_server_time = runtime.bigworld.serverTime
        self._original_target = getattr(runtime.bigworld, 'target', None)
        if not callable(self._original_target):
            raise RuntimeError('#1513 BigWorld.target is unavailable')
        if debug_panel_type is not None:
            self._original_debug_update = debug_panel_type.__dict__.get(
                'updateDebugInfo',
                getattr(debug_panel_type, 'updateDebugInfo', None))
        compatibility = self

        def account_init(account):
            offline_initializing = compatibility._connecting
            if offline_initializing:
                account.isOffline = True
                account.name = _OFFLINE_ACCOUNT_NAME
                account.initialServerSettings = dict(_SERVER_SETTINGS)
                property_name, property_value = (
                    runtime.account_module._CLIENT_SERVER_VERSION)
                setattr(account, property_name, property_value)
                context = compatibility.seed_account_context()
                receive_stats = getattr(account, 'receiveServerStats', None)
                if callable(receive_stats):
                    context['receive_server_stats'] = receive_stats
                on_enqueued = getattr(account, 'onEnqueued', None)
                if callable(on_enqueued):
                    context['on_enqueued'] = on_enqueued
                on_dequeued = getattr(account, 'onDequeued', None)
                if callable(on_dequeued):
                    context['on_dequeued'] = on_dequeued
                callback = getattr(runtime.bigworld, 'callback', None)
                if callback is None:
                    account.fakeServer = _FallbackServer()
                else:
                    from gui.mods.offline_lan_0922.account_rpc.server import \
                        FakeServer

                    def active_account():
                        try:
                            player = runtime.bigworld.player()
                        except ReferenceError:
                            return None
                        if (player is account and
                                getattr(account, _OFFLINE_INIT_COMPLETE,
                                        False) and
                                getattr(account, _OFFLINE_PLAYER_READY,
                                        False)):
                            return player
                        return None

                    account.fakeServer = FakeServer(
                        active_account, callback=callback, context=context)
                # Exact #1513 reuses g_accountRepository across Account
                # entities.  AccountSyncData.setAccount() saves its cache
                # before rebinding the cache's weak proxy, but BigWorld clears
                # the retired Entity's entire __dict__.  Point that one cache
                # at the replacement first so neither an empty nor a dead old
                # Entity is dereferenced during the native constructor.
                repository = getattr(
                    runtime.account_module, 'g_accountRepository', None)
                if (repository is not None and
                        getattr(repository, 'className', None) ==
                        account.__class__.__name__):
                    persistent_cache = getattr(
                        repository.syncData,
                        '_AccountSyncData__persistentCache')
                    persistent_cache.setAccount(account)
            compatibility._original_account_init(account)
            if offline_initializing:
                setattr(account, _OFFLINE_INIT_COMPLETE, True)

        def avatar_init(avatar):
            offline_initializing = compatibility._fake_connected
            if offline_initializing:
                compatibility._prepare_avatar_properties(avatar)
            compatibility._original_avatar_init(avatar)
            if offline_initializing:
                avatar.filter = runtime.bigworld.AvatarFilter()
                avatar.filter.enableLagDetection(True)
                setattr(avatar, _OFFLINE_INIT_COMPLETE, True)

        def control_mode_changed(handler, eMode, **args):
            result = compatibility._original_control_mode_changed(
                handler, eMode, **args)
            listener = compatibility._control_mode_listener
            if listener is not None:
                current = getattr(
                    handler, '_AvatarInputHandler__ctrlModeName', None)
                if current == eMode:
                    listener(handler, eMode)
            return result

        def rollback_vehicle_marker_start(plugin, primary_error):
            failures = []
            provider = getattr(plugin, 'sessionProvider', None)
            remove_arena_ctrl = getattr(provider, 'removeArenaCtrl', None)
            if not callable(remove_arena_ctrl):
                failures.append('removeArenaCtrl is unavailable')
            else:
                try:
                    remove_arena_ctrl(plugin)
                except Exception as error:
                    failures.append('removeArenaCtrl failed: %s' % error)
            try:
                compatibility._original_vehicle_marker_stop(plugin)
            except Exception as error:
                failures.append('native stop failed: %s' % error)
            finally:
                compatibility._vehicle_marker_plugins.pop(
                    id(plugin), None)
            if failures:
                raise RuntimeError(
                    '%s; vehicle-marker start rollback failed: %s' % (
                        primary_error, '; '.join(failures)))

        def vehicle_marker_start(plugin):
            expected = compatibility._battle_player_vehicle_id
            if expected:
                provider = getattr(plugin, 'sessionProvider', None)
                get_arena_dp = getattr(provider, 'getArenaDP', None)
                if not callable(get_arena_dp):
                    raise RuntimeError(
                        '#1513 vehicle-marker ArenaDP provider is unavailable')
                arena_dp = get_arena_dp()
                required = getattr(
                    arena_dp, 'isRequiredDataExists', None)
                get_player_vehicle_id = getattr(
                    arena_dp, 'getPlayerVehicleID', None)
                if not callable(required) or not callable(
                        get_player_vehicle_id):
                    raise RuntimeError(
                        '#1513 vehicle-marker player identity API is '
                        'unavailable')
                if not required():
                    raise RuntimeError(
                        '#1513 vehicle-marker ArenaDP identity is incomplete '
                        'before start')
                arena_vehicle_id = int(get_player_vehicle_id(False))
                if arena_vehicle_id != expected:
                    raise RuntimeError(
                        '#1513 vehicle-marker ArenaDP identity mismatch '
                        'before start: expected=%s arenaDP=%s' % (
                            expected, arena_vehicle_id))
            try:
                result = compatibility._original_vehicle_marker_start(plugin)
            except Exception as error:
                rollback_vehicle_marker_start(plugin, error)
                raise
            try:
                cached = getattr(
                    plugin, '_VehicleMarkerPlugin__playerVehicleID')
            except AttributeError:
                error = RuntimeError(
                    '#1513 vehicle-marker player identity cache is missing')
                rollback_vehicle_marker_start(plugin, error)
                raise error
            if expected and cached != expected:
                error = RuntimeError(
                    '#1513 vehicle-marker plugin captured a stale '
                    'player identity: expected=%s cached=%s' %
                    (expected, cached))
                rollback_vehicle_marker_start(plugin, error)
                raise error
            compatibility._vehicle_marker_plugins[id(plugin)] = plugin
            return result

        def vehicle_marker_stop(plugin):
            try:
                return compatibility._original_vehicle_marker_stop(plugin)
            finally:
                compatibility._vehicle_marker_plugins.pop(id(plugin), None)

        def consistent_link_own_vehicle(matrices, vehicle):
            overlay = compatibility._vehicle_property_overlays.get(
                id(vehicle))
            if (compatibility._battle_active and overlay is not None and
                    overlay.get('_pose_active')):
                provider = getattr(
                    matrices, '_ConsistentMatrices__ownVehicleMProv', None)
                if provider is None:
                    raise RuntimeError(
                        '#1513 own-vehicle matrix provider is unavailable')
                provider.target = overlay['matrix']
                if provider.target is not overlay['matrix']:
                    raise RuntimeError(
                        '#1513 own-vehicle matrix rejected live pose')
                return None
            return compatibility._original_consistent_link_own_vehicle(
                matrices, vehicle)

        def steady_relink_sources(calculator):
            if compatibility._battle_active:
                try:
                    player = runtime.bigworld.player()
                except ReferenceError:
                    player = None
                vehicle = (player.getVehicleAttached()
                           if player is not None else None)
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle)) if vehicle is not None else None
                if overlay is not None and overlay.get('_pose_active'):
                    matrix = overlay['matrix']
                    steady_rotation = overlay.get(
                        'steady_rotation_matrix', matrix)
                    stabilised_matrix = overlay.get(
                        'stabilised_matrix', matrix)
                    output = getattr(
                        calculator,
                        '_SteadyVehicleMatrixCalculator__outputMProv', None)
                    stabilised = getattr(
                        calculator,
                        '_SteadyVehicleMatrixCalculator__stabilisedMProv',
                        None)
                    if output is None or stabilised is None:
                        raise RuntimeError(
                            '#1513 steady vehicle providers are unavailable')
                    output.rotationSrc = steady_rotation
                    output.translationSrc = stabilised_matrix
                    stabilised.target = stabilised_matrix
                    if (output.rotationSrc is not steady_rotation or
                            output.translationSrc is not stabilised_matrix or
                            stabilised.target is not stabilised_matrix):
                        raise RuntimeError(
                            '#1513 steady vehicle providers rejected live '
                            'pose')
                    return None
            return compatibility._original_steady_relink_sources(calculator)

        def avatar_become_player(avatar):
            if not compatibility._fake_connected:
                return compatibility._original_avatar_become_player(avatar)
            if not getattr(avatar, _OFFLINE_INIT_COMPLETE, False):
                raise RuntimeError(
                    'offline Avatar initialization did not complete')
            compatibility.prepare_avatar(avatar)
            offline_filter = avatar.filter
            original_filter_factory = runtime.bigworld.AvatarFilter
            active = getattr(runtime.offline_map_creator, 'Active', None)
            if callable(active):
                map_was_active = bool(active())
            else:
                # Test doubles and a few unpacked client variants expose the
                # same state as a field.  The #1513 runtime uses Active().
                map_was_active = bool(getattr(
                    runtime.offline_map_creator, 'active', False))

            if compatibility._battle_gui_type is not None:
                avatar.arenaGuiType = compatibility._battle_gui_type
            if compatibility._battle_bonus_type is not None:
                avatar.arenaBonusType = compatibility._battle_bonus_type

            def reuse_offline_filter():
                return offline_filter

            runtime.bigworld.AvatarFilter = reuse_offline_filter
            if compatibility._native_battle:
                # The stock offline viewer intentionally skips the battle
                # session and real AvatarInputHandler.  A playable LAN battle
                # needs the normal #1513 initialization branches instead.
                runtime.offline_map_creator.SetActive(False)
            # Native onBecomePlayer attaches ChatManager, player events and
            # battle controllers before every later validation has finished.
            # Open the retirement token before entering stock code so a
            # partial promotion is still torn down exactly once.
            setattr(avatar, _OFFLINE_RETIRE_PENDING, True)
            try:
                result = compatibility._original_avatar_become_player(avatar)
            finally:
                if runtime.bigworld.AvatarFilter is reuse_offline_filter:
                    runtime.bigworld.AvatarFilter = original_filter_factory
                runtime.offline_map_creator.SetActive(map_was_active)
            arena = getattr(avatar, 'arena', None)
            if arena is None or getattr(arena, 'arenaType', None) is None:
                # Exact PlayerAvatar.onBecomePlayer can abort and return
                # normally when the arena type is missing.  A successful
                # Python return therefore is not by itself a ready Avatar.
                raise RuntimeError(
                    'offline Avatar has no initialized arena type')
            setattr(avatar, _OFFLINE_PLAYER_READY, True)
            return result

        def retire_offline_player(player, original):
            """Run a client-only player's native retirement exactly once."""
            if not getattr(player, _OFFLINE_INIT_COMPLETE, False):
                if compatibility._fake_connected:
                    # A failed constructor or an already-cleared PyEntity has
                    # no complete native lifecycle left to detach.  Never call
                    # stock teardown against its missing instance fields.
                    return None
                return original(player)
            if not getattr(player, _OFFLINE_RETIRE_PENDING, False):
                return None
            # BigWorld.clear* can invoke onBecomeNonPlayer again after our
            # explicit lifecycle boundary.  Close the token before entering
            # stock code so that second delivery cannot detach global owners
            # twice, even if the first call raises part-way through.
            setattr(player, _OFFLINE_RETIRE_PENDING, False)
            setattr(player, _OFFLINE_PLAYER_READY, False)
            try:
                result = original(player)
            except Exception:
                # Account/Avatar teardown can itself fail before reaching the
                # late ChatManager detach.  Preserve that first exception, but
                # never leave the global proxy pointing at an Entity whose
                # instance dictionary will be cleared next.
                try:
                    chat_manager = getattr(runtime, 'chat_manager', None)
                    if (chat_manager is not None and
                            getattr(chat_manager, 'playerProxy', None) is not
                            None):
                        chat_manager.switchPlayerProxy(None)
                except Exception:
                    pass
                raise
            chat_manager = getattr(runtime, 'chat_manager', None)
            if (chat_manager is not None and
                    getattr(chat_manager, 'playerProxy', None) is not None):
                chat_manager.switchPlayerProxy(None)
            return result

        def account_become_non_player(account):
            return retire_offline_player(
                account, compatibility._original_account_become_non_player)

        def avatar_become_non_player(avatar):
            return retire_offline_player(
                avatar, compatibility._original_avatar_become_non_player)

        def avatar_enter_world(avatar, prereqs):
            if (compatibility._fake_connected and
                    not getattr(avatar, _OFFLINE_INIT_COMPLETE, False)):
                # BigWorld still delivers world callbacks after a Python
                # constructor raises.  Stock PlayerAvatar.onEnterWorld then
                # dereferences fields that its interrupted __init__ never
                # created, obscuring the first property/constructor error.
                return None
            return compatibility._original_avatar_enter_world(
                avatar, prereqs)

        def avatar_leave_world(avatar):
            if (compatibility._fake_connected and
                    not getattr(avatar, _OFFLINE_INIT_COMPLETE, False)):
                # The matching entity clear can arrive for the same partial
                # PyEntity.  It has no native ConsistentMatrices owner to
                # notify and therefore no stock leave lifecycle to run.
                return None
            return compatibility._original_avatar_leave_world(avatar)

        def avatar_getattribute(avatar, name):
            if (name in ('base', 'cell', 'server', 'bwProto') and
                    compatibility._battle_active):
                try:
                    return compatibility._original_avatar_getattribute(
                        avatar, 'fakeServer')
                except AttributeError:
                    pass
            if name == 'vehicle' and compatibility._battle_active:
                vehicle_id = compatibility._postmortem_vehicle_id
                if vehicle_id:
                    vehicle = runtime.bigworld.entity(vehicle_id)
                    if vehicle is not None:
                        # Retail changes Avatar.vehicle before it publishes
                        # onSwitchViewpoint.  A client-only Avatar has no cell
                        # attachment, so expose the selected observed vehicle
                        # for the lifetime of postmortem control instead.
                        return vehicle
                try:
                    caller_code = sys._getframe(1).f_code
                except (AttributeError, ValueError):
                    caller_code = None
                if (caller_code is
                        compatibility._sniper_oscillator_acceleration_code):
                    try:
                        vehicle_id = \
                            compatibility._original_avatar_getattribute(
                                avatar, 'playerVehicleID')
                        vehicle = runtime.bigworld.entity(vehicle_id)
                        overlay = compatibility._vehicle_property_overlays.get(
                            id(vehicle))
                    except (AttributeError, ReferenceError, TypeError):
                        vehicle = None
                        overlay = None
                    if (vehicle is not None and overlay is not None and
                            overlay.get('_pose_active')):
                        # Exact SniperCamera reads ``player().vehicle`` while
                        # the client-only Avatar has no engine attachment.
                        # Expose the selected local Vehicle only to that one
                        # direct motion calculation; all other callers retain
                        # the truthful empty attachment.
                        return vehicle
            return compatibility._original_avatar_getattribute(avatar, name)

        def prime_initial_remote_enemy(avatar, vehicle):
            """Mark one enemy hidden before stock starts its visual."""
            if not compatibility._battle_active:
                return False
            public_info = getattr(vehicle, 'publicInfo', None)
            try:
                vehicle_team = int(public_info['team'])
                avatar_team = int(
                    compatibility._original_avatar_getattribute(
                        avatar, 'team'))
            except (AttributeError, KeyError, TypeError, ValueError):
                return False
            if vehicle_team == avatar_team:
                return False
            vehicle._spot_visible = False
            vehicle._offlineNativeDrawVisible = False
            vehicle._offlineNativeMarkerVisible = False
            vehicle.targetCaps = []
            return True

        def hide_initial_remote_enemy(avatar, vehicle):
            """Close the first native draw/marker window for one enemy."""
            if not prime_initial_remote_enemy(avatar, vehicle):
                return False
            show = getattr(vehicle, 'show', None)
            try:
                provider = compatibility._original_avatar_getattribute(
                    avatar, 'guiSessionProvider')
            except AttributeError:
                provider = None
            stop_visual = getattr(provider, 'stopVehicleVisual', None)
            if not callable(show) or not callable(stop_visual):
                raise RuntimeError(
                    '#1513 initial enemy visibility gate is unavailable')
            show(False)
            stop_visual(int(vehicle.id), False)
            return True

        def avatar_vehicle_enter(avatar, vehicle):
            server = None
            if compatibility._battle_active:
                try:
                    server = compatibility._original_avatar_getattribute(
                        avatar, 'fakeServer')
                except AttributeError:
                    server = None
                prepare = getattr(server, 'prepareVehicleEnter', None)
                accept = getattr(server, 'acceptVehicleEnter', None)
                if callable(accept):
                    try:
                        if callable(prepare):
                            prepare(vehicle)
                        accept(vehicle.id)
                    except Exception as error:
                        fail = getattr(server, 'failVehicleEnter', None)
                        if callable(fail):
                            fail(vehicle.id, error)
                        raise
            try:
                previous_entering = compatibility._avatar_entering_vehicle
                compatibility._avatar_entering_vehicle = vehicle
                prime_initial_remote_enemy(avatar, vehicle)
                if compatibility._original_avatar_vehicle_enter is not None:
                    result = compatibility._original_avatar_vehicle_enter(
                        avatar, vehicle)
                else:
                    result = None
                hide_initial_remote_enemy(avatar, vehicle)
            except Exception as error:
                fail = getattr(server, 'failVehicleEnter', None)
                if callable(fail):
                    fail(vehicle.id, error)
                raise
            finally:
                compatibility._avatar_entering_vehicle = previous_entering
            complete = getattr(server, 'completeVehicleEnter', None)
            if callable(complete):
                complete(vehicle.id)
            return result

        def vehicle_enter_world(vehicle, prereqs):
            """Let stock initialise every controller, then close enemy draw.

            PlayerAvatar.vehicle_onEnterWorld runs from inside the stock
            Vehicle lifecycle.  #1513 may still finish or refresh the model
            after that callback returns, so hiding only at the Avatar boundary
            leaves a one-frame spawn flash.  Prime the gate before the stock
            lifecycle and enforce it once more after the complete lifecycle;
            no stock startVisual/controller work is skipped.
            """
            original = compatibility._original_vehicle_enter_world
            if not compatibility._battle_active:
                return original(vehicle, prereqs)
            try:
                avatar = runtime.bigworld.player()
            except ReferenceError:
                avatar = None
            if avatar is not None:
                prime_initial_remote_enemy(avatar, vehicle)
            result = original(vehicle, prereqs)
            try:
                avatar = runtime.bigworld.player()
            except ReferenceError:
                avatar = None
            if avatar is not None:
                hide_initial_remote_enemy(avatar, vehicle)
            return result

        def vehicle_start_visual(vehicle):
            """Run every stock controller without publishing an enemy yet.

            Exact #1513 unconditionally calls ``show(True)`` and immediately
            registers the marker/minimap entry inside ``Vehicle.startVisual``.
            Hiding them after the method returns is too late: the GUI adaptor
            has already queued one visible frame.  The scoped attribute gates
            below alter only those two direct calls; appearance, physics,
            wheels, tracks, sounds and model ownership remain stock-owned.
            """
            original = compatibility._original_vehicle_start_visual
            if not compatibility._battle_active:
                return original(vehicle)
            try:
                avatar = runtime.bigworld.player()
            except ReferenceError:
                avatar = None
            if avatar is not None:
                prime_initial_remote_enemy(avatar, vehicle)
            previous = compatibility._vehicle_starting_visual
            compatibility._vehicle_starting_visual = vehicle
            try:
                result = original(vehicle)
            finally:
                compatibility._vehicle_starting_visual = previous
            if avatar is not None:
                hide_initial_remote_enemy(avatar, vehicle)
            return result

        def avatar_prereqs_loaded(avatar, resource_names, resource_refs):
            if compatibility._fake_connected:
                try:
                    player = runtime.bigworld.player()
                except ReferenceError:
                    player = None
                if (player is not avatar or
                        not getattr(avatar, _OFFLINE_INIT_COMPLETE, False) or
                        not getattr(avatar, _OFFLINE_PLAYER_READY, False)):
                    # BigWorld resource callbacks cannot be cancelled.  Drop
                    # one retained callback after the PyEntity has left the
                    # player boundary instead of invoking a cleared instance.
                    return None
            return compatibility._original_avatar_prereqs_loaded(
                avatar, resource_names, resource_refs)

        def avatar_aux_physics(avatar, previous):
            original = compatibility._original_avatar_aux_physics
            if not compatibility._battle_active:
                return original(avatar, previous)
            outer_avatar = compatibility._avatar_syncing_aux_physics
            compatibility._avatar_syncing_aux_physics = avatar
            try:
                return original(avatar, previous)
            finally:
                compatibility._avatar_syncing_aux_physics = outer_avatar

        def visible_native_remote_collisions(
                vehicle, start_point, end_point):
            """Collide against the same pose that #1513 currently draws."""
            if not compatibility._battle_active:
                return None
            overlay = compatibility._vehicle_property_overlays.get(
                id(vehicle))
            if overlay is None or not overlay.get('_pose_active'):
                return None
            try:
                native_remote = bool(
                    compatibility._original_vehicle_getattribute(
                        vehicle, '_offlineNativeRemote'))
            except AttributeError:
                native_remote = False
            if not native_remote:
                return None
            return runtime.visible_vehicle_collision(
                vehicle, overlay['matrix'], start_point, end_point,
                runtime.math)

        def vehicle_collide_segment(
                vehicle, start_point, end_point, skipGun=False,
                optimized=True):
            collisions = visible_native_remote_collisions(
                vehicle, start_point, end_point)
            if collisions is None:
                return compatibility._original_vehicle_collide_segment(
                    vehicle, start_point, end_point, skipGun, optimized)
            if skipGun:
                collisions = [
                    item for item in collisions
                    if item.compName != 'vehicleGun']
            if not collisions:
                return None
            closest = min(collisions, key=lambda item: item.dist)
            material = closest.matInfo
            armor = getattr(material, 'armor', 0) \
                if material is not None else 0
            return runtime.segment_collision_result_type(
                closest.dist, closest.hitAngleCos, armor)

        def vehicle_collide_segment_ext(vehicle, start_point, end_point):
            collisions = visible_native_remote_collisions(
                vehicle, start_point, end_point)
            if collisions is None:
                return compatibility._original_vehicle_collide_segment_ext(
                    vehicle, start_point, end_point)
            if not collisions:
                return None
            collisions = list(collisions)
            collisions.sort(key=lambda item: item.dist)
            return collisions

        def vehicle_getattribute(vehicle, name):
            caller_code = None
            locked_target_code = \
                compatibility._gun_rotator_predict_locked_target_code
            if (compatibility._vehicle_starting_visual is not None or
                    compatibility._vehicle_starting_wg_physics is not None or
                    compatibility._vehicle_syncing_gun_angles is not None or
                    compatibility._avatar_syncing_aux_physics is not None or
                    compatibility._avatar_entering_vehicle is not None or
                    name in ('filter', 'position')):
                try:
                    caller_code = sys._getframe(1).f_code
                except (AttributeError, ValueError):
                    pass
            if (compatibility._battle_active and
                    name in ('health', 'isCrewActive',
                             'position', 'yaw', 'matrix')):
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if overlay is not None and name in overlay:
                    if (name == 'position' and
                            caller_code is locked_target_code):
                        try:
                            native_remote = bool(
                                compatibility._original_vehicle_getattribute(
                                    vehicle, '_offlineNativeRemote'))
                        except AttributeError:
                            native_remote = False
                        if native_remote:
                            return runtime.math.Matrix(
                                overlay['matrix']).translation
                    return overlay[name]
            direct_start_visual = (
                compatibility._vehicle_starting_visual is vehicle and
                caller_code is compatibility._vehicle_start_visual_code)
            if (direct_start_visual and compatibility._battle_active and
                    name in ('show', 'guiSessionProvider')):
                try:
                    marker_visible = bool(
                        compatibility._original_vehicle_getattribute(
                            vehicle, '_offlineNativeMarkerVisible'))
                except AttributeError:
                    marker_visible = True
                try:
                    draw_visible = bool(
                        compatibility._original_vehicle_getattribute(
                            vehicle, '_offlineNativeDrawVisible'))
                except AttributeError:
                    draw_visible = True
                if name == 'show' and not draw_visible:
                    stock_show = \
                        compatibility._original_vehicle_getattribute(
                            vehicle, name)

                    def keep_initial_enemy_hidden(unused_visible):
                        return stock_show(False)

                    return keep_initial_enemy_hidden
                if name == 'guiSessionProvider' and not marker_visible:
                    provider = \
                        compatibility._original_vehicle_getattribute(
                            vehicle, name)
                    return _OfflineInitialVehicleVisualProvider(provider)
            direct_start_filter = (
                compatibility._vehicle_starting_wg_physics is vehicle and
                caller_code is compatibility._vehicle_start_wg_physics_code)
            direct_gun_sync = (
                compatibility._vehicle_syncing_gun_angles is vehicle and
                caller_code is compatibility._vehicle_set_gun_angles_code)
            direct_avatar_aux_sync = False
            if compatibility._avatar_syncing_aux_physics is not None:
                direct_avatar_aux_sync = (
                    caller_code is compatibility._avatar_aux_physics_code)
            direct_avatar_pose_init = (
                compatibility._avatar_entering_vehicle is vehicle and
                caller_code in (
                    compatibility._avatar_vehicle_enter_code,
                    compatibility._avatar_start_vehicle_visual_code))
            overlay = compatibility._vehicle_property_overlays.get(
                id(vehicle))
            direct_fixed_turret_pose = (
                caller_code is compatibility._gun_rotator_stabilised_code and
                overlay is not None and overlay.get('_pose_active'))
            direct_camera_motion = (
                caller_code in (
                    compatibility._camera_acceleration_update_code,
                    compatibility._arcade_oscillator_acceleration_code,
                    compatibility._sniper_oscillator_acceleration_code) and
                overlay is not None and overlay.get('_pose_active'))
            direct_crashed_track_pose = (
                caller_code in (
                    compatibility._crashed_track_setup_assembler_code,
                    compatibility._crashed_track_model_loaded_code) and
                overlay is not None and overlay.get('_pose_active'))
            direct_visible_collision = (
                caller_code is
                compatibility._projectile_segment_may_hit_code and
                overlay is not None and overlay.get('_pose_active'))
            if (name == 'filter' and compatibility._battle_active and
                    direct_visible_collision):
                try:
                    native_remote = bool(
                        compatibility._original_vehicle_getattribute(
                            vehicle, '_offlineNativeRemote'))
                except AttributeError:
                    native_remote = False
                if native_remote:
                    visible_position = runtime.math.Matrix(
                        overlay['matrix']).translation
                    collision_filter = overlay.get('_collision_filter')
                    if collision_filter is None:
                        collision_filter = runtime.remote_filter_type(
                            runtime.math, visible_position,
                            overlay['matrix'])
                        overlay['_collision_filter'] = collision_filter
                    velocity = overlay.get('velocity')
                    if velocity is None:
                        velocity = runtime.math.Vector3(0.0, 0.0, 0.0)
                    collision_filter.update(visible_position, velocity)
                    return collision_filter
            if (name == 'filter' and compatibility._battle_active and
                    (direct_start_filter or direct_gun_sync or
                     direct_avatar_aux_sync or direct_avatar_pose_init or
                     direct_fixed_turret_pose or direct_camera_motion or
                     direct_crashed_track_pose)):
                vehicle_filter = (
                    compatibility._original_vehicle_getattribute(
                        vehicle, name))
                pose_matrix = (
                    overlay.get('stabilised_matrix', overlay['matrix'])
                    if direct_fixed_turret_pose else
                    (overlay['matrix'] if direct_crashed_track_pose else None))
                velocity = (overlay.get('velocity')
                            if direct_camera_motion else None)
                acceleration = (overlay.get('acceleration')
                                if direct_camera_motion else None)
                return _OfflineVehicleFilterSyncProxy(
                    vehicle_filter, pose_matrix, velocity, acceleration)
            if name == 'cell' and compatibility._battle_active:
                try:
                    return compatibility._original_vehicle_getattribute(
                        vehicle, 'fakeCell')
                except AttributeError:
                    player = runtime.bigworld.player()
                    if isinstance(player, avatar_type):
                        try:
                            return compatibility._original_avatar_getattribute(
                                player, 'fakeServer')
                        except AttributeError:
                            pass
            return compatibility._original_vehicle_getattribute(vehicle, name)

        def vehicle_setattr(vehicle, name, value):
            if (compatibility._battle_active and
                    name in ('health', 'isCrewActive')):
                compatibility._vehicle_property_overlays.setdefault(
                    id(vehicle), {})[name] = value
                return None
            if (compatibility._battle_active and
                    name in ('position', 'yaw', 'matrix')):
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if overlay is not None and overlay.get('_pose_active'):
                    overlay[name] = value
                    return None
            return compatibility._original_vehicle_setattr(
                vehicle, name, value)

        def vehicle_get_speed(vehicle):
            if compatibility._battle_active:
                overlay = compatibility._vehicle_property_overlays.get(
                    id(vehicle))
                if (overlay is not None and
                        overlay.get('_pose_active') and
                        'speed' in overlay):
                    return overlay['speed']
            return compatibility._original_vehicle_get_speed(vehicle)

        def avatar_get_speeds(avatar, get_instantaneous=False):
            """Expose copied local physics to stock speed/dispersion users."""
            if compatibility._battle_active:
                try:
                    vehicle_id = compatibility._original_avatar_getattribute(
                        avatar, 'playerVehicleID')
                    vehicle = runtime.bigworld.entity(vehicle_id)
                    overlay = compatibility._vehicle_property_overlays.get(
                        id(vehicle))
                except (AttributeError, ReferenceError, TypeError):
                    overlay = None
                if (overlay is not None and overlay.get('_pose_active') and
                        'speed' in overlay and 'turn_speed' in overlay):
                    return (float(overlay['speed']),
                            float(overlay['turn_speed']))
            return compatibility._original_avatar_get_speeds(
                avatar, get_instantaneous)

        def avatar_auto_aim(avatar, target):
            """Admit the private remote Vehicle to the stock lock lifecycle.

            ``BigWorld.target()`` cannot return our Python gameplay adapter:
            its rendered owner is an ``OfflineEntity``.  The battle runtime
            therefore publishes the vehicle selected by the same precise ray
            that owns the outline.  Once admitted, keep #1513's native state,
            aiming mode, gun-rotator mode and sound notification sequence.
            """
            if not compatibility._battle_active:
                return compatibility._original_avatar_auto_aim(
                    avatar, target)
            current_id = compatibility._original_avatar_getattribute(
                avatar, '_PlayerAvatar__autoAimVehID')
            candidate = compatibility._target_lock_candidate
            caller_code = sys._getframe(1).f_code
            is_lock_input = (
                compatibility._target_lock_input_pending and
                avatar is compatibility._target_lock_input_avatar and
                caller_code in (
                    compatibility._arcade_handle_key_event_code,
                    compatibility._sniper_handle_key_event_code))
            if is_lock_input:
                compatibility._target_lock_input_pending = False
                compatibility._target_lock_input_avatar = None
                if target is None and candidate is not None:
                    target = candidate
            if (candidate is not None and
                    target is getattr(candidate, 'bw_entity', None)):
                target = candidate
            if not bool(getattr(
                    target, '_offlineLANPresentation', False)):
                return compatibility._original_avatar_auto_aim(
                    avatar, target)

            alive = getattr(target, 'isAlive')
            alive = alive() if callable(alive) else bool(alive)
            rejected = (
                int(getattr(target, 'id')) == int(current_id) or
                int(getattr(target, 'team')) == int(getattr(avatar, 'team')) or
                not alive)
            if rejected:
                if current_id:
                    # Let stock #1513 own the full unlock transition,
                    # including its aimingInfo convergence timestamp/factor.
                    return compatibility._original_avatar_auto_aim(
                        avatar, None)
                return None

            vehicle_id = int(target.id)
            setattr(avatar, '_PlayerAvatar__autoAimVehID', vehicle_id)
            avatar.cell.autoAim(vehicle_id)
            aiming_mode = runtime.constants.AIMING_MODE.TARGET_LOCK
            avatar.inputHandler.setAimingMode(True, aiming_mode)
            avatar.gunRotator.clientMode = False
            aim_sound = runtime.avatar_module.AimSound
            avatar.onLockTarget(aim_sound.TARGET_LOCKED, True)
            runtime.avatar_module.TriggersManager.g_manager.activateTrigger(
                runtime.avatar_module.TRIGGER_TYPE.AUTO_AIM_AT_VEHICLE,
                vehicleId=vehicle_id)
            return None

        def ammo_change_setting(controller, int_cd, avatar=None):
            """Let one offline CURRENT transaction own its native edge order.

            Stock #1513 optimistically applies CURRENT_SHELLS before invoking
            the cell mailbox.  The LAN runtime must close the old positive
            reload before it publishes the new current shell, otherwise the
            old completion edge is attributed to the new ammo slot.  NEXT and
            every non-offline call retain the unmodified stock behavior.
            """
            if not compatibility._battle_active:
                return compatibility._original_ammo_change_setting(
                    controller, int_cd, avatar)
            getter = runtime.avatar_getter
            if not getter.isVehicleAlive(avatar):
                return False
            code = controller.getNextSettingCode(int_cd)
            settings = getattr(runtime.constants, 'VEHICLE_SETTING', None)
            current_shells = getattr(settings, 'CURRENT_SHELLS', None)
            if current_shells is None or code != current_shells:
                return compatibility._original_ammo_change_setting(
                    controller, int_cd, avatar)
            if not getter.isPlayerOnArena(avatar):
                return compatibility._original_ammo_change_setting(
                    controller, int_cd, avatar)
            # AvatarServerBridge invokes BattleRuntime synchronously.  That
            # transaction publishes old reload 0, CURRENT_SHELLS and the new
            # positive duration before this stock input call returns.
            getter.changeVehicleSetting(code, int_cd, avatar)
            return True

        def handle_target_lock_input(original, control, is_down, key, mods,
                                     event):
            if not compatibility._battle_active:
                return original(control, is_down, key, mods, event)
            command_mapping = runtime.control_modes.CommandMapping
            is_lock_input = (
                bool(is_down) and
                command_mapping.g_instance.isFired(
                    command_mapping.CMD_CM_LOCK_TARGET, key))
            if (is_lock_input and
                    compatibility._target_lock_input_pending):
                raise RuntimeError('nested target-lock input is not allowed')
            if is_lock_input:
                compatibility._target_lock_input_pending = True
                compatibility._target_lock_input_avatar = \
                    runtime.bigworld.player()
            try:
                return original(control, is_down, key, mods, event)
            finally:
                if is_lock_input:
                    compatibility._target_lock_input_pending = False
                    compatibility._target_lock_input_avatar = None

        def strategic_camera_update(camera):
            """Keep the SPG camera loop alive after one failed tick.

            CallbackDelayer removes its entry before it calls the tick and
            re-arms only from the returned delay, so a single exception here
            would stop mouse aiming for the rest of the battle.  Report the
            first failure and return the stock 0.0 reschedule delay.
            """
            original = compatibility._original_strategic_camera_update
            if not compatibility._battle_active:
                return original(camera)
            try:
                return original(camera)
            except Exception:
                if not compatibility._strategic_camera_failure_reported:
                    compatibility._strategic_camera_failure_reported = True
                    print('[Offline LAN 0.9.22] strategic camera tick failed; '
                          'the camera loop continues')
                    traceback.print_exc()
                return 0.0

        def arcade_handle_key_event(control, is_down, key, mods, event):
            return handle_target_lock_input(
                compatibility._original_arcade_handle_key_event,
                control, is_down, key, mods, event)

        def sniper_handle_key_event(control, is_down, key, mods, event):
            return handle_target_lock_input(
                compatibility._original_sniper_handle_key_event,
                control, is_down, key, mods, event)

        def vehicle_leave_world(vehicle):
            original = compatibility._original_vehicle_leave_world
            if not compatibility._battle_active:
                return original(vehicle)
            try:
                player = runtime.bigworld.player()
            except ReferenceError:
                player = None
            callback = getattr(player, 'vehicle_onLeaveWorld', None)
            if callable(callback):
                return original(vehicle)

            # PlayerAvatar.onBecomeNonPlayer normally stopped every Vehicle
            # before the engine clears its PyEntities.  Exact #1513 then calls
            # Vehicle.onLeaveWorld after BigWorld.player() has already become
            # None, although the stock method dereferences it unconditionally.
            # Finish only the two remaining Vehicle-owned stages here.
            stop_extras = getattr(vehicle, '_Vehicle__stopExtras', None)
            if callable(stop_extras):
                stop_extras()
            if bool(getattr(vehicle, 'isStarted', False)):
                stop_visual = getattr(vehicle, 'stopVisual', None)
                if not callable(stop_visual):
                    raise RuntimeError(
                        'retired offline Vehicle cannot stop its visual')
                stop_visual(False)
            if bool(getattr(vehicle, 'isStarted', False)):
                raise RuntimeError(
                    'retired offline Vehicle remained visually started')
            return None

        def vehicle_start_wg_physics(vehicle):
            original = compatibility._original_vehicle_start_wg_physics
            if not compatibility._battle_active:
                return original(vehicle)
            previous = compatibility._vehicle_starting_wg_physics
            compatibility._vehicle_starting_wg_physics = vehicle
            try:
                # Keep the pinned client's complete physics setup.  The
                # scoped filter proxy suppresses only its unsafe initial
                # syncGunAngles native call.
                return original(vehicle)
            finally:
                compatibility._vehicle_starting_wg_physics = previous

        def vehicle_set_gun_angles(vehicle, previous):
            original = compatibility._original_vehicle_set_gun_angles
            if not compatibility._battle_active:
                return original(vehicle, previous)
            outer_vehicle = compatibility._vehicle_syncing_gun_angles
            compatibility._vehicle_syncing_gun_angles = vehicle
            try:
                return original(vehicle, previous)
            finally:
                compatibility._vehicle_syncing_gun_angles = outer_vehicle

        def compound_getattribute(appearance, name):
            value = compatibility._original_compound_getattribute(
                appearance, name)
            if (name == '_CompoundAppearance__filter' and
                    compatibility._battle_active and
                    compatibility._compound_refreshing_models is appearance):
                # __onModelsRefresh calls deactivate()/activate() before its
                # final gun-angle restore.  Those nested methods also read the
                # private filter and must retain its real identity; activate()
                # stores it back on Vehicle.  Return the proxy only to the
                # direct LOAD_ATTR in the original refresh code object.
                try:
                    caller_code = sys._getframe(1).f_code
                except (AttributeError, ValueError):
                    caller_code = None
                if caller_code is compatibility._compound_models_refresh_code:
                    return _OfflineVehicleFilterSyncProxy(value)
            return value

        def compound_deactivate(appearance, stopEffects=True):
            original = compatibility._original_compound_deactivate
            if not compatibility._battle_active:
                return original(appearance, stopEffects)
            try:
                player = runtime.bigworld.player()
            except ReferenceError:
                player = None
            handler = None
            if player is not None:
                try:
                    handler = getattr(player, 'inputHandler', None)
                except ReferenceError:
                    handler = None
            if handler is not None:
                return original(appearance, stopEffects)

            # PlayerAvatar.__destroyGUI clears inputHandler before its later
            # Vehicle.stopVisual loop.  CompoundAppearance.deactivate still
            # calls removeVehicleFromCameraCollider in that window.  Supply a
            # no-op collider owner for exactly this native call and restore
            # the original object/function even if another teardown stage
            # raises.
            fallback = _OfflineCameraColliderHandler()
            if player is not None:
                try:
                    player.inputHandler = fallback
                except Exception:
                    return original(appearance, stopEffects)
                try:
                    return original(appearance, stopEffects)
                finally:
                    if getattr(player, 'inputHandler', None) is fallback:
                        player.inputHandler = None

            bigworld_dict = getattr(runtime.bigworld, '__dict__', {})
            had_player = 'player' in bigworld_dict
            raw_player = bigworld_dict.get('player')
            original_player = runtime.bigworld.player
            surrogate_arena = type('_OfflineArenaOwner', (object,), {
                'onPeriodChange': _OfflineEventSink()})()
            surrogate = type('_OfflineColliderOwner', (object,), {
                'inputHandler': fallback, 'arena': surrogate_arena})()

            def collider_owner(*unused_args, **unused_kwargs):
                return surrogate

            runtime.bigworld.player = collider_owner
            try:
                return original(appearance, stopEffects)
            finally:
                if runtime.bigworld.player is collider_owner:
                    if had_player:
                        runtime.bigworld.player = raw_player
                    else:
                        delattr(runtime.bigworld, 'player')

        def compound_models_refresh(appearance, model_state, resource_list):
            original = compatibility._original_compound_models_refresh
            if not compatibility._battle_active:
                return original(appearance, model_state, resource_list)
            outer_appearance = compatibility._compound_refreshing_models
            compatibility._compound_refreshing_models = appearance
            try:
                return original(appearance, model_state, resource_list)
            finally:
                compatibility._compound_refreshing_models = outer_appearance

        def account_getattribute(account, name):
            if name in ('base', 'cell', 'server'):
                try:
                    is_offline = compatibility._original_account_getattribute(
                        account, 'isOffline')
                except AttributeError:
                    is_offline = False
                if is_offline:
                    return compatibility._original_account_getattribute(
                        account, 'fakeServer')
            return compatibility._original_account_getattribute(account, name)

        def account_become_player(account):
            is_offline = bool(getattr(account, 'isOffline', False))
            if not is_offline:
                return compatibility._original_account_become_player(account)
            if not getattr(account, _OFFLINE_INIT_COMPLETE, False):
                raise RuntimeError(
                    'offline Account initialization did not complete')

            # PlayerAccount.onBecomePlayer in exact build #1513 starts by
            # calling BigWorld.clearAllSpaces().  Our client-only Account is
            # itself hosted in a newly-created space, so the native call would
            # retire the Account that is currently becoming the player.  Skip
            # only that one destructive call and restore the engine function
            # before any lobby code runs.
            original_clear_all_spaces = getattr(
                runtime.bigworld, 'clearAllSpaces', None)

            def preserve_offline_account_space():
                return None

            if callable(original_clear_all_spaces):
                runtime.bigworld.clearAllSpaces = \
                    preserve_offline_account_space
            # See the Avatar wrapper above: a native Account can bind helpers,
            # chat and global events and then fail before the lobby is ready.
            setattr(account, _OFFLINE_RETIRE_PENDING, True)
            try:
                result = compatibility._original_account_become_player(
                    account)
            finally:
                if (callable(original_clear_all_spaces) and
                        getattr(runtime.bigworld, 'clearAllSpaces', None) is
                        preserve_offline_account_space):
                    runtime.bigworld.clearAllSpaces = \
                        original_clear_all_spaces

            if compatibility._show_lobby:
                try:
                    # A stale filter pickle in the shared preferences file
                    # fails CarouselFilter.load's key assertion at hangar load.
                    _sanitize_account_filters()
                except Exception as error:
                    print('[Offline LAN 0.9.22] saved lobby filters could '
                          'not be repaired: %s' % error)
                show_gui = getattr(account, 'showGUI', None)
                if callable(show_gui):
                    show_gui(_pickle.dumps(
                        dict(_LOBBY_GUI_CONTEXT), _pickle.HIGHEST_PROTOCOL))
            setattr(account, _OFFLINE_PLAYER_READY, True)
            return result

        def retire_fake_connection():
            """Run every native disconnect boundary and return its first error."""
            compatibility._connecting = False
            first_error = None

            # A native disconnect retires the current player and its spaces.
            # The fake transport has no engine connection to perform that
            # cleanup for us, so do it before repository listeners run.
            try:
                compatibility.retire_current_player()
            except Exception as error:
                first_error = error
            clear_all_spaces = getattr(runtime.bigworld, 'clearAllSpaces', None)
            if callable(clear_all_spaces):
                try:
                    clear_all_spaces()
                except Exception as error:
                    if first_error is None:
                        first_error = error
            compatibility._fake_connected = False

            try:
                setattr(runtime.connection_manager,
                        '_ConnectionManager__connectionStatus',
                        runtime.login_status.NOT_SET)
            except Exception as error:
                if first_error is None:
                    first_error = error

            notifications = (
                (getattr(runtime.bigworld,
                         'WGC_onServerResponse', None), (False,)),
                (getattr(runtime.connection_manager,
                         'onDisconnected', None), ()),
                (getattr(runtime.player_events,
                         'onDisconnected', None), ()),
            )
            for notification, arguments in notifications:
                if not callable(notification):
                    continue
                try:
                    notification(*arguments)
                except Exception as error:
                    if first_error is None:
                        first_error = error

            # Exact Event dispatch stops at the first failing listener.  Do
            # not let a retained repository outlive a failed listener or a
            # partially-created PyEntity.
            delete_repository = getattr(
                runtime.account_module, '_delAccountRepository', None)
            if callable(delete_repository):
                try:
                    delete_repository()
                except Exception as error:
                    if first_error is None:
                        first_error = error
                finally:
                    # A partial repository can fail inside its own close path
                    # before exact _delAccountRepository clears the global.
                    # Never make the next Account reuse that object.
                    if hasattr(runtime.account_module,
                               'g_accountRepository'):
                        runtime.account_module.g_accountRepository = None
            return first_error

        def connect(server, login_params, progress):
            if server != OFFLINE_SERVER_ADDRESS:
                return compatibility._original_connect(
                    server, login_params, progress)
            compatibility._fake_connected = True
            try:
                # The progress callback mutates connection state and invokes
                # arbitrary native listeners.  It belongs to the same
                # transaction as Account construction, not before rollback.
                progress(1, runtime.login_status.LOGGED_ON, '{}')
                compatibility._create_account_player()
                compatibility._connecting = False
            except Exception:
                retire_fake_connection()
                raise
            return None

        def disconnect():
            if not compatibility._fake_connected:
                return compatibility._original_disconnect()
            first_error = retire_fake_connection()
            if first_error is not None:
                raise first_error
            return None

        def server_time():
            """Advance the native battle clock on the client-only connection.

            The retail server owns ``BigWorld.serverTime()``.  It remains
            frozen on our fake connection, while #1513's stock period
            controller subtracts it from ``periodEndTime`` once per second.
            Reuse the 0.8.2 offline clock law, scoped to an active LAN battle,
            and preserve the original epoch so every native deadline remains
            in one coordinate system.
            """
            if (compatibility._battle_active and
                    compatibility._battle_server_time_origin is not None and
                    compatibility._battle_clock_origin is not None):
                clock = getattr(runtime.bigworld, 'time', None)
                if callable(clock):
                    try:
                        elapsed = (float(clock()) -
                                   compatibility._battle_clock_origin)
                        return (compatibility._battle_server_time_origin +
                                max(0.0, elapsed))
                    except Exception:
                        pass
            return compatibility._original_server_time()

        def debug_update(panel, ping, fps, isLaggingNow, fpsReplay=-1):
            """Render LAN transport health during a client-only battle.

            Exact #1513's DebugController reads BigWorld.statPing() and
            statLagDetected(), which describe the absent retail game-server
            transport.  Keep the stock panel and replace only those two
            values while the explicit LAN battle client is attached.
            """
            client = compatibility._battle_network_client
            if compatibility._battle_active and client is not None:
                connected = bool(getattr(client, 'connected', False))
                sample = getattr(client, 'rtt_ms', None)
                if sample is None:
                    ping = 0 if connected else 999
                else:
                    try:
                        sample = float(sample)
                        if sample != sample:
                            raise ValueError('NaN LAN RTT')
                        ping = int(round(max(0.0, min(sample, 999.0))))
                    except (TypeError, ValueError, OverflowError):
                        ping = 0 if connected else 999
                isLaggingNow = not connected
            return compatibility._original_debug_update(
                panel, ping, fps, isLaggingNow, fpsReplay)

        self._account_init_wrapper = account_init
        self._account_getattribute_wrapper = account_getattribute
        self._account_become_player_wrapper = account_become_player
        self._account_become_non_player_wrapper = account_become_non_player
        self._avatar_init_wrapper = avatar_init
        self._avatar_getattribute_wrapper = avatar_getattribute
        self._avatar_become_player_wrapper = avatar_become_player
        self._avatar_become_non_player_wrapper = avatar_become_non_player
        self._avatar_enter_world_wrapper = avatar_enter_world
        self._avatar_leave_world_wrapper = avatar_leave_world
        self._avatar_vehicle_enter_wrapper = avatar_vehicle_enter
        self._avatar_prereqs_loaded_wrapper = avatar_prereqs_loaded
        self._avatar_aux_physics_wrapper = avatar_aux_physics
        self._avatar_get_speeds_wrapper = avatar_get_speeds
        self._avatar_auto_aim_wrapper = avatar_auto_aim
        self._ammo_change_setting_wrapper = ammo_change_setting
        self._arcade_handle_key_event_wrapper = arcade_handle_key_event
        self._sniper_handle_key_event_wrapper = sniper_handle_key_event
        self._strategic_camera_update_wrapper = strategic_camera_update
        self._control_mode_changed_wrapper = control_mode_changed
        self._vehicle_marker_start_wrapper = vehicle_marker_start
        self._vehicle_marker_stop_wrapper = vehicle_marker_stop
        self._consistent_link_own_vehicle_wrapper = \
            consistent_link_own_vehicle
        self._steady_relink_sources_wrapper = steady_relink_sources
        self._vehicle_getattribute_wrapper = vehicle_getattribute
        self._vehicle_setattr_wrapper = vehicle_setattr
        self._vehicle_get_speed_wrapper = vehicle_get_speed
        self._vehicle_enter_world_wrapper = vehicle_enter_world
        self._vehicle_start_visual_wrapper = vehicle_start_visual
        self._vehicle_leave_world_wrapper = vehicle_leave_world
        self._vehicle_start_wg_physics_wrapper = vehicle_start_wg_physics
        self._vehicle_set_gun_angles_wrapper = vehicle_set_gun_angles
        self._vehicle_collide_segment_wrapper = vehicle_collide_segment
        self._vehicle_collide_segment_ext_wrapper = vehicle_collide_segment_ext
        self._compound_getattribute_wrapper = compound_getattribute
        self._compound_deactivate_wrapper = compound_deactivate
        self._compound_models_refresh_wrapper = compound_models_refresh
        self._connect_wrapper = connect
        self._disconnect_wrapper = disconnect
        self._server_time_wrapper = server_time
        self._debug_update_wrapper = debug_update

        try:
            self._install_host()
            account_type.__init__ = account_init
            account_type.__getattribute__ = account_getattribute
            if self._original_account_become_player is not None:
                account_type.onBecomePlayer = account_become_player
            if self._original_account_become_non_player is not None:
                account_type.onBecomeNonPlayer = account_become_non_player
            avatar_type.__init__ = avatar_init
            avatar_type.__getattribute__ = avatar_getattribute
            avatar_type.onBecomePlayer = avatar_become_player
            if self._original_avatar_become_non_player is not None:
                avatar_type.onBecomeNonPlayer = avatar_become_non_player
            if self._original_avatar_enter_world is not None:
                avatar_type.onEnterWorld = avatar_enter_world
            if self._original_avatar_leave_world is not None:
                avatar_type.onLeaveWorld = avatar_leave_world
            if self._original_avatar_vehicle_enter is not None:
                avatar_type.vehicle_onEnterWorld = avatar_vehicle_enter
            if self._original_avatar_prereqs_loaded is not None:
                avatar_type.onPrereqsLoaded = avatar_prereqs_loaded
            if self._original_avatar_aux_physics is not None:
                avatar_type._PlayerAvatar__onSetOwnVehicleAuxPhysicsData = (
                    avatar_aux_physics)
            if self._original_avatar_get_speeds is not None:
                avatar_type.getOwnVehicleSpeeds = avatar_get_speeds
            avatar_type.autoAim = avatar_auto_aim
            if ammo_controller_type is not None:
                ammo_controller_type.changeSetting = ammo_change_setting
            arcade_control_type.handleKeyEvent = arcade_handle_key_event
            sniper_control_type.handleKeyEvent = sniper_handle_key_event
            strategic_camera_type._StrategicCamera__cameraUpdate = (
                strategic_camera_update)
            input_handler_type.onControlModeChanged = control_mode_changed
            vehicle_marker_type.start = vehicle_marker_start
            vehicle_marker_type.stop = vehicle_marker_stop
            consistent_matrices_type._ConsistentMatrices__linkOwnVehicle = \
                consistent_link_own_vehicle
            steady_matrix_type.relinkSources = steady_relink_sources
            if vehicle_type is not None:
                vehicle_type.__getattribute__ = vehicle_getattribute
                vehicle_type.__setattr__ = vehicle_setattr
                if self._original_vehicle_get_speed is not None:
                    vehicle_type.getSpeed = vehicle_get_speed
                if self._original_vehicle_enter_world is not None:
                    vehicle_type.onEnterWorld = vehicle_enter_world
                if self._original_vehicle_start_visual is not None:
                    vehicle_type.startVisual = vehicle_start_visual
                if self._original_vehicle_leave_world is not None:
                    vehicle_type.onLeaveWorld = vehicle_leave_world
                if self._original_vehicle_start_wg_physics is not None:
                    vehicle_type._Vehicle__startWGPhysics = (
                        vehicle_start_wg_physics)
                if self._original_vehicle_set_gun_angles is not None:
                    vehicle_type.set_gunAnglesPacked = vehicle_set_gun_angles
                vehicle_type.collideSegment = vehicle_collide_segment
                vehicle_type.collideSegmentExt = vehicle_collide_segment_ext
            if compound_type is not None:
                compound_type.__getattribute__ = compound_getattribute
                if self._original_compound_deactivate is not None:
                    compound_type.deactivate = compound_deactivate
                if self._original_compound_models_refresh is not None:
                    compound_type._CompoundAppearance__onModelsRefresh = (
                        compound_models_refresh)
            runtime.bigworld.connect = connect
            runtime.bigworld.disconnect = disconnect
            runtime.bigworld.serverTime = server_time
            if self._original_debug_update is not None:
                debug_panel_type.updateDebugInfo = debug_update
            self._installed = True
        except Exception:
            self._rollback_install()
            raise

    def _install_host(self):
        hosts = self._runtime.predefined_hosts
        for host in hosts._hosts:
            if getattr(host, 'url', None) == OFFLINE_SERVER_ADDRESS:
                self._host = host
                self._host_added = False
                return
        self._host = hosts._makeHostItem(
            OFFLINE_SERVER_ADDRESS,
            OFFLINE_SERVER_ADDRESS,
            OFFLINE_SERVER_ADDRESS)
        hosts._hosts.append(self._host)
        self._host_added = True

    def _rollback_install(self):
        runtime = self._runtime
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        ammo_controller_type = getattr(
            runtime, 'ammo_controller_type', None)
        vehicle_type = getattr(
            getattr(runtime, 'vehicle_module', None), 'Vehicle', None)
        vehicle_marker_type = getattr(
            runtime, 'vehicle_marker_plugin_type', None)
        compound_type = getattr(
            getattr(runtime, 'compound_appearance_module', None),
            'CompoundAppearance', None)
        debug_panel_type = getattr(runtime, 'debug_panel_type', None)
        input_handler_type = getattr(
            getattr(runtime, 'avatar_input_handler', None),
            'AvatarInputHandler', None)
        arcade_control_type = getattr(
            getattr(runtime, 'control_modes', None),
            'ArcadeControlMode', None)
        sniper_control_type = getattr(
            getattr(runtime, 'control_modes', None),
            'SniperControlMode', None)
        consistent_matrices_type = getattr(
            getattr(runtime, 'avatar_position_control', None),
            'ConsistentMatrices', None)
        steady_matrix_type = getattr(
            getattr(runtime, 'steady_vehicle_matrix', None),
            'SteadyVehicleMatrixCalculator', None)
        if (account_type.__dict__.get('__init__') is
                self._account_init_wrapper):
            account_type.__init__ = self._original_account_init
        if (account_type.__dict__.get('__getattribute__') is
                self._account_getattribute_wrapper):
            account_type.__getattribute__ = (
                self._original_account_getattribute)
        if (self._original_account_become_player is not None and
                account_type.__dict__.get('onBecomePlayer') is
                self._account_become_player_wrapper):
            account_type.onBecomePlayer = self._original_account_become_player
        if (self._original_account_become_non_player is not None and
                account_type.__dict__.get('onBecomeNonPlayer') is
                self._account_become_non_player_wrapper):
            account_type.onBecomeNonPlayer = \
                self._original_account_become_non_player
        if (avatar_type.__dict__.get('__init__') is
                self._avatar_init_wrapper):
            avatar_type.__init__ = self._original_avatar_init
        if (avatar_type.__dict__.get('__getattribute__') is
                self._avatar_getattribute_wrapper):
            avatar_type.__getattribute__ = self._original_avatar_getattribute
        if (avatar_type.__dict__.get('onBecomePlayer') is
                self._avatar_become_player_wrapper):
            avatar_type.onBecomePlayer = self._original_avatar_become_player
        if (self._original_avatar_become_non_player is not None and
                avatar_type.__dict__.get('onBecomeNonPlayer') is
                self._avatar_become_non_player_wrapper):
            avatar_type.onBecomeNonPlayer = \
                self._original_avatar_become_non_player
        if (self._original_avatar_enter_world is not None and
                avatar_type.__dict__.get('onEnterWorld') is
                self._avatar_enter_world_wrapper):
            avatar_type.onEnterWorld = self._original_avatar_enter_world
        if (self._original_avatar_leave_world is not None and
                avatar_type.__dict__.get('onLeaveWorld') is
                self._avatar_leave_world_wrapper):
            avatar_type.onLeaveWorld = self._original_avatar_leave_world
        if (self._original_avatar_vehicle_enter is not None and
                avatar_type.__dict__.get('vehicle_onEnterWorld') is
                self._avatar_vehicle_enter_wrapper):
            avatar_type.vehicle_onEnterWorld = (
                self._original_avatar_vehicle_enter)
        if (self._original_avatar_prereqs_loaded is not None and
                avatar_type.__dict__.get('onPrereqsLoaded') is
                self._avatar_prereqs_loaded_wrapper):
            avatar_type.onPrereqsLoaded = (
                self._original_avatar_prereqs_loaded)
        if (self._original_avatar_aux_physics is not None and
                avatar_type.__dict__.get(
                    '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData') is
                self._avatar_aux_physics_wrapper):
            avatar_type._PlayerAvatar__onSetOwnVehicleAuxPhysicsData = (
                self._original_avatar_aux_physics)
        if (self._original_avatar_get_speeds is not None and
                avatar_type.__dict__.get('getOwnVehicleSpeeds') is
                self._avatar_get_speeds_wrapper):
            avatar_type.getOwnVehicleSpeeds = (
                self._original_avatar_get_speeds)
        if (self._original_avatar_auto_aim is not None and
                avatar_type.__dict__.get('autoAim') is
                self._avatar_auto_aim_wrapper):
            avatar_type.autoAim = self._original_avatar_auto_aim
        if (ammo_controller_type is not None and
                self._original_ammo_change_setting is not None and
                ammo_controller_type.__dict__.get('changeSetting') is
                self._ammo_change_setting_wrapper):
            ammo_controller_type.changeSetting = (
                self._original_ammo_change_setting)
        if (arcade_control_type is not None and
                self._original_arcade_handle_key_event is not None and
                arcade_control_type.__dict__.get('handleKeyEvent') is
                self._arcade_handle_key_event_wrapper):
            arcade_control_type.handleKeyEvent = \
                self._original_arcade_handle_key_event
        if (sniper_control_type is not None and
                self._original_sniper_handle_key_event is not None and
                sniper_control_type.__dict__.get('handleKeyEvent') is
                self._sniper_handle_key_event_wrapper):
            sniper_control_type.handleKeyEvent = \
                self._original_sniper_handle_key_event
        strategic_camera_type = getattr(
            self._runtime, 'strategic_camera_type', None)
        if (strategic_camera_type is not None and
                self._original_strategic_camera_update is not None and
                strategic_camera_type.__dict__.get(
                    '_StrategicCamera__cameraUpdate') is
                self._strategic_camera_update_wrapper):
            strategic_camera_type._StrategicCamera__cameraUpdate = (
                self._original_strategic_camera_update)
        if (input_handler_type is not None and
                self._original_control_mode_changed is not None and
                input_handler_type.__dict__.get('onControlModeChanged') is
                self._control_mode_changed_wrapper):
            input_handler_type.onControlModeChanged = (
                self._original_control_mode_changed)
        if (vehicle_marker_type is not None and
                self._original_vehicle_marker_start is not None and
                vehicle_marker_type.__dict__.get('start') is
                self._vehicle_marker_start_wrapper):
            vehicle_marker_type.start = self._original_vehicle_marker_start
        if (vehicle_marker_type is not None and
                self._original_vehicle_marker_stop is not None and
                vehicle_marker_type.__dict__.get('stop') is
                self._vehicle_marker_stop_wrapper):
            vehicle_marker_type.stop = self._original_vehicle_marker_stop
        if (consistent_matrices_type is not None and
                self._original_consistent_link_own_vehicle is not None and
                consistent_matrices_type.__dict__.get(
                    '_ConsistentMatrices__linkOwnVehicle') is
                self._consistent_link_own_vehicle_wrapper):
            consistent_matrices_type._ConsistentMatrices__linkOwnVehicle = (
                self._original_consistent_link_own_vehicle)
        if (steady_matrix_type is not None and
                self._original_steady_relink_sources is not None and
                steady_matrix_type.__dict__.get('relinkSources') is
                self._steady_relink_sources_wrapper):
            steady_matrix_type.relinkSources = (
                self._original_steady_relink_sources)
        if (vehicle_type is not None and
                self._original_vehicle_enter_world is not None and
                vehicle_type.__dict__.get('onEnterWorld') is
                self._vehicle_enter_world_wrapper):
            vehicle_type.onEnterWorld = self._original_vehicle_enter_world
        if (vehicle_type is not None and
                self._original_vehicle_start_visual is not None and
                vehicle_type.__dict__.get('startVisual') is
                self._vehicle_start_visual_wrapper):
            vehicle_type.startVisual = self._original_vehicle_start_visual
        if (vehicle_type is not None and
                self._original_vehicle_start_wg_physics is not None and
                vehicle_type.__dict__.get('_Vehicle__startWGPhysics') is
                self._vehicle_start_wg_physics_wrapper):
            vehicle_type._Vehicle__startWGPhysics = (
                self._original_vehicle_start_wg_physics)
        if (vehicle_type is not None and
                self._original_vehicle_leave_world is not None and
                vehicle_type.__dict__.get('onLeaveWorld') is
                self._vehicle_leave_world_wrapper):
            vehicle_type.onLeaveWorld = self._original_vehicle_leave_world
        if (vehicle_type is not None and
                vehicle_type.__dict__.get('__getattribute__') is
                self._vehicle_getattribute_wrapper):
            vehicle_type.__getattribute__ = self._original_vehicle_getattribute
        if (vehicle_type is not None and
                vehicle_type.__dict__.get('__setattr__') is
                self._vehicle_setattr_wrapper):
            vehicle_type.__setattr__ = self._original_vehicle_setattr
        if (vehicle_type is not None and
                self._original_vehicle_get_speed is not None and
                vehicle_type.__dict__.get('getSpeed') is
                self._vehicle_get_speed_wrapper):
            vehicle_type.getSpeed = self._original_vehicle_get_speed
        if (vehicle_type is not None and
                self._original_vehicle_set_gun_angles is not None and
                vehicle_type.__dict__.get('set_gunAnglesPacked') is
                self._vehicle_set_gun_angles_wrapper):
            vehicle_type.set_gunAnglesPacked = (
                self._original_vehicle_set_gun_angles)
        if (vehicle_type is not None and
                self._original_vehicle_collide_segment is not None and
                vehicle_type.__dict__.get('collideSegment') is
                self._vehicle_collide_segment_wrapper):
            vehicle_type.collideSegment = (
                self._original_vehicle_collide_segment)
        if (vehicle_type is not None and
                self._original_vehicle_collide_segment_ext is not None and
                vehicle_type.__dict__.get('collideSegmentExt') is
                self._vehicle_collide_segment_ext_wrapper):
            vehicle_type.collideSegmentExt = (
                self._original_vehicle_collide_segment_ext)
        if (compound_type is not None and
                self._original_compound_models_refresh is not None and
                compound_type.__dict__.get(
                    '_CompoundAppearance__onModelsRefresh') is
                self._compound_models_refresh_wrapper):
            compound_type._CompoundAppearance__onModelsRefresh = (
                self._original_compound_models_refresh)
        if (compound_type is not None and
                self._original_compound_deactivate is not None and
                compound_type.__dict__.get('deactivate') is
                self._compound_deactivate_wrapper):
            compound_type.deactivate = self._original_compound_deactivate
        if (compound_type is not None and
                compound_type.__dict__.get('__getattribute__') is
                self._compound_getattribute_wrapper):
            compound_type.__getattribute__ = (
                self._original_compound_getattribute)
        if runtime.bigworld.connect is self._connect_wrapper:
            runtime.bigworld.connect = self._original_connect
        if runtime.bigworld.disconnect is self._disconnect_wrapper:
            runtime.bigworld.disconnect = self._original_disconnect
        if runtime.bigworld.serverTime is self._server_time_wrapper:
            runtime.bigworld.serverTime = self._original_server_time
        if (debug_panel_type is not None and
                self._original_debug_update is not None and
                debug_panel_type.__dict__.get('updateDebugInfo') is
                self._debug_update_wrapper):
            debug_panel_type.updateDebugInfo = self._original_debug_update
        if self._host_added and self._host is not None:
            try:
                runtime.predefined_hosts._hosts.remove(self._host)
            except ValueError:
                pass
        self._host = None
        self._host_added = False
        self._vehicle_starting_wg_physics = None
        self._vehicle_start_wg_physics_code = None
        self._vehicle_syncing_gun_angles = None
        self._vehicle_set_gun_angles_code = None
        self._gun_rotator_stabilised_code = None
        self._gun_rotator_predict_locked_target_code = None
        self._projectile_segment_may_hit_code = None
        self._original_vehicle_collide_segment = None
        self._original_vehicle_collide_segment_ext = None
        self._vehicle_collide_segment_wrapper = None
        self._vehicle_collide_segment_ext_wrapper = None
        self._camera_acceleration_update_code = None
        self._arcade_oscillator_acceleration_code = None
        self._sniper_oscillator_acceleration_code = None
        self._avatar_syncing_aux_physics = None
        self._avatar_aux_physics_code = None
        self._avatar_entering_vehicle = None
        self._avatar_vehicle_enter_code = None
        self._avatar_start_vehicle_visual_code = None
        self._compound_refreshing_models = None
        self._compound_models_refresh_code = None
        self._battle_network_client = None
        self._battle_server_time_origin = None
        self._battle_clock_origin = None
        self._vehicle_property_overlays = {}
        self._vehicle_marker_plugins = {}
        self._battle_player_vehicle_id = 0
        self._postmortem_vehicle_id = 0
        self._control_mode_listener = None
        self._target_lock_candidate = None
        self._target_lock_input_pending = False
        self._target_lock_input_avatar = None
        self._installed = False

    def garage_state(self):
        """Return the one live garage, seeded from the bootstrap snapshot.

        Leaving battle destroys the lobby Account and builds a new one, so a
        garage owned by the retired Account would fall back to the bootstrap
        fitting and undo whatever the player changed in this session.
        """
        if self._garage_state is None:
            snapshot = self._account_context.get('selected_vehicle')
            if not snapshot:
                return None
            from gui.mods.offline_lan_0922.account_rpc import garage
            self._garage_state = garage.GarageState(snapshot)
        return self._garage_state

    def seed_account_context(self):
        """Build the request context handed to one Account entity."""
        context = dict(self._account_context)
        context['account_state'] = self._account_state
        garage_state = self.garage_state()
        if garage_state is not None:
            context['garage'] = garage_state
            context['selected_vehicle'] = garage_state.snapshot()
        return context

    def dispatch_account_int_command(self, command, values):
        """Persist a server-owned setting while Avatar owns the connection."""
        from gui.mods.offline_lan_0922.account_rpc import requests
        result = requests.dispatch(
            command, {'account_state': self._account_state}, (values,))
        return result.result_id, result.error

    def connect(self, show_lobby=False, account_context=None):
        self.install()
        if self.is_ready() or self._connecting:
            return
        self._show_lobby = bool(show_lobby)
        self._account_context = dict(account_context or {})
        self._garage_state = None
        provided_state = self._account_context.get('account_state')
        if provided_state is not None:
            self._account_state = provided_state
        self._connecting = True
        params = {
            'login': 'offline',
            'auth_method': 'basic',
            'session': '',
            'token2': '',
        }
        try:
            self._runtime.connection_manager.initiateConnection(
                params, '', OFFLINE_SERVER_ADDRESS)
        except Exception:
            self._connecting = False
            raise

    def is_ready(self):
        if not self._installed:
            return False
        try:
            player = self._runtime.bigworld.player()
            return (self._runtime.connection_manager.isConnected() and
                    player is not None and
                    bool(getattr(player, 'isOffline', False)))
        except Exception:
            return False

    def _discard_partial_account(self):
        runtime = self._runtime
        clear_all_spaces = getattr(runtime.bigworld, 'clearAllSpaces', None)
        if callable(clear_all_spaces):
            try:
                clear_all_spaces()
            except Exception:
                pass
        delete_repository = getattr(
            runtime.account_module, '_delAccountRepository', None)
        if callable(delete_repository):
            try:
                delete_repository()
            except Exception:
                pass
            finally:
                if hasattr(runtime.account_module, 'g_accountRepository'):
                    runtime.account_module.g_accountRepository = None

    def _create_account_player(self):
        runtime = self._runtime
        was_connecting = self._connecting
        # The login screen can call the patched low-level BigWorld.connect
        # directly after the first-run EULA.  Keep every client-only Account
        # construction inside the same property-injection scope, rather than
        # relying on connect() having been entered through our public helper.
        self._connecting = True
        try:
            try:
                space_id = runtime.bigworld.createSpace()
                account_id = runtime.bigworld.createEntity(
                    'Account', space_id, 0, (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0), {})
                account = runtime.bigworld.entities[account_id]
                if not getattr(account, _OFFLINE_INIT_COMPLETE, False):
                    raise RuntimeError(
                        'BigWorld returned a partial offline Account')
                runtime.bigworld.player(account)
                if not getattr(account, _OFFLINE_PLAYER_READY, False):
                    raise RuntimeError(
                        'BigWorld did not promote the offline Account')
                return account
            except Exception:
                self._discard_partial_account()
                raise
        finally:
            self._connecting = was_connecting

    def restore_lobby_account(self):
        """Recreate the fake Account after #1513 clears battle entities.

        ``OfflineMapCreator.destroy()`` calls ``clearEntitiesAndSpaces()``, so
        merely switching the application back to lobby leaves no player
        mailbox.  Creating another Account through the normal patched
        constructor rebinds the retained native account repository and starts
        its synchronization lifecycle again.
        """
        self.install()
        if (not self._fake_connected or
                not self._runtime.connection_manager.isConnected()):
            raise RuntimeError('offline connection is not active')
        player = self._runtime.bigworld.player()
        if player is not None:
            if bool(getattr(player, 'isOffline', False)):
                return player
            raise RuntimeError('another player entity is still active')

        was_connecting = self._connecting
        self._connecting = True
        try:
            try:
                # Avatar.onBecomePlayer removes the battle dispatcher.  During
                # Account.showGUI, #1513 broadcasts IGR state before the normal
                # lobby path recreates it; Hangar remains subscribed and reads
                # the dispatcher in that window.  Pre-create the idempotent
                # default dispatcher so the native coroutine can complete.
                create_dispatcher = getattr(
                    self._runtime.prb_loader,
                    'createBattleDispatcher', None)
                if not callable(create_dispatcher):
                    raise RuntimeError(
                        'prebattle dispatcher restore is unavailable')
                create_dispatcher()
                get_dispatcher = getattr(
                    self._runtime.prb_loader, 'getDispatcher', None)
                if (not callable(get_dispatcher) or
                        get_dispatcher() is None):
                    raise RuntimeError(
                        'prebattle dispatcher restore did not complete')
                return self._create_account_player()
            except Exception:
                # A constructor may have partially rebound the shared native
                # repository before failing.  End the fake connection rather
                # than advertise LOGGED_ON with no valid Account mailbox.
                if (self._fake_connected and
                        self._disconnect_wrapper is not None):
                    try:
                        self._disconnect_wrapper()
                    except Exception:
                        pass
                raise
        finally:
            self._connecting = was_connecting

    def retire_current_player(self):
        """Detach the current offline Account or Avatar before engine clear.

        BigWorld removes a PyEntity's complete instance dictionary.  Native
        Account and Avatar retirement must therefore first release ChatManager,
        account helpers, battle controllers and GUI-space listeners that retain
        the object.  The patched onBecomeNonPlayer methods make this boundary
        idempotent when the later engine clear delivers the callback again.
        """
        self.install()
        try:
            player = self._runtime.bigworld.player()
        except ReferenceError:
            return False
        if player is None:
            return False
        account_type = self._runtime.account_module.PlayerAccount
        avatar_type = self._runtime.avatar_module.PlayerAvatar
        if not isinstance(player, (account_type, avatar_type)):
            raise RuntimeError('unsupported player entity is active')
        if not getattr(player, _OFFLINE_INIT_COMPLETE, False):
            # The wrapper opens its retirement token only after construction
            # has completed and immediately before native onBecomePlayer.
            # A partial constructor therefore has no stock player lifecycle
            # to detach; its owning map cleanup must clear it directly.
            return False
        if not getattr(player, _OFFLINE_RETIRE_PENDING, False):
            return False
        retire = getattr(player, 'onBecomeNonPlayer', None)
        if not callable(retire):
            raise RuntimeError('player retirement boundary is unavailable')
        retire()
        if getattr(player, _OFFLINE_RETIRE_PENDING, False):
            raise RuntimeError('player retirement did not finish')
        if getattr(player, _OFFLINE_PLAYER_READY, False):
            raise RuntimeError('retired player is still marked ready')
        chat_manager = getattr(self._runtime, 'chat_manager', None)
        if (chat_manager is not None and
                getattr(chat_manager, 'playerProxy', None) is not None):
            raise RuntimeError('chat manager retained the retired player')
        return True

    def activate_map(self):
        if self._runtime is None:
            raise RuntimeError('offline compatibility is not installed')
        self._runtime.offline_map_creator.SetActive(True)

    def configure_battle(self, gui_type=None, bonus_type=None,
                         player_name=None, player_team=None,
                         arena_type_id=None):
        """Enable the normal battle UI/input path for the next native Avatar."""
        if player_name is not None:
            player_name = _entity_bytes(player_name, 'OfflinePlayer')
        if player_team is not None:
            player_team = int(player_team)
            if player_team not in (1, 2):
                raise ValueError('Avatar team must be 1 or 2')
        if arena_type_id is not None:
            self._battle_arena_type_id = int(arena_type_id)
        self.install()
        self._battle_active = True
        self._strategic_camera_failure_reported = False
        self._vehicle_property_overlays = {}
        self._battle_player_vehicle_id = 0
        self._postmortem_vehicle_id = 0
        self._target_lock_candidate = None
        self._native_battle = True
        self._battle_gui_type = gui_type
        self._battle_bonus_type = bonus_type
        if player_name is not None:
            self._battle_player_name = player_name
        if player_team is not None:
            self._battle_player_team = player_team
        self._battle_server_time_origin = float(
            self._original_server_time())
        self._battle_clock_origin = float(self._runtime.bigworld.time())
        self.activate_map()

    def synchronise_vehicle_marker_identity(self, expected_vehicle_id):
        """Refresh the stock marker cache after the local Vehicle exists.

        Exact #1513 starts ``VehicleMarkerPlugin`` while the Avatar still has
        ``playerVehicleID == 0`` and copies that value into a private cache.
        Neither ArenaDP invalidation nor later health events refresh it.  Keep
        the stock damage classification intact and update only that cached
        server identity at the same boundary that validates Avatar/ArenaDP.
        """
        if not self._battle_active:
            raise RuntimeError(
                '#1513 vehicle-marker identity requires an active battle')
        expected_vehicle_id = int(expected_vehicle_id)
        if expected_vehicle_id <= 0:
            raise RuntimeError('#1513 vehicle-marker identity is invalid')
        plugins = tuple(self._vehicle_marker_plugins.values())
        for plugin in plugins:
            try:
                getattr(plugin, '_VehicleMarkerPlugin__playerVehicleID')
            except AttributeError:
                raise RuntimeError(
                    '#1513 vehicle-marker player identity cache is missing')
        self._battle_player_vehicle_id = expected_vehicle_id
        for plugin in plugins:
            setattr(
                plugin, '_VehicleMarkerPlugin__playerVehicleID',
                expected_vehicle_id)
        return self.assert_vehicle_marker_identity(expected_vehicle_id)

    def assert_vehicle_marker_identity(self, expected_vehicle_id):
        """Reject a marker cache that would relabel player hits as ally hits."""
        expected_vehicle_id = int(expected_vehicle_id)
        if self._battle_player_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 vehicle-marker player identity mismatch: '
                'expected=%s runtime=%s' %
                (expected_vehicle_id, self._battle_player_vehicle_id))
        for plugin in tuple(self._vehicle_marker_plugins.values()):
            try:
                cached = getattr(
                    plugin, '_VehicleMarkerPlugin__playerVehicleID')
            except AttributeError:
                raise RuntimeError(
                    '#1513 vehicle-marker player identity cache is missing')
            if cached != expected_vehicle_id:
                raise RuntimeError(
                    '#1513 vehicle-marker player identity mismatch: '
                    'expected=%s cached=%s' %
                    (expected_vehicle_id, cached))
        return True

    def assert_vehicle_marker_damage_type(self, avatar,
                                          expected_vehicle_id):
        """Run the active stock plugin's exact attacker classifier."""
        expected_vehicle_id = int(expected_vehicle_id)
        self.assert_vehicle_marker_identity(expected_vehicle_id)
        plugins = tuple(self._vehicle_marker_plugins.values())
        if not plugins:
            raise RuntimeError(
                '#1513 active vehicle-marker plugin is unavailable')
        provider = getattr(avatar, 'guiSessionProvider', None)
        present_health = getattr(provider, 'setVehicleHealth', None)
        if not callable(present_health):
            raise RuntimeError(
                '#1513 remote vehicle health presenter is unavailable')
        get_arena_dp = getattr(provider, 'getArenaDP', None)
        if not callable(get_arena_dp):
            raise RuntimeError('#1513 ArenaDP provider is unavailable')
        arena_dp = get_arena_dp()
        get_vehicle_info = getattr(arena_dp, 'getVehicleInfo', None)
        if not callable(get_vehicle_info):
            raise RuntimeError('#1513 ArenaDP vehicle-info API is unavailable')
        attacker_info = get_vehicle_info(expected_vehicle_id)
        try:
            attacker_vehicle_id = int(attacker_info.vehicleID)
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 ArenaDP attacker vehicle info is invalid')
        if attacker_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 ArenaDP attacker identity mismatch: '
                'expected=%s arenaDP=%s' % (
                    expected_vehicle_id, attacker_vehicle_id))
        shared_repo = getattr(provider, 'shared', None)
        feedback = getattr(shared_repo, 'feedback', None)
        if feedback is None:
            raise RuntimeError(
                '#1513 active battle feedback adaptor is unavailable')
        damage_types = getattr(
            self._runtime, 'vehicle_marker_damage_type', None)
        expected_type = getattr(damage_types, 'FROM_PLAYER', None)
        if expected_type is None:
            raise RuntimeError(
                '#1513 vehicle-marker FROM_PLAYER type is unavailable')
        for plugin in plugins:
            plugin_provider = getattr(plugin, 'sessionProvider', None)
            if plugin_provider is not provider:
                raise RuntimeError(
                    '#1513 active vehicle-marker provider mismatch')
            plugin_get_arena_dp = getattr(
                plugin_provider, 'getArenaDP', None)
            if (not callable(plugin_get_arena_dp) or
                    plugin_get_arena_dp() is not arena_dp):
                raise RuntimeError(
                    '#1513 active vehicle-marker ArenaDP mismatch')
            classifier = getattr(
                plugin,
                '_VehicleMarkerPlugin__getVehicleDamageType', None)
            if not callable(classifier):
                raise RuntimeError(
                    '#1513 vehicle-marker damage classifier is unavailable')
            actual_type = classifier(attacker_info)
            if actual_type != expected_type:
                raise RuntimeError(
                    '#1513 vehicle-marker damage classification mismatch: '
                    'expected=%s actual=%s attacker=%s' % (
                        expected_type, actual_type, attacker_vehicle_id))
        return True

    def set_battle_network_client(self, client):
        """Attach the LAN transport whose RTT should drive the battle HUD."""
        self.install()
        self._battle_network_client = client

    def set_control_mode_listener(self, listener):
        """Publish exact #1513 control-mode transitions to the battle owner."""
        if listener is not None and not callable(listener):
            raise TypeError('control-mode listener must be callable')
        self.install()
        self._control_mode_listener = listener

    def set_target_lock_candidate(self, vehicle):
        """Publish the exact synthetic Vehicle under the native crosshair."""
        if vehicle is not None:
            if not self._battle_active:
                raise RuntimeError('target-lock candidate requires a battle')
            if not bool(getattr(
                    vehicle, '_offlineLANPresentation', False) or getattr(
                        vehicle, '_offlineNativeRemote', False)):
                raise TypeError('target-lock candidate is not a remote Vehicle')
            if (bool(getattr(vehicle, '_offlineLANPresentation', False)) and
                    getattr(vehicle, 'bw_entity', None) is None):
                raise ValueError(
                    'target-lock candidate has no visual entity')
        self._target_lock_candidate = vehicle
        return True

    def _target_lock_holds(self, current_id):
        """Say whether the locked target is still safe to hand to native code.

        A wreck stays rendered after death, so presence in the world is not
        enough: the entity must resolve, be alive, be spotted, and still own
        its visual.
        """
        target = self._runtime.bigworld.entity(current_id)
        if target is None:
            return False
        alive = getattr(target, 'isAlive', None)
        alive = alive() if callable(alive) else bool(alive)
        if not alive or not bool(getattr(target, '_spot_visible', True)):
            return False
        if not bool(getattr(target, '_offlineLANPresentation', False)):
            return True
        return (getattr(target, 'bw_entity', None) is not None and
                getattr(target, 'model', None) is not None and
                bool(getattr(target, 'inWorld', False)))

    def validate_target_lock(self, avatar):
        """Release a stock lock when its private remote target leaves AOI."""
        if not self._battle_active:
            return False
        current_id = self._original_avatar_getattribute(
            avatar, '_PlayerAvatar__autoAimVehID')
        if not current_id:
            return False
        try:
            holds = self._target_lock_holds(current_id)
        except Exception:
            # An unreadable target is exactly the case that must not reach
            # native aiming.
            holds = False
        if holds:
            return False
        # Stock owns target-lost state cleanup and convergence bookkeeping.
        self._original_avatar_auto_aim(avatar, None)
        return True

    def release_target_lock(self, avatar, vehicle_id):
        """Drop a lock held on one vehicle, in the frame it stops being safe.

        Waiting for the next validate leaves stock target-lock tracking one
        frame with a dead target it can still reach through native state.
        """
        if not self._battle_active:
            return False
        current_id = self._original_avatar_getattribute(
            avatar, '_PlayerAvatar__autoAimVehID')
        if not current_id or int(current_id) != int(vehicle_id):
            return False
        self._original_avatar_auto_aim(avatar, None)
        return True

    def set_vehicle_pose_overlay(self, vehicle, position, yaw, matrix,
                                 speed=0.0, turn_speed=0.0, velocity=None,
                                 acceleration=None,
                                 steady_rotation_matrix=None,
                                 stabilised_matrix=None):
        """Publish one copied-physics pose through the stock Vehicle API.

        #1513's client-only ``Vehicle`` has no retail cell stream, so its
        native entity transform never advances.  The copied 0.8.2 integrator
        owns the pose; this narrow overlay lets stock camera, gun and
        collision consumers read that same pose without mutating the native
        BigWorld entity or calling the forbidden ``teleport`` operation.
        """
        if not self._battle_active:
            raise RuntimeError('vehicle pose overlay requires a battle')
        overlay = self._vehicle_property_overlays.setdefault(id(vehicle), {})
        overlay['_pose_active'] = True
        overlay['position'] = position
        overlay['yaw'] = float(yaw)
        overlay['matrix'] = matrix
        overlay['steady_rotation_matrix'] = (
            matrix if steady_rotation_matrix is None
            else steady_rotation_matrix)
        overlay['stabilised_matrix'] = (
            matrix if stabilised_matrix is None else stabilised_matrix)
        overlay['speed'] = float(speed)
        overlay['turn_speed'] = float(turn_speed)
        if velocity is not None:
            overlay['velocity'] = velocity
        if acceleration is not None:
            overlay['acceleration'] = acceleration
        return True

    def set_postmortem_vehicle(self, vehicle_id):
        """Mirror #1513's Avatar.vehicle switch for a client-only battle."""
        if not self._battle_active:
            raise RuntimeError('postmortem attachment requires a battle')
        try:
            vehicle_id = int(vehicle_id or 0)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('postmortem vehicle id is invalid')
        if vehicle_id < 0:
            raise ValueError('postmortem vehicle id is invalid')
        if (vehicle_id and
                self._runtime.bigworld.entity(vehicle_id) is None):
            raise RuntimeError('postmortem vehicle is unavailable')
        previous = self._postmortem_vehicle_id
        self._postmortem_vehicle_id = vehicle_id
        return previous

    def clear_postmortem_vehicle(self):
        """Clear a synthetic attachment at any startup/teardown boundary."""
        previous = self._postmortem_vehicle_id
        self._postmortem_vehicle_id = 0
        return previous

    def bind_vehicle_pose_sources(self, avatar, vehicle):
        """Bind every stock #1513 pose provider to one live matrix.

        Python ``Vehicle.__getattribute__`` is not a complete server-state
        boundary: native matrix providers bypass it.  Bind the exact sources
        consumed by the minimap, camera, aiming systems and gun rotator only
        after the copied-physics overlay has established its canonical pose.
        """
        overlay = self._vehicle_property_overlays.get(id(vehicle))
        if (not self._battle_active or overlay is None or
                not overlay.get('_pose_active')):
            raise RuntimeError('player pose source requires a live overlay')
        matrix = overlay['matrix']
        stabilised_matrix = overlay.get('stabilised_matrix', matrix)
        matrices = getattr(avatar, 'consistentMatrices', None)
        if matrices is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        link = getattr(
            matrices, '_ConsistentMatrices__linkOwnVehicle', None)
        attached = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        if not callable(link) or not callable(attached):
            raise RuntimeError(
                '#1513 vehicle matrix binding methods are unavailable')
        link(vehicle)
        attached(matrix, False)

        stabilised = getattr(
            avatar, '_PlayerAvatar__ownVehicleStabMProv', None)
        if stabilised is None:
            raise RuntimeError(
                '#1513 player stabilised matrix provider is unavailable')
        stabilised.target = stabilised_matrix
        if stabilised.target is not stabilised_matrix:
            raise RuntimeError(
                '#1513 player stabilised matrix rejected live pose')

        handler = getattr(avatar, 'inputHandler', None)
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        relink = getattr(calculator, 'relinkSources', None)
        if not callable(relink):
            raise RuntimeError(
                '#1513 steady vehicle matrix relink is unavailable')
        relink()
        return True

    def restore_vehicle_pose_sources(self, avatar, vehicle, native_matrix,
                                     native_stabilised_matrix):
        """Restore the stock providers after the live overlay is cleared."""
        if self._vehicle_property_overlays.get(id(vehicle), {}).get(
                '_pose_active'):
            raise RuntimeError(
                'player pose overlay must be cleared before source restore')
        matrices = getattr(avatar, 'consistentMatrices', None)
        if matrices is None:
            raise RuntimeError('#1513 ConsistentMatrices is unavailable')
        attached = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        if not callable(attached):
            raise RuntimeError(
                '#1513 attached vehicle matrix boundary is unavailable')
        self._original_consistent_link_own_vehicle(matrices, vehicle)
        attached(native_matrix, False)

        stabilised = getattr(
            avatar, '_PlayerAvatar__ownVehicleStabMProv', None)
        if stabilised is None:
            raise RuntimeError(
                '#1513 player stabilised matrix provider is unavailable')
        stabilised.target = native_stabilised_matrix

        handler = getattr(avatar, 'inputHandler', None)
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        self._original_steady_relink_sources(calculator)
        return True

    def clear_vehicle_pose_overlay(self, vehicle):
        overlay = self._vehicle_property_overlays.get(id(vehicle))
        if overlay is None:
            return False
        for name in ('_pose_active', 'position', 'yaw', 'matrix',
                     'steady_rotation_matrix', 'stabilised_matrix',
                     'speed', 'turn_speed', 'velocity', 'acceleration',
                     '_collision_filter'):
            overlay.pop(name, None)
        if not overlay:
            self._vehicle_property_overlays.pop(id(vehicle), None)
        return True

    def native_vehicle_attribute(self, vehicle, name):
        """Read a native Vehicle member while a pose overlay is installed."""
        if self._original_vehicle_getattribute is None:
            raise RuntimeError('native Vehicle attribute boundary is unavailable')
        return self._original_vehicle_getattribute(vehicle, name)

    def attach_avatar_server(self, avatar, server):
        proxy = getattr(avatar, 'fakeServer', None)
        attach = getattr(proxy, 'attach', None)
        if not callable(attach):
            raise RuntimeError('Avatar deferred server is unavailable')
        attach(server)
        # Vehicle.cell is resolved through this same strict bridge.
        for entity in getattr(self._runtime.bigworld, 'entities', {}).values():
            if entity is avatar:
                continue
            try:
                entity.fakeCell = proxy
            except Exception:
                pass

    def _prepare_avatar_properties(self, avatar):
        # A client-only BigWorld Entity accepts its typed properties during
        # Python construction, but its STRING converter accepts Python-2
        # ``str`` only.  LAN JSON values are ``unicode``; normalize them here
        # before any property setter runs.  Public 0.9.22 offline layers use
        # this same pre-super property boundary.
        avatar.fakeServer = _DeferredAvatarServer()
        values = {
            # These are server properties in a retail battle.  Seed the exact
            # LAN roster identity before PlayerAvatar.onBecomePlayer creates
            # ArenaDataProvider; a later name fallback must never disagree
            # with the VEHICLE_ADDED record.
            'name': _entity_bytes(
                self._battle_player_name, 'OfflinePlayer'),
            'team': self._battle_player_team,
            'playerVehicleID': 0,
            'ownVehicleAuxPhysicsData': 0,
            'ownVehicleGear': 0,
            'denunciationsLeft': 10,
            'tkillIsSuspected': False,
            'clientCtx': _entity_bytes(''),
            'isObserverBothTeams': False,
            'isGunLocked': False,
            'arenaUniqueID': 0,
            # ArenaType.id is (gameplayID << 16) | geometryID, and the battle
            # GUI resolves bases and the minimap from it.  Zero named another
            # map's standard bases, which is what drew stray flags.
            'arenaTypeID': self._battle_arena_type_id,
            'arenaBonusType': 0,
            'arenaGuiType': 0,
            'arenaExtraData': {},
            'weatherPresetID': 0,
            'playLimits': {},
            # These four OWN_CLIENT properties come from AvatarObserver.def,
            # not the root Avatar.def.  A client-only Avatar created with an
            # empty property dictionary does not receive server defaults.
            'remoteCamera': {
                'time': 0.0,
                'shotPoint': self._runtime.math.Vector3(0.0, 0.0, 0.0),
                'zoom': 0,
            },
            'isObserverFPV': False,
            'observerFPVControlMode': 0,
            'numOfObservers': 0,
        }
        for name, value in values.items():
            if not hasattr(avatar, name):
                setattr(avatar, name, value)

    def prepare_avatar(self, avatar):
        if not self._native_battle:
            avatar.inputHandler = _OfflineInputHandler()
        if not hasattr(avatar, 'playLimits'):
            avatar.playLimits = {}

    def deactivate_map(self):
        try:
            if self._runtime is not None:
                self._runtime.offline_map_creator.SetActive(False)
        finally:
            self._battle_active = False
            self._native_battle = False
            self._battle_gui_type = None
            self._battle_bonus_type = None
            self._battle_player_name = 'OfflinePlayer'
            self._battle_player_team = 1
            self._battle_network_client = None
            self._target_lock_candidate = None
            self._target_lock_input_pending = False
            self._target_lock_input_avatar = None
            self._battle_server_time_origin = None
            self._battle_clock_origin = None
            self._vehicle_property_overlays = {}
            self._battle_player_vehicle_id = 0
            self._postmortem_vehicle_id = 0

    def disconnect(self):
        if self._runtime is None:
            return
        self._connecting = False
        first_error = None
        try:
            if self._fake_connected:
                if (self._runtime.bigworld.disconnect is
                        self._disconnect_wrapper):
                    self._runtime.connection_manager.disconnect()
                else:
                    self._disconnect_wrapper()
        except Exception as error:
            first_error = error
        try:
            self.deactivate_map()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def fini(self):
        if not self._installed:
            return
        try:
            self.disconnect()
        finally:
            self._arm_sound_shutdown_guard()
            self._rollback_install()

    def _arm_sound_shutdown_guard(self):
        """Protect exact #1513's late SoundGroups.destroy zombie lookup.

        game.fini clears the player Entity before guiModsFini calls this mod,
        but invokes SoundGroups.destroy only afterward.  The engine can retain
        a PlayerAccount identity whose instance dictionary is already empty;
        stock destroy then directly reads its deleted inputHandler.  Arm a
        one-shot instance wrapper that hides only that zombie for the duration
        of the original destroy call and then removes itself.
        """
        module = getattr(self._runtime, 'sound_groups_module', None)
        instance = getattr(module, 'g_instance', None)
        if instance is None:
            return False
        instance_dict = getattr(instance, '__dict__', None)
        if instance_dict is None:
            return False
        current_destroy = getattr(instance, 'destroy', None)
        if not callable(current_destroy):
            return False
        runtime = self._runtime
        player_types = (runtime.account_module.PlayerAccount,
                        runtime.avatar_module.PlayerAvatar)

        def guarded_destroy():
            player_owner_dict = getattr(runtime.bigworld, '__dict__', {})
            had_player_attribute = 'player' in player_owner_dict
            raw_player_attribute = player_owner_dict.get('player')
            original_player = runtime.bigworld.player
            temporary_player = None
            try:
                try:
                    player = original_player()
                except ReferenceError:
                    player = None
                if isinstance(player, player_types):
                    try:
                        player.inputHandler
                    except (AttributeError, ReferenceError):
                        def no_player(*unused_args, **unused_kwargs):
                            return None

                        temporary_player = no_player
                        runtime.bigworld.player = temporary_player
                return current_destroy()
            finally:
                if (temporary_player is not None and
                        runtime.bigworld.player is temporary_player):
                    if had_player_attribute:
                        runtime.bigworld.player = raw_player_attribute
                    else:
                        delattr(runtime.bigworld, 'player')
                if instance.__dict__.get('destroy') is guarded_destroy:
                    delattr(instance, 'destroy')

        instance.destroy = guarded_destroy
        return True


g_compatibility = OfflineCompatibility()
