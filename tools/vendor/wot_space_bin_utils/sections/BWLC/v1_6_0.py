""" BWLC (Lights) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *



class PulseLight_v1_6_0(CStructure):
	_size_ = 72

	_fields_ = [
		('position',       c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
		('lod_shift',      c_float      ), # lodShift
		('colour',         c_float * 4  ),
		('unknown',        c_uint32     ),
		('multiplier',     c_float      ),
		('cast_shadows',   c_uint32, 1  ),
		('pad',            c_uint32, 31 ),
		('frame_start_id', c_uint32     ),
		('frame_num',      c_uint32     ),
		('unknown_2',      c_float      ),
		('duration',       c_float      ),
		('unknown_3',      c_uint32     ),
		]

	_tests_ = {
		# ...
		'inner_radius': { '<=': 110.0, '>=': 0.0 },
		'outer_radius': { '<=': 161.0, '>=': 0.0 },
		'colour': { '<=': (1.0, 1.0, 1.0, 1.0), '>=': (0.0, 0.0, 0.0, 0.0) },
		'unknown': { '==': 0 },
		'multiplier': { '>=': 0.0 },
		# ...
		'pad': { '==': 0 },
		# ...
		'unknown_2': { 'in': (0.5, 0.6, 1.0) },
		'duration': { '>=': 0.0 },
		'unknown_3': { 'in': (0, 0xFFFFFFFF) },
		}



class PulseSpotLight_v1_6_0(CStructure):
	_size_ = 88

	_fields_ = [
		('position',       c_float * 3  ),
		('direction',      c_float * 3  ),
		('inner_radius',   c_float      ),
		('outer_radius',   c_float      ),
		('lod_shift',      c_float      ), # lodShift
		('cone_angle',     c_float      ),
		('colour',         c_float * 4  ),
		('unknown',        c_float      ),
		('multiplier',     c_float      ),
		('cast_shadows',   c_uint32, 1  ),
		('pad1',           c_uint32, 7  ),
		('unknown_bit_1',  c_uint32, 1  ),
		('unknown_bit_2',  c_uint32, 1  ),
		('unknown_bit_3',  c_uint32, 1  ),
		('pad2',           c_uint32, 21 ),
		('frame_start_id', c_uint32     ),
		('frame_num',      c_uint32     ),
		('unknown_2',      c_float      ),
		('duration',       c_float      ),
		('unknown_3',      c_uint32     ),
		]

	_tests_ = {
		# ...
		'direction': { '<=': (1.0, 1.0, 1.0), '>=': (-1.0, -1.0, -1.0) },
		'inner_radius': { '<=': 110.0, '>=': 0.0 },
		'outer_radius': { '<=': 161.0, '>=': 0.0 },
		'cone_angle': { '<=': 1.58, '>=': 0.0 },
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
		'unknown_2': { 'in': (0.5, 1.0) },
		'duration': { '>=': 0.0 },
		'unknown_3': { 'in': (0, 0xFFFFFFFF) },
		}



class BWLC_Section_1_6_0(Base_JSON_Section):
	header = 'BWLC'
	int1 = 2

	_fields_ = [
		(list, 'pulse_light_list',      PulseLight_v1_6_0     ),
		(list, 'pulse_spot_light_list', PulseSpotLight_v1_6_0 ),
		(list, 'frames',                '<2f'                 ),
		]

	def to_xml(self, chunks):
		write = lambda *args: self._add2xml(el, *args)

		frames = self._data['frames']

		for it in self._data['pulse_light_list']:
			if it['frame_start_id'] == 0 and it['frame_num'] == 0:
				self._omniLight_to_xml(chunks.gchunk, it)
			else:
				self._pulseLight_to_xml(chunks.gchunk, it, frames)

		for it in self._data['pulse_spot_light_list']:
			if it['frame_start_id'] == 0 and it['frame_num'] == 0:
				self._spotLight_to_xml(chunks.gchunk, it)
			else:
				self._pulseSpotLight_to_xml(chunks.gchunk, it, frames)

	@classmethod
	def _omniLight_to_xml(cls, chunk, item):
		el = ET.SubElement(chunk, 'omniLight')
		write = lambda *args: cls._add2xml(el, *args)

		write('position',    item['position']           )
		write('innerRadius', item['inner_radius']       )
		write('outerRadius', item['outer_radius']       )
		write('lodShift',    item['lod_shift']          )
		write('colour',      mul_col(item['colour'])    )
		write('multiplier',  item['multiplier']         )
		write('castShadows', bool(item['cast_shadows']) )

	@classmethod
	def _pulseLight_to_xml(cls, chunk, item, frames):
		el = ET.SubElement(chunk, 'pulseLight')
		write = lambda *args: cls._add2xml(el, *args)

		write('position',    item['position']           )
		write('innerRadius', item['inner_radius']       )
		write('outerRadius', item['outer_radius']       )
		write('lodShift',    item['lod_shift']          )
		write('colour',      mul_col(item['colour'])    )
		write('multiplier',  item['multiplier']         )
		write('castShadows', bool(item['cast_shadows']) )
		write('duration',    item['duration']           )

		for i in range(item['frame_num']):
			frame = frames[item['frame_start_id'] + i]
			write('frame', frame)

	@classmethod
	def _spotLight_to_xml(cls, chunk, item):
		el = ET.SubElement(chunk, 'spotLight')
		write = lambda *args: cls._add2xml(el, *args)

		write('position',    item['position']           )
		write('innerRadius', item['inner_radius']       )
		write('outerRadius', item['outer_radius']       )
		write('lodShift',    item['lod_shift']          )
		write('coneAngle',   item['cone_angle']         )
		write('colour',      mul_col(item['colour'])    )
		write('multiplier',  item['multiplier']         )
		write('castShadows', bool(item['cast_shadows']) )

	@classmethod
	def _pulseSpotLight_to_xml(cls, chunk, item, frames):
		el = ET.SubElement(chunk, 'pulseSpotLight')
		write = lambda *args: cls._add2xml(el, *args)

		write('position',    item['position']           )
		write('innerRadius', item['inner_radius']       )
		write('outerRadius', item['outer_radius']       )
		write('lodShift',    item['lod_shift']          )
		write('coneAngle',   item['cone_angle']         )
		write('colour',      mul_col(item['colour'])    )
		write('multiplier',  item['multiplier']         )
		write('castShadows', bool(item['cast_shadows']) )
		write('duration',    item['duration']           )

		for i in range(item['frame_num']):
			frame = frames[item['frame_start_id'] + i]
			write('frame', frame)



def mul_col(col):
	return (col[0] * 255, col[1] * 255, col[2] * 255)
