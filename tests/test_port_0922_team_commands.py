import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, Player, PREBATTLE_SECONDS, TICK_HZ,
    SIMULATION_WORKER_AUTHORITY_ID)


class _Socket(object):
    def sendall(self, unused_payload):
        pass


class _Planner(object):
    def __init__(self):
        self.team_orders = []

    def reset(self):
        pass

    def build_orders(self, *unused_args, **kwargs):
        self.team_orders.append(kwargs.get('team_orders'))
        return {'revision': len(self.team_orders), 'orders': []}


class TeamCommandTests(unittest.TestCase):
    def setUp(self):
        self.state = BattleState(map_name='01_karelia')
        self.state.client_build = CLIENT_BUILD_0922
        self.state.phase = 'battle'
        self.state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        self.sender = Player(1, _Socket(), ('127.0.0.1', 1), team=1,
                             x=0.0, z=0.0)
        self.enemy = Player(2, _Socket(), ('127.0.0.1', 2), team=2,
                            x=200.0, z=0.0)
        self.state.players = {1: self.sender, 2: self.enemy}
        self.state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        self.state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        self.state.bot_manifest = [
            {'id': 11, 'team': 1}, {'id': 12, 'team': 1},
            {'id': 21, 'team': 2},
        ]
        self.state.bot_states = {
            11: {'id': 11, 'team': 1, 'alive': True, 'x': 20.0, 'z': 0.0},
            12: {'id': 12, 'team': 1, 'alive': True, 'x': 40.0, 'z': 0.0},
            21: {'id': 21, 'team': 2, 'alive': True, 'x': 180.0, 'z': 0.0},
        }

    def _message(self, command, sequence=1, **fields):
        message = {
            'type': 'team_command', 'round_id': self.state.round_id,
            'command_seq': sequence, 'command': command,
        }
        message.update(fields)
        return message

    def test_history_eviction_publishes_a_terminal_receipt(self):
        from unittest import mock
        sent = []
        self.sender.offer_reliable = lambda message: sent.append(message) or True
        with mock.patch('lan_battle_server.TEAM_COMMAND_HISTORY', 1):
            self.assertTrue(self.state.submit_team_command(1, self._message('HELPME'))['accepted'])
            self.assertTrue(self.state.submit_team_command(1, self._message('ATTACK', sequence=2))['accepted'])
        self.assertEqual('superseded', sent[-1]['code'])
        self.assertEqual(1, sent[-1]['command_seq'])

    def test_unhashable_command_is_rejected_locally(self):
        ack = self.state.submit_team_command(1, self._message([]))
        self.assertFalse(ack['accepted'])
        self.assertEqual('unknown_command', ack['code'])

    def test_accepts_stock_cell_command_for_nearest_bounded_bots(self):
        ack = self.state.submit_team_command(
            1, self._message('ATTENTIONTOCELL', cell_index=38))

        self.assertTrue(ack['accepted'])
        self.assertEqual('accepted', ack['code'])
        self.assertEqual([11, 12], ack['recipient_bot_ids'])
        orders = self.state._active_team_commands_locked()
        self.assertEqual(1, len(orders))
        self.assertEqual('ATTENTIONTOCELL', orders[0]['command'])
        self.assertEqual(38, orders[0]['cell_index'])
        self.assertGreater(orders[0]['expires_tick'], self.state.tick)

    def test_enemy_command_requires_sender_current_direct_spot(self):
        message = self._message(
            'ATTACKENEMY', target_id=2, target_kind='human')
        rejected = self.state.submit_team_command(1, message)
        self.assertFalse(rejected['accepted'])
        self.assertEqual('enemy_not_visible', rejected['code'])

        self.state.player_spotted[1] = frozenset((('player', 2),))
        self.state.bot_spotted[12] = frozenset((('player', 2),))
        accepted = self.state.submit_team_command(1, message)

        self.assertTrue(accepted['accepted'])
        self.assertEqual([12, 11], accepted['recipient_bot_ids'])
        order = self.state._active_team_commands_locked()[0]
        self.assertEqual('human', order['target_kind'])
        self.assertEqual(2, order['target_id'])

    def test_ally_order_is_directed_only_to_the_named_live_bot(self):
        ack = self.state.submit_team_command(
            1, self._message('FOLLOWME', target_id=12, target_kind='bot'))

        self.assertTrue(ack['accepted'])
        self.assertEqual([12], ack['recipient_bot_ids'])
        self.assertEqual([12], self.state._active_team_commands_locked()[0]
                         ['recipient_bot_ids'])

    def test_sender_death_clears_command_before_next_planning_cycle(self):
        sent = []
        self.sender.offer_reliable = lambda message: sent.append(message) or True
        self.assertTrue(self.state.submit_team_command(
            1, self._message('HELPME'))['accepted'])
        self.sender.alive = False

        self.assertEqual([], self.state._active_team_commands_locked())
        self.assertEqual({}, self.state.team_commands)
        self.assertEqual('team_command_terminal', sent[-1]['type'])
        self.assertEqual('issuer_dead', sent[-1]['code'])

    def test_duplicate_sequence_replays_same_ack_but_conflict_is_rejected(self):
        message = self._message('ATTACK')
        accepted = self.state.submit_team_command(1, message)

        self.assertEqual(
            accepted, self.state.submit_team_command(1, dict(message)))
        rejected = self.state.submit_team_command(
            1, self._message('HELPME', sequence=1))
        self.assertFalse(rejected['accepted'])
        self.assertEqual('sequence_conflict', rejected['code'])

    def test_planner_gets_only_active_validated_team_orders(self):
        planner = _Planner()
        self.state.bot_planner = planner
        self.assertTrue(self.state.submit_team_command(
            1, self._message('BACKTOBASE'))['accepted'])

        self.state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(1, len(planner.team_orders))
        self.assertEqual('BACKTOBASE', planner.team_orders[0][0]['command'])

    def test_accepted_command_is_relayed_only_to_the_sender_team(self):
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        deliveries = {1: [], 2: [], 3: []}
        for player_id, player in self.state.players.items():
            player.offer_reliable = (
                lambda message, player_id=player_id:
                deliveries[player_id].append(message) or True)
        ack = self.state.submit_team_command(
            1, self._message('ATTENTIONTOCELL', cell_index=31))

        self.assertTrue(self.state.publish_team_command(ack))
        self.assertEqual('team_command', deliveries[1][0]['type'])
        self.assertEqual('ATTENTIONTOCELL', deliveries[3][0]['command'])
        self.assertEqual(31, deliveries[3][0]['cell_index'])
        self.assertEqual([], deliveries[2])

    def test_threatened_bot_ids_are_cleaned_without_rejecting_contact(self):
        relay = self.state.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'bot_observation', 'round_id': self.state.round_id,
                'contacts': [{
                    'observing_team': 1, 'target_kind': 'human',
                    'target_id': 2, 'target_team': 2,
                    'visible': True, 'fresh': True, 'time_left': 10.0,
                    'visible_by_bot_ids': [11],
                    'visible_by_player_ids': [],
                    'shootable_by_bot_ids': [11],
                    'threatened_bot_ids': [11, 12, 21, 'bad', 11],
                }], 'affordances': [],
            })

        self.assertIsInstance(relay, dict)
        self.assertEqual(
            [11, 12], self.state.bot_planner._contacts[1][
                ('human', 2)]['threatened_bot_ids'])


if __name__ == '__main__':
    unittest.main()
