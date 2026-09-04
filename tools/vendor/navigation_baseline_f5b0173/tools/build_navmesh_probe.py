#!/usr/bin/env python3
"""Build a minimal BigWorld client-navigation probe for WoT 0.8.2.

The generated files are a res_mods overlay. They leave the original map
package untouched and add one synthetic version-0 worldNavmesh polygon around
each Lakeville team spawn. This is deliberately a loader/API probe, not a
playable navigation mesh.
"""

import argparse
import hashlib
import io
import json
import os
import struct
import zipfile


PACKED_XML_MAGIC = b"\x45\x4e\xa1\x62"
TYPE_ELEMENT = 0
TYPE_STRING = 1
TYPE_INTEGER = 2
TYPE_VECTOR = 3
TYPE_BOOLEAN = 4
TYPE_COMPRESSED_STRING = 5
DESCRIPTOR_OFFSET_MASK = 0x0FFFFFFF

MAP_NAME = "07_lakeville"
MAP_PACKAGE = MAP_NAME + ".pkg"
SPACE_PREFIX = "spaces/%s/" % MAP_NAME
PROBE_CHUNKS = {
    # Chunk coordinates are world-space metres. Keep a half-metre inset so
    # the synthetic polygon cannot be linked to a neighbouring chunk.
    "fffe0003o": (-199.5, 300.5, -100.5, 399.5),
    # The mod places the front spawn row 20 metres toward the map centre, so
    # the live player normally lands just inside these adjacent chunks.
    "fffe0002o": (-199.5, 200.5, -100.5, 299.5),
    "fffefffco": (-199.5, -399.5, -100.5, -300.5),
    "fffefffdo": (-199.5, -299.5, -100.5, -200.5),
}


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
    prefix = bytearray(PACKED_XML_MAGIC + b"\0")
    for name in names:
        prefix.extend(name + b"\0")
    prefix.extend(b"\0")
    return bytes(prefix) + _encode_element(element, dictionary)


def _name_bytes(name):
    return name.encode("utf-8") if isinstance(name, str) else bytes(name)


def _string_value(value):
    return PackedValue(TYPE_STRING, value.encode("utf-8"))


def _element_value(element=None):
    return PackedValue(TYPE_ELEMENT, element or PackedElement())


def _set_child(element, name, value):
    encoded_name = _name_bytes(name)
    for index, (current_name, unused) in enumerate(element.children):
        if current_name == encoded_name:
            element.children[index] = (encoded_name, value)
            return
    element.children.append((encoded_name, value))


def _ensure_element_child(element, name):
    encoded_name = _name_bytes(name)
    for index, (current_name, value) in enumerate(element.children):
        if current_name == encoded_name:
            if value.value_type != TYPE_ELEMENT:
                value = _element_value()
                element.children[index] = (encoded_name, value)
            return value.value
    nested = PackedElement()
    element.children.append((encoded_name, _element_value(nested)))
    return nested


def enable_client_navigation(settings_data):
    root = read_packed_xml(settings_data)
    navigation = _ensure_element_child(root, "clientNavigation")
    _set_child(navigation, "enable", PackedValue(TYPE_BOOLEAN, True))
    return write_packed_xml(root)


def add_world_navmesh_reference(chunk_data, chunk_id):
    root = read_packed_xml(chunk_data)
    navmesh = _ensure_element_child(root, "worldNavmesh")
    _set_child(
        navmesh,
        "resource",
        _string_value("%s.cdata/worldNavmesh" % chunk_id),
    )
    return write_packed_xml(root)


def build_probe_navmesh(bounds, girth=0.5):
    min_x, min_z, max_x, max_z = [float(value) for value in bounds]
    # BigWorld navpolys are clockwise in the world X/Z plane.
    vertices = (
        (max_x, min_z),
        (min_x, min_z),
        (min_x, max_z),
        (max_x, max_z),
    )
    result = bytearray(struct.pack("<ifii", 0, float(girth), 1, len(vertices)))
    # The broad height interval lets the loader match the live spawn height.
    # The returned Y is intentionally ignored: this probe only checks that the
    # native graph loads and returns X/Z path points.
    result.extend(struct.pack("<ffi", -1000.0, 1000.0, len(vertices)))
    for x, z in vertices:
        # -1 is a closed/vista edge. 65535 specifically means that the edge
        # should be linked to a matching polygon in an adjacent chunk.
        result.extend(struct.pack("<ffi", x, z, -1))
    return bytes(result)


def add_world_navmesh_to_cdata(cdata_data, navmesh_data):
    source = zipfile.ZipFile(io.BytesIO(cdata_data), "r")
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", allowZip64=False) as target:
        for info in source.infolist():
            if info.filename == "worldNavmesh":
                continue
            target.writestr(info, source.read(info.filename))
        target.writestr("worldNavmesh", navmesh_data, zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _write_file(root, relative_path, data):
    path = os.path.join(root, *relative_path.split("/"))
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as stream:
        stream.write(data)
    return path


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def build_overlay(client_root, output_root):
    package_path = os.path.join(client_root, "res", "packages", MAP_PACKAGE)
    if not os.path.isfile(package_path):
        raise ValueError("Lakeville package not found: %s" % package_path)

    manifest = {"map": MAP_NAME, "files": {}, "probe_only": True}
    with zipfile.ZipFile(package_path, "r") as package:
        settings_name = SPACE_PREFIX + "space.settings"
        settings = enable_client_navigation(package.read(settings_name))
        _write_file(output_root, settings_name, settings)
        manifest["files"][settings_name] = _sha256(settings)

        for chunk_id, bounds in sorted(PROBE_CHUNKS.items()):
            chunk_name = SPACE_PREFIX + chunk_id + ".chunk"
            cdata_name = SPACE_PREFIX + chunk_id + ".cdata"
            navmesh = build_probe_navmesh(bounds)
            chunk = add_world_navmesh_reference(package.read(chunk_name), chunk_id)
            cdata = add_world_navmesh_to_cdata(package.read(cdata_name), navmesh)
            _write_file(output_root, chunk_name, chunk)
            _write_file(output_root, cdata_name, cdata)
            manifest["files"][chunk_name] = _sha256(chunk)
            manifest["files"][cdata_name] = _sha256(cdata)

    manifest_path = os.path.join(output_root, "NAVMESH_PROBE_MANIFEST.json")
    with open(manifest_path, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="WoT 0.8.2 game root")
    parser.add_argument("--output", required=True, help="res_mods overlay output root")
    args = parser.parse_args()
    manifest = build_overlay(os.path.abspath(args.client), os.path.abspath(args.output))
    print("Built Lakeville native-navmesh probe with %d files." % len(manifest["files"]))
    print("This is a loader/API probe only; do not use it as a playable navmesh.")


if __name__ == "__main__":
    main()
