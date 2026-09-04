from __future__ import print_function

"""Pure #1513 static-gun and hydraulic hull-aiming rules."""

import math


DISABLED = 0
SWITCHING_ON = 1
ENABLED = 2
SWITCHING_OFF = 3


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _finite(value, label):
    if isinstance(value, bool):
        raise ValueError('%s is not a finite number' % label)
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('%s is not a finite number' % label)
    if math.isnan(value) or math.isinf(value):
        raise ValueError('%s is not a finite number' % label)
    return value


def pitch_params(descriptor):
    """Return validated hydraulic-pitch parameters, or ``None``.

    Native descriptors expose the parser result on ``VehicleType``. Server
    projections retain that same nested surface. ``combinedPitchLimits`` is
    intentionally absent here: it is a derived UI envelope, not a gun angle.
    """
    vehicle_type = _field(descriptor, 'type', {}) or {}
    params = _field(vehicle_type, 'hullAimingParams')
    if params is None:
        params = _field(descriptor, 'hullAimingParams')
    if params is None:
        return None
    pitch = _field(params, 'pitch')
    if pitch is None:
        return None
    angles = _field(pitch, 'wheelsCorrectionAngles')
    if angles is None:
        return None
    minimum = _finite(_field(angles, 'pitchMin'), 'pitchMin')
    maximum = _finite(_field(angles, 'pitchMax'), 'pitchMax')
    speed = _finite(
        _field(pitch, 'wheelsCorrectionSpeed'), 'wheelsCorrectionSpeed')
    center = _finite(
        _field(pitch, 'wheelCorrectionCenterZ', 0.0),
        'wheelCorrectionCenterZ')
    if minimum > maximum or speed < 0.0:
        raise ValueError('hydraulic pitch parameters are invalid')
    return {
        'isAvailable': bool(_field(pitch, 'isAvailable', False)),
        'isEnabled': bool(_field(pitch, 'isEnabled', False)),
        'minimum': minimum,
        'maximum': maximum,
        'speed': speed,
        'centerZ': center,
    }


def absolute_pitch_limits(descriptor):
    """Return the active gun's validated relative pitch envelope."""
    gun = _field(descriptor, 'gun')
    limits = _field(gun, 'pitchLimits')
    absolute = _field(limits, 'absolute', limits)
    try:
        minimum = _finite(absolute[0], 'minimum gun pitch')
        maximum = _finite(absolute[1], 'maximum gun pitch')
    except (IndexError, KeyError, TypeError):
        raise ValueError('gun pitch limits are unavailable')
    if minimum > maximum:
        raise ValueError('gun pitch limits are inverted')
    return minimum, maximum


def siege_switching(siege_state):
    return int(siege_state) in (SWITCHING_ON, SWITCHING_OFF)


def static_yaw_locked(static_yaw, engine_destroyed=False,
                      track_destroyed=False, overturned=False,
                      moving=False, siege_state=DISABLED):
    """Mirror ``VehicleGunRotator.__getTurretYawLimits`` in #1513."""
    return bool(
        static_yaw is not None and
        (engine_destroyed or track_destroyed or overturned or moving or
         siege_switching(siege_state)))


def static_pitch_locked(static_pitch, engine_destroyed=False,
                        overturned=False, siege_state=DISABLED):
    """Mirror ``VehicleGunRotator.__getGunPitchLimits`` in #1513."""
    return bool(
        static_pitch is not None and
        (engine_destroyed or overturned or siege_switching(siege_state)))


def classify_pitch(pitch, minimum, maximum, tolerance=1.0e-9):
    """Classify a relative pitch below, inside, or above one gun envelope."""
    pitch = _finite(pitch, 'pitch')
    minimum = _finite(minimum, 'minimum pitch')
    maximum = _finite(maximum, 'maximum pitch')
    tolerance = max(0.0, _finite(tolerance, 'pitch tolerance'))
    if minimum > maximum:
        raise ValueError('gun pitch limits are inverted')
    if pitch < minimum - tolerance:
        return -1
    if pitch > maximum + tolerance:
        return 1
    return 0


def minimal_correction(total_pitch, gun_minimum, gun_maximum,
                       correction_minimum, correction_maximum):
    """Return the closest-to-zero flat-pose hydraulic correction.

    The returned second value says whether the requested total pitch is in the
    physical combined envelope. The gun still retains its ordinary relative
    pitch limits; the correction is a separate chassis angle.
    """
    total_pitch = _finite(total_pitch, 'total pitch')
    gun_minimum = _finite(gun_minimum, 'gun minimum')
    gun_maximum = _finite(gun_maximum, 'gun maximum')
    correction_minimum = _finite(
        correction_minimum, 'correction minimum')
    correction_maximum = _finite(
        correction_maximum, 'correction maximum')
    if (gun_minimum > gun_maximum or
            correction_minimum > correction_maximum):
        raise ValueError('pitch limits are inverted')
    if total_pitch < gun_minimum:
        required = total_pitch - gun_minimum
    elif total_pitch > gun_maximum:
        required = total_pitch - gun_maximum
    else:
        required = 0.0
    reachable = (correction_minimum - 1.0e-9 <= required <=
                 correction_maximum + 1.0e-9)
    return (max(correction_minimum, min(correction_maximum, required)),
            reachable)


def world_target_correction(world_pitch, terrain_pitch,
                            gun_minimum, gun_maximum,
                            correction_minimum, correction_maximum):
    """Resolve one world target against gun travel and hydraulic travel.

    ``world_pitch`` and ``terrain_pitch`` use #1513's negative-up convention.
    The result is the chassis correction nearest zero, so the gun consumes its
    ordinary relative travel before the hydraulic suspension moves.
    """
    world_pitch = _finite(world_pitch, 'world target pitch')
    terrain_pitch = _finite(terrain_pitch, 'terrain pitch')
    relative_pitch = ((world_pitch - terrain_pitch + math.pi) %
                      (2.0 * math.pi) - math.pi)
    return minimal_correction(
        relative_pitch, gun_minimum, gun_maximum,
        correction_minimum, correction_maximum)


def slew(current, desired, speed, elapsed):
    current = _finite(current, 'current correction')
    desired = _finite(desired, 'desired correction')
    maximum_step = max(0.0, _finite(speed, 'correction speed')) * max(
        0.0, _finite(elapsed, 'elapsed time'))
    difference = desired - current
    if difference > maximum_step:
        return current + maximum_step
    if difference < -maximum_step:
        return current - maximum_step
    return desired


def gun_pitch_step(current, desired, static_pitch, maximum_speed,
                   elapsed, turret_rotation_time=0.0, angle_limits=None):
    """Mirror #1513 static-pitch crossing and turret coordination."""
    current = _finite(current, 'current gun pitch')
    desired = _finite(desired, 'desired gun pitch')
    elapsed = max(0.0, _finite(elapsed, 'elapsed time'))
    maximum_speed = max(
        0.0, _finite(maximum_speed, 'maximum gun speed'))
    if maximum_speed == 0.0:
        return current
    bounded_desired = desired
    if angle_limits is not None:
        minimum, maximum = angle_limits
        minimum = _finite(minimum, 'minimum gun pitch')
        maximum = _finite(maximum, 'maximum gun pitch')
        if minimum > maximum:
            raise ValueError('gun pitch limits are inverted')
        bounded_desired = max(minimum, min(maximum, desired))
    if abs(current - desired) < 1.0e-6:
        return bounded_desired
    difference = bounded_desired - current
    speed_limit = maximum_speed * elapsed
    if static_pitch is not None:
        static_pitch = _finite(static_pitch, 'static pitch')
        if difference * (current - static_pitch) < 0.0:
            speed_limit *= 2.0
        turret_rotation_time = max(
            0.0, _finite(turret_rotation_time, 'turret rotation time'))
        if turret_rotation_time > 0.0:
            ideal_speed = abs(difference) / turret_rotation_time
            speed_limit = min(speed_limit, ideal_speed * elapsed)
    if difference > speed_limit:
        return current + speed_limit
    if difference < -speed_limit:
        return current - speed_limit
    return bounded_desired
