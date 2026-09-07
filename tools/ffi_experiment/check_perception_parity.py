#!/usr/bin/env python
"""Differential visibility fairness, fire edges, memory and phase tests."""
from __future__ import print_function
import argparse
import random

from check_core_parity import equal
from navigation_adapter import Backend
from perception_adapter import PerceptionBackend
from portable_workload import load_fixture, Path, ROOT, redirected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    rt = fixture['fixtures']._load()
    backend = Backend(args.module)
    with redirected():
        random.seed(17)
        first, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
        random.seed(17)
        second, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    for runtime in (first, second):
        runtime.states = dict((key, value) for key, value in runtime.states.items()
                              if key in (11, 12, 13, 26, 27, 28))
    # One guaranteed two-pair native budget forces priority changes and debt.
    old_budget = rt.MAX_VISIBILITY_PROBES_PER_FRAME
    rt.MAX_VISIBILITY_PROBES_PER_FRAME = 2
    native = PerceptionBackend(backend, second, rt)
    total = 0
    try:
        for frame in range(180):
            now = 100.0 + frame * 0.13
            logs = [[], []]
            players = [[fixture['wire_player'](
                1, team=1, vehicle='ussr:R11_MS-1', alive=True,
                x=0.0, y=0.0, z=120.0, speed=0.0, fire_seq=frame // 9),
                fixture['wire_player'](
                2, team=2, vehicle='ussr:R11_MS-1', alive=True,
                x=30.0, y=0.0, z=370.0, speed=3.0, fire_seq=frame // 5)]
                for unused in range(2)]
            for side, runtime in enumerate((first, second)):
                def probe(source, target, fired=False, side=side):
                    logs[side].append((source.get('kind', 'bot'), source['id'],
                                       target['kind'], target['network_id'],
                                       tuple(target['position']), fired))
                    if frame % 13 == 0:
                        raise RuntimeError('deterministic unavailable LOS')
                    return dict(line_of_sight=frame < 40 or frame > 140,
                                foliage_bonus=0.1 if frame % 3 else 0.0)
                runtime.visibility_probe = probe
                for index, key in enumerate(sorted(runtime.states)):
                    s = runtime.states[key]
                    s.update(x=float(index % 3) * 30.0, y=float(index),
                             z=100.0 if index < 3 else (140.0 if frame % 9 == 0 else 390.0),
                             speed=0.0 if frame % 4 else 3.0,
                             fire_seq=frame // 7 if frame < 90 else (frame - 90) // 11,
                             alive=not (key == 28 and 60 <= frame < 70))
                    if frame % 5:
                        runtime._server_orders[key]['target_id'] = 26 if key < 26 else 11
                runtime._begin_visibility_frame()
                runtime._prepare_visibility_frame(players[side], now, frame % 2 == 0)
            ticks = [{}, {}]
            teams = [{}, {}]
            if frame % 2 == 0:
                observations = [{}, {}]
                for side, runtime in enumerate((first, second)):
                    runtime._append_human_observations(
                        players[side], now, observations[side], teams[side], ticks[side])
                equal(observations[1], observations[0], 'human priority observations')
            processed = set()
            for key in sorted(first.states):
                results = []
                for side, runtime in enumerate((first, second)):
                    results.append(runtime._contacts_for(runtime.states[key], players[side], now,
                                                         teams[side], ticks[side], processed))
                equal(results[1], results[0], 'contacts frame=%d source=%d' % (frame, key))
                equal(teams[1], teams[0], 'shared team flags')
                # The next observer must see this post-motion pose, including
                # missing optional articulation in a newly remembered sample.
                for runtime in (first, second):
                    runtime.states[key]['x'] += 2.5
                    if frame % 6 == 0:
                        runtime.states[key].pop('velocity', None)
                processed.add(key)
                total += 1
            for runtime in (first, second):
                runtime._finish_visibility_frame()
            equal(logs[1], logs[0], 'visibility query sequence frame %d' % frame)
            equal(second._visible_target_poses, first._visible_target_poses, 'remembered poses')
            equal(second.diagnostic_totals(), first.diagnostic_totals(), 'fair scheduler counters')
            for key in ((1, 'bot', 26), (2, 'bot', 11)):
                equal(second._team_spot_time_left(key, now), first._team_spot_time_left(key, now), 'memory expiry')
            if frame == 95:
                # Same lifecycle identity replacement performed by battle
                # reset; the next native frame must retire all old state.
                for runtime in (first, second):
                    runtime._visibility_cache = {}
                    runtime._visibility_fire = {}
                    runtime._visibility_waiting = []
                    runtime._visibility_frame = None
                    runtime._spot_until = {}
                    runtime._visible_target_poses = {}
                    runtime._reset_visibility_diagnostics()
        # Human observers use the same cache/fair budget via the singleton
        # entry point; test their identity separately without an engine model.
        source = dict(kind='human', id=1, team=1, x=0.0, y=0.0, z=0.0)
        target = dict(kind='bot', id=26, network_id=26, team=2,
                      position=(40.0, 100.0, 0.0), fire_seq=0)
        equal(second._visible(source, target, 200.0), first._visible(source, target, 200.0), 'human proximity')
        equal(second._visible(source, target, 200.1), first._visible(source, target, 200.1), 'human cache')
        for runtime in (first, second):
            runtime._renew_team_spot((1, 'bot', 26), 200.0, 12.0)
        for now in (200.0, 210.0, 211.9, 212.0):
            equal(second._team_spot_time_left((1, 'bot', 26), now),
                  first._team_spot_time_left((1, 'bot', 26), now), 'designated lease')
        print('Exact perception parity: %d Bot contact batches plus 180 human observer slices; fair debt, human/selected/fire priorities, fire/reset edges, motion phases, hidden memory, probe failure and human cache/lease.' % total)
    finally:
        native.close()
        rt.MAX_VISIBILITY_PROBES_PER_FRAME = old_budget
        backend.close()


if __name__ == '__main__':
    main()
