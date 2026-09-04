""" BSMI (Model Instances) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *
from .v0_9_12 import ModelAnimation
from .v0_9_20 import BSMI_Section_0_9_20


class ChunkModel_v1_0_0(CStructure):
    '''
    data from <model> section of .chunk file
    '''
    _size_ = 8

    _fields_ = [
        ('casts_shadow',                 c_uint32, 1  ), # model/castsShadow
        ('pad1',                         c_uint32, 1  ),
        ('casts_local_shadow',           c_uint32, 1  ), # model/castsLocalShadow
        ('not_ignores_objects_farplane', c_uint32, 1  ), # negative model/ignoresObjectsFarplane
        ('always_dynamic',               c_uint32, 1  ), # model/alwaysDynamic
        ('unknown1',                     c_uint32, 1  ),
        ('has_animations',               c_uint32, 1  ), # if .model/animation sections
        ('pad2',                         c_uint32, 25 ),
        ('unknown',                      c_uint32     ),
        ]

    _tests_ = {
        # ...
        #'pad1': { '==': 0 },
        # ...
        #'pad2': { '==': 0 },
        'unknown': { 'in': (0, 1) },
        }


class SequencesInfo_v1_0_0(CStructure):
    _size_ = 28

    _fields_ = [
        ('i1',           c_uint32 ),
        ('resource_fnv', c_uint32 ),
        ('loop_count',   c_float  ), # loopCount
        ('i4',           c_uint32 ),
        ('speed',        c_float  ), # speed
        ('delay',        c_float  ), # delay
        ('i7',           c_uint32 ),
        ]


class BSMI_Section_1_0_0(Base_JSON_Section):
    header = 'BSMI'
    int1 = 3

    _fields_ = [
        (list, 'transforms',       '<16f'               ),
        (list, 'chunk_models',     ChunkModel_v1_0_0    ),
        (list, 'visibility_masks', '<I'                 ),
        (list, 'bsmo_models_id',   '<I'                 ),
        (list, 'animations_id',    '<i'                 ),
        (list, 'model_animation',  ModelAnimation       ),
        (list, 'sequences',        SequencesInfo_v1_0_0 ),
        (list, '8_4',              '<I'                 ),
        (list, '9_12',             '<3I'                ), # 0.9.12: WSMI['1_12']
        (list, '10_4',             '<I'                 ),
        (list, '11_20',            '<5f'                ),
        ]

    def model_ids(self):
        return BSMI_Section_0_9_20.model_ids(self)

    def to_xml(self, chunks):
        bsmo = chunks.secs['BSMO']

        for i, model_id in enumerate(self.model_ids()):
            # very bad solution
            path = chunks.gets(bsmo._data['models_colliders'][model_id]['bsp_section_name_fnv'])

            if not path:
                return

            path = path.replace('.primitives', '.model')

            chunk, transform = chunks.get_by_transform(self._data['transforms'][i])

            el = ET.SubElement(chunk, 'model')
            write = lambda *args: self._add2xml(el, *args)

            mdl = self._data['chunk_models'][i]

            write('transform',              transform                                     )
            write('visibilityMask',         self._data['visibility_masks'][i]             )
            write('resource',               path                                          )
            write('castsShadow',            bool(mdl['casts_shadow'])                     )
            write('alwaysDynamic',          bool(mdl['always_dynamic'])                   )
            write('ignoresObjectsFarplane', not bool(mdl['not_ignores_objects_farplane']) )
            # TODO: more
