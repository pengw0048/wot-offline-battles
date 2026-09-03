import json
import math
import os
import re
import sys
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))
sys.path.insert(0, str(ROOT / '0.9.22' / 'server'))

from gui.mods.offline_lan_0922.lan_client import (
    HUMAN_RAM_TIMELINE_CAPABILITY, LANClient,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY, MAX_PROJECTILE_ID,
    _strict_projectile_effect, _valid_player_environment_contract,
    project_bot_state)
from gui.mods.offline_lan_0922.authority_worker import (
    AuthorityWorkerLANClient)
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync
from lan_battle_server import (
    BattleState, CLIENT_BUILD_082, CLIENT_BUILD_0922, Player,
    PLAYER_FIRE_INTENT_CAPABILITY, PLAYER_INPUT_FAULT_CLASSES,
    PLAYER_INPUT_FAULT_ENV, PREBATTLE_SECONDS,
    RAM_CONTACT_LEDGER_CAPABILITY, TICK_HZ,
    _bot_combat_log_message, _player_input_fault_class,
    _server_event_log_message, _server_log)
from effective_params_fixture import effective_params


def _player_equipment_contract():
    return {
        'equipment_states': [],
        'equipment_revision': 0,
        'equipment_intent_seq': 0,
        'equipment_intent_result': {
            'intent_seq': 0, 'accepted': False, 'reason': ''},
    }


def _snapshot_player(player_id=1, **changes):
    player = {
        'id': player_id,
        'critical_revision': 0,
        'critical_base_revision': 0,
        'critical_ack_seq': 0,
        'input_seq': 0,
        'up_cosine': 1.0,
        'landing_observation_seq': 0,
    }
    player.update(_player_equipment_contract())
    player.update(changes)
    return player


class _Socket(object):
    def sendall(self, unused_payload):
        pass


class LanProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        self.client.ready = True
        self.client.phase = 'battle'
        self.client.round_id = 7
        self.client.state_revision = 4
        self.client.player_id = 1
        self.client.host_player_id = 1
        self.client.bot_authority_id = -1
        self.client.vehicle_compact_descr = 'dGVzdA=='
        self.client.effective_params = effective_params()
        self.client._published_player_effective_params[1] = \
            effective_params()
        self.sent = []
        def send(message, **unused_options):
            self.sent.append(message)
            return True
        self.client._send = send

    def _worker_client(self):
        worker = AuthorityWorkerLANClient('127.0.0.1', 28782)
        worker.ready = True
        worker.phase = 'battle'
        worker.round_id = self.client.round_id
        worker.bot_authority_id = worker.player_id
        worker._send = self.client._send
        return worker

    def test_v5_explicit_control_messages(self):
        self.assertTrue(self.client.leave_battle())
        critical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        self.assertFalse(self.client.send_hit(
            2, 4, 500, 2, 1, (1, 2, 3), critical,
            critical_target_base_revision=8,
            critical_target_ack_seq=3, hull_damage=120))
        worker = self._worker_client()
        self.assertTrue(worker.send_bot_manifest([{'id': 1}] * 40))
        self.assertTrue(worker.send_bot_state([{
            'id': 1, 'reload_time': 0.5, 'reload_duration': 0.5}]))
        self.assertTrue(worker.send_bot_observation([{}] * 70, [{}] * 20))
        self.assertTrue(worker.send_bot_bot_hit(1, 2, 3, 120, 2))
        self.assertTrue(worker.send_bot_ram(
            1, 'human', 2, 4, 620, 880))
        self.assertTrue(worker.send_rules_state({'1': {'points': 10}}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 17,
            'item_index': 4, 'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.5, 'speed': 12.0, 'is_shot': False}))
        self.assertTrue(worker.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 17,
            'item_index': 4, 'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.5, 'speed': 12.0, 'is_shot': False}))
        self.assertTrue(worker.send_battle_result(1, 'elimination'))
        self.assertEqual('leave_battle', self.sent[0]['type'])
        self.assertEqual(30, len(self.sent[1]['bots']))
        self.assertEqual(64, len(self.sent[3]['contacts']))
        self.assertEqual(1, self.sent[4]['attacker_bot'])
        self.assertEqual('bot_ram_report', self.sent[5]['type'])
        self.assertEqual(620, self.sent[5]['damage_to_bot'])
        self.assertEqual(880, self.sent[5]['damage_to_target'])
        self.assertEqual('rules_state', self.sent[6]['type'])
        self.assertEqual('destructible', self.sent[7]['type'])
        self.assertEqual('tree', self.sent[7]['destructible_kind'])
        self.assertEqual(17, self.sent[7]['chunk_id'])
        self.assertTrue(all(message['round_id'] == 7
                            for message in self.sent))

    def test_worker_bot_state_projects_human_ram_armor_results_exactly(self):
        worker = self._worker_client()
        worker._send_preencoded_trusted = self.client._send
        result = {
            'seq': 7, 'first_id': 1, 'second_id': 2,
            'available': True, 'armor_first': 45.0,
            'armor_second': 80.0,
        }

        self.assertTrue(worker.send_projected_bot_state(
            [], sample_time_us=40000,
            source_batch_horizon_us=40000,
            human_ram_armors=[result]))
        self.assertEqual([result], self.sent[-1]['human_ram_armors'])
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, armor_first=float('nan'))]))
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, unexpected=True)]))
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, first_id=2, second_id=1)]))

    def test_projectile_effect_carries_only_an_exact_stun_end_time(self):
        effect = {
            'target_kind': 'bot', 'target_id': 7,
            'damage': 0, 'shot_result': 2,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'stun_end_server_time_ms': 22000,
        }

        self.assertEqual(effect, _strict_projectile_effect(effect))
        self.assertIsNone(_strict_projectile_effect(dict(
            effect, stun_end_server_time_ms=True)))
        self.assertIsNone(_strict_projectile_effect(dict(
            effect, stun_duration_ms=7000)))

    def test_projectile_effect_target_pose_is_an_atomic_vector(self):
        effect = {
            'target_kind': 'bot', 'target_id': 7,
            'damage': 30, 'shot_result': 2,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'target_x': 4.0, 'target_y': 5.0, 'target_z': 6.0,
        }

        self.assertEqual(effect, _strict_projectile_effect(effect))
        incomplete = dict(effect)
        incomplete.pop('target_z')
        self.assertIsNone(_strict_projectile_effect(incomplete))

    def test_siege_request_is_an_exact_boolean_input_field(self):
        self.assertTrue(self.client.send_input(
            0.0, 0.0, siege_enabled=True))

        self.assertIs(True, self.sent[-1]['siege_enabled'])
        with self.assertRaisesRegex(ValueError, 'BOOL'):
            self.client.send_input(0.0, 0.0, siege_enabled=1)

    def test_input_carries_the_local_hull_pitch_and_roll(self):
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pitch=0.25, roll=-0.3))

        message = self.sent[-1]
        self.assertEqual(0.25, message['pitch'])
        self.assertEqual(-0.3, message['roll'])

    def test_track_repair_is_a_narrow_versioned_message(self):
        self.assertTrue(self.client.send_track_repair([{
            'name': 'leftTrackHealth', 'hp': 25.0,
            'max_hp': 100.0, 'state': 'destroyed',
        }], 4, 2))

        self.assertEqual({
            'type': 'track_repair', 'round_id': 7,
            'critical_base_revision': 4, 'repair_seq': 2,
            'tracks': [{
                'name': 'leftTrackHealth', 'hp': 25.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
        }, self.sent[-1])
        self.assertFalse(self.client.send_track_repair([{
            'name': 'engineHealth', 'hp': 25.0,
            'max_hp': 100.0, 'state': 'destroyed',
        }], 4, 3))
        self.assertFalse(self.client.send_track_repair([{
            'name': 'leftTrackHealth', 'hp': 100.0,
            'max_hp': 100.0, 'state': 'normal',
        }], 4, 3))

    def test_timeline_input_has_round_sequence_and_server_pose_time(self):
        self.client.capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        self.client.server_capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)

        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pose_time_us=123456))
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.5), yaw=0.4,
            pose_time_us=156789))

        self.assertEqual((1, 123456), (
            self.sent[-2]['input_seq'], self.sent[-2]['pose_time_us']))
        self.assertEqual((2, 156789), (
            self.sent[-1]['input_seq'], self.sent[-1]['pose_time_us']))

    def test_input_carries_bounded_ram_contact_ledger(self):
        contacts = [{'seq': value} for value in range(1, 20)]

        self.assertTrue(self.client.send_input(
            0.0, 0.0, ram_contacts=contacts))

        message = self.sent[-1]
        self.assertEqual(list(range(1, 17)), [
            value['seq'] for value in message['ram_contacts']])
        self.assertNotIn('ram_contact', message)

    def test_waiting_room_publishes_one_changed_garage_vehicle(self):
        self.client.phase = 'waiting'
        self.client.vehicle = 'ussr:R11_MS-1'
        self.client.max_health = 90

        self.assertFalse(self.client.select_vehicle('ussr:R11_MS-1', 90))
        self.assertTrue(self.client.select_vehicle(
            'germany:G01_PzI', 150,
            vehicle_compact_descr='cHpp'))

        self.assertEqual({'type': 'select_vehicle',
                          'vehicle': 'germany:G01_PzI',
                          'max_health': 150,
                          'vehicle_compact_descr': 'cHpp',
                          'effective_params': effective_params()},
                         self.sent[-1])
        # Only the server-published roster may retire the pending selection,
        # so a rejected update is resent on the next waiting roster.
        self.assertEqual('ussr:R11_MS-1', self.client.vehicle)

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 8,
            'state_revision': 9, 'phase': 'waiting', 'map': '01_karelia',
            'host_player_id': 1, 'authority_epoch': 0,
            'bot_authority_id': -1,
            'players': [dict(
                _player_equipment_contract(), id=1,
                vehicle='germany:G01_PzI', max_health=150,
                vehicle_compact_descr='cHpp',
                effective_params=effective_params())]})

        self.assertEqual('germany:G01_PzI', self.client.vehicle)
        self.assertEqual(150, self.client.max_health)
        self.assertFalse(self.client.select_vehicle('germany:G01_PzI', 150))

    def test_modern_vehicle_change_rejects_non_exact_health_atomically(self):
        invalid_values = (
            ('missing', None), ('bool', True), ('float', 150.0),
            ('string', '150'), ('zero', 0), ('negative', -1),
            ('overflow', 100001),
        )
        for name, value in invalid_values:
            with self.subTest(name=name):
                state = self._room_with_one_player()
                player = state.players[1]
                message = {
                    'vehicle': 'germany:G01_PzI',
                    'vehicle_compact_descr': 'cHpp',
                    'effective_params': effective_params(),
                }
                if name != 'missing':
                    message['max_health'] = value
                before = (
                    player.vehicle, player.health, player.max_health,
                    dict(player.outfits), player.vehicle_compact_descr,
                    player.siege_state, state.state_revision,
                )

                self.assertFalse(state.select_vehicle(1, message))

                self.assertEqual(before, (
                    player.vehicle, player.health, player.max_health,
                    dict(player.outfits), player.vehicle_compact_descr,
                    player.siege_state, state.state_revision,
                ))

    def test_legacy_vehicle_change_keeps_health_coercion(self):
        state = self._room_with_one_player()
        state.client_build = CLIENT_BUILD_082

        self.assertTrue(state.select_vehicle(1, {
            'vehicle': 'germany:G01_PzI', 'max_health': 150.75,
            'vehicle_compact_descr': 'cHpp',
            'effective_params': effective_params()}))

        self.assertEqual((150, 150), (
            state.players[1].health, state.players[1].max_health))

    def test_vehicle_selection_is_refused_outside_the_waiting_room(self):
        self.client.phase = 'battle'

        self.assertFalse(self.client.select_vehicle('germany:G01_PzI', 150))
        self.assertEqual([], self.sent)

    def test_host_can_set_each_team_size_during_the_waiting_room(self):
        from gui.mods.offline_lan_0922 import lan_client
        self.client.phase = 'waiting'
        self.client.server_capabilities = [
            lan_client.TEAM_SIZE_SELECTION_CAPABILITY]

        self.assertTrue(self.client.set_team_size(1, 4))
        self.assertTrue(self.client.set_team_size(2, 9))

        self.assertEqual({
            'type': 'set_team_size', 'team': 1, 'size': 4}, self.sent[-2])
        self.assertEqual({
            'type': 'set_team_size', 'team': 2, 'size': 9}, self.sent[-1])

    def test_team_size_request_requires_host_waiting_and_capability(self):
        from gui.mods.offline_lan_0922 import lan_client
        self.client.phase = 'waiting'
        self.client.server_capabilities = []
        self.assertFalse(self.client.set_team_size(1, 4))
        self.client.server_capabilities = [
            lan_client.TEAM_SIZE_SELECTION_CAPABILITY]
        self.client.host_player_id = 2
        self.assertFalse(self.client.set_team_size(1, 4))
        self.client.host_player_id = 1
        self.assertFalse(self.client.set_team_size(1, 0))
        self.assertFalse(self.client.set_team_size(3, 4))
        self.assertEqual([], self.sent)

    def test_team_size_denial_adopts_the_server_capacity(self):
        self.client.phase = 'waiting'
        events = []
        self.client.on_event = lambda kind, message: events.append(kind)

        self.client._handle_message({
            'type': 'team_size_denied', 'protocol': 5, 'round_id': 7,
            'state_revision': 5, 'team': 1, 'size': 2,
            'code': 'team_occupied',
            'team_sizes': {'1': 4, '2': 8},
        })

        self.assertEqual({1: 4, 2: 8}, self.client.team_sizes)
        self.assertEqual(['team_size_denied'], events)

    def _room_with_one_player(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.players[1] = Player(
            1, _Socket(), ('127.0.0.1', 1), vehicle='ussr:R11_MS-1',
            team=1, slot=0, health=90, max_health=90,
            vehicle_compact_descr='dGVzdA==',
            effective_params=effective_params())
        return state

    def test_bot_speed_survives_worker_server_and_snapshot_projection(self):
        source = {
            'id': 3, 'x': 1.0, 'y': 0.0, 'z': 2.0, 'yaw': 0.1,
            'pitch': 0.0, 'roll': 0.0, 'aim_yaw': 0.1,
            'gun_pitch': 0.0, 'speed': 6.25,
            'movement_dir': 1, 'rotation_dir': 0,
            'fire_seq': 0, 'health': 500, 'alive': True,
            'reload_time': 0.25, 'reload_duration': 0.5,
        }
        identity = {
            'id': 3, 'team': 1, 'slot': 1, 'name': 'Ally',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 500,
        }

        projected = project_bot_state(source)
        self.assertEqual(6.25, projected['speed'])
        sanitized = BattleState._sanitize_bot_state(
            projected, identity, None)
        self.assertEqual(6.25, sanitized['speed'])

        events = SnapshotSync(
            local_player_id=1, clock=lambda: 10.0).snapshot({
                'round_id': 7, 'server_tick': 1,
                'bot_state_revision': 1,
                'motion_time_us': 100000,
                'bot_state_time_us': 100000,
                'players': [], 'bots': [sanitized],
            })
        update = next(event for event in events
                      if event['type'] == 'update')
        self.assertEqual(6.25, update['state']['speed'])

        for raw, expected in (
                (81.0, 80.0), (-81.0, -80.0),
                (float('nan'), 0.0), (float('inf'), 0.0),
                (float('-inf'), 0.0)):
            bounded = BattleState._sanitize_bot_state(
                dict(projected, speed=raw), identity, None)['speed']
            self.assertTrue(math.isfinite(bounded))
            self.assertEqual(expected, bounded)

    def test_server_applies_a_waiting_room_vehicle_change(self):
        state = self._room_with_one_player()

        self.assertTrue(state.select_vehicle(1, {
            'vehicle': 'germany:G01_PzI', 'max_health': 150,
            'outfits': {}, 'vehicle_compact_descr': 'cHpp',
            'effective_params': effective_params()}))

        player = state.players[1]
        self.assertEqual('germany:G01_PzI', player.vehicle)
        self.assertEqual(150, player.max_health)
        self.assertEqual(150, player.health)
        self.assertFalse(state.select_vehicle(1, {
            'vehicle': 'germany:G01_PzI', 'max_health': 150,
            'outfits': {}, 'vehicle_compact_descr': 'cHpp',
            'effective_params': effective_params()}))

    def test_server_keeps_the_round_vehicle_once_the_battle_started(self):
        state = self._room_with_one_player()
        state.phase = 'battle'

        self.assertFalse(state.select_vehicle(1, {
            'vehicle': 'germany:G01_PzI', 'max_health': 150}))
        self.assertEqual('ussr:R11_MS-1', state.players[1].vehicle)
        self.assertEqual(90, state.players[1].max_health)

    def test_bot_combat_log_fields_explain_friendly_ram(self):
        players = {
            1: Player(1, None, ('127.0.0.1', 1), team=1, slot=0),
        }
        bots = {
            3: {'id': 3, 'team': 1},
            4: {'id': 4, 'team': 1},
            28: {'id': 28, 'team': 2},
        }

        self.assertEqual(
            'BOT COMBAT kind=bot_human_hit source=ram attacker=3 '
            'attacker_team=1 target=1 target_team=1 damage=27 '
            'health=853 dead=False', _bot_combat_log_message({
            'kind': 'bot_human_hit', 'source': 'ram',
            'attacker_bot': 3, 'target': 1,
            'damage': 27, 'health': 853, 'dead': False,
        }, players, bots))
        self.assertEqual(
            'BOT COMBAT kind=bot_bot_hit source=ram attacker=28 '
            'attacker_team=2 target=3 target_team=1 damage=14 '
            'health=806 dead=False', _bot_combat_log_message({
            'kind': 'bot_bot_hit', 'source': 'ram',
            'attacker_bot': 28, 'target_bot': 3,
            'damage': 14, 'health': 806, 'dead': False,
        }, players, bots))

    def test_server_event_log_omits_routine_simulation_noise(self):
        players = {
            1: Player(1, None, ('127.0.0.1', 1), team=1, slot=0),
        }
        bots = {
            3: {'id': 3, 'team': 1},
            4: {'id': 4, 'team': 1},
            28: {'id': 28, 'team': 2},
        }

        for event in (
                {'kind': 'destructible', 'destructible_kind': 'tree'},
                {'kind': 'health', 'target': 1, 'damage': 0,
                 'health': 850, 'dead': False,
                 'source': 'client_simulation'},
                {'kind': 'bot_bot_hit', 'attacker_bot': 28,
                 'target_bot': 3, 'damage': 14, 'health': 806,
                 'dead': False, 'source': 'shot'},
                {'kind': 'projectile_impact', 'shooter_kind': 'bot',
                 'projectile_id': '1:b:28:1'}):
            self.assertIsNone(
                _server_event_log_message(event, players, bots))

        self.assertIn('kind=bot_human_hit', _server_event_log_message({
            'kind': 'bot_human_hit', 'attacker_bot': 3, 'target': 1,
            'damage': 0, 'health': 850, 'dead': False, 'source': 'shot',
        }, players, bots))
        self.assertIn('attacker_team=1', _server_event_log_message({
            'kind': 'bot_bot_hit', 'attacker_bot': 3,
            'target_bot': 4, 'damage': 14, 'health': 806,
            'dead': False, 'source': 'shot',
        }, players, bots))
        self.assertIn('source=ram', _server_event_log_message({
            'kind': 'bot_bot_hit', 'attacker_bot': 28,
            'target_bot': 3, 'damage': 14, 'health': 806,
            'dead': False, 'source': 'ram',
        }, players, bots))
        self.assertIn('attacker_team=None', _server_event_log_message({
            'kind': 'bot_bot_hit', 'attacker_bot': 99,
            'target_bot': 4, 'damage': 14, 'health': 806,
            'dead': False, 'source': 'shot',
        }, players, bots))
        self.assertIn('source=shot', _server_event_log_message({
            'kind': 'health', 'target': 1, 'damage': 0,
            'health': 850, 'dead': False, 'source': 'shot',
        }, players, bots))
        self.assertIn('source=client_simulation',
                      _server_event_log_message({
                          'kind': 'health', 'target': 1, 'damage': None,
                          'health': 850, 'dead': False,
                          'source': 'client_simulation',
                      }, players, bots))
        self.assertEqual(
            'PROJECTILE TERMINAL id=1:p:1:4 outcome=impact elapsed_ms=117',
            _server_event_log_message({
                'kind': 'projectile_impact', 'shooter_kind': 'player',
                'projectile_id': '1:p:1:4', 'outcome': 'impact',
                'resolved_time_ms': 117,
            }, players, bots))
        self.assertEqual(
            'BATTLE RESULT winner=1 reason=base_captured base_team=2',
            _server_event_log_message({
                'kind': 'battle_result', 'winner': 1,
                'reason': 'base_captured', 'base_team': 2,
            }, players, bots))

    def test_server_log_writes_each_line_atomically(self):
        output = mock.Mock()
        with mock.patch('lan_battle_server.sys.stdout', output):
            _server_log('battle lifecycle')

        output.write.assert_called_once()
        self.assertTrue(
            output.write.call_args[0][0].endswith(
                '] battle lifecycle\n'))
        output.flush.assert_called_once_with()

    def test_critical_hit_requires_exact_target_contract(self):
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        self.assertFalse(
            self.client.send_hit(2, 1, 100, 2, critical=critical))
        worker = self._worker_client()
        with self.assertRaises(ValueError):
            worker.send_bot_hit(
                2, 1, 100, 2, critical=critical,
                critical_target_base_revision=True,
                critical_target_ack_seq=0, hull_damage=100)
        with self.assertRaises(ValueError):
            worker.send_bot_human_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0.5, hull_damage=100)
        with self.assertRaises(ValueError):
            worker.send_bot_bot_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0, hull_damage=-1)
        self.assertEqual([], self.sent)

    def test_assist_event_and_result_statistics_are_json_safe(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        for player_id, team in ((1, 1), (2, 2), (3, 1)):
            state.players[player_id] = Player(
                player_id, _Socket(), ('127.0.0.1', player_id), team=team)
        tracked = {
            'devices': [{'name': 'rightTrackHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['rightTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        state.track_immobilisers[('player', 2)] = ('player', 1)
        state.player_spotted[1] = frozenset([('player', 2)])
        state._record_damage(('player', 3), ('player', 2), 240, tracked)
        self.assertTrue(state._finish_battle(1, 'elimination'))

        self.assertEqual(
            ['track', 'radio'],
            [event['category'] for event in state.pending_events
             if event['kind'] == 'assist'])
        event = state.pending_events[0]
        self.assertEqual({
            'kind': 'assist', 'category': 'track',
            'assister_kind': 'player', 'assister_id': 1,
            'attacker_kind': 'player', 'attacker_id': 3,
            'target_kind': 'player', 'target_id': 2,
            'damage': 240,
        }, json.loads(json.dumps(event)))
        result = state.battle_result
        self.assertEqual(result, json.loads(json.dumps(result)))
        rows = dict((row['actor_id'], row)
                    for row in result['vehicle_statistics'])
        self.assertEqual({
            'actor_kind', 'actor_id', 'team', 'shots_fired', 'shots_hit',
            'shots_penetrated', 'damage_dealt', 'damage_received',
            'damage_blocked', 'damage_assisted_track',
            'damage_assisted_radio', 'damage_assisted_stun',
            'kills'}, set(rows[1]))
        for row in rows.values():
            self.assertTrue(all(key == key.lower() and key.isidentifier()
                                for key in row))
        self.assertEqual(240, rows[1]['damage_assisted_track'])
        self.assertEqual(240, rows[1]['damage_assisted_radio'])
        self.assertEqual(240, rows[3]['damage_dealt'])
        self.assertEqual(240, rows[2]['damage_received'])

    def test_battle_result_omits_statistics_for_the_0_8_2_build(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_082
        self.assertTrue(state._finish_battle(1, 'elimination'))
        self.assertNotIn('vehicle_statistics', state.battle_result)

    def test_destructible_report_requires_exact_identity_fields(self):
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1.5,
            'item_index': 2}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'unknown', 'chunk_id': 1,
            'item_index': 2}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1,
            'item_index': 2, 'x': 0, 'y': 0, 'z': 0,
            'fall_yaw': 0, 'speed': 0}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1,
            'item_index': 2, 'x': 0, 'y': 0, 'z': 0,
            'fall_yaw': 0, 'speed': 0, 'is_shot': 0}))

    def test_input_has_no_client_damage_verdict_fields(self):
        self.assertTrue(self.client.send_input(0, 0, speed=-12.5))

        message = self.sent[-1]
        self.assertFalse(any(
            key.startswith('reported_') for key in message))
        self.assertEqual(-12.5, message['speed'])
        with self.assertRaises(TypeError):
            self.client.send_input(0, 0, reported_health=0)

    def test_only_authority_can_send_bot_or_rule_messages(self):
        self.client.bot_authority_id = 2
        self.assertFalse(self.client.send_bot_manifest([{'id': 1}]))
        self.assertFalse(self.client.send_bot_state([{'id': 1}]))
        self.assertFalse(self.client.send_bot_observation([{}]))
        self.assertFalse(self.client.send_bot_human_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_bot_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_ram(
            1, 'human', 2, 1, 20, 80))
        self.assertFalse(self.client.send_rules_state({}))
        self.assertFalse(self.client.send_battle_result(1, 'elimination'))
        self.assertFalse(self.client.send_bot_hit(1, 1, 100, 2))
        self.assertEqual([], self.sent)

    def test_failed_fire_send_does_not_create_sequence_gap(self):
        launch = {
            'position': [1.0, 2.0, 3.0],
            'velocity': [900.0, 0.0, 0.0],
            'gravity': 9.81,
            'max_distance': 720.0,
            'max_time_ms': 20000,
            'source_shot': {
                'speed': 900.0, 'gravity': 9.81,
                'maxDistance': 720.0,
                'piercingPower': [220.0, 200.0],
                'deadeye': False,
                'shell': {
                    'kind': 'ARMOR_PIERCING', 'caliber': 105.0,
                    'damage': [390.0, 150.0],
                    'explosionRadius': 0.0,
                },
            },
        }
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.0,
            shell_index=0, pose_time_us=123456))
        self.client._send = lambda unused_message: False

        self.assertIsNone(self.client.send_fire(**launch))
        self.assertEqual(0, self.client._fire_intent_seq)

        self.client._send = lambda message: self.sent.append(message) or True
        self.assertEqual(1, self.client.send_fire(**launch))
        self.assertEqual({
            'type': 'fire_intent', 'round_id': 7,
            'intent_seq': 1, 'input_seq': 1, 'shell_index': 0,
            'shot_origin': [1.0, 2.0, 3.0],
            'shot_direction': [1.0, 0.0, 0.0],
            'dispersion_angle': 0.0,
        }, self.sent[-1])
        self.assertFalse(any(field in self.sent[-1]
                             for field in ('position', 'origin', 'velocity',
                                           'gravity', 'source_shot',
                                           'damage')))

    def test_worker_sends_hello_before_exposing_connected_socket(self):
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            effective_params=effective_params())
        sent = []
        outer = self

        class FakeSocket(object):
            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

            def sendall(self, payload):
                outer.assertFalse(client.connected)
                sent.append(json.loads(payload.decode('utf-8')))

            def recv(self, unused_size):
                return b''

            def close(self):
                pass

        import gui.mods.offline_lan_0922.lan_client as lan_client_module
        original_socket = lan_client_module.socket.socket
        lan_client_module.socket.socket = lambda *unused_args: FakeSocket()
        client.running = True
        try:
            client._worker()
        finally:
            lan_client_module.socket.socket = original_socket

        self.assertEqual('hello', sent[0]['type'])
        self.assertEqual(5, sent[0]['protocol'])
        self.assertEqual('wot-0.9.22.0.1-cn-1513',
                         sent[0]['client_build'])

    def test_pong_uses_network_receive_time_before_main_thread_delay(self):
        self.client.rtt_ms = None
        self.client._handle_message({
            'type': 'pong', 'client_time': 10.0,
            '_client_received_time': 10.025,
        })

        self.assertAlmostEqual(25.0, self.client.rtt_ms, places=3)

    def test_bot_observation_is_validated_and_stale_round_is_ignored(self):
        received = []
        self.client.on_event = (
            lambda kind, message: received.append((kind, message)))
        message = {
            'type': 'bot_observation', 'protocol': 5, 'round_id': 7,
            'contacts': [{
                'observing_team': 2, 'target_kind': 'human',
                'target_id': 1, 'target_team': 1, 'visible': True,
                'fresh': True, 'time_left': 10.0,
                'visible_by_bot_ids': [11],
                'visible_by_player_ids': [],
                'shootable_by_bot_ids': [],
            }],
        }

        self.client._handle_message(message)
        self.assertEqual(['bot_observation'],
                         [kind for kind, unused in received])

        stale = dict(message, round_id=6)
        self.client._handle_message(stale)
        self.assertEqual(1, len(received))
        self.client._handle_message(dict(stale, protocol='obsolete'))
        self.assertTrue(self.client.ready)
        self.assertIsNone(self.client.last_error)

        malformed = dict(message)
        malformed['contacts'] = [dict(
            message['contacts'][0], visible=1)]
        self.client.running = True
        self.client._handle_message(malformed)
        self.assertTrue(self.client.running)
        self.assertTrue(self.client.ready)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(1, len(received))

    def test_server_timing_projects_receive_time_and_half_rtt(self):
        self.client.rtt_ms = 100.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertAlmostEqual(114.95, self.client.combat_deadline, places=3)
        self.assertAlmostEqual(
            1014.95, self.client.combat_end_deadline, places=3)
        self.assertEqual('prebattle', self.client.combat_phase)

    def test_server_timing_rejects_inconsistent_payload(self):
        self.assertFalse(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900001, 'duration_ms': 900000}}))

    def test_invalid_snapshot_timing_keeps_state_and_old_clock(self):
        self.client.running = True
        message = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [_snapshot_player()], 'bots': [],
            'timing': {
                'phase': 'battle', 'start_in_ms': 0,
                'remaining_ms': 900001, 'duration_ms': 900000},
        }

        self.client._handle_message(message)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(1, self.client.last_snapshot['server_tick'])
        self.assertNotIn('timing', self.client.last_snapshot)
        self.assertEqual(-1, self.client._combat_timing_tick)

    def test_future_runtime_round_still_fails_lineage(self):
        self.client.running = True
        self.client._handle_message({
            'type': 'events', 'protocol': 5, 'round_id': 8,
            'server_tick': 1, 'events': [],
        })

        self.assertFalse(self.client.running)
        self.assertEqual('invalid events message', self.client.last_error)

    def test_current_runtime_message_without_protocol_is_recoverable(self):
        self.client.running = True
        message = {
            'type': 'snapshot', 'round_id': 7, 'server_tick': 1,
            'players': [], 'bots': [],
        }
        self.client._handle_message(message)
        self.client._handle_message(message)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIsNone(self.client.last_snapshot)
        self.assertEqual(
            1, self.client._runtime_drop_diagnostics['snapshot'][1])

    def test_negotiated_runtime_protocol_label_is_informational(self):
        self.client.running = True
        self.client._schema_negotiated = True
        self.client._handle_message({
            'type': 'events', 'protocol': 4, 'round_id': 7,
            'server_tick': 1, 'events': [],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)

    def test_invalid_runtime_protocol_marker_soft_drops_one_message(self):
        self.client.running = True
        self.client._schema_negotiated = True
        self.client._handle_message({
            'type': 'events', 'protocol': 'invalid', 'round_id': 7,
            'server_tick': 1, 'events': [],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIn('events', self.client._runtime_drop_diagnostics)

    def test_snapshot_missing_bot_combat_contract_keeps_last_good_state(self):
        self.client.running = True
        self.client._handle_message({
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1,
            'players': [{
                'id': 1, 'critical_revision': 0,
                'critical_base_revision': 0, 'critical_ack_seq': 0}],
            'bots': [{'id': 11, 'health': 500, 'alive': True}],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual('battle', self.client.phase)
        self.assertIsNone(self.client.last_snapshot)

    def test_lean_snapshot_inherits_the_last_static_bot_manifest(self):
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        player = _snapshot_player()
        manifest = [{'id': 11, 'vehicle': 'ussr:R11_MS-1'}]
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': manifest,
            'bot_authority_id': -1,
        }
        second = dict(first, server_tick=2)
        second.pop('bot_manifest')

        self.client._handle_message(first)
        self.client._handle_message(second)

        self.assertEqual(manifest, self.client.last_snapshot['bot_manifest'])
        self.assertIsNot(
            manifest, self.client.last_snapshot['bot_manifest'])

    def test_poll_does_not_coalesce_away_manifest_barrier(self):
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        self.client.running = True
        self.client.connected = False
        self.client.bigworld = mock.Mock()
        self.client.bigworld.callback.return_value = 1
        player = _snapshot_player()
        manifest = [{'id': 11, 'vehicle': 'ussr:R11_MS-1'}]
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': manifest,
            'bot_authority_id': -1, 'authority_epoch': 1,
        }
        second = dict(first, server_tick=3)
        second.pop('bot_manifest')
        with self.client._pending_lock:
            self.client._pending = [first, second]

        self.client._poll()

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(3, self.client.last_snapshot['server_tick'])
        self.assertEqual(manifest, self.client.last_snapshot['bot_manifest'])

    def test_lean_snapshot_requires_server_capability(self):
        self.client.running = True
        player = _snapshot_player()
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': [],
            'bot_authority_id': -1, 'authority_epoch': 1,
        }
        second = dict(first, server_tick=2)
        second.pop('bot_manifest')

        self.client._handle_message(first)
        accepted = self.client.last_snapshot
        self.client._handle_message(second)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIs(accepted, self.client.last_snapshot)

    def test_lean_snapshot_requires_existing_same_lineage_manifest(self):
        self.client.running = True
        player = _snapshot_player()
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': [],
            'bot_authority_id': -1, 'authority_epoch': 1,
        }
        changed = dict(
            first, server_tick=2, bot_authority_id=0,
            authority_epoch=2)
        changed.pop('bot_manifest')

        self.client._handle_message(first)
        self.client._handle_message(changed)

        self.assertEqual('invalid snapshot message', self.client.last_error)
        self.assertEqual('disconnected', self.client.phase)

        missing = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1')
        missing.running = True
        missing.ready = True
        missing.phase = 'battle'
        missing.round_id = 7
        missing.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        first.pop('bot_manifest')
        missing._handle_message(first)
        self.assertTrue(missing.running)
        self.assertIsNone(missing.last_error)
        self.assertIsNone(missing.last_snapshot)

    def test_snapshot_drops_non_exact_bot_combat_revisions(self):
        cases = (
            ('combat_revision', True),
            ('combat_base_revision', True),
            ('combat_ack_seq', True),
            ('combat_base_revision', 2),
            ('combat_ack_seq', -1),
            ('combat_fire_elapsed', float('nan')),
            ('combat_fire_elapsed', -0.1),
            ('combat_fire_elapsed', 10.1),
            ('combat_fire_timer', True),
            ('combat_fire_timer', 1.0),
        )
        for field, value in cases:
            client = LANClient(
                '127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': {},
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            bot[field] = value

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0, 'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertTrue(client.running, '%s=%r' % (field, value))
            self.assertIsNone(client.last_error, '%s=%r' % (field, value))
            self.assertEqual('battle', client.phase)
            self.assertIsNone(client.last_snapshot)

    def test_snapshot_drops_missing_or_non_object_bot_critical(self):
        for critical in (None, [], 'broken'):
            client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': critical,
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            if critical is None:
                bot.pop('critical')

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0,
                    'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertTrue(client.running)
            self.assertIsNone(client.last_error)
            self.assertEqual('battle', client.phase)
            self.assertIsNone(client.last_snapshot)

    def test_loading_ready_and_battle_live_form_one_transition(self):
        self.client.phase = 'loading'
        bases = {'1': [(-10.0, -20.0)], '2': [(10.0, 20.0)]}
        self.assertTrue(self.client.send_battle_ready(bases))
        self.assertEqual('battle_ready', self.sent[-1]['type'])
        self.assertEqual(bases, self.sent[-1]['bases'])

        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0,
            'bot_authority_id': -1,
            'state_revision': 5, 'countdown_seconds': 30.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 30000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.assertEqual('battle', self.client.phase)

    def test_older_timing_cannot_rewind_a_newer_snapshot_deadline(self):
        self.client.phase = 'loading'
        self.client.rtt_ms = 0.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 1,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 14967,
                'remaining_ms': 900000, 'duration_ms': 900000}}))
        deadline = self.client.combat_deadline

        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.5,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertEqual(deadline, self.client.combat_deadline)
        self.assertEqual(1, self.client._combat_timing_tick)

    def test_newer_roster_revision_cannot_swallow_first_battle_live(self):
        events = []
        self.client.on_event = lambda kind, message: events.append(kind)
        self.client.phase = 'loading'
        self.client.state_revision = 5
        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': -1,
            'players': [dict(
                _player_equipment_contract(), id=1,
                effective_params=effective_params())]})
        live = {
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'bot_authority_id': -1,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}

        self.client._handle_message(live)
        self.client._handle_message(live)

        self.assertEqual('battle', self.client.phase)
        self.assertEqual(7, self.client.state_revision)
        self.assertEqual(7, self.client._battle_live_round_id)
        self.assertAlmostEqual(115.0, self.client.combat_deadline)
        self.assertEqual(['roster', 'battle_live'], events)

    def test_visible_client_rejects_player_authority(self):
        self.client.player_id = 2
        self.client.phase = 'waiting'
        players = [
            {'id': 1, 'team': 1, 'slot': 0, 'name': 'Failed',
             'vehicle': 'ussr:MS-1', 'x': 0, 'y': 0, 'z': 0},
            {'id': 2, 'team': 2, 'slot': 0, 'name': 'Survivor',
             'vehicle': 'ussr:MS-1', 'x': 1, 'y': 0, 'z': 1},
        ]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': 1, 'players': players})
        self.assertEqual('waiting', self.client.phase)
        self.assertEqual('invalid battle_start message',
                         self.client.last_error)
        self.assertFalse(self.client.is_bot_authority())

    def test_visible_client_accepts_only_explicit_worker_failure_without_id(self):
        self.client.authority_epoch = 1

        self.client._handle_message({
            'type': 'events', 'protocol': 5, 'round_id': 7,
            'server_tick': 8, 'authority_epoch': 2,
            'bot_authority_id': None,
            'worker_status': 'failed',
            'worker_failure_reason': 'worker_disconnected',
            'events': [{
                'kind': 'authority', 'player_id': None,
                'authority_epoch': 2,
            }],
        })

        self.assertIsNone(self.client.last_error)
        self.assertIsNone(self.client.bot_authority_id)
        self.assertEqual(2, self.client.authority_epoch)
        self.assertFalse(self.client.is_bot_authority())

    def test_same_round_waiting_roster_cannot_demote_accepted_battle(self):
        players = [dict(
            _player_equipment_contract(), id=1, team=1, slot=0, name='P',
            vehicle='ussr:MS-1', x=0, y=0, z=0,
            effective_params=effective_params())]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5,
            'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': -1,
            'players': players})
        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'bot_authority_id': -1,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7,
            'phase': 'waiting', 'map': '05_prohorovka',
            'bot_authority_id': -1,
            'host_player_id': 1, 'players': [dict(
                _player_equipment_contract(), id=1, team=1, slot=0,
                name='Changed', vehicle='ussr:MS-1', x=1, y=0, z=1,
                effective_params=effective_params())]})

        self.assertEqual('battle', self.client.phase)
        self.assertEqual('01_karelia', self.client.map_name)
        self.assertEqual(players, self.client.roster)

if __name__ == '__main__':
    unittest.main()


class ShippingClientInputContractTests(unittest.TestCase):
    """Frames built by the shipping client must satisfy the real validator.

    The launcher always installs the matching client/server pair, so any frame
    ``LANClient.send_input`` queues has to pass the bundled server's
    pre-admission validation.  Anything that does not is a contract mismatch
    that would show up in play as a stream of rejected input.
    """

    def setUp(self):
        self.client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        self.client.ready = True
        self.client.phase = 'battle'
        self.client.player_id = 1
        self.client.host_player_id = 1
        self.client.bot_authority_id = -1
        self.client.capabilities = (
            HUMAN_RAM_TIMELINE_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
            RAM_CONTACT_LEDGER_CAPABILITY)
        self.client.server_capabilities = self.client.capabilities
        self.sent = []
        self.client._send = lambda message, **unused: (
            self.sent.append(message) or True)
        self.state = BattleState(map_name='04_himmelsdorf')
        self.state.client_build = CLIENT_BUILD_0922
        self.state.phase = 'battle'
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        self.player = Player(
            1, _Socket(), ('127.0.0.1', 1), team=1, slot=0,
            client_position=True,
            capabilities=self.client.capabilities,
            effective_params=effective_params())
        self.state.players[1] = self.player
        self.client.round_id = self.state.round_id

    def _checkpoint(self):
        return {
            'reload_time': 0.0, 'reload_duration': 5.0,
            'clip': 1, 'clip_size': 1, 'dispersion': 0.02,
        }

    def _send(self, **changes):
        keyword_args = {
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'pitch': 0.0,
            'roll': 0.0,
            'up_cosine': 1.0,
            'speed': 0.0,
            'shell_index': 0,
            'next_shell_index': 0,
            'shell_change_pending': False,
            'gun_checkpoint': self._checkpoint(),
            'pose_time_us': self.state._logical_motion_time_us(),
        }
        keyword_args.update(changes)
        forward = keyword_args.pop('forward', 0.0)
        turn = keyword_args.pop('turn', 0.0)
        aim_yaw = keyword_args.pop('aim_yaw', 0.0)
        gun_pitch = keyword_args.pop('gun_pitch', 0.0)
        return self.client.send_input(
            forward, turn, aim_yaw, gun_pitch, **keyword_args)

    def test_natural_client_frames_are_admitted_by_the_server(self):
        # Angle wrap boundaries, full control range, both shell states, siege
        # and contact rows: everything a real driving session produces.
        cases = (
            ('origin', {}),
            ('wrapped_aim', {'aim_yaw': 3.5, 'yaw': 3.5}),
            ('wrapped_aim_negative', {'aim_yaw': -3.5, 'yaw': -3.5}),
            ('turret_plus_hull', {'aim_yaw': math.pi + math.pi}),
            ('many_turns', {'aim_yaw': 40.0 * math.pi + 0.25}),
            ('pi_seam', {'aim_yaw': math.pi, 'yaw': -math.pi}),
            ('full_throttle', {'forward': 1.0, 'turn': -1.0, 'speed': 25.0}),
            ('reverse', {'forward': -1.0, 'turn': 1.0, 'speed': -12.5}),
            ('steep_hull', {'pitch': 0.6, 'roll': -0.6, 'up_cosine': 0.5}),
            ('steep_hull_beyond', {'pitch': 1.4, 'roll': -1.4}),
            ('gun_up', {'gun_pitch': -0.4}),
            ('gun_down', {'gun_pitch': 0.25}),
            ('gun_beyond_envelope', {'gun_pitch': -math.pi / 2.0}),
            ('shell_change', {
                'shell_index': 0, 'next_shell_index': 2,
                'shell_change_pending': True}),
            ('last_shell', {
                'shell_index': 9, 'next_shell_index': 9,
                'shell_change_pending': False}),
            ('siege_on', {'siege_enabled': True}),
            ('siege_off', {'siege_enabled': False}),
            ('fire_seq', {'fire_seq': 12}),
            ('clip_gun', {'gun_checkpoint': {
                'reload_time': 2.5, 'reload_duration': 5.0,
                'clip': 3, 'clip_size': 6, 'dispersion': 0.04}}),
            ('empty_clip', {'gun_checkpoint': {
                'reload_time': 5.0, 'reload_duration': 5.0,
                'clip': 0, 'clip_size': 1, 'dispersion': 0.5}}),
            ('map_corner', {'position': (-999.0, -80.0, 999.0)}),
            ('ram_rows', {'ram_contacts': [{'seq': value}
                                           for value in range(1, 20)]}),
            ('destructible_rows', {
                'destructible_contacts': [{'seq': value}
                                          for value in range(1, 20)]}),
        )
        for name, changes in cases:
            with self.subTest(frame=name):
                self.assertTrue(self._send(**changes))
                message = self.sent[-1]
                self.assertEqual(
                    self.player.input_processed_seq + 1,
                    message['input_seq'])
                # The server's own pre-admission validator, not a copy of it.
                self.assertEqual(
                    ('', ''),
                    self.state._player_input_frame_failure(
                        self.player, message, False)[0])
                self.assertTrue(self.state.update_input(1, message))
                self.assertEqual(
                    message['input_seq'], self.player.input_seq)

    def test_periodic_angles_are_normalized_not_clipped(self):
        for raw in (3.5, -3.5, 7.0, -7.0, 40.0 * math.pi + 0.25,
                    math.pi, -math.pi):
            with self.subTest(aim_yaw=raw):
                self.assertTrue(self._send(aim_yaw=raw, yaw=raw))
                message = self.sent[-1]
                for name in ('aim_yaw', 'yaw'):
                    value = message[name]
                    self.assertLessEqual(abs(value), math.pi + 1e-9)
                    # Mathematically the same orientation, not an endpoint.
                    difference = (raw - value) / (2.0 * math.pi)
                    self.assertAlmostEqual(
                        difference, round(difference), places=9)

    def test_an_out_of_world_pose_is_dropped_without_using_a_sequence(self):
        self.assertTrue(self._send())
        self.assertTrue(self.state.update_input(1, self.sent[-1]))
        committed = self.client._input_seq

        for position in ((3000.0, 0.0, 0.0), (0.0, 2000.0, 0.0),
                         (0.0, 0.0, -3000.0)):
            with self.subTest(position=position):
                self.assertFalse(self._send(position=position))
                self.assertEqual(committed, self.client._input_seq)

        # The dropped frames consumed no identifier, so the next frame is
        # still the server's exact next eligible sequence.
        self.assertTrue(self._send())
        self.assertEqual(committed + 1, self.client._input_seq)
        self.assertTrue(self.state.update_input(1, self.sent[-1]))
        self.assertEqual(committed + 1, self.player.input_seq)

    def test_an_out_of_range_fire_sequence_is_dropped_locally(self):
        self.assertTrue(self._send())
        committed = self.client._input_seq

        self.assertFalse(self._send(fire_seq=MAX_PROJECTILE_ID + 1))

        self.assertEqual(committed, self.client._input_seq)

    def test_an_exhausted_input_sequence_is_not_queued_or_advanced(self):
        self.client._input_seq_round = self.client.round_id
        self.client._input_seq = MAX_PROJECTILE_ID
        sent = len(self.sent)

        self.assertFalse(self._send())

        self.assertEqual(sent, len(self.sent))
        self.assertEqual(MAX_PROJECTILE_ID, self.client._input_seq)

    def test_the_client_resumes_from_the_server_terminal_frontier(self):
        self.client.round_id = 7
        self.client._input_seq_round = 7
        self.client._input_seq = 4
        self.client._landing_observation_round = 7

        # Applied 4, terminal 6: two later frames reached a terminal decision
        # the client has not folded yet.
        self.assertTrue(self.client._adopt_player_input_frontier([
            _snapshot_player(input_seq=4, input_processed_seq=6)]))
        self.assertEqual(6, self.client._input_seq)

        # A frontier that contradicts the applied sequence is not adopted.
        self.assertFalse(self.client._adopt_player_input_frontier([
            _snapshot_player(input_seq=8, input_processed_seq=6)]))

    def test_snapshot_validation_covers_the_terminal_frontier_relation(self):
        self.assertTrue(_valid_player_environment_contract(
            _snapshot_player(input_seq=4), required=True))
        self.assertTrue(_valid_player_environment_contract(
            _snapshot_player(input_seq=4, input_processed_seq=6),
            required=True))
        self.assertFalse(_valid_player_environment_contract(
            _snapshot_player(input_seq=4, input_processed_seq=3),
            required=True))
        self.assertFalse(_valid_player_environment_contract(
            _snapshot_player(input_seq=4, input_processed_seq=True),
            required=True))

    def test_the_server_publishes_both_input_frontiers(self):
        self.assertTrue(self._send())
        self.assertTrue(self.state.update_input(1, self.sent[-1]))
        self.assertFalse(self.state.update_input(1, dict(
            self.sent[-1], input_seq=2, health=0)))

        public = self.state._public_player(self.player)

        self.assertEqual(1, public['input_seq'])
        self.assertEqual(2, public['input_processed_seq'])


class InputFaultInjectionTests(unittest.TestCase):
    """The Windows acceptance hook must use the production validator."""

    def setUp(self):
        self.state = BattleState(map_name='04_himmelsdorf')
        self.state.client_build = CLIENT_BUILD_0922
        self.state.phase = 'battle'
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        self.player = Player(
            1, _Socket(), ('127.0.0.1', 1), team=1, slot=0,
            client_position=True,
            capabilities=(
                HUMAN_RAM_TIMELINE_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY),
            effective_params=effective_params())
        self.state.players[1] = self.player

    def _frame(self):
        player = self.player
        return {
            'type': 'input', 'round_id': self.state.round_id,
            'input_seq': player.input_processed_seq + 1,
            'pose_time_us': self.state._logical_motion_time_us(),
            'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0,
            'x': player.x, 'y': player.y, 'z': player.z,
            'yaw': player.yaw, 'pitch': 0.0, 'roll': 0.0,
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
            'gun_checkpoint': {
                'reload_time': 0.0, 'reload_duration': 5.0,
                'clip': 1, 'clip_size': 1, 'dispersion': 0.02,
            },
        }

    def test_the_hook_is_inert_unless_a_class_is_armed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PLAYER_INPUT_FAULT_ENV, None)
            self.assertEqual('', _player_input_fault_class())
            self.assertTrue(self.state.update_input(1, self._frame()))
            self.assertEqual(1, self.player.input_seq)

    def test_an_unknown_class_is_ignored(self):
        with mock.patch.dict(
                os.environ, {PLAYER_INPUT_FAULT_ENV: 'not_a_class'}):
            self.assertEqual('', _player_input_fault_class())
            self.assertTrue(self.state.update_input(1, self._frame()))

    def test_one_armed_class_breaks_exactly_one_frame_per_round(self):
        for fault_class in sorted(PLAYER_INPUT_FAULT_CLASSES):
            with self.subTest(fault=fault_class):
                self.setUp()
                self.assertTrue(self.state.update_input(1, self._frame()))
                with mock.patch.dict(
                        os.environ,
                        {PLAYER_INPUT_FAULT_ENV: fault_class}):
                    self.assertFalse(
                        self.state.update_input(1, self._frame()))
                    # It fails production validation, so the terminal frontier
                    # advances and nothing was applied.
                    self.assertEqual(2, self.player.input_processed_seq)
                    self.assertEqual(1, self.player.input_seq)
                    self.assertEqual(
                        'rejected',
                        self.player.input_decisions[2]['outcome'])
                    self.assertTrue(
                        self.player.last_input_reject['consumed'])

                    # Exactly one frame per round: the next one applies even
                    # while the hook stays armed.
                    self.assertTrue(
                        self.state.update_input(1, self._frame()))
                    self.assertEqual(3, self.player.input_seq)

    def test_shell_pair_fault_is_illegal_at_every_valid_shell_index(self):
        for shell_index in range(10):
            with self.subTest(shell_index=shell_index):
                self.setUp()
                frame = self._frame()
                frame.update({
                    'shell_index': shell_index,
                    'next_shell_index': shell_index,
                    'shell_change_pending': False,
                })
                with mock.patch.dict(
                        os.environ,
                        {PLAYER_INPUT_FAULT_ENV: 'shell_pair'}):
                    self.assertFalse(self.state.update_input(1, frame))
                self.assertEqual(1, self.player.input_processed_seq)
                self.assertEqual(0, self.player.input_seq)
                self.assertEqual(
                    'shell_selection',
                    self.player.last_input_reject['reason'])


class OrderedEventVocabularyTests(unittest.TestCase):
    """The client fails a round closed on an unknown ordered event, so the
    two sides' vocabularies must not drift.  Shipping an `assist` event the
    client did not know ended every battle."""

    SERVER = (Path(__file__).resolve().parents[1] / 'server' /
              'lan_battle_server.py')
    CLIENT = (Path(__file__).resolve().parents[1] / 'src' / 'res' /
              'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922' /
              'battle_runtime.py')

    def _server_kinds(self):
        source = self.SERVER.read_text()
        kinds = set(re.findall(r'"kind":\s*"([a-z_]+)"', source))
        # Critical-damage records have their own nested ``kind`` vocabulary;
        # they are payload rows inside a top-level hit/repair event and never
        # enter the ordered battle-event dispatcher directly.
        return kinds - {'device', 'ammo_rack', 'crew', 'fire'}

    def _client_kinds(self):
        namespace = {}
        source = self.CLIENT.read_text()
        for name in ('_SHOT_EVENT_KINDS', '_COMBAT_EVENT_KINDS',
                     '_SIMPLE_EVENT_KINDS'):
            match = re.search(
                r'^%s = \(.*?\)' % name, source, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(match, name)
            exec(compile(match.group(0), name, 'exec'), namespace)
        return (set(namespace['_SHOT_EVENT_KINDS']) |
                set(namespace['_COMBAT_EVENT_KINDS']) |
                set(namespace['_SIMPLE_EVENT_KINDS']))

    def test_the_client_handles_every_kind_the_server_emits(self):
        server_kinds = self._server_kinds()
        self.assertIn('assist', server_kinds)

        unhandled = server_kinds - self._client_kinds()

        self.assertEqual(set(), unhandled)

    def test_battle_result_stays_in_the_client_vocabulary(self):
        # The server sends it inside the round-end message rather than as a
        # "kind" literal, so the extraction above cannot see it.
        self.assertIn('battle_result', self._client_kinds())
