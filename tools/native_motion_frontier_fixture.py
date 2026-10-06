"""Exercise engine-query contracts through the real sim_motion world owner.

Only engine geometry and the skin ownership receipt are scripted here. The
production actor lifecycle, ordered world reducer, native filter stack and
original-hit effect boundary execute unchanged. This is host ABI evidence.
"""
from __future__ import print_function
import math
import struct


def _xyz(value):
    return (value.x, value.y, value.z)


def _fp32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


class FloatVector(object):
    """Explicit float32 arithmetic seam, shared by both comparison arms."""
    def __init__(self, x=0., y=0., z=0.):
        if hasattr(x, 'x'):
            x, y, z = x.x, x.y, x.z
        elif isinstance(x, (tuple, list)):
            x, y, z = x
        self.x, self.y, self.z = _fp32(x), _fp32(y), _fp32(z)
    def __iter__(self):
        return iter(_xyz(self))
    def __getitem__(self, index):
        return _xyz(self)[index]
    def __add__(self, other):
        return type(self)(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other):
        return type(self)(self.x-other.x, self.y-other.y, self.z-other.z)
    def scale(self, factor):
        factor = _fp32(factor)
        return type(self)(self.x*factor, self.y*factor, self.z*factor)
    @property
    def length(self):
        return _fp32(math.sqrt(_fp32(_fp32(self.x*self.x)+
            _fp32(self.y*self.y))+_fp32(self.z*self.z)))
    def normalise(self):
        length = self.length
        if length:
            self.x, self.y, self.z = tuple(self.scale(1./length))


class SkinScenario(object):
    def __init__(self, kind, vector):
        self.kind, self.vector = kind, vector
        self.start = self.end = None
        self.calls, self.proofs, self.hits = [], [], []
        self.effect_hits = []
        self.active, self.groups = False, 0
        self.nested = None
        self.nested_done = False
    def begin(self):
        self.active = self.groups == 0
        self.groups += 1
        return True
    def filter(self, *surface):
        return True
    def project(self, value):
        direction = self.end-self.start
        delta = value-self.start
        return sum(a*b for a,b in zip(delta,direction)) / sum(a*a for a in direction)
    def ray(self, space, start, end, flags, collision_filter):
        V = self.vector
        if self.start is None:
            self.start, self.end = V(start), V(end)
        if self.nested is not None and not self.nested_done:
            self.nested_done = True
            self.nested()
        if self.kind == 'budget':
            surfaces = [(0.1+index*.1, 73+index) for index in range(6)]
        else:
            surfaces = [(.1, 73), (.2, 74)]
            surfaces.append((.4, 0) if self.kind == 'inside_wall' else (.8, 73))
        lower, upper = self.project(start), self.project(end)
        checks, hit = [], None
        for fraction, material in surfaces:
            if not lower-1.e-7 <= fraction <= upper+1.e-7:
                continue
            # The anonymous slot changes on every native traversal. Only its
            # material/flags alias may suppress it in the proved interval.
            base = (1 << 40) if self.kind == 'long_ids' else 1000
            surface = (material, 128 if material else 0,
                       base+len(self.calls), 2000+len(self.calls))
            keep = bool(collision_filter(*surface))
            checks.append((surface, keep))
            if keep:
                point = self.start+(self.end-self.start).scale(fraction)
                hit = (point, V(0., 0., -1.), material)
                self.hits.append(hit)
                break
        self.calls.append((_xyz(start), _xyz(end), tuple(checks),
                           None if hit is None else (_xyz(hit[0]), hit[2])))
        return hit
    def skin(self, point, start, end, surfaces, normal):
        self.proofs.append((_xyz(point), tuple(sorted(surfaces))))
        fraction = self.project(point)
        if self.kind != 'budget' and fraction > .5:
            return None
        if not surfaces or any(surface[0] == 0 for surface in surfaces):
            return None
        exit_fraction = 1. if self.kind == 'budget' else .5
        distance = (self.end-self.start).length * (exit_fraction-self.project(start))
        return distance, set(surfaces)


def _actor(owner, scene, actor=16, now=.2):
    order = [actor]+[value for value in scene.runtime.states if value != actor]
    owner.begin_slice(.2, now, actor_order=order, players=[scene.human])
    for actor in order:
        owner.prepare_actor(actor)
        state = scene.runtime.states[actor]
        owner.advance_actor(actor, 1., 0., 0., move_position=(state['x'],state['y'],state['z']+30.))
        owner.after_weapon(actor)
    owner.settle_roster()


def _skin_case(scene_module, backend, NativeMotion, kind, floating=False, nested=False):
    from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
    from gui.mods.offline_lan_0922 import world_collision as world
    scene = scene_module.HostScene('frontier-'+kind, native_library='')
    scene.runtime.states[16].update(x=-40., z=-40., speed=3.)
    scene.owner._generation = 1
    V = FloatVector if floating else scene_module.Vec
    # Only the production world owner uses the selected Vector3 seam. The
    # analytic engine outside our scripted interval keeps its own Vec values.
    scene.math.Vector3 = V
    scene.owner._vector = lambda point: V(point)
    scenario = SkinScenario(kind,V)
    handle = backend.sim_open(5, 801)
    owner = NativeMotion(scene.runtime,backend,handle)
    owner.install_all()
    actual_dispatch, old_skin, old_destroy = owner.dispatch, sensor._compiled_motion_skin_1513, world._destroy_and_recast
    active_world = [False]
    def skin(*args):
        return scenario.skin(*args) if scenario.active else old_skin(*args)
    sensor._compiled_motion_skin_1513 = skin
    def destroy(space, start, end, hit, *args):
        if any(hit is value for value in scenario.hits):
            scenario.effect_hits.append(hit)
            return False
        return old_destroy(space,start,end,hit,*args)
    world._destroy_and_recast = destroy
    def dispatch(opcode, rows):
        value = actual_dispatch(opcode,rows)
        if opcode == 20 and isinstance(value,tuple) and len(value)==2:
            query = owner._engine_query
            if scenario.groups == 0:
                active_world[0] = True
                native_ray = query._native_ray
                def ray(*args):
                    return scenario.ray(*args) if scenario.active else native_ray(*args)
                query._native_ray = ray
                caps = list(value[0])
                caps[16] = scenario.begin
                return tuple(caps), value[1]
            active_world[0] = False
        if opcode == 1 and active_world[0]:
            return scenario.filter
        return value
    owner.dispatch = dispatch
    nested_handle = None
    if nested:
        nested_handle = backend.sim_open(5,802)
        nested_owner = NativeMotion(scene.runtime,backend,nested_handle)
        nested_owner.install_all()
        def reenter():
            # Preserve this oracle's script selection while a second genuine
            # world sweep installs its own native callback context.
            previous = scenario.active
            scenario.active = False
            try:
                _actor(nested_owner,scene,actor=17)
                assert not nested_owner.failures, nested_owner.failures
            finally:
                scenario.active = previous
        scenario.nested = reenter
    try:
        _actor(owner,scene)
        assert not owner.failures, owner.failures
        assert scenario.calls and scenario.hits, (kind,scenario.calls)
        assert scenario.effect_hits, (kind,'original engine hit never reached effect frontier')
        final_hit = scenario.effect_hits[0]
        # Replay the maintained Python law with the same first native segment
        # and the same scripted engine/ownership receipts, in a fresh arm.
        reference = SkinScenario(kind,V)
        sensor._compiled_motion_skin_1513 = reference.skin
        expected = sensor.collide_motion_segment(1,V(scenario.start),V(scenario.end),
            reference.filter,reference.ray)
        assert scenario.calls == reference.calls, (kind,'ordered ray/filter parity',scenario.calls,reference.calls)
        assert scenario.proofs == reference.proofs, (kind,'skin proof parity',scenario.proofs,reference.proofs)
        assert (_xyz(final_hit[0]),final_hit[2]) == (_xyz(expected[0]),expected[2])
        assert backend.engine_query_filter(0,0,0,0) == 1, 'native callback context leaked after sweep'
        if nested:
            assert scenario.nested_done, 'nested world sweep not exercised'
        if kind == 'budget':
            assert len(scenario.calls)==5 and len(scenario.proofs)==5, 'recast budget or final proof changed'
        return '%s%s%s (%d ordered rays)' % (kind,'/fp32' if floating else '',
                                            '/nested world' if nested else '',len(scenario.calls))
    finally:
        sensor._compiled_motion_skin_1513, world._destroy_and_recast = old_skin,old_destroy
        backend.sim_close(handle)
        if nested_handle is not None:
            backend.sim_close(nested_handle)


def _failure_case(scene_module,backend,NativeMotion,kind):
    scene = scene_module.HostScene('frontier-failure-'+kind,native_library='')
    scene.owner._generation = 1
    scene.runtime.states[16].update(x=-40., z=-40., speed=3.)
    if kind in ('ground_attribute', 'ground_filter'):
        scene.runtime.states[16].update(alive=False, health=0., speed=0.,
            pitch=.3, terrain_pitch=.3, push_x=2., push_z=6., _contact_dynamics=True)
    handle = backend.sim_open(5,803)
    owner = NativeMotion(scene.runtime,backend,handle)
    owner.install_all()
    original = owner.dispatch
    injected = [False]
    restore = []
    def dispatch(opcode,rows):
        value = original(opcode,rows)
        if kind == 'ground_filter' and opcode == 7 and not injected[0]:
            def failing_filter(*surface):
                if not injected[0]:
                    injected[0] = True
                    raise TypeError('scripted ground filter failure')
                return True
            return failing_filter
        if opcode == 20 and isinstance(value,tuple) and len(value)==2 and not injected[0]:
            query = owner._engine_query
            old_ray = query._native_ray
            def ray(*args):
                if not injected[0] and kind != 'ground_filter':
                    vertical = args[1].x==args[2].x and args[1].z==args[2].z
                    if kind == 'ground_attribute' and not vertical:
                        return old_ray(*args)
                    injected[0] = True
                    if kind == 'ground_attribute':
                        raise AttributeError('scripted unavailable ground ray')
                    if kind == 'ray_error':
                        raise RuntimeError('scripted engine ray failure')
                    if kind == 'malformed_hit':
                        return (scene_module.Vec(float('nan'), 0., 0.),
                                scene_module.Vec(0., 1., 0.), 0)
                    if kind == 'generation':
                        scene.owner._generation += 1
                        restore.append(lambda: setattr(scene.owner,'_generation',1))
                    elif kind == 'bots':
                        scene.owner._bots = object()
                        restore.append(lambda: setattr(scene.owner,'_bots',scene.runtime))
                    elif kind == 'round':
                        scene.runtime.round_id = 6
                        restore.append(lambda: setattr(scene.runtime,'round_id',5))
                    elif kind == 'space':
                        scene.owner._avatar.spaceID = 2
                        restore.append(lambda: setattr(scene.owner._avatar,'spaceID',1))
                return old_ray(*args)
            query._native_ray = ray
        return value
    owner.dispatch = dispatch
    try:
        _actor(owner,scene)
        assert injected[0], (kind,'failure seam not reached')
        if kind in ('ground_attribute', 'ground_filter'):
            assert not owner.failures, owner.failures
        else:
            assert scene.runtime.states[16].get('_native_motion_failed'), (kind,owner.failures)
            if kind != 'malformed_hit':
                assert owner.failures, (kind, 'Python failure not recorded')
        for action in restore:
            action()
        assert backend.engine_query_filter(0,0,0,0) == 1, 'failed callback context leaked'
        _actor(owner,scene,now=.4)
        assert not scene.runtime.states[16].get('_native_motion_failed'), (kind,'next operation did not recover')
        return kind
    finally:
        for action in restore:
            action()
        backend.sim_close(handle)


def check_frontier(scene_module,backend,NativeMotion):
    cases = [_skin_case(scene_module,backend,NativeMotion,kind,floating,nested)
             for kind,floating,nested in (
                 ('inside_wall',False,False),('tail_owner',False,False),
                 ('budget',False,False),('long_ids',False,False),
                 ('tail_owner',True,False),('tail_owner',False,True))]
    cases.extend(_failure_case(scene_module,backend,NativeMotion,kind) for kind in
                 ('ground_attribute','ground_filter','ray_error','malformed_hit',
                  'generation','bots','round','space'))
    return 'ordered native engine frontier: '+', '.join(cases)
