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
        for change in ({'round_id': 6}, {'team': 2}, {'command': []}, {'command_seq': True}):
            self.client._handle_message(dict(message, **change))
        self.assertEqual(['team_command'], received)
        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)

    def test_engine_network_and_account_id_domains_stay_separate(self):
        runtime, battle, unused, unused_target, unused_descriptor = (
            fixtures.BattleRuntimeContractTests()._bot_lane_scene())
        battle.client = self.client
        battle._worker_mode = False
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


if __name__ == '__main__':
    unittest.main()
