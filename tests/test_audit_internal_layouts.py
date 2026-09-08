import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import packed_xml

spec = importlib.util.spec_from_file_location(
    'audit_internal_layouts', TOOLS / 'audit_internal_layouts.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _string(value):
    return packed_xml.PackedValue(packed_xml.TYPE_STRING, value.encode('ascii'))


class InternalLayoutAuditTests(unittest.TestCase):
    def test_catalog_inventory_distinguishes_missing_and_mismatched_profiles(self):
        names = ('Ch02_Type62', 'Ch04_T34_1')
        listing = packed_xml.PackedElement(children=[
            (name.encode('ascii'), _string('')) for name in names])
        # Compressed Packed XML strings are base64 byte storage, not UTF-8.
        roles = [(b'commander', _string('')), (b'gunner', _string('')),
                 (b'driver', _string('')), (b'loader', packed_xml.PackedValue(
                     packed_xml.TYPE_COMPRESSED_STRING, b'\xad\xa7b\xa2f\xa7'))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scripts.pkg'
            for mismatch in (False, True):
                crew = list(roles)
                if mismatch:
                    crew[0] = (b'commander', _string('radioman'))
                    crew[-1] = (b'loader', _string(''))
                definition = packed_xml.PackedElement(children=[
                    (b'crew', packed_xml.PackedValue(packed_xml.TYPE_ELEMENT,
                        packed_xml.PackedElement(children=crew)))])
                with zipfile.ZipFile(path, 'w') as archive:
                    prefix = 'scripts/item_defs/vehicles/china/'
                    archive.writestr(prefix + 'list.xml',
                                     packed_xml.write_packed_xml(listing))
                    for name in names:
                        archive.writestr(prefix + name + '.xml',
                                         packed_xml.write_packed_xml(definition))
                result = audit.scan(path)
                self.assertFalse(result['collision_geometry_verified'])
                self.assertEqual({
                    'crew_mismatch' if mismatch else 'profile_matched': 1,
                    'missing_profile': 1}, result['summary'])


if __name__ == '__main__':
    unittest.main()
