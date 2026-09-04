""" BWT2 (Terrain 2) """

from struct import unpack
from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v0_9_12 import ChunkTerrain_v0_9_12, OctreeConfiguration_v0_9_12
from .v0_9_20 import TerrainSettings1_v0_9_20
from .v1_0_0 import OutlandCascade_v1_0_0, BWT2_Section_1_0_0
from .common import *


class TerrainSettings2_v1_0_1(CStructure):
    _size_ = 136

    _fields_ = [
        ('terrain_version',                    c_uint32    ), # space.settings/terrain/version
        ('blend_map_caching',                  c_uint32,  1), # terrain/blendMapCaching
        ('normal_map_caching',                 c_uint32,  1), # terrain/normalMapCaching
        ('pad1',                               c_uint32,  1),
        ('enable_auto_rebuild_normal_map',     c_uint32,  1), # terrain/editor/enableAutoRebuildNormalMap
        ('pad2',                               c_uint32,  1),
        ('enable_auto_rebuild_water_geometry', c_uint32,  1), # terrain/editor/enableAutoRebuildWaterGeometry
        ('pad3',                               c_uint32, 26),
        ('height_map_editor_size',             c_uint32    ), # terrain/heightMapEditorSize
        ('height_map_size',                    c_uint32    ), # terrain/heightMapSize
        ('normal_map_size',                    c_uint32    ), # terrain/normalMapSize
        ('hole_map_size',                      c_uint32    ), # terrain/holeMapSize
        ('shadow_map_size',                    c_uint32    ), # terrain/shadowMapSize
        ('blend_map_size',                     c_uint32    ), # terrain/blendMapSize
        ('unknown8',                           c_uint32    ),
        ('unknown9',                           c_uint32    ),
        ('lod_texture_distance',               c_float     ), # terrain/lodInfo/lodTextureDistance
        ('macro_lod_start',                    c_float     ), # terrain/lodInfo/macroLODStart
        ('unknown12',                          c_uint32    ),
        ('start_bias',                         c_float     ), # terrain/lodInfo/startBias
        ('end_bias',                           c_float     ), # terrain/lodInfo/endBias
        ('unknown15',                          c_uint32    ),
        ('detail_height_map_distance',         c_float     ), # terrain/lodInfo/detailHeightMapDistance
        ('direct_occlusion',                   c_float     ), # terrain/soundOcclusion/directOcclusion
        ('reverb_occlusion',                   c_float     ), # terrain/soundOcclusion/reverbOcclusion
        ('wrap_u',                             c_float     ), # terrain/detailNormal/wrapU
        ('wrap_v',                             c_float     ), # terrain/detailNormal/wrapV
        ('unknown21',                          c_uint32    ),
        ('tess_zoom_threshold',                c_float     ), # terrain/tessZoomThreshold
        ('tess_zoom_scale',                    c_float     ), # terrain/tessZoomScale
        ('blend_macro_influence',              c_float     ), # terrain/blendMacroInfluence
        ('blend_global_threshold',             c_float     ), # terrain/blendGlobalThreshold
        ('vt_lod_params',                      c_float * 4 ),
        ('bounding_box',                       c_float * 4 ),
        ]

    _tests_ = {
        'terrain_version': { '==': 250 },
        'pad1': { '==': 0 },
        'pad2': { '==': 1 },
        'pad3': { '==': 0 },
        'unknown8': { '==': 0 },
        'unknown9': { '==': 0 },
        'unknown12': { '==': 5 },
        'unknown15': { '==': 0 },
        'unknown21': { '==': 0 },
        }


class BWT2_Section_1_0_1(Base_JSON_Section):
    header = 'BWT2'
    int1 = 3

    _fields_ = [
        (dict, 'settings',             TerrainSettings1_v0_9_20    ),
        (list, 'cdatas',               ChunkTerrain_v0_9_12        ),
        (list, '3',                    '<i'                        ),
        (dict, 'settings2',            TerrainSettings2_v1_0_1     ),
        (list, 'lod_distances',        '<f'                        ), # terrain/lodInfo/lodDistances
        (list, '6',                    '<2i'                       ),
        (list, 'cascades',             OutlandCascade_v1_0_0       ), # outland/cascade
        (list, 'tiles_fnv',            '<I'                        ), # outland/tiles
        (dict, 'octree_configuration', OctreeConfiguration_v0_9_12 ),
        (list, 'node_bounds',          '<6f'                       ),
        (list, 'node_center',          '<3f'                       ),
        (list, 'node_children',        '<16H'                      ),
        (list, 'node_data_reference',  '<I'                        ),
        (list, 'node_parents',         '<I'                        ),
        (list, 'node_content_spans',   '<2H'                       ),
        (list, 'node_contents',        '<I'                        ),
        ]

    def prepare_unp_xml(self, gchunk, settings, in_dir: Path, out_dir: Path, secs):
        from zipfile import ZipFile
        from io import BytesIO

        s1 = self._data['settings']
        s2 = self._data['settings2']

        chunks = Chunks(gchunk, secs, s1['chunk_size'])

        for chunk in self._data['cdatas']:
            name = chunks.add_chunk(chunk, out_dir)

            cdata_path = (in_dir / f'{name}.cdata_processed')
            if not cdata_path.is_file():
                continue

            out_path = (out_dir / f'{name}.cdata')
            if not out_path.parent.is_dir():
                # dirty hack!!!
                out_path.parent.mkdir(parents=True)

            def cp(aname):
                try:zw.writestr(aname, zr.read(aname))
                except Exception:pass

            with ZipFile(cdata_path, 'r') as zr:
                with ZipFile(out_path, 'w') as zw:
                    cp('dirtyflags')
                    cp('navmeshdirty')
                    cp('terrain2/dominanttextures')
                    cp('terrain2/horizonshadows')
                    cp('terrain2/lodnormals')
                    cp('terrain2/normals')
                    cp('terrain2/heights') # ???
                    cp('terrain2/heights1') # ???
                    cp('terrain2/holes')

                    # blend_textures
                    data = BytesIO(zr.read('terrain2/blend_textures'))
                    assert(data.read(4) == b'bwb\0')

                    cnt = unpack('<I', data.read(4))[0]
                    assert(cnt <= 4)

                    lengths = unpack('<4I', data.read(16))
                    for i in range(cnt):
                        aname = 'terrain2/blend_texture %s' % (i + 1)
                        zw.writestr(aname, data.read(lengths[i]))

                    # layers
                    data = BytesIO(zr.read('terrain2/layers'))
                    assert(data.read(4) == b'blb\0')

                    cnt = unpack('<I', data.read(4))[0]
                    assert(cnt <= 8)

                    lengths = unpack('<8I', data.read(32))
                    for i in range(cnt):
                        aname = 'terrain2/layer %s' % (i + 1)
                        zw.writestr(aname, data.read(lengths[i]))

        terrainEl = ET.SubElement(settings, 'terrain')

        write = lambda *args: self._add2xml(el, *args)

        el = terrainEl

        write('version',              s2['terrain_version']                )
        write('blendMapCaching',      bool(s2['blend_map_caching'])        )
        write('normalMapCaching',     bool(s2['normal_map_caching'])       )
        #write('heightMapEditorSize',  s2['height_map_editor_size']         )
        write('heightMapSize',        s2['height_map_size']                )
        write('normalMapSize',        s2['normal_map_size']                )
        write('holeMapSize',          s2['hole_map_size']                  )
        write('shadowMapSize',        s2['shadow_map_size']                )
        write('blendMapSize',         s2['blend_map_size']                 )

        # for WoT 1.6.1+:
        write('tessZoomThreshold',    s2.get('tess_zoom_threshold', 0.05 ) ) # 0.05 from WoT 1.6.1 hangar_v3 space.settings
        write('tessZoomScale',        s2.get('tess_zoom_scale',     0.3  ) ) # 0.3 from WoT 1.6.1 hangar_v3 space.settings

        write('blendMacroInfluence',  s2['blend_macro_influence']          )
        write('blendGlobalThreshold', s2['blend_global_threshold']         )

        # for WoT 1.4+:
        write('blendHeight',             s2.get('blend_height',          0.3  ) ) # 0.3   from WoT 1.4 hangar_v3 space.settings
        write('disabledBlendHeight',     s2.get('disabled_blend_height', 0.05 ) ) # 0.05  from WoT 1.4 hangar_v3 space.settings

        write('VTLodParams',          s2['vt_lod_params']                  )
        write('globalMap',            chunks.gets(s1['global_map_fnv'])    )
        write('noiseTexture',         chunks.gets(s1['noise_texture_fnv']) )

        # lodInfo
        el = ET.SubElement(terrainEl, 'lodInfo')

        write('lodTextureDistance',      s2['lod_texture_distance']       )
        write('macroLODStart',           s2['macro_lod_start']            )
        write('startBias',               s2['start_bias']                 )
        write('endBias',                 s2['end_bias']                   )

        # for WoT 1.4+:
        write('detailHeightMapDistance', s2.get('detail_height_map_distance', 500.0 ) ) # 500.0 from WoT 1.4 hangar_v3 space.settings

        # lodDistances
        lod_distances = self._data['lod_distances']

        if lod_distances:
            el = ET.SubElement(el, 'lodDistances')
            for i, distance in enumerate(lod_distances):
                write('distance%d' % i, distance)

        # detailNormal
        el = ET.SubElement(terrainEl, 'detailNormal')

        write('normalMap', chunks.gets(s1['normal_map_fnv']) )
        write('wrapU',     s2['wrap_u']                      )
        write('wrapV',     s2['wrap_v']                      )

        # soundOcclusion
        el = ET.SubElement(terrainEl, 'soundOcclusion')

        write('directOcclusion', s2['direct_occlusion'] )
        write('reverbOcclusion', s2['reverb_occlusion'] )

        # outland
        outlandEl = ET.SubElement(terrainEl, 'outland')

        # outland/cascade
        cascades = self._data['cascades']

        for cascade in cascades:
            el = ET.SubElement(outlandEl, 'cascade')

            write('heightMap', chunks.gets(cascade['height_map_fnv']) )
            write('normalMap', chunks.gets(cascade['normal_map_fnv']) )
            write('tileMap',   chunks.gets(cascade['tile_map_fnv'])   )
            write('tileScale', cascade['tile_scale']                  )

            # extent
            el = ET.SubElement(el, 'extent')

            write('min', cascade['extent_min'] )
            write('max', cascade['extent_max'] )

        # outland/tiles
        tiles = self._data['tiles_fnv']

        if tiles:
            el = ET.SubElement(outlandEl, 'tiles')

            for tile in tiles:
                write('tile', chunks.gets(tile))

        return chunks

    @staticmethod
    def flush_unp_xml(chunks):
        return BWT2_Section_1_0_0.flush_unp_xml(chunks)
