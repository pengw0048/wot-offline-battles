""" BWSG (Static Geometry) """

from struct import unpack, pack
from .._base_json_section import *
from .v0_9_12 import PositionInfo, ModelInfo



class BWSG_Section_0_9_14(Base_JSON_Section):
	header = 'BWSG'
	int1 = 2

	@row_seek(True)
	def from_bin_stream(self, stream, row):
		self._data = {}
		entries = self.read_entries(stream, 12, '<3I')
		strings_size = unpack('<I', stream.read(4))[0]
		strings_start = stream.tell()
		_strings = {}
		for key, offset, length in entries:
			stream.seek(strings_start + offset)
			string = stream.read(length)
			assert getHash(string) == key
			_strings[key] = string.decode('ascii')
		stream.seek(strings_start + strings_size)
		self._data['strings'] = _strings
		self._data['models'] = self.read_entries(stream, ModelInfo)
		self._data['positions'] = self.read_entries(stream, PositionInfo)
		self._data['data_sizes'] = self.read_entries(stream, 4, '<I') # size BSGD section

	def to_bin(self):
		res = pack('<2I', 12, len(self._data['strings']))

		_list = []
		for _hash, _str in self._data['strings'].items():
			enc_str = _str.encode('ascii')
			_list.append((getHash(enc_str), enc_str))
		_list.sort()

		offset = 0
		for _hash, string in _list:
			res += pack('<3I', _hash, offset, len(string))
			offset += len(string) + 1
		res += pack('<I', offset)

		for _, string in _list:
			res += string + b'\x00'

		res += self.write_entries(self._data['models'], ModelInfo)
		res += self.write_entries(self._data['positions'], PositionInfo)
		res += self.write_entries(self._data['data_sizes'], 4, '<I')
		return res

	def add_str(self, _str):
		_hash = getHash(_str.encode('ascii'))
		self._data['strings'][_hash] = _str
		return _hash
