"""Reversible campaign hull withdrawal without discarding fitted property.

The enclosing campaign transaction owns reward eligibility and economic
receipts. These helpers move only the hull and its installed modules. Crew,
optional devices, consumables and ammunition return to the account; restoring
the hull never manufactures another crew or another load of ammunition.
"""
import copy

from gui.mods.offline_lan_0922.account_rpc.garage import (
    GarageError, GOLD_EXCHANGE_RATE)
from gui.mods.offline_lan_0922 import price_catalogue

SOURCE_FIELD = 'personalMissionVehicleSource'
PARKED_PREFIX = 'parkedVehicle:'


def full_price_credits(state, compact_descr):
    """Read the original client price, before the offline bond-shop override."""
    try:
        name = str(state._vehicles_module().getVehicleType(int(compact_descr)).name)
        nation, vehicle = name.split(':', 1)
        price = price_catalogue.vehicle_price(nation, vehicle)
        if price is None or (len(price) > price_catalogue.CRYSTAL and
                             price[price_catalogue.CRYSTAL]):
            raise ValueError('the original silver or gold price is unavailable')
        return int(price[price_catalogue.CREDITS]) + int(price[price_catalogue.GOLD]) * GOLD_EXCHANGE_RATE
    except Exception as error:
        raise GarageError('PERSONAL_MISSION_VEHICLE_PRICE_UNAVAILABLE: %s' % error)


def _owned(state, name):
    return next((record for record in state._records()
                 if record.get('vehicleTypeName') == name), None)


def legacy_effect(state, name):
    """Legacy claims cannot prove whether a tank or compensation was paid.

    A matching owned hull is not evidence: it may have been purchased before
    the old claim paid silver instead. Only a validated acquisition source
    can recover a missing receipt; otherwise the whole reset must refuse.
    """
    record = _owned(state, name)
    source = record.get(SOURCE_FIELD) if record is not None else None
    try:
        prefix, serial = source.rsplit(':', 1)
        serial = int(serial)
        maximum = int((state.snapshot().get('personalMissionRewardJournal') or {}).get(
            'vehicleSourceCounter', 0))
        valid = prefix == name and 0 < serial <= maximum
    except (AttributeError, TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise GarageError('PERSONAL_MISSION_RESET_VEHICLE_SOURCE_UNAVAILABLE: ' + name)
    return {'kind': 'vehicle', 'vehicle': name,
            'vehicle_type': int(record['vehicleTypeCompactDescr']),
            'origin': 'mission', 'source': source}


def _source(state, name):
    journal = state.snapshot().setdefault('personalMissionRewardJournal', {})
    serial = int(journal.get('vehicleSourceCounter', 0)) + 1
    journal['vehicleSourceCounter'] = serial
    return '%s:%d' % (name, serial)


def _module_counts(record):
    return dict((int(kind), dict((int(cd), int(count)) for cd, count in items.items()))
                for kind, items in record.get('inventoryItems', {}).items()
                if 2 <= int(kind) <= 7)


def _restore_module_stock(state, record):
    for kind, items in _module_counts(record).items():
        for cd, count in items.items():
            owned = state.snapshot().get('inventoryItems', {}).get(kind, {})
            state._set_owned(cd, kind, int(owned.get(cd, 0)) + count)


def _parked_record(state, entry):
    """Rebind session IDs while using the normal saved-descriptor overlay."""
    from gui.mods.offline_lan_0922 import vehicle_records
    from gui.mods.offline_lan_0922.account_rpc.garage_store import (
        GarageStore, _decode_bytes)
    saved = entry['record']
    compact_descr = _decode_bytes(saved.get('compDescr'))
    if not compact_descr:
        raise GarageError('PERSONAL_MISSION_PARKED_VEHICLE_INVALID')
    descriptor = state._vehicles_module().VehicleDescr(compactDescr=compact_descr)
    modules = vehicle_records.mounted_module_items(descriptor)
    record = {'id': state._next_inventory_id(), 'compDescr': compact_descr,
              'vehicleTypeCompactDescr': int(entry['vehicle_type']),
              'vehicleTypeName': entry['vehicle'],
              'crew': [None] * len(descriptor.type.crewRoles), 'tankmen': {},
              'repair': (0, descriptor.maxHealth), 'lock': (0, 0),
              'eqs': [0, 0, 0], 'eqsLayout': [0, 0, 0],
              'shells': list(saved.get('shells') or ()),
              'shellsLayoutIdx': (descriptor.turret.compactDescr, descriptor.gun.compactDescr),
              'shellsLayout': {}, 'inventoryItems': modules}
    GarageStore(path=None)._apply_vehicle(record, saved)
    return record


def grant(state, name, create):
    """Return actual payout metadata; the caller records it exactly once."""
    with state._transaction():
        snapshot = state.snapshot()
        journal = snapshot.setdefault('personalMissionRewardJournal', {})
        key = PARKED_PREFIX + name
        parked = journal.get(key)
        existing = _owned(state, name)
        if existing is not None:
            compact_descr = int(existing['vehicleTypeCompactDescr'])
            credits = full_price_credits(state, compact_descr)
            state._wallet()['credits'] += credits
            if parked is not None:
                # The user acquired another copy while this hull was parked.
                # Keep its detached modules as depot property when converting
                # the held hull to the requested full-price compensation.
                _restore_module_stock(state, _parked_record(state, parked))
                journal.pop(key, None)
            state.revision += 1
            return {'kind': 'compensation', 'vehicle': name,
                    'vehicle_type': compact_descr, 'credits': credits,
                    'origin': 'mission'}
        restored = parked is not None
        if restored:
            record = _parked_record(state, parked)
            records = state._records()
            slots = int(snapshot.get('accountSlots', 0))
            if slots and len(records) >= slots:
                raise GarageError('PERSONAL_MISSION_RESTORE_NO_GARAGE_SLOT')
            _restore_module_stock(state, record)
            records.append(record)
            snapshot['vehicles'] = records
            compact_descr = int(record['vehicleTypeCompactDescr'])
            snapshot.setdefault('vehicleTypeCompactDescrs', set()).add(compact_descr)
            journal.pop(key, None)
        else:
            compact_descr = int(create())
            record = _owned(state, name)
            if record is None or int(record['vehicleTypeCompactDescr']) != compact_descr:
                raise GarageError('PERSONAL_MISSION_VEHICLE_GRANT_UNAVAILABLE')
        source = _source(state, name)
        record[SOURCE_FIELD] = source
        state._touched.add(int(record['id']))
        state._touch_item_changes({}, record.get('inventoryItems', {}))
        state.revision += 1
        return {'kind': 'vehicle', 'vehicle': name, 'vehicle_type': compact_descr,
                'origin': 'mission', 'source': source, 'restored': restored}


def revoke(state, effect):
    """Park a claimed hull or reject without changing any account property."""
    if effect.get('kind') == 'compensation':
        # The economic receipt owns the actual silver credit and its inverse.
        return copy.deepcopy(effect)
    if effect.get('kind') != 'vehicle':
        raise GarageError('INVALID_PERSONAL_MISSION_VEHICLE_RECEIPT')
    name = str(effect.get('vehicle') or '')
    with state._transaction():
        snapshot = state.snapshot()
        record = _owned(state, name)
        if record is None or int(record['vehicleTypeCompactDescr']) != int(effect['vehicle_type']):
            raise GarageError('PERSONAL_MISSION_RESET_VEHICLE_SOURCE_UNAVAILABLE: ' + name)
        if not effect.get('source') or record.get(SOURCE_FIELD) != effect['source']:
            raise GarageError('PERSONAL_MISSION_RESET_VEHICLE_SOURCE_CHANGED: ' + name)
        remaining = [row for row in state._records() if row is not record]
        if not remaining:
            raise GarageError('PERSONAL_MISSION_RESET_LAST_VEHICLE')
        crew = dict(record.get('tankmen') or {})
        state._require_berths(len(crew))
        if set(crew) & set(snapshot.get('barracksTankmen') or {}):
            raise GarageError('PERSONAL_MISSION_RESET_DUPLICATE_CREW_SOURCE')
        parked = copy.deepcopy(record)
        descriptor = state._descriptor(record)
        for index, device in enumerate(descriptor.optionalDevices):
            if device is not None:
                descriptor.removeOptionalDevice(index)
        parked['compDescr'] = descriptor.makeCompactDescr()
        parked['crew'] = [None] * len(record['crew'])
        parked['tankmen'] = {}
        parked['eqs'] = [0] * len(record['eqs'])
        parked['eqsLayout'] = list(parked['eqs'])
        parked['shells'] = [value if index % 2 == 0 else 0
                            for index, value in enumerate(record['shells'])]
        parked['inventoryItems'][9] = {}
        parked['inventoryItems'][10] = dict(zip(parked['shells'][::2], parked['shells'][1::2]))
        parked['inventoryItems'][11] = {}
        # Only physical copies held by the hull leave live stock. Detached
        # optional devices, rounds and consumables were already counted there.
        for kind, items in _module_counts(parked).items():
            for cd, count in items.items():
                owned = int(snapshot.get('inventoryItems', {}).get(kind, {}).get(cd, 0))
                if owned - count < state._mounted(cd, kind, remaining):
                    raise GarageError('PERSONAL_MISSION_RESET_VEHICLE_MODULES_UNAVAILABLE')
                state._set_owned(cd, kind, owned - count)
        from gui.mods.offline_lan_0922.account_rpc.garage_store import GarageStore
        compact_descr = int(record['vehicleTypeCompactDescr'])
        saved = GarageStore(path=None)._payload({'vehicles': [parked]})['vehicles'][str(compact_descr)]
        journal = snapshot.setdefault('personalMissionRewardJournal', {})
        key = PARKED_PREFIX + name
        if key in journal:
            raise GarageError('PERSONAL_MISSION_DUPLICATE_PARKED_VEHICLE')
        journal[key] = {'vehicle': name, 'vehicle_type': compact_descr,
                        'record': saved, 'origin': effect.get('origin', 'mission')}
        for tankman_id, descriptor in crew.items():
            state._to_barracks(tankman_id, descriptor)
        snapshot['vehicles'] = remaining
        published = set(snapshot.get('vehicleTypeCompactDescrs') or ())
        published.discard(compact_descr)
        snapshot['vehicleTypeCompactDescrs'] = published
        if int(snapshot.get('id', 0)) == int(record['id']):
            # Legacy selected-vehicle fields must follow a remaining hull;
            # inventoryItems is the account-wide depot and stays independent.
            snapshot.update(dict((key, copy.deepcopy(value))
                                 for key, value in remaining[0].items()
                                 if key != 'inventoryItems'))
        state._touched.add(int(record['id']))
        state._touch_item_changes(record.get('inventoryItems', {}), {})
        state.revision += 1
        return {'kind': 'vehicle', 'vehicle': name, 'vehicle_type': compact_descr,
                'origin': effect.get('origin', 'mission'), 'withdrawn': True,
                'crew_returned': len(crew)}
