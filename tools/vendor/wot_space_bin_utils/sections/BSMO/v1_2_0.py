""" BSMO (Static Model) """

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


class BSMO_Section_1_2_0(Base_JSON_Section):
    header = 'BSMO'
    int1 = 2

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
        (list, 'fragile_model_info_items', WoTFragileModelInfoItem_v1_0_0 ), # 0.9.12: WSMO['3']
        (list, 'havok_info',               HavokInfo_v1_0_0               ),
        (list, '16_8',                     '<2I'                          ),
        (list, 'vertices_data_sizes',      VerticesDataSize_v0_9_20       ),
        ]
