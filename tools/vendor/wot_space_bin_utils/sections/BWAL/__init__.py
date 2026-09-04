""" BWAL (Asset List) """

from .utils import *



__all__ = ('BWAL_Section_0_9_12',)



class BWAL_Section_0_9_12(Base_JSON_Section):
	header = 'BWAL'
	int1 = 2

	_fields_ = [
		(list, 'asset_list', AssetInfo),
		]

	def add(self, asset_type, string_fnv):
		self._data['asset_list'].append({
			'asset_type': asset_type,
			'string_fnv': string_fnv
		})
