"""Copied persistent C++ terrain-route state with synchronous engine frontiers.

The facade owns no routing law. Cold snapshots support installation, inspection,
and exact restoration of the original navigator and its in-flight job receipts.
"""
from __future__ import print_function

from collections import deque

from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator
from gui.mods.offline_lan_0922.native_navigation import NativeSearch
from gui.mods.offline_lan_0922.worker_diagnostics import (
    count as combat_count, observed)


BOT_POINT_FIELDS = (
    'last_position', 'recovery_start', 'planned_goal', 'macro_progress_position',
    'macro_progress_target', 'last_target', 'macro_escape_target',
    'controlled_shallow_target')
BOT_KEY_FIELDS = ('path_key', 'recovery_key', 'request_key', 'request_path_key',
                  'macro_progress_path_key')
BOT_NUMBER_FIELDS = (
    'progress_time', 'recovery_until', 'planned_at', 'macro_progress_at',
    'blocked_step_escalated_until', 'macro_escape_until', 'pending_since',
    'index', 'recovery', 'replan_generation', 'macro_progress_replans',
    'macro_progress_index', 'blocked_step_replans', 'target_is_terminal',
    'replan_active', 'macro_replan_active')
BOT_FIELDS = BOT_POINT_FIELDS + BOT_KEY_FIELDS + BOT_NUMBER_FIELDS + (
    'navigation_status', 'blocked_step_tracker')
SCALARS = ('search_credit', 'search_now', 'search_frame_serial',
           'search_processed_frame', 'search_frame_budget',
           'search_max_expansions', 'search_completed', 'search_failed',
           'fallback_recovered', 'search_frame_open')
OPTIONAL_SCALARS = ('search_frame_time', 'housekeeping_time', 'search_auto_time',
                    'search_next_key')


def _bytes(value):
    return tuple(ord(c) for c in str(value))


def _text(value):
    return ''.join(chr(int(c)) for c in value)


def _key(value):
    if value is None:
        return 0, None
    if isinstance(value, (int, long, float)):
        return 1, value
    if isinstance(value, basestring):
        return 2, _bytes(value)
    if isinstance(value, tuple):
        return 3, tuple(_key(v) for v in value)
    raise TypeError('unsupported navigation identity %r' % (type(value),))


def _unkey(value):
    kind, data = value
    if kind == 0:
        return None
    if kind == 1:
        return int(data) if data == int(data) else data
    if kind == 2:
        return _text(data)
    if kind == 3:
        return tuple(_unkey(v) for v in data)
    raise ValueError('invalid navigation identity')


def _field(values, name, encode=lambda value: value):
    if name not in values:
        return (0,)
    if values[name] is None:
        return (1,)
    return 2, encode(values[name])


def _optional(value, encode=lambda item: item):
    return (1,) if value is None else (2, encode(value))


def _put_field(values, name, raw, decode=lambda value: value):
    if raw[0] == 2:
        values[name] = decode(raw[1])
    elif raw[0] == 1:
        values[name] = None
    elif raw[0] != 0:
        raise ValueError('invalid navigation field status')


def _tracker(value):
    return (value['key'], value['count'], value['first_at'],
            value['last_at'], value['origin'])


def _untracker(value):
    return dict(zip(('key', 'count', 'first_at', 'last_at', 'origin'), value))


def _bot_encode(state):
    if set(state) - set(BOT_FIELDS):
        raise ValueError('unreviewed navigation state fields: %r' %
                         sorted(set(state) - set(BOT_FIELDS)))
    rows = []
    for name in BOT_FIELDS:
        encode = (_key if name in BOT_KEY_FIELDS else
                  _bytes if name == 'navigation_status' else
                  _tracker if name == 'blocked_step_tracker' else
                  lambda value: value)
        rows.append(_field(state, name, encode))
    return tuple(rows)


def _bot_decode(rows):
    result = {}
    for name, row in zip(BOT_FIELDS, rows):
        decode = (_unkey if name in BOT_KEY_FIELDS else
                  _text if name == 'navigation_status' else
                  _untracker if name == 'blocked_step_tracker' else
                  lambda value: value)
        _put_field(result, name, row, decode)
    return result


def _leases(values):
    return tuple((bot_id, tuple((edge, data[0], data[1])
                               for edge, data in edges.items()))
                 for bot_id, edges in values.items())


def _search(job):
    return (job.job_id, job.hull_revision, job.submitted_at,
            bool(job.done), tuple(job.result or ()),
            ({'pending': 0, 'done': 0, 'cancelled': 1, 'failed': 2}[job.status],
             job.last_frame))


def _encode(navigator):
    paths = tuple((_key(key), tuple(path), navigator.path_times[key],
                   navigator.path_hull_revisions[key])
                  for key, path in navigator.paths.items())
    searches = tuple((_key(key), _search(job))
                     for key, job in navigator.searches.items())
    bots = tuple((bot_id, _bot_encode(state))
                 for bot_id, state in navigator.bot_states.items())
    direct = tuple((bot_id, _key(state['path_key']), state['target'],
                    state['position'], state['progress_at'],
                    state.get('replans', 0),
                    _field(state, 'escape_target'),
                    _field(state, 'escape_until'))
                   for bot_id, state in navigator.bot_direct_progress.items())
    optional = tuple(_optional(getattr(navigator, name),
                              _key if name == 'search_next_key' else
                              lambda value: value)
                     for name in OPTIONAL_SCALARS)
    scalar = optional + tuple(getattr(navigator, name) for name in SCALARS)
    return (paths, searches, bots, _leases(navigator.bot_failed_edges),
            _leases(navigator.bot_macro_edges), direct,
            tuple((bot_id, _bytes(mode)) for bot_id, mode in
                  navigator.fallback_modes.items()),
            tuple((_bytes(mode), count) for mode, count in
                  navigator.fallback_totals.items()), scalar + (True, False))


def _decode(snapshot, jobs):
    paths, searches, bots, failed, macro, direct, modes, totals, scalar = snapshot
    result = dict(paths={}, path_times={}, path_hull_revisions={},
                  searches={}, search_times={}, bot_states={},
                  bot_failed_edges={}, bot_macro_edges={}, bot_direct_progress={})
    for encoded, path, timestamp, revision in paths:
        key = _unkey(encoded)
        result['paths'][key] = tuple(tuple(p) for p in path)
        result['path_times'][key] = timestamp
        result['path_hull_revisions'][key] = int(revision)
    for encoded, row in searches:
        key = _unkey(encoded)
        job_id, revision, timestamp, done, path, completion = row
        job = jobs.get(job_id)
        if job is None:
            job = NativeSearch(job_id, revision, timestamp)
            jobs[job_id] = job
        job.hull_revision = int(revision)
        job.done = bool(done)
        job.result = tuple(path) if done else None
        job.status = ('done', 'cancelled', 'failed')[int(completion[0])] if done else 'pending'
        job.last_frame = completion[1]
        result['searches'][key] = job
        result['search_times'][key] = timestamp
    for bot_id, rows in bots:
        result['bot_states'][int(bot_id)] = _bot_decode(rows)
    for name, values in (('bot_failed_edges', failed), ('bot_macro_edges', macro)):
        result[name] = dict((int(bot_id), dict((edge, (until, penalty))
                            for edge, until, penalty in edges))
                            for bot_id, edges in values)
    for row in direct:
        bot_id, key, target, position, timestamp, replans, escape, until = row
        state = dict(path_key=_unkey(key), target=target, position=position,
                     progress_at=timestamp, replans=int(replans))
        _put_field(state, 'escape_target', escape)
        _put_field(state, 'escape_until', until)
        result['bot_direct_progress'][int(bot_id)] = state
    result['fallback_modes'] = dict((int(bot_id), _text(mode))
                                    for bot_id, mode in modes)
    result['fallback_totals'] = dict((_text(mode), int(count))
                                    for mode, count in totals)
    for name, row in zip(OPTIONAL_SCALARS, scalar[:4]):
        _put_field(result, name, row,
                   _unkey if name == 'search_next_key' else lambda value: value)
    for name, value in zip(SCALARS, scalar[4:14]):
        result[name] = value
    return result



def _grid_encode(grid, source_graph=None):
    # TerrainGrid clamps planner cell size to at least one metre. Physical
    # pose_is_safe reads the validated source value, including smaller cells.
    source_cell_size = (float(source_graph['cell_size'])
                        if isinstance(source_graph, dict) else 0.0)
    config = ((grid._baked_origin[0], grid._baked_origin[1], grid.cell_size,
               grid._baked_width, grid._baked_height, grid.max_grade_up,
               grid.max_grade_down, grid._baked_max_grade, grid.heuristic_weight,
               grid.obstacle_probe is not None),
              tuple(grid._baked_heights), tuple(grid._baked_links),
              tuple(grid._baked_hazards), grid.bounds, source_cell_size)
    state = (grid._static_hull_key, grid.static_hull_revision,
             tuple((edge, value[0], value[1])
                   for edge, value in grid._failed_edges.items()),
             tuple(grid._static_hull_edges.items()),
             tuple(grid._native_review_cells), tuple(grid._native_review_seeds),
             tuple((key, grid._baked_corridor_cache[key][0],
                    grid._baked_corridor_cache[key][1])
                   for key in grid._baked_corridor_order),
             tuple(grid._corridor_cache.items()))
    return config, state


def _grid_restore(grid, state):
    (hulls, revision, failed, static, cells, seeds, corridors, local) = state
    grid._static_hull_key = hulls
    grid.static_hull_revision = int(revision)
    grid._failed_edges = dict((key, (until, penalty)) for key, until, penalty in failed)
    grid._static_hull_edges = dict(static)
    grid._native_review_cells = set(cells)
    grid._native_review_seeds = set(seeds)
    grid._baked_corridor_cache = dict((key, (bool(clear), hazards))
                                     for key, clear, hazards in corridors)
    grid._baked_corridor_order = deque(key for key, clear, hazards in corridors)
    grid._corridor_cache = dict(local)


class _BotStates(dict):
    """Compatibility reader copies only the Bot requested by runtime code."""

    def __init__(self, owner):
        self.owner = owner

    def get(self, key, default=None):
        row = self.owner.backend.sim_navigation_bot_snapshot(self.owner.handle, int(key))
        if row is None:
            raise RuntimeError('persistent navigation Bot snapshot rejected')
        return _bot_decode(row[0]) if row else default

    def __getitem__(self, key):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        sentinel = object()
        return self.get(key, sentinel) is not sentinel

    def __deepcopy__(self, memo):
        import copy
        return copy.deepcopy(self.owner._snapshot()['bot_states'], memo)

    def items(self):
        return self.owner._snapshot()['bot_states'].items()

    def values(self):
        return self.owner._snapshot()['bot_states'].values()

    def keys(self):
        return self.owner._snapshot()['bot_states'].keys()

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.owner._snapshot()['bot_states'])


class NativeGrid(object):
    """Baked-grid decisions use the persistent map; raw proofs stay ordered."""

    _DYNAMIC = ('_static_hull_key', 'static_hull_revision', '_failed_edges',
                '_static_hull_edges', '_native_review_cells', '_native_review_seeds',
                '_baked_corridor_cache', '_baked_corridor_order', '_corridor_cache')

    def __init__(self, owner, original):
        self.owner = owner
        self.original = original
        self._snapshot_cache = None

    def _snapshot(self):
        if self._snapshot_cache is None:
            state = self.owner.backend.sim_navigation_grid_snapshot(self.owner.handle)
            if state is None:
                raise RuntimeError('persistent navigation grid snapshot rejected')
            self._snapshot_cache = state
        return self._snapshot_cache

    def __getattr__(self, name):
        if name in self._DYNAMIC:
            state = self._snapshot()
            if name == '_static_hull_key':
                return state[0]
            if name == 'static_hull_revision':
                return int(state[1])
            if name == '_failed_edges':
                return dict((key, (until, penalty)) for key, until, penalty in state[2])
            if name == '_static_hull_edges':
                return dict(state[3])
            if name == '_native_review_cells':
                return set(state[4])
            if name == '_native_review_seeds':
                return set(state[5])
            if name == '_baked_corridor_cache':
                return dict((key, (bool(clear), hazards)) for key, clear, hazards in state[6])
            if name == '_baked_corridor_order':
                return deque(key for key, clear, hazards in state[6])
            if name == '_corridor_cache':
                return dict(state[7])
        return getattr(self.original, name)

    @property
    def native_query_oracle(self):
        return self.original.native_query_oracle

    @native_query_oracle.setter
    def native_query_oracle(self, value):
        self.original.native_query_oracle = value

    @property
    def _native_review_pending(self):
        return self.original._native_review_pending

    @_native_review_pending.setter
    def _native_review_pending(self, value):
        self.original._native_review_pending = value

    def _call(self, op, *args):
        self.owner._cached_snapshot = None
        self._snapshot_cache = None
        row = self.owner.backend.sim_navigation_grid_command(
            self.owner.handle, op, args, self.owner._dispatch)
        if row is None:
            raise RuntimeError('persistent grid operation %d failed' % op)
        return row[0]

    def cell_for(self, point):
        return self._call(0, tuple(point))

    def segment_has_baked_hazard(self, start, end, mask):
        return bool(self._call(1, tuple(start), tuple(end), int(mask)))

    def baked_hazard_cells(self, start, end, mask):
        return self._call(2, tuple(start), tuple(end), int(mask))

    def segment_clear(self, start, end):
        return bool(self._call(3, tuple(start), tuple(end)))

    def dry_segment_clear(self, start, end, now):
        return bool(self._call(4, tuple(start), tuple(end), float(now)))

    def segment_penalty(self, start, end, now):
        return self._call(5, tuple(start), tuple(end), float(now))

    def _edge_keys_for_segment(self, start, end):
        return self._call(6, tuple(start), tuple(end))

    def _edge_cells_for_segment(self, start, end):
        return self._call(7, tuple(start), tuple(end))

    def safe_local_target(self, current, goal, now, avoid_points=None,
                          side_preference=1.0, edge_penalties=None, minimum_offset=0.0):
        return self._call(8, tuple(current), tuple(goal), float(now),
                          tuple(avoid_points or ()), float(side_preference),
                          tuple((edge_penalties or {}).items()), float(minimum_offset))

    def path_has_penalty(self, path, now):
        return bool(self._call(9, tuple(path), float(now)))

    def path_crosses_static_hull(self, path):
        return bool(self._call(10, tuple(path)))

    def shortcut_preserves_baked_clearance(self, path, start, end,
                                            maximum_exposure_increase=.25):
        return bool(self._call(11, tuple(path), int(start), int(end),
                               float(maximum_exposure_increase)))

    def live_shortcut_preserves_climb_approach(self, current, path, start, end):
        return bool(self._call(12, tuple(current), tuple(path), int(start), int(end)))

    def review_native_corridor(self, start, end):
        return bool(self._call(13, tuple(start), tuple(end)))

    def _needs_native_review(self, start, end):
        return bool(self._call(14, tuple(start), tuple(end)))

    def prune_failed_edges(self, now):
        self._call(17, float(now))

    def near_baked_navigation(self, point, max_radius=1):
        return bool(self._call(23, tuple(point), max(0, int(max_radius))))

    def segment_has_motion_hazard(self, start, end, mask):
        return bool(self._call(24, tuple(start), tuple(end), int(mask)))

    def point_has_baked_hazard(self, point, mask):
        return bool(self._call(25, tuple(point), int(mask)))

    def baked_hazard_near(self, point, max_radius=0):
        return bool(self._call(26, tuple(point), max(0, int(max_radius))))

    def local_corridor(self, point):
        return self._call(27, tuple(point))

    def hull_pose_clear(self, point, yaw, half_length, half_width):
        return bool(self._call(28, tuple(point), float(yaw),
                               float(half_length), float(half_width)))

    def set_static_hulls(self, hulls):
        # Keep the reviewed CPython 2 rounding at the snapshot boundary.
        key = tuple(sorted((int(h[0]), round(float(h[1]), 2), round(float(h[2]), 2),
                            round(float(h[3]), 3), round(float(h[4]), 2),
                            round(float(h[5]), 2)) for h in hulls or ()))
        return bool(self._call(29, key))

    def _ground(self, x, z, hint_y):
        return self._call(31, float(x), float(z), float(hint_y))

    def point_for(self, cell, height):
        return self._call(32, tuple(cell), float(height))

    def plan(self, *args, **kwargs):
        # Explicit synchronous tooling path only. Production uses native jobs;
        # exporting evidence lets the retained reference planner audit them.
        _grid_restore(self.original, self._snapshot())
        return self.original.plan(*args, **kwargs)



def _async_encode(native, jobs):
    if not hasattr(native, 'context'):
        return None
    scalar = (native.context, native.next_job_id, native.closed,
              native.total_submitted, native.total_completed, native.total_cancelled,
              native.total_queries, native.total_expansions, native.total_worker_seconds,
              native.total_main_seconds, native.max_completion_age, 0)
    return (scalar, tuple(_search(job) for job in native.jobs.values()),
            tuple((job_id, tuple(queue)) for job_id, queue in native.queries.items()),
            tuple(native.query_order), tuple(native.stats), tuple(jobs), 0)


def _async_restore(native, raw, jobs):
    if raw is None:
        return
    scalar, pending, queries, order, stats, watched, unused = raw
    names = ('context', 'next_job_id', 'closed', 'total_submitted', 'total_completed',
             'total_cancelled', 'total_queries', 'total_expansions',
             'total_worker_seconds', 'total_main_seconds', 'max_completion_age')
    for name, value in zip(names, scalar):
        setattr(native, name, (bool(value) if name == 'closed' else
                              float(value) if name in ('total_worker_seconds',
                                                        'total_main_seconds',
                                                        'max_completion_age') else int(value)))
    restored = {}
    for row in pending:
        job_id, revision, timestamp, done, path, completion = row
        job = jobs.get(job_id)
        if job is None:
            job = NativeSearch(job_id, revision, timestamp)
        job.done = bool(done)
        job.result = tuple(path) if done else None
        job.status = ('done', 'cancelled', 'failed')[int(completion[0])] if done else 'pending'
        job.last_frame = completion[1]
        job.hull_revision = int(revision)
        restored[job_id] = job
    native.jobs = restored
    native.queries = dict((job_id, deque(queue)) for job_id, queue in queries)
    native.query_order = deque(order)
    native.stats = tuple(stats)


class NativeAsyncView(object):
    """Read and explicitly drain the existing worker owned by the native Store."""

    def __init__(self, owner, original):
        self.owner = owner
        self.original = original
        self.backend = owner.backend
        self.grid = owner.grid

    def _snapshot(self):
        raw = self.backend.sim_navigation_async_snapshot(self.owner.handle)
        if raw is None:
            raise RuntimeError('persistent asynchronous navigation snapshot rejected')
        return raw

    def snapshot(self):
        raw = self._snapshot()
        scalar = raw[0]
        names = ('submitted', 'completed', 'cancelled', 'queries', 'expansions',
                 'worker_seconds', 'main_seconds', 'max_completion_age')
        result = dict(zip(names, scalar[3:11]))
        result['pending'] = len(raw[1])
        result['worker_stats'] = tuple(raw[4])
        return result

    def advance(self, now, query_budget):
        self.owner._call(12, float(now), int(query_budget))

    def close(self):
        self.owner.close()

    @property
    def jobs(self):
        # A cold diagnostic reader retains the same receipt objects as detach.
        return dict((job.job_id, job) for job in self.owner.searches.values()
                    if not job.done)

    @property
    def closed(self):
        return bool(self._snapshot()[0][2])


class NativeNavigationCore(object):
    """Facade for a complete native TerrainNavigator decision state machine."""

    def __init__(self, runtime, backend, handle):
        self.runtime = runtime
        self.backend = backend
        self.handle = handle
        self.original = runtime.navigator
        if type(self.original) is not TerrainNavigator:
            raise TypeError('persistent navigation requires TerrainNavigator')
        self.grid = self.original.grid
        self._native_navigation = self.original._native_navigation
        self._original_native = self._native_navigation
        if self._native_navigation is None:
            raise ValueError('persistent navigation requires the baked native planner')
        if any(not isinstance(job, NativeSearch)
               for job in self.original.searches.values()):
            raise TypeError('unreviewed in-flight navigation receipt')
        self._jobs = dict((job.job_id, job)
                          for job in self.original.searches.values())
        self._cached_snapshot = None
        self._path_order = self.original.path_times
        self._search_order = self.original.searches
        self.closed = False

    @classmethod
    def install(cls, runtime, backend, handle):
        facade = cls(runtime, backend, handle)
        snapshot = _encode(facade.original)
        async_state = _async_encode(facade._native_navigation, facade._jobs)
        if async_state is not None and facade._native_navigation.backend is not backend:
            raise ValueError('navigation worker belongs to a different native module')
        grid_config, grid_state = _grid_encode(
            facade.grid, getattr(runtime, 'baked_graph', None))
        if backend.sim_navigation_install(handle, snapshot, grid_config, grid_state, async_state) != 1:
            raise RuntimeError('persistent navigation installation rejected')
        facade.grid = NativeGrid(facade, facade.grid)
        if async_state is not None:
            facade._native_navigation = NativeAsyncView(facade, facade._original_native)
        else:
            facade._native_navigation.grid = facade.grid
        facade.bot_states = _BotStates(facade)
        runtime.navigator = facade
        return facade

    def _snapshot(self):
        self._sync_receipts()
        self._drain_order_events()
        if self._cached_snapshot is None:
            raw = self.backend.sim_navigation_snapshot(self.handle)
            if raw is None:
                raise RuntimeError('persistent navigation snapshot rejected')
            self._cached_snapshot = _decode(raw, self._jobs)
            active_ids = set(job.job_id for job in self._cached_snapshot['searches'].values())
            for job_id in list(self._jobs):
                if job_id not in active_ids:
                    self._jobs.pop(job_id, None)
        return self._cached_snapshot

    def __getattr__(self, name):
        if name in ('paths', 'path_times', 'path_hull_revisions', 'searches',
                    'search_times', 'bot_states', 'bot_failed_edges',
                    'bot_macro_edges', 'bot_direct_progress', 'fallback_modes',
                    'fallback_totals') + SCALARS + OPTIONAL_SCALARS:
            return self._snapshot()[name]
        raise AttributeError(name)

    def detach(self):
        state = self._snapshot()
        grid_state = self.grid._snapshot()
        async_state = self.backend.sim_navigation_async_snapshot(self.handle)
        if self.backend.sim_navigation_remove(self.handle) != 1:
            raise RuntimeError('persistent navigation detach rejected')
        for name, value in state.items():
            if name in ('path_times', 'searches'):
                container = self._path_order if name == 'path_times' else self._search_order
                for key, item in value.items():
                    container[key] = item
                value = container
            setattr(self.original, name, value)
        _grid_restore(self.original.grid, grid_state)
        _async_restore(self._original_native, async_state, self._jobs)
        self._original_native.grid = self.original.grid
        if self.runtime.navigator is self:
            self.runtime.navigator = self.original
        return self.original

    def _call(self, operation, *args):
        self._cached_snapshot = None
        self.grid._snapshot_cache = None
        result = self.backend.sim_navigation_command(
            self.handle, operation, args, self._dispatch,
            ())
        if result is None:
            raise RuntimeError('persistent navigation operation %d failed' % operation)
        self._sync_receipts()
        return result[0]

    @observed('frontier.navigation_receipts')
    def _sync_receipts(self):
        if not self._jobs:
            return
        rows = self.backend.sim_navigation_receipts(self.handle)
        if rows is None:
            raise RuntimeError('persistent navigation receipt settlement rejected')
        for raw, status, last_frame, retained in rows:
            job_id, revision, timestamp, done, path, unused = raw
            job = self._jobs.get(job_id)
            if not retained:
                self._jobs.pop(job_id, None)
            if job is not None:
                job.hull_revision = int(revision)
                job.done = bool(done)
                job.result = tuple(path)
                job.status = ('done', 'cancelled', 'failed')[int(status)]
                if last_frame is not None:
                    job.last_frame = last_frame

    def _order(self, kind, events):
        container = self._path_order if kind == 0 else self._search_order
        for encoded, inserted in events:
            key = _unkey(encoded)
            if inserted:
                container[key] = None
            else:
                container.pop(key, None)
        return tuple(_key(key) for key in container)

    def _drain_order_events(self):
        events = self.backend.sim_navigation_order_events(self.handle)
        if events is None:
            raise RuntimeError('navigation container-order transfer rejected')
        for kind, rows in enumerate(events):
            self._order(kind, rows)

    def bot_state(self, bot_id):
        return self.bot_states.get(int(bot_id), {})

    def begin_frame(self, elapsed):
        self._call(0, float(elapsed))

    def end_frame(self):
        self._call(1)

    def tick(self, now):
        self._call(2, float(now))

    def next_target(self, bot_id, current, goal, path_key, now, anchor=None,
                    avoid_points=None, lookahead_distance=None,
                    movement_intent=True):
        return self._call(3, int(bot_id), tuple(current), tuple(goal),
                          _key(tuple(path_key)), float(now), _optional(anchor),
                          tuple(avoid_points or ()),
                          _optional(lookahead_distance), bool(movement_intent))

    def observe_direct_target(self, bot_id, current, goal, path_key, now,
                              movement_intent=True):
        return self._call(4, int(bot_id), tuple(current), tuple(goal),
                          _key(tuple(path_key)), float(now), bool(movement_intent))

    def report_blocked_step(self, bot_id, current, target, now):
        if target is None:
            return False
        return bool(self._call(5, int(bot_id), tuple(current), tuple(target), float(now)))

    def report_blocked_plan(self, current, target):
        return bool(self._call(6, tuple(current), tuple(target)))

    def bot_segment_penalized(self, bot_id, start, end, now):
        return bool(self._call(7, int(bot_id), tuple(start), tuple(end), float(now)))

    def controlled_shallow_step(self, bot_id, current, sample_yaw,
                                maximum_yaw_error=0.45):
        return bool(self._call(8, int(bot_id), tuple(current), float(sample_yaw),
                               float(maximum_yaw_error), False))

    def controlled_shallow_committed(self, bot_id, current, travel_yaw):
        return bool(self._call(8, int(bot_id), tuple(current), float(travel_yaw),
                               0.0, True))

    def target_is_terminal(self, bot_id):
        return bool(self._call(9, int(bot_id)))

    def close(self):
        if self.closed:
            return
        self._call(10)
        self.closed = True

    navigation_paused = staticmethod(TerrainNavigator.navigation_paused)

    def fallback_diagnostics(self, active_bot_ids=None, now=None):
        if active_bot_ids is not None:
            self._call(11, tuple(int(value) for value in active_bot_ids))
        return TerrainNavigator.fallback_diagnostics.im_func(self, None, now)

    def _dispatch(self, op, args):
        grid = self.grid
        if op == 40:
            return self._order(*args)
        if op == 30:
            try:
                clear = grid.original._native_segment_clear(*args)
            except Exception:
                combat_count('nav_async_query_failed')
                return False, False
            pending = grid.original._native_review_pending
            unknown = (not clear and grid.original.native_query_oracle is not None and
                       pending is not None and (tuple(args[0]), tuple(args[1])) in pending)
            return bool(clear), bool(unknown)
        if op == 0:
            return grid.cell_for(args[0])
        if op == 1:
            return grid.segment_has_baked_hazard(*args)
        if op == 2:
            return grid.baked_hazard_cells(*args)
        if op == 3:
            return grid.segment_clear(*args)
        if op == 4:
            return grid.dry_segment_clear(*args)
        if op == 5:
            return grid.segment_penalty(*args)
        if op == 6:
            return tuple(grid._edge_keys_for_segment(*args))
        if op == 7:
            return grid._edge_cells_for_segment(*args)
        if op == 8:
            current, goal, now, avoid, side, edges, angle = args
            return grid.safe_local_target(current, goal, now, avoid, side,
                                          dict(edges) or None, angle)
        if op == 9:
            return grid.path_has_penalty(*args)
        if op == 10:
            return grid.path_crosses_static_hull(*args)
        if op == 11:
            return grid.shortcut_preserves_baked_clearance(*args)
        if op == 12:
            return grid.live_shortcut_preserves_climb_approach(*args)
        if op == 13:
            return grid.review_native_corridor(*args)
        if op == 14:
            return grid._needs_native_review(*args)
        if op == 15:
            grid.clear_negative_cache()
            return 1
        if op == 16:
            grid._native_review_pending = set() if args[0] else None
            return 1
        if op == 17:
            grid.original.trim_caches()
            return 1
        if op == 18:
            start, goal, now, limit, prefer, hard = args
            hard = dict(hard) or None
            job = self._native_navigation.submit(
                start, goal, now, limit, bool(prefer), hard, hard, None)
            self._jobs[job.job_id] = job
            return _search(job)
        if op == 19:
            self._native_navigation.advance(*args)
            ready = []
            for job_id, job in list(self._jobs.items()):
                if job.done:
                    ready.append(_search(job))
                    self._jobs.pop(job_id, None)
            return tuple(ready)
        if op == 20:
            jobs = [self._jobs.pop(job_id) for job_id in args if job_id in self._jobs]
            self._native_navigation.cancel(jobs)
            return 1
        if op == 21:
            self._native_navigation.close()
            self._jobs.clear()
            return 1
        if op == 22:
            combat_count(*args)
            return 1
        raise ValueError('unknown navigation frontier %r' % (op,))
