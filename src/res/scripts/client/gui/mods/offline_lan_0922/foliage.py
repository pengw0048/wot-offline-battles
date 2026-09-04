# -*- coding: utf-8 -*-
"""Pair-specific concealment from prebaked #1513 SpeedTree volumes."""

import math


FOLIAGE_CAMOUFLAGE_PER_VOLUME = 0.15
FOLIAGE_CAMOUFLAGE_LIMIT = 0.60
FIRE_TRANSPARENCY_DISTANCE = 15.0
OBSERVER_EYE_HEIGHT = 2.0
TARGET_CHECK_HEIGHT = 1.5


def _segment_cells(start, end, cell_size):
	cell_size = max(1.0, float(cell_size))
	start_x = float(start[0])
	start_z = float(start[2])
	end_x = float(end[0])
	end_z = float(end[2])
	dx = end_x - start_x
	dz = end_z - start_z
	cell_x = int(math.floor(start_x / cell_size))
	cell_z = int(math.floor(start_z / cell_size))
	end_cell_x = int(math.floor(end_x / cell_size))
	end_cell_z = int(math.floor(end_z / cell_size))
	result = []
	seen = set()

	def append(cell):
		if cell not in seen:
			seen.add(cell)
			result.append(cell)

	def append_point_cells(point_x, point_z):
		"""Include every closed grid cell touched by one endpoint."""
		base_x = int(math.floor(point_x / cell_size))
		base_z = int(math.floor(point_z / cell_size))
		x_values = [base_x]
		z_values = [base_z]
		if abs(point_x - round(
				point_x / cell_size) * cell_size) <= 1.0e-9:
			x_values.append(base_x - 1)
		if abs(point_z - round(
				point_z / cell_size) * cell_size) <= 1.0e-9:
			z_values.append(base_z - 1)
		for current_x in x_values:
			for current_z in z_values:
				append((current_x, current_z))

	def append_parallel_boundaries():
		if (abs(dx) <= 1.0e-12 and abs(
				start_x - round(start_x / cell_size) * cell_size) <=
				1.0e-9):
			for current_x, current_z in tuple(result):
				append((current_x - 1, current_z))
		if (abs(dz) <= 1.0e-12 and abs(
				start_z - round(start_z / cell_size) * cell_size) <=
				1.0e-9):
			for current_x, current_z in tuple(result):
				append((current_x, current_z - 1))

	append((cell_x, cell_z))
	if (cell_x, cell_z) == (end_cell_x, end_cell_z):
		append_parallel_boundaries()
		append_point_cells(start_x, start_z)
		append_point_cells(end_x, end_z)
		return result
	if dx > 0.0:
		step_x = 1
		t_max_x = ((cell_x + 1) * cell_size - start_x) / dx
		t_delta_x = cell_size / dx
	elif dx < 0.0:
		step_x = -1
		t_max_x = (cell_x * cell_size - start_x) / dx
		t_delta_x = -cell_size / dx
	else:
		step_x = 0
		t_max_x = float('inf')
		t_delta_x = float('inf')
	if dz > 0.0:
		step_z = 1
		t_max_z = ((cell_z + 1) * cell_size - start_z) / dz
		t_delta_z = cell_size / dz
	elif dz < 0.0:
		step_z = -1
		t_max_z = (cell_z * cell_size - start_z) / dz
		t_delta_z = -cell_size / dz
	else:
		step_z = 0
		t_max_z = float('inf')
		t_delta_z = float('inf')
	# Stop at the segment's parametric endpoint.  Comparing only cell ids can
	# step past floor(end) when an endpoint lies exactly on a grid boundary,
	# after which the monotonic traversal can never return to the target cell.
	while True:
		next_t = min(t_max_x, t_max_z)
		if next_t > 1.0 + 1.0e-12:
			break
		if t_max_x + 1.0e-12 < t_max_z:
			cell_x += step_x
			t_max_x += t_delta_x
			append((cell_x, cell_z))
		elif t_max_z + 1.0e-12 < t_max_x:
			cell_z += step_z
			t_max_z += t_delta_z
			append((cell_x, cell_z))
		else:
			# A line through a grid corner touches both side cells as well as
			# the diagonal one. Include the full supercover so an OBB indexed in
			# either side cell cannot disappear from the visibility query.
			append((cell_x + step_x, cell_z))
			append((cell_x, cell_z + step_z))
			cell_x += step_x
			cell_z += step_z
			t_max_x += t_delta_x
			t_max_z += t_delta_z
			append((cell_x, cell_z))
		if next_t >= 1.0 - 1.0e-12:
			break
	append_parallel_boundaries()
	append_point_cells(start_x, start_z)
	append_point_cells(end_x, end_z)
	return result


def _slab_interval(origin, delta, minimum, maximum, low, high):
	if abs(delta) <= 1e-9:
		if origin < minimum or origin > maximum:
			return None
		return low, high
	first = (minimum - origin) / delta
	second = (maximum - origin) / delta
	if first > second:
		first, second = second, first
	low = max(low, first)
	high = min(high, second)
	if low > high:
		return None
	return low, high


def _intersects(instance, start, end):
	"""Test a 3-D segment against one oriented foliage box row."""
	if isinstance(instance, dict):
		return _intersects_dynamic(instance, start, end)
	dx0 = float(start[0]) - float(instance[0])
	dz0 = float(start[2]) - float(instance[2])
	dx1 = float(end[0]) - float(instance[0])
	dz1 = float(end[2]) - float(instance[2])
	u0 = float(instance[4]) * dx0 + float(instance[5]) * dz0
	v0 = float(instance[6]) * dx0 + float(instance[7]) * dz0
	u1 = float(instance[4]) * dx1 + float(instance[5]) * dz1
	v1 = float(instance[6]) * dx1 + float(instance[7]) * dz1
	interval = _slab_interval(u0, u1 - u0, -1.0, 1.0, 0.0, 1.0)
	if interval is None:
		return False
	interval = _slab_interval(v0, v1 - v0, -1.0, 1.0,
		interval[0], interval[1])
	if interval is None:
		return False
	interval = _slab_interval(
		float(start[1]), float(end[1]) - float(start[1]),
		float(instance[1]), float(instance[3]),
		interval[0], interval[1])
	return interval is not None


def _dot(left, right):
	return sum(left[index] * right[index] for index in range(3))


def _cross(left, right):
	return (
		left[1] * right[2] - left[2] * right[1],
		left[2] * right[0] - left[0] * right[2],
		left[0] * right[1] - left[1] * right[0],
	)


def _dynamic_instance(center, half_axes):
	"""Build one exact native 3-D OBB for a moving fallen tree."""
	center = tuple(float(value) for value in center)
	half_axes = tuple(tuple(float(value) for value in axis)
		for axis in half_axes)
	if len(center) != 3 or len(half_axes) != 3 or any(
			len(axis) != 3 for axis in half_axes):
		raise ValueError('fallen tree native pose is invalid')
	values = center + tuple(value for axis in half_axes for value in axis)
	if any(math.isnan(value) or math.isinf(value) for value in values):
		raise ValueError('fallen tree native pose is non-finite')
	volume = abs(_dot(half_axes[0], _cross(half_axes[1], half_axes[2])))
	if volume <= 1.0e-9:
		raise ValueError('fallen tree native pose is degenerate')
	corners = []
	for first_sign in (-1.0, 1.0):
		for second_sign in (-1.0, 1.0):
			for third_sign in (-1.0, 1.0):
				corners.append(tuple(
					center[index] + first_sign * half_axes[0][index] +
					second_sign * half_axes[1][index] +
					third_sign * half_axes[2][index]
					for index in range(3)))
	radius = max(math.hypot(
		point[0] - center[0], point[2] - center[2]) for point in corners)
	instance = {
		'center': center,
		'half_axes': half_axes,
		'strength': FOLIAGE_CAMOUFLAGE_PER_VOLUME,
		'radius': radius,
	}
	bounds = (
		min(point[0] for point in corners),
		min(point[2] for point in corners),
		max(point[0] for point in corners),
		max(point[2] for point in corners),
	)
	return instance, bounds


def _intersects_dynamic(instance, start, end):
	center = instance['center']
	half_axes = instance['half_axes']
	start_delta = tuple(float(start[index]) - center[index]
		for index in range(3))
	end_delta = tuple(float(end[index]) - center[index]
		for index in range(3))
	face_axes = (
		_cross(half_axes[1], half_axes[2]),
		_cross(half_axes[2], half_axes[0]),
		_cross(half_axes[0], half_axes[1]),
	)
	low, high = 0.0, 1.0
	for index, face_axis in enumerate(face_axes):
		denominator = _dot(face_axis, half_axes[index])
		if abs(denominator) <= 1.0e-12:
			return False
		start_value = _dot(start_delta, face_axis) / denominator
		end_value = _dot(end_delta, face_axis) / denominator
		interval = _slab_interval(
			start_value, end_value - start_value,
			-1.0, 1.0, low, high)
		if interval is None:
			return False
		low, high = interval
	return True


def _bounds_cells(bounds, cell_size):
	minimum_cell_x = int(math.floor(bounds[0] / cell_size))
	minimum_cell_z = int(math.floor(bounds[1] / cell_size))
	maximum_cell_x = int(math.floor(bounds[2] / cell_size))
	maximum_cell_z = int(math.floor(bounds[3] / cell_size))
	for cell_x in range(minimum_cell_x, maximum_cell_x + 1):
		for cell_z in range(minimum_cell_z, maximum_cell_z + 1):
			yield cell_x, cell_z


class FoliageMap(object):
	"""Validated spatial foliage index for one arena."""

	def __init__(self, data):
		self.map_name = str(data.get('map') or '')
		self.cell_size = max(1.0, float(data.get('cell_size', 32.0)))
		self.instances = list(data.get('instances') or ())
		self.cells = {}
		for key, values in (data.get('cells') or {}).items():
			parts = str(key).split(',', 1)
			if len(parts) == 2:
				self.cells[(int(parts[0]), int(parts[1]))] = list(values)
		self.fallen_tree_profiles = {}
		for row in data.get('fallen_trees') or ():
			self.fallen_tree_profiles[(int(row[0]), int(row[1]))] = (
				tuple(float(value) for value in row[2:8]), row[8])
		self.activated_fallen_trees = set()
		self.refreshing_fallen_trees = set()
		self.fallen_tree_instances = {}
		self.fallen_tree_cells = {}
		self.inactive_instances = set()

	def activate_fallen_tree(self, chunk_id, item_index):
		"""Begin following one canonical tree's exact native matrix."""
		identity = (int(chunk_id), int(item_index))
		if identity in self.activated_fallen_trees:
			return False
		profile = self.fallen_tree_profiles.get(identity)
		if profile is None:
			return False
		self.activated_fallen_trees.add(identity)
		self.refreshing_fallen_trees.add(identity)
		return True

	def fallen_tree_profile(self, chunk_id, item_index):
		"""Return local center and half sizes for an activated native tree."""
		identity = (int(chunk_id), int(item_index))
		if identity not in self.activated_fallen_trees:
			return None
		profile = self.fallen_tree_profiles.get(identity)
		if profile is None:
			return None
		bounds = profile[0]
		center = tuple((bounds[index] + bounds[index + 3]) * 0.5
			for index in range(3))
		half_sizes = tuple((bounds[index + 3] - bounds[index]) * 0.5
			for index in range(3))
		return center, half_sizes

	def refreshing_fallen_tree_wires(self):
		return tuple(sorted(self.refreshing_fallen_trees))

	def settle_fallen_tree(self, chunk_id, item_index):
		identity = (int(chunk_id), int(item_index))
		if (identity not in self.refreshing_fallen_trees or
				identity not in self.fallen_tree_instances):
			return False
		self.refreshing_fallen_trees.remove(identity)
		return True

	def update_fallen_tree_pose(self, chunk_id, item_index, center, half_axes):
		"""Replace one fallen crown with its current exact native OBB."""
		identity = (int(chunk_id), int(item_index))
		if identity not in self.refreshing_fallen_trees:
			return False
		row, bounds = _dynamic_instance(center, half_axes)
		unused_bounds, standing_instance_id = self.fallen_tree_profiles[
			identity]
		if standing_instance_id is not None:
			self.inactive_instances.add(int(standing_instance_id))
		instance_id = self.fallen_tree_instances.get(identity)
		if instance_id is None:
			instance_id = len(self.instances)
			self.instances.append(row)
			self.fallen_tree_instances[identity] = instance_id
		else:
			for cell in self.fallen_tree_cells.get(identity, ()):
				members = self.cells.get(cell)
				if members is None:
					continue
				try:
					members.remove(instance_id)
				except ValueError:
					pass
				if not members:
					del self.cells[cell]
			self.instances[instance_id] = row
		cell_keys = tuple(_bounds_cells(bounds, self.cell_size))
		for cell in cell_keys:
			self.cells.setdefault(cell, []).append(instance_id)
		self.fallen_tree_cells[identity] = cell_keys
		return True

	def camouflage_bonus(self, observer, target, fired_recently=False):
		"""Return additive camouflage for this observer-target pair."""
		start = (float(observer[0]),
			float(observer[1]) + OBSERVER_EYE_HEIGHT,
			float(observer[2]))
		end = (float(target[0]), float(target[1]) + TARGET_CHECK_HEIGHT,
			float(target[2]))
		candidate_ids = []
		seen = set()
		for cell_x, cell_z in _segment_cells(start, end, self.cell_size):
			for instance_id in self.cells.get((cell_x, cell_z), ()):
				instance_id = int(instance_id)
				if instance_id not in seen:
					seen.add(instance_id)
					candidate_ids.append(instance_id)
		bonus = 0.0
		for instance_id in candidate_ids:
			if instance_id < 0 or instance_id >= len(self.instances):
				continue
			if instance_id in self.inactive_instances:
				continue
			instance = self.instances[instance_id]
			if fired_recently:
				if isinstance(instance, dict):
					center_x = instance['center'][0]
					center_z = instance['center'][2]
					radius = instance['radius']
				else:
					center_x = instance[0]
					center_z = instance[2]
					radius = instance[9]
				dx = float(target[0]) - float(center_x)
				dz = float(target[2]) - float(center_z)
				if math.sqrt(dx * dx + dz * dz) <= (
						FIRE_TRANSPARENCY_DISTANCE + float(radius)):
					continue
			if _intersects(instance, start, end):
				bonus += float(
					instance['strength'] if isinstance(instance, dict)
					else instance[8])
				if bonus >= FOLIAGE_CAMOUFLAGE_LIMIT:
					return FOLIAGE_CAMOUFLAGE_LIMIT
		return min(FOLIAGE_CAMOUFLAGE_LIMIT, max(0.0, bonus))
