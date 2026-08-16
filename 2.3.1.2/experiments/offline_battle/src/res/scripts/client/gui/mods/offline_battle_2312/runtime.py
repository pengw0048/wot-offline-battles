"""Offline battle bootstrap for the 2.3.1.2 client.

Flow: stock OfflineMapCreator builds the space and the real PlayerAvatar;
this runtime deactivates the creator around onBecomePlayer so the stock
BattleSessionProvider and AvatarInputHandler start, then creates one real
player Vehicle, publishes the modern roster, delivers playerVehicleID
through the stock property notifier, and finishes with setClientReady plus
an arena PERIOD update. Driving, camera and turret control stay stock.
"""
from __future__ import absolute_import

import cPickle
import math
import zlib

from gui.mods.offline_battle_2312 import account_setup
from gui.mods.offline_battle_2312 import diagnostics
from gui.mods.offline_battle_2312 import entity_setup
from gui.mods.offline_battle_2312.filter_proxy import OfflineFilterProxy
from gui.mods.offline_battle_2312 import server_settings_setup
from gui.mods.offline_battle_2312.avatar_server import AvatarServerBridge

LOG_PREFIX = '[OFFLINE_2312_BATTLE]'
POLL_INTERVAL_SECONDS = 0.25
BOOTSTRAP_TIMEOUT_SECONDS = 180.0
BATTLE_PERIOD_LENGTH_SECONDS = 3600.0
TERRAIN_COLLISION_MASK = 128
TERRAIN_ONLY_FLAGS = 8
TERRAIN_RAY_HEIGHT = 1000.0
VEHICLE_TYPE_NAME = 'ussr:R11_MS-1'
MAILBOX_NAMES = ('base', 'cell', 'server')


class _BridgeState(object):
    active = False
    bridge = None
    sync_scope_vehicle = None


_state = _BridgeState()


class OfflineBattleRuntime(object):

    def __init__(self, requested_space, map_name, write_marker):
        self._requested_space = requested_space
        self._map_name = map_name
        self._write_marker = write_marker
        self._callback_id = None
        self._elapsed = 0.0
        self._stage = 'idle'
        self._failed = False
        self._battle_ready = False
        self._became_player = False
        self._client_ready_requested = False
        self._period_pushed = False
        self._avatar = None
        self._bridge = None
        self._vehicle_id = 0
        self._arena_type_id = None
        self._arena_type = None
        self._class_patches = []
        self._started = False
        self._shutdown_done = False
        self._physics_ready = False
        self._input_deferred = False
        self._input_calls = 0
        self._last_input = None
        self._drive_probe_at = 0.0
        self._world_patch = None

    # ------------------------------------------------------------------
    def _log(self, message, *args):
        self._write_marker('%s %s' % (LOG_PREFIX, message % args if args
                                      else message))

    def _fail(self, reason, error=None):
        if self._failed:
            return
        self._failed = True
        if error is not None:
            detail = repr(error).replace('\n', ' ')[:200]
            self._log('bootstrap_failed reason=%s stage=%s error=%s '
                      'detail=%s', reason, self._stage,
                      type(error).__name__, detail)
        else:
            self._log('bootstrap_failed reason=%s stage=%s', reason,
                      self._stage)

    # ------------------------------------------------------------------
    def route_launch(self, space_name):
        if self._started:
            self._log('route_reentered request=%s', space_name)
            return None
        self._started = True
        self._log('route_enter request=%s map=%s', space_name,
                  self._map_name)
        if space_name != self._requested_space:
            self._fail('space_request_changed')
            return None
        self._schedule(0.0, self._begin)
        return None

    def _schedule(self, delay, target):
        import BigWorld

        def _run():
            self._callback_id = None
            try:
                target()
            except Exception as error:
                self._fail('unhandled_exception', error)

        self._callback_id = BigWorld.callback(delay, _run)

    # ------------------------------------------------------------------
    def _begin(self):
        import BigWorld
        from ArenaType import g_cache as arena_cache
        from OfflineMapCreator import g_offlineMapCreator as creator
        self._stage = 'preflight'
        if creator.Active():
            self._fail('creator_already_active')
            return
        if BigWorld.player() is not None:
            self._fail('player_already_present')
            return
        match = self._find_arena_type(arena_cache)
        if match is None:
            self._fail('arena_not_found')
            return
        self._arena_type_id, self._arena_type = match
        self._install_client_world_patch()
        self._start_gameplay_machine()
        server_settings_setup.install(self._log)
        server_settings_setup.seed(self._log, 'preflight')
        account_setup.install(self._log)
        diagnostics.install(self._log)
        self._install_class_patches()
        self._stage = 'create'
        self._log('native_create_requested map=%s arena_type_id=%s '
                  'gameplay=ctf', self._map_name, self._arena_type_id)
        if not self._create_map(creator):
            return
        self._stage = 'await_ground'
        self._schedule(POLL_INTERVAL_SECONDS, self._poll)

    def _install_client_world_patch(self):
        """Report a battle world while the offline space is created.

        CGF decides which systems to register for a space when the space
        is created, and getClientWorld derives that from the current
        player entity. OfflineMapCreator creates the space before it
        creates the Avatar, so the appearance systems, and with them
        vehicle activation and native physics, are never registered."""
        from cgf_components import client_worlds_helpers
        original = client_worlds_helpers.getClientWorld
        battle = client_worlds_helpers.ClientWorld.BATTLE

        def get_client_world():
            return battle

        client_worlds_helpers.getClientWorld = get_client_world
        self._world_patch = (client_worlds_helpers, 'getClientWorld',
                             original, get_client_world)
        self._log('client_world_forced value=%s previous=%s', battle,
                  original())

    def _restore_client_world_patch(self):
        if self._world_patch is None:
            return
        owner, name, original, installed = self._world_patch
        if getattr(owner, name) is installed:
            setattr(owner, name, original)
        self._world_patch = None
        self._log('client_world_restored')

    def _start_gameplay_machine(self):
        """game.start() skips ServiceLocator.gameplay.start() on the offline
        branch; the battle session posts state events, so start it here."""
        from helpers import dependency
        from skeletons.gameplay import IGameplayLogic
        gameplay = dependency.instance(IGameplayLogic)
        machine = getattr(gameplay, '_GameplayLogic__machine', None)
        if machine is not None and machine.isRunning():
            self._log('gameplay_machine_already_running')
            return
        gameplay.start()
        self._log('gameplay_machine_started running=%s',
                  machine.isRunning() if machine is not None else None)

    def _find_arena_type(self, arena_cache):
        matches = []
        for arena_type_id, arena_type in arena_cache.items():
            if (getattr(arena_type, 'geometryName', None) == self._map_name
                    and getattr(arena_type, 'gameplayName', None) == 'ctf'):
                matches.append((arena_type_id, arena_type))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0])
        return matches[0]

    # ------------------------------------------------------------------
    def _install_class_patches(self):
        import BigWorld
        import Vehicle
        from Avatar import PlayerAvatar
        avatar_cls = PlayerAvatar
        vehicle_cls = Vehicle.Vehicle

        original_avatar_getattribute = avatar_cls.__getattribute__

        def avatar_getattribute(avatar, name):
            if name in MAILBOX_NAMES and _state.active:
                try:
                    return original_avatar_getattribute(avatar, 'fakeServer')
                except AttributeError:
                    pass
            return original_avatar_getattribute(avatar, name)

        original_vehicle_getattribute = vehicle_cls.__getattribute__

        def vehicle_getattribute(vehicle, name):
            if name == 'cell' and _state.active and _state.bridge is not None:
                return _state.bridge
            value = original_vehicle_getattribute(vehicle, name)
            if name == 'filter' and _state.sync_scope_vehicle is vehicle:
                return OfflineFilterProxy(value)
            return value

        original_start_physics = vehicle_cls._Vehicle__startWGPhysics

        def start_wg_physics(vehicle):
            """Mark the moment the native physics owner exists."""
            previous = _state.sync_scope_vehicle
            _state.sync_scope_vehicle = vehicle
            try:
                result = original_start_physics(vehicle)
            finally:
                _state.sync_scope_vehicle = previous
            if not getattr(vehicle, '_offlineBattlePhysicsReady', False):
                vehicle._offlineBattlePhysicsReady = True
                self._physics_ready = True
                self._log('native_physics_ready id=%s', vehicle.id)
                BigWorld.callback(0.0, self._on_physics_ready)
            return result

        original_notify_keys = vehicle_cls.notifyInputKeysDown

        def notify_input_keys_down(vehicle, movement, rotation, handbrake):
            """Hold driving input until the filter owns native physics.

            startVisual only queues the appearance activation, so the
            physics owner appears a tick later. setClientReady feeds an
            initial movement command before that, which would reach the
            filter with no physics."""
            if not getattr(vehicle, '_offlineBattlePhysicsReady', False):
                if not self._input_deferred:
                    self._input_deferred = True
                    self._log('input_deferred reason=physics_not_attached')
                return None
            self._input_calls += 1
            self._last_input = (movement, rotation, handbrake)
            return original_notify_keys(vehicle, movement, rotation,
                                        handbrake)

        original_set_gun_angles = vehicle_cls.set_gunAnglesPacked

        def set_gun_angles_packed(vehicle, previous=None):
            """Keep the stock handler, minus the unsubmittable sync."""
            outer = _state.sync_scope_vehicle
            _state.sync_scope_vehicle = vehicle
            try:
                return original_set_gun_angles(vehicle, previous)
            finally:
                _state.sync_scope_vehicle = outer

        original_aux_physics = avatar_cls._PlayerAvatar__onSetOwnVehicleAuxPhysicsData

        def on_set_aux_physics(avatar, prev):
            """Defer the stabilised-pose sync until physics is attached.

            The native syncStabilisedYPR call needs the filter physics
            owner, which the appearance only attaches from its
            asynchronous CGF activation. Online the server never sends
            this property that early; offline stock startVehicleVisual
            reaches it microseconds after startVisual, where the native
            call faults."""
            vehicle = BigWorld.entity(avatar.playerVehicleID)
            outer = _state.sync_scope_vehicle
            _state.sync_scope_vehicle = vehicle
            try:
                return original_aux_physics(avatar, prev)
            finally:
                _state.sync_scope_vehicle = outer

        original_set_remote_camera = vehicle_cls.set_remoteCamera

        def set_remote_camera(vehicle, _=None):
            if hasattr(vehicle, 'ownVehicle'):
                return original_set_remote_camera(vehicle, _)
            return None

        avatar_cls.__getattribute__ = avatar_getattribute
        vehicle_cls.__getattribute__ = vehicle_getattribute
        vehicle_cls.set_remoteCamera = set_remote_camera
        vehicle_cls._Vehicle__startWGPhysics = start_wg_physics
        avatar_cls._PlayerAvatar__onSetOwnVehicleAuxPhysicsData = (
            on_set_aux_physics)
        vehicle_cls.notifyInputKeysDown = notify_input_keys_down
        vehicle_cls.set_gunAnglesPacked = set_gun_angles_packed
        self._class_patches = [
            (vehicle_cls, 'notifyInputKeysDown', original_notify_keys,
             notify_input_keys_down),
            (vehicle_cls, 'set_gunAnglesPacked', original_set_gun_angles,
             set_gun_angles_packed),
            (vehicle_cls, '_Vehicle__startWGPhysics', original_start_physics,
             start_wg_physics),
            (avatar_cls, '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData',
             original_aux_physics, on_set_aux_physics),
            (avatar_cls, '__getattribute__', original_avatar_getattribute,
             avatar_getattribute),
            (vehicle_cls, '__getattribute__', original_vehicle_getattribute,
             vehicle_getattribute),
            (vehicle_cls, 'set_remoteCamera', original_set_remote_camera,
             set_remote_camera),
        ]
        _state.active = True

    def _restore_class_patches(self):
        _state.active = False
        _state.bridge = None
        for cls, name, original, installed in self._class_patches:
            if getattr(cls, name) is installed:
                setattr(cls, name, original)
        self._class_patches = []

    # ------------------------------------------------------------------
    def _create_map(self, creator):
        import BigWorld
        import constants
        from Avatar import PlayerAvatar
        runtime = self
        original_init = PlayerAvatar.__dict__['__init__']
        original_become = PlayerAvatar.__dict__['onBecomePlayer']

        def routed_init(avatar, *args, **kwargs):
            runtime._avatar = avatar
            runtime._preseed_avatar(avatar, constants)
            result = original_init(avatar, *args, **kwargs)
            import AccountCommands
            bridge = AvatarServerBridge(
                avatar, BigWorld.callback, AccountCommands, runtime._log)
            avatar.fakeServer = bridge
            runtime._bridge = bridge
            _state.bridge = bridge
            return result

        def routed_become_player(avatar):
            avatar.arenaBonusType = constants.ARENA_BONUS_TYPE.REGULAR
            avatar.arenaGuiType = constants.ARENA_GUI_TYPE.RANDOM
            creator.SetActive(False)
            try:
                result = original_become(avatar)
            finally:
                creator.SetActive(True)
            runtime._became_player = True
            return result

        setup_camera_name = '_OfflineMapCreator__setupCamera'

        def bootstrap_setup_camera():
            # Streaming follows the camera, so aim a plain CursorCamera at
            # the spawn until the stock ArcadeCamera takes over.
            import Math
            BigWorld.setWatcher('Visibility/GUI', True)
            avatar = BigWorld.player()
            x, z, yaw, _ = entity_setup.spawn_pose(runtime._arena_type)
            camera = BigWorld.CursorCamera()
            camera.spaceID = avatar.spaceID
            target = Math.Matrix()
            target.setTranslate(Math.Vector3(x, 60.0, z))
            camera.target = target
            source = Math.Matrix()
            source.setRotateYPR((yaw, math.radians(-25.0), 0.0))
            camera.source = source
            BigWorld.camera(camera)
            camera.forceUpdate()

        PlayerAvatar.__init__ = routed_init
        PlayerAvatar.onBecomePlayer = routed_become_player
        setattr(creator, setup_camera_name, bootstrap_setup_camera)
        try:
            creator.create(self._map_name)
        finally:
            if PlayerAvatar.__dict__.get('__init__') is routed_init:
                PlayerAvatar.__init__ = original_init
            if (PlayerAvatar.__dict__.get('onBecomePlayer') is
                    routed_become_player):
                PlayerAvatar.onBecomePlayer = original_become
            if creator.__dict__.get(setup_camera_name) is \
                    bootstrap_setup_camera:
                delattr(creator, setup_camera_name)

        if not creator.Active():
            self._fail('creator_inactive_after_create')
            return False
        avatar = BigWorld.player()
        if avatar is None or avatar is not self._avatar:
            self._fail('player_mismatch_after_create')
            return False
        if not self._became_player:
            self._fail('become_player_missing')
            self._stop_partial_session(avatar)
            return False
        if getattr(avatar, 'arena', None) is None:
            self._fail('arena_missing_after_create')
            return False
        if avatar.guiSessionProvider.getArenaDP() is None:
            self._fail('battle_session_not_started')
            return False
        if avatar.inputHandler is None:
            self._fail('input_handler_missing_after_create')
            self._stop_partial_session(avatar)
            return False
        server_settings_setup.seed(self._log, 'avatar')
        diagnostics.trace_vehicle_enter_world(self._log, avatar)
        self._log('session_started arena_type_id=%s input_handler=%s '
                  'gui_type=%s bonus_type=%s', self._arena_type_id,
                  type(avatar.inputHandler).__name__, avatar.arenaGuiType,
                  avatar.arenaBonusType)
        return True

    def _neutral_aux_physics(self):
        import WoT
        value, overlaps = entity_setup.neutral_aux_physics_data(
            WoT.unpackAuxVehiclePhysicsData)
        self._log('aux_physics_neutral value=%s overlaps=%s decoded=%r',
                  value, overlaps, WoT.unpackAuxVehiclePhysicsData(value))
        return value

    def _on_physics_ready(self):
        """Replay what was held back until native physics existed."""
        import BigWorld
        self._resync_aux_physics()
        avatar = self._avatar
        if avatar is None or BigWorld.player() is not avatar:
            return
        if self._input_deferred:
            self._input_deferred = False
            avatar.moveVehicle(avatar.makeVehicleMovementCommandByKeys(),
                               False)
            self._log('input_resumed')

    def _resync_aux_physics(self):
        """Replay the deferred stabilised-pose sync once physics exists."""
        import BigWorld
        avatar = self._avatar
        if avatar is None or BigWorld.player() is not avatar:
            return
        avatar.set_ownVehicleAuxPhysicsData(avatar.ownVehicleAuxPhysicsData)
        self._log('aux_physics_resynced vehicle_id=%s', self._vehicle_id)

    def _stop_partial_session(self, avatar):
        try:
            if avatar.guiSessionProvider.getArenaDP() is not None:
                avatar.guiSessionProvider.stop()
                self._log('partial_session_stopped')
        except Exception as error:
            self._log('partial_session_stop_failed error=%s',
                      type(error).__name__)

    def _preseed_avatar(self, avatar, constants):
        avatar.arenaUniqueID = 0
        avatar.arenaTypeID = self._arena_type_id
        avatar.arenaBonusType = constants.ARENA_BONUS_TYPE.REGULAR
        avatar.arenaGuiType = constants.ARENA_GUI_TYPE.RANDOM
        avatar.arenaExtraData = {}
        avatar.weatherPresetID = 0
        avatar.bonusCapsOverrides = None
        avatar.name = entity_setup.PLAYER_NAME
        avatar.sessionID = entity_setup.PLAYER_SESSION_ID
        avatar.team = entity_setup.PLAYER_TEAM
        avatar.playerVehicleID = 0
        avatar.isObserverBothTeams = False
        avatar.observableTeamID = 0
        avatar.isGunLocked = False
        avatar.ownVehicleGear = 0
        avatar.ownVehicleAuxPhysicsData = self._neutral_aux_physics()
        avatar.ownVehicleHullAimingPitchPacked = 0
        avatar.denunciationsLeft = 0
        avatar.clientCtx = ''
        avatar.tkillIsSuspected = False
        avatar.customizationDisplayType = 0
        avatar.isObserverFPV = False
        avatar.numOfObservers = 0
        avatar.shouldSendKillcamSimulationData = False
        avatar.goodiesSnapshot = []
        avatar.playLimits = {'curfew': -1, 'weeklyPlayLimit': -1,
                             'dailyPlayLimit': -1, 'sessionLimit': -1}
        avatar.battleChatRestriction = {'isBattleChatDisabled': False,
                                        'restrictionReasonID': 0}

    # ------------------------------------------------------------------
    def _collide_ground(self, space_id, x, z):
        import BigWorld
        import Math
        start = Math.Vector3(x, TERRAIN_RAY_HEIGHT, z)
        end = Math.Vector3(x, -TERRAIN_RAY_HEIGHT, z)
        collision = BigWorld.wg_collideSegment(
            int(space_id), start, end, TERRAIN_COLLISION_MASK,
            TERRAIN_ONLY_FLAGS)
        if collision is None:
            return None
        return float(collision.closestPoint.y)

    def _create_player_vehicle(self):
        import BigWorld
        import Math
        import gun_rotation_shared
        from items import vehicles
        avatar = self._avatar
        x, z, yaw, source = entity_setup.spawn_pose(self._arena_type)
        ground_y = self._collide_ground(avatar.spaceID, x, z)
        if ground_y is None:
            return False
        descriptor = vehicles.VehicleDescr(typeName=VEHICLE_TYPE_NAME)
        comp_descr = descriptor.makeCompactDescr()
        max_health = int(descriptor.maxHealth)
        gun_angles = gun_rotation_shared.encodeGunAngles(
            0.0, 0.0, descriptor.gun.pitchLimits['absolute'])
        properties = entity_setup.vehicle_properties(
            comp_descr, max_health, avatar.id, self._arena_type_id,
            avatar.arenaBonusType, gun_angles_packed=gun_angles)
        position = Math.Vector3(x, ground_y, z)
        vehicle_id = BigWorld.createEntity(
            'Vehicle', avatar.spaceID, 0, position, (0.0, 0.0, yaw),
            properties)
        vehicle = BigWorld.entities.get(vehicle_id)
        if vehicle is None:
            self._fail('vehicle_create_failed')
            return False
        self._vehicle_id = vehicle_id
        self._bridge.set_vehicle_id(vehicle_id)
        self._log('vehicle_created id=%s type=%s spawn=(%.3f,%.3f,%.3f) '
                  'yaw=%.6f source=%s descriptor_ready=%s', vehicle_id,
                  VEHICLE_TYPE_NAME, x, ground_y, z, yaw, source,
                  vehicle.typeDescriptor is not None)
        avatar.arena.updateVehiclesList([
            entity_setup.roster_entry(vehicle_id, comp_descr, max_health)])
        avatar.playerVehicleID = vehicle_id
        avatar.set_playerVehicleID(0)
        arena_load = avatar.guiSessionProvider.shared.arenaLoad
        if arena_load is not None:
            arena_load.invalidateArenaInfo()
        self._push_battle_period(avatar)
        self._log('player_vehicle_selected id=%s init_progress=%s',
                  vehicle_id, self._init_progress(avatar))
        return True

    @staticmethod
    def _init_progress(avatar):
        return getattr(avatar, '_PlayerAvatar__initProgress', None)

    # ------------------------------------------------------------------
    def _poll(self):
        import BigWorld
        if self._failed:
            return
        self._elapsed += POLL_INTERVAL_SECONDS
        if not self._battle_ready and self._elapsed >= BOOTSTRAP_TIMEOUT_SECONDS:
            self._report_timeout()
            return
        avatar = self._avatar
        if avatar is None or BigWorld.player() is not avatar:
            self._fail('player_lost')
            return

        if self._stage == 'await_ground':
            if self._create_player_vehicle():
                self._stage = 'await_init'
        elif self._stage == 'await_init':
            vehicle = BigWorld.entities.get(self._vehicle_id)
            if vehicle is None:
                self._fail('vehicle_lost')
                return
            if (vehicle.appearance is not None and not self._physics_ready
                    and int(self._elapsed * 4) % 8 == 0):
                diagnostics.log_appearance_state(
                    self._log, vehicle, 'awaiting_physics')
            if avatar.initCompleted and vehicle.appearance is not None:
                diagnostics.log_vehicle_state(self._log, vehicle, 'init_done')
                self._stage = 'client_ready'
        elif self._stage == 'client_ready':
            if not avatar.userSeesWorld() and not self._client_ready_requested:
                self._client_ready_requested = True
                self._log('client_ready_requested init_progress=%s',
                          self._init_progress(avatar))
                avatar.setClientReady()
            if avatar.userSeesWorld():
                self._stage = 'await_started'
        elif self._stage == 'await_started':
            vehicle = BigWorld.entities.get(self._vehicle_id)
            if vehicle is not None and vehicle.isStarted:
                self._report_ready(avatar, vehicle)
        elif self._stage == 'battle_ready':
            self._probe_drive(avatar)
        self._schedule(POLL_INTERVAL_SECONDS, self._poll)

    def _push_battle_period(self, avatar):
        """Publish BATTLE before setClientReady.

        Stock setClientReady only calls __setIsOnArena, which enables
        driving and the gun rotator, when the arena already reports
        BATTLE. It also keeps the prebattle dog-tag markers out of the
        vehicle-visual path, where an offline vehicle has no dog tag."""
        import BigWorld
        from constants import ARENA_PERIOD, ARENA_UPDATE
        if self._period_pushed:
            return
        self._period_pushed = True
        end_time = BigWorld.serverTime() + BATTLE_PERIOD_LENGTH_SECONDS
        payload = zlib.compress(cPickle.dumps(
            (ARENA_PERIOD.BATTLE, end_time, BATTLE_PERIOD_LENGTH_SECONDS,
             ()), -1))
        avatar.updateArena(ARENA_UPDATE.PERIOD, payload)
        self._log('arena_period_pushed period=BATTLE length=%s',
                  BATTLE_PERIOD_LENGTH_SECONDS)

    def _report_ready(self, avatar, vehicle):
        import BigWorld
        self._stage = 'battle_ready'
        camera = BigWorld.camera()
        handler = avatar.inputHandler
        ctrl_mode = getattr(handler, '_AvatarInputHandler__ctrlModeName',
                            None)
        filter_name = type(getattr(vehicle, 'filter', None)).__name__
        self._log('battle_ready vehicle_id=%s started=%s filter=%s '
                  'appearance=%s init_progress=%s user_sees_world=%s '
                  'input_handler=%s ctrl_mode=%s camera=%s '
                  'client_ready_received=%s is_on_arena=%s gun_rotator=%s '
                  'arena_period=%s', vehicle.id, vehicle.isStarted,
                  filter_name, type(vehicle.appearance).__name__,
                  self._init_progress(avatar), bool(avatar.userSeesWorld()),
                  type(handler).__name__, ctrl_mode,
                  type(camera).__name__ if camera else None,
                  self._bridge.client_ready_received, avatar.isOnArena,
                  type(avatar.gunRotator).__name__, avatar.arena.period)
        self._battle_ready = True

    def _probe_drive(self, avatar):
        """Report whether input reaches the filter and whether it moves."""
        import BigWorld
        if self._elapsed - self._drive_probe_at < 2.0:
            return
        self._drive_probe_at = self._elapsed
        vehicle = BigWorld.entities.get(self._vehicle_id)
        if vehicle is None:
            return
        position = vehicle.position
        entity_filter = vehicle.filter
        self._log('drive_probe pos=(%.2f,%.2f,%.2f) speed=%.3f '
                  'input_calls=%s last_input=%s on_arena=%s '
                  'moving=%s velocity=%s',
                  position.x, position.y, position.z, vehicle.getSpeed(),
                  self._input_calls, self._last_input, avatar.isOnArena,
                  avatar.isVehicleMoving(),
                  getattr(entity_filter, 'velocity', None))

    def _report_timeout(self):
        import BigWorld
        avatar = self._avatar
        vehicle = BigWorld.entities.get(self._vehicle_id)
        self._log('timeout_state physics_ready=%s input_deferred=%s',
                  self._physics_ready, self._input_deferred)
        self._fail('bootstrap_timeout stage=%s init_progress=%s '
                   'space_load=%.3f vehicle=%s started=%s appearance=%s' % (
                       self._stage,
                       self._init_progress(avatar) if avatar else None,
                       float(BigWorld.spaceLoadStatus()),
                       self._vehicle_id,
                       getattr(vehicle, 'isStarted', None),
                       getattr(vehicle, 'appearance', None) is not None))

    # ------------------------------------------------------------------
    def shutdown(self, reason):
        import BigWorld
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._log('shutdown_begin reason=%s stage=%s', reason, self._stage)
        if self._callback_id is not None:
            try:
                BigWorld.cancelCallback(self._callback_id)
            except Exception:
                pass
            self._callback_id = None
        self._battle_ready = True
        avatar = BigWorld.player()
        if self._vehicle_id and avatar is not None:
            vehicle = BigWorld.entities.get(self._vehicle_id)
            if vehicle is not None:
                try:
                    BigWorld.destroyEntity(self._vehicle_id)
                    self._log('vehicle_destroyed id=%s', self._vehicle_id)
                except Exception as error:
                    detail = repr(error).replace('\n', ' ')[:200]
                    self._log('vehicle_destroy_failed error=%s detail=%s',
                              type(error).__name__, detail)
            self._vehicle_id = 0
        if (self._became_player and avatar is not None and
                avatar is self._avatar):
            try:
                avatar.onBecomeNonPlayer()
                self._log('avatar_retired')
            except Exception as error:
                detail = repr(error).replace('\n', ' ')[:200]
                self._log('avatar_retire_failed error=%s detail=%s',
                          type(error).__name__, detail)
        try:
            from OfflineMapCreator import g_offlineMapCreator as creator
            if creator.Active():
                creator.destroy()
                self._log('creator_destroyed active=%s',
                          bool(creator.Active()))
        except Exception as error:
            detail = repr(error).replace('\n', ' ')[:200]
            self._log('creator_destroy_failed error=%s detail=%s',
                      type(error).__name__, detail)
        self._restore_class_patches()
        self._restore_client_world_patch()
        diagnostics.uninstall(self._log)
        server_settings_setup.uninstall(self._log)
        account_setup.uninstall(self._log)
        self._avatar = None
        self._bridge = None
        self._log('shutdown_complete')
