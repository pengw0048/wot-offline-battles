from __future__ import division

"""Engine-free tank-to-tank collision laws from the current 0.8.2 runtime.

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
and returns positional correction, the e=0 velocity impulse, ram events, and a
legacy-compatible event timestamp mapping. Movement is never vetoed: existing
spawn overlap is separated using inverse-mass weighting instead of becoming an
"all directions blocked" local-avoidance deadlock.
"""

import math


DEFAULT_SHAPE = (1.5, 3.5, -0.8, 2.0)
POSITION_SLOP = 0.01
# A native collision callback and the copied authority pose can straddle one
# presentation frame.  Keep contact receipts inside a bounded one-frame body
# envelope instead of requiring the callback point to match the copied OBB to
# the centimetre.
RAM_CONTACT_POINT_SLOP = 0.75
POSITION_PERCENT = 0.95
_SHAPE_CACHE = {}
SPATIAL_CELL_SIZE = 24.0

# 0.9.22-era Wargaming Battle Mechanics defines a ram as an HE-like
# explosion.  The page revision shipped alongside 9.22 is 270080:
# https://wiki.wargaming.net/en/index.php?oldid=270080
#
#   potential = 0.5 * combined mass in tonnes * relative speed squared
#   share     = 1 - individual mass / combined mass
#   damage    = HE damage factor * share * potential
#               - HE absorption factor * nominal armour * spall coefficient
#
# The 2018 Wiki formula uses 0.5 and 1.1. These are mechanics constants, not
# feel-tuning controls. The client stores vehicle mass in kilograms, hence the
# exact physics_shared WEIGHT_SCALE.
WEIGHT_SCALE = 0.001
RAM_KINETIC_FACTOR = 0.5
RAM_HE_DAMAGE_FACTOR = 0.5
RAM_ARMOR_ABSORPTION_FACTOR = 1.1
# Temporary product tuning while the exact #1513 ramming curve is audited.
# Keep every physical input and modifier intact; scale only the final HP loss.
RAM_DAMAGE_COEFFICIENT = 0.25
RAMMING_BONUS_MAX = 0.15


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


def _finite(value):
    return value == value and abs(value) != float('inf')


def _value(container, name, default=None):
    if isinstance(container, dict):
        return container.get(name, default)
    return getattr(container, name, default)


def _bbox(component):
    hit_tester = _value(component, 'hitTester')
    if hit_tester is None:
        raise RuntimeError('#1513 component hit tester is unavailable')
    bbox = getattr(hit_tester, 'bbox', None)
    if bbox is None:
        raise RuntimeError('#1513 component hit tester bbox is unavailable')
    return bbox


def chassis_shape(type_descriptor):
    """Return ``(half_width, half_length, lower_y, upper_y)``.

    The x/z body comes from the chassis hit tester. Retail ``physics_shared``
    extends its upper edge to contain the mounted hull, which is required for
    correct contacts between vehicles on different vertical levels.
    """
    if type_descriptor is None:
        raise RuntimeError('#1513 vehicle descriptor is unavailable')
    cache_key = id(type_descriptor)
    cached = _SHAPE_CACHE.get(cache_key)
    if cached is not None and cached[0] is type_descriptor:
        return cached[1]
    chassis = _value(type_descriptor, 'chassis')
    if chassis is None:
        raise RuntimeError('#1513 chassis descriptor is unavailable')
    try:
        chassis_box = _bbox(chassis)
        # #1513's HitTester.bbox carries a third derived value after min/max;
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
            raise RuntimeError('#1513 hull descriptor is unavailable')
        hull_box = _bbox(hull)
        hull_position = _value(chassis, 'hullPosition')
        if hull_position is None:
            raise RuntimeError('#1513 chassis hull position is unavailable')
        upper_y = max(
            upper_y,
            _coord(hull_position, 1) + _coord(hull_box[1], 1))
        shape = (half_width, half_length, lower_y, upper_y)
        # Retain the descriptor so CPython cannot reuse its id for another
        # vehicle descriptor while this long-running client is alive.
        _SHAPE_CACHE[cache_key] = (type_descriptor, shape)
        return shape
    except (AttributeError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError('#1513 vehicle collision descriptor is invalid: %s' %
                           error)


def pose_axes(yaw, pitch=0.0, roll=0.0):
    """Return BigWorld YPR local axes in world coordinates."""
    sy, cy = math.sin(float(yaw)), math.cos(float(yaw))
    sp, cp = math.sin(float(pitch)), math.cos(float(pitch))
    sr, cr = math.sin(float(roll)), math.cos(float(roll))

    def rotate(vector):
        x, y, z = vector
        y, z = cp * y - sp * z, sp * y + cp * z
        return (cy * x + sy * z, y, -sy * x + cy * z)

    return (rotate((cr, sr, 0.0)),
            rotate((-sr, cr, 0.0)),
            rotate((0.0, 0.0, 1.0)))


def body_contains_point(body, point, slop=POSITION_SLOP):
    """Return whether a world point lies in one frozen pitched hull body."""
    try:
        shape = body['shape']
        delta = (
            float(point[0]) - float(body['x']),
            float(point[1]) - float(body['y']),
            float(point[2]) - float(body['z']))
        axes = pose_axes(
            body['yaw'], body.get('pitch', 0.0), body.get('roll', 0.0))
        local = tuple(
            sum(axis[index] * delta[index] for index in range(3))
            for axis in axes)
        margin = max(0.0, float(slop))
        return bool(
            abs(local[0]) <= float(shape[0]) + margin and
            float(shape[2]) - margin <= local[1] <=
            float(shape[3]) + margin and
            abs(local[2]) <= float(shape[1]) + margin)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


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


def descriptor_spall_coefficient(type_descriptor):
    """Return ``miscAttrs.antifragmentationLiningFactor``, or 1.0 without one.

    1.0 is the descriptor's own no-liner value, so an absent field leaves the
    armour absorption term unchanged rather than inventing a reduction.
    """
    misc = _value(type_descriptor, 'miscAttrs', {}) or {}
    try:
        spall = float(_value(misc, 'antifragmentationLiningFactor', 1.0))
    except (TypeError, ValueError):
        return 1.0
    if spall < 1.0 or not _finite(spall):
        return 1.0
    return spall


def descriptor_ram_profile(type_descriptor, ramming_bonus=0.0):
    """Return source-backed non-contact ram inputs from one descriptor.

    Nominal armour is deliberately absent: ``hull.primaryArmor`` is only a
    front/side/rear summary and cannot stand in for retail's armour at the
    actual collision point. Callers must attach that per-contact scalar to the
    body as ``contact_armor``. ``miscAttrs.antifragmentationLiningFactor``
    starts at 1.0 and is multiplied by the mounted Spall Liner. Controlled
    Impact contributes 0.0015 per trained percentage point and is bounded by
    its documented 15 percent maximum.
    """
    misc = _value(type_descriptor, 'miscAttrs', {}) or {}
    try:
        spall = float(_value(
            misc, 'antifragmentationLiningFactor', 1.0))
    except (TypeError, ValueError):
        raise RuntimeError('#1513 Spall Liner factor is invalid')
    if spall < 1.0 or not _finite(spall):
        raise RuntimeError('#1513 Spall Liner factor is invalid')
    try:
        bonus = float(ramming_bonus)
    except (TypeError, ValueError):
        raise RuntimeError('#1513 Controlled Impact bonus is invalid')
    if not _finite(bonus):
        raise RuntimeError('#1513 Controlled Impact bonus is invalid')
    bonus = max(0.0, min(RAMMING_BONUS_MAX, bonus))
    return {
        'spall_coefficient': spall,
        'ramming_bonus': bonus,
    }


def _ram_profile(tank):
    profile = _tank_value(tank, 'ram_profile')
    if profile is None:
        descriptor = _tank_value(tank, 'descriptor')
        profile = (descriptor_ram_profile(descriptor)
                   if descriptor is not None else {})
    try:
        spall = float(profile.get('spall_coefficient', 1.0))
        bonus = float(profile.get('ramming_bonus', 0.0))
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError('tank ram profile is invalid')
    if (spall < 1.0 or not _finite(spall) or
            bonus < 0.0 or bonus > RAMMING_BONUS_MAX or
            not _finite(bonus)):
        raise RuntimeError('tank ram profile is invalid')
    return spall, bonus


def _contact_ram_inputs(tank, contact_armor=None):
    """Return per-contact armour plus descriptor/crew ram modifiers.

    A missing contact scalar fails closed. OBB orientation is insufficient to
    reconstruct retail's contact point, nominal armour group and spaced-armour
    handling, so it must never be silently replaced with primaryArmor.
    """
    armor = (contact_armor if contact_armor is not None else
             _tank_value(tank, 'contact_armor'))
    if armor is None:
        return None
    try:
        armor = float(armor)
    except (TypeError, ValueError):
        raise RuntimeError('tank contact armor is invalid')
    if armor < 0.0 or not _finite(armor):
        raise RuntimeError('tank contact armor is invalid')
    spall, bonus = _ram_profile(tank)
    return armor, spall, bonus


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


def obb_impact_contact(x_a, z_a, yaw_a, shape_a, velocity_a,
                       x_b, z_b, yaw_b, shape_b, velocity_b):
    """Return the first horizontal impact face for an overlapping OBB pair.

    ``obb_contact`` is the current minimum-translation axis. That is the right
    direction for separating interpenetrating bodies, but it can rotate from
    front/rear to side after one delayed step creates a deep overlap. Recover
    the entry face by sweeping the current intervals backward at their frozen
    relative velocity. The returned normal is the historical B -> A normal at
    first contact, not the current shortest escape direction.
    """
    axes_a = _axes(yaw_a)
    axes_b = _axes(yaw_b)
    delta_x = float(x_a) - float(x_b)
    delta_z = float(z_a) - float(z_b)
    relative_x = float(velocity_a[0]) - float(velocity_b[0])
    relative_z = float(velocity_a[1]) - float(velocity_b[1])
    if not all(_finite(value) for value in (
            delta_x, delta_z, relative_x, relative_z)):
        return None
    best_age = None
    best_contact = None

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
        radius = radius_a + radius_b
        signed_distance = delta_x * axis_x + delta_z * axis_z
        overlap = radius - abs(signed_distance)
        if overlap <= 0.0:
            return None
        axis_velocity = relative_x * axis_x + relative_z * axis_z
        if abs(axis_velocity) <= 1.0e-9:
            continue
        if axis_velocity > 0.0:
            entry_age = (radius + signed_distance) / axis_velocity
            normal_x, normal_z = -axis_x, -axis_z
        else:
            entry_age = (radius - signed_distance) / -axis_velocity
            normal_x, normal_z = axis_x, axis_z
        if entry_age < -1.0e-9:
            continue
        if best_age is None or entry_age < best_age - 1.0e-9:
            best_age = max(0.0, entry_age)
            best_contact = (normal_x, normal_z, overlap)
    if best_contact is None:
        return None
    # An unbounded rewind can otherwise trace a separating body through the
    # entire other hull and invent an entry on its opposite face. The impact
    # normal must still point from B toward A at the observed overlap; once
    # the centres have crossed that face plane, the entry side is ambiguous.
    if (best_contact[0] * delta_x +
            best_contact[1] * delta_z) <= 1.0e-9:
        return None
    return best_contact


def planar_closing_speed(velocity_a, velocity_b, normal):
    """Return the horizontal speed compressing an already-contacting pair.

    Tangential motion is a scrape, not impact energy.  Keeping this planar is
    deliberate: vertical landing/world impacts follow their own damage path
    and must not turn a side contact into a high-speed ram.
    """
    normal_x, normal_z = normal[0], normal[1]
    normal_velocity = (
        (velocity_a[0] - velocity_b[0]) * normal_x +
        (velocity_a[1] - velocity_b[1]) * normal_z)
    return max(0.0, -normal_velocity)


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


def _owner_oriented_contact(contact, center_dx, center_dz,
                            self_id, other_id):
    """Give an ambiguous SAT axis reciprocal owner directions.

    When both centres have the same projection on the minimum-overlap axis,
    SAT cannot infer which side is B -> A.  Letting both owner passes retain
    the axis' enumeration direction moves coincident tanks together instead
    of separating them.  Canonicalise the undirected axis, then use the stable
    pair identity to give the two owners opposite normals.
    """
    if contact is None:
        return None
    normal_x, normal_z, penetration = contact
    projection = center_dx * normal_x + center_dz * normal_z
    if abs(projection) > 1.0e-9:
        return contact
    if (normal_x < -1.0e-9 or
            (abs(normal_x) <= 1.0e-9 and normal_z < 0.0)):
        normal_x = -normal_x
        normal_z = -normal_z
    if self_id > other_id:
        normal_x = -normal_x
        normal_z = -normal_z
    return normal_x, normal_z, penetration


def ram_damage(relative_speed, mass_self, mass_other,
               armor_self, armor_other,
               spall_self=1.0, spall_other=1.0,
               bonus_self=0.0, bonus_other=0.0,
               moving_self=True, moving_other=True):
    """Return the documented 9.22 ``(damage_to_other, damage_to_self)``.

    Wargaming's 9.22-era Battle Mechanics first creates an HE-like explosion
    from the pair's kinetic potential, distributes it by inverse mass share,
    then applies the contemporaneous non-penetrating HE law at zero impact
    distance.  There is no empirical threshold, ratio clamp, or damage cap.
    """
    relative_speed = abs(float(relative_speed))
    self_tonnes = max(0.0, float(mass_self)) * WEIGHT_SCALE
    other_tonnes = max(0.0, float(mass_other)) * WEIGHT_SCALE
    combined = self_tonnes + other_tonnes
    if combined <= 0.0 or relative_speed <= 0.0:
        return 0, 0
    potential = (RAM_KINETIC_FACTOR * combined *
                 relative_speed * relative_speed)
    alpha_self = potential * (other_tonnes / combined)
    alpha_other = potential * (self_tonnes / combined)

    raw_self = max(
        0.0,
        RAM_HE_DAMAGE_FACTOR * alpha_self -
        RAM_ARMOR_ABSORPTION_FACTOR *
        max(0.0, float(armor_self)) * max(1.0, float(spall_self)))
    raw_other = max(
        0.0,
        RAM_HE_DAMAGE_FACTOR * alpha_other -
        RAM_ARMOR_ABSORPTION_FACTOR *
        max(0.0, float(armor_other)) * max(1.0, float(spall_other)))

    # Controlled Impact modifies final received/inflicted ram damage and is
    # active only while the corresponding vehicle is moving.
    own_bonus = max(0.0, min(RAMMING_BONUS_MAX, float(bonus_self)))
    other_bonus = max(0.0, min(RAMMING_BONUS_MAX, float(bonus_other)))
    if moving_self:
        raw_self *= 1.0 - own_bonus
        raw_other *= 1.0 + own_bonus
    if moving_other:
        raw_other *= 1.0 - other_bonus
        raw_self *= 1.0 + other_bonus
    return (int(raw_other * RAM_DAMAGE_COEFFICIENT),
            int(raw_self * RAM_DAMAGE_COEFFICIENT))


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


def _same_team(first, second):
    """Return whether two battle participants belong to one real team."""
    try:
        first_team = int(_tank_value(first, 'team'))
        second_team = int(_tank_value(second, 'team'))
    except (TypeError, ValueError, OverflowError):
        return False
    return first_team in (1, 2) and first_team == second_team


def resolve_tank(tank, others, now=None, ram_cooldowns=None,
                 active_ram_contacts=None, contact_armor_probe=None):
    """Resolve one hull against other hulls using only plain data.

    A body with ``alive`` false is a wreck: it still blocks and separates, but
    it never moves and never produces a ram event.  A body with ``impulse``
    false separates without transferring velocity, which leaves one owner for
    a contact that both sides resolve.

    The return value is a mapping with:

    ``correction``
        ``(dx, dz)`` Baumgarte separation for this tank.
    ``delta_velocity``
        ``(dvx, dvz)`` perfectly-inelastic (e=0) impulse for this tank.
    ``ram_events``
        One mapping per newly admitted ram damage event.
    ``cooldowns``
        A copied pair->last-event mapping retained for adapter compatibility.
        It is diagnostic only and never suppresses a separated new impact.
    ``contacts``
        The OBB pairs in a damaging compression episode. Feed the preceding
        complete frame back as ``active_ram_contacts`` so sustained pressure
        cannot replay one impact. Harmless touching does not consume a later
        real impact from the same overlap.

    Supplying ``now=None`` disables ram-event admission while retaining all
    collision correction and impulses.

    ``contact_armor_probe`` is an optional native boundary returning nominal
    structural armour for ``(tank, other)`` at this exact contact.  It is
    consulted only for a live, compressing pair whose plain bodies do not
    already carry their per-contact armour.
    """
    self_id = _tank_value(tank, 'id', -1)
    x = float(_tank_value(tank, 'x', 0.0) or 0.0)
    y = _tank_value(tank, 'y')
    z = float(_tank_value(tank, 'z', 0.0) or 0.0)
    yaw = float(_tank_value(tank, 'yaw', 0.0) or 0.0)
    mass_self = max(float(_tank_value(tank, 'mass', 1.0) or 1.0), 1.0)
    inverse_self = 1.0 / mass_self
    velocity_x = float(_tank_value(tank, 'vx', 0.0) or 0.0)
    velocity_y = float(_tank_value(tank, 'vy', 0.0) or 0.0)
    velocity_z = float(_tank_value(tank, 'vz', 0.0) or 0.0)
    own_shape = _tank_shape(tank)
    own_radius = math.sqrt(
        own_shape[0] * own_shape[0] + own_shape[1] * own_shape[1])

    correction_x = 0.0
    correction_z = 0.0
    delta_velocity_x = 0.0
    delta_velocity_z = 0.0
    ram_events = []
    ram_diagnostics = []
    cooldowns = dict(ram_cooldowns or {})
    previous_contacts = set(active_ram_contacts or ())
    overlap_pairs = set()
    newly_damaging_pairs = set()

    for other in others or ():
        other_id = _tank_value(other, 'id', -1)
        if other is None or other_id == self_id:
            continue
        other_is_wreck = not _tank_value(other, 'alive', True)
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
        inverse_other = 0.0 if other_is_wreck else 1.0 / mass_other
        other_yaw = float(_tank_value(other, 'yaw', 0.0) or 0.0)
        contact = obb_contact(
            x, z, yaw, own_shape,
            other_x, other_z, other_yaw, other_shape)
        if contact is None:
            continue
        contact = _owner_oriented_contact(
            contact, center_dx, center_dz, self_id, other_id)
        pair = (min(self_id, other_id), max(self_id, other_id))
        overlap_pairs.add(pair)

        if other_is_wreck:
            other_velocity_x = other_velocity_y = other_velocity_z = 0.0
        else:
            other_velocity_x = float(_tank_value(other, 'vx', 0.0) or 0.0)
            other_velocity_y = float(_tank_value(other, 'vy', 0.0) or 0.0)
            other_velocity_z = float(_tank_value(other, 'vz', 0.0) or 0.0)
        impact_contact = obb_impact_contact(
            x, z, yaw, own_shape, (velocity_x, velocity_z),
            other_x, other_z, other_yaw, other_shape,
            (other_velocity_x, other_velocity_z))
        response = pair_response(
            contact, inverse_self, inverse_other,
            (velocity_x, velocity_z),
            (other_velocity_x, other_velocity_z))
        correction_x += response[0]
        correction_z += response[1]
        # One owner per contact velocity.  When both sides cancel the same
        # closing velocity in the same frame, each re-solves against the
        # other's already-corrected pose and the pair oscillates; the body
        # that owns the pair keeps the impulse and the other only separates.
        if _tank_value(other, 'impulse', True):
            delta_velocity_x += response[2]
            delta_velocity_z += response[3]

        # Friendly hulls remain solid and receive the normal separation and
        # velocity response above, but only enemy contact can cause HP loss.
        if _same_team(tank, other):
            continue

        if impact_contact is None:
            continue
        closing_speed = planar_closing_speed(
            (velocity_x, velocity_z),
            (other_velocity_x, other_velocity_z), impact_contact)
        if closing_speed <= 0.0:
            continue

        if other_is_wreck or now is None:
            continue
        # The previous complete frame already owns this damaging episode.
        # ``overlap_pairs`` keeps it armed until physical separation, so no
        # armour ray or damage recomputation is needed while it persists.
        if pair in previous_contacts:
            continue
        own_ram_inputs = _contact_ram_inputs(tank)
        other_ram_inputs = _contact_ram_inputs(other)
        if ((own_ram_inputs is None or other_ram_inputs is None) and
                callable(contact_armor_probe)):
            probed = contact_armor_probe(tank, other, impact_contact)
            if probed is not None:
                if not isinstance(probed, (list, tuple)) or len(probed) != 2:
                    raise RuntimeError(
                        'tank contact armor probe result is invalid')
                if own_ram_inputs is None:
                    own_ram_inputs = _contact_ram_inputs(tank, probed[0])
                if other_ram_inputs is None:
                    other_ram_inputs = _contact_ram_inputs(other, probed[1])
        if own_ram_inputs is None or other_ram_inputs is None:
            ram_diagnostics.append({
                'pair': pair,
                'reason': 'contact_armor_unavailable',
                'missing_self': own_ram_inputs is None,
                'missing_other': other_ram_inputs is None,
            })
            continue
        armor_self, spall_self, bonus_self = own_ram_inputs
        armor_other, spall_other, bonus_other = other_ram_inputs
        relative_velocity_x = velocity_x - other_velocity_x
        relative_velocity_y = velocity_y - other_velocity_y
        relative_velocity_z = velocity_z - other_velocity_z
        # Preserve full relative speed for diagnostics, but only the contact
        # normal's closing component is impact energy.  A high-speed side
        # scrape or vertical motion cannot amplify a shallow hull contact.
        relative_speed = math.sqrt(
            relative_velocity_x * relative_velocity_x +
            relative_velocity_y * relative_velocity_y +
            relative_velocity_z * relative_velocity_z)
        damage_other, damage_self = ram_damage(
            closing_speed, mass_self, mass_other,
            armor_self, armor_other,
            spall_self, spall_other,
            bonus_self, bonus_other,
            bool(velocity_x or velocity_y or velocity_z),
            bool(other_velocity_x or other_velocity_y or other_velocity_z))
        if not damage_other and not damage_self:
            continue
        newly_damaging_pairs.add(pair)
        # A retail ram consumes the relative kinetic impulse at contact.  A
        # pair remains armed until the hulls separate, even if compression
        # briefly falls below the damage threshold. A harmless initial touch
        # is not an impact and must not suppress a later acceleration into the
        # other hull.
        cooldowns[pair] = float(now)
        ram_events.append({
            'pair': pair,
            'self_id': self_id,
            'other_id': other_id,
            'self_vehicle': str(
                _tank_value(tank, 'vehicle', '') or ''),
            'other_vehicle': str(
                _tank_value(other, 'vehicle', '') or ''),
            'mass_self': mass_self,
            'mass_other': mass_other,
            'velocity_self': (velocity_x, velocity_z),
            'velocity_other': (other_velocity_x, other_velocity_z),
            'velocity_y_self': velocity_y,
            'velocity_y_other': other_velocity_y,
            'yaw_self': yaw,
            'yaw_other': other_yaw,
            'shape_self': own_shape,
            'shape_other': other_shape,
            'contact_normal': (impact_contact[0], impact_contact[1]),
            'contact_penetration': impact_contact[2],
            'closing_speed': closing_speed,
            'relative_speed': relative_speed,
            'impact_speed': closing_speed,
            'armor_self': armor_self,
            'armor_other': armor_other,
            'spall_self': spall_self,
            'spall_other': spall_other,
            'ramming_bonus_self': bonus_self,
            'ramming_bonus_other': bonus_other,
            'damage_to_other': damage_other,
            'damage_to_self': damage_self,
        })

    return {
        'correction': (correction_x, correction_z),
        'delta_velocity': (delta_velocity_x, delta_velocity_z),
        'ram_events': tuple(ram_events),
        'ram_diagnostics': tuple(ram_diagnostics),
        'cooldowns': cooldowns,
        'contacts': frozenset(
            (previous_contacts & overlap_pairs) | newly_damaging_pairs),
    }


# Names mirror the current 0.8.2 helpers and keep adapter call sites explicit.
_tank_chassis_shape = chassis_shape
_tank_resolve = resolve_tank
