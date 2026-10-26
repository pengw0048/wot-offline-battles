"""Check that compiled model states retain their authored draw/collision split."""

from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from prepare_native_static_models import StaticModelStates, StaticModelInstances, static_model_chunks


class Resources:
    def __init__(self):
        self.files = {}

    def read(self, name):
        return self.files[name]


def primitives():
    data = struct.pack('<I', 0x42a14e65)
    directory = b''
    for name in (b'vertices', b'indices'):
        data += b'actual geometry bytes\0\0\0'
        length = len(b'actual geometry bytes\0\0\0')
        directory += struct.pack('<6I', length, 0, 0, 0, 0, len(name)) + name
        directory += b'\0' * (-len(name) % 4)
    return data + directory + struct.pack('<I', len(directory))


def fixture():
    resources = Resources()
    strings = {1: 'assets/object.primitives', 2: 'assets/object.primitives/vertices',
               3: 'assets/object.primitives/indices', 4: 'assets/lod.primitives/vertices',
               5: 'assets/lod.primitives/indices', 6: 'shaders/original.fx',
               7: 'transform', 8: 'signedValue', 9: 'enabled', 10: 'albedo', 11: 'image.dds'}
    visual = '''<root><primitivesName>{name}</primitivesName><node>
      <identifier>Scene Root</identifier></node><renderSet>
      <node>Scene Root</node><treatAsWorldSpaceObject>false</treatAsWorldSpaceObject>
      <geometry><vertices>vertices</vertices><primitive>indices</primitive>
      <primitiveGroup>0<material><identifier>normal</identifier><fx>source.fx</fx></material></primitiveGroup>
      <primitiveGroup>1<material><identifier>damaged</identifier><fx>source.fx</fx></material></primitiveGroup>
      </geometry></renderSet></root>'''
    for name in ('object', 'lod'):
        resources.files['assets/' + name + '.visual_processed'] = visual.format(name='assets/' + name).encode()
        resources.files['assets/' + name + '.primitives_processed'] = primitives()
    resources.files['assets/object.model'] = b'''<root><extent>10</extent>
      <nodelessVisual>incorrect/unused</nodelessVisual><nodefullVisual>assets/object</nodefullVisual>
      <parent>assets/lod</parent></root>'''
    resources.files['assets/lod.model'] = b'''<root><extent>20</extent>
      <nodelessVisual>assets/lod</nodelessVisual><parent>uncompiled/far</parent></root>'''
    def render(vertices, indices, group):
        return dict(node_begin=0xffffffff, node_end=0xffffffff, material_index=0,
                    primitive_index=group, verts_name_fnv=vertices,
                    prims_name_fnv=indices, is_skinned=0)
    collider = dict(bsp_section_name_fnv=1, bsp_material_kind_begin=0, bsp_material_kind_end=0)
    models = dict(
        models_loddings=[dict(lod_begin=0, lod_end=1), dict(lod_begin=2, lod_end=2)],
        models_colliders=[collider, dict(collider, bsp_material_kind_begin=1, bsp_material_kind_end=1)],
        bsp_material_kinds=[dict(material_index=7, flags=0x4983), dict(material_index=8, flags=0x4a90)],
        models_visibility_bounds=[dict(min=(-1, -2, -3), max=(1, 2, 3))] * 2,
        lod_loddings=[100., 400., 100.],
        lod_renders=[dict(render_set_begin=i, render_set_end=i) for i in range(3)],
        renders=[render(2, 3, 0), render(4, 5, 0), render(2, 3, 1)])
    materials = dict(materials=[dict(key_fx=0, key_from=0, key_to=3)], fx=[6],
        props=[dict(prop_fnv=7, value_type=4, value=0),
               dict(prop_fnv=8, value_type=3, value=0xffffffff),
               dict(prop_fnv=9, value_type=1, value=2),
               dict(prop_fnv=10, value_type=6, value=11)],
        matrices=[list(range(16))], vectors=[])
    instances = dict(bsmo_models_id=[0, 1], chunk_models=[dict(has_animations=0)] * 2)
    compiled = SimpleNamespace(sections=dict(BWST=strings, BSMO=SimpleNamespace(_data=models),
        BSMA=SimpleNamespace(_data=materials), BSMI=SimpleNamespace(_data=instances)))
    return compiled, resources


class StaticModelStatesTest(unittest.TestCase):
    def test_instance_activity_shared_animation_and_identity(self):
        compiled, resources = fixture()
        compiled.sections['BWST'].update({12: 'motion', 13: 'assets/motion.animation'})
        compiled.sections['BSMO']._data['animations'] = [dict(anim_name_fnv=12, nodes_name_fnv=13)]
        resources.files['assets/object.model'] = resources.files['assets/object.model'].replace(
            b'</root>', b'<animation><name>motion</name><nodes>assets/motion</nodes></animation></root>')
        source = compiled.sections['BSMI']._data
        source.update(transforms=[[1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.,
                                   -95., 2., 205., 1.]] * 4,
                      bsmo_models_id=[0, 1, 0, 0],
                      animations_id=[21, 21, -1, -1], visibility_masks=[1, 1, 1, 2],
                      chunk_models=[dict(has_animations=1, casts_shadow=1, reflection_visible=0,
                                         unknown=1 if i != 2 else 0) for i in range(4)],
                      model_animation=[dict(model_index=0, animation_id=0, frame_rate_multiplier=.2)])
        identity = dict(chunkID=32124, itemIndex=7, filename='assets/object.model')
        with tempfile.TemporaryDirectory() as output:
            states = StaticModelStates(compiled, resources, output, 'states')
            instances = StaticModelInstances(compiled, states, {0: identity, 1: identity})
            self.assertEqual([instances.active(i, 1) for i in range(4)], [True, True, False, False])
            first, second = [instances.section(i, (-100., 200.)) for i in (0, 1)]
            self.assertEqual(first.findtext('transform/row3'), '5.0 2.0 5.0')
            self.assertEqual(first.findtext('stockIdentity/itemIndex'), '7')
            self.assertEqual(second.findtext('stockIdentity/itemIndex'), '7')
            self.assertEqual(second.findtext('animation/name'), 'motion')
            self.assertEqual(second.findtext('animation/frameRateMultiplier'), '0.2')
            self.assertNotEqual(first.findtext('resource'), second.findtext('resource'))
            source['animations_id'][1] = 22
            with self.assertRaisesRegex(ValueError, 'no shared animation producer'):
                instances.section(1)

    def test_chunk_boundaries_use_native_floor_and_local_coordinates(self):
        compiled, resources = fixture()
        compiled.sections['BWST'].update({14: 'ffff0000o.cdata_processed/terrain2',
                                          15: '00000000o.cdata_processed/terrain2'})
        compiled.sections['BWT2'] = SimpleNamespace(_data=dict(
            settings=dict(chunk_size=100.), cdatas=[
                dict(loc_x=-1, loc_y=0, resource_fnv=14),
                dict(loc_x=0, loc_y=0, resource_fnv=15)]))
        source = compiled.sections['BSMI']._data
        source.update(transforms=[[1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.,
                                   x, 2., 5., 1.] for x in (-.5, 0.)],
                      animations_id=[-1, -1], model_animation=[], visibility_masks=[1, 1],
                      chunk_models=[dict(has_animations=0, casts_shadow=1,
                                         reflection_visible=0, unknown=1)] * 2)
        with tempfile.TemporaryDirectory() as output:
            states = StaticModelStates(compiled, resources, output, 'states')
            instances = StaticModelInstances(compiled, states, {})
            chunks = static_model_chunks(compiled, instances)
            self.assertEqual(chunks['ffff0000o.chunk'].findtext('wotModel/transform/row3'),
                             '99.5 2.0 5.0')
            self.assertEqual(chunks['00000000o.chunk'].findtext('wotModel/transform/row3'),
                             '0.0 2.0 5.0')
            source['transforms'][1][12] = 100.
            with self.assertRaisesRegex(ValueError, 'outside the compiled terrain grid'):
                static_model_chunks(compiled, instances)

    def test_normal_and_damaged_states_are_separate(self):
        compiled, resources = fixture()
        with tempfile.TemporaryDirectory() as output:
            writer = StaticModelStates(compiled, resources, output, 'states')
            normal, damaged = writer.write(0), writer.write(1)
            self.assertNotEqual(normal, damaged)
            tree = ET.parse(Path(output) / 'states/0/lod0.visual').getroot()
            group = tree.findall('./renderSet/geometry/primitiveGroup')
            self.assertEqual([x.text for x in group], ['0'])
            material = group[0].find('material')
            self.assertEqual(material.findtext('fx'), 'shaders/original.fx')
            self.assertEqual(material.findtext('identifier'), 'normal')
            props = {p.text: p for p in material.findall('property')}
            self.assertEqual(props['transform'].findtext('Matrix/row3'), '12 13 14 15')
            self.assertEqual(props['signedValue'].findtext('Int'), '-1')
            self.assertEqual(props['enabled'].findtext('Bool'), 'false')
            self.assertEqual(props['albedo'].findtext('Texture'), 'image.dds')
            self.assertEqual(tree.findtext('stockBsp/material/index'), '7')
            self.assertEqual(tree.findtext('stockBsp/material/flags'), str(0x4983))
            damaged_tree = ET.parse(Path(output) / 'states/1/lod0.visual').getroot()
            self.assertEqual(damaged_tree.find('renderSet/geometry/primitiveGroup').text, '1')
            self.assertEqual(damaged_tree.findtext('stockBsp/material/index'), '8')
            self.assertIsNone(ET.parse(Path(output) / 'states/0/lod1.model').find('parent'))
            self.assertEqual(ET.parse(Path(output) / normal).findtext('parent'), 'states/0/lod1')
            self.assertFalse(list(Path(output).rglob('*.primitives*')))

    def test_absent_source_descriptors_use_real_compiled_geometry(self):
        for absent_visual in (False, True):
            with self.subTest(absent_visual=absent_visual), tempfile.TemporaryDirectory() as output:
                compiled, resources = fixture()
                del resources.files['assets/object.model']
                if absent_visual:
                    del resources.files['assets/object.visual_processed']
                writer = StaticModelStates(compiled, resources, output, 'states')
                writer.write(0)
                model = ET.parse(Path(output) / 'states/0/lod0.model')
                self.assertEqual(float(model.findtext('extent')), 10.)
                visual = ET.parse(Path(output) / 'states/0/lod0.visual')
                self.assertIsNotNone(visual.find('renderSet/geometry/primitiveGroup'))

    def test_missing_geometry_and_bad_later_lod_publish_no_partial_state(self):
        for failure in ('geometry', 'later_lod', 'animated', 'property', 'nodes'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as output:
                compiled, resources = fixture()
                if failure in ('geometry', 'nodes'):
                    del resources.files['assets/object.visual_processed']
                    if failure == 'geometry':
                        del resources.files['assets/object.primitives_processed']
                    else:
                        compiled.sections['BSMO']._data['renders'][0]['node_begin'] = 0
                elif failure == 'later_lod':
                    compiled.sections['BSMO']._data['lod_loddings'][1] = 900.
                elif failure == 'animated':
                    del resources.files['assets/object.model']
                    compiled.sections['BSMI']._data['chunk_models'][0]['has_animations'] = 1
                else:
                    compiled.sections['BSMA']._data['props'][0]['value_type'] = 99
                writer = StaticModelStates(compiled, resources, output, 'states')
                with self.assertRaises((KeyError, ValueError)):
                    writer.write(0)
                self.assertEqual(list(Path(output).rglob('*.model')), [])

    def test_raw_and_absent_collision_are_explicit(self):
        compiled, resources = fixture()
        writer = StaticModelStates(compiled, resources, '/unused', 'states')
        collider = compiled.sections['BSMO']._data['models_colliders'][0]
        collider['bsp_material_kind_begin'] = collider['bsp_material_kind_end'] = 0xffffffff
        self.assertEqual(writer.collision(0).findtext('raw'), 'true')
        collider['bsp_section_name_fnv'] = 0
        self.assertEqual(writer.collision(0).findtext('disabled'), 'true')

    def test_absent_property_preserves_effect_default(self):
        compiled, resources = fixture()
        compiled.sections['BSMA']._data['props'][3]['value_type'] = 0
        with tempfile.TemporaryDirectory() as output:
            writer = StaticModelStates(compiled, resources, output, 'states')
            writer.write(0)
            visual = ET.parse(Path(output) / 'states/0/lod0.visual')
            names = [p.text for p in visual.findall('.//material/property')]
            self.assertNotIn('albedo', names)


if __name__ == '__main__':
    unittest.main()
