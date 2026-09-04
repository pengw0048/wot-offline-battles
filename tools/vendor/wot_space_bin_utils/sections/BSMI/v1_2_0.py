""" BSMI (Model Instances) """

from ctypes import c_float, c_uint32, c_int32
from xml.etree import ElementTree as ET
from .._base_json_section import *
from .v1_0_0 import ChunkModel_v1_0_0, BSMI_Section_1_0_0


class ModelAnimation_v1_2_0(CStructure):
    _size_ = 32

    _fields_ = [
        ('model_index',   c_uint32     ),
        ('seq_res_fnv',   c_uint32     ),
        ('clip_name_fnv', c_uint32     ), # sequence/clipName
        ('auto_start',    c_uint32, 1  ), # sequence/autoStart
        ('loop',          c_uint32, 1  ), # sequence/loop
        ('pad1',          c_uint32, 14 ),
        ('unknown_1',     c_uint32, 1  ),
        ('unknown_2',     c_uint32, 1  ),
        ('pad2',          c_uint32, 14 ),
        ('loop_count',    c_int32      ), # sequence/loopCount
        ('speed',         c_float      ), # sequence/speed
        ('delay',         c_float      ), # sequence/delay
        ('unknown_3',     c_float      ),
        ]

    _tests_ = {
        'clip_name_fnv': {'==': 2216829733},
        'pad1': {'==': 0},
        'pad2': {'==': 0}
    }


class BSMI_Section_1_2_0(Base_JSON_Section):
    header = 'BSMI'
    int1 = 3

    _fields_ = [
        (list, 'transforms',       '<16f'               ),
        (list, 'chunk_models',     ChunkModel_v1_0_0    ),
        (list, 'visibility_masks', '<I'                 ),
        (list, 'bsmo_models_id',   '<I'                 ),
        (list, 'animations_id',    '<i'                 ),
        (list, 'model_animation',  ModelAnimation_v1_2_0),
        (list, '8_4',              '<I'                 ),
        (list, '9_12',             '<3I'                ), # 0.9.12: WSMI['1_12']
        (list, '10_4',             '<I'                 ),
        (list, '11_20',            '<5f'                ),
        ]

    def model_ids(self):
        return BSMI_Section_1_0_0.model_ids(self)

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

            anim_id = self._data['animations_id'][i]
            if anim_id != -1:
                # sequence
                el = ET.SubElement(el, 'sequence')

                for anim in self._data['model_animation']:
                    if anim['model_index'] == i:
                        break

                write('resource',       chunks.gets(anim['seq_res_fnv']) )
                write('autoStart',      bool(anim['auto_start'])         )
                write('isStateMachine', True                             )

            # TODO: more
