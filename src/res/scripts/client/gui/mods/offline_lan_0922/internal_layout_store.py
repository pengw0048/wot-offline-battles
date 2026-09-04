import copy
import json
import time

try:
    from gui.mods.offline_lan_0922.user_config import atomic_write_user_file, user_data_path
except Exception:
    from user_config import atomic_write_user_file, user_data_path


STORE_FILE_NAME = 'internal_layout_overrides.json'
_FORMAT_KEY = 1
_DISABLED_OVERRIDE_PREFIXES = ('in_game_',)
_CACHE = [None]


def _now():
    return int(time.time())


def _safe_text(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)
    except Exception:
        return str(value)


def _zone_key(target_or_kind, entity=None, parent=None, zone_id=None):
    if isinstance(target_or_kind, dict):
        target = target_or_kind
        kind = target.get('kind', 'module')
        entity = target.get('entity', '')
        parent = target.get('parent', '')
        zone_id = target.get('zone_id', '')
    else:
        kind = target_or_kind
    return '%s|%s|%s|%s' % (
        _safe_text(kind), _safe_text(entity), _safe_text(parent),
        _safe_text(zone_id))


def zone_key(target):
    return _zone_key(target)


def _empty_document():
    return {
        'format': _FORMAT_KEY,
        'updated_at': _now(),
        'vehicles': {},
    }


def _normalise_vector(value, fallback):
    try:
        result = (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return tuple(fallback)
    return result


def _normalise_document(document):
    if not isinstance(document, dict):
        return _empty_document()
    vehicles = document.get('vehicles')
    if not isinstance(vehicles, dict):
        vehicles = {}
    result = _empty_document()
    result['updated_at'] = int(document.get('updated_at', result['updated_at']))
    for fingerprint, vehicle_record in vehicles.items():
        if not isinstance(vehicle_record, dict):
            continue
        zones = vehicle_record.get('zones')
        if not isinstance(zones, dict):
            zones = {}
        clean_zones = {}
        for key, record in zones.items():
            if not isinstance(record, dict):
                continue
            center = _normalise_vector(record.get('center'), (0.0, 0.0, 0.0))
            half = _normalise_vector(record.get('half_extents'), (0.1, 0.1, 0.1))
            if min(half) <= 0.0:
                continue
            clean = dict(record)
            clean['center'] = center
            clean['half_extents'] = half
            clean['model_signature'] = _safe_text(record.get('model_signature', ''))
            clean['rotation_yaw_degrees'] = float(record.get('rotation_yaw_degrees', 0.0) or 0.0)
            clean_zones[_safe_text(key)] = clean
        custom_zones = vehicle_record.get('custom_zones')
        if not isinstance(custom_zones, dict):
            custom_zones = {}
        clean_custom = {}
        for key, record in custom_zones.items():
            if not isinstance(record, dict):
                continue
            center = _normalise_vector(record.get('center'), (0.0, 0.0, 0.0))
            half = _normalise_vector(record.get('half_extents'), (0.1, 0.1, 0.1))
            if min(half) <= 0.0:
                continue
            clean = dict(record)
            clean['center'] = center
            clean['half_extents'] = half
            clean['model_signature'] = _safe_text(record.get('model_signature', ''))
            clean['zone_id'] = _safe_text(record.get('zone_id', ''))
            clean['kind'] = _safe_text(record.get('kind', 'module'))
            clean['entity'] = _safe_text(record.get('entity', ''))
            clean['parent'] = _safe_text(record.get('parent', ''))
            clean['rotation_yaw_degrees'] = float(record.get('rotation_yaw_degrees', 0.0) or 0.0)
            clean['subtype'] = _safe_text(record.get('subtype', ''))
            clean['fire_eligible'] = bool(record.get('fire_eligible', True))
            clean_custom[_safe_text(key)] = clean
        result['vehicles'][_safe_text(fingerprint)] = {
            'vehicle_type': _safe_text(vehicle_record.get('vehicle_type', '')),
            'model_signatures': dict(vehicle_record.get('model_signatures', {}) or {}),
            'updated_at': int(vehicle_record.get('updated_at', result['updated_at'])),
            'zones': clean_zones,
            'custom_zones': clean_custom,
        }
    return result


def _read_document():
    path = user_data_path(STORE_FILE_NAME)
    try:
        handle = open(path, 'rb')
        try:
            payload = handle.read()
        finally:
            handle.close()
        return _normalise_document(json.loads(payload.decode('utf-8')))
    except Exception:
        return _empty_document()


def load_document(force=False):
    if force or _CACHE[0] is None:
        _CACHE[0] = _read_document()
    return _CACHE[0]


def _validate_path(path):
    handle = open(path, 'rb')
    try:
        document = json.loads(handle.read().decode('utf-8'))
    finally:
        handle.close()
    if not isinstance(document, dict):
        raise ValueError('Internal layout override root must be an object')
    value = document.get('format', 0)
    if not value:
        for key, item in document.items():
            if str(key).lower().startswith('schema'):
                value = item
                break
    if int(value or 0) != _FORMAT_KEY:
        raise ValueError('Unsupported internal layout data')


def save_document(document):
    document = _normalise_document(document)
    document['updated_at'] = _now()
    payload = json.dumps(document, ensure_ascii=True, indent=2,
        sort_keys=True).encode('utf-8')
    path = atomic_write_user_file(STORE_FILE_NAME, payload, _validate_path)
    _CACHE[0] = document
    return path


def get_override(fingerprint, target):
    document = load_document()
    vehicle_record = document.get('vehicles', {}).get(_safe_text(fingerprint))
    if not isinstance(vehicle_record, dict):
        return None
    record = vehicle_record.get('zones', {}).get(_zone_key(target))
    if not isinstance(record, dict):
        return None
    if _safe_text(record.get('source', '')).startswith(_DISABLED_OVERRIDE_PREFIXES):
        return None
    stored_signature = _safe_text(record.get('model_signature', ''))
    current_signature = _safe_text(target.get('model_signature', ''))
    if stored_signature and current_signature and stored_signature != current_signature:
        return None
    return record


def _clamp_geometry(center, half, bounds):
    if not bounds:
        return tuple(center), tuple(half)
    minimum, maximum = bounds
    span = tuple(float(maximum[i]) - float(minimum[i]) for i in range(3))
    result_half = []
    result_center = []
    for axis in range(3):
        axis_half = max(0.02, min(float(half[axis]), span[axis] * 0.48))
        low = float(minimum[axis]) + axis_half
        high = float(maximum[axis]) - axis_half
        axis_center = float(center[axis])
        if high < low:
            axis_center = (float(minimum[axis]) + float(maximum[axis])) * 0.5
            axis_half = max(0.02, span[axis] * 0.45)
        else:
            axis_center = min(high, max(low, axis_center))
        result_half.append(axis_half)
        result_center.append(axis_center)
    return tuple(result_center), tuple(result_half)


def _cap_half_extents_for_target(target, half_extents):
    cap = target.get('physical_half_cap_m')
    try:
        if cap is None or len(cap) != 3:
            return tuple(half_extents), False
        margin = 1.15
        if (target.get('entity') == 'surveyingDevice' or
                target.get('primitive_policy') == 'compact_fixed_gun_traverse'):
            margin = 1.0
        corrected = tuple(max(0.015, min(float(half_extents[axis]),
            float(cap[axis]) * margin)) for axis in range(3))
        changed = any(abs(float(corrected[axis]) -
            float(half_extents[axis])) > 0.0001 for axis in range(3))
        return corrected, changed
    except Exception:
        return tuple(half_extents), False


def _capsule_half_extents(radius, half_length, axis):
    axis = str(axis or 'y').lower()
    radius = max(0.001, float(radius))
    axis_half = max(0.0, float(half_length)) + radius
    if axis == 'x':
        return (axis_half, radius, radius)
    if axis == 'z':
        return (radius, radius, axis_half)
    return (radius, axis_half, radius)


def _transform_primitive(primitive, old_center, old_half, new_center, new_half):
    result = dict(primitive)
    scales = tuple(float(new_half[i]) / max(0.0001, float(old_half[i]))
        for i in range(3))
    primitive_center = primitive.get('center', old_center)
    transformed_center = tuple(float(new_center[i]) +
        (float(primitive_center[i]) - float(old_center[i])) * scales[i]
        for i in range(3))
    result['center'] = transformed_center
    shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
    yaw = _normalise_yaw(primitive.get('rotation_yaw_degrees', 0.0))
    if shape == 'sphere':
        radius_scale = min(scales)
        radius = max(0.02, float(primitive.get('radius', 0.05)) * radius_scale)
        result['radius'] = radius
        result['half_extents'] = (radius, radius, radius)
        result['minimum'] = tuple(transformed_center[i] - radius for i in range(3))
        result['maximum'] = tuple(transformed_center[i] + radius for i in range(3))
    elif shape == 'ellipsoid':
        old_radii = primitive.get('radii', primitive.get(
            'half_extents', old_half))
        radii = tuple(max(0.015, float(old_radii[i]) * scales[i])
            for i in range(3))
        result['radii'] = radii
        result['half_extents'] = radii
        result['minimum'], result['maximum'] = _rotated_box_bounds(
            transformed_center, radii, yaw)
    elif shape == 'capsule':
        axis = str(primitive.get('axis', 'y') or 'y').lower()
        if axis not in ('x', 'y', 'z'):
            axis = 'y'
        axis_index = {'x': 0, 'y': 1, 'z': 2}[axis]
        radial_indices = [index for index in range(3)
            if index != axis_index]
        radial_scale = min(scales[index] for index in radial_indices)
        radius = max(0.015, float(primitive.get('radius', 0.05)) *
            radial_scale)
        half_length = max(0.0, float(primitive.get(
            'half_length', 0.0)) * scales[axis_index])
        primitive_half = _capsule_half_extents(radius, half_length, axis)
        result['axis'] = axis
        result['radius'] = radius
        result['half_length'] = half_length
        result['half_extents'] = primitive_half
        result['minimum'], result['maximum'] = _rotated_box_bounds(
            transformed_center, primitive_half, yaw)
    else:
        old_primitive_half = primitive.get('half_extents', old_half)
        primitive_half = tuple(max(0.015,
            float(old_primitive_half[i]) * scales[i]) for i in range(3))
        result['half_extents'] = primitive_half
        result['minimum'], result['maximum'] = _rotated_box_bounds(
            transformed_center, primitive_half, yaw)
    return result

def _normalise_yaw(value):
    try:
        result = float(value) % 360.0
        if result > 180.0:
            result -= 360.0
        return result
    except Exception:
        return 0.0


def _rotated_box_bounds(center, half, yaw_degrees):
    import math
    angle = math.radians(float(yaw_degrees))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    points = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                dx = float(half[0]) * sx
                dz = float(half[2]) * sz
                points.append((float(center[0]) + dx * cosine - dz * sine,
                    float(center[1]) + float(half[1]) * sy,
                    float(center[2]) + dx * sine + dz * cosine))
    return (tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)))


def _rotate_point_y(point, center, degrees):
    import math
    angle = math.radians(float(degrees or 0.0))
    if abs(angle) <= 0.0000001:
        return tuple(float(value) for value in point)
    dx = float(point[0]) - float(center[0])
    dz = float(point[2]) - float(center[2])
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (float(center[0]) + dx * cosine - dz * sine,
        float(point[1]), float(center[2]) + dx * sine + dz * cosine)


def set_target_rotation(target, yaw_degrees, calibration_status='runtime_rotated'):
    yaw = _normalise_yaw(yaw_degrees)
    old_yaw = _normalise_yaw(target.get('rotation_yaw_degrees', 0.0))
    delta = _normalise_yaw(yaw - old_yaw)
    target_center = tuple(target.get('center', (0.0, 0.0, 0.0)))
    target['rotation_yaw_degrees'] = yaw
    rotated = []
    for primitive in target.get('primitives', ()):
        item = dict(primitive)
        shape = str(item.get('shape', 'aabb') or 'aabb').lower()
        center = tuple(item.get('center', target_center))
        if abs(delta) > 0.0000001:
            center = _rotate_point_y(center, target_center, delta)
        item['center'] = center
        if shape != 'sphere':
            primitive_yaw = _normalise_yaw(item.get(
                'rotation_yaw_degrees', 0.0))
            item['rotation_yaw_degrees'] = _normalise_yaw(
                primitive_yaw + delta)
        half = tuple(item.get('half_extents', target.get(
            'half_extents', (0.1, 0.1, 0.1))))
        if shape == 'sphere':
            radius = max(0.001, float(item.get('radius', min(half))))
            item['minimum'] = tuple(center[axis] - radius
                for axis in range(3))
            item['maximum'] = tuple(center[axis] + radius
                for axis in range(3))
        else:
            item['minimum'], item['maximum'] = _rotated_box_bounds(
                center, half, item.get('rotation_yaw_degrees', 0.0))
        rotated.append(item)
    target['primitives'] = tuple(rotated)
    if rotated:
        target['minimum'] = tuple(min(item['minimum'][axis]
            for item in rotated) for axis in range(3))
        target['maximum'] = tuple(max(item['maximum'][axis]
            for item in rotated) for axis in range(3))
    target['calibration_status'] = calibration_status
    return target


def set_target_geometry(target, center, half_extents, bounds=None,
        calibration_status='runtime_adjusted'):
    old_center = tuple(target.get('center', center))
    old_half = tuple(target.get('half_extents', half_extents))
    requested_half = tuple(float(value) for value in half_extents)
    capped_half, physical_cap_corrected = _cap_half_extents_for_target(
        target, requested_half)
    center, half_extents = _clamp_geometry(center, capped_half, bounds)
    bounds_corrected = any(abs(float(half_extents[axis]) -
        float(capped_half[axis])) > 0.0001 for axis in range(3))
    size_corrected = bool(physical_cap_corrected or bounds_corrected)
    primitives = tuple(_transform_primitive(
        primitive, old_center, old_half, center, half_extents)
        for primitive in target.get('primitives', ()))
    target['center'] = tuple(center)
    target['half_extents'] = tuple(half_extents)
    target['final_half_extents_m'] = tuple(half_extents)
    target['primitives'] = primitives
    if primitives:
        target['minimum'] = tuple(min(item['minimum'][axis]
            for item in primitives) for axis in range(3))
        target['maximum'] = tuple(max(item['maximum'][axis]
            for item in primitives) for axis in range(3))
        target['geometry_volume_m3'] = sum(_primitive_volume(item)
            for item in primitives)
    else:
        target['minimum'] = tuple(center[i] - half_extents[i] for i in range(3))
        target['maximum'] = tuple(center[i] + half_extents[i] for i in range(3))
    correction_reasons = list(target.get('size_correction_reasons', ()) or ())
    if physical_cap_corrected and 'saved_override_physical_cap' not in correction_reasons:
        correction_reasons.append('saved_override_physical_cap')
    if bounds_corrected and 'saved_override_component_bounds' not in correction_reasons:
        correction_reasons.append('saved_override_component_bounds')
    target['size_correction_reasons'] = tuple(correction_reasons)
    target['size_correction_applied'] = bool(
        target.get('size_correction_applied', False) or size_corrected)
    target['local_transform'] = {'translation': tuple(center)}
    target['calibration_status'] = (calibration_status + '_size_corrected'
        if size_corrected else calibration_status)
    target['override_size_corrected'] = bool(size_corrected)
    return target


def _primitive_volume(primitive):
    shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
    if shape == 'sphere':
        radius = float(primitive.get('radius', 0.0))
        return 4.0 * 3.141592653589793 * radius * radius * radius / 3.0
    if shape == 'ellipsoid':
        radii = primitive.get('radii', primitive.get(
            'half_extents', (0.0, 0.0, 0.0)))
        return (4.0 * 3.141592653589793 * float(radii[0]) *
            float(radii[1]) * float(radii[2]) / 3.0)
    if shape == 'capsule':
        radius = float(primitive.get('radius', 0.0))
        half_length = float(primitive.get('half_length', 0.0))
        return (3.141592653589793 * radius * radius *
            (half_length * 2.0) + 4.0 * 3.141592653589793 *
            radius * radius * radius / 3.0)
    half = primitive.get('half_extents', (0.0, 0.0, 0.0))
    return 8.0 * float(half[0]) * float(half[1]) * float(half[2])


def apply_target_override(fingerprint, target, bounds=None):
    record = get_override(fingerprint, target)
    if record is None:
        target.setdefault('calibration_status', 'profile_seed_unverified')
        return target
    set_target_geometry(target, record['center'], record['half_extents'], bounds,
        'user_calibrated')
    set_target_rotation(target, record.get('rotation_yaw_degrees', 0.0),
        'user_calibrated')
    target['calibration_updated_at'] = int(record.get('updated_at', 0))
    target['calibration_source'] = record.get('source',
        'manual')
    return target


def save_target_override(fingerprint, vehicle_type, target,
        model_signatures=None):
    document = copy.deepcopy(load_document())
    vehicles = document.setdefault('vehicles', {})
    fingerprint = _safe_text(fingerprint)
    vehicle_record = vehicles.setdefault(fingerprint, {
        'vehicle_type': _safe_text(vehicle_type),
        'model_signatures': {},
        'updated_at': _now(),
        'zones': {},
        'custom_zones': {},
    })
    vehicle_record['vehicle_type'] = _safe_text(vehicle_type)
    if model_signatures:
        vehicle_record['model_signatures'] = dict(model_signatures)
    vehicle_record['updated_at'] = _now()
    vehicle_record.setdefault('zones', {})[_zone_key(target)] = {
        'kind': _safe_text(target.get('kind', 'module')),
        'entity': _safe_text(target.get('entity', '')),
        'parent': _safe_text(target.get('parent', '')),
        'zone_id': _safe_text(target.get('zone_id', '')),
        'center': tuple(float(value) for value in target.get('center', (0, 0, 0))),
        'half_extents': tuple(float(value) for value in target.get(
            'half_extents', (0.1, 0.1, 0.1))),
        'model_signature': _safe_text(target.get('model_signature', '')),
        'rotation_yaw_degrees': _normalise_yaw(target.get('rotation_yaw_degrees', 0.0)),
        'updated_at': _now(),
        'source': 'manual',
    }
    return save_document(document)


def reset_target_override(fingerprint, target):
    document = copy.deepcopy(load_document())
    vehicle_record = document.get('vehicles', {}).get(_safe_text(fingerprint))
    if not isinstance(vehicle_record, dict):
        return False
    zones = vehicle_record.get('zones', {})
    removed = zones.pop(_zone_key(target), None)
    if not zones:
        document.get('vehicles', {}).pop(_safe_text(fingerprint), None)
    if removed is None:
        return False
    save_document(document)
    return True


def _vehicle_record(document, fingerprint, vehicle_type=''):
    vehicles = document.setdefault('vehicles', {})
    fingerprint = _safe_text(fingerprint)
    record = vehicles.setdefault(fingerprint, {
        'vehicle_type': _safe_text(vehicle_type),
        'model_signatures': {},
        'updated_at': _now(),
        'zones': {},
        'custom_zones': {},
    })
    record.setdefault('zones', {})
    record.setdefault('custom_zones', {})
    if vehicle_type:
        record['vehicle_type'] = _safe_text(vehicle_type)
    record['updated_at'] = _now()
    return record


def custom_zone_records(fingerprint):
    document = load_document()
    vehicle_record = document.get('vehicles', {}).get(_safe_text(fingerprint))
    if not isinstance(vehicle_record, dict):
        return ()
    records = vehicle_record.get('custom_zones', {})
    if not isinstance(records, dict):
        return ()
    result = []
    for key in sorted(records):
        record = dict(records[key])
        if _safe_text(record.get('source', '')).startswith(_DISABLED_OVERRIDE_PREFIXES):
            continue
        record['_store_key'] = key
        result.append(record)
    return tuple(result)


def append_custom_targets(fingerprint, targets, bounds_by_parent):
    records = custom_zone_records(fingerprint)
    if not records:
        return ()
    result = []
    source_targets = list(targets or ())
    for record in records:
        source = None
        for candidate in source_targets:
            if (candidate.get('kind') == record.get('kind') and
                    candidate.get('entity') == record.get('entity') and
                    candidate.get('parent') == record.get('parent')):
                source = candidate
                break
        if source is None:
            for candidate in source_targets:
                if (candidate.get('kind') == record.get('kind') and
                        candidate.get('entity') == record.get('entity')):
                    source = candidate
                    break
        if source is None:
            for candidate in source_targets:
                if candidate.get('kind') == record.get('kind'):
                    source = candidate
                    break
        if source is None:
            continue
        target = copy.deepcopy(source)
        target['kind'] = _safe_text(record.get('kind', target.get('kind', 'module')))
        target['entity'] = _safe_text(record.get('entity', target.get('entity', '')))
        target['parent'] = _safe_text(record.get('parent', target.get('parent', 'hull')))
        target['model_signature'] = _safe_text(record.get(
            'model_signature', target.get('model_signature', '')))
        target['zone_id'] = _safe_text(record.get('zone_id', 'custom_zone'))
        target['custom_zone'] = True
        target['custom_store_key'] = _safe_text(record.get('_store_key', ''))
        target['geometry_source'] = 'manual'
        target['calibration_source'] = 'manual'
        set_target_geometry(target, record.get('center'),
            record.get('half_extents'), bounds_by_parent.get(
                target.get('parent')), 'user_custom_zone')
        set_target_rotation(target, record.get('rotation_yaw_degrees', 0.0),
            'user_custom_zone')
        target['subtype'] = _safe_text(record.get('subtype', target.get('subtype', '')))
        target['fire_eligible'] = bool(record.get('fire_eligible', target.get('fire_eligible', True)))
        target['calibration_updated_at'] = int(record.get('updated_at', 0))
        result.append(target)
    return tuple(result)


def create_custom_zone(fingerprint, vehicle_type, source_target,
        model_signatures=None):
    document = copy.deepcopy(load_document())
    vehicle_record = _vehicle_record(document, fingerprint, vehicle_type)
    if model_signatures:
        vehicle_record['model_signatures'] = dict(model_signatures)
    custom = vehicle_record.setdefault('custom_zones', {})
    entity = _safe_text(source_target.get('entity', 'module'))
    parent = _safe_text(source_target.get('parent', 'hull'))
    kind = _safe_text(source_target.get('kind', 'module'))
    base = 'custom_%s_%s' % (entity, parent)
    index = 1
    existing_ids = set(_safe_text(record.get('zone_id', ''))
        for record in custom.values() if isinstance(record, dict))
    zone_id = '%s_%02d' % (base, index)
    while zone_id in existing_ids:
        index += 1
        zone_id = '%s_%02d' % (base, index)
    key = _zone_key(kind, entity, parent, zone_id)
    custom[key] = {
        'kind': kind,
        'entity': entity,
        'parent': parent,
        'zone_id': zone_id,
        'center': tuple(float(value) for value in source_target.get(
            'center', (0.0, 0.0, 0.0))),
        'half_extents': tuple(float(value) for value in source_target.get(
            'half_extents', (0.1, 0.1, 0.1))),
        'model_signature': _safe_text(source_target.get('model_signature', '')),
        'rotation_yaw_degrees': _normalise_yaw(source_target.get('rotation_yaw_degrees', 0.0)),
        'subtype': _safe_text(source_target.get('subtype', '')),
        'fire_eligible': bool(source_target.get('fire_eligible', True)),
        'updated_at': _now(),
        'source': 'manual',
    }
    path = save_document(document)
    return path, zone_id


def save_custom_zone(fingerprint, vehicle_type, target,
        model_signatures=None):
    document = copy.deepcopy(load_document())
    vehicle_record = _vehicle_record(document, fingerprint, vehicle_type)
    if model_signatures:
        vehicle_record['model_signatures'] = dict(model_signatures)
    custom = vehicle_record.setdefault('custom_zones', {})
    key = _safe_text(target.get('custom_store_key', ''))
    if not key:
        key = _zone_key(target)
    custom[key] = {
        'kind': _safe_text(target.get('kind', 'module')),
        'entity': _safe_text(target.get('entity', '')),
        'parent': _safe_text(target.get('parent', '')),
        'zone_id': _safe_text(target.get('zone_id', '')),
        'center': tuple(float(value) for value in target.get('center', (0, 0, 0))),
        'half_extents': tuple(float(value) for value in target.get(
            'half_extents', (0.1, 0.1, 0.1))),
        'model_signature': _safe_text(target.get('model_signature', '')),
        'rotation_yaw_degrees': _normalise_yaw(target.get('rotation_yaw_degrees', 0.0)),
        'subtype': _safe_text(target.get('subtype', '')),
        'fire_eligible': bool(target.get('fire_eligible', True)),
        'updated_at': _now(),
        'source': 'manual',
    }
    return save_document(document)


def delete_custom_zone(fingerprint, target):
    document = copy.deepcopy(load_document())
    vehicle_record = document.get('vehicles', {}).get(_safe_text(fingerprint))
    if not isinstance(vehicle_record, dict):
        return False
    custom = vehicle_record.get('custom_zones', {})
    key = _safe_text(target.get('custom_store_key', '')) or _zone_key(target)
    removed = custom.pop(key, None)
    if removed is None:
        return False
    if not custom and not vehicle_record.get('zones'):
        document.get('vehicles', {}).pop(_safe_text(fingerprint), None)
    save_document(document)
    return True

def clear_cache():
    _CACHE[0] = None
