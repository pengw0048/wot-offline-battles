""" BSG2 (?) """

from .._base_binary_section import *



class BSG2_Section_1_1_0(Base_Binary_Section):
	header = 'BSG2'
	int1 = 2

	def from_bin_stream(self, stream, row):
		stream.seek(row.position)
		self._data = stream.read(row.length)
		self._exist = True

	def to_bin(self):
		return self._data
