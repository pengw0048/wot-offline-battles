"""Build one garage vehicle record from the exact client descriptors.

``bootstrap`` builds the whole owned garage at startup and ``account_rpc``
builds one more record when the player buys a vehicle. Both need the identical
crew generation, module catalogue, ammunition closure and record shape, so the
factory lives here rather than in either caller.

Nothing in this module touches BigWorld: every client dependency arrives as an
already imported ``items`` module or an already built descriptor.
"""

NEW_SKILL_SLOTS = 8
_NEW_SKILL_XP = {}


def default_vehicle_settings():
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


def top_up_new_skill_slots(tankmen, descriptor):
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


def with_new_skill_slots(tankmen, descriptor):
    """Return the tankman with NEW_SKILL_SLOTS skills left for the player.

    No skill is chosen here; the player picks all of them.  The caller already
    unpacked this descriptor to validate the crew slot, so it is reused.
    """
    top_up_new_skill_slots(tankmen, descriptor)
    return descriptor.makeCompactDescr()


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


def offers_in_random_battle(descriptor):
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


def vehicle_type_modules(descriptor):
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


def vehicle_type_guns(descriptor):
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


_DEFAULT_CONSUMABLE_NAMES = ('autoExtinguishers', 'largeMedkit',
                             'largeRepairkit')


def default_consumables(vehicles):
    """Return the compact descriptors of the three mounted consumables.

    They come from the cache rather than from a rebuilt id, because #1513
    gives an artefact ``nations.NONE_INDEX`` instead of a real nation.
    """
    ids = vehicles.g_cache.equipmentIDs()
    equipments = vehicles.g_cache.equipments()
    slots = []
    for name in _DEFAULT_CONSUMABLE_NAMES:
        descriptor = equipments.get(ids.get(name))
        if descriptor is None or not offers_in_random_battle(descriptor):
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


def build_record(vehicles, tankmen, item_type_indices, type_id,
                 inventory_id, next_tankman_id, settings, consumables,
                 descriptor=None, top_modules=True, role_level=None,
                 own_researchable_modules=True):
    """Return one garage vehicle record and the catalogue it implies.

    ``top_modules`` fits the vehicle the way the historical sandbox garage
    does.  A vehicle bought in a career arrives stock, because retail sells the
    stock fitting and the rest is research.  ``own_researchable_modules`` is
    the same distinction for ownership: the sandbox owns every module the type
    can carry, a career owns only what is installed.

    The caller supplies ``inventory_id`` so ids stay durable across restarts,
    and ``next_tankman_id`` so crew ids never collide with an existing crew.
    """
    from gui.mods.offline_lan_0922 import vehicle_blacklist
    from gui.mods.offline_lan_0922.vehicle_configuration import (
        install_top_modules, is_standard_battle_vehicle)

    if descriptor is None:
        descriptor = vehicles.VehicleDescr(typeID=type_id)
    nation_id, vehicle_type_id = descriptor.type.id
    if not is_standard_battle_vehicle(descriptor.type):
        raise ValueError('vehicle type is not available in standard battles')
    if vehicle_blacklist.is_unusable(descriptor.type.name):
        raise ValueError(
            'this client has no resources for %s: %s' % (
                descriptor.type.name, ', '.join(
                    vehicle_blacklist.missing_resources(
                        descriptor.type.name))))
    if top_modules:
        install_top_modules(descriptor)

    # A fresh offline crew starts without preselected perks.  The free-XP
    # budget below exposes the requested empty slots, so the player remains the
    # sole owner of every skill choice.
    if role_level is None:
        role_level = tankmen.MAX_SKILL_LEVEL
    skills_mask = tankmen.getSkillsMask(())
    crew_compact_descrs = list(tankmen.generateTankmen(
        nation_id, vehicle_type_id, descriptor.type.crewRoles,
        False, role_level, skills_mask, False))
    if (not crew_compact_descrs or
            len(crew_compact_descrs) != len(descriptor.type.crewRoles)):
        raise ValueError('generated crew does not match vehicle crew slots')

    validated_tankmen = []
    for index, compact_descr in enumerate(crew_compact_descrs):
        tankman_descr = tankmen.TankmanDescr(compact_descr)
        roles = descriptor.type.crewRoles[index]
        if (tankman_descr.nationID != nation_id or
                tankman_descr.vehicleTypeID != vehicle_type_id or
                tankman_descr.role != roles[0]):
            raise ValueError(
                'generated tankman does not match vehicle crew slot')
        validated_tankmen.append(with_new_skill_slots(tankmen, tankman_descr))

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
        item_type = item_type_indices[item_type_name]
        record_inventory_items.setdefault(item_type, {})[compact_descr] = 1
    if own_researchable_modules:
        # Publish every module this vehicle type can carry, not only the stock
        # fitting, so its research tree shows them owned instead of costing XP.
        # The lists come from the vehicle's own type, so a premium hull still
        # offers only its own modules.
        for item_type_name, compact_descr in vehicle_type_modules(descriptor):
            item_type = item_type_indices[item_type_name]
            owned = record_inventory_items.setdefault(item_type, {})
            owned[compact_descr] = max(1, int(owned.get(compact_descr, 0)))

    shells = list(vehicles.getDefaultAmmoForGun(descriptor.gun))
    if not shells or len(shells) % 2:
        raise ValueError('default ammo must contain descriptor/count pairs')
    record_shell_items = record_inventory_items.setdefault(
        item_type_indices['shell'], {})
    for index in range(0, len(shells), 2):
        record_shell_items[shells[index]] = shells[index + 1]

    # Saved fittings may mount any gun in the vehicle's research tree.
    # Catalogue every such gun's shells at account level, while the
    # per-vehicle inventory above remains exactly the ammunition currently
    # loaded.  Without this closure an alternate gun saved valid shells that
    # disappeared from the next bootstrap's prices and Account sync aborted.
    shell_catalog = {}
    for gun in vehicle_type_guns(descriptor):
        gun_shells = list(vehicles.getDefaultAmmoForGun(gun))
        if not gun_shells or len(gun_shells) % 2:
            raise ValueError('gun ammo must contain descriptor/count pairs')
        for index in range(0, len(gun_shells), 2):
            compact_descr = int(gun_shells[index])
            count = int(gun_shells[index + 1])
            if compact_descr <= 0 or count < 0:
                raise ValueError(
                    'gun ammo has an invalid descriptor or count')
            shell_catalog[compact_descr] = max(
                count, int(shell_catalog.get(compact_descr, 0)))

    # The exact key #1513 Vehicle.shellsLayoutIdx looks up.
    layout_key = (descriptor.turret.compactDescr, descriptor.gun.compactDescr)
    vehicle_compact_descr = descriptor.makeCompactDescr()
    if not vehicle_compact_descr or descriptor.maxHealth <= 0:
        raise ValueError('vehicle descriptor is not garage-ready')
    vehicle_int_compact_descr = vehicles.makeIntCompactDescrByID(
        'vehicle', nation_id, vehicle_type_id)

    crew_ids = []
    tankman_compact_descrs = {}
    for compact_descr in validated_tankmen:
        crew_ids.append(next_tankman_id)
        tankman_compact_descrs[next_tankman_id] = compact_descr
        next_tankman_id += 1

    record = {
        'id': int(inventory_id),
        'compDescr': vehicle_compact_descr,
        'crew': crew_ids,
        'tankmen': tankman_compact_descrs,
        'repair': (0, descriptor.maxHealth),
        'lock': (0, 0),
        'shells': shells,
        'shellsLayout': {layout_key: list(shells)},
        'shellsLayoutIdx': layout_key,
        'settings': settings,
        'eqs': list(consumables),
        'eqsLayout': list(consumables),
        'inventoryItems': record_inventory_items,
        'vehicleTypeCompactDescr': vehicle_int_compact_descr,
    }
    return {
        'record': record,
        'shellCatalog': shell_catalog,
        'vehicleTypeCompactDescr': vehicle_int_compact_descr,
        'nextTankmanID': next_tankman_id,
        'descriptor': descriptor,
    }
