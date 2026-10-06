"""Persistent pure geometry for the streamed #1513 destructible sensor.

The sensor retains every native identity proof and effect/publication ledger.
Only validated numeric geometry is copied here; calls never invoke BigWorld.
"""
from __future__ import print_function

_owner = None
_disabled = False


def point(value):
    try:
        return float(value.x), float(value.y), float(value.z)
    except AttributeError:
        return tuple(float(value[index]) for index in range(3))


def pose(position, yaw, bbox, travel=0., motion_yaw=None, pitch=0., roll=0.):
    return (point(position), float(yaw), float(pitch), float(roll), float(travel),
            None if motion_yaw is None else float(motion_yaw),
            point(bbox[0]), point(bbox[1]))


def wire_key(value):
    return float(value[0]), int(value[1])


class Geometry(object):
    def __init__(self, backend, sensor):
        self.backend = backend
        self.handle = backend.destr_open()
        self.chunks = {}
        if self.handle is None:
            raise RuntimeError('Native destructible index was not created')
        try:
            catalog = sensor._destructible_catalog or {}
            bins = catalog.get('baked_shot_bins', {})
            self.update(4, tuple((cell, tuple(wire_key(key) for key in members))
                                for cell, members in bins.items()))
            for key, instance in sensor.__dict__.get(
                    'g_offh_destr_instances', {}).items():
                self.instance(sensor, key, instance)
        except Exception:
            self.close()
            raise

    def close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            self.backend.destr_close(handle)
        self.chunks.clear()

    def update(self, opcode, payload):
        if self.backend.destr_update(self.handle, opcode, payload) != 1:
            raise RuntimeError('Native destructible index mutation was rejected')

    def query(self, opcode, payload):
        return self.backend.destr_query(self.handle, opcode, payload)

    def instance(self, sensor, key, instance):
        kind = {'fragile': 0, 'structure': 1, 'falling': 2}[instance['kind']]
        if sensor._destructible_isolated_1513(*key):
            self.remove(*key)
            return
        bins = instance.get('bin_keys')
        if bins is None:
            bins = tuple(cell for cell, members in sensor.__dict__.get(
                'g_offh_destr_contact_bins', {}).items() if key in members)
        self.update(0, (wire_key(key), kind, instance['boxes'], bins))

    def remove(self, chunk_id, item_index=None):
        if item_index is None:
            self.update(3, float(chunk_id))
            self.chunks.pop(int(chunk_id), None)
        else:
            self.update(1, (float(chunk_id), int(item_index)))

    def chunk(self, sensor, chunk_id, registry, tree_type=None):
        current = self.chunks.get(int(chunk_id))
        if current is not None and current[0] is registry:
            return current[1]
        import AreaDestructibles
        if tree_type is None:
            tree_type = AreaDestructibles.DESTR_TYPE_TREE
        catalog_types = (AreaDestructibles.DESTR_TYPE_FRAGILE,
                         getattr(AreaDestructibles, 'DESTR_TYPE_STRUCTURE', None),
                         AreaDestructibles.DESTR_TYPE_FALLING_ATOM)
        items = {}
        indexes = []
        for name in ('bins', 'extended_bins'):
            bins = []
            for cell, values in registry.get(name, {}).items():
                rows = []
                for item in values:
                    index = int(item[0])
                    if index in items and items[index] != item:
                        raise RuntimeError('One streamed item has conflicting geometry')
                    items[index] = item
                    rows.append(index)
                bins.append((cell, tuple(rows)))
            indexes.append(tuple(bins))
        values = tuple((index, tuple(float(x) for x in item[1:4]),
                        int(item[4] == tree_type),
                        int(bool(sensor._normalized_filename(item[5]))),
                        int(bool(item[8]) and item[4] in catalog_types),
                        float(item[9])) for index, item in sorted(items.items()))
        self.update(2, (float(chunk_id), values, indexes[0], indexes[1]))
        self.chunks[int(chunk_id)] = registry, items
        return items


def reset():
    global _owner, _disabled
    owner, _owner = _owner, None
    _disabled = False
    if owner is not None:
        try:
            owner.close()
        except Exception:
            pass


def _fail(error):
    global _disabled
    reset()
    _disabled = True
    from gui.mods.offline_lan_0922 import native_math
    native_math._report_failure(error)


def _get(sensor):
    global _owner
    if _disabled:
        return None
    if _owner is None:
        from gui.mods.offline_lan_0922 import native_math
        backend = native_math._load()
        if backend is None or not hasattr(backend, 'destr_open'):
            return None
        try:
            _owner = Geometry(backend, sensor)
        except Exception as error:
            _fail(error)
    return _owner


def put_instance(sensor, key, instance):
    if _owner is not None:
        try:
            _owner.instance(sensor, key, instance)
        except Exception as error:
            _fail(error)


def drop(chunk_id, item_index=None):
    if _owner is not None:
        try:
            _owner.remove(chunk_id, item_index)
        except Exception as error:
            _fail(error)


def vehicle_box(sensor, position, yaw, bbox, travel, motion_yaw, pitch, roll):
    owner = _get(sensor)
    if owner is None:
        return None
    return owner.query(4, pose(position, yaw, bbox, travel, motion_yaw, pitch, roll))


def tree_sweep(sensor, start, start_yaw, end, end_yaw, minimum, maximum, pivot):
    owner = _get(sensor)
    if owner is None:
        return None
    return owner.query(5, (start, start_yaw, end, end_yaw, minimum, maximum, pivot))


def intersections(sensor, boxes, vehicle):
    owner = _get(sensor)
    if owner is None:
        return None
    indexes = owner.query(6, (vehicle, boxes))
    return None if indexes is None else [boxes[index] for index in indexes]


def baked_candidates(sensor, vehicle):
    owner = _get(sensor)
    if owner is None:
        return None
    rows = owner.query(3, vehicle)
    return None if rows is None else [(int(row[0]), int(row[1])) for row in rows]


def trees(sensor, chunk_id, registry, sweeps, tree_type, radius):
    owner = _get(sensor)
    if owner is None:
        return None
    items = owner.chunk(sensor, chunk_id, registry, tree_type)
    rows = owner.query(2, (float(chunk_id), sweeps, float(radius)))
    if rows is None:
        return None
    candidates, isolated = {}, set()
    for index in rows:
        identity = int(chunk_id), int(index), None
        if sensor._destructible_isolated_1513(chunk_id, index):
            isolated.add(identity)
        else:
            candidates[identity] = items[index]
    return candidates, isolated


def body(sensor, chunk_id, registry, position, yaw, speed, bbox, vehicle):
    owner = _get(sensor)
    if owner is None:
        return None
    items = owner.chunk(sensor, chunk_id, registry)
    result = owner.query(1, (float(chunk_id), pose(position, yaw, bbox),
                             float(speed), vehicle))
    if result is None:
        return None
    return [items[index] for index in result[0]], result[1], bool(result[2])


def catalog(sensor, vehicle, contact=None):
    owner = _get(sensor)
    if owner is None:
        return None
    result = owner.query(0, (vehicle, contact))
    if result is None:
        return None
    instances = sensor.__dict__.get('g_offh_destr_instances', {})
    def candidate(row):
        identity = int(row[0]), int(row[1])
        instance = instances[identity]
        box = instance['boxes'][row[2]]
        return identity + (box[2] if instance['kind'] == 'structure' else None,
                           sensor._instance_descriptor_filename_1513(instance),
                           instance['kind'], instance['item_scale'], box[0])
    values = [candidate(row) for row in result[0]]
    groups = {}
    for row in result[1]:
        value = candidate(row)
        identity = value[:2]
        if identity not in groups:
            groups[identity] = [], instances[identity]['boxes']
        groups[identity][0].append((value, bool(row[3])))
    return values, groups, result[2], result[3], bool(result[4])


def refresh_baked(sensor):
    if _owner is not None:
        try:
            bins = (sensor._destructible_catalog or {}).get('baked_shot_bins', {})
            _owner.update(4, tuple((cell, tuple(wire_key(key) for key in members))
                                  for cell, members in bins.items()))
        except Exception as error:
            _fail(error)
