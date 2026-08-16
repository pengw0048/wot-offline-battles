"""Own the player vehicle pose, because the native body never simulates.

Each tick this integrates the copied motion law, places the hull on the
copied four-point suspension, and writes the result to the compound model
matrix. The native filter keeps doing what it can still do offline:
turret, gun and track presentation.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import motion, suspension
from gui.mods.offline_battle_2312 import engine_shim
from gui.mods.offline_battle_2312 import tank_collision, world_collision

TICK_SECONDS = 0.02
TRACED_COAST_TICKS = 40


class MotionDriver(object):

    def __init__(self, vehicle, position, yaw, log, obstacles=None):
        self._vehicle_id = vehicle.id
        self._space_id = vehicle.spaceID
        descriptor = vehicle.typeDescriptor
        self._params = motion.derive_params(descriptor)
        self._x = float(position.x)
        self._y = float(position.y)
        self._z = float(position.z)
        self._yaw = float(yaw)
        self._speed = 0.0
        self._omega = 0.0
        self._movement = 0
        self._rotation = 0
        self._log = log
        self._callback_id = None
        self._stopped = False
        self._ticks = 0
        self._matrix = None
        self._position = None
        self._speed_provider = None
        self._pitch_limits = descriptor.gun.pitchLimits['absolute']
        self._descriptor = descriptor
        self._length, self._width = suspension.hull_span(descriptor)
        self._pitch = 0.0
        self._roll = 0.0
        self._drive_pitch = 0.0
        self._drive_history = []
        self._blocks = 0
        self._steps = 0
        self._obstacles = obstacles
        self._shape = tank_collision.chassis_shape(descriptor)
        self._stat_factors = {}
        self._pushes = 0
        self._slide_speed = 0.0
        self._downhill = (0.0, 0.0)
        self._slope_tangent = 0.0
        self._coast_traces = 0
        self._turns = 0
        self._grind = 0

    @property
    def vehicle_id(self):
        return self._vehicle_id

    @property
    def speed(self):
        return self._speed

    @property
    def matrix(self):
        """Live pose provider: stock consumers bind to this object once."""
        return self._matrix

    @property
    def position(self):
        return self._position

    def set_input(self, movement, rotation):
        self._movement = int(movement)
        self._rotation = int(rotation)

    def start(self):
        import Math
        self._matrix = Math.Matrix()
        self._position = Math.Vector3(self._x, self._y, self._z)
        self._speed_provider = Math.Vector4Basic()
        ground = suspension.wide_ground_y(self._space_id, self._x, self._z)
        if ground is not None:
            self._y = ground
        self._refresh_pose()
        self._schedule()
        self._log('motion_started mass=%.0f power=%.0f fwd=%.2f rot=%.3f '
                  'span=(%.2f,%.2f)'
                  % (self._params['mass'], self._params['powerW'],
                     self._params['speedFwd'], self._params['rotSpd'],
                     self._length, self._width))

    def stop(self):
        import BigWorld
        self._stopped = True
        if self._callback_id is not None:
            try:
                BigWorld.cancelCallback(self._callback_id)
            except Exception:
                pass
            self._callback_id = None

    def _schedule(self):
        import BigWorld
        if self._stopped:
            return
        self._callback_id = BigWorld.callback(TICK_SECONDS, self._tick)

    def _blocked(self, yaw=None):
        """The copied horizontal law, through the engine shim."""
        import BigWorld
        import Math
        return world_collision.check_horizontal_collision(
            engine_shim.wrap(BigWorld), Math, self._space_id,
            Math.Vector3(self._x, self._y, self._z),
            self._yaw if yaw is None else yaw, self._speed, self._descriptor,
            False, TICK_SECONDS)

    def _rotate(self):
        """Turn. The mature driver does not gate rotation either."""
        self._yaw += self._omega * TICK_SECONDS
        while self._yaw > math.pi:
            self._yaw -= 2.0 * math.pi
        while self._yaw < -math.pi:
            self._yaw += 2.0 * math.pi

    def _translate(self):
        """Advance, deflect along the obstacle, or grind to a stop."""
        step = self._speed * TICK_SECONDS
        if not step:
            return
        if not self._blocked():
            self._x += math.sin(self._yaw) * step
            self._z += math.cos(self._yaw) * step
            self._grind = max(0, self._grind - 1)
            return
        self._blocks += 1
        for delta in world_collision.SLIDE_YAWS:
            slide_yaw = self._yaw + delta
            if self._blocked(slide_yaw):
                continue
            if self._grind <= 0:
                self._speed *= world_collision.SLIDE_FIRST_FACTOR
            self._speed *= world_collision.SLIDE_DECAY ** (TICK_SECONDS * 60.0)
            slide = self._speed * TICK_SECONDS
            self._x += math.sin(slide_yaw) * slide
            self._z += math.cos(slide_yaw) * slide
            self._grind = 4
            return
        self._speed *= world_collision.STOP_DECAY ** (TICK_SECONDS * 60.0)
        if abs(self._speed) < world_collision.STOP_SPEED:
            self._speed = 0.0
        self._grind = 4

    def set_stat_factors(self, factors):
        """Module damage reaches the drive law as stat multipliers."""
        self._stat_factors = dict(factors or {})

    def _resolve_hulls(self):
        """Copied tank-against-tank separation, so hulls are not ghosts."""
        if self._obstacles is None:
            return
        others = self._obstacles()
        if not others:
            return
        sin_yaw, cos_yaw = math.sin(self._yaw), math.cos(self._yaw)
        tank = {
            'id': self._vehicle_id,
            'x': self._x, 'y': self._y, 'z': self._z, 'yaw': self._yaw,
            'mass': float(self._params['mass']),
            'vx': sin_yaw * self._speed, 'vz': cos_yaw * self._speed,
            'alive': True, 'shape': self._shape,
        }
        result = tank_collision.resolve_tank(tank, others)
        correction_x, correction_z = result['correction']
        if not correction_x and not correction_z:
            return
        self._x += correction_x
        self._z += correction_z
        delta_x, delta_z = result['delta_velocity']
        self._speed += sin_yaw * delta_x + cos_yaw * delta_z
        self._pushes += 1

    def _hull_pose(self):
        """Four-point suspension: front, back and both track lines."""
        sin_yaw, cos_yaw = math.sin(self._yaw), math.cos(self._yaw)
        half_length = self._length * 0.5
        half_width = self._width * 0.5
        samples = []
        for offset_x, offset_z in ((0.0, half_length), (0.0, -half_length),
                                   (half_width, 0.0), (-half_width, 0.0)):
            x = self._x + sin_yaw * offset_z + cos_yaw * offset_x
            z = self._z + cos_yaw * offset_z - sin_yaw * offset_x
            samples.append(suspension.ground_y(self._space_id, x, z, self._y))
        if None in samples:
            return
        pitch, roll = suspension.pose_angles(samples[0], samples[1],
                                             samples[2], samples[3],
                                             self._length, self._width)
        self._pitch = suspension.smooth(self._pitch, pitch)
        self._roll = suspension.smooth(self._roll, roll)
        down_x, down_z, tangent = suspension.slope_fall_line(
            samples[0], samples[1], samples[2], samples[3],
            self._length, self._width, self._yaw)
        self._downhill = (down_x, down_z)
        self._slope_tangent = tangent

    def _settle(self, start_x, start_z):
        """Place the hull on its centre support, or reject a step."""
        highest, centre = suspension.support(
            self._space_id, (self._x, self._y, self._z), self._yaw,
            self._length * 0.5)
        ground = centre if centre is not None else highest
        if ground is None:
            return
        if suspension.support_rise_is_obstacle(
                self._y, centre, suspension.climb_limit(self._speed,
                                                        TICK_SECONDS)):
            self._x, self._z = start_x, start_z
            self._speed = 0.0
            self._steps += 1
            return
        self._y = suspension.settle(self._y, ground, self._speed,
                                    TICK_SECONDS)

    def _slide(self):
        """Copied passive fall-line slip, cross-heading only.

        The along-hull component is already in the longitudinal law, so
        only the sideways part is applied here and the two never double
        count."""
        self._slide_speed = motion.slope_slide_speed(
            self._slide_speed, self._slope_tangent, TICK_SECONDS)
        if self._slide_speed <= 0.01:
            return
        cross_x, cross_z = math.cos(self._yaw), -math.sin(self._yaw)
        slide_dot = self._downhill[0] * cross_x + self._downhill[1] * cross_z
        slide_x, slide_z = cross_x * slide_dot, cross_z * slide_dot
        if abs(slide_x) <= 0.0001 and abs(slide_z) <= 0.0001:
            return
        self._x += slide_x * self._slide_speed * TICK_SECONDS
        self._z += slide_z * self._slide_speed * TICK_SECONDS

    def _tick(self):
        import BigWorld
        self._callback_id = None
        if self._stopped:
            return
        # Reschedule first: a fault in one tick must not end driving.
        self._schedule()
        vehicle = BigWorld.entities.get(self._vehicle_id)
        if vehicle is None or not vehicle.isStarted:
            return

        self._drive_pitch = suspension.smooth(
            self._drive_pitch,
            suspension.median_pitch(
                self._drive_history,
                suspension.drive_pitch(self._space_id,
                                       (self._x, self._y, self._z),
                                       self._yaw)))
        steering = self._rotation != 0
        # Module damage scales the drive intent, the mature bot rule; a
        # per-tick multiplier on the velocity decays it geometrically.
        movement = self._movement * self._stat_factors.get('mobility', 1.0)
        rotation = self._rotation * self._stat_factors.get('traverse', 1.0)
        self._omega = motion.traverse_step(
            self._params, self._omega, rotation, self._speed,
            TICK_SECONDS, drive_intent=movement)
        self._speed = motion.longitudinal_step(
            self._params, self._speed, movement, steering,
            self._drive_pitch, TICK_SECONDS)

        self._rotate()
        start_x, start_z = self._x, self._z
        self._translate()
        after_drive = (self._x, self._z)
        self._resolve_hulls()
        after_hulls = (self._x, self._z)
        self._hull_pose()
        self._slide()
        after_slide = (self._x, self._z)
        self._settle(start_x, start_z)
        self._trace_coast((start_x, start_z), after_drive, after_hulls,
                          after_slide)

        self._apply_pose(vehicle)
        self._ticks += 1
        if self._ticks % 250 == 0:
            rotator = getattr(BigWorld.player(), 'gunRotator', None)
            self._log('motion_state pos=(%.2f,%.2f,%.2f) yaw=%.3f speed=%.2f '
                      'pitch=%.3f roll=%.3f drive_pitch=%.3f input=(%s,%s) '
                      'blocked=%s steps=%s turns=%s pushes=%s '
                      'turret=%s marker=%s'
                      % (self._x, self._y, self._z, self._yaw, self._speed,
                         self._pitch, self._roll, self._drive_pitch,
                         self._movement, self._rotation, self._blocks,
                         self._steps, self._turns, self._pushes,
                         getattr(rotator, 'turretYaw', None),
                         getattr(rotator, 'markerInfo', (None,))[0]))

    def _trace_coast(self, start, after_drive, after_hulls, after_slide):
        """Name the mover that keeps the hull creeping with no throttle.

        The driver reports itself stopped while the hull keeps covering
        ground, so each mover's own displacement is recorded."""
        if self._movement or self._coast_traces >= TRACED_COAST_TICKS:
            return
        drive = math.hypot(after_drive[0] - start[0],
                           after_drive[1] - start[1])
        hulls = math.hypot(after_hulls[0] - after_drive[0],
                           after_hulls[1] - after_drive[1])
        slide = math.hypot(after_slide[0] - after_hulls[0],
                           after_slide[1] - after_hulls[1])
        total = math.hypot(self._x - start[0], self._z - start[1])
        if total < 0.0005:
            return
        self._coast_traces += 1
        self._log('coast_step speed=%.4f drive=%.4f hulls=%.4f slide=%.4f '
                  'total=%.4f drive_pitch=%.3f slide_speed=%.3f tangent=%.3f'
                  % (self._speed, drive, hulls, slide, total,
                     self._drive_pitch, self._slide_speed,
                     self._slope_tangent))

    def _refresh_pose(self):
        import Math
        self._matrix.setRotateYPR((self._yaw, self._pitch, self._roll))
        self._matrix.translation = Math.Vector3(self._x, self._y, self._z)
        self._position.set(Math.Vector3(self._x, self._y, self._z))

    def _apply_pose(self, vehicle):
        import Math
        self._refresh_pose()
        model = getattr(vehicle, 'model', None)
        if model is not None and model.matrix is not self._matrix:
            model.matrix = self._matrix
        # The speedometer reads speedInfo.value, and that provider
        # dereferences a Vector4 provider rather than a raw vector.
        self._speed_provider.value = Math.Vector4(
            self._speed, self._omega, self._speed, self._omega)
        speed_info = getattr(vehicle, '_Vehicle__speedInfo', None)
        if speed_info is not None:
            speed_info.set(self._speed_provider)
        self._publish_gun_angles(vehicle)
        appearance = getattr(vehicle, 'appearance', None)
        if appearance is not None:
            left, right = motion.track_scroll(self._params, self._speed,
                                              self._omega)
            appearance.updateTracksScroll(left, right)
            try:
                vehicle.filter.leftTrackScroll = left
                vehicle.filter.rightTrackScroll = right
            except Exception:
                pass

    def _publish_gun_angles(self, vehicle):
        """Offline this runtime is the authority the turret syncs against.

        VehicleGunRotator pulls its estimate back to getServerGunAngles
        whenever the two drift apart, so the packed property has to carry
        the angles the rotator just produced."""
        import BigWorld
        from gun_rotation_shared import encodeGunAngles
        rotator = getattr(BigWorld.player(), 'gunRotator', None)
        if rotator is None:
            return
        vehicle.gunAnglesPacked = encodeGunAngles(
            rotator.turretYaw, rotator.gunPitch, self._pitch_limits)
