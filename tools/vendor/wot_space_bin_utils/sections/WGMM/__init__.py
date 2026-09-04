""" WGMM (Megalod Model Instances) """

from .._base_json_section import *



__all__ = ('WGMM_Section_1_4_0',)



class WGMM_Section_1_4_0(Base_JSON_Section):
	header = 'WGMM'
	int1 = 1

	_fields_ = [
		(list, '1', '<8I' ),
		(list, '2', '<2I' ),
		]
