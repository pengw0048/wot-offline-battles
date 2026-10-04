"""Reset conservation: earned orders and the source-recorded woman only."""
import copy
import unittest

import test_port_0922_account_rpc as fixture
from gui.mods.offline_lan_0922 import personal_campaign_ledger as ledger


class CampaignInverseTests(unittest.TestCase):
    def state(self, **fields):
        snapshot = copy.deepcopy(fixture.SELECTED_VEHICLE)
        snapshot.update(personalMissionOrders=5, personalMissionPawned={'30': 4},
            accountBerths=3, barracksTankmen={500: b'reward-woman', 501: b'other-crew'},
            personalMissionDossier={ledger.TANKWOMAN_DOSSIER_KEY: 3})
        snapshot.update(fields)
        return fixture.account_requests.garage.GarageState(snapshot)

    def effect(self, **fields):
        result = {'tankman': 500, 'dossier_count': 1}
        result.update(fields)
        return result

    def test_revoke_earned_order_preserves_free_pawn_refunds_and_other_pledges(self):
        state = self.state()
        self.assertEqual(1, ledger.revoke_orders(state, 1))
        self.assertEqual(4, state.snapshot()['personalMissionOrders'])
        self.assertEqual({'30': 4}, state.snapshot()['personalMissionPawned'])
        self.assertEqual(4, ledger.revoke_orders(state, 5))
        self.assertEqual(0, state.snapshot()['personalMissionOrders'])
        self.assertEqual(0, ledger.revoke_orders(state, 1))
        self.assertEqual({'30': 4}, state.snapshot()['personalMissionPawned'])

    def test_revoke_woman_retains_berth_and_removes_only_her_dossier_increment(self):
        state = self.state(lastCrew=[500, 501])
        self.assertEqual(500, ledger.revoke_tankwoman(state, self.effect()))
        snapshot = state.snapshot()
        self.assertEqual({501: b'other-crew'}, snapshot['barracksTankmen'])
        self.assertEqual(3, snapshot['accountBerths'])
        self.assertEqual(2, snapshot['personalMissionDossier'][ledger.TANKWOMAN_DOSSIER_KEY])
        self.assertEqual([None, 501], snapshot['lastCrew'])
        self.assertIn(500, state._touched_tankmen)

    def test_remapped_source_id_does_not_remove_an_unrelated_original_id(self):
        state = self.state(barracksTankmen={500: b'other-crew', 807: b'reward-woman'})
        self.assertEqual(807, ledger.revoke_tankwoman(state, self.effect(tankman=807)))
        self.assertEqual({500: b'other-crew'}, state.snapshot()['barracksTankmen'])

    def test_exact_source_can_be_retrained_seated_or_dismissed(self):
        for fields in ({'barracksTankmen': {500: b'better-trained-reward'}},
                       {'barracksTankmen': {}, 'tankmen': {500: b'retrained-seated'},
                        'crew': [500, None]},
                       {'barracksTankmen': {},
                        'recycleBinTankmen': {500: (b'changed-dismissed', 100)}}):
            state = self.state(**fields)
            self.assertEqual(500, ledger.revoke_tankwoman(state, self.effect()))
            snapshot = state.snapshot()
            self.assertNotIn(500, snapshot.get('barracksTankmen', {}))
            self.assertNotIn(500, snapshot.get('tankmen', {}))
            self.assertNotIn(500, snapshot.get('recycleBinTankmen', {}))
            self.assertNotIn(500, snapshot.get('crew', []))
            self.assertEqual(3, snapshot['accountBerths'])

    def test_unverified_or_missing_source_never_searches_for_similar_crew(self):
        for identity in (0, 888):
            state = self.state()
            before = copy.deepcopy(state.snapshot())
            with self.assertRaises(ledger.GarageError):
                ledger.revoke_tankwoman(state, self.effect(tankman=identity))
            self.assertEqual(before, state.snapshot())

    def test_reward_woman_dossier_count_never_becomes_negative(self):
        state = self.state(personalMissionDossier={ledger.TANKWOMAN_DOSSIER_KEY: 0})
        self.assertEqual(500, ledger.revoke_tankwoman(state, self.effect()))
        self.assertNotIn(500, state.snapshot()['barracksTankmen'])
        self.assertEqual(0, state.snapshot()['personalMissionDossier'][ledger.TANKWOMAN_DOSSIER_KEY])

    def test_caller_transaction_restores_prior_inverse_when_a_later_receipt_is_invalid(self):
        state = self.state(personalMissionOrders=0)
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(ledger.GarageError):
            with state._transaction():
                ledger.revoke_tankwoman(state, self.effect())
                ledger.revoke_orders(state, -1)
        self.assertEqual(before, state.snapshot())
        self.assertEqual(set(), state._touched_tankmen)
        self.assertEqual(0, state.revision)

    def test_deleted_reward_identity_is_reserved_from_future_recruits(self):
        state = self.state(personalMissionRewardJournal={
            'crew:15': {'tankman': 900}, 'crewBonus:15': True})
        self.assertEqual(901, state._next_tankman_id())


if __name__ == '__main__':
    unittest.main()
