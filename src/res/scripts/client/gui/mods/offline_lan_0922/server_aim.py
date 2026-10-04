"""Accepted native gun inputs and worker-computed #1513 shot geometry.

The trusted LAN client supplies its native, speed-limited gun angles and
dispersion. The worker computes the shot ray from those accepted inputs and
the installed descriptor; a marker is never a copy of the visible HUD ray.
"""

import math

from gui.mods.offline_lan_0922 import shot_geometry


try:
    INTEGER_TYPES = (int, long)
except NameError:
    INTEGER_TYPES = (int,)

CHECKPOINT_FIELDS = frozenset((
    'position', 'rotation', 'turret_yaw', 'gun_pitch', 'dispersion_angle'))
SAMPLE_FIELDS = frozenset((
    'input_seq', 'origin', 'direction', 'dispersion_angle', 'shot_speed'))


def _number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(
            value, INTEGER_TYPES + (float,)):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(value) or math.isinf(value) or not minimum <= value <= maximum:
        return None
    return value


def _vector(value, limits):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    result = [_number(item, low, high)
              for item, (low, high) in zip(value, limits)]
    return None if None in result else result


def canonical_checkpoint(value):
    """Validate native gun inputs without admitting a client-computed ray."""
    if not isinstance(value, dict) or set(value) != CHECKPOINT_FIELDS:
        return None
    position = _vector(value['position'], (
        (-5000.0, 5000.0), (-1000.0, 3000.0), (-5000.0, 5000.0)))
    rotation = _vector(value['rotation'], ((-math.pi, math.pi),) * 3)
    turret = _number(value['turret_yaw'], -math.pi, math.pi)
    pitch = _number(value['gun_pitch'], -math.pi, math.pi)
    dispersion = _number(value['dispersion_angle'], 0.0, 0.5)
    if position is None or rotation is None or None in (turret, pitch, dispersion):
        return None
    return {'position': position, 'rotation': rotation,
            'turret_yaw': turret, 'gun_pitch': pitch,
            'dispersion_angle': dispersion}


def canonical_sample(value):
    """Validate one worker result bound to its accepted player input."""
    if not isinstance(value, dict) or set(value) != SAMPLE_FIELDS:
        return None
    sequence = value['input_seq']
    if type(sequence) not in INTEGER_TYPES or not 1 <= sequence <= 2147483647:
        return None
    origin = _vector(value['origin'], (
        (-5000.0, 5000.0), (-1000.0, 3000.0), (-5000.0, 5000.0)))
    direction = _vector(value['direction'], ((-1.0, 1.0),) * 3)
    dispersion = _number(value['dispersion_angle'], 0.0, 0.5)
    speed = _number(value['shot_speed'], 0.001, 3000.0)
    if origin is None or direction is None or dispersion is None or speed is None:
        return None
    if abs(sum(item * item for item in direction) - 1.0) > 0.00001:
        return None
    return {'input_seq': sequence, 'origin': origin, 'direction': direction,
            'dispersion_angle': dispersion, 'shot_speed': speed}


def sample(descriptor, checkpoint, input_seq, shot_speed):
    """Compute the same ray for continuous feedback and the admitted trigger."""
    checkpoint = canonical_checkpoint(checkpoint)
    if checkpoint is None:
        raise ValueError('native gun aim checkpoint is invalid')
    yaw, pitch, roll = checkpoint['rotation']
    origin, direction = shot_geometry.shot_origin_and_direction(
        descriptor, checkpoint['position'], yaw, pitch, roll,
        checkpoint['turret_yaw'], checkpoint['gun_pitch'])
    # Opposing native rotations can yield 1 + one floating-point ULP on a
    # component of this unit vector. Normalize the computed geometry before
    # applying the strict wire bounds, as the launch path also does.
    length = math.sqrt(sum(value * value for value in direction))
    if length <= 0.0:
        raise ValueError('worker gun direction is empty')
    direction = tuple(value / length for value in direction)
    result = canonical_sample({
        'input_seq': input_seq, 'origin': origin, 'direction': direction,
        'dispersion_angle': checkpoint['dispersion_angle'],
        'shot_speed': shot_speed})
    if result is None:
        raise ValueError('worker gun marker is invalid')
    return result
