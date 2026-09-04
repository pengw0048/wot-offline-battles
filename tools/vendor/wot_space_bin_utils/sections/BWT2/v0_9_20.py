""" BWT2 (Terrain 2) """

from ctypes import c_float, c_uint32, c_int32
from .._base_json_section import *
from .v0_9_12 import ChunkTerrain_v0_9_12, OctreeConfiguration_v0_9_12
from .v0_9_14 import BWT2_Section_0_9_14
from .common import *


class TerrainSettings1_v0_9_20(CStructure):
    _size_ = 32

    _fields_ = [
        ('chunk_size',     c_float        ), # space.settings/chunkSize or 100.0 by default
        # bounds/minX
        # bounds/maxX
        # bounds/minY
        # bounds/maxY
        ('bounds',            c_int32 * 4 ), # space.settings/bounds
        ('normal_map_fnv',    c_uint32    ),
        ('global_map_fnv',    c_uint32    ), # global_AM.dds, maybe tintTexture - global terrain albedo map
        ('noise_texture_fnv', c_uint32    ), # noiseTexture
        ]

    _tests_ = {
        'chunk_size': { '==': 100.0 },
        # ...
        'noise_texture_fnv': { '==': 2216829733 },
        }


class TerrainSettings2_v0_9_20(CStructure):
    _size_ = 148

    _fields_ = [
        ('terrain_version',  c_uint32    ), # space.settings/terrain/version
        ('_2',               c_uint32    ),
        ('lod_map_size',     c_uint32    ), # space.settings/terrain/lodMapSize
        ('_4',               c_uint32    ), # maybe aoMapSize/normalMapSize
        ('_5',               c_uint32    ), # maybe normalMapSize
        ('_6',               c_uint32    ), # maybe heightMapSize
        ('_7',               c_uint32    ), # maybe aoMapSize/normalMapSize
        ('_8',               c_uint32    ), # maybe holeMapSize
        ('_9',               c_uint32    ), # maybe shadowMapSize
        ('_10',              c_uint32    ), # maybe blendMapSize
        ('_11',              c_float     ),
        ('_12',              c_float     ),
        ('_13',              c_float     ),
        ('_14',              c_float     ),
        ('_15',              c_float     ), # maybe lodTextureStart/bumpFadingStart
        ('_16',              c_float     ), # maybe lodTextureDistance/bumpFadingDistance
        ('_17',              c_float     ), # maybe lodTextureStart/bumpFadingStart
        ('_18',              c_float     ), # maybe lodTextureDistance/bumpFadingDistance
        ('_19',              c_float     ), # maybe space.settings/terrain/lodInfo/blendPreloadDistance
        ('_20',              c_float     ),
        ('bbox_bottom_left', c_float * 2 ), # scripts/arena_defs/*.xml/boundingBox/bottomLeft
        ('bbox_upper_right', c_float * 2 ), # scripts/arena_defs/*.xml/boundingBox/upperRight
        ('_23',              c_float     ),
        ('_24',              c_float     ),
        ('_25',              c_float     ), # maybe space.settings/terrain/borderline/attenuationDistance
        ('_26',              c_uint32    ),
        ('_27',              c_float     ), # maybe space.settings/terrain/lodInfo/startBias
        ('_28',              c_float     ), # maybe space.settings/terrain/lodInfo/endBias
        ('_29',              c_float     ),
        ('_30',              c_float     ), # maybe space.settings/terrain/lodInfo/detailHeightMapDistance
        ('_31',              c_float     ),
        ('_32',              c_float     ),
        ('_33',              c_float     ), # maybe space.settings/terrain/detailNormal/wrapU
        ('_34',              c_float     ), # maybe space.settings/terrain/detailNormal/wrapV
        ('_35',              c_float     ),
        ]

    _tests_ = {
        'terrain_version': { '==': 200 },
        '_2': { 'in': (19, 27, 31) }, # ??? 19(101_dday)/27/31(112_eiffel_tower_ctf)
        # ...
        }


class BWT2_Section_0_9_20(Base_JSON_Section):
    header = 'BWT2'
    int1 = 2

    _fields_ = [
        (dict, 'settings',             TerrainSettings1_v0_9_20    ),
        (list, 'cdatas',               ChunkTerrain_v0_9_12        ),
        (list, '3',                    '<i'                        ),
        (dict, 'settings2',            TerrainSettings2_v0_9_20    ),
        (list, 'lod_distances',        '<f'                        ), # space.settings/terrain/lodInfo/lodDistances
        (list, '6',                    '<2i'                       ),
        (dict, 'octree_configuration', OctreeConfiguration_v0_9_12 ),
        (list, 'node_bounds',          '<6f'                       ),
        (list, 'node_center',          '<3f'                       ),
        (list, 'node_children',        '<8H'                       ),
        (list, 'node_data_reference',  '<I'                        ),
        (list, 'node_parents',         '<H'                        ),
        (list, 'node_content_spans',   '<2H'                       ),
        (list, 'node_contents',        '<I'                        ),
        ]

    def prepare_unp_xml(self, gchunk, settings, in_dir, out_dir, secs):
        s1 = self._data['settings']
        chunks = Chunks(gchunk, secs, s1['chunk_size'])

        for chunk in self._data['cdatas']:
            chunks.add_chunk(chunk, out_dir)

        return chunks

    @staticmethod
    def flush_unp_xml(chunks):
        return BWT2_Section_0_9_14.flush_unp_xml(chunks)
