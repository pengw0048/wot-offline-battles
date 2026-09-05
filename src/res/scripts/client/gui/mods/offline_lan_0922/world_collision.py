# -*- coding: utf-8 -*-
"""Dedented 0.8.2 horizontal world-collision law."""

from gui.mods.offline_lan_0922.destructibles_sensor import (
	_catalog_soft_static_path, _diagnostic_static_recast_1513,
	_try_destroy_solid_hit, _vehicle_hull_bbox,
	ground_collision_filter, horizontal_collision_filter,
	prepare_horizontal_collision_filter)


_MAX_DRIVABLE_GRADIENT = 1.28
_MAX_DESCENDING_GRADIENT = 1.75
_MIN_DRIVABLE_HEIGHT_CHANGE = 0.15
_GROUND_HIT_EPSILON = 1.0e-3
_WORLD_SOFT_RECAST_BUDGET = 4
_UNPREPARED_COLLISION_FILTER = object()


def _collide_horizontal(spaceID, start, end,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Raycast while hiding only exact destructibles already marked broken."""
	import BigWorld
	broken_filter = collision_filter
	if broken_filter is _UNPREPARED_COLLISION_FILTER:
		broken_filter = horizontal_collision_filter(start, end)
	if broken_filter is None:
		return BigWorld.wg_collideSegment(spaceID, start, end, 128)
	return BigWorld.wg_collideSegment(
		spaceID, start, end, 128, broken_filter)


def _profile_gradient_limit(heights):
	try:
		return (_MAX_DESCENDING_GRADIENT
			if float(heights[-1]) < float(heights[0]) else
			_MAX_DRIVABLE_GRADIENT)
	except (IndexError, TypeError, ValueError):
		return _MAX_DRIVABLE_GRADIENT


def _drivable_ground_profile(heights, segment_length):
	"""Recognise a continuous, bounded slope in either travel direction.

	A flat profile is deliberately not terrain evidence: a horizontal wall on a
	level street must still reach the solid collision path. Abrupt rises and drops
	remain solid edges rather than becoming a blanket downhill bypass.
	"""
	try:
		values = [float(value) for value in heights]
		if len(values) < 2:
			return False
		if abs(values[-1] - values[0]) <= _MIN_DRIVABLE_HEIGHT_CHANGE:
			return False
		segment = max(0.001, float(segment_length))
		for index in range(1, len(values)):
			delta = values[index] - values[index - 1]
			maximum_gradient = (_MAX_DESCENDING_GRADIENT
				if delta < 0.0 else _MAX_DRIVABLE_GRADIENT)
			if abs(delta) > segment * maximum_gradient:
				return False
		return True
	except Exception:
		return False


def _drivable_surface(collision, maximum_gradient=_MAX_DRIVABLE_GRADIENT):
	"""Require the actual horizontal hit, not just nearby ground, to be a slope."""
	try:
		normal = collision[1]
		length = (normal.x * normal.x + normal.y * normal.y +
			normal.z * normal.z) ** 0.5
		if length <= 0.0:
			return False
		minimum_normal_y = 1.0 / (1.0 +
			float(maximum_gradient) ** 2) ** 0.5
		return normal.y / length >= minimum_normal_y
	except (AttributeError, IndexError, TypeError, ZeroDivisionError):
		return False


def _ground_profile(spaceID, Math, pos, sx, sz, sin_y, cos_y, direction,
		look, segment_count=6):
	"""Sample the lane that produced a lower-hull hit."""
	import BigWorld
	segment = look / float(segment_count)
	probe_down = max(
		5.0, float(look) * _MAX_DESCENDING_GRADIENT + 1.0)
	heights = []
	for sample_index in range(segment_count + 1):
		distance = segment * sample_index
		x = sx + sin_y * distance * direction
		z = sz + cos_y * distance * direction
		start = Math.Vector3(x, pos.y + 12.0, z)
		end = Math.Vector3(x, pos.y - probe_down, z)
		broken_filter = ground_collision_filter(x, z)
		ground = (BigWorld.wg_collideSegment(spaceID, start, end, 128)
			if broken_filter is None else
			BigWorld.wg_collideSegment(
				spaceID, start, end, 128, broken_filter))
		if ground is None:
			return (), segment
		heights.append(ground[0].y)
	return heights, segment


def _hit_matches_ground_profile(collision, heights, segment_length,
		profile_x, profile_z, profile_sin, profile_cos, profile_direction):
	"""Return a coarse seam candidate; exact native top must confirm it."""
	try:
		values = [float(value) for value in heights]
		segment = float(segment_length)
		if len(values) < 2 or segment <= 0.0:
			return False
		point = collision[0]
		distance = float(profile_direction) * (
			(float(point.x) - float(profile_x)) * float(profile_sin) +
			(float(point.z) - float(profile_z)) * float(profile_cos))
		profile_length = segment * (len(values) - 1)
		if distance < 0.0 or distance > profile_length:
			return False
		index = min(len(values) - 2, int(distance / segment))
		fraction = (distance - index * segment) / segment
		ground_y = (values[index] +
			(values[index + 1] - values[index]) * fraction)
		return abs(float(point.y) - ground_y) <= _MIN_DRIVABLE_HEIGHT_CHANGE
	except (AttributeError, IndexError, TypeError, ValueError,
			ZeroDivisionError):
		return False


def _hit_matches_exact_ground_top(spaceID, Math, pos, collision, look):
	"""Confirm that a coarse-profile candidate is the native top at its XZ."""
	import BigWorld
	try:
		point = collision[0]
		probe_down = max(
			5.0, float(look) * _MAX_DESCENDING_GRADIENT + 1.0)
		start = Math.Vector3(point.x, pos.y + 12.0, point.z)
		end = Math.Vector3(point.x, pos.y - probe_down, point.z)
		broken_filter = ground_collision_filter(point.x, point.z)
		top = (BigWorld.wg_collideSegment(spaceID, start, end, 128)
			if broken_filter is None else
			BigWorld.wg_collideSegment(
				spaceID, start, end, 128, broken_filter))
		return (top is not None and
			abs(float(top[0].y) - float(point.y)) <=
			_GROUND_HIT_EPSILON)
	except (AttributeError, IndexError, TypeError, ValueError):
		return False


def _hull_pose_y(pitch, roll):
	"""Return local right/up/forward contributions to world height."""
	import math
	pitch = float(pitch)
	roll = float(roll)
	if pitch == 0.0 and roll == 0.0:
		return 0.0, 1.0, 0.0
	pitch_cos = math.cos(pitch)
	return (
		pitch_cos * math.sin(roll),
		pitch_cos * math.cos(roll),
		-math.sin(pitch))


def _hull_pose_endpoint(local_start, local_end, half_width,
		half_length_back, half_length_front):
	"""Stop pose extrapolation where a lane leaves the hull footprint."""
	start_right = float(local_start[0])
	start_forward = float(local_start[1])
	delta_right = float(local_end[0]) - start_right
	delta_forward = float(local_end[1]) - start_forward
	fraction = 1.0
	for start, delta, lower, upper in (
			(start_right, delta_right, -float(half_width), float(half_width)),
			(start_forward, delta_forward, -float(half_length_back),
				float(half_length_front))):
		if delta > 0.0:
			fraction = min(fraction, (upper - start) / delta)
		elif delta < 0.0:
			fraction = min(fraction, (lower - start) / delta)
	fraction = max(0.0, min(1.0, fraction))
	return (
		start_right + delta_right * fraction,
		start_forward + delta_forward * fraction)


def _ground_top(spaceID, Math, pos, x, z, look):
	"""Return the first native top below one world-space point."""
	import BigWorld
	try:
		probe_down = max(
			5.0, float(look) * _MAX_DESCENDING_GRADIENT + 1.0)
		start = Math.Vector3(x, pos.y + 12.0, z)
		end = Math.Vector3(x, pos.y - probe_down, z)
		broken_filter = ground_collision_filter(x, z)
		ground = (BigWorld.wg_collideSegment(spaceID, start, end, 128)
			if broken_filter is None else
			BigWorld.wg_collideSegment(
				spaceID, start, end, 128, broken_filter))
		return None if ground is None else float(ground[0].y)
	except (AttributeError, IndexError, TypeError, ValueError):
		return None


def _lane_ground_ahead(spaceID, Math, pos, start_x, start_z,
		footprint_x, footprint_z, end_x, end_z, look):
	"""Conservatively extend the ground observed inside the hull footprint.

	A downward ``wg_collideSegment`` returns the first surface, which can be a
	wall or roof rather than terrain.  The look-ahead endpoint can also lie past
	a cliff and return no hit.  Both shapes must not lift a pitched lane over the
	obstacle.  The lane start and its footprint edge are still under the current
	hull, so extend that witnessed ground trend and accept the endpoint top only
	when it is lower.
	"""
	import math
	start_ground = _ground_top(
		spaceID, Math, pos, start_x, start_z, look)
	footprint_ground = _ground_top(
		spaceID, Math, pos, footprint_x, footprint_z, look)
	end_ground = _ground_top(spaceID, Math, pos, end_x, end_z, look)
	try:
		inside_length = math.sqrt(
			(float(footprint_x) - float(start_x)) ** 2 +
			(float(footprint_z) - float(start_z)) ** 2)
		full_length = math.sqrt(
			(float(end_x) - float(start_x)) ** 2 +
			(float(end_z) - float(start_z)) ** 2)
		if (start_ground is not None and footprint_ground is not None and
				inside_length > 1.0e-6):
			extrapolated = (float(start_ground) +
				(float(footprint_ground) - float(start_ground)) *
				full_length / inside_length)
			return (extrapolated if end_ground is None else
				min(float(end_ground), extrapolated))
		inside_tops = [float(value) for value in (
			start_ground, footprint_ground) if value is not None]
		if inside_tops:
			inside_top = min(inside_tops)
			return (inside_top if end_ground is None else
				min(float(end_ground), inside_top))
		return None if end_ground is None else float(end_ground)
	except (TypeError, ValueError, OverflowError):
		return None


def _posed_ray(Math, pos, x1, z1, x2, z2, local_start, local_end,
		height, pose_y, ground_ahead=None):
	"""Rotate one copied collision lane with the authoritative hull pose.

	``local_end`` already stops the pose at the first hull edge, but the lane
	still spans the whole look-ahead segment, so that clamped height would
	otherwise be reached only at the far endpoint.  A hull pitched over a
	crest then lifts its own lowest witness above real geometry standing on
	the ground it is about to reach: on level ground a transiently nose-up
	hull passed straight over a fully exposed 1.2 m wall.

	``ground_ahead`` is a conservative continuation of the ground witnessed
	inside the hull footprint, bounded by the native top under the endpoint.
	The lane never ends higher than ``height`` above that estimate, and never
	higher than the hull plane at its own leading edge, so the pose can only ever
	lower this witness.
	"""
	right_y, up_y, forward_y = pose_y
	start_y = (float(pos.y) + float(local_start[0]) * right_y +
		float(height) * up_y + float(local_start[1]) * forward_y)
	end_y = (float(pos.y) + float(local_end[0]) * right_y +
		float(height) * up_y + float(local_end[1]) * forward_y)
	if ground_ahead is not None:
		end_y = min(end_y, float(ground_ahead) + float(height))
	return (
		Math.Vector3(x1, start_y, z1),
		Math.Vector3(x2, end_y, z2))


def _raised_ray_has_wall(spaceID, Math, pos, x1, z1, x2, z2,
		local_start, local_end, pose_y, target_length,
		maximum_gradient=_MAX_DRIVABLE_GRADIENT, ground_profile=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER,
		ground_ahead=None):
	"""A drivable lower slope must not hide an independent wall above it."""
	for height in (1.1, 1.6):
		start, end = _posed_ray(
			Math, pos, x1, z1, x2, z2, local_start, local_end,
			height, pose_y, ground_ahead)
		collision = _collide_horizontal(
			spaceID, start, end, collision_filter)
		if collision is None:
			continue
		if (collision[0] - start).length >= target_length:
			continue
		if _drivable_surface(collision, maximum_gradient):
			continue
		if (ground_profile is not None and
				_hit_matches_ground_profile(
					collision, ground_profile[0], ground_profile[1],
					ground_profile[2], ground_profile[3],
					ground_profile[4], ground_profile[5],
					ground_profile[6])):
			if _hit_matches_exact_ground_top(
					spaceID, Math, pos, collision, ground_profile[7]):
				continue
		return True
	return False


def _solid_contact_cleared(spaceID, segment_start, segment_end, vel, td,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Admit only a clear ray or a bounded chain of proved light props.

	#1513 keeps a destroyed fragile/module skin solid until its hide callback.
	After native authority has accepted the first contact, skip that residual
	skin only through its unique registered OBB exit.  The same read-only helper
	may classify following light props so the swept catalog commit can destroy
	them later in this tick.  Unknown geometry, a backing wall, an ambiguous OBB
	or an over-budget chain remains solid.
	"""
	import BigWorld
	recast = _collide_horizontal(
		spaceID, segment_start, segment_end, collision_filter)
	if recast is None:
		return True
	return _catalog_soft_static_path(
		spaceID, segment_start, segment_end, recast, vel, td,
		[_WORLD_SOFT_RECAST_BUDGET])


def _destroy_and_recast(spaceID, segment_start, segment_end, collision,
		yaw, vel, td, crush_state=None, allow_kinetic=False,
		kinetic_speed=None, commit_enabled=True,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	if crush_state is not None and crush_state[0]:
		# Another hull lane already obtained native authority for this copied-pose
		# step.  Classify this lane read-only so the delayed skin cannot cause a
		# second destroy attempt, while every unrelated solid still fails closed.
		cleared = _catalog_soft_static_path(
			spaceID, segment_start, segment_end, collision, vel, td,
			[_WORLD_SOFT_RECAST_BUDGET])
		_diagnostic_static_recast_1513(cleared)
		return cleared is True
	# Revisit an already accepted hide skin before probing material again. This
	# keeps the 0.2 s native callback window out of the hot path and still
	# requires the exact pending identity, OBB exit and a real backing-ray recast.
	cleared = _catalog_soft_static_path(
		spaceID, segment_start, segment_end, collision, vel, td,
		[_WORLD_SOFT_RECAST_BUDGET], require_pending_first=True,
		allow_kinetic_first=allow_kinetic,
		kinetic_speed=kinetic_speed)
	if cleared is True:
		if crush_state is not None:
			crush_state[0] = True
		_diagnostic_static_recast_1513(True)
		return True
	if cleared in ('deferred', 'pending_hard'):
		_diagnostic_static_recast_1513(False)
		return False
	if cleared == 'kinetic':
		# This is planning evidence only.  The catalog commit seam still requires
		# the exact current hull plus this frame's physical travel before it may
		# use the directional speed cap.
		_diagnostic_static_recast_1513(False)
		return 'kinetic'
	if not commit_enabled:
		# A visible player may classify its native ray and submit a hull-sweep
		# proposal, but only the hidden worker may mutate native map state.
		_diagnostic_static_recast_1513(False)
		return False
	if not _try_destroy_solid_hit(
			spaceID, segment_start, collision[0], collision[1], yaw, vel, td):
		# A previously accepted fragile/module may remain in the native static
		# skin until #1513's hide callback.  Only that exact pending identity may
		# be skipped here, through its registered OBB exit and another real ray.
		# Active kinetic rejects, expired skins, falling bodies and unknown solids
		# remain authoritative.
		cleared = _catalog_soft_static_path(
			spaceID, segment_start, segment_end, collision, vel, td,
			[_WORLD_SOFT_RECAST_BUDGET], require_pending_first=True,
			allow_kinetic_first=allow_kinetic,
			kinetic_speed=kinetic_speed)
		_diagnostic_static_recast_1513(cleared)
		return cleared if cleared == 'kinetic' else cleared is True
	if crush_state is not None:
		crush_state[0] = True
	cleared = _solid_contact_cleared(
		spaceID, segment_start, segment_end, vel, td,
		collision_filter)
	_diagnostic_static_recast_1513(cleared)
	return cleared is True


def check_horizontal_collision(bigworld, math_module, *args, **kwargs):
	"""Supply the engine modules formerly captured by the 0.8.2 closure."""
	import sys
	missing = object()
	old_bigworld = sys.modules.get('BigWorld', missing)
	old_math = sys.modules.get('Math', missing)
	sys.modules['BigWorld'] = bigworld
	sys.modules['Math'] = math_module
	try:
		return _check_horizontal_collision(*args, **kwargs)
	finally:
		if old_bigworld is missing:
			sys.modules.pop('BigWorld', None)
		else:
			sys.modules['BigWorld'] = old_bigworld
		if old_math is missing:
			sys.modules.pop('Math', None)
		else:
			sys.modules['Math'] = old_math


def _check_horizontal_collision(spaceID, pos, yaw, vel, td=None,
		airborne=False, dt=0.04, return_status=False,
		allow_kinetic=False, kinetic_speed=None, commit_enabled=True,
		motion_yaw=None, pitch=0.0, roll=0.0):
	import math, BigWorld, Math
	try:
		hw = 1.5
		hl_front = 3.5
		hl_back = 3.5

		bbox = _vehicle_hull_bbox(td)
		if bbox is not None:
			try:
				hw = max(abs(bbox[0][0]), abs(bbox[1][0])) - 0.1
				hl_back = abs(bbox[0][2])
				hl_front = abs(bbox[1][2])
			except (AttributeError, KeyError, TypeError, IndexError):
				raise RuntimeError('#1513 hull hit tester bbox is invalid')

		# Look-ahead beyond the hull. The old flat +2.0 m made an invisible
		# wall 2 m before every obstacle, and DURING A FALL it saw the cliff
		# face below-ahead and zeroed the speed mid-air - the tank then hugged
		# the wall and trickled down instead of flying a ballistic arc.
		# Grounded: just enough to not tunnel at speed. Airborne: only the
		# distance actually travelled this tick - contact stops, proximity not.
		if airborne:
			_ahead = abs(vel) * dt + 0.2
		else:
			# Cover the complete copied-pose translation of this frame.  A fixed
			# 1.2 m cap was shorter than a 20 m/s tank's 2 m slow-frame step and
			# could miss a hard wall immediately behind a crushed light prop.
			_ahead = max(0.4, abs(vel) * dt + 0.2)
		cos_y = math.cos(yaw)
		sin_y = math.sin(yaw)
		pose_y = _hull_pose_y(pitch, roll)
		lane_segments = []
		if motion_yaw is None:
			# Keep the shipped longitudinal probe byte-for-byte in geometry and
			# lane order.  The explicit path below is only for cross-heading
			# translation introduced by ram, slip and wall deflection.
			back_margin = -0.5 if vel > 0.0 else 0.5
			front_margin = ((hl_front + _ahead) if vel > 0.0 else
				-(hl_back + _ahead))
			direction = 1.0 if vel >= 0.0 else -1.0
			look = (hl_front if vel > 0.0 else hl_back) + _ahead
			target_len = abs(back_margin) + look
			for offset_x in (-hw, 0.0, hw):
				sx = pos.x + cos_y * offset_x
				sz = pos.z - sin_y * offset_x
				x1 = sx + sin_y * back_margin
				z1 = sz + cos_y * back_margin
				x2 = sx + sin_y * front_margin
				z2 = sz + cos_y * front_margin
				lane_segments.append((
					x1, z1, x2, z2, target_len,
					sx, sz, sin_y, cos_y, direction, look))
		else:
			# The supplied yaw is already the true signed travel direction.
			# Sweep each real hull corner plus the centre line.  A diagonal
			# projection can leave a corner metres behind the old shared u=-0.5
			# start, while strict lateral/longitudinal motion merges back to three
			# lanes.
			motion_sin = math.sin(float(motion_yaw))
			motion_cos = math.cos(float(motion_yaw))
			perp_x, perp_z = motion_cos, -motion_sin
			right_u = motion_sin * cos_y - motion_cos * sin_y
			forward_u = motion_sin * sin_y + motion_cos * cos_y
			right_v = perp_x * cos_y - perp_z * sin_y
			forward_v = perp_x * sin_y + perp_z * cos_y
			projected = []
			for hull_right, hull_forward in (
					(-hw, -hl_back), (hw, -hl_back),
					(hw, hl_front), (-hw, hl_front)):
				corner_u = right_u * hull_right + forward_u * hull_forward
				corner_v = right_v * hull_right + forward_v * hull_forward
				projected.append((corner_v, corner_u, corner_u + _ahead))
			limits = []
			if right_u > 1.0e-9:
				limits.append(hw / right_u)
			elif right_u < -1.0e-9:
				limits.append(-hw / right_u)
			if forward_u > 1.0e-9:
				limits.append(hl_front / forward_u)
			elif forward_u < -1.0e-9:
				limits.append(-hl_back / forward_u)
			center_front = min(limits) if limits else 0.0
			projected.append((0.0, -0.5, center_front + _ahead))
			merged = []
			for lane_v, start_u, end_u in sorted(projected):
				if merged and abs(lane_v - merged[-1][0]) <= 1.0e-7:
					previous_v, previous_start, previous_end = merged[-1]
					merged[-1] = (
						previous_v, min(previous_start, start_u),
						max(previous_end, end_u))
				else:
					merged.append((lane_v, start_u, end_u))
			for lane_v, start_u, end_u in merged:
				x1 = pos.x + perp_x * lane_v + motion_sin * start_u
				z1 = pos.z + perp_z * lane_v + motion_cos * start_u
				x2 = pos.x + perp_x * lane_v + motion_sin * end_u
				z2 = pos.z + perp_z * lane_v + motion_cos * end_u
				target_len = end_u - start_u
				lane_segments.append((
					x1, z1, x2, z2, target_len,
					x1, z1, motion_sin, motion_cos, 1.0, target_len))
		minimum_x = min(min(lane[0], lane[2]) for lane in lane_segments)
		maximum_x = max(max(lane[0], lane[2]) for lane in lane_segments)
		minimum_z = min(min(lane[1], lane[3]) for lane in lane_segments)
		maximum_z = max(max(lane[1], lane[3]) for lane in lane_segments)
		_sweep_filter = prepare_horizontal_collision_filter(
			Math.Vector3(minimum_x, pos.y + 0.6, minimum_z),
			Math.Vector3(maximum_x, pos.y + 1.6, maximum_z))
		_crush_state = [False]
		_kinetic_contact = False

		for (x1, z1, x2, z2, target_len,
				profile_x, profile_z, profile_sin, profile_cos,
				profile_direction, profile_look) in lane_segments:
			start_dx, start_dz = x1 - pos.x, z1 - pos.z
			end_dx, end_dz = x2 - pos.x, z2 - pos.z
			local_start = (
				start_dx * cos_y - start_dz * sin_y,
				start_dx * sin_y + start_dz * cos_y)
			ray_local_end = (
				end_dx * cos_y - end_dz * sin_y,
				end_dx * sin_y + end_dz * cos_y)
			# Keep the footprint start, but stop pose growth at the first hull edge.
			# This chord is a conservative witness inside the swept hull volume;
			# extrapolating pitch/roll through look-ahead can pass over a real wall.
			local_end = _hull_pose_endpoint(
				local_start, ray_local_end, hw, hl_back, hl_front)
			pose_clamped = (
				pose_y != (0.0, 1.0, 0.0) and
				(abs(local_end[0] - ray_local_end[0]) > 1.0e-9 or
				 abs(local_end[1] - ray_local_end[1]) > 1.0e-9))
			# Only the pitch term lifts a lane along its own travel, so only
			# a pitched lane samples the ground witnessed inside its footprint
			# and at look-ahead.  A level or purely rolled hull keeps the
			# shipped lane geometry and its exact ray count.
			footprint_x = (pos.x + cos_y * local_end[0] +
				sin_y * local_end[1])
			footprint_z = (pos.z - sin_y * local_end[0] +
				cos_y * local_end[1])
			_ground_ahead = (
				_lane_ground_ahead(spaceID, Math, pos,
					x1, z1, footprint_x, footprint_z,
					x2, z2, target_len)
				if pose_y[2] else None)
			
			# Spodní paprsek pro pevnou geometrii (0.6m nad zemí)
			start_bot, end_bot = _posed_ray(
				Math, pos, x1, z1, x2, z2, local_start, local_end,
				0.6, pose_y, _ground_ahead)
			col_bot = _collide_horizontal(
				spaceID, start_bot, end_bot, _sweep_filter)
			# A posed lane is longer than its flat XZ projection, and every
			# distance test below compares a 3-D ``.length``.  The prepared
			# sweep filter is keyed on the envelope's x/z bounds only, so
			# posing the ray in y keeps it valid.
			target_len = (end_bot - start_bot).length
			
			if col_bot is not None:
				d_bot = (col_bot[0] - start_bot).length
				if d_bot < target_len:
					# A lower ray may meet the slope itself. Admit it only when this
					# exact lane has a continuous non-flat ground profile and the
					# native contact normal is also a drivable surface. This handles
					# downhill terrain without hiding a wall merely located on a hill.
					_heights = ()
					_segment = 0.0
					_gradient_limit = _MAX_DESCENDING_GRADIENT
					# A normal level-pose wall avoids the seven ground rays. A swept
					# chord whose endpoint height is clamped at the first hull edge can
					# meet terrain later, so that bounded case earns the existing profile.
					if (
							_drivable_surface(col_bot, _gradient_limit) or
							pose_clamped):
						_heights, _segment = _ground_profile(
							spaceID, Math, pos, profile_x, profile_z,
							profile_sin, profile_cos, profile_direction,
							profile_look)
						_gradient_limit = _profile_gradient_limit(_heights)
						if (_heights and
								abs(float(_heights[-1]) -
									float(_heights[0])) >
								_MIN_DRIVABLE_HEIGHT_CHANGE and
								not _drivable_ground_profile(
									_heights, _segment)):
							# This is a proved continuous-direction terrain profile, not
							# a small prop. An ascent/descent outside its directional
							# bound remains solid instead of falling into prop handling.
							return 'hard' if return_status else True
					_surface_is_ground = _drivable_surface(
						col_bot, _gradient_limit)
					if (not _surface_is_ground and
							pose_clamped and _hit_matches_ground_profile(
								col_bot, _heights, _segment,
								profile_x, profile_z,
								profile_sin, profile_cos,
								profile_direction)):
						_surface_is_ground = _hit_matches_exact_ground_top(
							spaceID, Math, pos, col_bot, profile_look)
					if (_heights and
							_drivable_ground_profile(_heights, _segment) and
							_surface_is_ground):
						if _raised_ray_has_wall(
								spaceID, Math, pos, x1, z1, x2, z2,
								local_start, local_end, pose_y,
								target_len, _gradient_limit,
								(_heights, _segment,
								 profile_x, profile_z,
								 profile_sin, profile_cos,
								 profile_direction, profile_look),
								_sweep_filter, _ground_ahead):
							return 'hard' if return_status else True
						continue
					# Treat every occupied hull height as independent evidence.  The
					# previous distance-difference heuristic dropped the whole lane when
					# a low prop was followed by a farther upper wall, and could destroy
					# a lower prop even when an upper wall was nearer.  Sort the actual
					# contacts front-to-back; after the first native acceptance, later
					# heights use the same read-only exact-OBB recast path.
					_lane_hits = [(d_bot, start_bot, end_bot, col_bot)]
					for _height in (1.1, 1.6):
						_ray_start, _ray_end = _posed_ray(
							Math, pos, x1, z1, x2, z2,
							local_start, local_end, _height,
							pose_y, _ground_ahead)
						_ray_hit = _collide_horizontal(
							spaceID, _ray_start, _ray_end,
							_sweep_filter)
						if _ray_hit is None:
							continue
						_ray_distance = (_ray_hit[0] - _ray_start).length
						if _ray_distance < target_len:
							_lane_hits.append((_ray_distance, _ray_start,
								_ray_end, _ray_hit))
					_lane_hits.sort(key=lambda value: value[0])
					for _unused_distance, _ray_start, _ray_end, _ray_hit in _lane_hits:
						_resolve_args = (
							spaceID, _ray_start, _ray_end, _ray_hit,
							yaw, vel, td, _crush_state, allow_kinetic,
							kinetic_speed)
						_resolved = _destroy_and_recast(*(
							_resolve_args + (commit_enabled, _sweep_filter)))
						if _resolved == 'kinetic':
							_kinetic_contact = True
						elif _resolved is not True:
							return 'hard' if return_status else True
			if col_bot is None or d_bot >= target_len:
				# A suspended beam or upper wall may miss the 0.6 m ray entirely.
				# Probe the remaining hull heights even on a lower-ray miss; otherwise
				# three empty lower lanes could classify a real upper collision clear.
				_upper_hits = []
				for _height in (1.1, 1.6):
					_ray_start, _ray_end = _posed_ray(
						Math, pos, x1, z1, x2, z2,
						local_start, local_end, _height,
						pose_y, _ground_ahead)
					_ray_hit = _collide_horizontal(
							spaceID, _ray_start, _ray_end,
							_sweep_filter)
					if _ray_hit is None:
						continue
					_ray_distance = (_ray_hit[0] - _ray_start).length
					if _ray_distance < target_len:
						_upper_hits.append((_ray_distance, _ray_start,
							_ray_end, _ray_hit))
				_upper_hits.sort(key=lambda value: value[0])
				for _unused_distance, _ray_start, _ray_end, _ray_hit in _upper_hits:
					_resolve_args = (
						spaceID, _ray_start, _ray_end, _ray_hit,
						yaw, vel, td, _crush_state, allow_kinetic,
						kinetic_speed)
					_resolved = _destroy_and_recast(*(
						_resolve_args + (commit_enabled, _sweep_filter)))
					if _resolved == 'kinetic':
						_kinetic_contact = True
					elif _resolved is not True:
						return 'hard' if return_status else True
	except Exception:
		raise
	if return_status:
		return 'kinetic' if _kinetic_contact else 'clear'
	return bool(_kinetic_contact)
