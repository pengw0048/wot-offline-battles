import io
import json
import os
import tempfile
import unittest
from unittest import mock

import gold_shop
import save_ledger


LOWE = {
    "nation": "germany", "vehicle": "G51_Lowe", "name": "germany:G51_Lowe",
    "label": "Lowe", "level": 8, "gold": 12500, "notInShop": False,
}
TYPE59 = {
    "nation": "china", "vehicle": "Ch01_Type59", "name": "china:Ch01_Type59",
    "label": "Type 59", "level": 8, "gold": 11500, "notInShop": True,
}


class GoldShopTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.slot = "career"
        self.directory = os.path.join(self.root, self.slot)
        os.makedirs(self.directory)
        self.game = tempfile.mkdtemp()
        patch = mock.patch.object(
            gold_shop.vehicle_overlays, "list_gold_vehicles",
            return_value=[dict(LOWE), dict(TYPE59)])
        patch.start()
        self.addCleanup(patch.stop)

    def _write_state(self, gold=0, vehicles=None):
        state = {
            "schema": 5,
            "vehicles": vehicles if vehicles is not None else {
                "1": {"name": "ussr:R11_MS-1"}},
            "ledger": {"wallet": {"credits": 0, "gold": gold, "freeXP": 0}},
        }
        path = os.path.join(self.directory, "garage_state.json")
        with io.open(path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state))
        return path

    def _inbox(self):
        path = os.path.join(self.directory, "launcher_inbox.json")
        if not os.path.isfile(path):
            return None
        with io.open(path, encoding="utf-8") as stream:
            return json.load(stream)

    def _buy(self, name, **kwargs):
        return gold_shop.add_vehicle(
            self.slot, name, self.game, root=self.root,
            is_running=lambda: False, **kwargs)

    def test_adding_preserves_the_wallet_and_queues_the_vehicle(self):
        path = self._write_state(gold=20000)

        result = self._buy("germany:G51_Lowe")

        self.assertEqual("germany:G51_Lowe", result["name"])
        self.assertEqual(
            {"schema": 1, "vehicles": ["germany:G51_Lowe"]}, self._inbox())
        with io.open(path, encoding="utf-8") as stream:
            self.assertEqual(
                20000, json.load(stream)["ledger"]["wallet"]["gold"])

    def test_a_vehicle_the_retail_shop_never_sold_is_still_offered(self):
        """145 of the 196 are reward tanks with no shop and no tree node."""
        self._write_state(gold=20000)

        self._buy("china:Ch01_Type59")

        self.assertEqual(["china:Ch01_Type59"], self._inbox()["vehicles"])

    def test_a_save_that_cannot_afford_the_vehicle_keeps_its_gold(self):
        path = self._write_state(gold=100)

        self._buy("germany:G51_Lowe")
        self.assertEqual(["germany:G51_Lowe"], self._inbox()["vehicles"])
        with io.open(path, encoding="utf-8") as stream:
            self.assertEqual(
                100, json.load(stream)["ledger"]["wallet"]["gold"])

    def test_a_vehicle_the_save_already_owns_cannot_be_bought_again(self):
        self._write_state(gold=20000, vehicles={
            "12345": {"compDescr": "AAA=", "name": "germany:G51_Lowe"}})

        with self.assertRaises(gold_shop.GoldShopError):
            self._buy("germany:G51_Lowe")

        self.assertIsNone(self._inbox())

    def test_legacy_unnamed_vehicles_are_deduplicated_by_the_client(self):
        self._write_state(gold=0, vehicles={"12345": {"compDescr": "AAA="}})
        self._buy("germany:G51_Lowe")
        self.assertEqual(["germany:G51_Lowe"], self._inbox()["vehicles"])
        self.assertEqual(0, save_ledger.read_balances(self.slot, root=self.root)["gold"])

    def test_a_vehicle_already_waiting_cannot_be_bought_again(self):
        self._write_state(gold=40000)
        self._buy("germany:G51_Lowe")

        with self.assertRaises(gold_shop.GoldShopError):
            self._buy("germany:G51_Lowe")

        self.assertEqual(["germany:G51_Lowe"], self._inbox()["vehicles"])
        self.assertEqual(
            40000,
            save_ledger.read_balances(self.slot, root=self.root)["gold"])

    def test_two_different_vehicles_both_wait_for_the_client(self):
        self._write_state(gold=40000)

        self._buy("germany:G51_Lowe")
        self._buy("china:Ch01_Type59")

        self.assertEqual(
            ["germany:G51_Lowe", "china:Ch01_Type59"],
            self._inbox()["vehicles"])
        self.assertEqual(
            40000,
            save_ledger.read_balances(self.slot, root=self.root)["gold"])

    def test_a_vehicle_this_client_does_not_ship_is_refused(self):
        self._write_state(gold=40000)

        with self.assertRaises(gold_shop.GoldShopError):
            self._buy("ussr:Object_Nothing")

        self.assertIsNone(self._inbox())

    def test_a_new_save_can_add_a_vehicle_without_creating_or_charging_a_wallet(self):
        self._buy("germany:G51_Lowe")
        self.assertEqual(["germany:G51_Lowe"], self._inbox()["vehicles"])
        self.assertFalse(os.path.exists(os.path.join(self.directory, 'garage_state.json')))
        with self.assertRaises(gold_shop.GoldShopError):
            self._buy("germany:G51_Lowe")

    def test_a_running_game_owns_the_save_and_the_purchase_is_refused(self):
        self._write_state(gold=40000)

        with self.assertRaises(gold_shop.GoldShopError):
            gold_shop.add_vehicle(
                self.slot, "germany:G51_Lowe", self.game, root=self.root,
                is_running=lambda: True)

        self.assertIsNone(self._inbox())
        self.assertEqual(
            40000, save_ledger.read_balances(self.slot, root=self.root)["gold"])

    def test_the_offers_say_what_this_save_can_do_about_each_vehicle(self):
        self._write_state(gold=12000, vehicles={
            "1": {"name": "china:Ch01_Type59"}})

        offers = dict(
            (offer["name"], offer)
            for offer in gold_shop.list_offers(
                self.slot, self.game, root=self.root))

        self.assertTrue(offers["china:Ch01_Type59"]["owned"])
        self.assertFalse(offers["germany:G51_Lowe"]["owned"])
        self.assertFalse(offers["china:Ch01_Type59"]["available"])
        self.assertTrue(offers["germany:G51_Lowe"]["available"])
        self.assertFalse(offers["germany:G51_Lowe"]["pending"])

    def test_an_offer_that_was_bought_reads_as_pending(self):
        self._write_state(gold=20000)
        self._buy("germany:G51_Lowe")

        offers = dict(
            (offer["name"], offer)
            for offer in gold_shop.list_offers(
                self.slot, self.game, root=self.root))

        self.assertTrue(offers["germany:G51_Lowe"]["pending"])
        self.assertFalse(offers["germany:G51_Lowe"]["owned"])

    def test_a_save_with_no_state_can_add_any_offered_vehicle(self):
        offers = gold_shop.list_offers(self.slot, self.game, root=self.root)

        self.assertEqual(2, len(offers))
        self.assertTrue(all(offer["available"] for offer in offers))


if __name__ == "__main__":
    unittest.main()
