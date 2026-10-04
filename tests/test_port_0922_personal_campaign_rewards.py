"""Real garage economic rewards remain conserved across reset and replay."""
import copy
import json
import sys
import types
import unittest
from unittest import mock

import test_port_0922_economy as fixture
from gui.mods.offline_lan_0922 import personal_campaign as campaign
from gui.mods.offline_lan_0922 import personal_campaign_rewards as rewards


def node(text='', **fields):
    return {'value': str(text), 'children': list(fields.items())}


class ReversibleRewardsTests(unittest.TestCase):
    def grant(self, state, bonus, now=100):
        with state._transaction():
            return rewards.record_grant(state, bonus, now,
                lambda: campaign._grant_bonus(state, bonus, now))

    def test_actual_economy_stage_round_trip_excludes_later_battle_income(self):
        state = fixture._state()
        initial = state._balances()
        bonus = node(credits=node(100), gold=node(7), freeXP=node(20),
                     crystal=node(3), slots=node(1), berths=node(2),
                     item=node(9001, count=node(2)))
        receipt = self.grant(state, bonus)
        receipt = json.loads(json.dumps(receipt))
        state._wallet()['credits'] += 777
        rows = rewards.revoke(state, receipt, 200)
        expected = dict(initial, credits=initial['credits'] + 777)
        self.assertEqual(expected, state._balances())
        self.assertEqual(2, state.snapshot()['inventoryItems'][9][9001])
        self.assertEqual(30, state.snapshot()['accountSlots'])
        self.assertEqual(30, state.snapshot()['accountBerths'])
        self.assertIn({'kind': 'credits', 'count': 100}, rows)
        self.assertIn({'kind': 'item', 'id': 9001, 'count': 2}, rows)
        second = self.grant(state, bonus)
        self.assertEqual(receipt['effects'], second['effects'])
        rewards.revoke(state, second, 200)
        self.assertEqual(expected, state._balances())

    def test_vehicle_compensation_is_captured_without_xml_currency_node(self):
        state = fixture._state()
        effect = {'kind': 'compensation', 'vehicle': 'reward:tank', 'credits': 6000000}
        def grant():
            state._wallet()['credits'] += 6000000
            return {'vehicles': [effect]}
        receipt = rewards.record_grant(state, node(vehicle=node('reward:tank')), 100, grant)
        self.assertEqual([effect], receipt['vehicles'])
        self.assertEqual([{'kind': 'credits', 'count': 6000000}], receipt['rewards'])
        rewards.revoke(state, receipt, 100)
        self.assertEqual(100000, state._balances()['credits'])

    def test_hull_inventory_is_not_mistaken_for_an_explicit_item_award(self):
        state = fixture._state()
        def grant():
            state._set_owned(2002, 2, 2)
            state._set_owned(9001, 9, 4)
        receipt = rewards.record_grant(state,
            node(vehicle=node('reward:tank'), item=node(9001, count=node(2))), 100, grant)
        self.assertEqual([{'kind': 'item', 'id': 9001, 'count': 2}], receipt['rewards'])
        rewards.revoke(state, receipt, 100)
        self.assertEqual(2, state.snapshot()['inventoryItems'][2][2002])
        self.assertEqual(2, state.snapshot()['inventoryItems'][9][9001])

    def test_all_reward_currencies_stop_at_the_remaining_balance(self):
        state = fixture._state()
        receipt = self.grant(state, node(credits=node(100), gold=node(2000),
                                        freeXP=node(300), crystal=node(50)))
        state._wallet().update(credits=0, gold=1, freeXP=2, crystal=3)
        rows = rewards.revoke(state, receipt, 100)
        self.assertEqual(dict.fromkeys(rewards.WALLET_NAMES, 0), state._balances())
        self.assertEqual({'gold': 1, 'freeXP': 2, 'crystal': 3},
                         {row['kind']: row['count'] for row in rows})

    def test_duplicate_spent_currency_rows_share_the_remaining_balance(self):
        state = fixture._state()
        state._wallet().update(credits=70, freeXP=5)
        receipt = {'version': 1, 'effects': [
            {'kind': 'wallet', 'name': 'credits', 'count': 50},
            {'kind': 'wallet', 'name': 'credits', 'count': 50},
            {'kind': 'wallet', 'name': 'freeXP', 'count': 10}]}
        rows = rewards.revoke(state, receipt, 100)
        self.assertEqual([{'kind': 'credits', 'count': 50},
                          {'kind': 'credits', 'count': 20},
                          {'kind': 'freeXP', 'count': 5}], rows)
        self.assertEqual(0, state._wallet()['credits'])
        self.assertEqual(0, state._wallet()['freeXP'])
        self.assertEqual([], rewards.revoke(state, receipt, 101))

    def test_spent_item_rewards_clear_remaining_depot_stock_and_keep_mounted_items(self):
        for available, mounted in ((0, 0), (1, 0), (2, 1), (1, 1), (5, 1)):
            state = fixture._state()
            receipt = self.grant(state, node(credits=node(100), item=node(9001, count=node(2))))
            state._set_owned(9001, 9, available)
            if mounted:
                state.snapshot()['vehicles'][0]['inventoryItems'][9] = {9001: 1}
            records = copy.deepcopy(state.snapshot()['vehicles'])
            rows = rewards.revoke(state, receipt, 100)
            count = min(2, available - mounted)
            self.assertEqual(available - count, state.snapshot()['inventoryItems'][9].get(9001, 0))
            self.assertEqual(count, sum(row['count'] for row in rows if row['kind'] == 'item'))
            self.assertEqual(records, state.snapshot()['vehicles'])
            self.assertEqual(100000, state._balances()['credits'])

    def test_capacity_rewards_only_withdraw_free_slots_and_berths(self):
        for kind, key in (('slots', 'accountSlots'), ('berths', 'accountBerths')):
            state = fixture._state()
            state.snapshot()[key] = 0
            receipt = self.grant(state, node(**{kind: node(1)}))
            if kind == 'berths':
                state.snapshot()['barracksTankmen'] = {999: b'other-crew'}
            self.assertEqual([], rewards.revoke(state, receipt, 100))
            self.assertEqual(1, state.snapshot()[key])
            state.snapshot()[key] = 2
            receipt['effects'][0]['count'] = 3
            self.assertEqual([{'kind': kind, 'count': 1}], rewards.revoke(state, receipt, 100))
            self.assertEqual(1, state.snapshot()[key])

    def test_spent_consumable_reset_clears_claim_and_regrants_once(self):
        state = fixture._state()
        definitions = {1: {'main': node(bonus=node(item=node(9001, count=node(5)))),
                           'add': node(bonus=node())}}
        state.snapshot()['personalMissionRequestedCompleted'] = {'1': 1}
        self.assertEqual('', campaign.settle(state, now=100, definitions=definitions)['reset_error'])
        state.snapshot()['personalMissionRewardJournal'] = json.loads(json.dumps(
            state.snapshot()['personalMissionRewardJournal']))
        state._set_owned(9001, 9, 1)
        state.snapshot()['personalMissionRequestedCompleted'] = {}
        result = campaign.settle(state, now=101, definitions=definitions)
        self.assertEqual('', result['reset_error'])
        self.assertNotIn(9001, state.snapshot()['inventoryItems'][9])
        self.assertEqual({}, state.snapshot()['personalMissionRewarded'])
        self.assertIn({'kind': 'item', 'id': 9001, 'count': 1}, result['missions'][0]['rewards'])
        state.snapshot()['personalMissionRequestedCompleted'] = {'1': 1}
        campaign.settle(state, now=102, definitions=definitions)
        self.assertEqual(5, state.snapshot()['inventoryItems'][9][9001])
        campaign.settle(state, now=103, definitions=definitions)
        self.assertEqual(5, state.snapshot()['inventoryItems'][9][9001])

    def test_missing_or_partial_customization_and_counters_stop_at_zero(self):
        for remaining in (0, 1):
            state = fixture._state()
            state.snapshot()['customizationItems'] = {2: {123: {50001: remaining, 50002: 4}}}
            state.snapshot()['personalMissionDossier'] = {'counter': remaining}
            receipt = self.customization_receipt()
            receipt['effects'].append({'kind': 'dossier', 'mode': 'add', 'name': 'counter', 'count': 5})
            rows = rewards.revoke(state, receipt, 100)
            self.assertEqual(0, state.snapshot()['personalMissionDossier']['counter'])
            self.assertEqual(0, state.snapshot()['customizationItems'][2][123].get(50001, 0))
            self.assertEqual(4, state.snapshot()['customizationItems'][2][123][50002])
            self.assertEqual([1, 1] if remaining else [], [row['count'] for row in rows])

    def test_additive_and_set_dossier_restore_without_erasing_unrelated_progress(self):
        state = fixture._state()
        state.snapshot()['personalMissionDossier'] = {'counter': 3, 'last': 12}
        bonus = {'children': [('dossier', node(name=node('counter'), type=node('add'), value=node(5))),
                              ('dossier', node(name=node('last'), type=node('set'), value=node('timestamp')))]}
        receipt = self.grant(state, bonus)
        state.snapshot()['personalMissionDossier']['counter'] += 8
        rewards.revoke(state, receipt, 101)
        self.assertEqual({'counter': 11, 'last': 12}, state.snapshot()['personalMissionDossier'])

    def test_overwritten_set_dossier_refuses_without_changing_balance(self):
        state = fixture._state()
        bonus = node(credits=node(100), dossier=node(name=node('last'), type=node('set'), value=node(10)))
        receipt = self.grant(state, bonus)
        state.snapshot()['personalMissionDossier']['last'] = 11
        before = copy.deepcopy(state.snapshot())
        with self.assertRaisesRegex(rewards.GarageError, 'DOSSIER_CHANGED'):
            rewards.revoke(state, receipt, 101)
        self.assertEqual(before, state.snapshot())

    def test_orders_tokens_and_badges_remain_owned_by_their_canonical_rebuilders(self):
        state = fixture._state()
        bonus = {'children': [('token', node(id=node('component'), count=node(1))),
                              ('token', node(id=node('free_award_list'), count=node(1))),
                              ('dossier', node(name=node('playerBadges:10'), value=node('timestamp')))]}
        receipt = self.grant(state, bonus)
        self.assertEqual([], receipt['effects'])
        rewards.revoke(state, receipt, 100)
        self.assertEqual(1, state.snapshot()['personalMissionOrders'])
        self.assertEqual(1, state.snapshot()['personalMissionTokens']['component'][1])
        self.assertEqual(100, state.snapshot()['accountBadges']['10'])

    def test_premium_reclaims_unconsumed_award_without_reclaiming_later_purchase(self):
        state = fixture._state()
        receipt = self.grant(state, node(premium=node(3)), now=100)
        state.snapshot()['premiumExpiryTime'] += 10 * rewards.DAY
        rows = rewards.revoke(state, receipt, 100 + rewards.DAY)
        self.assertEqual(100 + 11 * rewards.DAY, state.snapshot()['premiumExpiryTime'])
        self.assertEqual([{'kind': 'premium', 'count': 2.0, 'seconds': 2 * rewards.DAY}], rows)

    def test_premium_before_award_is_preserved_and_expired_award_has_no_debit(self):
        state = fixture._state()
        state.snapshot()['premiumExpiryTime'] = 100 + 10 * rewards.DAY
        receipt = self.grant(state, node(premium=node(3)), now=100)
        rewards.revoke(state, receipt, 100 + rewards.DAY)
        self.assertEqual(100 + 10 * rewards.DAY, state.snapshot()['premiumExpiryTime'])
        state = fixture._state()
        receipt = self.grant(state, node(premium=node(3)), now=100)
        state.snapshot()['premiumExpiryTime'] = 100 + 8 * rewards.DAY
        self.assertEqual([], rewards.revoke(state, receipt, 100 + 4 * rewards.DAY))
        self.assertEqual(100 + 8 * rewards.DAY, state.snapshot()['premiumExpiryTime'])

    def test_separate_premium_resets_shift_later_receipt_before_second_reset(self):
        state = fixture._state()
        first = self.grant(state, node(premium=node(3)), now=100)
        second = self.grant(state, node(premium=node(3)), now=100)
        state.snapshot()['personalMissionRewardJournal'] = {'stage:1:main': first, 'stage:2:main': second}
        rewards.revoke(state, first, 100)
        state.snapshot()['personalMissionRewardJournal'].pop('stage:1:main')
        self.assertEqual(100, second['effects'][0]['start'])
        self.assertEqual(100 + 3 * rewards.DAY, second['effects'][0]['end'])
        rows = rewards.revoke(state, second, 100 + 2 * rewards.DAY)
        self.assertEqual([{'kind': 'premium', 'count': 1.0, 'seconds': rewards.DAY}], rows)
        self.assertEqual(100 + 2 * rewards.DAY, state.snapshot()['premiumExpiryTime'])

    def test_shortened_premium_withdraws_only_the_remaining_earned_interval(self):
        state = fixture._state()
        receipt = self.grant(state, node(credits=node(100), premium=node(3)), now=100)
        state.snapshot()['premiumExpiryTime'] = 100 + rewards.DAY
        rows = rewards.revoke(state, receipt, 100)
        self.assertEqual(100, state.snapshot()['premiumExpiryTime'])
        self.assertIn({'kind': 'premium', 'count': 1.0, 'seconds': rewards.DAY}, rows)
        self.assertEqual(100000, state._balances()['credits'])

    def test_legacy_fixed_stage_reconstructs_only_recorded_resource_payouts(self):
        state = fixture._state()
        bonus = node(credits=node(100), item=node(9001, count=node(2)),
                     slots=node(1), berths=node(2),
                     dossier=node(name=node('counter'), type=node('add'), value=node(2)))
        paid = self.grant(state, bonus)
        migrated = rewards.legacy_receipt(state, bonus, 100)
        self.assertEqual(sorted(paid['effects'], key=str), sorted(migrated['effects'], key=str))
        rewards.revoke(state, migrated, 100)
        self.assertEqual(100000, state._balances()['credits'])
        self.assertEqual(2, state.snapshot()['inventoryItems'][9][9001])

    def test_legacy_active_premium_and_set_dossier_never_guess_their_sources(self):
        state = fixture._state()
        state.snapshot()['premiumExpiryTime'] = 10000
        for bonus, reason in ((node(premium=node(3)), 'PREMIUM_PROVENANCE_MISSING'),
                              (node(dossier=node(name=node('last'), value=node(2))), 'DOSSIER_PROVENANCE_MISSING')):
            before = copy.deepcopy(state.snapshot())
            with self.assertRaisesRegex(rewards.GarageError, reason):
                rewards.legacy_receipt(state, bonus, 100)
            self.assertEqual(before, state.snapshot())
        self.assertEqual([], rewards.legacy_receipt(state, node(premium=node(3)), 10001)['effects'])

    def test_repeated_rows_are_aggregated_and_malformed_receipts_do_not_overdraw(self):
        state = fixture._state()
        bonus = {'children': [('credits', node(100)), ('credits', node(200)),
                              ('item', node(9001, count=node(1))), ('item', node(9001, count=node(2)))]}
        receipt = self.grant(state, bonus)
        self.assertIn({'kind': 'credits', 'count': 300}, receipt['rewards'])
        self.assertIn({'kind': 'item', 'id': 9001, 'count': 3}, receipt['rewards'])
        bad = {'version': 1, 'effects': [
            {'kind': 'wallet', 'name': 'gold', 'count': 700},
            {'kind': 'wallet', 'name': 'gold', 'count': -1}]}
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(rewards.GarageError):
            rewards.revoke(state, bad, 100)
        self.assertEqual(before, state.snapshot())

    def test_customization_count_is_exact_and_preserves_other_bound_vehicles(self):
        state = fixture._state()
        constants = types.SimpleNamespace(CustomizationType=types.SimpleNamespace(CAMOUFLAGE=2))
        components = types.ModuleType('items.components')
        components.c11n_constants = constants
        state._vehicles.VehicleDescr = lambda **kwargs: types.SimpleNamespace(type=types.SimpleNamespace(id=(0, 1)))
        state._vehicles.makeIntCompactDescrByID = lambda kind, nation, vehicle: 50001
        state.snapshot()['customizationItems'] = {2: {123: {50001: 2, 50002: 4}}}
        bonus = node(customizations=node(item=node(custType=node('camouflage'),
            id=node(123), boundVehicle=node('nation:tank'), value=node(3))))
        with mock.patch.dict(sys.modules, {'items.components': components}):
            receipt = self.grant(state, bonus)
            migrated = rewards.legacy_receipt(state, bonus, 100)
        self.assertEqual(receipt['effects'], migrated['effects'])
        rows = rewards.revoke(state, receipt, 100)
        self.assertEqual({2: {123: {50001: 2, 50002: 4}}}, state.snapshot()['customizationItems'])
        self.assertEqual([{'kind': 'customization', 'id': 123, 'cust_type': 2,
                          'vehicle': 50001, 'count': 3}], rows)

    def test_real_failed_grant_rolls_back_callback_and_does_not_create_receipt(self):
        state = fixture._state()
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(Exception):
            self.grant(state, node(credits=node(100), item=node(99999)))
        self.assertEqual(before, state.snapshot())

    def customization_receipt(self):
        return {'version': 1, 'effects': [{'kind': 'customization',
            'customization_type': 2, 'item_id': 123, 'vehicle_type': 50001, 'count': 3}]}

    def outfit_state(self):
        state = fixture._state()
        state.snapshot()['customizationItems'] = {2: {123: {50001: 3}}}
        payload = {'camouflages': [{'id': 123, 'appliedTo': 3}, {'id': 456, 'appliedTo': 4}],
                   'paints': [8, 9], 'decals': [17], 'styleId': 0}
        state.snapshot()['vehicles'][0]['outfits'] = {1: (json.dumps(payload), True)}
        class Outfit(object):
            def __init__(self, descriptor):
                self.payload = json.loads(descriptor)
                self.camouflages = [types.SimpleNamespace(**row) for row in self.payload['camouflages']]
            def makeCompDescr(self):
                payload = dict(self.payload)
                payload['camouflages'] = [vars(row) for row in self.camouflages]
                return json.dumps(payload)
        state._customizations = types.SimpleNamespace(parseOutfitDescr=Outfit)
        return state, payload

    def test_equipped_reward_camouflage_is_removed_without_replacing_other_outfit_items(self):
        state, original = self.outfit_state()
        rewards.revoke(state, self.customization_receipt(), 100)
        descriptor, fitted = state.snapshot()['vehicles'][0]['outfits'][1]
        expected = dict(original, camouflages=[{'id': 456, 'appliedTo': 4}])
        self.assertEqual(expected, json.loads(descriptor))
        self.assertTrue(fitted)
        self.assertIn(9, state._touched)
        self.assertEqual({}, state.snapshot()['customizationItems'][2][123])

    def test_unknown_equipped_outfit_or_failed_round_trip_refuses_complete_reset(self):
        for corrupt in ('unknown_components', 'serializer_drops_changes'):
            state, original = self.outfit_state()
            if corrupt == 'unknown_components':
                state._customizations.parseOutfitDescr = lambda unused: types.SimpleNamespace()
            else:
                parser = state._customizations.parseOutfitDescr
                def unchanging(descriptor):
                    result = parser(descriptor)
                    result.makeCompDescr = lambda: json.dumps(original)
                    return result
                state._customizations.parseOutfitDescr = unchanging
            before = copy.deepcopy(state.snapshot())
            receipt = self.customization_receipt()
            receipt['effects'].insert(0, {'kind': 'wallet', 'name': 'credits', 'count': 100})
            with self.assertRaisesRegex(rewards.GarageError, 'CUSTOMIZATION_OUTFIT_UNAVAILABLE'):
                rewards.revoke(state, receipt, 100)
            self.assertEqual(before, state.snapshot())
            self.assertEqual(set(), state._touched)

    def test_later_outfit_parse_failure_rolls_back_already_changed_earlier_season(self):
        state, unused = self.outfit_state()
        state.snapshot()['vehicles'][0]['outfits'][2] = ('unreadable', True)
        before = copy.deepcopy(state.snapshot())
        with self.assertRaisesRegex(rewards.GarageError, 'CUSTOMIZATION_OUTFIT_UNAVAILABLE'):
            rewards.revoke(state, self.customization_receipt(), 100)
        self.assertEqual(before, state.snapshot())
        self.assertEqual(set(), state._touched)


if __name__ == '__main__':
    unittest.main()
