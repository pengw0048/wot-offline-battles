"""Ground probes and the four-point hull pose, taken from the 0.9.22 port.

Every probe uses collision mask 128, which is terrain plus static scene
geometry. A rock is ground the vehicle stands on, not something it sinks
through, so the pose and the horizontal collision law agree on what is
solid.
"""
from __future__ import absolute_import

import math

COLLISION_MASK = 128
NEAR_PROBE_UP = 8.0
NEAR_PROBE_DOWN = 30.0
NEAR_PROBE_MIN_RISE = -14.0
NEAR_PROBE_MAX_RISE = 6.0
SUPPORT_PROBE_UP = 2.0
SUPPORT_PROBE_DOWN = 1000.0
WIDE_PROBE_UP = 1000.0
MIN_HULL_LENGTH = 3.0
MIN_HULL_WIDTH = 2.0
TILT_SCALE = 0.9
MAX_TILT = 0.61
POSE_SMOOTHING = 0.5
DRIVE_PROBE_DISTANCE = 2.0
DRIVE_WALL_GRADIENT = 1.43
MAX_DRIVE_PITCH = 0.96
DRIVE_PITCH_HISTORY = 5
DESCENT_RATE = 15.0
DESCENT_OVERSHOOT = 0.12


def _collide(space_id, x, top, bottom, z):
    import BigWorld
    import Math
    return BigWorld.wg_collideSegment(space_id, Math.Vector3(x, top, z),
                                      Math.Vector3(x, bottom, z),
                                      COLLISION_MASK)


def ground_y(space_id, x, z, hint):
    """Near-hull probe, so a roof far above does not become ground."""
    hit = _collide(space_id, x, hint + NEAR_PROBE_UP,
                   hint - NEAR_PROBE_DOWN, z)
    if hit is None:
        return None
    value = float(hit.closestPoint.y)
    if NEAR_PROBE_MIN_RISE < value - float(hint) < NEAR_PROBE_MAX_RISE:
        return value
    return None


def wide_ground_y(space_id, x, z):
    """Spawn-time probe with no height hint to work from."""
    hit = _collide(space_id, x, WIDE_PROBE_UP, -WIDE_PROBE_UP, z)
    if hit is None:
        return None
    return float(hit.closestPoint.y)


def support(space_id, position, yaw, half_length):
    """(highest, centre) ground under the hull front, centre and back."""
    x, y, z = position
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    highest = None
    centre = None
    for distance in (half_length, 0.0, -half_length):
        hit = _collide(space_id, x + sin_yaw * distance, y + SUPPORT_PROBE_UP,
                       -SUPPORT_PROBE_DOWN, z + cos_yaw * distance)
        if hit is None:
            continue
        value = float(hit.closestPoint.y)
        if highest is None or value > highest:
            highest = value
        if distance == 0.0:
            centre = value
    return highest, centre


def hull_span(descriptor):
    """(length, width) of the hull, floored at a track-width minimum."""
    length, width = MIN_HULL_LENGTH, MIN_HULL_WIDTH
    try:
        bbox = descriptor.hull.hitTester.bbox
        length = max(MIN_HULL_LENGTH,
                     abs(float(bbox[0][2])) + abs(float(bbox[1][2])))
        width = max(MIN_HULL_WIDTH,
                    abs(float(bbox[0][0])) + abs(float(bbox[1][0])))
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return length, width


def pose_angles(front_y, rear_y, right_y, left_y, length, width):
    """Four-point suspension pitch and roll, clamped to a survivable tilt."""
    pitch = -math.atan2(front_y - rear_y, length) * TILT_SCALE
    roll = math.atan2(right_y - left_y, width) * TILT_SCALE
    tilt = math.sqrt(pitch * pitch + roll * roll)
    if tilt > MAX_TILT:
        scale = MAX_TILT / tilt
        pitch *= scale
        roll *= scale
    return pitch, roll


def slope_fall_line(front_y, rear_y, right_y, left_y, length, width, yaw):
    """(downhill x, downhill z, slope tangent) for the copied slide law.

    Taken from the same four-point probe the pose comes from, so the
    fall line and the hull attitude agree."""
    gradient_forward = (rear_y - front_y) / length
    gradient_right = (left_y - right_y) / width
    tangent = math.sqrt(gradient_forward * gradient_forward +
                        gradient_right * gradient_right)
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    downhill_x = gradient_forward * sin_yaw + gradient_right * cos_yaw
    downhill_z = gradient_forward * cos_yaw - gradient_right * sin_yaw
    length_xz = math.sqrt(downhill_x * downhill_x + downhill_z * downhill_z)
    if length_xz > 0.001:
        return downhill_x / length_xz, downhill_z / length_xz, tangent
    return 0.0, 0.0, tangent


def smooth(previous, target):
    return previous + (target - previous) * POSE_SMOOTHING


def climb_limit(speed, dt):
    return max(0.6, abs(speed) * dt * 2.5)


def support_rise_is_obstacle(body_y, support_y, maximum_climb, slop=0.02,
                             maximum_step=0.85):
    """A centre support that rose more than this tick can climb is a step.

    Horizontal integration can leave the hull partly inside a rock, and
    then the centre ray hits its top. That is an obstacle, never ground.
    """
    if body_y is None or support_y is None:
        return False
    rise = float(support_y) - float(body_y)
    limit = min(max(0.0, float(maximum_climb)), max(0.0, float(maximum_step)))
    return rise > limit + max(0.0, float(slop))


def settle(body_y, ground, speed, dt):
    """Rise at the climb limit, descend towards the ground smoothly."""
    max_climb = climb_limit(speed, dt)
    if body_y < ground:
        return body_y + min(ground - body_y, max_climb)
    value = body_y + (ground - body_y) * min(1.0, dt * DESCENT_RATE)
    return min(value, ground + DESCENT_OVERSHOOT)


def drive_pitch(space_id, position, yaw):
    """Close-range drive slope, with walls clamped before they reach the law.

    This is deliberately separate from the visual hull pose: the drive law
    must not read a cliff face as a slope it can climb."""
    x, y, z = position
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    wall_rise = DRIVE_PROBE_DISTANCE * DRIVE_WALL_GRADIENT

    def probe(px, pz):
        top = y + 15.0
        for _unused in range(3):
            hit = _collide(space_id, px, top, y - 60.0, pz)
            if hit is None:
                return None
            value = float(hit.closestPoint.y)
            if value > y + 3.5:
                top = value - 0.5
                continue
            return value
        return None

    front = probe(x + sin_yaw * DRIVE_PROBE_DISTANCE,
                  z + cos_yaw * DRIVE_PROBE_DISTANCE)
    rear = probe(x - sin_yaw * DRIVE_PROBE_DISTANCE,
                 z - cos_yaw * DRIVE_PROBE_DISTANCE)
    if front is None or rear is None:
        return 0.0
    front_delta = max(-wall_rise, min(wall_rise, front - y))
    rear_delta = max(-wall_rise, min(wall_rise, rear - y))
    pitch = -math.atan2(front_delta - rear_delta,
                        2.0 * DRIVE_PROBE_DISTANCE)
    return max(-MAX_DRIVE_PITCH, min(MAX_DRIVE_PITCH, pitch))


def median_pitch(history, raw):
    """Median of the last five drive-pitch samples."""
    history.append(raw)
    del history[:-DRIVE_PITCH_HISTORY]
    return sorted(history)[len(history) // 2]
