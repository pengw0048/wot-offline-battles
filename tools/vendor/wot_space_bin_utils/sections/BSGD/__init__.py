""" BSGD (?) """

from .._base_binary_section import *



__all__ = ('BSGD_Section_0_9_14',)



class BSGD_Section_0_9_14(Base_Binary_Section):
	header = 'BSGD'
	int1 = 2

	def from_bin_stream(self, stream, row):
		stream.seek(row.position)
		self._data = stream.read(row.length)
		self._exist = True

	def to_bin(self):
		return self._data
