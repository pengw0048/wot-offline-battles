#!/usr/bin/env python3
"""Decode named collision surfaces out of a Havok packfile.

Console World of Tanks ships one Havok packfile per vehicle component, and
inside it every named surface -- the armour plates ``armor_1..N`` and the
interior modules and crew stations ``ammoBay``, ``engine``, ``fuelTank``,
``transmission``, ``radio``, ``turretRotator``, ``surveyingDevice``,
``driver``, ``commander``, ``gunner_1``, ``loader_1``, ``radioman_1`` and so
on -- is a separate shape.  Those names are exactly the material kinds
``scripts/item_defs/vehicles/common/vehicle.xml`` defines for #1513, so the
surfaces map onto this port's module targets without interpretation.

Nothing here is guessed.  The container layout is the documented Havok
packfile: a 64-byte header, then 64-byte section headers, then the sections
with their local, global and virtual fixup tables.  Everything else is
recovered from those tables rather than from a class layout, because the
``__types__`` section of these files is empty:

* the virtual fixups name every root object and its class;
* ``hknpPhysicsSystemData`` holds one 128-byte record per surface; the record
  carries the shape as a global fixup and, 24 bytes later, the surface name as
  a local fixup, so surface and shape pair with no ambiguity.  The record
  array's own offset differs per component -- it sits after the container
  boilerplate, whose size depends on the object graph -- so it is located by
  that pointer pairing rather than assumed;
* each ``hknpCompressedMeshShapeData`` carries its mesh's tight AABB inline as
  two four-float vectors at ``+32`` and ``+48``;
* the packed vertices are the one 4-byte-element array in the shape whose
  11/11/10-bit unpacking saturates that AABB on every axis, which is what
  identifies it -- a wrong array or a wrong bit split does not.

The AABB alone is not enough for a module built as two separated lobes: an
IS-7's ammunition racks sit against both hull sides, so their combined box
spans the full hull width.  Callers wanting that resolved should cluster the
decoded vertices.
"""

import struct


PACKFILE_MAGIC = b'\x57\xe0\xe0\x57\x10\xc0\xc0\x10'
SECTION_HEADER_SIZE = 64
# hknpPhysicsSystemData's per-surface record: stride, and the offsets of its
# name and shape pointers within the object.
RECORD_STRIDE = 128
# Inside one record, the surface name pointer follows the shape pointer.
RECORD_NAME_DELTA = 24
# hknpCompressedMeshShape is a fixed-size header; its data object follows.
SHAPE_HEADER_SIZE = 176
DOMAIN_MIN_OFFSET = 32
DOMAIN_MAX_OFFSET = 48
VERTEX_BITS = (11, 11, 10)


class UnsupportedPackfile(Exception):
    pass


class Packfile(object):
    """A Havok packfile's sections and fixup tables."""

    def __init__(self, blob):
        if blob[:8] != PACKFILE_MAGIC:
            raise UnsupportedPackfile('not a Havok packfile')
        self.blob = blob
        self.user_tag, self.file_version = struct.unpack('>2i', blob[8:16])
        rules = blob[16:20]
        self.pointer_size = rules[0]
        self.little_endian = bool(rules[1])
        if self.pointer_size != 4 or self.little_endian:
            raise UnsupportedPackfile(
                'expected 32-bit big-endian, got pointer=%d little=%s'
                % (self.pointer_size, self.little_endian))
        (self.num_sections, self.contents_section, unused_offset,
         self.class_name_section, unused_class_offset) = struct.unpack(
            '>5i', blob[20:40])
        self.contents_version = blob[40:56].split(b'\x00')[0].decode('ascii')
        self.sections = []
        for index in range(self.num_sections):
            start = SECTION_HEADER_SIZE * (index + 1)
            head = blob[start:start + SECTION_HEADER_SIZE]
            values = struct.unpack('>7i', head[20:48])
            self.sections.append({
                'tag': head[:19].split(b'\x00')[0].decode('ascii'),
                'start': values[0],
                'local': values[1],
                'global': values[2],
                'virtual': values[3],
                'exports': values[4],
            })

    def _table(self, section, first, last, stride):
        base = section['start']
        for offset in range(section[first], section[last], stride):
            chunk = self.blob[base + offset:base + offset + stride]
            if len(chunk) < stride:
                # A component with no named surfaces -- a bare chassis proxy --
                # can declare a table that runs past the file.  Stop there
                # rather than reading rubbish.
                return
            values = struct.unpack('>%di' % (stride // 4), chunk)
            if values[0] == -1:
                continue
            yield values

    def local_fixups(self, section):
        """{pointer field offset: destination offset}, section-relative."""
        return dict((values[0], values[1])
                    for values in self._table(section, 'local', 'global', 8))

    def global_fixups(self, section):
        """{pointer field offset: destination offset} across sections."""
        return dict((values[0], values[2])
                    for values in self._table(section, 'global', 'virtual', 12))

    def root_objects(self, section):
        """{object offset: class name}."""
        names = self.sections[self.class_name_section]
        out = {}
        for values in self._table(section, 'virtual', 'exports', 12):
            start = names['start'] + values[2]
            end = self.blob.index(b'\x00', start)
            out[values[0]] = self.blob[start:end].decode('ascii')
        return out


# No vehicle component is anywhere near this large, so a bound outside it is
# padding read as a float, not geometry.
MAX_PLAUSIBLE_EXTENT_M = 60.0


def _plausible_bound(minimum, maximum):
    for axis in range(3):
        low = minimum[axis]
        high = maximum[axis]
        if low != low or high != high:
            return False
        if abs(low) > MAX_PLAUSIBLE_EXTENT_M or \
                abs(high) > MAX_PLAUSIBLE_EXTENT_M:
            return False
        if high < low:
            return False
    return True


def _unpack_vertex(word, bits=VERTEX_BITS):
    bits_x, bits_y, bits_z = bits
    mask_x = (1 << bits_x) - 1
    mask_y = (1 << bits_y) - 1
    mask_z = (1 << bits_z) - 1
    return (float((word >> (bits_z + bits_y)) & mask_x) / mask_x,
            float((word >> bits_z) & mask_y) / mask_y,
            float(word & mask_z) / mask_z)


def _packed_vertices(pack, section, shape, region_end, minimum, maximum):
    """The shape's vertices in component-local metres, or () if not found."""
    blob = pack.blob
    base = section['start']
    fixups = sorted((src, dst) for src, dst in pack.local_fixups(section).items()
                    if shape <= src < region_end)
    targets = sorted(set(dst for unused_src, dst in fixups))
    span = [maximum[axis] - minimum[axis] for axis in range(3)]
    for src, dst in fixups:
        count = struct.unpack('>i', blob[base + src + 4:base + src + 8])[0]
        if count <= 0:
            continue
        position = targets.index(dst)
        end = (targets[position + 1] if position + 1 < len(targets)
               else region_end)
        if (end - dst) // count != 4:
            continue
        words = struct.unpack('>%dI' % count,
                              blob[base + dst:base + dst + 4 * count])
        points = [_unpack_vertex(word) for word in words]
        low = [min(point[axis] for point in points) for axis in range(3)]
        high = [max(point[axis] for point in points) for axis in range(3)]
        # Only the real vertex array reaches both ends of the tight AABB;
        # index and primitive arrays do not.  A nearly flat surface -- a
        # periscope slit is 6 cm tall -- quantizes too coarsely to saturate
        # its thin axis, so only axes with real extent are required to.
        thick = [axis for axis in range(3) if span[axis] > 0.01]
        if len(thick) < 2:
            thick = list(range(3))
        if any(low[axis] > 0.02 for axis in thick):
            continue
        if any(high[axis] < 0.98 for axis in thick):
            continue
        return tuple(tuple(minimum[axis] + point[axis] * span[axis]
                           for axis in range(3)) for point in points)
    return ()


def module_surfaces(blob, with_vertices=True):
    """{surface name: {'minimum', 'maximum', 'vertices'}} for one component."""
    pack = Packfile(blob)
    section = pack.sections[pack.contents_section]
    base = section['start']
    roots = pack.root_objects(section)
    shape_offsets = set(offset for offset, name in roots.items()
                        if name == 'hknpCompressedMeshShape')
    starts = sorted(roots)
    local = pack.local_fixups(section)
    globals_ = pack.global_fixups(section)

    def surface_name(pointer):
        if pointer not in local:
            return None
        offset = local[pointer]
        try:
            end = pack.blob.index(b'\x00', base + offset)
        except ValueError:
            return None
        raw = pack.blob[base + offset:end]
        if not raw or len(raw) > 32 or any(byte < 33 or byte > 126
                                           for byte in raw):
            return None
        return raw.decode('ascii')

    # Records are the shape pointers that carry a plain surface name 24 bytes
    # later.  The parallel array of bare shape pointers has no name beside it
    # and drops out here.
    records = sorted(pointer for pointer, shape in globals_.items()
                     if shape in shape_offsets
                     and surface_name(pointer + RECORD_NAME_DELTA) is not None)
    if len(records) > 1:
        strides = set(records[index + 1] - records[index]
                      for index in range(len(records) - 1))
        if strides != set([RECORD_STRIDE]):
            raise UnsupportedPackfile(
                'surface records are not evenly strided: %s' % sorted(strides))

    surfaces = {}
    for shape_pointer in records:
        shape = globals_[shape_pointer]
        name = surface_name(shape_pointer + RECORD_NAME_DELTA)
        data_object = shape + SHAPE_HEADER_SIZE
        minimum = struct.unpack(
            '>4f', pack.blob[base + data_object + DOMAIN_MIN_OFFSET:
                             base + data_object + DOMAIN_MIN_OFFSET + 16])[:3]
        maximum = struct.unpack(
            '>4f', pack.blob[base + data_object + DOMAIN_MAX_OFFSET:
                             base + data_object + DOMAIN_MAX_OFFSET + 16])[:3]
        if not _plausible_bound(minimum, maximum):
            # Some shapes leave the AABB slot as 0x7f7f7f7f padding, which
            # reads as ~3.4e38.  Drop that surface rather than let a garbage
            # bound through; the rest of the component is still good.
            continue
        vertices = ()
        if with_vertices:
            position = starts.index(shape)
            region_end = (starts[position + 2] if position + 2 < len(starts)
                          else section['local'])
            vertices = _packed_vertices(pack, section, shape, region_end,
                                        minimum, maximum)
        surfaces[name] = {
            'minimum': minimum,
            'maximum': maximum,
            'vertices': vertices,
        }
    if not surfaces:
        raise UnsupportedPackfile('no named surfaces found')
    return surfaces


def outer_shell(surfaces):
    """The union AABB of the armour plates: the component's own shell."""
    plates = [item for name, item in surfaces.items()
              if name.startswith('armor_')]
    if not plates:
        return None
    return (tuple(min(item['minimum'][axis] for item in plates)
                  for axis in range(3)),
            tuple(max(item['maximum'][axis] for item in plates)
                  for axis in range(3)))


def main():
    import sys
    blob = open(sys.argv[1], 'rb').read()
    surfaces = module_surfaces(blob)
    shell = outer_shell(surfaces)
    print('%d named surfaces' % len(surfaces))
    if shell is not None:
        print('outer shell %.3f x %.3f x %.3f m'
              % tuple(shell[1][axis] - shell[0][axis] for axis in range(3)))
    for name in sorted(surfaces):
        item = surfaces[name]
        print('  %-18s %8.3f %8.3f %8.3f .. %8.3f %8.3f %8.3f  verts=%d'
              % ((name,) + tuple(item['minimum']) + tuple(item['maximum'])
                 + (len(item['vertices']),)))


if __name__ == '__main__':
    main()
