from __future__ import print_function

import os
import sys
import time

import BigWorld

from gui.mods.offline_lan_0922.compat import g_compatibility
from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import instance_guard
from gui.mods.offline_lan_0922 import vehicle_blacklist
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

# Enough of every artefact that the garage never blocks a mount on stock.
OFFLINE_ARTEFACT_STOCK = 200
# Untrained skills offered per crewman before the player picks any.
NEW_SKILL_SLOTS = 8
_NEW_SKILL_XP = {}
_store = None
_postbattle_store = None


def _default_vehicle_settings():
    """Return the VEHICLE_SETTINGS_FLAG mask a fresh garage vehicle starts with.

    Auto-repair, both auto-resupply switches and accelerated crew training are
    on, so the player never has to tick them.
    """
    from AccountCommands import VEHICLE_SETTINGS_FLAG
    return (VEHICLE_SETTINGS_FLAG.XP_TO_TMAN |
            VEHICLE_SETTINGS_FLAG.AUTO_REPAIR |
            VEHICLE_SETTINGS_FLAG.AUTO_LOAD |
            VEHICLE_SETTINGS_FLAG.AUTO_EQUIP)


def _new_skill_xp(tankmen, descriptor, trained,
                  choices=NEW_SKILL_SLOTS):
    """Return the free XP that leaves ``choices`` skills to pick.

    #1513's ``Tankman.newSkillCount`` offers one more skill for every skill the
    stored free XP can train to ``tankmen.MAX_SKILL_LEVEL``, plus the one it
    starts.  The cost depends only on how many skills the crewman already has.
    """
    key = (trained, choices)
    if key not in _NEW_SKILL_XP:
        _NEW_SKILL_XP[key] = sum(
            descriptor.levelUpXpCost(level, trained + step)
            for step in range(1, choices)
            for level in range(tankmen.MAX_SKILL_LEVEL))
    return _NEW_SKILL_XP[key]


def _top_up_new_skill_slots(tankmen, descriptor):
    """Give one parsed crewman NEW_SKILL_SLOTS total skill choices.

    Learned skills stay selected.  XP is added only when the learned and still
    selectable skills would otherwise total less than the offline minimum.
    """
    maximum = int(tankmen.MAX_SKILL_LEVEL)
    selected = int(descriptor.lastSkillNumber)
    missing = NEW_SKILL_SLOTS - selected
    if missing <= 0:
        return False
    trained = max(0, selected - int(descriptor.freeSkillsNumber))

    role_level = int(getattr(descriptor, 'roleLevel', maximum))
    if role_level != maximum:
        # Offline crew is generated at 100%.  Do not silently retrain a saved
        # descriptor from another source just to make secondary slots appear.
        return False
    last_skill_level = int(getattr(
        descriptor, 'lastSkillLevel', maximum))
    required = 0
    incomplete = False
    if trained and last_skill_level < maximum:
        required += sum(
            descriptor.levelUpXpCost(level, trained)
            for level in range(max(0, last_skill_level), maximum))
        incomplete = True
    required += _new_skill_xp(
        tankmen, descriptor, trained, choices=missing)

    current = max(0, int(descriptor.freeXP))
    if current >= required and not incomplete:
        return False
    delta = max(0, required - current)
    # addXP consumes the budget into the current skill first.  Merely assigning
    # freeXP would leave #1513's newSkillCount blocked below 100%.
    descriptor.addXP(delta)
    return True


def _with_new_skill_slots(tankmen, descriptor):
    """Return the tankman with NEW_SKILL_SLOTS skills left for the player.

    No skill is chosen here; the player picks all of them.  The caller already
    unpacked this descriptor to validate the crew slot, so it is reused.
    """
    _top_up_new_skill_slots(tankmen, descriptor)
    return descriptor.makeCompactDescr()


def _migrate_saved_crew_skill_slots(snapshot, tankmen):
    """Top up restored crew without replacing any learned skill."""
    records = snapshot.get('vehicles') if isinstance(snapshot, dict) else None
    if not isinstance(records, (list, tuple)):
        records = [snapshot]
    migrated = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        crew = list(record.get('crew') or ())
        tankman_rows = record.get('tankmen')
        if not isinstance(tankman_rows, dict):
            continue
        for tankman_id in crew:
            compact_descr = tankman_rows.get(tankman_id)
            if compact_descr is None:
                continue
            descriptor = tankmen.TankmanDescr(compact_descr)
            if not _top_up_new_skill_slots(tankmen, descriptor):
                continue
            tankman_rows[tankman_id] = descriptor.makeCompactDescr()
            migrated += 1
    return migrated


def _schedule(delay, function):
    global _callback_id
    _callback_id = BigWorld.callback(delay, function)


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
        result = garage_store.apply_battle_crew_xp(
            snapshot, receipt['receipt_id'], vehicle_type_cd,
            receipt['rewards']['xp'], VEHICLE_SETTINGS_FLAG.XP_TO_TMAN,
            tankmen_module=tankmen)
        context['selected_vehicle'] = snapshot
        touched.add(int(result['vehicle_id']))
        return result

    binder(apply)
    return True


def _restore_garage(snapshot):
    store = _garage_store()
    if store is None:
        return False
    migrated = [0]

    def validate(staged):
        from items import tankmen
        migrated[0] = _migrate_saved_crew_skill_slots(staged, tankmen)
        return _validate_restored_garage(staged)

    try:
        restored = bool(store.apply(snapshot, validator=validate))
    except Exception as error:
        sys.stdout.write(
            '[Offline LAN 0.9.22] the saved garage could not be restored: '
            '%s\n' % error)
        return False
    if restored and migrated[0]:
        store.mark_dirty()
        if store.flush(snapshot):
            sys.stdout.write(
                '[Offline LAN 0.9.22] upgraded saved skill choices for %d '
                'crew member(s)\n' % migrated[0])
    return restored


def _validate_restored_garage(snapshot):
    """Exercise saved native descriptors before publishing the Account.

    ``GarageStore`` first validates the engine-free relational snapshot.  This
    second boundary rejects a damaged compact descriptor, crew row, outfit or
    ammunition set while the restore still lives on a detached copy.  A bad
    save therefore falls back to the freshly built stock garage instead of
    aborting Account synchronization and parking the client at login.
    """
    from items import customizations, tankmen, vehicles
    from gui.mods.offline_lan_0922.account_rpc import data

    data._validate_selected_vehicle(snapshot)
    records = snapshot.get('vehicles')
    if not isinstance(records, (list, tuple)):
        records = [snapshot]
    for record in records:
        descriptor = vehicles.VehicleDescr(
            compactDescr=record['compDescr'])
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
        if loaded <= 0:
            raise ValueError('saved vehicle has no loaded ammunition')
        maximum = int(getattr(descriptor.gun, 'maxAmmo', 0) or 0)
        if maximum > 0 and loaded > maximum:
            raise ValueError('saved ammunition exceeds the gun capacity')

        crew_ids = list(record.get('crew') or ())
        crew = dict(record.get('tankmen') or {})
        roles = tuple(descriptor.type.crewRoles)
        if len(crew_ids) != len(roles):
            raise ValueError('saved crew does not match the vehicle')
        for slot, tankman_id in enumerate(crew_ids):
            tankman = tankmen.TankmanDescr(crew[tankman_id])
            if (int(tankman.nationID) != int(nation_id) or
                    int(tankman.vehicleTypeID) != int(vehicle_type_id) or
                    tankman.role != roles[slot][0]):
                raise ValueError(
                    'saved crew member does not match the vehicle slot')

        for outfit_data in dict(record.get('outfits') or {}).values():
            outfit_descr, unused_enabled = outfit_data
            customizations.parseOutfitDescr(outfit_descr)
    return True


def _component_compact_descrs(value, seen):
    """Yield compact descriptors from a component list, however it nests.

    #1513 stores turrets per turret position, so the same walker has to accept
    both a flat component list and a list of per-position lists.
    """
    if value is None:
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            for compact_descr in _component_compact_descrs(item, seen):
                yield compact_descr
        return
    compact_descr = getattr(value, 'compactDescr', None)
    if compact_descr is None:
        return
    try:
        compact_descr = int(compact_descr)
    except (TypeError, ValueError):
        return
    if compact_descr in seen:
        return
    seen.add(compact_descr)
    yield compact_descr


def _component_descriptors(value, seen):
    """Yield unique component objects from a possibly nested component list."""
    if value is None:
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            for descriptor in _component_descriptors(item, seen):
                yield descriptor
        return
    compact_descr = getattr(value, 'compactDescr', None)
    try:
        compact_descr = int(compact_descr)
    except (TypeError, ValueError):
        return
    if compact_descr in seen:
        return
    seen.add(compact_descr)
    yield value


def _offers_in_random_battle(descriptor):
    """Return whether a standard random battle may carry this artefact.

    #1513 tags the artillery and airstrike consumables ``avatar`` and drives
    them through ``Avatar.activateAvatarEquipment``, which this port does not
    implement.  Battle boosters carry a non-regular ``equipmentType`` and the
    published garage has no slot for them.
    """
    from items import EQUIPMENT_TYPES
    if 'avatar' in (getattr(descriptor, 'tags', None) or ()):
        return False
    equipment_type = getattr(descriptor, 'equipmentType', None)
    return equipment_type in (None, EQUIPMENT_TYPES.regular)


def _vehicle_type_modules(descriptor):
    """Yield ``(itemTypeName, compactDescr)`` for every module of one type."""
    vehicle_type = getattr(descriptor, 'type', None)
    if vehicle_type is None:
        return
    seen = set()
    for item_type_name, attribute in (
            ('vehicleChassis', 'chassis'),
            ('vehicleTurret', 'turrets'),
            ('vehicleEngine', 'engines'),
            ('vehicleRadio', 'radios'),
            ('vehicleFuelTank', 'fuelTanks')):
        for compact_descr in _component_compact_descrs(
                getattr(vehicle_type, attribute, None), seen):
            yield (item_type_name, compact_descr)
    # Guns hang off each turret variant; a flat ``guns`` list may also exist.
    gun_seen = set()
    for turret in _turret_descriptors(vehicle_type):
        for compact_descr in _component_compact_descrs(
                getattr(turret, 'guns', None), gun_seen):
            yield ('vehicleGun', compact_descr)
    for compact_descr in _component_compact_descrs(
            getattr(vehicle_type, 'guns', None), gun_seen):
        yield ('vehicleGun', compact_descr)


def _vehicle_type_guns(descriptor):
    """Yield every gun the vehicle type can mount, including stock guns."""
    vehicle_type = getattr(descriptor, 'type', None)
    if vehicle_type is None:
        return
    seen = set()
    for turret in _turret_descriptors(vehicle_type):
        for gun in _component_descriptors(
                getattr(turret, 'guns', None), seen):
            yield gun
    for gun in _component_descriptors(
            getattr(vehicle_type, 'guns', None), seen):
        yield gun


# equipments.xml: autoExtinguishers id=1, largeMedkit id=3, largeRepairkit
# id=5.  None carries a <type>, a vehicleFilter or an incompatibleTags section,
# so all three are regular consumables that fit every vehicle.
_DEFAULT_CONSUMABLE_NAMES = ('autoExtinguishers', 'largeMedkit',
                             'largeRepairkit')


def _default_consumables(vehicles):
    """Return the compact descriptors of the three mounted consumables.

    They come from the cache rather than from a rebuilt id, because #1513
    gives an artefact ``nations.NONE_INDEX`` instead of a real nation.
    """
    ids = vehicles.g_cache.equipmentIDs()
    equipments = vehicles.g_cache.equipments()
    slots = []
    for name in _DEFAULT_CONSUMABLE_NAMES:
        descriptor = equipments.get(ids.get(name))
        if descriptor is None or not _offers_in_random_battle(descriptor):
            raise ValueError('client equipment %r is unavailable' % (name,))
        slots.append(int(descriptor.compactDescr))
    return slots


def _turret_descriptors(vehicle_type):
    stack = [getattr(vehicle_type, 'turrets', None)]
    while stack:
        value = stack.pop()
        if value is None:
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(value)
            continue
        if getattr(value, 'compactDescr', None) is not None:
            yield value


def _selected_vehicle(config, restore_saved=True):
    try:
        import nations
        from items import ITEM_TYPE_INDICES, tankmen, vehicles
        selected_descriptor = vehicles.VehicleDescr(
            typeName=config['vehicle'])
        selected_type_id = tuple(selected_descriptor.type.id)
        default_consumables = _default_consumables(vehicles)

        # The exact #1513 VehicleList exposes one nation-indexed mapping for
        # every nations.NAMES entry.  Put the configured vehicle first so its
        # inventory id remains stable while the rest of the loadable local
        # catalogue is discovered from the client, rather than hard-coded.
        type_ids = [selected_type_id]
        for nation_id in range(len(nations.NAMES)):
            for vehicle_type_id in sorted(
                    vehicles.g_list.getList(nation_id).keys()):
                type_id = (nation_id, vehicle_type_id)
                if type_id not in type_ids:
                    type_ids.append(type_id)

        vehicle_records = []
        inventory_items = {}
        shop_item_prices = {}
        vehicle_type_compact_descrs = set()
        unlock_item_compact_descrs = set()
        next_tankman_id = 100001
        default_settings = _default_vehicle_settings()
        for type_id in type_ids:
            try:
                if type_id == selected_type_id:
                    descriptor = selected_descriptor
                else:
                    descriptor = vehicles.VehicleDescr(typeID=type_id)

                nation_id, vehicle_type_id = descriptor.type.id
                if not _is_standard_battle_vehicle(descriptor.type):
                    raise ValueError(
                        'vehicle type is not available in standard battles')
                if vehicle_blacklist.is_unusable(descriptor.type.name):
                    raise ValueError(
                        'this client has no resources for %s: %s' % (
                            descriptor.type.name, ', '.join(
                                vehicle_blacklist.missing_resources(
                                    descriptor.type.name))))
                _install_top_modules(descriptor)
                # A fresh offline crew starts without preselected perks.  The
                # free-XP budget below exposes the requested empty slots, so
                # the player remains the sole owner of every skill choice.
                skills_mask = tankmen.getSkillsMask(())
                crew_compact_descrs = list(tankmen.generateTankmen(
                    nation_id, vehicle_type_id, descriptor.type.crewRoles,
                    False, tankmen.MAX_SKILL_LEVEL, skills_mask, False))
                if (not crew_compact_descrs or
                        len(crew_compact_descrs) !=
                        len(descriptor.type.crewRoles)):
                    raise ValueError(
                        'generated crew does not match vehicle crew slots')

                validated_tankmen = []
                for index, compact_descr in enumerate(crew_compact_descrs):
                    tankman_descr = tankmen.TankmanDescr(compact_descr)
                    roles = descriptor.type.crewRoles[index]
                    if (tankman_descr.nationID != nation_id or
                            tankman_descr.vehicleTypeID != vehicle_type_id or
                            tankman_descr.role != roles[0]):
                        raise ValueError(
                            'generated tankman does not match vehicle crew slot')
                    validated_tankmen.append(
                        _with_new_skill_slots(tankmen, tankman_descr))

                components = (
                    ('vehicleChassis', descriptor.chassis),
                    ('vehicleTurret', descriptor.turret),
                    ('vehicleGun', descriptor.gun),
                    ('vehicleEngine', descriptor.engine),
                    ('vehicleRadio', descriptor.radio),
                    ('vehicleFuelTank', descriptor.fuelTank),
                )
                record_inventory_items = {}
                for item_type_name, component in components:
                    compact_descr = component.compactDescr
                    item_type = ITEM_TYPE_INDICES[item_type_name]
                    record_inventory_items.setdefault(
                        item_type, {})[compact_descr] = 1
                # Publish every module this vehicle type can carry, not only
                # the stock fitting, so its research tree shows them owned
                # instead of costing XP.  The lists come from the vehicle's own
                # type, so a premium hull still offers only its own modules.
                for item_type_name, compact_descr in _vehicle_type_modules(
                        descriptor):
                    item_type = ITEM_TYPE_INDICES[item_type_name]
                    owned = record_inventory_items.setdefault(item_type, {})
                    owned[compact_descr] = max(
                        1, int(owned.get(compact_descr, 0)))

                shells = list(vehicles.getDefaultAmmoForGun(descriptor.gun))
                if not shells or len(shells) % 2:
                    raise ValueError(
                        'default ammo must contain descriptor/count pairs')
                record_shell_items = record_inventory_items.setdefault(
                    ITEM_TYPE_INDICES['shell'], {})
                for index in range(0, len(shells), 2):
                    record_shell_items[shells[index]] = shells[index + 1]

                # Saved fittings may mount any gun in the vehicle's research
                # tree.  Catalogue every such gun's shells at account level,
                # while the per-vehicle inventory below remains exactly the
                # ammunition currently loaded.  Without this closure an
                # alternate gun saved valid shells that disappeared from the
                # next bootstrap's prices/unlocks and Account sync aborted.
                shell_catalog = {}
                for gun in _vehicle_type_guns(descriptor):
                    gun_shells = list(vehicles.getDefaultAmmoForGun(gun))
                    if not gun_shells or len(gun_shells) % 2:
                        raise ValueError(
                            'gun ammo must contain descriptor/count pairs')
                    for index in range(0, len(gun_shells), 2):
                        compact_descr = int(gun_shells[index])
                        count = int(gun_shells[index + 1])
                        if compact_descr <= 0 or count < 0:
                            raise ValueError(
                                'gun ammo has an invalid descriptor or count')
                        shell_catalog[compact_descr] = max(
                            count, int(shell_catalog.get(compact_descr, 0)))

                # The exact key #1513 Vehicle.shellsLayoutIdx looks up.
                layout_key = (descriptor.turret.compactDescr,
                              descriptor.gun.compactDescr)
                vehicle_compact_descr = descriptor.makeCompactDescr()
                if not vehicle_compact_descr or descriptor.maxHealth <= 0:
                    raise ValueError('vehicle descriptor is not garage-ready')
                vehicle_int_compact_descr = (
                    vehicles.makeIntCompactDescrByID(
                        'vehicle', nation_id, vehicle_type_id))
            except Exception:
                if type_id == selected_type_id:
                    raise
                # Special or incomplete definitions can be advertised by
                # g_list but still fail native garage construction.  Skip a
                # non-selected entry unless its entire relational record is
                # valid; never publish a half-built vehicle.
                continue

            crew_ids = []
            tankman_compact_descrs = {}
            for compact_descr in validated_tankmen:
                tankman_id = next_tankman_id
                next_tankman_id += 1
                crew_ids.append(tankman_id)
                tankman_compact_descrs[tankman_id] = compact_descr

            for item_type, items in record_inventory_items.items():
                published_items = inventory_items.setdefault(item_type, {})
                for compact_descr, count in items.items():
                    published_items[compact_descr] = max(
                        int(published_items.get(compact_descr, 0)),
                        int(count))
                    shop_item_prices[compact_descr] = {
                        'credits': 0, 'gold': 0,
                    }
                    unlock_item_compact_descrs.add(compact_descr)

            published_shells = inventory_items.setdefault(
                ITEM_TYPE_INDICES['shell'], {})
            for compact_descr, count in shell_catalog.items():
                published_shells[compact_descr] = max(
                    int(published_shells.get(compact_descr, 0)), int(count))
                shop_item_prices[compact_descr] = {
                    'credits': 0, 'gold': 0,
                }
                unlock_item_compact_descrs.add(compact_descr)

            vehicle_type_compact_descrs.add(vehicle_int_compact_descr)
            unlock_item_compact_descrs.add(vehicle_int_compact_descr)
            shop_item_prices[vehicle_int_compact_descr] = {
                'credits': 0, 'gold': 0,
            }
            vehicle_records.append({
                'id': len(vehicle_records) + 1,
                'compDescr': vehicle_compact_descr,
                'crew': crew_ids,
                'tankmen': tankman_compact_descrs,
                'repair': (0, descriptor.maxHealth),
                'lock': (0, 0),
                'shells': shells,
                'shellsLayout': {layout_key: list(shells)},
                'shellsLayoutIdx': layout_key,
                'settings': default_settings,
                'eqs': list(default_consumables),
                'eqsLayout': list(default_consumables),
                'inventoryItems': record_inventory_items,
                'vehicleTypeCompactDescr': vehicle_int_compact_descr,
            })

        if not vehicle_records:
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
            for descriptor in cache_accessor().values():
                try:
                    compact_descr = int(descriptor.compactDescr)
                except (TypeError, ValueError, AttributeError):
                    continue
                if not _offers_in_random_battle(descriptor):
                    continue
                published[compact_descr] = max(
                    int(published.get(compact_descr, 0)),
                    OFFLINE_ARTEFACT_STOCK)
                shop_item_prices[compact_descr] = {'credits': 0, 'gold': 0}
                unlock_item_compact_descrs.add(compact_descr)
            artefact_counts[item_type_name] = len(published)
            if not published:
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
                customization_count += 1
        if customization_count <= 0:
            raise ValueError('client customization catalogue is empty')

        # Preserve the historical selected-vehicle fields for consumers that
        # only need the configured tank.  ``vehicles`` carries the complete
        # garage and account_rpc expands every record into native inventory.
        result = dict(vehicle_records[0])
        result.update({
            'vehicles': vehicle_records,
            'inventoryItems': inventory_items,
            'shopItemPrices': shop_item_prices,
            'shopNationCount': len(nations.NAMES),
            'customizationItemCount': customization_count,
            'vehicleTypeCompactDescrs': vehicle_type_compact_descrs,
            'unlockItemCompactDescrs': unlock_item_compact_descrs,
            'optionalDeviceCount': artefact_counts['optionalDevice'],
            'equipmentCount': artefact_counts['equipment'],
        })
        # Overlay the saved garage last, so it wins over the stock fitting but
        # never over the current client's catalogue.
        if restore_saved:
            _restore_garage(result)
        return result
    except Exception:
        # _run_once owns startup error reporting.  Returning an empty snapshot
        # here would merely defer a deterministic descriptor problem until a
        # native Hangar consumer crashes with a misleading IndexError.
        raise


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
    global _player_ready_signaled
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
    _started = False
    if errors:
        return errors[0]
    return None


def _fail_startup(error, prefix='startup failed', worker_process=False):
    worker_process = bool(
        worker_process or
        _worker_presentation is not None or
        _client_mode == port_config.SIMULATION_WORKER_MODE)
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


def _install_worker_presentation():
    """Hide and mute only the explicitly launched simulation worker."""
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
        if _client_mode == port_config.SIMULATION_WORKER_MODE:
            # The worker needs a native lobby only as a safe map-lifecycle
            # bridge. It owns no Battle button, announcement or preference
            # profile and therefore cannot mutate the player's UI settings.
            _install_worker_session()
        else:
            # LobbyHeaderMeta stores a bound ``fightClick`` Function when its
            # Scaleform movie receives ``script = self``.  A class patch
            # installed after HANGAR_READY can repaint the button but cannot
            # replace that cached callback. Own it before lobby creation.
            _install_announcement_ui()
            _install_lan_session()
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


def _wait_for_lobby():
    global _callback_id, _deadline, _player_ready_signaled
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
            else:
                if not _player_ready_signaled:
                    if not _signal_player_ready():
                        raise RuntimeError(
                            'visible player ready marker was not published')
                    _player_ready_signaled = True
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
        if _client_mode == port_config.SIMULATION_WORKER_MODE:
            if not _client_guard_released:
                raise RuntimeError(
                    'simulation worker requires client guard release')
            # The worker still needs Account/Hangar to enter and leave native
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
        if requested_mode == port_config.SIMULATION_WORKER_MODE:
            if not _client_guard_released:
                _started = False
                sys.stdout.write(
                    '[Offline LAN 0.9.22] simulation worker startup refused: '
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
                requested_mode == port_config.SIMULATION_WORKER_MODE))


def fini():
    cleanup_error = _cleanup_runtime()
    if cleanup_error is not None:
        sys.stdout.write(
            '[Offline LAN 0.9.22] shutdown failed: %s\n' % cleanup_error)
