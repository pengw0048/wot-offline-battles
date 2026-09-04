"""Strict local server surface for the #1513 Avatar and Vehicle entities.

The exact client calls these mailboxes while entering a battle:

* ``Avatar.base.setClientReady`` after all four native init steps;
* ``Avatar.cell.autoAim`` and ``switchObserverFPV``;
* Avatar sync and integer-setting commands; and
* ``Vehicle.cell.sendStateToOwnClient`` on the player's Vehicle.

The public 0.9.22 observer implementation at tuxedo commit
``c0bc550c46deac980194b7b860ee8781d53ec97b`` confirms the same lifecycle.
This implementation is intentionally explicit.  Unknown mailboxes still raise
``AttributeError`` instead of turning client errors into silent success.

Postmortem spectator switching is delegated to the battle runtime.  Exact
#1513 requires the cell server to reattach the Avatar before it invokes
``PlayerAvatar.onSwitchViewpoint``; the bridge therefore never emits that
client callback without a runtime-owned, validated attachment transaction.
"""

import math

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle


class AvatarBridgeError(RuntimeError):
    pass


class DeferredAvatarServer(object):
    """Exist before ``PlayerAvatar.onBecomePlayer`` and attach before spawn.

    The stock Avatar asks for VOIP state and Avatar sync synchronously while
    becoming the player.  Entity binding cannot exist until the Avatar itself
    exists, so only those exact early requests are queued/accepted here.
    """

    def __init__(self):
        self._target = None
        self._pending = []

    @property
    def voipController(self):
        return self

    def attach(self, target):
        if self._target is not None and self._target is not target:
            raise AvatarBridgeError('Avatar server is already attached')
        self._target = target
        pending = self._pending
        self._pending = []
        for name, args in pending:
            getattr(target, name)(*args)

    def invalidateMicrophoneMute(self):
        if self._target is not None:
            return self._target.invalidateMicrophoneMute()
        return None

    def isReady(self):
        return False

    def isVOIPEnabled(self):
        return False

    def isVivox(self):
        return False

    def isYY(self):
        return False

    def isPlayerSpeaking(self, account_dbid):
        return False

    def switchObserverFPV(self, enabled):
        if self._target is not None:
            return self._target.switchObserverFPV(enabled)
        return None

    def setClientReady(self):
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
        target = self._target
        if target is None:
            raise AttributeError(
                'Avatar server is not attached for mailbox %s' % name)
        return getattr(target, name)


class AvatarServerBridge(object):
    """Bridge native Avatar/Vehicle mailbox calls to entities and LAN input."""

    def __init__(self, avatar, entity_binding, property_builder, lan_sender,
                 account_commands=None, on_account_int_command=None,
                 on_ready=None, on_leave=None,
                 on_vehicle_enter=None, on_viewpoint_switch=None,
                 on_monitor_vehicle_devices=None,
                 initial_period='battle', initial_period_seconds=0.0):
        self._avatar = avatar
        self._binding = entity_binding
        self._builder = property_builder
        self._lan_sender = lan_sender
        self._account_commands = tuple(account_commands or ())
        self._on_account_int_command = on_account_int_command
        self._on_ready = on_ready
        self._on_leave = on_leave
        self._on_vehicle_enter = on_vehicle_enter
        self._on_viewpoint_switch = on_viewpoint_switch
        self._on_monitor_vehicle_devices = on_monitor_vehicle_devices
        self._initial_period = initial_period
        self._initial_period_seconds = max(
            0.0, float(initial_period_seconds))
        self._vehicle_id = None
        self._bound_vehicle_id = None
        self._arena_vehicle_added = False
        self._vehicle_enter_started = False
        self._vehicle_enter_completed = False
        self._vehicle_enter_error = None
        self._vehicle_enter_states = {}
        self._ready_requested = False
        self._client_ready = False
        self._ready_publish_started = False
        self._ready_publish_error = None
        self._client_context = ''
        self._destroyed = False
        self._leave_requested = False

    def prepareVehicleEnter(self, vehicle):
        """Install client-server pose state before stock local entry runs."""
        if self._destroyed:
            return False
        vehicle_id = int(vehicle.id)
        if self._vehicle_id is not None and vehicle_id != self._vehicle_id:
            return False
        if not callable(self._on_vehicle_enter):
            return True
        self._on_vehicle_enter(vehicle)
        return True

    @property
    def vehicle_id(self):
        return self._vehicle_id

    @property
    def voipController(self):
        return self

    def addVehicleToArena(self, snapshot):
        if self._destroyed:
            raise AvatarBridgeError('Avatar server is destroyed')
        if self._vehicle_id is not None:
            raise AvatarBridgeError('Vehicle already exists')
        properties = self._builder.build(snapshot)
        created_id = self._binding.create_vehicle(
            properties, self._required(snapshot, 'position'),
            self._required(snapshot, 'rotation'))
        if created_id is None:
            raise AvatarBridgeError('createEntity returned no Vehicle id')
        # Record the id returned by BigWorld.  Exact #1513 normally enters the
        # entity asynchronously, after createEntity has returned.
        if self._vehicle_id is None:
            self._vehicle_id = created_id
        elif self._vehicle_id != created_id:
            self._binding.destroy_entity(created_id)
            raise AvatarBridgeError('BigWorld entered a different Vehicle')
        selection_attempted = False
        try:
            # A synchronous/re-entrant Vehicle.onEnterWorld cannot be made
            # safe here.  playerVehicleID was necessarily still zero while
            # the stock handler ran, so it treated the local tank as remote
            # and skipped the own-vehicle matrix initialization.  Publishing
            # the id afterwards would only complete a partially initialized
            # Avatar and can crash the native client.  Tear it down instead.
            if self._vehicle_enter_started:
                raise AvatarBridgeError(
                    'Vehicle entered before createEntity returned')
            # Publish the roster before playerVehicleID.  The exact Avatar
            # setter may finish the complete battle GUI/visual lifecycle when
            # the entity is already in-world, and those consumers immediately
            # resolve the selected id through ClientArena.
            self._binding.arena_vehicle_added(self._vehicle_id, snapshot)
            self._arena_vehicle_added = True
            # On the normal #1513 path createEntity returns while Vehicle
            # prerequisites are still loading.  This setter therefore records
            # SET_PLAYER_ID only; native vehicle_onEnterWorld later initializes
            # the own-vehicle matrices and records VEHICLE_ENTERED.
            selection_attempted = True
            self._bind_avatar_once(self._vehicle_id)
        except Exception:
            was_bound = self._bound_vehicle_id is not None
            try:
                self._binding.destroy_entity(self._vehicle_id)
            finally:
                self._vehicle_id = None
                self._bound_vehicle_id = None
                self._arena_vehicle_added = False
                self._vehicle_enter_started = False
                self._vehicle_enter_completed = False
                self._vehicle_enter_error = None
                self._vehicle_enter_states = {}
                self._ready_requested = False
                self._client_ready = False
                self._ready_publish_started = False
                self._ready_publish_error = None
                if was_bound or selection_attempted:
                    try:
                        self._binding.avatar_select_vehicle(0)
                    except Exception:
                        pass
            raise
        return self._vehicle_id

    def acceptVehicleEnter(self, vehicle_id):
        """Retain the first local Vehicle without pre-empting stock enter."""
        if self._destroyed:
            return False
        vehicle_id = int(vehicle_id)
        current = self._vehicle_enter_states.get(vehicle_id)
        if current is None or current[0] == 'started':
            self._vehicle_enter_states[vehicle_id] = ('started', None)
        if self._vehicle_id is None:
            self._vehicle_id = vehicle_id
        elif self._vehicle_id != vehicle_id:
            return False
        self._vehicle_enter_started = True
        # Do not repeat the playerVehicleID notifier from inside
        # Vehicle.onEnterWorld.  In exact #1513 that notifier can mark
        # VEHICLE_ENTERED and synchronously start Vehicle visuals before native
        # PlayerAvatar.vehicle_onEnterWorld has initialized the own-vehicle
        # matrices.  The initial post-create bind is sufficient on the normal
        # asynchronous path.  addVehicleToArena rejects a callback that occurs
        # re-entrantly before createEntity returns because stock #1513 has
        # already skipped the local-only matrix initialization at that point.
        return True

    def completeVehicleEnter(self, vehicle_id):
        """Commit local entry only after stock vehicle_onEnterWorld returns."""
        if self._destroyed:
            return False
        vehicle_id = int(vehicle_id)
        current = self._vehicle_enter_states.get(vehicle_id)
        if current is None or current[0] == 'failed':
            return False
        self._vehicle_enter_states[vehicle_id] = ('completed', None)
        if vehicle_id != self._vehicle_id or not self._vehicle_enter_started:
            return True
        if self._vehicle_enter_error is not None:
            return False
        self._vehicle_enter_completed = True
        return True

    def failVehicleEnter(self, vehicle_id, error):
        """Latch a local native-enter failure for the deferred ready poll."""
        if self._destroyed:
            return False
        vehicle_id = int(vehicle_id)
        message = str(error)
        self._vehicle_enter_states[vehicle_id] = ('failed', message)
        if vehicle_id != self._vehicle_id:
            return True
        self._vehicle_enter_error = message
        return True

    def vehicleEnterStatus(self, vehicle_id):
        """Expose the exact native-enter phase to pending remote records."""
        return self._vehicle_enter_states.get(int(vehicle_id),
                                              ('pending', None))

    def forgetVehicleEnter(self, vehicle_id):
        self._vehicle_enter_states.pop(int(vehicle_id), None)

    def bindToVehicle(self, vehicle_id):
        if self._destroyed:
            return False
        vehicle_id = int(vehicle_id)
        if self._vehicle_id is None:
            self._vehicle_id = vehicle_id
        if vehicle_id != self._vehicle_id:
            raise AvatarBridgeError('cannot bind unknown Vehicle')
        self._bind_avatar_once(vehicle_id)
        return True

    def moveTo(self, position):
        """Accept the free-look Avatar move that the SPG cameras request.

        ``AvatarPositionControl.moveTo`` forwards straight to this mailbox,
        and ``StrategicCamera.enable``/``ArtyCamera.enable`` call it between
        ``BigWorld.camera(...)`` and their ``delayCallback`` registration.  A
        missing mailbox therefore leaves those cameras with no update tick:
        the aim point stops following the mouse and the arty camera keeps its
        default matrix at the world origin.  The offline Avatar entity owns no
        server-side position, so the request only has to be admitted.
        """
        if self._destroyed:
            return False
        for index in range(3):
            value = float(position[index])
            if math.isnan(value) or math.isinf(value):
                raise AvatarBridgeError('Avatar move position is invalid')
        return True

    def switchViewPointOrBindToVehicle(self, is_viewpoint,
                                       vehicle_or_point_id):
        """Delegate one complete server-style postmortem attachment.

        ``AvatarPositionControl.switchViewpoint`` discards this mailbox's
        return value.  Keep all target validation, matrix rebinding and the
        final client callback in one battle-runtime transaction so a rejected
        request cannot update only the HUD or only the camera.
        """
        if self._destroyed or not callable(self._on_viewpoint_switch):
            return False
        return bool(self._on_viewpoint_switch(
            bool(is_viewpoint), int(vehicle_or_point_id)))

    def _bind_avatar_once(self, vehicle_id):
        if self._bound_vehicle_id == vehicle_id:
            return False
        if self._bound_vehicle_id is not None:
            raise AvatarBridgeError('Avatar is already bound to another Vehicle')
        self._binding.avatar_select_vehicle(vehicle_id)
        self._bound_vehicle_id = vehicle_id
        return True

    def setClientReady(self):
        # Vehicle.onEnterWorld calls this mailbox before its engine callback
        # has returned.  Record the request only: BigWorld.entity(id) may not
        # become visible until the next engine tick.
        if self._destroyed or self._client_ready or self._ready_requested:
            return False
        self._ready_requested = True
        return True

    def flushClientReady(self):
        """Publish native readiness from a later BigWorld callback.

        BattleRuntime polls this boundary outside Vehicle.onEnterWorld.  It is
        deliberately separate from the server mailbox so a synchronous native
        callback cannot observe a half-materialized Vehicle.
        """
        return self._flush_client_ready()

    def _flush_client_ready(self):
        if self._destroyed:
            return False
        if self._ready_publish_error is not None:
            raise AvatarBridgeError(
                'player Vehicle ready failed: %s' %
                self._ready_publish_error)
        if self._vehicle_enter_error is not None:
            raise AvatarBridgeError(
                'player Vehicle enter failed: %s' % self._vehicle_enter_error)
        if (not self._ready_requested or self._client_ready or
                self._vehicle_id is None or not self._arena_vehicle_added or
                self._bound_vehicle_id != self._vehicle_id or
                not self._vehicle_enter_completed):
            return False
        if not self._binding.is_vehicle_ready(self._vehicle_id):
            return False
        if self._ready_publish_started:
            return False
        self._ready_publish_started = True
        try:
            self._binding.avatar_vehicle_entered()
            self._binding.avatar_client_ready()
            self._binding.avatar_ready()
            # Exact #1513 handles a PERIOD update synchronously.  Its
            # PlayerAvatar.__setIsOnArena path immediately calls moveVehicle,
            # so the mailbox must accept input before entering that callback.
            # A LAN battle deliberately leaves ``initial_period`` unset: the
            # ClientArena remains in WAITING while the remaining vehicles are
            # materialized, and the server publishes the sole PREBATTLE
            # countdown only after every client reports ready.
            self._client_ready = True
            if self._initial_period is not None:
                self._binding.arena_period(
                    self._initial_period, self._initial_period_seconds)
        except Exception as error:
            self._client_ready = False
            self._ready_publish_error = str(error)
            raise
        self._ready_requested = False
        if callable(self._on_ready):
            self._on_ready()
        return True

    def sendStateToOwnClient(self):
        """Vehicle properties already came from the local createEntity call."""
        if self._vehicle_id is None:
            raise AvatarBridgeError('Vehicle state requested before binding')
        return None

    def syncVehicleAttrs(self, attrs):
        if not isinstance(attrs, dict):
            raise AvatarBridgeError('attrs must be a dict')
        self._avatar.syncVehicleAttrs(dict(attrs))

    def vehicle_moveWith(self, flags):
        flags = int(flags)
        # PlayerAvatar.moveVehicle has already notified the native filter
        # before invoking this cell mailbox.  The local bridge owns only the
        # LAN relay; notifying the filter again duplicates stock input and
        # bypasses PlayerAvatar's movement guards.
        self._send_input('move', {'flags': flags})

    def setCruiseControlMode(self, mode):
        self._send_input('cruise', {'mode': int(mode)})

    def vehicle_changeSetting(self, code, value):
        handler = getattr(
            self._lan_sender, 'change_vehicle_setting', None)
        if (callable(handler) and
                handler(self._vehicle_id, code, value)):
            return
        updater = getattr(self._avatar, 'updateVehicleSetting', None)
        if updater is None:
            raise AttributeError('Avatar.updateVehicleSetting')
        updater(self._vehicle_id, code, value)

    def vehicle_trackWorldPointWithGun(self, point):
        self._send_input('track_world', {'point': point})

    def trackRelativePointWithGun(self, point):
        """Handle the exact #1513 Vehicle.cell gun-tracking mailbox."""
        self._send_input('track_relative', {'point': point})

    def vehicle_trackRelativePointWithGun(self, point):
        self._send_input('track_relative', {'point': point})

    def vehicle_stopTrackingWithGun(self, turret_yaw, gun_pitch):
        self._send_input('stop_tracking', {
            'turret_yaw': float(turret_yaw),
            'gun_pitch': float(gun_pitch)})

    def vehicle_shoot(self):
        accepted = self._send_input('shoot', {})
        if accepted is False:
            rejected = getattr(
                self._lan_sender, 'reject_native_shot_wait', None)
            if callable(rejected):
                # Exact #1513 starts PlayerAvatar's acknowledgement wait only
                # after this mailbox returns.  Defer cancellation through the
                # sender so a locally rejected trigger cannot time out into a
                # predicted muzzle flash and sound.
                rejected()
        return accepted

    def setDevelopmentFeature(self, name, value, data):
        if name == 'pickup':
            self._send_input('development', {
                'name': name, 'args': (value, data)})
            return
        if name == 'server_marker':
            return None
        raise AttributeError('unsupported development feature: %s' % name)

    def setVehicleDevelopmentFeature(self, vehicle_id, name, value, data):
        # Release #1513 does not expose development controls.  Keep the exact
        # mailbox shape explicit so an accidental dev-resource path is safe.
        return None

    def controlAnotherVehicle(self, vehicle_id, stage):
        return None

    def vehicle_teleport(self, position, yaw):
        return None

    def vehicle_replenishAmmo(self):
        # This slice presents a stable ammo count and has no consumable stock.
        return None

    def confirmBattleResultsReceiving(self):
        return None

    def makeDenunciation(self, violator_id, topic_id, violator_kind):
        return None

    def banUnbanUser(self, account_dbid, restriction_type, ban_period,
                     reason, is_ban):
        return None

    def requestToken(self, request_id, token_type):
        callback = getattr(self._avatar, 'onTokenReceived', None)
        if callable(callback):
            callback(request_id, token_type, '')

    def sendAccountStats(self, request_id, names):
        callback = getattr(self._avatar, 'receiveAccountStats', None)
        if callable(callback):
            values = dict((name, 0) for name in names)
            callback(request_id, _pickle.dumps(values))

    def logStreamCorruption(self, stream_id, original_length, packet_length,
                            original_crc32, crc32):
        return None

    def autoAim(self, vehicle_id):
        # Target selection is already applied by the local Avatar.
        return None

    def switchObserverFPV(self, enabled):
        return None

    def switchObserverFPVControlMode(self, control_mode):
        # RemoteCameraSender emits this for ordinary players whenever the
        # stock control mode changes, even when no observer is connected.
        return None

    def setRemoteCamera(self, data):
        return None

    def activateEquipment(self, equipment_id):
        # No equipment is provisioned by the current standard-battle slice.
        return None

    def monitorVehicleDamagedDevices(self, vehicle_id):
        if self._destroyed or not callable(
                self._on_monitor_vehicle_devices):
            return False
        return bool(self._on_monitor_vehicle_devices(int(vehicle_id)))

    def invalidateMicrophoneMute(self):
        return None

    def isReady(self):
        return False

    def isVOIPEnabled(self):
        return False

    def isVivox(self):
        return False

    def isYY(self):
        return False

    def isPlayerSpeaking(self, account_dbid):
        return False

    def setMicrophoneMute(self, muted):
        return None

    def setClientCtx(self, value):
        self._client_context = value

    def leaveArena(self, statistics):
        if self._destroyed or self._leave_requested:
            return False
        self._leave_requested = True
        try:
            if callable(self._on_leave):
                self._on_leave()
        except Exception:
            self._leave_requested = False
            raise
        return True

    def doCmdStr(self, request_id, command, string):
        self._ack_command(request_id, command)

    def doCmdIntArr(self, request_id, command, values):
        if command not in self._account_commands:
            raise AttributeError('unsupported account command: %s' % command)
        result_id = 0
        error = ''
        if callable(self._on_account_int_command):
            result_id, error = self._on_account_int_command(command, values)
        self._ack_command(request_id, command, result_id, error)

    def destroy(self):
        # ``_destroyed`` fences late native callbacks immediately, while the
        # vehicle id remains the retry token until both arena and entity
        # teardown have completed.
        if self._destroyed and self._vehicle_id is None:
            return False
        self._destroyed = True
        if self._vehicle_id is None:
            self._vehicle_enter_states = {}
            return False
        vehicle_id = self._vehicle_id
        if self._arena_vehicle_added:
            self._binding.arena_vehicle_removed(vehicle_id)
            self._arena_vehicle_added = False
        self._binding.destroy_entity(vehicle_id)
        self._vehicle_id = None
        self._bound_vehicle_id = None
        self._vehicle_enter_started = False
        self._vehicle_enter_completed = False
        self._vehicle_enter_error = None
        self._vehicle_enter_states = {}
        self._ready_requested = False
        self._client_ready = False
        self._ready_publish_started = False
        self._ready_publish_error = None
        return True

    def _ack_command(self, request_id, command, result_id=0, error=''):
        if command not in self._account_commands:
            raise AttributeError('unsupported account command: %s' % command)
        callback = getattr(self._avatar, 'onCmdResponse', None)
        if callback is None:
            raise AttributeError('Avatar.onCmdResponse')
        callback(request_id, result_id, error)

    def _send_input(self, kind, payload):
        if self._vehicle_id is None or not self._client_ready:
            raise AvatarBridgeError('Vehicle is not ready')
        sender = getattr(self._lan_sender, 'send_avatar_input', None)
        if sender is None:
            raise AttributeError('LAN sender.send_avatar_input')
        return sender(self._vehicle_id, kind, payload)

    def _required(self, values, name):
        if not isinstance(values, dict) or name not in values:
            raise AvatarBridgeError('missing %s' % name)
        return values[name]
