"""Persist the offline garage beside the other user-owned configuration.

Owner boundary: ``AccountState`` owns ``account_state.json`` and nothing else,
so the garage keeps a sibling ``garage_state.json``.  One file per owner means a
corrupt garage file cannot take out the saved interface settings, and neither
writer has to understand the other's schema.

Why the keys are not inventory ids: ``bootstrap._selected_vehicle`` numbers
vehicles ``len(vehicle_records) + 1`` while walking a type list whose FIRST
entry is the vehicle named in ``config.json``, and it numbers crew from 100001
upward in that same order.  Changing the configured vehicle therefore renumbers
every id.  This store keys vehicles on ``vehicleTypeCompactDescr`` and crew on
the slot index inside their vehicle, both of which survive a renumbering.

Compact descriptors are Python 2 byte strings, so they are base64 text on disk.
"""

from __future__ import print_function

import base64
import copy
import os
import sys

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922.account_rpc import data
from gui.mods.offline_lan_0922.account_rpc.garage import mirror_shells_layout


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


# Schema 2 fixes schema 1's vehicle settings, which were stored as a shifted
# bit index instead of a VEHICLE_SETTINGS_FLAG value.  Schema 3 drops files
# written before every vehicle was fitted with its top modules and its three
# consumables.  Schema 4 adds the bounded receipt journal that makes battle
# crew XP idempotent.  Schema 5 adds the account ledger: the balances, the
# researched items, the per-vehicle experience and which vehicles are owned.
# Every earlier readable schema upgrades on the next save.
SCHEMA = 6
READABLE_SCHEMAS = (3, 4, 5, SCHEMA)
STATE_FILE_NAME = 'garage_state.json'

# ``repair`` is (outstanding cost, remaining health): a vehicle a battle left
# damaged has to come back damaged, or a restart would be a free repair.
_VEHICLE_INT_KEYS = (
    'eqs', 'eqsLayout', 'shells', 'shellsLayoutIdx', 'repair')
_ARTEFACT_ITEM_TYPES = (9, 10, 11)
_CUSTOMIZATION_SEASONS = (1, 2, 4, 8, 15)
MAX_BATTLE_RECEIPTS = 512


def _log(message):
    sys.stdout.write('[Offline LAN 0.9.22] %s\n' % message)


def _encode_bytes(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    return None


def _decode_bytes(value):
    try:
        return base64.b64decode(value.encode('ascii'))
    except Exception:
        return None


def _int_list(value):
    if not isinstance(value, (list, tuple)):
        return None
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, integer_types):
            return None
        result.append(int(item))
    return result


def _ledger_payload(snapshot):
    """Return the account balances and research a save must keep.

    The garage already owns what the player has; the ledger is the rest of it.
    Keeping both in one file means one JSON replacement commits a purchase and
    the item it bought together, so a hard kill can never bank one without the
    other.
    """
    wallet = snapshot.get('wallet')
    wallet = wallet if isinstance(wallet, dict) else {}
    vehicle_xp = {}
    saved_xp = snapshot.get('vehicleXP')
    if isinstance(saved_xp, dict):
        for compact_descr, experience in saved_xp.items():
            try:
                vehicle_xp[str(int(compact_descr))] = max(
                    0, int(experience))
            except (TypeError, ValueError):
                continue
    unlocks = snapshot.get('unlockItemCompactDescrs')
    # A crew member in the barracks belongs to no vehicle, so there is no
    # slot to store them against.  Their inventory id is this client's own
    # bookkeeping and means nothing to the next one, so only the descriptor
    # is saved and the restore hands out fresh ids.
    barracks = []
    for compact_descr in (snapshot.get('barracksTankmen') or {}).values():
        encoded = _encode_bytes(compact_descr)
        if encoded is not None:
            barracks.append(encoded)
    return {
        'wallet': dict(
            (name, max(0, int(wallet.get(name, 0) or 0)))
            for name in ('credits', 'gold', 'freeXP')),
        'vehicleXP': vehicle_xp,
        'unlocks': sorted(int(value) for value in (unlocks or ())),
        'slots': max(0, int(snapshot.get('accountSlots', 0) or 0)),
        'berths': max(0, int(snapshot.get('accountBerths', 0) or 0)),
        'barracks': sorted(barracks),
    }


def _floor_account_stock(snapshot):
    """Own at least what the garage already holds.

    A round, a consumable and an optional device belong to the account, and
    ``garage.GarageState`` adds them up across every vehicle to decide what a
    resupply must buy.  A depot count below that sum would let one lot of them
    be mounted twice, so the whole garage is the floor under the depot.  Older
    saves recorded the largest count one vehicle carried rather than the total,
    and this is what raises them.
    """
    published = snapshot.setdefault('inventoryItems', {})
    for item_type in _ARTEFACT_ITEM_TYPES:
        totals = {}
        for record in _records(snapshot):
            items = record.get('inventoryItems')
            if not isinstance(items, dict):
                continue
            for compact_descr, count in (_int_map(
                    items.get(item_type) or {}) or {}).items():
                totals[compact_descr] = totals.get(compact_descr, 0) + count
        if not totals:
            continue
        target = published.setdefault(item_type, {})
        for compact_descr, count in totals.items():
            target[compact_descr] = max(
                int(target.get(compact_descr, 0)), int(count))


def _apply_ledger(staged, stored):
    """Overlay one saved ledger, keeping the current catalogue authoritative."""
    ledger = stored.get('ledger')
    if not isinstance(ledger, dict):
        # A file written before the ledger existed keeps the seeded balances
        # rather than starting the save at zero.
        return False
    wallet = ledger.get('wallet')
    if isinstance(wallet, dict):
        staged['wallet'] = dict(
            (name, max(0, _int_value(wallet.get(name))))
            for name in ('credits', 'gold', 'freeXP'))
    saved_xp = ledger.get('vehicleXP')
    if isinstance(saved_xp, dict):
        published = staged.setdefault('vehicleXP', {})
        for compact_descr, experience in saved_xp.items():
            try:
                key = int(compact_descr)
            except (TypeError, ValueError):
                continue
            # Only vehicles this client still offers keep their experience.
            if key in published:
                published[key] = max(0, _int_value(experience))
    unlocks = ledger.get('unlocks')
    if isinstance(unlocks, (list, tuple)):
        published = staged.get('unlockItemCompactDescrs')
        if isinstance(published, set):
            for value in unlocks:
                try:
                    published.add(int(value))
                except (TypeError, ValueError):
                    continue
    for name, key in (('slots', 'accountSlots'), ('berths', 'accountBerths')):
        if name in ledger:
            staged[key] = max(_int_value(ledger[name]),
                              int(staged.get(key, 0) or 0))
    barracks = ledger.get('barracks')
    if isinstance(barracks, (list, tuple)):
        staged['barracksTankmen'] = _restored_barracks(staged, barracks)
    return True


def _restored_barracks(staged, encoded_descriptors):
    """Give every saved barracks crew member an id no vehicle is using."""
    used = set()
    for record in _records(staged):
        for tankman_id in (record.get('tankmen') or ()):
            used.add(_int_value(tankman_id))
    next_id = (max(used) + 1) if used else 100001
    restored = {}
    for encoded in encoded_descriptors:
        decoded = _decode_bytes(encoded)
        if not decoded:
            continue
        restored[next_id] = decoded
        next_id += 1
    return restored


def _int_value(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_map(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for raw_key, raw_value in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            return None
        if isinstance(raw_value, bool) or not isinstance(
                raw_value, integer_types):
            return None
        result[key] = int(raw_value)
    return result


class GarageStore(object):
    """Load and save the mutable parts of one garage snapshot."""

    def __init__(self, path=port_config.ACTIVE_SAVE_SLOT):
        if path is port_config.ACTIVE_SAVE_SLOT:
            path = port_config.save_slot_state_path(STATE_FILE_NAME)
        self._path = path
        self._dirty = False
        self._battle_receipts = []
        self._receipts_loaded = False

    # ---- writing --------------------------------------------------------

    def mark_dirty(self):
        self._dirty = True

    def flush(self, snapshot):
        """Write the snapshot if a mutation is pending.

        Fittings happen at click speed, so writing on each accepted change is
        cheap and means a hard client kill cannot lose an applied change.
        """
        if not self._dirty or self._path is None:
            return False
        payload = self._payload(snapshot)
        try:
            port_config.write_json(self._path, payload)
        except (IOError, OSError) as error:
            _log('the garage state could not be saved: %s' % error)
            return False
        self._dirty = False
        return True

    def _payload(self, snapshot, battle_receipts=None):
        vehicles = {}
        for record in _records(snapshot):
            key = record.get('vehicleTypeCompactDescr')
            if key is None:
                continue
            stored = {}
            compact_descr = _encode_bytes(record.get('compDescr'))
            if compact_descr is not None:
                stored['compDescr'] = compact_descr
            # The launcher reads this file without a client to resolve a
            # compact descriptor with, so the save names its own vehicles.
            type_name = record.get('vehicleTypeName')
            if isinstance(type_name, str) and type_name:
                stored['name'] = type_name
            outfits = {}
            if isinstance(record.get('outfits'), dict):
                for raw_season, outfit_data in record['outfits'].items():
                    try:
                        season = int(raw_season)
                        descriptor, enabled = outfit_data
                    except (TypeError, ValueError):
                        continue
                    encoded = _encode_bytes(descriptor)
                    if season in _CUSTOMIZATION_SEASONS and encoded is not None:
                        outfits[str(season)] = [encoded, bool(enabled)]
            if outfits:
                stored['outfits'] = outfits
            for name in _VEHICLE_INT_KEYS:
                value = _int_list(record.get(name))
                if value is not None:
                    stored[name] = value
            # What the player asked the vehicle to carry, which a battle
            # deliberately does not change: it is what auto-load buys back.
            layout = _int_list(
                (record.get('shellsLayout') or {}).get(
                    tuple(record.get('shellsLayoutIdx') or ())))
            if layout is not None:
                stored['shellsLayout'] = layout
            try:
                stored['settings'] = int(record.get('settings', 0) or 0)
            except (TypeError, ValueError):
                stored['settings'] = 0
            crew = {}
            tankmen = record.get('tankmen')
            order = list(record.get('crew') or ())
            if isinstance(tankmen, dict):
                for slot, tankman_id in enumerate(order):
                    if tankman_id is None:
                        # An unloaded seat has to be saved as empty; leaving
                        # it out would let the next start's freshly built
                        # crew member sit back down.
                        crew[str(slot)] = None
                        continue
                    encoded = _encode_bytes(tankmen.get(tankman_id))
                    if encoded is not None:
                        crew[str(slot)] = encoded
            if crew:
                stored['crew'] = crew
            vehicles[str(int(key))] = stored

        owned = {}
        published = snapshot.get('inventoryItems')
        if isinstance(published, dict):
            for item_type, items in published.items():
                try:
                    item_type = int(item_type)
                except (TypeError, ValueError):
                    continue
                if item_type not in _ARTEFACT_ITEM_TYPES:
                    continue
                # An empty depot is saved as an empty depot.  A row is dropped
                # when its count reaches zero, so omitting the whole type here
                # would read back on the next start as "this save predates the
                # depot" and hand the stock supply out all over again.
                counts = _int_map(items)
                owned[str(item_type)] = dict(
                    (str(compact_descr), count)
                    for compact_descr, count in counts.items())
        if battle_receipts is None:
            battle_receipts = self._battle_receipts
        return {
            'schema': SCHEMA, 'vehicles': vehicles, 'owned': owned,
            'ledger': _ledger_payload(snapshot),
            'battleCrewReceipts': list(battle_receipts)[
                -MAX_BATTLE_RECEIPTS:],
        }

    def owned_vehicle_names(self):
        """Return the ``nation:vehicle`` names this save owns.

        A save written before records carried their names answers with the
        vehicles it can name and leaves out the rest; the client fills the
        missing ones in on its next start.
        """
        stored = self._read()
        vehicles = (stored or {}).get('vehicles')
        if not isinstance(vehicles, dict):
            return []
        names = []
        for value in vehicles.values():
            name = value.get('name') if isinstance(value, dict) else None
            if isinstance(name, str) and name:
                names.append(name)
        return sorted(set(names))

    def owned_vehicle_types(self):
        """Return the vehicle type compact descriptors this save owns.

        The saved vehicle map is already keyed on the type compact descriptor,
        so ownership needs no second list that could disagree with it.  An
        empty result means "nothing saved yet", which is what a new save is.
        """
        stored = self._read()
        vehicles = (stored or {}).get('vehicles')
        if not isinstance(vehicles, dict):
            return []
        owned = []
        for key in vehicles:
            try:
                owned.append(int(key))
            except (TypeError, ValueError):
                continue
        return owned

    def apply_battle_crew_xp(self, snapshot, receipt_id,
                             vehicle_type_compact_descr, battle_xp,
                             xp_to_tankman_flag, tankmen_module=None,
                             rewards=None, health=None, vehicles_module=None,
                             shells_fired=None, equipment_used=None):
        """Apply and persist one battle's whole settlement exactly once.

        The compact crew descriptors, the earnings, the damage the battle did
        and their receipt marker share one JSON replacement.  A receipt
        retried after a disconnect therefore either applies the whole
        settlement or observes the durable marker; it can never award the XP
        twice, and it can never bill the same damage twice either.
        """
        receipt_id = str(receipt_id or '')[:96]
        if not receipt_id:
            raise ValueError('battle crew receipt id is empty')
        self._ensure_receipts_loaded()
        for row in self._battle_receipts:
            if row['receipt_id'] == receipt_id:
                result = dict(row)
                result['applied'] = False
                return result

        from gui.mods.offline_lan_0922.account_rpc.garage import GarageState
        staged = copy.deepcopy(snapshot)
        state = GarageState(staged, tankmen_module=tankmen_module,
                            vehicles_module=vehicles_module)
        result = state.award_battle_crew_xp(
            vehicle_type_compact_descr, battle_xp, xp_to_tankman_flag)
        if health is not None:
            result['repair'] = state.settle_battle_damage(
                vehicle_type_compact_descr, health)
        if shells_fired:
            result['shells_spent'] = state.settle_battle_ammunition(
                vehicle_type_compact_descr, shells_fired)
        if equipment_used:
            result['consumables_spent'] = state.settle_battle_consumables(
                vehicle_type_compact_descr, equipment_used)
        if rewards is not None:
            # The crew award and the credits it was earned beside share one
            # JSON replacement, so a retried receipt can never bank one
            # without the other.
            result['earnings'] = state.award_battle_earnings(
                vehicle_type_compact_descr, rewards,
                accelerated=bool(result['accelerated']))
        staged = state.snapshot()
        marker = {
            'receipt_id': receipt_id,
            'accelerated': bool(result['accelerated']),
            'vehicle_id': int(result['vehicle_id']),
        }
        next_receipts = (list(self._battle_receipts) + [marker])[
            -MAX_BATTLE_RECEIPTS:]
        if self._path is not None:
            port_config.write_json(
                self._path, self._payload(staged, next_receipts))
        snapshot.clear()
        snapshot.update(staged)
        self._battle_receipts = next_receipts
        self._receipts_loaded = True
        self._dirty = False
        result['receipt_id'] = receipt_id
        result['applied'] = True
        return result

    # ---- reading --------------------------------------------------------

    def apply(self, snapshot, validator=None):
        """Overlay the saved garage onto a freshly built bootstrap snapshot.

        The snapshot always comes from the current client, so an unknown or
        stale key is skipped rather than trusted.  Restoration and validation
        happen on a detached copy; any problem leaves the bootstrap snapshot
        byte-for-byte untouched.  ``validator`` may additionally exercise the
        exact client's native compact-descriptor parsers before commit.
        """
        stored = self._read()
        if stored is None:
            self._receipts_loaded = True
            return False
        staged = copy.deepcopy(snapshot)
        vehicles = stored.get('vehicles')
        if not isinstance(vehicles, dict):
            vehicles = {}
        applied = 0
        for record in _records(staged):
            key = record.get('vehicleTypeCompactDescr')
            if key is None:
                continue
            saved = vehicles.get(str(int(key)))
            if isinstance(saved, dict) and self._apply_vehicle(record, saved):
                applied += 1

        owned = stored.get('owned')
        if isinstance(owned, dict):
            published = staged.setdefault('inventoryItems', {})
            prices = staged.get('shopItemPrices') or {}
            for item_type in _ARTEFACT_ITEM_TYPES:
                items = owned.get(str(item_type), owned.get(item_type))
                counts = _int_map(items)
                if counts is None:
                    # A save written before the depot was kept, or one whose
                    # depot cannot be read, keeps whatever stock the fresh
                    # build handed out.
                    continue
                # The save is the depot.  Taking the larger of the two would
                # hand the stock supply back every time the client started,
                # which is a refund for every round and consumable a battle
                # spent.
                target = {}
                for compact_descr, count in counts.items():
                    # A saved file can still name an item this client no
                    # longer offers, and an item with no price is one the
                    # current catalogue does not know.
                    if compact_descr in prices:
                        target[compact_descr] = int(count)
                published[item_type] = target
        _floor_account_stock(staged)

        _apply_ledger(staged, stored)

        try:
            data._validate_selected_vehicle(staged)
            if validator is not None:
                validator(staged)
        except Exception as error:
            _log('the saved garage state is inconsistent; using the stock '
                 'garage (%s)' % error)
            # Do not trust receipt markers whose matching crew descriptors
            # could not be restored.  A pending server receipt may now safely
            # rebuild the award on the fresh bootstrap garage.
            self._battle_receipts = []
            self._receipts_loaded = True
            return False

        snapshot.clear()
        snapshot.update(staged)
        self._battle_receipts = self._validated_battle_receipts(
            stored.get('battleCrewReceipts'))
        self._receipts_loaded = True
        if applied:
            _log('restored the saved garage for %d vehicle(s)' % applied)
        return True

    def _ensure_receipts_loaded(self):
        if self._receipts_loaded:
            return
        stored = self._read()
        if stored is not None:
            self._battle_receipts = self._validated_battle_receipts(
                stored.get('battleCrewReceipts'))
        self._receipts_loaded = True

    @staticmethod
    def _validated_battle_receipts(value):
        rows = []
        for raw in value if isinstance(value, list) else ():
            if not isinstance(raw, dict):
                continue
            receipt_id = str(raw.get('receipt_id') or '')[:96]
            try:
                vehicle_id = int(raw.get('vehicle_id', 0))
            except (TypeError, ValueError):
                continue
            if not receipt_id or vehicle_id <= 0:
                continue
            rows.append({
                'receipt_id': receipt_id,
                'accelerated': bool(raw.get('accelerated', False)),
                'vehicle_id': vehicle_id,
            })
        return rows[-MAX_BATTLE_RECEIPTS:]

    def _apply_vehicle(self, record, saved):
        changed = False
        # Python 2 json.load returns unicode, so this must not test for str.
        decoded = _decode_bytes(saved.get('compDescr'))
        if decoded:
            record['compDescr'] = decoded
            changed = True
        outfits = saved.get('outfits')
        if isinstance(outfits, dict):
            restored_outfits = {}
            for raw_season, outfit_data in outfits.items():
                try:
                    season = int(raw_season)
                    encoded, enabled = outfit_data
                except (TypeError, ValueError):
                    continue
                descriptor = _decode_bytes(encoded)
                if (season in _CUSTOMIZATION_SEASONS and descriptor is not None
                        and isinstance(enabled, bool)):
                    restored_outfits[season] = (descriptor, enabled)
            if restored_outfits:
                record['outfits'] = restored_outfits
                changed = True
        for name in _VEHICLE_INT_KEYS:
            value = _int_list(saved.get(name))
            if value is not None:
                record[name] = value
                changed = True
        if 'settings' in saved:
            try:
                record['settings'] = int(saved['settings'])
                changed = True
            except (TypeError, ValueError):
                pass
        crew = saved.get('crew')
        tankmen = record.get('tankmen')
        order = list(record.get('crew') or ())
        if isinstance(crew, dict) and isinstance(tankmen, dict):
            for raw_slot, encoded in crew.items():
                try:
                    slot = int(raw_slot)
                except (TypeError, ValueError):
                    continue
                if not 0 <= slot < len(order):
                    continue
                if encoded is None:
                    tankmen.pop(order[slot], None)
                    order[slot] = None
                    record['crew'] = order
                    changed = True
                    continue
                decoded = _decode_bytes(encoded)
                if decoded and order[slot] is not None:
                    tankmen[order[slot]] = decoded
                    changed = True
        layout = _int_list(saved.get('shellsLayout'))
        key = tuple(record.get('shellsLayoutIdx') or ())
        if layout is not None and key and not len(layout) % 2:
            record['shellsLayout'] = {key: layout}
            changed = True
        else:
            # A save written before the layout was kept separately loaded
            # exactly what it asked for.
            mirror_shells_layout(record)
        # Mounted shells must stay consistent with the shell inventory that
        # data._validate_selected_vehicle cross-checks.
        shells = _int_list(record.get('shells'))
        if shells is not None and not len(shells) % 2:
            pairs = {}
            for index in range(0, len(shells), 2):
                pairs[shells[index]] = shells[index + 1]
            record.setdefault('inventoryItems', {})[10] = pairs
        # A mounted consumable is what this vehicle holds of the account's
        # stock, so the record has to say so or a second vehicle would mount
        # the same one lot of it for nothing.
        consumables = {}
        for compact_descr in (record.get('eqs') or ()):
            try:
                compact_descr = int(compact_descr)
            except (TypeError, ValueError):
                continue
            if compact_descr:
                consumables[compact_descr] = 1
        record.setdefault('inventoryItems', {})[11] = consumables
        return changed

    def _read(self):
        if self._path is None:
            return None
        for path in (self._path, self._path + '.bak'):
            if not os.path.isfile(path):
                continue
            try:
                import json
                with open(path, 'rb') as stream:
                    value = json.load(stream)
            except (IOError, OSError, ValueError):
                _log('the saved garage state is unreadable; using the '
                     'stock garage')
                continue
            if not isinstance(value, dict):
                _log('the saved garage state has an unexpected shape; using '
                     'the stock garage')
                continue
            if value.get('schema') not in READABLE_SCHEMAS:
                _log('the saved garage state uses schema %r, not one of %r; '
                     'using the stock garage' % (
                         value.get('schema'), READABLE_SCHEMAS))
                return None
            return value
        return None


def _records(snapshot):
    if not isinstance(snapshot, dict):
        return []
    records = snapshot.get('vehicles')
    if isinstance(records, (list, tuple)) and records:
        return [record for record in records if isinstance(record, dict)]
    return [snapshot] if snapshot.get('compDescr') else []
