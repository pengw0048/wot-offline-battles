"""Verified #1513-facing BigWorld operations for :mod:`entities.runtime`.

This file is only the #1513 entity/property adapter.  It does not define
movement, collision, combat, spawning or battle rules; those come from the
audited 0.8.2 law modules used by :mod:`battle_runtime`.

The #1513 Vehicle.def, Vehicle.py and ClientArena.py establish the property
names, the 18-item vehicle tuple, and its compression behavior.  BigWorld's
native entity creation result remains a runtime capability: ``self_check``
must pass against the target client before this binding may create an entity.
"""

from __future__ import print_function

import math
try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle
import zlib

try:
    _integer_types = (int, long)
    _unicode_types = (unicode,)
except NameError:
    _integer_types = (int,)
    _unicode_types = ()

# BigWorld ``STRING`` is Python 2 ``str``, not ``unicode``.  LAN messages are
# decoded as UTF-8 before json.loads(), so every server-provided name reaches
# this boundary as unicode on the embedded 2.7 runtime.  Normalize all STRING
# members before handing the property dictionary to createEntity().
_entity_string_types = (str,)

# Exact #1513 ``WoT.unpackAuxVehiclePhysicsData`` layout.  The Avatar.def
# property is UINT64; the six fields occupy every bit in this order.
_AUX_PHYSICS_FIELDS = (
    (15, -math.pi, math.pi),
    (12, -math.pi / 2.0, math.pi / 2.0),
    (13, -math.pi, math.pi),
    (8, -15.0, 30.0),
    (8, -15.0, 30.0),
    (8, 0.0, 1.2),
)


def _entity_string(value):
    if _unicode_types and isinstance(value, _unicode_types):
        return value.encode('utf-8')
    return value


def _encode_aux_physics_value(value, bits, minimum, maximum):
    """Mirror #1513's restricted-value rounding for one packed field."""
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise CapabilityError('auxiliary vehicle physics value is not finite')
    ratio = (value - minimum) / (maximum - minimum)
    ratio = max(0.0, min(1.0, ratio))
    mask = (1 << bits) - 1
    return int(round(mask * ratio)) & mask


def _pack_aux_physics_data(values):
    if len(values) != len(_AUX_PHYSICS_FIELDS):
        raise CapabilityError('auxiliary vehicle physics field count mismatch')
    packed = 0
    shift = 0
    for value, field in zip(values, _AUX_PHYSICS_FIELDS):
        bits, minimum, maximum = field
        packed |= _encode_aux_physics_value(
            value, bits, minimum, maximum) << shift
        shift += bits
    return packed


class CapabilityError(RuntimeError):
    pass


class BigWorldVehicleBinding(object):
    """Concrete binding used by the asynchronous ``BattleRuntime``.

    Dependencies are injected so the capability contract can be tested
    outside BigWorld.  In the game loader pass ``BigWorld``, the player Avatar,
    constants, ``VehicleDescr`` and ``encodeGunAngles`` explicitly.
    """

    PROPERTY_NAMES = (
        'publicInfo', 'gunAnglesPacked', 'health', 'isCrewActive',
        'steeringAngle', 'isStrafing', 'physicsMode', 'siegeState',
        'engineMode', 'damageStickers', 'publicStateModifiers', 'stunInfo')

    def __init__(self, bigworld, avatar, constants, vehicle_descr_class,
                 encode_gun_angles, server_input=None, outfit_provider=None,
                 authority_entity_resolver=None):
        self._bigworld = bigworld
        self._avatar = avatar
        self._constants = constants
        self._vehicle_descr_class = vehicle_descr_class
        self._encode_gun_angles = encode_gun_angles
        self._server_input = server_input
        self._outfit_provider = outfit_provider
        self._authority_entity_resolver = authority_entity_resolver

    def self_check(self):
        self._need(self._bigworld, 'createEntity')
        self._need(self._bigworld, 'destroyEntity')
        self._need(self._bigworld, 'entity')
        self._need(self._bigworld, 'serverTime')
        self._need(self._avatar, 'spaceID')
        self._need(self._avatar, 'updateArena')
        self._need(self._avatar, 'syncVehicleAttrs')
        self._need(self._avatar, 'onVehicleChanged')
        self._need(self._avatar, 'consistentMatrices')
        self._need(self._avatar.consistentMatrices,
                   '_ConsistentMatrices__setTarget')
        self._need(self._constants, 'ARENA_UPDATE')
        self._need(self._constants, 'ARENA_PERIOD')
        self._need(self._constants, 'VEHICLE_PHYSICS_MODE')
        self._need(self._constants, 'VEHICLE_SIEGE_STATE')
        for name in ('VEHICLE_ADDED', 'VEHICLE_KILLED',
                     'VEHICLE_STATISTICS', 'TEAM_KILLER',
                     'AVATAR_READY', 'PERIOD'):
            self._need(self._constants.ARENA_UPDATE, name)
        self._need(self._constants.ARENA_PERIOD, 'PREBATTLE')
        self._need(self._constants.ARENA_PERIOD, 'BATTLE')
        self._need(self._constants.VEHICLE_PHYSICS_MODE, 'STANDARD')
        for name in ('DISABLED', 'SWITCHING_ON', 'ENABLED',
                     'SWITCHING_OFF'):
            self._need(self._constants.VEHICLE_SIEGE_STATE, name)
        self._need(
            self._constants.VEHICLE_MISC_STATUS,
            'SIEGE_MODE_STATE_CHANGED')
        if not callable(self._vehicle_descr_class):
            raise CapabilityError('VehicleDescr factory is unavailable')
        if not callable(self._encode_gun_angles):
            raise CapabilityError('encodeGunAngles is unavailable')
        if not callable(self._outfit_provider):
            raise CapabilityError('verified outfit provider is unavailable')
        if (self._authority_entity_resolver is not None and
                not callable(self._authority_entity_resolver)):
            raise CapabilityError(
                'authority entity resolver is unavailable')
        return True

    def properties_from_compact_descr(self, compact_descr, team, name):
        self.self_check()
        descriptor = self._vehicle_descr_class(compactDescr=compact_descr)
        return self._properties_from_descriptor(descriptor, team, name)

    def _properties_from_descriptor(self, descriptor, team, name):
        self._need(descriptor, 'makeCompactDescr')
        self._need(descriptor, 'maxHealth')
        self._need(descriptor, 'gun')
        self._need(descriptor, 'turret')
        self._need(descriptor.gun, 'pitchLimits')
        self._need(descriptor.turret, 'circularVisionRadius')
        pitch_limits = descriptor.gun.pitchLimits
        if not isinstance(pitch_limits, dict) or 'absolute' not in pitch_limits:
            raise CapabilityError('VehicleDescr.gun.pitchLimits.absolute unavailable')
        return {
            'publicInfo': {
                'compDescr': _entity_string(descriptor.makeCompactDescr()),
                'name': _entity_string(name),
                'team': team,
                'prebattleID': 0,
                'marksOnGun': 0,
                'index': 0,
                'outfit': _entity_string(self._outfit_provider(descriptor))},
            'gunAnglesPacked': self._encode_gun_angles(
                0, 0, pitch_limits['absolute']),
            'health': descriptor.maxHealth,
            'isCrewActive': True,
            'steeringAngle': 0.0,
            'isStrafing': False,
            'physicsMode': self._constants.VEHICLE_PHYSICS_MODE.STANDARD,
            'siegeState': self._constants.VEHICLE_SIEGE_STATE.DISABLED,
            'engineMode': (0, 0),
            'damageStickers': [],
            'publicStateModifiers': (),
            'stunInfo': 0.0}

    def create_vehicle(self, properties, position, rotation):
        self.self_check()
        self._validate_properties(properties)
        return self._bigworld.createEntity(
            'Vehicle', self._avatar.spaceID, 0, position, rotation, properties)

    def arena_vehicle_added(self, entity_id, snapshot):
        properties = self._snapshot_properties(snapshot)
        self._avatar.updateArena(
            self._constants.ARENA_UPDATE.VEHICLE_ADDED,
            self._pack_vehicle_arena_info(entity_id, properties, snapshot))

    def arena_vehicle_removed(self, entity_id):
        # #1513 has no ARENA_UPDATE.VEHICLE_REMOVED and ClientArena has no
        # corresponding update handler.  Entity destruction (or the complete
        # arena teardown) is the only exact removal boundary in this build.
        return None

    def start_vehicle_visual(self, entity_id, is_immediate=True):
        """Register one remote presentation with #1513 battle feedback."""
        # BattleRuntime applies the spotting gate before it starts a marker.
        # Resolve the corresponding presentation from the private registry so
        # a dead vehicle (which the public AOI facade intentionally omits) can
        # still register its wreck marker.
        entity = self._authority_entity_or_fail(entity_id)
        self._need(entity, 'proxy')
        self._need(self._avatar, 'guiSessionProvider')
        provider = self._avatar.guiSessionProvider
        self._need(provider, 'startVehicleVisual')
        provider.startVehicleVisual(entity.proxy, bool(is_immediate))

    def stop_vehicle_visual(self, entity_id, is_player=False):
        """Unregister one remote marker before its visual is destroyed."""
        self._need(self._avatar, 'guiSessionProvider')
        provider = self._avatar.guiSessionProvider
        self._need(provider, 'stopVehicleVisual')
        provider.stopVehicleVisual(int(entity_id), bool(is_player))

    def _vehicle_feedback_context(self, entity_id):
        """Return the exact arguments used by #1513's two visual signals."""
        entity = self._authority_entity_or_fail(entity_id)
        self._need(entity, 'proxy')
        provider = self._need(self._avatar, 'guiSessionProvider')
        get_arena_dp = self._need(provider, 'getArenaDP')
        if not callable(get_arena_dp):
            raise CapabilityError(
                'required #1513 capability is not callable: getArenaDP')
        arena_dp = get_arena_dp()
        get_vehicle_info = self._need(arena_dp, 'getVehicleInfo')
        get_gui_props = self._need(arena_dp, 'getPlayerGuiProps')
        if not callable(get_vehicle_info) or not callable(get_gui_props):
            raise CapabilityError(
                'required #1513 visual arena capabilities are not callable')
        vehicle_info = get_vehicle_info(int(entity_id))
        gui_props = get_gui_props(int(entity_id), vehicle_info.team)
        shared = self._need(provider, 'shared')
        feedback = self._need(shared, 'feedback')
        return entity, vehicle_info, gui_props, feedback

    def start_vehicle_marker(self, entity_id):
        """Add only the 3D marker, leaving the minimap entry unchanged."""
        entity, vehicle_info, gui_props, feedback = \
            self._vehicle_feedback_context(entity_id)
        added = self._need(feedback, 'onVehicleMarkerAdded')
        if not callable(added):
            raise CapabilityError(
                'required #1513 capability is not callable: '
                'onVehicleMarkerAdded')
        added(entity.proxy, vehicle_info, gui_props)
        return True

    def stop_vehicle_marker(self, entity_id):
        """Remove only the 3D marker, retaining team minimap knowledge."""
        provider = self._need(self._avatar, 'guiSessionProvider')
        shared = self._need(provider, 'shared')
        feedback = self._need(shared, 'feedback')
        removed = self._need(feedback, 'onVehicleMarkerRemoved')
        if not callable(removed):
            raise CapabilityError(
                'required #1513 capability is not callable: '
                'onVehicleMarkerRemoved')
        removed(int(entity_id))
        return True

    def start_vehicle_minimap(self, entity_id):
        """Add only the minimap entry for a team-known remote vehicle."""
        entity, vehicle_info, gui_props, feedback = \
            self._vehicle_feedback_context(entity_id)
        visible = self._need(
            feedback, '_BattleFeedbackAdaptor__visible')
        self._need(visible, 'add')
        visible.add(int(entity_id))
        added = self._need(feedback, 'onMinimapVehicleAdded')
        if not callable(added):
            raise CapabilityError(
                'required #1513 capability is not callable: '
                'onMinimapVehicleAdded')
        added(entity.proxy, vehicle_info, gui_props)
        return True

    def stop_vehicle_minimap(self, entity_id):
        """Remove only the minimap entry for a forgotten remote vehicle."""
        provider = self._need(self._avatar, 'guiSessionProvider')
        shared = self._need(provider, 'shared')
        feedback = self._need(shared, 'feedback')
        visible = self._need(
            feedback, '_BattleFeedbackAdaptor__visible')
        self._need(visible, 'discard')
        visible.discard(int(entity_id))
        removed = self._need(feedback, 'onMinimapVehicleRemoved')
        if not callable(removed):
            raise CapabilityError(
                'required #1513 capability is not callable: '
                'onMinimapVehicleRemoved')
        removed(int(entity_id))
        return True

    def refresh_vehicle_minimap(self, entity_id):
        """Rebind one stock minimap entry to its current Vehicle matrix.

        Native ``Vehicle.startVisual`` runs before the LAN pose overlay can be
        attached.  Its first minimap entry therefore captures the inert spawn
        matrix.  Replaying only the minimap-added signal after attach makes
        #1513 rebuild that matrix provider without removing the 2D marker or
        changing the feedback adaptor's visible-vehicle set.
        """
        return self.start_vehicle_minimap(entity_id)

    def arena_vehicle_killed(self, entity_id, attacker_id=0, reason=0):
        """Publish the exact uncompressed #1513 ClientArena kill tuple."""
        payload = (int(entity_id), int(attacker_id), 0, int(reason))
        self._avatar.updateArena(self._constants.ARENA_UPDATE.VEHICLE_KILLED,
                                 _pickle.dumps(payload))

    def arena_vehicle_statistics(self, entity_id, frags):
        """Publish exact #1513 compressed ``(vehicleID, frags)`` stats."""
        payload = (int(entity_id), int(frags))
        self._avatar.updateArena(
            self._constants.ARENA_UPDATE.VEHICLE_STATISTICS,
            zlib.compress(_pickle.dumps(payload)))

    def arena_team_killer(self, entity_id):
        """Publish exact #1513 uncompressed team-killer vehicle id."""
        self._avatar.updateArena(
            self._constants.ARENA_UPDATE.TEAM_KILLER,
            _pickle.dumps(int(entity_id)))

    def avatar_select_vehicle(self, entity_id):
        """Select the local id before stock PlayerAvatar enter handling."""
        self._set_avatar_property('playerVehicleID', entity_id)

    def avatar_vehicle_entered(self):
        """Publish the local Vehicle and bind #1513's attachment state.

        A retail server changes the Avatar's engine attachment after
        ``AvatarPositionControl.bindToVehicle``.  The client-only mailbox has
        no server Entity relationship to mutate, so ``avatar.vehicle`` stays
        empty even though ``playerVehicleID`` and the native Vehicle are
        valid.  Stock minimap entries follow
        ``consistentMatrices.attachedVehicleMatrix`` rather than
        ``updateOwnVehiclePosition``.  Likewise, the stock postmortem view
        controller only updates its current-vehicle cursor from
        ``PlayerAvatar.onVehicleChanged`` when ``avatar.vehicle`` is present.
        Reproduce both skipped Python-side results against the selected
        client-only Vehicle.
        """
        self._avatar.onVehicleChanged()
        entity = self._entity_or_fail(self._avatar.playerVehicleID)
        self._need(entity, 'matrix')
        setter = self._avatar.consistentMatrices.\
            _ConsistentMatrices__setTarget
        provider = self._need(self._avatar, 'guiSessionProvider')
        shared = self._need(provider, 'shared')
        view_points = self._need(shared, 'viewPoints')
        update_attached = self._need(view_points, 'updateAttachedVehicle')
        if not callable(update_attached):
            raise CapabilityError(
                'required #1513 capability is not callable: '
                'updateAttachedVehicle')
        setter(entity.matrix, False)
        update_attached(self._avatar.playerVehicleID)

    def avatar_client_ready(self):
        self._set_avatar_property('isGunLocked', False)
        self._set_avatar_property('ownVehicleAuxPhysicsData', 0)
        self._set_avatar_property('ownVehicleGear', 0)
        entity = self._entity_or_fail(self._avatar.playerVehicleID)
        self._need(entity, 'typeDescriptor')
        self._need(entity.typeDescriptor, 'turret')
        self._need(entity.typeDescriptor.turret, 'circularVisionRadius')
        self._avatar.syncVehicleAttrs({'circularVisionRadius':
            entity.typeDescriptor.turret.circularVisionRadius})

    def avatar_aux_physics(self, yaw, pitch, roll, left_scroll,
                           right_scroll, normalised_rpm, gear):
        """Publish the local engine inputs through exact #1513 properties.

        ``ownVehicleGear`` is UINT8 and ``ownVehicleAuxPhysicsData`` is the
        six-field UINT64 consumed by both ``DetailedEngineState`` and
        ``PlayerAvatar.__onSetOwnVehicleAuxPhysicsData``.  Set the gear first
        so the auxiliary-data notifier observes the matching engine state.
        """
        self._require_int('Avatar ownVehicleGear', gear, 0, 255)
        packed = _pack_aux_physics_data((
            yaw, pitch, roll, left_scroll, right_scroll, normalised_rpm))
        self._set_avatar_property('ownVehicleGear', gear)
        self._set_avatar_property('ownVehicleAuxPhysicsData', packed)
        return packed

    def avatar_ready(self):
        self._avatar.updateArena(self._constants.ARENA_UPDATE.AVATAR_READY,
                                 _pickle.dumps(self._avatar.playerVehicleID))

    def arena_period(self, period, duration=0.0):
        """Publish one native #1513 arena-period tuple.

        The stock HUD derives both its prebattle countdown and battle clock
        from ``periodEndTime``.  Publishing BATTLE immediately made the local
        Avatar playable as soon as it entered the world and skipped the same
        PREBATTLE barrier that the 0.8.2 runtime preserves.
        """
        if period == 'prebattle':
            value = self._constants.ARENA_PERIOD.PREBATTLE
        elif period == 'battle':
            value = self._constants.ARENA_PERIOD.BATTLE
        else:
            raise CapabilityError('unsupported arena period: %s' % period)
        duration = max(0.0, float(duration))
        end_time = (float(self._bigworld.serverTime()) + duration
                    if duration > 0.0 else 0.0)
        payload = (value, end_time, duration, [])
        self._avatar.updateArena(self._constants.ARENA_UPDATE.PERIOD,
                                 zlib.compress(_pickle.dumps(payload)))

    def drive_vehicle(self, entity_id, movement_dir, rotation_dir):
        """Replay one input state through #1513's native vehicle physics.

        The pinned executable's ``PyWGVehicleFilter`` method table exposes
        ``notifyInputKeysDown`` but neither ``set`` nor ``setPosition``.  The
        stock ``PlayerAvatar.moveVehicle`` method passes exactly these two
        signed directions to the filter.  Reusing that boundary keeps client-
        created remote vehicles on their native suspension, collision and
        terrain simulation instead of guessing a non-existent pose setter.
        """
        entity = self._entity_or_fail(entity_id)
        self._need(entity, 'filter')
        vehicle_filter = entity.filter
        self._need(vehicle_filter, 'notifyInputKeysDown')
        movement_dir = self._signed_direction(movement_dir)
        rotation_dir = self._signed_direction(rotation_dir)
        vehicle_filter.notifyInputKeysDown(movement_dir, rotation_dir)

    def set_vehicle_pose(self, entity_id, position, rotation,
                         relax_time=None, now=None):
        """Apply one pose to the copied 0.8.2 presentation boundary.

        ``relax_time`` is how long this pose should take to reach on screen.
        The vehicle eases its own drawn matrix over that interval, so a pose
        slower than the render rate never steps.
        """
        entity = self._authority_entity_or_fail(entity_id)
        setter = getattr(entity, 'set_pose', None)
        if not (bool(getattr(entity, '_offlineLANPresentation', False) or
                     getattr(entity, '_offlineNativeRemote', False)) and
                callable(setter)):
            raise CapabilityError(
                'remote vehicle has no authoritative presentation')
        setter(position, rotation, relax_time, now)

    def settle_vehicle_motion(self, entity_id, now=None):
        """Clear one stationary remote's pose-derived motion in place."""
        entity = self._authority_entity_or_fail(entity_id)
        settle = getattr(entity, 'settle_motion', None)
        if not (bool(getattr(entity, '_offlineLANPresentation', False) or
                     getattr(entity, '_offlineNativeRemote', False)) and
                callable(settle)):
            raise CapabilityError(
                'remote vehicle has no authoritative motion settlement')
        return bool(settle(now))

    def update_vehicle_aim(self, entity_id, hull_yaw, aim_yaw, gun_pitch):
        """Apply a network world aim to the exact packed Vehicle property."""
        entity = self._authority_entity_or_fail(entity_id)
        presentation_setter = getattr(entity, 'set_aim', None)
        if (bool(getattr(entity, '_offlineLANPresentation', False) or
                 getattr(entity, '_offlineNativeRemote', False)) and
                callable(presentation_setter)):
            presentation_setter(hull_yaw, aim_yaw, gun_pitch)
            return
        self._need(entity, 'gunAnglesPacked')
        self._need(entity, 'typeDescriptor')
        self._need(entity.typeDescriptor, 'gun')
        self._need(entity.typeDescriptor.gun, 'pitchLimits')
        pitch_limits = entity.typeDescriptor.gun.pitchLimits
        if not isinstance(pitch_limits, dict) or 'absolute' not in pitch_limits:
            raise CapabilityError('Vehicle gun pitch limits are unavailable')
        relative_yaw = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                        (2.0 * math.pi) - math.pi)
        packed = self._encode_gun_angles(
            relative_yaw, float(gun_pitch), pitch_limits['absolute'])
        self._require_int('gunAnglesPacked', packed, 0, 65535)
        previous = entity.gunAnglesPacked
        entity.gunAnglesPacked = packed
        notifier = getattr(entity, 'set_gunAnglesPacked', None)
        if callable(notifier):
            notifier(previous)

    def update_vehicle_siege_state(self, entity_id, state,
                                   time_to_next_mode):
        """Drive the exact ``Vehicle.onSiegeStateUpdated`` consumer.

        The retail server owns the four-state transition.  Client-created
        entities have no real cell property stream, so the LAN snapshot must
        assign ``siegeState`` and reproduce its consumer edge.  The player
        takes the exact ``Avatar.updateVehicleMiscStatus`` route (HUD, cruise
        reset, movement re-sample, then ``Vehicle.onSiegeStateUpdated``);
        remote vehicles take the property callback directly.
        """
        states = self._constants.VEHICLE_SIEGE_STATE
        allowed = (states.DISABLED, states.SWITCHING_ON,
                   states.ENABLED, states.SWITCHING_OFF)
        self._require_int('siegeState', state, 0, 255)
        if state not in allowed:
            raise CapabilityError('Vehicle siegeState is unsupported')
        self._require_number('siege transition time', time_to_next_mode)
        time_to_next_mode = float(time_to_next_mode)
        switching = state in (states.SWITCHING_ON, states.SWITCHING_OFF)
        if ((switching and time_to_next_mode <= 0.0) or
                (not switching and time_to_next_mode != 0.0)):
            raise CapabilityError('Vehicle siege transition time is invalid')
        entity = self._authority_entity_or_fail(entity_id)
        descriptor = self._need(entity, 'typeDescriptor')
        has_siege_mode = bool(getattr(descriptor, 'hasSiegeMode', False))
        if not has_siege_mode:
            if state != states.DISABLED:
                raise CapabilityError(
                    'Vehicle without Siege mode received an active state')
            return False
        self._need(entity, 'siegeState')
        previous = entity.siegeState
        entity.siegeState = state
        try:
            if entity_id == self._avatar.playerVehicleID:
                updater = self._need(
                    self._avatar, 'updateVehicleMiscStatus')
                updater(
                    entity_id,
                    self._constants.VEHICLE_MISC_STATUS.
                    SIEGE_MODE_STATE_CHANGED,
                    state, (time_to_next_mode,))
            else:
                callback = self._need(entity, 'onSiegeStateUpdated')
                callback(state, time_to_next_mode)
        except Exception:
            entity.siegeState = previous
            raise
        return previous != state

    def send_vehicle_input(self, entity_id, command):
        if self._server_input is None:
            raise CapabilityError('server input bridge is unavailable')
        self._server_input(entity_id, dict(command))

    def destroy_entity(self, entity_id):
        self._bigworld.destroyEntity(entity_id)

    def is_vehicle_ready(self, entity_id):
        """Return only after BigWorld has materialized the Vehicle in-world.

        ``createEntity`` returns the client-only id before Vehicle resource
        prerequisites finish.  During ``Vehicle.onEnterWorld`` the id can
        already be bound to the Avatar while ``BigWorld.entity(id)`` is still
        unavailable.  Native consumers use the same entity + inWorld gate.
        """
        try:
            entity = self._bigworld.entity(entity_id)
            if bool(getattr(entity, '_offlineLANPresentation', False)):
                return (bool(getattr(entity, 'inWorld', False)) and
                        bool(getattr(entity, 'isStarted', False)) and
                        getattr(entity, 'model', None) is not None)
            return (entity is not None and
                    bool(getattr(entity, 'inWorld', False)) and
                    bool(getattr(entity, 'isStarted', False)) and
                    getattr(entity, 'typeDescriptor', None) is not None)
        except ReferenceError:
            return False

    def _pack_vehicle_arena_info(self, entity_id, properties, snapshot=None):
        """Exact #1513 ClientArena 18-item vehicle-list shape."""
        public_info = properties['publicInfo']
        is_alive = (int(properties.get('health', 0)) > 0 and
                    bool(properties.get('isCrewActive', True)))
        # ClientArena indexes vehicles by accountDBID in addition to vehicle
        # id.  Giving every client-only Vehicle account 1 made each new bot
        # overwrite the previous player entry, which in turn made both side
        # panels look like one orange roster.  Engine entity ids are positive
        # and unique for the complete round, so they are the correct local
        # account identity namespace.  Every Vehicle is fully materialized
        # before this producer runs; publish it ready like the 0.8.2 roster.
        team_killer = bool((snapshot or {}).get('team_killer', False))
        values = [entity_id, public_info['compDescr'], public_info['name'],
                  public_info['team'], is_alive, True, team_killer,
                  entity_id, '', 0,
                  public_info['prebattleID'], False, False, {}, 0, [], 0, {}]
        return zlib.compress(_pickle.dumps(values))

    def _snapshot_properties(self, snapshot):
        if not isinstance(snapshot, dict) or 'properties' not in snapshot:
            raise CapabilityError('Vehicle snapshot properties are required')
        self._validate_properties(snapshot['properties'])
        return snapshot['properties']

    def _validate_properties(self, properties):
        if not isinstance(properties, dict):
            raise CapabilityError('Vehicle properties must be a dict')
        names = set(properties)
        expected = set(self.PROPERTY_NAMES)
        if names != expected:
            raise CapabilityError('Vehicle property contract mismatch')
        public_info = properties['publicInfo']
        if not isinstance(public_info, dict):
            raise CapabilityError('Vehicle publicInfo must be a dict')
        required = set(('compDescr', 'name', 'team', 'prebattleID', 'marksOnGun',
                        'index', 'outfit'))
        if set(public_info) != required:
            raise CapabilityError('Vehicle publicInfo contract mismatch')
        for name in ('compDescr', 'name', 'outfit'):
            if not isinstance(public_info[name], _entity_string_types):
                raise CapabilityError('Vehicle publicInfo.%s must be STRING' %
                                      name)
        for name in ('team', 'marksOnGun', 'index'):
            self._require_int('publicInfo.' + name, public_info[name], 0, 255)
        self._require_int('publicInfo.prebattleID',
                          public_info['prebattleID'], 0, 2147483647)
        self._require_int('gunAnglesPacked', properties['gunAnglesPacked'],
                          0, 65535)
        self._require_int('health', properties['health'], -32768, 32767)
        for name in ('isCrewActive', 'isStrafing'):
            if not isinstance(properties[name], bool):
                raise CapabilityError('Vehicle %s must be BOOL' % name)
        self._require_number('steeringAngle', properties['steeringAngle'])
        self._require_int('physicsMode', properties['physicsMode'], 0, 255)
        self._require_int('siegeState', properties['siegeState'], 0, 255)
        engine_mode = properties['engineMode']
        if not isinstance(engine_mode, tuple) or len(engine_mode) != 2:
            raise CapabilityError('Vehicle engineMode must be a 2-item TUPLE')
        for index, value in enumerate(engine_mode):
            self._require_int('engineMode[%d]' % index, value, 0, 255)
        for name in ('damageStickers', 'publicStateModifiers'):
            if not isinstance(properties[name], (list, tuple)):
                raise CapabilityError('Vehicle %s must be an ARRAY' % name)
        self._require_number('stunInfo', properties['stunInfo'])

    def _require_int(self, name, value, minimum, maximum):
        if (isinstance(value, bool) or
                not isinstance(value, _integer_types) or
                value < minimum or value > maximum):
            raise CapabilityError('Vehicle %s is outside integer schema' % name)

    def _require_number(self, name, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise CapabilityError('Vehicle %s must be numeric' % name)
        if math.isnan(value) or math.isinf(value):
            raise CapabilityError('Vehicle %s must be finite' % name)

    def _set_avatar_property(self, name, value):
        self._need(self._avatar, name)
        previous = getattr(self._avatar, name)
        setattr(self._avatar, name, value)
        notifier = getattr(self._avatar, 'set_' + name, None)
        if notifier is not None:
            notifier(previous)

    def _entity_or_fail(self, entity_id):
        entity = self._bigworld.entity(entity_id)
        if entity is None:
            raise CapabilityError('Vehicle entity %s is unavailable' % entity_id)
        return entity

    def _authority_entity_or_fail(self, entity_id):
        """Resolve simulation state without applying the stock AOI gate.

        Synthetic remote Vehicles remain authoritative while unspotted or
        dead.  The public ``BigWorld.entity`` facade intentionally hides those
        objects from stock aiming, collision and marker consumers, so internal
        pose, aim and explicitly visibility-gated presentation operations must
        use the private registry resolver.
        """
        resolver = self._authority_entity_resolver
        entity = (resolver(entity_id) if resolver is not None else
                  self._bigworld.entity(entity_id))
        if entity is None:
            raise CapabilityError(
                'Authority vehicle entity %s is unavailable' % entity_id)
        return entity

    @staticmethod
    def _signed_direction(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0.01:
            return 1
        if value < -0.01:
            return -1
        return 0

    def _need(self, value, name):
        if not hasattr(value, name):
            raise CapabilityError('required #1513 capability missing: %s' % name)
        return getattr(value, name)
