# -*- coding: utf-8 -*-
"""Engine-free legacy spotting and camouflage calculations for #1513."""

from __future__ import division

import math

from gui.mods.offline_lan_0922 import shot_geometry


PROXIMITY_SPOT_DISTANCE = 50.0
# Exact #1513 ``constants.VISIBILITY.MAX_RADIUS``.  This is the detection
# ceiling, not the wider entity-AOI radius used to draw an already spotted tank.
MAX_SPOT_DISTANCE = 445.0
# Exact #1513 ``constants.AOI.VEHICLE_CIRCULAR_AOI_RADIUS``.  Team spotting may
# keep its ten-second memory beyond this boundary, but the local client must not
# draw the remote vehicle there.
VEHICLE_AOI_RADIUS = 565.0
# Exact #1513 ``constants.AOI.CIRCULAR_AOI_MARGIN``.  Native AOI keeps an
# already-present vehicle for this extra distance so movement along the
# boundary does not repeatedly add and remove its world presentation.
VEHICLE_AOI_HYSTERESIS_MARGIN = 5.0
# Retail #1513 varied the post-detection hold within a 5-10 second window.
# Use its no-skill guaranteed-disappearance bound so deterministic LAN peers
# never hide a target earlier than the retail rule allowed.
SPOT_MEMORY_SECONDS = 10.0
# ``gunner_rancorous`` extends the ordinary visibility lease by two seconds
# while its living carrier keeps the target inside the five-degree sector.
DESIGNATED_SPOT_MEMORY_SECONDS = SPOT_MEMORY_SECONDS + 2.0
LAST_EFFORT_SECONDS = 2.0
MOVING_SPEED_EPSILON = 0.5
SHOT_CAMOUFLAGE_SECONDS = 0.75


def _field(value, name, default=None):
	if isinstance(value, dict):
		return value.get(name, default)
	return getattr(value, name, default)


def _finite_number(value, label):
	if isinstance(value, bool):
		raise ValueError('%s is not a finite number' % label)
	try:
		value = float(value)
	except (TypeError, ValueError, OverflowError):
		raise ValueError('%s is not a finite number' % label)
	if value != value or abs(value) == float('inf'):
		raise ValueError('%s is not a finite number' % label)
	return value


def _vector3(value, label):
	try:
		values = tuple(value)
	except (TypeError, ValueError):
		raise ValueError('%s is not a three-component vector' % label)
	if len(values) != 3:
		raise ValueError('%s is not a three-component vector' % label)
	return tuple(_finite_number(values[index], '%s[%d]' % (label, index))
		for index in range(3))


def _add(first, second):
	return (first[0] + second[0], first[1] + second[1],
		first[2] + second[2])


def _component_bbox(descriptor, component_name):
	component = _field(descriptor, component_name)
	if component is None:
		raise ValueError('descriptor has no %s component' % component_name)
	hit_tester = _field(component, 'hitTester')
	bbox = _field(hit_tester, 'bbox')
	if bbox is None:
		raise ValueError('%s.hitTester.bbox is unavailable' % component_name)
	try:
		minimum = _vector3(
			bbox[0], '%s.hitTester.bbox[0]' % component_name)
		maximum = _vector3(
			bbox[1], '%s.hitTester.bbox[1]' % component_name)
	except (IndexError, KeyError, TypeError):
		raise ValueError('%s.hitTester.bbox has no min/max points' %
			component_name)
	for axis in range(3):
		if maximum[axis] <= minimum[axis]:
			raise ValueError('%s.hitTester.bbox is degenerate' % component_name)
	return minimum, maximum


def _visibility_mount_offsets(descriptor):
	chassis = _field(descriptor, 'chassis')
	hull = _field(descriptor, 'hull')
	if chassis is None or hull is None:
		raise ValueError('descriptor has no chassis or hull component')
	hull_position = _vector3(
		_field(chassis, 'hullPosition'), 'chassis.hullPosition')
	turret_positions = _field(hull, 'turretPositions')
	try:
		turret_position = turret_positions[0]
	except (IndexError, KeyError, TypeError):
		raise ValueError('hull.turretPositions has no index 0')
	return hull_position, _vector3(
		turret_position, 'hull.turretPositions[0]')


def _mounted_bbox(bbox, offset):
	return _add(bbox[0], offset), _add(bbox[1], offset)


def _vehicle_visibility_bounds(descriptor):
	"""Return the gun-excluding descriptor bounds in vehicle-local space."""
	if descriptor is None:
		raise ValueError('vehicle descriptor is unavailable')
	hull_position, turret_position = _visibility_mount_offsets(descriptor)
	boxes = [
		_component_bbox(descriptor, 'chassis'),
		_mounted_bbox(
			_component_bbox(descriptor, 'hull'), hull_position),
		_mounted_bbox(
			_component_bbox(descriptor, 'turret'),
			_add(hull_position, turret_position)),
	]
	minimum = tuple(min(box[0][axis] for box in boxes)
		for axis in range(3))
	maximum = tuple(max(box[1][axis] for box in boxes)
		for axis in range(3))
	return minimum, maximum


def _visibility_pose(pose):
	"""Return position and the five exact angles from one frozen pose."""
	if isinstance(pose, (list, tuple)):
		if len(pose) != 6:
			raise ValueError('visibility pose is not a six-component tuple')
		position, yaw, pitch, roll, turret_yaw, gun_pitch = pose
	else:
		position = _field(pose, 'position')
		yaw = _field(pose, 'yaw')
		pitch = _field(pose, 'pitch')
		roll = _field(pose, 'roll')
		turret_yaw = _field(pose, 'turret_yaw')
		gun_pitch = _field(pose, 'gun_pitch')
	return (
		_vector3(position, 'visibility pose position'),
		_finite_number(yaw, 'visibility pose yaw'),
		_finite_number(pitch, 'visibility pose pitch'),
		_finite_number(roll, 'visibility pose roll'),
		_finite_number(turret_yaw, 'visibility pose turret yaw'),
		_finite_number(gun_pitch, 'visibility pose gun pitch'),
	)


def _bbox_visibility_points(bounds):
	minimum, maximum = bounds
	centre = tuple((minimum[axis] + maximum[axis]) * 0.5
		for axis in range(3))
	return (
		(centre[0], maximum[1], centre[2]),
		(centre[0], centre[1], maximum[2]),
		(centre[0], centre[1], minimum[2]),
		(minimum[0], centre[1], centre[2]),
		(maximum[0], centre[1], centre[2]),
	)


def vehicle_visibility_layout(descriptor, pose):
	"""Build both official PC visibility lists for one #1513 pose.

	The five hull-aligned points are the top, front, back, left and right face
	centres of the mounted chassis/hull/turret bounds.  The gun component is
	deliberately excluded.  The final two points are the current gun pivot and
	the same pivot with the turret fixed straight ahead; they coincide while
	the turret yaw is zero but remain separate structural checkpoints here.
	The official material establishes this structure, while the descriptor-to-
	point projection remains the target-client adapter exercised by this port.
	"""
	(position, yaw, pitch, roll,
	 turret_yaw, gun_pitch) = _visibility_pose(pose)
	local_points = _bbox_visibility_points(
		_vehicle_visibility_bounds(descriptor))
	points = [shot_geometry.transform_vehicle_point(
		point, position, yaw, pitch, roll) for point in local_points]
	current_pivot = shot_geometry.shot_origin_and_direction(
		descriptor, position, yaw, pitch, roll,
		turret_yaw, gun_pitch)[0]
	fixed_pivot = shot_geometry.shot_origin_and_direction(
		descriptor, position, yaw, pitch, roll, 0.0, gun_pitch)[0]
	points.extend((current_pivot, fixed_pivot))
	return (points[0], current_pivot), tuple(points)


def vehicle_visibility_checkpoints(descriptor, pose):
	"""Return the seven target checkpoints from one frozen layout."""
	return vehicle_visibility_layout(descriptor, pose)[1]


def vehicle_view_range_ports(descriptor, pose):
	"""Return the vehicle top and current gun-pivot observation ports."""
	return vehicle_visibility_layout(descriptor, pose)[0]


def trim_visibility_ray(start, end, clearance=4.0):
	"""Trim an equal vehicle clearance from both ends of one world-space ray.

	The current #1513 adapter exposes vehicle compounds to the same world query.
	Retain the established four-metre endpoint exclusion so an observer port or
	target checkpoint cannot immediately collide with its owning vehicle.  A
	segment too short to retain a positive middle interval returns ``None``.
	"""
	start = _vector3(start, 'visibility ray start')
	end = _vector3(end, 'visibility ray end')
	clearance = _finite_number(clearance, 'visibility ray clearance')
	if clearance < 0.0:
		raise ValueError('visibility ray clearance is negative')
	delta = tuple(end[axis] - start[axis] for axis in range(3))
	length = math.sqrt(sum(value * value for value in delta))
	if length <= 1.0e-12 or length <= clearance * 2.0:
		return None
	unit = tuple(value / length for value in delta)
	return (
		tuple(start[axis] + unit[axis] * clearance
			for axis in range(3)),
		tuple(end[axis] - unit[axis] * clearance
			for axis in range(3)),
	)


def visibility_ray_count(observer_layout, target_layout):
	"""Return the stable structural ray-slot count for two layouts."""
	ports = observer_layout[0]
	checkpoints = target_layout[1]
	if not ports or not checkpoints:
		return 0
	return len(ports) * len(checkpoints)


def visibility_ray_at(observer_layout, target_layout, index,
		clearance=4.0):
	"""Build one target-major structural ray slot on demand."""
	if isinstance(index, bool):
		raise ValueError('visibility ray index is invalid')
	try:
		index = int(index)
	except (TypeError, ValueError, OverflowError):
		raise ValueError('visibility ray index is invalid')
	ports = observer_layout[0]
	checkpoints = target_layout[1]
	count = visibility_ray_count(observer_layout, target_layout)
	if index < 0 or index >= count:
		raise ValueError('visibility ray index is out of range')
	port_count = len(ports)
	port_index = index % port_count
	checkpoint_index = index // port_count
	if (ports[port_index] in ports[:port_index] or
			checkpoints[checkpoint_index] in
			checkpoints[:checkpoint_index]):
		return None
	return trim_visibility_ray(
		ports[port_index], checkpoints[checkpoint_index],
		clearance)


def visibility_rays(observer_descriptor, observer_pose,
		target_descriptor, target_pose, clearance=4.0):
	"""Return unique target-major, observer-port-minor visibility rays."""
	observer_layout = vehicle_visibility_layout(
		observer_descriptor, observer_pose)
	target_layout = vehicle_visibility_layout(
		target_descriptor, target_pose)
	result = []
	seen = set()
	for index in range(visibility_ray_count(
			observer_layout, target_layout)):
		ray = visibility_ray_at(
			observer_layout, target_layout, index, clearance)
		if ray is None or ray in seen:
			continue
		seen.add(ray)
		result.append(ray)
	return tuple(result)


def clamp(value, minimum, maximum):
	return max(float(minimum), min(float(maximum), float(value)))


# optional_devices.xml gives both situational devices activateWhenStillSec 3.0.
STILL_DEVICE_DELAY_SECONDS = 3.0


def effective_view_range(base_range, misc_factor=1.0, crew_factor=1.0,
		binocular_factor=1.0, binocular_active=False):
	"""#1513 ``utils.getCircularVisionRadius`` with the still device gated.

	``misc_factor`` is ``miscAttrs['circularVisionRadiusFactor']`` times any
	damage factor; ``crew_factor`` is ``factors['circularVisionRadius']``
	without the stereoscope, which the battle applies only after the vehicle
	has stood still long enough.
	"""
	result = max(PROXIMITY_SPOT_DISTANCE, float(base_range or 0.0))
	result *= max(0.0, float(misc_factor or 0.0))
	result *= max(0.0, float(crew_factor or 0.0))
	if binocular_active:
		result *= max(1.0, float(binocular_factor or 1.0))
	return max(PROXIMITY_SPOT_DISTANCE, result)


def base_camouflage(moving_base, still_base, crew_factor=0.57,
		invisibility_factor=1.0, paint_bonus=0.0):
	"""Reproduce #1513 VehicleDescr.computeBaseInvisibility composition."""
	factor = (max(0.0, float(crew_factor or 0.0)) *
		max(0.0, float(invisibility_factor or 0.0)))
	bonus = max(0.0, float(paint_bonus or 0.0))
	return (max(0.0, float(moving_base or 0.0)) * factor + bonus,
		max(0.0, float(still_base or 0.0)) * factor + bonus)


def effective_camouflage(base_pair, moving=False, additive=0.0,
		multiplier=1.0, shot_factor=1.0, fired_recently=False,
		foliage_bonus=0.0):
	"""#1513 ``utils.getInvisibility`` plus the shot and foliage terms.

	``additive`` and ``multiplier`` are the aspect the caller resolved from
	``factors['invisibility']``: the camouflage net lives in the stationary
	aspect only.
	"""
	if not isinstance(base_pair, (list, tuple)) or len(base_pair) < 2:
		base_pair = (0.0, 0.0)
	result = float(base_pair[0] if moving else base_pair[1])
	result = (result + float(additive or 0.0)) * max(
		0.0, float(multiplier or 0.0))
	if fired_recently:
		result *= clamp(shot_factor, 0.0, 1.0)
	result += clamp(foliage_bonus, 0.0, 0.60)
	return clamp(result, 0.0, 0.95)


def detection_distance(view_range, camouflage):
	"""Apply #1513's 50 metre floor and 445 metre spotting ceiling."""
	view_range = max(PROXIMITY_SPOT_DISTANCE, float(view_range or 0.0))
	camouflage = clamp(camouflage, 0.0, 0.95)
	distance = view_range - (
		view_range - PROXIMITY_SPOT_DISTANCE) * camouflage
	return clamp(distance, PROXIMITY_SPOT_DISTANCE, MAX_SPOT_DISTANCE)


def is_detected(distance, view_range, camouflage, has_line_of_sight=True):
	distance = max(0.0, float(distance or 0.0))
	if distance <= PROXIMITY_SPOT_DISTANCE:
		return True
	return bool(has_line_of_sight and
		distance <= detection_distance(view_range, camouflage))
