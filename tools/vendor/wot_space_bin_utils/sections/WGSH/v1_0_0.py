""" WGSH (SHVolume) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class ShGridVolume_v1_0_0(CStructure):
	_size_ = 32

	_fields_ = [
		('enable',      c_uint32, 1  ),
		('pad',         c_uint32, 31 ),
		('position',    c_float * 3  ),
		('scale',       c_float * 3  ),
		('global_lerp', c_float      ),
		]



class WGSH_Section_1_0_0(Base_JSON_Section):
	header = 'WGSH'
	int1 = 1

	_fields_ = [
		(list, '1', ShGridVolume_v1_0_0 ),
		]
