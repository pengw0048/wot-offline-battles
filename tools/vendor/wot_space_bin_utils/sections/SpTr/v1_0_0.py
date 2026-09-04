""" SpTr (SpeedTree) """

from ctypes import c_float, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *



class SpTrInfo_v1_0_0(CStructure):
	_size_ = 80

	_fields_ = [
		('transform',                c_float * 16 ),
		('spt_fnv',                  c_uint32     ),
		('seed',                     c_uint32     ),
		('casts_shadow',             c_uint32, 1  ), # speedtree/castsShadow
		('casts_local_shadow',       c_uint32, 1  ), # speedtree/castsLocalShadow
		('always_dynamic',           c_uint32, 1  ), # speedtree/alwaysDynamic
		('pad',                      c_uint32, 29 ),
		('visibility_mask',          c_uint32     ),
		]

	_tests_ = {
		'pad': { 'in': (0, 1, 4, 8) }
		}



class SpTrInfo2_v1_0_0(CStructure):
	'''
	destructibles.xml/trees
	'''
	_size_ = 52

	_fields_ = [
		('lifetime_effect_fnv',    c_uint32    ), # lifetimeEffect
		('fracture_effect_fnv',    c_uint32    ), # fractureEffect
		('touchdown_effect_fnv',   c_uint32    ), # touchdownEffect
		('lifetime_effect_chance', c_float     ), # lifetimeEffectChance
		('health',                 c_float     ), # health
		('density',                c_float     ), # density
		('physic_params',          c_float * 7 ), # physicParams
		]

	_tests_ = {
		'lifetime_effect_chance': { 'in': (0.0, 1.0) }
		}



class SpTr_Section_1_0_0(Base_JSON_Section):
	header = 'SpTr'
	int1 = 3

	_fields_ = [
		(list, '1',    SpTrInfo_v1_0_0  ),
		(list, '2',    SpTrInfo2_v1_0_0 ),
		(dict, 'info', '<6f'            ),
		]

	def to_xml(self, chunks):
		write = lambda *args: self._add2xml(el, *args)

		for it in self._data['1']:
			chunk, transform = chunks.get_by_transform(it['transform'])

			el = ET.SubElement(chunk, 'speedtree')

			write('transform',        transform                      )
			write('spt',              chunks.gets(it['spt_fnv'])     )
			write('seed',             it['seed']                     )
			write('castsShadow',      bool(it['casts_shadow'])       )
			write('castsLocalShadow', bool(it['casts_local_shadow']) )
			write('alwaysDynamic',    bool(it['always_dynamic'])     )
			write('visibilityMask',   it['visibility_mask']          )
