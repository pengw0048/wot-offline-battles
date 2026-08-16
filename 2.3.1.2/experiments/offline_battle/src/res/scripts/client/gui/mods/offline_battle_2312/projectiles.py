"""Fly the shell and end it where it lands.

`Avatar.showTracer` hands the shell to the native ballistics simulator,
which flies it and detects its collision. The cell normally follows with
`explodeProjectile` at the impact, so this module marches the same
parabola in Python to find that point and that time.
"""
from __future__ import absolute_import

import math

COLLISION_MASK = 128
MAX_FLIGHT_SECONDS = 20.0
STEP_SECONDS = 0.05
DEFAULT_GRAVITY = 9.81


def trajectory_position(start, velocity, gravity, elapsed):
    """One absolute point on the shell parabola."""
    time = max(0.0, float(elapsed))
    return (start[0] + velocity[0] * time,
            start[1] + velocity[1] * time - 0.5 * gravity * time * time,
            start[2] + velocity[2] * time)


def flight_seconds(velocity, gravity, max_distance):
    """Time until the shell has flown its maximum distance."""
    speed = math.sqrt(velocity[0] ** 2 + velocity[1] ** 2 + velocity[2] ** 2)
    if speed <= 0.0:
        return 0.0
    return min(MAX_FLIGHT_SECONDS, float(max_distance) / speed)


def impact(space_id, start, velocity, gravity, max_distance,
           step=STEP_SECONDS):
    """(point, elapsed, matKind) where the parabola first meets the world."""
    import BigWorld
    import Math
    duration = flight_seconds(velocity, gravity, max_distance)
    if duration <= 0.0:
        return None
    count = max(1, int(math.ceil(duration / step)))
    previous = trajectory_position(start, velocity, gravity, 0.0)
    for index in range(1, count + 1):
        elapsed = duration * index / float(count)
        current = trajectory_position(start, velocity, gravity, elapsed)
        collision = BigWorld.wg_collideSegment(
            space_id, Math.Vector3(*previous), Math.Vector3(*current),
            COLLISION_MASK)
        if collision is not None:
            point = collision.closestPoint
            chord = math.sqrt(sum((current[axis] - previous[axis]) ** 2
                                  for axis in range(3)))
            hit = math.sqrt((point.x - previous[0]) ** 2 +
                            (point.y - previous[1]) ** 2 +
                            (point.z - previous[2]) ** 2)
            fraction = hit / chord if chord > 0.0 else 0.0
            reached = (elapsed - duration / float(count) +
                       fraction * duration / float(count))
            return point, reached, getattr(collision, 'matKind', 0)
        previous = current
    return None


def _effect_material_index(mat_kind):
    import material_kinds
    indexes = material_kinds.EFFECT_MATERIAL_INDEXES_BY_IDS or {}
    return indexes.get(mat_kind, 0)


class ProjectileRunner(object):
    """Launch one shell per shot and end it at its impact."""

    def __init__(self, vehicle, scheduler, log):
        self._vehicle_id = vehicle.id
        self._space_id = vehicle.spaceID
        self._schedule = scheduler
        self._log = log
        self._shot_id = 0
        self._launched = 0

    @property
    def launched(self):
        return self._launched

    def fire(self, avatar, shot):
        import BigWorld
        import Math
        from items.components.component_constants import INVALID_EFFECT_INDEX
        rotator = avatar.gunRotator
        if rotator is None:
            return
        start, velocity = rotator.getCurShotPosition()
        gravity = float(getattr(shot, 'gravity', DEFAULT_GRAVITY))
        max_distance = float(shot.maxDistance)
        shell = shot.shell
        self._shot_id += 1
        shot_id = self._shot_id
        avatar.showTracer(self._vehicle_id, shot_id, False,
                          shell.effectsIndex, INVALID_EFFECT_INDEX,
                          shell.kindIdx, shell.caliber, start, velocity,
                          gravity, max_distance, 0, 0)
        self._launched += 1
        landing = impact(self._space_id,
                         (start.x, start.y, start.z),
                         (velocity.x, velocity.y, velocity.z),
                         gravity, max_distance)
        if landing is None:
            self._schedule(flight_seconds((velocity.x, velocity.y, velocity.z),
                                          gravity, max_distance),
                           lambda: self._expire(shot_id, start, velocity,
                                                gravity, max_distance))
            return
        point, elapsed, mat_kind = landing
        speed = Math.Vector3(velocity.x, velocity.y - gravity * elapsed,
                             velocity.z)
        direction = Math.Vector3(speed)
        direction.normalise()
        material = _effect_material_index(mat_kind)
        self._schedule(elapsed, lambda: self._explode(
            shot_id, shell, point, direction, speed.length, material))
        if self._launched == 1:
            self._log('projectile_launched shot=%s speed=%.0f gravity=%.1f '
                      'flight=%.2f impact=(%.1f,%.1f,%.1f) material=%s'
                      % (shot_id, velocity.length, gravity, elapsed,
                         point.x, point.y, point.z, material))

    def _explode(self, shot_id, shell, point, direction, speed, material):
        import BigWorld
        from items.components.component_constants import INVALID_EFFECT_INDEX
        avatar = BigWorld.player()
        if avatar is None:
            return
        avatar.explodeProjectile(shot_id, shell.effectsIndex,
                                 INVALID_EFFECT_INDEX, material,
                                 shell.kindIdx, shell.caliber, point,
                                 direction, speed, '')

    def _expire(self, shot_id, start, velocity, gravity, max_distance):
        import BigWorld
        import Math
        avatar = BigWorld.player()
        if avatar is None:
            return
        elapsed = flight_seconds((velocity.x, velocity.y, velocity.z),
                                 gravity, max_distance)
        end = trajectory_position((start.x, start.y, start.z),
                                  (velocity.x, velocity.y, velocity.z),
                                  gravity, elapsed)
        avatar.stopTracer(shot_id, Math.Vector3(*end))
