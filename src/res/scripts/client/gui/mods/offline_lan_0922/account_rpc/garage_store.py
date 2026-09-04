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
# crew XP idempotent.  Schema 3 remains readable and upgrades on the next save.
SCHEMA = 4
READABLE_SCHEMAS = (3, SCHEMA)
STATE_PATH = os.path.join(
    port_config.USER_DATA_DIR, 'garage_state.json')
LEGACY_STATE_PATH = os.path.join(
    port_config.LEGACY_USER_DATA_DIR, 'garage_state.json')

_VEHICLE_INT_KEYS = ('eqs', 'eqsLayout', 'shells', 'shellsLayoutIdx')
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

    def __init__(self, path=STATE_PATH):
        self._path = (port_config.migrate_legacy_user_file(
            path, LEGACY_STATE_PATH) if path == STATE_PATH else path)
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
            try:
                stored['settings'] = int(record.get('settings', 0) or 0)
            except (TypeError, ValueError):
                stored['settings'] = 0
            crew = {}
            tankmen = record.get('tankmen')
            order = list(record.get('crew') or ())
            if isinstance(tankmen, dict):
                for slot, tankman_id in enumerate(order):
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
                counts = _int_map(items)
                if counts:
                    owned[str(item_type)] = dict(
                        (str(compact_descr), count)
                        for compact_descr, count in counts.items())
        if battle_receipts is None:
            battle_receipts = self._battle_receipts
        return {
            'schema': SCHEMA, 'vehicles': vehicles, 'owned': owned,
            'battleCrewReceipts': list(battle_receipts)[
                -MAX_BATTLE_RECEIPTS:],
        }

    def apply_battle_crew_xp(self, snapshot, receipt_id,
                             vehicle_type_compact_descr, battle_xp,
                             xp_to_tankman_flag, tankmen_module=None):
        """Apply and persist one crew award exactly once.

        The compact crew descriptors and their receipt marker share one JSON
        replacement.  A receipt retried after a disconnect therefore either
        applies the whole award or observes the durable marker; it can never
        add the XP twice.
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
        state = GarageState(staged, tankmen_module=tankmen_module)
        result = state.award_battle_crew_xp(
            vehicle_type_compact_descr, battle_xp, xp_to_tankman_flag)
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
            for raw_type, items in owned.items():
                try:
                    item_type = int(raw_type)
                except (TypeError, ValueError):
                    continue
                if item_type not in _ARTEFACT_ITEM_TYPES:
                    continue
                counts = _int_map(items)
                if not counts:
                    continue
                target = published.setdefault(item_type, {})
                for compact_descr, count in counts.items():
                    # A saved file written before the current catalogue can
                    # still name an item this client no longer offers.
                    if compact_descr not in target:
                        continue
                    target[compact_descr] = max(
                        int(target[compact_descr]), int(count))

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
                decoded = _decode_bytes(encoded)
                if decoded:
                    tankmen[order[slot]] = decoded
                    changed = True
        mirror_shells_layout(record)
        # Mounted shells must stay consistent with the shell inventory that
        # data._validate_selected_vehicle cross-checks.
        shells = _int_list(record.get('shells'))
        if shells is not None and not len(shells) % 2:
            pairs = {}
            for index in range(0, len(shells), 2):
                pairs[shells[index]] = shells[index + 1]
            record.setdefault('inventoryItems', {})[10] = pairs
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
