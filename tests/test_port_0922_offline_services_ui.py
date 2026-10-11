"""Native service controller contracts and transaction-facing UI regressions."""

import contextlib
import copy
import sys
import types
import unittest
from collections import namedtuple
from unittest import mock

import test_port_0922_garage as fixture


@contextlib.contextmanager
def native_modules(exports):
    modules = {}
    for name in exports:
        bits = name.split('.')
        for end in range(1, len(bits) + 1):
            path = '.'.join(bits[:end])
            if path not in modules:
                module = types.ModuleType(path)
                module.__path__ = []
                modules[path] = module
    for name, values in exports.items():
        modules[name].__dict__.update(values)
    for name, module in modules.items():
        if '.' in name:
            parent, child = name.rsplit('.', 1)
            if child not in modules[parent].__dict__:
                setattr(modules[parent], child, module)
    with mock.patch.dict(sys.modules, modules):
        yield modules


def bond_filter_controls():
    positions = {'obtainingTypeBuyBtn': 0, 'obtainingTypeRestoreBtn': 20,
        'obtainingTypeTradeInBtn': 40, 'vehTypeHeader': 68,
        'listVehicleType': 90, 'vehLevelHeader': 118, 'listVehicleLevels': 140,
        'vehicleFilterExtraName': 168, 'lockedChkBx': 188,
        'inHangarChkBx': 208, 'rentalsChckBx': 228}
    controls = types.SimpleNamespace(**{name: types.SimpleNamespace(
        y=y, visible=True, mouseEnabled=True) for name, y in positions.items()})
    controls.validateNow = lambda: None
    controls.resync = lambda: None
    return controls


class NativeServiceUITests(unittest.TestCase):
    def setUp(self):
        self.ui = fixture._load_port_module('offline_services_ui')
        self.data = {'vehicles': [{'vehicleTypeCompactDescr': 2}],
                     'offlineVehicleOffers': [
                         {'cd': 2, 'name': 'owned', 'price': 8000, 'label': 'Owned'},
                         {'cd': 3, 'name': 'new', 'price': 8000, 'label': 'New'}]}
        patch = mock.patch.object(self.ui, 'snapshot', side_effect=lambda: self.data)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.ui.uninstall)

    def test_item_sort_adapter_installs_once_and_restores_with_services(self):
        class FittingItem(object):
            def __cmp__(self, other):
                return 200 - other

        original = FittingItem.__dict__['__cmp__']
        other_installs = ('_install_directive_fitting', '_install_store_filters', '_install_shop',
            '_install_vehicle_filters_and_recovery', '_install_reserves',
            '_install_account', '_install_daily', '_install_mission_results',
            '_install_settings', '_install_exchange_dialog_limits',
            '_schedule_daily_rollover')
        with native_modules({
                'gui.shared.gui_items.fitting_item': {'FittingItem': FittingItem},
                'gui.mods.offline_lan_0922.crew_voice': {'install': lambda patch: None}
                }), mock.patch.multiple(self.ui, **{
                    name: mock.Mock() for name in other_installs}):
            self.ui.install()
            wrapper = FittingItem.__dict__['__cmp__']
            self.assertIsNot(wrapper, original)
            self.assertEqual(1, FittingItem().__cmp__(100))
            self.ui.install()
            self.assertIs(wrapper, FittingItem.__dict__['__cmp__'])
            self.ui._install_store_filters.assert_called_once_with()
            self.ui.uninstall()
            self.ui.uninstall()
            self.assertIs(original, FittingItem.__dict__['__cmp__'])

    def test_failed_service_startup_restores_item_comparator(self):
        class FittingItem(object):
            def __cmp__(self, other):
                return 200 - other

        original = FittingItem.__dict__['__cmp__']
        with native_modules({
                'gui.shared.gui_items.fitting_item': {'FittingItem': FittingItem}
                }), mock.patch.object(self.ui, '_install_directive_fitting'), \
                mock.patch.object(self.ui, '_install_store_filters',
                                      side_effect=RuntimeError('startup failed')):
            with self.assertRaises(RuntimeError):
                self.ui.install()
            self.assertIs(original, FittingItem.__dict__['__cmp__'])
            self.assertEqual([], self.ui._patches)

    def test_generic_directive_install_and_remove_reach_the_native_layout_processor(self):
        original = mock.Mock(return_value='ordinary installer')
        helper = mock.Mock(side_effect=lambda *args: ('layout', args))
        processor = mock.Mock(side_effect=lambda *args: ('processor', args))
        exports = {
            'gui.shared.gui_items.processors.module': {
                'getInstallerProcessor': original,
                'GUI_ITEM_TYPE': types.SimpleNamespace(BATTLE_BOOSTER=26)},
            'gui.shared.gui_items.processors.vehicle': {
                'VehicleBattleBoosterLayoutProcessor': processor},
            'gui.shared.gui_items.vehicle_equipment': {
                'EquipmentLayoutHelper': helper},
            'gui.shared.gui_items.items_actions.actions': {
                'getInstallerProcessor': original},
        }
        vehicle = object()
        directive = types.SimpleNamespace(itemTypeID=26, intCD=27387)
        with native_modules(exports) as modules:
            producer = modules['gui.shared.gui_items.processors.module']
            consumer = modules['gui.shared.gui_items.items_actions.actions']
            self.ui._install_directive_fitting()
            self.assertIs(producer.getInstallerProcessor, consumer.getInstallerProcessor)
            for install in (True, False):
                for slot in (0, 3):
                    result = consumer.getInstallerProcessor(
                        vehicle, directive, slot, install, True, skipConfirm=True)
                    helper.assert_called_with(
                        vehicle, None, (27387, 1) if install else (0, 0))
                    processor.assert_called_with(
                        vehicle, directive if install else None,
                        ('layout', helper.call_args.args), True)
                    self.assertEqual('processor', result[0])
            original.assert_not_called()
            regular = types.SimpleNamespace(itemTypeID=11)
            self.assertEqual('ordinary installer', consumer.getInstallerProcessor(
                vehicle, regular, 2, False, True, ['conflict'], True))
            original.assert_called_once_with(
                vehicle, regular, 2, False, True, ['conflict'], True)
            self.ui.uninstall()
            self.ui.uninstall()
            self.assertIs(original, producer.getInstallerProcessor)
            self.assertIs(original, consumer.getInstallerProcessor)

    def test_bond_shop_reuses_native_rows_and_filters_owned_vehicles(self):
        class UnboundMethod(object):
            def __init__(self, function):
                self.im_func = function

            def __call__(self, *args, **kwargs):
                return self.im_func(*args, **kwargs)

            def __get__(self, instance, owner):
                return (self if instance is None else
                        self.im_func.__get__(instance, owner))

        class Python2ShopType(type):
            def __getattribute__(cls, name):
                value = super().__getattribute__(name)
                if name == 'requestTableData' and isinstance(value, types.FunctionType):
                    # Python 2 class access wraps a function in an unbound
                    # method. Identity guards must inspect the raw class dict.
                    return UnboundMethod(value)
                return value

        class Shop(object, metaclass=Python2ShopType):
            def __init__(self):
                self.disposed = False
                self.native_table_alive = True
                self.navigation = []

            def getName(self):
                return 'shop'

            def requestTableData(self, nation, actions, item_type, filters):
                account_settings.setFilter('shop_current', (nation, item_type, actions))
                account_settings.setFilter('shop_' + item_type, dict(filters))
                return nation, actions, item_type, filters

            def fireEvent(self, event, scope):
                self.navigation.append((event, scope, copy.deepcopy(saved_filters)))
                self.native_table_alive = False
                self.disposed = True

            def _setTableData(self, *args):
                self.table_request = args

            def as_initFiltersDataS(self, *args):
                self.flashObject.form.menu.dataProvider[:] = [types.SimpleNamespace(
                    fittingType=name, enabled=True) for name in
                    ('vehicle', 'module', 'shell', 'equipment')]

            def as_setFilterOptionsS(self, data):
                self.filter_options = data
                # The native setter restores checkbox visibility each time.
                self.flashObject.form.menu.view.currentView.lockedChkBx.visible = True

            def _isDAAPIInited(self):
                return not self.disposed

            def _update(self):
                self.updated = True

        class VehicleTab(object):
            @classmethod
            def getFilterInitData(cls):
                return 'ShopVehiclesFiltersVO', True

            def itemWrapper(self, row):
                prices = self._getItemPrices(row[0])
                return {'type': row[0].icon,
                        'disabled': not self._isPurchaseEnabled(row[0], self._items.stats.money),
                        'price': prices.getSum().price.toMoneyTuple(),
                        'currency': prices.itemPrice.getCurrency(byWeight=False)}

        class Money(object):
            def __init__(self, **values):
                self.values = values

            def toMoneyTuple(self):
                return tuple(self.getSignValue(key) for key in ('credits', 'gold', 'crystal'))

            def getSignValue(self, key):
                return self.values.get(key, 0)

        class ItemPrice(object):
            def __init__(self, price, default):
                self.price, self.defPrice = price, default

            def getCurrency(self, byWeight=True):
                return next(key for key in ('credits', 'gold', 'crystal')
                            if self.price.getSignValue(key))

        class ItemPrices(object):
            def __init__(self, price):
                self.itemPrice = price

            def getSum(self):
                return self.itemPrice

        class StoreView(object):
            def __init__(self):
                # StoreView.onPopulate sets this before Python's as_initS.
                self.flashObject = types.SimpleNamespace(
                    viewStack=types.SimpleNamespace(cache=True))

            def _isDAAPIInited(self):
                return True

            def as_initS(self, data):
                self.data = data

        Settings = namedtuple('Settings', 'alias clazz')
        Settings.replaceSettings = lambda self, values: self._replace(**values)
        original = Settings('storeActions', object)
        current = {'storeActions': original}

        def add(settings):
            if settings.alias in current:
                raise ValueError('duplicate alias')
            current[settings.alias] = settings

        factory = types.SimpleNamespace(getSettings=current.get,
                                        removeSettings=current.pop, addSettings=add)
        account_settings = mock.Mock()
        account_settings.getFilter.return_value = (-1, 'vehicle', False)
        default_vehicle_filter = {'selectedTypes': [False, False],
            'selectedLevels': [False] * 10, 'obtainingType': 'vehicle',
            'extra': ['locked']}
        account_settings.getFilterDefault.return_value = default_vehicle_filter
        pending_callbacks = []
        account = [object()]
        defaults = {'filters': {}}
        exports = {
            'account_helpers.AccountSettings': {'AccountSettings': account_settings,
                'DEFAULT_VALUES': defaults, 'KEY_FILTERS': 'filters'},
            'gui.Scaleform.daapi.settings.views': {'VIEW_ALIAS': types.SimpleNamespace(
                LOBBY_STORE_ACTIONS='storeActions', LOBBY_STORE='store')},
            'gui.Scaleform.daapi.view.lobby.store.StoreView': {'StoreView': StoreView},
            'gui.Scaleform.daapi.view.lobby.store.Shop': {'Shop': Shop},
            'gui.Scaleform.daapi.view.lobby.store.Inventory': {'Inventory': type(
                'Inventory', (), {'requestTableData': lambda *args: None})},
            'gui.Scaleform.daapi.view.lobby.store.StoreComponent': {'StoreComponent': type(
                'StoreComponent', (), {'_populate': lambda self: None})},
            'gui.Scaleform.daapi.view.lobby.store.tabs.shop': {'ShopVehicleTab': VehicleTab},
            'gui.Scaleform.genConsts.STORE_CONSTANTS': {'STORE_CONSTANTS': types.SimpleNamespace(
                STORE_ACTIONS='storeActions', SHOP_LINKAGE='ShopUI', VEHICLE='vehicle')},
            'gui.shared.gui_items.Vehicle': {'VEHICLE_TYPES_ORDER': ['lightTank', 'heavyTank']},
            'gui.shared.money': {'Money': Money},
            'gui.shared.gui_items.gui_item_economics': {
                'ItemPrice': ItemPrice, 'ItemPrices': ItemPrices},
            'gui.Scaleform.framework': {'g_entitiesFactories': factory},
            'gui.shared.utils': {'flashObject2Dict': lambda value:
                                 dict(value) if value is not None else None},
            'gui.Scaleform.Waiting': {'Waiting': mock.Mock()},
            'gui': {'GUI_NATIONS': ['ussr', 'germany']},
            'gui.Scaleform': {'getVehicleTypeAssetPath': lambda name: name,
                'getLevelsAssetPath': lambda name: name},
            'gui.prb_control.settings': {'VEHICLE_LEVELS': range(1, 11)},
            'gui.shared.utils.functions': {'makeTooltip': lambda *args: args},
            'BigWorld': {'player': lambda: account[0],
                'callback': lambda delay, callback: pending_callbacks.append(callback)},
            'gui.shared': {'events': types.SimpleNamespace(
                LoadViewEvent=lambda alias, ctx: types.SimpleNamespace(alias=alias, ctx=ctx)),
                'EVENT_BUS_SCOPE': types.SimpleNamespace(LOBBY='lobby')},
        }
        with native_modules(exports):
            self.ui._install_store_filters()
            self.ui._install_shop()
            self.assertIsNot(Shop.requestTableData,
                             Shop.__dict__['requestTableData'])
            page = StoreView()
            tabs = {'buttonBarData': [
                {'id': 'storeActions', 'linkage': 'StoreActionsViewUI'},
                {'id': 'shop', 'linkage': 'ShopUI'}]}
            page.as_initS(tabs)
            self.assertFalse(page.flashObject.viewStack.cache)
            self.assertEqual('ShopUI', page.data['buttonBarData'][0]['linkage'])
            self.assertEqual('StoreActionsViewUI', tabs['buttonBarData'][0]['linkage'])
            bond_class = current['storeActions'].clazz
            for unused_battle in range(3):
                # Lobby package registration replaces the factory setting
                # when the Account is rebuilt on return from battle.
                current['storeActions'] = original
                page = StoreView()
                page.as_initS(tabs)
                self.assertIs(bond_class, current['storeActions'].clazz)
                self.assertEqual(1, len(self.ui._factory_settings))
                self.assertFalse(page.flashObject.viewStack.cache)
            # Model StoreView.clearCurrentVew / ViewStack.createView: the
            # latter only dispatches NEED_UPDATE on a linkage-cache miss.
            # The same ShopUI linkage must register a fresh controller for
            # either direction of travel, and release the previous one.
            for first, second in (('shop', 'storeActions'),
                                  ('storeActions', 'shop')):
                cached_views = {}
                components = {}
                previous = None
                for alias in (first, second, first):
                    if previous is not None and not page.flashObject.viewStack.cache:
                        del components[previous]
                    linkage = next(tab['linkage'] for tab in page.data['buttonBarData']
                                   if tab['id'] == alias)
                    if linkage not in cached_views:
                        component = (current[alias].clazz() if alias == 'storeActions'
                                     else Shop())
                        components[alias] = component
                        if page.flashObject.viewStack.cache:
                            cached_views[linkage] = component
                    self.assertEqual({alias}, set(components))
                    self.assertEqual(alias == 'storeActions',
                                     isinstance(components[alias], current['storeActions'].clazz))
                    previous = alias
            shop = current['storeActions'].clazz()
            self.assertEqual('shop', shop.getName())
            self.assertFalse(account_settings.setFilter.called)
            # Flash uses the stock name to include the inHangar checkbox;
            # persistence must nevertheless stay separate from regular Shop.
            saved_filters = {
                'offline_bond_current': (1, 'module', True),
                'offline_bond_vehicle': {'selectedTypes': [False, True],
                    'selectedLevels': [False] * 7 + [True, False, False],
                    'obtainingType': 'restoreVehicle',
                    'extra': ['locked', 'inHangar']},
                'shop_current': (0, 'shell', True)}
            account_settings.getFilter.side_effect = lambda key: saved_filters.get(
                key, defaults['filters'].get(key, default_vehicle_filter))
            account_settings.setFilter.side_effect = saved_filters.__setitem__
            self.assertEqual((1, 'vehicle', False),
                             shop._StoreComponent__getCurrentFilter())
            shop._onTableUpdate()
            self.assertEqual((1, 'vehicle', False, None), shop.table_request[1:])
            self.assertEqual(['inHangar'], shop.table_request[0]['extra'])
            self.assertEqual('vehicle', shop.table_request[0]['obtainingType'])
            self.assertEqual({'offline_bond_current', 'offline_bond_vehicle'},
                {call.args[0] for call in account_settings.setFilter.call_args_list})

            controls = bond_filter_controls()
            buttons = [types.SimpleNamespace(visible=True, enabled=True) for unused in range(4)]
            menu = types.SimpleNamespace(dataProvider=[], getButtonAt=buttons.__getitem__,
                validateNow=lambda: None, view=types.SimpleNamespace(currentView=controls))
            actions = types.SimpleNamespace(visible=True, mouseEnabled=True, mouseChildren=True)
            shop.flashObject = types.SimpleNamespace(form=types.SimpleNamespace(menu=menu),
                                                     actionsFilterView=actions)
            shop.as_initFiltersDataS([], '')
            self.assertFalse(actions.visible)
            self.assertFalse(actions.mouseEnabled)
            self.assertFalse(actions.mouseChildren)
            self.assertEqual(['vehicle'], [row.fittingType for index, row in enumerate(menu.dataProvider)
                                          if buttons[index].visible])
            self.assertEqual([True, False, False, False], [button.enabled for button in buttons])
            self.assertEqual([True, False, False, False], [row.enabled for row in menu.dataProvider])
            for unused in range(2):
                shop._StoreComponent__updateFilterOptions('vehicle')
                self.assertEqual([False, True],
                    [row['selected'] for row in shop.filter_options['voData']['vehicleTypes']])
                self.assertFalse(controls.lockedChkBx.visible)
                self.assertFalse(controls.obtainingTypeBuyBtn.visible)
                self.assertTrue(controls.inHangarChkBx.visible)
                self.assertTrue(controls.rentalsChckBx.visible)
                self.assertEqual(20, controls.rentalsChckBx.y - controls.inHangarChkBx.y)
                self.assertFalse(actions.visible)
            self.assertEqual(controls.obtainingTypeBuyBtn.y, controls.vehTypeHeader.y)
            # Offers has its own request/filter-option overrides. Repair those
            # saved filters too, without importing a module category's VO.
            saved_filters['offline_bond_vehicle'].pop('selectedLevels')
            shop._StoreComponent__updateFilterOptions('vehicle')
            self.assertEqual([False] * 10,
                shop.filter_options['voData']['selectedLevels'])
            shop.requestTableData(1, False, 'module', {'fitsType': 'myVehicle'})
            self.assertEqual('vehicle', shop.table_request[2])
            self.assertEqual([False] * 10, shop.table_request[0]['selectedLevels'])
            self.assertNotIn('fitsType', shop.table_request[0])
            tab = shop._getTabClass('vehicle')()
            tab._nation = None
            tab._filterData = {'selectedTypes': [False, False],
                               'selectedLevels': [False] * 10}
            items = {cd: types.SimpleNamespace(intCD=cd, nationID=0,
                     level=8, isRented=False, type='heavyTank', icon='garage-art-%d' % cd)
                     for cd in (2, 3)}
            tab._items = types.SimpleNamespace(getItemByCD=items.get,
                stats=types.SimpleNamespace(money=Money(gold=0, crystal=8000)))
            rows = tab.buildItems([])
            self.assertEqual([3], [row[0].intCD for row in rows])
            self.assertEqual('garage-art-3', tab.itemWrapper(rows[0])['type'])
            self.assertEqual('crystal', tab.itemWrapper(rows[0])['currency'])
            self.assertEqual((0, 0, 8000), tab.itemWrapper(rows[0])['price'])
            self.assertFalse(tab.itemWrapper(rows[0])['disabled'])
            tab._items.stats.money = Money(gold=1000000, crystal=7999)
            self.assertTrue(tab.itemWrapper(rows[0])['disabled'])
            tab._items.stats.money = Money(gold=0, crystal=8000)
            tab._filterData['extra'] = ['inHangar']
            owned_rows = tab.buildItems([])
            self.assertEqual([2], [row[0].intCD for row in owned_rows])
            self.assertTrue(tab.itemWrapper(owned_rows[0])['disabled'])
            tab._filterData['extra'] = ['rentals']
            self.assertEqual([], tab.buildItems([]))
            with mock.patch.object(self.ui, 'request') as request:
                self.assertFalse(shop.buyItem('2'))
                request.assert_not_called()

            regular = Shop()
            regular_filters = {'selectedTypes': [True, False],
                'selectedLevels': [True] + [False] * 9, 'extra': ['inHangar'],
                'obtainingType': 'vehicle'}
            regular.requestTableData(1, False, 'vehicle', regular_filters)
            self.assertEqual([], pending_callbacks)
            for unused in range(2):
                regular.requestTableData(1, True, 'vehicle', regular_filters)
            self.assertEqual(1, len(pending_callbacks))
            self.assertEqual([], regular.navigation)
            # The native Flash caller accesses storeTable after Python's
            # requestTableData returns. Navigation cannot dispose it yet.
            self.assertTrue(regular.native_table_alive)
            self.assertEqual((1, 'vehicle', False), saved_filters['shop_current'])
            self.assertEqual(regular_filters, saved_filters['shop_vehicle'])
            pending_callbacks.pop(0)()
            self.assertFalse(regular.native_table_alive)
            event, scope, filters_at_navigation = regular.navigation[0]
            self.assertEqual(('store', {'tabId': 'storeActions'}, 'lobby'),
                             (event.alias, event.ctx, scope))
            self.assertEqual((-1, 'vehicle', False),
                             filters_at_navigation['offline_bond_current'])
            reset_filter = filters_at_navigation['offline_bond_vehicle']
            self.assertEqual([False, False], reset_filter['selectedTypes'])
            self.assertEqual([False] * 10, reset_filter['selectedLevels'])
            self.assertEqual([], reset_filter['extra'])
            self.assertEqual('vehicle', reset_filter['obtainingType'])
            self.assertEqual(['locked'], default_vehicle_filter['extra'])
            self.assertEqual(regular_filters, saved_filters['shop_vehicle'])

            # A closed page or a changed account must not navigate later or
            # overwrite that account's filters from a queued click.
            for stale_reason in ('disposed', 'changed_account'):
                regular = Shop()
                regular.requestTableData(1, True, 'vehicle', regular_filters)
                before_callback = copy.deepcopy(saved_filters)
                if stale_reason == 'disposed':
                    regular.disposed = True
                else:
                    account[0] = object()
                pending_callbacks.pop(0)()
                self.assertEqual([], regular.navigation)
                self.assertEqual(before_callback, saved_filters)
            regular = Shop()
            regular.requestTableData(1, True, 'vehicle', regular_filters)
            self.ui.uninstall()
            before_callback = copy.deepcopy(saved_filters)
            pending_callbacks.pop(0)()
            self.assertEqual([], regular.navigation)
            self.assertEqual(before_callback, saved_filters)
            # Restoring the original unbound method must preserve instance
            # binding as it does on the embedded Python 2 runtime.
            self.assertEqual((1, True, 'vehicle', regular_filters),
                             regular.requestTableData(1, True, 'vehicle', regular_filters))
            self.assertEqual([], pending_callbacks)
            self.assertIs(original, current['storeActions'])
            self.assertFalse(defaults['filters'])

    def test_regular_shop_filters_and_recovery_confirmation_share_account_state(self):
        class Vehicle(object):
            restorePrice = property(lambda self: 'catalogue-price')

        class Tab(object):
            def _getExtraCriteria(self, *args):
                raise AssertionError('old inclusive checkboxes')

        class Criteria(object):
            def __or__(self, predicate):
                return predicate

        exports = {
            'gui.Scaleform.daapi.view.lobby.store.tabs.shop': {'ShopVehicleTab': Tab},
            'gui.shared.utils.requesters': {'REQ_CRITERIA': types.SimpleNamespace(CUSTOM=lambda f: f)},
            'gui.shared.gui_items.Vehicle': {'Vehicle': Vehicle},
            'gui.shared.money': {'Money': lambda **kw: kw},
        }
        with native_modules(exports):
            self.ui._install_vehicle_filters_and_recovery()
            item = Vehicle()
            item.intCD, item.isPurchased, item.isRented, item.isUnlocked = 4, True, False, True
            matches = Tab()._getExtraCriteria(['inHangar'], Criteria(), [])
            self.assertTrue(matches(item))
            item.intCD = 2
            self.assertFalse(matches(item))  # A bond offer belongs in Special Offers.
            item.intCD = 4
            self.data['vehicleRecovery'] = {
                4: {'soldAt': 100, 'credits': 2750000, 'limited': False}}
            self.assertEqual({'credits': 2750000}, item.restorePrice)
            self.data['vehicleRecovery'].clear()
            self.assertEqual('catalogue-price', item.restorePrice)
            self.ui.uninstall()
            self.assertEqual('catalogue-price', item.restorePrice)

    def test_purchase_confirmation_rechecks_ownership_before_sending(self):
        dialogs = []
        exports = {
            'gui': {'DialogsInterface': types.SimpleNamespace(
                showDialog=lambda meta, callback: dialogs.append(callback))},
            'gui.Scaleform.daapi.view.dialogs': {'SimpleDialogMeta': lambda **kw: kw},
        }
        with native_modules(exports), mock.patch.object(self.ui, 'request') as request:
            view = types.SimpleNamespace()
            self.assertTrue(self.ui._buy_vehicle(view, 3))
            self.data['vehicles'].append({'vehicleTypeCompactDescr': 3})
            dialogs[0](True)
            request.assert_not_called()
            self.assertFalse(view._offlineBuying)

    def test_boosters_keep_native_lifecycle_filters_and_route_paid_actions(self):
        confirmations = []
        class Boosters(object):
            def __init__(self, ctx):
                self.tab = 2
                self.ctx = ctx.get('tabID')
                self._BoostersWindow__tabsContainer = types.SimpleNamespace(
                    currentTab=types.SimpleNamespace(getID=lambda: self.tab))

            def _populate(self):
                self.native_populated = True

            def _dispose(self):
                self.native_disposed = True

            def onBoosterActionBtnClick(self, *args):
                raise AssertionError('online purchase')

            def _isDAAPIInited(self):
                return True

            def _BoostersWindow__update(self):
                self.updated = True

        class Quests(object):
            def _processBoostersData(self):
                raise AssertionError('online quests')

        class Controller(object):
            def _BoostersController__notifyBoosterTime(self):
                pass

        exports = {
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersWindow': {
                'BoostersWindow': Boosters, 'MAX_ACTIVE_BOOSTERS_COUNT': 1},
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersPanelComponent': {
                'MAX_ACTIVE_BOOSTERS_COUNT': 1, '_GUI_SLOTS_PROPS': {'slotsCount': 1}},
            'gui.goodies.goodie_items': {'MAX_ACTIVE_BOOSTERS_COUNT': 1},
            'gui.Scaleform.daapi.view.lobby.boosters.booster_tabs': {
                'QuestsBoostersTab': Quests, 'TABS_IDS': types.SimpleNamespace(QUESTS=1, SHOP=2)},
            'gui.game_control.BoostersController': {'BoostersController': Controller},
            'gui': {'DialogsInterface': types.SimpleNamespace(showDialog=
                lambda meta, callback: confirmations.append((meta, callback)))},
            'gui.Scaleform.daapi.view.dialogs': {
                'I18nConfirmDialogMeta': lambda key, **kwargs: kwargs,
                'DIALOG_BUTTON_ID': types.SimpleNamespace(CLOSE='close')},
            'gui.Scaleform.genConsts.BOOSTER_CONSTANTS': {'BOOSTER_CONSTANTS':
                types.SimpleNamespace(BOOSTER_ACTIVATION_CONFORMATION_TEXT_KEY='replace')},
            'gui.shared.formatters': {'text_styles': types.SimpleNamespace(middleTitle=str)},
        }
        native_populate, native_dispose = Boosters._populate, Boosters._dispose
        with native_modules(exports), mock.patch.object(self.ui, 'request') as request:
            self.ui._install_reserves()
            self.assertIs(native_populate, Boosters._populate)
            self.assertIs(native_dispose, Boosters._dispose)
            window = Boosters(None)
            window._populate()
            uid = self.ui.policy.RESERVE_IDS['xp']
            window.onBoosterActionBtnClick(uid, None)
            window.onBoosterActionBtnClick(uid, None)
            self.assertEqual(1, request.call_count)
            self.assertEqual(('buy_reserve', 'xp'), request.call_args.args[:2])
            request.call_args.args[2](True, '')
            window.tab = 0
            window.onBoosterActionBtnClick(uid, None)
            self.assertEqual(('activate_reserve', 'xp'), request.call_args.args[:2])
            request.call_args.args[2](True, '')
            request.reset_mock()
            self.data['personalReserves'] = {'active': {'xp_5_6h': [100, 21700]}}
            window._BoostersWindow__tabsContainer.currentTab.goodiesCache = types.SimpleNamespace(
                getBooster=lambda uid: types.SimpleNamespace(description='reserve-%d' % uid))
            with mock.patch.object(self.ui.policy, 'now_seconds', return_value=200):
                window.onBoosterActionBtnClick(uid, None)
                window.onBoosterActionBtnClick(uid, None)
                self.assertEqual(1, len(confirmations))
                self.assertEqual('close', confirmations[0][0]['focusedID'])
                request.assert_not_called()
                confirmations[0][1](False)
                request.assert_not_called()
                self.assertFalse(window._offlineBuying)
                window.onBoosterActionBtnClick(uid, None)
                confirmations[1][1](True)
                request.assert_called_once()
                self.assertEqual(('activate_reserve', 'xp'), request.call_args.args[:2])
            window._dispose()
            self.assertTrue(window.native_disposed)
            self.ui.uninstall()

    def test_account_popover_preserves_hide_event_and_badge_selection_is_deferred(self):
        events = []

        class Base(object):
            def __init__(self, ctx=None):
                self.live = False

            def _populate(self):
                self.live = True  # AbstractPopOverView's HIDE_POPOVER listener.

            def _dispose(self):
                self.live = False
                events.append('popover_destroyed')

            def destroy(self):
                self._dispose()

            def fireEvent(self, event, scope):
                events.append(event)

        class Popover(Base):
            def __init__(self, ctx):
                raise AssertionError('online clan/tutorial constructor')

            def _populate(self):
                raise AssertionError('online clan setup')

            def _dispose(self):
                raise AssertionError('unregistered clan callbacks')

            def _AccountPopover__syncUserInfo(self):
                pass

            def openBoostersWindow(self, idx):
                pass

            def openBadgesWindow(self):
                pass

            def as_setDataS(self, data):
                self.data = data

            def as_setClanDataS(self, data):
                self.clan = data

        class Badges(object):
            def _BadgesPage__updateBadges(self):
                pass

            def onSelectBadge(self, badge):
                pass

            def onDeselectBadge(self):
                pass

            def _isDAAPIInited(self):
                return True

            def as_setReceivedBadgesS(self, data):
                self.rows = data

            def as_setNotReceivedBadgesS(self, data):
                self.locked = data

            def as_setSelectedBadgeImgS(self, image):
                self.selected = image

        exports = {
            'BigWorld': {'player': lambda: types.SimpleNamespace(name='offline')},
            'gui.Scaleform.daapi.view.lobby.header.AccountPopover': {'AccountPopover': Popover},
            'gui.Scaleform.daapi.view.lobby.BadgesPage': {
                'BadgesPage': Badges, '_makeBadgeVO': lambda badge: {'id': badge.badgeID}},
            'gui.Scaleform.settings': {'getBadgeIconPath': lambda size, uid: '%d/%d' % (size, uid),
                                      'BADGES_ICONS': types.SimpleNamespace(X48=48)},
            'gui.Scaleform.locale.RES_ICONS': {'RES_ICONS': types.SimpleNamespace(
                MAPS_ICONS_LIBRARY_BADGES_48X48_BADGE_DEFAULT='default')},
            'gui.Scaleform.locale.BADGE': {'BADGE': types.SimpleNamespace(
                BADGESPAGE_BODY_UNCOLLECTED_TITLE='Not earned')},
            'gui.shared.formatters': {'text_styles': types.SimpleNamespace(highTitle=lambda x: x)},
            'gui.Scaleform.daapi.settings.views': {'VIEW_ALIAS': types.SimpleNamespace(
                BOOSTERS_WINDOW='reserves', BADGES_PAGE='badges')},
            'gui.shared': {'events': types.SimpleNamespace(LoadViewEvent=lambda alias, ctx: alias),
                           'EVENT_BUS_SCOPE': types.SimpleNamespace(LOBBY='lobby')},
        }
        with native_modules(exports), mock.patch.object(self.ui, 'request') as request:
            self.ui._install_account()
            self.data['selectedBadges'] = [17]
            self.data['badgeSelectionVerified'] = True
            popover = Popover(None)
            popover._populate()
            self.assertTrue(popover.live)
            self.assertEqual('48/17', popover.data['badgeIcon'])
            popover.openBadgesWindow()
            self.assertFalse(popover.live)
            self.assertEqual(['popover_destroyed', 'badges'], events)
            page = Badges()
            page.itemsCache = types.SimpleNamespace(items=types.SimpleNamespace(
                getBadges=lambda: {
                    17: types.SimpleNamespace(badgeID=17, isAchieved=True, getWeight=lambda: 1),
                    18: types.SimpleNamespace(badgeID=18, isAchieved=False, getWeight=lambda: 2)}))
            page.selected = 'unchanged'
            page.onDeselectBadge()
            self.assertEqual('unchanged', page.selected)
            self.data['selectedBadges'] = []
            request.call_args.args[2](True, '')
            self.assertEqual('', page.selected)
            self.assertTrue(page.rows['badgesData'][0]['enabled'])
            self.assertFalse(page.locked['badgesData'][0]['enabled'])
            self.ui.uninstall()

    def test_daily_page_does_not_populate_retail_empty_tabs_or_arrows(self):
        calls = []

        class Base(object):
            def _populate(self):
                calls.append('base_populate')

            def _dispose(self):
                calls.append('base_dispose')

        class Page(Base):
            def _populate(self):
                raise AssertionError('would create empty retail tabs')

            def _dispose(self):
                raise AssertionError('would remove unsubscribed builders')

            def _invalidate(self, ctx=None):
                raise AssertionError('would restore empty retail tabs')

            def onClose(self):
                calls.append('hangar')

            def as_setTabsDataProviderS(self, tabs):
                self.tabs = tabs

            def as_showFilterS(self, visible):
                self.filter = visible

        panel = mock.Mock()
        panel.open.return_value = True
        exports = {'gui.Scaleform.daapi.view.lobby.missions.regular.missions_page': {'MissionsPage': Page}}
        with native_modules(exports), mock.patch.object(self.ui, 'ServicePanel', return_value=panel):
            self.ui._install_daily()
            page = Page()
            page._populate()
            self.assertEqual([], page.tabs)
            self.assertFalse(page.filter)
            page._invalidate()
            panel.refresh.assert_called_once()
            page._dispose()
            panel.uninstall.assert_called_once()
            self.assertFalse(self.ui._panels)
            self.ui.uninstall()
        self.assertEqual(['base_populate', 'base_dispose'], calls)

    def test_purchase_notification_and_callback_follow_account_acknowledgement(self):
        callbacks, notices, finished = [], [], []
        account = types.SimpleNamespace(_doCmdStr=lambda cmd, payload, callback: callbacks.append(callback))
        exports = {
            'BigWorld': {'player': lambda: account},
            'gui': {'SystemMessages': types.SimpleNamespace(
                pushMessage=lambda text, **kwargs: notices.append((text, kwargs['type'])),
                SM_TYPE=types.SimpleNamespace(Error='error', PurchaseForGold='gold',
                    Information='info', PurchaseForCrystal='crystal'))},
            'gui.mods.offline_lan_0922.account_rpc.commands': {'CMD_OFFLINE_SERVICE': 19900},
        }
        with native_modules(exports), mock.patch.object(self.ui, 'tr', side_effect=lambda x: x):
            self.ui.request('buy_reserve', 'xp', lambda *result: finished.append(result))
            self.assertFalse(notices)
            callbacks[0](1, 0, '')
            self.assertIn('Spent 50 gold', notices[0][0])
            self.assertEqual('gold', notices[0][1])
            self.assertEqual([(True, '')], finished)
            self.ui.request('buy_reserve', 'xp', lambda *result: finished.append(result))
            callbacks[1](2, -1, 'The transaction could not be saved.')
            self.assertEqual('error', notices[-1][1])
            self.assertFalse(finished[-1][0])


class MissionResultUITests(unittest.TestCase):
    def setUp(self):
        self.ui = fixture._load_port_module('offline_services_ui')
        self.addCleanup(self.ui.uninstall)

    def test_midnight_refresh_waits_for_lobby_then_replaces_account_stats(self):
        callbacks = []
        server = types.SimpleNamespace(publish_postbattle_progress=mock.Mock())
        lobby = [False]
        exports = {
            'BigWorld': {
                'callback': lambda delay, callback: callbacks.append((delay, callback)) or len(callbacks),
                'player': lambda: types.SimpleNamespace(fakeServer=server),
                'cancelCallback': lambda unused: None},
            'helpers': {'isPlayerAccount': lambda: lobby[0]},
        }
        with native_modules(exports), mock.patch.object(self.ui.time, 'time', return_value=172799):
            self.ui._schedule_daily_rollover()
            self.assertAlmostEqual(1.1, callbacks[0][0])
            callbacks.pop(0)[1]()
            server.publish_postbattle_progress.assert_not_called()
            self.assertEqual(5.0, callbacks[0][0])
            lobby[0] = True
            callbacks.pop(0)[1]()
            server.publish_postbattle_progress.assert_called_once_with()
            self.assertEqual(1, len(callbacks))
            self.ui.uninstall()

    def test_completed_daily_rows_use_native_results_and_open_daily_page(self):
        stock, opened = [], []
        class Block(object):
            def setRecord(self, result, reusable):
                stock.append(result)
            def getNextComponentIndex(self):
                return len(self.rows)
            def addComponent(self, index, value):
                self.rows.append(value)
        class Window(object):
            def showEventsWindow(self, key, kind):
                opened.append(key)
            def destroy(self):
                opened.append('closed')
        exports = {
            'constants': {'EVENT_TYPE': types.SimpleNamespace(BATTLE_QUEST=2)},
            'gui.battle_results.components.progress': {'QuestsProgressBlock': Block},
            'gui.battle_results.components.base': {'DirectStatsItem': lambda unused, info: info},
            'gui.server_events.formatters': {
                'PROGRESS_BAR_TYPE': types.SimpleNamespace(SIMPLE=1, NONE=0),
                'packSimpleBonusesBlock': lambda values: {'rewards': values},
                'todict': lambda values: values},
            'gui.server_events.bonuses': {'GoodiesBonus': lambda name, value:
                types.SimpleNamespace(formattedList=lambda: [(name, value)])},
            'gui.Scaleform.genConsts.MISSIONS_STATES': {'MISSIONS_STATES': types.SimpleNamespace(COMPLETED='done')},
            'gui.Scaleform.genConsts.QUESTS_ALIASES': {'QUESTS_ALIASES': types.SimpleNamespace(RENDERER_TYPE_QUEST='quest')},
            'gui.Scaleform.daapi.view.battle_results_window': {'BattleResultsWindow': Window},
            'gui.server_events.events_dispatcher': {'showMissions': lambda: opened.append('daily')},
        }
        with native_modules(exports):
            self.ui._install_mission_results()
            block = Block()
            block.rows = []
            block.setRecord('receipt', types.SimpleNamespace(personal=types.SimpleNamespace(
                getQuestsProgress=lambda: {'offline_daily_wins': (), 'retail': ()})))
            self.assertEqual(['receipt'], stock)
            self.assertEqual(1, len(block.rows))
            self.assertEqual('done', block.rows[0]['questInfo']['status'])
            self.assertEqual('', block.rows[0]['alertMsg'])
            self.assertEqual([], block.rows[0]['progressList'])
            self.assertEqual(0, block.rows[0]['questInfo']['progrBarType'])
            self.assertEqual([{'rewards': [('goodies', {92002: {'count': 1}})]}],
                             block.rows[0]['awards'])
            Window().showEventsWindow('offline_daily_wins', 2)
            self.assertEqual(['daily', 'closed'], opened)

    def test_one_native_popup_combines_simultaneous_rewards(self):
        messages, dialogs = [], []
        exports = {
            'gui.SystemMessages': {'pushMessage': lambda message, **kw: messages.append(message),
                'SM_TYPE': types.SimpleNamespace(Information=1)},
            'gui.DialogsInterface': {'showDialog': lambda meta, callback: dialogs.append(meta)},
            'gui.Scaleform.daapi.view.dialogs': {
                'SimpleDialogMeta': lambda *args: args, 'InfoDialogButtons': lambda label: label},
        }
        with native_modules(exports):
            self.ui.notify_missions(['damage', 'wins'])
        self.assertEqual(1, len(messages))
        self.assertEqual(1, len(dialogs))
        self.assertIn('\n', messages[0])



class ExchangeDialogLimitTests(unittest.TestCase):
    """Verify the #1513 VO boundary, not a live Scaleform render."""

    def setUp(self):
        self.ui = fixture._load_port_module('offline_services_ui')
        self.addCleanup(self.ui.uninstall)
        class Meta(object):
            def __init__(self, gold=9833700, rate=400, needed=6090000):
                self.actual_gold = gold
                self.vo = {'title': 'stock title', 'exchangeBlockData': {
                    'exchangeRate': rate, 'defaultExchangeRate': rate,
                    'maxGoldValue': gold, 'goldStepSize': 1,
                    'defaultGoldValue': (needed + rate - 1) // rate
                        if rate > 0 else 0}}
            def makeVO(self):
                return self.vo
        self.meta_type = Meta
        self.original = Meta.makeVO

    def test_reported_purchase_keeps_amount_but_bounds_large_wallet_product(self):
        self.ui._install_exchange_dialog_limits(self.meta_type)
        meta = self.meta_type()
        before = copy.deepcopy(meta.vo)
        block = meta.makeVO()['exchangeBlockData']
        self.assertEqual(15225, block['defaultGoldValue'])
        self.assertEqual(6090000, block['defaultGoldValue'] * block['exchangeRate'])
        self.assertEqual(5368709, block['maxGoldValue'])
        self.assertLessEqual(block['maxGoldValue'] * block['exchangeRate'], 2147483647)
        self.assertEqual(9833700, meta.actual_gold)
        self.assertEqual(before, meta.vo)
        self.ui.uninstall()
        self.assertIs(self.original, self.meta_type.makeVO)

    def test_normal_and_xp_dialog_values_are_identical(self):
        self.ui._install_exchange_dialog_limits(self.meta_type)
        for gold, rate, needed in ((1000, 400, 4000), (0, 400, 6090000),
                                   (9833700, 25, 65600), (5368709, 400, 6090000)):
            with self.subTest(gold=gold, rate=rate):
                meta = self.meta_type(gold, rate, needed)
                self.assertIs(meta.vo, meta.makeVO())
        self.assertEqual(2624, self.meta_type(9833700, 25, 65600).
            makeVO()['exchangeBlockData']['defaultGoldValue'])

    def test_changed_rate_and_very_large_balances_use_the_live_rate(self):
        self.ui._install_exchange_dialog_limits(self.meta_type)
        for rate in (1, 25, 400, 800, 1000):
            with self.subTest(rate=rate):
                meta = self.meta_type(10 ** 12, rate, 100000)
                block = meta.makeVO()['exchangeBlockData']
                self.assertLessEqual(block['maxGoldValue'] * rate, 2147483647)
                self.assertGreater((block['maxGoldValue'] + 1) * rate, 2147483647)
                self.assertEqual(meta.vo['exchangeBlockData']['defaultGoldValue'],
                                 block['defaultGoldValue'])

    def test_restore_subclass_inherits_the_same_limit(self):
        class RestoreMeta(self.meta_type):
            pass
        self.ui._install_exchange_dialog_limits(self.meta_type)
        self.assertEqual(5368709, RestoreMeta().makeVO()['exchangeBlockData']['maxGoldValue'])
        self.assertNotIn('makeVO', RestoreMeta.__dict__)

    def test_nonpositive_rate_preserves_the_native_disabled_state(self):
        self.ui._install_exchange_dialog_limits(self.meta_type)
        meta = self.meta_type(rate=0)
        self.assertIs(meta.vo, meta.makeVO())

    def test_signed_32_bit_limit_model_no_longer_clamps_needed_credits_to_zero(self):
        # A boundary model for the observed zero field, NOT a native SWF test.
        def render(block):
            maximum = int(block['maxGoldValue'] * block['exchangeRate'])
            maximum = (maximum + (1 << 31)) % (1 << 32) - (1 << 31)
            wanted = block['defaultGoldValue'] * block['exchangeRate']
            return max(0, min(maximum, wanted))
        meta = self.meta_type()
        self.assertEqual(0, render(meta.makeVO()['exchangeBlockData']))
        self.ui._install_exchange_dialog_limits(self.meta_type)
        self.assertEqual(6090000, render(meta.makeVO()['exchangeBlockData']))
        block = meta.makeVO()['exchangeBlockData']
        for wanted in (400, 1000000, 6090000, 8000000):
            block['defaultGoldValue'] = wanted // 400
            self.assertEqual(wanted, render(block))

    def test_regular_services_install_and_rollback_own_the_native_base_hook(self):
        original = self.meta_type.makeVO
        others = ('_install_directive_fitting', '_install_item_comparisons', '_install_store_filters',
            '_install_shop', '_install_vehicle_filters_and_recovery',
            '_install_reserves', '_install_account', '_install_daily',
            '_install_mission_results', '_install_settings', '_schedule_daily_rollover')
        with native_modules({
                'gui.Scaleform.daapi.view.dialogs.ExchangeDialogMeta': {
                    '_ExchangeDialogMeta': self.meta_type},
                'gui.mods.offline_lan_0922.crew_voice': {'install': lambda patch: None}
                }), mock.patch.multiple(self.ui, **{name: mock.Mock() for name in others}):
            self.ui.install()
            wrapper = self.meta_type.makeVO
            self.assertIsNot(original, wrapper)
            self.ui.install()
            self.assertIs(wrapper, self.meta_type.makeVO)
            self.ui.uninstall()
            self.assertIs(original, self.meta_type.makeVO)
            with mock.patch.object(self.ui, '_schedule_daily_rollover',
                                   side_effect=RuntimeError('startup failed')):
                with self.assertRaises(RuntimeError):
                    self.ui.install()
            self.assertIs(original, self.meta_type.makeVO)
            self.assertEqual([], self.ui._patches)


if __name__ == '__main__':
    unittest.main()
