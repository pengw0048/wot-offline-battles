""" GOBJ (?) """

from .._base_json_section import *
from .v1_12_1 import ComplexObject_v1_12_1



class GOBJ_Section_1_31_0_RU(Base_JSON_Section):
    header = 'GOBJ'
    int1 = 4

    @row_seek(True)
    def from_bin_stream(self, stream, row):
        ''' FIXME! '''
        self._data = {}
        self.read_vector(stream, '1', ComplexObject_v1_12_1)
        self.read_vector(stream, '2', '<22I')
        rest_len = row.position + row.length - stream.tell()
        self._data['3'] = stream.read(rest_len).hex()

    def to_bin(self):
        ''' FIXME! '''
        res = b''
        res += self.write_vector('1', ComplexObject_v1_12_1)
        res += self.write_vector('2', '<22I')
        res += bytes.fromhex(self._data['3'])
        return res
