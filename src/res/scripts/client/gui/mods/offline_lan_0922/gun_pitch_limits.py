from __future__ import print_function

"""#1513 yaw-dependent gun-pitch interpolation without BigWorld."""

import math
import struct


def _float32(value):
    """Round one finite Python number exactly as an x86 ``float``."""
    try:
        result = struct.unpack('<f', struct.pack('<f', float(value)))[0]
    except (TypeError, ValueError, OverflowError, struct.error):
        raise ValueError('gun pitch limit value is not float32')
    if math.isnan(result) or math.isinf(result):
        raise ValueError('gun pitch limit value is not finite')
    return result


def _add(left, right):
    return _float32(_float32(left) + _float32(right))


def _subtract(left, right):
    return _float32(_float32(left) - _float32(right))


def _multiply(left, right):
    return _float32(_float32(left) * _float32(right))


def _divide(left, right):
    denominator = _float32(right)
    if denominator == 0.0:
        raise ValueError('gun pitch limit curve has duplicate yaw nodes')
    return _float32(_float32(left) / denominator)


_TWO_PI = _float32(2.0 * math.pi)


def _curve_points(raw_points):
    try:
        points = tuple(raw_points)
    except (TypeError, ValueError):
        raise ValueError('gun pitch limit curve is not a sequence')
    if len(points) < 2:
        raise ValueError('gun pitch limit curve has fewer than two nodes')
    result = []
    for point in points:
        try:
            if len(point) != 2:
                raise ValueError
            result.append((_float32(point[0]), _float32(point[1])))
        except (TypeError, ValueError, IndexError):
            raise ValueError('gun pitch limit node is not a Vector2')
    return tuple(result)


def _sample_curve(turret_yaw, raw_points):
    points = _curve_points(raw_points)
    lower = 0
    upper = len(points) - 1
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if turret_yaw > points[middle][0]:
            lower = middle
        else:
            upper = middle

    span = _subtract(points[upper][0], points[lower][0])
    fraction = _divide(
        _subtract(turret_yaw, points[lower][0]), span)
    return _add(
        _multiply(points[lower][1], _subtract(1.0, fraction)),
        _multiply(points[upper][1], fraction))


def calc_pitch_limits(turret_yaw, pitch_limits):
    """Mirror #1513 ``wg_calcGunPitchLimits`` for one local turret yaw.

    The native wrapper consumes only ``minPitch`` and ``maxPitch``.  The
    descriptor's ``absolute`` pair belongs to gun-angle serialization and is
    deliberately not a mechanical fallback here.
    """
    yaw = _float32(turret_yaw)
    if yaw < 0.0:
        yaw = _add(yaw, _TWO_PI)
    try:
        minimum_curve = pitch_limits['minPitch']
        maximum_curve = pitch_limits['maxPitch']
    except (KeyError, TypeError):
        raise ValueError('gun pitch limits have no yaw curves')
    return (
        _sample_curve(yaw, minimum_curve),
        _sample_curve(yaw, maximum_curve),
    )
