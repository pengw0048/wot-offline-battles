"""The exact #1513 command payloads the ledger handlers parse.

Every shape asserted here was read out of the shipped client bytecode rather
than guessed from a parameter name: ``Stats.unlock``,
``Stats.__exchange_onGetRate``, ``Stats.__convertToFreeXP_onGetParameters``,
``Stats.__slot_onShopSynced``, ``Stats.__berths_onShopSynced``,
``Shop.buyVehicle``, ``Inventory.__sellVehicle_onShopSynced``,
``Inventory.__sellItem_onShopSynced``, ``Inventory.equipTankman``,
``Inventory.dismissTankman``, ``Inventory.repair``,
``Inventory.__respecTman_onShopSynced``,
``Inventory.__multiRespecTman_onShopSynced``, ``Shop.buyTankman`` and
``Shop.buyAndEquipTankman``.  The economy suite proves the garage
transactions; this one proves the payload reaches them with every value in the
position the client actually sends it.
"""

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import requests as account_requests


class _RecordingGarage(object):
    """Record what the request layer asks the garage to do."""

    def __init__(self):
        self.calls = []

    def touched_vehicles(self):
        return set()

    def touched_items(self):
        return {}

    def touched_tankmen(self):
        return set()

    def snapshot(self):
        return {}

    # Shop.buyTankman's callback reads the new id out of the response.
    RESULTS = {'buy_tankman': 100005}

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.RESULTS.get(name)

        return record


class EconomyPayloadTests(unittest.TestCase):
    def _dispatch(self, command, args):
        garage = _RecordingGarage()
        result = account_requests.dispatch(command, {'garage': garage}, args)
        self.assertEqual(commands.RES_SUCCESS, result.result_id)
        return garage.calls

    def _respond(self, command, args):
        garage = _RecordingGarage()
        return account_requests.dispatch(command, {'garage': garage}, args)

    def _refuse(self, command, args):
        result = account_requests.dispatch(
            command, {'garage': _RecordingGarage()}, args)
        self.assertEqual(commands.RES_FAILURE, result.result_id)

    def test_unlock_carries_the_source_vehicle_and_the_step_index(self):
        # _doCmdInt3(CMD_UNLOCK, vehTypeCompDescr, unlockIdx, 0)
        self.assertEqual(
            [('unlock', (50001, 3), {})],
            self._dispatch(commands.CMD_UNLOCK, (50001, 3, 0)))

    def test_exchange_carries_the_gold_amount_after_the_shop_revision(self):
        # _doCmdInt3(CMD_EXCHANGE, shopRev, gold, 0)
        self.assertEqual(
            [('exchange_gold', (250,), {})],
            self._dispatch(commands.CMD_EXCHANGE, (17, 250, 0)))

    def test_free_experience_conversion_carries_the_vehicle_tail(self):
        # _doCmdIntArr(CMD_FREE_XP_CONV, [shopRev, xp, useDiscount] + cds)
        self.assertEqual(
            [('convert_to_free_xp', ([50001, 50002], 4000), {})],
            self._dispatch(
                commands.CMD_FREE_XP_CONV, ([17, 4000, 0, 50001, 50002],)))

    def test_a_slot_and_a_berth_pack_carry_nothing_but_the_revision(self):
        # _doCmdInt3(CMD_BUY_SLOT / CMD_BUY_BERTHS, shopRev, 0, 0)
        self.assertEqual(
            [('buy_slot', (), {})],
            self._dispatch(commands.CMD_BUY_SLOT, (17, 0, 0)))
        self.assertEqual(
            [('buy_berths', (), {})],
            self._dispatch(commands.CMD_BUY_BERTHS, (17, 0, 0)))

    def test_buying_a_vehicle_reads_the_shipped_flag_word(self):
        # _doCmdIntArr(CMD_BUY_VEHICLE,
        #   [cacheRev, typeCompDescr, flags, tmanCostTypeIdx, rentPeriod])
        # AccountCommands.BUY_VEHICLE_FLAG: NONE = 0, CREW = 1, SHELLS = 16.
        self.assertEqual(1, account_requests.BUY_VEHICLE_FLAG_CREW)
        self.assertEqual(16, account_requests.BUY_VEHICLE_FLAG_SHELLS)
        flags = (account_requests.BUY_VEHICLE_FLAG_CREW |
                 account_requests.BUY_VEHICLE_FLAG_SHELLS)
        self.assertEqual(
            [('buy_vehicle', (50001,), {
                'buy_shells': True, 'recruit_crew': True,
                'tman_cost_type_index': 0, 'rent_period': 0})],
            self._dispatch(
                commands.CMD_BUY_VEHICLE, ([42, 50001, flags, 0, 0],)))

    def test_buying_a_vehicle_without_extras_recruits_nothing(self):
        self.assertEqual(
            [('buy_vehicle', (50001,), {
                'buy_shells': False, 'recruit_crew': False,
                'tman_cost_type_index': 0, 'rent_period': 0})],
            self._dispatch(
                commands.CMD_BUY_VEHICLE, ([42, 50001, 0, 0, 0],)))

    def test_buying_an_item_carries_the_credit_price_switch(self):
        # _doCmdInt4(CMD_BUY_ITEM, cacheRev, itemShopID, count,
        # goldForCredits): the shop offers premium rounds and consumables for
        # credits, and this is the client asking for that price.
        self.assertEqual(
            [('buy_item', (10010, 20), {'gold_for_credits': False})],
            self._dispatch(commands.CMD_BUY_ITEM, (42, 10010, 20, 0)))
        self.assertEqual(
            [('buy_item', (11001, 1), {'gold_for_credits': True})],
            self._dispatch(commands.CMD_BUY_ITEM, (42, 11001, 1, 1)))

    def test_selling_an_item_reads_the_compact_descriptor_and_the_count(self):
        # _doCmdInt4(CMD_SELL_ITEM, shopRev, itemTypeIdx, itemInvID, count).
        # ModuleSeller passes the item's intCD as that inventory id.
        self.assertEqual(
            [('sell_item', (9001, 2), {})],
            self._dispatch(commands.CMD_SELL_ITEM, (17, 9, 9001, 2)))

    def test_selling_a_vehicle_splits_the_two_trailing_item_lists(self):
        # _doCmdIntArr(CMD_SELL_VEHICLE,
        #   [shopRev, vehInvID, isCrewDismiss, len(fromVehicle)] + fromVehicle
        #   + [len(fromInventory)] + fromInventory)
        self.assertEqual(
            [('sell_vehicle', (10,), {
                'dismiss_crew': True,
                'items_from_vehicle': [10010, 11001],
                'items_from_inventory': [9001]})],
            self._dispatch(
                commands.CMD_SELL_VEHICLE,
                ([17, 10, 1, 2, 10010, 11001, 1, 9001],)))

    def test_selling_a_vehicle_keeps_the_crew_flag_the_dialog_sent(self):
        self.assertEqual(
            [('sell_vehicle', (10,), {
                'dismiss_crew': False,
                'items_from_vehicle': [],
                'items_from_inventory': []})],
            self._dispatch(commands.CMD_SELL_VEHICLE, ([17, 10, 0, 0, 0],)))

    def test_retraining_carries_the_tankman_the_school_and_the_vehicle(self):
        # Inventory.__respecTman_onShopSynced -> _doCmdInt4(CMD_TMAN_RESPEC,
        #   shopRev, tmanInvID, tmanCostTypeIdx, vehTypeCompDescr)
        self.assertEqual(
            [('retrain_tankman', (100005, 1, 50002), {})],
            self._dispatch(commands.CMD_TMAN_RESPEC, (17, 100005, 1, 50002)))

    def test_retraining_a_crew_carries_the_vehicle_then_flat_pairs(self):
        # Inventory.__multiRespecTman_onShopSynced -> _doCmdIntArr(
        #   CMD_TMAN_MULTI_RESPEC,
        #   [shopRev, vehTypeCompDescr, tmanInvID, costIdx, ...])
        self.assertEqual(
            [('retrain_crew', (50002, [100005, 1, 100006, 0]), {})],
            self._dispatch(
                commands.CMD_TMAN_MULTI_RESPEC,
                ([17, 50002, 100005, 1, 100006, 0],)))

    def test_a_truncated_retraining_payload_is_refused_instead_of_guessed(self):
        for args in ((17, 100005, 1), (17,), ()):
            self._refuse(commands.CMD_TMAN_RESPEC, args)
        for args in (([17, 50002, 100005],), ([17],), ([],), ()):
            self._refuse(commands.CMD_TMAN_MULTI_RESPEC, args)

    def test_repairing_carries_nothing_but_the_vehicle(self):
        # Inventory.repair -> _doCmdInt3(CMD_REPAIR, vehInvID, 0, 0)
        self.assertEqual(
            [('repair_vehicle', (10,), {})],
            self._dispatch(commands.CMD_REPAIR, (10, 0, 0)))
        self._refuse(commands.CMD_REPAIR, ())

    def test_recruiting_carries_the_vehicle_type_the_role_and_the_school(self):
        # Shop.buyTankman -> _doCmdInt4(CMD_BUY_TMAN, cacheRev,
        #   makeIntCompactDescrByID('vehicle', nationIdx, innationIdx),
        #   tankmen.SKILL_INDICES[role], tmanCostTypeIdx)
        self.assertEqual(
            [('buy_tankman', (50001, 3, 1), {})],
            self._dispatch(commands.CMD_BUY_TMAN, (17, 50001, 3, 1)))

    def test_a_recruit_answers_with_the_inventory_id_the_callback_reads(self):
        """Shop.buyTankman's callback takes ext['tmanInvID'], not the cache."""
        result = self._respond(commands.CMD_BUY_TMAN, (17, 50001, 3, 1))

        self.assertEqual(commands.RES_SUCCESS, result.result_id)
        self.assertEqual({'tmanInvID': 100005}, result.ext)

    def test_recruiting_into_a_seat_carries_the_vehicle_seat_and_school(self):
        # Shop.buyAndEquipTankman -> _doCmdInt4(CMD_BUY_AND_EQUIP_TMAN,
        #   cacheRev, vehInvID, slot, tmanCostTypeIdx)
        self.assertEqual(
            [('buy_and_equip_tankman', (10, 2, 0), {})],
            self._dispatch(commands.CMD_BUY_AND_EQUIP_TMAN, (17, 10, 2, 0)))

    def test_a_truncated_recruit_payload_is_refused_instead_of_guessed(self):
        for args in ((17, 50001, 3), (17, 50001), (17,), ()):
            self._refuse(commands.CMD_BUY_TMAN, args)
            self._refuse(commands.CMD_BUY_AND_EQUIP_TMAN, args)

    def test_seating_a_crew_member_carries_the_vehicle_seat_and_tankman(self):
        # Inventory.equipTankman ->
        #   _doCmdInt3(CMD_EQUIP_TMAN, vehInvID, slot, tmanInvID)
        self.assertEqual(
            [('equip_tankman', (10, 2, 100005), {})],
            self._dispatch(commands.CMD_EQUIP_TMAN, (10, 2, 100005)))

    def test_unloading_a_seat_sends_minus_one_where_the_tankman_goes(self):
        # TankmanUnload passes None, which equipTankman turns into -1; a slot
        # of -1 alongside it unloads the whole crew.
        self.assertEqual(
            [('equip_tankman', (10, 3, -1), {})],
            self._dispatch(commands.CMD_EQUIP_TMAN, (10, 3, -1)))
        self.assertEqual(
            [('equip_tankman', (10, -1, -1), {})],
            self._dispatch(commands.CMD_EQUIP_TMAN, (10, -1, -1)))

    def test_dismissing_a_crew_member_carries_only_their_inventory_id(self):
        # Inventory.dismissTankman ->
        #   _doCmdInt3(CMD_DISMISS_TMAN, tmanInvID, 0, 0)
        self.assertEqual(
            [('dismiss_tankman', (100005,), {})],
            self._dispatch(commands.CMD_DISMISS_TMAN, (100005, 0, 0)))

    def test_a_truncated_crew_payload_is_refused_instead_of_guessed(self):
        for args in ((10, 2), (10,), ()):
            self._refuse(commands.CMD_EQUIP_TMAN, args)
        self._refuse(commands.CMD_DISMISS_TMAN, ())

    def test_a_truncated_sale_payload_is_refused_instead_of_guessed(self):
        for args in (([17, 10, 1, 2, 10010],), ([17, 10],), ([],), ()):
            self._refuse(commands.CMD_SELL_VEHICLE, args)
        self._refuse(commands.CMD_SELL_ITEM, (17, 9))


if __name__ == '__main__':
    unittest.main()
