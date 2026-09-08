import io
import json
import os
import tempfile
import unittest

import save_ledger
import save_slots


def _state(credits_amount=100000, gold=0, free_xp=0, **extra):
    state = {
        "schema": 5,
        "vehicles": {"50001": {"compDescr": "AAA="}},
        "owned": {"9": {"9001": 2}},
        "battleCrewReceipts": ["server:1:1"],
        "ledger": {
            "wallet": {
                "credits": credits_amount, "gold": gold, "freeXP": free_xp},
            "vehicleXP": {"50001": 4000},
            "unlocks": [50001],
            "slots": 30,
            "berths": 30,
        },
    }
    state.update(extra)
    return state


class SaveLedgerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.slot = "career"
        self.directory = os.path.join(self.root, self.slot)
        os.makedirs(self.directory)
        self.path = os.path.join(self.directory, "garage_state.json")

    def _write(self, state):
        with io.open(self.path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state))

    def _read(self):
        with io.open(self.path, "r", encoding="utf-8") as stream:
            return json.load(stream)

    def test_starting_balances_match_the_client_for_both_save_modes(self):
        from pathlib import Path
        import sys
        client = Path(__file__).resolve().parents[2] / 'src/res/scripts/client'
        sys.path.insert(0, str(client))
        self.addCleanup(sys.path.remove, str(client))
        from gui.mods.offline_lan_0922.account_rpc import economy
        self.assertEqual(economy.CAREER_WALLET,
                         save_ledger.DEFAULT_BALANCES[save_slots.MODE_NEW_ACCOUNT])
        self.assertEqual(economy.SANDBOX_WALLET,
                         save_ledger.DEFAULT_BALANCES[save_slots.MODE_UNLOCKED])

    def test_balances_are_read_from_the_saved_ledger(self):
        self._write(_state(credits_amount=250000, gold=1500, free_xp=90))

        self.assertEqual(
            {"credits": 250000, "gold": 1500, "freeXP": 90},
            save_ledger.read_balances(self.slot, root=self.root))

    def test_a_save_that_never_ran_reports_initial_balances(self):
        """A new save exposes the same initial wallet the client will use."""
        self.assertEqual(save_ledger.DEFAULT_BALANCES[save_slots.MODE_UNLOCKED],
                         save_ledger.read_balances(self.slot, root=self.root))

    def test_a_save_written_before_the_ledger_reports_initial_balances(self):
        state = _state()
        del state["ledger"]
        self._write(state)

        self.assertEqual(save_ledger.DEFAULT_BALANCES[save_slots.MODE_UNLOCKED],
                         save_ledger.read_balances(self.slot, root=self.root))

    def test_only_the_balances_change_and_the_garage_is_passed_through(self):
        self._write(_state())

        result = save_ledger.write_balances(
            self.slot, {"gold": 12500}, root=self.root,
            is_running=lambda: False)

        self.assertEqual(12500, result["gold"])
        stored = self._read()
        self.assertEqual(12500, stored["ledger"]["wallet"]["gold"])
        self.assertEqual(100000, stored["ledger"]["wallet"]["credits"])
        self.assertEqual({"50001": {"compDescr": "AAA="}}, stored["vehicles"])
        self.assertEqual({"9": {"9001": 2}}, stored["owned"])
        self.assertEqual(["server:1:1"], stored["battleCrewReceipts"])
        self.assertEqual({"50001": 4000}, stored["ledger"]["vehicleXP"])
        self.assertEqual(30, stored["ledger"]["slots"])

    def test_every_balance_can_be_set_at_once(self):
        self._write(_state())

        save_ledger.write_balances(
            self.slot, {"credits": 5, "gold": 6, "freeXP": 7},
            root=self.root, is_running=lambda: False)

        self.assertEqual(
            {"credits": 5, "gold": 6, "freeXP": 7},
            save_ledger.read_balances(self.slot, root=self.root))

    def test_a_negative_or_unreadable_amount_becomes_zero(self):
        self._write(_state())

        save_ledger.write_balances(
            self.slot, {"credits": -5, "gold": "many"},
            root=self.root, is_running=lambda: False)

        balances = save_ledger.read_balances(self.slot, root=self.root)
        self.assertEqual(0, balances["credits"])
        self.assertEqual(0, balances["gold"])

    def test_an_amount_beyond_the_client_field_is_capped(self):
        self._write(_state())

        save_ledger.write_balances(
            self.slot, {"gold": 10 ** 15}, root=self.root,
            is_running=lambda: False)

        self.assertEqual(
            save_ledger.MAX_BALANCE,
            save_ledger.read_balances(self.slot, root=self.root)["gold"])

    def test_a_newer_save_is_read_and_written_without_losing_anything(self):
        """The client owns this file's schema and every key the launcher
        does not know. Rebuilding the document instead of editing it would
        quietly delete whatever the next client version added -- the barracks
        crew, for one, who exist nowhere else."""
        state = _state(gold=1000)
        state["schema"] = 6
        state["ledger"]["barracks"] = ["dG1hbjoxMDE="]
        state["ledger"]["somethingNewer"] = {"kept": True}
        self._write(state)

        self.assertEqual(
            1000,
            save_ledger.read_balances(self.slot, root=self.root)["gold"])
        save_ledger.write_balances(
            self.slot, {"gold": 4000}, root=self.root,
            is_running=lambda: False)

        saved = self._read()
        self.assertEqual(6, saved["schema"])
        self.assertEqual(["dG1hbjoxMDE="], saved["ledger"]["barracks"])
        self.assertEqual({"kept": True}, saved["ledger"]["somethingNewer"])
        self.assertEqual(4000, saved["ledger"]["wallet"]["gold"])

    def test_a_running_game_owns_the_file_and_the_edit_is_refused(self):
        self._write(_state())

        with self.assertRaises(save_ledger.SaveLedgerError):
            save_ledger.write_balances(
                self.slot, {"gold": 1}, root=self.root,
                is_running=lambda: True)

        self.assertEqual(0, self._read()["ledger"]["wallet"]["gold"])

    def test_initial_balances_are_stored_without_inventing_a_garage(self):
        save_ledger.write_balances(
            self.slot, {"gold": 12500}, root=self.root,
            is_running=lambda: False)
        self.assertEqual(12500, save_ledger.read_balances(
            self.slot, root=self.root)["gold"])
        self.assertFalse(os.path.exists(self.path))
        with open(os.path.join(self.directory, 'save.json')) as stream:
            self.assertEqual(12500, json.load(stream)['initial_wallet']['gold'])
        self._write(_state(gold=9))
        self.assertEqual(9, save_ledger.read_balances(
            self.slot, root=self.root)["gold"])

    def test_default_legacy_wallet_is_editable_before_client_migration(self):
        legacy = os.path.join(self.root, 'garage_state.json')
        with open(legacy, 'w') as stream:
            json.dump(_state(gold=15), stream)
        root = os.path.join(self.root, 'saves')
        save_ledger.write_balances('default', {'gold': 21}, root=root,
                                  is_running=lambda: False)
        with open(legacy) as stream:
            self.assertEqual(21, json.load(stream)['ledger']['wallet']['gold'])
        self.assertFalse(os.path.exists(os.path.join(root, 'default', 'garage_state.json')))

    def test_a_damaged_save_is_reported_rather_than_overwritten(self):
        with io.open(self.path, "w", encoding="utf-8") as stream:
            stream.write(u"{not json")

        with self.assertRaises(save_ledger.SaveLedgerError):
            save_ledger.read_balances(self.slot, root=self.root)
        with self.assertRaises(save_ledger.SaveLedgerError):
            save_ledger.write_balances(
                self.slot, {"gold": 1}, root=self.root,
                is_running=lambda: False)

    def test_a_save_id_that_escapes_the_saves_directory_is_refused(self):
        with self.assertRaises(save_slots.SaveSlotError):
            save_ledger.ledger_path("../elsewhere", root=self.root)

    def test_a_failed_write_leaves_no_temporary_file_behind(self):
        self._write(_state())

        with self.assertRaises(save_ledger.SaveLedgerError):
            save_ledger._write_state(
                os.path.join(self.directory, "missing", "garage_state.json"),
                _state())

        self.assertEqual(
            ["garage_state.json"], sorted(os.listdir(self.directory)))


if __name__ == "__main__":
    unittest.main()
