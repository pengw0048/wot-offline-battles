#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import struct
from typing import Optional
import xml.etree.ElementTree as ET
import zipfile
import zlib


TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SOURCE_MEMBER = 'gui/flash/trainingWindow.swf'
OUTPUT_PATH = 'src/res/gui/flash/trainingWindow.swf'


class SWFError(ValueError):
    pass


class _Bits(object):

    def __init__(self, payload, offset):
        self._payload = payload
        self._bit = offset * 8

    @property
    def offset(self):
        return (self._bit + 7) // 8

    def read_unsigned(self, width):
        value = 0
        for unused_index in range(width):
            byte = self._payload[self._bit // 8]
            value = (value << 1) | ((byte >> (7 - self._bit % 8)) & 1)
            self._bit += 1
        return value

    def skip_signed(self, width):
        self._bit += width

    def align(self):
        self._bit = ((self._bit + 7) // 8) * 8


@dataclass(frozen=True)
class _EditText:
    character_id: int
    flags: int
    variable_name: str
    initial_text: str
    color_offset: int
    color: bytes


@dataclass(frozen=True)
class _Placement:
    sprite_id: int
    depth: int
    character_id: int
    name: Optional[str]


def _skip_rect(payload, offset):
    bits = _Bits(payload, offset)
    width = bits.read_unsigned(5)
    for unused_value in range(4):
        bits.skip_signed(width)
    bits.align()
    return bits.offset


def _skip_matrix(payload, offset):
    bits = _Bits(payload, offset)
    if bits.read_unsigned(1):
        width = bits.read_unsigned(5)
        bits.skip_signed(width)
        bits.skip_signed(width)
    if bits.read_unsigned(1):
        width = bits.read_unsigned(5)
        bits.skip_signed(width)
        bits.skip_signed(width)
    width = bits.read_unsigned(5)
    bits.skip_signed(width)
    bits.skip_signed(width)
    bits.align()
    return bits.offset


def _skip_color_transform(payload, offset):
    bits = _Bits(payload, offset)
    has_add = bits.read_unsigned(1)
    has_multiply = bits.read_unsigned(1)
    width = bits.read_unsigned(4)
    if has_multiply:
        for unused_value in range(4):
            bits.skip_signed(width)
    if has_add:
        for unused_value in range(4):
            bits.skip_signed(width)
    bits.align()
    return bits.offset


def _read_c_string(payload, offset, limit):
    end = payload.find(b'\0', offset, limit)
    if end < 0:
        raise SWFError('unterminated SWF string')
    try:
        value = payload[offset:end].decode('utf-8')
    except UnicodeDecodeError as error:
        raise SWFError('invalid UTF-8 in SWF string') from error
    return value, end + 1


def _iter_tags(payload, start, end, sprite_id=0):
    offset = start
    while offset < end:
        if offset + 2 > end:
            raise SWFError('truncated SWF tag header')
        tag_start = offset
        header = struct.unpack_from('<H', payload, offset)[0]
        offset += 2
        tag_code = header >> 6
        length = header & 0x3F
        if length == 0x3F:
            if offset + 4 > end:
                raise SWFError('truncated long SWF tag header')
            length = struct.unpack_from('<I', payload, offset)[0]
            offset += 4
        body_start = offset
        body_end = body_start + length
        if body_end > end:
            raise SWFError('SWF tag extends beyond its container')
        yield tag_code, tag_start, body_start, body_end, sprite_id
        if tag_code == 39:
            if length < 4:
                raise SWFError('truncated DefineSprite tag')
            child_id = struct.unpack_from('<H', payload, body_start)[0]
            yield from _iter_tags(
                payload, body_start + 4, body_end, sprite_id=child_id)
        offset = body_end
        if tag_code == 0:
            return
    raise SWFError('SWF tag stream has no End tag')


def _parse_edit_text(payload, body_start, body_end):
    if body_end - body_start < 5:
        raise SWFError('truncated DefineEditText tag')
    character_id = struct.unpack_from('<H', payload, body_start)[0]
    offset = _skip_rect(payload, body_start + 2)
    flags = struct.unpack_from('>H', payload, offset)[0]
    offset += 2
    if flags & 0x0100:
        offset += 2
    if flags & 0x0080:
        unused_font_class, offset = _read_c_string(
            payload, offset, body_end)
    if flags & 0x0180:
        offset += 2
    color_offset = -1
    color = b''
    if flags & 0x0400:
        color_offset = offset
        color = bytes(payload[offset:offset + 4])
        offset += 4
    if flags & 0x0200:
        offset += 2
    if flags & 0x0020:
        offset += 9
    variable_name, offset = _read_c_string(payload, offset, body_end)
    initial_text = ''
    if flags & 0x8000:
        initial_text, offset = _read_c_string(payload, offset, body_end)
    if offset != body_end:
        raise SWFError(
            'unexpected trailing bytes in DefineEditText id %d' %
            character_id)
    return _EditText(
        character_id, flags, variable_name, initial_text,
        color_offset, color)


def _parse_place_object3(payload, body_start, body_end, sprite_id):
    if body_end - body_start < 4:
        raise SWFError('truncated PlaceObject3 tag')
    flags1 = payload[body_start]
    flags2 = payload[body_start + 1]
    depth = struct.unpack_from('<H', payload, body_start + 2)[0]
    offset = body_start + 4
    has_character = bool(flags1 & 0x02)
    has_class_name = bool(flags2 & 0x08)
    has_image = bool(flags2 & 0x10)
    if has_class_name or (has_image and has_character):
        unused_class_name, offset = _read_c_string(
            payload, offset, body_end)
    character_id = None
    if has_character:
        character_id = struct.unpack_from('<H', payload, offset)[0]
        offset += 2
    if flags1 & 0x04:
        offset = _skip_matrix(payload, offset)
    if flags1 & 0x08:
        offset = _skip_color_transform(payload, offset)
    if flags1 & 0x10:
        offset += 2
    name = None
    if flags1 & 0x20:
        name, offset = _read_c_string(payload, offset, body_end)
    return _Placement(sprite_id, depth, character_id, name)


def _decompress_swf(payload):
    if len(payload) < 8:
        raise SWFError('truncated SWF header')
    if payload[:3] == b'CWS':
        try:
            result = b'FWS' + payload[3:8] + zlib.decompress(payload[8:])
        except zlib.error as error:
            raise SWFError('invalid CWS payload') from error
    elif payload[:3] == b'FWS':
        result = payload
    else:
        raise SWFError('expected an FWS or CWS file')
    declared_length = struct.unpack_from('<I', result, 4)[0]
    if declared_length != len(result):
        raise SWFError(
            'SWF length mismatch: header %d, decoded %d' %
            (declared_length, len(result)))
    return result


def _compress_swf(payload, version):
    result = b'CWS' + bytes((version,)) + payload[4:8]
    result += zlib.compress(payload[8:], level=9)
    if _decompress_swf(result) != payload:
        raise SWFError('generated CWS does not round-trip')
    return result


def _validate_client(client_root):
    version_path = client_root / 'version.xml'
    package_path = client_root / 'res' / 'packages' / 'gui.pkg'
    if not version_path.is_file() or not package_path.is_file():
        raise ValueError('client must contain version.xml and res/packages/gui.pkg')
    version_root = ET.parse(str(version_path)).getroot()
    version_element = version_root.find('version')
    version_text = ''.join(version_element.itertext()).strip()
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', version_text)
    if match is None:
        raise ValueError('unrecognized version.xml value: %r' % version_text)
    if match.groups() != (TARGET_VERSION, TARGET_BUILD):
        raise ValueError(
            'client must be %s #%s, got %s #%s' %
            (TARGET_VERSION, TARGET_BUILD, match.group(1), match.group(2)))
    return package_path


def build(client_root, output_path):
    package_path = _validate_client(client_root)
    with zipfile.ZipFile(str(package_path), 'r') as archive:
        try:
            source = archive.read(SOURCE_MEMBER)
        except KeyError as error:
            raise ValueError('gui.pkg has no %s' % SOURCE_MEMBER) from error

    decoded = bytearray(_decompress_swf(source))
    if decoded[3] != 17:
        raise SWFError('expected SWF version 17, got %d' % decoded[3])
    tag_start = _skip_rect(decoded, 8) + 4
    edit_texts = {}
    placements = []
    for tag_code, unused_tag_start, body_start, body_end, sprite_id in \
            _iter_tags(decoded, tag_start, len(decoded)):
        if tag_code == 37:
            edit_text = _parse_edit_text(decoded, body_start, body_end)
            if edit_text.character_id in edit_texts:
                raise SWFError(
                    'duplicate DefineEditText id %d' %
                    edit_text.character_id)
            edit_texts[edit_text.character_id] = edit_text
        elif tag_code == 70:
            placements.append(_parse_place_object3(
                decoded, body_start, body_end, sprite_id))

    expected_texts = {
        10: (0x6CB1, '', b'\xc9\xc9\xb6\xff'),
        14: (0xECB1, '#menu:training/create/maxPlayers',
             b'\x96\x96\x87\xff'),
    }
    for character_id, expected in expected_texts.items():
        actual = edit_texts.get(character_id)
        if actual is None:
            raise SWFError('DefineEditText id %d is missing' % character_id)
        observed = (actual.flags, actual.initial_text, actual.color)
        if observed != expected:
            raise SWFError(
                'DefineEditText id %d contract changed: %r' %
                (character_id, observed))
        if actual.variable_name:
            raise SWFError(
                'DefineEditText id %d unexpectedly has variable %r' %
                (character_id, actual.variable_name))

    expected_placements = {
        _Placement(16, 12, 10, 'maxPlayers'),
        _Placement(16, 16, 14, None),
    }
    relevant_placements = [
        placement for placement in placements
        if (placement.character_id in expected_texts or
            placement.name == 'maxPlayers')
    ]
    if (len(relevant_placements) != len(expected_placements) or
            set(relevant_placements) != expected_placements):
        raise SWFError(
            'training text placements changed: %r' % relevant_placements)

    original = bytes(decoded)
    changed_offsets = []
    for character_id in sorted(expected_texts):
        alpha_offset = edit_texts[character_id].color_offset + 3
        if decoded[alpha_offset] != 0xFF:
            raise SWFError(
                'DefineEditText id %d alpha is not opaque' % character_id)
        decoded[alpha_offset] = 0
        changed_offsets.append(alpha_offset)

    differences = [
        index for index, (before, after) in enumerate(zip(original, decoded))
        if before != after
    ]
    if differences != changed_offsets:
        raise SWFError(
            'decoded output changed unexpected bytes: %r' % differences)
    for offset in changed_offsets:
        if original[offset] != 0xFF or decoded[offset] != 0:
            raise SWFError('alpha edit is not 255 -> 0 at %d' % offset)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_compress_swf(bytes(decoded), source[3]))
    written = _decompress_swf(output_path.read_bytes())
    written_differences = [
        index for index, (before, after) in enumerate(zip(original, written))
        if before != after
    ]
    if len(written) != len(original) or written_differences != changed_offsets:
        raise SWFError('written SWF failed the two-byte decoded diff check')
    print(
        'Built %s from %s; decoded alpha edits: %s' %
        (output_path, SOURCE_MEMBER, ', '.join(
            '%d:255->0' % offset for offset in changed_offsets)))


def main():
    parser = argparse.ArgumentParser(
        description='Build the #1513 offline map picker Flash resource.')
    parser.add_argument(
        'client_root', type=Path,
        help='Chinese HD 0.9.22.0.1 #1513 client root')
    parser.add_argument(
        '--output', type=Path,
        default=Path(__file__).resolve().parents[1] / OUTPUT_PATH)
    args = parser.parse_args()
    try:
        build(args.client_root.expanduser().resolve(), args.output.resolve())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
