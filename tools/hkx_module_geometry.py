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

The reviewed Console layout is 32-bit big-endian hk_2014.2.5-r1.
Fixups locate the shape data and its section, primitive, packed-vertex and
shared-vertex arrays. Section codec parameters reconstruct component metres;
primitive index quadruples retain topology (including disconnected pieces).
The bit fields are numerical low-to-high x/y/z, independent of byte endian.
Cross-format resource checks against Console BigWorld meshes validate this
layout; an enclosing AABB cannot validate vertex interpretation.
"""

import struct


PACKFILE_MAGIC = b'\x57\xe0\xe0\x57\x10\xc0\xc0\x10'
SECTION_HEADER_SIZE = 64
# hknpPhysicsSystemData's per-surface record: stride, and the offsets of its
# name and shape pointers within the object.
RECORD_STRIDE = 128
# Inside one record, the surface name pointer follows the shape pointer.
RECORD_NAME_DELTA = 24
DOMAIN_MIN_OFFSET = 32
DOMAIN_MAX_OFFSET = 48


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


def _mesh(pack, section, data):
    """Decode indexed triangles from the reviewed 32-bit static mesh tree."""
    if pack.contents_version != 'hk_2014.2.5-r1':
        raise UnsupportedPackfile('unreviewed mesh version: ' + pack.contents_version)
    blob, base = pack.blob, section['start']
    local = pack.local_fixups(section)

    def read(fmt, offset):
        size = struct.calcsize('>' + fmt)
        if offset < 0 or offset + size > section['local']:
            raise UnsupportedPackfile('mesh field outside data section')
        return struct.unpack_from('>' + fmt, blob, base + offset)

    def array(field, stride):
        count = read('i', field + 4)[0]
        if count < 0:
            raise UnsupportedPackfile('negative mesh array count')
        if not count:
            return 0, 0
        offset = local.get(field)
        if offset is None or offset < 0 or offset + count * stride > section['local']:
            raise UnsupportedPackfile('invalid mesh array fixup or size')
        return offset, count

    sections, section_count = array(data + 76, 96)
    primitives, primitive_count = array(data + 88, 4)
    shared_indices, shared_index_count = array(data + 100, 2)
    packed, packed_count = array(data + 112, 4)
    shared, shared_count = array(data + 124, 8)
    low, high = read('4f', data + 32), read('4f', data + 48)
    vertices, triangles = [], []
    used_primitives = set()
    for i in range(section_count):
        sec = sections + i * 96
        codec = read('6f', sec + 48)
        first, shared_field, primitive_field = read('3I', sec + 72)
        num_packed, num_shared = read('2B', sec + 88)
        shared_base = shared_field >> 8
        primitive_base, num_primitives = primitive_field >> 8, primitive_field & 255
        if ((shared_field & 255) != num_packed or
                first + num_packed > packed_count or
                shared_base + num_shared > shared_index_count or
                primitive_base + num_primitives > primitive_count):
            raise UnsupportedPackfile('section vertex or primitive range invalid')
        start = len(vertices)
        for j in range(num_packed):
            word = read('I', packed + (first + j) * 4)[0]
            xyz = (word & 2047, (word >> 11) & 2047, word >> 22)
            vertices.append(tuple(codec[a] + xyz[a] * codec[a + 3]
                                  for a in range(3)))
        for j in range(num_shared):
            index = read('H', shared_indices + (shared_base + j) * 2)[0]
            if index >= shared_count:
                raise UnsupportedPackfile('shared vertex index invalid')
            word = read('Q', shared + index * 8)[0]
            xyz = (word & 2097151, (word >> 21) & 2097151, word >> 42)
            masks = (2097151, 2097151, 4194303)
            vertices.append(tuple(low[a] + xyz[a] * (high[a] - low[a]) / masks[a]
                                  for a in range(3)))
        for j in range(primitive_base, primitive_base + num_primitives):
            if j in used_primitives:
                raise UnsupportedPackfile('overlapping primitive sections')
            used_primitives.add(j)
            quad = read('4B', primitives + j * 4)
            # Havok retains degenerate slots (including 0xDEADDEAD) in
            # primitive arrays and their BVHs. They contain no triangle.
            if len(set(quad)) < 3:
                continue
            if max(quad) >= num_packed + num_shared:
                raise UnsupportedPackfile('primitive vertex index invalid')
            triangles.append(tuple(start + k for k in quad[:3]))
            if quad[2] != quad[3]:
                triangles.append(tuple(start + quad[k] for k in (0, 2, 3)))
    if len(used_primitives) != primitive_count or not triangles:
        raise UnsupportedPackfile('incomplete mesh topology')
    for point in vertices:
        if any(not low[a] - 0.002 <= point[a] <= high[a] + 0.002
               for a in range(3)):
            raise UnsupportedPackfile('decoded vertex outside mesh domain')
    return tuple(vertices), tuple(triangles)


def module_surfaces(blob, with_vertices=True):
    """{surface name: {'minimum', 'maximum', 'vertices', 'triangles'}} for one component."""
    pack = Packfile(blob)
    section = pack.sections[pack.contents_section]
    base = section['start']
    roots = pack.root_objects(section)
    shape_offsets = set(offset for offset, name in roots.items()
                        if name == 'hknpCompressedMeshShape')
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
        data_object = globals_.get(shape + 96)
        if roots.get(data_object) != 'hknpCompressedMeshShapeData':
            raise UnsupportedPackfile('shape data fixup does not name mesh data')
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
        vertices, triangles = _mesh(pack, section, data_object)
        surfaces[name] = {
            'minimum': minimum,
            'maximum': maximum,
            'vertices': vertices if with_vertices else (),
            'triangles': triangles if with_vertices else (),
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
