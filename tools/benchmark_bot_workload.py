#!/usr/bin/env python3
"""Measure the copied Bot loop with 29 real drivers and one shipped navgraph.

Terrain heights come from the graph; native obstacle/visibility/aim queries use
deterministic test seams. This measures Python work, not Windows frame pacing,
native collision cost, or a replay of a captured battle. Compare identical
arguments across checkouts and compare --snapshot files for behavioral parity.
"""

import argparse
import contextlib
import cProfile
import io
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time


def make_runtime(checkout, map_name):
    sys.path.insert(0, str(checkout / 'tests'))
    import test_port_0922_bot_runtime as fixtures
    from effective_params_fixture import bot_default_crew_factors
    module = fixtures._load()
    module.loadout.attribute_factors = bot_default_crew_factors
    graph = json.loads((checkout / 'navgraphs' / (map_name + '.json')).read_text())

    def ground(x, z, hint=0.0):
        ix = int(math.floor((x - graph['origin'][0]) / graph['cell_size'] + 0.5))
        iz = int(math.floor((z - graph['origin'][1]) / graph['cell_size'] + 0.5))
        if not (0 <= ix < graph['width'] and 0 <= iz < graph['height']):
            return None
        height = graph['heights_mm'][iz * graph['width'] + ix]
        return None if height is None else height / 1000.0

    def spawn(team, slot):
        point = graph['spawn_formations'][str(team)][slot]
        return tuple(point[:3]), point[3]

    equipment = fixtures._bot_equipment_contracts(module)
    runtime = module.BotRuntime(
        1, descriptor_resolver=lambda unused: fixtures._combat_descriptor(),
        direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
        visibility_probe=lambda *unused: True,
        firing_lane_probe=lambda *unused: True,
        ground_probe=ground, physics_ground_probe=ground,
        spawn_resolver=spawn, baked_graph=graph,
        direct_launch_origin_probe=lambda state, *unused:
            (state['x'], state['y'] + 1.0, state['z']),
        bot_equipment_resolver=lambda: equipment, control_seconds=0.1)
    roster = [dict(id=11 + index, team=1 if index < 15 else 2,
                   slot=index if index < 15 else index - 15,
                   name='Workload-%d' % index) for index in range(29)]
    runtime.battle_start(dict(round_id=1, map=map_name,
                             bot_authority_id=1, bots=roster))
    runtime.debug_logging = False
    for row in roster:
        goal = graph['spawn_formations'][
            '2' if row['team'] == 1 else '1'][row['slot']]
        runtime._server_orders[row['id']] = dict(
            combat_mode='route', move_position=tuple(goal[:3]),
            face_position=tuple(goal[:3]), aim_position=tuple(goal[:3]),
            fire_allowed=False, shell_index=0, fire_range=500.0)
        runtime._server_order_tokens[row['id']] = 1
    return runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument('--map', default='01_karelia')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--fps', type=float, default=30.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.fps <= 0 or args.repeats <= 0:
        parser.error('seconds, fps and repeats must be positive')
    frames = int(round(args.seconds * args.fps))
    timings = []
    result = None
    profiler = cProfile.Profile() if args.profile else None
    for repeat in range(args.repeats):
        random.seed(17)
        with contextlib.redirect_stdout(io.StringIO()):
            runtime = make_runtime(args.checkout.resolve(), args.map)
            messages = []
            started = time.process_time()
            if profiler is not None:
                profiler.enable()
            for frame in range(frames):
                dt = 1.0 / args.fps
                messages.extend(runtime.update(dt, 100.0 + (frame + 1) * dt))
            if profiler is not None:
                profiler.disable()
            timings.append(time.process_time() - started)
            snapshot = {
                'messages': messages, 'probes': runtime.probe_totals(),
                'decisions': runtime._decision_counts,
            }
            if result is not None and snapshot != result:
                raise RuntimeError('deterministic workload changed across repeats')
            result = snapshot
        print('repeat=%d cpu_seconds=%.6f' % (repeat + 1, timings[-1]), flush=True)
    if args.snapshot:
        args.snapshot.write_text(json.dumps(result, sort_keys=True))
    if profiler is not None:
        profiler.dump_stats(str(args.profile))
    print(json.dumps({
        'map': args.map, 'bots': 29, 'frames': frames,
        'cpu_median_seconds': statistics.median(timings),
        'decisions': sum(runtime._decision_counts.values()),
        'probes': runtime.probe_totals(), 'native_runtime_measured': False,
        'profile_enabled': profiler is not None,
    }, sort_keys=True))


if __name__ == '__main__':
    main()
