#!/usr/bin/env python3
"""Bake a deterministic terrain navigation graph from WoT 0.8.2 packages.

This tool is intentionally offline.  It reads the map terrain, water planes,
static model transforms, visual bounds, and primitive vertices directly from
the pinned client packages.  The resulting JSON is small enough to ship with
the mod, so players never have to scan a map in game.

Map bounds and validation anchors come from the tactical route registry shipped
with the mod. Its bases are checked against the pinned client's standard-battle
arena definitions before baking. ``--all`` bakes every stock map with the same
generic terrain, water, grade, obstacle, and connectivity rules.
"""

import argparse
import base64
import hashlib
import heapq
import importlib.util
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import types
import zipfile
import zlib
import xml.etree.ElementTree as ElementTree


TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from build_navmesh_probe import (  # noqa: E402
    TYPE_COMPRESSED_STRING,
    TYPE_ELEMENT,
    TYPE_INTEGER,
    TYPE_STRING,
    TYPE_VECTOR,
    read_packed_xml,
)


FORMAT_NAME = "offhangar-navgraph"
FORMAT_VERSION = 1
GAME_VERSION = "0.8.2"
CHUNK_SIZE = 100.0
HEIGHTMAP_INNER_SIZE = 64
HEIGHTMAP_BORDER = 2
# Stock maps deliberately use shallow, tank-passable fords. Deep water remains
# forbidden, while traversable water receives a large route cost below so bots
# use a dry road or bridge whenever one exists.
WATER_DEPTH_LIMIT = 0.90
SHALLOW_WATER_THRESHOLD = 0.12
VEHICLE_HALF_WIDTH = 2.15
BRIDGE_OBSTACLE_MARGIN = 1.0
VEHICLE_CLEARANCE_HEIGHT = 2.40
LOCAL_OBSTACLE_MAX_HEIGHT = 0.65
VEHICLE_GROUND_CLEARANCE = LOCAL_OBSTACLE_MAX_HEIGHT
MAX_GRADE_UP = 0.38
MAX_GRADE_DOWN = 0.38
# Normal tank routes must be controllable in both directions.  A drop that can
# be slid down but not climbed back up is an emergency transition, not a route
# shortcut, so the retained graph uses the stricter directional limit.
MAX_GRADE = min(MAX_GRADE_UP, MAX_GRADE_DOWN)
EDGE_CLEARANCE_RADII = (3.0, 6.0)
HAZARD_WATER = 1
HAZARD_EDGE = 2
HAZARD_SHALLOW_WATER = 4
SHALLOW_WATER_COST_MULTIPLIER = 4.0
NAVGRAPH_BOUND_MARGIN = 16.0
MAX_ROUTE_DETOUR = 2.0
MAX_ROUTE_TURN = math.radians(150.0)

DIRECTIONS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0),              (1, 0),
    (-1, 1),  (0, 1),     (1, 1),
)

def _load_tactical_maps():
    """Load the Python-2-compatible data modules without importing the game."""
    package_dir = os.path.join(
        REPO_ROOT, "scripts", "client", "gui", "mods", "offhangar"
    )
    package_names = ("gui", "gui.mods", "gui.mods.offhangar")
    saved = dict((name, sys.modules.get(name)) for name in package_names)
    loaded_names = []
    try:
        for name in package_names:
            package = types.ModuleType(name)
            package.__path__ = [package_dir] if name == "gui.mods.offhangar" else []
            sys.modules[name] = package
        module_name = "gui.mods.offhangar.bot_ai_maps"
        spec = importlib.util.spec_from_file_location(
            module_name, os.path.join(package_dir, "bot_ai_maps.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        loaded_names.append(module_name)
        spec.loader.exec_module(module)
        return dict(module.TACTICAL_MAPS)
    finally:
        for name in list(sys.modules):
            if (name.startswith("gui.mods.offhangar.bot_ai_maps_group_") or
                    name == "gui.mods.offhangar.bot_ai_maps_extra" or
                    name in loaded_names):
                sys.modules.pop(name, None)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _bake_map_config(tactical_map):
    route_records = []
    anchors = []
    for team in (1, 2):
        for route in tactical_map.get("routes", {}).get(team, ()):
            points = tuple((float(point[0]), float(point[1]),
                            bool(point[2]) if len(point) > 2 else False)
                           for point in route.get("waypoints", ()))
            if not points:
                continue
            route_records.append({
                "team": team,
                "id": str(route.get("id") or "route"),
                "capacity": max(1, int(route.get("capacity", 1))),
                "risk": float(route.get("risk", 0.5)),
                "role_weights": dict(route.get("role_weights", {})),
                "points": points,
            })
            anchors.extend((point[0], point[1]) for point in points)
    bases = (
        tuple(tactical_map["bases"][1]),
        tuple(tactical_map["bases"][2]),
    )
    anchors.extend(bases)
    return {
        "bounds": tuple(tactical_map["bounds"]),
        "bases": bases,
        "routes": tuple(route_records),
        "anchors": tuple(anchors),
    }


TACTICAL_MAPS = _load_tactical_maps()
MAPS = dict((name, _bake_map_config(tactical_map))
            for name, tactical_map in TACTICAL_MAPS.items())


def _expanded_bounds(map_config, cell_size):
    """Include route/base anchors that sit on the arena boundary."""
    bounds = map_config["bounds"]
    anchors = tuple(map_config.get("anchors", ())) + tuple(map_config["bases"])
    minimum_x = min([bounds[0]] + [point[0] - NAVGRAPH_BOUND_MARGIN
                                   for point in anchors])
    minimum_z = min([bounds[1]] + [point[1] - NAVGRAPH_BOUND_MARGIN
                                   for point in anchors])
    maximum_x = max([bounds[2]] + [point[0] + NAVGRAPH_BOUND_MARGIN
                                   for point in anchors])
    maximum_z = max([bounds[3]] + [point[1] + NAVGRAPH_BOUND_MARGIN
                                   for point in anchors])
    size = float(cell_size)
    return (
        math.floor(minimum_x / size) * size,
        math.floor(minimum_z / size) * size,
        math.ceil(maximum_x / size) * size,
        math.ceil(maximum_z / size) * size,
    )


def _packed_children(element, name):
    encoded = name.encode("ascii")
    return [value for child_name, value in element.children
            if child_name == encoded]


def _packed_child(element, name, required=True):
    values = _packed_children(element, name)
    if values:
        return values[0]
    if required:
        raise ValueError("missing Packed XML child %s" % name)
    return None


def _packed_text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode("ascii")
    if value.value_type != TYPE_STRING:
        raise ValueError("Packed XML value is not text")
    return value.value.decode("utf-8")


def _packed_vector(value):
    if value.value_type != TYPE_VECTOR or len(value.value) % 4:
        raise ValueError("Packed XML value is not a float vector")
    return struct.unpack("<%df" % (len(value.value) // 4), value.value)


def _packed_integer(value, default=None):
    if value is None:
        return default
    if value.value_type != TYPE_INTEGER:
        raise ValueError("Packed XML value is not an integer")
    return int(value.value)


def _packed_vector2(value):
    """Decode the Vector2 or historical text form used by arena definitions."""
    if value.value_type == TYPE_VECTOR:
        vector = _packed_vector(value)
        if len(vector) < 2:
            raise ValueError("Packed XML vector has fewer than two coordinates")
        return float(vector[0]), float(vector[1])
    if value.value_type == TYPE_STRING:
        parts = _packed_text(value).split()
        if len(parts) < 2:
            raise ValueError("Packed XML position has fewer than two coordinates")
        return float(parts[0]), float(parts[1])
    raise ValueError("Packed XML position is neither a vector nor text")


def ctf_bases_from_arena_data(data):
    """Read the two standard-battle base positions from packed arena XML."""
    root = read_packed_xml(data)
    gameplay = _packed_child(root, "gameplayTypes")
    if gameplay.value_type != TYPE_ELEMENT:
        raise ValueError("arena gameplayTypes is not an element")
    ctf = _packed_child(gameplay.value, "ctf")
    if ctf.value_type != TYPE_ELEMENT:
        raise ValueError("arena ctf gameplay type is not an element")
    positions = _packed_child(ctf.value, "teamBasePositions")
    if positions.value_type != TYPE_ELEMENT:
        raise ValueError("ctf teamBasePositions is not an element")
    bases = []
    for team in (1, 2):
        team_value = _packed_child(positions.value, "team%d" % team)
        if team_value.value_type != TYPE_ELEMENT:
            raise ValueError("ctf team%d base list is not an element" % team)
        candidates = [value for name, value in team_value.value.children
                      if name.startswith(b"position")]
        if not candidates:
            raise ValueError("ctf team%d has no base position" % team)
        bases.append(_packed_vector2(candidates[0]))
    return tuple(bases)


def read_client_ctf_bases(client_root, map_name):
    path = os.path.join(
        os.path.abspath(client_root), "res", "scripts", "arena_defs",
        map_name + ".xml",
    )
    if not os.path.isfile(path):
        raise ValueError("arena definition not found: %s" % path)
    with open(path, "rb") as arena_file:
        return ctf_bases_from_arena_data(arena_file.read())


def validate_tactical_bases(map_name, map_config, actual_bases, tolerance=1.0):
    """Reject route metadata from another gameplay type or swapped teams."""
    expected_bases = tuple(map_config["bases"])
    if len(actual_bases) != 2 or len(expected_bases) != 2:
        raise ValueError("%s must define exactly two ctf bases" % map_name)
    for team, (expected, actual) in enumerate(
            zip(expected_bases, actual_bases), 1):
        offset = math.hypot(float(expected[0]) - float(actual[0]),
                            float(expected[1]) - float(actual[1]))
        if offset > float(tolerance):
            raise ValueError(
                "%s tactical team%d base differs from arena ctf by %.1f m "
                "(tactical=(%.1f, %.1f), ctf=(%.1f, %.1f))" % (
                    map_name, team, offset,
                    expected[0], expected[1], actual[0], actual[1],
                )
            )


def _signed_hex16(value):
    number = int(value, 16)
    return number - 65536 if number >= 32768 else number


def chunk_coordinates(name):
    base = os.path.basename(name).split(".", 1)[0]
    if len(base) < 8:
        raise ValueError("invalid outside chunk name %s" % name)
    return _signed_hex16(base[:4]), _signed_hex16(base[4:8])


class PackageResources(object):
    def __init__(self, package_paths):
        self.archives = []
        self.names = []
        for path in package_paths:
            archive = zipfile.ZipFile(path, "r")
            self.archives.append(archive)
            self.names.append(set(archive.namelist()))

    def close(self):
        for archive in self.archives:
            archive.close()

    def read(self, name):
        for archive, names in zip(self.archives, self.names):
            if name in names:
                return archive.read(name)
        raise KeyError(name)

    def contains(self, name):
        return any(name in names for names in self.names)

    def iter_names(self, suffix=None, prefix=None):
        seen = set()
        for names in self.names:
            for name in names:
                if name in seen:
                    continue
                if suffix is not None and not name.endswith(suffix):
                    continue
                if prefix is not None and not name.startswith(prefix):
                    continue
                seen.add(name)
                yield name


def soft_destructible_models(data):
    """Return models a tank can push through instead of routing around."""
    root = read_packed_xml(data)
    result = set()
    for category_name in ("fragiles", "fallingAtoms"):
        category_value = _packed_child(root, category_name, False)
        if category_value is None or category_value.value_type != TYPE_ELEMENT:
            continue
        for entry_value in _packed_children(category_value.value, "entry"):
            if entry_value.value_type != TYPE_ELEMENT:
                continue
            filename_value = _packed_child(entry_value.value, "filename", False)
            if filename_value is not None:
                result.add(_packed_text(filename_value))
    return result


def decode_png_rgba(png):
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature")
    position = 8
    width = height = None
    compressed = []
    while position < len(png):
        length = struct.unpack_from(">I", png, position)[0]
        kind = png[position + 4:position + 8]
        data = png[position + 8:position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if (bit_depth, color_type, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError("unsupported height PNG encoding")
        elif kind == b"IDAT":
            compressed.append(data)
        elif kind == b"IEND":
            break
    if width is None or height is None:
        raise ValueError("PNG has no IHDR")
    raw = zlib.decompress(b"".join(compressed))
    bytes_per_pixel = 4
    stride = width * bytes_per_pixel
    rows = []
    previous = bytearray(stride)
    offset = 0
    for unused_y in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        for index in range(stride):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - upper_left
                left_error = abs(estimate - left)
                above_error = abs(estimate - above)
                corner_error = abs(estimate - upper_left)
                if left_error <= above_error and left_error <= corner_error:
                    predictor = left
                elif above_error <= corner_error:
                    predictor = above
                else:
                    predictor = upper_left
            elif filter_type == 0:
                predictor = 0
            else:
                raise ValueError("unsupported PNG filter %d" % filter_type)
            row[index] = (row[index] + predictor) & 255
        rows.append(row)
        previous = row
    return width, height, rows


class HeightChunk(object):
    def __init__(self, data):
        if len(data) < 36 or data[:4] != b"hmp\0":
            raise ValueError("invalid terrain2 height header")
        width, height = struct.unpack_from("<II", data, 4)
        png_width, png_height, rows = decode_png_rgba(data[36:])
        if (width, height) != (png_width, png_height):
            raise ValueError("height header and PNG dimensions differ")
        if width < HEIGHTMAP_INNER_SIZE + HEIGHTMAP_BORDER * 2 + 1:
            raise ValueError("height map has no expected terrain border")
        self.width = width
        self.height = height
        self.minimum = struct.unpack_from("<f", data, 20)[0]
        self.maximum = struct.unpack_from("<f", data, 24)[0]
        self.values = []
        for row in rows:
            values = []
            for x in range(width):
                # BigWorld terrain2 version 4 stores one little-endian int32
                # millimetre value in each PNG RGBA pixel. Reading only R/G as
                # an int16 wraps hills above 32.767 m and truncates every map
                # whose terrain leaves the signed-16-bit range.
                millimetres = struct.unpack(
                    "<i", bytes(row[x * 4:x * 4 + 4])
                )[0]
                values.append(millimetres / 1000.0)
            self.values.append(values)

    def sample(self, local_x, local_z):
        image_x = HEIGHTMAP_BORDER + local_x * HEIGHTMAP_INNER_SIZE / CHUNK_SIZE
        image_z = HEIGHTMAP_BORDER + local_z * HEIGHTMAP_INNER_SIZE / CHUNK_SIZE
        image_x = max(0.0, min(self.width - 1.0, image_x))
        image_z = max(0.0, min(self.height - 1.0, image_z))
        x0 = int(math.floor(image_x))
        z0 = int(math.floor(image_z))
        x1 = min(self.width - 1, x0 + 1)
        z1 = min(self.height - 1, z0 + 1)
        fx = image_x - x0
        fz = image_z - z0
        lower = self.values[z0][x0] * (1.0 - fx) + self.values[z0][x1] * fx
        upper = self.values[z1][x0] * (1.0 - fx) + self.values[z1][x1] * fx
        return lower * (1.0 - fz) + upper * fz


class WaterPlane(object):
    def __init__(self, position, size, orientation):
        self.x = float(position[0])
        self.y = float(position[1])
        self.z = float(position[2])
        self.half_x = abs(float(size[0])) * 0.5
        self.half_z = abs(float(size[2])) * 0.5
        self.angle = float(orientation[0]) if orientation else 0.0
        self.cosine = math.cos(-self.angle)
        self.sine = math.sin(-self.angle)

    def contains(self, x, z):
        dx = float(x) - self.x
        dz = float(z) - self.z
        local_x = dx * self.cosine - dz * self.sine
        local_z = dx * self.sine + dz * self.cosine
        return abs(local_x) <= self.half_x and abs(local_z) <= self.half_z


class Terrain(object):
    def __init__(self, resources, map_name):
        self.resources = resources
        self.map_name = map_name
        self.prefix = "spaces/%s/" % map_name
        self.chunks = {}
        self.waters = []
        self._load()

    def _load(self):
        for name in self.resources.iter_names(suffix=".cdata", prefix=self.prefix):
            chunk_name = os.path.basename(name).split(".", 1)[0]
            try:
                coordinates = chunk_coordinates(chunk_name)
            except ValueError:
                continue
            with zipfile.ZipFile(io.BytesIO(self.resources.read(name)), "r") as archive:
                if "terrain2/heights" not in archive.namelist():
                    continue
                self.chunks[coordinates] = HeightChunk(archive.read("terrain2/heights"))
        for name in self.resources.iter_names(suffix=".vlo", prefix=self.prefix):
            root = read_packed_xml(self.resources.read(name))
            for value in _packed_children(root, "water"):
                if value.value_type != TYPE_ELEMENT:
                    continue
                element = value.value
                position = _packed_vector(_packed_child(element, "position"))
                size = _packed_vector(_packed_child(element, "size"))
                orientation_value = _packed_child(element, "orientation", False)
                orientation = _packed_vector(orientation_value) if orientation_value else (0.0,)
                self.waters.append(WaterPlane(position, size, orientation))
        if not self.chunks:
            raise ValueError("no terrain chunks found for %s" % self.map_name)

    def height(self, x, z):
        chunk_x = int(math.floor(float(x) / CHUNK_SIZE))
        chunk_z = int(math.floor(float(z) / CHUNK_SIZE))
        chunk = self.chunks.get((chunk_x, chunk_z))
        if chunk is None:
            return None
        local_x = float(x) - chunk_x * CHUNK_SIZE
        local_z = float(z) - chunk_z * CHUNK_SIZE
        return chunk.sample(local_x, local_z)

    def water_depth(self, x, z, height=None):
        if height is None:
            height = self.height(x, z)
        if height is None:
            return None
        depth = 0.0
        for water in self.waters:
            if water.contains(x, z):
                depth = max(depth, water.y - float(height))
        return max(0.0, depth)


def _primitive_sections(data):
    if data[:4] != b"\x65\x4e\xa1\x42":
        raise ValueError("invalid primitives magic")
    if len(data) < 8:
        raise ValueError("truncated primitives file")
    table_length = struct.unpack_from("<I", data, len(data) - 4)[0]
    position = len(data) - 4 - table_length
    section_offset = 4
    remaining = table_length
    sections = {}
    while remaining:
        if remaining < 24:
            raise ValueError("truncated primitives section table")
        section_length = struct.unpack_from("<I", data, position)[0]
        name_length = struct.unpack_from("<I", data, position + 20)[0]
        name_start = position + 24
        name = data[name_start:name_start + name_length].decode("utf-8")
        name_padding = (-name_length) % 4
        record_length = 24 + name_length + name_padding
        sections[name] = data[section_offset:section_offset + section_length]
        position += record_length
        remaining -= record_length
        section_offset += section_length + (-section_length) % 4
    return sections


def _cstring(data, start, length):
    raw = data[start:start + length]
    return raw.split(b"\0", 1)[0].decode("ascii")


def _vertex_positions(section):
    vertex_type = _cstring(section, 0, 64)
    count = struct.unpack_from("<I", section, 64)[0]
    position = 68
    if vertex_type.startswith("BPVT"):
        vertex_type = _cstring(section, position, 64)
        count = struct.unpack_from("<I", section, position + 64)[0]
        position += 68
    strides = {
        "xyznuv": 24,
        "xyznuvtb": 32,
    }
    stride = strides.get(vertex_type)
    if stride is None:
        raise ValueError("unsupported static vertex type %s" % vertex_type)
    vertices = []
    for unused_index in range(count):
        if position + stride > len(section):
            raise ValueError("truncated vertex section")
        vertices.append(struct.unpack_from("<fff", section, position))
        position += stride
    return vertices


def _index_groups(section):
    index_type = _cstring(section, 0, 64)
    index_width = 4 if index_type == "list32" else 2 if index_type == "list" else None
    if index_width is None:
        raise ValueError("unsupported index type %s" % index_type)
    index_count, group_count = struct.unpack_from("<II", section, 64)
    if index_count % 3:
        raise ValueError("triangle index count is not divisible by three")
    position = 72
    primitives = []
    index_format = "<I" if index_width == 4 else "<H"
    for unused_index in range(index_count // 3):
        triangle = []
        for unused_vertex in range(3):
            triangle.append(struct.unpack_from(index_format, section, position)[0])
            position += index_width
        primitives.append(tuple(triangle))
    groups = []
    for unused_index in range(group_count):
        groups.append(struct.unpack_from("<IIII", section, position))
        position += 16
    return primitives, groups


def _bsp_triangles(section, material_names, material_flags):
    if len(section) < 16:
        raise ValueError("truncated BSP2 header")
    magic, triangle_count, unused_max_triangles, unused_node_count = struct.unpack_from(
        "<IIII", section, 0
    )
    if magic & 0x00FFFFFF != 0x00505342:
        raise ValueError("invalid BSP2 magic")
    required = 16 + triangle_count * 40
    if len(section) < required:
        raise ValueError("truncated BSP2 triangle array")
    triangles = []
    for index in range(triangle_count):
        offset = 16 + index * 40
        values = struct.unpack_from("<9f", section, offset)
        material_index = struct.unpack_from("<I", section, offset + 36)[0]
        material_name = (material_names[material_index]
                         if material_index < len(material_names) else "")
        collision_flags = int(material_flags.get(material_name, 0))
        if collision_flags & 16:  # TRIANGLE_NOCOLLIDE
            continue
        triangles.append((values[0:3], values[3:6], values[6:9]))
    return triangles


def _bsp_material_names(section):
    root = ElementTree.fromstring(section.decode("utf-8"))
    return [element.text.strip() if element.text else ""
            for element in root.findall("id")]


def _convex_hull(points):
    unique = sorted(set((float(point[0]), float(point[1])) for point in points))
    if len(unique) <= 1:
        return tuple(unique)

    def cross(origin, first, second):
        return ((first[0] - origin[0]) * (second[1] - origin[1]) -
                (first[1] - origin[1]) * (second[0] - origin[0]))

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


class ModelShape(object):
    def __init__(self, hull, triangles, minimum, maximum, source):
        self.hull = tuple(hull)
        self.triangles = tuple(triangles)
        self.minimum = tuple(minimum)
        self.maximum = tuple(maximum)
        self.source = source


def _is_local_obstacle(shape):
    return (float(shape.maximum[1]) - float(shape.minimum[1]) <=
            LOCAL_OBSTACLE_MAX_HEIGHT)


def _is_bridge_model(model_name):
    """Return whether a static model supplies a drivable bridge deck.

    Collision geometry alone cannot distinguish a road deck from a building
    roof. The stock assets do carry that semantic distinction in their model
    resource names, so keep surface extraction deliberately limited to bridge
    models instead of making arbitrary horizontal scenery traversable.
    """
    return "bridge" in str(model_name).lower()


class ModelLibrary(object):
    def __init__(self, resources):
        self.resources = resources
        self.cache = {}

    def load(self, model_name):
        if model_name in self.cache:
            return self.cache[model_name]
        model_root = read_packed_xml(self.resources.read(model_name))
        model_children = dict(model_root.children)
        visual_value = (model_children.get(b"nodelessVisual") or
                        model_children.get(b"nodefullVisual"))
        if visual_value is None:
            raise ValueError("model has no visual: %s" % model_name)
        visual_base = _packed_text(visual_value)
        visual_root = read_packed_xml(self.resources.read(visual_base + ".visual"))
        bounding = _packed_child(visual_root, "boundingBox").value
        minimum = _packed_vector(_packed_child(bounding, "min"))
        maximum = _packed_vector(_packed_child(bounding, "max"))
        points = []
        render_triangles = []
        material_flags = {}
        primitives_name = visual_base + ".primitives"
        if self.resources.contains(primitives_name):
            sections = _primitive_sections(self.resources.read(primitives_name))
            for render_value in _packed_children(visual_root, "renderSet"):
                if render_value.value_type != TYPE_ELEMENT:
                    continue
                geometry_value = _packed_child(render_value.value, "geometry", False)
                if geometry_value is None or geometry_value.value_type != TYPE_ELEMENT:
                    continue
                for group_value in _packed_children(geometry_value.value, "primitiveGroup"):
                    if group_value.value_type != TYPE_ELEMENT:
                        continue
                    material_value = _packed_child(group_value.value, "material", False)
                    if material_value is None or material_value.value_type != TYPE_ELEMENT:
                        continue
                    identifier_value = _packed_child(material_value.value, "identifier", False)
                    if identifier_value is None:
                        continue
                    identifier = _packed_text(identifier_value)
                    flags_value = _packed_child(material_value.value, "collisionFlags", False)
                    material_flags[identifier] = _packed_integer(flags_value, 0)
                vertices_value = _packed_child(geometry_value.value, "vertices", False)
                indices_value = _packed_child(geometry_value.value, "primitive", False)
                if vertices_value is None or indices_value is None:
                    continue
                vertices_name = _packed_text(vertices_value)
                indices_name = _packed_text(indices_value)
                if vertices_name not in sections or indices_name not in sections:
                    continue
                vertices = _vertex_positions(sections[vertices_name])
                primitives, groups = _index_groups(sections[indices_name])
                points.extend((vertex[0], vertex[2]) for vertex in vertices)
                group_values = _packed_children(geometry_value.value, "primitiveGroup")
                group_indices = []
                for group_value in group_values:
                    if group_value.value_type == TYPE_ELEMENT:
                        group_indices.append(_packed_integer(group_value.value.value, 0))
                if not group_indices:
                    group_indices = list(range(len(groups)))
                for group_index in group_indices:
                    if group_index < 0 or group_index >= len(groups):
                        continue
                    primitive_offset, primitive_count, vertex_offset, vertex_count = groups[group_index]
                    group_vertices = vertices[vertex_offset:vertex_offset + vertex_count]
                    for primitive in primitives[primitive_offset:primitive_offset + primitive_count]:
                        if max(primitive) >= len(group_vertices):
                            continue
                        render_triangles.append(tuple(group_vertices[index] for index in primitive))
            if "bsp2" in sections and "bsp2_materials" in sections:
                triangles = _bsp_triangles(
                    sections["bsp2"],
                    _bsp_material_names(sections["bsp2_materials"]),
                    material_flags,
                )
            else:
                triangles = render_triangles
        else:
            triangles = []
        if not points:
            points = (
                (minimum[0], minimum[2]), (minimum[0], maximum[2]),
                (maximum[0], maximum[2]), (maximum[0], minimum[2]),
            )
        shape = ModelShape(_convex_hull(points), triangles, minimum, maximum, model_name)
        self.cache[model_name] = shape
        return shape


def _transform_point(transform, point, chunk_x, chunk_z):
    x, y, z = point
    return (
        transform[0] * x + transform[3] * y + transform[6] * z + transform[9] + chunk_x * CHUNK_SIZE,
        transform[1] * x + transform[4] * y + transform[7] * z + transform[10],
        transform[2] * x + transform[5] * y + transform[8] * z + transform[11] + chunk_z * CHUNK_SIZE,
    )


def _distance_to_segment(point, first, second):
    dx = second[0] - first[0]
    dz = second[1] - first[1]
    length_squared = dx * dx + dz * dz
    if length_squared <= 1e-10:
        return math.hypot(point[0] - first[0], point[1] - first[1])
    fraction = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dz) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    x = first[0] + dx * fraction
    z = first[1] + dz * fraction
    return math.hypot(point[0] - x, point[1] - z)


def _point_in_convex_polygon(point, polygon):
    if len(polygon) < 3:
        return False
    sign = None
    previous = polygon[-1]
    for current in polygon:
        cross = ((current[0] - previous[0]) * (point[1] - previous[1]) -
                 (current[1] - previous[1]) * (point[0] - previous[0]))
        if abs(cross) > 1e-8:
            current_sign = cross > 0.0
            if sign is None:
                sign = current_sign
            elif sign != current_sign:
                return False
        previous = current
    # A vertical collision triangle projects to a line in X/Z. In that
    # degenerate case every cross product is zero; treating the whole bounding
    # rectangle as "inside" turns a diagonal wall into a large solid block.
    return sign is not None


def _distance_to_polygon(point, polygon):
    if _point_in_convex_polygon(point, polygon):
        return 0.0
    if not polygon:
        return float("inf")
    return min(_distance_to_segment(point, polygon[index - 1], polygon[index])
               for index in range(len(polygon)))


class ObstacleField(object):
    def __init__(self, resources, map_name, raster_size=1.0, soft_models=None):
        self.resources = resources
        self.map_name = map_name
        self.raster_size = float(raster_size)
        self.soft_models = set(soft_models or ())
        self.cells = {}
        self.surface_cells = {}
        self.instance_count = 0
        self.bridge_instance_count = 0
        self.bridge_surface_triangle_count = 0
        self.soft_instance_count = 0
        self.local_instance_count = 0
        self.model_library = ModelLibrary(resources)
        self.skipped = 0
        self._load()

    def _mark_cell(self, cell_x, cell_z, minimum_y, maximum_y):
        key = (int(cell_x), int(cell_z))
        previous = self.cells.get(key)
        if previous is None:
            self.cells[key] = [float(minimum_y), float(maximum_y)]
        else:
            previous[0] = min(previous[0], float(minimum_y))
            previous[1] = max(previous[1], float(maximum_y))

    def _mark_surface(self, cell_x, cell_z, height):
        key = (int(cell_x), int(cell_z))
        previous = self.surface_cells.get(key)
        if previous is None or float(height) > previous:
            self.surface_cells[key] = float(height)

    @staticmethod
    def _walkable_triangle(triangle):
        first, second, third = triangle
        ax = second[0] - first[0]
        ay = second[1] - first[1]
        az = second[2] - first[2]
        bx = third[0] - first[0]
        by = third[1] - first[1]
        bz = third[2] - first[2]
        normal_x = ay * bz - az * by
        normal_y = az * bx - ax * bz
        normal_z = ax * by - ay * bx
        projected_area = abs(normal_y) * 0.5
        if projected_area <= 1e-6:
            return False, 0.0
        slope = math.hypot(normal_x, normal_z) / abs(normal_y)
        return slope <= MAX_GRADE, projected_area

    @staticmethod
    def _triangle_height(triangle, x, z):
        first, second, third = triangle
        denominator = ((second[2] - third[2]) * (first[0] - third[0]) +
                       (third[0] - second[0]) * (first[2] - third[2]))
        if abs(denominator) <= 1e-10:
            return None
        first_weight = (((second[2] - third[2]) * (x - third[0]) +
                         (third[0] - second[0]) * (z - third[2])) /
                        denominator)
        second_weight = (((third[2] - first[2]) * (x - third[0]) +
                          (first[0] - third[0]) * (z - third[2])) /
                         denominator)
        third_weight = 1.0 - first_weight - second_weight
        epsilon = 1e-7
        if (first_weight < -epsilon or second_weight < -epsilon or
                third_weight < -epsilon):
            return None
        return (first_weight * first[1] + second_weight * second[1] +
                third_weight * third[1])

    def _raster_surface_triangle(self, triangle):
        minimum_x = min(point[0] for point in triangle)
        maximum_x = max(point[0] for point in triangle)
        minimum_z = min(point[2] for point in triangle)
        maximum_z = max(point[2] for point in triangle)
        min_cell_x = int(math.floor(minimum_x / self.raster_size))
        max_cell_x = int(math.floor(maximum_x / self.raster_size))
        min_cell_z = int(math.floor(minimum_z / self.raster_size))
        max_cell_z = int(math.floor(maximum_z / self.raster_size))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(min_cell_z, max_cell_z + 1):
                z = (cell_z + 0.5) * self.raster_size
                height = self._triangle_height(triangle, x, z)
                if height is not None:
                    self._mark_surface(cell_x, cell_z, height)

    def _bridge_deck_triangles(self, triangles):
        # A bridge model contains both the level deck and its drivable approach
        # ramps. Selecting only triangles near the dominant deck height leaves
        # a short gap at each bank, so the resulting graph contains an isolated
        # deck that tanks can never enter. The resource name already provides
        # the bridge semantic boundary; within it every tank-grade surface is
        # road, and _mark_surface keeps the highest surface where they overlap.
        candidates = set()
        for triangle in triangles:
            walkable, unused_projected_area = self._walkable_triangle(triangle)
            if not walkable:
                continue
            candidates.add(id(triangle))
        return candidates

    def _raster_triangle(self, triangle):
        polygon = tuple((point[0], point[2]) for point in triangle)
        minimum_y = min(point[1] for point in triangle)
        maximum_y = max(point[1] for point in triangle)
        radius = self.raster_size * math.sqrt(2.0) * 0.5
        minimum_x = min(point[0] for point in polygon) - radius
        maximum_x = max(point[0] for point in polygon) + radius
        minimum_z = min(point[1] for point in polygon) - radius
        maximum_z = max(point[1] for point in polygon) + radius
        min_cell_x = int(math.floor(minimum_x / self.raster_size))
        max_cell_x = int(math.floor(maximum_x / self.raster_size))
        min_cell_z = int(math.floor(minimum_z / self.raster_size))
        max_cell_z = int(math.floor(maximum_z / self.raster_size))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(min_cell_z, max_cell_z + 1):
                z = (cell_z + 0.5) * self.raster_size
                if _distance_to_polygon((x, z), polygon) <= radius:
                    self._mark_cell(cell_x, cell_z, minimum_y, maximum_y)

    def _raster_shape_fallback(self, shape, transform, chunk_x, chunk_z):
        polygon = []
        for x, z in shape.hull:
            world = _transform_point(transform, (x, 0.0, z), chunk_x, chunk_z)
            polygon.append((world[0], world[2]))
        polygon = _convex_hull(polygon)
        if len(polygon) < 2:
            return False
        corners = []
        for x in (shape.minimum[0], shape.maximum[0]):
            for y in (shape.minimum[1], shape.maximum[1]):
                for z in (shape.minimum[2], shape.maximum[2]):
                    corners.append(_transform_point(transform, (x, y, z), chunk_x, chunk_z))
        minimum_y = min(point[1] for point in corners)
        maximum_y = max(point[1] for point in corners)
        if maximum_y - minimum_y < 0.45:
            return True
        radius = self.raster_size * math.sqrt(2.0) * 0.5
        min_cell_x = int(math.floor((min(point[0] for point in polygon) - radius) /
                                    self.raster_size))
        max_cell_x = int(math.floor((max(point[0] for point in polygon) + radius) /
                                    self.raster_size))
        min_cell_z = int(math.floor((min(point[1] for point in polygon) - radius) /
                                    self.raster_size))
        max_cell_z = int(math.floor((max(point[1] for point in polygon) + radius) /
                                    self.raster_size))
        for cell_x in range(min_cell_x, max_cell_x + 1):
            x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(min_cell_z, max_cell_z + 1):
                z = (cell_z + 0.5) * self.raster_size
                if _distance_to_polygon((x, z), polygon) <= radius:
                    self._mark_cell(cell_x, cell_z, minimum_y, maximum_y)
        return True

    def _load(self):
        prefix = "spaces/%s/" % self.map_name
        for chunk_name in self.resources.iter_names(suffix=".chunk", prefix=prefix):
            chunk_x, chunk_z = chunk_coordinates(chunk_name)
            root = read_packed_xml(self.resources.read(chunk_name))
            for model_value in _packed_children(root, "model"):
                if model_value.value_type != TYPE_ELEMENT:
                    continue
                model = model_value.value
                resource_value = _packed_child(model, "resource", False)
                transform_value = _packed_child(model, "transform", False)
                if resource_value is None or transform_value is None:
                    self.skipped += 1
                    continue
                model_name = _packed_text(resource_value)
                transform = _packed_vector(transform_value)
                if len(transform) != 12:
                    self.skipped += 1
                    continue
                if model_name in self.soft_models:
                    self.soft_instance_count += 1
                    continue
                try:
                    shape = self.model_library.load(model_name)
                except (KeyError, ValueError, struct.error, zipfile.BadZipFile):
                    self.skipped += 1
                    continue
                # Curbs, low borders and similar props belong to locomotion,
                # not the strategic graph. A tank can cross them and their
                # tilted world AABB otherwise erases an entire four-metre road
                # cell. Explicit destructibles were already skipped above.
                if _is_local_obstacle(shape):
                    self.local_instance_count += 1
                    continue
                self.instance_count += 1
                if shape.triangles:
                    transformed_triangles = [
                        tuple(_transform_point(transform, point, chunk_x, chunk_z)
                              for point in triangle)
                        for triangle in shape.triangles
                    ]
                    bridge_deck = set()
                    if _is_bridge_model(model_name):
                        self.bridge_instance_count += 1
                        bridge_deck = self._bridge_deck_triangles(
                            transformed_triangles)
                    for triangle in transformed_triangles:
                        if id(triangle) in bridge_deck:
                            self._raster_surface_triangle(triangle)
                            self.bridge_surface_triangle_count += 1
                        else:
                            self._raster_triangle(triangle)
                elif not self._raster_shape_fallback(shape, transform, chunk_x, chunk_z):
                    self.skipped += 1

    def blocked(self, x, z, ground_y, margin=VEHICLE_HALF_WIDTH):
        centre_x = int(math.floor(float(x) / self.raster_size))
        centre_z = int(math.floor(float(z) / self.raster_size))
        cell_radius = int(math.ceil(float(margin) / self.raster_size))
        # Roads, paving slabs, rubble lips, and curbs are often part of a much
        # taller visual model in the old client, so model-level height filtering
        # cannot identify them. Ignore collision triangles below track-clearance
        # height at query time; walls and building volumes still intersect the
        # remaining 0.65..2.40 m vehicle body interval.
        vehicle_minimum_y = float(ground_y) + VEHICLE_GROUND_CLEARANCE
        vehicle_maximum_y = float(ground_y) + VEHICLE_CLEARANCE_HEIGHT
        for cell_x in range(centre_x - cell_radius, centre_x + cell_radius + 1):
            sample_x = (cell_x + 0.5) * self.raster_size
            for cell_z in range(centre_z - cell_radius, centre_z + cell_radius + 1):
                interval = self.cells.get((cell_x, cell_z))
                if interval is None:
                    continue
                sample_z = (cell_z + 0.5) * self.raster_size
                if math.hypot(sample_x - float(x), sample_z - float(z)) > (
                        float(margin) + self.raster_size * math.sqrt(2.0) * 0.5):
                    continue
                if interval[1] < vehicle_minimum_y or interval[0] > vehicle_maximum_y:
                    continue
                return True
        return False

    def surface_height(self, x, z):
        cell_x = int(math.floor(float(x) / self.raster_size))
        cell_z = int(math.floor(float(z) / self.raster_size))
        return self.surface_cells.get((cell_x, cell_z))


def _nearest_node(graph, point, max_distance=56.0):
    best = None
    best_distance = float(max_distance)
    width = graph["width"]
    origin_x, origin_z = graph["origin"]
    cell_size = graph["cell_size"]
    for index, height in enumerate(graph["heights_mm"]):
        if height is None:
            continue
        x = origin_x + (index % width) * cell_size
        z = origin_z + (index // width) * cell_size
        distance = math.hypot(x - point[0], z - point[1])
        if distance < best_distance:
            best_distance = distance
            best = index
    return best, best_distance


def _graph_path(graph, start, goal):
    width = graph["width"]
    height = graph["height"]
    cell_size = graph["cell_size"]
    links = graph["links"]
    queue = [(0.0, start)]
    costs = {start: 0.0}
    previous = {}
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != costs.get(current):
            continue
        if current == goal:
            break
        x = current % width
        z = current // width
        mask = links[current]
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (mask & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= height:
                continue
            neighbour = nz * width + nx
            new_cost = cost + cell_size * (math.sqrt(2.0) if dx and dz else 1.0)
            if new_cost < costs.get(neighbour, float("inf")):
                costs[neighbour] = new_cost
                previous[neighbour] = current
                heapq.heappush(queue, (new_cost, neighbour))
    if goal not in costs:
        return (), float("inf")
    path = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return tuple(path), costs[goal]


def _reachable_nodes(graph, root):
    reachable = set((root,))
    stack = [root]
    width = graph["width"]
    height = graph["height"]
    while stack:
        current = stack.pop()
        x = current % width
        z = current // width
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (graph["links"][current] & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= height:
                continue
            neighbour = nz * width + nx
            if (graph["heights_mm"][neighbour] is not None and
                    neighbour not in reachable):
                reachable.add(neighbour)
                stack.append(neighbour)
    return reachable


def _connected_node_components(graph):
    remaining = set(index for index, value in enumerate(graph["heights_mm"])
                    if value is not None)
    components = []
    width = graph["width"]
    height = graph["height"]
    while remaining:
        root = remaining.pop()
        stack = [root]
        component = set((root,))
        while stack:
            current = stack.pop()
            x = current % width
            z = current // width
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                if not (graph["links"][current] & (1 << direction_index)):
                    continue
                nx = x + dx
                nz = z + dz
                if nx < 0 or nx >= width or nz < 0 or nz >= height:
                    continue
                neighbour = nz * width + nx
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    components.sort(key=len, reverse=True)
    return components


def _connected_components(graph):
    return [len(component) for component in _connected_node_components(graph)]


def _nearest_node_in_component(graph, point, component):
    best = None
    best_distance = float("inf")
    width = graph["width"]
    origin_x, origin_z = graph["origin"]
    cell_size = graph["cell_size"]
    for index in component:
        x = origin_x + (index % width) * cell_size
        z = origin_z + (index // width) * cell_size
        distance = math.hypot(x - point[0], z - point[1])
        if distance < best_distance:
            best = index
            best_distance = distance
    return best, best_distance


def retain_base_component(graph, map_config, minimum_fraction=0.60):
    """Remove isolated ledges and pockets that cannot reach both team bases."""
    node_components = _connected_node_components(graph)
    if not node_components:
        raise ValueError("baked graph has no navigable nodes")
    source_components = [len(component) for component in node_components]
    source_nodes = sum(source_components)
    candidates = []
    for component in node_components:
        start, start_offset = _nearest_node_in_component(
            graph, map_config["bases"][0], component)
        goal, goal_offset = _nearest_node_in_component(
            graph, map_config["bases"][1], component)
        if start_offset <= 56.0 and goal_offset <= 56.0:
            candidates.append((-len(component), start_offset + goal_offset,
                               min(component), component, start, goal))
    if not candidates:
        raise ValueError("a team base has no nearby navigable node")
    # A tiny ledge can be a few metres closer to a boundary base than the road
    # network. Select one component that can serve both bases, then apply the
    # existing retained-fraction guard so an implausibly small shortcut cannot win.
    unused_size, unused_score, unused_root, retained, start, goal = min(candidates)
    retained_fraction = float(len(retained)) / float(source_nodes)
    if retained_fraction < float(minimum_fraction):
        raise ValueError("base graph component is unexpectedly small: %.1f%%" %
                         (retained_fraction * 100.0))
    width = graph["width"]
    height = graph["height"]
    for index in range(len(graph["heights_mm"])):
        if index not in retained:
            graph["heights_mm"][index] = None
            graph["links"][index] = 0
            continue
        x = index % width
        z = index // width
        mask = graph["links"][index]
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (mask & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if (nx < 0 or nx >= width or nz < 0 or nz >= height or
                    nz * width + nx not in retained):
                mask &= ~(1 << direction_index)
        graph["links"][index] = mask
    graph["bake"].update({
        "source_components": len(source_components),
        "source_navigable_nodes": source_nodes,
        "retained_nodes": len(retained),
        "retained_fraction": round(retained_fraction, 5),
        "pruned_nodes": source_nodes - len(retained),
    })
    return retained


def _node_point(graph, index):
    width = graph["width"]
    return (
        graph["origin"][0] + (index % width) * graph["cell_size"],
        graph["origin"][1] + (index // width) * graph["cell_size"],
    )


def _oriented_route_points(route_record, map_config):
    """Orient a tactical corridor and include its two objective endpoints."""
    team = int(route_record["team"])
    own = map_config["bases"][team - 1]
    enemy = map_config["bases"][2 - team]
    points = list(route_record["points"])
    if len(points) < 2:
        return points

    def distance_squared(point, base):
        return ((float(point[0]) - float(base[0])) ** 2 +
                (float(point[1]) - float(base[1])) ** 2)

    forward = distance_squared(points[0], own) + distance_squared(points[-1], enemy)
    reverse = distance_squared(points[-1], own) + distance_squared(points[0], enemy)
    if reverse < forward:
        points.reverse()
    if distance_squared(points[0], own) > 1.0:
        points.insert(0, (float(own[0]), float(own[1]), False))
    else:
        points[0] = (float(own[0]), float(own[1]), bool(points[0][2]))
    if distance_squared(points[-1], enemy) > 1.0:
        points.append((float(enemy[0]), float(enemy[1]), False))
    else:
        points[-1] = (float(enemy[0]), float(enemy[1]), bool(points[-1][2]))
    return points


def _sample_route_path(graph, path, hold_nodes, maximum_points=16,
                       required_nodes=()):
    """Reduce a safe grid path while retaining endpoints and tactical holds."""
    if not path:
        return []
    maximum_points = max(2, int(maximum_points))
    cumulative = [0.0]
    for first, second in zip(path, path[1:]):
        first_point = _node_point(graph, first)
        second_point = _node_point(graph, second)
        cumulative.append(cumulative[-1] + math.hypot(
            second_point[0] - first_point[0],
            second_point[1] - first_point[1],
        ))
    selected = set((0, len(path) - 1))
    selected.update(index for index, node in enumerate(path) if node in hold_nodes)
    selected.update(index for index, node in enumerate(path)
                    if node in required_nodes)
    if len(selected) > maximum_points:
        raise ValueError("tactical route has more required gates than protocol slots")
    # Preserve bends before filling long straight gaps. Uniform arc-length
    # sampling can put two waypoints on opposite sides of a tight obstacle and
    # make their direct separation tiny even though the graph leg loops around
    # it. Greedily split the highest-detour gap at its geometric apex first.
    while len(selected) < min(maximum_points, len(path)):
        ordered = sorted(selected)
        gaps = []
        for first, second in zip(ordered, ordered[1:]):
            if second - first <= 1:
                continue
            first_point = _node_point(graph, path[first])
            second_point = _node_point(graph, path[second])
            path_distance = cumulative[second] - cumulative[first]
            direct = max(
                graph["cell_size"],
                math.hypot(second_point[0] - first_point[0],
                           second_point[1] - first_point[1]),
            )
            ratio = path_distance / direct
            # Curved gaps above 1.25x outrank all straight gaps. Once the
            # polyline represents every bend, distribute remaining samples by
            # travelled distance.
            priority = ratio if ratio > 1.25 else 1.0
            gaps.append((priority, path_distance, first, second))
        if not gaps:
            break
        unused_priority, unused_distance, first, second = max(gaps)
        first_point = _node_point(graph, path[first])
        second_point = _node_point(graph, path[second])
        candidates = range(first + 1, second)
        index = max(
            candidates,
            key=lambda value: _distance_to_segment(
                _node_point(graph, path[value]), first_point, second_point),
        )
        if _distance_to_segment(
                _node_point(graph, path[index]), first_point,
                second_point) < graph["cell_size"] * 0.25:
            target = (cumulative[first] + cumulative[second]) * 0.5
            index = min(candidates,
                        key=lambda value: abs(cumulative[value] - target))
        selected.add(index)
    result = []
    for index in sorted(selected):
        x, z = _node_point(graph, path[index])
        result.append([round(x, 3), round(z, 3), path[index] in hold_nodes])
    return result


def _polyline_distance(point, polyline):
    if not polyline:
        return float("inf")
    if len(polyline) == 1:
        return math.hypot(point[0] - polyline[0][0],
                          point[1] - polyline[0][1])
    return min(_distance_to_segment(point, first, second)
               for first, second in zip(polyline, polyline[1:]))


def _corridor_height_at(graph, index, corridor):
    if not corridor or len(corridor[0]) < 3:
        return None
    point = _node_point(graph, index)
    if len(corridor) == 1:
        return float(corridor[0][2])
    best = None
    for first, second in zip(corridor, corridor[1:]):
        dx = float(second[0]) - float(first[0])
        dz = float(second[1]) - float(first[1])
        length_squared = dx * dx + dz * dz
        if length_squared <= 1e-10:
            fraction = 0.0
        else:
            fraction = (((point[0] - float(first[0])) * dx +
                         (point[1] - float(first[1])) * dz) /
                        length_squared)
            fraction = max(0.0, min(1.0, fraction))
        projected_x = float(first[0]) + dx * fraction
        projected_z = float(first[1]) + dz * fraction
        distance = math.hypot(point[0] - projected_x,
                              point[1] - projected_z)
        height = (float(first[2]) +
                  (float(second[2]) - float(first[2])) * fraction)
        candidate = (distance, height)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best[1] if best is not None else None


def _corridor_graph_path(graph, start, goal, corridor):
    """Find a simple base-to-base path biased toward a tactical polyline."""
    width = graph["width"]
    height = graph["height"]
    cell_size = graph["cell_size"]
    goal_point = _node_point(graph, goal)
    queue = [(0.0, 0.0, start)]
    costs = {start: 0.0}
    previous = {}
    while queue:
        unused_priority, cost, current = heapq.heappop(queue)
        if cost != costs.get(current):
            continue
        if current == goal:
            break
        x = current % width
        z = current // width
        mask = graph["links"][current]
        for direction_index, (dx, dz) in enumerate(DIRECTIONS):
            if not (mask & (1 << direction_index)):
                continue
            nx = x + dx
            nz = z + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= height:
                continue
            neighbour = nz * width + nx
            point = _node_point(graph, neighbour)
            corridor_offset = min(180.0, _polyline_distance(point, corridor))
            distance = cell_size * (math.sqrt(2.0) if dx and dz else 1.0)
            desired_height = _corridor_height_at(graph, neighbour, corridor)
            height_cost = 0.0
            if desired_height is not None:
                actual_height = float(graph["heights_mm"][neighbour]) / 1000.0
                height_cost = distance * min(
                    6.0, abs(actual_height - desired_height) / 8.0)
            hazards = graph.get("hazards")
            is_shallow_water = (hazards is not None and
                                neighbour < len(hazards) and
                                int(hazards[neighbour]) &
                                HAZARD_SHALLOW_WATER)
            shallow_water_cost = (distance * SHALLOW_WATER_COST_MULTIPLIER
                                  if is_shallow_water else 0.0)
            new_cost = (cost + distance * (1.0 + corridor_offset / 45.0) +
                        shallow_water_cost + height_cost)
            if new_cost >= costs.get(neighbour, float("inf")):
                continue
            costs[neighbour] = new_cost
            previous[neighbour] = current
            heuristic = math.hypot(point[0] - goal_point[0],
                                   point[1] - goal_point[1])
            heapq.heappush(queue, (new_cost + heuristic, new_cost, neighbour))
    if goal not in costs:
        return ()
    path = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return tuple(path)


def _route_maximum_detour(graph, waypoints):
    maximum = 1.0
    for first, second in zip(waypoints, waypoints[1:]):
        first_node, first_offset = _nearest_node(graph, first)
        second_node, second_offset = _nearest_node(graph, second)
        if first_node is None or second_node is None:
            return float("inf")
        path, distance = _graph_path(graph, first_node, second_node)
        if not path:
            return float("inf")
        direct = max(
            graph["cell_size"],
            math.hypot(second[0] - first[0], second[1] - first[1]),
        )
        maximum = max(
            maximum, (distance + first_offset + second_offset) / direct
        )
    return maximum


def _route_geometry_issue(waypoints):
    """Return why a sampled route loops back on itself, or ``None``."""
    points = [(float(point[0]), float(point[1])) for point in waypoints]
    for first, second, third in zip(points, points[1:], points[2:]):
        first_heading = math.atan2(second[1] - first[1],
                                   second[0] - first[0])
        second_heading = math.atan2(third[1] - second[1],
                                    third[0] - second[0])
        turn = abs(second_heading - first_heading)
        if turn > math.pi:
            turn = math.pi * 2.0 - turn
        if turn >= MAX_ROUTE_TURN:
            return "hairpin %.1f degrees" % math.degrees(turn)

    def orientation(first, second, third):
        return ((second[0] - first[0]) * (third[1] - first[1]) -
                (second[1] - first[1]) * (third[0] - first[0]))

    def on_segment(first, second, point):
        epsilon = 1e-7
        return (abs(orientation(first, second, point)) <= epsilon and
                min(first[0], second[0]) - epsilon <= point[0] <=
                max(first[0], second[0]) + epsilon and
                min(first[1], second[1]) - epsilon <= point[1] <=
                max(first[1], second[1]) + epsilon)

    def intersects(first, second, third, fourth):
        first_side = orientation(first, second, third)
        second_side = orientation(first, second, fourth)
        third_side = orientation(third, fourth, first)
        fourth_side = orientation(third, fourth, second)
        if (((first_side > 0.0 and second_side < 0.0) or
             (first_side < 0.0 and second_side > 0.0)) and
                ((third_side > 0.0 and fourth_side < 0.0) or
                 (third_side < 0.0 and fourth_side > 0.0))):
            return True
        return (on_segment(first, second, third) or
                on_segment(first, second, fourth) or
                on_segment(third, fourth, first) or
                on_segment(third, fourth, second))

    segments = list(zip(points, points[1:]))
    for first_index, first in enumerate(segments):
        for second_index in range(first_index + 2, len(segments)):
            if intersects(first[0], first[1],
                          segments[second_index][0],
                          segments[second_index][1]):
                return "self-intersection at segments %d/%d" % (
                    first_index, second_index)
    return None


def bake_tactical_routes(graph, map_config, maximum_projection=180.0):
    """Project tactical intent onto the retained graph and emit safe routes.

    Hand-authored points select a lane, but they are not trusted locomotion
    coordinates. Every point is projected to the one component that connects
    both bases, every segment is recomputed over validated graph links, and the
    result is sampled to the protocol's sixteen-waypoint limit.
    """
    routes = {"1": [], "2": []}
    maximum_offset = 0.0
    soft_fallbacks = []
    soft_fallback_causes = {}
    for route_record in map_config.get("routes", ()):
        source_points = _oriented_route_points(route_record, map_config)
        projected = []
        for point in source_points:
            node, offset = _nearest_node(
                graph, point, max_distance=float(maximum_projection) + 0.001)
            if node is None:
                raise ValueError("route point has no retained navigation node: %r" %
                                 (point[:2],))
            maximum_offset = max(maximum_offset, offset)
            if offset > float(maximum_projection):
                raise ValueError("route point projection is implausible: %.1f m" % offset)
            projected.append(node)
        if not projected:
            continue
        if len(source_points) == 1:
            full_path = (projected[0],)
            hold_nodes = set(projected if bool(source_points[0][2]) else ())
            waypoints = _sample_route_path(graph, full_path, hold_nodes, 2)
        else:
            corridor = []
            for point, node in zip(source_points, projected):
                corridor.append((point[0], point[1],
                                 float(graph["heights_mm"][node]) / 1000.0))
            # One representative hold point is the tactical lane gate;
            # ordinary points and remaining holds shape the corridor around it.
            # Making every sketch point mandatory turns a harmless point behind
            # one building into a U-turn, while making all points soft lets a
            # hill route collapse onto the direct city road. The hold farthest
            # from the base line best preserves the route's distinguishing lane.
            gate_indexes = [0]
            hold_indexes = [index for index, point in enumerate(source_points)
                            if bool(point[2]) and index not in
                            (0, len(source_points) - 1)]
            if hold_indexes:
                base_line = (source_points[0], source_points[-1])
                gate_indexes.append(max(
                    hold_indexes,
                    key=lambda index: _distance_to_segment(
                        source_points[index], base_line[0], base_line[1]),
                ))
            gate_indexes.append(len(source_points) - 1)
            gate_indexes = sorted(set(gate_indexes))
            full_path = []
            for gate_number, (start_index, goal_index) in enumerate(
                    zip(gate_indexes, gate_indexes[1:])):
                segment = _corridor_graph_path(
                    graph, projected[start_index], projected[goal_index],
                    tuple(corridor[start_index:goal_index + 1]))
                if not segment:
                    raise ValueError(
                        "tactical corridor cannot connect route gate %d" %
                        gate_number
                    )
                if full_path and segment[0] == full_path[-1]:
                    full_path.extend(segment[1:])
                else:
                    full_path.extend(segment)
            full_path = tuple(full_path)
            hold_nodes = set()
            for point in source_points:
                if not bool(point[2]):
                    continue
                hold_nodes.add(min(
                    full_path,
                    key=lambda node: math.hypot(
                        _node_point(graph, node)[0] - point[0],
                        _node_point(graph, node)[1] - point[1]),
                ))
            waypoints = _sample_route_path(
                graph, full_path, hold_nodes, 16,
                set(projected[index] for index in gate_indexes)
            )
            detour = _route_maximum_detour(graph, waypoints)
            geometry_issue = _route_geometry_issue(waypoints)
            if detour > MAX_ROUTE_DETOUR or geometry_issue is not None:
                # Some old hand sketches place their most lateral hold just
                # behind an impassable ridge. Preserve the entire sketch as a
                # soft corridor instead of forcing a multi-hundred-metre U-turn.
                full_path = _corridor_graph_path(
                    graph, projected[0], projected[-1], tuple(corridor))
                if not full_path:
                    raise ValueError("soft tactical corridor cannot connect the team bases")
                # Once a hard gate proved geometrically unsafe, do not re-add
                # nearby hold nodes as mandatory samples. They do not alter the
                # graph path, but can make the 16-point protocol polyline double
                # back around the same obstacle and reintroduce the hairpin.
                hold_nodes = set()
                waypoints = _sample_route_path(
                    graph, full_path, hold_nodes, 16,
                    (projected[0], projected[-1]),
                )
                route_key = "%d:%s" % (
                    int(route_record["team"]), route_record["id"])
                soft_issue = _route_geometry_issue(waypoints)
                if soft_issue is not None:
                    raise ValueError(
                        "soft tactical corridor has invalid geometry: %s" %
                        soft_issue
                    )
                soft_fallbacks.append(route_key)
                soft_fallback_causes[route_key] = (
                    geometry_issue or "detour %.2fx" % detour)
        routes[str(int(route_record["team"]))].append({
            "id": route_record["id"],
            "capacity": route_record["capacity"],
            "risk": round(route_record["risk"], 3),
            "role_weights": dict(route_record["role_weights"]),
            "waypoints": waypoints,
        })
    graph["bake"]["maximum_route_projection"] = round(maximum_offset, 3)
    graph["bake"]["soft_route_fallbacks"] = soft_fallbacks
    graph["bake"]["soft_route_fallback_causes"] = soft_fallback_causes
    return routes


def validate_graph(graph, map_config):
    components = _connected_components(graph)
    if not components:
        raise ValueError("baked graph has no navigable nodes")
    start, start_offset = _nearest_node(graph, map_config["bases"][0])
    goal, goal_offset = _nearest_node(graph, map_config["bases"][1])
    if start is None or goal is None:
        raise ValueError("a team base has no nearby navigable node")
    path, distance = _graph_path(graph, start, goal)
    if not path:
        raise ValueError("team bases are in disconnected components")
    anchor_offsets = []
    route_detours = []
    route_segments = 0
    maximum_opening_regression = 0.0
    baked_routes = graph.get("routes") or {}
    route_records = []
    for team in (1, 2):
        for route in baked_routes.get(str(team), ()):
            route_records.append((team, route.get("waypoints") or ()))
    if not route_records and map_config.get("routes"):
        # Retain compatibility with compact synthetic test fixtures.
        for route_record in map_config.get("routes", ()):
            points = route_record.get("points", ()) if isinstance(route_record, dict) else route_record
            team = int(route_record.get("team", 1)) if isinstance(route_record, dict) else 1
            route_records.append((team, points))
    if not route_records:
        for anchor in map_config.get("anchors", ()):
            unused_node, offset = _nearest_node(graph, anchor)
            if unused_node is None:
                raise ValueError("route anchor has no nearby navigable node: %r" %
                                 (anchor,))
            anchor_offsets.append(offset)
    for team, route in route_records:
        enemy_base = map_config["bases"][2 - int(team)]
        normalized_route = []
        for point in route:
            normalized_route.append((float(point[0]), float(point[1])))
            unused_node, offset = _nearest_node(graph, point)
            if unused_node is None:
                raise ValueError("route anchor has no nearby navigable node: %r" % (point,))
            anchor_offsets.append(offset)
        route = normalized_route
        if len(route) > 1:
            start_to_enemy = math.hypot(
                route[0][0] - enemy_base[0], route[0][1] - enemy_base[1])
            next_to_enemy = math.hypot(
                route[1][0] - enemy_base[0], route[1][1] - enemy_base[1])
            maximum_opening_regression = max(
                maximum_opening_regression, next_to_enemy - start_to_enemy)
        for first, second in zip(route, route[1:]):
            first_node, first_offset = _nearest_node(graph, first)
            second_node, second_offset = _nearest_node(graph, second)
            segment_path, segment_distance = _graph_path(
                graph, first_node, second_node)
            if not segment_path:
                raise ValueError("route segment is disconnected: %r -> %r" %
                                 (first, second))
            direct_distance = max(
                graph["cell_size"],
                math.hypot(second[0] - first[0], second[1] - first[1]),
            )
            detour = (segment_distance + first_offset + second_offset) / direct_distance
            route_detours.append(detour)
            route_segments += 1
    maximum_anchor_offset = max(anchor_offsets or [0.0])
    if maximum_anchor_offset > 12.0:
        raise ValueError("route anchor is too far from the retained graph: %.1f m" %
                         maximum_anchor_offset)
    maximum_route_detour = max(route_detours or [1.0])
    if maximum_route_detour > MAX_ROUTE_DETOUR:
        raise ValueError("route segment detour is implausible: %.2fx" %
                         maximum_route_detour)
    # A flank may legitimately move laterally or slightly away from the enemy
    # base to enter its lane. Keep the metric visible and reject only a gross
    # reversal; route-specific visual audits catch smaller tactical oddities.
    if maximum_opening_regression > 120.0:
        raise ValueError("route opening moves away from the objective: %.1f m" %
                         maximum_opening_regression)
    navigable = sum(components)
    largest_fraction = float(components[0]) / float(navigable)
    if largest_fraction < 0.72:
        raise ValueError("largest graph component is unexpectedly small: %.1f%%" %
                         (largest_fraction * 100.0))
    return {
        "components": len(components),
        "largest_component": components[0],
        "largest_fraction": round(largest_fraction, 5),
        "base_offsets": [round(start_offset, 3), round(goal_offset, 3)],
        "base_path_nodes": len(path),
        "base_path_metres": round(distance, 3),
        "maximum_anchor_offset": round(maximum_anchor_offset, 3),
        "route_segments": route_segments,
        "maximum_route_detour": round(maximum_route_detour, 3),
        "maximum_opening_regression": round(maximum_opening_regression, 3),
    }


def _ground_height(terrain, obstacles, x, z):
    terrain_height = terrain.height(x, z)
    surface_height = obstacles.surface_height(x, z)
    if terrain_height is None:
        return surface_height
    if surface_height is None:
        return terrain_height
    return max(float(terrain_height), float(surface_height))


def _segment_clear(terrain, obstacles, start, end):
    distance = math.hypot(end[0] - start[0], end[2] - start[2])
    steps = max(1, int(math.ceil(distance / 2.0)))
    previous = start
    for step in range(1, steps + 1):
        fraction = float(step) / float(steps)
        x = start[0] + (end[0] - start[0]) * fraction
        z = start[2] + (end[2] - start[2]) * fraction
        y = _ground_height(terrain, obstacles, x, z)
        if y is None or terrain.water_depth(x, z, y) > WATER_DEPTH_LIMIT:
            return False
        horizontal = math.hypot(x - previous[0], z - previous[2])
        if horizontal > 0.0:
            delta = y - previous[1]
            if (delta > horizontal * MAX_GRADE_UP or
                    delta < -horizontal * MAX_GRADE_DOWN):
                return False
        obstacle_margin = (BRIDGE_OBSTACLE_MARGIN
                           if obstacles.surface_height(x, z) is not None
                           else VEHICLE_HALF_WIDTH)
        if obstacles.blocked(x, z, y, margin=obstacle_margin):
            return False
        previous = (x, y, z)
    return True


def _has_safe_edge_clearance(terrain, obstacles, x, z, ground_y):
    """Reject cells whose hull shoulder can fall into water or off a steep lip.

    A route centre can be dry while collision avoidance places one track over a
    shoreline or cliff.  Sampling an eroded shoulder around every baked node
    gives the runtime driver room to deviate without relying on map-specific
    forbidden polygons.
    """
    directions = (
        (-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0),
        (-math.sqrt(0.5), -math.sqrt(0.5)),
        (math.sqrt(0.5), -math.sqrt(0.5)),
        (-math.sqrt(0.5), math.sqrt(0.5)),
        (math.sqrt(0.5), math.sqrt(0.5)),
    )
    # A bridge surface exists only where a decoded collision-mesh deck exists.
    # Use a one-metre shoulder here: the ordinary 3/6 m terrain erosion would
    # erase a narrow diagonal bridge, while accepting the centre cell alone can
    # place a route on the outer lip of the deck. At an approach ramp, ordinary
    # dry terrain may safely supply the missing shoulder.
    if obstacles.surface_height(x, z) is not None:
        maximum_drop = BRIDGE_OBSTACLE_MARGIN * MAX_GRADE
        for direction_x, direction_z in directions:
            sample_x = float(x) + direction_x * BRIDGE_OBSTACLE_MARGIN
            sample_z = float(z) + direction_z * BRIDGE_OBSTACLE_MARGIN
            sample_y = _ground_height(terrain, obstacles, sample_x, sample_z)
            if sample_y is None:
                return False
            if terrain.water_depth(sample_x, sample_z, sample_y) > WATER_DEPTH_LIMIT:
                return False
            if float(ground_y) - float(sample_y) > maximum_drop:
                return False
        return True
    radii = EDGE_CLEARANCE_RADII
    for radius in radii:
        maximum_drop = radius * MAX_GRADE
        for direction_x, direction_z in directions:
            sample_x = float(x) + direction_x * radius
            sample_z = float(z) + direction_z * radius
            sample_y = _ground_height(terrain, obstacles, sample_x, sample_z)
            if sample_y is None:
                return False
            if terrain.water_depth(sample_x, sample_z, sample_y) > WATER_DEPTH_LIMIT:
                return False
            if float(ground_y) - float(sample_y) > maximum_drop:
                return False
    return True


def bake_graph(resources, map_name, cell_size=4.0, soft_models=None):
    map_config = MAPS[map_name]
    bounds = _expanded_bounds(map_config, cell_size)
    terrain = Terrain(resources, map_name)
    obstacles = ObstacleField(resources, map_name, soft_models=soft_models)
    width = int(math.ceil((bounds[2] - bounds[0]) / cell_size))
    height = int(math.ceil((bounds[3] - bounds[1]) / cell_size))
    origin_x = bounds[0] + cell_size * 0.5
    origin_z = bounds[1] + cell_size * 0.5
    heights = [None] * (width * height)
    hazards = [0] * (width * height)
    rejected_water = 0
    shallow_water = 0
    rejected_obstacle = 0
    rejected_edge = 0
    for z_index in range(height):
        z = origin_z + z_index * cell_size
        for x_index in range(width):
            x = origin_x + x_index * cell_size
            index = z_index * width + x_index
            ground = _ground_height(terrain, obstacles, x, z)
            if ground is None:
                continue
            water_depth = terrain.water_depth(x, z, ground)
            if water_depth > WATER_DEPTH_LIMIT:
                hazards[index] |= HAZARD_WATER
                rejected_water += 1
                continue
            if water_depth > SHALLOW_WATER_THRESHOLD:
                hazards[index] |= HAZARD_SHALLOW_WATER
                shallow_water += 1
            if not _has_safe_edge_clearance(terrain, obstacles, x, z, ground):
                hazards[index] |= HAZARD_EDGE
                rejected_edge += 1
                continue
            obstacle_margin = (BRIDGE_OBSTACLE_MARGIN
                               if obstacles.surface_height(x, z) is not None
                               else VEHICLE_HALF_WIDTH)
            if obstacles.blocked(x, z, ground, margin=obstacle_margin):
                rejected_obstacle += 1
                continue
            heights[index] = int(round(ground * 1000.0))
    links = [0] * (width * height)
    for z_index in range(height):
        for x_index in range(width):
            index = z_index * width + x_index
            if heights[index] is None:
                continue
            start = (origin_x + x_index * cell_size,
                     heights[index] / 1000.0,
                     origin_z + z_index * cell_size)
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                nx = x_index + dx
                nz = z_index + dz
                if nx < 0 or nx >= width or nz < 0 or nz >= height:
                    continue
                neighbour = nz * width + nx
                if heights[neighbour] is None:
                    continue
                if dx and dz:
                    side_a = z_index * width + nx
                    side_b = nz * width + x_index
                    if heights[side_a] is None or heights[side_b] is None:
                        # A four-metre grid can represent a narrow diagonal
                        # bridge as one deck cell across. The sampled segment,
                        # grade, water and collision checks below are the
                        # authoritative safety test for two bridge endpoints.
                        if (obstacles.surface_height(start[0], start[2]) is None or
                                obstacles.surface_height(
                                    origin_x + nx * cell_size,
                                    origin_z + nz * cell_size) is None):
                            continue
                end = (origin_x + nx * cell_size,
                       heights[neighbour] / 1000.0,
                       origin_z + nz * cell_size)
                if _segment_clear(terrain, obstacles, start, end):
                    links[index] |= 1 << direction_index
    # ``links`` are stored per source node and therefore support one-way edges.
    # Ordinary tanks must not intentionally use a one-way fall, though: retain
    # an edge only when the reverse traversal was independently validated too.
    reverse_directions = dict((direction, index)
                              for index, direction in enumerate(DIRECTIONS))
    for z_index in range(height):
        for x_index in range(width):
            index = z_index * width + x_index
            mask = links[index]
            for direction_index, (dx, dz) in enumerate(DIRECTIONS):
                if not (mask & (1 << direction_index)):
                    continue
                neighbour = (z_index + dz) * width + (x_index + dx)
                reverse_index = reverse_directions[(-dx, -dz)]
                if not (links[neighbour] & (1 << reverse_index)):
                    mask &= ~(1 << direction_index)
            links[index] = mask
    graph = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "game_version": GAME_VERSION,
        "map": map_name,
        "cell_size": float(cell_size),
        "bounds": list(bounds),
        "origin": [origin_x, origin_z],
        "width": width,
        "height": height,
        "directions": [list(direction) for direction in DIRECTIONS],
        "heights_mm": heights,
        "links": links,
        # Hazard cells are distinct from ordinary non-navigable obstacle cells.
        # Runtime rollback may reject water/cliff entry without treating every
        # building footprint as a fatal map edge.
        "hazards": hazards,
        "bases": [list(base) for base in map_config["bases"]],
        "bake": {
            "water_depth_limit": WATER_DEPTH_LIMIT,
            "shallow_water_threshold": SHALLOW_WATER_THRESHOLD,
            "shallow_water_cost_multiplier": SHALLOW_WATER_COST_MULTIPLIER,
            "vehicle_half_width": VEHICLE_HALF_WIDTH,
            "bridge_obstacle_margin": BRIDGE_OBSTACLE_MARGIN,
            "vehicle_clearance_height": VEHICLE_CLEARANCE_HEIGHT,
            "vehicle_ground_clearance": VEHICLE_GROUND_CLEARANCE,
            "max_grade": MAX_GRADE,
            "max_grade_up": MAX_GRADE_UP,
            "max_grade_down": MAX_GRADE_DOWN,
            "reversible_links": True,
            "edge_clearance_radii": list(EDGE_CLEARANCE_RADII),
            "terrain_chunks": len(terrain.chunks),
            "water_planes": len(terrain.waters),
            "model_shapes": len(obstacles.model_library.cache),
            "model_instances": obstacles.instance_count,
            "bridge_model_instances": obstacles.bridge_instance_count,
            "bridge_surface_triangles": obstacles.bridge_surface_triangle_count,
            "bridge_surface_cells": len(obstacles.surface_cells),
            "soft_model_instances": obstacles.soft_instance_count,
            "local_obstacle_instances": obstacles.local_instance_count,
            "local_obstacle_max_height": LOCAL_OBSTACLE_MAX_HEIGHT,
            "obstacle_raster_cells": len(obstacles.cells),
            "skipped_models": obstacles.skipped,
            "rejected_water_nodes": rejected_water,
            "shallow_water_nodes": shallow_water,
            "rejected_obstacle_nodes": rejected_obstacle,
            "rejected_edge_nodes": rejected_edge,
        },
    }
    retain_base_component(graph, map_config)
    graph["routes"] = bake_tactical_routes(graph, map_config)
    graph["validation"] = validate_graph(graph, map_config)
    return graph


def write_graph(path, graph):
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as output:
        json.dump(graph, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
    os.replace(temporary, path)


def default_output(map_name):
    return os.path.join(
        REPO_ROOT,
        "scripts", "client", "gui", "mods", "offhangar", "navgraphs",
        map_name + ".json",
    )


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_staged_batch(staging_dir, map_names):
    """Publish a fully validated batch; callers never invoke this on failure."""
    target_dir = os.path.dirname(default_output(map_names[0]))
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    files = []
    for map_name in map_names:
        source = os.path.join(staging_dir, map_name + ".json")
        files.append({
            "map": map_name,
            "file": map_name + ".json",
            "sha256": _file_sha256(source),
        })
    manifest = {
        "format": FORMAT_NAME + "-manifest",
        "version": FORMAT_VERSION,
        "game_version": GAME_VERSION,
        "maps": files,
    }
    manifest_path = os.path.join(staging_dir, "manifest.json")
    write_graph(manifest_path, manifest)
    for map_name in map_names:
        os.replace(os.path.join(staging_dir, map_name + ".json"),
                   default_output(map_name))
    # The manifest is the batch completion marker and is always promoted last.
    os.replace(manifest_path, os.path.join(target_dir, "manifest.json"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True,
                        help="Path to the pinned World of Tanks 0.8.2 client")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--map", choices=sorted(MAPS))
    selection.add_argument("--all", action="store_true",
                           help="Bake and validate every stock 0.8.2 map")
    parser.add_argument("--cell-size", type=float, default=4.0)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.all and args.output:
        parser.error("--output can only be used with one --map")
    map_names = sorted(MAPS) if args.all else [args.map or "07_lakeville"]
    packages = os.path.join(os.path.abspath(args.client), "res", "packages")
    shared_package = os.path.join(packages, "shared_content.pkg")
    destructibles_path = os.path.join(os.path.abspath(args.client),
                                      "res", "scripts", "destructibles.xml")
    for path in (shared_package, destructibles_path):
        if not os.path.isfile(path):
            parser.error("required client resource not found: %s" % path)
    with open(destructibles_path, "rb") as destructibles_file:
        soft_models = soft_destructible_models(destructibles_file.read())

    failures = []
    staging_dir = tempfile.mkdtemp(prefix="offhangar-navgraphs-") if args.all else None
    for map_name in map_names:
        try:
            actual_bases = read_client_ctf_bases(args.client, map_name)
            validate_tactical_bases(map_name, MAPS[map_name], actual_bases)
        except Exception as error:
            failures.append((map_name, str(error)))
            print("FAILED %s: %s" % (map_name, error))
            continue
        map_package = os.path.join(packages, map_name + ".pkg")
        if not os.path.isfile(map_package):
            failures.append((map_name, "map package not found"))
            print("FAILED %s: map package not found" % map_name)
            continue
        output = (os.path.join(staging_dir, map_name + ".json")
                  if staging_dir is not None else args.output or default_output(map_name))
        resources = PackageResources((map_package, shared_package))
        try:
            graph = bake_graph(resources, map_name, args.cell_size, soft_models)
        except Exception as error:
            failures.append((map_name, str(error)))
            print("FAILED %s: %s" % (map_name, error))
            continue
        finally:
            resources.close()
        write_graph(output, graph)
        validation = graph["validation"]
        print("Baked %s: %d/%d navigable nodes, %d model instances" % (
            map_name,
            sum(value is not None for value in graph["heights_mm"]),
            len(graph["heights_mm"]),
            graph["bake"]["model_instances"],
        ))
        print("Validated: %d components, largest %.1f%%, base route %.1f m" % (
            validation["components"],
            validation["largest_fraction"] * 100.0,
            validation["base_path_metres"],
        ))
        print("Output: %s" % os.path.abspath(output))
    if failures:
        print("Bake failed for %d/%d map(s)." % (len(failures), len(map_names)))
        if staging_dir is not None:
            shutil.rmtree(staging_dir)
        return 1
    if staging_dir is not None:
        _publish_staged_batch(staging_dir, map_names)
        shutil.rmtree(staging_dir)
    print("Bake completed for %d map(s)." % len(map_names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
