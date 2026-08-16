"""Destructible sensing, taken from the 0.9.22 port.

The law is unchanged. Version differences belong in the
adapters in this package, never in this file.

Contract, from the original module:
0.8.2 destructible contact sensors on the 2.3.1.2 engine boundary.

The three sensor bodies below are dedented copies from ``offline_battle.py``.
Only their former closure dependencies are supplied at module scope.
"""
from __future__ import absolute_import
# -*- coding: utf-8 -*-

_event_sink = None

_DESTRUCTIBLE_BIN_METRES = 8.0
_DESTRUCTIBLE_ORIGIN_RADIUS = 8.0
_DESTRUCTIBLE_CHUNK_METRES_1513 = 100.0
_SOLID_CONTACT_RADIUS_1513 = 0.5
_SOLID_CONTACT_NORMAL_DOT_1513 = 0.5
_CATALOG_POINT_EPSILON = 0.075
_SHOT_RAY_EPSILON = 1.0e-4
_SOFT_STATIC_MAX_SKIPS = 4
_NATIVE_HIDE_MIN_SECONDS = 0.2
_FALLING_REFRESH_SECONDS = 1.0 / 60.0
_DIAGNOSTICS_ENABLED = True
_DIAGNOSTIC_EMIT_SECONDS = 0.25
_DIAGNOSTIC_CHUNK_LIMIT = 24
_DIAGNOSTIC_PENDING_LIMIT = 4
_DIAGNOSTIC_CONTACT_LIMIT = 32
_destructible_catalog = None
_diagnostic_writer = None

# Server-side values pinned by 2.3.1.2 ``scripts/destructibles.xml``.  The
# release client does not load these fields outside development builds.  Each
# listed effect material uses factor=0/minimum=25, hence max(P*0, 25)=25 mm.
_SHOT_THROUGH_MAX_HP_1513 = 19.0
_SHOT_THROUGH_MIN_REDUCTION_1513 = 25.0
_SHOT_AP_KINDS_1513 = frozenset((
    'ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)


def _normalized_filename(filename):
    if not isinstance(filename, _STRING_TYPES):
        return None
    return filename.replace('\\', '/').strip().lower()


def _native_chunk_destructible_count_1513(manager, chunk_id):
    """Read the count written by ``game.wg_onChunkLoad`` on pinned 2.3.1.2.

    ``wg_getChunkDestrFilenames`` is not a slot-count API: the offline client can
    return only the named SpeedTree prefix while fragile, structure and falling
    atoms remain addressable at later native indices.  The manager's private map
    is populated from the engine callback's ``numDestructibles`` argument and is
    the only exact streamed-slot boundary available to Python.
    """
    loaded = getattr(
        manager, '_DestructiblesManager__loadedChunkIDs', None)
    if not isinstance(loaded, dict):
        raise RuntimeError(
            '2.3.1.2 destructibles manager loaded-count ABI is unavailable')
    chunk_id = int(chunk_id)
    if chunk_id not in loaded:
        return None
    count = loaded[chunk_id]
    if (isinstance(count, bool) or not isinstance(count, _INTEGER_TYPES) or
            count < 0):
        raise RuntimeError(
            '2.3.1.2 native destructible count is invalid: chunk=%s value=%r' %
            (chunk_id, count))
    return int(count)


def set_diagnostics(enabled, writer=None):
    """Enable bounded 2.3.1.2 destructible diagnostics for measurement builds."""
    global _DIAGNOSTICS_ENABLED, _diagnostic_writer
    _DIAGNOSTICS_ENABLED = bool(enabled)
    _diagnostic_writer = writer
    globals().pop('g_offh_destr_diagnostics', None)


def _diagnostic_time_1513():
    try:
        import BigWorld
        return float(BigWorld.time())
    except (AttributeError, ImportError, TypeError, ValueError):
        return 0.0


def _diagnostic_flush_1513(now=None):
    if not _DIAGNOSTICS_ENABLED:
        return
    state = globals().get('g_offh_destr_diagnostics')
    if not state or not state['queue']:
        return
    if now is None:
        now = _diagnostic_time_1513()
    if float(now) < state['next_emit']:
        return
    line = state['queue'].pop(0)
    try:
        writer = _diagnostic_writer
        if writer is None:
            import sys
            writer = sys.stdout.write
        writer('[Offline LAN 0.9.22] DESTR %s\n' % line)
    except Exception:
        # Diagnostics are observational.  A closed stdout stream must not change
        # movement, destruction or authority state.
        state['queue'] = []
        state['disabled'] = True
        return
    state['next_emit'] = float(now) + _DIAGNOSTIC_EMIT_SECONDS


def _diagnostic_enqueue_1513(category, key, fields, now=None):
    """Queue one bounded line; never query the engine or log per frame/slot."""
    if not _DIAGNOSTICS_ENABLED:
        return
    state = globals().setdefault('g_offh_destr_diagnostics', {
        'queue': [], 'seen_chunks': set(), 'seen_pending': set(),
        'seen_contacts': set(),
        'next_emit': 0.0, 'disabled': False,
    })
    if state.get('disabled'):
        return
    _diagnostic_flush_1513(now)
    seen_name = (
        'seen_chunks' if category == 'chunk' else
        'seen_pending' if category == 'chunk_pending' else
        'seen_contacts')
    seen = state[seen_name]
    if key in seen:
        return
    limit = (
        _DIAGNOSTIC_CHUNK_LIMIT if category == 'chunk' else
        _DIAGNOSTIC_PENDING_LIMIT if category == 'chunk_pending' else
        _DIAGNOSTIC_CONTACT_LIMIT)
    if len(seen) >= limit:
        return
    seen.add(key)
    parts = ['chunk' if category == 'chunk_pending' else category]
    for name, value in fields:
        text = str(value).replace('\n', '?').replace('\r', '?')
        parts.append('%s=%s' % (name, text))
    line = ' '.join(parts)
    if category == 'contact':
        state['queue'].insert(0, line)
    else:
        state['queue'].append(line)
    _diagnostic_flush_1513(now)


def _diagnostic_slot_1513(chunk_id, item_index):
    state = globals().get('g_offh_tree_state', {})
    registry = state.get('chunks', {}).get(int(chunk_id), {})
    return registry.get('slot_diagnostics', {}).get(int(item_index))


def _diagnostic_counts_1513(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return ','.join('%s:%s' % (key, counts[key]) for key in sorted(counts)) \
        or '-'


def _diagnostic_chunk_1513(chunk_id, native_count, filenames, registry):
    """Emit one aggregate after a complete first scan of a streamed chunk."""
    slots = registry['slot_diagnostics']
    ordered = [slots[index] for index in sorted(slots)]
    signatures = [slot['signature_state'] for slot in ordered]
    results = [slot['result'] for slot in ordered]
    effects = [slot['effect_category'] for slot in ordered
        if slot['effect_category'] != '-']
    registered = [result[len('registered_'):] for result in results
        if result.startswith('registered_')]
    _diagnostic_enqueue_1513('chunk', ('ready', int(chunk_id)), (
        ('chunk', int(chunk_id)),
        ('slots', int(native_count)),
        ('names', len(filenames)),
        ('named', sum(1 for slot in ordered if slot['raw'] == 'named')),
        ('blank', sum(1 for slot in ordered if slot['raw'] == 'blank')),
        ('v3_unique', signatures.count('unique')),
        ('v3_ambig', signatures.count('ambig')),
        ('v3_miss', signatures.count('miss')),
        ('effects', _diagnostic_counts_1513(effects)),
        ('registered', _diagnostic_counts_1513(registered)),
        ('boxes', sum(slot['boxes'] for slot in ordered)),
        ('rejects', _diagnostic_counts_1513(
            result for result in results
            if not result.startswith('registered_'))),
    ))


def _diagnostic_chunk_pending_1513(stage, chunk_id, native_count=None):
    fields = [('chunk', int(chunk_id)), ('state', stage)]
    if native_count is not None:
        fields.append(('slots', int(native_count)))
    _diagnostic_enqueue_1513(
        'chunk_pending', (stage, int(chunk_id)), fields)


def _diagnostic_contact_1513(stage, chunk_id=None, item_index=None,
        point=None, fields=(), now=None):
    if not _DIAGNOSTICS_ENABLED:
        return
    if chunk_id is None or item_index is None:
        if point is None:
            cell = ('unknown',)
        else:
            cell = (int(float(point.x) // 2.0),
                int(float(point.y) // 2.0), int(float(point.z) // 2.0))
        key = (stage,) + cell
        base = [('stage', stage), ('identity', 'none'), ('cell', cell)]
    else:
        chunk_id = int(chunk_id)
        item_index = int(item_index)
        key = (stage, chunk_id, item_index)
        base = [('stage', stage), ('chunk', chunk_id), ('item', item_index)]
        slot = _diagnostic_slot_1513(chunk_id, item_index)
        if slot is not None:
            base.extend((
                ('raw', slot.get('raw', '-')),
                ('sig', slot.get('signature_state', '-')),
                ('effect', slot.get('effect_category', '-')),
                ('registered', slot.get('result', '-')),
                ('boxes', slot.get('boxes', 0)),
            ))
    _diagnostic_enqueue_1513('contact', key, base + list(fields), now)


def _diagnostic_static_recast_1513(cleared, now=None):
    last = globals().pop('g_offh_destr_diag_last_static', None)
    if last is None:
        return
    chunk_id, item_index, fields = last
    _diagnostic_contact_1513(
        'static_recast_clear' if cleared else 'static_recast_blocked',
        chunk_id, item_index, fields=fields, now=now)


def _clear_runtime_registry():
    for name in ('g_offh_destr_seen', 'g_offh_destr_nodesc',
            'g_offh_tree_state', 'g_offh_destr_ordered',
            'g_offh_destr_chunks', 'g_offh_destr_instances',
            'g_offh_destr_contact_bins', 'g_offh_destr_pending',
            'g_offh_destr_falling_active',
            'g_offh_destr_diagnostics', 'g_offh_destr_diag_last_static',
            'g_offh_destr_runtime_space'):
        globals().pop(name, None)


def set_catalog(catalog):
    """Install one validated per-map 2.3.1.2 collider catalog.

    ``reset`` intentionally preserves this immutable map input.  Battle startup
    always replaces it (or explicitly passes ``None``), while reset only drops
    the streamed native item registry.
    """
    global _destructible_catalog
    if catalog is None:
        _destructible_catalog = None
        _clear_runtime_registry()
        return
    if not isinstance(catalog, dict):
        raise ValueError('destructible catalog root is invalid')
    try:
        quantization = int(catalog.get('locator_quantization'))
    except (TypeError, ValueError):
        raise ValueError('destructible locator quantization is invalid')
    if quantization != 1000:
        raise ValueError('destructible locator quantization is invalid')
    resources = catalog.get('resources')
    if not isinstance(resources, dict) or not resources:
        raise ValueError('destructible catalog resources are unavailable')
    prepared = {}
    max_radius = 0.0
    for filename, raw in resources.items():
        normalized = _normalized_filename(filename)
        if not normalized or normalized in prepared or not isinstance(raw, dict):
            raise ValueError('destructible catalog resource is invalid')
        kind = raw.get('kind')
        if kind not in ('fragile', 'structure', 'falling'):
            raise ValueError('destructible catalog resource kind is invalid')
        boxes = []
        for raw_box in raw.get('boxes') or ():
            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 7:
                raise ValueError('destructible catalog box is invalid')
            try:
                values = tuple(float(value) for value in raw_box[:6])
            except (TypeError, ValueError):
                raise ValueError('destructible catalog box is invalid')
            if not (values[0] < values[3] and values[1] < values[4] and
                    values[2] < values[5]):
                raise ValueError('destructible catalog box is invalid')
            mat_kind = raw_box[6]
            if kind != 'structure':
                if mat_kind is not None:
                    raise ValueError(
                        'non-structure catalog box has a material')
            else:
                try:
                    mat_kind = int(mat_kind)
                except (TypeError, ValueError):
                    raise ValueError('structure catalog material is invalid')
                if mat_kind < 71 or mat_kind > 130:
                    raise ValueError('structure catalog material is invalid')
            boxes.append(values + (mat_kind,))
            for local_x in (values[0], values[3]):
                for local_z in (values[2], values[5]):
                    radius = (local_x * local_x + local_z * local_z) ** 0.5
                    max_radius = max(max_radius, radius)
        if not boxes:
            raise ValueError('destructible catalog boxes are unavailable')
        locators = {}
        for row in raw.get('locators') or ():
            if (not isinstance(row, (list, tuple)) or len(row) != 13 or
                    any(type(value) is not int for value in row)):
                raise ValueError('destructible instance locator is invalid')
            signature = tuple(row[:12])
            box_index = int(row[12])
            if (signature in locators or box_index < 0 or
                    box_index >= len(boxes)):
                raise ValueError('destructible instance locator is invalid')
            locators[signature] = box_index
        if kind != 'structure' and len(boxes) > 1 and not locators:
            raise ValueError('ambiguous catalog has no locators')
        if (kind == 'structure' or len(boxes) == 1) and locators:
            raise ValueError('destructible catalog has unexpected locators')
        prepared[normalized] = {
            'filename': filename, 'kind': kind, 'boxes': tuple(boxes),
            'locators': locators,
        }
    try:
        catalog_version = int(catalog.get('version', 1))
    except (TypeError, ValueError):
        raise ValueError('destructible catalog version is invalid')
    raw_instances = catalog.get('instances')
    raw_ambiguous = catalog.get('ambiguous_instances')
    if catalog_version >= 3:
        if not isinstance(raw_instances, list) or not raw_instances:
            raise ValueError('destructible instance index is unavailable')
        if not isinstance(raw_ambiguous, list):
            raise ValueError(
                'ambiguous destructible instance index is invalid')
    else:
        raw_instances = raw_instances or ()
        raw_ambiguous = raw_ambiguous or ()
    instance_index = {}
    for row in raw_instances:
        if (not isinstance(row, (list, tuple)) or len(row) != 14 or
                any(type(value) is not int for value in row[:12])):
            raise ValueError('destructible instance row is invalid')
        signature = tuple(row[:12])
        normalized = _normalized_filename(row[12])
        record = prepared.get(normalized)
        if record is None or signature in instance_index:
            raise ValueError('destructible instance row is invalid')
        box_index = row[13]
        if record['kind'] == 'structure':
            if box_index is not None:
                raise ValueError(
                    'structure instance has a box index')
        else:
            if (type(box_index) is not int or box_index < 0 or
                    box_index >= len(record['boxes'])):
                raise ValueError(
                    'destructible instance box index is invalid')
        instance_index[signature] = {
            'filename': normalized, 'kind': record['kind'],
            'box_index': box_index,
        }
    ambiguous_signatures = set()
    for row in raw_ambiguous:
        if (not isinstance(row, (list, tuple)) or len(row) != 13 or
                any(type(value) is not int for value in row[:12]) or
                not isinstance(row[12], list) or len(row[12]) < 2):
            raise ValueError(
                'ambiguous destructible instance row is invalid')
        signature = tuple(row[:12])
        if signature in instance_index or signature in ambiguous_signatures:
            raise ValueError(
                'ambiguous destructible instance row is invalid')
        for candidate in row[12]:
            if (not isinstance(candidate, (list, tuple)) or
                    len(candidate) != 2 or
                    _normalized_filename(candidate[0]) not in prepared):
                raise ValueError(
                    'ambiguous destructible candidate is invalid')
        ambiguous_signatures.add(signature)
    _destructible_catalog = {
        'resources': prepared, 'quantization': quantization,
        'max_radius': max_radius, 'instances': instance_index,
        'ambiguous_instances': ambiguous_signatures,
        'has_instance_index': catalog_version >= 3,
    }
    _clear_runtime_registry()


def _destructible_bin_key(x, z):
    import math
    return (int(math.floor(float(x) / _DESTRUCTIBLE_BIN_METRES)),
        int(math.floor(float(z) / _DESTRUCTIBLE_BIN_METRES)))


def _bin_keys_for_bounds(minimum_x, maximum_x, minimum_z, maximum_z):
    import math
    minimum_bin_x = int(math.floor(minimum_x / _DESTRUCTIBLE_BIN_METRES))
    maximum_bin_x = int(math.floor(maximum_x / _DESTRUCTIBLE_BIN_METRES))
    minimum_bin_z = int(math.floor(minimum_z / _DESTRUCTIBLE_BIN_METRES))
    maximum_bin_z = int(math.floor(maximum_z / _DESTRUCTIBLE_BIN_METRES))
    for bin_x in xrange(minimum_bin_x, maximum_bin_x + 1):
        for bin_z in xrange(minimum_bin_z, maximum_bin_z + 1):
            yield bin_x, bin_z


def _nearby_destructibles(registry, pos, vehicle_box=None):
    """Yield origin items and catalog-footprint items near the hull."""
    origin_bounds = (float(pos.x) - _DESTRUCTIBLE_ORIGIN_RADIUS,
        float(pos.x) + _DESTRUCTIBLE_ORIGIN_RADIUS,
        float(pos.z) - _DESTRUCTIBLE_ORIGIN_RADIUS,
        float(pos.z) + _DESTRUCTIBLE_ORIGIN_RADIUS)
    extended_bounds = (_box_xz_bounds(vehicle_box) if vehicle_box is not None
        else origin_bounds)
    seen = set()
    for bins, bounds in ((registry['bins'], origin_bounds),
            (registry['extended_bins'], extended_bounds)):
        for bin_key in _bin_keys_for_bounds(*bounds):
            for item in bins.get(bin_key, ()):
                if item[0] in seen:
                    continue
                seen.add(item[0])
                yield item

def _symmetric_quantize(value, scale):
    import math
    scaled = float(value) * scale
    if scaled >= 0.0:
        return int(math.floor(scaled + 0.5))
    return int(math.ceil(scaled - 0.5))


def _matrix_point(matrix, math_module, x, y, z, chunk_translation):
    point = matrix.applyPoint(math_module.Vector3(x, y, z))
    return (float(chunk_translation.x + point.x),
        float(chunk_translation.y + point.y),
        float(chunk_translation.z + point.z))


def _locator_signature(matrix, chunk_translation, math_module, scale):
    origin = _matrix_point(
        matrix, math_module, 0.0, 0.0, 0.0, chunk_translation)
    basis = []
    for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0)):
        vector = matrix.applyVector(math_module.Vector3(*axis))
        basis.extend((vector.x, vector.y, vector.z))
    return tuple(_symmetric_quantize(value, scale)
        for value in origin + tuple(basis))


def _vector_dot(left, right):
    return (left[0] * right[0] + left[1] * right[1] +
        left[2] * right[2])


def _vector_cross(left, right):
    return (left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0])


def _matrix_vector(matrix, math_module, x, y, z):
    vector = matrix.applyVector(math_module.Vector3(x, y, z))
    return float(vector.x), float(vector.y), float(vector.z)


def _matrix_item_scale_1513(matrix, math_module):
    """Return the exact scale convention used by stock AreaDestructibles."""
    import math
    y_axis = _matrix_vector(matrix, math_module, 0.0, 1.0, 0.0)
    item_scale = _vector_dot(y_axis, y_axis) ** 0.5
    if (item_scale <= 0.0 or math.isinf(item_scale) or
            math.isnan(item_scale)):
        raise RuntimeError('2.3.1.2 destructible item scale is invalid')
    return item_scale


def _box_xz_bounds(box):
    center, half_axes = box[:2]
    radius_x = sum(abs(axis[0]) for axis in half_axes)
    radius_z = sum(abs(axis[2]) for axis in half_axes)
    return (center[0] - radius_x, center[0] + radius_x,
        center[2] - radius_z, center[2] + radius_z)


def _world_catalog_boxes(record, matrix, chunk_translation, math_module,
        instance_box_index=None):
    boxes = record['boxes']
    if record['kind'] != 'structure' and instance_box_index is not None:
        boxes = (boxes[instance_box_index],)
    elif record['kind'] != 'structure' and len(boxes) > 1:
        signature = _locator_signature(matrix, chunk_translation, math_module,
            _destructible_catalog['quantization'])
        box_index = record['locators'].get(signature)
        if box_index is None:
            return ()
        boxes = (boxes[box_index],)
    result = []
    for box in boxes:
        center = _matrix_point(matrix, math_module,
            (box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5,
            (box[2] + box[5]) * 0.5, chunk_translation)
        half_axes = (
            _matrix_vector(matrix, math_module,
                (box[3] - box[0]) * 0.5, 0.0, 0.0),
            _matrix_vector(matrix, math_module,
                0.0, (box[4] - box[1]) * 0.5, 0.0),
            _matrix_vector(matrix, math_module,
                0.0, 0.0, (box[5] - box[2]) * 0.5))
        volume = abs(_vector_dot(
            half_axes[0], _vector_cross(half_axes[1], half_axes[2])))
        if volume <= 1.0e-9:
            continue
        result.append((center, half_axes, box[6]))
    return tuple(result)


def _catalog_kind_for_type_1513(area_destructibles, destr_type):
    if destr_type == getattr(
            area_destructibles, 'DESTR_TYPE_FALLING_ATOM', None):
        return 'falling'
    if destr_type == getattr(
            area_destructibles, 'DESTR_TYPE_FRAGILE', None):
        return 'fragile'
    if destr_type == getattr(
            area_destructibles, 'DESTR_TYPE_STRUCTURE', None):
        return 'structure'
    return None


def _catalog_instance_for_matrix_1513(matrix, chunk_translation,
        math_module):
    if (_destructible_catalog is None or
            not _destructible_catalog.get('has_instance_index')):
        return None, None
    signature = _locator_signature(
        matrix, chunk_translation, math_module,
        _destructible_catalog['quantization'])
    if signature in _destructible_catalog['ambiguous_instances']:
        return signature, None
    return signature, _destructible_catalog['instances'].get(signature)


def _box_face_axes(half_axes):
    return (_vector_cross(half_axes[1], half_axes[2]),
        _vector_cross(half_axes[2], half_axes[0]),
        _vector_cross(half_axes[0], half_axes[1]))


def _boxes_intersect(left, right):
    left_center, left_half_axes = left[:2]
    right_center, right_half_axes = right[:2]
    delta = tuple(right_center[index] - left_center[index]
        for index in range(3))
    axes = list(_box_face_axes(left_half_axes))
    axes.extend(_box_face_axes(right_half_axes))
    axes.extend(_vector_cross(left_axis, right_axis)
        for left_axis in left_half_axes for right_axis in right_half_axes)
    for axis in axes:
        length_squared = _vector_dot(axis, axis)
        if length_squared <= 1.0e-16:
            continue
        left_radius = sum(abs(_vector_dot(axis, half_axis))
            for half_axis in left_half_axes)
        right_radius = sum(abs(_vector_dot(axis, half_axis))
            for half_axis in right_half_axes)
        if (abs(_vector_dot(delta, axis)) > left_radius + right_radius +
                1.0e-7 * length_squared ** 0.5):
            return False
    return True


def _point_in_world_box(point, world_box):
    center, half_axes = world_box[:2]
    delta = (point.x - center[0], point.y - center[1],
        point.z - center[2])
    for axis in _box_face_axes(half_axes):
        length = _vector_dot(axis, axis) ** 0.5
        if length <= 1.0e-8:
            return False
        radius = sum(abs(_vector_dot(axis, half_axis))
            for half_axis in half_axes)
        if abs(_vector_dot(delta, axis)) > (
                radius + _CATALOG_POINT_EPSILON * length):
            return False
    return True


def _segment_world_box_interval(start, end, world_box, padding=None):
    """Return the exact normalized ray interval inside one transformed OBB."""
    if padding is None:
        padding = _CATALOG_POINT_EPSILON
    center, half_axes = world_box[:2]
    start_delta = (start.x - center[0], start.y - center[1],
        start.z - center[2])
    end_delta = (end.x - center[0], end.y - center[1],
        end.z - center[2])
    entry = 0.0
    exit = 1.0
    for index, axis in enumerate(_box_face_axes(half_axes)):
        denominator = _vector_dot(axis, half_axes[index])
        axis_length = _vector_dot(axis, axis) ** 0.5
        if abs(denominator) <= 1.0e-12 or axis_length <= 1.0e-12:
            return None
        bound = 1.0 + float(padding) * axis_length / abs(denominator)
        start_value = _vector_dot(start_delta, axis) / denominator
        end_value = _vector_dot(end_delta, axis) / denominator
        delta = end_value - start_value
        if abs(delta) <= 1.0e-12:
            if abs(start_value) > bound:
                return None
            continue
        near = (-bound - start_value) / delta
        far = (bound - start_value) / delta
        if near > far:
            near, far = far, near
        entry = max(entry, near)
        exit = min(exit, far)
        if entry > exit:
            return None
    return entry, exit


def _instance_descriptor_filename_1513(instance):
    """Return the case-preserved filename required by DestructiblesCache."""
    filename = instance.get('descriptor_filename')
    if filename:
        return filename
    record = (_destructible_catalog or {}).get('resources', {}).get(
        instance.get('filename'))
    return record['filename'] if record is not None else instance['filename']


def _catalog_shot_intersection(start, end, maximum_distance=None):
    """Resolve the nearest unique streamed catalog OBB along a shell ray."""
    segment = end - start
    segment_length = segment.length
    if segment_length <= 1.0e-9:
        return None
    instances = globals().get('g_offh_destr_instances', {})
    if not instances:
        return None
    authority = _get_destr_authority()
    hits = {}
    for identity in sorted(instances):
        instance = instances[identity]
        for world_box in instance['boxes']:
            interval = _segment_world_box_interval(
                start, end, world_box, 0.0)
            if interval is None:
                continue
            entry, exit = interval
            distance = entry * segment_length
            if (maximum_distance is not None and
                    distance > float(maximum_distance) + 1.0e-6):
                continue
            mat_kind = (world_box[2]
                if instance['kind'] == 'structure' else None)
            if authority.is_destroyed(identity[0], identity[1], mat_kind):
                continue
            key = (identity[0], identity[1], mat_kind)
            previous = hits.get(key)
            if previous is None or distance < previous[0]:
                point = start + segment.scale(entry)
                candidate = identity + (
                    mat_kind, _instance_descriptor_filename_1513(instance),
                    instance['kind'], instance['item_scale'],
                    (float(point.x), float(point.y), float(point.z)))
                hits[key] = (distance, exit * segment_length, candidate)
    if not hits:
        return None
    nearest_distance = min(value[0] for value in hits.values())
    nearest = [value for value in hits.values()
        if abs(value[0] - nearest_distance) <= _CATALOG_POINT_EPSILON]
    if len(nearest) != 1:
        return {
            'candidate': None, 'distance': nearest_distance,
            'exit_distance': nearest_distance, 'ambiguous': True,
        }
    distance, exit_distance, candidate = nearest[0]
    return {
        'candidate': candidate, 'distance': distance,
        'exit_distance': exit_distance, 'ambiguous': False,
    }


def _vehicle_swept_box(pos, yaw, vel, bbox, travel_reach=None):
    import math
    minimum, maximum = bbox[:2]
    half_width = max(abs(minimum[0]), abs(maximum[0])) + 0.5
    back = abs(minimum[2])
    front = abs(maximum[2])
    if travel_reach is None:
        # Registration/streaming look-ahead keeps the historical generous reach.
        # Commit-side callers pass the exact frame travel separately below.
        reach = 0.8 + min(abs(vel) * 0.25, 1.2)
    else:
        reach = max(0.0, float(travel_reach))
    if vel < 0.0:
        minimum_forward = -(back + reach)
        maximum_forward = front
    else:
        minimum_forward = -back
        maximum_forward = front + reach
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    center_forward = (minimum_forward + maximum_forward) * 0.5
    half_forward = (maximum_forward - minimum_forward) * 0.5
    center_y = pos.y + (minimum[1] + maximum[1]) * 0.5
    half_y = (maximum[1] - minimum[1]) * 0.5
    center = (pos.x + sin_y * center_forward, center_y,
        pos.z + cos_y * center_forward)
    half_axes = ((cos_y * half_width, 0.0, -sin_y * half_width),
        (0.0, half_y, 0.0),
        (sin_y * half_forward, 0.0, cos_y * half_forward))
    return center, half_axes


def _vehicle_contact_box(pos, yaw, bbox, epsilon=0.075, travel=0.0):
    """Return only the leading hull face plus this frame's travel."""
    import math
    minimum, maximum = bbox[:2]
    margin = max(0.0, float(epsilon))
    travel = float(travel)
    if travel < 0.0:
        minimum_forward = float(minimum[2]) - margin + travel
        maximum_forward = float(minimum[2]) + margin
    else:
        minimum_forward = float(maximum[2]) - margin
        maximum_forward = float(maximum[2]) + margin + travel
    half_width = max(abs(float(minimum[0])), abs(float(maximum[0]))) + margin
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    center_forward = (minimum_forward + maximum_forward) * 0.5
    half_forward = (maximum_forward - minimum_forward) * 0.5
    center_y = pos.y + (float(minimum[1]) + float(maximum[1])) * 0.5
    half_y = (float(maximum[1]) - float(minimum[1])) * 0.5 + margin
    center = (pos.x + sin_y * center_forward, center_y,
        pos.z + cos_y * center_forward)
    half_axes = ((cos_y * half_width, 0.0, -sin_y * half_width),
        (0.0, half_y, 0.0),
        (sin_y * half_forward, 0.0, cos_y * half_forward))
    return center, half_axes


def _catalog_intersections(world_boxes, vehicle_box):
    result = []
    for world_box in world_boxes:
        if not _boxes_intersect(vehicle_box, world_box):
            continue
        result.append(world_box)
    return result


def _native_hide_delay():
    import AreaDestructibles
    try:
        delay = float(getattr(
            AreaDestructibles, 'DESTRUCTIBLE_HIDING_DELAY',
            _NATIVE_HIDE_MIN_SECONDS))
    except (TypeError, ValueError):
        delay = _NATIVE_HIDE_MIN_SECONDS
    return max(_NATIVE_HIDE_MIN_SECONDS, delay)


def note_destroyed(kind, chunkID, itemIndex, matKind=None, now=None):
    """Track native hide or falling-matrix collision after destruction."""
    if kind not in ('fragile', 'module', 'column'):
        return False
    if now is None:
        import BigWorld
        now = BigWorld.time()
    if kind == 'column':
        identity = (int(chunkID), int(itemIndex))
        active = globals().setdefault('g_offh_destr_falling_active', {})
        if identity not in active:
            active[identity] = {'last_refresh': None}
        return True
    key = (int(chunkID), int(itemIndex),
        int(matKind) if matKind is not None else None)
    pending = globals().setdefault('g_offh_destr_pending', {})
    if key not in pending:
        pending[key] = float(now) + _native_hide_delay()
    return True


def _catalog_contact_candidates(vehicle_box):
    instances = globals().get('g_offh_destr_instances', {})
    contact_bins = globals().get('g_offh_destr_contact_bins', {})
    candidates = []
    seen = set()
    for bin_key in _bin_keys_for_bounds(*_box_xz_bounds(vehicle_box)):
        for chunk_id, item_index in sorted(contact_bins.get(bin_key, ())):
            identity = (int(chunk_id), int(item_index))
            if identity in seen:
                continue
            seen.add(identity)
            instance = instances.get(identity)
            if instance is None:
                continue
            for world_box in _catalog_intersections(
                    instance['boxes'], vehicle_box):
                mat_kind = (world_box[2]
                    if instance['kind'] == 'structure' else None)
                candidate = identity + (mat_kind,
                    _instance_descriptor_filename_1513(instance),
                    instance['kind'], instance['item_scale'], world_box[0])
                if candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def _synthetic_mat_info(candidate, math_module):
    chunk_id, item_index, mat_kind, filename = candidate[:4]
    center = candidate[6]
    point = math_module.Vector3(center[0], center[1], center[2])
    normal = math_module.Vector3(0.0, 1.0, 0.0)
    return (True, point, normal,
        mat_kind if mat_kind is not None else 73,
        filename, item_index, chunk_id)


def _catalog_candidate_on_ray_1513(contact_pt, segment_start, segment_end):
    """Resolve one exact registered OBB on the current native ray.

    Point containment deliberately has a 7.5 cm tolerance for compiled BSP
    contacts.  That tolerance makes two touching fence tiles look ambiguous at
    the shared face, even after the ray has already advanced past the first
    tile's exact exit.  Intersecting the *remaining* ray with each unpadded OBB
    removes the tile behind ``segment_start`` without advancing the native ray or
    weakening the wall epsilon.
    """
    if _destructible_catalog is None:
        return None
    segment_length = (segment_end - segment_start).length
    if segment_length <= 1.0e-9:
        return None
    hit_distance = (contact_pt - segment_start).length
    instances = globals().get('g_offh_destr_instances', {})
    contact_bins = globals().get('g_offh_destr_contact_bins', {})
    bin_key = _destructible_bin_key(contact_pt.x, contact_pt.z)
    candidates = []
    for chunk_id, item_index in contact_bins.get(bin_key, ()):
        instance = instances.get((chunk_id, item_index))
        if instance is None:
            continue
        for world_box in instance['boxes']:
            interval = _segment_world_box_interval(
                segment_start, segment_end, world_box, 0.0)
            if interval is None:
                continue
            entry_distance = interval[0] * segment_length
            exit_distance = interval[1] * segment_length
            if (entry_distance > hit_distance + _CATALOG_POINT_EPSILON or
                    exit_distance + _CATALOG_POINT_EPSILON < hit_distance):
                continue
            mat_kind = (world_box[2]
                if instance['kind'] == 'structure' else None)
            candidate = (int(chunk_id), int(item_index), mat_kind,
                _instance_descriptor_filename_1513(instance),
                instance['kind'], instance['item_scale'])
            if candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) > 1:
                return None
    return candidates[0] if len(candidates) == 1 else None


def _catalog_soft_static_path(spaceID, segment_start, segment_end,
        collision, vel, td, recast_budget=None,
        require_pending_first=False, allow_kinetic_first=False,
        kinetic_speed=None):
    """Classify a far static ray without destroying anything.

    A bot direction probe may look 15--20 metres ahead.  It may regard a
    proved crushable as a soft obstacle so the bot can reach real hull contact,
    but it must never destroy from that distance or skip unrelated geometry
    behind the item.  Every skipped hit therefore needs one unique registered
    OBB, the retail kinetic gate, an exact OBB exit and a clear/native-next-hit
    recast.  Unknown and ambiguous chains remain solid.  Exhausting the shared
    native recast budget instead returns ``'deferred'`` so the caller can avoid
    caching a false hard wall.
    """
    if (_destructible_catalog is None or collision is None or td is None):
        return False
    import BigWorld
    import Math
    try:
        direction = segment_end - segment_start
        remaining = direction.length
        if remaining <= 1.0e-6:
            return False
        direction.normalise()
    except (AttributeError, TypeError, ValueError):
        return False

    current_start = segment_start
    current_hit = collision
    authority = _get_destr_authority()
    pending = globals().get('g_offh_destr_pending', {})
    kinetic_contact = False
    pending_contact = False
    for candidate_index in range(_SOFT_STATIC_MAX_SKIPS):
        try:
            hit_point = current_hit[0]
        except (TypeError, IndexError):
            return 'pending_hard' if pending_contact else False
        candidate = _catalog_candidate_on_ray_1513(
            hit_point, current_start, segment_end)
        if candidate is None:
            return 'pending_hard' if pending_contact else False
        destroyed = authority.is_destroyed(
            candidate[0], candidate[1], candidate[2])
        if candidate[4] == 'falling' and destroyed:
            # A felled column remains a moving/final native body.  Its refreshed
            # catalog OBB is collision geometry, not a hidden fragile skin.
            return 'pending_hard' if pending_contact else False
        candidate_key = (candidate[0], candidate[1], candidate[2])
        deadline = pending.get(candidate_key)
        pending_accepted = False
        if (candidate[4] in ('fragile', 'structure') and
                deadline is not None and destroyed):
            try:
                pending_accepted = float(BigWorld.time()) < float(deadline)
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError(
                    '2.3.1.2 pending destructible clock is unavailable')
        if (candidate[4] in ('fragile', 'structure') and destroyed and
                not pending_accepted):
            # An accepted identity cannot become a fresh kinetic contact.  If its
            # native skin survives past the pinned hide window, fail closed without
            # re-running the native material probes for an already-destroyed item.
            return 'pending_hard'
        mat_info = _synthetic_mat_info(candidate + ((
            float(hit_point.x), float(hit_point.y), float(hit_point.z)),), Math)
        current_crushable = pending_accepted or _stock_crushable_1513(
            mat_info, vel, td, candidate[5])
        if require_pending_first and candidate_index == 0:
            if pending_accepted:
                pending_contact = True
                current_crushable = True
            elif (allow_kinetic_first and kinetic_speed is not None and
                    not current_crushable and _stock_crushable_1513(
                        mat_info, kinetic_speed, td, candidate[5])):
                kinetic_contact = True
                current_crushable = True
            else:
                return 'pending_hard' if pending_contact else False
        elif (allow_kinetic_first and
                kinetic_speed is not None and not current_crushable and
                _stock_crushable_1513(
                    mat_info, kinetic_speed, td, candidate[5])):
            kinetic_contact = True
            current_crushable = True
        if not current_crushable:
            return 'pending_hard' if pending_contact else False
        exit_distance = _registered_shot_exit_1513(
            candidate[0], candidate[1], candidate[2], candidate[3],
            current_start, segment_end, hit_point)
        if exit_distance is None:
            return 'pending_hard' if pending_contact else False
        next_start = current_start + direction.scale(
            float(exit_distance) + _SHOT_RAY_EPSILON)
        if (segment_end - next_start).length <= _SHOT_RAY_EPSILON:
            return 'kinetic' if kinetic_contact else True
        if recast_budget is not None:
            if not recast_budget or int(recast_budget[0]) <= 0:
                return 'pending_hard' if pending_contact else 'deferred'
            recast_budget[0] = int(recast_budget[0]) - 1
        current_hit = BigWorld.wg_collideSegment(
            spaceID, next_start, segment_end, 128)
        if current_hit is None:
            return 'kinetic' if kinetic_contact else True
        current_start = next_start
    return 'pending_hard' if pending_contact else False


def _motion_travel_reach(vel, dt):
    # Match the grounded native sweep in world_collision.  A shorter catalog
    # reach can accept the static hit, then miss the pending native skin during
    # the copied pose commit and incorrectly feed it through hard-wall braking.
    return max(0.4, abs(float(vel)) * max(0.0, float(dt)) + 0.2)


def _catalog_pending_at_hull(pos, yaw, vel, td, now, dt=0.04):
    """Return whether a proved fragile/module hide window overlaps the hull.

    This is classification only.  Callers keep the pose blocked while native
    geometry hides, but preserve impact momentum instead of applying the hard
    wall exponential brake.  Falling atoms deliberately never enter this map.
    """
    bbox = _vehicle_hull_bbox(td)
    if _destructible_catalog is None or bbox is None:
        return False
    vehicle_box = _vehicle_swept_box(
        pos, yaw, vel, bbox, _motion_travel_reach(vel, dt))
    pending = globals().get('g_offh_destr_pending', {})
    for candidate in _catalog_contact_candidates(vehicle_box):
        key = (candidate[0], candidate[1], candidate[2])
        deadline = pending.get(key)
        if deadline is not None and float(now) < float(deadline):
            return True
    return False


def _catalog_hull_contact(pos, yaw, vel, td, dt=0.04):
    """Cheap contact-bin guard for the copied player/Bot pose integrators."""
    bbox = _vehicle_hull_bbox(td)
    if _destructible_catalog is None or bbox is None:
        return False
    return bool(_catalog_contact_candidates(
        _vehicle_swept_box(
            pos, yaw, vel, bbox, _motion_travel_reach(vel, dt))))


def _catalog_motion_result(status, token=None, accepted_now=False,
        used_kinetic_speed=False, return_status=False, return_detail=False):
    """Keep the legacy status seam while exposing an exact commit receipt."""
    if return_detail:
        return {
            'status': status,
            'token': tuple(sorted(token or ())) or None,
            'accepted_now': bool(accepted_now),
            'used_kinetic_speed': bool(used_kinetic_speed),
        }
    # ``approach`` is meaningful only to the combined world+catalog resolver.
    # Older callers must continue to fail closed on a non-contact lookahead.
    legacy_status = 'hard' if status == 'approach' else status
    return legacy_status if return_status else legacy_status != 'clear'


def _catalog_motion_blocked(spaceID, pos, yaw, vel, td, now,
        return_status=False, dt=0.04, kinetic_speed=None,
        return_detail=False, kinetic_commit=False):
    """Resolve exact streamed OBB contact before committing local movement."""
    _diagnostic_flush_1513(now)
    if _destructible_catalog is None:
        return _catalog_motion_result(
            'clear', return_status=return_status,
            return_detail=return_detail)
    bbox = _vehicle_hull_bbox(td)
    if bbox is None:
        return _catalog_motion_result(
            'clear', return_status=return_status,
            return_detail=return_detail)
    import Math
    auth = _get_destr_authority()
    _refresh_destroyed_falling_instances_1513(spaceID, auth, now)
    vehicle_box = _vehicle_swept_box(
        pos, yaw, vel, bbox, _motion_travel_reach(vel, dt))
    candidates = _catalog_contact_candidates(vehicle_box)
    if not candidates:
        return _catalog_motion_result(
            'clear', return_status=return_status,
            return_detail=return_detail)

    grouped = {}
    for candidate in candidates:
        grouped.setdefault((candidate[0], candidate[1]), []).append(candidate)
    pending = globals().setdefault('g_offh_destr_pending', {})
    instances = globals().get('g_offh_destr_instances', {})
    contact_box = (_vehicle_contact_box(
        pos, yaw, bbox, travel=float(vel) * max(0.0, float(dt)))
        if kinetic_speed is not None else None)
    blocked = False
    crushed = False
    kinetic = False
    approach = False
    exact_token = set()
    commit_candidates = []

    for identity in sorted(grouped):
        by_material = {}
        for candidate in grouped[identity]:
            by_material.setdefault(candidate[2], candidate)
        active = []
        for mat_kind in sorted(
                by_material, key=lambda value: -1 if value is None else value):
            candidate = by_material[mat_kind]
            chunk_id, item_index, unused_mat, unused_filename, kind = (
                candidate[:5])
            key = (chunk_id, item_index, mat_kind)
            contact_candidate = (contact_box is not None and
                kind in ('fragile', 'structure') and
                any(_boxes_intersect(contact_box, world_box)
                    for world_box in instances.get(
                        (chunk_id, item_index), {}).get('boxes', ())
                    if (kind != 'structure' or world_box[2] == mat_kind)))
            if (kind == 'falling' and
                    auth.is_destroyed(chunk_id, item_index, None)):
                blocked = True
                _diagnostic_contact_1513(
                    'swept_falling_active', chunk_id, item_index,
                    fields=(('kind', kind),), now=now)
                continue
            deadline = pending.get(key)
            if deadline is not None:
                if (float(now) < float(deadline) and
                        auth.is_destroyed(chunk_id, item_index, mat_kind)):
                    crushed = True
                    if contact_candidate:
                        exact_token.add(key)
                    _diagnostic_contact_1513(
                        'swept_pending', chunk_id, item_index,
                        fields=(('kind', kind), ('mat', mat_kind)), now=now)
                elif not auth.is_destroyed(
                        chunk_id, item_index, mat_kind):
                    blocked = True
                    _diagnostic_contact_1513(
                        'swept_pending_expired', chunk_id, item_index,
                        fields=(('kind', kind), ('mat', mat_kind)), now=now)
                # The native static probe already ran before this catalog seam.
                # Once its hide window expires, an authority-destroyed dynamic OBB
                # is clear here; a still-visible native skin is rejected in
                # ``_catalog_soft_static_path`` before this function is reached.
                continue
            if auth.is_destroyed(chunk_id, item_index, mat_kind):
                note_destroyed(
                    'module' if mat_kind is not None else 'fragile',
                    chunk_id, item_index, mat_kind, now)
                crushed = True
                if contact_candidate:
                    exact_token.add(key)
                _diagnostic_contact_1513(
                    'swept_destroyed', chunk_id, item_index,
                    fields=(('kind', kind), ('mat', mat_kind)), now=now)
                continue
            active.append((candidate, contact_candidate))

        if not active:
            continue
        if active[0][0][4] == 'structure' and len(active) != 1:
            blocked = True
            _diagnostic_contact_1513(
                'swept_multi_module', identity[0], identity[1],
                fields=(('modules', len(active)),), now=now)
            continue
        for candidate, contact_candidate in active:
            chunk_id, item_index, mat_kind, unused_filename, kind = (
                candidate[:5])
            key = (chunk_id, item_index, mat_kind)
            mat_info = _synthetic_mat_info(candidate, Math)
            physical_crushable = _stock_crushable_1513(
                mat_info, vel, td, candidate[5])
            cap_crushable = (kinetic_speed is not None and
                kind in ('fragile', 'structure') and
                _stock_crushable_1513(
                    mat_info, kinetic_speed, td, candidate[5]))
            if (kinetic_speed is not None and not contact_candidate and
                    not physical_crushable):
                # A real frame sweep at sufficient physical speed keeps the old
                # crush-through behaviour.  Only the directional-cap shortcut is
                # restricted to exact hull contact; otherwise it is planning-only.
                if kind in ('fragile', 'structure') and cap_crushable:
                    approach = True
                else:
                    blocked = True
                continue
            if contact_candidate:
                exact_token.add(key)
            if physical_crushable:
                commit_candidates.append((candidate, vel, False))
            elif cap_crushable:
                if kinetic_commit:
                    commit_candidates.append((candidate, kinetic_speed, True))
                else:
                    kinetic = True
            else:
                blocked = True
            _diagnostic_contact_1513(
                ('swept_kinetic_hold' if cap_crushable else
                'swept_kinetic_reject'), chunk_id, item_index,
                fields=(('kind', kind), ('mat', mat_kind),
                    ('speed', '%.3f' % float(vel)),
                    ('scale', '%.5f' % float(candidate[5]))), now=now)

    accepted_now = False
    used_kinetic_speed = False
    if not blocked and not kinetic:
        for candidate, gate_speed, used_cap in commit_candidates:
            chunk_id, item_index, mat_kind, unused_filename, kind = (
                candidate[:5])
            mat_info = _synthetic_mat_info(candidate, Math)
            point = mat_info[1]
            if kind == 'fragile':
                accepted = auth.destroy_fragile(
                    spaceID, chunk_id, item_index, point, False)
                event_kind = 'fragile'
            elif kind == 'structure':
                accepted = auth.destroy_module(
                    spaceID, chunk_id, item_index, mat_kind, point, False)
                event_kind = 'module'
            elif kind == 'falling' and not used_cap:
                accepted = auth.destroy_column(
                    spaceID, chunk_id, item_index, yaw, vel, point)
                event_kind = 'column'
            else:
                blocked = True
                break
            if not accepted:
                raise RuntimeError(
                    'native catalog contact destroy was not accepted: '
                    'chunk=%s item=%s' % (chunk_id, item_index))
            note_destroyed(
                event_kind, chunk_id, item_index, mat_kind, now)
            _publish_destroyed(
                event_kind, chunk_id, item_index, point, yaw, vel,
                mat_kind if event_kind == 'module' else None)
            accepted_now = True
            used_kinetic_speed = used_kinetic_speed or used_cap
            _diagnostic_contact_1513(
                'swept_native_accept', chunk_id, item_index,
                fields=(('kind', kind), ('mat', mat_kind),
                    ('speed', '%.3f' % float(gate_speed))), now=now)
            if event_kind == 'column':
                blocked = True
            else:
                crushed = True

    status = ('hard' if blocked else
        'kinetic' if kinetic else
        'crushed' if crushed else
        'approach' if approach else 'clear')
    return _catalog_motion_result(
        status, None if blocked else exact_token, accepted_now,
        used_kinetic_speed, return_status, return_detail)


def _catalog_instance_boxes(chunkID, itemIndex, filename, kind,
        matKind=None):
    if _destructible_catalog is None:
        return None
    normalized = _normalized_filename(filename)
    record = _destructible_catalog['resources'].get(normalized)
    instance = globals().get('g_offh_destr_instances', {}).get(
        (chunkID, itemIndex))
    if (record is None or record['kind'] != kind or instance is None or
            instance['filename'] != normalized or instance['kind'] != kind):
        return ()
    if kind == 'structure':
        return tuple(box for box in instance['boxes'] if box[2] == matKind)
    return instance['boxes']


def _catalog_candidate_at_contact(contact_pt):
    """Resolve exactly one registered catalog item/module at native contact."""
    if _destructible_catalog is None:
        return None
    candidates = []
    instances = globals().get('g_offh_destr_instances', {})
    contact_bins = globals().get('g_offh_destr_contact_bins', {})
    bin_key = _destructible_bin_key(contact_pt.x, contact_pt.z)
    for chunk_id, item_index in contact_bins.get(bin_key, ()):
        instance = instances.get((chunk_id, item_index))
        if instance is None:
            continue
        for world_box in instance['boxes']:
            if not _point_in_world_box(contact_pt, world_box):
                continue
            mat_kind = (world_box[2]
                if instance['kind'] == 'structure' else None)
            candidate = (int(chunk_id), int(item_index), mat_kind,
                _instance_descriptor_filename_1513(instance),
                instance['kind'], instance['item_scale'])
            if candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) > 1:
                return None
    return candidates[0] if len(candidates) == 1 else None


def _catalog_candidate_for_native_identity_1513(
        chunk_id, item_index, mat_kind, contact_pt):
    """Recover an anonymous native material hit without guessing identity."""
    instance = globals().get('g_offh_destr_instances', {}).get(
        (int(chunk_id), int(item_index)))
    if instance is None:
        return None
    if instance['kind'] == 'structure':
        boxes = tuple(box for box in instance['boxes']
            if box[2] == mat_kind and _point_in_world_box(contact_pt, box))
        candidate_mat = mat_kind
    else:
        boxes = tuple(box for box in instance['boxes']
            if _point_in_world_box(contact_pt, box))
        candidate_mat = None
    if not boxes:
        return None
    return (int(chunk_id), int(item_index), candidate_mat,
        _instance_descriptor_filename_1513(instance),
        instance['kind'], instance['item_scale'])


def _catalog_bin_keys_1513(world_boxes):
    """Validate and return every spatial bin touched by exact world boxes."""
    bin_keys = set()
    for world_box in world_boxes:
        bin_keys.update(_bin_keys_for_bounds(*_box_xz_bounds(world_box)))
    return bin_keys


def _index_catalog_instance_1513(contact_bins, key, instance,
        bin_keys=None):
    """Index one streamed instance into every exact world-footprint bin."""
    if bin_keys is None:
        bin_keys = _catalog_bin_keys_1513(instance['boxes'])
    for bin_key in bin_keys:
        contact_bins.setdefault(bin_key, set()).add(key)
    instance['bin_keys'] = tuple(sorted(bin_keys))
    return bin_keys


def _falling_initial_matrix_1513(spaceID, chunkID, itemIndex, math_module):
    """Read the exact pre-animation matrix cached by the pinned manager."""
    import AreaDestructibles
    mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
    if mgr is None or mgr.getSpaceID() != spaceID:
        raise RuntimeError(
            '2.3.1.2 falling destructibles manager is unavailable for space')
    matrices = getattr(
        mgr, '_DestructiblesManager__destrInitialMatrices', None)
    if not isinstance(matrices, dict):
        raise RuntimeError(
            '2.3.1.2 falling initial-matrix cache is unavailable')
    raw_matrix = matrices.get((int(chunkID), int(itemIndex)))
    if raw_matrix is None:
        return None
    try:
        return math_module.Matrix(raw_matrix)
    except Exception as error:
        raise RuntimeError(
            '2.3.1.2 falling initial matrix is invalid: chunk=%s item=%s: %s' %
            (chunkID, itemIndex, error))


def _falling_native_state_1513(spaceID, chunkID, itemIndex, math_module):
    """Return the pinned manager's exact initial matrix and active flag."""
    initial_matrix = _falling_initial_matrix_1513(
        spaceID, chunkID, itemIndex, math_module)
    if initial_matrix is None:
        return None, False

    import AreaDestructibles
    animator = getattr(AreaDestructibles, 'g_destructiblesAnimator', None)
    bodies = getattr(
        animator, '_DestructiblesAnimator__bodies', None)
    if not isinstance(bodies, list):
        raise RuntimeError('2.3.1.2 falling animator body list is unavailable')
    matches = 0
    for body in bodies:
        if not isinstance(body, dict):
            raise RuntimeError('2.3.1.2 falling animator body is invalid')
        try:
            body_identity = (int(body['spaceID']), int(body['chunkID']),
                int(body['destrIndex']))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('2.3.1.2 falling animator body identity is invalid')
        if body_identity == (int(spaceID), int(chunkID), int(itemIndex)):
            matches += 1
    if matches > 1:
        raise RuntimeError('2.3.1.2 falling animator identity is ambiguous')
    return initial_matrix, matches == 1


def _refresh_destroyed_falling_instances_1513(spaceID, authority, now):
    """Follow each destroyed falling atom's live native transform exactly."""
    instances = globals().get('g_offh_destr_instances', {})
    active = globals().setdefault('g_offh_destr_falling_active', {})
    identities = []
    for identity in sorted(active):
        instance = instances.get(identity)
        state = active[identity]
        if (instance is None or instance.get('kind') != 'falling' or
                not authority.is_destroyed(
                    identity[0], identity[1], None)):
            continue
        last_refresh = state['last_refresh']
        if (last_refresh is not None and
                float(now) - last_refresh < _FALLING_REFRESH_SECONDS):
            continue
        identities.append(identity)
    if not identities:
        return

    import BigWorld
    import Math
    contact_bins = globals().setdefault('g_offh_destr_contact_bins', {})
    for identity in identities:
        chunk_id, item_index = identity
        instance = instances[identity]
        record = _destructible_catalog['resources'].get(
            instance['filename'])
        if record is None or record['kind'] != 'falling':
            raise RuntimeError(
                '2.3.1.2 falling catalog identity is unavailable: '
                'chunk=%s item=%s' % identity)
        initial_matrix, animation_active = _falling_native_state_1513(
            spaceID, chunk_id, item_index, Math)
        if initial_matrix is None:
            # The manager has admitted the canonical result but has not flushed its
            # streamed-chunk queue yet.  Preserve the last exact OBB and retry.
            active[identity]['last_refresh'] = float(now)
            continue
        try:
            matrix = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
                spaceID, chunk_id, item_index))
        except Exception as error:
            raise RuntimeError(
                '2.3.1.2 falling destructible matrix query failed: '
                'chunk=%s item=%s: %s' %
                (chunk_id, item_index, error))
        chunk_translation = Math.Vector3(*instance['chunk_translation'])
        boxes = _world_catalog_boxes(
            record, matrix, chunk_translation, Math,
            instance.get('box_index'))
        if not boxes:
            raise RuntimeError(
                '2.3.1.2 falling destructible has no current collision box: '
                'chunk=%s item=%s' % identity)
        current_scale = _matrix_item_scale_1513(matrix, Math)
        initial_scale = float(instance['item_scale'])
        if abs(current_scale - initial_scale) > max(
                1.0e-5, initial_scale * 1.0e-5):
            raise RuntimeError(
                '2.3.1.2 falling destructible changed scale: '
                'chunk=%s item=%s' % identity)
        # Validate the complete replacement index before mutating the live one.
        # Any malformed native matrix therefore preserves the previous solid OBB.
        new_bin_keys = _catalog_bin_keys_1513(boxes)

        old_bin_keys = instance.get('bin_keys')
        if old_bin_keys is None:
            old_bin_keys = set()
            for world_box in instance['boxes']:
                old_bin_keys.update(_bin_keys_for_bounds(
                    *_box_xz_bounds(world_box)))
        for bin_key in old_bin_keys:
            members = contact_bins.get(bin_key)
            if members is None:
                continue
            members.discard(identity)
            if not members:
                del contact_bins[bin_key]
        instance['boxes'] = boxes
        _index_catalog_instance_1513(
            contact_bins, identity, instance, new_bin_keys)
        state = active[identity]
        state['last_refresh'] = float(now)
        if not animation_active:
            # The manager caches the initial matrix immediately before showFall.
            # Once that cache exists, no matching body means either synchronous
            # final placement or a completed animation.  Freeze this final OBB and
            # stop polling the native matrix for this identity.
            del active[identity]


def _decode_mat_info_1513(payload):
    """Translate the pinned 2.3.1.2 material-hit ABI to the 0.8.2 law.

    The older engine returned six values and used ``None`` for a miss.  2.3.1.2
    always returns seven values and carries the hit/miss bit in element zero.
    Its native tail is ``(itemIndex, chunkID)``; the canonical copied law below
    still consumes ``(chunkID, itemIndex)``.
    Keeping that translation here lets the copied contact law retain its mature
    internal field order without guessing at the native tuple shape.
    """
    if not isinstance(payload, tuple):
        raise RuntimeError(
            '2.3.1.2 wg_getMatInfoNearPoint payload must be a tuple')
    width = len(payload)
    if width != 7:
        raise RuntimeError(
            '2.3.1.2 wg_getMatInfoNearPoint payload must contain 7 items; got %d' %
            width)
    (collided, hitPt, surfNormal, matKind, fname,
     itemIndex, chunkID) = payload
    if type(collided) is not bool:
        raise RuntimeError(
            '2.3.1.2 wg_getMatInfoNearPoint collided flag must be bool')
    if not collided:
        return None
    return hitPt, surfNormal, chunkID, itemIndex, matKind, fname


def _descriptor_value(value, name, default=None):
    """Read copied mappings or native 2.3.1.2 component attributes."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _vehicle_hull_bbox(type_descriptor):
    """Return the native hull bbox without touching disabled LegacyStuff APIs."""
    if type_descriptor is None:
        return None
    hull = _descriptor_value(type_descriptor, 'hull')
    if hull is None:
        raise RuntimeError('2.3.1.2 vehicle hull descriptor is unavailable')
    hit_tester = _descriptor_value(hull, 'hitTester')
    if hit_tester is None:
        raise RuntimeError('2.3.1.2 hull hit tester is unavailable')
    bbox = getattr(hit_tester, 'bbox', None)
    if bbox is None:
        raise RuntimeError('2.3.1.2 hull hit tester bbox is unavailable')
    return bbox

def LOG_DEBUG(*unused_args):
    # The user requested no trace-heavy battle logging.
    pass


def _get_destr_authority():
    from gui.mods.offline_battle_2312 import destructibles_authority
    return destructibles_authority


def set_event_sink(callback):
    global _event_sink
    if callback is not None and not callable(callback):
        raise TypeError('destructible event sink must be callable')
    _event_sink = callback


def _position_payload(pos):
    try:
        return float(pos.x), float(pos.y), float(pos.z)
    except AttributeError:
        return float(pos[0]), float(pos[1]), float(pos[2])


def _publish_destroyed(kind, chunkID, itemIndex, pos, fallYaw=0.0,
        speed=0.0, matKind=None, isShotDamage=False):
    if _event_sink is None:
        return True
    x, y, z = _position_payload(pos)
    event = {
        'destructible_kind': str(kind),
        'chunk_id': int(chunkID),
        'item_index': int(itemIndex),
        'x': x, 'y': y, 'z': z,
        'fall_yaw': float(fallYaw),
        'speed': float(speed),
        'is_shot': bool(isShotDamage),
    }
    if matKind is not None:
        event['mat_kind'] = int(matKind)
    if not _event_sink(event):
        raise RuntimeError('destructible event was not admitted by LAN client')
    return True


def reset(spaceID=None):
    _clear_runtime_registry()
    if spaceID is not None:
        globals()['g_offh_destr_runtime_space'] = int(spaceID)
    _get_destr_authority().reset(spaceID)


def _try_destroy_destructible(spaceID, matInfo, yaw, vel,
        isShotDamage=False):
    decoded = _decode_mat_info_1513(matInfo)
    if decoded is None:
        return False
    import AreaDestructibles
    if (not hasattr(AreaDestructibles, 'g_destructiblesManager') or
            not AreaDestructibles.g_destructiblesManager):
        raise RuntimeError('destructibles manager is unavailable')

    hitPt, surfNormal, chunkID, itemIndex, matKind, fname = decoded
    _dseen = globals().setdefault('g_offh_destr_seen', set())
    _dkey = (matKind, fname)
    if _dkey not in _dseen:
        _dseen.add(_dkey)
        LOG_DEBUG('Destr hit: matKind=', matKind, 'fname=', repr(fname),
            'chunk=', chunkID, 'idx=', itemIndex)
    # Widened band: the strict 71-100 range rejected spawn barriers/props at
    # matKind 102. getDescByFilename below is the real filter, so a wider band
    # only lets more candidates reach the authoritative desc check.
    if matKind < 71 or matKind > 130:
        return False
    desc = AreaDestructibles.g_cache.getDescByFilename(fname)
    if not desc:
        _dnd = globals().setdefault('g_offh_destr_nodesc', set())
        if _dkey not in _dnd:
            _dnd.add(_dkey)
            LOG_DEBUG('Destr no desc: matKind=', matKind,
                'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
        return False

    # Data-driven vegetation gate: soft vegetation (bush/shrub/fern)
    # ships with health <= 5; real fallable trees start at 10.
    typ = desc['type']
    if _destructible_catalog is not None and typ in (
            AreaDestructibles.DESTR_TYPE_FALLING_ATOM,
            AreaDestructibles.DESTR_TYPE_FRAGILE,
            AreaDestructibles.DESTR_TYPE_STRUCTURE):
        record = _destructible_catalog['resources'].get(
            _normalized_filename(fname))
        expected_kind = _catalog_kind_for_type_1513(
            AreaDestructibles, typ)
        if record is None or record['kind'] != expected_kind:
            return False
    if typ == AreaDestructibles.DESTR_TYPE_TREE:
        _hp_gate = desc.get('health', 0)
        if _hp_gate < 10 or _hp_gate > 1000:
            return False
    # All bookkeeping (chunk bootstrap, dedup, encoding) lives in
    # the authority - this path is now just a contact sensor.
    _auth = _get_destr_authority()
    # STRUCTURE (buildings) now falls through to the module-destroy path.
    if _auth.is_destroyed(chunkID, itemIndex, matKind):
        # This only means the order was accepted.  Animated 2.3.1.2 fragile and
        # module skins remove native collision later, so movement must still
        # re-cast the actual solid ray before it becomes passable.
        return False

    if typ == AreaDestructibles.DESTR_TYPE_TREE:
        _destr_ok = _auth.destroy_tree(
            spaceID, chunkID, itemIndex, yaw, vel, hitPt)
    elif typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM:
        _destr_ok = _auth.destroy_column(
            spaceID, chunkID, itemIndex, yaw, vel, hitPt)
    elif typ == AreaDestructibles.DESTR_TYPE_FRAGILE:
        _destr_ok = _auth.destroy_fragile(
            spaceID, chunkID, itemIndex, hitPt, isShotDamage)
    else:
        _destr_ok = _auth.destroy_module(
            spaceID, chunkID, itemIndex, matKind, hitPt, isShotDamage)
    if not _destr_ok:
        raise RuntimeError(
            'native destructible destroy was not accepted: chunk=%s item=%s' %
            (chunkID, itemIndex))
    _event_kind = (
        'tree' if typ == AreaDestructibles.DESTR_TYPE_TREE else
        'column' if typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM else
        'fragile' if typ == AreaDestructibles.DESTR_TYPE_FRAGILE else
        'module')
    # Fragile/module skins hide after 2.3.1.2's delayed callback.  Falling atoms
    # instead animate their native matrix and remain in the world at the final
    # pose; their catalog OBB follows that matrix in the motion contact path.
    if _event_kind in ('fragile', 'module', 'column'):
        try:
            import BigWorld
        except ImportError:
            # Unit-level callers may exercise the pure transaction helper
            # without the engine module.  The real 2.3.1.2 runtime always supplies
            # BigWorld.time; zero still preserves one monotonic pending window.
            _now = 0.0
        else:
            _now = BigWorld.time()
        note_destroyed(
            _event_kind, chunkID, itemIndex,
            matKind if _event_kind == 'module' else None,
            _now)
    _publish_destroyed(
        _event_kind,
        chunkID, itemIndex, hitPt, yaw, vel,
        matKind if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE else None,
        isShotDamage)
    return True


def _fell_trees_near(spaceID, pos, yaw, vel, td=None):
    # Offline tree/pole felling. Online the SERVER detected tank-vs-tree
    # contact; the client-side collision probes never return tree/column
    # materials, so trees could never fall offline. Instead: enumerate
    # each chunk's destructibles once (filename + world matrix), then
    # fell TREE / FALLING_ATOM items that intersect the moving hull.
    import math
    import AreaDestructibles
    import BigWorld
    import Math
    try:
        mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
        if not mgr:
            raise RuntimeError('destructibles manager is unavailable')
        structure_type = getattr(
            AreaDestructibles, 'DESTR_TYPE_STRUCTURE', None)
        if mgr.getSpaceID() != spaceID:
            mgr.startSpace(spaceID)
        if globals().get('g_offh_destr_runtime_space') != spaceID:
            _clear_runtime_registry()
            globals()['g_offh_destr_runtime_space'] = int(spaceID)
        _st = globals().setdefault('g_offh_tree_state', {'chunks': {}, 'felled': set(), 'spaceID': None})
        if _st.get('spaceID') != spaceID:
            # New battle/space: chunk IDs collide between maps and the
            # dedup sets would suppress destruction of fresh objects.
            _st['chunks'] = {}
            _st['felled'] = set()
            _st['spaceID'] = spaceID
            globals().setdefault('g_offh_destr_ordered', set())
            globals().setdefault('g_offh_destr_chunks', set())
            globals().setdefault('g_offh_destr_seen', set())
            globals().setdefault('g_offh_destr_instances', {})
            globals().setdefault('g_offh_destr_contact_bins', {})
            globals().setdefault('g_offh_destr_pending', {})
            globals().setdefault('g_offh_destr_falling_active', {})
        cos_y = math.cos(yaw); sin_y = math.sin(yaw)
        cids = set()
        _current_cid = None
        for _pf in (0.0, 6.0 if vel >= 0 else -6.0):
            _mapped_cid = AreaDestructibles.chunkIDFromPosition(
                Math.Vector3(pos.x + sin_y * _pf, pos.y,
                    pos.z + cos_y * _pf))
            if _pf == 0.0:
                _current_cid = _mapped_cid
            cids.add(_mapped_cid)
        # 2.3.1.2 chunks are 100 m squares.  Catalog instances can be non-uniformly
        # scaled, so raw resource bounds cannot determine the origin reach.  Sample
        # the current chunk plus all eight neighbours through the native mapper;
        # the pinned catalog's transformed maximum XZ reach is below 50 m.
        if _destructible_catalog is not None:
            for _offset_x in (-_DESTRUCTIBLE_CHUNK_METRES_1513, 0.0,
                    _DESTRUCTIBLE_CHUNK_METRES_1513):
                for _offset_z in (-_DESTRUCTIBLE_CHUNK_METRES_1513, 0.0,
                        _DESTRUCTIBLE_CHUNK_METRES_1513):
                    cids.add(AreaDestructibles.chunkIDFromPosition(
                        Math.Vector3(pos.x + _offset_x, pos.y,
                            pos.z + _offset_z)))
        cids.discard(None)
        bbox = ((-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None)
        bbox = _vehicle_hull_bbox(td)
        if bbox is None:
            bbox = ((-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None)
        try:
            hw = max(abs(bbox[0][0]), abs(bbox[1][0]))
            hl_b = abs(bbox[0][2])
            hl_f = abs(bbox[1][2])
        except (AttributeError, KeyError, TypeError, IndexError):
            raise RuntimeError('2.3.1.2 hull hit tester bbox is invalid')
        vehicle_box = _vehicle_swept_box(pos, yaw, vel, bbox)
        instances = globals().setdefault('g_offh_destr_instances', {})
        contact_bins = globals().setdefault(
            'g_offh_destr_contact_bins', {})
        for cid in cids:
            registry = _st['chunks'].get(cid)
            if registry is None:
                _native_count = _native_chunk_destructible_count_1513(
                    mgr, cid)
                if _native_count is None:
                    if cid == _current_cid:
                        _diagnostic_chunk_pending_1513(
                            'count_pending', cid)
                    # ``game.wg_onChunkLoad`` has not admitted this chunk yet. Do not
                    # infer a count from the diagnostic filename prefix; retry after
                    # the native streaming callback populates the manager map.
                    continue
                _dfn = BigWorld.wg_getChunkDestrFilenames(spaceID, cid)
                if _dfn is None:
                    if cid == _current_cid:
                        _diagnostic_chunk_pending_1513(
                            'names_pending', cid, _native_count)
                    continue # chunk not streamed in yet; retry next tick
                if not isinstance(_dfn, (list, tuple)):
                    raise RuntimeError(
                        '2.3.1.2 destructible filename payload is invalid: chunk=%s' %
                        cid)
                if len(_dfn) > _native_count:
                    raise RuntimeError(
                        '2.3.1.2 destructible filename prefix exceeds native count: '
                        'chunk=%s names=%s count=%s' %
                        (cid, len(_dfn), _native_count))
                registry = {
                    'bins': {}, 'extended_bins': {}, 'count': 0,
                    'max_radius': 0.0, 'slot_diagnostics': {},
                }
                _retry_registry = False
                _cm_t = BigWorld.wg_getChunkMatrix(spaceID, cid).translation
                if _cm_t is None:
                    continue
                for _ti in xrange(_native_count):
                    try:
                        # 2.3.1.2's offline chunk list keeps the native item slots,
                        # but returns blank filenames for many non-tree items.  Read
                        # the item matrix first and recover the resource only through
                        # the checksum-pinned whole-map instance signature.
                        _m = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
                            spaceID, cid, _ti))
                        _raw_filename = (
                            _dfn[_ti] if _ti < len(_dfn) else '')
                        if not isinstance(_raw_filename, _STRING_TYPES):
                            raise RuntimeError(
                                '2.3.1.2 destructible filename slot is invalid: '
                                'chunk=%s item=%s' % (cid, _ti))
                        _raw_normalized = _normalized_filename(_raw_filename)
                        _slot_diag = {
                            'raw': 'named' if _raw_normalized else 'blank',
                            'signature_state': 'none',
                            'effect_category': '-',
                            'result': 'pending',
                            'boxes': 0,
                        }
                        registry['slot_diagnostics'][_ti] = _slot_diag
                        if ((cid, _ti) in globals().setdefault(
                                'g_offh_destr_falling_active', {})):
                            # A falling item's live matrix is no longer its catalog
                            # placement and can even quantize to another valid resource.
                            # Recover identity only from the exact pre-animation cache;
                            # the live matrix below remains authoritative for its OBB.
                            _initial_matrix = _falling_initial_matrix_1513(
                                spaceID, cid, _ti, Math)
                            if _initial_matrix is None:
                                _retry_registry = True
                                _slot_diag['result'] = 'native_matrix_pending'
                                continue
                            _signature, _located = (
                                _catalog_instance_for_matrix_1513(
                                    _initial_matrix, _cm_t, Math))
                        else:
                            _signature, _located = (
                                _catalog_instance_for_matrix_1513(
                                    _m, _cm_t, Math))
                        if (_destructible_catalog is not None and
                                _destructible_catalog.get('has_instance_index')):
                            if _signature in _destructible_catalog[
                                    'ambiguous_instances']:
                                _slot_diag['signature_state'] = 'ambig'
                                _slot_diag['result'] = 'sig_ambig'
                                # Multiple native identities have exactly the same
                                # matrix. A blank slot cannot select one without
                                # guessing.
                                continue
                            _slot_diag['signature_state'] = (
                                'unique' if _located is not None else 'miss')
                        _instance_box_index = None
                        if _located is not None:
                            if (_raw_normalized and
                                    _raw_normalized != _located['filename']):
                                raise RuntimeError(
                                    '2.3.1.2 destructible filename disagrees with '
                                    'catalog instance')
                            _catalog_record = _destructible_catalog[
                                'resources'][_located['filename']]
                            _filename = _catalog_record['filename']
                            _instance_box_index = _located['box_index']
                        else:
                            _filename = _raw_filename
                            _catalog_record = None
                            if (_raw_normalized and
                                    _destructible_catalog is not None):
                                _catalog_record = _destructible_catalog[
                                    'resources'].get(_raw_normalized)
                                if (_catalog_record is not None and
                                        _destructible_catalog.get(
                                            'has_instance_index')):
                                    # A v3 catalog resource without its exact placement
                                    # signature is not this native item.
                                    _slot_diag['result'] = 'sig_miss'
                                    continue
                        desc = AreaDestructibles.g_cache.getDescByFilename(
                            _filename)
                        if desc is None:
                            _slot_diag['result'] = 'desc_missing'
                            continue
                        typ = desc['type']
                        if typ in (AreaDestructibles.DESTR_TYPE_FRAGILE,
                                structure_type,
                                AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
                            _expected_kind = _catalog_kind_for_type_1513(
                                AreaDestructibles, typ)
                            if (_catalog_record is None or
                                    _catalog_record['kind'] != _expected_kind):
                                _slot_diag['result'] = 'kind_mismatch'
                                continue
                            if _located is not None:
                                _module_index = (0 if _expected_kind ==
                                    'structure' else -1)
                                try:
                                    _native_type = (
                                        BigWorld.wg_getDestructibleEffectCategory(
                                            spaceID, cid, _ti, _module_index))
                                except Exception as error:
                                    raise RuntimeError(
                                        '2.3.1.2 destructible effect category query '
                                        'failed: chunk=%s item=%s module=%s: %s' %
                                        (cid, _ti, _module_index, error))
                                if _catalog_kind_for_type_1513(
                                        AreaDestructibles, _native_type) != _expected_kind:
                                    _slot_diag['effect_category'] = _native_type
                                    _slot_diag['result'] = 'effect_mismatch'
                                    continue
                                _slot_diag['effect_category'] = _native_type
                        elif typ not in (
                                AreaDestructibles.DESTR_TYPE_TREE,
                                AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
                            _slot_diag['result'] = 'type_unsupported'
                            continue
                        # Data-driven vegetation gate: destructibles.xml gives
                        # soft vegetation (bushes/shrubs/ferns/weeds) health<=5
                        # (or -2); real fallable trees start at health 10.
                        # ChristmasTree sentinels use 40000 = unrammable.
                        if typ == AreaDestructibles.DESTR_TYPE_TREE:
                            _hp_gate = desc.get('health', 0)
                            if _hp_gate < 10 or _hp_gate > 1000:
                                _slot_diag['result'] = 'health_gate'
                                continue
                        # Destructible matrices are CHUNK-LOCAL: world pos =
                        # chunk translation + destructible translation
                        # (see AreaDestructibles.__launchEffect)
                        if _catalog_record is None:
                            _origin = (_cm_t.x + _m.translation.x,
                                _cm_t.y + _m.translation.y,
                                _cm_t.z + _m.translation.z)
                        else:
                            _origin = _matrix_point(
                                _m, Math, 0.0, 0.0, 0.0, _cm_t)
                        _world_boxes = ()
                        _contact_radius = 0.0
                        _item_scale = None
                        if _catalog_record is not None:
                            _item_scale = _matrix_item_scale_1513(_m, Math)
                            _world_boxes = _world_catalog_boxes(
                                _catalog_record, _m, _cm_t, Math,
                                _instance_box_index)
                            if not _world_boxes:
                                _slot_diag['result'] = 'boxes_empty'
                                continue
                            for _world_box in _world_boxes:
                                _center, _half_axes = _world_box[:2]
                                _center_radius = ((_center[0] - _origin[0]) ** 2 +
                                    (_center[2] - _origin[2]) ** 2) ** 0.5
                                _horizontal_radius = sum(
                                    (axis[0] * axis[0] + axis[2] * axis[2]) ** 0.5
                                    for axis in _half_axes)
                                _contact_radius = max(
                                    _contact_radius,
                                    _center_radius + _horizontal_radius)
                        _item = (
                            _ti, _origin[0], _origin[1], _origin[2], typ,
                            _filename, desc.get('health', 0),
                            desc.get('mass', 0), _world_boxes,
                            _contact_radius)
                        if _catalog_record is not None:
                            instances[(cid, _ti)] = {
                                'filename': _normalized_filename(_filename),
                                'descriptor_filename': _filename,
                                'kind': _catalog_record['kind'],
                                'boxes': _world_boxes,
                                'item_scale': _item_scale,
                                'box_index': _instance_box_index,
                                'chunk_translation': (
                                    float(_cm_t.x), float(_cm_t.y), float(_cm_t.z)),
                            }
                        if _world_boxes:
                            _item_bins = registry['extended_bins']
                            _bin_keys = _index_catalog_instance_1513(
                                contact_bins, (cid, _ti),
                                instances[(cid, _ti)])
                            for _bin_key in _bin_keys:
                                _item_bins.setdefault(_bin_key, []).append(_item)
                        else:
                            registry['bins'].setdefault(
                                _destructible_bin_key(_item[1], _item[3]), []).append(
                                    _item)
                        registry['count'] += 1
                        registry['max_radius'] = max(
                            registry['max_radius'], _contact_radius)
                        _slot_diag['result'] = 'registered_%s' % (
                            _catalog_record['kind']
                            if _catalog_record is not None else 'tree')
                        _slot_diag['boxes'] = len(_world_boxes)
                    except Exception:
                        raise
                if not _retry_registry:
                    _st['chunks'][cid] = registry
                    _diagnostic_chunk_1513(
                        cid, _native_count, _dfn, registry)
                LOG_DEBUG('DestrTree: chunk registry', cid,
                    registry['count'], 'trees/poles')
            if not registry['count']:
                continue
            for (_ti, _tx, _ty, _tz, _ttyp, _tfn, _thp, _tmass,
                    _world_boxes, _contact_radius) in _nearby_destructibles(
                        registry, pos, vehicle_box):
                dx = _tx - pos.x; dz = _tz - pos.z
                _origin_radius = (
                    _DESTRUCTIBLE_ORIGIN_RADIUS + _contact_radius)
                if dx * dx + dz * dz > _origin_radius * _origin_radius:
                    continue
                _mat_kind = None
                if (_world_boxes and _ttyp in (
                        AreaDestructibles.DESTR_TYPE_FRAGILE,
                        structure_type,
                        AreaDestructibles.DESTR_TYPE_FALLING_ATOM)):
                    # Fragile/structure catalog boxes register exact native
                    # identities for the anchored material probe.  They are not a
                    # collision event and must never destroy or permit movement on
                    # proximity alone.  Stock WGVehiclePhysics (when available) or
                    # the native solid ray below remains the contact authority.
                    continue
                else:
                    if abs(vel) < 1.0:
                        continue
                    fwd = dx * sin_y + dz * cos_y
                    lat = dx * cos_y - dz * sin_y
                    reach_f = hl_f + 0.8 + min(abs(vel) * 0.25, 1.2)
                    if vel < 0:
                        in_reach = -(hl_b + 0.8) <= fwd <= hl_f
                    else:
                        in_reach = -hl_b <= fwd <= reach_f
                    if abs(lat) > hw + 0.5 or not in_reach:
                        continue
                _key = ((cid, _ti, _mat_kind) if _mat_kind is not None
                    else (cid, _ti))
                if _key in _st['felled']:
                    continue
                fall_yaw = yaw if vel >= 0 else (yaw + math.pi)
                _auth = _get_destr_authority()
                if _auth.is_destroyed(cid, _ti, _mat_kind):
                    _st['felled'].add(_key)
                    continue
                _object_pos = Math.Vector3(_tx, _ty, _tz)
                if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE:
                    _ok = _auth.destroy_fragile(
                        spaceID, cid, _ti, _object_pos, False)
                elif _ttyp == structure_type:
                    _ok = _auth.destroy_module(
                        spaceID, cid, _ti, _mat_kind, _object_pos, False)
                elif _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
                    _ok = _auth.destroy_tree(
                        spaceID, cid, _ti, fall_yaw, vel, _object_pos)
                else:
                    _ok = _auth.destroy_column(
                        spaceID, cid, _ti, fall_yaw, vel, _object_pos)
                if not _ok:
                    raise RuntimeError(
                        'native proximity destroy was not accepted: '
                        'chunk=%s item=%s' % (cid, _ti))
                _publish_destroyed(
                    ('fragile' if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE
                     else 'module' if _ttyp == structure_type
                     else 'tree' if _ttyp == AreaDestructibles.DESTR_TYPE_TREE
                     else 'column'),
                    cid, _ti, _object_pos, fall_yaw, vel,
                    _mat_kind)
                _st['felled'].add(_key)
                LOG_DEBUG('DestrTree: FELLED', cid, _ti, 'type', _ttyp,
                    'hp', _thp, 'mass', _tmass, _tfn)
    except Exception:
        raise


def _solid_destructible_candidate_1513(mat_info, contact_pt,
        contact_normal):
    """Accept only a material hit proved to belong to this solid contact."""
    decoded = _decode_mat_info_1513(mat_info)
    if decoded is None:
        return False
    hit_pt, surf_normal, chunkID, itemIndex, matKind, fname = decoded
    if matKind < 71 or matKind > 130:
        return False

    import AreaDestructibles
    desc = AreaDestructibles.g_cache.getDescByFilename(fname)
    if not desc:
        return False
    typ = desc['type']
    if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE:
        modules = desc.get('modules')
        if modules is None or modules.get(matKind) is None:
            return False
    elif typ not in (AreaDestructibles.DESTR_TYPE_FRAGILE,
            getattr(AreaDestructibles, 'DESTR_TYPE_FALLING_ATOM', None)):
        return False

    if _destructible_catalog is not None:
        expected_kind = _catalog_kind_for_type_1513(
            AreaDestructibles, typ)
        world_boxes = _catalog_instance_boxes(
            chunkID, itemIndex, fname, expected_kind, matKind)
        if not world_boxes:
            return False
        for world_box in world_boxes:
            if (_point_in_world_box(contact_pt, world_box) and
                    _point_in_world_box(hit_pt, world_box)):
                return True
        return False

    # Preserve the direct-helper contract for callers without a prebaked map.
    # Installed catalogs instead fail closed on every unknown/mismatched item.
    if (hit_pt - contact_pt).length > _SOLID_CONTACT_RADIUS_1513:
        return False
    try:
        _hit_normal = type(surf_normal)(
            surf_normal.x, surf_normal.y, surf_normal.z)
        if _hit_normal.length <= 0.001:
            return False
        _hit_normal.normalise()
    except (AttributeError, TypeError, ValueError):
        return False
    _dot = (_hit_normal.x * contact_normal.x +
        _hit_normal.y * contact_normal.y +
        _hit_normal.z * contact_normal.z)
    return abs(_dot) >= _SOLID_CONTACT_NORMAL_DOT_1513


def _registered_item_scale_1513(chunkID, itemIndex, filename):
    instance = globals().get('g_offh_destr_instances', {}).get(
        (chunkID, itemIndex))
    if (instance is None or instance['filename'] !=
            _normalized_filename(filename)):
        return None
    # Runtime-registered exact catalog items retain descriptor case.  Legacy
    # direct-helper fixtures without that field still have no case claim to
    # validate and preserve their kinetic contract.
    if (instance.get('descriptor_filename') is not None and
            _instance_descriptor_filename_1513(instance) != filename):
        return None
    return instance['item_scale']


def _registered_shot_exit_1513(chunkID, itemIndex, matKind, filename,
        start_pos, end_pos, contact_pt):
    """Return one exact registered item/module OBB exit on a native hit."""
    instance = globals().get('g_offh_destr_instances', {}).get(
        (int(chunkID), int(itemIndex)))
    if (instance is None or instance['filename'] !=
            _normalized_filename(filename) or
            _instance_descriptor_filename_1513(instance) != filename):
        return None
    if instance['kind'] == 'structure':
        boxes = tuple(box for box in instance['boxes'] if box[2] == matKind)
    else:
        boxes = instance['boxes']
    segment_length = (end_pos - start_pos).length
    if segment_length <= 1.0e-9:
        return None
    contact_distance = (contact_pt - start_pos).length
    exits = []
    for world_box in boxes:
        interval = _segment_world_box_interval(
            start_pos, end_pos, world_box, 0.0)
        if interval is None:
            continue
        entry_distance = interval[0] * segment_length
        exit_distance = interval[1] * segment_length
        if (entry_distance <= contact_distance + _CATALOG_POINT_EPSILON and
                exit_distance + _CATALOG_POINT_EPSILON >= contact_distance):
            exits.append(exit_distance)
    # More than one matching interval is not an exact module/instance answer;
    # do not jump over unknown static geometry between disjoint boxes.
    return exits[0] if len(exits) == 1 else None


def _stock_crushable_1513(mat_info, vel, td, item_scale=None):
    """Apply the exact retail Vehicle kinetic law to one proved contact."""
    decoded = _decode_mat_info_1513(mat_info)
    if decoded is None or td is None:
        return False
    unused_hit, unused_normal, chunkID, itemIndex, matKind, fname = decoded
    import AreaDestructibles
    import DestructiblesCache
    desc = AreaDestructibles.g_cache.getDescByFilename(fname)
    if desc is None:
        return False
    if item_scale is None:
        item_scale = _registered_item_scale_1513(
            chunkID, itemIndex, fname)
    if item_scale is None:
        # A native material result identifies the item, but without its streamed
        # matrix we cannot reproduce scaledDestructibleHealth safely.
        return False
    try:
        item_scale = float(item_scale)
        mass = float(_descriptor_value(
            _descriptor_value(td, 'physics'), 'weight'))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeError(
            '2.3.1.2 vehicle destructible kinetic inputs are unavailable')
    if item_scale <= 0.0 or mass <= 0.0:
        raise RuntimeError('2.3.1.2 destructible kinetic inputs are invalid')
    instant_damage = 0.5 * mass * vel * vel * 0.00015
    if desc['type'] == AreaDestructibles.DESTR_TYPE_STRUCTURE:
        module = desc.get('modules', {}).get(matKind)
        if module is None:
            return False
        ref_health = module['health']
    elif desc['type'] in (AreaDestructibles.DESTR_TYPE_FRAGILE,
            getattr(AreaDestructibles, 'DESTR_TYPE_FALLING_ATOM', None)):
        try:
            instant_damage *= pow(
                mass / float(AreaDestructibles.g_cache.unitVehicleMass),
                float(desc['kineticDamageCorrection']))
            ref_health = desc['health']
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return False
    else:
        return False
    return (DestructiblesCache.scaledDestructibleHealth(
        item_scale, ref_health) < instant_damage)


def _try_destroy_solid_hit(spaceID, segment_start, hit_pt, surf_normal,
        yaw, vel, td=None):
    # wg_collideSegment does not return the descriptor filename needed by the
    # copied contact law. Prefer 2.3.1.2's stock point/normal probe, then retain
    # the mature incoming-ray probe for compiled skins that only resolve in that
    # direction. Both paths share a strict same-contact descriptor gate before
    # native destruction is attempted.
    import BigWorld
    try:
        _normal = type(surf_normal)(
            surf_normal.x, surf_normal.y, surf_normal.z)
        if _normal.length <= 0.001:
            raise RuntimeError(
                '2.3.1.2 static collision surface normal is invalid')
        _normal.normalise()
        _probes = ((hit_pt - _normal.scale(3.0),
            hit_pt + _normal.scale(2.0)),)
        _incoming = hit_pt - segment_start
        if _incoming.length > 0.001:
            _incoming.normalise()
            _probes += ((hit_pt + _incoming.scale(3.0),
                hit_pt - _incoming.scale(2.0)),)
        for _seg_a, _seg_b in _probes:
            _mi = BigWorld.wg_getMatInfoNearPoint(
                spaceID, _seg_a, _seg_b, hit_pt, lambda *a: False)
            _decoded = _decode_mat_info_1513(_mi)
            if not _solid_destructible_candidate_1513(
                    _mi, hit_pt, _normal):
                if _decoded is None:
                    _diagnostic_contact_1513(
                        'static_mat_miss', point=hit_pt,
                        fields=(('speed', '%.3f' % float(vel)),))
                else:
                    _diagnostic_contact_1513(
                        'static_descriptor_reject',
                        _decoded[2], _decoded[3],
                        fields=(('mat', _decoded[4]),
                            ('name', _normalized_filename(_decoded[5]) or '-'),
                            ('speed', '%.3f' % float(vel))))
                continue
            if not _stock_crushable_1513(_mi, vel, td):
                _diagnostic_contact_1513(
                    'static_kinetic_reject', _decoded[2], _decoded[3],
                    fields=(('mat', _decoded[4]),
                        ('speed', '%.3f' % float(vel))))
                continue
            if _try_destroy_destructible(spaceID, _mi, yaw, vel):
                globals()['g_offh_destr_diag_last_static'] = (
                    _decoded[2], _decoded[3],
                    (('path', 'material'), ('mat', _decoded[4]),
                        ('speed', '%.3f' % float(vel))))
                _diagnostic_contact_1513(
                    'static_native_accept', _decoded[2], _decoded[3],
                    fields=(('path', 'material'), ('mat', _decoded[4]),
                        ('speed', '%.3f' % float(vel))))
                return True
        # Some compiled 2.3.1.2 skins participate in wg_collideSegment but do not
        # resolve through wg_getMatInfoNearPoint.  Fall back only when the real
        # contact point lies in exactly one registered catalog item/module.  This
        # path uses the exact stock kinetic gate before publishing the identity.
        _candidate = _catalog_candidate_at_contact(hit_pt)
        if _candidate is None:
            _diagnostic_contact_1513(
                'static_catalog_miss', point=hit_pt,
                fields=(('speed', '%.3f' % float(vel)),))
        elif td is not None:
            _chunk, _item, _mat, _fname, _kind, _item_scale = _candidate
            _synthetic = (True, hit_pt, _normal,
                _mat if _mat is not None else 73, _fname,
                _item, _chunk)
            if not _stock_crushable_1513(
                    _synthetic, vel, td, _item_scale):
                _diagnostic_contact_1513(
                    'static_fallback_kinetic_reject', _chunk, _item,
                    fields=(('kind', _kind), ('mat', _mat),
                        ('speed', '%.3f' % float(vel)),
                        ('scale', '%.5f' % float(_item_scale))))
            elif _try_destroy_destructible(
                    spaceID, _synthetic, yaw, vel):
                globals()['g_offh_destr_diag_last_static'] = (
                    _chunk, _item,
                    (('path', 'catalog'), ('mat', _mat),
                        ('speed', '%.3f' % float(vel))))
                _diagnostic_contact_1513(
                    'static_native_accept', _chunk, _item,
                    fields=(('path', 'catalog'), ('kind', _kind),
                        ('mat', _mat), ('speed', '%.3f' % float(vel))))
                return True
    except Exception:
        raise
    return False


def _shot_kind_1513(shot):
    shell = _descriptor_value(shot, 'shell', {})
    kind = _descriptor_value(shell, 'kind')
    return str(kind) if kind in (
        'ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR',
        'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE') else None


def _shot_through_health_1513(desc, mat_kind):
    import AreaDestructibles
    if desc['type'] == AreaDestructibles.DESTR_TYPE_STRUCTURE:
        module = desc.get('modules', {}).get(mat_kind)
        return None if module is None else float(module.get('health', 0.0))
    return float(desc.get('health', 0.0))


def _scaled_shot_through_health_1513(desc, mat_kind, item_scale):
    if desc is None:
        return None
    health = _shot_through_health_1513(desc, mat_kind)
    if health is None:
        return None
    if item_scale is None:
        return None
    try:
        import DestructiblesCache
        return float(DestructiblesCache.scaledDestructibleHealth(
            float(item_scale), health))
    except (AttributeError, ImportError, TypeError, ValueError):
        return None


def _typed_shot_result_1513(world_distance, stop_distance=None,
        piercing_loss=0.0, continue_from=None, loss_distance=None,
        stopped_by_destructible=False):
    return {
        'world_distance': float(world_distance),
        'stop_distance': (None if stop_distance is None
            else float(stop_distance)),
        'piercing_loss': float(piercing_loss),
        'continue_from': (None if continue_from is None
            else float(continue_from)),
        'loss_distance': (None if loss_distance is None
            else float(loss_distance)),
        'stopped_by_destructible': bool(stopped_by_destructible),
    }


def shot_world_distance(bigworld, spaceID, start_pos, end_pos, dir_vec,
        shot=None):
    """Resolve the first native or exact-catalog destructible on a shell ray.

    Passing a shot opts into typed traversal metadata.  Omitting it preserves the
    legacy float contract for diagnostics and old fixtures.
    """
    import math
    world_dist = 99999.0
    world_collision = bigworld.wg_collideSegment(
        spaceID, start_pos, end_pos, 128)
    shot_yaw = math.atan2(dir_vec.x, dir_vec.z)
    if world_collision is not None:
        world_dist = (world_collision[0] - start_pos).length
        mat_info = bigworld.wg_getMatInfoNearPoint(
            spaceID, start_pos,
            world_collision[0] + dir_vec.scale(0.3),
            world_collision[0], lambda *unused: False)
        decoded = _decode_mat_info_1513(mat_info)
        if _try_destroy_destructible(
                spaceID, mat_info, shot_yaw, 12.0, True):
            if decoded is not None:
                _diagnostic_contact_1513(
                    'shot_material_accept', decoded[2], decoded[3],
                    fields=(('mat', decoded[4]),))
            if shot is not None:
                desc = __import__('AreaDestructibles').g_cache.getDescByFilename(
                    decoded[5])
                item_scale = _registered_item_scale_1513(
                    decoded[2], decoded[3], decoded[5])
                health = _scaled_shot_through_health_1513(
                    desc, decoded[4], item_scale)
                can_continue = (_shot_kind_1513(shot) in _SHOT_AP_KINDS_1513 and
                    health is not None and health <= _SHOT_THROUGH_MAX_HP_1513)
                if can_continue:
                    registered_exit = _registered_shot_exit_1513(
                        decoded[2], decoded[3], decoded[4], decoded[5],
                        start_pos, end_pos, decoded[0])
                    if registered_exit is None:
                        if _destructible_catalog is not None:
                            return _typed_shot_result_1513(
                                world_dist, stop_distance=world_dist,
                                stopped_by_destructible=True)
                        continue_from = world_dist + 0.6
                    else:
                        continue_from = (registered_exit +
                            _SHOT_RAY_EPSILON)
                    return _typed_shot_result_1513(
                        99999.0, piercing_loss=_SHOT_THROUGH_MIN_REDUCTION_1513,
                        continue_from=continue_from, loss_distance=world_dist)
                return _typed_shot_result_1513(
                    world_dist, stop_distance=world_dist,
                    stopped_by_destructible=True)
            # Destructible broken by the shell: re-cast past the debris.
            second = bigworld.wg_collideSegment(
                spaceID, world_collision[0] + dir_vec.scale(0.6),
                end_pos, 128)
            return ((second[0] - start_pos).length
                if second is not None else 99999.0)

    # Dynamic destructible BSPs frequently do not participate in mask 128, and
    # anonymous 2.3.1.2 slots also make the point-material query return no usable
    # filename.  Resolve only the nearest unique streamed catalog OBB.  A real
    # static hit caps the ray, so an object behind an unrelated wall is never
    # destroyed by this fallback.
    catalog_hit = None
    if world_collision is not None:
        point_candidate = None
        if decoded is not None:
            point_candidate = _catalog_candidate_for_native_identity_1513(
                decoded[2], decoded[3], decoded[4], decoded[0])
        if point_candidate is None:
            point_candidate = _catalog_candidate_at_contact(world_collision[0])
        if (point_candidate is not None and
                _get_destr_authority().is_destroyed(
                    point_candidate[0], point_candidate[1],
                    point_candidate[2])):
            # The next static surface may sit within the small point-identity
            # tolerance of the OBB that this shell just destroyed.  Never map
            # that backing surface back onto an already-removed module.
            point_candidate = None
        if point_candidate is not None:
            point = world_collision[0]
            exit_distance = _registered_shot_exit_1513(
                point_candidate[0], point_candidate[1], point_candidate[2],
                point_candidate[3], start_pos, end_pos, point)
            catalog_hit = {
                'candidate': point_candidate + ((
                    float(point.x), float(point.y), float(point.z)),),
                'distance': world_dist,
                'exit_distance': (world_dist if exit_distance is None
                    else exit_distance),
                'exit_proved': exit_distance is not None,
                'ambiguous': False,
            }
    if catalog_hit is None:
        catalog_hit = _catalog_shot_intersection(
            start_pos, end_pos,
            world_dist if world_collision is not None else None)
    if catalog_hit is None:
        if world_collision is not None:
            _diagnostic_contact_1513(
                'shot_catalog_miss', point=world_collision[0])
        return (_typed_shot_result_1513(
            world_dist, stop_distance=(world_dist
                if world_collision is not None else None))
            if shot is not None else world_dist)
    if catalog_hit['ambiguous']:
        _diagnostic_contact_1513(
            'shot_catalog_ambiguous', point=(
                world_collision[0] if world_collision is not None else start_pos))
        ambiguous_distance = float(catalog_hit['distance'])
        return (_typed_shot_result_1513(
            ambiguous_distance, stop_distance=ambiguous_distance,
            stopped_by_destructible=True)
            if shot is not None else ambiguous_distance)

    candidate = catalog_hit['candidate']
    chunk_id, item_index, mat_kind, unused_filename, kind = candidate[:5]
    center = candidate[6]
    point = type(start_pos)(center[0], center[1], center[2])
    normal = type(start_pos)(0.0, 1.0, 0.0)
    mat_info = (True, point, normal,
        mat_kind if mat_kind is not None else 73,
        candidate[3], item_index, chunk_id)
    if not _try_destroy_destructible(
            spaceID, mat_info, shot_yaw, 12.0, True):
        _diagnostic_contact_1513(
            'shot_native_reject', chunk_id, item_index,
            fields=(('kind', kind), ('mat', mat_kind)))
        return (_typed_shot_result_1513(
            world_dist, stop_distance=(world_dist
                if world_collision is not None else None))
            if shot is not None else world_dist)
    _diagnostic_contact_1513(
        'shot_catalog_accept', chunk_id, item_index,
        fields=(('kind', kind), ('mat', mat_kind)))
    if shot is not None:
        import AreaDestructibles
        desc = AreaDestructibles.g_cache.getDescByFilename(candidate[3])
        health = _scaled_shot_through_health_1513(
            desc, mat_kind, candidate[5])
        can_continue = (_shot_kind_1513(shot) in _SHOT_AP_KINDS_1513 and
            health is not None and health <= _SHOT_THROUGH_MAX_HP_1513)
        if can_continue and catalog_hit.get('exit_proved', True):
            return _typed_shot_result_1513(
                99999.0, piercing_loss=_SHOT_THROUGH_MIN_REDUCTION_1513,
                continue_from=(catalog_hit['exit_distance'] +
                    _SHOT_RAY_EPSILON),
                loss_distance=catalog_hit['distance'])
        return _typed_shot_result_1513(
            catalog_hit['distance'], stop_distance=catalog_hit['distance'],
            stopped_by_destructible=True)
    # Re-cast beyond the proved OBB just like the legacy material path.  This
    # lets a shell continue after a dynamic-only prop while a surviving static
    # backing remains authoritative for structures during native replacement.
    if not catalog_hit.get('exit_proved', True):
        return catalog_hit['distance']
    recast_distance = catalog_hit['exit_distance'] + _SHOT_RAY_EPSILON
    recast_start = start_pos + dir_vec.scale(recast_distance)
    second = bigworld.wg_collideSegment(
        spaceID, recast_start, end_pos, 128)
    return ((second[0] - start_pos).length
        if second is not None else 99999.0)
