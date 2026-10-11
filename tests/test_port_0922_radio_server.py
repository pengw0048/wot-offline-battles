"""Radio knowledge must survive relay without becoming team omniscience."""
import copy
import unittest

import test_port_0922_bot_runtime as runtime_fixture
import test_port_0922_server_bot_ai as planner_fixture
from lan_battle_server import SIMULATION_WORKER_AUTHORITY_ID as WORKER
from server_bot_ai import BotPlanner


class RadioRelayTests(unittest.TestCase):
    def setUp(self):
        self.state, _, _ = runtime_fixture.ServerBotObservationRelayTests._server()
        self.message = runtime_fixture.ServerBotObservationRelayTests._message(
            self.state.round_id)
        self.message['contacts'][0]['radio_recipients'] = [
            {'kind': 'bot', 'id': 11, 'time_left': 4.0}]

    def test_recipient_lease_survives_relay_and_hidden_clears_it(self):
        result = self.state.update_bot_observation(WORKER, self.message)
        self.assertEqual(self.message['contacts'][0]['radio_recipients'],
                         result['contacts'][0]['radio_recipients'])
        hidden = runtime_fixture.ServerBotObservationRelayTests._message(
            self.state.round_id, visible=False)
        hidden['contacts'][0]['radio_recipients'] = []
        result = self.state.update_bot_observation(WORKER, hidden)
        self.assertEqual([], result['contacts'][0]['radio_recipients'])
        self.assertEqual({}, self.state.bot_planner._contacts[2][
            ('human', 2)]['radio_bot_until'])

    def test_invalid_recipient_rejects_whole_batch_without_committing(self):
        variants = [None, {}, [{'kind': 'bot', 'id': 999, 'time_left': 2}],
                    [{'kind': 'human', 'id': 1, 'time_left': 2}]]
        for field, value in [('kind', 'other'), ('id', True), ('id', -1),
                             ('time_left', 11), ('time_left', 0),
                             ('time_left', float('nan'))]:
            row = {'kind': 'bot', 'id': 11, 'time_left': 4}
            row[field] = value
            variants.append([row])
        variants.append(self.message['contacts'][0]['radio_recipients'] * 2)
        for rows in variants:
            with self.subTest(rows=rows):
                bad = copy.deepcopy(self.message)
                bad['contacts'][0]['radio_recipients'] = rows
                self.assertFalse(self.state.update_bot_observation(WORKER, bad))
                self.assertEqual({1: {}, 2: {}}, self.state.bot_planner._contacts)

    def test_radio_links_relay_and_empty_batch_clear_coverage(self):
        links = [{'kind': 'human', 'id': 1,
                  'allies': [{'kind': 'human', 'id': 2}]}]
        self.message['radio_links'] = links
        result = self.state.update_bot_observation(WORKER, self.message)
        self.assertEqual(links, result['radio_links'])
        result = self.state.update_bot_observation(WORKER, {
            'round_id': self.state.round_id, 'contacts': [], 'radio_links': []})
        self.assertIsInstance(result, dict)
        self.assertEqual([], result['radio_links'])

    def test_unknown_enemy_self_and_duplicate_radio_links_are_rejected(self):
        variants = [
            [{'kind': 'human', 'id': 1}],
            [{'kind': 'bot', 'id': 11}],
            [{'kind': 'human', 'id': 999}],
            [{'kind': 'human', 'id': 2}] * 2]
        for allies in variants:
            with self.subTest(allies=allies):
                bad = copy.deepcopy(self.message)
                bad['radio_links'] = [{'kind': 'human', 'id': 1, 'allies': allies}]
                self.assertFalse(self.state.update_bot_observation(WORKER, bad))
                self.assertEqual({1: {}, 2: {}}, self.state.bot_planner._contacts)

    def test_known_ally_dying_in_flight_is_pruned(self):
        self.state.players[2].alive = False
        # Use no enemy observation; only the live sender's ally-link snapshot.
        result = self.state.update_bot_observation(WORKER, {
            'round_id': self.state.round_id, 'contacts': [],
            'radio_links': [{'kind': 'human', 'id': 1,
                             'allies': [{'kind': 'human', 'id': 2}]}]})
        self.assertEqual([], result['radio_links'][0]['allies'])


class RadioPlannerTests(unittest.TestCase):
    def test_unconnected_bot_cannot_target_and_received_lease_expires(self):
        planner = BotPlanner()
        route = planner_fixture._route('line', [(0, 0, False), (0, 300, True)])
        manifest = [planner_fixture._bot(i, 1, i - 11, route, 'mediumTank')
                    for i in (11, 12)]
        states = [planner_fixture._state(i, 1, (i - 11) * 10, 0)
                  for i in (11, 12)]
        players = [{'id': 2, 'team': 2, 'alive': True}]
        contact = planner_fixture._contact(2, 0, 150, [11, 12])
        contact['radio_recipients'] = [
            {'kind': 'bot', 'id': 11, 'time_left': 0.5}]
        planner.report_contacts([contact], planner.known_targets(states, players), 1.0)
        orders = {row['id']: row for row in planner.build_orders(
            manifest, states, players, 1.0)['orders']}
        self.assertEqual(2, orders[11].get('target_id'))
        self.assertIsNone(orders[12].get('target_id'))
        expired = planner.build_orders(manifest, states, players, 1.6)['orders']
        self.assertTrue(all(row.get('target_id') is None for row in expired))
        self.assertTrue(all(not row.get('fire_allowed') for row in expired))
        # A new team contact cannot recreate an expired recipient's lease.
        contact['radio_recipients'] = []
        planner.report_contacts([contact], planner.known_targets(states, players), 2.0)
        disconnected = planner.build_orders(manifest, states, players, 2.0)['orders']
        self.assertTrue(all(row.get('target_id') is None for row in disconnected))


if __name__ == '__main__':
    unittest.main()
