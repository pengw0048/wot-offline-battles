"""Route reinforcements must obey the recipient's own radio knowledge."""
import copy
import unittest

import test_port_0922_bot_runtime  # Install the standalone client fixtures.
import test_port_0922_server_bot_ai as fixture
from server_bot_ai import BotPlanner


class RadioRouteTests(unittest.TestCase):
    def _fixture(self, recipients=(), lease=10.0, legacy=False):
        planner = BotPlanner()
        left = fixture._route('left', [(-300, 0, False), (-300, 300, True)])
        right = fixture._route('right', [(300, 0, False), (300, 300, True)])
        manifest = [fixture._bot(
            identity, 1, identity - 11, left if identity < 14 else right,
            'mediumTank', {'support': 1.0}) for identity in range(11, 15)]
        states = [fixture._state(identity, 1, -300 if identity < 14 else 300, 0)
                  for identity in range(11, 15)]
        players = [{'id': identity, 'team': 2, 'alive': True}
                   for identity in range(2, 5)]
        contacts = []
        for identity in range(2, 5):
            contact = fixture._contact(identity, 300, 300, [])
            if not legacy:
                contact['radio_recipients'] = [
                    {'kind': 'bot', 'id': recipient, 'time_left': lease}
                    for recipient in recipients]
            contacts.append(contact)
        targets = planner.known_targets(states, players)
        planner.report_contacts(contacts, targets, 1.0)
        return planner, manifest, states, players, contacts

    @staticmethod
    def _orders(planner, manifest, states, players, now):
        return {row['id']: row for row in
                planner.build_orders(manifest, states, players, now)['orders']}

    def test_unreceived_enemy_positions_do_not_change_routes(self):
        planner, manifest, states, players, unused = self._fixture()
        orders = self._orders(planner, manifest, states, players, 1.0)
        for identity in (11, 12, 13):
            self.assertEqual('left', orders[identity]['route_id'])
            self.assertEqual(-300.0, orders[identity]['move_position']['x'])
        self.assertTrue(all(row['target_id'] is None for row in orders.values()))

    def test_only_a_receiving_donor_can_reinforce_the_reported_flank(self):
        planner, manifest, states, players, unused = self._fixture((11,))
        orders = self._orders(planner, manifest, states, players, 1.0)
        self.assertEqual('right', orders[11]['route_id'])
        self.assertEqual('left', orders[12]['route_id'])
        self.assertEqual('left', orders[13]['route_id'])
        self.assertTrue(planner._route_assignments[11]['radio_scoped'])

    def test_disconnect_retires_route_lease_before_rebalance_interval(self):
        planner, manifest, states, players, contacts = self._fixture((11,))
        orders = self._orders(planner, manifest, states, players, 1.0)
        self.assertEqual('right', orders[11]['route_id'])
        for contact in contacts:
            contact['radio_recipients'] = []
        planner.report_contacts(contacts, planner.known_targets(states, players), 1.1)
        for now in (1.1, 5.0, 9.0):
            orders = self._orders(planner, manifest, states, players, now)
            self.assertEqual('left', orders[11]['route_id'])
            self.assertEqual(0.0, planner._route_assignments[11]['until'])

    def test_expired_radio_lease_does_not_retain_route_movement(self):
        planner, manifest, states, players, unused = self._fixture((11,), lease=0.5)
        self.assertEqual('right', self._orders(
            planner, manifest, states, players, 1.0)[11]['route_id'])
        self.assertEqual('left', self._orders(
            planner, manifest, states, players, 1.6)[11]['route_id'])

    def test_one_receivers_disconnect_keeps_another_receivers_knowledge(self):
        planner, manifest, states, players, contacts = self._fixture((11,))
        self._orders(planner, manifest, states, players, 1.0)
        changed = copy.deepcopy(contacts)
        for contact in changed:
            contact['radio_recipients'] = [
                {'kind': 'bot', 'id': 12, 'time_left': 10.0}]
        planner.report_contacts(changed, planner.known_targets(states, players), 5.0)
        orders = self._orders(planner, manifest, states, players, 5.0)
        self.assertEqual('left', orders[11]['route_id'])
        self.assertEqual('right', orders[12]['route_id'])
        self.assertEqual('left', orders[13]['route_id'])

    def test_legacy_contacts_keep_existing_route_lease_semantics(self):
        planner, manifest, states, players, unused = self._fixture(legacy=True)
        orders = self._orders(planner, manifest, states, players, 1.0)
        donors = [identity for identity in (11, 12, 13)
                  if orders[identity]['route_id'] == 'right']
        self.assertEqual(1, len(donors))
        donor = donors[0]
        old_deadline = planner._route_assignments[donor]['until']
        orders = self._orders(planner, manifest, states, players, 5.0)
        self.assertEqual('right', orders[donor]['route_id'])
        self.assertGreater(planner._route_assignments[donor]['until'], old_deadline)


if __name__ == '__main__':
    unittest.main()
