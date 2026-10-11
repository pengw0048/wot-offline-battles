# -*- coding: utf-8 -*-
"""Engine-free legacy spotting and camouflage calculations for #1513."""


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
# The trained Designated Target directive extends memory by four seconds.
MAX_SPOT_MEMORY_SECONDS = SPOT_MEMORY_SECONDS + 4.0
LAST_EFFORT_SECONDS = 2.0
MOVING_SPEED_EPSILON = 0.5
SHOT_CAMOUFLAGE_SECONDS = 0.75

# The client package does not carry the cell-app detection implementation.
# Preserve the existing cap instead of treating a published maximum as an
# exact #1513 calibration for this port's vegetation coverage approximation.
CAMOUFLAGE_LIMIT = 0.95
# Preserve the existing tuning until exact per-asset density and coverage are
# available. Published vegetation values vary by density; a single ray through
# an enclosing volume cannot justify assigning every asset the maximum value.
# These retained values are not claimed as #1513 server constants.
FOLIAGE_CAMOUFLAGE_PER_VOLUME = 0.15
FOLIAGE_CAMOUFLAGE_LIMIT = 0.60
# Soft cover this close to a vehicle is transparent to that vehicle, so its own
# bush conceals it without blinding it. Retain the existing full removal of
# nearby foliage after firing; the exact residual coefficient is unverified.
FOLIAGE_TRANSPARENCY_DISTANCE = 15.0

# Fallback heights before descriptor collision geometry is available.
# One owner for spotting geometry. The hidden worker decides
# spotting and the visible client samples the same pair for its own
# presentation, so a client ray that is even slightly more permissive draws an
# enemy the authority never spotted: no spotting credit, no Bot reaction, and
# no warning for the target. Vegetation queries share the selected checkpoints.
OBSERVER_EYE_HEIGHT = 2.0
TARGET_CHECK_HEIGHT = 1.5
# Retain the authority ray's existing allowance for a hit near the target
# endpoint. This tolerance does not identify the native surface that was hit.
SIGHT_END_TOLERANCE = 1.5


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
	"""#1513 ``utils.getInvisibility`` plus the shot and vegetation terms.

	``additive`` and ``multiplier`` are the aspect the caller resolved from
	``factors['invisibility']``: the camouflage net lives in the stationary
	aspect only. The exact ``VehicleParams.__getInvisibilityValues`` consumer
	multiplies the complete ``getClientInvisibility`` moving/still values by
	``invisibilityFactorAtShot``. Those values already include paint and the
	resolved device aspect, so all of them retain that shot factor here.
	"""
	if not isinstance(base_pair, (list, tuple)) or len(base_pair) < 2:
		base_pair = (0.0, 0.0)
	result = float(base_pair[0] if moving else base_pair[1])
	result = (result + float(additive or 0.0)) * max(
		0.0, float(multiplier or 0.0))
	if fired_recently:
		result *= clamp(shot_factor, 0.0, 1.0)
	result += clamp(foliage_bonus, 0.0, FOLIAGE_CAMOUFLAGE_LIMIT)
	return clamp(result, 0.0, CAMOUFLAGE_LIMIT)


def detection_distance(view_range, camouflage):
	"""Apply #1513's 50 metre floor and 445 metre spotting ceiling."""
	view_range = max(PROXIMITY_SPOT_DISTANCE, float(view_range or 0.0))
	camouflage = clamp(camouflage, 0.0, CAMOUFLAGE_LIMIT)
	distance = view_range - (
		view_range - PROXIMITY_SPOT_DISTANCE) * camouflage
	return clamp(distance, PROXIMITY_SPOT_DISTANCE, MAX_SPOT_DISTANCE)


def is_detected(distance, view_range, camouflage, has_line_of_sight=True):
	distance = max(0.0, float(distance or 0.0))
	if distance <= PROXIMITY_SPOT_DISTANCE:
		return True
	return bool(has_line_of_sight and
		distance <= detection_distance(view_range, camouflage))


def descriptor_check_points(descriptor):
    """Build #1513 cell-side checkpoints from the loaded client collision BSPs.

    VehicleDescriptor.__updateAttributes assigns visibilityCheckPoints only
    under IS_CELLAPP. Client descriptors therefore need the same construction
    after their hit testers have loaded; a fixed-height ray loses both the
    chassis observation port and exposed hull checkpoints.
    """
    from gui.mods.offline_lan_0922 import shot_geometry
    field = shot_geometry._field
    points = field(descriptor, 'visibilityCheckPoints', ()) or ()
    if len(points) >= 6:
        return tuple(points[:6])
    try:
        chassis = field(descriptor, 'chassis')
        hull = field(descriptor, 'hull')
        turret = field(descriptor, 'turret')
        hp = shot_geometry._box_point(field(chassis, 'hullPosition'))
        tp = shot_geometry._box_point(field(hull, 'turretPositions')[0])
        gp = shot_geometry._box_point(field(turret, 'gunPosition'))
        hull_box = field(field(hull, 'hitTester'), 'bbox')
        turret_box = field(field(turret, 'hitTester'), 'bbox')
        lo = shot_geometry._box_point(hull_box[0])
        hi = shot_geometry._box_point(hull_box[1])
        turret_top = shot_geometry._box_point(turret_box[1])[1]
        top = max(hi[1], tp[1] + turret_top)
        gun = tuple(tp[i] + gp[i] for i in range(3))
        center_y = (lo[1] + hi[1]) / 2.0
        center_z = (lo[2] + hi[2]) / 2.0
        local = (gun, (0.0, center_y, hi[2]),
                 (0.0, center_y, lo[2]),
                 (hi[0], gun[1], center_z),
                 (lo[0], gun[1], center_z))
        return ((0.0, hp[1] + top, 0.0),) + tuple(
            tuple(hp[i] + point[i] for i in range(3)) for point in local)
    except (AttributeError, TypeError, ValueError, IndexError, KeyError):
        return ()


def vehicle_check_points(descriptor, pose, observer=False, phase=0):
    """Project six checkpoints, alternating the chassis/turret observer ports."""
    from gui.mods.offline_lan_0922 import shot_geometry
    field = shot_geometry._field
    points = descriptor_check_points(descriptor)
    position = pose.get('position') or (
        pose.get('x', 0.0), pose.get('y', 0.0), pose.get('z', 0.0))
    if len(points) < 6:
        height = OBSERVER_EYE_HEIGHT if observer else TARGET_CHECK_HEIGHT
        return ((position[0], position[1] + height, position[2]),)
    indices = (int(phase) % 2,) if observer else range(6)
    result = []
    for index in indices:
        point = shot_geometry._box_point(points[index])
        if index == 1:
            chassis = field(descriptor, 'chassis', {})
            hull = field(descriptor, 'hull', {})
            try:
                hp = shot_geometry._box_point(field(chassis, 'hullPosition'))
                tp = shot_geometry._box_point(field(hull, 'turretPositions')[0])
                mount = tuple(hp[i] + tp[i] for i in range(3))
                yaw = float(pose.get('yaw', 0.0))
                turret_yaw = field(field(descriptor, 'gun', {}), 'staticTurretYaw')
                if turret_yaw is None:
                    turret_yaw = pose.get('turret_yaw', pose.get('aim_yaw', yaw) - yaw)
                relative = tuple(point[i] - mount[i] for i in range(3))
                relative = shot_geometry._rotate_y(relative, float(turret_yaw))
                point = tuple(mount[i] + relative[i] for i in range(3))
            except (TypeError, ValueError, IndexError, KeyError):
                pass
        result.append(shot_geometry.transform_vehicle_point(
            point, position, pose.get('yaw', 0.0),
            pose.get('pitch', 0.0), pose.get('roll', 0.0)))
    return tuple(result)


def radio_link(first_position, first_range, second_position, second_range):
    """Direct legacy radio contact: sum both ranges, never relay chains."""
    distance_squared = sum((float(first_position[i]) -
                            float(second_position[i])) ** 2 for i in range(3))
    first_range, second_range = float(first_range), float(second_range)
    reach = max(0.0, first_range) + max(0.0, second_range)
    return (first_range > 0.0 and second_range > 0.0 and
            distance_squared <= reach * reach)
