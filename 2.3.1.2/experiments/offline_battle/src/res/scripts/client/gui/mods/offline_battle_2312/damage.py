"""Decide what a shell does to a vehicle it reaches.

The armour and damage law is `combat_rules`, copied from the 0.9.22
port. This module only feeds it the 2.3.1.2 inputs: the collision layers
`Vehicle.collideSegmentExt` returns, and the travelled distance.
"""
from __future__ import absolute_import

import random

from gui.mods.offline_battle_2312 import combat_rules
from gui.mods.offline_battle_2312 import device_damage

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

CHASSIS_DEVICES = ('leftTrackHealth', 'rightTrackHealth')
GUN_DEVICES = ('gunHealth', 'turretRotatorHealth')


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


def nearest_vehicle(vehicles, start, end):
    """(vehicle, distance along the chord, collisions) for the first hit."""
    best = None
    for vehicle in vehicles:
        collisions = vehicle.collideSegmentExt(start, end)
        if not collisions:
            continue
        distance = min(float(collision.dist) for collision in collisions)
        if best is None or distance < best[1]:
            best = (vehicle, distance, collisions)
    return best


def resolve(shot, travelled, collisions, random_uniform=None):
    """(result, damage) for one shell reaching one vehicle."""
    converted = shot if isinstance(shot, dict) else legacy_shot(shot)
    resolved = combat_rules.resolve_hull_hit(converted, travelled, collisions,
                                             random_uniform=random_uniform)
    if resolved is None:
        return None, 0
    result = resolved[0]
    nominal = combat_rules.he_nominal_armor(collisions)
    return result, combat_rules.damage(converted, result, nominal,
                                       random_uniform=random_uniform)
