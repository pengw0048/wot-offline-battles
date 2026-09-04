""" BWSG (Static Geometry) """

from struct import unpack, pack
from ctypes import c_uint32
from pathlib import Path
from .._base_json_section import *



class PositionInfo(CStructure):
    _size_ = 20

    _fields_ = [
        ('type',          c_uint32 ), # 0 - geom, 10 - uv2, 11 - colour
        ('vstride',       c_uint32 ),
        ('size',          c_uint32 ), # size of vertices block from .primitives
        ('data_sizes_id', c_uint32 ), # index data_sizes
        ('position',      c_uint32 ), # start position in BSGD binary file
        ]

    _tests_ = {
        'type': { 'in': (0, 10, 11,) }
        }



class ModelInfo(CStructure):
    _size_ = 20

    _fields_ = [
        ('vertices_fnv', c_uint32 ),
        ('id_from',      c_uint32 ),
        ('id_to',        c_uint32 ),
        ('vcount',       c_uint32 ),
        ('vtype_fnv',    c_uint32 ),
        ]



class BWSG_Section_0_9_12(Base_JSON_Section):
    header = 'BWSG'
    int1 = 1

    @row_seek(False)
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
        self._data['data_sizes'] = self.read_entries(stream, 4, '<I')
        self._bin_data = b''
        #for sz in self._data['data_sizes']:
        #    self._bin_data += stream.read(sz)

    def unp_to_dir(self, unp_dir: Path):
        super(BWSG_Section_0_9_12, self).unp_to_dir(unp_dir)
        with (unp_dir / 'BWSG.bin').open('wb') as f:
            f.write(self._bin_data)

    def from_dir(self, unp_dir: Path):
        super(BWSG_Section_0_9_12, self).from_dir(unp_dir)
        with (unp_dir / 'BWSG.bin').open('rb') as f:
            self._bin_data = f.read()

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
        res += self._bin_data
        return res
