""" BWST (String Table) """

from struct import unpack, pack
from .._base_json_section import *



__all__ = ('BWST_Section_0_9_12',)



class BWST_Section_0_9_12(Base_JSON_Section):
	header = 'BWST'
	int1 = 2

	@row_seek(False)
	def from_bin_stream(self, stream, row):
		self._data = {}
		entries = self.read_entries(stream, 12, '<3I')
		strings_size = unpack('<I', stream.read(4))[0]
		strings_start = stream.tell()
		for key, offset, length in entries:
			stream.seek(strings_start + offset)
			string = stream.read(length)
			self._data[getHash(string)] = string.decode('latin-1')

	def to_bin(self):
		res = pack('<2I', 12, len(self._data))
		offset = 0
		_list = []
		for _hash, _str in self._data.items():
			enc_str = _str.encode('latin-1')
			_list.append((getHash(enc_str), enc_str))
		_list.sort()
		for _hash, string in _list:
			res += pack('<3I', _hash, offset, len(string))
			offset += len(string) + 1
		res += pack('<I', offset)
		for _, string in _list:
			res += string + b'\x00'
		return res

	def add_str(self, _str):
		_hash = getHash(_str.encode('latin-1'))
		self._data[_hash] = _str
		return _hash

	def get(self, key):
		return self._data.get(key)
