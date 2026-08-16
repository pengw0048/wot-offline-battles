"""Spotting law, taken from the 0.9.22 port.

The law is unchanged. The 2.3.1.2 inputs reach it through the
adapters in damage.py and the callers in this package.
"""
from __future__ import absolute_import
PROXIMITY_SPOT_DISTANCE = 50.0
MAX_SPOT_DISTANCE = 500.0
SPOT_MEMORY_SECONDS = 5.0
MOVING_SPEED_EPSILON = 0.5
SHOT_CAMOUFLAGE_SECONDS = 0.75


def clamp(value, minimum, maximum):
    return max(float(minimum), min(float(maximum), float(value)))


def qualification_factor(role_level):
    """Normalize the legacy commander curve so a 100% commander is 1.0."""
    return (0.5 + 0.00375 * clamp(role_level, 0.0, 150.0)) / 0.875


def effective_view_range(base_range, commander_level=100.0,
        vision_factor=1.0):
    """Return uncapped view range; excess range still counters camouflage."""
    result = max(PROXIMITY_SPOT_DISTANCE, float(base_range or 0.0))
    result *= qualification_factor(commander_level)
    result *= max(0.0, float(vision_factor or 0.0))
    return max(PROXIMITY_SPOT_DISTANCE, result)


def crew_camouflage_factor(skill_level):
    """Scale the stored full-skill coefficient using the legacy 4/7 baseline."""
    return (4.0 / 7.0 + 3.0 / 7.0 *
        clamp(skill_level, 0.0, 100.0) / 100.0)


def base_camouflage(moving_base, still_base, crew_skill_level=0.0,
        invisibility_factor=1.0, paint_bonus=0.0):
    """Reproduce 2.3.1.2 VehicleDescr.computeBaseInvisibility composition."""
    factor = (crew_camouflage_factor(crew_skill_level) *
        max(0.0, float(invisibility_factor or 0.0)))
    bonus = max(0.0, float(paint_bonus or 0.0))
    return (max(0.0, float(moving_base or 0.0)) * factor + bonus,
        max(0.0, float(still_base or 0.0)) * factor + bonus)


def effective_camouflage(base_pair, moving=False, camouflage_net_bonus=0.0,
        camouflage_net_active=False, shot_factor=1.0,
        fired_recently=False, foliage_bonus=0.0):
    """Compose vehicle, optional-device, shot and pair-specific foliage camo."""
    if not isinstance(base_pair, (list, tuple)) or len(base_pair) < 2:
        base_pair = (0.0, 0.0)
    result = float(base_pair[0] if moving else base_pair[1])
    if camouflage_net_active and not moving:
        result += max(0.0, float(camouflage_net_bonus or 0.0))
    if fired_recently:
        result *= clamp(shot_factor, 0.0, 1.0)
    result += clamp(foliage_bonus, 0.0, 0.60)
    return clamp(result, 0.0, 0.95)


def detection_distance(view_range, camouflage):
    """Apply the historical 50 metre floor and 500 metre spotting ceiling."""
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
