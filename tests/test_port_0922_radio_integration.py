import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922.lan_client import LANClient, ORDERED_RECEIVE_TYPES
from gui.mods.offline_lan_0922.battle_runtime import _LANInputSender
import test_port_0922_battle_runtime as fixtures


class RadioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = LANClient('127.0.0.1', 28782, 'P', 'ussr:R11_MS-1')
        self.client.ready = True
        self.client.running = True
        self.client.phase = 'battle'
        self.client.round_id = 7
        self.client.player_id = 1
        self.client.team = 1
        self.client.bot_authority_id = -1
        self.sent = []
        self.client._send = lambda value: self.sent.append(value) or True

    def test_wire_is_ordered_and_sequence_is_scoped_to_round(self):
        self.assertEqual(1, self.client.send_team_command('FOLLOWME', 'bot', 1))
        self.assertEqual('bot', self.sent[-1]['target_kind'])
        self.assertEqual(2, self.client.send_team_command('ATTENTIONTOCELL', cell_index=23))
        self.assertFalse(self.client.send_team_command('ATTENTIONTOCELL', cell_index=100))
        self.client.round_id = 8
        self.assertEqual(1, self.client.send_team_command('HELPME'))
        self.assertEqual(8, self.sent[-1]['round_id'])
        self.assertTrue({'team_command', 'team_command_ack', 'team_command_terminal'} <= ORDERED_RECEIVE_TYPES)

    def test_malformed_wrong_team_and_old_round_radio_do_not_end_battle(self):
        received = []
        self.client.on_event = lambda kind, value: received.append(kind)
        message = dict(type='team_command', protocol=5, round_id=7, command='HELPME',
                       command_seq=1, issuer_id=1, team=1)
        self.client._handle_message(message)
        self.assertEqual(['team_command'], received)
        for change in ({'round_id': 6}, {'round_id': 8}, {'team': 2},
                       {'command': []}, {'command_seq': True}):
            self.client._handle_message(dict(message, **change))
        self.assertEqual(['team_command'], received)
        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)

    def test_engine_network_and_account_id_domains_stay_separate(self):
        runtime, battle, unused, unused_target, unused_descriptor = (
            fixtures.BattleRuntimeContractTests()._bot_lane_scene())
        battle.client = self.client
        battle._worker_mode = False
        battle.state = 'running'
        battle._battle_live = True
        battle._start_message = {'round_id': 7}
        battle._server = mock.Mock()
        battle._records = {
            'player:1': dict(kind='player', network_id=1, engine_id=101, ready=True),
            'bot:1': dict(kind='bot', network_id=1, engine_id=201, ready=True),
        }
        battle._avatar.arena = types.SimpleNamespace(vehicles={
            101: {'accountDBID': 10001, 'team': 1},
            201: {'accountDBID': 20001, 'team': 1},
        })
        sender = _LANInputSender(battle)
        self.assertEqual(1, sender.send_team_command(dict(
            command='FOLLOWME', stock_target_id=201, target_relation='ally')))
        self.assertEqual(('bot', 1), (self.sent[-1]['target_kind'], self.sent[-1]['target_id']))
        message = dict(type='team_command', round_id=7, command_seq=1, issuer_id=1,
                       team=1, command='FOLLOWME', target_kind='bot', target_id=1)
        self.assertTrue(battle.on_team_command(message))
        battle._server.receive_team_command.assert_called_once_with('FOLLOWME', 10001, 201, None)
        self.assertFalse(battle.on_team_command(message))
        self.assertTrue(battle.on_team_command_ack(dict(
            round_id=7, command_seq=1, accepted=True, recipient_bot_ids=[1])))
        battle._server.receive_team_command_ack.assert_called_once_with(1, True, [20001])
        self.assertFalse(battle.on_team_command_ack(dict(
            round_id=6, command_seq=1, accepted=True, recipient_bot_ids=[1])))
        battle._records['bot:1']['tombstone'] = True
        self.assertFalse(sender.send_team_command(dict(command='FOLLOWME', stock_target_id=201)))

    def test_chat_and_commands_can_be_sent_during_countdown(self):
        unused, battle, unused_source, unused_target, unused_descriptor = (
            fixtures.BattleRuntimeContractTests()._bot_lane_scene())
        battle.client = self.client
        battle._worker_mode = False
        battle.state = 'running'
        battle._battle_live = False
        sender = _LANInputSender(battle)

        self.assertEqual(1, sender.send_team_chat({'text': u'Hold here'}))
        self.assertEqual(1, sender.send_team_command(
            {'command': 'ATTENTIONTOCELL', 'cell_index': 12}))
        for state in ('loading_entities', 'stopping', 'idle'):
            battle.state = state
            self.assertFalse(sender.send_team_chat({'text': u'Late'}))
            self.assertFalse(sender.send_team_command({'command': 'HELPME'}))
        battle.state = 'running'
        battle._worker_mode = True
        self.assertFalse(sender.send_team_chat({'text': u'Worker'}))

    def test_chat_sequence_is_independent_and_restarts_at_round_boundary(self):
        text = u'\u961f\u53cb,\u8bf7\u63a9\u62a4 <A1> & B2'
        self.assertEqual(1, self.client.send_team_chat(text))
        self.assertEqual(text, self.sent[-1]['text'])
        self.assertEqual(1, self.client.send_team_command('HELPME'))
        self.assertEqual(2, self.client.send_team_chat(text))
        self.client._send = lambda unused: False
        self.assertFalse(self.client.send_team_chat(text))
        self.client._send = lambda value: self.sent.append(value) or True
        self.assertEqual(3, self.client.send_team_chat(text))
        self.client.round_id = 8
        self.assertEqual(1, self.client.send_team_chat(text))
        self.assertEqual(8, self.sent[-1]['round_id'])
        self.assertTrue({'team_chat', 'team_chat_ack'} <= ORDERED_RECEIVE_TYPES)

    def test_invalid_chat_never_disconnects_or_mutates_round(self):
        from gui.mods.offline_lan_0922.tactical_radio import MAX_TEAM_CHAT_LENGTH
        received = []
        self.client.on_event = lambda kind, value: received.append((kind, value))
        message = dict(type='team_chat', protocol=5, round_id=7,
                       chat_seq=1, issuer_id=1, team=1, text=u'Hello')
        invalid = ({'round_id': 6}, {'round_id': 8}, {'round_id': True},
                   {'team': 2}, {'team': True}, {'issuer_id': True},
                   {'chat_seq': True}, {'text': []}, {'text': ''},
                   {'text': 'x' * (MAX_TEAM_CHAT_LENGTH + 1)},
                   {'text': u'\ud800'})
        for change in invalid:
            self.client._handle_message(dict(message, **change))
        self.assertEqual([], received)
        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(7, self.client.round_id)
        self.client._handle_message(message)
        self.assertEqual([('team_chat', message)], received)
        ack = dict(type='team_chat_ack', protocol=5, round_id=7,
                   chat_seq=1, accepted=True, code='accepted')
        self.client._handle_message(ack)
        self.assertEqual(('team_chat_ack', ack), received[-1])
        for change in ({'chat_seq': 0}, {'accepted': 1}, {'code': []}):
            self.client._handle_message(dict(ack, **change))
        self.assertEqual(2, len(received))

    def test_stock_reload_and_spg_details_survive_wire_and_runtime(self):
        details = {'aim_point': [125.0, 42.0, -270.0], 'reload_time': 8.25}
        self.assertEqual(1, self.client.send_team_command(
            'SPG_AIM_AREA', cell_index=42, details=details))
        wire = self.sent[-1]
        self.assertEqual(details['aim_point'], wire['aim_point'])
        self.assertEqual(8.25, wire['reload_time'])
        received = []
        self.client.on_event = lambda kind, value: received.append(value)
        broadcast = dict(wire, protocol=5, issuer_id=1, team=1)
        self.client._handle_message(broadcast)
        self.assertEqual([broadcast], received)
        for change in ({'reload_time': float('nan')},
                       {'aim_point': [0.0, 0.0]}, {'cell_index': 100}):
            self.client._handle_message(dict(broadcast, **change))
        self.assertEqual([broadcast], received)
        self.assertTrue(self.client.running)

        unused, battle, unused_source, unused_target, unused_descriptor = (
            fixtures.BattleRuntimeContractTests()._bot_lane_scene())
        battle.client = self.client
        battle.state = 'running'
        battle._worker_mode = False
        battle._start_message = {'round_id': 7}
        battle._server = mock.Mock()
        battle._records = {
            'player:1': dict(kind='player', network_id=1, engine_id=101,
                             ready=True),
        }
        battle._avatar.arena = types.SimpleNamespace(vehicles={
            101: {'accountDBID': 10001, 'team': 1},
        })
        self.assertTrue(battle.on_team_command(broadcast))
        battle._server.receive_team_command.assert_called_once_with(
            'SPG_AIM_AREA', 10001, None, 42, details=details)
        self.assertEqual(2, _LANInputSender(battle).send_team_command(dict(
            command='RELOADING_CASSETE',
            details={'reload_time': 4.5, 'quantity': 3})))
        self.assertEqual(4.5, self.sent[-1]['reload_time'])
        self.assertEqual(3, self.sent[-1]['quantity'])

    def test_targeted_command_cannot_bypass_reload_validation(self):
        received = []
        self.client.on_event = lambda kind, value: received.append(value)
        broadcast = dict(type='team_command', round_id=7, command_seq=1,
                         command='ATTACKENEMY', issuer_id=1, team=1,
                         target_kind='human', target_id=2, reload_time=3.75)
        for value in (float('nan'), -1.0, True, '3.75'):
            self.client._handle_message(dict(broadcast, reload_time=value))
        self.assertEqual([], received)
        self.client._handle_message(broadcast)
        self.assertEqual([broadcast], received)

    def test_chat_display_resolves_dead_teammate_dbid_and_echoes_once(self):
        unused, battle, unused_source, unused_target, unused_descriptor = (
            fixtures.BattleRuntimeContractTests()._bot_lane_scene())
        battle.client = self.client
        battle._worker_mode = False
        battle.state = 'running'
        battle._start_message = {'round_id': 7}
        battle._server = mock.Mock()
        battle._records = {
            'player:1': dict(kind='player', network_id=1, engine_id=101,
                             ready=True, state={'alive': False}),
        }
        battle._avatar.arena = types.SimpleNamespace(vehicles={
            101: {'accountDBID': 10001, 'team': 1, 'isAlive': False},
        })
        message = dict(type='team_chat', round_id=7, chat_seq=1,
                       issuer_id=1, team=1, text=u'Cover our base')

        self.assertTrue(battle.on_team_chat(message))
        battle._server.receive_team_chat.assert_called_once_with(
            u'Cover our base', 10001)
        self.assertFalse(battle.on_team_chat(message))
        for change in ({'round_id': 6}, {'team': 2}, {'issuer_id': 999}):
            self.assertFalse(battle.on_team_chat(dict(message, **change)))
        self.assertTrue(battle.on_team_chat_ack(dict(
            round_id=7, chat_seq=1, accepted=True)))
        battle._server.receive_team_chat_ack.assert_called_once_with(1, True)
        self.assertFalse(battle.on_team_chat_ack(dict(
            round_id=6, chat_seq=1, accepted=True)))
        self.assertEqual(1, battle._server.receive_team_chat.call_count)
        battle.state = 'stopping'
        self.assertFalse(battle.on_team_chat(dict(message, chat_seq=2)))
        self.assertFalse(battle.on_team_chat_ack(dict(
            round_id=7, chat_seq=2, accepted=True)))


if __name__ == '__main__':
    unittest.main()
