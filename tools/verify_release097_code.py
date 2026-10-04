"""CPython 2.7 gate: packaged executable code equals the accepted 0.9.7 code."""
from __future__ import print_function
import hashlib
import json
import marshal
import os
import sys
import types
import zipfile

FIELDS = ('co_argcount', 'co_nlocals', 'co_stacksize', 'co_flags', 'co_code',
          'co_consts', 'co_names', 'co_varnames', 'co_name', 'co_freevars', 'co_cellvars')


def freeze(value):
    if isinstance(value, types.CodeType):
        return tuple((key, freeze(getattr(value, key))) for key in FIELDS)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (int, long)):
        return ('integer', str(value))
    if isinstance(value, frozenset):
        return ('frozenset', tuple(sorted((freeze(item) for item in value), key=repr)))
    return value


def digest(code):
    return hashlib.sha256(repr(freeze(code))).hexdigest()


if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    expected = json.load(open(os.path.join(root, 'tests/release097/accepted_code.json')))
    with zipfile.ZipFile(sys.argv[1]) as archive:
        names = set(n for n in archive.namelist() if n.endswith('.pyc'))
        assert names == set(expected), 'Accepted client module inventory changed'
        for name in sorted(names):
            actual = digest(marshal.loads(archive.read(name)[8:]))
            assert actual == expected[name], 'Accepted client executable code differs: ' + name
    print('Accepted client executable code verified:', len(expected), 'modules')
