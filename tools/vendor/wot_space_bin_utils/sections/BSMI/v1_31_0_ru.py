""" BSMI (Model Instances) """

from struct import unpack, pack
from .._base_json_section import *
from .v1_0_0 import ChunkModel_v1_0_0
from .v1_16_1 import ModelAnimation_v1_16_1, BSMI_Section_1_16_1


class BSMI_Section_1_31_0_RU(Base_JSON_Section):
    header = 'BSMI'
    int1 = 3

    _fields_ = [
        (list, 'transforms',       '<16f'                ),
        (list, 'chunk_models',     ChunkModel_v1_0_0     ),
        (list, 'visibility_masks', '<I'                  ),
        (list, 'bsmo_models_id',   '<2I'                 ),
        (list, 'animations_id',    '<i'                  ),
        (list, 'model_animation',  ModelAnimation_v1_16_1),
        (list, '8_40',             '<10I'                ),
        (list, '9_4',              '<I'                  ),
        (list, '10_12',            '<3I'                 ), # 0.9.12: WSMI['1_12']
        (list, '11_4',             '<I'                  ),
        (list, '12_20',            '<5f'                 ),
        (int,  '13_4',             '<I'                  ), # always 0
        (list, '14_4',             '<I'                  ),
        ]

    def model_ids(self):
        return BSMI_Section_1_16_1.model_ids(self)

    def to_xml(self, chunks):
        return BSMI_Section_1_16_1.to_xml(self, chunks)
