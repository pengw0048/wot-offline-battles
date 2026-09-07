#!/usr/bin/env python
"""Differential A* checks, runnable unchanged on CPython 2.7 and 3.

Checks exact completed paths, paid-step completion timing, and live penalty
expiry across synthetic disconnected/risk-weighted graphs and shipped maps.
No approximation tolerance or changed expansion budget is accepted.
"""
from __future__ import print_function
import argparse
import json
import math
import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT = os.path.join(ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods', 'offline_lan_0922')
for name, path in [('gui', CLIENT), ('gui.mods', CLIENT),
                   ('gui.mods.offline_lan_0922', CLIENT),
                   ('gui.mods.offline_lan_0922.ai', os.path.join(CLIENT, 'ai'))]:
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
from gui.mods.offline_lan_0922.ai import navigation as nav
from navigation_adapter import Backend


def ground_for(graph):
    def ground(x, z, unused_hint=0.0):
        ix = int(math.floor((x - graph['origin'][0]) / graph['cell_size'] + 0.5))
        iz = int(math.floor((z - graph['origin'][1]) / graph['cell_size'] + 0.5))
        if 0 <= ix < graph['width'] and 0 <= iz < graph['height']:
            height = graph['heights_mm'][iz * graph['width'] + ix]
            return None if height is None else float(height) / 1000.0
        return None
    return ground


def random_graph(rng, width=13, height=11):
    heights = [None if rng.random() < 0.18 else rng.randrange(-1000, 1001)
               for unused in range(width * height)]
    return dict(format=nav.BAKED_FORMAT_NAME, version=nav.BAKED_FORMAT_VERSION,
                width=width, height=height, origin=[-17.0, -23.0], cell_size=4.0,
                heights_mm=heights, links=[rng.randrange(256) for unused in heights],
                hazards=[rng.choice((0, 0, 0, 1, 2, 4, 4)) for unused in heights],
                bake={'max_grade': 0.3})


def check(backend, graph, seed, maximum=1600):
    rng = random.Random(seed)
    ground = ground_for(graph)
    grids = [nav.TerrainGrid(ground, baked_graph=graph) for unused in range(2)]
    points = []
    for unused in range(2):
        x = rng.randrange(-2, graph['width'] + 2)
        z = rng.randrange(-2, graph['height'] + 2)
        points.append(grids[0].point_for((x, z), 0.0))
    avoid = [grids[0].point_for((rng.randrange(graph['width']), rng.randrange(graph['height'])), 0)
             for unused in range(seed % 4)]
    local = [{}, {}]
    hard = [set(), set()]
    for grid in grids:
        grid._failed_edges[((0, 0), (1, 0))] = (2.0 if seed % 2 else 10.0, 240.0)
        grid._static_hull_edges[((1, 1), (2, 1))] = 240.0
    params = (points[0], points[1], avoid, maximum, 5.0, seed % 2 == 0)
    reference = backend.original_begin(grids[0], *(params + (local[0], hard[0])))
    candidate = grids[1].begin_plan(*(params + (local[1], hard[1])))
    step_count = 0
    while not reference.done:
        budget = (1, 3, 11, 2)[step_count % 4]
        if step_count == 2:
            for index in (0, 1):
                local[index][((2, 2), (3, 2))] = 120.0
                hard[index].add(((3, 3), (4, 3)))
                grids[index]._static_hull_edges[((4, 4), (5, 4))] = 480.0
        reference.step(budget)
        candidate.step(budget)
        if reference.done != candidate.done:
            raise AssertionError('completion frame differs seed=%s step=%s' % (seed, step_count))
        if grids[0]._failed_edges != grids[1]._failed_edges:
            raise AssertionError('failure expiry differs seed=%s step=%s' % (seed, step_count))
        if reference.result != candidate.result:
            raise AssertionError('path differs seed=%s step=%s\n%r\n%r' %
                                 (seed, step_count, reference.result, candidate.result))
        step_count += 1
        if step_count > 200000:
            raise AssertionError('search did not terminate')
    return step_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--random-cases', type=int, default=200)
    args = parser.parse_args()
    original_begin = nav.TerrainGrid.__dict__['begin_plan']
    original_advance = nav.TerrainNavigator.__dict__['_advance_searches']
    backend = Backend(args.module).install(nav)
    cases = steps = 0
    try:
        for seed in range(args.random_cases):
            graph = random_graph(random.Random(seed))
            maximum = (0, 1, 2, 15, 30, 100, 1600)[seed % 7]
            steps += check(backend, graph, seed, maximum)
            cases += 1
        for map_name in ('01_karelia', '05_prohorovka', '59_asia_great_wall'):
            with open(os.path.join(ROOT, 'navgraphs', map_name + '.json')) as stream:
                graph = json.load(stream)
            for seed in range(12):
                steps += check(backend, graph, seed + 500, (30, 1600, 2500)[seed % 3])
                cases += 1
        # Cancel after allocation and repeat cleanup.
        grid = nav.TerrainGrid(lambda *unused: 0.0, baked_graph=random_graph(random.Random(3)))
        cancelled = grid.begin_plan((0, 0, 0), (4, 0, 4))
        cancelled.ensure_started()
        cancelled.close()
        cancelled.close()
        for invalid in ([99], [3, 999999, 0, 0, 1, 0, 0], [5, 1, 1, 0, 0, 0, 0, 0, 999999]):
            try:
                backend.call(invalid)
            except RuntimeError:
                pass
            else:
                raise AssertionError('invalid command was accepted')
    finally:
        backend.close()
        backend.close()
    assert nav.TerrainGrid.__dict__['begin_plan'] is original_begin
    assert nav.TerrainNavigator.__dict__['_advance_searches'] is original_advance
    print(json.dumps({'cases': cases, 'paid_batches': steps,
                      'exact_path_and_completion_parity': True,
                      'python': sys.version}, sort_keys=True))


if __name__ == '__main__':
    main()
