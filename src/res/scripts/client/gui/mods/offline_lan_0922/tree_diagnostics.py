"""Read-only local tree evidence, including contacts absent from the registry."""
import math


class TreeContactDiagnostics(object):
    """Sample cached placement and native-order state without engine queries."""

    def __init__(self):
        self.space = None
        self.catalog_key = None
        self.bins = {}
        self.next_sample = 0.0
        self.last_signature = None
        self.commits = set()

    def sample(self, sensor, authority, space, start, end, yaw, speed, dt,
               now, detail, stage='proposal'):
        if self.space != space:
            self.__init__()
            self.space = space
        token = tuple(tuple(row) for row in detail.get('token') or ())
        if stage == 'commit':
            fresh = set(token) - self.commits
            if not fresh:
                return None
            self.commits.update(fresh)
        elif now < self.next_sample:
            return None
        else:
            # This is a log sampling limit, never a motion or retry deadline.
            self.next_sample = now + 0.5
        catalog = getattr(sensor, '_destructible_catalog', None)
        if not catalog:
            return None
        key = (id(catalog), tuple(sorted(catalog.get(
            'layout_generations', {}).items())),
            tuple(sorted(catalog.get('layout_repairs', ()))))
        if key != self.catalog_key:
            self.bins = {}
            quantization = float(catalog['quantization'])
            for wire, record in catalog.get('tree_instances', {}).items():
                point = tuple(value / quantization
                              for value in record['signature'][:3])
                cell = (int(math.floor(point[0] / 8.0)),
                        int(math.floor(point[2] / 8.0)))
                self.bins.setdefault(cell, []).append((wire, record, point))
            self.catalog_key = key
        state = getattr(sensor, 'g_offh_tree_state', {})
        if state.get('spaceID') != space:
            state = {}
        ledger = getattr(authority, '_state', {})
        if ledger.get('spaceID') != space:
            ledger = {}
        cell = (int(math.floor(end[0] / 8.0)),
                int(math.floor(end[2] / 8.0)))
        nearby = []
        for cx in range(cell[0] - 1, cell[0] + 2):
            for cz in range(cell[1] - 1, cell[1] + 2):
                for wire, record, point in self.bins.get((cx, cz), ()):
                    distance = (point[0] - end[0]) ** 2 + (point[2] - end[2]) ** 2
                    if distance <= 64.0:
                        nearby.append((distance, wire, record, point))
        nearby.sort(key=lambda row: (row[0], row[1]))
        rows = []
        for distance, wire, record, point in nearby[:12]:
            registry = state.get('chunks', {}).get(wire[0])
            chunk = ledger.get('chunks', {}).get(wire[0], {})
            receipt = chunk.get('treePresentations', {}).get(wire[1], {})
            native = None
            if registry is not None:
                bin_key = sensor._destructible_bin_key(point[0], point[2])
                native = next((item for item in registry.get('bins', {}).get(
                    bin_key, ()) if item[0] == wire[1]), None)
            names = getattr(sensor, 'g_offh_destr_item_names', {}).get(
                (int(space), wire[0]), {})
            name_result = names.get('result')
            rows.append({
                'wire': wire, 'resource': record['descriptor_filename'],
                'authored_position': point, 'distance': round(distance ** 0.5, 3),
                'chunk_registered': registry is not None,
                'layout_pending': wire[0] in catalog.get('layout_repairs', ()),
                'isolated': sensor.is_isolated_1513(*wire),
                'registry_position': None if native is None else native[1:4],
                'health': (registry or {}).get('tree_health', {}).get(wire[1]),
                'name_status': (name_result[1] if name_result else
                                'pending' if names else 'uncached'),
                'name_next_item': names.get('next_item'),
                'native_count': (registry or {}).get('native_count'),
                'name_slots': (names.get('fingerprint') or (None,))[0],
                'candidate': (wire[0], wire[1], None) in token,
                'native_committed': wire in state.get('native_committed', ()),
                'canonical_published': wire in state.get('canonical_published', ()),
                'publish_pending': wire in state.get('publish_pending', {}),
                'authority_destroyed': (wire[1], None) in chunk.get('keys', ()),
                'presentation_at_accept': receipt.get('status'),
                'fall_pitch': receipt.get('fallPitch'),
            })
        if not rows and not token:
            return None
        signature = (str(detail.get('status')), token, tuple(
            (tuple(row['wire']), row['chunk_registered'], row['layout_pending'],
             row['isolated'], row['registry_position'] is not None, row['health'],
             row['name_status'], row['name_next_item'],
             row['candidate'], row['native_committed'], row['canonical_published'],
             row['publish_pending'], row['authority_destroyed'],
             row['presentation_at_accept']) for row in rows))
        if stage != 'commit' and signature == self.last_signature:
            return None
        self.last_signature = signature
        return {
            'map': catalog.get('map'), 'space': space, 'stage': stage,
            'start': tuple(start), 'end': tuple(end), 'yaw': yaw,
            'speed': speed, 'dt': dt, 'status': detail.get('status'),
            'token': token, 'requires_commit': detail.get('requires_commit'),
            'nearby_count': len(nearby), 'trees': rows,
        }
