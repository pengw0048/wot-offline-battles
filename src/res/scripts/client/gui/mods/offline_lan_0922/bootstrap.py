from __future__ import print_function

import copy
import os
import sys
import time

import BigWorld

from gui.mods.offline_lan_0922.compat import g_compatibility
from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import instance_guard
from gui.mods.offline_lan_0922 import vehicle_blacklist
from gui.mods.offline_lan_0922 import vehicle_records
from gui.mods.offline_lan_0922.account_rpc import data, economy
from gui.mods.offline_lan_0922.vehicle_records import (
    STOCKED_ITEM_TYPES,
    default_consumables, default_vehicle_settings, offers_in_random_battle,
    vehicle_type_guns, vehicle_type_modules)
from gui.mods.offline_lan_0922.vehicle_configuration import (
    install_top_modules as _install_top_modules,
    is_standard_battle_vehicle as _is_standard_battle_vehicle,
    top_component as _top_component)
from gui.mods.offline_lan_0922.account_rpc.state import AccountState


_callback_id = None
_started = False
_session = None
_announcement_ui = None
_intro_skip = None
_worker_presentation = None
_config = None
_client_mode = None
_account_context = None
_deadline = 0.0
_login_space_seen = False
_lobby_view_loaded = False
_lobby_listener_installed = False
_client_guard_released = False
_worker_ready_signaled = False
_player_ready_signaled = False
_driver_ready_signaled = False

# Enough of every artefact that the garage never blocks a mount on stock.
OFFLINE_ARTEFACT_STOCK = 200
_store = None
_postbattle_store = None










def _schedule(delay, function):
    """Retire the handle before running it; it is no longer cancellable."""
    global _callback_id
    token = [None]

    def deliver():
        global _callback_id
        if _callback_id != token[0]:
            return
        _callback_id = None
        function()

    token[0] = BigWorld.callback(delay, deliver)
    _callback_id = token[0]


def _garage_store():
    """Return the persistent garage store, or None if it cannot be used.

    A saved garage is a convenience.  Losing it must never make the garage
    itself unusable, so every failure here degrades to the stock snapshot.
    """
    global _store
    if _store is None:
        try:
            from gui.mods.offline_lan_0922.account_rpc.garage_store import \
                GarageStore
            _store = GarageStore()
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] the garage state store is unavailable: '
                '%s\n' % error)
            return None
    return _store


def _battle_results_store():
    """Return the one receipt owner shared by LAN and reconstructed Accounts."""
    global _postbattle_store
    if _postbattle_store is None:
        from gui.mods.offline_lan_0922.account_rpc.postbattle_store import \
            PostBattleStore
        _postbattle_store = PostBattleStore()
    return _postbattle_store


def _research_snapshot(snapshot):
    """Copy only the fields that determine native unlock and elite sets."""
    types = set(snapshot.get('vehicleTypeCompactDescrs') or ())
    if not types:
        records = snapshot.get('vehicles') or (snapshot,)
        types.update(record['vehicleTypeCompactDescr'] for record in records
                     if record.get('vehicleTypeCompactDescr') is not None)
    return {'vehicleTypeCompactDescrs': types,
            'unlockItemCompactDescrs': set(snapshot.get('unlockItemCompactDescrs') or ()),
            'shopVehicleOfferCompactDescrs': set(snapshot.get('shopVehicleOfferCompactDescrs') or ()),
            'vehicleXP': dict((key, 0) for key in (snapshot.get('vehicleXP') or {}))}


def _bind_battle_progress(context):
    """Bind durable result receipts to the equally durable garage crew."""
    garage_store = context.get('garage_store')
    bootstrap_snapshot = context.get('selected_vehicle')
    if garage_store is None or not isinstance(bootstrap_snapshot, dict):
        # Hidden workers intentionally own no result or garage store.  If a
        # live client changes mode in-process, retire an older binding without
        # constructing a new persistent result owner for the worker.
        if _postbattle_store is not None:
            binder = getattr(
                _postbattle_store, 'set_progress_applier', None)
            if callable(binder):
                binder(None)
        return False
    postbattle = _battle_results_store()
    binder = getattr(postbattle, 'set_progress_applier', None)
    if not callable(binder):
        # Keeps narrow embedding/test stores source-compatible.  The real
        # PostBattleStore always exposes this transaction boundary.
        return False
    touched = context.setdefault('postbattle_touched_vehicles', set())
    touched_items = context.setdefault('postbattle_touched_items', {})

    def apply(receipt):
        from AccountCommands import VEHICLE_SETTINGS_FLAG
        from items import tankmen, vehicles
        provider = getattr(g_compatibility, 'garage_state', None)
        live_garage = provider() if callable(provider) else None
        if live_garage is not None:
            snapshot_provider = getattr(live_garage, 'snapshot', None)
            snapshot = (snapshot_provider()
                        if callable(snapshot_provider) else None)
        else:
            snapshot = context.get('selected_vehicle')
        if not isinstance(snapshot, dict):
            raise RuntimeError('the live garage snapshot is unavailable')
        descriptor = vehicles.VehicleDescr(typeName=str(receipt['vehicle']))
        nation_id, vehicle_type_id = descriptor.type.id
        vehicle_type_cd = vehicles.makeIntCompactDescrByID(
            'vehicle', nation_id, vehicle_type_id)
        used = list(receipt.get('equipment_used') or ())
        if receipt.get('battle_booster'):
            used.append(int(receipt['battle_booster']))
        research_before = _research_snapshot(snapshot)
        result = garage_store.apply_battle_crew_xp(
            snapshot, receipt['receipt_id'], vehicle_type_cd,
            receipt['rewards']['xp'], VEHICLE_SETTINGS_FLAG.XP_TO_TMAN,
            tankmen_module=tankmen, rewards=receipt['rewards'],
            health=receipt.get('health'), vehicles_module=vehicles,
            shells_fired=receipt.get('shells_fired'),
            equipment_used=used,
            friendly_fire_facts=receipt.get('friendly_fire'),
            vehicle_type_name=receipt['vehicle'],
            battle_start=int(receipt['arena_unique_id']) & 0xffffffff,
            daily_facts=({'damage': receipt['stats']['damage'],
                          'premature_leave': receipt['premature_leave'],
                          'finished_at': (int(receipt['arena_unique_id']) & 0xffffffff) + receipt['duration'],
                          'won': receipt['winner'] == receipt['team']}
                         if receipt.get('battle_mode', 'regular') == 'regular'
                         else None),
            training=receipt.get('battle_mode') == 'training',
            campaign_receipt=receipt,
            auto_settings=(VEHICLE_SETTINGS_FLAG.AUTO_REPAIR,
                           VEHICLE_SETTINGS_FLAG.AUTO_LOAD,
                           VEHICLE_SETTINGS_FLAG.AUTO_EQUIP,
                           VEHICLE_SETTINGS_FLAG.AUTO_EQUIP_BOOSTER))
        context['selected_vehicle'] = snapshot
        research_after = _research_snapshot(snapshot)
        if result.get('applied', True) and research_after != research_before:
            # Mission reward tanks can grant genuinely new research. Derive
            # only those additions; replaying the account's whole elite set
            # makes the native client reopen every historical elite dialog.
            before_stats = data.stats(research_before)['stats']
            after_stats = data.stats(research_after)['stats']
            for name in ('unlocks', 'eliteVehicles'):
                context.setdefault('postbattle_added_' + name, set()).update(
                    set(after_stats[name]) - set(before_stats[name]))
        if result.get('touched_tankmen'):
            from items import ITEM_TYPE_INDICES
            touched_items.setdefault(ITEM_TYPE_INDICES['tankman'], set()).update(
                int(value) for value in result['touched_tankmen'])
        touched.add(int(result['vehicle_id']))
        touched.update(int(vehicle_id) for vehicle_id in result.get('touched_vehicles', ()))
        # The depot changed too: a battle spends rounds and consumables, and
        # the vehicle's own switches may have bought them back.  The client
        # only drops what the diff names.
        for item_type, items in (result.get('touched_items') or {}).items():
            touched_items.setdefault(int(item_type), set()).update(
                int(compact_descr) for compact_descr in items)
        return result

    def capture(receipt_id, vehicle):
        from items import tankmen, vehicles
        provider = getattr(g_compatibility, 'garage_state', None)
        live = provider() if callable(provider) else None
        snapshot = live.snapshot() if live is not None else context.get('selected_vehicle')
        if not isinstance(snapshot, dict):
            raise RuntimeError('participating crew snapshot is unavailable')
        descriptor = vehicles.VehicleDescr(typeName=str(vehicle))
        nation_id, vehicle_id = descriptor.type.id
        compact = vehicles.makeIntCompactDescrByID('vehicle', nation_id, vehicle_id)
        return garage_store.capture_crew_service(
            snapshot, receipt_id, compact, tankmen_module=tankmen)

    binder(apply)
    participant_binder = getattr(postbattle, 'set_participation_applier', None)
    if callable(participant_binder):
        participant_binder(capture)
    return True


def _restore_garage(snapshot):
    store = _garage_store()
    if store is None:
        return False

    def validate(staged):
        from items import vehicles
        _clamp_saved_repair(staged, vehicles)
        return _validate_restored_garage(staged)

    try:
        restored = bool(store.apply(snapshot, validator=validate))
    except Exception as error:
        sys.stdout.write(
            '[Offline LAN 0.9.22] the saved garage could not be restored: '
            '%s\n' % error)
        return False
    if not restored:
        return restored
    # A save written before records carried their vehicle names has to gain
    # them here: the launcher reads that file without a client and cannot
    # resolve a compact descriptor on its own.
    named = set(store.owned_vehicle_names())
    unnamed = sum(
        1 for record in (snapshot.get('vehicles') or ())
        if isinstance(record, dict) and
        str(record.get('vehicleTypeName') or '') not in named)
    degraded = getattr(store, 'restore_degraded', None)
    if unnamed and not (callable(degraded) and degraded()):
        store.mark_dirty()
        if store.flush(snapshot):
            sys.stdout.write(
                '[Offline LAN 0.9.22] named %d saved vehicle(s) for the '
                'launcher\n' % unnamed)
    return restored


def _clamp_saved_repair(snapshot, vehicles):
    """Keep a saved repair state inside what this client's vehicle can be.

    A save is written against one catalogue and restored against another, so
    the health it recorded can exceed the maximum this client's vehicle has.
    That is a stale number, not a damaged save: repairing it is right and
    throwing the whole career away over it is not.
    """
    for record in (snapshot.get('vehicles') or ()):
        if not isinstance(record, dict):
            continue
        value = record.get('repair')
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            continue
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            maximum = int(descriptor.maxHealth)
            bill = max(0, int(value[0]))
            health = int(value[1])
        except Exception:
            continue
        if health >= maximum:
            record['repair'] = (0, maximum)
        else:
            record['repair'] = (bill, health)


def _validate_restored_garage(snapshot):
    """Exercise saved native descriptors before publishing the Account.

    Rebuild installed module rows before ``GarageStore`` reconciles stock and
    validates the relational snapshot. A saved fitting can differ from the
    initial stock descriptor without invalidating the player's whole garage.
    """
    from items import customizations, tankmen, vehicles
    records = snapshot.get('vehicles')
    if not isinstance(records, (list, tuple)):
        records = [snapshot]
    for record in records:
        try:
            _validate_restored_vehicle(
                record, vehicles, tankmen, customizations)
        except data.VehicleRestoreError:
            raise
        except Exception as error:
            # Name the vehicle so the store can contain one refused record
            # instead of discarding the whole save.
            raise data.VehicleRestoreError(
                str(error), record.get('vehicleTypeCompactDescr'))

    # A crew member in the barracks is published too, so a descriptor this
    # client cannot parse has to be caught at the same boundary.
    for compact_descr in (snapshot.get('barracksTankmen') or {}).values():
        tankmen.TankmanDescr(compact_descr)
    return True


def _validate_restored_vehicle(record, vehicles, tankmen, customizations):
    """Exercise one saved vehicle's native descriptors.

    Extracted so a refusal can name the vehicle it refused: the store
    drops that one record and keeps the rest of the career.
    """
    descriptor = vehicles.VehicleDescr(
        compactDescr=record['compDescr'])
    record.setdefault('inventoryItems', {}).update(
        vehicle_records.mounted_module_items(descriptor))
    devices = {}
    for device in descriptor.optionalDevices:
        if device is not None:
            compact_descr = int(device.compactDescr)
            devices[compact_descr] = devices.get(compact_descr, 0) + 1
    record['inventoryItems'][9] = devices
    nation_id, vehicle_type_id = descriptor.type.id
    vehicle_type = vehicles.makeIntCompactDescrByID(
        'vehicle', nation_id, vehicle_type_id)
    if int(record.get('vehicleTypeCompactDescr', 0)) != int(vehicle_type):
        raise ValueError(
            'saved vehicle descriptor does not match its garage type')

    layout_key = (int(descriptor.turret.compactDescr),
                  int(descriptor.gun.compactDescr))
    if tuple(record.get('shellsLayoutIdx') or ()) != layout_key:
        raise ValueError(
            'saved ammunition layout does not match the mounted gun')
    compatible_shells = set()
    for shot in (getattr(descriptor.gun, 'shots', ()) or ()):
        shell = getattr(shot, 'shell', None)
        compact_descr = getattr(shell, 'compactDescr', 0)
        if compact_descr:
            compatible_shells.add(int(compact_descr))
    shells = list(record.get('shells') or ())
    loaded = 0
    for index in range(0, len(shells), 2):
        compact_descr = int(shells[index])
        count = int(shells[index + 1])
        if compact_descr not in compatible_shells or count < 0:
            raise ValueError(
                'saved ammunition does not fit the mounted gun')
        loaded += count
    # An empty rack is a real save: a battle that fired every round leaves
    # one, and the layout still names what to buy back.  Refusing it here
    # would throw the whole career away over an ordinary last shot.
    maximum = int(getattr(descriptor.gun, 'maxAmmo', 0) or 0)
    if maximum > 0 and loaded > maximum:
        raise ValueError('saved ammunition exceeds the gun capacity')

    crew_ids = list(record.get('crew') or ())
    crew = dict(record.get('tankmen') or {})
    roles = tuple(descriptor.type.crewRoles)
    if len(crew_ids) != len(roles):
        raise ValueError('saved crew does not match the vehicle')
    for slot, tankman_id in enumerate(crew_ids):
        if tankman_id is None:
            # An unloaded seat is empty, not damaged: #1513 reads it back
            # as a vehicle whose crew is not full.
            continue
        tankman = tankmen.TankmanDescr(crew[tankman_id])
        if (int(tankman.nationID) != int(nation_id) or
                tankman.role != roles[slot][0]):
            raise ValueError(
                'saved crew member does not match the vehicle slot')

    for outfit_data in dict(record.get('outfits') or {}).values():
        outfit_descr, unused_enabled = outfit_data
        customizations.parseOutfitDescr(outfit_descr)
    return True












# equipments.xml: autoExtinguishers id=1, largeMedkit id=3, largeRepairkit
# id=5.  None carries a <type>, a vehicleFilter or an incompatibleTags section,
# so all three are regular consumables that fit every vehicle.






def _starter_vehicle_types(vehicles, nations, prices):
    """Return the type ids a fresh career owns.

    A retail account can research nothing until it owns a vehicle, and the
    only vehicles no research leads to are the tier-1 starters. #1513 knows
    exactly which those are: ``getUnlocksSources`` inverts every vehicle's
    research list, so a type that appears in no source set is a starter. They
    cost nothing, so owning them from the first launch is the same account a
    new player has a minute after creating one, without an empty garage the
    native Hangar never has to render.
    """
    sources = vehicles.getUnlocksSources()
    starters = []
    for nation_id in range(len(nations.NAMES)):
        for vehicle_type_id in sorted(
                vehicles.g_list.getList(nation_id).keys()):
            compact_descr = vehicles.makeIntCompactDescrByID(
                'vehicle', nation_id, vehicle_type_id)
            if compact_descr in sources:
                continue
            price = prices.get(compact_descr)
            if price is None or price[0] or price[1] or price[2]:
                # Priced or never offered: a premium or reward vehicle, not a
                # starter every account begins with.
                continue
            starters.append((nation_id, vehicle_type_id))
    return starters


def _owned_vehicle_types(vehicles, nations, career, prices,
                         consult_save=True):
    """Return the vehicle type ids this save owns, as a set of id tuples.

    A save's stored garage is what the player owns, in either mode.  What the
    save mode decides is only the seed a save that has never been written
    starts from: every vehicle for the historical sandbox, the free starters
    for a career.  Treating the seed as a standing guarantee instead would
    hand back every vehicle the player had sold on the next start.

    ``consult_save`` is the caller saying whether it may read the save at all.
    The hidden worker owns no garage store and must not open the visible
    client's file, so it builds from the seed.
    """
    store = _garage_store() if consult_save else None
    owned = store.owned_vehicle_types() if store is not None else None
    resolved = set()
    for compact_descr in (owned or ()):
        try:
            vehicle_type = vehicles.getVehicleType(int(compact_descr))
        except Exception:
            # A save can name a vehicle this client no longer ships. Losing
            # that one record is contained; refusing to start the garage is
            # not.
            continue
        resolved.add(tuple(vehicle_type.id))
    if resolved:
        return resolved
    if not career:
        return None
    return set(_starter_vehicle_types(vehicles, nations, prices))


def _deliver_launcher_purchases(snapshot, vehicles, tankmen, settings):
    """Commit launcher-added vehicles and their system notices together."""
    from gui.mods.offline_lan_0922 import launcher_inbox
    from items import ITEM_TYPE_INDICES

    try:
        path = launcher_inbox.inbox_path()
        pending = launcher_inbox.pending_vehicles(path)
    except Exception as error:
        sys.stdout.write(
            '[Offline LAN 0.9.22] launcher purchases could not be read: %s\n'
            % error)
        return 0
    if not pending:
        return 0
    from gui.mods.offline_lan_0922.personal_campaign_ui import queue_notification
    owned = set(
        str(record.get('vehicleTypeName') or '')
        for record in (snapshot.get('vehicles') or ()))
    delivered = []
    unbuilt = []
    working = copy.deepcopy(snapshot)
    for name in pending:
        if name in owned:
            # The launcher refuses to sell a vehicle a save already owns, so
            # this is a duplicate rather than a purchase. The entry is settled
            # either way: a second copy is not what the player bought.
            sys.stdout.write(
                '[Offline LAN 0.9.22] the purchased vehicle %s is already in '
                'this garage\n' % name)
            continue
        # Build on a detached copy, exactly as a restore does. A record this
        # client can build but cannot publish would otherwise be flushed, and
        # the next start discards an unpublishable save whole -- one refused
        # vehicle would cost the player the entire career.
        staged = copy.deepcopy(working)
        try:
            compact_descr = _build_purchased_vehicle(
                staged, vehicles, tankmen, ITEM_TYPE_INDICES, settings, name)
        except Exception as error:
            unbuilt.append(name)
            sys.stdout.write(
                '[Offline LAN 0.9.22] this client cannot build the purchased '
                'vehicle %s, it stays pending: %s\n' % (name, error))
            continue
        try:
            _validate_restored_garage(staged)
            data._validate_selected_vehicle(staged)
        except Exception as error:
            unbuilt.append(name)
            sys.stdout.write(
                '[Offline LAN 0.9.22] the purchased vehicle %s does not make '
                'a publishable garage, it stays pending: %s\n'
                % (name, error))
            continue
        record = next(row for row in staged['vehicles']
                      if row.get('vehicleTypeCompactDescr') == compact_descr)
        rewards = [{'kind': 'vehicle', 'vehicle': name,
                    'vehicle_type': compact_descr}]
        if record.get('tankmen'):
            rewards.append({'kind': 'crew', 'count': len(record['tankmen'])})
        queue_notification(staged, {'account_changes': [
            {'phase': 'granted', 'rewards': rewards}]})
        working = staged
        owned.add(name)
        delivered.append(name)
    if delivered:
        # A delivered vehicle that is never flushed is delivered again on the
        # next start, against an inbox entry that is already gone.
        store = _garage_store()
        if store is None:
            return 0
        store.mark_dirty()
        if not store.flush(working):
            return 0
        snapshot.clear()
        snapshot.update(working)
        for name in delivered:
            sys.stdout.write(
                '[Offline LAN 0.9.22] delivered the purchased vehicle %s\n'
                % name)
    try:
        launcher_inbox.keep_pending(unbuilt, path)
    except Exception as error:
        sys.stdout.write(
            '[Offline LAN 0.9.22] launcher purchases could not be cleared: '
            '%s\n' % error)
    return len(delivered)


def _settle_launcher_campaign(snapshot, vehicles, tankmen):
    """Deliver edited completion and its reward markers in one durable save."""
    if not (snapshot.get('personalMissionProgress') or
            snapshot.get('personalMissionTokens') or
            snapshot.get('personalMissionOrders') or
            snapshot.get('personalMissionPawned') or
            snapshot.get('personalMissionRewardJournal') or
            snapshot.get('personalMissionTokenRewards') or
            snapshot.get('accountBadges') or
            snapshot.get('selectedBadges') or
            'personalMissionRequestedCompleted' in snapshot):
        return
    from gui.mods.offline_lan_0922 import personal_campaign
    from gui.mods.offline_lan_0922.account_rpc.garage import GarageState
    state = GarageState(snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
    result = personal_campaign.settle(state)
    if result.get('reset_error'):
        sys.stdout.write('[Offline LAN 0.9.22] personal mission edit rejected: %s\n'
                         % result['reset_error'])
    for mission_id, error in result['pending']:
        sys.stdout.write('[Offline LAN 0.9.22] personal mission %s reward '
                         'remains pending: %s\n' % (mission_id, error))
    # Keep the message and reward mutation in the same durable commit. The
    # first lobby may not exist yet, so publishing here would lose both the
    # native notification and its retry intent on an Account transition.
    from gui.mods.offline_lan_0922.personal_campaign_ui import queue_notification
    if queue_notification(state.snapshot(), result):
        state.revision += 1
    if not state.revision:
        return
    staged = state.snapshot()
    try:
        _validate_restored_garage(staged)
        data._validate_selected_vehicle(staged)
    except Exception as error:
        sys.stdout.write('[Offline LAN 0.9.22] personal mission rewards '
                         'could not be published: %s\n' % error)
        return
    store = _garage_store()
    if store is None:
        return
    store.mark_dirty()
    if store.flush(staged):
        snapshot.clear()
        snapshot.update(staged)


def _stocked_total(item_type, published, count):
    """Return the account count one more vehicle's stock leaves behind.

    All physical items, including shared module types, add their own copies.
    ``account_rpc.garage`` uses the same rule for purchases and fitting.
    """
    if int(item_type) in STOCKED_ITEM_TYPES:
        return int(published) + int(count)
    return max(int(published), int(count))


def _build_purchased_vehicle(snapshot, vehicles, tankmen, item_type_indices,
                             settings, name):
    """Own one more vehicle, stock, exactly as a shop purchase arrives."""
    descriptor = vehicles.VehicleDescr(typeName=str(name))
    built = vehicle_records.build_record(
        vehicles, tankmen, item_type_indices, tuple(descriptor.type.id),
        _next_inventory_id(snapshot), _next_tankman_id(snapshot), settings,
        [0, 0, 0], top_modules=False, own_researchable_modules=False)
    record = built['record']
    snapshot.setdefault('vehicles', []).append(record)
    compact_descr = int(built['vehicleTypeCompactDescr'])
    published = snapshot.setdefault('vehicleTypeCompactDescrs', set())
    if isinstance(published, set):
        published.add(compact_descr)
    unlocks = snapshot.setdefault('unlockItemCompactDescrs', set())
    if isinstance(unlocks, set):
        unlocks.add(compact_descr)
        unlocks.update(economy.autounlocked_items(vehicles, compact_descr))
    prices = snapshot.setdefault('shopItemPrices', {})
    for item_type, items in record['inventoryItems'].items():
        for item_compact_descr, count in items.items():
            owned_items = snapshot.setdefault(
                'inventoryItems', {}).setdefault(int(item_type), {})
            owned_items[item_compact_descr] = _stocked_total(
                item_type, owned_items.get(item_compact_descr, 0), count)
            prices.setdefault(item_compact_descr, {'credits': 0})
            if isinstance(unlocks, set):
                unlocks.add(int(item_compact_descr))
    prices.setdefault(compact_descr, {'credits': 0})
    snapshot.setdefault('vehicleXP', {}).setdefault(compact_descr, 0)
    return compact_descr


def _next_inventory_id(snapshot):
    used = [int(record.get('id', 0))
            for record in (snapshot.get('vehicles') or ())
            if isinstance(record, dict)]
    return (max(used) if used else 0) + 1


def _next_tankman_id(snapshot):
    used = [100000]
    for record in (snapshot.get('vehicles') or ()):
        if not isinstance(record, dict):
            continue
        for tankman_id in (record.get('tankmen') or ()):
            try:
                used.append(int(tankman_id))
            except (TypeError, ValueError):
                continue
    # A crew member in the barracks still holds their inventory id. Handing it
    # out twice does not break one vehicle, it makes the whole garage
    # unrestorable on the next start.
    for field in ('barracksTankmen', 'recycleBinTankmen'):
        for tankman_id in (snapshot.get(field) or ()):
            try:
                used.append(int(tankman_id))
            except (TypeError, ValueError):
                continue
    # A dismissed campaign reward may have left the recycle bin while its
    # reset receipt still identifies it. Do not let a delivered tank's crew
    # take that identity and become the target of a later reward clawback.
    for key, effect in (snapshot.get('personalMissionRewardJournal') or {}).items():
        if not key.startswith('crew:') or not isinstance(effect, dict):
            continue
        try:
            used.append(int(effect.get('tankman', 0)))
        except (TypeError, ValueError):
            continue
    return max(used) + 1


def _selected_vehicle(config, restore_saved=True):
    try:
        import nations
        from items import ITEM_TYPE_INDICES, tankmen, vehicles
        selected_descriptor = vehicles.VehicleDescr(
            typeName=config['vehicle'])
        selected_type_id = tuple(selected_descriptor.type.id)
        consumables = default_consumables(vehicles)
        save_mode = port_config.save_slot_mode()
        career = save_mode == port_config.SAVE_MODE_NEW_ACCOUNT
        prices = economy.price_index(vehicles, nations)
        shop_item_prices, not_in_shop_items = economy.shop_prices(prices)
        shop_vehicle_offers = economy.retail_gold_vehicle_offers(
            vehicles, nations, prices)
        owned_types = _owned_vehicle_types(
            vehicles, nations, career, prices, consult_save=restore_saved)
        restricted = owned_types is not None

        # The exact #1513 VehicleList exposes one nation-indexed mapping for
        # every nations.NAMES entry.  Put the configured vehicle first so its
        # inventory id remains stable while the rest of the loadable local
        # catalogue is discovered from the client, rather than hard-coded.
        type_ids = []
        if not restricted or selected_type_id in owned_types:
            type_ids.append(selected_type_id)
        for nation_id in range(len(nations.NAMES)):
            for vehicle_type_id in sorted(
                    vehicles.g_list.getList(nation_id).keys()):
                type_id = (nation_id, vehicle_type_id)
                if type_id in type_ids:
                    continue
                if restricted and type_id not in owned_types:
                    continue
                type_ids.append(type_id)

        records = []
        inventory_items = {}
        # shop_item_prices is already the baked catalogue.  Rebinding it here
        # would drop every real price on the floor and publish the whole shop
        # at no cost, which is what the loops below only fill gaps in.
        vehicle_type_compact_descrs = set()
        unlock_item_compact_descrs = set()
        next_tankman_id = 100001
        default_settings = default_vehicle_settings()
        for type_id in type_ids:
            try:
                built = vehicle_records.build_record(
                    vehicles, tankmen, ITEM_TYPE_INDICES, type_id,
                    len(records) + 1, next_tankman_id, default_settings,
                    ([0, 0, 0] if career else consumables),
                    descriptor=(None if career else
                                (selected_descriptor
                                 if type_id == selected_type_id else None)),
                    top_modules=not career,
                    own_researchable_modules=not career)
            except Exception:
                if type_id == selected_type_id:
                    raise
                # Special or incomplete definitions can be advertised by
                # g_list but still fail native garage construction.  Skip a
                # non-selected entry unless its entire relational record is
                # valid; never publish a half-built vehicle.
                continue

            next_tankman_id = built['nextTankmanID']
            record = built['record']
            vehicle_int_compact_descr = built['vehicleTypeCompactDescr']

            for item_type, items in record['inventoryItems'].items():
                published_items = inventory_items.setdefault(item_type, {})
                for compact_descr, count in items.items():
                    published_items[compact_descr] = _stocked_total(
                        item_type, published_items.get(compact_descr, 0),
                        count)
                    # Every published item carries a price whether or not the
                    # baked catalogue knew it, so a baking gap can never make
                    # the snapshot fail its own consistency check.
                    shop_item_prices.setdefault(
                        compact_descr, {'credits': 0})
                    unlock_item_compact_descrs.add(compact_descr)

            published_shells = inventory_items.setdefault(
                ITEM_TYPE_INDICES['shell'], {})
            for compact_descr, count in built['shellCatalog'].items():
                # A career owns exactly the ammunition it loaded; the closure
                # only has to keep an alternate gun's shells priced.
                shop_item_prices.setdefault(compact_descr, {'credits': 0})
                if not career:
                    # A sandbox is handed one alternate load per vehicle, not
                    # one for the whole garage: the loaded rows above already
                    # add up the same way.
                    published_shells[compact_descr] = _stocked_total(
                        ITEM_TYPE_INDICES['shell'],
                        published_shells.get(compact_descr, 0), count)
                    unlock_item_compact_descrs.add(compact_descr)

            vehicle_type_compact_descrs.add(vehicle_int_compact_descr)
            shop_item_prices.setdefault(
                vehicle_int_compact_descr, {'credits': 0})
            unlock_item_compact_descrs.add(vehicle_int_compact_descr)
            unlock_item_compact_descrs.update(
                economy.autounlocked_items(
                    vehicles, vehicle_int_compact_descr))
            # The factory's catalogue can seed sandbox spares, but a vehicle
            # record must only claim the modules its descriptor actually fits.
            record['inventoryItems'].update(
                vehicle_records.mounted_module_items(built['descriptor']))
            records.append(record)

        if not records:
            raise ValueError('client vehicle catalogue is empty')

        # items/__init__ ITEM_TYPE_NAMES: optionalDevice is 9 and equipment is
        # 11.  Neither was published, so the garage showed an empty equipment
        # and optional-device surface no matter what the account owned.
        artefact_counts = {}
        for item_type_name, cache_accessor in (
                ('optionalDevice', vehicles.g_cache.optionalDevices),
                ('equipment', vehicles.g_cache.equipments)):
            item_type = ITEM_TYPE_INDICES[item_type_name]
            published = inventory_items.setdefault(item_type, {})
            offered = 0
            for descriptor in cache_accessor().values():
                try:
                    compact_descr = int(descriptor.compactDescr)
                except (TypeError, ValueError, AttributeError):
                    continue
                if not offers_in_random_battle(descriptor):
                    # The baked prices already contain special-mode items.
                    shop_item_prices.pop(compact_descr, None)
                    not_in_shop_items.add(compact_descr)
                    continue
                shop_item_prices.setdefault(
                    compact_descr, {'credits': 0})
                if career:
                    # A career buys its consumables and optional devices, so
                    # only count what the catalogue offers.
                    offered += 1
                    continue
                published[compact_descr] = max(
                    int(published.get(compact_descr, 0)),
                    OFFLINE_ARTEFACT_STOCK)
                unlock_item_compact_descrs.add(compact_descr)
            artefact_counts[item_type_name] = (
                offered if career else len(published))
            if not artefact_counts[item_type_name]:
                raise ValueError(
                    'client %s catalogue is empty' % item_type_name)

        customization_count = 0
        customization_cache = vehicles.g_cache.customization20()
        for collection_name in (
                'paints', 'camouflages', 'decals', 'modifications', 'styles'):
            collection = getattr(customization_cache, collection_name)
            for item in collection.values():
                shop_item_prices[item.compactDescr] = {
                    # Keep zero-price appearance items credit-denominated.
                    # Money's weighted currency chooser prefers gold when
                    # both zero-valued keys are present.
                    'credits': 0,
                }
                unlock_item_compact_descrs.add(item.compactDescr)
                customization_count += 1
        if customization_count <= 0:
            raise ValueError('client customization catalogue is empty')

        # Preserve the historical selected-vehicle fields for consumers that
        # only need the configured tank.  ``vehicles`` carries the complete
        # garage and account_rpc expands every record into native inventory.
        result = dict(records[0])
        result.update({
            'vehicles': records,
            'inventoryItems': inventory_items,
            'shopItemPrices': shop_item_prices,
            'shopNationCount': len(nations.NAMES),
            'customizationItemCount': customization_count,
            'vehicleTypeCompactDescrs': vehicle_type_compact_descrs,
            'unlockItemCompactDescrs': unlock_item_compact_descrs,
            # The server's offer entitlement is deliberately separate from
            # persisted research.  data.stats merges it only into the native
            # lobby view that #1513's vehicle shop uses for its UNLOCKED
            # criterion; researching and save migration remain honest.
            'shopVehicleOfferCompactDescrs': shop_vehicle_offers,
            'optionalDeviceCount': artefact_counts['optionalDevice'],
            'equipmentCount': artefact_counts['equipment'],
            'notInShopItems': not_in_shop_items,
            'saveMode': save_mode,
            # What this save multiplies its battle earnings by.  It is read
            # once here, beside the mode, because both are the launcher's
            # description of the account rather than anything the garage
            # earns; the hidden worker builds no save and plays at 100.
            'earningsPercent': economy.earnings_percent(
                port_config.save_slot_earnings_percent()
                if restore_saved else economy.DEFAULT_EARNINGS_PERCENT),
            'wallet': (economy.CAREER_WALLET.copy() if career else
                       economy.SANDBOX_WALLET.copy()),
            'vehicleXP': dict(
                (compact_descr, 0)
                for compact_descr in vehicle_type_compact_descrs),
            'accountSlots': (economy.CAREER_GARAGE_SLOTS if career else
                             economy.SANDBOX_GARAGE_SLOTS),
            'accountBerths': (economy.CAREER_BARRACKS_BERTHS if career else
                              economy.SANDBOX_BARRACKS_BERTHS),
            'premiumExpiryTime': 0,
            'personalMissionSelections': {'regular': []},
            'tankmanCosts': (economy.CAREER_TANKMAN_COSTS if career else
                             economy.SANDBOX_TANKMAN_COSTS),
            'deviceRemovalCost': dict(
                economy.CAREER_DEVICE_REMOVAL if career else
                economy.SANDBOX_DEVICE_REMOVAL),
            # The crew shop.  A role change and a passport replacement are
            # priced by this client's own shipped defaults; the recycle bin's
            # window and price are offline policy.
            'crewChangeRoleCost': dict(economy.CHANGE_ROLE_COST),
            'crewPassportCost': dict(economy.PASSPORT_CHANGE_COST),
            'crewFemalePassportCost': dict(economy.FEMALE_PASSPORT_CHANGE_COST),
            'tankmenRestoreConfig': dict(economy.TANKMEN_RESTORE_CONFIG),
            'dropSkillsCosts': dict(
                (index, dict(choice))
                for index, choice in economy.DROP_SKILLS_COSTS.items()),
            # Who the account dismissed and when, as the barracks' own
            # recovery list reads it.
            'recycleBinTankmen': {},
            'nextInventoryID': len(records) + 1,
            'defaultVehicleSettings': default_settings,
        })
        from gui.mods.offline_lan_0922 import offline_services
        offline_services.publish_offers(result, vehicles)
        if restore_saved:
            result['wallet'].update(port_config.save_slot_initial_wallet())
            result.update(port_config.save_slot_initial_personal_progress())
        if not career:
            # A sandbox has researched everything, so its tech tree is elite
            # by the same derived rule a career uses rather than by assertion.
            unlock_item_compact_descrs.update(shop_item_prices)
        # Overlay the saved garage last, so it wins over the stock fitting but
        # never over the current client's catalogue.
        if restore_saved:
            _restore_garage(result)
            _deliver_launcher_purchases(
                result, vehicles, tankmen, default_settings)
            _settle_launcher_campaign(result, vehicles, tankmen)
        return result
    except Exception:
        # _run_once owns startup error reporting.  Returning an empty snapshot
        # here would merely defer a deterministic descriptor problem until a
        # native Hangar consumer crashes with a misleading IndexError.
        raise


def _on_inventory_refreshed():
    notify = getattr(_session, 'on_inventory_refreshed', None)
    if callable(notify):
        notify()


def _on_lobby_view_loaded(event):
    global _lobby_view_loaded
    _lobby_view_loaded = True
    session = _session
    notify = getattr(session, 'on_lobby_view_loaded', None)
    if callable(notify):
        notify()


def _install_lobby_listener():
    global _lobby_listener_installed
    if _lobby_listener_installed:
        return
    from gui.shared import events, g_eventBus
    g_eventBus.addListener(
        events.GUICommonEvent.LOBBY_VIEW_LOADED,
        _on_lobby_view_loaded)
    _lobby_listener_installed = True


def _remove_lobby_listener():
    global _lobby_listener_installed
    if not _lobby_listener_installed:
        return
    from gui.shared import events, g_eventBus
    g_eventBus.removeListener(
        events.GUICommonEvent.LOBBY_VIEW_LOADED,
        _on_lobby_view_loaded)
    _lobby_listener_installed = False


def _cleanup_runtime():
    global _account_context, _callback_id, _client_mode, _config, _deadline
    global _lobby_listener_installed, _lobby_view_loaded
    global _announcement_ui, _intro_skip, _login_space_seen, _session, _started
    global _worker_presentation, _worker_ready_signaled
    global _player_ready_signaled, _driver_ready_signaled
    global _client_guard_released
    errors = []

    callback_id = _callback_id
    if callback_id is not None:
        try:
            BigWorld.cancelCallback(callback_id)
        except Exception as error:
            errors.append(error)
        else:
            if _callback_id == callback_id:
                _callback_id = None

    session = _session
    if session is not None:
        try:
            # Global mod shutdown is followed by compatibility.disconnect().
            # Do not create a fresh Account and start lobby coroutines only to
            # destroy it immediately in the next cleanup stage.
            session.stop(show_login=False, restore_account=False,
                         release_join=True)
        except Exception as error:
            errors.append(error)
        else:
            if _session is session:
                _session = None

    worker_presentation = _worker_presentation
    if worker_presentation is not None:
        try:
            # This cleanup runs only during worker failure or process exit.
            # Keep the second client hidden and silent until Windows tears it
            # down; restoring here produces a visible/audible shutdown flash.
            worker_presentation.deactivate(restore=False)
        except Exception as error:
            errors.append(error)
        else:
            if _worker_presentation is worker_presentation:
                _worker_presentation = None

    intro_skip = _intro_skip
    if intro_skip is not None:
        try:
            intro_skip.uninstall()
        except Exception as error:
            errors.append(error)
        else:
            if _intro_skip is intro_skip:
                _intro_skip = None

    announcement_ui = _announcement_ui
    if announcement_ui is not None:
        try:
            announcement_ui.uninstall()
        except Exception as error:
            errors.append(error)
        else:
            if _announcement_ui is announcement_ui:
                _announcement_ui = None

    try:
        _remove_lobby_listener()
    except Exception as error:
        errors.append(error)

    try:
        from gui.mods.offline_lan_0922 import offline_services_ui
        offline_services_ui.uninstall()
    except Exception as error:
        errors.append(error)

    try:
        from gui.mods.offline_lan_0922 import engine_audio
        engine_audio.uninstall()
    except Exception as error:
        errors.append(error)

    try:
        g_compatibility.fini()
    except Exception as error:
        errors.append(error)

    _account_context = None
    _client_mode = None
    _config = None
    _deadline = 0.0
    _login_space_seen = False
    _lobby_view_loaded = False
    _client_guard_released = False
    _worker_ready_signaled = False
    _player_ready_signaled = False
    _driver_ready_signaled = False
    _started = False
    if errors:
        return errors[0]
    return None


def _fail_startup(error, prefix='startup failed', worker_process=False):
    worker_process = bool(
        worker_process or
        _worker_presentation is not None or
        _client_mode in (port_config.SIMULATION_WORKER_MODE,
                         port_config.PLAYER_DRIVER_MODE))
    cleanup_error = _cleanup_runtime()
    if cleanup_error is None:
        sys.stdout.write('[Offline LAN 0.9.22] %s: %s\n' %
                         (prefix, error))
    else:
        sys.stdout.write(
            '[Offline LAN 0.9.22] %s: %s; cleanup failed: %s\n' %
            (prefix, error, cleanup_error))
    if worker_process:
        try:
            BigWorld.quit()
        except Exception as quit_error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] simulation worker exit failed: %s\n' %
                quit_error)


def _lobby_is_ready(app_loader, lobby):
    if lobby is None or not _lobby_view_loaded:
        return False
    # In build #1513 SFApplication exists before its Scaleform managers and
    # cursor are initialized.  Starting the LAN picker in that interval queues
    # a view which cannot be synchronously closed if the connection then fails.
    if not bool(getattr(lobby, 'initialized', True)):
        return False

    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID
    if app_loader.getSpaceID() != GUI_GLOBAL_SPACE_ID.LOBBY:
        return False

    from gui.shared.utils.HangarSpace import g_hangarSpace
    if not (g_hangarSpace.inited and g_hangarSpace.spaceInited):
        return False

    from CurrentVehicle import g_currentVehicle
    if g_currentVehicle.isPresent():
        vehicle = g_hangarSpace.getVehicleEntity()
        if vehicle is None or getattr(vehicle, 'model', None) is None:
            return False
    return True


def _login_space_is_ready():
    """Whether LoginState has finished entering its exact GUI space."""
    from gui.app_loader import g_appLoader
    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID

    if g_appLoader.getSpaceID() != GUI_GLOBAL_SPACE_ID.LOGIN:
        return False
    lobby = g_appLoader.getDefLobbyApp()
    return (lobby is not None and
            bool(getattr(lobby, 'initialized', True)))


def _native_lobby_is_ready():
    from gui.app_loader import g_appLoader
    return _lobby_is_ready(g_appLoader, g_appLoader.getDefLobbyApp())


def _install_lan_session():
    """Install the Battle callback before Scaleform binds LobbyHeader."""
    global _session
    if _session is not None:
        return True
    from gui.mods.offline_lan_0922.lan_session import LANSession
    from gui.mods.offline_lan_0922 import engine_audio, runtime_diagnostics
    engine_audio.install()
    runtime_diagnostics.install_exit_trace()
    session = LANSession(
        _config, lobby_ready=_native_lobby_is_ready,
        callback=BigWorld.callback,
        cancel_callback=BigWorld.cancelCallback,
        postbattle_store=_battle_results_store())
    try:
        if not session.install():
            raise RuntimeError('LAN Battle button did not install')
    except Exception:
        session.stop(show_login=False, restore_account=False,
                     release_join=True)
        raise
    _session = session
    return True


def _install_worker_session():
    """Install the opt-in simulation worker without any lobby controls."""
    global _session
    if _session is not None:
        return True
    from gui.mods.offline_lan_0922.authority_worker import WorkerSession
    _session = WorkerSession(
        _config, lobby_ready=_native_lobby_is_ready,
        callback=BigWorld.callback,
        cancel_callback=BigWorld.cancelCallback,
        bigworld=BigWorld)
    return True


def _install_driver_session():
    """Install one local player driver without a room connection or UI."""
    global _session
    if _session is not None:
        return True
    from gui.mods.offline_lan_0922.driver_session import DriverSession
    _session = DriverSession(
        _config, lobby_ready=_native_lobby_is_ready,
        callback=BigWorld.callback,
        cancel_callback=BigWorld.cancelCallback,
        bigworld=BigWorld)
    return True


def _install_worker_presentation():
    """Hide and mute an explicitly launched native companion."""
    global _worker_presentation
    if _worker_presentation is not None:
        return True
    from gui.mods.offline_lan_0922.worker_presentation import \
        WorkerPresentation
    presentation = WorkerPresentation()
    _worker_presentation = presentation
    try:
        if not presentation.activate():
            raise RuntimeError(
                'simulation worker presentation did not start')
    except Exception:
        # Activation may have muted WWISE or hidden a window before the final
        # step failed. Never undo those safeguards while the worker exits.
        try:
            presentation.deactivate(restore=False)
        except Exception as cleanup_error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] worker presentation cleanup failed: '
                '%s\n' % cleanup_error)
        _worker_presentation = None
        raise
    return True


def _signal_worker_ready():
    """Publish readiness after the worker Hangar and LAN welcome succeed."""
    from gui.mods.offline_lan_0922.worker_presentation import \
        signal_worker_ready
    return signal_worker_ready()


def _signal_player_ready():
    """Publish readiness after the visible client's Hangar is stable."""
    from gui.mods.offline_lan_0922.worker_presentation import \
        signal_player_ready
    return signal_player_ready()


def _signal_driver_ready():
    """Publish the driver Hangar and listener boundary to its own starter."""
    from gui.mods.offline_lan_0922.worker_presentation import \
        _signal_ready_marker
    return _signal_ready_marker(
        port_config.DRIVER_READY_MARKER_ENV, 'player driver')


def _install_intro_skip():
    """Skip the startup video so the client reaches the login screen."""
    global _intro_skip
    if _intro_skip is not None:
        return _intro_skip
    try:
        from gui.mods.offline_lan_0922.lobby_ui import IntroVideoSkip
        intro_skip = IntroVideoSkip()
        intro_skip.install()
    except Exception as error:
        # The startup video is presentation only. Never let it stop startup.
        sys.stdout.write(
            '[Offline LAN 0.9.22] the startup video stays: %s\n' % error)
        return None
    _intro_skip = intro_skip
    return intro_skip


def _install_announcement_ui():
    """Own only the stock CN automatic server-announcement window."""
    global _announcement_ui
    if _announcement_ui is not None:
        return True
    from gui.mods.offline_lan_0922.lobby_ui import ServerAnnouncementUI
    announcement_ui = ServerAnnouncementUI()
    announcement_ui.install()
    _announcement_ui = announcement_ui
    return True


def _wait_for_login_space():
    """Create the client-only Account one tick after stable LoginState."""
    global _callback_id, _login_space_seen
    _callback_id = None
    try:
        if not _login_space_is_ready():
            _login_space_seen = False
            _schedule(0.10, _wait_for_login_space)
            return
        if not _login_space_seen:
            # LoginState.init() clears every client-only entity and space.
            # Recheck on the next engine tick so that cleanup always precedes
            # creation of our Account, including after the startup video.
            _login_space_seen = True
            _schedule(0.0, _wait_for_login_space)
            return
        _login_space_seen = False
        try:
            # #1513 keys its vehicle dossier cache file on the account name,
            # and PlayerAccount.__init__ builds that cache before any account
            # hook of ours runs. Scope it here, once, for each process role.
            from gui.mods.offline_lan_0922 import compat as _compat
            _compat.pin_dossier_cache(_dossier_cache_career)
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] the vehicle dossier cache was not '
                'scoped to this career: %s\n' % error)
        if _client_mode == port_config.SIMULATION_WORKER_MODE:
            # The worker needs a native lobby only as a safe map-lifecycle
            # bridge. It owns no Battle button, announcement or preference
            # profile and therefore cannot mutate the player's UI settings.
            _install_worker_session()
        elif _client_mode == port_config.PLAYER_DRIVER_MODE:
            _install_driver_session()
        else:
            # LobbyHeaderMeta stores a bound ``fightClick`` Function when its
            # Scaleform movie receives ``script = self``.  A class patch
            # installed after HANGAR_READY can repaint the button but cannot
            # replace that cached callback. Own it before lobby creation.
            _install_announcement_ui()
            _install_lan_session()
            from gui.mods.offline_lan_0922 import offline_services_ui
            offline_services_ui.install()
            try:
                # This must outlive every connect/disconnect: it decides which
                # preferences profile the player's interface settings use.
                from gui.mods.offline_lan_0922 import compat as _compat
                _compat.pin_account_settings()
            except Exception as error:
                sys.stdout.write(
                    '[Offline LAN 0.9.22] interface settings were not pinned: '
                    '%s\n' % error)
        g_compatibility.connect(
            show_lobby=True, account_context=_account_context)
        _schedule(0.10, _wait_for_lobby)
    except Exception as error:
        _fail_startup(error)


def _dossier_cache_career():
    """Return the identity that owns this process's vehicle dossier cache.

    #1513 caches vehicle dossiers per account and asks the server only for
    rows newer than the highest ``changeTime`` it already holds.  Offline
    every save slot logs in under the same account name, and the hidden
    companions log in beside the visible client, so all would share one
    file and one watermark.  The save slot names the career, the role
    separates the processes, and the post-battle store's account key
    separates a career that was recreated in the same slot from the one whose
    rows the cache still holds.  ``PostBattleStore`` mints a fresh key until a
    battle persists one, which is correct: there is no record to cache yet.
    """
    role = ('worker' if _client_mode == port_config.SIMULATION_WORKER_MODE
            else 'driver' if _client_mode == port_config.PLAYER_DRIVER_MODE
            else 'player')
    account_key = ''
    if _postbattle_store is not None:
        account_key = str(
            getattr(_postbattle_store, 'account_key', '') or '')
    return '%s.%s.%s' % (port_config.active_save_slot(), role, account_key)


_replay_autostart_requested = False


def _autostart_selected_replay():
    # A separate callback lets the native HANGAR_READY stack unwind first.
    # Do not call again on returning from playback or start a live LAN room.
    try:
        from gui.mods.offline_lan_0922 import offline_replay
        if (not offline_replay.replay_request() or _session is None or
                _client_mode in (port_config.SIMULATION_WORKER_MODE,
                                 port_config.PLAYER_DRIVER_MODE)):
            return
        sys.stdout.write('[Offline LAN 0.9.22] REPLAY_ENTRY auto_join\n')
        _session.join()
    except Exception as error:
        sys.stdout.write('[Offline LAN 0.9.22] REPLAY_ENTRY failed: %s\n' % error)
        _fail_startup(error)


def _wait_for_lobby():
    global _callback_id, _deadline, _player_ready_signaled
    global _driver_ready_signaled
    global _replay_autostart_requested
    _callback_id = None
    try:
        if _lobby_view_loaded and _deadline <= 0.0:
            _deadline = time.time() + float(
                _config.get('startupTimeoutSeconds', 30.0))
        from gui.app_loader import g_appLoader
        lobby = g_appLoader.getDefLobbyApp()
        if (g_compatibility.is_ready() and
                _lobby_is_ready(g_appLoader, lobby)):
            if _session is None:
                raise RuntimeError('LAN session is not installed')
            if _client_mode == port_config.SIMULATION_WORKER_MODE:
                if not _session.start():
                    raise RuntimeError('simulation worker did not start')
                # WorkerSession.start only launches its socket thread. Do not
                # release the player until the worker receives a valid server
                # welcome and publishes connected+ready on the main thread.
                _deadline = time.time() + float(
                    _config.get('startupTimeoutSeconds', 30.0))
                _schedule(0.10, _wait_for_worker_connection)
            elif _client_mode == port_config.PLAYER_DRIVER_MODE:
                if not _session.start():
                    raise RuntimeError('player driver did not start')
                # The launcher starts the visible process only after this
                # marker. Waiting for a paired connection here would deadlock.
                if not bool(getattr(_session, 'listening', False)):
                    raise RuntimeError('player driver listener is unavailable')
                if not _driver_ready_signaled:
                    if not _signal_driver_ready():
                        raise RuntimeError(
                            'player driver ready marker was not published')
                    _driver_ready_signaled = True
                _deadline = 0.0
            else:
                if not _player_ready_signaled:
                    if not _signal_player_ready():
                        raise RuntimeError(
                            'visible player ready marker was not published')
                    _player_ready_signaled = True
                from gui.mods.offline_lan_0922 import offline_replay
                if (offline_replay.replay_request() and
                        os.environ.get('WOT_OFFLINE_REPLAY_AUTOSTART') == '1'):
                    if not _replay_autostart_requested:
                        _replay_autostart_requested = True
                        _schedule(0.10, _autostart_selected_replay)
                else:
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] lobby ready; click Battle to join '
                        '%s:%s\n' % (
                            _config.get('host', '127.0.0.1'),
                            _config.get('port', 28782)))
            return
        # EULA and other first-run screens require user interaction and must
        # not consume the hangar-startup timeout.  The deadline begins when
        # the native lobby view reports that it has loaded.
        if (_lobby_view_loaded and _deadline > 0.0 and
                time.time() >= _deadline):
            raise RuntimeError('offline lobby loading timed out')
        _schedule(0.10, _wait_for_lobby)
    except Exception as error:
        _fail_startup(error)


def _wait_for_worker_connection():
    global _callback_id, _deadline, _worker_ready_signaled
    _callback_id = None
    try:
        if _worker_ready_signaled:
            return
        if _session is None:
            raise RuntimeError('simulation worker session is unavailable')
        client = getattr(_session, 'client', None)
        if (client is not None and
                bool(getattr(client, 'connected', False)) and
                bool(getattr(client, 'ready', False))):
            sys.stdout.write(
                '[Offline LAN 0.9.22] simulation worker connected to '
                '%s:%s\n' % (
                    _config.get('host', '127.0.0.1'),
                    _config.get('port', 28782)))
            if not _signal_worker_ready():
                raise RuntimeError(
                    'simulation worker ready marker was not published')
            # The marker is the final fallible startup operation. Once it is
            # visible, the waiting player helper may launch immediately.
            _worker_ready_signaled = True
            _deadline = 0.0
            return
        if (getattr(_session, 'state', None) == 'failed' or
                (_deadline > 0.0 and time.time() >= _deadline)):
            raise RuntimeError('simulation worker connection timed out')
        _schedule(0.10, _wait_for_worker_connection)
    except Exception as error:
        _fail_startup(error)


def _worker_account_state():
    """Seed the hidden worker's EULA setting from this exact client."""
    from constants import USER_SERVER_SETTINGS
    from gui.doc_loaders.EULAVersionLoader import EULAVersionLoader

    setting_key = int(USER_SERVER_SETTINGS.EULA_VERSION)
    version = int(EULAVersionLoader().xmlVersion)
    if version < 0:
        raise RuntimeError('invalid client EULA version')
    account_state = AccountState(path=None)
    account_state.add_int_settings((setting_key, version))
    return account_state


def _run_once():
    global _account_context, _callback_id, _client_mode, _config, _deadline
    _callback_id = None
    try:
        _config = port_config.load()
        if not _config['enabled']:
            _cleanup_runtime()
            sys.stdout.write('[Offline LAN 0.9.22] disabled by config\n')
            return
        _client_mode = port_config.client_mode(_config)
        if _client_mode in (port_config.SIMULATION_WORKER_MODE,
                            port_config.PLAYER_DRIVER_MODE):
            if not _client_guard_released:
                raise RuntimeError(
                    'hidden native client requires client guard release')
            # Each companion needs Account/Hangar to enter and leave native
            # spaces safely. Every mutable store is in memory and the saved
            # player garage is neither read nor overlaid.
            _account_context = {
                'selected_vehicle': _selected_vehicle(
                    _config, restore_saved=False),
                'account_state': _worker_account_state(),
            }
        else:
            account_state = AccountState()
            account_state.postbattle_store = _battle_results_store()
            _account_context = {
                'selected_vehicle': _selected_vehicle(_config),
                'garage_store': _garage_store(),
                'on_inventory_refreshed': _on_inventory_refreshed,
                # Account settings are server-owned in #1513. Keep their
                # local offline substitute beside config across restarts.
                'account_state': account_state,
            }
        _bind_battle_progress(_account_context)
        _deadline = 0.0
        _wait_for_login_space()
    except Exception as error:
        _fail_startup(error)


def _log_session_identity(requested_mode):
    role = ('hidden-worker' if
            requested_mode == port_config.SIMULATION_WORKER_MODE else
            'hidden-player-driver' if
            requested_mode == port_config.PLAYER_DRIVER_MODE else
            'visible-client')
    identity = {
        'semanticVersion': 'unknown',
        'buildIdentity': 'unknown',
        'launcherSemanticVersion': 'unknown',
        'launcherBuildIdentity': 'unknown',
    }
    try:
        provider = getattr(port_config, 'session_identity', None)
        if callable(provider):
            loaded = provider()
            if isinstance(loaded, dict):
                identity.update(loaded)
    except Exception:
        pass
    sys.stdout.write(
        '[Offline LAN 0.9.22] session version=%s build=%s role=%s '
        'launcher_version=%s launcher_build=%s\n' % (
            identity['semanticVersion'], identity['buildIdentity'], role,
            identity['launcherSemanticVersion'],
            identity['launcherBuildIdentity']))


def init():
    global _callback_id, _client_guard_released, _started
    if _started:
        return
    _started = True
    requested_mode = os.environ.get(port_config.CLIENT_MODE_ENV, '')
    try:
        requested_mode = requested_mode.strip()
    except AttributeError:
        requested_mode = ''
    _log_session_identity(requested_mode)
    guard_error = None
    try:
        # Complete #1513's native WGC teardown before scheduling callbacks;
        # the Python callback thread does not own WGC's client mutex.
        _client_guard_released = instance_guard.release_if_requested()
    except Exception as error:
        guard_error = error
        _client_guard_released = False

    if _client_guard_released:
        sys.stdout.write(
            '[Offline LAN 0.9.22] released wot_client_mutex for another '
            'offline client\n')
    elif guard_error is not None:
        sys.stdout.write(
            '[Offline LAN 0.9.22] client guard release failed: %s\n' %
            guard_error)

    try:
        # Install this before every worker refusal path. A fresh preferences
        # leaf otherwise selects #1513's compulsory, unskippable intro movie.
        _install_intro_skip()
        if requested_mode in (port_config.SIMULATION_WORKER_MODE,
                              port_config.PLAYER_DRIVER_MODE):
            if not _client_guard_released:
                _started = False
                sys.stdout.write(
                    '[Offline LAN 0.9.22] hidden native client startup refused: '
                    'WGC client guard teardown did not complete\n')
                try:
                    BigWorld.quit()
                except Exception as quit_error:
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] simulation worker exit failed: '
                        '%s\n' % quit_error)
                return
            _install_worker_presentation()
        _install_lobby_listener()
        _schedule(0.0, _run_once)
    except Exception as error:
        _fail_startup(
            error, prefix='startup callback failed',
            worker_process=(
                requested_mode in (port_config.SIMULATION_WORKER_MODE,
                                   port_config.PLAYER_DRIVER_MODE)))


def fini():
    cleanup_error = _cleanup_runtime()
    if cleanup_error is not None:
        sys.stdout.write(
            '[Offline LAN 0.9.22] shutdown failed: %s\n' % cleanup_error)
