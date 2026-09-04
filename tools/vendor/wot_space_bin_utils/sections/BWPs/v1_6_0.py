""" BWPs (Particles) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *



class ParticleInfo_1_6_0(CStructure):
	_size_ = 80

	_fields_ = [
		('transform',          c_float * 16 ),
		('resource_fnv',       c_uint32     ),
		('reflection_visible', c_uint32, 1  ),
		('pad',                c_uint32, 31 ),
		('unknown',            c_uint32     ), # mask?
		('seed_time',          c_float      ), # 0.1 by default
		]

	_tests_ = {
		# ...
		#'pad': { '==': 0 },
		'unknown': { 'in': (0xffffff99, 0xFFFFFFFF) },
		'seed_time': { '>=': 0.0 },
		}



class BWPs_Section_1_6_0(Base_JSON_Section):
	header = 'BWPs'
	int1 = 2

	_fields_ = [
		(list, 'particles', ParticleInfo_1_6_0),
		]

	def to_xml(self, chunks):
		write = lambda *args: self._add2xml(el, *args)

		for it in self._data['particles']:
			chunk, transform = chunks.get_by_transform(it['transform'])
			el = ET.SubElement(chunk, 'particles')

			#write('visibilityMask', 4294967295)
			write('resource',  chunks.gets(it['resource_fnv']) )
			write('transform', transform                       )
