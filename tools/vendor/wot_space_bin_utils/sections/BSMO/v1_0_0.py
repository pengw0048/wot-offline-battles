""" BSMO (Static Model) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v0_9_12 import (ModelLoddingItem_v0_9_12,
                      ModelColliderItem_v0_9_12,
                      BSPMaterialKindItem_v0_9_12,
                      LODRenderItem_v0_9_12,
                      RenderItem_v0_9_12,
                      AnimationItem_v0_9_12,
                      MinMax)
from .v0_9_20 import (VerticesDataSize_v0_9_20,
                      WoTModelInfoItem_v0_9_12)


class WoTFallingModelInfoItem_v1_0_0(CStructure):
    '''
    destructibles.xml/fallingAtoms
    in WoT: 55 8B EC 8B 41 50 53 56 57 8B 48 20 8B B0 BC 00 00 00 8B 45 08
    '''
    _size_ = 48

    _fields_ = [
        ('lifetime_effect_fnv',  c_uint32    ), # lifetimeEffect
        ('fracture_effect_fnv',  c_uint32    ), # fractureEffect
        ('touchdown_effect_fnv', c_uint32    ), # touchdownEffect
        ('unknown',              c_float     ),
        ('effect_scale',         c_float     ), # effectScale
        ('physic_params',        c_float * 7 ), # physicParams
        ]

    _tests_ = {
        'unknown': { '==': 1.0 }
        }


class WoTFragileModelInfoItem_v1_0_0(CStructure):
    '''
    destructibles.xml/fragiles
    destructibles.xml/structures
    '''
    _size_ = 36

    _fields_ = [
        ('lifetime_effect_fnv',   c_uint32 ), # lifetimeEffect
        ('effect_fnv',            c_uint32 ), # effect / ramEffect
        ('decay_effect_fnv',      c_uint32 ), # decayEffect
        ('hit_effect_fnv',        c_uint32 ), # hitEffect
        ('_4',                    c_float  ),
        ('effect_scale',          c_float  ), # effectScale
        ('hardpoint_index',       c_uint32 ),
        ('destroyed_model_index', c_uint32 ),
        ('entry_type',            c_uint32 ), # fragiles:0,2, structures:1
        ]

    _tests_ = {
        '_4': { '==': 1.0 },
        'entry_type': { 'in': (0, 1, 2, 3) },
        }


class NodeItem_v1_0_0(CStructure):
    _size_ = 72

    _fields_ = [
        ('parent_index',   c_uint32     ),
        ('transform',      c_float * 16 ),
        ('identifier_fnv', c_uint32     ),
        ]


class HavokInfo_v1_0_0(CStructure):
    _size_ = 8

    _fields_ = [
        ('havok_fnv',    c_uint32 ),
        ('mat_name_fnv', c_uint32 ),
        ]


class BSMO_Section_1_0_0(Base_JSON_Section):
    header = 'BSMO'
    int1 = 2

    _fields_ = [
        (list, 'models_loddings',          ModelLoddingItem_v0_9_12       ),
        (list, 'models_colliders',         ModelColliderItem_v0_9_12      ),
        (list, 'bsp_material_kinds',       BSPMaterialKindItem_v0_9_12    ),
        (list, 'models_visibility_bounds', MinMax                         ),
        (list, 'model_info_items',         WoTModelInfoItem_v0_9_12       ), # 0.9.12: WSMO['1']
        (list, 'model_sound_items',        '<I'                           ), # 0.9.12: WSMO['5']
        (list, 'lod_loddings',             '<f'                           ),
        (list, 'lod_renders',              LODRenderItem_v0_9_12          ),
        (list, 'renders',                  RenderItem_v0_9_12             ),
        (list, 'node_affectors1',          '<I'                           ),
        (list, 'animations',               AnimationItem_v0_9_12          ),
        (list, '12_4',                     '<I'                           ), # node_affectors2 ?
        (list, '13_8',                     '<2I'                          ),
        (list, '14_4',                     '<I'                           ),
        (list, 'visual_nodes',             NodeItem_v1_0_0                ),
        (list, 'model_hardpoint_items',    '<16f'                         ), # 0.9.12: WSMO['4']
        (list, 'falling_model_info_items', WoTFallingModelInfoItem_v1_0_0 ), # 0.9.12: WSMO['2']
        (list, 'fragile_model_info_items', WoTFragileModelInfoItem_v1_0_0 ), # 0.9.12: WSMO['3']
        (list, 'havok_info',               HavokInfo_v1_0_0               ),
        (list, 'vertices_data_sizes',      VerticesDataSize_v0_9_20       ),
        ]
