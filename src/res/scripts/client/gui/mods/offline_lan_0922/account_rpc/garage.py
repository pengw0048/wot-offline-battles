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

import contextlib
import copy
import math
from gui.mods.offline_lan_0922.vehicle_records import (
    MODULE_ATTRIBUTES, mounted_module_items)

EQUIPMENT_SLOT_COUNT = 3
# vehicles.NUM_EQUIPMENT_SLOTS in #1513: the three regular slots plus the
# battle-booster slot that every equipment payload still carries.
EQUIPMENT_PAYLOAD_SLOT_COUNT = 4
EQUIPMENT_TYPE_REGULAR = 0
VEHICLE_ITEM_TYPE = 1
TURRET_ITEM_TYPE = 3
GUN_ITEM_TYPE = 4
OPTIONAL_DEVICE_ITEM_TYPE = 9
SHELL_ITEM_TYPE = 10
EQUIPMENT_ITEM_TYPE = 11
ACCOUNT_ITEM_TYPES = (OPTIONAL_DEVICE_ITEM_TYPE, EQUIPMENT_ITEM_TYPE)
# Every physical copy counts, whether it is mounted or in the depot.
STOCKED_ITEM_TYPES = tuple(row[0] for row in MODULE_ATTRIBUTES) + (9, 10, 11)
# The two item types #1513's shop publishes as buyable for credits even when
# their catalogue price is gold: premium rounds and premium consumables.
CREDIT_PRICED_GOLD_TYPES = (SHELL_ITEM_TYPE, EQUIPMENT_ITEM_TYPE)
# Shop.freeXPToTManXPRate in the pinned #1513 sync data.
FREE_XP_TO_TANKMAN_XP_RATE = 10

# items.components.c11n_constants.SeasonType in #1513.  Keep these values
# engine-free here; the stock parser still owns the descriptor validation.
CUSTOMIZATION_SEASONS = (1, 2, 4, 8, 15)
CUSTOMIZATION_ALL_SEASONS = 15


# items/__init__ ITEM_TYPE_NAMES: the installable modules are the only items a
# research tree gates. Vehicles are gated by their own unlock entry.
RESEARCHED_ITEM_TYPES = (2, 3, 4, 5, 6, 7)
# items/vehicles.py uses this literal for IS_CLIENT while reading the vehicle
# list, so a sale returns half of what an item cost.
SELL_PRICE_FACTOR = 0.5
# Offline policy, not #1513 values: the gold exchange rate, the slot price and
# the berth block are all server state the client never receives. 400 credits
# per gold and 300 gold per slot are the long-published retail numbers.
GOLD_EXCHANGE_RATE = 400
GARAGE_SLOT_GOLD_PRICE = 300
BARRACKS_BERTH_GOLD_PRICE = 300
BARRACKS_BERTH_COUNT = 16
# Shop.freeXPConversion in the pinned #1513 sync data: 25 vehicle experience
# becomes 25 free experience for one gold.
FREE_XP_CONVERSION = (25, 1)


class GarageError(Exception):
    pass


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GarageError('expected an integer, got %r' % (value,))


def mirror_shells_layout(record):
    """Recover a legacy save's ammunition layout from its loaded shells.

    #1513 reads ``shellsLayout[(turretCompDescr, gunCompDescr)]`` and falls back
    to the gun's default ammo, then warns through ``Vehicle.isAutoLoadFull``
    when a loaded count differs from that layout. Current saves keep the target
    separately so battle consumption must not overwrite it with the remainder.
    """
    key = record.get('shellsLayoutIdx')
    previous = (record.get('shellsLayout') or {}).get(tuple(key or ()), ())
    alternative = set(abs(value) for value in previous[::2] if value < 0)
    loaded = list(record.get('shells') or ())
    for index in range(0, len(loaded), 2):
        if loaded[index] in alternative:
            loaded[index] = -loaded[index]
    record['shellsLayout'] = {tuple(key): loaded} if key else {}


def _layout_pairs(values, slot_limit=None, preserve_currency=False):
    """Decode a flat #1513 layout into ``(compactDescr, count)`` pairs."""
    values = [_int(value) for value in (values or ())]
    if len(values) % 2:
        raise GarageError('a layout must contain descriptor/count pairs')
    pairs = [((values[index] if preserve_currency else abs(values[index])),
              values[index + 1])
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
        self._touched_tankmen = set()
        self._touched_recycled = set()
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

    def touched_tankmen(self):
        """Return and clear the crew ids a sale removed from the account."""
        touched = set(self._touched_tankmen)
        self._touched_tankmen = set()
        return touched

    def touched_items(self):
        """Return the owned items mutated since the last call, then reset."""
        touched = self._touched_items
        self._touched_items = {}
        return touched

    def touched_recycled(self):
        """Return and clear the recycle-bin rows a command moved."""
        touched = set(self._touched_recycled)
        self._touched_recycled = set()
        return touched

    def _touch_item_changes(self, before, after):
        """Publish depot changes caused by loading or unloading owned copies."""
        for item_type in STOCKED_ITEM_TYPES:
            previous = before.get(item_type, {})
            current = after.get(item_type, {})
            changed = set(compact_descr for compact_descr in
                          set(previous) | set(current)
                          if _int(previous.get(compact_descr, 0)) !=
                          _int(current.get(compact_descr, 0)))
            if changed:
                self._touched_items.setdefault(item_type, set()).update(changed)

    def _tankman_record(self, tankman_inventory_id):
        """Return the mapping that holds one crew member's descriptor.

        A crew member is either in a vehicle or in the barracks, and both are
        trained and taught skills the same way, so the caller gets whichever
        mapping owns them rather than the vehicle record.
        """
        wanted = _int(tankman_inventory_id)
        for record in self._records():
            tankmen = record.get('tankmen')
            if isinstance(tankmen, dict) and wanted in tankmen:
                self._touched.add(_int(record.get('id', 0)))
                return tankmen, wanted
        barracks = self._barracks()
        if wanted in barracks:
            self._touched_tankmen.add(wanted)
            return barracks, wanted
        raise GarageError('unknown tankman inventory id %d' % wanted)

    def _publish_owned(self, compact_descr, item_type, count):
        published = self._snapshot.setdefault('inventoryItems', {})
        owned = published.setdefault(int(item_type), {})
        owned[compact_descr] = max(int(count), int(owned.get(compact_descr, 0)))
        self._touched_items.setdefault(int(item_type), set()).add(compact_descr)

    def _stock_owned(self, compact_descr, item_type, count):
        """Publish the stock that arrives with a newly built vehicle.

        The vehicle's installed modules and carried supplies are new physical
        copies. Existing copies on another vehicle remain owned separately.
        """
        item_type = int(item_type)
        if item_type not in STOCKED_ITEM_TYPES:
            self._publish_owned(compact_descr, item_type, count)
            return
        owned = self._snapshot.setdefault(
            'inventoryItems', {}).setdefault(item_type, {})
        self._set_owned(
            compact_descr, item_type,
            _int(owned.get(compact_descr, 0)) + _int(count))

    @contextlib.contextmanager
    def _transaction(self):
        """Run a multi-step command on a copy, or leave the garage untouched.

        A command that charges more than once cannot fail halfway: the client
        is told one result for the whole command, and ``_fitting`` has no way
        to undo the part that already succeeded.
        """
        staged = copy.deepcopy(self._snapshot)
        touched = set(self._touched)
        touched_items = dict(
            (item_type, set(items))
            for item_type, items in self._touched_items.items())
        touched_tankmen = set(self._touched_tankmen)
        touched_recycled = set(self._touched_recycled)
        revision = self.revision
        try:
            yield
        except Exception:
            self._snapshot.clear()
            self._snapshot.update(staged)
            self._touched = touched
            self._touched_items = touched_items
            self._touched_tankmen = touched_tankmen
            self._touched_recycled = touched_recycled
            self.revision = revision
            raise

    # ---- ammunition -----------------------------------------------------

    def equip_shells(self, vehicle_inventory_id, shells):
        """Load one vehicle, buying whatever rounds the account is short of.

        #1513 calls this command SET_AND_FILL_LAYOUTS for a reason: setting a
        layout the depot cannot fill buys the difference.  The account count
        covers every round the garage holds, loaded ones included, so what has
        to be bought is whatever the new layouts need above it.
        """
        # account_shared.LayoutIterator: a negative descriptor selects
        # itemAltPrice (credits); positive keeps itemPrice (possibly gold).
        layout = [_int(value) for value in (shells or ())]
        values = [abs(value) if index % 2 == 0 else value
                  for index, value in enumerate(layout)]
        alternative_items = set(abs(value) for value in layout[::2]
                                if value < 0)
        if len(values) % 2:
            raise GarageError('shells must be descriptor/count pairs')
        record = self._record(vehicle_inventory_id, touch=False)
        # data._validate_selected_vehicle requires the shell inventory and the
        # flat pair list to agree, so both move together.
        pairs = {}
        for index in range(0, len(values), 2):
            pairs[values[index]] = values[index + 1]
        others = [row for row in self._records()
                  if _int(row.get('id', 0)) != _int(record.get('id', 0))]
        # Read the stock before anything is published: the loops below raise
        # the very counts this arithmetic is against.
        owned = dict(self._snapshot.get('inventoryItems', {}).get(
            SHELL_ITEM_TYPE, {}))
        purchase = {}
        for compact_descr, count in pairs.items():
            needed = _int(count) + self._mounted(
                compact_descr, SHELL_ITEM_TYPE, others)
            missing = needed - _int(owned.get(compact_descr, 0))
            if missing > 0:
                purchase[compact_descr] = missing
        # Every refusal happens before the first round is loaded, so a load
        # the account cannot pay for leaves the vehicle exactly as it was.
        self._charge(self._shells_cost(purchase, alternative_items))
        self._touch_item_changes(
            {SHELL_ITEM_TYPE: record.get('inventoryItems', {}).get(
                SHELL_ITEM_TYPE, {})}, {SHELL_ITEM_TYPE: pairs})
        self._touched.add(_int(record.get('id', 0)))
        record['shells'] = values
        key = record.get('shellsLayoutIdx')
        record['shellsLayout'] = {tuple(key): layout} if key else {}
        record.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = pairs
        for compact_descr in pairs:
            self._publish_owned(
                compact_descr, SHELL_ITEM_TYPE,
                _int(owned.get(compact_descr, 0)) +
                purchase.get(compact_descr, 0))
            self._price(compact_descr)
        self.revision += 1
        return record

    def _shells_cost(self, purchase, alternative_items=()):
        """Price rounds in the currency selected by #1513 LayoutIterator."""
        total = {}
        for compact_descr, count in dict(purchase or {}).items():
            price = self._item_cost(compact_descr, count)
            if compact_descr in alternative_items:
                price = self._in_credits(price, SHELL_ITEM_TYPE)
            for currency, value in price.items():
                total[currency] = _int(total.get(currency, 0)) + _int(value)
        return total

    def _price(self, compact_descr):
        """Make sure one item the garage publishes also carries a price.

        The snapshot arrives with the whole baked catalogue, so this only
        covers an item the catalogue did not know: publishing it at no price
        keeps the snapshot self-consistent instead of failing a mount over a
        baking gap.  It deliberately no longer grants an unlock; research owns
        that, and an item can only be mounted once it has been researched and
        bought.
        """
        prices = self._snapshot.setdefault('shopItemPrices', {})
        if compact_descr and compact_descr not in prices:
            prices[compact_descr] = {'credits': 0}

    # ---- the account ledger ---------------------------------------------

    def _unlocks(self):
        unlocks = self._snapshot.get('unlockItemCompactDescrs')
        if not isinstance(unlocks, set):
            unlocks = set(unlocks or ())
            self._snapshot['unlockItemCompactDescrs'] = unlocks
        return unlocks

    def _item_cost(self, compact_descr, count=1):
        """Return what ``count`` of one item costs as a currency mapping.

        A #1513 price carries exactly one currency, so the total keeps that
        currency rather than converting between them.
        """
        price = self._snapshot.get('shopItemPrices', {}).get(
            _int(compact_descr))
        count = max(1, _int(count))
        if not isinstance(price, dict):
            return {'credits': 0}
        for currency in ('gold', 'credits'):
            amount = _int(price.get(currency, 0) or 0)
            if amount:
                return {currency: amount * count}
        return {'credits': 0}

    def _item_refund(self, compact_descr, count=1):
        """Match Shop.getSellPrice with the published sell-for-gold set empty.

        #1513 converts gold to credits, rounds up each unit's sell price,
        then the sale dialog multiplies that unit price by the stack count.
        """
        factor = self._snapshot.get('sellPriceFactor')
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            factor = SELL_PRICE_FACTOR
        cost = self._item_cost(compact_descr)
        credits = (_int(cost.get('credits', 0)) +
                   _int(cost.get('gold', 0)) * GOLD_EXCHANGE_RATE)
        return {'credits': int(math.ceil(credits * factor)) * max(1, _int(count))}

    def _in_credits(self, cost, item_type):
        """Price a gold round or consumable in credits, as the shop does.

        ``isEnabledBuyingGoldShellsForCredits`` and its equipment twin cover
        exactly these two item types; a gold optional device or a gold vehicle
        stays gold-only, which is what the client offers.
        """
        gold = _int(cost.get('gold', 0) or 0)
        if not gold or int(item_type) not in CREDIT_PRICED_GOLD_TYPES:
            return cost
        priced = dict(cost)
        priced.pop('gold', None)
        priced['credits'] = (
            _int(priced.get('credits', 0)) + gold * GOLD_EXCHANGE_RATE)
        return priced

    def _charge(self, amount):
        """Take one currency mapping from the account, or refuse it whole."""
        wallet = self._wallet()
        for currency in ('credits', 'gold'):
            needed = _int(amount.get(currency, 0) or 0)
            if needed > wallet[currency]:
                raise GarageError(
                    'the account has %d %s and needs %d' % (
                        wallet[currency], currency, needed))
        for currency in ('credits', 'gold'):
            needed = _int(amount.get(currency, 0) or 0)
            if needed:
                wallet[currency] = wallet[currency] - needed
        return wallet

    def _pay_back(self, amount):
        wallet = self._wallet()
        for currency in ('credits', 'gold'):
            value = _int(amount.get(currency, 0) or 0)
            if value:
                wallet[currency] = wallet[currency] + value
        return wallet

    def _next_inventory_id(self):
        """Return an id no live record uses.

        Ids are session state: garage_store keys vehicles on their type
        compact descriptor and a battle receipt names a type, so nothing needs
        them to survive a restart.  What a purchase needs is an id that cannot
        collide with a record already on screen.
        """
        used = set(_int(record.get('id', 0)) for record in self._records())
        candidate = _int(self._snapshot.get('nextInventoryID', 0))
        candidate = max(candidate, max(used) + 1 if used else 1)
        while candidate in used:
            candidate += 1
        self._snapshot['nextInventoryID'] = candidate + 1
        return candidate

    def _next_tankman_id(self):
        used = set()
        for record in self._records():
            used.update(
                _int(value) for value in (record.get('crew') or ())
                if value is not None)
        # A crew member in the barracks still owns their inventory id, and a
        # reused one makes the whole restored garage invalid rather than one
        # vehicle wrong.  So does a dismissed one: ``ItemsRequester.
        # getTankmen`` builds one collection keyed by inventory id over the
        # active crew and the recycle bin together, so a reissued id would
        # replace a live crew member in the barracks with a dismissed one.
        used.update(_int(value) for value in self._barracks())
        used.update(_int(value) for value in self._recycle_bin())
        return (max(used) + 1) if used else 100001

    # ---- barracks -------------------------------------------------------

    def _barracks(self):
        """Return the crew this account owns and no vehicle carries."""
        barracks = self._snapshot.get('barracksTankmen')
        if not isinstance(barracks, dict):
            barracks = {}
            self._snapshot['barracksTankmen'] = barracks
        return barracks

    def _berths(self):
        return _int(self._snapshot.get('accountBerths', 0))

    def free_berths(self):
        """Return how many more crew members the barracks can hold.

        #1513's ``BarracksSlotsValidator`` counts exactly this before it lets
        the player unload a crew or sell a vehicle without dismissing one, so
        a refusal here is the same refusal the client would have made.
        """
        return max(0, self._berths() - len(self._barracks()))

    def _to_barracks(self, tankman_id, compact_descr):
        self._barracks()[_int(tankman_id)] = compact_descr
        self._touched_tankmen.add(_int(tankman_id))

    @staticmethod
    def _remember_last_crew(record, seats):
        """Record who was sitting where, so the crew can be sent back.

        #1513's crew operations popover reads ``lastCrew`` as one inventory id
        per seat and offers to put each of them back.  It is deliberately not
        saved: the ids are this client's own bookkeeping, and a restart
        rebuilds the garage with new ones, so a saved list would point at
        nobody.
        """
        remembered = list(record.get('lastCrew') or ())
        crew = list(record.get('crew') or ())
        while len(remembered) < len(crew):
            remembered.append(None)
        for index in seats:
            if 0 <= index < len(crew):
                remembered[index] = crew[index]
        record['lastCrew'] = remembered[:len(crew)]

    def return_crew(self, vehicle_inventory_id):
        """Put the crew that was in this vehicle back where it was.

        Only a crew member the account still holds and who still fits the seat
        comes back; anyone else is left where they are, which is what the
        popover shows the player before they press it.
        """
        record = self._record(vehicle_inventory_id, touch=False)
        remembered = list(record.get('lastCrew') or ())
        if not remembered:
            raise GarageError('this vehicle has no crew to send back')
        returned = []
        for slot, tankman_id in enumerate(remembered):
            if tankman_id is None:
                continue
            try:
                self.equip_tankman(
                    vehicle_inventory_id, slot, _int(tankman_id))
            except GarageError:
                continue
            returned.append(_int(tankman_id))
        if not returned:
            raise GarageError('none of this crew can be sent back')
        return returned

    def _require_berths(self, needed):
        needed = _int(needed)
        if needed > self.free_berths():
            raise GarageError(
                'the barracks has %d free berth(s) and %d crew member(s) need '
                'a place' % (self.free_berths(), needed))

    # ---- consumables ----------------------------------------------------

    def equip_equipments(self, vehicle_inventory_id, equipments):
        """Mount the regular consumables of one equipment payload.

        A consumable a battle used has to be bought again, exactly as a round
        does, so the layout is filled from the depot and whatever the depot is
        short of is paid for at this client's prices.
        """
        layout = [_int(value) for value in (equipments or ())]
        values = [abs(value) for value in layout]
        alternative_items = set(abs(value) for value in layout if value < 0)
        if len(values) > EQUIPMENT_PAYLOAD_SLOT_COUNT:
            raise GarageError('an equipment payload carries at most four slots')
        # The trailing battle-booster slot has no published counterpart.
        values = values[:EQUIPMENT_SLOT_COUNT]
        values += [0] * (EQUIPMENT_SLOT_COUNT - len(values))
        record = self._record(vehicle_inventory_id, touch=False)
        purchase, owned = self._consumables_to_buy(record, values)
        # Every refusal happens before the first slot is filled, so a layout
        # the account cannot pay for leaves the vehicle exactly as it was.
        self._charge(self._consumables_cost(purchase, alternative_items))
        self._touched.add(_int(record.get('id', 0)))
        record['eqs'] = values
        # The vehicle is at its layout again, which is what the player asked
        # for.  Vehicle.isAutoEquipFull compares the two and warns when they
        # differ, and a battle is what makes them differ.
        record['eqsLayout'] = (layout[:EQUIPMENT_SLOT_COUNT] +
                               [0] * EQUIPMENT_SLOT_COUNT)[:EQUIPMENT_SLOT_COUNT]
        carried = {}
        for compact_descr in values:
            if compact_descr:
                carried[compact_descr] = carried.get(compact_descr, 0) + 1
        self._touch_item_changes(
            {EQUIPMENT_ITEM_TYPE: record.get('inventoryItems', {}).get(
                EQUIPMENT_ITEM_TYPE, {})}, {EQUIPMENT_ITEM_TYPE: carried})
        record.setdefault('inventoryItems', {})[EQUIPMENT_ITEM_TYPE] = carried
        for compact_descr in values:
            if not compact_descr:
                continue
            self._publish_owned(
                compact_descr, EQUIPMENT_ITEM_TYPE,
                _int(owned.get(compact_descr, 0)) +
                purchase.get(compact_descr, 0))
            self._price(compact_descr)
        self.revision += 1
        return record

    def _consumables_to_buy(self, record, values):
        """Return what a consumable layout must buy, and the stock it read."""
        # Read the stock before anything is published: the caller's loop
        # raises the very counts this arithmetic is against.
        owned = dict(self._snapshot.get('inventoryItems', {}).get(
            EQUIPMENT_ITEM_TYPE, {}))
        others = [row for row in self._records()
                  if _int(row.get('id', 0)) != _int(record.get('id', 0))]
        wanted = {}
        for compact_descr in values:
            if compact_descr:
                wanted[compact_descr] = wanted.get(compact_descr, 0) + 1
        purchase = {}
        for compact_descr, count in wanted.items():
            self._require_offered_item(compact_descr)
            needed = count + self._mounted(
                compact_descr, EQUIPMENT_ITEM_TYPE, others)
            missing = needed - _int(owned.get(compact_descr, 0))
            if missing > 0:
                purchase[compact_descr] = missing
        return purchase, owned

    def _consumables_cost(self, purchase, alternative_items=()):
        """Price a consumable resupply, in the currency #1513 charges."""
        total = {}
        for compact_descr, count in dict(purchase or {}).items():
            price = self._item_cost(compact_descr, count)
            if compact_descr in alternative_items:
                price = self._in_credits(price, EQUIPMENT_ITEM_TYPE)
            for currency, value in price.items():
                total[currency] = _int(total.get(currency, 0)) + _int(value)
        return total

    def settle_battle_consumables(self, vehicle_type_compact_descr, used):
        """Take the consumables a battle used out of the vehicle and account.

        #1513 consumes one of each consumable the player activated, however
        many times they activated it, and leaves the slot empty while the
        layout still names it -- which is exactly what ``eqs`` and
        ``eqsLayout`` are for.
        """
        wanted = set(_int(value) for value in (used or ()) if _int(value))
        if not wanted:
            return []
        record = self._record_by_vehicle_type(vehicle_type_compact_descr)
        mounted = list(record.get('eqs') or ())
        spent = []
        for slot, compact_descr in enumerate(mounted):
            compact_descr = _int(compact_descr)
            if compact_descr and compact_descr in wanted:
                mounted[slot] = 0
                spent.append(compact_descr)
        if not spent:
            return []
        record['eqs'] = mounted
        # The vehicle stops carrying what it used, which is what the account
        # count is a sum of.
        carried = {}
        for compact_descr in mounted:
            compact_descr = _int(compact_descr)
            if compact_descr:
                carried[compact_descr] = carried.get(compact_descr, 0) + 1
        record.setdefault(
            'inventoryItems', {})[EQUIPMENT_ITEM_TYPE] = carried
        owned = self._snapshot.setdefault(
            'inventoryItems', {}).setdefault(EQUIPMENT_ITEM_TYPE, {})
        for compact_descr in spent:
            self._set_owned(
                compact_descr, EQUIPMENT_ITEM_TYPE,
                max(self._mounted(compact_descr, EQUIPMENT_ITEM_TYPE,
                                  self._records()),
                    _int(owned.get(compact_descr, 0)) - 1))
        self.revision += 1
        return spent

    def set_layouts(self, vehicle_inventory_id, shells_layout=None,
                    equipment_type=EQUIPMENT_TYPE_REGULAR,
                    equipments_layout=None):
        """Apply the signed currency choices and both purchases atomically."""
        with self._transaction():
            record = self._record(vehicle_inventory_id)
            if shells_layout is not None:
                flat = []
                for compact_descr, count in _layout_pairs(
                        shells_layout, preserve_currency=True):
                    flat.extend((compact_descr, count))
                self.equip_shells(vehicle_inventory_id, flat)
            if (equipments_layout is not None and
                    _int(equipment_type) == EQUIPMENT_TYPE_REGULAR):
                pairs = _layout_pairs(
                    equipments_layout, EQUIPMENT_PAYLOAD_SLOT_COUNT,
                    preserve_currency=True)
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
        return record

    def equip_optional_device(self, vehicle_inventory_id, device_compact_descr,
                              slot_index, paid_removal=False):
        """Mount, swap or take off one optional device.

        #1513's own ``VehicleDescriptor.removeOptionalDevice`` returns what
        comes back and what is destroyed: a device its descriptor marks
        ``removable`` always comes back, and a complex one is destroyed unless
        it was a paid removal.  The client sends which of the two the player
        chose as the fifth value of the command, and the price is the shop
        value ``OptionalDevice.getRemovalPrice`` shows them.
        """
        with self._transaction():
            record = self._record(vehicle_inventory_id, touch=False)
            device_compact_descr = _int(device_compact_descr)
            slot_index = _int(slot_index)
            # Whatever is in the slot now is what leaves it, whether the slot is
            # being emptied or swapped.
            outgoing = self._device_in_slot(record, slot_index)
            # Mounting is not buying.  #1513 sells an optional device through the
            # shop and offers a separate buy-and-install command for doing both,
            # so a device the account does not own is refused here rather than
            # handed over.
            self._require_owned_device(record, device_compact_descr, slot_index)
            destroyed = 0
            if outgoing and not self._is_removable(outgoing):
                if paid_removal:
                    self._charge(self._removal_cost())
                else:
                    destroyed = outgoing
            self._touched.add(_int(record.get('id', 0)))

            def mutate(descriptor):
                # Removing first makes a slot swap idempotent; #1513 rejects an
                # install into an occupied slot.
                if outgoing:
                    descriptor.removeOptionalDevice(slot_index)
                if device_compact_descr:
                    descriptor.installOptionalDevice(
                        device_compact_descr, slot_index)

            self._rebuild_descriptor(record, mutate)
            # The record states what the vehicle holds, which is what the
            # descriptor now says rather than everything it has ever carried.
            record.setdefault('inventoryItems', {})[
                OPTIONAL_DEVICE_ITEM_TYPE] = self._mounted_devices(record)
            if outgoing:
                self._touched_items.setdefault(
                    OPTIONAL_DEVICE_ITEM_TYPE, set()).add(outgoing)
            if device_compact_descr:
                self._publish_owned(
                    device_compact_descr, OPTIONAL_DEVICE_ITEM_TYPE,
                    self._mounted(device_compact_descr,
                                  OPTIONAL_DEVICE_ITEM_TYPE, self._records()))
                self._price(device_compact_descr)
            if destroyed:
                self._destroy_device(destroyed)
            self.revision += 1
            return record

    def _mounted_devices(self, record):
        """Return what one vehicle's descriptor currently has in its slots."""
        vehicles = self._vehicles_module()
        mounted = {}
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            devices = list(getattr(descriptor, 'optionalDevices', ()) or ())
        except Exception:
            return dict(record.get('inventoryItems', {}).get(
                OPTIONAL_DEVICE_ITEM_TYPE, {}))
        for device in devices:
            compact_descr = _int(getattr(device, 'compactDescr', 0) or 0)
            if compact_descr:
                mounted[compact_descr] = mounted.get(compact_descr, 0) + 1
        return mounted

    def _require_owned_device(self, record, compact_descr, slot_index):
        """Refuse to mount an optional device the account does not own.

        A device belongs to the account, so what this vehicle would hold after
        the mount plus what every other vehicle already holds is what the
        depot has to cover.
        """
        if not compact_descr:
            return
        owned = _int(self._snapshot.get('inventoryItems', {}).get(
            OPTIONAL_DEVICE_ITEM_TYPE, {}).get(compact_descr, 0))
        others = [row for row in self._records()
                  if _int(row.get('id', 0)) != _int(record.get('id', 0))]
        wanted = self._mounted(
            compact_descr, OPTIONAL_DEVICE_ITEM_TYPE, others) + 1
        # The slot being filled gives its own device back first, so replacing
        # a device with itself needs nothing new.
        for slot, mounted in enumerate(self._device_slots(record)):
            if mounted == compact_descr and slot != _int(slot_index):
                wanted += 1
        if owned < wanted:
            raise GarageError(
                'the account does not own optional device %d' % compact_descr)

    def _device_slots(self, record):
        """Return one vehicle's slots as compact descriptors, zero for empty."""
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            devices = list(getattr(descriptor, 'optionalDevices', ()) or ())
        except Exception:
            return []
        return [_int(getattr(device, 'compactDescr', 0) or 0)
                for device in devices]

    def _require_owned_module(self, record, compact_descr, item_type):
        """Refuse to install a module the account does not own.

        A copy installed on another vehicle is not a spare. Reinstalling the
        same module on this vehicle can keep its current copy.
        """
        compact_descr = _int(compact_descr)
        if not compact_descr:
            return
        owned = _int(self._snapshot.get('inventoryItems', {}).get(
            int(item_type), {}).get(compact_descr, 0))
        others = [row for row in self._records()
                  if _int(row.get('id', 0)) != _int(record.get('id', 0))]
        needed = 1 + self._mounted(compact_descr, item_type, others)
        if owned < needed:
            raise GarageError(
                'the account does not own module %d' % compact_descr)

    def _destroy_device(self, compact_descr):
        """Take one complex device out of the account, as removing it does."""
        owned = self._snapshot.get('inventoryItems', {}).get(
            OPTIONAL_DEVICE_ITEM_TYPE, {})
        remaining = max(
            self._mounted(
                compact_descr, OPTIONAL_DEVICE_ITEM_TYPE, self._records()),
            _int(owned.get(compact_descr, 0)) - 1)
        self._set_owned(compact_descr, OPTIONAL_DEVICE_ITEM_TYPE, remaining)

    def _is_removable(self, device_compact_descr):
        """Report whether one optional device comes off without being lost."""
        vehicles = self._vehicles_module()
        try:
            item = vehicles.getItemByCompactDescr(_int(device_compact_descr))
        except Exception:
            # A device this client cannot describe is not one it can destroy.
            return True
        return bool(getattr(item, 'removable', True))

    def _device_in_slot(self, record, slot_index):
        """Return the optional device one slot currently holds, or zero."""
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            devices = list(
                getattr(descriptor, 'optionalDevices', ()) or ())
        except Exception:
            return 0
        if not 0 <= _int(slot_index) < len(devices):
            return 0
        device = devices[_int(slot_index)]
        return _int(getattr(device, 'compactDescr', 0) or 0)

    def _removal_cost(self):
        """Return what a paid removal costs, as the shop publishes it."""
        cost = self._snapshot.get('deviceRemovalCost')
        if not isinstance(cost, dict):
            return {}
        return dict((str(currency), _int(amount))
                    for currency, amount in cost.items())

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
        # Mounting is not buying.  #1513 researches a module, then sells it,
        # and offers a separate buy-and-install command for doing both at
        # once, so a module the account does not own is refused here.
        self._require_owned_module(record, compact_descr, item_type)
        if is_turret and gun_compact_descr:
            # A turret swap names a gun only when the mounted one will not
            # fit; ``TurretInstaller._findAvailableGun`` then picks the first
            # of the turret's guns whose item ``isInInventory``, so the gun
            # this arrives with is one the account owns.
            self._require_owned_module(
                record, gun_compact_descr, self._item_type(gun_compact_descr))

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
            staged.setdefault('inventoryItems', {}).update(
                mounted_module_items(verified))
        except GarageError:
            raise
        except Exception as error:
            raise GarageError('the client refused the fitting: %s' % error)

        staged['compDescr'] = serialized
        staged['shellsLayoutIdx'] = new_layout_key
        if new_layout_key != old_layout_key:
            # A new gun arrives with an empty rack.  Its default load becomes
            # the layout, which is what the resupply button and the vehicle's
            # own auto-load switch buy; handing the rounds over here would be
            # free ammunition every time a gun changed.
            shells = self._validated_default_ammo(vehicles, verified)
            staged['shellsLayout'] = {new_layout_key: list(shells)}
            staged['shells'] = [
                value if index % 2 == 0 else 0
                for index, value in enumerate(shells)]
            staged.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = dict(
                (shells[index], 0) for index in range(0, len(shells), 2))
        # A fitting that keeps the gun and turret also keeps the requested
        # loadout, including rounds still missing after a battle or purchase.

        # Everything above is validation-only.  Publish the descriptor,
        # layout and ammunition together once no native operation can fail.
        self._touch_item_changes(
            record.get('inventoryItems', {}), staged.get('inventoryItems', {}))
        record.clear()
        record.update(staged)
        self._touched.add(_int(vehicle_inventory_id))
        self._price(compact_descr)
        if gun_compact_descr:
            self._price(gun_compact_descr)
        if new_layout_key != old_layout_key:
            for index in range(0, len(shells), 2):
                # The rack is empty, so nothing is owned here that was not
                # already; this only keeps the new gun's rounds priced and
                # visible in the depot.
                self._publish_owned(shells[index], SHELL_ITEM_TYPE, 0)
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

    def buy_item(self, compact_descr, count=1, gold_for_credits=False):
        """Own more of one item and pay the catalogue price for it.

        The account has to be able to afford the whole order before any of it
        is owned, so the charge is taken first and refuses the purchase whole
        rather than delivering part of it.  ``gold_for_credits`` is the fourth
        value of #1513's own buy command: the shop publishes premium rounds
        and consumables as buyable for credits, and this is the client asking
        for that price.
        """
        compact_descr = _int(compact_descr)
        count = max(1, _int(count))
        item_type, cost = self._purchase_terms(
            compact_descr, count, gold_for_credits)
        self._charge(cost)
        existing = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        self._publish_owned(
            compact_descr, item_type,
            int(existing.get(compact_descr, 0)) + count)
        self._price(compact_descr)
        self.revision += 1
        return compact_descr

    def _require_offered_item(self, compact_descr):
        if (compact_descr in (self._snapshot.get('notInShopItems') or ()) and
                compact_descr not in (self._snapshot.get('shopItemPrices') or {})):
            raise GarageError('item %d is not offered in standard battles' %
                              compact_descr)

    def _purchase_terms(self, compact_descr, count=1, gold_for_credits=False):
        """Return one purchase's item type and price, or refuse it.

        Every check that can refuse a purchase lives here, so a request that
        buys and mounts in one step can be refused before it touches a vehicle
        rather than after.
        """
        if not compact_descr:
            raise GarageError('a purchase needs an item')
        self._require_offered_item(compact_descr)
        item_type = self._item_type(compact_descr)
        # Only modules are researched. #1513 sells shells, consumables and
        # optional devices straight from the shop, so gating them on the
        # unlock set would make them permanently unbuyable.  An empty unlock
        # set means the snapshot opted out of unlock enforcement, exactly as
        # data._validate_selected_vehicle reads it.
        unlocks = self._unlocks()
        if (unlocks and item_type in RESEARCHED_ITEM_TYPES and
                compact_descr not in unlocks):
            raise GarageError('item %d is not researched' % compact_descr)
        cost = self._item_cost(compact_descr, max(1, _int(count)))
        if gold_for_credits:
            cost = self._in_credits(cost, item_type)
        balances = self._balances()
        for currency in ('credits', 'gold'):
            needed = _int(cost.get(currency, 0) or 0)
            if needed > balances[currency]:
                raise GarageError(
                    'the account has %d %s and needs %d' % (
                        balances[currency], currency, needed))
        return item_type, cost

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
                           slot_index=0, gun_compact_descr=0, paid_removal=False):
        """Own one item and mount it on the vehicle in the same request."""
        compact_descr = _int(compact_descr)
        item_type, unused_cost = self._purchase_terms(compact_descr)
        # Installation requires a free owned copy. Buy it inside the same
        # transaction so a native fitting refusal rolls back both the item
        # and its payment, including any additional device-removal charge.
        with self._transaction():
            self.buy_item(compact_descr, 1)
            if item_type == OPTIONAL_DEVICE_ITEM_TYPE:
                record = self.equip_optional_device(
                    vehicle_inventory_id, compact_descr, slot_index,
                    paid_removal=paid_removal)
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
                record = self.install_component(
                    vehicle_inventory_id, compact_descr, gun_compact_descr,
                    slot_index)
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
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        names = getattr(tankmen, 'SKILL_NAMES', ())
        try:
            skill_name = names[_int(skill_index)]
        except (IndexError, TypeError):
            raise GarageError('unknown crew skill index %r' % (skill_index,))
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
            descriptor.addSkill(skill_name)
            rows[tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the crew skill: %s' % error)
        self.revision += 1
        return tankman_id

    def drop_tankman_skills(self, tankman_inventory_id, cost_index=0):
        """Reset one crew member's skills, at the price they chose.

        ``SkillDropWindow`` lists ``shop.dropSkillsCost`` and sends back the
        key the player picked; each entry carries what it costs and how much
        of the accumulated skill experience it keeps, so the reset is charged
        for and the descriptor is dropped at that fraction rather than always
        at the dearest option for nothing.  ``TankmanDescr.isFreeDropSkills``
        is the one case the client itself calls free: a crew member who has
        not finished a first skill has nothing to give up.
        """
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        choice = self._drop_skills_choice(cost_index)
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
            is_free = getattr(descriptor, 'isFreeDropSkills', None)
            free = bool(is_free()) if callable(is_free) else False
        except Exception as error:
            raise GarageError('the client refused the skill reset: %s' % error)
        try:
            descriptor.dropSkills(
                float(choice.get('xpReuseFraction', 0.0) or 0.0), False)
            serialized = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the skill reset: %s' % error)
        if not free:
            self._charge(choice)
        rows[tankman_id] = serialized
        self.revision += 1
        return tankman_id

    def _drop_skills_choice(self, cost_index):
        """Return one published skill-reset choice, or refuse an unknown one."""
        costs = self._snapshot.get('dropSkillsCosts')
        costs = costs if isinstance(costs, dict) else {}
        choice = costs.get(_int(cost_index))
        if not isinstance(choice, dict):
            raise GarageError(
                'the shop does not offer skill reset %r' % (cost_index,))
        return {
            'credits': max(0, _int(choice.get('credits', 0))),
            'gold': max(0, _int(choice.get('gold', 0))),
            'xpReuseFraction': float(
                choice.get('xpReuseFraction', 0.0) or 0.0),
        }

    def train_tankman(self, tankman_inventory_id, free_xp):
        """Convert the requested free XP into crew XP at the #1513 rate."""
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        amount = _int(free_xp)
        if amount <= 0:
            raise GarageError('crew training XP must be positive')
        if amount > self._balances()['freeXP']:
            raise GarageError('the account cannot pay for crew training')
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
            descriptor.addXP(amount * FREE_XP_TO_TANKMAN_XP_RATE)
            serialized = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused crew training: %s' % error)
        self._wallet()['freeXP'] -= amount
        rows[tankman_id] = serialized
        self.revision += 1
        return tankman_id

    def _crew_xp_factor(self, vehicle_type_compact_descr):
        """Return the vehicle's own crew training multiplier.

        ``crewXpFactor`` is exact #1513 data: every shipped vehicle definition
        carries it, and it is the reason a premium vehicle trains its crew
        faster than a researchable one.  It is read on the client because the
        client owns the descriptors; an unreadable descriptor trains at the
        stock rate rather than failing the battle transaction.
        """
        try:
            vehicles = self._vehicles_module()
            # ``getVehicleType`` is the #1513 entry point for a *type* compact
            # descriptor; ``VehicleDescr`` wants a full item descriptor.
            vehicle_type = vehicles.getVehicleType(
                _int(vehicle_type_compact_descr))
            factor = float(getattr(vehicle_type, 'crewXpFactor', 1.0))
        except Exception:
            return 1.0
        if factor <= 0.0:
            return 1.0
        return factor

    def award_battle_crew_xp(self, vehicle_type_compact_descr, battle_xp,
                             xp_to_tankman_flag):
        """Apply one battle's crew XP and return the durable award summary.

        Every crew member receives battle XP scaled by the vehicle's own
        ``crewXpFactor``. On an elite vehicle with accelerated training
        enabled, the least experienced crew member receives one additional
        equal award. Both the vehicle setting and its current research
        completion are required.
        """
        amount = _int(battle_xp)
        if amount < 0:
            raise GarageError('battle crew XP cannot be negative')
        amount = int(amount * self._crew_xp_factor(
            vehicle_type_compact_descr))
        record = self._record_by_vehicle_type(
            vehicle_type_compact_descr, touch=False)
        crew_ids = list(record.get('crew') or ())
        tankman_rows = record.get('tankmen')
        if not isinstance(tankman_rows, dict):
            tankman_rows = {}

        tankmen = self._tankmen_module()
        descriptors = []
        for slot, tankman_id in enumerate(crew_ids):
            if tankman_id is None:
                # An empty seat earns nothing; the rest of the crew still do.
                continue
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
        accelerated = bool(descriptors and accelerated and self._is_elite(
            vehicle_type_compact_descr))
        weakest = (min(descriptors, key=lambda row: (row[2], row[0]))
                   if descriptors else None)
        try:
            # The shipped helper owns Mentor's factor, including Brothers in
            # Arms and food. Evaluate the starting crew and carried equipment
            # before XP can level a skill or settlement consumes that food.
            crew = [row[3] for row in descriptors]
            ammo = []
            for compact_descr in record.get('eqs') or ():
                if compact_descr:
                    ammo.extend([abs(_int(compact_descr)), 1])
            tutor_bonus = (tankmen.commanderTutorXpBonusFactorForCrew(crew, ammo)
                           if any(tman.role == 'commander' for tman in crew)
                           else 0.0)
            xp_by_tankman = {}
            for unused_slot, tankman_id, unused_total, descriptor in descriptors:
                multiplier = (1.0 if descriptor.role == 'commander'
                              else 1.0 + tutor_bonus)
                earned = amount * (2 if accelerated and descriptor is weakest[3] else 1)
                earned = int(earned * multiplier)
                descriptor.addXP(earned)
                xp_by_tankman[tankman_id] = earned
            updated_rows = dict(
                (tankman_id, descriptor.makeCompactDescr())
                for unused_slot, tankman_id, unused_total, descriptor
                in descriptors)
        except Exception as error:
            raise GarageError('the client refused battle crew XP: %s' % error)
        tankman_rows.update(updated_rows)

        vehicle_id = _int(record.get('id', 0))
        self._touched.add(vehicle_id)
        self.revision += 1
        return {
            'accelerated': accelerated,
            'vehicle_id': vehicle_id,
            'weakest_tankman_id': weakest[1] if accelerated else 0,
            'xp_by_tankman': xp_by_tankman,
        }

    def award_battle_earnings(self, vehicle_type_compact_descr, rewards,
                              accelerated=False):
        """Bank one battle's credits and experience into the account ledger.

        Accelerated crew training spends the vehicle's experience on the crew
        instead of the vehicle, which is why the caller passes the flag the
        crew award already resolved rather than reading the setting twice.
        Free experience is a separate award and is banked either way.
        """
        rewards = rewards if isinstance(rewards, dict) else {}
        wallet = self._wallet()
        wallet['credits'] = max(
            0, wallet['credits'] + max(0, _int(rewards.get('credits', 0))))
        wallet['freeXP'] = max(
            0, wallet['freeXP'] + max(0, _int(rewards.get('free_xp', 0))))
        experience = max(0, _int(rewards.get('xp', 0)))
        key = _int(vehicle_type_compact_descr)
        vehicle_xp = self._snapshot.setdefault('vehicleXP', {})
        if not accelerated and experience:
            vehicle_xp[key] = max(0, _int(vehicle_xp.get(key, 0))) + experience
        self.revision += 1
        return {
            'credits': wallet['credits'],
            'freeXP': wallet['freeXP'],
            'vehicleXP': _int(vehicle_xp.get(key, 0)),
        }

    # ---- damage and repair ----------------------------------------------

    def settle_battle_damage(self, vehicle_type_compact_descr, health):
        """Record what one battle left of a vehicle, and what it will cost.

        #1513 keeps both in one inventory field: ``invData['repair']`` is
        ``(outstanding repair cost, remaining health)``.  ``Vehicle.modelState``
        reads a health of 0 with a bill as DESTROYED and a negative one as
        EXPLODED, and ``Vehicle.isBroken`` is the bill alone, so the pair is
        the whole contract.

        The bill comes out of ``VehicleDescriptor.getMaxRepairCost``, whose
        hull term is ``maxHealth * type.repairCost``: one health point costs
        ``type.repairCost``, so losing some costs that much per point.  The
        rest of that formula prices destroyed modules, and the battle receipt
        does not say which modules were destroyed, so none are charged.
        """
        record = self._record_by_vehicle_type(vehicle_type_compact_descr)
        descriptor = self._descriptor(record)
        maximum = _int(getattr(descriptor, 'maxHealth', 0))
        if maximum <= 0:
            raise GarageError('the vehicle has no maximum health')
        health = _int(health)
        if health >= maximum:
            # Untouched, or healed past its own maximum by a stale receipt.
            record['repair'] = (0, maximum)
            self.revision += 1
            return record['repair']
        try:
            per_point = float(getattr(descriptor.type, 'repairCost', 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            per_point = 0.0
        lost = maximum - max(health, 0)
        record['repair'] = (max(0, int(round(per_point * lost))), health)
        self.revision += 1
        return record['repair']

    def settle_battle_ammunition(self, vehicle_type_compact_descr,
                                 shells_fired):
        """Take the rounds a battle fired out of the vehicle and the account.

        The receipt counts rounds by the shell's index in the gun's own shot
        order, because that is what the client sent with every fire intent and
        the server cannot resolve it: only this client owns the definitions
        that turn an index into a shell.
        """
        counts = {}
        for index, count in dict(shells_fired or {}).items():
            count = _int(count)
            if count > 0:
                counts[_int(index)] = count
        if not counts:
            return {}
        record = self._record_by_vehicle_type(vehicle_type_compact_descr)
        descriptor = self._descriptor(record)
        shots = tuple(getattr(descriptor.gun, 'shots', ()) or ())
        loaded = list(record.get('shells') or ())
        pairs = {}
        for position in range(0, len(loaded) - 1, 2):
            pairs[_int(loaded[position])] = _int(loaded[position + 1])
        spent = {}
        for index, count in counts.items():
            if not 0 <= index < len(shots):
                # A round this gun cannot fire is a receipt from another
                # fitting; charging the wrong shell is worse than charging
                # nothing.
                continue
            shell = getattr(shots[index], 'shell', None)
            compact_descr = _int(getattr(shell, 'compactDescr', 0))
            if compact_descr not in pairs:
                continue
            taken = min(count, pairs[compact_descr])
            if taken <= 0:
                continue
            pairs[compact_descr] = pairs[compact_descr] - taken
            spent[compact_descr] = spent.get(compact_descr, 0) + taken
        if not spent:
            return {}
        record['shells'] = [value for compact_descr in
                            (loaded[position] for position in
                             range(0, len(loaded) - 1, 2))
                            for value in (compact_descr,
                                          pairs[_int(compact_descr)])]
        # The layout is deliberately left alone: it is what the player asked
        # to carry, and after a battle it is what auto-load and the resupply
        # button buy back.  #1513's own Vehicle.isAutoLoadFull compares the
        # two and reports the tank as not full, which it is.
        record.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = dict(pairs)
        owned = self._snapshot.setdefault(
            'inventoryItems', {}).setdefault(SHELL_ITEM_TYPE, {})
        for compact_descr, count in spent.items():
            owned[compact_descr] = max(
                self._mounted(compact_descr, SHELL_ITEM_TYPE, self._records()),
                _int(owned.get(compact_descr, 0)) - count)
            self._touched_items.setdefault(
                SHELL_ITEM_TYPE, set()).add(compact_descr)
        self.revision += 1
        return spent

    def repair_vehicle(self, vehicle_inventory_id):
        """Pay one vehicle's outstanding repair bill and put it back together."""
        record = self._record(vehicle_inventory_id, touch=False)
        descriptor = self._descriptor(record)
        maximum = _int(getattr(descriptor, 'maxHealth', 0))
        if maximum <= 0:
            raise GarageError('the vehicle has no maximum health')
        bill, health = self._repair_state(record)
        if bill <= 0 and health >= maximum:
            raise GarageError('this vehicle does not need repairing')
        self._charge({'credits': bill})
        record['repair'] = (0, maximum)
        self._touched.add(_int(record.get('id', 0)))
        self.revision += 1
        return bill

    @staticmethod
    def _repair_state(record):
        value = record.get('repair')
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return 0, 0
        return _int(value[0]), _int(value[1])

    def _descriptor(self, record):
        vehicles = self._vehicles_module()
        try:
            return vehicles.VehicleDescr(compactDescr=record['compDescr'])
        except Exception as error:
            raise GarageError('the client refused the vehicle: %s' % error)

    # ---- purchases, sales and research ----------------------------------

    def sell_item(self, compact_descr, count=1):
        """Give up one owned item and take back half of what it cost."""
        compact_descr = _int(compact_descr)
        if not compact_descr:
            raise GarageError('a sale needs an item')
        count = max(1, _int(count))
        item_type = self._item_type(compact_descr)
        owned = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        available = _int(owned.get(compact_descr, 0))
        if available < count:
            raise GarageError(
                'the account owns %d of item %d, not %d' % (
                    available, compact_descr, count))
        mounted = self._mounted(compact_descr, item_type, self._records())
        if mounted > available - count:
            raise GarageError(
                'item %d is mounted on %d vehicle(s) and cannot drop to %d' % (
                    compact_descr, mounted, available - count))
        self._set_owned(compact_descr, item_type, available - count)
        self._pay_back(self._item_refund(compact_descr, count))
        self.revision += 1
        return compact_descr

    def _set_owned(self, compact_descr, item_type, count):
        """Publish an exact owned count, including zero.

        ``_publish_owned`` only ever raises a count because a mount must never
        lower one.  A sale is the one operation that lowers it, so it writes
        the count directly and drops the row when nothing is left.
        """
        published = self._snapshot.setdefault('inventoryItems', {})
        owned = published.setdefault(int(item_type), {})
        if count > 0:
            owned[compact_descr] = int(count)
        else:
            owned.pop(compact_descr, None)
        self._touched_items.setdefault(int(item_type), set()).add(compact_descr)

    def buy_vehicle(self, vehicle_type_compact_descr, buy_shells=False,
                    recruit_crew=False, tman_cost_type_index=0,
                    rent_period=-1):
        """Own one more vehicle, stock, and pay the catalogue price for it.

        A bought vehicle arrives exactly as retail sells it: the stock fitting,
        nothing mounted that was not paid for, and only the modules the vehicle
        unlocks for free already researched.

        An unchecked shell option leaves a zero-count rack with the gun's
        normal layout. Empty crew seats likewise remain real slots, and a
        requested crew is recruited at the exact school the player chose.
        """
        compact_descr = _int(vehicle_type_compact_descr)
        if not compact_descr:
            raise GarageError('a purchase needs a vehicle type')
        # #1513 VehicleBuyer sends -1 for a permanent purchase.
        if _int(rent_period) != -1:
            raise GarageError('offline vehicles are not rented')
        for record in self._records():
            if _int(record.get('vehicleTypeCompactDescr', 0)) == compact_descr:
                raise GarageError('the account already owns this vehicle')
        slots = _int(self._snapshot.get('accountSlots', 0))
        if slots and len(self._records()) >= slots:
            raise GarageError('every garage slot is occupied')

        from gui.mods.offline_lan_0922 import vehicle_records
        from items import ITEM_TYPE_INDICES

        with self._transaction():
            vehicles = self._vehicles_module()
            tankmen = self._tankmen_module()
            vehicle_type = vehicles.getVehicleType(compact_descr)
            built = vehicle_records.build_record(
                vehicles, tankmen, ITEM_TYPE_INDICES, tuple(vehicle_type.id),
                self._next_inventory_id(), self._next_tankman_id(),
                self._default_vehicle_settings(),
                [0, 0, 0], top_modules=False, own_researchable_modules=False,
                recruit_crew=False)
            record = built['record']

            cost = self._item_cost(compact_descr)
            shells = [_int(value) for value in (record.get('shells') or ())]
            if not buy_shells:
                shells = [value if index % 2 == 0 else 0
                          for index, value in enumerate(shells)]
                record['shells'] = shells
                record['inventoryItems'][SHELL_ITEM_TYPE] = dict(
                    (shells[index], 0) for index in range(0, len(shells), 2))
            for index in range(0, len(shells) - 1, 2):
                if shells[index + 1] <= 0:
                    continue
                self._add_money(cost, self._in_credits(
                    self._item_cost(shells[index], shells[index + 1]),
                    SHELL_ITEM_TYPE))
            self._charge(cost)
            records = self._snapshot.setdefault('vehicles', self._records())
            records.append(record)
            self._snapshot['vehicles'] = records
            if recruit_crew:
                nation_id, vehicle_type_id = vehicle_type.id
                for slot, roles in enumerate(vehicle_type.crewRoles):
                    tankman_id, descriptor = self._recruit(
                        nation_id, vehicle_type_id, roles[0], tman_cost_type_index)
                    record['crew'][slot] = tankman_id
                    record['tankmen'][tankman_id] = descriptor
                    self._touched_tankmen.add(tankman_id)
            published = self._snapshot.setdefault('vehicleTypeCompactDescrs', set())
            if isinstance(published, set):
                published.add(compact_descr)
            unlocks = self._unlocks()
            unlocks.add(compact_descr)
            # The vehicle's free modules come with it, exactly as #1513 records
            # them on the type rather than as a research step the player pays for.
            for item in (getattr(vehicle_type, 'autounlockedItems', ()) or ()):
                unlocks.add(_int(item))
            for item_type, items in record['inventoryItems'].items():
                for item_compact_descr, count in items.items():
                    self._stock_owned(item_compact_descr, item_type, count)
                    self._price(item_compact_descr)
                    unlocks.add(_int(item_compact_descr))
            self._snapshot.setdefault('vehicleXP', {}).setdefault(compact_descr, 0)
            self._touched.add(_int(record['id']))
            self.revision += 1
            return record

    def sell_vehicle(self, vehicle_inventory_id, dismiss_crew=True,
                     items_from_vehicle=(), items_from_inventory=()):
        """Give up one vehicle and take back half of what it cost.

        #1513's sell dialog also lists what goes with the vehicle.  Its own
        ``VehicleSeller.__getGainSpendMoney`` pays the carried rounds by their
        count, one unit for each mounted equipment or optional device, and the
        whole stored stack for each module sold out of the inventory, so this
        settles the same three cases from the same lists.
        """
        with self._transaction():
            record = self._record(vehicle_inventory_id, touch=False)
            records = self._records()
            if len(records) <= 1:
                raise GarageError('the last garage vehicle cannot be sold')
            crew_rows = dict(record.get('tankmen') or {})
            if not dismiss_crew:
                # #1513's VehicleSeller adds a BarracksSlotsValidator for exactly
                # this count when the player keeps the crew, so refusing here is
                # the refusal the client would already have made.
                self._require_berths(len(crew_rows))
            compact_descr = _int(record.get('vehicleTypeCompactDescr', 0))
            refund = self._item_refund(compact_descr)
            remaining = [row for row in records
                         if _int(row.get('id', 0)) != _int(record.get('id', 0))]
            items_from_vehicle = [_int(value) for value in (items_from_vehicle or ())]
            items_from_inventory = [_int(value) for value in (items_from_inventory or ())]
            if (len(set(items_from_vehicle)) != len(items_from_vehicle) or
                    len(set(items_from_inventory)) != len(items_from_inventory)):
                raise GarageError('a sale lists the same item more than once')
            # Vehicle._calcSellPrice replaces each stock module's value with
            # the installed module's value. Those installed copies leave with
            # the hull; they must not also remain as free depot stock.
            try:
                default_modules, installed_modules, devices = (
                    self._descriptor(record).getDevices())
                if len(default_modules) != len(installed_modules):
                    raise ValueError('inconsistent module list')
            except Exception as error:
                raise GarageError('the client refused the vehicle sale: %s' % error)
            for default, installed in zip(default_modules, installed_modules):
                if default != installed:
                    refund['credits'] += (
                        self._item_refund(installed)['credits'] -
                        self._item_refund(default)['credits'])
                item_type = self._item_type(installed)
                owned = self._snapshot.get('inventoryItems', {}).get(item_type, {})
                self._set_owned(installed, item_type, max(
                    _int(owned.get(installed, 0)) - 1,
                    self._mounted(installed, item_type, remaining)))
            for device in devices:
                if device not in items_from_vehicle and not self._is_removable(device):
                    self._charge(self._removal_cost())
            for listed in items_from_vehicle:
                self._add_money(
                    refund, self._sold_off_vehicle(record, listed, remaining))
            # Installed copies have left; depot sale counts must now exclude
            # this vehicle, or a legitimate spare of the same module appears
            # to be mounted and the combined sale is incorrectly refused.
            self._snapshot['vehicles'] = remaining
            self._touch_item_changes(record.get('inventoryItems', {}), {})
            for listed in items_from_inventory:
                self._add_money(refund, self._sold_out_of_inventory(listed))
            published = self._snapshot.get('vehicleTypeCompactDescrs')
            if isinstance(published, set):
                published.discard(compact_descr)
            # The client only drops what the diff names, so a removal has to be
            # announced as loudly as a change.
            self._touched.add(_int(record.get('id', 0)))
            for tankman_id, tankman_compact_descr in crew_rows.items():
                if dismiss_crew:
                    # #1513's own RestoreController counts these separately as
                    # "deleted by selling", so they land in the same bin a
                    # dismissal fills and can be hired back from it.
                    self._to_recycle_bin(tankman_id, tankman_compact_descr)
                    self._touched_tankmen.add(_int(tankman_id))
                else:
                    self._to_barracks(tankman_id, tankman_compact_descr)
            self._pay_back(refund)
            self.revision += 1
            return compact_descr

    # ---- crew placement -------------------------------------------------

    def equip_tankman(self, vehicle_inventory_id, slot, tankman_inventory_id):
        """Seat one crew member, or send a seat's occupant to the barracks.

        #1513 sends all three cases through ``Inventory.equipTankman``: a
        tankman id of -1 empties the named seat, and a slot of -1 alongside it
        empties every seat, which is what the barracks' "unload crew" button
        does. An occupied target seat is a swap, and its occupant goes to the
        barracks rather than being destroyed.
        """
        record = self._record(vehicle_inventory_id, touch=False)
        slot = _int(slot)
        tankman_inventory_id = _int(tankman_inventory_id)
        crew = list(record.get('crew') or ())
        rows = dict(record.get('tankmen') or {})
        roles = self._crew_roles(record)
        if len(crew) != len(roles):
            raise GarageError('the vehicle crew does not match its roles')

        if tankman_inventory_id <= 0:
            if slot >= len(crew):
                raise GarageError('vehicle has no crew seat %d' % slot)
            seats = range(len(crew)) if slot < 0 else [slot]
            leaving = [(index, crew[index]) for index in seats
                       if crew[index] is not None]
            if not leaving:
                raise GarageError('there is no crew member in that seat')
            self._require_berths(len(leaving))
            self._remember_last_crew(
                record, [index for index, unused in leaving])
            for index, tankman_id in leaving:
                self._to_barracks(tankman_id, rows.pop(tankman_id))
                crew[index] = None
            record['crew'] = crew
            record['tankmen'] = rows
            self._touched.add(_int(record.get('id', 0)))
            self.revision += 1
            return len(leaving)

        if slot < 0 or slot >= len(crew):
            raise GarageError('vehicle has no crew seat %d' % slot)
        barracks = self._barracks()
        source = None
        if tankman_inventory_id in barracks:
            compact_descr = barracks[tankman_inventory_id]
        else:
            source = self._seated_record(tankman_inventory_id)
            if source is None:
                raise GarageError(
                    'unknown tankman inventory id %d' % tankman_inventory_id)
            compact_descr = (rows if source is record
                             else source['tankmen'])[tankman_inventory_id]
        self._check_tankman_fits(record, roles, slot, compact_descr)
        occupant = crew[slot]
        if occupant == tankman_inventory_id:
            return tankman_inventory_id
        # #1513 asks for no berth here because the server is expected to place
        # whoever is displaced. Offline there is only the barracks, so count
        # what actually lands in it: the seat's occupant arrives, and the
        # newcomer leaves only if that is where they were.
        arriving = (1 if occupant is not None else 0) - (0 if source else 1)
        if arriving > self.free_berths():
            raise GarageError(
                'the barracks has no room for the crew member leaving seat %d'
                % slot)
        if occupant is not None:
            self._remember_last_crew(record, [slot])
            self._to_barracks(occupant, rows.pop(occupant))
        if source is None:
            del barracks[tankman_inventory_id]
        elif source is record:
            crew[crew.index(tankman_inventory_id)] = None
            rows.pop(tankman_inventory_id, None)
        else:
            source_crew = list(source.get('crew') or ())
            source_crew[source_crew.index(tankman_inventory_id)] = None
            source['crew'] = source_crew
            source['tankmen'].pop(tankman_inventory_id, None)
            self._touched.add(_int(source.get('id', 0)))
        rows[tankman_inventory_id] = compact_descr
        crew[slot] = tankman_inventory_id
        record['crew'] = crew
        record['tankmen'] = rows
        self._touched.add(_int(record.get('id', 0)))
        self._touched_tankmen.add(tankman_inventory_id)
        self.revision += 1
        return tankman_inventory_id

    def buy_tankman(self, vehicle_type_compact_descr, role_index,
                    cost_type_index):
        """Recruit one crew member into the barracks.

        #1513's recruit window sends the vehicle they are trained for, the
        role as an index into ``tankmen.SKILL_NAMES``, and which of the three
        schools was chosen.  The account carries the same three schools the
        window priced them from, so what the player was shown is what is
        charged.

        The window lists every vehicle the account has unlocked, not the ones
        it owns: its criteria are ``REQ_CRITERIA.UNLOCKED`` without
        ``INVENTORY``.  Hiring a crew before buying the tank is the case the
        barracks exists for, so owning one is not required here either.
        """
        compact_descr = _int(vehicle_type_compact_descr)
        if compact_descr not in self._unlocks():
            raise GarageError(
                'vehicle type %d is not researched' % compact_descr)
        nation_id, vehicle_type_id, roles = self._vehicle_type_crew(
            compact_descr)
        role = self._crew_role_name(role_index)
        if role not in [row[0] for row in roles]:
            raise GarageError(
                'this vehicle has no %s in its crew' % role)
        self._require_berths(1)
        tankman_id, tankman_compact_descr = self._recruit(
            nation_id, vehicle_type_id, role, cost_type_index)
        self._to_barracks(tankman_id, tankman_compact_descr)
        self.revision += 1
        return tankman_id

    def _vehicle_type_crew(self, vehicle_type_compact_descr):
        """Return one vehicle type's nation, id and crew roles."""
        vehicles = self._vehicles_module()
        try:
            vehicle_type = vehicles.getVehicleType(
                _int(vehicle_type_compact_descr))
            nation_id, type_id = vehicle_type.id
            return _int(nation_id), _int(type_id), tuple(
                vehicle_type.crewRoles)
        except Exception as error:
            raise GarageError('unknown vehicle type: %s' % error)

    def buy_and_equip_tankman(self, vehicle_inventory_id, slot,
                              cost_type_index):
        """Recruit one crew member straight into a seat."""
        record = self._record(vehicle_inventory_id, touch=False)
        roles = self._crew_roles(record)
        slot = _int(slot)
        crew = list(record.get('crew') or ())
        if slot < 0 or slot >= len(crew) or len(crew) != len(roles):
            raise GarageError('vehicle has no crew seat %d' % slot)
        occupant = crew[slot]
        if occupant is not None:
            # The newcomer is not coming out of the barracks, so the seat's
            # occupant needs a berth of their own.
            self._require_berths(1)
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            nation_id, vehicle_type_id = descriptor.type.id
        except Exception as error:
            raise GarageError('the client refused the vehicle: %s' % error)
        tankman_id, compact_descr = self._recruit(
            _int(nation_id), _int(vehicle_type_id), roles[slot][0],
            cost_type_index)
        rows = dict(record.get('tankmen') or {})
        if occupant is not None:
            self._to_barracks(occupant, rows.pop(occupant))
        rows[tankman_id] = compact_descr
        crew[slot] = tankman_id
        record['crew'] = crew
        record['tankmen'] = rows
        self._touched.add(_int(record.get('id', 0)))
        self._touched_tankmen.add(tankman_id)
        self.revision += 1
        return tankman_id

    def _recruit(self, nation_id, vehicle_type_id, role, cost_type_index):
        """Charge one recruitment and return the crew member it bought."""
        cost = self._tankman_cost(cost_type_index)
        tankmen = self._tankmen_module()
        try:
            # generateTankmen's isPremium selects the premium name and icon
            # groups through generatePassport, which is not the same thing as
            # the school's gold price.  The stock garage builds every crew
            # member with False, so a recruit gets the same faces.
            compact_descrs = list(tankmen.generateTankmen(
                _int(nation_id), _int(vehicle_type_id), ((role,),), False,
                _int(cost.get('roleLevel', 0)),
                tankmen.getSkillsMask(()), False))
        except Exception as error:
            raise GarageError('the client refused the recruit: %s' % error)
        if len(compact_descrs) != 1:
            raise GarageError('the client produced no crew member to recruit')
        # Charge only once the client has actually produced the crew member,
        # so a refusal never costs the player anything.
        self._charge(cost)
        return self._next_tankman_id(), compact_descrs[0]

    def _tankman_cost(self, cost_type_index):
        costs = self._snapshot.get('tankmanCosts')
        if not isinstance(costs, (list, tuple)) or not costs:
            from gui.mods.offline_lan_0922.account_rpc import data

            costs = data.DEFAULT_TANKMAN_COSTS
        index = _int(cost_type_index)
        if not 0 <= index < len(costs):
            raise GarageError('unknown recruitment school %r'
                              % (cost_type_index,))
        return dict(costs[index])

    def _crew_role_name(self, role_index):
        tankmen = self._tankmen_module()
        names = getattr(tankmen, 'SKILL_NAMES', ())
        index = _int(role_index)
        try:
            role = names[index]
        except (IndexError, TypeError):
            raise GarageError('unknown crew role index %r' % (role_index,))
        if role not in tuple(getattr(tankmen, 'ROLES', ()) or ()):
            raise GarageError('%r is not a crew role' % (role,))
        return role

    def retrain_tankman(self, tankman_inventory_id, cost_type_index,
                        vehicle_type_compact_descr):
        """Retrain one crew member for another vehicle, at one of the schools.

        ``TankmanDescr.respecialize`` is the client's own implementation of
        this: it takes the school's minimum role level and its two role-level
        losses, works out for itself whether the new vehicle is the same class
        and applies the right one.  So the garage supplies the numbers the
        recruit window already showed and lets the client do the arithmetic.
        """
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        cost = self._tankman_cost(cost_type_index)
        compact_descr = _int(vehicle_type_compact_descr)
        if compact_descr not in self._unlocks():
            raise GarageError(
                'vehicle type %d is not researched' % compact_descr)
        nation_id, vehicle_type_id, unused_roles = self._vehicle_type_crew(
            compact_descr)
        seat = self._seated_record(tankman_id)
        if seat is not None:
            seated_type = _int(seat.get('vehicleTypeCompactDescr', 0))
            if seated_type != compact_descr:
                # A seated crew member has to match their vehicle's nation,
                # type and role or the next restore rejects the whole garage.
                raise GarageError(
                    'unload this crew member before retraining them for '
                    'another vehicle')
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
            if _int(descriptor.nationID) != nation_id:
                raise GarageError('this crew member serves another nation')
            descriptor.respecialize(
                vehicle_type_id, _int(cost.get('roleLevel', 0)),
                float(cost.get('baseRoleLoss', 0.0) or 0.0),
                float(cost.get('classChangeRoleLoss', 0.0) or 0.0),
                bool(cost.get('isPremium', False)))
            retrained = descriptor.makeCompactDescr()
        except GarageError:
            raise
        except Exception as error:
            raise GarageError('the client refused the retraining: %s' % error)
        # Charge only once the client has produced the retrained crew member,
        # so a refusal never costs the player anything.
        self._charge(cost)
        rows[tankman_id] = retrained
        self.revision += 1
        return tankman_id

    def retrain_crew(self, vehicle_type_compact_descr, choices):
        """Retrain several crew members for one vehicle in one command.

        #1513's crew operations popover sends the whole crew as flat
        tankman/school pairs, so the whole list is checked before any of it is
        charged: a crew half retrained is worse than one not retrained.
        """
        pairs = [_int(value) for value in (choices or ())]
        if len(pairs) % 2:
            raise GarageError(
                'crew retraining needs tankman/school pairs')
        # Each member is retrained and charged in turn, so the whole command
        # runs on a copy: a wallet that covers two of three would otherwise
        # leave two members retrained, two schools paid for, and the client
        # told the command failed.
        with self._transaction():
            retrained = []
            for index in range(0, len(pairs), 2):
                retrained.append(self.retrain_tankman(
                    pairs[index], pairs[index + 1],
                    vehicle_type_compact_descr))
        return retrained

    def _seated_record(self, tankman_inventory_id):
        wanted = _int(tankman_inventory_id)
        for record in self._records():
            rows = record.get('tankmen')
            if isinstance(rows, dict) and wanted in rows:
                return record
        return None

    def dismiss_tankman(self, tankman_inventory_id):
        """Let one crew member go, from a vehicle seat or from the barracks."""
        wanted = _int(tankman_inventory_id)
        barracks = self._barracks()
        if wanted in barracks:
            self._to_recycle_bin(wanted, barracks[wanted])
            del barracks[wanted]
            self._touched_tankmen.add(wanted)
            self.revision += 1
            return wanted
        for record in self._records():
            rows = record.get('tankmen')
            if not isinstance(rows, dict) or wanted not in rows:
                continue
            self._to_recycle_bin(wanted, rows[wanted])
            del rows[wanted]
            crew = list(record.get('crew') or ())
            self._remember_last_crew(
                record,
                [index for index, value in enumerate(crew)
                 if _int(value or 0) == wanted])
            record['crew'] = [None if _int(value or 0) == wanted else value
                              for value in crew]
            self._touched.add(_int(record.get('id', 0)))
            self._touched_tankmen.add(wanted)
            self.revision += 1
            return wanted
        raise GarageError('unknown tankman inventory id %d' % wanted)

    # ---- the recycle bin ------------------------------------------------

    def _recycle_bin(self):
        """Return who this account dismissed, keyed by their inventory id."""
        bin_rows = self._snapshot.get('recycleBinTankmen')
        if not isinstance(bin_rows, dict):
            bin_rows = {}
            self._snapshot['recycleBinTankmen'] = bin_rows
        return bin_rows

    def _restore_config(self):
        """Return the recycle bin's window and price, as the shop shows it."""
        config = self._snapshot.get('tankmenRestoreConfig')
        if not isinstance(config, dict):
            return {}
        result = {}
        for name in ('freeDuration', 'goldDuration', 'goldCost', 'limit'):
            result[name] = _int(config.get(name, 0) or 0)
        return result

    def _to_recycle_bin(self, tankman_id, compact_descr):
        """Remember one dismissed crew member so they can be hired back.

        #1513 reads the bin through ``RecycleBinRequester.getTankmen``, which
        keeps ``{tmanInvID: (compactDescr, dismissedAt)}`` and drops anyone
        whose dismissal is older than the shop's billable duration.
        ``time_utils.getTimeDeltaTilNow`` compares against ``datetime.utcnow``,
        so the timestamp is a plain UTC epoch second.
        """
        import time

        config = self._restore_config()
        limit = config.get('limit', 0)
        bin_rows = self._recycle_bin()
        bin_rows[_int(tankman_id)] = (compact_descr, int(time.time()))
        self._touched_recycled.add(_int(tankman_id))
        if limit > 0 and len(bin_rows) > limit:
            # The bin is a fixed-length buffer in #1513 as well; the oldest
            # dismissal is the one that falls out of it.
            for oldest in sorted(
                    bin_rows, key=lambda key: _int(bin_rows[key][1]))[
                        :len(bin_rows) - limit]:
                del bin_rows[oldest]
                self._touched_recycled.add(_int(oldest))

    def restore_tankman(self, tankman_inventory_id):
        """Hire one dismissed crew member back into the barracks.

        ``client_recycle_bin.restoreTankman`` sends only the inventory id, and
        ``restore_contoller.getTankmenRestoreInfo`` is what priced it on
        screen: free while the dismissal is younger than ``freeDuration``,
        the configured cost after that, and gone once the billable window has
        passed.  All three are settled here from the same published numbers.
        """
        import time

        wanted = _int(tankman_inventory_id)
        bin_rows = self._recycle_bin()
        if wanted not in bin_rows:
            raise GarageError('nobody was dismissed with id %d' % wanted)
        compact_descr, dismissed_at = bin_rows[wanted]
        config = self._restore_config()
        if not config:
            # Without the published window and price there is nothing to
            # charge, and handing a crew member back for free is not the
            # answer to a snapshot that never carried the shop.
            raise GarageError('the shop publishes no recovery window')
        elapsed = max(0, int(time.time()) - _int(dismissed_at))
        window = config.get('goldDuration', 0)
        if window and elapsed >= window:
            raise GarageError(
                'crew member %d can no longer be recovered' % wanted)
        # #1513 adds a BarracksSlotsValidator for exactly one berth before it
        # lets the player press the button.
        self._require_berths(1)
        if elapsed >= config.get('freeDuration', 0):
            self._charge({'gold': config.get('goldCost', 0)})
        del bin_rows[wanted]
        self._touched_recycled.add(wanted)
        self._to_barracks(wanted, compact_descr)
        self.revision += 1
        return wanted

    def change_tankman_role(self, tankman_inventory_id, role_index,
                            vehicle_type_compact_descr):
        """Retrain one crew member into another role, as the shop sells it.

        ``Inventory.changeTankmanRole`` carries the role index into
        ``tankmen.SKILL_NAMES`` and the vehicle type the new role belongs to,
        and ``TankmanChangeRole`` prices it at ``shop.changeRoleCost`` in gold.
        The client's own ``tankmenGroupCanChangeRole`` decides whether this
        crew member's group offers more than one role at all.
        """
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        vehicles = self._vehicles_module()
        compact_descr = _int(vehicle_type_compact_descr)
        names = list(getattr(tankmen, 'SKILL_NAMES', ()) or ())
        roles = set(getattr(tankmen, 'ROLES', ()) or ())
        try:
            role = names[_int(role_index)]
        except (IndexError, TypeError):
            raise GarageError('unknown crew role index %r' % (role_index,))
        if roles and role not in roles:
            raise GarageError('%s is a skill, not a crew role' % role)
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
            vehicle_type = vehicles.getVehicleType(compact_descr)
            nation_id, vehicle_type_id = vehicle_type.id
            crew_roles = tuple(vehicle_type.crewRoles)
        except Exception as error:
            raise GarageError('the client refused the role change: %s' % error)
        if _int(descriptor.nationID) != _int(nation_id):
            raise GarageError('a crew member cannot change nation')
        if not any(role in seat for seat in crew_roles):
            raise GarageError('this vehicle has no %s' % role)
        can_change = getattr(tankmen, 'tankmenGroupCanChangeRole', None)
        if can_change is not None:
            try:
                allowed = can_change(
                    _int(descriptor.nationID), _int(descriptor.gid),
                    bool(descriptor.isPremium))
            except Exception:
                allowed = True
            if not allowed:
                raise GarageError(
                    'this crew member cannot change role')
        seat = self._seat_of(tankman_id)
        if seat is not None:
            record, slot = seat
            record_roles = self._crew_roles(record)
            if (_int(record.get('vehicleTypeCompactDescr', 0)) != compact_descr
                    or not 0 <= slot < len(record_roles)
                    or role not in record_roles[slot]):
                # The restore boundary requires every seated crew member to
                # match their seat, so a change that would break it is
                # refused rather than allowed to make the save unloadable.
                raise GarageError(
                    'take this crew member out of the vehicle first')
        cost = self._crew_cost('crewChangeRoleCost')
        # Serialize before charging.  The client can accept a change and
        # still refuse to write it down, and ``_fitting`` reports one result
        # for the whole command, so nothing that can fail may follow the
        # money leaving the wallet.
        try:
            descriptor.role = role
            descriptor.vehicleTypeID = _int(vehicle_type_id)
            serialized = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the role change: %s' % error)
        self._charge(cost)
        rows[tankman_id] = serialized
        self.revision += 1
        return tankman_id

    def change_tankman_passport(self, tankman_inventory_id, is_premium,
                                is_female, first_name_group, first_name,
                                last_name_group, last_name, icon_group, icon):
        """Give one crew member another name and face.

        ``Inventory.replacePassport`` sends ``-1`` for every field the player
        left alone, and ``TankmanDescr.validatePassport`` turns the request
        into the exact four values ``replacePassport`` applies.  The price is
        the one ``TankmanChangePassport`` shows, which depends on the crew
        member's current sex rather than the requested one.
        """
        rows, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(rows[tankman_id])
        except Exception as error:
            raise GarageError('the client refused the passport: %s' % error)
        if bool(_int(is_premium)) != bool(descriptor.isPremium):
            raise GarageError(
                'the passport request names the wrong crew member kind')
        cost = self._crew_cost(
            'crewFemalePassportCost' if descriptor.isFemale
            else 'crewPassportCost')
        wanted_female = _int(is_female)
        try:
            accepted, reason, context = descriptor.validatePassport(
                bool(_int(is_premium)),
                None if wanted_female < 0 else bool(wanted_female),
                _int(first_name_group),
                None if _int(first_name) < 0 else _int(first_name),
                _int(last_name_group),
                None if _int(last_name) < 0 else _int(last_name),
                _int(icon_group),
                None if _int(icon) < 0 else _int(icon))
        except Exception as error:
            raise GarageError('the client refused the passport: %s' % error)
        if not accepted:
            raise GarageError('the client refused the passport: %s' % reason)
        try:
            descriptor.replacePassport(context)
            serialized = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the passport: %s' % error)
        self._charge(cost)
        rows[tankman_id] = serialized
        self.revision += 1
        return tankman_id

    def _crew_cost(self, key):
        """Return one published crew-shop price as a currency mapping.

        An absent price is refused rather than treated as free: the snapshot
        and the shop stream are built from the same key, so a missing one
        means the client was shown a price this garage cannot charge.
        """
        cost = self._snapshot.get(key)
        if not isinstance(cost, dict):
            raise GarageError('the shop publishes no %s' % key)
        return dict((str(currency), _int(amount))
                    for currency, amount in cost.items())

    def _seat_of(self, tankman_id):
        """Return the record and slot one crew member occupies, or None."""
        wanted = _int(tankman_id)
        for record in self._records():
            crew = list(record.get('crew') or ())
            for slot, value in enumerate(crew):
                if value is not None and _int(value) == wanted:
                    return record, slot
        return None

    def _crew_roles(self, record):
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            return tuple(descriptor.type.crewRoles)
        except Exception as error:
            raise GarageError('the client refused the vehicle crew: %s' % error)

    def _check_tankman_fits(self, record, roles, slot, compact_descr):
        """Refuse a seat this crew member cannot hold without retraining.

        The restore boundary requires every seated crew member to match their
        vehicle's nation, type and role, so a mismatch would make the whole
        save unrestorable.  Retraining is how a crew member changes vehicle;
        a seat is not.
        """
        tankmen = self._tankmen_module()
        vehicles = self._vehicles_module()
        try:
            descriptor = tankmen.TankmanDescr(compact_descr)
            vehicle = vehicles.VehicleDescr(compactDescr=record['compDescr'])
            nation_id, vehicle_type_id = vehicle.type.id
        except Exception as error:
            raise GarageError('the client refused the crew member: %s' % error)
        if _int(descriptor.nationID) != _int(nation_id):
            raise GarageError('this crew member serves another nation')
        if _int(descriptor.vehicleTypeID) != _int(vehicle_type_id):
            raise GarageError(
                'this crew member is trained for another vehicle: retrain '
                'them first')
        if descriptor.role != roles[slot][0]:
            raise GarageError(
                'this crew member is a %s and seat %d is for a %s'
                % (descriptor.role, slot, roles[slot][0]))

    @staticmethod
    def _add_money(total, amount):
        for currency, value in amount.items():
            total[currency] = _int(total.get(currency, 0)) + _int(value)
        return total

    def _sold_off_vehicle(self, record, compact_descr, remaining):
        """Refund one item the sell dialog listed as mounted on the vehicle.

        Refusing an item the vehicle does not carry keeps a malformed list
        from minting credits.  The owned count comes down by what was sold and
        then back up to whatever a remaining vehicle still carries, so no
        vehicle is ever left mounting an item the account no longer owns.
        """
        compact_descr = _int(compact_descr)
        item_type = self._item_type(compact_descr)
        if item_type not in (OPTIONAL_DEVICE_ITEM_TYPE, SHELL_ITEM_TYPE,
                             EQUIPMENT_ITEM_TYPE):
            raise GarageError('installed modules are already included in the vehicle sale')
        carried = self._held(record, item_type, compact_descr)
        if item_type != SHELL_ITEM_TYPE:
            # Only rounds are carried by the dozen; #1513 pays one unit for
            # each mounted equipment and optional device.
            carried = min(1, carried)
        if carried <= 0:
            raise GarageError(
                'vehicle %d does not carry item %d' % (
                    _int(record.get('id', 0)), compact_descr))
        owned = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        left = max(0, _int(owned.get(compact_descr, 0)) - carried)
        self._set_owned(
            compact_descr, item_type,
            max(left, self._mounted(compact_descr, item_type, remaining)))
        return self._item_refund(compact_descr, carried)

    def _sold_out_of_inventory(self, compact_descr):
        """Refund the whole stored stack of one module, as #1513 prices it."""
        compact_descr = _int(compact_descr)
        item_type = self._item_type(compact_descr)
        owned = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        count = _int(owned.get(compact_descr, 0))
        if count <= 0:
            raise GarageError(
                'the account does not own item %d' % compact_descr)
        mounted = self._mounted(compact_descr, item_type, self._records())
        if mounted >= count:
            raise GarageError(
                'item %d is mounted on %d vehicle(s) and cannot be sold out '
                'of the inventory' % (compact_descr, mounted))
        self._set_owned(compact_descr, item_type, mounted)
        return self._item_refund(compact_descr, count - mounted)

    @staticmethod
    def _carried(record, item_type, compact_descr):
        return _int(record.get('inventoryItems', {}).get(
            int(item_type), {}).get(compact_descr, 0))

    def _held(self, record, item_type, compact_descr):
        """Return what one vehicle holds of an item, its descriptor first.

        A record's optional-device row is written when a device is mounted,
        but a record rebuilt for a save arrives with a stock row beside the
        saved descriptor, and only the descriptor knows what is in the slots.
        Reading the row alone would make a device mounted before a restart
        invisible, and one lot of it would mount on a second vehicle for
        nothing. Modules likewise live in the descriptor, while a historical
        record's module rows may also contain researchable spare parts.
        """
        if int(item_type) == OPTIONAL_DEVICE_ITEM_TYPE:
            return _int(self._mounted_devices(record).get(
                _int(compact_descr), 0))
        for module_type, attribute in MODULE_ATTRIBUTES:
            if int(item_type) == module_type:
                descriptor = self._descriptor(record)
                mounted = getattr(descriptor, attribute).compactDescr
                return int(_int(mounted) == _int(compact_descr))
        return self._carried(record, item_type, compact_descr)

    def _mounted(self, compact_descr, item_type, records):
        """Return how many of one item the given vehicles hold between them.

        Each installed component, optional device and carried supply uses
        its own physical copy, including when two vehicles share a type.
        """
        counts = [self._held(record, item_type, compact_descr)
                  for record in records]
        if int(item_type) in STOCKED_ITEM_TYPES:
            return sum(counts)
        return max(counts) if counts else 0

    def unlock(self, vehicle_type_compact_descr, unlock_index):
        """Research one item this vehicle leads to.

        The cost, the target and its prerequisites all come from the live
        ``VehicleType.unlocksDescrs``, so the tech tree the player sees and the
        one the garage charges for are the same data.
        """
        vehicles = self._vehicles_module()
        compact_descr = _int(vehicle_type_compact_descr)
        try:
            vehicle_type = vehicles.getVehicleType(compact_descr)
            descriptors = list(
                getattr(vehicle_type, 'unlocksDescrs', ()) or ())
        except Exception as error:
            raise GarageError('unknown research source: %s' % error)
        index = _int(unlock_index)
        if not 0 <= index < len(descriptors):
            raise GarageError('unknown research step %d' % index)
        descriptor = tuple(descriptors[index])
        if len(descriptor) < 2:
            raise GarageError('research step %d is malformed' % index)
        target = _int(descriptor[1])
        cost = max(0, _int(descriptor[0]))
        unlocks = self._unlocks()
        if compact_descr not in unlocks:
            raise GarageError('the research source is not unlocked')
        for required in descriptor[2:]:
            if _int(required) not in unlocks:
                raise GarageError(
                    'research step %d still needs item %d' % (
                        index, _int(required)))
        if target in unlocks:
            return {'compactDescr': target, 'vehicleXP': 0, 'freeXP': 0}

        free_modules = []
        if self._item_type(target) == VEHICLE_ITEM_TYPE:
            try:
                free_modules = [int(value) for value in
                                vehicles.getVehicleType(target).autounlockedItems]
            except Exception as error:
                raise GarageError('the client refused the research target: %s' % error)
        wallet = self._wallet()
        vehicle_xp = self._snapshot.setdefault('vehicleXP', {})
        available = max(0, _int(vehicle_xp.get(compact_descr, 0)))
        from_vehicle = min(available, cost)
        remainder = cost - from_vehicle
        if remainder > wallet['freeXP']:
            raise GarageError(
                'research needs %d more experience' % (
                    remainder - wallet['freeXP']))
        vehicle_xp[compact_descr] = available - from_vehicle
        wallet['freeXP'] = wallet['freeXP'] - remainder
        unlocks.add(target)
        unlocks.update(free_modules)
        self._price(target)
        self.revision += 1
        return {'compactDescr': target, 'vehicleXP': from_vehicle,
                'freeXP': remainder}

    def exchange_gold(self, gold):
        """Convert gold into credits at the published shop rate."""
        amount = _int(gold)
        if amount <= 0:
            raise GarageError('a gold exchange must be positive')
        rate = max(1, _int(self._snapshot.get('goldExchangeRate', 0)) or
                   GOLD_EXCHANGE_RATE)
        self._charge({'gold': amount})
        self._pay_back({'credits': amount * rate})
        self.revision += 1
        return {'gold': amount, 'credits': amount * rate}

    def convert_to_free_xp(self, vehicle_type_compact_descrs, experience):
        """Turn elite vehicle experience into free experience for gold.

        #1513 publishes ``freeXPConversion`` as ``(rate, goldCost)``: ``rate``
        vehicle experience becomes ``rate`` free experience for ``goldCost``
        gold, which is why the gold charged is the experience divided by the
        rate.
        """
        wanted = _int(experience)
        if wanted <= 0:
            raise GarageError('an experience conversion must be positive')
        rate, gold_cost = FREE_XP_CONVERSION
        vehicle_xp = self._snapshot.setdefault('vehicleXP', {})
        sources = []
        for value in (vehicle_type_compact_descrs or ()):
            compact_descr = _int(value)
            if compact_descr not in sources:
                sources.append(compact_descr)
        for compact_descr in sources:
            if not self._is_elite(compact_descr):
                raise GarageError(
                    'vehicle %d still has research left' % compact_descr)
        available = sum(max(0, _int(vehicle_xp.get(key, 0)))
                        for key in sources)
        if available < wanted:
            raise GarageError(
                'the selected vehicles hold %d experience, not %d' % (
                    available, wanted))
        gold = (wanted + rate - 1) // rate * gold_cost
        self._charge({'gold': gold})
        remaining = wanted
        for key in sources:
            if remaining <= 0:
                break
            taken = min(remaining, max(0, _int(vehicle_xp.get(key, 0))))
            vehicle_xp[key] = _int(vehicle_xp.get(key, 0)) - taken
            remaining -= taken
        wallet = self._wallet()
        wallet['freeXP'] = wallet['freeXP'] + wanted
        self.revision += 1
        return {'freeXP': wanted, 'gold': gold}

    def buy_slot(self):
        """Buy one garage slot at the published price."""
        self._charge({'gold': GARAGE_SLOT_GOLD_PRICE})
        self._snapshot['accountSlots'] = _int(
            self._snapshot.get('accountSlots', 0)) + 1
        self.revision += 1
        return self._snapshot['accountSlots']

    def _default_vehicle_settings(self):
        """Return the settings mask a vehicle built at startup would carry.

        A bought vehicle arriving with auto-repair and auto-resupply off would
        silently differ from every vehicle the garage built itself.
        """
        published = self._snapshot.get('defaultVehicleSettings')
        if published is not None:
            return _int(published)
        from gui.mods.offline_lan_0922 import vehicle_records

        return _int(vehicle_records.default_vehicle_settings())

    def _is_elite(self, vehicle_type_compact_descr):
        """Return whether nothing this vehicle researches is left to unlock.

        #1513 only converts the experience of an elite vehicle, because the
        experience on a vehicle still being researched is what pays for the
        rest of its own tree.
        """
        vehicles = self._vehicles_module()
        try:
            vehicle_type = vehicles.getVehicleType(
                _int(vehicle_type_compact_descr))
            descriptors = tuple(
                getattr(vehicle_type, 'unlocksDescrs', ()) or ())
        except Exception as error:
            raise GarageError('unknown vehicle type: %s' % error)
        unlocks = self._unlocks()
        for row in descriptors:
            row = tuple(row)
            if len(row) >= 2 and _int(row[1]) not in unlocks:
                return False
        return True

    def buy_berths(self):
        """Buy one barracks berth block at the published price."""
        self._charge({'gold': BARRACKS_BERTH_GOLD_PRICE})
        self._snapshot['accountBerths'] = _int(
            self._snapshot.get('accountBerths', 0)) + BARRACKS_BERTH_COUNT
        self.revision += 1
        return self._snapshot['accountBerths']

    def _balances(self):
        """Return the account balances without writing anything.

        A snapshot written before the ledger existed carries no wallet.  Zero
        is the one value that is wrong for both save modes: it would refuse
        every purchase in a career and turn the historical sandbox's unlimited
        balance into whatever the next battle paid.  Read the sandbox balance
        instead, which is what such a save had.
        """
        from gui.mods.offline_lan_0922.account_rpc import economy

        wallet = self._snapshot.get('wallet')
        wallet = wallet if isinstance(wallet, dict) else {}
        return dict(
            (name, max(0, _int(
                wallet.get(name, economy.SANDBOX_WALLET[name]))))
            for name in ('credits', 'gold', 'freeXP'))

    def _wallet(self):
        """Return the mutable account balances, seeding them if a save had none.

        Only a path that is about to change a balance may call this: it writes
        the seeded wallet into the snapshot.  A check that can still refuse
        reads ``_balances`` instead, so a refused request leaves the save
        exactly as it found it.
        """
        wallet = self._snapshot.get('wallet')
        if not isinstance(wallet, dict):
            wallet = {}
            self._snapshot['wallet'] = wallet
        wallet.update(self._balances())
        return wallet
