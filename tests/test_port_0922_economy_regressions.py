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
