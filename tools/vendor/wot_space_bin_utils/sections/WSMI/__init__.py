""" WSMI (WoT Static Model Instances) """

from .._base_json_section import *



__all__ = ('WSMI_Section_0_9_12',)



class WSMI_Section_0_9_12(Base_JSON_Section):
	header = 'WSMI'
	int1 = 1

	_fields_ = [
		(list, '1', '<3i' ),
		(list, '2', '<I'  ),
		]
