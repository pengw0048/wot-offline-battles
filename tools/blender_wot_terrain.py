"""Blender authoring front end; exports terrain sources, not playable maps."""

bl_info = {
    'name': 'WoT Terrain Source',
    'author': 'WoT Offline Battles contributors',
    'version': (0, 1, 0),
    'blender': (4, 3, 0),
    'location': 'View3D > Sidebar > WoT Map',
    'description': 'Create and export a new terrain source for the #1513 map experiment',
    'category': 'Import-Export',
}

import json
import math
import os
import tempfile

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper


INTERVALS = 32
BORDER = 2
CHUNK_METRES = 100.0


def new_terrain(context):
    """Create an editable 200 m square with the required two-sample apron."""
    scale = context.scene.unit_settings.scale_length
    spacing = CHUNK_METRES / INTERVALS
    side = 2 * INTERVALS + 2 * BORDER + 1
    vertices = [((-100 + (x - BORDER) * spacing) / scale,
                 (-100 + (y - BORDER) * spacing) / scale, 0)
                for y in range(side) for x in range(side)]
    faces = [(y * side + x, y * side + x + 1,
              (y + 1) * side + x + 1, (y + 1) * side + x)
             for y in range(side - 1) for x in range(side - 1)]
    mesh = bpy.data.meshes.new('TerrainSurface')
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new('Terrain_200m', mesh)
    context.collection.objects.link(obj)
    obj['wot_origin'] = [-100.0, -100.0]
    obj['wot_chunks'] = [2, 2]
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def terrain_source(context, obj):
    """Sample evaluated geometry in metres, including modifiers and transforms.

    Blender (X, Y, Z) maps to game (X, Y, Z) = (X, Z, Y).
    The stored rectangle is in world metres; moving its surface must still cover
    that rectangle and its apron. Decoration is deliberately excluded.
    """
    if obj is None or obj.type != 'MESH' or 'wot_chunks' not in obj:
        raise ValueError('Select a terrain created with New 200 m Terrain')
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    origin = list(obj.get('wot_origin', []))
    chunks = list(obj['wot_chunks'])
    if (len(origin) != 2 or any(not math.isfinite(v) for v in origin)
            or len(chunks) != 2
            or any(type(v) is not int or not 1 <= v <= 16 for v in chunks)):
        raise ValueError('Invalid terrain rectangle metadata')
    scale = context.scene.unit_settings.scale_length
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = [tuple((evaluated.matrix_world @ vertex.co) * scale)
                    for vertex in mesh.vertices]
        if not vertices or any(not math.isfinite(c) for v in vertices for c in v):
            raise ValueError('Terrain has no finite geometry')
        mesh.calc_loop_triangles()
        triangles = [tuple(tri.vertices) for tri in mesh.loop_triangles]
        if not triangles:
            raise ValueError('Terrain has no faces')
        spacing = CHUNK_METRES / INTERVALS
        width, height = [count * INTERVALS + 2 * BORDER + 1 for count in chunks]
        samples = [[None] * width for _ in range(height)]
        # Rasterize projected triangles instead of ray-casting exact vertices.
        # Blender BVH can miss boundary vertices; its epsilon also offsets hit
        # distances on slopes. Barycentric interpolation preserves those edges.
        for ia, ib, ic in triangles:
            ax, ay, az = vertices[ia]
            bx, by, bz = vertices[ib]
            cx, cy, cz = vertices[ic]
            determinant = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(determinant) < 1e-8:
                raise ValueError('Terrain has vertical or degenerate faces; use a heightfield surface')
            x0 = max(0, math.ceil((min(ax, bx, cx) - origin[0]) / spacing + BORDER - 1e-5))
            x1 = min(width - 1, math.floor((max(ax, bx, cx) - origin[0]) / spacing + BORDER + 1e-5))
            y0 = max(0, math.ceil((min(ay, by, cy) - origin[1]) / spacing + BORDER - 1e-5))
            y1 = min(height - 1, math.floor((max(ay, by, cy) - origin[1]) / spacing + BORDER + 1e-5))
            for row in range(y0, y1 + 1):
                y = origin[1] + (row - BORDER) * spacing
                for column in range(x0, x1 + 1):
                    x = origin[0] + (column - BORDER) * spacing
                    a = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / determinant
                    b = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / determinant
                    c = 1 - a - b
                    if min(a, b, c) < -1e-5:
                        continue
                    z = a * az + b * bz + c * cz
                    previous = samples[row][column]
                    if previous is not None and abs(previous - z) > 0.002:
                        raise ValueError('Terrain has overlapping surfaces at (%.3f, %.3f)' % (x, y))
                    if previous is None:
                        samples[row][column] = z
        rows = []
        for row, samples_row in enumerate(samples):
            values = []
            for column, z in enumerate(samples_row):
                if z is None:
                    x = origin[0] + (column - BORDER) * spacing
                    y = origin[1] + (row - BORDER) * spacing
                    raise ValueError('Terrain does not cover sample (%.3f, %.3f), including its border' % (x, y))
                mm = round(z * 1000)
                if not -(2 ** 31) <= mm < 2 ** 31:
                    raise ValueError('Terrain height exceeds signed 32-bit millimetres')
                values.append(mm)
            rows.append(values)
        return {
            'format': 'wot-map-source', 'version': 1,
            'terrain': {'origin': origin, 'chunks': chunks,
                        'intervals_per_chunk': INTERVALS, 'border': BORDER,
                        'heights_mm': rows},
        }
    finally:
        evaluated.to_mesh_clear()


def write_source(path, source):
    directory = os.path.dirname(os.path.abspath(path))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=directory, delete=False) as out:
            temporary = out.name
            json.dump(source, out, allow_nan=False, separators=(',', ':'))
            out.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class WOTMAP_OT_new_terrain(bpy.types.Operator):
    bl_idname = 'wotmap.new_terrain'
    bl_label = 'New 200 m Terrain'
    bl_description = 'Add a flat terrain surface; sculpt its height while retaining its outer border'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        new_terrain(context)
        return {'FINISHED'}


class WOTMAP_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.wot_terrain_source'
    bl_label = 'Export Terrain Source'
    bl_description = 'Export the selected evaluated terrain; this is not a playable game map'
    filename_ext = '.wotmap.json'
    filter_glob: StringProperty(default='*.wotmap.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            source = terrain_source(context, context.active_object)
            write_source(self.filepath, source)
        except (ValueError, OSError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Terrain source exported; native map compilation is still required')
        return {'FINISHED'}


class WOTMAP_PT_panel(bpy.types.Panel):
    bl_label = 'WoT Map Authoring'
    bl_idname = 'WOTMAP_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'WoT Map'

    def draw(self, context):
        layout = self.layout
        layout.operator('wotmap.new_terrain', icon='MESH_GRID')
        layout.operator('export_scene.wot_terrain_source', icon='EXPORT')
        layout.label(text='Terrain source prototype', icon='INFO')
        layout.label(text='Game compilation is not included.')


def export_menu(self, context):
    self.layout.operator(WOTMAP_OT_export.bl_idname, text='WoT Terrain Source (.wotmap.json)')


CLASSES = (WOTMAP_OT_new_terrain, WOTMAP_OT_export, WOTMAP_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(export_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(export_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
