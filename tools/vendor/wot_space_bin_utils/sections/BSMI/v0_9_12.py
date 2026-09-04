""" BSMI (Model Instances) """

from ctypes import c_float, c_uint32, c_int32
from xml.etree import ElementTree as ET
from .._base_json_section import *


class ChunkModel(CStructure):
    '''
    data from <model> section of .chunk file
    '''
    _size_ = 8

    _fields_ = [
        ('casts_shadow',                 c_uint32, 1  ), # model/castsShadow
        ('reflection_visible',           c_uint32, 1  ), # model/reflectionVisible
        ('pad1',                         c_uint32, 1  ),
        ('shadow_proxy',                 c_uint32, 1  ), # model/shadowProxy
        ('casts_local_shadow',           c_uint32, 1  ), # model/castsLocalShadow
        ('not_ignores_objects_farplane', c_uint32, 1  ), # negative model/ignoresObjectsFarplane
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


class ModelAnimation(CStructure):
    '''
    data from .model file
    '''
    _size_ = 16

    _fields_ = [
        ('model_index',           c_uint32 ),
        ('animation_id',          c_int32  ), # index .model/animation from .chunk/model/animation/name
        ('unknown',               c_uint32 ),
        ('frame_rate_multiplier', c_float  ), # .chunk/model/animation/frameRateMultiplier
        ]

    _tests_ = {
        # ...
        'animation_id': { '>=': -1 },
        'unknown': { '==': 0 },
        'frame_rate_multiplier': { '>=': 0.0 },
        }


class BSMI_Section_0_9_12(Base_JSON_Section):
    header = 'BSMI'
    int1 = 1

    _fields_ = [
        (list, 'transforms',       '<16f'         ),
        (list, 'chunk_models',     ChunkModel     ),
        (list, 'bsmo_models_id',   '<I'           ),
        (list, 'animations_id',    '<i'           ),
        (list, 'model_animation',  ModelAnimation ),
        (list, '6_4',              '<I'           ),
        ]

    def model_ids(self):
        for model_id in self._data['bsmo_models_id']:
            yield model_id

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

            # mdl = self._data['chunk_models'][i]

            write('transform',              transform                                     )
            # write('visibilityMask',         self._data['visibility_masks'][i]             )
            write('resource',               path                                          )
            # TODO: more
