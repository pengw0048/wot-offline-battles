#!/usr/bin/env python3
"""Cross-check the native replay owner using independent zlib/Blowfish fixtures."""
import argparse
import ctypes
import ctypes.util
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

KEY = bytes.fromhex('de72bea0deadbeefdeadbeefdeadbeef')
CURRENT = 0x11343212
LEGACY = 0x12343210


class BlowfishKey(ctypes.Structure):
    _fields_ = [('p', ctypes.c_uint32 * 18), ('s', ctypes.c_uint32 * 1024)]


class Blowfish:
    def __init__(self, library=None):
        library = library or ctypes.util.find_library('crypto')
        if not library:
            raise RuntimeError('A real libcrypto with Blowfish is required')
        self.library = ctypes.CDLL(library)
        self.library.BF_set_key.argtypes = (ctypes.POINTER(BlowfishKey), ctypes.c_int, ctypes.c_void_p)
        self.library.BF_ecb_encrypt.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(BlowfishKey), ctypes.c_int)
        self.key = BlowfishKey()
        self.library.BF_set_key(ctypes.byref(self.key), len(KEY), KEY)

    def block(self, data, encrypt):
        assert len(data) == 8
        output = ctypes.create_string_buffer(8)
        self.library.BF_ecb_encrypt(data, output, ctypes.byref(self.key), int(encrypt))
        return output.raw

    def chain(self, data, encrypt):
        assert len(data) % 8 == 0
        output, previous = bytearray(), bytes(8)
        for offset in range(0, len(data), 8):
            block = data[offset:offset + 8]
            if encrypt:
                output.extend(self.block(bytes(a ^ b for a, b in zip(block, previous)), True))
                previous = block
            else:
                plain = bytes(a ^ b for a, b in zip(self.block(block, False), previous))
                output.extend(plain)
                previous = plain
        return bytes(output)


def word(value):
    return struct.pack('<I', value)


def packet(kind, time, payload):
    return struct.pack('<IIf', len(payload), kind, time) + payload


def string_packet(kind, value):
    value = value.encode('utf-8')
    return packet(kind, 0, word(len(value)) + value)


def current(cipher, plain, metadata, packed=None, padding=None):
    packed = zlib.compress(plain, 9) if packed is None else packed
    header = word(CURRENT) + word(len(metadata))
    header += b''.join(word(len(value)) + value for value in metadata)
    padded = packed + bytes((-len(packed)) % 8)
    if padding is not None:
        assert len(padded) > len(packed)
        padded = padded[:-1] + bytes((padding,))
    return header + word(len(plain)) + word(len(packed)) + cipher.chain(padded, True)


def unpack_current(cipher, data):
    magic, count = struct.unpack_from('<II', data)
    assert magic == CURRENT
    offset, metadata = 8, []
    for _ in range(count):
        length, = struct.unpack_from('<I', data, offset)
        metadata.append(data[offset + 4:offset + 4 + length])
        offset += 4 + length
    raw_size, packed_size = struct.unpack_from('<II', data, offset)
    padded = cipher.chain(data[offset + 8:], False)
    assert len(padded) == (packed_size + 7) // 8 * 8
    assert padded[packed_size:] == bytes(len(padded) - packed_size)
    decoder = zlib.decompressobj()
    plain = decoder.decompress(padded[:packed_size]) + decoder.flush()
    assert decoder.eof and not decoder.unused_data and len(plain) == raw_size
    return plain, metadata


def generate(root, cipher):
    plain = b''.join((string_packet(0x18, 'v.0.9.22.0 #1513'), string_packet(0x2d, '0.9.22'),
                      string_packet(0x30, 'HD'), packet(2, 1.5, b'\x00\xffpayload'),
                      packet(0xabcdef, -1, b''), packet(7, 1.5, b'same time'), packet(8, 9, b'end')))
    metadata = ['{"playerName":"测试","clientVersionFromExe":"0.9.22.0"}'.encode('utf-8'),
                b'{"result":"fixture"}']
    legacy = word(LEGACY) + word(len(plain)) + zlib.compress(plain, 9)
    encrypted = current(cipher, plain, metadata)
    fixtures = {'raw.wotreplay': plain, 'legacy.wotreplay': legacy, '当前回放.wotreplay': encrypted,
                'terminated.wotreplay': current(cipher, plain + packet(0xffffffff, 1000, b'') + b'trailer', metadata),
                'bad-count': word(CURRENT) + word(0xffffffff),
                'bad-meta-length': word(CURRENT) + word(1) + word(0xffffffff),
                'bad-raw-limit': word(LEGACY) + word(0x3200001) + b'x',
                'bad-packed-size': word(CURRENT) + word(0) + word(len(plain)) + word(99999) + bytes(8),
                'bad-alignment': encrypted[:-1], 'bad-cipher': encrypted[:-8] + bytes(8),
                'bad-zlib': word(LEGACY) + word(len(plain)) + b'not zlib',
                'bad-raw-size': word(LEGACY) + word(len(plain) + 1) + zlib.compress(plain, 9),
                'bad-packet-header': plain + b'\0',
                'bad-packet-size': packet(1, 0, b'x')[:-1],
                'bad-time': packet(1, float('nan'), b''),
                'bad-padding': current(cipher, plain, [], padding=7),
                'bad-zlib-trailing': current(cipher, plain, [], packed=zlib.compress(plain, 9) + b'junk')}
    for name, content in fixtures.items():
        (root / name).write_bytes(content)
    (root / 'protected-directory').mkdir()
    (root / 'protected-directory' / 'unchanged').write_bytes(b'keep')
    return plain, metadata


def verify_key(executable):
    data = executable.read_bytes()
    pe, = struct.unpack_from('<I', data, 0x3c)
    count, optional_size = struct.unpack_from('<H12xH', data, pe + 6)
    optional = pe + 24
    base, = struct.unpack_from('<I', data, optional + 28)
    for index in range(count):
        _, size, rva, disk_size, raw = struct.unpack_from('<8sIIII', data, optional + optional_size + 40 * index)
        if rva <= 0x1a0f490 - base < rva + max(size, disk_size):
            offset = raw + 0x1a0f490 - base - rva
            key = bytearray(data[offset:offset + 17])
            assert key == bytes.fromhex('deadbeefdeadbeefdeadbeefdeadbeef00')
            assert b'\xc6\x05' + word(0x1a0f491) + b'\x72' in data
            assert b'\xc6\x05' + word(0x1a0f493) + b'\xa0' in data
            key[1], key[3] = 0x72, 0xa0
            assert key[:-1] == KEY
            return
    raise AssertionError('The exact replay key initializer was not mapped')


def verify(root, cipher):
    plain = (root / 'raw.wotreplay').read_bytes()
    _, metadata = unpack_current(cipher, (root / '当前回放.wotreplay').read_bytes())
    assert unpack_current(cipher, (root / '输出当前.wotreplay').read_bytes()) == (plain, metadata)
    legacy = (root / 'output-legacy.wotreplay').read_bytes()
    assert struct.unpack_from('<II', legacy) == (LEGACY, len(plain)) and zlib.decompress(legacy[8:]) == plain
    assert (root / 'output-raw.wotreplay').read_bytes() == plain
    assert unpack_current(cipher, (root / 'output-terminated.wotreplay').read_bytes()) == unpack_current(cipher, (root / 'terminated.wotreplay').read_bytes())
    assert (root / 'protected-directory' / 'unchanged').read_bytes() == b'keep'
    assert not list(root.glob('*.tmp.*')) and not (root / 'metadata-must-survive').exists()
    print('Independent Blowfish/zlib fixtures verified all native output formats and failure publication boundaries')


def run(probe, cipher):
    assert cipher.chain(cipher.chain(bytes(range(64)), True), False) == bytes(range(64))
    with tempfile.TemporaryDirectory(prefix='wot-replay-probe-') as directory:
        root = Path(directory)
        generate(root, cipher)
        subprocess.run([str(probe), str(root)], check=True, timeout=45)
        verify(root, cipher)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path, nargs='?')
    parser.add_argument('--crypto-library')
    parser.add_argument('--client-executable', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only', type=Path)
    mode.add_argument('--verify-only', type=Path)
    options = parser.parse_args()
    if options.client_executable:
        verify_key(options.client_executable)
    cipher = Blowfish(options.crypto_library)
    if options.prepare_only:
        options.prepare_only.mkdir(parents=True, exist_ok=False)
        generate(options.prepare_only, cipher)
    elif options.verify_only:
        verify(options.verify_only, cipher)
    elif options.probe:
        run(options.probe, cipher)
    else:
        parser.error('Supply a native probe or a separate fixture preparation/verification mode')
