"""Transactions and restart boundaries exposed by the economy review."""

import copy
import os
import tempfile
import unittest
from unittest import mock

import test_port_0922_garage as fixture


class EconomyRegressionTests(unittest.TestCase):
    def setUp(self):
        unused_requests, unused_commands, self.garage = fixture._request_modules()
        self.stores = fixture._load('garage_store')
        self.vehicles, self.tankmen = fixture._modules()
        self.stock = fixture.GaragePersistenceTests._matching_snapshot()
        self.stock['wallet']['freeXP'] = 1000
        modules = self.garage.mounted_module_items(
            self.vehicles.VehicleDescr(self.stock['vehicles'][0]['compDescr']))
        self.stock['vehicles'][0]['inventoryItems'].update(modules)
        self.types = {3333: 3, 4444: 4}
        for item_type, items in modules.items():
            for compact_descr in items:
                self.types[compact_descr] = item_type
                self.stock['inventoryItems'].setdefault(item_type, {})[
                    compact_descr] = 1
                self.stock['shopItemPrices'][compact_descr] = {'credits': 1000}
        self.stock['shopItemPrices'][4444] = {'credits': 2000}
        old_resolver = self.vehicles.getTypeOfCompactDescr
        self.vehicles.getTypeOfCompactDescr = lambda cd: self.types.get(
            cd, old_resolver(cd))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, 'garage.json')

    def state(self, snapshot=None):
        return self.garage.GarageState(
            self.stock if snapshot is None else snapshot,
            vehicles_module=self.vehicles, tankmen_module=self.tankmen)

    def restart(self, snapshot):
        store = self.stores.GarageStore(self.path)
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))
        fresh = copy.deepcopy(self.stock)

        def normalize(value):
            for record in value['vehicles']:
                record['inventoryItems'].update(
                    self.garage.mounted_module_items(
                        self.vehicles.VehicleDescr(record['compDescr'])))
        self.assertTrue(self.stores.GarageStore(self.path).apply(
            fresh, validator=normalize))
        return fresh

    def test_bought_copies_of_every_module_type_survive_restart(self):
        state = self.state()
        for item_type, items in self.garage.mounted_module_items(
                self.vehicles.VehicleDescr(b'veh:9')).items():
            compact_descr = next(iter(items))
            state.buy_item(compact_descr, 2)
        restored = self.restart(state.snapshot())
        for item_type, items in self.stock['vehicles'][0]['inventoryItems'].items():
            if item_type in range(2, 8):
                for compact_descr in items:
                    self.assertEqual(3, restored['inventoryItems'][item_type][compact_descr])
        self.assertEqual(88000, restored['wallet']['credits'])

    def test_installed_gun_cannot_be_sold_but_old_gun_can(self):
        state = self.state()
        state.install_component(9, 4444)
        with self.assertRaises(self.garage.GarageError):
            state.sell_item(4444)
        state.sell_item(7002)
        restored = self.restart(state.snapshot())
        self.assertEqual(0, restored['inventoryItems'][4].get(7002, 0))
        self.assertEqual(1, restored['inventoryItems'][4][4444])
        self.assertEqual(4444, self.vehicles.VehicleDescr(
            restored['vehicles'][0]['compDescr']).gun.compactDescr)

    def test_one_module_copy_cannot_be_installed_on_two_vehicles(self):
        snapshot = copy.deepcopy(self.stock)
        second = copy.deepcopy(snapshot['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002)
        snapshot['vehicles'].append(second)
        state = self.state(snapshot)
        state.install_component(9, 4444)
        with self.assertRaises(self.garage.GarageError):
            state.install_component(10, 4444)
        state.buy_item(4444)
        state.install_component(10, 4444)
        self.assertEqual(2, state._mounted(4444, 4, state._records()))

    def test_buy_and_install_can_purchase_a_previously_unowned_module(self):
        self.stock['inventoryItems'][4].pop(4444)
        state = self.state()
        state.buy_and_equip_item(9, 4444)
        self.assertEqual(98000, state.snapshot()['wallet']['credits'])
        self.assertEqual(1, state.snapshot()['inventoryItems'][4][4444])
        self.assertEqual(4444, self.vehicles.VehicleDescr(
            state.snapshot()['vehicles'][0]['compDescr']).gun.compactDescr)

    def test_refused_native_install_rolls_back_purchase_and_payment(self):
        self.stock['inventoryItems'][4].pop(4444)
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with mock.patch.object(fixture._Descriptor, 'installComponent',
                               side_effect=ValueError('native refusal')):
            with self.assertRaises(self.garage.GarageError):
                state.buy_and_equip_item(9, 4444)
        self.assertEqual(before, state.snapshot())
        self.assertEqual({}, state.touched_items())

    def test_training_spends_free_experience_once_and_survives_restart(self):
        state = self.state()
        state.train_tankman(101, 50)
        restored = self.restart(state.snapshot())
        self.assertEqual(950, restored['wallet']['freeXP'])
        self.assertEqual(500, self.tankmen.TankmanDescr(
            restored['vehicles'][0]['tankmen'][101]).totalXP())

    def test_unaffordable_or_refused_training_changes_nothing(self):
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(self.garage.GarageError):
            state.train_tankman(101, 1001)
        self.assertEqual(before, state.snapshot())
        with mock.patch.object(fixture._TankmanDescriptor, 'makeCompactDescr',
                               side_effect=ValueError('serialization refused')):
            with self.assertRaises(self.garage.GarageError):
                state.train_tankman(101, 50)
        self.assertEqual(before, state.snapshot())

    def test_settlement_retry_preserves_award_across_a_store_restart(self):
        snapshot = copy.deepcopy(self.stock)
        snapshot['earningsPercent'] = 250
        store = self.stores.GarageStore(self.path)
        args = dict(tankmen_module=self.tankmen, vehicles_module=self.vehicles,
                    rewards={'credits': 1000, 'xp': 200, 'free_xp': 10})
        first = store.apply_battle_crew_xp(
            snapshot, 'review:1:1', 50001, 200, 1, **args)
        wallet = dict(snapshot['wallet'])
        retry = self.stores.GarageStore(self.path).apply_battle_crew_xp(
            snapshot, 'review:1:1', 50001, 200, 1, **args)
        self.assertFalse(retry['applied'])
        self.assertEqual({'credits': 2500, 'xp': 500, 'free_xp': 25}, retry['awarded'])
        self.assertEqual(first['awarded'], retry['awarded'])
        self.assertEqual(wallet, snapshot['wallet'])

    def test_postbattle_write_failure_retries_without_repaying_or_losing_award(self):
        import test_port_0922_postbattle as postbattle
        snapshot = copy.deepcopy(self.stock)
        snapshot['earningsPercent'] = 250
        garage_store = self.stores.GarageStore(self.path)
        results = postbattle.postbattle_store.PostBattleStore(
            path=os.path.join(os.path.dirname(self.path), 'results.json'))
        receipt = postbattle._receipt(results.account_key)

        def settle(value):
            return garage_store.apply_battle_crew_xp(
                snapshot, value['receipt_id'], 50001, value['rewards']['xp'], 1,
                tankmen_module=self.tankmen, vehicles_module=self.vehicles,
                rewards=value['rewards'])
        results.set_progress_applier(settle)
        with mock.patch.object(results, '_save', side_effect=IOError('disk failure')):
            with self.assertRaises(IOError):
                results.accept(receipt)
        banked_wallet = dict(snapshot['wallet'])
        garage_store = self.stores.GarageStore(self.path)
        self.assertTrue(results.accept(receipt))
        self.assertEqual(banked_wallet, snapshot['wallet'])
        self.assertEqual(110500, snapshot['wallet']['credits'])
        self.assertEqual(10500, results.progress()['credits'])
        saved = results._pending[str(receipt['arena_unique_id'])]
        self.assertEqual(10500, postbattle._packed_vehicle(saved)['credits'])

    def test_non_elite_vehicle_banks_xp_despite_an_old_acceleration_setting(self):
        self.stock['vehicles'][0]['settings'] = 1
        original = self.vehicles.getVehicleType
        def vehicle_type(cd):
            value = original(cd)
            value.unlocksDescrs = [(500, 999999)]
            return value
        self.vehicles.getVehicleType = vehicle_type
        state = self.state()
        policy = state.award_battle_crew_xp(50001, 100, 1)
        state.award_battle_earnings(50001, {'xp': 100}, policy['accelerated'])
        restored = self.restart(state.snapshot())
        self.assertFalse(policy['accelerated'])
        self.assertEqual(100, restored['vehicleXP'][50001])
        for compact_descr in restored['vehicles'][0]['tankmen'].values():
            self.assertEqual(100, self.tankmen.TankmanDescr(compact_descr).totalXP())

    def test_empty_crew_still_banks_vehicle_experience_and_credits(self):
        self.stock['vehicles'][0].update(crew=[None, None], tankmen={}, settings=1)
        state = self.state()
        result = state.award_battle_crew_xp(50001, 100, 1)
        self.assertFalse(result['accelerated'])
        state.award_battle_earnings(50001, {'xp': 100, 'credits': 1000},
                                  result['accelerated'])
        self.assertEqual(100, state.snapshot()['vehicleXP'][50001])
        self.assertEqual(101000, state.snapshot()['wallet']['credits'])

    def test_duplicate_conversion_sources_cannot_mint_experience(self):
        self.stock['vehicleXP'] = {50001: 100}
        self.stock['wallet']['gold'] = 100
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(self.garage.GarageError):
            state.convert_to_free_xp([50001, 50001], 200)
        self.assertEqual(before, state.snapshot())
        state.convert_to_free_xp([50001, 50001], 100)
        self.assertEqual(0, state.snapshot()['vehicleXP'][50001])
        self.assertEqual(1100, state.snapshot()['wallet']['freeXP'])

    def test_failed_paid_device_swap_preserves_gold_and_the_old_device(self):
        self.stock['inventoryItems'][9] = {9001: 1, 9002: 1}
        self.stock['wallet']['gold'] = 100
        state = self.state()
        state.equip_optional_device(9, 9002, 0)
        state.touched_vehicles()
        state.touched_items()
        before = copy.deepcopy(state.snapshot())
        with mock.patch.object(fixture._Descriptor, 'installOptionalDevice',
                               side_effect=ValueError('native refusal')):
            with self.assertRaises(self.garage.GarageError):
                state.equip_optional_device(9, 9001, 0, paid_removal=True)
        self.assertEqual(before, state.snapshot())
        self.assertEqual(set(), state.touched_vehicles())
        self.assertEqual({}, state.touched_items())

    def test_buy_and_swap_preserves_the_device_when_paid_removal_is_selected(self):
        self.stock['inventoryItems'][9] = {9002: 1}
        self.stock['shopItemPrices'][9001] = {'credits': 1000}
        self.stock['wallet']['gold'] = 100
        self.stock['deviceRemovalCost'] = {'gold': 10}
        state = self.state()
        state.equip_optional_device(9, 9002, 0)
        state.buy_and_equip_item(9, 9001, paid_removal=True)
        self.assertEqual(90, state.snapshot()['wallet']['gold'])
        self.assertEqual(99000, state.snapshot()['wallet']['credits'])
        self.assertEqual(1, state.snapshot()['inventoryItems'][9][9002])

    def test_sale_failure_after_a_valid_item_does_not_remove_stock(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, vehicleTypeCompactDescr=50002, tankmen={}, crew=[])
        self.stock['vehicles'].append(second)
        self.stock['shopItemPrices'][50002] = {'credits': 1000}
        self.stock['inventoryItems'][4][4444] = 1
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(self.garage.GarageError):
            state.sell_vehicle(10, items_from_inventory=[4444, 999999])
        self.assertEqual(before, state.snapshot())
        self.assertEqual({}, state.touched_items())

    def test_unowned_vehicle_experience_survives_restart(self):
        self.stock['shopItemPrices'][50002] = {'credits': 5000}
        state = self.state()
        state.snapshot()['vehicleXP'] = {50001: 10, 50002: 321}
        restored = self.restart(state.snapshot())
        self.assertEqual(321, restored['vehicleXP'][50002])
        self.assertEqual([50001], [v['vehicleTypeCompactDescr']
                                  for v in restored['vehicles']])

    def test_sale_uses_the_upgraded_module_value_and_removes_its_copy(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                      tankmen={}, crew=[])
        self.stock['vehicles'].append(second)
        self.stock['shopItemPrices'][50002] = {'credits': 10000}
        for item_type, items in self.garage.mounted_module_items(
                self.vehicles.VehicleDescr(b'veh:10')).items():
            for cd in items:
                self.stock['inventoryItems'][item_type][cd] = 2
        state = self.state()
        state.install_component(10, 4444)
        state.sell_vehicle(10)
        # 5000 for the stock hull, minus 500 stock gun, plus 1000 new gun.
        self.assertEqual(105500, state.snapshot()['wallet']['credits'])
        self.assertEqual(0, state.snapshot()['inventoryItems'][4].get(4444, 0))
        self.assertEqual(2, state.snapshot()['inventoryItems'][4][7002])
        self.assertEqual(1, state.snapshot()['inventoryItems'][2][2002])

    def test_stack_sale_rounds_each_unit_before_multiplying(self):
        self.stock['shopItemPrices'][4444] = {'credits': 3}
        state = self.state()
        self.assertEqual({'credits': 6}, state._item_refund(4444, 3))

    def test_selling_with_a_retained_complex_device_charges_dismantling(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                      tankmen={}, crew=[])
        self.stock['vehicles'].append(second)
        self.stock['shopItemPrices'][50002] = {'credits': 10000}
        self.stock['inventoryItems'][9] = {9002: 1}
        self.stock['wallet']['gold'] = 100
        self.stock['deviceRemovalCost'] = {'gold': 10}
        state = self.state()
        state.equip_optional_device(10, 9002, 0)
        state.sell_vehicle(10)
        self.assertEqual(90, state.snapshot()['wallet']['gold'])
        self.assertEqual(1, state.snapshot()['inventoryItems'][9][9002])

    def test_duplicate_sale_entries_cannot_refund_a_carried_item_twice(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                      tankmen={}, crew=[])
        self.stock['vehicles'].append(second)
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with self.assertRaises(self.garage.GarageError):
            state.sell_vehicle(10, items_from_vehicle=[20010, 20010])
        self.assertEqual(before, state.snapshot())

    def test_actual_service_costs_survive_settlement_retry_and_result_restart(self):
        import test_port_0922_postbattle as postbattle
        record = self.stock['vehicles'][0]
        record['settings'] = 14
        self.stock['wallet']['gold'] = 100
        self.stock['inventoryItems'][10] = {20010: 30, 20011: 15}
        record['eqs'] = [11001, 0, 0]
        record['eqsLayout'] = [11001, 0, 0]
        record['inventoryItems'][11] = {11001: 1}
        self.stock['inventoryItems'][11] = {11001: 1}
        self.stock['shopItemPrices'][11001] = {'gold': 50}
        store = self.stores.GarageStore(self.path)
        args = dict(tankmen_module=self.tankmen, vehicles_module=self.vehicles,
                    health=400, shells_fired={0: 2}, equipment_used=[11001],
                    auto_settings=(2, 4, 8),
                    rewards={'credits': 1000, 'xp': 100, 'free_xp': 5})
        applied = store.apply_battle_crew_xp(
            self.stock, 'costs:1:1', 50001, 100, 1, **args)
        expected = dict(repair_credits=1200, ammo_credits=200, ammo_gold=0,
                        equipment_credits=0, equipment_gold=50)
        self.assertEqual(expected, applied['service_costs'])
        wallet = dict(self.stock['wallet'])
        retry = self.stores.GarageStore(self.path).apply_battle_crew_xp(
            self.stock, 'costs:1:1', 50001, 100, 1, **args)
        self.assertEqual(expected, retry['service_costs'])
        self.assertEqual(wallet, self.stock['wallet'])
        results_path = os.path.join(os.path.dirname(self.path), 'results.json')
        results = postbattle.postbattle_store.PostBattleStore(path=results_path)
        receipt = postbattle._receipt(results.account_key)
        results.set_progress_applier(lambda unused: retry)
        self.assertTrue(results.accept(receipt))
        restored = postbattle.postbattle_store.PostBattleStore(path=results_path)
        saved = restored._pending[str(receipt['arena_unique_id'])]
        packed = postbattle._packed_vehicle(saved)
        self.assertEqual(1200, packed['autoRepairCost'])
        self.assertEqual((200, 0), packed['autoLoadCost'])
        self.assertEqual((0, 50, 0), packed['autoEquipCost'])

    def test_a_refused_vehicle_step_still_banks_what_the_battle_earned(self):
        """A battle is worth what it is worth, whatever the tank has become.

        The crew award, the repair bill, the rounds and the consumables each
        settle one vehicle's own state and each can refuse -- a fitting this
        client will not rebuild, a vehicle the garage no longer holds.  None
        of them may cost the player the credits the battle earned.
        """
        record = self.stock['vehicles'][0]
        record['settings'] = 14
        record['eqs'] = [11001, 0, 0]
        self.stock['inventoryItems'][10] = {20010: 30}
        before = int(self.stock['wallet']['credits'])
        store = self.stores.GarageStore(self.path)

        # 59999 is no vehicle this garage holds, so every per-vehicle step
        # refuses for the same reason a changed one would.
        applied = store.apply_battle_crew_xp(
            self.stock, 'refused:1:1', 59999, 100, 1,
            tankmen_module=self.tankmen, vehicles_module=self.vehicles,
            health=400, shells_fired={0: 2}, equipment_used=[11001],
            auto_settings=(2, 4, 8),
            rewards={'credits': 1000, 'xp': 100, 'free_xp': 5})

        self.assertTrue(applied['applied'])
        self.assertEqual({'credits': 1000, 'xp': 100, 'free_xp': 5},
                         applied['awarded'])
        self.assertEqual(before + 1000, self.stock['wallet']['credits'])
        self.assertEqual(4, len(applied['refused']))
        self.assertEqual({}, applied['xp_by_tankman'])
        self.assertFalse(applied['accelerated'])
        # The refusal is durable too: a retried receipt observes the marker
        # rather than banking the award a second time.
        retry = self.stores.GarageStore(self.path).apply_battle_crew_xp(
            self.stock, 'refused:1:1', 59999, 100, 1,
            tankmen_module=self.tankmen, vehicles_module=self.vehicles,
            rewards={'credits': 1000, 'xp': 100, 'free_xp': 5})
        self.assertFalse(retry['applied'])
        self.assertEqual(before + 1000, self.stock['wallet']['credits'])

    def test_one_refused_step_does_not_stop_the_others(self):
        """Containment is per step, not per settlement."""
        record = self.stock['vehicles'][0]
        # A seat naming a crew member the barracks does not hold is what the
        # crew award refuses on; the vehicle itself is still there, so its
        # repair bill settles normally.
        record['crew'] = [999999] + list(record.get('crew') or ())[1:]
        before = int(self.stock['wallet']['credits'])
        store = self.stores.GarageStore(self.path)

        applied = store.apply_battle_crew_xp(
            self.stock, 'partial:1:1', 50001, 100, 1,
            tankmen_module=self.tankmen, vehicles_module=self.vehicles,
            health=400, rewards={'credits': 1000, 'xp': 100, 'free_xp': 5})

        self.assertEqual(1, len(applied['refused']))
        self.assertIn('crew experience', applied['refused'][0])
        self.assertEqual({}, applied['xp_by_tankman'])
        # The repair bill was still settled and the wallet still moved.
        self.assertIsNotNone(applied['repair'])
        self.assertEqual(1000, applied['awarded']['credits'])
        self.assertEqual(before + 1000, self.stock['wallet']['credits'])

    def test_mentor_uses_the_native_factor_for_other_crew_before_consumption(self):
        self.stock['vehicles'][0]['eqs'] = [11001, 0, 0]
        seen = []
        def tutor(crew, ammo):
            seen.append(([member.role for member in crew], ammo))
            return 0.1
        self.tankmen.commanderTutorXpBonusFactorForCrew = tutor
        state = self.state()
        state.award_battle_crew_xp(50001, 100, 1)
        crew = state.snapshot()['vehicles'][0]['tankmen']
        self.assertEqual(100, self.tankmen.TankmanDescr(crew[101]).totalXP())
        self.assertEqual(110, self.tankmen.TankmanDescr(crew[102]).totalXP())
        self.assertEqual([(['commander', 'driver'], [11001, 1])], seen)

    def test_vehicle_sale_can_sell_a_spare_of_the_module_leaving_with_it(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                      tankmen={}, crew=[])
        self.stock['vehicles'].append(second)
        self.stock['shopItemPrices'][50002] = {'credits': 10000}
        self.stock['inventoryItems'][4][7002] = 3
        state = self.state()
        state.sell_vehicle(10, items_from_inventory=[7002])
        self.assertEqual(1, state.snapshot()['inventoryItems'][4][7002])
        self.assertEqual(105500, state.snapshot()['wallet']['credits'])

    def test_refused_native_dismantling_does_not_charge_or_report_success(self):
        self.stock['inventoryItems'][9] = {9002: 1}
        self.stock['wallet']['gold'] = 100
        state = self.state()
        state.equip_optional_device(9, 9002, 0)
        before = copy.deepcopy(state.snapshot())
        with mock.patch.object(fixture._Descriptor, 'removeOptionalDevice',
                               side_effect=ValueError('native refusal')):
            with self.assertRaises(self.garage.GarageError):
                state.equip_optional_device(9, 0, 0, paid_removal=True)
        self.assertEqual(before, state.snapshot())

    def test_unloaded_consumable_can_be_sold_without_restarting(self):
        self.stock['inventoryItems'][11] = {11001: 1}
        self.stock['shopItemPrices'][11001] = {'credits': 3000}
        state = self.state()
        state.equip_equipments(9, [11001, 0, 0])
        state.equip_equipments(9, [0, 0, 0])
        self.assertEqual({}, state.snapshot()['vehicles'][0]['inventoryItems'][11])
        state.sell_item(11001)
        restored = self.restart(state.snapshot())
        self.assertEqual(101500, restored['wallet']['credits'])
        self.assertEqual(0, restored['inventoryItems'][11].get(11001, 0))

    def test_unloaded_consumable_moves_to_another_vehicle_without_repurchase(self):
        self.stock['inventoryItems'][11] = {11001: 1}
        self.stock['shopItemPrices'][11001] = {'credits': 3000}
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, vehicleTypeCompactDescr=50002)
        self.stock['vehicles'].append(second)
        state = self.state()
        state.equip_equipments(9, [11001, 0, 0])
        state.equip_equipments(9, [0, 0, 0])
        state.equip_equipments(10, [11001, 0, 0])
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])
        self.assertEqual(1, state.snapshot()['inventoryItems'][11][11001])

    def test_device_changes_keep_the_unfilled_shell_layout_and_currency(self):
        record = self.stock['vehicles'][0]
        record['shellsLayout'] = {(7001, 7002): [-20010, 30, 20011, 15]}
        state = self.state()
        state.settle_battle_ammunition(50001, {0: 5})
        expected_layout = copy.deepcopy(record['shellsLayout'])
        expected_shells = [20010, 25, 20011, 15]
        for compact_descr in (9001, 0):
            state.equip_optional_device(9, compact_descr, 0)
            current = state.snapshot()['vehicles'][0]
            self.assertEqual(expected_layout, current['shellsLayout'])
            self.assertEqual(expected_shells, current['shells'])
        restored = self.restart(state.snapshot())
        self.assertEqual(expected_layout, restored['vehicles'][0]['shellsLayout'])

    def test_engine_upgrade_keeps_the_unfilled_shell_layout(self):
        class EngineDescriptor(fixture._Descriptor):
            def __init__(self, compact_descr):
                super().__init__(compact_descr)
                if self.components.get(0) == 5555:
                    self.engine = fixture._Component(5555)

            def installComponent(self, compact_descr, position_index):
                self.components[position_index] = compact_descr
                self.engine = fixture._Component(compact_descr)

        self.vehicles.VehicleDescr = lambda compactDescr: EngineDescriptor(compactDescr)
        self.types[5555] = 5
        self.stock['inventoryItems'][5][5555] = 1
        self.stock['shopItemPrices'][5555] = {'credits': 1000}
        state = self.state()
        state.settle_battle_ammunition(50001, {0: 5})
        expected_layout = copy.deepcopy(state.snapshot()['vehicles'][0]['shellsLayout'])
        state.install_component(9, 5555)
        record = state.snapshot()['vehicles'][0]
        self.assertEqual(5555, self.vehicles.VehicleDescr(record['compDescr']).engine.compactDescr)
        self.assertEqual([20010, 25, 20011, 15], record['shells'])
        self.assertEqual(expected_layout, record['shellsLayout'])

    def _inventory_delta(self, state):
        return fixture._load('data').inventory(
            state.snapshot(), validate=False,
            only_vehicles=state.touched_vehicles(),
            only_items=state.touched_items())['inventory']

    def test_inventory_publishes_only_spare_copies_and_unloaded_rounds(self):
        self.stock['inventoryItems'][4][7002] = 3
        self.stock['inventoryItems'][10][20010] = 35
        state = self.state()
        state.equip_equipments(9, [11001, 0, 0])
        state.equip_optional_device(9, 9001, 0)
        inventory = fixture._load('data').inventory(
            state.snapshot(), validate=False)['inventory']
        self.assertEqual(2, inventory[4][7002])
        self.assertEqual(5, inventory[10][20010])
        self.assertEqual(199, inventory[9][9001])
        self.assertEqual(199, inventory[11][11001])
        self.assertEqual(35, state.snapshot()['inventoryItems'][10][20010])

    def test_equipment_delta_releases_the_old_copy_and_takes_the_new_one(self):
        self.stock['inventoryItems'][11] = {11001: 1, 11002: 1}
        self.stock['shopItemPrices'][11002] = {'credits': 3000}
        state = self.state()
        state.equip_equipments(9, [11001, 0, 0])
        self.assertEqual({11001: None}, self._inventory_delta(state)[11])
        state.equip_equipments(9, [11002, 0, 0])
        self.assertEqual({11001: 1, 11002: None}, self._inventory_delta(state)[11])
        state.equip_equipments(9, [0, 0, 0])
        self.assertEqual({11002: 1}, self._inventory_delta(state)[11])

    def test_device_delta_releases_a_dismantled_copy(self):
        self.stock['inventoryItems'][9] = {9001: 1}
        state = self.state()
        state.equip_optional_device(9, 9001, 0)
        self.assertEqual({9001: None}, self._inventory_delta(state)[9])
        state.equip_optional_device(9, 0, 0)
        self.assertEqual({9001: 1}, self._inventory_delta(state)[9])

    def test_narrow_transfer_delta_counts_copies_on_both_vehicles(self):
        self.stock['inventoryItems'][11] = {11001: 2}
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002)
        self.stock['vehicles'].append(second)
        state = self.state()
        state.equip_equipments(9, [11001, 0, 0])
        self.assertEqual({11001: 1}, self._inventory_delta(state)[11])
        state.equip_equipments(10, [11001, 0, 0])
        delta = self._inventory_delta(state)
        self.assertEqual({11001: None}, delta[11])
        self.assertEqual({10}, set(delta[1]['eqs']))
        state.equip_equipments(9, [0, 0, 0])
        self.assertEqual({11001: 1}, self._inventory_delta(state)[11])

    def test_component_delta_releases_the_old_gun_and_its_loaded_rounds(self):
        state = self.state()
        state.install_component(9, 4444)
        delta = self._inventory_delta(state)
        self.assertEqual({7002: 1, 4444: None}, delta[4])
        self.assertEqual({20010: 30, 20011: 15}, delta[10])

    def test_unloading_an_omitted_shell_type_updates_its_depot_count(self):
        state = self.state()
        state.equip_shells(9, [20010, 30])
        self.assertEqual({20010: None, 20011: 15}, self._inventory_delta(state)[10])

    def test_vehicle_sale_releases_its_retained_supplies_in_the_delta(self):
        second = copy.deepcopy(self.stock['vehicles'][0])
        second.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002)
        self.stock['vehicles'].append(second)
        self.stock['shopItemPrices'][50002] = {'credits': 10000}
        self.stock['inventoryItems'][10] = {20010: 60, 20011: 30}
        state = self.state()
        state.equip_optional_device(10, 9001, 0)
        state.equip_equipments(10, [11001, 0, 0])
        self._inventory_delta(state)
        state.sell_vehicle(10)
        delta = self._inventory_delta(state)
        self.assertEqual({20010: 30, 20011: 15}, delta[10])
        self.assertEqual({9001: 200}, delta[9])
        self.assertEqual({11001: 200}, delta[11])
