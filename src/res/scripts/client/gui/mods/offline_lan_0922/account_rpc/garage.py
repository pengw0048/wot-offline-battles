"""Mutable offline garage: fitting, ammunition layouts and crew skills.

The immutable snapshot that ``bootstrap._selected_vehicle`` builds is the
starting point.  This module keeps a mutable copy of exactly that shape, so a
full ``CMD_SYNC_DATA`` and a pushed diff both flow through the already
validated ``data.inventory`` shaping code instead of a second wire format.

Exact #1513 contracts used here, all from ``account_helpers/Inventory.pyc``:

- ``CMD_EQUIP_EQS`` carries ``[vehInvID] + [int(e) for e in eqs]``, where
  ``eqs`` is ``VehicleEquipment.getConsumablesIntCDs()``: three regular slots
  followed by the battle-booster slot;
- ``CMD_EQUIP_SHELLS`` carries ``[vehInvID] + [int(s) for s in shells]``;
- ``CMD_EQUIP_OPTDEV`` carries
  ``[shopRev, vehInvID, deviceCompDescr, slotIdx, int(isPaidRemoval)]``;
- ``CMD_SET_AND_FILL_LAYOUTS`` carries
  ``[shopRev, vehInvID, len(shellsLayout), *shellsLayout, equipmentType,
  len(eqsLayout), *eqsLayout]``, with a single ``0`` in place of a missing
  layout.  Both layouts are flat ``(compactDescr, count)`` pairs read by
  ``account_shared.LayoutIterator``, which takes ``abs(compactDescr)`` and
  reads the sign as "buy for the alternative price";
- ``CMD_TMAN_ADD_SKILL`` is a ``_doCmdInt3`` of ``(tmanInvID, skillIdx, 0)``.

Optional devices and modules live inside the vehicle's own compact descriptor,
so a mount rebuilds ``compDescr`` through ``VehicleDescr`` rather than storing a
parallel list.  This is why the 0.8.2 reference insists that the fitting and
customization writers share one live record: two independent writers would each
rebuild the descriptor from a stale copy and silently drop the other's change.
"""

import copy

EQUIPMENT_SLOT_COUNT = 3
# vehicles.NUM_EQUIPMENT_SLOTS in #1513: the three regular slots plus the
# battle-booster slot that every equipment payload still carries.
EQUIPMENT_PAYLOAD_SLOT_COUNT = 4
EQUIPMENT_TYPE_REGULAR = 0
TURRET_ITEM_TYPE = 3
GUN_ITEM_TYPE = 4
OPTIONAL_DEVICE_ITEM_TYPE = 9
SHELL_ITEM_TYPE = 10
EQUIPMENT_ITEM_TYPE = 11
# Shop.freeXPToTManXPRate in the pinned #1513 sync data.
FREE_XP_TO_TANKMAN_XP_RATE = 10

# items.components.c11n_constants.SeasonType in #1513.  Keep these values
# engine-free here; the stock parser still owns the descriptor validation.
CUSTOMIZATION_SEASONS = (1, 2, 4, 8, 15)
CUSTOMIZATION_ALL_SEASONS = 15


class GarageError(Exception):
    pass


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GarageError('expected an integer, got %r' % (value,))


def mirror_shells_layout(record):
    """Publish the loaded shells as the vehicle's own ammunition layout.

    #1513 reads ``shellsLayout[(turretCompDescr, gunCompDescr)]`` and falls back
    to the gun's default ammo, then warns through ``Vehicle.isAutoLoadFull``
    when a loaded count differs from that layout.  Offline resupply is instant,
    so the layout is always exactly what is loaded.
    """
    key = record.get('shellsLayoutIdx')
    record['shellsLayout'] = (
        {tuple(key): list(record.get('shells') or ())} if key else {})


def _layout_pairs(values, slot_limit=None):
    """Decode a flat #1513 layout into ``(compactDescr, count)`` pairs."""
    values = [_int(value) for value in (values or ())]
    if len(values) % 2:
        raise GarageError('a layout must contain descriptor/count pairs')
    pairs = [(abs(values[index]), values[index + 1])
             for index in range(0, len(values), 2)]
    if slot_limit is not None and len(pairs) > slot_limit:
        raise GarageError('a layout carries at most %d slots' % slot_limit)
    return pairs


class GarageState(object):
    """One mutable garage snapshot shared by every account command."""

    def __init__(self, snapshot, vehicles_module=None, tankmen_module=None,
                 customizations_module=None):
        if not isinstance(snapshot, dict):
            raise GarageError('garage snapshot must be a mapping')
        self._snapshot = copy.deepcopy(snapshot)
        self._vehicles = vehicles_module
        self._tankmen = tankmen_module
        self._customizations = customizations_module
        self._touched = set()
        self._touched_items = {}
        self.revision = 0

    def snapshot(self):
        return self._snapshot

    def _vehicles_module(self):
        if self._vehicles is None:
            from items import vehicles
            self._vehicles = vehicles
        return self._vehicles

    def _tankmen_module(self):
        if self._tankmen is None:
            from items import tankmen
            self._tankmen = tankmen
        return self._tankmen

    def _customizations_module(self):
        if self._customizations is None:
            from items import customizations
            self._customizations = customizations
        return self._customizations

    def _records(self):
        records = self._snapshot.get('vehicles')
        if isinstance(records, (list, tuple)) and records:
            return list(records)
        return [self._snapshot] if self._snapshot.get('compDescr') else []

    def _record(self, vehicle_inventory_id, touch=True):
        wanted = _int(vehicle_inventory_id)
        for record in self._records():
            if _int(record.get('id', 0)) == wanted:
                if touch:
                    self._touched.add(wanted)
                return record
        raise GarageError('unknown vehicle inventory id %d' % wanted)

    def _record_by_vehicle_type(self, vehicle_type_compact_descr,
                                touch=True):
        wanted = _int(vehicle_type_compact_descr)
        for record in self._records():
            if _int(record.get('vehicleTypeCompactDescr', 0)) == wanted:
                if touch:
                    self._touched.add(_int(record.get('id', 0)))
                return record
        raise GarageError('unknown vehicle type compact descriptor %d' %
                          wanted)

    def touched_vehicles(self):
        """Return the vehicle ids mutated since the last call, then reset."""
        touched = set(self._touched)
        self._touched = set()
        return touched

    def touched_items(self):
        """Return the owned items mutated since the last call, then reset."""
        touched = self._touched_items
        self._touched_items = {}
        return touched

    def _tankman_record(self, tankman_inventory_id):
        wanted = _int(tankman_inventory_id)
        for record in self._records():
            tankmen = record.get('tankmen')
            if isinstance(tankmen, dict) and wanted in tankmen:
                self._touched.add(_int(record.get('id', 0)))
                return record, wanted
        raise GarageError('unknown tankman inventory id %d' % wanted)

    def _own(self, record, compact_descr, item_type, count=1):
        """Own an item on the record and in the account-wide catalogue.

        ``data._validate_selected_vehicle`` requires the top-level catalogue to
        cover every per-record item at no less than the record's count, so both
        levels always move together.
        """
        if not compact_descr:
            return
        count = max(1, int(count))
        items = record.setdefault('inventoryItems', {})
        owned = items.setdefault(int(item_type), {})
        owned[compact_descr] = max(count, int(owned.get(compact_descr, 0)))
        self._publish_owned(compact_descr, item_type, owned[compact_descr])

    def _publish_owned(self, compact_descr, item_type, count):
        published = self._snapshot.setdefault('inventoryItems', {})
        owned = published.setdefault(int(item_type), {})
        owned[compact_descr] = max(int(count), int(owned.get(compact_descr, 0)))
        self._touched_items.setdefault(int(item_type), set()).add(compact_descr)

    # ---- ammunition -----------------------------------------------------

    def equip_shells(self, vehicle_inventory_id, shells):
        values = [_int(value) for value in (shells or ())]
        if len(values) % 2:
            raise GarageError('shells must be descriptor/count pairs')
        record = self._record(vehicle_inventory_id)
        record['shells'] = values
        mirror_shells_layout(record)
        # data._validate_selected_vehicle requires the shell inventory and the
        # flat pair list to agree, so both move together.
        pairs = {}
        for index in range(0, len(values), 2):
            pairs[values[index]] = values[index + 1]
        record.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = pairs
        for compact_descr, count in pairs.items():
            self._publish_owned(compact_descr, SHELL_ITEM_TYPE, count)
            self._price(compact_descr)
        self.revision += 1
        return record

    def _price(self, compact_descr):
        prices = self._snapshot.setdefault('shopItemPrices', {})
        if compact_descr and compact_descr not in prices:
            prices[compact_descr] = {'credits': 0, 'gold': 0}
        unlocks = self._snapshot.get('unlockItemCompactDescrs')
        # Only extend a set that already lists the garage: an empty set means
        # the snapshot opted out of the unlock check, and partially filling it
        # would start enforcing a constraint on items nobody validated.
        if isinstance(unlocks, set) and unlocks and compact_descr:
            unlocks.add(compact_descr)

    # ---- consumables ----------------------------------------------------

    def equip_equipments(self, vehicle_inventory_id, equipments):
        """Mount the regular consumables of one equipment payload."""
        values = [_int(value) for value in (equipments or ())]
        if len(values) > EQUIPMENT_PAYLOAD_SLOT_COUNT:
            raise GarageError('an equipment payload carries at most four slots')
        # The trailing battle-booster slot has no published counterpart.
        values = values[:EQUIPMENT_SLOT_COUNT]
        values += [0] * (EQUIPMENT_SLOT_COUNT - len(values))
        record = self._record(vehicle_inventory_id)
        record['eqs'] = values
        # Offline resupply is instant, so the vehicle is always at its layout.
        # Vehicle.isAutoEquipFull compares the two and warns when they differ.
        record['eqsLayout'] = list(values)
        for compact_descr in values:
            self._own(record, compact_descr, 11)
            self._price(compact_descr)
        self.revision += 1
        return record

    def set_layouts(self, vehicle_inventory_id, shells_layout=None,
                    equipment_type=EQUIPMENT_TYPE_REGULAR,
                    equipments_layout=None):
        """Store one layout and load the vehicle to it.

        Offline stock is unlimited, so the "fill" half of the request is the
        mount itself: the client shows ``eqs`` and ``shells``, not the layout.
        """
        record = self._record(vehicle_inventory_id)
        if shells_layout is not None:
            flat = []
            for compact_descr, count in _layout_pairs(shells_layout):
                flat.extend((compact_descr, count))
            self.equip_shells(vehicle_inventory_id, flat)
        if (equipments_layout is not None and
                _int(equipment_type) == EQUIPMENT_TYPE_REGULAR):
            pairs = _layout_pairs(
                equipments_layout, EQUIPMENT_PAYLOAD_SLOT_COUNT)
            slots = [compact_descr
                     for compact_descr, unused_count in pairs
                     ][:EQUIPMENT_SLOT_COUNT]
            self.equip_equipments(vehicle_inventory_id, slots)
        self.revision += 1
        return record

    # ---- optional devices and modules -----------------------------------

    def _rebuild_descriptor(self, record, mutate):
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(
                compactDescr=record['compDescr'])
        except Exception as error:
            raise GarageError('vehicle descriptor is unreadable: %s' % error)
        try:
            mutate(descriptor)
            record['compDescr'] = descriptor.makeCompactDescr()
            record['shellsLayoutIdx'] = (
                descriptor.turret.compactDescr, descriptor.gun.compactDescr)
        except Exception as error:
            raise GarageError('the client refused the fitting: %s' % error)
        mirror_shells_layout(record)
        return record

    def equip_optional_device(self, vehicle_inventory_id, device_compact_descr,
                              slot_index):
        record = self._record(vehicle_inventory_id)
        device_compact_descr = _int(device_compact_descr)
        slot_index = _int(slot_index)

        def mutate(descriptor):
            # Removing first makes a slot swap idempotent; #1513 rejects an
            # install into an occupied slot.
            try:
                descriptor.removeOptionalDevice(slot_index)
            except Exception:
                pass
            if device_compact_descr:
                descriptor.installOptionalDevice(
                    device_compact_descr, slot_index)

        self._rebuild_descriptor(record, mutate)
        self._own(record, device_compact_descr, 9)
        self._price(device_compact_descr)
        self.revision += 1
        return record

    def install_component(self, vehicle_inventory_id, compact_descr,
                          gun_compact_descr=0, position_index=0):
        """Install a module: this is the gun, turret, engine or chassis swap.

        #1513 ``installComponent`` dispatches on the gun, chassis, engine,
        radio and fuel tank and ends in ``assert False`` for a turret, so a
        turret goes through ``installTurret`` with the gun ``Inventory.
        equipTurret`` carries in the third integer of ``CMD_EQUIP``.
        """
        # Build the complete fitting on a detached record.  VehicleDescr can
        # accept the component and still refuse serialization, and default
        # ammunition discovery can fail after a gun changes.  Neither failure
        # may leave a new descriptor paired with stale or empty shells.
        record = self._record(vehicle_inventory_id, touch=False)
        staged = copy.deepcopy(record)
        compact_descr = _int(compact_descr)
        gun_compact_descr = _int(gun_compact_descr)
        position_index = _int(position_index)
        item_type = self._item_type(compact_descr)
        is_turret = item_type == TURRET_ITEM_TYPE

        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(
                compactDescr=staged['compDescr'])
            old_layout_key = (
                _int(descriptor.turret.compactDescr),
                _int(descriptor.gun.compactDescr))
            if is_turret:
                descriptor.installTurret(
                    compact_descr, gun_compact_descr, position_index)
            else:
                descriptor.installComponent(compact_descr, position_index)
            serialized = descriptor.makeCompactDescr()
            if not serialized:
                raise ValueError('the fitted descriptor is empty')
            # Parse the serialized result once before publishing it.  This is
            # the same constructor bootstrap and battle entry will use, and it
            # catches a component combination that only fails on round-trip.
            verified = vehicles.VehicleDescr(compactDescr=serialized)
            new_layout_key = (
                _int(verified.turret.compactDescr),
                _int(verified.gun.compactDescr))
            expected_layout_key = (
                _int(descriptor.turret.compactDescr),
                _int(descriptor.gun.compactDescr))
            if is_turret and expected_layout_key[0] != compact_descr:
                raise ValueError('the selected turret was not installed')
            if (is_turret and gun_compact_descr and
                    expected_layout_key[1] != gun_compact_descr):
                raise ValueError('the selected gun was not installed')
            if (item_type == GUN_ITEM_TYPE and
                    expected_layout_key[1] != compact_descr):
                raise ValueError('the selected gun was not installed')
            if new_layout_key != expected_layout_key:
                raise ValueError('the fitted descriptor did not round-trip')
        except GarageError:
            raise
        except Exception as error:
            raise GarageError('the client refused the fitting: %s' % error)

        staged['compDescr'] = serialized
        staged['shellsLayoutIdx'] = new_layout_key
        if new_layout_key != old_layout_key:
            shells = self._validated_default_ammo(vehicles, verified)
            staged['shells'] = shells
            staged.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = dict(
                (shells[index], shells[index + 1])
                for index in range(0, len(shells), 2))
        mirror_shells_layout(staged)

        # Everything above is validation-only.  Publish the descriptor,
        # layout and ammunition together once no native operation can fail.
        record.clear()
        record.update(staged)
        self._touched.add(_int(vehicle_inventory_id))
        self._price(compact_descr)
        if gun_compact_descr:
            self._price(gun_compact_descr)
        if new_layout_key != old_layout_key:
            for index in range(0, len(shells), 2):
                self._publish_owned(
                    shells[index], SHELL_ITEM_TYPE, shells[index + 1])
                self._price(shells[index])
        self.revision += 1
        return record

    def _validated_default_ammo(self, vehicles, descriptor):
        """Return non-empty default ammo belonging to ``descriptor.gun``."""
        try:
            shells = [_int(value) for value in
                      vehicles.getDefaultAmmoForGun(descriptor.gun)]
        except GarageError:
            raise
        except Exception as error:
            raise GarageError(
                'compatible ammunition is unavailable: %s' % error)
        if not shells or len(shells) % 2:
            raise GarageError(
                'compatible ammunition must contain descriptor/count pairs')

        compatible = set()
        for shot in (getattr(descriptor.gun, 'shots', ()) or ()):
            shell = getattr(shot, 'shell', None)
            compact_descr = getattr(shell, 'compactDescr', 0)
            if compact_descr:
                compatible.add(_int(compact_descr))
        if not compatible:
            raise GarageError('the fitted gun has no compatible ammunition')

        total = 0
        seen = set()
        for index in range(0, len(shells), 2):
            compact_descr = shells[index]
            count = shells[index + 1]
            if compact_descr <= 0 or count < 0:
                raise GarageError(
                    'compatible ammunition has an invalid descriptor or count')
            if compact_descr in seen:
                raise GarageError(
                    'compatible ammunition contains a duplicate shell')
            if compact_descr not in compatible:
                raise GarageError(
                    'default ammunition does not fit the selected gun')
            seen.add(compact_descr)
            total += count
        if total <= 0:
            raise GarageError('the selected gun has no loaded ammunition')

        maximum = getattr(descriptor.gun, 'maxAmmo', 0)
        try:
            maximum = int(maximum or 0)
        except (TypeError, ValueError):
            maximum = 0
        if maximum > 0 and total > maximum:
            raise GarageError('default ammunition exceeds the gun capacity')
        return shells

    # ---- purchases and settings -----------------------------------------

    def buy_item(self, compact_descr, count=1):
        """Own more of one item.

        Offline balances are unlimited: the shop publishes every item at zero
        price, so deducting credits would always subtract nothing. Ownership is
        the only part of a purchase that has an observable effect here.
        """
        compact_descr = _int(compact_descr)
        if not compact_descr:
            raise GarageError('a purchase needs an item')
        count = max(1, _int(count))
        item_type = self._item_type(compact_descr)
        existing = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        self._publish_owned(
            compact_descr, item_type,
            int(existing.get(compact_descr, 0)) + count)
        self._price(compact_descr)
        self.revision += 1
        return compact_descr

    def _item_type(self, compact_descr):
        vehicles = self._vehicles_module()
        resolver = getattr(vehicles, 'getTypeOfCompactDescr', None)
        if resolver is None:
            from items import getTypeOfCompactDescr as resolver
        try:
            return int(resolver(compact_descr))
        except Exception as error:
            raise GarageError('unknown item %d: %s' % (compact_descr, error))

    def buy_and_equip_item(self, vehicle_inventory_id, compact_descr,
                           slot_index=0, gun_compact_descr=0):
        """Own one item and mount it on the vehicle in the same request."""
        compact_descr = _int(compact_descr)
        item_type = self._item_type(compact_descr)
        if item_type == OPTIONAL_DEVICE_ITEM_TYPE:
            record = self.equip_optional_device(
                vehicle_inventory_id, compact_descr, slot_index)
        elif item_type == EQUIPMENT_ITEM_TYPE:
            record = self._record(vehicle_inventory_id)
            slots = list(record.get('eqs') or [0] * EQUIPMENT_SLOT_COUNT)
            slots += [0] * (EQUIPMENT_SLOT_COUNT - len(slots))
            index = _int(slot_index)
            if not 0 <= index < EQUIPMENT_SLOT_COUNT:
                raise GarageError('a vehicle has three equipment slots')
            slots[index] = compact_descr
            record = self.equip_equipments(vehicle_inventory_id, slots)
        else:
            # Every remaining owned type is a vehicle module.  A turret buy
            # carries its selected gun in the sixth wire value.
            record = self.install_component(
                vehicle_inventory_id, compact_descr, gun_compact_descr,
                slot_index)
        # Mount first so a refused descriptor or ammunition set cannot leave
        # behind ownership from a failed buy-and-equip request.
        self.buy_item(compact_descr, 1)
        return record

    def change_vehicle_setting(self, vehicle_inventory_id, setting, is_on):
        """Set or clear one bit of a vehicle's settings mask.

        ``setting`` is already a ``VEHICLE_SETTINGS_FLAG`` value: #1513's
        VehicleSettingsProcessor sends AUTO_REPAIR (2) itself, not its index.
        """
        record = self._record(vehicle_inventory_id)
        bit = max(0, _int(setting))
        current = 0
        try:
            current = int(record.get('settings', 0) or 0)
        except (TypeError, ValueError):
            current = 0
        record['settings'] = (current | bit) if _int(is_on) else (
            current & ~bit)
        self.revision += 1
        return record

    # ---- customization 2.0 ---------------------------------------------

    def apply_outfit(self, vehicle_inventory_id, season, outfit_descr):
        """Validate and atomically store one #1513 outfit compact descriptor.

        The client already serializes the editor state.  Re-parsing and
        re-serializing it through ``items.customizations`` is deliberately the
        only accepted path: the offline mod never splices the binary format.
        """
        season = _int(season)
        if season not in CUSTOMIZATION_SEASONS:
            raise GarageError('unknown customization season %d' % season)
        record = self._record(vehicle_inventory_id, touch=False)
        customizations = self._customizations_module()
        try:
            outfit = customizations.parseOutfitDescr(outfit_descr)
            canonical = outfit.makeCompDescr()
            # Exercise the parser once more on exactly what will be persisted.
            customizations.parseOutfitDescr(canonical)
        except Exception as error:
            raise GarageError('the client refused the outfit: %s' % error)

        staged = copy.deepcopy(record)
        outfits = staged.setdefault('outfits', {})
        # Vehicle.getOutfit prioritizes a styled outfit.  Leaving the previous
        # ALL-season style beside a newly applied custom season would make the
        # accepted CMD 119 invisible in both garage and battle.
        if season != CUSTOMIZATION_ALL_SEASONS:
            outfits.pop(CUSTOMIZATION_ALL_SEASONS, None)
        outfits[season] = (canonical, True)
        record.clear()
        record.update(staged)
        self._touched.add(_int(vehicle_inventory_id))
        self.revision += 1
        return record

    def apply_style(self, vehicle_inventory_id, style_id):
        """Store a stock style reference under SeasonType.ALL.

        #1513's style command carries an id rather than an outfit descriptor;
        ``CustomizationOutfit`` is the stock serializer for that reference.
        """
        style_id = _int(style_id)
        if style_id <= 0:
            raise GarageError('a style request needs a positive style id')
        try:
            styles = self._vehicles_module().g_cache.customization20().styles
            if style_id not in styles:
                raise ValueError('unknown style id %d' % style_id)
            customizations = self._customizations_module()
            outfit = customizations.CustomizationOutfit(styleId=style_id)
            canonical = outfit.makeCompDescr()
            customizations.parseOutfitDescr(canonical)
        except Exception as error:
            raise GarageError('the client refused the style: %s' % error)

        record = self._record(vehicle_inventory_id, touch=False)
        staged = copy.deepcopy(record)
        # Vehicle._parseStyledOutfits expands an ALL-season style into the
        # arena-specific outfits supplied by the style definition.
        staged['outfits'] = {
            CUSTOMIZATION_ALL_SEASONS: (canonical, True)}
        record.clear()
        record.update(staged)
        self._touched.add(_int(vehicle_inventory_id))
        self.revision += 1
        return record

    def buy_customizations(self, vehicle_inventory_id, purchases):
        """Own ``(intCompactDescr, count)`` pairs for one vehicle type."""
        record = self._record(vehicle_inventory_id)
        pairs = _layout_pairs(purchases)
        vehicle_type = _int(record.get('vehicleTypeCompactDescr', 0))
        if vehicle_type <= 0:
            raise GarageError('the vehicle has no type compact descriptor')
        parsed = []
        for compact_descr, count in pairs:
            if count <= 0:
                raise GarageError('a customization purchase needs a count')
            custom_type, item_id = self._customization_identity(compact_descr)
            parsed.append((custom_type, item_id, count))

        owned = self._snapshot.setdefault('customizationItems', {})
        staged = copy.deepcopy(owned)
        for custom_type, item_id, count in parsed:
            buckets = staged.setdefault(custom_type, {}).setdefault(
                item_id, {})
            buckets[vehicle_type] = int(buckets.get(vehicle_type, 0)) + count
        self._snapshot['customizationItems'] = staged
        self.revision += 1
        return record

    def sell_customization(self, vehicle_inventory_id, compact_descr, count):
        """Remove a vehicle-bound customization item using CMD 117 fields."""
        record = self._record(vehicle_inventory_id)
        count = _int(count)
        if count <= 0:
            raise GarageError('a customization sale needs a count')
        custom_type, item_id = self._customization_identity(compact_descr)
        vehicle_type = _int(record.get('vehicleTypeCompactDescr', 0))
        owned = self._snapshot.setdefault('customizationItems', {})
        staged = copy.deepcopy(owned)
        buckets = staged.get(custom_type, {}).get(item_id, {})
        current = int(buckets.get(vehicle_type, 0))
        if current < count:
            raise GarageError('not enough vehicle-bound customizations to sell')
        remaining = current - count
        if remaining:
            buckets[vehicle_type] = remaining
        else:
            buckets.pop(vehicle_type, None)
        self._snapshot['customizationItems'] = staged
        self.revision += 1
        return record

    def _customization_identity(self, compact_descr):
        compact_descr = _int(compact_descr)
        if compact_descr <= 0:
            raise GarageError('a customization needs a compact descriptor')
        try:
            parser = getattr(
                self._customizations_module(), 'parseIntCompactDescr', None)
            if parser is None:
                from items import parseIntCompactDescr as parser
            # items.parseIntCompactDescr returns
            # (GUI_ITEM_TYPE.CUSTOMIZATION, customizationType, itemID).
            value = parser(compact_descr)
            custom_type, item_id = value[1], value[2]
            custom_type = int(custom_type)
            item_id = int(item_id)
        except Exception as error:
            raise GarageError('unknown customization %d: %s' % (
                compact_descr, error))
        if custom_type <= 0 or item_id <= 0:
            raise GarageError('unknown customization %d' % compact_descr)
        return custom_type, item_id

    # ---- crew -----------------------------------------------------------

    def add_tankman_skill(self, tankman_inventory_id, skill_index):
        record, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        names = getattr(tankmen, 'SKILL_NAMES', ())
        try:
            skill_name = names[_int(skill_index)]
        except (IndexError, TypeError):
            raise GarageError('unknown crew skill index %r' % (skill_index,))
        try:
            descriptor = tankmen.TankmanDescr(record['tankmen'][tankman_id])
            descriptor.addSkill(skill_name)
            record['tankmen'][tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the crew skill: %s' % error)
        self.revision += 1
        return record

    def drop_tankman_skills(self, tankman_inventory_id):
        record, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(record['tankmen'][tankman_id])
            descriptor.dropSkills(1.0, False)
            record['tankmen'][tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the skill reset: %s' % error)
        self.revision += 1
        return record

    def train_tankman(self, tankman_inventory_id, free_xp):
        """Convert the requested free XP into crew XP at the #1513 rate."""
        record, tankman_id = self._tankman_record(tankman_inventory_id)
        amount = _int(free_xp)
        if amount <= 0:
            raise GarageError('crew training XP must be positive')
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(record['tankmen'][tankman_id])
            descriptor.addXP(amount * FREE_XP_TO_TANKMAN_XP_RATE)
            record['tankmen'][tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused crew training: %s' % error)
        self.revision += 1
        return record

    def award_battle_crew_xp(self, vehicle_type_compact_descr, battle_xp,
                             xp_to_tankman_flag):
        """Apply one battle's crew XP and return the durable award summary.

        Every crew member receives the battle XP.  On an elite vehicle with
        accelerated training enabled, the vehicle XP is diverted to the least
        experienced crew member as one additional equal award.  The offline
        account publishes every vehicle as elite, so the persisted vehicle
        setting is the remaining stock eligibility check.
        """
        amount = _int(battle_xp)
        if amount < 0:
            raise GarageError('battle crew XP cannot be negative')
        record = self._record_by_vehicle_type(
            vehicle_type_compact_descr, touch=False)
        crew_ids = list(record.get('crew') or ())
        tankman_rows = record.get('tankmen')
        if not crew_ids or not isinstance(tankman_rows, dict):
            raise GarageError('vehicle has no complete crew')

        tankmen = self._tankmen_module()
        descriptors = []
        for slot, tankman_id in enumerate(crew_ids):
            try:
                descriptor = tankmen.TankmanDescr(tankman_rows[tankman_id])
                total_xp = int(descriptor.totalXP())
            except Exception as error:
                raise GarageError(
                    'the client refused battle crew XP: %s' % error)
            descriptors.append((slot, tankman_id, total_xp, descriptor))

        try:
            setting_mask = int(record.get('settings', 0) or 0)
            accelerated = bool(
                setting_mask & max(0, _int(xp_to_tankman_flag)))
        except (TypeError, ValueError):
            accelerated = False
        weakest = min(descriptors, key=lambda row: (row[2], row[0]))
        try:
            for unused_slot, unused_id, unused_total, descriptor in descriptors:
                descriptor.addXP(amount)
            if accelerated:
                weakest[3].addXP(amount)
            for unused_slot, tankman_id, unused_total, descriptor in descriptors:
                tankman_rows[tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused battle crew XP: %s' % error)

        vehicle_id = _int(record.get('id', 0))
        self._touched.add(vehicle_id)
        self.revision += 1
        return {
            'accelerated': accelerated,
            'vehicle_id': vehicle_id,
            'weakest_tankman_id': weakest[1] if accelerated else 0,
        }
