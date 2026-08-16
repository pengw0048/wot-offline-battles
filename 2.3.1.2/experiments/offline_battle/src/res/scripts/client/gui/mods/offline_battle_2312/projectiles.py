"""Fly the shell and end it where it lands.

`Avatar.showTracer` hands the shell to the native ballistics simulator,
which flies it and detects its collision. The cell normally follows with
`explodeProjectile` at the impact, so this module marches the same
parabola in Python to find that point, that time, and whatever vehicle
stands in the way.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import damage
from gui.mods.offline_battle_2312 import projectile_runtime

COLLISION_MASK = 128
MAX_FLIGHT_SECONDS = 20.0
STEP_SECONDS = 0.05
DEFAULT_GRAVITY = 9.81


def trajectory_position(start, velocity, gravity, elapsed):
    """One absolute point on the shell parabola.

    The client hands gravity as a downward scalar; the copied law takes
    a vector."""
    return projectile_runtime.trajectory_position(
        start, velocity, (0.0, -abs(float(gravity)), 0.0), elapsed)


def flight_seconds(velocity, gravity, max_distance):
    """Time until the shell has flown its maximum distance."""
    speed = math.sqrt(velocity[0] ** 2 + velocity[1] ** 2 + velocity[2] ** 2)
    if speed <= 0.0:
        return 0.0
    return min(MAX_FLIGHT_SECONDS, float(max_distance) / speed)


def _distance(first, second):
    return math.sqrt(sum((second[axis] - first[axis]) ** 2
                         for axis in range(3)))


class Impact(object):
    """Where a shell stopped, and on what."""

    def __init__(self, point, elapsed, travelled, mat_kind=0, vehicle=None,
                 collisions=None):
        self.point = point
        self.elapsed = elapsed
        self.travelled = travelled
        self.mat_kind = mat_kind
        self.vehicle = vehicle
        self.collisions = collisions


def impact(space_id, start, velocity, gravity, max_distance, targets=(),
           step=STEP_SECONDS):
    """March the parabola and report the first thing the shell meets."""
    import BigWorld
    import Math
    duration = flight_seconds(velocity, gravity, max_distance)
    if duration <= 0.0:
        return None
    count = max(1, int(math.ceil(duration / step)))
    chord_seconds = duration / float(count)
    previous = trajectory_position(start, velocity, gravity, 0.0)
    travelled = 0.0
    for index in range(1, count + 1):
        current = trajectory_position(start, velocity, gravity,
                                      chord_seconds * index)
        chord = _distance(previous, current)
        head = Math.Vector3(*previous)
        tail = Math.Vector3(*current)
        terrain = BigWorld.wg_collideSegment(space_id, head, tail,
                                             COLLISION_MASK)
        terrain_reach = None
        if terrain is not None:
            terrain_reach = (terrain.closestPoint - head).length
        target = damage.nearest_vehicle(targets, head, tail)
        if target is not None and (terrain_reach is None or
                                   target[1] < terrain_reach):
            vehicle, reach, collisions = target
            fraction = reach / chord if chord else 0.0
            point = Math.Vector3(
                previous[0] + (current[0] - previous[0]) * fraction,
                previous[1] + (current[1] - previous[1]) * fraction,
                previous[2] + (current[2] - previous[2]) * fraction)
            return Impact(point,
                          chord_seconds * (index - 1 + fraction),
                          travelled + reach, vehicle=vehicle,
                          collisions=collisions)
        if terrain is not None:
            fraction = terrain_reach / chord if chord else 0.0
            return Impact(terrain.closestPoint,
                          chord_seconds * (index - 1 + fraction),
                          travelled + terrain_reach,
                          mat_kind=getattr(terrain, 'matKind', 0))
        travelled += chord
        previous = current
    return None


def _effect_material_index(mat_kind):
    import material_kinds
    indexes = material_kinds.EFFECT_MATERIAL_INDEXES_BY_IDS or {}
    return indexes.get(mat_kind, 0)


class ProjectileRunner(object):
    """Launch one shell per shot and end it at its impact."""

    def __init__(self, vehicle, scheduler, log, targets=None,
                 on_vehicle_hit=None):
        self._vehicle_id = vehicle.id
        self._space_id = vehicle.spaceID
        self._schedule = scheduler
        self._log = log
        self._targets = targets
        self._on_vehicle_hit = on_vehicle_hit
        self._shot_id = 0
        self._launched = 0

    @property
    def launched(self):
        return self._launched

    def _live_targets(self):
        return self._targets() if self._targets is not None else ()

    def fire(self, avatar, shot):
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
        landing = impact(self._space_id, (start.x, start.y, start.z),
                         (velocity.x, velocity.y, velocity.z), gravity,
                         max_distance, self._live_targets())
        if landing is None:
            self._schedule(
                flight_seconds((velocity.x, velocity.y, velocity.z), gravity,
                               max_distance),
                lambda: self._expire(shot_id, start, velocity, gravity,
                                     max_distance))
            return
        speed = Math.Vector3(velocity.x,
                             velocity.y - gravity * landing.elapsed,
                             velocity.z)
        direction = Math.Vector3(speed)
        direction.normalise()
        material = _effect_material_index(landing.mat_kind)
        self._schedule(landing.elapsed, lambda: self._land(
            shot_id, shell, shot, landing, direction, speed.length, material))
        if self._launched == 1:
            self._log('projectile_launched shot=%s speed=%.0f flight=%.2f '
                      'travelled=%.1f target=%s'
                      % (shot_id, velocity.length, landing.elapsed,
                         landing.travelled,
                         landing.vehicle.id if landing.vehicle else None))

    def _land(self, shot_id, shell, shot, landing, direction, speed,
              material):
        import BigWorld
        from items.components.component_constants import INVALID_EFFECT_INDEX
        avatar = BigWorld.player()
        if avatar is None:
            return
        avatar.explodeProjectile(shot_id, shell.effectsIndex,
                                 INVALID_EFFECT_INDEX, material,
                                 shell.kindIdx, shell.caliber, landing.point,
                                 direction, speed, '')
        if landing.vehicle is not None and self._on_vehicle_hit is not None:
            self._on_vehicle_hit(landing, shot)

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
