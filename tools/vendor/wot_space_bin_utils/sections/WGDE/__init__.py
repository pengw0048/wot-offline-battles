""" WGDE (DestructiblesSceneProvider) """

from .._base_json_section import *



__all__ = ('WGDE_Section_0_9_12', 'WGDE_Section_0_9_20')



class WGDE_Section_0_9_12(Base_JSON_Section):
	header = 'WGDE'
	int1 = 1

	_fields_ = [
		(list, '1', '<3I' ),
		(list, '2', '<I'  ),
		]



class WGDE_Section_0_9_20(Base_JSON_Section):
	header = 'WGDE'
	int1 = 1

	_fields_ = [
		(list, '1', '<3I' ),
		(list, '2', '<2I' ),
		(list, '3', '<I'  ),
		]
