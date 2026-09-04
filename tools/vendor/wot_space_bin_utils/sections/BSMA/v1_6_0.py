""" BSMA (Static Materials) """

from struct import unpack, pack
from ctypes import c_uint32, c_int32
from .._base_json_section import *
from .v0_9_12 import PropertyInfo, DDS_HEADER



class MaterialInfo_1_6_0(CStructure):
	_size_ = 16

	_fields_ = [
		('key_fx',         c_int32  ),
		('key_from',       c_int32  ),
		('key_to',         c_int32  ),
		('identifier_fnv', c_uint32 ),
		]

	_tests_ = {
		'key_fx': { '>=': -1 },
		'key_from': { '>=': -1 },
		'key_to': { '>=': -1 },
		}



class BSMA_Section_1_6_0(Base_JSON_Section):
	header = 'BSMA'
	int1 = 1

	@row_seek(True)
	def from_bin_stream(self, stream, row):
		self._data = {}
		self._data['materials'] = self.read_entries(stream, MaterialInfo_1_6_0)
		self._data['fx'] = self.read_entries(stream, 4, '<I')
		self._data['props'] = self.read_entries(stream, PropertyInfo)
		self._data['matrices'] = self.read_entries(stream, 64, '<16f')
		self._data['vectors'] = self.read_entries(stream, 16, '<4f')
		textures = []
		textures_cnt = unpack('<I', stream.read(4))[0]
		for _ in range(textures_cnt):
			tex_fnv, _2, mip_map_count, length = unpack('<4I', stream.read(16))
			assert _2 in (0, 1, 2, 3, 4, 5, 6, 7), _2
			data = stream.read(length)
			assert data[:4] == b'DDS '
			header = DDS_HEADER(data[4:128]).to_dict()
			# TODO: failed in "121_lost_paradise_v":
			# assert mip_map_count == header['dwMipMapCount'], mip_map_count
			texformat_len = unpack('<I', stream.read(4))[0]
			texformat = stream.read(texformat_len)
			str_length = unpack('<I', stream.read(4))[0]
			str_data = stream.read(str_length).decode('ascii')
			textures.append({
				'tex_fnv': tex_fnv,
				'_2': _2,
				'mip_map_count': mip_map_count,
				'length': length,
				'dds_header': header,
				'dds_data': data[128:].decode('latin1'),
				'texformat_len': texformat_len,
				'texformat': texformat.decode('latin1'),
				'str_length': str_length,
				'str_data': str_data,
			})
		self._data['textures'] = textures

	def to_bin(self):
		res = self.write_entries(self._data['materials'], MaterialInfo_1_6_0)
		res += self.write_entries(self._data['fx'], 4, '<I')
		res += self.write_entries(self._data['props'], PropertyInfo)
		res += self.write_entries(self._data['matrices'], 64, '<16f')
		res += self.write_entries(self._data['vectors'], 16, '<4f')
		res += pack('<I', len(self._data['textures']))
		for tex in self._data['textures']:
			res += pack(
				'<4I',
				tex['tex_fnv'],
				tex['_2'],
				tex['mip_map_count'],
				tex['length']
			)
			res += b'DDS '
			res += DDS_HEADER(tex['dds_header']).to_bin()
			res += tex['dds_data'].encode('latin1')
			res += pack('<I', tex['texformat_len'])
			res += tex['texformat'].encode('latin1')
			res += pack('<I', tex['str_length'])
			res += tex['str_data'].encode('ascii')
		return res
