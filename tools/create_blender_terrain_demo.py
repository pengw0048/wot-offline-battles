"""Create an editable terrain example and an explicitly non-game preview.

Run with Blender --background --python this_file.py -- --output-dir DIRECTORY.
"""

import argparse
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_wot_terrain as terrain_addon


def terrain_height(x, y):
    """An original ridge and valley, with smooth, editable transitions."""
    ridge = 49.0 * math.exp(-((x + 20.0) / 51.0) ** 2 - ((y - 30.0) / 46.0) ** 2)
    shoulder = 18.0 * math.exp(-((x - 50.0) / 38.0) ** 2 - ((y - 49.0) / 36.0) ** 2)
    valley_axis = -34.0 + 0.22 * x + 5.0 * math.sin(x / 36.0)
    valley = 13.0 * math.exp(-((y - valley_axis) / 17.0) ** 2)
    return ridge + shoulder - valley


def solid_material(name, color, roughness=0.8):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    return material


def terrain_material():
    material = solid_material('Elevation bands - authoring aid', (0.24, 0.43, 0.29))
    nodes, links = material.node_tree.nodes, material.node_tree.links
    geometry = nodes.new('ShaderNodeNewGeometry')
    xyz = nodes.new('ShaderNodeSeparateXYZ')
    links.new(geometry.outputs['Position'], xyz.inputs[0])
    height = nodes.new('ShaderNodeMapRange')
    height.inputs['From Min'].default_value = -13.0
    height.inputs['From Max'].default_value = 50.0
    links.new(xyz.outputs['Z'], height.inputs['Value'])
    ramp = nodes.new('ShaderNodeValToRGB')
    colors = [(0.00, (0.045, 0.16, 0.17, 1)),
              (0.20, (0.10, 0.29, 0.25, 1)),
              (0.43, (0.30, 0.46, 0.27, 1)),
              (0.66, (0.59, 0.60, 0.35, 1)),
              (0.84, (0.79, 0.70, 0.48, 1)),
              (1.00, (0.92, 0.85, 0.65, 1))]
    for element in list(ramp.color_ramp.elements)[1:]:
        ramp.color_ramp.elements.remove(element)
    for index, (position, color) in enumerate(colors):
        element = ramp.color_ramp.elements[0] if index == 0 else ramp.color_ramp.elements.new(position)
        element.position, element.color = position, color
    links.new(height.outputs['Result'], ramp.inputs['Fac'])
    period = nodes.new('ShaderNodeMath')
    period.operation = 'MULTIPLY'
    period.inputs[1].default_value = 0.2
    links.new(xyz.outputs['Z'], period.inputs[0])
    fraction = nodes.new('ShaderNodeMath')
    fraction.operation = 'FRACT'
    links.new(period.outputs[0], fraction.inputs[0])
    contour = nodes.new('ShaderNodeMath')
    contour.operation = 'LESS_THAN'
    contour.inputs[1].default_value = 0.025
    links.new(fraction.outputs[0], contour.inputs[0])
    mix = nodes.new('ShaderNodeMixRGB')
    links.new(contour.outputs[0], mix.inputs[0])
    links.new(ramp.outputs['Color'], mix.inputs[1])
    mix.inputs[2].default_value = (0.055, 0.11, 0.085, 1)
    links.new(mix.outputs[0], nodes.get('Principled BSDF').inputs['Base Color'])
    return material


def link_object(collection, name, data):
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    return obj


def terrain_skirt(collection, terrain):
    side = 2 * terrain_addon.INTERVALS + 2 * terrain_addon.BORDER + 1
    perimeter = (list(range(side)) + [y * side + side - 1 for y in range(1, side)]
                 + list(range(side * side - 2, side * (side - 1) - 1, -1))
                 + [y * side for y in range(side - 2, 0, -1)])
    upper = [tuple(terrain.data.vertices[index].co) for index in perimeter]
    count = len(upper)
    vertices = upper + [(x, y, -20.0) for x, y, _ in upper]
    faces = [(i, (i + 1) % count, (i + 1) % count + count, i + count)
             for i in range(count)]
    mesh = bpy.data.meshes.new('Preview tile edge')
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = link_object(collection, 'Preview tile edge - excluded from export', mesh)
    obj.data.materials.append(solid_material('Tile edge', (0.055, 0.105, 0.085)))


def text_label(collection, camera, body, x, y, size, color):
    font = bpy.data.curves.new(body, 'FONT')
    font.body = body
    font.size = size
    font.space_character = 1.1
    obj = link_object(collection, body, font)
    obj.rotation_euler = camera.rotation_euler
    obj.location = camera.location + camera.rotation_euler.to_quaternion() @ Vector((x, y, -200))
    material = bpy.data.materials.new(body + ' ink')
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (*color, 1)
    links.new(emission.outputs[0], nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
    font.materials.append(material)


def setup_preview(scene, terrain):
    collection = bpy.data.collections.new('Preview only - excluded from terrain export')
    scene.collection.children.link(collection)
    terrain_skirt(collection, terrain)
    floor_mesh = bpy.data.meshes.new('Preview backdrop')
    floor_mesh.from_pydata([(-2000, -2000, -20.1), (2000, -2000, -20.1),
                           (2000, 2000, -20.1), (-2000, 2000, -20.1)], [], [(0, 1, 2, 3)])
    floor = link_object(collection, 'Preview backdrop', floor_mesh)
    floor.data.materials.append(solid_material('Warm paper', (0.77, 0.80, 0.74)))
    camera = link_object(collection, 'Preview camera', bpy.data.cameras.new('Preview camera'))
    camera.location = (280, -330, 290)
    camera.rotation_euler = (Vector((0, 0, 12)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 375
    camera.data.clip_end = 3000
    scene.camera = camera
    sun = link_object(collection, 'Preview sunlight', bpy.data.lights.new('Preview sunlight', 'SUN'))
    sun.rotation_euler = (math.radians(25), math.radians(-28), math.radians(-28))
    sun.data.energy = 2.2
    sun.data.angle = 0.12
    scene.world = bpy.data.worlds.new('Preview world')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (0.68, 0.76, 0.83, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = 0.45
    text_label(collection, camera, 'RIDGE & VALLEY', -167, 117, 8.2, (0.025, 0.07, 0.055))
    text_label(collection, camera, 'An original, editable Blender terrain', -167, 106, 4.2, (0.12, 0.19, 0.15))
    text_label(collection, camera, '200 x 200 m  /  5 m elevation contours', -167, -114, 4.2, (0.08, 0.15, 0.11))
    text_label(collection, camera, 'BLENDER AUTHORING PREVIEW - NOT A GAME RENDER', -167, -124, 3.3, (0.18, 0.24, 0.20))
    for obj in collection.objects:
        obj.hide_set(True)
    # hide_set affects the interactive viewport, while the preview remains renderable.
    return camera


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        parser.error('Output directory must be empty; existing Blender work will not be overwritten')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    terrain_addon.register()
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0
    terrain = terrain_addon.new_terrain(bpy.context)
    terrain.name = 'Ridge and Valley - EDIT THIS TERRAIN'
    for vertex in terrain.data.vertices:
        vertex.co.z = terrain_height(vertex.co.x, vertex.co.y)
    terrain.data.update()
    terrain.data.materials.append(terrain_material())
    for polygon in terrain.data.polygons:
        polygon.use_smooth = True
    source = terrain_addon.terrain_source(bpy.context, terrain)
    heights = [value for row in source['terrain']['heights_mm'] for value in row]
    assert min(heights) < 0 < 32767 < max(heights)
    source_path = args.output_dir / 'ridge-and-valley.wotmap.json'
    terrain_addon.write_source(str(source_path), source)
    camera = setup_preview(scene, terrain)
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1200
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = str(args.output_dir / 'terrain-preview.png')
    scene.view_settings.view_transform = 'AgX'
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.show_region_ui = True
                space.clip_end = 3000
                space.region_3d.view_location = (0, 0, 10)
                space.region_3d.view_distance = 340
                space.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
                space.region_3d.view_perspective = 'ORTHO'
                space.shading.color_type = 'MATERIAL'
    bpy.context.view_layer.objects.active = terrain
    terrain.select_set(True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir / 'ridge-and-valley.blend'))
    bpy.ops.render.render(write_still=True)
    print('Created editable Blender terrain: %s' % (args.output_dir / 'ridge-and-valley.blend'))
    print('Exported evaluated surface: %s; %d x %d samples; range %.3f to %.3f metres' %
          (source_path, len(source['terrain']['heights_mm'][0]), len(source['terrain']['heights_mm']),
           min(heights) / 1000.0, max(heights) / 1000.0))
    print('Rendered authoring preview: %s' % scene.render.filepath)


if __name__ == '__main__':
    main()
