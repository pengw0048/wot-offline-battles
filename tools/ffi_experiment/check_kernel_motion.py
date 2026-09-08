#!/usr/bin/env python
"""Compare owned native motion, receipt fairness and callback-time poses."""
from __future__ import print_function

import argparse
import copy
import math
import random

from check_kernel_state import compare, plain
from check_motion_flow import reference
from kernel_adapter import (Kernel, bot_config, critical_config, motion_config,
                            ENGINE_FIELDS)
from kernel_engine import EngineLeaves
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--frames', type=int, default=120)
    args = parser.parse_args()
    with redirected():
        fixture = load_fixture(args.fixture)
        source, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
        native, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    backend = Backend(args.module)
    selected = list(source.states)[:8]
    original = dict((key, copy.deepcopy(source.states[key])) for key in selected)
    source_tape, native_tape = [], []
    frame_box = [0]
    for runtime, tape in ((source, source_tape), (native, native_tape)):
        runtime.states = copy.deepcopy(original)
        runtime.navigator = None
        runtime.baked_graph = None
        runtime._log_motion_stall = lambda *unused: None
        runtime._log_direction_flip = lambda *unused: None

        def install(runtime, tape):
            def direction(position, yaw, speed, descriptor, maximum=None, width=None):
                tape.append(('direction', position, yaw, speed, maximum, width))
                kind = int(abs(position[0]) + frame_box[0]) % 17
                if kind == 4:
                    raise RuntimeError('injected direction query failure')
                if kind == 5:
                    return {}
                return dict(clear=kind != 3, collision=kind == 3, water=False,
                            slope=.03 if kind == 6 else 0.0, deferred=kind == 7)

            def receipt(position, yaw, speed, descriptor, maximum=None):
                tape.append(('receipt', position, yaw, speed, maximum))
                kind = int(abs(position[0]) + frame_box[0]) % 7
                if kind == 1:
                    return 'deferred'
                if kind == 2:
                    return False
                if kind == 3:
                    return None
                return dict(origin=position, yaw=yaw, direction=-1 if speed < 0 else 1,
                            leading=3.5, distance=30.0)

            def resolver(bot_id, position, yaw, speed, descriptor, dt, now,
                         commit=True, motion_yaw=None):
                state = runtime.states[bot_id]
                tape.append(('resolver', bot_id, position, yaw, speed, dt, now,
                             commit, motion_yaw,
                             tuple(state.get(name) for name in (
                                 'x', 'y', 'z', 'yaw', 'speed', 'movement_dir',
                                 'rotation_dir', 'airborne', 'grounded_once')),
                             runtime._turn_speeds.get(bot_id, 0)))
                kind = (bot_id + frame_box[0]) % 19
                return 'hard' if kind == 1 else 'soft' if kind == 2 else 'clear'

            def report(*values):
                tape.append(('report', values))

            def ground(x, z, hint):
                tape.append(('ground', x, z, hint))
                return None if frame_box[0] % 11 == 8 else 5 + .03 * math.sin(x / 20) + .06 * math.cos(z / 13)

            runtime.direction_probe = direction
            runtime.world_receipt_probe = receipt
            runtime.motion_resolver = resolver
            runtime.motion_report = report
            runtime.ground_probe = ground
            runtime._physics_ground_probe = ground
        install(runtime, tape)
    bots = []
    for bot_id in selected:
        state = source.states[bot_id]
        descriptor = source._descriptors[bot_id]
        value = bot_config(state, source._gun_states[bot_id],
                           source._ammo_states[bot_id], source._burst_states[bot_id])
        value['critical_config'] = critical_config(rt, source, bot_id)
        value['config'] = dict(motion=dict(physics=source._physics_params_for(bot_id),
                                          yaw_limits=rt.ai_driver.gun_yaw_limits(descriptor)))
        bots.append(value)
    kernel = Kernel(backend, rt.bot_state_codec, bots,
                    engine=dict(fields=ENGINE_FIELDS), motion=motion_config(rt, native))
    engine = EngineLeaves(rt, native)
    original_step = reference(rt)
    rng = random.Random(715131)
    count = 0
    try:
        for frame in range(args.frames):
            frame_box[0] = frame
            actions, expected = [], []
            dt, now = (.02, .07, .13)[frame % 3], 100 + frame * .13

            def act(action):
                method = action['method']
                command = action.get('command')
                result = None
                if method == 'begin':
                    source._begin_world_receipt_frame()
                elif method == 'finish':
                    source._finish_world_receipt_frame()
                else:
                    bot_id = action['id']
                    state = source.states[bot_id]
                    if method == 'set':
                        state.update(action['state'])
                        if 'turn_speed' in action:
                            source._turn_speeds[bot_id] = action['turn_speed']
                    elif method == 'step':
                        command = copy.deepcopy(command)
                        original_step(source, state, command, action.get('target'),
                                      source._descriptors[bot_id], rt._position(state),
                                      action['dt'], action['now'], action['decision_due'],
                                      action['refresh'], action['siege_lock'],
                                      action['siege_yaw'], {}, {}, False, None)
                    elif method == 'vertical':
                        result = source._update_vertical_motion(state, action['dt'],
                                                               action['tick'], action['attempted'])
                    elif method == 'slope':
                        result = source._update_slope_pose(state, action.get('allow_ungrounded', False))
                    elif method == 'landing':
                        result = source._apply_bot_landing_impact(state, action['speed'])
                expected.append(plain(dict(result=result, states=source.states,
                    turns=dict((key, source._turn_speeds.get(key, 0)) for key in selected),
                    command=command, waiting=source._world_receipt_waiting)))
                actions.append(action)

            act(dict(method='begin'))
            tick_poses = {}
            for index, bot_id in enumerate(selected):
                state = source.states[bot_id]
                if frame % 13 == 0:
                    act(dict(method='set', id=bot_id, state=dict(
                        x=original[bot_id]['x'] + rng.uniform(-20, 20),
                        y=7 + index * .15, z=original[bot_id]['z'],
                        yaw=rng.uniform(-3, 3), speed=(-1 if index % 3 == 1 else 1) * 4,
                        health=1000, max_health=1000, alive=True,
                        grounded_once=True, airborne=False,
                        pitch=0, roll=0, terrain_pitch=0),
                        turn_speed=.2 if index % 3 == 0 else 0))
                command = dict(throttle=(-.7, 1., 0., .3)[(index + frame // 5) % 4],
                    turn=0 if index % 3 else .4, movement_intent=True,
                    move_position=(state['x'] + 10, state['y'], state['z'] + 15),
                    aim_position=(state['x'] + 30, state['y'], state['z'] - 7),
                    recovery_mode='reverse_turn' if index % 4 == 0 else 'drive',
                    combat_mode='engage', fire_allowed=True)
                tick_poses[bot_id] = rt._position(state)
                act(dict(method='step', id=bot_id, command=command,
                         target=dict(position=command['aim_position']) if index % 2 else None,
                         dt=dt, now=now, decision_due=frame % 4 == 0,
                         refresh=frame % 3 != 2, siege_lock=frame % 23 == 21,
                         siege_yaw=state['yaw']))
            for bot_id in selected:
                act(dict(method='vertical', id=bot_id, dt=dt, tick=tick_poses[bot_id],
                         attempted=source.states[bot_id]['yaw']))
                if frame % 4 == 0:
                    act(dict(method='slope', id=bot_id, tier=source._detail_tier(source.states[bot_id])))
            act(dict(method='finish'))
            actual = kernel.motion_actions(actions, engine)
            for index, (left, right) in enumerate(zip(expected, actual)):
                compare(left, right, ('motion', frame, index, actions[index]))
            compare(plain(source_tape), plain(native_tape), ('ordered engine leaves', frame))
            count += len(actions)
        print('Exact owned native motion: %d transitions, %d ordered engine queries, %d frames.' % (count, len(source_tape), args.frames))
    finally:
        kernel.close()


if __name__ == '__main__':
    main()
