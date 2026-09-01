"""Interactive armor geometry viewer for the 0.9.22 vehicle editor.

The viewer reads the stock ``collision_client`` resources directly from the
selected #1513 installation.  It intentionally uses only the Python standard
library and Tk so the Windows launcher keeps its existing packaging boundary.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import math
import os
import re
import struct
import zipfile

try:
    import packed_xml
except ImportError:
    import sys

    _TOOLS_ROOT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "0.9.22", "tools")
    if _TOOLS_ROOT not in sys.path:
        sys.path.insert(0, _TOOLS_ROOT)
    import packed_xml

try:
    from . import i18n, vehicle_overlays
except ImportError:
    import i18n
    import vehicle_overlays


_PRIMITIVES_MAGIC = b"\x65\x4e\xa1\x42"
_ARMOR_FIELD = re.compile(r"^[A-Za-z0-9_.-]+$")
_TURRET_GROUP = re.compile(r"^turrets[0-9]+$")
_TURRET_VARIANT_KEY = re.compile(r"^turret[0-9]+$")
_MESH_CACHE_LIMIT = 64
_MESH_CACHE = OrderedDict()
_RESOURCE_PACKAGE_CACHE = {}

_ZH = {
    "3D armor viewer": "3D 装甲检视",
    "Drag to orbit · wheel to zoom · click armor to select its field":
        "拖动旋转 · 滚轮缩放 · 点击装甲跳转到对应属性",
    "Loading stock collision model...": "正在读取原版碰撞模型…",
    "No armor field selected": "尚未选中装甲属性",
    "Selected: %s · %s mm": "已选：%s · %s 毫米",
    "Selected: %s · %s mm · no surface in this collision model":
        "已选：%s · %s 毫米 · 当前碰撞模型中没有可视化表面",
    "Material %s has no editable thickness field.":
        "材质 %s 没有可修改的厚度属性。",
    "Armor model unavailable: %s": "无法显示装甲模型：%s",
    "Reset view": "复位视角",
    "Nominal thickness (mm)": "标称厚度（毫米）",
    "Unmapped": "未映射",
    "Click an armor surface or choose an armor field.":
        "点击装甲表面，或在左侧选择装甲属性。",
}


class ArmorViewerError(Exception):
    """A contained stock-resource or geometry error."""


def _translated(language, text):
    if i18n.resolve_language(language) == i18n.LANGUAGE_CHINESE:
        return _ZH.get(text, text)
    return text


def _aligned(value, alignment=4):
    return (value + alignment - 1) // alignment * alignment


def _decode_primitives_sections(data):
    """Return named payloads from one BigWorld primitives container."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 32:
        raise ArmorViewerError("The primitives resource is truncated.")
    if bytes(data[:4]) != _PRIMITIVES_MAGIC:
        raise ArmorViewerError("The primitives resource has an unknown magic.")
    footer_size = struct.unpack_from("<I", data, len(data) - 4)[0]
    footer_start = len(data) - 4 - footer_size
    if footer_start < 4 or footer_start % 4:
        raise ArmorViewerError("The primitives directory is invalid.")

    entries = []
    offset = footer_start
    footer_end = len(data) - 4
    while offset < footer_end:
        if offset + 24 > footer_end:
            raise ArmorViewerError("The primitives directory is truncated.")
        payload_size = struct.unpack_from("<I", data, offset)[0]
        offset += 20  # payload length plus the stock 16-byte digest field
        name_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        padded_name_size = _aligned(name_size)
        if (name_size < 1 or offset + padded_name_size > footer_end or
                b"\0" in data[offset:offset + name_size]):
            raise ArmorViewerError("A primitives section name is invalid.")
        try:
            name = bytes(data[offset:offset + name_size]).decode("ascii")
        except UnicodeDecodeError:
            raise ArmorViewerError(
                "A primitives section name is not ASCII text.")
        offset += padded_name_size
        entries.append((name, payload_size))
    if offset != footer_end or not entries:
        raise ArmorViewerError("The primitives directory is incomplete.")

    sections = {}
    payload_offset = 4
    for name, payload_size in entries:
        payload_end = payload_offset + payload_size
        if payload_end > footer_start or name in sections:
            raise ArmorViewerError("The primitives sections overlap.")
        sections[name] = bytes(data[payload_offset:payload_end])
        payload_offset = _aligned(payload_end)
    if payload_offset != footer_start:
        raise ArmorViewerError("The primitives payload sizes are inconsistent.")
    return sections


def _header_text(payload, offset=0, size=64):
    if len(payload) < offset + size:
        return ""
    raw = payload[offset:offset + size].split(b"\0", 1)[0]
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return ""


def _decode_index_section(payload):
    if len(payload) < 72:
        raise ArmorViewerError("The collision index buffer is truncated.")
    index_format = _header_text(payload)
    if index_format == "list":
        index_width = 2
        unpack_code = "H"
    elif index_format in ("list32", "list32bit"):
        index_width = 4
        unpack_code = "I"
    else:
        raise ArmorViewerError(
            "The collision index format is unsupported: %s" %
            (index_format or "unknown"))
    index_count, group_count = struct.unpack_from("<II", payload, 64)
    if index_count > 20 * 1000 * 1000 or group_count > 100000:
        raise ArmorViewerError("The collision index counts are unreasonable.")
    index_end = 72 + index_count * index_width
    expected = index_end + group_count * 16
    if expected != len(payload):
        raise ArmorViewerError("The collision index buffer size is invalid.")
    indices = struct.unpack_from(
        "<%d%s" % (index_count, unpack_code), payload, 72)
    groups = []
    for group_index in range(group_count):
        start, triangle_count, first_vertex, vertex_count = struct.unpack_from(
            "<4I", payload, index_end + group_index * 16)
        end = start + triangle_count * 3
        if (end > index_count or first_vertex + vertex_count > 0xffffffff or
                any(index < first_vertex or
                    index >= first_vertex + vertex_count
                    for index in indices[start:end])):
            raise ArmorViewerError("A collision primitive group is invalid.")
        groups.append({
            "start": start,
            "triangleCount": triangle_count,
            "firstVertex": first_vertex,
            "vertexCount": vertex_count,
        })
    return tuple(indices), tuple(groups)


def _decode_vertex_section(payload):
    if len(payload) < 136:
        raise ArmorViewerError("The collision vertex buffer is truncated.")
    vertex_format = _header_text(payload)
    declaration = _header_text(payload, 68)
    vertex_count = struct.unpack_from("<I", payload, 132)[0]
    if vertex_count < 1 or vertex_count > 10 * 1000 * 1000:
        raise ArmorViewerError("The collision vertex count is invalid.")
    data_size = len(payload) - 136
    if data_size % vertex_count:
        raise ArmorViewerError("The collision vertex stride is invalid.")
    stride = data_size // vertex_count
    if stride < 12 or stride > 512:
        raise ArmorViewerError("The collision vertex layout is unsupported.")
    if not vertex_format or not declaration:
        raise ArmorViewerError("The collision vertex declaration is missing.")
    vertices = []
    for index in range(vertex_count):
        point = struct.unpack_from("<3f", payload, 136 + index * stride)
        if not all(math.isfinite(value) for value in point):
            raise ArmorViewerError("The collision vertex data is not finite.")
        vertices.append(point)
    return tuple(vertices)


def _children(element, name):
    encoded = name.encode("utf-8")
    return [value for current, value in element.children
            if current == encoded]


def _element_children(element, name):
    return [value.value for value in _children(element, name)
            if value.value_type == packed_xml.TYPE_ELEMENT]


def _unique_child(element, name):
    values = _children(element, name)
    return values[0] if len(values) == 1 else None


def _first_child(element, name):
    values = _children(element, name)
    return values[0] if values else None


def _value_text(value):
    if value is None:
        return None
    if value.value_type == packed_xml.TYPE_STRING:
        try:
            return value.value.decode("utf-8")
        except (AttributeError, UnicodeDecodeError):
            return None
    if value.value_type == packed_xml.TYPE_INTEGER:
        return str(value.value)
    if value.value_type == packed_xml.TYPE_COMPRESSED_STRING:
        try:
            return base64.b64encode(value.value).decode("ascii")
        except (AttributeError, TypeError, ValueError):
            return None
    return None


def _vector(value, default=(0.0, 0.0, 0.0)):
    fallback = None if default is None else tuple(default)
    if value is None:
        return fallback
    if value.value_type == packed_xml.TYPE_VECTOR:
        raw = value.value
        if len(raw) != 12:
            return fallback
        result = struct.unpack("<3f", raw)
    elif value.value_type == packed_xml.TYPE_STRING:
        try:
            parts = value.value.decode("ascii").strip().split()
            if len(parts) != 3:
                return fallback
            result = tuple(float(part) for part in parts)
        except (AttributeError, UnicodeDecodeError, TypeError, ValueError,
                OverflowError):
            return fallback
    else:
        return fallback
    if not all(math.isfinite(item) for item in result):
        return fallback
    return tuple(float(item) for item in result)


def _layer_values(layers, path):
    current = [layer for layer in layers if layer is not None]
    for offset, name in enumerate(path):
        final = offset == len(path) - 1
        nested = []
        scalar = None
        for layer in current:
            value = _first_child(layer, name)
            if value is None:
                continue
            if final and scalar is None:
                scalar = value
            if value.value_type == packed_xml.TYPE_ELEMENT:
                nested.append(value.value)
        if final:
            return scalar
        current = nested
        if not current:
            return None
    return None


def _layer_elements(layers, path):
    current = [layer for layer in layers if layer is not None]
    for name in path:
        nested = []
        for layer in current:
            value = _first_child(layer, name)
            if value is not None and value.value_type == packed_xml.TYPE_ELEMENT:
                nested.append(value.value)
        current = nested
        if not current:
            break
    return current


def _ordered_entry_names(layers, container_name):
    names = []
    for container in _layer_elements(layers, (container_name,)):
        for raw_name, unused_value in container.children:
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if name not in names:
                names.append(name)
    return names


def _entry_layers(layers, container_name, entry_name):
    result = []
    for container in _layer_elements(layers, (container_name,)):
        value = _unique_child(container, entry_name)
        if value is not None and value.value_type == packed_xml.TYPE_ELEMENT:
            result.append(value.value)
    return result


def _shared_entry(root, name):
    if root is None:
        return None
    shared = _unique_child(root, "shared")
    if shared is None or shared.value_type != packed_xml.TYPE_ELEMENT:
        return None
    entry = _unique_child(shared.value, name)
    if entry is None or entry.value_type != packed_xml.TYPE_ELEMENT:
        return None
    return entry.value


def _matching_hull_variant(hull, chassis_name, turret_group, turret_name):
    """Return the best exact component-matched hull variant, if present."""
    variants = _first_child(hull, "variants")
    if variants is None or variants.value_type != packed_xml.TYPE_ELEMENT:
        return None
    best = None
    best_score = -1
    for unused_name, value in variants.value.children:
        if value.value_type != packed_xml.TYPE_ELEMENT:
            continue
        variant = value.value
        score = 0
        chassis = _first_child(variant, "chassis")
        if chassis is not None:
            if _value_text(chassis) != chassis_name:
                continue
            score += 100
        matched = True
        for raw_name, component in variant.children:
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if _TURRET_VARIANT_KEY.fullmatch(name) is None:
                continue
            group_name = "turrets" + name[len("turret"):]
            if (group_name != turret_group or
                    _value_text(component) != turret_name):
                matched = False
                break
            score += 1
        if matched and score > best_score and score > 0:
            best = variant
            best_score = score
    return best


def _armor_identity(record):
    parts = str(record.get("fieldPath", "")).split("/")
    if len(parts) < 3 or _ARMOR_FIELD.fullmatch(parts[-1]) is None:
        return None
    category = record.get("category")
    material = parts[-1]
    if category == "vehicle" and parts[:2] == ["hull", "armor"]:
        return ("hull", "hull", material, None)
    if category == "chassis":
        if (parts[0] == "shared" and len(parts) == 4 and
                parts[2] == "armor"):
            return ("chassis", parts[1], material, None)
        if (parts[0] == "chassis" and len(parts) == 4 and
                parts[2] == "armor"):
            return ("chassis", parts[1], material, ("local",))
    if category == "turret":
        if (parts[0] == "shared" and len(parts) == 4 and
                parts[2] == "armor"):
            return ("turret", parts[1], material, None)
        if (_TURRET_GROUP.fullmatch(parts[0]) is not None and
                len(parts) == 4 and parts[2] == "armor"):
            return ("turret", parts[1], material, (parts[0], parts[1]))
    if category == "guns":
        if (parts[0] == "shared" and len(parts) == 4 and
                parts[2] == "armor"):
            return ("gun", parts[1], material, None)
        if (_TURRET_GROUP.fullmatch(parts[0]) is not None and
                len(parts) == 6 and parts[2] == "guns" and
                parts[4] == "armor"):
            return ("gun", parts[3], material, (parts[0], parts[1]))
    return None


def _record_map(records):
    result = {}
    for record in records:
        identity = _armor_identity(record)
        if identity is not None and identity not in result:
            result[identity] = record
    return result


def _focus_identity(record):
    return _armor_identity(record or {})


def _component_roots(package_path, nation):
    result = {}
    for category in ("chassis", "guns", "turrets"):
        member = "scripts/item_defs/vehicles/%s/components/%s.xml" % (
            nation, category)
        try:
            unused_data, result[category] = vehicle_overlays._read_source_member(
                package_path, member)
        except vehicle_overlays.VehicleOverlayError:
            result[category] = None
    return result


def _resource_package_paths(game_root):
    package_root = os.path.join(os.path.abspath(game_root), "res", "packages")
    if not os.path.isdir(package_root):
        raise ArmorViewerError("The stock resource package folder is missing.")
    try:
        names = [name for name in os.listdir(package_root)
                 if name.lower().endswith(".pkg")]
    except OSError as error:
        raise ArmorViewerError(
            "The stock resource package folder is unreadable: %s" % error)
    preferred = [name for name in names
                 if name.startswith("vehicles_") or
                 name.startswith("00_tank_tutorial")]
    remaining = [name for name in names if name not in preferred]
    return tuple(os.path.join(package_root, name)
                 for name in sorted(preferred) + sorted(remaining))


def _resource_directory(member):
    return member.rsplit("/", 1)[0] if "/" in member else member


def _read_resource_member(game_root, member):
    member = re.sub(r"/{2,}", "/", member)
    if (not member.startswith("vehicles/") or "\\" in member or
            member.startswith("/") or ".." in member.split("/")):
        raise ArmorViewerError("The collision resource path is unsafe.")
    root = os.path.realpath(os.path.abspath(game_root))
    directory_key = (root, _resource_directory(member))
    paths = list(_resource_package_paths(root))
    preferred = _RESOURCE_PACKAGE_CACHE.get(directory_key)
    if preferred in paths:
        paths.remove(preferred)
        paths.insert(0, preferred)
    for path in paths:
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with zipfile.ZipFile(path, "r") as archive:
                matches = [info for info in archive.infolist()
                           if info.filename == member]
                if len(matches) > 1:
                    raise ArmorViewerError(
                        "A collision resource is repeated in %s." %
                        os.path.basename(path))
                if matches:
                    payload = archive.read(matches[0])
                    _RESOURCE_PACKAGE_CACHE[directory_key] = path
                    return payload
        except ArmorViewerError:
            raise
        except (IOError, OSError, ValueError, zipfile.BadZipFile) as error:
            raise ArmorViewerError(
                "A stock resource package is unreadable: %s" % error)
    raise ArmorViewerError("The stock resource is missing: %s" % member)


def _material_identifiers(visual_root):
    groups = []
    render_sets = _element_children(visual_root, "renderSet")
    for render_set in render_sets:
        for geometry in _element_children(render_set, "geometry"):
            for primitive_group in _element_children(
                    geometry, "primitiveGroup"):
                materials = _element_children(primitive_group, "material")
                identifier = None
                if len(materials) == 1:
                    identifier = _value_text(
                        _unique_child(materials[0], "identifier"))
                groups.append(identifier or "unmapped")
    return tuple(groups)


def _mesh_for_model(game_root, model_member):
    cache_key = (os.path.realpath(os.path.abspath(game_root)), model_member)
    if cache_key in _MESH_CACHE:
        mesh = _MESH_CACHE.pop(cache_key)
        _MESH_CACHE[cache_key] = mesh
        return mesh
    if not model_member.endswith(".model"):
        raise ArmorViewerError("The collision model path is invalid.")
    try:
        model_root = packed_xml.read_packed_xml(
            _read_resource_member(game_root, model_member))
    except (TypeError, ValueError) as error:
        raise ArmorViewerError("The collision model is unreadable: %s" % error)
    visual_base = _value_text(_unique_child(model_root, "nodelessVisual"))
    if not visual_base or not visual_base.startswith("vehicles/"):
        raise ArmorViewerError("The collision model has no nodeless visual.")
    visual_member = visual_base + ".visual_processed"
    primitives_member = visual_base + ".primitives_processed"
    try:
        visual_root = packed_xml.read_packed_xml(
            _read_resource_member(game_root, visual_member))
        sections = _decode_primitives_sections(
            _read_resource_member(game_root, primitives_member))
    except (TypeError, ValueError) as error:
        raise ArmorViewerError(
            "The collision visual is unreadable: %s" % error)

    index_candidates = []
    vertex_candidates = []
    for name, payload in sections.items():
        header = _header_text(payload)
        if header in ("list", "list32", "list32bit"):
            index_candidates.append((name, payload))
        elif len(payload) >= 136 and _header_text(payload, 68):
            remainder = len(payload) - 136
            count = struct.unpack_from("<I", payload, 132)[0]
            if count and remainder >= count * 12 and remainder % count == 0:
                vertex_candidates.append((name, payload))
    if len(index_candidates) != 1 or len(vertex_candidates) != 1:
        raise ArmorViewerError(
            "The collision visual does not have one mesh buffer pair.")
    indices, groups = _decode_index_section(index_candidates[0][1])
    vertices = _decode_vertex_section(vertex_candidates[0][1])
    identifiers = _material_identifiers(visual_root)
    if len(identifiers) != len(groups):
        raise ArmorViewerError(
            "The collision materials and primitive groups do not match.")

    surfaces = []
    for group_index, group in enumerate(groups):
        triangles = []
        start = group["start"]
        for offset in range(group["triangleCount"]):
            point_indices = indices[start + offset * 3:start + offset * 3 + 3]
            if any(index >= len(vertices) for index in point_indices):
                raise ArmorViewerError(
                    "A collision triangle references a missing vertex.")
            triangles.append(tuple(vertices[index] for index in point_indices))
        surfaces.append({
            "material": identifiers[group_index],
            "triangles": tuple(triangles),
        })
    result = {"model": model_member, "surfaces": tuple(surfaces)}
    _MESH_CACHE[cache_key] = result
    while len(_MESH_CACHE) > _MESH_CACHE_LIMIT:
        _MESH_CACHE.popitem(last=False)
    return result


def _collision_model(layers):
    value = _layer_values(layers, ("hitTester", "collisionModelClient"))
    path = _value_text(value)
    if not path:
        return None
    return path if path.endswith(".model") else path + ".model"


def _add_points(left, right):
    return tuple(float(left[index]) + float(right[index]) for index in range(3))


def _part_scene(game_root, role, name, layers, offset, records, contexts):
    model = _collision_model(layers)
    if not model:
        return None
    mesh = _mesh_for_model(game_root, model)
    mapping = _record_map(records)
    surfaces = []
    for surface in mesh["surfaces"]:
        material = surface["material"]
        record = next((mapping[(role, name, material, context)]
                       for context in contexts
                       if (role, name, material, context) in mapping), None)
        thickness = None
        if record is not None:
            raw_value = record.get(
                "currentValue", record.get("originalValue"))
            try:
                thickness = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                thickness = None
        translated_triangles = tuple(
            tuple(_add_points(point, offset) for point in triangle)
            for triangle in surface["triangles"])
        surfaces.append({
            "role": role,
            "component": name,
            "material": material,
            "fieldKey": ((record["member"], record["fieldPath"])
                         if record is not None else None),
            "thickness": thickness,
            "triangles": translated_triangles,
        })
    return {
        "role": role,
        "component": name,
        "model": model,
        "surfaces": tuple(surfaces),
    }


def _configuration(vehicle_root, component_roots, focus):
    focus_role = focus[0] if focus else None
    focus_name = focus[1] if focus else None
    focus_context = focus[3] if focus else None

    chassis_names = _ordered_entry_names([vehicle_root], "chassis")
    if not chassis_names:
        raise ArmorViewerError("The vehicle has no chassis configuration.")
    chassis_name = (focus_name if focus_role == "chassis" and
                    focus_name in chassis_names else chassis_names[0])
    chassis_layers = _entry_layers([vehicle_root], "chassis", chassis_name)
    shared_chassis = _shared_entry(component_roots.get("chassis"), chassis_name)
    if shared_chassis is not None:
        chassis_layers.append(shared_chassis)

    turret_candidates = []
    for raw_group, value in vehicle_root.children:
        try:
            group_name = raw_group.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (_TURRET_GROUP.fullmatch(group_name) is not None and
                value.value_type == packed_xml.TYPE_ELEMENT):
            for raw_name, unused_entry in value.value.children:
                try:
                    name = raw_name.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                turret_candidates.append((group_name, name))
    if not turret_candidates:
        raise ArmorViewerError("The vehicle has no turret configuration.")

    chosen_turret = None
    if focus_role == "turret":
        if focus_context is not None:
            chosen_turret = next((item for item in turret_candidates
                                  if item == focus_context), None)
        else:
            chosen_turret = next((item for item in turret_candidates
                                  if item[1] == focus_name), None)
    if focus_role == "gun":
        candidates = turret_candidates
        if focus_context is not None:
            candidates = [item for item in turret_candidates
                          if item == focus_context]
        for item in candidates:
            group = _unique_child(vehicle_root, item[0])
            local_turret = (_unique_child(group.value, item[1])
                            if group is not None and
                            group.value_type == packed_xml.TYPE_ELEMENT
                            else None)
            turret_layers = ([local_turret.value]
                             if local_turret is not None and
                             local_turret.value_type == packed_xml.TYPE_ELEMENT
                             else [])
            shared = _shared_entry(component_roots.get("turrets"), item[1])
            if shared is not None:
                turret_layers.append(shared)
            if focus_name in _ordered_entry_names(turret_layers, "guns"):
                chosen_turret = item
                break
    if chosen_turret is None:
        chosen_turret = turret_candidates[0]

    turret_group, turret_name = chosen_turret
    group_value = _unique_child(vehicle_root, turret_group)
    local_turret = (_unique_child(group_value.value, turret_name)
                    if group_value is not None and
                    group_value.value_type == packed_xml.TYPE_ELEMENT
                    else None)
    turret_layers = ([local_turret.value]
                     if local_turret is not None and
                     local_turret.value_type == packed_xml.TYPE_ELEMENT
                     else [])
    shared_turret = _shared_entry(component_roots.get("turrets"), turret_name)
    if shared_turret is not None:
        turret_layers.append(shared_turret)

    gun_names = _ordered_entry_names(turret_layers, "guns")
    if not gun_names:
        raise ArmorViewerError("The selected turret has no gun configuration.")
    gun_name = (focus_name if focus_role == "gun" and
                focus_name in gun_names else gun_names[0])
    gun_layers = _entry_layers(turret_layers, "guns", gun_name)
    shared_gun = _shared_entry(component_roots.get("guns"), gun_name)
    if shared_gun is not None:
        gun_layers.append(shared_gun)

    hull = _unique_child(vehicle_root, "hull")
    if hull is None or hull.value_type != packed_xml.TYPE_ELEMENT:
        raise ArmorViewerError("The vehicle hull descriptor is missing.")
    hull_variant = _matching_hull_variant(
        hull.value, chassis_name, turret_group, turret_name)
    hull_layers = ([hull_variant] if hull_variant is not None else [])
    hull_layers.append(hull.value)
    return {
        "chassis": (chassis_name, chassis_layers),
        "hull": ("hull", hull_layers),
        "turret": (turret_group, turret_name, turret_layers),
        "gun": (gun_name, gun_layers),
    }


def _scene_bounds(parts):
    points = [point for part in parts for surface in part["surfaces"]
              for triangle in surface["triangles"] for point in triangle]
    if not points:
        raise ArmorViewerError("The collision scene contains no triangles.")
    minimum = tuple(min(point[index] for point in points) for index in range(3))
    maximum = tuple(max(point[index] for point in points) for index in range(3))
    return minimum, maximum


def load_vehicle_armor_scene(game_root, vehicle_member, records,
                             focus_record=None):
    """Load one mounted configuration around the requested armor field."""
    status, package_path = vehicle_overlays._require_target(game_root)
    match = vehicle_overlays._VEHICLE_MEMBER.fullmatch(vehicle_member)
    if match is None or "/components/" in vehicle_member:
        raise ArmorViewerError("Select one original vehicle definition.")
    nation, unused_vehicle = match.groups()
    try:
        unused_data, vehicle_root = vehicle_overlays._read_source_member(
            package_path, vehicle_member)
    except vehicle_overlays.VehicleOverlayError as error:
        raise ArmorViewerError(str(error))
    component_roots = _component_roots(package_path, nation)
    focus = _focus_identity(focus_record)
    config = _configuration(vehicle_root, component_roots, focus)

    chassis_name, chassis_layers = config["chassis"]
    hull_name, hull_layers = config["hull"]
    turret_group, turret_name, turret_layers = config["turret"]
    gun_name, gun_layers = config["gun"]
    hull_position = _vector(_layer_values(chassis_layers, ("hullPosition",)))
    turret_positions = _layer_elements(hull_layers, ("turretPositions",))
    try:
        turret_slot = int(turret_group[len("turrets"):])
    except (TypeError, ValueError):
        raise ArmorViewerError("The turret group has no valid position slot.")
    if not turret_positions or turret_slot >= len(turret_positions[0].children):
        raise ArmorViewerError(
            "The hull has no position for %s." % turret_group)
    turret_position = _vector(
        turret_positions[0].children[turret_slot][1], default=None)
    if turret_position is None:
        raise ArmorViewerError(
            "The hull position for %s is invalid." % turret_group)
    gun_position = _vector(_layer_values(turret_layers, ("gunPosition",)))
    turret_offset = _add_points(hull_position, turret_position)
    gun_offset = _add_points(turret_offset, gun_position)

    specs = (
        ("chassis", chassis_name, chassis_layers, (0.0, 0.0, 0.0),
         (("local",), None)),
        ("hull", hull_name, hull_layers, hull_position, (None,)),
        ("turret", turret_name, turret_layers, turret_offset,
         ((turret_group, turret_name), None)),
        ("gun", gun_name, gun_layers, gun_offset,
         ((turret_group, turret_name), None)),
    )
    parts = []
    errors = []
    for role, name, layers, offset, contexts in specs:
        try:
            part = _part_scene(
                status["path"], role, name, layers, offset, records, contexts)
        except ArmorViewerError as error:
            errors.append("%s: %s" % (role, error))
            continue
        if part is not None:
            parts.append(part)
    if not parts:
        raise ArmorViewerError("; ".join(errors) or
                               "No stock collision models were found.")
    return {
        "vehicleMember": vehicle_member,
        "configuration": {
            "chassis": chassis_name,
            "turretGroup": turret_group,
            "turret": turret_name,
            "gun": gun_name,
        },
        "parts": tuple(parts),
        "bounds": _scene_bounds(parts),
        "warnings": tuple(errors),
    }


def _interpolate(left, right, amount):
    return tuple(int(round(left[index] + (right[index] - left[index]) * amount))
                 for index in range(3))


_COLOR_STOPS = (
    (0.0, (76, 88, 105)),
    (20.0, (55, 126, 184)),
    (50.0, (45, 176, 168)),
    (100.0, (99, 190, 94)),
    (150.0, (229, 190, 64)),
    (250.0, (226, 101, 55)),
    (400.0, (180, 68, 127)),
)


def thickness_color(thickness):
    """Map nominal millimetres to a stable thin-to-thick color ramp."""
    try:
        value = max(0.0, float(thickness))
    except (TypeError, ValueError, OverflowError):
        return "#555b66"
    for index in range(len(_COLOR_STOPS) - 1):
        start_value, start_color = _COLOR_STOPS[index]
        end_value, end_color = _COLOR_STOPS[index + 1]
        if value <= end_value:
            amount = ((value - start_value) / (end_value - start_value)
                      if end_value != start_value else 0.0)
            color = _interpolate(start_color, end_color, max(0.0, amount))
            return "#%02x%02x%02x" % color
    return "#%02x%02x%02x" % _COLOR_STOPS[-1][1]


def _shade(color, amount):
    raw = color.lstrip("#")
    base = tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))
    if amount >= 0:
        target = (255, 255, 255)
        result = _interpolate(base, target, min(1.0, amount))
    else:
        result = _interpolate(base, (0, 0, 0), min(1.0, -amount))
    return "#%02x%02x%02x" % result


class ArmorViewerPanel(object):
    """Tk Canvas software renderer with orbit, zoom, and surface picking."""

    def __init__(self, parent, tk_module, on_select, language, game_root,
                 scene_loader=load_vehicle_armor_scene):
        self._tk = tk_module
        self._on_select = on_select
        self._language = i18n.resolve_language(language)
        self._game_root = game_root
        self._scene_loader = scene_loader
        self._vehicle_member = None
        self._records = []
        self._record_by_key = {}
        self._focus_key = None
        self._scene = None
        self._scene_error = ""
        self._yaw = -0.65
        self._pitch = -0.22
        self._zoom = 1.0
        self._drag_start = None
        self._drag_last = None
        self._dragged = False
        self._item_surface = {}

        self.frame = tk_module.LabelFrame(
            parent, text=self._t("3D armor viewer"), padx=8, pady=8)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(2, weight=1)
        tk_module.Label(
            self.frame,
            text=self._t(
                "Drag to orbit · wheel to zoom · click armor to select its field"),
            anchor="w").grid(row=0, column=0, sticky="we")
        self.selection = tk_module.StringVar(
            value=self._t("No armor field selected"))
        tk_module.Label(
            self.frame, textvariable=self.selection, anchor="w",
            justify="left", wraplength=520).grid(
                row=1, column=0, sticky="we", pady=(4, 6))
        self.canvas = tk_module.Canvas(
            self.frame, width=540, height=500, background="#171b22",
            highlightthickness=1, highlightbackground="#414958")
        self.canvas.grid(row=2, column=0, sticky="nsew")
        controls = tk_module.Frame(self.frame)
        controls.grid(row=3, column=0, sticky="we", pady=(6, 0))
        tk_module.Button(
            controls, text=self._t("Reset view"), command=self.reset_view).pack(
                side="right")
        tk_module.Label(
            controls, text=self._t("Nominal thickness (mm)"),
            anchor="w").pack(side="left")
        self.legend = tk_module.Canvas(
            controls, width=285, height=28, background="#171b22",
            highlightthickness=0)
        self.legend.pack(side="left", padx=(8, 0))
        self._draw_legend()

        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_by(1.12))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_by(1.0 / 1.12))

    def _t(self, text):
        return _translated(self._language, text)

    def grid(self, *args, **kwargs):
        return self.frame.grid(*args, **kwargs)

    def load_vehicle(self, vehicle_member, records, focus_record=None):
        self._vehicle_member = vehicle_member
        self._records = [dict(record) for record in records]
        self._record_by_key = dict(
            ((record["member"], record["fieldPath"]), record)
            for record in self._records)
        self._focus_key = None
        self.selection.set(self._t("Loading stock collision model..."))
        self._load_scene(focus_record)

    def _load_scene(self, focus_record=None):
        if not self._vehicle_member:
            return False
        try:
            self._scene = self._scene_loader(
                self._game_root, self._vehicle_member, self._records,
                focus_record=focus_record)
        except (ArmorViewerError, vehicle_overlays.VehicleOverlayError,
                IOError, OSError, ValueError, zipfile.BadZipFile) as error:
            self._scene = None
            self._scene_error = self._t("Armor model unavailable: %s") % error
            self.selection.set(self._scene_error)
            self._redraw()
            return False
        self._scene_error = ""
        self._redraw()
        if focus_record is None:
            self.selection.set(self._t(
                "Click an armor surface or choose an armor field."))
            return True
        return self.focus_field(focus_record, reload_scene=False)

    def focus_field(self, record, reload_scene=True):
        identity = _focus_identity(record)
        new_key = ((record.get("member"), record.get("fieldPath"))
                   if identity is not None else None)
        if (reload_scene and new_key is not None and self._scene is not None and
                not self._scene_contains_key(new_key)):
            self._focus_key = new_key
            return self._load_scene(record)
        self._focus_key = new_key
        if new_key is None:
            self.selection.set(
                self._scene_error if self._scene is None and self._scene_error
                else self._t("No armor field selected"))
        elif self._scene is None:
            self.selection.set(self._scene_error or self._t(
                "Selected: %s · %s mm · no surface in this collision model") % (
                    record.get("fieldLabel", record.get("fieldPath", "-")),
                    record.get("currentValue",
                               record.get("originalValue", "-"))))
        elif not self._scene_contains_key(new_key):
            current = record.get("currentValue", record.get("originalValue", "-"))
            self.selection.set(self._t(
                "Selected: %s · %s mm · no surface in this collision model") % (
                    record.get("fieldLabel", record.get("fieldPath", "-")),
                    current))
        else:
            current = record.get("currentValue", record.get("originalValue", "-"))
            self.selection.set(self._t("Selected: %s · %s mm") % (
                record.get("fieldLabel", record.get("fieldPath", "-")), current))
        self._redraw()
        return new_key is not None and self._scene_contains_key(new_key)

    def _scene_contains_key(self, key):
        if self._scene is None:
            return False
        return any(surface.get("fieldKey") == key
                   for part in self._scene["parts"]
                   for surface in part["surfaces"])

    def update_field(self, member, field_path, current_value):
        key = (member, field_path)
        record = self._record_by_key.get(key)
        if record is None:
            return False
        record["currentValue"] = str(current_value)
        for current in self._records:
            if (current["member"], current["fieldPath"]) == key:
                current["currentValue"] = str(current_value)
        if self._scene is not None:
            for part in self._scene["parts"]:
                for surface in part["surfaces"]:
                    if surface["fieldKey"] == key:
                        try:
                            surface["thickness"] = float(current_value)
                        except (TypeError, ValueError, OverflowError):
                            surface["thickness"] = None
        if self._focus_key == key:
            self.focus_field(record, reload_scene=False)
        else:
            self._redraw()
        return True

    def reset_values(self):
        for record in self._records:
            record["currentValue"] = record.get("originalValue", "0")
        focus = self._record_by_key.get(self._focus_key)
        self._load_scene(focus)

    def reset_view(self):
        self._yaw = -0.65
        self._pitch = -0.22
        self._zoom = 1.0
        self._redraw()

    def _draw_legend(self):
        self.legend.delete("all")
        values = (0, 20, 50, 100, 150, 250, 400)
        width = 38
        for index, value in enumerate(values):
            left = index * width
            self.legend.create_rectangle(
                left, 1, left + width, 13, fill=thickness_color(value),
                outline="")
            label = "%s+" % value if index == len(values) - 1 else str(value)
            self.legend.create_text(
                left + width / 2, 21, text=label, fill="#d8dde7",
                font=("TkDefaultFont", 7))

    def _press(self, event):
        self._drag_start = (event.x, event.y)
        self._drag_last = (event.x, event.y)
        self._dragged = False

    def _drag(self, event):
        if self._drag_last is None:
            return
        dx = event.x - self._drag_last[0]
        dy = event.y - self._drag_last[1]
        if abs(event.x - self._drag_start[0]) + abs(
                event.y - self._drag_start[1]) > 4:
            self._dragged = True
        self._yaw += dx * 0.012
        self._pitch = max(-1.35, min(1.35, self._pitch + dy * 0.009))
        self._drag_last = (event.x, event.y)
        self._redraw()

    def _release(self, event):
        if not self._dragged:
            self._pick(event.x, event.y)
        self._drag_start = None
        self._drag_last = None

    def _wheel(self, event):
        delta = getattr(event, "delta", 0)
        if delta:
            self._zoom_by(1.12 if delta > 0 else 1.0 / 1.12)

    def _zoom_by(self, factor):
        self._zoom = max(0.45, min(3.5, self._zoom * factor))
        self._redraw()
        return "break"

    def _pick(self, x, y):
        matches = self.canvas.find_overlapping(x, y, x, y)
        surface = next((self._item_surface[item]
                        for item in reversed(matches)
                        if item in self._item_surface), None)
        if surface is None:
            return False
        key = surface.get("fieldKey")
        if key is None or key not in self._record_by_key:
            self.selection.set(self._t(
                "Material %s has no editable thickness field.") %
                surface.get("material", "unmapped"))
            return False
        self._on_select(key)
        return True

    def _projected_triangles(self, width, height):
        if self._scene is None:
            return []
        minimum, maximum = self._scene["bounds"]
        center = tuple((minimum[index] + maximum[index]) * 0.5
                       for index in range(3))
        radius = max(maximum[index] - minimum[index] for index in range(3)) * 0.5
        radius = max(radius, 0.001)
        scale = min(width, height) * 0.43 * self._zoom / radius
        cosine_yaw, sine_yaw = math.cos(self._yaw), math.sin(self._yaw)
        cosine_pitch, sine_pitch = math.cos(self._pitch), math.sin(self._pitch)

        def project(point):
            x = point[0] - center[0]
            y = point[1] - center[1]
            z = point[2] - center[2]
            rotated_x = cosine_yaw * x + sine_yaw * z
            rotated_z = -sine_yaw * x + cosine_yaw * z
            rotated_y = cosine_pitch * y - sine_pitch * rotated_z
            depth = sine_pitch * y + cosine_pitch * rotated_z
            return (width * 0.5 + rotated_x * scale,
                    height * 0.52 - rotated_y * scale, depth)

        result = []
        for part in self._scene["parts"]:
            for surface in part["surfaces"]:
                for triangle in surface["triangles"]:
                    projected = tuple(project(point) for point in triangle)
                    area = ((projected[1][0] - projected[0][0]) *
                            (projected[2][1] - projected[0][1]) -
                            (projected[1][1] - projected[0][1]) *
                            (projected[2][0] - projected[0][0]))
                    if abs(area) < 0.05:
                        continue
                    result.append((
                        sum(point[2] for point in projected) / 3.0,
                        projected, surface, area))
        result.sort(key=lambda item: item[0])
        return result

    def _redraw(self, unused_event=None):
        width = max(10, int(self.canvas.winfo_width()))
        height = max(10, int(self.canvas.winfo_height()))
        self.canvas.delete("all")
        self._item_surface = {}
        if self._scene is None:
            self.canvas.create_text(
                width / 2, height / 2, text=self.selection.get(),
                fill="#c8ced8", width=max(120, width - 48), justify="center")
            return
        for unused_depth, projected, surface, area in self._projected_triangles(
                width, height):
            key = surface.get("fieldKey")
            focused = key is not None and key == self._focus_key
            base = thickness_color(surface.get("thickness"))
            fill = _shade(base, 0.22 if area < 0 else -0.08)
            if focused:
                fill = _shade(base, 0.34)
            coords = [coordinate for point in projected
                      for coordinate in point[:2]]
            item = self.canvas.create_polygon(
                coords, fill=fill,
                outline="#ffffff" if focused else "#242a34",
                width=2 if focused else 1)
            self._item_surface[item] = surface


class NullArmorViewerPanel(object):
    """Test/headless fallback preserving the editor interaction contract."""

    def __init__(self, *unused_args, **unused_kwargs):
        self.loaded = []
        self.focused = []
        self.updated = []

    def grid(self, *unused_args, **unused_kwargs):
        return None

    def load_vehicle(self, vehicle_member, records, focus_record=None):
        self.loaded.append((vehicle_member, list(records), focus_record))

    def focus_field(self, record, reload_scene=True):
        self.focused.append((record, reload_scene))
        return _focus_identity(record) is not None

    def update_field(self, member, field_path, current_value):
        self.updated.append((member, field_path, current_value))
        return True

    def reset_values(self):
        return None
