# -*- coding: utf-8 -*-
"""Generated catalogue of #1513 vehicles this client cannot load.

Do not edit by hand.  Run
``tools/bake_vehicle_blacklist_0922.py "$WOT_0922_CLIENT"`` to
regenerate it against the pinned client.

Each entry lists required resource paths that are absent from the packages
or point at the retired-vehicle R00_Placeholder instead of real vehicle art.
"""

CLIENT_VERSION = '0.9.22.0.1'
CLIENT_BUILD = '1513'
CATALOGUE_SIZE = 680

UNUSABLE_VEHICLES = {
    'germany:G138_VK168_02_Mauerbrecher': (
        'vehicles/german/G138_VK168_02_Mauerbrecher/collision_client/Chassis.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/collision_client/Gun_02.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/collision_client/Hull.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/collision_client/Turret_01.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/normal/lod0/Chassis.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/normal/lod0/Gun_02.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/normal/lod0/Hull.model',
        'vehicles/german/G138_VK168_02_Mauerbrecher/normal/lod0/Turret_01.model',
    ),
    'germany:G79_Pz_IV_AusfGH': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'uk:GB70_FV4202_105': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'usa:A08_T23': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'usa:A15_T57': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'usa:A26_T18': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'ussr:R05_KV': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
    'ussr:R70_T_50_2': (
        'vehicles/russian/R00_Placeholder/collision_client/Chassis.model',
        'vehicles/russian/R00_Placeholder/collision_client/Gun_01.model',
        'vehicles/russian/R00_Placeholder/collision_client/Hull.model',
        'vehicles/russian/R00_Placeholder/collision_client/Turret_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Chassis.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Gun_01.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model',
        'vehicles/russian/R00_Placeholder/normal/lod0/Turret_01.model',
    ),
}


def is_unusable(name):
    """Return whether the pinned client lacks usable vehicle resources."""
    return str(name or '') in UNUSABLE_VEHICLES


def missing_resources(name):
    """Return absent or placeholder resource paths for one vehicle name."""
    return UNUSABLE_VEHICLES.get(str(name or ''), ())
