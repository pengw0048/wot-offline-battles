#!/usr/bin/env python
"""Compare native chassis contact episodes and reliable human ram receipts."""
from __future__ import print_function

import argparse
import copy
import json
import math
import random

from check_kernel_state import compare, plain
from kernel_adapter import Kernel, ENGINE_FIELDS, bot_config, motion_config
from kernel_engine import EngineLeaves
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=240)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    with redirected():
        source, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
        native, unused = fixture['make_runtime'](Path(ROOT), '59_asia_great_wall', 'combat')
    rt = fixture['fixtures']._load()
    ids = list(source.states)[:8]
    for runtime in (source, native):
        runtime.states = dict((key, runtime.states[key]) for key in ids)
        runtime.navigator = None
    backend = Backend(args.module)
    bots = [bot_config(native.states[identity], native._gun_states[identity],
                       native._ammo_states[identity], native._burst_states[identity]) for identity in ids]
    kernel = Kernel(backend, rt.bot_state_codec, bots, engine=dict(fields=ENGINE_FIELDS),
                    motion=motion_config(rt, native),
                    contacts=dict(human_base=rt.HUMAN_TARGET_ID_BASE, has_armor=True, py2=True,
                                  wreck_drop=rt.WRECK_SUPPORT_DROP, wreck_rise=rt.WRECK_SUPPORT_RISE))
    leaves = EngineLeaves(rt, native)
    source_queries, native_queries, frame_box = [], [], [0]
    def install(runtime, tape):
        def direction(position, yaw, speed, descriptor, maximum=None, width=None):
            tape.append(('direction', position, yaw, speed, maximum, width))
            if frame_box[0] % 13 == 7:
                raise RuntimeError('injected blocked contact sweep')
            return dict(clear=True, collision=False, water=False)
        def armor(first, second, contact):
            tape.append(('armor', first, second, contact))
            return None if frame_box[0] % 11 == 3 else (40.0, 65.0)
        runtime.direction_probe = direction
        runtime.ram_contact_probe = armor
    install(source, source_queries)
    install(native, native_queries)
    checks = [0]

    def act(method, **kwargs):
        action = dict(kwargs, method=method)
        result = None
        if method == 'set':
            source.states[kwargs['id']].update(kwargs['state'])
        elif method == 'resolve':
            result = rt.tank_collision.resolve_tank(
                kwargs['body'], kwargs['others'], now=kwargs.get('now'),
                ram_cooldowns=source._ram_cooldowns,
                active_ram_contacts=kwargs['previous'])
            source._ram_cooldowns = result.pop('cooldowns')
            result['contacts'] = sorted(result['contacts'])
        elif method == 'step':
            with redirected():
                result = source._resolve_tank_contacts(kwargs['players'], kwargs['now'], kwargs['dt'])
        elif method == 'ack':
            result = source.ack_human_ram_receipt(kwargs['player'], kwargs['seq'])
        expected = plain(dict(result=result, states=source.states,
                              contacts=sorted(source._ram_contacts),
                              leases=source._contact_lease_elapsed,
                              cooldowns=sorted((key[0], key[1], value) for key, value in source._ram_cooldowns.items())))
        actual = kernel.contact_actions([action], leaves)[0]
        compare(plain(source_queries), plain(native_queries), (checks[0], method, 'engine'))
        compare(expected, actual, (checks[0], method))
        del source_queries[:]
        del native_queries[:]
        checks[0] += 1
        return result

    rng = random.Random(7231513)
    try:
        previous = []
        for index in range(args.cases):
            shape = (rng.uniform(.8, 3), rng.uniform(1.5, 5), -.8, rng.uniform(1, 4))
            def body(identity):
                return dict(id=identity, team=1 if identity % 3 else 2,
                            alive=index % 17 != identity % 17,
                            x=rng.uniform(-4, 4), y=rng.uniform(-.8, .8), z=rng.uniform(-4, 4),
                            yaw=rng.uniform(-math.pi, math.pi), mass=rng.uniform(3000, 95000),
                            vx=rng.uniform(-20, 20), vy=rng.uniform(-25, 25), vz=rng.uniform(-20, 20),
                            shape=shape, contact_armor=None if index % 5 else rng.uniform(5, 200),
                            ram_profile=dict(spall_coefficient=1.25, ramming_bonus=.12),
                            impulse=index % 7 != identity % 7, vehicle='fixture')
            result = act('resolve', body=body(11), others=[body(value) for value in (12, 13, 14)],
                         now=index * .07 if index % 19 else None, previous=previous)
            previous = result['contacts']
        for frame in range(32):
            frame_box[0] = frame
            for index, identity in enumerate(ids):
                act('set', id=identity, state=dict(
                    x=(index % 4) * 5.5, y=0, z=(index // 4) * 8 + (frame % 5) * .2,
                    yaw=0 if index < 4 else math.pi,
                    speed=10 if frame % 9 else 0, push_x=0, push_z=0,
                    alive=frame % 8 != index, team=1 if index < 4 else 2))
            act('step', players=[], now=100 + frame * .11, dt=.11)

        with open(args.fixture) as stream:
            body = json.load(stream)['_effective_params_snapshot']
        scope = {}
        eval(compile(body, '<exact-player-effective-fixture>', 'exec'), scope)
        player = dict(id=1, team=2, vehicle='ussr:R11_MS-1', alive=True,
                      x=0, y=0, z=5.8, yaw=math.pi, speed=10,
                      effective_params=scope['_effective_params_snapshot']())
        player['_kernel'] = dict(collision=source._player_collision_profile(player))
        leaves.players[1] = dict(player)
        identity = ids[0]
        for seq in range(1, 17):
            act('set', id=identity, state=dict(x=0, y=0, z=0, yaw=0, speed=10, pitch=0,
                                              roll=0, alive=True, team=1, push_x=0, push_z=0))
            historical = copy.deepcopy(source.states[identity])
            receipt = dict(seq=seq, bot_id=identity, x=0, y=0, z=5.8, yaw=math.pi,
                           vx=0, vy=0, vz=-10, bot_vx=0, bot_vy=0, bot_vz=10,
                           presentation_time_us=10000000, contact_x=0, contact_y=0,
                           contact_z=2.9, contact_armor_player=50, contact_armor_bot=70,
                           contact_normal_x=0, contact_normal_z=1,
                           contact_spall_player=1.25, contact_bonus_player=.1)
            if seq % 5 == 2:
                receipt['contact_armor_bot'] = 0
            if seq % 5 == 3:
                receipt['contact_z'] = 100
            current = dict(player, ram_contact=receipt, _ram_contact_bot_state=historical)
            if seq % 5 == 1:
                act('step', players=[dict(current, _ram_contact_bot_state=None)], now=120 + seq, dt=.07)
            act('step', players=[current], now=120 + seq, dt=.07)
            act('step', players=[current], now=120 + seq + .1, dt=.07)
            act('ack', player=1, seq=seq)
            act('step', players=[current], now=120 + seq + .2, dt=.07)
    finally:
        kernel.close()
    print('Native tank contact parity: %d solver/lifecycle transitions.' % checks[0])


if __name__ == '__main__':
    main()
