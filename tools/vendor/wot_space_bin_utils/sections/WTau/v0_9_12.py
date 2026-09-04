""" WTau (Audio) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class Audio1(CStructure):
    _size_ = 24

    _fields_ = [
        ('wwevent_name_fnv', c_uint32    ),
        ('event_name_fnv',   c_uint32    ),
        ('max_distance',     c_float     ),
        ('position',         c_float * 3 ),
        ]



class WTau_Section_0_9_12(Base_JSON_Section):
    header = 'WTau'
    int1 = 2

    _fields_ = [
        (list, '1', Audio1 ),
        (list, '2', '<6I'  ),
        ]
