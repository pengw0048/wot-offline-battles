""" BSMO (Static Model) """

from ctypes import c_uint32
from .._base_json_section import *
from ..WSMO.v0_9_12 import (WoTModelInfoItem_v0_9_12,
                          WoTFallingModelInfoItem_v0_9_12,
                          WoTFragileModelInfoItem_v0_9_12)
from .v0_9_12 import (ModelLoddingItem_v0_9_12,
                      ModelColliderItem_v0_9_12,
                      BSPMaterialKindItem_v0_9_12,
                      LODRenderItem_v0_9_12,
                      RenderItem_v0_9_12,
                      AnimationItem_v0_9_12,
                      MinMax)


class VerticesDataSize_v0_9_20(CStructure):
    _size_ = 8

    _fields_ = [
        ('vertices_fnv', c_uint32 ), # path to *.primitives/vertices
        ('data_size',    c_uint32 ), # BWSG/positions/size
        ]


class BSMO_Section_0_9_20(Base_JSON_Section):
    header = 'BSMO'
    int1 = 1

    _fields_ = [
        (list, 'models_loddings',          ModelLoddingItem_v0_9_12        ),
        (list, 'models_colliders',         ModelColliderItem_v0_9_12       ),
        (list, 'bsp_material_kinds',       BSPMaterialKindItem_v0_9_12     ),
        (list, 'models_visibility_bounds', MinMax                          ), # *.model/visibilityBox
        (list, 'model_info_items',         WoTModelInfoItem_v0_9_12        ), # 0.9.12: WSMO['1']
        (list, 'model_sound_items',        '<I'                            ), # 0.9.12: WSMO['5']
        (list, 'lod_loddings',             '<f'                            ), # *.model/extent
        (list, 'lod_renders',              LODRenderItem_v0_9_12           ),
        (list, 'renders',                  RenderItem_v0_9_12              ),
        (list, 'node_affectors1',          '<I'                            ), # link section 'renders' with 'visual_nodes'
        (list, 'animations',               AnimationItem_v0_9_12           ),
        (list, 'node_affectors2',          '<I'                            ),
        (list, 'visual_nodes',             '<I16f'                         ), # *.visual/nodes
        (list, 'model_hardpoint_items',    '<16f'                          ), # 0.9.12: WSMO['4']
        (list, 'falling_model_info_items', WoTFallingModelInfoItem_v0_9_12 ), # 0.9.12: WSMO['2']
        (list, 'fragile_model_info_items', WoTFragileModelInfoItem_v0_9_12 ), # 0.9.12: WSMO['3']
        (list, 'vertices_data_sizes',      VerticesDataSize_v0_9_20        ),
        ]
