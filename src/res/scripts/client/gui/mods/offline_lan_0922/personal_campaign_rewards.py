"""Actual, reversible economic receipts for one personal-mission reward stage.

The caller owns claim markers and encloses grants and a complete reset in a
GarageState transaction. Orders, component tokens, badges, vehicles and source
identified crew have separate owners. Only XML-declared depot rewards enter
this ledger: equipment supplied with a reward vehicle belongs to its hull.
"""
import copy

from gui.mods.offline_lan_0922.account_rpc.garage import GarageError

WALLET_NAMES = ('credits', 'gold', 'freeXP', 'crystal')
DAY = 86400


def _children(node, name):
    return [row for tag, row in (node or {}).get('children', ()) if tag == name]


def _value(node, name=None, default=''):
    if name is not None:
        rows = _children(node, name)
        node = rows[0] if rows else None
    return node.get('value', default) if node is not None else default


def _count(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
    if value < 0:
        raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
    return value


def _customization_keys(state, bonus):
    sections = _children(bonus, 'customizations')
    if not sections:
        return []
    from items.components import c11n_constants
    result = []
    for section in sections:
        for row in _children(section, 'item'):
            if _value(row, 'custType') != 'camouflage':
                raise GarageError('UNSUPPORTED_PERSONAL_MISSION_CUSTOMIZATION')
            vehicles = state._vehicles_module()
            vehicle = vehicles.VehicleDescr(typeName=_value(row, 'boundVehicle'))
            nation, vehicle_id = vehicle.type.id
            bound = vehicles.makeIntCompactDescrByID('vehicle', nation, vehicle_id)
            result.append((c11n_constants.CustomizationType.CAMOUFLAGE,
                           int(_value(row, 'id')), int(bound),
                           _count(_value(row, 'value'))))
    return result


def _targets(state, bonus):
    """Resolve explicit assets before taking the narrow stage snapshot."""
    items = set()
    dossiers = {}
    fields = set()
    for name, row in (bonus or {}).get('children', ()):
        if name == 'item':
            compact_descr = int(_value(row))
            items.add((int(state._item_type(compact_descr)), compact_descr))
        elif name == 'dossier':
            identifier = _value(row, 'name')
            if not identifier.startswith('playerBadges:'):
                mode = 'add' if _value(row, 'type') == 'add' else 'set'
                if dossiers.get(identifier, mode) != mode:
                    mode = 'set'
                dossiers[identifier] = mode
        elif name in ('slots', 'berths', 'premium'):
            fields.add(name)
    return {'items': items, 'dossiers': dossiers, 'fields': fields,
            'customizations': _customization_keys(state, bonus)}


def _at(mapping, keys):
    for key in keys:
        mapping = (mapping or {}).get(key, {})
    return _count(mapping or 0)


def _capture(state, targets):
    snapshot = state.snapshot()
    return {
        'wallet': dict(state._balances()),
        'slots': _count(snapshot.get('accountSlots', 0)),
        'berths': _count(snapshot.get('accountBerths', 0)),
        'premium': _count(snapshot.get('premiumExpiryTime', 0)),
        'items': dict((key, _at(snapshot.get('inventoryItems'), key))
                      for key in targets['items']),
        'dossiers': dict((name, snapshot.get('personalMissionDossier', {}).get(name))
                         for name in targets['dossiers']),
        'customizations': dict((key[:3], _at(snapshot.get('customizationItems'), key[:3]))
                               for key in targets['customizations']),
    }


def _delta(after, before):
    result = int(after) - int(before)
    if result < 0:
        raise GarageError('PERSONAL_MISSION_REWARD_HAS_NEGATIVE_DELTA')
    return result


def notification_rows(effects):
    """Use the same actual quantities for award and withdrawal messages."""
    rows = []
    for effect in effects:
        kind = effect['kind']
        count = effect.get('count', 0)
        if kind == 'wallet':
            row = {'kind': effect['name'], 'count': count}
        elif kind == 'item':
            row = {'kind': kind, 'id': effect['compact_descr'], 'count': count}
        elif kind == 'customization':
            row = {'kind': kind, 'id': effect['item_id'],
                   'cust_type': effect['customization_type'],
                   'vehicle': effect['vehicle_type'], 'count': count}
        elif kind == 'premium':
            seconds = effect.get('seconds', effect['end'] - effect['start'])
            row = {'kind': kind, 'count': seconds / float(DAY), 'seconds': seconds}
        elif kind == 'dossier':
            row = {'kind': kind, 'id': effect['name'], 'count': count,
                   'mode': effect['mode']}
        else:
            row = {'kind': kind, 'count': count}
        rows.append(row)
    return rows


def record_grant(state, bonus, now, grant_callback):
    """Invoke a real grant and return its JSON receipt, without claiming it.

The zero-argument callback may return ``{'vehicles': [effect, ...]}``. Vehicle
effects are retained for the vehicle owner; economic reversal never uses them.
"""
    now = int(now)
    targets = _targets(state, bonus)
    before = _capture(state, targets)
    outcome = grant_callback()
    after = _capture(state, targets)
    effects = []
    # An already owned reward vehicle pays credits even without a credits XML
    # node. Wallet differences therefore include the entire isolated stage.
    for name in WALLET_NAMES:
        count = _delta(after['wallet'][name], before['wallet'][name])
        if count:
            effects.append({'kind': 'wallet', 'name': name, 'count': count})
    for field in ('slots', 'berths'):
        if field in targets['fields']:
            count = _delta(after[field], before[field])
            if count:
                effects.append({'kind': field, 'count': count})
    if 'premium' in targets['fields']:
        start = max(now, before['premium'])
        count = _delta(after['premium'], start)
        if count:
            effects.append({'kind': 'premium', 'start': start, 'end': after['premium']})
    for (item_type, compact_descr), old in sorted(before['items'].items()):
        count = _delta(after['items'][(item_type, compact_descr)], old)
        if count:
            effects.append({'kind': 'item', 'item_type': item_type,
                            'compact_descr': compact_descr, 'count': count})
    for name, mode in sorted(targets['dossiers'].items()):
        old, new = before['dossiers'][name], after['dossiers'][name]
        if old == new:
            continue
        if mode == 'add':
            effects.append({'kind': 'dossier', 'name': name, 'mode': mode,
                            'count': _delta(new or 0, old or 0)})
        else:
            effects.append({'kind': 'dossier', 'name': name, 'mode': mode,
                            'before': old, 'after': new, 'count': new or 0})
    for key, old in sorted(before['customizations'].items()):
        count = _delta(after['customizations'][key], old)
        if count:
            effects.append({'kind': 'customization', 'customization_type': key[0],
                            'item_id': key[1], 'vehicle_type': key[2], 'count': count})
    receipt = {'version': 1, 'bonus': copy.deepcopy(bonus), 'effects': effects,
               'rewards': notification_rows(effects)}
    if isinstance(outcome, dict) and outcome.get('vehicles'):
        receipt['vehicles'] = copy.deepcopy(outcome['vehicles'])
    return receipt


def legacy_receipt(state, bonus, now, granted_at=None):
    """Rebuild only fixed XML payouts supported by a historical paid marker.

Vehicle ownership/compensation is deliberately not inferred here. A past set
operation has no recoverable previous value. Active premium without its grant
time cannot be attributed to this stage or distinguished from paid premium.
"""
    targets = _targets(state, bonus)
    effects, grouped = [], {}
    for name, row in (bonus or {}).get('children', ()):
        if name in WALLET_NAMES or name in ('slots', 'berths'):
            grouped[(name,)] = grouped.get((name,), 0) + _count(_value(row))
        elif name == 'item':
            compact_descr = int(_value(row))
            key = ('item', int(state._item_type(compact_descr)), compact_descr)
            grouped[key] = grouped.get(key, 0) + _count(_value(row, 'count', '1'))
        elif name == 'premium':
            if int(state.snapshot().get('premiumExpiryTime', 0)) <= int(now):
                continue
            # Even a grant timestamp does not identify pre-existing premium's
            # expiry, which controls where this appended duration began.
            raise GarageError('PERSONAL_MISSION_RESET_PREMIUM_PROVENANCE_MISSING')
        elif name == 'dossier':
            identifier = _value(row, 'name')
            if identifier.startswith('playerBadges:'):
                continue
            if _value(row, 'type') != 'add' or _value(row, 'value') == 'timestamp':
                raise GarageError('PERSONAL_MISSION_RESET_DOSSIER_PROVENANCE_MISSING: ' + identifier)
            key = ('dossier', identifier)
            grouped[key] = grouped.get(key, 0) + _count(_value(row, 'value'))
    for key, count in sorted(grouped.items()):
        if not count:
            continue
        name = key[0]
        if name in WALLET_NAMES:
            effects.append({'kind': 'wallet', 'name': name, 'count': count})
        elif name == 'item':
            effects.append({'kind': 'item', 'item_type': key[1],
                            'compact_descr': key[2], 'count': count})
        elif name == 'dossier':
            effects.append({'kind': name, 'name': key[1], 'mode': 'add', 'count': count})
        else:
            effects.append({'kind': name, 'count': count})
    grouped = {}
    for row in targets['customizations']:
        key = row[:3]
        grouped[key] = grouped.get(key, 0) + row[3]
    for key, count in sorted(grouped.items()):
        if count:
            effects.append({'kind': 'customization', 'customization_type': key[0],
                            'item_id': key[1], 'vehicle_type': key[2], 'count': count})
    return {'version': 1, 'legacy': True, 'bonus': copy.deepcopy(bonus),
            'effects': effects, 'rewards': notification_rows(effects)}


def _premium_effects(value):
    if isinstance(value, dict):
        if value.get('kind') == 'premium' and 'start' in value and 'end' in value:
            yield value
        else:
            for row in value.values():
                for found in _premium_effects(row):
                    yield found
    elif isinstance(value, (tuple, list)):
        for row in value:
            for found in _premium_effects(row):
                yield found


def _remove_reward_camouflage(state, item_id, vehicle_type):
    """Remove the withdrawn camouflage from saved outfits, preserving others.

Use the same native parser/serializer as GarageState.apply_outfit. Unexpected
components fail the enclosing reset, rather than retaining an equipped reward
after removing its entitlement or replacing a player's whole outfit.
"""
    records = [record for record in state._records()
               if int(record.get('vehicleTypeCompactDescr', 0)) == vehicle_type
               and record.get('outfits')]
    if not records:
        return
    customizations = state._customizations_module()
    try:
        for record in records:
            for season, saved in list(record['outfits'].items()):
                if not isinstance(saved, (tuple, list)) or len(saved) != 2:
                    raise ValueError('invalid saved outfit')
                outfit = customizations.parseOutfitDescr(saved[0])
                rows = outfit.camouflages
                if not isinstance(rows, (tuple, list)):
                    raise ValueError('invalid camouflage components')
                retained = [row for row in rows if int(row.id) != item_id]
                if len(retained) == len(rows):
                    continue
                outfit.camouflages = retained
                canonical = outfit.makeCompDescr()
                checked = customizations.parseOutfitDescr(canonical)
                if any(int(row.id) == item_id for row in checked.camouflages):
                    raise ValueError('camouflage removal did not round trip')
                record['outfits'][season] = (canonical, saved[1])
                state._touched.add(int(record['id']))
    except Exception as error:
        raise GarageError('PERSONAL_MISSION_RESET_CUSTOMIZATION_OUTFIT_UNAVAILABLE: %s' % error)


def revoke(state, receipt, now):
    """Withdraw exact economic assets; return actual withdrawal message rows.

The caller must remove reward vehicles and crew first, making their slots and
berths free. Quantity rewards are withdrawn only up to the remaining stock.
Mounted items and occupied capacity stay with their current owners. Invalid
or ambiguous provenance still rolls back the enclosing reset and its claims.
"""
    if not isinstance(receipt, dict) or receipt.get('version') != 1:
        raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
    effects = copy.deepcopy(receipt.get('effects') or [])
    snapshot, now = state.snapshot(), int(now)
    # Validate against a staging state so duplicate resource rows cannot each
    # pass against the same balance. _transaction also restores touch sets.
    with state._transaction():
        actual = []
        for effect in effects:
            kind = effect.get('kind')
            count = _count(effect.get('count', 0))
            if kind == 'wallet':
                name = effect.get('name')
                if name not in WALLET_NAMES:
                    raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
                available = state._balances()[name]
                count = min(count, max(0, available))
                effect['count'] = count
                state._wallet()[name] = available - count
                if not count:
                    continue
            elif kind == 'item':
                item_type = _count(effect['item_type'])
                compact_descr = _count(effect['compact_descr'])
                available = _at(snapshot.get('inventoryItems'), (item_type, compact_descr))
                mounted = state._mounted(compact_descr, item_type, state._records())
                count = min(count, max(0, available - mounted))
                if not count:
                    continue
                effect['count'] = count
                state._set_owned(compact_descr, item_type, available - count)
            elif kind in ('slots', 'berths'):
                key = 'accountSlots' if kind == 'slots' else 'accountBerths'
                total = _count(snapshot.get(key, 0))
                used = len(state._records()) if kind == 'slots' else len(snapshot.get('barracksTankmen') or {})
                count = min(count, max(0, total - used))
                if not count:
                    continue
                effect['count'] = count
                snapshot[key] = total - count
            elif kind == 'premium':
                start, end = _count(effect['start']), _count(effect['end'])
                if end < start:
                    raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
                expiry = _count(snapshot.get('premiumExpiryTime', 0))
                seconds = max(0, min(end, expiry) - max(now, start))
                if seconds:
                    snapshot['premiumExpiryTime'] = expiry - seconds
                    # Subsequent grants move earlier with the shortened
                    # subscription, so a later, separate reset remains exact.
                    for later in _premium_effects(snapshot.get('personalMissionRewardJournal') or {}):
                        if _count(later['start']) >= end:
                            later['start'] -= seconds
                            later['end'] -= seconds
                effect['seconds'] = seconds
                if not seconds:
                    continue
            elif kind == 'dossier':
                name = effect['name']
                dossier = snapshot.setdefault('personalMissionDossier', {})
                if effect.get('mode') == 'add':
                    available = _count(dossier.get(name, 0))
                    count = min(count, available)
                    if not count:
                        continue
                    effect['count'] = count
                    dossier[name] = available - count
                elif effect.get('mode') == 'set':
                    if dossier.get(name) != effect.get('after'):
                        raise GarageError('PERSONAL_MISSION_RESET_DOSSIER_CHANGED: ' + name)
                    if effect.get('before') is None:
                        dossier.pop(name, None)
                    else:
                        dossier[name] = effect['before']
                else:
                    raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
            elif kind == 'customization':
                keys = tuple(_count(effect[key]) for key in
                             ('customization_type', 'item_id', 'vehicle_type'))
                available = _at(snapshot.get('customizationItems'), keys)
                count = min(count, available)
                if not count:
                    continue
                effect['count'] = count
                _remove_reward_camouflage(state, keys[1], keys[2])
                buckets = snapshot['customizationItems'][keys[0]][keys[1]]
                if available > count:
                    buckets[keys[2]] = available - count
                else:
                    buckets.pop(keys[2], None)
            else:
                raise GarageError('INVALID_PERSONAL_MISSION_REWARD_JOURNAL')
            actual.append(effect)
        if actual:
            state.revision += 1
    return notification_rows(actual)
