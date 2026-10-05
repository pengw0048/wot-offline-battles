"""CPython 2 host conformance for the in-process native worker pool.

Usage: python2.7 tools/check_native_workers.py EXTENSION [CLIENT_SCRIPTS]
This proves owned-data scheduling and Python-oracle parity, not BigWorld or
Windows frame pacing. All waits require acknowledgements and have deadlines.
"""
from __future__ import print_function
import copy
import gc
import imp
import math
import os
import sys
import threading
import time
import types

sys.dont_write_bytecode = True
client_scripts = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src', 'res', 'scripts', 'client'))
sys.path.insert(0, client_scripts)
for name in ('gui', 'gui.mods'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(client_scripts, *name.split('.'))]
    sys.modules[name] = package
from gui.mods.offline_lan_0922.ai import navigation
from gui.mods.offline_lan_0922 import foliage, spotting
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])
owner = threading.current_thread().ident
checks = [0]


def equal(actual, expected):
    if isinstance(expected, (tuple, list)):
        assert isinstance(actual, (tuple, list)) and len(actual) == len(expected), (actual, expected)
        for first, second in zip(actual, expected):
            equal(first, second)
    else:
        assert abs(float(actual)-float(expected)) <= 1.e-7 * max(1., abs(float(expected))), (actual, expected)
    checks[0] += 1


def graph_for(width=19, height=13, blocked=(), shallow=(), slope=False):
    blocked = set(blocked)
    heights = [None if (x, z) in blocked else (x*200 if slope else 0)
               for z in range(height) for x in range(width)]
    links = []
    for z in range(height):
        for x in range(width):
            mask = 0
            if (x, z) not in blocked:
                for bit, (dx, dz, unused) in enumerate(navigation.TerrainGrid._NEIGHBOURS):
                    a, b = x+dx, z+dz
                    if 0 <= a < width and 0 <= b < height and (a, b) not in blocked:
                        mask |= 1 << bit
            links.append(mask)
    return dict(format=navigation.BAKED_FORMAT_NAME, version=navigation.BAKED_FORMAT_VERSION,
                origin=[0., 0.], cell_size=4., width=width, height=height,
                heights_mm=heights, links=links,
                hazards=[2 if (x, z) in blocked else 4 if (x, z) in shallow else 0
                         for z in range(height) for x in range(width)],
                bounds=[-2., -2., (width-1)*4.+2., (height-1)*4.+2.])


def grid_for(graph):
    return navigation.TerrainGrid(lambda *unused: 0.,
                                  lambda *unused: False, baked_graph=graph)


def graph_values(graph, grid):
    return [graph['origin'][0], graph['origin'][1], graph['cell_size'],
            graph['width'], graph['height'], graph['heights_mm'], graph['links'],
            graph['hazards'], graph['bounds'], grid._baked_max_grade,
            grid.heuristic_weight]


def search_values(grid, job_id, start, goal, maximum=4096,
                  clearance=True, avoid=(), edges=(), hard=(), world=()):
    return [job_id, list(start), list(goal), maximum, clearance, list(avoid),
            list(edges), list(hard), list(world), sorted(grid._native_review_cells),
            grid.static_hull_revision]


def nav_wait(context, identities, grid, receipts=None, waves=None):
    done = {}
    deadline = time.time()+15.
    while len(done) < len(identities):
        complete, queries, stats = backend.nav_poll(context)
        assert stats[5] == 2
        for row in complete:
            assert row[0] in identities and row[0] not in done
            assert row[1] in ('done', 'cancelled') and row[5] >= 0.
            done[row[0]] = row
        answers = []
        if queries and waves is not None:
            waves.append(len(queries))
        for identity, job_id, start, end in queries:
            assert threading.current_thread().ident == owner
            assert job_id in identities
            if receipts is not None:
                assert identity not in receipts
                receipts.add(identity)
            answers.append((identity, grid._native_segment_clear(start, end)))
        if answers:
            assert backend.nav_answer(context, answers)
        assert time.time() < deadline, 'Navigation acknowledgement timed out'
        if len(done) < len(identities):
            time.sleep(.001)
    return done


def navigation_checks():
    cases = [graph_for(slope=True),
             graph_for(blocked=[(9, z) for z in range(13) if z not in (2, 3)]),
             graph_for(7, 1, shallow=[(3, 0)]),
             graph_for(blocked=[(9, z) for z in range(13)])]
    for index, graph in enumerate(cases):
        grid = grid_for(graph)
        start = grid.point_for((1, 0), grid._baked_cell_height((1, 0)))
        goal_cell = (graph['width']-2, graph['height']-1)
        goal = grid.point_for(goal_cell, grid._baked_cell_height(goal_cell))
        maximum = 8 if index == 0 else 4096
        avoid = [(24., 0., 12.)] if index == 1 else []
        expected = grid.plan(start, goal, avoid, maximum, 0., True)
        data = graph_values(graph, grid)
        refs = [sys.getrefcount(value) for value in (data, data[5], data[6])]
        context = backend.nav_open(data)
        assert context is not None
        assert refs == [sys.getrefcount(value) for value in (data, data[5], data[6])]
        arguments = search_values(grid, 1, start, goal, maximum, avoid=avoid)
        before = copy.deepcopy(arguments)
        references = [sys.getrefcount(value) for value in (arguments[1], arguments[2], arguments[5])]
        assert backend.nav_submit(context, *arguments)
        assert before == arguments
        assert references == [sys.getrefcount(value) for value in (arguments[1], arguments[2], arguments[5])]
        # Mutating every borrowed source after admission must not alter work.
        arguments[1][:] = [800., 500., 800.]
        arguments[2][:] = [900., 500., 900.]
        arguments[5][:] = [(0., 0., 0.)]
        data[5][:] = [None]*len(data[5])
        del data, arguments
        gc.collect()
        row = nav_wait(context, [1], grid)[1]
        assert row[1] == 'done'
        equal(row[2], expected)
        assert backend.nav_close(context) and backend.nav_close(context)
        assert backend.nav_poll(context)[0:2] == ((), ())

    graph = graph_for()
    grid = grid_for(graph)
    grid._native_review_cells = set((x, z) for x in range(19) for z in range(13))
    start, goal = (4., 0., 4.), (60., 0., 40.)
    expected = grid.plan(start, goal, max_expansions=4096, prefer_clearance=True)
    context = backend.nav_open(graph_values(graph, grid))
    assert backend.nav_submit(context, *search_values(grid, 11, start, goal))
    receipts = set()
    waves = []
    equal(nav_wait(context, [11], grid, receipts, waves)[11][2], expected)
    assert receipts, 'Review region must issue main-thread corridor queries'
    assert len(waves) == 1, 'A clear reviewed route must be proved in one batch'
    # Cancel while awaiting native proof, then submit the same actor job ID.
    assert backend.nav_submit(context, *search_values(grid, 12, start, goal))
    deadline = time.time()+10.
    old_queries = ()
    while not old_queries:
        completed, old_queries, unused = backend.nav_poll(context)
        assert not completed
        assert time.time() < deadline
        if not old_queries: time.sleep(.001)
    assert backend.nav_cancel(context, [12])
    assert backend.nav_answer(context, [(row[0], True) for row in old_queries])
    assert nav_wait(context, [12], grid)[12][1:3] == ('cancelled', ())
    assert backend.nav_submit(context, *search_values(grid, 12, start, goal))
    backend.nav_answer(context, [(row[0], False) for row in old_queries])
    equal(nav_wait(context, [12], grid)[12][2], expected)
    assert backend.nav_submit(context, *search_values(grid, 13, start, goal))
    assert backend.nav_close(context)
    assert backend.nav_answer(context, [(row[0], True) for row in old_queries])
    assert backend.nav_poll(context)[0:2] == ((), ())
    fresh = backend.nav_open(graph_values(graph, grid))
    assert fresh != context
    assert backend.nav_submit(fresh, *search_values(grid, 13, start, goal))
    equal(nav_wait(fresh, [13], grid)[13][2], expected)
    backend.nav_close(fresh)

    # A rejected candidate edge must trigger a new owned search. A thin
    # blocker intersects the initial diagonal, and every final shortcut is
    # independently checked against it after all asynchronous proof waves.
    rejected = [0]
    def blocked(first, second, unused_radius):
        dx, dz = second[0]-first[0], second[2]-first[2]
        denominator = dx*dx+dz*dz
        fraction = ((32.-first[0])*dx+(24.-first[2])*dz)/denominator if denominator else 0.
        fraction = max(0., min(1., fraction))
        hit = (first[0]+fraction*dx-32.)**2+(first[2]+fraction*dz-24.)**2 < 36.
        if hit: rejected[0] += 1
        return hit
    grid.obstacle_probe = blocked
    grid._native_review_cache.clear()
    context = backend.nav_open(graph_values(graph, grid))
    assert backend.nav_submit(context, *search_values(grid, 14, start, goal))
    waves = []
    result = nav_wait(context, [14], grid, waves=waves)[14]
    assert result[1] == 'done' and result[2] and rejected[0] > 0 and len(waves) > 1
    for first, second in zip(result[2], result[2][1:]):
        assert grid.segment_clear(first, second), 'Unproved or blocked path was published'
    backend.nav_close(context)


def check_points(descriptor):
    points = spotting.descriptor_check_points(descriptor)
    hull = descriptor.get('chassis', {}).get('hullPosition')
    mounts = descriptor.get('hull', {}).get('turretPositions', [])
    mount = tuple(hull[i]+mounts[0][i] for i in range(3)) if hull and mounts else None
    return (tuple(points[:6]) if len(points) >= 6 else (), mount,
            descriptor.get('gun', {}).get('staticTurretYaw'))


def pose_values(pose):
    return [list(pose['position']), pose['yaw'], pose['pitch'], pose['roll'], pose['turret_yaw']]


def visibility_wait(context, identities):
    done = {}
    deadline = time.time()+10.
    while len(done) < len(identities):
        for row in backend.vis_poll(context):
            assert row[0] in identities and row[0] not in done
            assert row[1] in ('done', 'cancelled') and row[3] >= 0.
            done[row[0]] = row
        assert time.time() < deadline, 'Visibility acknowledgement timed out'
        if len(done) < len(identities): time.sleep(.001)
    return done


def visibility_checks():
    descriptor = dict(chassis=dict(hullPosition=(0., .4, 0.)),
                      hull=dict(turretPositions=[(0., 1., .2)],
                                hitTester=dict(bbox=((-1., 0., -2.), (1., 1.5, 2.)))),
                      turret=dict(gunPosition=(0., .2, 1.5),
                                  hitTester=dict(bbox=((-.5, 0., -.5), (.5, 1., .5)))),
                      gun=dict(staticTurretYaw=None))
    descriptor['visibilityCheckPoints'] = spotting.descriptor_check_points(descriptor)
    source = dict(position=(0., 0., 0.), yaw=.2, pitch=.1, roll=-.1, turret_yaw=.3)
    target = dict(position=(100., 0., 0.), yaw=-.4, pitch=.05, roll=.2, turret_yaw=-.2)
    row = [50., -4., 0., 8., .1, 0., 0., .1, .25, 14.]
    fm = foliage.FoliageMap(dict(instances=[row], cells={'1,0': [0], '1,-1': [0]}, cell_size=32.))
    # Explicit cell memberships match the fixture, including both sides of z=0.
    fm.cells = {(1, 0): [0], (1, -1): [0]}
    snapshot = [True, 32., [(0, list(row))], [(1, 0, [0]), (1, -1, [0])], []]
    detection = [100., 360., (.10, .20), True, True, .01, 1.1, .5]
    pair = [check_points(descriptor), check_points(descriptor),
            pose_values(source), pose_values(target), 1, list(detection)]
    refs = [sys.getrefcount(v) for v in (snapshot, snapshot[2], snapshot[3])]
    context = backend.vis_open(snapshot)
    assert context is not None
    assert refs == [sys.getrefcount(v) for v in (snapshot, snapshot[2], snapshot[3])]
    refs = [sys.getrefcount(v) for v in (pair, pair[2], pair[3], pair[5])]
    assert backend.vis_submit(context, 1, pair)
    assert refs == [sys.getrefcount(v) for v in (pair, pair[2], pair[3], pair[5])]
    # Future jobs get the new foliage snapshot; the admitted one stays frozen.
    assert backend.vis_update(context, [], [], [0])
    assert backend.vis_submit(context, 2, pair)
    pair[2][0][:] = [900., 100., 900.]
    pair[3][0][:] = [800., 100., 800.]
    pair[5][:] = [1., 50., (.9, .9), False, False, 0., 0., 0.]
    snapshot[2][:] = []
    del pair, snapshot
    gc.collect()
    done = visibility_wait(context, [1, 2])
    rays = []
    for a in spotting.vehicle_check_points(descriptor, source, True, 1):
        for b in spotting.vehicle_check_points(descriptor, target):
            cover = fm.camouflage_bonus(source['position'], target['position'], True, start=a, end=b)
            rays.append((a, b, cover))
    equal(done[1][2], rays)
    equal(done[2][2], [(a, b, 0.) for a, b, unused in rays])
    assert len(rays) == 6 and any(ray[2] > 0. for ray in rays)
    clear = [False, True, False, True, False, True]
    cover = min(ray[2] for ray, flag in zip(rays, clear) if flag)
    camo = spotting.effective_camouflage((.10, .20), True, .01, 1.1, .5, True, cover)
    expected = (True, cover, camo, spotting.is_detected(100., 360., camo, True),
                spotting.detection_distance(360., camo))
    assert backend.vis_reduce(context, 1, [False]) is None
    equal(backend.vis_reduce(context, 1, clear), expected)
    assert backend.vis_reduce(context, 1, clear) is None
    # A clear zero-cover ray completes the ordered native-query prefix.
    camo = spotting.effective_camouflage((.10, .20), True, .01, 1.1, .5, True, 0.)
    equal(backend.vis_reduce(context, 2, [True]),
          (True, 0., camo, spotting.is_detected(100., 360., camo, True),
           spotting.detection_distance(360., camo)))
    pair = [check_points(descriptor), check_points(descriptor),
            pose_values(source), pose_values(target), 1, detection]
    assert backend.vis_submit(context, 3, pair)
    assert backend.vis_cancel(context, [3])
    assert visibility_wait(context, [3])[3][1:3] == ('cancelled', ())
    assert backend.vis_submit(context, 4, pair)
    visibility_wait(context, [4])
    assert backend.vis_cancel(context, [4])
    assert backend.vis_reduce(context, 4, [True]) is None
    # Row replacement and cell removal publish together; invalid updates do
    # not replace the previous immutable snapshot.
    assert backend.vis_update(context, [(0, (0, row))], [(1, -1, []), (1, 0, [])], [])
    assert backend.vis_update(context, [(4, (0, row))], [], []) == 0
    assert backend.vis_submit(context, 5, pair)
    equal(visibility_wait(context, [5])[5][2], [(a, b, 0.) for a, b, unused in rays])
    assert backend.vis_submit(context, 6, pair)
    assert backend.vis_close(context) and backend.vis_close(context)
    assert backend.vis_poll(context) == () and backend.vis_reduce(context, 5, [True]) is None


def background_checks():
    graph = graph_for(160, 160, blocked=[(80, z) for z in range(160)])
    grid = grid_for(graph)
    context = backend.nav_open(graph_values(graph, grid))
    for job_id in range(20, 24):
        assert backend.nav_submit(context, *search_values(
            grid, job_id, (4., 0., 4.), (600., 0., 600.), 25600, False))
    # While Python retains its GIL, actual running C++ jobs must make progress.
    # Observing RunState::Running after submit proves work was not completed
    # synchronously in admission. Completion is the acknowledgement, not sleep.
    old_interval = sys.getcheckinterval()
    sys.setcheckinterval(100000000)
    saw_running = False
    complete = {}
    deadline = time.time()+15.
    try:
        while len(complete) < 4:
            rows, queries, stats = backend.nav_poll(context)
            assert not queries and stats[5] == 2
            saw_running = saw_running or stats[1] > 0
            for row in rows:
                assert row[1] == 'done' and row[5] > 0.
                complete[row[0]] = row
            assert time.time() < deadline, 'Workers failed to progress while Python held its GIL'
    finally:
        sys.setcheckinterval(old_interval)
        backend.nav_close(context)
    assert saw_running, 'Expected observable concurrent native work'
    checks[0] += 1


navigation_checks()
visibility_checks()
background_checks()
print('Native worker conformance passed: %d comparisons; navigation, receipts, cancellation, '
      'foliage snapshots, borrowed-input ownership, and progress under the GIL' % checks[0])
