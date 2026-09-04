""" WGSD (Decal) """

from struct import unpack, pack
from ctypes import c_float, c_uint8, c_uint32
from xml.etree import ElementTree as ET
from .._base_json_section import *
from .v0_9_12 import Decal2Info



class DecalInfo_TYPE1_v1_0_0(CStructure):
	_size_ = 127
	_pack_ = 1

	_fields_ = [
		('_1',                 c_uint32     ),
		('_2',                 c_uint32     ),
		('accurate',           c_uint8      ), # accurate
		('transform',          c_float * 16 ),
		('diff_tex_fnv',       c_uint32     ), # diffTex
		('bump_tex_fnv',       c_uint32     ), # bumpTex
		('hm_tex_fnv',         c_uint32     ), # hmTex
		('add_tex_fnv',        c_uint32     ), # addTex
		('priority',           c_uint32     ), # priority
		('influence',          c_uint8      ), # influence
		('_3',                 c_uint8      ), # type ?
		('offsets',            c_float * 4  ), # offsets
		('uv_wrapping',        c_float * 2  ), # uvWrapping
		('visibility_mask',    c_uint32     ), # visibilityMask
		('tiles_fade',         c_float      ), # tilesFade
		#('parallax_offset',    c_float      ), # -1 - parallax_offset
		#('parallax_amplitude', c_float      ), # parallax_amplitude
		]


class DecalInfo_TYPE3_v1_0_0(CStructure):
	_size_ = 135
	_pack_ = 1

	_fields_ = [
		('_1',                 c_uint32     ),
		('_2',                 c_uint32     ),
		('accurate',           c_uint8      ), # accurate
		('transform',          c_float * 16 ),
		('diff_tex_fnv',       c_uint32     ), # diffTex
		('bump_tex_fnv',       c_uint32     ), # bumpTex
		('hm_tex_fnv',         c_uint32     ), # hmTex
		('add_tex_fnv',        c_uint32     ), # addTex
		('priority',           c_uint32     ), # priority
		('influence',          c_uint8      ), # influence
		('_3',                 c_uint8      ), # type ?
		('offsets',            c_float * 4  ), # offsets
		('uv_wrapping',        c_float * 2  ), # uvWrapping
		('visibility_mask',    c_uint32     ), # visibilityMask
		('tiles_fade',         c_float      ), # tilesFade
		('parallax_offset',    c_float      ), # -1 - parallax_offset
		('parallax_amplitude', c_float      ), # parallax_amplitude
		]



class WGSD_Section_1_0_0(Base_JSON_Section):
	header = 'WGSD'
	int1 = 2

	@row_seek(True)
	def from_bin_stream(self, stream, row):
		''' FIXME! '''

		self._data = {}

		int1 = unpack('<I', stream.read(4))[0]

		#print(int1)

		self._data['1_135'] = None
		self._data['1_127'] = None
		self._data['2_127'] = None
		self._data['2_135'] = None

		if int1 != 0:
			int2, int3, int4 = unpack('<3I', stream.read(12))
			#print(int2, int3, int4)

			assert int1 in (1, 2), int1
			assert int2 in (1, 3), int2

			if int2 == 3:
				self._data['1_135'] = []
				assert int4 == 135, int4
				for i in range(int3):
					self._data['1_135'].append(DecalInfo_TYPE3_v1_0_0(stream.read(int4)).to_dict())

			elif int2 == 1:
				self._data['1_127'] = []
				assert int4 == 127, int4
				for i in range(int3):
					self._data['1_127'].append(DecalInfo_TYPE1_v1_0_0(stream.read(int4)).to_dict())

			if int1 == 2:
				int5, int6, int7 = unpack('<3I', stream.read(12))
				assert (int5, int7) in ((1, 127), (3, 135)), (int5, int7)
				if int7 == 127:
					self._data['2_127'] = []
					for i in range(int6):
						self._data['2_127'].append(DecalInfo_TYPE1_v1_0_0(stream.read(int7)).to_dict())
				elif int7 == 135:
					self._data['2_135'] = []
					for i in range(int6):
						self._data['2_135'].append(DecalInfo_TYPE3_v1_0_0(stream.read(int7)).to_dict())

		# FIXME:
		self._data['3'] = self.read_entries(stream, Decal2Info)

	def to_bin(self):
		''' FIXME! '''
		res = b''

		_1_135 = self._data['1_135']
		_1_127 = self._data['1_127']
		_2_127 = self._data['2_127']
		_2_135 = self._data['2_135']

		if _2_127 or _2_135:
			res += pack('<I', 2)
		elif _1_135 or _1_127:
			res += pack('<I', 1)
		else:
			res += pack('<I', 0)

		if _1_135:
			cnt = len(_1_135)
			res += pack('<3I', 3, cnt, 135)
			for it in _1_135:
				res += DecalInfo_TYPE3_v1_0_0(it).to_bin()

		elif _1_127:
			cnt = len(_1_127)
			res += pack('<3I', 1, cnt, 127)
			for it in _1_127:
				res += DecalInfo_TYPE1_v1_0_0(it).to_bin()

		if _2_135:
			cnt = len(_2_135)
			res += pack('<3I', 3, cnt, 135)
			for it in _2_135:
				res += DecalInfo_TYPE3_v1_0_0(it).to_bin()

		if _2_127:
			cnt = len(_2_127)
			res += pack('<3I', 1, cnt, 127)
			for it in _2_127:
				res += DecalInfo_TYPE1_v1_0_0(it).to_bin()

		res += self.write_entries(self._data['3'], Decal2Info)

		return res

	def to_xml(self, chunks):
		''' FIXME! '''
		_1_135 = self._data['1_135']
		_1_127 = self._data['1_127']
		_2_127 = self._data['2_127']

		if _1_135:
			for it in _1_135:
				self.__item_to_xml(chunks, it)
		elif _1_127:
			for it in _1_127:
				self.__item_to_xml(chunks, it)

		if _2_127:
			for it in _2_127:
				self.__item_to_xml(chunks, it)

	@classmethod
	def __item_to_xml(cls, chunks, item):
		chunk, transform = chunks.get_by_transform(item['transform'])

		el = ET.SubElement(chunk, 'staticDecal')

		write = lambda *args: cls._add2xml(el, *args)

		write('accurate',       item['accurate']                  )
		write('transform',      transform                         )
		write('diffTex',        chunks.gets(item['diff_tex_fnv']) )
		write('bumpTex',        chunks.gets(item['bump_tex_fnv']) )
		write('hmTex',          chunks.gets(item['hm_tex_fnv'])   )
		write('addTex',         chunks.gets(item['add_tex_fnv'])  )
		write('priority',       item['priority']                  )
		write('influence',      item['influence']                 )
		write('visibilityMask', item['visibility_mask']           )
		write('uvWrapping',     item['uv_wrapping']               )
		write('offsets',        item['offsets']                   )
		write('tilesFade',      item['tiles_fade']                )
