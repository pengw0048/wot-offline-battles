"""Whole campaign edits reverse real payouts before permitting another grant."""

import copy
import unittest
from unittest import mock

import test_port_0922_economy as fixture
from test_port_0922_personal_campaign import node, quest, token
from gui.mods.offline_lan_0922 import personal_campaign as campaign
from gui.mods.offline_lan_0922 import personal_campaign_vehicles as vehicles
import test_port_0922_personal_campaign_vehicles as vehicle_fixture


class CampaignRewardResetTests(unittest.TestCase):
    def setUp(self):
        self.state = fixture._state()
        self.state.snapshot()['wallet'].setdefault('crystal', 0)
        self.definitions = {15: {
            'main': quest(node(credits=node(100), token=token('parts'))),
            'add': quest({'children': [
                ('gold', node(10)), ('token', token('free_award_list')),
                ('token', token('honors'))]})}}

        def operation(identifier, source, bonus):
            return node(id=node(identifier), enabled=node('true'),
                conditions=node(preBattle=node(account=node(token=node(
                    id=node(source), greaterOrEqual=node(1), consume=node(1))))),
                bonus=bonus)

        self.tiles = node(quests={'children': [
            ('tokenQuest', operation('main_operation', 'parts',
                node(credits=node(500), slots=node(1)))),
            ('tokenQuest', operation('honors_operation', 'honors',
                node(credits=node(1000), dossier=node(
                    name=node('playerBadges:20'), value=node('timestamp'),
                    type=node('set')))))]})
        patch = mock.patch.object(campaign, '_resource', return_value=self.tiles)
        patch.start()
        self.addCleanup(patch.stop)

    def settle(self, requested=None, now=100):
        if requested is not None:
            self.state.snapshot()['personalMissionRequestedCompleted'] = requested
        return campaign.settle(self.state, now=now, definitions=self.definitions)

    def test_honors_and_main_inverse_clear_only_their_stages_and_regrant_once(self):
        original = copy.deepcopy(self.state.snapshot()['wallet'])
        first = self.settle({'15': 2})
        self.assertEqual('', first['reset_error'])
        self.assertEqual([], first['pending'])
        self.assertEqual(101600, self.state.snapshot()['wallet']['credits'])
        self.assertEqual(31, self.state.snapshot()['accountSlots'])
        self.assertEqual(1, self.state.snapshot()['personalMissionOrders'])
        self.assertEqual([1, 2], first['missions'][0]['paid_stages'])
        self.assertEqual(2, len([row for row in first['operations'] if row['id'] != 'badges']))

        honors = self.settle({'15': 1}, now=101)
        self.assertEqual('', honors['reset_error'])
        self.assertEqual(100600, self.state.snapshot()['wallet']['credits'])
        self.assertEqual(original['gold'], self.state.snapshot()['wallet']['gold'])
        self.assertEqual(31, self.state.snapshot()['accountSlots'])
        self.assertEqual(0, self.state.snapshot()['personalMissionOrders'])
        self.assertEqual({'15': 1}, self.state.snapshot()['personalMissionRewarded'])
        self.assertEqual(['main_operation'], self.state.snapshot()['personalMissionTokenRewards'])
        self.assertEqual('honors_operation', honors['operations'][0]['id'])
        self.assertEqual('revoked', honors['operations'][0]['phase'])
        self.assertEqual(1, honors['missions'][0]['orders_revoked'])
        self.assertNotIn('20', self.state.snapshot()['accountBadges'])

        main = self.settle({}, now=102)
        self.assertEqual('', main['reset_error'])
        self.assertEqual(original, self.state.snapshot()['wallet'])
        self.assertEqual(30, self.state.snapshot()['accountSlots'])
        self.assertEqual({}, self.state.snapshot()['personalMissionRewarded'])
        self.assertEqual([], self.state.snapshot()['personalMissionTokenRewards'])
        self.assertFalse(any(key.startswith(('stage:', 'operation:')) for key in
                             self.state.snapshot()['personalMissionRewardJournal']))
        again = self.settle({'15': 2}, now=103)
        self.assertEqual([], again['pending'])
        self.assertEqual(101600, self.state.snapshot()['wallet']['credits'])
        self.assertEqual(1, self.state.snapshot()['personalMissionOrders'])
        before_duplicate = copy.deepcopy(self.state.snapshot())
        duplicate = self.settle(now=104)
        self.assertEqual([], duplicate['missions'])
        self.assertEqual([], duplicate['operations'])
        self.assertEqual(before_duplicate, self.state.snapshot())

    def test_spent_operation_reward_resets_and_reports_only_the_remaining_balance(self):
        self.settle({'15': 2})
        self.state.snapshot()['wallet']['credits'] = 600
        result = self.settle({}, now=101)
        self.assertEqual('', result['reset_error'])
        self.assertEqual(0, self.state._wallet()['credits'])
        self.assertEqual({}, self.state.snapshot()['personalMissionProgress'])
        self.assertEqual({}, self.state.snapshot()['personalMissionRewarded'])
        self.assertEqual(600, sum(reward['count'] for row in
            result['missions'] + result['operations'] for reward in row['rewards']
            if reward['kind'] == 'credits'))
        self.assertEqual(30, self.state.snapshot()['accountSlots'])
        self.assertEqual([], self.settle(now=102)['missions'])

    def test_owned_vehicle_spent_compensation_and_free_xp_reset_then_regrant_once(self):
        for credits_left, xp_left in ((0, 0), (1000, 200), (9000000, 45001)):
            self.state = vehicle_fixture.state_with_reward()
            if not any(tag == 'vehicle' for tag, row in campaign.child(
                    campaign.children(campaign.child(self.tiles, 'quests'), 'tokenQuest')[0],
                    'bonus')['children']):
                self.add_operation_vehicle()
                self.definitions[15]['main']['children'][0][1]['children'].append(
                    ('freeXP', node(45000)))
            original = copy.deepcopy(self.state.snapshot()['vehicles'])
            with mock.patch.object(vehicles, 'full_price_credits', return_value=6100000):
                self.settle({'15': 1})
            self.state._wallet().update(credits=credits_left, freeXP=xp_left)
            result = self.settle({}, now=101)
            self.assertEqual('', result['reset_error'])
            self.assertEqual(max(0, credits_left - 6100600), self.state._wallet()['credits'])
            self.assertEqual(max(0, xp_left - 45000), self.state._wallet()['freeXP'])
            self.assertEqual(original, self.state.snapshot()['vehicles'])
            rows = [reward for row in result['missions'] + result['operations']
                    for reward in row['rewards']]
            total = sum(row.get('credits', 0) if row['kind'] == 'compensation'
                        else row.get('count', 0) if row['kind'] == 'credits' else 0
                        for row in rows)
            self.assertEqual(min(credits_left, 6100600), total)
            self.assertEqual(min(xp_left, 45000), sum(row['count'] for row in rows
                                                   if row['kind'] == 'freeXP'))
            before = dict(self.state._wallet())
            with mock.patch.object(vehicles, 'full_price_credits', return_value=6100000):
                self.assertEqual([], self.settle({'15': 1}, now=102)['pending'])
            self.assertEqual(before['credits'] + 6100600, self.state._wallet()['credits'])
            self.assertEqual(before['freeXP'] + 45000, self.state._wallet()['freeXP'])
            self.assertEqual([], self.settle(now=103)['missions'])

    def test_component_notifications_use_the_receipted_token_delta(self):
        self.tiles['children'] = []
        component = 'token:pt:final:s1:t1'
        self.definitions = {15: {'main': quest(node(token=token(component, limit=node(5)))),
                                'add': quest(node())}}
        granted = self.settle({'15': 1})
        self.assertEqual([{'kind': 'token', 'id': component, 'count': 1}],
                         granted['missions'][0]['rewards'])
        revoked = self.settle({}, now=101)
        self.assertEqual([{'kind': 'token', 'id': component, 'count': 1}],
                         revoked['missions'][0]['rewards'])
        self.assertEqual({}, self.state.snapshot()['personalMissionTokens'])

    def test_badge_notification_is_actual_ownership_delta_without_duplicate_reward(self):
        self.state.snapshot()['accountBadges'] = {'20': 80}
        granted = self.settle({'15': 2})
        self.assertFalse(any(reward['kind'] == 'badge' for row in granted['operations']
                             for reward in row['rewards']))
        revoked = self.settle({'15': 1}, now=101)
        self.assertEqual([{'kind': 'badge', 'id': '20', 'count': 1}],
                         next(row['rewards'] for row in revoked['operations']
                              if row['id'] == 'badges'))

    def test_fixed_legacy_stage_can_migrate_and_reverse_without_a_prior_receipt(self):
        self.tiles['children'] = []
        self.definitions = {15: {'main': quest(node(credits=node(100))),
            'add': quest(node(gold=node(10), token=token('free_award_list')))}}
        self.state.snapshot().update(personalMissionProgress={'15': 2},
                                     personalMissionRewarded={'15': 2})
        self.state._wallet()['credits'] += 100
        self.state._wallet()['gold'] += 10
        result = self.settle({})
        self.assertEqual('', result['reset_error'])
        self.assertEqual(100000, self.state._wallet()['credits'])
        self.assertEqual(1000, self.state._wallet()['gold'])
        self.assertEqual({}, self.state.snapshot()['personalMissionRewarded'])
        self.assertEqual(0, self.state.snapshot()['personalMissionOrders'])

    def test_unknown_legacy_premium_interval_refuses_without_touching_currency(self):
        self.tiles['children'] = []
        self.definitions = {15: {'main': quest(node(credits=node(100), premium=node(1))),
                                'add': quest(node())}}
        self.state.snapshot().update(personalMissionProgress={'15': 1},
            personalMissionRewarded={'15': 1}, premiumExpiryTime=999999)
        self.state._wallet()['credits'] += 100
        result = self.settle({})
        self.assertIn('PREMIUM', result['reset_error'])
        self.assertEqual(100100, self.state._wallet()['credits'])
        self.assertEqual(999999, self.state.snapshot()['premiumExpiryTime'])
        self.assertEqual({'15': 1}, self.state.snapshot()['personalMissionRewarded'])

    def add_operation_vehicle(self):
        operation = campaign.children(campaign.child(self.tiles, 'quests'), 'tokenQuest')[0]
        campaign.child(operation, 'bonus')['children'].append(
            ('vehicle', node(vehicle_fixture.NAME)))

    def test_real_vehicle_receipt_withdraws_and_restores_without_another_crew(self):
        self.state = vehicle_fixture.state_with_reward()
        # Start with a legitimately held hull. The actual operation grant
        # must create a fresh receipt/source for the restored physical hull.
        vehicles.revoke(self.state, vehicle_fixture.receipt())
        self.add_operation_vehicle()
        original_crew = copy.deepcopy(self.state.snapshot()['barracksTankmen'])
        original_ammo = copy.deepcopy(self.state.snapshot()['inventoryItems'][10])
        granted = self.settle({'15': 1})
        self.assertEqual([], granted['pending'])
        operation = granted['operations'][0]
        hull = next(row for row in operation['rewards'] if row['kind'] == 'vehicle')
        self.assertTrue(hull['restored'])
        self.assertEqual(2, len(self.state.snapshot()['vehicles']))
        revoked = self.settle({}, now=101)
        self.assertEqual('', revoked['reset_error'])
        self.assertEqual(1, len(self.state.snapshot()['vehicles']))
        self.assertEqual(100000, self.state._wallet()['credits'])
        self.assertEqual(30, self.state.snapshot()['accountSlots'])
        self.assertEqual(original_crew, self.state.snapshot()['barracksTankmen'])
        self.assertEqual(original_ammo, self.state.snapshot()['inventoryItems'][10])
        again = self.settle({'15': 1}, now=102)
        self.assertEqual([], again['pending'])
        self.assertEqual(2, len(self.state.snapshot()['vehicles']))
        self.assertEqual(original_crew, self.state.snapshot()['barracksTankmen'])
        self.assertEqual(original_ammo, self.state.snapshot()['inventoryItems'][10])
        self.assertFalse(any(key.startswith('parkedVehicle:') for key in
                             self.state.snapshot()['personalMissionRewardJournal']))

    def test_owned_tank_compensation_is_recorded_and_retracted_without_the_tank(self):
        self.state = vehicle_fixture.state_with_reward()
        self.add_operation_vehicle()
        original = copy.deepcopy(self.state.snapshot()['vehicles'])
        with mock.patch.object(vehicles, 'full_price_credits', return_value=6100000):
            granted = self.settle({'15': 1})
        self.assertEqual([], granted['pending'])
        self.assertEqual(6200600, self.state._wallet()['credits'])
        rewards = granted['operations'][0]['rewards']
        self.assertEqual(500, sum(row['count'] for row in rewards if row['kind'] == 'credits'))
        self.assertEqual(6100000, next(row['credits'] for row in rewards
                                     if row['kind'] == 'compensation'))
        revoked = self.settle({}, now=101)
        self.assertEqual('', revoked['reset_error'])
        self.assertEqual(100000, self.state._wallet()['credits'])
        self.assertEqual(original, self.state.snapshot()['vehicles'])
        self.assertEqual(6100000, next(row['credits'] for row in
            revoked['operations'][0]['rewards'] if row['kind'] == 'compensation'))


if __name__ == '__main__':
    unittest.main()
