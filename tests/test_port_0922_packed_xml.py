import importlib.util
import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKED_XML_PATH = ROOT / "tools" / "packed_xml.py"
SPEC = importlib.util.spec_from_file_location("packed_xml_round_trip", PACKED_XML_PATH)
packed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packed
SPEC.loader.exec_module(packed)


# Exact scripts.pkg member from the pinned Chinese HD 0.9.22.0.1 #1513 client:
# scripts/item_defs/vehicles/poland/components/turrets.xml
PINNED_TURRETS_XML = bytes.fromhex(
    "454ea162005475727265745f315f506c30335f507a565f506f6c616e6400696473"
    "00736861726564000002000000001001000c00000002000c000010010000000010"
    "000000000020"
)


class PackedXmlRoundTripTest(unittest.TestCase):

    def test_pinned_client_member_round_trips_byte_for_byte(self):
        root = packed.read_packed_xml(PINNED_TURRETS_XML)

        self.assertEqual([b"ids", b"shared"], [name for name, _ in root.children])
        ids = root.children[0][1]
        self.assertEqual(packed.TYPE_ELEMENT, ids.value_type)
        self.assertEqual(
            [(b"Turret_1_Pl03_PzV_Poland", packed.TYPE_INTEGER, 0)],
            [(name, value.value_type, value.value)
             for name, value in ids.value.children],
        )
        self.assertEqual(
            PINNED_TURRETS_XML, packed.write_packed_xml(root))

    def test_synthetic_document_uses_canonical_dictionary_and_keeps_semantics(self):
        nested = packed.PackedElement(children=[
            (b"middle", packed.PackedValue(packed.TYPE_BOOLEAN, True)),
            (b"beta", packed.PackedValue(packed.TYPE_STRING, b"value")),
        ])
        root = packed.PackedElement(children=[
            (b"zeta", packed.PackedValue(packed.TYPE_INTEGER, 7)),
            (b"alpha", packed.PackedValue(packed.TYPE_ELEMENT, nested)),
            (b"vector", packed.PackedValue(
                packed.TYPE_VECTOR, struct.pack("<2f", 1.5, -2.0))),
        ])

        encoded = packed.write_packed_xml(root)

        self.assertTrue(encoded.startswith(
            packed.PACKED_XML_MAGIC +
            b"\0alpha\0beta\0middle\0vector\0zeta\0\0"))
        reparsed = packed.read_packed_xml(encoded)
        self.assertEqual(encoded, packed.write_packed_xml(reparsed))
        self.assertEqual(
            [(b"zeta", packed.TYPE_INTEGER),
             (b"alpha", packed.TYPE_ELEMENT),
             (b"vector", packed.TYPE_VECTOR)],
            [(name, value.value_type) for name, value in reparsed.children],
        )
        self.assertEqual(7, reparsed.children[0][1].value)
        self.assertEqual(
            [(b"middle", packed.TYPE_BOOLEAN, True),
             (b"beta", packed.TYPE_STRING, b"value")],
            [(name, value.value_type, value.value)
             for name, value in reparsed.children[1][1].value.children],
        )
        self.assertEqual(
            (1.5, -2.0), struct.unpack("<2f", reparsed.children[2][1].value))


if __name__ == "__main__":
    unittest.main()
