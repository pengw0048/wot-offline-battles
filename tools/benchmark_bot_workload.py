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
import types
from unittest import mock


def make_runtime(checkout, map_name, scenario='navigation'):
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

    formations = graph['spawn_formations']
    if scenario == 'combat':
        # Bring the opposing team within spotting range on valid shipped cells.
        # This is a deterministic synthetic engagement, not captured input.
        first = formations['1'][0]
        second = formations['2'][0]
        length = math.hypot(second[0] - first[0], second[2] - first[2])
        offset = ((second[0] - first[0]) * 140.0 / length,
                  (second[2] - first[2]) * 140.0 / length)
        cells = [(graph['origin'][0] + (i % graph['width']) * graph['cell_size'],
                  height / 1000.0,
                  graph['origin'][1] + (i // graph['width']) * graph['cell_size'])
                 for i, height in enumerate(graph['heights_mm'])
                 if height is not None and graph['hazards'][i] == 0]
        enemy = []
        for point in formations['1']:
            wanted = (point[0] + offset[0], point[2] + offset[1])
            nearest = min(cells, key=lambda cell:
                          (cell[0] - wanted[0]) ** 2 +
                          (cell[2] - wanted[1]) ** 2)
            cells.remove(nearest)
            enemy.append(nearest + (math.atan2(-offset[0], -offset[1]),))
        formations = {'1': formations['1'], '2': enemy}

    def spawn(team, slot):
        point = formations[str(team)][slot]
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
        goal = formations[
            '2' if row['team'] == 1 else '1'][row['slot']]
        runtime._server_orders[row['id']] = dict(
            combat_mode='engage' if scenario == 'combat' else 'route',
            move_position=tuple(goal[:3]),
            face_position=tuple(goal[:3]), aim_position=tuple(goal[:3]),
            fire_allowed=scenario == 'combat', shell_index=0, fire_range=500.0)
        if scenario == 'combat':
            enemies = [other for other in roster if other['team'] != row['team']]
            target = enemies[row['slot'] % len(enemies)]
            runtime._server_orders[row['id']].update(
                target_id=target['id'], target_kind='bot')
        runtime._server_order_tokens[row['id']] = 1
    return runtime, ground


@contextlib.contextmanager
def combat_native_queries(runtime, ground):
    """Run production world/candidate/body code against deterministic natives.

    The test scene contains terrain and one finite hard wall. The destructible
    registry exercises streamed empty-cell reuse; positive destruction/recast
    parity is covered by the world-collision and destructible tests.
    """
    import test_port_0922_destructibles as fixtures
    from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
    from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
    fixture = fixtures.DestructiblesCompatibilityTests()
    unused_manager, unused_mapper, area, bigworld, math_module, unused_td = (
        fixture._empty_catalog_scan_fixture())
    queries = []
    center_x = sum(state['x'] for state in runtime.states.values()) / 29.0
    center_z = sum(state['z'] for state in runtime.states.values()) / 29.0
    vector = math_module.Vector3

    def collide(space, start, end, mask, *filters):
        queries.append((space, (start.x, start.y, start.z),
                        (end.x, end.y, end.z), mask, len(filters)))
        if abs(start.x - end.x) < 1e-9 and abs(start.z - end.z) < 1e-9:
            height = ground(start.x, start.z)
            if height is not None and min(start.y, end.y) <= height <= max(start.y, end.y):
                return vector(start.x, height, start.z), vector(0, 1, 0), 0
            return None
        if abs(end.z - start.z) > 1e-9:
            fraction = (center_z - start.z) / (end.z - start.z)
            hit_x = start.x + (end.x - start.x) * fraction
            if 0.0 <= fraction <= 1.0 and abs(hit_x - center_x) <= 18.0:
                return (vector(hit_x, start.y + (end.y - start.y) * fraction,
                               center_z), vector(0, 0, -1), 75)
        return None

    bigworld.wg_collideSegment = collide
    bigworld.wg_getMatInfoNearPoint = lambda *unused: (
        False, vector(), vector(), 0, '', 0, 0)
    bigworld.wg_getChunkDestrFilenames = lambda *unused: ()
    bigworld.wg_getChunkMatrix = lambda *unused: types.SimpleNamespace(
        translation=vector())
    bigworld.time = lambda: runtime._sample_time_us / 1000000.0
    area.chunkIDFromPosition = lambda unused: 22
    authority = types.SimpleNamespace(is_destroyed=lambda *unused: False)
    holder = types.SimpleNamespace(
        _runtime=types.SimpleNamespace(bigworld=bigworld, math=math_module),
        _avatar=types.SimpleNamespace(spaceID=1), _bots=runtime,
        _destructibles=sensor, _bot_motion_kinds={},
        _combat_diagnostics=runtime._combat_diagnostics,
        _vector=lambda point: vector(*point),
        _records={'bot:%d' % bot: {'ready': True, 'engine_id': bot}
                  for bot in runtime.states},
        _server_entity=lambda bot: types.SimpleNamespace(
            typeDescriptor=runtime._descriptors[bot]))
    runtime.motion_resolver = lambda *args, **kwargs: (
        BattleRuntime._resolve_bot_motion(holder, *args, **kwargs))
    runtime.destructible_body_scan = lambda *args: (
        BattleRuntime._scan_bot_destructible_body(holder, *args))
    with mock.patch.dict(sys.modules, {
            'AreaDestructibles': area, 'BigWorld': bigworld, 'Math': math_module}), \
            mock.patch.object(sensor, '_get_destr_authority', return_value=authority):
        yield queries
    sensor.set_catalog(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument('--map', default='01_karelia')
    parser.add_argument('--scenario', choices=('navigation', 'combat'),
                        default='navigation')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--fps', type=float, default=30.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--diagnostics', action='store_true')
    parser.add_argument('--diagnostic-stride', type=int, default=4)
    parser.add_argument('--diagnostic-summary', type=Path)
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
            runtime, ground = make_runtime(
                args.checkout.resolve(), args.map, args.scenario)
            diagnostic = None
            if args.diagnostics:
                from gui.mods.offline_lan_0922.worker_diagnostics import WorkerCombatDiagnostics
                diagnostic = WorkerCombatDiagnostics(
                    time.perf_counter, capture_seconds=args.seconds + 1.0,
                    detail_stride=args.diagnostic_stride)
                runtime._combat_diagnostics = diagnostic
            messages = []
            query_context = (combat_native_queries(runtime, ground)
                             if args.scenario == 'combat' else
                             contextlib.nullcontext([]))
            with query_context as native_queries:
                started = time.process_time()
                if profiler is not None:
                    profiler.enable()
                for frame in range(frames):
                    dt = 1.0 / args.fps
                    if diagnostic is not None:
                        diagnostic.begin_frame(
                            frame, 100.0 + (frame + 1) * dt, 'benchmark')
                    outgoing = runtime.update(dt, 100.0 + (frame + 1) * dt)
                    messages.extend(outgoing)
                    for message in outgoing:
                        for launch in message.get('launches', ()):
                            runtime.ack_projectile_launch(
                                launch['id'], launch['fire_seq'])
                    if diagnostic is not None:
                        if diagnostic.finish_frame() is None:
                            raise RuntimeError('diagnostic capture failed')
                if profiler is not None:
                    profiler.disable()
                timings.append(time.process_time() - started)
            if diagnostic is not None:
                diagnostic.close()
                captures = diagnostic.drain_completed()
                if args.diagnostic_summary:
                    args.diagnostic_summary.write_text(json.dumps(captures))
            snapshot = {
                'messages': messages, 'probes': runtime.probe_totals(),
                'decisions': runtime._decision_counts,
            }
            if args.scenario == 'combat':
                snapshot['native_queries'] = native_queries
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
        'scenario': args.scenario,
        'cpu_median_seconds': statistics.median(timings),
        'decisions': sum(runtime._decision_counts.values()),
        'probes': runtime.probe_totals(), 'native_runtime_measured': False,
        'profile_enabled': profiler is not None,
        'diagnostics_enabled': args.diagnostics,
        'native_query_count': len(native_queries),
        'launches': sum(len(message.get('launches', ())) for message in messages),
        'projectile_terminals_simulated': False,
    }, sort_keys=True))


if __name__ == '__main__':
    main()
