"""Registered request handlers only; unknown command ids deliberately fail."""

from __future__ import print_function

import sys
import time
import traceback

_clock = getattr(time, 'perf_counter', None) or time.clock

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import data
from gui.mods.offline_lan_0922.account_rpc import garage

try:
    _NATIVE_LONG = long
except NameError:
    _NATIVE_LONG = int


class Result(object):
    def __init__(self, result_id, error='', stream=None, ext=None,
                 before_response=None, wait_for_before_response=False):
        self.result_id = result_id
        self.error = error
        self.stream = stream
        self.ext = ext
        self.before_response = before_response
        self.wait_for_before_response = bool(wait_for_before_response)


# AccountCommands.BUY_VEHICLE_FLAG in #1513, read from the shipped bytecode:
# NONE = 0, CREW = 1, SHELLS = 16.  Shop.buyVehicle folds the two optional
# extras into one flag word before sending them.
BUY_VEHICLE_FLAG_CREW = 1
BUY_VEHICLE_FLAG_SHELLS = 16


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _garage(context):
    """Return the mutable garage, promoting the immutable snapshot once."""
    state = context.get('garage')
    if state is None:
        state = garage.GarageState(context.get('selected_vehicle') or {})
        context['garage'] = state
    return state


def _fitting(context, mutate, extension=None):
    """Apply one fitting mutation and push the resulting inventory diff.

    #1513 refreshes the garage from ``PlayerAccount.update``, which unpickles
    its argument and runs the normal ``_update`` event path.  Publishing the
    reshaped inventory section reuses the same builder a full sync uses, so the
    pushed diff and a later re-sync can never disagree.  The success response
    waits for that refresh because an immediate battle snapshots crew and
    equipment from ``g_currentVehicle``, not directly from ``GarageState``.
    """
    started = _clock()
    state = _garage(context)
    state.touched_vehicles()
    state.touched_items()
    try:
        outcome = mutate(state)
    except garage.GarageError as error:
        return Result(commands.RES_FAILURE, str(error))
    # A few #1513 callbacks read a value out of the response's ext dictionary
    # rather than re-reading the inventory, so the mutation can name one.
    ext = None if extension is None else extension(outcome)
    context['selected_vehicle'] = state.snapshot()
    mutated = _clock()
    store = context.get('garage_store')
    if store is not None:
        # A fitting happens at click speed, so saving on each accepted change
        # costs nothing and a hard client kill cannot lose an applied change.
        store.mark_dirty()
        store.flush(state.snapshot())
    saved = _clock()
    push = context.get('push_update')
    if not callable(push):
        _report_fitting_cost(started, mutated, saved, saved, None)
        return Result(commands.RES_SUCCESS, ext=ext)
    push_and_wait = context.get('push_update_and_wait')

    touched = state.touched_vehicles()
    touched_items = state.touched_items()
    moved_tankmen = state.touched_tankmen()

    def publish(on_complete=None):
        diff = data.inventory(
            state.snapshot(), validate=False, only_vehicles=touched,
            only_items=touched_items, touched_tankmen=moved_tankmen)
        built = _clock()
        completed = [False]

        def complete(unused_player=None):
            if completed[0] or not callable(on_complete):
                return
            completed[0] = True
            on_complete()

        if callable(on_complete) and callable(push_and_wait):
            if not push_and_wait(diff, after_publish=complete):
                complete()
        else:
            push(diff)
            complete()
        _report_fitting_cost(started, mutated, saved, built, diff)

    return Result(
        commands.RES_SUCCESS, ext=ext, before_response=publish,
        wait_for_before_response=True)


def _report_fitting_cost(started, mutated, saved, built, diff):
    """Log where a garage click spends its time, and how big the diff is."""
    items = 0
    if isinstance(diff, dict):
        for section in (diff.get('inventory') or {}).values():
            if isinstance(section, dict):
                for value in section.values():
                    items += len(value) if hasattr(value, '__len__') else 1
    finished = _clock()
    sys.stdout.write(
        '[Offline LAN 0.9.22] garage command ms mutate=%.1f save=%.1f '
        'build=%.1f publish=%.1f total=%.1f diff_items=%d\n' % (
            (mutated - started) * 1000.0, (saved - mutated) * 1000.0,
            (built - saved) * 1000.0, (finished - built) * 1000.0,
            (finished - started) * 1000.0, items))


def _equip_equipments(context, args):
    # [vehInvID, *getConsumablesIntCDs()]: three regular slots then the booster
    values = list(args[0] if args else ())
    if not values:
        return Result(commands.RES_FAILURE, 'INVALID_EQUIPMENT_REQUEST')
    return _fitting(context, lambda state: state.equip_equipments(
        values[0], values[1:]))


def _equip_shells(context, args):
    values = list(args[0] if args else ())
    if not values:
        return Result(commands.RES_FAILURE, 'INVALID_SHELL_REQUEST')
    return _fitting(context, lambda state: state.equip_shells(
        values[0], values[1:]))


def _equip_optional_device(context, args):
    # [shopRev, vehInvID, deviceCompDescr, slotIdx, isPaidRemoval]
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_DEVICE_REQUEST')
    paid_removal = bool(_int(values[4])) if len(values) > 4 else False
    return _fitting(context, lambda state: state.equip_optional_device(
        values[1], values[2], values[3], paid_removal=paid_removal))


def _equip_component(context, args):
    # _doCmdInt3: (vehInvID, compactDescr, gunCompactDescr).  Inventory.equip
    # sends 0 in the third slot; Inventory.equipTurret puts the gun there.
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_MODULE_REQUEST')
    return _fitting(context, lambda state: state.install_component(
        args[0], args[1], args[2]))


def _set_and_fill_layouts(context, args):
    # [shopRev, vehInvID, len(shells), *shells, eqType, len(eqs), *eqs], where
    # both blocks are flat descriptor/count pairs and eqs covers four slots.
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_LAYOUT_REQUEST')
    cursor = 2
    shell_count = int(values[cursor])
    cursor += 1
    shells_layout = None
    if shell_count:
        shells_layout = values[cursor:cursor + shell_count]
        cursor += shell_count
    if cursor >= len(values):
        return Result(commands.RES_FAILURE, 'INVALID_LAYOUT_REQUEST')
    equipment_type = int(values[cursor])
    cursor += 1
    equipments_layout = None
    if cursor < len(values):
        equipment_count = int(values[cursor])
        cursor += 1
        if equipment_count:
            equipments_layout = values[cursor:cursor + equipment_count]
    return _fitting(context, lambda state: state.set_layouts(
        values[1], shells_layout, equipment_type, equipments_layout))


def _add_tankman_skill(context, args):
    if len(args) < 2:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(context, lambda state: state.add_tankman_skill(
        args[0], args[1]))


def _drop_tankman_skills(context, args):
    # Inventory.__dropSkillsTman_onShopSynced sends
    # (shopRev, tmanInvID, dropSkillsCostIdx).
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context, lambda state: state.drop_tankman_skills(args[1]))


def _train_tankman(context, args):
    # Inventory.__freeXPToTankman_onShopSynced sends
    # (shopRev, tmanInvID, freeXP).
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context, lambda state: state.train_tankman(args[1], args[2]))


def _equip_tankman(context, args):
    # Inventory.equipTankman -> _doCmdInt3(CMD_EQUIP_TMAN, vehInvID, slot,
    # tmanInvID), with tmanInvID -1 for an empty seat.  TankmanUnload sends
    # slot -1 alongside it to unload the whole crew.
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(context, lambda state: state.equip_tankman(
        args[0], args[1], args[2]))


def _dismiss_tankman(context, args):
    # Inventory.dismissTankman -> _doCmdInt3(CMD_DISMISS_TMAN, tmanInvID,
    # 0, 0).
    if len(args) < 1:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(context, lambda state: state.dismiss_tankman(args[0]))


def _retrain_tankman(context, args):
    # Inventory.__respecTman_onShopSynced -> _doCmdInt4(CMD_TMAN_RESPEC,
    #   shopRev, tmanInvID, tmanCostTypeIdx, vehTypeCompDescr).
    if len(args) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(context, lambda state: state.retrain_tankman(
        args[1], args[2], args[3]))


def _retrain_crew(context, args):
    # Inventory.__multiRespecTman_onShopSynced -> _doCmdIntArr(
    #   CMD_TMAN_MULTI_RESPEC,
    #   [shopRev, vehTypeCompDescr, tmanInvID, costIdx, ...]).
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context, lambda state: state.retrain_crew(values[1], values[2:]))


def _repair(context, args):
    # Inventory.repair -> _doCmdInt3(CMD_REPAIR, vehInvID, 0, 0).
    if len(args) < 1:
        return Result(commands.RES_FAILURE, 'INVALID_REPAIR_REQUEST')
    return _fitting(context, lambda state: state.repair_vehicle(args[0]))


def _buy_tankman(context, args):
    # Shop.buyTankman -> _doCmdInt4(CMD_BUY_TMAN, cacheRev, vehTypeCompDescr,
    # roleIdx, tmanCostTypeIdx), where roleIdx is tankmen.SKILL_INDICES[role].
    # Its callback reads ext['tmanInvID'], so the response has to carry it.
    if len(args) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context,
        lambda state: state.buy_tankman(args[1], args[2], args[3]),
        extension=lambda tankman_id: {'tmanInvID': int(tankman_id)})


def _buy_and_equip_tankman(context, args):
    # Shop.buyAndEquipTankman -> _doCmdInt4(CMD_BUY_AND_EQUIP_TMAN, cacheRev,
    # vehInvID, slot, tmanCostTypeIdx).
    if len(args) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context,
        lambda state: state.buy_and_equip_tankman(args[1], args[2], args[3]))


def _buy_item(context, args):
    # _doCmdInt4: (cacheRev, intCompactDescr, count, goldForCredits)
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_PURCHASE_REQUEST')
    gold_for_credits = bool(_int(args[3])) if len(args) > 3 else False
    return _fitting(context, lambda state: state.buy_item(
        args[1], args[2], gold_for_credits=gold_for_credits))


def _buy_and_equip_item(context, args):
    # [cacheRev, compDescr, vehInvID, slotIdx, isPaidRemoval, gunCompDescr]
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_PURCHASE_REQUEST')
    gun_compact_descr = values[5] if len(values) > 5 else 0
    return _fitting(context, lambda state: state.buy_and_equip_item(
        values[2], values[1], values[3], gun_compact_descr))


def _unlock(context, args):
    # Stats.unlock -> _doCmdInt3(CMD_UNLOCK, vehTypeCompDescr, unlockIdx, 0).
    if len(args) < 2:
        return Result(commands.RES_FAILURE, 'INVALID_RESEARCH_REQUEST')
    return _fitting(
        context, lambda state: state.unlock(args[0], args[1]))


def _buy_vehicle(context, args):
    # Shop.buyVehicle -> _doCmdIntArr(CMD_BUY_VEHICLE,
    # [cacheRev, typeCompDescr, flags, tmanCostTypeIdx, rentPeriod]).
    values = list(args[0] if args else ())
    if len(values) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_PURCHASE_REQUEST')
    flags = _int(values[2])
    rent_period = values[4] if len(values) > 4 else 0
    tman_cost_type_index = values[3] if len(values) > 3 else 0
    return _fitting(context, lambda state: state.buy_vehicle(
        values[1],
        buy_shells=bool(flags & BUY_VEHICLE_FLAG_SHELLS),
        recruit_crew=bool(flags & BUY_VEHICLE_FLAG_CREW),
        tman_cost_type_index=tman_cost_type_index,
        rent_period=rent_period))


def _sell_vehicle(context, args):
    # Inventory.__sellVehicle_onShopSynced -> _doCmdIntArr(CMD_SELL_VEHICLE,
    # [shopRev, vehInvID, isCrewDismiss, len(itemsFromVehicle)]
    # + itemsFromVehicle + [len(itemsFromInventory)] + itemsFromInventory).
    # The two lists carry the intCDs of the mounted and stored items the sell
    # dialog offered to sell along with the vehicle.
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_SALE_REQUEST')
    from_vehicle_count = _int(values[3])
    tail = 4 + from_vehicle_count
    if from_vehicle_count < 0 or len(values) < tail + 1:
        return Result(commands.RES_FAILURE, 'INVALID_SALE_REQUEST')
    items_from_vehicle = [_int(value) for value in values[4:tail]]
    from_inventory_count = _int(values[tail])
    end = tail + 1 + from_inventory_count
    if from_inventory_count < 0 or len(values) < end:
        return Result(commands.RES_FAILURE, 'INVALID_SALE_REQUEST')
    items_from_inventory = [_int(value) for value in values[tail + 1:end]]
    return _fitting(context, lambda state: state.sell_vehicle(
        values[1], dismiss_crew=bool(_int(values[2])),
        items_from_vehicle=items_from_vehicle,
        items_from_inventory=items_from_inventory))


def _sell_item(context, args):
    # Inventory.__sellItem_onShopSynced -> _doCmdInt4(CMD_SELL_ITEM, shopRev,
    # itemTypeIdx, itemInvID, count).  ModuleSeller passes the item's intCD as
    # the inventory id, so the third value is the compact descriptor itself.
    if len(args) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_SALE_REQUEST')
    return _fitting(
        context, lambda state: state.sell_item(args[2], args[3]))


def _exchange(context, args):
    # Stats.exchange -> _doCmdInt3(CMD_EXCHANGE, shopRev, gold, 0).
    if len(args) < 2:
        return Result(commands.RES_FAILURE, 'INVALID_EXCHANGE_REQUEST')
    return _fitting(context, lambda state: state.exchange_gold(args[1]))


def _convert_free_xp(context, args):
    # Stats.convertToFreeXP -> _doCmdIntArr(CMD_FREE_XP_CONV,
    # [shopRev, xp, useDiscount] + vehTypeCompDescrs).
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_EXCHANGE_REQUEST')
    return _fitting(context, lambda state: state.convert_to_free_xp(
        values[3:], values[1]))


def _buy_slot(context, args):
    # Stats.buySlot -> _doCmdInt3(CMD_BUY_SLOT, shopRev, 0, 0).
    return _fitting(context, lambda state: state.buy_slot())


def _buy_berths(context, args):
    # Stats.buyBerths -> _doCmdInt3(CMD_BUY_BERTHS, shopRev, 0, 0).
    return _fitting(context, lambda state: state.buy_berths())


def _vehicle_settings(context, args):
    # _doCmdInt3: (vehInvID, setting, isOn)
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_SETTING_REQUEST')
    return _fitting(context, lambda state: state.change_vehicle_setting(
        args[0], args[1], args[2]))


def _apply_style(context, args):
    # Shop.applyStyle -> _doCmdInt3(shopRev, vehInvID, styleID).
    if len(args) != 3:
        return Result(commands.RES_FAILURE, 'INVALID_STYLE_REQUEST')
    return _fitting(context, lambda state: state.apply_style(
        args[1], args[2]))


def _sell_customization(context, args):
    # Shop.sellCustomization -> (shopRev, itemCD, count, vehInvID).
    if len(args) != 4:
        return Result(commands.RES_FAILURE, 'INVALID_CUSTOMIZATION_SALE')
    return _fitting(context, lambda state: state.sell_customization(
        args[3], args[1], args[2]))


def _buy_customizations(context, args):
    # [shopRev, vehInvID, itemCD, count, ...].
    values = list(args[0] if len(args) == 1 else ())
    if len(values) < 4 or len(values[2:]) % 2:
        return Result(commands.RES_FAILURE, 'INVALID_CUSTOMIZATION_PURCHASE')
    return _fitting(context, lambda state: state.buy_customizations(
        values[1], values[2:]))


def _apply_outfit(context, args):
    # Shop.applyOutfit -> intArr [shopRev, vehInvID, season], strArr [descr].
    ints = list(args[0] if len(args) == 2 else ())
    strings = list(args[1] if len(args) == 2 else ())
    if len(ints) != 3 or len(strings) != 1:
        return Result(commands.RES_FAILURE, 'INVALID_OUTFIT_REQUEST')
    return _fitting(context, lambda state: state.apply_outfit(
        ints[1], ints[2], strings[0]))


def _sync_data(context, args):
    revision = args[0] if args else 0
    account_state = context.get('account_state')
    int_user_settings = (
        account_state.snapshot() if account_state is not None else {})
    postbattle = context.get('postbattle_store')
    progress = postbattle.progress() if postbattle is not None else None
    return Result(commands.RES_SUCCESS, '', ext=data.sync_data(
        revision, context.get('selected_vehicle'), int_user_settings,
        progress))


def _server_stats(context, args):
    receiver = context.get('receive_server_stats')
    if not callable(receiver):
        return Result(commands.RES_SUCCESS)

    def publish_stats():
        receiver({'clusterCCU': 0, 'regionCCU': 0})

    return Result(commands.RES_SUCCESS, before_response=publish_stats)


def _native_price_value(value, integer_factory=None):
    """Give #1513's native price formatter real Python 2 ``long`` values."""
    integer_factory = integer_factory or _NATIVE_LONG
    if isinstance(value, dict):
        return dict((currency, integer_factory(amount))
                    for currency, amount in value.items())
    if isinstance(value, tuple):
        return tuple(_native_price_value(part, integer_factory)
                     if isinstance(part, (dict, tuple)) else part
                     for part in value)
    return value


def _wrap_shop_item_prices(value, factory=None, integer_factory=None):
    """Convert wire mappings to #1513's dual-purpose ItemsPrices object."""
    if factory is None:
        from items import ItemsPrices
        factory = ItemsPrices
    current = value['items']['itemPrices']
    defaults = value['defaults']['items']['itemPrices']
    current = dict((compact_descr, _native_price_value(
        price, integer_factory)) for compact_descr, price in current.items())
    defaults = dict((compact_descr, _native_price_value(
        price, integer_factory)) for compact_descr, price in defaults.items())
    value['items']['itemPrices'] = factory(current)
    value['defaults']['items']['itemPrices'] = factory(defaults)
    return value


def _sync_shop(context, args):
    revision = args[0] if args else 0
    value = data.shop(revision, context.get('selected_vehicle'))
    value = _wrap_shop_item_prices(
        value, context.get('items_prices_factory'))
    return Result(commands.RES_STREAM, '', value)


def _sync_dossiers(context, args):
    revision = args[0] if args else 0
    max_change_time = args[1] if len(args) > 1 else 0
    postbattle = context.get('postbattle_store')
    progress = postbattle.progress() if postbattle is not None else None
    return Result(commands.RES_STREAM, '', data.dossiers(
        revision, max_change_time, progress))


def _request_battle_results(context, args):
    store = context.get('postbattle_store')
    if store is None or len(args) != 3:
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_UNAVAILABLE')
    try:
        result = store.result(args[0])
    except Exception as error:
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_PACK_FAILED: %s'
                      % error)
    if result is None:
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_NOT_FOUND')
    return Result(commands.RES_STREAM, '', result)


def _battle_results_received(context, args):
    store = context.get('postbattle_store')
    if store is None or len(args) != 3:
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_UNAVAILABLE')
    try:
        acknowledged = store.acknowledge(args[0])
    except (IOError, OSError, TypeError, ValueError):
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_ACK_FAILED')
    if not acknowledged:
        return Result(commands.RES_FAILURE, 'BATTLE_RESULTS_NOT_FOUND')
    return Result(commands.RES_SUCCESS)


def _set_language(context, args):
    return Result(commands.RES_STREAM, '', args[0] if args else '')


def _contain_queue_listeners(callback):
    # The engine's entity-call boundary logs a failing script and keeps the
    # server alive; Event.__call__ in #1513 re-raises after logging.
    try:
        callback(commands.QUEUE_TYPE_RANDOMS)
    except Exception:
        print('[Offline LAN 0.9.22] a queue-event listener failed:')
        traceback.print_exc()


def _enqueue_random(context, args):
    on_enqueued = context.get('on_enqueued')
    if not callable(on_enqueued):
        return Result(commands.RES_FAILURE, 'QUEUE_EVENTS_UNAVAILABLE')

    def enter_queue():
        _contain_queue_listeners(on_enqueued)

    return Result(commands.RES_SUCCESS, before_response=enter_queue)


def _dequeue_random(context, args):
    on_dequeued = context.get('on_dequeued')
    if not callable(on_dequeued):
        return Result(commands.RES_FAILURE, 'QUEUE_EVENTS_UNAVAILABLE')

    def leave_queue():
        _contain_queue_listeners(on_dequeued)

    return Result(commands.RES_SUCCESS, before_response=leave_queue)


def _add_int_user_settings(context, args):
    account_state = context.get('account_state')
    if account_state is None:
        return Result(commands.RES_FAILURE, 'ACCOUNT_STATE_UNAVAILABLE')
    try:
        account_state.add_int_settings(args[0] if args else ())
    except (IOError, OSError, TypeError, ValueError):
        return Result(commands.RES_FAILURE, 'INVALID_INT_USER_SETTINGS')
    return Result(commands.RES_SUCCESS)


def _del_int_user_settings(context, args):
    account_state = context.get('account_state')
    if account_state is None:
        return Result(commands.RES_FAILURE, 'ACCOUNT_STATE_UNAVAILABLE')
    try:
        account_state.del_int_settings(args[0] if args else ())
    except (IOError, OSError, TypeError, ValueError):
        return Result(commands.RES_FAILURE, 'INVALID_INT_USER_SETTINGS')
    return Result(commands.RES_SUCCESS)


HANDLERS = {
    commands.CMD_SYNC_DATA: _sync_data,
    commands.CMD_EQUIP: _equip_component,
    commands.CMD_EQUIP_OPTDEV: _equip_optional_device,
    commands.CMD_EQUIP_SHELLS: _equip_shells,
    commands.CMD_EQUIP_EQS: _equip_equipments,
    commands.CMD_SET_AND_FILL_LAYOUTS: _set_and_fill_layouts,
    commands.CMD_TMAN_ADD_SKILL: _add_tankman_skill,
    commands.CMD_TMAN_DROP_SKILLS: _drop_tankman_skills,
    commands.CMD_TRAINING_TMAN: _train_tankman,
    commands.CMD_EQUIP_TMAN: _equip_tankman,
    commands.CMD_DISMISS_TMAN: _dismiss_tankman,
    commands.CMD_REPAIR: _repair,
    commands.CMD_TMAN_RESPEC: _retrain_tankman,
    commands.CMD_TMAN_MULTI_RESPEC: _retrain_crew,
    commands.CMD_BUY_TMAN: _buy_tankman,
    commands.CMD_BUY_AND_EQUIP_TMAN: _buy_and_equip_tankman,
    commands.CMD_BUY_ITEM: _buy_item,
    commands.CMD_UNLOCK: _unlock,
    commands.CMD_EXCHANGE: _exchange,
    commands.CMD_FREE_XP_CONV: _convert_free_xp,
    commands.CMD_BUY_SLOT: _buy_slot,
    commands.CMD_BUY_BERTHS: _buy_berths,
    commands.CMD_BUY_VEHICLE: _buy_vehicle,
    commands.CMD_SELL_VEHICLE: _sell_vehicle,
    commands.CMD_SELL_ITEM: _sell_item,
    commands.CMD_BUY_AND_EQUIP_ITEM: _buy_and_equip_item,
    commands.CMD_VEH_SETTINGS: _vehicle_settings,
    commands.CMD_VEH_APPLY_STYLE: _apply_style,
    commands.CMD_SELL_C11N_ITEMS: _sell_customization,
    commands.CMD_BUY_C11N_ITEMS: _buy_customizations,
    commands.CMD_VEH_APPLY_OUTFIT: _apply_outfit,
    commands.CMD_REQ_SERVER_STATS: _server_stats,
    commands.CMD_SYNC_SHOP: _sync_shop,
    commands.CMD_SYNC_DOSSIERS: _sync_dossiers,
    commands.CMD_ENQUEUE_RANDOM: _enqueue_random,
    commands.CMD_DEQUEUE_RANDOM: _dequeue_random,
    commands.CMD_SET_LANGUAGE: _set_language,
    commands.CMD_COMPLETE_TUTORIAL: lambda context, args: Result(commands.RES_SUCCESS),
    commands.CMD_REQ_BATTLE_RESULTS: _request_battle_results,
    commands.CMD_BATTLE_RESULTS_RECEIVED: _battle_results_received,
    commands.CMD_ADD_INT_USER_SETTINGS: _add_int_user_settings,
    commands.CMD_DEL_INT_USER_SETTINGS: _del_int_user_settings,
}


def _payload_shape(args):
    """Describe the argument shape without dumping its contents."""
    parts = []
    for value in args:
        if isinstance(value, (list, tuple)):
            parts.append('%s[%d]' % (type(value).__name__, len(value)))
        else:
            parts.append(type(value).__name__)
    return '(%s)' % ', '.join(parts)


def _log_rejection(command, handler, message, args):
    sys.stdout.write(
        '[Offline LAN 0.9.22] command %d rejected by %s: %s; payload %s\n'
        % (command, handler, message or 'no reason given',
           _payload_shape(args)))


def dispatch(command, context, args):
    command = int(command)
    args = tuple(args or ())
    handler = HANDLERS.get(command)
    if handler is None:
        _log_rejection(command, 'dispatch', 'UNSUPPORTED_OFFLINE_COMMAND', args)
        return Result(commands.RES_FAILURE, 'UNSUPPORTED_OFFLINE_COMMAND')
    result = handler(context, args)
    if result.result_id == commands.RES_FAILURE:
        _log_rejection(
            command, getattr(handler, '__name__', repr(handler)),
            result.error, args)
    return result
