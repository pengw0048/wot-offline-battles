"""Publish the targeting parameters the cell normally sends.

VehicleGunRotator.start() refuses to run until `update()` has supplied the
turret and gun rotation speeds, and only `Avatar.updateTargetingInfo`
calls it. That is a cell-to-client method, so offline the rotator never
ticks and the aim never moves. Every value below comes from the real
vehicle descriptor, which is where the cell reads them too.
"""
from __future__ import absolute_import

DEFAULT_DISPERSION_FACTOR = 1.0


def _factor(mapping, key, fallback=0.0):
    try:
        value = mapping[key]
    except (KeyError, TypeError):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def targeting_info(descriptor, vehicle_id):
    """Arguments for Avatar.updateTargetingInfo, in its own order."""
    gun = descriptor.gun
    turret = descriptor.turret
    gun_factors = getattr(gun, 'shotDispersionFactors', {}) or {}
    chassis_factors = getattr(descriptor.chassis, 'shotDispersionFactors',
                              {}) or {}
    return (
        vehicle_id,
        0.0,
        0.0,
        float(turret.rotationSpeed),
        float(gun.rotationSpeed),
        DEFAULT_DISPERSION_FACTOR,
        _factor(gun_factors, 'turretRotation'),
        _factor(chassis_factors, 'movement'),
        _factor(chassis_factors, 'rotation'),
        _factor(gun_factors, 'afterShot'),
        float(getattr(gun, 'aimingTime', 2.0)),
    )


def publish(log, avatar, vehicle):
    arguments = targeting_info(vehicle.typeDescriptor, vehicle.id)
    avatar.updateTargetingInfo(*arguments)
    rotator = avatar.gunRotator
    log('targeting_published turret_speed=%.3f gun_speed=%.3f aiming=%.2f '
        'rotator_started=%s'
        % (arguments[3], arguments[4], arguments[10],
           getattr(rotator, '_VehicleGunRotator__isStarted', None)))
