"""Real CPython 2.7 sight binding/Store integration; no Windows timing claim."""
from __future__ import print_function
import imp
import math
import os
import struct
import sys
import time
import types

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(HERE), 'src', 'res', 'scripts', 'client')
sys.path.insert(0, HERE)
import check_native_simulation_control as fixture
facade, driver, traffic = fixture.load_sources(CLIENT)
from gui.mods.offline_lan_0922 import battle_visibility, native_visibility
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])


def fp32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


class Vector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = fp32(x), fp32(y), fp32(z)
    def __sub__(self, other):
        return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    @property
    def length(self):
        return math.sqrt(self.x*self.x+self.y*self.y+self.z*self.z)


class Box(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Owner(object):
    def _bot_visibility_async(self, *unused):
        raise AssertionError('per-pair Python sight callback escaped frontier')
    def _optional_feature_enabled(self, name):
        return False
    def _server_entity(self, identity):
        return self.entities[identity]
    def _sight_collision_filter(self):
        return None
    def _turret_server_time_ms(self):
        self.clock_calls += 1
        return 4200.


getattr(Owner._bot_visibility_async, 'im_func', Owner._bot_visibility_async).__module__ = (
    'gui.mods.offline_lan_0922.battle_runtime')


def run(blocked):
    checker = fixture.Checker(backend, facade, driver, traffic)
    control, runtime = checker.owner()
    runtime._probe_totals = [0, 0]
    owner = Owner()
    owner.clock_calls = 0
    owner._avatar = Box(spaceID=9)
    owner._bots = Box(round_id=3)
    owner._generation = 4
    owner._foliage = None
    owner._destructibles = None
    calls = []
    def ray(space, start, end, flags, *filter_arg):
        calls.append((space, (start.x, start.y, start.z),
                      (end.x, end.y, end.z), flags))
        if blocked:
            return Vector((start.x+end.x)*.5, (start.y+end.y)*.5, (start.z+end.z)*.5), Vector(0., 1., 0.)
        return None
    owner._runtime = Box(bigworld=Box(wg_collideSegment=ray), math=Box(Vector3=Vector))
    descriptor = dict(chassis=dict(hullPosition=(0., .4, 0.)),
        hull=dict(turretPositions=[(0., 1., .2)], hitTester=dict(bbox=((-1., 0., -2.), (1., 1.5, 2.)))),
        turret=dict(gunPosition=(0., .2, 1.5), hitTester=dict(bbox=((-.5, 0., -.5), (.5, 1., .5)))),
        gun=dict(staticTurretYaw=None))
    owner.entities = {11: Box(typeDescriptor=descriptor), 12: Box(typeDescriptor=descriptor)}
    owner._records = {'bot:1': {'engine_id': 11}, 'bot:2': {'engine_id': 12}}
    service = native_visibility.NativeVisibility(backend, None, False)
    owner._native_visibility = service
    owner._native_visibility_owner = (4, 3, 9)
    runtime.visibility_async_probe = owner._bot_visibility_async
    runtime.states = {1: dict(id=1, x=0., y=0., z=0., yaw=.2, speed=2., alive=True),
                      2: dict(id=2, x=100., y=0., z=0., yaw=-.4, speed=2., alive=True)}
    control._sources = dict(((1, key), state) for key, state in runtime.states.items())
    control._templates = dict(control._sources)
    order = ((1, 1), (1, 2))
    for key in order:
        config = (key[1], 0, 3.5, 1.7, 360., 360., -1., .2, .1,
                  0., 0., 1., 1., .5, -1., 300., 0., 0.)
        control.configure(key, config)
    control.update_samples(tuple((key, facade._pose(runtime.states[key[1]]), 0, 9, None) for key in order))
    # The same BattleRuntime map can still own an admitted legacy positive-ID
    # job while the control frontier owns negative-ID jobs. Neither poll steals.
    legacy_pair = (service._checkpoints(descriptor), service._checkpoints(descriptor),
        native_visibility._pose(runtime.states[1]), native_visibility._pose(runtime.states[2]),
        2, (100., 360., (.1, .2), True, False, 0., 1., .5))
    assert backend.vis_submit(service.context, 77, legacy_pair)
    now, sequence = 1., 0
    deadline = time.time()+2.
    while time.time() < deadline:
        sequence += 1
        control.begin_samples(order, now, 48, True, False)
        control._sight_binding = battle_visibility.bind_visibility(control)
        rows = control.contacts((1, 1), control._sight_binding)
        control.finish()
        count = runtime._probe_totals[0]
        control.finish()  # A duplicate finish must not report probes twice.
        assert runtime._probe_totals[0] == count
        stats = service.snapshot()
        if stats['completed']:
            assert stats['completed'] == 1 and stats['pending'] == 0
            assert owner.clock_calls == stats['frontier_requests']
            assert stats['submitted'] == 1
            assert stats['frontier_requests'] == runtime._probe_totals[0]
            assert bool(rows and rows[0][1]&1) == (not blocked), (blocked, rows)
            if not blocked:
                assert rows[0][0] == (1, 2) and rows[0][3] == 1.
            break
        assert not calls
        now += .000001
    else:
        raise AssertionError('worker did not acknowledge prepared sight job')
    assert len(calls) == (6 if blocked else 1), (blocked, calls)
    from gui.mods.offline_lan_0922 import spotting
    expected = []
    for a in spotting.vehicle_check_points(descriptor, runtime.states[1], True, 2):
        for b in spotting.vehicle_check_points(descriptor, runtime.states[2]):
            start, end = Vector(*a), Vector(*b)
            expected.append((9, (start.x, start.y, start.z), (end.x, end.y, end.z), 128))
            if not blocked:
                break
        if not blocked:
            break
    assert calls == expected, (calls, expected)

    legacy = ()
    deadline = time.time()+2.
    while not legacy and time.time()<deadline:
        legacy = backend.vis_poll(service.context)
    assert len(legacy)==1 and legacy[0][0]==77 and legacy[0][1]=='done', legacy
    assert backend.vis_reduce(service.context,77,(True,))[0]
    saved = list(calls)
    # A control detach releases strong actor owners but preserves the shared map.
    control.detach()
    control.detach()
    assert not service.closed
    assert backend.vis_frontier_snapshot(service.context)[3] == 0
    assert runtime._probe_totals[0] == count
    service.close()
    backend.sim_close(control.handle)
    return saved


clear = run(False)
blocked = run(True)
assert clear[0] == blocked[0]
print('native visibility/control binding: clear/blocked + ordered rays + sampled lease + duplicate finish/detach passed')
