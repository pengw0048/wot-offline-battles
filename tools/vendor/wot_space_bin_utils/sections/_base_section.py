import os



class Base_Section:
	_exist = None
	_data = None

	def __init__(self, *args):
		self._exist = False
		if len(args) == 2:
			self.from_bin_stream(*args)
		elif (len(args) == 1) and os.path.isdir(args[0]):
			self.from_dir(*args)

	def from_bin_stream(self, stream, row):
		raise NotImplementedError()

	def unp_to_dir(self, unp_dir):
		raise NotImplementedError()

	def from_dir(self, unp_dir):
		raise NotImplementedError()

	def to_bin(self):
		raise NotImplementedError()
