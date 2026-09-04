""" WTau (Audio) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class Audio1_v1_5_1(CStructure):
    _size_ = 28

    _fields_ = [
        ('wwevent_name_fnv', c_uint32    ),
        ('event_name_fnv',   c_uint32    ),
        ('max_distance',     c_float     ),
        ('position',         c_float * 3 ),
        ('unknown_1',        c_uint32    ),
        ]

    _tests_ = {
        'unknown_1': { '==': 0 },
        }



class WTau_Section_1_5_1(Base_JSON_Section):
    header = 'WTau'
    int1 = 2

    _fields_ = [
        (list, '1', Audio1_v1_5_1 ),
        (list, '2', '<6I'         ),
        ]
