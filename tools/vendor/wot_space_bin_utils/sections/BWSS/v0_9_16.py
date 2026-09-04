""" BWSS (?) """

from .._base_json_section import *



class BWSS_Section_0_9_16(Base_JSON_Section):
	header = 'BWSS'
	int1 = 1

	_fields_ = [
		(list, '1',  '<2I'),
		]
