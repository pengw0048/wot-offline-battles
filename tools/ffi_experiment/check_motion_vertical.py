#!/usr/bin/env python
"""Compare complete legacy ground/ballistic updates and native query order."""
from __future__ import print_function
import argparse
import copy
import random
from navigation_adapter import Backend
from driver_adapter import DriverBackend
from navigation_flow_adapter import NavigationFlowBackend
from motion_adapter import MotionFlowBackend
from portable_workload import load_fixture, Path, ROOT


def first_difference(a, b, path=''):
    if type(a) != type(b):
        # Python exposes the same numeric value for int/float spellings.
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b:
            return None
        return (path, a, b)
    if isinstance(a, dict):
        if set(a) != set(b):
            return (path + '.keys', set(a), set(b))
        pairs = ((a[k], b[k], path + '.' + str(k)) for k in sorted(a))
    elif isinstance(a, (tuple, list)):
        if len(a) != len(b):
            return (path + '.length', len(a), len(b))
        pairs = ((x, y, path + '[%d]' % i) for i, (x, y) in enumerate(zip(a, b)))
    else:
        return None if a == b else (path, a, b)
    for x, y, p in pairs:
        diff = first_difference(x, y, p)
        if diff is not None:
            return diff


def straddle_cases(first, second, initial):
    """Cover the chassis-end rise boundary independently of random terrain."""
    samples = (
        (-1.0, .12, .12, None, None),
        (-1.0, .120001, .120001, None, None),
        (-1.0, .2, .2, None, None),
        (-1.0, .6, .6, None, None),
        (-1.0, None, None, .12, .12),
        (-1.0, None, None, .120001, .120001),
        (-1.0, -.8, -.8, None, None),
        (-1.0, -.800001, -.800001, None, None),
        (-1.0, .12, None, .12, None),
    )
    queries = 0
    for heights in samples:
        outputs = []
        for runtime in (first, second):
            state = copy.deepcopy(initial)
            state.update(y=0.0, yaw=.35, speed=0.0, last_drive_pitch=0.0,
                         airborne=False, grounded_once=True, vertical_speed=0.0)
            runtime.states[11] = state
            tape = []
            def probe(x, z, hint):
                tape.append((x, z, hint))
                return heights[len(tape) - 1]
            runtime._physics_ground_probe = probe
            result = runtime._update_vertical_motion(state, 1.0 / 15)
            outputs.append((result, state, tape))
        diff = first_difference(outputs[0], outputs[1])
        if diff:
            raise AssertionError(('straddled support', heights, diff))
        queries += len(outputs[0][2])
    return len(samples), queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=3000)
    args = parser.parse_args()
    fixtures = load_fixture(args.fixture)
    module = fixtures['fixtures']._load()
    from gui.mods.offline_lan_0922.ai import navigation
    first, unused = fixtures['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    second, unused = fixtures['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    backend = Backend(args.module)
    driver = DriverBackend(backend, second, True)
    nav = NavigationFlowBackend(backend, second, navigation)
    motion = MotionFlowBackend(backend, second, module)
    initial = copy.deepcopy(first.states[11])
    rng = random.Random(627001513)
    checks = queries = 0
    try:
        checks, queries = straddle_cases(first, second, initial)
        for case in range(args.cases):
            state = copy.deepcopy(initial)
            state.update(speed=rng.uniform(-9, 20), vertical_speed=rng.uniform(-30, 15),
                         yaw=rng.uniform(-3.14, 3.14), airborne=bool(case % 3),
                         grounded_once=bool(case % 5), last_drive_pitch=rng.uniform(-0.7, 0.7),
                         air_lateral_x=rng.uniform(-3, 3), air_lateral_z=rng.uniform(-3, 3))
            state['_motion_stall_pending'] = {}
            step = rng.choice((0.01, 1.0/30, 0.05, 0.1, 0.2))
            tick = None if case % 7 == 0 else (state['x'] + rng.uniform(-5, 5), state['y'] - rng.uniform(0, 3), state['z'] + rng.uniform(-5, 5))
            support_kind = case % 6
            rise, dx, dz = rng.uniform(-3, 4), rng.uniform(-0.8, 0.8), rng.uniform(-0.8, 0.8)
            outputs = []
            for runtime in (first, second):
                current = copy.deepcopy(state)
                runtime.states[11] = current
                tape = []
                runtime._turn_speeds[11] = 0.2
                runtime._motion_probe_cache = {11: {'test': 1}}
                runtime._decision_cache = {11: {'test': 2}}
                def probe(x, z, hint):
                    tape.append(('ground', (x, z, hint), copy.deepcopy(current)))
                    if support_kind == 0 or support_kind == 1 and x == state['x'] and z == state['z']:
                        return None
                    if support_kind == 2 and len(tape) % 2:
                        return None
                    return state['y'] + rise + (x-state['x']) * dx + (z-state['z']) * dz
                runtime._physics_ground_probe = probe
                result = runtime._update_vertical_motion(current, step, tick, state['yaw'] + 0.2)
                outputs.append((result, current, tape, runtime._turn_speeds[11],
                                runtime._decision_cache, runtime._motion_probe_cache))
            diff = first_difference(outputs[0], outputs[1])
            if diff:
                raise AssertionError(('vertical', case, diff))
            queries += len(outputs[0][2]); checks += 1
            # The complete four-point pose sampler also covers absent edge
            # support, marker reuse, large tilts and prior suspension offset.
            state['airborne'] = False
            state['grounded_once'] = True
            outputs = []
            for runtime in (first, second):
                current = copy.deepcopy(state)
                current['pose_sample'] = (state['x'], state['z'], state['yaw']) if case % 4 == 0 else None
                tape = []
                def probe(x, z, hint):
                    tape.append((x, z, hint))
                    return None if support_kind == 0 else state['y'] + rise + (x-state['x']) * dx + (z-state['z']) * dz
                runtime._physics_ground_probe = probe
                result = runtime._update_slope_pose(current)
                outputs.append((result, current, tape))
            diff = first_difference(outputs[0], outputs[1])
            if diff:
                raise AssertionError(('slope', case, diff))
            queries += len(outputs[0][2]); checks += 1
            # Contact impulses observe the reduced forward speed before the
            # native nudge gate, then publish decay and the admitted endpoint.
            outputs = []
            impulse = dict(delta_velocity=(rng.uniform(-4, 4), rng.uniform(-4, 4)),
                           correction=(rng.uniform(-1, 1), rng.uniform(-1, 1)))
            for runtime in (first, second):
                current = copy.deepcopy(state)
                tape = []
                def clear(*values):
                    tape.append((values, copy.deepcopy(current)))
                    return bool(case % 3)
                runtime._clear = clear
                runtime._apply_tank_contact_response(current, impulse, step, bool(case % 4), bool(case % 5))
                outputs.append((current, tape))
            diff = first_difference(outputs[0], outputs[1])
            if diff:
                raise AssertionError(('contact impulse', case, diff))
            queries += len(outputs[0][1]); checks += 1
            # Check every side/corner of the baked bounds and hazardous cells.
            outputs = []
            current_position = (state['x'] + rng.uniform(-1000, 1000), state['y'], state['z'] + rng.uniform(-1000, 1000))
            for runtime in (first, second):
                current = copy.deepcopy(state)
                current['x'], current['y'], current['z'] = current_position
                runtime._decision_cache = {11: 'command'}
                runtime._motion_probe_cache = {11: 'proof'}
                result = runtime._guard_realised_pose(current, module._position(state), bool(case % 2), state['yaw'])
                outputs.append((result, current, runtime._decision_cache, runtime._motion_probe_cache))
            diff = first_difference(outputs[0], outputs[1])
            if diff:
                raise AssertionError(('final guard', case, diff))
            checks += 1
            params = first._physics_params_for(11)
            for pitch in (-0.7, 0.0, 0.7):
                source = dict(id=11, speed=state['speed'], last_drive_pitch=pitch)
                command = dict(turn=0.5 if case % 2 else 0.0)
                expected = first._cached_traffic_stopping_distance(source, command, params)
                actual = second._cached_traffic_stopping_distance(source, command, params)
                if expected != actual:
                    raise AssertionError(('stopping', case, expected, actual))
                checks += 1
        print('Exact vertical/slope/coast flow: %d checks and %d native-ground query observations.' % (checks, queries))
    finally:
        motion.close(); nav.close(); driver.close(); backend.close()


if __name__ == '__main__':
    main()
