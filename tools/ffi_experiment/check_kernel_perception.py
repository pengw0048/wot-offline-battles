#!/usr/bin/env python
"""Compare complete native target ownership, visibility and cached poses."""
from __future__ import print_function

import argparse
import copy
import json
import random

from check_kernel_state import compare, plain
from kernel_adapter import (Kernel, bot_config, critical_config, perception_config,
                            player_config, lane_config, ENGINE_FIELDS)
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def configuration(rt, runtime):
    bots = []
    for bot_id, state in runtime.states.items():
        value = bot_config(state, runtime._gun_states[bot_id],
                           runtime._ammo_states[bot_id],
                           runtime._burst_states[bot_id])
        target = dict(state, kind='bot', network_id=bot_id)
        value.update(critical_config=critical_config(rt, runtime, bot_id),
                     config=dict(perception=dict(
                         profile=runtime._spotting_profile(target),
                         vision=runtime._vision_ranges.get(bot_id))))
        bots.append(value)
    return bots


def snapshot(runtime, team):
    def kind(value):
        return int(value == 'human')
    remembered = sorted([a, kind(b), c, v] for (a, b, c), v in
                        runtime._visible_target_poses.items())
    source = sorted([kind(key[0]), key[1], value] if isinstance(key, tuple)
                    else [0, key, value]
                    for key, value in runtime._source_still.items())
    target = sorted([kind(key[0]), key[1], value]
                    for key, value in runtime._visibility_still.items())
    visible = sorted([a, kind(b), c] for (a, b, c), v in team.items() if v)
    return dict(remembered=remembered, source_still=source,
                target_still=target, team_visible=visible)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--frames', type=int, default=40)
    parser.add_argument('--human', action='store_true')
    parser.add_argument('--lanes', action='store_true')
    args = parser.parse_args()
    with redirected():
        fixture = load_fixture(args.fixture)
        runtime, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    backend = Backend(args.module)
    rng = random.Random(271513)
    original = copy.deepcopy(runtime.states)
    source_queries, native_queries = [], []
    human = None
    if args.human:
        with open(args.fixture) as stream:
            body = json.load(stream)['_effective_params_snapshot']
        scope = {}
        eval(compile(body, 'human_fixture', 'exec'), scope)
        effective = scope['_effective_params_snapshot']()
        effective['crew']['members'][0]['roles'] = ['commander', 'gunner', 'radioman']
        effective['crew']['members'][0]['skills'] = [dict(
            name=name, level=100.0, active=True, enabled=True)
            for name in ('gunner_rancorous', 'radioman_lasteffort')]
        effective['skills']['designated_target'] = True
        effective['skills']['last_effort'] = True
        center_x = sum(s['x'] for s in original.values()) / len(original)
        center_z = sum(s['z'] for s in original.values()) / len(original)
        human = dict(id=1, team=1, vehicle=next(iter(original.values()))['vehicle'],
                     x=center_x, y=1, z=center_z, yaw=0, aim_yaw=0,
                     speed=0, alive=True, health=1000, max_health=1000,
                     fire_seq=0, critical={}, effective_params=effective)

    def visibility(trace, source, target, fired):
        row = (rt._position(source), tuple(target['position']), bool(fired))
        trace.append(row)
        if int(abs(source['x'] + target['z'])) % 13 == 0:
            raise RuntimeError('injected native visibility failure')
        return dict(line_of_sight=int(abs(source['x'] - target['z'])) % 7 != 0,
                    foliage_bonus=.23 if fired else .17)

    runtime.visibility_probe = lambda source, target, fired=False: visibility(
        source_queries, source, target, fired)
    source_lanes, native_lanes = [], []

    def lane(trace, kind, source, target):
        trace.append((kind, source['id'], target['kind'], target['network_id'],
                      rt._position(source), target['position'],
                      source.get('yaw', 0), target.get('yaw', 0),
                      source.get('fire_seq', 0), target.get('fire_seq', 0)))
        value = (int(abs(source['x'] + target['z'])) + kind) % 5
        return None if kind == 752 and value == 0 else value > 1

    runtime.firing_lane_probe = lambda source, target: lane(source_lanes, 751, source, target)
    runtime.incoming_lane_probe = lambda source, target: lane(source_lanes, 752, source, target)
    kernel = Kernel(backend, rt.bot_state_codec, configuration(rt, runtime),
                    perception=perception_config(rt, runtime),
                    engine=dict(fields=ENGINE_FIELDS), lanes=lane_config(rt, runtime))
    actions, expected = [], []
    tick, team, processed, players, aggregate = {}, {}, set(), [], {}
    cached_contacts, probe_targets = {}, {}

    def engine(query):
        def actor(at):
            kind, identity, mask = (int(value) for value in query[at:at + 3])
            raw = next(player for player in players if player['id'] == identity) if kind else original[identity]
            value = dict(raw, kind='human' if kind else 'bot', network_id=identity)
            for index, name in enumerate(ENGINE_FIELDS):
                if mask & (1 << index):
                    value[name] = query[at + 3 + index]
                else:
                    value.pop(name, None)
            at += 3 + len(ENGINE_FIELDS)
            for name in ('position', 'velocity'):
                if query[at]:
                    value[name] = tuple(query[at + 1:at + 4])
                else:
                    value.pop(name, None)
                at += 4
            value.setdefault('position', rt._position(value))
            return value, at
        source, at = actor(1)
        target, at = actor(at)
        kind = int(query[0])
        result = lane(native_lanes, kind, source, target)
        query[0] = bool(result) if kind == 751 else result is not None
        query[1] = bool(result)
        return 0

    def act(action):
        method, now = action['method'], action.get('now', 0.0)
        result = None
        if method == 'begin':
            runtime._begin_visibility_frame()
        elif method == 'finish':
            runtime._finish_visibility_frame()
        elif method == 'prepare':
            runtime._prepare_visibility_frame(players, now, bool(players))
        elif method == 'lifecycle':
            runtime._track_human_observer_lifecycle(players, now, tick)
        elif method == 'humans':
            runtime._append_human_observations(players, now, aggregate, team, tick)
        elif method == 'set':
            runtime.states[action['bot']].update(action['state'])
        elif method == 'still':
            runtime._note_source_stillness(runtime.states[action['bot']], now)
        elif method == 'contacts':
            contacts, targets = runtime._contacts_for(
                runtime.states[action['bot']], players, now, team, tick, processed)
            result = dict(contacts=contacts, targets=targets)
            cached_contacts[action['bot']] = contacts, targets
        elif method == 'collect':
            source = runtime.states[action['bot']]
            contacts, targets = cached_contacts[action['bot']]
            live = runtime._refresh_target_poses(
                targets, live_players=runtime._index_live_players(players),
                probe_targets=probe_targets, processed_bot_ids=processed)
            for cached in contacts:
                target = live.get(cached.get('id'), cached)
                key = (int(source['team']), target['kind'], target['network_id'])
                fresh = cached.get('fresh_visible', False)
                team[key] = bool(fresh or team.get(key, False))
                if fresh:
                    remembered = runtime._target_pose_snapshot(target, tick.setdefault('target_pose_snapshots', {}))
                    runtime._visible_target_poses[key] = remembered
                    target.update(remembered)
                entry = aggregate.setdefault(key, [False, set(), target, set(), set()])
                entry[0] = bool(cached['visible'] or entry[0])
                entry[2] = target
                if cached.get('direct_visible'):
                    entry[4].add(action['bot'])
        elif method == 'service':
            priorities = dict(((row[0], 'human' if row[1] else 'bot', row[2]), row[3])
                              for row in action['selected'])
            budget = [action['budget']]
            runtime._incoming_lane_budget = action['incoming_budget']
            pending = runtime._service_shot_lane_work(
                now, action['cycle'], team, players, tick, processed, priorities, budget)
            result = pending, budget[0], runtime._incoming_lane_budget
        elif method == 'merge':
            result = runtime._merge_observation_shot_lanes(aggregate, team, now)
        elif method == 'pack':
            result = runtime._pack_observations(aggregate, now)
        elif method == 'processed':
            processed.add(action['bot'])
        expected.append(plain(dict(
            result=result, state=snapshot(runtime, team) if action.get('snapshot') else None)))
        actions.append(action)

    try:
        action_count = 0
        for frame in range(args.frames):
            actions, expected = [], []
            cadence = .41 if args.human else .11
            now = 100 + frame * cadence
            tick, team, processed, aggregate = {}, {}, set(), {}
            cached_contacts, probe_targets = {}, {}
            players = []
            if human is not None:
                # Repeated death, revival, absent actor and crew injury edges
                # exercise both the live and Last Effort observer paths.
                human['alive'] = frame % 13 < 6
                human['fire_seq'] = frame // 4
                human['speed'] = 0 if frame % 5 else 7
                human['critical'] = dict(crew_ko=['commander'] if frame % 11 == 2 else [], fire=frame % 6 == 1)
                if frame % 17 != 16:
                    players = [player_config(rt, runtime, copy.deepcopy(human))]
            act(dict(method='begin'))
            # Motion-phase templates must retain the birth-of-slice pose while
            # later observers see already integrated actors in the same slice.
            for bot_id, state in runtime.states.items():
                base = original[bot_id]
                act(dict(method='set', bot=bot_id, state=dict(
                    x=base['x'] + rng.uniform(-70, 70),
                    z=base['z'] + rng.uniform(-70, 70),
                    yaw=rng.uniform(-3, 3), speed=rng.choice((0, 0, 8)),
                    fire_seq=frame // 3, alive=(bot_id % 7 != frame % 7))))
            act(dict(method='slice', players=players))
            act(dict(method='lifecycle', now=now))
            selected = []
            due = []
            for bot_id, state in runtime.states.items():
                target = runtime._selected_visibility_target(state)
                if target:
                    selected.append((bot_id, int(target[0] == 'human'), target[1]))
                if runtime._visibility_decision_due(state, now):
                    due.append(bot_id)
            act(dict(method='prepare', now=now, selected=selected, due=due,
                     include_humans=bool(players)))
            if players:
                act(dict(method='humans', now=now))
            for bot_id, state in runtime.states.items():
                if not state['alive']:
                    continue
                act(dict(method='still', bot=bot_id, now=now))
                act(dict(method='contacts', bot=bot_id, now=now))
                if args.lanes:
                    act(dict(method='collect', bot=bot_id, now=now))
                act(dict(method='set', bot=bot_id, state=dict(
                    x=state['x'] + 4.1, speed=0 if state['speed'] else 8,
                    turret_yaw=rng.uniform(-2, 2))))
                act(dict(method='processed', bot=bot_id))
            if args.lanes:
                selected_lanes = []
                for bot_id, (contacts, targets) in sorted(cached_contacts.items()):
                    for target in contacts[:2]:
                        selected_lanes.append((bot_id, int(target['kind'] == 'human'),
                                               target['network_id'], bot_id % 2))
                act(dict(method='service', now=now,
                         cycle=100 + (frame // 7 + 1) * cadence * 7,
                         selected=selected_lanes, budget=(0, 1, 4)[frame % 3],
                         incoming_budget=int(frame % 2 == 0)))
                act(dict(method='merge', now=now))
                act(dict(method='pack', now=now))
            act(dict(method='finish', snapshot=True))
            actual = kernel.perception_actions(actions, lambda source, target, fired: visibility(
                native_queries, source, target, fired), engine=engine)
            compare(expected, actual, ('perception', frame))
            action_count += len(actions)
        compare(source_queries, native_queries, ('ordered native leaves',))
        compare(source_lanes, native_lanes, ('ordered lane leaves',))
        if args.lanes and args.frames >= 8 and not source_lanes:
            raise AssertionError('lane workload made no native requests')
        print('Exact native target flow: %d actions, %d ordered visibility queries, %d lane queries, %d complete frames.' % (action_count, len(source_queries), len(source_lanes), args.frames))
    finally:
        kernel.close()
        backend.close()


if __name__ == '__main__':
    main()
