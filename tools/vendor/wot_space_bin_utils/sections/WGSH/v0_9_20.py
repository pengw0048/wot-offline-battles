""" WGSH (SHVolume) """

from .._base_json_section import *



class WGSH_Section_0_9_20(Base_JSON_Section):
	header = 'WGSH'
	int1 = 1

	_fields_ = [
		(list, '1', '<11I' ),
		]
