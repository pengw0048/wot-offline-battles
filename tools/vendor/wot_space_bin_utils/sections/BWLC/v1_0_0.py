""" BWLC (Lights) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class PulseLight_v1_0_0(CStructure):
	_size_ = 64

	_fields_ = [
		('position',       c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
		('colour',         c_float * 4  ),
		('unknown',        c_uint32     ),
		('multiplier',     c_float      ),
		('cast_shadows',   c_uint32, 1  ),
		('pad',            c_uint32, 31 ),
		('frame_start_id', c_uint32     ),
		('frame_num',      c_uint32     ),
		('unknown_2',      c_float      ),
		('duration',       c_float      ),
		]

	_tests_ = {
		# ...
		'inner_radius': { '<=': 110.0, '>=': 0.0 },
		'outer_radius': { '<=': 140.0, '>=': 0.0 },
		'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
		'unknown': { '==': 0 },
		'multiplier': { '>=': 0.0 },
		# ...
		'pad': { '==': 0 },
		# ...
		'unknown_2': { 'in': (0.5, 1.0) },
		'duration': { '>=': 0.0 },
		}



class PulseSpotLight_v1_0_0(CStructure):
	_size_ = 80

	_fields_ = [
		('position',       c_float * 3  ),
		('direction',      c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
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
		('unknown_2',      c_float      ),
		('duration',       c_float      ),
		]

	_tests_ = {
		# ...
		'direction': { '<=': (1.0, 1.0, 1.0), '>=': (-1.0, -1.0, -1.0) },
		'inner_radius': { '<=': 110.0, '>=': 0.0 },
		'outer_radius': { '<=': 140.0, '>=': 0.0 },
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
		'unknown_2': { '==': 1.0 },
		'duration': { '>=': 0.0 },
		}



class BWLC_Section_1_0_0(Base_JSON_Section):
	header = 'BWLC'
	int1 = 1

	_fields_ = [
		(list, 'pulse_light_list',      PulseLight_v1_0_0     ),
		(list, 'pulse_spot_light_list', PulseSpotLight_v1_0_0 ),
		(list, 'frames',                '<2f'                 ),
		]
