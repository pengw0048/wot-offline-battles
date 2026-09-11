#!/usr/bin/env python
"""Compare route cohorts, whole driver orders and crossing lease outcomes."""
from __future__ import print_function

import argparse
import copy
import math
import random

from check_kernel_state import compare, plain
from driver_adapter import DriverBackend
from kernel_adapter import (Kernel, ENGINE_FIELDS, bot_config, critical_config,
                            motion_config, driver_config)
from kernel_engine import EngineLeaves
from navigation_adapter import Backend
from navigation_flow_adapter import NavigationFlowBackend
from portable_workload import load_fixture, Path, ROOT, redirected


def run(backend, fixture, map_name, frames):
    with redirected():
        source, unused = fixture['make_runtime'](Path(ROOT), map_name, 'navigation')
        native, unused = fixture['make_runtime'](Path(ROOT), map_name, 'navigation')
    rt = fixture['fixtures']._load()
    from gui.mods.offline_lan_0922.ai import navigation
    components = [NavigationFlowBackend(backend, native, navigation),
                  DriverBackend(backend, native, True)]
    bots = []
    ids = list(source.states)
    for identity in ids:
        state = native.states[identity]
        bot = bot_config(state, native._gun_states[identity],
                         native._ammo_states[identity], native._burst_states[identity])
        bot['critical_config'] = critical_config(rt, native, identity)
        bot['config'] = {}
        bots.append(bot)
    kernel = Kernel(backend, rt.bot_state_codec, bots,
                    engine=dict(fields=ENGINE_FIELDS), motion=motion_config(rt, native),
                    driver=driver_config(rt, native), orders=list(native._server_orders.items()))
    leaves = EngineLeaves(rt, native)
    checks = [0]
    tape, frame_box = [], [0]
    source_queries, native_queries = [], []

    def install(runtime, tape):
        def probe(position, yaw, speed, descriptor, maximum=None, width=None):
            tape.append((position, yaw, speed, maximum, width))
            if frame_box[0] % 19 == 7:
                return None
            return dict(clear=frame_box[0] % 17 != 9, collision=frame_box[0] % 17 == 9,
                        deferred=frame_box[0] % 11 == 4,
                        slope=.63 if frame_box[0] % 13 == 10 else 0, water=False)
        runtime.direction_probe = probe
    install(source, source_queries)
    install(native, native_queries)

    def act(method, identity=ids[0], **kwargs):
        action = dict(kwargs, id=identity, method=method)
        result = None
        state = source.states[identity]
        decision = copy.deepcopy(kwargs.get('decision'))
        if method == 'begin':
            source.navigator.begin_frame(kwargs['dt'])
        elif method == 'end':
            source.navigator.end_frame()
        elif method == 'set':
            state.update(kwargs['state'])
        elif method == 'orders':
            source._server_orders = dict(kwargs['orders'])
        elif method == 'binding':
            result = source._route_lane_binding(identity, tuple(kwargs['group']),
                                                 kwargs['goal'], kwargs['order'])
        elif method == 'lane_goal':
            result = source._route_lane_goal(identity, kwargs['position'], kwargs['goal'],
                                             kwargs['order'], kwargs['now'], kwargs['joining'])
        elif method == 'route':
            result = source._navigation_target(identity, kwargs['position'], kwargs['goal'], kwargs['order'], decision)
        elif method in ('drive', 'traffic'):
            position = rt._position(state)
            samples = {}

            def clear(yaw, maximum=None):
                grid = source.navigator.grid
                shallow = grid.point_has_baked_hazard(position, rt.BAKED_SHALLOW_WATER)
                advisory = source._planner_corridor_clear(
                    position, yaw, state['speed'], maximum_distance=maximum,
                    wet_escape=shallow or state.get('_water_depth', -1) > rt.BOT_WATER_AVOID_DEPTH,
                    allow_shallow=source.navigator.controlled_shallow_step(identity, position, yaw))
                if advisory is not None:
                    return bool(advisory)
                key = (round((yaw + math.pi) % (2 * math.pi) - math.pi, 4),
                       None if maximum is None else round(maximum, 2))
                if key not in samples:
                    samples[key] = source._probe_direction(position, yaw, state['speed'], source._descriptors[identity], maximum)
                value = samples[key]
                return True if value is None or isinstance(value, dict) and value.get('deferred') else source._probe_is_clear(value)

            if method == 'drive':
                decision['pose_clear'] = lambda yaw: source.navigator.grid.hull_pose_clear(
                    position, yaw, state.get('half_length', 3.5), state.get('half_width', 1.7))
                result = source.adapter.decide_with_order(decision, kwargs['order'], clear)
                decision.pop('pose_clear', None)
            else:
                result = source._traffic_coordinator.adjust(identity, kwargs['body'], kwargs['command'], kwargs['neighbours'], kwargs['now'], clear)
        expected = plain(dict(result=result, states=source.states, decision=decision))
        actual = kernel.driver_actions([action], leaves)[0]
        compare(expected, actual, (map_name, checks[0], method, identity))
        compare(plain(source_queries), plain(native_queries), (map_name, checks[0], method, 'engine'))
        del source_queries[:]
        del native_queries[:]
        checks[0] += 1
        return result

    try:
        rng = random.Random(7221513)
        for frame in range(frames):
            frame_box[0] = frame
            now = 100 + frame * .13
            act('begin', dt=.13)
            if frame % 7 == 0:
                orders = {}
                for identity in ids:
                    state = source.states[identity]
                    order = copy.deepcopy(source._server_orders[identity])
                    order.update(route_id='group-%d' % (state['slot'] % 2), route_index=frame // 7,
                                 route_join=frame % 14 == 0,
                                 route_anchor=rt._position(source.states[ids[0]]),
                                 combat_mode='route')
                    orders[identity] = order
                act('orders', orders=list(orders.items()))
            for index, identity in enumerate(ids[:8]):
                state = source.states[identity]
                order = source._server_orders[identity]
                position = rt._position(state)
                if frame % 9 == 3:
                    act('set', identity, state=dict(alive=index % 3 != 1))
                if frame % 9 == 4:
                    act('set', identity, state=dict(alive=True))
                if frame % 6 == 1:
                    act('binding', identity, group=(state['team'], order['route_id']),
                        goal=order['move_position'], order=order)
                decision = dict(id=identity, slot=state['slot'], position=position,
                                yaw=state['yaw'], speed=state['speed'], dt=.13, now=now,
                                half_length=state['half_length'], half_width=state['half_width'],
                                neighbours=[], decision_horizon=.1, stopping_distance=None)
                if frame % 8 == 5:
                    order = dict(order, move_area_bounds=(position[0] - 20, position[2] - 20,
                                                         position[0] + 20, position[2] + 20),
                                 team_command_id=frame)
                command = act('drive', identity, decision=decision, order=order)
                if frame % 4 == 2:
                    next_pose = command['move_position']
                    act('set', identity, state=dict(x=next_pose[0], y=next_pose[1], z=next_pose[2],
                                                    speed=rng.uniform(0, 7), yaw=command['target_yaw']))
                # Crossings and head-on pairs retain the actual body geometry.
                yaw = 0.0
                body = dict(id=identity, team=1, alive=True, position=(0, 0, 0), yaw=yaw,
                            velocity=(0, 0, 4), half_width=1.7, half_length=3.5,
                            shape=(1.7, 3.5, 0, 2))
                peer_yaw = math.pi if frame % 2 else -math.pi * .5
                peer = dict(body, id=100 + index, position=(0, 0, 9) if frame % 2 else (6, 0, 5),
                            yaw=peer_yaw, velocity=(math.sin(peer_yaw) * 4, 0, math.cos(peer_yaw) * 4))
                act('traffic', identity, body=body, neighbours=[peer], now=now,
                    command=dict(throttle=1, turn=0, target_yaw=0, combat_mode='route', recovery_mode='drive'))
            act('end')
    finally:
        kernel.close()
        for component in reversed(components):
            component.close()
    return checks[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--frames', type=int, default=28)
    args = parser.parse_args()
    fixture, backend = load_fixture(args.fixture), Backend(args.module)
    total = sum(run(backend, fixture, name, args.frames) for name in (
        '59_asia_great_wall', '04_himmelsdorf', '10_hills'))
    print('Native route, driver and traffic parity: %d transitions.' % total)


if __name__ == '__main__':
    main()
