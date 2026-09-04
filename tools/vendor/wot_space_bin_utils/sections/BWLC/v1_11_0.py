""" BWLC (Lights) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v1_7_0 import BWLC_Section_1_7_0



class PulseLight_v1_11_0(CStructure):
    _size_ = 104

    _fields_ = [
        ('position',             c_float * 3  ),
        ('inner_radius',         c_float      ),
        ('outer_radius',         c_float      ),
        ('lod_shift',            c_float      ), # lodShift
        ('colour',               c_float * 4  ),
        ('unknown',              c_uint32     ),
        ('multiplier',           c_float      ),
        ('cast_shadows',         c_uint32, 1  ),
        ('pad',                  c_uint32, 31 ),
        ('shadows_type',         c_uint32     ), # shadowsType
        ('shadows_quality',      c_uint32     ), # shadowsQuality
        ('shadow_bias',          c_float      ), # shadowBias
        ('frame_start_id',       c_uint32     ),
        ('frame_num',            c_uint32     ),
        ('time_scale',           c_float      ), # timeScale
        ('duration',             c_float      ),
        ('unknown_6',            c_uint32     ),
        ('unknown_7',            c_uint32     ),
        ('unknown_8',            c_float      ),
        ('hemisphere_direction', c_float * 3  ), # hemisphereDirection
        ]

    _tests_ = {
        # TODO ...
        }



class PulseSpotLight_v1_11_0(CStructure):
    _size_ = 104

    _fields_ = [
        ('position',        c_float * 3  ),
        ('direction',       c_float * 3  ),
        ('inner_radius',    c_float      ),
        ('outer_radius',    c_float      ),
        ('lod_shift',       c_float      ), # lodShift
        ('cone_angle',      c_float      ),
        ('colour',          c_float * 4  ),
        ('unknown',         c_float      ),
        ('multiplier',      c_float      ),
        ('cast_shadows',    c_uint32, 1  ),
        ('pad1',            c_uint32, 7  ),
        ('unknown_bit_1',   c_uint32, 1  ),
        ('unknown_bit_2',   c_uint32, 1  ),
        ('unknown_bit_3',   c_uint32, 1  ),
        ('pad2',            c_uint32, 21 ),
        ('shadows_type',    c_uint32     ), # shadowsType
        ('shadows_quality', c_uint32     ), # shadowsQuality
        ('shadow_bias',     c_float      ), # shadowBias
        ('frame_start_id',  c_uint32     ),
        ('frame_num',       c_uint32     ),
        ('time_scale',      c_float      ), # timeScale
        ('duration',        c_float      ),
        ('unknown_6',       c_uint32     ),
        ('unknown_7',       c_uint32     ),
        ]

    _tests_ = {
        # TODO ...
        }



class BWLC_Section_1_11_0(Base_JSON_Section):
    header = 'BWLC'
    int1 = 2

    _fields_ = [
        (list, 'pulse_light_list',      PulseLight_v1_11_0     ),
        (list, 'pulse_spot_light_list', PulseSpotLight_v1_11_0 ),
        (list, 'frames',                '<2f'                  ),
        ]

    def to_xml(self, chunks):
        # TODO (untested):
        BWLC_Section_1_7_0.to_xml(self, chunks)

    @classmethod
    def _omniLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_7_0._omniLight_to_xml(chunk, item)

    @classmethod
    def _pulseLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_7_0._pulseLight_to_xml(chunk, item, frames)

    @classmethod
    def _spotLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_7_0._spotLight_to_xml(chunk, item)

    @classmethod
    def _pulseSpotLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_7_0._pulseSpotLight_to_xml(chunk, item, frames)
