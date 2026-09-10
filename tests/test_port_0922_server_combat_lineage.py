import copy
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest

import bot_state_rows


PORT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' /
                'gui' / 'mods' / 'offline_lan_0922')
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import BattleState, CLIENT_BUILD_0922, Player  # noqa: E402
from effective_params_fixture import (  # noqa: E402
    bot_default_crew_factors, effective_params)


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
        cls._attribute_factors = cls.bot_runtime.loadout.attribute_factors
        cls.bot_runtime.loadout.attribute_factors = bot_default_crew_factors

    @classmethod
    def tearDownClass(cls):
        cls.bot_runtime.loadout.attribute_factors = cls._attribute_factors

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
            'round_id': server.round_id, 'bots': bot_state_rows.bots(manifest)}))
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
        self.assertTrue(server.update_bot_states(1, bot_state_rows.publication({
            'round_id': server.round_id,
            'bots': publication(1, 44, 2, True, 1, 0.1),
            'sample_time_us': 200000,
            'source_batch_horizon_us': 1000000,
        })), server.last_bot_state_reject)
        first_mapped_time_us = server.bot_state_time_us

        self.assertTrue(server.update_bot_states(1, bot_state_rows.publication({
            'round_id': server.round_id,
            'bots': publication(3, 42, 0, False, 3, 0.0),
            'sample_time_us': 1000000,
            'source_batch_horizon_us': 1000000,
        })), server.last_bot_state_reject)

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

    def test_combat_wire_roundtrip_keeps_lineage_and_real_change_guards(self):
        server = BattleState.__new__(BattleState)
        for roster in (['gunner1', 'driver'],
                       ['commander', 'gunner1', 'driver', 'loader1'],
                       ['loader2', 'loader1', 'commander', 'driver']):
            with self.subTest(roster=roster):
                previous = {
                    'id': 11, 'health': 900, 'alive': True,
                    'combat_revision': 3, 'combat_base_revision': 3,
                    'combat_seq': 0, 'combat_ack_seq': 0,
                    'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
                    'stun_end_server_time_ms': 0,
                    'critical': {
                        'devices': [
                            {'name': 'leftTrackHealth', 'hp': 170.0,
                             'max_hp': 170.0, 'state': 'normal'},
                            {'name': 'gunHealth', 'hp': 54.0,
                             'max_hp': 54.0, 'state': 'normal'},
                        ],
                        'destroyed': [], 'crew_ko': [], 'crew_roster': roster,
                        'fire': False, 'ammo_rack_death': False, 'events': [],
                    },
                }
                frozen = copy.deepcopy(previous)
                raw = bot_state_rows.bot_state_codec.decode_row(
                    bot_state_rows.row(previous), {})
                for signature in (server._bot_combat_signature,
                                  self.bot_runtime._combat_signature):
                    self.assertEqual(signature(previous), signature(raw))
                current = copy.deepcopy(raw)
                server._reconcile_modern_bot_combat(raw, previous, current)
                self.assertEqual(3, current['combat_base_revision'])
                self.assertEqual(0, current['combat_ack_seq'])
                self.assertEqual(frozen, previous)

                for changed_field in ('health', 'device_hp', 'crew_ko',
                                      'crew_roster'):
                    with self.subTest(changed_field=changed_field):
                        changed = copy.deepcopy(raw)
                        critical = changed['critical']
                        if changed_field == 'health':
                            changed['health'] -= 1
                        elif changed_field == 'device_hp':
                            critical['devices'][0]['hp'] -= 1.0
                        elif changed_field == 'crew_ko':
                            critical['crew_ko'] = [roster[0]]
                        else:
                            critical['crew_roster'].remove(roster[0])
                        for signature in (server._bot_combat_signature,
                                          self.bot_runtime._combat_signature):
                            self.assertNotEqual(signature(previous),
                                                signature(changed))
                        with self.assertRaisesRegex(
                                ValueError, 'repeated bot combat publication'):
                            server._reconcile_modern_bot_combat(
                                changed, previous, copy.deepcopy(changed))
                        # The same real change is admitted when its owner
                        # advances the publication sequence exactly once.
                        changed['combat_seq'] = 1
                        current = copy.deepcopy(changed)
                        server._reconcile_modern_bot_combat(
                            changed, previous, current)
                        self.assertEqual(1, current['combat_ack_seq'])
                        self.assertEqual(3, current['combat_base_revision'])

    def test_unavailable_projection_preserves_actor_and_recovers(self):
        runtime, server, unused_roster = self._runtime_and_server()
        first = runtime.update(1.0 / 24.0, 1.0)[0]
        self.assertTrue(server.update_bot_states(1, dict(
            first, round_id=server.round_id)), server.last_bot_state_reject)
        previous = copy.deepcopy(server.bot_states[11])
        other_x = server.bot_states[12]['x']
        codec = self.bot_runtime.bot_state_codec
        original = codec.encode_row

        def invalid_pose(state):
            if state['id'] == 11:
                return original(dict(state, shot_yaw=float('nan'),
                                     shot_pitch=0.0))
            if state['id'] == 12:
                return original(dict(state, x=other_x + 3.0))
            return original(state)

        codec.encode_row = invalid_pose
        try:
            failed = runtime.update(1.0 / 24.0, 1.1)[0]
        finally:
            codec.encode_row = original
        self.assertIn([11], failed['rows'])
        self.assertEqual(29, len(failed['rows']))
        self.assertTrue(server.update_bot_states(1, dict(
            failed, round_id=server.round_id)), server.last_bot_state_reject)
        self.assertEqual(previous, server.bot_states[11])
        self.assertEqual(other_x + 3.0, server.bot_states[12]['x'])
        self.assertIsNone(server.battle_result)
        self.assertFalse(runtime.finished)
        recovered = runtime.update(1.0 / 24.0, 1.2)[0]
        self.assertTrue(all(len(row) > 1 for row in recovered['rows']))
        self.assertTrue(server.update_bot_states(1, dict(
            recovered, round_id=server.round_id)), server.last_bot_state_reject)
        self.assertNotIn('_wire_projection_failure', runtime.states[11])

    def test_unavailable_projection_does_not_ack_or_discard_frozen_launch(self):
        runtime, server, unused_roster = self._runtime_and_server()
        frozen = {'id': 11, 'fire_seq': 1, 'shot_yaw': 0.2}
        runtime._pending_launches.append(frozen)
        codec = self.bot_runtime.bot_state_codec
        original = codec.encode_row

        def invalid_pose(state):
            if state['id'] == 11:
                raise codec.BotStateCodecError('shot_yaw: not finite')
            return original(state)

        codec.encode_row = invalid_pose
        try:
            failed = runtime.update(1.0 / 24.0, 1.0)[0]
        finally:
            codec.encode_row = original
        self.assertNotIn('launches', failed)
        self.assertEqual([frozen], runtime._pending_launches)
        recovered = runtime.update(1.0 / 24.0, 1.1)[0]
        self.assertEqual([frozen], recovered['launches'])
        self.assertEqual([frozen], runtime._pending_launches)

    def test_unavailable_projection_retains_contact_barriers_for_both_actors(self):
        runtime, server, unused_roster = self._runtime_and_server()
        reports = [
            {'type': 'bot_ram', 'bot_id': 11, 'target_kind': 'bot',
             'target_id': 12, 'ram_seq': 1},
            {'type': 'bot_ram', 'bot_id': 12, 'target_kind': 'bot',
             'target_id': 11, 'ram_seq': 2},
            {'type': 'bot_ram', 'bot_id': 12, 'target_kind': 'human',
             'target_id': 1, 'ram_seq': 3},
        ]
        runtime._pending_ram_reports.extend(reports)
        runtime._resolve_tank_contacts = lambda *unused: []
        codec = self.bot_runtime.bot_state_codec
        original = codec.encode_row

        def invalid_pose(state):
            if state['id'] == 11:
                raise codec.BotStateCodecError('shot_yaw: not finite')
            return original(state)

        codec.encode_row = invalid_pose
        try:
            outgoing = runtime.update(1.0 / 24.0, 1.0)
        finally:
            codec.encode_row = original
        self.assertEqual([reports[2]], [message for message in outgoing
                                       if message['type'] == 'bot_ram'])
        self.assertEqual(reports[:2], runtime._pending_ram_reports)
        recovered = runtime.update(1.0 / 24.0, 1.1)
        self.assertEqual(reports[:2], [message for message in recovered
                                      if message['type'] == 'bot_ram'])
        self.assertEqual([], runtime._pending_ram_reports)

    def test_unavailable_rows_still_require_unique_manifest_identities(self):
        runtime, server, unused_roster = self._runtime_and_server()
        rows = [[bot_id] for bot_id in sorted(server.bot_states)]
        for invalid in (rows[:-1], rows[:-1] + [rows[0]],
                        rows[:-1] + [[99999]]):
            self.assertFalse(server.update_bot_states(1, {
                'round_id': server.round_id, 'rows': invalid}))
        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id, 'rows': rows}),
            server.last_bot_state_reject)
        self.assertIsNone(server.battle_result)

    def test_external_hit_crew_roster_survives_repeated_publication(self):
        runtime, server, unused_roster = self._runtime_and_server()
        human = _human()
        first = runtime.update(
            1.0 / 24.0, 1.0, players=[human])[0]
        self.assertTrue(server.update_bot_states(1, bot_state_rows.publication({
            'round_id': server.round_id, 'bots': bot_state_rows.bots(first),
            'sample_time_us': first['sample_time_us'],
            'source_batch_horizon_us':
                first['source_batch_horizon_us']})))

        critical = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 170.0,
                'max_hp': 170.0, 'state': 'normal',
            }],
            'destroyed': [], 'crew_ko': [],
            # Descriptor crew order differs from the compact wire mask order.
            'crew_roster': ['gunner1', 'driver'],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        }
        canonical = server.bot_states[11]
        before = server._bot_combat_signature(canonical)
        canonical.update(health=900, display_health=900,
                         critical=dict(critical))
        self.assertTrue(server._commit_external_bot_combat(
            canonical, before))
        external_revision = canonical['combat_base_revision']

        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [dict(server.bot_states[bot_id])
                     for bot_id in sorted(server.bot_states)],
        })
        repeated = runtime.update(
            1.0 / 24.0, 1.1, players=[human])[0]
        wire = next(bot for bot in bot_state_rows.bots(repeated) if bot['id'] == 11)
        self.assertEqual(['driver', 'gunner1'],
                         wire['critical']['crew_roster'])
        self.assertEqual(0, wire['combat_seq'])
        self.assertTrue(server.update_bot_states(1, bot_state_rows.publication({
            'round_id': server.round_id, 'bots': bot_state_rows.bots(repeated),
            'sample_time_us': repeated['sample_time_us'],
            'source_batch_horizon_us':
                repeated['source_batch_horizon_us'],
        })), server.last_bot_state_reject)
        self.assertEqual(external_revision,
                         server.bot_states[11]['combat_base_revision'])
        for frame in range(1, 25):
            runtime.apply_snapshot({
                'server_tick': 2 + frame,
                'bots': [dict(server.bot_states[bot_id])
                         for bot_id in sorted(server.bot_states)],
            })
            publication = runtime.update(
                1.0 / 24.0, 1.1 + frame / 24.0,
                players=[human])[0]
            self.assertTrue(server.update_bot_states(1, bot_state_rows.publication({
                'round_id': server.round_id,
                'bots': bot_state_rows.bots(publication),
                'sample_time_us': publication['sample_time_us'],
                'source_batch_horizon_us':
                    publication['source_batch_horizon_us'],
            })), server.last_bot_state_reject)
            self.assertEqual(external_revision,
                             server.bot_states[11]['combat_base_revision'])
            if server.bot_pending_projectile_launches:
                break
        self.assertTrue(any(
            bot_id != 11 for bot_id, unused_fire_seq in
            server.bot_pending_projectile_launches))

if __name__ == '__main__':
    unittest.main()
