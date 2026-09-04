""" BWfr (Flare) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class FlareInfo_0_9_12(CStructure):
	_size_ = 44

	_fields_ = [
		('resource_fnv', c_uint32    ),
		('max_distance', c_float     ),
		('area',         c_float     ),
		('fade_speed',   c_float     ),
		('position',     c_float * 3 ),
		('colour',       c_float * 3 ),
		('unknown',      c_uint32    ),
		]

	_tests_ = {
		'unknown': { '==': 1 },
		}



class FlareInfo_ACTUAL(CStructure):
	_size_ = 48

	_fields_ = [
		('resource_fnv',    c_uint32    ),
		('max_distance',    c_float     ),
		('area',            c_float     ),
		('fade_speed',      c_float     ),
		('position',        c_float * 3 ),
		('colour',          c_float * 3 ),
		('unknown',         c_uint32    ),
		('visibility_mask', c_uint32    ),
		]

	_tests_ = {
		'unknown': { '==': 1 },
		}
