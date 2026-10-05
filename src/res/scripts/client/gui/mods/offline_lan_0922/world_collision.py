# -*- coding: utf-8 -*-
"""Dedented 0.8.2 horizontal world-collision law."""

from gui.mods.offline_lan_0922.collision_flags import VEHICLE_SKIP_FLAGS

from gui.mods.offline_lan_0922.worker_diagnostics import (
    observed, observed_ray)

from gui.mods.offline_lan_0922.destructibles_sensor import (
	_catalog_soft_static_path, _diagnostic_static_recast_1513,
	_try_destroy_solid_hit, _vehicle_hull_bbox, _descriptor_value,
	ground_collision_filter, horizontal_collision_filter,
	prepare_horizontal_collision_filter, collide_motion_segment)


_MAX_DRIVABLE_GRADIENT = 1.28
_MAX_DESCENDING_GRADIENT = 1.75
_MIN_DRIVABLE_HEIGHT_CHANGE = 0.15
_GROUND_HIT_EPSILON = 1.0e-3
_WORLD_SOFT_RECAST_BUDGET = 4
_UNPREPARED_COLLISION_FILTER = object()


def _trace_collision_filter(collision_filter, trace):
    """Observe bounded native callback candidates without another query.

    Candidates are not asserted to be the nearest returned hit: the native
    callback supplies identity but no position or ordering guarantee.
    """
    if collision_filter is None or trace is None:
        return collision_filter
    candidates = []
    trace['native_surface_candidates'] = candidates
    trace['native_surface_columns'] = 'material,flags,item,chunk,keep'

    def observed_filter(*hit):
        keep = collision_filter(*hit)
        if len(hit) == 4:
            candidate = tuple(hit) + (bool(keep),)
            if len(candidates) < 16 and candidate not in candidates:
                candidates.append(candidate)
        return keep
    return observed_filter


def _record_hard_contact(trace, reason, start, end, collision,
        ground_ahead=None, heights=()):
    """Copy existing query evidence; diagnostics must never change the verdict."""
    if trace is None:
        return
    try:
        def vector(value):
            return tuple(float(getattr(value, axis)) for axis in ('x', 'y', 'z'))
        trace.update(reason=reason, ray_start=vector(start), ray_end=vector(end),
                     hit=vector(collision[0]), normal=vector(collision[1]),
                     ground_ahead=ground_ahead, profile=list(heights))
    except Exception:
        trace['reason'] = reason


def _collide_horizontal(spaceID, start, end,
		collision_filter=_UNPREPARED_COLLISION_FILTER, departing_contact=None):
	"""Raycast while hiding only exact destructibles already marked broken."""
	import BigWorld
	broken_filter = collision_filter
	if broken_filter is _UNPREPARED_COLLISION_FILTER:
		broken_filter = horizontal_collision_filter(start, end)
	current = start
	for unused in range(_WORLD_SOFT_RECAST_BUDGET + 1):
		hit = collide_motion_segment(spaceID, current, end, broken_filter,
			BigWorld.wg_collideSegment)
		if hit is None or departing_contact is None or not departing_contact(hit):
			return hit
		remaining = end - hit[0]
		if remaining.length <= _GROUND_HIT_EPSILON:
			return None
		current = hit[0] + remaining.scale(_GROUND_HIT_EPSILON / remaining.length)
	# A bounded depenetration must still inspect every later surface.
	return hit


def _profile_gradient_limit(heights):
	try:
		return (_MAX_DESCENDING_GRADIENT
			if float(heights[-1]) < float(heights[0]) else
			_MAX_DRIVABLE_GRADIENT)
	except (IndexError, TypeError, ValueError):
		return _MAX_DRIVABLE_GRADIENT


def _drivable_ground_profile(heights, segment_length, allow_flat=False):
	"""Recognise a continuous, bounded slope in either travel direction.

	A flat profile is deliberately not terrain evidence: a horizontal wall on a
	level street must still reach the solid collision path. Abrupt rises and drops
	remain solid edges rather than becoming a blanket downhill bypass.
	"""
	try:
		values = [float(value) for value in heights]
		if len(values) < 2:
			return False
		if (not allow_flat and
				abs(values[-1] - values[0]) <= _MIN_DRIVABLE_HEIGHT_CHANGE):
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


def _drivable_perimeter_ground_profile(heights, segment_length, allow_flat=False):
	"""Validate support across a translated hull edge without lane-direction bias.

	Destination-perimeter lanes run around the hull winding, not along vehicle
	travel.  Applying the asymmetric uphill/downhill limit in that arbitrary
	winding makes the same bank pass on one edge direction and become a wall
	when the edge is reversed.  Accept the profile only if one of the two
	orientations is a valid continuous ground profile.  Real travel lanes keep
	their directional climb/descent law.
	"""
	try:
		values = tuple(float(value) for value in heights)
	except (TypeError, ValueError):
		return False
	return (
		_drivable_ground_profile(values, segment_length, allow_flat) or
		_drivable_ground_profile(
			tuple(reversed(values)), segment_length, allow_flat))


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


@observed('motion.ground_profile')
def _ground_profile(spaceID, Math, pos, sx, sz, sin_y, cos_y, direction,
		look, segment_count=6, ground_plane=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Sample the lane that produced a lower-hull hit."""
	segment = look / float(segment_count)
	heights = []
	for sample_index in range(segment_count + 1):
		distance = segment * sample_index
		x = sx + sin_y * distance * direction
		z = sz + cos_y * distance * direction
		ground = _ground_top(
			spaceID, Math, pos, x, z, look, ground_plane,
			collision_filter)
		if ground is None:
			return (), segment
		heights.append(ground)
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


def _hit_matches_exact_ground_top(spaceID, Math, pos, collision, look,
		ground_plane=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Confirm that a coarse-profile candidate is the native top at its XZ."""
	try:
		point = collision[0]
		top = _ground_top(
			spaceID, Math, pos, point.x, point.z, look, ground_plane,
			collision_filter)
		return (top is not None and
			abs(float(top) - float(point.y)) <=
			_GROUND_HIT_EPSILON)
	except (AttributeError, IndexError, TypeError, ValueError):
		return False


def _ground_exit_is_clear(spaceID, Math, pos, start, end, collision,
		look, ground_plane, collision_filter):
	"""Prove an outward terrain contact and query the rest of the same ray.

	A steeper drop beyond a supported slope is not a horizontal wall. Only an
	actual drivable top, crossed outward, earns this exception; a second native
	hit remains solid. In particular, a low wall behind the slope must not be
	hidden by the first terrain triangle or by the raised hull rays.
	"""
	if not _drivable_surface(collision, _MAX_DESCENDING_GRADIENT):
		return False
	delta = end - start
	length = delta.length
	if length <= _GROUND_HIT_EPSILON:
		return False
	normal = collision[1]
	outward = (delta.x * normal.x + delta.y * normal.y +
		delta.z * normal.z) / length
	if outward <= _GROUND_HIT_EPSILON:
		return False
	if not _hit_matches_exact_ground_top(
			spaceID, Math, pos, collision, look, ground_plane,
			collision_filter):
		return False
	remaining = end - collision[0]
	remaining_length = remaining.length
	if remaining_length <= _GROUND_HIT_EPSILON:
		return False
	recast_start = collision[0] + remaining.scale(
		_GROUND_HIT_EPSILON / remaining_length)
	return _collide_horizontal(
		spaceID, recast_start, end, collision_filter) is None


def _vehicle_motion_bounds(descriptor):
	"""Cover the chassis and mounted hull instead of only the narrow armour."""
	hull_box = _vehicle_hull_bbox(descriptor)
	if hull_box is None:
		return None
	chassis = _descriptor_value(descriptor, 'chassis')
	tester = _descriptor_value(chassis, 'hitTester')
	chassis_box = getattr(tester, 'bbox', None)
	hull_position = _descriptor_value(chassis, 'hullPosition')
	if chassis_box is None or hull_position is None:
		raise RuntimeError('#1513 chassis collision descriptor is unavailable')
	lower = tuple(min(float(chassis_box[0][i]),
		float(hull_box[0][i]) + float(hull_position[i])) for i in (0, 2))
	upper = tuple(max(float(chassis_box[1][i]),
		float(hull_box[1][i]) + float(hull_position[i])) for i in (0, 2))
	return lower[0], upper[0], -lower[1], upper[1]


def _vehicle_motion_extents(descriptor):
	bounds = _vehicle_motion_bounds(descriptor)
	if bounds is None:
		return None
	left, right, back, front = bounds
	return max(abs(left), abs(right)), back, front


def _vehicle_motion_heights(descriptor):
	hull_box = _vehicle_hull_bbox(descriptor)
	if hull_box is None:
		return 0.6, 1.6
	chassis = _descriptor_value(descriptor, 'chassis')
	chassis_box = _descriptor_value(chassis, 'hitTester').bbox
	hull_position = _descriptor_value(chassis, 'hullPosition')
	return (min(float(chassis_box[0][1]), float(hull_box[0][1])+float(hull_position[1])),
		max(float(chassis_box[1][1]), float(hull_box[1][1])+float(hull_position[1])))


def _translation_departing_contact(pos, yaw, bounds, pose_y, dx, dz,
		height_bounds=(0.6, 1.6), dy=0.0, pose_axes=None):
	"""Release only an existing wall plane whose penetration is decreasing.

	The hit must prove an occupied material point leaving the face. Its centre
	need not have crossed the face: a partly overhanging track already overlaps a
	bridge side while the centre is still above the deck. Each later native
	surface is recast; a new wall or an inward step cannot use this exception.
	"""
	import math
	left, right, back, front = bounds
	sine, cosine = math.sin(yaw), math.cos(yaw)
	planes = []

	def departing(collision):
		point, normal = collision[:2]
		if normal.y < -0.2:
			return False
		outward = dx * normal.x + dy * normal.y + dz * normal.z
		# Existing upward support may be crossed tangentially without deeper
		# penetration. Its finite edge must not become a horizontal wall as
		# suspension tips the hull. New terrain and every later wall still
		# have to pass the ordinary sweep.
		if (outward < -1.0e-8 or
				(outward <= 1.0e-8 and not _drivable_surface(collision))):
			return False
		px, py, pz = point.x - pos.x, point.y - pos.y, point.z - pos.z
		if pose_axes is not None:
			x, y, z = [axis[0]*px + axis[1]*py + axis[2]*pz
				for axis in pose_axes]
		else:
			if abs(pose_y[1]) < 0.1:
				return False
			x, z = px * cosine - pz * sine, px * sine + pz * cosine
			y = (py - x * pose_y[0] - z * pose_y[2]) / pose_y[1]
		inside = (left - 0.001 <= x <= right + 0.001 and
			-back - 0.001 <= z <= front + 0.001 and
			height_bounds[0] - 0.001 <= y <= height_bounds[1] + 0.001)
		if not inside and pose_axes is not None and outward > 1.0e-8:
			# A corner trajectory can meet the face halfway through this
			# translation. Back-project that exact hit along the swept interval
			# into the old box. An outward material point then proves existing
			# penetration; a new inward wall cannot use this exception.
			local_delta = [axis[0]*dx + axis[1]*dy + axis[2]*dz for axis in pose_axes]
			lo, hi = 0.0, 1.0
			for value, delta, lower, upper in zip((x, y, z), local_delta,
					(left, height_bounds[0], -back), (right, height_bounds[1], front)):
				if abs(delta) < 1.0e-12:
					if not lower-0.001 <= value <= upper+0.001:
						hi = -1.0
						break
				else:
					a, b = (value-upper-0.001)/delta, (value-lower+0.001)/delta
					lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
			inside = lo <= hi
		if inside:
			planes.append((point, normal))
			return True
		# A destination perimeter can cross the same finite face just beyond
		# the old footprint. Only a prior native hit inside that footprint
		# proves this is the already occupied plane, rather than a new wall.
		for old_point, old_normal in planes:
			alignment = (normal.x*old_normal.x + normal.y*old_normal.y + normal.z*old_normal.z)
			distance = ((point.x-old_point.x)*normal.x +
				(point.y-old_point.y)*normal.y + (point.z-old_point.z)*normal.z)
			if alignment >= 0.9999 and abs(distance) <= 0.001:
				return True
		return False
	return departing


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
		half_length_back, half_length_front, lateral_bounds=None):
	"""Stop pose extrapolation where a lane leaves the hull footprint."""
	start_right = float(local_start[0])
	start_forward = float(local_start[1])
	delta_right = float(local_end[0]) - start_right
	delta_forward = float(local_end[1]) - start_forward
	fraction = 1.0
	left, right = lateral_bounds or (-float(half_width), float(half_width))
	for start, delta, lower, upper in (
			(start_right, delta_right, left, right),
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


@observed('motion.ground_top')
def _ground_top(spaceID, Math, pos, x, z, look, ground_plane=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Return support below the occupied lane, not an overhead deck.

	The ceiling follows the posed upper hull ray, or the tangent plane of an
	already witnessed drivable hit. A sky-origin ray can select a gatehouse
	roof in one column and its road in the next, inventing a cliff. Horizontal
	lower and upper rays still own walls and beams inside the occupied lanes.

	``collision_filter`` is the sweep-wide broken-skin callback already prepared
	for the horizontal lanes.  Every ground column sampled by this sweep lies
	inside that envelope, and the callback still resolves each hit by its exact
	native identity against the live accepted ledger, so sharing it decides
	exactly what a per-column filter decides without rebuilding the candidate
	set for each of the sweep's ground rays.
	"""
	import BigWorld
	try:
		probe_down = max(
			5.0, float(look) * _MAX_DESCENDING_GRADIENT + 1.0)
		start_y = pos.y + 12.0
		if ground_plane is not None:
			px, py, pz, gradient_x, gradient_z = ground_plane
			start_y = min(start_y, py +
				(float(x) - px) * gradient_x +
				(float(z) - pz) * gradient_z)
		if start_y <= pos.y - probe_down:
			return None
		start = Math.Vector3(x, start_y, z)
		end = Math.Vector3(x, pos.y - probe_down, z)
		broken_filter = collision_filter
		if broken_filter is _UNPREPARED_COLLISION_FILTER:
			broken_filter = ground_collision_filter(x, z)
		ground = collide_motion_segment(spaceID, start, end, broken_filter,
			BigWorld.wg_collideSegment, 'native.motion.ground')
		return None if ground is None else float(ground[0].y)
	except (AttributeError, IndexError, TypeError, ValueError):
		return None


@observed('motion.ground_ahead')
def _lane_ground_ahead(spaceID, Math, pos, start_x, start_z,
		footprint_x, footprint_z, end_x, end_z, look, ground_plane=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER, descending=False,
		support_start_y=None):
	"""Extend only support witnessed under the current hull footprint.

	A lower floor beyond a crest is not occupied by this horizontal sweep.
	Pulling its endpoint down to that floor creates an artificial diagonal
	through the cliff top and blocks departure in both travel directions.
	The under-hull trend still caps a nose-up ray against real walls ahead;
	vertical integration owns contact with a lower landing surface.
	"""
	import math
	start_ground = _ground_top(
		spaceID, Math, pos, start_x, start_z, look, ground_plane,
		collision_filter)
	footprint_ground = _ground_top(
		spaceID, Math, pos, footprint_x, footprint_z, look, ground_plane,
		collision_filter)
	if (start_ground is not None and support_start_y is not None and
			float(start_ground) < float(support_start_y) - _GROUND_HIT_EPSILON):
		# A floor below the posed chassis is not its support. This also applies
		# to outward corner lanes, whose clamped local endpoints coincide: they
		# have no descending pose trend even when a trench lies below them.
		# Pulling their end down to that floor invents a collision with the lip.
		return None
	if (start_ground is not None and footprint_ground is not None and
			(descending or float(footprint_ground) < float(start_ground))):
		# The ground may descend even while the hull lane rises. Before
		# extrapolating that descent, require the middle to agree with the
		# same support chord. Either a high crest or a low trench sample
		# breaks continuity; neither may bend the occupied ray into a lip.
		middle_ground = _ground_top(
			spaceID, Math, pos, (start_x + footprint_x) * 0.5,
			(start_z + footprint_z) * 0.5, look, ground_plane,
			collision_filter)
		if (middle_ground is None or
				abs(float(middle_ground) -
					(float(start_ground) + float(footprint_ground)) * 0.5) >
				_GROUND_HIT_EPSILON):
			return None
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
			return extrapolated
		inside_tops = [float(value) for value in (
			start_ground, footprint_ground) if value is not None]
		if inside_tops:
			inside_top = min(inside_tops)
			return inside_top
		return None
	except (TypeError, ValueError, OverflowError):
		return None


def _posed_ray(Math, pos, x1, z1, x2, z2, local_start, local_end,
		height, pose_y, ground_ahead=None, lane_rotation=None):
	"""Rotate one copied collision lane with the authoritative hull pose.

	``local_end`` already stops the pose at the first hull edge, but the lane
	still spans the whole look-ahead segment, so that clamped height would
	otherwise be reached only at the far endpoint.  A hull pitched over a
	crest then lifts its own lowest witness above real geometry standing on
	the ground it is about to reach: on level ground a transiently nose-up
	hull passed straight over a fully exposed 1.2 m wall.

	``ground_ahead`` is a conservative continuation of the ground witnessed
	inside the hull footprint.
	The lane never ends higher than ``height`` above that estimate, and never
	higher than the hull plane at its own leading edge, so the pose can only ever
	lower this witness.
	"""
	right_y, up_y, forward_y = pose_y
	start_y = (float(pos.y) + float(local_start[0]) * right_y +
		float(height) * up_y + float(local_start[1]) * forward_y)
	end_y = (float(pos.y) + float(local_end[0]) * right_y +
		float(height) * up_y + float(local_end[1]) * forward_y)
	if lane_rotation is not None:
		# Rotate the occupied body in all three dimensions. Keep the swept
		# displacement in world coordinates: it is translation, not more hull.
		axes, sine, cosine, end_rise = lane_rotation
		def offset(local):
			x, z = local
			return (x*(axes[0][0]-cosine) + height*axes[1][0] +
				z*(axes[2][0]-sine),
				x*(axes[0][2]+sine) + height*axes[1][2] +
				z*(axes[2][2]-cosine))
		dx, dz = offset(local_start)
		x1, z1 = x1+dx, z1+dz
		dx, dz = offset(local_end)
		x2, z2 = x2+dx, z2+dz
		end_y += end_rise
	if ground_ahead is not None:
		end_y = min(end_y, float(ground_ahead) + float(height))
	return (
		Math.Vector3(x1, start_y, z1),
		Math.Vector3(x2, end_y, z2))


def _posed_support_top(pos, pose_y, extents):
	hw, back, front = extents
	return (float(pos.y) + abs(pose_y[0]) * hw +
		max(-back * pose_y[2], front * pose_y[2]))


def _supported_flat_top_is_clear(spaceID, Math, pos, end, collision,
		pose_y, extents, heights, segment, look, ground_plane, collision_filter):
	'''Recognise a level deck already inside the posed track height range.

	A small net height change cannot prove a wall. Require the actual upward
	face, a bounded profile, the exact native top and a clear ray remainder.
	The caller still checks both occupied upper lanes.
	'''
	if (not heights or abs(float(heights[-1]) - float(heights[0])) >
			_MIN_DRIVABLE_HEIGHT_CHANGE or
			not _drivable_surface(collision) or
			collision[0].y > _posed_support_top(pos, pose_y, extents) +
			_GROUND_HIT_EPSILON or
			not _drivable_ground_profile(heights, segment, allow_flat=True) or
			not _hit_matches_exact_ground_top(spaceID, Math, pos, collision,
				look, ground_plane, collision_filter)):
		return False
	remaining = end - collision[0]
	if remaining.length <= _GROUND_HIT_EPSILON:
		return True
	start = collision[0] + remaining.scale(
		_GROUND_HIT_EPSILON / remaining.length)
	return _collide_horizontal(spaceID, start, end, collision_filter) is None


def _supported_seam_is_clear(spaceID, Math, pos, collision, x1, z1, x2, z2,
		local_start, local_end, pose_y, extents, collision_filter,
		lane_rotation=None):
	"""Cross a low support seam already straddled by the posed tracks.

	Paris reports have one track on a 0.6 m pavement and one on the street.
	The lowered longitudinal witness hits the pavement's vertical side and
	classifies it as a building. Require native support on both sides, a broad
	low top within the existing track plane, and a clear lifted body corridor.
	An upright tank approaching a wall has no such raised support plane.
	"""
	import math, BigWorld
	point, normal = collision[:2]
	normal_length = math.sqrt(normal.x ** 2 + normal.y ** 2 + normal.z ** 2)
	if (normal_length <= 1.0e-12 or normal.y / normal_length < -0.2 or
			_drivable_surface(collision)):
		return False
	length = math.hypot(normal.x, normal.z)
	if length <= 1.0e-12:
		return False
	nx, nz = normal.x / length, normal.z / length
	posed_top = _posed_support_top(pos, pose_y, extents)
	if posed_top < point.y - _GROUND_HIT_EPSILON:
		return False
	tops = []
	for offset in (0.12, -0.12, -0.60):
		x, z = point.x + nx * offset, point.z + nz * offset
		start = Math.Vector3(x, pos.y + 1.6, z)
		end = Math.Vector3(x, pos.y - 3.0, z)
		hit = collide_motion_segment(spaceID, start, end, collision_filter,
			BigWorld.wg_collideSegment, 'native.motion.ground')
		# The outside column can land on the bevel itself. Both columns
		# inside the step must still establish its broad, nearly level top.
		if (hit is None or len(hit) < 2 or hit[1].y <= 0.0 or
				(offset < 0.0 and not _drivable_surface(hit, 0.5))):
			return False
		tops.append(float(hit[0].y))
	rise = max(tops[1:]) - tops[0]
	if (not 0.03 < rise <= 0.75 or abs(tops[1] - tops[2]) > 0.12 or
			min(tops[1:]) < point.y - _GROUND_HIT_EPSILON or
			max(tops[1:]) > posed_top + 0.075):
		return False
	for height in (0.6, 1.1, 1.6):
		start, end = _posed_ray(Math, pos, x1, z1, x2, z2,
			local_start, local_end, height, pose_y, lane_rotation=lane_rotation)
		# Raise the same corridor by only the measured step. Never discard
		# the rest of the segment or the occupied upper hull heights.
		offset = Math.Vector3(0.0, rise, 0.0)
		if _collide_horizontal(spaceID, start + offset, end + offset,
				collision_filter) is not None:
			return False
	return True


def _raised_ray_has_wall(spaceID, Math, pos, x1, z1, x2, z2,
		local_start, local_end, pose_y, target_length,
		maximum_gradient=_MAX_DRIVABLE_GRADIENT, ground_profile=None,
		collision_filter=_UNPREPARED_COLLISION_FILTER,
		ground_ahead=None, trace=None, require_clear_exit=False,
		departing_contact=None, lane_rotation=None):
	"""A drivable lower slope must not hide an independent wall above it."""
	for height in (1.1, 1.6):
		start, end = _posed_ray(
			Math, pos, x1, z1, x2, z2, local_start, local_end,
			height, pose_y, ground_ahead, lane_rotation)
		collision = _collide_horizontal(
			spaceID, start, end, collision_filter, departing_contact)
		if collision is None:
			continue
		if (collision[0] - start).length >= target_length:
			continue
		if _drivable_surface(collision, maximum_gradient):
			if (not require_clear_exit or _ground_exit_is_clear(
					spaceID, Math, pos, start, end, collision,
					ground_profile[7], ground_profile[8], collision_filter)):
				continue
		if (not require_clear_exit and ground_profile is not None and
				_hit_matches_ground_profile(
					collision, ground_profile[0], ground_profile[1],
					ground_profile[2], ground_profile[3],
					ground_profile[4], ground_profile[5],
					ground_profile[6])):
			if _hit_matches_exact_ground_top(
					spaceID, Math, pos, collision, ground_profile[7],
					ground_profile[8], collision_filter):
				continue
		_record_hard_contact(trace, 'raised_wall', start, end, collision,
			ground_ahead)
		return True
	return False


@observed('motion.solid_recast')
def _solid_contact_cleared(spaceID, segment_start, segment_end, vel, td,
		collision_filter=_UNPREPARED_COLLISION_FILTER):
	"""Admit only a clear ray or a bounded chain of proved light props.

	#1513 keeps a destroyed fragile/module skin solid until its hide callback.
	After native authority has accepted the first contact, filter that exact
	original native key and query the complete ray, including its box interior.  The same read-only helper
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


@observed('motion.destroy_recast')
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
	# requires the exact accepted identity and a filtered query of the whole ray.
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
		# be filtered here; the complete ray still checks every other surface.
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


def _posed_sweep_bounds(pos, axes, left, right, back, front, travel_x, travel_z):
	"""Evaluate the four extremal coordinates of the posed swept body.

	Each coordinate is monotone in each local component. Select its extremal
	vertex, retaining the original corner expression's arithmetic order.
	"""
	result = []
	for component, origin, travel in ((0, pos.x, travel_x), (2, pos.z, travel_z)):
		r, u, f = axes[0][component], axes[1][component], axes[2][component]
		minimum = (origin + r*(right if r < 0.0 else left) +
			u*(1.6 if u < 0.0 else 0.6) + f*(front if f < 0.0 else -back) +
			(1.0 if travel < 0.0 else 0.0)*travel)
		maximum = (origin + r*(right if r > 0.0 else left) +
			u*(1.6 if u > 0.0 else 0.6) + f*(front if f > 0.0 else -back) +
			(1.0 if travel > 0.0 else 0.0)*travel)
		result.extend((minimum, maximum))
	return result


@observed('motion.world')
def _check_horizontal_collision(spaceID, pos, yaw, vel, td=None,
		airborne=False, dt=0.04, return_status=False,
		allow_kinetic=False, kinetic_speed=None, commit_enabled=True,
		motion_yaw=None, pitch=0.0, roll=0.0, trace=None,
		exact_footprint=False, departing_contact=None):
	import sys
	from . import native_math, native_world
	try:
		result = native_world.run(sys.modules[__name__], spaceID, pos, yaw, vel,
			td, airborne, dt, return_status, allow_kinetic, kinetic_speed,
			commit_enabled, motion_yaw, pitch, roll, trace, exact_footprint,
			departing_contact)
	except Exception as error:
		# Earlier live effects remain committed. Reject only this motion;
		# replaying the Python law could repeat destruction.
		native_math.report_world_failure(error)
		if trace is not None:
			try:
				trace.update(reason='native_world_error', error=str(error))
			except Exception:
				pass
		return 'hard' if return_status else True
	if result is not None:
		return result
	import math, BigWorld, Math
	try:
		hw = 1.5
		hl_front = 3.5
		hl_back = 3.5

		bounds = _vehicle_motion_bounds(td)
		if bounds is None:
			left, right = -hw, hw
		else:
			left, right, hl_back, hl_front = bounds
			hw = max(abs(left), abs(right))

		if trace is not None:
			trace.clear()
			trace.update(position=(pos.x, pos.y, pos.z), yaw=yaw, speed=vel,
				dt=dt, motion_yaw=motion_yaw, pitch=pitch, roll=roll,
				airborne=airborne, extents=(hw, hl_back, hl_front),
				lateral_bounds=(left, right))

		# The occupied hull and this frame's actual travel are the entire
		# collision sweep. A stationary turn already supplies its swept body.
		# Neither contact path has a proximity margin or a waiting interval.
		_ahead = (0.0 if exact_footprint else
			abs(float(vel)) * max(0.0, float(dt)))
		cos_y = math.cos(yaw)
		sin_y = math.sin(yaw)
		pose_y = _hull_pose_y(pitch, roll)
		from .tank_collision import pose_axes as body_axes
		axes = body_axes(yaw, pitch, roll)
		# The support plane is normal to the actual local up axis. The old
		# Y-only shear kept a horizontal footprint even for a vertical wreck.
		gradient_x = -axes[1][0]/axes[1][1] if abs(axes[1][1]) > 0.1 else 0.0
		gradient_z = -axes[1][2]/axes[1][1] if abs(axes[1][1]) > 0.1 else 0.0
		travel_yaw = (float(motion_yaw) if motion_yaw is not None else
			float(yaw) + (math.pi if vel < 0.0 else 0.0))
		travel_x = math.sin(travel_yaw) * _ahead
		travel_z = math.cos(travel_yaw) * _ahead
		# A grounded passive translation follows its current supporting plane.
		# Testing a horizontal destination perimeter first makes that same
		# plane look like an upper wall on a bank. Extrapolate only the already
		# occupied attitude over this displacement, never the floor below a
		# future cliff. Airborne hulls retain their actual height and attitude.
		# A tipped body on an undrivable face is not a road-aligned chassis;
		# suspension/gravity owns its descent, not this horizontal sweep.
		grounded_passive = (motion_yaw is not None and not airborne and
			_drivable_surface((pos, Math.Vector3(*axes[1])),
				_MAX_DESCENDING_GRADIENT))
		travel_y = (gradient_x * travel_x + gradient_z * travel_z
			if grounded_passive else 0.0)
		if grounded_passive and abs(travel_y) > _GROUND_HIT_EPSILON:
			# Attitude alone is not proof that this plane continues over a lip.
			# Require support at the current, middle and destination centres;
			# otherwise retain the fixed-height sweep until gravity takes over.
			plane = (pos.x, pos.y + 0.6, pos.z,
				gradient_x, gradient_z)
			heights = [_ground_top(spaceID, Math, pos,
				pos.x + travel_x * t, pos.z + travel_z * t, _ahead, plane)
				for t in (0.0, 0.5, 1.0)]
			if (any(h is None for h in heights) or
					abs(heights[0] - pos.y) > 0.6 or
					abs(heights[2] - heights[0] - travel_y) > 0.6 or
					abs(heights[1] - (heights[0] + heights[2]) * 0.5) >
						_GROUND_HIT_EPSILON):
				grounded_passive, travel_y = False, 0.0
			else:
				travel_y = heights[2] - heights[0]
		if departing_contact is None and _ahead > 0.0:
			departing_contact = _translation_departing_contact(
				pos, yaw, (left, right, hl_back, hl_front), pose_y,
				travel_x, travel_z, _vehicle_motion_heights(td), dy=travel_y,
				pose_axes=axes if motion_yaw is not None else None)
		ground_plane = (
			float(pos.x), float(pos.y) + 1.6 * pose_y[1], float(pos.z),
			(gradient_x if motion_yaw is not None else cos_y*pose_y[0]+sin_y*pose_y[2]),
			(gradient_z if motion_yaw is not None else -sin_y*pose_y[0]+cos_y*pose_y[2]))
		lane_segments = []
		perimeter_lanes = set()
		if motion_yaw is None:
			# Preserve the lane order, using both actual body edges separately.
			back_margin = -0.5 if vel > 0.0 else 0.5
			front_margin = ((hl_front + _ahead) if vel > 0.0 else
				-(hl_back + _ahead))
			direction = 1.0 if vel >= 0.0 else -1.0
			look = (hl_front if vel > 0.0 else hl_back) + _ahead
			target_len = abs(back_margin) + look
			for offset_x in (left, 0.0, right):
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
					(left, -hl_back), (right, -hl_back),
					(right, hl_front), (left, hl_front)):
				corner_u = right_u * hull_right + forward_u * hull_forward
				corner_v = right_v * hull_right + forward_v * hull_forward
				projected.append((corner_v, corner_u, corner_u + _ahead))
			limits = []
			if right_u > 1.0e-9:
				limits.append(right / right_u)
			elif right_u < -1.0e-9:
				limits.append(left / right_u)
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
			# Corner trajectories alone leave the middle of a long side open.
			# A house corner can enter that side during a glancing slide without
			# meeting a corner ray or the centre line. Inspect the destination
			# perimeter too, using the same terrain/upper-wall rules below.
			if _ahead > 0.0:
				corners = [(pos.x + cos_y * x + sin_y * z + travel_x,
					pos.z - sin_y * x + cos_y * z + travel_z)
					for x, z in ((left, -hl_back), (right, -hl_back),
						(right, hl_front), (left, hl_front))]
				for index, (x1, z1) in enumerate(corners):
					x2, z2 = corners[(index + 1) % len(corners)]
					length = math.hypot(x2 - x1, z2 - z1)
					if length > 1.0e-9:
						perimeter_lanes.add(len(lane_segments))
						lane_segments.append((x1, z1, x2, z2, length,
							x1, z1, (x2 - x1) / length,
							(z2 - z1) / length, 1.0, length))
		minimum_x = min(min(lane[0], lane[2]) for lane in lane_segments)
		maximum_x = max(max(lane[0], lane[2]) for lane in lane_segments)
		minimum_z = min(min(lane[1], lane[3]) for lane in lane_segments)
		maximum_z = max(max(lane[1], lane[3]) for lane in lane_segments)
		posed_bounds = _posed_sweep_bounds(
			pos, axes, left, right, hl_back, hl_front, travel_x, travel_z)
		minimum_x = min(minimum_x, posed_bounds[0])
		maximum_x = max(maximum_x, posed_bounds[1])
		minimum_z = min(minimum_z, posed_bounds[2])
		maximum_z = max(maximum_z, posed_bounds[3])
		_sweep_filter = prepare_horizontal_collision_filter(
			Math.Vector3(minimum_x, pos.y + 0.6, minimum_z),
			Math.Vector3(maximum_x, pos.y + 1.6, maximum_z))
		_sweep_filter = _trace_collision_filter(_sweep_filter, trace)
		_crush_state = [False]
		_kinetic_contact = False

		for lane_index, lane in enumerate(lane_segments):
			(x1, z1, x2, z2, target_len,
				profile_x, profile_z, profile_sin, profile_cos,
				profile_direction, profile_look) = lane
			start_dx, start_dz = x1 - pos.x, z1 - pos.z
			end_dx, end_dz = x2 - pos.x, z2 - pos.z
			perimeter_lane = lane_index in perimeter_lanes
			lane_ground_plane = ground_plane
			lane_pos = pos
			if perimeter_lane:
				# This lane belongs to the translated body, with unchanged
				# attitude; do not extrapolate its height beyond the old body.
				start_dx, start_dz = start_dx - travel_x, start_dz - travel_z
				end_dx, end_dz = end_dx - travel_x, end_dz - travel_z
				lane_ground_plane = (ground_plane[0] + travel_x,
					ground_plane[1] + travel_y, ground_plane[2] + travel_z,
					ground_plane[3], ground_plane[4])
				lane_pos = Math.Vector3(pos.x, pos.y + travel_y, pos.z)
			# Terrain evidence for a translated destination edge belongs to the
			# translated body pose.  Using the old centre here makes a valid bank
			# appear above/below the body and can reject the very displacement
			# whose perimeter is being checked.
			profile_pos = lane_pos if perimeter_lane else pos
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
				local_start, ray_local_end, hw, hl_back, hl_front,
				lateral_bounds=(left, right))
			lane_rotation = (axes, sin_y, cos_y,
				travel_y if lane_index not in perimeter_lanes else 0.0)
			if motion_yaw is None:
				lane_rotation = None
			pose_clamped = (
				pose_y != (0.0, 1.0, 0.0) and
				(abs(local_end[0] - ray_local_end[0]) > 1.0e-9 or
				 abs(local_end[1] - ray_local_end[1]) > 1.0e-9))
			# Preserve continuous-slope wall coverage in either direction, but
			# do not let a lower floor under the leading edge turn a crest
			# departure into an artificial downward collision chord.
			descending_lane = (
				(local_end[0] - local_start[0]) * pose_y[0] +
				(local_end[1] - local_start[1]) * pose_y[2] < -1.0e-9)

			footprint_x = (pos.x + cos_y * local_end[0] +
				sin_y * local_end[1])
			footprint_z = (pos.z - sin_y * local_end[0] +
				cos_y * local_end[1])
			if lane_index in perimeter_lanes:
				footprint_x += travel_x
				footprint_z += travel_z
			_ground_ahead = (
				_lane_ground_ahead(spaceID, Math, pos,
					x1, z1, footprint_x, footprint_z,
						x2, z2, target_len, lane_ground_plane, _sweep_filter,
					descending=descending_lane,
					support_start_y=(pos.y + local_start[0] * pose_y[0] +
						local_start[1] * pose_y[2]))
				if pose_y[2] and motion_yaw is None else None)
			# An explicit displacement direction is a passive, posed sweep.
			# Do not bend it down toward future ground: at a ledge that ray
			# crosses the cliff below the actual body and vetoes departure.
			
			# Lower solid-geometry witness, 0.6 m above the support plane.
			start_bot, end_bot = _posed_ray(
				Math, lane_pos, x1, z1, x2, z2, local_start, local_end,
				0.6, pose_y, _ground_ahead, lane_rotation)
			col_bot = _collide_horizontal(
				spaceID, start_bot, end_bot, _sweep_filter, departing_contact)
			# Distance tests use the actual 3-D lane. The prepared filter
			# includes both the rotated body and its translated destination.
			target_len = (end_bot - start_bot).length
			if motion_yaw is not None:
				# Ground evidence must be sampled under the same rotated ray.
				profile_x, profile_z = start_bot.x, start_bot.z
				profile_look = math.hypot(end_bot.x-start_bot.x, end_bot.z-start_bot.z)
				if profile_look > 1.0e-9:
					profile_sin = (end_bot.x-start_bot.x)/profile_look
					profile_cos = (end_bot.z-start_bot.z)/profile_look
				profile_direction = 1.0
			
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
					_profile_plane = lane_ground_plane
					if _drivable_surface(col_bot, _gradient_limit):
						# The native hit anchors a newly entered ramp even before the
						# body has pitched to match it. Keep the same 1.6 m occupied
						# upper-lane clearance above that actual surface.
						_point, _normal = col_bot[0], col_bot[1]
						_profile_plane = (
							float(_point.x), float(_point.y) + 1.6,
							float(_point.z),
							-float(_normal.x) / float(_normal.y),
							-float(_normal.z) / float(_normal.y))
					# A normal level-pose wall avoids the seven ground rays. A swept
					# chord whose endpoint height is clamped at the first hull edge can
					# meet terrain later, so that bounded case earns the existing profile.
					if (
							_drivable_surface(col_bot, _gradient_limit) or
							pose_clamped):
						_heights, _segment = _ground_profile(
							spaceID, Math, profile_pos, profile_x, profile_z,
							profile_sin, profile_cos, profile_direction,
							profile_look, ground_plane=_profile_plane,
							collision_filter=_sweep_filter)
						_profile_drivable = (
							_drivable_perimeter_ground_profile
							if perimeter_lane else _drivable_ground_profile)
						_gradient_limit = (
							_MAX_DESCENDING_GRADIENT if perimeter_lane else
							_profile_gradient_limit(_heights))
						# A destination-perimeter edge is a body cross-section, not a
						# travel direction.  If its lower witness is the exact native
						# ground described by one continuous profile, let the upper
						# occupied rays decide whether an independent wall exists.
						# Requiring the lower ray remainder itself to be clear makes a
						# long bank look like a wall at each following terrain triangle.
						if (perimeter_lane and _heights and
								_profile_drivable(
									_heights, _segment, allow_flat=True) and
								_drivable_surface(col_bot, _gradient_limit) and
								_hit_matches_ground_profile(
									col_bot, _heights, _segment,
									profile_x, profile_z, profile_sin, profile_cos,
									profile_direction) and
								_hit_matches_exact_ground_top(
									spaceID, Math, profile_pos, col_bot, profile_look,
									_profile_plane, _sweep_filter)):
							if _raised_ray_has_wall(
									spaceID, Math, lane_pos, x1, z1, x2, z2,
									local_start, local_end, pose_y, target_len,
									_gradient_limit,
									(_heights, _segment, profile_x, profile_z,
									 profile_sin, profile_cos, profile_direction,
									 profile_look, _profile_plane),
									_sweep_filter, _ground_ahead, trace=trace,
									departing_contact=departing_contact,
									lane_rotation=lane_rotation):
								return 'hard' if return_status else True
							continue
						if (_heights and
								abs(float(_heights[-1]) -
									float(_heights[0])) >
								_MIN_DRIVABLE_HEIGHT_CHANGE and
								not _profile_drivable(
									_heights, _segment)):
							# A descending lane can leave the actual surface before a
							# steeper drop farther ahead. Do not turn that lower ground
							# into a wall: prove the outward native top and a clear
							# remainder at every occupied hull height. Ascents, mixed
							# profiles and contacts entering terrain retain the limit.
							departing = (
								all(_heights[index] <= _heights[index - 1]
									for index in range(1, len(_heights))) and
								_ground_exit_is_clear(
									spaceID, Math, profile_pos, start_bot, end_bot, col_bot,
									profile_look, _profile_plane, _sweep_filter))
							if departing:
								if _raised_ray_has_wall(
										spaceID, Math, lane_pos, x1, z1, x2, z2,
										local_start, local_end, pose_y, target_len,
										_gradient_limit,
										(_heights, _segment, profile_x, profile_z,
										 profile_sin, profile_cos, profile_direction,
										 profile_look, _profile_plane),
										_sweep_filter, _ground_ahead, trace=trace,
										require_clear_exit=True,
										departing_contact=departing_contact,
										lane_rotation=lane_rotation):
									return 'hard' if return_status else True
								continue
							_record_hard_contact(trace, 'ground_profile', start_bot,
								end_bot, col_bot, _ground_ahead, _heights)
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
							spaceID, Math, profile_pos, col_bot, profile_look,
							_profile_plane, _sweep_filter)
					_supported_flat_top = (not airborne and
						_supported_flat_top_is_clear(
							spaceID, Math, profile_pos, end_bot, col_bot, pose_y,
							(hw, hl_back, hl_front), _heights, _segment,
							profile_look, _profile_plane, _sweep_filter))
					if ((_heights and
							_profile_drivable(_heights, _segment) and
							_surface_is_ground) or _supported_flat_top):
						if _raised_ray_has_wall(
								spaceID, Math, lane_pos, x1, z1, x2, z2,
								local_start, local_end, pose_y,
								target_len, _gradient_limit,
								(_heights, _segment,
								 profile_x, profile_z,
								 profile_sin, profile_cos,
								 profile_direction, profile_look, _profile_plane),
								_sweep_filter, _ground_ahead, trace=trace,
								departing_contact=departing_contact,
								lane_rotation=lane_rotation):
							return 'hard' if return_status else True
						continue
					if (not airborne and _supported_seam_is_clear(
							spaceID, Math, profile_pos, col_bot, x1, z1, x2, z2,
							local_start, local_end, pose_y,
							(hw, hl_back, hl_front), _sweep_filter, lane_rotation)):
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
							Math, lane_pos, x1, z1, x2, z2,
							local_start, local_end, _height,
							pose_y, _ground_ahead, lane_rotation)
						_ray_hit = _collide_horizontal(
							spaceID, _ray_start, _ray_end,
							_sweep_filter, departing_contact)
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
							_record_hard_contact(trace, 'solid_lane', _ray_start,
								_ray_end, _ray_hit, _ground_ahead, _heights)
							return 'hard' if return_status else True
			if col_bot is None or d_bot >= target_len:
				# A suspended beam or upper wall may miss the 0.6 m ray entirely.
				# Probe the remaining hull heights even on a lower-ray miss; otherwise
				# three empty lower lanes could classify a real upper collision clear.
				_upper_hits = []
				for _height in (1.1, 1.6):
					_ray_start, _ray_end = _posed_ray(
						Math, lane_pos, x1, z1, x2, z2,
						local_start, local_end, _height,
						pose_y, _ground_ahead, lane_rotation)
					_ray_hit = _collide_horizontal(
							spaceID, _ray_start, _ray_end,
							_sweep_filter, departing_contact)
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
						_record_hard_contact(trace, 'upper_lane', _ray_start,
							_ray_end, _ray_hit, _ground_ahead)
						return 'hard' if return_status else True
	except Exception:
		raise
	if return_status:
		return 'kinetic' if _kinetic_contact else 'clear'
	return bool(_kinetic_contact)
