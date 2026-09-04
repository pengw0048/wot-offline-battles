""" BSMA (Static Materials) """

from struct import unpack, pack
from ctypes import c_uint32, c_int32
from .._base_json_section import *



class MaterialInfo(CStructure):
	_size_ = 12

	_fields_ = [
		('key_fx',   c_int32 ),
		('key_from', c_int32 ),
		('key_to',   c_int32 ),
		]

	_tests_ = {
		'key_fx': { '>=': -1 },
		'key_from': { '>=': -1 },
		'key_to': { '>=': -1 },
		}



class PropertyInfo:
	_size_ = 12

	def __init__(self, data):
		if isinstance(data, dict):
			self.from_dict(data)
		else:
			self.from_bin(data)

	def to_bin(self):
		return pack(
			'<2If' if self.value_type == 2 else '<3I',
			self.prop_fnv,
			self.value_type,
			self.value
		)

	def to_dict(self):
		result = {}
		result['prop_fnv'] = self.prop_fnv
		result['value_type'] = self.value_type
		result['value'] = self.value
		return result

	def from_dict(self, data):
		self.prop_fnv = data['prop_fnv']
		self.value_type = data['value_type']
		self.value = data['value']

	def from_bin(self, data):
		self.prop_fnv, vt = unpack('<2I', data[:8])
		self.value_type = vt
		self.value = unpack('<f' if vt == 2 else '<I', data[8:])[0]



class DDS_HEADER(CStructure):
	_size_ = 124

	_fields_ = [
		('dwSize',              c_uint32      ),
		('dwFlags',             c_uint32      ),
		('dwHeight',            c_uint32      ),
		('dwWidth',             c_uint32      ),
		('dwPitchOrLinearSize', c_uint32      ),
		('dwDepth',             c_uint32      ),
		('dwMipMapCount',       c_uint32      ),
		('dwReserved1',         c_uint32 * 11 ),
		('pf_Size',             c_uint32      ),
		('pf_Flags',            c_uint32      ),
		('pf_FourCC',           c_uint32      ),
		('pf_RGBBitCount',      c_uint32      ),
		('pf_RBitMask',         c_uint32      ),
		('pf_GBitMask',         c_uint32      ),
		('pf_BBitMask',         c_uint32      ),
		('pf_ABitMask',         c_uint32      ),
		('dwCaps',              c_uint32      ),
		('dwCaps2',             c_uint32      ),
		('dwCaps3',             c_uint32      ),
		('dwCaps4',             c_uint32      ),
		('dwReserved2',         c_uint32      ),
		]



class BSMA_Section_0_9_12(Base_JSON_Section):
	header = 'BSMA'
	int1 = 1

	_fields_ = [
		(list, 'materials', MaterialInfo ),
		(list, 'fx',        '<I'         ),
		(list, 'props',     PropertyInfo ),
		(list, 'matrices',  '<16f'       ),
		(list, 'vectors',   '<4f'        ),
		]
