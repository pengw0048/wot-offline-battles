from __future__ import division, print_function

"""Pure #1513 gun geometry shared by client and server authority.

The transform order mirrors ``VehicleGunRotator.__getShotPosition`` and
``physics_shared.computeBarrelLocalPoint`` from the pinned 0.9.22 #1513
client.  Every angle is in radians.  ``turret_yaw`` is relative to the hull,
and ``gun_pitch`` uses BigWorld's sign convention (negative raises the gun).
The supplied hull yaw, pitch, and roll must be the stabilised vehicle pose.
"""

import math


__all__ = (
    'barrel_world_point',
    'compute_barrel_local_point',
    'inverse_transform_vehicle_vector',
    'shot_origin_and_direction',
    'transform_vehicle_point',
    'transform_vehicle_vector',
    'vehicle_aim_point',
    'vehicle_aim_samples',
    'world_direction_to_local_gun_angles',
)


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


def _component(descriptor, name):
    value = _field(descriptor, name)
    if value is None:
        raise ValueError('descriptor has no %s component' % name)
    return value


def _add(first, second):
    return (first[0] + second[0], first[1] + second[1],
            first[2] + second[2])


def _rotate_x(vector, angle):
    sine = math.sin(angle)
    cosine = math.cos(angle)
    return (vector[0],
            cosine * vector[1] - sine * vector[2],
            sine * vector[1] + cosine * vector[2])


def _rotate_y(vector, angle):
    sine = math.sin(angle)
    cosine = math.cos(angle)
    return (cosine * vector[0] + sine * vector[2],
            vector[1],
            -sine * vector[0] + cosine * vector[2])


def _rotate_z(vector, angle):
    sine = math.sin(angle)
    cosine = math.cos(angle)
    return (cosine * vector[0] - sine * vector[1],
            sine * vector[0] + cosine * vector[1],
            vector[2])


def transform_vehicle_vector(vector, yaw, pitch=0.0, roll=0.0):
    """Apply BigWorld's stabilised yaw/pitch/roll rotation to a vector."""
    vector = _vector3(vector, 'vehicle-local vector')
    yaw = _finite_number(yaw, 'vehicle yaw')
    pitch = _finite_number(pitch, 'vehicle pitch')
    roll = _finite_number(roll, 'vehicle roll')
    # Math.Matrix.setRotateYPR exposes local roll, then pitch, then yaw in
    # world space. This is the same basis used by #1513's vehicle matrix.
    return _rotate_y(_rotate_x(_rotate_z(vector, roll), pitch), yaw)


def inverse_transform_vehicle_vector(vector, yaw, pitch=0.0, roll=0.0):
    """Transform one world vector back into the stabilised vehicle basis."""
    vector = _vector3(vector, 'world vector')
    yaw = _finite_number(yaw, 'vehicle yaw')
    pitch = _finite_number(pitch, 'vehicle pitch')
    roll = _finite_number(roll, 'vehicle roll')
    # Forward is Ry(yaw) * Rx(pitch) * Rz(roll); invert in reverse order.
    return _rotate_z(_rotate_x(_rotate_y(
        vector, -yaw), -pitch), -roll)


def world_direction_to_local_gun_angles(
        direction, yaw, pitch=0.0, roll=0.0):
    """Return local turret yaw and BigWorld gun pitch for a world ray."""
    local = inverse_transform_vehicle_vector(
        direction, yaw, pitch, roll)
    length = math.sqrt(sum(value * value for value in local))
    if length <= 1.0e-12:
        raise ValueError('world direction has zero length')
    local = tuple(value / length for value in local)
    horizontal = math.sqrt(local[0] * local[0] + local[2] * local[2])
    return (math.atan2(local[0], local[2]),
            -math.atan2(local[1], max(1.0e-12, horizontal)))


def transform_vehicle_point(point, position, yaw, pitch=0.0, roll=0.0):
    """Apply the stabilised vehicle matrix to one vehicle-local point."""
    position = _vector3(position, 'vehicle position')
    return _add(position, transform_vehicle_vector(
        point, yaw, pitch, roll))


def vehicle_aim_samples(descriptor):
    """Return bounded hull/turret samples in their reviewed #1513 frames.

    Quarter-box centres sample partial exposure without inventing a vehicle
    height. Missing geometry contributes no candidate. These are aiming
    hypotheses, not armour plates or proof that a projectile penetrates.
    """
    chassis = _field(descriptor, 'chassis')
    hull = _field(descriptor, 'hull')
    try:
        hull_mount = _vector3(_field(chassis, 'hullPosition'),
                              'chassis.hullPosition')
    except (TypeError, ValueError):
        return ()
    components = [('hull', hull, hull_mount, 0.0)]
    try:
        turret_mount = _add(hull_mount, _vector3(
            _field(hull, 'turretPositions')[0], 'hull.turretPositions[0]'))
        static_yaw = _field(_field(descriptor, 'gun'), 'staticTurretYaw')
        if static_yaw is not None:
            static_yaw = _finite_number(static_yaw, 'staticTurretYaw')
        components.append(('turret', _field(descriptor, 'turret'),
                           turret_mount, static_yaw))
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    centres = []
    edges = []
    for name, component, mount, static_yaw in components:
        try:
            bbox = _field(_field(component, 'hitTester'), 'bbox')
            lower = _vector3(bbox[0], 'aim bbox minimum')
            upper = _vector3(bbox[1], 'aim bbox maximum')
            if any(upper[axis] <= lower[axis] for axis in range(3)):
                continue
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        centre = tuple((lower[axis] + upper[axis]) * 0.5
                       for axis in range(3))
        centres.append((name, mount, centre, static_yaw))
        for axis, sign in ((0, -1.0), (0, 1.0), (1, 1.0)):
            point = list(centre)
            point[axis] += (upper[axis] - lower[axis]) * 0.25 * sign
            edges.append((name, mount, tuple(point), static_yaw))
    return tuple(centres + edges)


def vehicle_aim_point(sample, pose):
    """Resolve a retained part sample against the current observed pose."""
    name, mount, point, static_yaw = sample
    yaw = _finite_number(pose.get('yaw', 0.0), 'target yaw')
    if name == 'turret':
        turret_yaw = (static_yaw if static_yaw is not None else
                      pose.get('turret_yaw',
                               pose.get('aim_yaw', yaw) - yaw))
        point = _rotate_y(point, _finite_number(turret_yaw, 'target turret'))
    elif name != 'hull':
        raise ValueError('unknown aim component')
    position = pose.get('position')
    if position is None:
        position = (pose['x'], pose['y'], pose['z'])
    return transform_vehicle_point(
        _add(mount, point), position, yaw,
        pose.get('pitch', 0.0), pose.get('roll', 0.0))


def _mount_offsets(descriptor, turret_index):
    chassis = _component(descriptor, 'chassis')
    hull = _component(descriptor, 'hull')
    turret = _component(descriptor, 'turret')
    hull_position = _vector3(
        _field(chassis, 'hullPosition'), 'chassis.hullPosition')
    turret_positions = _field(hull, 'turretPositions')
    try:
        turret_position = turret_positions[turret_index]
    except (TypeError, IndexError, KeyError):
        raise ValueError('hull.turretPositions has no index %d' %
                         turret_index)
    turret_position = _vector3(
        turret_position, 'hull.turretPositions[%d]' % turret_index)
    gun_position = _vector3(
        _field(turret, 'gunPosition'), 'turret.gunPosition')
    return hull_position, turret_position, gun_position


def _active_turret_position(descriptor):
    value = _field(descriptor, 'activeTurretPosition')
    if value is None or isinstance(value, bool):
        raise ValueError('descriptor has no exact activeTurretPosition')
    try:
        index = int(value)
        exact = float(value) == index
    except (TypeError, ValueError, OverflowError):
        exact = False
    if not exact or index < 0:
        raise ValueError('descriptor has an invalid activeTurretPosition')
    return index


def shot_origin_and_direction(descriptor, position, yaw, pitch, roll,
                              turret_yaw, gun_pitch):
    """Return #1513's physical gun-pivot origin and unit shot direction.

    The gun pivot always uses ``hull.turretPositions[0]``, exactly like
    ``VehicleGunRotator.__getShotPosition``. ``activeTurretPosition`` is a
    separate barrel-water contract and deliberately does not affect it.
    """
    turret_yaw = _finite_number(turret_yaw, 'turret yaw')
    gun_pitch = _finite_number(gun_pitch, 'gun pitch')
    hull_position, turret_position, gun_position = _mount_offsets(
        descriptor, 0)
    turret_offset = _add(hull_position, turret_position)
    local_origin = _add(turret_offset, _rotate_y(
        gun_position, turret_yaw))
    local_direction = _rotate_y(_rotate_x(
        (0.0, 0.0, 1.0), gun_pitch), turret_yaw)
    origin = transform_vehicle_point(
        local_origin, position, yaw, pitch, roll)
    direction = transform_vehicle_vector(
        local_direction, yaw, pitch, roll)
    return origin, direction


def compute_barrel_local_point(descriptor, turret_yaw, gun_pitch):
    """Return the exact vehicle-local barrel endpoint used by #1513 water.

    This is the pure equivalent of
    ``physics_shared.computeBarrelLocalPoint``. The barrel length is the gun
    hit-tester bounding box's maximum local Z, not a model node or a fixed
    forward offset.
    """
    turret_yaw = _finite_number(turret_yaw, 'turret yaw')
    gun_pitch = _finite_number(gun_pitch, 'gun pitch')
    active_index = _active_turret_position(descriptor)
    hull_position, turret_position, gun_position = _mount_offsets(
        descriptor, active_index)
    gun = _component(descriptor, 'gun')
    hit_tester = _field(gun, 'hitTester')
    bbox = _field(hit_tester, 'bbox')
    try:
        maximum = bbox[1]
    except (TypeError, IndexError, KeyError):
        raise ValueError('gun.hitTester.bbox has no maximum point')
    maximum = _vector3(maximum, 'gun.hitTester.bbox[1]')
    point = _rotate_x((0.0, 0.0, maximum[2]), gun_pitch)
    point = _add(point, gun_position)
    point = _rotate_y(point, turret_yaw)
    point = _add(point, turret_position)
    return _add(point, hull_position)


def barrel_world_point(descriptor, position, yaw, pitch, roll,
                       turret_yaw, gun_pitch):
    """Return the barrel endpoint after the stabilised vehicle transform."""
    return transform_vehicle_point(
        compute_barrel_local_point(descriptor, turret_yaw, gun_pitch),
        position, yaw, pitch, roll)
