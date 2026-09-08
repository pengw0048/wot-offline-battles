"""Opt-in experiment: persistent native A* with the unchanged Python finish.

Only experiment runners install these patches; production imports no module
from this directory. Every native command uses one caller-owned double array.
The native module keeps graph/search data, never the array or Python objects.
Both CPython 2.7 and 3 can exercise this exact adapter.
"""
from __future__ import print_function

from array import array
import sys
import weakref


class Backend(object):
    def __init__(self, module_path):
        if sys.version_info[0] == 2:
            import imp
            self.module = imp.load_dynamic('offline_astar_native', module_path)
        else:
            import importlib.util
            spec = importlib.util.spec_from_file_location('offline_astar_native', module_path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        if self.module.layout_self_test(11, 22, 33) != 112233:
            raise RuntimeError('native A* bridge layout self-test failed')
        if hasattr(self.module, 'callback_self_test'):
            def callback_probe(a, b, c):
                return a * 10000 + b * 100 + c
            if self.module.callback_self_test(callback_probe, (11, 22, 33)) != 112233:
                raise RuntimeError('native callback bridge self-test failed')
        self.closed = False
        self.graphs = {}
        self.searches = weakref.WeakValueDictionary()
        self.calls = 0
        self.expansions = 0
        self.completed = 0
        self.original_begin = None
        self.original_advance = None

    def call(self, values):
        buffer = values if isinstance(values, array) else array('d', values)
        address, count = buffer.buffer_info()
        status = self.module.dispatch(int(address & 0xffff), int(address >> 16), int(count))
        self.calls += 1
        if status:
            raise RuntimeError('native A* command %s failed: status=%s' % (buffer[0], status))
        return buffer

    def call_sync(self, values, query_packet, callback):
        buffer = values if isinstance(values, array) else array('d', values)
        address, count = buffer.buffer_info()
        query_address, query_count = query_packet.buffer_info()
        status = self.module.dispatch_sync(
            int(address & 0xffff), int(address >> 16), int(count),
            int(query_address & 0xffff), int(query_address >> 16), int(query_count),
            callback, ())
        self.calls += 1
        if status:
            raise RuntimeError('native synchronous command %s failed: status=%s' % (buffer[0], status))
        return buffer

    def graph(self, grid, navigation):
        owner = self.graphs.get(grid)
        if owner is None:
            owner = Graph(self, grid, navigation)
            self.graphs[grid] = owner
        return owner

    def install(self, navigation, batch=True):
        if self.original_begin is not None:
            raise RuntimeError('native A* experiment is already installed')
        self.navigation = navigation
        self.original_begin = navigation.TerrainGrid.__dict__['begin_plan']
        self.original_advance = navigation.TerrainNavigator.__dict__['_advance_searches']
        backend = self

        def begin(grid, start, goal, avoid_points=None, max_expansions=1600,
                  now=0.0, prefer_clearance=False, edge_penalties=None,
                  hard_edge_penalties=None):
            if not grid.prebaked:
                raise RuntimeError('native A* experiment requires a baked graph')
            return Search(backend.graph(grid, navigation), start, goal,
                          avoid_points, max_expansions, now, prefer_clearance,
                          edge_penalties, hard_edge_penalties)

        def advance(navigator, now):
            return backend.advance(navigator, now)

        self.patched_begin = begin
        self.patched_advance = advance
        navigation.TerrainGrid.begin_plan = begin
        if batch:
            navigation.TerrainNavigator._advance_searches = advance
        return self

    def batch(self, queue, budget, now=None):
        if not queue or budget <= 0:
            return 0, queue, None
        graph = queue[0].graph
        graph.sync_globals()
        for search in queue:
            if search.graph is not graph:
                raise RuntimeError('one fair queue must belong to one graph')
            search.ensure_started()
            search.sync_inputs()
        packet = self.call([5, int(budget), len(queue), 0, 0, 0, 0, 0] +
                           [search.handle for search in queue])
        consumed, completed = int(packet[3]), int(packet[4])
        self.expansions += int(packet[6])
        if now is not None:
            for search in queue[:min(consumed, len(queue))]:
                search.last_frame = float(now)
        remaining = [self.searches[int(handle)] for handle in packet[8:8 + int(packet[5])]]
        graph.drain_expired()
        finished = self.searches[completed] if completed else None
        if finished is not None:
            finished.finish()
            self.completed += 1
        return consumed, remaining, finished

    def advance(self, navigator, now):
        """Copy the existing credit/rotation law, batch only the paid steps."""
        nav = self.navigation
        navigator.search_now = float(now)
        if not navigator.search_frame_open:
            navigator._begin_automatic_frame(now)
        if navigator.search_processed_frame == navigator.search_frame_serial:
            nav.combat_count('nav_batch_already_processed')
            return
        navigator.search_processed_frame = navigator.search_frame_serial
        navigator.search_frame_time = float(now)
        keys = sorted(navigator.searches, key=lambda value: repr(value))
        if not keys:
            navigator.search_next_key = None
            return
        if navigator.search_next_key in keys:
            start = keys.index(navigator.search_next_key)
            keys = keys[start:] + keys[:start]
        queue = [navigator.searches[key] for key in keys]
        owners = dict((id(navigator.searches[key]), key) for key in keys)
        budget = min(max(0, int(navigator.search_credit)),
                     max(0, int(navigator.search_frame_budget)))
        processed = 0
        nav.combat_count('nav_batch_pending_jobs', len(queue))
        expansions_before = self.expansions
        while budget > 0 and queue:
            consumed, queue, finished = self.batch(queue, budget, now)
            if consumed <= 0:
                raise RuntimeError('native A* made no paid progress')
            budget -= consumed
            processed += consumed
            if finished is not None:
                navigator._finish_search(owners[id(finished)], finished, now)
        navigator.search_credit = max(0.0, navigator.search_credit - processed)
        navigator.search_frame_budget = max(0, int(navigator.search_frame_budget) - processed)
        navigator.search_next_key = owners[id(queue[0])] if queue else None
        nav.combat_count('nav_astar_expansions', self.expansions - expansions_before)
        nav.combat_count('nav_search_steps', processed)
        if queue and budget <= 0:
            nav.combat_count('nav_batch_budget_exhausted')
        navigator._trim_cache(now)

    def close(self):
        if self.closed:
            return
        if self.original_begin is not None:
            if self.navigation.TerrainGrid.__dict__['begin_plan'] is self.patched_begin:
                self.navigation.TerrainGrid.begin_plan = self.original_begin
            if self.navigation.TerrainNavigator.__dict__['_advance_searches'] is self.patched_advance:
                self.navigation.TerrainNavigator._advance_searches = self.original_advance
        self.call([0])
        self.closed = True
        self.graphs.clear()


class Graph(object):
    def __init__(self, backend, grid, nav):
        self.backend, self.grid = backend, grid
        self.failed = None
        self.hulls = None
        packet = array('d', [1, grid._baked_width, grid._baked_height,
                            grid.cell_size, grid._baked_origin[0], grid._baked_origin[1],
                            grid._baked_max_grade, grid.heuristic_weight,
                            nav.BAKED_EDGE_CLEARANCE_WEIGHT,
                            nav.BAKED_SHALLOW_WATER_PENALTY, nav.SQRT_TWO])
        for height, links, hazards in zip(grid._baked_heights, grid._baked_links,
                                         grid._baked_hazards):
            packet.extend((float('nan') if height is None else height, links, hazards))
        self.handle = int(backend.call(packet)[0])

    def index(self, cell):
        if cell is None:
            return -1
        x, z = cell
        if not (0 <= x < self.grid._baked_width and 0 <= z < self.grid._baked_height):
            return -1
        return z * self.grid._baked_width + x

    def cell(self, index):
        return index % self.grid._baked_width, index // self.grid._baked_width

    def edges(self, items, timed=False, hard=False):
        result = []
        for key, value in items:
            first, second = self.index(key[0]), self.index(key[1])
            if first < 0 or second < 0:
                continue
            result.extend((first, second))
            if timed:
                result.extend((float(value[0]), float(value[1])))
            elif not hard:
                result.append(float(value))
        return result

    def sync_globals(self):
        grid = self.grid
        if self.failed == grid._failed_edges and self.hulls == grid._static_hull_edges:
            return
        failed = self.edges(grid._failed_edges.items(), timed=True)
        hulls = self.edges(grid._static_hull_edges.items())
        self.backend.call([2, self.handle, len(failed) // 4] + failed +
                          [len(hulls) // 3] + hulls)
        self.failed = dict(grid._failed_edges)
        self.hulls = dict(grid._static_hull_edges)

    def drain_expired(self):
        if not self.failed:
            return
        packet = self.backend.call([7, self.handle] + [0] * (2 * len(self.failed)))
        for index in range(int(packet[0])):
            edge = tuple(sorted((self.cell(int(packet[1 + index * 2])),
                                 self.cell(int(packet[2 + index * 2])))))
            self.grid._failed_edges.pop(edge, None)
            self.failed.pop(edge, None)


class Search(object):
    def __init__(self, graph, start, goal, avoid, maximum, now, prefer, local, hard):
        self.graph, self.start, self.goal = graph, start, goal
        self.avoid, self.local, self.hard = avoid, local, hard
        self.maximum, self.now, self.prefer = int(maximum), float(now), bool(prefer)
        self.hull_revision = graph.grid.static_hull_revision
        self.done = False
        self.result = None
        self.last_frame = None
        self.handle = None
        self.inputs = None
        self.closed = False

    def ensure_started(self):
        if self.handle is not None:
            return
        grid = self.graph.grid
        self.start_cell = grid._nearest_baked_cell(grid.cell_for(self.start), 3)
        self.goal_cell = grid._nearest_baked_cell(grid.cell_for(self.goal), 3)
        packet = self.graph.backend.call([
            3, self.graph.handle, self.graph.index(self.start_cell),
            self.graph.index(self.goal_cell), self.maximum, self.now, int(self.prefer)])
        self.handle = int(packet[0])
        self.graph.backend.searches[self.handle] = self

    def sync_inputs(self):
        avoid = tuple((float(point[0]), float(point[2])) for point in self.avoid or ())
        if (self.inputs is not None and self.inputs[0] == avoid and
                self.inputs[1] == (self.local or {}) and
                self.inputs[2] == set(self.hard or ())):
            return
        local = self.graph.edges((self.local or {}).items())
        hard = self.graph.edges(((edge, 0) for edge in self.hard or ()), hard=True)
        self.graph.backend.call(
            [4, self.handle, len(avoid)] + [v for point in avoid for v in point] +
            [len(local) // 3] + local + [int(bool(self.hard)), len(hard) // 2] + hard)
        self.inputs = (avoid, dict(self.local or {}), set(self.hard or ()))

    def finish(self):
        grid = self.graph.grid
        packet = self.graph.backend.call([6, self.handle] + [0] * max(1, self.maximum + 1))
        count = int(packet[0])
        if count:
            cells = [self.graph.cell(int(index)) for index in packet[2:2 + count]]
            path = [grid.point_for(cell, grid._baked_cell_height(cell)) for cell in cells]
            goal_y = grid._ground(float(self.goal[0]), float(self.goal[2]), path[-1][1])
            goal_point = (float(self.goal[0]), goal_y if goal_y is not None else path[-1][1],
                          float(self.goal[2]))
            if (grid.segment_clear(path[-1], goal_point) and
                    not grid.path_has_edge_penalty((path[-1], goal_point), self.hard)):
                path.append(goal_point)
            self.result = grid._smooth(tuple(path), self.now, self.prefer, self.hard)
        else:
            self.result = ()
        self.done = True
        self.close()

    def step(self, budget):
        if self.done:
            return True
        self.graph.backend.batch([self], max(1, int(budget)))
        return self.done

    def close(self):
        if not self.closed and self.handle is not None and not self.graph.backend.closed:
            self.graph.backend.call([8, self.handle])
        self.closed = True

    def __del__(self):
        self.close()
