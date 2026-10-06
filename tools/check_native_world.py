"""Python2.7 host checks for the complete synchronous native world stage.

Usage: python2.7 tools/check_native_world.py EXTENSION [CLIENT_SCRIPTS]
Engine calls use analytic fixtures; this is not Windows timing acceptance.
"""
from __future__ import print_function
import gc
import imp
import math
import os
import sys
import threading
import types
from StringIO import StringIO

sys.dont_write_bytecode = True
client_scripts = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src', 'res', 'scripts', 'client'))
sys.path.insert(0, client_scripts)
for name in ('gui', 'gui.mods'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(client_scripts, *name.split('.'))]
    sys.modules[name] = package
from gui.mods.offline_lan_0922 import native_math, native_world, world_collision as world
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])
native_math._attempted = True
owner = threading.current_thread().ident


class Vector(object):
    def __init__(self, x=0., y=0., z=0.):
        self.x, self.y, self.z = float(x), float(y), float(z)
    def __add__(self, other):
        return Vector(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other):
        return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    def scale(self, value):
        return Vector(self.x*value, self.y*value, self.z*value)
    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length
    @property
    def length(self):
        return math.sqrt(self.x*self.x+self.y*self.y+self.z*self.z)


def object_for(**values):
    result = type('Component', (object,), {})()
    result.__dict__.update(values)
    return result


def xyz(value):
    return value.x, value.y, value.z


def equal(actual, expected):
    if isinstance(expected, (tuple, list)):
        assert isinstance(actual, (tuple, list)) and len(actual) == len(expected), (actual, expected)
        for first, second in zip(actual, expected):
            equal(first, second)
    elif isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key in expected:
            equal(actual[key], expected[key])
    elif isinstance(expected, float):
        assert abs(actual-expected) <= 1.e-7*max(1., abs(expected)), (actual, expected)
    else:
        assert actual == expected, (actual, expected)


def miss_material(*unused):
    return False, Vector(), Vector(), 0, '', 0, 0


math_module = object_for(Vector3=Vector)
descriptor = object_for(hull=object_for(hitTester=object_for(
    bbox=((-1.6, -1., -4.), (1.6, 1., 6.), None))),
    chassis=object_for(hullPosition=(0., 0., 0.), hitTester=object_for(
        bbox=((-1.6, -1., -4.), (1.6, 1., 6.), None))))


def scene_for(grade=0., wall=False, beam=False, corner=False):
    queries = []
    sine, cosine = math.sin(.55), math.cos(.55)
    corner_v = cosine*(-1.6)-sine*6.
    def collide(space, start, end, mask, *extra):
        assert threading.current_thread().ident == owner
        delta = end-start
        hits = []
        gx, gz = (grade*sine, grade*cosine) if corner else (0., grade)
        denominator = delta.y-gx*delta.x-gz*delta.z
        vertical = abs(delta.x) < 1.e-9 and abs(delta.z) < 1.e-9
        corridor = (abs(delta.x*cosine-delta.z*sine) < 1.e-9 and
                    abs(start.x*cosine-start.z*sine-corner_v) < 1.e-9)
        if abs(denominator) > 1.e-12 and (not corner or vertical or corridor):
            fraction = (gx*start.x+gz*start.z-start.y)/denominator
            if 0. <= fraction <= 1.:
                hits.append((fraction, Vector(-gx, 1., -gz)))
        if (wall or beam) and abs(delta.z) > 1.e-12:
            fraction = (2.8-start.z)/delta.z
            height = start.y+delta.y*fraction
            if 0. <= fraction <= 1. and (1.05 <= height <= 2. if beam else 0. <= height <= 1.2):
                hits.append((fraction, Vector(0., 0., -1.)))
        hit = None
        if hits:
            fraction, normal = min(hits, key=lambda row: row[0])
            hit = (start+delta.scale(fraction), normal, 0)
        queries.append((xyz(start), xyz(end), mask,
                        None if hit is None else (xyz(hit[0]), xyz(hit[1]), hit[2])))
        return hit
    return object_for(wg_collideSegment=collide, wg_getMatInfoNearPoint=miss_material), queries


cases = []
for grade in (-.6, 0., .6):
    for obstacle in ('clear', 'wall', 'beam'):
        for speed in (-12., 12.):
            cases.append((dict(grade=grade, wall=obstacle=='wall', beam=obstacle=='beam'),
                          dict(pitch=-math.atan(grade), roll=.13, dt=.3), speed))
for motion_yaw in (0., .55, math.pi/2., math.pi):
    cases.append((dict(wall=True), dict(motion_yaw=motion_yaw, pitch=.21,
                                      roll=-.18, dt=.3), 5.))
cases.append((dict(grade=.135, corner=True), dict(motion_yaw=.55, dt=.5), 5.))
cases.append((dict(beam=True), dict(airborne=True, dt=.296), 0.))
cases.append((dict(wall=True), dict(exact_footprint=True, dt=.296), 5.))
checks = 0
ray_count = 0
for config, arguments, speed in cases:
    arms = []
    for selected in (None, backend):
        native_math._backend = selected
        scene, queries = scene_for(**config)
        trace = {}
        status = world.check_horizontal_collision(scene, math_module, 1, Vector(),
                    0., speed, descriptor, return_status=True, trace=trace, **arguments)
        arms.append((status, queries, trace))
    equal(arms[1], arms[0])
    ray_count += len(arms[1][1])
    checks += 1
    if config.get('corner'):
        assert arms[1][0] == 'clear'
        queries = arms[1][1]
        index = next(i for i, row in enumerate(queries) if row[3] is not None and
                     abs(row[0][1]-.6) < 1.e-9 and abs(row[1][1]-.6) < 1.e-9)
        start, end = queries[index][:2]
        for column, row in enumerate(queries[index+1:index+8]):
            for axis in (0, 2):
                equal(row[0][axis], start[axis]+(end[axis]-start[axis])*column/6.)
        assert abs(start[0]) > 1.e-3

# The direct adapter preserves an engine exception after committed effects.
native_math._backend = backend
commits, queries = [], []
error = RuntimeError('engine failure after first-lane destruction')
def collide_after_commit(space, start, end, mask, *unused):
    assert threading.current_thread().ident == owner
    queries.append((xyz(start), xyz(end)))
    if commits:
        raise error
    return start+(end-start).scale(.25), Vector(0., 0., -1.), 0
original_resolve = world._destroy_and_recast
def resolve(*args):
    commits.append(args[3])
    return True
world._destroy_and_recast = resolve
missing = object()
previous_modules = dict((name, sys.modules.get(name, missing))
                        for name in ('BigWorld', 'Math'))
try:
    scene = object_for(wg_collideSegment=collide_after_commit,
                       wg_getMatInfoNearPoint=miss_material)
    sys.modules['BigWorld'], sys.modules['Math'] = scene, math_module
    try:
        native_world.run(world, 1, Vector(), 0., 5., None, False, .08)
    except RuntimeError as caught:
        assert caught is error
    else:
        raise AssertionError('The engine error was swallowed')
finally:
    for name, previous in previous_modules.items():
        if previous is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
assert len(commits) == 3 and len(queries) == 4, (len(commits), len(queries))
checks += 1

# The public motion boundary rejects only this operation, without replaying
# earlier destruction or disabling the backend used by the next actor.
failure_count = native_math.snapshot()['world_failures']
diagnostic = StringIO()
previous_stdout = sys.stdout
sys.stdout = diagnostic
try:
    for return_status in (False, True):
        del commits[:]
        del queries[:]
        trace = {}
        result = world.check_horizontal_collision(scene, math_module, 1,
                    Vector(), 0., 5., None, False, .08,
                    return_status=return_status, trace=trace)
        assert result == ('hard' if return_status else True)
        assert trace['reason'] == 'native_world_error'
        assert trace['error'] == str(error)
        assert len(commits) == 3 and len(queries) == 4, (len(commits), len(queries))
        assert native_math._backend is backend
        checks += 1
    next_scene, next_queries = scene_for()
    assert world.check_horizontal_collision(next_scene, math_module, 1,
                Vector(20., 0., 0.), 0., 5., return_status=True) == 'clear'
    assert next_queries and native_math._backend is backend
    checks += 1
finally:
    sys.stdout = previous_stdout
    world._destroy_and_recast = original_resolve
assert native_math.snapshot()['world_failures'] == failure_count+2
assert diagnostic.getvalue().count('blocking this motion without replay') == 1
assert 'using Python' not in diagnostic.getvalue()

# Borrowed callback/inputs are not kept after synchronous execution.
snapshot = (0., 0., 0., 0., 5., .08, 0., 0., 0., -1.5, 1.5, 3.5,
            3.5, .6, 1.6, False, False, False, False)
def clear_dispatch(opcode, rows):
    assert threading.current_thread().ident == owner
    if opcode in (1, 2, 6):
        return None
    return tuple((None, None, 0, row[0], row[1]) for row in rows)

# Unsupported snapshots return None before a dispatcher has run at all.
invalid_snapshot = list(snapshot)
invalid_snapshot[0] = float('nan')
unexpected_callbacks = []
def unexpected_dispatch(opcode, rows):
    unexpected_callbacks.append(opcode)
    return clear_dispatch(opcode, rows)
assert backend.world_run(tuple(invalid_snapshot), unexpected_dispatch) is None
assert not unexpected_callbacks
checks += 1

# Malformed dispatcher output reaches the real bridge's post-entry -2 guard.
malformed_callbacks = []
def malformed_dispatch(opcode, rows):
    malformed_callbacks.append(opcode)
    if opcode in (1, 2, 6):
        return None
    return tuple((None,) for row in rows)
assert backend.world_run(snapshot, malformed_dispatch) == -2
assert malformed_callbacks == [2, 1, 3]
checks += 1
def malformed_world(snapshot, unused_dispatch):
    return backend.world_run(snapshot, malformed_dispatch)
native_math._backend = object_for(world_run=malformed_world)
try:
    try:
        native_math.world_run(snapshot, clear_dispatch)
    except RuntimeError as caught:
        assert str(caught) == 'Native world stage failed after engine dispatch'
    else:
        raise AssertionError('The invalid native result was accepted')
    trace = {}
    assert world.check_horizontal_collision(next_scene, math_module, 1,
                Vector(), 0., 5., return_status=True, trace=trace) == 'hard'
    assert trace['reason'] == 'native_world_error'
    assert trace['error'] == 'Native world stage failed after engine dispatch'
finally:
    native_math._backend = backend
assert native_math.snapshot()['world_failures'] == failure_count+3
checks += 1

# Process-control exceptions are never treated as an ordinary motion failure.
interrupt = KeyboardInterrupt('stop native world caller')
def interrupted_world(*unused):
    raise interrupt
native_math._backend = object_for(world_run=interrupted_world)
try:
    try:
        world.check_horizontal_collision(next_scene, math_module, 1,
                    Vector(), 0., 5., return_status=True)
    except KeyboardInterrupt as caught:
        assert caught is interrupt
    else:
        raise AssertionError('BaseException was swallowed')
finally:
    native_math._backend = backend
assert native_math.snapshot()['world_failures'] == failure_count+3
checks += 1
# A synchronous dispatcher may reenter another world call on the same GIL.
nested = [False]
def reentrant_dispatch(opcode, rows):
    if opcode == 1 and not nested[0]:
        nested[0] = True
        assert backend.world_run(snapshot, clear_dispatch) == 0
    return clear_dispatch(opcode, rows)
assert backend.world_run(snapshot, reentrant_dispatch) == 0 and nested[0]
checks += 1
gc.collect()
references = sys.getrefcount(snapshot), sys.getrefcount(clear_dispatch)
for unused in range(100):
    assert backend.world_run(snapshot, clear_dispatch) == 0
gc.collect()
assert references == (sys.getrefcount(snapshot), sys.getrefcount(clear_dispatch))
checks += 1
assert native_math.snapshot()['world_run'] == len(cases)+1
assert native_math.snapshot()['fallbacks'] == 0
print('Native world host checks passed: %d cases; %d ordered engine rays; '
      'same-thread reentry, corner profile, local failure containment and owned refs.' %
      (checks, ray_count))
