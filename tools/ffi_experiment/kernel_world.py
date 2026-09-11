"""Borrow native/catalog leaves for the stack-owned collision resolver.

Only the explicit experiment runner supplies a battle owner. Each operation
owns fresh hit/filter references; no native pointer or result survives update
failure or teardown. The source catalog retains mutation and event ownership.
"""
from __future__ import print_function
from array import array
import math

_ZERO = array('d', [0]) * 8
_STATUSES = ('clear', 'crushed', 'soft', 'cap_crushed', 'hard', 'approach')


def descriptor_profile(descriptor):
    from gui.mods.offline_lan_0922 import vehicle_physics, world_collision
    extents = world_collision._vehicle_motion_extents(descriptor)
    if extents is None:
        raise ValueError('Native world experiment requires exact collision extents')
    # The resolver derives unmodified descriptor limits, not crew/equipment
    # adjusted Bot driving parameters. Compile the same producer per mode.
    params = vehicle_physics.derive_params(descriptor)
    return dict(extents=extents, forward=params['speedFwd'], backward=params['speedBwd'])


class Context(object):
    def __init__(self, owner, descriptor, identity, values):
        self.identity, self.descriptor = identity, descriptor
        self.position = owner._vector(tuple(values[:3]))
        self.yaw, self.speed, self.dt, self.now = values[3:7]
        self.motion = {'motion_yaw': values[8]} if values[7] else {}
        self.crush, self.commit = bool(values[9]), bool(values[11])
        self.kinetic = values[10] if self.crush else None
        self.filter, self.crush_state, self.hits, self.sequence = None, [False], {}, 0


class WorldLeaves(object):
    def __init__(self, owner, engine):
        from gui.mods.offline_lan_0922 import world_collision
        if owner._bots is not engine.runtime:
            raise ValueError('Native world owner does not own this Bot runtime')
        self.owner, self.engine, self.world = owner, engine, world_collision
        self.space, self.round = owner._avatar.spaceID, engine.runtime.round_id
        self.catalog = owner._destructibles
        self.context = None
        self.calls = [0, 0, 0, 0]

    def clear(self):
        self.context = None

    def check_owner(self):
        if (self.owner._bots is not self.engine.runtime or
                self.owner._avatar.spaceID != self.space or
                self.engine.runtime.round_id != self.round or
                self.owner._destructibles is not self.catalog):
            raise RuntimeError('Native world operation lifetime changed')

    def __call__(self, packet):
        operation = int(packet[1])
        if operation not in (0, 1, 2, 3):
            raise RuntimeError('Unknown native world operation')
        self.check_owner()
        self.calls[operation] += 1
        if operation == 0:
            self.engine.runtime._sample_time_us = int(packet[62])
            self.engine.runtime._equipment_now = packet[63]
            self.begin(packet, int(packet[2]), int(packet[3]))
            self.check_owner()
            return
        if self.context is None or self.context.identity != packet[2]:
            raise RuntimeError('Native world operation has no matching owner')
        if operation == 1:
            return self.query(packet)
        if operation == 3:
            self.query(packet)
            # The scalar C++ reader rejects non-finite answers before issuing
            # another ray. Preserve that barrier inside the paired callback.
            if packet[0] and any(math.isnan(v) or math.isinf(v) for v in packet[1:7]):
                raise RuntimeError('world engine answer')
            self.check_owner()
            return self.query(packet, 20, 8)
        if operation == 2:
            try:
                self.finish(packet)
                self.check_owner()
                return
            finally:
                self.clear()
        raise RuntimeError('Unknown native world operation')

    def begin(self, packet, identity, mode):
        owner = self.owner
        if self.context is not None:
            raise RuntimeError('Native world operation is already active')
        values = packet[4:18]
        descriptor = self.engine.descriptor_at(identity, mode)
        context = Context(owner, descriptor, identity, values)
        self.context = context
        reusable = bool(values[12]) and self.catalog is not None
        if reusable:
            reusable = not self.catalog._catalog_hull_contact(
                context.position, context.yaw, context.speed, descriptor,
                context.dt, **context.motion)
        diagnostic = getattr(owner, '_combat_diagnostics', None)
        if diagnostic is not None:
            diagnostic.count('motion_world_reused' if reusable else 'motion_world_fallback')
            reason = int(values[13])
            if not reusable and reason:
                diagnostic.count(('unused', 'motion_world_airborne',
                                  'motion_world_coast_or_reverse', 'motion_world_turning')[reason])
        packet[0], packet[1] = reusable, self.catalog is not None
        if reusable:
            self.clear()

    def query(self, packet, at=4, out=0):
        import BigWorld
        import Math
        context, world = self.context, self.world
        kind, hit_identity = int(packet[at]), int(packet[at + 7])
        start = Math.Vector3(packet[at + 1], packet[at + 2], packet[at + 3])
        end = Math.Vector3(packet[at + 4], packet[at + 5], packet[at + 6])
        context.sequence += 1
        packet[out:out + 8] = _ZERO
        if kind == 1:
            context.filter = world.prepare_horizontal_collision_filter(start, end)
        elif kind == 2:
            try:
                args = (self.space, start, end, 128)
                if context.filter is not None:
                    args += (context.filter,)
                hit = world.observed_ray('native.motion.ground', BigWorld.wg_collideSegment, *args)
                if hit is not None:
                    packet[out], packet[out + 2] = 1, float(hit[0].y)
            except (AttributeError, IndexError, TypeError, ValueError):
                packet[out:out + 8] = _ZERO
        elif kind == 3:
            hit = world._collide_horizontal(self.space, start, end, context.filter)
            if hit is not None:
                packet[out] = 1
                packet[out + 1], packet[out + 2], packet[out + 3] = hit[0].x, hit[0].y, hit[0].z
                try:
                    normal = hit[1]
                    nx, ny, nz = normal.x, normal.y, normal.z
                except (AttributeError, IndexError, TypeError):
                    nx, ny, nz = 0, 0, 0
                packet[out + 4], packet[out + 5], packet[out + 6] = nx, ny, nz
                packet[out + 7] = context.sequence
                context.hits[context.sequence] = hit
        elif kind == 4:
            result = world._destroy_and_recast(
                self.space, start, end, context.hits[hit_identity],
                context.yaw, context.speed, context.descriptor,
                context.crush_state, context.crush, context.kinetic,
                context.commit, context.filter)
            packet[out] = 2 if result == 'kinetic' else 1 if result is True else 0
        else:
            raise RuntimeError('Unknown native world query')

    def finish(self, packet):
        context, owner = self.context, self.owner
        action = int(packet[4])
        owner._bot_motion_kinds[context.identity] = '-'
        packet[:8] = _ZERO
        if action == 1:
            pending = self.catalog._catalog_pending_at_hull(
                context.position, context.yaw, context.speed, context.descriptor,
                context.now, context.dt, **context.motion)
            if pending:
                owner._bot_motion_kinds[context.identity] = 'broken'
            packet[0] = bool(pending)
        elif action == 2:
            detail = self.catalog._catalog_motion_blocked(
                self.space, context.position, context.yaw, context.speed,
                context.descriptor, context.now, dt=context.dt,
                kinetic_speed=context.kinetic, return_detail=True,
                kinetic_commit=context.crush and context.commit,
                commit_enabled=context.commit, **context.motion)
            if isinstance(detail, bool):
                detail = {'status': 'hard' if detail else 'clear'}
            elif isinstance(detail, str):
                detail = {'status': detail}
            if not isinstance(detail, dict):
                raise RuntimeError('bot motion resolver detail is unavailable')
            status = detail.get('status')
            if status not in ('clear', 'crushed', 'soft', 'hard', 'approach'):
                raise RuntimeError('bot motion resolver returned an invalid status')
            owner._bot_motion_kinds[context.identity] = str(detail.get('kinds', '-'))
            packet[0] = _STATUSES.index(status)
            packet[1] = bool(detail.get('accepted_now', False))
            packet[2] = bool(detail.get('used_kinetic_speed', False))
            packet[3] = bool(detail.get('token'))
        elif action != 0:
            raise RuntimeError('Unknown native world completion')
