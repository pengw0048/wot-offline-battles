""" WGSD (Decal) """

from ctypes import c_float, c_uint8, c_uint32
from .._base_json_section import *



class Decal1Info(CStructure):
	_size_ = 116

	_fields_ = [
		('accurate',        c_uint32     ),
		('transform',       c_float * 16 ),
		('diff_tex_fnv',    c_uint32     ),
		('bump_tex_fnv',    c_uint32     ),
		('hm_tex_fnv',      c_uint32     ),
		('_6_fnv',          c_uint32     ), # maybe addTex
		('priority',        c_uint8      ),
		('influence',       c_uint8      ),
		('type',            c_uint8      ),
		('pad',             c_uint8      ),
		('offsets',         c_float * 4  ),
		('uv_wrapping',     c_float * 2  ),
		('visibility_mask', c_uint32     ),
		]



class Decal2Info(CStructure):
	_size_ = 32

	_fields_ = [
		('floats',    c_float * 6 ),
		('unknown_1', c_uint32    ),
		('unknown_2', c_uint32    ),
		]



class WGSD_Section_0_9_12(Base_JSON_Section):
	header = 'WGSD'
	int1 = 2

	_fields_ = [
		(list, '1', Decal1Info ),
		(list, '2', Decal2Info ),
		]
