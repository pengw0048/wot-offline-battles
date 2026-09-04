'''Uniform vehicle-physics laws for the offhangar mod (WoT 0.9.22 #1513).

ONE set of formulas, applied identically to the player and every bot. All
inputs come from the REAL vehicle descriptor (td.physics, built by
items/vehicles.pyc from the item_defs XML):

  weight            kg
  enginePower       W        (engine XML 'power' - reader converts)
  speedLimits       (fwd m/s, bwd m/s)
  terrainResistance (firm, medium, soft) multipliers
  specificFriction  mu*g in m/s^2 (chassis XML 0.07 x 9.81 = 0.6867 default)
  brakeForce        N when the target reader supplies it.  Exact #1513 leaves
                       this legacy descriptor field at zero; braking therefore
                       falls back to the shared track-grip limit.
  trackCenterOffset half track gauge in m (chassis topRightCarryingPoint.x)
  rotationSpeed     chassis, rad/s (reader applies radians())

This module must stay importable with nothing but the stdlib: it sits right
above paths.py at the bottom of the offhangar import graph.
'''

import math

# ---- Wargaming's own tuning, extracted from scripts/common/physics_shared.pyc
# (the module THIS client ships; it parameterizes the BigWorld rigid-body
# vehicle sim). Names kept 1:1 where they exist there. ----
G = 9.81
GRAVITY_FACTOR = 1.25          # WG: tanks live under 1.25 g 'arcade gravity'
GRAVITY = G * GRAVITY_FACTOR   # 12.26 m/s^2 - falls, slopes, all force laws
COHESION = 1.3                 # WG: physics.brakeFriction (brake/hold grip)
# Exact #1513 ``g_defaultTankXPhysicsCfg`` supplies longitudinal terrain grip
# as a two-point curve over the ground normal's Y component. It keeps full
# grip through 27.5 degrees, then linearly falls to 0.1 at 32 degrees. The
# previous port retained a fixed 0.54 coefficient from the 0.8.2 model; after
# rolling drag that cut drive around 24.8 degrees, before the #1513 curve even
# starts releasing grip. ``DRIVE_TRACTION`` remains a live-tuning multiplier,
# but its stock value must leave the recovered native curve unchanged.
DRIVE_TRACTION = 1.0
SLOPE_GRIP_LNG_FULL_Y = math.cos(math.radians(27.5))
SLOPE_GRIP_LNG_FULL = 1.0
SLOPE_GRIP_LNG_MIN_Y = math.cos(math.radians(32.0))
SLOPE_GRIP_LNG_MIN = 0.1
# Track-slip drag when rolling UP a grade past SLIP_THRESHOLD_TAN: extra
# deceleration = SLIP_DRAG * (tan(grade) - SLIP_THRESHOLD_TAN) * g. Bleeds the
# 'coast up a mountain on momentum' - real tracks slip and stop dead.
#
# The #1513 longitudinal grip starts falling at 27.5 degrees. Momentum bleed
# starts at the same point: starting it at the former 25.6-degree threshold
# made a still-full-grip native slope lose several m/s2 for no retail reason.
# SLIP_DRAG shapes the ramp above the threshold. 10.0 stays progressive:
# about 1.4 m/s2 at 28 degrees, 7.0 at 30 degrees and 22 at 35 degrees.
SLIP_THRESHOLD_TAN = math.tan(math.radians(27.5))
SLIP_DRAG = 10.0
FORWARD_FRICTION = 0.07        # WG: physics.forwardFriction (rolling)
# WG scales enginePower by GRAVITY_FACTOR_SCALED (0.00125) against masses in
# tons (WEIGHT_SCALE 0.001): net effect, drive power acts 1.25x its SI value.
POWER_FACTOR = GRAVITY_FACTOR
BKWD_POWER_FRACTION = 1.0      # WG: reverse gets FULL engine power in 0.8.2
ANG_ACCELERATION_TIME = 0.05   # WG: hull traverse reaches full rate in 50 ms
SPEED_AFFECT_ROT_DECREASE = 0.0  # WG: driving speed does NOT slow the traverse
ROTATION_POWER_FRACTION = 0.85   # WG: rotation may use 85% of engine power
# The brake/hold cohesion approximation is separate from #1513's recovered
# longitudinal pulling-grip curve above.
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
# #1513 classifies overturn from the hull world-up cosine.
OVERTURN_WARNING_COSINE = math.cos(math.radians(70.0))
OVERTURN_DANGER_COSINE = math.cos(math.radians(80.0))
# Ground following may bridge small suspension seams, but the allowance must
# describe the old supporting slope rather than grow with absolute road speed.
# Otherwise a faster tank is pulled farther down a cliff each frame and never
# enters the airborne phase. The bounds preserve the copied integration
# envelope while keeping flat-ground tolerance independent of speed.
GROUND_FOLLOW_BASE = 0.6
GROUND_FOLLOW_MIN = 0.8
GROUND_FOLLOW_MAX = 2.5
GROUND_PITCH_LIMIT = 0.96
# Grounded hulls use the same four glancing directions after an exact hard
# contact. These angles and decay constants used to live only in the visible
# player's integrator while copied Bots stopped with a separate fixed factor.
HARD_CONTACT_YAW_DELTAS = (0.55, -0.55, 1.0, -1.0)
HARD_CONTACT_ENTRY_FACTOR = 0.60
HARD_CONTACT_SLIDE_DECAY = 0.85
HARD_CONTACT_BRAKE_DECAY = 0.35
HARD_CONTACT_STOP_SPEED = 0.05
HARD_CONTACT_GRIND_TICKS = 4
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
SLIDE_HOLD_TAN = 0.50   # 26.6 deg static perch; a powered hull can briefly climb beyond the angle it can hold after release
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
# dial to match original WoT feel. See apply_tuning(). ----
_TUNABLE = {
	'gravity_factor':      'GRAVITY_FACTOR',
	'cohesion':            'COHESION',
	'drive_traction':      'DRIVE_TRACTION',
	'slip_drag':           'SLIP_DRAG',
	'slip_threshold_tan':  'SLIP_THRESHOLD_TAN',
	'power_factor':        'POWER_FACTOR',
	'bkwd_power_fraction': 'BKWD_POWER_FRACTION',
	'traverse_accel_time': 'ANG_ACCELERATION_TIME',
	'traverse_speed_cost': 'SPEED_AFFECT_ROT_DECREASE',
	'coast_brake_share':   'COAST_BRAKE_SHARE',
	'steer_resist_mult':   'STEER_RESIST_MULT',
	'slide_max':           'SLIDE_MAX',
	'slide_drag':          'SLIDE_DRAG',
	'slide_hold_tan':      'SLIDE_HOLD_TAN',
	'slide_kinetic':       'SLIDE_KINETIC',
	'overspeed_max_factor': 'OVERSPEED_MAX_FACTOR',
	'overspeed_damp':      'OVERSPEED_DAMP',
	'overspeed_build':     'OVERSPEED_BUILD',
	'slope_coh_decay':     'SLOPE_COH_DECAY',
	'slope_coh_decay_y':   'SLOPE_COH_DECAY_Y',
	'coh_decay_bound':     'COH_DECAY_BOUND',
	'fall_safe_speed':     'FALL_SAFE_SPEED',
	'fall_dmg_per_ms':     'FALL_DMG_PER_MS',
}


def apply_tuning(overrides):
	'''Overlay config.json "physics_tuning" onto the module constants. Returns
	the list of applied "key=value" strings (for logging). GRAVITY is derived
	from G*GRAVITY_FACTOR, so it is recomputed after any override.'''
	applied = []
	g = globals()
	if isinstance(overrides, dict):
		for k, gname in _TUNABLE.items():
			if k in overrides:
				try:
					g[gname] = float(overrides[k])
					applied.append('%s=%s' % (k, overrides[k]))
				except (TypeError, ValueError):
					pass
	g['GRAVITY'] = G * GRAVITY_FACTOR
	# brakeDecel default is COHESION*GRAVITY, frozen at load - refresh it so a
	# cohesion/gravity override reaches the no-brakeForce fallback too.
	_DEFAULTS['brakeDecel'] = COHESION * GRAVITY
	return applied


def hard_contact_candidate_yaws(yaw):
	'''Return the shared ordered glancing paths for one blocked hull heading.'''
	return tuple(float(yaw) + delta for delta in HARD_CONTACT_YAW_DELTAS)


def hard_contact_step(speed, dt, grinding=False, slide_yaw=None):
	'''Resolve one grounded hard-contact response without probing the world.

	Callers own their native/static collision queries and pass the first clear
	glancing yaw, if any. The returned tuple is ``(speed, dx, dz)`` so visible
	players and copied Bots cannot apply different damping or displacement after
	the same probe result.
	'''
	speed = float(speed)
	dt = max(0.0, float(dt))
	if slide_yaw is None:
		speed *= HARD_CONTACT_BRAKE_DECAY ** (dt * 60.0)
		if abs(speed) < HARD_CONTACT_STOP_SPEED:
			speed = 0.0
		return speed, 0.0, 0.0
	if not grinding:
		speed *= HARD_CONTACT_ENTRY_FACTOR
	speed *= HARD_CONTACT_SLIDE_DECAY ** (dt * 60.0)
	yaw = float(slide_yaw)
	return (speed, math.sin(yaw) * speed * dt,
			math.cos(yaw) * speed * dt)


def snapshot(p, v, omega, throttle, slope_pitch, airborne, slide_speed, tank='',
             ground_kmh=None, pitch_deg=None, roll_deg=None, vert_ms=None,
             deflect=None, dy=None, slide_slope=None, dy_tick=None, y_src=None, terr_spread=None):
	'''One telemetry row: the live physics state + the force balance that
	decides hold/climb/slide, PLUS the observable extras that the pure force
	numbers miss - ACTUAL over-ground speed vs commanded v (catches wall-slide
	sideways drift / clipping), hull pitch & roll (catches orientation glitches),
	vertical speed & height delta (catches float/sink/teleport), and whether the
	wall-slide deflection fired this frame. Returns (name, value) pairs.'''
	grip = _grip_decel(p, slope_pitch)
	grav_a = GRAVITY * math.sin(slope_pitch)
	ef = engine_force(p, v, throttle, slope_pitch) / p['mass'] if throttle else 0.0
	rows = [
		('tank', tank),
		('mass_kg', round(p['mass'], 0)),
		('power_hp', round(p['powerW'] / 735.49875, 0)),
		('pw_ratio_hp_t', round((p['powerW'] / 735.49875) / max(p['mass'] / 1000.0, 0.01), 1)),
		('speedFwd_kmh', round(p['speedFwd'] * 3.6, 1)),
		('v_kmh', round(v * 3.6, 2)),
		('throttle', throttle),
		('slope_deg', round(math.degrees(slope_pitch), 1)),
		('grip_ms2', round(grip, 2)),
		('grav_along_ms2', round(grav_a, 2)),
		('hold_margin_ms2', round(grip - abs(grav_a), 2)),   # <0 => slides
		('drive_accel_ms2', round(ef, 2)),
		('turn_degs', round(math.degrees(omega), 1)),
		('rotSpd_max_degs', round(math.degrees(p['rotSpd']), 1)),
		('airborne', 1 if airborne else 0),
		('slide_ms', round(slide_speed, 2)),
		('terrain_r0', p['terrainResist'][0]),
		('spec_friction', round(p['specificFriction'], 3)),
		('brake_ms2', round(p['brakeDecel'], 2)),
	]
	# Observable extras (only when supplied by the caller):
	if ground_kmh is not None:
		# real over-ground speed; a big gap vs v_kmh = sideways wall-slide/clip
		rows.append(('ground_kmh', round(ground_kmh, 2)))
		rows.append(('drift_kmh', round(ground_kmh - abs(v * 3.6), 2)))
	if pitch_deg is not None:
		rows.append(('hull_pitch_deg', round(pitch_deg, 1)))
	if roll_deg is not None:
		rows.append(('hull_roll_deg', round(roll_deg, 1)))
	if vert_ms is not None:
		rows.append(('vert_ms', round(vert_ms, 2)))
	if dy is not None:
		rows.append(('dy_m', round(dy, 3)))   # per-tick height change (float/sink/teleport)
	if slide_slope is not None:
		# Raw ground gradient (a tangent) that DRIVES the slide gate - it includes
		# the lateral/roll component and gets no spike rejection. slope_deg above is
		# the hull-axis pitch the FORCE model actually integrated. When these two
		# disagree, a 'flat ground + sliding' row is the two probes differing, NOT a
		# physics contradiction. Log both so the CSV is honest about which is which.
		rows.append(('slide_slope_deg', round(math.degrees(math.atan(slide_slope)), 1)))
	if dy_tick is not None:
		rows.append(('dy_tick_m', round(dy_tick, 3)))   # biggest single 60fps-tick height jump this window
	if y_src is not None:
		rows.append(('y_src', y_src))                    # which path set veh_pos.y at that jump
	if terr_spread is not None:
		rows.append(('terr_spread_m', round(terr_spread, 2)))   # height spread of the 4 footprint samples = edge sharpness
	if deflect is not None:
		rows.append(('wall_deflect', 1 if deflect else 0))
	return rows


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
	'nativePowerRatio': 1.0,
}


def _factor_list(factors, name):
	try:
		values = factors[name]
		return (float(values[0]), float(values[1]), float(values[2]))
	except (KeyError, IndexError, TypeError, ValueError):
		return (1.0, 1.0, 1.0)


def _factor(factors, name):
	try:
		return max(0.0, float(factors[name]))
	except (KeyError, TypeError, ValueError):
		return 1.0


def _native_power_ratio(td, power_w):
	'''Read #1513's selected detailed-physics engine override.

	``smplEnginePower`` is stored in the native tonnes-based configuration.
	The generic descriptor conversion would be ``enginePower * 0.00125``;
	the selected detailed entry replaces that value before the native physics
	is configured.  Return the exact replacement/base ratio so the copied SI
	integrator keeps the same per-engine override while retaining POWER_FACTOR
	as the live-tuning base.
	'''
	try:
		projected = getattr(td, 'physics', {}).get('nativePowerRatio')
		projected = float(projected)
		if (projected > 0.0 and not math.isnan(projected) and
				not math.isinf(projected)):
			return projected
	except (AttributeError, TypeError, ValueError):
		pass
	try:
		engine_name = getattr(getattr(td, 'engine'), 'name')
		xphysics = getattr(getattr(td, 'type'), 'xphysics')
		detailed = xphysics['detailed']
		engine_cfg = detailed['engines'][engine_name]
		native_power = float(engine_cfg['smplEnginePower'])
		base_power = float(power_w) * 0.001 * GRAVITY_FACTOR
		if (native_power > 0.0 and base_power > 0.0 and
				not math.isnan(native_power) and
				not math.isinf(native_power)):
			return native_power / base_power
	except (AttributeError, KeyError, TypeError, ValueError):
		pass
	return 1.0


def derive_params(td, factors=None):
	'''Real per-vehicle parameter set from a VehicleDescr. Every consumer
	(player tick, each bot) MUST source its numbers from here - this is the
	single place that knows the units of td.physics.

	``factors`` is the #1513 attribute-factor dictionary the garage panel
	reads. With it the driver skills, the grousers and the engine consumables
	reach the physics exactly as VehicleParams composes them.'''
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
			# Legacy descriptor observation retained for telemetry/compatibility.
			# Detailed #1513 drive grip comes from the common curve above.
			p['minPlaneNormalY'] = float(tdp['minPlaneNormalY'])
	except Exception:
		pass
	p['nativePowerRatio'] = _native_power_ratio(td, p['powerW'])
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
	# VehicleParams composes terrain resistance as the crew factor times the
	# descriptor's own rolling-friction factors, which is where the grousers
	# live.
	rolling = (1.0, 1.0, 1.0)
	try:
		if 'rollingFrictionFactors' in tdp:
			values = tdp['rollingFrictionFactors']
			rolling = (float(values[0]), float(values[1]), float(values[2]))
	except (IndexError, TypeError, ValueError):
		rolling = (1.0, 1.0, 1.0)
	crew = _factor_list(factors or {}, 'chassis/terrainResistance')
	p['terrainResist'] = tuple(
		p['terrainResist'][index] * crew[index] * rolling[index]
		for index in range(3))
	if factors:
		p['rotSpd'] *= _factor(factors, 'vehicle/rotationSpeed')
		p['powerW'] *= _factor(factors, 'engine/power')
	return p


def longitudinal_slope_grip(slope_pitch):
	'''Return #1513's terrain pulling grip for this fore/aft slope.

	The native configuration stores the curve as ``(normalY, grip)`` pairs and
	the #1513 executable evaluates those pairs with clamped linear
	interpolation. Ground flatter than 27.5 degrees therefore keeps grip 1.0;
	ground at or beyond 32 degrees keeps only 0.1. ``DRIVE_TRACTION`` is an
	explicit tuning multiplier around that recovered stock curve.
	'''
	ny = math.cos(slope_pitch)
	if ny >= SLOPE_GRIP_LNG_FULL_Y:
		grip = SLOPE_GRIP_LNG_FULL
	elif ny <= SLOPE_GRIP_LNG_MIN_Y:
		grip = SLOPE_GRIP_LNG_MIN
	else:
		span = SLOPE_GRIP_LNG_FULL_Y - SLOPE_GRIP_LNG_MIN_Y
		progress = (ny - SLOPE_GRIP_LNG_MIN_Y) / span
		grip = (SLOPE_GRIP_LNG_MIN + progress *
			(SLOPE_GRIP_LNG_FULL - SLOPE_GRIP_LNG_MIN))
	return grip * DRIVE_TRACTION


def engine_force(p, v, throttle, slope_pitch=0.0):
	'''F = P_eff / max(|v|, v_min), TRACTION-capped. P_eff = powerW x
	POWER_FACTOR (WG scales engine power by GRAVITY_FACTOR). Reverse gets
	BKWD_POWER_FRACTION (1.0 = full power).

	Drive force can never exceed the #1513 longitudinal terrain-grip curve
	times the current normal force. The curve stays at 1.0 through 27.5 degrees
	and releases to 0.1 at 32 degrees, so the selected engine and installed
	mass govern ordinary climbs while very steep faces still lose drive.'''
	if throttle == 0:
		return 0.0
	pw = p['powerW'] * POWER_FACTOR * p.get('nativePowerRatio', 1.0)
	if throttle < 0:
		pw *= BKWD_POWER_FRACTION
	f = pw / max(abs(v), ENGINE_MIN_V)
	ny = math.cos(slope_pitch)
	fmax = (longitudinal_slope_grip(slope_pitch) * p['mass'] * GRAVITY *
		(ny if ny > 0.1 else 0.1))
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
	near the static perch tangent, where gravity owns the descent.'''
	ny = math.cos(slope_pitch)
	grip_decel = slope_cohesion(ny) * GRAVITY * (ny if ny > 0.1 else 0.1)
	brake = p['brakeDecel'] if p['brakeDecel'] < grip_decel else grip_decel
	if active:
		return p['mass'] * brake
	return (rolling_resist_force(p, terrainIdx, False) +
		p['mass'] * COAST_BRAKE_SHARE * brake)


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
		_max_climb = (longitudinal_slope_grip(slope_pitch) * GRAVITY *
			(_ny_c if _ny_c > 0.1 else 0.1))
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
			# The 2.3-reviewed coast law: rolling + partial grip brake oppose
			# the motion; gravity still acts. The old relief started unloading
			# at zero slope and glided ~27 m on a 7-degree field descent; the
			# share now fades only near the static perch limit, so a slope the
			# parked hold cannot keep slides while every parkable slope brakes
			# like the flat.
			motion_sign = 1.0 if v > 0.0 else -1.0
			downhill_tangent = max(0.0, math.tan(slope_pitch) * motion_sign)
			fade_start = 0.8 * SLIDE_HOLD_TAN
			fade = min(1.0, max(0.0, (downhill_tangent - fade_start) /
			                    (SLIDE_HOLD_TAN - fade_start)))
			resist = rr + COAST_BRAKE_SHARE * (1.0 - fade) * grip
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
		# Gravity holds the surplus only while the throttle still drives the
		# motion. A released throttle brakes, so the surplus bleeds; the field
		# run stayed pinned at the limit down the whole descent without this.
		if throttle * _dir > 0.0 and (grav_a * _dir) > 0.05:
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


def slope_slide_speed(cur, slope_tan, dt):
	'''WG-faithful passive slope slide along the fall line. The tracks HOLD the
	hull while tan(theta) <= effective cohesion (~46 deg with slope decay,
	~52 deg on firm ground) - a parked tank does NOT creep down ordinary hills.
	Past the grip limit the hull accelerates down-slope by the EXCESS
	g*(sin - coh*cos), regardless of throttle: engine force acts along the hull
	heading (handled by longitudinal_step), it cannot stop the LATERAL fall-line
	slip. The caller applies only the cross-heading component so the two never
	double-count. Below the grip limit the residual bleeds off fast.'''
	theta = math.atan(slope_tan)
	# Lateral fall-line hold: tracks resist SIDEWAYS slip only up to SLIDE_HOLD_TAN
	# (gentler than the brake COHESION), so a hull cannot perch across a steep bank
	# it is only leaning on. Past it, slip down the excess.
	if slope_tan <= SLIDE_HOLD_TAN:
		cur -= COHESION * GRAVITY * dt   # holds: kill any residual slide fast
		return cur if cur > 0.0 else 0.0
	# accelerate by the grip-excess, minus a track drag proportional to slide
	# speed -> settles at a natural terrain-dependent terminal, not a flat cap.
	cur += (GRAVITY * (math.sin(theta) - SLIDE_HOLD_TAN * math.cos(theta)) - SLIDE_DRAG * cur) * dt
	if cur < 0.0:
		cur = 0.0
	elif cur > SLIDE_MAX:
		cur = SLIDE_MAX
	return cur


def ground_follow_gap(speed, slope_pitch, dt):
	'''Maximum supported drop for one grounded copied-pose step.

	``speed`` is signed along the same axis used by ``slope_pitch`` and the pose
	integrator moves x/z by ``speed * dt``. The old surface contributes
	``speed * tan(pitch) * dt`` only when travel is downhill. Gravity adds the
	same semi-implicit one-step sag used by the airborne integrator.
	'''
	step = max(0.0, float(dt))
	pitch = max(-GROUND_PITCH_LIMIT, min(
		GROUND_PITCH_LIMIT, float(slope_pitch)))
	tangent_drop = max(0.0, float(speed) * math.tan(pitch) * step)
	gap = GROUND_FOLLOW_BASE + tangent_drop + GRAVITY * step * step
	return max(GROUND_FOLLOW_MIN, min(GROUND_FOLLOW_MAX, gap))


def launch_vertical_speed(speed, slope_pitch):
	'''Upward velocity retained when signed travel loses ground support.'''
	vertical = float(speed) * math.sin(-float(slope_pitch))
	return vertical if vertical > 0.0 else 0.0


def overturn_level_from_up_cosine(up_cosine, warning_cosine=None,
			danger_cosine=None):
	'''Return 0=safe, 1=caution or 2=danger from hull world-up cosine.'''
	warning = (OVERTURN_WARNING_COSINE if warning_cosine is None else
	           float(warning_cosine))
	danger = (OVERTURN_DANGER_COSINE if danger_cosine is None else
	          float(danger_cosine))
	up_cosine = max(-1.0, min(1.0, float(up_cosine)))
	if up_cosine <= danger:
		return 2
	if up_cosine <= warning:
		return 1
	return 0


def overturn_level(pitch, roll, warning_cosine=None, danger_cosine=None):
	'''Classify an Euler hull pose when a world-up cosine is unavailable.'''
	return overturn_level_from_up_cosine(
		math.cos(float(pitch)) * math.cos(float(roll)),
		warning_cosine, danger_cosine)


def fall_damage(maxHealth, impact_speed):
	'''HP cost of a landing. 0 below FALL_SAFE_SPEED (~4 m drop).'''
	iv = abs(impact_speed)
	if iv <= FALL_SAFE_SPEED:
		return 0
	return int(maxHealth * (iv - FALL_SAFE_SPEED) * FALL_DMG_PER_MS)
