""" BWSS (?) """

from .._base_json_section import *



class BWSS_Section_0_9_12(Base_JSON_Section):
	header = 'BWSS'
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
		(list, '17', '<6f'   ),
		(list, '18', '<4f'   ),
		(list, '19', '<2I'   ),
		]
