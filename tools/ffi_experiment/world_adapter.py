"""Opt-in horizontal collision core; Python retains exact engine leaves."""
from __future__ import print_function
from array import array
import importlib


def point(value):
    return [value.x, value.y, value.z]


def inputs(world, pos, yaw, vel, descriptor, airborne, dt, motion_yaw, pitch, roll):
    extents = world._vehicle_motion_extents(descriptor)
    if extents is None:
        extents = (1.5, 3.5, 3.5)
    return point(pos) + [yaw, vel] + list(extents) + [
        int(bool(airborne)), dt, int(motion_yaw is not None),
        motion_yaw if motion_yaw is not None else 0.0, pitch, roll]


def reply(kind, hit, identity=0):
    if hit is None:
        return [0] * 8
    if kind == 2:
        return [1, 0, float(hit[0].y), 0, 0, 0, 0, 0]
    try:
        normal = point(hit[1])
    except (AttributeError, IndexError, TypeError):
        normal = [0, 0, 0]
    return [1] + point(hit[0]) + normal + [identity]


class WorldBackend(object):
    def __init__(self, backend):
        self.backend = backend
        self.world = importlib.import_module('gui.mods.offline_lan_0922.world_collision')
        self.original = self.world._check_horizontal_collision
        self.replacement = self.check
        self.world._check_horizontal_collision = self.replacement

    def close(self):
        if self.world._check_horizontal_collision is self.replacement:
            self.world._check_horizontal_collision = self.original

    def check(self, spaceID, pos, yaw, vel, td=None, airborne=False, dt=0.04,
              return_status=False, allow_kinetic=False, kinetic_speed=None,
              commit_enabled=True, motion_yaw=None, pitch=0.0, roll=0.0):
        import BigWorld
        import Math
        world = self.world
        header = inputs(world, pos, yaw, vel, td, airborne, dt, motion_yaw, pitch, roll)
        values = self.backend.call([400] + header + [0] * 16)[-16:]
        handle = int(values[15])
        collision_filter, crush_state, hits = None, [False], {}
        packet = array('d', [401, handle] + [0] * 24)
        sequence = 0
        try:
            while values[0]:
                kind = int(values[0])
                start, end = Math.Vector3(*values[1:4]), Math.Vector3(*values[4:7])
                sequence += 1
                if kind == 1:
                    collision_filter = world.prepare_horizontal_collision_filter(start, end)
                    answer = [0] * 8
                elif kind == 2:
                    try:
                        args = (spaceID, start, end, 128)
                        if collision_filter is not None:
                            args += (collision_filter,)
                        hit = world.observed_ray('native.motion.ground', BigWorld.wg_collideSegment, *args)
                        answer = reply(kind, hit)
                    except (AttributeError, IndexError, TypeError, ValueError):
                        answer = [0] * 8
                elif kind == 3:
                    hit = world._collide_horizontal(spaceID, start, end, collision_filter)
                    answer = reply(kind, hit, sequence)
                    if hit is not None:
                        hits[sequence] = hit
                elif kind == 4:
                    result = world._destroy_and_recast(
                        spaceID, start, end, hits[int(values[7])], yaw, vel, td,
                        crush_state, allow_kinetic, kinetic_speed, commit_enabled,
                        collision_filter)
                    answer = [2 if result == 'kinetic' else 1 if result is True else 0] + [0] * 7
                else:
                    raise RuntimeError('unknown native world request')
                packet[2:10] = array('d', answer)
                self.backend.call(packet)
                values = packet[-16:]
            status = int(values[1])
            return ('hard', 'clear', 'kinetic')[status] if return_status else status != 1
        finally:
            self.backend.call([402, handle])
