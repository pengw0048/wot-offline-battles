""" BSMO (Static Model) """

from ctypes import c_float, c_uint32, c_int32
from .._base_json_section import *


#===============================================================================
# Contains the list of LODs for this model.
#===============================================================================
class ModelLoddingItem_v0_9_12(CStructure):
    _size_ = 8

    _fields_ = [
        ('lod_begin', c_uint32 ),
        ('lod_end',   c_uint32 ),
        ]


#===============================================================================
# Contains the collision data for this model such as bounds and bsp.
#===============================================================================
class ModelColliderItem_v0_9_12(CStructure):
    _size_ = 36

    _fields_ = [
        ('collision_bounds_min',    c_float * 3 ), # .visual/boundingBox/min
        ('collision_bounds_max',    c_float * 3 ), # .visual/boundingBox/max
        ('bsp_section_name_fnv',    c_uint32    ), # path to .primitives
        ('bsp_material_kind_begin', c_uint32    ),
        ('bsp_material_kind_end',   c_uint32    ),
        ]


#===============================================================================
# Contains data for bsp material linkage and flags
#
# flags:
#    WorldTriangle::Flags
#===============================================================================
class BSPMaterialKindItem_v0_9_12(CStructure):
    _size_ = 8

    _fields_ = [
        ('material_index', c_uint32 ),
        ('flags',          c_uint32 ),
        ]

    _tests_ = {
        'flags': { 'in': (0x0000, 0x0083, 0x0090, 0x00ff,
                          0x4900, 0x4980, 0x4983,
                          0x4a00, 0x4a83,
                          0x4b00, 0x4b83,
                          0x4c00, 0x4c83,
                          0x4d00, 0x4d83,
                          0x4e00, 0x4e83,
                          0x4f00, 0x4f83,
                          0x5000, 0x5083,
                          0x5700,
                          0x5800,
                          0x65ff,
                          0x6c00, 0x6c83, 0x6cff,
                          0x6f00, 0x6f83, 0x6fff,
                          0x7000, 0x7083, 0x70ff,) }
        }


# Bounding Box
class MinMax(CStructure):
    _size_ = 24

    _fields_ = [
        ('min', c_float * 3 ),
        ('max', c_float * 3 ),
        ]


#===============================================================================
# Contains the render sets to draw for this lod
#===============================================================================
class LODRenderItem_v0_9_12(CStructure):
    _size_ = 8

    _fields_ = [
        ('render_set_begin', c_uint32 ),
        ('render_set_end',   c_uint32 ),
        ]


#===============================================================================
# Contains the data for rendering a model segment, including nodes, material,
# primitives, verts and draw flags.
#
# node_begin:
#   0xFFFFFFFF if renderSet/node = Scene Root
#
# node_end:
#   0xFFFFFFFF if renderSet/node = Scene Root
#
# primitive_index:
#   *.visual/renderSet/geometry/primitiveGroup
#
# verts_name_fnv:
#   *.primitives/vertices
#
# prims_name_fnv:
#   *.primitives/indices
#
# is_skinned:
#   *.visual/renderSet/treatAsWorldSpaceObject
#===============================================================================
class RenderItem_v0_9_12(CStructure):
    _size_ = 28

    _fields_ = [
        ('node_begin',      c_uint32     ),
        ('node_end',        c_uint32     ),
        ('material_index',  c_uint32     ),
        ('primitive_index', c_uint32     ),
        ('verts_name_fnv',  c_uint32     ),
        ('prims_name_fnv',  c_uint32     ),
        ('is_skinned',      c_uint32, 1  ),
        ('pad',             c_uint32, 31 ),
        ]

    _tests_ = {
        'pad': { '==': 0 }
        }


#===============================================================================
# Contains information for animating a model including the animation name,
# frames, nodes and channels.
#
# anim_name_fnv:
#   .model/animation/name
#
# frame_rate:
#   .model/animation/frameRate
#
# first_frame:
#   .model/animation/firstFrame
#
# last_frame:
#   .model/animation/lastFrame
#
# cognate_fnv:
#   .model/animation/cognate
#
# nodes_name_fnv:
#   .model/animation/nodes + .animation
#
# anca_resource_name_fnv:
#   .model/nodefullVisual + .anca
#===============================================================================
class AnimationItem_v0_9_12(CStructure):
    _size_ = 36

    _fields_ = [
        ('anim_name_fnv',          c_uint32 ),
        ('frame_rate',             c_float  ),
        ('first_frame',            c_int32  ),
        ('last_frame',             c_int32  ),
        ('cognate_fnv',            c_uint32 ),
        ('nodes_name_fnv',         c_uint32 ),
        ('channel_node_begin',     c_uint32 ),
        ('channel_node_end',       c_uint32 ),
        ('anca_resource_name_fnv', c_uint32 ),
        ]


class BSMO_Section_0_9_12(Base_JSON_Section):
    header = 'BSMO'
    int1 = 1

    _fields_ = [
        # Contains the list of LODs for this model
        (list, 'models_loddings', ModelLoddingItem_v0_9_12),

        # Contains the collision data for this model such as bounds and bsp
        (list, 'models_colliders', ModelColliderItem_v0_9_12),

        # Contains data for bsp material linkage and flags
        (list, 'bsp_material_kinds', BSPMaterialKindItem_v0_9_12),

        # Contains the visibility bounds for the model
        # *.model/visibilityBox
        (list, 'models_visibility_bounds', MinMax),

        # Contains the distance information for the lod
        # *.model/extent
        (list, 'lod_loddings', '<f'),

        # Contains the render sets to draw for this lod
        (list, 'lod_renders', LODRenderItem_v0_9_12),

        # Contains the data for rendering a model segment, including nodes,
        # material, primitives, verts and draw flags
        (list, 'renders', RenderItem_v0_9_12),

        # Contains a relative index to a node that affects this render set,
        # so we can have sequential access
        (list, 'node_affectors1', '<I'),

        # Contains information for animating a model including the animation name,
        # frames, nodes and channels
        (list, 'animations', AnimationItem_v0_9_12),

        # Contains an index to the nodes that the animation effects so they can be stored sequentially
        (list, 'node_affectors2', '<I'),

        # *.visual/nodes
        (list, 'visual_nodes', '<I16f'),
        ]
