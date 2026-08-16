"""Own the player vehicle pose, because the native body never simulates.

Each tick this integrates the copied motion law, follows the terrain with
`BigWorld.wg_collideSegment`, and writes the result to the compound model
matrix. The native filter keeps doing what it can still do offline:
turret, gun and track presentation.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import motion, world_collision

TICK_SECONDS = 0.02
TERRAIN_COLLISION_MASK = 128
TERRAIN_ONLY_FLAGS = 8
TERRAIN_RAY_HEIGHT = 1000.0
# Sampling the hull front and back gives the pitch the law integrates on,
# and the sides give the roll the hull sits at.
HULL_HALF_LENGTH = 2.0
HULL_HALF_WIDTH = 1.2


class MotionDriver(object):

    def __init__(self, vehicle, position, yaw, log):
        self._vehicle_id = vehicle.id
        self._space_id = vehicle.spaceID
        self._params = motion.derive_params(vehicle.typeDescriptor)
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
        self._pitch_limits = vehicle.typeDescriptor.gun.pitchLimits['absolute']
        self._extents = world_collision.hull_extents(vehicle.typeDescriptor)

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
        self._refresh_pose(0.0)
        self._schedule()
        self._log('motion_started mass=%.0f power=%.0f fwd=%.2f rot=%.3f '
                  'hull=(%.2f,%.2f,%.2f)'
                  % (self._params['mass'], self._params['powerW'],
                     self._params['speedFwd'], self._params['rotSpd'],
                     self._extents[0], self._extents[1], self._extents[2]))

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

    def _ground_height(self, x, z):
        import BigWorld
        import Math
        collision = BigWorld.wg_collideSegment(
            self._space_id, Math.Vector3(x, TERRAIN_RAY_HEIGHT, z),
            Math.Vector3(x, -TERRAIN_RAY_HEIGHT, z), TERRAIN_COLLISION_MASK,
            TERRAIN_ONLY_FLAGS)
        if collision is None:
            return None
        return float(collision.closestPoint.y)

    def _blocked(self):
        return world_collision.blocked(
            self._space_id, (self._x, self._y, self._z), self._yaw,
            self._speed, self._extents, TICK_SECONDS)

    def _hull_roll(self, x, z, sin_yaw, cos_yaw):
        left = self._ground_height(x - cos_yaw * HULL_HALF_WIDTH,
                                   z + sin_yaw * HULL_HALF_WIDTH)
        right = self._ground_height(x + cos_yaw * HULL_HALF_WIDTH,
                                    z - sin_yaw * HULL_HALF_WIDTH)
        if left is None or right is None:
            return 0.0
        return math.atan2(left - right, 2.0 * HULL_HALF_WIDTH)

    def _slope_pitch(self, x, z, sin_yaw, cos_yaw):
        front = self._ground_height(x + sin_yaw * HULL_HALF_LENGTH,
                                    z + cos_yaw * HULL_HALF_LENGTH)
        back = self._ground_height(x - sin_yaw * HULL_HALF_LENGTH,
                                   z - cos_yaw * HULL_HALF_LENGTH)
        if front is None or back is None:
            return 0.0
        # BigWorld convention: nose up is a negative pitch.
        return -math.atan2(front - back, 2.0 * HULL_HALF_LENGTH)

    def _tick(self):
        import BigWorld
        self._callback_id = None
        if self._stopped:
            return
        vehicle = BigWorld.entities.get(self._vehicle_id)
        if vehicle is None or not vehicle.isStarted:
            self._schedule()
            return

        sin_yaw = math.sin(self._yaw)
        cos_yaw = math.cos(self._yaw)
        pitch = self._slope_pitch(self._x, self._z, sin_yaw, cos_yaw)
        steering = self._rotation != 0

        self._omega = motion.traverse_step(
            self._params, self._omega, self._rotation, self._speed,
            TICK_SECONDS, drive_intent=self._movement)
        self._speed = motion.longitudinal_step(
            self._params, self._speed, self._movement, steering, pitch,
            TICK_SECONDS)

        self._yaw += self._omega * TICK_SECONDS
        sin_yaw = math.sin(self._yaw)
        cos_yaw = math.cos(self._yaw)
        step = self._speed * TICK_SECONDS
        if step and self._blocked():
            self._speed = 0.0
            step = 0.0
        x = self._x + sin_yaw * step
        z = self._z + cos_yaw * step
        ground = self._ground_height(x, z)
        if ground is not None:
            self._x, self._z, self._y = x, z, ground
        roll = self._hull_roll(self._x, self._z, sin_yaw, cos_yaw)

        self._apply_pose(vehicle, pitch, roll)
        self._ticks += 1
        if self._ticks % 250 == 0:
            import BigWorld
            rotator = getattr(BigWorld.player(), 'gunRotator', None)
            self._log('motion_state pos=(%.2f,%.2f,%.2f) yaw=%.3f speed=%.2f '
                      'omega=%.3f input=(%s,%s) turret=%s pitch=%s '
                      'marker=%s avatar_vehicle=%s'
                      % (self._x, self._y, self._z, self._yaw, self._speed,
                         self._omega, self._movement, self._rotation,
                         getattr(rotator, 'turretYaw', None),
                         getattr(rotator, 'gunPitch', None),
                         getattr(rotator, 'markerInfo', (None,))[0],
                         getattr(BigWorld.player(), 'vehicle', None)
                         is not None))
        self._schedule()

    def _refresh_pose(self, pitch, roll=0.0):
        import Math
        self._matrix.setRotateYPR((self._yaw, pitch, roll))
        self._matrix.translation = Math.Vector3(self._x, self._y, self._z)
        self._position.set(Math.Vector3(self._x, self._y, self._z))

    def _apply_pose(self, vehicle, pitch, roll=0.0):
        import Math
        self._refresh_pose(pitch, roll)
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
