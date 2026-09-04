""" WTbl (Benchmark Locations) """

from .._base_json_section import *



__all__ = ('WTbl_Section_0_9_20',)



class WTbl_Section_0_9_20(Base_JSON_Section):
	header = 'WTbl'
	int1 = 0

	_fields_ = [
		(list, 'benchmark_locations', '<3f' ),
		]
