""" BWEP (environment probe) """

from .._base_json_section import *



class BWEP_Section_0_9_20(Base_JSON_Section):
	header = 'BWEP'
	int1 = 4

	_fields_ = [
		(list, '1', '<43I' ),
		(list, '2', '<43I' ),
		]
