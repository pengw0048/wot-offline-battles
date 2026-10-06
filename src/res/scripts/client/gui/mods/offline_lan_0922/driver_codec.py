"""Plain binary values for the authenticated private player-driver link.

The exact Python 2 client uses the native codec. Python 3 has a reference
implementation for host contract tests, with the same tags and bounds.
"""
import math
import marshal
import struct
import sys

MAGIC = b'WDP2'
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 32
try:
    _INTS = (int, long)
    _TEXT = unicode
except NameError:
    _INTS = (int,)
    _TEXT = str



def _text(value):
    if type(value) is bytes:
        value = value.decode('utf-8')
    if type(value) is not _TEXT:
        raise ValueError('driver text must be UTF-8')
    return value.encode('utf-8')


def _reference_encode(value):
    parts = [MAGIC]
    total = [len(MAGIC)]

    def put(value):
        total[0] += len(value)
        if total[0] > MAX_BYTES:
            raise ValueError('driver value is too large')
        parts.append(value)

    def visit(value, depth):
        if depth > MAX_DEPTH:
            raise ValueError('driver value is too deep')
        kind = type(value)
        if value is None:
            put(b'n')
        elif kind is bool:
            put(b't' if value else b'f')
        elif kind in _INTS:
            if not -(2 ** 63) <= value < 2 ** 64:
                raise ValueError('driver integer exceeds int64/uint64')
            if value < 2 ** 63:
                put(b'i' + struct.pack('<q', value))
            else:
                put(b'u' + struct.pack('<Q', value))
        elif kind is float:
            if math.isnan(value) or math.isinf(value):
                raise ValueError('driver number must be finite')
            put(b'd' + struct.pack('<d', value))
        elif kind in (bytes, _TEXT):
            text = _text(value)
            put(b's' + struct.pack('<I', len(text)) + text)
        elif kind in (list, tuple):
            put(b'l' + struct.pack('<I', len(value)))
            for item in value:
                visit(item, depth + 1)
        elif kind is dict:
            put(b'm' + struct.pack('<I', len(value)))
            keys = set()
            for key, item in value.items():
                if type(key) in _INTS:
                    if not -(2 ** 63) <= key < 2 ** 64:
                        raise ValueError('driver object key exceeds int64/uint64')
                    key = str(key)
                text = _text(key)
                if text in keys:
                    raise ValueError('duplicate driver key')
                keys.add(text)
                put(b's' + struct.pack('<I', len(text)) + text)
                visit(item, depth + 1)
        else:
            raise ValueError('driver value is not plain data')
    visit(value, 0)
    return b''.join(parts)


def _reference_decode(data):
    if type(data) is not bytes or not 5 <= len(data) <= MAX_BYTES:
        raise ValueError('invalid driver bytes')
    if data[:4] != MAGIC:
        raise ValueError('invalid driver magic')
    at = [4]

    def take(count):
        start = at[0]
        if count > len(data) - start:
            raise ValueError('truncated driver value')
        at[0] += count
        return data[start:at[0]]

    def count():
        value = struct.unpack('<I', take(4))[0]
        if value > len(data) - at[0]:
            raise ValueError('invalid driver container size')
        return value

    def text():
        length = struct.unpack('<I', take(4))[0]
        return take(length).decode('utf-8')

    def visit(depth):
        if depth > MAX_DEPTH:
            raise ValueError('driver value is too deep')
        tag = take(1)
        if tag == b'n':
            return None
        if tag in (b't', b'f'):
            return tag == b't'
        if tag == b'i':
            return struct.unpack('<q', take(8))[0]
        if tag == b'u':
            return struct.unpack('<Q', take(8))[0]
        if tag == b'd':
            value = struct.unpack('<d', take(8))[0]
            if math.isnan(value) or math.isinf(value):
                raise ValueError('driver number must be finite')
            return value
        if tag == b's':
            return text()
        if tag == b'l':
            return [visit(depth + 1) for unused in range(count())]
        if tag == b'm':
            out = {}
            for unused in range(count()):
                if take(1) != b's':
                    raise ValueError('invalid driver object key')
                key = text()
                if key in out:
                    raise ValueError('duplicate driver key')
                out[key] = visit(depth + 1)
            return out
        raise ValueError('invalid driver tag')
    result = visit(0)
    if at[0] != len(data):
        raise ValueError('trailing driver bytes')
    return result


def _native():
    # Production is only embedded CPython 2.7.7. Missing matching native APIs
    # are a local bridge startup failure, never a slower alternate wire path.
    backend = sys.modules.get('offline_math_batch_native')
    if backend is None:
        from gui.mods.offline_lan_0922 import native_math
        backend = native_math._load()
    if backend is None:
        raise RuntimeError('matching native driver codec is unavailable')
    return backend


def check_available():
    if sys.version_info[0] == 2:
        backend = _native()
        if (not callable(getattr(backend, 'driver_encode', None)) or
                not callable(getattr(backend, 'driver_decode', None))):
            raise ImportError('matching native driver codec is unavailable')


def encode(value):
    if sys.version_info[0] == 2:
        result = _native().driver_encode(value)
        if type(result) is not bytes:
            raise ValueError('native driver encode rejected plain data')
        return result
    return _reference_encode(value)


def decode(data):
    if sys.version_info[0] == 2:
        result = _native().driver_decode(data, marshal.loads)
        if type(result) is not tuple or len(result) != 1:
            raise ValueError('native driver decode rejected bytes')
        return result[0]
    return _reference_decode(data)
