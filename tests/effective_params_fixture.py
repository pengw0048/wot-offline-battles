def effective_params():
    """One complete canonical effective_params_v1 test fixture."""
    return {
        'version': 1,
        'loadout': {
            'crew_level': 100.0,
            'commander_level': 100.0,
            'effective_crew_level': 110.0,
            'crew_multiplier': 0.96,
            'crew_factor': 1.04,
            'gun_rotation_factor': 1.04,
            'reload_factor': 0.96,
            'aim_time_factor': 0.96,
            'dispersion_factor': 0.96,
            'repair_factor': 0.57,
            'vehicle_rotation_factor': 1.0,
            'terrain_resistance_factors': [1.0, 1.0, 1.0],
            'radio_factor': 1.0,
            'has_big_kit': False,
            'from_client_factors': True,
            'bloom_move_factor': 1.0,
            'bloom_rotation_factor': 1.0,
            'bloom_turret_factor': 1.0,
            'has_rammer': False,
            'has_aim_drives': False,
            'has_ventilation': False,
            'has_stabiliser': False,
            'has_rations': False,
            'has_brotherhood': False,
            'has_snap_shot': False,
            'has_smooth_ride': False,
            'has_sixth_sense': False,
        },
        'physics': {
            'mass': 5730.0,
            'powerW': 33097.0,
            'speedFwd': 8.9,
            'speedBwd': 3.3,
            'rotSpd': 0.66,
            'terrainResist': [1.1, 1.4, 2.6],
            'specificFriction': 0.6867,
            'brakeDecel': 15.9,
            'trackCenter': 1.5,
            'minPlaneNormalY': 0.906,
            'nativePowerRatio': 1.0,
        },
        'spotting': {
            'commander_level': 100.0,
            'recon_level': 0.0,
            'situational_level': 0.0,
            'camouflage_level': 0.0,
            'binocular_factor': 1.0,
            'binocular_delay': 3.0,
            'camouflage_net_bonus': 0.0,
            'camouflage_net_delay': 3.0,
            'has_binoculars': False,
            'has_camouflage_net': False,
            'vision_factor': 1.0,
            'camouflage_factor': 0.57,
            'invisibility_moving': [0.0, 1.0],
            'invisibility_still': [0.0, 1.0],
            'from_client_factors': True,
        },
        'ramming': {
            'spall_coefficient': 1.0,
            'ramming_bonus': 0.0,
        },
        'ammo': [[1, 20], [2, 10]],
        'camouflage': {
            'camouflage_id': None,
            'base_moving': 0.12,
            'base_still': 0.16,
            'shot_factor': 0.25,
        },
        'skills': {
            'sixth_sense': False,
            'expert': False,
            'deadeye': False,
            'intuition_chances': 0,
            'controlled_impact': False,
            'designated_target': False,
            'last_effort': False,
        },
        'crew': {
            'members': [{
                'instance': 'commander',
                'roles': ['commander'],
                'skills': [],
            }],
            'dynamic_spotting': {
                'crew': ['commander'],
                'states': dict(
                    ('%d:%d' % (mask, fire), {
                        'vision': 1.0,
                        'signal': 1.0,
                        'camouflage': 1.0,
                        'base_moving': 0.12,
                        'base_still': 0.16,
                        'invisibility_moving': [0.0, 1.0],
                        'invisibility_still': [0.0, 1.0],
                    })
                    for mask in (0, 1) for fire in (0, 1)),
            },
        },
        'gun': {
            'clip_size': 1,
            'shots': [{
                'compact_descr': 1,
                'source_shot': {
                    'speed': 800.0,
                    'gravity': 9.81,
                    'maxDistance': 720.0,
                    'piercingPower': [100.0, 80.0],
                    'deadeye': False,
                    'shell': {
                        'kind': 'ARMOR_PIERCING',
                        'caliber': 37.0,
                        'damage': [40.0, 20.0],
                        'explosionRadius': 0.0,
                    },
                },
            }, {
                'compact_descr': 2,
                'source_shot': {
                    'speed': 900.0,
                    'gravity': 9.81,
                    'maxDistance': 720.0,
                    'piercingPower': [120.0, 95.0],
                    'deadeye': False,
                    'shell': {
                        'kind': 'ARMOR_PIERCING_CR',
                        'caliber': 37.0,
                        'damage': [40.0, 20.0],
                        'explosionRadius': 0.0,
                    },
                },
            }],
        },
        'equipment': [],
        'critical': {
            'devices': [{
                'name': 'engineHealth',
                'max_hp': 100.0,
                'regen_hp': 50.0,
            }],
            'activation_targets': [],
            'crew_roster': ['commander'],
        },
    }


def bot_default_crew_factors(unused_descriptor, crew=None):
    """Exact factor shape supplied by #1513 to pure-Python Bot fixtures."""
    if crew is not None:
        raise AssertionError('Bot fixtures require the plain default crew')
    crew_factor = 0.57 + 0.0043 * 110.0
    crew_multiplier = 1.0 / crew_factor
    return {
        'turret/rotationSpeed': crew_factor,
        'gun/rotationSpeed': crew_factor,
        'gun/reloadTime': crew_multiplier,
        'gun/aimingTime': crew_multiplier,
        'shotDispersion': (crew_multiplier,),
        'repairSpeed': 0.57,
        'vehicle/rotationSpeed': 1.0,
        'engine/power': 1.0,
        'chassis/terrainResistance': (1.0, 1.0, 1.0),
        'radio/distance': 1.0,
        'circularVisionRadius': 1.0,
        'camouflage': 0.57,
    }


def equipment_ledger():
    """One complete empty canonical equipment ledger fixture."""
    return {
        'equipment_states': [],
        'equipment_revision': 0,
        'equipment_intent_seq': 0,
        'equipment_intent_result': {
            'intent_seq': 0,
            'accepted': False,
            'reason': '',
        },
    }


def wire_player(player_id, **values):
    """One complete player row accepted by modern roster wires."""
    result = {
        'id': player_id,
        'outfits': {},
        'effective_params': effective_params(),
        'critical_revision': 0,
        'critical_base_revision': 0,
        'critical_ack_seq': 0,
    }
    result.update(equipment_ledger())
    result.update(values)
    return result
