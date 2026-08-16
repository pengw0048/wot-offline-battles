"""Collide a shell with a vehicle at the pose it is drawn at.

Taken from the 0.9.22 port's `collide_vehicle_at_matrix`. The client's
own `Vehicle.collideSegmentExt` runs the ray through the native filter
first, and this port leaves that filter at the spawn pose, so it reports
no chassis at all: a shell aimed at a track or an idler finds nothing.
Asking each descriptor component's own hit tester finds all four parts,
and returns the component's own material, devices included.
"""
from __future__ import absolute_import

from collections import namedtuple

# The client's own shape, name for name. compName is the item type name.
SegmentCollisionResultExt = namedtuple(
    'SegmentCollisionResultExt',
    ('dist', 'hitAngleCos', 'matInfo', 'compName'))


def _value(component, name, default=None):
    return getattr(component, name, default)


def pose_components(vehicle, math_module):
    """Descriptor hit-test transforms for one visible vehicle pose."""
    descriptor = vehicle.typeDescriptor
    result = []
    identity = math_module.Matrix()
    identity.setIdentity()
    result.append((descriptor.chassis, identity))

    hull_offset = _value(descriptor.chassis, 'hullPosition',
                         math_module.Vector3(0.0, 0.0, 0.0))
    hull = math_module.Matrix()
    hull.setTranslate(-hull_offset)
    result.append((descriptor.hull, hull))

    turret_positions = _value(descriptor.hull, 'turretPositions', ())
    turret_offset = (turret_positions[0] if turret_positions else
                     math_module.Vector3(0.0, 0.0, 0.0))
    turret = math_module.Matrix()
    turret.setTranslate(-hull_offset - turret_offset)
    rotation = math_module.Matrix()
    rotation.setRotateY(-math_module.Matrix(
        vehicle.appearance.turretMatrix).yaw)
    turret.postMultiply(rotation)
    result.append((descriptor.turret, turret))

    gun_offset = _value(descriptor.turret, 'gunPosition',
                        math_module.Vector3(0.0, 0.0, 0.0))
    gun = math_module.Matrix()
    gun.setTranslate(-gun_offset)
    rotation = math_module.Matrix()
    rotation.setRotateX(-math_module.Matrix(
        vehicle.appearance.gunMatrix).pitch)
    gun.postMultiply(rotation)
    gun.preMultiply(turret)
    result.append((descriptor.gun, gun))
    return result


def collide_at_matrix(vehicle, vehicle_matrix, start_point, end_point):
    """Layers a shell crosses, sorted from the muzzle outwards."""
    import Math
    world_to_vehicle = Math.Matrix(vehicle_matrix)
    world_to_vehicle.invert()
    start = world_to_vehicle.applyPoint(start_point)
    end = world_to_vehicle.applyPoint(end_point)
    hits = []
    for component, component_matrix in pose_components(vehicle, Math):
        tester = _value(component, 'hitTester')
        local_hit_test = getattr(tester, 'localHitTest', None)
        if not callable(local_hit_test):
            continue
        collisions = local_hit_test(component_matrix.applyPoint(start),
                                    component_matrix.applyPoint(end))
        materials = _value(component, 'materials', {}) or {}
        name = _value(component, 'itemTypeName')
        for collision in collisions or ():
            try:
                distance, _triangle, angle_cos, kind = collision
            except (TypeError, ValueError):
                continue
            hits.append(SegmentCollisionResultExt(
                float(distance), float(angle_cos), materials.get(kind), name))
    hits.sort(key=lambda item: item.dist)
    return hits
