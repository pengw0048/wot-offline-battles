"""Record exact collision leaves, then replay the full numeric controller.

Replay is a computation experiment. It never supplies cached physics to a
live battle, and does not measure native engine cost or a shippable boundary.
"""
from __future__ import print_function
import importlib
import math
import sys

from world_adapter import inputs, point, reply


def request(kind, start, end, identity=0):
    return [kind] + point(start) + point(end) + [identity]


class Recorder(object):
    def __init__(self, backend=None):
        self.world = importlib.import_module('gui.mods.offline_lan_0922.world_collision')
        self.original = self.world._check_horizontal_collision
        self.backend = backend
        self.traces = []
        self.replacement = self.check
        self.world._check_horizontal_collision = self.replacement

    def close(self):
        if self.world._check_horizontal_collision is self.replacement:
            self.world._check_horizontal_collision = self.original

    def check(self, spaceID, pos, yaw, vel, td=None, airborne=False, dt=0.04,
              return_status=False, allow_kinetic=False, kinetic_speed=None,
              commit_enabled=True, motion_yaw=None, pitch=0.0, roll=0.0, trace=None):
        import BigWorld
        world = self.world
        header = inputs(world, pos, yaw, vel, td, airborne, dt, motion_yaw, pitch, roll)
        old_prepare, old_horizontal, old_destroy = (
            world.prepare_horizontal_collision_filter, world._collide_horizontal,
            world._destroy_and_recast)
        old_ray = BigWorld.wg_collideSegment
        rows, hit_ids = [], {}
        context = [2, False]

        def prepare(start, end):
            value = old_prepare(start, end)
            if not context[1]:
                rows.append(request(1, start, end) + [int(value is not None)] + [0] * 7)
            return value

        def ray(space, start, end, *args):
            value = old_ray(space, start, end, *args)
            if not context[1]:
                identity = len(rows) + 1
                data = reply(context[0], value, identity)
                rows.append(request(context[0], start, end) + data)
                if value is not None and context[0] == 3:
                    hit_ids[(tuple(point(start) + point(end)), id(value))] = identity
            return value

        def horizontal(*args, **kwargs):
            prior = context[0]
            context[0] = 3
            try:
                return old_horizontal(*args, **kwargs)
            finally:
                context[0] = prior

        def destroy(*args, **kwargs):
            context[1] = True
            try:
                value = old_destroy(*args, **kwargs)
            finally:
                context[1] = False
            start, end, hit = args[1:4]
            identity = hit_ids[(tuple(point(start) + point(end)), id(hit))]
            status = 2 if value == 'kinetic' else 1 if value is True else 0
            rows.append(request(4, start, end, identity) +
                        [status] + [0] * 6 + [int(bool(args[7][0]))])
            return value

        world.prepare_horizontal_collision_filter = prepare
        world._collide_horizontal = horizontal
        world._destroy_and_recast = destroy
        BigWorld.wg_collideSegment = ray
        try:
            value = self.original(
                spaceID, pos, yaw, vel, td, airborne, dt, True, allow_kinetic,
                kinetic_speed, commit_enabled, motion_yaw, pitch, roll, trace=trace)
        finally:
            world.prepare_horizontal_collision_filter = old_prepare
            world._collide_horizontal = old_horizontal
            world._destroy_and_recast = old_destroy
            BigWorld.wg_collideSegment = old_ray
        status = ('hard', 'clear', 'kinetic').index(value)
        trace = dict(inputs=header, queries=rows, status=status)
        if self.backend is not None:
            try:
                register(self.backend, trace)
            except RuntimeError:
                # A complete numeric record makes a mismatch reproducible.
                import json
                with open('/tmp/wot-world-mismatch.json', 'w') as stream:
                    json.dump(trace, stream)
                raise
        self.traces.append(trace)
        return value if return_status else status != 1


def register(backend, trace):
    packet = [403] + trace['inputs'] + [len(trace['queries'])]
    for row in trace['queries']:
        packet.extend(row)
    packet.append(trace['status'])
    return int(backend.call(packet)[0])


class Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __sub__(self, other):
        return Vector(self.x-other.x, self.y-other.y, self.z-other.z)

    @property
    def length(self):
        return math.sqrt(self.x*self.x + self.y*self.y + self.z*self.z)


class Namespace(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Replay(object):
    def __init__(self):
        self.world = importlib.import_module('gui.mods.offline_lan_0922.world_collision')
        self.missing = object()
        self.old_modules = dict((key, sys.modules.get(key, self.missing)) for key in ('BigWorld', 'Math'))
        sys.modules['BigWorld'] = Namespace(wg_collideSegment=self.ray)
        sys.modules['Math'] = Namespace(Vector3=Vector)
        names = ('prepare_horizontal_collision_filter', '_collide_horizontal',
                 '_destroy_and_recast', '_vehicle_motion_extents')
        self.originals = dict((name, getattr(self.world, name)) for name in names)
        self.world.prepare_horizontal_collision_filter = self.prepare
        self.world._collide_horizontal = self.horizontal
        self.world._destroy_and_recast = self.destroy
        self.world._vehicle_motion_extents = lambda unused: tuple(self.trace['inputs'][5:8])
        self.filter_token = object()
        self.kind = 2
        self.verify = True

    def close(self):
        for name, value in self.originals.items():
            setattr(self.world, name, value)
        for key, value in self.old_modules.items():
            if value is self.missing:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def consume(self, kind, start, end, identity=0):
        row = self.trace['queries'][self.cursor]
        if self.verify and row[:8] != request(kind, start, end, identity):
            raise AssertionError('Python trace query %d differs: %r != %r' %
                                 (self.cursor, request(kind, start, end, identity), row[:8]))
        self.cursor += 1
        return row[8:]

    def prepare(self, start, end):
        value = self.consume(1, start, end)
        return self.filter_token if value[0] else None

    def ray(self, space, start, end, *unused):
        value = self.consume(self.kind, start, end)
        if not value[0]:
            return None
        hit = (Vector(*value[1:4]), Vector(*value[4:7]), 0)
        self.hits[(tuple(point(start) + point(end)), id(hit))] = int(value[7])
        return hit

    def horizontal(self, *args, **kwargs):
        self.kind = 3
        try:
            return self.originals['_collide_horizontal'](*args, **kwargs)
        finally:
            self.kind = 2

    def destroy(self, *args, **unused):
        start, end, hit = args[1:4]
        identity = self.hits[(tuple(point(start) + point(end)), id(hit))]
        value = self.consume(4, start, end, identity)
        args[7][0] = bool(value[7])
        return 'kinetic' if value[0] == 2 else value[0] == 1

    def run(self, trace):
        self.trace, self.cursor, self.hits = trace, 0, {}
        v = trace['inputs']
        value = self.world._check_horizontal_collision(
            1, Vector(*v[:3]), v[3], v[4], None, bool(v[8]), v[9], True,
            motion_yaw=v[11] if v[10] else None, pitch=v[12], roll=v[13])
        if ('hard', 'clear', 'kinetic').index(value) != trace['status'] or self.cursor != len(trace['queries']):
            raise AssertionError('Python trace result/consumption differs')
        return trace['status']
