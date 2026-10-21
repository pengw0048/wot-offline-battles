"""Real Blender integration checks; run with Blender --background --python."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import blender_wot_terrain as terrain_addon


class BlenderTerrainSmoke(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.system = 'METRIC'
        bpy.context.scene.unit_settings.scale_length = 1.0

    def test_flat_surface_and_operator_export(self):
        terrain_addon.register()
        try:
            self.assertEqual(bpy.ops.wotmap.new_terrain(), {'FINISHED'})
            obj = bpy.context.active_object
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'flat.wotmap.json'
                self.assertEqual(bpy.ops.export_scene.wot_terrain_source(filepath=str(path)), {'FINISHED'})
                source = json.loads(path.read_text())
            self.assertEqual(source['format'], 'wot-map-source')
            self.assertEqual(source['version'], 1)
            self.assertEqual(source['terrain']['origin'], [-100.0, -100.0])
            self.assertEqual(source['terrain']['chunks'], [2, 2])
            self.assertEqual(source['terrain']['border'], 2)
            self.assertEqual(source['terrain']['intervals_per_chunk'], 32)
            rows = source['terrain']['heights_mm']
            self.assertEqual(len(rows), 69)
            self.assertTrue(all(len(row) == 69 for row in rows))
            self.assertEqual(set(value for row in rows for value in row), {0})
            self.assertIs(obj, bpy.context.active_object)
        finally:
            terrain_addon.unregister()

    def test_slope_sign_axis_order_and_large_heights(self):
        obj = terrain_addon.new_terrain(bpy.context)
        for vertex in obj.data.vertices:
            vertex.co.z = 8.0 + 0.25 * vertex.co.x + 0.125 * vertex.co.y
        obj.data.update()
        source = terrain_addon.terrain_source(bpy.context, obj)
        rows = source['terrain']['heights_mm']
        for row in (0, 2, 34, 66, 68):
            y = -100.0 + (row - 2) * 3.125
            for column in (0, 2, 34, 66, 68):
                x = -100.0 + (column - 2) * 3.125
                self.assertAlmostEqual(rows[row][column], round((8.0 + 0.25 * x + 0.125 * y) * 1000), delta=1)
        self.assertLess(rows[0][0], 0)
        self.assertGreater(rows[-1][-1], 32767)

    def test_scene_units_world_transform_and_evaluated_modifier(self):
        bpy.context.scene.unit_settings.scale_length = 0.01
        obj = terrain_addon.new_terrain(bpy.context)
        for vertex in obj.data.vertices:
            vertex.co.z = 125.0
        obj.scale.z = 2.0
        obj.location.z = 250.0
        modifier = obj.modifiers.new('Evaluated height offset', 'DISPLACE')
        modifier.direction = 'Z'
        modifier.strength = 75.0
        modifier.mid_level = 0.0
        obj.data.update()
        bpy.context.view_layer.update()
        source = terrain_addon.terrain_source(bpy.context, obj)
        self.assertEqual(set(value for row in source['terrain']['heights_mm'] for value in row), {6500})
        self.assertEqual(len(obj.modifiers), 1)
        self.assertEqual(obj.data.vertices[0].co.z, 125.0)

    def test_hole_is_rejected(self):
        obj = terrain_addon.new_terrain(bpy.context)
        vertices = [tuple(vertex.co) for vertex in obj.data.vertices]
        faces = []
        for polygon in obj.data.polygons:
            points = [vertices[index] for index in polygon.vertices]
            covers_origin = (min(p[0] for p in points) <= 0 <= max(p[0] for p in points)
                             and min(p[1] for p in points) <= 0 <= max(p[1] for p in points))
            if not covers_origin:
                faces.append(tuple(polygon.vertices))
        obj.data.clear_geometry()
        obj.data.from_pydata(vertices, [], faces)
        obj.data.update()
        with self.assertRaisesRegex(ValueError, r'does not cover sample \(0\.000, 0\.000\)'):
            terrain_addon.terrain_source(bpy.context, obj)

    def test_overlapping_ground_is_rejected(self):
        obj = terrain_addon.new_terrain(bpy.context)
        vertices = [tuple(vertex.co) for vertex in obj.data.vertices]
        faces = [tuple(polygon.vertices) for polygon in obj.data.polygons]
        count = len(vertices)
        vertices += [(x, y, z + 10.0) for x, y, z in vertices]
        faces += [tuple(index + count for index in face) for face in faces]
        obj.data.clear_geometry()
        obj.data.from_pydata(vertices, [], faces)
        obj.data.update()
        with self.assertRaisesRegex(ValueError, 'overlapping surfaces'):
            terrain_addon.terrain_source(bpy.context, obj)

    def test_save_reopen_edit_and_export_again(self):
        obj = terrain_addon.new_terrain(bpy.context)
        obj.location.z = 2.0
        object_name = obj.name
        with tempfile.TemporaryDirectory() as directory:
            blend_path = str(Path(directory) / 'editable.blend')
            source_path = str(Path(directory) / 'editable.wotmap.json')
            before = terrain_addon.terrain_source(bpy.context, obj)
            self.assertEqual(set(value for row in before['terrain']['heights_mm'] for value in row), {2000})
            terrain_addon.write_source(source_path, before)
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            bpy.ops.wm.open_mainfile(filepath=blend_path, load_ui=False)
            restored = bpy.data.objects[object_name]
            self.assertEqual(list(restored['wot_origin']), [-100.0, -100.0])
            self.assertEqual(list(restored['wot_chunks']), [2, 2])
            self.assertIs(bpy.context.active_object, restored)
            bpy.ops.object.mode_set(mode='EDIT')
            edited_mesh = bmesh.from_edit_mesh(restored.data)
            for vertex in edited_mesh.verts:
                vertex.co.z += 3.0
            bmesh.update_edit_mesh(restored.data)
            after = terrain_addon.terrain_source(bpy.context, restored)
            terrain_addon.write_source(source_path, after)
            exported = json.loads(Path(source_path).read_text())
            self.assertEqual(set(value for row in exported['terrain']['heights_mm'] for value in row), {5000})
            self.assertEqual(exported['terrain']['origin'], before['terrain']['origin'])
            self.assertEqual(exported['terrain']['chunks'], before['terrain']['chunks'])
            self.assertEqual(restored.mode, 'EDIT')
            bpy.ops.object.mode_set(mode='OBJECT')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BlenderTerrainSmoke))
    if not result.wasSuccessful():
        raise SystemExit(1)
