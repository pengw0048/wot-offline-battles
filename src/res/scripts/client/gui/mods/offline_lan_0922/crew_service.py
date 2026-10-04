"""Native tankman dossier settlement for the enrolled crew, never an UI overlay.

The participation journal belongs to GarageStore's atomic save. Descriptors
carry their own service data through ordinary native transfer/retrain commands.
Only medals supported by the installed tankman dossier layout are incremented.
"""
from __future__ import print_function

import base64

try:
    text_types = (basestring,)
except NameError:
    text_types = (str,)


def receipt_key(value):
    if not isinstance(value, text_types) or not 1 <= len(value) <= 96:
        raise ValueError('crew service receipt identity is invalid')
    return str(value)


def _passport(descriptor):
    # Never match members by the vehicle seat after a transfer. These native
    # passport fields do not change with XP, skills or vehicle retraining.
    return list(descriptor.getPassport())


def capture(record, tankmen):
    result = []
    for seat, member_id in enumerate(record.get('crew') or ()):
        if member_id is None:
            continue
        compact = (record.get('tankmen') or {}).get(member_id)
        if not isinstance(compact, bytes) or not compact:
            raise ValueError('participating tankman descriptor is unavailable')
        descriptor = tankmen.TankmanDescr(compact)
        result.append({'seat': seat, 'passport': _passport(descriptor),
                       'compact': base64.b64encode(compact).decode('ascii')})
    return {'vehicle': int(record['vehicleTypeCompactDescr']), 'members': result}


def validate_journal(value):
    result = {}
    if not isinstance(value, dict) or len(value) > 512:
        return result
    for key, row in value.items():
        try:
            receipt_key(key)
            if not isinstance(row, dict) or int(row['vehicle']) <= 0:
                continue
            members = row['members']
            if not isinstance(members, list) or len(members) > 16:
                continue
            seen = set()
            for member in members:
                if (not isinstance(member, dict) or len(member['passport']) != 6 or
                        not isinstance(member['compact'], text_types) or
                        len(member['compact']) > 128 * 1024):
                    raise ValueError('invalid enrolled member')
                seat = int(member['seat'])
                if not 0 <= seat < 16 or seat in seen:
                    raise ValueError('duplicate or invalid seat')
                seen.add(seat)
                base64.b64decode(member['compact'].encode('ascii'))
            result[key] = row
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _members(snapshot):
    seen = set()
    records = snapshot.get('vehicles') or [snapshot]
    for record in records:
        for member_id, compact in (record.get('tankmen') or {}).items():
            if member_id in seen:
                raise ValueError('tankman has more than one garage owner')
            seen.add(member_id)
            yield record['tankmen'], member_id, compact, int(record.get('id', 0)), False
    for member_id, compact in (snapshot.get('barracksTankmen') or {}).items():
        if member_id in seen:
            raise ValueError('tankman has more than one garage owner')
        seen.add(member_id)
        yield snapshot['barracksTankmen'], member_id, compact, 0, False
    for member_id, row in (snapshot.get('recycleBinTankmen') or {}).items():
        if member_id in seen:
            raise ValueError('tankman has more than one garage owner')
        seen.add(member_id)
        yield snapshot['recycleBinTankmen'], member_id, row[0], 0, True


def award(snapshot, roster, medals, tankmen, dossier_factory=None,
          medal_layout=None):
    if dossier_factory is None:
        from dossiers2.custom.builders import getTankmanDossierDescr
        dossier_factory = getTankmanDossierDescr
    if medal_layout is None:
        from dossiers2.custom.tankman_layout import TMAN_ACHIEVEMENTS_BLOCK_LAYOUT
        medal_layout = TMAN_ACHIEVEMENTS_BLOCK_LAYOUT
    medals = sorted(set(str(name) for name in medals) & set(medal_layout))
    available = list(_members(snapshot))
    used = set()
    pending = []
    missing = []
    for enrolled in roster['members']:
        compact = base64.b64decode(enrolled['compact'].encode('ascii'))
        matches = [row for row in available if row[1] not in used and row[2] == compact]
        if not matches:
            matches = [row for row in available if row[1] not in used and
                       _passport(tankmen.TankmanDescr(row[2])) == enrolled['passport']]
        if len(matches) != 1:
            # Ambiguous identical passports do not authorize assigning a medal
            # to a replacement occupant. Keep the transaction pending instead.
            raise ValueError('enrolled tankman is missing or ambiguous (seat=%s)' % enrolled['seat'])
        owner, member_id, current, vehicle_id, recycled = matches[0]
        used.add(member_id)
        descriptor = tankmen.TankmanDescr(current)
        dossier = dossier_factory(descriptor.dossierCompactDescr)
        total = dossier['total']
        total['battlesCount'] = int(total['battlesCount']) + 1
        achievements = dossier['achievements']
        for name in medals:
            achievements[name] = int(achievements[name]) + 1
        descriptor.dossierCompactDescr = dossier.makeCompDescr()
        updated = descriptor.makeCompactDescr()
        # Reparse the result through the actual installed reader before any
        # owner entry is changed. No manual binary offset editing is used.
        reread = tankmen.TankmanDescr(updated)
        restored = dossier_factory(reread.dossierCompactDescr)
        if int(restored['total']['battlesCount']) != int(total['battlesCount']):
            raise ValueError('native tankman service round-trip failed')
        pending.append((owner, member_id, updated, vehicle_id, recycled))
    for owner, member_id, updated, vehicle_id, recycled in pending:
        if recycled:
            previous = owner[member_id]
            owner[member_id] = (updated,) + tuple(previous[1:])
        else:
            owner[member_id] = updated
    return {'members': sorted(used), 'medals': medals,
            'vehicles': sorted(set(row[3] for row in pending if row[3])),
            'missing': missing}
