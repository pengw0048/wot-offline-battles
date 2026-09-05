import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, Player, PREBATTLE_SECONDS, TICK_HZ,
    SIMULATION_WORKER_AUTHORITY_ID, TEAM_CHAT_MAX_CHARACTERS)


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

    def test_same_tick_commands_keep_admission_order_across_sequence_digits(self):
        from server_bot_ai import BotPlanner
        first = self.state.submit_team_command(1, self._message('HELPME', sequence=9))
        second = self.state.submit_team_command(1, self._message('ATTACK', sequence=10))
        self.assertTrue(first['accepted'] and second['accepted'])
        orders = self.state._active_team_commands_locked()
        bots = [{'id': 11, 'team': 1}, {'id': 12, 'team': 1}]
        selected = BotPlanner._team_orders_by_bot(orders, bots)
        self.assertEqual('ATTACK', selected[11]['command'])
        self.assertEqual(10, selected[11]['command_seq'])

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

    def test_enemy_message_broadcasts_without_a_spot_but_only_spotted_order_reaches_bot(self):
        message = self._message(
            'ATTACKENEMY', target_id=2, target_kind='human')
        accepted_message = self.state.submit_team_command(1, message)
        self.assertTrue(accepted_message['accepted'])
        self.assertEqual([], accepted_message['recipient_bot_ids'])
        self.assertEqual([], self.state._active_team_commands_locked())

        self.state.player_spotted[1] = frozenset((('player', 2),))
        self.state.bot_spotted[12] = frozenset((('player', 2),))
        accepted = self.state.submit_team_command(
            1, self._message('ATTACKENEMY', sequence=2,
                             target_id=2, target_kind='human'))

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

    def test_social_command_broadcasts_but_never_creates_bot_order(self):
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        deliveries = {1: [], 2: [], 3: []}
        for player_id, player in self.state.players.items():
            player.offer_reliable = (
                lambda message, player_id=player_id:
                deliveries[player_id].append(message) or True)

        ack = self.state.submit_team_command(1, self._message('POSITIVE'))

        self.assertTrue(ack['accepted'])
        self.assertEqual([], ack['recipient_bot_ids'])
        self.assertEqual([], self.state._active_team_commands_locked())
        self.assertTrue(self.state.publish_team_command(ack))
        self.assertTrue(self.state.publish_team_command(ack))
        self.assertEqual(1, len(deliveries[1]))
        self.assertEqual('POSITIVE', deliveries[3][0]['command'])
        self.assertEqual([], deliveries[2])

    def test_cell_message_reaches_human_team_with_no_live_bot(self):
        self.state.bot_states[11]['alive'] = False
        self.state.bot_states[12]['alive'] = False
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        messages = []
        teammate.offer_reliable = lambda message: messages.append(message) or True

        ack = self.state.submit_team_command(
            1, self._message('ATTENTIONTOCELL', cell_index=31))

        self.assertTrue(ack['accepted'])
        self.assertEqual([], ack['recipient_bot_ids'])
        self.assertTrue(self.state.publish_team_command(ack))
        self.assertEqual(31, messages[0]['cell_index'])
        self.assertEqual([], self.state._active_team_commands_locked())

    def test_human_ally_target_and_dead_sender_broadcast_without_bot_order(self):
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        messages = []
        teammate.offer_reliable = lambda message: messages.append(message) or True
        self.sender.alive = False

        follow = self.state.submit_team_command(
            1, self._message('FOLLOWME', target_kind='human', target_id=3))
        cell = self.state.submit_team_command(
            1, self._message('ATTENTIONTOCELL', sequence=2, cell_index=4))

        self.assertTrue(follow['accepted'] and cell['accepted'])
        self.assertEqual([], follow['recipient_bot_ids'])
        self.assertEqual([], cell['recipient_bot_ids'])
        self.assertTrue(self.state.publish_team_command(follow))
        self.assertTrue(self.state.publish_team_command(cell))
        self.assertEqual(['FOLLOWME', 'ATTENTIONTOCELL'],
                         [message['command'] for message in messages])
        self.assertEqual([], self.state._active_team_commands_locked())

    def _chat_message(self, text, sequence=1):
        return {
            'type': 'team_chat', 'round_id': self.state.round_id,
            'chat_seq': sequence, 'text': text,
        }

    def test_team_chat_is_canonical_unicode_and_idempotently_broadcast(self):
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        deliveries = {1: [], 2: [], 3: []}
        for player_id, player in self.state.players.items():
            player.offer_reliable = (
                lambda message, player_id=player_id:
                deliveries[player_id].append(message) or True)
        self.sender.alive = False
        message = self._chat_message(u'队友 <注意> & 伏击')

        ack = self.state.submit_team_chat(1, message)

        self.assertTrue(ack['accepted'])
        self.assertTrue(self.state.publish_team_chat(1, ack))
        self.assertTrue(self.state.publish_team_chat(1, ack))
        self.assertEqual(1, len(deliveries[1]))
        self.assertEqual(message['text'], deliveries[3][0]['text'])
        self.assertEqual([], deliveries[2])
        self.assertEqual(ack, self.state.submit_team_chat(1, dict(message)))

    def test_team_chat_rejects_blank_invalid_unicode_and_overlong_text_locally(self):
        self.assertEqual('invalid_text', self.state.submit_team_chat(
            1, self._chat_message(''))['code'])
        self.assertEqual('invalid_text', self.state.submit_team_chat(
            1, self._chat_message(' \t '))['code'])
        self.assertEqual('invalid_text', self.state.submit_team_chat(
            1, self._chat_message(u'\ud800'))['code'])

    def test_team_chat_uses_stock_utf16_unit_limit(self):
        self.assertTrue(self.state.submit_team_chat(
            1, self._chat_message(u'😀' * 70))['accepted'])
        self.assertEqual('invalid_text', self.state.submit_team_chat(
            1, self._chat_message(u'😀' * 71, sequence=2))['code'])

    def test_team_chat_preserves_stock_unicode_across_server_unicode_versions(self):
        text = u'\U0001f16a'
        ack = self.state.submit_team_chat(1, self._chat_message(text))
        deliveries = []
        self.sender.offer_reliable = lambda message: deliveries.append(message) or True

        self.assertTrue(ack['accepted'])
        self.assertTrue(self.state.publish_team_chat(1, ack))
        self.assertEqual(text, deliveries[0]['text'])

    def test_stale_or_rebound_acknowledgements_cannot_publish(self):
        command_ack = self.state.submit_team_command(self.sender.player_id,
                                                     self._message('HELPME'))
        chat_ack = self.state.submit_team_chat(
            self.sender.player_id, self._chat_message('hold'))
        deliveries = []
        self.sender.offer_reliable = lambda message: deliveries.append(message) or True

        self.state.round_id += 1
        self.assertFalse(self.state.publish_team_command(command_ack))
        self.assertFalse(self.state.publish_team_chat(self.sender.player_id,
                                                      chat_ack))
        self.assertEqual([], deliveries)

        self.state.round_id -= 1
        altered_command = dict(command_ack, recipient_bot_ids=[])
        altered_chat = dict(chat_ack, code='invalid_text')
        self.assertFalse(self.state.publish_team_command(altered_command))
        self.assertFalse(self.state.publish_team_chat(self.sender.player_id,
                                                      altered_chat))
        self.assertEqual([], deliveries)

    def test_old_round_rejections_cannot_alias_current_round_sequence_one(self):
        self.state.round_id = 8
        stale_command = self._message('HELPME', sequence=1)
        stale_command['round_id'] = 7
        stale_chat = self._chat_message('old', sequence=1)
        stale_chat['round_id'] = 7

        command_rejection = self.state.submit_team_command(1, stale_command)
        chat_rejection = self.state.submit_team_chat(1, stale_chat)
        command_current = self.state.submit_team_command(
            1, self._message('HELPME', sequence=1))
        chat_current = self.state.submit_team_chat(
            1, self._chat_message('current', sequence=1))

        self.assertFalse(command_rejection['accepted'])
        self.assertFalse(chat_rejection['accepted'])
        self.assertEqual(7, command_rejection['round_id'])
        self.assertEqual(7, chat_rejection['round_id'])
        self.assertTrue(command_current['accepted'])
        self.assertTrue(chat_current['accepted'])
        self.assertEqual(8, command_current['round_id'])
        self.assertEqual(8, chat_current['round_id'])

    def test_dead_enemy_can_be_displayed_but_never_becomes_bot_focus(self):
        self.enemy.alive = False
        self.state.player_spotted[1] = frozenset((('player', 2),))

        ack = self.state.submit_team_command(
            1, self._message('ATTACKENEMY', target_kind='human', target_id=2))

        self.assertTrue(ack['accepted'])
        self.assertEqual([], ack['recipient_bot_ids'])
        self.assertEqual([], self.state._active_team_commands_locked())

    def test_all_stock_information_commands_relay_without_bot_orders(self):
        teammate = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        self.state.players[3] = teammate
        deliveries = []
        teammate.offer_reliable = lambda message: deliveries.append(message) or True
        messages = (
            self._message('SPG_AIM_AREA', aim_point=[4999.75, -2000.0, -4999.25],
                          cell_index=0, reload_time=0.0),
            self._message('RELOADINGGUN', sequence=2, reload_time=2.5),
            self._message('RELOADING_CASSETE', sequence=3, reload_time=2.5,
                          quantity=7),
            self._message('RELOADING_READY', sequence=4),
            self._message('RELOADING_READY_CASSETE', sequence=5, quantity=7),
            self._message('RELOADING_UNAVAILABLE', sequence=6),
        )

        for message in messages:
            ack = self.state.submit_team_command(1, message)
            self.assertTrue(ack['accepted'])
            self.assertEqual([], ack['recipient_bot_ids'])
            self.assertTrue(self.state.publish_team_command(ack))

        self.assertEqual([message['command'] for message in messages],
                         [message['command'] for message in deliveries])
        self.assertEqual(messages[0]['aim_point'], deliveries[0]['aim_point'])
        self.assertEqual(0.0, deliveries[0]['reload_time'])
        self.assertEqual(7, deliveries[2]['quantity'])
        self.assertEqual([], self.state._active_team_commands_locked())

    def test_reload_metadata_is_validated_relayed_and_not_a_bot_order_field(self):
        self.state.player_spotted[1] = frozenset((('player', 2),))
        ack = self.state.submit_team_command(
            1, self._message('ATTACKENEMY', target_kind='human', target_id=2,
                             reload_time=0.0))

        self.assertTrue(ack['accepted'])
        self.assertEqual(0.0, ack['reload_time'])
        order = self.state._active_team_commands_locked()[0]
        self.assertNotIn('reload_time', order)

        invalid = self.state.submit_team_command(
            1, self._message('SPG_AIM_AREA', sequence=2,
                             aim_point=[0.0, 0.0, 0.0], cell_index=100,
                             reload_time=0.0))
        invalid_reload = self.state.submit_team_command(
            1, self._message('RELOADINGGUN', sequence=2, reload_time=True))
        invalid_quantity = self.state.submit_team_command(
            1, self._message('RELOADING_READY_CASSETE', sequence=2,
                             quantity=256))
        invalid_aim = self.state.submit_team_command(
            1, self._message('SPG_AIM_AREA', sequence=2,
                             aim_point=[float('nan'), 0.0, 0.0], cell_index=0,
                             reload_time=0.0))

        self.assertFalse(invalid['accepted'])
        self.assertEqual('invalid_cell', invalid['code'])
        self.assertEqual('invalid_reload_time', invalid_reload['code'])
        self.assertEqual('invalid_quantity', invalid_quantity['code'])
        self.assertEqual('invalid_aim_point', invalid_aim['code'])

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
