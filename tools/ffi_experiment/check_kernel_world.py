#!/usr/bin/env python
"""Compare native collision arbitration with the unchanged battle resolver.

The source executes its real sweep and resolver. Scripted leaves cover return
contracts and failures; populated catalog scenes execute real contact, commit,
publication, pending-skin and filter code against deterministic native fakes.
"""
from __future__ import print_function
import argparse
import contextlib
import copy
import itertools
import math
import random
import sys

from check_kernel_state import compare, plain
from kernel_adapter import Kernel, bot_config, ENGINE_FIELDS, motion_config
from kernel_engine import EngineLeaves
from kernel_world import WorldLeaves, descriptor_profile, _STATUSES
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, Namespace, patch_dict, redirected


@contextlib.contextmanager
def replaced(owner, **values):
    old = dict((key, getattr(owner, key)) for key in values)
    for key, value in values.items():
        setattr(owner, key, value)
    try:
        yield
    finally:
        for key, value in old.items():
            setattr(owner, key, value)


def point(value):
    return (value.x, value.y, value.z)


class Counts(object):
    def __init__(self):
        self.values = {}

    def count(self, name):
        self.values[name] = self.values.get(name, 0) + 1


class Audit(object):
    def __init__(self, backend, fixture):
        from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
        from gui.mods.offline_lan_0922 import world_collision
        self.world, self.fixture, self.backend = world_collision, fixture, backend
        with redirected():
            self.runtime, unused_ground = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
        self.rt, self.vector = fixture['fixtures']._load(), fixture['_Vector']
        self.identity = sorted(self.runtime.states)[0]
        self.base_state = dict(self.runtime.states[self.identity])
        self.descriptor = self.runtime._descriptors[self.identity]
        self.descriptor.physics['weight'] = 40000.0
        self.owner = BattleRuntime.__new__(BattleRuntime)
        self.owner.__dict__.update(
            _bots=self.runtime, _avatar=Namespace(spaceID=1),
            _vector=lambda value: self.vector(*value), _bot_motion_kinds={},
            _combat_diagnostics=Counts())
        self.engine = EngineLeaves(self.rt, self.runtime)
        self.engine.owned = True
        self.rows = 0
        self.outcomes = {}

    def kernel(self):
        identity, runtime = self.identity, self.runtime
        value = bot_config(self.base_state, runtime._gun_states[identity],
                           runtime._ammo_states[identity], runtime._burst_states[identity])
        value['config'] = dict(motion=dict(physics=runtime._physics_params_for(identity),
                                         world=descriptor_profile(self.descriptor)))
        return Kernel(self.backend, self.rt.bot_state_codec, [value],
                      engine=dict(fields=ENGINE_FIELDS), motion=motion_config(self.rt, runtime))

    def state(self, case):
        return dict(self.base_state, x=0, y=0, z=0, yaw=case.get('yaw', 0),
                    speed=case.get('speed', 4), airborne=case.get('airborne', False),
                    movement_dir=case.get('drive', 1), rotation_dir=case.get('turn', 0),
                    terrain_pitch=case.get('pitch', 0), roll=case.get('roll', 0))

    def run(self, kernel, case, native):
        state, owner, runtime = self.state(case), self.owner, self.runtime
        runtime.states[self.identity] = state
        runtime._turn_speeds[self.identity] = case.get('turn_speed', 0)
        runtime.motion_world_corridor_reusable = lambda *unused: case.get('reuse', False)
        owner._bot_motion_kinds = {self.identity: 'previous'}
        owner._combat_diagnostics = Counts()
        motion_yaw = case.get('motion_yaw')
        commit = case.get('commit', True) if motion_yaw is not None else True
        position = case.get('position', (0, 0, 0))
        if native:
            query = [615, self.identity] + list(position) + [
                motion_yaw if motion_yaw is not None else state['yaw'],
                state['speed'], case.get('dt', .04), 10.0,
                int(motion_yaw is not None), int(commit)] + [0] * 9
            query += [int(case.get('reuse', False)), 0]
            assert len(query) == 22
            self.engine.world = WorldLeaves(owner, self.engine)
            try:
                rows = kernel.motion_actions([
                    dict(method='set', id=self.identity, state=state,
                         turn_speed=case.get('turn_speed', 0)),
                    dict(method='world', id=self.identity, query=query)], self.engine)
                result = _STATUSES[rows[-1]['result']]
                assert self.engine.world.context is None
            except Exception as error:
                result = ('error', type(error).__name__)
            finally:
                self.engine.world.clear()
        else:
            try:
                result = owner._resolve_bot_motion(
                    self.identity, position, state['yaw'], state['speed'], self.descriptor,
                    case.get('dt', .04), 10.0, commit,
                    motion_yaw=(motion_yaw + (math.pi if state['speed'] < 0 else 0)
                                if motion_yaw is not None else None))
            except Exception as error:
                result = ('error', type(error).__name__)
        return (result, dict(owner._bot_motion_kinds), dict(owner._combat_diagnostics.values))

    def scripted(self, kernel, case, native):
        tape, v = [], self.vector
        fault = case.get('fault')

        def note(kind, *values):
            tape.append((kind,) + values)
            if kind == fault:
                raise RuntimeError('injected ' + kind)

        def catalog_call(kind, *args, **kwargs):
            position, yaw, speed, descriptor = args[:4]
            note(kind, point(position), yaw, speed, args[4:], kwargs)
            if case.get('retire_catalog') and kind == 'catalog':
                self.runtime.round_id += 1
            return case.get(kind, False if kind in ('guard', 'pending') else dict(status='clear'))

        self.owner._destructibles = None if case.get('no_catalog') else Namespace(
            _catalog_hull_contact=lambda *a, **kw: catalog_call('guard', *a, **kw),
            _catalog_pending_at_hull=lambda *a, **kw: catalog_call('pending', *a, **kw),
            _catalog_motion_blocked=lambda space, *a, **kw: catalog_call('catalog', *a, **kw))
        filter_token = object()

        def prepare(start, end):
            note('prepare', point(start), point(end))
            return filter_token if case.get('filter') else None

        def collide(space, start, end, mask, *filters):
            note('ray', space, point(start), point(end), mask, bool(filters))
            if abs(start.x - end.x) < 1e-9 and abs(start.z - end.z) < 1e-9:
                if case.get('invalid_ground'):
                    return v(start.x, float('nan'), start.z), v(0, 1, 0), 0
                height = (.496 if start.x < -294 else .384) if case.get('power_rounding') else 0
                return (v(start.x, height, start.z), v(0, 1, 0), 0) if min(start.y, end.y) <= height <= max(start.y, end.y) else None
            if case.get('retire_round') and start.y == 1.1:
                self.runtime.round_id += 1
            if case.get('upper_failure') and start.y == 1.1:
                raise RuntimeError('injected first upper ray failure')
            if case.get('invalid_upper') and start.y == 1.1:
                return v(float('nan'), start.y, start.z), v(0, 0, -1), 75
            if case.get('wall'):
                normal = object() if case.get('bad_normal') else v(0, 0, -1)
                return (v((start.x + end.x) * .5, (start.y + end.y) * .5,
                          (start.z + end.z) * .5), normal, 75)
            return None

        def destroy(space, start, end, hit, yaw, speed, descriptor, crush,
                    allow_kinetic, kinetic_speed, commit, collision_filter):
            note('destroy', point(start), point(end), point(hit[0]), yaw, speed,
                 crush[0], allow_kinetic, kinetic_speed, commit,
                 collision_filter is filter_token)
            result = case.get('destroy', False)
            if result is True:
                crush[0] = True
            return result

        bigworld, math_module = Namespace(wg_collideSegment=collide), Namespace(Vector3=v)
        self.owner._runtime = Namespace(bigworld=bigworld, math=math_module)
        with patch_dict(sys.modules, dict(BigWorld=bigworld, Math=math_module)):
            with replaced(self.world, prepare_horizontal_collision_filter=prepare,
                          _destroy_and_recast=destroy):
                result = self.run(kernel, case, native)
        return result, tape

    def check_scripted(self, kernel, case):
        expected = self.scripted(kernel, case, False)
        actual = self.scripted(kernel, case, True)
        compare(plain(expected), plain(actual), ('world', self.rows, case))
        self.rows += 1
        key = str(expected[0][0])
        self.outcomes[key] = self.outcomes.get(key, 0) + 1

    def contracts(self, kernel):
        rounding = dict(position=(-295.2507744825458, .496, 384.6935827796212),
                        yaw=2.1156409658631405, speed=3.3158922348808404, dt=.07,
                        pitch=.02058014125132504, roll=.014903167058722107, power_rounding=True)
        self.check_scripted(kernel, rounding)
        unused_result, tape = self.scripted(kernel, rounding, False)
        first = next(row for row in tape if row[0] == 'ray' and row[2][0] != row[3][0])
        # The discontinuous lower floor no longer pulls down the occupied hull ray.
        assert first[3][1] == .9984518347729856
        for speed, drive, airborne, turn, reuse, wall in itertools.product(
                (-4, 0, 4), (-1, 0, 1), (False, True), (0, 1), (False, True), (False, True)):
            self.check_scripted(kernel, dict(speed=speed, drive=drive, airborne=airborne,
                                            turn=turn, reuse=reuse, wall=wall, filter=True))
        details = [False, True, 'clear', 'crushed', 'soft', 'hard', 'approach',
                   dict(status='crushed', accepted_now=True, used_kinetic_speed=True,
                        token=((22, 37, None),), kinds='fragile'),
                   dict(status='clear', accepted_now=True),
                   dict(status='soft', accepted_now=True),
                   dict(status='approach', accepted_now=True),
                   dict(status='crushed', used_kinetic_speed=True),
                   dict(status='crushed', accepted_now=True, used_kinetic_speed=True),
                   dict(status='pending'), None, 17]
        for detail, destroyed, wall in itertools.product(details, (False, True, 'kinetic'), (False, True)):
            self.check_scripted(kernel, dict(catalog=detail, destroy=destroyed, wall=wall))
        for phase in ('guard', 'prepare', 'ray', 'destroy', 'pending', 'catalog'):
            self.check_scripted(kernel, dict(fault=phase, wall=phase in ('destroy', 'pending'),
                                            reuse=phase == 'guard'))
            self.check_scripted(kernel, {}) # Same native owner works after failure.
        rng = random.Random(7721513)
        for index in range(200):
            self.check_scripted(kernel, dict(
                speed=rng.choice((-8, -.01, 0, .01, 10)), drive=rng.choice((-1, 0, 1)),
                yaw=rng.uniform(-3, 3), pitch=rng.choice((0, -.17, .12)),
                roll=rng.choice((0, -.08, .07)), turn_speed=rng.choice((0, .01, .010001)),
                motion_yaw=rng.uniform(-3, 3) if index % 2 else None,
                commit=index % 3 != 0, dt=rng.choice((.001, .04, .27)),
                reuse=index % 4 == 0, guard=index % 3 == 0, pending=index % 5 == 0,
                no_catalog=index % 7 == 0, wall=index % 2 == 0,
                destroy=rng.choice((False, True, 'kinetic')), bad_normal=index % 3 == 0))
        # Compare error barriers with the existing scalar native reader. An
        # invalid first upper answer must not issue the second paired ray.
        from world_adapter import SyncWorldBackend
        scalar = SyncWorldBackend(self.backend)
        try:
            for name in ('upper_failure', 'invalid_upper', 'invalid_ground'):
                case = {name: True, 'pitch': .1 if name == 'invalid_ground' else 0}
                self.check_scripted(kernel, case)
                result, tape = self.scripted(kernel, case, True)
                assert result[0] == ('error', 'RuntimeError')
                assert not any(row[0] == 'ray' and row[2][1] == 1.6 for row in tape)
        finally:
            scalar.close()

    def lifetimes(self, kernel):
        round_id = self.runtime.round_id
        try:
            result, tape = self.scripted(kernel, dict(retire_round=True), True)
            assert result[0] == ('error', 'RuntimeError')
            assert self.engine.world.context is None
            assert not any(row[0] == 'ray' and row[2][1] == 1.6 for row in tape)
        finally:
            self.runtime.round_id = round_id
        try:
            result, unused_tape = self.scripted(kernel, dict(retire_catalog=True), True)
            assert result[0] == ('error', 'RuntimeError')
            assert self.engine.world.context is None
        finally:
            self.runtime.round_id = round_id
        self.check_scripted(kernel, {})
        from kernel_adapter import KernelBackend
        navigator, driver = self.runtime.navigator, self.runtime.adapter.driver
        wrong_owner = copy.copy(self.owner)
        wrong_owner._bots = object()
        try:
            KernelBackend(self.backend, self.runtime, self.rt, world_owner=wrong_owner)
        except ValueError:
            pass
        else:
            raise AssertionError('Mismatched battle owner was admitted')
        assert self.runtime.navigator is navigator
        assert self.runtime.adapter.driver is driver
        self.check_scripted(kernel, {})

    def populated(self, kernel, case, native):
        sensor, v = self.fixture['destructibles_sensor'], self.vector
        kind = case.get('kind', 'fragile')
        filename = 'content/GatesAndFences/ffi/normal/lod0/fence.model'
        materials = (73, 74) if kind == 'structure' else (None,)
        sensor.set_catalog(self.fixture['_catalog']({filename: dict(
            kind=kind, boxes=[[-.25, -.2, -.5, .25, 1.5, .5, mat] for mat in materials])}))
        record = sensor._destructible_catalog['resources'][filename.lower()]
        instances, bins = {}, {}
        for identity, x in ((37, -.2), (38, .2)):
            matrix = self.fixture['_ItemMatrix'](v(x, 0, case.get('obstacle_z', 3.5)))
            instance = dict(filename=filename.lower(), kind=kind, item_scale=1.0,
                            boxes=sensor._world_catalog_boxes(record, matrix, v(), Namespace(Vector3=v)))
            instances[(22, identity)] = instance
            sensor._index_catalog_instance_1513(bins, (22, identity), instance)
        sensor.g_offh_destr_instances = instances
        sensor.g_offh_destr_contact_bins = bins
        destroyed, events, tape = set(), [], []

        def destroy(chunk, identity, material, position):
            tape.append(('destroy', chunk, identity, material, point(position)))
            destroyed.add((chunk, identity, material))
            return True

        authority = Namespace(
            is_destroyed=lambda chunk, item, mat=None: (chunk, item, mat) in destroyed,
            destroyed_keys=lambda chunk: set((item, mat) for cid, item, mat in destroyed if cid == chunk),
            destroy_fragile=lambda space, chunk, item, position, shot: destroy(chunk, item, None, position),
            destroy_module=lambda space, chunk, item, mat, position, shot: destroy(chunk, item, mat, position))
        area = Namespace(
            DESTR_TYPE_TREE=1, DESTR_TYPE_FALLING_ATOM=2, DESTR_TYPE_FRAGILE=3,
            DESTR_TYPE_STRUCTURE=4, DESTRUCTIBLE_HIDING_DELAY=.2,
            g_destructiblesManager=object(),
            g_cache=Namespace(unitVehicleMass=10000.0, getDescByFilename=lambda name: dict(
                type=4 if kind == 'structure' else 3, health=5, kineticDamageCorrection=1.0,
                modules=dict((mat, dict(health=5)) for mat in materials))))

        def collide(space, start, end, mask, *filters):
            tested = tuple((item, mat, collision_filter(75 if mat is None else mat, 0, item, 22))
                           for collision_filter in filters for item in (37, 38) for mat in materials)
            tape.append(('ray', point(start), point(end), tested))
            if abs(start.x - end.x) < 1e-9 and abs(start.z - end.z) < 1e-9:
                return (v(start.x, 0, start.z), v(0, 1, 0), 0) if min(start.y, end.y) <= 0 <= max(start.y, end.y) else None
            return None

        bigworld = Namespace(wg_collideSegment=collide, time=lambda: 10.0)
        math_module = Namespace(Vector3=v)
        cache = Namespace(scaledDestructibleHealth=lambda scale, health: scale * health)
        self.owner._runtime = Namespace(bigworld=bigworld, math=math_module)
        self.owner._destructibles = sensor
        old_sink = sensor._event_sink
        sensor.set_event_sink(lambda event: events.append(copy.deepcopy(event)) or True)
        try:
            with patch_dict(sys.modules, dict(BigWorld=bigworld, Math=math_module,
                                              AreaDestructibles=area, DestructiblesCache=cache)):
                with replaced(sensor, _get_destr_authority=lambda: authority):
                    results = [self.run(kernel, case, native), self.run(kernel, case, native)]
                    pending = sorted(sensor.__dict__.get('g_offh_destr_pending', {}).items())
                    published = sensor.__dict__.get('g_offh_tree_state', {}).get('publish_pending', {})
                    assert not published
            return results, sorted(destroyed), events, tape, pending
        finally:
            sensor.set_event_sink(old_sink)
            sensor.set_catalog(None)

    def catalogs(self, kernel):
        for kind in ('fragile', 'structure'):
            cases = [dict(speed=20), dict(speed=.01), dict(speed=.01, drive=0),
                     dict(speed=20, motion_yaw=0, commit=False), dict(airborne=True),
                     dict(speed=.01, obstacle_z=7), dict(speed=-20, drive=-1, obstacle_z=-3.5)]
            for case in cases:
                case['kind'] = kind
                expected = self.populated(kernel, case, False)
                actual = self.populated(kernel, case, True)
                compare(plain(expected), plain(actual), ('populated', case))
                if (case.get('speed', 0) > 0 and case.get('drive', 1) and
                        case.get('commit', True) and case.get('obstacle_z', 3.5) == 3.5):
                    assert len(expected[1]) == (4 if kind == 'structure' else 2), expected[:3]
                    assert expected[2], 'Native destruction had no canonical publication'
                self.rows += 1

    def profiles(self):
        original = self.descriptor
        try:
            for forward, backward, width in ((3.0, 1.5, 2.1), (18.0, 9.0, 1.2)):
                self.descriptor = copy.deepcopy(original)
                self.descriptor.physics['speedLimits'] = (forward, backward)
                self.descriptor.hull.hitTester.bbox = ((-width, -.2, -4.1), (width, 1.4, 3.8), None)
                self.runtime._descriptors[self.identity] = self.descriptor
                kernel = self.kernel()
                try:
                    for speed, motion in itertools.product((-4, 4), (None, .25)):
                        self.check_scripted(kernel, dict(speed=speed, drive=-1 if speed < 0 else 1,
                                                        motion_yaw=motion, wall=True, destroy='kinetic',
                                                        catalog=dict(status='crushed', accepted_now=True,
                                                                     used_kinetic_speed=True, token=(1,))))
                finally:
                    kernel.close()
        finally:
            self.descriptor = original
            self.runtime._descriptors[self.identity] = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    backend = Backend(args.module)
    audit = Audit(backend, fixture)
    kernel = audit.kernel()
    try:
        audit.contracts(kernel)
        audit.catalogs(kernel)
        audit.lifetimes(kernel)
        audit.profiles()
    finally:
        kernel.close()
        backend.close()
    print('Native world resolver parity: %d cases; outcomes=%r' % (audit.rows, audit.outcomes))


if __name__ == '__main__':
    main()
