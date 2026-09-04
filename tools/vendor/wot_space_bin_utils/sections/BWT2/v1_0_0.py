""" BWT2 (Terrain 2) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v0_9_12 import ChunkTerrain_v0_9_12, OctreeConfiguration_v0_9_12
from .v0_9_20 import TerrainSettings1_v0_9_20, BWT2_Section_0_9_20
from .common import *


class OutlandCascade_v1_0_0(CStructure):
    _size_ = 40

    _fields_ = [
        ('extent_min',     c_float * 3 ),
        ('extent_max',     c_float * 3 ),
        ('height_map_fnv', c_uint32    ),
        ('normal_map_fnv', c_uint32    ),
        ('tile_map_fnv',   c_uint32    ),
        ('tile_scale',     c_float     ),
        ]


class BWT2_Section_1_0_0(Base_JSON_Section):
    header = 'BWT2'
    int1 = 3

    _fields_ = [
        (dict, 'settings',             TerrainSettings1_v0_9_20    ),
        (list, 'cdatas',               ChunkTerrain_v0_9_12        ),
        (list, '3',                    '<i'                        ),
        (dict, 'settings2',            '<35I'                      ),
        (list, 'lod_distances',        '<f'                        ),
        (list, '6',                    '<2i'                       ),
        (list, 'cascades',             OutlandCascade_v1_0_0       ), # outland/cascade
        (list, 'tiles_fnv',            '<I'                        ), # outland/tiles
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
        return BWT2_Section_0_9_20.flush_unp_xml(chunks)
