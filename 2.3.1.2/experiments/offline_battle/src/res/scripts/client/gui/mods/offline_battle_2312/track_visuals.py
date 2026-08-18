"""Show and clear the crashed track models the cell normally commands."""
from __future__ import absolute_import

TRACKS = (('leftTrackHealth', True), ('rightTrackHealth', False))


def _track_world_point(vehicle, is_left):
    """The broken track's world point, at the side of the hull."""
    try:
        import Math
        physics = getattr(vehicle.typeDescriptor, 'physics', None)
        offset = (float(physics.get('trackCenterOffset', 1.0))
                  if isinstance(physics, dict) else 1.0)
        matrix = Math.Matrix(vehicle.matrix)
        return matrix.applyPoint(
            Math.Vector3(-offset if is_left else offset, 0.3, 0.0))
    except Exception:
        return None


def ensure_scroll(vehicle, log=None):
    """The stock _startSystems sequence, once: activate, then bind the
    native filter. Without activate the controller ignores setExternal."""
    state = getattr(vehicle, '_offh_scroll_state', None)
    if state is not None:
        return state == 'active'
    appearance = getattr(vehicle, 'appearance', None)
    controller = getattr(appearance, 'trackScrollController', None)
    if controller is None:
        vehicle._offh_scroll_state = 'missing'
        if log is not None:
            log('scroll_controller_missing id=%s' % vehicle.id)
        return False
    try:
        controller.activate()
        entity_filter = vehicle.filter
        controller.setData(getattr(entity_filter, '_filter', entity_filter))
    except Exception as err:
        vehicle._offh_scroll_state = 'failed'
        if log is not None:
            log('scroll_activate_failed id=%s err=%r' % (vehicle.id, err))
        return False
    vehicle._offh_scroll_state = 'active'
    if log is not None:
        log('scroll_active id=%s' % vehicle.id)
    return True


def engine_mode(speed, omega, engine_dead):
    """The kill-cam law: (2, direction bits) moving, (1, 0) idle."""
    if engine_dead:
        return (0, 0)
    if abs(speed) > 0.01 or abs(omega) > 0.01:
        if speed > 0.01:
            direction = 1
        elif speed < -0.01:
            direction = 2
        else:
            direction = 4 if omega > 0.0 else 8
        return (2, direction)
    return (1, 0)


def drive_engine(vehicle, speed, omega, engine_dead):
    """Feed the engine mode the cell normally sends. The activated
    scroll controller only scrolls a running engine."""
    mode = engine_mode(speed, omega, engine_dead)
    if getattr(vehicle, '_offh_engine_mode', None) == mode:
        return
    vehicle._offh_engine_mode = mode
    vehicle.engineMode = mode
    appearance = getattr(vehicle, 'appearance', None)
    if appearance is None:
        return
    try:
        appearance.changeEngineMode(mode)
    except Exception:
        pass


def feed_scroll(vehicle, left, right):
    """setExternal through the stock updateTracksScroll, plus the filter
    attributes the bound controller reads. The filter write must reach
    the native filter: the proxy's __slots__ rejects new attributes."""
    appearance = getattr(vehicle, 'appearance', None)
    if appearance is None:
        return
    try:
        appearance.updateTracksScroll(left, right)
    except Exception:
        pass
    entity_filter = getattr(vehicle, 'filter', None)
    native = getattr(entity_filter, '_filter', entity_filter)
    try:
        native.leftTrackScroll = left
        native.rightTrackScroll = right
    except Exception:
        pass


def refresh(vehicle):
    appearance = getattr(vehicle, 'appearance', None)
    if appearance is None:
        return
    destroyed = getattr(vehicle, '_destroyed_devices', None) or ()
    shown = getattr(vehicle, '_offh_crashed_tracks', None)
    if shown is None:
        shown = set()
        vehicle._offh_crashed_tracks = shown
    for name, is_left in TRACKS:
        broken = name in destroyed
        if broken and name not in shown:
            # The kill-cam entry: stock addCrashedTrack reads the hit
            # point from server-fed extras this battle never sets. The
            # point is world space; the module default anchors the
            # wreck ribbon near the map origin.
            controller = getattr(appearance, 'crashedTracksController', None)
            try:
                pairs = max(1, int(controller.getPairsCnt()))
            except Exception:
                pairs = 1
            in_air = (bool(getattr(appearance, 'isLeftSideFlying', False)),
                      bool(getattr(appearance, 'isRightSideFlying', False)))
            try:
                appearance.addSimulatedCrashedTrack(
                    0 if is_left else pairs, in_air,
                    _track_world_point(vehicle, is_left))
            except Exception:
                continue
            shown.add(name)
        elif not broken and name in shown:
            try:
                appearance.delCrashedTrack(is_left, 0)
            except Exception:
                continue
            shown.discard(name)
