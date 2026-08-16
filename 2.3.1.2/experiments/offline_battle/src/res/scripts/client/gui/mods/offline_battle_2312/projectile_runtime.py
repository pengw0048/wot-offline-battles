"""Projectile stepping helpers, taken from the 0.9.22 port.

The law is unchanged. The 2.3.1.2 inputs reach it through the
adapters in damage.py and the callers in this package.
"""
from __future__ import absolute_import
PROJECTILE_CALLBACK_SECONDS = 0.01
PROJECTILE_MAX_SUBSTEP_SECONDS = 0.025
PROJECTILE_BROADPHASE_RADIUS = 15.0


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
