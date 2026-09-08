#!/usr/bin/env python
"""Compare native gunner randomness, frozen launches and reliable shot outbox."""
from __future__ import print_function

import argparse
import copy
import hashlib
import random

from check_kernel_state import compare, plain
from kernel_adapter import Kernel, bot_config
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def gunner_cases(backend, rt, runtime, count):
    checks = 0
    bot_id = next(iter(runtime.states))
    state = runtime.states[bot_id]
    base = copy.deepcopy(state)
    for rating in (0.0, .21, .59, .82, 1.0):
        state.clear()
        state.update(copy.deepcopy(base))
        runtime._gunnery_holds.clear()
        runtime.bot_rating = lambda unused: rating
        config = dict(rt.bot_gunnery.rating_parameters(rating))
        config.update(epoch_seconds=rt.bot_gunnery.AIM_BIAS_SECONDS,
                      maximum_offset=rt.bot_gunnery.MAX_AIM_OFFSET_METRES,
                      vertical_share=rt.bot_gunnery.VERTICAL_BIAS_SHARE)
        bot = bot_config(state, runtime._gun_states[bot_id],
                         runtime._ammo_states[bot_id], runtime._burst_states[bot_id])
        bot['config'] = dict(gunnery=config)
        kernel = Kernel(backend, rt.bot_state_codec, [bot], round_id=runtime.round_id)
        rng = random.Random(1513)
        actions, expected = [], []

        def act(action):
            method = action['method']
            target, now = action.get('target'), action.get('now', 0)
            if method == 'set':
                state.update(action['state'])
                result = None
            elif method == 'random':
                generator = random.Random(action['seed'])
                result = [generator.gauss(0, action.get('sigma', 1))
                          if action.get('gauss') else generator.random()
                          for unused in range(action['count'])]
            elif method == 'hash':
                result = int(hashlib.sha1(action['text'].encode('utf-8')).hexdigest()[:8], 16) & 0x7fffffff
            elif method == 'dispersed':
                result = rt._dispersed_barrel_angles(
                    bot_id, runtime.round_id, action['seq'], action['yaw'],
                    action['pitch'], action['dispersion'], action.get('index', 0),
                    action.get('group'), base_direction=action.get('base'))
            else:
                methods = dict(hold=runtime._track_gunnery_hold,
                               error=runtime._gunnery_error,
                               aimed=runtime._aimed_target,
                               ready=lambda s, t, n: runtime._gunner_ready(
                                   s, runtime._gun_states[bot_id], t, n))
                result = methods[method](state, target, now)
            actions.append(action)
            expected.append(plain(dict(result=result,
                                       hold=runtime._gunnery_holds.get(bot_id))))

        for seed in (0, 1, 1513, 0x7fffffff, 0xffffffff):
            for gaussian in (False, True):
                act(dict(method='random', seed=seed, count=1250, gauss=gaussian, sigma=.037))
        for text in ('', 'abc', 'a' * 55, 'b' * 56, 'c' * 64, 'd' * 1000):
            act(dict(method='hash', text=text))
        for frame in range(count):
            target = dict(kind='human' if frame % 19 < 8 else 'bot',
                          network_id=1 + frame // 31,
                          position=(state['x'] + rng.uniform(-500, 500),
                                    rng.uniform(-100, 100), state['z'] + rng.uniform(-500, 500)),
                          yaw=rng.uniform(-3, 3), speed=rng.uniform(-18, 25),
                          alive=True, visible=True)
            if frame % 5 == 0:
                target['velocity'] = (1.7, 3.1, -2.3)
            if frame % 13 == 1:
                target = None
            if frame % 7 == 2:
                act(dict(method='set', state=dict(fire_seq=frame // 7)))
            for method in ('hold', 'error', 'aimed', 'ready'):
                act(dict(method=method, target=target, now=100 + frame * .27))
            shot = dict(method='dispersed', seq=frame + 1, index=frame % 4,
                        group=frame + 1 - frame % 4, yaw=rng.uniform(-3, 3),
                        pitch=rng.uniform(-1, 1), dispersion=rng.uniform(.001, .04))
            if frame % 2:
                shot['base'] = (rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(-2, 2))
            act(shot)
        try:
            actual = kernel.gunnery_actions(bot_id, actions)
            compare(expected, actual, ('gunner', rating))
            checks += len(actions)
        finally:
            kernel.close()
    return checks


def launch_cases(backend, rt, runtime, count):
    checks = 0
    bot_id = next(iter(runtime.states))
    original = copy.deepcopy(runtime.states[bot_id])
    descriptor = runtime._descriptors[bot_id]
    for clip_size, burst_count in ((1, 1), (3, 1), (7, 3)):
        installed = copy.deepcopy(descriptor)
        installed.gun.clip = (clip_size, .19)
        installed.gun.burst = (burst_count, .11 if burst_count > 1 else 0.0)
        state = runtime.states[bot_id]
        state.clear()
        state.update(copy.deepcopy(original))
        gun = rt._BotGunState(installed)
        ammo = rt._BotAmmoState(installed, state['profile'])
        burst = rt.burst_mechanics.BurstClock()
        runtime._gun_states[bot_id] = gun
        runtime._ammo_states[bot_id] = ammo
        runtime._burst_states[bot_id] = burst
        runtime._descriptors[bot_id] = installed
        runtime._pending_launches = []
        runtime._pending_launch_keys = {}
        runtime._pending_launch_by_bot = {}
        kernel = Kernel(backend, rt.bot_state_codec,
                        [bot_config(state, gun, ammo, burst)], round_id=runtime.round_id)
        rng = random.Random(3001513)
        actions, expected = [], []

        def act(action):
            method = action['method']
            factor = action.get('factor', 1)
            if method == 'set':
                state.update(action['state'])
                result = None
            elif method == 'weapon':
                owner, name = action['action']['method'].split('.')
                result = getattr(dict(gun=gun, ammo=ammo, burst=burst)[owner], name)(
                    *action['action'].get('args', ()))
            elif method == 'fire':
                result = runtime._fire(state, gun, factor, installed,
                                       launch_receipt=action.get('receipt'), ammo_state=ammo,
                                       launch_preview=action.get('preview'),
                                       launch_time_us=action['time'])
            elif method == 'cancel':
                result = runtime._cancel_active_burst(state, gun, ammo, burst)
            elif method == 'ack':
                result = runtime.ack_projectile_launch(action.get('id', bot_id), action['seq'])
            elif method == 'advance':
                result = []
                for edge in burst.advance(action['dt']):
                    time = min(action['end'], action['time'] + max(0, int(round(edge['due_offset'] * 1000000))))
                    preview = action.get('preview')
                    if action.get('sequence_preview') and preview is not None:
                        preview = dict(preview, fire_seq=edge['shot_seq'])
                    accepted = runtime._commit_burst_edge(
                        state, gun, factor, installed, edge, ammo,
                        launch_receipt=action.get('receipt'), launch_preview=preview,
                        launch_time_us=time)
                    result.append(accepted)
                    if not accepted:
                        runtime._cancel_active_burst(state, gun, ammo, burst)
                        break
                burst.publish(state)
            record = bot_config(state, gun, ammo, burst)
            record = plain(record)
            for name in ('crew_level', 'loadout'):
                record['gun'].pop(name, None)
            expected.append(plain(dict(result=result, state=record,
                                       launches=runtime._pending_launches)))
            actions.append(action)

        for frame in range(count):
            act(dict(method='set', state=dict(_drowning=frame % 23 == 3,
                                              _overturned=frame % 31 == 4,
                                              pitch=.15 if frame % 29 == 4 else 0,
                                              x=original['x'] + frame * .8)))
            if not burst.active:
                act(dict(method='weapon', action=dict(method='gun.tick', args=[rng.choice((.1, .19, 2.7, 8.1))])))
                kind = 'full' if gun.reload_kind == 'full' else 'intra'
                ready = gun.ready()
                act(dict(method='weapon', action=dict(method='gun.complete_reload', args=[1, ammo.planned_rounds()])))
                act(dict(method='weapon', action=dict(method='ammo.stage', args=[frame % 3, ready, kind == 'full'])))
                preview = dict(fire_seq=state.get('fire_seq', 0) + (2 if frame % 17 == 7 else 1),
                               shot_yaw=.24, shot_pitch=.017, origin=(1.5, 7.3, 2.7))
                act(dict(method='fire', time=frame * 200000,
                         preview=preview if frame % 3 else None))
            else:
                if frame % 5 == 3:
                    act(dict(method='cancel'))
                else:
                    act(dict(method='advance', dt=.23, time=frame * 200000,
                             end=frame * 200000 + 230000,
                             sequence_preview=True,
                             preview=dict(fire_seq=1, shot_yaw=.31, shot_pitch=.028,
                                          origin=(frame * 1.5, 7.3, 2.7))))
            if runtime._pending_launches and frame % 3 == 1:
                oldest = runtime._pending_launches[0]['fire_seq']
                act(dict(method='ack', seq=oldest + 1))
                act(dict(method='ack', seq=oldest))
                act(dict(method='ack', seq=oldest))
        try:
            actual = kernel.launch_actions(bot_id, actions)
            for index, (left, right) in enumerate(zip(expected, actual)):
                compare(left, right, ('launch', clip_size, burst_count, index, actions[index]))
            checks += len(actions)
        finally:
            kernel.close()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=120)
    args = parser.parse_args()
    with redirected():
        fixture = load_fixture(args.fixture)
        runtime, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    backend = Backend(args.module)
    gunners = gunner_cases(backend, rt, runtime, args.cases)
    launches = launch_cases(backend, rt, runtime, args.cases)
    print('Exact native gunner flow: %d actions; atomic launch/outbox: %d transitions.' % (gunners, launches))


if __name__ == '__main__':
    main()
