from ctypes import c_uint32
from .._base_json_section import *



class AssetInfo(CStructure):
	_size_ = 8

	_fields_ = [
		('asset_type', c_uint32 ),
		('string_fnv', c_uint32 ),
		]

	_tests_ = {
		'asset_type': {
			'in': (
				1, # particles_resource
				2, # water_reflection_texture
				5, # control_point_radius_path
				6, # model_resource
				)
			}
		}
