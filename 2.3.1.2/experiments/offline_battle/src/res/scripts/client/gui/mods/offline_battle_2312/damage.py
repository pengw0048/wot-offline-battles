"""Decide what a shell does to a vehicle it reaches.

The armour and damage law is `combat_rules`, copied from the 0.9.22
port. This module only feeds it the 2.3.1.2 inputs: the collision layers
`Vehicle.collideSegmentExt` returns, and the travelled distance.
"""
from __future__ import absolute_import

import math
import random

from gui.mods.offline_battle_2312 import combat_rules
from gui.mods.offline_battle_2312 import device_damage
from gui.mods.offline_battle_2312 import tank_collision

RICOCHET = 0
NOT_PIERCED = 1
PIERCED = 2

# constants.VEHICLE_HIT_FLAGS, frozen here so a hit report needs no import.
HIT_VEHICLE_KILLED = 1
HIT_RICOCHET = 8
HIT_PIERCED = 16
HIT_NOT_PIERCED = 32
HIT_DEVICE_DAMAGED = 1024
HIT_CHASSIS_DAMAGED = 2048
HIT_GUN_DAMAGED = 4096
HIT_DIRECT_PROJECTILE = 1048576

CHASSIS_DEVICES = ('chassisHealth', 'leftTrackHealth', 'rightTrackHealth')
GUN_DEVICES = ('gunHealth', 'turretRotatorHealth')

# 2.3.1.2 carries one chassis device where the copied law tables expect a
# track per side. Every other device and every crew name already matches.
LAW_DEVICE_NAMES = {
    'chassisHealth': ('leftTrackHealth', 'rightTrackHealth'),
}


class ShotResult(object):
    """The fields Avatar.showShotResults reads from one result."""

    def __init__(self, vehicle_id, hit_flags, gun_installation_index=0):
        self.vehicleID = vehicle_id
        self.hitFlags = hit_flags
        self.gunInstallationIndex = gun_installation_index


def hit_flags(result, killed):
    flags = HIT_DIRECT_PROJECTILE
    if result == RICOCHET:
        flags |= HIT_RICOCHET
    elif result == PIERCED:
        flags |= HIT_PIERCED
    else:
        flags |= HIT_NOT_PIERCED
    if killed:
        flags |= HIT_VEHICLE_KILLED
    return flags


HIGH_EXPLOSIVE_KINDS = ('HIGH_EXPLOSIVE', 'HIGH_EXPLOSIVE_MODERN',
                        'HIGH_EXPLOSIVE_LEGACY_STUN',
                        'HIGH_EXPLOSIVE_LEGACY_NO_STUN')


def legacy_shot(shot):
    """The shot dict the copied law reads.

    2.3.1.2 keeps the shell damage in `armorDamage` and the blast radius
    on the shell type, and it splits high explosive into several kind
    names the law only knows one of."""
    shell = shot.shell
    kind = shell.kind
    return {
        'shell': {
            'kind': 'HIGH_EXPLOSIVE' if kind in HIGH_EXPLOSIVE_KINDS
                    else kind,
            'caliber': shell.caliber,
            'damage': shell.armorDamage,
            'deviceDamage': shell.deviceDamage,
            'explosionRadius': getattr(shell.type, 'explosionRadius', 0.0),
        },
        'piercingPower': shot.piercingPower,
        'maxDistance': shot.maxDistance,
    }


def module_hits(collisions, random_uniform=None):
    """Devices this shell crits, by the copied saving throws.

    A crit-only material carries the device it protects in `extra`, and
    the material itself carries the chance the device is actually hit."""
    sampler = random.uniform if random_uniform is None else random_uniform
    names = []
    for collision in collisions or ():
        material = collision.matInfo
        if material is None:
            continue
        extra = getattr(material, 'extra', None)
        name = getattr(extra, 'name', None)
        if not name or name in names:
            continue
        if sampler(0.0, 1.0) < device_damage.saving_throw(material, name):
            names.append(name)
    return names


def law_devices(names):
    """The same devices, named the way the copied law tables name them."""
    result = []
    for name in names or ():
        for mapped in LAW_DEVICE_NAMES.get(name, (name,)):
            if mapped not in result:
                result.append(mapped)
    return result


TURRET_PART_INDEX = 2
GUN_PART_INDEX = 3


def hull_local_point(pose, point):
    """A world point in hull-local metres: +z forward, +x right."""
    delta_x = float(point[0]) - float(pose[0])
    delta_z = float(point[2]) - float(pose[2])
    sin_yaw = math.sin(float(pose[3]))
    cos_yaw = math.cos(float(pose[3]))
    return (delta_x * cos_yaw - delta_z * sin_yaw,
            delta_x * sin_yaw + delta_z * cos_yaw)


def interior_hit(descriptor, part_index, local_point, random_roll=None):
    """The device a penetrating shell plausibly reaches inside.

    The client's collision layers carry no device on this vehicle, so
    the copied interior model decides, from the compartment the shell
    entered and the crew this tank actually has."""
    if part_index in (TURRET_PART_INDEX, GUN_PART_INDEX):
        zone = 'turret'
    else:
        try:
            bbox = descriptor.hull.hitTester.bbox
            half_width = max(abs(float(bbox[0][0])), abs(float(bbox[1][0])))
        except (AttributeError, IndexError, TypeError, ValueError):
            half_width = 1.0
        try:
            ring_z = float(descriptor.hull.turretPositions[0][2])
        except (AttributeError, IndexError, TypeError, ValueError):
            ring_z = 0.0
        zone = device_damage.interior_zone(local_point[0], local_point[1],
                                           ring_z, half_width)
    roster = []
    for role in getattr(descriptor.type, 'crewRoles', ()) or ():
        roster.extend(role if isinstance(role, (tuple, list)) else [role])
    candidates = device_damage.interior_candidates(zone, roster, descriptor)
    return device_damage.pick_interior(candidates, random_roll), zone


def crit_flags(names):
    """Report a crit the way the cell reports one, by device group."""
    flags = 0
    for name in names or ():
        if name in CHASSIS_DEVICES:
            flags |= HIT_CHASSIS_DAMAGED
        elif name in GUN_DEVICES:
            flags |= HIT_GUN_DAMAGED
        else:
            flags |= HIT_DEVICE_DAMAGED
    return flags


def running_gear_reach(vehicle, pose, start, end):
    """Distance along the chord at which it crosses the running gear.

    The client's collision layers never include the chassis, so a shell
    aimed at a track or an idler reports nothing at all and flies on.
    The chassis box comes from the vehicle's own hit tester."""
    shape = tank_collision.chassis_shape(vehicle.typeDescriptor)
    half_width, half_length = shape[0], shape[1]
    lower = pose[1] + shape[2]
    upper = pose[1] + shape[3]
    sin_yaw, cos_yaw = math.sin(pose[3]), math.cos(pose[3])
    steps = 12
    for index in range(steps + 1):
        fraction = index / float(steps)
        x = start.x + (end.x - start.x) * fraction
        y = start.y + (end.y - start.y) * fraction
        z = start.z + (end.z - start.z) * fraction
        if not lower <= y <= upper:
            continue
        delta_x, delta_z = x - pose[0], z - pose[2]
        local_x = delta_x * cos_yaw - delta_z * sin_yaw
        local_z = delta_x * sin_yaw + delta_z * cos_yaw
        if abs(local_x) <= half_width and abs(local_z) <= half_length:
            return math.sqrt((x - start.x) ** 2 + (y - start.y) ** 2 +
                             (z - start.z) ** 2)
    return None


def nearest_vehicle(vehicles, start, end, poses=None):
    """(vehicle, distance along the chord, collisions) for the first hit.

    An empty collision list means the shell reached the running gear and
    nothing else, which is the track hit the armour layers never show."""
    best = None
    for vehicle in vehicles:
        collisions = vehicle.collideSegmentExt(start, end)
        distance = None
        if collisions:
            distance = min(float(collision.dist) for collision in collisions)
        elif poses is not None:
            pose = poses(vehicle.id)
            if pose is not None:
                distance = running_gear_reach(vehicle, pose, start, end)
                collisions = []
        if distance is None:
            continue
        if best is None or distance < best[1]:
            best = (vehicle, distance, collisions)
    return best


TRACK_ABSORBED = -1


def resolve(shot, travelled, collisions, random_uniform=None):
    """(result, damage) for one shell reaching one vehicle.

    A result of TRACK_ABSORBED means the running gear swallowed the
    shell before it reached structure. That is the track hit that breaks
    a track, and it deals no hull damage."""
    converted = shot if isinstance(shot, dict) else legacy_shot(shot)
    resolved = combat_rules.resolve_hull_hit(converted, travelled, collisions,
                                             random_uniform=random_uniform)
    if resolved is None:
        return (TRACK_ABSORBED if collisions else None), 0
    result = resolved[0]
    nominal = combat_rules.he_nominal_armor(collisions)
    return result, combat_rules.damage(converted, result, nominal,
                                       random_uniform=random_uniform)
