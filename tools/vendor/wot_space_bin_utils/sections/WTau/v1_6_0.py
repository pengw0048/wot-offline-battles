""" WTau (Audio) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class Audio1_v1_6_0(CStructure):
    _size_ = 32

    _fields_ = [
        ('wwevent_name_fnv', c_uint32    ),
        ('event_name_fnv',   c_uint32    ),
        ('max_distance',     c_float     ),
        ('position',         c_float * 3 ),
        ('unknown_1',        c_uint32    ),
        ('unknown_2',        c_uint32    ),
        ]

    _tests_ = {
        'unknown_1': { '==': 0 },
        'unknown_2': { '==': 0xFFFFFFFF },
        }



class WTau_Section_1_6_0(Base_JSON_Section):
    header = 'WTau'
    int1 = 3

    _fields_ = [
        (list, '1', Audio1_v1_6_0 ),
        (list, '2', '<6I'         ),
        ]
