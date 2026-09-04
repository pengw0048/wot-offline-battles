""" WGCO (Spatial Feature) """

from .._base_json_section import *



__all__ = ('WGCO_Section_0_9_20', 'WGCO_Section_1_0_0', 'WGCO_Section_1_0_1')



class WGCO_Section_0_9_20(Base_JSON_Section):
	header = 'WGCO'
	int1 = 1

	_fields_ = [
		(dict, '1', '<I4fI' ),
		(list, '2', '<6f'   ),
		(list, '3', '<3f'   ),
		(list, '4', '<4I'   ),
		(list, '5', '<i'    ),
		(list, '6', '<h'    ),
		(list, '7', '<I'    ),
		(list, '8', '<I'    ),
		]



class WGCO_Section_1_0_0(Base_JSON_Section):
	header = 'WGCO'
	int1 = 1

	_fields_ = [
		(dict, '1',  '<I4fI' ),
		(list, '2',  '<6f'   ),
		(list, '3',  '<3f'   ),
		(list, '4',  '<4I'   ),
		(list, '5',  '<i'    ),
		(list, '6',  '<h'    ),
		(list, '7',  '<I'    ),
		(list, '8',  '<I'    ),
		(dict, '9',  '<I4fI' ),
		(list, '10', '<6f'   ),
		(list, '11', '<3f'   ),
		(list, '12', '<4I'   ),
		(list, '13', '<i'    ),
		(list, '14', '<h'    ),
		(list, '15', '<I'    ),
		(list, '16', '<I'    ),
		]



class WGCO_Section_1_0_1(Base_JSON_Section):
	header = 'WGCO'
	int1 = 1

	_fields_ = [
		(dict, '1',  '<I4fI' ),
		(list, '2',  '<6f'   ),
		(list, '3',  '<3f'   ),
		(list, '4',  '<8I'   ),
		(list, '5',  '<i'    ),
		(list, '6',  '<I'    ),
		(list, '7',  '<I'    ),
		(list, '8',  '<I'    ),
		]
