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

from gui.mods.offline_lan_0922.worker_diagnostics import observed

import math

# ---- Wargaming's own tuning, extracted from scripts/common/physics_shared.pyc
# (the module THIS client ships; it parameterizes the BigWorld rigid-body
# vehicle sim). Names kept 1:1 where they exist there. ----
G = 9.81
GRAVITY_FACTOR = 1.25          # WG: tanks live under 1.25 g 'arcade gravity'
GRAVITY = G * GRAVITY_FACTOR   # 12.26 m/s^2 - falls, slopes, all force laws
WEIGHT_SCALE = 0.001
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
# The contrib suspension trial applies a separate side-slip projection. These
# two points came from the contributed implementation; this repository has not
# proved that they reproduce the #1513 native curve.
SLOPE_GRIP_SDW_FULL_Y = math.cos(math.radians(24.5))
SLOPE_GRIP_SDW_FULL = 1.0
SLOPE_GRIP_SDW_MIN_Y = math.cos(math.radians(29.0))
SLOPE_GRIP_SDW_MIN = 0.1
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
# Contrib suspension trial. Descriptor geometry and detailed-xphysics values
# remain client-owned inputs; the interpolation and rigid solver below are a
# derived projection whose native gameplay feel still needs Windows evidence.
SUSP_COMPRESSION_MIN = 0.85
SUSP_COMPRESSION_MIN_MASS = 60.0
SUSP_COMPRESSION_MAX = 0.88
SUSP_COMPRESSION_MAX_MASS = 30.0
CLEARANCE_SCALE = 1.75
CLEARANCE_MIN = 0.55
CLEARANCE_MAX = 0.60
CLEARANCE_TO_LENGTH_MIN = 0.085
CLEARANCE_TO_LENGTH_MAX = 0.112
TRACK_LENGTH_MIN = 0.60
TRACK_LENGTH_MAX = 0.64
HARD_RATIO_MIN = 0.50
HARD_RATIO_MAX = 0.52
# Exact #1513 physics_shared.initVehiclePhysicsClient spring layout.  Both
# NUM_SPRINGS_NORMAL and NUM_SPRINGS_LONG are 5 in this build, so the client
# always mounts five pairs, evenly spaced over _computeTrackLength and offset
# to +/-0.45 * chassis bbox width.  descriptor.physics['trackCenterOffset'] is
# the visual track centre, not this spring mount, and is deliberately unused.
NUM_SPRING_PAIRS = 5
SPRING_TRACK_WIDTH_RATIO = 0.45
# Per-spring stiffness, damping and the hull inertia tensor are owned by the
# native #1513 solver and are shipped to no process, client or cell.  These
# remain an explicit trial projection rather than a recovered retail law.
TRIAL_HULL_INERTIA_FACTORS = (1.0, 1.0, 1.8)
SERVER_PHYSICS_HZ = 30.0
SERVER_PHYSICS_SUBSTEPS = 2
SERVER_PHYSICS_STEP = 1.0 / (
	SERVER_PHYSICS_HZ * SERVER_PHYSICS_SUBSTEPS)
SERVER_PHYSICS_MAX_SUBSTEPS = 12
SERVER_PHYSICS_CONSTRAINT_ITERATIONS = 10
SUSPENSION_PATH_MAX_PROBES = 3
SUSPENSION_PATH_PROBE_SPACING = 1.0
SERVER_PHYSICS_DAMPING_RATIO_BASE = 0.75
SERVER_PHYSICS_DAMPING_RATIO_SCALE = 0.25
SERVER_PHYSICS_MAX_SPRING_FORCE_FACTOR = 6.0
AIRBORNE_ANGULAR_DAMPING = 4.0
AIRBORNE_ANGULAR_SPEED_LIMIT = 0.6
FREEZE_ANG_ACCEL_EPSILON = 0.35
FREEZE_ACCEL_EPSILON = 0.4
FREEZE_VEL_EPSILON = 0.15
FREEZE_ANG_VEL_EPSILON = 0.06
ALLOWED_PENETRATION = 0.01
CONTACT_PENETRATION = 0.1
TRACKS_PENETRATION = 0.01
SUSPENSION_GEOMETRY_EPSILON = 0.01
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


@observed('physics.derive_params')
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


def _value(component, name, default=None):
	if isinstance(component, dict):
		return component.get(name, default)
	return getattr(component, name, default)


def _required_value(component, name, context):
	value = _value(component, name)
	if value is None:
		raise ValueError(
			'%s is required by the contrib suspension trial' % context)
	return value


def _optional_factor(config, name):
	'''Return one positive side-stiffness refinement, defaulting to uniform.'''
	raw = _optional_value(config, name)
	if raw is None:
		return 1.0
	value = _finite_number(raw, 'detailed chassis %s' % name)
	if value <= 0.0:
		raise ValueError('detailed chassis stiffness must be positive')
	return value


def _optional_value(component, name):
	'''Return one refinement value, or None when the client does not ship it.'''
	if isinstance(component, dict):
		return component.get(name)
	return getattr(component, name, None)


def _finite_number(value, context):
	try:
		value = float(value)
	except (TypeError, ValueError) as error:
		raise ValueError('%s is invalid: %s' % (context, error))
	if math.isnan(value) or math.isinf(value):
		raise ValueError('%s is not finite' % context)
	return value


def _number_tuple(values, context):
	try:
		return tuple(
			_finite_number(value, '%s[%d]' % (context, index))
			for index, value in enumerate(values))
	except TypeError as error:
		raise ValueError('%s is invalid: %s' % (context, error))


def _linear_interpolate(value, minimum, maximum, lower, upper):
	if maximum == minimum:
		return float(lower)
	ratio = (float(value) - float(minimum)) / (
		float(maximum) - float(minimum))
	ratio = max(0.0, min(1.0, ratio))
	return float(lower) + (float(upper) - float(lower)) * ratio


def _suspension_compression(mass_tons):
	return _linear_interpolate(
		mass_tons, SUSP_COMPRESSION_MIN_MASS,
		SUSP_COMPRESSION_MAX_MASS,
		SUSP_COMPRESSION_MIN, SUSP_COMPRESSION_MAX)


def _track_length(clearance, chassis_length):
	'''Exact #1513 physics_shared._computeTrackLength.'''
	ratio = float(clearance) / max(float(chassis_length), 0.01)
	length_ratio = _linear_interpolate(
		ratio, CLEARANCE_TO_LENGTH_MIN, CLEARANCE_TO_LENGTH_MAX,
		TRACK_LENGTH_MAX, TRACK_LENGTH_MIN)
	return length_ratio * float(chassis_length)


def _hard_ratio(clearance, chassis_length):
	ratio = float(clearance) / max(float(chassis_length), 0.01)
	return _linear_interpolate(
		ratio, CLEARANCE_TO_LENGTH_MIN, CLEARANCE_TO_LENGTH_MAX,
		HARD_RATIO_MIN, HARD_RATIO_MAX)


def _detailed_chassis_config(descriptor):
	"""Return the optional richer chassis xphysics, or an empty mapping.

	Exact #1513 ``vehicles.VehicleType.__init__`` reads xphysics through
	``_readXPhysics`` only under ``IS_CELLAPP``; every ``IS_CLIENT`` process
	goes through ``_readXPhysicsClient``, which returns a flat
	``{'engines': ..., 'chassis': ...}`` mapping with no ``detailed`` level,
	and whose ``_xphysicsParseChassisClient`` fills each chassis entry with
	``grounds`` alone.  Both this product's visible client and its hidden
	worker are real client processes, so ``roadWheelPositions``,
	``stiffnessFactors``, ``stiffness0``, ``stiffness1``, ``damping``,
	``bodyHeight`` and ``hullInertiaFactors`` are never present in production.
	They stay supported as refinements for a non-client descriptor, but the
	trial derives its layout from the client projection when they are absent.
	"""
	try:
		tank_type = _required_value(
			descriptor, 'type', 'vehicle type descriptor')
		xphysics = _required_value(
			tank_type, 'xphysics', 'vehicle xphysics')
	except ValueError:
		return {}
	if not isinstance(xphysics, dict):
		return {}
	# The client mapping is already the chassis level; the cell mapping nests
	# the same key one level below 'detailed'.
	level = xphysics.get('detailed', xphysics)
	if not isinstance(level, dict):
		return {}
	configs = level.get('chassis')
	if not isinstance(configs, dict):
		return {}
	try:
		chassis = _required_value(
			descriptor, 'chassis', 'vehicle chassis descriptor')
		name = _required_value(chassis, 'name', 'selected chassis name')
	except ValueError:
		return {}
	config = configs.get(name)
	return config if isinstance(config, dict) else {}


def _bbox(component, context):
	tester = _required_value(component, 'hitTester', '%s hitTester' % context)
	bounds = _required_value(tester, 'bbox', '%s hitTester bbox' % context)
	try:
		minimum = bounds[0]
		maximum = bounds[1]
		dimensions = tuple(
			_finite_number(maximum[index], '%s bbox maximum' % context) -
			_finite_number(minimum[index], '%s bbox minimum' % context)
			for index in range(3))
	except (IndexError, TypeError) as error:
		raise ValueError('%s hitTester bbox is invalid: %s' % (
			context, error))
	if any(value <= 0.0 for value in dimensions):
		raise ValueError('%s hitTester bbox has no volume' % context)
	return minimum, maximum, dimensions


def _suspension_wheel_radius(chassis, config, spring_spacing):
	'''Return the contact-memory radius: a wheel, else one spring spacing.

	The radius only sizes ``contact_memory_distance``, which bridges a single
	rail or sleeper gap between two ground samples.  One spring spacing is
	exactly that gap, so an unrefined descriptor without wheel groups keeps a
	bounded tolerance instead of retiring the whole trial.
	'''
	raw = _optional_value(config, 'wheelRadius')
	if raw is not None:
		radius = _finite_number(
			raw, 'detailed chassis wheelRadius')
		if radius > 0.0:
			return radius
	try:
		radius = _finite_number(
			chassis.wheels.groups[0].radius,
			'selected chassis wheel radius')
	except (AttributeError, IndexError, TypeError, ValueError):
		radius = 0.0
	if radius > 0.0:
		return radius
	return max(0.125, float(spring_spacing) * 0.5)


def suspension_trial_excluded(descriptor):
	'''Return whether exact #1513 uses hydraulic hull-aiming suspension.'''
	return bool(_value(
		descriptor, 'isPitchHullAimingAvailable', False))


def derive_suspension_params(descriptor):
	'''Build the ten-spring rigid-body trial for one tank.

	The spring layout is the exact #1513 client projection.
	``physics_shared.initVehiclePhysicsClient`` derives its five carrier pairs
	from the hull and chassis bboxes, ``chassis.hullPosition`` and
	``physics['weight']`` alone, and every interpolation constant used here
	(``WEIGHT_SCALE``, ``CLEARANCE``, ``CLEARANCE_MIN``/``MAX``,
	``SUSP_COMPRESSION_*``, ``CLEARANCE_TO_LENGTH_*``, ``TRACK_LENGTH_*``,
	``HARD_RATIO_*``) is that module's own value.

	Per-spring stiffness, damping and the hull inertia tensor are not: the
	native solver owns them and no #1513 process is given them, so those are
	an explicit trial projection and are the reason this remains a trial.
	'''
	if suspension_trial_excluded(descriptor):
		# Exact #1513 initVehiclePhysicsClient gives pitch-hull-aiming tanks a
		# zero hard ratio and installs mode-specific suspensionSpringsLength
		# caches on both descriptors.  This trial owns neither that native hull
		# aiming state nor those per-mode caches, so its ordinary-tank projection
		# must not compete with the existing hydraulic implementation.
		raise ValueError(
			'#1513 hydraulic suspension is outside the contrib trial')
	physics = derive_params(descriptor)
	descriptor_physics = _required_value(
		descriptor, 'physics', 'vehicle physics descriptor')
	mass = _finite_number(
		_required_value(descriptor_physics, 'weight', 'vehicle physics weight'),
		'vehicle physics weight')
	if mass <= 0.0:
		raise ValueError('vehicle physics weight must be positive')
	# Keep derive_params as the unit owner and reject any unexpected divergence
	# instead of silently simulating suspension with a generic mass.
	if abs(float(physics['mass']) - mass) > 1.0e-6:
		raise ValueError('vehicle physics mass projection is inconsistent')
	chassis = _required_value(
		descriptor, 'chassis', 'vehicle chassis descriptor')
	hull = _required_value(descriptor, 'hull', 'vehicle hull descriptor')
	unused_chassis_min, unused_chassis_max, chassis_size = _bbox(
		chassis, 'chassis')
	hull_minimum, unused_hull_max, unused_hull_size = _bbox(hull, 'hull')
	width, chassis_height, chassis_length = chassis_size
	hull_position = _required_value(
		chassis, 'hullPosition', 'selected chassis hullPosition')
	try:
		clearance_raw = (
			_finite_number(hull_position[1], 'chassis hullPosition.y') +
			_finite_number(hull_minimum[1], 'hull bbox minimum.y'))
	except (IndexError, TypeError) as error:
		raise ValueError('selected chassis hullPosition is invalid: %s' % error)
	if clearance_raw <= 0.0:
		raise ValueError('descriptor ground clearance must be positive')
	clearance = clearance_raw * CLEARANCE_SCALE
	clearance = max(
		CLEARANCE_MIN * chassis_height,
		min(CLEARANCE_MAX * chassis_height, clearance))
	mass_tons = mass * WEIGHT_SCALE
	compression_ratio = _suspension_compression(mass_tons)
	rest_length = clearance / max(compression_ratio, 0.01)
	static_compression = max(0.01, rest_length - clearance)
	hard_ratio = _hard_ratio(clearance, chassis_length)
	track_length = _track_length(clearance, chassis_length)
	step_z = track_length / float(NUM_SPRING_PAIRS - 1)
	config = _detailed_chassis_config(descriptor)
	# Exact #1513 initVehiclePhysicsClient mounts the five carrier pairs at
	# begZ = -trackLen * 0.5 with stepZ = trackLen / (pairs - 1).  A non-client
	# descriptor may refine that with authored road-wheel positions.
	raw_positions = _optional_value(config, 'roadWheelPositions')
	if raw_positions is None:
		positions = tuple(
			-track_length * 0.5 + step_z * float(index)
			for index in range(NUM_SPRING_PAIRS))
	else:
		positions = _number_tuple(
			raw_positions, 'detailed chassis roadWheelPositions')
		if len(positions) != NUM_SPRING_PAIRS:
			raise ValueError(
				'contrib suspension trial requires five road-wheel positions')
		position_limit = chassis_length * 0.5
		if any(abs(value) > position_limit + SUSPENSION_GEOMETRY_EPSILON
				for value in positions):
			raise ValueError(
				'roadWheelPositions extend beyond the chassis bbox')
	raw_stiffness_factors = _optional_value(config, 'stiffnessFactors')
	if raw_stiffness_factors is None:
		# The client's addDamperSpring call passes one shared length and hard
		# ratio to every pair, so an unrefined layout is uniform.
		stiffness_factors = tuple(1.0 for unused in positions)
	else:
		stiffness_factors = _number_tuple(
			raw_stiffness_factors, 'detailed chassis stiffnessFactors')
		if len(stiffness_factors) != len(positions):
			raise ValueError(
				'stiffnessFactors must match the five road-wheel positions')
		if any(value <= 0.0 for value in stiffness_factors):
			raise ValueError('stiffnessFactors must be positive')
	left_factor = _optional_factor(config, 'stiffness0')
	right_factor = _optional_factor(config, 'stiffness1')
	raw_damping = _optional_value(config, 'damping')
	if raw_damping is None:
		damping_value = 0.0
	else:
		damping_value = _finite_number(
			raw_damping, 'detailed chassis damping')
		if damping_value < 0.0:
			raise ValueError(
				'detailed chassis damping must not be negative')
	damping_ratio = max(0.55, min(
		1.25, SERVER_PHYSICS_DAMPING_RATIO_BASE +
		SERVER_PHYSICS_DAMPING_RATIO_SCALE * damping_value))
	track_x = width * SPRING_TRACK_WIDTH_RATIO
	raw_springs = []
	for side, x, side_factor in (
			('left', -track_x, left_factor),
			('right', track_x, right_factor)):
		for index, z in enumerate(positions):
			raw_springs.append((
				side, x, z, stiffness_factors[index] * side_factor))
	weight_total = sum(row[3] for row in raw_springs)
	if weight_total <= 0.0:
		raise ValueError('suspension stiffness weights must be positive')
	springs = []
	for side, x, z, weight in raw_springs:
		sprung_mass = mass * weight / weight_total
		stiffness = sprung_mass * GRAVITY / static_compression
		damping = 2.0 * damping_ratio * math.sqrt(
			stiffness * sprung_mass)
		springs.append({
			'side': side, 'x': x, 'z': z,
			'mass': sprung_mass, 'stiffness': stiffness,
			'damping': damping,
			'rest_length': rest_length,
			'static_compression': static_compression,
			'max_compression': max(
				static_compression,
				rest_length - clearance * hard_ratio),
			'max_force': sprung_mass * GRAVITY *
				SERVER_PHYSICS_MAX_SPRING_FORCE_FACTOR,
		})
	pseudo_contacts = []
	for side, x in (('left', -track_x), ('right', track_x)):
		for index in range(len(positions) - 1):
			pseudo_contacts.append({
				'side': side, 'kind': 'track', 'x': x, 'y': 0.0,
				'z': (positions[index] + positions[index + 1]) * 0.5,
				'penetration': TRACKS_PENETRATION,
			})
	# Centre-line belly points keep a ridge between both tracks from vanishing
	# between the ten spring queries.
	for index in range(len(positions) - 1):
		pseudo_contacts.append({
			'side': None, 'kind': 'body', 'x': 0.0, 'y': clearance,
			'z': (positions[index] + positions[index + 1]) * 0.5,
			'penetration': ALLOWED_PENETRATION,
		})
	raw_body_height = _optional_value(config, 'bodyHeight')
	if raw_body_height is None:
		# The inertia tensor below is a labelled trial projection either way,
		# so an unrefined body height uses the chassis bbox this port already
		# measured rather than reproducing the client's collision-box law.
		body_height = chassis_height
	else:
		body_height = _finite_number(
			raw_body_height, 'detailed chassis bodyHeight')
		if body_height <= 0.0:
			raise ValueError(
				'detailed chassis bodyHeight must be positive')
	raw_inertia_factors = _optional_value(config, 'hullInertiaFactors')
	if raw_inertia_factors is None:
		inertia_factors = TRIAL_HULL_INERTIA_FACTORS
	else:
		inertia_factors = _number_tuple(
			raw_inertia_factors, 'detailed chassis hullInertiaFactors')
		if len(inertia_factors) != 3 or any(
				value <= 0.0 for value in inertia_factors):
			raise ValueError(
				'hullInertiaFactors must contain three positive values')
	wheel_radius = _suspension_wheel_radius(chassis, config, step_z)
	pitch_inertia = max(
		1.0, mass * (body_height ** 2 + chassis_length ** 2) /
		12.0 * inertia_factors[0])
	roll_inertia = max(
		1.0, mass * (body_height ** 2 + width ** 2) /
		12.0 * inertia_factors[2])
	return {
		'mass': mass, 'width': width, 'length': chassis_length,
		'clearance': clearance, 'rest_length': rest_length,
		'static_compression': static_compression,
		'hard_ratio': hard_ratio,
		'contact_memory_distance': max(
			0.25, min(0.9, wheel_radius * 2.0)),
		'pitch_inertia': pitch_inertia,
		'roll_inertia': roll_inertia,
		'springs': tuple(springs),
		'pseudo_contacts': tuple(pseudo_contacts),
		'fixed_step': SERVER_PHYSICS_STEP,
		'constraint_iterations': SERVER_PHYSICS_CONSTRAINT_ITERATIONS,
	}


def suspension_world_points(params, position, yaw):
	'''Return one world x/z query point for each trial damper spring.'''
	x, unused_y, z = map(float, position)
	sine, cosine = math.sin(float(yaw)), math.cos(float(yaw))
	result = []
	for spring in params['springs']:
		local_x = float(spring['x'])
		local_z = float(spring['z'])
		result.append((
			x + cosine * local_x + sine * local_z,
			z - sine * local_x + cosine * local_z))
	return tuple(result)


def suspension_pseudo_world_points(params, position, yaw):
	'''Return world x/z query points for the twelve trial pseudo contacts.'''
	x, unused_y, z = map(float, position)
	sine, cosine = math.sin(float(yaw)), math.cos(float(yaw))
	result = []
	for contact in params.get('pseudo_contacts', ()):
		local_x = float(contact['x'])
		local_z = float(contact['z'])
		result.append((
			x + cosine * local_x + sine * local_z,
			z - sine * local_x + cosine * local_z))
	return tuple(result)


def ground_normal(gradient_x, gradient_z):
	'''Return the upward unit normal of ``y = gx*x + gz*z + c``.'''
	try:
		gradient_x = float(gradient_x)
		gradient_z = float(gradient_z)
	except (TypeError, ValueError, OverflowError):
		return None
	if any(math.isnan(value) or math.isinf(value)
			for value in (gradient_x, gradient_z)):
		return None
	# Scale before normalising so large but finite gradients cannot overflow
	# their squared length into a zero or non-finite up component.
	scale = max(1.0, abs(gradient_x), abs(gradient_z))
	local_x = gradient_x / scale
	local_y = 1.0 / scale
	local_z = gradient_z / scale
	length = math.sqrt(
		local_x * local_x + local_y * local_y + local_z * local_z)
	if length <= 0.0 or math.isnan(length) or math.isinf(length):
		return None
	normal = (-local_x / length, local_y / length, -local_z / length)
	if normal[1] <= 0.0:
		return None
	return normal


def sampled_ground_plane(front_y, rear_y, right_y, left_y, center_y,
		yaw, length, width, maximum_residual):
	'''Fit one ground plane from five actual suspension-height samples.

	The residual is measured across the fixed hull footprint. It is not an
	allowance paid once per simulation tick, so render cadence cannot grow it.
	'''
	if None in (front_y, rear_y, right_y, left_y, center_y):
		return None
	try:
		front_y = float(front_y)
		rear_y = float(rear_y)
		right_y = float(right_y)
		left_y = float(left_y)
		center_y = float(center_y)
		yaw = float(yaw)
		length = float(length)
		width = float(width)
		maximum_residual = float(maximum_residual)
	except (TypeError, ValueError, OverflowError):
		return None
	values = (front_y, rear_y, right_y, left_y, center_y,
		yaw, length, width, maximum_residual)
	if (any(math.isnan(value) or math.isinf(value) for value in values) or
			length <= 0.0 or width <= 0.0 or maximum_residual < 0.0):
		return None
	long_mid = (front_y + rear_y) * 0.5
	side_mid = (right_y + left_y) * 0.5
	residual = max(
		abs(long_mid - side_mid), abs(center_y - long_mid),
		abs(center_y - side_mid))
	if residual > maximum_residual:
		return None
	height_forward = (front_y - rear_y) / length
	height_right = (right_y - left_y) / width
	sine, cosine = math.sin(yaw), math.cos(yaw)
	gradient_x = height_forward * sine + height_right * cosine
	gradient_z = height_forward * cosine - height_right * sine
	normal = ground_normal(gradient_x, gradient_z)
	if normal is None:
		return None
	slope_tangent = math.hypot(height_forward, height_right)
	if math.isnan(slope_tangent) or math.isinf(slope_tangent):
		return None
	downhill_x = -gradient_x
	downhill_z = -gradient_z
	downhill_length = math.hypot(downhill_x, downhill_z)
	if downhill_length > 0.001:
		downhill_x /= downhill_length
		downhill_z /= downhill_length
	else:
		downhill_x = downhill_z = 0.0
	return {
		'center_y': center_y,
		'gradient_x': gradient_x,
		'gradient_z': gradient_z,
		'normal': normal,
		'pitch': -math.atan2(front_y - rear_y, length),
		# BigWorld applies YPR as yaw, pitch and then roll. Dividing by the
		# pitched right-axis length keeps this Euler pose on the fitted plane.
		'roll': math.atan2(
			height_right,
			math.hypot(1.0, height_forward)),
		'slope_tangent': slope_tangent,
		'up_cosine': normal[1],
		'downhill': (downhill_x, 0.0, downhill_z),
		'residual': residual,
	}


def suspension_ground_plane(params, ground_heights,
		maximum_residual=None):
	'''Fit terrain gradients from contacted springs, not body attitude.

	Suspension pitch and roll contain transient damper motion. Feeding that
	rocking back into slope grip would invent a hill on flat ground, so callers
	use this least-squares contact plane for terrain-only metadata.
	'''
	try:
		if len(ground_heights) != len(params['springs']):
			return None
	except (KeyError, TypeError):
		return None
	rows = []
	for index, spring in enumerate(params['springs']):
		ground = ground_heights[index]
		if ground is None:
			continue
		try:
			x = float(spring['x'])
			z = float(spring['z'])
			y = float(ground)
		except (KeyError, TypeError, ValueError):
			return None
		if any(math.isnan(value) or math.isinf(value)
				for value in (x, z, y)):
			return None
		rows.append((x, z, y))
	if len(rows) < 3:
		return None
	count = float(len(rows))
	mean_x = sum(row[0] for row in rows) / count
	mean_z = sum(row[1] for row in rows) / count
	mean_y = sum(row[2] for row in rows) / count
	xx = xz = zz = xy = zy = 0.0
	for x, z, y in rows:
		dx = x - mean_x
		dz = z - mean_z
		dy = y - mean_y
		xx += dx * dx
		xz += dx * dz
		zz += dz * dz
		xy += dx * dy
		zy += dz * dy
	determinant = xx * zz - xz * xz
	if determinant <= 1.0e-8:
		return None
	x_gradient = (xy * zz - zy * xz) / determinant
	z_gradient = (zy * xx - xy * xz) / determinant
	center_y = mean_y - x_gradient * mean_x - z_gradient * mean_z
	residual = max(abs(
		y - (center_y + x_gradient * x + z_gradient * z))
		for x, z, y in rows)
	if maximum_residual is not None:
		try:
			limit = max(0.0, float(maximum_residual))
		except (TypeError, ValueError):
			return None
		if residual > limit:
			return None
	return {
		'center_y': center_y,
		'x_gradient': x_gradient,
		'z_gradient': z_gradient,
		'max_residual': residual,
		'contact_count': len(rows),
	}


def suspension_world_ground_plane(params, ground_heights, position, yaw,
		maximum_residual=None):
	'''Fit one suspension plane in stable world-space coordinates.'''
	local_plane = suspension_ground_plane(
		params, ground_heights, maximum_residual)
	if local_plane is None:
		return None
	try:
		center_x = float(position[0])
		center_z = float(position[2])
		yaw = float(yaw)
	except (IndexError, TypeError, ValueError, OverflowError):
		return None
	if any(math.isnan(value) or math.isinf(value)
			for value in (center_x, center_z, yaw)):
		return None
	right_gradient = float(local_plane['x_gradient'])
	forward_gradient = float(local_plane['z_gradient'])
	sine, cosine = math.sin(yaw), math.cos(yaw)
	gradient_x = forward_gradient * sine + right_gradient * cosine
	gradient_z = forward_gradient * cosine - right_gradient * sine
	center_y = float(local_plane['center_y'])
	max_residual = float(local_plane['max_residual'])
	if any(math.isnan(value) or math.isinf(value) for value in (
			center_y, gradient_x, gradient_z, max_residual)):
		return None
	normal = ground_normal(gradient_x, gradient_z)
	if normal is None:
		return None
	return {
		'center_x': center_x,
		'center_z': center_z,
		'center_y': center_y,
		'gradient_x': gradient_x,
		'gradient_z': gradient_z,
		'normal': normal,
		'max_residual': max_residual,
		'contact_count': int(local_plane['contact_count']),
	}


def suspension_plane_height(plane, x, z):
	'''Project a world-space ground plane to one horizontal position.'''
	if not isinstance(plane, dict):
		return None
	try:
		values = (
			float(plane['center_x']), float(plane['center_z']),
			float(plane['center_y']), float(plane['gradient_x']),
			float(plane['gradient_z']), float(x), float(z))
	except (KeyError, TypeError, ValueError, OverflowError):
		return None
	if any(math.isnan(value) or math.isinf(value) for value in values):
		return None
	center_x, center_z, center_y, gradient_x, gradient_z, x, z = values
	height = (center_y + gradient_x * (x - center_x) +
		gradient_z * (z - center_z))
	if math.isnan(height) or math.isinf(height):
		return None
	return height


def suspension_support_vertical_velocity(plane, velocity_x, velocity_z):
	'''Return the vertical constraint speed induced by horizontal travel.

	The terrain is stationary in world space, but its height under a moving
	vehicle changes at ``gradient dot horizontal velocity``.  Keep this input
	geometric: the body's observed height delta is deliberately not accepted,
	so a suspension correction cannot feed back as next tick's support speed.
	'''
	if not isinstance(plane, dict):
		return None
	try:
		values = (
			float(plane['gradient_x']), float(plane['gradient_z']),
			float(velocity_x), float(velocity_z))
	except (KeyError, TypeError, ValueError, OverflowError):
		return None
	if any(math.isnan(value) or math.isinf(value) for value in values):
		return None
	gradient_x, gradient_z, velocity_x, velocity_z = values
	return gradient_x * velocity_x + gradient_z * velocity_z


def landing_impact_speed(world_velocity, support_normal):
	'''Return closing speed along one trustworthy upward support normal.

	If the plane normal or either horizontal velocity component is unavailable,
	the established vertical-only fall observation remains authoritative.
	'''
	try:
		velocity_y = float(world_velocity[1])
	except (IndexError, TypeError, ValueError, OverflowError):
		return 0.0
	vertical = (max(0.0, -velocity_y)
		if not math.isnan(velocity_y) and not math.isinf(velocity_y) else 0.0)
	try:
		velocity_x = float(world_velocity[0])
		velocity_z = float(world_velocity[2])
	except (IndexError, TypeError, ValueError, OverflowError):
		return vertical
	if support_normal is None:
		return vertical
	try:
		normal_x = float(support_normal[0])
		normal_y = float(support_normal[1])
		normal_z = float(support_normal[2])
	except (IndexError, TypeError, ValueError, OverflowError):
		return vertical
	components = (normal_x, normal_y, normal_z)
	if (any(math.isnan(value) or math.isinf(value)
			for value in components) or normal_y <= 0.0):
		return vertical
	scale = max(abs(normal_x), abs(normal_y), abs(normal_z))
	if scale <= 0.0:
		return vertical
	scaled_x = normal_x / scale
	scaled_y = normal_y / scale
	scaled_z = normal_z / scale
	length = math.sqrt(
		scaled_x * scaled_x + scaled_y * scaled_y + scaled_z * scaled_z)
	if length <= 0.0 or math.isnan(length) or math.isinf(length):
		return vertical
	if any(math.isnan(value) or math.isinf(value) for value in (
			velocity_x, velocity_y, velocity_z)):
		return vertical
	closing = -(
		velocity_x * scaled_x + velocity_y * scaled_y +
		velocity_z * scaled_z) / length
	if math.isnan(closing) or math.isinf(closing):
		return vertical
	return max(0.0, closing)


def suspension_ground_planes_continuous(
		previous, current, start_position, end_position,
		height_tolerance, gradient_tolerance):
	'''Reject a layer jump or slope break between two world-space planes.'''
	try:
		start_x = float(start_position[0])
		start_z = float(start_position[2])
		end_x = float(end_position[0])
		end_z = float(end_position[2])
		height_tolerance = float(height_tolerance)
		gradient_tolerance = float(gradient_tolerance)
	except (IndexError, TypeError, ValueError, OverflowError):
		return False
	values = (start_x, start_z, end_x, end_z,
		height_tolerance, gradient_tolerance)
	if (any(math.isnan(value) or math.isinf(value) for value in values) or
			height_tolerance < 0.0 or gradient_tolerance < 0.0):
		return False
	previous_start = suspension_plane_height(previous, start_x, start_z)
	current_start = suspension_plane_height(current, start_x, start_z)
	previous_end = suspension_plane_height(previous, end_x, end_z)
	current_end = suspension_plane_height(current, end_x, end_z)
	if any(value is None for value in (
			previous_start, current_start, previous_end, current_end)):
		return False
	if (abs(previous_start - current_start) > height_tolerance or
			abs(previous_end - current_end) > height_tolerance):
		return False
	try:
		difference_x = (
			float(current['gradient_x']) - float(previous['gradient_x']))
		difference_z = (
			float(current['gradient_z']) - float(previous['gradient_z']))
	except (KeyError, TypeError, ValueError, OverflowError):
		return False
	return (difference_x * difference_x + difference_z * difference_z <=
		gradient_tolerance * gradient_tolerance + 1.0e-12)


def suspension_path_probe_fractions(distance):
	'''Return a fixed-budget set of interior continuity checkpoints.'''
	try:
		distance = max(0.0, float(distance))
	except (TypeError, ValueError):
		return ()
	steps = min(SUSPENSION_PATH_MAX_PROBES, max(
		0, int(math.ceil(distance / SUSPENSION_PATH_PROBE_SPACING)) - 1))
	return tuple(
		float(index + 1) / float(steps + 1) for index in range(steps))


def suspension_support_allowed(height, normal_y, flat_maximum_y=None):
	'''Validate spring travel and the incline-only penetration extension.'''
	height = float(height)
	normal_y = float(normal_y)
	if (math.isnan(height) or math.isinf(height) or
			math.isnan(normal_y) or math.isinf(normal_y)):
		return False
	if normal_y <= 0.5:
		return False
	if (flat_maximum_y is None or
			height <= float(flat_maximum_y) + 0.01):
		return True
	return normal_y < 0.995


def retained_ground_contact(point, ground, memory, maximum_distance,
		support_gradient=None):
	'''Keep one recent ground contact across a short wheel-scale gap.'''
	try:
		x, z = float(point[0]), float(point[1])
		distance = max(0.0, float(maximum_distance))
	except (IndexError, TypeError, ValueError, OverflowError):
		return None, None
	if any(math.isnan(value) or math.isinf(value)
			for value in (x, z, distance)):
		return None, None
	if ground is not None:
		try:
			ground = float(ground)
		except (TypeError, ValueError, OverflowError):
			return None, None
		if math.isnan(ground) or math.isinf(ground):
			return None, None
		return ground, (x, z, ground, x, z, 0.0, False)
	if memory is None:
		return None, None
	try:
		origin_x = float(memory[0])
		origin_z = float(memory[1])
		height = float(memory[2])
		last_x = float(memory[3])
		last_z = float(memory[4])
		travelled = max(0.0, float(memory[5]))
		last_query_missed = bool(memory[6])
		values = (origin_x, origin_z, height, last_x, last_z, travelled)
		if any(math.isnan(value) or math.isinf(value) for value in values):
			return None, None
		step_x = x - last_x
		step_z = z - last_z
		step_sq = step_x * step_x + step_z * step_z
		# One miss covers a transient query hole. Repeating that miss without
		# moving proves the support vanished and must release it.
		if step_sq <= 1.0e-10:
			if last_query_missed:
				return None, None
			return height, (
				origin_x, origin_z, height, x, z, travelled, True)
		dx = x - origin_x
		dz = z - origin_z
		travelled += math.sqrt(step_sq)
		if (dx * dx + dz * dz <= distance * distance + 1.0e-12 and
				travelled <= distance + 1.0e-9):
			retained_height = height
			if support_gradient is not None:
				gradient_x = float(support_gradient[0])
				gradient_z = float(support_gradient[1])
				if any(math.isnan(value) or math.isinf(value)
						for value in (gradient_x, gradient_z)):
					return None, None
				# A remembered contact belongs to its original world point.  When
				# the point moves across a short query gap, carry the last trusted
				# terrain plane with it instead of flattening that plane at the old
				# world height and corrupting the next fit.
				retained_height += gradient_x * dx + gradient_z * dz
			return retained_height, (
				origin_x, origin_z, height, x, z, travelled, True)
	except (IndexError, TypeError, ValueError, OverflowError):
		pass
	return None, None


def _rigid_point_height(state, point):
	pitch = float(state.get('pitch', 0.0))
	roll = float(state.get('roll', 0.0))
	return (float(state['height']) + float(point.get('y', 0.0)) -
		float(point['z']) * math.sin(pitch) +
		float(point['x']) * math.sin(roll))


def _rigid_point_velocity(state, point):
	pitch = float(state.get('pitch', 0.0))
	roll = float(state.get('roll', 0.0))
	return (float(state.get('vertical_velocity', 0.0)) -
		float(point['z']) * math.cos(pitch) *
		float(state.get('pitch_velocity', 0.0)) +
		float(point['x']) * math.cos(roll) *
		float(state.get('roll_velocity', 0.0)))


def _spring_height(state, spring):
	return _rigid_point_height(state, spring)


def _spring_point_velocity(state, spring):
	return _rigid_point_velocity(state, spring)


def _suspension_contact_keys(params, state, ground_heights,
		pseudo_ground_heights):
	'''Return final geometric contacts, separated from within-step impacts.'''
	contacts = set()
	left = set()
	right = set()
	for index, spring in enumerate(params['springs']):
		ground = ground_heights[index]
		if ground is None:
			continue
		compression = (
			spring['static_compression'] + float(ground) -
			_spring_height(state, spring))
		if compression <= 0.0:
			continue
		key = ('spring', index)
		contacts.add(key)
		if spring['side'] == 'left':
			left.add(key)
		elif spring['side'] == 'right':
			right.add(key)
	for index, contact in enumerate(params.get('pseudo_contacts', ())):
		ground = pseudo_ground_heights[index]
		if ground is None:
			continue
		gap = float(ground) - _rigid_point_height(state, contact)
		if gap < -CONTACT_PENETRATION:
			continue
		key = ('pseudo', index)
		contacts.add(key)
		if contact.get('side') == 'left':
			left.add(key)
		elif contact.get('side') == 'right':
			right.add(key)
	return contacts, left, right


def suspension_limit_excess(params, state, ground_heights):
	'''Return the largest compression beyond a projected hard limit.'''
	maximum = 0.0
	for index, spring in enumerate(params['springs']):
		ground = ground_heights[index]
		if ground is None:
			continue
		compression = (
			spring['static_compression'] + float(ground) -
			_spring_height(state, spring))
		maximum = max(maximum, compression - spring['max_compression'])
	return maximum


def _project_suspension_limits(params, state, ground_heights,
		pseudo_ground_heights=None, support_vertical_velocity=0.0,
		support_height_offset=0.0):
	'''Resolve hard limits with an order-independent worst-contact projection.'''
	inv_mass = 1.0 / params['mass']
	inv_pitch = 1.0 / params['pitch_inertia']
	inv_roll = 1.0 / params['roll_inertia']
	constraints = []
	for index, spring in enumerate(params['springs']):
		ground = ground_heights[index]
		if ground is not None:
			constraints.append((
				('spring', index), spring,
				float(ground) + support_height_offset,
				float(spring['max_compression']) -
				float(spring['static_compression'])))
	pseudo_ground_heights = pseudo_ground_heights or ()
	for index, contact in enumerate(params.get('pseudo_contacts', ())):
		ground = (pseudo_ground_heights[index]
			if index < len(pseudo_ground_heights) else None)
		if ground is not None:
			constraints.append((
				('pseudo', index), contact,
				float(ground) + support_height_offset,
				float(contact.get('penetration', ALLOWED_PENETRATION))))
	touched = set()
	for unused_iteration in range(params['constraint_iterations']):
		rows = []
		maximum_excess = 0.0
		for key, point, ground, penetration in constraints:
			excess = (
				ground - _rigid_point_height(state, point) - penetration)
			if excess <= 1.0e-5:
				continue
			touched.add(key)
			pitch_grad = float(point['z']) * math.cos(state['pitch'])
			roll_grad = -float(point['x']) * math.cos(state['roll'])
			denominator = (inv_mass + pitch_grad * pitch_grad * inv_pitch +
				roll_grad * roll_grad * inv_roll)
			if denominator <= 1.0e-12:
				continue
			rows.append((
				point, excess, pitch_grad, roll_grad, denominator))
			maximum_excess = max(maximum_excess, excess)
		if not rows:
			break
		# Project only the equally deepest contacts. Averaging every violated
		# half-space makes a severe contact advance by roughly 1/N per pass and
		# leaves centimetres of penetration at the fixed iteration budget. The
		# maximum-residual (Motzkin) projection converges quickly; grouping ties
		# and using fsum keeps symmetric axles/tracks and input order unbiased.
		tie_tolerance = max(1.0e-10, maximum_excess * 1.0e-8)
		selected = tuple(row for row in rows
			if maximum_excess - row[1] <= tie_tolerance)
		position_height = []
		position_pitch = []
		position_roll = []
		velocity_height = []
		velocity_pitch = []
		velocity_roll = []
		for point, excess, pitch_grad, roll_grad, denominator in selected:
			point_velocity = (
				_rigid_point_velocity(state, point) -
				support_vertical_velocity)
			if point_velocity < 0.0:
				velocity_impulse = -point_velocity / denominator
				velocity_height.append(velocity_impulse * inv_mass)
				velocity_pitch.append(
					-velocity_impulse * pitch_grad * inv_pitch)
				velocity_roll.append(
					-velocity_impulse * roll_grad * inv_roll)
			else:
				velocity_height.append(0.0)
				velocity_pitch.append(0.0)
				velocity_roll.append(0.0)
			impulse = excess / denominator
			position_height.append(impulse * inv_mass)
			position_pitch.append(-impulse * pitch_grad * inv_pitch)
			position_roll.append(-impulse * roll_grad * inv_roll)
		scale = 1.0 / float(len(selected))
		state['vertical_velocity'] += math.fsum(velocity_height) * scale
		state['pitch_velocity'] += math.fsum(velocity_pitch) * scale
		state['roll_velocity'] += math.fsum(velocity_roll) * scale
		state['height'] += math.fsum(position_height) * scale
		state['pitch'] += math.fsum(position_pitch) * scale
		state['roll'] += math.fsum(position_roll) * scale
	# The nonlinear sine pose can leave a tiny residual after the bounded
	# angular iterations. A final conservative heave is itself a real position
	# projection (not a reported-value clamp) and satisfies every half-space at
	# once without adding pitch/roll bias or another iterative sweep.
	remaining_excess = 0.0
	for key, point, ground, penetration in constraints:
		excess = ground - _rigid_point_height(state, point) - penetration
		if excess > 0.0:
			remaining_excess = max(remaining_excess, excess)
			touched.add(key)
	if remaining_excess > 0.0:
		state['height'] += remaining_excess + 1.0e-9
	return touched


def damper_suspension_step(params, state, ground_heights, dt,
		pseudo_ground_heights=None, support_vertical_velocity=0.0):
	'''Advance the contrib ten-spring heave/pitch/roll trial.'''
	if not isinstance(state, dict):
		raise ValueError('suspension state must be a dictionary')
	if len(ground_heights) != len(params['springs']):
		raise ValueError('suspension ground sample count mismatch')
	pseudo_contacts = params.get('pseudo_contacts', ())
	if pseudo_ground_heights is None:
		pseudo_ground_heights = (None,) * len(pseudo_contacts)
	if len(pseudo_ground_heights) != len(pseudo_contacts):
		raise ValueError('pseudo-contact ground sample count mismatch')
	try:
		support_vertical_velocity = float(support_vertical_velocity)
	except (TypeError, ValueError, OverflowError):
		raise ValueError('support vertical velocity is invalid')
	if (math.isnan(support_vertical_velocity) or
			math.isinf(support_vertical_velocity)):
		raise ValueError('support vertical velocity is not finite')
	result = {
		'height': float(state.get('height', 0.0)),
		'vertical_velocity': float(state.get('vertical_velocity', 0.0)),
		'pitch': float(state.get('pitch', 0.0)),
		'pitch_velocity': float(state.get('pitch_velocity', 0.0)),
		'roll': float(state.get('roll', 0.0)),
		'roll_velocity': float(state.get('roll_velocity', 0.0)),
	}
	total = max(0.0, min(0.2, float(dt)))
	steps = min(SERVER_PHYSICS_MAX_SUBSTEPS, max(
		1, int(math.ceil(total / params['fixed_step']))))
	step = total / float(steps) if steps else 0.0
	touched_keys = set()
	maximum_compression = 0.0
	impact_speed = None
	contact_transition_seen = False
	vertical_acceleration = 0.0
	pitch_acceleration = 0.0
	roll_acceleration = 0.0
	for substep_index in range(steps):
		# Horizontal travel moves the reduced heave constraint from the
		# previous sample position to the current one.  Forces are evaluated at
		# the start of this semi-implicit substep; its hard projection is
		# evaluated at the end.  Keeping those two support poses distinct avoids
		# presenting a complete one-substep rise as instantaneous compression.
		support_force_offset = -support_vertical_velocity * max(
			0.0, total - step * float(substep_index))
		support_projection_offset = -support_vertical_velocity * max(
			0.0, total - step * float(substep_index + 1))
		total_force = -params['mass'] * GRAVITY
		pitch_torque = 0.0
		roll_torque = 0.0
		substep_contacts = 0
		substep_vertical_speed = result['vertical_velocity']
		substep_relative_speed = (
			substep_vertical_speed - support_vertical_velocity)
		for index, spring in enumerate(params['springs']):
			ground = ground_heights[index]
			if ground is None:
				continue
			ground = float(ground) + support_force_offset
			compression = (
				spring['static_compression'] + ground -
				_spring_height(result, spring))
			if compression <= 0.0:
				continue
			maximum_compression = max(maximum_compression, compression)
			key = ('spring', index)
			touched_keys.add(key)
			substep_contacts += 1
			if (not contact_transition_seen and
					substep_relative_speed < 0.0):
				impact_speed = (substep_vertical_speed
					if impact_speed is None else
					min(impact_speed, substep_vertical_speed))
			relative_point_velocity = (
				_spring_point_velocity(result, spring) -
				support_vertical_velocity)
			force = (spring['stiffness'] * compression -
				spring['damping'] * relative_point_velocity)
			if compression > spring['max_compression']:
				excess = compression - spring['max_compression']
				force += spring['stiffness'] * 8.0 * excess * (
					1.0 + excess / max(spring['max_compression'], 0.01))
			force = max(0.0, min(spring['max_force'], force))
			total_force += force
			pitch_torque -= float(spring['z']) * math.cos(
				result['pitch']) * force
			roll_torque += float(spring['x']) * math.cos(
				result['roll']) * force
		for index, contact in enumerate(pseudo_contacts):
			ground = pseudo_ground_heights[index]
			if ground is None:
				continue
			ground = float(ground) + support_force_offset
			gap = ground - _rigid_point_height(result, contact)
			if gap < -CONTACT_PENETRATION:
				continue
			key = ('pseudo', index)
			touched_keys.add(key)
			substep_contacts += 1
			if (not contact_transition_seen and
					substep_relative_speed < 0.0):
				impact_speed = (substep_vertical_speed
					if impact_speed is None else
					min(impact_speed, substep_vertical_speed))
		if substep_contacts:
			contact_transition_seen = True
		if substep_contacts == 0:
			decay = math.exp(-AIRBORNE_ANGULAR_DAMPING * step)
			result['pitch_velocity'] = max(
				-AIRBORNE_ANGULAR_SPEED_LIMIT,
				min(AIRBORNE_ANGULAR_SPEED_LIMIT,
					result['pitch_velocity'])) * decay
			result['roll_velocity'] = max(
				-AIRBORNE_ANGULAR_SPEED_LIMIT,
				min(AIRBORNE_ANGULAR_SPEED_LIMIT,
					result['roll_velocity'])) * decay
		vertical_acceleration = total_force / params['mass']
		pitch_acceleration = pitch_torque / params['pitch_inertia']
		roll_acceleration = roll_torque / params['roll_inertia']
		result['vertical_velocity'] += vertical_acceleration * step
		result['pitch_velocity'] += pitch_acceleration * step
		result['roll_velocity'] += roll_acceleration * step
		result['height'] += result['vertical_velocity'] * step
		result['pitch'] += result['pitch_velocity'] * step
		result['roll'] += result['roll_velocity'] * step
		pre_projection_speed = result['vertical_velocity']
		projected = _project_suspension_limits(
			params, result, ground_heights, pseudo_ground_heights,
			support_vertical_velocity, support_projection_offset)
		if projected:
			if (not contact_transition_seen and
					pre_projection_speed -
					support_vertical_velocity < 0.0):
				impact_speed = (pre_projection_speed if impact_speed is None else
					min(impact_speed, pre_projection_speed))
			contact_transition_seen = True
			touched_keys.update(projected)
	contact_keys, left_contact_keys, right_contact_keys = \
		_suspension_contact_keys(
			params, result, ground_heights, pseudo_ground_heights)
	contact_count = len(contact_keys)
	# A contact may begin during the final integration after the last force and
	# hard-limit query. The final-pose query is then the first proof available
	# in this outer step, so preserve its post-gravity/pre-contact CoM speed for
	# the runtime's airborne-to-grounded landing gate.
	if contact_keys and not contact_transition_seen:
		if (result['vertical_velocity'] -
				support_vertical_velocity < 0.0):
			impact_speed = result['vertical_velocity']
		contact_transition_seen = True
	touched_keys.update(contact_keys)
	if contact_count:
		if (abs(vertical_acceleration) < FREEZE_ACCEL_EPSILON and
				abs(result['vertical_velocity'] -
					support_vertical_velocity) < FREEZE_VEL_EPSILON):
			result['vertical_velocity'] = support_vertical_velocity
		if (abs(pitch_acceleration) < FREEZE_ANG_ACCEL_EPSILON and
				abs(result['pitch_velocity']) < FREEZE_ANG_VEL_EPSILON):
			result['pitch_velocity'] = 0.0
		if (abs(roll_acceleration) < FREEZE_ANG_ACCEL_EPSILON and
				abs(result['roll_velocity']) < FREEZE_ANG_VEL_EPSILON):
			result['roll_velocity'] = 0.0
	result['contact_count'] = contact_count
	result['airborne'] = contact_count == 0
	result['left_flying'] = not bool(left_contact_keys)
	result['right_flying'] = not bool(right_contact_keys)
	result['contacted_this_step'] = bool(touched_keys)
	result['touched_contact_count'] = len(touched_keys)
	result['impact_speed'] = impact_speed
	result['max_compression'] = maximum_compression
	result['max_limit_excess'] = max(
		0.0, suspension_limit_excess(params, result, ground_heights))
	for index, contact in enumerate(pseudo_contacts):
		ground = pseudo_ground_heights[index]
		if ground is None:
			continue
		result['max_limit_excess'] = max(
			result['max_limit_excess'], float(ground) -
			_rigid_point_height(result, contact) -
			float(contact.get('penetration', ALLOWED_PENETRATION)))
	return result


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


def lateral_slope_grip(normal_y):
	'''Return the contrib trial's low-cost side-grip interpolation.'''
	normal_y = max(0.0, min(1.0, float(normal_y)))
	if normal_y >= SLOPE_GRIP_SDW_FULL_Y:
		return SLOPE_GRIP_SDW_FULL
	if normal_y <= SLOPE_GRIP_SDW_MIN_Y:
		return SLOPE_GRIP_SDW_MIN
	span = SLOPE_GRIP_SDW_FULL_Y - SLOPE_GRIP_SDW_MIN_Y
	progress = (normal_y - SLOPE_GRIP_SDW_MIN_Y) / span
	return (SLOPE_GRIP_SDW_MIN + progress *
		(SLOPE_GRIP_SDW_FULL - SLOPE_GRIP_SDW_MIN))


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


@observed('physics.longitudinal')
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


@observed('physics.traverse')
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
	'''Advance the existing passive slope slide along the fall line.'''
	theta = math.atan(slope_tan)
	# Lateral fall-line hold: tracks resist SIDEWAYS slip only up to SLIDE_HOLD_TAN
	# (gentler than the brake COHESION), so a hull cannot perch across a steep bank
	# it is only leaning on. Past it, slip down the excess.
	if slope_tan <= SLIDE_HOLD_TAN:
		cur -= COHESION * GRAVITY * dt   # holds: kill any residual slide fast
		return cur if cur > 0.0 else 0.0
	# accelerate by the grip-excess, minus a track drag proportional to slide
	# speed -> settles at a natural terrain-dependent terminal, not a flat cap.
	cur += (GRAVITY * (
		math.sin(theta) - SLIDE_HOLD_TAN * math.cos(theta)) -
		SLIDE_DRAG * cur) * dt
	if cur < 0.0:
		cur = 0.0
	elif cur > SLIDE_MAX:
		cur = SLIDE_MAX
	return cur


def suspension_slope_slide_speed(cur, slope_tan, dt):
	'''Apply the contrib trial's unproved lateral-grip projection.'''
	theta = math.atan(slope_tan)
	hold_tangent = (
		SLIDE_HOLD_TAN * lateral_slope_grip(math.cos(theta)))
	if slope_tan <= hold_tangent:
		cur -= COHESION * GRAVITY * dt
		return cur if cur > 0.0 else 0.0
	cur += (GRAVITY * (
		math.sin(theta) - hold_tangent * math.cos(theta)) -
		SLIDE_DRAG * cur) * dt
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
