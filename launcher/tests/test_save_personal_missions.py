import json
import os
import tempfile
import unittest
from unittest import mock

import save_ledger
import save_personal_missions as missions
import personal_missions_ui
from test_launcher_window import _FakeTk, _FakeTtk, _Root, _StringVar
from types import SimpleNamespace


class PersonalMissionEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.directory = os.path.join(self.root, "career")
        os.mkdir(self.directory)
        self.path = os.path.join(self.directory, "garage_state.json")
        self.kwargs = dict(root=self.root, is_running=lambda: False)

    def read(self, filename="garage_state.json"):
        with open(os.path.join(self.directory, filename)) as stream:
            return json.load(stream)

    def test_all_operations_and_chains_cover_exactly_300_missions(self):
        ids = [qid for operation in range(4) for chain in range(5)
               for qid in missions.mission_ids(operation, chain)]
        self.assertEqual(list(range(1, 301)), ids)

    def test_new_save_uses_metadata_and_does_not_fabricate_a_garage(self):
        missions.write_progress("career", {"1": 1, "300": 2}, **self.kwargs)
        self.assertFalse(os.path.exists(self.path))
        expected = {str(qid): 1 for qid in list(range(1, 226)) + list(range(286, 300))}
        expected["300"] = 2
        self.assertEqual(expected, self.read("save.json")[missions.INITIAL_KEY])
        self.assertEqual(0, missions.read_account_fields("career", root=self.root)["orders"])

    def test_existing_save_preserves_daily_goals_wallet_vehicles_and_other_chains(self):
        state = {"schema": 5, "vehicles": {"tank": {"shells": [1, 2]}},
                 "ledger": {"wallet": {"gold": 123},
                            "personalMissions": {"regular": [1, 16], "orders": 4},
                            "offlineServices": {"dailyMissions": {"damage": 500}}}}
        with open(self.path, "w") as stream:
            json.dump(state, stream)
        missions.write_progress("career", {"1": 2, "16": 1, "300": 2}, **self.kwargs)
        saved = self.read()
        self.assertEqual(state["vehicles"], saved["vehicles"])
        self.assertEqual(state["ledger"]["wallet"], saved["ledger"]["wallet"])
        self.assertEqual(state["ledger"]["offlineServices"], saved["ledger"]["offlineServices"])
        self.assertEqual([1, 16], saved["ledger"]["personalMissions"]["regular"])
        self.assertEqual([16], saved["ledger"]["personalMissions"]["requestedRegular"])
        self.assertEqual(4, saved["ledger"]["personalMissions"]["orders"])
        expected = {str(qid): 1 for qid in list(range(1, 226)) + list(range(286, 300))}
        expected.update({"1": 2, "300": 2})
        self.assertEqual(expected, missions.read_progress("career", root=self.root))

    def test_invalid_or_running_edits_do_not_write(self):
        for progress in ({"0": 1}, {"301": 1}, {"1": 3}, {"1": True}):
            with self.assertRaises(save_ledger.SaveLedgerError):
                missions.write_progress("career", progress, **self.kwargs)
        with self.assertRaises(save_ledger.SaveLedgerError):
            missions.write_progress("career", {"1": 1}, root=self.root, is_running=lambda: True)
        self.assertFalse(os.path.exists(os.path.join(self.directory, "save.json")))

    def test_badge_removal_clears_equipped_badge_and_preserves_dates(self):
        state = {"schema": 5, "ledger": {"accountBadges": {"1": 100, "2": 200},
                 "offlineServices": {"selectedBadges": [1], "badgeSelectionVerified": True}}}
        with open(self.path, "w") as stream:
            json.dump(state, stream)
        with mock.patch.object(missions, "badge_catalogue", return_value=[{"id": 1}, {"id": 2}]):
            missions.write_account_fields("career", badges=[2], **self.kwargs)
            self.assertEqual({"2": 200}, self.read()["ledger"]["accountBadges"])
            self.assertEqual([], self.read()["ledger"]["offlineServices"]["selectedBadges"])
            notices = self.read()['ledger']['personalMissions']['notifications']
            self.assertEqual([{'phase': 'revoked', 'rewards': [
                {'kind': 'badge', 'id': '1', 'count': 1}]}],
                notices[0]['settlement']['account_changes'])
            missions.write_account_fields('career', badges=[2], **self.kwargs)
            self.assertEqual(notices, self.read()['ledger']['personalMissions']['notifications'])
            with self.assertRaises(save_ledger.SaveLedgerError):
                missions.write_account_fields("career", badges=[999], **self.kwargs)

    def test_initial_badge_and_wallet_changes_share_a_durable_notice_queue(self):
        with mock.patch.object(missions, 'badge_catalogue', return_value=[{'id': 1}]):
            missions.write_account_fields('career', badges=[1], **self.kwargs)
            save_ledger.write_balances('career', {'credits': 1234}, **self.kwargs)
            missions.write_account_fields('career', badges=[1], **self.kwargs)
        metadata = self.read('save.json')
        notices = metadata['initial_account_notifications']
        self.assertEqual(2, len(notices))
        self.assertNotEqual(notices[0]['id'], notices[1]['id'])
        self.assertEqual([{'kind': 'badge', 'id': '1', 'count': 1}],
            notices[0]['settlement']['account_changes'][0]['rewards'])
        self.assertEqual([{'kind': 'credits', 'count': 100000000 - 1234}],
            notices[1]['settlement']['account_changes'][0]['rewards'])
        self.assertEqual('revoked', notices[1]['settlement']['account_changes'][0]['phase'])

    def test_failed_badge_save_writes_neither_ownership_nor_notification(self):
        original = {'schema': 5, 'ledger': {'accountBadges': {'1': 100}}}
        with open(self.path, 'w') as stream:
            json.dump(original, stream)
        with mock.patch.object(missions, 'badge_catalogue', return_value=[{'id': 1}, {'id': 2}]), \
                mock.patch.object(save_ledger.os, 'replace', side_effect=OSError('busy')):
            with self.assertRaises(save_ledger.SaveLedgerError):
                missions.write_account_fields('career', badges=[2], **self.kwargs)
        self.assertEqual(original, self.read())

    def test_failed_atomic_replace_preserves_original(self):
        missions.write_progress("career", {"1": 1}, **self.kwargs)
        with mock.patch.object(save_ledger.os, "replace", side_effect=OSError("busy")):
            with self.assertRaises(save_ledger.SaveLedgerError):
                missions.write_progress("career", {"1": 2}, **self.kwargs)
        self.assertEqual({"1": 1}, missions.read_progress("career", root=self.root))

    def test_badge_catalogue_reads_resource_dictionary_ids_and_native_labels(self):
        px = missions.vehicle_overlays.packed_xml
        def text(value):
            return px.PackedValue(px.TYPE_STRING, value.encode())
        def element(children, value=None):
            return px.PackedValue(px.TYPE_ELEMENT, px.PackedElement(value, children))
        badge = element([(b'name', text('ranked_s1_gold')), (b'value', element([
            (b'item', element([(b'name', text('id')), (b'type', text('int'))], text('29'))),
            (b'item', element([(b'name', text('weight')), (b'type', text('float'))], text('5.10'))),
        ]))])
        tree = px.PackedElement(children=[(b'badges', element([(b'badge', badge)]))])
        with mock.patch.object(missions.vehicle_overlays, '_require_target',
                return_value=({'path': self.root}, 'scripts.pkg')), mock.patch.object(
                missions, '_read_badge_catalogue', return_value=tree):
            self.assertEqual([{'id': 29, 'label': 'ranked_s1_gold', 'weight': 5.1}],
                             missions.badge_catalogue(self.root))

    def test_ui_honors_dependency_and_switching_keep_unsaved_edits(self):
        owner = SimpleNamespace(_tk=_FakeTk, _ttk=_FakeTtk, _t=lambda text: text,
            _save_slot_id="career", game_root=_StringVar(""), save_dialog=_Root(),
            _busy=False, _maintenance_busy=False)
        with mock.patch.object(missions, "read_progress", return_value={}), mock.patch.object(
                missions, "read_edit_status", return_value={"pending": False, "error": ""}):
            dialog = personal_missions_ui.PersonalMissionsDialog(owner)
        label, completed, honors = dialog.rows[0]
        honors.set(True)
        dialog.changed(0, "honors")
        self.assertTrue(completed.get())
        self.assertEqual({"1": 2}, dialog.progress)
        completed.set(False)
        dialog.changed(0, "completed")
        self.assertFalse(honors.get())
        self.assertEqual({}, dialog.progress)
        dialog.set_chain(1)
        dialog.operation.set(dialog.operations[3])
        dialog.chain.set(dialog.chains[4])
        dialog.refresh()
        dialog.set_chain(2)
        self.assertEqual(1, dialog.progress["1"])
        self.assertEqual(2, dialog.progress["300"])
        with mock.patch.object(missions, "write_progress", side_effect=lambda slot, progress, root: progress) as write:
            self.assertTrue(dialog.save())
            write.assert_called_once_with("career", dialog.progress, None)

    def test_final_requires_fourteen_tasks_and_previous_operations_without_honors(self):
        progress = missions.edit_progress({}, [270], 2)
        self.assertEqual(set(map(str, list(range(1, 226)) + list(range(256, 271)))), set(progress))
        self.assertEqual(2, progress["270"])
        self.assertTrue(all(value == 1 for key, value in progress.items() if key != "270"))
        # Ordinary missions within an operation are not sequential.
        self.assertEqual({"14": 1}, missions.edit_progress({}, [14], 1))

    def test_reset_prerequisite_cascades_but_honors_downgrade_does_not(self):
        progress = missions.edit_progress({}, [270], 2)
        progress["1"] = 2
        downgraded = missions.edit_progress(progress, [1], 1)
        self.assertEqual(2, downgraded["270"])
        reset = missions.edit_progress(progress, [1], 0)
        self.assertNotIn("1", reset)
        self.assertNotIn("15", reset)
        self.assertTrue(all(int(key) <= 75 for key in reset))
        self.assertEqual(1, reset["75"])

    def test_every_main_reset_clears_its_final_and_all_five_later_classes(self):
        progress = {str(qid): 2 for qid in range(1, 301)}
        for qid in range(1, 301):
            with self.subTest(qid=qid):
                final = ((qid - 1) // 15 + 1) * 15
                last_allowed = ((qid - 1) // 75 + 1) * 75
                expected = {str(other): 2 for other in range(1, last_allowed + 1)
                            if other not in (qid, final)}
                self.assertEqual(expected, missions.edit_progress(progress, [qid], 0))
                expected_honors = dict(progress, **{str(qid): 1})
                self.assertEqual(expected_honors, missions.edit_progress(progress, [qid], 1))

    def test_sparse_old_progress_cannot_leave_later_classes_after_main_reset(self):
        progress = {"1": 1, "16": 2, "90": 2, "180": 1, "270": 2, "300": 1}
        self.assertEqual({"16": 2}, missions.edit_progress(progress, [1], 0))
        with open(self.path, "w") as stream:
            json.dump({"schema": 5, "ledger": {"personalMissions": {
                "completed": progress}}}, stream)
        # Persistence also enforces the rule for callers outside the dialog.
        requested = dict(progress)
        requested.pop("1")
        self.assertEqual({"16": 2}, missions.write_progress("career", requested, **self.kwargs))
        self.assertEqual({"16": 2}, missions.read_progress("career", root=self.root))

    def test_honors_downgrade_preserves_order_skipped_progress_on_save(self):
        progress = {"15": 2, "90": 2, "270": 2}
        expected = dict(progress, **{"15": 1})
        with open(self.path, "w") as stream:
            json.dump({"schema": 5, "ledger": {"personalMissions": {
                "completed": progress}}}, stream)
        self.assertEqual(expected, missions.edit_progress(progress, [15], 1))
        self.assertEqual(expected, missions.write_progress("career", expected, **self.kwargs))
        self.assertEqual(expected, missions.read_progress("career", root=self.root))

    def test_reset_mt15_queues_selection_and_preserves_committed_rewards_until_withdrawal(self):
        progress = missions.edit_progress({}, [270], 2)
        state = {"schema": 5, "ledger": {"wallet": {"credits": 987},
                 "personalMissions": {"completed": progress, "rewarded": dict(progress),
                 "regular": [1, 16, 31, 46, 61], "orders": 21, "pawned": {"270": 4},
                 "tankwomen": {"45": True, "270": True}, "tokenRewards": ["operation4"]}}}
        with open(self.path, "w") as stream:
            json.dump(state, stream)
        missions.write_progress("career", missions.edit_progress(progress, [270], 0), **self.kwargs)
        saved = self.read()["ledger"]
        edited = saved["personalMissions"]
        self.assertNotIn("270", edited["requestedCompleted"])
        self.assertEqual(progress, edited["completed"])
        self.assertEqual(progress, edited["rewarded"])
        self.assertEqual([1, 16, 31, 46, 61], edited["regular"])
        self.assertEqual([1, 16, 46, 61, 270], edited["requestedRegular"])
        self.assertEqual(21, edited["orders"])
        self.assertEqual({"270": 4}, edited["pawned"])
        self.assertEqual({"45": True, "270": True}, edited["tankwomen"])
        self.assertEqual(["operation4"], edited["tokenRewards"])
        self.assertEqual({"credits": 987}, saved["wallet"])
        # Reopening and saving a pending reset must not change live property.
        self.assertEqual({"pending": True, "error": ""},
                         missions.read_edit_status("career", root=self.root))
        missions.write_progress("career", edited["requestedCompleted"], **self.kwargs)
        self.assertEqual(edited, self.read()["ledger"]["personalMissions"])

    def test_rejected_client_withdrawal_is_visible_and_next_edit_clears_error(self):
        state = {"schema": 5, "ledger": {"personalMissions": {
            "completed": {"1": 2}, "rewarded": {"1": 2},
            "resetError": "Not enough free orders to reset these missions."}}}
        with open(self.path, "w") as stream:
            json.dump(state, stream)
        self.assertEqual({"1": 2}, missions.read_progress("career", root=self.root))
        self.assertTrue(missions.read_edit_status("career", root=self.root)["error"])
        missions.write_progress("career", {}, **self.kwargs)
        self.assertEqual({}, missions.read_progress("career", root=self.root))
        self.assertEqual({"pending": True, "error": ""},
                         missions.read_edit_status("career", root=self.root))

    def test_launcher_leaves_balance_for_client_entitlement_reconciliation(self):
        state = {"schema": 5, "ledger": {"personalMissions": {"orders": 22}}}
        with open(self.path, "w") as stream:
            json.dump(state, stream)
        missions.write_progress("career", {"1": 1}, **self.kwargs)
        self.assertEqual(22, missions.read_account_fields("career", root=self.root)["orders"])


if __name__ == "__main__":
    unittest.main()
