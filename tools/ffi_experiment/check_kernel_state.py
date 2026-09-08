#!/usr/bin/env python
"""Compare owned native weapon clocks and wire rows with the source runtime."""
from __future__ import print_function

from array import array
import argparse
import copy
import json
import random

from kernel_adapter import Kernel, bot_config, packet, critical_config, health_config
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def plain(value):
    return json.loads(json.dumps(value, allow_nan=False))


def compare(expected, actual, path=()):
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            raise AssertionError((path, set(expected), set(actual)))
        for name in expected:
            compare(expected[name], actual[name], path + (name,))
    elif isinstance(expected, (list, tuple)):
        if len(expected) != len(actual):
            raise AssertionError((path, len(expected), len(actual)))
        for index, (left, right) in enumerate(zip(expected, actual)):
            compare(left, right, path + (index,))
    elif expected != actual:
        raise AssertionError((path, expected, actual))


def weapon_cases(backend, rt, runtime, rng, count):
    checks = 0
    source_id = next(iter(runtime.states))
    base = runtime._descriptors[source_id]
    for clip_size in (1, 3, 7):
        descriptor = copy.deepcopy(base)
        descriptor.gun.clip = (clip_size, 0.19)
        gun = rt._BotGunState(descriptor)
        ammo = rt._BotAmmoState(descriptor, runtime.states[source_id]['profile'])
        burst = rt.burst_mechanics.BurstClock()
        state = {'id': 1}
        kernel = Kernel(backend, rt.bot_state_codec,
                        [bot_config(state, gun, ammo, burst)])
        actions, expected = [], []
        objects = dict(gun=gun, ammo=ammo, burst=burst)

        def act(method, args=()):
            owner, name = method.split('.')
            result = getattr(objects[owner], name)(*args)
            actions.append(dict(method=method, args=args))
            expected.append(plain(dict(
                result=result, state=bot_config(state, gun, ammo, burst))))

        for frame in range(count):
            dt = rng.choice((0, .01, .19, 1.0 / 15, .8, 4.7))
            factor = rng.choice((0.5, 1., 1.8))
            act('gun.rescale_reload', (factor,))
            act('gun.tick', (dt,))
            act('gun.tick_dispersion',
                (dt, rng.uniform(-15, 22), rng.uniform(-.7, .7),
                 rng.uniform(-1, 1), factor, factor))
            act('gun.ready', (factor,))
            act('gun.duration', (factor,))
            act('gun.remaining', (factor,))
            act('gun.complete_reload', (factor, ammo.planned_rounds()))
            act('ammo.stage', (rng.randrange(-1, 5), gun.ready(factor),
                              gun.reload_kind == 'full'))
            act('ammo.can_fire')
            if frame % 11 == 0:
                act('gun.require_full_reload')
            elif frame % 7 == 0:
                act('gun.cancel_burst')
            elif frame % 3 == 0:
                act('gun.fire', (factor,))
                act('ammo.consume_loaded', (bool(frame % 2),))
                act('gun.commit_shot_bloom', (factor, bool(frame % 2)))
            else:
                act('gun.begin_burst', (rng.randrange(-1, clip_size + 2), factor))
                act('gun.fire_burst_round', (bool(frame % 2),))
            act('ammo.planned_rounds')
            act('ammo.loaded_shell_requires_full_reload')
            act('burst.start', (frame + 1, rng.choice((1, 2, 7)), .19, 1))
            act('burst.advance', (dt,))
            if frame % 9 == 0:
                act('burst.cancel', (rng.randrange(burst.next_index + 1),))
        actual = kernel.weapon_actions(1, actions)
        # Descriptor/crew initialization is a Python producer. The native gun
        # owns only mutable clocks and the numeric coefficients it consumes.
        for index, (left, right) in enumerate(zip(expected, actual)):
            for name in ('crew_level', 'loadout'):
                left['state']['gun'].pop(name, None)
            compare(left, right, ('weapon', clip_size, index, actions[index]))
            checks += 1
        kernel.close()
    return checks


def codec_cases(backend, rt, runtime, rng, count):
    kernel = Kernel(backend, rt.bot_state_codec, [])
    states = [{}]
    for unused in range(count):
        state = copy.deepcopy(rng.choice(list(runtime.states.values())))
        for name, scale in rt.bot_state_codec.SCALARS:
            if name == '_flags':
                continue
            state[name] = rng.uniform(-3000, 3000) if scale else rng.randrange(1000)
        state['movement_dir'] = rng.choice((-1, -.01, 0, .01, 1))
        state['rotation_dir'] = rng.choice((-1, -.01, 0, .01, 1))
        state['critical'] = dict(
            devices=[dict(name=name, hp=rng.uniform(0, 500), max_hp=500,
                          state=rng.choice(rt.bot_state_codec.DEVICE_STATES))
                     for name in rng.sample(rt.bot_state_codec.DEVICE_NAMES,
                                            rng.randrange(10))],
            crew_ko=rng.sample(rt.bot_state_codec.CREW_NAMES, rng.randrange(9)),
            crew_roster=list(rt.bot_state_codec.CREW_NAMES),
            fire=bool(rng.randrange(2)), ammo_rack_death=bool(rng.randrange(2)))
        state['equipment_states'] = [dict(
            usesLeft=rng.randrange(10), cooldownTimeLeft=rng.uniform(0, 90),
            active=bool(rng.randrange(2)),
            autoPendingElapsed=rng.choice((None, rng.random())),
            aiPendingElapsed=rng.choice((None, rng.random()))) for i in range(3)]
        for bit, names in rt.bot_state_codec.OPTIONAL_GROUPS:
            if rng.randrange(2):
                for name in names:
                    state.pop(name, None)
        states.append(state)
    expected = [rt.bot_state_codec.encode_row(state) for state in states]
    compare(expected, kernel.encode(states), ('codec',))
    invalid = [None, [], dict(id=True), dict(id=None), dict(x=None),
               dict(x='bad'), dict(id='1.2'), dict(shot_yaw=1),
               dict(clip=1), dict(equipment_states=[None])]
    for state in invalid:
        try:
            rt.bot_state_codec.encode_row(state)
        except Exception:
            pass
        else:
            raise AssertionError(('source accepted invalid test', state))
        try:
            kernel.encode([state])
        except RuntimeError:
            pass
        else:
            raise AssertionError(('native accepted invalid test', state))
    for malformed in (None, {}, 3, 'bad'):
        try:
            backend.call(packet([711, kernel.handle], malformed))
        except RuntimeError:
            pass
        else:
            raise AssertionError(('native accepted malformed array', malformed))
    compare([expected[0]], kernel.encode([{}]), ('after rejection',))
    kernel.close()
    return len(states) + len(invalid)


def equipment_config(equipment):
    return dict((name, getattr(equipment, name))
                for name in equipment.__slots__)


def equipment_cases(backend, rt, runtime, rng, count):
    source_id = next(iter(runtime.states))
    equipment = copy.deepcopy(runtime._equipment_states[source_id])
    gun, ammo = runtime._gun_states[source_id], runtime._ammo_states[source_id]
    state = bot_config({'id': 1}, gun, ammo, rt.burst_mechanics.BurstClock())
    state['equipment'] = [equipment_config(e) for e in equipment]
    kernel = Kernel(backend, rt.bot_state_codec, [state])
    actions, expected = [], []
    now = 0.0
    for frame in range(count):
        now += rng.choice((0, .01, .1, .2, 20, 93))
        critical = dict(fire=bool(frame % 4),
                        devices=[dict(name='engineHealth', state='critical')]
                        if frame % 3 else [],
                        destroyed=['leftTrackHealth'] if frame % 4 else [],
                        crew_ko=['gunner1'] if frame % 5 else [])
        for slot, e in enumerate(equipment):
            method = 'ready' if frame % 5 == 0 else 'poll_bot'
            kwargs = dict(now=now)
            if method == 'poll_bot':
                kwargs.update(critical=critical, stunned=bool(frame % 3))
            result = getattr(e, method)(**kwargs)
            action = dict(kwargs, slot=slot, method=method)
            actions.append(action)
            expected.append(plain(dict(
                result=result, states=[equipment_config(v) for v in equipment],
                wires=[v.trusted_snapshot(now) for v in equipment],
                passives=rt.equipment_mechanics.passive_effects(equipment))))
    compare(expected, kernel.equipment_actions(1, actions), ('equipment',))
    inputs = []
    for digits in range(7):
        scale = 10 ** digits
        inputs.extend((value, digits) for value in
                      (0, -0.0, .125, -.125, 2.675, -2.675, .035, -.035))
        for i in range(count):
            inputs.append((rng.uniform(-10000, 10000), digits))
            inputs.append(((rng.randrange(-10000, 10000) + .5) / scale, digits))
    import sys
    if sys.version_info[0] == 2:
        compare([round(v, digits) for v, digits in inputs],
                kernel.round(inputs), ('decimal rounding',))
    kernel.close()
    return len(actions), len(inputs)


def health_cases(backend, rt, runtime, rng, count):
    bot_id = next(iter(runtime.states))
    original = copy.deepcopy(runtime.states[bot_id])
    descriptor = copy.deepcopy(runtime._descriptors[bot_id])
    descriptor.engine = dict(maxHealth=105, maxRegenHealth=52)
    descriptor.fuelTank = dict(maxHealth=100, maxRegenHealth=40)
    descriptor.miscAttrs = {}
    runtime._descriptors[bot_id] = descriptor
    original_equipment = copy.deepcopy(runtime._equipment_states[bot_id])
    checks = 0
    for mode in ('repair', 'fire', 'drowning', 'overturn', 'kits'):
        state = copy.deepcopy(original)
        state['critical'] = dict(devices=[dict(
            name=name, hp=0.0,
            max_hp=float(rt.device_damage.device_max_hp(descriptor, name)),
            state='destroyed') for name in ('engineHealth', 'leftTrackHealth',
                                            'fuelTankHealth')],
            destroyed=['engineHealth', 'leftTrackHealth', 'fuelTankHealth'],
            crew_ko=['gunner1'], fire=mode == 'fire', events=[],
            ammo_rack_death=False)
        state['combat_fire_elapsed'] = 0.0
        state['combat_fire_timer'] = 0.0
        state['stun_end_server_time_ms'] = 115000 if mode == 'kits' else 0
        state['_stun_until_equipment_time'] = 15.0 if mode == 'kits' else 0.0
        runtime.states[bot_id] = state
        runtime._combat_sync.pop(bot_id, None)
        runtime._equipment_wire_cache.pop(bot_id, None)
        runtime._equipment_states[bot_id] = (
            copy.deepcopy(original_equipment) if mode == 'kits' else [])
        runtime._equipment_passives[bot_id] = rt.equipment_mechanics.passive_effects(
            runtime._equipment_states[bot_id])
        runtime._repair_factors.pop(bot_id, None)
        runtime._friendly_repositions[bot_id] = {'test': True}
        runtime._turn_speeds[bot_id] = .3
        config = bot_config(state, runtime._gun_states[bot_id],
                            runtime._ammo_states[bot_id], rt.burst_mechanics.BurstClock())
        config.update(critical_config=critical_config(rt, runtime, bot_id),
                      config=health_config(rt, descriptor),
                      equipment=[equipment_config(e) for e in
                                 runtime._equipment_states[bot_id]],
                      turn_speed=.3)
        kernel = Kernel(backend, rt.bot_state_codec, [config])
        actions, expected = [], []
        elapsed = 0.0

        def act(action):
            method = action['method']
            runtime._equipment_now = action.get('equipment_now', elapsed)
            if method == 'set':
                state.update(action['state'])
                result = None
            elif method == 'critical':
                result = runtime._advance_bot_critical(
                    state, action['dt'], action['now'])
            elif method == 'publish':
                result = runtime._mark_combat_publication(state)
            elif method == 'drowning':
                runtime._water_depth_probe = lambda position: action['depth']
                result = runtime._advance_bot_drowning(state, action['dt'])
            elif method == 'overturn':
                result = runtime._advance_bot_overturn(state, action['dt'])
            actions.append(action)
            expected.append(plain(dict(
                result=result, state=state, sync=runtime._combat_sync.get(bot_id),
                turn_speed=runtime._turn_speeds.get(bot_id, 0),
                clear_reposition=bot_id not in runtime._friendly_repositions)))

        for frame in range(count):
            dt = rng.choice((0, .01, 1.0 / 15, .15, .37, 1.13))
            elapsed += dt
            if mode == 'overturn':
                act(dict(method='set', state=dict(pitch=1.9, roll=.5)))
                act(dict(method='overturn', dt=dt))
            elif mode == 'drowning':
                act(dict(method='drowning', dt=dt, depth=3.0 if frame > 4 else -.1))
            else:
                act(dict(method='critical', dt=dt, now=100 + elapsed,
                         equipment_now=elapsed))
            if frame % 7 == 0:
                act(dict(method='publish'))
        compare(expected, kernel.health_actions(bot_id, actions), ('health', mode))
        checks += len(actions)
        kernel.close()
    return checks


def boundary_cases(backend, rt):
    """Exercise caller-owned byte output and recovery after rejected commands."""
    kernel = Kernel(backend, rt.bot_state_codec, [])
    checks = 0
    try:
        size = int(backend.call([724, kernel.handle])[0])
        expected = kernel.output(size)
        for width in (2, (size + 7) // 8 + 1):
            guard = 987654321.125
            values = array('d', [guard] + [719, kernel.handle] +
                           [0] * (width - 2) + [guard])
            address = values.buffer_info()[0] + values.itemsize
            status = backend.module.dispatch(int(address & 0xffff), int(address >> 16), width)
            assert values[0] == values[-1] == guard
            if width == 2:
                assert status != 0 and values[1:3] == array('d', [719, kernel.handle])
            else:
                assert status == 0 and int(values[1]) == size
                payload = values[2:-1]
                raw = payload.tobytes() if hasattr(payload, 'tobytes') else payload.tostring()
                compare(expected, json.loads(raw[:size].decode('utf-8')), ('packed output',))
            checks += 1
        invalid = ([724], [724, float('nan')], [724, float('inf')],
                   [724, kernel.handle, 1], [726, kernel.handle],
                   [723, kernel.handle, 2, 123, 125],
                   [711, kernel.handle, 2, 91], [711, kernel.handle, 1, 256],
                   [700, 2, 123, 125], [700, -1])
        for command in invalid:
            try:
                backend.call(command)
            except RuntimeError:
                checks += 1
            else:
                raise AssertionError(('accepted invalid kernel boundary', command))
            compare(expected, kernel.output(size), ('recovery after rejection',))
    finally:
        identity = kernel.handle
        kernel.close()
    kernel.close()
    try:
        backend.call([724, identity])
    except RuntimeError:
        checks += 1
    else:
        raise AssertionError('retired kernel owner remained accessible')
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=300)
    args = parser.parse_args()
    with redirected():
        fixture = load_fixture(args.fixture)
        runtime, unused_ground = fixture['make_runtime'](
            Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    backend = Backend(args.module)
    try:
        rng = random.Random(7151327)
        weapons = weapon_cases(backend, rt, runtime, rng, args.cases)
        rows = codec_cases(backend, rt, runtime, rng, args.cases)
        equipment, decimals = equipment_cases(backend, rt, runtime, rng, args.cases)
        health = health_cases(backend, rt, runtime, rng, args.cases)
        boundaries = boundary_cases(backend, rt)
        print('Exact owned kernel state: %d weapon transitions, %d wire/invalid rows, %d equipment transitions, %d decimal cases, %d health/publication transitions.' % (weapons, rows, equipment, decimals, health))
        print('Packed output, malformed commands and owner retirement: %d checks.' % boundaries)
    finally:
        backend.close()


if __name__ == '__main__':
    main()
