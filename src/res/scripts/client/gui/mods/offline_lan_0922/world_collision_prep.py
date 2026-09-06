# -*- coding: utf-8 -*-
"""One batched preparation of a horizontal world-collision sweep.

This module owns only arithmetic.  It never touches BigWorld, an entity, a
descriptor, the destructible ledger or round identity, and it issues no
query: the caller keeps every engine crossing, its query order and its early
exits.  The batch exists so the same numbers can be produced once per sweep by
Python or by the exact-build extension, and compared.

Every transcendental stays with the caller.  ``math.sin``/``math.cos`` results
are inputs here, so the native backend evaluates only ``+ - * /``, comparisons
and ``min``/``max``.  #1513's interpreter performs Python float arithmetic with
SSE2 doubles, and the extension is built with ``-msse2 -mfpmath=sse
-ffp-contract=off``, so both backends must agree exactly, to zero units in the
last place.

Lane layout, per lane, in the order the sweep consumes it::

    0  x1            6  profile_z      12 local_start_f   18 posed_start_y 0.6
    1  z1            7  profile_sin    13 local_end_r     19 posed_end_y   0.6
    2  x2            8  profile_cos    14 local_end_f     20 posed_start_y 1.1
    3  z2            9  profile_dir    15 pose_clamped    21 posed_end_y   1.1
    4  target_len   10  profile_look   16 footprint_x     22 posed_start_y 1.6
    5  profile_x    11  local_start_r  17 footprint_z     23 posed_end_y   1.6

The three posed heights are the shipped lane heights.  Their look-ahead
clamp against witnessed ground stays with the caller, because it depends on a
query result.
"""

LANE_HEIGHTS = (0.6, 1.1, 1.6)
LANE_VALUES = 24
MAXIMUM_LANES = 5
# Input scalars, then the sweep header, then ``MAXIMUM_LANES`` lanes.
INPUT_VALUES = 17
HEADER_VALUES = 11
BUFFER_VALUES = INPUT_VALUES + HEADER_VALUES + MAXIMUM_LANES * LANE_VALUES

_LANE_MERGE_EPSILON = 1.0e-7
_POSE_EPSILON = 1.0e-9


def prepare_sweep(pos_x, pos_y, pos_z, yaw_sin, yaw_cos,
		pose_right, pose_up, pose_forward,
		half_width, half_back, half_front,
		vel, dt, airborne, motion_sin, motion_cos, has_motion_yaw):
	"""Return ``(ahead, ground_plane, bounds, lanes)`` for one sweep.

	The statements below are the shipped sweep's own arithmetic, in its own
	order, so the batched result is the inline result.
	"""
	if airborne:
		ahead = abs(vel) * dt + 0.2
	else:
		ahead = max(0.4, abs(vel) * dt + 0.2)
	ground_plane = (
		pos_x, pos_y + 1.6 * pose_up, pos_z,
		yaw_cos * pose_right + yaw_sin * pose_forward,
		-yaw_sin * pose_right + yaw_cos * pose_forward)
	posed = not (pose_right == 0.0 and pose_up == 1.0 and pose_forward == 0.0)

	lanes = []
	if not has_motion_yaw:
		back_margin = -0.5 if vel > 0.0 else 0.5
		front_margin = ((half_front + ahead) if vel > 0.0 else
			-(half_back + ahead))
		direction = 1.0 if vel >= 0.0 else -1.0
		look = (half_front if vel > 0.0 else half_back) + ahead
		target_len = abs(back_margin) + look
		for offset_x in (-half_width, 0.0, half_width):
			sx = pos_x + yaw_cos * offset_x
			sz = pos_z - yaw_sin * offset_x
			lanes.append([
				sx + yaw_sin * back_margin, sz + yaw_cos * back_margin,
				sx + yaw_sin * front_margin, sz + yaw_cos * front_margin,
				target_len, sx, sz, yaw_sin, yaw_cos, direction, look])
	else:
		perp_x, perp_z = motion_cos, -motion_sin
		right_u = motion_sin * yaw_cos - motion_cos * yaw_sin
		forward_u = motion_sin * yaw_sin + motion_cos * yaw_cos
		right_v = perp_x * yaw_cos - perp_z * yaw_sin
		forward_v = perp_x * yaw_sin + perp_z * yaw_cos
		projected = []
		for hull_right, hull_forward in (
				(-half_width, -half_back), (half_width, -half_back),
				(half_width, half_front), (-half_width, half_front)):
			corner_u = right_u * hull_right + forward_u * hull_forward
			corner_v = right_v * hull_right + forward_v * hull_forward
			projected.append((corner_v, corner_u, corner_u + ahead))
		limits = []
		if right_u > 1.0e-9:
			limits.append(half_width / right_u)
		elif right_u < -1.0e-9:
			limits.append(-half_width / right_u)
		if forward_u > 1.0e-9:
			limits.append(half_front / forward_u)
		elif forward_u < -1.0e-9:
			limits.append(-half_back / forward_u)
		center_front = min(limits) if limits else 0.0
		projected.append((0.0, -0.5, center_front + ahead))
		merged = []
		for lane_v, start_u, end_u in sorted(projected):
			if merged and abs(lane_v - merged[-1][0]) <= _LANE_MERGE_EPSILON:
				previous_v, previous_start, previous_end = merged[-1]
				merged[-1] = (
					previous_v, min(previous_start, start_u),
					max(previous_end, end_u))
			else:
				merged.append((lane_v, start_u, end_u))
		for lane_v, start_u, end_u in merged:
			x1 = pos_x + perp_x * lane_v + motion_sin * start_u
			z1 = pos_z + perp_z * lane_v + motion_cos * start_u
			x2 = pos_x + perp_x * lane_v + motion_sin * end_u
			z2 = pos_z + perp_z * lane_v + motion_cos * end_u
			target_len = end_u - start_u
			lanes.append([
				x1, z1, x2, z2, target_len, x1, z1,
				motion_sin, motion_cos, 1.0, target_len])

	minimum_x = maximum_x = lanes[0][0]
	minimum_z = maximum_z = lanes[0][1]
	for lane in lanes:
		minimum_x = min(minimum_x, lane[0], lane[2])
		maximum_x = max(maximum_x, lane[0], lane[2])
		minimum_z = min(minimum_z, lane[1], lane[3])
		maximum_z = max(maximum_z, lane[1], lane[3])

	for lane in lanes:
		start_dx, start_dz = lane[0] - pos_x, lane[1] - pos_z
		end_dx, end_dz = lane[2] - pos_x, lane[3] - pos_z
		local_start_r = start_dx * yaw_cos - start_dz * yaw_sin
		local_start_f = start_dx * yaw_sin + start_dz * yaw_cos
		ray_end_r = end_dx * yaw_cos - end_dz * yaw_sin
		ray_end_f = end_dx * yaw_sin + end_dz * yaw_cos
		local_end_r, local_end_f = _hull_pose_endpoint(
			local_start_r, local_start_f, ray_end_r, ray_end_f,
			half_width, half_back, half_front)
		clamped = posed and (
			abs(local_end_r - ray_end_r) > _POSE_EPSILON or
			abs(local_end_f - ray_end_f) > _POSE_EPSILON)
		lane.extend((
			local_start_r, local_start_f, local_end_r, local_end_f,
			1.0 if clamped else 0.0,
			pos_x + yaw_cos * local_end_r + yaw_sin * local_end_f,
			pos_z - yaw_sin * local_end_r + yaw_cos * local_end_f))
		for height in LANE_HEIGHTS:
			lane.append(pos_y + local_start_r * pose_right +
				height * pose_up + local_start_f * pose_forward)
			lane.append(pos_y + local_end_r * pose_right +
				height * pose_up + local_end_f * pose_forward)

	return (ahead, ground_plane,
		(minimum_x, maximum_x, minimum_z, maximum_z), lanes)


def _hull_pose_endpoint(start_right, start_forward, end_right, end_forward,
		half_width, half_length_back, half_length_front):
	"""Stop pose extrapolation where a lane leaves the hull footprint."""
	delta_right = end_right - start_right
	delta_forward = end_forward - start_forward
	fraction = 1.0
	if delta_right > 0.0:
		fraction = min(fraction, (half_width - start_right) / delta_right)
	elif delta_right < 0.0:
		fraction = min(fraction, (-half_width - start_right) / delta_right)
	if delta_forward > 0.0:
		fraction = min(
			fraction, (half_length_front - start_forward) / delta_forward)
	elif delta_forward < 0.0:
		fraction = min(
			fraction, (-half_length_back - start_forward) / delta_forward)
	fraction = max(0.0, min(1.0, fraction))
	return (start_right + delta_right * fraction,
		start_forward + delta_forward * fraction)


class PythonPreparation(object):
	"""Evaluate the batch in Python, returning the values the sweep reads."""

	name = 'batch'

	def prepare_sweep(self, *values):
		return prepare_sweep(*values)


class NativePreparation(object):
	"""Evaluate the batch in the exact-build extension.

	One preallocated ``array.array('d')`` carries the inputs in and the
	results out.  Its address is resolved once; the array is never resized,
	so the address stays valid.  Nothing but doubles crosses the boundary and
	no Python object outlives the synchronous call.
	"""

	name = 'native'

	def __init__(self, module):
		import array
		self._module = module
		self._buffer = array.array('d', [0.0]) * BUFFER_VALUES
		address = self._buffer.buffer_info()[0]
		# #1513 is large-address aware, so the buffer can sit above the signed
		# 32-bit range and reach Python as a long. Two 16-bit halves always
		# arrive as plain ints, which is all the extension's argument reader
		# accepts.
		self._address_low = int(address & 0xffff)
		self._address_high = int(address >> 16)
		self._prepare = module.prepare_sweep

	def prepare_sweep(self, *values):
		data = self._buffer
		index = 0
		for value in values:
			data[index] = value
			index += 1
		status = self._prepare(
			self._address_low, self._address_high, BUFFER_VALUES)
		if status != 0:
			raise NativeComputeError(status)
		base = INPUT_VALUES
		lane_count = int(data[base + 10])
		if not 1 <= lane_count <= MAXIMUM_LANES:
			raise NativeComputeError(-1)
		lanes = []
		offset = base + HEADER_VALUES
		for unused_index in range(lane_count):
			lanes.append(data[offset:offset + LANE_VALUES].tolist())
			offset += LANE_VALUES
		return (
			data[base],
			(data[base + 1], data[base + 2], data[base + 3],
				data[base + 4], data[base + 5]),
			(data[base + 6], data[base + 7], data[base + 8],
				data[base + 9]),
			lanes)


class ShadowPreparation(object):
	"""Run both batches and report any disagreement, exactly.

	This is a correctness mode for the exact Windows client, where the
	extension's own build cannot be differentially tested here.  It doubles
	the pure computation, publishes the Python result, and must never be used
	for a performance measurement.
	"""

	name = 'native-shadow'

	def __init__(self, native):
		self._native = native

	def prepare_sweep(self, *values):
		native_plan = self._native.prepare_sweep(*values)
		python_plan = prepare_sweep(*values)
		if not identical_plans(native_plan, python_plan):
			from gui.mods.offline_lan_0922 import native_compute
			native_compute.note('shadow_mismatches')
		# The Python batch owns behaviour in this mode, so a mismatch is
		# reported without changing what the sweep does.
		return python_plan


def plan_values(plan):
	"""Flatten one plan into the doubles a comparison must agree on."""
	values = [plan[0]]
	values.extend(plan[1])
	values.extend(plan[2])
	values.append(float(len(plan[3])))
	for lane in plan[3]:
		values.extend(lane)
	return values


def identical_plans(left, right):
	"""Compare two plans as raw doubles, not as rounded values."""
	import struct
	if len(left[3]) != len(right[3]):
		return False
	for one, other in zip(plan_values(left), plan_values(right)):
		if struct.pack('<d', one) != struct.pack('<d', other):
			return False
	return True


class NativeComputeError(RuntimeError):
	"""The extension refused or could not complete one preparation."""

	def __init__(self, status):
		self.status = int(status)
		RuntimeError.__init__(
			self, 'native sweep preparation failed with status %d' % (
				self.status,))


def python_preparation():
	return PythonPreparation()


def native_preparation(module):
	return NativePreparation(module)


def shadow_preparation(module):
	return ShadowPreparation(NativePreparation(module))
