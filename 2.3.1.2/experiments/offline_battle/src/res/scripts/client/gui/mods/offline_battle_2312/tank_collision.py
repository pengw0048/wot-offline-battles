"""Vehicle-against-vehicle collision, taken from the 0.9.22 port.

The law is unchanged. The 2.3.1.2 inputs reach it through the
adapters in damage.py and the callers in this package.

Contract, from the original module:
Engine-free tank-to-tank collision laws from the current 0.8.2 runtime.

The live runtime owns BigWorld entities, descriptors, health, and clocks.  This
module deliberately owns none of them.  Callers convert each vehicle to a plain
mapping with these fields::

    {
        'id': 7,
        'x': 10.0, 'y': 2.0, 'z': -4.0, 'yaw': 0.0,
        'mass': 25000.0,
        'vx': 0.0, 'vz': 8.0,
        'shape': (half_width, half_length, lower_y, upper_y),
    }

The retail body is sized from the native chassis ``hitTester.bbox`` and extended
vertically to contain the mounted hull. ``resolve_tank`` uses yaw-aware OBB SAT
and returns positional correction, the e=0 velocity impulse, ram events, and an
updated cooldown mapping. Movement is never vetoed: existing spawn overlap is
separated using inverse-mass weighting instead of becoming an "all directions
blocked" local-avoidance deadlock.
"""
from __future__ import absolute_import
from __future__ import division

import math


DEFAULT_SHAPE = (1.5, 3.5, -0.8, 2.0)
POSITION_SLOP = 0.01
POSITION_PERCENT = 0.95
RAM_SAFE_SPEED = 3.5
RAM_COOLDOWN = 0.75
_SHAPE_CACHE = {}
SPATIAL_CELL_SIZE = 24.0


def build_spatial_index(bodies, cell_size=SPATIAL_CELL_SIZE):
    """Bucket body ids by x/z for local steering and collision broad phase."""
    size = max(1.0, float(cell_size))
    buckets = {}
    for body_id, body in (bodies or {}).items():
        try:
            position = body.get('position') if isinstance(body, dict) else body
            x = _coord(position, 0)
            z = _coord(position, 2)
            key = (int(math.floor(x / size)),
                   int(math.floor(z / size)))
            buckets.setdefault(key, []).append(body_id)
        except Exception:
            continue
    return size, buckets


def nearby_ids(index, x, z):
    """Return ids in the query cell and its eight neighbours."""
    if not index:
        return ()
    try:
        size, buckets = index
        cell_x = int(math.floor(float(x) / float(size)))
        cell_z = int(math.floor(float(z) / float(size)))
    except Exception:
        return ()
    result = []
    for offset_z in (-1, 0, 1):
        for offset_x in (-1, 0, 1):
            result.extend(buckets.get(
                (cell_x + offset_x, cell_z + offset_z), ()))
    return tuple(result)


def _coord(value, index, default=0.0):
    try:
        return float(value[index])
    except Exception:
        try:
            return float((value.x, value.y, value.z)[index])
        except Exception:
            return float(default)


def _value(container, name, default=None):
    if isinstance(container, dict):
        return container.get(name, default)
    return getattr(container, name, default)


def _bbox(component):
    hit_tester = _value(component, 'hitTester')
    if hit_tester is None:
        raise RuntimeError('2.3.1.2 component hit tester is unavailable')
    bbox = getattr(hit_tester, 'bbox', None)
    if bbox is None:
        raise RuntimeError('2.3.1.2 component hit tester bbox is unavailable')
    return bbox


def chassis_shape(type_descriptor):
    """Return ``(half_width, half_length, lower_y, upper_y)``.

    The x/z body comes from the chassis hit tester. Retail ``physics_shared``
    extends its upper edge to contain the mounted hull, which is required for
    correct contacts between vehicles on different vertical levels.
    """
    if type_descriptor is None:
        raise RuntimeError('2.3.1.2 vehicle descriptor is unavailable')
    cache_key = id(type_descriptor)
    cached = _SHAPE_CACHE.get(cache_key)
    if cached is not None and cached[0] is type_descriptor:
        return cached[1]
    chassis = _value(type_descriptor, 'chassis')
    if chassis is None:
        raise RuntimeError('2.3.1.2 chassis descriptor is unavailable')
    try:
        chassis_box = _bbox(chassis)
        # 2.3.1.2's HitTester.bbox carries a third derived value after min/max;
        # index it exactly as both retail physics_shared and current 0.8.2 do.
        minimum = chassis_box[0]
        maximum = chassis_box[1]
        half_width = max(
            abs(_coord(minimum, 0)), abs(_coord(maximum, 0)), 0.8)
        half_length = max(
            abs(_coord(minimum, 2)), abs(_coord(maximum, 2)), 1.0)
        lower_y = _coord(minimum, 1, DEFAULT_SHAPE[2])
        upper_y = _coord(maximum, 1, DEFAULT_SHAPE[3])

        hull = _value(type_descriptor, 'hull')
        if hull is None:
            raise RuntimeError('2.3.1.2 hull descriptor is unavailable')
        hull_box = _bbox(hull)
        hull_position = _value(chassis, 'hullPosition')
        if hull_position is None:
            raise RuntimeError('2.3.1.2 chassis hull position is unavailable')
        upper_y = max(
            upper_y,
            _coord(hull_position, 1) + _coord(hull_box[1], 1))
        shape = (half_width, half_length, lower_y, upper_y)
        # Retain the descriptor so CPython cannot reuse its id for another
        # vehicle descriptor while this long-running client is alive.
        _SHAPE_CACHE[cache_key] = (type_descriptor, shape)
        return shape
    except (AttributeError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError('2.3.1.2 vehicle collision descriptor is invalid: %s' %
                           error)


def forget_chassis_shape(type_descriptor):
    """Release one descriptor-derived shape at its BSP owner boundary."""
    cache_key = id(type_descriptor)
    cached = _SHAPE_CACHE.get(cache_key)
    if cached is None or cached[0] is not type_descriptor:
        return False
    del _SHAPE_CACHE[cache_key]
    return True


def vertical_overlap(y_a, shape_a, y_b, shape_b, slop=0.02):
    """Return whether the two descriptor-derived body intervals overlap."""
    if y_a is None or y_b is None:
        return True
    a_low = float(y_a) + shape_a[2]
    a_high = float(y_a) + shape_a[3]
    b_low = float(y_b) + shape_b[2]
    b_high = float(y_b) + shape_b[3]
    return min(a_high, b_high) - max(a_low, b_low) > slop


def support_rise_is_obstacle(body_y, support_y, maximum_climb, slop=0.02,
                             maximum_step=0.85):
    """Return whether a new centre support is a step, not drivable ground.

    A vertical support ray can hit the deck of a wagon, a low roof, or the top
    of a large prop after horizontal integration moved the hull partly inside
    it. Only rises this tick can physically climb may be used as ground; the
    hard cap keeps a slow frame from turning a vertical wall into a step.
    """
    if body_y is None or support_y is None:
        return False
    try:
        rise = float(support_y) - float(body_y)
        limit = min(max(0.0, float(maximum_climb)),
                    max(0.0, float(maximum_step)))
        limit += max(0.0, float(slop))
        return rise > limit
    except (TypeError, ValueError):
        return False


def _axes(yaw):
    # Local chassis x (right) and z (forward) in world x/z coordinates.
    sine = math.sin(yaw)
    cosine = math.cos(yaw)
    return ((cosine, -sine), (sine, cosine))


def obb_contact(x_a, z_a, yaw_a, shape_a,
                x_b, z_b, yaw_b, shape_b):
    """Return ``(nx, nz, penetration)``, with the normal pointing B -> A."""
    axes_a = _axes(yaw_a)
    axes_b = _axes(yaw_b)
    delta_x = x_a - x_b
    delta_z = z_a - z_b
    best_overlap = None
    best_x = 0.0
    best_z = 0.0

    for axis in (axes_a[0], axes_a[1], axes_b[0], axes_b[1]):
        axis_x, axis_z = axis
        radius_a = (
            shape_a[0] * abs(
                axis_x * axes_a[0][0] + axis_z * axes_a[0][1]) +
            shape_a[1] * abs(
                axis_x * axes_a[1][0] + axis_z * axes_a[1][1]))
        radius_b = (
            shape_b[0] * abs(
                axis_x * axes_b[0][0] + axis_z * axes_b[0][1]) +
            shape_b[1] * abs(
                axis_x * axes_b[1][0] + axis_z * axes_b[1][1]))
        signed_distance = delta_x * axis_x + delta_z * axis_z
        overlap = radius_a + radius_b - abs(signed_distance)
        if overlap <= 0.0:
            return None
        if best_overlap is None or overlap < best_overlap:
            if signed_distance < 0.0:
                axis_x = -axis_x
                axis_z = -axis_z
            best_overlap = overlap
            best_x = axis_x
            best_z = axis_z
    return best_x, best_z, best_overlap


def pair_response(contact, inverse_a, inverse_b, velocity_a, velocity_b,
                  slop=POSITION_SLOP, percent=POSITION_PERCENT):
    """Return inverse-mass corrections and e=0 impulses for both bodies."""
    zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    if contact is None:
        return zero
    inverse_sum = inverse_a + inverse_b
    if inverse_sum <= 0.0:
        return zero
    normal_x, normal_z, penetration = contact
    correction = (
        max(penetration - slop, 0.0) * percent / inverse_sum)
    correction_a_x = normal_x * correction * inverse_a
    correction_a_z = normal_z * correction * inverse_a
    correction_b_x = -normal_x * correction * inverse_b
    correction_b_z = -normal_z * correction * inverse_b

    delta_a_x = 0.0
    delta_a_z = 0.0
    delta_b_x = 0.0
    delta_b_z = 0.0
    relative_normal = (
        (velocity_a[0] - velocity_b[0]) * normal_x +
        (velocity_a[1] - velocity_b[1]) * normal_z)
    if relative_normal < 0.0:
        impulse = -relative_normal / inverse_sum
        delta_a_x = normal_x * impulse * inverse_a
        delta_a_z = normal_z * impulse * inverse_a
        delta_b_x = -normal_x * impulse * inverse_b
        delta_b_z = -normal_z * impulse * inverse_b
    return (correction_a_x, correction_a_z, delta_a_x, delta_a_z,
            correction_b_x, correction_b_z, delta_b_x, delta_b_z)


def ram_damage(closing_speed, mass_self, mass_other):
    """Return ``(damage_to_other, damage_to_self)`` for one ram event.

    This is the mature 0.8.2 mass-ratio law.  A heavier rammer deals more and
    receives less damage; impacts at or below 3.5 m/s are harmless.
    """
    relative_speed = abs(closing_speed)
    if relative_speed <= RAM_SAFE_SPEED:
        return 0, 0
    impulse = (relative_speed - RAM_SAFE_SPEED) ** 2
    ratio = max(0.1, min(4.0, mass_self / max(mass_other, 1.0)))
    damage_other = min(
        450, int(impulse * 1.7 * max(0.35, min(2.6, ratio))))
    damage_self = min(
        350, int(impulse * 1.0 * max(0.25, min(2.2, 1.0 / ratio))))
    return damage_other, damage_self


def _tank_value(tank, name, default=None):
    try:
        return tank.get(name, default)
    except AttributeError:
        return getattr(tank, name, default)


def _tank_shape(tank):
    shape = _tank_value(tank, 'shape')
    if shape is not None:
        try:
            return (float(shape[0]), float(shape[1]),
                    float(shape[2]), float(shape[3]))
        except (IndexError, TypeError, ValueError):
            pass
    descriptor = _tank_value(tank, 'descriptor')
    if descriptor is not None:
        return chassis_shape(descriptor)
    # Compatibility for snapshots/tests produced before the OBB port. New
    # adapters always supply the descriptor-derived four-component shape.
    dims = _tank_value(tank, 'dims')
    if dims is not None:
        try:
            return (float(dims[0]), max(float(dims[1]), float(dims[2])),
                    DEFAULT_SHAPE[2], DEFAULT_SHAPE[3])
        except (IndexError, TypeError, ValueError):
            pass
    return DEFAULT_SHAPE


def resolve_tank(tank, others, now=None, ram_cooldowns=None):
    """Resolve one hull against other living hulls using only plain data.

    The return value is a mapping with:

    ``correction``
        ``(dx, dz)`` Baumgarte separation for this tank.
    ``delta_velocity``
        ``(dvx, dvz)`` perfectly-inelastic (e=0) impulse for this tank.
    ``ram_events``
        One mapping per newly admitted ram damage event.
    ``cooldowns``
        A copied and updated pair->time mapping.  The input mapping is never
        mutated, so callers can publish the result atomically.

    Supplying ``now=None`` disables ram-event admission while retaining all
    collision correction and impulses.
    """
    self_id = _tank_value(tank, 'id', -1)
    x = float(_tank_value(tank, 'x', 0.0) or 0.0)
    y = _tank_value(tank, 'y')
    z = float(_tank_value(tank, 'z', 0.0) or 0.0)
    yaw = float(_tank_value(tank, 'yaw', 0.0) or 0.0)
    mass_self = max(float(_tank_value(tank, 'mass', 1.0) or 1.0), 1.0)
    inverse_self = 1.0 / mass_self
    velocity_x = float(_tank_value(tank, 'vx', 0.0) or 0.0)
    velocity_z = float(_tank_value(tank, 'vz', 0.0) or 0.0)
    own_shape = _tank_shape(tank)
    own_radius = math.sqrt(
        own_shape[0] * own_shape[0] + own_shape[1] * own_shape[1])

    correction_x = 0.0
    correction_z = 0.0
    delta_velocity_x = 0.0
    delta_velocity_z = 0.0
    ram_events = []
    cooldowns = dict(ram_cooldowns or {})

    for other in others or ():
        other_id = _tank_value(other, 'id', -1)
        if other is None or other_id == self_id:
            continue
        if not _tank_value(other, 'alive', True):
            continue
        other_x = float(_tank_value(other, 'x', 0.0) or 0.0)
        other_y = _tank_value(other, 'y')
        other_z = float(_tank_value(other, 'z', 0.0) or 0.0)
        other_shape = _tank_shape(other)
        if not vertical_overlap(y, own_shape, other_y, other_shape):
            continue
        center_dx = x - other_x
        center_dz = z - other_z
        other_radius = math.sqrt(
            other_shape[0] * other_shape[0] +
            other_shape[1] * other_shape[1])
        maximum_distance = own_radius + other_radius + 0.25
        if (center_dx * center_dx + center_dz * center_dz >
                maximum_distance * maximum_distance):
            continue

        mass_other = max(
            float(_tank_value(other, 'mass', 1.0) or 1.0), 1.0)
        inverse_other = 1.0 / mass_other
        other_yaw = float(_tank_value(other, 'yaw', 0.0) or 0.0)
        contact = obb_contact(
            x, z, yaw, own_shape,
            other_x, other_z, other_yaw, other_shape)
        if contact is None:
            continue

        other_velocity_x = float(_tank_value(other, 'vx', 0.0) or 0.0)
        other_velocity_z = float(_tank_value(other, 'vz', 0.0) or 0.0)
        response = pair_response(
            contact, inverse_self, inverse_other,
            (velocity_x, velocity_z),
            (other_velocity_x, other_velocity_z))
        correction_x += response[0]
        correction_z += response[1]
        delta_velocity_x += response[2]
        delta_velocity_z += response[3]

        normal_velocity = (
            (velocity_x - other_velocity_x) * contact[0] +
            (velocity_z - other_velocity_z) * contact[1])
        if normal_velocity >= 0.0:
            continue

        if now is None or normal_velocity >= -RAM_SAFE_SPEED:
            continue
        pair = (min(self_id, other_id), max(self_id, other_id))
        if float(now) - float(cooldowns.get(pair, 0.0)) <= RAM_COOLDOWN:
            continue
        cooldowns[pair] = float(now)
        closing_speed = -normal_velocity
        damage_other, damage_self = ram_damage(
            closing_speed, mass_self, mass_other)
        if damage_other or damage_self:
            ram_events.append({
                'pair': pair,
                'self_id': self_id,
                'other_id': other_id,
                'closing_speed': closing_speed,
                'damage_to_other': damage_other,
                'damage_to_self': damage_self,
            })

    return {
        'correction': (correction_x, correction_z),
        'delta_velocity': (delta_velocity_x, delta_velocity_z),
        'ram_events': tuple(ram_events),
        'cooldowns': cooldowns,
    }


# Names mirror the current 0.8.2 helpers and keep adapter call sites explicit.
_tank_chassis_shape = chassis_shape
_tank_resolve = resolve_tank
