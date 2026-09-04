from __future__ import print_function

"""Pinned #1513 Siege-mode contracts shared by players and authority bots."""


DISABLED = 0
SWITCHING_ON = 1
ENABLED = 2
SWITCHING_OFF = 3
STATES = (DISABLED, SWITCHING_ON, ENABLED, SWITCHING_OFF)

# Exact Chinese HD #1513 values from the stock vehicle and paired
# ``*_siege_mode.xml`` definitions. Values are switch-on seconds,
# switch-off seconds, enabled-mode metres/second and damaged-engine factor.
VEHICLE_PARAMS = {
    'sweden:S10_Strv_103_0_Series': (2.0, 1.3, 10.0 / 3.6, 2.0),
    'sweden:S11_Strv_103B': (2.0, 1.3, 10.0 / 3.6, 2.0),
    'sweden:S21_UDES_03': (2.0, 2.0, 5.0 / 3.6, 2.0),
    'sweden:S22_Strv_S1': (2.0, 1.3, 8.0 / 3.6, 2.0),
}


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def vehicle_name(descriptor_or_name):
    """Return the canonical nation-qualified vehicle type name."""
    if isinstance(descriptor_or_name, str):
        return descriptor_or_name
    try:
        text_types = (unicode,)
    except NameError:
        text_types = ()
    if isinstance(descriptor_or_name, text_types):
        return descriptor_or_name
    vehicle_type = _field(descriptor_or_name, 'type', {}) or {}
    return str(_field(vehicle_type, 'name', '') or '')


def params(descriptor_or_name):
    """Return the exact transition and speed tuple, or ``None``."""
    return VEHICLE_PARAMS.get(vehicle_name(descriptor_or_name))


def descriptor_pair(descriptor):
    """Return this bot's immutable ``(travel, siege)`` descriptor pair.

    Native #1513 composites publicly expose ``defaultVehicleDescr`` and
    ``siegeVehicleDescr``. Engine-free server projections carry the same pair
    as a top-level travel projection plus ``siege_descriptor``. No caller
    mutates either member; changing mode replaces only the bot-local active
    reference.
    """
    default_descriptor = _field(
        descriptor, 'defaultVehicleDescr', descriptor)
    siege_descriptor = _field(descriptor, 'siegeVehicleDescr')
    if siege_descriptor is None:
        siege_descriptor = _field(descriptor, 'siege_descriptor')
    has_mode = bool(_field(descriptor, 'hasSiegeMode', False))
    if not has_mode:
        has_mode = params(default_descriptor) is not None
    if not has_mode:
        return default_descriptor, None
    if siege_descriptor is None:
        raise ValueError(
            '#1513 Siege vehicle has no mode-specific descriptor')
    return default_descriptor, siege_descriptor


def active_descriptor(pair, state):
    """Select #1513's active descriptor for one of the four wire states."""
    if state not in STATES:
        raise ValueError('invalid Siege state')
    travel, siege = pair
    if state == ENABLED:
        if siege is None:
            raise ValueError('non-Siege vehicle cannot enter Siege mode')
        return siege
    # Exact #1513 returns to the travel descriptor on the SWITCHING_OFF edge;
    # SWITCHING_ON also remains on travel until the final ENABLED edge.
    return travel


def transition_seconds(descriptor_or_name, enabling, engine_damaged=False):
    """Return the pinned transition duration for a legal mode request."""
    values = params(descriptor_or_name)
    if values is None:
        raise ValueError('vehicle has no Siege mode')
    duration = float(values[0] if enabling else values[1])
    if engine_damaged:
        duration *= float(values[3])
    return duration


def enabled_speed_limit(descriptor_or_name):
    values = params(descriptor_or_name)
    return None if values is None else float(values[2])


def request_transition(state, time_left, transition_total,
                       descriptor_or_name, enabled,
                       engine_damaged=False):
    """Apply one native #1513 Siege request, including a reversal.

    ``time_left`` and ``transition_total`` use an arbitrary shared unit.  The
    returned values use the same unit as the vehicle durations, so callers use
    seconds internally and convert to ticks or milliseconds at their boundary.

    Reversing a transition preserves the physical suspension progress.  The
    total of the transition being reversed is explicit because engine damage
    may have changed since that transition began.
    """
    if state not in STATES or not isinstance(enabled, bool):
        raise ValueError('invalid Siege request')
    if params(descriptor_or_name) is None:
        raise ValueError('vehicle has no Siege mode')
    remaining = max(0.0, float(time_left))
    total = max(0.0, float(transition_total))
    desired_state = ENABLED if enabled else DISABLED
    desired_switch = SWITCHING_ON if enabled else SWITCHING_OFF

    if state == desired_state:
        return state, 0.0, 0.0, False
    if state in (DISABLED, ENABLED):
        duration = transition_seconds(
            descriptor_or_name, enabled, engine_damaged)
        return desired_switch, duration, duration, True

    same_direction = (
        (state == SWITCHING_ON and enabled) or
        (state == SWITCHING_OFF and not enabled))
    if same_direction:
        return state, remaining, total, False

    if total <= 0.0:
        raise ValueError('Siege transition total is missing')
    remaining = min(remaining, total)
    progress = max(0.0, min(1.0, (total - remaining) / total))
    reverse_total = transition_seconds(
        descriptor_or_name, enabled, engine_damaged)
    reverse_remaining = progress * reverse_total
    if reverse_remaining <= 1.0e-9:
        return desired_state, 0.0, 0.0, True
    return (desired_switch, reverse_remaining, reverse_total, True)


def valid_wire_state(state, time_left_ms, descriptor_or_name=None,
                     transition_total_ms=None):
    """Validate the atomic four-state presentation/wire pair."""
    if isinstance(state, bool) or isinstance(time_left_ms, bool):
        return False
    raw_state = state
    raw_time_left_ms = time_left_ms
    try:
        state = int(state)
        time_left_ms = int(time_left_ms)
    except (TypeError, ValueError, OverflowError):
        return False
    try:
        exact = (float(raw_state) == state and
                 float(raw_time_left_ms) == time_left_ms)
    except (TypeError, ValueError, OverflowError):
        return False
    if not exact or state not in STATES or not 0 <= time_left_ms <= 4000:
        return False
    if ((state in (SWITCHING_ON, SWITCHING_OFF)) !=
            (time_left_ms > 0)):
        return False
    if transition_total_ms is not None:
        if isinstance(transition_total_ms, bool):
            return False
        raw_total_ms = transition_total_ms
        try:
            transition_total_ms = int(transition_total_ms)
            total_exact = float(raw_total_ms) == transition_total_ms
        except (TypeError, ValueError, OverflowError):
            return False
        if (not total_exact or not 0 <= transition_total_ms <= 4000 or
                ((state in (SWITCHING_ON, SWITCHING_OFF)) !=
                 (transition_total_ms > 0)) or
                time_left_ms > transition_total_ms):
            return False
    if descriptor_or_name is not None and params(descriptor_or_name) is None:
        return (state == DISABLED and time_left_ms == 0 and
                transition_total_ms in (None, 0))
    return True
