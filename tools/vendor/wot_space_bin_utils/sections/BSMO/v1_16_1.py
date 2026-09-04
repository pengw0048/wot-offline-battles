""" BSMO (Static Model) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v0_9_12 import (ModelLoddingItem_v0_9_12,
                      ModelColliderItem_v0_9_12,
                      BSPMaterialKindItem_v0_9_12,
                      LODRenderItem_v0_9_12,
                      RenderItem_v0_9_12,
                      MinMax)
from .v1_0_0 import (VerticesDataSize_v0_9_20,
                     WoTModelInfoItem_v0_9_12,
                     WoTFallingModelInfoItem_v1_0_0,
                     WoTFragileModelInfoItem_v1_0_0,
                     NodeItem_v1_0_0,
                     HavokInfo_v1_0_0)



class WoTFragileModelInfoItem_v1_16_1(CStructure):
    '''
    destructibles.xml/fragiles
    destructibles.xml/structures
    '''
    _size_ = 40

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
        ('unknown',               c_uint32 ), # ???
        ]

    _tests_ = {
        '_4': { '==': 1.0 },
        'entry_type': { 'in': (0, 1, 2, 3) },
        }



class BSMO_Section_1_16_1(Base_JSON_Section):
    header = 'BSMO'
    int1 = 3

    _fields_ = [
        (list, 'models_loddings',          ModelLoddingItem_v0_9_12       ),
        (list, '1_4',                      '<I'                           ),
        (list, 'models_colliders',         ModelColliderItem_v0_9_12      ),
        (list, 'bsp_material_kinds',       BSPMaterialKindItem_v0_9_12    ),
        (list, 'models_visibility_bounds', MinMax                         ),
        (list, 'model_info_items',         WoTModelInfoItem_v0_9_12       ), # 0.9.12: WSMO['1']
        (list, 'model_sound_items',        '<I'                           ), # 0.9.12: WSMO['5']
        (list, 'lod_loddings',             '<f'                           ),
        (list, 'lod_renders',              LODRenderItem_v0_9_12          ),
        (list, 'renders',                  RenderItem_v0_9_12             ),
        (list, 'node_affectors1',          '<I'                           ),
        (list, 'visual_nodes',             NodeItem_v1_0_0                ),
        (list, 'model_hardpoint_items',    '<16f'                         ), # 0.9.12: WSMO['4']
        (list, 'falling_model_info_items', WoTFallingModelInfoItem_v1_0_0 ), # 0.9.12: WSMO['2']
        (list, 'fragile_model_info_items', WoTFragileModelInfoItem_v1_16_1 ), # 0.9.12: WSMO['3']
        (list, 'havok_info',               HavokInfo_v1_0_0               ),
        (list, '16_8',                     '<2I'                          ),
        (list, 'vertices_data_sizes',      VerticesDataSize_v0_9_20       ),
        ]
