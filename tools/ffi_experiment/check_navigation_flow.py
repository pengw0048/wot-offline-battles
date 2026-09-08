#!/usr/bin/env python
"""Compare complete navigation transitions, queue timing and persistent state."""
from __future__ import print_function

import argparse
import random

from check_parity import nav, random_graph, ground_for
from navigation_adapter import Backend
from navigation_flow_adapter import NativeNavigator


def difference(a, b, path='root'):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return path, 'keys', sorted(repr(key) for key in set(a) - set(b)), sorted(repr(key) for key in set(b) - set(a))
        for key in sorted(a, key=repr):
            found = difference(a[key], b[key], path + '[' + repr(key) + ']')
            if found:
                return found
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return path, 'length', len(a), len(b)
        for i, (first, second) in enumerate(zip(a, b)):
            found = difference(first, second, path + '[%s]' % i)
            if found:
                return found
    elif a != b:
        return path, a, b
    return None


def reference_state(original):
    names = ('paths', 'path_times', 'path_hull_revisions', 'search_times', 'bot_states',
             'bot_failed_edges', 'bot_macro_edges', 'bot_direct_progress',
             'search_frame_time', 'housekeeping_time', 'search_next_key', 'search_credit',
             'search_frame_serial', 'search_processed_frame', 'search_frame_budget',
             'search_frame_open', 'search_auto_time', 'search_max_expansions',
             'search_completed', 'search_failed', 'search_now', 'fallback_totals',
             'fallback_recovered', 'fallback_modes')
    result = dict((name, getattr(original, name)) for name in names)
    result['searches'] = dict((key, dict(last_frame=search.last_frame,
                                        hull_revision=search.hull_revision, done=search.done))
                              for key, search in original.searches.items())
    result['grid_failed_edges'] = original.grid._failed_edges
    return result


def native_state(native):
    result = dict(native.dump_state())
    result['searches'] = dict((key, dict(last_frame=search.last_frame,
                                        hull_revision=search.hull_revision, done=search.done))
                              for key, search in result['searches'].items())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--cases', type=int, default=12)
    parser.add_argument('--frames', type=int, default=80)
    args = parser.parse_args()
    backend = Backend(args.module)
    operations = 0
    try:
        for seed in range(args.cases):
            rng = random.Random(seed + 7513)
            graph = random_graph(rng, 21, 17)
            reference = nav.TerrainNavigator(ground_for(graph), baked_graph=graph)
            shell = nav.TerrainNavigator(ground_for(graph), baked_graph=graph)
            native = NativeNavigator(backend, shell, nav)
            reference.search_max_expansions = native.search_max_expansions = (8, 32, 128, 4096)[seed % 4]
            positions, goals = {}, {}
            for bot in range(1, 9):
                positions[bot] = reference.grid.point_for((rng.randrange(21), rng.randrange(17)), 0)
                goals[bot] = reference.grid.point_for((rng.randrange(21), rng.randrange(17)), 0)
            now = 0.0
            for frame in range(args.frames):
                elapsed = (0.03, 0.1, 0.23, 0.5)[frame % 4]
                now += elapsed
                for owner in (reference, native):
                    owner.begin_frame(elapsed)
                    owner.tick(now)
                if frame in (20, 40, 60):
                    hulls = [(5, -1.0, 5.0, 0.8, 3.5, 1.7)] if frame != 40 else []
                    reference.grid.set_static_hulls(hulls)
                    native.grid.set_static_hulls(hulls)
                for bot in range(1, 9):
                    current, goal = positions[bot], goals[bot]
                    key = ('route', bot % 2 + 1, 'test_lane', frame // 23) if bot < 5 else ('local', bot, 'combat', frame // 29)
                    movement = not (frame % 11 == 0)
                    anchor = current if bot % 3 == 0 else None
                    if frame % 3 == 0:
                        direct = (((current[0]-goal[0])**2 + (current[2]-goal[2])**2)**0.5 <= 15.0 and
                                  not reference.bot_segment_penalized(bot, current, goal, now) and
                                  reference.grid.dry_segment_clear(current, goal, now))
                        if direct:
                            expected = tuple(reference.observe_direct_target(bot, current, goal, key, now, movement) or goal)
                        else:
                            expected = reference.next_target(bot, current, goal, key, now, anchor, None, 20.0, movement)
                        stop = bool(bot % 2)
                        expected_stop = stop and ((direct and expected == tuple(goal)) or
                                                 (not direct and reference.target_is_terminal(bot)))
                        actual, actual_stop = native.motion_target(bot, current, goal, key, now, anchor, 20.0, movement, stop)
                        if expected_stop != actual_stop:
                            raise AssertionError((seed, frame, bot, 'terminal target', expected_stop, actual_stop))
                    else:
                        expected = reference.next_target(bot, current, goal, key, now,
                                                         anchor, None, 20.0, movement)
                        actual = native.next_target(bot, current, goal, key, now,
                                                    anchor, None, 20.0, movement)
                    if expected != actual:
                        raise AssertionError((seed, frame, bot, 'target', expected, actual))
                    operations += 1
                    for name, values in (
                        ('report_blocked_step', (bot, current, actual, now)),
                        ('bot_segment_penalized', (bot, current, goal, now)),
                        ('controlled_shallow_step', (bot, current, frame * 0.17)),
                        ('controlled_shallow_committed', (bot, current, frame * 0.17)),
                        ('target_is_terminal', (bot,)),
                    ):
                        expected_value = getattr(reference, name)(*values)
                        actual_value = getattr(native, name)(*values)
                        if expected_value != actual_value:
                            raise AssertionError((seed, frame, bot, name, expected_value, actual_value))
                        operations += 1
                    # Some hulls remain stationary to exercise stall and
                    # repeated-blocked transitions; others consume the route.
                    if bot % 3 and movement:
                        positions[bot] = tuple(current[i] + (actual[i] - current[i]) * 0.14 for i in range(3))
                for owner in (reference, native):
                    owner.end_frame()
                active_keys = set(native.paths) | set(native.searches)
                if native.search_next_key is not None:
                    active_keys.add(native.search_next_key)
                if set(native._native_keys.values()) != active_keys:
                    raise AssertionError(('retired navigation cache identities', seed, frame, set(native._native_keys.values()) - active_keys))
                found = difference(reference_state(reference), native_state(native))
                if found:
                    raise AssertionError((seed, frame, found))
                expected = reference.fallback_diagnostics(range(1, 9), now)
                actual = native.fallback_diagnostics(range(1, 9), now)
                if expected != actual:
                    raise AssertionError((seed, frame, 'diagnostics', difference(expected, actual)))
            # A direct route has its own progress lease. Keep the request and
            # pose fixed long enough to select and expire a real safe escape.
            for frame in range(100):
                now += 0.5
                current, goal = positions[1], goals[1]
                key = ('local', 91, 'direct_test')
                expected = reference.observe_direct_target(91, current, goal, key, now, frame % 41 != 0)
                actual = native.observe_direct_target(91, current, goal, key, now, frame % 41 != 0)
                if expected != actual:
                    raise AssertionError((seed, frame, 'direct', expected, actual))
                active_keys = set(native.paths) | set(native.searches)
                if native.search_next_key is not None:
                    active_keys.add(native.search_next_key)
                if set(native._native_keys.values()) != active_keys:
                    raise AssertionError(('retired navigation cache identities', seed, frame, set(native._native_keys.values()) - active_keys))
                found = difference(reference_state(reference), native_state(native))
                if found:
                    raise AssertionError((seed, frame, 'direct_state', found))
            native.close()
        print('Exact native navigation: %d operations and %d complete frame-state comparisons.' %
              (operations, args.cases * (args.frames + 100)))
    finally:
        backend.close()


if __name__ == '__main__':
    main()
