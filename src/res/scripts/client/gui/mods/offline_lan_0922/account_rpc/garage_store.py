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
from gui.mods.offline_lan_0922.account_rpc import data, economy
from gui.mods.offline_lan_0922.account_rpc.garage import (
    STOCKED_ITEM_TYPES, mirror_shells_layout)


try:
    integer_types = (int, long)
    string_types = (basestring,)
except NameError:
    integer_types = (int,)
    string_types = (str,)


# Schema 2 fixes schema 1's vehicle settings, which were stored as a shifted
# bit index instead of a VEHICLE_SETTINGS_FLAG value.  Schema 3 drops files
# written before every vehicle was fitted with its top modules and its three
# consumables.  Schema 4 adds the bounded receipt journal that makes battle
# crew XP idempotent.  Schema 5 adds the account ledger: the balances, the
# researched items, the per-vehicle experience and which vehicles are owned.
# Schema 7 preserves module stock as well as consumables and the actual award
# needed to replay a settlement after the post-battle file failed to commit.
SCHEMA = 7
READABLE_SCHEMAS = (3, 4, 5, 6, SCHEMA)
STATE_FILE_NAME = 'garage_state.json'

# ``repair`` is (outstanding cost, remaining health): a vehicle a battle left
# damaged has to come back damaged, or a restart would be a free repair.
_VEHICLE_INT_KEYS = (
    'eqs', 'eqsLayout', 'shells', 'shellsLayoutIdx', 'repair')
_CUSTOMIZATION_SEASONS = (1, 2, 4, 8, 15)
MAX_BATTLE_RECEIPTS = 512
# How many individual vehicles a restore may drop before it stops trying.  A
# handful of refused records is a stale save against a changed catalogue; a
# long run of them means the file itself is wrong, and the ledger-only restore
# below is the honest answer.
MAX_CONTAINED_VEHICLES = 8


# The relational validator owns this type. It is raised from both the native
# descriptor checks in ``bootstrap`` and the snapshot checks in ``data``, and
# what a restore acts on is the ``vehicle_key`` it carries rather than the
# class: a validator loaded through a second module instance is a different
# class object with the same contract.
VehicleRestoreError = data.VehicleRestoreError


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
    # The recycle bin outlives a restart in retail, and the window it prices
    # is measured from the dismissal, so the timestamp is saved with the
    # descriptor.  Inventory ids are not: the restore hands out fresh ones.
    recycled = []
    for compact_descr, dismissed_at in (
            snapshot.get('recycleBinTankmen') or {}).values():
        encoded = _encode_bytes(compact_descr)
        if encoded is not None:
            recycled.append([encoded, _int_value(dismissed_at)])
    return {
        'wallet': dict(
            (name, max(0, int(wallet.get(name, 0) or 0)))
            for name in ('credits', 'gold', 'freeXP')),
        'vehicleXP': vehicle_xp,
        'unlocks': sorted(int(value) for value in (unlocks or ())),
        'slots': max(0, int(snapshot.get('accountSlots', 0) or 0)),
        'berths': max(0, int(snapshot.get('accountBerths', 0) or 0)),
        'barracks': sorted(barracks),
        'recycleBin': sorted(recycled),
    }


def _contained(refused, name, operation, garage_error):
    """Run one settlement step so a refusal costs that step and nothing else.

    A battle is worth what it is worth.  Every step of the settlement below
    the award touches one vehicle's own state -- its crew, its repair bill,
    its rounds, its consumables -- and any of them can refuse for a reason
    the player cannot see: a fitting this client will not rebuild, a crew
    descriptor it rejects, a vehicle the garage no longer holds.  None of
    that may cost the player the credits and experience the battle earned,
    so each step is attempted on its own and its refusal recorded by name.
    Every step raises before it mutates the staged snapshot, so a refusal
    leaves nothing half applied.
    """
    try:
        return operation()
    except garage_error as error:
        refused.append('%s (%s)' % (name, error))
        return None


def _settle_automatically(state, vehicle_id, auto_settings, garage_error):
    """Attempt each enabled service independently and record actual debits."""
    costs = economy.service_costs(None)
    if not auto_settings:
        return costs
    repair_flag, load_flag, equip_flag = auto_settings
    for record in state.snapshot().get('vehicles') or ():
        if int(record.get('id', 0)) != int(vehicle_id):
            continue
        try:
            settings = int(record.get('settings', 0) or 0)
        except (TypeError, ValueError):
            return costs
        shell_layout = (record.get('shellsLayout') or {}).get(
            tuple(record.get('shellsLayoutIdx') or ()))
        equipment_layout = list(record.get('eqsLayout') or ())
        operations = (
            (repair_flag, 'repair', lambda: state.repair_vehicle(vehicle_id)),
            (load_flag, 'ammo', lambda: state.equip_shells(
                vehicle_id, list(shell_layout)) if shell_layout else None),
            (equip_flag, 'equipment', lambda: state.equip_equipments(
                vehicle_id, equipment_layout) if any(equipment_layout) else None),
        )
        for flag, name, apply in operations:
            if not settings & int(flag or 0):
                continue
            before = state._balances()
            try:
                apply()
            except garage_error:
                continue
            after = state._balances()
            for currency in ('credits', 'gold'):
                key = name + '_' + currency
                if key in costs:
                    costs[key] = max(0, before[currency] - after[currency])
        return costs
    return costs


def _floor_account_stock(snapshot):
    """Own at least what the garage already holds.

    Every mounted module and carried supply contributes a physical copy.
    Native restore validation rebuilds module rows from the saved descriptor
    before this migration, so stock fittings cannot manufacture spare parts.
    """
    published = snapshot.setdefault('inventoryItems', {})
    for item_type in STOCKED_ITEM_TYPES:
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
            # A sold vehicle is absent from the owned XP seed but remains
            # in this exact client's shop catalogue. Keep its earned XP.
            if key in published or key in staged.get('shopItemPrices', {}):
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
    recycled = ledger.get('recycleBin')
    if isinstance(recycled, (list, tuple)):
        staged['recycleBinTankmen'] = _restored_recycle_bin(staged, recycled)
    return True


def _used_tankman_ids(staged):
    """Return every crew inventory id the restored garage already holds."""
    used = set()
    for record in _records(staged):
        for tankman_id in (record.get('tankmen') or ()):
            used.add(_int_value(tankman_id))
    used.update(
        _int_value(tankman_id)
        for tankman_id in (staged.get('barracksTankmen') or ()))
    return used


def _restored_barracks(staged, encoded_descriptors):
    """Give every saved barracks crew member an id no vehicle is using."""
    used = _used_tankman_ids(staged)
    next_id = (max(used) + 1) if used else 100001
    restored = {}
    for encoded in encoded_descriptors:
        decoded = _decode_bytes(encoded)
        if not decoded:
            continue
        restored[next_id] = decoded
        next_id += 1
    return restored


def _restored_recycle_bin(staged, rows):
    """Restore who was dismissed, with ids nothing else in the garage uses.

    The barracks is restored first, so its fresh ids are already taken here.
    """
    used = _used_tankman_ids(staged)
    next_id = (max(used) + 1) if used else 100001
    restored = {}
    for row in rows:
        try:
            encoded, dismissed_at = row
        except (TypeError, ValueError):
            continue
        decoded = _decode_bytes(encoded)
        if not decoded:
            continue
        restored[next_id] = (decoded, _int_value(dismissed_at))
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
        # Inventory ids are rebuilt on startup. Preserve actual crew awards
        # for a same-session results retry, never as durable crew identity.
        self._session_crew_xp = {}
        # What the file on disk holds, so a write that would destroy far more
        # than the command behind it can be recognised before it replaces it.
        self._saved_vehicle_keys = None
        self._saved_unlock_count = 0
        self._rotated = False
        self._shrink_kept = False
        # Set when a restore could not publish the save whole: this session
        # plays a garage the file does not describe, so nothing but a real
        # player change may rewrite it.
        self._restore_degraded = False
        # Set when no saved vehicle reached the garage at all.  Then every
        # fitting and crew member in the file is one this session never had,
        # so this session may not write the file back at any price.
        self._vehicles_unrestored = False
        self._refusals_logged = set()

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
        if not self._write_state(self._payload(snapshot), snapshot):
            return False
        self._dirty = False
        return True

    def _write_state(self, payload, snapshot=None):
        """Replace the saved garage, keeping a copy of what it overwrites.

        Every write here is the whole file, so a payload built from the wrong
        snapshot replaces a career rather than editing it.  Three things stand
        between that and a lost career: one rotated copy per session, a
        quarantined copy the first time a write shrinks the save, and a
        refusal for the shapes no accepted command can produce.
        """
        if self._path is None:
            return False
        if self._vehicles_unrestored:
            # Not a judgement about this payload: no saved vehicle reached
            # this session at all, so any payload it builds describes a
            # garage this file never held.  Whether the vehicle count or the
            # research happens to look smaller is beside the point.
            self._refuse('no saved vehicle could be published this session, '
                         'so this garage is not what the save holds')
            return False
        removed, lost_unlocks = self._destroyed_by(payload)
        if removed or lost_unlocks:
            reason = self._refusal(payload, removed, lost_unlocks, snapshot)
            if reason is not None:
                self._refuse(reason)
                return False
            if not self._shrink_kept:
                kept = port_config.quarantine_state_file(
                    self._path, port_config.QUARANTINE_SHRUNK)
                self._shrink_kept = True
                dropped = []
                if removed:
                    dropped.append('%d vehicle(s)' % len(removed))
                if lost_unlocks:
                    dropped.append(
                        '%d researched item(s) this client no longer offers'
                        % lost_unlocks)
                _log('this save write drops %s; the previous file was kept '
                     'as %s' % (
                         ' and '.join(dropped),
                         kept if kept is not None else '(no copy)'))
        if not self._rotated:
            port_config.rotate_state_backup(self._path)
            self._rotated = True
        try:
            port_config.write_json(self._path, payload)
        except (IOError, OSError) as error:
            _log('the garage state could not be saved: %s' % error)
            return False
        self._remember_saved(payload)
        return True

    def _refuse(self, reason):
        """Report one refused write, once per reason per session.

        Fittings happen at click speed and a refused save stays refused, so
        repeating the same line for every click would bury it.
        """
        if reason in self._refusals_logged:
            return
        self._refusals_logged.add(reason)
        _log('the garage state was NOT saved: %s. The save on disk is kept as '
             'it is, so nothing earned before this session is lost. Restart '
             'the client to load it again, or restore a backup from the '
             'Launcher' % reason)

    def _destroyed_by(self, payload):
        """Return what one payload would remove from the file on disk.

        Both counts come from what the last read or write left in memory, so
        an ordinary write costs no file access and no second copy of the
        unlock set.
        """
        if self._saved_vehicle_keys is None:
            return (frozenset(), 0)
        removed = frozenset(self._saved_vehicle_keys) - frozenset(
            payload.get('vehicles') or ())
        ledger = payload.get('ledger')
        unlocks = (ledger.get('unlocks') if isinstance(ledger, dict) else ())
        lost = max(0, self._saved_unlock_count - len(unlocks or ()))
        return (removed, lost)

    def _refusal(self, payload, removed, lost_unlocks, snapshot):
        """Return why a write must not happen, or None to let it through.

        Selling a vehicle and spending credits legitimately shrink a save, and
        so does playing against a vehicle catalogue that no longer offers
        something the save named, so a shrinking write is kept rather than
        refused.  Two shapes have no accepted command behind them:

        - a garage that lost every vehicle at once, which is what an empty or
          not-yet-populated snapshot writes; and
        - research this client still sells that the payload no longer holds.
          Nothing in this economy ever revokes research, so a payload missing
          an item the current catalogue still offers was not built from the
          saved account at all.  That is the write that turns a career into a
          new account, and it is the one worth refusing.
        """
        if removed and not payload.get('vehicles'):
            return ('a payload with no vehicles would replace %d saved '
                    'vehicle(s)' % len(removed))
        if not lost_unlocks:
            return None
        missing = self._missing_unlocks(payload)
        if not missing:
            return None
        offered = set(int(compact_descr)
                      for compact_descr in ((snapshot or {}).get(
                          'shopItemPrices') or ()))
        still_offered = missing & offered if offered else missing
        if still_offered:
            return ('%d researched item(s) this client still offers are '
                    'missing from the payload, including %d' % (
                        len(still_offered), min(still_offered)))
        return None

    def _missing_unlocks(self, payload):
        """Return the saved research a payload does not carry.

        The saved set is re-read here rather than held: it is the whole
        catalogue in a fully unlocked save, and this only runs on the write
        that already looks wrong.
        """
        stored = self._read_file(self._path)
        ledger = (stored or {}).get('ledger')
        saved = (ledger.get('unlocks') if isinstance(ledger, dict) else None)
        if not isinstance(saved, (list, tuple)):
            return set()
        payload_ledger = payload.get('ledger')
        published = (payload_ledger.get('unlocks')
                     if isinstance(payload_ledger, dict) else ())
        return set(_int_value(value) for value in saved) - set(
            _int_value(value) for value in (published or ()))

    def _remember_saved(self, payload):
        self._saved_vehicle_keys = frozenset(payload.get('vehicles') or ())
        ledger = payload.get('ledger')
        unlocks = (ledger.get('unlocks') if isinstance(ledger, dict) else None)
        self._saved_unlock_count = len(unlocks or ())

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
            if isinstance(type_name, string_types) and type_name:
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
                if item_type not in STOCKED_ITEM_TYPES:
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
            if isinstance(name, string_types) and name:
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
                             shells_fired=None, equipment_used=None,
                             auto_settings=None):
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
                result['vehicle_id'] = next((
                    _int_value(record.get('id')) for record in _records(snapshot)
                    if _int_value(record.get('vehicleTypeCompactDescr')) ==
                    _int_value(vehicle_type_compact_descr)), 0)
                result['xp_by_tankman'] = dict(
                    self._session_crew_xp.get(receipt_id, {}))
                result.setdefault('refused', [])
                result['applied'] = False
                return result

        from gui.mods.offline_lan_0922.account_rpc.garage import (
            GarageError, GarageState)
        from gui.mods.offline_lan_0922.account_rpc import economy
        staged = copy.deepcopy(snapshot)
        state = GarageState(staged, tankmen_module=tankmen_module,
                            vehicles_module=vehicles_module)
        # The receipt says what the battle did; what it is worth is the
        # account's business, so the two multipliers are applied here, once,
        # and everything downstream banks and shows the same numbers.
        experience_percent = economy.earnings_percent(
            snapshot.get('earningsPercent'))
        credits_percent = experience_percent
        if (vehicles_module is not None and economy.is_premium_vehicle(
                vehicles_module, vehicle_type_compact_descr)):
            credits_percent = (
                credits_percent * economy.PREMIUM_VEHICLE_CREDITS_PERCENT
                // 100)
        awarded = economy.scale_rewards(
            rewards, credits_percent=credits_percent,
            experience_percent=experience_percent)
        battle_xp = awarded.get('xp', battle_xp) if rewards else (
            max(0, int(battle_xp or 0)) * experience_percent // 100)
        # What the battle earned does not depend on the vehicle it was
        # fought in beyond the multipliers above, so no per-vehicle step may
        # take the award away.  Each of them is contained; the award is not.
        refused = []
        result = {
            'accelerated': False,
            'vehicle_id': next((
                _int_value(record.get('id')) for record in _records(staged)
                if _int_value(record.get('vehicleTypeCompactDescr')) ==
                _int_value(vehicle_type_compact_descr)), 0),
            'weakest_tankman_id': 0,
            'xp_by_tankman': {},
        }
        crew = _contained(
            refused, 'crew experience',
            lambda: state.award_battle_crew_xp(
                vehicle_type_compact_descr, battle_xp, xp_to_tankman_flag),
            GarageError)
        if crew is not None:
            result.update(crew)
        # Crew training owns crewXpFactor; bank the separate vehicle XP
        # bonus exactly once without multiplying the crew award again.
        premium_factor = economy.premium_vehicle_xp_factor_100(
            vehicles_module, vehicle_type_compact_descr)
        for name in ('xp', 'free_xp'):
            awarded[name] += economy.premium_xp_bonus(
                awarded[name], premium_factor)
        if health is not None:
            result['repair'] = _contained(
                refused, 'repair bill',
                lambda: state.settle_battle_damage(
                    vehicle_type_compact_descr, health), GarageError)
        if shells_fired:
            result['shells_spent'] = _contained(
                refused, 'rounds fired',
                lambda: state.settle_battle_ammunition(
                    vehicle_type_compact_descr, shells_fired), GarageError)
        if equipment_used:
            result['consumables_spent'] = _contained(
                refused, 'consumables used',
                lambda: state.settle_battle_consumables(
                    vehicle_type_compact_descr, equipment_used), GarageError)
        if rewards is not None:
            # The award needs no vehicle record: the wallet and the vehicle's
            # experience are both keyed by the type this battle was fought
            # in.  It shares the crew award's one JSON replacement, so a
            # retried receipt can never bank one without the other.
            result['earnings'] = state.award_battle_earnings(
                vehicle_type_compact_descr, awarded,
                accelerated=bool(result['accelerated']))
            # What was actually banked, so the battle-results screen and the
            # lifetime counters report the same amounts as the wallet.
            result['awarded'] = dict(
                (name, int(awarded.get(name, 0) or 0))
                for name in ('credits', 'xp', 'free_xp'))
        if refused:
            _log('battle settlement refused %s for receipt %s; the award was '
                 'banked anyway' % (', '.join(refused), receipt_id))
        result['refused'] = list(refused)
        result['service_costs'] = _settle_automatically(
            state, int(result['vehicle_id']), auto_settings, GarageError)
        # Every other field of this result is plain JSON, and the store hands
        # it straight to a caller that may well write it down.
        result['touched_items'] = dict(
            (int(item_type), sorted(int(value) for value in items))
            for item_type, items in state.touched_items().items())
        staged = state.snapshot()
        marker = {
            'receipt_id': receipt_id,
            'accelerated': bool(result['accelerated']),
            'vehicle_id': int(result['vehicle_id']),
        }
        if 'awarded' in result:
            marker['awarded'] = dict(result['awarded'])
        marker['touched_items'] = copy.deepcopy(result['touched_items'])
        marker['service_costs'] = dict(result['service_costs'])
        next_receipts = (list(self._battle_receipts) + [marker])[
            -MAX_BATTLE_RECEIPTS:]
        if self._path is not None and not self._write_state(
                self._payload(staged, next_receipts), staged):
            # The award is not banked unless its marker reaches disk with it,
            # or a retried receipt would award it twice.  The server keeps an
            # unacknowledged receipt, so leaving it pending is recoverable and
            # claiming it silently is not.
            raise RuntimeError(
                'the garage state could not be saved, so this battle receipt '
                'is left pending')
        snapshot.clear()
        snapshot.update(staged)
        self._battle_receipts = next_receipts
        active_receipts = set(row['receipt_id'] for row in next_receipts)
        self._session_crew_xp = dict(
            (key, value) for key, value in self._session_crew_xp.items()
            if key in active_receipts)
        self._session_crew_xp[receipt_id] = dict(result['xp_by_tankman'])
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

        A refusal is contained as narrowly as the validator can attribute it.
        One vehicle this client cannot publish costs that vehicle's fittings
        and crew, not the account's research, balances and every other tank:
        the whole save used to be discarded over any single bad field, and the
        first accepted change afterwards overwrote it with the stock garage.
        """
        stored = self._read()
        self._session_crew_xp = {}
        if stored is None:
            if self._path is not None and any(os.path.isfile(path) for path in
                    (self._path, self._path + '.bak')):
                self._restore_degraded = True
                self._vehicles_unrestored = True
                for path in (self._path, self._path + '.bak'):
                    port_config.quarantine_state_file(
                        path, port_config.QUARANTINE_REJECTED)
            self._receipts_loaded = True
            return False
        skipped = set()
        while True:
            staged, applied = self._staged(snapshot, stored, skipped)
            try:
                self._validate_staged(staged, validator)
            except Exception as error:
                key = getattr(error, 'vehicle_key', None)
                if (key is None or key in skipped or
                        len(skipped) >= MAX_CONTAINED_VEHICLES):
                    _log('the saved garage state is inconsistent (%s)'
                         % error)
                    break
                skipped.add(key)
                _log('the saved garage for vehicle %s is inconsistent; that '
                     'one vehicle is restored as stock (%s)' % (key, error))
                continue
            return self._commit(snapshot, stored, staged, applied, skipped)

        # Nothing about the vehicles could be published.  The ledger is plain
        # integers and crew the barracks owns, so it is restored on its own
        # rather than lost with them: research and balances are what a career
        # cannot rebuild by playing.
        staged, applied = self._staged(
            snapshot, stored, skipped=None, ledger_only=True)
        try:
            self._validate_staged(staged, validator)
        except Exception as error:
            kept = port_config.quarantine_state_file(
                self._path, port_config.QUARANTINE_REJECTED)
            _log('the saved garage state could not be published at all; using '
                 'the stock garage (%s). The refused file was kept as %s'
                 % (error, kept if kept is not None else '(no copy)'))
            # Do not trust receipt markers whose matching crew descriptors
            # could not be restored.  A pending server receipt may now safely
            # rebuild the award on the fresh bootstrap garage.
            self._battle_receipts = []
            self._receipts_loaded = True
            self._restore_degraded = True
            self._vehicles_unrestored = True
            return False
        return self._commit(
            snapshot, stored, staged, applied, skipped, ledger_only=True)

    def _commit(self, snapshot, stored, staged, applied, skipped,
                ledger_only=False):
        """Publish one restored garage and report how complete it is."""
        snapshot.clear()
        snapshot.update(staged)
        self._battle_receipts = self._validated_battle_receipts(
            stored.get('battleCrewReceipts'))
        self._receipts_loaded = True
        if ledger_only or skipped:
            self._restore_degraded = True
            self._vehicles_unrestored = bool(ledger_only)
            kept = port_config.quarantine_state_file(
                self._path, port_config.QUARANTINE_REJECTED)
            _log('the saved garage was restored without %s; the file as it '
                 'was saved was kept as %s' % (
                     'any vehicle fitting or crew' if ledger_only else
                     '%d vehicle(s)' % len(skipped),
                     kept if kept is not None else '(no copy)'))
        if applied:
            _log('restored the saved garage for %d vehicle(s)' % applied)
        return True

    def restore_degraded(self):
        """Report whether this session plays a garage the save does not hold.

        A caller that writes the save for its own convenience rather than for
        something the player did must not do it while this is true: the file
        still holds fittings and crew this client could not publish, and a
        later build may well be able to.
        """
        return self._restore_degraded

    def _validate_staged(self, staged, validator):
        if validator is not None:
            validator(staged)
        _floor_account_stock(staged)
        data._validate_selected_vehicle(staged)

    def _staged(self, snapshot, stored, skipped, ledger_only=False):
        """Build one candidate restore on a detached copy of the snapshot.

        ``skipped`` names the saved vehicles to leave at their stock build;
        ``ledger_only`` leaves every vehicle stock and restores just the
        account.  Each attempt starts from the bootstrap snapshot again so a
        refused overlay cannot leave half of itself behind.
        """
        staged = copy.deepcopy(snapshot)
        vehicles = stored.get('vehicles')
        if not isinstance(vehicles, dict) or ledger_only:
            vehicles = {}
        applied = 0
        for record in _records(staged):
            key = record.get('vehicleTypeCompactDescr')
            if key is None:
                continue
            key = str(int(key))
            if skipped and key in skipped:
                continue
            saved = vehicles.get(key)
            if isinstance(saved, dict) and self._apply_vehicle(record, saved):
                applied += 1

        owned = stored.get('owned')
        if isinstance(owned, dict):
            published = staged.setdefault('inventoryItems', {})
            prices = staged.get('shopItemPrices') or {}
            for item_type in STOCKED_ITEM_TYPES:
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
        _apply_ledger(staged, stored)
        return (staged, applied)

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
            if not receipt_id:
                continue
            row = {
                'receipt_id': receipt_id,
                'accelerated': bool(raw.get('accelerated', False)),
                # The receipt id is what makes this marker idempotent.  A
                # settlement whose per-vehicle steps all refused still banked
                # the award and still names no vehicle, so dropping the row
                # for that would pay the same battle twice.
                'vehicle_id': max(0, vehicle_id),
            }
            awarded = raw.get('awarded')
            if isinstance(awarded, dict):
                row['awarded'] = dict(
                    (name, max(0, _int_value(awarded.get(name))))
                    for name in ('credits', 'xp', 'free_xp'))
            row['service_costs'] = economy.service_costs(raw.get('service_costs'))
            touched = raw.get('touched_items')
            if isinstance(touched, dict):
                row['touched_items'] = dict(
                    (int(item_type), _int_list(items))
                    for item_type, items in touched.items()
                    if _int_value(item_type) in STOCKED_ITEM_TYPES and
                    _int_list(items) is not None)
            rows.append(row)
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

    @staticmethod
    def _read_file(path):
        """Parse one state file, or return None with the reason logged."""
        if path is None or not os.path.isfile(path):
            return None
        try:
            import json
            with open(path, 'rb') as stream:
                value = json.load(stream)
        except (IOError, OSError, ValueError) as error:
            _log('the saved garage state in %s is unreadable (%s)'
                 % (path, error))
            return None
        if not isinstance(value, dict):
            _log('the saved garage state in %s has an unexpected shape'
                 % path)
            return None
        return value

    def _read(self):
        if self._path is None:
            return None
        for path in (self._path, self._path + '.bak'):
            value = self._read_file(path)
            if value is None:
                continue
            if value.get('schema') not in READABLE_SCHEMAS:
                _log('the saved garage state uses schema %r, not one of %r; '
                     'using the stock garage' % (
                         value.get('schema'), READABLE_SCHEMAS))
                return None
            self._remember_read(value)
            return value
        return None

    def _remember_read(self, stored):
        """Record what the file holds, before this session can replace it."""
        vehicles = stored.get('vehicles')
        self._saved_vehicle_keys = frozenset(
            vehicles if isinstance(vehicles, dict) else ())
        ledger = stored.get('ledger')
        unlocks = (ledger.get('unlocks') if isinstance(ledger, dict) else None)
        self._saved_unlock_count = len(
            unlocks if isinstance(unlocks, (list, tuple)) else ())


def _records(snapshot):
    if not isinstance(snapshot, dict):
        return []
    records = snapshot.get('vehicles')
    if isinstance(records, (list, tuple)) and records:
        return [record for record in records if isinstance(record, dict)]
    return [snapshot] if snapshot.get('compDescr') else []
