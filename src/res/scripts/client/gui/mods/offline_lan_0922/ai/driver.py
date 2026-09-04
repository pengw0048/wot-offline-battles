# -*- coding: utf-8 -*-
"""Engine-free short-range driver for offline battle bots.

The strategic director supplies a waypoint.  Callers supply ``direction_clear``
for the current collision/terrain query; this module chooses only throttle and
steering, so it is safe to exercise outside the BigWorld client.
"""

import math


WAYPOINT_ARRIVAL_RADIUS = 1.5
TRAFFIC_WAIT_LEASE_SECONDS = 1.5
# First avoidance branch of the steering fan.
FIRST_CANDIDATE_OFFSET = 0.42
# Fifteen-degree circular buckets centre both the +/-pi seam and cardinal yaws.
FAILED_YAW_BUCKET_COUNT = 24


def _angle_delta(target, current):
	value = float(target) - float(current)
	while value > math.pi:
		value -= math.pi * 2.0
	while value < -math.pi:
		value += math.pi * 2.0
	return value


def _yaw_to(first, second):
	return math.atan2(float(second[0]) - float(first[0]),
	                  float(second[2]) - float(first[2]))


def _distance(first, second):
	dx = float(first[0]) - float(second[0])
	dz = float(first[2]) - float(second[2])
	return math.sqrt(dx * dx + dz * dz)


def _identity_phase(bot_id):
	"""Stable 0..1 value without Python's randomized string hash."""
	text = str(bot_id)
	value = 0
	for char in text:
		value = (value * 33 + ord(char)) & 0x7fffffff
	return float(value % 997) / 997.0


def _component_value(component, name, default=None):
	if component is None:
		return default
	if isinstance(component, dict):
		return component.get(name, default)
	return getattr(component, name, default)


def gun_yaw_limits(descriptor):
	"""Return installed gun yaw limits in radians plus a limited-arc flag."""
	gun = _component_value(descriptor, 'gun')
	turret = _component_value(descriptor, 'turret')
	limits = _component_value(gun, 'turretYawLimits')
	if limits is None:
		limits = _component_value(turret, 'yawLimits')
	minimum = -math.pi
	maximum = math.pi
	try:
		minimum = float(limits[0])
		maximum = float(limits[1])
		if abs(minimum) > math.pi + 0.1 or abs(maximum) > math.pi + 0.1:
			minimum = math.radians(minimum)
			maximum = math.radians(maximum)
	except Exception:
		minimum = -math.pi
		maximum = math.pi
	limited = not (minimum <= -math.pi + 0.1 and maximum >= math.pi - 0.1)
	return minimum, maximum, limited


def combat_hull_aim(hull_yaw, target_yaw, minimum_yaw, maximum_yaw,
		turn, throttle, recovery_mode, has_target=True):
	"""Turn a limited-traverse hull until its gun can physically bear."""
	if not has_target or recovery_mode in ('avoid', 'blocked', 'reverse_turn',
			'pivot_recovery'):
		return float(turn), float(throttle), False
	limited = not (float(minimum_yaw) <= -math.pi + 0.1 and
	               float(maximum_yaw) >= math.pi - 0.1)
	if not limited:
		return float(turn), float(throttle), False
	relative = _angle_delta(target_yaw, hull_yaw)
	if float(minimum_yaw) + 0.04 <= relative <= float(maximum_yaw) - 0.04:
		return float(turn), float(throttle), False
	# Rotate before the physics step. The former post-physics velocity write was
	# overwritten by LocalDriver on the next frame and never moved the hull.
	hull_delta = _angle_delta(target_yaw, hull_yaw)
	aim_turn = max(-1.0, min(1.0, hull_delta / 0.58))
	return aim_turn, 0.0, True


def gun_aligned(target_yaw, hull_yaw, turret_yaw, desired_pitch, gun_pitch,
		yaw_tolerance=0.06, pitch_tolerance=0.04):
	"""Require both rendered traverse and elevation to settle before firing."""
	abs_yaw = float(hull_yaw) + float(turret_yaw)
	yaw_error = abs(_angle_delta(target_yaw, abs_yaw))
	pitch_error = abs(float(desired_pitch) - float(gun_pitch))
	return (yaw_error <= float(yaw_tolerance) and
	        pitch_error <= float(pitch_tolerance))


def barrel_direction(yaw, pitch):
	"""Unit direction for BigWorld's negative-pitch-is-up convention."""
	horizontal = math.cos(float(pitch))
	return (math.sin(float(yaw)) * horizontal,
	        -math.sin(float(pitch)),
	        math.cos(float(yaw)) * horizontal)


class LocalDriver(object):
	"""Stateful local steering, keyed only by bot id.

	``direction_clear(absolute_yaw)`` must return whether a short vehicle-length
	segment in that direction is drivable.  It may raise; a failed probe is
	treated as blocked.
	"""
	_CANDIDATE_OFFSETS = (
		0.0, FIRST_CANDIDATE_OFFSET, -FIRST_CANDIDATE_OFFSET,
		0.78, -0.78, 1.18, -1.18, 1.55, -1.55)
	gun_yaw_limits = staticmethod(gun_yaw_limits)
	combat_hull_aim = staticmethod(combat_hull_aim)
	gun_aligned = staticmethod(gun_aligned)
	barrel_direction = staticmethod(barrel_direction)

	def __init__(self, stuck_seconds=1.8, recovery_seconds=0.85,
			separation_radius=12.0, failure_ttl=2.0):
		self.stuck_seconds = max(0.4, float(stuck_seconds))
		self.recovery_seconds = max(0.25, float(recovery_seconds))
		self.separation_radius = max(2.0, float(separation_radius))
		self.failure_ttl = max(0.25, float(failure_ttl))
		self.states = {}

	@staticmethod
	def resolve_order_positions(position, aim_position, move_position, face_position):
		"""Resolve optional tactical targets without mistaking travel for a hold."""
		stop_without_route = aim_position is None and move_position is None
		if stop_without_route:
			aim_position = position
		elif aim_position is None:
			# Server route orders intentionally omit an aim target until an enemy is
			# spotted.  Use the route target for facing, but do not apply idle braking.
			aim_position = move_position
		if move_position is None:
			move_position = aim_position
		if face_position is None:
			face_position = move_position
		return aim_position, move_position, face_position, stop_without_route

	def forget(self, bot_id):
		self.states.pop(bot_id, None)

	def wait_for_traffic(self, bot_id, elapsed=None):
		"""Suppress brief right-of-way waits without masking a deadlock forever.

		``elapsed`` is the physical contact interval this lease covers. Callers
		outside ``drive`` must supply it because ``last_step`` is refreshed only
		when the planner runs.
		"""
		state = self.states.get(bot_id)
		if state is None:
			return False
		state['traffic_waiting'] = True
		if elapsed is None:
			elapsed = state.get('last_step', 0.0)
		state['traffic_wait_time'] += max(0.0, float(elapsed))
		if state['traffic_wait_time'] <= TRAFFIC_WAIT_LEASE_SECONDS:
			state['stuck_time'] = 0.0
			state['recovery_time'] = 0.0
		return True

	def _state(self, bot_id, position):
		state = self.states.get(bot_id)
		if state is None:
			phase = _identity_phase(bot_id)
			state = {
				'last_position': (float(position[0]), float(position[2])),
				'stuck_time': 0.0,
				'recovery_time': 0.0,
				'recovery_count': 0,
				'steering_yaw': None,
				'steering_age': 999.0,
				'plan_age': 999.0,
				'phase': phase,
				'clock': 0.0,
				'failed_yaws': {},
				'escape_side': 0.0,
				'escape_side_until': 0.0,
				'last_desired_yaw': None,
				'last_heading_error': None,
				'traffic_waiting': False,
				'traffic_wait_time': 0.0,
				'last_step': 0.0,
				'braking_target': None,
			}
			self.states[bot_id] = state
		return state

	def _yaw_key(self, yaw):
		turn = math.pi * 2.0
		from_anchor = (float(yaw) + math.pi) % turn
		bucket = int(math.floor(
			from_anchor * FAILED_YAW_BUCKET_COUNT / turn + 0.5))
		return bucket % FAILED_YAW_BUCKET_COUNT

	def remember_failure(self, bot_id, yaw, ttl=None):
		"""Temporarily penalize a direction after a caller-observed bad path.

		Use this when a terrain probe was clear but later movement establishes that
		the direction is a ditch, steep lip, or another unusable local route.
		"""
		state = self.states.get(bot_id)
		if state is None:
			return
		if ttl is None:
			ttl = self.failure_ttl
		ttl = max(0.1, float(ttl))
		state['failed_yaws'][self._yaw_key(yaw)] = state['clock'] + ttl
		desired = state.get('last_desired_yaw')
		offset = _angle_delta(yaw, desired) if desired is not None else 0.0
		if abs(offset) >= 0.10:
			side = 1.0 if offset > 0.0 else -1.0
		else:
			# Adjacent ids choose opposite initial sides, then keep that side while
			# widening the escape angle. This avoids left/right grinding at a broad
			# obstacle without turning the finite failure TTL into a permanent bias.
			side = 1.0 if (int(state['phase'] * 997.0 + 0.5) & 1) else -1.0
		state['escape_side'] = side
		state['escape_side_until'] = (
			state['clock'] + min(2.0, max(0.8, ttl)))
		state['steering_yaw'] = None
		state['plan_age'] = 999.0

	def _failure_penalty(self, state, yaw):
		key = self._yaw_key(yaw)
		expires = state['failed_yaws'].get(key)
		if expires is None:
			return 0.0
		if expires <= state['clock']:
			state['failed_yaws'].pop(key, None)
			return 0.0
		return 3.0 + (expires - state['clock']) / self.failure_ttl

	def _prune_failures(self, state):
		failed = state['failed_yaws']
		for key, expires in list(failed.items()):
			if expires <= state['clock']:
				failed.pop(key, None)
		if len(failed) > 32:
			ordered = sorted(failed.items(), key=lambda item: item[1])
			for key, unused in ordered[:len(failed) - 32]:
				failed.pop(key, None)

	def _neighbour_position(self, neighbour):
		if isinstance(neighbour, dict):
			return neighbour.get('position') or neighbour.get('pos')
		return neighbour

	def _separation_yaw(self, position, current_yaw, neighbours,
			half_length, half_width):
		"""Return an escape heading only for hulls that already overlap.

		The simultaneous contact solver handles impending collisions. Treating
		every tank inside a broad radius as an emergency made harmless side-by-side
		traffic continually override the route and reconsider its steering.
		"""
		push_x = 0.0
		push_z = 0.0
		for neighbour in neighbours or ():
			other = self._neighbour_position(neighbour)
			if other is None:
				continue
			try:
				if _distance(position, other) > 20.0:
					continue
			except Exception:
				continue
			try:
				if abs(float(other[1]) - float(position[1])) > 5.0:
					continue
			except Exception:
				pass
			try:
				dx = float(position[0]) - float(other[0])
				dz = float(position[2]) - float(other[2])
				dist = math.sqrt(dx * dx + dz * dz)
			except Exception:
				continue
			if dist < 0.05 or dist >= self.separation_radius:
				continue
			other_yaw = 0.0
			other_length = half_length
			other_width = half_width
			if isinstance(neighbour, dict):
				other_yaw = float(neighbour.get('yaw', 0.0) or 0.0)
				other_length = float(neighbour.get('half_length', half_length) or half_length)
				other_width = float(neighbour.get('half_width', half_width) or half_width)
			# Existing overlap belongs to the physical hull pose. The route heading
			# is only a future steering candidate and cannot rotate that pose early.
			if not self._obb_overlap(
					position, current_yaw, half_length + 0.20, half_width + 0.20,
					other, other_yaw, other_length + 0.20, other_width + 0.20):
				continue
			weight = max(0.15, (self.separation_radius - dist) / self.separation_radius)
			push_x += dx / dist * weight
			push_z += dz / dist * weight
		if abs(push_x) + abs(push_z) < 0.001:
			return None
		return math.atan2(push_x, push_z)

	def _clear(self, direction_clear, yaw):
		try:
			return bool(direction_clear(yaw))
		except Exception:
			return False

	def _velocity(self, value):
		if value is None:
			return (0.0, 0.0)
		try:
			return (float(value[0]), float(value[2]))
		except Exception:
			try:
				return (float(value[0]), float(value[1]))
			except Exception:
				return (0.0, 0.0)

	def _obb_overlap(self, first, first_yaw, first_length, first_width,
				 second, second_yaw, second_length, second_width):
		"""2D rectangle SAT, using yaw convention atan2(x, z)."""
		axes = ((math.sin(first_yaw), math.cos(first_yaw)),
		        (math.cos(first_yaw), -math.sin(first_yaw)),
		        (math.sin(second_yaw), math.cos(second_yaw)),
		        (math.cos(second_yaw), -math.sin(second_yaw)))
		forward_a = (math.sin(first_yaw), math.cos(first_yaw))
		side_a = (math.cos(first_yaw), -math.sin(first_yaw))
		forward_b = (math.sin(second_yaw), math.cos(second_yaw))
		side_b = (math.cos(second_yaw), -math.sin(second_yaw))
		dx = float(second[0]) - float(first[0])
		dz = float(second[2]) - float(first[2])
		for axis in axes:
			distance = abs(dx * axis[0] + dz * axis[1])
			radius_a = (abs(forward_a[0] * axis[0] + forward_a[1] * axis[1]) * first_length +
			            abs(side_a[0] * axis[0] + side_a[1] * axis[1]) * first_width)
			radius_b = (abs(forward_b[0] * axis[0] + forward_b[1] * axis[1]) * second_length +
			            abs(side_b[0] * axis[0] + side_b[1] * axis[1]) * second_width)
			if distance > radius_a + radius_b:
				return False
		return True

	def _prediction_clear(self, position, candidate_yaw, speed, velocity,
				neighbours, half_length, half_width):
		"""Reject a locally clear ray if its next 1.2s overlaps another OBB."""
		own_speed = max(0.0, abs(float(speed)))
		# At walking pace there is not enough velocity for an OBB extrapolation
		# to be useful.  In a dense line-up it instead predicts every neighbour's
		# acceleration against a nearly stationary hull and vetoes all exits.
		# Separation steering and the physical tank resolver remain active; resume
		# predictive collision avoidance once the bot has actually got moving.
		if own_speed < 1.25:
			return True
		desired_vx = math.sin(candidate_yaw) * own_speed
		desired_vz = math.cos(candidate_yaw) * own_speed
		actual_vx, actual_vz = self._velocity(velocity)
		# Tanks cannot instantaneously rotate their velocity vector.  Blend the
		# observed velocity into the short prediction whenever the caller has it.
		if abs(actual_vx) + abs(actual_vz) > 0.05:
			own_vx = actual_vx * 0.45 + desired_vx * 0.55
			own_vz = actual_vz * 0.45 + desired_vz * 0.55
		else:
			own_vx = desired_vx
			own_vz = desired_vz
		for neighbour in neighbours or ():
			other = self._neighbour_position(neighbour)
			if other is None:
				continue
			try:
				if abs(float(other[1]) - float(position[1])) > 5.0:
					continue
			except Exception:
				pass
			other_yaw = 0.0
			other_velocity = None
			other_length = half_length
			other_width = half_width
			if isinstance(neighbour, dict):
				other_yaw = float(neighbour.get('yaw', 0.0) or 0.0)
				other_velocity = neighbour.get('velocity') or neighbour.get('vel')
				other_length = float(neighbour.get('half_length', half_length) or half_length)
				other_width = float(neighbour.get('half_width', half_width) or half_width)
			other_vx, other_vz = self._velocity(other_velocity)
			# Spawn formations can place two hull boxes slightly inside each other.
			# Treating that existing overlap as a future collision rejects every
			# steering candidate, so all bots stop and enter the recovery turn loop.
			# Separation steering already handles this case; predictive vetoes resume
			# as soon as the hulls have moved apart.
			if self._obb_overlap(position, candidate_yaw, half_length, half_width,
					other, other_yaw, other_length, other_width):
				continue
			for horizon in (0.35, 0.75, 1.20):
				own = (float(position[0]) + own_vx * horizon, 0.0,
				       float(position[2]) + own_vz * horizon)
				predicted = (float(other[0]) + other_vx * horizon, 0.0,
				             float(other[2]) + other_vz * horizon)
				if self._obb_overlap(own, candidate_yaw, half_length, half_width,
						predicted, other_yaw, other_length, other_width):
					return False
		return True

	def _reverse_blocked_by_vehicle(self, position, yaw, neighbours,
			half_length, half_width):
		"""Reject a blind reverse whose reachable hull sweep is occupied.

		``direction_clear`` answers for terrain and static world geometry only.
		In a spawn line-up every tank reaches the stuck threshold within about a
		second of every other one, so an unchecked reverse recovery drives each
		hull straight into the one behind it and the whole formation grinds.
		"""
		reverse_distance = half_length * 1.6
		# Translating an OBB along its longitudinal axis sweeps one exact longer
		# OBB. Sampling only the final pose misses a hull at the current or an
		# intermediate reachable position.
		sweep = (
			float(position[0]) - math.sin(float(yaw)) * reverse_distance * 0.5,
			float(position[1]),
			float(position[2]) - math.cos(float(yaw)) * reverse_distance * 0.5)
		sweep_length = half_length + reverse_distance * 0.5
		for neighbour in neighbours or ():
			other = self._neighbour_position(neighbour)
			if other is None:
				continue
			try:
				if abs(float(other[1]) - float(position[1])) > 5.0:
					continue
			except Exception:
				pass
			other_yaw = 0.0
			other_length = half_length
			other_width = half_width
			if isinstance(neighbour, dict):
				other_yaw = float(neighbour.get('yaw', 0.0) or 0.0)
				other_length = float(
					neighbour.get('half_length', half_length) or half_length)
				other_width = float(
					neighbour.get('half_width', half_width) or half_width)
			try:
				if self._obb_overlap(
						sweep, float(yaw), sweep_length, half_width,
						other, other_yaw, other_length, other_width):
					return True
			except Exception:
				continue
		return False

	def _choose_yaw(self, state, desired_yaw, current_yaw, position, speed,
			velocity, neighbours, direction_clear, half_length, half_width):
		separation = self._separation_yaw(
			position, current_yaw, neighbours, half_length, half_width)
		candidates = []
		for offset in self._CANDIDATE_OFFSETS:
			candidate = desired_yaw + offset
			score = abs(offset) + self._failure_penalty(state, candidate)
			if (state.get('escape_side_until', 0.0) > state['clock'] and
					float(offset) * float(
						state.get('escape_side', 0.0)) < -0.01):
				# Continue around the selected side before testing the mirror branch.
				# The finite penalty still permits the other side when this fan is spent.
				score += 1.25
			if separation is not None:
				# When bodies overlap, separation outranks route alignment; otherwise
				# two tanks can choose the same narrow opening forever.
				score = score * 0.30 + abs(_angle_delta(candidate, separation))
			candidates.append((score, candidate))
		candidates.sort(key=lambda item: item[0])
		# Probe in score order and return the first fully viable direction. Most
		# frames need one terrain ray set instead of probing all seven candidates.
		for unused_score, candidate in candidates:
			if self._clear(direction_clear, candidate):
				return candidate
		return None

	def drive(self, bot_id, position, yaw, speed, dt, target, neighbours,
			direction_clear, velocity=None, half_length=3.5, half_width=1.7,
			movement_intent=True, stopping_distance=None,
			stop_at_target=True, decision_horizon=0.0):
		"""Return ``throttle``, ``turn``, ``target_yaw`` and ``recovery_mode``.

		All timing uses the complete supplied interval.  The authority caller
		already advances vehicle physics in bounded substeps; throwing away the
		planner's remaining wall time would leave recovery and route leases
		permanently behind after every slow callback.
		"""
		state = self._state(bot_id, position)
		step = max(0.0, float(dt))
		if not state.pop('traffic_waiting', False):
			state['traffic_wait_time'] = 0.0
		state['last_step'] = step
		state['clock'] += step
		self._prune_failures(state)
		state['steering_age'] += step
		state['plan_age'] += step
		previous_desired_yaw = state.get('last_desired_yaw')
		desired_yaw = _yaw_to(position, target)
		heading_error = abs(_angle_delta(desired_yaw, yaw))
		previous_heading_error = state.get('last_heading_error')
		stable_heading = bool(
			previous_desired_yaw is not None and
			abs(_angle_delta(desired_yaw, previous_desired_yaw)) <= 0.12)
		heading_progress = bool(
			stable_heading and previous_heading_error is not None and
			heading_error + 0.002 < previous_heading_error)
		state['last_desired_yaw'] = desired_yaw
		state['last_heading_error'] = heading_error
		target_distance = _distance(position, target)
		if not movement_intent:
			# Cover/engagement orders intentionally stop within a tolerance. Do not
			# reinterpret that commanded hold as a stuck tank 1.8 seconds later.
			state['stuck_time'] = 0.0
			state['recovery_time'] = 0.0
			state['steering_yaw'] = None
			state['braking_target'] = None
			return {
				'throttle': 0.0,
				'turn': 0.0,
				'target_yaw': float(yaw),
				'recovery_mode': 'arrived',
			}
		own_half_length = max(0.5, float(half_length))
		own_half_width = max(0.3, float(half_width))
		displacement = _distance((position[0], 0.0, position[2]),
		                         (state['last_position'][0], 0.0,
		                          state['last_position'][1]))
		if target_distance <= WAYPOINT_ARRIVAL_RADIUS:
			# Reaching a waypoint is a stop, not a request to drive north: atan2(0, 0)
			# is zero and previously produced full throttle until the next order tick.
			state['stuck_time'] = 0.0
			state['recovery_time'] = 0.0
			state['steering_yaw'] = None
			state['last_position'] = (
				float(position[0]), float(position[2]))
			state['braking_target'] = None
			return {
				'throttle': 0.0,
				'turn': 0.0,
				'target_yaw': float(yaw),
				'recovery_mode': 'arrived',
			}

		# Physical progress is translation. A stable pivot may borrow a bounded
		# heading-progress lease while its error is actually shrinking, but merely
		# rotating (especially alternating left/right) cannot keep a wedged hull
		# alive forever. Keep the position as an accumulation anchor so genuine
		# low-speed travel is not lost between short planner samples.
		if displacement >= 0.08:
			state['last_position'] = (
				float(position[0]), float(position[2]))
			state['stuck_time'] = 0.0
		elif heading_progress:
			state['stuck_time'] = max(0.0, state['stuck_time'] - step)
		else:
			state['stuck_time'] += step

		threshold = self.stuck_seconds + state['phase'] * 0.42
		if state['recovery_time'] > 0.0:
			state['recovery_time'] = max(0.0, state['recovery_time'] - step)
			if state['recovery_time'] == 0.0:
				state['recovery_count'] += 1
				state['stuck_time'] = 0.0
		else:
			if state['stuck_time'] >= threshold:
				if state.get('last_clear_yaw') is not None:
					state['failed_yaws'][self._yaw_key(state['last_clear_yaw'])] = (
						state['clock'] + self.failure_ttl)
				state['recovery_time'] = self.recovery_seconds + state['phase'] * 0.28

		if state['recovery_time'] > 0.0:
			# Alternate the turn direction each recovery so a bot does not grind a
			# wall forever.  Phase makes adjacent ids leave a traffic jam apart. Never
			# reverse blindly: at a cliff or shoreline the space behind the hull can be
			# the unsafe side that caused the stall in the first place.
			direction = 1.0 if ((state['recovery_count'] + int(state['phase'] * 10)) % 2) else -1.0
			recovery_yaw = float(yaw) + direction * 0.85
			if (not self._clear(direction_clear, float(yaw) + math.pi) or
					self._reverse_blocked_by_vehicle(
						position, yaw, neighbours,
						own_half_length, own_half_width)):
				return {
					'throttle': 0.0,
					'turn': direction,
					'target_yaw': recovery_yaw,
					'recovery_mode': 'pivot_recovery',
				}
			# Reverse recovery is an explicit reverse command. The traverse law flips
			# steering from that command immediately, not from signed velocity.
			recovery_turn = -direction
			return {
				'throttle': -0.72,
				'turn': recovery_turn,
				'target_yaw': recovery_yaw,
				'recovery_mode': 'reverse_turn',
			}

		chosen_yaw = None
		old_yaw = state.get('steering_yaw')
		# Keep a clear avoidance branch long enough for the hull to pass the wall,
		# while retaining the shorter cadence for an unobstructed route heading.
		# Replanning a symmetric left/right choice every few frames made bots wag
		# in front of flat walls without committing to either exit.
		hold_seconds = (1.20 if old_yaw is not None and
			abs(_angle_delta(desired_yaw, old_yaw)) > 0.05 else 0.35)
		if (old_yaw is not None and state['plan_age'] < hold_seconds and
				abs(_angle_delta(desired_yaw, old_yaw)) < 2.15 and
				self._failure_penalty(state, old_yaw) <= 0.0 and
				self._clear(direction_clear, old_yaw)):
			chosen_yaw = old_yaw
		if chosen_yaw is None:
			chosen_yaw = self._choose_yaw(
				state, desired_yaw, yaw, position, speed, velocity, neighbours,
				direction_clear, own_half_length, own_half_width)
			state['plan_age'] = 0.0
		if chosen_yaw is None:
			# No forward ray is usable.  Start a timed recovery on the next tick
			# rather than issuing an unsafe blind turn.
			state['stuck_time'] = max(state['stuck_time'], threshold)
			return {
				'throttle': 0.0,
				'turn': 0.0,
				'target_yaw': float(yaw),
				'recovery_mode': 'blocked',
			}
		state['last_clear_yaw'] = chosen_yaw

		# Retain a selected side for a short time. This removes left/right flip
		# flop while the per-frame hard terrain veto remains active above.
		old_yaw = state['steering_yaw']
		if old_yaw is None or abs(_angle_delta(chosen_yaw, old_yaw)) > 0.04:
			state['steering_yaw'] = chosen_yaw
			state['steering_age'] = 0.0

		delta = _angle_delta(chosen_yaw, yaw)
		turn = max(-1.0, min(1.0, delta / 0.58))
		# This branch commands forward drive. Signed speed can still be negative
		# while braking a recovery or sliding downhill; steering remains forward.
		avoiding = abs(_angle_delta(chosen_yaw, desired_yaw)) > 0.05
		throttle = 1.0
		climb_grade = ((float(target[1]) - float(position[1])) /
		               max(0.1, target_distance))
		if climb_grade > 0.10 and abs(delta) > 0.30 and not avoiding:
			# Enter steep route edges square to the slope. Applying full drive
			# while the hull is still turning makes it circle at the foot of the
			# climb and repeatedly invalidates the next terrain sample.
			throttle = 0.0
		elif abs(delta) > math.pi * 0.5 and not avoiding:
			# Only a target behind the hull needs a stationary pivot. Side and
			# diagonal route corners retain forward progress while steering, which
			# avoids long zero-throttle pauses on slow heavy tanks.
			throttle = 0.0
		if stop_at_target and not avoiding and stopping_distance is not None:
			try:
				brake_distance = max(0.0, float(stopping_distance))
				reaction_distance = (abs(float(speed)) *
				                     max(0.0, float(decision_horizon)))
			except (TypeError, ValueError, OverflowError):
				brake_distance = 0.0
				reaction_distance = 0.0
			target_key = (round(float(target[0]), 2),
			              round(float(target[2]), 2))
			if state.get('braking_target') not in (None, target_key):
				state['braking_target'] = None
			if (target_distance <= WAYPOINT_ARRIVAL_RADIUS +
					brake_distance + reaction_distance):
				state['braking_target'] = target_key
			if state.get('braking_target') == target_key:
				# Releasing the throttle uses the same copied coast law that
				# produced ``stopping_distance``. If tuning or a slope leaves the
				# hull stopped short, release the latch and approach again.
				if (abs(float(speed)) <= 0.35 and
						target_distance > WAYPOINT_ARRIVAL_RADIUS + 0.5):
					state['braking_target'] = None
				else:
					throttle = 0.0
		elif not stop_at_target:
			state['braking_target'] = None
		return {
			'throttle': throttle,
			'turn': turn,
			'target_yaw': chosen_yaw,
			'recovery_mode': 'avoid' if avoiding else 'drive',
		}
