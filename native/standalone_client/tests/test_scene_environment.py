"""The terrain preview keeps authored lighting units and HDR exposure."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import prepare_native_scene as scene
from bw_primitives_geometry import MAGIC


def hdr_settings():
    return ET.fromstring('''<HDR>
        <version>2</version><enable>true</enable><adaptationSpeed>60</adaptationSpeed>
        <bloom><enable>true</enable><brightThreshold>0.3</brightThreshold>
          <factor>0.3</factor><tint>1 0.8 0.6 0</tint></bloom>
        <tonemapping><eyeDarkLimit>0.3</eyeDarkLimit><eyeLightLimit>0.8</eyeLightLimit>
          <middleGray>0.4</middleGray><whitePoint>5</whitePoint><blackPoint>0.15</blackPoint>
        </tonemapping>
        <environment><skyLumMultiplier>1</skyLumMultiplier><sunlightLumMultiplier>2.5</sunlightLumMultiplier>
          <ambientLumMultiplier>1</ambientLumMultiplier><sunLumMultiplier>15</sunLumMultiplier>
        </environment><gammaCorrection><enabled>true</enabled></gammaCorrection>
        <colorCorrection><map>original_filmic_cube.dds</map></colorCorrection>
      </HDR>''')


class SceneLightingTests(unittest.TestCase):
    def test_hdr_maps_surface_light_without_copying_sun_disc_brightness(self):
        source = hdr_settings()
        result = scene.public_hdr_settings(source)
        self.assertEqual(result.findtext('environment/sunLumMultiplier'), '2.5')
        self.assertEqual(source.findtext('environment/sunLumMultiplier'), '15')
        for path, value in (('tonemapping/eyeDarkLimit', '0.3'),
                            ('tonemapping/eyeLightLimit', '0.8'),
                            ('tonemapping/middleGray', '0.4'),
                            ('tonemapping/whitePoint', '5'),
                            ('bloom/factor', '0.3'),
                            ('gammaCorrection/enabled', 'true')):
            self.assertEqual(result.findtext(path), value)
        self.assertIsNone(result.find('colorCorrection'))
        self.assertIsNone(result.find('tonemapping/blackPoint'))
        self.assertIsNone(result.find('bloom/tint'))

    def test_selects_authored_active_curve_and_preserves_high_dynamic_range(self):
        source = hdr_settings()
        source.remove(source.find('tonemapping'))
        mappings = ET.SubElement(source, 'tonemappings')
        for value in ('0.25', '1.55', '3'):
            tone = ET.SubElement(mappings, 'tonemapping')
            ET.SubElement(tone, 'eyeDarkLimit').text = value
        ET.SubElement(mappings, 'active').text = '1'
        source.find('environment/sunlightLumMultiplier').text = '18'
        result = scene.public_hdr_settings(source)
        self.assertEqual(result.findtext('tonemapping/eyeDarkLimit'), '1.55')
        self.assertEqual(result.findtext('environment/sunLumMultiplier'), '18')

    def test_missing_tone_fields_use_native_reader_defaults(self):
        source = hdr_settings()
        source.remove(source.find('tonemapping'))
        result = scene.public_hdr_settings(source)
        self.assertEqual(result.findtext('tonemapping/eyeDarkLimit'), '0.05')
        self.assertEqual(result.findtext('tonemapping/eyeLightLimit'), '1.5')
        self.assertEqual(result.findtext('tonemapping/middleGray'), '0.5')
        self.assertEqual(result.findtext('tonemapping/whitePoint'), '5.0')

    def test_invalid_active_curve_does_not_silently_use_a_different_curve(self):
        for active in ('-1', '3'):
            source = hdr_settings()
            mappings = ET.SubElement(source, 'tonemappings')
            ET.SubElement(mappings, 'tonemapping')
            ET.SubElement(mappings, 'active').text = active
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, 'active tone'):
                scene.public_hdr_settings(source)

    def test_missing_or_unreviewed_hdr_is_not_replaced_with_public_defaults(self):
        with self.assertRaisesRegex(ValueError, 'no authored HDR'):
            scene.public_hdr_settings(None)
        source = hdr_settings()
        source.find('version').text = '3'
        with self.assertRaisesRegex(ValueError, 'HDR version 2'):
            scene.public_hdr_settings(source)
        source = hdr_settings()
        source.find('environment').remove(source.find('environment/sunlightLumMultiplier'))
        with self.assertRaisesRegex(ValueError, 'sunlightLumMultiplier'):
            scene.public_hdr_settings(source)

    def test_forward_rgb_keys_keep_255_units_in_public_time_of_day(self):
        source = ET.fromstring('''<root><day_night_cycle>
          <starttime>8</starttime><hourlength>0</hourlength>
          <ambientkey><time>8</time><colour>138 153 175</colour></ambientkey>
          <forward><angle>25</angle><lightkey><time>8</time><colour>255 211.130692 76.5</colour></lightkey></forward>
          <deferred><angle>25</angle><lightkey><time>8</time><colour>255 212 153</colour></lightkey></deferred>
        </day_night_cycle></root>''')
        source.append(hdr_settings())
        settings = ET.fromstring('<root><timeOfDay>sky.xml</timeOfDay></root>')
        with tempfile.TemporaryDirectory() as temporary, patch.object(scene, 'PackageVFS') as vfs:
            vfs.return_value.read.return_value = ET.tostring(source)
            resource, original, hdr = scene.prepare_lighting(
                Path('packages'), settings, Path(temporary), 'spaces/test')
            result = ET.parse(Path(temporary) / 'native_lighting.xml')
            self.assertEqual(resource, 'spaces/test/native_lighting.xml')
            self.assertEqual(original, 'sky.xml')
            self.assertEqual(result.findtext('day_night_cycle/lightkey/colour'),
                             '255 211.130692 76.5')
            self.assertEqual(result.findtext('day_night_cycle/ambientkey/colour'), '138 153 175')
            self.assertEqual(hdr.findtext('environment/sunLumMultiplier'), '2.5')
            vfs.return_value.close.assert_called_once()


class StaticSkyTests(unittest.TestCase):
    def test_uses_public_sky_effect_with_authored_visual_texture_and_orientation(self):
        # A minimal original-style primitive container with one vertex stream.
        name = b'vertices'
        stream = b'set3/xyznuvtbpc'.ljust(64, b'\0') + struct.pack('<I', 0)
        directory = struct.pack('<6I', len(stream), 0, 0, 0, 0, len(name)) + name
        directory += b'\0' * (-len(name) % 4)
        primitive = struct.pack('<I', MAGIC) + stream + directory + struct.pack('<I', len(directory))
        data = {
            'maps/sky.model': b'<root><nodelessVisual>maps/sky</nodelessVisual></root>',
            'maps/sky.visual_processed': b'''<root>
              <node><identifier>Scene Root</identifier><transform>
                <row0>1 0 0</row0><row1>0 1 0</row1><row2>0 0 1</row2><row3>0 0 0</row3>
              </transform></node><renderSet><node>Scene Root</node><geometry><primitiveGroup>0
                <material><fx>shaders/environment/sky_box.fx</fx>
                  <property>diffuseMap<Texture>maps/sky.dds</Texture></property>
                  <property>LDR2HDRMap<Texture>unsupported_cube.dds</Texture></property>
                </material></primitiveGroup></geometry></renderSet></root>''',
            'maps/sky.primitives_processed': primitive,
            'maps/sky.dds': b'authored texture bytes',
            'shaders/formats/set3/xyznuvtbpc.xml': b'<root/>',
        }
        settings = ET.fromstring('''<root><skyDome>maps/sky.model</skyDome><skyWorld>
          <row0>0 0 1</row0><row1>0 1 0</row1><row2>-1 0 0</row2><row3>0 0 0</row3>
        </skyWorld></root>''')
        with tempfile.TemporaryDirectory() as temporary, patch.object(scene, 'PackageVFS') as vfs:
            vfs.return_value.index = {name: ('synthetic.pkg', name) for name in data}
            vfs.return_value.read.side_effect = data.__getitem__
            output = Path(temporary)
            resources, runtime = scene.prepare_skyboxes(Path('packages'), settings, output)
            self.assertEqual(resources, ['maps/sky.visual'])
            self.assertIn('shaders/environment/sky_box.fx', runtime)
            self.assertIn('shaders/formats/xyznuvtbpc.xml', runtime)
            visual = ET.parse(output / 'res/maps/sky.visual')
            self.assertEqual(visual.findtext('.//material/fx'), 'shaders/environment/sky_box.fx')
            self.assertEqual(visual.findtext('.//material/property/Texture'), 'maps/sky.dds')
            self.assertEqual(len(visual.findall('.//material/property')), 1)
            self.assertEqual(visual.findtext('node/transform/row0'), '0 0 1')
            self.assertEqual(visual.findtext('node/node/identifier'), 'Scene Root')
            self.assertEqual((output / 'res/maps/sky.primitives').read_bytes(), primitive)
            self.assertEqual((output / 'res/maps/sky.dds').read_bytes(), data['maps/sky.dds'])

    def test_unreviewed_sky_effect_is_rejected_instead_of_becoming_a_lit_vehicle_material(self):
        extractor = scene.SkyExtractor(None, Path('unused'))
        visual = ET.fromstring('<root><material><fx>unknown.fx</fx></material></root>')
        with self.assertRaisesRegex(ValueError, 'unreviewed sky effect'):
            extractor.preview_materials('sky.visual', visual)


if __name__ == '__main__':
    unittest.main()
