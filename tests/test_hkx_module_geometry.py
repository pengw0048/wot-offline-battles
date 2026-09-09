"""Reviewed 32-bit big-endian Havok topology contracts, not bound heuristics."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import hkx_module_geometry as hkx


def packed(x, y, z):
    return x | (y << 11) | (z << 22)


def shared(x, y, z):
    return x | (y << 21) | (z << 42)


class MeshData:
    """Small explicit wire fixture with different global and section codecs."""
    def __init__(self, sections, words, quads, shared_words=(), shared_indices=()):
        self.blob = bytearray(8192)
        self.contents_version = 'hk_2014.2.5-r1'
        self.fixups = {}
        self.section = {'start': 0, 'local': len(self.blob)}
        self.write('4f', 32, -10., -10., -10., 1.)
        self.write('4f', 48, 10., 10., 10., 1.)
        self.array(76, 256, len(sections))
        self.array(88, 2048, len(quads))
        self.array(100, 3072, len(shared_indices))
        self.array(112, 4096, len(words))
        self.array(124, 6144, len(shared_words))
        for i, (first, count, shared_base, shared_count, primitive_base,
                primitive_count, offset, scale) in enumerate(sections):
            address = 256 + i * 96
            self.write('6f', address + 48, *(offset + scale))
            self.write('4I', address + 72, first,
                       (shared_base << 8) | count,
                       (primitive_base << 8) | primitive_count, 0)
            self.write('2B', address + 88, count, shared_count)
        for i, quad in enumerate(quads):
            self.write('4B', 2048 + i * 4, *quad)
        for i, index in enumerate(shared_indices):
            self.write('H', 3072 + i * 2, index)
        for i, word in enumerate(words):
            self.write('I', 4096 + i * 4, word)
        for i, word in enumerate(shared_words):
            self.write('Q', 6144 + i * 8, word)

    def write(self, fmt, address, *values):
        struct.pack_into('>' + fmt, self.blob, address, *values)

    def array(self, field, address, count):
        self.write('3I', field, 0, count, 0x80000000 | count)
        if count:
            self.fixups[field] = address

    def local_fixups(self, unused_section):
        return self.fixups

    def decode(self):
        return hkx._mesh(self, self.section, 0)


class HavokTopologyTests(unittest.TestCase):
    def fixture(self):
        return MeshData(
            [(0, 4, 0, 0, 0, 1, (-1., 2., -3.), (.25, .125, .5))],
            [packed(0, 0, 0), packed(4, 0, 0), packed(4, 8, 0), packed(0, 8, 0)],
            [(0, 1, 2, 3)])

    def test_low_to_high_numeric_bits_and_section_codec(self):
        vertices, faces = self.fixture().decode()
        self.assertEqual(((-1., 2., -3.), (0., 2., -3.),
                          (0., 3., -3.), (-1., 3., -3.)), vertices)
        # The global domain is deliberately [-10, 10]. It must not replace
        # the section codec, even though both could saturate a guessed AABB.
        self.assertEqual(((0, 1, 2), (0, 2, 3)), faces)

    def test_multi_section_shared_vertices_and_quad_indices(self):
        fixture = MeshData(
            [(0, 2, 0, 1, 0, 1, (-2., 0., 0.), (.5, .25, .125)),
             (2, 3, 1, 1, 1, 1, (2., 0., 0.), (.5, .25, .125))],
            [packed(0, 0, 0), packed(2, 0, 0),
             packed(0, 0, 0), packed(2, 0, 0), packed(2, 4, 0)],
            [(0, 1, 2, 2), (0, 1, 2, 3)],
            [shared(2097151, 0, 4194303)], [0, 0])
        vertices, faces = fixture.decode()
        self.assertEqual((-2., 0., 0.), vertices[0])
        self.assertEqual((2., 0., 0.), vertices[3])
        self.assertEqual((10., -10., 10.), vertices[2])
        self.assertEqual(vertices[2], vertices[6])
        self.assertEqual(((0, 1, 2), (3, 4, 5), (3, 5, 6)), faces)

    def test_degenerate_triangle_slot_has_no_invented_area(self):
        fixture = MeshData(
            [(0, 3, 0, 0, 0, 2, (0., 0., 0.), (1., 1., 1.))],
            [packed(0, 0, 0), packed(1, 0, 0), packed(0, 1, 0)],
            [(0, 1, 2, 2), (0xde, 0xad, 0xde, 0xad)])
        self.assertEqual(((0, 1, 2),), fixture.decode()[1])

    def test_invalid_ranges_and_shared_indices_are_rejected(self):
        fixture = self.fixture()
        fixture.write('4B', 2048, 0, 1, 2, 4)
        with self.assertRaisesRegex(hkx.UnsupportedPackfile, 'vertex index'):
            fixture.decode()
        fixture = self.fixture()
        fixture.fixups[112] = 8190
        with self.assertRaisesRegex(hkx.UnsupportedPackfile, 'array'):
            fixture.decode()
        fixture = MeshData(
            [(0, 2, 0, 1, 0, 1, (0., 0., 0.), (1., 1., 1.))],
            [packed(0, 0, 0), packed(1, 0, 0)], [(0, 1, 2, 2)],
            [shared(1, 1, 1)], [1])
        with self.assertRaisesRegex(hkx.UnsupportedPackfile, 'shared vertex'):
            fixture.decode()

    def test_unreviewed_version_and_invalid_codec_do_not_guess(self):
        fixture = self.fixture()
        fixture.contents_version = 'hk_2099.0.0'
        with self.assertRaisesRegex(hkx.UnsupportedPackfile, 'unreviewed'):
            fixture.decode()
        fixture = self.fixture()
        fixture.write('f', 256 + 48, float('nan'))
        with self.assertRaisesRegex(hkx.UnsupportedPackfile, 'domain'):
            fixture.decode()

    def test_shape_data_is_followed_by_fixup_not_fixed_header_size(self):
        # Embed the mesh fixture into a minimal real packfile. Its shape/data
        # gap is deliberately different from the old 176-byte assumption.
        fixture = self.fixture()
        shift, shape, record = 1024, 512, 128
        data = bytearray(shift + len(fixture.blob))
        data[shift:] = fixture.blob
        local = dict((k + shift, v + shift) for k, v in fixture.fixups.items())
        name_address = 300
        data[name_address:name_address + 8] = b'ammoBay\0'
        local[record + 24] = name_address
        globals_ = {record: shape, shape + 96: shift}
        classes = b'hknpCompressedMeshShape\0hknpCompressedMeshShapeData\0'
        data_start = (192 + len(classes) + 15) & ~15
        local_start = len(data)
        local_bytes = b''.join(struct.pack('>2i', a, b) for a, b in sorted(local.items()))
        global_bytes = b''.join(struct.pack('>3i', a, 1, b) for a, b in sorted(globals_.items()))
        virtual_bytes = (struct.pack('>3i', shape, 0, 0) +
                         struct.pack('>3i', shift, 0, len(b'hknpCompressedMeshShape\0')))
        header = bytearray(192)
        header[:8] = hkx.PACKFILE_MAGIC
        struct.pack_into('>2i', header, 8, 0, 11)
        header[16:20] = bytes((4, 0, 1, 1))
        struct.pack_into('>5i', header, 20, 2, 1, 0, 0, 0)
        header[40:56] = b'hk_2014.2.5-r1\0\0'
        header[64:75] = b'__classes__'
        struct.pack_into('>7i', header, 84, 192, len(classes), len(classes),
                         len(classes), len(classes), len(classes), len(classes))
        header[128:136] = b'__data__'
        g = local_start + len(local_bytes)
        v = g + len(global_bytes)
        end = v + len(virtual_bytes)
        struct.pack_into('>7i', header, 148, data_start, local_start, g, v, end, end, end)
        blob = bytes(header) + classes
        blob += b'\0' * (data_start - len(blob))
        blob += bytes(data) + local_bytes + global_bytes + virtual_bytes
        decoded = hkx.module_surfaces(blob)['ammoBay']
        self.assertEqual(fixture.decode()[0], decoded['vertices'])
        self.assertEqual(fixture.decode()[1], decoded['triangles'])


if __name__ == '__main__':
    unittest.main()
