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
CONTACT_BROADPHASE_PADDING = 0.25
# A chassis-end straddle may only stop a descent, never lift the hull.
SUPPORT_STRADDLE_RISE = 0.12
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


def vertical_interval(y, shape, pitch=0.0, roll=0.0):
    """Project all eight body corners onto world Y before broad-phase culling."""
    cp, sp = math.cos(float(pitch)), math.sin(float(pitch))
    cr, sr = math.cos(float(roll)), math.sin(float(roll))
    up = cp * cr
    center = float(y) + (shape[2] + shape[3]) * 0.5 * up
    extent = (abs(cp * sr) * shape[0] + abs(sp) * shape[1] +
              abs(up) * (shape[3] - shape[2]) * 0.5)
    return center - extent, center + extent


def vertical_overlap(y_a, shape_a, y_b, shape_b, slop=0.02,
                     pitch_a=0.0, roll_a=0.0, pitch_b=0.0, roll_b=0.0):
    """Return whether the two oriented body intervals overlap."""
    if y_a is None or y_b is None:
        return True
    a_low, a_high = vertical_interval(y_a, shape_a, pitch_a, roll_a)
    b_low, b_high = vertical_interval(y_b, shape_b, pitch_b, roll_b)
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


def chassis_span_offsets(yaw, half_width, half_length):
    """Return the four chassis-end XZ offsets that can straddle a gap.

    The pairs are ordered ``(front, rear)`` then ``(right, left)`` so a caller
    can test each chassis axis independently.
    """
    sine = math.sin(float(yaw))
    cosine = math.cos(float(yaw))
    length = max(0.0, float(half_length))
    width = max(0.0, float(half_width))
    return (
        ((sine * length, cosine * length),
         (-sine * length, -cosine * length)),
        ((cosine * width, -sine * width),
         (-cosine * width, sine * width)),
    )


def straddled_support(body_y, follow_gap, axis_samples,
                      maximum_rise=SUPPORT_STRADDLE_RISE):
    """Return the support of a hull spanning a gap under its centre column.

    A tracked hull rests on its chassis ends, not on one ray at its centre.
    When the centre column drops out of the follow envelope but both ends of a
    chassis axis are still inside it, the hull is bridging a trench, crater or
    slot narrower than itself and must not descend into it.  One supported end
    is a cliff edge, not a bridge, so it keeps the existing fall.

    ``axis_samples`` is the sequence returned for
    :func:`chassis_span_offsets`, each entry a ``(first, second)`` pair of
    sampled ground heights or ``None``.  This law may only stop a descent, so
    an end higher than ``maximum_rise`` above the body is not straddle
    evidence: climbing a step stays the centre column's own gated decision and
    must not become an unreviewable lift from a side ray on a wall top.
    """
    if body_y is None:
        return None
    try:
        body_y = float(body_y)
        gap = max(0.0, float(follow_gap))
        rise = max(0.0, float(maximum_rise))
    except (TypeError, ValueError):
        return None
    support = None
    for pair in axis_samples:
        heights = []
        for value in pair:
            if value is None:
                break
            try:
                height = float(value)
            except (TypeError, ValueError):
                break
            if not -gap <= height - body_y <= rise:
                break
            heights.append(height)
        if len(heights) != 2:
            continue
        candidate = max(heights)
        if support is None or candidate > support:
            support = candidate
    return support


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


def ram_hull_vertical_interval(descriptor, y, pitch=0.0, roll=0.0):
    """Bound the mounted hull, rather than the track-to-roof body envelope.

    These bounds only select rays. Each accepted plate still requires a
    structural material from the exact native hit tester on that same ray.
    """
    hull = _value(descriptor, 'hull')
    chassis = _value(descriptor, 'chassis')
    bounds = _bbox(hull)
    origin = _value(chassis, 'hullPosition')
    if origin is None:
        raise RuntimeError('#1513 chassis hull position is unavailable')
    sp, cp = math.sin(pitch), math.cos(pitch)
    sr, cr = math.sin(roll), math.cos(roll)
    heights = []
    for ix in (0, 1):
        for iy in (0, 1):
            for iz in (0, 1):
                x = _coord(bounds[ix], 0) + _coord(origin, 0)
                local_y = _coord(bounds[iy], 1) + _coord(origin, 1)
                z = _coord(bounds[iz], 2) + _coord(origin, 2)
                heights.append(float(y) + cp * (sr*x + cr*local_y) - sp*z)
    return min(heights), max(heights)


def ram_contact_sample_heights(hit_y, span):
    """Return stable structural-probe heights inside one real contact span.

    #1513 ramming damage is applied at a point inside the total contact area.
    A synthetic OBB overlap has no native Y coordinate, so its single chassis
    midpoint can land on track-only or empty material.  Keep the observed Y
    first, then test nearby interior heights; callers still require both real
    native hit testers to return structural armour at the same height.
    """
    try:
        hit_y = float(hit_y)
    except (TypeError, ValueError, OverflowError):
        return ()
    if not _finite(hit_y):
        return ()
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return (hit_y,)
    try:
        low, high = float(span[0]), float(span[1])
    except (TypeError, ValueError, OverflowError):
        return (hit_y,)
    if not (_finite(low) and _finite(high)) or high <= low:
        return (hit_y,)
    observed = max(low, min(high, hit_y))
    raw = [observed]
    for fraction in (0.5, 1.0 / 3.0, 2.0 / 3.0, 0.2, 0.8):
        raw.append(low + (high - low) * fraction)
    raw[1:] = sorted(raw[1:], key=lambda value: abs(value - observed))
    result = []
    for value in raw:
        if not result or all(abs(value - old) > 1.0e-4 for old in result):
            result.append(value)
    return tuple(result)


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
    result = _obb_overlap(x_a, z_a, yaw_a, shape_a,
                          x_b, z_b, yaw_b, shape_b)
    return result if result[2] > 0.0 else None


def _obb_overlap(x_a, z_a, yaw_a, shape_a,
                 x_b, z_b, yaw_b, shape_b):
    """Signed SAT depth, including separation for conservative arc pruning."""
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
        if best_overlap is None or overlap < best_overlap:
            if signed_distance < 0.0:
                axis_x = -axis_x
                axis_z = -axis_z
            best_overlap = overlap
            best_x = axis_x
            best_z = axis_z
    return best_x, best_z, best_overlap


def rotation_fraction(position, yaw, candidate_yaw, shape, others,
                      pivot_offset=0.0, translation=(0.0, 0.0)):
    """Project kinematic traverse onto the first legal chassis contact.

    Powered traverse uses zero translation. A passive wreck can supply its
    simultaneous centre travel so the complete rigid pose is constrained.
    Traverse cannot bypass the mass/track-force response by turning a corner
    into the other hull and asking positional separation to move it for free.
    Existing overlap may decrease; neither player nor Bot identity changes
    this geometry rule.
    Sample the whole swept angle at less than half the existing penetration
    slop per corner, then refine the first blocked interval. End poses alone
    miss a hull swept through during a late callback.
    """
    delta = (candidate_yaw-yaw+math.pi) % (2.0*math.pi)-math.pi
    radius = math.hypot(shape[0], shape[1])
    linear_travel = math.hypot(*translation)
    travel = abs(delta)*(radius+abs(pivot_offset)) + linear_travel
    if travel <= 1e-9:
        return 1.0
    samples = max(1, int(math.ceil(travel/(POSITION_SLOP*0.5))))
    fraction = 1.0
    for other in others:
        where = other.get('position')
        if where is None:
            where = (other['x'], other.get('y', 0.0), other['z'])
        other_shape = _tank_shape(other)
        reach = (radius+math.hypot(other_shape[0], other_shape[1]) +
                 abs(pivot_offset)*abs(delta) + linear_travel)
        if ((position[0]-where[0])**2+(position[2]-where[2])**2 > reach*reach or
                not vertical_overlap(position[1], shape, where[1], other_shape)):
            continue
        other_yaw = other.get('yaw', 0.0)
        def depth(at):
            angle = yaw+delta*at
            px = position[0]+pivot_offset*(math.cos(yaw)-math.cos(angle))+translation[0]*at
            pz = position[2]+pivot_offset*(math.sin(angle)-math.sin(yaw))+translation[1]*at
            hit = _obb_overlap(px, pz, angle, shape,
                              where[0], where[2], other_yaw, other_shape)
            return hit[2]
        allowed = max(POSITION_SLOP, depth(0.0))
        # Each SAT projection changes by at most this distance per radian.
        # Reject whole clear intervals using that bound, preserving every
        # sample and the first-contact refinement of the former dense scan.
        # Planning a large recovery turn beside a hull no longer performs
        # hundreds of redundant trigonometric/SAT queries each callback.
        lipschitz = (reach + abs(pivot_offset) +
                     math.hypot(position[0]-where[0], position[2]-where[2]))
        def first_blocked(lo, hi, dl, dh):
            span = (hi-lo)*fraction/float(samples)
            if max(dl, dh)+(lipschitz*abs(delta)+linear_travel)*span*.5 <= allowed+1e-9:
                return None
            if hi-lo == 1:
                return (lo, hi) if dh > allowed+1e-9 else None
            mid = (lo+hi)//2
            dm = depth(mid*fraction/float(samples))
            return (first_blocked(lo, mid, dl, dm) or
                    first_blocked(mid, hi, dm, dh))
        blocked = first_blocked(0, samples, depth(0.0), depth(fraction))
        if blocked is not None:
            low, high = (i*fraction/float(samples) for i in blocked)
            while (high-low)*travel > 1e-6:
                middle = (low+high)*.5
                if depth(middle) > allowed+1e-9:
                    high = middle
                else:
                    low = middle
            fraction = low
    return fraction


def post_contact_velocity_bodies(tanks, results):
    """Return frozen bodies after the already-solved normal contact impulse.

    Traverse torque is a second constraint in the same physics slice.  Feeding
    it pre-contact velocities makes the same closing speed available twice and
    lets a small steering twitch add a second collision impulse.  Only velocity
    is carried forward here; geometric separation remains owned by the normal
    solver and its world-collision gate.
    """
    updated = []
    results = results or {}
    for tank in tanks or ():
        body = dict(tank)
        result = results.get(body.get('id'), {}) or {}
        delta = result.get('delta_velocity', (0.0, 0.0))
        try:
            body['vx'] = float(body.get('vx', 0.0)) + float(delta[0])
            body['vz'] = float(body.get('vz', 0.0)) + float(delta[1])
            body['push_yaw'] = body.get('push_yaw', 0.0) + result.get('delta_yaw', 0.0)
        except (TypeError, ValueError, IndexError, OverflowError):
            raise RuntimeError('invalid solved contact velocity')
        updated.append(body)
    return updated


def traverse_contact_point(a, b, normal, omega):
    """Choose the closing endpoint of the actual common footprint.

    A longer hull's extreme corner may be metres beyond its shorter peer.
    Apply force only in the clipped contact patch, including a slop-wide
    touching skin, never at that fictitious distant corner. The pair normal
    still comes from SAT and the real shapes are never enlarged for motion.
    """
    aa, bb = dict(a, shape=_tank_shape(a)), dict(b, shape=_tank_shape(b))
    polygon = obb_overlap_polygon(aa, bb)
    if not polygon:
        shape = bb['shape']
        bb['shape'] = (shape[0]+POSITION_SLOP, shape[1]+POSITION_SLOP,
                       shape[2], shape[3])
        polygon = obb_overlap_polygon(aa, bb)
    if not polygon:
        return None
    nx, nz = normal
    return min(polygon, key=lambda point:
               ((point[1]-a['z'])*nx-(point[0]-a['x'])*nz)*omega)


def traverse_impulses(tanks, dt, anchor=None, angular_results=None):
    """Spend track torque at an occupied corner instead of a free yaw shove.

    The chassis remains a constrained planar box, not the retail cell body.
    The descriptor's traction-limited engine torque bounds the contact
    impulse. Actual yaw still sweeps the free space: a held peer
    permits no corner penetration; a movable peer opens space under force.
    Ground reactions use the same track budget as translational contacts.
    Callers transport the reciprocal linear momentum through the usual ledger.
    """
    bodies = sorted(tanks, key=lambda b: b['id'])
    result = dict((b['id'], (0.0, 0.0)) for b in bodies)
    angular = dict((b['id'], 0.0) for b in bodies)
    if dt <= 0.0:
        return result
    for a in bodies:
        omega = a.get('traverse_speed', 0.0)
        budget = a.get('traverse_torque', 0.0)*dt
        if not omega or budget <= 0.0 or not a.get('alive', True):
            continue
        shape = _tank_shape(a)
        # ``traverse_speed`` is a motor target, not stored angular momentum.
        # A held corner converts the track couple into force at its lever
        # arm. Spending the torque on a fictitious free angular acceleration
        # first, then dividing by angular inertia again at the contact, made
        # ordinary heavy/light side hugs immovable despite sufficient torque.
        # The budget limits force; the target speed separately limits travel.
        axes = _axes(a['yaw'])
        for b in bodies:
            if (a['id'] == b['id'] or not a.get('impulse', True) or
                    not b.get('impulse', True) or budget <= 0.0 or
                    (anchor is not None and anchor not in (a['id'], b['id']))):
                continue
            other_shape = _tank_shape(b)
            reach = math.hypot(*shape[:2])+math.hypot(*other_shape[:2])+POSITION_SLOP
            if ((a['x']-b['x'])**2+(a['z']-b['z'])**2 > reach*reach or
                    not vertical_overlap(
                        a.get('y'), shape, b.get('y'), other_shape,
                        pitch_a=a.get('pitch', 0.0), roll_a=a.get('roll', 0.0),
                        pitch_b=b.get('pitch', 0.0), roll_b=b.get('roll', 0.0))):
                continue
            nx, nz, depth = _obb_overlap(a['x'], a['z'], a['yaw'], shape,
                                         b['x'], b['z'], b['yaw'], other_shape)
            if depth < -POSITION_SLOP:
                continue
            point = traverse_contact_point(a, b, (nx, nz), omega)
            if point is None:
                continue
            corner_x, corner_z = point[0]-a['x'], point[1]-a['z']
            arm = corner_z*nx-corner_x*nz
            if arm*omega >= -1e-9:
                continue
            ia = 0.0 if a.get('immovable') else 1.0/a['mass']
            ib = 0.0 if b.get('immovable') else 1.0/b['mass']
            if ia + ib <= 0.0:
                continue
            avx, avz = result[a['id']]
            bvx, bvz = result[b['id']]
            relative = ((a.get('vx', 0.0)+avx-b.get('vx', 0.0)-bvx)*nx +
                        (a.get('vz', 0.0)+avz-b.get('vz', 0.0)-bvz)*nz)
            inertia = wreck_yaw_inertia(b)
            inverse_i = 1.0/inertia if inertia else 0.0
            peer_arm = ((a['z']+corner_z-b['z'])*nx -
                        (a['x']+corner_x-b['x'])*nz)
            relative -= (b.get('push_yaw', 0.0)+angular[b['id']])*peer_arm
            closing = max(0.0, -arm*omega-relative)
            impulse = min(budget/abs(arm), closing/(ia+ib+peer_arm**2*inverse_i))
            budget -= impulse*abs(arm)
            angular[b['id']] -= impulse*peer_arm*inverse_i
            for body, inverse, sign in ((a, ia, 1.0), (b, ib, -1.0)):
                grip = body.get('contact_decel')
                yaw = body['yaw']
                vx, vz = result[body['id']]
                normal_speed = ((body.get('vx', 0.0)+vx)*nx+
                                (body.get('vz', 0.0)+vz)*nz)
                forward = abs(nx*math.sin(yaw)+nz*math.cos(yaw))
                side = abs(nx*math.cos(yaw)-nz*math.sin(yaw))
                held = (grip is not None and abs(normal_speed) <= 1e-9 and
                        impulse*inverse*forward <= grip[0]*dt and
                        impulse*inverse*side <= grip[1]*dt)
                if not held:
                    result[body['id']] = (vx+nx*sign*impulse*inverse,
                                           vz+nz*sign*impulse*inverse)
    if angular_results is not None:
        for actor, delta in angular.items():
            angular_results[actor]['delta_yaw'] = (
                angular_results[actor].get('delta_yaw', 0.0)+delta)
    return result


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
                  slop=POSITION_SLOP, percent=POSITION_PERCENT,
                  friction_inverse=None):
    """Return inverse-mass corrections and e=0 impulses for both bodies.

    Exact #1513 ``physics_shared.updateCommonConf`` publishes the retail
    solver's hull-to-hull Coulomb coefficient as
    ``wg_setupPhysicsParam('CONTACT_FRICTION_VEHICLES', 0.3)``, so a real
    contact also carries a tangential impulse limited by that normal load.
    ``USE_PSEUDO_CONTACTS`` True and
    ``CONTACT_PENETRATION`` 0.1 from the same function are the
    positional-correction class implemented above. That Python function does
    not configure ``RESTITUTION``; this does not establish the native default
    or retail restitution. The normal term preserves this project's existing
    e=0 response.
    """
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
        tangent_x, tangent_z = -normal_z, normal_x
        tangent_a, tangent_b = friction_inverse or (inverse_a, inverse_b)
        tangent_sum = tangent_a + tangent_b
        relative_tangent = ((velocity_a[0] - velocity_b[0]) * tangent_x +
                            (velocity_a[1] - velocity_b[1]) * tangent_z)
        if abs(relative_tangent) <= 1.0e-9:
            relative_tangent = 0.0
        tangent_impulse = max(-0.3 * impulse, min(
            0.3 * impulse, -relative_tangent / tangent_sum)) if tangent_sum else 0.0
        delta_a_x += tangent_x * tangent_impulse * tangent_a
        delta_a_z += tangent_z * tangent_impulse * tangent_a
        delta_b_x -= tangent_x * tangent_impulse * tangent_b
        delta_b_z -= tangent_z * tangent_impulse * tangent_b
    return (correction_a_x, correction_a_z, delta_a_x, delta_a_z,
            correction_b_x, correction_b_z, delta_b_x, delta_b_z)


def grounded_inverse_masses(contact, first, second, inverse_a, inverse_b, dt):
    """A stationary track can hold an impulse before position recovery.

    Test the impulse required to stop the moving neighbour against the same
    descriptor-derived track budget the motion integrators use. A held hull
    has zero mobility for this contact only; it is never made infinite-mass
    because of its player/Bot identity. Once the load exceeds that budget,
    both real inverse masses participate. Engine power enters through the
    incoming speed produced by longitudinal_step over this very slice.
    """
    if dt <= 0.0 or not inverse_a or not inverse_b:
        return inverse_a, inverse_b
    nx, nz = contact[:2]
    va = first.get('vx', 0.0)*nx + first.get('vz', 0.0)*nz
    vb = second.get('vx', 0.0)*nx + second.get('vz', 0.0)*nz
    if va >= vb:
        return inverse_a, inverse_b

    def holds(body, speed, inverse, moving_inverse):
        grip = body.get('contact_decel')
        if grip is None or abs(speed) > 1.0e-9:
            return False
        yaw = body.get('yaw', 0.0)
        impulse_speed = (vb-va) * inverse/moving_inverse
        forward = abs(nx*math.sin(yaw) + nz*math.cos(yaw))
        side = abs(nx*math.cos(yaw) - nz*math.sin(yaw))
        return (impulse_speed*forward <= grip[0]*dt and
                impulse_speed*side <= grip[1]*dt)

    if holds(first, va, inverse_a, inverse_b):
        return 0.0, inverse_b
    if holds(second, vb, inverse_b, inverse_a):
        return inverse_a, 0.0
    return inverse_a, inverse_b


def translation_fraction(body, movement, others):
    """Sweep one translated OBB, retaining only the existing contact slop.

    An endpoint test can miss an entire intervening hull. Intersect the four
    SAT time intervals instead. Existing overlap may escape or slide, but
    cannot deepen or pass through the neighbour's centre plane.
    """
    mx, mz = movement
    if abs(mx) + abs(mz) <= 1.0e-12:
        return 1.0
    shape = _tank_shape(body)
    axes = _axes(body['yaw'])
    body_radius = math.hypot(*shape[:2])
    fraction = 1.0
    for other in others:
        if body['id'] == other['id']:
            continue
        peer_shape = _tank_shape(other)
        reach = body_radius + math.hypot(*peer_shape[:2])
        if (other['x'] < body['x']+min(0.0, mx)-reach or
                other['x'] > body['x']+max(0.0, mx)+reach or
                other['z'] < body['z']+min(0.0, mz)-reach or
                other['z'] > body['z']+max(0.0, mz)+reach):
            continue
        if not vertical_overlap(
                body.get('y'), shape, other.get('y'), peer_shape,
                pitch_a=body.get('pitch', 0.0), roll_a=body.get('roll', 0.0),
                pitch_b=other.get('pitch', 0.0), roll_b=other.get('roll', 0.0)):
            continue
        dx, dz = body['x']-other['x'], body['z']-other['z']
        contact = _obb_overlap(body['x'], body['z'], body['yaw'], shape,
                               other['x'], other['z'], other['yaw'], peer_shape)
        contact = _owner_oriented_contact(contact, dx, dz, body['id'], other['id'])
        if contact[2] >= POSITION_SLOP - 1.0e-9:
            if mx*contact[0] + mz*contact[1] < -1.0e-9:
                fraction = 0.0
            continue
        peer_axes = _axes(other['yaw'])
        entry, leave = 0.0, 1.0
        for nx, nz in axes + peer_axes:
            axis_radius = sum(s[i]*abs(nx*a[i][0]+nz*a[i][1])
                         for s, a in ((shape, axes), (peer_shape, peer_axes))
                         for i in (0, 1)) - POSITION_SLOP
            offset, travel = dx*nx + dz*nz, mx*nx + mz*nz
            if abs(travel) <= 1.0e-12:
                if abs(offset) >= axis_radius:
                    entry = 2.0
                    break
                continue
            first, last = sorted(((-axis_radius-offset)/travel,
                                  (axis_radius-offset)/travel))
            entry, leave = max(entry, first), min(leave, last)
            if entry > leave:
                break
        if entry <= leave and leave >= 0.0:
            fraction = min(fraction, max(0.0, entry))
    return fraction


def slide_translation(body, movement, others, first_fraction=None):
    """Retain tangential travel when another owned hull blocks the normal.

    Replica positions stay solid until their owner moves them. Truncating
    the entire vector at first contact also cancels the unconstrained tangent
    and wedges oblique pushes. Project only the entering remainder, re-sweep
    every projected segment, and leave momentum to the reciprocal solver.
    The caller may reuse a first fraction computed for the same initial body,
    movement and roster in this operation; later segments always sweep again.
    """
    current = dict(body)
    remaining = tuple(movement)
    total = [0.0, 0.0]
    for segment in range(4):
        fraction = (first_fraction if segment == 0 and first_fraction is not None
                    else translation_fraction(current, remaining, others))
        accepted = (remaining[0]*fraction, remaining[1]*fraction)
        for i, key in enumerate(('x', 'z')):
            current[key] += accepted[i]
            total[i] += accepted[i]
        if fraction >= 1.0:
            break
        remaining = (remaining[0]*(1.0-fraction), remaining[1]*(1.0-fraction))
        changed = False
        for other in others:
            if current['id'] == other['id'] or not vertical_overlap(
                    current.get('y'), _tank_shape(current), other.get('y'),
                    _tank_shape(other), pitch_a=current.get('pitch', 0.0),
                    roll_a=current.get('roll', 0.0), pitch_b=other.get('pitch', 0.0),
                    roll_b=other.get('roll', 0.0)):
                continue
            contact = _obb_overlap(current['x'], current['z'], current['yaw'],
                _tank_shape(current), other['x'], other['z'], other['yaw'], _tank_shape(other))
            contact = _owner_oriented_contact(contact, current['x']-other['x'],
                current['z']-other['z'], current['id'], other['id'])
            if contact[2] < POSITION_SLOP-1.0e-7:
                continue
            entering = remaining[0]*contact[0]+remaining[1]*contact[1]
            if entering < -1.0e-9:
                remaining = (remaining[0]-entering*contact[0],
                             remaining[1]-entering*contact[1])
                changed = True
        if not changed or math.hypot(*remaining) <= 1.0e-9:
            break
    return tuple(total)


def obb_vertices(body):
    shape = body['shape']
    yaw = float(body['yaw'])
    sine = math.sin(yaw)
    cosine = math.cos(yaw)
    right_x, right_z = cosine, -sine
    forward_x, forward_z = sine, cosine
    center_x, center_z = float(body['x']), float(body['z'])
    half_width, half_length = float(shape[0]), float(shape[1])
    return [
        (center_x + sx * half_width * right_x +
         sz * half_length * forward_x,
         center_z + sx * half_width * right_z +
         sz * half_length * forward_z)
        for sx, sz in ((-1.0, -1.0), (1.0, -1.0),
                       (1.0, 1.0), (-1.0, 1.0))]

def obb_overlap_polygon(body_a, body_b):
    """Clip the mounted chassis footprints to their shared contact area."""
    polygon = obb_vertices(body_a)
    clip = obb_vertices(body_b)
    for index in range(4):
        start = clip[index]
        end = clip[(index + 1) % 4]
        edge_x, edge_z = end[0] - start[0], end[1] - start[1]

        def inside(point):
            return (edge_x * (point[1] - start[1]) -
                    edge_z * (point[0] - start[0])) >= -1.0e-7

        def intersection(first, second):
            segment_x = second[0] - first[0]
            segment_z = second[1] - first[1]
            denominator = segment_x * edge_z - segment_z * edge_x
            if abs(denominator) <= 1.0e-12:
                return second
            ratio = ((start[0] - first[0]) * edge_z -
                     (start[1] - first[1]) * edge_x) / denominator
            return (first[0] + ratio * segment_x,
                    first[1] + ratio * segment_z)

        output = []
        if not polygon:
            return None
        previous = polygon[-1]
        previous_inside = inside(previous)
        for current in polygon:
            current_inside = inside(current)
            if current_inside != previous_inside:
                output.append(intersection(previous, current))
            if current_inside:
                output.append(current)
            previous = current
            previous_inside = current_inside
        polygon = output
    return polygon

def obb_overlap_point(body_a, body_b):
    """Return a point inside the exact convex overlap of two OBBs."""
    polygon = obb_overlap_polygon(body_a, body_b)
    if not polygon:
        return None
    count = float(len(polygon))
    return (sum(point[0] for point in polygon) / count,
            sum(point[1] for point in polygon) / count)


def wreck_yaw_inertia(body):
    """Uniform footprint inertia for the existing copied rigid-hull trial.

    Use the original installed mass and mounted chassis footprint, never a
    dead-body weight multiplier. This is a planar trial approximation, not a
    recovered retail inertia tensor. Powered tracks retain their motor owner.
    """
    if body.get('alive', True) or body.get('immovable'):
        return 0.0
    shape = body.get('collision_shape') or _tank_shape(body)
    return float(body['mass']) * (shape[0]**2 + shape[1]**2) / 3.0


def _angular_pair_response(a, b, hit, ia, ib):
    """Coupled linear/yaw e=0 impulse at the shared footprint contact.

    Include rotational effective mass before choosing the impulse. Adding a
    torque after a centre-of-mass solve would manufacture kinetic energy.
    """
    inertias = [wreck_yaw_inertia(body) for body in (a, b)]
    inv_i = [1.0/value if value else 0.0 for value in inertias]
    point = obb_overlap_point(a, b)
    if point is None:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    arms = [(point[0]-body['x'], point[1]-body['z']) for body in (a,b)]
    velocities = [[body['vx'], body['vz'], body.get('push_yaw', 0.0)] for body in (a,b)]
    changes = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    normal_impulse = 0.0
    for axis, normal in (((hit[0],hit[1]), True), ((-hit[1],hit[0]), False)):
        levers = [arm[1]*axis[0]-arm[0]*axis[1] for arm in arms]
        relative = sum((1.0 if i == 0 else -1.0) *
                       (v[0]*axis[0]+v[1]*axis[1]+v[2]*levers[i])
                       for i,v in enumerate(velocities))
        inverse = [ia,ib] if normal else [
            0.0 if body.get('immovable') else 1.0/body['mass'] for body in (a,b)]
        effective = sum(inverse)+sum(inv_i[i]*levers[i]**2 for i in (0,1))
        if effective <= 0.0 or (normal and relative >= 0.0):
            continue
        impulse = -relative/effective
        if normal:
            normal_impulse = impulse
        else:
            impulse = max(-0.3*normal_impulse, min(0.3*normal_impulse, impulse))
        for i in (0,1):
            signed = impulse if i == 0 else -impulse
            delta = (signed*axis[0]*inverse[i], signed*axis[1]*inverse[i],
                     signed*levers[i]*inv_i[i])
            for k in (0,1,2):
                velocities[i][k] += delta[k]
                changes[i][k] += delta[k]
    return changes


def resolve_pairs(tanks, dt, anchor=None):
    """Resolve an authority's simultaneous contacts once per unordered pair.

    Each later constraint sees the velocities already changed by earlier
    ones. Summing independently solved contacts against frozen velocities
    can cancel the same momentum several times in a crowded spawn and lets
    each side apply a different hull-friction impulse. This shared sweep is
    reciprocal and dissipative for every integrated pair.

    A visible player owns only pairs containing its anchor. Bot/Bot pairs
    remain with the worker; both owners still use the same sequential solve.
    """
    bodies = [dict(tank) for tank in sorted(tanks, key=lambda t: t['id'])]
    results = dict((b['id'], {'correction': (0.0, 0.0),
                              'delta_velocity': (0.0, 0.0),
                              'delta_yaw': 0.0}) for b in bodies)
    shapes = dict((b['id'], _tank_shape(b)) for b in bodies)
    for body in bodies:
        body['shape'] = shapes[body['id']]
    radii = dict((key, math.hypot(*value[:2])) for key, value in shapes.items())
    pairs = []
    for index, a in enumerate(bodies):
        for b in bodies[index+1:]:
            if anchor is not None and anchor not in (a['id'], b['id']):
                continue
            if a.get('kind') == b.get('kind') == 'player':
                continue
            if not (a.get('alive', True) or b.get('alive', True) or
                    a['vx'] or a['vz'] or b['vx'] or b['vz'] or
                    a.get('push_yaw') or b.get('push_yaw')):
                continue
            shape_a, shape_b = shapes[a['id']], shapes[b['id']]
            reach = radii[a['id']]+radii[b['id']]+CONTACT_BROADPHASE_PADDING
            if (a['x']-b['x'])**2 + (a['z']-b['z'])**2 <= reach*reach:
                pairs.append((a, b, shape_a, shape_b))
    for unused_pass in range(4):
        for a, b, shape_a, shape_b in pairs:
            if not vertical_overlap(
                    a.get('y'), shape_a, b.get('y'), shape_b,
                    pitch_a=a.get('pitch', 0.0), roll_a=a.get('roll', 0.0),
                    pitch_b=b.get('pitch', 0.0), roll_b=b.get('roll', 0.0)):
                continue
            hit = obb_contact(a['x'], a['z'], a['yaw'], shape_a,
                              b['x'], b['z'], b['yaw'], shape_b)
            hit = _owner_oriented_contact(hit, a['x']-b['x'], a['z']-b['z'], a['id'], b['id'])
            if hit is None:
                continue
            ia = 0.0 if a.get('immovable') else 1.0/max(a['mass'], 1.0)
            ib = 0.0 if b.get('immovable') else 1.0/max(b['mass'], 1.0)
            mobility_a, mobility_b = grounded_inverse_masses(hit, a, b, ia, ib, dt)
            response = pair_response(hit, mobility_a, mobility_b,
                                     (a['vx'], a['vz']), (b['vx'], b['vz']),
                                     friction_inverse=(ia, ib))
            angular = (_angular_pair_response(a, b, hit, mobility_a, mobility_b)
                       if wreck_yaw_inertia(a) or wreck_yaw_inertia(b) else None)
            # Ownership does not make the peer infinitely heavy. Solve the
            # positional shares with the same physical mobilities in this
            # private constraint roster, then publish only the owned share.
            # The caller still sweeps against the actual remote pose; no
            # speculative remote travel can open a passage through it.
            position_response = pair_response(
                hit, mobility_a, mobility_b,
                (0.0, 0.0), (0.0, 0.0))
            apply_impulse = a.get('impulse', True) and b.get('impulse', True)
            for body, offset in ((a, 0), (b, 4)):
                dx, dz, dvx, dvz = response[offset:offset+4]
                dv_yaw = 0.0
                if angular is not None:
                    dvx, dvz, dv_yaw = angular[0 if offset == 0 else 1]
                dx, dz = position_response[offset:offset+2]
                result = results[body['id']]
                if not body.get('position_fixed'):
                    result['correction'] = (result['correction'][0]+dx, result['correction'][1]+dz)
                body['x'] += dx
                body['z'] += dz
                if apply_impulse:
                    result['delta_velocity'] = (result['delta_velocity'][0]+dvx,
                                                result['delta_velocity'][1]+dvz)
                    body['vx'] += dvx
                    body['vz'] += dvz
                    result['delta_yaw'] += dv_yaw
                    body['push_yaw'] = body.get('push_yaw', 0.0) + dv_yaw
    return results


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
                 active_ram_contacts=None, contact_armor_probe=None, dt=0.0):
    """Resolve one hull against other hulls using only plain data.

    A body with ``alive`` false is a wreck: it blocks, separates and can be
    shoved by its inverse-mass share, but it never produces a ram event and
    never takes ram damage.  A body with ``immovable`` true keeps the old
    infinite-mass behaviour for a hull no process integrates.  A body with
    ``impulse`` false separates without transferring velocity, which leaves
    one owner for a contact that both sides resolve.

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
    responses = []
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
        # A wreck has no engine, but it is still a hull resting on tracks and
        # a heavy enough neighbour shoves it.  ``immovable`` is for a body no
        # process integrates - the local player's own wreck - which has to
        # keep behaving as world geometry rather than absorb a share of the
        # correction nobody will ever apply.
        other_is_immovable = bool(_tank_value(other, 'immovable', False))
        other_x = float(_tank_value(other, 'x', 0.0) or 0.0)
        other_y = _tank_value(other, 'y')
        other_z = float(_tank_value(other, 'z', 0.0) or 0.0)
        other_shape = _tank_shape(other)
        if not vertical_overlap(
                y, own_shape, other_y, other_shape,
                pitch_a=_tank_value(tank, 'pitch', 0.0),
                roll_a=_tank_value(tank, 'roll', 0.0),
                pitch_b=_tank_value(other, 'pitch', 0.0),
                roll_b=_tank_value(other, 'roll', 0.0)):
            continue
        center_dx = x - other_x
        center_dz = z - other_z
        other_radius = math.sqrt(
            other_shape[0] * other_shape[0] +
            other_shape[1] * other_shape[1])
        maximum_distance = own_radius + other_radius + CONTACT_BROADPHASE_PADDING
        if (center_dx * center_dx + center_dz * center_dz >
                maximum_distance * maximum_distance):
            continue

        mass_other = max(
            float(_tank_value(other, 'mass', 1.0) or 1.0), 1.0)
        inverse_other = 0.0 if other_is_immovable else 1.0 / mass_other
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

        if other_is_immovable:
            other_velocity_x = other_velocity_y = other_velocity_z = 0.0
        else:
            # A pushed wreck carries a real contact velocity.  Reading it as
            # zero made the solver see a closing pair that was already moving
            # together and re-cancel the same momentum every frame.
            other_velocity_x = float(_tank_value(other, 'vx', 0.0) or 0.0)
            other_velocity_y = float(_tank_value(other, 'vy', 0.0) or 0.0)
            other_velocity_z = float(_tank_value(other, 'vz', 0.0) or 0.0)
        impact_contact = obb_impact_contact(
            x, z, yaw, own_shape, (velocity_x, velocity_z),
            other_x, other_z, other_yaw, other_shape,
            (other_velocity_x, other_velocity_z))
        mobility_self, mobility_other = grounded_inverse_masses(
            contact, tank, other, inverse_self, inverse_other, dt)
        response = pair_response(
            contact, mobility_self, mobility_other,
            (velocity_x, velocity_z),
            (other_velocity_x, other_velocity_z),
            friction_inverse=(inverse_self, inverse_other))
        correction_x += response[0]
        correction_z += response[1]
        # One owner per contact velocity.  When both sides cancel the same
        # closing velocity in the same frame, each re-solves against the
        # other's already-corrected pose and the pair oscillates; the body
        # that owns the pair keeps the impulse and the other only separates.
        if _tank_value(other, 'impulse', True):
            delta_velocity_x += response[2]
            delta_velocity_z += response[3]
            responses.append((other_id, (response[6], response[7])))

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
            'contact_positions': (x, z, other_x, other_z),
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
        'responses': tuple(responses),
        'ram_events': tuple(ram_events),
        'ram_diagnostics': tuple(ram_diagnostics),
        'cooldowns': cooldowns,
        'contacts': frozenset(
            (previous_contacts & overlap_pairs) | newly_damaging_pairs),
    }


# Names mirror the current 0.8.2 helpers and keep adapter call sites explicit.
_tank_chassis_shape = chassis_shape
_tank_resolve = resolve_tank
