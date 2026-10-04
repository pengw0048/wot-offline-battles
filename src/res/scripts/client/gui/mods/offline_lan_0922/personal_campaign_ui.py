"""Native system messages for durable personal-mission receipt facts.

The caller owns receipt delivery and retry. Formatting never changes mission
state, claims a delayed crew reward, or infers new payment from completion.
"""

import re

from gui.mods.offline_lan_0922.ui_i18n import as_text, tr


_COMPONENT_TOKEN = re.compile(r'token:pt:final:s[1-9][0-9]*:t[1-9][0-9]*\Z')


def _value(node, name=None, default=''):
    if name is not None:
        node = next((item for tag, item in (node or {}).get('children', ())
                     if tag == name), None)
    return (node or {}).get('value', default)


def _label(value):
    # The native message renderer interprets HTML. Quest/item names are text.
    return as_text(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def mission_name(identifier):
    try:
        from gui.shared.personality import ServicesLocator
        quest = ServicesLocator.eventsCache.personalMissions.getQuests().get(int(identifier))
        if quest is not None:
            return _label(quest.getUserName() or quest.getShortUserName())
    except Exception:
        # A missing cache must not hide an already durable reward receipt.
        pass
    return tr('Personal mission %d') % int(identifier)


def _item_name(compact_descr):
    try:
        from items import vehicles
        item = vehicles.getItemByCompactDescr(int(compact_descr))
        name = item.userString
        if name:
            return _label(name)
    except Exception:
        pass
    return tr('Item')


def _vehicle_name(row):
    try:
        from items import vehicles
        if row.get('vehicle_type'):
            vehicle = vehicles.getVehicleType(int(row['vehicle_type']))
        else:
            vehicle = vehicles.VehicleDescr(typeName=row['vehicle']).type
        if vehicle.userString:
            return _label(vehicle.userString)
    except Exception:
        pass
    return tr('Reward vehicle')


def _badge_name(identifier):
    try:
        from gui.Scaleform.locale.BADGE import BADGE
        from helpers import i18n
        key = BADGE.badgeName(int(identifier))
        name = i18n.makeString(key)
        if name and name != key:
            return _label(name)
    except Exception:
        pass
    return None


def queue_notification(snapshot, settlement):
    """Commit the notice with its assets; native delivery happens in lobby."""
    if not messages(settlement):
        return False
    import copy
    import uuid
    snapshot.setdefault('personalMissionNotifications', []).append({
        'id': uuid.uuid4().hex, 'settlement': copy.deepcopy(settlement)})
    return True


def _reward_parts(bonuses):
    """Render only assets actually included in newly paid stages.

Internal unlock counters and free_award_list are omitted: the latter has a
separate earned-order receipt and must not be counted a second time here.
"""
    labels = {'credits': 'Credits', 'gold': 'Gold', 'freeXP': 'Free XP',
              'crystal': 'Bonds', 'slots': 'Garage slots', 'berths': 'Barracks bunks'}
    totals, items = {}, {}
    for bonus in bonuses:
        for name, node in (bonus or {}).get('children', ()):
            if name in labels or name == 'premium':
                totals[name] = totals.get(name, 0) + int(_value(node, default='0'))
            elif name == 'item':
                identifier = int(_value(node))
                items[identifier] = items.get(identifier, 0) + int(_value(node, 'count', '1'))
            elif name == 'token':
                if _COMPONENT_TOKEN.match(_value(node, 'id')):
                    totals['component'] = totals.get('component', 0) + int(_value(node, 'count', '1'))
            elif name == 'customizations':
                for tag, item in node.get('children', ()):
                    if tag == 'item' and _value(item, 'custType') == 'camouflage':
                        totals['camouflage'] = totals.get('camouflage', 0) + int(_value(item, 'value', '0'))
    parts = []
    for name in ('credits', 'gold', 'freeXP', 'crystal', 'slots', 'berths'):
        if totals.get(name, 0) > 0:
            parts.append(tr('%s: %d') % (tr(labels[name]), totals[name]))
    if totals.get('premium', 0) > 0:
        parts.append(tr('Premium account: %d day(s)') % totals['premium'])
    if totals.get('component', 0) > 0:
        parts.append(tr('Reward vehicle components: %d') % totals['component'])
    if totals.get('camouflage', 0) > 0:
        parts.append(tr('Camouflage: %d') % totals['camouflage'])
    for identifier in sorted(items):
        if items[identifier] > 0:
            parts.append(tr('%s x%d') % (_item_name(identifier), items[identifier]))
    return parts


def _payout_parts(rows):
    """Format actual operation payouts without guessing from operation IDs."""
    nodes, parts, premium_seconds = [], [], 0
    for row in rows:
        kind = row.get('kind')
        if kind in ('vehicle', 'vehicle_restored'):
            parts.append(tr('Vehicle: %s') % _vehicle_name(row))
        elif kind == 'compensation':
            parts.append(tr('Vehicle compensation: %d credits (%s)') %
                         (int(row.get('credits', 0)), _vehicle_name(row)))
        elif kind == 'badge':
            name = _badge_name(row.get('id'))
            parts.append((tr('Badge: %s') % name) if name else
                         tr('Badges: %d') % int(row.get('count', 0)))
        elif kind == 'crew':
            parts.append(tr('Crew members: %d') % int(row.get('count', 0)))
        elif kind == 'customization':
            # The reward ledger currently accepts camouflage only and stores
            # the native numeric customization type rather than its XML name.
            parts.append(tr('Camouflage: %d') % int(row.get('count', 0)))
        elif kind == 'premium':
            # Revocation may remove only the unconsumed part of a grant.
            # The receipt's exact seconds override any rounded day count.
            premium_seconds += max(0, int(row['seconds']) if 'seconds' in row
                                   else int(float(row.get('count', 0)) * 86400))
        elif kind in ('credits', 'gold', 'freeXP', 'crystal', 'slots', 'berths'):
            nodes.append((kind, {'value': str(row.get('count', 0))}))
        elif kind == 'item':
            nodes.append(('item', {'value': str(row['id']), 'children': [
                ('count', {'value': str(row.get('count', 0))})]}))
        elif kind == 'token':
            nodes.append(('token', {'children': [
                ('id', {'value': row['id']}),
                ('count', {'value': str(row.get('count', 0))})]}))
    if premium_seconds:
        days, rest = divmod(premium_seconds, 86400)
        if not rest:
            parts.append(tr('Premium account: %d day(s)') % days)
        else:
            hours, rest = divmod(rest, 3600)
            minutes, seconds = divmod(rest, 60)
            parts.append(tr('Premium account: %d d %d h %d min %d s') %
                         (days, hours, minutes, seconds))
    return _reward_parts([{'children': nodes}]) + parts


def _paid_parts(rows):
    structured = [row for row in rows if 'kind' in row]
    xml = [row for row in rows if 'kind' not in row]
    return _reward_parts(xml) + _payout_parts(structured)


def _reset_error(error):
    """Describe the blocked reset without leaking internal asset identifiers."""
    error = as_text(error)
    wallet = re.match(r'PERSONAL_MISSION_RESET_WALLET_UNAVAILABLE: ([A-Za-z]+) '
                      r'required=([0-9]+) available=([0-9]+)\Z', error)
    if wallet:
        labels = {'credits': 'Credits', 'gold': 'Gold', 'freeXP': 'Free XP', 'crystal': 'Bonds'}
        name, required, available = wallet.groups()
        reason = tr('Not enough %s to withdraw rewards: %d required, %d available.') % (
            tr(labels.get(name, 'Currency')), int(required), int(available))
    else:
        code = error.split(':', 1)[0]
        reasons = {
            'PERSONAL_MISSION_RESET_ORDERS_SPENT': 'Some reward orders are committed to other missions. Return them before resetting.',
            'PERSONAL_MISSION_RESET_ITEM_UNAVAILABLE': 'Some reward items are mounted or no longer in the depot.',
            'PERSONAL_MISSION_RESET_SLOTS_UNAVAILABLE': 'Free garage slots are needed to withdraw the reward slots.',
            'PERSONAL_MISSION_RESET_BERTHS_UNAVAILABLE': 'Free barracks bunks are needed to withdraw the reward bunks.',
            'PERSONAL_MISSION_RESET_LAST_VEHICLE': 'The reward vehicle is your last tank. Keep another tank before resetting.',
            'PERSONAL_MISSION_RESET_VEHICLE_MODULES_UNAVAILABLE': 'Some reward vehicle modules are mounted or no longer in the depot.',
            'PERSONAL_MISSION_RESET_CUSTOMIZATION_UNAVAILABLE': 'Some reward camouflage is no longer available.',
        }
        if 'PROVENANCE_MISSING' in code or 'SOURCE_UNAVAILABLE' in code:
            reason = tr('The original reward cannot be identified in this save. The reset was cancelled to protect your other assets.')
        elif code in reasons:
            reason = tr(reasons[code])
        else:
            reason = tr('Rewards could not be withdrawn. Please include an error report when asking for help.')
    return tr('Personal mission reset was not applied. %s') % reason


def messages(settlement):
    """Return localized lines without consulting mutable reward balances."""
    if not isinstance(settlement, dict):
        return []
    lines = []
    if settlement.get('reset_error'):
        lines.append(_reset_error(settlement['reset_error']))
    for row in settlement.get('missions', ()):
        before, after = int(row.get('before', 0)), int(row.get('after', 0))
        progressed = after > before and after > 0
        paid = bool(row.get('paid_stages'))
        earned = int(row.get('orders_earned', 0))
        refunded = int(row.get('orders_refunded', 0))
        revoked = row.get('phase') == 'revoked'
        orders_revoked = int(row.get('orders_revoked', 0))
        tankwomen_revoked = int(row.get('tankwomen_revoked', 0))
        withdrawal = revoked and (before > after or row.get('rewards') or
                                   orders_revoked > 0 or tankwomen_revoked > 0)
        if not (progressed or paid or earned > 0 or refunded > 0 or withdrawal):
            continue
        name = mission_name(row['id'])
        if revoked:
            title = ('Personal mission honors withdrawn: %s.' if before == 2 and after == 1
                     else 'Personal mission completion withdrawn: %s.')
        elif progressed:
            title = ('Personal mission completed with honors: %s.' if after == 2
                     else 'Personal mission completed: %s.')
        else:
            title = 'Personal mission rewards: %s.'
        details = [tr(title) % name]
        rewards = _paid_parts(row.get('rewards', ())) if paid or revoked else []
        if rewards:
            details.append(tr('Rewards withdrawn: %s.' if revoked else
                              'Rewards granted: %s.') % u', '.join(rewards))
        elif paid and earned <= 0 and not revoked:
            details.append(tr('Rewards granted.'))
        elif progressed and not revoked and int(row.get('paid_before', 0)) >= after:
            details.append(tr('Previously claimed rewards have already been received.'))
        if earned > 0:
            details.append(tr('Orders earned: %d.') % earned)
        if refunded > 0:
            details.append(tr('Committed orders returned: %d.') % refunded)
        if orders_revoked > 0:
            details.append(tr('Orders withdrawn: %d.') % orders_revoked)
        if tankwomen_revoked > 0:
            details.append(tr('Female crew members withdrawn: %d.') % tankwomen_revoked)
        if row.get('tankwomen_already_dismissed'):
            details.append(tr('Female crew members already permanently dismissed: %d. No crew withdrawal needed.') %
                           int(row['tankwomen_already_dismissed']))
        if row.get('tankwoman_pending') and not revoked:
            details.append(tr('Female crew member available: choose her nation, vehicle and role in Personal Missions.'))
        lines.append(u' '.join(details))
    for row in settlement.get('operations', ()):
        rewards = _paid_parts(row.get('rewards', ()))
        if rewards:
            title = ('Personal mission operation rewards withdrawn: %s.'
                     if row.get('phase') == 'revoked' else
                     'Personal mission operation rewards granted: %s.')
            lines.append(tr(title) % u', '.join(rewards))
    for row in settlement.get('account_changes', ()):
        rewards = _paid_parts(row.get('rewards', ()))
        if rewards:
            title = ('Account assets removed: %s.' if row.get('phase') == 'revoked'
                     else 'Account assets received: %s.')
            lines.append(tr(title) % u', '.join(rewards))
    return lines


def notify(settlement):
    """Deliver one native message; propagate failure for receipt-level retry."""
    lines = messages(settlement)
    if not lines:
        return False
    from gui import SystemMessages
    SystemMessages.pushMessage(u'\n'.join(lines), type=SystemMessages.SM_TYPE.Information)
    return True
