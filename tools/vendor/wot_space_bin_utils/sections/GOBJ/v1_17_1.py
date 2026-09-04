""" GOBJ (?) """

from ctypes import c_float, c_uint32, c_uint64
from .._base_json_section import *



class ComplexObject_v1_17_1(CStructure):
    _size_ = 96

    _fields_ = [
        ('resource_fnv',  c_uint32     ),
        ('unknown',       c_uint32     ), # always 0
        ('instance_uuid', c_uint64 * 2 ), # instanceUUID
        ('transform',     c_float * 16 ),
        ('unknown_2',     c_uint32     ), # ??? new in v1_17_1
        ('unknown_3',     c_uint32     ), # ??? new in v1_17_1
        ]



class GOBJ_Section_1_17_1(Base_JSON_Section):
    header = 'GOBJ'
    int1 = 3

    @row_seek(True)
    def from_bin_stream(self, stream, row):
        ''' FIXME! '''
        self._data = {}
        self.read_vector(stream, '1', ComplexObject_v1_17_1)
        self.read_vector(stream, '2', '<22I')
        rest_len = row.position + row.length - stream.tell()
        self._data['3'] = stream.read(rest_len).hex()

    def to_bin(self):
        ''' FIXME! '''
        res = b''
        res += self.write_vector('1', ComplexObject_v1_17_1)
        res += self.write_vector('2', '<22I')
        res += bytes.fromhex(self._data['3'])
        return res