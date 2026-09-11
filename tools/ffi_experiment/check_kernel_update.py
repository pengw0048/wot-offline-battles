#!/usr/bin/env python
"""Compare the complete owned Bot update with the unchanged Python update."""
from __future__ import print_function
import argparse
import copy
import json
import random
import sys
from check_kernel_state import compare, plain
from kernel_adapter import KernelBackend
from navigation_adapter import Backend
from portable_workload import load_fixture, Path, ROOT, redirected, patch_dict


def check_unsupported_motion(runtime, rt):
    class NoNativeCalls(object):
        def call(self, *unused):
            raise AssertionError('Unsupported motion allocated native state')
    for name, message in (('_turret_motion_probe', 'detached-turret collision'),
                          ('_turret_hulls_provider', 'detached-turret collision'),
                          ('native_motion', 'copied vertical controller'),
                          ('_suspension_ground_probe', 'copied vertical controller')):
        previous = getattr(runtime, name)
        setattr(runtime, name, True if name == 'native_motion' else lambda *unused: None)
        try:
            try:
                KernelBackend(NoNativeCalls(), runtime, rt)
            except ValueError as error:
                assert message in str(error), (name, error)
            else:
                raise AssertionError('Unsupported motion accepted: ' + name)
        finally:
            setattr(runtime, name, previous)


def run(backend, fixture, map_name, scenario, frames, siege=False, human=False, cover=False, fixture_source=None, orders=False, native_world=False, projection_failure=False, fps=None):
    with redirected():
        random.seed(17)
        source, source_ground = fixture['make_runtime'](Path(ROOT), map_name, scenario)
        random.seed(17)
        native, native_ground = fixture['make_runtime'](Path(ROOT), map_name, scenario)
    rt = fixture['fixtures']._load()
    check_unsupported_motion(native, rt)
    player = None
    if human:
        with open(fixture_source) as stream:
            body = json.load(stream)['_effective_params_snapshot']
        scope = {}
        eval(compile(body, 'human_fixture', 'exec'), scope)
        states = list(source.states.values())
        player = dict(id=1, team=1, vehicle=states[0]['vehicle'],
                      x=sum(state['x'] for state in states) / len(states),
                      y=states[0]['y'], z=sum(state['z'] for state in states) / len(states),
                      yaw=0, speed=0, alive=True, health=1000, max_health=1000,
                      fire_seq=0, critical={}, effective_params=scope['_effective_params_snapshot']())
    if siege:
        for runtime in (source, native):
            for identity in sorted(runtime.states)[:3]:
                travel = runtime._descriptors[identity]
                travel.type = dict(name='sweden:S11_Strv_103B')
                enabled = copy.deepcopy(travel)
                enabled.gun.shotDispersionAngle = .012
                enabled.gun.aimingTime = .9
                enabled.gun.reloadTime = .8
                enabled.physics['speedLimits'] = (10.0 / 3.6, 5.0 / 3.6)
                runtime._descriptor_pairs[identity] = (travel, enabled)
                runtime.states[identity]['vehicle'] = 'sweden:S11_Strv_103B'
    failed_id = sorted(source.states)[-1]
    if projection_failure:
        for runtime in (source, native):
            # A broken shot-angle pair must retain just this actor's checkpoint.
            # Retire its driving so the fixture isolates publication recovery.
            state = runtime.states[failed_id]
            state.update(alive=False, health=0, speed=0, shot_yaw=0.0)
            state.pop('shot_pitch', None)
    with fixture['combat_native_queries'](source, source_ground) as source_queries:
        source_env = dict((name, sys.modules[name]) for name in ('AreaDestructibles', 'BigWorld', 'Math'))
        with fixture['combat_native_queries'](native, native_ground) as native_queries:
            native_env = dict((name, sys.modules[name]) for name in source_env)
            tapes = [[], []]
            for index, runtime in enumerate((source, native)):
                for name in ('direction_probe', 'world_receipt_probe'):
                    original = getattr(runtime, name)
                    if not callable(original):
                        continue
                    def wrap(original, name, tape):
                        def probe(*args, **kwargs):
                            tape.append((name, args[:3], args[4:], kwargs))
                            return original(*args, **kwargs)
                        return probe
                    setattr(runtime, name, wrap(original, name, tapes[index]))
            cover_calls = [[], []]
            if cover:
                def cover_probe(tape):
                    def probe(source, target, route, allies, clear):
                        tape.append((source['id'], target['network_id'], rt._position(source),
                                     target['position'], route, allies))
                        return [dict(position=route, supported=clear(rt._position(source), route),
                                     target_id=target['network_id'])]
                    return probe
                source.cover_probe = cover_probe(cover_calls[0])
                native.cover_probe = cover_probe(cover_calls[1])
            kernel = KernelBackend(backend, native, rt,
                                   world_owner=native._ffi_world_owner if native_world else None)
            try:
                enabled_seen = False
                failed_publications = restored_publications = 0
                query_cursor = 0
                for frame in range(frames):
                    if projection_failure and frame == 4:
                        restored = dict(shot_pitch=0.0)
                        source.states[failed_id].update(restored)
                        kernel.kernel.health_actions(failed_id, [dict(method='set', state=restored)])
                    if orders and frame % 13 == 5:
                        message = dict(bot_order_revision=source._order_revision + 1,
                                       bot_orders=[dict(copy.deepcopy(order), id=identity)
                                                   for identity, order in source._server_orders.items()])
                        for order in message['bot_orders'][:5]:
                            order['route_id'] = 'kernel-change-%d' % frame
                            order['fire_allowed'] = frame % 2 == 0
                            order['throttle_override'] = .35 if frame % 2 else .8
                        assert source._apply_orders(message)
                        assert native._apply_orders(message)
                    dt = (1.0 / 30, 1.0 / 30, 1.0 / 30, .27, .013)[frame % 5]
                    now = 100 + (frame + 1) * .13
                    if fps is not None:
                        dt = 1.0 / fps
                        now = 100.0 + (frame + 1) / fps
                    players = []
                    if player is not None:
                        players = [dict(player, x=player['x'] + frame * .7,
                                        alive=frame % 19 not in (8, 9, 10),
                                        fire_seq=frame // 5, speed=3 if frame % 7 else 0)]
                        source._camera_position = native._camera_position = (
                            player['x'] + (0, 400, 900)[frame % 3], player['y'], player['z'])
                    with redirected():
                        with patch_dict(sys.modules, source_env):
                            expected = source.update(dt, now, players)
                        with patch_dict(sys.modules, native_env):
                            actual = kernel.update(dt, now, players)
                    try:
                        compare(plain(expected), plain(actual), (map_name, scenario, frame, 'messages'))
                    except AssertionError:
                        dump = dict(expected=expected, actual=actual, native=kernel.snapshot(),
                                    states=source.states, source_queries=source_queries,
                                    native_queries=native_queries, decisions=source._decision_cache)
                        with open('/tmp/wot-kernel-update-difference.json', 'w') as stream:
                            json.dump(dump, stream, default=repr, indent=2)
                        raise
                    if projection_failure:
                        publications = [message for message in actual if message['type'] == 'bot_state']
                        for publication in publications:
                            affected = [row for row in publication['rows'] if row[0] == failed_id]
                            assert len(affected) == 1
                            assert (len(affected[0]) == 1) == (frame < 4)
                            assert any(len(row) > 1 for row in publication['rows'])
                            if frame < 4:
                                failed_publications += 1
                            else:
                                restored_publications += 1
                    compare(plain(source_queries[query_cursor:]), plain(native_queries[query_cursor:]),
                            (map_name, scenario, frame, 'queries'))
                    query_cursor = len(source_queries)
                    if native_world:
                        compare(source._ffi_world_owner._bot_motion_kinds,
                                native._ffi_world_owner._bot_motion_kinds,
                                (map_name, scenario, frame, 'world_kinds'))
                        assert kernel.engine.world.context is None
                    compare(source._last_update_control_steps, native._last_update_control_steps,
                            (map_name, scenario, frame, 'control_steps'))
                    compare(source._last_update_max_control_step, native._last_update_max_control_step,
                            (map_name, scenario, frame, 'maximum_step'))
                    compare(plain(cover_calls[0]), plain(cover_calls[1]), (map_name, scenario, frame, 'cover_leaves'))
                    compare(plain(tapes[0]), plain(tapes[1]), (map_name, scenario, frame, 'motion_leaves'))
                    compare(plain(source.probe_totals()), plain(kernel.probes()),
                            (map_name, scenario, frame, 'probes'))
                    compare(source._decision_counts, kernel.counters()['decisions'],
                            (map_name, scenario, frame, 'decisions'))
                    compare(source.diagnostic_totals(), kernel.diagnostics(),
                            (map_name, scenario, frame, 'diagnostics'))
                    expected_bots = {}
                    for identity, state in source.states.items():
                        clean = dict((key, value) for key, value in state.items()
                                     if key not in ('_gun_pitch_limit_cache', '_motion_stall_pending'))
                        expected_bots[str(identity)] = dict(state=clean, gun=source._gun_states[identity].__dict__,
                            ammo=source._ammo_states[identity].__dict__, burst=source._burst_states[identity].__dict__)
                    snapshot = kernel.snapshot()
                    enabled_seen = enabled_seen or any(state.get("siege_state") == 2 for state in source.states.values())
                    # Static producer fields remain on their Python descriptors;
                    # the focused weapon tests compare their full clock schema.
                    for identity in expected_bots:
                        compare(plain(expected_bots[identity]['state']), snapshot['bots'][identity]['state'],
                                (map_name, scenario, frame, identity, 'state'))
                    for message in expected:
                        for launch in message.get('launches', ()):
                            assert source.ack_projectile_launch(launch['id'], launch['fire_seq'])
                            assert kernel.ack(launch['id'], launch['fire_seq'])
                # Retain a full-history check for accidental mutation of old receipts.
                compare(plain(source_queries), plain(native_queries), (map_name, scenario, 'all queries'))
                if projection_failure:
                    assert failed_publications, 'Fixture never published the failed actor'
                    if frames > 4:
                        assert restored_publications, 'Fixture never published the restored actor'
                if cover and frames >= 30:
                    assert cover_calls[0], "Cover fixture never serviced a job"
                    assert len(kernel.engine.tokens) < 30, "Retired cover receipts leaked"
                if siege and frames >= 30:
                    assert enabled_seen, "Siege fixture never completed its transition"
                if native_world:
                    class WorldFailure(Exception):
                        pass
                    original_query = kernel.engine.world.query
                    def fail_world(*unused):
                        raise WorldFailure('injected owned world leaf failure')
                    kernel.engine.world.query = fail_world
                    failed = False
                    try:
                        for retry in range(30):
                            try:
                                with patch_dict(sys.modules, native_env):
                                    kernel.update(.13, now + (retry + 1) * .13, players)
                            except WorldFailure:
                                failed = True
                                assert kernel.engine.world.context is None
                                break
                        assert failed, 'World failure fixture issued no collision query'
                    finally:
                        kernel.engine.world.query = original_query
            finally:
                kernel.close()
                kernel.close()
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--frames', type=int, default=30)
    parser.add_argument('--fps', type=float, help='use the workload clock instead of irregular updates')
    parser.add_argument('--cover', action='store_true')
    parser.add_argument('--human', action='store_true')
    parser.add_argument('--siege', action='store_true')
    parser.add_argument('--orders', action='store_true')
    parser.add_argument('--native-world', action='store_true')
    parser.add_argument('--projection-failure', action='store_true')
    parser.add_argument('--map', default='59_asia_great_wall')
    parser.add_argument('--scenario', default='combat')
    args = parser.parse_args()
    if args.fps is not None and not 0 < args.fps < float('inf'):
        parser.error('--fps must be positive and finite')
    fixture = load_fixture(args.fixture)
    count = run(Backend(args.module), fixture, args.map, args.scenario, args.frames, args.siege, args.human, args.cover, args.fixture, args.orders, args.native_world, args.projection_failure, args.fps)
    print('Native whole Bot update parity: %d complete callbacks.' % count)


if __name__ == '__main__':
    main()
