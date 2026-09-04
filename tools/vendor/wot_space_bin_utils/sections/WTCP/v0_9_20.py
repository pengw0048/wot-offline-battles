""" WTCP (WoT static scene Control Point) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *



class ControlPoint_v0_9_20(CStructure):
	_size_ = 124

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
		]

	_tests_ = {
		'pad': { '==': 0 },
		}



class WTCP_Section_0_9_20(Base_JSON_Section):
	header = 'WTCP'
	int1 = 2

	_fields_ = [
		(list, 'control_points', ControlPoint_v0_9_20 ),
		]

	def to_xml(self, chunks):
		write = lambda *args: self._add2xml(el, *args)

		for it in self._data['control_points']:
			el = ET.SubElement(chunks.gchunk, 'ControlPoint')

			write('transform',         it['transform']                       )
			write('radius',            it['radius']                          )
			write('team',              it['team']                            )
			write('baseID',            it['base_id']                         )
			write('overTerrainHeight', it['over_terrain_height']             )
			write('radiusColor',       it['radius_color']                    )
			write('flagPath',          chunks.gets(it['flag_path_fnv'])      )
			write('flagstaffPath',     chunks.gets(it['flagstaff_path_fnv']) )
			write('radiusPath',        chunks.gets(it['radius_path_fnv'])    )
			write('flagScale',         it['flag_scale']                      )
			write('wweventName',       chunks.gets(it['wwevent_name_fnv'])   )
			write('visibilityMask',    it['visibility_mask']                 )
			#write('pointsPerSecond',    1.0 ) # ?
			#write('maxPointsPerSecond', 3.0 ) # ?
