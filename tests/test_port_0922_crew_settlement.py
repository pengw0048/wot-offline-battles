"""Crew award publication and same-session settlement retry boundaries."""

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import test_port_0922_garage as fixture
import test_port_0922_postbattle as result_fixture


class CrewSettlementTests(unittest.TestCase):
    def setUp(self):
        unused_requests, unused_commands, self.garage = fixture._request_modules()
        self.stores = fixture._load('garage_store')
        self.results = fixture._load('postbattle_store')
        self.vehicles, self.tankmen = fixture._modules()
        self.snapshot = fixture.GaragePersistenceTests._matching_snapshot()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.garage_path = os.path.join(directory.name, 'garage.json')
        self.result_path = os.path.join(directory.name, 'results.json')
        self.garage_store = self.stores.GarageStore(self.garage_path)
        self.result_store = self.results.PostBattleStore(self.result_path)
        self.result_store.set_progress_applier(self.settle)

    def settle(self, receipt):
        return self.garage_store.apply_battle_crew_xp(
            self.snapshot, receipt['receipt_id'], 50001,
            receipt['rewards']['xp'], 1, tankmen_module=self.tankmen,
            vehicles_module=self.vehicles, rewards=receipt['rewards'])

    def receipt(self):
        return result_fixture._receipt(self.result_store.account_key)

    def packed_vehicle(self, store, receipt):
        packers = result_fixture._Packers()
        with mock.patch.object(self.results, '_vehicle_type_compact_descr',
                               return_value=50001), mock.patch.object(
                self.results, '_arena_type_id', return_value=70001):
            store.result(receipt['arena_unique_id'], packers=packers,
                         replay_types=(result_fixture._Replay,
                                       result_fixture._ReplayConnector))
        return dict(packers.calls)['VEH_FULL_RESULTS']

    def test_result_publishes_actual_mentor_and_accelerated_awards(self):
        record = self.snapshot['vehicles'][0]
        record['settings'] = 1
        commander = self.tankmen.TankmanDescr(record['tankmen'][101])
        commander.addXP(100)
        record['tankmen'][101] = commander.makeCompactDescr()
        self.tankmen.commanderTutorXpBonusFactorForCrew = lambda crew, ammo: 0.1
        receipt = self.receipt()
        receipt['rewards'].update(xp=100, free_xp=5)
        receipt['public_results'][0]['xp'] = 100
        self.assertTrue(self.result_store.accept(receipt))
        self.assertEqual([(101, 100), (102, 220)], self.packed_vehicle(
            self.result_store, receipt)['xpByTmen'])
        crew = self.snapshot['vehicles'][0]['tankmen']
        self.assertEqual(200, self.tankmen.TankmanDescr(crew[101]).totalXP())
        self.assertEqual(220, self.tankmen.TankmanDescr(crew[102]).totalXP())
        self.assertEqual(0, self.snapshot['vehicleXP'].get(50001, 0))
        self.assertEqual(5, self.snapshot['wallet']['freeXP'])

    def test_failed_result_save_retries_without_losing_award_or_repaying(self):
        receipt = self.receipt()
        with mock.patch.object(self.result_store, '_save',
                               side_effect=IOError('disk failure')):
            with self.assertRaises(IOError):
                self.result_store.accept(receipt)
        banked = copy.deepcopy(self.snapshot)
        self.assertTrue(self.result_store.accept(receipt))
        self.assertEqual(banked, self.snapshot)
        self.assertEqual([(101, 600), (102, 600)], self.packed_vehicle(
            self.result_store, receipt)['xpByTmen'])
        self.assertFalse(self.result_store.accept(receipt))
        self.assertEqual(banked, self.snapshot)

    def test_restart_keeps_awards_but_drops_session_inventory_ids(self):
        receipt = self.receipt()
        self.result_store.accept(receipt)
        for path in (self.garage_path, self.result_path):
            with open(path) as stream:
                stored = json.load(stream)
            self.assertNotIn('xp_by_tankman', repr(stored))
            self.assertNotIn('session_crew_xp', repr(stored))
        restarted = self.results.PostBattleStore(self.result_path)
        self.assertEqual([], self.packed_vehicle(restarted, receipt)['xpByTmen'])
        wallet = dict(self.snapshot['wallet'])
        self.garage_store = self.stores.GarageStore(self.garage_path)
        retry = self.settle(receipt)
        self.assertFalse(retry['applied'])
        self.assertEqual({}, retry['xp_by_tankman'])
        self.assertEqual(wallet, self.snapshot['wallet'])

    def test_durable_retry_resolves_current_vehicle_id_after_reordering(self):
        receipt = self.receipt()
        self.settle(receipt)
        self.garage_store = self.stores.GarageStore(self.garage_path)
        self.snapshot['vehicles'][0]['id'] = 17
        retry = self.settle(receipt)
        self.assertEqual(17, retry['vehicle_id'])
        self.assertEqual({}, retry['xp_by_tankman'])

    def test_archived_result_keeps_current_session_crew_progress(self):
        receipt = self.receipt()
        self.result_store.accept(receipt)
        self.assertTrue(self.result_store.acknowledge(receipt['arena_unique_id']))
        self.assertEqual([(101, 600), (102, 600)], self.packed_vehicle(
            self.result_store, receipt)['xpByTmen'])

    def test_dossier_preserves_base_experience_separately_from_awards(self):
        self.snapshot['earningsPercent'] = 250
        receipt = self.receipt()
        self.result_store.accept(receipt)
        row = self.result_store.progress()['vehicles'][receipt['vehicle']]
        self.assertEqual(600, row['originalXP'])
        self.assertEqual(1500, row['xp'])
        restarted = self.results.PostBattleStore(self.result_path)
        self.assertEqual(row, restarted.progress()['vehicles'][receipt['vehicle']])


if __name__ == '__main__':
    unittest.main()
