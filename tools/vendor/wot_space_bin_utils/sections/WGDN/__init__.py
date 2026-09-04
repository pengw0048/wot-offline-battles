""" WGDN (?) """

from .._base_json_section import *



__all__ = ('WGDN_Section_0_9_12',)



class WGDN_Section_0_9_12(Base_JSON_Section):
	header = 'WGDN'
	int1 = 1

	_fields_ = [
		(list, '1', '<3I' ),
		(list, '2', '<2I' ),
		(list, '3', '<I'  ),
		]
