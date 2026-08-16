"""Horizontal world-collision law, taken from the 0.9.22 port.

The 2.3.1.2 collision result is an object with `closestPoint` and
`normal` instead of the 0.9.22 tuple. Destruction is not ported yet, so
every solid hit that is not drivable ground stops the hull.
"""
from __future__ import absolute_import

import math

COLLISION_MASK = 128
MAX_DRIVABLE_GRADIENT = 1.28
MAX_DESCENDING_GRADIENT = 1.75
MIN_DRIVABLE_HEIGHT_CHANGE = 0.15
LOWER_RAY_HEIGHT = 0.6
UPPER_RAY_HEIGHTS = (1.1, 1.6)
CORNER_RAY_HEIGHTS = (0.6, 1.1)
CORNER_MARGIN = 0.05
SLIDE_YAWS = (0.55, -0.55, 1.0, -1.0)
SLIDE_FIRST_FACTOR = 0.6
SLIDE_DECAY = 0.85
STOP_DECAY = 0.35
STOP_SPEED = 0.05
DEFAULT_HALF_WIDTH = 1.5
DEFAULT_HALF_LENGTH = 3.5


def hull_extents(descriptor):
    """(half width, front length, back length) from the hull hit tester."""
    half_width = DEFAULT_HALF_WIDTH
    front = back = DEFAULT_HALF_LENGTH
    try:
        bbox = descriptor.hull.hitTester.bbox
        half_width = max(abs(bbox[0][0]), abs(bbox[1][0])) - 0.1
        back = abs(bbox[0][2])
        front = abs(bbox[1][2])
    except (AttributeError, IndexError, KeyError, TypeError):
        return DEFAULT_HALF_WIDTH, DEFAULT_HALF_LENGTH, DEFAULT_HALF_LENGTH
    return half_width, front, back


def _profile_gradient_limit(heights):
    try:
        if float(heights[-1]) < float(heights[0]):
            return MAX_DESCENDING_GRADIENT
        return MAX_DRIVABLE_GRADIENT
    except (IndexError, TypeError, ValueError):
        return MAX_DRIVABLE_GRADIENT


def _drivable_ground_profile(heights, segment_length):
    """A continuous, bounded slope in either travel direction.

    A flat profile is not terrain evidence, so a wall on level ground
    still reaches the solid path."""
    try:
        values = [float(value) for value in heights]
    except (TypeError, ValueError):
        return False
    if len(values) < 2:
        return False
    if abs(values[-1] - values[0]) <= MIN_DRIVABLE_HEIGHT_CHANGE:
        return False
    segment = max(0.001, float(segment_length))
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        limit = (MAX_DESCENDING_GRADIENT if delta < 0.0
                 else MAX_DRIVABLE_GRADIENT)
        if abs(delta) > segment * limit:
            return False
    return True


def _drivable_surface(collision, maximum_gradient=MAX_DRIVABLE_GRADIENT):
    """The hit surface itself must be a slope, not merely near one."""
    try:
        normal = collision.normal
        length = (normal.x * normal.x + normal.y * normal.y +
                  normal.z * normal.z) ** 0.5
        if length <= 0.0:
            return False
        minimum_y = 1.0 / (1.0 + float(maximum_gradient) ** 2) ** 0.5
        return normal.y / length >= minimum_y
    except (AttributeError, TypeError, ZeroDivisionError):
        return False


def _ground_profile(space_id, pos_y, sx, sz, sin_y, cos_y, direction, look,
                    segment_count=6):
    """Sample the lane that produced a lower-hull hit."""
    import BigWorld
    import Math
    segment = look / float(segment_count)
    probe_down = max(5.0, float(look) * MAX_DESCENDING_GRADIENT + 1.0)
    heights = []
    for index in range(segment_count + 1):
        distance = segment * index
        x = sx + sin_y * distance * direction
        z = sz + cos_y * distance * direction
        ground = BigWorld.wg_collideSegment(
            space_id, Math.Vector3(x, pos_y + 12.0, z),
            Math.Vector3(x, pos_y - probe_down, z), COLLISION_MASK)
        if ground is None:
            return (), segment
        heights.append(ground.closestPoint.y)
    return heights, segment


def _raised_ray_has_wall(space_id, pos_y, x1, z1, x2, z2, target_length,
                         maximum_gradient=MAX_DRIVABLE_GRADIENT):
    """A drivable lower slope must not hide a wall above it."""
    import BigWorld
    import Math
    for height in UPPER_RAY_HEIGHTS:
        start = Math.Vector3(x1, pos_y + height, z1)
        end = Math.Vector3(x2, pos_y + height, z2)
        collision = BigWorld.wg_collideSegment(space_id, start, end,
                                               COLLISION_MASK)
        if collision is None:
            continue
        if ((collision.closestPoint - start).length < target_length and
                not _drivable_surface(collision, maximum_gradient)):
            return True
    return False


def _upper_ray_hits(space_id, pos_y, x1, z1, x2, z2, target_length):
    import BigWorld
    import Math
    for height in UPPER_RAY_HEIGHTS:
        start = Math.Vector3(x1, pos_y + height, z1)
        end = Math.Vector3(x2, pos_y + height, z2)
        collision = BigWorld.wg_collideSegment(space_id, start, end,
                                               COLLISION_MASK)
        if collision is None:
            continue
        if (collision.closestPoint - start).length < target_length:
            return True
    return False


def hull_contacts(space_id, position, yaw, extents):
    """Count hull corners that sit behind solid geometry.

    The forward sweep only covers the travel direction, so a hull that
    turns beside a rock walks into it sideways. A ray from the hull
    centre outwards starts in open space, which a ray from inside the
    rock would not."""
    import BigWorld
    import Math
    half_width, hull_front, hull_back = extents
    pos_x, pos_y, pos_z = position
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    contacts = 0
    for offset_x, offset_z in ((half_width, hull_front),
                               (-half_width, hull_front),
                               (half_width, -hull_back),
                               (-half_width, -hull_back)):
        x = pos_x + sin_y * offset_z + cos_y * offset_x
        z = pos_z + cos_y * offset_z - sin_y * offset_x
        reach = (offset_x * offset_x + offset_z * offset_z) ** 0.5
        for height in CORNER_RAY_HEIGHTS:
            start = Math.Vector3(pos_x, pos_y + height, pos_z)
            end = Math.Vector3(x, pos_y + height, z)
            collision = BigWorld.wg_collideSegment(space_id, start, end,
                                                   COLLISION_MASK)
            if collision is None:
                continue
            if (collision.closestPoint - start).length < reach - CORNER_MARGIN:
                contacts += 1
                break
    return contacts


def blocked(space_id, position, yaw, velocity, extents, dt, airborne=False):
    """True when solid geometry stops this frame's translation."""
    import BigWorld
    import Math
    half_width, hull_front, hull_back = extents
    pos_x, pos_y, pos_z = position
    if airborne:
        ahead = abs(velocity) * dt + 0.2
    else:
        ahead = max(0.4, abs(velocity) * dt + 0.2)
    back_margin = -0.5 if velocity > 0 else 0.5
    front_margin = ((hull_front + ahead) if velocity > 0
                    else -(hull_back + ahead))
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    target_length = (abs(back_margin) +
                     (hull_front if velocity > 0 else hull_back) + ahead)

    for offset_x in (-half_width, 0.0, half_width):
        sx = pos_x + cos_y * offset_x
        sz = pos_z - sin_y * offset_x
        x1 = sx + sin_y * back_margin
        z1 = sz + cos_y * back_margin
        x2 = sx + sin_y * front_margin
        z2 = sz + cos_y * front_margin

        start = Math.Vector3(x1, pos_y + LOWER_RAY_HEIGHT, z1)
        end = Math.Vector3(x2, pos_y + LOWER_RAY_HEIGHT, z2)
        lower = BigWorld.wg_collideSegment(space_id, start, end,
                                           COLLISION_MASK)
        if lower is not None:
            distance = (lower.closestPoint - start).length
            if distance < target_length:
                direction = 1.0 if velocity >= 0 else -1.0
                look = (hull_front if velocity > 0 else hull_back) + ahead
                heights = ()
                segment = 0.0
                limit = MAX_DESCENDING_GRADIENT
                if _drivable_surface(lower, limit):
                    heights, segment = _ground_profile(
                        space_id, pos_y, sx, sz, sin_y, cos_y, direction,
                        look)
                    limit = _profile_gradient_limit(heights)
                    if (heights and
                            abs(float(heights[-1]) - float(heights[0])) >
                            MIN_DRIVABLE_HEIGHT_CHANGE and
                            not _drivable_ground_profile(heights, segment)):
                        return True
                if (heights and _drivable_ground_profile(heights, segment) and
                        _drivable_surface(lower, limit)):
                    if _raised_ray_has_wall(space_id, pos_y, x1, z1, x2, z2,
                                            target_length, limit):
                        return True
                    continue
                return True
        if _upper_ray_hits(space_id, pos_y, x1, z1, x2, z2, target_length):
            return True
    return False
