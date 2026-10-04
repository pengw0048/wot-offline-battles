"""Offline shop and reserve policy; no native GUI or network dependencies.

The permanent bond shop postdates 0.9.22. The first 2019 assortment is
intersected with this client's definitions. Retired prices, reserve prices
and daily missions below are explicit offline extensions, not retail data.
"""

import copy
import hashlib
import time
from collections import namedtuple

OFFICIAL_BOND_OFFERS = {
    'china:Ch25_121_mod_1971B': 15000,
    'usa:A92_M60': 15000,
    'uk:GB13_FV215b': 12000,
    'france:F74_AMX_M4_1949_Liberte': 8000,
    'usa:A117_T26E5_Patriot': 8000,
    'germany:G119_Pz58_Mutz': 8000,
    'ussr:R146_STG_Tday': 8000,
    'germany:G70_PzIV_Hydro': 3000,
}
# Tier VII / IX / X offline brackets. The old tech-tree credit prices and
# placeholder gold values are not offers for these removed definitions.
RETIRED_BOND_OFFERS = {
    'germany:G85_Auf_Panther': 6000,
    'ussr:R75_SU122_54': 12000,
    'ussr:R96_Object_430B': 15000,
    'ussr:R93_Object263B': 15000,
    'germany:G98_Waffentrager_E100': 15000,
}
BOND_OFFERS = dict(OFFICIAL_BOND_OFFERS, **RETIRED_BOND_OFFERS)

# Four resource categories share three simultaneous activation slots.
# Keep the native readiness checks, slot panel and counter on this limit.
MAX_ACTIVE_RESERVES = 3

# WG API 2.40.0 response captured on 2016-04-26, before 0.9.22:
# victor-lyan/wotwrap@931feede6d2f86e7f46973a25b6df6b233e261bd,
# tests/json/encyclopedia.boosters.json. Each row is percent, hours, API ID.
# Include every distinct manual, non-expiring variant in that response;
# collapse identical event IDs and omit already-expired 2015 event reserves.
# No post-1.18 combined Crew/Free XP reserves are imported.
_HISTORICAL_RESERVES = {
    'xp': ((50, 1, 5020), (25, 1, 5021), (25, 2, 5022), (15, 2, 5023),
           (15, 4, 5024), (10, 4, 5025), (10, 6, 5026), (5, 6, 5027),
           (50, 2, 5060), (100, 2, 5061), (100, 1, 5062),
           (10, 2, 10002), (5, 2, 10003)),
    'crew_xp': ((300, 1, 5035), (200, 1, 5036), (200, 2, 5037),
                (100, 2, 5038), (100, 4, 5039), (50, 4, 5040),
                (50, 6, 5041), (300, 2, 5064), (50, 2, 6002),
                (75, 2, 10011), (25, 2, 10013)),
    'free_xp': ((300, 1, 5028), (200, 1, 5029), (200, 2, 5030),
                (100, 2, 5031), (100, 4, 5032), (50, 4, 5033),
                (50, 6, 5034), (20, 6, 5052), (300, 2, 5063),
                (50, 2, 6003), (75, 2, 10022)),
    'credits': ((50, 1, 5042), (25, 1, 5043), (25, 2, 5044),
                (15, 2, 5045), (15, 4, 5046), (10, 4, 5047),
                (10, 6, 5048), (5, 6, 5049), (50, 2, 5065)),
}
Reserve = namedtuple('Reserve', 'key label percent price kind duration api_id')
_LEGACY_RESERVES = (
    Reserve('xp', 'Combat XP', 50, 50, 'xp', 3600, 5020),
    Reserve('crew_xp', 'Crew XP', 200, 100, 'crew_xp', 3600, 5036),
    Reserve('free_xp', 'Free XP', 200, 50, 'free_xp', 3600, 5029),
    Reserve('credits', 'Credits', 50, 100, 'credits', 3600, 5042),
)
# Gold prices are an offline extension, scaled from the four existing offers
# by effect and duration, rounded up. They are not historical shop prices.
RESERVES = _LEGACY_RESERVES + tuple(
    Reserve('%s_%d_%dh' % (base.kind, percent, hours), base.label, percent,
            (base.price * percent * hours + base.percent - 1) // base.percent,
            base.kind, hours * 3600, api_id)
    for base in _LEGACY_RESERVES
    for percent, hours, api_id in _HISTORICAL_RESERVES[base.kind]
    if (percent, hours) != (base.percent, 1))
RESERVE_BY_ID = dict((row[0], row) for row in RESERVES)
DAILY_MISSIONS = (
    ('battles', 'Complete 3 battles', 3, 'xp'),
    ('damage', 'Complete battles and deal 3000 damage', 3000, 'credits'),
    ('wins', 'Complete and win 1 battle', 1, 'crew_xp'),
)
# Each template has a fixed reward. Only the daily selection is random;
# opening the page, restarting or receiving a late receipt cannot reroll it.
MISSION_POOL = (
    ('battles_3', 'Complete %d battles', 'battles', 3, 'xp'),
    ('battles_4', 'Complete %d battles', 'battles', 4, 'free_xp'),
    ('battles_5', 'Complete %d battles', 'battles', 5, 'crew_xp'),
    ('damage_2000', 'Complete battles and deal %d damage', 'damage', 2000, 'xp'),
    ('damage_3000', 'Complete battles and deal %d damage', 'damage', 3000, 'credits'),
    ('damage_5000', 'Complete battles and deal %d damage', 'damage', 5000, 'crew_xp'),
    ('wins_1', 'Complete and win %d battles', 'wins', 1, 'free_xp'),
    ('wins_2', 'Complete and win %d battles', 'wins', 2, 'credits'),
    ('wins_3', 'Complete and win %d battles', 'wins', 3, 'crew_xp'),
)
MISSION_BY_ID = dict((row[0], row) for row in MISSION_POOL)
for _key, _label, _target, _reward in DAILY_MISSIONS:
    MISSION_BY_ID[_key] = (_key, _label, _key, _target, _reward)

# Mod-owned IDs, using the retail GoodieData and GoodieVariable wire shapes.
RESERVE_IDS = dict((row.key, 92001 + index)
                  for index, row in enumerate(_LEGACY_RESERVES))
RESERVE_IDS.update((row.key, 100000 + row.api_id) for row in RESERVES
                   if row.key not in RESERVE_IDS)
RESERVE_KEYS = dict((value, key) for key, value in RESERVE_IDS.items())


def owned_vehicle_types(snapshot):
    return set(int(row.get('vehicleTypeCompactDescr', 0))
               for row in snapshot.get('vehicles', ()))


def matches_vehicle_filters(extra, owned, rented, unlocked, bond=False):
    """Selected stock checkboxes select categories; no selection means buyable."""
    selected = set(extra or ()) & set(('inHangar', 'rentals') if bond else
                                     ('locked', 'inHangar', 'rentals'))
    if not selected:
        return not owned and not rented and (bond or unlocked)
    return bool(('inHangar' in selected and owned and not rented) or
                ('rentals' in selected and rented) or
                ('locked' in selected and not unlocked and not owned and not rented))


VEHICLE_RESTORE_SECONDS = 72 * 3600
VEHICLE_RESTORE_FACTOR = 1.1


def vehicle_recovery_state(snapshot):
    """Normalize sold-vehicle entitlements across JSON save/reload."""
    rows = {}
    for key, entry in (snapshot.get('vehicleRecovery') or {}).items():
        try:
            cd = int(key)
            sold_at, price = int(entry['soldAt']), int(entry['credits'])
            if cd > 0 and sold_at >= 0 and price > 0:
                rows[cd] = {'soldAt': sold_at, 'credits': price,
                            'limited': bool(entry.get('limited', True))}
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def vehicle_recovery_offer(snapshot, compact_descr, now=None):
    row = vehicle_recovery_state(snapshot).get(int(compact_descr))
    if row and (not row['limited'] or
                now_seconds(now) < row['soldAt'] + VEHICLE_RESTORE_SECONDS):
        return row
    return None


def vehicle_recovery_buffer(snapshot):
    # ItemRestore.RESTORE_VEHICLE_TYPE: PREMIUM=0, ACTION=1.
    # A rare ACTION vehicle with changedAt=0 has no restoration deadline.
    return dict((cd, (0, row['soldAt']) if row['limited'] else (1, 0))
                for cd, row in vehicle_recovery_state(snapshot).items())


def reserve_catalogue():
    resources = {'xp': 30, 'crew_xp': 40, 'free_xp': 50, 'credits': 20}
    return {
        'prices': dict((RESERVE_IDS[row.key], (0, row.price)) for row in RESERVES),
        'notInShop': set(),
        'goodies': dict((RESERVE_IDS[row.key],
                        (1, (3, None, None), True, row.duration, None, 0, False,
                         None, (resources[row.kind], row.percent, True)))
                       for row in RESERVES)}


def reserve_inventory(snapshot, now=None):
    reserves = reserve_state(snapshot)
    stamp = now_seconds(now)
    result = {}
    for key, uid in RESERVE_IDS.items():
        interval = reserves['active'].get(key)
        active = interval is not None and interval[1] > stamp
        result[uid] = (1 if active else 0,
                       interval[1] if active else 0, reserves['counts'][key])
    return result


def service_diff(snapshot, unused_outcome=None):
    return {'goodies': reserve_inventory(snapshot),
            'badges': tuple(selected_badges(snapshot))}


def selected_badges(snapshot):
    # Earlier builds allowed every cosmetic. Do not turn those selections
    # into achievements when loading an old save.
    if not snapshot.get('badgeSelectionVerified', False):
        return []
    return [int(value) for value in
            (snapshot.get('selectedBadges') or ())[:1] if int(value) > 0]


def badge_catalogue():
    # Read the installed client's definitions only when choosing a cosmetic.
    # Do not add a GUI dependency to hidden-worker bootstrap.
    from gui.shared.utils.requesters.badges_requester import (
        _readBadges, _BADGES_XML_PATH)
    return _readBadges(_BADGES_XML_PATH)


def earned_badges():
    """Read achievement ownership, never the installed artwork catalogue."""
    from helpers import dependency
    from skeletons.gui.shared import IItemsCache
    items = dependency.instance(IItemsCache).items
    return set(badge.badgeID for badge in items.getBadges().values()
               if badge.isAchieved)


def now_seconds(now=None):
    return int(time.time() if now is None else now)


def reserve_state(snapshot):
    raw = snapshot.get('personalReserves') or {}
    counts, active, history = {}, {}, {}
    for key in RESERVE_BY_ID:
        counts[key] = max(0, int((raw.get('counts') or {}).get(key, 0)))
        value = (raw.get('active') or {}).get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            start, end = int(value[0]), int(value[1])
            if 0 <= start < end:
                active[key] = [start, end]
        history[key] = []
        for interval in (raw.get('history') or {}).get(key, ()):
            if isinstance(interval, (list, tuple)) and len(interval) == 2:
                start, end = int(interval[0]), int(interval[1])
                if 0 <= start < end:
                    history[key].append([start, end])
    return {'counts': counts, 'active': active, 'history': history}


def daily_state(snapshot, now=None):
    day = now_seconds(now) // 86400
    raw = snapshot.get('dailyMissions') or {}
    same_day = int(raw.get('day', -1)) == day
    ids = raw.get('missions') if same_day else None
    if same_day and ids is None:
        # Finish the old fixed set today without granting its rewards twice.
        ids = [row[0] for row in DAILY_MISSIONS]
    if not (isinstance(ids, (list, tuple)) and len(ids) == 3 and
            len(set(ids)) == 3 and all(key in MISSION_BY_ID for key in ids)):
        def rank(row):
            return hashlib.sha256(('%d:%s' % (day, row[0])).encode('ascii')).digest()
        ids = [min((row for row in MISSION_POOL if row[2] == metric),
                   key=rank)[0] for metric in ('battles', 'damage', 'wins')]
    claimed = (raw.get('claimed') or []) if same_day else []
    result = {'day': day, 'missions': list(ids),
              'claimed': [key for key in ids if key in claimed]}
    for key in ids:
        target = MISSION_BY_ID[key][3]
        result[key] = min(target, max(0, int(raw.get(key, 0)))) if same_day else 0
    return result


def publish_offers(snapshot, vehicles):
    """Keep native catalogue prices separate from Special Offers quotes."""
    from gui.mods.offline_lan_0922 import price_catalogue
    rows = []
    for name, price in sorted(BOND_OFFERS.items()):
        try:
            descriptor = vehicles.VehicleDescr(typeName=name)
            vehicle_type = descriptor.type
            nation, vehicle_id = vehicle_type.id
            cd = vehicles.makeIntCompactDescrByID('vehicle', nation, vehicle_id)
        except (KeyError, ValueError):
            continue
        native_prices = snapshot.setdefault('shopItemPrices', {})
        unavailable = snapshot.setdefault('notInShopItems', set())
        if name in RETIRED_BOND_OFFERS:
            # These definitions intentionally have only an offline bond offer;
            # their old credit/placeholder prices are not purchase terms.
            native_prices[cd] = {'crystal': price}
            unavailable.discard(cd)
        else:
            catalogue_price = price_catalogue.vehicle_price(*name.split(':', 1))
            if catalogue_price is not None:
                native_prices[cd] = price_catalogue.money(catalogue_price)
                if catalogue_price[price_catalogue.NOT_IN_SHOP]:
                    unavailable.add(cd)
                else:
                    unavailable.discard(cd)
        snapshot.setdefault('shopVehicleOfferCompactDescrs', set()).add(cd)
        # Offers grant purchase access, not inventory ownership. Account sync
        # requires vehicleTypeCompactDescrs to match complete garage records;
        # GarageState.buy_vehicle adds that ownership only after a purchase.
        rows.append({'name': name, 'cd': cd, 'price': price,
                     'level': int(vehicle_type.level),
                     'label': vehicle_type.userString,
                     'retired': name in RETIRED_BOND_OFFERS})
    snapshot['offlineVehicleOffers'] = rows


def transact(state, action, key, now=None):
    """Mutate through GarageState's rollback and persistence boundary."""
    from gui.mods.offline_lan_0922.account_rpc.garage import GarageError
    stamp = now_seconds(now)
    with state._transaction():
        snapshot = state.snapshot()
        if action == 'vehicle':
            offer = next((row for row in snapshot.get('offlineVehicleOffers', [])
                          if row['name'] == key), None)
            if offer is None:
                raise GarageError('This vehicle is not offered.')
            if offer['cd'] in owned_vehicle_types(snapshot):
                raise GarageError('the account already owns this vehicle')
            # The common purchase owns duplicate/slot/affordability checks,
            # real stock modules and crew. Never construct a reward clone.
            state.buy_vehicle(offer['cd'], recruit_crew=True, bond_offer=True)
        elif action in ('buy_reserve', 'activate_reserve'):
            if key not in RESERVE_BY_ID:
                raise GarageError('Unknown personal reserve.')
            reserves = reserve_state(snapshot)
            if action == 'buy_reserve':
                state._charge({'gold': RESERVE_BY_ID[key].price})
                reserves['counts'][key] += 1
            else:
                # Reconnect receipts can arrive after another reserve was
                # activated. Retain expired intervals so their battle-start
                # entitlement cannot be overwritten by the new activation.
                for kind, interval in reserves['active'].items():
                    if interval[1] <= stamp:
                        reserves['history'][kind].append(interval)
                active = dict((kind, interval) for kind, interval in
                              reserves['active'].items() if interval[1] > stamp)
                current = next((other for other in active
                    if RESERVE_BY_ID[other].kind == RESERVE_BY_ID[key].kind), None)
                if (current is not None and
                        RESERVE_BY_ID[current].percent >= RESERVE_BY_ID[key].percent):
                    raise GarageError('This reserve type is already active.')
                if current is None and len(active) >= MAX_ACTIVE_RESERVES:
                    raise GarageError('Only three reserve types can be active.')
                if reserves['counts'][key] < 1:
                    raise GarageError('No personal reserve of this type is owned.')
                if current is not None:
                    # Retail permits replacing a type with a stronger bonus.
                    # Keep only its actual interval for late battle receipts.
                    if active[current][0] < stamp:
                        reserves['history'][current].append([active[current][0], stamp])
                    del active[current]
                reserves['counts'][key] -= 1
                active[key] = [stamp, stamp + RESERVE_BY_ID[key].duration]
                reserves['active'] = active
            snapshot['personalReserves'] = reserves
        elif action == 'expire_reserves':
            reserves = reserve_state(snapshot)
            for kind, interval in list(reserves['active'].items()):
                if interval[1] <= stamp:
                    reserves['history'][kind].append(interval)
                    del reserves['active'][kind]
            snapshot['personalReserves'] = reserves
        elif action == 'select_badge':
            try:
                badge = int(key)
            except (TypeError, ValueError):
                raise GarageError('Unknown badge.')
            if badge and badge not in badge_catalogue():
                raise GarageError('Unknown badge.')
            if badge and badge not in earned_badges():
                raise GarageError('Complete the required missions to earn this badge first.')
            snapshot['selectedBadges'] = [badge] if badge else []
            snapshot['badgeSelectionVerified'] = True
        else:
            raise GarageError('Unknown offline service action.')
        state.revision += 1
    return True


def reserve_bonuses(snapshot, awarded, battle_start):
    """Freeze reserve eligibility at battle start, including expiry in battle."""
    reserves = reserve_state(snapshot)
    bonuses = dict((row.kind, 0) for row in _LEGACY_RESERVES)
    for row in RESERVES:
        key = row.key
        intervals = list(reserves['history'][key])
        if key in reserves['active']:
            intervals.append(reserves['active'][key])
        if any(start <= battle_start < end for start, end in intervals):
            base = awarded.get('xp' if row.kind == 'crew_xp' else row.kind, 0)
            # Separate variants of one resource must never stack, including
            # an imported save with overlapping activation intervals.
            bonuses[row.kind] = max(bonuses[row.kind],
                                   max(0, int(base)) * row.percent // 100)
    return bonuses


def advance_daily(snapshot, facts, now=None):
    """Called inside the durable battle receipt transaction, once per battle."""
    if facts.get('premature_leave', False):
        return []
    stamp = now_seconds(now)
    if int((snapshot.get('dailyMissions') or {}).get('day', -1)) > stamp // 86400:
        return []
    daily = daily_state(snapshot, stamp)
    reserves = reserve_state(snapshot)
    values = {'battles': 1, 'damage': max(0, int(facts.get('damage', 0))),
              'wins': int(bool(facts.get('won')))}
    granted = []
    for key in daily['missions']:
        unused_id, unused_label, metric, target, reward = MISSION_BY_ID[key]
        daily[key] = min(target, daily[key] + values[metric])
        if daily[key] >= target and key not in daily['claimed']:
            daily['claimed'].append(key)
            reserves['counts'][reward] += 1
            granted.append(reward)
    snapshot['dailyMissions'] = daily
    snapshot['personalReserves'] = reserves
    return granted


def saved_fields(snapshot):
    return {'personalReserves': reserve_state(snapshot),
            'vehicleRecovery': vehicle_recovery_state(snapshot),
            'dailyMissions': copy.deepcopy(snapshot.get('dailyMissions') or {}),
            'firstWinDays': dict(snapshot.get('firstWinDays') or {}),
            'selectedBadges': selected_badges(snapshot),
            'badgeSelectionVerified': bool(snapshot.get('badgeSelectionVerified', False))}
