""" BWLC (Lights) """

from ctypes import c_float, c_uint32, c_int32
from .._base_json_section import *



class OmniLight_v0_9_12(CStructure):
	_size_ = 48

	_fields_ = [
		('position',     c_float * 3  ),
		('inner_radius', c_float      ),
		('outer_radius', c_float      ),
		('colour',       c_float * 4  ),
		('unknown',      c_uint32     ),
		('multiplier',   c_float      ),
		('cast_shadows', c_uint32, 1  ),
		('pad',          c_uint32, 31 ),
		]

	_tests_ = {
		# ...
		'inner_radius': { '<=': 100.0, '>=': 0.0 },
		'outer_radius': { '<=': 101.0, '>=': 0.0 },
		'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
		'unknown': { '==': 0 },
		'multiplier': { '>=': 0.0 },
		# ...
		'pad': { '==': 0 },
		}



class SpotLight_v0_9_12(CStructure):
	_size_ = 64

	_fields_ = [
		('position',       c_float * 3  ),
		('direction',      c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
		('cos_cone_angle', c_float      ),
		('colour',         c_float * 4  ),
		('unknown',        c_float      ),
		('multiplier',     c_float      ),
		('cast_shadows',   c_uint32, 1  ),
		('pad1',           c_uint32, 7  ),
		('unknown_bit_1',  c_uint32, 1  ),
		('pad2',           c_uint32, 23 ),
		]

	_tests_ = {
		# ...
		'direction': { '<=': (1.0, 1.0, 1.0), '>=': (-1.0, -1.0, -1.0) },
		'inner_radius': { '<=': 100.0, '>=': 0.0 },
		'outer_radius': { '<=': 101.0, '>=': 0.0 },
		'cos_cone_angle': { '<=': 1.0, '>=': 0.0 },
		'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
		'unknown': { '==': 0.0 },
		'multiplier': { '>=': 0.0 },
		# ...
		'pad1': { '==': 0 },
		'unknown_bit_1': { '==': 0 },
		'pad2': { '==': 0 },
		}



class PulseLight_v0_9_12(CStructure):
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
		('animation_id',   c_uint32     ),
		('frame_start_id', c_uint32     ),
		('frame_num',      c_uint32     ),
		('duration',       c_float      ),
		]

	_tests_ = {
		# ...
		'inner_radius': { '<=': 100.0, '>=': 0.0 },
		'outer_radius': { '<=': 101.0, '>=': 0.0 },
		'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
		'unknown': { '==': 0 },
		'multiplier': { '>=': 0.0 },
		# ...
		'pad': { '==': 0 },
		# ...
		'duration': { '>=': 0.0 },
		}



class PulseSpotLight_v0_9_12(CStructure):
	_size_ = 80

	_fields_ = [
		('position',       c_float * 3  ),
		('direction',      c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
		('cos_cone_angle', c_float      ),
		('colour',         c_float * 4  ),
		('unknown',        c_float      ),
		('multiplier',     c_float      ),
		('cast_shadows',   c_uint32, 1  ),
		('pad1',           c_uint32, 7  ),
		('unknown_bit_1',  c_uint32, 1  ),
		('unknown_bit_2',  c_uint32, 1  ),
		('unknown_bit_3',  c_uint32, 1  ),
		('pad2',           c_uint32, 21 ),
		('animation_id',   c_uint32     ),
		('frame_start_id', c_uint32     ),
		('frame_num',      c_uint32     ),
		('duration',       c_float      ),
		]

	_tests_ = {
		# ...
		'direction': { '<=': (1.0, 1.0, 1.0), '>=': (-1.0, -1.0, -1.0) },
		'inner_radius': { '<=': 100.0, '>=': 0.0 },
		'outer_radius': { '<=': 101.0, '>=': 0.0 },
		'cos_cone_angle': { '<=': 1.0, '>=': 0.0 },
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



class BWLC_Section_0_9_12(Base_JSON_Section):
	header = 'BWLC'
	int1 = 1

	_fields_ = [
		(list, 'omni_light_list',       OmniLight_v0_9_12      ),
		(list, 'spot_light_list',       SpotLight_v0_9_12      ),
		(list, 'pulse_light_list',      PulseLight_v0_9_12     ),
		(list, 'pulse_spot_light_list', PulseSpotLight_v0_9_12 ),
		(list, 'frames',                '<2f'                  ),
		]
