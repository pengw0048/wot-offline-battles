#!/usr/bin/env python
"""Differential native aim, muzzle, intent and exact launch receipt lifecycles."""
from __future__ import print_function

import argparse
import copy
import math
import random

from check_kernel_state import compare, plain
from combat_adapter import CombatBackend
from kernel_adapter import (Kernel, ENGINE_FIELDS, bot_config, critical_config,
                            gunner_config, aim_config, aim_profile)
from kernel_engine import EngineLeaves
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def run(backend, fixture, variant, frames):
    with redirected():
        runtime, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    identity = next(iter(runtime.states))
    state = runtime.states[identity]
    descriptor = copy.deepcopy(runtime._descriptors[identity])
    if variant == 1:
        descriptor.gun.pitchLimits = dict(
            minPitch=[(0, -.18), (1.8, -.10), (3.8, -.12), (2 * math.pi, -.18)],
            maxPitch=[(0, .30), (2.5, .20), (2 * math.pi, .30)])
    elif variant == 2:
        descriptor.gun.staticPitch = -.02
        descriptor.gun.staticTurretYaw = .01
        descriptor.gun.turretYawLimits = (-.15, .15)
        descriptor.hullAimingParams = dict(pitch=dict(
            isAvailable=True, isEnabled=True,
            wheelsCorrectionAngles=dict(pitchMin=-.25, pitchMax=.20),
            wheelsCorrectionSpeed=.05))
    elif variant == 3:
        state['profile']['class_tag'] = 'SPG'
    runtime._descriptors[identity] = descriptor
    runtime._gun_yaw_limits.pop(identity, None)
    gun = runtime._gun_states[identity]
    tape, mode = [], [0]

    def note(kind, source, target, args):
        pose = tuple(source.get(name, 0) for name in (
            'id', 'x', 'y', 'z', 'yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch'))
        target_pose = None if target is None else (
            target.get('kind'), target.get('network_id'),
            tuple(target['position']), tuple(runtime._target_velocity(target)))
        tape.append((kind, pose, target_pose, args))

    def origin(source, desc, shell, seq, yaw, pitch, time):
        note('origin', source, None, (shell, seq, yaw, pitch, time))
        if mode[0] == 1:
            raise RuntimeError('missing muzzle')
        if mode[0] == 2:
            return (0, float('nan'), 0)
        return (source['x'] + .3, source['y'] + 2.1, source['z'] + 1.7)

    def part(source, target):
        note('part', source, target, ())
        if mode[0] == 3:
            return None
        point = target['position']
        return dict(aim_position=(point[0], point[1] + 1, point[2]),
                    aim_token=('hull', target['network_id'], mode[0] == 4))

    def ballistic(source, target, desc, shell, now):
        note('ballistic', source, target, (shell, now))
        if mode[0] == 5:
            return None
        position = target['position']
        start = (source['x'] + .3, source['y'] + 2.1, source['z'] + 1.7)
        aim = (position[0], position[1] + 1, position[2])
        speed, gravity, maximum = rt._shot_ballistics(desc, shell)
        answer = rt.ballistics.ballistic_intercept(
            start, aim, runtime._target_velocity(target), speed, gravity,
            -math.pi * .5, math.pi * .5)
        if answer is None:
            return None
        aim, pitch, time = answer
        return dict(aim_position=aim, pitch=pitch, flight_time=time,
                    yaw=math.atan2(aim[0] - start[0], aim[2] - start[2]),
                    arc='low', proof_metadata=('native', (17, 9)))

    def receipt(source, target, desc, shell, seq, yaw, pitch, time, now):
        note('receipt', source, target, (shell, seq, yaw, pitch, time, now))
        if mode[0] == 6:
            return None
        speed, gravity, maximum = rt._shot_ballistics(desc, shell)
        velocity = (math.sin(yaw) * math.cos(pitch) * speed,
                    math.sin(pitch) * speed, math.cos(yaw) * math.cos(pitch) * speed)
        result = dict(origin=(source['x'] + .3, source['y'] + 2.1, source['z'] + 1.7),
                      velocity=velocity, shot_yaw=yaw, shot_pitch=pitch,
                      gravity=gravity, max_distance=maximum, max_time_ms=20000,
                      fire_seq=seq, shell_index=shell, flight_time=time,
                      proof_key=('exact', seq, (yaw, pitch, time)))
        if mode[0] == 7:
            result['fire_seq'] += 1
        elif mode[0] == 8:
            result['velocity'] = (velocity[0] + .01, velocity[1], velocity[2])
        return result

    def cancel(source):
        tape.append(('cancel', dict(source)))

    runtime.direct_launch_origin_probe = origin
    runtime.direct_aim_point_probe = part
    runtime.ballistic_solution_probe = ballistic
    runtime.artillery_launch_probe = receipt
    runtime.artillery_launch_cancel = cancel
    combat = CombatBackend(backend, rt)
    bot = bot_config(state, gun, runtime._ammo_states[identity], runtime._burst_states[identity])
    bot['critical_config'] = critical_config(rt, runtime, identity)
    bot['config'] = dict(gunnery=gunner_config(rt, runtime, identity),
                         aim=aim_profile(rt, runtime, identity, combat))
    kernel = Kernel(backend, rt.bot_state_codec, [bot], round_id=runtime.round_id,
                    aim=aim_config(rt, runtime), engine=dict(fields=ENGINE_FIELDS))
    leaves = EngineLeaves(rt, runtime)
    checks, queries = [0], [0]

    def act(method, **kwargs):
        action = dict(kwargs, id=identity, method=method)
        target, now, shell = kwargs.get('target'), kwargs.get('now', 0), kwargs.get('shell', state.get('shell_index', 0))
        del tape[:]
        result = None
        if method == 'set':
            state.update(kwargs['state'])
        elif method == 'cadenced':
            runtime._refresh_control_this_step = kwargs.get('refresh', False)
            result = runtime._cadenced_ballistic_solution(state, target, descriptor, shell, now, kwargs.get('force', False))
        elif method == 'local':
            result = runtime._local_ballistic_solution(state, target, descriptor, shell)
        elif method == 'slew':
            result = runtime._update_gun_aim(state, kwargs['command'], target, kwargs['dt'])
        elif method == 'matches':
            result = runtime._direct_aim_matches(state, target, kwargs['solution'])
        elif method == 'preview':
            result = runtime._direct_launch_preview(state, descriptor, shell, gun, kwargs['solution'])
        elif method == 'intent':
            result = runtime._create_artillery_intent(state, target, descriptor, shell, gun, kwargs['solution'], now)
        elif method == 'active':
            result = runtime._active_artillery_intent(state, target, descriptor, shell, now)
        elif method == 'proof':
            result = runtime._active_artillery_reproof(state, target, descriptor, shell, now)
        elif method == 'receipt':
            result = runtime._artillery_launch_receipt(state, target, descriptor, shell, gun, kwargs['solution'], now)
        elif method == 'cancel':
            result = runtime._cancel_artillery_intent(identity, kwargs.get('preserve', False))
        else:
            raise AssertionError(method)
        observed_state = dict((key, value) for key, value in state.items()
                              if key != rt._GUN_PITCH_LIMIT_CACHE)
        expected = plain(dict(result=result, state=observed_state,
                              hold=runtime._gunnery_holds.get(identity),
                              intent=runtime._artillery_intents.get(identity),
                              proof=runtime._artillery_reproofs.get(identity)))
        expected_tape = plain(tape)
        del tape[:]
        actual = kernel.aim_actions([action], leaves)[0]
        compare(expected_tape, plain(tape), (variant, checks[0], method, 'engine'))
        compare(expected, actual, (variant, checks[0], method))
        checks[0] += 1
        queries[0] += len(tape)
        return copy.deepcopy(result)

    rng = random.Random(9181513)
    try:
        for frame in range(frames):
            now = 100 + frame * .07
            target = dict(kind='human' if frame % 13 < 3 else 'bot', network_id=51,
                          position=(state['x'] + 30 + frame * .2, state['y'], state['z'] + 90),
                          velocity=(1.1, 0, -2.3), alive=True, health=100)
            if variant != 3:
                mode[0] = frame % 16
                act('set', state=dict(yaw=rng.uniform(-.3, .3), pitch=rng.uniform(-.2, .2),
                                      roll=rng.uniform(-.2, .2), fire_seq=frame // 20,
                                      movement_dir=frame % 3 - 1, siege_state=frame % 4))
                solution, fresh = act('cadenced', target=target, now=now, refresh=frame % 3 == 0)
                act('slew', target=target, command=dict(_ballistic_solution=solution), dt=.07)
                act('matches', target=target, solution=solution)
                act('preview', solution=solution)
                act('slew', target=None, command={}, dt=.07)
            else:
                mode[0] = 0
                act('cancel')
                act('set', state=dict(yaw=0, pitch=0, roll=0, terrain_pitch=0, suspension_pitch=0,
                                      turret_yaw=0, gun_pitch=0, speed=0, fire_seq=frame))
                # Use an ordinary unbiassed solve to isolate proof lifetime from gunner error.
                solution = act('local', target=target)
                assert solution is not None
                act('set', state=dict(turret_yaw=solution['yaw'], aim_yaw=solution['yaw'],
                                      gun_pitch=solution['pitch'], gun_aligned=True))
                intent = act('intent', target=target, solution=solution, now=now)
                assert intent is not None
                act('active', target=target, now=now + .01)
                mode[0] = 6 + frame % 4
                act('receipt', target=target, solution=solution, now=now + .02)
                mode[0] = 0
                moved = copy.deepcopy(target)
                moved['position'] = (target['position'][0] + 8, target['position'][1], target['position'][2])
                act('receipt', target=moved, solution=solution, now=now + .3)
                act('cadenced', target=moved, now=now + .31, force=True)
                act('proof', target=target, now=now + 20)
                act('cancel')
    finally:
        kernel.close()
        combat.close()
    return checks[0], queries[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--frames', type=int, default=80)
    args = parser.parse_args()
    fixture, backend = load_fixture(args.fixture), Backend(args.module)
    results = [run(backend, fixture, variant, args.frames) for variant in range(4)]
    print('Native aim parity: %d transitions and %d ordered engine queries.' % (
        sum(row[0] for row in results), sum(row[1] for row in results)))


if __name__ == '__main__':
    main()
