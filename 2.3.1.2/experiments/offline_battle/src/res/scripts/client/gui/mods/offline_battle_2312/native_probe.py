"""Find out what the native vehicle filter and physics can still do offline.

Input reaches notifyInputKeysDown but the native simulation emits no pose.
The first probe run captured both method tables and showed the filter with
zero track contacts, placingOnGround false and lag detection latched. This
run reads the physics state one attribute at a time, so a native fault
names the attribute, then tries the levers that could wake the body.
"""
from __future__ import absolute_import

# Read one at a time: a faulting getter must name itself in the log.
# Proven safe on 2.3.1.2; speed and the contact getters fault while the
# physics body is unbound.
PHYSICS_READS = (
    'vehicleID', 'isFrozen', 'allowFreeze', 'staticMode', 'movementSignals',
    'cruiseSignals')


def _position(vehicle):
    position = vehicle.position
    return (round(position.x, 3), round(position.y, 3), round(position.z, 3))


def _read_physics_state(log, physics):
    for name in PHYSICS_READS:
        log('physics_read name=%s' % (name,))
        try:
            value = getattr(physics, name)
        except Exception as error:
            log('physics_read name=%s error=%s' % (name, type(error).__name__))
            continue
        if callable(value):
            try:
                value = value()
            except Exception as error:
                log('physics_read name=%s call_error=%s'
                    % (name, type(error).__name__))
                continue
        text = repr(value)
        log('physics_read name=%s value=%s' % (name, text[:60]))


def run_step(log, vehicle, physics, step):
    """One experiment per call. Returns True while steps remain."""
    entity_filter = vehicle.filter
    before = _position(vehicle)

    if physics is None:
        log('native_try result=no_physics_captured')
        return False

    if step == 0:
        _read_physics_state(log, physics)
        return True
    if step == 1:
        log('native_try name=bind_vehicle_id current=%s target=%s'
            % (physics.vehicleID, vehicle.id))
        physics.vehicleID = vehicle.id
        log('native_try name=bind_vehicle_id after=%s' % (physics.vehicleID,))
        return True
    if step == 2:
        physics.allowFreeze = False
        log('native_try name=allow_freeze_off frozen=%s allow=%s'
            % (physics.isFrozen, physics.allowFreeze))
        return True
    if step == 3:
        entity_filter.notifyInputKeysDown(1, 0)
        log('native_try name=drive_after_bind pos=%s signals=%s cruise=%s'
            % (_position(vehicle), physics.movementSignals,
               physics.cruiseSignals))
        return True
    if step == 4:
        log('native_try name=set_signal_direct before_signals=%s'
            % (physics.movementSignals,))
        physics.movementSignals = 1
        log('native_try name=set_signal_direct after_signals=%s pos=%s'
            % (physics.movementSignals, _position(vehicle)))
        return True
    if step == 5:
        log('native_try name=after_signal pos=%s left=%s right=%s'
            % (_position(vehicle), entity_filter.numLeftTrackContacts,
               entity_filter.numRightTrackContacts))
        return True
    if step == 6:
        log('native_try name=touch_ground before=%s' % (before,))
        physics.touchGround()
        log('native_try name=touch_ground after=%s' % (_position(vehicle),))
        return True
    if step == 7:
        log('native_try name=settled pos=%s velocity=%s lagging=%s'
            % (_position(vehicle), entity_filter.velocity,
               entity_filter.isLaggingNow))
        return False
    return False
