"""Strict CPython 2.7 conformance for persistent native route/recovery state.

The deterministic receipt fixture exercises the full TerrainNavigator owner;
real asynchronous worker and whole-Bot checks remain separate acceptance layers.
"""
from __future__ import print_function
import sys
sys.dont_write_bytecode = True
import copy
import imp
import json
import math
import os
import types
import time


def load_sources(path):
    base = os.path.join(path, 'gui', 'mods', 'offline_lan_0922')
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [base]
        sys.modules[name] = package
    package = types.ModuleType('gui.mods.offline_lan_0922.ai')
    package.__path__ = [os.path.join(base, 'ai')]
    sys.modules[package.__name__] = package
    from gui.mods.offline_lan_0922.ai import navigation
    from gui.mods.offline_lan_0922 import native_navigation_core
    return navigation, native_navigation_core


def equal(a, b, label):
    if a != b:
        raise AssertionError('%s\nreference=%r\nnative=%r' % (label, a, b))


def graph():
    width = height = 31
    heights = [0] * (width * height)
    hazards = [0] * len(heights)
    links = []
    offsets = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0),
               (-1, 1), (0, 1), (1, 1))
    for z in range(height):
        for x in range(width):
            links.append(sum(1 << i for i, (dx, dz) in enumerate(offsets)
                             if 0 <= x + dx < width and 0 <= z + dz < height))
            if x == 15 and z not in (14, 15, 16):
                hazards[z * width + x] = 4
    return dict(format='offline-lan-0922-navgraph', version=2,
                width=width, height=height, origin=(-60., -60.),
                cell_size=4., heights_mm=heights, links=links, hazards=hazards,
                bounds=(-62., -62., 62., 62.))


class ReceiptPlanner(object):
    def __init__(self, grid, source):
        self.grid = grid
        self.source = source
        self.jobs = {}
        self.next_job_id = 0
        self.closed = False
        self.delay = .25
        self.force_empty = False
        self.completed = self.cancelled = 0

    def submit(self, start, goal, now, max_expansions, prefer_clearance,
               edge_penalties=None, hard_edge_penalties=None, avoid_points=None):
        self.next_job_id += 1
        job = self.source.NativeSearch(self.next_job_id,
                                      self.grid.static_hull_revision, now)
        job.fixture_result = (() if self.force_empty else self.grid.plan(
            start, goal, avoid_points, max_expansions, now, prefer_clearance,
            edge_penalties, hard_edge_penalties))
        self.jobs[job.job_id] = job
        return job

    def advance(self, now, budget):
        for job_id, job in list(self.jobs.items()):
            if now - job.submitted_at >= self.delay:
                job.done = True
                job.result = job.fixture_result
                job.status = 'done'
                job.last_frame = now
                self.jobs.pop(job_id)
                self.completed += 1

    def cancel(self, jobs):
        for job in jobs:
            if self.jobs.pop(job.job_id, None) is not None:
                job.done = True
                job.result = ()
                job.status = 'cancelled'
                self.cancelled += 1

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel(list(self.jobs.values()))

    def snapshot(self):
        return dict(submitted=self.next_job_id, completed=self.completed,
                    cancelled=self.cancelled, pending=len(self.jobs))


def comparable(nav):
    fields = ('paths', 'path_times', 'path_hull_revisions', 'search_times',
              'bot_states', 'bot_failed_edges', 'bot_macro_edges',
              'bot_direct_progress', 'fallback_modes', 'fallback_totals',
              'search_frame_time', 'housekeeping_time', 'search_auto_time',
              'search_next_key', 'search_credit', 'search_now',
              'search_frame_serial', 'search_processed_frame',
              'search_frame_budget', 'search_max_expansions',
              'search_completed', 'search_failed', 'fallback_recovered',
              'search_frame_open')
    result = dict((name, copy.deepcopy(getattr(nav, name))) for name in fields)
    result['searches'] = dict((key, (job.job_id, job.hull_revision,
                                    job.submitted_at, job.done, job.result))
                             for key, job in nav.searches.items())
    return result


def main():
    path = os.path.abspath(sys.argv[1])
    scripts = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client'))
    source, facade = load_sources(scripts)
    backend = imp.load_dynamic('offline_math_batch_native', path)
    from gui.mods.offline_lan_0922 import native_navigation
    create_native = source.NativeNavigation.create.im_func
    source.NativeNavigation.create = classmethod(
        lambda cls, grid: ReceiptPlanner(grid, native_navigation))
    checks = [0]

    def compare(left, right, label):
        equal(left, right, label)
        checks[0] += 1

    for scenario in range(5):
        queries = [[], []]
        navigators = []
        for side in range(2):
            def ground(x, z, hint, side=side):
                queries[side].append(('ground', x, z, hint))
                return 0.
            def obstacle(a, b, width, side=side):
                queries[side].append(('obstacle', tuple(a), tuple(b), width))
                return False
            nav = source.TerrainNavigator(ground, obstacle, baked_graph=graph())
            if scenario in (1, 2, 3):
                # The physical veto forces a real search without changing its
                # search-result law or allowing unproved local motion.
                edge = nav.grid._edge_cells_for_segment((-40., 0., 8.), (40., 0., 8.))
                nav.bot_failed_edges[7] = {edge: (30., 240.)}
            if scenario == 2:
                nav._native_navigation.force_empty = True
            if scenario == 3:
                nav._native_navigation.delay = 1.2
            navigators.append(nav)
        reference, original = navigators
        runtime = types.ModuleType('navigation_fixture')
        runtime.navigator = original
        handle = backend.sim_open(1, scenario + 1)
        native = facade.NativeNavigationCore.install(runtime, backend, handle)
        compare(comparable(reference), comparable(native), 'cold install %d' % scenario)
        elapsed = (.1, .07, .23, .05)
        now = 0.
        for frame in range(180):
            dt = elapsed[frame % len(elapsed)]
            now += dt
            reference.begin_frame(dt)
            native.begin_frame(dt)
            for bot in (7, 8, 9):
                key = ('route', 1, 'east')
                z = 8. + (bot - 7) * 12.
                current = (-40. + min(frame * .19, 35.) if scenario == 0 else -40., 0., z)
                goal = (40., 0., z)
                if frame in range(30, 55):
                    key, goal = ('local', bot, 'new'), (-50., 0., -32.)
                if scenario == 4 and frame >= 90:
                    current, goal = (0., 0., 32.), (24., 0., 32.)
                args = (bot, current, goal, key, now)
                kwargs = dict(anchor=(-40., 99., z), movement_intent=frame % 31 != 0,
                              lookahead_distance=30. if frame % 2 else None)
                compare(reference.next_target(*args, **kwargs),
                        native.next_target(*args, **kwargs),
                        'next %d/%d/%d' % (scenario, frame, bot))
                if scenario == 1 and 70 <= frame < 110:
                    compare(reference.report_blocked_step(bot, current, goal, now),
                            native.report_blocked_step(bot, current, goal, now),
                            'blocked step')
                compare(reference.target_is_terminal(bot), native.target_is_terminal(bot),
                        'terminal')
                for yaw in (0., .5, math.pi / 2.):
                    compare(reference.controlled_shallow_step(bot, current, yaw),
                            native.controlled_shallow_step(bot, current, yaw), 'shallow intent')
                    compare(reference.controlled_shallow_committed(bot, current, yaw),
                            native.controlled_shallow_committed(bot, current, yaw), 'shallow commit')
            for bot in (10, 11):
                args = (bot, (-24., 0., -24.), (32., 0., -24.),
                        ('local', bot, 'direct'), now, frame % 97 != 0)
                compare(reference.observe_direct_target(*args),
                        native.observe_direct_target(*args), 'direct target')
            reference.end_frame()
            native.end_frame()
            compare(comparable(reference), comparable(native),
                    'full state %d/%d' % (scenario, frame))
            compare(queries[0], queries[1], 'ordered native queries %d/%d' % (scenario, frame))
            queries[0][:] = []
            queries[1][:] = []
            if frame == 65:
                old_jobs = dict(native.searches)
                compare(original, native.detach(), 'detach original identity')
                compare(comparable(reference), comparable(original), 'detach current state')
                for key, job in old_jobs.items():
                    compare(True, original.searches[key] is job, 'in-flight identity')
                native = facade.NativeNavigationCore.install(runtime, backend, handle)
        compare(reference.fallback_diagnostics((7, 8, 9), now),
                native.fallback_diagnostics((7, 8, 9), now), 'diagnostics')
        reference.close()
        native.close()
        native.close()
        compare(comparable(reference), comparable(native), 'idempotent close')
        native.detach()
        backend.sim_close(handle)
        compare(None, backend.sim_navigation_snapshot(handle), 'closed lifetime')
    # Equal timestamps and a resized/deleted CPython dictionary must retire
    # exactly the same cache keys. This also checks multi-job publication order.
    for seed_kind in ('cached', 'completed'):
        pair = []
        for side in range(2):
            nav = source.TerrainNavigator(lambda *unused: 0., lambda *unused: False,
                                          baked_graph=graph())
            for index in range(120):
                start, goal = (-40., 0., 0.), (40., 0., 0.)
                key = nav._cache_key(('local', index % 29, 'capacity', index), goal)
                if seed_kind == 'cached':
                    nav.paths[key] = (start, goal)
                    nav.path_times[key] = .1
                    nav.path_hull_revisions[key] = 0
                else:
                    job = native_navigation.NativeSearch(index + 1, 0, .1)
                    job.fixture_result = (start, goal) if index % 4 else ()
                    nav.searches[key] = job
                    nav.search_times[key] = .1
                    nav._native_navigation.jobs[job.job_id] = job
                    nav._native_navigation.next_job_id = job.job_id
            if seed_kind == 'cached':
                # Preserve an actual dummy-slot history, not merely a sorted
                # list of live keys with timestamps attached.
                for index, key in enumerate(list(nav.paths)):
                    if index % 7 == 0:
                        path = nav.paths.pop(key)
                        nav.path_times.pop(key)
                        nav.path_hull_revisions.pop(key)
                        nav.paths[key] = path
                        nav.path_times[key] = .1
                        nav.path_hull_revisions[key] = 0
            pair.append(nav)
        reference, original = pair
        runtime = types.ModuleType('capacity_fixture')
        runtime.navigator = original
        handle = backend.sim_open(80, 1)
        native = facade.NativeNavigationCore.install(runtime, backend, handle)
        for nav in (reference, native):
            nav.begin_frame(.5)
            nav.tick(1.)
            nav.end_frame()
        compare(comparable(reference), comparable(native), 'equal-time cache capacity %s' % seed_kind)
        compare(80, len(native.paths), 'cache bounded trim target')
        for frame in range(5):
            now = 2. + frame
            for nav in (reference, native):
                nav.begin_frame(.1)
                for index in range(35):
                    nav.next_target(index, (-12., 0., 0.), (12., 0., 0.),
                                    ('local', index, 'new', frame), now)
                nav.end_frame()
            compare(comparable(reference), comparable(native), 'cache churn %s/%d' % (seed_kind, frame))
        native.detach()
        compare(comparable(reference), comparable(original), 'cache-order detach')
        reference.close()
        original.close()
        backend.sim_close(handle)

    # Independently compare all baked-grid decision entry points, including
    # current collision review and moving wreck occupancy. Only raw engine
    # proofs cross the callback boundary.
    pair = []
    raw_queries = [[], []]
    for side in range(2):
        def ground(x, z, hint, side=side):
            raw_queries[side].append(('ground', x, z, hint))
            return 0.
        def obstacle(a, b, width, side=side):
            raw_queries[side].append(('obstacle', tuple(a), tuple(b), width))
            return False
        pair.append(source.TerrainNavigator(ground, obstacle, baked_graph=graph()))
    reference, original = pair
    runtime = types.ModuleType('grid_fixture')
    runtime.navigator = original
    handle = backend.sim_open(81, 1)
    native = facade.NativeNavigationCore.install(runtime, backend, handle)
    for nav in (reference, native):
        nav.begin_frame(.1)
        nav.report_blocked_plan((-20., 0., -20.), (20., 0., 20.))
    for index in range(160):
        point = ((index * 17 % 151) - 75., float(index % 3), (index * 23 % 151) - 75.)
        end = ((index * 13 % 137) - 68., float(index % 4), (index * 19 % 137) - 68.)
        calls = (('cell_for', (point,)), ('near_baked_navigation', (point, index % 3)),
                 ('baked_hazard_near', (point, index % 2)),
                 ('local_corridor', (point,)),
                 ('hull_pose_clear', (point, index * .31, 2.7, 1.3)),
                 ('segment_clear', (point, end)),
                 ('dry_segment_clear', (point, end, index * .1)),
                 ('segment_penalty', (point, end, index * .1)),
                 ('_edge_keys_for_segment', (point, end)),
                 ('_edge_cells_for_segment', (point, end)),
                 ('safe_local_target', (point, end, index * .1, ((3., 0., 4.),), -1.)),
                 ('_ground', (point[0], point[2], point[1])))
        for name, args in calls:
            compare(getattr(reference.grid, name)(*args),
                    getattr(native.grid, name)(*args), 'baked grid %s/%d' % (name, index))
        for mask in (1, 2, 3, 4, 7):
            for name, args in (('segment_has_baked_hazard', (point, end, mask)),
                               ('segment_has_motion_hazard', (point, end, mask)),
                               ('baked_hazard_cells', (point, end, mask)),
                               ('point_has_baked_hazard', (point, mask))):
                compare(getattr(reference.grid, name)(*args),
                        getattr(native.grid, name)(*args), 'grid hazards %s/%d' % (name, index))
        if index % 13 == 0:
            hulls = ((99, point[0], point[2], index * .031, 2.7, 1.3),)
            compare(reference.grid.set_static_hulls(hulls), native.grid.set_static_hulls(hulls),
                    'moving wreck geometry')
            compare(reference.grid._static_hull_edges, native.grid._static_hull_edges,
                    'moving wreck exact edge set')
        compare(raw_queries[0], raw_queries[1], 'grid ordered raw engine proofs')
        raw_queries[0][:] = []
        raw_queries[1][:] = []
    native.detach()
    compare(reference.grid._native_review_cells, original.grid._native_review_cells,
            'review cells transferred back')
    compare(reference.grid._static_hull_edges, original.grid._static_hull_edges,
            'wreck edges transferred back')
    reference.close()
    original.close()
    backend.sim_close(handle)

    # Exercise the actual existing C++ worker across ownership transfer. The
    # wait ends on a query receipt or completion, never on a fixed delay alone.
    source.NativeNavigation.create = classmethod(create_native)
    from gui.mods.offline_lan_0922 import native_math
    native_math._backend = backend

    class Oracle(object):
        def __init__(self):
            self.mode = 'unknown'
            self.calls = []
            self.on_call = None

        def run(self, start, end, cell_size, up, down):
            self.calls.append((tuple(start), tuple(end), cell_size, up, down))
            if self.on_call is not None:
                self.on_call()
            if self.mode == 'exception':
                raise ValueError('fixture unknown native column')
            if self.mode == 'malformed':
                return ()
            if self.mode == 'none':
                return None
            return (-1,) if self.mode == 'unknown' else (1,)

    for mode in ('unknown', 'exception', 'malformed', 'none', 'close'):
        nav = source.TerrainNavigator(lambda *unused: 0., lambda *unused: False,
                                      baked_graph=graph())
        start, goal = (-40., 0., 8.), (40., 0., 8.)
        nav.grid.review_native_corridor(start, goal)
        job = nav._native_navigation.submit(start, goal, 0., 4096, True)
        key = nav._cache_key(('route', 1, mode), goal)
        nav.searches[key] = job
        nav.search_times[key] = 0.
        deadline = time.time() + 10.
        while not nav._native_navigation.queries:
            nav._native_navigation._poll(0.)
            if time.time() >= deadline:
                raise AssertionError('native query admission acknowledgement timed out')
            time.sleep(.001)
        context = nav._native_navigation.context
        original_queries = copy.deepcopy(nav._native_navigation.queries)
        original_order = tuple(nav._native_navigation.query_order)
        runtime = types.ModuleType('real_navigation_fixture')
        runtime.navigator = nav
        handle = backend.sim_open(42, 101)
        oracle = Oracle()
        oracle.mode = mode
        nav.grid.native_query_oracle = oracle
        native = facade.NativeNavigationCore.install(runtime, backend, handle)
        raw = backend.sim_navigation_async_snapshot(handle)
        compare(context, raw[0][0], 'same live worker context')
        compare(original_order, raw[3], 'same queued frontier order')
        compare(dict((k, tuple(v)) for k, v in original_queries.items()),
                dict(raw[2]), 'same queued frontier receipts')
        native.begin_frame(.1)
        if mode == 'close':
            oracle.on_call = lambda: backend.sim_close(handle)
            try:
                native._native_navigation.advance(.1, 4)
            except RuntimeError:
                pass
            else:
                raise AssertionError('closed authority accepted a navigation commit')
            compare(1, len(oracle.calls), 'no query replay after synchronous close')
            compare(None, backend.sim_navigation_snapshot(handle), 'closed owner inaccessible')
            backend.nav_close(context)
            continue
        native._native_navigation.advance(.1, 4)
        if mode in ('unknown', 'exception'):
            snapshot = native._native_navigation.snapshot()
            compare(0, snapshot['queries'], 'unknown receipt never admitted as clear or wall')
            compare(False, job.done, 'unknown job remains owned')
            calls_before = len(oracle.calls)
            native._native_navigation.advance(.1, 4)
            compare(calls_before, len(oracle.calls), 'same-frame pending proof not replayed')
        native.end_frame()
        oracle.mode = 'clear'
        now = .2
        deadline = time.time() + 10.
        while native._native_navigation.jobs:
            native.begin_frame(.1)
            native._native_navigation.advance(now, 96)
            native.end_frame()
            now += .1
            if time.time() >= deadline:
                raise AssertionError('native completion acknowledgement timed out')
            time.sleep(.001)
        compare(True, job.done, 'retained Python receipt settles after native ownership')
        compare('done', job.status, 'terminal receipt status retained')
        compare(True, native.searches[key] is job, 'completed receipt identity before publication')
        compare(job.last_frame, native.searches[key].last_frame, 'completion simulation time retained')
        compare(1, native._native_navigation.snapshot()['submitted'], 'admitted job never resubmitted')
        compare(1, native._native_navigation.snapshot()['completed'], 'one terminal per admitted job')
        native.begin_frame(.1)
        native.tick(now)
        native.end_frame()
        if mode != 'malformed':
            expected = nav.grid.plan(start, goal, prefer_clearance=True)
            compare(expected, native.paths[key], 'actual native worker path')
        native.detach()
        compare(context, nav._native_navigation.context, 'detach transfers same worker back')
        compare(0, len(nav._native_navigation.jobs), 'no pending owner after terminal')
        nav.close()
        backend.sim_close(handle)

    print(json.dumps(dict(checks=checks[0], scenarios=5, frames=900,
                          status='strict parity passed'), sort_keys=True))


if __name__ == '__main__':
    main()
