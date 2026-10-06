# -*- coding: utf-8 -*-
"""Keep live engine effects on the caller while C++ runs the world law."""

def run(module, spaceID, pos, yaw, vel, td=None, airborne=False, dt=0.04,
        return_status=False, allow_kinetic=False, kinetic_speed=None,
        commit_enabled=True, motion_yaw=None, pitch=0.0, roll=0.0,
        trace=None, exact_footprint=False, departing_contact=None):
    from . import native_math
    if not native_math.world_available():
        return None
    import BigWorld, Math
    bounds = module._vehicle_motion_bounds(td)
    if bounds is None:
        bounds = (-1.5, 1.5, 3.5, 3.5)
    left, right, back, front = bounds
    if trace is not None:
        trace.clear()
        trace.update(position=(pos.x, pos.y, pos.z), yaw=yaw, speed=vel,
                     dt=dt, motion_yaw=motion_yaw, pitch=pitch, roll=roll,
                     airborne=airborne,
                     extents=(max(abs(left), abs(right)), back, front),
                     lateral_bounds=(left, right))
    heights = module._vehicle_motion_heights(td)
    snapshot = (pos.x, pos.y, pos.z, yaw, vel, dt, pitch, roll,
                0.0 if motion_yaw is None else motion_yaw) + tuple(bounds) + tuple(heights) + (
                motion_yaw is not None, airborne, exact_footprint,
                departing_contact is not None)
    hits = {}
    state = [module._UNPREPARED_COLLISION_FILTER, departing_contact]
    crush = [False]
    V = Math.Vector3
    def xyz(v):
        return (v.x, v.y, v.z)
    def dispatch(op, rows):
        if op == 1:
            a, b = rows[0]
            state[0] = module._trace_collision_filter(
                module.prepare_horizontal_collision_filter(V(*a), V(*b)), trace)
            return None
        if op == 2:
            v = rows[0]
            state[1] = module._translation_departing_contact(
                pos, yaw, bounds, v[3:6], v[0], v[1], heights,
                dy=v[2], pose_axes=(v[6:9], v[9:12], v[12:15])
                if motion_yaw is not None else None)
            return None
        if op == 5:
            a, b, handle = rows[0]
            result = module._destroy_and_recast(
                spaceID, V(*a), V(*b), hits[handle], yaw, vel, td,
                crush, allow_kinetic, kinetic_speed, commit_enabled, state[0])
            return (2 if result == 'kinetic' else 1 if result is True else 0,)
        if op == 6:
            a, b, handle, reason, ground_ahead, profile = rows[0]
            module._record_hard_contact(
                trace, ('', 'raised_wall', 'ground_profile', 'solid_lane', 'upper_lane')[reason],
                V(*a), V(*b), hits[handle], ground_ahead, profile)
            return None
        result = []
        stopped = False
        for a, b, flags in rows:
            if stopped or flags & 4:
                result.append((None, None, 0, a, b))
                if op == 4 and flags & 1:
                    stopped = True
                continue
            start, end = V(*a), V(*b)
            if op == 3:
                hit = module._collide_horizontal(spaceID, start, end, state[0],
                                                 state[1] if flags & 1 else None)
            else:
                collision_filter = state[0]
                if collision_filter is module._UNPREPARED_COLLISION_FILTER:
                    collision_filter = module.ground_collision_filter(a[0], a[2])
                try:
                    hit = module.collide_motion_segment(
                        spaceID, start, end, collision_filter,
                        BigWorld.wg_collideSegment, 'native.motion.ground')
                except (AttributeError, IndexError, TypeError, ValueError):
                    hit = None
            if hit is None:
                result.append((None, None, 0, xyz(start), xyz(end)))
                if op == 4 and flags & 1:
                    stopped = True
            else:
                handle = len(hits) + 1
                hits[handle] = hit
                normal = xyz(hit[1]) if len(hit) > 1 else (0.0, 0.0, 0.0)
                result.append((xyz(hit[0]), normal, handle, xyz(start), xyz(end)))
        return tuple(result)
    # An exception propagates. Replaying the law could repeat destruction.
    status = native_math.world_run(snapshot, dispatch)
    if status is None:
        return None
    if return_status:
        return ('clear', 'hard', 'kinetic')[status]
    return status != 0
