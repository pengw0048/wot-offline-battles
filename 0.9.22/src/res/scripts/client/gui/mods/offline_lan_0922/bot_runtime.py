from __future__ import print_function

"""Authority-side, engine-free bridge from v5 bots to the local AI package."""

import copy
import math
import random
import sys

from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.ai import driver as ai_driver
from gui.mods.offline_lan_0922.ai import planner as ai_planner
from gui.mods.offline_lan_0922.ai.navigation import (
    BAKED_FATAL_HAZARDS, BAKED_SHALLOW_WATER, TerrainNavigator)
from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import ballistics
from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import effective_params
from gui.mods.offline_lan_0922 import equipment_mechanics
from gui.mods.offline_lan_0922 import gun_pitch_limits
from gui.mods.offline_lan_0922 import hull_aiming
from gui.mods.offline_lan_0922 import prebaked_navigation
from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922 import siege_mechanics
from gui.mods.offline_lan_0922 import spotting
from gui.mods.offline_lan_0922 import loadout
from gui.mods.offline_lan_0922 import lan_client
from gui.mods.offline_lan_0922 import tank_collision
from gui.mods.offline_lan_0922 import vehicle_physics


OBSERVATION_SECONDS = 0.40
# The logical runtime keeps its mature 30 Hz default for engine-free callers.
# Production hidden workers explicitly select the lower-cost 10 Hz control and
# copied-hull cadence through BotRuntime's internal constructor seam. Native
# projectile progression remains owned by BattleRuntime's separate clock,
# while burst edges below retain their exact due timestamps.
PUBLICATION_SECONDS = 1.0 / 30.0
WORKER_CONTROL_SECONDS = 0.10
# A stalled render callback may consume one regular control step plus one
# catch-up step.  Remaining elapsed stays as debt for later callbacks instead
# of multiplying full-roster native work in the frame that already stalled.
MAX_CONTROL_STEPS_PER_FRAME = 2
LOCAL_ACTION_SECONDS = 0.10
TACTICAL_REFRESH_SECONDS = 1.0
# Eight protocol-maximum exact paths can share only two of the four native
# rays while strategic work is pending.  Give both the initial intent and each
# stale moving-target reproof their own queue-sized lifetime.
ARTILLERY_INTENT_SECONDS = 60.0
ARTILLERY_REPROOF_SECONDS = 60.0
ARTILLERY_TOTAL_PROOF_SECONDS = 120.0
# Refresh an undispersed SPG lead when target motion during the native proof
# queue moves its intended aim point farther than a conservative half-width.
# This gate must never inspect or compensate the random projectile endpoint.
ARTILLERY_AIM_STALENESS_METRES = 1.5
COVER_JOBS_PER_OBSERVATION = 3
HUMAN_TARGET_ID_BASE = 1000000
# The pinned client evaluates spotting at roughly six hertz. Keep the full
# camouflage/LOS calculation on that fixed authority cadence; intervening
# planner reads reuse the last pair result. A target fire-sequence edge still
# invalidates the pair immediately below.
VISIBILITY_SAMPLE_SECONDS = 1.0 / 6.0
SHOT_LANE_SECONDS = 0.20
# Spread full-roster tactical refreshes across one second independently from
# the 0.40-second spotting publication. A selected target still goes through
# the independent 0.20-second final-fire gate above, so a cached tactical lane
# can never authorize an unsafe shot.
SHOT_LANE_REFRESH_SECONDS = TACTICAL_REFRESH_SECONDS
SHOT_LANE_PHASES = 29
# Sixty-four exact static rays finish all 435 protocol-maximum pairs inside a
# one-second tactical window at 24 FPS while removing the old 110-ray
# render-thread spike. Spotting publication never waits for this advisory
# shooter list; missing or expired pairs publish as not shootable.
MAX_SHOT_LANE_PAIRS_PER_FRAME = 64
# The server never assigns a visible target beyond 560 metres. Keep a
# conservative 25-metre broad-phase margin for the advisory roster scan; the
# selected target's independent 0.20-second final-fire check does not use this
# stale tactical result as launch authorization.
SHOT_LANE_QUERY_DISTANCE = 585.0
# Artillery is reported shootable only after the authority has completed a
# pitch-valid curved-world probe.  Keep its query envelope at map scale without
# making every ordinary tank spend native rays beyond the server's 560 m lease.
SPG_SHOT_LANE_QUERY_DISTANCE = 2500.0
# Cover fans share the one-second global-tactics cadence but remain phased
# through its first half. Visibility reports continue independently.
COVER_REFRESH_SECONDS = TACTICAL_REFRESH_SECONDS
COVER_JOB_WINDOW_SECONDS = COVER_REFRESH_SECONDS * 0.5
PROBE_KINDS = ('visibility', 'lane', 'cover', 'ground', 'motion')
DECISION_SECONDS = 0.150
# Distance tiers for planner and suspension sampling.  Physical integration is
# globally capped by PUBLICATION_SECONDS; MatrixAnimation interpolates the
# accepted poses, so a second per-bot integration throttle would only create
# incomplete observations and unequal time steps.
DETAIL_NEAR_METRES = 150.0
DETAIL_FAR_METRES = 350.0
# Travel that must accumulate before a tier re-samples the four ground rays.
SLOPE_SAMPLE_METRES = (0.35, 1.50, 4.00)
SLOPE_SAMPLE_RADIANS = (0.05, 0.15, 0.40)
# Pitch/roll is presentation-only; centre support and copied longitudinal
# physics remain live for every Bot every authority tick.  Countdown prewarm
# removes the 29 * 4 start spike, while this full-roster limit guarantees that
# every due visual hull target is refreshed in the same authority tick.
MAX_SLOPE_POSE_SAMPLES_PER_FRAME = 29
# Planner cadence multiplier per tier.
DECISION_TIER_FACTOR = (1.0, 2.0, 4.0)
# The #1513 production probe owns a 15 m low-speed / 20 m high-speed,
# three-lane corridor.  A cached sample may only be reused while the hull stays
# well inside the 2.2 m outer lanes.  The time bound also limits maximum copied
# travel to 35 m/s * 0.150 s = 5.25 m; the tighter 3.5 m spatial containment
# below still forces a new native probe before that full interval at top speed.
MOTION_PROBE_SECONDS = DECISION_SECONDS
MOTION_PROBE_LATERAL_BUDGET = 1.0
MOTION_PROBE_FORWARD_BUDGET = 3.5
# A validated baked edge needs enough native lookahead to react, not a ray past
# its next turn. Expand from one grid cell to a 1.5-second horizon with speed.
BAKED_MOTION_LOOKAHEAD_SECONDS = 1.5
# Friendly following uses the same one-second time gap that the former hard
# cutoff attempted to enforce.  Keep its standstill gap separate from hull
# extents: ``clearance`` below is already measured edge-to-edge.
TRAFFIC_HEADWAY_SECONDS = 1.0
TRAFFIC_STANDSTILL_CLEARANCE = 1.5
# Match the copied longitudinal integrator's parked-speed boundary when
# integrating the remaining coast distance.  A parked hull still keeps its
# physical yaw for same-lane versus crossing classification.
TRAFFIC_DIRECTION_SPEED_EPSILON = 0.02
# A friendly-hull escape is a temporary tactical override, not a new route.
# Bound it so an unreachable lateral point cannot suppress the server order
# forever; every metre still has to pass the ordinary native motion gate.
FRIENDLY_REPOSITION_SECONDS = 4.0
# A final exact receipt costs nine native rays. Thirteen jobs per render frame
# drains all 29 Bots within three frames even at the supported 24 FPS floor.
# The complete dual-height, three-lane generic sweep remains authoritative
# while an exact receipt waits for that bounded optimisation queue. A completed
# exact hard receipt still vetoes motion immediately.
MAX_WORLD_RECEIPTS_PER_FRAME = 13
FIRE_DURATION_SECONDS = 10.0
FIRE_TICK_SECONDS = 1.0
SIEGE_ENABLE_DEBOUNCE_SECONDS = 0.30
SIEGE_DISABLE_DEBOUNCE_SECONDS = 0.80
SIEGE_LONG_TRAVEL_METRES = 35.0
BOT_WATER_AVOID_DEPTH = 0.90
BOT_DROWNING_PROBE_SECONDS = 0.30
BOT_DROWNING_SECONDS = 10.0
BOT_DROWNING_DEATH_REASON = 5
BOT_OVERTURN_IGNORE_SECONDS = 0.10
BOT_OVERTURN_WARNING_COSINE = vehicle_physics.OVERTURN_WARNING_COSINE
BOT_OVERTURN_DANGER_COSINE = vehicle_physics.OVERTURN_DANGER_COSINE
BOT_OVERTURN_DEATH_SECONDS = 30.0
BOT_OVERTURN_DEATH_REASON = 7


def _cache_deadline(now, entity_id, interval, salt=0, stagger=False):
    """Spread only the first expiry, then retain the requested cadence."""
    interval = max(0.001, float(interval))
    deadline = float(now) + interval
    if not stagger:
        return deadline
    phase = (((abs(int(entity_id)) * 17 + int(salt) * 11) % 29) /
             29.0) * interval
    return deadline + phase


def _motion_probe_deadline(now, entity_id, initial=False):
    """Stagger first rechecks without ever exceeding the safety interval."""
    if not initial:
        return float(now) + MOTION_PROBE_SECONDS
    phase = (((abs(int(entity_id)) * 17 + 7 * 11) % 29) + 1) / 29.0
    return float(now) + MOTION_PROBE_SECONDS * phase


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _position(state):
    return (_number(state.get('x')), _number(state.get('y')),
            _number(state.get('z')))


def _value(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _water_sensor_vector(value, count, label):
    """Validate one vector consumed by #1513's native WaterSensor."""
    try:
        values = tuple(value)
    except (TypeError, ValueError):
        raise ValueError('%s is not a %d-component vector' % (label, count))
    if len(values) != count:
        raise ValueError('%s is not a %d-component vector' % (label, count))
    result = []
    for index, raw in enumerate(values):
        if isinstance(raw, bool):
            raise ValueError('%s[%d] is not finite' % (label, index))
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('%s[%d] is not finite' % (label, index))
        if math.isnan(number) or math.isinf(number):
            raise ValueError('%s[%d] is not finite' % (label, index))
        result.append(number)
    return tuple(result)


def _water_sensor_geometry(descriptor):
    """Return the exact local underwater point donated to WaterSensor."""
    chassis = _value(descriptor, 'chassis')
    hull = _value(descriptor, 'hull')
    if chassis is None or hull is None:
        raise ValueError('descriptor has no WaterSensor chassis or hull')
    hull_position = _water_sensor_vector(
        _value(chassis, 'hullPosition'), 3, 'chassis.hullPosition')
    turret_positions = _value(hull, 'turretPositions')
    try:
        turret_position = turret_positions[0]
    except (KeyError, IndexError, TypeError):
        raise ValueError('hull.turretPositions has no index 0')
    turret_position = _water_sensor_vector(
        turret_position, 3, 'hull.turretPositions[0]')
    carrying_point = _water_sensor_vector(
        _value(chassis, 'topRightCarryingPoint'), 2,
        'chassis.topRightCarryingPoint')
    return (
        (hull_position[0] + turret_position[0],
         hull_position[1] + turret_position[1],
         hull_position[2] + turret_position[2]),
        carrying_point,
    )


def _vehicle_local_height(point, pitch, roll):
    """Return world-up height after the pinned roll-then-pitch transform."""
    x_value, y_value, z_value = point
    roll_sine, roll_cosine = math.sin(roll), math.cos(roll)
    rolled_y = roll_sine * x_value + roll_cosine * y_value
    pitch_sine, pitch_cosine = math.sin(pitch), math.cos(pitch)
    return pitch_cosine * rolled_y - pitch_sine * z_value


def _water_sensor_level(state, descriptor, water_depth):
    """Mirror #1513 WaterSensor's in-water and underwater predicates."""
    depth = float(water_depth)
    if math.isnan(depth) or math.isinf(depth):
        raise ValueError('water depth is not finite')
    if depth < 0.0:
        return 0
    turret_offset, unused_carrying_point = _water_sensor_geometry(descriptor)
    turret_height = _vehicle_local_height(
        turret_offset, _number(state.get('pitch')),
        _number(state.get('roll')))
    return 2 if depth > turret_height else 1


def _player_effective_params(raw):
    """Return the immutable client-derived player mechanics snapshot.

    A hidden worker does not own the human's mounted crew, equipment,
    consumables or camouflage. Reconstructing those values from a bare
    descriptor silently changes collision and spotting results, so an absent
    or malformed snapshot is a roster-contract failure rather than a reason
    to install defaults.
    """
    if not isinstance(raw, dict):
        raise ValueError('player effective parameters row is invalid')
    snapshot = effective_params.canonical(raw.get('effective_params'))
    if snapshot is None:
        raise ValueError('player effective parameters are missing or invalid')
    return snapshot


def _player_dynamic_spotting(snapshot, state):
    """Select the client's exact native row for this crew/fire state."""
    crew = snapshot.get('crew') or {}
    dynamic = crew.get('dynamic_spotting') or {}
    roster = tuple(str(name) for name in (dynamic.get('crew') or ()))
    states = dynamic.get('states') or {}
    critical = state.get('critical')
    critical = critical if isinstance(critical, dict) else {}
    knocked_out = set(str(name) for name in
                      (critical.get('crew_ko') or ()))
    if knocked_out.difference(roster):
        raise ValueError('player critical crew is outside its projection')
    mask = 0
    for index, name in enumerate(roster):
        if name in knocked_out:
            mask |= 1 << index
    key = '%d:%d' % (mask, int(bool(critical.get('fire', False))))
    row = states.get(key)
    if not isinstance(row, dict):
        raise ValueError('player dynamic spotting state is unavailable')
    return key, row


def _player_spotting_perk(snapshot, state, wanted):
    """Return whether a living projected crewman carries one finished perk."""
    wanted = str(wanted).lower()
    critical = state.get('critical')
    critical = critical if isinstance(critical, dict) else {}
    knocked_out = set(str(name) for name in
                      (critical.get('crew_ko') or ()))
    for member in (snapshot.get('crew') or {}).get('members') or ():
        if str(member.get('instance')) in knocked_out:
            continue
        for skill in member.get('skills') or ():
            if (str(skill.get('name')).lower() == wanted and
                    skill.get('active') is True and
                    _number(skill.get('level')) >= 100.0):
                return True
    return False


def _forward_speed(descriptor):
    physics = _value(descriptor, 'physics', {}) or {}
    limits = _value(physics, 'speedLimits', (14.0, 7.0))
    try:
        value = abs(float(limits[0]))
    except (TypeError, ValueError, IndexError):
        value = 14.0
    return max(4.0, min(value, 35.0))


def _bot_profile(descriptor):
    """Spotting inputs for a vehicle whose crew is the #1513 default crew."""
    return loadout.spotting_profile(
        descriptor, None, factors=loadout.attribute_factors(descriptor))




def _view_range(descriptor, still_seconds=0.0):
    """Bot view range, using the same device and crew law as the player."""
    turret = _value(descriptor, 'turret', {}) or {}
    misc = _value(descriptor, 'miscAttrs', {}) or {}
    profile = _bot_profile(descriptor)
    return spotting.effective_view_range(
        _value(turret, 'circularVisionRadius', 330.0),
        misc_factor=_value(misc, 'circularVisionRadiusFactor', 1.0),
        crew_factor=profile['vision_factor'],
        binocular_factor=profile['binocular_factor'],
        binocular_active=(
            profile['has_binoculars'] and
            loadout.still_device_active(
                still_seconds, profile['binocular_delay'])))


def _vision_range_pair(descriptor):
    """Bot view range while moving, once its stereoscope arms, and the delay.

    The delay is ``None`` when the bot carries no stationary vision device, so
    the caller never pays for a stillness lookup it cannot use.
    """
    profile = _bot_profile(descriptor)
    moving = _view_range(descriptor)
    if not profile['has_binoculars']:
        return moving, moving, None
    turret = _value(descriptor, 'turret', {}) or {}
    misc = _value(descriptor, 'miscAttrs', {}) or {}
    still = spotting.effective_view_range(
        _value(turret, 'circularVisionRadius', 330.0),
        misc_factor=_value(misc, 'circularVisionRadiusFactor', 1.0),
        crew_factor=profile['vision_factor'],
        binocular_factor=profile['binocular_factor'],
        binocular_active=True)
    return moving, still, profile['binocular_delay']


def _base_invisibility(descriptor, profile=None, camouflage_id=None):
    if profile is None:
        profile = _bot_profile(descriptor)
    crew_factor = profile['camouflage_factor']
    calculator = getattr(descriptor, 'computeBaseInvisibility', None)
    if callable(calculator):
        try:
            values = calculator(crew_factor, camouflage_id)
            if isinstance(values, (list, tuple)) and len(values) >= 2:
                return (_number(values[0]), _number(values[1]))
        except Exception:
            pass
    vehicle_type = _value(descriptor, 'type', {}) or {}
    values = _value(vehicle_type, 'invisibility', (0.0, 0.0))
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        values = (0.0, 0.0)
    misc = _value(descriptor, 'miscAttrs', {}) or {}
    return spotting.base_camouflage(
        values[0], values[1], crew_factor=crew_factor,
        invisibility_factor=_value(misc, 'invisibilityFactor', 1.0))


def _shot_invisibility_factor(descriptor):
    gun = _value(descriptor, 'gun', {}) or {}
    return spotting.clamp(
        _value(gun, 'invisibilityFactorAtShot', 1.0), 0.0, 1.0)


def _invisibility_aspect(profile, moving, still_device_ready):
    """Pick the stationary aspect only once the net has really settled."""
    if moving or (profile['has_camouflage_net'] and not still_device_ready):
        return profile['invisibility_moving']
    return profile['invisibility_still']


def _detection_upper_bound(distance, view_range, base_pair, moving,
                           shot_factor, fired_recently):
    """Return detection with the best possible geometry for this pair.

    Foliage camouflage is additive and clamped to a non-negative value, so
    zero foliage is the minimum possible camouflage.  A clear line of sight is
    likewise the maximum possible visibility.  If this upper bound is false,
    neither the native collision ray nor the real foliage result can make the
    target visible.
    """
    minimum_camouflage = spotting.effective_camouflage(
        base_pair, moving=moving, shot_factor=shot_factor,
        fired_recently=fired_recently, foliage_bonus=0.0)
    return spotting.is_detected(
        distance, view_range, minimum_camouflage, True)


def _hull_dimensions(descriptor):
    """Derive AI avoidance dimensions from the admitted collision body."""
    shape = tank_collision.chassis_shape(descriptor)
    return shape[1], shape[0]


def _collision_shape(descriptor):
    """Return the current 0.8.2 chassis hit-tester body."""
    return tank_collision.chassis_shape(descriptor)


def _distance(first, second):
    dx = _number(first[0]) - _number(second[0])
    dz = _number(first[2]) - _number(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _angle_delta(target, current):
    value = _number(target) - _number(current)
    while value > math.pi:
        value -= math.pi * 2.0
    while value < -math.pi:
        value += math.pi * 2.0
    return value


def _wrapped(value):
    return _angle_delta(value, 0.0)


def _point(value, fallback=(0.0, 0.0, 0.0)):
    if isinstance(value, dict):
        return (_number(value.get('x'), fallback[0]),
                _number(value.get('y'), fallback[1]),
                _number(value.get('z'), fallback[2]))
    try:
        return (_number(value[0], fallback[0]),
                _number(value[1], fallback[1]),
                _number(value[2], fallback[2]))
    except (TypeError, IndexError):
        return fallback


def _slew(current, desired, maximum_step):
    difference = float(desired) - float(current)
    step = max(0.0, float(maximum_step))
    if difference > step:
        return float(current) + step
    if difference < -step:
        return float(current) - step
    return float(desired)


def _rotation_speed(component, default):
    return max(0.0, _number(_value(component, 'rotationSpeed', default),
                            default))


def slope_pose(probe, position, yaw, half_length, half_width,
               last_pitch=0.0, last_roll=0.0):
    """One step of the copied 0.8.2 four-point suspension hull pose."""
    length = max(3.0, 2.0 * float(half_length))
    width = max(2.0, 2.0 * float(half_width))
    sine, cosine = math.sin(yaw), math.cos(yaw)
    front = probe(position[0] + sine * length * 0.5,
                  position[2] + cosine * length * 0.5, position[1])
    rear = probe(position[0] - sine * length * 0.5,
                 position[2] - cosine * length * 0.5, position[1])
    right = probe(position[0] + cosine * width * 0.5,
                  position[2] - sine * width * 0.5, position[1])
    left = probe(position[0] - cosine * width * 0.5,
                 position[2] + sine * width * 0.5, position[1])
    if None in (front, rear, right, left):
        return float(last_pitch), float(last_roll)
    pitch = -math.atan2(float(front) - float(rear), length) * 0.9
    roll = math.atan2(float(right) - float(left), width) * 0.9
    tilt = math.sqrt(pitch * pitch + roll * roll)
    if tilt > 0.61:
        scale = 0.61 / tilt
        pitch *= scale
        roll *= scale
    return (float(last_pitch) + (pitch - float(last_pitch)) * 0.5,
            float(last_roll) + (roll - float(last_roll)) * 0.5)


def _gun_pitch_limits(descriptor, turret_yaw=0.0):
    """Return the installed gun envelope at the current local turret yaw."""
    gun = _value(descriptor, 'gun', {}) or {}
    limits = _value(gun, 'pitchLimits')
    if isinstance(limits, dict) and all(
            name in limits for name in ('minPitch', 'maxPitch')):
        try:
            return gun_pitch_limits.calc_pitch_limits(turret_yaw, limits)
        except ValueError:
            return None
    if isinstance(limits, dict):
        limits = limits.get('absolute')
    try:
        return float(limits[0]), float(limits[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _shot_ballistics(descriptor, shell_index):
    """Return frozen ``(speed, gravity, max_distance)`` or ``None``.

    Exact #1513 descriptors are attribute-only objects.  Missing physical
    fields are not permission to resurrect the old instantaneous straight-ray
    shot, so production firing fails closed when this tuple is unavailable.
    """
    gun = _value(descriptor, 'gun', {}) or {}
    shots = _value(gun, 'shots', ()) or ()
    try:
        shot = shots[max(0, int(shell_index))]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    speed = _number(_value(shot, 'speed'), -1.0)
    gravity = abs(_number(_value(shot, 'gravity'), -1.0))
    maximum = _number(_value(shot, 'maxDistance'), -1.0)
    if speed <= 1.0 or gravity <= 0.01 or maximum <= 1.0:
        return None
    return speed, gravity, maximum


_CRITICAL_PARTS_TICK_CACHE = '_critical_parts_tick_cache'


def _parse_critical_parts(critical):
    if not isinstance(critical, dict):
        return {}, set(), set(), set()
    devices = {}
    yellow = set()
    for record in critical.get('devices') or ():
        if isinstance(record, dict) and record.get('name'):
            name = str(record['name'])
            devices[name] = _number(record.get('hp'))
            if record.get('state') == 'critical':
                yellow.add(name)
    destroyed = set(str(name) for name in
                    (critical.get('destroyed') or ()))
    yellow.difference_update(destroyed)
    crew_ko = set(str(name) for name in (critical.get('crew_ko') or ()))
    return devices, destroyed, crew_ko, yellow


def _critical_parts(state):
    critical = state.get('critical')
    cached = state.get(_CRITICAL_PARTS_TICK_CACHE)
    if (isinstance(cached, tuple) and len(cached) == 2 and
            cached[0] is critical):
        return cached[1]
    return _parse_critical_parts(critical)


def _cache_critical_parts_for_tick(state):
    """Parse one stable post-repair payload for the current Bot tick."""
    critical = state.get('critical')
    parts = _parse_critical_parts(critical)
    state[_CRITICAL_PARTS_TICK_CACHE] = (critical, parts)
    return parts


def _clear_critical_parts_tick_cache(state):
    state.pop(_CRITICAL_PARTS_TICK_CACHE, None)


def _copy_runtime_state(state):
    """Copy authority state without the private one-tick parse cache."""
    copied = dict(state)
    copied.pop(_CRITICAL_PARTS_TICK_CACHE, None)
    return copied


def _critical_factor(state, descriptor, stat):
    devices, destroyed, crew_ko, yellow = _critical_parts(state)
    return (device_damage.crew_stat_factor(crew_ko, stat) *
            device_damage.module_stat_factor(
                devices, destroyed, descriptor, stat, yellow))


def _critical_signature(payload):
    """Match the server's durable, three-decimal critical-state boundary."""
    if not isinstance(payload, dict) or not payload:
        return ()
    devices = []
    for record in payload.get('devices') or ():
        if not isinstance(record, dict) or not record.get('name'):
            raise ValueError('bot critical device is malformed')
        devices.append((
            str(record['name']), round(_number(record.get('hp')), 3),
            round(_number(record.get('max_hp'), 1.0), 3),
            str(record.get('state', ''))))
    signature = (
        tuple(sorted(devices)),
        tuple(sorted(str(name) for name in
                     (payload.get('destroyed') or ()))),
        tuple(sorted(str(name) for name in
                     (payload.get('crew_ko') or ()))),
        tuple(str(name) for name in
              (payload.get('crew_roster') or ())),
        bool(payload.get('fire', False)),
        bool(payload.get('ammo_rack_death', False)))
    if signature == ((), (), (), (), False, False):
        return ()
    return signature


def _canonical_critical(payload):
    """Emit exactly the durable shape returned by server ``_critical_state``."""
    if not isinstance(payload, dict) or not payload:
        return {}
    devices = []
    for record in payload.get('devices') or ():
        if (not isinstance(record, dict) or not record.get('name') or
                'hp' not in record or 'max_hp' not in record or
                record.get('state') not in (
                    'normal', 'critical', 'destroyed')):
            raise ValueError('bot critical device is malformed')
        maximum = max(1.0, round(float(record['max_hp']), 3))
        hp = max(0.0, min(round(float(record['hp']), 3), maximum))
        devices.append({
            'name': str(record['name']), 'hp': hp, 'max_hp': maximum,
            'state': str(record['state'])})
    devices.sort(key=lambda record: record['name'])
    result = {
        'devices': devices,
        'destroyed': sorted(str(name) for name in
                            (payload.get('destroyed') or ())),
        'crew_ko': sorted(str(name) for name in
                          (payload.get('crew_ko') or ())),
        'fire': bool(payload.get('fire', False)),
        'ammo_rack_death': bool(payload.get('ammo_rack_death', False)),
        'events': [],
    }
    crew_roster = payload.get('crew_roster')
    if isinstance(crew_roster, (list, tuple)) and crew_roster:
        result['crew_roster'] = [str(name) for name in crew_roster]
    return result


def _combat_signature(state):
    return (
        max(0, int(_number(state.get('health')))),
        bool(state.get('alive', False)),
        _critical_signature(state.get('critical')),
        round(_number(state.get('combat_fire_elapsed')), 6),
        round(_number(state.get('combat_fire_timer')), 6),
        max(0, int(_number(state.get('stun_end_server_time_ms')))))


def _combat_record(state):
    return {
        'health': max(0, int(_number(state.get('health')))),
        'alive': bool(state.get('alive', False)),
        'critical': _canonical_critical(state.get('critical')),
        'combat_fire_elapsed': round(
            _number(state.get('combat_fire_elapsed')), 6),
        'combat_fire_timer': round(
            _number(state.get('combat_fire_timer')), 6),
        'stun_end_server_time_ms': max(
            0, int(_number(state.get('stun_end_server_time_ms')))),
    }


def _local_launch_record(state, launch_time_us=None):
    """Return only fields consumed by BattleRuntime's projectile launch."""
    if 'shot_yaw' not in state or 'shot_pitch' not in state:
        return None
    try:
        raw_launch_time_us = launch_time_us
        launch_time_us = int(raw_launch_time_us)
        exact_launch_time = float(raw_launch_time_us) == launch_time_us
    except (TypeError, ValueError, OverflowError):
        return None
    launch_pose = tuple(_number(state.get(name)) for name in (
        'x', 'y', 'z', 'yaw', 'pitch', 'roll'))
    if (isinstance(raw_launch_time_us, bool) or
            not exact_launch_time or
            launch_time_us < 0 or
            any(math.isnan(value) or math.isinf(value)
                for value in launch_pose)):
        return None
    profile = state.get('profile')
    profile = profile if isinstance(profile, dict) else {}
    class_tag = str(profile.get('class_tag') or '')
    result = {
        'id': int(state['id']),
        'fire_seq': int(state.get('fire_seq', 0)),
        'shell_index': int(state.get('shell_index', 0)),
        'shot_yaw': state['shot_yaw'],
        'shot_pitch': state['shot_pitch'],
        'class_tag': class_tag,
        'burst_group_seq': int(state.get(
            'burst_group_seq', state.get('fire_seq', 0))),
        'burst_index': int(state.get('burst_index', 0)),
        'burst_count': int(state.get('burst_count', 1)),
        'launch_time_us': launch_time_us,
        'launch_pose': launch_pose,
    }
    if 'shot_origin' in state:
        result['shot_origin'] = state['shot_origin']
    if class_tag == 'SPG':
        for name in (
                'shot_velocity', 'shot_gravity',
                'shot_max_distance', 'shot_max_time_ms', 'shot_proof_key'):
            if name in state:
                result[name] = state[name]
    return result


def _apply_combat_record(state, record):
    state['health'] = max(0, min(
        int(_number(record.get('health'))), int(state['max_health'])))
    state['alive'] = bool(record.get('alive')) and state['health'] > 0
    state['critical'] = _canonical_critical(record.get('critical'))
    state['combat_fire_elapsed'] = round(
        _number(record.get('combat_fire_elapsed')), 6)
    state['combat_fire_timer'] = round(
        _number(record.get('combat_fire_timer')), 6)
    state['stun_end_server_time_ms'] = max(
        0, int(_number(record.get('stun_end_server_time_ms'))))
    state['display_health'] = state['health']
    if not state['alive']:
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['target_kind'] = None
        state['target_id'] = None


class _BotCriticalVehicle(object):
    """Detached adapter for the copied 0.8.2 repair and fire functions."""

    def __init__(self, state, descriptor, fire_started, fire_timer,
                 equipment_passives=None):
        payload = state.get('critical') or {}
        devices = {}
        for record in payload.get('devices') or ():
            if not isinstance(record, dict) or not record.get('name'):
                raise ValueError('bot critical device is malformed')
            devices[str(record['name'])] = max(
                0.0, float(record.get('hp', 0.0)))
        self.id = int(state['id'])
        self.health = max(0, int(_number(state.get('health'))))
        self.maxHealth = max(1, int(_number(
            state.get('max_health'), self.health or 1)))
        self.typeDescriptor = descriptor
        self.devices_hp = devices
        self._destroyed_devices = set(
            str(name) for name in (payload.get('destroyed') or ()))
        self._crew_ko = set(
            str(name) for name in (payload.get('crew_ko') or ()))
        self._crew_impaired = frozenset()
        self.is_on_fire = bool(payload.get('fire', False))
        self._ammo_rack_death = bool(
            payload.get('ammo_rack_death', False))
        self._fire_started = fire_started
        self._fire_timer = max(0.0, float(fire_timer or 0.0))
        self._offline_proposal_only = True
        equipment_passives = equipment_passives or {}
        self._fire_starting_chance_factor = max(0.0, _number(
            equipment_passives.get('fireStartingChanceFactor'), 1.0))
        self._medkit_bonus_value = max(0.0, _number(
            equipment_passives.get('medkitBonusValue'), 0.0))
        self.is_tracked = False
        self.is_engine_dead = False
        self.is_gun_destroyed = False
        self.is_turret_locked = False


def _descriptor_crew_roster(descriptor):
    """Return the exact #1513 health-instance names for one real crew."""
    roles = getattr(getattr(descriptor, 'type', None), 'crewRoles', None)
    if not isinstance(roles, (list, tuple)) or not roles:
        return ()
    counters = {'gunner': 1, 'loader': 1, 'radioman': 1}
    allowed = frozenset(
        ('commander', 'driver', 'gunner', 'loader', 'radioman'))
    roster = []
    for crewman_roles in roles:
        if (not isinstance(crewman_roles, (list, tuple)) or
                not crewman_roles):
            return ()
        main_role = str(crewman_roles[0])
        if main_role not in allowed:
            return ()
        if main_role in counters:
            name = main_role + str(counters[main_role])
            counters[main_role] += 1
        else:
            name = main_role
        if name in roster:
            return ()
        roster.append(name)
    return tuple(roster)


def _terminal_critical(state, descriptor, cause):
    """Build complete wreck state from the worker's installed descriptor."""
    shadow = _BotCriticalVehicle(
        state, descriptor, None,
        _number(state.get('combat_fire_timer')))
    terminal = critical_damage.apply_death(shadow, cause)
    if not isinstance(terminal, dict):
        return None
    roster = _descriptor_crew_roster(descriptor)
    if roster:
        terminal = dict(terminal)
        terminal['crew_roster'] = list(roster)
        terminal['crew_ko'] = list(roster)
    return _canonical_critical(terminal)


class _BotGunState(object):
    """The final 0.8.2 bot reload/clip and #1513 dispersion clocks.

    Inventory and loaded-shell selection live in ``_BotAmmoState``. A clip
    starts full; rounds inside it use ``clip[1]`` and an empty clip remains
    empty until the full reload boundary completes.
    """

    def __init__(self, descriptor, fire_seq=0, dispersion_factor=1.0):
        gun = _value(descriptor, 'gun', {}) or {}
        gun_modifiers = loadout.modifiers(
            descriptor, factors=loadout.attribute_factors(descriptor))
        self.loadout = dict(gun_modifiers)
        raw_dispersion = _value(gun, 'shotDispersionAngle')
        try:
            self.fully_aimed_dispersion = (
                float(raw_dispersion) *
                float(gun_modifiers['dispersion_factor']))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                'installed gun shotDispersionAngle is unavailable')
        if (isinstance(raw_dispersion, bool) or
                math.isnan(self.fully_aimed_dispersion) or
                math.isinf(self.fully_aimed_dispersion) or
                self.fully_aimed_dispersion <= 0.0):
            raise ValueError(
                'installed gun shotDispersionAngle must be positive')
        factors = _value(gun, 'shotDispersionFactors', {}) or {}
        self.after_shot = max(
            0.0, _number(_value(factors, 'afterShot'), 1.5))
        self.after_shot_in_burst = burst_mechanics.after_shot_factor(
            gun, False)
        self.burst_count, self.burst_interval = \
            burst_mechanics.descriptor_burst(gun)
        self.turret_dispersion_factor = max(
            0.0, _number(_value(factors, 'turretRotation'), 0.0))
        self.aiming_time = max(
            0.1, _number(_value(gun, 'aimingTime'), 2.0) *
            float(gun_modifiers['aim_time_factor']))
        chassis_factors = _value(
            _value(descriptor, 'chassis', {}) or {},
            'shotDispersionFactors', (0.0, 0.0)) or (0.0, 0.0)
        try:
            self.movement_dispersion_factor = max(
                0.0, float(chassis_factors[0]))
            self.rotation_dispersion_factor = max(
                0.0, float(chassis_factors[1]))
        except (TypeError, ValueError, IndexError, OverflowError):
            self.movement_dispersion_factor = 0.0
            self.rotation_dispersion_factor = 0.0
        self.current_dispersion_factor = 1.0
        self.aiming_start_factor = 1.0
        self.aiming_elapsed = 0.0
        self.dispersion = self.fully_aimed_dispersion
        self.motion_dispersion_squared = 0.0
        self.reload_full = max(
            0.01, _number(_value(gun, 'reloadTime', 3.0), 3.0) *
            float(gun_modifiers['reload_factor']))
        self.clip_size = 1
        self.reload_intra = 0.0
        clip = _value(gun, 'clip')
        try:
            if len(clip) == 2:
                self.clip_size = max(1, int(clip[0]))
                self.reload_intra = max(0.01, float(clip[1]))
        except (TypeError, ValueError, IndexError):
            pass
        shots = _value(gun, 'shots', ()) or ()
        try:
            self.shell_count = max(1, len(shots))
        except TypeError:
            self.shell_count = 1
        self.clip = self.clip_size
        self.elapsed = 0.0
        self.reload_duration = self.reload_full
        self.reload_kind = 'full'
        self.reload_factor = 1.0
        self._burst_remaining = 0
        self.restore_fire_seq(fire_seq, dispersion_factor)

    def adopt_descriptor(self, descriptor):
        """Adopt a mode descriptor without resetting active battle clocks."""
        old_dispersion = self.dispersion
        active_reload_duration = self.reload_duration
        aiming_elapsed = self.aiming_elapsed
        candidate = _BotGunState(descriptor)
        if (candidate.shell_count != self.shell_count or
                candidate.clip_size != self.clip_size):
            raise RuntimeError(
                '#1513 Siege descriptor changed the ammunition contract')
        names = (
            'loadout', 'fully_aimed_dispersion', 'after_shot',
            'after_shot_in_burst', 'burst_count', 'burst_interval',
            'turret_dispersion_factor', 'aiming_time',
            'movement_dispersion_factor', 'rotation_dispersion_factor',
            'reload_full', 'reload_intra')
        changed = any(getattr(self, name) != getattr(candidate, name)
                      for name in names)
        for name in names:
            value = getattr(candidate, name)
            setattr(self, name, dict(value) if isinstance(value, dict)
                    else value)
        # A composite descriptor changes future reload intervals only. The
        # currently running interval and its completed fraction stay frozen.
        self.reload_duration = active_reload_duration
        current_factor = max(
            1.0, old_dispersion / self.fully_aimed_dispersion)
        self.current_dispersion_factor = current_factor
        self.aiming_elapsed = aiming_elapsed
        aiming_time = max(self.aiming_time, 0.1)
        maximum_start = 1000.0
        maximum_elapsed = (
            aiming_time * math.log(maximum_start / current_factor)
            if 0.0 < current_factor <= maximum_start else 0.0)
        if aiming_elapsed > maximum_elapsed:
            self.aiming_start_factor = max(
                current_factor, maximum_start)
            self.aiming_elapsed = maximum_elapsed
        else:
            self.aiming_start_factor = (
                current_factor * math.exp(aiming_elapsed / aiming_time))
        self.dispersion = (self.fully_aimed_dispersion *
                           self.current_dispersion_factor)
        return changed

    def restore_fire_seq(self, fire_seq, dispersion_factor=1.0,
                         reload_time=None, reload_duration=None,
                         reload_factor=1.0, clip=None, clip_size=None):
        fire_seq = max(0, int(_number(fire_seq)))
        has_clip = clip is not None
        has_clip_size = clip_size is not None
        if has_clip != has_clip_size:
            raise ValueError('bot clip snapshot must be an atomic pair')
        if has_clip:
            raw_clip = clip
            raw_clip_size = clip_size
            try:
                clip = int(clip)
                clip_size = int(clip_size)
            except (TypeError, ValueError, OverflowError):
                raise ValueError('bot clip snapshot is invalid')
            if (isinstance(raw_clip, bool) or
                    isinstance(raw_clip_size, bool) or
                    float(raw_clip) != clip or
                    float(raw_clip_size) != clip_size or
                    clip_size != self.clip_size or
                    clip < 0 or clip > clip_size):
                raise ValueError(
                    'bot clip snapshot disagrees with installed gun')
            self.clip = clip
            self.reload_kind = (
                'intra' if 0 < clip < clip_size else 'full')
            self.reload_duration = (
                self.reload_intra if self.reload_kind == 'intra'
                else self.reload_full)
        elif self.clip_size > 1:
            used = fire_seq % self.clip_size
            self.clip = self.clip_size - used if used else self.clip_size
            self.reload_kind = 'intra' if used else 'full'
            self.reload_duration = (self.reload_intra if used
                                    else self.reload_full)
        else:
            self.clip = 1
            self.reload_duration = self.reload_full
            self.reload_kind = 'full'
        has_reload_time = reload_time is not None
        has_reload_duration = reload_duration is not None
        if has_reload_time != has_reload_duration:
            raise ValueError('bot reload progress must be an atomic pair')
        if has_reload_time:
            if (isinstance(reload_time, bool) or
                    isinstance(reload_duration, bool) or
                    isinstance(reload_factor, bool)):
                raise ValueError('bot reload progress is invalid')
            try:
                reload_time = float(reload_time)
                reload_duration = float(reload_duration)
                reload_factor = float(reload_factor)
            except (TypeError, ValueError, OverflowError):
                raise ValueError('bot reload progress is invalid')
            if (math.isnan(reload_time) or math.isinf(reload_time) or
                    math.isnan(reload_duration) or
                    math.isinf(reload_duration) or
                    math.isnan(reload_factor) or math.isinf(reload_factor) or
                    reload_factor <= 0.0 or reload_duration <= 0.0 or
                    reload_time < 0.0 or reload_time > reload_duration):
                raise ValueError('bot reload progress is invalid')
            expected_duration = self.duration(reload_factor)
            tolerance = max(1.0, expected_duration) * 1.0e-9
            if abs(reload_duration - expected_duration) > tolerance:
                raise ValueError(
                    'bot reload duration disagrees with installed gun')
            self.elapsed = reload_duration - reload_time
            self.reload_factor = reload_factor
        else:
            self.elapsed = 0.0
            self.reload_factor = 1.0
        self._burst_remaining = 0
        if fire_seq > 0:
            # The previous authority's exact aiming clock is not on the wire.
            # Start conservatively at one post-shot ideal and let the full safe
            # reload interval converge it instead of granting an instant aim.
            self.motion_dispersion_squared = 0.0
            self.commit_shot_bloom(dispersion_factor)

    def tick(self, dt):
        self.elapsed += max(0.0, float(dt))

    def tick_dispersion(self, dt, move_speed, rotation_speed, turret_speed,
                        dispersion_factor=1.0, aim_time_factor=1.0):
        """Advance the exact #1513 movement bloom and aiming convergence."""
        dt = max(0.0, float(dt))
        move_term = (abs(float(move_speed)) *
                     self.movement_dispersion_factor)
        rotation_term = (abs(float(rotation_speed)) *
                         self.rotation_dispersion_factor)
        turret_term = (abs(float(turret_speed)) *
                       self.turret_dispersion_factor)
        self.motion_dispersion_squared = (
            move_term * move_term + rotation_term * rotation_term +
            turret_term * turret_term)
        dispersion_factor = max(0.0, float(dispersion_factor))
        ideal = (dispersion_factor *
                 math.sqrt(1.0 + self.motion_dispersion_squared))
        if (math.isnan(ideal) or math.isinf(ideal) or ideal <= 0.0):
            raise ValueError('dynamic bot shot dispersion must be positive')
        aiming_time = self.aiming_time * max(
            0.0, float(aim_time_factor))
        elapsed = self.aiming_elapsed + dt
        candidate = self.aiming_start_factor * math.exp(
            -elapsed / max(aiming_time, 0.1))
        if candidate < ideal:
            self.current_dispersion_factor = ideal
            self.aiming_start_factor = ideal
            self.aiming_elapsed = 0.0
        else:
            self.current_dispersion_factor = candidate
            self.aiming_elapsed = elapsed
        self.dispersion = (self.fully_aimed_dispersion *
                           self.current_dispersion_factor)

    def commit_shot_bloom(self, dispersion_factor=1.0, final_round=True):
        """Apply #1513's intra-burst or final physical-shot bloom."""
        dispersion_factor = max(0.0, float(dispersion_factor))
        bloom = (self.after_shot if final_round else
                 self.after_shot_in_burst)
        ideal = dispersion_factor * math.sqrt(
            1.0 + self.motion_dispersion_squared +
            bloom * bloom)
        if self.current_dispersion_factor < ideal:
            self.current_dispersion_factor = ideal
            self.aiming_start_factor = ideal
            self.aiming_elapsed = 0.0
            self.dispersion = self.fully_aimed_dispersion * ideal

    def rescale_reload(self, reload_factor):
        """Keep the completed fraction when a live reload penalty changes."""
        reload_factor = max(0.0, float(reload_factor))
        if abs(reload_factor - self.reload_factor) <= 1.0e-9:
            return False
        old_duration = self.duration(self.reload_factor)
        new_duration = self.duration(reload_factor)
        if old_duration > 0.0:
            if self.elapsed < old_duration:
                completed_fraction = max(
                    0.0, min(1.0, self.elapsed / old_duration))
                self.elapsed = new_duration * completed_fraction
            else:
                # A shell that was already ready stays ready. Preserve the
                # strict-ready clock's small overrun across the factor change.
                self.elapsed = new_duration + (
                    self.elapsed - old_duration)
        self.reload_factor = reload_factor
        return True

    def duration(self, reload_factor=1.0):
        """Return the current #1513 reload interval.

        Crew, equipment and critical-state factors affect a full reload.
        ``gun.clip[1]`` is already the final intra-clip interval.
        """
        factor = (1.0 if self.reload_kind == 'intra' else
                  max(0.0, float(reload_factor)))
        return self.reload_duration * factor

    def ready(self, reload_factor=1.0):
        return self.elapsed > self.duration(reload_factor)

    def complete_reload(self, reload_factor=1.0,
                        available_rounds=None):
        """Return the completed boundary and refill only an empty clip."""
        if not self.ready(reload_factor):
            return None
        if self.reload_kind == 'full' and self.clip == 0:
            refill = self.clip_size
            if available_rounds is not None:
                refill = min(refill, max(0, int(available_rounds)))
            self.clip = refill
        return self.reload_kind

    def require_full_reload(self):
        """Discard unusable clip slots before changing shell type."""
        self.clip = 0
        self.reload_kind = 'full'
        self.reload_duration = self.reload_full

    def shell_index(self, requested):
        return max(0, min(int(_number(requested)), self.shell_count - 1))

    def fire(self, reload_factor=1.0):
        if not self.begin_burst(1, reload_factor):
            return False
        return self.fire_burst_round(True, reload_factor)

    def begin_burst(self, count, reload_factor=1.0):
        """Arm a physical group without consuming its first round."""
        if self._burst_remaining > 0 or not self.ready(reload_factor):
            return False
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            return False
        if self.clip <= 0:
            self.complete_reload(reload_factor)
        count = min(count, self.clip)
        if count <= 0:
            return False
        self._burst_remaining = count
        return True

    def fire_burst_round(self, final_round, reload_factor=1.0):
        """Consume one armed round and start reload only on the last."""
        if self._burst_remaining <= 0 or self.clip <= 0:
            return False
        expected_final = self._burst_remaining == 1
        if bool(final_round) != expected_final:
            return False
        self.clip -= 1
        self._burst_remaining -= 1
        if not expected_final:
            return True
        self.elapsed = 0.0
        if self.clip <= 0:
            self.clip = 0
            self.reload_kind = 'full'
            self.reload_duration = self.reload_full
        else:
            self.reload_kind = 'intra'
            self.reload_duration = self.reload_intra
        return True

    def cancel_burst(self):
        """Cancel only the automatic tail and enter ordinary recovery."""
        if self._burst_remaining <= 0:
            return False
        self._burst_remaining = 0
        self.elapsed = 0.0
        if self.clip > 0:
            self.reload_kind = 'intra'
            self.reload_duration = self.reload_intra
        else:
            self.reload_kind = 'full'
            self.reload_duration = self.reload_full
        return True

    def remaining(self, reload_factor=1.0):
        duration = self.duration(reload_factor)
        return max(0.0, duration - self.elapsed)


def _bot_ammo_capacity(descriptor):
    """Read the installed vehicle's real ammunition capacity."""
    gun = _value(descriptor, 'gun', {}) or {}
    maximum = _value(descriptor, 'maxAmmo', None)
    if maximum is None:
        maximum = _value(gun, 'maxAmmo', None)
    if maximum is None:
        maximum = _value(_value(descriptor, 'turret', {}), 'maxAmmo', 45)
    try:
        maximum = int(maximum)
    except (TypeError, ValueError, OverflowError):
        maximum = 45
    return max(0, min(maximum, 1000))


def _bot_ammo_categories(profile, shell_count):
    """Classify descriptor-order shells without relying on store prices.

    The pinned descriptor does not expose a stable credits/gold price at this
    seam.  The first non-HE shell is therefore the standard baseline; a later
    non-HE shell is premium only when its representative penetration is at
    least three percent higher.  This keeps standard AP/APCR as the default
    while still recognizing the usual higher-penetration APCR/HEAT round.
    """
    shells = profile.get('shells', ()) if isinstance(profile, dict) else ()
    shells = tuple(shells or ())
    by_index = {}
    for raw in shells:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get('index', -1))
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= index < shell_count:
            by_index[index] = raw
    non_he = []
    categories = {}
    for index in range(shell_count):
        shell = by_index.get(index, {})
        kind = str(shell.get('kind', '') or '').lower()
        is_he = ('high_explosive' in kind or
                 ('explosive' in kind and 'armor_piercing' not in kind))
        if is_he:
            categories[index] = 'he'
        else:
            non_he.append(index)
    baseline = non_he[0] if non_he else None
    baseline_penetration = max(0.0, _number(
        by_index.get(baseline, {}).get('penetration', 0.0)))
    for index in non_he:
        penetration = max(0.0, _number(
            by_index.get(index, {}).get('penetration', 0.0)))
        categories[index] = (
            'premium' if (index != baseline and
                          baseline_penetration > 0.0 and
                          penetration >= baseline_penetration * 1.03)
            else 'standard')
    return categories


def _bot_ammo_distribution(descriptor, profile, shell_count):
    """Allocate one exact, fixed per-battle inventory by shell category."""
    maximum = _bot_ammo_capacity(descriptor)
    if shell_count <= 0 or maximum <= 0:
        return [0] * max(0, shell_count)
    categories = _bot_ammo_categories(profile, shell_count)
    class_tag = str((profile or {}).get('class_tag', ''))
    # Ordinary vehicles use the requested 3:2:1 baseline. Artillery carries a
    # physically HE-led 1:1:4 load; unavailable categories are redistributed
    # across the shells that the installed gun actually exposes.
    category_weights = ({'standard': 1.0, 'premium': 1.0, 'he': 4.0}
                        if class_tag == 'SPG' else
                        {'standard': 3.0, 'premium': 2.0, 'he': 1.0})
    active_categories = sorted(set(categories.values()))
    total_weight = sum(category_weights[name] for name in active_categories)
    if total_weight <= 0.0:
        return [0] * shell_count
    category_counts = dict((name, int(
        maximum * category_weights[name] / total_weight))
        for name in active_categories)
    assigned = sum(category_counts.values())
    remainders = sorted(
        active_categories,
        key=lambda name: (
            -(maximum * category_weights[name] / total_weight -
              category_counts[name]), name))
    for offset in range(maximum - assigned):
        category_counts[remainders[offset % len(remainders)]] += 1
    result = [0] * shell_count
    for name in active_categories:
        indices = sorted(index for index, category in categories.items()
                         if category == name)
        quantity = category_counts[name]
        for offset in range(quantity):
            result[indices[offset % len(indices)]] += 1
    return result


class _BotAmmoState(object):
    """Finite Bot inventory with distinct loaded and planned-next rounds."""

    def __init__(self, descriptor, profile, raw=None):
        gun = _value(descriptor, 'gun', {}) or {}
        shots = _value(gun, 'shots', ()) or ()
        try:
            self.shell_count = max(1, len(shots))
        except TypeError:
            self.shell_count = 1
        self.categories = _bot_ammo_categories(profile, self.shell_count)
        self.remaining = _bot_ammo_distribution(
            descriptor, profile, self.shell_count)
        self.loaded = self._standard_fallback()
        self.next = self.loaded
        self.reload_pending = False
        self.plan_pending = True
        if isinstance(raw, dict):
            self.restore(raw)

    def _standard_fallback(self):
        candidates = [index for index in range(self.shell_count)
                      if self.remaining[index] > 0]
        if not candidates:
            return 0
        standard = [index for index in candidates
                    if self.categories.get(index) == 'standard']
        return standard[0] if standard else candidates[0]

    def _available(self, requested):
        try:
            requested = int(requested)
        except (TypeError, ValueError, OverflowError):
            requested = -1
        if (0 <= requested < self.shell_count and
                self.remaining[requested] > 0):
            return requested
        return self._standard_fallback()

    def restore(self, raw):
        if ('ammo_remaining' not in raw and
                'next_shell_index' not in raw and
                'ammo_reload_pending' not in raw):
            return False
        present = [name in raw for name in (
            'ammo_remaining', 'shell_index', 'next_shell_index',
            'ammo_reload_pending')]
        if any(present) and not all(present):
            raise ValueError('bot ammunition snapshot is incomplete')
        if not all(present):
            return False
        remaining = raw.get('ammo_remaining')
        if (not isinstance(remaining, (list, tuple)) or
                len(remaining) != self.shell_count):
            raise ValueError('bot ammunition inventory shape is invalid')
        parsed = []
        for quantity in remaining:
            try:
                exact = int(quantity)
            except (TypeError, ValueError, OverflowError):
                raise ValueError('bot ammunition quantity is invalid')
            if (isinstance(quantity, bool) or exact < 0 or exact > 1000 or
                    float(quantity) != exact):
                raise ValueError('bot ammunition quantity is invalid')
            parsed.append(exact)
        try:
            loaded = int(raw.get('shell_index'))
            planned = int(raw.get('next_shell_index'))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('bot ammunition selection is invalid')
        if (loaded < 0 or loaded >= self.shell_count or
                planned < 0 or planned >= self.shell_count):
            raise ValueError('bot ammunition selection is invalid')
        reload_pending = raw.get('ammo_reload_pending')
        if not isinstance(reload_pending, bool):
            raise ValueError('bot ammunition reload state is invalid')
        total = sum(parsed)
        if total > 0 and parsed[planned] <= 0:
            raise ValueError('bot planned ammunition is exhausted')
        if total > 0 and not reload_pending and parsed[loaded] <= 0:
            raise ValueError('bot loaded ammunition is exhausted')
        self.remaining = parsed
        self.loaded = loaded
        self.next = planned
        self.reload_pending = reload_pending
        # The canonical snapshot already locks the planned round.  Only a
        # real pending-to-ready reload edge may promote it or choose another.
        self.plan_pending = False
        return True

    def stage(self, requested, ready, full_reload=True):
        """Commit loaded/next choices only at one completed reload edge."""
        if not ready:
            return False
        changed = False
        if self.reload_pending:
            if full_reload:
                selected = self._available(self.next)
                if selected != self.loaded:
                    self.loaded = selected
                    changed = True
            self.reload_pending = False
            self.plan_pending = True
        if self.plan_pending:
            selected = self._available(requested)
            if selected != self.next:
                self.next = selected
                changed = True
            self.plan_pending = False
        return changed

    def can_fire(self, continuing_burst=False):
        return (0 <= self.loaded < self.shell_count and
                self.remaining[self.loaded] > 0 and
                (bool(continuing_burst) or not self.reload_pending))

    def consume_loaded(self, continuing_burst=False):
        if not self.can_fire(continuing_burst):
            return False
        self.remaining[self.loaded] -= 1
        self.next = self._available(self.next)
        self.reload_pending = True
        self.plan_pending = False
        return True

    def planned_rounds(self):
        if 0 <= self.next < len(self.remaining):
            return self.remaining[self.next]
        return 0

    def loaded_shell_requires_full_reload(self):
        return (0 <= self.loaded < len(self.remaining) and
                self.remaining[self.loaded] <= 0 and
                sum(self.remaining) > 0)

    def publish(self, state):
        state['shell_index'] = int(self.loaded)
        state['next_shell_index'] = int(self.next)
        state['ammo_remaining'] = list(self.remaining)
        state['ammo_reload_pending'] = bool(self.reload_pending)


def _effective_shot_dispersion(gun_state, state, descriptor):
    """Return the current dynamic dispersion with critical-state malus."""
    minimum = (float(gun_state.fully_aimed_dispersion) *
               _critical_factor(state, descriptor, 'dispersion'))
    value = max(float(gun_state.dispersion), minimum)
    if math.isnan(value) or math.isinf(value) or value <= 0.0:
        raise ValueError('effective bot shot dispersion must be positive')
    return value


def _dispersed_barrel_angles(bot_id, round_id, fire_seq, yaw, pitch,
                             dispersion_angle, burst_index=0,
                             burst_group_seq=None, base_direction=None):
    """Return the actual physical shot ray used by the battle resolver.

    The 0.8.2 presentation uses negative pitch for a raised barrel.  Protocol
    ``shot_pitch`` is a physical vector elevation (positive is up), matching
    the #1513 projectile/raycast boundary.  A per-shot seed makes authority
    takeover deterministic without sharing ``random`` module state.
    """
    if base_direction is None:
        direction = list(ai_driver.barrel_direction(yaw, pitch))
    else:
        try:
            direction = [float(value) for value in base_direction]
        except (TypeError, ValueError, OverflowError):
            raise ValueError('bot shot direction is unavailable')
        if (len(direction) != 3 or
                any(math.isnan(value) or math.isinf(value)
                    for value in direction)):
            raise ValueError('bot shot direction is unavailable')
        length = math.sqrt(sum(value * value for value in direction))
        if length <= 1.0e-12:
            raise ValueError('bot shot direction is unavailable')
        direction = [value / length for value in direction]
    group_seq = fire_seq if burst_group_seq is None else burst_group_seq
    seed = ((int(_number(round_id)) & 0xffff) * 1000003 +
            (int(bot_id) & 0xffff) * 9176 +
            (int(group_seq) & 0x7fffffff) * 6113 +
            (int(burst_index) & 0xffff) * 3571) & 0x7fffffff
    generator = random.Random(seed)
    try:
        dispersion_angle = float(dispersion_angle)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('bot shot dispersion is unavailable')
    if (math.isnan(dispersion_angle) or math.isinf(dispersion_angle) or
            dispersion_angle <= 0.0):
        raise ValueError('bot shot dispersion must be positive')
    # #1513's aiming circle is a hard two-sigma angular boundary.  The former
    # three independent world-axis Gaussians were unbounded and could publish
    # a bot shell outside the circle used by the player gun and marker.
    sigma = dispersion_angle / 2.0
    radius = abs(generator.gauss(0.0, sigma))
    if radius > dispersion_angle:
        radius = dispersion_angle * generator.uniform(0.0, 1.0)
    azimuth = generator.uniform(0.0, 2.0 * math.pi)

    dx, dy, dz = direction
    if abs(dx) <= abs(dy) and abs(dx) <= abs(dz):
        reference = (1.0, 0.0, 0.0)
    elif abs(dy) <= abs(dz):
        reference = (0.0, 1.0, 0.0)
    else:
        reference = (0.0, 0.0, 1.0)
    tangent = (
        dy * reference[2] - dz * reference[1],
        dz * reference[0] - dx * reference[2],
        dx * reference[1] - dy * reference[0])
    tangent_length = math.sqrt(sum(value * value for value in tangent))
    tangent = tuple(value / tangent_length for value in tangent)
    up = (
        dy * tangent[2] - dz * tangent[1],
        dz * tangent[0] - dx * tangent[2],
        dx * tangent[1] - dy * tangent[0])
    side = tuple(
        tangent[index] * math.cos(azimuth) +
        up[index] * math.sin(azimuth)
        for index in range(3))
    cosine, sine = math.cos(radius), math.sin(radius)
    direction = [
        direction[index] * cosine + side[index] * sine
        for index in range(3)]
    horizontal = math.sqrt(direction[0] * direction[0] +
                           direction[2] * direction[2])
    return (math.atan2(direction[0], direction[2]),
            math.atan2(direction[1], max(1e-9, horizontal)))


def _overlay_live_target_pose(command, target, source_position=None):
    """Replace a low-rate team-spotted order with the current target pose.

    A visible-but-occluded contact still needs a current approach goal. The
    authority's local visibility probe is deliberately not a second fire gate
    here: one ally may spot a target that another ally has the clear barrel
    lane to shoot. The actual lane is probed again immediately before firing.
    """
    result = dict(command)
    if result.get('target_id') is None:
        return result
    if target is None:
        result['fire_allowed'] = False
        return result
    if not isinstance(target, dict):
        raise ValueError('canonical live target must be a record')
    for name in ('alive', 'visible'):
        if name not in target or not isinstance(target[name], bool):
            raise ValueError(
                'canonical live target %s flag is invalid' % name)
    if not target['alive']:
        result['fire_allowed'] = False
        return result
    if 'position' not in target:
        raise ValueError('canonical live target position is unavailable')
    raw_position = target['position']
    if (not isinstance(raw_position, (list, tuple)) or
            len(raw_position) != 3 or
            any(isinstance(value, bool) for value in raw_position)):
        raise ValueError('canonical live target position is invalid')
    try:
        position = tuple(float(raw_position[index]) for index in range(3))
    except (TypeError, ValueError, OverflowError, IndexError):
        raise ValueError('canonical live target position is invalid')
    if any(math.isnan(value) or math.isinf(value) for value in position):
        raise ValueError('canonical live target position must be finite')
    result['aim_position'] = position
    stable_hull_face = result.get('stable_hull_face', False)
    if not isinstance(stable_hull_face, bool):
        raise ValueError('stable hull face flag is invalid')
    angle_degrees = result.get('hull_angle_degrees')
    if angle_degrees is not None:
        try:
            angle_degrees = float(angle_degrees)
            if (math.isnan(angle_degrees) or math.isinf(angle_degrees) or
                    abs(angle_degrees) > 45.0):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise ValueError('hull angle origin is invalid')
    if not stable_hull_face and angle_degrees is None:
        result['face_position'] = position
    elif not stable_hull_face:
        origin = source_position
        if isinstance(origin, dict):
            origin = (origin.get('x'), origin.get('y'), origin.get('z'))
        try:
            origin = tuple(float(origin[index]) for index in range(3))
            if any(math.isnan(value) or math.isinf(value)
                   for value in origin):
                raise ValueError
            angle = math.radians(angle_degrees)
            dx = position[0] - origin[0]
            dz = position[2] - origin[2]
            cosine = math.cos(angle)
            sine = math.sin(angle)
            result['face_position'] = (
                origin[0] + dx * cosine - dz * sine,
                origin[1],
                origin[2] + dx * sine + dz * cosine)
        except (TypeError, ValueError, OverflowError, IndexError):
            raise ValueError('hull angle origin is invalid')
    if result.get('combat_mode') == 'advance_contact':
        result['move_position'] = position
    return result


def _server_order_signature(order):
    """Return the strategic part of one server order.

    The server deliberately excludes a live target's aim/face position (and
    the moving advance goal) from its order revision signature.  Those values
    are overlaid from the current target pose every render frame, so treating
    them as a new strategic decision only flushes valid perception caches.
    """
    result = dict(order or {})
    if result.get('target_id') is not None:
        result.pop('aim_position', None)
        if not result.get('stable_hull_face', False):
            result.pop('face_position', None)
        if result.get('combat_mode') == 'advance_contact':
            result.pop('move_position', None)
    return result


class BotRuntime(object):
    """Produces v5 ``bot_manifest`` and ``bot_state`` payloads without entities."""

    @staticmethod
    def _adapt_direction_probe(probe):
        """Adapt injected legacy test probes once, never after side effects.

        The pinned production callback has five arguments. Catching TypeError
        around every invocation used to mistake an exception from inside that
        callback for an old arity and execute it again, which is unsafe for a
        native collision seam. Python functions expose an exact code object;
        opaque/native callables must honor the current five-argument contract.
        """
        target = getattr(
            probe, 'im_func', getattr(probe, '__func__', probe))
        code = getattr(target, 'func_code', getattr(target, '__code__', None))
        if code is None:
            return probe
        argument_count = int(code.co_argcount)
        bound_self = getattr(
            probe, 'im_self', getattr(probe, '__self__', None))
        if bound_self is not None:
            argument_count -= 1
        has_varargs = bool(code.co_flags & 0x04)
        if has_varargs or argument_count >= 5:
            return probe
        if argument_count == 4:
            return lambda position, yaw, speed, descriptor, \
                    unused_maximum_distance: probe(
                        position, yaw, speed, descriptor)
        if argument_count == 3:
            return lambda position, yaw, speed, unused_descriptor, \
                    unused_maximum_distance: probe(position, yaw, speed)
        if argument_count == 2:
            return lambda position, yaw, unused_speed, unused_descriptor, \
                    unused_maximum_distance: probe(position, yaw)
        raise ValueError('direction probe must accept 2, 3, 4 or 5 arguments')

    @staticmethod
    def _adapt_world_receipt_probe(probe):
        """Add the optional local-target distance to legacy receipt probes."""
        target = getattr(
            probe, 'im_func', getattr(probe, '__func__', probe))
        code = getattr(target, 'func_code', getattr(target, '__code__', None))
        if code is None:
            return probe
        argument_count = int(code.co_argcount)
        bound_self = getattr(
            probe, 'im_self', getattr(probe, '__self__', None))
        if bound_self is not None:
            argument_count -= 1
        if bool(code.co_flags & 0x04) or argument_count >= 5:
            return probe
        if argument_count == 4:
            return lambda position, yaw, speed, descriptor, \
                    unused_maximum_distance: probe(
                        position, yaw, speed, descriptor)
        raise ValueError('world receipt probe must accept 4 or 5 arguments')

    @staticmethod
    def _adapt_friendly_lane_probe(probe):
        """Adapt the former two-argument internal seam once at startup."""
        target = getattr(
            probe, 'im_func', getattr(probe, '__func__', probe))
        code = getattr(target, 'func_code', getattr(target, '__code__', None))
        if code is None:
            return probe
        argument_count = int(code.co_argcount)
        bound_self = getattr(
            probe, 'im_self', getattr(probe, '__self__', None))
        if bound_self is not None:
            argument_count -= 1
        if bool(code.co_flags & 0x04) or argument_count >= 5:
            return probe
        if argument_count == 2:
            return lambda source, target, unused_descriptor, unused_shell, \
                    unused_launch: probe(source, target)
        raise ValueError(
            'friendly lane probe must accept 2 or at least 5 arguments')

    def __init__(self, local_player_id, descriptor_resolver=None,
                 player_descriptor_resolver=None,
                 direction_probe=None, adapter_factory=None,
                 vehicle_selector=None, visibility_probe=None,
                 firing_lane_probe=None, friendly_lane_probe=None,
                 direct_launch_origin_probe=None,
                 ballistic_solution_probe=None,
                 artillery_launch_probe=None,
                 artillery_friendly_lane_probe=None,
                 artillery_launch_cancel=None,
                 spawn_resolver=None, ground_probe=None,
                 physics_ground_probe=None,
                 obstacle_probe=None, bounds=None, cover_probe=None,
                 native_motion=False, baked_graph=None, probe_clock=None,
                 motion_resolver=None, motion_report=None,
                 world_receipt_probe=None, probe_timing_seconds=0.0,
                 water_depth_probe=None, ram_contact_probe=None,
                 bot_equipment_resolver=None, control_seconds=None):
        self.local_player_id = local_player_id
        self.descriptor_resolver = descriptor_resolver or (lambda unused: {})
        self.player_descriptor_resolver = player_descriptor_resolver
        self.direction_probe = self._adapt_direction_probe(
            direction_probe or (lambda *unused: True))
        self.adapter_factory = adapter_factory or BotAdapter
        self.vehicle_selector = vehicle_selector or (
            lambda raw: raw.get('vehicle') or 'ussr:R11_MS-1')
        self.visibility_probe = visibility_probe or (
            lambda unused_source, unused_target: True)
        # The production #1513 adapter uses the same static collision ray for
        # spotting and shooting, but they are separate decisions and caches.
        # Keeping an explicit seam also makes it impossible for a stale team
        # spot to stand in for a current clear barrel lane.
        self.firing_lane_probe = firing_lane_probe or self.visibility_probe
        # Dynamic allied hulls are deliberately outside the cached static-world
        # lane.  Production checks this seam again at every final fire attempt.
        self.friendly_lane_probe = self._adapt_friendly_lane_probe(
            friendly_lane_probe or (
                lambda unused_source, unused_target: True))
        # Production freezes the exact native HP_gunFire transform before the
        # friendly-hull proof.  The logical fallback keeps the pure runtime
        # usable in engine-free tests; BattleRuntime always injects the native
        # boundary.
        self.direct_launch_origin_probe = (
            direct_launch_origin_probe or
            (lambda source, unused_descriptor, unused_shell,
             unused_fire_seq, unused_yaw, unused_pitch,
             unused_flight_time: (
                 _number(source.get('x')),
                 _number(source.get('y')) + 1.5,
                 _number(source.get('z')))))
        # SPG solutions are completed by BattleRuntime's bounded native arc
        # queue.  Returning None means pending or fail-closed; a dict is a
        # fully probed physical solution shared by aiming and firing.
        self.ballistic_solution_probe = ballistic_solution_probe
        # BattleRuntime owns the native HP_gunFire origin. This second seam
        # publishes a frozen receipt only after the next deterministic,
        # dispersed SPG trajectory itself has passed the bounded arc queue.
        self.artillery_launch_probe = artillery_launch_probe
        # The exact receipt carries that same proved path.  Production checks
        # live allied hulls against it immediately before committing the shot.
        self.artillery_friendly_lane_probe = (
            artillery_friendly_lane_probe or
            (lambda unused_source, unused_target, unused_descriptor,
             unused_shell, unused_receipt: True))
        self.artillery_launch_cancel = artillery_launch_cancel
        self.spawn_resolver = spawn_resolver
        self._injected_baked_graph = baked_graph
        self.baked_graph = None
        self._navigation_map_name = None
        self._navigation_error = None
        self._ground_probe = ground_probe
        self._physics_ground_probe = physics_ground_probe
        self._obstacle_probe = obstacle_probe
        self._navigation_bounds = bounds
        self.navigator = (TerrainNavigator(
            ground_probe, obstacle_probe, bounds, 18.0)
            if callable(ground_probe) else None)
        self.cover_probe = cover_probe
        self.native_motion = bool(native_motion)
        self.motion_resolver = motion_resolver
        self.motion_report = motion_report
        self.world_receipt_probe = (
            self._adapt_world_receipt_probe(world_receipt_probe)
            if callable(world_receipt_probe) else None)
        self.ram_contact_probe = (
            ram_contact_probe if callable(ram_contact_probe) else None)
        self.bot_equipment_resolver = (
            bot_equipment_resolver if callable(bot_equipment_resolver) else
            (lambda: ()))
        self._fixed_control = control_seconds is not None
        self._control_seconds = max(
            0.001, _number(control_seconds, PUBLICATION_SECONDS))
        self._water_depth_probe = (
            water_depth_probe if callable(water_depth_probe) else
            (lambda unused_position: -1.0))
        probe_clock = probe_clock if callable(probe_clock) else None
        self._probe_timing_seconds = max(
            0.0, _number(probe_timing_seconds))
        self._probe_timing_deadline = None
        self._probe_clock_pending = None
        if probe_clock is not None and self._probe_timing_seconds > 0.0:
            # A bounded timing window starts only on the first accepted
            # authority tick. Countdown prewarm and an idle worker therefore
            # cannot consume the useful sample before BotRuntime does work.
            self._probe_clock = None
            self._probe_clock_pending = probe_clock
            self._probe_timing_state = 'pending'
        else:
            # Preserve the injected-test and explicit unbounded-probe contract.
            self._probe_clock = probe_clock
            self._probe_timing_state = (
                'unbounded' if probe_clock is not None else 'off')
        self.adapter = None
        self.authority_id = None
        self.round_id = None
        self.states = {}
        self._accumulator = 0.0
        # This is the pose's simulation clock, not the wall clock observed
        # after native probes and JSON preparation have finished.  The server
        # maps it onto its own motion epoch so variable worker execution time
        # cannot be mistaken for a variable vehicle speed.
        self._sample_time_us = 0
        self._manifest_sent = False
        self._pending_manifest = None
        self._pending_manifest_round_id = None
        self._pending_manifest_authority_id = None
        self._descriptor_pairs = {}
        self._descriptors = {}
        self._gun_yaw_limits = {}
        self._gun_states = {}
        self._ammo_states = {}
        self._burst_states = {}
        self._equipment_states = {}
        self._equipment_passives = {}
        self._equipment_now = 0.0
        self._pending_launches = []
        self._pending_launch_keys = {}
        self._pending_launch_by_bot = {}
        self._artillery_intents = {}
        self._artillery_reproofs = {}
        self._ballistic_solution_cache = {}
        self._friendly_repositions = {}
        self._shot_los_cache = {}
        self._shot_los_deadlines = {}
        self._physics_params = {}
        self._repair_factors = {}
        self._vision_ranges = {}
        self._source_still = {}
        self._player_vehicle_profiles = {}
        self._player_collision_profiles = {}
        self._spotting_profiles = {}
        self._visibility_fire = {}
        self._visibility_still = {}
        self._turn_speeds = {}
        self._hard_contact_grinds = {}
        self._ram_cooldowns = {}
        self._human_ram_cooldowns = {}
        self._ram_contacts = frozenset()
        self._ram_seq = 0
        self._human_ram_receipt_seq = {}
        self._human_ram_report_cache = {}
        self.finished = False
        self._visibility_cache = {}
        self._team_visibility_cache = {}
        self._visible_target_poses = {}
        self._spot_until = {}
        self._human_observer_alive = {}
        self._human_last_alive_critical = {}
        self._human_direct_targets = {}
        self._human_vengeance_until = {}
        self._server_orders = {}
        self._server_order_tokens = {}
        self._order_revision = -1
        self._next_observation = 0.0
        self._next_shot_lane_refresh = 0.0
        self._next_cover_refresh = 0.0
        self._next_publication = 0.0
        self._pending_ram_reports = []
        self._cover_cursor = 0
        self._cover_queue = []
        self._cover_results = []
        self._decision_cache = {}
        self._motion_probe_cache = {}
        self._traffic_stopping_cache = {}
        self._slope_pose_cursor = 0
        self._flip_diary = {}
        self.debug_logging = False
        self._camera_position = None
        self._world_receipt_budget = 0
        self._world_receipt_waiting = []
        self._world_receipt_frame = None
        self._prewarm_receipt_cursor = 0
        self._combat_sync = {}
        self._server_tick = -1
        # These monotonic, pull-only totals are diagnostic data.  They never
        # enter a LAN payload or feed a scheduler/cache decision.
        self._probe_totals = [0, 0, 0, 0, 0]
        self._probe_duration_totals = [0.0, 0.0, 0.0, 0.0, 0.0]
        self._alive_bot_ticks = 0
        self._decision_counts = {}

    def probe_totals(self):
        """Return logical native-query totals without resetting any state."""
        return tuple(self._probe_totals)

    def load_report(self):
        """Return the busiest planners of this window and reset the counts."""
        busiest = sorted(self._decision_counts.items(),
                         key=lambda item: -item[1])[:5]
        self._decision_counts = {}
        return {'busiest': tuple(busiest)}

    def probe_duration_totals(self):
        """Return measured query time without resetting or driving work."""
        return tuple(self._probe_duration_totals)

    def probe_timing_state(self):
        """Describe the bounded native-query timer without advancing it."""
        return self._probe_timing_state

    def _advance_probe_timing(self, now):
        """Start and expire the optional bounded timer on authority time."""
        if self._probe_clock_pending is not None:
            self._probe_clock = self._probe_clock_pending
            self._probe_clock_pending = None
            self._probe_timing_deadline = (
                float(now) + self._probe_timing_seconds)
            self._probe_timing_state = 'active'
        elif (self._probe_timing_deadline is not None and
              float(now) + 1e-9 >= self._probe_timing_deadline):
            self._probe_clock = None
            self._probe_timing_deadline = None
            self._probe_timing_state = 'complete'

    def diagnostic_totals(self):
        """Return counters which never participate in simulation decisions."""
        return {'alive_bot_ticks': int(self._alive_bot_ticks)}

    def _probe_started(self):
        if self._probe_clock is None:
            return None
        try:
            return float(self._probe_clock())
        except Exception:
            # Diagnostics must never change or terminate gameplay.
            self._probe_clock = None
            self._probe_clock_pending = None
            self._probe_timing_deadline = None
            self._probe_timing_state = 'failed'
            return None

    def _probe_finished(self, index, started):
        if started is None or self._probe_clock is None:
            return
        try:
            elapsed = float(self._probe_clock()) - float(started)
            if (elapsed > 0.0 and not math.isnan(elapsed) and
                    not math.isinf(elapsed)):
                self._probe_duration_totals[index] += elapsed
        except Exception:
            self._probe_clock = None
            self._probe_clock_pending = None
            self._probe_timing_deadline = None
            self._probe_timing_state = 'failed'

    def is_authority(self):
        return self.authority_id == self.local_player_id

    def _ensure_navigation_graph(self, map_name):
        """Install a matching immutable graph after the battle map is known."""
        map_name = tactical_maps.normalize_map_name(map_name)
        if not map_name:
            raise ValueError('battle map name is unavailable')
        if self._navigation_map_name == map_name:
            return
        if map_name not in prebaked_navigation.SUPPORTED_MAPS:
            raise ValueError(
                'standard battle map is not supported: %s' % map_name)
        graph = None
        injected = self._injected_baked_graph
        if isinstance(injected, dict):
            injected_name = tactical_maps.normalize_map_name(
                injected.get('map'))
            if injected_name == map_name:
                graph = injected
        if graph is None:
            graph = prebaked_navigation.load_graph(map_name)
        if graph is None:
            raise ValueError(
                'required navigation graph is missing for %s' % map_name)
        prebaked_navigation._validate(graph, map_name)
        self._validated_baked_routes(graph)
        if not callable(self._ground_probe):
            raise ValueError(
                'navigation ground probe is unavailable for %s' % map_name)
        if not callable(self._physics_ground_probe):
            raise ValueError(
                'physics ground probe is unavailable for %s' % map_name)
        try:
            navigator = TerrainNavigator(
                self._ground_probe, self._obstacle_probe,
                self._navigation_bounds, 18.0, baked_graph=graph)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(
                'required navigation graph cannot be installed for %s: %s' %
                (map_name, error))
        self._navigation_map_name = map_name
        self._navigation_error = None
        self.baked_graph = graph
        self.navigator = navigator

    @staticmethod
    def _validated_baked_routes(graph):
        routes = graph.get('routes') if isinstance(graph, dict) else None
        if not isinstance(routes, dict):
            raise ValueError('navigation graph routes are missing')
        for team in (1, 2):
            values = routes.get(str(team), routes.get(team))
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(
                    'navigation graph routes are missing for team %d' % team)
            for route in values:
                if not isinstance(route, dict):
                    raise ValueError('navigation graph route is invalid')
                waypoints = route.get('waypoints')
                if (not isinstance(waypoints, (list, tuple)) or
                        not waypoints or len(waypoints) > 16):
                    raise ValueError(
                        'navigation graph route waypoint count is invalid')
                for point in waypoints:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    try:
                        x = float(point[0])
                        z = float(point[1])
                    except (TypeError, ValueError, IndexError):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
                    if (x != x or z != z or abs(x) == float('inf') or
                            abs(z) == float('inf')):
                        raise ValueError(
                            'navigation graph route waypoint is invalid')
        return routes

    def _new_adapter(self, map_name, round_id):
        """Keep custom two-argument factories compatible with the graph seam."""
        baked_routes = (self.baked_graph or {}).get('routes')
        if (not isinstance(baked_routes, dict) or not any(
                baked_routes.get(key) for key in (1, 2, '1', '2'))):
            return self.adapter_factory(map_name, round_id)
        if self.adapter_factory is BotAdapter:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        try:
            return self.adapter_factory(map_name, round_id,
                                        baked_routes=baked_routes)
        except TypeError:
            return self.adapter_factory(map_name, round_id)

    def _clear(self, position, yaw, speed=0.0, descriptor=None):
        """Treat collision, excessive slope and water as a failed local ray."""
        return self._probe_is_clear(
            self._probe_direction(position, yaw, speed, descriptor))

    def _probe_direction(self, position, yaw, speed=0.0, descriptor=None,
                         maximum_distance=None):
        """Return one canonical direction sample for planning and physics."""
        self._probe_totals[4] += 1
        probe_started = self._probe_started()
        try:
            result = self.direction_probe(
                position, yaw, speed, descriptor, maximum_distance)
        except Exception:
            return {'clear': False, 'collision': True,
                    'water': False, 'slope': 0.0}
        finally:
            self._probe_finished(4, probe_started)
        return result

    def _begin_world_receipt_frame(self):
        """Reserve receipt work for previously eligible deferred Bots."""
        waiting = []
        seen = set()
        for entry in self._world_receipt_waiting:
            bot_id, uncached = entry
            bot_id = int(bot_id)
            state = self.states.get(bot_id)
            if (bot_id not in seen and state is not None and
                    state.get('alive', True)):
                waiting.append((bot_id, bool(uncached)))
                seen.add(bot_id)
        initial_waiting = [
            bot_id for bot_id, initial in waiting if initial]
        priority_source = (initial_waiting if initial_waiting else
                           [bot_id for bot_id, unused in waiting])
        self._world_receipt_budget = MAX_WORLD_RECEIPTS_PER_FRAME
        self._world_receipt_frame = {
            'waiting': tuple(waiting),
            'waiting_initial': dict(waiting),
            'priority': set(
                priority_source[:MAX_WORLD_RECEIPTS_PER_FRAME]),
            'initial_first': bool(initial_waiting),
            'requested': [],
            'requested_set': set(),
            'request_uncached': {},
            'attempted': set(),
            'attempt_results': {},
            'attempt_deferred': [],
        }

    def _finish_world_receipt_frame(self):
        """Rotate real deferred requests without retaining ineligible Bots."""
        frame = self._world_receipt_frame
        if not isinstance(frame, dict):
            return
        requested = frame['requested_set']
        attempted = frame['attempted']
        next_waiting = []
        seen = set()

        def append_once(bot_id, uncached):
            bot_id = int(bot_id)
            if bot_id not in seen:
                next_waiting.append((bot_id, bool(uncached)))
                seen.add(bot_id)

        # Preserve the established queue for eligible requests which did not
        # receive a native job. New requests follow in encounter order. A Bot
        # whose native callback itself deferred rotates behind both cohorts.
        request_uncached = frame['request_uncached']
        for bot_id, unused_previous_uncached in frame['waiting']:
            if bot_id in requested and bot_id not in attempted:
                append_once(bot_id, request_uncached.get(bot_id, False))
        for bot_id in frame['requested']:
            if bot_id not in attempted:
                append_once(bot_id, request_uncached.get(bot_id, False))
        deferred = set(frame['attempt_deferred'])
        for bot_id, unused_previous_uncached in frame['waiting']:
            if bot_id in deferred:
                append_once(bot_id, False)
        for bot_id in frame['requested']:
            if bot_id in deferred:
                append_once(bot_id, False)
        self._world_receipt_waiting = next_waiting
        self._world_receipt_frame = None

    def _probe_world_receipt(self, bot_id, position, yaw, speed, descriptor,
                             uncached, maximum_distance=None):
        """Run one read-only exact-hull proof for the selected travel ray."""
        if not callable(self.world_receipt_probe):
            return None
        bot_id = int(bot_id)
        frame = self._world_receipt_frame
        if not isinstance(frame, dict):
            return 'deferred'
        if bot_id not in frame['requested_set']:
            frame['requested'].append(bot_id)
            frame['requested_set'].add(bot_id)
            waiting_initial = frame['waiting_initial']
            frame['request_uncached'][bot_id] = (
                waiting_initial[bot_id] if bot_id in waiting_initial else
                bool(uncached))
        if bot_id in frame['attempted']:
            # One render callback may contain several catch-up slices. Never
            # multiply native receipt work for the same Bot inside that one
            # callback. Preserve a proved hard result; every other result can
            # safely use the already-complete generic corridor until the next
            # render callback refreshes the exact optimisation.
            return (False if frame['attempt_results'].get(bot_id) is False
                    else 'deferred')
        priority = frame['priority']
        if (frame['initial_first'] and
                not frame['request_uncached'][bot_id]):
            return 'deferred'
        if priority and bot_id not in priority:
            return 'deferred'
        priority.discard(bot_id)
        if self._world_receipt_budget <= 0:
            return 'deferred'
        self._world_receipt_budget -= 1
        frame['attempted'].add(bot_id)
        result = self._call_world_receipt_probe(
            position, yaw, speed, descriptor, maximum_distance)
        frame['attempt_results'][bot_id] = result
        if result == 'deferred':
            frame['attempt_deferred'].append(bot_id)
        return result

    def _call_world_receipt_probe(self, position, yaw, speed, descriptor,
                                  maximum_distance=None):
        """Run one measured exact receipt without changing queue ownership."""
        if not callable(self.world_receipt_probe):
            return None
        self._probe_totals[4] += 1
        probe_started = self._probe_started()
        try:
            result = self.world_receipt_probe(
                position, yaw, speed, descriptor, maximum_distance)
        except Exception:
            # A receipt is only an optimisation.  Its absence restores the
            # authoritative per-frame world sweep and never grants movement.
            return None
        finally:
            self._probe_finished(4, probe_started)
        return result

    def prewarm_world_receipts(self, now):
        """Pre-prove one stationary forward corridor during the countdown.

        This method never plans, integrates, publishes or grants motion.  A
        successful receipt is retained with an already-expired generic sample,
        so the first live travel tick must still run the complete native
        direction probe before it may consume the exact contained receipt.
        """
        if (not self.is_authority() or self.adapter is None or self.finished or
                not callable(self.world_receipt_probe)):
            return False
        states = [state for state in self._ordered_states()
                  if state.get('alive', True)]
        if not states:
            return False
        start = self._prewarm_receipt_cursor % len(states)
        for visited in range(len(states)):
            index = (start + visited) % len(states)
            state = states[index]
            cached = self._motion_probe_cache.get(state['id'])
            result = ((cached or {}).get('result')
                      if isinstance(cached, dict) else None)
            if isinstance(result, dict) and isinstance(
                    result.get('world_receipt'), dict):
                continue
            self._prewarm_receipt_cursor = (index + 1) % len(states)
            position = _position(state)
            yaw = _number(state.get('yaw'))
            descriptor = self._descriptors.get(state['id'])
            # Presentation-only suspension is safe to sample at the baked
            # spawn pose before the first grounded simulation tick.  No pose,
            # velocity or integration state is advanced here.
            self._update_slope_pose(state, allow_ungrounded=True)
            direction = self._probe_direction(
                position, yaw, 0.0, descriptor)
            if (not isinstance(direction, dict) or
                    not self._probe_is_clear(direction) or
                    direction.get('deferred', False) or
                    abs(_number(direction.get('slope'))) > 0.01):
                return False
            receipt = self._call_world_receipt_probe(
                position, yaw, 0.000001, descriptor)
            if not self._world_receipt_contains(
                    receipt, position, yaw, 0.000001, 0.1):
                return False
            direction = dict(direction)
            direction['world_receipt'] = receipt
            # Expire the generic proof immediately.  Only the exact static
            # corridor survives countdown time; live movement re-probes its
            # selected direction natively before using this receipt.
            self._motion_probe_cache[state['id']] = {
                'result': direction,
                'position': position,
                'yaw': yaw,
                'deadline': _number(now),
            }
            return True
        return False

    @staticmethod
    def _probe_is_clear(result):
        if isinstance(result, dict):
            if not result.get('clear', True) or result.get('collision', False):
                return False
            if result.get('water', False):
                return False
            return abs(_number(result.get('slope', 0.0))) <= 0.55
        return bool(result)

    def _install_bot_descriptor(self, bot_id, state, siege_state):
        """Install one bot's active immutable mode descriptor."""
        bot_id = int(bot_id)
        pair = self._descriptor_pairs.get(bot_id)
        if pair is None:
            return False
        descriptor = siege_mechanics.active_descriptor(pair, siege_state)
        previous = self._descriptors.get(bot_id)
        self._descriptors[bot_id] = descriptor
        if previous is descriptor:
            return False
        self._physics_params[bot_id] = vehicle_physics.derive_params(
            descriptor)
        self._gun_yaw_limits[bot_id] = ai_driver.gun_yaw_limits(descriptor)
        gun_state = self._gun_states.get(bot_id)
        if gun_state is not None:
            gun_state.adopt_descriptor(descriptor)
        self._repair_factors.pop(bot_id, None)
        self._vision_ranges.pop(bot_id, None)
        self._spotting_profiles.pop(('bot', bot_id), None)
        self._motion_probe_cache.pop(bot_id, None)
        self._traffic_stopping_cache.pop(bot_id, None)
        self._cancel_artillery_intent(bot_id)
        if state is not None:
            half_length, half_width = _hull_dimensions(descriptor)
            state['move_speed'] = _forward_speed(descriptor)
            state['view_range'] = self._cache_vision_range(
                bot_id, descriptor)
            state['half_length'] = half_length
            state['half_width'] = half_width
            state['collision_shape'] = _collision_shape(descriptor)
            state['mass'] = self._physics_params[bot_id]['mass']
            state['ram_profile'] = tank_collision.descriptor_ram_profile(
                descriptor)
        return True

    def _bot_equipment_contracts(self, snapshots=None):
        raw = tuple(self.bot_equipment_resolver() or ())
        contracts = ()
        if raw:
            contracts = equipment_mechanics.bot_consumable_contracts({
                'botConsumables': raw})
        if snapshots is not None:
            restored_contracts = \
                equipment_mechanics.bot_consumable_contracts(
                    None, snapshot=snapshots)
            if contracts and restored_contracts != contracts:
                raise ValueError('bot equipment contracts changed')
            if not contracts:
                contracts = restored_contracts
        return contracts

    def _install_bot_equipments(self, bot_id, snapshots=None,
                                canonical_restore=False):
        """Create one independent exact consumable ledger for each bot."""
        bot_id = int(bot_id)
        contracts = self._bot_equipment_contracts(snapshots)
        existing = self._equipment_states.get(bot_id)
        if existing is not None:
            existing_contracts = tuple(
                value.contract for value in existing)
            if contracts and existing_contracts != contracts:
                raise ValueError('bot equipment contracts changed')
            if snapshots is not None:
                restored = equipment_mechanics.restore_equipment_states(
                    snapshots, contracts=existing_contracts,
                    now=self._equipment_now)
                if canonical_restore:
                    # A server-issued authority handoff is an ownership
                    # boundary. Discard any locally consumed but unacknowledged
                    # item state from the former authority interval.
                    existing = restored
                    self._equipment_states[bot_id] = existing
        elif snapshots is None:
            existing = [equipment_mechanics.EquipmentState(
                contract, self._equipment_now) for contract in contracts]
            self._equipment_states[bot_id] = existing
        else:
            existing = equipment_mechanics.restore_equipment_states(
                snapshots, contracts=contracts, now=self._equipment_now)
            self._equipment_states[bot_id] = existing
        states = self._equipment_states.get(bot_id, ())
        self._equipment_passives[bot_id] = \
            equipment_mechanics.passive_effects(states)
        return states

    def _advance_equipment_clock(self, step):
        """Advance cooldowns on simulation time, never wall-clock time."""
        self._equipment_now += max(0.0, _number(step))
        return self._equipment_now

    def _publish_equipment_state(self, state):
        states = self._equipment_states.get(int(state['id']), ())
        state['equipment_states'] = [
            equipment.snapshot(self._equipment_now) for equipment in states]
        return True

    def _observe_bot_stun(self, state, raw, server_time_ms):
        """Anchor one server-owned stun end time to the worker sim clock."""
        raw = raw if isinstance(raw, dict) else {}
        try:
            end = int(raw.get('stun_end_server_time_ms', 0))
            observed = int(server_time_ms)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('bot stun clock is invalid')
        if (isinstance(raw.get('stun_end_server_time_ms', 0), bool) or
                isinstance(server_time_ms, bool) or end < 0 or observed < 0):
            raise ValueError('bot stun clock is invalid')
        state['stun_end_server_time_ms'] = end
        state['_stun_until_equipment_time'] = (
            self._equipment_now + max(0.0, (end - observed) / 1000.0))
        return end > observed

    def _bot_stunned(self, state):
        return bool(
            state.get('alive', False) and
            _number(state.get('_stun_until_equipment_time')) >
            self._equipment_now + 1.0e-9)

    def bot_equipment_passives(self, bot_id):
        return dict(self._equipment_passives.get(
            int(bot_id), equipment_mechanics.passive_effects(())))

    def _set_bot_siege_state(self, state, siege_state, duration=0.0,
                             transition_total=None):
        siege_state = int(siege_state)
        duration = max(0.0, float(duration))
        if transition_total is None:
            transition_total = duration
        transition_total = max(0.0, float(transition_total))
        if siege_state not in (siege_mechanics.SWITCHING_ON,
                               siege_mechanics.SWITCHING_OFF):
            duration = 0.0
            transition_total = 0.0
        state['siege_state'] = siege_state
        state['_siege_time_left'] = duration
        state['_siege_transition_total'] = transition_total
        state['siege_time_left_ms'] = (
            int(math.ceil(duration * 1000.0 - 1.0e-9))
            if duration > 0.0 else 0)
        state['siege_transition_total_ms'] = (
            int(math.ceil(transition_total * 1000.0 - 1.0e-9))
            if transition_total > 0.0 else 0)
        self._install_bot_descriptor(
            int(state['id']), state, siege_state)
        return True

    def _advance_bot_siege(self, state, step):
        pair = self._descriptor_pairs.get(int(state['id']))
        if pair is None or pair[1] is None:
            self._set_bot_siege_state(state, siege_mechanics.DISABLED)
            return False
        current = int(state.get(
            'siege_state', siege_mechanics.DISABLED))
        if current not in (siege_mechanics.SWITCHING_ON,
                           siege_mechanics.SWITCHING_OFF):
            state['_siege_time_left'] = 0.0
            state['siege_time_left_ms'] = 0
            state['_siege_transition_total'] = 0.0
            state['siege_transition_total_ms'] = 0
            return False
        remaining = max(
            0.0, _number(state.get('_siege_time_left')) - float(step))
        if remaining > 1.0e-9:
            state['_siege_time_left'] = remaining
            state['siege_time_left_ms'] = int(math.ceil(
                remaining * 1000.0 - 1.0e-9))
            return True
        final_state = (
            siege_mechanics.ENABLED
            if current == siege_mechanics.SWITCHING_ON else
            siege_mechanics.DISABLED)
        self._set_bot_siege_state(state, final_state)
        state['_siege_intent'] = (final_state == siege_mechanics.ENABLED)
        state['_siege_intent_elapsed'] = 0.0
        return True

    @staticmethod
    def _siege_desired(state, command, target):
        legal_target = bool(
            isinstance(target, dict) and target.get('alive', True))
        destination = _point(
            command.get('move_position'), _position(state))
        long_travel = bool(
            command.get('movement_intent', abs(_number(
                command.get('throttle'))) > 0.05) and
            _distance(_position(state), destination) >
            SIEGE_LONG_TRAVEL_METRES)
        return legal_target and not long_travel

    def _update_bot_siege_intent(self, state, command, target, step):
        pair = self._descriptor_pairs.get(int(state['id']))
        if pair is None or pair[1] is None:
            return False
        burst_state = self._burst_states.get(int(state['id']))
        if burst_state is not None and burst_state.active:
            # One native trigger owns the complete physical group.  A mode
            # descriptor may not change between its automatic rounds.
            state['_siege_intent_elapsed'] = 0.0
            return False
        current = int(state.get(
            'siege_state', siege_mechanics.DISABLED))
        switching = current in (siege_mechanics.SWITCHING_ON,
                                siege_mechanics.SWITCHING_OFF)
        if switching:
            command['fire_allowed'] = False
        desired = self._siege_desired(state, command, target)
        previous_desired = state.get('_siege_intent')
        if previous_desired is None or bool(previous_desired) != desired:
            state['_siege_intent'] = desired
            state['_siege_intent_elapsed'] = 0.0
            return switching
        same_transition_direction = (
            (current == siege_mechanics.SWITCHING_ON and desired) or
            (current == siege_mechanics.SWITCHING_OFF and not desired))
        if same_transition_direction:
            state['_siege_intent_elapsed'] = 0.0
            return True
        elapsed = _number(state.get('_siege_intent_elapsed')) + float(step)
        state['_siege_intent_elapsed'] = elapsed
        threshold = (SIEGE_ENABLE_DEBOUNCE_SECONDS if desired else
                     SIEGE_DISABLE_DEBOUNCE_SECONDS)
        already_desired = (
            (desired and current == siege_mechanics.ENABLED) or
            (not desired and current == siege_mechanics.DISABLED))
        if already_desired or elapsed + 1.0e-9 < threshold:
            return False
        unused_devices, destroyed, unused_crew, yellow = \
            _critical_parts(state)
        if 'engineHealth' in destroyed:
            state['_siege_intent_elapsed'] = 0.0
            return False
        next_state, remaining, transition_total, changed = \
            siege_mechanics.request_transition(
                current, state.get('_siege_time_left', 0.0),
                state.get('_siege_transition_total', 0.0),
                state.get('vehicle', ''), desired,
                'engineHealth' in yellow)
        if changed:
            self._set_bot_siege_state(
                state, next_state, remaining, transition_total)
        command['fire_allowed'] = False
        state['_siege_intent_elapsed'] = 0.0
        return changed or switching

    def battle_start(self, message):
        """Build a local authority manifest from the server roster once per round."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if round_id != self.round_id:
            self.round_id = round_id
            self.states = {}
            self._accumulator = 0.0
            self._sample_time_us = 0
            self._manifest_sent = False
            self._pending_manifest = None
            self._pending_manifest_round_id = None
            self._pending_manifest_authority_id = None
            self._descriptor_pairs = {}
            self._descriptors = {}
            self._gun_yaw_limits = {}
            self._gun_states = {}
            self._ammo_states = {}
            self._burst_states = {}
            self._equipment_states = {}
            self._equipment_passives = {}
            self._equipment_now = 0.0
            self._pending_launches = []
            self._pending_launch_keys = {}
            self._pending_launch_by_bot = {}
            self._clear_artillery_intents()
            self._ballistic_solution_cache = {}
            self._friendly_repositions = {}
            self._shot_los_cache = {}
            self._shot_los_deadlines = {}
            self._physics_params = {}
            self._repair_factors = {}
            self._vision_ranges = {}
            self._source_still = {}
            self._player_vehicle_profiles = {}
            self._player_collision_profiles = {}
            self._spotting_profiles = {}
            self._visibility_fire = {}
            self._visibility_still = {}
            self._turn_speeds = {}
            self._hard_contact_grinds = {}
            self._ram_cooldowns = {}
            self._human_ram_cooldowns = {}
            self._ram_contacts = frozenset()
            self._ram_seq = 0
            self._human_ram_receipt_seq = {}
            self._human_ram_report_cache = {}
            self.adapter = None
            self.finished = False
            self._visibility_cache = {}
            self._team_visibility_cache = {}
            self._visible_target_poses = {}
            self._spot_until = {}
            self._human_observer_alive = {}
            self._human_last_alive_critical = {}
            self._human_direct_targets = {}
            self._human_vengeance_until = {}
            self._server_orders = {}
            self._server_order_tokens = {}
            self._order_revision = -1
            self._next_observation = 0.0
            self._next_shot_lane_refresh = 0.0
            self._next_cover_refresh = 0.0
            self._next_publication = 0.0
            self._pending_ram_reports = []
            self._pending_launches = []
            self._pending_launch_keys = {}
            self._pending_launch_by_bot = {}
            self._cover_cursor = 0
            self._cover_queue = []
            self._cover_results = []
            self._decision_cache = {}
            self._motion_probe_cache = {}
            self._traffic_stopping_cache = {}
            self._slope_pose_cursor = 0
            self._world_receipt_waiting = []
            self._world_receipt_frame = None
            self._prewarm_receipt_cursor = 0
            self._combat_sync = {}
            self._server_tick = -1
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
            self._clear_artillery_intents()
            self._ballistic_solution_cache = {}
            self._friendly_repositions = {}
        previous_authority = self.authority_id
        self.authority_id = message.get('bot_authority_id')
        authority_handoff = (
            previous_authority is not None and
            previous_authority != self.authority_id and
            self.is_authority() and
            isinstance(message.get('bot_manifest'), (list, tuple)))
        if previous_authority != self.authority_id:
            self._sample_time_us = 0
            self._manifest_sent = False
            self._pending_manifest = None
            self._pending_manifest_round_id = None
            self._pending_manifest_authority_id = None
            self._clear_artillery_intents()
            self._ballistic_solution_cache = {}
            self._friendly_repositions = {}
            self._visibility_cache = {}
            self._team_visibility_cache = {}
            self._visible_target_poses = {}
            self._spot_until = {}
            self._human_observer_alive = {}
            self._human_last_alive_critical = {}
            self._human_direct_targets = {}
            self._human_vengeance_until = {}
            self._visibility_fire = {}
            self._visibility_still = {}
            self._shot_los_cache = {}
            self._shot_los_deadlines = {}
            self._decision_cache = {}
            self._motion_probe_cache = {}
            self._traffic_stopping_cache = {}
            self._world_receipt_waiting = []
            self._world_receipt_frame = None
            self._prewarm_receipt_cursor = 0
            self._pending_ram_reports = []
            self._pending_launches = []
            self._pending_launch_keys = {}
            self._pending_launch_by_bot = {}
            self._ram_cooldowns = {}
            self._human_ram_cooldowns = {}
            self._ram_contacts = frozenset()
            self._cover_queue = []
            self._cover_results = []
            self._next_observation = 0.0
            self._next_shot_lane_refresh = 0.0
            self._next_cover_refresh = 0.0
            self._next_publication = 0.0
        if authority_handoff:
            # The takeover manifest is an explicit server-authority boundary.
            # Existing combat sync entries may still be based on an older
            # snapshot than publications already accepted from the previous
            # authority, so keep a per-bot handoff window until one canonical
            # acknowledgement resolves that overlap.
            for sync in self._combat_sync.values():
                sync['authority_handoff_pending'] = True
        if not self.is_authority():
            return []
        if self.finished:
            self._pending_manifest = None
            self._pending_manifest_round_id = None
            self._pending_manifest_authority_id = None
            return []
        pending_manifest = self.pending_manifest()
        if pending_manifest is not None:
            return [pending_manifest]
        if self._manifest_sent:
            return []
        server_manifest = message.get('bot_manifest')
        restoring_authority = bool(server_manifest)
        manifest = server_manifest or message.get('bots') or []
        resolved_manifest = []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            bot_id = int(raw['id'])
            vehicle_name = self.vehicle_selector(raw)
            try:
                resolved_descriptor = self.descriptor_resolver(vehicle_name)
                descriptor_pair = siege_mechanics.descriptor_pair(
                    resolved_descriptor)
            except Exception as error:
                raise RuntimeError(
                    'bot %d vehicle %s descriptor is unavailable: %s' % (
                        bot_id, vehicle_name, error))
            resolved_manifest.append((
                raw, bot_id, vehicle_name, descriptor_pair))
        if self.adapter is None:
            self._ensure_navigation_graph(message.get('map', ''))
            self.adapter = self._new_adapter(message.get('map', ''),
                                             round_id or 0)
            if self.navigator is not None:
                self.adapter.navigation_target = self._navigation_target
        for raw, bot_id, vehicle_name, descriptor_pair in resolved_manifest:
            raw_siege_state = raw.get(
                'siege_state', siege_mechanics.DISABLED)
            raw_wire_time = raw.get('siege_time_left_ms', 0)
            raw_wire_total = raw.get('siege_transition_total_ms', 0)
            if not siege_mechanics.valid_wire_state(
                    raw_siege_state, raw_wire_time, vehicle_name,
                    raw_wire_total):
                raise ValueError('authority manifest Bot Siege state is invalid')
            siege_state = int(raw_siege_state)
            wire_time = int(raw_wire_time)
            wire_total = int(raw_wire_total)
            siege_time_left = wire_time / 1000.0
            siege_transition_total = wire_total / 1000.0
            self._descriptor_pairs[bot_id] = descriptor_pair
            descriptor = siege_mechanics.active_descriptor(
                descriptor_pair, siege_state)
            half_length, half_width = _hull_dimensions(descriptor)
            self._descriptors[bot_id] = descriptor
            self._gun_yaw_limits[bot_id] = \
                ai_driver.gun_yaw_limits(descriptor)
            if bot_id not in self._gun_states:
                self._gun_states[bot_id] = _BotGunState(
                    descriptor, raw.get('fire_seq', 0),
                    _critical_factor(raw, descriptor, 'dispersion'))
                if restoring_authority:
                    if ('reload_time' not in raw or
                            'reload_duration' not in raw):
                        raise ValueError(
                            'authority manifest has no bot reload progress')
                    reload_factor = _critical_factor(
                        raw, descriptor, 'reload')
                    self._gun_states[bot_id].restore_fire_seq(
                        raw.get('fire_seq', 0),
                        _critical_factor(
                            raw, descriptor, 'dispersion'),
                        raw.get('reload_time'),
                        raw.get('reload_duration'), reload_factor,
                        raw.get('clip'), raw.get('clip_size'))
            else:
                self._gun_states[bot_id].adopt_descriptor(descriptor)
            self._physics_params[bot_id] = vehicle_physics.derive_params(
                descriptor)
            self._turn_speeds[bot_id] = 0.0
            if isinstance(raw.get('profile'), dict):
                self.adapter.director.register_profile(bot_id, raw.get('team', 1),
                                                       raw['profile'], raw.get('name', 'Bot'))
            else:
                self.adapter.register(bot_id, raw.get('team', 1), descriptor,
                                      raw.get('name', 'Bot'))
            spawn, spawn_yaw = self._spawn(
                int(raw.get('team', 1)), int(raw.get('slot', 0)))
            agents = getattr(self.adapter.director, 'agents', {})
            agent = agents.get(bot_id, {})
            profile = agent.get('profile', {})
            route = agent.get('route') or {}
            max_health = max(1, int(getattr(descriptor, 'maxHealth',
                                            raw.get('max_health', 1000))))
            health = max(0, min(
                int(_number(raw.get('health'), max_health)), max_health))
            self.states.setdefault(bot_id, {
                'id': bot_id, 'team': int(raw.get('team', 1)),
                'slot': int(raw.get('slot', 0)), 'name': raw.get('name', 'Bot'),
                'vehicle': vehicle_name,
                'x': _number(raw.get('x'), spawn[0]),
                'y': _number(raw.get('y'), spawn[1]),
                'z': _number(raw.get('z'), spawn[2]),
                'yaw': _number(raw.get('yaw'), spawn_yaw),
                'aim_yaw': _number(raw.get('yaw'), spawn_yaw),
                'turret_yaw': 0.0, 'gun_pitch': 0.0,
                'desired_gun_pitch': 0.0,
                'gun_aligned': False, 'hull_aiming': False,
                'health': health, 'max_health': max_health,
                'alive': bool(raw.get('alive', health > 0)) and health > 0,
                'fire_seq': max(0, int(_number(raw.get('fire_seq'), 0))),
                'shell_index': max(0, min(
                    int(_number(raw.get('shell_index'), 0)), 9)),
                'speed': _number(raw.get('speed')),
                'movement_dir': 0, 'rotation_dir': 0,
                'move_speed': _forward_speed(descriptor),
                'view_range': self._cache_vision_range(bot_id, descriptor),
                'half_length': half_length, 'half_width': half_width,
                'collision_shape': _collision_shape(descriptor),
                'mass': self._physics_params[bot_id]['mass'],
                'ram_profile': tank_collision.descriptor_ram_profile(
                    descriptor),
                'push_x': 0.0, 'push_z': 0.0,
                'air_lateral_x': 0.0, 'air_lateral_z': 0.0,
                'slide_speed': 0.0,
                'vertical_speed': 0.0, 'airborne': False,
                'grounded_once': False, 'last_drive_pitch': 0.0,
                'pitch': _number(raw.get('pitch')),
                'roll': _number(raw.get('roll')),
                'terrain_pitch': _number(raw.get('pitch')),
                'suspension_pitch': 0.0,
                'siege_state': siege_state,
                'siege_time_left_ms': wire_time,
                '_siege_time_left': siege_time_left,
                'siege_transition_total_ms': wire_total,
                '_siege_transition_total': siege_transition_total,
                '_siege_intent': (
                    siege_state == siege_mechanics.ENABLED),
                '_siege_intent_elapsed': 0.0,
                '_drown_check': 0.0,
                '_drown_time': 0.0,
                '_drowning': False,
                '_overturn_check': 0.0,
                '_overturn_time': 0.0,
                '_overturn_level': 0,
                '_overturned': False,
                'stun_end_server_time_ms': max(0, int(_number(
                    raw.get('stun_end_server_time_ms'), 0))),
                '_stun_until_equipment_time': self._equipment_now,
                'critical': (dict(raw.get('critical'))
                             if isinstance(raw.get('critical'), dict) else {}),
                'combat_revision': max(0, int(_number(
                    raw.get('combat_revision'), 0))),
                'combat_base_revision': max(0, int(_number(
                    raw.get('combat_base_revision'), 0))),
                'combat_ack_seq': max(0, int(_number(
                    raw.get('combat_ack_seq'), 0))),
                'combat_fire_elapsed': round(max(0.0, min(
                    FIRE_DURATION_SECONDS, _number(
                        raw.get('combat_fire_elapsed'), 0.0))), 6),
                'combat_fire_timer': round(max(0.0, min(
                    FIRE_TICK_SECONDS - 0.000001, _number(
                        raw.get('combat_fire_timer'), 0.0))), 6),
                'profile': profile, 'route': route,
            })
            gun_state = self._gun_states[bot_id]
            state = self.states[bot_id]
            state.setdefault('_overturn_check', 0.0)
            state.setdefault('_overturn_time', 0.0)
            state.setdefault('_overturn_level', 0)
            state.setdefault('_overturned', False)
            self._observe_bot_stun(
                state, raw, max(0, int(_number(
                    message.get('server_time_ms'), 0))))
            self._install_bot_equipments(
                bot_id, raw.get('equipment_states')
                if 'equipment_states' in raw else None,
                canonical_restore=authority_handoff)
            self._publish_equipment_state(state)
            state['siege_state'] = siege_state
            state['siege_time_left_ms'] = wire_time
            state['_siege_time_left'] = siege_time_left
            state['siege_transition_total_ms'] = wire_total
            state['_siege_transition_total'] = siege_transition_total
            state['_siege_intent'] = (
                siege_state == siege_mechanics.ENABLED)
            state['_siege_intent_elapsed'] = 0.0
            ammo_state = self._ammo_states.get(bot_id)
            if ammo_state is None:
                ammo_state = _BotAmmoState(descriptor, profile, raw)
                self._ammo_states[bot_id] = ammo_state
            elif authority_handoff:
                ammo_state.restore(raw)
                if ('reload_time' not in raw or
                        'reload_duration' not in raw):
                    raise ValueError(
                        'authority manifest has no bot reload progress')
                reload_factor = _critical_factor(
                    raw, descriptor, 'reload')
                gun_state.restore_fire_seq(
                    max(int(state.get('fire_seq', 0)),
                        int(_number(raw.get('fire_seq', 0)))),
                    _critical_factor(raw, descriptor, 'dispersion'),
                    raw.get('reload_time'), raw.get('reload_duration'),
                    reload_factor, raw.get('clip'), raw.get('clip_size'))
            ammo_state.publish(state)
            burst_state = self._burst_states.get(bot_id)
            if burst_state is None:
                burst_state = burst_mechanics.BurstClock()
                self._burst_states[bot_id] = burst_state
            if restoring_authority:
                restored = burst_state.restore(
                    raw, state.get('fire_seq', 0))
                if restored:
                    gun_state._burst_remaining = max(
                        0, burst_state.count - burst_state.next_index)
                else:
                    burst_state.cancel(0)
            burst_state.publish(state)
            if authority_handoff:
                self._apply_authority_takeover_motion(state, raw)
            sync = self._combat_sync_state(state)
            if authority_handoff:
                sync['authority_handoff_pending'] = True
            reload_factor = _critical_factor(state, descriptor, 'reload')
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = gun_state.duration(reload_factor)
        bots = [self._manifest_entry(state)
                for state in self._ordered_states()]
        player_collision_profiles = (
            self._player_collision_manifest(message.get('players'))
            if message.get('human_ram_timeline') else None)
        outgoing = {'type': 'bot_manifest', 'bots': bots}
        if player_collision_profiles is not None:
            outgoing['player_collision_profiles'] = (
                player_collision_profiles)
        self._pending_manifest = copy.deepcopy(outgoing)
        self._pending_manifest_round_id = self.round_id
        self._pending_manifest_authority_id = self.authority_id
        return [copy.deepcopy(self._pending_manifest)]

    def pending_manifest(self):
        """Return one isolated copy of the current unsent manifest."""
        if (self._manifest_sent or self._pending_manifest is None or
                not self.is_authority() or
                self._pending_manifest_round_id != self.round_id or
                self._pending_manifest_authority_id != self.authority_id):
            return None
        return copy.deepcopy(self._pending_manifest)

    def discard_pending_manifest(self):
        """Fence an unsent manifest at a round or authority boundary."""
        pending = self._pending_manifest is not None
        self._pending_manifest = None
        self._pending_manifest_round_id = None
        self._pending_manifest_authority_id = None
        return pending

    def mark_manifest_enqueued(self, message):
        """Commit manifest delivery only after the transport accepts it."""
        if (not isinstance(message, dict) or self._manifest_sent or
                self._pending_manifest is None or
                not self.is_authority() or
                self._pending_manifest_round_id != self.round_id or
                self._pending_manifest_authority_id != self.authority_id or
                message != self._pending_manifest):
            return False
        self._manifest_sent = True
        self._pending_manifest = None
        self._pending_manifest_round_id = None
        self._pending_manifest_authority_id = None
        return True

    def _combat_sync_state(self, state):
        bot_id = int(state['id'])
        sync = self._combat_sync.get(bot_id)
        if sync is None:
            signature = _combat_signature(state)
            revision = max(0, int(_number(
                state.get('combat_revision'), 0)))
            base_revision = max(0, int(_number(
                state.get('combat_base_revision'), 0)))
            acked_seq = max(0, int(_number(
                state.get('combat_ack_seq'), 0)))
            sync = {
                'server_signature': signature,
                'server_combat': _combat_record(state),
                'published_signature': signature,
                'pending': [],
                'next_seq': acked_seq,
                'acked_seq': acked_seq,
                'combat_revision': revision,
                'base_revision': base_revision,
                'server_tick': -1,
                'unpublished_steps': [],
                'authority_handoff_pending': False,
            }
            self._combat_sync[bot_id] = sync
        state['combat_revision'] = sync['combat_revision']
        state['combat_base_revision'] = sync['base_revision']
        state['combat_ack_seq'] = sync['acked_seq']
        state['combat_seq'] = sync['next_seq']
        return sync

    def _apply_authority_takeover_motion(self, state, raw):
        """Rebase one resumed authority on the server's canonical pose.

        ``apply_snapshot`` deliberately never rewinds an active authority's
        locally integrated pose.  The same rule cannot apply after authority
        was lost: on handback the merged manifest is the only canonical pose
        boundary, and retaining the old local state rewinds the battle to the
        point where this client stopped simulating.  Copy only pose, aim and
        motion here; combat continues through the existing revision/ack
        reconciliation below.
        """
        yaw = _number(raw.get('yaw'), state.get('yaw'))
        aim_yaw = _number(raw.get('aim_yaw'), yaw)
        gun_pitch = _number(raw.get('gun_pitch'), 0.0)
        state['x'] = _number(raw.get('x'), state.get('x'))
        state['y'] = _number(raw.get('y'), state.get('y'))
        state['z'] = _number(raw.get('z'), state.get('z'))
        state['yaw'] = yaw
        state['aim_yaw'] = aim_yaw
        state['turret_yaw'] = _angle_delta(aim_yaw, yaw)
        state['gun_pitch'] = gun_pitch
        state['desired_gun_pitch'] = gun_pitch
        state['pitch'] = _number(raw.get('pitch'), state.get('pitch'))
        state['roll'] = _number(raw.get('roll'), state.get('roll'))
        state['terrain_pitch'] = state['pitch']
        state['suspension_pitch'] = 0.0
        state['gun_aligned'] = False
        state['hull_aiming'] = False
        # Current LAN snapshots carry intent but not a velocity magnitude.
        # Resume from rest unless a later protocol explicitly supplies one;
        # stale pre-handoff momentum is not server-canonical state.
        state['speed'] = _number(raw.get('speed'), 0.0)
        movement = _number(raw.get('movement_dir'))
        rotation = _number(raw.get('rotation_dir'))
        state['movement_dir'] = (
            1 if movement > 0.01 else (-1 if movement < -0.01 else 0))
        state['rotation_dir'] = (
            1 if rotation > 0.01 else (-1 if rotation < -0.01 else 0))
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['vertical_speed'] = 0.0
        state['airborne'] = False
        state['grounded_once'] = False
        state['last_drive_pitch'] = 0.0
        self._turn_speeds[int(state['id'])] = 0.0
        return True

    def _mark_combat_publication(self, state):
        sync = self._combat_sync_state(state)
        signature = _combat_signature(state)
        if signature == sync['published_signature']:
            return False
        sync['next_seq'] += 1
        sync['pending'].append({
            'seq': sync['next_seq'],
            'signature': signature,
            'combat': _combat_record(state),
            'steps': list(sync['unpublished_steps']),
        })
        sync['unpublished_steps'] = []
        sync['published_signature'] = signature
        state['combat_seq'] = sync['next_seq']
        return True

    def _apply_server_combat_state(self, state, raw, server_tick):
        """Reconcile an explicit server base/revision/ack boundary.

        Signatures validate an acknowledged publication; they never decide
        whether the server consumed it.  ``combat_ack_seq`` is the sole answer
        to that question.  When an external hit opens a new base, only the
        unacknowledged repair/fire time slices are replayed on the new canonical
        state.
        """
        sync = self._combat_sync_state(state)
        if server_tick is not None and server_tick < sync['server_tick']:
            return False
        candidate = _copy_runtime_state(state)
        candidate['health'] = max(0, min(
            int(_number(raw.get('health'), state['health'])),
            int(state['max_health'])))
        candidate['alive'] = (
            bool(raw.get('alive', candidate['health'] > 0)) and
            candidate['health'] > 0)
        contract = ('critical', 'combat_revision', 'combat_base_revision',
                    'combat_ack_seq', 'combat_fire_elapsed',
                    'combat_fire_timer', 'stun_end_server_time_ms')
        if not all(name in raw for name in contract):
            raise ValueError('modern bot snapshot combat contract is missing')
        if not isinstance(raw['critical'], dict):
            raise ValueError('modern bot snapshot critical state is invalid')
        try:
            revision = int(raw['combat_revision'])
            base_revision = int(raw['combat_base_revision'])
            acked_seq = int(raw['combat_ack_seq'])
            fire_elapsed = float(raw['combat_fire_elapsed'])
            fire_timer = float(raw['combat_fire_timer'])
            stun_end = int(raw['stun_end_server_time_ms'])
            exact = (
                not isinstance(raw['combat_revision'], bool) and
                not isinstance(raw['combat_base_revision'], bool) and
                not isinstance(raw['combat_ack_seq'], bool) and
                not isinstance(raw['combat_fire_elapsed'], bool) and
                not isinstance(raw['combat_fire_timer'], bool) and
                not isinstance(raw['stun_end_server_time_ms'], bool) and
                float(raw['combat_revision']) == revision and
                float(raw['combat_base_revision']) == base_revision and
                float(raw['combat_ack_seq']) == acked_seq and
                float(raw['stun_end_server_time_ms']) == stun_end and
                not math.isnan(fire_elapsed) and
                not math.isinf(fire_elapsed) and
                not math.isnan(fire_timer) and
                not math.isinf(fire_timer))
        except (TypeError, ValueError, OverflowError):
            exact = False
        if (not exact or revision < 0 or base_revision < 0 or
                acked_seq < 0 or base_revision > revision or
                fire_elapsed < 0.0 or
                fire_elapsed > FIRE_DURATION_SECONDS or
                fire_timer < 0.0 or fire_timer >= FIRE_TICK_SECONDS):
            raise ValueError('modern bot snapshot combat contract is invalid')
        candidate['critical'] = _canonical_critical(raw['critical'])
        if (not candidate['critical'].get('fire', False) and
                (fire_elapsed != 0.0 or fire_timer != 0.0)):
            raise ValueError('inactive bot fire has a non-zero clock')
        candidate['combat_fire_elapsed'] = round(fire_elapsed, 6)
        candidate['combat_fire_timer'] = round(fire_timer, 6)
        if stun_end < 0:
            raise ValueError('modern bot snapshot combat contract is invalid')
        candidate['stun_end_server_time_ms'] = stun_end
        candidate_record = _combat_record(candidate)
        signature = _combat_signature(candidate)

        acknowledged = None
        if acked_seq > sync['acked_seq']:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    acknowledged = pending
                    break
        handoff_pending = sync.get('authority_handoff_pending', False)
        handoff_new_base = (
            handoff_pending and
            base_revision > sync['base_revision'])
        handoff_same_base_overlap = (
            handoff_pending and
            base_revision == sync['base_revision'] and
            acked_seq > sync['acked_seq'] and
            (acked_seq > sync['next_seq'] or
             (acknowledged is not None and
              acknowledged['signature'] != signature)))
        handoff_canonical_reset = (
            handoff_new_base or handoff_same_base_overlap)
        if (revision < sync['combat_revision'] or
                base_revision < sync['base_revision'] or
                acked_seq < sync['acked_seq'] or
                (acked_seq > sync['next_seq'] and
                 not handoff_canonical_reset)):
            raise ValueError('server bot combat revision moved backwards')

        if handoff_canonical_reset:
            if revision <= sync['combat_revision']:
                raise ValueError('server bot combat handoff ack is inconsistent')
            # A promoted authority may start from the last snapshot it consumed
            # while the server has already accepted later publications from the
            # old authority.  Those sequence numbers overlap any work started
            # locally during the promotion window.  A new base also makes every
            # local pending step a derivative of the superseded baseline, so no
            # step can be identified safely for replay.  Treat either case as
            # an explicit canonical handoff reset and resume after the server
            # ack.
            _apply_combat_record(state, candidate_record)
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['published_signature'] = signature
            sync['pending'] = []
            sync['unpublished_steps'] = []
            sync['next_seq'] = acked_seq
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            sync['base_revision'] = base_revision
            sync['authority_handoff_pending'] = False
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = acked_seq
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return True

        if base_revision == sync['base_revision']:
            if acked_seq == sync['acked_seq']:
                if (revision != sync['combat_revision'] or
                        signature != sync['server_signature']):
                    raise ValueError(
                        'server changed bot combat without a publication ack')
            else:
                if (acknowledged is None or
                        acknowledged['signature'] != signature or
                        revision <= sync['combat_revision']):
                    raise ValueError('server bot combat ack is inconsistent')
                sync['pending'] = [
                    pending for pending in sync['pending']
                    if pending['seq'] > acked_seq]
                # A matching publication from this authority is an ordered
                # barrier: no accepted state from the previous authority can
                # remain unresolved after it.
                sync['authority_handoff_pending'] = False
            sync['server_signature'] = signature
            sync['server_combat'] = candidate_record
            sync['acked_seq'] = acked_seq
            sync['combat_revision'] = revision
            state['combat_revision'] = revision
            state['combat_base_revision'] = base_revision
            state['combat_ack_seq'] = acked_seq
            state['combat_seq'] = sync['next_seq']
            if server_tick is not None:
                sync['server_tick'] = server_tick
            return False

        if revision < base_revision or revision <= sync['combat_revision']:
            raise ValueError('new bot combat base has no canonical revision')
        boundary = None
        if acked_seq == sync['acked_seq']:
            boundary_record = sync['server_combat']
        else:
            for pending in sync['pending']:
                if pending['seq'] == acked_seq:
                    boundary = pending
                    break
            if boundary is None:
                raise ValueError('new bot combat base has an unknown ack')
            boundary_record = boundary['combat']

        replay_steps = []
        for pending in sync['pending']:
            if pending['seq'] > acked_seq:
                replay_steps.extend(pending['steps'])
        replay_steps.extend(sync['unpublished_steps'])

        _apply_combat_record(state, candidate_record)
        sync['server_signature'] = signature
        sync['server_combat'] = candidate_record
        sync['published_signature'] = signature
        sync['pending'] = []
        sync['unpublished_steps'] = []
        sync['next_seq'] = acked_seq
        sync['acked_seq'] = acked_seq
        sync['combat_revision'] = revision
        sync['base_revision'] = base_revision
        sync['authority_handoff_pending'] = False

        for replay in replay_steps:
            replay_step, replay_now, replay_fire = replay[:3]
            replay_equipment = replay[3] if len(replay) > 3 else ()
            self._advance_bot_critical(
                state, replay_step, replay_now, record_step=False,
                advance_fire=replay_fire,
                equipment_effects=replay_equipment)
        replayed_signature = _combat_signature(state)
        if replayed_signature != signature:
            # A replay is local work, not a wire publication. Reserving its
            # sequence here makes the next real publication skip that unseen
            # proposal, after which the server rejects every later full-state
            # update as out of order. Retain the replay slices as unpublished
            # lineage; the next bot_state coalesces them with that frame's
            # repair/fire advancement into exactly one ack+1 proposal. If a
            # newer base arrives first, these same slices remain available for
            # another canonical rebase.
            sync['unpublished_steps'] = list(replay_steps)
        else:
            # The explicit fire clock is part of the durable signature.  A
            # replay slice that changed neither combat state nor clock is a
            # completed no-op and must not leak into a later lineage.
            sync['unpublished_steps'] = []
        state['combat_revision'] = revision
        state['combat_base_revision'] = base_revision
        state['combat_ack_seq'] = acked_seq
        state['combat_seq'] = sync['next_seq']
        if server_tick is not None:
            sync['server_tick'] = server_tick
        return True

    def _apply_bot_equipment_effect(self, state, descriptor, effect,
                                    strict=False):
        """Apply one already-authorized bot consumable through shared law."""
        action = str((effect or {}).get('action') or '')
        shadow = _BotCriticalVehicle(
            state, descriptor, None,
            _number(state.get('combat_fire_timer')),
            self._equipment_passives.get(int(state['id'])))
        clear_stun = bool((effect or {}).get('clearStun', False))
        stun_cleared = False
        if clear_stun:
            try:
                stun_base = int(effect.get('stunBaseEndServerTimeMs'))
            except (TypeError, ValueError, OverflowError):
                if strict:
                    raise RuntimeError('bot medkit stun base is invalid')
                return False
            if (isinstance(effect.get('stunBaseEndServerTimeMs'), bool) or
                    stun_base <= 0):
                if strict:
                    raise RuntimeError('bot medkit stun base is invalid')
                return False
        if action == 'extinguish_fire':
            payload = critical_damage.use_extinguisher(shadow)
        elif action == 'repair_devices':
            payload = critical_damage.repair_device(
                shadow, effect.get('selected'),
                bool(effect.get('repairAll', False)))
        elif action == 'restore_crew':
            payload = critical_damage.restore_crew(
                shadow, effect.get('selected'),
                bool(effect.get('repairAll', False)))
        else:
            if strict:
                raise RuntimeError('bot equipment action is unsupported')
            return False
        if payload is None and not clear_stun:
            if strict:
                raise RuntimeError('bot equipment effect could not be applied')
            return False
        if payload is not None:
            state['critical'] = _canonical_critical(payload)
        if action == 'extinguish_fire':
            state['combat_fire_elapsed'] = 0.0
            state['combat_fire_timer'] = 0.0
        if clear_stun:
            if int(state.get('stun_end_server_time_ms', 0)) == stun_base:
                state['stun_end_server_time_ms'] = 0
                state['_stun_until_equipment_time'] = self._equipment_now
                stun_cleared = True
        return payload is not None or stun_cleared

    def _poll_bot_equipments(self, state, descriptor):
        """Run fixed bot kit policy and return replayable effect records."""
        effects = []
        for equipment in self._equipment_states.get(int(state['id']), ()):
            effect = equipment.poll_bot(
                self._equipment_now, state.get('critical'),
                stunned=self._bot_stunned(state))
            if effect is None:
                continue
            effect = dict(effect)
            if effect.get('clearStun', False):
                effect['stunBaseEndServerTimeMs'] = int(
                    state.get('stun_end_server_time_ms', 0))
            self._apply_bot_equipment_effect(
                state, descriptor, effect, strict=True)
            effects.append(dict(effect))
        self._publish_equipment_state(state)
        return effects

    def _advance_bot_critical(self, state, step, now, record_step=True,
                              advance_fire=True, equipment_effects=None):
        # Repair, consumables and fire may replace the canonical payload. Never
        # carry a parsed view across that mutation boundary.
        _clear_critical_parts_tick_cache(state)
        payload = state.get('critical')
        if ((not isinstance(payload, dict) or not payload) and
                not self._bot_stunned(state)):
            return False
        before_signature = _combat_signature(state)
        was_on_fire = bool(payload.get('fire', False))
        sync = self._combat_sync_state(state)
        descriptor = self._descriptors.get(state['id'], {})
        if equipment_effects is None:
            equipment_effects = (
                self._poll_bot_equipments(state, descriptor)
                if record_step else ())
        else:
            for effect in equipment_effects:
                self._apply_bot_equipment_effect(
                    state, descriptor, effect, strict=False)
        payload = state.get('critical') or {}
        fire_elapsed = round(max(0.0, min(
            FIRE_DURATION_SECONDS,
            _number(state.get('combat_fire_elapsed')))), 6)
        fire_timer = round(max(0.0, min(
            FIRE_TICK_SECONDS - 0.000001,
            _number(state.get('combat_fire_timer')))), 6)
        fire_started = (
            float(now) - min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step))
            if was_on_fire and advance_fire else None)
        shadow = _BotCriticalVehicle(
            state, descriptor, fire_started, fire_timer,
            self._equipment_passives.get(int(state['id'])))
        repair_payload = critical_damage.tick_repair(
            shadow, step,
            repair_factor=self._bot_repair_factor(state['id'], descriptor))
        fire_damage = 0
        fire_payload = None
        if advance_fire:
            fire_damage, fire_payload = critical_damage.tick_fire(
                shadow, step, now=now)
        if was_on_fire and advance_fire and shadow.is_on_fire:
            state['combat_fire_elapsed'] = round(min(
                FIRE_DURATION_SECONDS, fire_elapsed + float(step)), 6)
            state['combat_fire_timer'] = round(
                max(0.0, min(FIRE_TICK_SECONDS - 0.000001,
                             shadow._fire_timer)), 6)
        elif not shadow.is_on_fire:
            state['combat_fire_elapsed'] = 0.0
            state['combat_fire_timer'] = 0.0
        durable = fire_payload or repair_payload
        if durable is not None:
            state['critical'] = _canonical_critical(durable)
        if fire_damage > 0:
            state['health'] = max(
                0, int(state['health']) - int(fire_damage))
            state['alive'] = state['health'] > 0
            state['display_health'] = state['health']
            if not state['alive']:
                terminal = _terminal_critical(
                    state, descriptor, 'fire')
                if terminal is not None:
                    state['critical'] = terminal
                state['combat_fire_elapsed'] = 0.0
                state['combat_fire_timer'] = 0.0
                self._friendly_repositions.pop(state['id'], None)
                state['death_reason'] = 1
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['target_kind'] = None
                state['target_id'] = None
        changed = _combat_signature(state) != before_signature
        if (record_step and
                (changed or was_on_fire or
                 bool((state.get('critical') or {}).get('fire', False)) or
                 equipment_effects)):
            sync['unpublished_steps'].append(
                (float(step), float(now), bool(was_on_fire),
                 tuple(dict(effect) for effect in equipment_effects)))
        return changed

    def _advance_bot_drowning(self, state, step):
        """Apply #1513 WaterSensor danger and its ten-second death clock."""
        if (not state.get('alive', False) or
                _number(state.get('health')) <= 0.0 or step <= 0.0):
            return False
        check = (_number(state.get('_drown_check')) + float(step))
        state['_drown_check'] = check
        if check < BOT_DROWNING_PROBE_SECONDS:
            return False
        # The current depth sample is the only native fact available for this
        # authority interval. Account for the whole elapsed interval instead
        # of silently discarding time after a slow render callback.
        elapsed = check
        state['_drown_check'] = 0.0
        depth = _number(self._water_depth_probe(_position(state)), -1.0)
        state['_water_depth'] = depth
        descriptor = self._descriptors.get(state['id'], {})
        level = _water_sensor_level(state, descriptor, depth)
        if level != 2:
            state['_drown_time'] = 0.0
            state['_drowning'] = False
            return False
        state['_drowning'] = True
        state['_drown_time'] = (
            _number(state.get('_drown_time')) + elapsed)
        if state['_drown_time'] <= BOT_DROWNING_SECONDS:
            return False

        display_health = max(0, int(_number(state.get('health'))))
        critical = _terminal_critical(state, descriptor, 'drowning')
        if critical is not None:
            state['critical'] = critical
        state['health'] = 0
        state['alive'] = False
        state['display_health'] = display_health
        state['death_reason'] = BOT_DROWNING_DEATH_REASON
        state['_drowned'] = True
        state['_drown_time'] = BOT_DROWNING_SECONDS
        state['_drowning'] = False
        self._friendly_repositions.pop(state['id'], None)
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['target_kind'] = None
        state['target_id'] = None
        return True

    def _advance_bot_overturn(self, state, step):
        """Apply #1513 overturn warning, input lock and terminal countdown."""
        if (not state.get('alive', False) or
                _number(state.get('health')) <= 0.0 or step <= 0.0):
            return False
        level = vehicle_physics.overturn_level(
            _number(state.get('pitch')), _number(state.get('roll')),
            BOT_OVERTURN_WARNING_COSINE, BOT_OVERTURN_DANGER_COSINE)
        if level == 0:
            state['_overturn_check'] = 0.0
            state['_overturn_time'] = 0.0
            state['_overturn_level'] = 0
            state['_overturned'] = False
            return False
        state['_overturn_check'] = (
            _number(state.get('_overturn_check')) + float(step))
        if (state['_overturn_check'] + 0.000001 <
                BOT_OVERTURN_IGNORE_SECONDS):
            return False
        if level != int(_number(state.get('_overturn_level'))):
            state['_overturn_level'] = level
            state['_overturn_time'] = 0.0
        state['_overturned'] = level == 2
        if level != 2:
            state['_overturn_time'] = 0.0
            return False
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        self._turn_speeds[int(state['id'])] = 0.0
        state['_overturn_time'] = (
            _number(state.get('_overturn_time')) + float(step))
        if (state['_overturn_time'] + 0.000001 <
                BOT_OVERTURN_DEATH_SECONDS):
            return False
        descriptor = self._descriptors.get(state['id'], {})
        terminal = _terminal_critical(state, descriptor, 'overturn')
        if terminal is not None:
            state['critical'] = terminal
        state['health'] = 0
        state['alive'] = False
        state['display_health'] = 0
        state['death_reason'] = BOT_OVERTURN_DEATH_REASON
        state['_overturn_time'] = BOT_OVERTURN_DEATH_SECONDS
        self._friendly_repositions.pop(state['id'], None)
        state['target_kind'] = None
        state['target_id'] = None
        return True

    def apply_snapshot(self, message):
        """Apply only server-owned combat state to the authority simulation.

        Bot poses remain locally simulated to avoid feeding a delayed echo back
        into steering.  Health and alive state are server authoritative because
        hits may be reported by other clients or by the authority's collision
        resolver after the local AI tick that fired the shot.
        """
        message = message if isinstance(message, dict) else {}
        self._apply_orders(message)
        if message.get('battle_result') is not None:
            self.finished = True
            self._clear_artillery_intents()
            self._friendly_repositions = {}
        server_tick = message.get('server_tick')
        if server_tick is not None:
            try:
                server_tick = int(server_tick)
            except (TypeError, ValueError):
                raise ValueError('bot snapshot server_tick is invalid')
            if server_tick < self._server_tick:
                return
            self._server_tick = server_tick
        for raw in message.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                state = self.states.get(int(raw['id']))
            except (TypeError, ValueError):
                continue
            if state is None:
                continue
            self._apply_server_combat_state(state, raw, server_tick)
            self._observe_bot_stun(
                state, state, max(0, int(_number(
                    message.get('server_time_ms'), 0))))
            previous_fire_seq = int(state.get('fire_seq', 0))
            previous_shell_index = int(state.get('shell_index', 0))
            incoming_fire_seq = max(
                0, int(_number(raw.get('fire_seq'), 0)))
            state['fire_seq'] = max(previous_fire_seq, incoming_fire_seq)
            if incoming_fire_seq > previous_fire_seq:
                gun_state = self._gun_states.get(state['id'])
                if gun_state is not None:
                    if ('reload_time' not in raw or
                            'reload_duration' not in raw):
                        raise ValueError(
                            'bot snapshot has no reload progress')
                    descriptor = self._descriptors.get(state['id'], {})
                    gun_state.restore_fire_seq(
                        incoming_fire_seq,
                        _critical_factor(
                            state, descriptor, 'dispersion'),
                        raw.get('reload_time'), raw.get('reload_duration'),
                        _critical_factor(state, descriptor, 'reload'),
                        raw.get('clip'), raw.get('clip_size'))
                    reload_factor = _critical_factor(
                        state, descriptor, 'reload')
                    state['clip'] = gun_state.clip
                    state['reload_time'] = gun_state.remaining(reload_factor)
                    state['reload_duration'] = gun_state.duration(
                        reload_factor)
            ammo_contract = all(name in raw for name in (
                'ammo_remaining', 'shell_index', 'next_shell_index',
                'ammo_reload_pending'))
            ammo_state = self._ammo_states.get(state['id'])
            if (incoming_fire_seq > previous_fire_seq and ammo_contract and
                    ammo_state is not None):
                ammo_state.restore(raw)
                ammo_state.publish(state)
            elif not ammo_contract:
                # Compatibility for pre-ammunition snapshots. Current clients
                # always carry the finite inventory contract.
                state['shell_index'] = max(0, min(
                    int(_number(raw.get('shell_index'),
                                state.get('shell_index', 0))), 9))
            if (incoming_fire_seq > previous_fire_seq or
                    state['shell_index'] != previous_shell_index or
                    not state['alive']):
                self._cancel_artillery_intent(state['id'])
            if not state['alive']:
                self._friendly_repositions.pop(state['id'], None)
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['target_kind'] = None
                state['target_id'] = None

    def _apply_orders(self, message):
        if not isinstance(message, dict) or 'bot_orders' not in message:
            return False
        orders = message.get('bot_orders')
        if not isinstance(orders, (list, tuple)):
            return False
        try:
            revision = int(message.get('bot_order_revision'))
        except (TypeError, ValueError):
            return False
        if revision < self._order_revision:
            return False
        accepted = {}
        for raw in orders[:30]:
            if not isinstance(raw, dict) or raw.get('id') is None:
                return False
            try:
                bot_id = int(raw.get('id'))
            except (TypeError, ValueError):
                return False
            if bot_id in accepted:
                return False
            order = dict(raw)
            # Match the mature 0.8.2 network boundary: JSON point records are
            # converted before any planner, navigator, or driver sees them.
            # Leaving route_anchor as a dict makes TerrainNavigator execute
            # tuple(dict), then float('x'/'y'/'z') on the first live tick.
            for name in ('aim_position', 'face_position', 'move_position',
                         'route_anchor'):
                point = order.get(name)
                if isinstance(point, dict):
                    order[name] = _position(point)
            accepted[bot_id] = order
        previous = self._server_orders
        changed_ids = set(previous).union(accepted)
        changed_ids = set(
            bot_id for bot_id in changed_ids
            if _server_order_signature(previous.get(bot_id)) !=
            _server_order_signature(accepted.get(bot_id)))
        for bot_id in changed_ids:
            self._server_order_tokens[bot_id] = (
                int(self._server_order_tokens.get(bot_id, 0)) + 1)
            self._decision_cache.pop(bot_id, None)
            self._motion_probe_cache.pop(bot_id, None)
        self._server_orders = accepted
        self._order_revision = revision
        return True

    def _manifest_entry(self, state):
        self._publish_equipment_state(state)
        keys = ('id', 'team', 'slot', 'name', 'vehicle', 'health',
                'max_health', 'x', 'y', 'z', 'yaw', 'profile', 'fire_seq',
                'shell_index', 'next_shell_index', 'ammo_remaining',
                'ammo_reload_pending', 'reload_time', 'reload_duration',
                'clip', 'clip_size', 'siege_state',
                'siege_time_left_ms', 'siege_transition_total_ms',
                'equipment_states', 'stun_end_server_time_ms')
        result = dict((key, state[key]) for key in keys)
        descriptor = self._descriptors.get(state['id'], {})
        terminal = _terminal_critical(state, descriptor, 'shot')
        # Retail descriptors always expose the physical crew. Keep synthetic
        # descriptor seams usable in tests, but never advertise a partial
        # terminal projection as canonical.
        if (terminal is not None and terminal.get('crew_roster')):
            result['terminal_critical'] = terminal
        # These coordinates were resolved against the loaded retail map by
        # the authority.  Consumers must not run the formation resolver a
        # second time and nudge the same slot away from its canonical pose.
        result['world_pose'] = True
        route = state.get('route') or {}
        waypoints = route.get('waypoints', ()) or ()
        if len(waypoints) > 16:
            raise ValueError(
                'bot route exceeds the 16-waypoint LAN protocol limit')
        result['route'] = {
            'id': route.get('id', 'map_route'),
            'waypoints': [
                {'x': point[0], 'y': 0.0, 'z': point[1],
                 'hold': bool(point[2]) if len(point) > 2 else False}
                for point in waypoints],
        }
        for key in ('capacity', 'risk'):
            if key in route:
                result['route'][key] = route[key]
        for key in ('role_weights', 'class_weights'):
            values = route.get(key)
            if isinstance(values, dict):
                result['route'][key] = dict(values)
        return result

    def _player_collision_manifest(self, players):
        """Donate client-effective human collision data to the LAN server."""
        result = []
        seen = set()
        for raw in players or ():
            if not isinstance(raw, dict):
                raise ValueError('player collision manifest row is invalid')
            try:
                player_id = int(raw['id'])
                vehicle_name = str(raw['vehicle'])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise ValueError('player collision manifest identity is invalid')
            # The hidden simulation worker projects one synthetic vehicle into
            # its local battle roster so the native client can enter the
            # arena.  It is not a human participant and must never be donated
            # as a server-side human collision body.
            if player_id == lan_client.WORKER_AUTHORITY_ID:
                continue
            if player_id <= 0 or player_id in seen or not vehicle_name:
                raise ValueError('player collision manifest identity is invalid')
            seen.add(player_id)
            snapshot = _player_effective_params(raw)
            descriptor = (self.player_descriptor_resolver(raw)
                          if callable(self.player_descriptor_resolver)
                          else self.descriptor_resolver(vehicle_name))
            if descriptor is None or descriptor == {}:
                raise ValueError(
                    'player collision manifest descriptor is unavailable')
            mass = float(snapshot['physics']['mass'])
            shape = tuple(float(value) for value in
                          tank_collision.chassis_shape(descriptor))
            ram_profile = snapshot['ramming']
            if (math.isnan(mass) or math.isinf(mass) or mass <= 0.0 or
                    len(shape) != 4 or
                    any(math.isnan(value) or math.isinf(value)
                        for value in shape)):
                raise ValueError('player collision manifest body is invalid')
            result.append({
                'id': player_id,
                'vehicle': vehicle_name,
                'mass': mass,
                'shape': list(shape),
                'ram_profile': {
                    'spall_coefficient': ram_profile[
                        'spall_coefficient'],
                    'ramming_bonus': ram_profile['ramming_bonus'],
                },
            })
        result.sort(key=lambda value: value['id'])
        return result

    def _ordered_states(self):
        return sorted(self.states.values(), key=lambda state: (
            int(state.get('slot', 0)), int(state.get('team', 1))))

    def _spawn(self, team, slot):
        if callable(self.spawn_resolver):
            return self.spawn_resolver(team, slot)
        raise ValueError(
            'validated spawn resolver is missing for team %s slot %s' %
            (team, slot))

    def _spotting_profile(self, target):
        kind = target.get('kind')
        target_id = target.get('network_id', target.get('id', 0))
        dynamic_key = None
        if kind != 'bot':
            snapshot = _player_effective_params(target)
            dynamic_key, unused_row = _player_dynamic_spotting(
                snapshot, target)
        key = ((kind, int(target_id)) if kind == 'bot' else
               (kind, int(target_id), dynamic_key))
        cached = self._spotting_profiles.get(key)
        if cached is not None:
            return cached
        if kind == 'bot':
            descriptor = self._descriptors.get(int(target_id), {})
            profile = _bot_profile(descriptor)
            cached = (_base_invisibility(descriptor, profile),
                      _shot_invisibility_factor(descriptor), profile)
        else:
            vehicle_profile = self._player_vehicle_profile(target)
            snapshot = _player_effective_params(target)
            unused_key, dynamic = _player_dynamic_spotting(
                snapshot, target)
            profile = dict(snapshot['spotting'])
            profile['vision_factor'] *= _number(dynamic.get('vision'), 1.0)
            profile['camouflage_factor'] *= _number(
                dynamic.get('camouflage'), 1.0)
            profile['invisibility_moving'] = tuple(
                dynamic['invisibility_moving'])
            profile['invisibility_still'] = tuple(
                dynamic['invisibility_still'])
            cached = ((dynamic['base_moving'], dynamic['base_still']),
                      snapshot['camouflage']['shot_factor'], profile)
        self._spotting_profiles[key] = cached
        return cached

    def _bot_repair_factor(self, bot_id, descriptor):
        """A bot repairs at #1513's default-crew repair speed, like the player."""
        cached = self._repair_factors.get(bot_id)
        if cached is None:
            cached = loadout.modifiers(
                descriptor,
                factors=loadout.attribute_factors(descriptor))[
                    'repair_factor']
            passive = self._equipment_passives.get(int(bot_id), {})
            cached *= 1.0 + max(0.0, _number(
                passive.get('repairkitBonusValue'), 0.0))
            self._repair_factors[bot_id] = cached
        return cached

    def _cache_vision_range(self, bot_id, descriptor):
        """Record this bot's moving and armed view ranges, return the moving one."""
        moving, still, delay = _vision_range_pair(descriptor)
        self._vision_ranges[int(bot_id)] = (moving, still, delay)
        return moving

    def _source_view_range(self, source, now):
        """Return one trusted observer's live #1513 view range."""
        if source.get('kind') == 'human':
            player_id = int(source.get('network_id', source.get('id', 0)))
            snapshot = _player_effective_params(source)
            profile = snapshot['spotting']
            descriptor = self._player_vehicle_profile(source)['descriptor']
            turret = _value(descriptor, 'turret', {}) or {}
            misc = _value(descriptor, 'miscAttrs', {}) or {}
            unused_key, dynamic = _player_dynamic_spotting(snapshot, source)
            devices, destroyed, unused_crew, yellow = _critical_parts(source)
            module_factor = device_damage.module_stat_factor(
                devices, destroyed, descriptor, 'vision', yellow)
            damage_factor = device_damage.clamp_vision_factor(
                _number(dynamic.get('vision'), 1.0) * module_factor)
            since = self._source_still.get(('human', player_id))
            binocular_active = bool(
                profile['has_binoculars'] and since is not None and
                loadout.still_device_active(
                    _number(now) - since, profile['binocular_delay']))
            return spotting.effective_view_range(
                _value(turret, 'circularVisionRadius', 330.0),
                misc_factor=(
                    _value(misc, 'circularVisionRadiusFactor', 1.0) *
                    damage_factor),
                crew_factor=profile['vision_factor'],
                binocular_factor=profile['binocular_factor'],
                binocular_active=binocular_active)

        # A bot earns its own stereoscope after it stands still, like the
        # player.
        bot_id = int(source.get('id', 0))
        cached = self._vision_ranges.get(bot_id)
        if cached is None:
            value = _number(source.get('view_range'), 330.0)
        else:
            moving, still, delay = cached
            since = self._source_still.get(bot_id)
            if (delay is not None and since is not None and
                    loadout.still_device_active(
                        _number(now) - since, delay)):
                value = still
            else:
                value = moving
        descriptor = getattr(self, '_descriptors', {}).get(bot_id)
        if descriptor is None:
            return value
        return value * device_damage.clamp_vision_factor(
            _critical_factor(source, descriptor, 'vision'))

    def _note_source_stillness(self, state, now):
        """Stamp when this bot stopped, so its own stereoscope can arm.

        Every alive bot is sampled once per tick here rather than per observed
        pair, so the stamp cannot go stale between two cache misses.
        """
        bot_id = int(state.get('id', 0))
        if abs(_number(state.get('speed'))) > spotting.MOVING_SPEED_EPSILON:
            self._source_still.pop(bot_id, None)
        elif bot_id not in self._source_still:
            self._source_still[bot_id] = _number(now)
        return True

    def _note_human_source_stillness(self, source, now):
        """Track a human observer's stationary-device clock by owner id."""
        player_id = int(source.get('network_id', source.get('id', 0)))
        key = ('human', player_id)
        if abs(_number(source.get('speed'))) > spotting.MOVING_SPEED_EPSILON:
            self._source_still.pop(key, None)
        elif key not in self._source_still:
            self._source_still[key] = _number(now)
        return True

    def _target_still_seconds(self, key, moving, now):
        """Seconds this target has stood still, for its stationary devices."""
        if moving:
            self._visibility_still.pop(key, None)
            return 0.0
        since = self._visibility_still.get(key)
        if since is None:
            self._visibility_still[key] = _number(now)
            return 0.0
        return max(0.0, _number(now) - since)

    def _target_fired_recently(self, target, now):
        if target.get('fire_seq') is None:
            return False, False, None
        kind = target.get('kind')
        target_id = target.get('network_id', target.get('id', 0))
        key = (kind, int(target_id))
        try:
            fire_seq = max(0, int(target.get('fire_seq')))
        except (TypeError, ValueError):
            return False, False, None
        previous = self._visibility_fire.get(key)
        if previous is None or fire_seq < previous[0]:
            self._visibility_fire[key] = (fire_seq, 0.0)
            return False, False, fire_seq
        if fire_seq > previous[0]:
            deadline = _number(now) + spotting.SHOT_CAMOUFLAGE_SECONDS
            self._visibility_fire[key] = (fire_seq, deadline)
            return True, True, fire_seq
        return _number(now) < previous[1], False, fire_seq

    def _target_detection_projection(
            self, target, target_id, now, tick_cache=None):
        """Project target-only camouflage inputs once per simulation slice."""
        moving = (abs(_number(target.get('speed'))) >
                  spotting.MOVING_SPEED_EPSILON)
        identity = (target.get('kind'), int(target_id))
        profile_cache = (tick_cache.setdefault('profile', {})
                         if isinstance(tick_cache, dict) else None)
        profile_bundle = (profile_cache.get(identity)
                          if profile_cache is not None else None)
        if profile_bundle is None:
            profile_bundle = self._spotting_profile(target)
            if profile_cache is not None:
                profile_cache[identity] = profile_bundle
        token = (_number(now), moving, id(profile_bundle))
        cache = (tick_cache.setdefault('target', {})
                 if isinstance(tick_cache, dict) else None)
        cached = cache.get(identity) if cache is not None else None
        if cached is not None and cached[0] == token:
            return cached[1]
        base_pair, shot_factor, profile = profile_bundle
        still_seconds = self._target_still_seconds(
            identity, moving, now)
        additive, multiplier = _invisibility_aspect(
            profile, moving, loadout.still_device_active(
                still_seconds, profile['camouflage_net_delay']))
        result = (
            base_pair, shot_factor, profile, moving,
            additive, multiplier)
        if cache is not None:
            # Keep only the latest exact target state. If a target changes
            # motion class twice in one slice, the second transition must run
            # the stillness state machine again rather than reuse old data.
            cache[identity] = (token, result)
        return result

    def _visible(self, source, target, now, tick_cache=None,
                 source_position=None, view_range_resolver=None):
        target_id = target.get('network_id', target.get('id', 0))
        source_kind = source.get('kind', 'bot')
        key = (source_kind, int(source.get('id', 0)),
               target.get('kind'), int(target_id))
        fired_recently, fire_changed, fire_seq = \
            self._target_fired_recently(target, now)
        cached = self._visibility_cache.get(key)
        ttl = VISIBILITY_SAMPLE_SECONDS
        if (not fire_changed and cached is not None and
                cached[2] == fire_seq and
                _number(now) - cached[0] < ttl):
            return cached[1]
        source_position = (source_position if source_position is not None else
                           _position(source))
        distance = _distance(source_position, target.get('position') or
                             _position(target))
        view_range = (view_range_resolver()
                      if callable(view_range_resolver) else
                      self._source_view_range(source, now))
        if distance <= spotting.PROXIMITY_SPOT_DISTANCE:
            value = True
        elif distance > spotting.MAX_SPOT_DISTANCE:
            value = False
        else:
            (base_pair, shot_factor, profile, moving,
             additive, multiplier) = self._target_detection_projection(
                 target, target_id, now, tick_cache)
            if not _detection_upper_bound(
                    distance, view_range, base_pair, moving, shot_factor,
                    fired_recently):
                value = False
            else:
                try:
                    self._probe_totals[0] += 1
                    probe_started = self._probe_started()
                    try:
                        try:
                            visibility = self.visibility_probe(
                                source, target, fired_recently)
                        except TypeError:
                            # Preserve the engine-free two-argument probe contract.
                            visibility = self.visibility_probe(source, target)
                    finally:
                        self._probe_finished(0, probe_started)
                except Exception:
                    visibility = False
                if isinstance(visibility, dict):
                    has_line_of_sight = bool(
                        visibility.get('line_of_sight', False))
                    foliage_bonus = _number(
                        visibility.get('foliage_bonus'), 0.0)
                else:
                    has_line_of_sight = bool(visibility)
                    foliage_bonus = 0.0
                camouflage = spotting.effective_camouflage(
                    base_pair, moving=moving, additive=additive,
                    multiplier=multiplier, shot_factor=shot_factor,
                    fired_recently=fired_recently,
                    foliage_bonus=foliage_bonus)
                value = spotting.is_detected(
                    distance, view_range, camouflage, has_line_of_sight)
        self._visibility_cache[key] = (_number(now), value, fire_seq)
        if len(self._visibility_cache) > 1024:
            oldest = sorted(self._visibility_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._visibility_cache.pop(old_key, None)
        return value

    @staticmethod
    def _human_planner_id(player_id):
        return HUMAN_TARGET_ID_BASE + int(player_id)

    @staticmethod
    def _observer_target_key(target):
        return (target.get('kind'),
                int(target.get('network_id', target.get('id', 0))))

    def _renew_team_spot(self, key, now, duration=None):
        """Renew one worker-owned team visibility lease monotonically."""
        if duration is None:
            duration = spotting.SPOT_MEMORY_SECONDS
        duration = max(
            0.0, min(spotting.DESIGNATED_SPOT_MEMORY_SECONDS,
                     _number(duration)))
        deadline = _number(now) + duration
        self._spot_until[key] = max(
            float(self._spot_until.get(key, 0.0)), deadline)
        return True

    def _team_spot_time_left(self, key, now):
        remaining = float(self._spot_until.get(key, 0.0)) - _number(now)
        if remaining <= 0.0:
            self._spot_until.pop(key, None)
            return 0.0
        return min(spotting.DESIGNATED_SPOT_MEMORY_SECONDS, remaining)

    def _track_human_observer_lifecycle(self, players, now):
        """Open Last Effort only on one observed alive-to-dead edge."""
        present = set()
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            player_id = int(raw.get('id'))
            present.add(player_id)
            alive = bool(raw.get('alive', True))
            previous = self._human_observer_alive.get(player_id)
            if previous is True and not alive:
                snapshot = _player_effective_params(raw)
                prior = {
                    'critical': self._human_last_alive_critical.get(
                        player_id, {})}
                if _player_spotting_perk(
                        snapshot, prior, 'radioman_lasteffort'):
                    self._human_vengeance_until[player_id] = (
                        _number(now) + spotting.LAST_EFFORT_SECONDS)
            elif alive:
                critical = raw.get('critical')
                self._human_last_alive_critical[player_id] = dict(
                    critical if isinstance(critical, dict) else {})
                self._human_vengeance_until.pop(player_id, None)
            self._human_observer_alive[player_id] = alive
        for player_id in tuple(self._human_observer_alive):
            if player_id in present:
                continue
            self._human_observer_alive.pop(player_id, None)
            self._human_last_alive_critical.pop(player_id, None)
            self._human_direct_targets.pop(player_id, None)
            self._human_vengeance_until.pop(player_id, None)
            self._source_still.pop(('human', player_id), None)
        for player_id, deadline in tuple(
                self._human_vengeance_until.items()):
            if _number(now) >= float(deadline):
                self._human_vengeance_until.pop(player_id, None)
                self._human_direct_targets.pop(player_id, None)
        return True

    @staticmethod
    def _designated_spot_duration(source, target, snapshot):
        """Apply Designated Target only inside its proved five-degree sector."""
        if not _player_spotting_perk(
                snapshot, source, 'gunner_rancorous'):
            return spotting.SPOT_MEMORY_SECONDS
        source_position = _position(source)
        target_position = target.get('position') or _position(target)
        dx = _number(target_position[0]) - _number(source_position[0])
        dz = _number(target_position[2]) - _number(source_position[2])
        if dx * dx + dz * dz <= 0.000001:
            return spotting.DESIGNATED_SPOT_MEMORY_SECONDS
        bearing = math.atan2(dx, dz)
        gun_yaw = _number(source.get('aim_yaw'), source.get('yaw'))
        if abs(_angle_delta(bearing, gun_yaw)) <= math.radians(5.0) + 1e-9:
            return spotting.DESIGNATED_SPOT_MEMORY_SECONDS
        return spotting.SPOT_MEMORY_SECONDS

    def _human_observation_target(self, raw):
        target = dict(raw)
        player_id = int(raw.get('network_id', raw.get('id', 0)))
        if player_id <= 0:
            raise ValueError('human observation identity is invalid')
        target['kind'] = 'human'
        target['network_id'] = player_id
        target['id'] = self._human_planner_id(player_id)
        target['position'] = _position(raw)
        vehicle_profile = self._player_vehicle_profile(raw)
        target['class_tag'] = vehicle_profile['class_tag']
        target['armor'] = vehicle_profile['armor']
        return target

    @staticmethod
    def _bot_observation_target(bot_id, raw):
        target = dict(raw)
        target['kind'] = 'bot'
        target['network_id'] = int(bot_id)
        target['id'] = int(bot_id)
        target['position'] = _position(raw)
        return target

    def _append_human_observations(
            self, players, now, aggregate, team_visibility,
            visibility_tick=None):
        """Merge trusted human direct observers into the contact batch."""
        human_targets = [
            self._human_observation_target(raw)
            for raw in players or ()
            if (isinstance(raw, dict) and raw.get('id') is not None and
                raw.get('alive', True))]
        bot_targets = [
            self._bot_observation_target(bot_id, raw)
            for bot_id, raw in self.states.items()
            if raw.get('alive', True)]
        targets = human_targets + bot_targets

        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            source = dict(raw)
            source['kind'] = 'human'
            source['network_id'] = int(raw.get('id'))
            source['id'] = source['network_id']
            source_team = int(source.get('team', 0))
            if source_team not in (1, 2) or source['id'] <= 0:
                raise ValueError('human observer identity is invalid')
            alive = bool(source.get('alive', True))
            if alive:
                self._note_human_source_stillness(source, now)
                source_position = _position(source)
                source_view_range = [None]

                def resolve_source_view_range():
                    if source_view_range[0] is None:
                        source_view_range[0] = self._source_view_range(
                            source, now)
                    return source_view_range[0]

                direct_targets = set()
                for target in targets:
                    if int(target.get('team', 0)) == source_team:
                        continue
                    if self._visible(
                            source, target, now, visibility_tick,
                            source_position, resolve_source_view_range):
                        direct_targets.add(self._observer_target_key(target))
                self._human_direct_targets[source['id']] = direct_targets
            elif _number(now) < float(self._human_vengeance_until.get(
                    source['id'], 0.0)):
                direct_targets = set(self._human_direct_targets.get(
                    source['id'], ()))
            else:
                direct_targets = set()

            snapshot = _player_effective_params(source)
            for target in targets:
                if int(target.get('team', 0)) == source_team:
                    continue
                target_key = self._observer_target_key(target)
                key = (source_team, target_key[0], target_key[1])
                direct_visible = target_key in direct_targets
                entry = aggregate.get(key)
                if entry is None:
                    # visible, firing lanes, target, human and bot observers
                    entry = [False, set(), target, set(), set()]
                    aggregate[key] = entry
                entry[0] = bool(entry[0] or direct_visible)
                entry[2] = target
                if not direct_visible:
                    continue
                entry[3].add(source['id'])
                team_visibility[key] = True
                remembered = {
                    'position': _position(target),
                    'x': _number(target.get('x')),
                    'y': _number(target.get('y')),
                    'z': _number(target.get('z')),
                    'yaw': _number(target.get('yaw')),
                    'speed': _number(target.get('speed')),
                }
                self._visible_target_poses[key] = remembered
                entry[2].update(remembered)
                duration = spotting.SPOT_MEMORY_SECONDS
                if alive:
                    duration = self._designated_spot_duration(
                        source, target, snapshot)
                self._renew_team_spot(key, now, duration)
        return True

    def _contacts_for(self, source, players, now, team_spotted=None,
                      visibility_tick=None):
        contacts = []
        lookup = {}
        source_team = int(source.get('team', 0))
        source_position = _position(source)
        source_view_range = [None]
        remembered_team = (
            visibility_tick.setdefault('remembered_team', {})
            if isinstance(visibility_tick, dict) else {})

        def resolve_source_view_range():
            if source_view_range[0] is None:
                source_view_range[0] = self._source_view_range(source, now)
            return source_view_range[0]

        def retain_team_known_pose(target):
            key = (source_team, target.get('kind'),
                   int(target.get('network_id', 0)))
            if target['fresh_visible']:
                remembered = {
                    'position': _position(target),
                    'x': _number(target.get('x')),
                    'y': _number(target.get('y')),
                    'z': _number(target.get('z')),
                    'yaw': _number(target.get('yaw')),
                    'speed': _number(target.get('speed')),
                }
                self._visible_target_poses[key] = remembered
                target.update(remembered)
                return True
            remembered = self._visible_target_poses.get(key)
            if remembered is None:
                # The server treats a first hidden sample as a valid no-op.
                # Publish a shape-complete neutral record, but never expose
                # the worker's omniscient live pose to local targeting.
                target.update({
                    'position': (0.0, 0.0, 0.0),
                    'x': 0.0, 'y': 0.0, 'z': 0.0,
                    'yaw': 0.0, 'speed': 0.0,
                })
                target['visible'] = False
                return False
            target.update(remembered)
            return bool(target.get('visible'))

        def visible_to_team(target):
            key = (source_team, target.get('kind'),
                   int(target.get('network_id', 0)))
            direct_visible = self._visible(
                source, target, now, visibility_tick, source_position,
                resolve_source_view_range)
            if direct_visible:
                self._renew_team_spot(key, now)
                remembered_team[key] = True
            if direct_visible and team_spotted is not None:
                team_spotted[key] = True
            if key not in remembered_team:
                remembered_team[key] = \
                    self._team_spot_time_left(key, now) > 0.0
            remembered = remembered_team[key]
            fresh_shared = bool(
                team_spotted is not None and team_spotted.get(key, False))
            return (bool(direct_visible or remembered or fresh_shared),
                    bool(direct_visible),
                    bool(direct_visible or fresh_shared))

        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'human'
            target['network_id'] = int(raw['id'])
            planner_id = self._human_planner_id(raw['id'])
            target['id'] = planner_id
            target['position'] = _position(raw)
            vehicle_profile = self._player_vehicle_profile(raw)
            target['class_tag'] = vehicle_profile['class_tag']
            target['armor'] = vehicle_profile['armor']
            (target['visible'], target['direct_visible'],
             target['fresh_visible']) = visible_to_team(target)
            if retain_team_known_pose(target):
                lookup[planner_id] = target
            contacts.append(target)
        for bot_id, raw in self.states.items():
            if (bot_id == source.get('id') or not raw.get('alive', True) or
                    int(raw.get('team', 0)) == source_team):
                continue
            target = dict(raw)
            target['kind'] = 'bot'
            target['network_id'] = int(bot_id)
            target['position'] = _position(raw)
            (target['visible'], target['direct_visible'],
             target['fresh_visible']) = visible_to_team(target)
            if retain_team_known_pose(target):
                lookup[int(bot_id)] = target
            contacts.append(target)
        return contacts, lookup

    def _index_live_players(self, players):
        """Index current human records once for one rendered update."""
        live_players = {}
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            live_players[self._human_planner_id(raw['id'])] = raw
        return live_players

    @staticmethod
    def _overlay_target_state(cached, live):
        """Copy one cached contact and overlay one canonical live record."""
        target = dict(cached)
        if live is not None:
            if target.get('fresh_visible') is True:
                target['position'] = _position(live)
                pose_fields = ('x', 'y', 'z', 'yaw', 'speed')
            else:
                pose_fields = ()
            for name in (('alive', 'health', 'max_health', 'team') +
                         pose_fields):
                if name in live:
                    target[name] = live[name]
        return target

    def _refresh_target_pose(self, planner_id, cached, live_players):
        """Refresh state without replacing a hidden contact's known pose."""
        if cached.get('kind') == 'human':
            live = live_players.get(planner_id)
        else:
            live = self.states.get(int(cached.get(
                'network_id', planner_id)))
        return self._overlay_target_state(cached, live)

    def _probe_target_pose(self, planner_id, cached, live_players,
                           probe_targets, processed_bot_ids):
        """Share only the canonical fields consumed by static lane probes.

        Spotting visibility belongs to the observer's cached contact and is
        read from that record separately.  Static firing-lane rays consume the
        target identity and current pose. A copied bot has at most two poses
        while sources are processed: before and after its own integration.
        """
        kind = cached.get('kind')
        target_id = int(cached.get('network_id', planner_id))
        phase = bool(kind == 'bot' and target_id in processed_bot_ids)
        key = (kind, target_id, phase)
        target = probe_targets.get(key)
        if target is None:
            target = self._refresh_target_pose(
                planner_id, cached, live_players)
            probe_targets[key] = target
        return target

    def _refresh_target_poses(self, targets, players=None,
                              live_players=None, probe_targets=None,
                              processed_bot_ids=None):
        """Overlay target poses for one source at its copied-motion boundary."""
        if live_players is None:
            live_players = self._index_live_players(players)
        if probe_targets is None:
            probe_targets = {}
        if processed_bot_ids is None:
            processed_bot_ids = set()
        refreshed = {}
        for planner_id, cached in (targets or {}).items():
            refreshed[planner_id] = self._probe_target_pose(
                planner_id, cached, live_players, probe_targets,
                processed_bot_ids)
        return refreshed

    @staticmethod
    def _world_receipt_contains(receipt, position, travel_yaw, speed, dt):
        """Return whether exact typed rays still contain this hull sweep.

        The planning sample has a deliberately short lifetime because slope and
        steering alternatives must be refreshed often.  The typed 3x3 receipt
        owns its own exact origin, yaw and direction, so a later planning sample
        may retain it while the current hull sweep remains a strict subset.
        """
        if not isinstance(receipt, dict):
            return False
        origin = receipt.get('origin')
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            return False
        receipt_yaw = _number(receipt.get('yaw'))
        receipt_sign = int(_number(receipt.get('direction')))
        current_sign = -1 if _number(speed) < 0.0 else 1
        if receipt_sign not in (-1, 1) or receipt_sign != current_sign:
            return False
        rdx = _number(position[0]) - _number(origin[0])
        rdy = abs(_number(position[1]) - _number(origin[1]))
        rdz = _number(position[2]) - _number(origin[2])
        rsine, rcosine = math.sin(receipt_yaw), math.cos(receipt_yaw)
        receipt_forward = rdx * rsine + rdz * rcosine
        receipt_lateral = abs(rdx * rcosine - rdz * rsine)
        receipt_angle = abs(_angle_delta(travel_yaw, receipt_yaw))
        leading = max(0.0, _number(receipt.get('leading')))
        distance = max(0.0, _number(receipt.get('distance')))
        frame_step = max(0.0, min(0.2, _number(dt)))
        current_reach = max(
            0.4, abs(_number(speed)) * frame_step + 0.2)
        return bool(
            receipt_forward >= -0.0001 and
            receipt_forward + leading + current_reach <= distance and
            rdy <= 0.0001 and receipt_lateral <= 0.0001 and
            receipt_angle <= 0.00001)

    @staticmethod
    def _world_receipt_refresh_due(receipt, position, travel_yaw, speed, dt):
        """Refresh a contained receipt before its remaining corridor expires."""
        if not BotRuntime._world_receipt_contains(
                receipt, position, travel_yaw, speed, dt):
            return False
        origin = receipt['origin']
        receipt_yaw = _number(receipt.get('yaw'))
        dx = _number(position[0]) - _number(origin[0])
        dz = _number(position[2]) - _number(origin[2])
        forward = dx * math.sin(receipt_yaw) + dz * math.cos(receipt_yaw)
        frame_step = max(0.0, min(0.2, _number(dt)))
        current_reach = max(
            0.4, abs(_number(speed)) * frame_step + 0.2)
        remaining = (
            max(0.0, _number(receipt.get('distance'))) - forward -
            max(0.0, _number(receipt.get('leading'))) - current_reach)
        # At the supported 24 FPS floor, six metres covers four maximum-speed
        # frames while the 13-job fair queue drains a 29-Bot cohort in three.
        return remaining <= 6.0

    @staticmethod
    def _contained_cached_world_receipt(cached, position, travel_yaw,
                                         speed, dt):
        """Return one contained typed receipt independently of plan expiry."""
        if not isinstance(cached, dict):
            return None
        result = cached.get('result')
        if (not isinstance(result, dict) or
                result.get('deferred', False) or
                not BotRuntime._probe_is_clear(result)):
            return None
        receipt = result.get('world_receipt')
        if BotRuntime._world_receipt_contains(
                receipt, position, travel_yaw, speed, dt):
            return receipt
        return None

    @staticmethod
    def _motion_probe_reusable(cached, position, travel_yaw, speed, now,
                               settled=False, dt=None,
                               ignore_deadline=False):
        """Prove that a cached hull corridor still contains this motion ray."""
        if not isinstance(cached, dict):
            return False
        if isinstance(cached.get('result'), dict) and cached['result'].get(
                'deferred', False):
            # Exhausting the shared native recast budget proves neither a wall
            # nor a soft path. Retry next frame instead of pinning this Bot's
            # fixed-id cache to a false answer.
            return False
        sample_position = cached.get('position')
        if not isinstance(sample_position, (list, tuple)) or len(sample_position) != 3:
            return False
        sample_yaw = _number(cached.get('yaw'))
        dx = _number(position[0]) - _number(sample_position[0])
        dy = abs(_number(position[1]) - _number(sample_position[1]))
        dz = _number(position[2]) - _number(sample_position[2])
        sine, cosine = math.sin(sample_yaw), math.cos(sample_yaw)
        forward = dx * sine + dz * cosine
        lateral = abs(dx * cosine - dz * sine)
        angle = abs(_angle_delta(travel_yaw, sample_yaw))
        # A fully settled hull cannot enter a new corridor. Preserve its
        # established slope while its pose and heading stay exact; the first
        # movement, turn, collision push or slide restores the normal expiry.
        if settled:
            return bool(
                abs(forward) <= 0.05 and lateral <= 0.05 and dy <= 0.05 and
                angle <= 0.005)
        if (not ignore_deadline and
                now >= cached.get('deadline', 0.0)):
            return False
        lookahead = 20.0 if abs(_number(speed)) > 5.0 else 15.0
        heading_drift = lookahead * abs(math.sin(angle))
        reusable = bool(
            -0.1 <= forward <= MOTION_PROBE_FORWARD_BUDGET and
            lateral + heading_drift <= MOTION_PROBE_LATERAL_BUDGET)
        if not reusable:
            return False
        receipt = (cached.get('result') or {}).get('world_receipt')
        if (receipt is not None and
                not BotRuntime._world_receipt_contains(
                    receipt, position, travel_yaw, speed, dt)):
            return False
        return True

    @staticmethod
    def _motion_probe_covers_distance(cached, maximum_distance):
        """Do not reuse a short turn probe for a later, farther target."""
        if not isinstance(cached, dict):
            return False
        cached_distance = cached.get('maximum_distance')
        if maximum_distance is None:
            return cached_distance is None
        if cached_distance is None:
            return True
        return (_number(cached_distance) + 1.0e-6 >=
                _number(maximum_distance))

    def motion_world_receipt_reusable(self, bot_id, position, travel_yaw,
                                      speed, now, dt):
        """Return whether the current exact hull rays reuse a typed receipt."""
        cached = self._motion_probe_cache.get(int(bot_id))
        if not isinstance(cached, dict):
            return False
        result = cached.get('result')
        if not isinstance(result, dict) or not isinstance(
                result.get('world_receipt'), dict):
            return False
        return self._motion_probe_reusable(
            cached, position, travel_yaw, speed, now, False, dt)

    def motion_world_corridor_reusable(self, bot_id, position, travel_yaw,
                                       speed, now, dt):
        """Reuse exact rays or a generic sweep awaiting its exact receipt.

        The generic dual-height, three-lane sweep is a complete native world
        query. An exact 3x3 receipt only avoids repeating that work at commit
        time; exhausting its fair per-frame queue is not a physical collision
        and must not freeze authoritative motion.
        """
        cached = self._motion_probe_cache.get(int(bot_id))
        if not isinstance(cached, dict):
            return False
        result = cached.get('result')
        if not isinstance(result, dict):
            return False
        exact = isinstance(result.get('world_receipt'), dict)
        pending = bool(result.get('_world_receipt_pending', False))
        if not (exact or pending):
            return False
        if exact:
            return bool(
                not result.get('deferred', False) and
                self._probe_is_clear(result) and
                self._world_receipt_contains(
                    result.get('world_receipt'), position, travel_yaw,
                    speed, dt))
        return self._motion_probe_reusable(
            cached, position, travel_yaw, speed, now, False, dt,
            ignore_deadline=True)

    def _neighbours_for(self, source, supplied, spatial_index=None,
                        traffic_bodies=None):
        if spatial_index is not None and traffic_bodies is not None:
            position = _position(source)
            result = []
            for body_id in tank_collision.nearby_ids(
                    spatial_index, position[0], position[2]):
                if body_id == source.get('id'):
                    continue
                body = traffic_bodies.get(body_id)
                if body is not None:
                    result.append(body)
            return result
        result = list(supplied or ())
        for bot_id, raw in self.states.items():
            if bot_id == source.get('id'):
                continue
            speed = (_number(raw.get('speed'))
                     if raw.get('alive', True) else 0.0)
            result.append({
                'id': bot_id, 'position': _position(raw),
                'team': int(_number(raw.get('team'))),
                'yaw': _number(raw.get('yaw')),
                'velocity': (
                    math.sin(_number(raw.get('yaw'))) * speed,
                    0.0,
                    math.cos(_number(raw.get('yaw'))) * speed),
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            })
        return result

    def _traffic_snapshot(self, supplied):
        """Build one immutable local-traffic snapshot for this authority tick."""
        bodies = {}
        for raw in supplied or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            body = dict(raw)
            body['position'] = _position(raw)
            bodies[raw['id']] = body
        for bot_id, raw in self.states.items():
            yaw = _number(raw.get('yaw'))
            speed = (_number(raw.get('speed'))
                     if raw.get('alive', True) else 0.0)
            bodies[bot_id] = {
                'id': bot_id, 'position': _position(raw), 'yaw': yaw,
                'team': int(_number(raw.get('team'))),
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
                'half_length': _number(raw.get('half_length'), 3.5),
                'half_width': _number(raw.get('half_width'), 1.7),
            }
        return bodies, tank_collision.build_spatial_index(bodies)

    def apply_native_pose(self, bot_id, position, yaw, speed):
        """Feed a sampled #1513 WGVehiclePhysics pose back to the AI law."""
        try:
            state = self.states.get(int(bot_id))
            if state is None:
                return False
            if isinstance(position, dict):
                point = _position(position)
            else:
                point = (_number(position[0]), _number(position[1]),
                         _number(position[2]))
            state['x'], state['y'], state['z'] = point
            state['yaw'] = _number(yaw, state.get('yaw', 0.0))
            state['speed'] = _number(speed, state.get('speed', 0.0))
            return True
        except (TypeError, ValueError):
            return False

    def _terrain_support(self, state):
        """Probe centre first, then the 0.8.2 edge fallback when unsupported."""
        position = _position(state)
        yaw = _number(state.get('yaw'))
        half_length = max(1.5, _number(state.get('half_length'), 3.5))
        sine, cosine = math.sin(yaw), math.cos(yaw)
        self._probe_totals[3] += 1
        probe_started = self._probe_started()
        try:
            centre = self._physics_ground_probe(
                position[0], position[2], position[1])
        finally:
            self._probe_finished(3, probe_started)
        if centre is not None:
            centre = float(centre)
            # The vertical law below always selects centre while it exists;
            # front/back could not affect the realised pose on this branch.
            return centre, centre
        highest = None
        for distance in (half_length, -half_length):
            x = position[0] + sine * distance
            z = position[2] + cosine * distance
            self._probe_totals[3] += 1
            probe_started = self._probe_started()
            try:
                value = self._physics_ground_probe(x, z, position[1])
            finally:
                self._probe_finished(3, probe_started)
            if value is None:
                continue
            value = float(value)
            if highest is None or value > highest:
                highest = value
        return highest, centre

    def _log_direction_flip(self, state, path_clear, motion_probe, now):
        """Log rapid drive reversals with the corridor verdict behind them."""
        if not self.debug_logging:
            return False
        direction = int(state.get('movement_dir', 0))
        bot_id = state['id']
        diary = self._flip_diary.get(bot_id)
        if diary is None:
            self._flip_diary[bot_id] = {
                'dir': direction, 'changed': _number(now), 'logged': -10.0}
            return False
        previous = diary['dir']
        if direction == previous:
            return False
        elapsed = _number(now) - diary['changed']
        diary['dir'] = direction
        diary['changed'] = _number(now)
        if (direction == 0 or previous == 0 or elapsed > 2.0 or
                _number(now) - diary['logged'] < 1.0):
            return False
        diary['logged'] = _number(now)
        verdict = 'none'
        if isinstance(motion_probe, dict):
            verdict = 'clear=%s collision=%s deferred=%s' % (
                bool(motion_probe.get('clear')),
                bool(motion_probe.get('collision')),
                bool(motion_probe.get('deferred')))
        print('[BOT FLIP] id=%s reversed %+d->%+d after %.2fs at '
              '(%.1f,%.1f) path_clear=%s probe=%s' % (
                  bot_id, previous, direction, elapsed,
                  _number(state.get('x')), _number(state.get('z')),
                  bool(path_clear), verdict))
        return True

    def set_camera_position(self, position):
        """Publish the viewpoint that drives the presentation detail tiers."""
        if position is None:
            self._camera_position = None
            return False
        self._camera_position = (
            _number(position[0]), _number(position[1]), _number(position[2]))
        return True

    def _detail_tier(self, state):
        """Return 0 near the camera, 1 at medium range, 2 far away."""
        camera = self._camera_position
        if camera is None:
            return 0
        dx = _number(state.get('x')) - camera[0]
        dz = _number(state.get('z')) - camera[2]
        distance_sq = dx * dx + dz * dz
        if distance_sq <= DETAIL_NEAR_METRES * DETAIL_NEAR_METRES:
            return 0
        if distance_sq <= DETAIL_FAR_METRES * DETAIL_FAR_METRES:
            return 1
        return 2

    def _planner_corridor_clear(self, position, yaw, speed,
                                wet_escape=False, allow_shallow=False,
                                hazard_only=False):
        """Rank one candidate through the validated baked static corridor.

        ``True`` admits a planner candidate and ``False`` rejects a known fatal
        hazard. ``None`` asks the caller to retain the native planner probe
        because the shipped graph is unavailable, failed, or only produced an
        ambiguous corridor negative. This result is never stored in the native
        motion cache and can never authorize a realised step.
        """
        if wet_escape:
            return None
        navigator = self.navigator
        grid = getattr(navigator, 'grid', None)
        graph = self.baked_graph
        bake = graph.get('bake') if isinstance(graph, dict) else None
        clearance_radii = (bake.get('edge_clearance_radii')
                           if isinstance(bake, dict) else ())
        if (grid is None or not getattr(grid, 'prebaked', False) or
                not isinstance(bake, dict) or
                _number(bake.get('vehicle_half_width')) < 2.15 or
                not isinstance(clearance_radii, (list, tuple)) or
                max([_number(value) for value in clearance_radii] or [0.0]) <
                3.0):
            return None
        try:
            if not grid.near_baked_navigation(position, 0):
                return None
            # Rank against one baked edge. Looking 15-20 metres straight ahead
            # rejects valid four-metre turns in tight spawn exits even though the
            # navigator explicitly selected that adjacent linked cell. The native
            # motion gate and per-frame hull sweep still prove realised travel.
            distance = max(1.0, _number(getattr(grid, 'cell_size', 1.0)))
            sine = math.sin(float(yaw))
            cosine = math.cos(float(yaw))
            end = (
                _number(position[0]) + sine * distance,
                _number(position[1]),
                _number(position[2]) + cosine * distance,
            )
            hazard_mask = BAKED_FATAL_HAZARDS
            if not allow_shallow:
                hazard_mask |= BAKED_SHALLOW_WATER
            if grid.segment_has_baked_hazard(position, end, hazard_mask):
                return False
            if hazard_only:
                return True
            if grid.segment_clear(position, end):
                return True
            # A coarse baked corridor can reject a valid short turn on uneven
            # terrain. Let the native candidate probe decide that ambiguous
            # negative; fatal baked hazards above remain an immediate veto.
            return None
        except Exception:
            # The graph is a planner optimisation. Unknown graph state keeps
            # the old native candidate probe; it never becomes a clear path.
            return None

    def _update_slope_pose(self, state, allow_ungrounded=False):
        """Refresh the four-point hull pose after this tick's ground settle."""
        if (state.get('airborne', False) or
                (not allow_ungrounded and not state.get(
                    'grounded_once', False))):
            return False
        yaw = _number(state.get('yaw'))
        x = _number(state.get('x'))
        z = _number(state.get('z'))
        tier = self._detail_tier(state)
        travel = SLOPE_SAMPLE_METRES[tier]
        turn = SLOPE_SAMPLE_RADIANS[tier]
        marker = state.get('pose_sample')
        if (isinstance(marker, (list, tuple)) and len(marker) == 3 and
                abs(x - _number(marker[0])) < travel and
                abs(z - _number(marker[1])) < travel and
                abs(yaw - _number(marker[2])) < turn):
            return False

        def probe(sample_x, sample_z, hint):
            self._probe_totals[3] += 1
            probe_started = self._probe_started()
            try:
                return self._physics_ground_probe(sample_x, sample_z, hint)
            finally:
                self._probe_finished(3, probe_started)

        suspension_pitch = _number(state.get('suspension_pitch'))
        terrain_pitch = _number(
            state.get('terrain_pitch'),
            _number(state.get('pitch')) - suspension_pitch)
        pitch, roll = slope_pose(
            probe, (x, _number(state.get('y')), z), yaw,
            _number(state.get('half_length'), 3.5),
            _number(state.get('half_width'), 1.7),
            terrain_pitch, _number(state.get('roll')))
        state['terrain_pitch'] = pitch
        state['pitch'] = pitch + suspension_pitch
        state['roll'] = roll
        state['pose_sample'] = (x, z, yaw)
        return True

    def _passive_motion_status(self, state, position, yaw, speed,
                               descriptor, step, now, commit_enabled=False):
        """Resolve glancing travel without enabling active-drive crush."""
        if not callable(self.motion_resolver):
            return 'clear'
        had_movement_dir = 'movement_dir' in state
        movement_dir = state.get('movement_dir')
        # BattleRuntime's resolver infers active crush intent from this field.
        state['movement_dir'] = 0
        try:
            if self._probe_clock is None:
                status = self.motion_resolver(
                    state['id'], position, yaw, speed,
                    descriptor, step, now, commit_enabled)
            else:
                probe_started = self._probe_started()
                try:
                    status = self.motion_resolver(
                        state['id'], position, yaw, speed,
                        descriptor, step, now, commit_enabled)
                finally:
                    self._probe_finished(4, probe_started)
        finally:
            if had_movement_dir:
                state['movement_dir'] = movement_dir
            else:
                state.pop('movement_dir', None)
        if status not in ('clear', 'crushed', 'soft', 'hard'):
            raise RuntimeError(
                'bot passive motion resolver returned an invalid status')
        return status

    def _hard_contact_response(self, state, position, yaw, speed,
                               descriptor, step, now):
        """Probe the shared glancing paths and apply copied hull damping."""
        slide_yaw = None
        for candidate_yaw in vehicle_physics.hard_contact_candidate_yaws(yaw):
            if callable(self.motion_resolver):
                status = self._passive_motion_status(
                    state, position, candidate_yaw, speed,
                    descriptor, step, now, commit_enabled=False)
                clear = status in ('clear', 'crushed')
            else:
                clear = self._probe_is_clear(self._probe_direction(
                    position, candidate_yaw, speed, descriptor))
            if clear:
                if callable(self.motion_resolver):
                    status = self._passive_motion_status(
                        state, position, candidate_yaw, speed,
                        descriptor, step, now, commit_enabled=True)
                    if status in ('clear', 'crushed'):
                        slide_yaw = candidate_yaw
                else:
                    slide_yaw = candidate_yaw
                break
        bot_id = int(state['id'])
        speed, delta_x, delta_z = vehicle_physics.hard_contact_step(
            speed, step,
            grinding=self._hard_contact_grinds.get(bot_id, 0) > 0,
            slide_yaw=slide_yaw)
        self._hard_contact_grinds[bot_id] = (
            vehicle_physics.HARD_CONTACT_GRIND_TICKS)
        return (speed,
                (position[0] + delta_x, position[1], position[2] + delta_z),
                slide_yaw is not None)

    def _invalidate_realised_motion(self, bot_id, attempted_yaw):
        """Forget a command whose committed pose hit a real obstacle."""
        self._decision_cache.pop(bot_id, None)
        self._motion_probe_cache.pop(bot_id, None)
        driver = getattr(self.adapter, 'driver', None)
        remember = getattr(driver, 'remember_failure', None)
        if callable(remember):
            remember(bot_id, attempted_yaw, 5.0)

    def _apply_bot_fall_damage(self, state, impact_speed):
        """Apply the shared landing law to one hidden-worker Bot."""
        maximum = max(1, int(_number(
            state.get('max_health'), state.get('health', 1))))
        damage = vehicle_physics.fall_damage(maximum, impact_speed)
        if damage <= 0:
            return 0
        health = max(0, int(_number(state.get('health'), maximum)) - damage)
        state['health'] = health
        state['display_health'] = health
        state['alive'] = health > 0
        if state['alive']:
            return damage
        descriptor = self._descriptors.get(state['id'], {})
        terminal = _terminal_critical(state, descriptor, 'world_collision')
        if terminal is not None:
            state['critical'] = terminal
        state['combat_fire_elapsed'] = 0.0
        state['combat_fire_timer'] = 0.0
        self._friendly_repositions.pop(state['id'], None)
        state['death_reason'] = 3
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['target_kind'] = None
        state['target_id'] = None
        self._turn_speeds[state['id']] = 0.0
        return damage

    def _apply_bot_landing_impact(self, state, vertical_speed):
        """Combine retained lateral velocity with the vertical impact."""
        lateral_x = _number(state.get('air_lateral_x'))
        lateral_z = _number(state.get('air_lateral_z'))
        lateral_speed = math.sqrt(
            lateral_x * lateral_x + lateral_z * lateral_z)
        if lateral_speed > 0.01:
            state['slide_speed'] = max(
                _number(state.get('slide_speed')), lateral_speed)
        state['air_lateral_x'] = 0.0
        state['air_lateral_z'] = 0.0
        impact_speed = math.sqrt(
            _number(vertical_speed) * _number(vertical_speed) +
            lateral_speed * lateral_speed)
        return self._apply_bot_fall_damage(state, impact_speed)

    def _update_vertical_motion(self, state, step, tick_pose=None,
                                attempted_yaw=None):
        """Run grounded/ballistic phases and reject false raised support."""
        highest, centre = self._terrain_support(state)
        # Front/rear hits keep a bot supported across a narrow ditch, but use
        # their real CoM distance below so a remote valley floor cannot pull
        # the bot down in one tick.
        ground = centre if centre is not None else highest
        if ground is not None:
            speed = abs(_number(state.get('speed')))
            snap_gap = vehicle_physics.ground_follow_gap(
                _number(state.get('speed')),
                _number(state.get('last_drive_pitch')), step)
            max_climb = max(0.6, speed * step * 2.5)
            com_gap = state['y'] - ground
            land_y = ground if centre is None else centre
            if not state.get('grounded_once', False):
                state['y'] = land_y
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                state['grounded_once'] = True
            elif tank_collision.support_rise_is_obstacle(
                    state.get('y'), centre, max_climb):
                # The centre ray hit a wagon deck, roof, or large prop only
                # after this tick's horizontal integration put the hull partly
                # inside it. Restore only this tick's pose and let LocalDriver
                # choose its normal reverse/turn recovery on the next update.
                if tick_pose is not None:
                    state['x'], state['y'], state['z'] = tick_pose
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['push_x'] = 0.0
                state['push_z'] = 0.0
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                state.pop('destructible_contact_speed', None)
                self._turn_speeds[state['id']] = 0.0
                self._invalidate_realised_motion(
                    state['id'],
                    (_number(state.get('yaw')) if attempted_yaw is None
                     else attempted_yaw))
                return True
            elif (state['y'] <= ground or
                  (com_gap <= snap_gap and not state.get('airborne', False))):
                impact_speed = (_number(state.get('vertical_speed'))
                                if state.get('airborne', False) else 0.0)
                if state['y'] < ground:
                    rise = ground - state['y']
                    state['y'] += min(rise, max_climb)
                else:
                    state['y'] += ((ground - state['y']) *
                                   min(1.0, step * 15.0))
                    state['y'] = min(state['y'], ground + 0.12)
                state['vertical_speed'] = 0.0
                state['airborne'] = False
                if impact_speed < 0.0:
                    self._apply_bot_landing_impact(state, impact_speed)
            else:
                if not state.get('airborne', False):
                    pitch = _number(state.get('last_drive_pitch'))
                    state['vertical_speed'] = (
                        vehicle_physics.launch_vertical_speed(
                            _number(state.get('speed')), pitch))
                state['airborne'] = True
                substeps = min(8, max(
                    1, int(abs(_number(state.get('vertical_speed')) * step) /
                           0.5) + 1))
                sub_step = step / float(substeps)
                for unused_step in range(substeps):
                    state['vertical_speed'] -= (
                        vehicle_physics.GRAVITY * sub_step)
                    state['y'] += state['vertical_speed'] * sub_step
                    if state['y'] <= land_y:
                        impact_speed = state['vertical_speed']
                        state['y'] = land_y
                        state['vertical_speed'] = 0.0
                        state['airborne'] = False
                        self._apply_bot_landing_impact(
                            state, impact_speed)
                        break
        elif state.get('grounded_once', False):
            if not state.get('airborne', False):
                state['vertical_speed'] = (
                    vehicle_physics.launch_vertical_speed(
                        _number(state.get('speed')),
                        _number(state.get('last_drive_pitch'))))
            state['airborne'] = True
            state['vertical_speed'] -= vehicle_physics.GRAVITY * step
            state['y'] += state['vertical_speed'] * step
        else:
            # Terrain streaming owns the first placement. A missing first hit
            # must not turn map loading into a fictitious fall from altitude.
            state['vertical_speed'] = 0.0
            state['airborne'] = False
        return False

    def _guard_realised_pose(self, state, tick_pose, tick_was_safe,
                             attempted_yaw):
        """Reject a new hazard or outward map-edge drift after all motion."""
        realised_pose = _position(state)
        moved_farther_outside = not self._baked_pose_progress_clear(
            state, tick_pose, state.get('yaw'),
            realised_pose, state.get('yaw'))
        if (not moved_farther_outside and
                (not tick_was_safe or
                 prebaked_navigation.pose_is_safe(
                     self.baked_graph, realised_pose, shoulder_cells=0))):
            return False
        state['x'], state['y'], state['z'] = tick_pose
        state['speed'] = 0.0
        state['movement_dir'] = 0
        state['rotation_dir'] = 0
        state['push_x'] = 0.0
        state['push_z'] = 0.0
        state['vertical_speed'] = 0.0
        state['airborne'] = False
        self._invalidate_realised_motion(state['id'], attempted_yaw)
        return True

    def _baked_boundary_overflow(self, state, position, yaw):
        """Return per-edge chassis overflow past the authored rectangle.

        Arena bounds constrain the complete vehicle, not only its centre.
        Projecting the exact Bot collision dimensions here keeps this final
        authority guard consistent with the local player's boundary gate.
        """
        graph = self.baked_graph
        if not isinstance(graph, dict):
            return None
        try:
            bounds = tuple(float(value) for value in graph['bounds'])
            if len(bounds) != 4:
                return None
            minimum_x, minimum_z, maximum_x, maximum_z = bounds
            x = float(position[0])
            z = float(position[2])
            hull_yaw = float(yaw)
            half_length = max(0.5, float(state.get('half_length', 3.5)))
            half_width = max(0.3, float(state.get('half_width', 1.7)))
            values = bounds + (x, z, hull_yaw, half_length, half_width)
            if (minimum_x >= maximum_x or minimum_z >= maximum_z or
                    any(math.isnan(value) or math.isinf(value)
                        for value in values)):
                return None
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return None
        sine = abs(math.sin(hull_yaw))
        cosine = abs(math.cos(hull_yaw))
        extent_x = cosine * half_width + sine * half_length
        extent_z = sine * half_width + cosine * half_length
        return (
            max(0.0, minimum_x + extent_x - x),
            max(0.0, x + extent_x - maximum_x),
            max(0.0, minimum_z + extent_z - z),
            max(0.0, z + extent_z - maximum_z),
        )

    def _baked_pose_progress_clear(self, state, before_position, before_yaw,
                                   after_position, after_yaw):
        """Allow a legal pose or recovery that worsens no boundary edge."""
        before = self._baked_boundary_overflow(
            state, before_position, before_yaw)
        after = self._baked_boundary_overflow(
            state, after_position, after_yaw)
        if before is None or after is None:
            return True
        return all(after[index] <= before[index] + 1.0e-6
                   for index in range(4))

    def _navigation_target(self, bot_id, position, goal, strategic, state):
        mode = strategic.get('combat_mode', 'route')
        stop_at_goal = mode not in ('route', 'advance')
        if self.navigator is None:
            state['navigation_stop_at_target'] = stop_at_goal
            return goal
        if _distance(position, goal) <= 15.0:
            grid = getattr(self.navigator, 'grid', None)
            direct = getattr(grid, 'dry_segment_clear', None)
            if callable(direct) and direct(
                    position, goal, state.get('now', 0.0)):
                state['navigation_stop_at_target'] = stop_at_goal
                return goal
        route_index = int(_number(strategic.get('route_index'), 0))
        if mode == 'base_defense':
            path_key = (
                'local', int(bot_id), 'base_defense',
                str(strategic.get('defense_base_id') or 'own_base'))
            anchor = None
        elif mode in ('route', 'advance', 'hold'):
            anchor = (strategic.get('route_anchor')
                      if bool(strategic.get('route_join')) else None)
            if anchor is not None:
                # A route's first shared path used to be cached from whichever
                # spawn slot requested it first. Every following tank then
                # converged onto that one hull's egress line. Keep the strategic
                # destination shared, but join it from each real slot through a
                # bot-scoped terrain path. Later route segments remain shared.
                path_key = (
                    'route_join', int(bot_id),
                    int(self.states[bot_id].get('team', 0)),
                    strategic.get('route_id', 'direct'), route_index)
            else:
                path_key = (
                    'route', int(self.states[bot_id].get('team', 0)),
                    strategic.get('route_id', 'direct'), route_index)
        else:
            path_key = ('local', int(bot_id), mode,
                        strategic.get('target_id'))
            anchor = None
        # Moving hulls are not terrain. The simultaneous tank-contact solver
        # keeps them solid and LocalDriver separates an existing overlap. Feeding
        # every live neighbour back into the navigation fallback made a moving
        # teammate repeatedly change the selected waypoint and produced visible
        # drive/stop cycles, especially for slow heavy tanks.
        avoid = None
        grid = getattr(self.navigator, 'grid', None)
        cell_size = max(1.0, _number(getattr(grid, 'cell_size', 1.0), 1.0))
        lookahead_distance = max(
            cell_size * 2.0,
            abs(_number(state.get('speed'))) *
            BAKED_MOTION_LOOKAHEAD_SECONDS)
        target = self.navigator.next_target(
            bot_id, position, goal, path_key, state.get('now', 0.0),
            anchor, avoid, lookahead_distance)
        terminal = getattr(self.navigator, 'target_is_terminal', None)
        state['navigation_stop_at_target'] = bool(
            stop_at_goal and callable(terminal) and terminal(bot_id))
        return target

    @staticmethod
    def _player_neighbours(players):
        result = []
        for raw in players or ():
            if (not isinstance(raw, dict) or raw.get('id') is None or
                    not raw.get('alive', True)):
                continue
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed'))
            result.append({
                'id': HUMAN_TARGET_ID_BASE + int(raw['id']),
                'position': _position(raw),
                'team': int(_number(raw.get('team'))), 'yaw': yaw,
                'velocity': (math.sin(yaw) * speed, 0.0,
                             math.cos(yaw) * speed),
            })
        return result

    @staticmethod
    def _traffic_stopping_distance(
            speed, physics_params, slope_pitch=0.0, steering=False):
        """Integrate the copied coast law to the first stationary pose.

        Traffic is evaluated before this authority tick's longitudinal step,
        so there is no separate reaction-distance term.  Advancing with the
        same nominal 30 Hz semi-implicit step used by copied physics gives the
        actual forward travel remaining after throttle is released.  A grade
        which cannot reduce forward speed has no finite stopping distance.
        """
        current = abs(_number(speed))
        if current <= TRAFFIC_DIRECTION_SPEED_EPSILON:
            return 0.0
        step = PUBLICATION_SECONDS
        distance = 0.0
        # Valid #1513 vehicle parameters settle in a few dozen steps.  This is
        # only a finite-number guard for malformed/tuned inputs, not a traffic
        # distance coefficient.
        for unused_step in range(4096):
            following = vehicle_physics.longitudinal_step(
                physics_params, current, 0.0, bool(steering),
                float(slope_pitch), step, False, 0, False)
            if math.isnan(following) or math.isinf(following):
                return float('inf')
            if following <= TRAFFIC_DIRECTION_SPEED_EPSILON:
                return distance + max(0.0, following) * step
            if following >= current:
                return float('inf')
            distance += following * step
            current = following
        return float('inf')

    def _cached_traffic_stopping_distance(
            self, source, command, physics_params):
        """Memoize the exact coast integral for unchanged physical inputs."""
        bot_id = int(_number(source.get('id')))
        speed = abs(_number(source.get('speed')))
        slope_pitch = _number(source.get('last_drive_pitch'))
        steering = abs(_number(command.get('turn'))) > 0.01
        key = (id(physics_params), speed, slope_pitch, steering)
        cached = self._traffic_stopping_cache.get(bot_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        distance = self._traffic_stopping_distance(
            speed, physics_params, slope_pitch, steering)
        self._traffic_stopping_cache[bot_id] = (key, distance)
        return distance

    @staticmethod
    def _traffic_lateral_separated(
            lateral, own_width, own_length, other_width, other_length):
        """Return a strict OBB circumcircle separation for corridor travel."""
        return bool(
            float(lateral) >
            math.hypot(float(own_width), float(own_length)) +
            math.hypot(float(other_width), float(other_length)))

    @staticmethod
    def _traffic_obb_clearance(
            dx, dz, corridor_yaw, own_yaw, own_width, own_length,
            other_yaw, other_width, other_length):
        """Return exact forward travel to first contact with a frozen OBB.

        Continuous SAT intersects the travel intervals on both local axes of
        both hulls.  Unlike independent forward/lateral projection bounds,
        this does not invent a blocker where two rotated hull corners pass one
        another without their OBBs ever meeting.
        """
        direction_x = math.sin(corridor_yaw)
        direction_z = math.cos(corridor_yaw)

        def body_axes(yaw):
            sine = math.sin(yaw)
            cosine = math.cos(yaw)
            return ((cosine, -sine), (sine, cosine))

        own_axes = body_axes(own_yaw)
        other_axes = body_axes(other_yaw)
        entry = 0.0
        leave = float('inf')
        for axis_x, axis_z in own_axes + other_axes:
            centre = dx * axis_x + dz * axis_z
            rate = direction_x * axis_x + direction_z * axis_z
            radius = (
                abs(own_axes[0][0] * axis_x +
                    own_axes[0][1] * axis_z) * own_width +
                abs(own_axes[1][0] * axis_x +
                    own_axes[1][1] * axis_z) * own_length +
                abs(other_axes[0][0] * axis_x +
                    other_axes[0][1] * axis_z) * other_width +
                abs(other_axes[1][0] * axis_x +
                    other_axes[1][1] * axis_z) * other_length)
            if abs(rate) <= 1e-12:
                if abs(centre) > radius:
                    return float('inf')
                continue
            first = (centre - radius) / rate
            last = (centre + radius) / rate
            entry = max(entry, min(first, last))
            leave = min(leave, max(first, last))
            if entry > leave:
                return float('inf')
        if leave < 0.0:
            return float('inf')
        return entry

    @staticmethod
    def _traffic_throttle(source, command, neighbours, physics_params=None,
                          stopping_distance_resolver=None):
        """Return ``(throttle, waiting)`` for nearby friendly traffic.

        Same-lane followers always respect the vehicle ahead. At a crossing or
        merge, the lower bot id has deterministic right of way; every bot yields
        to a human.  A following vehicle uses a continuous time-gap controller
        translated through its copied drivetrain.  The former absolute-speed
        cutoff alternated full throttle and coast braking whenever a dense
        spawn row crossed ``clearance == speed`` even if both tanks had the
        same velocity.
        """
        throttle = max(-1.0, min(1.0, _number(command.get('throttle'))))
        if throttle <= 0.01:
            return throttle, False
        position = _position(source)
        source_team = int(_number(source.get('team')))
        if source_team not in (1, 2):
            return throttle, False
        yaw = _number(source.get('yaw'))
        own_speed = abs(_number(source.get('speed')))
        own_length = max(0.5, _number(source.get('half_length'), 3.5))
        own_width = max(0.3, _number(source.get('half_width'), 1.7))
        if physics_params is None:
            physics_params = vehicle_physics.derive_params({})
        slope_pitch = _number(source.get('last_drive_pitch'))
        steering = abs(_number(command.get('turn'))) > 0.01
        stopping_clearance = None
        scan_clearance = None
        sine, cosine = math.sin(yaw), math.cos(yaw)
        target_yaw = _number(command.get('target_yaw'), yaw)
        target_sine = math.sin(target_yaw)
        target_cosine = math.cos(target_yaw)
        nearest = None
        for raw in neighbours or ():
            if not isinstance(raw, dict):
                continue
            if int(_number(raw.get('team'))) != source_team:
                continue
            other = raw.get('position') or raw.get('pos')
            if other is None:
                continue
            try:
                dx = float(other[0]) - position[0]
                dz = float(other[2]) - position[2]
                if abs(float(other[1]) - position[1]) > 5.0:
                    continue
            except (TypeError, ValueError, IndexError):
                continue
            forward = dx * sine + dz * cosine
            lateral = abs(dx * cosine - dz * sine)
            corridor_yaw = yaw
            if abs(_angle_delta(target_yaw, yaw)) > 0.20:
                target_forward = dx * target_sine + dz * target_cosine
                target_lateral = abs(
                    dx * target_cosine - dz * target_sine)
                if (target_forward > 0.0 and
                        target_lateral < lateral):
                    forward = target_forward
                    lateral = target_lateral
                    corridor_yaw = target_yaw
            if forward <= 0.0:
                continue
            other_length = max(
                0.5, _number(raw.get('half_length'), 3.5))
            other_width = max(
                0.3, _number(raw.get('half_width'), 1.7))
            # The sum of both OBB circumradii is a strict separating bound.
            # Outside it, no forward translation along this corridor can make
            # the hulls overlap, so the four-axis continuous SAT is needless.
            if BotRuntime._traffic_lateral_separated(
                    lateral, own_width, own_length,
                    other_width, other_length):
                continue
            other_velocity = raw.get('velocity') or raw.get('vel')
            try:
                other_vx = float(other_velocity[0])
                other_vz = float(other_velocity[2])
            except (TypeError, ValueError, IndexError):
                other_vx = 0.0
                other_vz = 0.0
            other_yaw = _number(raw.get('yaw'), corridor_yaw)
            # Velocity has no direction once a teammate is parked.  Its hull
            # yaw still tells us whether it occupies this lane longitudinally
            # or crosses it, and therefore whether deterministic right of way
            # can break a spawn-grid deadlock.  The physical near-field gate
            # below still makes both ids brake inside the copied stopping
            # distance.
            same_direction = abs(
                _angle_delta(other_yaw, corridor_yaw)) < 0.65
            clearance = BotRuntime._traffic_obb_clearance(
                dx, dz, corridor_yaw, yaw, own_width, own_length,
                other_yaw, other_width, other_length)
            if stopping_clearance is None:
                stopping_distance = (
                    stopping_distance_resolver(
                        source, command, physics_params)
                    if callable(stopping_distance_resolver) else
                    BotRuntime._traffic_stopping_distance(
                        own_speed, physics_params, slope_pitch, steering))
                stopping_clearance = (
                    TRAFFIC_STANDSTILL_CLEARANCE + stopping_distance)
                scan_clearance = max(
                    9.0, TRAFFIC_STANDSTILL_CLEARANCE +
                    own_speed * TRAFFIC_HEADWAY_SECONDS,
                    stopping_clearance)
            if clearance > scan_clearance:
                continue
            if (not same_direction and raw.get('id') is not None and
                    source.get('id') is not None):
                try:
                    other_id = int(raw.get('id'))
                    if (other_id < HUMAN_TARGET_ID_BASE and
                            other_id > int(source.get('id')) and
                            clearance > stopping_clearance):
                        continue
                except (TypeError, ValueError):
                    pass
            corridor_sine = math.sin(corridor_yaw)
            corridor_cosine = math.cos(corridor_yaw)
            other_forward = max(
                0.0, other_vx * corridor_sine +
                other_vz * corridor_cosine)
            candidate = (clearance, other_forward, same_direction)
            if nearest is None or candidate[0] < nearest[0]:
                nearest = candidate
        if nearest is None:
            return throttle, False
        clearance, leader_speed, same_direction = nearest
        if not same_direction:
            # A crossing or head-on merge is a discrete right-of-way event,
            # not longitudinal following.  Preserve the established yield
            # gate; the continuous controller below applies only to vehicles
            # travelling along the same corridor.
            safe_clearance = max(
                stopping_clearance,
                TRAFFIC_STANDSTILL_CLEARANCE +
                own_speed * TRAFFIC_HEADWAY_SECONDS)
            if clearance <= safe_clearance:
                return (0.0,
                        own_speed > TRAFFIC_DIRECTION_SPEED_EPSILON)
            if own_speed > leader_speed + 0.5:
                limited = min(throttle, max(0.0, min(
                    1.0, (clearance - safe_clearance) / 4.0)))
                return (limited, bool(
                    limited <= 0.01 and
                    own_speed > TRAFFIC_DIRECTION_SPEED_EPSILON))
            return throttle, False
        if clearance <= TRAFFIC_STANDSTILL_CLEARANCE:
            return (0.0,
                    own_speed > TRAFFIC_DIRECTION_SPEED_EPSILON)
        try:
            mass = max(1.0, float(physics_params['mass']))
            drive_accel = vehicle_physics.engine_force(
                physics_params, own_speed, 1.0, slope_pitch) / mass
            rolling_accel = vehicle_physics.rolling_resist_force(
                physics_params, 0, steering) / mass
            gravity_accel = vehicle_physics.GRAVITY * math.sin(slope_pitch)
        except (KeyError, TypeError, ValueError, OverflowError):
            return throttle, False
        if drive_accel <= 0.000001:
            return throttle, False
        desired_clearance = max(
            stopping_clearance,
            TRAFFIC_STANDSTILL_CLEARANCE +
            own_speed * TRAFFIC_HEADWAY_SECONDS)
        # Standard constant-time-headway feedback: close the spacing error in
        # one headway while also matching the leader's forward velocity.  The
        # result is an acceleration, not a hand-tuned throttle blend.
        desired_accel = (
            (clearance - desired_clearance) /
            (TRAFFIC_HEADWAY_SECONDS * TRAFFIC_HEADWAY_SECONDS) +
            (leader_speed - own_speed) / TRAFFIC_HEADWAY_SECONDS)
        required = ((desired_accel - gravity_accel + rolling_accel) /
                    drive_accel)
        limited = min(throttle, max(0.0, min(1.0, required)))
        # ``waiting`` suppresses route-stuck recovery only while a moving hull
        # is actually coasting to a traffic stop.  A partial drive command is
        # still progress, and a parked crossing must enter the existing
        # recovery path so two stopped neighbours cannot reset one another's
        # stuck clock forever.
        return (limited, bool(
            limited <= 0.01 and
            own_speed > TRAFFIC_DIRECTION_SPEED_EPSILON))

    def _player_vehicle_profile(self, raw):
        vehicle_name = raw.get('vehicle')
        compact = raw.get('vehicle_compact_descr') or ''
        player_id = raw.get('network_id', raw.get('id'))
        try:
            player_id = int(player_id)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('player effective parameters identity is invalid')
        cache_key = (player_id, vehicle_name or '', compact)
        cached = self._player_vehicle_profiles.get(cache_key)
        if cached is not None:
            return cached
        snapshot = _player_effective_params(raw)
        descriptor = {}
        tactical = {}
        try:
            descriptor = (self.player_descriptor_resolver(raw)
                          if callable(self.player_descriptor_resolver)
                          else self.descriptor_resolver(
                              vehicle_name or 'ussr:R11_MS-1'))
        except Exception:
            descriptor = {}
        if vehicle_name:
            try:
                tactical = ai_planner.build_vehicle_profile(descriptor)
            except Exception:
                tactical = {}
        profile = snapshot['spotting']
        camouflage = snapshot['camouflage']
        cached = {
            'descriptor': descriptor,
            'class_tag': str(tactical.get('class_tag') or 'unknown'),
            'armor': max(0.0, _number(tactical.get('armor'))),
            'spotting': ((camouflage['base_moving'],
                          camouflage['base_still']),
                         camouflage['shot_factor'], profile),
        }
        self._player_vehicle_profiles[cache_key] = cached
        return cached

    def _player_collision_profile(self, raw):
        player_id = raw.get('network_id', raw.get('id'))
        try:
            player_id = int(player_id)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('player collision identity is invalid')
        cache_key = (player_id, raw.get('vehicle') or '',
                     raw.get('vehicle_compact_descr') or '')
        cached = self._player_collision_profiles.get(cache_key)
        if cached is not None:
            return cached
        snapshot = _player_effective_params(raw)
        descriptor = self._player_vehicle_profile(raw)['descriptor']
        if descriptor is None or descriptor == {}:
            raise ValueError('player collision descriptor is unavailable')
        cached = {
            'mass': snapshot['physics']['mass'],
            'shape': _collision_shape(descriptor),
            'ram_profile': snapshot['ramming'],
        }
        self._player_collision_profiles[cache_key] = cached
        return cached

    @staticmethod
    def _native_ram_hit_supported(body, hit):
        """Contain one frame of native/copy pose skew inside the frozen OBB."""
        return tank_collision.body_contains_point(
            body, hit, slop=tank_collision.RAM_CONTACT_POINT_SLOP)

    def _ram_reports(self, state, events, human_ram_receipt=None):
        reports = []
        for event in events:
            other_id = int(event['other_id'])
            target_kind = ('human' if
                           other_id >= HUMAN_TARGET_ID_BASE else 'bot')
            if target_kind == 'human':
                other_id -= HUMAN_TARGET_ID_BASE
                if not isinstance(human_ram_receipt, dict):
                    # Current-frame human contact may separate the bodies,
                    # but only a player's immutable presented-pose receipt
                    # owns human HP. This prevents delayed replay double-hit.
                    continue
            self_velocity = event['velocity_self']
            other_velocity = event['velocity_other']
            self_shape = event['shape_self']
            other_shape = event['shape_other']
            contact_normal = event['contact_normal']
            self_vehicle = event['self_vehicle'].replace(
                '\r', ' ').replace('\n', ' ')
            other_vehicle = event['other_vehicle'].replace(
                '\r', ' ').replace('\n', ' ')
            sys.stdout.write(
                '[Offline LAN 0.9.22] RAM diagnostic '
                'self_id=%d self_vehicle=%s other_kind=%s other_id=%d '
                'other_vehicle=%s mass_self=%.3f mass_other=%.3f '
                'velocity_self_xz=(%.4f,%.4f) '
                'velocity_other_xz=(%.4f,%.4f) '
                'yaw_self=%.5f yaw_other=%.5f '
                'shape_self=(%.3f,%.3f,%.3f,%.3f) '
                'shape_other=(%.3f,%.3f,%.3f,%.3f) '
                'contact_normal_xz=(%.5f,%.5f) '
                'contact_penetration=%.5f '
                'normal_closing_speed=%.5f damage_to_self=%d '
                'damage_to_other=%d\n' % (
                    int(event['self_id']), self_vehicle, target_kind,
                    other_id, other_vehicle, event['mass_self'],
                    event['mass_other'], self_velocity[0],
                    self_velocity[1], other_velocity[0],
                    other_velocity[1], event['yaw_self'],
                    event['yaw_other'], self_shape[0], self_shape[1],
                    self_shape[2], self_shape[3], other_shape[0],
                    other_shape[1], other_shape[2], other_shape[3],
                    contact_normal[0], contact_normal[1],
                    event['contact_penetration'], event['closing_speed'],
                    event['damage_to_self'], event['damage_to_other']))
            self._ram_seq += 1
            report = {
                'type': 'bot_ram', 'bot_id': int(state['id']),
                'target_kind': target_kind, 'target_id': other_id,
                'ram_seq': self._ram_seq,
                'damage_to_bot': event['damage_to_self'],
                'damage_to_target': event['damage_to_other'],
            }
            if (target_kind == 'human' and
                    isinstance(human_ram_receipt, dict)):
                report['ram_contact_player_id'] = int(
                    human_ram_receipt['player_id'])
                report['ram_contact_seq'] = int(
                    human_ram_receipt['seq'])
            reports.append(report)
        return reports

    def ack_human_ram_receipt(self, player_id, seq):
        """Apply the server-owned terminal high-water from a snapshot."""
        try:
            player_id = int(player_id)
            seq = int(seq)
        except (TypeError, ValueError, OverflowError):
            return False
        if player_id <= 0 or seq <= 0:
            return False
        previous = int(self._human_ram_receipt_seq.get(player_id, 0))
        if seq <= previous:
            return False
        self._human_ram_receipt_seq[player_id] = seq
        for key in list(self._human_ram_report_cache):
            if key[0] == player_id and key[1] <= seq:
                self._human_ram_report_cache.pop(key, None)
        return True

    def _terminal_human_ram_report(self, bot_id, player_id, seq):
        self._ram_seq += 1
        return {
            'type': 'bot_ram', 'bot_id': int(bot_id),
            'target_kind': 'human', 'target_id': int(player_id),
            'ram_seq': self._ram_seq, 'damage_to_bot': 0,
            'damage_to_target': 0,
            'ram_contact_player_id': int(player_id),
            'ram_contact_seq': int(seq),
        }

    def _apply_tank_contact_response(self, state, result, step,
                                     advance_push=True,
                                     apply_correction=True):
        """Apply one resolver response through the canonical bot motion path."""
        delta_x, delta_z = result['delta_velocity']
        yaw = _number(state.get('yaw'))
        speed = _number(state.get('speed'))
        forward_impulse = (delta_x * math.sin(yaw) +
                           delta_z * math.cos(yaw))
        applied_forward = 0.0
        if forward_impulse * speed < 0.0:
            applied_forward = (-speed if
                               abs(forward_impulse) >= abs(speed)
                               else forward_impulse)
            state['speed'] = speed + applied_forward
        push_x = (_number(state.get('push_x')) + delta_x -
                  applied_forward * math.sin(yaw))
        push_z = (_number(state.get('push_z')) + delta_z -
                  applied_forward * math.cos(yaw))
        correction_x, correction_z = (result['correction'] if
                                      apply_correction else (0.0, 0.0))
        move_x = correction_x + (push_x * step if advance_push else 0.0)
        move_z = correction_z + (push_z * step if advance_push else 0.0)
        move_distance = math.sqrt(move_x * move_x + move_z * move_z)
        if move_distance > 0.0001:
            contact_yaw = math.atan2(move_x, move_z)
            contact_speed = move_distance / max(float(step), 1.0 / 120.0)
            if not self._clear(
                    _position(state), contact_yaw, contact_speed, None):
                # Tank separation is not permission to cross static world
                # geometry. Let the other hull keep its inverse-mass share.
                move_x = 0.0
                move_z = 0.0
                push_x = 0.0
                push_z = 0.0
        state['x'] += move_x
        state['z'] += move_z
        push_decay = (0.90 ** (max(0.0, float(step)) * 60.0)
                      if advance_push else 1.0)
        state['push_x'] = push_x * push_decay
        state['push_z'] = push_z * push_decay

    def _resolve_human_ram_receipts(self, players, now, step=None,
                                    processed_pairs=None):
        """Recompute client-observed contact against its canonical bot body."""
        reports = []
        receipt_players = {}
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                player_id = int(raw['id'])
                resolved_seq = int(raw.get(
                    'ram_contact_resolved_seq', 0) or 0)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if resolved_seq > 0:
                self.ack_human_ram_receipt(player_id, resolved_seq)
            contacts = raw.get('ram_contacts')
            if isinstance(contacts, list):
                for receipt in contacts:
                    if not isinstance(receipt, dict):
                        continue
                    entry = dict(raw)
                    entry['ram_contact'] = receipt
                    entry['_ram_contact_bot_state'] = receipt.get(
                        '_ram_contact_bot_state')
                    receipt_players.setdefault(player_id, []).append(entry)
                continue
            receipt_players.setdefault(player_id, []).append(raw)
        for player_id in sorted(receipt_players):
            entries = receipt_players[player_id]
            def receipt_order(value):
                try:
                    return int((value.get('ram_contact') or {}).get('seq'))
                except (AttributeError, TypeError, ValueError,
                        OverflowError):
                    return 2147483647
            entries.sort(key=receipt_order)
            for raw in entries:
                receipt = raw.get('ram_contact')
                historical = raw.get('_ram_contact_bot_state')
                if not isinstance(receipt, dict):
                    continue
                try:
                    seq = int(receipt['seq'])
                    bot_id = int(receipt['bot_id'])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if seq <= int(self._human_ram_receipt_seq.get(
                        player_id, 0)):
                    continue
                key = (player_id, seq)
                pair = (
                    min(bot_id, HUMAN_TARGET_ID_BASE + player_id),
                    max(bot_id, HUMAN_TARGET_ID_BASE + player_id))
                cached = self._human_ram_report_cache.get(key)
                if cached is not None:
                    if processed_pairs is not None:
                        processed_pairs.add(pair)
                    reports.extend(dict(report) for report in cached)
                    break
                # Missing history is temporary when replaceable snapshots
                # coalesce the exact revision. Do not skip this sequence and
                # let a later receipt overtake it.
                if not isinstance(historical, dict):
                    break
                current = self.states.get(bot_id)
                if current is None:
                    # A takeover snapshot can expose the receipt before the
                    # bot state has materialised. Keep it retryable.
                    break
                if (not current.get('alive', True) or
                        int(_number(historical.get('id'), -1)) != bot_id):
                    receipt_reports = [self._terminal_human_ram_report(
                        bot_id, player_id, seq)]
                else:
                    try:
                        player_x = float(receipt['x'])
                        player_y = float(receipt['y'])
                        player_z = float(receipt['z'])
                        player_yaw = float(receipt['yaw'])
                        player_vx = float(receipt['vx'])
                        player_vy = float(receipt['vy'])
                        player_vz = float(receipt['vz'])
                        bot_vx = float(receipt['bot_vx'])
                        bot_vy = float(receipt['bot_vy'])
                        bot_vz = float(receipt['bot_vz'])
                        contact_time = float(
                            receipt['presentation_time_us']) / 1000000.0
                    except (KeyError, TypeError, ValueError, OverflowError):
                        receipt_reports = [self._terminal_human_ram_report(
                            bot_id, player_id, seq)]
                    else:
                        profile = self._player_collision_profile(raw)
                        bot_yaw = _number(historical.get('yaw'))
                        bot_speed = (_number(historical.get('speed')) if
                                     historical.get('alive', True) else 0.0)
                        bot = {
                            'id': bot_id,
                            'alive': bool(historical.get('alive', True)),
                            'team': int(_number(historical.get('team'))),
                            'vehicle': str(historical.get('vehicle') or ''),
                            'x': _number(historical.get('x')),
                            'y': _number(historical.get('y')),
                            'z': _number(historical.get('z')),
                            'yaw': bot_yaw,
                            'pitch': _number(historical.get('pitch')),
                            'roll': _number(historical.get('roll')),
                            'mass': _number(
                                historical.get('mass'), 25000.0),
                            'shape': historical.get('collision_shape'),
                            'ram_profile': historical.get('ram_profile'),
                            'vx': bot_vx, 'vy': bot_vy, 'vz': bot_vz,
                        }
                        player = {
                            'id': HUMAN_TARGET_ID_BASE + player_id,
                            'alive': bool(raw.get('alive', True)),
                            'team': int(_number(raw.get('team'))),
                            'vehicle': str(raw.get('vehicle') or ''),
                            'x': player_x, 'y': player_y, 'z': player_z,
                            'yaw': player_yaw,
                            'pitch': _number(receipt.get('pitch')),
                            'roll': _number(receipt.get('roll')),
                            'mass': profile['mass'],
                            'shape': profile['shape'],
                            'ram_profile': profile['ram_profile'],
                            'vx': player_vx, 'vy': player_vy,
                            'vz': player_vz,
                        }
                        try:
                            hit = (
                                float(receipt['contact_x']),
                                float(receipt['contact_y']),
                                float(receipt['contact_z']))
                            player_armor = float(
                                receipt['contact_armor_player'])
                            bot_armor = float(receipt['contact_armor_bot'])
                            player_normal_x = float(
                                receipt['contact_normal_x'])
                            player_normal_z = float(
                                receipt['contact_normal_z'])
                        except (KeyError, TypeError, ValueError,
                                OverflowError):
                            native_supported = False
                        else:
                            player_spall = _number(
                                receipt.get('contact_spall_player'))
                            player_bonus = _number(
                                receipt.get('contact_bonus_player'), -1.0)
                            normal_length = math.hypot(
                                player_normal_x, player_normal_z)
                            normal_alignment = (
                                player_normal_x *
                                (player_x - bot['x']) +
                                player_normal_z *
                                (player_z - bot['z']))
                            native_supported = bool(
                                player_armor > 0.0 and bot_armor > 0.0 and
                                not math.isnan(player_armor) and
                                not math.isinf(player_armor) and
                                not math.isnan(bot_armor) and
                                not math.isinf(bot_armor) and
                                1.0 <= player_spall <= 1.5 and
                                0.0 <= player_bonus <= 0.15 and
                                not math.isnan(normal_length) and
                                not math.isinf(normal_length) and
                                0.999 <= normal_length <= 1.001 and
                                normal_alignment > 1.0e-6 and
                                not receipt.get('contact_screened_player') and
                                not receipt.get('contact_screened_bot') and
                                self._native_ram_hit_supported(player, hit) and
                                self._native_ram_hit_supported(bot, hit))
                        if not native_supported:
                            sys.stdout.write(
                                '[Offline LAN 0.9.22] RAM native receipt '
                                'rejected player_id=%d bot_id=%d seq=%d\n' % (
                                    player_id, bot_id, seq))
                            receipt_reports = [
                                self._terminal_human_ram_report(
                                    bot_id, player_id, seq)]
                        else:
                            # The immutable receipt already proves one contact
                            # episode and its real armour layers. Its proof also
                            # carries the frozen player-oriented first-impact
                            # normal. Reverse it for Bot -> player damage instead
                            # of re-solving a later deep overlap.
                            bot_profile = bot.get('ram_profile') or {}
                            bot_spall = _number(bot_profile.get(
                                'spall_coefficient'), -1.0)
                            bot_bonus = _number(bot_profile.get(
                                'ramming_bonus'), -1.0)
                            if (not 1.0 <= bot_spall <= 1.5 or
                                    not 0.0 <= bot_bonus <= 0.15):
                                receipt_reports = [
                                    self._terminal_human_ram_report(
                                        bot_id, player_id, seq)]
                            else:
                                normal_x = -player_normal_x / normal_length
                                normal_z = -player_normal_z / normal_length
                                penetration = 0.0
                                closing_speed = (
                                    tank_collision.planar_closing_speed(
                                        (bot_vx, bot_vz),
                                        (player_vx, player_vz),
                                        (normal_x, normal_z)))
                                if (bot['team'] in (1, 2) and
                                        bot['team'] == player['team']):
                                    damage_player = damage_bot = 0
                                else:
                                    damage_player, damage_bot = (
                                        tank_collision.ram_damage(
                                            closing_speed,
                                            bot['mass'], player['mass'],
                                            bot_armor, player_armor,
                                            bot_spall, player_spall,
                                            bot_bonus, player_bonus,
                                            bool(bot_vx or bot_vy or bot_vz),
                                            bool(player_vx or player_vy or
                                                 player_vz)))
                                response = tank_collision.resolve_tank(
                                    bot, (player,), now=None)
                                if step is not None:
                                    self._apply_tank_contact_response(
                                        current, response, step,
                                        advance_push=False,
                                        apply_correction=False)
                                event = {
                                    'self_id': bot['id'],
                                    'other_id': player['id'],
                                    'self_vehicle': bot['vehicle'],
                                    'other_vehicle': player['vehicle'],
                                    'mass_self': bot['mass'],
                                    'mass_other': player['mass'],
                                    'velocity_self': (bot_vx, bot_vz),
                                    'velocity_other': (player_vx, player_vz),
                                    'yaw_self': bot['yaw'],
                                    'yaw_other': player['yaw'],
                                    'shape_self': bot['shape'],
                                    'shape_other': player['shape'],
                                    'contact_normal': (
                                        normal_x, normal_z),
                                    'contact_penetration': penetration,
                                    'closing_speed': closing_speed,
                                    'damage_to_self': damage_bot,
                                    'damage_to_other': damage_player,
                                }
                                receipt_reports = self._ram_reports(
                                    current, ((event,) if
                                              (damage_bot or damage_player)
                                              else ()), {
                                        'player_id': player_id, 'seq': seq,
                                    }) or [self._terminal_human_ram_report(
                                        bot_id, player_id, seq)]
                if processed_pairs is not None:
                    processed_pairs.add(pair)
                frozen = [dict(report) for report in receipt_reports]
                self._human_ram_report_cache[key] = frozen
                reports.extend(dict(report) for report in frozen)
                # One unresolved transaction per player preserves ledger
                # order even when transport retries or snapshots coalesce.
                break
        return reports

    def _resolve_tank_contacts(self, players, now, step):
        """Apply current 0.8.2 chassis OBB response and report rams."""
        if self.native_motion:
            return []
        tanks = []
        for state in self._ordered_states():
            alive = bool(state.get('alive', True))
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed')) if alive else 0.0
            tanks.append({
                'id': int(state['id']), 'kind': 'bot',
                'network_id': int(state['id']), 'alive': alive,
                'team': int(_number(state.get('team'))),
                'vehicle': str(state.get('vehicle') or ''),
                'x': _number(state.get('x')), 'y': _number(state.get('y')),
                'z': _number(state.get('z')), 'yaw': yaw,
                'mass': _number(state.get('mass'), 25000.0),
                'shape': state.get('collision_shape'),
                'ram_profile': state.get('ram_profile'),
                'vx': (math.sin(yaw) * speed +
                       _number(state.get('push_x'))),
                'vy': _number(state.get(
                    'ram_vy', state.get('vertical_speed'))),
                'vz': (math.cos(yaw) * speed +
                       _number(state.get('push_z'))),
                'pitch': _number(state.get('pitch')),
                'roll': _number(state.get('roll')),
            })
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                player_id = HUMAN_TARGET_ID_BASE + int(raw['id'])
            except (TypeError, ValueError):
                continue
            profile = self._player_collision_profile(raw)
            alive = bool(raw.get('alive', True))
            yaw = _number(raw.get('yaw'))
            speed = _number(raw.get('speed')) if alive else 0.0
            tanks.append({
                'id': player_id, 'kind': 'player',
                'network_id': int(raw['id']), 'alive': alive,
                'team': int(_number(raw.get('team'))),
                'vehicle': str(raw.get('vehicle') or ''),
                # The human client owns its own contact impulse; taking it
                # here too would make an enemy pair shake.  A friendly bot is
                # the exception: it owns the velocity response so the local
                # player does not inherit the teammate's lateral momentum.
                'impulse': False,
                'x': _number(raw.get('x')), 'y': _number(raw.get('y')),
                'z': _number(raw.get('z')), 'yaw': yaw,
                'mass': profile['mass'], 'shape': profile['shape'],
                'ram_profile': profile['ram_profile'],
                'vx': math.sin(yaw) * speed,
                'vz': math.cos(yaw) * speed,
            })

        by_id = dict((tank['id'], tank) for tank in tanks)
        collision_bodies = {}
        maximum_radius = 4.0
        for tank in tanks:
            shape = tank.get('shape') or tank_collision.DEFAULT_SHAPE
            radius = math.sqrt(
                _number(shape[0]) * _number(shape[0]) +
                _number(shape[1]) * _number(shape[1]))
            maximum_radius = max(maximum_radius, radius)
            collision_bodies[tank['id']] = {
                'position': (tank['x'], tank['y'], tank['z'])}
        collision_index = tank_collision.build_spatial_index(
            collision_bodies, maximum_radius * 2.0 + 4.0)
        receipt_pairs = set()
        reports = self._resolve_human_ram_receipts(
            players, now, step=step, processed_pairs=receipt_pairs)
        previous_ram_contacts = self._ram_contacts
        current_ram_contacts = set()
        frame_ram_armors = {}

        def contact_armor_probe(first, second, contact):
            first_id = int(first['id'])
            second_id = int(second['id'])
            pair = (min(first_id, second_id), max(first_id, second_id))
            if pair not in frame_ram_armors:
                armors = self.ram_contact_probe(first, second, contact)
                if armors is None:
                    frame_ram_armors[pair] = None
                elif (isinstance(armors, (list, tuple)) and
                      len(armors) == 2):
                    frame_ram_armors[pair] = (
                        tuple(armors) if first_id <= second_id else
                        tuple(reversed(armors)))
                else:
                    return armors
            canonical = frame_ram_armors[pair]
            if canonical is None or first_id <= second_id:
                return canonical
            return tuple(reversed(canonical))

        for state in self._ordered_states():
            if not state.get('alive', True):
                continue
            own = by_id.get(int(state['id']))
            if own is None:
                continue
            candidate_ids = tank_collision.nearby_ids(
                collision_index, own['x'], own['z'])
            others = []
            for tank_id in candidate_ids:
                if tank_id == own['id'] or tank_id not in by_id:
                    continue
                pair = (min(own['id'], tank_id), max(own['id'], tank_id))
                if pair in receipt_pairs:
                    continue
                other = by_id[tank_id]
                others.append(other)
            resolve_kwargs = {
                'now': now,
                'ram_cooldowns': self._ram_cooldowns,
                'active_ram_contacts': frozenset(
                    set(previous_ram_contacts) | current_ram_contacts),
            }
            if self.ram_contact_probe is not None:
                resolve_kwargs['contact_armor_probe'] = \
                    contact_armor_probe
            result = tank_collision.resolve_tank(
                own, others, **resolve_kwargs)
            self._ram_cooldowns = result['cooldowns']
            current_ram_contacts.update(result['contacts'])
            self._apply_tank_contact_response(state, result, step)
            reports.extend(self._ram_reports(
                state, result['ram_events']))
        self._ram_contacts = frozenset(current_ram_contacts)
        return reports

    @staticmethod
    def _target_velocity(target):
        if target is None:
            return (0.0, 0.0, 0.0)
        raw = target.get('velocity')
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return (_number(raw[0]), _number(raw[1]), _number(raw[2]))
        yaw = _number(target.get('yaw'))
        speed = _number(target.get('speed'))
        return (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed)

    @staticmethod
    def _exact_shot_direction(state, descriptor):
        """Return the current barrel ray in the stabilised world basis."""
        try:
            yaw = _number(state.get('yaw'))
            pitch = _number(state.get('pitch'))
            roll = _number(state.get('roll'))
            if abs(pitch) <= 1.0e-12 and abs(roll) <= 1.0e-12:
                flat_yaw = (
                    _wrapped(yaw + _number(state.get('turret_yaw')))
                    if 'turret_yaw' in state else
                    _number(state.get('aim_yaw'), yaw))
                return ai_driver.barrel_direction(
                    flat_yaw, _number(state.get('gun_pitch')))
            turret_yaw = (
                _number(state.get('turret_yaw'))
                if 'turret_yaw' in state else
                _angle_delta(_number(state.get('aim_yaw'), yaw), yaw))
            local_direction = ai_driver.barrel_direction(
                turret_yaw, _number(state.get('gun_pitch')))
            return shot_geometry.transform_vehicle_vector(
                local_direction, yaw, pitch, roll)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _exact_shot_origin(self, state, descriptor, shell_index=0):
        """Read the worker's frozen native HP_gunFire transform."""
        direction = self._exact_shot_direction(state, descriptor)
        if direction is None:
            return None
        horizontal = math.sqrt(
            direction[0] * direction[0] + direction[2] * direction[2])
        shot_yaw = math.atan2(direction[0], direction[2])
        shot_pitch = -math.atan2(
            direction[1], max(1.0e-12, horizontal))
        try:
            raw = self.direct_launch_origin_probe(
                _copy_runtime_state(state), descriptor, int(shell_index),
                int(state.get('fire_seq', 0)) + 1,
                shot_yaw, shot_pitch, 0.0)
            origin = tuple(float(value) for value in raw)
        except Exception:
            return None
        if (len(origin) != 3 or
                any(math.isnan(value) or math.isinf(value)
                    for value in origin)):
            return None
        return origin

    @staticmethod
    def _world_barrel_angles(state, descriptor):
        """Return current world yaw and BigWorld negative-is-up pitch."""
        direction = BotRuntime._exact_shot_direction(state, descriptor)
        if direction is None:
            return None
        horizontal = math.sqrt(
            direction[0] * direction[0] + direction[2] * direction[2])
        return (math.atan2(direction[0], direction[2]),
                -math.atan2(direction[1], max(1.0e-12, horizontal)))

    @staticmethod
    def _dispersal_base_direction(state, descriptor):
        """Override the legacy flat basis only when hull attitude requires it."""
        if (abs(_number(state.get('pitch'))) <= 1.0e-12 and
                abs(_number(state.get('roll'))) <= 1.0e-12):
            return None
        return BotRuntime._exact_shot_direction(state, descriptor)

    @staticmethod
    def _local_gun_angles_for_world(state, world_yaw, world_pitch):
        try:
            return shot_geometry.world_direction_to_local_gun_angles(
                ai_driver.barrel_direction(world_yaw, world_pitch),
                _number(state.get('yaw')),
                _number(state.get('pitch')),
                _number(state.get('roll')))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _terrain_pitch(state):
        correction = _number(state.get('suspension_pitch'))
        return _number(
            state.get('terrain_pitch'),
            _number(state.get('pitch')) - correction)

    @staticmethod
    def _static_gun_value(descriptor, name):
        gun = _value(descriptor, 'gun', {}) or {}
        value = _value(gun, name)
        return None if value is None else _number(value)

    @classmethod
    def _effective_gun_pitch_limits(cls, state, descriptor, turret_yaw):
        static_pitch = cls._static_gun_value(descriptor, 'staticPitch')
        unused_devices, destroyed, unused_crew, unused_yellow = \
            _critical_parts(state)
        if hull_aiming.static_pitch_locked(
                static_pitch,
                engine_destroyed='engineHealth' in destroyed,
                overturned=bool(state.get('_overturned', False)),
                siege_state=int(state.get(
                    'siege_state', siege_mechanics.DISABLED))):
            return static_pitch, static_pitch
        return _gun_pitch_limits(descriptor, turret_yaw)

    def _effective_gun_yaw_limits(self, state, descriptor, moving=None):
        normal = self._gun_yaw_limits.get(state['id'])
        if normal is None:
            normal = ai_driver.gun_yaw_limits(descriptor)
            self._gun_yaw_limits[state['id']] = normal
        static_yaw = self._static_gun_value(
            descriptor, 'staticTurretYaw')
        unused_devices, destroyed, unused_crew, unused_yellow = \
            _critical_parts(state)
        if moving is None:
            moving = int(_number(state.get('movement_dir'))) != 0
        if hull_aiming.static_yaw_locked(
                static_yaw,
                engine_destroyed='engineHealth' in destroyed,
                track_destroyed=bool(destroyed.intersection((
                    'leftTrackHealth', 'rightTrackHealth'))),
                overturned=bool(state.get('_overturned', False)),
                moving=bool(moving),
                siege_state=int(state.get(
                    'siege_state', siege_mechanics.DISABLED))):
            return static_yaw, static_yaw, True
        return normal

    @classmethod
    def _pitch_status_at_correction(
            cls, state, descriptor, direction, terrain_pitch, correction):
        try:
            local = shot_geometry.world_direction_to_local_gun_angles(
                direction, _number(state.get('yaw')),
                terrain_pitch + correction,
                _number(state.get('roll')))
        except (TypeError, ValueError, OverflowError):
            return None
        limits = cls._effective_gun_pitch_limits(
            state, descriptor, local[0])
        if limits is None:
            return None
        return (hull_aiming.classify_pitch(
                    local[1], limits[0], limits[1], 1.0e-8),
                local, limits)

    @classmethod
    def _hydraulic_target_correction(
            cls, state, descriptor, world_yaw, world_pitch, config):
        direction = ai_driver.barrel_direction(world_yaw, world_pitch)
        terrain_pitch = cls._terrain_pitch(state)
        minimum = config['minimum']
        maximum = config['maximum']
        neutral = max(minimum, min(maximum, 0.0))
        at_neutral = cls._pitch_status_at_correction(
            state, descriptor, direction, terrain_pitch, neutral)
        if at_neutral is None:
            return neutral, False
        status = at_neutral[0]
        if status == 0:
            return neutral, True
        edge = minimum if status < 0 else maximum
        at_edge = cls._pitch_status_at_correction(
            state, descriptor, direction, terrain_pitch, edge)
        if at_edge is None:
            return edge, False
        edge_status = at_edge[0]
        if ((status < 0 and edge_status < 0) or
                (status > 0 and edge_status > 0)):
            return edge, False
        blocked = neutral
        feasible = edge
        for unused in range(48):
            candidate = (blocked + feasible) * 0.5
            result = cls._pitch_status_at_correction(
                state, descriptor, direction, terrain_pitch, candidate)
            if result is None:
                return edge, False
            if result[0] == status:
                blocked = candidate
            else:
                feasible = candidate
        return feasible, True

    @classmethod
    def _active_hydraulic_config(cls, state, descriptor):
        try:
            config = hull_aiming.pitch_params(descriptor)
        except ValueError:
            return None
        if (config is None or not config['isAvailable'] or
                not config['isEnabled'] or
                int(state.get('siege_state', siege_mechanics.DISABLED)) !=
                siege_mechanics.ENABLED):
            return None
        return config

    @classmethod
    def _update_hydraulic_suspension(
            cls, state, descriptor, world_yaw, world_pitch, elapsed):
        try:
            params = hull_aiming.pitch_params(descriptor)
        except ValueError:
            params = None
        active = cls._active_hydraulic_config(state, descriptor)
        target = 0.0
        if active is not None:
            target, unused_reachable = cls._hydraulic_target_correction(
                state, descriptor, world_yaw, world_pitch, active)
        current = _number(state.get('suspension_pitch'))
        if params is None:
            correction = 0.0
        else:
            current = max(
                params['minimum'], min(params['maximum'], current))
            correction = hull_aiming.slew(
                current, target, params['speed'], elapsed)
        terrain_pitch = cls._terrain_pitch(state)
        state['terrain_pitch'] = terrain_pitch
        state['suspension_pitch'] = correction
        state['pitch'] = terrain_pitch + correction
        return correction

    @classmethod
    def _world_solution_reachable(cls, state, descriptor,
                                  world_yaw, world_pitch):
        config = cls._active_hydraulic_config(state, descriptor)
        if config is not None:
            unused_target, reachable = cls._hydraulic_target_correction(
                state, descriptor, world_yaw, world_pitch, config)
            return reachable
        local = cls._local_gun_angles_for_world(
            state, world_yaw, world_pitch)
        if local is None:
            return False
        limits = cls._effective_gun_pitch_limits(
            state, descriptor, local[0])
        if limits is None:
            return False
        return bool(
            limits[0] - 0.0001 <= local[1] <= limits[1] + 0.0001)

    def _local_ballistic_solution(self, state, target, descriptor,
                                  shell_index):
        """Solve the ordinary low arc and moving-target lead without BSP."""
        physical = _shot_ballistics(descriptor, shell_index)
        if target is None or physical is None:
            return None
        speed, gravity, maximum = physical
        start = self._exact_shot_origin(state, descriptor, shell_index)
        if start is None:
            return None
        target_position = _point(
            target.get('position'), _position(target))
        target_position = (
            target_position[0], target_position[1] + 1.0,
            target_position[2])
        solution = ballistics.ballistic_intercept(
            start, target_position, self._target_velocity(target),
            speed, gravity, -math.pi * 0.5, math.pi * 0.5)
        if solution is None:
            return None
        aim_position, pitch, flight_time = solution
        if (flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                speed * flight_time > maximum + 1e-6):
            return None
        yaw = math.atan2(
            aim_position[0] - start[0], aim_position[2] - start[2])
        if not self._world_solution_reachable(
                state, descriptor, yaw, pitch):
            return None
        return {
            'aim_position': aim_position, 'yaw': yaw, 'pitch': pitch,
            'flight_time': flight_time, 'arc': 'low',
            # Gun aiming immediately follows this solve in the same authority
            # tick, before hydraulic, turret or barrel state advances. Reuse
            # the exact frozen muzzle instead of crossing the native
            # HP_gunFire boundary a second time for the identical pose.
            '_origin': start,
        }

    @staticmethod
    def _artillery_target_identity(target):
        if not isinstance(target, dict):
            return None
        target_id = target.get('network_id', target.get('id'))
        if target_id is None:
            return None
        try:
            return str(target.get('kind') or ''), int(target_id)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _artillery_source_pose(state):
        return (
            _number(state.get('x')), _number(state.get('y')),
            _number(state.get('z')), _number(state.get('yaw')),
            _number(state.get('pitch')), _number(state.get('roll')),
            _number(state.get('turret_yaw')),
            _number(state.get('gun_pitch')),
        )

    def _cancel_artillery_intent(self, bot_id, preserve_reproof=False):
        try:
            bot_id = int(bot_id)
        except (TypeError, ValueError, OverflowError):
            return False
        intent = self._artillery_intents.pop(bot_id, None)
        reproof = self._artillery_reproofs.get(bot_id)
        if not preserve_reproof:
            reproof = self._artillery_reproofs.pop(bot_id, reproof)
        cancel = self.artillery_launch_cancel
        if intent is not None and callable(cancel):
            try:
                cancel(dict(intent['source']))
            except Exception:
                # Local intent deletion is the safety boundary. A failed
                # native-queue cleanup may waste bounded work, but must not
                # resurrect or consume the cancelled next-fire sequence.
                pass
        return intent is not None or reproof is not None

    def _clear_artillery_intents(self):
        bot_ids = set(self._artillery_intents)
        bot_ids.update(self._artillery_reproofs)
        for bot_id in list(bot_ids):
            self._cancel_artillery_intent(bot_id)

    def _active_artillery_reproof(
            self, state, target, descriptor, shell_index, now):
        try:
            bot_id = int(state.get('id', 0))
        except (TypeError, ValueError, OverflowError):
            return None
        reproof = self._artillery_reproofs.get(bot_id)
        if reproof is None:
            return None
        physical = _shot_ballistics(descriptor, shell_index)
        target_dead = (
            not isinstance(target, dict) or
            not bool(target.get('alive', True)) or
            ('health' in target and _number(target.get('health')) <= 0.0))
        pose = self._artillery_source_pose(state)
        baseline = reproof['source_pose']
        moved = math.sqrt(sum(
            (pose[index] - baseline[index]) ** 2 for index in range(3)))
        orientation_changed = any(
            abs(_angle_delta(pose[index], baseline[index])) > 0.001
            for index in range(3, min(len(pose), len(baseline))))
        invalid = (
            target_dead or
            self._artillery_target_identity(target) !=
            reproof['target_identity'] or
            int(shell_index) != reproof['shell_index'] or
            int(state.get('fire_seq', 0)) + 1 != reproof['fire_seq'] or
            physical != reproof['physical'] or
            moved > 0.05 or orientation_changed)
        if invalid:
            self._cancel_artillery_intent(bot_id)
            return None
        if _number(now) > reproof['deadline'] + 1e-9:
            self._cancel_artillery_intent(bot_id)
            return None
        return reproof

    def _active_artillery_intent(
            self, state, target, descriptor, shell_index, now):
        try:
            bot_id = int(state.get('id', 0))
        except (TypeError, ValueError, OverflowError):
            return None
        intent = self._artillery_intents.get(bot_id)
        if intent is None:
            return None
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            return None
        return intent

    def _create_artillery_intent(
            self, state, target, descriptor, shell_index, gun_state,
            ballistic_solution, now):
        physical = _shot_ballistics(descriptor, shell_index)
        target_identity = self._artillery_target_identity(target)
        target_dead = (
            not bool(target.get('alive', True)) or
            ('health' in target and _number(target.get('health')) <= 0.0))
        if (physical is None or target_identity is None or
                target_dead or
                abs(_number(state.get('speed'))) > 0.05 or
                not self._spg_exact_aligned(
                    state, descriptor, ballistic_solution)):
            return None
        bot_id = int(state['id'])
        fire_seq = int(state.get('fire_seq', 0)) + 1
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            reproof = {
                'source': {'id': bot_id},
                'source_pose': self._artillery_source_pose(state),
                'target_identity': target_identity,
                'shell_index': int(shell_index),
                'fire_seq': fire_seq,
                'physical': physical,
                # Reproofs predict only target motion accumulated while the
                # exact native world path is queued. Random dispersion stays.
                'proof_latency': 0.0,
                'attempts': 0,
                'created': _number(now),
                'deadline': _number(now) + ARTILLERY_INTENT_SECONDS,
                'absolute_deadline': (
                    _number(now) + ARTILLERY_TOTAL_PROOF_SECONDS),
            }
            self._artillery_reproofs[bot_id] = reproof
        elif reproof.get('attempts', 0):
            reproof['deadline'] = min(
                _number(reproof.get(
                    'absolute_deadline', reproof['deadline'])),
                _number(now) + ARTILLERY_REPROOF_SECONDS)
        base_direction = self._dispersal_base_direction(state, descriptor)
        if (base_direction is None and
                (abs(_number(state.get('pitch'))) > 1.0e-12 or
                 abs(_number(state.get('roll'))) > 1.0e-12)):
            return None
        shot_yaw, shot_pitch = _dispersed_barrel_angles(
            state['id'], self.round_id, fire_seq,
            state['aim_yaw'], state['gun_pitch'],
            _effective_shot_dispersion(gun_state, state, descriptor),
            base_direction=base_direction)
        solution = dict(ballistic_solution)
        solution['aim_position'] = _point(solution['aim_position'])
        solution['yaw'] = float(solution['yaw'])
        solution['pitch'] = float(solution['pitch'])
        solution['flight_time'] = float(solution['flight_time'])
        reproof['hold_solution'] = dict(solution)
        intent = {
            'source': {'id': bot_id},
            'source_pose': reproof['source_pose'],
            'target_identity': target_identity,
            'shell_index': int(shell_index),
            'fire_seq': fire_seq,
            'physical': physical,
            'solution': solution,
            'shot_yaw': shot_yaw,
            'shot_pitch': shot_pitch,
            'created': _number(now),
            'deadline': reproof['deadline'],
        }
        self._artillery_intents[bot_id] = intent
        return intent

    def _artillery_reproof_solution(
            self, state, target, descriptor, shell_index, reproof):
        """Re-lead a proved SPG arc without repeating strategic world rays.

        The first strategic proof selects a clear low/high family. Target
        motion while its exact path is queued does not invalidate that family:
        re-solve it at the latest contact plus the observed queue latency, then
        submit the new immutable parabola to the full exact world probe. Any
        changed target motion still has to pass the undispersed aim-staleness
        gate, so this prediction cannot select or compensate a random endpoint.
        """
        physical = _shot_ballistics(descriptor, shell_index)
        if physical is None or not isinstance(target, dict):
            return None
        speed, gravity, maximum = physical
        start = self._exact_shot_origin(state, descriptor, shell_index)
        if start is None:
            return None
        target_position = _point(
            target.get('position'), _position(target))
        target_position = (
            target_position[0], target_position[1] + 1.0,
            target_position[2])
        target_velocity = self._target_velocity(target)
        proof_latency = max(0.0, _number(reproof.get('proof_latency')))
        predicted = tuple(
            target_position[index] +
            target_velocity[index] * proof_latency
            for index in range(3))
        arc = str(reproof.get('arc') or '')
        if arc not in ('low', 'high'):
            return None
        solution = ballistics.ballistic_intercept(
            start, predicted, target_velocity, speed, gravity,
            -math.pi * 0.5, math.pi * 0.5, arc == 'high')
        if solution is None:
            return None
        aim_position, pitch, flight_time = solution
        if (flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                speed * flight_time > maximum + 1e-6):
            return None
        yaw = math.atan2(
            aim_position[0] - start[0], aim_position[2] - start[2])
        if not self._world_solution_reachable(
                state, descriptor, yaw, pitch):
            return None
        return {
            'aim_position': aim_position,
            'yaw': yaw,
            'pitch': pitch, 'flight_time': flight_time, 'arc': arc,
        }

    def _ballistic_solution(self, state, target, descriptor, shell_index,
                            now):
        profile = state.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        if str(profile.get('class_tag') or '') == 'SPG':
            intent = self._active_artillery_intent(
                state, target, descriptor, shell_index, now)
            if intent is not None:
                return dict(intent['solution'])
            reproof = self._active_artillery_reproof(
                state, target, descriptor, shell_index, now)
            if reproof is not None and reproof.get('attempts', 0):
                return self._artillery_reproof_solution(
                    state, target, descriptor, shell_index, reproof)
            if not callable(self.ballistic_solution_probe):
                return None
            if self._probe_clock is None:
                value = self.ballistic_solution_probe(
                    _copy_runtime_state(state), (dict(target)
                                  if target is not None else None),
                    descriptor, int(shell_index), _number(now))
            else:
                probe_started = self._probe_started()
                try:
                    value = self.ballistic_solution_probe(
                        _copy_runtime_state(state), (dict(target)
                                      if target is not None else None),
                        descriptor, int(shell_index), _number(now))
                finally:
                    self._probe_finished(1, probe_started)
            if not isinstance(value, dict):
                return None
            try:
                aim = _point(value['aim_position'])
                yaw = float(value['yaw'])
                pitch = float(value['pitch'])
                flight_time = float(value['flight_time'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            if (flight_time <= 0.0 or
                    flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                    not self._world_solution_reachable(
                        state, descriptor, yaw, pitch)):
                return None
            result = dict(value)
            result.update({
                'aim_position': aim, 'yaw': yaw, 'pitch': pitch,
                'flight_time': flight_time,
            })
            return result
        return self._local_ballistic_solution(
            state, target, descriptor, shell_index)

    @staticmethod
    def _ballistic_solution_signature(
            state, target, descriptor, shell_index):
        target = target if isinstance(target, dict) else {}
        return (
            str(target.get('kind') or ''),
            target.get('network_id', target.get('id')),
            bool(target.get('alive', True)),
            int(shell_index), id(descriptor),
            int(state.get('siege_state', siege_mechanics.DISABLED)),
            int(state.get('fire_seq', 0)),
        )

    def _cadenced_ballistic_solution(
            self, state, target, descriptor, shell_index, now, force=False):
        """Refresh target geometry at 10 Hz while presentation stays 30 Hz."""
        bot_id = int(state['id'])
        signature = self._ballistic_solution_signature(
            state, target, descriptor, shell_index)
        cached = self._ballistic_solution_cache.get(bot_id)
        fresh = bool(
            force or cached is None or cached[0] != signature or
            _number(now) + 1.0e-9 >= cached[1])
        if fresh:
            solution = self._ballistic_solution(
                state, target, descriptor, shell_index, now)
            self._ballistic_solution_cache[bot_id] = (
                signature,
                _cache_deadline(
                    now, bot_id, LOCAL_ACTION_SECONDS, 13,
                    cached is None),
                solution)
            return solution, True
        return cached[2], False

    def _update_gun_aim(self, state, command, target, step):
        """Slew the rendered turret and barrel through the 0.8.2 limits."""
        descriptor = self._descriptors.get(state['id'], {})
        ballistic_solution = command.get('_ballistic_solution')
        if target is None and not isinstance(ballistic_solution, dict):
            # Strategic route points lie on the terrain. They steer the hull,
            # but are not gun targets: aiming a tall tank at a nearby ground
            # waypoint produces the reported 20-degree nose-down barrel.
            # Preserve the last safe world bearing and return to a neutral
            # horizontal rest without paying for an unused shot-origin solve.
            desired_yaw = _number(
                state.get('aim_yaw'), state.get('yaw'))
            world_pitch = 0.0
            horizontal = 0.0
        else:
            fallback = (target.get('position') if target is not None
                        else _position(state))
            if isinstance(ballistic_solution, dict):
                aim_position = _point(
                    ballistic_solution.get('aim_position'), fallback)
            else:
                aim_position = _point(command.get('aim_position'), fallback)
            origin = (ballistic_solution.get('_origin')
                      if isinstance(ballistic_solution, dict) else None)
            if not (isinstance(origin, (list, tuple)) and len(origin) == 3):
                origin = self._exact_shot_origin(
                    state, descriptor, state.get('shell_index', 0))
            if origin is None:
                state['gun_aligned'] = False
                return _number(state.get('aim_yaw')), 0.0
            dx = aim_position[0] - origin[0]
            dz = aim_position[2] - origin[2]
            horizontal = math.sqrt(dx * dx + dz * dz)
            desired_yaw = (_number(ballistic_solution.get('yaw'))
                           if isinstance(ballistic_solution, dict) else
                           (math.atan2(dx, dz) if horizontal > 0.1
                            else _number(state.get('yaw'))))
            world_pitch = (_number(ballistic_solution.get('pitch'))
                           if isinstance(ballistic_solution, dict) else
                           -math.atan2(
                               (aim_position[1] + 1.0) - origin[1],
                               max(0.5, horizontal)))
        self._update_hydraulic_suspension(
            state, descriptor, desired_yaw, world_pitch, step)
        local_angles = self._local_gun_angles_for_world(
            state, desired_yaw, world_pitch)
        if local_angles is None:
            local_angles = (
                _angle_delta(desired_yaw, state['yaw']), world_pitch)
        raw_relative, raw_pitch = local_angles
        minimum_yaw, maximum_yaw, limited = \
            self._effective_gun_yaw_limits(state, descriptor)
        desired_relative = raw_relative
        if limited:
            desired_relative = max(
                minimum_yaw, min(maximum_yaw, desired_relative))
        gun_state = self._gun_states.get(state['id'])
        modifier_bundle = (gun_state.loadout
                           if gun_state is not None else {})
        turret = _value(descriptor, 'turret', {}) or {}
        turret_speed = (_rotation_speed(turret, 0.5) *
                        max(0.0, _number(
                            modifier_bundle.get('crew_factor'), 1.0)) *
                        _critical_factor(
                            state, descriptor, 'turret_speed'))
        turret_step = turret_speed * step
        current_relative = _number(state.get('turret_yaw'))
        turret_difference = _angle_delta(desired_relative, current_relative)
        current_relative = _wrapped(
            current_relative + max(-turret_step,
                                   min(turret_step, turret_difference)))
        if limited:
            current_relative = max(
                minimum_yaw, min(maximum_yaw, current_relative))
        state['turret_yaw'] = current_relative

        turret_rotation_time = 0.0
        if turret_speed > 0.0:
            turret_rotation_time = abs(
                current_relative - raw_relative) / turret_speed

        desired_pitch = raw_pitch
        pitch_limits = self._effective_gun_pitch_limits(
            state, descriptor, current_relative)
        if pitch_limits is None:
            desired_pitch = _number(state.get('gun_pitch'))
        else:
            desired_pitch = max(
                pitch_limits[0], min(pitch_limits[1], desired_pitch))
        gun = _value(descriptor, 'gun', {}) or {}
        static_pitch = self._static_gun_value(descriptor, 'staticPitch')
        if pitch_limits is not None:
            state['gun_pitch'] = hull_aiming.gun_pitch_step(
                _number(state.get('gun_pitch')), raw_pitch, static_pitch,
                _rotation_speed(gun, 0.35) * max(
                    0.0, _number(modifier_bundle.get(
                        'gun_rotation_factor'), 1.0)),
                step, turret_rotation_time, pitch_limits)
        state['desired_gun_pitch'] = desired_pitch
        world_angles = self._world_barrel_angles(state, descriptor)
        state['aim_yaw'] = (
            world_angles[0] if world_angles is not None else
            _wrapped(state['yaw'] + current_relative))
        state['gun_aligned'] = bool(
            pitch_limits is not None and target is not None and
            abs(_angle_delta(raw_relative, state['turret_yaw'])) <= 0.06 and
            abs(raw_pitch - state['gun_pitch']) <= 0.04)
        return desired_yaw, horizontal

    @staticmethod
    def _shot_los_key(source, target):
        target_id = target.get('network_id', target.get('id', 0))
        return (int(source.get('id', 0)), target.get('kind'), int(target_id))

    @staticmethod
    def _shot_los_phase(key):
        kind_salt = 11 if key[1] == 'human' else 0
        bucket = ((abs(int(key[0])) * 31 + abs(int(key[2])) * 17 +
                   kind_salt) % SHOT_LANE_PHASES)
        return (float(bucket) / SHOT_LANE_PHASES) * \
            SHOT_LANE_REFRESH_SECONDS

    def _shot_clear(self, source, target, now, force=False,
                    probe_budget=None, lane_key=None, distance_cache=None):
        """Probe a current static firing lane independently from team spotting."""
        key = (lane_key if lane_key is not None else
               self._shot_los_key(source, target))
        if distance_cache is not None and distance_cache[0] is not None:
            target_distance = distance_cache[0]
        else:
            target_position = _point(
                target.get('position'), _position(target))
            target_distance = _distance(_position(source), target_position)
            if distance_cache is not None:
                distance_cache[0] = target_distance
        profile = source.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        query_distance = (SPG_SHOT_LANE_QUERY_DISTANCE
                          if str(profile.get('class_tag') or '') == 'SPG'
                          else SHOT_LANE_QUERY_DISTANCE)
        if target_distance > query_distance:
            self._shot_los_cache[key] = (_number(now), False)
            return False
        cached = self._shot_los_cache.get(key)
        if (not force and cached is not None and
                _number(now) - cached[0] <= SHOT_LANE_SECONDS + 1e-9):
            return cached[1]
        if probe_budget is not None:
            if probe_budget[0] <= 0:
                return None
            probe_budget[0] -= 1
        self._probe_totals[1] += 1
        probe_started = self._probe_started()
        try:
            value = bool(self.firing_lane_probe(source, target))
        finally:
            self._probe_finished(1, probe_started)
        self._shot_los_cache[key] = (_number(now), value)
        if len(self._shot_los_cache) > 1024:
            oldest = sorted(self._shot_los_cache.items(),
                            key=lambda item: item[1][0])[:256]
            for old_key, unused_value in oldest:
                self._shot_los_cache.pop(old_key, None)
                self._shot_los_deadlines.pop(old_key, None)
        return value

    def _refresh_shot_clear(self, source, target, now, observation_time,
                            probe_budget=None, lane_key=None,
                            distance_cache=None):
        """Refresh one pair on a stable phase before its observation."""
        key = (lane_key if lane_key is not None else
               self._shot_los_key(source, target))
        now = _number(now)
        observation_time = _number(observation_time)
        if self._shot_los_deadlines.get(key) == observation_time:
            return False
        window_start = observation_time - SHOT_LANE_REFRESH_SECONDS
        deadline = window_start + self._shot_los_phase(key)
        cached = self._shot_los_cache.get(key)
        if cached is not None and cached[0] > window_start + 1e-9:
            self._shot_los_deadlines[key] = observation_time
            return False
        if now + 1e-9 < deadline:
            return False
        value = self._shot_clear(
            source, target, now, force=True, probe_budget=probe_budget,
            lane_key=key, distance_cache=distance_cache)
        if value is None:
            return False
        self._shot_los_deadlines[key] = observation_time
        return True

    def _pack_observations(self, aggregate, now):
        """Serialise one lightweight record per canonical team target.

        Lane checks remain in the per-bot loop so their cache and native-probe
        side effects keep the same order. The tactical lane cache is advisory
        and refreshes independently; the last target snapshot and visibility
        OR remain complete on every latency-sensitive observation.
        """
        packed = []
        for key in sorted(aggregate):
            row = aggregate[key]
            target_visible, shootable, observed_target = row[:3]
            visible_by_players = row[3] if len(row) > 3 else ()
            visible_by_bots = row[4] if len(row) > 4 else ()
            time_left = self._team_spot_time_left(key, now)
            visible = bool(time_left > 0.0)
            remembered = self._visible_target_poses.get(key)
            if visible and remembered is None:
                # A fresh authority starts with no inherited pose.  A lease
                # without one is not renderable and must not expose the
                # simulator's current omniscient target state.
                visible = False
                time_left = 0.0
            elif visible:
                observed_target.update(remembered)
            fresh = bool(
                visible and (visible_by_players or visible_by_bots))
            if not fresh:
                shootable = ()
            if not visible:
                visible_by_players = ()
                visible_by_bots = ()
                time_left = 0.0
            profile = observed_target.get('profile')
            profile = profile if isinstance(profile, dict) else {}
            packed.append({
                'observing_team': key[0], 'target_kind': key[1],
                'target_id': key[2],
                'target_team': int(observed_target.get('team', 0)),
                'visible': visible,
                'fresh': fresh,
                'time_left': round(time_left, 6),
                # These identities are produced only by the hidden worker's
                # native LOS probes. Visible clients cannot submit this
                # authority message.
                'visible_by_player_ids': sorted(visible_by_players),
                'visible_by_bot_ids': sorted(visible_by_bots),
                # Current clients always publish this field. An empty list
                # means team-spotted without a local firing lane; the server
                # rejects omission rather than guessing.
                'shootable_by_bot_ids': sorted(shootable),
                'x': _number(observed_target.get('x')),
                'y': _number(observed_target.get('y')),
                'z': _number(observed_target.get('z')),
                'health': max(0, int(_number(
                    observed_target.get('health'), 1))),
                'max_health': max(1, int(_number(
                    observed_target.get('max_health'), 1))),
                'class_tag': observed_target.get(
                    'class_tag', profile.get('class_tag', 'unknown')),
                'armor': max(0.0, _number(
                    observed_target.get(
                        'armor', profile.get('armor', 0.0)))),
            })
        return packed

    @classmethod
    def _spg_exact_aligned(cls, state, descriptor, ballistic_solution):
        if (not state.get('gun_aligned') or
                not isinstance(ballistic_solution, dict)):
            return False
        world_angles = cls._world_barrel_angles(state, descriptor)
        if world_angles is None:
            return False
        return (
            abs(_angle_delta(
                _number(ballistic_solution.get('yaw')),
                world_angles[0])) <= 1e-7 and
            abs(_number(ballistic_solution.get('pitch')) -
                world_angles[1]) <= 1e-7)

    def _validated_artillery_receipt(
            self, value, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time):
        if not isinstance(value, dict) or 'proof_key' not in value:
            return None
        required = (
            'origin', 'velocity', 'shot_yaw', 'shot_pitch', 'gravity',
            'max_distance', 'max_time_ms', 'fire_seq', 'shell_index',
            'flight_time')
        if any(name not in value for name in required):
            return None
        try:
            origin = tuple(float(component) for component in value['origin'])
            velocity = tuple(
                float(component) for component in value['velocity'])
            receipt_yaw = float(value['shot_yaw'])
            receipt_pitch = float(value['shot_pitch'])
            gravity = float(value['gravity'])
            maximum = float(value['max_distance'])
            max_time_ms = int(value['max_time_ms'])
            receipt_fire_seq = int(value['fire_seq'])
            receipt_shell_index = int(value['shell_index'])
            receipt_flight = float(value['flight_time'])
            if len(origin) != 3 or len(velocity) != 3:
                return None
        except (TypeError, ValueError, OverflowError):
            return None
        values = origin + velocity + (
            receipt_yaw, receipt_pitch, gravity, maximum, receipt_flight)
        physical = _shot_ballistics(descriptor, shell_index)
        if (physical is None or
                any(math.isnan(component) or math.isinf(component)
                    for component in values) or
                receipt_fire_seq != int(fire_seq) or
                receipt_shell_index != int(shell_index) or
                receipt_yaw != float(shot_yaw) or
                receipt_pitch != float(shot_pitch) or
                receipt_flight != float(flight_time) or
                max_time_ms <= 0 or max_time_ms > 20000 or
                gravity != float(physical[1]) or
                maximum != float(physical[2])):
            return None
        horizontal = math.cos(shot_pitch)
        expected_velocity = (
            math.sin(shot_yaw) * horizontal * physical[0],
            math.sin(shot_pitch) * physical[0],
            math.cos(shot_yaw) * horizontal * physical[0],
        )
        if any(abs(velocity[index] - expected_velocity[index]) > 1e-7
               for index in range(3)):
            return None
        result = dict(value)
        result.update({
            'origin': origin, 'velocity': velocity,
            'shot_yaw': receipt_yaw, 'shot_pitch': receipt_pitch,
            'gravity': gravity, 'max_distance': maximum,
            'max_time_ms': max_time_ms, 'flight_time': receipt_flight,
        })
        return result

    def _reject_stale_artillery_receipt(
            self, state, target, descriptor, shell_index, intent,
            receipt, now):
        intent_solution = intent.get('solution')
        intent_solution = (intent_solution
                           if isinstance(intent_solution, dict) else {})
        try:
            intended_impact = _point(intent_solution['aim_position'])
            flight_time = float(receipt['flight_time'])
        except (KeyError, TypeError, ValueError, OverflowError):
            self._cancel_artillery_intent(state.get('id'))
            return True
        target_velocity = self._target_velocity(target)
        target_position = _point(
            target.get('position'), _position(target))
        target_at_impact = (
            target_position[0] + target_velocity[0] * flight_time,
            target_position[1] + 1.0 + target_velocity[1] * flight_time,
            target_position[2] + target_velocity[2] * flight_time,
        )
        error = tuple(target_at_impact[index] - intended_impact[index]
                      for index in range(3))
        if any(math.isnan(value) or math.isinf(value)
               for value in target_at_impact + intended_impact + error):
            self._cancel_artillery_intent(state.get('id'))
            return True
        distance = math.sqrt(sum(value * value for value in error))
        if distance <= ARTILLERY_AIM_STALENESS_METRES + 1e-9:
            return False
        reproof = self._active_artillery_reproof(
            state, target, descriptor, shell_index, now)
        if reproof is None:
            self._cancel_artillery_intent(state.get('id'))
            return True
        # Re-lead only for target motion accumulated while the native world
        # proof was pending.  Never feed the dispersed terminal back as an aim
        # correction: doing so cancels this fire sequence's random offset.
        reproof['proof_latency'] = max(
            0.0, _number(now) - _number(intent.get('created')))
        reproof['attempts'] = int(reproof.get('attempts', 0)) + 1
        reproof['deadline'] = min(
            _number(reproof.get('absolute_deadline', reproof['deadline'])),
            _number(now) + ARTILLERY_REPROOF_SECONDS)
        reproof['last_proof_latency'] = max(
            0.0, _number(now) - _number(intent.get('created')))
        reproof['last_aim_staleness'] = distance
        reproof['arc'] = str(intent_solution.get('arc') or '')
        self._cancel_artillery_intent(
            state.get('id'), preserve_reproof=True)
        return True

    def _artillery_launch_receipt(
            self, state, target, descriptor, shell_index, gun_state,
            ballistic_solution, now):
        if not callable(self.artillery_launch_probe):
            return None
        intent = self._active_artillery_intent(
            state, target, descriptor, shell_index, now)
        if intent is None:
            intent = self._create_artillery_intent(
                state, target, descriptor, shell_index, gun_state,
                ballistic_solution, now)
        if (intent is None or
                not self._spg_exact_aligned(
                    state, descriptor, intent['solution'])):
            return None
        fire_seq = intent['fire_seq']
        shot_yaw = intent['shot_yaw']
        shot_pitch = intent['shot_pitch']
        flight_time = intent['solution']['flight_time']
        if self._probe_clock is None:
            value = self.artillery_launch_probe(
                _copy_runtime_state(state), dict(target), descriptor,
                int(shell_index),
                fire_seq, shot_yaw, shot_pitch, flight_time, _number(now))
        else:
            probe_started = self._probe_started()
            try:
                value = self.artillery_launch_probe(
                    _copy_runtime_state(state), dict(target), descriptor,
                    int(shell_index),
                    fire_seq, shot_yaw, shot_pitch, flight_time,
                    _number(now))
            finally:
                self._probe_finished(1, probe_started)
        receipt = self._validated_artillery_receipt(
            value, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time)
        if receipt is None:
            return None
        # A valid receipt proves the frozen random parabola against the world;
        # it does not prove a hit. Reproof may refresh only an aim solution that
        # went stale while queued; it must never compensate the random endpoint.
        if self._reject_stale_artillery_receipt(
                state, target, descriptor, shell_index, intent, receipt, now):
            return None
        return receipt

    def _direct_launch_preview(
            self, state, descriptor, shell_index, gun_state,
            ballistic_solution, burst_edge=None):
        """Freeze the exact next direct-shell angles without committing it."""
        flight_time = None
        if isinstance(ballistic_solution, dict):
            try:
                flight_time = float(ballistic_solution['flight_time'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
        elif isinstance(burst_edge, dict):
            # Once armed, a native automatic group keeps firing from the
            # current barrel even when its original target solution is lost.
            physical = _shot_ballistics(descriptor, shell_index)
            if physical is None:
                return None
            speed, unused_gravity, maximum = physical
            if speed > 0.0 and maximum > 0.0:
                flight_time = min(
                    ballistics.PROJECTILE_MAX_FLIGHT_SECONDS,
                    maximum / speed)
        if flight_time is None:
            return None
        if (math.isnan(flight_time) or math.isinf(flight_time) or
                flight_time <= 0.0 or
                flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS):
            return None
        fire_seq = int(state.get('fire_seq', 0)) + 1
        burst_index = 0
        burst_group_seq = fire_seq
        if isinstance(burst_edge, dict):
            try:
                fire_seq = int(burst_edge['shot_seq'])
                burst_index = int(burst_edge['burst_index'])
                burst_group_seq = int(burst_edge['burst_group_seq'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            if fire_seq != int(state.get('fire_seq', 0)) + 1:
                return None
        base_direction = self._dispersal_base_direction(state, descriptor)
        if (base_direction is None and
                (abs(_number(state.get('pitch'))) > 1.0e-12 or
                 abs(_number(state.get('roll'))) > 1.0e-12)):
            return None
        shot_yaw, shot_pitch = _dispersed_barrel_angles(
            state['id'], self.round_id, fire_seq,
            state['aim_yaw'], state['gun_pitch'],
            _effective_shot_dispersion(gun_state, state, descriptor),
            burst_index, burst_group_seq,
            base_direction=base_direction)
        try:
            if self._probe_clock is None:
                raw_origin = self.direct_launch_origin_probe(
                    _copy_runtime_state(state), descriptor, int(shell_index),
                    fire_seq,
                    shot_yaw, shot_pitch, flight_time)
            else:
                probe_started = self._probe_started()
                try:
                    raw_origin = self.direct_launch_origin_probe(
                        _copy_runtime_state(state), descriptor,
                        int(shell_index), fire_seq,
                        shot_yaw, shot_pitch, flight_time)
                finally:
                    self._probe_finished(1, probe_started)
            origin = tuple(float(value) for value in raw_origin)
        except Exception:
            return None
        if (len(origin) != 3 or
                any(math.isnan(value) or math.isinf(value)
                    for value in origin)):
            return None
        return {
            'fire_seq': fire_seq,
            'shell_index': int(shell_index),
            'shot_yaw': shot_yaw,
            'shot_pitch': shot_pitch,
            'flight_time': flight_time,
            'origin': origin,
        }

    @staticmethod
    def _friendly_lane_verdict(value):
        """Return a fail-closed clear flag plus optional blocker metadata."""
        if isinstance(value, dict):
            if not isinstance(value.get('clear'), bool):
                return False, {}
            return bool(value['clear']), dict(value)
        return bool(value), {}

    def _clear_friendly_reposition(self, bot_id):
        try:
            bot_id = int(bot_id)
        except (TypeError, ValueError, OverflowError):
            return False
        removed = self._friendly_repositions.pop(bot_id, None)
        if removed is not None:
            self._decision_cache.pop(bot_id, None)
        return removed is not None

    def _mark_friendly_reposition(
            self, state, command, target, launch, verdict, now):
        """Move laterally through the existing safe driver after a blocked shot."""
        try:
            bot_id = int(state['id'])
            source_team = int(state['team'])
            fire_seq = int(launch['fire_seq'])
            shot_yaw = float(launch['shot_yaw'])
            target_id = int(command['target_id'])
            blocker_kind = str(verdict['blocker_kind'])
            blocker_id = int(verdict['blocker_id'])
            blocker_team = int(verdict['blocker_team'])
            blocker_position = tuple(
                float(verdict['blocker_position'][index])
                for index in range(3))
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False
        if (blocker_kind not in ('bot', 'player') or
                blocker_id <= 0 or blocker_team != source_team or
                (blocker_kind == 'bot' and blocker_id == bot_id) or
                any(math.isnan(value) or math.isinf(value)
                    for value in blocker_position)):
            return False
        shape = state.get('collision_shape')
        try:
            source_radius = math.hypot(float(shape[0]), float(shape[1]))
        except (TypeError, ValueError, IndexError):
            source_radius = math.hypot(
                max(0.3, _number(state.get('half_width'), 1.7)),
                max(0.5, _number(state.get('half_length'), 3.5)))
        blocker_radius = _number(
            verdict.get('blocker_radius'), source_radius)
        if (math.isnan(blocker_radius) or math.isinf(blocker_radius) or
                blocker_radius <= 0.0):
            blocker_radius = source_radius
        clearance = source_radius + blocker_radius + \
            tank_collision.POSITION_SLOP
        side = 1.0 if ((bot_id + fire_seq) & 1) else -1.0
        position = _position(state)
        destination = (
            position[0] + math.cos(shot_yaw) * clearance * side,
            position[1],
            position[2] - math.sin(shot_yaw) * clearance * side,
        )
        self._friendly_repositions[bot_id] = {
            'target_id': target_id,
            'target_kind': target.get('kind') if isinstance(target, dict)
            else None,
            'destination': destination,
            'shell_index': int(state.get('shell_index', 0)),
            'fire_range': max(0.0, _number(command.get('fire_range'))),
            'deadline': _number(now) + FRIENDLY_REPOSITION_SECONDS,
        }
        # The next authority frame must run the safe LocalDriver for this new
        # destination; a cached hold command would otherwise remain stationary.
        self._decision_cache.pop(bot_id, None)
        return True

    def _friendly_reposition_order(self, state, targets, now):
        """Return an ordinary lane escape plus whether its lease expired."""
        bot_id = int(state['id'])
        marker = self._friendly_repositions.get(bot_id)
        if marker is None:
            return None, False
        if _number(now) >= _number(marker.get('deadline')):
            self._clear_friendly_reposition(bot_id)
            return None, True
        target = (targets or {}).get(marker['target_id'])
        if (not isinstance(target, dict) or
                not bool(target.get('alive', True)) or
                ('health' in target and _number(target.get('health')) <= 0.0)):
            self._clear_friendly_reposition(bot_id)
            return None, False
        destination = marker['destination']
        if _distance(_position(state), destination) <= \
                ai_driver.WAYPOINT_ARRIVAL_RADIUS:
            self._clear_friendly_reposition(bot_id)
            return None, False
        target_position = _point(
            target.get('position'), _position(target))
        return {
            'target_id': marker['target_id'],
            'aim_position': target_position,
            'face_position': target_position,
            'move_position': destination,
            'fire_allowed': False,
            'combat_mode': 'friendly_lane_reposition',
            'throttle_override': 0.72,
            'fire_range': marker['fire_range'],
            'shell_index': marker['shell_index'],
        }, False

    @staticmethod
    def _direct_preview_values(launch_preview, expected_seq):
        try:
            preview_seq = int(launch_preview['fire_seq'])
            preview_yaw = float(launch_preview['shot_yaw'])
            preview_pitch = float(launch_preview['shot_pitch'])
            preview_origin = tuple(
                float(value) for value in launch_preview['origin'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if (preview_seq != int(expected_seq) or len(preview_origin) != 3 or
                math.isnan(preview_yaw) or math.isinf(preview_yaw) or
                math.isnan(preview_pitch) or math.isinf(preview_pitch) or
                any(math.isnan(value) or math.isinf(value)
                    for value in preview_origin)):
            return None
        return preview_yaw, preview_pitch, preview_origin

    def _queue_pending_launch(self, launch):
        """Freeze one physical launch in its ordered reliable outbox."""
        try:
            key = (int(launch['id']), int(launch['fire_seq']))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('physical bot launch identity is invalid')
        frozen = dict(launch)
        previous = self._pending_launch_keys.get(key)
        if previous is not None:
            if previous != frozen:
                raise RuntimeError('physical bot launch identity changed')
            return False
        self._pending_launches.append(frozen)
        self._pending_launch_keys[key] = frozen
        self._pending_launch_by_bot.setdefault(key[0], []).append(key)
        return True

    def ack_projectile_launch(self, bot_id, fire_seq):
        """Remove only this Bot's head confirmed by the canonical ledger."""
        try:
            key = (int(bot_id), int(fire_seq))
        except (TypeError, ValueError, OverflowError):
            return False
        bot_queue = self._pending_launch_by_bot.get(key[0]) or []
        if not bot_queue or bot_queue[0] != key:
            return False
        frozen = self._pending_launch_keys.get(key)
        if frozen is None:
            return False
        launch_index = next((
            index for index, launch in enumerate(self._pending_launches)
            if launch is frozen), None)
        if launch_index is None:
            return False
        del self._pending_launches[launch_index]
        self._pending_launch_keys.pop(key, None)
        del bot_queue[0]
        if not bot_queue:
            self._pending_launch_by_bot.pop(key[0], None)
        return True

    def _commit_burst_edge(
            self, state, gun_state, reload_factor, descriptor, edge,
            ammo_state, launch_receipt=None, launch_preview=None,
            launch_time_us=None):
        """Atomically commit one real projectile in an armed group."""
        if (state.get('_drowning', False) or
                state.get('_overturned', False)):
            return False
        try:
            shot_seq = int(edge['shot_seq'])
            group_seq = int(edge['burst_group_seq'])
            burst_index = int(edge['burst_index'])
            burst_count = int(edge['burst_count'])
            shell_index = int(edge['shell_index'])
            final_round = bool(edge['final'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if (shot_seq != int(state.get('fire_seq', 0)) + 1 or
                shot_seq != group_seq + burst_index or
                burst_index < 0 or burst_index >= burst_count or
                shell_index != int(ammo_state.loaded)):
            return False
        preview_values = None
        fallback_direction = None
        if launch_receipt is not None:
            if (launch_preview is not None or burst_count != 1 or
                    int(launch_receipt.get('fire_seq', -1)) != shot_seq):
                return False
        elif launch_preview is not None:
            preview_values = self._direct_preview_values(
                launch_preview, shot_seq)
            if preview_values is None:
                return False
        else:
            fallback_direction = self._dispersal_base_direction(
                state, descriptor)
            if (fallback_direction is None and
                    (abs(_number(state.get('pitch'))) > 1.0e-12 or
                     abs(_number(state.get('roll'))) > 1.0e-12)):
                return False
        continuing = burst_index > 0
        if (not ammo_state.can_fire(continuing) or
                not gun_state.fire_burst_round(
                    final_round, reload_factor)):
            return False
        if not ammo_state.consume_loaded(continuing):
            raise RuntimeError(
                'bot ammunition changed during atomic burst')
        if final_round and ammo_state.loaded_shell_requires_full_reload():
            gun_state.require_full_reload()
        state['fire_seq'] = shot_seq
        state['burst_group_seq'] = group_seq
        state['burst_index'] = burst_index
        state['burst_count'] = burst_count
        for name in (
                'shot_origin', 'shot_velocity', 'shot_gravity',
                'shot_max_distance', 'shot_max_time_ms', 'shot_proof_key'):
            state.pop(name, None)
        if launch_receipt is not None:
            state['shot_yaw'] = launch_receipt['shot_yaw']
            state['shot_pitch'] = launch_receipt['shot_pitch']
            state['shot_origin'] = tuple(launch_receipt['origin'])
            state['shot_velocity'] = tuple(launch_receipt['velocity'])
            state['shot_gravity'] = launch_receipt['gravity']
            state['shot_max_distance'] = launch_receipt['max_distance']
            state['shot_max_time_ms'] = launch_receipt['max_time_ms']
            state['shot_proof_key'] = launch_receipt['proof_key']
        elif preview_values is not None:
            state['shot_yaw'], state['shot_pitch'], state['shot_origin'] = \
                preview_values
        else:
            state['shot_yaw'], state['shot_pitch'] = \
                _dispersed_barrel_angles(
                    state['id'], self.round_id, shot_seq,
                    state['aim_yaw'], state['gun_pitch'],
                    _effective_shot_dispersion(
                        gun_state, state, descriptor),
                    burst_index, group_seq,
                    base_direction=fallback_direction)
        gun_state.commit_shot_bloom(
            _critical_factor(state, descriptor, 'dispersion'),
            final_round=final_round)
        state['clip'] = gun_state.clip
        state['reload_time'] = gun_state.remaining(reload_factor)
        state['reload_duration'] = gun_state.duration(reload_factor)
        ammo_state.publish(state)
        if launch_time_us is None:
            launch_time_us = self._sample_time_us
        launch = _local_launch_record(state, launch_time_us)
        if launch is None:
            raise RuntimeError('physical bot burst launch is incomplete')
        self._queue_pending_launch(launch)
        return True

    def _fire(self, state, gun_state, reload_factor, descriptor,
              launch_receipt=None, ammo_state=None, launch_preview=None,
              launch_time_us=None):
        if (state.get('_drowning', False) or
                state.get('_overturned', False)):
            return False
        if ammo_state is None:
            ammo_state = self._ammo_states.get(int(state.get('id', 0)))
        if ammo_state is None:
            ammo_state = _BotAmmoState(
                descriptor, state.get('profile') or {}, state)
            self._ammo_states[int(state.get('id', 0))] = ammo_state
            ammo_state.stage(state.get('shell_index', 0), True)
        if not ammo_state.can_fire():
            return False
        count, interval = burst_mechanics.planned_count(
            _value(descriptor, 'gun', {}) or {},
            ammo_state.remaining[ammo_state.loaded], gun_state.clip)
        if count <= 0:
            return False
        # Every SPG parabola requires its own world proof.  The pinned build
        # has no burst artillery descriptor, so keep that exact path singular.
        if launch_receipt is not None:
            count, interval = 1, 0.0
        burst_state = self._burst_states.get(int(state['id']))
        if burst_state is None:
            burst_state = burst_mechanics.BurstClock()
            self._burst_states[int(state['id'])] = burst_state
        first_seq = int(state.get('fire_seq', 0)) + 1
        if (not burst_state.start(
                first_seq, count, interval, ammo_state.loaded) or
                not gun_state.begin_burst(count, reload_factor)):
            burst_state.cancel(0)
            return False
        edge = burst_state.advance(0.0)[0]
        if not self._commit_burst_edge(
                state, gun_state, reload_factor, descriptor, edge,
                ammo_state, launch_receipt, launch_preview,
                launch_time_us):
            gun_state.cancel_burst()
            burst_state.cancel(0)
            return False
        burst_state.publish(state)
        return True

    def _cancel_active_burst(
            self, state, gun_state=None, ammo_state=None, burst_state=None):
        """Cancel the unlaunched tail and begin ordinary recovery."""
        bot_id = int(state.get('id', 0))
        burst_state = burst_state or self._burst_states.get(bot_id)
        gun_state = gun_state or self._gun_states.get(bot_id)
        gun_pending = bool(
            gun_state is not None and gun_state._burst_remaining > 0)
        if burst_state is None or (not burst_state.active and not gun_pending):
            return False
        launched = 0
        if burst_state.group_seq > 0:
            launched = max(
                0, int(state.get('fire_seq', 0)) - burst_state.group_seq + 1)
        launched = min(launched, burst_state.next_index)
        burst_state.cancel(launched)
        if gun_pending:
            gun_state.cancel_burst()
        ammo_state = ammo_state or self._ammo_states.get(bot_id)
        if ammo_state is not None:
            ammo_state.publish(state)
        if gun_state is not None:
            reload_factor = _critical_factor(
                state, self._descriptors.get(bot_id, {}), 'reload')
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = gun_state.duration(reload_factor)
        burst_state.publish(state)
        return True

    def _advance_active_burst(
            self, state, gun_state, ammo_state, reload_factor, descriptor,
            target, ballistic_solution, step, destroyed_devices,
            step_start_time_us=None, step_end_time_us=None):
        """Launch every descriptor-cadence edge crossed by this tick."""
        burst_state = self._burst_states.get(int(state['id']))
        if burst_state is None or not burst_state.active:
            return 0
        if step_start_time_us is None:
            step_start_time_us = self._sample_time_us
        if step_end_time_us is None:
            step_end_time_us = step_start_time_us + max(
                0, int(round(float(step) * 1000000.0)))
        committed = 0
        for edge in burst_state.advance(step):
            launch_time_us = min(
                int(step_end_time_us), int(step_start_time_us) + max(
                    0, int(round(float(edge['due_offset']) * 1000000.0))))
            if (not state.get('alive', False) or
                    state.get('_drowning', False) or
                    state.get('_overturned', False) or
                    'gunHealth' in destroyed_devices or
                    int(state.get('siege_state',
                                  siege_mechanics.DISABLED)) in (
                        siege_mechanics.SWITCHING_ON,
                        siege_mechanics.SWITCHING_OFF) or
                    not ammo_state.can_fire(True)):
                self._cancel_active_burst(
                    state, gun_state, ammo_state, burst_state)
                break
            preview = self._direct_launch_preview(
                state, descriptor, burst_state.shell_index, gun_state,
                ballistic_solution, burst_edge=edge)
            if preview is None:
                self._cancel_active_burst(
                    state, gun_state, ammo_state, burst_state)
                break
            if self._probe_clock is None:
                lane_value = self.friendly_lane_probe(
                    state, target if isinstance(target, dict) else {},
                    descriptor, burst_state.shell_index, preview)
            else:
                probe_started = self._probe_started()
                try:
                    lane_value = self.friendly_lane_probe(
                        state, target if isinstance(target, dict) else {},
                        descriptor, burst_state.shell_index, preview)
                finally:
                    self._probe_finished(1, probe_started)
            lane_clear, unused_verdict = self._friendly_lane_verdict(
                lane_value)
            if (not lane_clear or
                    not self._commit_burst_edge(
                        state, gun_state, reload_factor, descriptor, edge,
                        ammo_state, launch_preview=preview,
                        launch_time_us=launch_time_us)):
                self._cancel_active_burst(
                    state, gun_state, ammo_state, burst_state)
                break
            committed += 1
        burst_state.publish(state)
        return committed

    def _bounded_burst_step(self, maximum):
        """End a simulation slice at the next armed physical round."""
        result = max(0.0, float(maximum))
        for burst_state in self._burst_states.values():
            if not burst_state.active:
                continue
            due = max(0.0, float(burst_state.time_left))
            if due <= 1.0e-9:
                return min(result, 1.0e-9)
            result = min(result, due)
        return result

    def update(self, dt, now, players=None, neighbours=None):
        """Advance fixed-rate Bot control with bounded render catch-up.

        Render callbacks only add elapsed debt.  At most two configured fixed
        control steps run in one callback, so a stall cannot immediately
        multiply all roster probes and projections. Debt is retained, never
        discarded; ordinary render rates drain it over following callbacks.
        Burst clocks still advance across the complete processed step and
        enqueue every crossed physical round with its exact due timestamp.
        """
        if (not self.is_authority() or self.adapter is None or
                self.finished):
            return []
        elapsed_input = max(0.0, _number(dt))
        self._accumulator += elapsed_input
        now = _number(now)
        navigator_begin = getattr(self.navigator, 'begin_frame', None)
        navigator_end = getattr(self.navigator, 'end_frame', None)
        if callable(navigator_begin):
            navigator_begin(elapsed_input)
        outgoing = []
        receipt_frame_open = False
        try:
            if not self._fixed_control:
                # Preserve the mature engine-free seam for focused law tests
                # and non-production callers. Hidden workers always select the
                # fixed branch explicitly at construction.
                if (self._accumulator <= 0.0 or
                        now + 1e-9 < self._next_publication):
                    return []
            elif self._accumulator + 1e-9 < self._control_seconds:
                return []
            # Exact world-receipt work is capped per render callback, not per
            # catch-up step. Do not open an empty receipt frame on intervening
            # high-FPS callbacks: finishing one without a control step would
            # retire valid deferred requests as no longer eligible.
            self._begin_world_receipt_frame()
            receipt_frame_open = True
            try:
                if not self._fixed_control:
                    elapsed = self._accumulator
                    self._accumulator = 0.0
                    while elapsed > 1e-12:
                        frame_step = self._bounded_burst_step(
                            min(elapsed, 0.2))
                        elapsed = max(0.0, elapsed - frame_step)
                        step_now = now - elapsed
                        outgoing.extend(self._update_once(
                            frame_step, step_now, players, neighbours))
                else:
                    steps = 0
                    while (steps < MAX_CONTROL_STEPS_PER_FRAME and
                           self._accumulator + 1e-9 >=
                           self._control_seconds):
                        self._accumulator = max(
                            0.0, self._accumulator - self._control_seconds)
                        step_now = now - self._accumulator
                        outgoing.extend(self._update_once(
                            self._control_seconds, step_now,
                            players, neighbours))
                        steps += 1
            finally:
                if receipt_frame_open:
                    self._finish_world_receipt_frame()
        finally:
            if callable(navigator_end):
                navigator_end()
        source_batch_horizon_us = self._sample_time_us
        for message in outgoing:
            if message.get('type') != 'bot_state':
                continue
            sample_time_us = int(message['sample_time_us'])
            if sample_time_us > source_batch_horizon_us:
                raise RuntimeError('bot source sample exceeds its batch horizon')
            message['source_batch_horizon_us'] = source_batch_horizon_us
        return outgoing

    def _update_once(self, frame_step, now, players=None, neighbours=None):
        """Advance one stable authority substep and preserve its events."""
        publish = True
        step_duration_us = max(
            1, int(round(float(frame_step) * 1000000.0)))
        step_start_time_us = self._sample_time_us
        step_end_time_us = step_start_time_us + step_duration_us
        if self._next_publication <= 0.0:
            self._next_publication = now
        # This is a diagnostic authority deadline only; fixed control debt is
        # scheduled by update() and never derived from render-frame spacing.
        while self._next_publication <= now + 1e-9:
            self._next_publication += self._control_seconds
        self._advance_probe_timing(now)
        self._advance_equipment_clock(frame_step)
        players = list(players or [])
        self._track_human_observer_lifecycle(players, now)
        live_players = None
        live_probe_targets = {}
        processed_bot_ids = set()
        # Spotting is latency-sensitive presentation state. Full-roster firing
        # lanes and cover fans only guide the one-hertz server planner, so they
        # run on independent clocks and can never delay this observation.
        observation_due = now >= self._next_observation
        collect_observation = publish and observation_due
        shot_lane_refresh_time = self._next_shot_lane_refresh
        shot_lane_refresh_due = now >= shot_lane_refresh_time
        refresh_shot_lanes = (
            not self._cover_queue and
            (shot_lane_refresh_due or
             (shot_lane_refresh_time > 0.0 and
              now + SHOT_LANE_REFRESH_SECONDS + 1e-9 >=
              shot_lane_refresh_time)))
        collect_cover_jobs = bool(
            publish and not self._cover_queue and
            now >= self._next_cover_refresh)
        shot_lane_budget = [MAX_SHOT_LANE_PAIRS_PER_FRAME]
        shot_lanes_ready = True
        neighbours = list(neighbours or []) + self._player_neighbours(players)
        # Native terrain and visibility probes run on BigWorld's render thread.
        # Build the local-overlap view lazily, only when a staggered decision is
        # due. It steers apart hulls which already touch; it never predicts
        # traffic or changes their strategic terrain path.
        traffic_bodies = None
        traffic_index = None
        observation_entries = {}
        observation_pairs = []
        team_visibility = {}
        # Source-independent camouflage projections are exact for this bounded
        # simulation slice. Share them across all observers, but never across
        # slices where motion or equipment state may change.
        visibility_tick = {}
        if collect_observation or refresh_shot_lanes:
            self._append_human_observations(
                players, now, observation_entries, team_visibility,
                visibility_tick)
        cover_jobs = []
        tick_poses = {}
        tick_safe = {}
        attempted_yaws = {}
        siege_locked_poses = {}
        integrated = set()
        for state in self.states.values():
            if not state['alive']:
                self._cancel_active_burst(state)
                continue
            self._note_source_stillness(state, now)
            # Every live bot consumes the same banked authority step. Planner
            # and slope detail may still vary by distance, but skipping one
            # far bot here would leak an incomplete observation and a different
            # contact/reload/vertical clock into the same canonical tick.
            step = frame_step
            integrated.add(state['id'])
            self._advance_bot_critical(state, step, now)
            if not state['alive']:
                self._cancel_active_burst(state)
                continue
            self._advance_bot_drowning(state, step)
            if not state['alive']:
                self._cancel_active_burst(state)
                continue
            self._advance_bot_overturn(state, step)
            if not state['alive']:
                self._cancel_active_burst(state)
                continue
            # Drowning and overturn are the last critical-state writers before
            # this Bot's ordinary decision/aim/fire work. Parse once only for
            # that stable portion of this authority tick.
            _cache_critical_parts_for_tick(state)
            tick_siege_yaw = _number(state.get('yaw'))
            siege_motion_locked = int(state.get(
                'siege_state', siege_mechanics.DISABLED)) in (
                    siege_mechanics.SWITCHING_ON,
                    siege_mechanics.SWITCHING_OFF)
            self._advance_bot_siege(state, step)
            position = _position(state)
            tick_poses[state['id']] = position
            tick_safe[state['id']] = prebaked_navigation.pose_is_safe(
                self.baked_graph, position, shoulder_cells=0)
            server_order = self._server_orders.get(state['id'])
            decide_with_order = getattr(self.adapter, 'decide_with_order', None)
            cache_key = (('server', self._server_order_tokens.get(
                              state['id'], 0))
                         if server_order is not None else ('local',))
            decision_cache = self._decision_cache.get(state['id'])
            decision_due = not (
                decision_cache is not None and
                decision_cache[0] == cache_key and
                _number(now) < decision_cache[1])
            decision_deadline = None
            raw_command = None
            planner_probe_samples = {}

            def planner_sample_direction(sample_yaw):
                # Invalid or unavailable baked data retains the mature native
                # planner path.  These advisory samples never enter the motion
                # cache and never authorize the finally selected direction.
                normalised = ((float(sample_yaw) + math.pi) %
                              (2.0 * math.pi) - math.pi)
                key = round(normalised, 4)
                if key not in planner_probe_samples:
                    planner_probe_samples[key] = self._probe_direction(
                        position, sample_yaw, state.get('speed', 0.0),
                        self._descriptors.get(state['id']))
                return planner_probe_samples[key]

            grid = getattr(self.navigator, 'grid', None)
            point_hazard = getattr(grid, 'point_has_baked_hazard', None)
            baked_shallow_escape = bool(
                callable(point_hazard) and point_hazard(
                    position, BAKED_SHALLOW_WATER))
            controlled_shallow = getattr(
                self.navigator, 'controlled_shallow_step', None)

            def sample_clear(sample_yaw):
                advisory = self._planner_corridor_clear(
                    position, sample_yaw, state.get('speed', 0.0),
                    wet_escape=(baked_shallow_escape or
                                _number(state.get('_water_depth'), -1.0) >
                                BOT_WATER_AVOID_DEPTH),
                    allow_shallow=(callable(controlled_shallow) and
                                   controlled_shallow(
                                       state['id'], position, sample_yaw)))
                if advisory is not None:
                    return bool(advisory)
                sample = planner_sample_direction(sample_yaw)
                # Exhausting the soft-static recast budget is not a wall. Keep
                # the previous drive intent; commit-side world collision still
                # stops the hull if this corridor reaches real hard geometry.
                if (sample is None or
                        (isinstance(sample, dict) and
                         sample.get('deferred', False))):
                    return True
                return self._probe_is_clear(sample)

            if not decision_due:
                if len(decision_cache) < 6:
                    raise RuntimeError(
                        'cached bot perception is unavailable')
                command = dict(decision_cache[3])
                contacts = decision_cache[4]
                targets = decision_cache[5]
            else:
                contacts, targets = self._contacts_for(
                    state, players, now, team_visibility, visibility_tick)
                if traffic_bodies is None:
                    traffic_bodies, traffic_index = self._traffic_snapshot(
                        neighbours)
                decision_step = step
                if decision_cache is not None:
                    decision_step = max(
                        step, _number(now) - decision_cache[2])
                detail_tier = self._detail_tier(state)
                decision_horizon = (
                    DECISION_SECONDS * DECISION_TIER_FACTOR[detail_tier])
                stopping_distance = None
                expected_mode = (
                    server_order.get('combat_mode', 'route')
                    if isinstance(server_order, dict) else None)
                physics_params = self._physics_params.get(state['id'])
                if (physics_params is not None and
                        abs(_number(state.get('speed'))) > 0.35 and
                        expected_mode not in ('route', 'advance')):
                    previous_command = (
                        decision_cache[3] if decision_cache is not None else {})
                    stopping_distance = self._cached_traffic_stopping_distance(
                        state, previous_command, physics_params)
                decision_state = {
                    'id': state['id'], 'position': position,
                    'yaw': state['yaw'],
                    'speed': abs(_number(state.get('speed'))),
                    'dt': decision_step, 'now': now,
                    'health': state['health'],
                    'max_health': state['max_health'],
                    'contacts': contacts,
                    'neighbours': self._neighbours_for(
                        state, neighbours, traffic_index, traffic_bodies),
                    'velocity': (
                        math.sin(_number(state.get('yaw'))) *
                        _number(state.get('speed')), 0.0,
                        math.cos(_number(state.get('yaw'))) *
                        _number(state.get('speed'))),
                    'half_length': _number(state.get('half_length'), 3.5),
                    'half_width': _number(state.get('half_width'), 1.7),
                    'stopping_distance': stopping_distance,
                    'decision_horizon': decision_horizon,
                }
                reposition_order, reposition_expired = \
                    self._friendly_reposition_order(state, targets, now)
                if (reposition_order is not None and
                        callable(decide_with_order)):
                    command = decide_with_order(
                        decision_state, reposition_order, sample_clear)
                elif server_order is not None and callable(decide_with_order):
                    server_order = dict(server_order)
                    if (server_order.get('target_kind') == 'human' and
                            server_order.get('target_id') is not None):
                        server_order['target_id'] = self._human_planner_id(
                            server_order.get('target_id'))
                    server_order = _overlay_live_target_pose(
                        server_order,
                        targets.get(server_order.get('target_id')),
                        position)
                    command = decide_with_order(
                        decision_state, server_order,
                        sample_clear)
                else:
                    command = self.adapter.decide(
                        decision_state, sample_clear)
                if reposition_expired:
                    # Resume the ordinary strategic movement on this frame,
                    # but do not immediately recreate the expired override by
                    # attempting the same blocked shot again.
                    command['fire_allowed'] = False
                bot_id = int(state['id'])
                self._decision_counts[bot_id] = self._decision_counts.get(
                    bot_id, 0) + 1
                decision_deadline = _cache_deadline(
                    now, state['id'],
                    DECISION_SECONDS *
                    DECISION_TIER_FACTOR[self._detail_tier(state)],
                    3, decision_cache is None)
                raw_command = dict(command)
            # Hull contact is already solved simultaneously after every Bot's
            # copied step. Friendly ramming no longer deals damage, so a second
            # predictive headway controller only makes slow vehicles coast and
            # accelerate in visible pulses. Preserve the planner's last valid
            # command until terrain/world collision gives a completed veto.
            command['throttle'] = max(
                -1.0, min(1.0, _number(command.get('throttle'))))
            if decision_due:
                self._decision_cache[state['id']] = (
                    cache_key, decision_deadline, _number(now), raw_command,
                    contacts, targets)
            # Preserve the old refresh point: in copied-physics mode, a later
            # bot observes poses integrated by earlier bots in this same tick.
            # Human records do not change inside update, so index them once at
            # the first live bot instead of rebuilding the map for every bot.
            if live_players is None:
                live_players = self._index_live_players(players)
            refresh_all_targets = bool(
                collect_observation or refresh_shot_lanes or
                collect_cover_jobs)
            active_contacts = (
                contacts if (collect_observation or refresh_shot_lanes)
                else ())
            live_targets = None
            if refresh_all_targets:
                live_targets = self._refresh_target_poses(
                    targets, live_players=live_players,
                    probe_targets=live_probe_targets,
                    processed_bot_ids=processed_bot_ids)
            lane_source = (_copy_runtime_state(state)
                           if active_contacts else None)
            for cached_target in active_contacts:
                observed_target = live_targets.get(
                    cached_target.get('id'), cached_target)
                lane_key = self._shot_los_key(state, observed_target)
                key = (int(state.get('team', 0)),
                       observed_target.get('kind'),
                       int(observed_target.get('network_id', 0)))
                if ('visible' not in observed_target or
                        not isinstance(observed_target['visible'], bool)):
                    raise ValueError(
                        'canonical contact visible flag is invalid')
                target_visible = bool(cached_target['visible'])
                direct_visible = bool(cached_target.get('direct_visible'))
                fresh_visible = bool(cached_target.get('fresh_visible'))
                team_visibility[key] = bool(
                    fresh_visible or team_visibility.get(key, False))
                if fresh_visible:
                    remembered = {
                        'position': _position(observed_target),
                        'x': _number(observed_target.get('x')),
                        'y': _number(observed_target.get('y')),
                        'z': _number(observed_target.get('z')),
                        'yaw': _number(observed_target.get('yaw')),
                        'speed': _number(observed_target.get('speed')),
                    }
                    self._visible_target_poses[key] = remembered
                    observed_target.update(remembered)
                if collect_observation:
                    entry = observation_entries.get(key)
                    if entry is None:
                        entry = [False, set(), observed_target, set(), set()]
                        observation_entries[key] = entry
                    entry[0] = bool(target_visible or entry[0])
                    entry[2] = observed_target
                    if direct_visible:
                        entry[4].add(int(state['id']))
                observation_pairs.append((
                    lane_source, observed_target, lane_key, key, [None]))
            target_id = command.get('target_id')
            if target_id in (targets or {}):
                # Aim/fire gating retains the observer-specific spotting flag.
                target = self._refresh_target_pose(
                    target_id, targets[target_id], live_players)
            else:
                target = None
            command = _overlay_live_target_pose(command, target, position)
            state['target_kind'] = (
                target.get('kind') if target is not None else None)
            state['target_id'] = (
                target.get('network_id') if target is not None else None)
            siege_motion_locked = bool(
                self._update_bot_siege_intent(
                    state, command, target, step) or
                siege_motion_locked)
            descriptor = self._descriptors.get(state['id'], {})
            profile = state.get('profile')
            profile = profile if isinstance(profile, dict) else {}
            gun_state = self._gun_states.get(state['id'])
            if gun_state is None:
                gun_state = _BotGunState(
                    descriptor, state.get('fire_seq', 0),
                    _critical_factor(
                        state, descriptor, 'dispersion'))
                self._gun_states[state['id']] = gun_state
            ammo_state = self._ammo_states.get(state['id'])
            if ammo_state is None:
                ammo_state = _BotAmmoState(descriptor, profile, state)
                self._ammo_states[state['id']] = ammo_state
            burst_state = self._burst_states.get(state['id'])
            if burst_state is None:
                burst_state = burst_mechanics.BurstClock()
                self._burst_states[state['id']] = burst_state
            reload_factor = _critical_factor(
                state, descriptor, 'reload')
            if not burst_state.active:
                gun_state.rescale_reload(reload_factor)
                gun_state.tick(step)
                reload_kind = gun_state.complete_reload(
                    reload_factor, ammo_state.planned_rounds())
                ammo_state.stage(
                    gun_state.shell_index(command.get('shell_index', 0)),
                    reload_kind is not None, reload_kind == 'full')
            ammo_state.publish(state)
            is_spg = str(profile.get('class_tag') or '') == 'SPG'
            pending_intent = None
            pending_reproof = None
            if is_spg:
                # Artillery proofs are keyed to the physically loaded round,
                # never the server's desired future selection.
                intent_shell = int(state['shell_index'])
                if not command.get('fire_allowed'):
                    self._cancel_artillery_intent(state['id'])
                else:
                    pending_intent = self._active_artillery_intent(
                        state, target, descriptor, intent_shell, now)
                    pending_reproof = self._active_artillery_reproof(
                        state, target, descriptor, intent_shell, now)
                if pending_intent is not None:
                    frozen = pending_intent['solution']
                    command['aim_position'] = frozen['aim_position']
                    command['face_position'] = frozen['aim_position']
                elif (pending_reproof is not None and
                      isinstance(pending_reproof.get('hold_solution'), dict)):
                    frozen = pending_reproof['hold_solution']
                    command['aim_position'] = frozen['aim_position']
                    command['face_position'] = frozen['aim_position']
                if pending_reproof is not None:
                    command['throttle'] = 0.0
                    command['turn'] = 0.0
                    command['movement_intent'] = False
            throttle = max(-1.0, min(1.0, _number(command['throttle'])))
            turn = max(-1.0, min(1.0, _number(command.get('turn'))))
            aim_fallback = (target.get('position') if target is not None
                            else _position(state))
            aim_position = _point(command.get('aim_position'), aim_fallback)
            aim_dx = aim_position[0] - _number(state.get('x'))
            aim_dz = aim_position[2] - _number(state.get('z'))
            aim_distance = math.sqrt(aim_dx * aim_dx + aim_dz * aim_dz)
            desired_aim_yaw = (
                math.atan2(aim_dx, aim_dz) if aim_distance > 0.1
                else _number(state.get('yaw')))
            gun_yaw_limits = self._gun_yaw_limits.get(state['id'])
            if gun_yaw_limits is None:
                gun_yaw_limits = ai_driver.gun_yaw_limits(descriptor)
                self._gun_yaw_limits[state['id']] = gun_yaw_limits
            minimum_yaw, maximum_yaw, unused_limited = gun_yaw_limits
            turn, throttle, hull_aiming = ai_driver.combat_hull_aim(
                state['yaw'], desired_aim_yaw, minimum_yaw, maximum_yaw,
                turn, throttle, command.get('recovery_mode', 'drive'),
                target is not None and
                command.get('combat_mode') != 'base_defense')
            state['hull_aiming'] = bool(hull_aiming)
            if siege_motion_locked:
                # Stock Siege transitions immobilize the hull for the whole
                # transition tick, including the publication which starts or
                # completes the state edge.  Clear both planner intent and the
                # copied-physics clocks before horizontal integration.
                command['fire_allowed'] = False
                command['throttle'] = 0.0
                command['turn'] = 0.0
                command['movement_intent'] = False
                throttle = 0.0
                turn = 0.0
                state['speed'] = 0.0
                state['movement_dir'] = 0
                state['rotation_dir'] = 0
                state['push_x'] = 0.0
                state['push_z'] = 0.0
                self._turn_speeds[state['id']] = 0.0
                siege_locked_poses[state['id']] = (
                    position[0], position[2], tick_siege_yaw)
            unused_devices, destroyed_devices, unused_crew, unused_yellow = (
                _critical_parts(state))
            if (state.get('_overturned', False) or
                    destroyed_devices.intersection((
                    'engineHealth', 'leftTrackHealth',
                    'rightTrackHealth'))):
                throttle = 0.0
                turn = 0.0
            elif abs(throttle) > 0.01:
                throttle *= _critical_factor(
                    state, descriptor, 'mobility')
            travel_sign = -1.0 if (
                throttle < 0.0 or
                (abs(throttle) <= 0.01 and
                 _number(state.get('speed')) < 0.0)) else 1.0
            travel_yaw = (state['yaw'] if travel_sign > 0.0
                          else state['yaw'] + math.pi)
            attempted_yaws[state['id']] = travel_yaw
            maximum_probe_distance = None
            move_position = command.get('move_position')
            navigation_grid = getattr(self.navigator, 'grid', None)
            if getattr(navigation_grid, 'prebaked', False):
                baked_cell_size = _number(
                    getattr(navigation_grid, 'cell_size',
                            (self.baked_graph or {}).get('cell_size', 1.0)),
                    1.0)
                reactive_horizon = max(
                    baked_cell_size,
                    abs(_number(state.get('speed'))) *
                    BAKED_MOTION_LOOKAHEAD_SECONDS)
            else:
                reactive_horizon = None
            if (travel_sign > 0.0 and reactive_horizon is not None and
                    move_position is not None and
                    command.get('movement_intent', True) and
                    command.get('recovery_mode', 'drive') in
                    ('drive', 'avoid')):
                # A local navigation point may deliberately stop just before a
                # wall and turn onto the next baked edge. Probing past that
                # point makes the wall after the corner block this safe edge.
                remaining = max(
                    0.5,
                    _distance(position, _point(move_position, position)) -
                    ai_driver.WAYPOINT_ARRIVAL_RADIUS)
                maximum_probe_distance = min(remaining, reactive_horizon)
            elif (travel_sign < 0.0 and reactive_horizon is not None and
                    command.get('recovery_mode') == 'reverse_turn'):
                # Recovery is intentionally a short backing manoeuvre. A wall
                # beyond that escape edge must not veto clear space at the rear.
                maximum_probe_distance = reactive_horizon
            cached_motion_probe = self._motion_probe_cache.get(state['id'])
            settled_motion = bool(
                abs(throttle) <= 0.01 and abs(turn) <= 0.01 and
                abs(_number(state.get('speed'))) <= 0.02 and
                state.get('grounded_once', False) and
                not state.get('airborne', False))
            if not ((not decision_due or self._motion_probe_covers_distance(
                    cached_motion_probe, maximum_probe_distance)) and
                    self._motion_probe_reusable(
                        cached_motion_probe, position, travel_yaw,
                        state.get('speed', 0.0), now, settled_motion, step)):
                # Planner ranking is intentionally unable to satisfy this
                # gate.  Every newly selected travel corridor receives its own
                # complete native generic probe before an exact receipt can be
                # reused or acquired.
                motion_probe = self._probe_direction(
                    position, travel_yaw, state.get('speed', 0.0), descriptor,
                    maximum_probe_distance)
                # Planner alternatives keep the mature six horizontal rays.
                # Only the finally selected, translating, non-turning travel
                # sample pays for the exact 3x3 receipt used by commit-side
                # motion.  Releasing the throttle does not stop an already
                # moving hull in this tick, so traffic coasting still needs
                # the same native receipt before its pose can advance.
                if (isinstance(motion_probe, dict) and
                        self._probe_is_clear(motion_probe) and
                        not motion_probe.get('deferred', False) and
                        abs(_number(motion_probe.get('slope'))) <= 0.01 and
                        (abs(throttle) > 0.01 or
                         abs(_number(state.get('speed'))) > 0.0001) and
                        abs(turn) <= 0.01 and
                        abs(_number(self._turn_speeds.get(
                            state['id'], 0.0))) <= 0.01 and
                        not state.get('airborne', False)):
                    motion_probe = dict(motion_probe)
                    receipt_speed = abs(_number(state.get('speed')))
                    if (throttle < 0.0 or
                            (abs(throttle) <= 0.01 and
                             _number(state.get('speed')) < 0.0)):
                        # Preserve reverse intent even when the copied hull is
                        # starting from exactly zero. ``-0.0 < 0`` is false
                        # and would select the forward hull extent.
                        receipt_speed = -max(receipt_speed, 0.000001)
                    receipt = self._contained_cached_world_receipt(
                        cached_motion_probe, position, travel_yaw,
                        receipt_speed, step)
                    if receipt is not None:
                        if self._world_receipt_refresh_due(
                                receipt, position, travel_yaw,
                                receipt_speed, step):
                            refreshed = self._probe_world_receipt(
                                state['id'], position, travel_yaw,
                                receipt_speed, descriptor, False,
                                maximum_probe_distance)
                            if refreshed is False:
                                # A completed exact probe found a new blocker;
                                # the older corridor may no longer grant motion.
                                motion_probe.update({
                                    'clear': False,
                                    'collision': True,
                                })
                            elif refreshed == 'deferred':
                                motion_probe['_world_receipt_pending'] = True
                            elif self._world_receipt_contains(
                                    refreshed, position, travel_yaw,
                                    receipt_speed, step):
                                receipt = refreshed
                        # Queue deferral or callback failure proves no new
                        # blocker. Keep the still-contained old corridor while
                        # its fair proactive refresh remains queued.
                        motion_probe['world_receipt'] = receipt
                    else:
                        receipt = self._probe_world_receipt(
                            state['id'], position, travel_yaw, receipt_speed,
                            descriptor, not isinstance(
                                ((cached_motion_probe or {}).get('result') or
                                 {}).get('world_receipt'), dict),
                            maximum_probe_distance)
                        if receipt == 'deferred':
                            # The complete generic native corridor already
                            # passed. Queueing its exact cache receipt is an
                            # optimisation delay, not a collision result.
                            motion_probe['_world_receipt_pending'] = True
                        elif receipt is False:
                            motion_probe.update({
                                'clear': False,
                                'collision': True,
                            })
                        elif isinstance(receipt, dict):
                            motion_probe['world_receipt'] = receipt
                if (motion_probe is not None and
                        not (isinstance(motion_probe, dict) and
                             motion_probe.get('deferred', False))):
                    receipt_pending = bool(
                        isinstance(motion_probe, dict) and
                        motion_probe.get('_world_receipt_pending', False))
                    self._motion_probe_cache[state['id']] = {
                        'result': motion_probe,
                        'position': position,
                        'yaw': travel_yaw,
                        'maximum_distance': maximum_probe_distance,
                        'deadline': (
                            _number(now)
                            if receipt_pending
                            else _motion_probe_deadline(
                                now, state['id'],
                                cached_motion_probe is None)),
                    }
                else:
                    old_result = ((cached_motion_probe or {}).get(
                        'result') or {})
                    if not isinstance(old_result.get(
                            'world_receipt'), dict):
                        self._motion_probe_cache.pop(state['id'], None)
            else:
                motion_probe = cached_motion_probe['result']
            probe_deferred = bool(
                isinstance(motion_probe, dict) and
                motion_probe.get('deferred', False))
            # The generic/world corridor looks beyond this simulation step.
            # When the exact resolver is available, only its current hull
            # sweep may turn that forecast into a realised hard contact.
            exact_motion_owns_collision = bool(
                callable(self.motion_resolver) and
                isinstance(motion_probe, dict) and
                not probe_deferred and
                motion_probe.get('collision', False) and
                not motion_probe.get('water', False) and
                abs(_number(motion_probe.get('slope', 0.0))) <= 0.55)
            path_clear = (True if (probe_deferred or motion_probe is None or
                                   exact_motion_owns_collision or
                                   (abs(throttle) <= 0.01 and
                                    abs(_number(state.get('speed'))) <=
                                    0.0001) or
                                   state.get('airborne', False)) else
                          self._probe_is_clear(motion_probe))
            if not path_clear:
                throttle = 0.0
                driver = getattr(self.adapter, 'driver', None)
                remember = getattr(driver, 'remember_failure', None)
                if callable(remember) and not probe_deferred:
                    remember(state['id'], travel_yaw)
                report_blocked = getattr(
                    self.navigator, 'report_blocked_step', None)
                # A hull contact escalates from its realised status below.
                if (callable(report_blocked) and not probe_deferred and
                        not (isinstance(motion_probe, dict) and
                             motion_probe.get('collision', False)) and
                        command.get('move_position') is not None):
                    report_blocked(state['id'], position,
                                   command.get('move_position'), now)
            steer_dir = 0
            if abs(turn) > 0.01:
                # LocalDriver already inverts reverse recovery steering for the
                # copied traverse law.  Re-deriving this sign from target_yaw
                # discards that command and recreates the stationary spin.
                steer_dir = 1 if turn > 0.0 else -1
            state['movement_dir'] = (
                1 if throttle > 0.01 else (-1 if throttle < -0.01 else 0))
            state['rotation_dir'] = steer_dir
            self._log_direction_flip(state, path_clear, motion_probe, now)
            if not self.native_motion:
                params = self._physics_params.get(state['id'])
                if params is None:
                    params = vehicle_physics.derive_params({})
                    self._physics_params[state['id']] = params
                # The selected corridor's ground sample is also the copied
                # physics slope.  A second native probe here used to double the
                # render-thread work for every moving bot.
                slope = (_number(motion_probe.get('slope'))
                         if isinstance(motion_probe, dict) else 0.0)
                # Direction probes follow travel_yaw, while copied physics and
                # stored pose pitch use the hull-forward axis. Convert reverse
                # probes once so signed speed stays correct while coasting and
                # when a ramp loses support.
                slope_pitch = travel_sign * -math.atan(slope)
                turn_speed = (0.0 if siege_motion_locked else
                    vehicle_physics.traverse_step(
                        params, self._turn_speeds.get(state['id'], 0.0),
                        turn, _number(state.get('speed')), step,
                        drive_intent=throttle))
                old_hull_yaw = _number(state.get('yaw'))
                candidate_hull_yaw = old_hull_yaw + turn_speed * step
                while candidate_hull_yaw > math.pi:
                    candidate_hull_yaw -= math.pi * 2.0
                while candidate_hull_yaw < -math.pi:
                    candidate_hull_yaw += math.pi * 2.0
                if not self._baked_pose_progress_clear(
                        state, position, old_hull_yaw,
                        position, candidate_hull_yaw):
                    # Turning is a pose change even without translation. Keep
                    # the prior legal OBB until the hull first moves far enough
                    # inward to rotate without crossing the official red line.
                    turn_speed = 0.0
                    candidate_hull_yaw = old_hull_yaw
                    state['rotation_dir'] = 0
                self._turn_speeds[state['id']] = turn_speed
                state['yaw'] = candidate_hull_yaw
                committed_travel_yaw = (
                    candidate_hull_yaw if travel_sign > 0.0 else
                    candidate_hull_yaw + math.pi)
                attempted_yaws[state['id']] = committed_travel_yaw
                committed_corridor = self._planner_corridor_clear(
                    position, committed_travel_yaw,
                    state.get('speed', 0.0),
                    wet_escape=(baked_shallow_escape or
                                _number(state.get('_water_depth'), -1.0) >
                                BOT_WATER_AVOID_DEPTH),
                    allow_shallow=(callable(controlled_shallow) and
                                   controlled_shallow(
                                       state['id'], position,
                                       committed_travel_yaw)),
                    hazard_only=True)
                if committed_corridor is False:
                    # Copied physics integrates the post-turn hull yaw, while
                    # the native motion receipt above proves the pre-turn
                    # corridor. Never let that difference enter an unplanned
                    # shallow-water cell; rotating toward a dry escape remains
                    # allowed and the next decision can choose a new route.
                    path_clear = False
                    throttle = 0.0
                    state['movement_dir'] = 0
                    self._invalidate_realised_motion(
                        state['id'], committed_travel_yaw)
                previous_speed = _number(state.get('speed'))
                speed = (0.0 if siege_motion_locked else
                    vehicle_physics.longitudinal_step(
                        params, previous_speed, throttle,
                        steer_dir != 0, slope_pitch, step,
                        bool(state.get('airborne', False)), 0, False))
                state['last_drive_pitch'] = slope_pitch
                hard_contact = False
                contact_position = position
                contact_deflected = False
                if not path_clear:
                    if (isinstance(motion_probe, dict) and
                          motion_probe.get('collision', False)):
                        hard_contact = True
                    else:
                        # Water, slope and other planner vetoes are not hull
                        # contacts. Keep their existing AI safety damping.
                        speed *= 0.2
                    state.pop('destructible_contact_speed', None)
                motion_status = 'clear'
                resolved_motion = False
                contact_speed = _number(state.get(
                    'destructible_contact_speed'), speed)
                contact_v0 = speed
                if (path_clear and abs(speed) > 0.0001 and
                        callable(self.motion_resolver)):
                    resolved_motion = True
                    if self._probe_clock is None:
                        motion_status = self.motion_resolver(
                            state['id'], position, state['yaw'], speed,
                            descriptor, step, now)
                    else:
                        probe_started = self._probe_started()
                        try:
                            motion_status = self.motion_resolver(
                                state['id'], position, state['yaw'], speed,
                                descriptor, step, now)
                        finally:
                            self._probe_finished(4, probe_started)
                    if motion_status not in (
                            'clear', 'crushed', 'soft', 'cap_crushed',
                            'hard'):
                        raise RuntimeError(
                            'bot motion resolver returned an invalid status')
                    if motion_status not in ('clear', 'crushed'):
                        path_clear = False
                        if motion_status == 'soft':
                            # Native contact has not cleared yet. Freeze the
                            # copied wheel speed at the real impact value; do
                            # not accelerate against a pose that did not move.
                            contact_speed = min(abs(contact_speed), abs(speed))
                            speed = (-contact_speed if speed < 0.0 else
                                     contact_speed)
                            state['destructible_contact_speed'] = speed
                        elif motion_status == 'cap_crushed':
                            # The directional cap is a stock-gate proof, not
                            # physical momentum.  The accepted item blocks this
                            # tick while the bot keeps its pre-step real speed.
                            speed = previous_speed
                            state.pop('destructible_contact_speed', None)
                        elif motion_status == 'hard':
                            self._invalidate_realised_motion(
                                state['id'], travel_yaw)
                            hard_contact = True
                            state.pop('destructible_contact_speed', None)
                    else:
                        state.pop('destructible_contact_speed', None)
                if (hard_contact and
                        not state.get('airborne', False)):
                    speed, contact_position, contact_deflected = \
                        self._hard_contact_response(
                            state, position, state['yaw'], speed,
                            descriptor, step, now)
                    report_contact = getattr(
                        self.navigator, 'report_blocked_step', None)
                    if (callable(report_contact) and
                            command.get('move_position') is not None):
                        report_contact(
                            state['id'], position,
                            command.get('move_position'), now)
                elif motion_status in ('soft', 'cap_crushed'):
                    self._hard_contact_grinds[state['id']] = 1
                if resolved_motion and callable(self.motion_report):
                    self.motion_report(
                        state['id'], motion_status, contact_v0, speed)
                state['speed'] = speed
                if state.get('siege_state') == siege_mechanics.ENABLED:
                    siege_limit = siege_mechanics.enabled_speed_limit(
                        state.get('vehicle', ''))
                    if siege_limit is not None:
                        speed = max(-siege_limit, min(siege_limit, speed))
                        state['speed'] = speed
                if contact_deflected:
                    state['x'], state['y'], state['z'] = contact_position
                elif path_clear:
                    state['x'] += math.sin(state['yaw']) * speed * step
                    state['z'] += math.cos(state['yaw']) * speed * step
                    if abs(speed) > 0.0001:
                        bot_id = int(state['id'])
                        self._hard_contact_grinds[bot_id] = max(
                            0, self._hard_contact_grinds.get(bot_id, 0) - 1)
            ammo_state.publish(state)
            ballistic_solution, local_action_fresh = \
                self._cadenced_ballistic_solution(
                    state, target, descriptor, state['shell_index'], now,
                    force=(burst_state.active or
                           pending_intent is not None or
                           pending_reproof is not None))
            command['_ballistic_solution'] = ballistic_solution
            previous_turret_yaw = _number(state.get('turret_yaw'))
            unused_desired_yaw, unused_horizontal = self._update_gun_aim(
                state, command, target, step)
            turret_speed = abs(_angle_delta(
                _number(state.get('turret_yaw')), previous_turret_yaw)) / max(
                    step, 1.0e-9)
            gun_state.tick_dispersion(
                step, abs(_number(state.get('speed'))),
                abs(_number(self._turn_speeds.get(state['id'], 0.0))),
                turret_speed,
                _critical_factor(state, descriptor, 'dispersion'),
                _critical_factor(state, descriptor, 'aim_time'))
            state['clip_size'] = gun_state.clip_size
            state['clip'] = gun_state.clip
            state['reload_time'] = gun_state.remaining(reload_factor)
            state['reload_duration'] = gun_state.duration(reload_factor)
            self._advance_active_burst(
                state, gun_state, ammo_state, reload_factor, descriptor,
                target, ballistic_solution, step, destroyed_devices,
                step_start_time_us, step_end_time_us)
            fire_range = max(0.0, _number(command.get('fire_range'), 0.0))
            target_distance = (_distance(_position(state), target['position'])
                               if target is not None else 0.0)
            in_range = (target is not None and target_distance > 1.0 and
                        ballistic_solution is not None and
                        (fire_range <= 0.0 or target_distance < fire_range))
            if (publish and local_action_fresh and
                    command['fire_allowed'] and target is not None and
                    in_range and
                    not state.get('_drowning', False) and
                    not state.get('_overturned', False) and
                    'gunHealth' not in destroyed_devices and
                    state.get('gun_aligned') and
                    not burst_state.active and
                    gun_state.ready(reload_factor) and
                    ammo_state.can_fire() and
                    (pending_intent is not None or
                     pending_reproof is not None or
                     self._shot_clear(
                        state, target, now,
                        probe_budget=shot_lane_budget))):
                launch_receipt = None
                launch_preview = None
                lane_clear = False
                lane_verdict = {}
                if is_spg:
                    launch_receipt = self._artillery_launch_receipt(
                        state, target, descriptor, state['shell_index'],
                        gun_state, ballistic_solution, now)
                    if launch_receipt is not None:
                        if self._probe_clock is None:
                            lane_value = self.artillery_friendly_lane_probe(
                                state, target, descriptor,
                                state['shell_index'], launch_receipt)
                        else:
                            probe_started = self._probe_started()
                            try:
                                lane_value = \
                                    self.artillery_friendly_lane_probe(
                                        state, target, descriptor,
                                        state['shell_index'], launch_receipt)
                            finally:
                                self._probe_finished(1, probe_started)
                        lane_clear, lane_verdict = \
                            self._friendly_lane_verdict(lane_value)
                else:
                    launch_preview = self._direct_launch_preview(
                        state, descriptor, state['shell_index'], gun_state,
                        ballistic_solution)
                    if launch_preview is not None:
                        if self._probe_clock is None:
                            lane_value = self.friendly_lane_probe(
                                state, target, descriptor,
                                state['shell_index'], launch_preview)
                        else:
                            probe_started = self._probe_started()
                            try:
                                lane_value = self.friendly_lane_probe(
                                    state, target, descriptor,
                                    state['shell_index'], launch_preview)
                            finally:
                                self._probe_finished(1, probe_started)
                        lane_clear, lane_verdict = \
                            self._friendly_lane_verdict(lane_value)
                launch = launch_receipt if is_spg else launch_preview
                if launch is not None and lane_clear:
                    self._clear_friendly_reposition(state['id'])
                    fired = self._fire(
                        state, gun_state, reload_factor, descriptor,
                        launch_receipt=launch_receipt,
                        ammo_state=ammo_state,
                        launch_preview=launch_preview,
                        launch_time_us=step_end_time_us)
                    if fired and is_spg:
                        self._cancel_artillery_intent(state['id'])
                elif launch is not None:
                    self._mark_friendly_reposition(
                        state, command, target, launch, lane_verdict, now)
                    if is_spg:
                        # The proved path belongs to the old muzzle pose. A
                        # moving SPG must discard it and prove the next shot
                        # again after its normal safe-driver motion completes.
                        self._cancel_artillery_intent(state['id'])
            mode = command.get('combat_mode')
            if (collect_cover_jobs and
                    target is not None and target.get('visible') and
                    callable(self.cover_probe) and
                    (mode in ('take_cover', 'cover_hold', 'cover_peek',
                              'cover_return', 'under_fire_withdraw',
                              'low_health_retreat', 'crossfire_withdraw') or
                     (command.get('fire_allowed') and mode in (
                         'engage', 'advance_contact', 'jiggle_forward',
                         'jiggle_back')))):
                cover_jobs.append((state['id'], _copy_runtime_state(state),
                                   dict(target),
                                   command.get('move_position', position)))
            _clear_critical_parts_tick_cache(state)
            processed_bot_ids.add(int(state['id']))
        # A static firing lane is only meaningful after at least one member of
        # the observing team has spotted the target.  The server ignores the
        # shooter set for an unspotted contact, so probing all 14x15 enemy
        # pairs in that state spent hundreds of render-thread collision rays
        # without changing a decision.  Aggregate radio spotting first, then
        # prove every shooter lane for only those team-visible targets.  The
        # selected target's independent final-fire gate below remains live.
        for (lane_source, observed_target, lane_key, key,
             lane_distance) in observation_pairs:
            if not team_visibility.get(key, False):
                continue
            if refresh_shot_lanes:
                if (self._shot_los_deadlines.get(lane_key) !=
                        shot_lane_refresh_time):
                    self._refresh_shot_clear(
                        lane_source, observed_target, now,
                        shot_lane_refresh_time, shot_lane_budget,
                        lane_key=lane_key,
                        distance_cache=lane_distance)
                if (self._shot_los_deadlines.get(lane_key) !=
                        shot_lane_refresh_time):
                    shot_lanes_ready = False
            if collect_observation:
                # Report the latest bounded tactical sample without waiting
                # for the current one-second roster refresh. Missing or old
                # samples mean "not shootable"; the independent 0.20-second
                # final-fire gate remains the only launch authorization.
                lane_sample = self._shot_los_cache.get(lane_key)
                if (lane_sample is not None and
                        now - lane_sample[0] <=
                        SHOT_LANE_REFRESH_SECONDS +
                        self._control_seconds + 1e-9 and lane_sample[1]):
                    observation_entries[key][1].add(
                        int(lane_source['id']))
        if (shot_lane_refresh_due and refresh_shot_lanes and
                shot_lanes_ready):
            self._next_shot_lane_refresh = (
                _number(now) + SHOT_LANE_REFRESH_SECONDS)
        completed_affordances = ()
        if collect_observation:
            completed_affordances = tuple(self._cover_results)
            self._cover_results = []
        cover_jobs.sort(key=lambda value: value[0])
        if collect_cover_jobs:
            self._next_cover_refresh = _number(now) + COVER_REFRESH_SECONDS
        if collect_cover_jobs and cover_jobs:
            cursor = self._cover_cursor % len(cover_jobs)
            ordered = cover_jobs[cursor:] + cover_jobs[:cursor]
            count = min(COVER_JOBS_PER_OBSERVATION, len(ordered))
            self._cover_cursor = (cursor + count) % len(cover_jobs)
            ally_positions = dict((team, [
                _position(value) for value in self.states.values()
                if value.get('alive') and value.get('team') == team])
                for team in (1, 2))
            window_start = _number(now)
            for index, value in enumerate(ordered[:count]):
                bot_id, source, target, route = value
                ready_at = window_start + (
                    COVER_JOB_WINDOW_SECONDS * float(index) /
                    float(count))
                self._cover_queue.append((
                    ready_at, bot_id, source, target, route,
                    tuple(ally_positions.get(source.get('team'), ()))))
        if self._cover_queue:
            ready_at, bot_id, source, target, route, allies = \
                self._cover_queue[0]
            if now + 1e-9 >= ready_at:
                del self._cover_queue[0]
                try:
                    self._probe_totals[2] += 1
                    probe_started = self._probe_started()
                    try:
                        candidates = self.cover_probe(
                            source, target, route, allies,
                            (self.navigator.grid.segment_clear
                             if self.navigator is not None else None))
                    finally:
                        self._probe_finished(2, probe_started)
                except Exception:
                    candidates = ()
                if candidates:
                    self._cover_results.append({
                        'bot_id': int(bot_id),
                        'target_id': int(target.get('network_id')),
                        'target_kind': target.get('kind', 'human'),
                        'candidates': list(candidates),
                    })
        self._pending_ram_reports.extend(
            self._resolve_tank_contacts(players, now, frame_step))
        for bot_id, locked_pose in siege_locked_poses.items():
            state = self.states.get(bot_id)
            if state is None:
                continue
            state['x'], state['z'], state['yaw'] = locked_pose
            state['speed'] = 0.0
            state['movement_dir'] = 0
            state['rotation_dir'] = 0
            state['push_x'] = 0.0
            state['push_z'] = 0.0
            self._turn_speeds[bot_id] = 0.0
        ordered_states = self._ordered_states()
        slope_candidates = []
        for state in ordered_states:
            if state.get('alive', True) and state['id'] in integrated:
                attempted_yaw = attempted_yaws.get(
                    state['id'], state.get('yaw', 0.0))
                support_blocked = self._update_vertical_motion(
                    state, frame_step,
                    tick_poses[state['id']], attempted_yaw)
                if not support_blocked:
                    self._guard_realised_pose(
                        state, tick_poses[state['id']], tick_safe[state['id']],
                        attempted_yaw)
                slope_candidates.append(state)
        self._alive_bot_ticks += len(slope_candidates)
        if slope_candidates:
            start = self._slope_pose_cursor % len(slope_candidates)
            visited = 0
            sampled = 0
            while (visited < len(slope_candidates) and
                   sampled < MAX_SLOPE_POSE_SAMPLES_PER_FRAME):
                index = (start + visited) % len(slope_candidates)
                if self._update_slope_pose(slope_candidates[index]):
                    sampled += 1
                visited += 1
            self._slope_pose_cursor = (
                start + max(1, visited)) % len(slope_candidates)
        for state in ordered_states:
            if publish:
                self._mark_combat_publication(state)
        if not publish:
            return []
        wire_states = []
        launches = [dict(launch) for launch in self._pending_launches]
        for state in self._ordered_states():
            burst_state = self._burst_states.get(int(state['id']))
            if burst_state is not None:
                burst_state.publish(state)
            self._publish_equipment_state(state)
            projected = lan_client.project_bot_state(state)
            if projected is None:
                raise RuntimeError('bot publication projection failed')
            wire_states.append(projected)
        self._sample_time_us = step_end_time_us
        publication = {
            'type': 'bot_state', 'bots': wire_states,
            'sample_time_us': self._sample_time_us,
        }
        if launches:
            # Never put these local-only SPG proof receipts on the LAN wire.
            # BattleRuntime retries an unaccepted launch from this compact list.
            publication['launches'] = launches
        outgoing = [publication]
        # The server validates ram proximity against its latest authority pose.
        # Publish state first, then the cooldown-gated damage reports.
        outgoing.extend(self._pending_ram_reports)
        self._pending_ram_reports = []
        if collect_observation:
            self._next_observation = _number(now) + OBSERVATION_SECONDS
            outgoing.append({
                'type': 'bot_observation',
                'contacts': self._pack_observations(
                    observation_entries, now),
                'affordances': list(completed_affordances),
            })
        return outgoing

    def presentation_states(self, now=None):
        """Return current authority poses without forming a LAN proposal.

        Poses are published exactly as integrated.  Smoothing between two
        accepted poses belongs to the compound's own MatrixAnimation, which
        INTERPOLATES; extrapolating here as well would guess ahead and then
        correct itself, and that correction is what reads as a jump.
        """
        if not self.is_authority() or self.adapter is None or self.finished:
            return ()
        return tuple(_copy_runtime_state(state)
                     for state in self._ordered_states())
