""" BWT2 (Terrain 2) """

from ctypes import c_float, c_uint16, c_int16, c_uint32, c_int32
from pathlib import Path
from .._base_json_section import *
from .common import *


class TerrainSettings1_v0_9_12(CStructure):
    _size_ = 20

    _fields_ = [
        ('chunk_size', c_float     ),
        ('bounds',     c_int32 * 4 ),
        ]

    _tests_ = {
        'chunk_size': { '==': 100.0 },
        # ...
        }



class ChunkTerrain_v0_9_12(CStructure):
    _size_ = 8

    _fields_ = [
        ('resource_fnv', c_uint32 ),
        ('loc_x',        c_int16  ),
        ('loc_y',        c_int16  ),
        ]


class OctreeConfiguration_v0_9_12(CStructure):
    _size_ = 24

    _fields_ = [
        ('max_depth',        c_uint32    ), # Maximum depth of the tree. All data is referenced from maxDepth nodes
        ('world_center',     c_float * 3 ),
        ('world_size',       c_float     ),
        ('num_data_entries', c_uint16    ),
        ('pad',              c_uint16    ),
        ]

    _tests_ = {
        'pad': { '==': 0 },
        }


class BWT2_Section_0_9_12(Base_JSON_Section):
    header = 'BWT2'
    int1 = 1

    _fields_ = [
        (dict, 'settings',             TerrainSettings1_v0_9_12    ),
        (list, 'cdatas',               ChunkTerrain_v0_9_12        ),
        (list, '3',                    '<i'                        ),
        (dict, 'octree_configuration', OctreeConfiguration_v0_9_12 ),
        (list, 'node_bounds',          '<6f'                       ), # Bounding boxes of all nodes
        (list, 'node_center',          '<3f'                       ), # Center of bounding boxes for all nodes
        (list, 'node_children',        '<8H'                       ), # The array of actual nodes. Each node contains indices to it's eight children
        (list, 'node_data_reference',  '<I'                        ), # Index into the array of data references for each node
        (list, 'node_parents',         '<H'                        ), # An array of parent indices for each node
        (list, 'node_content_spans',   '<2H'                       ), # This array of DataSpan defines, for each NodeDataReference, the first index and the last index of the objects in the Content array which make up the contents of this node.
        (list, 'node_contents',        '<I'                        ), # Array of "handles" to actual scene content
        ]

    def prepare_unp_xml(self, gchunk, settings, in_dir, out_dir: Path, secs):
        s1 = self._data['settings']
        chunks = Chunks(gchunk, secs, s1['chunk_size'])

        for chunk in self._data['cdatas']:
            chunks.add_chunk(chunk, out_dir)

        return chunks

    @staticmethod
    def flush_unp_xml(chunks):
        from xml.dom import minidom

        for name, path in chunks.name_to_path.items():
            if not path.parent.is_dir():
                # dirty hack!!!
                path.parent.mkdir(parents=True)
            with path.open('w') as f:
                reparsed = minidom.parseString(ET.tostring(chunks.name_to_tree[name]))
                f.write(reparsed.toprettyxml())
