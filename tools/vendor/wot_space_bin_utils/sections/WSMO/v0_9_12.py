""" WSMO (WoT Static Models) """

from ctypes import c_float, c_uint32
from .._base_json_section import *


class WoTModelInfoItem_v0_9_12(CStructure):
    '''
    Contains information on the type of WoT model (Static, Falling,
    Fragile, Structure) and an index to associated data for that.
    '''
    _size_ = 8

    _fields_ = [
        ('type',       c_uint32 ),
        ('info_index', c_uint32 ),
        ]


class WoTFallingModelInfoItem_v0_9_12(CStructure):
    '''
    destructibles.xml/fallingAtoms
    Contains the information for falling items such as physics params and effects.
    '''
    _size_ = 44

    _fields_ = [
        ('lifetime_effect_fnv',  c_uint32 ), # lifetimeEffect
        ('fracture_effect_fnv',  c_uint32 ), # fractureEffect
        ('touchdown_effect_fnv', c_uint32 ), # touchdownEffect
        ('effect_scale',         c_float  ), # effectScale
        ('mass',                 c_float  ),
        ('height',               c_float  ),
        ('air_resistance',       c_float  ),
        ('bury_depth_',          c_float  ),
        ('spring_stiffness',     c_float  ),
        ('spring_angle',         c_float  ),
        ('spring_resistance',    c_float  ),
        ]


class WoTFragileModelInfoItem_v0_9_12(CStructure):
    '''
    destructibles.xml/fragiles
    destructibles.xml/structures
    Contains the information for fragile items such as effects, hardpoints and destruction effects.
    '''
    _size_ = 36

    _fields_ = [
        ('lifetime_effect_fnv',   c_uint32 ), # lifetimeEffect
        ('destroyed_effect_fnv',  c_uint32 ), # effect / ramEffect
        ('decay_effect_fnv',      c_uint32 ), # decayEffect
        ('hit_effect_fnv',        c_uint32 ), # hitEffect
        ('havok_resource_fnv',    c_uint32 ),
        ('effect_scale',          c_float  ), # effectScale
        ('hardpoint_index',       c_uint32 ),
        ('destroyed_model_index', c_uint32 ),
        ('entry_type',            c_uint32 ), # fragiles:0, structures:1
        ]

    _tests_ = {
        'havok_resource_fnv': { '==': 0 },
        'entry_type': { 'in': (0, 1) },
        }


class WSMO_Section_0_9_12(Base_JSON_Section):
    header = 'WSMO'
    int1 = 1

    _fields_ = [
        (list, 'model_info_items',         WoTModelInfoItem_v0_9_12        ),
        (list, 'falling_model_info_items', WoTFallingModelInfoItem_v0_9_12 ), # destructibles.xml/fallingAtoms
        (list, 'fragile_model_info_items', WoTFragileModelInfoItem_v0_9_12 ), # destructibles.xml/fragiles, destructibles.xml/structures
        (list, 'model_hardpoint_items',    '<16f'                          ),
        (list, 'model_sound_items',        '<I'                            ),
        ]
