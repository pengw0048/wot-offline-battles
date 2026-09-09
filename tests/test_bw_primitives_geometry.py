"""The BigWorld .primitives collision decoder, on a container built here.

Console shipped its collision proxies as Havok packfiles up to console4.9 and
as this container from console4.10 on, so both are needed to reach the whole
roster.  These tests pin the container contract without needing the archive:
the section directory layout, the material-index pairing, resolving a group's
vertices through the index array rather than the unused per-group vertex
range, and refusing a file whose sizes do not agree instead of guessing.
"""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import bw_primitives_geometry as bw


def _section_container(sections):
    """Assemble a container from [(name, bytes)] the way BigWorld does."""
    body = struct.pack('<I', bw.MAGIC)
    directory = b''
    for name, blob in sections:
        body += blob
        body += b'\x00' * ((-len(blob)) % 4)
        encoded = name.encode('ascii')
        # length, four reserved words, then the name length: 24 bytes.
        directory += struct.pack('<6I', len(blob), 0, 0, 0, 0, len(encoded))
        directory += encoded
        directory += b'\x00' * ((-len(encoded)) % 4)
    return body + directory + struct.pack('<I', len(directory))


def _vertices_section(vertices):
    header = b'xyz'.ljust(bw.FORMAT_FIELD, b'\x00')
    header += struct.pack('<I', len(vertices))
    for vertex in vertices:
        header += struct.pack('<3f', *vertex)
    return header


def _indices_section(indices, groups):
    header = b'list'.ljust(bw.FORMAT_FIELD, b'\x00')
    header += struct.pack('<2I', len(indices), len(groups))
    header += struct.pack('<%dH' % len(indices), *indices)
    for group in groups:
        header += struct.pack('<4i', *group)
    return header


def _materials_section(names):
    text = '<temp_bsp_materials>'
    for name in names:
        text += '<id>%s</id>' % name
    text += '</temp_bsp_materials>'
    return text.encode('ascii')


class PrimitivesDecoderTests(unittest.TestCase):

    def setUp(self):
        # Two triangles per surface, laid out so each surface's own bound is
        # unmistakable: the engine sits behind centre, the driver ahead.
        self.vertices = [
            (-0.5, 0.0, -2.0), (0.5, 0.0, -2.0), (0.0, 0.4, -1.6),
            (-0.3, 0.1, 2.0), (0.3, 0.1, 2.0), (0.0, 0.5, 2.4),
        ]
        self.indices = (0, 1, 2, 3, 4, 5)
        # start_vertex / vertex_count are deliberately zero, as the real
        # files leave them.
        self.groups = [(0, 1, 0, 0), (3, 1, 0, 0)]
        self.names = ['engine', 'driver']
        self.blob = _section_container((
            ('vertices', _vertices_section(self.vertices)),
            ('indices', _indices_section(self.indices, self.groups)),
            ('bsp2_materials', _materials_section(self.names)),
        ))

    def test_surfaces_are_named_and_bounded_from_the_index_array(self):
        surfaces = bw.module_surfaces(self.blob)
        self.assertEqual({'engine', 'driver'}, set(surfaces))
        engine = surfaces['engine']
        self._assert_box(engine, (-0.5, 0.0, -2.0), (0.5, 0.4, -1.6))
        driver = surfaces['driver']
        self._assert_box(driver, (-0.3, 0.1, 2.0), (0.3, 0.5, 2.4))
        # The engine is behind centre and the driver ahead of it, so the
        # decoder is not silently transposing axes.
        self.assertLess(engine['maximum'][2], driver['minimum'][2])

    def _assert_box(self, surface, low, high):
        # The vertices round-trip through float32, so compare to that.
        for axis in range(3):
            self.assertAlmostEqual(low[axis], surface['minimum'][axis],
                                   places=6)
            self.assertAlmostEqual(high[axis], surface['maximum'][axis],
                                   places=6)

    def test_the_unused_per_group_vertex_range_is_not_trusted(self):
        # Every real file leaves these zero.  A decoder that read them would
        # produce no surfaces at all, which is the bug this pins.
        for group in self.groups:
            self.assertEqual(0, group[2])
            self.assertEqual(0, group[3])
        self.assertEqual(2, len(bw.module_surfaces(self.blob)))

    def test_outer_shell_unions_only_the_armour_plates(self):
        blob = _section_container((
            ('vertices', _vertices_section(self.vertices)),
            ('indices', _indices_section(self.indices, self.groups)),
            ('bsp2_materials', _materials_section(['armor_1', 'engine'])),
        ))
        surfaces = bw.module_surfaces(blob)
        shell = bw.outer_shell(surfaces)
        for axis, value in enumerate((-0.5, 0.0, -2.0)):
            self.assertAlmostEqual(value, shell[0][axis], places=6)
        for axis, value in enumerate((0.5, 0.4, -1.6)):
            self.assertAlmostEqual(value, shell[1][axis], places=6)
        self.assertIsNone(bw.outer_shell(
            {'engine': surfaces['engine']}))

    def test_a_wrong_magic_is_refused(self):
        blob = b'\x00\x00\x00\x00' + self.blob[4:]
        with self.assertRaises(bw.UnsupportedPrimitives):
            bw.module_surfaces(blob)

    def test_a_material_count_mismatch_is_refused(self):
        blob = _section_container((
            ('vertices', _vertices_section(self.vertices)),
            ('indices', _indices_section(self.indices, self.groups)),
            ('bsp2_materials', _materials_section(['engine'])),
        ))
        with self.assertRaises(bw.UnsupportedPrimitives):
            bw.module_surfaces(blob)

    def test_a_vertex_count_that_does_not_fill_the_section_is_refused(self):
        header = b'xyz'.ljust(bw.FORMAT_FIELD, b'\x00')
        header += struct.pack('<I', len(self.vertices) + 5)
        for vertex in self.vertices:
            header += struct.pack('<3f', *vertex)
        blob = _section_container((
            ('vertices', header),
            ('indices', _indices_section(self.indices, self.groups)),
            ('bsp2_materials', _materials_section(self.names)),
        ))
        with self.assertRaises(bw.UnsupportedPrimitives):
            bw.module_surfaces(blob)

    def test_a_group_reaching_past_the_index_array_is_refused(self):
        groups = [(0, 1, 0, 0), (3, 9, 0, 0)]
        blob = _section_container((
            ('vertices', _vertices_section(self.vertices)),
            ('indices', _indices_section(self.indices, groups)),
            ('bsp2_materials', _materials_section(self.names)),
        ))
        with self.assertRaises(bw.UnsupportedPrimitives):
            bw.module_surfaces(blob)

    def test_a_missing_section_is_refused(self):
        blob = _section_container((
            ('vertices', _vertices_section(self.vertices)),
            ('bsp2_materials', _materials_section(self.names)),
        ))
        with self.assertRaises(bw.UnsupportedPrimitives):
            bw.module_surfaces(blob)

    def test_an_implausible_bound_is_dropped_not_reported(self):
        vertices = [(0.0, 0.0, 0.0), (1e30, 0.0, 0.0), (0.0, 1e30, 0.0),
                    (-0.3, 0.1, 2.0), (0.3, 0.1, 2.0), (0.0, 0.5, 2.4)]
        blob = _section_container((
            ('vertices', _vertices_section(vertices)),
            ('indices', _indices_section(self.indices, self.groups)),
            ('bsp2_materials', _materials_section(self.names)),
        ))
        surfaces = bw.module_surfaces(blob)
        self.assertNotIn('engine', surfaces)
        self.assertIn('driver', surfaces)


if __name__ == '__main__':
    unittest.main()
