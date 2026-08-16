"""Drive the visible turret and gun joints of every vehicle.

Offline nothing consumes the appearance matrix providers (measured:
rotator and provider agree while the node stays at hull yaw), so this
writes the joints directly, the way the stock hangar SimpleTurretRotator
does: node(...).local takes the full joint transform.
"""
from __future__ import absolute_import

import math


class Rig(object):

    def __init__(self, vehicle_id, appearance, descriptor):
        import Math
        from vehicle_systems.tankStructure import TankNodeNames
        self.vehicle_id = vehicle_id
        self._turret_local = Math.Matrix()
        self._gun_local = Math.Matrix()
        self._turret_pitch = float(descriptor.hull.turretPitches[0])
        self._turret_position = descriptor.hull.turretPositions[0]
        self._gun_joint_pitch = float(descriptor.turret.gunJointPitch)
        self._gun_position = descriptor.turret.gunPosition
        self._rotation_speed = float(getattr(descriptor.turret,
                                             'rotationSpeed', 2.0))
        self.yaw = 0.0
        self.pitch = 0.0
        self._desired_yaw = 0.0
        self._desired_pitch = 0.0
        model = appearance.compoundModel
        model.node(TankNodeNames.TURRET_JOINT).local = self._turret_local
        model.node(TankNodeNames.GUN_JOINT).local = self._gun_local
        self._apply()

    def aim(self, turret_yaw, gun_pitch):
        """Where the turret should end up; advance() slews toward it."""
        self._desired_yaw = float(turret_yaw)
        self._desired_pitch = float(gun_pitch)

    def snap(self, turret_yaw, gun_pitch):
        """Take the pose now; the caller already owns the slewing."""
        self.yaw = float(turret_yaw)
        self.pitch = float(gun_pitch)
        self._desired_yaw = self.yaw
        self._desired_pitch = self.pitch
        self._apply()

    def advance(self, dt):
        delta = self._desired_yaw - self.yaw
        while delta > math.pi:
            delta -= 2.0 * math.pi
        while delta < -math.pi:
            delta += 2.0 * math.pi
        step = self._rotation_speed * dt
        if abs(delta) <= step:
            self.yaw = self._desired_yaw
        else:
            self.yaw += step if delta > 0.0 else -step
        self.pitch = self._desired_pitch
        self._apply()

    def _apply(self):
        import Math
        turret = self._turret_local
        turret.setRotateX(self._turret_pitch)
        turret.translation = self._turret_position
        rotation = Math.Matrix()
        rotation.setRotateY(self.yaw)
        turret.preMultiply(rotation)
        gun = self._gun_local
        gun.setRotateX(self._gun_joint_pitch + self.pitch)
        gun.translation = self._gun_position


class TurretRigs(object):
    """One frame ticker; the player snaps to the rotator, bots slew."""

    def __init__(self, scheduler, log):
        self._schedule = scheduler
        self._log = log
        self._rigs = {}
        self._player_id = None
        self._stopped = False
        self._last_time = None

    def bind(self, vehicle, is_player=False):
        appearance = getattr(vehicle, 'appearance', None)
        if appearance is None or appearance.compoundModel is None:
            self._log('turret_rig_skipped id=%s' % (vehicle.id,))
            return None
        rig = Rig(vehicle.id, appearance, vehicle.typeDescriptor)
        self._rigs[vehicle.id] = rig
        if is_player:
            self._player_id = vehicle.id
        self._log('turret_rig_bound id=%s player=%s' % (vehicle.id,
                                                        is_player))
        return rig

    def ensure(self, vehicle):
        if vehicle.id not in self._rigs:
            self.bind(vehicle)

    def aim(self, vehicle_id, turret_yaw, gun_pitch):
        rig = self._rigs.get(vehicle_id)
        if rig is not None:
            rig.aim(turret_yaw, gun_pitch)

    def start(self):
        self._schedule(0.0, self._tick)

    def stop(self):
        self._stopped = True

    def _tick(self):
        import BigWorld
        if self._stopped:
            return
        self._schedule(0.0, self._tick)
        now = BigWorld.time()
        last = self._last_time
        self._last_time = now
        if last is None:
            return
        dt = min(0.35, max(0.0, now - last))
        rotator = getattr(BigWorld.player(), 'gunRotator', None)
        for rig in self._rigs.values():
            if rig.vehicle_id == self._player_id:
                if rotator is not None:
                    rig.snap(rotator.turretYaw, rotator.gunPitch)
            else:
                rig.advance(dt)
