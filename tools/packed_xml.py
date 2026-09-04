#!/usr/bin/env python3
"""Read and write the BigWorld Packed XML format used by the pinned client."""

import io
import struct


PACKED_XML_MAGIC = b"\x45\x4e\xa1\x62"
TYPE_ELEMENT = 0
TYPE_STRING = 1
TYPE_INTEGER = 2
TYPE_VECTOR = 3
TYPE_BOOLEAN = 4
TYPE_COMPRESSED_STRING = 5
DESCRIPTOR_OFFSET_MASK = 0x0FFFFFFF

class PackedValue(object):
    def __init__(self, value_type, value):
        self.value_type = int(value_type)
        self.value = value


class PackedElement(object):
    def __init__(self, value=None, children=None):
        self.value = value or PackedValue(TYPE_STRING, b"")
        self.children = list(children or ())


def _read_exact(reader, size):
    data = reader.read(size)
    if len(data) != size:
        raise ValueError("unexpected end of Packed XML")
    return data


def _read_cstring(reader):
    result = bytearray()
    while True:
        byte = _read_exact(reader, 1)
        if byte == b"\0":
            return bytes(result)
        result.extend(byte)


def _descriptor(raw):
    return raw >> 28, raw & DESCRIPTOR_OFFSET_MASK


def _read_scalar(reader, value_type, size):
    raw = _read_exact(reader, size)
    if value_type == TYPE_STRING:
        return PackedValue(value_type, raw)
    if value_type == TYPE_INTEGER:
        if not raw:
            value = 0
        elif len(raw) in (1, 2, 4, 8):
            value = int.from_bytes(raw, "little", signed=True)
        else:
            raise ValueError("invalid Packed XML integer length %d" % len(raw))
        return PackedValue(value_type, value)
    if value_type == TYPE_VECTOR:
        if len(raw) % 4:
            raise ValueError("invalid Packed XML vector length %d" % len(raw))
        return PackedValue(value_type, raw)
    if value_type == TYPE_BOOLEAN:
        if len(raw) not in (0, 1):
            raise ValueError("invalid Packed XML boolean length %d" % len(raw))
        return PackedValue(value_type, bool(raw and raw[0]))
    if value_type == TYPE_COMPRESSED_STRING:
        return PackedValue(value_type, raw)
    raise ValueError("unknown Packed XML value type %d" % value_type)


def _read_element(reader, dictionary):
    start = reader.tell()
    child_count = struct.unpack("<H", _read_exact(reader, 2))[0]
    self_descriptor = _descriptor(struct.unpack("<I", _read_exact(reader, 4))[0])
    child_descriptors = []
    for unused in range(child_count):
        name_index, raw_descriptor = struct.unpack("<HI", _read_exact(reader, 6))
        if name_index >= len(dictionary):
            raise ValueError("Packed XML dictionary index out of range")
        child_descriptors.append((dictionary[name_index], _descriptor(raw_descriptor)))

    offset = 0

    def read_value(value_type, end_offset):
        nonlocal offset
        size = end_offset - offset
        if size < 0:
            raise ValueError("Packed XML descriptor offsets are not monotonic")
        value_start = reader.tell()
        if value_type == TYPE_ELEMENT:
            nested, unused_nested_size = _read_element(reader, dictionary)
            value = PackedValue(TYPE_ELEMENT, nested)
        else:
            value = _read_scalar(reader, value_type, size)
        consumed = reader.tell() - value_start
        if consumed != size:
            raise ValueError(
                "Packed XML value consumed %d bytes, expected %d" % (consumed, size)
            )
        offset = end_offset
        return value

    root_value = read_value(*self_descriptor)
    children = []
    for name, child_descriptor in child_descriptors:
        children.append((name, read_value(*child_descriptor)))
    element = PackedElement(root_value, children)
    return element, reader.tell() - start


def read_packed_xml(data):
    reader = io.BytesIO(data)
    if _read_exact(reader, 4) != PACKED_XML_MAGIC:
        raise ValueError("invalid Packed XML magic")
    _read_exact(reader, 1)  # Historical format byte; zero in this client.
    dictionary = []
    while True:
        name = _read_cstring(reader)
        if not name:
            break
        dictionary.append(name)
    element, unused = _read_element(reader, dictionary)
    if reader.read(1):
        raise ValueError("trailing bytes after Packed XML root element")
    return element


def _minimal_signed_integer(value):
    if value == 0:
        return b""
    for size in (1, 2, 4, 8):
        low = -(1 << (size * 8 - 1))
        high = (1 << (size * 8 - 1)) - 1
        if low <= value <= high:
            return int(value).to_bytes(size, "little", signed=True)
    raise ValueError("Packed XML integer is outside signed 64-bit range")


def _collect_dictionary(element, names, indices):
    for name, value in element.children:
        if name not in indices:
            indices[name] = len(names)
            names.append(name)
        if value.value_type == TYPE_ELEMENT:
            _collect_dictionary(value.value, names, indices)


def _encode_value(value, dictionary):
    value_type = value.value_type
    if value_type == TYPE_ELEMENT:
        return value_type, _encode_element(value.value, dictionary)
    if value_type in (TYPE_STRING, TYPE_VECTOR, TYPE_COMPRESSED_STRING):
        return value_type, bytes(value.value)
    if value_type == TYPE_INTEGER:
        return value_type, _minimal_signed_integer(value.value)
    if value_type == TYPE_BOOLEAN:
        return value_type, b"\x01" if value.value else b""
    raise ValueError("cannot encode Packed XML value type %d" % value_type)


def _make_descriptor(value_type, end_offset):
    if end_offset > DESCRIPTOR_OFFSET_MASK:
        raise ValueError("Packed XML element exceeds descriptor capacity")
    return (int(value_type) << 28) | int(end_offset)


def _encode_element(element, dictionary):
    self_type, self_data = _encode_value(element.value, dictionary)
    values = [(self_type, self_data)]
    for unused_name, child in element.children:
        values.append(_encode_value(child, dictionary))

    offset = 0
    descriptors = []
    for value_type, data in values:
        offset += len(data)
        descriptors.append(_make_descriptor(value_type, offset))

    header = bytearray(struct.pack("<HI", len(element.children), descriptors[0]))
    for index, (name, unused_value) in enumerate(element.children):
        header.extend(struct.pack("<HI", dictionary[name], descriptors[index + 1]))
    return bytes(header) + b"".join(data for unused_type, data in values)


def write_packed_xml(element):
    names = []
    dictionary = {}
    _collect_dictionary(element, names, dictionary)
    # BigWorld's packer writes the shared name dictionary in bytewise lexical
    # order, independently of element traversal order.  Rebuild those indices
    # so an unchanged stock document serializes to its original bytes.
    names.sort()
    dictionary = dict((name, index) for index, name in enumerate(names))
    prefix = bytearray(PACKED_XML_MAGIC + b"\0")
    for name in names:
        prefix.extend(name + b"\0")
    prefix.extend(b"\0")
    return bytes(prefix) + _encode_element(element, dictionary)
