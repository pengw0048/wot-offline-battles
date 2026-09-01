import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' /
                'gui' / 'mods' / 'offline_lan_0922')
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import BattleState, CLIENT_BUILD_0922, Player  # noqa: E402
from effective_params_fixture import effective_params  # noqa: E402


def _load_bot_runtime():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    ai_name = 'gui.mods.offline_lan_0922.ai'
    if ai_name not in sys.modules:
        module = types.ModuleType(ai_name)
        module.__path__ = [str(PACKAGE_ROOT / 'ai')]
        sys.modules[ai_name] = module
    name = 'gui.mods.offline_lan_0922.bot_runtime'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, PACKAGE_ROOT / 'bot_runtime.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _StrictComponent(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class _HitTester(object):
    def __init__(self, minimum, maximum):
        self.bbox = (minimum, maximum, None)


def _descriptor():
    gun = types.SimpleNamespace(
        shots=({'shell': {'effectsIndex': 0}, 'speed': 1000.0,
                'gravity': 9.81, 'maxDistance': 1000.0},),
        reloadTime=0.5,
        clip=(1,), turretYawLimits=(-math.pi, math.pi),
        pitchLimits={'absolute': (-0.35, 0.15)}, rotationSpeed=10.0,
        shotDispersionAngle=0.03, maxHealth=54, maxRegenHealth=27)
    chassis = _StrictComponent(
        hitTester=_HitTester((-1.5, -0.8, -3.5), (1.5, 0.8, 3.5)),
        hullPosition=(0.0, 0.6, 0.0), rotationSpeed=0.75,
        shotDispersionFactors=(0.14, 0.14),
        maxHealth=170, maxRegenHealth=130)
    hull = _StrictComponent(
        hitTester=_HitTester((-1.7, -0.2, -3.5), (1.7, 1.4, 3.5)),
        turretPositions=((0.0, 1.0, 0.0),))
    descriptor = types.SimpleNamespace(
        gun=gun,
        turret={'rotationSpeed': 10.0, 'circularVisionRadius': 445.0},
        physics={'speedLimits': (14.0, 7.0)}, chassis=chassis,
        hull=hull, maxHealth=1000, fuelTank={
            'maxHealth': 100, 'maxRegenHealth': 40}, miscAttrs={})
    return descriptor


def _graph():
    routes = ((0.0, 0.0, False), (8.0, 0.0, False))
    return {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
        'cell_size': 4.0, 'origin': (0.0, 0.0),
        'bounds': (0, 0, 8, 0), 'width': 3, 'height': 1,
        'heights_mm': (0, 0, 0),
        'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
        'hazards': (0, 0, 0),
        'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
        'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
        'spawn_formations': {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, math.pi)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'route-1', 'waypoints': routes},),
            '2': ({'id': 'route-2', 'waypoints': tuple(reversed(routes))},),
        },
        'bake': {'max_grade': 0.30},
    }


def _spawn(team, slot):
    point = _graph()['spawn_formations'][str(int(team))][int(slot)]
    return ((point[0], point[1], point[2]), point[3])


def _human():
    return {
        'id': 1, 'team': 1, 'alive': True,
        'x': 0.0, 'y': 0.0, 'z': 100.0,
        'effective_params': effective_params(),
    }


class _Director(object):
    def __init__(self):
        self.agents = {}

    def register_profile(self, bot_id, team, profile, name):
        self.agents[bot_id] = {
            'team': team, 'profile': profile, 'name': name,
            'route': {'id': 'test', 'waypoints': []},
        }


class _FixedAdapter(object):
    def __init__(self, command):
        self.command = dict(command)
        self.director = _Director()

    def register(self, bot_id, team, descriptor, name):
        self.director.register_profile(bot_id, team, {}, name)

    def decide(self, state, clear):
        clear(state['yaw'])
        return dict(self.command)

    def decide_with_order(self, state, strategic, clear):
        return self.decide(state, clear)


def _burning_payload():
    return {
        'devices': [{
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed',
        }],
        'destroyed': ['fuelTankHealth'], 'crew_ko': [],
        'fire': True, 'ammo_rack_death': False, 'events': [],
    }


class ServerCombatLineageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot_runtime = _load_bot_runtime()

    @staticmethod
    def _roster():
        return [{
            'id': 11 + index,
            'team': 1 if index < 14 else 2,
            'slot': index if index < 14 else index - 14,
            'name': 'Lineage-%d' % index,
        } for index in range(29)]

    def _runtime_and_server(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.bot_runtime.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.bot_runtime.BotRuntime(
            1, descriptor_resolver=lambda unused: _descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn, baked_graph=_graph())
        roster = self._roster()
        manifest = runtime.battle_start({
            'round_id': 1, 'map': '01_karelia',
            'bot_authority_id': 1, 'bots': roster,
        })[0]
        for state in runtime.states.values():
            state.update(x=0.0, y=0.0, z=0.0,
                         yaw=0.0, aim_yaw=0.0)

        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(roster)
        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id, 'bots': manifest['bots']}))
        return runtime, server, roster

    def test_same_source_batch_horizon_preserves_slow_callback_burst_edges(
            self):
        unused_runtime, server, unused_roster = self._runtime_and_server()
        shooter = server.bot_states[11]
        shooter.update({
            'clip': 3, 'clip_size': 3,
            'reload_time': 0.0, 'reload_duration': 0.5,
            'ammo_remaining': [45], 'ammo_reload_pending': False,
        })

        def publication(fire_seq, ammo, clip, burst_active, next_index,
                        time_left):
            rows = []
            for bot_id in sorted(server.bot_states):
                row = dict(server.bot_states[bot_id])
                row['ammo_remaining'] = list(
                    row.get('ammo_remaining') or [])
                row['equipment_states'] = list(
                    row.get('equipment_states') or [])
                row['combat_seq'] = int(row.get('combat_ack_seq', 0))
                rows.append(row)
            shot = next(row for row in rows if row['id'] == 11)
            shot.update({
                'fire_seq': fire_seq,
                'ammo_remaining': [ammo],
                'ammo_reload_pending': True,
                'clip': clip, 'clip_size': 3,
                'reload_time': 0.5, 'reload_duration': 0.5,
                'burst_active': burst_active,
                'burst_group_seq': 1, 'burst_count': 3,
                'burst_next_index': next_index,
                'burst_interval': 0.1,
                'burst_time_left': time_left,
                'burst_shell_index': 0,
            })
            return rows

        # One slow worker callback can expose its first physical edge before
        # the callback's final source horizon. The later publication belongs
        # to the same callback even though almost no server wall time elapsed.
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id,
            'bots': publication(1, 44, 2, True, 1, 0.1),
            'sample_time_us': 200000,
            'source_batch_horizon_us': 1000000,
        }), server.last_bot_state_reject)
        first_mapped_time_us = server.bot_state_time_us

        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id,
            'bots': publication(3, 42, 0, False, 3, 0.0),
            'sample_time_us': 1000000,
            'source_batch_horizon_us': 1000000,
        }), server.last_bot_state_reject)

        launches = sorted(server.bot_pending_projectile_launches)
        metadata = server.bot_pending_projectile_metadata
        self.assertEqual({
            'launches': [(11, 1), (11, 2), (11, 3)],
            'burst_indexes': [0, 1, 2],
            'sample_windows': [
                (0, 200000),
                (200000, 1000000),
                (200000, 1000000),
            ],
            'mapped_time_delta_us': 800000,
        }, {
            'launches': launches,
            'burst_indexes': [
                metadata[key]['burst_index'] for key in launches],
            'sample_windows': [(
                metadata[key]['sample_start_us'],
                metadata[key]['sample_end_us']) for key in launches],
            'mapped_time_delta_us': (
                server.bot_state_time_us - first_mapped_time_us),
        })

    def test_29_bot_fire_hits_survive_external_combat_rebases(self):
        runtime, server, roster = self._runtime_and_server()
        # This historical stress test invokes legacy reports directly.  Keep
        # its modern combat-publication contract while selecting the legacy
        # hit admission only around each direct report; #1513 ledger behavior
        # is covered independently by test_port_0922_server_projectiles.py.
        def legacy_report(method, message):
            build = server.client_build
            server.client_build = 'wot-0.8.2'
            try:
                return method(1, message)
            finally:
                server.client_build = build
        human = _human()
        last_fire = dict((entry['id'], 0) for entry in roster)
        publications = 0
        hit_reports = 0
        ignited = False
        fps = 24
        for frame in range(fps * 10):
            outgoing = runtime.update(
                1.0 / fps, 1.0 + frame / float(fps), players=[human])
            for message in outgoing:
                if message.get('type') != 'bot_state':
                    continue
                publications += 1
                self.assertTrue(server.update_bot_states(1, {
                    'round_id': server.round_id,
                    'bots': message['bots'],
                    'sample_time_us': message['sample_time_us'],
                    'source_batch_horizon_us':
                        message['source_batch_horizon_us'],
                }), server.last_bot_state_reject)

                if not ignited:
                    server.players[1].fire_seq = 1
                    target = runtime.states[11]
                    self.assertTrue(legacy_report(server.report_bot_hit, {
                        'round_id': server.round_id, 'target': 11,
                        'shot_seq': 1, 'damage': 1,
                        'critical': _burning_payload(),
                        'critical_target_base_revision':
                            target['combat_base_revision'],
                        'critical_target_ack_seq':
                            target['combat_ack_seq'],
                        'hull_damage': 1,
                    }), server.last_bot_hit_reject)
                    ignited = True

                for bot in message['bots']:
                    bot_id = bot['id']
                    current_fire = bot['fire_seq']
                    self.assertLessEqual(current_fire - last_fire[bot_id], 1)
                    if current_fire > last_fire[bot_id]:
                        target_id = 11 + ((bot_id + current_fire) % 14)
                        self.assertTrue(legacy_report(server.report_bot_hit, {
                            'round_id': server.round_id,
                            'attacker_bot': bot_id,
                            'target': target_id,
                            'shot_seq': current_fire,
                            'damage': 1,
                        }), server.last_bot_hit_reject)
                        hit_reports += 1
                    last_fire[bot_id] = current_fire

            # Deliberately delay canonical combat snapshots by half a second.
            # This is the ordering window that used to form an unsent replay
            # proposal and then skip it on the next render publication.
            if frame % 12 == 0:
                runtime.apply_snapshot({
                    'server_tick': frame,
                    'bots': [dict(server.bot_states[bot_id])
                             for bot_id in sorted(server.bot_states)],
                })

        self.assertEqual(fps * 10, publications)
        self.assertGreater(hit_reports, 100)
        self.assertTrue(ignited)

    def test_external_hit_crew_roster_survives_repeated_publication(self):
        runtime, server, unused_roster = self._runtime_and_server()
        human = _human()
        first = runtime.update(
            1.0 / 24.0, 1.0, players=[human])[0]
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': first['bots'],
            'sample_time_us': first['sample_time_us'],
            'source_batch_horizon_us':
                first['source_batch_horizon_us']}))

        critical = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 170.0,
                'max_hp': 170.0, 'state': 'normal',
            }],
            'destroyed': [], 'crew_ko': [],
            'crew_roster': ['driver', 'gunner1'],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        }
        canonical = server.bot_states[11]
        before = server._bot_combat_signature(canonical)
        canonical.update(health=900, display_health=900,
                         critical=dict(critical))
        self.assertTrue(server._commit_external_bot_combat(
            canonical, before))

        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [dict(server.bot_states[bot_id])
                     for bot_id in sorted(server.bot_states)],
        })
        repeated = runtime.update(
            1.0 / 24.0, 1.1, players=[human])[0]
        wire = next(bot for bot in repeated['bots'] if bot['id'] == 11)
        self.assertEqual(['driver', 'gunner1'],
                         wire['critical']['crew_roster'])
        self.assertEqual(0, wire['combat_seq'])
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': repeated['bots'],
            'sample_time_us': repeated['sample_time_us'],
            'source_batch_horizon_us':
                repeated['source_batch_horizon_us'],
        }), server.last_bot_state_reject)
        for frame in range(1, 25):
            publication = runtime.update(
                1.0 / 24.0, 1.1 + frame / 24.0,
                players=[human])[0]
            self.assertTrue(server.update_bot_states(1, {
                'round_id': server.round_id,
                'bots': publication['bots'],
                'sample_time_us': publication['sample_time_us'],
                'source_batch_horizon_us':
                    publication['source_batch_horizon_us'],
            }), server.last_bot_state_reject)
            if server.bot_pending_projectile_launches:
                break
        self.assertTrue(any(
            bot_id != 11 for bot_id, unused_fire_seq in
            server.bot_pending_projectile_launches))

    def test_exact_reject_reason_is_coalesced_without_changing_returns(self):
        runtime, server, unused_roster = self._runtime_and_server()
        def legacy_report(method, message):
            build = server.client_build
            server.client_build = 'wot-0.8.2'
            try:
                return method(1, message)
            finally:
                server.client_build = build
        publication = runtime.update(
            1.0 / 24.0, 1.0, players=[_human()])[0]
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': publication['bots'],
            'sample_time_us': publication['sample_time_us'],
            'source_batch_horizon_us':
                publication['source_batch_horizon_us']}))

        malformed = [dict(bot) for bot in publication['bots']]
        malformed[0]['combat_seq'] = 2
        rejected_sample_time_us = publication['sample_time_us'] + 1
        self.assertFalse(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': malformed,
            'sample_time_us': rejected_sample_time_us,
            'source_batch_horizon_us': rejected_sample_time_us}))
        self.assertEqual('combat_contract', server.last_bot_state_reject_code)
        self.assertIn('bot=11', server.last_bot_state_reject)
        self.assertIn('client_seq=2', server.last_bot_state_reject)
        self.assertIn('server_ack=0', server.last_bot_state_reject)
        self.assertTrue(server.should_log_protocol_reject(
            'bot_state', False))

        self.assertFalse(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': malformed,
            'sample_time_us': rejected_sample_time_us,
            'source_batch_horizon_us': rejected_sample_time_us}))
        self.assertFalse(server.should_log_protocol_reject(
            'bot_state', False))
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': publication['bots'],
            'sample_time_us': rejected_sample_time_us,
            'source_batch_horizon_us': rejected_sample_time_us}))
        self.assertFalse(server.should_log_protocol_reject(
            'bot_state', True))
        self.assertFalse(server.update_bot_states(1, {
            'round_id': server.round_id, 'bots': malformed,
            'sample_time_us': rejected_sample_time_us + 1,
            'source_batch_horizon_us': rejected_sample_time_us + 1}))
        self.assertTrue(server.should_log_protocol_reject(
            'bot_state', False))

        self.assertFalse(legacy_report(server.report_bot_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 11, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertEqual('shot_lineage', server.last_bot_hit_reject_code)
        self.assertIn('attacker_bot=25', server.last_bot_hit_reject)
        self.assertIn('client_fire=99', server.last_bot_hit_reject)
        self.assertIn('server_fire=', server.last_bot_hit_reject)
        self.assertIn('server_target_base=', server.last_bot_hit_reject)
        self.assertIn('server_target_ack=', server.last_bot_hit_reject)
        self.assertTrue(server.should_log_protocol_reject(
            'bot_hit', False))
        self.assertFalse(legacy_report(server.report_bot_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 11, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertFalse(server.should_log_protocol_reject(
            'bot_hit', False))
        server.bot_states[25]['fire_seq'] = 1
        accepted = legacy_report(server.report_bot_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 12, 'shot_seq': 1, 'damage': 0,
        })
        self.assertTrue(accepted, server.last_bot_hit_reject)
        self.assertFalse(server.should_log_protocol_reject(
            'bot_hit', accepted))
        self.assertFalse(legacy_report(server.report_bot_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 11, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertTrue(server.should_log_protocol_reject(
            'bot_hit', False))

        self.assertFalse(legacy_report(server.report_bot_human_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 1, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertEqual(
            'shot_lineage', server.last_bot_human_hit_reject_code)
        self.assertIn('attacker_bot=25', server.last_bot_human_hit_reject)
        self.assertIn('client_fire=99', server.last_bot_human_hit_reject)
        self.assertIn(
            'server_target_base=', server.last_bot_human_hit_reject)
        self.assertIn(
            'server_target_ack=', server.last_bot_human_hit_reject)
        self.assertTrue(server.should_log_protocol_reject(
            'bot_human_hit', False))
        self.assertFalse(legacy_report(server.report_bot_human_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 1, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertFalse(server.should_log_protocol_reject(
            'bot_human_hit', False))
        server.bot_states[25]['fire_seq'] = 2
        accepted = legacy_report(server.report_bot_human_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 1, 'shot_seq': 2, 'damage': 0,
        })
        self.assertTrue(accepted, server.last_bot_human_hit_reject)
        self.assertFalse(server.should_log_protocol_reject(
            'bot_human_hit', accepted))
        self.assertFalse(legacy_report(server.report_bot_human_hit, {
            'round_id': server.round_id, 'attacker_bot': 25,
            'target': 1, 'shot_seq': 99, 'damage': 1,
        }))
        self.assertTrue(server.should_log_protocol_reject(
            'bot_human_hit', False))


if __name__ == '__main__':
    unittest.main()
