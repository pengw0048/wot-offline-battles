# -*- coding: utf-8 -*-
"""Engine-free swept-motion helpers for in-flight projectiles.

All vectors are plain three-component sequences. The live adapter remains
responsible for clocks, BigWorld collision queries, and vehicle matrices.
"""

import math

PROJECTILE_CALLBACK_SECONDS = 0.01
PROJECTILE_MAX_SUBSTEP_SECONDS = 0.05
PROJECTILE_MAX_CHORD_ERROR_METERS = 0.05
PROJECTILE_BROADPHASE_RADIUS = 15.0


def projectile_range_distance(state, point):
    """Return straight 3-D range from one frozen range origin.

    The pinned #1513 client marker evaluates penetration falloff with a
    Euclidean point-to-point distance.  New projectile payloads freeze the
    source vehicle position as ``range_origin``; older states fall back to the
    muzzle in ``start``.  This deliberately does not replace the manager's
    travelled path distance.
    """
    payload = state.get('payload') or {}
    origin = (payload['range_origin']
              if 'range_origin' in payload else state['start'])
    delta_x = float(point[0]) - float(origin[0])
    delta_y = float(point[1]) - float(origin[1])
    delta_z = float(point[2]) - float(origin[2])
    return math.sqrt(
        delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)


def ideal_reflection_velocity(incoming_velocity, surface_normal):
    """Reflect a finite 3-D velocity around a normalized surface normal.

    Returns ``None`` when either input is not exactly three finite components
    or when the normal is numerically degenerate.  The returned vector follows
    ``v - 2 * dot(v, n) * n`` and therefore preserves the incoming speed.
    """
    try:
        if len(incoming_velocity) != 3 or len(surface_normal) != 3:
            return None
        velocity = tuple(float(value) for value in incoming_velocity)
        normal = tuple(float(value) for value in surface_normal)
    except (TypeError, ValueError, OverflowError):
        return None
    if any(math.isnan(value) or math.isinf(value)
           for value in velocity + normal):
        return None
    normal_length_sq = sum(value * value for value in normal)
    if (math.isnan(normal_length_sq) or math.isinf(normal_length_sq) or
            normal_length_sq <= 1.0e-24):
        return None
    inverse_length = 1.0 / math.sqrt(normal_length_sq)
    unit_normal = tuple(value * inverse_length for value in normal)
    projection = sum(
        velocity[index] * unit_normal[index] for index in range(3))
    reflected = tuple(
        velocity[index] - 2.0 * projection * unit_normal[index]
        for index in range(3))
    if any(math.isnan(value) or math.isinf(value) for value in reflected):
        return None
    return reflected


def trajectory_position(start, velocity, gravity, elapsed):
    """Return ``r0 + v0*t + 1/2*g*t^2`` as a plain three-tuple."""
    time = max(0.0, float(elapsed or 0.0))
    half_time_sq = 0.5 * time * time
    return (
        float(start[0]) + float(velocity[0]) * time +
        float(gravity[0]) * half_time_sq,
        float(start[1]) + float(velocity[1]) * time +
        float(gravity[1]) * half_time_sq,
        float(start[2]) + float(velocity[2]) * time +
        float(gravity[2]) * half_time_sq,
    )


def lerp3(first, second, fraction):
    """Linearly interpolate two plain three-component positions."""
    value = max(0.0, min(1.0, float(fraction or 0.0)))
    return (
        float(first[0]) + (float(second[0]) - float(first[0])) * value,
        float(first[1]) + (float(second[1]) - float(first[1])) * value,
        float(first[2]) + (float(second[2]) - float(first[2])) * value,
    )


def compensate_segment_for_moving_target(projectile_start, projectile_end,
                                         target_previous, target_current,
                                         interval_start=0.0,
                                         interval_end=1.0):
    """Express a projectile chord in the target's current collision frame.

    A vehicle hit tester sees its current matrix. Moving each chord endpoint by
    the target displacement remaining at that endpoint converts the query into
    a relative-motion sweep without requiring an engine-owned historic matrix.
    """
    previous_at_start = lerp3(
        target_previous, target_current, interval_start)
    previous_at_end = lerp3(
        target_previous, target_current, interval_end)
    return (
        (
            float(projectile_start[0]) + float(target_current[0]) -
            previous_at_start[0],
            float(projectile_start[1]) + float(target_current[1]) -
            previous_at_start[1],
            float(projectile_start[2]) + float(target_current[2]) -
            previous_at_start[2],
        ),
        (
            float(projectile_end[0]) + float(target_current[0]) -
            previous_at_end[0],
            float(projectile_end[1]) + float(target_current[1]) -
            previous_at_end[1],
            float(projectile_end[2]) + float(target_current[2]) -
            previous_at_end[2],
        ),
    )


def point_segment_distance_sq(point, start, end):
    """Return squared 3-D distance from a point to a finite segment."""
    segment_x = float(end[0]) - float(start[0])
    segment_y = float(end[1]) - float(start[1])
    segment_z = float(end[2]) - float(start[2])
    point_x = float(point[0]) - float(start[0])
    point_y = float(point[1]) - float(start[1])
    point_z = float(point[2]) - float(start[2])
    denominator = (segment_x * segment_x + segment_y * segment_y +
                   segment_z * segment_z)
    if denominator <= 1e-12:
        return point_x * point_x + point_y * point_y + point_z * point_z
    fraction = (point_x * segment_x + point_y * segment_y +
                point_z * segment_z) / denominator
    fraction = max(0.0, min(1.0, fraction))
    delta_x = point_x - segment_x * fraction
    delta_y = point_y - segment_y * fraction
    delta_z = point_z - segment_z * fraction
    return delta_x * delta_x + delta_y * delta_y + delta_z * delta_z


def point_in_expanded_segment_bounds(point, start, end, radius):
    """Return whether ``point`` is inside a segment's expanded AABB.

    This is a conservative broad phase: a point within ``radius`` of the
    finite segment must pass it, while distant records avoid the more costly
    point-to-segment projection and native entity lookup.
    """
    radius = max(0.0, float(radius or 0.0))
    for index in range(3):
        value = float(point[index])
        lower = min(float(start[index]), float(end[index])) - radius
        upper = max(float(start[index]), float(end[index])) + radius
        if value < lower or value > upper:
            return False
    return True


def parabolic_chord_error(gravity, duration):
    """Return the maximum sagitta of a constant-gravity trajectory chord."""
    try:
        gravity_magnitude = math.sqrt(sum(
            float(value) * float(value) for value in gravity))
    except TypeError:
        gravity_magnitude = abs(float(gravity or 0.0))
    duration = max(0.0, float(duration or 0.0))
    return gravity_magnitude * duration * duration / 8.0


def curvature_limited_substep(
        gravity, maximum_step=PROJECTILE_MAX_SUBSTEP_SECONDS,
        maximum_error=PROJECTILE_MAX_CHORD_ERROR_METERS):
    """Choose the longest chord that keeps parabolic sagitta bounded."""
    maximum_step = max(0.001, float(
        maximum_step or PROJECTILE_MAX_SUBSTEP_SECONDS))
    maximum_error = max(1e-9, float(
        maximum_error or PROJECTILE_MAX_CHORD_ERROR_METERS))
    try:
        gravity_magnitude = math.sqrt(sum(
            float(value) * float(value) for value in gravity))
    except TypeError:
        gravity_magnitude = abs(float(gravity or 0.0))
    if gravity_magnitude <= 1e-12:
        return maximum_step
    error_limited = math.sqrt(8.0 * maximum_error / gravity_magnitude)
    return max(0.001, min(maximum_step, error_limited))


def substep_boundaries(start_time, end_time,
                       maximum_step=PROJECTILE_MAX_SUBSTEP_SECONDS):
    """Yield bounded absolute-time chords without dropping a slow frame."""
    start_time = max(0.0, float(start_time or 0.0))
    end_time = max(start_time, float(end_time or 0.0))
    maximum_step = max(
        0.001, float(maximum_step or PROJECTILE_MAX_SUBSTEP_SECONDS))
    cursor = start_time
    while cursor + 1e-9 < end_time:
        next_time = min(end_time, cursor + maximum_step)
        yield cursor, next_time
        cursor = next_time
