""" SpTr (SpeedTree) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *


class SpTrInfo_0_9_12(CStructure):
    _size_ = 76

    _fields_ = [
        ('transform',                c_float * 16 ),
        ('spt_fnv',                  c_uint32     ),
        ('seed',                     c_uint32     ),
        ('casts_shadow',             c_uint32, 1  ),
        ('reflection_visible',       c_uint32, 1  ),
        ('casts_local_shadow',       c_uint32, 1  ),
        ('editor_only_casts_shadow', c_uint32, 1  ), # editorOnly/castsShadow
        ('pad',                      c_uint32, 28 ),
        ]

    _tests_ = {
        'pad': { '==': 0 }
        }


class SpTr_Section_0_9_12(Base_JSON_Section):
    header = 'SpTr'
    int1 = 2

    _fields_ = [
        (list, 'speedtree_list', SpTrInfo_0_9_12 ),
        (dict, 'info',           '<6f'           ),
        ]

    def to_xml(self, chunks):
        write = lambda *args: self._add2xml(el, *args)

        for it in self._data['speedtree_list']:
            chunk, transform = chunks.get_by_transform(it['transform'])

            el = ET.SubElement(chunk, 'model')

            write('transform', transform)
            write('resource', chunks.gets(it['spt_fnv']).replace('.spt', '.model'))
