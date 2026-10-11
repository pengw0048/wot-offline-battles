"""Native store, reserve and account adapters for offline services.

Transactions use the Account mailbox. Stock Scaleform components own shop
rows, reserve filters/slots, popover closing, badge cards and system messages.
"""

import copy
import json
import time

from gui.mods.offline_lan_0922 import offline_services as policy
from gui.mods.offline_lan_0922.ui_i18n import tr, as_text
from gui.mods.offline_lan_0922.waiting_room_ui import (
    WaitingRoomUI, _ControlScript, CONTROL_Z, CONTROL_TEXT_COLOUR,
    OUTLINE_TEXTURE, NativeSurface)

_patches = []
_panels = []
_factory_settings = []
_filter_defaults = []
_daily_timer = None


def _schedule_daily_rollover(delay=None):
    import BigWorld
    global _daily_timer
    if delay is None:
        delay = 86400 - time.time() % 86400 + 0.1

    def refresh():
        from helpers import isPlayerAccount
        global _daily_timer
        _daily_timer = None
        account = BigWorld.player()
        server = getattr(account, 'fakeServer', None)
        if not isPlayerAccount() or server is None:
            _schedule_daily_rollover(5.0)
            return
        try:
            server.publish_postbattle_progress()
        finally:
            _schedule_daily_rollover()
    _daily_timer = BigWorld.callback(delay, refresh)


def snapshot():
    from gui.mods.offline_lan_0922.compat import g_compatibility
    state = g_compatibility.garage_state()
    return state.snapshot() if state is not None else {}


def mission_reward_text(key):
    row = policy.MISSION_BY_ID[key]
    return tr('Mission completed: %s. Reward: %s reserve x1.') % (
        mission_label(row), tr(policy.RESERVE_BY_ID[row[4]][1]))


def notify_missions(keys):
    """Publish one combined native dialog for this durably completed battle."""
    from gui import SystemMessages, DialogsInterface
    from gui.Scaleform.daapi.view.dialogs import SimpleDialogMeta, InfoDialogButtons
    lines = [mission_reward_text(key) for key in keys if key in policy.MISSION_BY_ID]
    if not lines:
        return
    text = u'\n'.join(lines)
    SystemMessages.pushMessage(text, type=SystemMessages.SM_TYPE.Information)
    DialogsInterface.showDialog(SimpleDialogMeta(
        tr('Mission completed'), text, InfoDialogButtons(tr('CLOSE'))), lambda unused: None)


def _install_mission_results():
    from constants import EVENT_TYPE
    from gui.battle_results.components import progress, base
    from gui.server_events.formatters import PROGRESS_BAR_TYPE
    from gui.Scaleform.genConsts.MISSIONS_STATES import MISSIONS_STATES
    from gui.Scaleform.genConsts.QUESTS_ALIASES import QUESTS_ALIASES
    from gui.Scaleform.daapi.view.battle_results_window import BattleResultsWindow
    from gui.server_events import bonuses, formatters
    original = progress.QuestsProgressBlock.setRecord

    def set_record(block, result, reusable):
        original(block, result, reusable)
        for key in sorted(reusable.personal.getQuestsProgress()):
            if not str(key).startswith('offline_daily_'):
                continue
            mission_id = key[len('offline_daily_'):]
            if mission_id not in policy.MISSION_BY_ID:
                continue
            row = policy.MISSION_BY_ID[mission_id]
            reward = bonuses.GoodiesBonus('goodies', {
                policy.RESERVE_IDS[row[4]]: {'count': 1}})
            awards = [formatters.packSimpleBonusesBlock(reward.formattedList())]
            # Stock completed quests show their reward and COMPLETED tick.
            # alertMsg is reserved for a reset/warning, not success text.
            info = {'title': mission_label(row), 'awards': formatters.todict(awards),
                'alertMsg': '',
                'questInfo': {'questID': key, 'eventType': EVENT_TYPE.BATTLE_QUEST,
                    'IGR': False, 'taskType': '', 'tasksCount': 1,
                    'progrBarType': PROGRESS_BAR_TYPE.NONE, 'progrTooltip': None,
                    'maxProgrVal': 0, 'currentProgrVal': 0,
                    'rendererType': QUESTS_ALIASES.RENDERER_TYPE_QUEST,
                    'timerDescription': '', 'status': MISSIONS_STATES.COMPLETED,
                    'description': mission_label(row), 'tooltip': '',
                    'isSelectable': True, 'isNew': False, 'isAvailable': True},
                'personalInfo': [],
                'questType': EVENT_TYPE.BATTLE_QUEST,
                'progressList': []}
            block.addComponent(block.getNextComponentIndex(), base.DirectStatsItem('', info))
    _patch(progress.QuestsProgressBlock, 'setRecord', set_record)
    original_show = BattleResultsWindow.showEventsWindow

    def show_events(view, event_id, event_type):
        if str(event_id).startswith('offline_daily_'):
            from gui.server_events.events_dispatcher import showMissions
            showMissions()
            view.destroy()
        else:
            original_show(view, event_id, event_type)
    _patch(BattleResultsWindow, 'showEventsWindow', show_events)


def _notify(action, key, success, error):
    from gui import SystemMessages
    if not success:
        SystemMessages.pushMessage(error, type=SystemMessages.SM_TYPE.Error)
        return
    if action == 'expire_reserves':
        return
    if action in ('buy_reserve', 'activate_reserve'):
        row = policy.RESERVE_BY_ID[key]
        hours = row.duration // 3600
        text = (tr('Purchased personal reserve: %s +%d%%, %d h. Spent %d gold.') %
                (tr(row.label), row.percent, hours, row.price) if action == 'buy_reserve' else
                tr('Activated personal reserve: %s +%d%%, %d h.') %
                (tr(row.label), row.percent, hours))
        kind = (SystemMessages.SM_TYPE.PurchaseForGold if action == 'buy_reserve'
                else SystemMessages.SM_TYPE.Information)
    elif action == 'vehicle':
        offer = next(row for row in snapshot()['offlineVehicleOffers']
                     if row['name'] == key)
        text = tr('Purchased %s for %d bonds. Includes a slot and 100%% crew.') % (
            as_text(offer['label']), offer['price'])
        kind = SystemMessages.SM_TYPE.PurchaseForCrystal
    else:
        text, kind = tr('Badge selection saved.'), SystemMessages.SM_TYPE.Information
    SystemMessages.pushMessage(text, type=kind)


def request(action, key, callback):
    import BigWorld
    from gui.mods.offline_lan_0922.account_rpc.commands import CMD_OFFLINE_SERVICE
    account = BigWorld.player()

    def complete(unused_request, result, error, *unused):
        if BigWorld.player() is account:
            message = tr(error) if error else ''
            if error and error.startswith('the account has '):
                message = tr('Insufficient currency for this purchase.')
            success = result >= 0
            try:
                _notify(action, key, success, message)
            finally:
                callback(success, message)

    account._doCmdStr(CMD_OFFLINE_SERVICE,
                     json.dumps({'action': action, 'key': key}), complete)


def _patch(owner, name, replacement):
    _patches.append((owner, name, getattr(owner, name)))
    setattr(owner, name, replacement)


def _replace_component(alias, cls):
    from gui.Scaleform.framework import g_entitiesFactories
    settings = g_entitiesFactories.getSettings(alias)
    if settings is None:
        raise RuntimeError('the native component is not registered: %s' % alias)
    if settings.clazz is cls:
        return
    replacement = settings.replaceSettings({'clazz': cls})
    g_entitiesFactories.removeSettings(alias)
    try:
        g_entitiesFactories.addSettings(replacement)
    except Exception:
        g_entitiesFactories.addSettings(settings)
        raise
    # Lobby packages register their stock settings again after a battle.
    # The saved rollback entry is not proof that the live factory still uses
    # our controller. Rebind every new store page, retaining one original.
    if not any(saved.alias == alias for saved in _factory_settings):
        _factory_settings.append(settings)


def _buy_vehicle(view, compact_descr):
    data = snapshot()
    compact_descr = int(compact_descr)
    if compact_descr in policy.owned_vehicle_types(data):
        return False
    offer = next((row for row in data.get('offlineVehicleOffers', ())
                  if row['cd'] == compact_descr), None)
    if offer is None or getattr(view, '_offlineBuying', False):
        return False
    from gui import DialogsInterface
    from gui.Scaleform.daapi.view.dialogs import SimpleDialogMeta
    view._offlineBuying = True

    class Buttons(object):
        def getLabels(self):
            return [{'id': 'submit', 'label': tr('BUY'), 'focused': False},
                    {'id': 'close', 'label': tr('CANCEL'), 'focused': True}]

    def confirm(accepted):
        if not accepted:
            view._offlineBuying = False
            return
        # Ownership may have changed while this confirmation was open.
        if compact_descr in policy.owned_vehicle_types(snapshot()):
            view._offlineBuying = False
            return

        def done(success, error):
            view._offlineBuying = False
            if view._isDAAPIInited():
                view._onTableUpdate()

        try:
            request('vehicle', offer['name'], done)
        except Exception:
            view._offlineBuying = False
            raise

    DialogsInterface.showDialog(SimpleDialogMeta(
        title=tr('BUY VEHICLE'),
        message=tr('Buy %s for %d bonds?') %
                (as_text(offer['label']), offer['price']), buttons=Buttons()), confirm)
    return True


def _store_filter_data(key, incoming=None):
    """Complete this category's payload before native readers persist/use it."""
    from account_helpers.AccountSettings import AccountSettings
    from gui.shared.utils import flashObject2Dict
    defaults = AccountSettings.getFilterDefault(key)
    result = copy.deepcopy(defaults)
    for values in (AccountSettings.getFilter(key), flashObject2Dict(incoming)):
        if isinstance(values, dict):
            for name, value in values.items():
                # Empty selections are intentional; absent/null fields are not.
                if value is not None or name not in defaults:
                    result[name] = copy.deepcopy(value)
    if 'obtainingType' in defaults:
        result['obtainingType'] = defaults['obtainingType']
    return result


def _install_store_filters():
    from account_helpers.AccountSettings import AccountSettings, DEFAULT_VALUES, KEY_FILTERS
    from gui.Scaleform.daapi.view.lobby.store.StoreComponent import StoreComponent
    from gui.Scaleform.daapi.view.lobby.store.Shop import Shop
    from gui.Scaleform.daapi.view.lobby.store.Inventory import Inventory
    from gui.Scaleform.Waiting import Waiting

    def wrap_request(original, prefix, waiting):
        def request_table(view, nation, actions, item_type, filters):
            filters = _store_filter_data(prefix + '_' + item_type, filters)
            try:
                return original(view, nation, actions, item_type, filters)
            except Exception:
                # Stock show/build/hide has no finally. Balance only its failed
                # request; a second hide on success could remove another wait.
                Waiting.hide(waiting)
                raise
        return request_table

    _patch(Shop, 'requestTableData', wrap_request(
        Shop.requestTableData, 'shop', 'updateShop'))
    _patch(Inventory, 'requestTableData', wrap_request(
        Inventory.requestTableData, 'inventory', 'updateInventory'))
    original_populate = StoreComponent._populate

    def populate(view):
        # A previous failed request may already have saved an incomplete VO.
        # Repair it before __populateFilters reads vehicleCD/selectedTypes.
        prefix = view.getName() + '_'
        for key, defaults in DEFAULT_VALUES[KEY_FILTERS].items():
            if key.startswith(prefix) and isinstance(defaults, dict):
                filters = _store_filter_data(key)
                if filters != AccountSettings.getFilter(key):
                    AccountSettings.setFilter(key, filters)
        return original_populate(view)

    _patch(StoreComponent, '_populate', populate)


def _install_shop():
    from account_helpers.AccountSettings import AccountSettings, DEFAULT_VALUES, KEY_FILTERS
    from gui.Scaleform.daapi.settings.views import VIEW_ALIAS
    from gui.Scaleform.daapi.view.lobby.store.StoreView import StoreView
    from gui.Scaleform.daapi.view.lobby.store.Shop import Shop
    from gui.Scaleform.daapi.view.lobby.store.tabs.shop import ShopVehicleTab
    from gui.Scaleform.genConsts.STORE_CONSTANTS import STORE_CONSTANTS
    from gui.shared.gui_items.Vehicle import VEHICLE_TYPES_ORDER
    from gui.Scaleform.Waiting import Waiting
    prefix = 'offline_bond'
    defaults = DEFAULT_VALUES[KEY_FILTERS]
    for key, value in ((prefix + '_current', (-1, STORE_CONSTANTS.VEHICLE, False)),
                       (prefix + '_' + STORE_CONSTANTS.VEHICLE,
                        copy.deepcopy(AccountSettings.getFilterDefault(
                            'shop_' + STORE_CONSTANTS.VEHICLE)))):
        _filter_defaults.append((defaults, key, key in defaults, defaults.get(key)))
        defaults[key] = value

    class BondVehicleTab(ShopVehicleTab):
        def _offerPrice(self, item):
            offer = next((row for row in snapshot().get('offlineVehicleOffers', ())
                          if row['cd'] == item.intCD), None)
            if offer is None or offer['price'] <= 0:
                raise ValueError('the bond vehicle row has no published quote')
            return offer['price']

        def _getItemPrices(self, item):
            from gui.shared.gui_items.gui_item_economics import ItemPrices, ItemPrice
            from gui.shared.money import Money
            price = Money(crystal=self._offerPrice(item))
            # Supply the native row producer's complete price object, without
            # mutating the shared Vehicle or its ordinary catalogue price.
            return ItemPrices(ItemPrice(price, price))

        def _isPurchaseEnabled(self, item, money):
            return (item.intCD not in policy.owned_vehicle_types(snapshot()) and
                    money.getSignValue('crystal') >= self._offerPrice(item))

        def _isItemOnDiscount(self, item):
            return False

        def buildItems(self, inv_vehicles):
            data = snapshot()
            owned = policy.owned_vehicle_types(data)
            rows = []
            types = self._filterData.get('selectedTypes', ())
            levels = self._filterData.get('selectedLevels', ())
            selected_types = set(name for idx, name in enumerate(VEHICLE_TYPES_ORDER)
                                 if idx < len(types) and types[idx])
            for offer in data.get('offlineVehicleOffers', ()):
                item = self._items.getItemByCD(offer['cd'])
                if self._nation is not None and item.nationID != self._nation:
                    continue
                if selected_types and item.type not in selected_types:
                    continue
                if any(levels) and (len(levels) < item.level or not levels[item.level - 1]):
                    continue
                if not policy.matches_vehicle_filters(
                        self._filterData.get('extra'), item.intCD in owned,
                        item.isRented, True, bond=True):
                    continue
                rows.append((item, None, 0))
            rows.sort(key=lambda row: (row[0].intCD in owned, row[0].level,
                                       row[0].nationID, row[0].intCD))
            return rows

        def itemWrapper(self, row):
            result = ShopVehicleTab.itemWrapper(self, row)
            item = row[0]
            # The native wrapper uses item.icon: the same full vehicle artwork
            # as Shop/Inventory, never the low-resolution battle contour.
            if item.intCD in policy.owned_vehicle_types(snapshot()):
                result.update(disabled=True, statusMessage=tr('OWNED'))
            return result

    class BondShop(Shop):
        def getName(self):
            # Flash VehicleView.onFitsArrayRequest compares this to 'shop'.
            # A custom name selects inventory controls (including 'broken')
            # and silently drops the owned-vehicle checkbox from its payload.
            return Shop.getName(self)

        def _StoreComponent__getCurrentFilter(self):
            nation, unused_type, unused_actions = AccountSettings.getFilter(
                prefix + '_current')
            from gui import GUI_NATIONS
            return (nation if nation < len(GUI_NATIONS) else -1,
                    STORE_CONSTANTS.VEHICLE, False)

        def _StoreComponent__updateFilterOptions(self, unused_type):
            from gui.Scaleform import getVehicleTypeAssetPath, getLevelsAssetPath
            from gui.prb_control.settings import VEHICLE_LEVELS
            from gui.shared.utils.functions import makeTooltip
            filters = _store_filter_data(prefix + '_' + STORE_CONSTANTS.VEHICLE)
            filters['obtainingType'] = STORE_CONSTANTS.VEHICLE
            filters['extra'] = [value for value in filters.get('extra', ())
                                if value in ('inHangar', 'rentals')]
            filters['vehicleTypes'] = [
                {'value': getVehicleTypeAssetPath(name),
                 'tooltip': makeTooltip('#menu:carousel_tank_filter/' + name,
                     '#tank_carousel_filter:tooltip/vehicleTypes/body'),
                 'selected': filters['selectedTypes'][index]}
                for index, name in enumerate(VEHICLE_TYPES_ORDER)]
            filters['levels'] = [
                {'value': getLevelsAssetPath('level_%d' % level),
                 'selected': filters['selectedLevels'][level - 1]}
                for level in VEHICLE_LEVELS]
            vo_class, show_extra = BondVehicleTab.getFilterInitData()
            self.as_setFilterOptionsS({'voClassName': vo_class,
                'showExtra': show_extra, 'voData': filters})
            self._update()

        def _onTableUpdate(self, *unused):
            nation, item_type, actions = self._StoreComponent__getCurrentFilter()
            self.requestTableData(nation, actions, item_type,
                AccountSettings.getFilter(prefix + '_' + item_type))

        def as_initFiltersDataS(self, nations, action_label):
            result = Shop.as_initFiltersDataS(self, nations, action_label)
            if self._isDAAPIInited():
                # The native ShopUI includes its discount selector even when
                # reused for Offers. All rows here are already bond offers.
                actions = self.flashObject.actionsFilterView
                actions.visible = False
                actions.mouseEnabled = False
                actions.mouseChildren = False
                _hide_bond_extra_categories(self.flashObject.form.menu)
            return result

        def as_setFilterOptionsS(self, data):
            result = Shop.as_setFilterOptionsS(self, data)
            if self._isDAAPIInited():
                menu = self.flashObject.form.menu
                _hide_bond_extra_categories(menu)
                _layout_bond_vehicle_filters(menu.view.currentView)
            return result

        def _getTabClass(self, unused_type):
            return BondVehicleTab

        def requestFilterData(self, unused_type):
            nation, unused, unused_actions = AccountSettings.getFilter(prefix + '_current')
            if unused_type != STORE_CONSTANTS.VEHICLE:
                self.as_setFilterTypeS({'language': nation,
                    'tabType': STORE_CONSTANTS.VEHICLE,
                    'fittingType': STORE_CONSTANTS.VEHICLE, 'actionsSelected': False})
            return Shop.requestFilterData(self, STORE_CONSTANTS.VEHICLE)

        def requestTableData(self, nation, unused_actions, unused_type, filters):
            # Other store accordion categories have no bond offers. Use the
            # saved native vehicle filters, not a module-filter VO as a tank VO.
            if unused_type != STORE_CONSTANTS.VEHICLE:
                filters = AccountSettings.getFilter(prefix + '_' + STORE_CONSTANTS.VEHICLE)
            filters = _store_filter_data(prefix + '_' + STORE_CONSTANTS.VEHICLE, filters)
            filters['obtainingType'] = STORE_CONSTANTS.VEHICLE
            filters['extra'] = [value for value in filters.get('extra', ())
                                if value in ('inHangar', 'rentals')]
            AccountSettings.setFilter(prefix + '_current',
                                      (nation, STORE_CONSTANTS.VEHICLE, False))
            AccountSettings.setFilter(prefix + '_' + STORE_CONSTANTS.VEHICLE, filters)
            Waiting.show('updateShop')
            try:
                self._setTableData(filters, nation, STORE_CONSTANTS.VEHICLE, False, None)
            finally:
                Waiting.hide('updateShop')

        def buyItem(self, itemCD, unused_trade_in=False):
            return _buy_vehicle(self, itemCD)

    original_table_request = Shop.requestTableData

    def request_shop_table(view, nation, actions_selected, item_type, filters):
        # ActionsFilterView.SELECT reaches Python through requestTableData.
        # Keep the regular shop's saved selection usable when returning here;
        # the offline control opens Offers instead of filtering retail sales.
        result = original_table_request(
            view, nation, False if actions_selected else actions_selected,
            item_type, filters)
        if (not actions_selected or not view._isDAAPIInited() or
                getattr(view, '_offlineOffersPending', False)):
            return result
        import BigWorld
        account = BigWorld.player()
        view._offlineOffersPending = True

        def open_offers():
            view._offlineOffersPending = False
            if (not view._isDAAPIInited() or BigWorld.player() is not account or
                    Shop.__dict__.get('requestTableData') is not request_shop_table):
                return
            from gui.shared import events, EVENT_BUS_SCOPE
            all_vehicles = copy.deepcopy(AccountSettings.getFilterDefault(
                'shop_' + STORE_CONSTANTS.VEHICLE))
            all_vehicles['obtainingType'] = STORE_CONSTANTS.VEHICLE
            all_vehicles['extra'] = []
            AccountSettings.setFilter(prefix + '_current',
                                      (-1, STORE_CONSTANTS.VEHICLE, False))
            AccountSettings.setFilter(prefix + '_' + STORE_CONSTANTS.VEHICLE,
                                      all_vehicles)
            view.fireEvent(events.LoadViewEvent(VIEW_ALIAS.LOBBY_STORE,
                ctx={'tabId': STORE_CONSTANTS.STORE_ACTIONS}),
                scope=EVENT_BUS_SCOPE.LOBBY)

        # StoreComponent.updateTable still accesses its table after this
        # Python callback returns. Let that Flash stack finish before the
        # non-cached StoreView destroys Shop and creates BondShop.
        BigWorld.callback(0.0, open_offers)
        return result

    _patch(Shop, 'requestTableData', request_shop_table)
    original_init = StoreView.as_initS

    def init_store(view, data):
        # StoreView registers the component using the tab ID. Bind that ID to
        # Shop's native Python controller before Flash creates ShopUI.
        _replace_component(VIEW_ALIAS.LOBBY_STORE_ACTIONS, BondShop)
        if view._isDAAPIInited():
            # StoreView.onPopulate enables caching, but ViewStack caches by
            # linkage, not tab ID. Both tabs use ShopUI: a cached ordinary Shop
            # skips NEED_UPDATE and never registers the BondShop controller.
            # Set this before as_init creates the first view. The stock
            # non-cache path unregisters/disposes the old tab on every switch.
            view.flashObject.viewStack.cache = False
        data = dict(data)
        data['buttonBarData'] = [dict(tab) for tab in data['buttonBarData']]
        for tab in data['buttonBarData']:
            if tab['id'] == STORE_CONSTANTS.STORE_ACTIONS:
                tab['linkage'] = STORE_CONSTANTS.SHOP_LINKAGE
        return original_init(view, data)

    _patch(StoreView, 'as_initS', init_store)


def _hide_bond_extra_categories(menu):
    # DataProvider extends Array: PyGFxValue converts it to a Python list.
    # Mutating that copy or calling .splice on it cannot change Flash.
    # Hide/disable the stock buttons; Accordion retains disposal ownership.
    # Its keyboard navigation skips disabled buttons as well.
    menu.validateNow()
    entries = menu.dataProvider
    for index in range(1, len(entries)):
        entries[index].enabled = False
        button = menu.getButtonAt(index)
        button.visible = False
        button.enabled = False


def _layout_bond_vehicle_filters(view):
    """Trim the stock ShopVehicleView after its own layout and visibility pass."""
    view.validateNow()
    view.resync()
    controls = ('vehTypeHeader', 'listVehicleType', 'vehLevelHeader',
                'listVehicleLevels', 'vehicleFilterExtraName', 'lockedChkBx',
                'inHangarChkBx', 'rentalsChckBx')
    shift = max(0, view.vehTypeHeader.y - view.obtainingTypeBuyBtn.y)
    for name in controls:
        control = getattr(view, name)
        control.y -= shift
    for name in ('obtainingTypeBuyBtn', 'obtainingTypeRestoreBtn',
                 'obtainingTypeTradeInBtn', 'lockedChkBx'):
        control = getattr(view, name)
        control.visible = False
        control.mouseEnabled = False
    # Reuse the authored checkbox spacing; repeated refreshes are idempotent.
    step = view.rentalsChckBx.y - view.inHangarChkBx.y
    view.inHangarChkBx.y = view.lockedChkBx.y
    view.rentalsChckBx.y = view.inHangarChkBx.y + step


def _install_vehicle_filters_and_recovery():
    from gui.Scaleform.daapi.view.lobby.store.tabs.shop import ShopVehicleTab
    from gui.shared.utils.requesters import REQ_CRITERIA
    from gui.shared.gui_items.Vehicle import Vehicle
    from gui.shared.money import Money
    original_price = Vehicle.restorePrice

    def extra_criteria(tab, extra, criteria, unused_inventory):
        bond_vehicles = set(row['cd'] for row in snapshot().get('offlineVehicleOffers', ()))
        return criteria | REQ_CRITERIA.CUSTOM(lambda item:
            item.intCD not in bond_vehicles and
            policy.matches_vehicle_filters(extra, item.isPurchased,
                                           item.isRented, item.isUnlocked))

    def restore_price(item):
        offer = policy.vehicle_recovery_offer(snapshot(), item.intCD)
        return (Money(credits=offer['credits']) if offer is not None
                else original_price.fget(item))

    _patch(ShopVehicleTab, '_getExtraCriteria', extra_criteria)
    _patch(Vehicle, 'restorePrice', property(restore_price))


def _install_reserve_slots():
    """Publish the offline limit to every native consumer's imported copy."""
    import importlib
    # Changing only goodie_items leaves already-imported window/panel copies
    # and the prebuilt Flash layout at the regional client's old slot count.
    modules = tuple(importlib.import_module(name) for name in (
            'gui.goodies.goodie_items',
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersWindow',
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersPanelComponent'))
    for module in modules:
        _patch(module, 'MAX_ACTIVE_BOOSTERS_COUNT', policy.MAX_ACTIVE_RESERVES)
    panel = modules[-1]
    props = dict(panel._GUI_SLOTS_PROPS)
    props['slotsCount'] = policy.MAX_ACTIVE_RESERVES
    _patch(panel, '_GUI_SLOTS_PROPS', props)


def _install_reserves():
    from gui.Scaleform.daapi.view.lobby.boosters.BoostersWindow import BoostersWindow
    from gui.Scaleform.daapi.view.lobby.boosters import booster_tabs
    from gui.game_control.BoostersController import BoostersController
    _install_reserve_slots()
    original_init = BoostersWindow.__init__

    def boosters_init(view, ctx=None):
        original_init(view, ctx or {})

    def action(view, booster_id, quest_id):
        key = policy.RESERVE_KEYS.get(int(booster_id))
        if key is None or getattr(view, '_offlineBuying', False):
            return
        tab = view._BoostersWindow__tabsContainer.currentTab.getID()
        if tab == booster_tabs.TABS_IDS.QUESTS:
            from gui.server_events.events_dispatcher import showMissions
            view.destroy()
            showMissions()
            return
        view._offlineBuying = True

        def done(success, error):
            view._offlineBuying = False
            if view._isDAAPIInited():
                view._BoostersWindow__update()

        def submit(accepted=True):
            if not accepted:
                view._offlineBuying = False
                return
            try:
                request('buy_reserve' if tab == booster_tabs.TABS_IDS.SHOP
                        else 'activate_reserve', key, done)
            except Exception:
                view._offlineBuying = False
                raise

        active = policy.reserve_state(snapshot())['active']
        current = next((other for other, interval in active.items()
            if interval[1] > policy.now_seconds() and
               policy.RESERVE_BY_ID[other].kind == policy.RESERVE_BY_ID[key].kind), None)
        if tab != booster_tabs.TABS_IDS.SHOP and current is not None:
            from gui import DialogsInterface
            from gui.Scaleform.daapi.view.dialogs import I18nConfirmDialogMeta, DIALOG_BUTTON_ID
            from gui.Scaleform.genConsts.BOOSTER_CONSTANTS import BOOSTER_CONSTANTS
            from gui.shared.formatters import text_styles
            cache = view._BoostersWindow__tabsContainer.currentTab.goodiesCache
            try:
                DialogsInterface.showDialog(I18nConfirmDialogMeta(
                    BOOSTER_CONSTANTS.BOOSTER_ACTIVATION_CONFORMATION_TEXT_KEY,
                    messageCtx={
                        'newBoosterName': text_styles.middleTitle(cache.getBooster(
                            policy.RESERVE_IDS[key]).description),
                        'curBoosterName': text_styles.middleTitle(cache.getBooster(
                            policy.RESERVE_IDS[current]).description)},
                    focusedID=DIALOG_BUTTON_ID.CLOSE), submit)
            except Exception:
                view._offlineBuying = False
                raise
        else:
            submit()

    def daily_boosters(tab):
        daily = policy.daily_state(snapshot())
        tab._boosters = []
        for key in daily['missions']:
            row = policy.MISSION_BY_ID[key]
            label = mission_label(row)
            label += ' (%d/%d)' % (daily[key], row[3])
            if key in daily['claimed']:
                label += tr(' (RECEIVED)')
            booster = tab.goodiesCache.getBooster(policy.RESERVE_IDS[row[4]])
            if booster is not None:
                tab._boosters.append((key, label, booster, 1))
        tab._totalCount = len(tab._boosters)
        tab._boosters = [row for row in tab._boosters if tab._isBoosterValid(row[2])]
        tab._count = len(tab._boosters)

    original_notify = BoostersController._BoostersController__notifyBoosterTime

    def notify(controller):
        original_notify(controller)
        stamp = policy.now_seconds()
        expired = any(value[1] <= stamp for value in
                      policy.reserve_state(snapshot())['active'].values())
        if expired and not getattr(controller, '_offlineExpiring', False):
            controller._offlineExpiring = True

            def done(success, error):
                controller._offlineExpiring = False

            try:
                request('expire_reserves', '', done)
            except Exception:
                controller._offlineExpiring = False
                raise

    _patch(BoostersWindow, '__init__', boosters_init)
    _patch(BoostersWindow, 'onBoosterActionBtnClick', action)
    _patch(booster_tabs.QuestsBoostersTab, '_processBoostersData', daily_boosters)
    _patch(BoostersController, '_BoostersController__notifyBoosterTime', notify)


def _install_account():
    from gui.Scaleform.daapi.view.lobby.header.AccountPopover import AccountPopover
    from gui.Scaleform.daapi.view.lobby.BadgesPage import BadgesPage
    from gui.Scaleform.settings import getBadgeIconPath, BADGES_ICONS
    from gui.Scaleform.locale.RES_ICONS import RES_ICONS
    from gui.Scaleform.daapi.settings.views import VIEW_ALIAS
    from gui.shared import events, EVENT_BUS_SCOPE

    def account_init(view, ctx=None):
        super(AccountPopover, view).__init__(ctx)
        view._AccountPopover__infoBtnEnabled = True
        view._AccountPopover__tutorStorage = None

    def account_data(view):
        import BigWorld
        selected = policy.selected_badges(snapshot())
        icon = (getBadgeIconPath(BADGES_ICONS.X48, selected[0]) if selected else
                RES_ICONS.MAPS_ICONS_LIBRARY_BADGES_48X48_BADGE_DEFAULT)
        name = BigWorld.player().name
        view.as_setDataS({'userData': {'fullName': name, 'userName': name,
                                     'clanAbbrev': ''},
                         'isTeamKiller': False, 'badgeIcon': icon,
                         'boostersBlockTitle': tr('PERSONAL RESERVES'),
                         'boostersBlockTitleTooltip': ''})
        view.as_setClanDataS({'isInClan': False, 'isClanFeaturesEnabled': False,
            'isDoActionBtnVisible': False, 'requestInviteBtnVisible': False,
            'isSearchClanBtnVisible': False, 'isTextFieldNameVisible': False,
            'isSearchClanBtnEnabled': False, 'inviteBtnEnabled': False,
            'btnEnabled': False, 'formation': '', 'clanResearchIcon': '',
            'clanResearchTFText': '', 'searchClanTooltip': '', 'inviteBtnIcon': '',
            'inviteBtnTooltip': '', 'clansResearchBtnYposition': 72})

    def populate(view):
        # AbstractPopOverView installs HidePopoverEvent and announces its
        # destruction. Keep that lifecycle; avoid online clan/tutorial setup.
        super(AccountPopover, view)._populate()
        account_data(view)

    def dispose(view):
        super(AccountPopover, view)._dispose()

    def open_reserves(view, unused_idx):
        view.destroy()
        view.fireEvent(events.LoadViewEvent(VIEW_ALIAS.BOOSTERS_WINDOW, ctx={}),
                       EVENT_BUS_SCOPE.LOBBY)

    def open_badges(view):
        view.destroy()
        view.fireEvent(events.LoadViewEvent(VIEW_ALIAS.BADGES_PAGE, ctx={}),
                       EVENT_BUS_SCOPE.LOBBY)

    def update_badges(view):
        from gui.Scaleform.daapi.view.lobby.BadgesPage import _makeBadgeVO
        selected = policy.selected_badges(snapshot())
        rows, locked = [], []
        for badge in sorted(view.itemsCache.items.getBadges().values(),
                            key=lambda value: (value.getWeight(), value.badgeID)):
            row = _makeBadgeVO(badge)
            row.update(enabled=badge.isAchieved,
                       selected=badge.isAchieved and badge.badgeID in selected)
            (rows if badge.isAchieved else locked).append(row)
        view.as_setReceivedBadgesS({'badgesData': rows})
        from gui.Scaleform.locale.BADGE import BADGE
        from gui.shared.formatters import text_styles
        view.as_setNotReceivedBadgesS({
            'title': text_styles.highTitle(BADGE.BADGESPAGE_BODY_UNCOLLECTED_TITLE),
            'badgesData': locked})
        view.as_setSelectedBadgeImgS(
            getBadgeIconPath(BADGES_ICONS.X48, selected[0]) if selected else '')

    def select_badge(view, badge_id=0):
        if getattr(view, '_offlineBuying', False):
            return
        view._offlineBuying = True

        def done(success, error):
            view._offlineBuying = False
            if view._isDAAPIInited():
                update_badges(view)

        try:
            request('select_badge', badge_id, done)
        except Exception:
            view._offlineBuying = False
            raise

    _patch(AccountPopover, '__init__', account_init)
    _patch(AccountPopover, '_populate', populate)
    _patch(AccountPopover, '_dispose', dispose)
    _patch(AccountPopover, 'openBoostersWindow', open_reserves)
    _patch(AccountPopover, 'openBadgesWindow', open_badges)
    _patch(AccountPopover, '_AccountPopover__syncUserInfo', account_data)
    _patch(BadgesPage, '_BadgesPage__updateBadges', update_badges)
    _patch(BadgesPage, 'onSelectBadge', select_badge)
    _patch(BadgesPage, 'onDeselectBadge', select_badge)


def mission_label(row):
    label = tr(row[1])
    return label % row[3] if '%d' in label else label


class _ServiceControl(_ControlScript):
    def handleKeyEvent(self, event):
        import Keys
        if event.key == Keys.KEY_ESCAPE and event.isKeyDown():
            self._room.activate('close')
            return True
        return False


class _DailySurface(NativeSurface):
    def window(self):
        return self._gui.Window(OUTLINE_TEXTURE)


class ServicePanel(WaitingRoomUI):
    """Daily missions on an empty native page, with no stock tab below it."""
    def __init__(self, page='daily', on_close=None, surface=None):
        WaitingRoomUI.__init__(self, lambda unused: False, lambda: (),
                               on_close=on_close, surface=surface or _DailySurface())
        self._last_paint = 0

    def install(self):
        if self._panel is not None:
            return True
        WaitingRoomUI.install(self)
        for component in list(self._controls.values()) + list(self._labels.values()):
            self._panel.delChild(component)
        self._controls, self._labels = {}, {}
        self._panel.script = _ServiceControl(self, None)
        self._make_label('title', tr('DAILY MISSIONS'), (-0.89, 0.82, 0), 1.8, 0.12)
        for index in range(3):
            self._make_label('row%d' % index, '',
                             (-0.89, 0.48 - index * 0.3, 0), 1.8, 0.2)
        self._make_label('note', tr('Standard battles only. Rewards automatic. Resets at 00:00 UTC.'),
                         (-0.89, -0.55, 0), 1.8, 0.13)
        for role, label, x in (('reserves', tr('PERSONAL RESERVES'), -0.45),
                               ('close', tr('CLOSE'), 0.55)):
            self._make_control(role, (x, -0.85, CONTROL_Z), 0.75, 0.16)
            self._make_label(role, label, (x, -0.85, 0), 0.75, 0.14,
                             anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
            self._controls[role].script = _ServiceControl(self, role)
        return True

    def _sync_selection(self):
        return ()

    def _move_pointer(self):
        WaitingRoomUI._move_pointer(self)
        stamp = int(time.time())
        if self._open and self._last_paint != stamp:
            self._last_paint = stamp
            self.refresh()

    def _refresh_contents(self):
        self._apply_layout()
        daily = policy.daily_state(snapshot())
        for index, key in enumerate(daily['missions']):
            row = policy.MISSION_BY_ID[key]
            self._set_text('row%d' % index, tr('%s: %d/%d | Reward: %s reserve x1%s') % (
                mission_label(row), daily[key], row[3],
                tr(policy.RESERVE_BY_ID[row[4]][1]),
                tr(' (RECEIVED)') if key in daily['claimed'] else ''))
        for component in list(self._controls.values()) + list(self._labels.values()):
            self._set(component, 'visible', True)
        self._set(self._panel, 'visible', True)
        self._paint()
        return True

    def activate(self, role):
        if not self._open or role not in ('close', 'reserves'):
            return False
        self.close()
        if callable(self._on_close):
            self._on_close()
        if role == 'reserves':
            from gui.shared.event_dispatcher import showBoostersWindow
            showBoostersWindow()
        return True


def close_panel(view):
    panel = getattr(view, '_offlineServicePanel', None)
    if panel is not None:
        panel.uninstall()
        if panel in _panels:
            _panels.remove(panel)
        view._offlineServicePanel = None


def _install_daily():
    from gui.Scaleform.daapi.view.lobby.missions.regular.missions_page import MissionsPage

    def populate(view):
        super(MissionsPage, view)._populate()
        view.as_setTabsDataProviderS([])
        view.as_showFilterS(False)
        panel = ServicePanel(on_close=view.onClose)
        view._offlineServicePanel = panel
        _panels.append(panel)
        if not panel.open():
            close_panel(view)
            view.onClose()
            raise RuntimeError('the offline daily mission page could not open')

    def dispose(view):
        close_panel(view)
        super(MissionsPage, view)._dispose()

    def invalidate(view, ctx=None):
        # Do not rebuild retail tabs/empty messages on an offline task page.
        panel = getattr(view, '_offlineServicePanel', None)
        if panel is not None:
            panel.refresh()

    _patch(MissionsPage, '_populate', populate)
    _patch(MissionsPage, '_dispose', dispose)
    _patch(MissionsPage, '_invalidate', invalidate)


def _install_settings():
    from gui.Scaleform.daapi.view.common.settings.SettingsWindow import (
        SettingsWindow, SETTINGS, _PAGES_INDICES, _setLastTabIndex)
    from account_helpers.settings_core.options import VOIPSupportSetting
    original_tab = SettingsWindow.onTabSelected

    def tab_selected(view, tab):
        if tab != SETTINGS.SOUNDTITLE:
            return original_tab(view, tab)
        _setLastTabIndex(_PAGES_INDICES[tab])
        if not getattr(view, '_offlineVoiceNotice', False):
            from gui import SystemMessages
            SystemMessages.pushMessage(tr('Voice chat is unavailable in offline mode.'))
            view._offlineVoiceNotice = True

    _patch(SettingsWindow, 'onTabSelected', tab_selected)
    _patch(VOIPSupportSetting, '_VOIPSupportSetting__isSupported', lambda unused: False)


def _install_item_comparisons(item_type=None):
    """Keep native price longs out of Python 2's strict cmp return boundary.

    Barracks forwards the seated vehicle's comparison to sorted(). The stock
    fitting comparator can return a price subtraction, and our shop supplies
    longs for the native price formatter. Preserve the stock ordering and
    price objects; only the comparison result becomes an int sign. Patching
    the producer also covers direct vehicle/module sorts using that result.
    """
    if item_type is None:
        from gui.shared.gui_items.fitting_item import FittingItem
        item_type = FittingItem
    original = item_type.__cmp__

    def compare_items(first, second):
        result = original(first, second)
        return (result > 0) - (result < 0)

    _patch(item_type, '__cmp__', compare_items)


def _install_directive_fitting():
    """Route #1513's generic fitting actions to its directive processor.

    BattleBooster has a distinct GUI item type. The stock generic factory
    falls through to OtherModuleInstaller, whose validator rejects it before
    any Account request. Both action consumers import the factory by name.
    Reuse the stock four-slot layout helper and confirmation/response path.
    """
    from gui.shared.gui_items.processors import module as module_processors
    from gui.shared.gui_items.processors.vehicle import (
        VehicleBattleBoosterLayoutProcessor)
    from gui.shared.gui_items.vehicle_equipment import EquipmentLayoutHelper
    from gui.shared.gui_items.items_actions import actions

    original = module_processors.getInstallerProcessor

    def get_installer(vehicle, newComponentItem, slotIdx=0, install=True,
                      isUseMoney=False, conflictedEqs=None, skipConfirm=False):
        if (newComponentItem.itemTypeID !=
                module_processors.GUI_ITEM_TYPE.BATTLE_BOOSTER):
            return original(vehicle, newComponentItem, slotIdx, install,
                            isUseMoney, conflictedEqs, skipConfirm)
        booster = newComponentItem if install else None
        layout = EquipmentLayoutHelper(
            vehicle, None, (booster.intCD, 1) if booster else (0, 0))
        return VehicleBattleBoosterLayoutProcessor(
            vehicle, booster, layout, skipConfirm)

    _patch(module_processors, 'getInstallerProcessor', get_installer)
    _patch(actions, 'getInstallerProcessor', get_installer)


def _install_exchange_dialog_limits(meta_type=None):
    """Keep one legacy exchange control within signed 32-bit item amounts.

    #1513's shared meta sends maxGoldValue and exchangeRate to the stock
    credit/XP confirmation block. Large offline balances can overflow a
    32-bit converted-item limit even when the requested purchase is small.
    Limit only the presentation maximum; balances, requested/default amounts,
    rates and the Account transaction remain owned by their native paths.
    Exact #1513 SWF rendering remains a Windows acceptance item.
    """
    if meta_type is None:
        from gui.Scaleform.daapi.view.dialogs.ExchangeDialogMeta import (
            _ExchangeDialogMeta)
        meta_type = _ExchangeDialogMeta
    original = meta_type.makeVO

    def make_vo(meta):
        value = original(meta)
        block = value['exchangeBlockData']
        rate = float(block['exchangeRate'])
        if rate <= 0:
            return value
        safe_gold = int(2147483647 // rate)
        if block['maxGoldValue'] <= safe_gold:
            return value
        # Never change a native/cached VO shared with another window.
        limited = dict(block)
        limited['maxGoldValue'] = safe_gold
        result = dict(value)
        result['exchangeBlockData'] = limited
        return result

    _patch(meta_type, 'makeVO', make_vo)


def install():
    if _patches:
        return
    try:
        _install_item_comparisons()
        _install_directive_fitting()
        _install_store_filters()
        _install_shop()
        _install_vehicle_filters_and_recovery()
        _install_reserves()
        _install_account()
        _install_daily()
        _install_mission_results()
        _install_settings()
        _install_exchange_dialog_limits()
        from gui.mods.offline_lan_0922 import crew_voice
        crew_voice.install(_patch)
        _schedule_daily_rollover()
    except Exception:
        uninstall()
        raise


def uninstall():
    global _daily_timer
    if _daily_timer is not None:
        import BigWorld
        BigWorld.cancelCallback(_daily_timer)
        _daily_timer = None
    for panel in list(_panels):
        panel.uninstall()
    _panels[:] = []
    if _factory_settings:
        from gui.Scaleform.framework import g_entitiesFactories
        for settings in reversed(_factory_settings):
            g_entitiesFactories.removeSettings(settings.alias)
            g_entitiesFactories.addSettings(settings)
        _factory_settings[:] = []
    for defaults, key, present, value in reversed(_filter_defaults):
        if present:
            defaults[key] = value
        else:
            defaults.pop(key, None)
    _filter_defaults[:] = []
    for owner, name, original in reversed(_patches):
        setattr(owner, name, original)
    _patches[:] = []
