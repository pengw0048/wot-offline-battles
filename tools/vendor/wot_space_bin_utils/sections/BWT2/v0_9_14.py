""" BWT2 (Terrain 2) """

from ctypes import c_float, c_uint8, c_uint32, c_int32
from .._base_json_section import *
from .v0_9_12 import ChunkTerrain_v0_9_12, OctreeConfiguration_v0_9_12, BWT2_Section_0_9_12
from .common import *


class TerrainSettings1_v0_9_14(CStructure):
    _size_ = 32

    _fields_ = [
        ('chunk_size',     c_float     ), # space.settings/chunkSize or 100.0 by default
        ('bounds',         c_int32 * 4 ), # space.settings/bounds
        ('normal_map_fnv', c_uint32    ),
        ('unknown_1_fnv',  c_uint32    ), # global_AM.dds, maybe tintTexture
        ('unknown_2_fnv',  c_uint32    ), # maybe noiseTexture
        ]

    _tests_ = {
        'chunk_size': { '==': 100.0 },
        # ...
        #'unknown_1_fnv': { '==': 2216829733 },
        'unknown_2_fnv': { '==': 2216829733 },
        }


class TerrainSettings2_v0_9_14(CStructure):
    _size_ = 156

    _fields_ = [
        ('terrain_version',  c_uint32    ), # space.settings/terrain/version
        ('other',            c_uint32*38 ),
        ]

    _tests_ = {
        'terrain_version': { '==': 200 },
        }


class BWT2_Section_0_9_14(Base_JSON_Section):
    header = 'BWT2'
    int1 = 2

    _fields_ = [
        (dict, 'settings',             TerrainSettings1_v0_9_14    ),
        (list, 'cdatas',               ChunkTerrain_v0_9_12        ),
        (list, '3',                    '<i'                        ),
        (dict, 'settings2',            TerrainSettings2_v0_9_14    ),
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
        return BWT2_Section_0_9_12.flush_unp_xml(chunks)
