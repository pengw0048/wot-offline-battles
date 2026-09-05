"""The offline account economy: prices, balances, ownership and research."""

import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load(relative, name):
    for parent in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922',
                   'gui.mods.offline_lan_0922.account_rpc'):
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
    sys.modules['gui.mods.offline_lan_0922'].__path__ = [str(PACKAGE_ROOT)]
    sys.modules['gui.mods.offline_lan_0922.account_rpc'].__path__ = [
        str(PACKAGE_ROOT / 'account_rpc')]
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, PACKAGE_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PRICE_CATALOGUE = _load(
    'price_catalogue.py', 'gui.mods.offline_lan_0922.price_catalogue')
sys.modules['gui.mods.offline_lan_0922'].price_catalogue = PRICE_CATALOGUE
ECONOMY = _load(
    'account_rpc/economy.py',
    'gui.mods.offline_lan_0922.account_rpc.economy')
GARAGE = _load(
    'account_rpc/garage.py', 'gui.mods.offline_lan_0922.account_rpc.garage')


VEHICLE_CD = 50001
SECOND_VEHICLE_CD = 50002
TURRET_CD = 2003


def _snapshot():
    return copy.deepcopy({
        'vehicles': [{
            'id': 9,
            'compDescr': b'veh:9',
            'crew': [101, 102],
            'tankmen': {101: b'tman:101', 102: b'tman:102'},
            'repair': (0, 100),
            'lock': (0, 0),
            'shells': [10010, 20],
            'shellsLayout': {(7001, 7002): [10010, 20]},
            'shellsLayoutIdx': (7001, 7002),
            'eqs': [0, 0, 0],
            'eqsLayout': [0, 0, 0],
            'inventoryItems': {
                2: {2002: 1}, 3: {TURRET_CD: 1}, 4: {2004: 1},
                5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
                10: {10010: 20},
            },
            'vehicleTypeCompactDescr': VEHICLE_CD,
        }],
        'inventoryItems': {
            2: {2002: 1}, 3: {TURRET_CD: 1}, 4: {2004: 1},
            5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
            9: {9001: 2},
            10: {10010: 20},
            11: {11001: 1},
        },
        'shopItemPrices': {
            2002: {'credits': 1000}, TURRET_CD: {'credits': 2000},
            2004: {'credits': 3000}, 2005: {'credits': 400},
            2006: {'credits': 500}, 2007: {'credits': 600},
            9001: {'credits': 50000}, 10010: {'credits': 100},
            11001: {'gold': 50},
            VEHICLE_CD: {'credits': 400000},
            SECOND_VEHICLE_CD: {'gold': 12500},
            2222: {'credits': 20000},
        },
        'vehicleTypeCompactDescrs': {VEHICLE_CD},
        'unlockItemCompactDescrs': set(
            [2002, TURRET_CD, 2004, 2005, 2006, 2007, VEHICLE_CD]),
        'wallet': {'credits': 100000, 'gold': 1000, 'freeXP': 500},
        'vehicleXP': {VEHICLE_CD: 30000},
        'accountSlots': 30,
        'accountBerths': 30,
        'nextInventoryID': 10,
        'defaultVehicleSettings': 15,
        'shopNationCount': 9,
        'customizationItemCount': 1,
    })


def _state(snapshot=None, vehicles=None):
    return GARAGE.GarageState(
        snapshot if snapshot is not None else _snapshot(),
        vehicles_module=vehicles or _vehicles(),
        tankmen_module=types.SimpleNamespace())


def _vehicles(unlocks_descrs=(), autounlocked=()):
    """Return the smallest client surface the ledger reads."""
    item_types = {
        VEHICLE_CD: 1, SECOND_VEHICLE_CD: 1,
        2002: 2, TURRET_CD: 3, 2004: 4, 2005: 5, 2006: 6, 2007: 7,
        2222: 3, 9001: 9, 10010: 10, 11001: 11,
    }
    return types.SimpleNamespace(
        getTypeOfCompactDescr=lambda compact_descr: item_types[compact_descr],
        getVehicleType=lambda compact_descr: types.SimpleNamespace(
            id=(0, 1), unlocksDescrs=unlocks_descrs,
            autounlockedItems=autounlocked))


class PriceCatalogueTests(unittest.TestCase):
    def test_the_baked_prices_are_the_exact_client_values(self):
        self.assertEqual(
            (1424000, 0, False), PRICE_CATALOGUE.VEHICLE_PRICES['ussr:R01_IS'])
        self.assertEqual(
            (0, 12500, False),
            PRICE_CATALOGUE.VEHICLE_PRICES['germany:G51_Lowe'])
        self.assertEqual(
            (12990, 0, False),
            PRICE_CATALOGUE.COMPONENT_PRICES['ussr:vehicleChassis:IS-1'])
        self.assertEqual(
            (500000, 0, False), PRICE_CATALOGUE.ARTEFACT_PRICES['toolbox'])

    def test_a_reward_vehicle_keeps_its_price_and_is_marked_unbuyable(self):
        """The launcher sells what the retail shop never did."""
        credits_amount, gold, not_in_shop = (
            PRICE_CATALOGUE.VEHICLE_PRICES['china:Ch01_Type59'])
        self.assertEqual(0, credits_amount)
        self.assertEqual(11500, gold)
        self.assertTrue(not_in_shop)

    def test_a_price_publishes_one_currency_only(self):
        """Money prefers gold whenever a gold key is present at all."""
        self.assertEqual(
            {'credits': 1424000},
            PRICE_CATALOGUE.money((1424000, 0, False)))
        self.assertEqual(
            {'gold': 12500}, PRICE_CATALOGUE.money((0, 12500, False)))
        self.assertIsNone(PRICE_CATALOGUE.money(None))

    def test_a_starter_vehicle_costs_nothing_in_every_nation_that_has_one(self):
        starters = [key for key, price in
                    PRICE_CATALOGUE.VEHICLE_PRICES.items()
                    if price == (0, 0, False)]
        self.assertIn('ussr:R11_MS-1', starters)
        self.assertIn('germany:G12_Ltraktor', starters)
        self.assertIn('usa:A01_T1_Cunningham', starters)


class PriceIndexTests(unittest.TestCase):
    def test_the_installed_client_decides_which_items_exist(self):
        """A catalogue name this client lacks must not enter the index."""
        class _List(object):
            def getList(self, nation_id):
                return {7: object()} if nation_id == 0 else {}

            def getIDsByName(self, name):
                if name == 'ussr:R11_MS-1':
                    return (0, 7)
                raise KeyError(name)

        cache = types.SimpleNamespace(
            chassisIDs=lambda nation: {'IS-1': 3} if nation == 0 else {},
            turretIDs=lambda nation: {},
            gunIDs=lambda nation: {},
            engineIDs=lambda nation: {},
            fuelTankIDs=lambda nation: {},
            radioIDs=lambda nation: {},
            shellIDs=lambda nation: {},
            optionalDevices=lambda: {
                1: types.SimpleNamespace(compactDescr=9001, name='toolbox')},
            equipments=lambda: {})
        vehicles = types.SimpleNamespace(
            makeIntCompactDescrByID=lambda kind, nation, item: (
                hash((kind, nation, item)) & 0xffff),
            g_list=_List(), g_cache=cache)
        nations = types.SimpleNamespace(NAMES=('ussr', 'germany'))

        index = ECONOMY.price_index(vehicles, nations)

        vehicle_cd = vehicles.makeIntCompactDescrByID('vehicle', 0, 7)
        chassis_cd = vehicles.makeIntCompactDescrByID('vehicleChassis', 0, 3)
        self.assertEqual((0, 0, False), index[vehicle_cd])
        self.assertEqual((12990, 0, False), index[chassis_cd])
        self.assertEqual((500000, 0, False), index[9001])

    def test_the_shop_mapping_separates_price_from_availability(self):
        prices, not_in_shop = ECONOMY.shop_prices({
            10: (1000, 0, False), 20: (0, 250, True), 30: (0, 0, False)})

        self.assertEqual({'credits': 1000}, prices[10])
        self.assertEqual({'gold': 250}, prices[20])
        self.assertEqual({20}, not_in_shop)
        # A free item is still an item; it is priced at nothing, not absent.
        self.assertEqual({'credits': 0}, prices[30])


class PurchaseTests(unittest.TestCase):
    def test_buying_an_item_charges_the_catalogue_price(self):
        state = _state()

        state.buy_item(9001, 2)

        snapshot = state.snapshot()
        self.assertEqual(4, snapshot['inventoryItems'][9][9001])
        self.assertEqual(0, snapshot['wallet']['credits'])

    def test_a_purchase_the_account_cannot_afford_changes_nothing(self):
        snapshot = _snapshot()
        snapshot['wallet']['credits'] = 40000
        state = _state(snapshot)

        with self.assertRaises(GARAGE.GarageError):
            state.buy_item(9001, 1)

        self.assertEqual(40000, state.snapshot()['wallet']['credits'])
        self.assertEqual(2, state.snapshot()['inventoryItems'][9][9001])

    def test_a_module_must_be_researched_before_it_can_be_bought(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.buy_item(2222, 1)

        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_shells_and_consumables_are_sold_without_research(self):
        """#1513 never gates ammunition or equipment on a research tree."""
        state = _state()

        state.buy_item(10010, 5)
        state.buy_item(11001, 1)

        snapshot = state.snapshot()
        self.assertEqual(25, snapshot['inventoryItems'][10][10010])
        self.assertEqual(99500, snapshot['wallet']['credits'])
        self.assertEqual(950, snapshot['wallet']['gold'])

    def test_selling_returns_half_and_drops_the_ownership_row(self):
        state = _state()

        state.sell_item(9001, 2)

        snapshot = state.snapshot()
        self.assertNotIn(9001, snapshot['inventoryItems'][9])
        self.assertEqual(100000 + 50000, snapshot['wallet']['credits'])

    def test_a_mounted_item_cannot_be_sold_out_from_under_the_vehicle(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.sell_item(TURRET_CD, 1)

        self.assertEqual(1, state.snapshot()['inventoryItems'][3][TURRET_CD])

    def test_selling_more_than_the_account_owns_is_refused(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.sell_item(9001, 3)


class GoldForCreditsTests(unittest.TestCase):
    """#1513 publishes premium rounds and consumables as credit purchases."""

    def test_a_premium_consumable_can_be_bought_at_the_credit_price(self):
        state = _state()

        state.buy_item(11001, 1, gold_for_credits=True)

        snapshot = state.snapshot()
        self.assertEqual(1000, snapshot['wallet']['gold'])
        self.assertEqual(
            100000 - 50 * GARAGE.GOLD_EXCHANGE_RATE,
            snapshot['wallet']['credits'])
        self.assertEqual(2, snapshot['inventoryItems'][11][11001])

    def test_the_same_purchase_still_takes_gold_when_the_client_asks(self):
        state = _state()

        state.buy_item(11001, 1)

        snapshot = state.snapshot()
        self.assertEqual(950, snapshot['wallet']['gold'])
        self.assertEqual(100000, snapshot['wallet']['credits'])

    def test_a_gold_vehicle_is_never_priced_in_credits(self):
        """Only the two item types the client's own flags cover convert."""
        state = _state()

        self.assertEqual(
            {'gold': 12500},
            state._item_cost(SECOND_VEHICLE_CD))
        self.assertEqual(
            {'gold': 6250},
            state._item_refund(SECOND_VEHICLE_CD))


class ResearchTests(unittest.TestCase):
    def test_research_spends_the_vehicle_experience_first(self):
        vehicles = _vehicles(unlocks_descrs=((20000, 2222),))
        state = _state(vehicles=vehicles)

        result = state.unlock(VEHICLE_CD, 0)

        snapshot = state.snapshot()
        self.assertEqual(2222, result['compactDescr'])
        self.assertEqual(20000, result['vehicleXP'])
        self.assertEqual(0, result['freeXP'])
        self.assertEqual(10000, snapshot['vehicleXP'][VEHICLE_CD])
        self.assertEqual(500, snapshot['wallet']['freeXP'])
        self.assertIn(2222, snapshot['unlockItemCompactDescrs'])

    def test_free_experience_covers_only_the_remainder(self):
        vehicles = _vehicles(unlocks_descrs=((30300, 2222),))
        state = _state(vehicles=vehicles)

        result = state.unlock(VEHICLE_CD, 0)

        snapshot = state.snapshot()
        self.assertEqual(30000, result['vehicleXP'])
        self.assertEqual(300, result['freeXP'])
        self.assertEqual(0, snapshot['vehicleXP'][VEHICLE_CD])
        self.assertEqual(200, snapshot['wallet']['freeXP'])

    def test_research_the_account_cannot_afford_changes_nothing(self):
        vehicles = _vehicles(unlocks_descrs=((99999, 2222),))
        state = _state(vehicles=vehicles)

        with self.assertRaises(GARAGE.GarageError):
            state.unlock(VEHICLE_CD, 0)

        snapshot = state.snapshot()
        self.assertEqual(30000, snapshot['vehicleXP'][VEHICLE_CD])
        self.assertEqual(500, snapshot['wallet']['freeXP'])
        self.assertNotIn(2222, snapshot['unlockItemCompactDescrs'])

    def test_a_prerequisite_that_is_not_researched_blocks_the_step(self):
        vehicles = _vehicles(unlocks_descrs=((100, 2222, 3333),))
        state = _state(vehicles=vehicles)

        with self.assertRaises(GARAGE.GarageError):
            state.unlock(VEHICLE_CD, 0)

    def test_researching_the_same_item_twice_costs_nothing_more(self):
        vehicles = _vehicles(unlocks_descrs=((100, 2222),))
        state = _state(vehicles=vehicles)

        state.unlock(VEHICLE_CD, 0)
        again = state.unlock(VEHICLE_CD, 0)

        self.assertEqual(0, again['vehicleXP'])
        self.assertEqual(29900, state.snapshot()['vehicleXP'][VEHICLE_CD])

    def test_an_unknown_research_step_is_refused(self):
        state = _state(vehicles=_vehicles(unlocks_descrs=((100, 2222),)))

        with self.assertRaises(GARAGE.GarageError):
            state.unlock(VEHICLE_CD, 7)


class VehiclePurchaseTests(unittest.TestCase):
    def _built(self, records):
        module = types.ModuleType('gui.mods.offline_lan_0922.vehicle_records')

        def build_record(vehicles, tankmen, item_type_indices, type_id,
                         inventory_id, next_tankman_id, settings, consumables,
                         descriptor=None, top_modules=True, role_level=None,
                         own_researchable_modules=True):
            records.append({
                'inventoryID': inventory_id,
                'settings': settings,
                'topModules': top_modules,
                'ownResearchable': own_researchable_modules,
                'consumables': list(consumables),
                'nextTankmanID': next_tankman_id,
            })
            return {
                'record': {
                    'id': inventory_id,
                    'compDescr': b'veh:new',
                    'crew': [next_tankman_id],
                    'tankmen': {next_tankman_id: b'tman'},
                    'repair': (0, 100),
                    'lock': (0, 0),
                    'shells': [10010, 10],
                    'shellsLayout': {},
                    'shellsLayoutIdx': (7001, 7002),
                    'eqs': list(consumables),
                    'eqsLayout': list(consumables),
                    'inventoryItems': {2: {2002: 1}, 10: {10010: 10}},
                    'vehicleTypeCompactDescr': SECOND_VEHICLE_CD,
                },
                'shellCatalog': {},
                'vehicleTypeCompactDescr': SECOND_VEHICLE_CD,
                'nextTankmanID': next_tankman_id + 1,
                'descriptor': None,
            }

        module.build_record = build_record
        module.default_vehicle_settings = lambda: 7
        items = types.ModuleType('items')
        items.ITEM_TYPE_INDICES = {
            'vehicleChassis': 2, 'vehicleTurret': 3, 'vehicleGun': 4,
            'vehicleEngine': 5, 'vehicleFuelTank': 6, 'vehicleRadio': 7,
            'shell': 10,
        }
        return {
            'gui.mods.offline_lan_0922.vehicle_records': module,
            'items': items,
        }

    def test_a_bought_vehicle_arrives_stock_and_charges_gold(self):
        from unittest import mock

        calls = []
        snapshot = _snapshot()
        snapshot['wallet']['gold'] = 20000
        vehicles = _vehicles(autounlocked=(2002,))
        state = _state(snapshot, vehicles=vehicles)
        with mock.patch.dict(sys.modules, self._built(calls)):
            record = state.buy_vehicle(SECOND_VEHICLE_CD)

        snapshot = state.snapshot()
        self.assertEqual(1, len(calls))
        # Retail sells the stock fitting and nothing that was not paid for.
        self.assertFalse(calls[0]['topModules'])
        self.assertFalse(calls[0]['ownResearchable'])
        self.assertEqual([0, 0, 0], calls[0]['consumables'])
        self.assertEqual(20000 - 12500, snapshot['wallet']['gold'])
        # The vehicle must arrive loaded, so its load is paid for: ten rounds
        # at 100 credits.  Free ammunition would be mintable through a sale.
        self.assertEqual(100000 - 1000, snapshot['wallet']['credits'])
        self.assertEqual(2, len(snapshot['vehicles']))
        self.assertIn(SECOND_VEHICLE_CD, snapshot['vehicleTypeCompactDescrs'])
        self.assertIn(2002, snapshot['unlockItemCompactDescrs'])
        self.assertEqual(0, snapshot['vehicleXP'][SECOND_VEHICLE_CD])
        self.assertNotEqual(9, record['id'])

    def test_a_bought_vehicle_starts_with_the_refill_switches_on(self):
        """A purchase must not differ from a vehicle the garage built itself."""
        from unittest import mock

        calls = []
        snapshot = _snapshot()
        snapshot['wallet']['gold'] = 20000
        state = _state(snapshot, vehicles=_vehicles())
        with mock.patch.dict(sys.modules, self._built(calls)):
            state.buy_vehicle(SECOND_VEHICLE_CD)

        self.assertEqual(15, calls[0]['settings'])

    def test_a_save_without_a_published_default_asks_the_record_factory(self):
        from unittest import mock

        calls = []
        snapshot = _snapshot()
        snapshot['wallet']['gold'] = 20000
        del snapshot['defaultVehicleSettings']
        state = _state(snapshot, vehicles=_vehicles())
        with mock.patch.dict(sys.modules, self._built(calls)):
            state.buy_vehicle(SECOND_VEHICLE_CD)

        self.assertEqual(7, calls[0]['settings'])

    def test_a_new_vehicle_never_reuses_a_live_inventory_id(self):
        from unittest import mock

        calls = []
        snapshot = _snapshot()
        snapshot['nextInventoryID'] = 9
        snapshot['wallet']['gold'] = 20000
        state = _state(snapshot)
        with mock.patch.dict(sys.modules, self._built(calls)):
            record = state.buy_vehicle(SECOND_VEHICLE_CD)

        self.assertNotEqual(9, record['id'])
        self.assertEqual(
            2, len(set(row['id'] for row in state.snapshot()['vehicles'])))

    def test_a_vehicle_the_account_cannot_afford_is_refused(self):
        from unittest import mock

        calls = []
        snapshot = _snapshot()
        snapshot['wallet']['gold'] = 100
        state = _state(snapshot)
        with mock.patch.dict(sys.modules, self._built(calls)):
            with self.assertRaises(GARAGE.GarageError):
                state.buy_vehicle(SECOND_VEHICLE_CD)

        self.assertEqual(1, len(state.snapshot()['vehicles']))
        self.assertEqual(100, state.snapshot()['wallet']['gold'])

    def test_a_new_crew_never_takes_an_inventory_id_the_barracks_holds(self):
        """A reused id does not break one vehicle, it invalidates the save."""
        from unittest import mock

        snapshot = _snapshot()
        snapshot['wallet']['gold'] = 20000
        snapshot['barracksTankmen'] = {103: b'tman:103'}
        calls = []
        state = _state(snapshot)
        with mock.patch.dict(sys.modules, self._built(calls)):
            state.buy_vehicle(SECOND_VEHICLE_CD)

        self.assertEqual(104, calls[0]['nextTankmanID'])

    def test_buying_a_vehicle_the_account_already_owns_is_refused(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.buy_vehicle(VEHICLE_CD)

    def test_a_full_garage_refuses_another_vehicle(self):
        from unittest import mock

        snapshot = _snapshot()
        snapshot['accountSlots'] = 1
        snapshot['wallet']['gold'] = 20000
        state = _state(snapshot)
        with mock.patch.dict(sys.modules, self._built([])):
            with self.assertRaises(GARAGE.GarageError):
                state.buy_vehicle(SECOND_VEHICLE_CD)

    def test_selling_a_vehicle_returns_half_and_drops_its_experience(self):
        from unittest import mock

        snapshot = _snapshot()
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = SECOND_VEHICLE_CD
        snapshot['vehicles'].append(second)
        snapshot['vehicleTypeCompactDescrs'].add(SECOND_VEHICLE_CD)
        snapshot['vehicleXP'][SECOND_VEHICLE_CD] = 4000
        state = _state(snapshot)

        state.sell_vehicle(10)

        result = state.snapshot()
        self.assertEqual(1, len(result['vehicles']))
        self.assertNotIn(SECOND_VEHICLE_CD, result['vehicleXP'])
        self.assertEqual(1000 + 6250, result['wallet']['gold'])

    def test_the_last_vehicle_cannot_be_sold(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.sell_vehicle(9)

    def _two_vehicles(self):
        snapshot = _snapshot()
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        # A crew member belongs to one vehicle, so the second one has its own.
        second['crew'] = [103, 104]
        second['tankmen'] = {103: b'tman:103', 104: b'tman:104'}
        second['vehicleTypeCompactDescr'] = SECOND_VEHICLE_CD
        second['eqs'] = [11001, 0, 0]
        second['inventoryItems'][11] = {11001: 1}
        snapshot['vehicles'].append(second)
        snapshot['vehicleTypeCompactDescrs'].add(SECOND_VEHICLE_CD)
        snapshot['vehicleXP'][SECOND_VEHICLE_CD] = 4000
        return snapshot

    def test_a_sale_that_keeps_the_crew_sends_them_to_the_barracks(self):
        """That is what #1513 offers instead of dismissing them."""
        state = _state(self._two_vehicles())

        state.sell_vehicle(10, dismiss_crew=False)

        snapshot = state.snapshot()
        self.assertEqual(1, len(snapshot['vehicles']))
        self.assertEqual(
            {103: b'tman:103', 104: b'tman:104'},
            snapshot['barracksTankmen'])
        self.assertEqual(1000 + 6250, snapshot['wallet']['gold'])

    def test_a_sale_that_keeps_the_crew_is_refused_by_a_full_barracks(self):
        """#1513's own sell dialog checks the same berths before asking."""
        snapshot = self._two_vehicles()
        snapshot['accountBerths'] = 1
        state = _state(snapshot)

        with self.assertRaises(GARAGE.GarageError):
            state.sell_vehicle(10, dismiss_crew=False)

        self.assertEqual(2, len(state.snapshot()['vehicles']))
        self.assertEqual(1000, state.snapshot()['wallet']['gold'])

    def test_a_sale_that_dismisses_the_crew_uses_no_berth(self):
        state = _state(self._two_vehicles())

        state.sell_vehicle(10, dismiss_crew=True)

        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))

    def test_the_listed_rounds_are_paid_by_the_count_the_vehicle_carried(self):
        state = _state(self._two_vehicles())

        state.sell_vehicle(10, items_from_vehicle=[10010])

        snapshot = state.snapshot()
        # 20 rounds at 100 credits, halved, on top of the vehicle's own gold.
        self.assertEqual(100000 + 1000, snapshot['wallet']['credits'])
        self.assertEqual(1000 + 6250, snapshot['wallet']['gold'])
        # The remaining vehicle still carries its own twenty.
        self.assertEqual(20, snapshot['inventoryItems'][10][10010])

    def test_a_listed_premium_consumable_pays_one_unit_back_in_credits(self):
        """The shop sells these for credits, so a sale cannot mint gold."""
        state = _state(self._two_vehicles())

        state.sell_vehicle(10, items_from_vehicle=[11001])

        snapshot = state.snapshot()
        self.assertEqual(1000 + 6250, snapshot['wallet']['gold'])
        self.assertEqual(
            100000 + 50 * GARAGE.GOLD_EXCHANGE_RATE // 2,
            snapshot['wallet']['credits'])
        self.assertNotIn(11001, snapshot['inventoryItems'][11])

    def test_an_item_the_vehicle_never_carried_cannot_be_sold_with_it(self):
        state = _state(self._two_vehicles())

        with self.assertRaises(GARAGE.GarageError):
            state.sell_vehicle(10, items_from_vehicle=[9001])

        self.assertEqual(2, len(state.snapshot()['vehicles']))

    def test_a_stored_module_is_refunded_by_its_whole_stack(self):
        state = _state(self._two_vehicles())

        state.sell_vehicle(10, items_from_inventory=[9001])

        snapshot = state.snapshot()
        # Two stored devices at 50000 credits, halved.
        self.assertEqual(100000 + 50000, snapshot['wallet']['credits'])
        self.assertNotIn(9001, snapshot['inventoryItems'][9])

    def test_a_module_a_remaining_vehicle_still_mounts_is_not_sold(self):
        snapshot = self._two_vehicles()
        snapshot['vehicles'][0]['inventoryItems'][9] = {9001: 2}
        state = _state(snapshot)

        with self.assertRaises(GARAGE.GarageError):
            state.sell_vehicle(10, items_from_inventory=[9001])

        self.assertEqual(2, len(state.snapshot()['vehicles']))


class CurrencyTests(unittest.TestCase):
    def test_gold_converts_to_credits_at_the_published_rate(self):
        state = _state()

        result = state.exchange_gold(10)

        snapshot = state.snapshot()
        self.assertEqual(10 * GARAGE.GOLD_EXCHANGE_RATE, result['credits'])
        self.assertEqual(990, snapshot['wallet']['gold'])
        self.assertEqual(
            100000 + 10 * GARAGE.GOLD_EXCHANGE_RATE,
            snapshot['wallet']['credits'])

    def test_an_exchange_the_account_cannot_afford_changes_nothing(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.exchange_gold(5000)

        self.assertEqual(1000, state.snapshot()['wallet']['gold'])
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_vehicle_experience_converts_to_free_experience_for_gold(self):
        state = _state()

        result = state.convert_to_free_xp([VEHICLE_CD], 500)

        snapshot = state.snapshot()
        self.assertEqual(500, result['freeXP'])
        self.assertEqual(20, result['gold'])
        self.assertEqual(29500, snapshot['vehicleXP'][VEHICLE_CD])
        self.assertEqual(1000, snapshot['wallet']['freeXP'])
        self.assertEqual(980, snapshot['wallet']['gold'])

    def test_converting_more_experience_than_the_vehicles_hold_is_refused(self):
        state = _state()

        with self.assertRaises(GARAGE.GarageError):
            state.convert_to_free_xp([VEHICLE_CD], 999999)

        self.assertEqual(30000, state.snapshot()['vehicleXP'][VEHICLE_CD])

    def test_a_vehicle_with_research_left_cannot_convert_its_experience(self):
        """That experience is what pays for the rest of its own tree."""
        state = _state(vehicles=_vehicles(unlocks_descrs=((100, 2222),)))

        with self.assertRaises(GARAGE.GarageError):
            state.convert_to_free_xp([VEHICLE_CD], 100)

        self.assertEqual(30000, state.snapshot()['vehicleXP'][VEHICLE_CD])
        self.assertEqual(1000, state.snapshot()['wallet']['gold'])

    def test_two_vehicles_sharing_a_device_cannot_sell_it_from_under_them(self):
        snapshot = _snapshot()
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = SECOND_VEHICLE_CD
        second['inventoryItems'][9] = {9001: 1}
        snapshot['vehicles'][0]['inventoryItems'][9] = {9001: 1}
        snapshot['vehicles'].append(second)
        state = _state(snapshot)

        with self.assertRaises(GARAGE.GarageError):
            state.sell_item(9001, 2)

        self.assertEqual(2, state.snapshot()['inventoryItems'][9][9001])

    def test_a_garage_slot_and_a_berth_block_are_bought_with_gold(self):
        state = _state()

        slots = state.buy_slot()
        berths = state.buy_berths()

        snapshot = state.snapshot()
        self.assertEqual(31, slots)
        self.assertEqual(30 + GARAGE.BARRACKS_BERTH_COUNT, berths)
        self.assertEqual(
            1000 - GARAGE.GARAGE_SLOT_GOLD_PRICE -
            GARAGE.BARRACKS_BERTH_GOLD_PRICE, snapshot['wallet']['gold'])


class BattleEarningsTests(unittest.TestCase):
    def test_a_battle_banks_credits_free_experience_and_vehicle_experience(
            self):
        state = _state()

        state.award_battle_earnings(
            VEHICLE_CD, {'credits': 900, 'xp': 400, 'free_xp': 20})

        snapshot = state.snapshot()
        self.assertEqual(100900, snapshot['wallet']['credits'])
        self.assertEqual(520, snapshot['wallet']['freeXP'])
        self.assertEqual(30400, snapshot['vehicleXP'][VEHICLE_CD])

    def test_accelerated_crew_training_spends_the_vehicle_experience(self):
        """The crew already received it; banking it again would double it."""
        state = _state()

        state.award_battle_earnings(
            VEHICLE_CD, {'credits': 900, 'xp': 400, 'free_xp': 20},
            accelerated=True)

        snapshot = state.snapshot()
        self.assertEqual(30000, snapshot['vehicleXP'][VEHICLE_CD])
        self.assertEqual(520, snapshot['wallet']['freeXP'])


if __name__ == '__main__':
    unittest.main()


class BuyAndEquipTests(unittest.TestCase):
    """#1513's "buy and install" sends one command that does both.

    It is the button on the tech tree and in the depot, so it is the first
    purchase a career makes, and it mounts before it charges.
    """

    def _state(self, credits_amount, item_type=9):
        snapshot = _snapshot()
        snapshot['wallet'] = {
            'credits': credits_amount, 'gold': 0, 'freeXP': 0}
        vehicles = _vehicles()
        vehicles.getTypeOfCompactDescr = lambda compact_descr: (
            item_type if compact_descr == 9001 else 10)
        return GARAGE.GarageState(
            snapshot, vehicles_module=vehicles,
            tankmen_module=types.SimpleNamespace())

    def test_an_unaffordable_device_is_refused_before_it_is_mounted(self):
        state = self._state(100)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(GARAGE.GarageError):
            state.buy_and_equip_item(9, 9001, 0)

        self.assertEqual(before, state.snapshot())

    def test_an_unresearched_module_is_refused_before_it_is_mounted(self):
        state = self._state(1000000, item_type=4)
        state.snapshot()['unlockItemCompactDescrs'].discard(9001)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(GARAGE.GarageError):
            state.buy_and_equip_item(9, 9001, 0)

        self.assertEqual(before, state.snapshot())
