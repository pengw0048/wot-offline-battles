#!/usr/bin/env python3
"""Decode per-surface geometry from a BigWorld ``.primitives`` collision mesh.

The Armor Inspector archive ships Console collision proxies as Havok
packfiles up to ``console4.9`` and as this container from ``console4.10``
onward, so the tanks Console added after 2019 are only readable here.
``tools/hkx_module_geometry.py`` handles the Havok side; this module presents
the same result -- one exact axis-aligned bound per named surface -- so the
baker can use either interchangeably.

The container is a plain section archive, and unlike the Havok files it needs
no reverse engineering of class layouts:

* four magic bytes ``0x42a14e65``, then every section back to back;
* a directory at the end, then a trailing ``uint32`` giving its length.  Each
  directory entry is ``uint32 length``, four reserved words, ``uint32
  nameLength`` and the name padded to a four-byte boundary;
* ``bsp2_materials`` holds ``<temp_bsp_materials><id>NAME</id>...`` in
  material-index order, and those names are exactly #1513's collision
  material kinds -- ``ammoBay``, ``engine``, ``transmission``, ``fuelTank``,
  ``radio``, ``surveyingDevice``, one per crew station, and the ``armor_*``
  plates;
* ``indices`` is ``char format[64]``, ``uint32 nIndices``, ``uint32
  nTriangleGroups``, the index array, then one
  ``{startIndex, nPrimitives, startVertex, nVertices}`` record per group.
  Group *i* carries material *i*, which is what pairs a surface with its
  triangles;
* ``vertices`` is ``char format[64]``, ``uint32 nVertices`` and the vertex
  array, ``xyz`` being three little-endian floats.

The section sizes are self-checking: header plus index array plus group table
must equal the ``indices`` section exactly, and the vertex count must divide
the ``vertices`` section exactly.  A file that fails either check is rejected
rather than guessed at.
"""
import re
import struct

MAGIC = 0x42A14E65
FORMAT_FIELD = 64
VERTEX_FORMATS = {'xyz': 12}
MAX_PLAUSIBLE_EXTENT_M = 60.0


class UnsupportedPrimitives(Exception):
    """The container is not a shape this decoder can read exactly."""


def _sections(blob):
    """{name: bytes} for one BigWorld section container."""
    if len(blob) < 8:
        raise UnsupportedPrimitives('too short to be a section container')
    if struct.unpack_from('<I', blob, 0)[0] != MAGIC:
        raise UnsupportedPrimitives('not a BigWorld primitives container')
    directory_length = struct.unpack_from('<I', blob, len(blob) - 4)[0]
    start = len(blob) - 4 - directory_length
    if start < 4 or directory_length == 0:
        raise UnsupportedPrimitives('directory length out of range')
    directory = blob[start:len(blob) - 4]

    entries = []
    offset = 0
    while offset + 24 <= len(directory):
        length = struct.unpack_from('<I', directory, offset)[0]
        name_length = struct.unpack_from('<I', directory, offset + 20)[0]
        offset += 24
        if name_length == 0 or offset + name_length > len(directory):
            raise UnsupportedPrimitives('malformed directory entry')
        name = directory[offset:offset + name_length].decode('ascii',
                                                             'replace')
        offset += name_length
        offset += (-name_length) % 4
        entries.append((name, length))
    if not entries:
        raise UnsupportedPrimitives('directory holds no sections')

    sections = {}
    position = 4
    for name, length in entries:
        if position + length > len(blob):
            raise UnsupportedPrimitives('section %r runs past the file'
                                        % name)
        sections[name] = blob[position:position + length]
        position += length
        position += (-length) % 4
    return sections


def _material_names(blob):
    """Surface names in material-index order."""
    text = blob.split(b'\x00', 1)[0].decode('ascii', 'replace')
    names = re.findall(r'<id>([^<]*)</id>', text)
    if not names:
        raise UnsupportedPrimitives('bsp2_materials names nothing')
    return names


def _vertices(blob):
    """[(x, y, z)] from a vertices section."""
    if len(blob) < FORMAT_FIELD + 4:
        raise UnsupportedPrimitives('vertices section too short')
    layout = blob[:FORMAT_FIELD].split(b'\x00', 1)[0].decode('ascii',
                                                             'replace')
    stride = VERTEX_FORMATS.get(layout)
    if stride is None:
        raise UnsupportedPrimitives('unsupported vertex format %r' % layout)
    count = struct.unpack_from('<I', blob, FORMAT_FIELD)[0]
    body = blob[FORMAT_FIELD + 4:]
    if count == 0 or count * stride != len(body):
        raise UnsupportedPrimitives(
            'vertex count %d does not fill the section (%d bytes, stride %d)'
            % (count, len(body), stride))
    return [struct.unpack_from('<3f', body, index * stride)
            for index in range(count)]


def _indices_and_groups(blob):
    """(index array, [(start_index, triangles, start_vertex, vertices)]).

    The per-group vertex range is left at zero in these files, so a group is
    defined by its index range instead: ``triangles`` triples starting at
    ``start_index``.  Both are returned so the caller can resolve a group's
    vertices through the index array rather than trusting the unused fields.
    """
    if len(blob) < FORMAT_FIELD + 8:
        raise UnsupportedPrimitives('indices section too short')
    layout = blob[:FORMAT_FIELD].split(b'\x00', 1)[0].decode('ascii',
                                                             'replace')
    index_count, group_count = struct.unpack_from('<2I', blob, FORMAT_FIELD)
    if group_count == 0:
        raise UnsupportedPrimitives('indices section holds no groups')
    for width, code in ((2, 'H'), (4, 'I')):
        if (FORMAT_FIELD + 8 + index_count * width
                + group_count * 16) == len(blob):
            break
    else:
        raise UnsupportedPrimitives(
            'indices section size %d matches no index width for %d indices '
            'and %d groups (format %r)'
            % (len(blob), index_count, group_count, layout))
    indices = struct.unpack_from('<%d%s' % (index_count, code), blob,
                                 FORMAT_FIELD + 8)
    table = FORMAT_FIELD + 8 + index_count * width
    groups = [struct.unpack_from('<4i', blob, table + index * 16)
              for index in range(group_count)]
    return indices, groups


def _plausible(low, high):
    for axis in range(3):
        span = high[axis] - low[axis]
        if not -MAX_PLAUSIBLE_EXTENT_M <= low[axis] <= MAX_PLAUSIBLE_EXTENT_M:
            return False
        if not 0.0 <= span <= MAX_PLAUSIBLE_EXTENT_M:
            return False
    return True


def module_surfaces(blob):
    """{surface name: {'minimum', 'maximum', 'vertices'}} for one component.

    Same shape as ``hkx_module_geometry.module_surfaces``, so either decoder
    can feed the baker.  A surface whose group is empty or whose bound is
    implausible is dropped rather than reported with a guessed extent.
    """
    sections = _sections(blob)
    for required in ('vertices', 'indices', 'bsp2_materials'):
        if required not in sections:
            raise UnsupportedPrimitives('no %s section' % required)

    names = _material_names(sections['bsp2_materials'])
    indices, groups = _indices_and_groups(sections['indices'])
    vertices = _vertices(sections['vertices'])
    if len(groups) != len(names):
        raise UnsupportedPrimitives(
            '%d triangle groups but %d material names'
            % (len(groups), len(names)))

    surfaces = {}
    for name, (start_index, triangles, unused_start_vertex,
               unused_vertex_count) in zip(names, groups):
        if triangles <= 0:
            continue
        stop = start_index + triangles * 3
        if start_index < 0 or stop > len(indices):
            raise UnsupportedPrimitives(
                'group %r spans indices %d..%d of %d'
                % (name, start_index, stop, len(indices)))
        referenced = set(indices[start_index:stop])
        if max(referenced) >= len(vertices):
            raise UnsupportedPrimitives(
                'group %r references vertex %d of %d'
                % (name, max(referenced), len(vertices)))
        block = [vertices[index] for index in referenced]
        low = tuple(min(vertex[axis] for vertex in block)
                    for axis in range(3))
        high = tuple(max(vertex[axis] for vertex in block)
                     for axis in range(3))
        if not _plausible(low, high):
            continue
        surfaces[name] = {'minimum': low, 'maximum': high,
                          'vertices': tuple(block)}
    if not surfaces:
        raise UnsupportedPrimitives('no usable surface in the container')
    return surfaces


def outer_shell(surfaces):
    """The union of the ``armor_*`` plates, or None."""
    plates = [item for name, item in surfaces.items()
              if name.startswith('armor')]
    if not plates:
        return None
    low = tuple(min(item['minimum'][axis] for item in plates)
                for axis in range(3))
    high = tuple(max(item['maximum'][axis] for item in plates)
                 for axis in range(3))
    return low, high
