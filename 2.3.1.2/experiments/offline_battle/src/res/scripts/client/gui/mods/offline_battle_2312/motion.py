"""Vehicle motion law, copied from the mature 0.9.22 offline port.

The native WGTankPhysics never simulates for a client-created Vehicle on
2.3.1.2: the body is not attached to a physics world, so movement signals
and track contacts stay at zero. Motion therefore has to be owned here.

Every formula and constant below is the mature port's
`vehicle_physics.py`, unchanged apart from indentation. Parameters come
from the real 2.3.1.2 vehicle descriptor, whose `physics` dict and
`chassis.rotationSpeed` carry the same fields and units.
"""
from __future__ import absolute_import

import math

G = 9.81
GRAVITY_FACTOR = 1.25          # WG: tanks live under 1.25 g 'arcade gravity'
GRAVITY = G * GRAVITY_FACTOR   # 12.26 m/s^2 - falls, slopes, all force laws
COHESION = 1.3                 # WG: physics.brakeFriction (brake/hold grip)
# Drive (pulling) traction coefficient - track-vs-ground grip for CLIMBING.
# Lower than the brake COHESION on purpose: a tank locks and holds a slope it
# cannot power up.
#
# CALIBRATION (do not 'simplify' this to atan(DRIVE_TRACTION)): the drive is cut
# when  DRIVE_TRACTION*g*cos(t) < g*sin(t) + rr,  so the EFFECTIVE limit is
#     tan(t_max) = DRIVE_TRACTION - rr/(g*cos t)
# and rolling resistance is NOT negligible: rr = specificFriction*GRAVITY_FACTOR
# = 0.6867*1.25 = 0.86 m/s^2 on firm ground, i.e. it eats ~3.7 deg. Measured:
#     0.43 -> 19.6 deg     0.47 -> 21.5 deg     0.54 -> 24.8 deg
# WG's own threshold is physics_shared.RISE_FRICTION_Y = 0.906 = cos(25.04 deg)
# (below that normal.y it applies RISE_FRICTION = 50.0, i.e. a wall), and 0.8.2
# chassis maxClimbAngle is 25 deg. So 0.54 reproduces the stock climb ceiling;
# lower values refuse slopes the map designers built to be drivable (Dragon Ridge
# became near-unplayable at 0.43 = 19.6 deg). Raise drive_traction in config to
# climb steeper.
DRIVE_TRACTION = 0.54
# Track-slip drag when rolling UP a grade past SLIP_THRESHOLD_TAN: extra
# deceleration = SLIP_DRAG * (tan(grade) - SLIP_THRESHOLD_TAN) * g. Bleeds the
# 'coast up a mountain on momentum' - real tracks slip and stop dead.
#
# The threshold must sit just above the EFFECTIVE climb limit (see DRIVE_TRACTION),
# NOT above the naive atan(DRIVE_TRACTION). Any gap between the two is a window
# where the drive is already cut but nothing bleeds speed - free momentum coasting.
# That window, not a high climb ceiling, was what let a tank rush the Serene Coast
# mountain: 0.47/0.50 left 21.5..26.6 deg (5.0 deg) wide open. 0.54/0.48 leaves
# 24.8..25.6 deg = 0.8 deg, so momentum can no longer cheat a slope the engine
# refuses.
# SLIP_DRAG shapes the ramp above the threshold. 20.0 was a cliff (20 m/s^2 by
# 28 deg) that killed every partial climb; 10.0 stays progressive - ~0.9 m/s^2 at
# 26 deg (a near-limit slope still feels dynamic), 11.9 at 30 deg, 27 at 35 deg.
SLIP_THRESHOLD_TAN = 0.48   # 25.6 deg: just above the 24.8 deg effective climb limit
SLIP_DRAG = 10.0
FORWARD_FRICTION = 0.07        # WG: physics.forwardFriction (rolling)
# WG scales enginePower by GRAVITY_FACTOR_SCALED (0.00125) against masses in
# tons (WEIGHT_SCALE 0.001): net effect, drive power acts 1.25x its SI value.
POWER_FACTOR = GRAVITY_FACTOR
BKWD_POWER_FRACTION = 1.0      # WG: reverse gets FULL engine power in 0.8.2
ANG_ACCELERATION_TIME = 0.05   # WG: hull traverse reaches full rate in 50 ms
SPEED_AFFECT_ROT_DECREASE = 0.0  # WG: driving speed does NOT slow the traverse
ROTATION_POWER_FRACTION = 0.85   # WG: rotation may use 85% of engine power
# WG slope-grip laws (physics_shared): track cohesion decays with the ground
# normal - cubically below normal.y 0.969 (~14 deg), an extra 0.25 beyond
# normal.y 0.72 (~44 deg), floored at 0.5. Per vehicle, chassis maxClimbAngle
# (25 deg across 0.8.2 -> minPlaneNormalY = cos = 0.906) is the HARD climb
# limit: steeper ground gives the engine no traction at all.
COH_DECAY_Y = 0.969
COH_DECAY_FACTOR = 5.78
COH_DECAY_POW = 3.0
COH_DECAY_BOUND = 0.5
SLOPE_COH_DECAY = 0.25
SLOPE_COH_DECAY_Y = 0.72
# ---- offline-model constants (no exact native transition curve recoverable) ----
# Exact #1513 exposes per-vehicle mass, speed and terrain resistance plus the
# common WGVehiclePhysics brake/damping configuration, but the W-release curve
# itself lives in native code.  Use a conservative share of the recovered track
# grip: 0.65 shortens a Type 62 flat-road 60 km/h stop by about 15% versus the
# former 0.55 calibration without pretending that neutral coast is a full track
# lock.  A real downhill grade progressively unloads this drag.  Above the
# static perch tangent only rolling resistance remains, so gravity can carry a
# tank down a steep continuous slope without making flat roads frictionless.
COAST_BRAKE_SHARE = 0.65
# Steering adds track-differential drag to the rolling resistance.
STEER_RESIST_MULT = 1.6
# Engine force F = P / max(|v|, ENGINE_MIN_V), capped by track cohesion.
ENGINE_MIN_V = 1.5
# Track scroll must stay strictly below fashion.maxMovement or the native
# scroll wraps to zero at exactly top speed.
SCROLL_CAP = 0.995
# Fall damage: free below this impact speed (~4 m drop under 1.25 g), then
# linear in the excess. 10 m fall ~ 17% HP, 20 m ~ 38%.
FALL_SAFE_SPEED = 10.0
FALL_DMG_PER_MS = 0.03
# Ramming hurts beyond this closing speed (m/s).
RAM_SAFE_SPEED = 3.5
# Downhill slide on a slope the tracks cannot hold. The slide accelerates by the
# grip-excess g*(sin-coh*cos) but a track drag SLIDE_DRAG*v pulls it to a natural,
# terrain-dependent TERMINAL speed instead of ramping to a flat cap. SLIDE_MAX is
# just the hard safety ceiling for near-vertical faces.
SLIDE_MAX = 7.0
SLIDE_DRAG = 1.5
# Static hold limit (slope tangent) for a stationary hull, sideways AND fore/aft:
# the tracks perch only up to ~atan(SLIDE_HOLD_TAN); steeper ground slides off.
# Gentler than the brake COHESION (~52 deg) so a tank cannot cling to steep
# banks/cliffs. Deceleration (braking/coast) still uses the full COHESION grip.
SLIDE_HOLD_TAN = 0.50   # 26.6 deg static perch - must exceed the 24.8 deg climb limit, else a hull slides off the very slope it just drove up
# Kinetic (slipping) track drag while the hull slides BACK down a grade it could
# not climb - lower than the static hold so it does not hang mid-slope; it bleeds
# down to the foot at a controlled speed. Lower = slides faster/further.
SLIDE_KINETIC = 0.45
# Gravity overspeed: a steep descent / fall may carry the hull up to this
# multiple of its spec top speed, temporarily; OVERSPEED_DAMP (m/s^2) bleeds
# the surplus back to spec once the ground flattens.
OVERSPEED_MAX_FACTOR = 1.05   # gravity overspeed on a descent caps at 105% of spec
OVERSPEED_DAMP = 2.0
# Descent overspeed BUILDS UP gradually (m/s of surplus per sec, scaled by sin(grade))
# instead of snapping to the cap - the longer/steeper the descent, the more speed.
# ISOLATED to the overspeed clamp (>spec, descending only): does NOT touch the climb
# limit, slide-back, momentum-kill or flat driving. Higher = builds faster.
OVERSPEED_BUILD = 0.20


# ---- Live tuning: config.json "physics_tuning" can override these WITHOUT a
# recompile (only a client restart). Key -> module global. Values you would
# dial to match original WoT feel. ----


_DEFAULTS = {
    'mass': 5730.0,
    'powerW': 45.0 * 735.49875,
    'speedFwd': 32.0 / 3.6,
    'speedBwd': 12.0 / 3.6,
    'rotSpd': math.radians(38.0),
    'terrainResist': (1.1, 1.4, 2.6),
    'specificFriction': 0.6867,
    'brakeDecel': COHESION * GRAVITY,
    'trackCenter': 1.5,
    'minPlaneNormalY': math.cos(math.radians(25.0)),
}


def derive_params(td):
    '''Real per-vehicle parameter set from a VehicleDescr. Every consumer
    (player tick, each bot) MUST source its numbers from here - this is the
    single place that knows the units of td.physics.'''
    p = dict(_DEFAULTS)
    try:
        tdp = getattr(td, 'physics', None) or {}
    except Exception:
        tdp = {}
    try:
        if 'weight' in tdp:
            p['mass'] = float(tdp['weight'])
        if 'enginePower' in tdp:
            p['powerW'] = float(tdp['enginePower'])
        if 'speedLimits' in tdp:
            p['speedFwd'] = float(tdp['speedLimits'][0])
            p['speedBwd'] = float(tdp['speedLimits'][1])
        if 'terrainResistance' in tdp:
            tr = tdp['terrainResistance']
            p['terrainResist'] = (float(tr[0]), float(tr[1]), float(tr[2]))
        if 'specificFriction' in tdp:
            p['specificFriction'] = float(tdp['specificFriction'])
        if 'brakeForce' in tdp:
            # Exact #1513 initializes this legacy field to zero and never reads
            # the similarly named chassis XML value.  Zero means unavailable, not
            # a frictionless brake; retain the recovered track-grip fallback.
            raw_brake = float(tdp['brakeForce'])
            if raw_brake > 0.0:
                p['brakeDecel'] = min(
                    COHESION * GRAVITY,
                    raw_brake / max(p['mass'], 1.0))
        if 'trackCenterOffset' in tdp:
            tc = abs(float(tdp['trackCenterOffset']))
            if 0.3 <= tc <= 3.0:
                p['trackCenter'] = tc
        if 'minPlaneNormalY' in tdp:
            # = cos(chassis maxClimbAngle); the vehicle's hard climb limit
            p['minPlaneNormalY'] = float(tdp['minPlaneNormalY'])
    except Exception:
        pass
    try:
        ch = getattr(td, 'chassis', None)
        if ch is not None:
            raw_value = (ch.get('rotationSpeed') if isinstance(ch, dict)
                         else getattr(ch, 'rotationSpeed', None))
            if raw_value is None:
                raise RuntimeError(
                    '#1513 chassis rotation speed is unavailable')
            raw = float(raw_value)
            # reader stores radians; tolerate a raw-degrees dump
            p['rotSpd'] = math.radians(raw) if raw > 6.3 else raw
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(
            '#1513 chassis rotation speed is invalid: %s' % error)
    return p


def engine_force(p, v, throttle, slope_pitch=0.0):
    '''F = P_eff / max(|v|, v_min), TRACTION-capped. P_eff = powerW x
    POWER_FACTOR (WG scales engine power by GRAVITY_FACTOR). Reverse gets
    BKWD_POWER_FRACTION (1.0 in 0.8.2 = full power).

    Drive force can never exceed the DRIVE TRACTION on the current slope:
    fmax = DRIVE_TRACTION * m * g * cos(theta) = drive traction x normal force.
    DRIVE_TRACTION (0.54) is the track-vs-ground grip for PULLING, distinct
    from and lower than the brake/hold COHESION (1.3): a tank can lock its
    tracks and hold a slope it cannot power UP. This makes the climb limit
    emergent AND realistic (~atan(0.54) ~= 28 deg before rolling resistance):
    steeper than the resulting roughly 25-degree practical limit the
    grip-limited drive can't beat m*g*sin(theta), the hull stalls - it does NOT
    climb 40-50 deg walls the way a cohesion(1.3) cap wrongly allowed.'''
    if throttle == 0:
        return 0.0
    pw = p['powerW'] * POWER_FACTOR
    if throttle < 0:
        pw *= BKWD_POWER_FRACTION
    f = pw / max(abs(v), ENGINE_MIN_V)
    ny = math.cos(slope_pitch)
    fmax = DRIVE_TRACTION * p['mass'] * GRAVITY * (ny if ny > 0.1 else 0.1)
    if f > fmax:
        f = fmax
    # The climb LIMIT is not a hard gate here (that oscillated at the boundary); it
    # lives in longitudinal_step as an rr-aware limit that cuts the drive on a grade
    # the tracks cannot pull up. engine_force stays purely power/traction limited.
    return f * throttle


def rolling_resist_force(p, terrainIdx=0, steering=False):
    '''Rolling drag = mu * N. The descriptor bakes mu*9.81 into
    specificFriction; the WG sim applies mu against the 1.25 g normal force,
    so the stored value gets the GRAVITY_FACTOR back. terrainResistance
    scales it; steering adds track-differential drag.'''
    f = p['mass'] * p['specificFriction'] * GRAVITY_FACTOR * p['terrainResist'][terrainIdx]
    if steering:
        f *= STEER_RESIST_MULT
    return f


def brake_force(p, active, terrainIdx=0, slope_pitch=0.0):
    '''Braking is GRIP-limited, like drive traction: the locked tracks can
    only hold cohesion x normal force, and the normal force shrinks with slope
    (cos theta) while cohesion decays on steep ground. So a hull braking on a
    slope past the grip limit CANNOT hold and slides - the same ~50 deg limit
    as the lateral fall-line slip, kept consistent on purpose.
    active=True: opposite-throttle / hold lock-up. active=False: the established
    flat-ground drivetrain coast drag; longitudinal_step relieves that drag only
    when gravity is carrying the hull downhill.'''
    ny = math.cos(slope_pitch)
    grip_decel = slope_cohesion(ny) * GRAVITY * (ny if ny > 0.1 else 0.1)
    brake = p['brakeDecel'] if p['brakeDecel'] < grip_decel else grip_decel
    if active:
        return p['mass'] * brake
    return (rolling_resist_force(p, terrainIdx, False) +
        p['mass'] * COAST_BRAKE_SHARE * brake)


def slope_cohesion(ny):
    '''Effective track cohesion for a ground normal.y (WG physics_shared slope
    decay): full COHESION until normal.y drops below COH_DECAY_Y (~14 deg), then a
    cubic decay COH_DECAY_FACTOR*(COH_DECAY_Y-ny)**COH_DECAY_POW, plus an extra
    SLOPE_COH_DECAY step below SLOPE_COH_DECAY_Y (~44 deg), floored at
    COH_DECAY_BOUND. The cubic term smooths the old single 44 deg grip cliff so
    grip falls off gradually on steepening ground, as WG does.'''
    coh = COHESION
    if ny < COH_DECAY_Y:
        coh -= COH_DECAY_FACTOR * (COH_DECAY_Y - ny) ** COH_DECAY_POW
    if ny < SLOPE_COH_DECAY_Y:
        coh -= SLOPE_COH_DECAY
    return coh if coh > COH_DECAY_BOUND else COH_DECAY_BOUND


def _grip_decel(p, slope_pitch):
    '''Max track hold as a deceleration (m/s^2): cohesion x normal-force,
    both shrinking with slope. This is the single grip limit that caps drive
    traction, braking AND the static parked hold, so climb/brake/slide all
    share one consistent ~50 deg threshold.'''
    ny = math.cos(slope_pitch)
    return slope_cohesion(ny) * GRAVITY * (ny if ny > 0.1 else 0.1)


def longitudinal_step(p, v, throttle, steering, slope_pitch, dt,
                      airborne=False, terrainIdx=0, handbrake=False):
    '''One integration step of forward (along-hull) speed. Returns the new v.
    slope_pitch: fore/aft ground pitch (BigWorld: nose-up negative).

    Gravity along the slope ALWAYS acts when grounded (even parked at v=0), so
    a hull pointed up/down a slope too steep for its tracks slides off from a
    standstill - the fix for "stuck where it should slide". The tracks resist
    up to the grip limit (_grip_decel): below it they hold, past it the excess
    drives the slide. The climb limit is emergent (grip-capped engine force vs
    slope gravity), so there is no dead can-neither-climb-nor-slide band.'''
    if airborne:
        return v  # no track grip in the air; horizontal momentum kept as-is

    grav_a = GRAVITY * math.sin(slope_pitch)      # signed accel along hull (+fwd downhill)
    if handbrake and not airborne:
        # Locked tracks: full grip opposes any motion, and at a standstill the hull
        # holds unless the slope beats the tracks outright. Grip-limited like every
        # other brake here, so a cliff still wins - it is a parking brake, not glue.
        _hb_grip = _grip_decel(p, slope_pitch)
        if abs(v) < 0.05:
            return 0.0 if abs(grav_a) <= _hb_grip else v + (grav_a - (_hb_grip if grav_a > 0.0 else -_hb_grip)) * dt
        _hb_a = grav_a - (_hb_grip if v > 0.0 else -_hb_grip)
        _hb_nv = v + _hb_a * dt
        if (v > 0.0) != (_hb_nv > 0.0):
            return 0.0            # braked through zero: stop, do not crawl backwards
        return _hb_nv
    grip = _grip_decel(p, slope_pitch)            # max track hold, m/s^2
    rr = rolling_resist_force(p, terrainIdx, steering) / p['mass']

    if throttle != 0:
        # Drive: engine force (power/traction limited) + slope gravity.
        _ef = engine_force(p, v, throttle, slope_pitch) / p['mass']
        # TRUE rolling-drag-aware climb limit: powering INTO a grade the pulling tracks
        # cannot overcome (peak drive accel < gravity-along + rolling drag). There the
        # drive is CUT and the hold dropped to the slide limit, so the hull slides BACK
        # instead of the engine pinning it just under a fixed gate (the 'stuck at the
        # foot of a descent' bug). Replaces the hard minPlaneNormalY gate the hull used
        # to oscillate across.
        _ny_c = math.cos(slope_pitch)
        _max_climb = DRIVE_TRACTION * GRAVITY * (_ny_c if _ny_c > 0.1 else 0.1)
        _cant_climb = throttle * grav_a < 0.0 and _max_climb < abs(grav_a) + rr
        if _cant_climb:
            _ef = 0.0                                  # can't power up -> cut drive; momentum does not drive the hull up a too-steep grade
        accel = _ef + grav_a
        # Rolling drag ramped smoothly through v=0 - a hard abs(v)>0.01 threshold made
        # the hull judder / stick in a limit cycle wherever engine and gravity nearly
        # balanced (e.g. at the foot of a climb).
        _rrf = v / 0.08
        if _rrf > 1.0:
            _rrf = 1.0
        elif _rrf < -1.0:
            _rrf = -1.0
        accel -= rr * _rrf
        # (track-slip drag now runs for BOTH throttle states - see below)
        if _cant_climb:
            # Can't climb -> tracks slip; a LOW kinetic drag opposes the involuntary
            # slide-back so the hull bleeds down the grade at a controlled speed and does
            # NOT hang mid-slope. It is NOT held (no auto-brake) - it slides to the foot
            # where the grade becomes climbable again, carrying momentum onto the flat.
            if abs(v) > 0.05:
                _kin = SLIDE_KINETIC * GRAVITY * (_ny_c if _ny_c > 0.1 else 0.1)
                accel += _kin if v < 0.0 else -_kin
        else:
            # Auto-brake: intentional reverse, CLAMPED so grip never overshoots v past 0
            # in one tick (the raw +/-grip impulse limit-cycled ~1 km/h around v=0).
            if (throttle > 0 and v < -0.1) or (throttle < 0 and v > 0.1):
                _need = -v / dt - accel
                accel += _need if abs(_need) < grip else (grip if _need > 0.0 else -grip)
    else:
        # Parked / coasting: static grip tries to hold against slope gravity.
        if abs(v) < 0.02:
            _ny_h = math.cos(slope_pitch)
            _hold = SLIDE_HOLD_TAN * GRAVITY * (_ny_h if _ny_h > 0.1 else 0.1)   # static perch limit ~27 deg
            if abs(grav_a) <= _hold:
                return 0.0                        # tracks hold - no creep on ordinary hills
            accel = grav_a - (_hold if grav_a > 0.0 else -_hold)   # slides off a too-steep parked slope
        else:
            # Preserve the established flat-road settling distance, but unload the
            # drivetrain drag as the current direction points downhill.  The ratio is
            # direction-aware: coasting uphill receives no relief.  At/past the static
            # perch tangent gravity owns the descent and only rolling resistance remains.
            motion_sign = 1.0 if v > 0.0 else -1.0
            downhill_tangent = max(
                0.0, math.tan(slope_pitch) * motion_sign)
            downhill_relief = min(
                1.0, downhill_tangent / SLIDE_HOLD_TAN) ** 0.4
            coast_share = COAST_BRAKE_SHARE * (1.0 - downhill_relief)
            resist = rr + coast_share * grip
            accel = grav_a - (resist if v > 0.0 else -resist)

    # TRACK-SLIP DRAG: rolling UP a grade steeper than the tracks can pull, they
    # slip and momentum bleeds far faster than gravity alone would take it.
    # This used to sit inside the throttle != 0 branch only, so releasing the
    # throttle removed it entirely: build speed on the flat, let go, and coast
    # straight up a slope the engine flatly refuses. It must apply whenever the
    # hull is moving INTO the grade, powered or not.
    _grade = -slope_pitch if v > 0.0 else slope_pitch   # >0 = moving uphill
    if not airborne and abs(v) > 0.5 and _grade > 0.0:
        _tan = math.tan(_grade)
        if _tan > SLIP_THRESHOLD_TAN:
            _slip = SLIP_DRAG * (_tan - SLIP_THRESHOLD_TAN) * GRAVITY
            accel -= _slip if v > 0.0 else -_slip

    nv = v + accel * dt

    # Coast/brake must not yank the hull backwards through zero into a reverse
    # crawl (it should settle at rest) - but only when gravity itself can't hold
    # a slide going (gentle ground); on a steep slope let it cross into reverse.
    if throttle == 0 and abs(grav_a) <= grip and v != 0.0 and (v > 0.0) != (nv > 0.0):
        nv = 0.0

    # Speed limit with GRAVITY OVERSPEED: the engine can never push past the
    # spec limit, but gravity (steep descent / a fall's downhill momentum) may
    # carry the hull FASTER, temporarily, up to OVERSPEED_MAX_FACTOR x the limit.
    # Above the limit the engine stops contributing and an overspeed drag bleeds
    # the excess back to spec on flatter ground - the WoT 'downhill overspeed'
    # feel. Airborne already returned early, so a fall keeps its momentum and
    # this bleed only re-engages once the hull is back on the ground.
    _dir = 1.0 if nv >= 0.0 else -1.0
    _lim = p['speedFwd'] if nv >= 0.0 else p['speedBwd']
    if abs(nv) > _lim:
        # The overspeed drag ALWAYS bleeds the surplus back toward spec (rolling +
        # OVERSPEED_DAMP), so leaving a descent onto flat/uphill ground eases down
        # instead of a 1-tick snap to the limit. Gravity down THIS way is what lets
        # the surplus PERSIST up to the cap; without it the accel step adds no new
        # surplus, so the bleed just decays what is there, smoothly.
        _cap = _lim * (OVERSPEED_MAX_FACTOR - 1.0)
        # Build the surplus from the PREVIOUS speed at a slope-scaled rate so a descent
        # gains speed gradually toward the cap (not a 1-tick jump); bleed it off once the
        # ground stops helping.
        _prev_ex = abs(v) - _lim
        if _prev_ex < 0.0:
            _prev_ex = 0.0
        if (grav_a * _dir) > 0.05:
            _excess = _prev_ex + OVERSPEED_BUILD * math.sin(abs(slope_pitch)) * dt
        else:
            _excess = _prev_ex - (rr + OVERSPEED_DAMP) * dt
        if _excess < 0.0:
            _excess = 0.0
        if _excess > _cap:
            _excess = _cap
        nv = _dir * (_lim + _excess)
    return nv


def traverse_step(p, omega, steer_dir, v, dt, terrainIdx=0, drive_intent=0.0):
    '''One integration step of hull rotation speed (rad/s). WG 0.8.2: driving
    speed does NOT slow the traverse (SPEED_AFFECT_ROT_DECREASE = 0.0) and the
    rate ramps to full in ANG_ACCELERATION_TIME (50 ms). Medium/soft ground
    scales by the terrain-resistance ratio (the sim couples the limit to
    terrainResistance).'''
    speed_ratio = abs(v) / max(p['speedFwd'], 0.1)
    rot_mod = 1.0 / (1.0 + speed_ratio * SPEED_AFFECT_ROT_DECREASE)
    ter_mod = p['terrainResist'][0] / p['terrainResist'][terrainIdx]
    max_rot = p['rotSpd'] * rot_mod * ter_mod

    # Steering sign follows the explicit drive command. Velocity can disagree
    # while gravity, a collision or braking carries the hull the other way.
    intent_sign = -1.0 if drive_intent < 0.0 else 1.0
    target = steer_dir * intent_sign * max_rot
    diff = target - omega
    ramp = max_rot / ANG_ACCELERATION_TIME
    if abs(diff) < ramp * dt:
        omega = target
    else:
        omega += ramp * dt * (1 if diff > 0 else -1)
    if steer_dir == 0 and abs(omega) < 0.01:
        omega = 0.0
    return omega


def track_scroll(p, v, omega):
    '''Per-track surface speeds for WGVehicleFashion.movementInfo:
    v_track = v -/+ omega * halfGauge, clamped strictly below maxMovement.'''
    tls = v - omega * p['trackCenter']
    trs = v + omega * p['trackCenter']
    cap = p['speedFwd'] * SCROLL_CAP
    if tls > cap: tls = cap
    elif tls < -cap: tls = -cap
    if trs > cap: trs = cap
    elif trs < -cap: trs = -cap
    return tls, trs