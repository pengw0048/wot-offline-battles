""" BWLC (Lights) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v1_6_0 import BWLC_Section_1_6_0



class PulseLight_v1_7_0(CStructure):
    _size_ = 100

    _fields_ = [
        ('position',       c_float * 3  ),
        ('inner_radius',   c_float      ),
        ('outer_radius',   c_float      ),
        ('lod_shift',      c_float      ), # lodShift
        ('colour',         c_float * 4  ),
        ('unknown',        c_uint32     ),
        ('multiplier',     c_float      ),
        ('cast_shadows',   c_uint32, 1  ),
        ('pad',            c_uint32, 31 ),
        ('frame_start_id', c_uint32     ),
        ('frame_num',      c_uint32     ),
        ('unknown_2',      c_float      ), # c_float ?
        ('duration',       c_float      ),
        ('unknown_3',      c_uint32     ),
        ('unknown_4',      c_float      ),
        ('unknown_5',      c_uint32     ),
        ('unknown_6',      c_uint32     ),
        ('unknown_7',      c_uint32     ),
        ('unknown_8',      c_float      ),
        ('unknown_9',      c_float      ),
        ('unknown_10',     c_uint32     ),
        ]

    _tests_ = {
        # ...
        'inner_radius': { '<=': 110.0, '>=': 0.0 },
        'outer_radius': { '<=': 161.0, '>=': 0.0 },
        'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
        'unknown': { '==': 0 },
        'multiplier': { '>=': 0.0 },
        # ...
        'pad': { '==': 0 },
        # ...
        'unknown_2': { '==': 0.0001 },
        'duration': { '>=': 0.0 },
        'unknown_3': { 'in': (0, 0xFFFFFFFF) },
        'unknown_6': { 'in': (0, 0xFFFFFFFF) },
        # ...
        'unknown_8': { 'in': (0.0, -0.999999, -1.0) },
        }



class PulseSpotLight_v1_7_0(CStructure):
    _size_ = 100

    _fields_ = [
        ('position',       c_float * 3  ),
        ('direction',      c_float * 3  ),
        ('inner_radius',   c_float      ),
        ('outer_radius',   c_float      ),
        ('lod_shift',      c_float      ), # lodShift
        ('cone_angle',     c_float      ),
        ('colour',         c_float * 4  ),
        ('unknown',        c_float      ),
        ('multiplier',     c_float      ),
        ('cast_shadows',   c_uint32, 1  ),
        ('pad1',           c_uint32, 7  ),
        ('unknown_bit_1',  c_uint32, 1  ),
        ('unknown_bit_2',  c_uint32, 1  ),
        ('unknown_bit_3',  c_uint32, 1  ),
        ('pad2',           c_uint32, 21 ),
        ('frame_start_id', c_uint32     ),
        ('frame_num',      c_uint32     ),
        ('unknown_2',      c_float      ), # c_float ?
        ('duration',       c_float      ),
        ('unknown_3',      c_uint32     ),
        ('unknown_4',      c_float      ),
        ('unknown_5',      c_float      ),
        ('unknown_6',      c_uint32     ),
        ]

    _tests_ = {
        # ...
        'direction': { '<=': (1.0, 1.0, 1.0), '>=': (-1.0, -1.0, -1.0) },
        'inner_radius': { '<=': 110.0, '>=': 0.0 },
        'outer_radius': { '<=': 161.0, '>=': 0.0 },
        'cone_angle': { '<=': 1.58, '>=': 0.0 },
        'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
        'unknown': { '==': 0.0 },
        'multiplier': { '>=': 0.0 },
        # ...
        'pad1': { '==': 0 },
        'unknown_bit_1': { '==': 0 },
        'unknown_bit_2': { '==': 0 },
        'unknown_bit_3': { '==': 0 },
        'pad2': { '==': 0 },
        # ...
        'unknown_2': { 'in': (0.0001, 9.1e-05, 8.8e-05) },
        'duration': { '>=': 0.0 },
        'unknown_3': { 'in': (0, 2) },
        'unknown_5': { 'in': (0.0, 1.0) },
        'unknown_6': { 'in': (0, 0xFFFFFFFF) },
        }



class BWLC_Section_1_7_0(Base_JSON_Section):
    header = 'BWLC'
    int1 = 2

    _fields_ = [
        (list, 'pulse_light_list',      PulseLight_v1_7_0     ),
        (list, 'pulse_spot_light_list', PulseSpotLight_v1_7_0 ),
        (list, 'frames',                '<2f'                 ),
        ]

    def to_xml(self, chunks):
        # TODO (untested):
        BWLC_Section_1_6_0.to_xml(self, chunks)

    @classmethod
    def _omniLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_6_0._omniLight_to_xml(chunk, item)

    @classmethod
    def _pulseLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_6_0._pulseLight_to_xml(chunk, item, frames)

    @classmethod
    def _spotLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_6_0._spotLight_to_xml(chunk, item)

    @classmethod
    def _pulseSpotLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_6_0._pulseSpotLight_to_xml(chunk, item, frames)
