"""Store filter handoff and waiting ownership after the #1513 shop report."""

import copy
import types
import unittest

import test_port_0922_garage as fixture
from test_port_0922_offline_services_ui import native_modules


def native_filter_defaults():
    # Reviewed 0.9.22 AccountSettings schemas; production reads the client's
    # own defaults instead of maintaining another catalogue of filter fields.
    result = {'scroll_to_item': None}
    for prefix in ('shop', 'inventory'):
        result[prefix + '_current'] = (-1, 'vehicle', False)
        result[prefix + '_vehicle'] = {
            'selectedTypes': [False] * 5, 'selectedLevels': [False] * 10,
            'extra': ['locked'] if prefix == 'shop' else ['brocken', 'locked']}
        result[prefix + '_module'] = {
            'fitsType': 'myVehicles', 'vehicleCD': -1,
            'extra': ['locked', 'inHangar'] if prefix == 'shop' else [],
            'itemTypes': ['vehicleGun', 'vehicleTurret', 'vehicleEngine',
                          'vehicleChassis', 'vehicleRadio']}
        result[prefix + '_shell'] = {'fitsType': 'myVehicleGun', 'vehicleCD': -1,
            'itemTypes': ['ARMOR_PIERCING', 'ARMOR_PIERCING_CR',
                          'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE']}
        for name in ('optionalDevice', 'equipment'):
            result[prefix + '_' + name] = {
                'fitsType': 'myVehicle', 'vehicleCD': -1, 'extra': ['onVehicle']}
        result[prefix + '_battleBooster'] = {'targetType': 'allKind'}
    result['shop_vehicle']['obtainingType'] = 'vehicle'
    for name in ('restoreVehicle', 'tradeInVehicle'):
        result['shop_' + name] = {'obtainingType': name,
            'selectedTypes': [False] * 5, 'selectedLevels': [False] * 10}
    return result


class StoreFilterTests(unittest.TestCase):
    def setUp(self):
        self.ui = fixture._load_port_module('offline_services_ui')
        self.defaults = native_filter_defaults()
        self.saved = copy.deepcopy(self.defaults)
        self.waits = []
        self.wait_events = []
        self.table_requests = []
        self.populated = []
        self.failure = None
        owner = self

        class Waiting(object):
            @staticmethod
            def show(name):
                owner.waits.append(name)
                owner.wait_events.append(('show', name))

            @staticmethod
            def hide(name):
                owner.waits.remove(name)
                owner.wait_events.append(('hide', name))

        def flash_to_dict(value):
            # Native Flash input is a PyGFx object, not a Python mapping.
            if hasattr(value, 'children'):
                return {key: flash_to_dict(item) for key, item in value.children.items()
                        if key not in ('isPrototypeOf', 'propertyIsEnumerable', 'hasOwnProperty')}
            return value

        class StoreComponent(object):
            def getName(self):
                return self.prefix

            def _populate(self):
                # Stock initialization reads saved fields before requesting
                # the first table, including vehicleCD and both vehicle lists.
                for key, defaults in owner.defaults.items():
                    if key.startswith(self.prefix + '_') and isinstance(defaults, dict):
                        for field in defaults:
                            if owner.saved[key][field] is None:
                                raise TypeError(field)
                owner.populated.append(self.prefix)

            def _setTableData(self, filters, nation, item_type, actions, item_cd):
                defaults = owner.defaults[self.prefix + '_' + item_type]
                for field in defaults:
                    # Match the native tab's direct subscripts; do not hide a
                    # missing key behind a permissive fake or dict.get.
                    if filters[field] is None:
                        raise TypeError(field)
                if owner.failure:
                    raise owner.failure
                owner.table_requests.append((self.prefix, item_type,
                    copy.deepcopy(filters), nation, actions, item_cd))

            def requestTableData(self, nation, actions, item_type, filters):
                # The native Shop and Inventory both persist before building
                # and leave their wait open if building raises.
                Waiting.show(self.waiting)
                filters = flash_to_dict(filters)
                item_cd = owner.saved['scroll_to_item']
                owner.saved[self.prefix + '_current'] = (nation, item_type, actions)
                owner.saved[self.prefix + '_' + item_type] = filters
                owner.saved['scroll_to_item'] = None
                self._setTableData(filters, nation, item_type, actions, item_cd)
                Waiting.hide(self.waiting)

        class Shop(StoreComponent):
            prefix, waiting = 'shop', 'updateShop'

        class Inventory(StoreComponent):
            prefix, waiting = 'inventory', 'updateInventory'

        self.Shop, self.Inventory = Shop, Inventory
        self.Waiting = Waiting
        self.native_request = StoreComponent.requestTableData
        self.native_populate = StoreComponent._populate
        self.StoreComponent = StoreComponent
        exports = {
            'account_helpers.AccountSettings': {'AccountSettings': types.SimpleNamespace(
                getFilterDefault=self.defaults.get, getFilter=self.saved.__getitem__,
                setFilter=self.saved.__setitem__),
                'DEFAULT_VALUES': {'filters': self.defaults}, 'KEY_FILTERS': 'filters'},
            'gui.shared.utils': {'flashObject2Dict': flash_to_dict},
            'gui.Scaleform.Waiting': {'Waiting': Waiting},
            'gui.Scaleform.daapi.view.lobby.store.StoreComponent': {'StoreComponent': StoreComponent},
            'gui.Scaleform.daapi.view.lobby.store.Shop': {'Shop': Shop},
            'gui.Scaleform.daapi.view.lobby.store.Inventory': {'Inventory': Inventory},
        }
        self.modules = native_modules(exports)
        self.modules.__enter__()
        self.addCleanup(self.modules.__exit__, None, None, None)
        self.addCleanup(self.ui.uninstall)

    def test_report_restore_to_buy_missing_extra_reproduces_then_recovers(self):
        payload = copy.deepcopy(self.defaults['shop_restoreVehicle'])
        payload['obtainingType'] = 'vehicle'
        with self.assertRaisesRegex(KeyError, 'extra'):
            self.Shop().requestTableData(0, False, 'vehicle', payload)
        self.assertEqual(['updateShop'], self.waits)
        self.assertNotIn('extra', self.saved['shop_vehicle'])
        self.Waiting.hide('updateShop')  # Re-enter with the fixed build.
        self.ui._install_store_filters()
        self.Shop()._populate()
        self.assertEqual(['locked'], self.saved['shop_vehicle']['extra'])
        for unused in range(3):
            for category in ('restoreVehicle', 'vehicle', 'tradeInVehicle', 'vehicle'):
                incoming = copy.deepcopy(payload)
                incoming['obtainingType'] = category
                self.Shop().requestTableData(0, False, category,
                    types.SimpleNamespace(children=incoming))
                self.assertEqual(category, self.table_requests[-1][2]['obtainingType'])
                self.assertEqual([], self.waits)
        self.assertEqual(['locked'], self.table_requests[-1][2]['extra'])
        self.assertNotIn('extra', payload)

    def test_missing_and_null_fields_in_all_fourteen_native_categories(self):
        self.ui._install_store_filters()
        before_defaults = copy.deepcopy(self.defaults)
        categories = [(key, value) for key, value in self.defaults.items()
                      if isinstance(value, dict)]
        self.assertEqual(14, len(categories))
        for key, defaults in categories:
            prefix, category = key.split('_', 1)
            view = self.Shop() if prefix == 'shop' else self.Inventory()
            for missing in defaults:
                for null in (False, True):
                    with self.subTest(category=key, missing=missing, null=null):
                        self.saved[key] = copy.deepcopy(defaults)
                        del self.saved[key][missing]
                        incoming = copy.deepcopy(self.saved[key])
                        if null:
                            incoming[missing] = None
                        unchanged = copy.deepcopy(incoming)
                        view.requestTableData(1, False, category,
                            types.SimpleNamespace(children=incoming))
                        self.assertEqual(defaults, self.table_requests[-1][2])
                        self.assertEqual(defaults, self.saved[key])
                        self.assertEqual(unchanged, incoming)
                        self.assertEqual([], self.waits)
        self.assertEqual(before_defaults, self.defaults)

    def test_keeps_saved_choices_and_explicit_empty_filters_without_cross_tab_leaks(self):
        self.ui._install_store_filters()
        self.saved['shop_vehicle']['extra'] = ['inHangar']
        self.saved['shop_vehicle']['selectedTypes'][2] = True
        self.saved['inventory_vehicle']['extra'] = ['brocken']
        incoming = {'selectedLevels': [False] * 9 + [True], 'obtainingType': 'restoreVehicle'}
        self.Shop().requestTableData(4, True, 'vehicle', incoming)
        row = self.table_requests[-1]
        self.assertEqual(['inHangar'], row[2]['extra'])
        self.assertTrue(row[2]['selectedTypes'][2])
        self.assertTrue(row[2]['selectedLevels'][9])
        self.assertEqual('vehicle', row[2]['obtainingType'])
        self.assertEqual((4, True), row[3:5])
        self.Shop().requestTableData(-1, False, 'vehicle', {'extra': []})
        self.assertEqual([], self.saved['shop_vehicle']['extra'])
        self.assertEqual(['brocken'], self.saved['inventory_vehicle']['extra'])
        self.assertEqual(['locked'], self.defaults['shop_vehicle']['extra'])

    def test_reopen_repairs_saved_filters_before_native_initialization(self):
        self.ui._install_store_filters()
        for prefix, view in (('shop', self.Shop()), ('inventory', self.Inventory())):
            for key, value in self.defaults.items():
                if key.startswith(prefix + '_') and isinstance(value, dict):
                    self.saved[key] = {}
            view._populate()
            for key, value in self.defaults.items():
                if key.startswith(prefix + '_'):
                    self.assertEqual(value, self.saved[key])
        self.assertEqual(['shop', 'inventory'], self.populated)

    def test_failed_table_releases_only_its_wait_and_next_request_still_works(self):
        self.ui._install_store_filters()
        for view in (self.Shop(), self.Inventory()):
            self.Waiting.show('unrelated')
            self.Waiting.show(view.waiting)
            self.failure = RuntimeError('native row failure')
            with self.assertRaisesRegex(RuntimeError, 'native row failure'):
                view.requestTableData(-1, False, 'vehicle', {})
            self.assertEqual(['unrelated', view.waiting], self.waits)
            self.failure = None
            self.saved['scroll_to_item'] = 123
            view.requestTableData(-1, False, 'vehicle', {})
            self.assertEqual(123, self.table_requests[-1][-1])
            self.assertIsNone(self.saved['scroll_to_item'])
            self.assertEqual(['unrelated', view.waiting], self.waits)
            self.Waiting.hide(view.waiting)
            self.Waiting.hide('unrelated')
        self.assertEqual([], self.waits)

    def test_uninstall_restores_original_controllers_and_wait_ownership(self):
        self.ui._install_store_filters()
        self.ui.uninstall()
        self.assertIs(self.native_request, self.Shop.requestTableData)
        self.assertIs(self.native_request, self.Inventory.requestTableData)
        self.assertIs(self.native_populate, self.StoreComponent._populate)
        payload = copy.deepcopy(self.defaults['inventory_vehicle'])
        self.Inventory().requestTableData(-1, False, 'vehicle', payload)
        self.assertEqual([('show', 'updateInventory'), ('hide', 'updateInventory')],
                         self.wait_events)


if __name__ == '__main__':
    unittest.main()
