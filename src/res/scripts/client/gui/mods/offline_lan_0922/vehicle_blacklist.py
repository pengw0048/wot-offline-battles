# -*- coding: utf-8 -*-
"""Generated catalogue of #1513 vehicles this client cannot load.

Do not edit by hand.  Run
``tools/bake_vehicle_blacklist_0922.py "$WOT_0922_CLIENT"`` to
regenerate it against the pinned client.

Each entry lists the exact resource paths the vehicle item definition
references and no package member provides.
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
}


def is_unusable(name):
    """Return whether the pinned client lacks this vehicle's resources."""
    return str(name or '') in UNUSABLE_VEHICLES


def missing_resources(name):
    """Return the absent resource paths recorded for one vehicle name."""
    return UNUSABLE_VEHICLES.get(str(name or ''), ())
