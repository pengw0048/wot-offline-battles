import json
import logging
import functools

from pathlib import Path
from struct import unpack, pack, calcsize
from xml.etree import ElementTree as ET
from ..fnvhash import fnv1a_64
from ._ctypes_utils import *
from ._base_section import *


logger = logging.getLogger(__name__)


__all__ = (
    'Base_JSON_Section', 'row_seek', 'CStructure',
    'getHash'
    )



def getHash(string):
    return fnv1a_64(string) & 0xffffffff



def row_seek(check_pos: bool):
    def my_decorator(func, *args):
        def wrapped(self, stream, row):
            assert row.header == self.header, row.header
            assert row.int1 == self.int1, (row.int1, row.header)
            if row.length == 0:
                return
            stream.seek(row.position)
            func(self, stream, row)
            if check_pos:
                assert stream.tell() == row.position + row.length, (stream.tell(), row.position + row.length, row.header)
            self._exist = True
        return wrapped
    return my_decorator



def my_unpack(*args):
    res = unpack(*args)
    return res[0] if len(res) == 1 else res



class Base_JSON_Section(Base_Section):
    def unp_to_dir(self, unp_dir: Path):
        json_file = unp_dir / f'{self.header}.json'
        with json_file.open('w') as fw:
            json.dump(self._data, fw, sort_keys=False, indent='\t')

    def from_dir(self, unp_dir: Path):
        json_file = unp_dir / f'{self.header}.json'
        self._exist = json_file.is_file()
        if not self._exist:
            return
        with json_file.open('r') as fr:
            self._data = json.load(fr)

    @staticmethod
    def read_entries(stream, *args):
        sz, cnt = unpack('<2I', stream.read(8))

        if len(args) == 1 and hasattr(args[0], 'to_bin'):
            arg = args[0]
            assert sz == arg._size_, (sz, arg._size_)
            return [arg(stream.read(arg._size_)).to_dict() for _ in range(cnt)]

        ex_sz = args[0]
        arg = args[1] if len(args) == 2 else None
        assert sz == ex_sz, (sz, ex_sz)
        if arg is None:
            return [stream.read(sz) for _ in range(cnt)]
        elif isinstance(arg, str):
            return [my_unpack(arg, stream.read(sz)) for _ in range(cnt)]
        elif callable(arg):
            return [arg(stream.read(sz)) for _ in range(cnt)]
        assert False

    @staticmethod
    def write_entries(entries, *args):
        if len(args) == 1 and hasattr(args[0], 'to_bin'):
            arg = args[0]
            res = pack('<2I', arg._size_, len(entries))
            for item in entries:
                res += arg(item).to_bin()
            return res

        ex_sz = args[0]
        arg = args[1]

        res = pack('<2I', ex_sz, len(entries))
        if isinstance(arg, str):
            sz = calcsize(arg)
            assert sz == ex_sz, (sz, ex_sz)
            for item in entries:
                if hasattr(item, '__len__'):
                    res += pack(arg, *tuple(item))
                else:
                    res += pack(arg, item)
        elif callable(arg):
            for item in entries:
                res += arg(item)
        else:
            assert False
        return res

    @row_seek(True)
    def from_bin_stream(self, stream, row):
        self._data = {}
        for prop, name, data_type in self._fields_:
            if prop == list:
                self.read_vector(stream, name, data_type)
            elif prop == dict:
                self.read_dict(stream, name, data_type)
            elif prop == int:
                sz= calcsize(data_type)
                self._data[name] = my_unpack(data_type, stream.read(sz))

    @staticmethod
    def _add2xml(el, key, val):
        new = ET.SubElement(el, key)
        if isinstance(val, (list, tuple)):
            fmt = lambda l: ' '.join(map('{0:.6f}'.format, l))
            if key == 'transform' and len(val) == 16:
                row0 = ET.SubElement(new, 'row0')
                row1 = ET.SubElement(new, 'row1')
                row2 = ET.SubElement(new, 'row2')
                row3 = ET.SubElement(new, 'row3')
                row0.text = fmt(val[0:3])
                row1.text = fmt(val[4:7])
                row2.text = fmt(val[8:11])
                row3.text = fmt(val[12:15])
            else:
                new.text = fmt(val)
        elif isinstance(val, bool):
            new.text = 'true' if val else 'false'
        elif isinstance(val, float):
            new.text = '{0:.6f}'.format(val)
        else:
            new.text = str(val)

    def to_bin(self):
        res = b''
        for prop, name, data_type in self._fields_:
            if prop == list:
                res += self.write_vector(name, data_type)
            elif prop == dict:
                res += self.write_dict(name, data_type)
            elif prop == int:
                res += pack(data_type, self._data[name])
        return res

    def read_dict(self, stream, name, data_type):
        sz = unpack('<I', stream.read(4))[0]
        if isinstance(data_type, str):
            assert sz == calcsize(data_type), (sz, calcsize(data_type), name)
            self._data[name] = my_unpack(data_type, stream.read(sz))
        elif hasattr(data_type, 'to_dict'):
            assert sz == data_type._size_, (sz, data_type._size_)
            self._data[name] = data_type(stream.read(data_type._size_)).to_dict()

    def write_dict(self, name, data_type):
        data = self._data[name]
        if isinstance(data_type, str):
            sz = calcsize(data_type)
            res = pack('<I', sz)
            if hasattr(data, '__len__'):
                res += pack(data_type, *tuple(data))
            else:
                res += pack(data_type, data)
            return res
        elif hasattr(data_type, 'to_bin'):
            res = pack('<I', data_type._size_)
            res += data_type(data).to_bin()
            return res

    def read_vector(self, stream, name, data_type):
        sz, cnt = unpack('<2I', stream.read(8))
        if isinstance(data_type, str):
            assert sz == calcsize(data_type), (sz, calcsize(data_type), name)
            self._data[name] = [my_unpack(data_type, stream.read(sz)) for _ in range(cnt)]
        elif hasattr(data_type, 'to_dict'):
            assert sz == data_type._size_, (sz, data_type._size_, name)
            self._data[name] = [data_type(stream.read(data_type._size_)).to_dict() for _ in range(cnt)]

    def write_vector(self, name, data_type):
        vector = self._data[name]
        if isinstance(data_type, str):
            sz = calcsize(data_type)
            res = pack('<2I', sz, len(vector))
            for item in vector:
                if hasattr(item, '__len__'):
                    res += pack(data_type, *tuple(item))
                else:
                    res += pack(data_type, item)
            return res
        elif hasattr(data_type, 'to_bin'):
            res = pack('<2I', data_type._size_, len(vector))
            for data in vector:
                res += data_type(data).to_bin()
            return res
