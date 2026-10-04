"""Launcher-edited regular campaigns, orders and native account badges."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_port_0922_account_rpc as fixture


data = fixture.account_data


class PersonalProgressTests(unittest.TestCase):
    def test_completion_uses_native_storage_and_excludes_honored_active_mission(self):
        storage = mock.Mock()
        storage.return_value.makeCompDescr.return_value = b'native-pm-description'
        native = types.SimpleNamespace(PMStorage=storage,
            PM_STATE=types.SimpleNamespace(MAIN_REWARD_GOTTEN=3, ALL_REWARDS_GOTTEN=6))
        snapshot = {'personalMissionProgress': {'1': 1, '300': 2, '301': 2},
                    'personalMissionRewarded': {'1': 1, '300': 2},
                    'personalMissionTankwomen': {'300': True},
                    'personalMissionSelections': {'regular': [1, 300]}}
        with mock.patch.dict(sys.modules, {'personal_missions': native}):
            published = data.personal_missions(snapshot)
        storage.assert_called_once_with(storage={1: (0, 3), 300: (0, 6)})
        self.assertEqual(b'native-pm-description', published['compDescr'])
        self.assertEqual([1], published['regular']['selected'])
        self.assertEqual(5, published['regular']['slots'])

    def test_orders_publish_the_native_expiry_count_tuple(self):
        snapshot = copy.deepcopy(fixture.SELECTED_VEHICLE)
        snapshot['personalMissionOrders'] = 9
        self.assertEqual((4104777660, 9), data.sync_data(
            selected_vehicle=snapshot)['tokens']['free_award_list'])

    def test_badges_use_account_dossier_block_without_battle_or_medal_fabrication(self):
        class Dossier(dict):
            def makeCompDescr(self):
                return dict(self)
        result = data.account_dossier(badges={'1': 1700000000, '29': 1700000001},
            dossier_factory=lambda unused: Dossier(playerBadges={}))
        self.assertEqual({'playerBadges': {1: 1700000000, 29: 1700000001}}, result)

    def test_new_save_metadata_initializes_all_three_editors(self):
        from gui.mods.offline_lan_0922 import config
        notice = {'id': 'initial-badge', 'settlement': {'account_changes': [
            {'phase': 'granted', 'rewards': [{'kind': 'badge', 'id': 1, 'count': 1}]}]}}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / config.SAVE_METADATA_FILE_NAME
            path.write_text(json.dumps({'initial_personal_missions': {'1': 1, '300': 2},
                'initial_personal_orders': 8, 'initial_account_badges': {'1': 1700000000},
                'initial_account_notifications': [notice, None, {'id': 'invalid'}]}))
            with mock.patch.object(config, 'save_slot_dir', return_value=root):
                result = config.save_slot_initial_personal_progress()
        self.assertEqual({'1': 1, '300': 2}, result['personalMissionProgress'])
        self.assertEqual(8, result['personalMissionOrders'])
        self.assertEqual({'1': 1700000000}, result['accountBadges'])
        self.assertEqual([notice], result['personalMissionNotifications'])


if __name__ == '__main__':
    unittest.main()
