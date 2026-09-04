""" BWCS (CompiledSpaceSettings) """

from .._base_json_section import *



__all__ = ('BWCS_Section_0_9_12',)



class BWCS_Section_0_9_12(Base_JSON_Section):
	header = 'BWCS'
	int1 = 1

	_fields_ = [
		(dict, '1', '<6f' ),
		]
