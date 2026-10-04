"""Hull-height water thresholds shared by visible and authority clients.

The product rule follows the user's retail-server observation: caution above
half of the actual hull, danger above its top. The turret mount and native
splash sensor do not define either threshold.
"""
from __future__ import division

import math

from gui.mods.offline_lan_0922 import shot_geometry


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name)


def _vector(value):
    result = tuple(float(value[index]) for index in range(3))
    if any(math.isnan(number) or math.isinf(number) for number in result):
        raise ValueError('hull water geometry is not finite')
    return result


def hull_sample(descriptor, position, yaw=0.0, pitch=0.0, roll=0.0):
    """Return a world bottom-plane probe and the posed hull's height.

    The hit tester is hull-local; apply the chassis mount before the complete
    vehicle rotation. All eight corners contribute to the world-up bounds,
    including when the hull pitches, rolls or overturns. Probe at the bottom
    plane so negative water-query sentinels cannot hide a submerged inverted
    hull whose entire body lies below the vehicle origin.

    Missing or unloaded geometry has no threshold. It must not acquire a
    guessed universal height while native descriptors are being rebuilt.
    """
    try:
        hull = _field(descriptor, 'hull')
        bbox = _field(_field(hull, 'hitTester'), 'bbox')
        minimum, maximum = _vector(bbox[0]), _vector(bbox[1])
        mount = _vector(_field(_field(descriptor, 'chassis'), 'hullPosition'))
        if any(maximum[index] <= minimum[index] for index in range(3)):
            return None
        position = _vector(position)
        angles = _vector((yaw, pitch, roll))
        corners = [shot_geometry.transform_vehicle_point(
            (x_value + mount[0], y_value + mount[1], z_value + mount[2]),
            position, *angles)
            for x_value in (minimum[0], maximum[0])
            for y_value in (minimum[1], maximum[1])
            for z_value in (minimum[2], maximum[2])]
        bottom = min(point[1] for point in corners)
        top = max(point[1] for point in corners)
        centre = tuple(sum(point[index] for point in corners) / 8.0
                       for index in range(3))
        height = top - bottom
        if height <= 0.0 or math.isnan(height) or math.isinf(height):
            return None
        return (centre[0], bottom, centre[2]), height
    except (AttributeError, IndexError, KeyError, TypeError, ValueError,
            OverflowError, RuntimeError):
        return None


def warning_level(depth_above_bottom, hull_height):
    """Classify a native water depth at ``hull_sample``'s bottom plane."""
    try:
        depth, height = float(depth_above_bottom), float(hull_height)
    except (TypeError, ValueError, OverflowError):
        return None
    if (math.isnan(depth) or math.isinf(depth) or
            math.isnan(height) or math.isinf(height) or height <= 0.0):
        return None
    if depth > height:
        return 2
    return 1 if depth > height * 0.5 else 0
