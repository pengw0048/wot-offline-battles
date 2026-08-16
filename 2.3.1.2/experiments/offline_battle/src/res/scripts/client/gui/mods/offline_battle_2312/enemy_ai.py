"""Make the enemy vehicles aim and shoot back.

Turret aim goes out through `gunAnglesPacked`, the same property the
server writes for a remote vehicle, so the stock filter animates it. The
shot uses the client's own muzzle transform, copied from
`VehicleGunRotator.__getShotPosition`.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import projectiles

TICK_SECONDS = 0.25
TRACED_TICKS = 3
# Binding an enemy appearance provider is the open suspect behind the
# 0.7.1 and 0.8.0 load crashes. Off until a run proves the rest is clean.
ANIMATE_TURRET = False
ENGAGE_RANGE_METRES = 400.0
FIRST_SHOT_DELAY_SECONDS = 6.0
ALL_GUNS = -1


def aim_angles(shooter_position, shooter_yaw, target_position):
    """(turret yaw relative to the hull, gun pitch) to reach the target."""
    delta_x = target_position[0] - shooter_position[0]
    delta_y = target_position[1] - shooter_position[1]
    delta_z = target_position[2] - shooter_position[2]
    flat = math.sqrt(delta_x * delta_x + delta_z * delta_z)
    turret_yaw = math.atan2(delta_x, delta_z) - shooter_yaw
    while turret_yaw > math.pi:
        turret_yaw -= 2.0 * math.pi
    while turret_yaw < -math.pi:
        turret_yaw += 2.0 * math.pi
    return turret_yaw, math.atan2(delta_y, flat)


def clamp_pitch(pitch, limits):
    try:
        return max(float(limits[0]), min(float(limits[1]), pitch))
    except (IndexError, TypeError, ValueError):
        return pitch


class EnemyAI(object):

    def __init__(self, force, player_vehicle_id, scheduler, log,
                 on_player_hit=None):
        self._force = force
        self._player_vehicle_id = player_vehicle_id
        self._schedule = scheduler
        self._log = log
        self._on_player_hit = on_player_hit
        self._stopped = False
        self._elapsed = 0.0
        self._next_shot = {}
        self._shot_id = 0
        self._shots = 0
        self._aim_matrices = {}
        self._ticks = 0

    @property
    def shots(self):
        return self._shots

    def start(self):
        self._schedule(TICK_SECONDS, self._tick)
        self._log('enemy_ai_started range=%.0f first_shot=%.1f'
                  % (ENGAGE_RANGE_METRES, FIRST_SHOT_DELAY_SECONDS))

    def stop(self):
        self._stopped = True

    def _tick(self):
        """Aim and fire, but never while the battle is still loading.

        Touching an enemy appearance during the load reaches providers
        CGF is still wiring."""
        import BigWorld
        if self._stopped:
            return
        self._schedule(TICK_SECONDS, self._tick)
        avatar = BigWorld.player()
        if avatar is None or not avatar.userSeesWorld():
            return
        self._elapsed += TICK_SECONDS
        self._ticks += 1
        trace = self._ticks <= TRACED_TICKS
        if trace:
            self._stage('tick_begin')
        player = BigWorld.entities.get(self._player_vehicle_id)
        if player is None or not player.isStarted:
            return
        target = self._aim_point(player)
        if trace:
            self._stage('aim_point')
        for vehicle in self._force.alive():
            if not vehicle.isStarted:
                continue
            self._engage(vehicle, target, trace)
        if trace:
            self._stage('tick_done')

    def _stage(self, name, detail=''):
        self._log('enemy_ai_stage tick=%s name=%s%s'
                  % (self._ticks, name, detail))

    def _engage(self, vehicle, target, trace=False):
        import Math
        from gun_rotation_shared import encodeGunAngles
        pose = self._force.pose(vehicle.id)
        if pose is None:
            return
        matrix = Math.Matrix()
        matrix.setRotateY(pose[3])
        matrix.translation = Math.Vector3(pose[0], pose[1], pose[2])
        descriptor = vehicle.typeDescriptor
        muzzle = self._muzzle_origin(descriptor, matrix)
        distance = (target - muzzle).length
        if distance > ENGAGE_RANGE_METRES:
            return
        limits = descriptor.gun.pitchLimits['absolute']
        turret_yaw, pitch = aim_angles(
            (muzzle.x, muzzle.y, muzzle.z), pose[3],
            (target.x, target.y, target.z))
        pitch = clamp_pitch(pitch, limits)
        if trace:
            self._stage('aimed', ' id=%s yaw=%.3f pitch=%.3f'
                        % (vehicle.id, turret_yaw, pitch))
        vehicle.gunAnglesPacked = encodeGunAngles(turret_yaw, pitch, limits)
        if trace:
            self._stage('angles_written', ' id=%s' % (vehicle.id,))
        self._point_turret(vehicle, descriptor, turret_yaw, pitch, trace)
        if trace:
            self._stage('turret_pointed', ' id=%s' % (vehicle.id,))
        ready_at = self._next_shot.get(vehicle.id, FIRST_SHOT_DELAY_SECONDS)
        if self._elapsed < ready_at:
            return
        self._next_shot[vehicle.id] = (self._elapsed +
                                       float(descriptor.gun.reloadTime))
        self._fire(vehicle, matrix, turret_yaw, pitch)

    def _point_turret(self, vehicle, descriptor, turret_yaw, pitch,
                      trace=False):
        """Animate the turret without the native sync this build rejects.

        The appearance follows the filter's turret and gun matrices, so
        this points those providers at matrices the runtime owns and
        drives them the way VehicleGunRotator drives the player's."""
        import Math
        from gun_rotation_shared import calcGunPitchCorrection
        if not ANIMATE_TURRET:
            return
        state = self._aim_matrices.get(vehicle.id)
        if state is None:
            appearance = vehicle.appearance
            if appearance is None:
                return
            state = (Math.Matrix(), Math.Matrix())
            state[0].setRotateY(turret_yaw)
            state[1].setRotateX(pitch)
            if trace:
                self._stage('binding_turret', ' id=%s' % (vehicle.id,))
            appearance.turretMatrix.target = state[0]
            appearance.gunMatrix.target = state[1]
            if trace:
                self._stage('bound_turret', ' id=%s' % (vehicle.id,))
            self._aim_matrices[vehicle.id] = state
        state[0].setRotateY(turret_yaw)
        state[1].setRotateX(pitch - calcGunPitchCorrection(
            turret_yaw, descriptor.hull.turretPitches[0],
            descriptor.turret.gunJointPitch))

    def _aim_point(self, player):
        """Aim at the hull centre, not at the ground under the hull."""
        import Math
        return self._muzzle_origin(player.typeDescriptor,
                                   Math.Matrix(player.matrix))

    @staticmethod
    def _muzzle_origin(descriptor, matrix):
        """Where this vehicle's gun sits, and what a shooter aims at."""
        turret_offset = (descriptor.hull.turretPositions[0] +
                         descriptor.chassis.hullPosition)
        return matrix.applyPoint(turret_offset)

    def _muzzle(self, vehicle, matrix, turret_yaw, pitch):
        """Copy the client's own shot transform for a turret pose."""
        import Math
        descriptor = vehicle.typeDescriptor
        turret_offset = (descriptor.hull.turretPositions[0] +
                         descriptor.chassis.hullPosition)
        turret = Math.Matrix()
        turret.setRotateY(turret_yaw)
        turret.translation = turret_offset
        turret.postMultiply(matrix)
        position = turret.applyPoint(descriptor.activeGunShotPosition)
        gun = Math.Matrix()
        gun.setRotateX(pitch)
        gun.postMultiply(turret)
        velocity = gun.applyVector(
            Math.Vector3(0.0, 0.0, descriptor.shot.speed))
        return position, velocity

    def _fire(self, vehicle, matrix, turret_yaw, pitch):
        import BigWorld
        from items.components.component_constants import INVALID_EFFECT_INDEX
        avatar = BigWorld.player()
        if avatar is None:
            return
        shot = vehicle.typeDescriptor.shot
        shell = shot.shell
        start, velocity = self._muzzle(vehicle, matrix, turret_yaw, pitch)
        self._shot_id += 1
        shot_id = -self._shot_id
        self._stage('firing', ' id=%s' % (vehicle.id,))
        vehicle.showShooting(1, ALL_GUNS, shell.kindIdx)
        avatar.showTracer(vehicle.id, shot_id, False, shell.effectsIndex,
                          INVALID_EFFECT_INDEX, shell.kindIdx, shell.caliber,
                          start, velocity, shot.gravity, shot.maxDistance,
                          0, 0)
        self._shots += 1
        self._stage('tracer_shown', ' id=%s' % (vehicle.id,))
        player = BigWorld.entities.get(self._player_vehicle_id)
        targets = [player] if player is not None else []
        landing = projectiles.impact(
            vehicle.spaceID, (start.x, start.y, start.z),
            (velocity.x, velocity.y, velocity.z), float(shot.gravity),
            float(shot.maxDistance), targets)
        if landing is None:
            return
        self._schedule(landing.elapsed,
                       lambda: self._land(shot_id, shot, vehicle.id, landing))

    def _land(self, shot_id, shot, shooter_id, landing):
        import BigWorld
        import Math
        from items.components.component_constants import INVALID_EFFECT_INDEX
        avatar = BigWorld.player()
        if avatar is None:
            return
        shell = shot.shell
        direction = Math.Vector3(0.0, -1.0, 0.0)
        avatar.explodeProjectile(shot_id, shell.effectsIndex,
                                 INVALID_EFFECT_INDEX, 0, shell.kindIdx,
                                 shell.caliber, landing.point, direction,
                                 float(shot.speed), '')
        if landing.vehicle is not None and self._on_player_hit is not None:
            self._on_player_hit(landing, shot, shooter_id)
