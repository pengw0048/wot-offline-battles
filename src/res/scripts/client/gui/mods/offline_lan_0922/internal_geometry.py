import math


GEOMETRY_MODE = 'profile'

_PROBE_CACHE = {}
_PROBE_CACHE_LIMIT = 256


def _value(value, key, default=None):
	if value is None:
		return default
	if isinstance(value, dict):
		return value.get(key, default)
	return getattr(value, key, default)


def _number(value, default=0.0):
	try:
		return float(value)
	except Exception:
		return float(default)


def _vector_tuple(value):
	try:
		return (float(value.x), float(value.y), float(value.z))
	except Exception:
		pass
	try:
		return (float(value[0]), float(value[1]), float(value[2]))
	except Exception:
		return None


def _clamp(value, minimum, maximum):
	return min(maximum, max(minimum, value))


def _span(bounds):
	return tuple(bounds[1][axis] - bounds[0][axis] for axis in range(3))


def _fraction_point(bounds, fractions):
	minimum, maximum = bounds
	return tuple(minimum[axis] + (maximum[axis] - minimum[axis]) *
		float(fractions[axis]) for axis in range(3))


def _component_model_paths(component):
	result = []
	models = _value(component, 'models', None)
	for source in (models, component):
		if source is None:
			continue
		for key in ('undamaged', 'destroyed', 'exploded', 'model', 'modelName'):
			value = _value(source, key, None)
			if value in (None, ''):
				continue
			text = str(value)
			if text not in result:
				result.append(text)
	return tuple(result)


def _component_identity(component, bounds):
	return '%s|%s|%s|%s|%.5f,%.5f,%.5f' % (
		str(_value(component, 'name', '')),
		str(_value(component, 'id', '')),
		str(_value(component, 'compactDescr', '')),
		','.join(_component_model_paths(component)),
		_span(bounds)[0], _span(bounds)[1], _span(bounds)[2])


def _line_intervals(hit_tester, start, end, axis):
	try:
		try:
			import Math
			try:
				start_value = Math.Vector3(float(start[0]), float(start[1]),
					float(start[2]))
				end_value = Math.Vector3(float(end[0]), float(end[1]),
					float(end[2]))
			except Exception:
				start_value = Math.Vector3(start)
				end_value = Math.Vector3(end)
		except Exception:
			start_value = start
			end_value = end
		collisions = hit_tester.localHitTest(start_value, end_value)
	except Exception:
		return ()
	if not collisions:
		return ()
	length = abs(float(end[axis]) - float(start[axis]))
	if length <= 0.0001:
		return ()
	coordinates = []
	for collision in collisions:
		try:
			distance = float(collision[0])
		except Exception:
			continue
		coordinate = float(start[axis]) + distance
		if coordinate < min(start[axis], end[axis]) - 0.01:
			continue
		if coordinate > max(start[axis], end[axis]) + 0.01:
			continue
		coordinates.append(coordinate)
	coordinates.sort()
	filtered = []
	for coordinate in coordinates:
		if not filtered or abs(coordinate - filtered[-1]) > 0.002:
			filtered.append(coordinate)
	intervals = []
	index = 0
	while index + 1 < len(filtered):
		low = filtered[index]
		high = filtered[index + 1]
		if high - low > 0.02:
			intervals.append((low, high))
		index += 2
	if not intervals and len(filtered) >= 2:
		low = filtered[0]
		high = filtered[-1]
		if high - low > 0.02:
			intervals.append((low, high))
	return tuple(intervals)


def _build_probe(component, bounds):
	hit_tester = _value(component, 'hitTester', None)
	if hit_tester is None or not hasattr(hit_tester, 'localHitTest'):
		return {'mode': 'bbox_only', 'cells': {}, 'nx': 0, 'nz': 0}
	try:
		if hasattr(hit_tester, 'loadBspModel'):
			hit_tester.loadBspModel()
	except Exception:
		pass
	minimum, maximum = bounds
	span = _span(bounds)
	nx = 9
	nz = 11
	margin = max(0.05, span[1] * 0.03)
	cells = {}
	for ix in range(nx):
		x_fraction = (float(ix) + 0.5) / float(nx)
		x = minimum[0] + span[0] * x_fraction
		for iz in range(nz):
			z_fraction = (float(iz) + 0.5) / float(nz)
			z = minimum[2] + span[2] * z_fraction
			start = (x, minimum[1] - margin, z)
			end = (x, maximum[1] + margin, z)
			intervals = _line_intervals(hit_tester, start, end, 1)
			if intervals:
				cells[(ix, iz)] = max(intervals,
					key=lambda interval: interval[1] - interval[0])
	return {
		'mode': ('collision_grid' if cells else 'bbox_only'),
		'cells': cells,
		'nx': nx,
		'nz': nz,
		'bounds': bounds,
	}


def component_probe(component, bounds, allow_native=False):
	key = '%s|native=%d' % (
		_component_identity(component, bounds), int(bool(allow_native)))
	probe = _PROBE_CACHE.get(key)
	if probe is not None:
		return probe
	if allow_native:
		probe = _build_probe(component, bounds)
	else:
		probe = {
			'mode': 'bbox_only_startup_safe',
			'cells': {},
			'nx': 0,
			'nz': 0,
			'bounds': bounds,
		}
	probe['component_identity'] = key
	if len(_PROBE_CACHE) >= _PROBE_CACHE_LIMIT:
		_PROBE_CACHE.clear()
	_PROBE_CACHE[key] = probe
	return probe


def _select_probe_cell(probe, bounds, anchor, required_height):
	if probe.get('mode') != 'collision_grid':
		return None
	minimum, maximum = bounds
	span = _span(bounds)
	nx = probe['nx']
	nz = probe['nz']
	best = None
	for key, interval in probe['cells'].items():
		ix, iz = key
		x = minimum[0] + span[0] * (float(ix) + 0.5) / float(nx)
		z = minimum[2] + span[2] * (float(iz) + 0.5) / float(nz)
		height = interval[1] - interval[0]
		if height < min(required_height * 1.2, span[1] * 0.85):
			continue
		dx = (x - anchor[0]) / max(0.01, span[0])
		dz = (z - anchor[2]) / max(0.01, span[2])
		dy = 0.0
		if anchor[1] < interval[0]:
			dy = (interval[0] - anchor[1]) / max(0.01, span[1])
		elif anchor[1] > interval[1]:
			dy = (anchor[1] - interval[1]) / max(0.01, span[1])
		score = dx * dx + dz * dz + dy * dy * 0.4
		candidate = (score, key, x, z, interval)
		if best is None or candidate[0] < best[0]:
			best = candidate
	return best


def _contiguous_cell_bounds(probe, bounds, key, y_value):
	minimum, maximum = bounds
	span = _span(bounds)
	nx = probe['nx']
	nz = probe['nz']
	ix, iz = key
	left = ix
	right = ix
	back = iz
	front = iz
	while left - 1 >= 0:
		interval = probe['cells'].get((left - 1, iz))
		if interval is None or not (interval[0] <= y_value <= interval[1]):
			break
		left -= 1
	while right + 1 < nx:
		interval = probe['cells'].get((right + 1, iz))
		if interval is None or not (interval[0] <= y_value <= interval[1]):
			break
		right += 1
	while back - 1 >= 0:
		interval = probe['cells'].get((ix, back - 1))
		if interval is None or not (interval[0] <= y_value <= interval[1]):
			break
		back -= 1
	while front + 1 < nz:
		interval = probe['cells'].get((ix, front + 1))
		if interval is None or not (interval[0] <= y_value <= interval[1]):
			break
		front += 1
	cell_x = span[0] / float(nx)
	cell_z = span[2] / float(nz)
	return (
		minimum[0] + left * cell_x,
		minimum[0] + (right + 1) * cell_x,
		minimum[2] + back * cell_z,
		minimum[2] + (front + 1) * cell_z,
	)


def _component_weight(vehicle_descriptor, entity):
	component_name = {
		'engine': 'engine',
		'fuelTank': 'fuelTank',
		'radio': 'radio',
		'gun': 'gun',
	}.get(entity)
	if component_name:
		component = getattr(vehicle_descriptor, component_name, None)
		weight = _number(_value(component, 'weight', 0.0), 0.0)
		if weight > 0.0:
			return weight, 'VehicleDescr.%s.weight' % component_name
	if entity == 'ammoBay':
		gun = getattr(vehicle_descriptor, 'gun', None)
		capacity = int(_number(_value(gun, 'maxAmmo',
			_value(vehicle_descriptor, 'maxAmmo', 0)), 0.0))
		shots = _value(gun, 'shots', ()) or ()
		weights = []
		for shot in shots:
			shell = _value(shot, 'shell', None)
			weight = _number(_value(shell, 'weight', 0.0), 0.0)
			if weight > 0.0:
				weights.append(weight)
		if capacity > 0 and weights:
			return max(20.0, capacity * sum(weights) / float(len(weights))), \
				'gun.maxAmmo*shell.weight'
	if entity == 'turretRotator':
		turret = getattr(vehicle_descriptor, 'turret', None)
		weight = _number(_value(turret, 'weight', 0.0), 0.0)
		if weight > 0.0:
			return max(30.0, weight * 0.08), 'VehicleDescr.turret.weight*0.08'
	if entity == 'surveyingDevice':
		turret = getattr(vehicle_descriptor, 'turret', None)
		weight = _number(_value(turret, 'weight', 0.0), 0.0)
		if weight > 0.0:
			return max(8.0, weight * 0.012), 'VehicleDescr.turret.weight*0.012'
	if entity.startswith('crew:'):
		return 80.0, 'local_emulation.crew_mass_80kg'
	return 0.0, 'unavailable'


def _is_fixed_fighting_compartment(vehicle_descriptor):
	try:
		tags = set(vehicle_descriptor.type.tags or ())
	except Exception:
		tags = set()
	if 'AT-SPG' not in tags and 'SPG' not in tags:
		return False
	try:
		yaw_limits = _value(vehicle_descriptor.turret, 'yawLimits')
		if yaw_limits is None:
			return False
		left = float(yaw_limits[0])
		right = float(yaw_limits[1])
		return max(0.0, right - left) < math.radians(270.0)
	except Exception:
		return True


def _physical_half_cap(vehicle_descriptor, entity, kind, zone_id):
	zone_id = str(zone_id or '').lower()
	if kind == 'crew':
		return (0.26, 0.55, 0.32)
	if entity == 'surveyingDevice':
		if ('sight' in zone_id or 'driver' in zone_id or
				'viewport' in zone_id or 'visor' in zone_id):
			return (0.12, 0.08, 0.14)
		if 'roof' in zone_id or 'cupola' in zone_id:
			return (0.14, 0.10, 0.16)
		return (0.16, 0.11, 0.18)
	if entity == 'radio':
		return (0.30, 0.23, 0.34)
	if entity == 'turretRotator':
		if _is_fixed_fighting_compartment(vehicle_descriptor):
			return (0.50, 0.24, 0.52)
		return (0.78, 0.24, 0.78)
	if entity == 'engine':
		return (0.95, 0.72, 1.10)
	if entity == 'ammoBay':
		return (0.78, 0.52, 0.92)
	if entity == 'fuelTank':
		return (0.50, 0.50, 0.86)
	if entity == 'gun':
		return (0.25, 0.25, 1.25)
	return None


def _module_physical_half(vehicle_descriptor, entity, kind, bounds,
		profile_half, zone_id=''):
	span = _span(bounds)
	profile = tuple(max(0.02, min(span[axis] * 0.40,
		span[axis] * float(profile_half[axis]))) for axis in range(3))
	cap = _physical_half_cap(vehicle_descriptor, entity, kind, zone_id)
	if cap is None:
		result = profile
	else:
		result = tuple(max(0.02, min(profile[axis], float(cap[axis]),
			span[axis] * 0.40)) for axis in range(3))
	return tuple(result), tuple(profile), (tuple(cap) if cap is not None else None)


def _rotated_aabb_bounds(center, half, yaw_degrees=0.0):
	center = tuple(float(value) for value in center)
	half = tuple(max(0.0, float(value)) for value in half)
	yaw = math.radians(float(yaw_degrees or 0.0))
	cosine = abs(math.cos(yaw))
	sine = abs(math.sin(yaw))
	extent_x = half[0] * cosine + half[2] * sine
	extent_z = half[0] * sine + half[2] * cosine
	extents = (extent_x, half[1], extent_z)
	return (tuple(center[axis] - extents[axis] for axis in range(3)),
		tuple(center[axis] + extents[axis] for axis in range(3)))


def _box(center, half, primitive_id, yaw_degrees=0.0):
	minimum, maximum = _rotated_aabb_bounds(center, half, yaw_degrees)
	return {
		'shape': 'aabb',
		'primitive_id': primitive_id,
		'center': tuple(center),
		'half_extents': tuple(half),
		'rotation_yaw_degrees': float(yaw_degrees or 0.0),
		'minimum': minimum,
		'maximum': maximum,
	}


def _sphere(center, radius, primitive_id):
	radius = max(0.001, float(radius))
	return {
		'shape': 'sphere',
		'primitive_id': primitive_id,
		'center': tuple(center),
		'radius': radius,
		'half_extents': (radius, radius, radius),
		'minimum': tuple(center[axis] - radius for axis in range(3)),
		'maximum': tuple(center[axis] + radius for axis in range(3)),
	}


def _ellipsoid(center, radii, primitive_id, yaw_degrees=0.0):
	radii = tuple(max(0.001, float(value)) for value in radii)
	minimum, maximum = _rotated_aabb_bounds(center, radii, yaw_degrees)
	return {
		'shape': 'ellipsoid',
		'primitive_id': primitive_id,
		'center': tuple(center),
		'radii': radii,
		'half_extents': radii,
		'rotation_yaw_degrees': float(yaw_degrees or 0.0),
		'minimum': minimum,
		'maximum': maximum,
	}


def _capsule(center, radius, half_length, axis, primitive_id,
		yaw_degrees=0.0):
	axis = str(axis or 'y').lower()
	if axis not in ('x', 'y', 'z'):
		axis = 'y'
	radius = max(0.001, float(radius))
	half_length = max(0.0, float(half_length))
	axis_half = half_length + radius
	if axis == 'x':
		half = (axis_half, radius, radius)
	elif axis == 'z':
		half = (radius, radius, axis_half)
	else:
		half = (radius, axis_half, radius)
	minimum, maximum = _rotated_aabb_bounds(center, half, yaw_degrees)
	return {
		'shape': 'capsule',
		'primitive_id': primitive_id,
		'center': tuple(center),
		'radius': radius,
		'half_length': half_length,
		'axis': axis,
		'half_extents': half,
		'rotation_yaw_degrees': float(yaw_degrees or 0.0),
		'minimum': minimum,
		'maximum': maximum,
	}


def _capsule_from_half(center, half, primitive_id, axis=None,
		yaw_degrees=0.0, radius_factor=0.88):
	half = tuple(max(0.015, float(value)) for value in half)
	if axis not in ('x', 'y', 'z'):
		axis_index = max(range(3), key=lambda index: half[index])
		axis = ('x', 'y', 'z')[axis_index]
	else:
		axis_index = ('x', 'y', 'z').index(axis)
	perpendicular = [half[index] for index in range(3)
		if index != axis_index]
	radius = max(0.015, min(perpendicular) * float(radius_factor))
	axis_half = max(radius, half[axis_index] * 0.96)
	return _capsule(center, radius, max(0.0, axis_half - radius), axis,
		primitive_id, yaw_degrees)


def _crew_primitives(center, half, zone_id):
	hx, hy, hz = half
	body_center = (center[0], center[1] - hy * 0.10, center[2])
	body_half = (hx * 0.82, hy * 0.72, hz * 0.82)
	body = _capsule_from_half(body_center, body_half,
		zone_id + ':body', 'y', 0.0, 0.92)
	head_radius = max(0.055, min(hx * 0.62, hz * 0.62, hy * 0.20))
	head_center = (center[0], center[1] + hy * 0.68, center[2])
	return (body, _sphere(head_center, head_radius, zone_id + ':head'))


def _ring_primitives(center, half, zone_id):
	hx, hy, hz = half
	tube_radius = max(0.025, min(hy * 0.92, hx * 0.14, hz * 0.14))
	radius_x = max(tube_radius * 1.25, hx - tube_radius)
	radius_z = max(tube_radius * 1.25, hz - tube_radius)
	segment_count = 12
	primitives = []
	for index in range(segment_count):
		angle0 = math.pi * 2.0 * float(index) / float(segment_count)
		angle1 = math.pi * 2.0 * float(index + 1) / float(segment_count)
		first = (center[0] + math.cos(angle0) * radius_x, center[1],
			center[2] + math.sin(angle0) * radius_z)
		second = (center[0] + math.cos(angle1) * radius_x, center[1],
			center[2] + math.sin(angle1) * radius_z)
		midpoint = tuple((first[axis] + second[axis]) * 0.5
			for axis in range(3))
		dx = second[0] - first[0]
		dz = second[2] - first[2]
		length = math.sqrt(dx * dx + dz * dz)
		yaw = math.degrees(math.atan2(dz, dx))
		primitives.append(_capsule(midpoint, tube_radius, length * 0.5,
			'x', zone_id + ':ring%02d' % index, yaw))
	return tuple(primitives)


def _fixed_traverse_primitives(center, half, zone_id):
	hx, hy, hz = half
	core = _capsule_from_half(center,
		(max(0.04, hx * 0.54), hy, max(0.04, hz * 0.74)),
		zone_id + ':gun_mount', 'z', 0.0, 0.86)
	actuator_half = (max(0.03, hx * 0.20),
		max(0.03, hy * 0.58), max(0.03, hz * 0.40))
	offset = max(0.0, hx - actuator_half[0])
	left = _capsule_from_half(
		(center[0] - offset, center[1], center[2]), actuator_half,
		zone_id + ':left_actuator', 'z', 0.0, 0.82)
	right = _capsule_from_half(
		(center[0] + offset, center[1], center[2]), actuator_half,
		zone_id + ':right_actuator', 'z', 0.0, 0.82)
	return (core, left, right)


def _ammo_primitives(center, half, zone_id):
	hx, hy, hz = half
	primitives = []
	if hx >= hz:
		row_half = (hx / 3.2, hy, hz * 0.82)
		for index, offset in enumerate((-0.62, 0.0, 0.62)):
			primitives.append(_box(
				(center[0] + hx * offset, center[1], center[2]),
				row_half, zone_id + ':row%d' % index))
	else:
		row_half = (hx * 0.82, hy, hz / 3.2)
		for index, offset in enumerate((-0.62, 0.0, 0.62)):
			primitives.append(_box(
				(center[0], center[1], center[2] + hz * offset),
				row_half, zone_id + ':row%d' % index))
	return tuple(primitives)


def _engine_primitives(center, half, zone_id):
	hx, hy, hz = half
	main_half = (hx * 0.82, hy * 0.78, hz * 0.72)
	upper_half = (hx * 0.62, hy * 0.30, hz * 0.62)
	return (
		_box((center[0], center[1] - hy * 0.10, center[2]),
			main_half, zone_id + ':block'),
		_box((center[0], center[1] + hy * 0.58, center[2]),
			upper_half, zone_id + ':upper'),
	)


def _build_primitives(entity, kind, center, half, zone_id,
		fixed_fighting_compartment=False, shape_hint=None):
	shape_hint = str(shape_hint or 'box')
	if kind == 'crew' or shape_hint == 'crew_capsule_head':
		return _crew_primitives(center, half, zone_id)
	if shape_hint == 'elliptic_ring_capsules':
		if fixed_fighting_compartment:
			return _fixed_traverse_primitives(center, half, zone_id)
		return _ring_primitives(center, half, zone_id)
	if shape_hint == 'gun_traverse_capsules':
		return _fixed_traverse_primitives(center, half, zone_id)
	if shape_hint == 'ammo_rack_boxes' or entity == 'ammoBay':
		return _ammo_primitives(center, half, zone_id)
	if shape_hint == 'engine_compound_box' or entity == 'engine':
		return _engine_primitives(center, half, zone_id)
	if shape_hint == 'capsule_longest':
		return (_capsule_from_half(center, half, zone_id + ':body'),)
	if shape_hint == 'capsule_x':
		return (_capsule_from_half(center, half, zone_id + ':body', 'x'),)
	if shape_hint == 'capsule_y':
		return (_capsule_from_half(center, half, zone_id + ':body', 'y'),)
	if shape_hint == 'capsule_z' or entity == 'gun':
		return (_capsule_from_half(center, half, zone_id + ':body', 'z'),)
	if shape_hint == 'sphere':
		return (_sphere(center, min(half), zone_id + ':body'),)
	if shape_hint == 'ellipsoid':
		return (_ellipsoid(center, tuple(max(0.015, value * 0.94)
			for value in half), zone_id + ':body'),)
	return (_box(center, half, zone_id + ':body'),)

def _primitive_volume(primitive):
	shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
	if shape == 'sphere':
		radius = float(primitive.get('radius', 0.0))
		return 4.0 * math.pi * radius * radius * radius / 3.0
	if shape == 'ellipsoid':
		radii = primitive.get('radii', primitive.get('half_extents', (0, 0, 0)))
		return 4.0 * math.pi * float(radii[0]) * float(radii[1]) * float(radii[2]) / 3.0
	if shape == 'capsule':
		radius = float(primitive.get('radius', 0.0))
		half_length = float(primitive.get('half_length', 0.0))
		return (math.pi * radius * radius * (half_length * 2.0) +
			4.0 * math.pi * radius * radius * radius / 3.0)
	half = primitive.get('half_extents', (0.0, 0.0, 0.0))
	return 8.0 * half[0] * half[1] * half[2]

def _penetration_resistance(vehicle_descriptor, entity, kind, primitives):
	logical_entity = ('crew:' + entity if kind == 'crew' else entity)
	weight, weight_source = _component_weight(
		vehicle_descriptor, logical_entity)
	volume = max(0.001, sum(_primitive_volume(item) for item in primitives))
	if weight <= 0.0:
		fallback_density = {
			'ammoBay': 1050.0,
			'fuelTank': 260.0,
			'radio': 420.0,
			'turretRotator': 2200.0,
			'surveyingDevice': 350.0,
		}.get(entity, 700.0 if kind == 'module' else 110.0)
		density = fallback_density
		weight_source = 'entity_material_density_fallback'
	else:
		density = weight / volume
	steel_ratio = _clamp(density / 7850.0, 0.006, 0.65)
	resistance_per_meter = 1000.0 * steel_ratio
	return {
		'weight_kg': float(weight),
		'weight_source': weight_source,
		'volume_m3': volume,
		'effective_density_kg_m3': density,
		'penetration_resistance_mm_per_meter': resistance_per_meter,
		'resistance_source': ('component_mass_or_entity_density/'
			'fitted_primitive_volume_relative_to_RHA'),
	}


def _explicit_anchor_fractions(fractions):
	return tuple(_clamp(float(value), 0.02, 0.98) for value in fractions)

def fit_target(vehicle_descriptor, parent, entity, kind, bounds,
		center_fractions, half_fractions, zone_id, role_data=None,
		shape_hint=None):
	component = getattr(vehicle_descriptor, parent, None)
	probe = component_probe(component, bounds, allow_native=False)
	center_fractions = _explicit_anchor_fractions(center_fractions)
	anchor = _fraction_point(bounds, center_fractions)
	desired_half, profile_half_metres, physical_half_cap = (
		_module_physical_half(vehicle_descriptor, entity, kind, bounds,
			half_fractions, zone_id))
	selection = _select_probe_cell(probe, bounds, anchor,
		desired_half[1] * 2.0)
	minimum, maximum = bounds
	span = _span(bounds)
	fit_mode = probe.get('mode', 'bbox_only')
	if selection is None:
		center = list(anchor)
		half = [min(desired_half[axis], span[axis] * 0.40)
			for axis in range(3)]
		for axis in range(3):
			center[axis] = _clamp(center[axis], minimum[axis] + half[axis],
				maximum[axis] - half[axis])
	else:
		unused_score, cell_key, cell_x, cell_z, vertical = selection
		anchor_y_fraction = _clamp(float(center_fractions[1]), 0.0, 1.0)
		y_center = vertical[0] + (vertical[1] - vertical[0]) * anchor_y_fraction
		y_half = min(desired_half[1], (vertical[1] - vertical[0]) * 0.43)
		y_center = _clamp(y_center, vertical[0] + y_half,
			vertical[1] - y_half)
		cell_bounds = _contiguous_cell_bounds(probe, bounds, cell_key, y_center)
		x_half = min(desired_half[0], (cell_bounds[1] - cell_bounds[0]) * 0.43)
		z_half = min(desired_half[2], (cell_bounds[3] - cell_bounds[2]) * 0.43)
		x_center = _clamp(cell_x, cell_bounds[0] + x_half,
			cell_bounds[1] - x_half)
		z_center = _clamp(cell_z, cell_bounds[2] + z_half,
			cell_bounds[3] - z_half)
		center = [x_center, y_center, z_center]
		half = [max(0.025, x_half), max(0.025, y_half), max(0.025, z_half)]
	physical_cap_corrected = bool(any(abs(
		float(desired_half[axis]) - float(profile_half_metres[axis])) > 0.0001
		for axis in range(3)))
	collision_fit_corrected = bool(any(abs(
		float(half[axis]) - float(desired_half[axis])) > 0.0001
		for axis in range(3)))
	size_correction_reasons = []
	if physical_cap_corrected:
		size_correction_reasons.append('physical_cap')
	if collision_fit_corrected:
		size_correction_reasons.append('component_collision_fit')
	fixed_fighting_compartment = _is_fixed_fighting_compartment(
		vehicle_descriptor)
	effective_shape_hint = str(shape_hint or 'box')
	if (entity == 'turretRotator' and fixed_fighting_compartment and
			effective_shape_hint == 'elliptic_ring_capsules'):
		effective_shape_hint = 'gun_traverse_capsules'
	primitives = _build_primitives(entity, kind, tuple(center), tuple(half),
		zone_id, fixed_fighting_compartment, effective_shape_hint)
	primitive_minimum = tuple(min(item['minimum'][axis] for item in primitives)
		for axis in range(3))
	primitive_maximum = tuple(max(item['maximum'][axis] for item in primitives)
		for axis in range(3))
	resistance = _penetration_resistance(vehicle_descriptor, entity, kind,
		primitives)
	return {
		'center': tuple(center),
		'half_extents': tuple(half),
		'minimum': primitive_minimum,
		'maximum': primitive_maximum,
		'shape': 'compound',
		'primitives': tuple(primitives),
		'fit_mode': fit_mode,
		'fit_source': parent,
		'model_signature': probe.get('component_identity'),
		'geometry_mode': GEOMETRY_MODE,
		'size_policy': 'profile',
		'profile_half_extents_m': tuple(profile_half_metres),
		'physical_half_cap_m': physical_half_cap,
		'final_half_extents_m': tuple(half),
		'physical_cap_size_corrected': bool(physical_cap_corrected),
		'collision_fit_size_corrected': bool(collision_fit_corrected),
		'size_correction_applied': bool(physical_cap_corrected or
			collision_fit_corrected),
		'size_correction_reasons': tuple(size_correction_reasons),
		'fixed_fighting_compartment': bool(fixed_fighting_compartment),
		'shape_hint': effective_shape_hint,
		'shape_source': 'profile',
		'primitive_policy': effective_shape_hint,
		'resistance': resistance,
		'role_data': role_data,
	}


def _sample_primitive_points(primitive):
	shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
	center = tuple(float(v) for v in primitive.get('center', (0.0, 0.0, 0.0)))
	if shape == 'sphere':
		radius = max(0.0, float(primitive.get('radius', 0.0)))
		minimum = tuple(center[axis] - radius for axis in range(3))
		maximum = tuple(center[axis] + radius for axis in range(3))
	elif shape == 'ellipsoid':
		radii = tuple(float(value) for value in primitive.get(
			'radii', primitive.get('half_extents', (0.0, 0.0, 0.0))))
		minimum = tuple(center[axis] - radii[axis] for axis in range(3))
		maximum = tuple(center[axis] + radii[axis] for axis in range(3))
	elif shape == 'capsule':
		minimum = tuple(float(value) for value in primitive.get('minimum', center))
		maximum = tuple(float(value) for value in primitive.get('maximum', center))
	else:
		minimum = tuple(float(v) for v in primitive.get('minimum', center))
		maximum = tuple(float(v) for v in primitive.get('maximum', center))
	points = [center]
	for x in (minimum[0], maximum[0]):
		for y in (minimum[1], maximum[1]):
			for z in (minimum[2], maximum[2]):
				points.append((x, y, z))
	points.extend((
		(minimum[0], center[1], center[2]), (maximum[0], center[1], center[2]),
		(center[0], minimum[1], center[2]), (center[0], maximum[1], center[2]),
		(center[0], center[1], minimum[2]), (center[0], center[1], maximum[2]),
	))
	return tuple(points)

def _point_inside_component(hit_tester, bounds, point, safety_margin):
	minimum, maximum = bounds
	for axis in range(3):
		if point[axis] < minimum[axis] + safety_margin:
			return False
		if point[axis] > maximum[axis] - safety_margin:
			return False
	if hit_tester is None or not hasattr(hit_tester, 'localHitTest'):
		return True
	span = _span(bounds)
	line_margin = max(0.05, span[1] * 0.04)
	start = (point[0], minimum[1] - line_margin, point[2])
	end = (point[0], maximum[1] + line_margin, point[2])
	intervals = _line_intervals(hit_tester, start, end, 1)
	for low, high in intervals:
		if point[1] >= low + safety_margin and point[1] <= high - safety_margin:
			return True
	return False


def validate_target_geometry(vehicle_descriptor, parent, bounds, target,
		safety_margin=0.015):
	component = getattr(vehicle_descriptor, parent, None)
	hit_tester = _value(component, 'hitTester', None)
	if hit_tester is not None:
		try:
			if hasattr(hit_tester, 'loadBspModel'):
				hit_tester.loadBspModel()
		except Exception:
			pass
	invalid = []
	checked = 0
	for primitive in tuple(target.get('primitives', ())):
		for point in _sample_primitive_points(primitive):
			checked += 1
			if not _point_inside_component(hit_tester, bounds, point, safety_margin):
				invalid.append(point)
	return {
		'valid': not invalid,
		'checked_points': checked,
		'invalid_points': tuple(invalid[:24]),
		'mode': ('collision_model' if hit_tester is not None and
			hasattr(hit_tester, 'localHitTest') else 'bbox_only'),
		'safety_margin': float(safety_margin),
	}

def normalize_entity_resistance(vehicle_descriptor, targets):
	groups = {}
	for target in tuple(targets or ()):
		kind = target.get('kind', 'module')
		entity = target.get('entity', '')
		logical_entity = ('crew:' + entity if kind == 'crew' else entity)
		groups.setdefault((kind, entity, logical_entity), []).append(target)
	for (kind, entity, logical_entity), records in groups.items():
		weight, weight_source = _component_weight(
			vehicle_descriptor, logical_entity)
		if weight <= 0.0:
			continue
		total_volume = max(0.001, sum(max(0.0, _number(
			record.get('geometry_volume_m3', 0.0), 0.0))
			for record in records))
		density = weight / total_volume
		steel_ratio = _clamp(density / 7850.0, 0.006, 0.65)
		resistance_per_meter = 1000.0 * steel_ratio
		for record in records:
			zone_volume = max(0.0, _number(
				record.get('geometry_volume_m3', 0.0), 0.0))
			record['penetration_resistance_mm_per_meter'] = resistance_per_meter
			record['penetration_resistance_source'] = (
				'component_total_mass/distributed_total_fitted_volume_'
				'relative_to_RHA')
			record['geometry_mass_kg'] = float(weight)
			record['geometry_zone_mass_share_kg'] = float(weight) * (
				zone_volume / total_volume)
			record['geometry_mass_source'] = weight_source
			record['geometry_entity_total_volume_m3'] = total_volume
			record['geometry_effective_density_kg_m3'] = density
			record['geometry_physical_zone_count'] = len(records)
	return targets

def _segment_aabb_interval(start, end, minimum, maximum):
	t_min = 0.0
	t_max = 1.0
	for axis in range(3):
		origin = float(start[axis])
		direction = float(end[axis]) - origin
		if abs(direction) <= 0.0000001:
			if origin < minimum[axis] or origin > maximum[axis]:
				return None
			continue
		inverse = 1.0 / direction
		t1 = (minimum[axis] - origin) * inverse
		t2 = (maximum[axis] - origin) * inverse
		if t1 > t2:
			t1, t2 = t2, t1
		t_min = max(t_min, t1)
		t_max = min(t_max, t2)
		if t_min > t_max:
			return None
	return float(t_min), float(t_max)


def _rotate_point_y(point, center, degrees):
	try:
		angle = math.radians(float(degrees))
	except Exception:
		angle = 0.0
	if abs(angle) <= 0.0000001:
		return tuple(float(value) for value in point)
	dx = float(point[0]) - float(center[0])
	dz = float(point[2]) - float(center[2])
	cosine = math.cos(angle)
	sine = math.sin(angle)
	return (float(center[0]) + dx * cosine - dz * sine,
		float(point[1]), float(center[2]) + dx * sine + dz * cosine)


def _segment_oriented_box_interval(start, end, primitive):
	center = primitive.get('center', (0.0, 0.0, 0.0))
	half = primitive.get('half_extents', (0.1, 0.1, 0.1))
	yaw = float(primitive.get('rotation_yaw_degrees', 0.0) or 0.0)
	local_start = _rotate_point_y(start, center, -yaw)
	local_end = _rotate_point_y(end, center, -yaw)
	minimum = tuple(float(center[axis]) - float(half[axis]) for axis in range(3))
	maximum = tuple(float(center[axis]) + float(half[axis]) for axis in range(3))
	return _segment_aabb_interval(local_start, local_end, minimum, maximum)


def _segment_sphere_interval(start, end, center, radius):
	direction = tuple(float(end[axis]) - float(start[axis]) for axis in range(3))
	offset = tuple(float(start[axis]) - float(center[axis]) for axis in range(3))
	a = sum(value * value for value in direction)
	if a <= 0.0000001:
		return None
	b = 2.0 * sum(offset[axis] * direction[axis] for axis in range(3))
	c = sum(value * value for value in offset) - radius * radius
	discriminant = b * b - 4.0 * a * c
	if discriminant < 0.0:
		return None
	root = math.sqrt(discriminant)
	t1 = (-b - root) / (2.0 * a)
	t2 = (-b + root) / (2.0 * a)
	entry = max(0.0, min(t1, t2))
	exit_value = min(1.0, max(t1, t2))
	if entry > exit_value:
		return None
	return float(entry), float(exit_value)


def _segment_ellipsoid_interval(start, end, primitive):
	center = primitive.get('center', (0.0, 0.0, 0.0))
	radii = tuple(max(0.0001, float(value)) for value in primitive.get(
		'radii', primitive.get('half_extents', (0.1, 0.1, 0.1))))
	yaw = float(primitive.get('rotation_yaw_degrees', 0.0) or 0.0)
	local_start = _rotate_point_y(start, center, -yaw)
	local_end = _rotate_point_y(end, center, -yaw)
	normalized_start = tuple((float(local_start[axis]) - float(center[axis])) /
		radii[axis] for axis in range(3))
	normalized_end = tuple((float(local_end[axis]) - float(center[axis])) /
		radii[axis] for axis in range(3))
	return _segment_sphere_interval(normalized_start, normalized_end,
		(0.0, 0.0, 0.0), 1.0)


def _axis_components(axis):
	axis = str(axis or 'y').lower()
	if axis == 'x':
		return 0, (1, 2)
	if axis == 'z':
		return 2, (0, 1)
	return 1, (0, 2)


def _clip_interval(interval, minimum, maximum):
	if interval is None:
		return None
	entry = max(float(interval[0]), float(minimum))
	exit_value = min(float(interval[1]), float(maximum))
	if entry > exit_value:
		return None
	return entry, exit_value


def _segment_capsule_interval(start, end, primitive):
	center = tuple(float(value) for value in primitive.get(
		'center', (0.0, 0.0, 0.0)))
	yaw = float(primitive.get('rotation_yaw_degrees', 0.0) or 0.0)
	local_start = _rotate_point_y(start, center, -yaw)
	local_end = _rotate_point_y(end, center, -yaw)
	start_rel = tuple(float(local_start[axis]) - center[axis]
		for axis in range(3))
	end_rel = tuple(float(local_end[axis]) - center[axis]
		for axis in range(3))
	direction = tuple(end_rel[axis] - start_rel[axis] for axis in range(3))
	radius = max(0.0001, float(primitive.get('radius', 0.1)))
	half_length = max(0.0, float(primitive.get('half_length', 0.0)))
	axis_index, radial_axes = _axis_components(primitive.get('axis', 'y'))
	intervals = []
	a = sum(direction[index] * direction[index] for index in radial_axes)
	b = 2.0 * sum(start_rel[index] * direction[index]
		for index in radial_axes)
	c = sum(start_rel[index] * start_rel[index]
		for index in radial_axes) - radius * radius
	if a > 0.0000001:
		discriminant = b * b - 4.0 * a * c
		if discriminant >= 0.0:
			root = math.sqrt(discriminant)
			cylinder = (min((-b - root) / (2.0 * a),
				(-b + root) / (2.0 * a)),
				max((-b - root) / (2.0 * a),
				(-b + root) / (2.0 * a)))
			cylinder = _clip_interval(cylinder, 0.0, 1.0)
			if cylinder is not None:
				axis_start = start_rel[axis_index]
				axis_direction = direction[axis_index]
				if abs(axis_direction) <= 0.0000001:
					if axis_start >= -half_length and axis_start <= half_length:
						intervals.append(cylinder)
				else:
					t1 = (-half_length - axis_start) / axis_direction
					t2 = (half_length - axis_start) / axis_direction
					axis_interval = (min(t1, t2), max(t1, t2))
					clipped = _clip_interval(cylinder,
						axis_interval[0], axis_interval[1])
					if clipped is not None:
						intervals.append(clipped)
	elif c <= 0.0:
		axis_start = start_rel[axis_index]
		axis_direction = direction[axis_index]
		if abs(axis_direction) <= 0.0000001:
			if axis_start >= -half_length and axis_start <= half_length:
				intervals.append((0.0, 1.0))
		else:
			t1 = (-half_length - axis_start) / axis_direction
			t2 = (half_length - axis_start) / axis_direction
			clipped = _clip_interval((min(t1, t2), max(t1, t2)), 0.0, 1.0)
			if clipped is not None:
				intervals.append(clipped)
	for sign in (-1.0, 1.0):
		endpoint = [0.0, 0.0, 0.0]
		endpoint[axis_index] = sign * half_length
		interval = _segment_sphere_interval(start_rel, end_rel,
			tuple(endpoint), radius)
		if interval is not None:
			intervals.append(interval)
	if not intervals:
		return None
	entry = max(0.0, min(item[0] for item in intervals))
	exit_value = min(1.0, max(item[1] for item in intervals))
	if entry > exit_value:
		return None
	return float(entry), float(exit_value)


def primitive_interval(start, end, primitive):
	shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
	if shape == 'sphere':
		return _segment_sphere_interval(start, end, primitive['center'],
			primitive['radius'])
	if shape == 'ellipsoid':
		return _segment_ellipsoid_interval(start, end, primitive)
	if shape == 'capsule':
		return _segment_capsule_interval(start, end, primitive)
	if abs(float(primitive.get('rotation_yaw_degrees', 0.0) or 0.0)) > 0.0001:
		return _segment_oriented_box_interval(start, end, primitive)
	return _segment_aabb_interval(start, end, primitive['minimum'],
		primitive['maximum'])

def target_intervals(start, end, target):
	intervals = []
	for primitive in target.get('primitives', ()):
		interval = primitive_interval(start, end, primitive)
		if interval is None:
			continue
		intervals.append((float(interval[0]), float(interval[1]), primitive))
	intervals.sort(key=lambda item: (item[0], item[1],
		item[2].get('primitive_id', '')))
	merged = []
	for entry, exit_value, primitive in intervals:
		if not merged or entry > merged[-1][1] + 0.0001:
			merged.append([entry, exit_value, primitive])
		else:
			merged[-1][1] = max(merged[-1][1], exit_value)
	return tuple((float(item[0]), float(item[1]), item[2])
		for item in merged)


def target_interval(start, end, target):
	intervals = target_intervals(start, end, target)
	return intervals[0] if intervals else None


def clear_cache():
	_PROBE_CACHE.clear()
