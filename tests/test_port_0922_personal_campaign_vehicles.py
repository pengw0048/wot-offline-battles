"""Campaign hull reset keeps fitted property and cannot duplicate a crew."""
import copy
import json
import types
import unittest
from unittest import mock

import test_port_0922_economy as fixture
from gui.mods.offline_lan_0922 import personal_campaign_vehicles as vehicles
from gui.mods.offline_lan_0922.account_rpc import data, garage_store


NAME = 'ussr:R110_Object_260'


class Descriptor(object):
    """Native-shaped descriptor whose optional-device mutation is serialized."""
    def __init__(self, compactDescr):
        raw = compactDescr.decode('ascii')
        prefix, devices = raw.split('|')
        self.prefix = prefix
        self.optionalDevices = [types.SimpleNamespace(compactDescr=int(value))
                                if int(value) else None for value in devices.split(',')]
        for kind, field in ((2, 'chassis'), (3, 'turret'), (4, 'gun'),
                            (5, 'engine'), (6, 'fuelTank'), (7, 'radio')):
            setattr(self, field, types.SimpleNamespace(compactDescr=2000 + kind))
        self.type = types.SimpleNamespace(crewRoles=(('commander',), ('driver',)), name=NAME)
        self.maxHealth = 100

    def removeOptionalDevice(self, index):
        self.optionalDevices[index] = None

    def makeCompactDescr(self):
        return (self.prefix + '|' + ','.join(str(device.compactDescr) if device else '0'
                for device in self.optionalDevices)).encode('ascii')


def state_with_reward():
    snapshot = fixture._snapshot()
    base = snapshot['vehicles'][0]
    base['vehicleTypeName'] = 'usa:regular'
    base['compDescr'] = b'base|0,0,0'
    reward = copy.deepcopy(base)
    reward.update(id=20, vehicleTypeCompactDescr=fixture.SECOND_VEHICLE_CD,
                  vehicleTypeName=NAME, compDescr=b'reward|9001,0,0',
                  crew=[201, 202], tankmen={201: b'crew:201', 202: b'crew:202'},
                  eqs=[11001, 0, 0], eqsLayout=[11001, 0, 0],
                  repair=(700, 80), personalMissionVehicleSource='source:one')
    reward['inventoryItems'][9] = {9001: 1}
    reward['inventoryItems'][11] = {11001: 1}
    snapshot['vehicles'].append(reward)
    snapshot['vehicleTypeCompactDescrs'].add(fixture.SECOND_VEHICLE_CD)
    snapshot['unlockItemCompactDescrs'].add(fixture.SECOND_VEHICLE_CD)
    for kind in range(2, 8):
        for cd in snapshot['inventoryItems'][kind]:
            snapshot['inventoryItems'][kind][cd] = 2
    snapshot['inventoryItems'][10][10010] = 40
    snapshot['shopItemPrices'][fixture.SECOND_VEHICLE_CD] = {'credits': 6100000}
    snapshot.update(dict((key, copy.deepcopy(value)) for key, value in reward.items()
                         if key != 'inventoryItems'))
    state = fixture._state(snapshot)
    state._vehicles.VehicleDescr = Descriptor
    state._vehicles.getVehicleType = lambda cd: types.SimpleNamespace(name=NAME)
    return state


def receipt():
    return {'kind': 'vehicle', 'vehicle': NAME,
            'vehicle_type': fixture.SECOND_VEHICLE_CD,
            'origin': 'mission', 'source': 'source:one'}


class CampaignVehicleTests(unittest.TestCase):
    def test_first_vehicle_grant_records_an_acquisition_source(self):
        state = fixture._state()
        record = copy.deepcopy(state_with_reward().snapshot()['vehicles'][-1])
        record.pop(vehicles.SOURCE_FIELD)

        def create():
            state.snapshot()['vehicles'].append(record)
            return record['vehicleTypeCompactDescr']

        effect = vehicles.grant(state, NAME, create)
        self.assertEqual('vehicle', effect['kind'])
        self.assertFalse(effect['restored'])
        self.assertEqual(effect['source'], record[vehicles.SOURCE_FIELD])
        self.assertEqual(1, state.snapshot()['personalMissionRewardJournal']['vehicleSourceCounter'])
        self.assertEqual([201, 202], record['crew'])

    def test_withdraw_returns_fitted_property_and_selects_remaining_hull(self):
        state = state_with_reward()
        wallet = copy.deepcopy(state.snapshot()['wallet'])
        result = vehicles.revoke(state, receipt())
        snapshot = state.snapshot()
        self.assertTrue(result['withdrawn'])
        self.assertEqual(2, result['crew_returned'])
        self.assertEqual(['usa:regular'], [row['vehicleTypeName'] for row in snapshot['vehicles']])
        self.assertEqual(9, snapshot['id'])
        self.assertEqual({201: b'crew:201', 202: b'crew:202'}, snapshot['barracksTankmen'])
        self.assertEqual(2, snapshot['inventoryItems'][9][9001])
        self.assertEqual(40, snapshot['inventoryItems'][10][10010])
        self.assertEqual(1, snapshot['inventoryItems'][11][11001])
        self.assertEqual(1, snapshot['inventoryItems'][2][2002])
        self.assertEqual(wallet, snapshot['wallet'])
        self.assertEqual({fixture.VEHICLE_CD}, snapshot['vehicleTypeCompactDescrs'])
        self.assertIn(20, state.touched_vehicles())
        data._validate_selected_vehicle(snapshot)
        json.dumps(garage_store._ledger_payload(snapshot))

    def test_regrant_after_json_restart_restores_hull_without_more_crew_or_ammo(self):
        state = state_with_reward()
        vehicles.revoke(state, receipt())
        serialized = json.loads(json.dumps(garage_store._ledger_payload(state.snapshot())))
        restored = copy.deepcopy(state.snapshot())
        restored['barracksTankmen'] = {}
        restored['vehicles'][0].update(crew=[1001, 1002],
                                      tankmen={1001: b'a', 1002: b'b'})
        garage_store._apply_ledger(restored, {'ledger': serialized})
        state = fixture._state(restored)
        state._vehicles.VehicleDescr = Descriptor
        before_crew = copy.deepcopy(state.snapshot()['barracksTankmen'])
        create = mock.Mock(side_effect=AssertionError('must reuse the parked hull'))
        result = vehicles.grant(state, NAME, create)
        create.assert_not_called()
        self.assertTrue(result['restored'])
        record = state.snapshot()['vehicles'][-1]
        self.assertEqual([None, None], record['crew'])
        self.assertEqual({}, record['tankmen'])
        self.assertEqual([10010, 0], record['shells'])
        self.assertEqual([0, 0, 0], record['eqs'])
        self.assertEqual(b'reward|0,0,0', record['compDescr'])
        self.assertEqual([700, 80], record['repair'])
        self.assertEqual(before_crew, state.snapshot()['barracksTankmen'])
        self.assertEqual(40, state.snapshot()['inventoryItems'][10][10010])
        self.assertEqual(2, state.snapshot()['inventoryItems'][2][2002])
        self.assertNotIn(vehicles.PARKED_PREFIX + NAME, state.snapshot()['personalMissionRewardJournal'])
        data._validate_selected_vehicle(state.snapshot())
        vehicles.revoke(state, result)
        again = vehicles.grant(state, NAME, create)
        self.assertTrue(again['restored'])
        self.assertEqual(before_crew, state.snapshot()['barracksTankmen'])
        self.assertEqual(2, state.snapshot()['inventoryItems'][2][2002])

    def test_already_owned_compensates_full_price_and_does_not_revoke_tank(self):
        state = state_with_reward()
        create = mock.Mock()
        result = vehicles.grant(state, NAME, create)
        self.assertEqual('compensation', result['kind'])
        self.assertEqual(9000000, result['credits'])
        self.assertEqual(9100000, state.snapshot()['wallet']['credits'])
        create.assert_not_called()
        before = copy.deepcopy(state.snapshot())
        vehicles.revoke(state, result)
        self.assertEqual(before, state.snapshot())

    def test_newly_purchased_copy_while_parked_gets_compensation_not_duplicate(self):
        state = state_with_reward()
        existing = copy.deepcopy(state.snapshot()['vehicles'][-1])
        vehicles.revoke(state, receipt())
        existing.update(id=77, crew=[None, None], tankmen={}, compDescr=b'bought|0,0,0')
        existing['inventoryItems'][9] = {}
        state.snapshot()['vehicles'].append(existing)
        state.snapshot()['vehicleTypeCompactDescrs'].add(fixture.SECOND_VEHICLE_CD)
        result = vehicles.grant(state, NAME, mock.Mock())
        self.assertEqual('compensation', result['kind'])
        self.assertEqual(9000000, result['credits'])
        self.assertEqual(2, len(state.snapshot()['vehicles']))
        self.assertEqual(77, state.snapshot()['vehicles'][-1]['id'])
        self.assertEqual(2, state.snapshot()['inventoryItems'][2][2002])
        self.assertNotIn(vehicles.PARKED_PREFIX + NAME, state.snapshot()['personalMissionRewardJournal'])

    def test_sold_and_rebought_known_source_is_not_confused_with_reward(self):
        state = state_with_reward()
        del state.snapshot()['vehicles'][-1][vehicles.SOURCE_FIELD]
        before = copy.deepcopy(state.snapshot())
        with self.assertRaisesRegex(vehicles.GarageError, 'SOURCE_CHANGED'):
            vehicles.revoke(state, receipt())
        self.assertEqual(before, state.snapshot())

    def test_legacy_owned_hull_without_provenance_cannot_replace_unknown_silver_claim(self):
        state = state_with_reward()
        del state.snapshot()['vehicles'][-1][vehicles.SOURCE_FIELD]
        # It could be a pre-purchased tank whose old operation paid silver.
        # Withdrawing that hull would leave the unknown silver award intact.
        before = copy.deepcopy(state.snapshot())
        with self.assertRaisesRegex(vehicles.GarageError, 'SOURCE_UNAVAILABLE'):
            vehicles.legacy_effect(state, NAME)
        self.assertEqual(before, state.snapshot())

    def test_legacy_missing_receipt_requires_a_valid_persisted_acquisition_source(self):
        state = state_with_reward()
        source = NAME + ':7'
        state.snapshot()['vehicles'][-1][vehicles.SOURCE_FIELD] = source
        state.snapshot()['personalMissionRewardJournal'] = {'vehicleSourceCounter': 7}
        effect = vehicles.legacy_effect(state, NAME)
        self.assertEqual('mission', effect['origin'])
        self.assertEqual(source, effect['source'])
        vehicles.revoke(state, effect)
        self.assertTrue(vehicles.grant(state, NAME, mock.Mock())['restored'])
        for source in (NAME + ':0', NAME + ':999', 'another:vehicle:1', 'invalid'):
            state.snapshot()['vehicles'][-1][vehicles.SOURCE_FIELD] = source
            with self.assertRaisesRegex(vehicles.GarageError, 'SOURCE_UNAVAILABLE'):
                vehicles.legacy_effect(state, NAME)

    def test_legacy_unknown_origin_cannot_bypass_vehicle_source_validation(self):
        state = state_with_reward()
        effect = receipt()
        effect.pop('source')
        effect['origin'] = 'legacy_unknown'
        before = copy.deepcopy(state.snapshot())
        with self.assertRaisesRegex(vehicles.GarageError, 'SOURCE_CHANGED'):
            vehicles.revoke(state, effect)
        self.assertEqual(before, state.snapshot())

    def test_last_hull_or_insufficient_barracks_refuses_atomically(self):
        for problem in ('last', 'berths'):
            state = state_with_reward()
            if problem == 'last':
                state.snapshot()['vehicles'] = state.snapshot()['vehicles'][-1:]
            else:
                state.snapshot()['accountBerths'] = 1
            before = copy.deepcopy(state.snapshot())
            with self.assertRaises(vehicles.GarageError):
                vehicles.revoke(state, receipt())
            self.assertEqual(before, state.snapshot())
            self.assertEqual(0, state.revision)

    def test_compensation_uses_original_catalogue_not_bond_override_or_sale_discount(self):
        state = state_with_reward()
        state.snapshot()['sellPriceFactor'] = 0.1
        state.snapshot()['shopItemPrices'][fixture.SECOND_VEHICLE_CD] = {'crystal': 12345}
        for name, expected in (('germany:G104_Stug_IV', 800000),
                                ('usa:A102_T28_concept', 2800000),
                                ('germany:G105_T-55_NVA_DDR', 7000000),
                                ('ussr:R110_Object_260', 9000000)):
            state._vehicles.getVehicleType = lambda cd: types.SimpleNamespace(name=name)
            self.assertEqual(expected, vehicles.full_price_credits(state, fixture.SECOND_VEHICLE_CD))

    def test_missing_original_price_never_falls_back_to_shop_or_partial_payment(self):
        state = state_with_reward()
        before = copy.deepcopy(state.snapshot())
        with mock.patch.object(vehicles.price_catalogue, 'vehicle_price', return_value=None):
            with self.assertRaisesRegex(vehicles.GarageError, 'PRICE_UNAVAILABLE'):
                vehicles.grant(state, NAME, mock.Mock())
        self.assertEqual(before, state.snapshot())

    def test_vehicle_source_survives_normal_garage_save_and_restore(self):
        state = state_with_reward()
        store = garage_store.GarageStore(path=None)
        payload = json.loads(json.dumps(store._payload(state.snapshot())))
        saved = payload['vehicles'][str(fixture.SECOND_VEHICLE_CD)]
        record = copy.deepcopy(state.snapshot()['vehicles'][-1])
        record['id'] = 99
        record.pop(vehicles.SOURCE_FIELD)
        store._apply_vehicle(record, saved)
        self.assertEqual('source:one', record[vehicles.SOURCE_FIELD])
        saved.pop(vehicles.SOURCE_FIELD)
        store._apply_vehicle(record, saved)
        self.assertNotIn(vehicles.SOURCE_FIELD, record)


if __name__ == '__main__':
    unittest.main()
