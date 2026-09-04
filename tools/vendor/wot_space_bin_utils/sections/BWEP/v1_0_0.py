""" BWEP (environment probe) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *



class EnvironmentProbe_v1_0_0(CStructure):
	_size_ = 52

	_fields_ = [
		('unknown1',                c_uint32    ),
		('width',                   c_float     ), # width
		('height',                  c_float     ), # height
		('length',                  c_float     ), # length
		('box_transition_distance', c_float     ), # boxTransitionDistance
		('world_position',          c_float * 3 ), # worldPosition
		('priority',                c_uint32    ), # priority
		('probe_type',              c_uint32    ), # probeType
		('id',                      c_uint32    ), # id
		('target_type',             c_uint32    ), # targetType
		('visibility_mask',         c_uint32    ), # visibilityMask
		]

	_tests_ = {
		'unknown1': { '==': 1 },
		'priority': { 'in': (0, 1) },
		'probe_type': { 'in': (0, 1) },
		'target_type': { 'in': (2, 3) },
		}



class BWEP_Section_1_0_0(Base_JSON_Section):
	header = 'BWEP'
	int1 = 4

	_fields_ = [
		(list, 'environment_probes', EnvironmentProbe_v1_0_0 ),
		(list, '2',                  '<I4f8I'                ),
		]

	def to_xml(self, chunks):
		write = lambda *args: self._add2xml(el, *args)

		for it in self._data['environment_probes']:
			chunk, transform = chunks.get_by_worldpos(it['world_position'])

			el = ET.SubElement(chunk, 'environmentProbe')

			write('width',                 it['width']                   )
			write('height',                it['height']                  )
			write('length',                it['length']                  )
			write('boxTransitionDistance', it['box_transition_distance'] )
			write('worldPosition',         it['world_position']          )
			write('priority',              it['priority']                )
			write('probeType',             it['probe_type']              )
			write('id',                    chunks.gets(it['id'])         )
			write('targetType',            it['target_type']             )
			write('visibilityMask',        it['visibility_mask']         )
			write('transform',             transform                     )

			# TODO:
			write('numberOfPasses', 1)
