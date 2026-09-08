"""Resource-presence checks must not admit retired placeholder vehicles."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import bake_vehicle_blacklist_0922 as baker
import packed_xml as packed


def element(children):
    return packed.PackedValue(
        packed.TYPE_ELEMENT, packed.PackedElement(children=children))


class VehicleBlacklistBakerTests(unittest.TestCase):
    def test_existing_placeholder_art_is_excluded_but_real_art_is_retained(self):
        placeholder = 'vehicles/russian/R00_Placeholder/normal/lod0/Hull.model'
        real = 'vehicles/british/GB70_N_FV4202_105/normal/lod0/Hull.model'
        absent = 'vehicles/missing/normal/lod0/Hull.model'
        resources = {'Retired': placeholder, 'Real': real, 'Missing': absent}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = root / 'res' / 'packages'
            packages.mkdir(parents=True)
            with zipfile.ZipFile(str(packages / 'scripts.pkg'), 'w') as archive:
                listing = packed.PackedElement(children=[
                    (name.encode(), element([])) for name in resources])
                archive.writestr('scripts/item_defs/vehicles/uk/list.xml',
                                 packed.write_packed_xml(listing))
                for name, resource in resources.items():
                    definition = packed.PackedElement(children=[
                        (b'hull', element([(b'models', element([
                            (b'undamaged', packed.PackedValue(
                                packed.TYPE_STRING, resource.encode()))]))]))])
                    archive.writestr(
                        'scripts/item_defs/vehicles/uk/%s.xml' % name,
                        packed.write_packed_xml(definition))
                archive.writestr(placeholder, b'placeholder art exists')
                archive.writestr(real, b'real art exists')
            with mock.patch.object(baker, 'NATIONS', ('uk',)):
                count, unusable = baker.scan(root)
        self.assertEqual(3, count)
        self.assertEqual({'uk:Retired': (placeholder,),
                          'uk:Missing': (absent,)}, unusable)
