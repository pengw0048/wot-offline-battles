""" BSMI (Model Instances) """

from .._base_json_section import *
from .v0_9_12 import ChunkModel, ModelAnimation
from .v0_9_16 import BSMI_Section_0_9_16


class BSMI_Section_0_9_20(Base_JSON_Section):
    header = 'BSMI'
    int1 = 2

    _fields_ = [
        (list, 'transforms',       '<16f'         ),
        (list, 'chunk_models',     ChunkModel     ),
        (list, 'visibility_masks', '<I'           ), # 0.9.12: BWSV (visibilityMask)
        (list, 'bsmo_models_id',   '<I'           ),
        (list, 'animations_id',    '<i'           ),
        (list, 'model_animation',  ModelAnimation ),
        (list, '7_4',              '<I'           ),
        (list, '8_12',             '<3I'          ), # 0.9.12: WSMI['1_12']
        (list, '9_4',              '<I'           ),
        (list, '10_20',            '<5f'          ),
        ]

    def model_ids(self):
        return BSMI_Section_0_9_16.model_ids(self)

    def to_xml(self, chunks):
        return BSMI_Section_0_9_16.to_xml(self, chunks)
