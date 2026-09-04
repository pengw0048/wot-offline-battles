""" WTCP (WoT static scene Control Point) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class ControlPoint_v0_9_12(CStructure):
	_size_ = 120

	_fields_ = [
		('transform',           c_float * 16 ),
		('radius',              c_float      ),
		('team',                c_uint32     ),
		('base_id',             c_uint32     ),
		('fade_speed',          c_float      ),
		('over_terrain_height', c_float      ),
		('radius_color',        c_float * 3  ),
		('flag_path_fnv',       c_uint32     ),
		('flagstaff_path_fnv',  c_uint32     ),
		('radius_path_fnv',     c_uint32     ),
		('flag_scale',          c_float      ),
		('event_name_fnv',      c_uint32     ),
		('before_wind',         c_uint32, 1  ),
		('pad',                 c_uint32, 31 ),
		]

	_tests_ = {
		'pad': { '==': 0 },
		}



class WTCP_Section_0_9_12(Base_JSON_Section):
	header = 'WTCP'
	int1 = 1

	_fields_ = [
		(list, 'control_points', ControlPoint_v0_9_12 ),
		]
