""" WTCP (WoT static scene Control Point) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class ControlPoint_v1_23_0(CStructure):
	_size_ = 136

	_fields_ = [
		('transform',           c_float * 16 ),
		('radius',              c_float      ),
		('team',                c_uint32     ),
		('base_id',             c_uint32     ),
		('over_terrain_height', c_float      ),
		('radius_color',        c_float * 4  ),
		('flag_path_fnv',       c_uint32     ),
		('flagstaff_path_fnv',  c_uint32     ),
		('radius_path_fnv',     c_uint32     ),
		('flag_scale',          c_float      ),
		('wwevent_name_fnv',    c_uint32     ),
		('before_wind',         c_uint32, 1  ),
		('pad',                 c_uint32, 31 ),
		('visibility_mask',     c_uint32     ),
		('arcade_1',            c_float * 3  ),
		]

	_tests_ = {
		'pad': { '==': 0 },
		}



class WTCP_Section_1_23_0(Base_JSON_Section):
	header = 'WTCP'
	int1 = 3

	_fields_ = [
		(list, 'control_points', ControlPoint_v1_23_0 ),
		]
