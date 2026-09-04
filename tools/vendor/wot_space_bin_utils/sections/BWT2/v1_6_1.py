""" BWT2 (Terrain 2) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v0_9_12 import ChunkTerrain_v0_9_12
from .v0_9_20 import TerrainSettings1_v0_9_20
from .v1_0_0 import OutlandCascade_v1_0_0
from .v1_4_0 import BWT2_Section_1_4_0


class TerrainSettings2_v1_6_1(CStructure):
    _size_ = 128

    _fields_ = [
        ('terrain_version',                    c_uint32    ), # space.settings/terrain/version
        ('blend_map_caching',                  c_uint32,  1), # terrain/blendMapCaching
        ('normal_map_caching',                 c_uint32,  1), # terrain/normalMapCaching
        ('pad1',                               c_uint32,  1),
        ('enable_auto_rebuild_normal_map',     c_uint32,  1), # terrain/editor/enableAutoRebuildNormalMap
        ('pad2',                               c_uint32,  1),
        ('enable_auto_rebuild_water_geometry', c_uint32,  1), # terrain/editor/enableAutoRebuildWaterGeometry
        ('pad3',                               c_uint32, 26),
        ('height_map_size',                    c_uint32    ), # terrain/heightMapSize
        ('normal_map_size',                    c_uint32    ), # terrain/normalMapSize
        ('hole_map_size',                      c_uint32    ), # terrain/holeMapSize
        ('shadow_map_size',                    c_uint32    ), # terrain/shadowMapSize
        ('blend_map_size',                     c_uint32    ), # terrain/blendMapSize
        ('lod_texture_distance',               c_float     ), # terrain/lodInfo/lodTextureDistance
        ('macro_lod_start',                    c_float     ), # terrain/lodInfo/macroLODStart
        ('unknown_1',                          c_uint32    ),
        ('start_bias',                         c_float     ), # terrain/lodInfo/startBias
        ('end_bias',                           c_float     ), # terrain/lodInfo/endBias
        ('direct_occlusion',                   c_float     ), # terrain/soundOcclusion/directOcclusion
        ('reverb_occlusion',                   c_float     ), # terrain/soundOcclusion/reverbOcclusion
        ('wrap_u',                             c_float     ), # terrain/detailNormal/wrapU
        ('wrap_v',                             c_float     ), # terrain/detailNormal/wrapV
        ('unknown_2',                          c_uint32    ),
        ('unknown_3',                          c_float     ),
        ('unknown_4',                          c_float     ),
        ('unknown_5',                          c_float     ),
        ('blend_macro_influence',              c_float     ), # terrain/blendMacroInfluence
        ('blend_global_threshold',             c_float     ), # terrain/blendGlobalThreshold
        ('blend_height',                       c_float     ), # terrain/blendHeight
        ('disabled_blend_height',              c_float     ), # terrain/disabledBlendHeight
        ('vt_lod_params',                      c_float * 4 ), # terrain/VTLodParams
        ('bounding_box',                       c_float * 4 ),
        ]

    _tests_ = {
        'terrain_version': { '==': 250 },
        'pad1': { '==': 0 },
        'pad2': { '==': 1 },
        'pad3': { '==': 0 },
        'unknown_1': { 'in': (5, 6, 7) },
        'unknown_2': { '==': 0 },
        'unknown_3': { 'in': (0.0, -0.03) },
        'unknown_4': { 'in': (0.0, 0.3) },
        'unknown_5': { 'in': (0.0, 0.2) },
        }


class BWT2_Section_1_6_1(Base_JSON_Section):
    header = 'BWT2'
    int1 = 3

    _fields_ = [
        (dict, 'settings',      TerrainSettings1_v0_9_20 ),
        (list, 'cdatas',        ChunkTerrain_v0_9_12     ),
        (list, '3',             '<i'                     ),
        (dict, 'settings2',     TerrainSettings2_v1_6_1  ),
        (list, 'lod_distances', '<f'                     ), # terrain/lodInfo/lodDistances
        (list, '6',             '<2i'                    ),
        (list, 'cascades',      OutlandCascade_v1_0_0    ), # outland/cascade
        (list, 'tiles_fnv',     '<I'                     ), # outland/tiles
        ]

    def prepare_unp_xml(*args):
        return BWT2_Section_1_4_0.prepare_unp_xml(*args)

    @staticmethod
    def flush_unp_xml(chunks):
        return BWT2_Section_1_4_0.flush_unp_xml(chunks)
