"""Exact-#1513 stock presentation for remote ``Vehicle`` entities.

The retail entity owns its CompoundAppearance, WGVehicleFilter and
PyTrackScroll.  The LAN server still owns gameplay poses: #1513 exposes no
legal transform setter for a client-created remote Vehicle, so the copied
physics pose is published through the same narrow compatibility overlay used
by the local tank. The compound-only implementation remains an explicit
fallback for diagnostics and clients that cannot provide the pinned ABI.
"""

from __future__ import print_function

import math

from gui.mods.offline_lan_0922.entities.remote_vehicle import (
    _RemoteShotPresenter, _blend_angle, _component_aim_angles,
    close_stock_presentation_extras, set_model_attachment_visibility)


_MINIMUM_KEYFRAME_SECONDS = 0.001
_SIEGE_ENABLED = 2
_SIEGE_SWITCHING_OFF = 3


def set_draw_visibility(entity, visible):
    """Use the stock compound gate so a hidden tank cannot cast a shadow."""
    show = getattr(entity, 'show', None)
    appearance = getattr(entity, 'appearance', None)
    change_visibility = getattr(appearance, 'changeVisibility', None)
    if not callable(show) or not callable(change_visibility):
        raise RuntimeError(
            '#1513 native vehicle visibility gate is unavailable')
    visible = bool(visible)
    # Vehicle.show controls the model draw pass while CompoundAppearance owns
    # the compound, stickers and crashed-track visibility.  Keep both native
    # layers symmetric: the initial enemy gate may already have selected the
    # shadow-only pass, which changeVisibility(True) cannot restore by itself.
    show(visible)
    change_visibility(visible)
    # Neither of those two stock layers touches the model's attachment draw
    # flag, so every node-bound effect (fire, smoke, exhaust, ground dust)
    # keeps drawing over a hidden tank.  Gate the attachments too.
    set_model_attachment_visibility(
        getattr(appearance, 'compoundModel', None), visible)
    # That flag is one unproven native property.  Close the two stock owners
    # it would have covered directly as well: the ground occlusion decals and
    # the camera-distance dust/exhaust selectors.
    close_stock_presentation_extras(appearance, visible)
    return True


class _AimTarget(object):

    def __init__(self, math_module):
        self.turretMatrix = math_module.Matrix()
        self.turretMatrix.setIdentity()
        self.gunMatrix = math_module.Matrix()
        self.gunMatrix.setIdentity()


class _NativeRemoteState(object):

    def __init__(self, bigworld, math_module, compatibility, data_links,
                 position, rotation, interpolate_motion=True,
                 authority_geometry=False):
        self._bigworld = bigworld
        self._math = math_module
        self._compatibility = compatibility
        self._data_links = data_links
        self._authority_geometry = bool(authority_geometry)
        self.position = math_module.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self.speed = 0.0
        self.turn_speed = 0.0
        # Keep stable callable objects alive for the native #1513 data-link
        # setters.  Re-reading a bound method would create a temporary object.
        self._vehicle_speed_link = self._read_vehicle_speed
        self._vehicle_rotation_speed_link = \
            self._read_vehicle_rotation_speed
        self.velocity = math_module.Vector3(0.0, 0.0, 0.0)
        self.acceleration = math_module.Vector3(0.0, 0.0, 0.0)
        self._last_pose_time = None
        self.matrix = math_module.Matrix()
        self._write_matrix(self.matrix)
        self._identity_placing_compensation = math_module.Matrix()
        self._identity_placing_compensation.setIdentity()
        self._key_from = math_module.Matrix(self.matrix)
        self._key_to = math_module.Matrix(self.matrix)
        self._render_pose = (
            float(self.position.x), float(self.position.y),
            float(self.position.z), self.yaw, self.pitch, self.roll)
        self._render_from = None
        self._render_to = None
        self._render_started = 0.0
        self._render_duration = 0.0
        animation_factory = getattr(math_module, 'MatrixAnimation', None)
        self.animation = (animation_factory()
                          if (interpolate_motion and
                              callable(animation_factory)) else None)
        self.provider = self.animation or self.matrix
        if self.animation is not None:
            self._rekey(_MINIMUM_KEYFRAME_SECONDS)
        self.aim = _AimTarget(math_module)
        self._aim_relative_yaw = None
        self._aim_gun_pitch = None
        self._aim_desired_gun_pitch = None
        self._siege_relative_body_matrix = None
        self._siege_body_matrix = None
        self.entity = None
        self.model_changed = None
        self.track_scroll = None
        self.track_mode = None
        self._track_feed = None
        self._track_feed_hidden = False
        self.presentation_capabilities = {}
        self.presentation_errors = {}

    def _write_matrix(self, matrix):
        matrix.setRotateYPR((self.yaw, self.pitch, self.roll))
        matrix.translation = self.position

    def _matrix_product(self, first, second=None):
        product_type = getattr(self._math, 'MatrixProduct', None)
        if not callable(product_type):
            raise RuntimeError('#1513 Math.MatrixProduct is unavailable')
        product = product_type()
        product.a = first
        if second is not None:
            product.b = second
        return product

    def _prepare_siege_pose(self):
        """Relate native hydraulic body pitch to the canonical LAN ground."""
        entity = self.entity
        descriptor = getattr(entity, 'typeDescriptor', None)
        if not bool(getattr(descriptor, 'hasSiegeMode', False)):
            return False
        if self._siege_relative_body_matrix is not None:
            return True
        inverse_type = getattr(self._math, 'MatrixInverse', None)
        if not callable(inverse_type):
            raise RuntimeError('#1513 Math.MatrixInverse is unavailable')
        vehicle_filter = getattr(entity, 'filter', None)
        native_body = getattr(vehicle_filter, 'bodyMatrix', None)
        native_ground = getattr(vehicle_filter, 'groundPlacingMatrix', None)
        if native_body is None or native_ground is None:
            raise RuntimeError(
                '#1513 hydraulic vehicle matrices are unavailable')
        # Exact #1513 Vehicle.getComponents() uses body * inverse(ground) for
        # the hull/turret/gun while the chassis keeps groundPlacingMatrix.
        self._siege_relative_body_matrix = self._matrix_product(
            native_body, inverse_type(native_ground))
        self._siege_body_matrix = self._matrix_product(
            self._siege_relative_body_matrix, self.matrix)
        return True

    @staticmethod
    def _absolute_gun_pitch_limits(descriptor):
        gun = getattr(descriptor, 'gun', None)
        limits = getattr(gun, 'pitchLimits', None)
        if isinstance(limits, dict):
            limits = limits.get('absolute')
        else:
            limits = getattr(limits, 'absolute', None)
        try:
            minimum = float(limits[0])
            maximum = float(limits[1])
        except (AttributeError, IndexError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 hydraulic gun pitch limits are unavailable')
        if minimum > maximum:
            raise RuntimeError('#1513 hydraulic gun pitch limits are invalid')
        return minimum, maximum

    def _write_aim_pitch(self, gun_pitch):
        gun_pitch = float(gun_pitch)
        if gun_pitch == self._aim_gun_pitch:
            return False
        self.aim.gunMatrix.setRotateYPR((0.0, gun_pitch, 0.0))
        self._aim_gun_pitch = gun_pitch
        return True

    def update_siege_pose(self):
        """Feed #1513 hydraulics and preserve its body/ground split."""
        if not self._authority_geometry:
            return False
        entity = self.entity
        descriptor = getattr(entity, 'typeDescriptor', None)
        if not bool(getattr(descriptor, 'hasSiegeMode', False)):
            return False
        self._prepare_siege_pose()
        active = getattr(entity, 'siegeState', 0) in (
            _SIEGE_ENABLED, _SIEGE_SWITCHING_OFF)
        desired_pitch = self._aim_desired_gun_pitch
        visible_pitch = desired_pitch
        pitch_delta = 0.0
        if active and desired_pitch is not None:
            local_desired = float(desired_pitch) - float(self.pitch)
            minimum, maximum = self._absolute_gun_pitch_limits(descriptor)
            visible_pitch = max(minimum, min(maximum, local_desired))
            pitch_delta = ((local_desired - visible_pitch + math.pi) %
                           (2.0 * math.pi) - math.pi)
        if visible_pitch is not None:
            self._write_aim_pitch(visible_pitch)
        vehicle_filter = getattr(entity, 'filter', None)
        get_physics = getattr(vehicle_filter, 'getVehiclePhysics', None)
        if not callable(get_physics):
            raise RuntimeError(
                '#1513 Siege vehicle physics boundary is unavailable')
        physics = get_physics()
        if physics is None:
            return False
        set_delta = getattr(physics, 'setHullAimingAnglesDelta', None)
        if not callable(set_delta):
            raise RuntimeError(
                '#1513 hydraulic aiming input boundary is unavailable')
        # The exact x86 wrapper takes yaw first and pitch second.
        set_delta(0.0, pitch_delta)
        return True

    def collision_matrices(self, ground_matrix=None):
        """Return authority body and chassis matrices at one ground pose."""
        canonical_ground = self.matrix if ground_matrix is None else \
            ground_matrix
        if not self._authority_geometry:
            return canonical_ground, canonical_ground
        entity = self.entity
        descriptor = getattr(entity, 'typeDescriptor', None)
        active = bool(
            getattr(descriptor, 'hasSiegeMode', False) and
            getattr(entity, 'siegeState', 0) in (
                _SIEGE_ENABLED, _SIEGE_SWITCHING_OFF))
        if not active:
            return canonical_ground, canonical_ground
        self._prepare_siege_pose()
        body = (self._siege_body_matrix if ground_matrix is None else
                self._matrix_product(
                    self._siege_relative_body_matrix, canonical_ground))
        return body, canonical_ground

    def _rekey(self, relax_time):
        if self.animation is None:
            return False
        try:
            self.animation.keyframes = (
                (0.0, self._key_from),
                (max(float(relax_time), _MINIMUM_KEYFRAME_SECONDS),
                 self._key_to))
            self.animation.time = 0.0
        except Exception as error:
            self.animation = None
            self.provider = self.matrix
            self._rebind_provider()
            return False
        return True

    def _rebind_provider(self):
        """Move every stock motion consumer to the current pose provider."""
        entity = getattr(self, 'entity', None)
        if entity is None or getattr(entity, 'model', None) is None:
            return False
        entity.model.matrix = self.provider
        self._track_feed = None
        self._bind_stock_motion()
        self._publish_pose()
        return True

    def set_interpolate_motion(self, enabled):
        """Follow live bot-authority handoffs without retaining two filters."""
        enabled = bool(enabled)
        if enabled == (self.animation is not None):
            return False
        previous = self.provider
        pose = (
            float(self.position.x), float(self.position.y),
            float(self.position.z), self.yaw, self.pitch, self.roll)
        self._render_pose = pose
        self._render_from = None
        self._render_to = None
        self._render_duration = 0.0
        self._write_pose(self._key_from, pose)
        self._write_pose(self._key_to, pose)
        if enabled:
            animation_factory = getattr(
                self._math, 'MatrixAnimation', None)
            if callable(animation_factory):
                self.animation = animation_factory()
                self.provider = self.animation
                self._rekey(_MINIMUM_KEYFRAME_SECONDS)
        else:
            self.animation = None
            self.provider = self.matrix
        if self.provider is not previous:
            self._rebind_provider()
            return True
        return False

    @staticmethod
    def _write_pose(matrix, pose):
        matrix.setRotateYPR((pose[3], pose[4], pose[5]))
        matrix.translation = (pose[0], pose[1], pose[2])

    def _mirror_pose(self, now):
        target = self._render_to
        if target is None:
            return self._render_pose
        source = self._render_from
        if source is None or now is None or self._render_duration <= 0.0:
            return target
        ratio = (float(now) - self._render_started) / self._render_duration
        if ratio >= 1.0:
            return target
        ratio = max(0.0, ratio)
        return (
            source[0] + (target[0] - source[0]) * ratio,
            source[1] + (target[1] - source[1]) * ratio,
            source[2] + (target[2] - source[2]) * ratio,
            _blend_angle(source[3], target[3], ratio),
            _blend_angle(source[4], target[4], ratio),
            _blend_angle(source[5], target[5], ratio))

    def _retarget(self, relax_time, now):
        target = (
            float(self.position.x), float(self.position.y),
            float(self.position.z), self.yaw, self.pitch, self.roll)
        relax_time = float(relax_time or 0.0)
        if self.animation is None:
            self._render_pose = target
            self._write_pose(self._key_to, target)
            return False
        current = self._mirror_pose(now)
        if relax_time <= 0.0 or current is None:
            self._render_from = None
            self._render_to = None
            self._render_pose = target
            self._write_pose(self._key_from, target)
            self._write_pose(self._key_to, target)
            return self._rekey(0.0)
        if current == target:
            return False
        self._write_pose(self._key_from, current)
        self._write_pose(self._key_to, target)
        self._render_from = current
        self._render_to = target
        self._render_pose = current
        self._render_started = float(now or 0.0)
        self._render_duration = relax_time
        return self._rekey(relax_time)

    def _engine_owns_entity(self):
        entity = self.entity
        if entity is None:
            return False
        entities = getattr(self._bigworld, 'entities', None)
        lookup = getattr(entities, 'get', None)
        if not callable(lookup):
            return False
        try:
            return (lookup(int(entity.id)) is entity and
                    bool(getattr(entity, 'inWorld', False)) and
                    bool(getattr(entity, 'isStarted', False)))
        except (AttributeError, KeyError, TypeError, ValueError,
                ReferenceError):
            return False

    def _record_capability(self, name, available, error=None):
        self.presentation_capabilities[name] = bool(available)
        if error is None:
            self.presentation_errors.pop(name, None)
        elif name not in self.presentation_errors:
            self.presentation_errors[name] = str(error)
        entity = self.entity
        if entity is not None and self._engine_owns_entity():
            entity._offlinePresentationCapabilities = \
                self.presentation_capabilities
            entity._offlinePresentationErrors = self.presentation_errors
        return bool(available)

    def _read_vehicle_speed(self):
        return float(self.speed)

    def _read_vehicle_rotation_speed(self):
        return float(self.turn_speed)

    def _bind_stock_motion(self):
        """Rebind stock #1513 presentation components to copied LAN motion."""
        entity = self.entity
        appearance = getattr(entity, 'appearance', None)
        if appearance is None:
            return False

        detailed = getattr(appearance, 'detailedEngineState', None)
        audition = getattr(appearance, 'engineAudition', None)
        engine_ready = False
        if detailed is not None and audition is not None:
            try:
                # #1513 accepts a callable here.  DataLinks.createFloatLink
                # only supports native data-link owners; passing this plain
                # Python state creates an empty std::function and crashes the
                # next DetailedEngineState update with bad_function_call.
                detailed.vehicleSpeedLink = self._vehicle_speed_link
                detailed.rotationSpeedLink = \
                    self._vehicle_rotation_speed_link
                engine_ready = True
            except Exception as error:
                self._record_capability(
                    'engine_audio_motion', False, error)
        if engine_ready:
            self._record_capability('engine_audio_motion', True)
        elif 'engine_audio_motion' not in self.presentation_errors:
            self._record_capability(
                'engine_audio_motion', False,
                'stock DetailedEngineState is unavailable')

        swinging = getattr(appearance, 'swingingAnimator', None)
        if swinging is not None:
            try:
                # CompoundAppearance initially binds this to the entity's
                # native filter matrices.  Our model is driven by a copied
                # LAN MatrixAnimation whose root already contains the
                # authoritative ground pitch and roll.  Keep acceleration
                # rocking on that provider, but replace the stale native
                # placing compensation so ground placement is applied once.
                swinging.placingCompensationMatrix = \
                    self._identity_placing_compensation
                swinging.worldMatrix = self.provider
                self._record_capability('body_swinging', True)
            except Exception as error:
                self._record_capability('body_swinging', False, error)
        else:
            self._record_capability(
                'body_swinging', False,
                'stock SwingingAnimator is unavailable')

        # CompoundAppearance.activate binds the stock LodCalculator after it
        # links the compound to the entity filter.  We replace that compound
        # matrix with the copied LAN provider after stock activation, so the
        # old position link would otherwise remain at the filter's spawn pose.
        # WheelsAnimator, suspension and other stock components share this LOD
        # state; belts can keep scrolling through PyTrackScroll while the
        # wheels themselves stop updating once the camera leaves that stale
        # position.  Repeat #1513's exact public binding for our provider.
        lod = getattr(appearance, 'lodCalculator', None)
        link_translation = getattr(
            self._data_links, 'linkMatrixTranslation', None)
        setup_position = getattr(lod, 'setupPosition', None)
        lod_ready = False
        if callable(link_translation) and callable(setup_position):
            try:
                setup_position(link_translation(self.provider))
                lod_ready = True
            except Exception as error:
                self._record_capability(
                    'stock_motion_lod', False, error)
        if lod_ready:
            self._record_capability('stock_motion_lod', True)
        elif 'stock_motion_lod' not in self.presentation_errors:
            self._record_capability(
                'stock_motion_lod', False,
                'stock LodCalculator/DataLinks are unavailable')

        wheels = getattr(appearance, 'wheelsAnimator', None)
        suspension = getattr(appearance, 'suspension', None)
        if suspension is None:
            suspension = getattr(appearance, 'leveredSuspension', None)
        self._record_capability(
            'stock_wheels', wheels is not None,
            None if wheels is not None else
            'stock WheelsAnimator is unavailable')
        self._record_capability(
            'stock_suspension', suspension is not None,
            None if suspension is not None else
            'stock suspension is unavailable')
        return bool(engine_ready or swinging is not None or
                    wheels is not None or suspension is not None)

    def _publish_pose(self):
        entity = self.entity
        if entity is None:
            return False
        self._compatibility.set_vehicle_pose_overlay(
            entity, self.position, self.yaw, self.provider,
            self.speed, self.turn_speed, self.velocity, self.acceleration)
        return True

    def _update_motion(self, previous_position, previous_yaw, now):
        velocity = self._math.Vector3(0.0, 0.0, 0.0)
        acceleration = self._math.Vector3(0.0, 0.0, 0.0)
        speed = 0.0
        turn_speed = 0.0
        if now is not None:
            now = float(now)
            previous_time = self._last_pose_time
            if previous_time is not None and now > previous_time:
                elapsed = now - previous_time
                velocity = (self.position - previous_position).scale(
                    1.0 / elapsed)
                acceleration = (velocity - self.velocity).scale(
                    1.0 / elapsed)
                # BigWorld's yaw zero points down the model's +Z axis.
                speed = (float(velocity.x) * math.sin(self.yaw) +
                         float(velocity.z) * math.cos(self.yaw))
                turn_speed = _blend_angle(
                    previous_yaw, self.yaw, 1.0) - previous_yaw
                turn_speed /= elapsed
            self._last_pose_time = now
        self.velocity = velocity
        self.acceleration = acceleration
        self.speed = float(speed)
        self.turn_speed = float(turn_speed)

    def settle_motion(self, now=None):
        """Clear pose-derived motion after the rendered pose stops changing."""
        if now is not None:
            self._last_pose_time = float(now)
        self.velocity = self._math.Vector3(0.0, 0.0, 0.0)
        self.acceleration = self._math.Vector3(0.0, 0.0, 0.0)
        self.speed = 0.0
        self.turn_speed = 0.0
        if self.entity is not None:
            self._publish_pose()
        return True

    def attach(self, entity):
        self.entity = entity
        entity._offlineNativeRemote = True
        entity._offlineNativeMarkerVisible = bool(getattr(
            entity, '_offlineNativeMarkerVisible', True))
        entity._offlineNativeDrawVisible = bool(getattr(
            entity, '_offlineNativeDrawVisible', True))
        entity._offlineNativeMotionState = self
        entity._aim_yaw = self.yaw
        entity._gun_pitch = 0.0
        entity.team = int(entity.publicInfo['team'])
        entity.bw_entity_id = int(entity.id)
        entity.set_pose = self.set_pose
        entity.set_aim = self.set_aim
        entity.settle_motion = self.settle_motion
        entity.update_tracks = self.update_tracks
        entity.track_scroll_readback = self.track_scroll_readback
        entity.model.matrix = self.provider
        self._publish_pose()
        appearance = entity.appearance
        self.track_scroll = getattr(
            appearance, '_CompoundAppearance__trackScrollCtl', None)
        entity.track_scroll = self.track_scroll
        setup = getattr(appearance, 'setupGunMatrixTargets', None)
        if not callable(setup):
            raise RuntimeError(
                '#1513 CompoundAppearance aim-target boundary is unavailable')
        setup(self.aim)
        self._bind_stock_motion()
        if (self._authority_geometry and
                bool(getattr(entity.typeDescriptor, 'hasSiegeMode', False))):
            self._prepare_siege_pose()
            self.update_siege_pose()

        def on_model_changed(*unused_args, **unused_kwargs):
            if self.entity is not None and self.entity.appearance is not None:
                self._rebind_provider()
                self.entity.appearance.setupGunMatrixTargets(self.aim)
                # Stock may replace the scroll controller together with the
                # chassis fashion.  Re-read it at that lifecycle boundary and
                # make the next 20 Hz feed replay both engine mode and speed.
                self.track_scroll = getattr(
                    self.entity.appearance,
                    '_CompoundAppearance__trackScrollCtl', None)
                self.entity.track_scroll = self.track_scroll
                self.track_mode = None
                self._track_feed = None
                # CompoundAppearance may replace the model after damage or a
                # streaming refresh.  Stock startVisual makes that replacement
                # visible again, so restore the runtime-owned spotting gates
                # after every relink without touching marker registration.
                set_draw_visibility(self.entity, bool(getattr(
                    self.entity, '_offlineNativeDrawVisible', True)))
                is_alive = getattr(self.entity, 'isAlive', None)
                alive = (bool(is_alive()) if callable(is_alive)
                         else bool(is_alive))
                self.entity.targetCaps = ([1] if alive and bool(getattr(
                    self.entity, '_spot_visible', True)) else [])

        changed = getattr(appearance, 'onModelChanged', None)
        if changed is not None:
            changed += on_model_changed
            self.model_changed = on_model_changed
        return entity

    def set_pose(self, position, rotation, relax_time=None, now=None):
        previous_position = self.position
        previous_yaw = self.yaw
        self.position = self._math.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self._write_matrix(self.matrix)
        self._retarget(relax_time, now)
        self._update_motion(previous_position, previous_yaw, now)
        entity = self.entity
        if entity is not None:
            entity._aim_yaw = getattr(entity, '_aim_yaw', self.yaw)
            if (self._authority_geometry and bool(getattr(
                    entity.typeDescriptor, 'hasSiegeMode', False))):
                self.update_siege_pose()
            self._publish_pose()
        return True

    def set_aim(self, hull_yaw, aim_yaw, gun_pitch):
        relative = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                    (2.0 * math.pi) - math.pi)
        raw_gun_pitch = float(gun_pitch)
        descriptor = getattr(self.entity, 'typeDescriptor', None)
        component_yaw, component_pitch = _component_aim_angles(
            descriptor, relative, raw_gun_pitch)
        if component_yaw != self._aim_relative_yaw:
            self.aim.turretMatrix.setRotateYPR((
                component_yaw, 0.0, 0.0))
            self._aim_relative_yaw = component_yaw
        self._aim_desired_gun_pitch = component_pitch
        entity = self.entity
        if (self._authority_geometry and bool(getattr(
                getattr(entity, 'typeDescriptor', None),
                'hasSiegeMode', False))):
            self.update_siege_pose()
        else:
            self._write_aim_pitch(component_pitch)
        if entity is not None:
            entity._aim_yaw = float(aim_yaw)
            entity._gun_pitch = raw_gun_pitch
        return True

    def update_tracks(self, left, right, mode):
        entity = self.entity
        if entity is None:
            return False
        if not self._engine_owns_entity():
            # Once BigWorld releases the PyEntity, even reading a component
            # can dereference native memory.  Record the fallback state only
            # in Python and leave every engine object untouched.
            self.presentation_capabilities[
                'engine_owned_track_motion'] = False
            self.presentation_errors.setdefault(
                'engine_owned_track_motion',
                'BigWorld no longer owns the Vehicle entity')
            return False
        appearance = getattr(entity, 'appearance', None)
        if appearance is None:
            return False
        left = float(left)
        right = float(right)
        # Retail owns no presentation writer for a vehicle the client cannot
        # see: an unspotted or out-of-AOI entity is simply absent.  A LAN
        # remote never leaves AOI, so this runtime is that writer and must
        # stop driving belt speed and engine mode while the tank is hidden.
        # Settle the belts once on the hide edge, then feed nothing.
        hidden = not bool(getattr(entity, '_offlineNativeDrawVisible', True))
        if hidden:
            if self._track_feed_hidden:
                return False
            self._track_feed_hidden = True
            left = 0.0
            right = 0.0
            self._track_feed = None
        elif self._track_feed_hidden:
            # The reveal must replay both the engine mode and the belt speed,
            # so drop the dedupe caches that would otherwise swallow it.
            self._track_feed_hidden = False
            self._track_feed = None
            self.track_mode = None
        flying = getattr(appearance, 'flyingInfoProvider', None)
        left_contact = not bool(getattr(
            flying, 'isLeftSideFlying', False))
        right_contact = not bool(getattr(
            flying, 'isRightSideFlying', False))
        feed = (round(left, 3), round(right, 3), tuple(mode),
                left_contact, right_contact)
        if feed == self._track_feed:
            return bool(self.presentation_capabilities.get(
                'engine_owned_track_motion'))
        native_updated = False
        vehicle_filter = getattr(entity, 'filter', None)
        appearance_filter = getattr(appearance, 'filter', None)
        setter = getattr(vehicle_filter, 'setTracksSpeed', None)
        movement_info = getattr(vehicle_filter, 'movementInfo', None)
        if (appearance_filter is vehicle_filter and callable(setter) and
                movement_info is not None):
            try:
                # This native call is fatal on an unattached WGVehicleFilter.
                # The identity/ownership checks above are therefore part of
                # the ABI boundary, not just a best-effort optimisation. The
                # #1513 wrapper parses float/bool/float/bool in this order.
                setter(left, left_contact, right, right_contact)
                native_updated = True
                self._record_capability('engine_owned_track_motion', True)
            except Exception as error:
                self._record_capability(
                    'engine_owned_track_motion', False, error)
        else:
            self._record_capability(
                'engine_owned_track_motion', False,
                'Appearance/filter ownership or movementInfo is unavailable')
        if not hidden and mode != self.track_mode:
            appearance.changeEngineMode(mode, True)
            self.track_mode = mode
        # PyTrackScroll remains the safe belt/audio fallback when the exact
        # native wheel input is absent.  It never calls WGVehicleFilter APIs.
        appearance.updateTracksScroll(left, right)
        # Merely finding the controller does not prove stock activated and
        # bound it.  Commit the cache only after the engine-owned filter has
        # accepted the #1513 native write; otherwise the same feed must be
        # allowed to retry instead of becoming a permanent false success.
        if native_updated:
            self._track_feed = feed
        return bool(native_updated)

    def track_scroll_readback(self):
        if self.track_scroll is None:
            return None
        result = []
        for name in ('leftScroll', 'rightScroll', 'leftContact',
                     'rightContact'):
            reader = getattr(self.track_scroll, name, None)
            result.append(reader() if callable(reader) else reader)
        return tuple(result)

    def detach(self):
        entity = self.entity
        engine_owned = self._engine_owns_entity()
        if entity is None:
            return False
        if engine_owned:
            appearance = getattr(entity, 'appearance', None)
            changed = getattr(appearance, 'onModelChanged', None)
            if changed is not None and self.model_changed is not None:
                try:
                    changed -= self.model_changed
                except Exception:
                    pass
        self._compatibility.clear_vehicle_pose_overlay(entity)
        # The overlay clear is the last native-facing operation.  Preserve the
        # entity and callback owners if it raises so factory teardown can retry
        # rather than forgetting a still-linked presentation.
        self.model_changed = None
        self.entity = None
        return True


class NativeRemoteVehicleFactory(object):
    """Create only real #1513 Vehicle entities; never synthetic compounds."""

    native_entities = True

    def __init__(self, bigworld, math_module, model_assembler, space_id,
                 binding, compatibility, data_links=None,
                 interpolate_motion=True, authority_geometry=False,
                 **unused_kwargs):
        self._space_id = int(space_id)
        self._bigworld = bigworld
        self._math = math_module
        self._binding = binding
        self._compatibility = compatibility
        self._data_links = data_links
        self._interpolate_motion = bool(interpolate_motion)
        self._authority_geometry = bool(authority_geometry)
        self._states = {}
        self._vehicles = {}
        self._failed_creates = set()
        self._descriptors = {}
        self._hit_testers = {}
        self.track_animation_error = None
        self._shot_presenter = _RemoteShotPresenter(
            bigworld, math_module, model_assembler, self._space_id)

    def prepare_descriptor(self, descriptor):
        # Stock Vehicle.prerequisites/CompoundAppearance own BSP references.
        self._descriptors[id(descriptor)] = descriptor
        return descriptor

    def create(self, descriptor, properties, position, rotation):
        self._retire_failed_creates()
        self.prepare_descriptor(descriptor)
        entity_id = self._binding.create_vehicle(
            properties, position, rotation)
        if entity_id is None:
            raise RuntimeError('native remote createEntity returned no id')
        # Exact #1513 enters client-created Vehicles asynchronously.  A
        # re-entrant entry would let stock startVisual consume a roster entry
        # which cannot yet exist, so reject rather than complete half a tank.
        if self._bigworld.entity(entity_id) is not None:
            self._binding.destroy_entity(entity_id)
            raise RuntimeError(
                'native remote Vehicle entered before createEntity returned')
        self._states[int(entity_id)] = _NativeRemoteState(
            self._bigworld, self._math, self._compatibility,
            self._data_links, position, rotation,
            interpolate_motion=self._interpolate_motion,
            authority_geometry=self._authority_geometry)
        self._vehicles[int(entity_id)] = None
        try:
            self._binding.arena_vehicle_added(entity_id, {
                'properties': properties, 'team_killer': False})
        except Exception as error:
            # The caller never receives this id, so remove it from the ordinary
            # factory registries immediately.  A client-created Vehicle enters
            # asynchronously in #1513 and destroyEntity is unsafe until the id
            # becomes visible; retain a tombstone and retire it exactly once at
            # the next safe lifecycle poll.
            self._states.pop(int(entity_id), None)
            self._vehicles.pop(int(entity_id), None)
            self._failed_creates.add(int(entity_id))
            try:
                self._retire_failed_creates()
            except Exception as cleanup_error:
                raise RuntimeError(
                    'native remote arena registration failed: %s; '
                    'entity cleanup failed: %s' % (error, cleanup_error))
            raise
        return int(entity_id)

    def get(self, entity_id):
        entity_id = int(entity_id)
        entity = self._vehicles.get(entity_id)
        if entity is not None:
            return entity
        return self._bigworld.entity(entity_id)

    def is_ready(self, entity_id):
        entity_id = int(entity_id)
        entity = self._bigworld.entity(entity_id)
        if not (entity is not None and
                bool(getattr(entity, 'inWorld', False)) and
                bool(getattr(entity, 'isStarted', False)) and
                getattr(entity, 'model', None) is not None and
                getattr(entity, 'appearance', None) is not None and
                getattr(entity, 'typeDescriptor', None) is not None):
            return False
        if self._vehicles.get(entity_id) is None:
            state = self._states.get(entity_id)
            if state is None:
                return False
            state.attach(entity)
            self._vehicles[entity_id] = entity
        return True

    def error(self, unused_entity_id):
        return None

    def set_entity_interpolate_motion(self, entity_id, enabled):
        """Switch one native presentation at an authority handoff."""
        state = self._states.get(int(entity_id))
        if state is None:
            return False
        return state.set_interpolate_motion(enabled)

    def projectile_collision_matrix(self, entity_id):
        """Return the canonical pose, never the authority-only render blend.

        The simulation worker eases its own compounds between copied-physics
        samples so its hidden renderer stays smooth.  Projectile authority is
        already swept against the copied pose timeline and must therefore use
        the state's unblended matrix.  Visible clients do not call this seam;
        their reticle and tracer continue to follow the rendered provider.
        """
        matrices = self.projectile_collision_matrices(entity_id)
        return None if matrices is None else matrices[0]

    def projectile_collision_matrices(self, entity_id, ground_matrix=None):
        """Return unblended hydraulic body and ground authority poses."""
        state = self._states.get(int(entity_id))
        if state is None or state.entity is None:
            return None
        getter = getattr(state, 'collision_matrices', None)
        if callable(getter):
            return getter(ground_matrix)
        canonical = state.matrix if ground_matrix is None else ground_matrix
        return canonical, canonical

    def update_entity_siege_pose(self, entity_id):
        state = self._states.get(int(entity_id))
        if state is None or state.entity is None:
            return False
        return state.update_siege_pose()

    def request_wreck(self, unused_entity_id):
        # Vehicle.onHealthChanged owns stock damaged-model replacement.
        return False

    def play_projectile_tracer(self, descriptor, shell_index, origin,
                               velocity, gravity, max_distance, attacker_id,
                               projectile_id=None, reference_position=None,
                               reference_velocity=None,
                               is_ricochet=False, visual_start=None):
        return self._shot_presenter.play_canonical(
            descriptor, shell_index, origin, velocity, gravity,
            max_distance, attacker_id, projectile_id,
            reference_position, reference_velocity, is_ricochet,
            visual_start)

    def admit_projectile_visual(self, attacker_id, projectile_id, now=None):
        """Reserve one bounded cosmetic slot for a canonical projectile."""
        return self._shot_presenter.admit_visual(
            attacker_id, projectile_id, now)

    def stop_projectile_tracer(self, projectile_id, end_position,
                               explosion=None, missed=False):
        return self._shot_presenter.stop_canonical(
            projectile_id, end_position, explosion, missed)

    def update_projectile_visual(self, projectile_id, position,
                                 velocity=None):
        """Move a tracer to the latest hidden-worker-confirmed cursor."""
        return self._shot_presenter.update_canonical(
            projectile_id, position, velocity)

    def reset_projectile_visuals(self):
        """Release the old epoch's visuals while keeping the factory live."""
        return self._shot_presenter.reset_canonical()

    def engine_owns(self, entity_id):
        entities = getattr(self._bigworld, 'entities', None)
        lookup = getattr(entities, 'get', None)
        return bool(callable(lookup) and lookup(int(entity_id)) is not None)

    def engine_active(self):
        return (any(self.engine_owns(entity_id)
                    for entity_id in self._states) or
                any(self.engine_owns(entity_id)
                    for entity_id in self._failed_creates))

    def _retire_failed_creates(self):
        """Destroy failed create requests only after BigWorld owns their ids."""
        first_error = None
        for entity_id in tuple(self._failed_creates):
            if not self.engine_owns(entity_id):
                continue
            try:
                self._binding.destroy_entity(entity_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
                continue
            self._failed_creates.discard(entity_id)
        if first_error is not None:
            raise first_error
        return not self._failed_creates

    def destroy(self, entity_id):
        entity_id = int(entity_id)
        state = self._states.get(entity_id)
        if state is None:
            return False
        state.detach()
        if self.engine_owns(entity_id):
            self._binding.destroy_entity(entity_id)
        # Native entity destruction is the ownership commit boundary.  A
        # failure above leaves both registries intact for an exact retry.
        self._states.pop(entity_id, None)
        self._vehicles.pop(entity_id, None)
        return True

    def destroy_all(self):
        first_error = None
        try:
            self._retire_failed_creates()
        except Exception as error:
            first_error = error
        for entity_id in tuple(self._states):
            try:
                self.destroy(entity_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        self._descriptors = {}
        try:
            self._shot_presenter.destroy()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error
