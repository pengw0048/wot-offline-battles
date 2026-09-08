#!/usr/bin/env python
"""Differential preparation/integration with callback-visible state and caches."""
from __future__ import print_function
import argparse
import copy
import inspect
import random
import textwrap

from navigation_adapter import Backend
from driver_adapter import DriverBackend
from navigation_flow_adapter import NavigationFlowBackend
from motion_adapter import MotionFlowBackend
from portable_workload import load_fixture, Path, ROOT, redirected


def equivalent(first, second):
    if isinstance(first, dict) and isinstance(second, dict):
        return set(first) == set(second) and all(equivalent(first[key], second[key]) for key in first)
    if isinstance(first, (tuple, list)) and isinstance(second, (tuple, list)):
        return len(first) == len(second) and all(equivalent(a, b) for a, b in zip(first, second))
    return first == second


def reference(module):
    source = open(inspect.getsourcefile(module.BotRuntime), 'rb').read().decode('utf-8')
    start = source.index("            throttle = max(-1.0, min(1.0, command['throttle']))", source.index('    def _update_once('))
    end = source.index("            if diagnostic is not None:\n                diagnostic.phase('bot.aim_fire')", start)
    header = '''def original_step(self, state, command, target, descriptor, position, step, now,
        decision_due, refresh_control, siege_motion_locked, tick_siege_yaw,
        siege_locked_poses, attempted_yaws, baked_shallow_escape, diagnostic):
    controlled_shallow = getattr(self.navigator, 'controlled_shallow_step', None)
    controlled_shallow_commit = getattr(self.navigator, 'controlled_shallow_commit_step', None)
'''
    code = header + '\n'.join('    ' + line for line in textwrap.dedent(source[start:end]).splitlines()) + '\n    return destroyed_devices\n'
    scope = dict(module.__dict__)
    eval(compile(code, '<source-motion-block>', 'exec'), scope)
    return scope['original_step']


def run(args):
    fixtures = load_fixture(args.fixture)
    module = fixtures['fixtures']._load()
    from gui.mods.offline_lan_0922.ai import navigation
    first, unused = fixtures['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    second, unused = fixtures['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    backend = Backend(args.module)
    driver = DriverBackend(backend, second, True)
    nav = NavigationFlowBackend(backend, second, navigation)
    motion = MotionFlowBackend(backend, second, module)
    navigation_events = []
    publish_event = second.navigator._publish_event
    def observe_navigation(packet=None):
        if packet is not None:
            navigation_events.append(int(packet[0]))
        return publish_event(packet)
    second.navigator._publish_event = observe_navigation
    original = reference(module)
    initial = copy.deepcopy(first.states[11])
    rng = random.Random(6261513)
    query_count = 0
    try:
        for case in range(args.cases):
            state = copy.deepcopy(initial)
            state.update(speed=rng.choice((0.0, 0.02, 0.1, 4.0, -3.0)),
                         yaw=rng.uniform(-3.14, 3.14), airborne=bool(case % 11 == 0),
                         grounded_once=bool(case % 7), _water_depth=rng.choice((-1.0, 0.0, 0.7, 1.1)))
            if case % 9 == 0:
                state['destructible_contact_speed'] = rng.uniform(-4, 4)
            state['_motion_stall_pending'] = {}
            command = dict(throttle=rng.choice((-1.0, 0.0, 0.3, 1.0)),
                turn=rng.choice((-0.8, 0.0, 0.0, 0.4)), movement_intent=bool(case % 4),
                move_position=(state['x'] + 3, state['y'], state['z'] + 10),
                aim_position=(state['x'] + 20, state['y'], state['z'] - 2),
                recovery_mode=rng.choice(('drive', 'avoid', 'blocked', 'reverse_turn', 'pivot_recovery', 'arrived')),
                combat_mode='engage', fire_allowed=True)
            now, dt = 100.0 + case * 0.05, rng.choice((0.01, 0.05, 0.1, 0.2))
            probe_kind = rng.randrange(10)
            receipt_kind = rng.randrange(5)
            resolver_kind = rng.randrange(5)
            turnspeed = rng.choice((0.0, 0.0, 0.2, -0.5))
            probes = [None, False, {}, {'clear': True}, {'clear': False, 'collision': True},
                      {'clear': False, 'water': True}, {'clear': True, 'slope': 0.4},
                      {'clear': True, 'slope': -0.03}, {'clear': True, 'deferred': True},
                      {'clear': True, '_world_receipt_pending': True}]
            receipt = [None, False, 'deferred', {}, dict(origin=module._position(state),
                       yaw=state['yaw'], direction=1, leading=3.5, distance=30)][receipt_kind]
            cached = None
            if case % 3:
                cached = dict(result=copy.deepcopy(probes[(probe_kind + 3) % 10]),
                    position=module._position(state), yaw=state['yaw'], maximum_distance=None,
                    probe_distance=15.0, probe_leading=3.5, deadline=now + (0.1 if case % 2 else -0.1))
                if case % 5 == 0 and isinstance(cached['result'], dict):
                    cached['result']['world_receipt'] = copy.deepcopy(receipt)
            outputs, tapes = [], []
            for runtime, native in ((first, False), (second, True)):
                runtime.states[11] = current = copy.deepcopy(state)
                current_command = copy.deepcopy(command)
                runtime._motion_probe_cache.clear()
                if cached is not None:
                    runtime._motion_probe_cache[11] = copy.deepcopy(cached)
                runtime._hard_contact_grinds = {11: case % 5} if case % 2 else {}
                runtime._turn_speeds[11] = turnspeed
                runtime._decision_cache = {11: {'test': case}}
                if case % 29 == 0:
                    navigator = runtime.navigator
                    navigator.search_max_expansions = 1
                    navigator.begin_frame(0.1)
                    navigator.tick(now)
                    navigator.next_target(11, module._position(current),
                        (current['x'] + 100, current['y'], current['z'] + 100),
                        ('local', 11, 'motion_cancellation', case), now)
                    navigator.end_frame()
                tape = []
                def record(kind, args):
                    tape.append((kind, copy.deepcopy(args), copy.deepcopy(current),
                                 copy.deepcopy(runtime._motion_probe_cache),
                                 copy.deepcopy(runtime._hard_contact_grinds)))
                def direction(*values):
                    record('direction', values[:3] + values[4:])
                    if case % 137 == 1:
                        raise RuntimeError('injected motion query failure')
                    return copy.deepcopy(probes[probe_kind])
                def exact(*values):
                    record('receipt', values[:4] + values[5:])
                    return copy.deepcopy(receipt)
                def resolver(*values, **kwargs):
                    record('resolver', (values[:4] + values[5:], kwargs))
                    # A passive query cannot return the active-crush cap.
                    kind = resolver_kind if len(values) == 7 else (resolver_kind if resolver_kind != 3 else 4)
                    return ('clear', 'crushed', 'soft', 'cap_crushed', 'hard')[kind]
                def report(*values):
                    record('report', values)
                def log_flip(*values):
                    record('flip', values[1:])
                def log_stall(*values):
                    record('stall', values[1:])
                runtime._probe_direction = direction
                runtime._probe_world_receipt = exact
                runtime.motion_resolver = resolver if case % 4 else None
                runtime.motion_report = report
                runtime._log_direction_flip = log_flip
                runtime._log_motion_stall = log_stall
                poses, yaws = {}, {}
                callargs = (current, current_command, {'position': command['aim_position']} if case % 2 else None,
                    runtime._descriptors[11], module._position(current), dt, now, bool(case % 2),
                    bool(case % 3), bool(case % 19 == 0), current['yaw'], poses, yaws, bool(case % 13 == 0), None)
                try:
                    result = motion.step(*callargs) if native else original(runtime, *callargs)
                    error = None
                except (AttributeError, TypeError, RuntimeError) as exc:
                    error = (type(exc).__name__, str(exc))
                    result = None
                outputs.append((error, result, current, current_command, runtime._motion_probe_cache,
                    runtime._hard_contact_grinds, runtime._turn_speeds[11], runtime._decision_cache, poses, yaws))
                tapes.append(tape)
            if outputs[0] != outputs[1] or tapes[0] != tapes[1]:
                for index, (a, b) in enumerate(zip(outputs[0], outputs[1])):
                    if a != b:
                        print('State mismatch', case, index, repr(a), repr(b))
                for index, (a, b) in enumerate(zip(tapes[0], tapes[1])):
                    if a != b:

                        for field, (aa, bb) in enumerate(zip(a, b)):
                            if aa != bb:
                                print('Callback mismatch', case, index, field, repr(aa), repr(bb))
                        break
                print('Callback lengths', len(tapes[0]), len(tapes[1]))
                raise AssertionError('motion case %d' % case)
            if len(motion.objects.get(11, {})) > 3:
                raise AssertionError(('unbounded opaque cache', case, len(motion.objects[11])))
            query_count += len(tapes[0])
        for name, actual in second.navigator.dump_state().items():
            expected = (first.navigator.grid._failed_edges if name == 'grid_failed_edges'
                        else getattr(first.navigator, name))
            if name == 'searches':
                def searches(values):
                    return dict((key, (value.last_frame, value.hull_revision, value.done))
                                for key, value in values.items())
                expected, actual = searches(expected), searches(actual)
            if not equivalent(expected, actual):
                raise AssertionError(('navigation after motion', name, expected, actual))
        if args.cases >= 1000 and 504 not in navigation_events:
            raise AssertionError('motion never exercised pending-search retirement')
        return query_count, len(navigation_events)
    finally:
        motion.close()
        nav.close()
        driver.close()
        backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=2000)
    args = parser.parse_args()
    count, retirements = run(args)
    print('Exact motion flow: %d cases, %d ordered callback/state observations, %d navigation retirements.' %
          (args.cases, count, retirements))


if __name__ == '__main__':
    main()
