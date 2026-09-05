"""Small #1513 account snapshots consumed by the native account helpers.

The numeric item indices come from the target client's exact
``scripts/common/items/__init__.pyc``.  Keep this module engine-free so the
wire shapes can be tested without importing BigWorld.
"""


VEHICLE_ITEM_TYPE = 1
TANKMAN_ITEM_TYPE = 8
OPTIONAL_DEVICE_ITEM_TYPE = 9
SHELL_ITEM_TYPE = 10
EQUIPMENT_ITEM_TYPE = 11
CUSTOMIZATION_ITEM_TYPE = 12
# account_helpers.CustomizationInvData in the exact #1513 client.
CUSTOMIZATION_ITEMS = 1
CUSTOMIZATION_OUTFITS = 2
CUSTOMIZATION_UNLOCKS = 3
ITEM_TYPE_INDICES = tuple(range(1, 13))
REQUIRED_VEHICLE_COMPONENT_TYPES = (2, 3, 4, 5, 6, 7)
# Account-wide artefacts: owned once and mountable on any vehicle.
ARTEFACT_ITEM_TYPES = (OPTIONAL_DEVICE_ITEM_TYPE, EQUIPMENT_ITEM_TYPE)
# items/vehicles.py uses this literal for IS_CLIENT while reading the
# vehicle list, so a sale returns half of what an item cost.
OFFLINE_SELL_PRICE_FACTOR = 0.5
# Offline policy, not a #1513 value. See shop().
OFFLINE_GOLD_EXCHANGE_RATE = 400
OFFLINE_CREDITS = 100000000
OFFLINE_GOLD = 1000000
OFFLINE_FREE_XP = 100000000
OFFLINE_GARAGE_SLOTS = 2000
OFFLINE_BARRACKS_BERTHS = 2000
# #1513's InventoryRequester defaults a tankman's missing vehicle key to -1,
# and ItemsRequester treats anything not above zero as "not in a tank".
BARRACKS_VEHICLE_ID = -1
# #1513 counts each battle-hero medal in the vehicle dossier's aggregate
# ``battleHeroes`` record as well as in its own counter.
BATTLE_HERO_ACHIEVEMENTS = frozenset((
    'warrior', 'invader', 'sniper', 'sniper2', 'mainGun', 'defender',
    'steelwall', 'supporter', 'scout', 'evileye'))


def _vehicle_records(vehicle):
    records = vehicle.get('vehicles')
    if records is None:
        return [vehicle] if vehicle.get('compDescr') else []
    if not isinstance(records, (tuple, list)) or not records:
        raise ValueError('vehicles must be a non-empty sequence')
    if any(not isinstance(record, dict) for record in records):
        raise ValueError('vehicle records must be mappings')
    return list(records)


def _validate_selected_vehicle(vehicle):
    """Reject incomplete garage snapshots before native requesters consume them."""
    records = _vehicle_records(vehicle)
    if not records:
        return

    item_prices = dict(vehicle.get('shopItemPrices', {}))
    vehicle_ids = []
    tankman_ids = []
    comp_descrs = []
    vehicle_type_compact_descrs = []
    installed_item_compact_descrs = set()
    for record in records:
        if not record.get('compDescr'):
            raise ValueError('vehicle compact descriptor must be non-empty')
        vehicle_id = int(record.get('id', 0))
        if vehicle_id <= 0:
            raise ValueError('vehicle inventory ids must be positive')
        vehicle_ids.append(vehicle_id)
        comp_descrs.append(record['compDescr'])
        crew = list(record.get('crew', ()))
        tankmen = dict(record.get('tankmen', {}))
        # #1513's ``Vehicle._buildCrew`` walks the crew list slot by slot and
        # reads ``None`` as an empty seat, so an unloaded crew member leaves a
        # hole rather than shortening the list.
        manned = [tankman_id for tankman_id in crew if tankman_id is not None]
        tankman_ids.extend(manned)
        vehicle_type_compact_descr = record.get(
            'vehicleTypeCompactDescr')
        if vehicle_type_compact_descr is not None:
            vehicle_type_compact_descrs.append(vehicle_type_compact_descr)

        if not crew:
            raise ValueError('selected vehicle crew must have one seat per role')
        try:
            crew_ids_are_positive = all(
                int(tankman_id) > 0 for tankman_id in manned)
        except (TypeError, ValueError):
            crew_ids_are_positive = False
        if not crew_ids_are_positive:
            raise ValueError('selected vehicle crew ids must be positive')
        if len(manned) != len(tankmen) or set(manned) != set(tankmen):
            raise ValueError(
                'selected vehicle crew ids must resolve to tankmen')

        for key in ('repair', 'lock'):
            value = record.get(key)
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                raise ValueError(
                    'selected vehicle %s must contain two values' % key)
        if record['repair'][1] <= 0:
            raise ValueError('selected vehicle health must be positive')
        for key in ('eqs', 'eqsLayout'):
            value = record.get(key)
            if not isinstance(value, (tuple, list)) or len(value) != 3:
                raise ValueError(
                    'selected vehicle %s must contain three slots' % key)

        shells = record.get('shells')
        if (not isinstance(shells, (tuple, list)) or not shells or
                len(shells) % 2):
            raise ValueError(
                'selected vehicle shells must contain descriptor/count pairs')
        if not isinstance(record.get('shellsLayout'), dict):
            raise ValueError('selected vehicle shellsLayout must be a mapping')

        inventory_items = dict(record.get('inventoryItems', {}))
        for item_type in REQUIRED_VEHICLE_COMPONENT_TYPES + (10,):
            items = inventory_items.get(item_type)
            if not isinstance(items, dict) or not items:
                raise ValueError(
                    'selected vehicle item type %d must be non-empty' %
                    item_type)

        required_prices = set()
        for item_type in REQUIRED_VEHICLE_COMPONENT_TYPES + (10,):
            required_prices.update(inventory_items[item_type])
        installed_item_compact_descrs.update(required_prices)
        if not required_prices.issubset(set(item_prices)):
            raise ValueError(
                'selected vehicle modules and shells must have shop prices')
        shell_pairs = dict(
            (shells[index], shells[index + 1])
            for index in range(0, len(shells), 2))
        if shell_pairs != inventory_items[10]:
            raise ValueError(
                'selected vehicle shell layout and inventory must match')

    if len(set(vehicle_ids)) != len(vehicle_ids):
        raise ValueError('vehicle inventory ids must be unique')
    if len(set(comp_descrs)) != len(comp_descrs):
        raise ValueError('vehicle compact descriptors must be unique')
    barracks = vehicle.get('barracksTankmen')
    if barracks is not None:
        if not isinstance(barracks, dict):
            raise ValueError('barracks tankmen must be a mapping')
        for tankman_id, compact_descr in barracks.items():
            try:
                positive = int(tankman_id) > 0
            except (TypeError, ValueError):
                positive = False
            if not positive:
                raise ValueError('barracks tankman ids must be positive')
            if not compact_descr:
                raise ValueError(
                    'barracks tankman compact descriptors must be non-empty')
        berths = int(vehicle.get('accountBerths', OFFLINE_BARRACKS_BERTHS) or 0)
        if len(barracks) > berths:
            raise ValueError('the barracks holds more tankmen than it has berths')
        # A crew member is in exactly one place. The client decides which from
        # the published ``vehicle`` foreign key, so two claims on one id would
        # make the garage disagree with itself.
        tankman_ids.extend(barracks)
    if len(set(tankman_ids)) != len(tankman_ids):
        raise ValueError('tankman inventory ids must be unique')
    if (vehicle_type_compact_descrs and
            len(set(vehicle_type_compact_descrs)) != len(records)):
        raise ValueError('vehicle type compact descriptors must be unique')

    published_vehicle_types = set(
        vehicle.get('vehicleTypeCompactDescrs', ()))
    if (published_vehicle_types and
            published_vehicle_types != set(vehicle_type_compact_descrs)):
        raise ValueError(
            'vehicle type compact descriptor catalogue must match garage')

    unlocks = set(vehicle.get('unlockItemCompactDescrs', ()))
    required_unlocks = (set(vehicle_type_compact_descrs) |
                        installed_item_compact_descrs)
    if unlocks and not required_unlocks.issubset(unlocks):
        raise ValueError(
            'every garage vehicle, module and shell must be unlocked')
    if not set(vehicle_type_compact_descrs).issubset(set(item_prices)):
        raise ValueError('every garage vehicle type must have a shop price')

    inventory_items = dict(vehicle.get('inventoryItems', {}))
    if 'vehicles' in vehicle:
        for record in records:
            for item_type, items in record['inventoryItems'].items():
                published = inventory_items.get(item_type, {})
                if any(int(published.get(compact_descr, 0)) < int(count)
                       for compact_descr, count in items.items()):
                    raise ValueError(
                        'garage inventory must contain every installed item')

    for compact_descr, price in item_prices.items():
        if isinstance(price, dict):
            currencies = set(price)
            if (not currencies or
                    not currencies.issubset(
                        set(('credits', 'gold', 'crystal')))):
                raise ValueError(
                    'shop price %r must contain valid currencies' %
                    compact_descr)
        elif not isinstance(price, tuple) or len(price) < 2:
            raise ValueError(
                'shop price %r must be a currency mapping or tuple' %
                compact_descr)

    if int(vehicle.get('shopNationCount', 0)) <= 0:
        raise ValueError('selected vehicle shop nation count must be positive')
    if int(vehicle.get('customizationItemCount', 0)) <= 0:
        raise ValueError(
            'selected vehicle customization catalogue must be non-empty')


def inventory(selected_vehicle=None, validate=True, only_vehicles=None,
              only_items=None, touched_tankmen=None):
    """Return every loadable garage vehicle and its relational records.

    ``selected_vehicle`` is serialized by ``bootstrap._selected_vehicle``.  It
    carries engine-derived compact descriptors, while this low-level module
    stays importable without BigWorld or the item definition cache.

    ``validate`` walks every record, so a fitting that already went through
    ``GarageState`` publishes without paying for it again.

    ``only_vehicles`` limits the per-vehicle rows to the ids a fitting touched,
    ``touched_tankmen`` names the crew ids whose place changed, and
    ``only_items`` maps an item type to the owned compact descriptors it
    changed.  Either one selects a delta: ``Inventory.synchronize`` merges the
    diff per item type through ``synchronizeDicts``, and an omitted type keeps
    its cache untouched.  This matters because ``ItemsRequester.invalidateCache``
    evicts one GUI item per published compact descriptor, so a full catalogue
    would rebuild the whole garage on every click.
    """
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    if validate:
        _validate_selected_vehicle(vehicle)
    delta = only_vehicles is not None or only_items is not None
    records = _vehicle_records(vehicle)
    values = dict((item_type, {}) for item_type in ITEM_TYPE_INDICES)
    vehicle_values = {
        'repair': {}, 'lastCrew': {}, 'crew': {}, 'settings': {},
        'compDescr': {}, 'eqs': {}, 'eqsLayout': {}, 'shells': {},
        'shellsLayout': {}, 'lock': {},
    }
    all_tankmen = {}
    tankman_vehicles = {}
    customization_outfits = {}
    for record in records:
        vehicle_id = int(record.get('id', 1))
        if only_vehicles is not None and vehicle_id not in only_vehicles:
            continue
        crew = list(record.get('crew', ()))
        tankmen = dict(record.get('tankmen', {}))
        vehicle_values['crew'][vehicle_id] = crew
        # #1513's InventoryRequester defaults a missing repair entry to the
        # integer 0, while the GUI Vehicle constructor unpacks a two-tuple.
        vehicle_values['repair'][vehicle_id] = tuple(
            record.get('repair', (0, 0)))
        try:
            vehicle_values['settings'][vehicle_id] = int(
                record.get('settings', 0) or 0)
        except (TypeError, ValueError):
            vehicle_values['settings'][vehicle_id] = 0
        vehicle_values['compDescr'][vehicle_id] = record['compDescr']
        vehicle_values['eqs'][vehicle_id] = list(
            record.get('eqs', (0, 0, 0)))
        vehicle_values['eqsLayout'][vehicle_id] = list(
            record.get('eqsLayout', (0, 0, 0)))
        vehicle_values['shells'][vehicle_id] = list(
            record.get('shells', ()))
        # GUI Vehicle calls .get() on this value before parsing the layout.
        vehicle_values['shellsLayout'][vehicle_id] = dict(
            record.get('shellsLayout', {}))
        # GUI Vehicle.isLocked indexes both positions without normalizing the
        # InventoryRequester default (which is the integer zero).
        vehicle_values['lock'][vehicle_id] = tuple(
            record.get('lock', (0, 0)))
        # A missing lastCrew record means that no historical crew is stored.
        # An empty per-vehicle list is not equivalent in #1513: the crew
        # operations popover treats presence as a real history entry.

        outfits = record.get('outfits')
        vehicle_type = record.get('vehicleTypeCompactDescr')
        if isinstance(outfits, dict) and vehicle_type is not None:
            serialized = {}
            for season, outfit_data in outfits.items():
                try:
                    season = int(season)
                    compact_descr, enabled = outfit_data
                except (TypeError, ValueError):
                    continue
                serialized[season] = (compact_descr, bool(enabled))
            if serialized:
                customization_outfits[int(vehicle_type)] = serialized

        for item_type, items in dict(
                record.get('inventoryItems', {})).items():
            item_type = int(item_type)
            if item_type in values and item_type not in (
                    VEHICLE_ITEM_TYPE, TANKMAN_ITEM_TYPE,
                    CUSTOMIZATION_ITEM_TYPE):
                wanted = _wanted_items(only_items, item_type)
                target = values[item_type]
                for compact_descr, count in items.items():
                    if wanted is not None and compact_descr not in wanted:
                        continue
                    target[compact_descr] = max(
                        int(target.get(compact_descr, 0)), int(count))

        all_tankmen.update(tankmen)
        # This foreign key is the vehicle inventory id, not its type id.
        tankman_vehicles.update(dict(
            (tankman_id, vehicle_id) for tankman_id in tankmen))

    # A crew member the account owns but no vehicle carries is in the
    # barracks. #1513's ``InventoryRequester.__makeTankman`` reads a missing
    # or non-positive ``vehicle`` foreign key as exactly that, and
    # ``ItemsRequester`` then builds the Tankman with no vehicle at all.
    wanted_tankmen = None if not delta else set(
        int(tankman_id) for tankman_id in (touched_tankmen or ()))
    for tankman_id, compact_descr in dict(
            vehicle.get('barracksTankmen') or {}).items():
        tankman_id = int(tankman_id)
        if wanted_tankmen is not None and tankman_id not in wanted_tankmen:
            continue
        all_tankmen[tankman_id] = compact_descr
        tankman_vehicles[tankman_id] = BARRACKS_VEHICLE_ID

    # The account owns items; a vehicle only carries some of them. The
    # snapshot's top-level catalogue is that account view, and it is the only
    # place a spare module or a bought consumable exists at all, because no
    # vehicle carries one. Publishing it alongside the per-vehicle counts is
    # what puts a purchase in the player's depot.
    #
    # Ammunition is the exception: offline resupply is unlimited and nothing
    # consumes a round yet, so a shell's account count is a high-water mark
    # rather than a stock, and the honest number to show is what the vehicle
    # is actually carrying.
    for item_type, items in dict(
            vehicle.get('inventoryItems', {})).items():
        item_type = int(item_type)
        if item_type not in values or item_type in (
                VEHICLE_ITEM_TYPE, TANKMAN_ITEM_TYPE, SHELL_ITEM_TYPE,
                CUSTOMIZATION_ITEM_TYPE):
            continue
        wanted = _wanted_items(only_items, item_type)
        target = values[item_type]
        for compact_descr, count in dict(items).items():
            if wanted is not None and compact_descr not in wanted:
                continue
            target[compact_descr] = max(
                int(target.get(compact_descr, 0)), int(count))

    values[TANKMAN_ITEM_TYPE] = {
        'compDescr': all_tankmen,
        'vehicle': tankman_vehicles,
    }
    values[VEHICLE_ITEM_TYPE] = vehicle_values
    customization_items = {}
    for custom_type, items in dict(
            vehicle.get('customizationItems', {})).items():
        custom_type = int(custom_type)
        customization_items[custom_type] = {}
        for item_id, counts in dict(items).items():
            customization_items[custom_type][int(item_id)] = dict(
                (int(vehicle_type), int(count))
                for vehicle_type, count in dict(counts).items())
    values[CUSTOMIZATION_ITEM_TYPE] = {
        CUSTOMIZATION_ITEMS: customization_items,
        CUSTOMIZATION_OUTFITS: customization_outfits,
        CUSTOMIZATION_UNLOCKS: {},
    }
    if delta:
        _publish_removals(values, vehicle_values, records, vehicle,
                          only_vehicles, only_items, touched_tankmen)
        values = _prune_empty(values)
    return {'inventory': values}


def _publish_removals(values, vehicle_values, records, vehicle,
                      only_vehicles, only_items, touched_tankmen):
    """Say what the account no longer owns, in the client's own vocabulary.

    #1513 merges an inventory diff through ``diff_utils.synchronizeDicts``,
    which pops a cached key when the diff carries that key with the value
    ``None``. Leaving a sold vehicle, a dismissed crew member or an item whose
    count reached zero out of the diff is therefore not a removal at all: the
    client's cache keeps it. Only a delta can remove anything; a full sync
    replaces the cache outright.

    A removal is decided by the account, never by the diff: an item the
    account still owns but that this publication had no reason to carry is
    left alone, so a publication rule can never delete a player's property by
    saying nothing about it.
    """
    owned = dict(vehicle.get('inventoryItems', {}))
    live = set(int(record.get('id', 1)) for record in records)
    for vehicle_id in (only_vehicles or ()):
        vehicle_id = int(vehicle_id)
        if vehicle_id in live:
            continue
        for section in vehicle_values.values():
            section[vehicle_id] = None
    tankmen = values.get(TANKMAN_ITEM_TYPE)
    if isinstance(tankmen, dict):
        held = set()
        for record in records:
            held.update(
                int(tankman_id)
                for tankman_id in (record.get('tankmen') or ()))
        held.update(
            int(tankman_id)
            for tankman_id in (vehicle.get('barracksTankmen') or ()))
        for tankman_id in (touched_tankmen or ()):
            tankman_id = int(tankman_id)
            if tankman_id in held:
                # Moved, not removed: a crew member the account still holds
                # was republished above with wherever they now sit.
                continue
            tankmen['compDescr'][tankman_id] = None
            tankmen['vehicle'][tankman_id] = None
    for item_type, wanted in dict(only_items or {}).items():
        item_type = int(item_type)
        if item_type in (VEHICLE_ITEM_TYPE, TANKMAN_ITEM_TYPE,
                         CUSTOMIZATION_ITEM_TYPE):
            continue
        section = values.get(item_type)
        if not isinstance(section, dict):
            continue
        still_owned = dict(owned.get(item_type, {}))
        for compact_descr in (wanted or ()):
            if _int_count(still_owned.get(compact_descr)) > 0:
                continue
            section.setdefault(compact_descr, None)


def _int_count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _wanted_items(only_items, item_type):
    """Return the owned descriptors to publish for one item type, or None."""
    if only_items is None:
        return None
    return set(only_items.get(int(item_type)) or ())


def _prune_empty(values):
    """Drop the sections a delta did not change."""
    pruned = {}
    for item_type, section in values.items():
        # ``None`` is the client's removal marker, not an empty section.
        section = dict((key, value)
                       for key, value in section.items()
                       if value or value is None)
        if section:
            pruned[item_type] = section
    return pruned


def _elite_vehicles(vehicle_types, unlocks):
    """Return the owned vehicles whose whole research list is unlocked.

    #1513 marks a vehicle elite when nothing it leads to is left to research.
    Deriving it means a career's tech tree is honest and a sandbox stays fully
    elite because its unlock set already covers the catalogue, instead of the
    garage asserting eliteness for every vehicle it publishes.
    """
    try:
        from items import vehicles
    except ImportError:
        return set(vehicle_types)
    elite = set()
    for compact_descr in vehicle_types:
        try:
            vehicle_type = vehicles.getVehicleType(int(compact_descr))
            descriptors = tuple(
                getattr(vehicle_type, 'unlocksDescrs', ()) or ())
        except Exception:
            # A vehicle whose type cannot be read is a presentation problem,
            # not a reason to withhold the rest of the account.
            continue
        if all(int(tuple(row)[1]) in unlocks
               for row in descriptors if len(tuple(row)) >= 2):
            elite.add(compact_descr)
    return elite


def stats(selected_vehicle=None, postbattle_progress=None):
    """Return an unlocked, well-funded account for the local garage."""
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    vehicle_types = set(vehicle.get('vehicleTypeCompactDescrs', ()))
    if not vehicle_types:
        vehicle_types.update(
            record.get('vehicleTypeCompactDescr')
            for record in _vehicle_records(vehicle)
            if record.get('vehicleTypeCompactDescr') is not None)
    unlocks = set(vehicle.get('unlockItemCompactDescrs', ()))
    unlocks.update(vehicle_types)
    # The garage owns the balances: research spends vehicle experience and a
    # purchase spends credits, so the same file that records what the player
    # has must record what it cost. ``postbattle_progress`` keeps the battle
    # counters its own consumers read.
    progress = (postbattle_progress
                if isinstance(postbattle_progress, dict) else {})
    wallet = vehicle.get('wallet')
    wallet = wallet if isinstance(wallet, dict) else {}
    balances = dict(
        (name, max(0, int(wallet.get(name, default) or 0)))
        for name, default in (('credits', OFFLINE_CREDITS),
                              ('gold', OFFLINE_GOLD),
                              ('freeXP', OFFLINE_FREE_XP)))
    vehicle_xp = dict((compact_descr, 0) for compact_descr in vehicle_types)
    saved_xp = vehicle.get('vehicleXP')
    if isinstance(saved_xp, dict):
        for compact_descr, experience in saved_xp.items():
            try:
                vehicle_xp[int(compact_descr)] = max(0, int(experience or 0))
            except (TypeError, ValueError):
                continue
    elite = _elite_vehicles(vehicle_types, unlocks)
    return {
        'account': {
            'clanDBID': 0, 'attrs': 0, 'premiumExpiryTime': 0,
            'autoBanTime': 0, 'globalRating': 0,
        },
        'stats': {
            'credits': balances['credits'],
            'gold': balances['gold'],
            'crystal': 0,
            'freeXP': balances['freeXP'],
            'slots': max(0, int(
                vehicle.get('accountSlots', OFFLINE_GARAGE_SLOTS) or 0)),
            'berths': max(0, int(
                vehicle.get('accountBerths', OFFLINE_BARRACKS_BERTHS) or 0)),
            'accOnline': 0, 'accOffline': 0,
            'freeTMenLeft': 0, 'freeVehiclesLeft': 0,
            'vehicleSellsLeft': 0, 'captchaTriesLeft': 0,
            # Match the established offline-server account profile.  Zero
            # starts the stock lobby tutorial/hints lifecycle even though this
            # account cannot persist its tutorial actions on a retail server.
            'denunciationsLeft': 0, 'tutorialsCompleted': 33553532,
            'dossier': account_dossier(progress),
            'battlesTillCaptcha': 0, 'dailyPlayHours': [0],
            # Full daily/weekly periods disable parental-control blocking in
            # the native #1513 GameSessionController.  Zero means no allowed
            # play time, not "unlimited".
            'playLimits': ((86400, ''), (604800, '')),
            'vehTypeXP': vehicle_xp,
            'vehTypeLocks': {}, 'restrictions': {},
            'globalVehicleLocks': {}, 'refSystem': {'referrals': {}},
            'unlocks': unlocks,
            'eliteVehicles': elite,
            'multipliedXPVehs': set(),
        },
        'cache': {
            'isFinPswdVerified': True,
            # False means "wallet synchronization is still in progress" in
            # #1513, so the header deliberately renders gold/free XP as "--".
            # The offline snapshot above is already authoritative.
            'mayConsumeWalletResources': True,
            'unitAcceptDeadline': 0,
            'oldVehInvIDs': set(),
        },
    }


def personal_missions():
    """#1513's _PersonalMissionsProgressRequester._response indexes
    ``value['potapovQuests']['compDescr']`` for every non-empty diff."""
    return {
        'compDescr': '',
        'regular': {'slots': 0, 'selected': [], 'lastIDs': {}},
        'training': {'slots': 0, 'selected': [], 'lastIDs': {}},
    }


def sync_data(revision=0, selected_vehicle=None, int_user_settings=None,
              postbattle_progress=None):
    # These are deliberately present even when empty.  #1513's account
    # helpers only create a requester cache entry when the corresponding key
    # exists in the sync diff; several lobby requesters then index that entry
    # directly instead of applying a missing-value default.
    result = {
        'rev': int(revision) + 1,
        'prevRev': int(revision),
        'quests': {},
        'tokens': {},
        'potapovQuests': personal_missions(),
        'intUserSettings': dict(int_user_settings or {}),
        'goodies': {},
        'groupLocks': {'groupBattles': [], 'isGroupLocked': []},
        'vehiclesGroupMapping': {},
        'recycleBin': {},
        'ranked': {},
        'badges': (),
        'newYear': {},
        'eventsData': {},
    }
    result.update(inventory(selected_vehicle))
    result.update(stats(selected_vehicle, postbattle_progress))
    return result


def shop(revision=0, selected_vehicle=None):
    """Return the smallest stream accepted by #1513 ``Shop``.

    The lobby/map picker does not buy items, but the native account lifecycle
    always synchronizes the shop.  ``sellPriceFactor`` is mandatory in
    ``Shop.__onSyncDataReceived``; the remaining values keep read-only getters
    deterministic instead of leaving a half-synchronized cache.
    """
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    _validate_selected_vehicle(vehicle)
    item_prices = dict(vehicle.get('shopItemPrices', {}))
    nation_count = max(1, int(vehicle.get('shopNationCount', 16)))
    empty_items = {
        'itemPrices': item_prices,
        'notInShopItems': set(
            vehicle.get('notInShopItems', ()) or ()),
        'vehiclesNotToBuy': set(),
        'vehiclesRentPrices': {},
        'vehiclesToSellForGold': set(),
        'vehicleSellPriceFactors': {},
        # Legacy and customization 2.0 requesters both index these arrays by
        # nation without guarding an empty default.  The exact count comes
        # from nations.NAMES in bootstrap; the values remain read-only.
        'inscriptionGroupPriceFactors': [
            {} for unused_index in range(nation_count)],
        'notInShopInscriptionGroups': [
            set() for unused_index in range(nation_count)],
        'camouflagePriceFactors': [
            {} for unused_index in range(nation_count)],
        'notInShopCamouflages': [
            set() for unused_index in range(nation_count)],
        'playerEmblemGroupPriceFactors': {},
        'notInShopPlayerEmblemGroups': set(),
        'vehicleCamouflagePriceFactors': {},
        'vehicleHornPriceFactors': {},
    }
    empty_goodies = {'prices': {}, 'notInShop': set(), 'goodies': {}}
    return {
        'rev': int(revision) + 1,
        'prevRev': int(revision),
        'crystalExchangeRate': 0,
        'sellPriceFactor': OFFLINE_SELL_PRICE_FACTOR,
        'items': dict(empty_items),
        'defaults': {
            'items': dict(empty_items),
            'freeXPToTManXPRate': 10,
            'goodies': dict(empty_goodies),
            'paidRemovalCost': {'gold': 0},
            # #1513 OptionalDevice.getRemovalPrice uses a separate Money
            # value for optional devices tagged ``deluxe``.
            'paidDeluxeRemovalCost': {'crystal': 0},
        },
        'goodies': dict(empty_goodies),
        # Exact #1513 consumers fall back to the final price entry and the
        # berth helper divides by pack size.  Empty lists and a zero pack size
        # therefore crash even though the outer tuple arity is correct.
        'berthsPrices': (0, 1, [0]),
        'slotsPrices': (0, [0]),
        # Stock-compatible, non-zero exchange ratios.  The native exchange
        # dialogs divide by both freeXPConversion[0] and this tankman rate.
        'freeXPConversion': (25, 1),
        'dropSkillsCost': {
            0: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 0.5,
            },
            1: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 0.5,
            },
            2: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 1.0,
            },
        },
        # The three native recruitment choices are positional.  Keep their
        # complete descriptor dictionaries even though offline prices are 0.
        'tankmanCost': (
            {
                'credits': 0, 'gold': 0, 'roleLevel': 50,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': False,
            },
            {
                'credits': 0, 'gold': 0, 'roleLevel': 75,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': False,
            },
            {
                'credits': 0, 'gold': 0, 'roleLevel': 100,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': True,
            },
        ),
        'premiumCost': {},
        # RefSystem.__update indexes posByXPinTeam directly.  Once this dict is
        # non-empty, its #1513 helpers also index the other three values, so
        # keep the entire native disabled/default shape together.
        'refSystem': {
            'periods': 0,
            'maxReferralXPPool': 0,
            'maxNumberOfReferrals': 0,
            'posByXPinTeam': 0,
        },
        # ShopRequester calls .get(Currency.GOLD) on this value.
        'paidRemovalCost': {'gold': 0},
        # Deluxe optional devices do not use paidRemovalCost.  Publish their
        # exact #1513 shop field so the requester does not use its retail
        # crystal-price fallback.
        'paidDeluxeRemovalCost': {'crystal': 0},
        'dailyXPFactor': 1,
        'changeRoleCost': 0,
        'freeXPToTManXPRate': 10,
        # Offline policy, not a #1513 value: the gold exchange rate is
        # server state the client never receives. 400 credits per gold is the
        # long-published retail rate and the one the garage's own dialogs
        # were written around.
        'exchangeRate': OFFLINE_GOLD_EXCHANGE_RATE,
        'exchangeRateForShellsAndEqs': OFFLINE_GOLD_EXCHANGE_RATE,
        'isEnabledBuyingGoldShellsForCredits': True,
        'isEnabledBuyingGoldEqsForCredits': True,
    }


def _vehicle_type_compact_descr(type_name):
    from items import vehicles
    descriptor = vehicles.VehicleDescr(typeName=str(type_name))
    nation_id, vehicle_type_id = descriptor.type.id
    return int(vehicles.makeIntCompactDescrByID(
        'vehicle', nation_id, vehicle_type_id))


def _write_achievements(dossier, counts):
    """Store earned medal counters in the exact #1513 dossier block.

    The block's record layout is fixed by the pinned client, so a name it
    does not carry is skipped rather than allowed to raise inside the native
    dossier builder.  ``battleHeroes`` mirrors stock's aggregate counter.
    """
    if not isinstance(counts, dict) or not counts:
        return
    block = dossier['achievements']
    heroes = 0
    for name in sorted(counts):
        value = counts.get(name)
        # Optional persisted state reaches here, so a counter that is not a
        # positive whole number is dropped rather than coerced.  ``bool`` is
        # an ``int`` subclass and is not a count.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            continue
        if name in BATTLE_HERO_ACHIEVEMENTS:
            heroes += value
        try:
            block[name] = value
        except (KeyError, TypeError, ValueError):
            continue
    if heroes:
        try:
            block['battleHeroes'] = heroes
        except (KeyError, TypeError, ValueError):
            pass


def account_dossier(postbattle_progress=None, dossier_factory=None):
    """Return the account dossier compact descriptor #1513 reads.

    ``StatsRequester.accountDossier`` of the pinned client reads the ``stats``
    cache key ``dossier``; the lobby profile builds its achievements page from
    it.  An account with no medals keeps the stock empty-string value.
    """
    progress = (postbattle_progress
                if isinstance(postbattle_progress, dict) else {})
    counts = progress.get('achievements')
    if not isinstance(counts, dict) or not counts:
        return ''
    if dossier_factory is None:
        from dossiers2.custom.builders import getAccountDossierDescr
        dossier_factory = getAccountDossierDescr
    dossier = dossier_factory('')
    _write_achievements(dossier, counts)
    return dossier.makeCompDescr()


def dossiers(revision=0, max_change_time=0, postbattle_progress=None,
             dossier_factory=None, vehicle_type_resolver=None):
    """Return exact native-built vehicle dossier rows for #1513 cache."""
    progress = (postbattle_progress
                if isinstance(postbattle_progress, dict) else {})
    vehicle_rows = dict(progress.get('vehicles', {}))
    if not vehicle_rows:
        return (1, [])
    if dossier_factory is None:
        from dossiers2.custom.builders import getVehicleDossierDescr
        dossier_factory = getVehicleDossierDescr
    resolver = vehicle_type_resolver or _vehicle_type_compact_descr
    rows = []
    for type_name, stats in sorted(
            vehicle_rows.items()):
        change_time = max(1, int(stats.get(
            'changeTime', stats.get('battles', 0)) or 0))
        if change_time <= int(max_change_time or 0):
            continue
        dossier = dossier_factory('')
        block = dossier['a15x15']
        block2 = dossier['a15x15_2']
        battles = max(0, int(stats.get('battles', 0) or 0))
        wins = min(battles, max(0, int(stats.get('wins', 0) or 0)))
        if 'losses' in stats:
            losses = min(
                battles - wins,
                max(0, int(stats.get('losses', 0) or 0)))
        else:
            # Schema-1 files written before draws were preserved have no way
            # to distinguish a draw from a loss. Keep their former behavior.
            losses = battles - wins
        block['xp'] = max(0, int(stats.get('xp', 0) or 0))
        block['battlesCount'] = battles
        block['wins'] = wins
        block['losses'] = losses
        block['frags'] = max(0, int(stats.get('kills', 0) or 0))
        block['damageDealt'] = max(0, int(stats.get('damage', 0) or 0))
        for field_name in (
                'shots', 'directHits', 'spotted', 'damageReceived',
                'capturePoints', 'droppedCapturePoints', 'survivedBattles'):
            block[field_name] = max(
                0, int(stats.get(field_name, 0) or 0))
        for field_name in (
                'piercings', 'damageBlockedByArmor',
                'damageAssistedTrack', 'damageAssistedRadio',
                'damageAssistedStun'):
            block2[field_name] = max(
                0, int(stats.get(field_name, 0) or 0))
        _write_achievements(dossier, stats.get('achievements'))
        rows.append((resolver(type_name), change_time,
                     dossier.makeCompDescr()))
    # This cache version describes our dossier row schema, not battle count.
    # Keeping it stable lets maxChangeTime request only changed vehicle rows.
    return (1, rows)
