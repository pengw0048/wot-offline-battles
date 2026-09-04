""" BWWa (Water) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class WaterInfo(CStructure):
	_size_ = 268

	_fields_ = [
		('position',                      c_float * 3  ),
		('size',                          c_float * 2  ),
		('orientation',                   c_float      ),
		('tessellation',                  c_float      ),
		('texture_tessellation',          c_float      ),
		('consistency',                   c_float      ),
		('reflection_strength',           c_float      ),
		('refraction_strength',           c_float      ),
		('water_contrast',                c_float      ),
		('animation_speed',               c_float      ),
		('scroll_speed1',                 c_float * 2  ),
		('scroll_speed2',                 c_float * 2  ),
		('wave_scale',                    c_float * 2  ),
		('sun_power',                     c_float      ),
		('sun_scale',                     c_float      ),
		('sun_scale_deferred',            c_float      ),
		('wind_velocity',                 c_float      ),
		('depth',                         c_float      ),
		('reflection_tint',               c_float * 4  ),
		('refraction_tint',               c_float * 4  ),
		('cellsize',                      c_float      ),
		('smoothness',                    c_float      ),
		('use_edge_alpha',                c_uint32, 1  ),
		('pad1',                          c_uint32, 7  ),
		('use_simulation',                c_uint32, 1  ),
		('pad2',                          c_uint32, 23 ),
		('unknown_1',                     c_uint32     ),
		('reflect_bottom',                c_uint32, 1  ),
		('pad3',                          c_uint32, 31 ),
		('deep_water_opacity_forward',    c_float      ),
		('bank_distance_opacity_forward', c_float      ),
		('opacity_multiplier_forward',    c_float      ),
		('opacity_power_forward',         c_float      ),
		('shallow_water_bias_forward',    c_float      ),
		('deep_colour',                   c_float * 4  ),
		('deep_colour_multiplier',        c_float      ),
		('fade_depth',                    c_float      ),
		('foam_intersection',             c_float      ),
		('foam_multiplier',               c_float      ),
		('foam_tiling',                   c_float      ),
		('foam_freq',                     c_float      ),
		('foam_amplitude',                c_float      ),
		('foam_width',                    c_float      ),
		('bypass_depth',                  c_uint32, 1  ),
		('pad4',                          c_uint32, 31 ),
		('freq_x',                        c_float      ),
		('freq_z',                        c_float      ),
		('wave_height',                   c_float      ),
		('caustics_power',                c_float      ),
		('wave_texture_number',           c_uint32     ),
		('use_shadows',                   c_uint32, 1  ),
		('pad5',                          c_uint32, 7  ),
		('use_water_probes',              c_uint32, 1  ),
		('pad6',                          c_uint32, 23 ),
		('foam_texture_fnv',              c_uint32     ),
		('wave_texture_fnv',              c_uint32     ),
		('reflection_texture_fnv',        c_uint32     ),
		('odata_path_fnv',                c_uint32     ),
		('start_id',                      c_uint32     ),
		('end_id',                        c_uint32     ),
		]

	_tests_ = {
		# ...
		'pad1': { '==': 0 },
		# ...
		'pad2': { '==': 0 },
		'unknown_1': { '==': 0 },
		# ...
		'pad3': { '==': 0 },
		# ...
		'pad4': { '==': 0 },
		# ...
		'pad5': { '==': 0 },
		# ...
		'pad6': { '==': 0 },
		# ...
		}



class BWWa_Section_0_9_12(Base_JSON_Section):
	header = 'BWWa'
	int1 = 2

	_fields_ = [
		(list, '1', WaterInfo ),
		(list, '2', '<6f'     ),
		]
