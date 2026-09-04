""" BWWa (Water) """

from ctypes import c_float, c_uint32
from .._base_json_section import *



class WaterInfo_v1_0_0(CStructure):
	_size_ = 336

	_fields_ = [
		('bbox_min',                     c_float * 3  ), # bboxMin
		('bbox_max',                     c_float * 3  ), # bboxMax
		('scale_parameters',             c_float * 4  ), # scaleParameters
		('flow_map_parameters',          c_float * 2  ), # flowMapParameters
		('scroll1',                      c_float * 2  ), # scroll1
		('texture_rotation',             c_float      ), # textureRotation
		('animation_speed',              c_float      ), # animationSpeed
		('wave_texture_number',          c_uint32     ), # waveTextureNumber
		('scale_parameters2',            c_float * 4  ), # scaleParameters2
		('flow_map_parameters2',         c_float * 2  ), # flowMapParameters2
		('scroll2',                      c_float * 2  ), # scroll2
		('texture_rotation2',            c_float      ), # textureRotation2
		('animation_speed2',             c_float      ), # animationSpeed2
		('wave_texture_number2',         c_uint32     ), # waveTextureNumber2
		('foam_flow_map_parameters',     c_float * 2  ), # foamFlowMapParameters
		('foam_scroll',                  c_float * 2  ), # foamScroll
		('foam_scale',                   c_float      ), # foamScale
		('foam_contrast',                c_float      ), # foamContrast
		('foam_enabled',                 c_uint32, 1  ), # foamEnabled
		('pad1',                         c_uint32, 31 ),
		('forward_scroll',               c_float * 2  ), # forwardScroll
		('forward_reflection_intensity', c_float      ), # forwardReflectionIntensity
		('min_opacity',                  c_float      ), # minOpacity
		('position',                     c_float * 3  ),
		('size',                         c_float * 2  ),
		('ramp_depth',                   c_float      ), # rampDepth
		('soft_depth',                   c_float      ), # softDepth
		('out_water_sides0',             c_uint32, 1  ), # outWaterSides0
		('pad2',                         c_uint32, 7  ),
		('out_water_sides1',             c_uint32, 1  ), # outWaterSides1
		('pad3',                         c_uint32, 7  ),
		('out_water_sides2',             c_uint32, 1  ), # outWaterSides2
		('pad4',                         c_uint32, 7  ),
		('out_water_sides3',             c_uint32, 1  ), # outWaterSides3
		('pad5',                         c_uint32, 7  ),
		('out_water_corners0',           c_uint32, 1  ), # outWaterCorners0
		('pad6',                         c_uint32, 7  ),
		('out_water_corners1',           c_uint32, 1  ), # outWaterCorners1
		('pad7',                         c_uint32, 7  ),
		('out_water_corners2',           c_uint32, 1  ), # outWaterCorners2
		('pad8',                         c_uint32, 7  ),
		('out_water_corners3',           c_uint32, 1  ), # outWaterCorners3
		('pad9',                         c_uint32, 7  ),
		('fog_color',                    c_float * 4  ), # fogColor
		('fog_color_multiplier',         c_float      ), # fogColorMultiplier
		('fog_depth',                    c_float      ), # fogDepth
		('caustics_intensity',           c_float      ), # causticsIntensity
		('wet_over_intensity',           c_float      ), # wetOverIntensity
		('wet_power',                    c_float      ), # wetPower
		('wet_underwater_power',         c_float      ), # wetUnderwaterPower
		('wet_height',                   c_float      ), # wetHeight
		('space_min_x',                  c_float      ), # spaceMinX
		('space_min_z',                  c_float      ), # spaceMinZ
		('space_max_x',                  c_float      ), # spaceMaxX
		('space_max_z',                  c_float      ), # spaceMaxZ
		('wave2_enabled',                c_uint32, 1  ), # wave2Enabled
		('pad10',                        c_uint32, 7  ),
		('use_water_probes',             c_uint32, 1  ), # useWaterProbes
		('pad11',                        c_uint32, 7  ),
		('flow_map_invert_x',            c_uint32, 1  ), # flowMapInvertX
		('pad12',                        c_uint32, 7  ),
		('flow_map_invert_z',            c_uint32, 1  ), # flowMapInvertZ
		('pad13',                        c_uint32, 7  ),
		('use_flowmap',                  c_uint32, 1  ), # useFlowmap
		('pad14',                        c_uint32, 7  ),
		('use_amplitudes_map',           c_uint32, 1  ), # useAmplitudesMap
		('pad15',                        c_uint32, 23 ),
		('camera_offset',                c_float      ), # cameraOffset
		('foam_texture_fnv',             c_uint32     ), # foamTexture
		('flowmap_texture0_fnv',         c_uint32     ), # flowmapTexture0
		('flowmap_texture1_fnv',         c_uint32     ), # flowmapTexture1
		('wave_texture_fnv',             c_uint32     ), # waveTexture
		('wave_height_texture_fnv',      c_uint32     ), # waveHeightTexture
		('wave_texture2_fnv',            c_uint32     ), # waveTexture2
		('wave_height_texture2_fnv',     c_uint32     ), # waveHeightTexture2
		('ramp_texture_fnv',             c_uint32     ), # rampTexture
		('unknown',                      c_uint32     ),
		('depth_fnv',                    c_uint32     ),
		('start_id_1',                   c_uint32     ),
		('end_id_1',                     c_uint32     ),
		('start_id_2',                   c_uint32     ),
		('end_id_2',                     c_uint32     ),
		('start_id_3',                   c_uint32     ),
		('end_id_3',                     c_uint32     ),
		('start_id_4',                   c_uint32     ),
		('end_id_4',                     c_uint32     ),
		]



class BWWa_Section_1_0_0(Base_JSON_Section):
	header = 'BWWa'
	int1 = 2

	_fields_ = [
		(list, '1', WaterInfo_v1_0_0 ),
		(list, '2', '<6f'            ),
		(list, '3', '<3f2If'         ),
		(list, '4', '<I'             ),
		(list, '5', '<b'             ),
		]
