import copy
import inspect
import json
import pickle
import sys
from collections.abc import Mapping
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import data as account_data
from gui.mods.offline_lan_0922.account_rpc import requests as account_requests
from gui.mods.offline_lan_0922 import compat as compatibility
from gui.mods.offline_lan_0922.account_rpc.server import FakeServer
from gui.mods.offline_lan_0922.account_rpc.state import AccountState


CONTRACT_PATH = (
    ROOT / '0.9.22' / 'tools' /
    'account_lobby_consumer_contract.json')
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))

SELECTED_VEHICLE = {
    'id': 9,
    'compDescr': b'compact',
    'crew': [101, 102],
    'tankmen': {101: b'commander', 102: b'driver'},
    'repair': (0, 100),
    'lock': (0, 0),
    'shells': [10010, 20, 10011, 10],
    'shellsLayout': {},
    'eqs': [0, 0, 0],
    'eqsLayout': [0, 0, 0],
    'inventoryItems': {
        2: {2002: 1}, 3: {2003: 1}, 4: {2004: 1},
        5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
        10: {10010: 20, 10011: 10},
    },
    'shopItemPrices': dict(
        (compact_descr,
         ({'credits': 0} if compact_descr >= 12000 else
          {'credits': 0, 'gold': 0}))
        for compact_descr in (
            2002, 2003, 2004, 2005, 2006, 2007, 10010, 10011,
            12001, 12002)),
    'shopNationCount': 9,
    'customizationItemCount': 2,
}


def _full_garage_snapshot():
    first = copy.deepcopy(SELECTED_VEHICLE)
    first['vehicleTypeCompactDescr'] = 50001
    second = {
        'id': 10,
        'compDescr': b'compact-2',
        'crew': [201, 202],
        'tankmen': {201: b'commander-2', 202: b'driver-2'},
        'repair': (0, 200),
        'lock': (0, 0),
        'shells': [11010, 30, 11011, 15],
        'shellsLayout': {},
        'eqs': [0, 0, 0],
        'eqsLayout': [0, 0, 0],
        'inventoryItems': {
            2: {3002: 1}, 3: {3003: 1}, 4: {3004: 1},
            5: {3005: 1}, 6: {3006: 1}, 7: {3007: 1},
            10: {11010: 30, 11011: 15},
        },
        'vehicleTypeCompactDescr': 50002,
    }
    snapshot = copy.deepcopy(SELECTED_VEHICLE)
    snapshot['vehicles'] = [first, second]
    for item_type, items in second['inventoryItems'].items():
        snapshot['inventoryItems'].setdefault(item_type, {}).update(items)
    snapshot['shopItemPrices'].update(dict(
        (compact_descr, {'credits': 0, 'gold': 0})
        for compact_descr in (
            3002, 3003, 3004, 3005, 3006, 3007, 11010, 11011,
            50001, 50002)))
    snapshot['vehicleTypeCompactDescrs'] = {50001, 50002}
    snapshot['unlockItemCompactDescrs'] = set(
        compact_descr
        for item_type in tuple(range(2, 8)) + (10,)
        for compact_descr in snapshot['inventoryItems'][item_type])
    snapshot['unlockItemCompactDescrs'].update(
        snapshot['vehicleTypeCompactDescrs'])
    return snapshot


def _contract_path(values, path):
    """Resolve one producer path from the machine-readable lobby contract."""
    root, remainder = path.split('.', 1)
    value = values[root]
    for key in remainder.split('.'):
        value = value[key]
    return value


class _Player(object):
    def __init__(self):
        self.responses = []
        self.ext_responses = []
        self.streams = []
        self.updates = []
        self.dossier_resyncs = 0

    def onCmdResponse(self, request_id, result_id, error):
        self.responses.append((request_id, result_id, error))

    def onStreamComplete(self, request_id, desc, payload):
        self.streams.append((request_id, desc, payload))

    def onCmdResponseExt(self, request_id, result_id, error, ext):
        self.ext_responses.append((request_id, result_id, error, ext))

    def update(self, payload):
        self.updates.append(payload)

    def resyncDossiers(self):
        self.dossier_resyncs += 1


class _ItemsPrices(dict):
    """Test double for exact #1513 items.ItemsPrices."""
    def getPrices(self, compact_descr):
        return self[compact_descr]


class _NativeLong(int):
    pass


class AccountRpcTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.player = _Player()
        self.server = FakeServer(lambda: self.player,
                                 lambda delay, fn: self.pending.append((delay, fn)),
                                 {'items_prices_factory': _ItemsPrices})

    def _run(self):
        self.assertTrue(self.pending)
        delay, callback = self.pending.pop(0)
        self.assertEqual(0.0, delay)
        callback()

    def test_shop_item_prices_are_normalized_for_native_long_formatter(self):
        value = {
            'items': {'itemPrices': {
                101: {'credits': 0},
                102: ({'gold': 25}, {'gold': 30}),
            }},
            'defaults': {'items': {'itemPrices': {
                101: {'credits': 0},
            }}},
        }

        wrapped = account_requests._wrap_shop_item_prices(
            value, _ItemsPrices, _NativeLong)

        self.assertIsInstance(
            wrapped['items']['itemPrices'][101]['credits'], _NativeLong)
        self.assertIsInstance(
            wrapped['items']['itemPrices'][102][0]['gold'], _NativeLong)
        self.assertIsInstance(
            wrapped['items']['itemPrices'][102][1]['gold'], _NativeLong)
        self.assertIsInstance(
            wrapped['defaults']['items']['itemPrices'][101]['credits'],
            _NativeLong)

    def test_exact_registered_handler_is_asynchronous(self):
        self.server.doCmdInt3(31, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        self.assertEqual([], self.player.responses)
        self._run()
        self.assertEqual([(31, commands.RES_SUCCESS, '')], self.player.responses)

    def test_postbattle_progress_pushes_resources_and_vehicle_xp_now(self):
        class Store(object):
            def progress(self):
                return {
                    'credits': 700, 'freeXP': 30,
                    'vehicles': {'ussr:R11_MS-1': {'xp': 600}},
                }

        vehicles_module = types.ModuleType('items.vehicles')
        vehicles_module.VehicleDescr = lambda typeName=None: (
            types.SimpleNamespace(type=types.SimpleNamespace(id=(0, 1))))
        vehicles_module.makeIntCompactDescrByID = (
            lambda unused_type, unused_nation, unused_vehicle: 50001)
        items_module = types.ModuleType('items')
        items_module.vehicles = vehicles_module
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)), {
                'selected_vehicle': {
                    'vehicleTypeCompactDescrs': [50001]},
                'postbattle_store': Store(),
            })
        with mock.patch.dict(sys.modules, {
                'items': items_module, 'items.vehicles': vehicles_module}):
            self.assertTrue(server.publish_postbattle_progress())
            self._run()

        update = pickle.loads(self.player.updates[-1])
        self.assertEqual(
            {'credits', 'freeXP', 'vehTypeXP'}, set(update['stats']))
        self.assertNotIn('eliteVehicles', update['stats'])
        self.assertNotIn('unlocks', update['stats'])
        self.assertEqual(account_data.OFFLINE_CREDITS + 700,
                         update['stats']['credits'])
        self.assertEqual(account_data.OFFLINE_FREE_XP + 30,
                         update['stats']['freeXP'])
        self.assertEqual(600, update['stats']['vehTypeXP'][50001])
        self.assertEqual(1, self.player.dossier_resyncs)

    def test_stats_update_does_not_run_the_inventory_refresh_fallback(self):
        with mock.patch(
                'gui.mods.offline_lan_0922.account_rpc.server.'
                '_refresh_garage_views') as refresh:
            self.assertTrue(self.server._push_update({'stats': {'credits': 1}}))
            self._run()

        refresh.assert_not_called()

    def test_inventory_update_runs_the_garage_refresh_fallback(self):
        diff = {'inventory': {1: {'compDescr': {9: b'compact'}}}}
        with mock.patch(
                'gui.mods.offline_lan_0922.account_rpc.server.'
                '_refresh_garage_views') as refresh:
            self.assertTrue(self.server._push_update(diff))
            self._run()

        refresh.assert_called_once()
        self.assertEqual(diff['inventory'],
                         refresh.call_args[0][0]['inventory'])

    def test_add_skill_success_waits_for_current_vehicle_refresh(self):
        trace = []
        snapshot = _full_garage_snapshot()
        snapshot['vehicles'][1]['tankmen'][202] = b'loader-2'

        class TankmanDescriptor(object):
            def __init__(self, compact_descr):
                self.compact_descr = compact_descr

            def addSkill(self, name):
                self.compact_descr += b'|' + name.encode('ascii')

            def makeCompactDescr(self):
                return self.compact_descr

        skill_names = ['skill_%d' % index for index in range(61)]
        skill_names[48] = 'loader_intuition'
        tankmen = types.SimpleNamespace(
            SKILL_NAMES=skill_names, TankmanDescr=TankmanDescriptor)
        context = {
            'garage': account_requests.garage.GarageState(
                snapshot, tankmen_module=tankmen),
        }
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)), context)

        def update(payload):
            diff = pickle.loads(payload)
            trace.append(('account-update', diff))

        self.player.update = update
        self.player.onCmdResponse = (
            lambda *args: trace.append('response'))

        def delayed_refresh(unused_diff, after_refresh=None):
            trace.append('refresh-start')

            def complete():
                trace.append('refresh-complete')
                if callable(after_refresh):
                    after_refresh()

            self.pending.append((0.0, complete))
            return True

        with mock.patch(
                'gui.mods.offline_lan_0922.account_rpc.server.'
                '_refresh_garage_views', side_effect=delayed_refresh):
            server.doCmdInt3(
                45, commands.CMD_TMAN_ADD_SKILL, 202, 48, 0)

            self._run()
            self.assertEqual([], trace)
            self._run()
            self.assertEqual('account-update', trace[0][0])
            self.assertEqual('refresh-start', trace[1])
            self._run()

        diff = trace[0][1]
        self.assertEqual(
            b'loader-2|loader_intuition',
            diff['inventory'][8]['compDescr'][202])
        self.assertEqual(
            b'commander-2', diff['inventory'][8]['compDescr'][201])
        self.assertNotIn(101, diff['inventory'][8]['compDescr'])
        self.assertEqual(10, diff['inventory'][8]['vehicle'][202])
        self.assertEqual([201, 202], diff['inventory'][1]['crew'][10])
        self.assertNotIn(9, diff['inventory'][1]['crew'])
        self.assertEqual(
            ['refresh-start', 'refresh-complete', 'response'], trace[1:])

    def test_fitting_success_is_dropped_if_account_retires_before_publish(self):
        active = [self.player]
        context = {
            'garage': account_requests.garage.GarageState(
                copy.deepcopy(SELECTED_VEHICLE)),
        }
        server = FakeServer(
            lambda: active[0],
            lambda delay, fn: self.pending.append((delay, fn)), context)

        server.doCmdIntArr(
            46, commands.CMD_EQUIP_SHELLS, [9, 10010, 7])
        self._run()
        active[0] = None
        self._run()

        self.assertEqual([], self.player.updates)
        self.assertEqual([], self.player.responses)

    def test_response_is_dropped_after_account_is_retired(self):
        active = [self.player]
        server = FakeServer(
            lambda: active[0],
            lambda delay, fn: self.pending.append((delay, fn)),
            {'items_prices_factory': _ItemsPrices})

        server.doCmdInt3(39, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        active[0] = None
        self._run()

        self.assertEqual([], self.player.responses)

    def test_stream_is_dropped_if_account_changes_after_response(self):
        active = [self.player]
        server = FakeServer(
            lambda: active[0],
            lambda delay, fn: self.pending.append((delay, fn)),
            {'items_prices_factory': _ItemsPrices})

        server.doCmdInt3(40, commands.CMD_SYNC_SHOP, 0, 0, 0)
        self._run()
        self.assertEqual([(40, commands.RES_STREAM, '')],
                         self.player.responses)
        active[0] = _Player()
        self._run()

        self.assertEqual([], self.player.streams)

    def test_server_stats_event_is_async_and_precedes_command_response(self):
        trace = []
        self.player.onCmdResponse = lambda *args: trace.append('response')
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'receive_server_stats': lambda value: trace.append('stats')})

        server.doCmdInt3(38, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        self.assertEqual([], trace)
        self._run()
        self.assertEqual(['stats', 'response'], trace)
        self.assertTrue(
            CONTRACT['deliveryOrder']['allClientCallbacksAreAsynchronous'])
        self.assertTrue(
            CONTRACT['deliveryOrder']['serverStatsBeforeCommandResponse'])

    def test_unknown_command_returns_failure_not_success(self):
        self.server.doCmdInt3(32, 999999, 0, 0, 0)
        self._run()
        self.assertEqual(commands.RES_FAILURE, self.player.responses[0][1])
        self.assertEqual('UNSUPPORTED_OFFLINE_COMMAND', self.player.responses[0][2])

    def test_enqueue_random_fires_the_account_event_asynchronously(self):
        queue_events = []
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'on_enqueued': queue_events.append})

        server.doCmdInt3(commands.REQUEST_ID_NO_RESPONSE, commands.CMD_ENQUEUE_RANDOM,
                         9, 65535, 0)
        self.assertEqual([], queue_events)
        self._run()
        self.assertEqual([commands.QUEUE_TYPE_RANDOMS], queue_events)

    def test_dequeue_random_fires_the_account_event_asynchronously(self):
        queue_events = []
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'on_dequeued': queue_events.append})

        server.doCmdInt3(commands.REQUEST_ID_NO_RESPONSE, commands.CMD_DEQUEUE_RANDOM,
                         0, 0, 0)
        self.assertEqual([], queue_events)
        self._run()
        self.assertEqual([commands.QUEUE_TYPE_RANDOMS], queue_events)

    def test_queue_commands_fail_without_the_account_event_boundary(self):
        for command in (commands.CMD_ENQUEUE_RANDOM,
                        commands.CMD_DEQUEUE_RANDOM):
            self.server.doCmdInt3(commands.REQUEST_ID_NO_RESPONSE, command,
                                  0, 0, 0)
            self._run()
        for unused_request_id, result_id, error in self.player.responses:
            self.assertEqual(commands.RES_FAILURE, result_id)
            self.assertEqual('QUEUE_EVENTS_UNAVAILABLE', error)

    def test_broken_queue_listener_cannot_abort_the_command_response(self):
        # Event.__call__ in #1513 re-raises after logging, so a broken lobby
        # listener reaches the entity boundary; the fake server must contain
        # it the way the engine's entity dispatch does.
        def on_enqueued(queue_type):
            raise RuntimeError('carousel listener failed')

        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'on_enqueued': on_enqueued})

        server.doCmdInt3(commands.REQUEST_ID_NO_RESPONSE,
                         commands.CMD_ENQUEUE_RANDOM, 9, 65535, 0)
        self._run()
        self.assertEqual(
            [(commands.REQUEST_ID_NO_RESPONSE, commands.RES_SUCCESS, '')],
            self.player.responses)

    def test_filter_sanitizer_repairs_only_divergent_saved_filters(self):
        defaults = {
            'CAROUSEL_FILTER_2': {'elite': False, 'favorite': False},
            'CAROUSEL_FILTER_CLIENT_1': {'searchNameVehicle': ''},
            'BOOSTERS_FILTER': 0,
        }
        saved = {
            'CAROUSEL_FILTER_2': {'elite': True, 'legacyKey': True},
            'CAROUSEL_FILTER_CLIENT_1': {'searchNameVehicle': 'T-34'},
        }
        writes = {}

        class _Settings(object):
            @staticmethod
            def getFilter(name):
                if name in saved:
                    return copy.deepcopy(saved[name])
                return copy.deepcopy(defaults[name])

            @staticmethod
            def setFilter(name, value):
                writes[name] = value

        class _Module(object):
            KEY_FILTERS = 'FILTERS'
            DEFAULT_VALUES = {'FILTERS': defaults}
            AccountSettings = _Settings

        repaired = compatibility._sanitize_account_filters(_Module)

        self.assertEqual(['CAROUSEL_FILTER_2'], repaired)
        self.assertEqual({'CAROUSEL_FILTER_2': {
            'elite': True, 'favorite': False}}, writes)

    def test_filter_sanitizer_resolves_the_shadowed_settings_module(self):
        # account_helpers/__init__ in #1513 rebinds the submodule name to the
        # AccountSettings class, so the default import must use sys.modules.
        writes = {}

        class _Settings(object):
            @staticmethod
            def getFilter(name):
                return {'stale': 1}

            @staticmethod
            def setFilter(name, value):
                writes[name] = value

        module = types.ModuleType('account_helpers.AccountSettings')
        module.AccountSettings = _Settings
        module.KEY_FILTERS = 'FILTERS'
        module.DEFAULT_VALUES = {'FILTERS': {'CAROUSEL_FILTER_2': {
            'elite': False}}}
        package = types.ModuleType('account_helpers')
        package.AccountSettings = _Settings
        sys.modules['account_helpers'] = package
        sys.modules['account_helpers.AccountSettings'] = module
        try:
            repaired = compatibility._sanitize_account_filters()
        finally:
            sys.modules.pop('account_helpers', None)
            sys.modules.pop('account_helpers.AccountSettings', None)

        self.assertEqual(['CAROUSEL_FILTER_2'], repaired)
        self.assertEqual({'CAROUSEL_FILTER_2': {'elite': False}}, writes)

    def test_filter_sanitizer_replaces_a_non_mapping_saved_filter(self):
        defaults = {'CAROUSEL_FILTER_2': {'elite': False}}
        writes = {}

        class _Settings(object):
            @staticmethod
            def getFilter(name):
                return 'not-a-mapping'

            @staticmethod
            def setFilter(name, value):
                writes[name] = value

        class _Module(object):
            KEY_FILTERS = 'FILTERS'
            DEFAULT_VALUES = {'FILTERS': defaults}
            AccountSettings = _Settings

        repaired = compatibility._sanitize_account_filters(_Module)

        self.assertEqual(['CAROUSEL_FILTER_2'], repaired)
        self.assertEqual({'CAROUSEL_FILTER_2': {'elite': False}}, writes)

    def test_filter_sanitizer_keeps_a_filter_that_has_no_key_schema(self):
        # #1513 stores the shown promo URLs as a set under an empty default.
        defaults = {'PROMO': {}, 'CAROUSEL_FILTER_2': {'elite': False}}
        writes = {}

        class _Settings(object):
            @staticmethod
            def getFilter(name):
                return set(['seen']) if name == 'PROMO' else {'elite': True}

            @staticmethod
            def setFilter(name, value):
                writes[name] = value

        class _Module(object):
            KEY_FILTERS = 'FILTERS'
            DEFAULT_VALUES = {'FILTERS': defaults}
            AccountSettings = _Settings

        repaired = compatibility._sanitize_account_filters(_Module)

        self.assertEqual([], repaired)
        self.assertEqual({}, writes)

    def test_eula_version_survives_server_restart_and_can_be_deleted(self):
        eula_contract = CONTRACT['intUserSettings']
        self.assertEqual(
            eula_contract['addCommand'], commands.CMD_ADD_INT_USER_SETTINGS)
        self.assertEqual(
            eula_contract['deleteCommand'],
            commands.CMD_DEL_INT_USER_SETTINGS)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'account_state.json')
            first_state = AccountState(path)
            first_server = FakeServer(
                lambda: self.player,
                lambda delay, fn: self.pending.append((delay, fn)),
                {'account_state': first_state})
            eula_key = eula_contract['eulaVersionKey']

            first_server.doCmdIntArr(
                41, commands.CMD_ADD_INT_USER_SETTINGS, [eula_key, 17])
            self.assertEqual([], self.player.responses)
            self._run()
            self.assertEqual(
                (41, commands.RES_SUCCESS, ''), self.player.responses[-1])

            restarted_state = AccountState(path)
            restarted_server = FakeServer(
                lambda: self.player,
                lambda delay, fn: self.pending.append((delay, fn)),
                {'account_state': restarted_state})
            restarted_server.doCmdInt3(
                42, commands.CMD_SYNC_DATA, 0, 0, 0)
            self._run()
            synced = pickle.loads(self.player.ext_responses[-1][3])
            self.assertEqual({eula_key: 17}, synced['intUserSettings'])

            restarted_server.doCmdIntArr(
                43, commands.CMD_DEL_INT_USER_SETTINGS, [eula_key])
            self._run()
            self.assertEqual(
                (43, commands.RES_SUCCESS, ''), self.player.responses[-1])
            self.assertEqual({}, AccountState(path).snapshot())

    def test_malformed_integer_settings_fail_without_mutating_state(self):
        state = AccountState(path=None)
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'account_state': state})

        server.doCmdIntArr(
            44, commands.CMD_ADD_INT_USER_SETTINGS, [54, 17, 99])
        self._run()

        self.assertEqual(commands.RES_FAILURE, self.player.responses[-1][1])
        self.assertEqual({}, state.snapshot())

    def test_stream_response_has_crc_and_pickled_body(self):
        self.server.doCmdInt3(33, commands.CMD_SYNC_SHOP, 0, 0, 0)
        self._run()
        self.assertEqual([(33, commands.RES_STREAM, '')],
                         self.player.responses)
        self.assertTrue(
            CONTRACT['deliveryOrder']['streamCommandResponseBeforePayload'])
        self.assertEqual([], self.player.streams)
        self._run()
        request_id, desc, payload = self.player.streams[0]
        corrupted, original_length, packet_length, original_crc, crc = desc
        self.assertEqual(33, request_id)
        self.assertFalse(corrupted)
        self.assertEqual(len(payload), original_length)
        self.assertEqual(len(payload), packet_length)
        self.assertEqual(zlib.crc32(payload) & 0xffffffff, crc)
        self.assertEqual(crc, original_crc)
        shop = pickle.loads(zlib.decompress(payload))
        self.assertIsInstance(shop['items']['itemPrices'], _ItemsPrices)
        self.assertIsInstance(
            shop['defaults']['items']['itemPrices'], _ItemsPrices)
        self.assertEqual(0.5, shop['sellPriceFactor'])
        shop_contract = CONTRACT['shop']
        self.assertTrue(set(shop_contract['directKeys']).issubset(shop))
        self.assertEqual(
            set(shop_contract['itemsDirectKeys']), set(shop['items']))
        self.assertEqual(
            set(shop_contract['goodiesDirectKeys']), set(shop['goodies']))
        self.assertEqual(
            set(shop_contract['itemsDirectKeys']),
            set(shop['defaults']['items']))
        self.assertEqual(
            set(shop_contract['goodiesDirectKeys']),
            set(shop['defaults']['goodies']))
        for key, arity in shop_contract['tupleArities'].items():
            self.assertEqual(arity, len(shop[key]), key)
        for key, index in shop_contract[
                'nonEmptyPriceScheduleIndices'].items():
            self.assertTrue(shop[key][index], key)
        for key, index in shop_contract['positiveIntegerIndices'].items():
            self.assertGreater(shop[key][index], 0, key)
        for key in shop_contract['positiveIntegerValues']:
            self.assertGreater(shop[key], 0, key)
            self.assertEqual(shop[key], shop['defaults'][key])
        for cost in shop['tankmanCost']:
            self.assertEqual(
                set(shop_contract['tankmanCostDirectKeys']), set(cost))
        currency_mappings = {
            'paidRemovalCost': {'gold': 0},
            'paidDeluxeRemovalCost': {'crystal': 0},
        }
        self.assertEqual(
            set(currency_mappings), set(shop_contract['currencyMappings']))
        for key, expected in currency_mappings.items():
            self.assertIsInstance(shop[key], dict)
            self.assertEqual(expected, shop[key])
            self.assertIsInstance(shop['defaults'][key], dict)
            self.assertEqual(expected, shop['defaults'][key])
        ref_contract = shop_contract['refSystem']
        ref_system = shop['refSystem']
        self.assertEqual(set(ref_contract['directKeys']), set(ref_system))
        self.assertEqual(ref_contract['disabledDefaults'], ref_system)
        self.assertIs(type(ref_system['posByXPinTeam']), int)

    def test_sync_data_dispatches_only_its_registered_shape(self):
        self.server.doCmdInt3(34, commands.CMD_SYNC_DATA, 6, 0, 0)
        self._run()
        request_id, result_id, error, ext = self.player.ext_responses[0]
        self.assertEqual(34, request_id)
        self.assertEqual(commands.RES_SUCCESS, result_id)
        self.assertEqual('', error)
        data = pickle.loads(ext)
        self.assertEqual(7, data['rev'])
        self.assertEqual(set(range(1, 13)), set(data['inventory']))
        self.assertEqual({}, data['inventory'][1]['compDescr'])

    def test_sync_data_populates_all_exact_lobby_consumer_caches(self):
        self.server.doCmdInt3(37, commands.CMD_SYNC_DATA, 0, 0, 0)
        self._run()
        value = pickle.loads(self.player.ext_responses[0][3])

        sync_contract = CONTRACT['syncData']
        self.assertTrue(set(sync_contract['directKeys']).issubset(value))
        self.assertEqual({}, value['quests'])
        self.assertEqual({}, value['tokens'])
        self.assertEqual(
            set(sync_contract['groupLocksDirectKeys']),
            set(value['groupLocks']))
        self.assertEqual(
            set(sync_contract['accountDirectKeys']), set(value['account']))
        self.assertEqual(
            set(sync_contract['statsDirectKeys']), set(value['stats']))
        self.assertEqual(
            set(sync_contract['cacheDirectKeys']), set(value['cache']))
        play_limits = value['stats']['playLimits']
        self.assertEqual(
            sync_contract['playLimitsTupleArities'][0], len(play_limits))
        for period in play_limits:
            self.assertEqual(
                sync_contract['playLimitsTupleArities'][1], len(period))
        self.assertEqual((), value['badges'])

        personal_missions = value['potapovQuests']
        pm_contract = sync_contract['potapovQuests']
        self.assertEqual(
            set(pm_contract['directKeys']), set(personal_missions))
        self.assertEqual('', personal_missions['compDescr'])
        for quest_type in ('regular', 'training'):
            progress = personal_missions[quest_type]
            self.assertEqual(
                set(pm_contract['progressDirectKeys']), set(progress))
            self.assertEqual(0, progress['slots'])
            self.assertEqual([], progress['selected'])
            self.assertEqual({}, progress['lastIDs'])

    def test_selected_vehicle_uses_exact_vehicle_item_index(self):
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {
                'selected_vehicle': SELECTED_VEHICLE,
                'items_prices_factory': _ItemsPrices,
            })
        server.doCmdInt3(35, commands.CMD_SYNC_DATA, 0, 0, 0)
        self._run()
        data = pickle.loads(self.player.ext_responses[0][3])
        inventory_contract = CONTRACT['inventory']
        self.assertEqual(
            set(inventory_contract['itemTypeIndices']),
            set(data['inventory']))
        vehicle_data = data['inventory'][1]
        self.assertEqual(
            set(inventory_contract['vehicleDirectKeys']), set(vehicle_data))
        self.assertEqual(
            set(inventory_contract['tankmanDirectKeys']),
            set(data['inventory'][8]))
        self.assertEqual({9: b'compact'}, data['inventory'][1]['compDescr'])
        for key, arity in inventory_contract[
                'selectedVehicleTupleArities'].items():
            self.assertEqual(arity, len(vehicle_data[key][9]), key)
        for key in inventory_contract['selectedVehicleMappingValues']:
            self.assertIsInstance(vehicle_data[key][9], dict)
        for key, length in inventory_contract[
                'selectedVehicleSequenceLengths'].items():
            self.assertEqual(length, len(vehicle_data[key][9]), key)
        self.assertEqual((0, 100), vehicle_data['repair'][9])
        self.assertEqual((0, 0), vehicle_data['lock'][9])
        self.assertEqual([101, 102], vehicle_data['crew'][9])
        self.assertNotIn(9, vehicle_data['lastCrew'])
        self.assertEqual(
            {101: b'commander', 102: b'driver'},
            data['inventory'][8]['compDescr'])
        self.assertEqual(
            {101: 9, 102: 9}, data['inventory'][8]['vehicle'])
        self.assertEqual(
            set(vehicle_data['crew'][9]),
            set(data['inventory'][8]['compDescr']))
        for item_type in inventory_contract[
                'requiredComponentItemTypeIndices']:
            self.assertTrue(data['inventory'][item_type], item_type)
        shell_type = inventory_contract['shellItemTypeIndex']
        self.assertTrue(data['inventory'][shell_type])
        self.assertEqual(0, len(vehicle_data['shells'][9]) % 2)
        self.assertEqual({}, data['inventory'][1]['shellsLayout'][9])
        self.assertEqual(
            ((86400, ''), (604800, '')), data['stats']['playLimits'])
        self.assertEqual({}, data['inventory'][9])
        self.assertEqual({}, data['inventory'][11])
        self.assertEqual({1: {}, 2: {}, 3: {}}, data['inventory'][12])

    def test_account_artefact_catalogue_reaches_devices_and_equipment(self):
        garage = copy.deepcopy(SELECTED_VEHICLE)
        # bootstrap publishes these account-wide, not inside a vehicle record.
        garage['inventoryItems'] = dict(garage['inventoryItems'])
        garage['inventoryItems'][9] = {9001: 200, 9002: 200}
        garage['inventoryItems'][11] = {11001: 200}
        garage['shopItemPrices'] = dict(garage['shopItemPrices'])
        for compact_descr in (9001, 9002, 11001):
            garage['shopItemPrices'][compact_descr] = {
                'credits': 0, 'gold': 0}

        value = account_data.sync_data(selected_vehicle=garage)

        self.assertEqual({9001: 200, 9002: 200}, value['inventory'][9])
        self.assertEqual({11001: 200}, value['inventory'][11])
        # The per-vehicle records stay untouched by the account catalogue.
        self.assertEqual({10010: 20, 10011: 10}, value['inventory'][10])

    def test_full_garage_expands_every_vehicle_and_tankman_foreign_key(self):
        garage = _full_garage_snapshot()
        value = account_data.sync_data(selected_vehicle=garage)
        inventory = value['inventory']

        self.assertEqual(
            {9: b'compact', 10: b'compact-2'},
            inventory[1]['compDescr'])
        self.assertEqual([101, 102], inventory[1]['crew'][9])
        self.assertEqual([201, 202], inventory[1]['crew'][10])
        self.assertEqual(
            {101: 9, 102: 9, 201: 10, 202: 10},
            inventory[8]['vehicle'])
        self.assertEqual(
            {101, 102, 201, 202},
            set(inventory[8]['compDescr']))
        for item_type in tuple(range(2, 8)) + (10,):
            expected = set(garage['inventoryItems'][item_type])
            self.assertEqual(expected, set(inventory[item_type]), item_type)
        for vehicle_id in (9, 10):
            self.assertEqual((0, 0), inventory[1]['lock'][vehicle_id])
            self.assertEqual(3, len(inventory[1]['eqs'][vehicle_id]))
            self.assertEqual(3, len(inventory[1]['eqsLayout'][vehicle_id]))

    def test_full_garage_rejects_duplicate_vehicle_and_tankman_ids(self):
        cases = (
            ('vehicle inventory ids must be unique',
             lambda value: value['vehicles'][1].update(id=9)),
            ('tankman inventory ids must be unique',
             lambda value: (
                 value['vehicles'][1].update(crew=[101, 202]),
                 value['vehicles'][1]['tankmen'].update(
                     {101: value['vehicles'][1]['tankmen'].pop(201)}))),
            ('vehicle type compact descriptors must be unique',
             lambda value: value['vehicles'][1].update(
                 vehicleTypeCompactDescr=50001)),
            ('every garage vehicle type must have a shop price',
             lambda value: value['shopItemPrices'].pop(50002)),
            ('every garage vehicle, module and shell must be unlocked',
             lambda value: value['unlockItemCompactDescrs'].remove(3004)),
            ('garage inventory must contain every installed item',
             lambda value: value['inventoryItems'][10].__setitem__(
                 11010, 29)),
        )
        for message, mutate in cases:
            garage = _full_garage_snapshot()
            mutate(garage)
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    account_data.sync_data(selected_vehicle=garage)

    def test_offline_account_is_well_funded_and_all_vehicles_are_elite(self):
        garage = _full_garage_snapshot()
        value = account_data.sync_data(selected_vehicle=garage)
        stats = value['stats']

        self.assertEqual(account_data.OFFLINE_CREDITS, stats['credits'])
        self.assertEqual(account_data.OFFLINE_GOLD, stats['gold'])
        self.assertEqual(account_data.OFFLINE_FREE_XP, stats['freeXP'])
        self.assertEqual(account_data.OFFLINE_GARAGE_SLOTS, stats['slots'])
        self.assertEqual(
            account_data.OFFLINE_BARRACKS_BERTHS, stats['berths'])
        self.assertEqual({50001, 50002}, set(stats['vehTypeXP']))
        self.assertEqual({50001, 50002}, stats['eliteVehicles'])
        self.assertTrue(
            garage['unlockItemCompactDescrs'].issubset(stats['unlocks']))

    def test_incomplete_selected_vehicle_is_rejected_before_hangar_build(self):
        with self.assertRaisesRegex(ValueError, 'crew and tankmen'):
            account_data.sync_data(
                selected_vehicle={'id': 9, 'compDescr': b'compact'})

    def test_selected_vehicle_relational_contract_rejects_semantic_empties(self):
        cases = (
            ('crew and tankmen', lambda value: value.update(crew=[])),
            ('crew ids must be positive',
             lambda value: (
                 value.update(crew=[-101, 102]),
                 value['tankmen'].update(
                     {-101: value['tankmen'].pop(101)}))),
            ('crew ids must resolve',
             lambda value: value['tankmen'].pop(102)),
            ('lock must contain two', lambda value: value.update(lock=0)),
            ('health must be positive',
             lambda value: value.update(repair=(0, 0))),
            ('eqs must contain three', lambda value: value.update(eqs=[])),
            ('descriptor/count pairs',
             lambda value: value.update(shells=[10010])),
            ('item type 4 must be non-empty',
             lambda value: value['inventoryItems'].__setitem__(4, {})),
            ('must have shop prices',
             lambda value: value['shopItemPrices'].pop(2004)),
            ('must contain valid currencies',
             lambda value: value['shopItemPrices'].__setitem__(
                 2004, {'bonds': 0})),
            ('must be a currency mapping or tuple',
             lambda value: value['shopItemPrices'].__setitem__(
                 2004, [0, 0])),
            ('shell layout and inventory must match',
             lambda value: value['inventoryItems'][10].__setitem__(
                 10010, 19)),
            ('shop nation count must be positive',
             lambda value: value.update(shopNationCount=0)),
            ('customization catalogue must be non-empty',
             lambda value: value.update(customizationItemCount=0)),
        )
        for message, mutate in cases:
            selected = copy.deepcopy(SELECTED_VEHICLE)
            mutate(selected)
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    account_data.sync_data(selected_vehicle=selected)

    def test_selected_vehicle_shop_catalog_prices_every_required_item(self):
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {
                'selected_vehicle': SELECTED_VEHICLE,
                'items_prices_factory': _ItemsPrices,
            })
        server.doCmdInt3(46, commands.CMD_SYNC_SHOP, 0, 0, 0)
        self._run()
        self._run()
        shop = pickle.loads(zlib.decompress(self.player.streams[-1][2]))
        prices = shop['items']['itemPrices']
        self.assertIsInstance(prices, _ItemsPrices)
        self.assertEqual(set(SELECTED_VEHICLE['shopItemPrices']), set(prices))
        self.assertEqual({'credits': 0}, prices[12001])
        self.assertEqual({'credits': 0}, prices[12002])
        self.assertEqual(prices, shop['defaults']['items']['itemPrices'])
        for key in CONTRACT['shop']['nationIndexedMappingItemKeys']:
            self.assertEqual(
                SELECTED_VEHICLE['shopNationCount'],
                len(shop['items'][key]), key)
            self.assertEqual(
                SELECTED_VEHICLE['shopNationCount'],
                len(shop['defaults']['items'][key]), key)
            self.assertTrue(all(
                isinstance(value, dict) for value in shop['items'][key]))
        for key in CONTRACT['shop']['nationIndexedSetItemKeys']:
            self.assertEqual(
                SELECTED_VEHICLE['shopNationCount'],
                len(shop['items'][key]), key)
            self.assertEqual(
                SELECTED_VEHICLE['shopNationCount'],
                len(shop['defaults']['items'][key]), key)
            self.assertTrue(all(
                isinstance(value, set) for value in shop['items'][key]))

    def test_account_validator_receives_only_validatable_inventory_shapes(self):
        value = account_data.sync_data()
        inventory = value['inventory']
        validator = CONTRACT['accountValidator']

        for item_type in validator['emptyItemTypeIndicesWithoutSelectedVehicle']:
            if item_type == 12:
                self.assertEqual({1: {}, 2: {}, 3: {}}, inventory[item_type])
            else:
                self.assertEqual({}, inventory[item_type], item_type)
        self.assertIsInstance(inventory[1]['compDescr'], Mapping)
        self.assertIsInstance(inventory[8]['compDescr'], Mapping)
        self.assertIsInstance(value['stats']['eliteVehicles'], set)
        self.assertEqual({}, inventory[1]['compDescr'])
        self.assertEqual({}, inventory[8]['compDescr'])

        selected_inventory = account_data.sync_data(
            selected_vehicle=SELECTED_VEHICLE)['inventory']
        for item_type in validator['emptyItemTypeIndicesWithSelectedVehicle']:
            if item_type == 12:
                self.assertEqual(
                    {1: {}, 2: {}, 3: {}}, selected_inventory[item_type])
            else:
                self.assertEqual({}, selected_inventory[item_type], item_type)

        bootstrap = (
            CLIENT_SCRIPTS / 'gui' / 'mods' / 'offline_lan_0922' /
            'bootstrap.py').read_text(encoding='utf-8')
        self.assertEqual(
            'VehicleDescr.makeCompactDescr',
            validator['selectedVehicleCompDescrProducer'])
        self.assertIn('descriptor.makeCompactDescr()', bootstrap)

    def test_entire_lobby_controller_chain_receives_safe_nested_shapes(self):
        values = {
            'syncData': account_data.sync_data(),
            'shop': account_data.shop(),
            'serverSettings': compatibility._SERVER_SETTINGS,
        }
        chain = CONTRACT['lobbyControllerChain']

        for path in chain['mappingPaths']:
            self.assertIsInstance(_contract_path(values, path), Mapping, path)
        for path in chain['numberPaths']:
            value = _contract_path(values, path)
            self.assertIsInstance(value, (int, float), path)
            self.assertNotIsInstance(value, bool, path)
        for path in chain['booleanPaths']:
            self.assertIs(type(_contract_path(values, path)), bool, path)
        for path, minimum in chain['minimumSequenceLengths'].items():
            self.assertGreaterEqual(
                len(_contract_path(values, path)), minimum, path)
        for path, arity in chain['tupleArities'].items():
            self.assertEqual(
                arity, len(_contract_path(values, path)), path)

        # Exact #1513 ShopRequester supplies disabled objects for these
        # optional keys when they are absent. Keep them absent instead of
        # publishing a second, unverified server-side schema.
        for key in chain['defaultedShopKeys']:
            self.assertNotIn(key, values['shop'])
        self.assertEqual({}, values['syncData']['newYear'])
        self.assertTrue(chain['newYearEmptySyncDataIsSupported'])
        self.assertEqual(
            set(chain['directServerSettingsKeys']), {'wallet'})

    def test_offline_wallet_and_tutorials_are_terminal_not_syncing(self):
        sync_data = account_data.sync_data()

        self.assertTrue(
            sync_data['cache']['mayConsumeWalletResources'])
        self.assertEqual(
            33553532, sync_data['stats']['tutorialsCompleted'])
        self.assertFalse(
            compatibility._SERVER_SETTINGS['isTutorialEnabled'])

    def test_spg_stun_feature_matches_authoritative_battle_state(self):
        features = compatibility._SERVER_SETTINGS[
            'spgRedesignFeatures']

        self.assertEqual({
            'stunEnabled': True,
            'markTargetAreaEnabled': False,
        }, features)

    def test_dossier_stream_matches_native_two_tuple_consumer(self):
        self.server.doCmdInt3(36, commands.CMD_SYNC_DOSSIERS, 4, 0, 0)
        self._run()
        self._run()
        value = pickle.loads(zlib.decompress(self.player.streams[0][2]))
        self.assertEqual(
            CONTRACT['dossiers']['streamTupleArity'], len(value))
        for change in value[1]:
            self.assertEqual(
                CONTRACT['dossiers']['changeTupleArity'], len(change))
        self.assertEqual((1, []), value)

    def test_old_chat_mailbox_does_not_echo_command_as_chat_action(self):
        events = []
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'receive_chat_action': events.append})

        self.assertTrue(server.chatCommandFromClient(
            41, 9, 0, -1, 0, '', ''))
        self.assertTrue(server.chatCommandFromClient(
            42, 10, 0, -1, 0, '', ''))
        self.assertFalse(
            CONTRACT['chatAction']['publishedByOfflineServer'])
        self.assertFalse(
            CONTRACT['chatAction']['commandIndexMayBeUsedAsActionIndex'])
        self.assertEqual([], events)
        self.assertEqual([], self.pending)

    def test_chat2_mailbox_is_present_and_safely_one_way(self):
        self.assertTrue(
            self.server.messenger_onActionByClient_chat2(1, 43, ()))
        self.assertEqual([], self.pending)

    def test_fake_server_mailbox_arities_match_exact_contract(self):
        for name, arity in CONTRACT[
                'mailboxAritiesExcludingSelf'].items():
            method = getattr(FakeServer, name)
            parameters = list(inspect.signature(method).parameters)
            self.assertEqual('self', parameters[0], name)
            self.assertEqual(arity, len(parameters) - 1, name)

    def test_initial_account_and_lobby_direct_keys_match_contract(self):
        settings_contract = CONTRACT['initialServerSettings']
        self.assertTrue(
            set(settings_contract['directKeys']).issubset(
                compatibility._SERVER_SETTINGS))
        self.assertTrue(
            set(settings_contract['rankedConfigDirectKeys']).issubset(
                compatibility._SERVER_SETTINGS['ranked_config']))
        self.assertEqual(
            settings_contract['elenSettings'],
            compatibility._SERVER_SETTINGS['elenSettings'])
        for key, arity in settings_contract['tupleArities'].items():
            self.assertEqual(
                arity, len(compatibility._SERVER_SETTINGS[key]), key)
        roaming_hosts = compatibility._SERVER_SETTINGS['roaming'][
            settings_contract['roamingHostsIndex']]
        self.assertIsInstance(roaming_hosts, list)
        for host in roaming_hosts:
            self.assertEqual(
                settings_contract['roamingHostTupleArity'], len(host))
        self.assertTrue(
            set(CONTRACT['lobbyGuiContext']['directKeys']).issubset(
                compatibility._LOBBY_GUI_CONTEXT))

    def test_contract_is_driven_by_current_producer_functions(self):
        sync_value = account_data.sync_data()
        shop_value = account_data.shop()
        dossier_value = account_data.dossiers()
        self.assertTrue(
            set(CONTRACT['syncData']['directKeys']).issubset(sync_value))
        self.assertTrue(
            set(CONTRACT['shop']['directKeys']).issubset(shop_value))
        self.assertEqual(
            CONTRACT['dossiers']['streamTupleArity'], len(dossier_value))


if __name__ == '__main__':
    unittest.main()
