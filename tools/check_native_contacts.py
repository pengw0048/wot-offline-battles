"""CPython 2.7 conformance for synchronous whole-roster native contacts.

Use EXTENSION [CLIENT_SCRIPTS], or --extension PATH --client-scripts PATH.
CLIENT_SCRIPTS accepts the client
scripts root or the offline_lan_0922 module directory. This checks pure laws,
ordered synchronous armor settlement, and owned output; it does not establish
BigWorld behavior, retail physics, Windows memory safety, or frame pacing.
"""
from __future__ import print_function
import sys
sys.dont_write_bytecode = True
import argparse
import copy
import gc
import imp
import json
import math
import os
import random
import threading
import types


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('extension_path', nargs='?')
    parser.add_argument('client_path', nargs='?')
    parser.add_argument('--extension')
    parser.add_argument('--client-scripts')
    args = parser.parse_args()
    if args.extension and args.extension_path:
        parser.error('Supply the extension either positionally or with --extension')
    if args.client_scripts and args.client_path:
        parser.error('Supply client scripts either positionally or with --client-scripts')
    args.extension = args.extension or args.extension_path
    if not args.extension:
        parser.error('An extension path is required')
    args.client_scripts = args.client_scripts or args.client_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client')
    return args


def load_sources(path):
    module_dir = os.path.join(path, 'gui', 'mods', 'offline_lan_0922')
    if not os.path.isfile(os.path.join(module_dir, 'tank_collision.py')):
        module_dir = path
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [module_dir] if name.endswith('0922') else []
        sys.modules[name] = package
    from gui.mods.offline_lan_0922 import tank_collision, native_math
    assert callable(getattr(tank_collision, 'finalize_contact_row', None)), (
        'Client source must provide the original resolve_tank and finalize_contact_row')
    return tank_collision, native_math


class Checker(object):
    def __init__(self, source, native_math, backend):
        self.source, self.native_math, self.backend = source, native_math, backend
        self.owner_thread = threading.current_thread().ident
        self.comparisons = 0
        self.maximum = 0.
        self.cases = 0
        self.native_calls = 0
        self.candidates = 0
        self.armor_queries = 0
        self.events = 0
        self.fallback_checks = 0

    def equal(self, actual, expected, path):
        if isinstance(expected, dict):
            assert isinstance(actual, dict) and set(actual) == set(expected), path
            for key in expected:
                self.equal(actual[key], expected[key], path+'.'+str(key))
        elif isinstance(expected, (tuple, list)):
            assert isinstance(actual, (tuple, list)) and len(actual) == len(expected), (path, actual, expected)
            for index, (first, second) in enumerate(zip(actual, expected)):
                self.equal(first, second, path+'[%d]' % index)
        elif isinstance(expected, (set, frozenset)):
            assert set(actual) == set(expected), (path, actual, expected)
        elif isinstance(expected, float):
            delta = abs(float(actual)-expected)
            self.maximum = max(self.maximum, delta)
            assert delta <= 1.e-9*max(1., abs(expected)), (path, actual, expected, delta)
        else:
            assert actual == expected, (path, actual, expected)
        self.comparisons += 1

    def peers(self, tanks, by_id, own):
        source = self.source
        radii = dict((tank['id'], math.sqrt(sum(value*value for value in
                     source._tank_shape(tank)[:2]))) for tank in tanks)
        collision = dict((tank['id'], {'position': (tank['x'], tank.get('y'), tank['z'])})
                         for tank in tanks)
        index = source.build_spatial_index(collision, max([4.]+radii.values())*2.+4.)
        others = []
        for identity in source.nearby_ids(index, own['x'], own['z']):
            if identity == own['id']:
                continue
            other = by_id[identity]
            reach = radii[own['id']]+radii[identity]+source.CONTACT_BROADPHASE_PADDING
            if (own['x']-other['x'])**2+(own['z']-other['z'])**2 <= reach*reach:
                others.append(other)
        return others

    def probe(self, trace, mode):
        cache = {}
        def probe(first, second, contact):
            assert threading.current_thread().ident == self.owner_thread
            first_id, second_id = first['id'], second['id']
            pair = tuple(sorted((first_id, second_id)))
            if pair not in cache:
                trace.append((first_id, second_id, contact))
                if mode == 'missing':
                    cache[pair] = None
                elif mode == 'zero':
                    cache[pair] = (1000000., 1000000.)
                elif mode == 'partial':
                    cache[pair] = (None, 55.)
                else:
                    values = (30.+first_id, 45.+second_id)
                    cache[pair] = values if first_id <= second_id else tuple(reversed(values))
            values = cache[pair]
            return values if values is None or first_id <= second_id else tuple(reversed(values))
        return probe

    def run(self, label, tanks, dt=.2, prior=(), mode='valid'):
        source = self.source
        by_id = dict((tank['id'], tank) for tank in tanks)
        owner_ids = [tank['id'] for tank in tanks if tank['kind'] != 'player']
        physical = source.resolve_pairs(tanks, dt)
        post = source.post_contact_velocity_bodies(tanks, physical)
        for identity, delta in source.traverse_impulses(post, dt, angular_results=physical).items():
            physical[identity]['delta_velocity'] = tuple(
                physical[identity]['delta_velocity'][i]+delta[i] for i in (0, 1))
        inputs = [list(by_id.values()), owner_ids, dt, list(prior)]
        before = copy.deepcopy(inputs)
        tracked = (inputs[0], inputs[1], inputs[3]) + tuple(inputs[0])
        references = [sys.getrefcount(value) for value in tracked]
        rows = self.backend.contact_roster(*inputs)
        self.native_calls += 1
        assert rows is not None, (label, 'Unexpected native fallback')
        assert references == [sys.getrefcount(value) for value in tracked], (label, 'Borrowed input retained')
        assert inputs == before, (label, 'Input mutated')
        assert [row[0] for row in rows] == owner_ids, (label, 'Owner order changed')
        old_active, new_active = set(prior), set(prior)
        old_current, new_current = set(), set()
        old_cooldowns, new_cooldowns = {}, {}
        old_trace, new_trace = [], []
        frame_events = 0
        old_probe, new_probe = self.probe(old_trace, mode), self.probe(new_trace, mode)
        for row in rows:
            own = by_id[row[0]]
            peers = self.peers(tanks, by_id, own)
            name = label+'.owner%d' % own['id']
            self.equal(row[1], physical[own['id']]['correction'], name+'.correction')
            self.equal(row[2], physical[own['id']]['delta_velocity'], name+'.velocity')
            self.equal(row[3], physical[own['id']]['delta_yaw'], name+'.yaw')
            self.equal(bool(row[4]), bool(peers), name+'.has_peers')
            self.equal(bool(row[5]), any(peer.get('alive', True) or peer['vx'] or
                peer['vz'] or peer.get('push_yaw') for peer in peers), name+'.active_peer')
            alive = own.get('alive', True)
            common = dict(now=1. if alive else None, dt=dt)
            old = source.resolve_tank(own, peers, ram_cooldowns=old_cooldowns,
                active_ram_contacts=old_active, contact_armor_probe=old_probe, **common)
            old.update(physical[own['id']])
            old.pop('responses', None)
            new = source.finalize_contact_row(own, by_id, row, now=common['now'],
                ram_cooldowns=new_cooldowns, active_ram_contacts=new_active,
                contact_armor_probe=new_probe)
            if not alive:
                # Worker wreck consumer uses physical response only and never
                # feeds resolve_tank's compatibility contact set back to state.
                old.pop('contacts', None)
                new.pop('contacts', None)
            self.equal(new, old, name+'.settlement')
            if alive:
                old_cooldowns, new_cooldowns = old['cooldowns'], new['cooldowns']
                old_current.update(old['contacts'])
                new_current.update(new['contacts'])
                old_active = set(prior) | old_current
                new_active = set(prior) | new_current
            self.candidates += len(row[7])
            self.events += len(new['ram_events'])
            frame_events += len(new['ram_events'])
        self.equal(new_trace, old_trace, label+'.ordered_armor')
        self.equal(new_current, old_current, label+'.frame_contacts')
        self.equal(new_cooldowns, old_cooldowns, label+'.cooldowns')
        assert inputs == before, (label, 'Settlement mutated frozen inputs')
        self.armor_queries += len(new_trace)
        self.cases += 1
        self.last_contacts = frozenset(new_current)
        self.last_event_count = frame_events
        return rows

    def lifecycle(self):
        tanks = [body(1, vz=14.), body(2, z=5.5)]
        owners, previous = [1, 2], []
        outputs = self.backend.contact_roster(tanks, owners, .2, previous)
        self.native_calls += 1
        expected = copy.deepcopy(outputs)
        tanks[0]['shape'] = (900., 900., -10., 10.)
        tanks[1]['vx'] = 800.
        owners[:] = []
        previous[:] = [(8, 9)]
        del tanks, owners, previous
        gc.collect()
        self.equal(outputs, expected, 'output_after_input_release')
        for unused in range(16):
            temporary = [body(1), body(2, z=5.5)]
            result = self.backend.contact_roster(temporary, [1, 2], .2, [])
            assert result is not None
            self.native_calls += 1
            del temporary, result
        gc.collect()
        # Unsupported inputs must not invoke Python conversion/user hooks or
        # poison the next valid synchronous operation.
        called = []
        class Convertible(object):
            def __float__(self):
                called.append('float')
                return 1.
        class CustomBody(dict):
            def get(self, *args):
                called.append('get')
                return dict.get(self, *args)
        valid = [body(1), body(2, z=5.5)]
        invalid = [([dict(valid[0], vx=float('nan'))], [1], .2, []),
                   ([dict(valid[0], mass=Convertible())], [1], .2, []),
                   ([CustomBody(valid[0])], [1], .2, []),
                   (valid, [1, 1], .2, []), (valid, [999], .2, []),
                   (valid, [1], float('inf'), []),
                   (valid, [1], .2, frozenset()),
                   ([dict(valid[0], id=2**80)], [1], .2, []),
                   (valid, [1], .2, [(1,)]), (valid, [1], .2)]
        for args in invalid:
            assert self.backend.contact_roster(*args) is None, ('Expected local fallback', args)
            self.fallback_checks += 1
            assert self.backend.contact_roster(valid, [1, 2], .2, []) is not None
            self.native_calls += 1
        assert not called, ('Unsafe conversion hooks called', called)
        self.native_math._backend = self.backend
        self.native_math._attempted = True
        before = self.native_math.snapshot()
        assert self.native_math.contact_roster(valid, [1, 2], .2, frozenset(((1, 2),))) is not None
        after = self.native_math.snapshot()
        assert after['contact_roster'] == before['contact_roster']+1
        assert after['fallbacks'] == before['fallbacks']
        assert self.native_math.contact_roster(valid, [999], .2, frozenset()) is None
        assert self.native_math.snapshot()['fallbacks'] == after['fallbacks']+1
        assert self.native_math.contact_roster(valid, [1, 2], .2, frozenset()) is not None
        self.native_calls += 2


def body(identity, x=0., z=0., **changes):
    value = dict(id=identity, kind='bot', x=x, y=0., z=z, yaw=0.,
        pitch=0., roll=0., shape=(1.5, 3.5, -.8, 2.), mass=30000.,
        vx=0., vy=0., vz=0., alive=True, team=1+(identity % 2),
        vehicle='fixture', push_yaw=0., traverse_speed=0., traverse_torque=0.,
        contact_decel=None, ram_profile=dict(spall_coefficient=1., ramming_bonus=0.))
    value.update(changes)
    return value


def controlled_cases():
    cases = []
    for mode in ('valid', 'missing', 'partial', 'zero'):
        cases.append(('armor_'+mode, [body(1, vz=14.), body(2, z=5.5)], .2, (), mode))
    cases.extend([
        ('live_cluster_owner_order', [body(3, x=1., z=5., vz=-4.),
            body(1, vz=14.), body(2, x=-1., z=5.5)], .2, (), 'valid'),
        ('wreck_yaw', [body(1, x=-1., vx=8., vz=4.),
            body(2, x=1., z=2., alive=False, push_yaw=.2)], .2, (), 'valid'),
        ('settled_wrecks', [body(1, alive=False), body(2, z=2., alive=False)], .2, (), 'valid'),
        ('dead_player_fixed', [body(1, vz=14.), body(2, z=5.5, kind='player',
            alive=False, immovable=True, position_fixed=True)], .2, (), 'valid'),
        ('player_ledger_impulse_false', [body(1, vz=14.), body(2, z=5.5,
            kind='player', impulse=False, position_fixed=True)], .2, (), 'valid'),
        ('held_track', [body(1, vz=.2), body(2, z=5.5, contact_decel=(4., 10.))], .2, (), 'valid'),
        ('vertical_separated', [body(1, vz=14.), body(2, z=5.5, y=9.)], .2, (), 'valid'),
        ('pitch_roll', [body(1, x=-1., vx=8., vz=4., pitch=.2),
            body(2, x=1., z=2., yaw=.35, pitch=.45, roll=-.3)], .2, (), 'valid'),
        ('corner_traverse', [body(1, x=-1., vx=3., vz=2., traverse_speed=.25,
            traverse_torque=300000.), body(2, x=1., z=2., yaw=.35)], .2, (), 'valid'),
        ('prior_episode', [body(1, vz=14.), body(2, z=5.5)], .2, ((1, 2),), 'valid'),
        ('prior_separated', [body(1), body(2, z=15.)], .2, ((1, 2),), 'valid'),
        ('friendly', [body(1, vz=14., team=1), body(2, z=5.5, team=1)], .2, (), 'valid'),
        ('subunit_mass', [body(1, mass=.25, vz=14.), body(2, z=5.5, mass=.75)], .2, (), 'valid'),
        ('zero_dt', [body(1, vz=14.), body(2, z=5.5)], 0., (), 'valid'),
        ('first_impact_deep', [body(1, vz=14., vy=50.), body(2, z=.5)], .2, (), 'valid'),
        ('crossed_impact_plane', [body(1, vz=14.), body(2, z=-.5)], .2, (), 'valid'),
        ('spall_controlled_impact', [body(1, vz=14., ram_profile=dict(
            spall_coefficient=1.5, ramming_bonus=.15)), body(2, z=5.5,
            contact_armor=35.)], .2, (), 'valid'),
        ('player_player_only', [body(1, kind='player'),
            body(2, z=2., kind='player')], .2, (), 'valid'),
        ('empty_roster', [], .2, (), 'valid'),
    ])
    return cases


def random_cases():
    rng = random.Random(9221513)
    cases = []
    for index in range(64):
        tanks = []
        for identity in range(1, rng.randrange(3, 9)):
            alive, player = rng.random() > .2, rng.random() < .15
            tanks.append(body(identity, x=rng.uniform(-8., 8.), z=rng.uniform(-8., 8.),
                y=rng.choice((0., 0., 5.)), yaw=rng.uniform(-math.pi, math.pi),
                pitch=rng.uniform(-.35, .35), roll=rng.uniform(-.35, .35),
                mass=rng.uniform(1200., 120000.),
                shape=(rng.uniform(.6, 2.5), rng.uniform(1.5, 5.), -.8, 2.),
                vx=rng.uniform(-8., 8.), vy=rng.uniform(-3., 3.), vz=rng.uniform(-8., 8.),
                alive=alive, kind='player' if player else 'bot', team=rng.choice((0, 1, 2)),
                immovable=player and not alive, position_fixed=player, impulse=not player,
                push_yaw=rng.uniform(-.2, .2) if not alive else 0.,
                traverse_speed=rng.uniform(-.4, .4) if alive else 0.,
                traverse_torque=rng.uniform(1000., 600000.),
                contact_decel=rng.choice((None, (.6, 3.), (4., 10.)))))
        rng.shuffle(tanks)
        prior = ((1, 2),) if index % 7 == 0 else ()
        cases.append(('random_%d' % index, tanks, rng.choice((0., .001, .05, .2)),
                      prior, 'missing' if index % 3 == 0 else 'valid'))
    return cases


def episode_checks(checker):
    tanks = [body(1, vz=.01), body(2, z=5.5)]
    checker.run('episode_harmless', tanks, mode='zero')
    assert not checker.last_contacts and not checker.last_event_count
    tanks[0]['vz'] = 14.
    checker.run('episode_impact', tanks)
    assert checker.last_contacts == frozenset(((1, 2),)) and checker.last_event_count == 1
    checker.run('episode_sustained', tanks, prior=checker.last_contacts)
    assert checker.last_contacts == frozenset(((1, 2),)) and not checker.last_event_count
    tanks[1]['z'] = 15.
    checker.run('episode_separated', tanks, prior=checker.last_contacts)
    assert not checker.last_contacts and not checker.last_event_count
    tanks[1]['z'] = 5.5
    checker.run('episode_new_impact', tanks, prior=checker.last_contacts)
    assert checker.last_contacts == frozenset(((1, 2),)) and checker.last_event_count == 1


def main():
    args = arguments()
    assert sys.version_info[:2] == (2, 7), 'Run this checker with CPython 2.7'
    source, native_math = load_sources(os.path.abspath(args.client_scripts))
    backend = imp.load_dynamic('offline_math_batch_native', os.path.abspath(args.extension))
    checker = Checker(source, native_math, backend)
    controlled = controlled_cases()
    for label, tanks, dt, prior, mode in controlled + random_cases():
        checker.run(label, tanks, dt, prior, mode)
    assert checker.candidates and checker.armor_queries and checker.events, 'Ram coverage did not execute'
    episode_checks(checker)
    checker.lifecycle()
    print(json.dumps(dict(cases=checker.cases, controlled_cases=len(controlled),
        comparisons=checker.comparisons, maximum_absolute_difference=checker.maximum,
        successful_native_calls=checker.native_calls, unsupported_fallback_checks=checker.fallback_checks,
        ram_candidates=checker.candidates, main_thread_armor_queries=checker.armor_queries,
        ram_events=checker.events, failures=0, seed=9221513,
        scope='Pure contact and synchronous Python boundary conformance; no Windows runtime evidence.'),
        indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
