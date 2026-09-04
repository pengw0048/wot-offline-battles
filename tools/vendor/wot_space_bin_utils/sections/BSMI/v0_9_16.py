""" BSMI (Model Instances) """

from .._base_json_section import *
from xml.etree import ElementTree as ET
from .v0_9_12 import ChunkModel, ModelAnimation, BSMI_Section_0_9_12


class BSMI_Section_0_9_16(Base_JSON_Section):
    header = 'BSMI'
    int1 = 1

    _fields_ = [
        (list, 'transforms',       '<16f'         ),
        (list, 'chunk_models',     ChunkModel     ),
        (list, 'bsmo_models_id',   '<I'           ),
        (list, 'visibility_masks', '<I'           ), # 0.9.12: BWSV (visibilityMask)
        (list, 'model_animation',  ModelAnimation ),
        (list, 'animations_id',    '<i'           ),
        (list, '7_12',             '<3I'          ), # 0.9.12: WSMI['1_12']
        (list, '8_4',              '<I'           ),
        (list, '9_20',             '<5f'          ),
        ]

    def model_ids(self):
        return BSMI_Section_0_9_12.model_ids(self)

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
            # TODO: more
