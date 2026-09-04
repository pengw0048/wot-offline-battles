# -*- coding: utf-8 -*-
"""Engine-free ballistic aiming and trajectory sampling helpers.

Pitch follows the legacy BigWorld convention: negative pitch elevates the
barrel. Gravity may be supplied with either sign and is treated as a downward
magnitude by the scalar ballistic API.
"""

import math


PROJECTILE_MAX_FLIGHT_SECONDS = 20.0


def ballistic_solutions(shooter_position, target_position, projectile_speed,
                        gravity, minimum_pitch=-math.pi * 0.5,
                        maximum_pitch=math.pi * 0.5):
    """Return valid ``(pitch, flight_time)`` roots ordered low arc first."""
    try:
        shooter = tuple(float(value) for value in shooter_position[:3])
        target = tuple(float(value) for value in target_position[:3])
        speed = float(projectile_speed)
        acceleration = abs(float(gravity))
        minimum = float(minimum_pitch)
        maximum = float(maximum_pitch)
    except (TypeError, ValueError, IndexError):
        return ()
    if speed <= 1.0 or acceleration <= 0.01:
        return ()
    delta_x = target[0] - shooter[0]
    delta_z = target[2] - shooter[2]
    horizontal_distance = math.sqrt(
        delta_x * delta_x + delta_z * delta_z)
    if horizontal_distance <= 0.1:
        return ()
    delta_y = target[1] - shooter[1]
    speed_sq = speed * speed
    discriminant = (speed_sq * speed_sq - acceleration *
                    (acceleration * horizontal_distance *
                     horizontal_distance + 2.0 * delta_y * speed_sq))
    if discriminant < 0.0:
        return ()
    root = math.sqrt(max(0.0, discriminant))
    result = []
    for numerator in (speed_sq - root, speed_sq + root):
        elevation = math.atan(
            numerator / (acceleration * horizontal_distance))
        pitch = -elevation
        if pitch < minimum - 0.0001 or pitch > maximum + 0.0001:
            continue
        horizontal_speed = speed * math.cos(elevation)
        if horizontal_speed <= 0.01:
            continue
        flight_time = horizontal_distance / horizontal_speed
        if (flight_time <= 0.0 or
                flight_time > PROJECTILE_MAX_FLIGHT_SECONDS):
            continue
        value = (pitch, flight_time)
        if not result or abs(value[0] - result[-1][0]) > 0.00001:
            result.append(value)
    result.sort(key=lambda value: value[1])
    return tuple(result)


def ballistic_intercept(shooter_position, target_position, target_velocity,
                        projectile_speed, gravity,
                        minimum_pitch=-math.pi * 0.5,
                        maximum_pitch=math.pi * 0.5,
                        prefer_high=False,
                        max_lead_time=PROJECTILE_MAX_FLIGHT_SECONDS):
    """Iteratively lead a constant-velocity target on the real parabola.

    Return ``(aim_point, pitch, flight_time)`` or ``None``. Four fixed-point
    iterations match the mature 0.8.2 law while remaining deterministic.
    """
    try:
        target = tuple(float(value) for value in target_position[:3])
        velocity = tuple(float(value) for value in target_velocity[:3])
        maximum_time = max(0.0, float(max_lead_time))
    except (TypeError, ValueError, IndexError):
        return None
    aim = target
    solution = None
    for unused_iteration in range(4):
        solutions = ballistic_solutions(
            shooter_position, aim, projectile_speed, gravity,
            minimum_pitch, maximum_pitch)
        if not solutions:
            return None
        solution = solutions[-1] if prefer_high else solutions[0]
        if maximum_time and solution[1] > maximum_time + 1e-9:
            return None
        flight_time = solution[1]
        aim = (target[0] + velocity[0] * flight_time,
               target[1] + velocity[1] * flight_time,
               target[2] + velocity[2] * flight_time)
    if solution is None:
        return None
    # Re-solve the final lead coordinate so pitch/time describe the returned
    # point rather than the previous fixed-point iteration.
    solutions = ballistic_solutions(
        shooter_position, aim, projectile_speed, gravity,
        minimum_pitch, maximum_pitch)
    if not solutions:
        return None
    solution = solutions[-1] if prefer_high else solutions[0]
    if maximum_time and solution[1] > maximum_time + 1e-9:
        return None
    return aim, solution[0], solution[1]


def ballistic_position(start_position, yaw, pitch, projectile_speed, gravity,
                       flight_time):
    """Return one absolute point on the shell parabola."""
    start = tuple(float(value) for value in start_position[:3])
    time = max(0.0, float(flight_time))
    horizontal_speed = math.cos(float(pitch)) * float(projectile_speed)
    velocity_x = math.sin(float(yaw)) * horizontal_speed
    velocity_y = -math.sin(float(pitch)) * float(projectile_speed)
    velocity_z = math.cos(float(yaw)) * horizontal_speed
    return (
        start[0] + velocity_x * time,
        start[1] + velocity_y * time -
        0.5 * abs(float(gravity)) * time * time,
        start[2] + velocity_z * time,
    )


def ballistic_path(start_position, yaw, pitch, projectile_speed, gravity,
                   flight_time, maximum_step=0.12):
    """Sample the exact parabola into bounded collision chords."""
    duration = max(0.0, float(flight_time))
    if duration <= 0.0:
        return (tuple(start_position[:3]),)
    step = max(0.02, min(0.25, float(maximum_step)))
    count = max(1, int(math.ceil(duration / step)))
    return tuple(ballistic_position(
        start_position, yaw, pitch, projectile_speed, gravity,
        duration * float(index) / float(count))
        for index in range(count + 1))
