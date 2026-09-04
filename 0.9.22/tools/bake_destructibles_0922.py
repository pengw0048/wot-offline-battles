#!/usr/bin/env python3
"""Bake exact #1513 crushable collision bounds for every map.

The client exposes a destructible's chunk-local matrix at runtime, but not its
compiled collision bounds.  This tool joins the pinned ``destructibles.xml``
descriptors to falling-atom and type-2 BSMO colliders in each compiled
``space.bin``.  The result is a resource-level catalog: runtime keeps the
native chunk/item index as authority and transforms these local boxes only as
a contact sensor.

All joins are strict.  Missing descriptors, kind conflicts, invalid material
kinds, malformed bounds, or ambiguous instance locators abort the bake.
"""

import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import zipfile


TOOL_ROOT = os.path.dirname(os.path.abspath(__file__))
PORT_ROOT = os.path.dirname(TOOL_ROOT)
SCHEMA_ROOT = os.path.join(
    PORT_ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'offline_lan_0922')
VENDOR_ROOT = os.path.join(TOOL_ROOT, 'vendor')
for path in (TOOL_ROOT, SCHEMA_ROOT, VENDOR_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from packed_xml import TYPE_ELEMENT, TYPE_STRING, read_packed_xml
from wot_space_bin_utils import CompiledSpace
import navigation_graph_schema


FORMAT_NAME = 'offline-lan-0922-destructible-catalog'
FORMAT_VERSION = 7
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
GAME_VERSION = '0.9.22.0.1-cn-1513'
DECODER_VERSION = '0.9.22.0.1'
DECODER_REGION = 'RU'
SUPPORTED_MAPS = navigation_graph_schema.SUPPORTED_MAPS
DEFAULT_OUTPUT_ROOT = os.path.join(PORT_ROOT, 'destructibles')
NORMAL_MATERIAL_KIND_MIN = 73
LOCATOR_QUANTIZATION = 1000

MODEL_TYPE_FALLING = 1
MODEL_TYPE_DESTRUCTIBLE = 2
ENTRY_TYPE_FRAGILE = 0
ENTRY_TYPE_STRUCTURE = 1
SPTR_INDEX_BIT = 0x80000000


def _children(element, name):
    encoded = name.encode('ascii')
    return [value for child_name, value in element.children
            if child_name == encoded]


def _element(element, name):
    values = _children(element, name)
    if len(values) != 1 or values[0].value_type != TYPE_ELEMENT:
        raise ValueError('destructibles.xml requires one element %s' % name)
    return values[0].value


def _string(element, name):
    values = _children(element, name)
    if len(values) != 1 or values[0].value_type != TYPE_STRING:
        raise ValueError('destructibles.xml requires one string %s' % name)
    return values[0].value.decode('utf-8')


def normalize_model_filename(value):
    """Normalize only VFS separators; preserve client path case."""
    normalized = str(value).replace('\\', '/').strip()
    if (not normalized or normalized.startswith('/') or '..' in
            normalized.split('/') or not normalized.lower().endswith('.model')):
        raise ValueError('invalid destructible model filename: %r' % value)
    return normalized


def model_filename_from_primitive(value):
    normalized = str(value).replace('\\', '/').strip()
    suffix = '.primitives'
    if not normalized.lower().endswith(suffix):
        raise ValueError('BSMO collider is not a primitives resource: %r' % value)
    model = normalized[:-len(suffix)] + '.model'
    # Compiled type-2 collision may select a lower-detail visual, while the
    # native destructible cache always keys the corresponding descriptor by
    # its normal/lod0 model.  Change only that exact path segment, then require
    # an exact descriptor join below; no basename or fuzzy fallback is used.
    model = re.sub(r'(?i)(/normal/)lod[0-9]+/', r'\1lod0/', model)
    return normalize_model_filename(model)


def parse_descriptors(data):
    """Return exact falling/fragile/structure descriptors by filename."""
    root = read_packed_xml(data)
    result = {}
    casefold = {}
    for section_name, kind in (('fallingAtoms', 'falling'),
                               ('fragiles', 'fragile'),
                               ('structures', 'structure')):
        section = _element(root, section_name)
        for child_name, value in section.children:
            if child_name != b'entry' or value.value_type != TYPE_ELEMENT:
                raise ValueError('invalid %s descriptor entry' % section_name)
            entry = value.value
            filename = normalize_model_filename(_string(entry, 'filename'))
            folded = filename.lower()
            if filename in result or (folded in casefold and
                                      casefold[folded] != filename):
                raise ValueError('duplicate destructible descriptor %s' % filename)
            modules = ()
            if kind == 'structure':
                module_names = []
                for module_name, module_value in _element(
                        entry, 'modules').children:
                    if (module_name != b'module' or
                            module_value.value_type != TYPE_ELEMENT):
                        raise ValueError('invalid structure module in %s' % filename)
                    module_names.append(_string(module_value.value, 'matName'))
                if not module_names or len(set(module_names)) != len(module_names):
                    raise ValueError('invalid structure modules in %s' % filename)
                # Client DestructiblesCache assigns NORMAL matKinds in sorted
                # module-ID order, beginning at 73.
                modules = tuple(sorted(module_names))
            result[filename] = {'kind': kind, 'modules': modules}
            casefold[folded] = filename
    if not result:
        raise ValueError('destructibles.xml contains no descriptors')
    return result


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _rounded_bounds(collider, model_id):
    values = (tuple(collider['collision_bounds_min']) +
              tuple(collider['collision_bounds_max']))
    if (len(values) != 6 or not all(math.isfinite(float(value))
                                    for value in values)):
        raise ValueError('BSMO model %d has non-finite collision bounds' % model_id)
    if any(float(values[index]) > float(values[index + 3])
           for index in range(3)):
        raise ValueError('BSMO model %d has inverted collision bounds' % model_id)
    if all(abs(float(values[index]) - float(values[index + 3])) <= 1e-9
           for index in range(3)):
        raise ValueError('BSMO model %d has empty collision bounds' % model_id)
    return tuple(round(float(value), 6) for value in values)


def _material_kinds(bsmo, collider, model_id):
    materials = bsmo['bsp_material_kinds']
    first = int(collider['bsp_material_kind_begin'])
    last = int(collider['bsp_material_kind_end'])
    if first > last or first < 0 or last >= len(materials):
        raise ValueError('BSMO model %d has an invalid material range' % model_id)
    return tuple(int(record['flags']) >> 8
                 for record in materials[first:last + 1])


def _quantize_locator_value(value):
    """Round one transform component symmetrically to the locator grid."""
    scaled = float(value) * LOCATOR_QUANTIZATION
    if not math.isfinite(scaled):
        raise ValueError('BSMI transform has a non-finite component')
    if scaled >= 0.0:
        return int(math.floor(scaled + 0.5))
    return int(math.ceil(scaled - 0.5))


def _locator_signature(transform):
    """Return quantized world origin and transformed unit-basis deltas.

    BSMI stores a column-major world transform.  The pinned runtime exposes
    the same transform split into a chunk translation and chunk-local matrix,
    so these values are reconstructible without treating the global BSMI
    array position as a native chunk item index.
    """
    if len(transform) != 16:
        raise ValueError('BSMI transform is not a 4x4 matrix')
    components = (
        transform[12], transform[13], transform[14],
        transform[0], transform[1], transform[2],
        transform[4], transform[5], transform[6],
        transform[8], transform[9], transform[10],
    )
    return tuple(_quantize_locator_value(value) for value in components)


def _record_locator(located, signature, box_index, filename):
    previous = located.get(signature)
    if previous is not None and previous != box_index:
        raise ValueError(
            'destructible locator maps one transform to multiple boxes: %s' %
            filename)
    located[signature] = box_index


def _candidate_sort_key(candidate):
    filename, box_index = candidate
    return filename, -1 if box_index is None else int(box_index)


def _item_scale(transform):
    """Return the runtime AreaDestructibles scale: the Y-basis length."""
    y_axis = (float(transform[4]), float(transform[5]), float(transform[6]))
    scale = (y_axis[0] * y_axis[0] + y_axis[1] * y_axis[1] +
             y_axis[2] * y_axis[2]) ** 0.5
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError('BSMI transform has an invalid item scale')
    return scale


def native_wires(compiled, bsmi_count):
    """Map referenced scene rows to native ``(chunk_id, item_index)`` wires.

    Exact pinned #1513 enumeration contract, one rule per WGDE row class:

    * Table "1" rows are ``(chunk_id, global_item_begin, item_count)``.  Sorted
      by ``global_item_begin`` they must partition table "2" from zero, and no
      chunk id may repeat.  File order is not significant, and the native item
      index restarts at zero inside every chunk, so a streamed chunk produces
      the same wires as the same chunk loaded at battle start.
    * Table "2" rows are inclusive reference ranges ``[ref_begin, ref_end]``
      into table "3" and must partition it from zero.  ``ref_end ==
      ref_begin - 1`` is an authored empty row that references no scene
      instance.
    * An empty table "2" row does NOT consume a streamed native item index.
      The provider enumerates only rows that reference at least one scene
      instance, so the native item index of a row is the number of non-empty
      rows before it inside its chunk.
    * A table "3" reference selects an SpTr row when bit ``0x80000000`` is set
      and a BSMI row otherwise.  Both classes are scene instances and consume
      the same item index space: a SpeedTree row advances the index exactly
      like a BSMI row.
    * Several references in one row share one native item; that is how the
      separate BSMO module rows of a structure collapse to one identity.  A
      single reference may never appear in two rows.
    * An ignored BSMO entry type (a type-2/type-3 authored effect) is still a
      referenced scene instance, so its row consumes an item index even though
      ``bake_compiled_map`` never makes it crushable.

    The empty-row rule is the one class that pinned resource data alone cannot
    settle, because a compiled empty row is indistinguishable from a row the
    provider skipped.  It is fixed by exact live #1513 evidence: three separate
    reports recovered a live destructible matrix whose quantized locator
    signature is unique in the baked catalog, and in every case the live item
    index equalled the non-empty-row count and not the table-2 position:

    * ``11_murovanka`` chunk 32124 ``gaf001_WoodFence`` live item 7;
    * ``18_cliff`` chunk 32893 ``bld704_shed`` live item 6;
    * ``34_redshire`` chunk 33148 ``env422_WallLamp`` live item 58.

    Counting the empty rows shifted exactly those three slots to 8, 7 and 59.
    Empty rows exist in only six standard maps, and every reported live wire
    mismatch came from three of those six; the other 35 maps are unaffected by
    this rule and reported no wire mismatch.
    """
    wgde = compiled.sections['WGDE']._data
    table1 = wgde.get('1')
    table2 = wgde.get('2')
    table3 = wgde.get('3')
    if not table1 or not isinstance(table2, list) or \
            not isinstance(table3, list):
        raise ValueError('WGDE destructible tables are unavailable')
    sptr_count = len(compiled.sections['SpTr']._data['speedtree_list'])
    chunk_ranges = sorted((int(begin), int(count), int(chunk_id))
                          for chunk_id, begin, count in table1)
    item_cursor = 0
    seen_chunk_ids = set()
    for begin, count, chunk_id in chunk_ranges:
        if begin != item_cursor or count < 0 or chunk_id in seen_chunk_ids:
            raise ValueError('WGDE chunk item ranges are not contiguous')
        seen_chunk_ids.add(chunk_id)
        item_cursor += count
    if item_cursor != len(table2):
        raise ValueError('WGDE chunk item ranges do not cover the item table')
    # Resolve every reference span before assigning wires so that "empty row"
    # has exactly one definition for both the index and the reference walk.
    spans = []
    ref_cursor = 0
    for ref_begin, ref_end in table2:
        ref_begin = int(ref_begin)
        ref_end = int(ref_end)
        if ref_begin != ref_cursor:
            raise ValueError('WGDE item references are not contiguous')
        if ref_begin > ref_end:
            if ref_end != ref_cursor - 1:
                raise ValueError('WGDE empty item span is invalid')
            spans.append(None)
            continue
        spans.append((ref_begin, ref_end))
        ref_cursor = ref_end + 1
    if ref_cursor != len(table3):
        raise ValueError('WGDE item spans do not cover the reference table')
    item_wires = [None] * len(table2)
    for begin, count, chunk_id in chunk_ranges:
        native_index = 0
        for position in range(begin, begin + count):
            if spans[position] is None:
                continue
            item_wires[position] = (chunk_id, native_index)
            native_index += 1
    row_wires = {}
    wire_rows = {}
    speedtree_wires = {}
    for position, span in enumerate(spans):
        if span is None:
            continue
        ref_begin, ref_end = span
        wire = item_wires[position]
        rows = set()
        for ref_index in range(ref_begin, ref_end + 1):
            ref = int(table3[ref_index])
            if ref & SPTR_INDEX_BIT:
                index = ref & ~SPTR_INDEX_BIT
                if not 0 <= index < sptr_count or index in speedtree_wires:
                    raise ValueError('WGDE SpTr reference is invalid')
                speedtree_wires[index] = wire
            else:
                if not 0 <= ref < bsmi_count or ref in row_wires:
                    raise ValueError('WGDE BSMI reference is invalid')
                row_wires[ref] = wire
                rows.add(ref)
        if rows:
            wire_rows[wire] = frozenset(rows)
    return row_wires, wire_rows, speedtree_wires


def _native_wires(compiled, bsmi_count):
    """Backward-compatible BSMI-only view used by this catalog baker."""
    row_wires, wire_rows, unused_speedtree_wires = native_wires(
        compiled, bsmi_count)
    return row_wires, wire_rows


def bake_compiled_map(map_name, map_package_data, space_data,
                      destructibles_data, descriptors=None):
    """Join one decoded map to descriptors; pure apart from binary decoding."""
    if descriptors is None:
        descriptors = parse_descriptors(destructibles_data)
    compiled = CompiledSpace(io.BytesIO(space_data), DECODER_VERSION,
                             DECODER_REGION,
                             ['BWST', 'BSMI', 'BSMO', 'WGDE', 'SpTr'])
    missing = [name for name in ('BWST', 'BSMI', 'BSMO', 'WGDE', 'SpTr')
               if name not in compiled.sections]
    if missing:
        raise ValueError('compiled space omitted %s' % ', '.join(missing))
    strings = compiled.sections['BWST']
    bsmo = compiled.sections['BSMO']._data
    bsmi = compiled.sections['BSMI']
    model_ids = list(bsmi.model_ids())
    if len(model_ids) != len(bsmi._data['transforms']):
        raise ValueError('BSMI model ids do not match transforms')
    row_wires, wire_rows = _native_wires(compiled, len(model_ids))
    instance_counts = {}
    instances_by_model = {}
    for model_id, transform in zip(model_ids, bsmi._data['transforms']):
        instance_counts[model_id] = instance_counts.get(model_id, 0) + 1
        instances_by_model.setdefault(model_id, []).append(transform)

    raw_resources = {}
    instance_model_resources = {}
    type1_models = 0
    type2_models = 0
    ignored_entry_types = 0
    for model_id, model_info in enumerate(bsmo['model_info_items']):
        model_type = int(model_info['type'])
        if model_type == MODEL_TYPE_FALLING:
            type1_models += 1
            info_index = int(model_info['info_index'])
            falling_infos = bsmo['falling_model_info_items']
            if info_index < 0 or info_index >= len(falling_infos):
                raise ValueError(
                    'BSMO model %d has an invalid falling info index' %
                    model_id)
            kind = 'falling'
        elif model_type == MODEL_TYPE_DESTRUCTIBLE:
            type2_models += 1
            info_index = int(model_info['info_index'])
            fragile_infos = bsmo['fragile_model_info_items']
            if info_index < 0 or info_index >= len(fragile_infos):
                raise ValueError(
                    'BSMO model %d has an invalid fragile info index' %
                    model_id)
            entry_type = int(fragile_infos[info_index]['entry_type'])
            if entry_type not in (ENTRY_TYPE_FRAGILE, ENTRY_TYPE_STRUCTURE):
                # Types 2/3 are authored client effects outside the ram-contact
                # descriptor contract. Record them, but never make them crushable.
                ignored_entry_types += 1
                continue
            kind = ('fragile' if entry_type == ENTRY_TYPE_FRAGILE
                    else 'structure')
        else:
            continue
        collider = bsmo['models_colliders'][model_id]
        primitive = strings.get(collider['bsp_section_name_fnv'])
        if not primitive:
            raise ValueError('BSMO model %d has no collider resource' % model_id)
        filename = model_filename_from_primitive(primitive)
        descriptor = descriptors.get(filename)
        if descriptor is None:
            raise ValueError('BSMO model %d has no descriptor: %s' %
                             (model_id, filename))
        if descriptor['kind'] != kind:
            raise ValueError('BSMO/descriptor kind conflict for %s' % filename)
        bounds = _rounded_bounds(collider, model_id)
        material_kinds = _material_kinds(bsmo, collider, model_id)
        if kind != 'structure':
            rows = (bounds + (None,),)
        else:
            allowed = tuple(range(
                NORMAL_MATERIAL_KIND_MIN,
                NORMAL_MATERIAL_KIND_MIN + len(descriptor['modules'])))
            unique_material_kinds = tuple(sorted(set(material_kinds)))
            if (len(unique_material_kinds) != 1 or
                    unique_material_kinds[0] not in allowed):
                raise ValueError(
                    'structure BSMO model %d has invalid matKinds %r for %s' %
                    (model_id, material_kinds, filename))
            rows = (bounds + (unique_material_kinds[0],),)
        if instance_counts.get(model_id, 0) <= 0:
            # A descriptor can define more modules than this compiled map
            # instantiates.  Such BSMO templates are not native destructible
            # items in this space and must not add unreachable contact boxes.
            continue
        resource = raw_resources.setdefault(filename, {
            'kind': kind, 'boxes': set(), 'bsmo_model_ids': [],
            'instance_count': 0, 'model_boxes': {},
        })
        if resource['kind'] != kind:
            raise ValueError('map-local kind conflict for %s' % filename)
        resource['boxes'].update(rows)
        resource['bsmo_model_ids'].append(model_id)
        resource['instance_count'] += instance_counts.get(model_id, 0)
        resource['model_boxes'][model_id] = rows[0]
        instance_model_resources[model_id] = (filename, kind)

    resources = {}
    kinds = ('falling', 'fragile', 'structure')
    kind_resources = dict((kind, 0) for kind in kinds)
    kind_boxes = dict((kind, 0) for kind in kinds)
    kind_instances = dict((kind, 0) for kind in kinds)
    variant_resources = 0
    locator_resources = 0
    locators = 0
    falling_locator_resources = 0
    falling_locators = 0
    fragile_locator_resources = 0
    fragile_locators = 0
    max_boxes = 0
    resource_box_indexes = {}
    for filename in sorted(raw_resources):
        raw = raw_resources[filename]
        boxes = sorted(raw['boxes'], key=lambda row: (
            -1 if row[6] is None else int(row[6]), row[:6]))
        resource_box_indexes[filename] = dict(
            (box, index) for index, box in enumerate(boxes))
        count = int(raw['instance_count'])
        record = {
            'kind': raw['kind'],
            'boxes': [list(row) for row in boxes],
            'bsmo_model_ids': sorted(raw['bsmo_model_ids']),
            'instance_count': count,
        }
        if raw['kind'] != 'structure' and len(boxes) > 1:
            box_indexes = dict((box, index)
                               for index, box in enumerate(boxes))
            located = {}
            for model_id in raw['bsmo_model_ids']:
                box_index = box_indexes[raw['model_boxes'][model_id]]
                for transform in instances_by_model.get(model_id, ()):
                    signature = _locator_signature(transform)
                    _record_locator(
                        located, signature, box_index, filename)
            if not located:
                raise ValueError(
                    'destructible resource has no instance locators: %s' %
                    filename)
            record['locators'] = [
                list(signature) + [located[signature]]
                for signature in sorted(located)]
            locator_resources += 1
            locators += len(located)
            if raw['kind'] == 'fragile':
                fragile_locator_resources += 1
                fragile_locators += len(located)
            else:
                falling_locator_resources += 1
                falling_locators += len(located)
        resources[filename] = record
        max_boxes = max(max_boxes, len(boxes))
        if len(boxes) > 1:
            variant_resources += 1
        kind_resources[raw['kind']] += 1
        kind_boxes[raw['kind']] += len(boxes)
        kind_instances[raw['kind']] += count
    if not resources:
        raise ValueError('map %s has no crushable descriptors' % map_name)

    # The native offline filename registry leaves compiled type-1/type-2 slots
    # blank.  Bake a map-level identity index from the BSMI world transform so
    # runtime can recover the canonical descriptor without treating the global
    # BSMI order as a native chunk item index.  Structure modules are separate
    # BSMO rows at the same transform and intentionally collapse to one native
    # structure identity.  Any transform that still names more than one
    # resource or non-structure collider variant is kept explicitly ambiguous
    # and is never eligible for runtime destruction.
    instance_candidates = {}
    instance_rows = {}
    for row_index, (model_id, transform) in enumerate(
            zip(model_ids, bsmi._data['transforms'])):
        model_resource = instance_model_resources.get(model_id)
        if model_resource is None:
            continue
        filename, kind = model_resource
        box_index = None
        if kind != 'structure':
            model_box = raw_resources[filename]['model_boxes'][model_id]
            box_index = resource_box_indexes[filename][model_box]
        signature = _locator_signature(transform)
        instance_candidates.setdefault(signature, []).append(
            (filename, box_index, kind, model_id))
        rows = instance_rows.setdefault(
            signature, {'rows': set(), 'scales': set()})
        rows['rows'].add(row_index)
        rows['scales'].add(_item_scale(transform))

    instances = []
    ambiguous_instances = []
    emitted_wires = set()
    instance_kind_signatures = dict((kind, 0) for kind in kinds)
    ambiguous_instance_candidates = 0
    for signature in sorted(instance_candidates):
        rows_by_candidate = {}
        for filename, box_index, kind, model_id in instance_candidates[
                signature]:
            rows_by_candidate.setdefault(
                (filename, box_index, kind), []).append(model_id)
        candidates = []
        for (filename, box_index, kind), candidate_model_ids in sorted(
                rows_by_candidate.items(),
                key=lambda item: _candidate_sort_key(item[0][:2])):
            if kind == 'structure':
                expected_model_ids = set(
                    raw_resources[filename]['bsmo_model_ids'])
                model_counts = {}
                for model_id in candidate_model_ids:
                    model_counts[model_id] = model_counts.get(model_id, 0) + 1
                if set(model_counts) != expected_model_ids:
                    raise ValueError(
                        'structure instance omits compiled modules: %s' %
                        filename)
                multiplicities = set(model_counts.values())
                if len(multiplicities) != 1:
                    raise ValueError(
                        'structure instance module multiplicity differs: %s' %
                        filename)
                multiplicity = next(iter(multiplicities))
            else:
                multiplicity = len(candidate_model_ids)
            candidates.extend([(filename, box_index)] * multiplicity)
        candidates.sort(key=_candidate_sort_key)
        if len(candidates) == 1:
            filename, box_index = candidates[0]
            rows = instance_rows[signature]
            wires = set(row_wires.get(row) for row in rows['rows'])
            if None in wires or len(wires) != 1:
                raise ValueError(
                    'destructible instance does not resolve to one native '
                    'item: %s' % filename)
            wire = wires.pop()
            if wire_rows[wire] != frozenset(rows['rows']):
                raise ValueError(
                    'native item references rows outside its instance: %s' %
                    filename)
            if wire in emitted_wires:
                raise ValueError(
                    'native item is claimed by two instances: %s' % filename)
            emitted_wires.add(wire)
            if len(rows['scales']) != 1:
                raise ValueError(
                    'destructible instance scales disagree: %s' % filename)
            instances.append(list(signature) + [
                filename, box_index, wire[0], wire[1],
                next(iter(rows['scales']))])
            instance_kind_signatures[resources[filename]['kind']] += 1
        else:
            ambiguous_instances.append(
                list(signature) + [[[filename, box_index]
                    for filename, box_index in candidates]])
            ambiguous_instance_candidates += len(candidates)

    return {
        'format': FORMAT_NAME,
        'version': FORMAT_VERSION,
        'game_version': GAME_VERSION,
        'map': map_name,
        'locator_quantization': LOCATOR_QUANTIZATION,
        'source': {
            'map_package_sha256': _sha256_bytes(map_package_data),
            'space_bin_sha256': _sha256_bytes(space_data),
            'destructibles_xml_sha256': _sha256_bytes(destructibles_data),
        },
        'resources': resources,
        'instances': instances,
        'ambiguous_instances': ambiguous_instances,
        'census': {
            'source_type1_models': type1_models,
            'source_type2_models': type2_models,
            'ignored_entry_types': ignored_entry_types,
            'resources': len(resources),
            'falling_resources': kind_resources['falling'],
            'fragile_resources': kind_resources['fragile'],
            'structure_resources': kind_resources['structure'],
            'boxes': sum(kind_boxes.values()),
            'falling_boxes': kind_boxes['falling'],
            'fragile_boxes': kind_boxes['fragile'],
            'structure_boxes': kind_boxes['structure'],
            'instances': sum(kind_instances.values()),
            'falling_instances': kind_instances['falling'],
            'fragile_instances': kind_instances['fragile'],
            'structure_instances': kind_instances['structure'],
            'variant_resources': variant_resources,
            'locator_resources': locator_resources,
            'locators': locators,
            'falling_locator_resources': falling_locator_resources,
            'falling_locators': falling_locators,
            'fragile_locator_resources': fragile_locator_resources,
            'fragile_locators': fragile_locators,
            'max_boxes_per_resource': max_boxes,
            'instance_signatures': len(instances),
            'falling_instance_signatures': instance_kind_signatures['falling'],
            'fragile_instance_signatures': instance_kind_signatures['fragile'],
            'structure_instance_signatures': instance_kind_signatures[
                'structure'],
            'ambiguous_instance_signatures': len(ambiguous_instances),
            'ambiguous_instance_candidates': ambiguous_instance_candidates,
        },
    }


def _client_inputs(client_root, map_name):
    packages = os.path.join(os.path.abspath(client_root), 'res', 'packages')
    scripts_path = os.path.join(packages, 'scripts.pkg')
    map_path = os.path.join(packages, map_name + '.pkg')
    for path in (scripts_path, map_path):
        if not os.path.isfile(path):
            raise ValueError('required client package not found: %s' % path)
    return scripts_path, map_path


def read_destructibles(client_root):
    scripts_path, unused_map = _client_inputs(client_root, SUPPORTED_MAPS[0])
    with zipfile.ZipFile(scripts_path, 'r') as package:
        try:
            return package.read('scripts/destructibles.xml')
        except KeyError:
            raise ValueError('destructibles.xml missing from scripts.pkg')


def bake_map(client_root, map_name, destructibles_data=None,
             descriptors=None):
    if map_name not in SUPPORTED_MAPS:
        raise ValueError('unsupported standard map: %s' % map_name)
    unused_scripts, map_path = _client_inputs(client_root, map_name)
    if destructibles_data is None:
        destructibles_data = read_destructibles(client_root)
    if descriptors is None:
        descriptors = parse_descriptors(destructibles_data)
    with open(map_path, 'rb') as handle:
        map_package_data = handle.read()
    space_member = 'spaces/%s/space.bin' % map_name
    with zipfile.ZipFile(io.BytesIO(map_package_data), 'r') as package:
        try:
            space_data = package.read(space_member)
        except KeyError:
            raise ValueError('compiled space missing: %s' % space_member)
    return bake_compiled_map(map_name, map_package_data, space_data,
                             destructibles_data, descriptors)


def write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8', newline='\n') as output:
        json.dump(data, output, sort_keys=True, separators=(',', ':'))
        output.write('\n')
    os.replace(temporary, path)


def _aggregate_census(data_by_map):
    keys = ('resources', 'falling_resources', 'fragile_resources',
            'structure_resources', 'boxes', 'falling_boxes',
            'fragile_boxes', 'structure_boxes', 'instances',
            'falling_instances', 'fragile_instances', 'structure_instances',
            'variant_resources', 'locator_resources', 'locators',
            'falling_locator_resources', 'falling_locators',
            'fragile_locator_resources', 'fragile_locators',
            'instance_signatures', 'falling_instance_signatures',
            'fragile_instance_signatures', 'structure_instance_signatures',
            'ambiguous_instance_signatures',
            'ambiguous_instance_candidates')
    result = dict((key, sum(data['census'][key]
                            for data in data_by_map.values())) for key in keys)
    result['maps'] = len(data_by_map)
    result['max_boxes_per_resource'] = max(
        data['census']['max_boxes_per_resource']
        for data in data_by_map.values())
    return result


def _write_manifest(output_root, data_by_map, digests,
                    destructibles_sha256):
    records = []
    for map_name in SUPPORTED_MAPS:
        records.append({
            'map': map_name,
            'file': map_name + '.json',
            'sha256': digests[map_name],
            'map_package_sha256': data_by_map[map_name][
                'source']['map_package_sha256'],
            'space_bin_sha256': data_by_map[map_name][
                'source']['space_bin_sha256'],
        })
    write_json(os.path.join(output_root, 'manifest.json'), {
        'format': MANIFEST_FORMAT,
        'version': FORMAT_VERSION,
        'game_version': GAME_VERSION,
        'locator_quantization': LOCATOR_QUANTIZATION,
        'destructibles_xml_sha256': destructibles_sha256,
        'census': _aggregate_census(data_by_map),
        'maps': records,
    })


def bake_all(client_root, output_root=DEFAULT_OUTPUT_ROOT):
    """Bake all supported maps, then publish maps first and manifest last."""
    output_root = os.path.abspath(output_root)
    parent = os.path.dirname(output_root)
    if not os.path.isdir(parent):
        raise ValueError('destructible output parent does not exist: %s' % parent)
    if not os.path.isdir(output_root):
        os.makedirs(output_root)
    expected = set(map_name + '.json' for map_name in SUPPORTED_MAPS)
    actual = set(name for name in os.listdir(output_root)
                 if name.endswith('.json') and name != 'manifest.json')
    if actual and actual != expected:
        raise ValueError('existing destructible output set is incomplete or extra')
    destructibles_data = read_destructibles(client_root)
    descriptors = parse_descriptors(destructibles_data)
    with tempfile.TemporaryDirectory(
            prefix='offline-lan-0922-destructibles-', dir=parent) as staging:
        data_by_map = {}
        digests = {}
        for map_name in SUPPORTED_MAPS:
            data = bake_map(client_root, map_name, destructibles_data,
                            descriptors)
            path = os.path.join(staging, map_name + '.json')
            write_json(path, data)
            data_by_map[map_name] = data
            digests[map_name] = _sha256_file(path)
            print('baked %s: %d resources, %d boxes, %d instances' % (
                map_name, data['census']['resources'],
                data['census']['boxes'], data['census']['instances']),
                flush=True)
        if set(data_by_map) != set(SUPPORTED_MAPS):
            raise ValueError('destructible batch did not produce every map')
        _write_manifest(staging, data_by_map, digests,
                        _sha256_bytes(destructibles_data))
        for map_name in SUPPORTED_MAPS:
            os.replace(os.path.join(staging, map_name + '.json'),
                       os.path.join(output_root, map_name + '.json'))
        os.replace(os.path.join(staging, 'manifest.json'),
                   os.path.join(output_root, 'manifest.json'))
    return digests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True,
                        help='Pinned #1513 client root')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--map', choices=SUPPORTED_MAPS)
    selection.add_argument('--all', action='store_true')
    parser.add_argument('--output', help='Single-map output JSON path')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_ROOT,
                        help='Complete batch destination')
    args = parser.parse_args(argv)
    if args.all and args.output:
        parser.error('--output can only be used with one --map')
    try:
        if args.all:
            digests = bake_all(args.client, args.output_dir)
            print('validated destructible batch: %d standard maps' %
                  len(digests))
        else:
            map_name = args.map or '06_ensk'
            data = bake_map(args.client, map_name)
            output = args.output or os.path.join(
                args.output_dir, map_name + '.json')
            write_json(output, data)
            print('baked %s: %d resources, %d boxes, %d instances' % (
                map_name, data['census']['resources'],
                data['census']['boxes'], data['census']['instances']))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print('FAILED destructible bake: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
