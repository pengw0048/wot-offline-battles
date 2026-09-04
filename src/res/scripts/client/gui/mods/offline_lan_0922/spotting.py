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
LAST_EFFORT_SECONDS = 2.0
MOVING_SPEED_EPSILON = 0.5
SHOT_CAMOUFLAGE_SECONDS = 0.75


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
