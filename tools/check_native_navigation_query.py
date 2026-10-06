"""Complete CPython2 navigation-oracle differential and paired host timing.

Uses real TerrainGrid/BattleRuntime callers with analytic engine capabilities.
No claim about Windows engine, timing, rendering or translated x86 performance.
"""
from __future__ import print_function
import argparse
import gc
import imp
import json
import math
import os
import random
import struct
import sys
import threading
import time
import types

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument('extension')
parser.add_argument('--output')
parser.add_argument('--pairs', type=int, default=0)
options = parser.parse_args()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
client = os.path.join(root, 'src/res/scripts/client')
sys.path.insert(0, client)
for name in ('gui', 'gui.mods'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(client, *name.split('.'))]
    sys.modules[name] = package
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
from gui.mods.offline_lan_0922 import native_navigation_query as query, worker_diagnostics as diagnostic
backend = imp.load_dynamic('offline_math_batch_native', options.extension)


def obj(**values):
    result = type('OwnerComponent', (object,), {})()
    result.__dict__.update(values)
    return result


def fp32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


class Vector(object):
    __slots__ = ('x', 'y', 'z')
    def __init__(self, x, y, z):
        self.x, self.y, self.z = fp32(x), fp32(y), fp32(z)


def xyz(value):
    return value.x, value.y, value.z


class Engine(object):
    def __init__(self, scenario, record=True):
        self.scenario = scenario
        self.record = record
        self.calls = []
        self.rays = 0
        self.waters = 0
        self.owner_thread = threading.current_thread().ident

    def wg_collideSegment(self, space, start, end, flags, keep):
        assert threading.current_thread().ident == self.owner_thread
        self.rays += 1
        surface_checks = []
        hit = None
        s = self.scenario
        if s.get('ray_failure') == self.rays:
            if self.record:
                self.calls.append(('ray_error', space, xyz(start), xyz(end), flags))
            raise RuntimeError('fixture engine query failed')
        vertical = start.x == end.x and start.z == end.z
        if vertical and not s.get('missing'):
            plane = s.get('gx', 0.) * start.x + s.get('gz', 0.) * start.z
            layers = list(s.get('layers', ())) + [(plane, 1.)]
            candidates = [(height, ny) for height, ny in layers if end.y <= height <= start.y]
            candidates.sort(reverse=True)
            for height, ny in candidates:
                surface = (0, 0, 0, 1)
                accepted = bool(keep(*surface))
                surface_checks.append((surface, accepted))
                if accepted:
                    hit = (Vector(start.x, height, start.z), Vector(0., ny, 0.), 0)
                    break
        elif not vertical and s.get('surfaces'):
            for surface in s['surfaces']:
                accepted = bool(keep(*surface))
                surface_checks.append((surface, accepted))
                if accepted:
                    hit = (Vector((start.x + end.x) * .5, (start.y + end.y) * .5,
                                  (start.z + end.z) * .5), Vector(0., 0., 1.), surface[0])
                    break
        if s.get('short_hit') and vertical:
            hit = (Vector(start.x, 0., start.z),)
        if self.record:
            values = None if hit is None else tuple(xyz(value) if isinstance(value, Vector) else value for value in hit)
            self.calls.append(('ray', space, xyz(start), xyz(end), flags, surface_checks, values))
        return hit

    def wg_collideWater(self, start, end, flag):
        assert threading.current_thread().ident == self.owner_thread
        self.waters += 1
        s = self.scenario
        if self.record:
            self.calls.append(('water', xyz(start), xyz(end), flag))
        if s.get('water_failure'):
            raise RuntimeError('fixture water failed')
        if s.get('water_none'):
            return None
        return 20.0 - s.get('water', 0.)


def fixture(scenario, native, record=True, cell=18.):
    engine = Engine(scenario, record)
    owner = BattleRuntime.__new__(BattleRuntime)
    owner._avatar = obj(spaceID=7)
    def construct(x, y, z):
        if scenario.get('water_vector_failure') and y > 15.:
            raise RuntimeError('fixture water vector failed')
        return Vector(x, y, z)
    owner._runtime = obj(bigworld=engine, math=obj(Vector3=construct if scenario.get('water_vector_failure') else Vector))
    from gui.mods.offline_lan_0922 import destructibles_sensor
    owner._destructibles = destructibles_sensor
    grid = TerrainGrid(owner._navigation_ground, owner._navigation_obstacle, cell_size=cell)
    if native:
        grid.native_query_oracle = query.Oracle(owner, backend)
    return owner, grid, engine


CASES = [({}, (0., 0., 0.), (0., 0., 12.))]
for key, value in (
        ('missing', True), ('ray_failure', 1), ('ray_failure', 2), ('short_hit', True),
        ('gx', .37), ('gz', .39), ('gz', -.41), ('water', .90), ('water', .91),
        ('water_failure', True), ('water_none', True), ('water_vector_failure', True),
        ('layers', [(6., 1.), (5., 1.)]),
        ('layers', [(6., 1.), (5., 1.), (4., 0.)]),
        ('layers', [(2., 0.)])):
    CASES.append(({key: value}, (0., 0., 0.), (0., 0., 12.)))
for material in (70, 71, 85, 86, 87, 100):
    CASES.append(({'surfaces': [(material, 0, 17, 2)]}, (0., 0., 0.), (3., 0., 12.)))
CASES.extend([
    ({'surfaces': [(long(71), 0, 17, 2), (long(86), long(0), long(18), long(2))]}, (0., 0., 0.), (3., 0., 12.)),
    ({'surfaces': [(71, True, 17, 2)]}, (0., 0., 0.), (3., 0., 12.)),
    ({'surfaces': [(71., 0, 17, 2)]}, (0., 0., 0.), (3., 0., 12.)),
    ({}, (0., 0., 0.), (0., 0., 0.)),
    ({}, (0., 0., 0.), (0., 0., 300.))])
rng = random.Random(9221513)
for unused in range(250):
    start = (rng.uniform(-300., 300.), rng.uniform(-2., 2.), rng.uniform(-300., 300.))
    end = (start[0] + rng.uniform(-28., 28.), start[1], start[2] + rng.uniform(-28., 28.))
    CASES.append(({'gx': rng.uniform(-.15, .15), 'gz': rng.uniform(-.15, .15)}, start, end))

checks = []
ray_count = water_count = 0
for index, (scenario, start, end) in enumerate(CASES):
    arms = []
    for native in (False, True):
        owner, grid, engine = fixture(scenario, native)
        grid._native_review_pending = set()
        result = grid._native_segment_clear(start, end)
        # Verify a second call performs exactly the existing cache/pending law.
        again = grid._native_segment_clear(start, end)
        arms.append((result, again, engine.calls, dict(grid._native_review_cache),
                     set(grid._native_review_pending)))
    if scenario.get('water', 0.) > .90:
        # A measured forbidden water depth is a known blocker, unlike absent
        # support. The old ground callback loses that distinction as None.
        assert arms[0][:3] == arms[1][:3], ('deep_water_queries', arms)
        assert arms[0][4] and not arms[1][4]
        assert list(arms[1][3].values()) == [False]
        owner, grid, engine = fixture(scenario, True)
        assert grid.native_query_oracle.run(start, end, grid.cell_size,
                                           grid.max_grade_up, grid.max_grade_down)[0] == 0
    else:
        assert arms[0] == arms[1], ('case', index, scenario, arms)
    checks.append({'index': index, 'result': arms[0][0], 'queries': len(arms[0][2])})
    ray_count += sum(row[0] == 'ray' for row in arms[0][2])
    water_count += sum(row[0] == 'water' for row in arms[0][2])

# Detailed native scopes remain balanced, including a raw engine exception.
for failure in (False, True):
    owner, grid, engine = fixture({'ray_failure': 1} if failure else {}, True)
    d = diagnostic.WorkerCombatDiagnostics(time.clock)
    d.begin_frame(1, 1., 'fixture')
    def run():
        d.begin_slice(.1, True, True)
        d.actor(5)
        result = grid._native_segment_clear((0., 0., 0.), (0., 0., 12.))
        d.actor(None)
        return result
    result = diagnostic.call(d, 'fixture.caller', run)
    frame = d.finish_frame()
    assert frame is not None and d.enabled and not d._stack
    assert frame['stages']['native.navigation.ray']['calls'] == engine.rays
    assert frame['counts']['native.navigation.ray_queries'] == engine.rays
    assert frame['actors'][0]['bot'] == 5
    assert result == (not failure)

# The current owner/space/round must still own this exact oracle.
owner, grid, engine = fixture({}, True)
oracle = grid.native_query_oracle
owner._avatar = obj(spaceID=7)
assert oracle.run((0., 0., 0.), (0., 0., 8.), 18., .48, .38) == (-1, 0, 0)
assert not engine.calls
owner, grid, engine = fixture({}, True)
owner._avatar.spaceID = 8
assert grid.native_query_oracle.run((0., 0., 0.), (0., 0., 8.), 18., .48, .38) == (-1, 0, 0)
assert not engine.calls
owner, grid, engine = fixture({}, True)
owner._bots = object()
assert grid.native_query_oracle.run((0., 0., 0.), (0., 0., 8.), 18., .48, .38) == (-1, 0, 0)
assert not engine.calls

# Repeated borrowed capability reads must not retain input/engine objects.
owner, grid, engine = fixture({}, True, record=False)
oracle = grid.native_query_oracle
args = ((0., 0., 0.), (0., 0., 8.), 18., .48, .38)
gc.collect()
refs = tuple(sys.getrefcount(v) for v in oracle._capabilities)
for unused in range(500):
    assert oracle.run(*args)[0] == 1
gc.collect()
assert refs == tuple(sys.getrefcount(v) for v in oracle._capabilities)
first_cpu = backend.thread_cpu_seconds()
for unused in range(10000):
    math.sqrt(3.)
last_cpu = backend.thread_cpu_seconds()
assert isinstance(first_cpu, float) and last_cpu >= first_cpu

# Same query work, no diagnostics: actual grid proof + actual BattleRuntime
# oracle functions + raw capabilities. Fresh frontiers deliberately avoid the
# already-shared whole-corridor cache; cached lookup is measured separately.
SEGMENTS = []
for i in range(426):
    a = (float(i * 4), 0., float(i % 17) * 4.)
    SEGMENTS.append((a, (a[0] + 3., 0., a[2] + 12.)))

def timed_arm(native, cached):
    owner, grid, engine = fixture({}, native, record=False)
    grid._native_review_pending = set()
    if cached:
        for a, b in SEGMENTS:
            assert grid._native_segment_clear(a, b)
    before = engine.rays, engine.waters
    started = time.time()
    for a, b in SEGMENTS:
        assert grid._native_segment_clear(a, b)
    elapsed = time.time() - started
    counts = (engine.rays - before[0], engine.waters - before[1])
    if cached:
        assert counts == (0, 0)
    return elapsed, counts

trials = []
if options.pairs:
    for cached in (False, True):
        timed_arm(False, cached); timed_arm(True, cached)
        for pair in range(options.pairs):
            arms = [False, True] if pair % 2 == 0 else [True, False]
            row = {'pair': pair, 'cached': cached}
            for native in arms:
                elapsed, counts = timed_arm(native, cached)
                row['native' if native else 'python'] = {'seconds': elapsed, 'ray_count': counts[0], 'water_count': counts[1]}
            assert row['native']['ray_count'] == row['python']['ray_count']
            assert row['native']['water_count'] == row['python']['water_count']
            row['reduction_percent'] = 100. * (1. - row['native']['seconds'] / row['python']['seconds'])
            trials.append(row)

def median(values):
    values = sorted(values)
    count = len(values)
    return .5 * (values[(count - 1) // 2] + values[count // 2])

summary = []
for cached in (False, True):
    rows = [row for row in trials if row['cached'] == cached]
    if rows:
        summary.append({'cached': cached, 'pairs': len(rows), 'complete_corridors_per_arm': len(SEGMENTS),
                        'python_ms_median': median([row['python']['seconds'] * 1000. for row in rows]),
                        'native_ms_median': median([row['native']['seconds'] * 1000. for row in rows]),
                        'paired_reduction_percent_median': median([row['reduction_percent'] for row in rows]),
                        'native_faster_pairs': sum(row['reduction_percent'] > 0 for row in rows)})
result = {'python': sys.version, 'extension': os.path.abspath(options.extension), 'cases': len(CASES),
          'exact_result_query_order_and_counts_parity': True,
          'exact_cache_parity_except_known_deepwater': True,
          'known_deepwater_status_zero_cached_false_not_pending': True,
          'differential_ray_calls': ray_count,
          'differential_water_calls': water_count, 'diagnostic_balanced_success_and_failure': True,
          'borrowed_refs_unchanged': True, 'stale_avatar_space_and_round_no_queries': True,
          'thread_cpu_seconds': [first_cpu, last_cpu], 'summary': summary, 'trials': trials,
          'limitations': ['Actual Python callers; engine is analytic fake, not BigWorld timing.',
                         '426 fresh corridor proofs isolate oracle benefit; warm lookup deliberately shows no oracle work.',
                         'No native Windows or FPS claim; diagnostics disabled for paired timings.']}
if options.output:
    with open(options.output, 'w') as output:
        json.dump(result, output, indent=2)
print(json.dumps({k: v for k, v in result.items() if k not in ('trials',)}, indent=2))
