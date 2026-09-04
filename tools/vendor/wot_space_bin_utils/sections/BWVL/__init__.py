""" BWVL (?) """

from .._base_json_section import *



__all__ = ('BWVL_Section_1_15_0',)



class BWVL_Section_1_15_0(Base_JSON_Section):
	header = 'BWVL'
	int1 = 0

	_fields_ = [
		(list, '1', '<16f3I' ), # transform + keys from BWST
		]
