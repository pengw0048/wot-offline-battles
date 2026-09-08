"""Opt-in persistent route/motion navigation; Python owns external identities."""
from __future__ import print_function

import ast
import json
import sys
from array import array


def _path(path):
    return [len(path)] + [float(value) for point in path for value in point]


def _penalties(values):
    values = values or {}
    rows = []
    for edge in values:
        rows.extend(edge[0] + edge[1])
        rows.append(float(values[edge]) if isinstance(values, dict) else 1.0)
    return [len(values)] + rows


class NativeGrid(object):
    def __init__(self, backend, original, navigation):
        if not original.prebaked:
            raise ValueError('native navigation requires a reviewed baked graph')
        self.backend, self.original = backend, original
        self.graph = backend.graph(original, navigation)
        bounds = original.bounds
        if bounds is not None and len(bounds) != 4:
            raise ValueError('native navigation requires normalized bounds')
        self.handle = int(backend.call([500, self.graph.handle, int(bounds is not None)] +
                                       list(bounds or (0, 0, 0, 0)))[0])
        self.failed = self.hulls = self.revision = None
        self.sync_globals()

    def __getattr__(self, name):
        return getattr(self.original, name)

    def sync_globals(self):
        original = self.original
        if (self.failed == original._failed_edges and self.hulls == original._static_hull_edges and
                self.revision == original.static_hull_revision):
            return
        failed = []
        for edge, value in original._failed_edges.items():
            failed.extend(edge[0] + edge[1] + value)
        self.backend.call([502, self.handle, original.static_hull_revision,
                           len(original._failed_edges)] + failed +
                          _penalties(original._static_hull_edges))
        self.failed = dict(original._failed_edges)
        self.hulls = dict(original._static_hull_edges)
        self.revision = original.static_hull_revision

    def geometry(self, kind, values, capacity=0):
        packet = [501, self.handle, kind, len(values)] + list(values)
        packet.extend([0.0] * max(0, capacity - len(packet)))
        return self.backend.call(packet)

    def _ground(self, x, z, hint_y):
        packet = self.geometry(0, (x, hint_y, z))
        return packet[1] if packet[0] else None

    def segment_clear(self, start, end):
        return bool(self.geometry(1, tuple(start) + tuple(end))[0])

    def dry_segment_clear(self, start, end, now):
        return bool(self.geometry(2, tuple(start) + tuple(end) + (now,))[0])

    def segment_has_baked_hazard(self, start, end, mask):
        return bool(self.geometry(3, tuple(start) + tuple(end) + (mask,))[0])

    def segment_has_motion_hazard(self, start, end, mask):
        return bool(self.geometry(4, tuple(start) + tuple(end) + (mask,))[0])

    def point_has_baked_hazard(self, point, mask):
        return bool(self.geometry(5, tuple(point) + (mask,))[0])

    def baked_hazard_near(self, point, max_radius=0):
        return bool(self.geometry(6, tuple(point) + (max(0, int(max_radius)),))[0])

    def near_baked_navigation(self, point, max_radius=1):
        return bool(self.geometry(7, tuple(point) + (max(0, int(max_radius)),))[0])

    def hull_pose_clear(self, point, yaw, half_length, half_width):
        return bool(self.geometry(8, tuple(point) + (yaw, half_length, half_width))[0])

    def local_corridor(self, point):
        packet = self.geometry(9, point)
        return tuple(packet[1:3]) if packet[0] else None

    def segment_penalty(self, start, end, now):
        return self.geometry(10, tuple(start) + tuple(end) + (now,))[0]

    def _cell_capacity(self, start, end):
        a, b = self.cell_for(start), self.cell_for(end)
        return max(abs(a[0] - b[0]), abs(a[1] - b[1])) + 5

    def _baked_segment_cells(self, start, end, require_height=True):
        packet = self.geometry(11, tuple(start) + tuple(end) + (int(require_height),),
                               1 + self._cell_capacity(start, end) * 2)
        return tuple((int(packet[1 + i * 2]), int(packet[2 + i * 2]))
                     for i in range(int(packet[0])))

    def baked_hazard_cells(self, start, end, mask):
        packet = self.geometry(12, tuple(start) + tuple(end) + (mask,),
                               1 + self._cell_capacity(start, end) * 2)
        if packet[0] < 0:
            return None
        return tuple((int(packet[1 + i * 2]), int(packet[2 + i * 2]))
                     for i in range(int(packet[0])))

    def _smooth(self, path, now=0.0, prefer_clearance=False, edge_penalties=None):
        packet = self.geometry(13, _path(path) + [now, int(prefer_clearance)] +
                               _penalties(edge_penalties), 1 + len(path) * 3)
        return tuple(tuple(packet[1 + i * 3:4 + i * 3]) for i in range(int(packet[0])))

    def safe_local_target(self, current, goal, now, avoid_points=None,
                          side_preference=1.0, edge_penalties=None, minimum_offset=0.0):
        packet = self.geometry(14, list(current) + list(goal) + [now, side_preference, minimum_offset] +
                               _path(avoid_points or ()) + _penalties(edge_penalties))
        return tuple(packet[1:4]) if packet[0] else None

    def path_has_edge_penalty(self, path, edge_penalties):
        return bool(self.geometry(15, _path(path) + _penalties(edge_penalties))[0])

    def _baked_clearance_exposure(self, path):
        packet = self.geometry(16, _path(path))
        return packet[1] if packet[0] else None

    def shortcut_preserves_climb_approach(self, path, start_index, end_index,
                                         minimum_grade=0.10, minimum_turn=0.30):
        return bool(self.geometry(17, _path(path) +
                                 [start_index, end_index, minimum_grade, minimum_turn])[0])

    def live_shortcut_preserves_climb_approach(self, current, path, start_index, end_index):
        return bool(self.geometry(18, list(current) + _path(path) + [start_index, end_index])[0])

    def shortcut_preserves_baked_clearance(self, path, start_index, end_index,
                                         maximum_exposure_increase=0.25):
        return bool(self.geometry(19, _path(path) +
                                 [start_index, end_index, maximum_exposure_increase])[0])

    def path_has_penalty(self, path, now):
        return bool(self.geometry(20, _path(path) + [now])[0])

    def path_crosses_static_hull(self, path):
        return bool(self.geometry(21, _path(path))[0])

    def _edge_keys_for_segment(self, start, end):
        packet = self.geometry(22, list(start) + list(end),
                               1 + self._cell_capacity(start, end) * 4)
        return tuple(((int(packet[1 + i * 4]), int(packet[2 + i * 4])),
                      (int(packet[3 + i * 4]), int(packet[4 + i * 4])))
                     for i in range(int(packet[0])))

    def _baked_corridor(self, start, end):
        packet = self.geometry(23, list(start) + list(end))
        return bool(packet[0]), None if packet[1] < 0 else int(packet[1])

    def set_static_hulls(self, hulls):
        changed = self.original.set_static_hulls(hulls)
        self.sync_globals()
        return changed

    def close(self):
        if self.handle is not None and not self.backend.closed:
            self.backend.call([503, self.handle])
        self.handle = None


def _text(value):
    encoded = value.encode('utf-8')
    return [len(encoded)] + list(bytearray(encoded))


def _tuples(value):
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        result = dict((key, _tuples(item)) for key, item in value.items())
        for key in ('path_key', 'request_key', 'request_path_key', 'macro_progress_path_key'):
            if result.get(key) is not None:
                result[key] = ast.literal_eval(result[key])
        return result
    return value


class SearchProjection(object):
    def __init__(self, values):
        self.__dict__.update(values)


class NativeNavigator(object):
    def __init__(self, backend, original, navigation):
        self.backend, self.original, self.navigation = backend, original, navigation
        self.grid = NativeGrid(backend, original.grid, navigation)
        self.handle = int(backend.call([510, self.grid.handle])[0])
        self.identities = {}
        self._native_keys = {}
        self._cache_order = {}
        self.paths = {}
        self.bot_states = {}
        self._snapshot = None
        self._snapshot_full = False
        self._progress = None
        self._query_packet = array('d', [0.0] * 16384)
        self._progress_packet = array('d', [0.0] * 1024)
        navigator = self

        def publish(packet=None):
            if packet is None:
                packet = navigator._query_packet
            kind, identity = int(packet[0]), int(packet[1])
            if kind == 503:
                key = bytearray(int(value) for value in packet[3:3 + int(packet[2])]).decode('utf-8')
                navigator._native_keys[identity] = ast.literal_eval(key)
            elif kind == 500:
                key = navigator._native_keys[identity]
                navigator.paths[key] = tuple(tuple(packet[3 + i * 3:6 + i * 3]) for i in range(int(packet[2])))
                navigator._cache_order[key] = identity
            elif kind == 501:
                key = navigator._native_keys[identity]
                navigator.paths.pop(key, None)
                navigator._cache_order.pop(key, None)
            elif kind == 504:
                navigator._native_keys.pop(identity)
            elif kind == 502:
                order = list(navigator._cache_order.values())
                if len(order) != identity:
                    raise AssertionError('native cache keys differ from their projection')
                packet[2:2 + len(order)] = array('d', order)
            else:
                raise RuntimeError('unknown navigation identity event: %s' % kind)

        self._publish_event = publish
        self.search_max_expansions = original.search_max_expansions

    def __getattr__(self, name):
        if name in ('searches', 'search_next_key', 'search_credit',
                    'search_frame_budget', 'search_completed', 'search_failed'):
            return self.progress_state()[name]
        if name.startswith(('search_', 'path_', 'fallback_', 'bot_')) or name in ('paths', 'searches', 'housekeeping_time'):
            full = name not in ('paths', 'searches', 'search_next_key', 'search_credit',
                                'search_frame_budget', 'search_completed', 'search_failed')
            snapshot = self.dump_state(full)
            if name in snapshot:
                return snapshot[name]
        return getattr(self.original, name)

    def call(self, kind, values=()):
        self._snapshot = None
        self._progress = None
        packet = [512, self.handle, kind, len(values)] + list(values)
        packet.extend([0.0] * max(0, 16 - len(packet)))
        return self.backend.call_sync(packet, self._query_packet, self._publish_event)

    def identity(self, key):
        key = tuple(key)
        handle = self.identities.get(key)
        if handle is None:
            required = len(repr(key)) + 1024 + self._search_max_expansions * 3
            if required > len(self._query_packet):
                self._query_packet = array('d', [0.0] * required)
            owner = self.original._path_owner(key)
            handle = int(self.backend.call(
                [511, self.handle] + _text(repr(key)) + _text(str(key[0]) if key else '') +
                [-1 if owner is None else owner, int(self.original._prefers_baked_clearance(key))])[0])
            self.identities[key] = handle
        return handle

    def _publish(self, bot_id, packet, offset):
        if not packet[offset]:
            return
        row = {'navigation_status': ('pending', 'safe', 'blocked')[int(packet[offset + 1])],
               'target_is_terminal': bool(packet[offset + 10])}
        if packet[offset + 2]:
            row['planned_goal'] = tuple(packet[offset + 3:offset + 6])
        if packet[offset + 6]:
            row['controlled_shallow_target'] = tuple(packet[offset + 7:offset + 10])
        self.bot_states[int(bot_id)] = row

    def begin_frame(self, elapsed):
        self.call(0, [elapsed])

    def end_frame(self):
        self.call(1)

    def tick(self, now):
        self.call(2, [now])

    def _advance_searches(self, now):
        self.call(11, [now])

    def next_target(self, bot_id, current, goal, path_key, now, anchor=None,
                    avoid_points=None, lookahead_distance=None, movement_intent=True):
        values = ([bot_id, self.identity(path_key)] + list(current) + list(goal) +
                  [now, int(movement_intent), int(anchor is not None)])
        if anchor is not None:
            values.extend(anchor)
        values.append(int(lookahead_distance is not None))
        if lookahead_distance is not None:
            values.append(lookahead_distance)
        packet = self.call(3, values + _path(avoid_points or ()))
        self._publish(bot_id, packet, 4)
        return tuple(packet[1:4])

    def observe_direct_target(self, bot_id, current, goal, path_key, now, movement_intent=True):
        packet = self.call(4, [bot_id, self.identity(path_key)] + list(current) + list(goal) +
                           [now, int(movement_intent)])
        self._publish(bot_id, packet, 4)
        return tuple(packet[1:4]) if packet[0] else None

    def motion_target(self, bot_id, current, goal, path_key, now, anchor,
                      lookahead_distance, movement_intent, stop_at_goal):
        values = ([bot_id, self.identity(path_key)] + list(current) + list(goal) +
                  [now, int(movement_intent), int(anchor is not None)])
        if anchor is not None:
            values.extend(anchor)
        packet = self.call(13, values + [lookahead_distance, int(stop_at_goal)])
        self._publish(bot_id, packet, 4)
        return tuple(packet[1:4]), bool(packet[0])

    def report_blocked_step(self, bot_id, current, target, now):
        values = [bot_id] + list(current) + [int(target is not None)]
        if target is not None:
            values.extend(target)
        packet = self.call(5, values + [now])
        self._publish(bot_id, packet, 1)
        return bool(packet[0])

    def bot_segment_penalized(self, bot_id, start, end, now):
        return bool(self.call(6, [bot_id] + list(start) + list(end) + [now])[0])

    def controlled_shallow_step(self, bot_id, current, sample_yaw, maximum_yaw_error=0.45):
        return bool(self.call(7, [bot_id] + list(current) + [sample_yaw, maximum_yaw_error])[0])

    def controlled_shallow_committed(self, bot_id, current, travel_yaw):
        return bool(self.call(8, [bot_id] + list(current) + [travel_yaw, 0.45])[0])

    def target_is_terminal(self, bot_id):
        return self.bot_states.get(int(bot_id), {}).get('target_is_terminal', False)

    @property
    def search_max_expansions(self):
        return self._search_max_expansions

    @search_max_expansions.setter
    def search_max_expansions(self, value):
        required = max(16384, int(value) * 3 + 1024)
        if required > len(self._query_packet):
            self._query_packet = array('d', [0.0] * required)
        self.call(10, [int(value)])
        self._search_max_expansions = int(value)

    def progress_state(self):
        if self._progress is not None:
            return self._progress
        while True:
            self._progress_packet[0:2] = array('d', [516, self.handle])
            packet = self.backend.call(self._progress_packet)
            if packet[1] >= 0:
                break
            self._progress_packet = array('d', [0.0] * int(packet[0]))
        searches = {}
        for index in range(int(packet[0])):
            offset = 6 + index * 6
            key = self._native_keys[int(packet[offset])]
            searches[key] = SearchProjection(dict(
                last_frame=packet[offset + 2] if packet[offset + 1] else None,
                hull_revision=int(packet[offset + 3]), done=bool(packet[offset + 4]),
                expansions=int(packet[offset + 5])))
        self._progress = dict(searches=searches, search_credit=packet[1], search_frame_budget=int(packet[2]),
                              search_completed=int(packet[3]), search_failed=int(packet[4]),
                              search_next_key=self._native_keys[int(packet[5])] if packet[5] else None)
        return self._progress

    def dump_state(self, full=True):
        if self._snapshot is not None and (self._snapshot_full or not full):
            return self._snapshot
        size = int(self.backend.call([513, self.handle, int(full)])[0])
        packet = self.backend.call(array('d', [514, self.handle] + [0.0] * max(0, size - 1)))
        encoded = bytearray(int(value) for value in packet[1:size + 1])
        result = json.loads(encoded.decode('utf-8'))
        for name in ('paths', 'searches', 'path_times', 'path_hull_revisions', 'search_times'):
            if name in result:
                result[name] = dict((ast.literal_eval(key), _tuples(value))
                                    for key, value in result[name].items())
        result['searches'] = dict((key, SearchProjection(value)) for key, value in result['searches'].items())
        if result['search_next_key'] is not None:
            result['search_next_key'] = ast.literal_eval(result['search_next_key'])
        for name in ('bot_states', 'bot_direct_progress', 'fallback_modes'):
            if name in result:
                result[name] = dict((int(key), _tuples(value)) for key, value in result[name].items())
        for name in ('bot_failed_edges', 'bot_macro_edges'):
            if name in result:
                result[name] = dict((int(bot_id), dict((_tuples(row[0]), tuple(row[1:])) for row in rows))
                                    for bot_id, rows in result[name].items())
        if 'grid_failed_edges' in result:
            result['grid_failed_edges'] = dict((_tuples(row[0]), tuple(row[1:])) for row in result['grid_failed_edges'])
        self._snapshot, self._snapshot_full = result, full
        return result

    def fallback_diagnostics(self, active_bot_ids=None, now=None):
        if active_bot_ids is not None:
            self.call(12, [len(active_bot_ids)] + list(active_bot_ids))
        snapshot = self.dump_state()
        active = dict((key, 0) for key in ('pending', 'safe_direct', 'safe_local', 'reactive'))
        for mode in snapshot['fallback_modes'].values():
            active[mode] += 1
        times = snapshot['search_times']
        search_now = snapshot['search_now']
        return dict(
            graph=dict(source='baked', cell_mm=int(round(self.grid.cell_size * 1000.0)),
                       nodes=sum(1 for value in self.grid._baked_heights if value is not None)),
            total=dict(snapshot['fallback_totals']), active=active, recovered=snapshot['fallback_recovered'],
            search=dict(pending=len(snapshot['searches']), completed=snapshot['search_completed'],
                        failed=snapshot['search_failed'],
                        oldest_ms=int(max(0.0, search_now - min(times.values())) * 1000.0) if times else 0,
                        tick_age_ms=int(max(0.0, now - search_now) * 1000.0) if now is not None else 0),
            blocked_step_replans=sum(state['blocked_step_replans'] for state in snapshot['bot_states'].values()),
            macro_progress_replans=(sum(state['macro_progress_replans'] for state in snapshot['bot_states'].values()) +
                                    sum(state['replans'] for state in snapshot['bot_direct_progress'].values())))

    def close(self):
        if self.handle is not None and not self.backend.closed:
            self.backend.call([515, self.handle])
        self.handle = None
        self.grid.close()


class NavigationFlowBackend(object):
    def __init__(self, backend, runtime, navigation):
        self.runtime = runtime
        self.original = runtime.navigator
        self.navigator = NativeNavigator(backend, self.original, navigation)
        runtime.navigator = self.navigator

    def close(self):
        if self.runtime.navigator is self.navigator:
            self.runtime.navigator = self.original
        self.navigator.close()
