"""Strict offline stand-in for the Avatar base/cell and Vehicle cell mailboxes.

Every method below is an interface a 2.3.1.2 client call site actually uses.
There is deliberately no catch-all __getattr__: an unexpected mailbox call
raises AttributeError and surfaces in python.log.
"""
from __future__ import absolute_import

import cPickle
import weakref


class AvatarServerBridge(object):

    def __init__(self, avatar, scheduler, account_commands, log):
        self._avatar_ref = weakref.ref(avatar)
        self._schedule = scheduler
        self._commands = account_commands
        self._log = log
        self._int_settings = {}
        self._vehicle_id = 0
        self._client_ready_received = False
        self._client_ctx = ''
        self._last_move_flags = 0
        self._auto_aim_target = 0
        self._shoot_requests = 0
        self._shoot_logged = False
        self._gunnery = None

    def set_gunnery(self, gunnery):
        self._gunnery = gunnery

    @property
    def client_ready_received(self):
        return self._client_ready_received

    @property
    def last_move_flags(self):
        return self._last_move_flags

    def set_vehicle_id(self, vehicle_id):
        self._vehicle_id = int(vehicle_id)

    def _avatar(self):
        return self._avatar_ref()

    def _respond(self, request_id, result_id=None, error=''):
        avatar = self._avatar()
        if avatar is None:
            return
        if result_id is None:
            result_id = self._commands.RES_SUCCESS

        def deliver():
            live = self._avatar()
            if live is not None and getattr(live, 'inWorld', False):
                live.onCmdResponse(request_id, result_id, error)

        self._schedule(0.0, deliver)

    # --- Avatar base methods -------------------------------------------

    def setClientReady(self):
        self._client_ready_received = True
        self._log('bridge_client_ready')

    def setClientCtx(self, ctx):
        self._client_ctx = ctx

    def leaveArena(self):
        self._log('bridge_leave_arena_requested')

    def confirmBattleResultsReceiving(self):
        pass

    def logLag(self):
        pass

    def logStreamCorruption(self, stream_id, original_length, packet_length,
                            original_crc32, crc32):
        pass

    def makeDenunciation(self, violator_id, topic_id, violator_kind):
        pass

    def banUnbanUser(self, account_dbid, restriction_type, ban_period,
                     reason, is_ban):
        pass

    def requestToken(self, request_id, token_type):
        avatar = self._avatar()
        if avatar is not None:
            self._schedule(0.0, lambda: avatar.onTokenReceived(
                request_id, token_type, ''))

    def sendAccountStats(self, request_id, names):
        avatar = self._avatar()
        if avatar is not None:
            payload = cPickle.dumps({}, -1)
            self._schedule(0.0, lambda: avatar.receiveAccountStats(
                request_id, payload))

    def setDevelopmentFeature(self, entity_id, name, value, data):
        self._log('bridge_dev_feature_ignored name=%s' % (name,))

    def vehicle_teleport(self, position, yaw):
        pass

    def vehicle_replenishAmmo(self):
        pass

    # --- Avatar base account commands ----------------------------------

    def doCmdNoArgs(self, request_id, command):
        self._ack_command(request_id, command)

    def doCmdStr(self, request_id, command, string_value):
        self._ack_command(request_id, command)

    def doCmdInt(self, request_id, command, int1):
        self._ack_command(request_id, command)

    def doCmdInt2(self, request_id, command, int1, int2):
        self._ack_command(request_id, command)

    def doCmdInt3(self, request_id, command, int1, int2, int3):
        self._ack_command(request_id, command)

    def doCmdInt4(self, request_id, command, int1, int2, int3, int4):
        self._ack_command(request_id, command)

    def doCmdInt2Str(self, request_id, command, int1, int2, string_value):
        self._ack_command(request_id, command)

    def doCmdInt3Str(self, request_id, command, int1, int2, int3,
                     string_value):
        self._ack_command(request_id, command)

    def doCmdIntStr(self, request_id, command, int1, string_value):
        self._ack_command(request_id, command)

    def doCmdIntStrArr(self, request_id, command, int1, strings):
        self._ack_command(request_id, command)

    def doCmdIntArrStrArr(self, request_id, command, ints, strings):
        self._ack_command(request_id, command)

    def doCmdStrArr(self, request_id, command, strings):
        self._ack_command(request_id, command)

    def doCmdIntArr(self, request_id, command, values):
        if command == self._commands.CMD_ADD_INT_USER_SETTINGS:
            pairs = list(values)
            for index in range(0, len(pairs) - 1, 2):
                self._int_settings[pairs[index]] = pairs[index + 1]
        elif command == self._commands.CMD_DEL_INT_USER_SETTINGS:
            for key in values:
                self._int_settings.pop(key, None)
        self._ack_command(request_id, command)

    def _ack_command(self, request_id, command):
        supported = (
            self._commands.CMD_GET_AVATAR_SYNC,
            self._commands.CMD_ADD_INT_USER_SETTINGS,
            self._commands.CMD_DEL_INT_USER_SETTINGS,
            self._commands.CMD_SET_ACTIVE_VEH_SEASON)
        if command not in supported:
            self._log('bridge_command_unsupported command=%s' % (command,))
        self._respond(request_id)

    # --- Avatar cell methods -------------------------------------------

    def autoAim(self, vehicle_id, magnetic=False):
        self._auto_aim_target = int(vehicle_id)

    def vehicle_moveWith(self, flags):
        self._last_move_flags = int(flags)

    def vehicle_shoot(self):
        self._shoot_requests += 1
        if self._gunnery is not None:
            self._gunnery.request_shot()
        elif not self._shoot_logged:
            self._shoot_logged = True
            self._log('bridge_shoot_requested authority=none')

    def vehicle_trackWorldPointWithGun(self, point):
        pass

    def vehicle_trackRelativePointWithGun(self, point):
        pass

    def vehicle_stopTrackingWithGun(self, turret_yaw, gun_pitch):
        pass

    def vehicle_changeSetting(self, code, value):
        if self._gunnery is not None:
            self._gunnery.change_setting(code, value)

    def setServerMarker(self, enable):
        pass

    def setDualGunCharger(self, start):
        pass

    def setSendKillCamSimulationData(self, enable):
        pass

    def submitPlayerSatisfactionRating(self, rating):
        pass

    def setupAmmo(self, ammo):
        pass

    def monitorVehicleDamagedDevices(self, vehicle_id):
        pass

    def switchObserverFPV(self, enable):
        pass

    def switchViewPointOrBindToVehicle(self, is_viewpoint, entity_id):
        pass

    def bindToVehicle(self, vehicle_id):
        pass

    def activateEquipment(self, equipment_id, index=-1):
        pass

    def setEquipmentApplicationPoint(self, equipment_id, point, direction):
        pass

    def reportClientStats(self, stats):
        pass

    # --- Vehicle cell methods ------------------------------------------

    def sendStateToOwnClient(self):
        pass

    def moveWith(self, flags):
        self._last_move_flags = int(flags)

    def trackWorldPointWithGun(self, point):
        pass

    def trackRelativePointWithGun(self, point):
        pass

    def stopTrackingWithGun(self, turret_yaw, gun_pitch):
        pass

    def changeSetting(self, code, value):
        pass

    def switchSetup(self, group_id, index):
        pass

    def sendVisibilityDevelopmentInfo(self, entity_id, point):
        pass

    def recoveryMechanic_startRecovering(self):
        pass

    def recoveryMechanic_stopRecovering(self):
        pass
