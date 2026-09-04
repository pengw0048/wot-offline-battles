""" BWLC (Lights) """

from ctypes import c_float, c_uint32
from .._base_json_section import *
from .v1_11_0 import BWLC_Section_1_11_0



class PulseLight_v1_31_0_RU(CStructure):
    _size_ = 100

    _fields_ = [
        ('colour',               c_float * 4  ),
        ('position',             c_float * 3  ),
        ('shadows_quality',      c_uint32     ), # shadowsQuality
        ('hemisphere_direction', c_float * 2  ), # hemisphereDirection
        ('inner_radius',         c_float      ),
        ('outer_radius',         c_float      ),
        ('lod_shift',            c_float      ), # lodShift
        ('multiplier',           c_float      ),
        ('cast_shadows',         c_uint32, 1  ),
        ('shadows_type',         c_uint32     ), # shadowsType
        ('shadow_bias',          c_float      ), # shadowBias
        ('frame_num',            c_uint32     ),
        ('frame_start_id',       c_uint32     ),
        ('time_scale',           c_float      ), # timeScale
        ('duration',             c_float      ),
        ('decorative',           c_uint32     ), # decorative (Decor Type)
        ('visibility_mask',      c_uint32     ), # visibilityMask
        ('pad',                  c_uint32, 31),
        ('unknown_8',            c_float      ),
        ]

    _tests_ = {
        # TODO ...
        }



class PulseSpotLight_v1_31_0_RU(CStructure):
    _size_ = 104

    _fields_ = [
        ('colour',          c_float * 4  ),
        ('position',       c_float  * 3 ),
        ('direction',    c_float   * 3 ),
        ('inner_radius',    c_float      ),
        ('outer_radius',        c_float  ),
        ('lod_shift',       c_float       ), # lodShift
        ('cone_angle',      c_float      ),
        ('multiplier',      c_float      ),
        ('shadow_bias',         c_float     ),
        ('shadows_type',    c_uint32     ), # shadowsType
        ('shadows_quality', c_uint32     ), # shadowsQuality
        ('unknown',     c_float      ), # shadowBias
        ('frame_start_id',  c_uint32     ),
        ('frame_num',       c_uint32     ),
        ('time_scale',      c_float      ), # timeScale
        ('duration',        c_float      ),
        ('visibility_mask', c_uint32     ), # visibilityMask
        ('decorative',      c_uint32     ), # decorative (Decor Type)
        ('cast_shadows',    c_uint32, 1  ),
        ('pad1',            c_uint32, 7  ),
        ('unknown_bit_1',   c_uint32, 1  ),
        ('unknown_bit_2',   c_uint32, 1  ),
        ('unknown_bit_3',   c_uint32, 1  ),
        ('pad2',            c_uint32, 21 ),
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

        'duration': { '>=': 0.0 },

        }



class BWLC_Section_1_31_0_RU(Base_JSON_Section):
    header = 'BWLC'
    int1 = 3

    _fields_ = [
        (list, 'pulse_light_list',      PulseLight_v1_31_0_RU     ),
        (list, 'pulse_spot_light_list', PulseSpotLight_v1_31_0_RU ),
        (list, 'frames',                '<2f'                     ),
        ]

    def to_xml(self, chunks):
        # TODO (untested):
        BWLC_Section_1_11_0.to_xml(self, chunks)

    @classmethod
    def _omniLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_11_0._omniLight_to_xml(chunk, item)

    @classmethod
    def _pulseLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_11_0._pulseLight_to_xml(chunk, item, frames)

    @classmethod
    def _spotLight_to_xml(cls, chunk, item):
        # TODO (untested):
        BWLC_Section_1_11_0._spotLight_to_xml(chunk, item)

    @classmethod
    def _pulseSpotLight_to_xml(cls, chunk, item, frames):
        # TODO (untested):
        BWLC_Section_1_11_0._pulseSpotLight_to_xml(chunk, item, frames)
