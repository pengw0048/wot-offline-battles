""" BWfr (Flare) """

from .utils import *



__all__ = ('BWfr_Section_0_9_12', 'BWfr_Section_0_9_20')



class BWfr_Section_0_9_12(Base_JSON_Section):
	header = 'BWfr'
	int1 = 1

	_fields_ = [
		(list, 'flares', FlareInfo_0_9_12 ),
		]



class BWfr_Section_0_9_20(Base_JSON_Section):
	header = 'BWfr'
	int1 = 2

	_fields_ = [
		(list, 'flares', FlareInfo_ACTUAL ),
		]



"""
*o.chunk example:

<root>
	<overlapper>	00am0001i	</overlapper>
</root>


00am0001i.chunk example:

<root>
	<flare>
		<resource>	environments/fx/corona.xml	</resource>
		<position>	3.914024 2.467885 -1.490318	</position>
		<colour>	222.000000 141.000000 33.000000	</colour>
		<visibilityMask>	4294967172	</visibilityMask>
	</flare>
</root>
"""
