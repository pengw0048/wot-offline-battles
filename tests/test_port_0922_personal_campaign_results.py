"""Campaign cards use native main/add IDs and receipt-local battle facts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_port_0922_postbattle as fixture
from gui.mods.offline_lan_0922 import personal_campaign_results


class PersonalCampaignResultsTests(unittest.TestCase):
    def settlement(self):
        return {'completed': [270], 'operation_rewards': [], 'pending': [],
            'missions': [{'id': 270, 'before': 1, 'after': 2,
                'evaluated': True, 'main_quest': 'native_main_270',
                'add_quest': 'native_add_270', 'main_complete': True,
                'add_complete': True, 'paid_before': 1, 'paid_after': 2,
                'paid_stages': [2], 'rewards': [], 'orders_earned': 1,
                'orders_refunded': 4, 'tankwoman_pending': True}]}

    def test_pack_both_native_quest_ids_and_keep_daily_missions(self):
        receipt = fixture._receipt()
        receipt['daily_missions'] = ['battles']
        receipt['personal_missions'] = self.settlement()
        packers = fixture._Packers()
        with mock.patch.object(fixture.postbattle_store,
                '_vehicle_type_compact_descr', return_value=50001), \
                mock.patch.object(fixture.postbattle_store,
                '_arena_type_id', return_value=70001):
            fixture.postbattle_store.pack_battle_result(receipt,
                packers=packers, replay_types=(fixture._Replay,
                                               fixture._ReplayConnector))
        avatar = next(value for name, value in packers.calls
                      if name == 'AVATAR_FULL_RESULTS')
        self.assertEqual({'offline_daily_battles', 'native_main_270',
                          'native_add_270'}, set(avatar['questsProgress']))
        for identifier, row in avatar['questsProgress'].items():
            group, previous, current = row
            self.assertEqual(0, group)
            self.assertEqual(0 if identifier == 'native_main_270' else 1,
                current['bonusCount'] - previous['bonusCount'])

    def test_attempts_and_pending_old_rewards_have_distinct_card_behavior(self):
        settlement = self.settlement()
        mission = settlement['missions'][0]
        mission.update(main_complete=False, add_complete=False,
                       before=1, after=1)
        progress = personal_campaign_results.quests_progress(settlement)
        self.assertEqual(2, len(progress))
        self.assertTrue(all(row[2]['bonusCount'] - row[1]['bonusCount'] == 0
                            for row in progress.values()))
        # A late payment for an unrelated old mission is not this battle's
        # active task and must not create a misleading completed task card.
        mission['evaluated'] = False
        self.assertEqual({}, personal_campaign_results.quests_progress(settlement))

    def test_saved_pending_and_reopened_results_keep_the_same_campaign_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'postbattle_state.json')
            store = fixture.postbattle_store.PostBattleStore(path=path)
            receipt = fixture._receipt(store.account_key)
            settlement = self.settlement()
            expected = copy.deepcopy(settlement)
            calls = []

            def apply(row):
                calls.append(row['receipt_id'])
                return {'personal_missions': settlement}

            store.set_progress_applier(apply)
            self.assertTrue(store.accept(receipt))
            self.assertFalse(store.accept(receipt))
            self.assertEqual([receipt['receipt_id']], calls)
            settlement['missions'][0]['after'] = 0
            with open(path) as source:
                self.assertEqual(expected,
                    json.load(source)['pending'][0]['personal_missions'])
            restarted = fixture.postbattle_store.PostBattleStore(path=path)
            with mock.patch.object(fixture.postbattle_store,
                    '_vehicle_type_compact_descr', return_value=50001), \
                    mock.patch.object(fixture.postbattle_store,
                    '_arena_type_id', return_value=70001):
                first = restarted.service_message_data(receipt['arena_unique_id'])
                self.assertEqual(expected, first['offlinePersonalMissions'])
                first['offlinePersonalMissions']['missions'][0]['after'] = 0
                restarted.acknowledge(receipt['arena_unique_id'])
                reopened = restarted.service_message_data(receipt['arena_unique_id'])
                self.assertEqual(expected, reopened['offlinePersonalMissions'])


if __name__ == '__main__':
    unittest.main()
